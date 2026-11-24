#!/usr/bin/env python3
"""Build HACF's public, product-only release exports (mission release-export, R3-R8, R15, R17).

`profile check` validates the data profile `dev/release_export.yml`. `build --version <v>` turns the
private release commit `<source>` into one public commit whose only parent is the previous export
(or the profile's `base`), whose tree is the source tree minus that version's excludes plus its
overlay, and points `refs/heads/public/main` and the annotated tag `v<v>` at it.

`scan` checks a would-be export tree for private strings (mission R9, R19); `gate` proves one
export (R10, R11, R13), writing a report outside the repository (R20); `plan` writes the push plan
and `verify-push` re-checks the ancestry and the tags right before the push (R12).

The tool writes git objects and those two refs only. It never touches HEAD, the repository's index
or a working tree, and it never pushes: the remote is only read (`ls-remote`, and `fetch` into
FETCH_HEAD) to decide whether a version is already published. The push commands exist only as
printed text for the lead. Every git call goes through `git()`, which builds its environment from
scratch so the same inputs give the same shas under any ambient git configuration or environment.

Exit codes: 0 ok, 1 FAIL (scan, gate, verify-push), 2 refusal, usage or setup error, 75 the gate
waited out the host heavy-job lock (dev/scripts/host_gate.py) and should be retried later.
Stdlib plus PyYAML; runs on Python 3.10.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# The host heavy-job lock and the heavy-process helpers live in dev/scripts/host_gate.py.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import host_gate  # noqa: E402
from host_gate import (  # noqa: E402,F401 - re-exported: the gate's helpers moved to host_gate
    INTERPRETER_RE,
    _pytest_module,
    _runs_heavy,
    ancestor_pids,
    is_heavy_process,
)
from summarize_pytest import summarize_output  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dev dependency; its absence is a setup error
    yaml = None  # type: ignore[assignment]

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
EXIT_BUSY = host_gate.EXIT_BUSY  # 75: the host stayed busy; retry later, never a verdict
EXIT_UNKNOWN = 3  # `scan --tree` without a digest file: never a verdict, like 75
SCHEMA = "hacf.release-export/v2"
PROFILE_MAX_BYTES = 256 * 1024
ZERO_SHA = "0" * 40
PUBLIC_MAIN = "refs/heads/public/main"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = Path(__file__).resolve().with_name("release_export.yml")

VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z")
SELECTOR_RE = re.compile(r"(<=|>=)?(.+)\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# A raw git date: seconds since the epoch and a timezone offset, as in a commit object.
RAW_DATE_RE = re.compile(r"[0-9]{1,12} [+-][0-9]{4}\Z")

# The tool's built-in strict literals: the generic Linux and macOS home roots (mission R9, F8).
# Built from fragments so this file holds neither; the profile must hold both as `literal` rows
# (written there with the first / escaped). Private literals live only in the digest file (F1).
TOOL_LITERALS = {"home-dir": "/" + "home/", "users-dir": "/" + "Users/"}
# The profile's own path: the only file a `raw-key` allowlist row may name.
PROFILE_REL = "dev/release_export.yml"
RAW_KEY = "raw-key"
HEX64_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")

DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
LABEL_RE = re.compile(r"[a-z][a-z0-9-]{0,40}\Z")
# An email-shaped token (mission R9), matched on bytes so binary blobs scan as they are.
EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_PATTERN = "email:"
DIGEST_PATTERN = "private address"
BINARY_SNIFF = 8000  # git's own heuristic: a NUL byte in the first 8000 bytes means binary
COMMIT_OBJECT, TAG_OBJECT = "<commit object>", "<tag object>"

CACHE_VARS = ("UV_CACHE_DIR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "CBM_CACHE_DIR", "XDG_CACHE_HOME")
MCP_FILES = (".mcp.json", ".cursor/mcp.json", ".roo/mcp.json")
CODE_GRAPH_SERVER = "codebase-memory-mcp"
LIVE_TEST = "dev/tests/contract/test_codegraph_live.py"
# The live codegraph test writes its verdict with the raw captures (never exported, F7).
LIVE_VERDICT = "dev/tests/fixtures/recorded-raw/S04/verdict.json"
# The install record fields that legitimately differ between an install from the export clone
# and one from the private source (Task 1, read from HACF-install.sh): the manifest's
# installed_commit, installed_at and source.commit (write_manifest/cmd_manifest), the
# per-machine source record's path, ref, commit and written_at (cmd_sourcefile), and the
# timestamped backup directory name `.agents/.backup/<UTC stamp>/` (BACKUP_TS).
MANIFEST_REL = ".agents/hacf-manifest.json"
SOURCE_RECORD_REL = ".agents/state/install/source.json"
MANIFEST_VOLATILE = frozenset({"installed_commit", "installed_at"})
SOURCE_RECORD_VOLATILE = frozenset({"path", "ref", "written_at", "commit"})
BACKUP_STAMP_RE = re.compile(r"^\.agents/\.backup/[0-9]{8}T[0-9]{6}Z(/|$)")


class Refusal(Exception):
    """A refusal: printed as one line, exit 2."""


# --------------------------------------------------------------------------------------- git


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of every git call, built from scratch (mission R3).

    Only PATH and HOME are carried over (plus SSH_AUTH_SOCK, which a read of an ssh remote needs);
    every other variable, every ambient `GIT_*` among them, is dropped. The global and system
    configuration files are switched off.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    env.update(extra or {})
    return env


def git_argv(repo: Path, args: tuple[str, ...]) -> list[str]:
    """The argv of one hermetic git command in `repo`."""
    return [
        "git",
        "-C",
        str(repo),
        "--no-replace-objects",
        "--literal-pathspecs",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "tag.gpgSign=false",
        "-c",
        "i18n.commitEncoding=UTF-8",
        "-c",
        "core.autocrlf=false",
        *args,
    ]


def git_bytes(repo: Path, *args: str, stdin: bytes = b"") -> bytes:
    """Run one hermetic git command and return its raw stdout; a failure refuses."""
    proc = subprocess.run(
        git_argv(repo, args), input=stdin, capture_output=True, env=git_env(), check=False
    )
    if proc.returncode != 0:
        detail = (proc.stderr.decode("utf-8", "replace").strip().splitlines() or ["?"])[-1]
        raise Refusal(f"git {args[0]} failed: {detail}")
    return proc.stdout


def git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one git command in `repo` hermetically; with `check`, a failure becomes a Refusal."""
    proc = subprocess.run(
        git_argv(repo, args),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env=git_env(env),
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr.strip().splitlines() or ["(no output)"])[-1]
        raise Refusal(f"git {args[0]} failed: {detail}")
    return proc


def rev(repo: Path, spec: str) -> str | None:
    """The full sha `spec` names, or None when it names nothing."""
    proc = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", spec, check=False)
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and SHA_RE.match(sha) else None


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Whether `ancestor` is an ancestor of `descendant` (a git error refuses)."""
    proc = git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if proc.returncode not in (0, 1):
        raise Refusal(f"cannot compare {ancestor[:12]} and {descendant[:12]}")
    return proc.returncode == 0


def raw_dates(repo: Path, commit: str) -> tuple[str, str]:
    """The raw author and committer dates (`<seconds> <tz>`) of a commit object."""
    header = git(repo, "cat-file", "-p", commit).stdout.split("\n\n", 1)[0]
    dates: dict[str, str] = {}
    for line in header.splitlines():
        key, _, rest = line.partition(" ")
        if key in ("author", "committer") and key not in dates:
            dates[key] = " ".join(rest.rsplit(" ", 2)[-2:])
    author, committer = dates.get("author", ""), dates.get("committer", "")
    if not (RAW_DATE_RE.match(author) and RAW_DATE_RE.match(committer)):
        raise Refusal(f"cannot read the raw dates of {commit[:12]}")
    return author, committer


# ----------------------------------------------------------------------------------- profile


@dataclass(frozen=True)
class Overlay:
    """A release's overlay: whole files taken from a later commit (mission R15)."""

    commit: str | None
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Release:
    """One profile release: its version, private source commit and optional overlay."""

    version: str
    source: str
    overlay: Overlay | None


@dataclass(frozen=True)
class Exclude:
    """One exclude row of the profile (mission R6, R7)."""

    path: str  # a trailing / means a directory
    reason: str
    versions: str

    @property
    def is_dir(self) -> bool:
        """Whether the row names a directory (a trailing /)."""
        return self.path.endswith("/")

    @property
    def bare(self) -> str:
        """The row's path without its trailing /."""
        return self.path.rstrip("/")


@dataclass(frozen=True)
class StrictPattern:
    """A strict scan literal of the profile: one of the tool's generic home roots (F8)."""

    label: str
    literal: str


@dataclass(frozen=True)
class AdvisoryPattern:
    """An advisory scan pattern: reported with file:line, never failing (R9)."""

    label: str
    regex: str


@dataclass(frozen=True)
class ScanConfig:
    """The profile's `scan` section; empty when the profile has none."""

    strict: tuple[StrictPattern, ...] = ()
    email_allowlist: tuple[str, ...] = ()
    advisory: tuple[AdvisoryPattern, ...] = ()
    strict_keys: tuple[str, ...] = ()

    def missing_tool_literals(self) -> list[str]:
        """The labels of the tool's built-in strict literals this section does not hold."""
        held = {(p.label, p.literal) for p in self.strict}
        return [label for label, lit in TOOL_LITERALS.items() if (label, lit) not in held]


@dataclass(frozen=True)
class AllowRow:
    """An allowlist row: a text row {path, pattern, count} or a binary row {path, blob_sha256}."""

    path: str
    reason: str
    versions: str
    pattern: str | None = None
    count: int | None = None
    blob_sha256: str | None = None


@dataclass
class Profile:
    """The validated profile."""

    base: str
    identity_name: str
    releases: list[Release]
    exclude: list[Exclude]
    expected_skips: list[dict[str, str]]
    top_level: list[str]
    scan: ScanConfig = field(default_factory=ScanConfig)
    allowlist: list[AllowRow] = field(default_factory=list)
    required_digest_labels: tuple[str, ...] = ()

    def release(self, version: str) -> tuple[int, Release]:
        """The index and row of `version`; a version not in the profile refuses."""
        for index, release in enumerate(self.releases):
            if release.version == version:
                return index, release
        raise Refusal(f"version {version} is not in the profile")


def vkey(version: str) -> tuple[int, int, int]:
    """A version's sort key; a malformed version refuses."""
    match = VERSION_RE.match(version)
    if not match:
        raise Refusal(f"bad version {version!r}")
    return int(match[1]), int(match[2]), int(match[3])


def applies(selector: str, version: str) -> bool:
    """Whether a `versions:` selector (`all`, `<v>`, `<=<v>`, `>=<v>`) covers `version`."""
    if selector == "all":
        return True
    match = SELECTOR_RE.match(selector)
    op, bound = (match[1], match[2]) if match else ("", selector)
    have, want = vkey(version), vkey(bound)
    return {"<=": have <= want, ">=": have >= want}.get(op or "", have == want)


def _keys(where: str, value: Any, required: set[str], optional: frozenset[str] = frozenset()):
    if not isinstance(value, dict):
        raise Refusal(f"{where}: expected a mapping")
    unknown = sorted(set(map(str, value)) - required - optional)
    if unknown:
        raise Refusal(f"{where}: unknown key {unknown[0]!r}")
    missing = sorted(required - set(value))
    if missing:
        raise Refusal(f"{where}: missing key {missing[0]!r}")
    return value


def _str(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise Refusal(f"{where}: expected a non-empty string")
    return value


def _sha(where: str, value: Any) -> str:
    if not isinstance(value, str) or not SHA_RE.match(value):
        raise Refusal(f"{where}: expected a full 40-hex commit sha")
    return value


def _relpath(where: str, value: Any, allow_dir: bool) -> str:
    path = _str(where, value)
    bare = path[:-1] if allow_dir and path.endswith("/") else path
    parts = bare.split("/")
    if path.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in path:
        raise Refusal(f"{where}: {path!r} is not a clean relative path")
    return path


def _selector(where: str, value: Any) -> str:
    selector = _str(where, value)
    if selector != "all":
        match = SELECTOR_RE.match(selector)
        if not match or not VERSION_RE.match(match[2]):
            raise Refusal(f"{where}: bad versions selector {selector!r}")
    return selector


def _list(where: str, value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise Refusal(f"{where}: expected a list")
    return value


def _label(where: str, value: Any) -> str:
    label = _str(where, value)
    if not LABEL_RE.match(label):
        raise Refusal(f"{where}: {label!r} is not a lower-case label")
    return label


def _digest(where: str, value: Any) -> str:
    if not isinstance(value, str) or not DIGEST_RE.match(value):
        raise Refusal(f"{where}: expected a 64-hex sha256")
    return value


def _count(where: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Refusal(f"{where}: expected a positive integer")
    return value


def _email_entry(where: str, value: Any) -> str:
    """An email allowlist entry: an address, a domain, or `*.` and a domain suffix."""
    entry = _str(where, value)
    bare = entry[2:] if entry.startswith("*.") else entry
    if entry != entry.lower() or not re.fullmatch(
        r"([a-z0-9._%+-]+@)?[a-z0-9-]+(\.[a-z0-9-]+)*", bare
    ):
        raise Refusal(f"{where}: {entry!r} is not a lower-case address, domain or *.suffix")
    return entry


def _scan_config(value: Any) -> ScanConfig:
    """The `scan` section (mini-requirement 1); `{}` means none (a build needs none)."""
    if value == {}:
        return ScanConfig()
    raw = _keys("scan", value, {"strict", "strict_keys", "email_allowlist", "advisory"})
    strict, labels = [], {RAW_KEY}
    for i, row in enumerate(_list("scan.strict", raw["strict"])):
        where = f"scan.strict[{i}]"
        _keys(where, row, {"label", "literal"})
        label = _label(f"{where}.label", row["label"])
        literal = _str(f"{where}.literal", row["literal"])
        if TOOL_LITERALS.get(label) != literal:
            raise Refusal(
                f"{where}: only the tool's built-in literals ({', '.join(TOOL_LITERALS)})"
            )
        strict.append(StrictPattern(label, literal))
        labels.add(label)
    keys = []
    for i, key in enumerate(_list("scan.strict_keys", raw["strict_keys"])):
        key = _str(f"scan.strict_keys[{i}]", key)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) or key in keys:
            raise Refusal(f"scan.strict_keys[{i}]: not a bare JSON key name, or a duplicate")
        keys.append(key)
    emails = tuple(
        _email_entry(f"scan.email_allowlist[{i}]", e)
        for i, e in enumerate(_list("scan.email_allowlist", raw["email_allowlist"]))
    )
    advisory = []
    for i, row in enumerate(_list("scan.advisory", raw["advisory"])):
        where = f"scan.advisory[{i}]"
        _keys(where, row, {"label", "regex"})
        regex = _str(f"{where}.regex", row["regex"])
        try:
            re.compile(regex.encode("utf-8"))
        except re.error as exc:
            raise Refusal(f"{where}.regex: does not compile ({exc})") from None
        advisory.append(AdvisoryPattern(_label(f"{where}.label", row["label"]), regex))
        labels.add(advisory[-1].label)
    if len(labels) != 1 + len(strict) + len(advisory):
        raise Refusal("scan: a label is used twice")
    return ScanConfig(tuple(strict), emails, tuple(advisory), tuple(keys))


def _allowlist(value: Any, scan: ScanConfig, private: tuple[str, ...] = ()) -> list[AllowRow]:
    """The `allowlist` rows (mini-requirement 1, mission R19); never a digest-file label."""
    rows, seen = [], set()
    strict = {p.label: p for p in scan.strict}
    for i, row in enumerate(_list("allowlist", value)):
        where = f"allowlist[{i}]"
        if isinstance(row, dict) and "blob_sha256" in row:
            _keys(where, row, {"path", "blob_sha256", "reason", "versions"})
            entry = AllowRow(
                _relpath(f"{where}.path", row["path"], allow_dir=False),
                _str(f"{where}.reason", row["reason"]),
                _selector(f"{where}.versions", row["versions"]),
                blob_sha256=_digest(f"{where}.blob_sha256", row["blob_sha256"]),
            )
        else:
            _keys(where, row, {"path", "pattern", "count", "reason", "versions"})
            pattern = _str(f"{where}.pattern", row["pattern"])
            if pattern.startswith(EMAIL_PATTERN):
                if not EMAIL_RE.fullmatch(pattern[len(EMAIL_PATTERN) :].encode("utf-8")):
                    raise Refusal(f"{where}.pattern: {pattern!r} is not email:<address>")
            elif pattern in private:
                raise Refusal(f"{where}.pattern: {pattern!r} may never be allowlisted")
            elif pattern == RAW_KEY:
                if row["path"] != PROFILE_REL:
                    raise Refusal(f"{where}: a {RAW_KEY} row may name only {PROFILE_REL}")
            elif pattern not in strict:
                raise Refusal(f"{where}.pattern: {pattern!r} is not a scan.strict label or email:")
            entry = AllowRow(
                _relpath(f"{where}.path", row["path"], allow_dir=False),
                _str(f"{where}.reason", row["reason"]),
                _selector(f"{where}.versions", row["versions"]),
                pattern=pattern,
                count=_count(f"{where}.count", row["count"]),
            )
        key = (entry.path, entry.pattern, entry.versions)
        if key in seen:
            raise Refusal(f"{where}: duplicates an earlier row")
        seen.add(key)
        rows.append(entry)
    if rows and not scan.strict:
        raise Refusal("allowlist: rows need a scan section")
    return rows


def load_profile(path: Path) -> Profile:
    """Read and validate the profile's schema; unknown keys anywhere refuse."""
    if yaml is None:
        raise Refusal("PyYAML is not installed")
    try:
        with path.open("rb") as handle:
            data = handle.read(PROFILE_MAX_BYTES + 1)
    except OSError as exc:
        raise Refusal(f"cannot read the profile: {exc.strerror}") from None
    if len(data) > PROFILE_MAX_BYTES:
        raise Refusal(f"the profile is larger than {PROFILE_MAX_BYTES} bytes")
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Refusal(f"the profile is not valid YAML: {type(exc).__name__}") from None

    top = {"schema", "base", "identity", "releases", "exclude", "expected_skips", "top_level"}
    top |= {"required_digest_labels"}
    _keys("profile", doc, top | {"scan", "allowlist"})
    if doc["schema"] != SCHEMA:
        raise Refusal(f"profile: schema must be {SCHEMA}")
    # P18 (lead ruling): a binary allowlist row's blob_sha256 hashes a blob of the public tree,
    # never a private literal: the one 64-hex value v2 allows, and only in that field. `profile
    # check` binds it to a real blob of its path.
    for value in profile_keys_and_strings(_without_blob_shas(doc)):
        if value == "sha256" or HEX64_RE.search(value):
            raise Refusal("profile: holds a sha256 key or a 64-hex value (v2 names no digest)")
    required = tuple(
        _label(f"required_digest_labels[{i}]", label)
        for i, label in enumerate(_list("required_digest_labels", doc["required_digest_labels"]))
    )
    if len(set(required)) != len(required):
        raise Refusal("required_digest_labels: a label is listed twice")
    identity = _keys("identity", doc["identity"], {"name", "email_from"})
    if identity["email_from"] != "base":
        raise Refusal("identity: email_from must be 'base'")
    if not isinstance(identity["name"], str) or re.search(r"[<>\x00-\x1f]", identity["name"]):
        raise Refusal("identity.name: holds <, > or a control character")

    releases = []
    for i, row in enumerate(_list("releases", doc["releases"])):
        where = f"releases[{i}]"
        _keys(where, row, {"version", "source"}, frozenset({"overlay"}))
        version = _str(f"{where}.version", row["version"])
        if not VERSION_RE.match(version):
            raise Refusal(f"{where}.version: {version!r} is not MAJOR.MINOR.PATCH")
        overlay = None
        if "overlay" in row:
            raw = _keys(f"{where}.overlay", row["overlay"], {"commit", "paths"})
            commit = (
                None if raw["commit"] is None else _sha(f"{where}.overlay.commit", raw["commit"])
            )
            paths = tuple(
                _relpath(f"{where}.overlay.paths[{j}]", p, allow_dir=False)
                for j, p in enumerate(_list(f"{where}.overlay.paths", raw["paths"]))
            )
            if not paths or len(set(paths)) != len(paths):
                raise Refusal(f"{where}.overlay.paths: empty or duplicated")
            overlay = Overlay(commit, paths)
        releases.append(Release(version, _sha(f"{where}.source", row["source"]), overlay))
    if not releases:
        raise Refusal("releases: the profile has no release")

    excludes = []
    for i, row in enumerate(_list("exclude", doc["exclude"])):
        where = f"exclude[{i}]"
        _keys(where, row, {"path", "reason", "versions"})
        excludes.append(
            Exclude(
                _relpath(f"{where}.path", row["path"], allow_dir=True),
                _str(f"{where}.reason", row["reason"]),
                _selector(f"{where}.versions", row["versions"]),
            )
        )
    skips = []
    for i, row in enumerate(_list("expected_skips", doc["expected_skips"])):
        where = f"expected_skips[{i}]"
        _keys(where, row, {"versions", "nodeid", "reason_contains"})
        _selector(f"{where}.versions", row["versions"])
        skips.append({k: _str(f"{where}.{k}", row[k]) for k in row})
    top_level = [
        _relpath(f"top_level[{i}]", name, allow_dir=False)
        for i, name in enumerate(_list("top_level", doc["top_level"]))
    ]
    if any("/" in name for name in top_level):
        raise Refusal("top_level: a name holds a /")
    scan = _scan_config(doc.get("scan", {}))
    clash = set(required) & ({p.label for p in scan.strict} | {a.label for a in scan.advisory})
    if clash:
        raise Refusal(f"scan: {sorted(clash)[0]!r} is a digest-file label")
    allowlist = _allowlist(doc.get("allowlist", []), scan, required)
    return Profile(
        _sha("base", doc["base"]),
        _str("identity.name", identity["name"]),
        releases,
        excludes,
        skips,
        top_level,
        scan,
        allowlist,
        required,
    )


def strict_hits(value: str) -> bool:
    """Whether `value` contains one of the tool's built-in strict literals."""
    return any(literal in value for literal in TOOL_LITERALS.values())


def _without_blob_shas(doc: Any) -> Any:
    """The parsed profile with each allowlist row's `blob_sha256` field left out (P18)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("allowlist"), list):
        return doc
    rows = [
        {k: v for k, v in r.items() if k != "blob_sha256"} if isinstance(r, dict) else r
        for r in doc["allowlist"]
    ]
    return {**doc, "allowlist": rows}


def profile_keys_and_strings(node: Any):
    """Every key and string value in a parsed profile (keys too: a key may hold a digest)."""
    yield from profile_strings(node)


def profile_strings(node: Any):
    """Every key and string value in a parsed profile."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from profile_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from profile_strings(value)
    elif isinstance(node, str):
        yield node


def tree_paths(repo: Path, commit: str) -> list[str]:
    """Every file path of a commit's tree."""
    out = git(repo, "ls-tree", "-r", "-z", "--name-only", "--full-tree", commit).stdout
    return [p for p in out.split("\0") if p]


def excluded(path: str, row: Exclude) -> bool:
    """Whether an exclude row covers `path`."""
    return path.startswith(row.path) if row.is_dir else path == row.path


def export_paths(repo: Path, profile: Profile, release: Release) -> list[str]:
    """The file paths of a version's export tree, computed from the source listing."""
    rows = [row for row in profile.exclude if applies(row.versions, release.version)]
    paths = {p for p in tree_paths(repo, release.source) if not any(excluded(p, r) for r in rows)}
    if release.overlay and release.overlay.commit:
        paths.update(release.overlay.paths)
    return sorted(paths)


def blob_sha256_at(repo: Path, commit: str, path: str) -> str | None:
    """The sha256 of `path`'s content at `commit`, or None when it is absent there."""
    args = ("cat-file", "blob", f"{commit}:{path}")
    proc = subprocess.run(git_argv(repo, args), capture_output=True, env=git_env(), check=False)
    return hashlib.sha256(proc.stdout).hexdigest() if proc.returncode == 0 else None


def check_blob_row(repo: Path, profile: Profile, index: int, row: AllowRow) -> None:
    """P18: a binary row's blob_sha256 must be a real blob of its path (never echoed)."""
    for release in profile.releases:
        if applies(row.versions, release.version):
            if blob_sha256_at(repo, release.source, row.path) == row.blob_sha256:
                return
    if blob_sha256_at(repo, "HEAD", row.path) == row.blob_sha256:
        emit(f"pending: allowlist[{index}] matches its blob only at HEAD (no release row yet)")
        return
    raise Refusal(f"allowlist[{index}]: blob_sha256 is not the sha256 of its path at any version")


def profile_check(repo: Path, profile_path: Path) -> None:
    """Validate the profile against the repository (mini-requirement 2); print `profile ok`."""
    profile = load_profile(profile_path)
    with profile_path.open("rb") as handle:
        doc = yaml.safe_load(handle.read().decode("utf-8"))
    for row in (doc.get("scan") or {}).get("strict") or []:
        row.pop("literal", None)  # the literal rows themselves are the only place they may be
    for value in profile_strings(doc):
        if strict_hits(value):
            raise Refusal("a profile value matches a strict scan literal")

    keys = [vkey(r.version) for r in profile.releases]
    if any(a >= b for a, b in zip(keys, keys[1:])):
        raise Refusal("releases: versions are not strictly increasing")
    previous = profile.base
    if rev(repo, f"{profile.base}^{{commit}}") != profile.base:
        raise Refusal(f"base {profile.base[:12]} is not a commit in this repository")
    for release in profile.releases:
        if rev(repo, f"{release.source}^{{commit}}") != release.source:
            raise Refusal(f"{release.version}: source {release.source[:12]} is not a commit")
        if not is_ancestor(repo, previous, release.source):
            raise Refusal(f"{release.version}: source does not descend from {previous[:12]}")
        previous = release.source

    listings = {r.version: tree_paths(repo, r.source) for r in profile.releases}
    head: list[str] | None = None
    for row in profile.exclude:
        if not any(any(excluded(p, row) for p in listing) for listing in listings.values()):
            head = tree_paths(repo, "HEAD") if head is None else head
            if not any(excluded(p, row) for p in head):
                raise Refusal(f"exclude row {row.path!r} matches nothing at every release")
            emit(f"pending: exclude row {row.path} matches only HEAD (no release row ships it yet)")

    missing = profile.scan.missing_tool_literals()
    if missing:
        raise Refusal(f"scan.strict: misses the tool's strict literal {missing[0]}")
    overlaid = {p for r in profile.releases if r.overlay for p in r.overlay.paths}
    for index, row in enumerate(profile.allowlist):
        if row.blob_sha256 is not None:
            check_blob_row(repo, profile, index, row)
        if (
            not any(row.path in listing for listing in listings.values())
            and row.path not in overlaid
        ):
            raise Refusal(f"allowlist row {row.path!r} matches nothing at every release")

    names: set[str] = set()
    for release in profile.releases:
        names.update(p.split("/", 1)[0] for p in export_paths(repo, profile, release))
    for name in profile.top_level:
        if name not in names:
            raise Refusal(f"top_level name {name!r} is in no release's export tree")
    emit("profile ok")


# ------------------------------------------------------------------------------------- build


def changelog_section(repo: Path, source: str, version: str) -> str:
    """The source CHANGELOG's `## [<version>]` section, trailing blank lines stripped (R4)."""
    proc = git(repo, "cat-file", "blob", f"{source}:CHANGELOG.md", check=False)
    if proc.returncode != 0:
        raise Refusal(f"{version}: the source has no CHANGELOG.md")
    lines, section = proc.stdout.split("\n"), None
    for i, line in enumerate(lines):
        if section is None and line.startswith(f"## [{version}]"):
            section = [line]
        elif section is not None:
            if line.startswith("## ["):
                break
            section.append(line)
    if section is None:
        raise Refusal(f"{version}: the source's CHANGELOG has no ## [{version}] section")
    while section and not section[-1].strip():
        section.pop()
    return "\n".join(section)


def export_tree(repo: Path, profile: Profile, release: Release) -> str:
    """Write the export tree from a temporary index outside every repository; return its sha."""
    tmp = tempfile.mkdtemp(prefix="hacf-export-index-")
    try:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        git(repo, "read-tree", release.source, env=env)
        for row in profile.exclude:
            if applies(row.versions, release.version):
                # -f: the temporary index matches neither HEAD nor the working tree, so git rm's
                # up-to-date check would refuse; --cached keeps every working-tree file untouched.
                git(
                    repo,
                    *("rm", "-r", "-f", "--cached", "--quiet", "--ignore-unmatch", "--"),
                    row.bare,
                    env=env,
                )
        if release.overlay:
            for path in release.overlay.paths:
                entry = git(
                    repo, "ls-tree", "-z", "--full-tree", release.overlay.commit or "", "--", path
                ).stdout
                meta, _, name = entry.rstrip("\0").partition("\t")
                parts = meta.split()
                if name != path or len(parts) != 3 or parts[1] != "blob":
                    raise Refusal(f"{release.version}: overlay path {path} is absent")
                info = f"{parts[0]},{parts[2]},{path}"
                git(repo, "update-index", "--add", "--cacheinfo", info, env=env)
        return git(repo, "write-tree", env=env).stdout.strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def published(repo: Path, remote: str, version: str, commit: str) -> bool:
    """Mission R5: the tag is on the remote, or the export is reachable from the remote's main."""
    proc = git(repo, "ls-remote", "--end-of-options", remote, f"refs/tags/v{version}", check=False)
    if proc.returncode != 0:
        raise Refusal(f"cannot read remote {remote} (ls-remote)")
    if proc.stdout.strip():
        return True
    fetch = git(
        repo,
        *("fetch", "--quiet", "--no-tags", "--refmap=", "--no-recurse-submodules"),
        *("--no-auto-maintenance", "--end-of-options", remote, "refs/heads/main"),
        check=False,
    )
    if fetch.returncode != 0:
        raise Refusal(f"cannot read remote {remote} (fetch main)")
    fetched = rev(repo, "FETCH_HEAD")
    if fetched is None:
        raise Refusal(f"cannot read remote {remote} (no FETCH_HEAD)")
    return is_ancestor(repo, commit, fetched)


def build(repo: Path, profile_path: Path, version: str, replace: bool, remote: str) -> str:
    """Build, or confirm, one version's export commit and tag; return the output line."""
    profile = load_profile(profile_path)
    index, release = profile.release(version)
    tag_ref = f"refs/tags/v{version}"

    if rev(repo, f"{profile.base}^{{commit}}") != profile.base:
        raise Refusal(f"base {profile.base[:12]} is not a commit in this repository")
    if rev(repo, f"{release.source}^{{commit}}") != release.source:
        raise Refusal(f"{version}: source {release.source[:12]} is not a commit")
    if index == 0:
        parent, previous_source = profile.base, profile.base
    else:
        previous = profile.releases[index - 1]
        parent = rev(repo, f"refs/tags/v{previous.version}^{{commit}}")
        if parent is None:
            raise Refusal(f"{version}: the previous release {previous.version} has no export")
        previous_source = previous.source
    if not is_ancestor(repo, previous_source, release.source):
        raise Refusal(f"{version}: the source does not descend from {previous_source[:12]}")
    if release.overlay and release.overlay.commit is None:
        raise Refusal(f"{version}: the overlay commit is null")
    if release.overlay and rev(repo, f"{release.overlay.commit}^{{commit}}") is None:
        raise Refusal(f"{version}: the overlay commit is not a commit")
    public = rev(repo, PUBLIC_MAIN)
    if public is not None and not is_ancestor(repo, profile.base, public):
        raise Refusal("public/main does not descend from the base")

    # The objects: tree, commit, tag. Writing objects changes no ref.
    message = changelog_section(repo, release.source, version) + "\n\n"
    message += f"HACF-Source-Commit: {release.source}\n"
    for path in release.overlay.paths if release.overlay else ():
        message += f"HACF-Overlay: {release.overlay.commit} {path}\n"  # type: ignore[union-attr]
    tree = export_tree(repo, profile, release)
    email = git(repo, "log", "-1", "--format=%ae", profile.base).stdout.strip()
    if not email or re.search(r"[<>\s]", email):
        raise Refusal("cannot read the base's author email")
    name = profile.identity_name
    author_date, committer_date = raw_dates(repo, release.source)
    who = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": committer_date,
    }
    commit = git(repo, "commit-tree", tree, "-p", parent, "-F", "-", env=who, stdin=message)
    commit_sha = commit.stdout.strip()
    tag_text = (
        f"object {commit_sha}\ntype commit\ntag v{version}\n"
        f"tagger {name} <{email}> {committer_date}\n\nHACF v{version}\n"
    )
    tag_sha = git(repo, "mktag", stdin=tag_text).stdout.strip()

    existing_tag, replace_ok = rev(repo, tag_ref), False
    if existing_tag is not None:
        existing_commit = rev(repo, f"{tag_ref}^{{commit}}")
        if existing_tag == tag_sha and existing_commit == commit_sha:
            return f"unchanged {commit_sha} {tag_sha}"
        if not replace:
            raise Refusal(f"{version}: exported with different inputs")
        if not REMOTE_RE.match(remote):
            raise Refusal(f"bad remote name {remote!r}")
        if published(repo, remote, version, existing_commit or ""):
            raise Refusal(f"{version}: published")
        if public != existing_commit:
            raise Refusal(f"{version}: the export is not the public/main tip")
        back = rev(repo, f"{existing_commit}^")
        if back is None:
            raise Refusal(f"{version}: the export has no parent")
        git(repo, "update-ref", PUBLIC_MAIN, back, existing_commit or "")
        git(repo, "update-ref", "-d", tag_ref, existing_tag)
        public, replace_ok = back, True

    if index == 0:
        # After a replace, public/main is back at the base; otherwise it must be absent or
        # already this export.
        if public is not None and public not in (commit_sha, *((parent,) if replace_ok else ())):
            raise Refusal(f"{version}: public/main exists and is not this version's export")
    elif public != parent:
        raise Refusal(
            f"{version}: public/main is not at the v{profile.releases[index - 1].version} export"
        )
    git(repo, "update-ref", PUBLIC_MAIN, commit_sha, public or ZERO_SHA)
    git(repo, "update-ref", tag_ref, tag_sha, ZERO_SHA)
    return f"created {commit_sha} {tag_sha}"


# -------------------------------------------------------------------------------------- scan


@dataclass
class DigestFile:
    """The private digest file (F1): the only place private literals live, as digests."""

    addresses: dict[str, str] = field(default_factory=dict)  # sha256 -> label (an address)
    windows: list[tuple[str, int, str, str]] = field(default_factory=list)  # sha, len, label, level
    regexes: list[tuple[str, re.Pattern[bytes]]] = field(default_factory=list)  # advisory only
    labels: set[str] = field(default_factory=set)


def _digest_line(line: str, file: DigestFile) -> bool:
    """Parse one non-comment line into `file`; False when it has no known shape."""
    words = line.split()
    if words[0] == "window" and len(words) == 5:
        _, digest, length, label, level = words
        if not (DIGEST_RE.match(digest) and length.isdigit() and LABEL_RE.match(label)):
            return False
        if not 1 <= int(length) <= 256 or level not in ("strict", "advisory"):
            return False
        file.windows.append((digest, int(length), label, level))
    elif words[0] == "regex" and len(words) >= 4:
        label, level = words[1], words[2]
        pattern = line.split(None, 3)[3]
        if not LABEL_RE.match(label) or level != "advisory":
            return False
        try:
            file.regexes.append((label, re.compile(pattern.encode("utf-8"))))
        except re.error:
            return False
    elif DIGEST_RE.match(words[0]) and len(words) <= 2:
        label = words[1] if len(words) == 2 else f"#{len(file.addresses) + 1}"
        if "@" in label:
            return False
        file.addresses[words[0]] = label
    else:
        return False
    file.labels.add(label)
    return True


def load_digests(arg: str | None) -> DigestFile:
    """The private digest file (mission R9, F1, F6); any defect refuses.

    `--digests` or `$HACF_EXPORT_DIGESTS`, never tracked; its path and lines never reach the output,
    a report or a log. Line kinds: `<64 hex> [label]` (a private address), `window <64 hex>
    <length> <label> strict|advisory` (a literal, matched by hashing every window of that length)
    and `regex <label> advisory <pattern>`; `#` comments and blank lines. `digest-line` prints a
    line from a literal on stdin.
    """
    source = arg or os.environ.get("HACF_EXPORT_DIGESTS", "")
    if not source:
        raise Refusal("no digest file: pass --digests or set HACF_EXPORT_DIGESTS")
    try:
        text = Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refusal("the digest file is missing or unreadable") from None
    file = DigestFile()
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.search(r"[\x00-\x1f]", line) or not _digest_line(line, file):
            raise Refusal(f"the digest file is malformed (line {number})")
    if not file.labels:
        raise Refusal("the digest file holds no digest")
    _REDACT.clear()
    _REDACT.update(file.addresses)
    return file


def require_labels(digests: DigestFile, profile: Profile) -> None:
    """Refuse, before any scan, a digest file that lacks a label the profile requires (F1)."""
    for label in profile.required_digest_labels:
        if label not in digests.labels:
            raise Refusal(f"the digest file lacks the required label {label}")


def digest_line(label: str, kind: str, level: str, text: str) -> str:
    """One digest-file line for a literal (or pattern) read from stdin; never echoes it."""
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise Refusal("digest-line: stdin is empty")
    if not LABEL_RE.match(label):
        raise Refusal("digest-line: the label is not [a-z][a-z0-9-]*")
    if kind == "regex":
        if level != "advisory":
            raise Refusal("digest-line: a regex line is advisory only")
        if "\n" in text:
            raise Refusal("digest-line: a regex must be one line")
        try:
            re.compile(text.encode("utf-8"))
        except re.error:
            raise Refusal("digest-line: the regex does not compile") from None
        return f"regex {label} advisory {text}"
    data = text.encode("utf-8")
    if len(data) > 256:
        raise Refusal("digest-line: a window literal is at most 256 bytes")
    return f"window {hashlib.sha256(data).hexdigest()} {len(data)} {label} {level}"


# The private-address digests every written line is redacted against (mission R9): set by
# load_digests; empty until a command loads a digest file.
_REDACT: dict[str, str] = {}
_REDACT_CACHE: dict[str, str | None] = {}
EMAIL_TEXT_RE = re.compile(EMAIL_RE.pattern.decode("ascii"))


def redact(text: str) -> str:
    """`text` with every token holding a private address replaced by its digest label (R9).

    The one redaction pass: every line the tool writes (stdout, stderr, REPORT.md, every log,
    PUSH-PLAN.md) goes through it, so a private address in a message, a path name or a command's
    output never reaches the output, whatever printed it.
    """
    if not _REDACT:
        return text

    def replace(match: re.Match[str]) -> str:
        label = digest_label(match[0].lower(), _REDACT, _REDACT_CACHE)
        return match[0] if label is None else f"<{DIGEST_PATTERN} (digest {label})>"

    return EMAIL_TEXT_RE.sub(replace, text)


def emit(text: str = "", *, end: str = "\n", err: bool = False) -> None:
    """Print one redacted text to stdout (or stderr)."""
    print(redact(text), end=end, file=sys.stderr if err else sys.stdout)


def write_output(path: Path, text: str) -> None:
    """Write one redacted report, plan or log file."""
    path.write_text(redact(text), encoding="utf-8")


@dataclass(frozen=True)
class ScanItem:
    """One scanned unit: a blob, a path name, or a raw commit or tag object."""

    path: str
    data: bytes
    kind: str  # "blob", "name" or "object"

    @property
    def binary(self) -> bool:
        """Whether a blob is binary (a NUL byte near its start)."""
        return self.kind == "blob" and b"\0" in self.data[:BINARY_SNIFF]


@dataclass
class ScanResult:
    """A scan's outcome: report lines, the per-pattern table and the failures."""

    lines: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)
    table: dict[str, list[int]] = field(default_factory=dict)  # pattern: [hits, allowed, failing]
    failures: int = 0

    def render(self) -> str:
        """The per-pattern table, then every hit line, then the advisory hits."""
        out = ["pattern                          hits  allowed  failing"]
        for label, (hits, ok, bad) in self.table.items():
            out.append(f"{label:<32} {hits:>4}  {ok:>7}  {bad:>7}")
        out += ["", "strict hits:", *(self.lines or ["(none)"])]
        out += ["", "advisory (never failing):", *(self.advisory or ["(none)"])]
        out += ["", f"strict failures: {self.failures}"]
        return "\n".join(out) + "\n"


_LEARNED: dict[str, bytes] = {}


def learn_literals(items: list[ScanItem], patterns: list[tuple[str, int]]) -> dict[str, bytes]:
    """The bytes of each strict pattern that occurs in `items`, by hashing every window.

    The literals are known only by digest; once a window matches, its bytes are known and every
    count after that is a plain search. A literal that occurs nowhere costs one pass per length.
    """
    found = {sha: _LEARNED[sha] for sha, _ in patterns if sha in _LEARNED}
    wanted: dict[int, set[str]] = {}
    for sha, length in patterns:
        if sha not in found:
            wanted.setdefault(length, set()).add(sha)
    for length, digests in wanted.items():
        seen: set[bytes] = set()
        for item in items:
            data = item.data
            for start in range(len(data) - length + 1):
                window = data[start : start + length]
                if window in seen:
                    continue
                seen.add(window)
                digest = hashlib.sha256(window).hexdigest()
                if digest in digests:
                    found[digest] = _LEARNED[digest] = window
                    digests.discard(digest)
                    if not digests:
                        break
            if not digests:
                break
    return found


def line_numbers(data: bytes, positions: list[int]) -> list[int]:
    """The 1-based line of each (ascending) byte position."""
    lines, line, last = [], 1, 0
    for pos in positions:
        line += data.count(b"\n", last, pos)
        last = pos
        lines.append(line)
    return lines


def find_all(data: bytes, needle: bytes) -> list[int]:
    """The start of every non-overlapping occurrence of `needle`."""
    out, start = [], data.find(needle)
    while start != -1:
        out.append(start)
        start = data.find(needle, start + len(needle))
    return out


def email_allowed(token: str, allow: tuple[str, ...]) -> bool:
    """Whether a lower-cased email token is on the email allowlist."""
    domain = token.rpartition("@")[2]
    for entry in allow:
        if "@" in entry:
            if token == entry:
                return True
        elif entry.startswith("*."):
            if domain.endswith(entry[1:]):
                return True
        elif domain == entry:
            return True
    return False


def digest_label(token: str, digests: dict[str, str], cache: dict[str, str | None]) -> str | None:
    """The digest label when `token`, or any address-shaped part of it, is a private address."""
    if token not in cache:
        cache[token] = None
        at = token.index("@")
        for start in range(at + 1):
            for end in range(len(token), at + 1, -1):
                digest = hashlib.sha256(token[start:end].encode("utf-8")).hexdigest()
                if digest in digests:
                    cache[token] = digests[digest]
                    return cache[token]
    return cache[token]


def advisory_patterns(profile: Profile, version: str) -> list[tuple[str, re.Pattern[bytes]]]:
    """The profile's advisory patterns, plus a mention of each excluded path at `version`."""
    out = [(a.label, re.compile(a.regex.encode("utf-8"))) for a in profile.scan.advisory]
    for row in profile.exclude:
        if applies(row.versions, version):
            needle = re.escape(row.path.encode("utf-8"))
            out.append((f"mentions {row.path}", re.compile(rb"(?<![\w.-])" + needle)))
    return out


def raw_key_patterns(keys: tuple[str, ...]) -> list[re.Pattern[bytes]]:
    """Each raw-only key as a JSON key, plain or inside escaped JSON strings at any depth (F7)."""
    return [re.compile(rb'\\*"' + re.escape(k.encode()) + rb'\\*"\s*:') for k in keys]


def run_scan(
    items: list[ScanItem], profile: Profile, version: str, digests: DigestFile
) -> ScanResult:
    """Scan items against strict, digest and advisory patterns and the allowlist (R9, R19)."""
    if profile.scan.missing_tool_literals():
        raise Refusal("the profile's scan section does not hold the tool's strict literals")
    result = ScanResult()
    strict_windows = [(d, n, label) for d, n, label, level in digests.windows if level == "strict"]
    never = {label for _, _, label in strict_windows}
    labels = [p.label for p in profile.scan.strict] + sorted(never)
    for label in [*labels, RAW_KEY, "email", DIGEST_PATTERN]:
        result.table[label] = [0, 0, 0]
    literals = {p.label: p.literal.encode("utf-8") for p in profile.scan.strict}
    learned = learn_literals(items, [(d, n) for d, n, _, _ in digests.windows])
    literals.update({label: learned[d] for d, _, label in strict_windows if d in learned})
    # A digest-file literal is never printed: in every reported path it reads <label>.
    masks = sorted(
        ((learned[d].decode("utf-8", "surrogateescape"), label)
         for d, _, label, _ in digests.windows if d in learned),
        key=lambda pair: -len(pair[0]),
    )  # fmt: skip

    def shown(path: str) -> str:
        for literal, label in masks:
            path = path.replace(literal, f"<{label}>")
        return path

    keys = raw_key_patterns(profile.scan.strict_keys)
    hits: dict[tuple[str, str, str], list[int]] = {}
    blobs = {item.path: item for item in items if item.kind == "blob"}
    cache: dict[str, str | None] = {}
    for item in items:
        for label, literal in literals.items():
            positions = find_all(item.data, literal)
            if positions:
                hits[(item.kind, item.path, label)] = line_numbers(item.data, positions)
        if item.kind == "blob":
            positions = sorted(m.start() for key in keys for m in key.finditer(item.data))
            if positions:
                hits[(item.kind, item.path, RAW_KEY)] = line_numbers(item.data, positions)
        for match in EMAIL_RE.finditer(item.data):
            token = match.group().decode("ascii").lower()
            label = digest_label(token, digests.addresses, cache)
            if label is not None:
                key = f"{DIGEST_PATTERN} (digest {label})"
            elif email_allowed(token, profile.scan.email_allowlist):
                continue
            else:
                key = EMAIL_PATTERN + token
            hits.setdefault((item.kind, item.path, key), []).extend(
                line_numbers(item.data, [match.start()])
            )

    rows = [r for r in profile.allowlist if applies(r.versions, version)]
    used: set[int] = set()
    for (kind, path, pattern), lines in sorted(hits.items()):
        count, note = len(lines), ""
        where = {"blob": shown(path), "name": f"{shown(path)} (path name)"}.get(kind, path)
        allowed = False
        blob = blobs.get(path) if kind == "blob" else None
        if pattern.startswith(DIGEST_PATTERN) or pattern in never:
            note = "never allowlisted"
        elif blob is not None and blob.binary and pattern.startswith(EMAIL_PATTERN):
            # A binary row covers only the allowlistable home-root literals; an email is
            # allowlisted only by the email allowlist (lead's ruling on R9).
            note = "an email is allowlisted only by the email allowlist, never by a binary row"
        elif blob is not None and blob.binary:
            sha = hashlib.sha256(blob.data).hexdigest()
            match = [i for i, r in enumerate(rows) if r.path == path and r.blob_sha256]
            if match and rows[match[0]].blob_sha256 == sha:
                allowed, note = True, f"binary row ({rows[match[0]].reason})"
                used.add(match[0])
            elif match:
                used.add(match[0])
                note = f"binary blob sha256 {sha[:12]} differs from the row's"
            else:
                note = f"binary blob sha256 {sha} has no allowlist row"
        elif blob is not None:
            match = [i for i, r in enumerate(rows) if r.path == path and r.pattern == pattern]
            if match:
                row = rows[match[0]]
                used.add(match[0])
                allowed = row.count == count
                note = row.reason if allowed else f"count {count}, the row says {row.count}"
            else:
                note = "no allowlist row"
        else:
            note = "a path name or object header is never allowlisted"
        table_key = (
            "email"
            if pattern.startswith(EMAIL_PATTERN)
            else DIGEST_PATTERN
            if pattern.startswith(DIGEST_PATTERN)
            else pattern
        )
        cell = result.table[table_key]
        cell[0] += count
        cell[1 if allowed else 2] += count
        result.failures += 0 if allowed else 1
        at = "" if blob is not None and blob.binary else " lines " + ",".join(map(str, lines))
        verdict = "ok  " if allowed else "FAIL"
        result.lines.append(f"{verdict} {pattern}  {where}  x{count}{at}  ({note})")
    present = {item.path for item in items if item.kind == "blob"}
    for i, row in enumerate(rows):
        if i in used:
            continue
        name = row.pattern or "binary"
        if row.path not in present:
            result.lines.append(f"n/a  {name}  {shown(row.path)}  (absent at {version})")
        else:
            result.failures += 1
            want = f"x{row.count}" if row.count else f"blob {(row.blob_sha256 or '')[:12]}"
            result.lines.append(f"FAIL {name}  {shown(row.path)}  x0  (the row expects {want})")

    windows = [(d, n, label) for d, n, label, level in digests.windows if level == "advisory"]
    extra = [(label, re.compile(re.escape(learned[d]))) for d, _, label in windows if d in learned]
    for label, regex in [*advisory_patterns(profile, version), *extra, *digests.regexes]:
        for item in items:
            if item.kind == "name":
                continue
            positions = [m.start() for m in regex.finditer(item.data)]
            if not positions:
                continue
            if item.binary:
                result.advisory.append(f"advisory  {label}  {shown(item.path)} (binary)")
            else:
                found = ",".join(map(str, line_numbers(item.data, positions)))
                result.advisory.append(f"advisory  {label}  {shown(item.path)}:{found}")
    return result


def tree_entries(repo: Path, treeish: str) -> dict[str, tuple[str, str]]:
    """A commit's or tree's blobs: path -> (mode, blob sha)."""
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", treeish).stdout
    entries = {}
    for record in out.split("\0"):
        if record:
            meta, _, path = record.partition("\t")
            mode, kind, sha = meta.split()
            if kind == "blob":
                entries[path] = (mode, sha)
    return entries


def read_blobs(repo: Path, shas: list[str]) -> dict[str, bytes]:
    """The contents of blobs, read in one `cat-file --batch`."""
    unique = sorted(set(shas))
    out = git_bytes(repo, "cat-file", "--batch", stdin="".join(f"{s}\n" for s in unique).encode())
    blobs, pos = {}, 0
    for sha in unique:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        if len(header) != 3 or header[0].decode() != sha:
            raise Refusal(f"cannot read blob {sha[:12]}")
        size = int(header[2])
        blobs[sha] = out[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
    return blobs


def computed_entries(
    repo: Path, profile: Profile, release: Release, overlay_commit: str | None, strict: bool
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """A version's export tree computed from the profile alone: entries plus notes.

    `strict` (the gate's shape step) refuses an absent overlay path, as `build` does; the
    measuring `scan` reports it and keeps the source's blob.
    """
    rows = [r for r in profile.exclude if applies(r.versions, release.version)]
    entries = {
        p: e
        for p, e in tree_entries(repo, release.source).items()
        if not any(excluded(p, r) for r in rows)
    }
    notes = []
    if release.overlay and overlay_commit is None:
        notes.append("overlay not applied: its commit is null")
    elif release.overlay and overlay_commit:
        over = tree_entries(repo, overlay_commit)
        for path in release.overlay.paths:
            if path in over:
                entries[path] = over[path]
            elif strict:
                raise Refusal(f"{release.version}: overlay path {path} is absent")
            else:
                notes.append(f"overlay path {path} is absent at {overlay_commit[:12]}; skipped")
    return entries, notes


def tree_items(repo: Path, entries: dict[str, tuple[str, str]]) -> list[ScanItem]:
    """Scan items for every blob and every path name of a tree."""
    blobs = read_blobs(repo, [sha for _, sha in entries.values()])
    items = [ScanItem(p, blobs[sha], "blob") for p, (_, sha) in sorted(entries.items())]
    items += [ScanItem(p, p.encode("utf-8", "surrogateescape"), "name") for p in sorted(entries)]
    return items


def scan_command(
    repo: Path, profile_path: Path, version: str, overlay: str | None, digests_arg: str | None
) -> int:
    """`scan --version <v>`: scan the would-be export tree without writing anything."""
    profile = load_profile(profile_path)
    _, release = profile.release(version)
    digests = load_digests(digests_arg)
    require_labels(digests, profile)
    commit = None
    if overlay is not None:
        commit = rev(repo, f"{overlay}^{{commit}}")
        if commit is None:
            raise Refusal(f"--overlay-commit {overlay!r} is not a commit")
    elif release.overlay:
        commit = release.overlay.commit
    if rev(repo, f"{release.source}^{{commit}}") != release.source:
        raise Refusal(f"{version}: source {release.source[:12]} is not a commit")
    entries, notes = computed_entries(repo, profile, release, commit, strict=False)
    result = run_scan(tree_items(repo, entries), profile, version, digests)
    emit(f"scan {version}: source {release.source}, overlay {commit or 'none'}")
    for note in notes:
        emit(f"note: {note}")
    emit(result.render(), end="")
    return EXIT_FAIL if result.failures else EXIT_OK


def tree_digest_path(arg: str | None) -> str | None:
    """`scan --tree`'s digest file: --digests, $HACF_EXPORT_DIGESTS, then the per-user file.

    The first of these that names a readable file wins; None when none does. The path is never
    printed.
    """
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "/nonexistent"), ".config"
    )
    for source in (arg, os.environ.get("HACF_EXPORT_DIGESTS"), f"{config}/hacf/export-digests"):
        if source and os.path.isfile(source) and os.access(source, os.R_OK):
            return source
    return None


def worktree_items(repo: Path) -> list[ScanItem]:
    """Scan items for the work tree: tracked and untracked, not ignored, files that exist.

    A symlink is scanned by its target text, as a committed symlink's blob reads; a deleted
    tracked file, and anything that is neither a regular file nor a symlink, is skipped.
    """
    out = git(repo, "ls-files", "-z", "-co", "--exclude-standard").stdout
    data: dict[str, bytes] = {}
    for path in sorted({p for p in out.split("\0") if p}):
        full = repo / path
        if full.is_symlink():
            data[path] = os.fsencode(os.readlink(full))
        elif full.is_file():
            data[path] = full.read_bytes()
    items = [ScanItem(p, blob, "blob") for p, blob in data.items()]
    items += [ScanItem(p, p.encode("utf-8", "surrogateescape"), "name") for p in data]
    return items


def scan_tree_command(
    repo: Path, profile_path: Path, tree: str, digests_arg: str | None, overlay: str | None
) -> int:
    """`scan --tree worktree|head`: the gate's rules on the work tree or HEAD (R31, R37).

    Paths an exclude row of the profile's latest release row covers are dropped; no overlay.
    Without a readable digest file the answer is unknown (exit 3), never a verdict.
    """
    if overlay is not None:
        raise Refusal("--overlay-commit needs --version")
    profile = load_profile(profile_path)
    if not profile.releases:
        raise Refusal("the profile has no release row")
    latest = max(profile.releases, key=lambda r: vkey(r.version)).version
    source = tree_digest_path(digests_arg)
    if source is None:
        emit(
            f"scan {tree}: unknown (no digest file: pass --digests, set HACF_EXPORT_DIGESTS "
            "or place the per-user file)"
        )
        return EXIT_UNKNOWN
    digests = load_digests(source)
    require_labels(digests, profile)
    if tree == "head":
        items = tree_items(repo, tree_entries(repo, "HEAD"))
    else:
        items = worktree_items(repo)
    rows = [r for r in profile.exclude if applies(r.versions, latest)]
    items = [i for i in items if not any(excluded(i.path, r) for r in rows)]
    result = run_scan(items, profile, latest, digests)
    files = sum(1 for i in items if i.kind == "blob")
    emit(f"scan {tree}: {files} files, latest row {latest}")
    emit(result.render(), end="")
    return EXIT_FAIL if result.failures else EXIT_OK


# -------------------------------------------------------------------------------------- gate


class Runner:
    """Runs the gate's heavy commands (steps 6-10) and checks the host; tests replace it."""

    def run(self, argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
        """Run `argv` in `cwd`; return its exit code and combined output."""
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        return proc.returncode, proc.stdout

    def hold_host(self) -> host_gate.Lease:
        """Take the host heavy-job lock for the gate (OPR.99.0.6.2).

        The memory floor, then one HACF heavy job at a time; host_gate.HostBusy when the bounded
        wait runs out.
        """
        return host_gate.acquire("release_export")

    def isolated_daemons(self, runtime_dir: Path) -> list[int]:
        """The codebase-memory-mcp processes started with `CBM_RUNTIME_DIR=<runtime_dir>`.

        CBM 0.12.1 meets its daemon at `$CBM_RUNTIME_DIR/cbm-daemon-<uid>` (default
        `/tmp/cbm-daemon-<uid>`; HOME, XDG_RUNTIME_DIR and TMPDIR do not move it), and the daemon is
        re-parented away from the run, so it is found by its own environment, never by name alone.
        The account daemon has no such variable and can never match (mission R24).
        """
        want = f"CBM_RUNTIME_DIR={runtime_dir}".encode()
        pids = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                argv0 = (proc / "cmdline").read_bytes().split(b"\0", 1)[0]
                environ = (proc / "environ").read_bytes().split(b"\0")
            except OSError:
                continue
            if os.path.basename(argv0) == b"codebase-memory-mcp" and want in environ:
                pids.append(int(proc.name))
        return sorted(pids)

    def stop_isolated_daemons(self, runtime_dir: Path) -> list[str]:
        """SIGTERM (then, after 10 s, SIGKILL) only the daemons of `runtime_dir`; log lines."""
        lines = []
        for pid in self.isolated_daemons(runtime_dir):
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while pid in self.isolated_daemons(runtime_dir) and time.monotonic() < deadline:
                time.sleep(0.2)
            if pid in self.isolated_daemons(runtime_dir):
                os.kill(pid, signal.SIGKILL)
                lines.append(f"stopped the step's own daemon pid {pid} (SIGKILL after 10 s)")
            else:
                lines.append(f"stopped the step's own daemon pid {pid} (SIGTERM)")
        return lines or ["no daemon of the step's own runtime dir was left running"]


@dataclass
class StepOutcome:
    """One gate step's verdict, note, exit code and log."""

    number: int
    name: str
    verdict: str = "n/a"
    note: str = ""
    exit_code: str = "-"
    seconds: float = 0.0
    log: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)  # console lines under the step's line


@dataclass
class GateRun:
    """The state shared by the gate's steps."""

    repo: Path
    profile: Profile
    index: int
    release: Release
    export: str
    tag: str
    tree: str
    report_dir: Path
    work: Path
    runner: Runner
    py310: str
    digests: DigestFile
    exports: dict[str, str] = field(default_factory=dict)  # version -> export commit, <= this one
    clone: Path | None = None
    reference: Path | None = None
    deletions: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)
    overlay_commit: str | None = None
    lease: host_gate.Lease | None = None  # the host lock, held from the first heavy step on

    def heavy_env(self, step_dir: Path, **extra: str) -> dict[str, str]:
        """The environment of a heavy command: the caller's minus every GIT_*, temp in the step."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        tmp = step_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        env.update({"TMPDIR": str(tmp), **extra})
        return env

    def cache_env(self, step_dir: Path) -> dict[str, str]:
        """uv, codebase-memory-mcp and XDG caches under the step dir (mission R11)."""
        caches = {name: str(step_dir / "cache" / name.lower()) for name in CACHE_VARS}
        for path in caches.values():
            Path(path).mkdir(parents=True, exist_ok=True)
        return caches


def parse_never_copy(text: str) -> list[str] | None:
    """The NEVER_COPY entries of an installer script, or None when there is no such array."""
    match = re.search(r"^NEVER_COPY=\((.*?)^\)", text, re.S | re.M)
    if not match:
        return None
    body = "\n".join(line.split("#", 1)[0] for line in match[1].splitlines())
    return re.findall(r'"([^"]*)"', body)


def never_copy_covers(path: str, entries: list[str]) -> bool:
    """Whether a NEVER_COPY entry covers an exclude row's path (a dir entry covers below it)."""
    bare = path.rstrip("/")
    for entry in entries:
        if entry.endswith("/"):
            if bare == entry[:-1] or bare.startswith(entry):
                return True
        elif fnmatch.fnmatchcase(bare, entry):
            return True
    return False


def step_scan(run: GateRun, out: StepOutcome) -> None:
    """Step 1: every blob, path name and the raw commit and tag objects (R9)."""
    entries = tree_entries(run.repo, run.export)
    items = tree_items(run.repo, entries)
    items.append(
        ScanItem(COMMIT_OBJECT, git_bytes(run.repo, "cat-file", "-p", run.export), "object")
    )
    items.append(ScanItem(TAG_OBJECT, git_bytes(run.repo, "cat-file", "-p", run.tag), "object"))
    result = run_scan(items, run.profile, run.release.version, run.digests)
    run.advisory = result.advisory
    out.log.append(result.render())
    out.verdict = "FAIL" if result.failures else "PASS"
    out.note = f"{result.failures} strict failure(s), {len(result.advisory)} advisory line(s)"


def step_shape(run: GateRun, out: StepOutcome) -> None:
    """Step 2: parent, recomputed tree, no spec/, NEVER_COPY cover, top_level, deletions."""
    problems: list[str] = []
    version = run.release.version
    if run.index == 0:
        parent = run.profile.base
    else:
        previous = run.profile.releases[run.index - 1].version
        parent = rev(run.repo, f"refs/tags/v{previous}^{{commit}}") or f"(no v{previous} export)"
    header = git(run.repo, "cat-file", "-p", run.export).stdout.split("\n\n", 1)[0]
    parents = [line[7:] for line in header.splitlines() if line.startswith("parent ")]
    if parents != [parent]:
        problems.append(f"parents {parents} are not [{parent}]")
    out.log.append(f"parents: {parents}; expected [{parent}]")

    actual = tree_entries(run.repo, run.export)
    try:
        expected, _ = computed_entries(
            run.repo, run.profile, run.release, run.overlay_commit, strict=True
        )
    except Refusal as exc:
        expected = {}
        problems.append(f"cannot recompute the tree: {exc}")
    if expected and actual != expected:
        differ = sorted(set(actual) ^ set(expected)) + sorted(
            p for p in set(actual) & set(expected) if actual[p] != expected[p]
        )
        problems.append(f"the tree differs from the profile's at {len(differ)} path(s)")
        out.log += [f"  differs: {p}" for p in differ]
    out.log.append(
        f"tree recomputed from the profile: {'equal' if actual == expected else 'DIFFERS'}"
    )

    spec = [p for p in actual if p.startswith("spec/")]
    if spec:
        problems.append(f"{len(spec)} spec/ path(s), e.g. {spec[0]}")

    installer = git(
        run.repo, "cat-file", "blob", f"{run.release.source}:HACF-install.sh", check=False
    )
    entries = parse_never_copy(installer.stdout) if installer.returncode == 0 else None
    source_paths = list(tree_entries(run.repo, run.release.source))
    for row in run.profile.exclude:
        if not applies(row.versions, version):
            continue
        if not any(excluded(p, row) for p in source_paths):
            out.log.append(f"exclude {row.path}: n/a at {version}")
        elif entries is None:
            problems.append("the source's HACF-install.sh has no NEVER_COPY array")
            break
        elif never_copy_covers(row.path, entries):
            out.log.append(f"exclude {row.path}: covered by NEVER_COPY")
        else:
            problems.append(f"exclude {row.path} is not covered by the source's NEVER_COPY")

    names = sorted({p.split("/", 1)[0] for p in actual})
    for name in names:
        if name not in run.profile.top_level:
            problems.append(f"top-level name {name!r} is not in top_level (mission R17)")

    previous_tree = parent if SHA_RE.match(parent) else run.profile.base
    run.deletions = sorted(set(tree_entries(run.repo, previous_tree)) - set(actual))
    out.log.append(f"public deletions against {previous_tree[:12]}: {len(run.deletions)}")
    out.log += [f"  deleted: {p}" for p in run.deletions]
    out.log += [f"FAIL {p}" for p in problems]
    out.verdict = "FAIL" if problems else "PASS"
    out.note = "; ".join(problems) or f"{len(run.deletions)} public deletion(s)"


MD_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)\n]*)?\)")
MD_REF_RE = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*<?([^\s>]+)>?", re.M)
MD_FENCE_RE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", re.M | re.S)
MD_CODE_RE = re.compile(r"`[^`\n]*`")


def markdown_links(text: str) -> list[tuple[int, str]]:
    """The (line, target) of every Markdown link outside code fences and code spans."""
    blank = MD_FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), text)
    blank = MD_CODE_RE.sub(lambda m: " " * len(m[0]), blank)
    found = []
    for regex in (MD_LINK_RE, MD_REF_RE):
        for match in regex.finditer(blank):
            found.append((blank.count("\n", 0, match.start()) + 1, match[1]))
    return sorted(found)


def step_links(run: GateRun, out: StepOutcome) -> None:
    """Step 3: relative links in exported Markdown; a link into an excluded path fails."""
    entries = tree_entries(run.repo, run.export)
    paths = set(entries)
    dirs = {posixpath.dirname(p) for p in paths}
    for p in list(dirs):
        while p:
            p = posixpath.dirname(p)
            dirs.add(p)
    rows = [r for r in run.profile.exclude if applies(r.versions, run.release.version)]
    markdown = sorted(p for p in paths if p.lower().endswith(".md"))
    blobs = read_blobs(run.repo, [entries[p][1] for p in markdown])
    failing = unresolved = 0
    for md in markdown:
        text = blobs[entries[md][1]].decode("utf-8", "replace")
        for line, target in markdown_links(text):
            if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith(("#", "//")):
                continue
            clean = urllib.parse.unquote(re.split(r"[#?]", target, maxsplit=1)[0])
            if not clean:
                continue
            base = "" if clean.startswith("/") else posixpath.dirname(md)
            resolved = posixpath.normpath(posixpath.join(base, clean.lstrip("/")))
            hit = [r for r in rows if resolved == r.bare or resolved.startswith(r.bare + "/")]
            if hit:
                failing += 1
                out.log.append(f"FAIL {md}:{line} -> {target} (inside excluded {hit[0].path})")
            elif resolved != "." and resolved not in paths and resolved not in dirs:
                unresolved += 1
                out.log.append(f"unresolved {md}:{line} -> {target} (reported, not failing)")
    out.verdict = "FAIL" if failing else "PASS"
    out.note = f"{failing} link(s) into excluded paths, {unresolved} other unresolved"


def step_messages(run: GateRun, out: StepOutcome) -> None:
    """Step 4: the raw commit and tag objects, for the lead to read (always PASS; R13)."""
    out.log.append(git(run.repo, "cat-file", "-p", run.export).stdout)
    out.log.append(git(run.repo, "cat-file", "-p", run.tag).stdout)
    out.verdict, out.note = "PASS", "the raw objects are in REPORT.md"


def step_clone(run: GateRun, out: StepOutcome) -> None:
    """Step 5: a public-only clone: only the export and its tags reach it (R10)."""
    step = run.work / "05-clone"
    bare, clone = step / "public.git", step / "clone"
    step.mkdir(parents=True)
    git(step, "init", "-q", "--bare", str(bare))
    refspecs = [f"refs/tags/v{v}:refs/tags/v{v}" for v in run.exports]
    git(bare, "fetch", "-q", "--no-tags", "--end-of-options", str(run.repo), *refspecs)
    git(bare, "update-ref", "refs/heads/main", run.export, ZERO_SHA)
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    git(step, "clone", "-q", "--no-hardlinks", "--end-of-options", str(bare), str(clone))
    problems = []
    if rev(clone, "HEAD") != run.export or rev(clone, "refs/heads/main") != run.export:
        problems.append("the clone's main/HEAD is not the export")
    for version in run.exports:
        if rev(clone, f"refs/tags/v{version}") != rev(run.repo, f"refs/tags/v{version}"):
            problems.append(f"the clone's v{version} differs from the private tag")
    allowed = set(git(run.repo, "rev-list", run.profile.base).stdout.split())
    allowed |= set(run.exports.values())
    reachable = git(clone, "rev-list", "--all").stdout.split()
    private = sorted(set(reachable) - allowed)
    if private:
        problems.append(f"{len(private)} commit(s) outside the base history and the exports")
        out.log += [f"  private: {sha}" for sha in private]
    out.log.append(f"clone {clone}: {len(reachable)} commit(s), main at {run.export}")
    run.clone = clone if not problems else None
    out.verdict = "FAIL" if problems else "PASS"
    out.note = "; ".join(problems) or f"{len(reachable)} commits, all base history or exports"


SKIP_RE = re.compile(r"^SKIPPED \[\d+\] (.+?):(\d+): (.*)$", re.M)


def classify_skips(output: str, profile: Profile, version: str) -> tuple[list[str], int]:
    """Each `-rs` SKIPPED line with its match; and how many are unexpected.

    pytest's `-rs` summary names a skip by `<file>:<line>`, so an `expected_skips` row matches
    when its nodeid's file is that file and its `reason_contains` is in the reason. A skip whose
    reason names an `HACF_LIVE_` variable is an opt-in live test.
    """
    rows = [r for r in profile.expected_skips if applies(r["versions"], version)]
    lines, unexpected = [], 0
    for match in SKIP_RE.finditer(output):
        path, reason = match[1], match[3]
        row = next(
            (
                r
                for r in rows
                if r["nodeid"].split("::", 1)[0] == path and r["reason_contains"] in reason
            ),
            None,
        )
        if row is not None:
            lines.append(f"expected  {match[0]}  (row {row['nodeid']})")
        elif "HACF_LIVE_" in reason:
            lines.append(f"live      {match[0]}")
        else:
            unexpected += 1
            lines.append(f"UNEXPECTED {match[0]}")
    return lines, unexpected


def run_logged(run: GateRun, out: StepOutcome, argv: list[str], cwd: Path, env: dict[str, str]):
    """Run one heavy command through the runner and log it; return (exit code, output)."""
    start = time.monotonic()
    code, output = run.runner.run(argv, cwd, env)
    out.log.append(
        f"$ (cd {cwd} && {' '.join(argv)})\nexit {code}, {time.monotonic() - start:.1f}s"
    )
    out.log.append(output)
    out.exit_code = str(code) if out.exit_code in ("-", "0") else out.exit_code
    return code, output


def hold_host(run: GateRun, out: StepOutcome) -> bool:
    """Hold the host lock from the first heavy step to the end of the run (one lease, F15).

    The first heavy step takes it (memory, the lock, memory again, a bounded wait); later steps
    run inside the same lease, and their commands inherit its token (a nested check.sh does not
    wait on its own gate). False, a `host busy` FAIL, when the wait runs out.
    """
    if run.lease is not None:
        return True
    try:
        run.lease = run.runner.hold_host()
    except host_gate.HostBusy as exc:
        out.verdict = "FAIL"
        out.note = f"host busy ({exc.reason}); a setup verdict, not a product one"
        return False
    os.environ.update(run.lease.env())
    return True


def step_check(run: GateRun, out: StepOutcome) -> None:
    """Step 6: check.sh in the clone, both legs, every skip accounted for (R10)."""
    if not hold_host(run, out):
        return
    step = run.work / "06-check"
    # Verbose: the full output still reaches logs/06-check.sh.log and classify_skips (an older
    # export's check.sh ignores both variables). -rsfE adds the FAILED/ERROR lines to the skips.
    env = run.heavy_env(
        step,
        HACF_PY310=run.py310,
        HACF_TEST_WORKERS="4",
        PYTEST_ADDOPTS="-rsfE",
        HACF_CHECK_VERBOSE="1",
        HACF_LOG_DIR=str(step / "check-logs"),
    )
    code, output = run_logged(run, out, ["bash", "dev/scripts/check.sh"], run.clone, env)
    run.skips, unexpected = classify_skips(output, run.profile, run.release.version)
    out.log += ["skips:", *run.skips]
    # From the captured output, not from files: the gate runs the export's own check.sh.
    out.summary = [*summarize_output(output), f"log: logs/{out.number:02d}-{out.name}.log"]
    out.log += ["summary:", *out.summary]
    out.verdict = "PASS" if code == 0 and not unexpected else "FAIL"
    out.note = f"exit {code}; {len(run.skips)} skip line(s), {unexpected} unexpected"


def step_ruff(run: GateRun, out: StepOutcome) -> None:
    """Step 7: a non-vacuous ruff over every tracked .py of the clone (R10, F2)."""
    # Inside the lease step 6 took: a busy host stops the run before this step.
    # Every tracked file, filtered here: git() passes --literal-pathspecs, so no '*.py' glob.
    names = git(run.clone, "ls-files", "-z").stdout
    files = sorted(p for p in names.split("\0") if p.endswith(".py"))
    if not files:
        out.verdict = "FAIL"
        out.note = "vacuous: 0 tracked .py files"
        return
    env = run.heavy_env(run.work / "07-ruff")
    codes = []
    for argv in (["ruff", "check"], ["ruff", "format", "--check"]):
        code, _ = run_logged(
            run,
            out,
            ["uv", "run", "--locked", *argv, "--force-exclude", "--", *files],
            run.clone,
            env,
        )
        codes.append(code)
    out.verdict = "PASS" if all(c == 0 for c in codes) else "FAIL"
    out.note = f"{len(files)} tracked .py file(s)"


def reference_source(run: GateRun) -> Path:
    """A throwaway clone of the private repository with the source's tree in place (R11)."""
    if run.reference is None:
        path = run.work / "reference" / "hacf"
        path.parent.mkdir(parents=True, exist_ok=True)
        git(
            path.parent,
            "clone",
            "-q",
            "--no-hardlinks",
            "--no-checkout",
            "--end-of-options",
            str(run.repo),
            str(path),
        )
        git(path, "read-tree", "-u", "--reset", run.release.source)
        git(path, "update-ref", "--no-deref", "HEAD", run.release.source)
        run.reference = path
    return run.reference


def normalise_install(project: Path, placeholders: dict[str, str]) -> dict[str, tuple[str, bytes]]:
    """An installed tree minus `.git/`, with the record fields that may differ normalised.

    Returns `relative path -> (kind, bytes)`; `kind` is `file`, `exec` or `link`.
    """
    tree: dict[str, tuple[str, bytes]] = {}
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project).as_posix()
        if rel == ".git" or rel.startswith(".git/") or (path.is_dir() and not path.is_symlink()):
            continue
        if rel.startswith(CODEGRAPH_STATE):  # R66: per-machine; compare_codegraph compares it
            continue
        key = BACKUP_STAMP_RE.sub(r".agents/.backup/<stamp>\1", rel)
        if path.is_symlink():
            data, kind = os.readlink(path).encode(), "link"
        else:
            data = path.read_bytes()
            kind = "exec" if os.access(path, os.X_OK) else "file"
        if rel in (MANIFEST_REL, SOURCE_RECORD_REL):
            try:
                doc = json.loads(data)
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                volatile = MANIFEST_VOLATILE if rel == MANIFEST_REL else SOURCE_RECORD_VOLATILE
                doc = {k: v for k, v in doc.items() if k not in volatile}
                if rel == MANIFEST_REL and isinstance(doc.get("source"), dict):
                    doc["source"] = {
                        k: v for k, v in doc["source"].items() if k not in SOURCE_RECORD_VOLATILE
                    }
                data = json.dumps(doc, sort_keys=True).encode()
        for real, name in placeholders.items():
            data = data.replace(real.encode(), name.encode())
        tree[key] = (kind, data)
    return tree


def compare_installs(
    a: Path,
    b: Path,
    sources: dict[str, str],
    cbm_cache: Path | None = None,
    scratch: Path | None = None,
    log: list[str] | None = None,
) -> list[str]:
    """The differences between two installed projects, after normalisation.

    `.agents/state/codegraph/` leaves the byte comparison and is compared by meaning instead
    (R66, compare_codegraph); with `cbm_cache` (step 9) the two CBM stores are compared too, on
    copies under `scratch`, and the similarity counts go to `log`.
    """
    left = normalise_install(a, {str(a): "<project>", **sources})
    right = normalise_install(b, {str(b): "<project>", **sources})
    diff = [f"only in the export install: {p}" for p in sorted(set(left) - set(right))]
    diff += [f"only in the source install: {p}" for p in sorted(set(right) - set(left))]
    diff += [f"differs: {p}" for p in sorted(set(left) & set(right)) if left[p] != right[p]]
    diff += compare_codegraph(a, b, cbm_cache, scratch, log if log is not None else [])
    return diff


CODEGRAPH_STATE = ".agents/state/codegraph/"  # per-machine and gitignored in an install (R66)
CBM_STORE_VERSION = "0.12.1"  # the pinned backend; MR 4 reads this version's private store schema
CBM_STORE_TABLES = ("projects", "file_hashes", "nodes", "edges")
CBM_SIMILARITY_EDGE = "SEMANTICALLY_RELATED"  # path-derived (R66): logged, never a difference
INDEX_COUNTS = (
    "nodes",
    "not_indexed_files_count",
    "skipped_count",
    "parse_partial_count",
    "parse_unusable_count",
)
CBM_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _json_object(path: Path) -> dict:
    """The JSON object in `path`, or {} when it is missing, unreadable or not an object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def compare_codegraph(
    a: Path, b: Path, cbm_cache: Path | None, scratch: Path | None, log: list[str]
) -> list[str]:
    """The code-graph state of two installs, compared by meaning (R66, MR 3-4).

    Each failed assertion is one difference. Neither install having the state is equal (step 8).
    """
    sides = (("export", a / CODEGRAPH_STATE), ("source", b / CODEGRAPH_STATE))
    present = [name for name, state in sides if state.is_dir()]
    if not present:
        return []
    if len(present) == 1:
        return [f"only in the {present[0]} install: {CODEGRAPH_STATE}"]
    diff: list[str] = []
    indexes = {}
    names = {}
    for name, state in sides:
        project = _json_object(state / "project.json")
        index = _json_object(state / "raw" / "index.json")
        indexes[name] = index
        names[name] = project.get("project_name")
        if project.get("version") != CBM_STORE_VERSION:
            diff.append(
                f"code-graph state: the {name} project.json version {project.get('version')!r} "
                f"is not the pinned {CBM_STORE_VERSION}"
            )
        last = project.get("last_index")
        if not (isinstance(last, dict) and last.get("at")):
            diff.append(f"code-graph state: the {name} project.json has no last_index.at")
        if index.get("status") != "indexed":
            diff.append(
                f"code-graph state: the {name} raw/index.json status is "
                f"{index.get('status')!r}, not 'indexed'"
            )
    for key in INDEX_COUNTS:
        left, right = indexes["export"].get(key), indexes["source"].get(key)
        if left != right:
            diff.append(f"code-graph index differs: {key} {left!r} (export) != {right!r} (source)")
    if cbm_cache is not None:
        if scratch is None:
            raise ValueError("comparing the CBM stores needs a scratch dir for their copies")
        diff += compare_cbm_stores(names, cbm_cache, scratch, log)
    return diff


class CBMStoreError(Exception):
    """A CBM store that cannot be copied, read or does not have the expected tables."""


def read_cbm_store(cbm_cache: Path, name: object, into: Path) -> dict[str, Any]:
    """One install's CBM store, read from a copy under `into`; project names become `<P>`.

    Returns the `(rel_path, sha256)` set, the node and structural-edge multisets and the
    `SEMANTICALLY_RELATED` edge multiset. The store in `cbm_cache` is copied, never opened.
    """
    import sqlite3
    from collections import Counter

    if not (isinstance(name, str) and CBM_PROJECT_RE.fullmatch(name)):
        raise CBMStoreError(f"no usable project_name in project.json: {name!r}")
    store = cbm_cache / f"{name}.db"
    if not store.is_file():
        raise CBMStoreError(f"no store {store.name} in the CBM cache dir")
    into.mkdir(parents=True, exist_ok=True)
    copy = into / store.name
    try:
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{store}{suffix}")
            if source.is_file():
                shutil.copyfile(source, Path(f"{copy}{suffix}"))
        con = sqlite3.connect(copy)
        try:
            listed = con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            tables = {row[0] for row in listed}
            missing = [t for t in CBM_STORE_TABLES if t not in tables]
            if missing:
                raise CBMStoreError(f"{store.name} lacks table(s) {', '.join(missing)}")

            def qn(value: str) -> str:
                return value.replace(name, "<P>")

            hashes = set(
                con.execute("SELECT rel_path, sha256 FROM file_hashes WHERE project = ?", (name,))
            )
            nodes = Counter(
                (label, qn(q), path, start, end)
                for label, q, path, start, end in con.execute(
                    "SELECT label, qualified_name, file_path, start_line, end_line "
                    "FROM nodes WHERE project = ?",
                    (name,),
                )
            )
            structural: Counter = Counter()
            similar: Counter = Counter()
            rows = con.execute(
                "SELECT s.qualified_name, s.file_path, t.qualified_name, t.file_path, e.type "
                "FROM edges e JOIN nodes s ON s.id = e.source_id "
                "JOIN nodes t ON t.id = e.target_id WHERE e.project = ?",
                (name,),
            )
            for sq, sf, tq, tf, kind in rows:
                edge = (qn(sq), sf, qn(tq), tf, kind)
                (similar if kind == CBM_SIMILARITY_EDGE else structural)[edge] += 1
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        raise CBMStoreError(f"{store.name}: {exc}") from exc
    return {"hashes": hashes, "nodes": nodes, "edges": structural, "similar": similar}


def compare_cbm_stores(
    names: dict[str, object], cbm_cache: Path, scratch: Path, log: list[str]
) -> list[str]:
    """MR 4: equal file hashes, nodes and structural edges in the two installs' CBM stores.

    `SEMANTICALLY_RELATED` edges depend on the project path (R66): their counts are logged and
    never a difference. A store that cannot be read fails closed.
    """
    stores = {}
    for side in ("export", "source"):
        try:
            stores[side] = read_cbm_store(cbm_cache, names[side], scratch / "cbm-stores" / side)
        except CBMStoreError as exc:
            return [f"cannot verify the code-graph index: the {side} store: {exc}"]
    ex, src = stores["export"], stores["source"]
    for side, store in stores.items():
        log.append(
            f"code-graph store ({side}): {len(store['hashes'])} file hashes, "
            f"{sum(store['nodes'].values())} nodes, {sum(store['edges'].values())} structural "
            f"edges, {sum(store['similar'].values())} {CBM_SIMILARITY_EDGE}"
        )
    only_ex = sum((ex["similar"] - src["similar"]).values())
    only_src = sum((src["similar"] - ex["similar"]).values())
    log.append(
        f"{CBM_SIMILARITY_EDGE} edges: {only_ex} only in the export, {only_src} only in the "
        "source (path-derived; logged, never a difference)"
    )
    diff = []
    if ex["hashes"] != src["hashes"]:
        diff.append(
            "code-graph index differs: file hashes "
            f"({len(ex['hashes'] - src['hashes'])} only in the export, "
            f"{len(src['hashes'] - ex['hashes'])} only in the source)"
        )
    for key, what in (("nodes", "nodes"), ("edges", "structural edges")):
        if ex[key] != src[key]:
            diff.append(
                f"code-graph index differs: {what} "
                f"({sum((ex[key] - src[key]).values())} only in the export, "
                f"{sum((src[key] - ex[key]).values())} only in the source)"
            )
    return diff


def install_pair(
    run: GateRun,
    out: StepOutcome,
    step: Path,
    flags: list[str],
    env: dict[str, str],
    cbm_cache: Path | None = None,
):
    """Install from the clone (A) and from the private source (B); return (A, B, ok).

    Step 9 passes its `cbm_cache`, so the two CBM stores are compared too (R66, MR 4).
    """
    reference = reference_source(run)
    # One basename for both: the installer writes it into the scaffolded SPEC.md heading
    # (`# Project - <basename>`), so differently named dirs would differ on that line alone.
    a, b = step / "export" / "project", step / "source" / "project"
    codes = []
    for project, source, ref in ((a, run.clone, run.export), (b, reference, run.release.source)):
        project.mkdir(parents=True)
        git(project, "init", "-q")
        argv = ["bash", str(source / "HACF-install.sh"), f"--source={source}", f"--ref={ref}"]
        code, _ = run_logged(run, out, [*argv, "-y", "--ides=all", *flags], project, env)
        codes.append(code)
    sources = {str(run.clone): "<hacf>", str(reference): "<hacf>"}
    store_log: list[str] = []
    diff = compare_installs(a, b, sources, cbm_cache, step, store_log)
    out.log += ["install comparison:", *(diff or ["equal except the named record fields"])]
    out.log += store_log
    out.log.append(
        "named record fields: "
        f"{MANIFEST_REL} installed_commit, installed_at, source.commit; "
        f"{SOURCE_RECORD_REL} path, ref, commit, written_at; .agents/.backup/<stamp>/; "
        f"{CODEGRAPH_STATE}** (per-machine, gitignored; compared by MR 3-4 instead)"
    )
    problems = [f"install exit {c}" for c in codes if c != 0]
    if diff:
        problems.append(f"{len(diff)} difference(s) between the installs")
    return a, problems


def step_install_plain(run: GateRun, out: StepOutcome) -> None:
    """Step 8: a plain install from the clone equals one from the private source (R11)."""
    if not hold_host(run, out):
        return
    step = run.work / "08-install-plain"
    _, problems = install_pair(run, out, step, [], run.heavy_env(step))
    out.verdict = "FAIL" if problems else "PASS"
    out.note = "; ".join(problems) or "both installs exit 0 and are equal"


def step_install_codegraph(run: GateRun, out: StepOutcome) -> None:
    """Step 9: the code-graph install, its MCP entries and `doctor` (R11)."""
    if not hold_host(run, out):
        return
    step = run.work / "09-install-codegraph"
    caches = run.cache_env(step)
    env = run.heavy_env(step, **caches)
    flags = [f"--code-graph={CODE_GRAPH_SERVER}", "--install-code-graph"]
    project, problems = install_pair(run, out, step, flags, env, Path(caches["CBM_CACHE_DIR"]))
    for rel in MCP_FILES:
        try:
            servers = json.loads((project / rel).read_text(encoding="utf-8")).get("mcpServers", {})
        except (OSError, ValueError, AttributeError):
            servers = {}
        if CODE_GRAPH_SERVER not in servers:
            problems.append(f"{rel} has no {CODE_GRAPH_SERVER} entry")
    env = {**env, "PATH": f"{caches['UV_TOOL_BIN_DIR']}{os.pathsep}{env.get('PATH', '')}"}
    argv = ["python3", ".agents/scripts/hyper_codegraph.py", "doctor", "--json"]
    code, output = run_logged(run, out, argv, project, env)
    try:
        report = json.loads(output[output.index("{") :])
    except ValueError:
        report = {}
    healthy = (
        code == 0
        and report.get("backend") == CODE_GRAPH_SERVER
        and report.get("prefix")
        and report.get("backend_version") == report.get("pinned_version")
    )
    if not healthy:
        problems.append(f"doctor not ok (exit {code})")
    out.log.append(
        "doctor counts as status ok when it exits 0 with backend codebase-memory-mcp, a resolved "
        "prefix, and backend_version equal to pinned_version"
    )
    out.verdict = "FAIL" if problems else "PASS"
    out.note = "; ".join(problems) or (
        "installs equal, code-graph indexes equal (structure), three MCP entries, doctor ok"
    )


CBM_RUNTIME_BASE = "/tmp"  # lead ruling P11: short, because of the AF_UNIX path limit
CBM_SOCKET_MAX = 100  # bytes; sun_path holds 108 with the NUL, P11 keeps a margin


def cbm_socket_path(runtime: Path) -> str:
    """The longest socket path CBM 0.12.1 makes under `runtime` (cbm-<16 hex>.sock)."""
    return f"{runtime}/cbm-daemon-{os.getuid()}/cbm-{'0' * 16}.sock"


def cbm_socket_problem(runtime: Path) -> str | None:
    """Why CBM could not create its socket under `runtime`, or None (lead ruling P11)."""
    path = cbm_socket_path(runtime)
    if len(path.encode()) >= CBM_SOCKET_MAX:
        return (
            f"CBM socket path {len(path.encode())} bytes >= {CBM_SOCKET_MAX} "
            f"(AF_UNIX limit): {path}"
        )
    return None


def remove_runtime(runtime: Path) -> str:
    """Remove step 10's own CBM runtime dir by its exact path; one log line."""
    if not runtime.name.startswith("hacf-cbm.") or runtime.parent != Path(CBM_RUNTIME_BASE):
        return f"left {runtime} in place: not a gate-made CBM runtime dir"
    shutil.rmtree(runtime, ignore_errors=True)
    return f"removed the step's CBM runtime dir {runtime}"


LIVE_TMP_BASE = Path.home() / ".cache"  # lead ruling P12: short and on disk (/tmp is tmpfs)
LIVE_REPO_TAIL = "pytest-0/test_codegraph_contract0/repo"  # where the live test's repo lands
PROJECT_NAME_MAX = 128  # hyper_codegraph.PROJECT_RE: [A-Za-z0-9][A-Za-z0-9._-]{0,127}


def live_project_name(tmpdir: Path) -> str:
    """The CBM project name of the live test's repo under `tmpdir` (its path, `/` -> `-`)."""
    user = os.environ.get("USER") or str(os.getuid())
    repo = f"{tmpdir}/pytest-of-{user}/{LIVE_REPO_TAIL}"
    return repo.strip("/").replace("/", "-")


def live_project_problem(tmpdir: Path) -> str | None:
    """Why the adapter would refuse the live test's project name, or None (lead ruling P12)."""
    name = live_project_name(tmpdir)
    if len(name) > PROJECT_NAME_MAX:
        return f"CBM project name {len(name)} chars > {PROJECT_NAME_MAX}: {name}"
    return None


def make_live_tmp(base: Path | None = None) -> Path:
    """Step 10's own short on-disk TMPDIR, `mktemp -d <base>/hacf-live.XXXXXX` (mode 700)."""
    base = base or LIVE_TMP_BASE
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="hacf-live.", dir=base))


def remove_live_tmp(tmpdir: Path, base: Path | None = None) -> str:
    """Remove step 10's TMPDIR by its exact path; one log line."""
    if not tmpdir.name.startswith("hacf-live.") or tmpdir.parent != (base or LIVE_TMP_BASE):
        return f"left {tmpdir} in place: not a gate-made live TMPDIR"
    shutil.rmtree(tmpdir, ignore_errors=True)
    return f"removed the step's TMPDIR {tmpdir}"


def live_isolation(step: Path, runtime_base: str = CBM_RUNTIME_BASE) -> tuple[dict[str, str], Path]:
    """Step 10's own HOME, runtime dirs and uv Python dir; return (env, CBM runtime dir) (R24).

    `CBM_RUNTIME_DIR` is what separates the run's daemon from the account one. Its socket path
    must stay under the AF_UNIX limit, so it is a short `mktemp -d` under /tmp (mode 700, lead
    ruling P11) rather than under the step dir; HOME and XDG_RUNTIME_DIR stay in the step dir.
    HOME moves uv's default Python install dir, so the caller's is kept reachable (read only by
    uv) rather than downloaded again.

    The adapter prefers a `codebase-memory-mcp` on PATH to the pinned uvx prefix the contract
    test exercises, so PATH leaves out every dir holding one; `uv` and `uvx` stay reachable
    through links in the step's own `bin/`.
    """
    runtime = step / "run"
    home = step / "home"
    bindir = step / "bin"
    for path in (runtime, home, bindir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    pythons = os.environ.get("UV_PYTHON_INSTALL_DIR") or str(Path(data) / "uv" / "python")
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for tool in ("uv", "uvx"):
        found = shutil.which(tool)
        link = bindir / tool
        if found and not link.exists():
            link.symlink_to(found)
    kept = [d for d in dirs if not (Path(d) / CODE_GRAPH_SERVER).exists()]
    cbm_runtime = Path(tempfile.mkdtemp(prefix="hacf-cbm.", dir=runtime_base))  # mode 700
    env = {
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(runtime),
        "CBM_RUNTIME_DIR": str(cbm_runtime),
        "UV_PYTHON_INSTALL_DIR": pythons,
        "PATH": os.pathsep.join([str(bindir), *kept]),
    }
    return env, cbm_runtime


def step_codegraph_live(run: GateRun, out: StepOutcome) -> None:
    """Step 10: the live code-graph contract, only inside the gate's clone (R11)."""
    if not hold_host(run, out):
        return
    step = run.work / "10-codegraph-live"
    isolated, runtime = live_isolation(step)
    live_tmp = make_live_tmp()
    env = run.heavy_env(step, HACF_LIVE_CODEGRAPH="1", **run.cache_env(step), **isolated)
    env["TMPDIR"] = str(live_tmp)
    out.log.append(f"TMPDIR={live_tmp} (short, on disk: the CBM project name comes from it)")
    out.log.append(
        "isolated from the account code-graph daemon: "
        + ", ".join(f"{k}={isolated[k]}" for k in sorted(isolated) if k != "PATH")
    )
    dropped = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    dropped = [d for d in dropped if d not in isolated["PATH"].split(os.pathsep)]
    out.log.append(f"PATH without the dirs holding {CODE_GRAPH_SERVER}: {dropped or 'none'}")
    problem = cbm_socket_problem(runtime) or live_project_problem(live_tmp)
    if problem:
        out.log += [remove_runtime(runtime), remove_live_tmp(live_tmp)]
        out.verdict, out.note = "FAIL", f"setup: {problem}; the live test was not run"
        return
    # The shipped verdict file records an earlier run; only this run may write the one read.
    shipped = run.clone / LIVE_VERDICT
    if shipped.exists():
        shipped.unlink()
        out.log.append(f"removed the shipped {LIVE_VERDICT} before the run")
    else:
        out.log.append(f"no shipped {LIVE_VERDICT} before the run")
    argv = ["uv", "run", "--locked", "pytest", "-q", LIVE_TEST]
    try:
        code, output = run_logged(run, out, argv, run.clone, env)
    finally:
        out.log += run.runner.stop_isolated_daemons(runtime)
        out.log += [remove_runtime(runtime), remove_live_tmp(live_tmp)]
    try:
        verdict = json.loads(shipped.read_text(encoding="utf-8"))
        contract = verdict.get("contract_verdict")
    except (OSError, ValueError, AttributeError):
        contract = None
    passed = re.search(r"(?<![\w-])1 passed\b", output) is not None
    problems = []
    if code != 0:
        problems.append(f"exit {code}")
    if not passed:
        skipped = re.search(r"\b\d+ skipped\b", output)
        problems.append("the live test was skipped" if skipped else "no `1 passed` in the output")
    elif re.search(r"\b\d+ (failed|skipped|errors?)\b", output):
        problems.append("the output also reports a failure, error or skip")
    if contract != "pass":
        problems.append(f"this run's verdict file says {contract!r}")
    out.verdict = "FAIL" if problems else "PASS"
    out.note = "; ".join(problems) or f"exit {code}; 1 passed; this run's verdict file says 'pass'"


STEPS: list[tuple[str, Callable[[GateRun, StepOutcome], None]]] = [
    ("scan", step_scan),
    ("shape", step_shape),
    ("links", step_links),
    ("messages", step_messages),
    ("clone", step_clone),
    ("check.sh", step_check),
    ("ruff", step_ruff),
    ("install-plain", step_install_plain),
    ("install-codegraph", step_install_codegraph),
    ("codegraph-live", step_codegraph_live),
]


def default_report_root() -> Path:
    """Mission R20: `$HACF_EXPORT_REPORT_ROOT`, else `${XDG_STATE_HOME:-~/.local/state}/...`."""
    if os.environ.get("HACF_EXPORT_REPORT_ROOT"):
        return Path(os.environ["HACF_EXPORT_REPORT_ROOT"])
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "hacf" / "release-export"


def exclusion_lines(repo: Path, profile: Profile, release: Release) -> list[str]:
    """Each applied exclude row: path, reason, files and `def test_` count at the source."""
    entries = tree_entries(repo, release.source)
    lines = []
    for row in profile.exclude:
        if not applies(row.versions, release.version):
            continue
        hit = sorted(p for p in entries if excluded(p, row))
        if not hit:
            lines.append(f"| {row.path} | n/a at {release.version} | - | - |")
            continue
        blobs = read_blobs(repo, [entries[p][1] for p in hit if p.endswith(".py")])
        tests = sum(
            len(re.findall(rb"^\s*(?:async\s+)?def test_", data, re.M)) for data in blobs.values()
        )
        lines.append(f"| {row.path} | {row.reason} | {len(hit)} | {tests} |")
    return lines


def write_report(run: GateRun, outcomes: list[StepOutcome], verdict: str, kept: list[Path]) -> None:
    """REPORT.md: verdict, identities, steps, exclusions, advisory, deletions, skips, messages."""
    release = run.release
    overlay = "none"
    if release.overlay:
        overlay = f"{run.overlay_commit or 'null'} ({', '.join(release.overlay.paths)})"
    lines = [
        f"VERDICT: {verdict}",
        "",
        f"# Release export gate: v{release.version}",
        "",
        f"- version: {release.version}",
        f"- source: {release.source}",
        f"- export commit: {run.export}",
        f"- tree: {run.tree}",
        f"- tag object: {run.tag}",
        f"- overlay: {overlay}",
        "",
        "## Steps",
        "",
        "| # | step | verdict | exit | seconds | note |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(
            f"| {o.number} | {o.name} | {o.verdict} | {o.exit_code} | {o.seconds:.1f} | {o.note} |"
        )
    lines += ["", "Logs: `logs/<NN>-<step>.log`."]
    if kept:
        lines += ["", "Throwaway directories kept for inspection (FAIL):"]
        lines += [f"- {p}" for p in kept]
    lines += [
        "",
        "## Exclusions applied",
        "",
        "| path | reason | files | def test_ |",
        "|---|---|---|---|",
    ]
    lines += exclusion_lines(run.repo, run.profile, release)
    lines += ["", "## Advisory hits (never failing)", "", *(run.advisory or ["(none)"])]
    lines += ["", "## Public deletions against the previous public tree", ""]
    lines += [f"- {p}" for p in run.deletions] or ["(none)"]
    lines += ["", "## Skips (check.sh, -rs)", "", *(run.skips or ["(none recorded)"])]
    lines += ["", "## Messages in full", "", "### Commit object", "", "```text"]
    lines += [git(run.repo, "cat-file", "-p", run.export).stdout.rstrip("\n"), "```", ""]
    lines += [
        "### Tag object",
        "",
        "```text",
        git(run.repo, "cat-file", "-p", run.tag).stdout.rstrip("\n"),
        "```",
    ]
    write_output(run.report_dir / "REPORT.md", "\n".join(lines) + "\n")


def gate(
    repo: Path,
    profile_path: Path,
    version: str,
    digests_arg: str | None,
    report_root: Path | None,
    remote: str,
    runner: Runner | None = None,
) -> int:
    """`gate --version <v>`: prove one export (mission R9-R13, R17, R19, R20); exit 0 on PASS."""
    profile = load_profile(profile_path)
    index, release = profile.release(version)
    py310 = os.environ.get("HACF_PY310", "")
    if not py310:
        raise Refusal("HACF_PY310 is not set (the clone's check.sh needs it)")
    if not REMOTE_RE.match(remote):
        raise Refusal(f"bad remote name {remote!r}")
    digests = load_digests(digests_arg)
    require_labels(digests, profile)
    if profile.scan.missing_tool_literals():
        raise Refusal("the profile's scan section does not hold the tool's strict literals")
    tag = rev(repo, f"refs/tags/v{version}")
    export = rev(repo, f"refs/tags/v{version}^{{commit}}")
    if tag is None or export is None or tag == export:
        raise Refusal(f"v{version} is missing or is not an annotated tag")
    if rev(repo, PUBLIC_MAIN) is None:
        raise Refusal("public/main is missing")
    exports = {}
    for earlier in profile.releases[: index + 1]:
        sha = rev(repo, f"refs/tags/v{earlier.version}^{{commit}}")
        if sha is None:
            raise Refusal(f"v{earlier.version} has no export")
        exports[earlier.version] = sha

    report_dir = (report_root or default_report_root()) / f"{version}-{export[:12]}"
    if report_dir.exists():  # an earlier gate of the same export: keep it (lead ruling P11)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        kept = report_dir.with_name(f"{report_dir.name}.prev-{stamp}")
        n = 1
        while kept.exists():
            n += 1
            kept = report_dir.with_name(f"{report_dir.name}.prev-{stamp}-{n}")
        report_dir.rename(kept)
    (report_dir / "logs").mkdir(parents=True)
    run = GateRun(
        repo=repo,
        profile=profile,
        index=index,
        release=release,
        export=export,
        tag=tag,
        tree=git(repo, "rev-parse", f"{export}^{{tree}}").stdout.strip(),
        report_dir=report_dir,
        work=report_dir / "work",
        runner=runner or Runner(),
        py310=py310,
        digests=digests,
        exports=exports,
        overlay_commit=release.overlay.commit if release.overlay else None,
    )
    run.work.mkdir()
    outcomes: list[StepOutcome] = []
    stop = ""
    inherited = os.environ.get(host_gate.TOKEN_VAR)
    try:
        for number, (name, func) in enumerate(STEPS, start=1):
            out = StepOutcome(number, name)
            outcomes.append(out)
            if stop:
                out.note = stop
            else:
                start = time.monotonic()
                try:
                    func(run, out)
                except Refusal as exc:
                    out.verdict, out.note = "FAIL", str(exc)
                out.seconds = time.monotonic() - start
            log = report_dir / "logs" / f"{number:02d}-{name}.log"
            write_output(
                log,
                f"step {number} {name}: {out.verdict} ({out.note})\nexit {out.exit_code}, "
                f"{out.seconds:.1f}s\n\n" + "\n".join(out.log) + "\n",
            )
            if number == 4 and any(o.verdict == "FAIL" for o in outcomes):
                stop = "not run: scan, shape or links failed (no clone, no download)"
            elif name == "clone" and out.verdict != "PASS":
                stop = "not run: no public-only clone"
            elif out.note.startswith("host busy"):
                stop = "not run: host busy"
    finally:  # the one lease of the gated steps ends with the run
        if run.lease is not None:
            run.lease.release()
            if inherited is None:
                os.environ.pop(host_gate.TOKEN_VAR, None)
            else:
                os.environ[host_gate.TOKEN_VAR] = inherited
    verdict = "PASS" if all(o.verdict == "PASS" for o in outcomes) else "FAIL"
    kept: list[Path] = []
    if verdict == "PASS":
        shutil.rmtree(run.work, ignore_errors=True)
    elif any(run.work.iterdir()):
        kept = [run.work]
    else:
        run.work.rmdir()
    write_report(run, outcomes, verdict, kept)
    for o in outcomes:
        emit(f"{o.number:>2} {o.name:<18} {o.verdict:<4} {o.note}")
        for line in o.summary:
            emit(f"      {line}")
    emit(f"VERDICT: {verdict}  {report_dir / 'REPORT.md'}")
    if any(o.note.startswith("host busy") for o in outcomes):
        return EXIT_BUSY  # the host stayed busy: retry later (exit 75), not a product verdict
    return EXIT_OK if verdict == "PASS" else EXIT_FAIL


# ------------------------------------------------------------------------- plan, verify-push


REPORT_FIELD_RE = re.compile(r"^- (export commit|tree|tag object): ([0-9a-f]{40})$", re.M)


def read_report(report_root: Path, version: str, export: str) -> dict[str, str] | None:
    """The verdict and shas of `<root>/<v>-<export12>/REPORT.md`, or None when absent."""
    path = report_root / f"{version}-{export[:12]}" / "REPORT.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fields = dict(REPORT_FIELD_RE.findall(text))
    fields["verdict"] = text.split("\n", 1)[0].removeprefix("VERDICT: ").strip()
    fields["text"] = text
    return fields


@dataclass(frozen=True)
class Gated:
    """One release with its export, tag and tree, and the report that gated them."""

    version: str
    export: str
    tag: str
    tree: str


def gated_prefix(repo: Path, profile: Profile, root: Path) -> tuple[list[Gated], str]:
    """The longest prefix of releases exported on the public/main chain with a matching PASS."""
    public = rev(repo, PUBLIC_MAIN)
    out: list[Gated] = []
    for release in profile.releases:
        tag = rev(repo, f"refs/tags/v{release.version}")
        export = rev(repo, f"refs/tags/v{release.version}^{{commit}}")
        if tag is None or export is None or public is None:
            return out, f"v{release.version}: no export, tag or public/main"
        if not is_ancestor(repo, export, public):
            return out, f"v{release.version}: the export is not on public/main"
        tree = git(repo, "rev-parse", f"{export}^{{tree}}").stdout.strip()
        report = read_report(root, release.version, export)
        if report is None:
            return out, f"v{release.version}: no gate report"
        if report["verdict"] != "PASS":
            return out, f"v{release.version}: the gate report says {report['verdict']}"
        if (report.get("export commit"), report.get("tree"), report.get("tag object")) != (
            export,
            tree,
            tag,
        ):
            return out, f"v{release.version}: the gate report's shas differ from the refs"
        out.append(Gated(release.version, export, tag, tree))
    return out, ""


def report_section(text: str, heading: str) -> list[str]:
    """The lines of one `## heading` section of a report."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line == f"## {heading}"
        elif inside:
            lines.append(line)
    return [line for line in lines if line.strip()]


def plan(
    repo: Path,
    profile_path: Path,
    report_root: Path | None,
    remote: str,
    digests_arg: str | None = None,
) -> int:
    """`plan`: print and write PUSH-PLAN.md for the gated prefix; it runs no command (R12).

    It prints every message in full, so it loads the digests like `gate` (for the redaction).
    """
    profile = load_profile(profile_path)
    require_labels(load_digests(digests_arg), profile)
    if not REMOTE_RE.match(remote):
        raise Refusal(f"bad remote name {remote!r}")
    root = report_root or default_report_root()
    gated, stopped = gated_prefix(repo, profile, root)
    if not gated:
        raise Refusal(f"no gated release to push ({stopped})")
    tip = gated[-1]
    tags = " ".join(f"refs/tags/v{g.version}" for g in gated)
    push_args = f"--atomic {remote} {tip.export}:refs/heads/main {tags}"
    base = profile.base
    lines = [
        f"# Push plan: v{gated[0].version} .. v{tip.version}",
        "",
        "Written by `dev/release_export.py plan`. The tool runs none of these commands; the lead "
        "runs them, in order, after reading every message below.",
        "",
        "## Versions",
        "",
    ]
    lines += [
        f"- v{g.version}: export {g.export}, tag object {g.tag}, tree {g.tree}" for g in gated
    ]
    if stopped:
        lines.append(f"- stopped after v{tip.version}: {stopped}")
    for g in gated:
        lines += ["", f"## v{g.version} messages in full", "", "### Commit object", "", "```text"]
        lines += [git(repo, "cat-file", "-p", g.export).stdout.rstrip("\n"), "```", ""]
        lines += [
            "### Tag object",
            "",
            "```text",
            git(repo, "cat-file", "-p", g.tag).stdout.rstrip("\n"),
            "```",
        ]
    lines += ["", "## Excluded paths per version", ""]
    for g in gated:
        _, release = profile.release(g.version)
        lines += [
            f"### v{g.version}",
            "",
            "| path | reason | files | def test_ |",
            "|---|---|---|---|",
        ]
        lines += exclusion_lines(repo, profile, release) + [""]
    lines += ["## Advisory hits per version (never failing)", ""]
    for g in gated:
        report = read_report(root, g.version, g.export) or {"text": ""}
        lines += [
            f"### v{g.version}",
            "",
            *(report_section(report["text"], "Advisory hits (never failing)") or ["(none)"]),
            "",
        ]
    expected_tags = []
    for g in gated:
        expected_tags += [
            f"{g.tag}\trefs/tags/v{g.version}",
            f"{g.export}\trefs/tags/v{g.version}^{{}}",
        ]
    lines += [
        "## Commands",
        "",
        "1. Check the remote before the push:",
        "",
        f"       git ls-remote {remote} refs/heads/main 'refs/tags/v*'",
        "",
        f"   Expected: `{base}\trefs/heads/main` (or the last published export) and no line for a "
        "planned tag.",
        "",
        "2. Re-check the ancestry, the reports and the tags:",
        "",
        f"       python3 dev/release_export.py verify-push --through {tip.version} "
        f"--report-root {shlex.quote(str(root))} --remote {remote}",
        "",
        "   Expected: every line `ok`, exit 0.",
        "",
        "3. Dry run, atomic:",
        "",
        f"       git push --dry-run --porcelain {push_args}",
        "",
        "   Expected (then `Done`, exit 0):",
        "",
        f"       \t{tip.export}:refs/heads/main\t{base[:7]}..{tip.export[:7]} (a fast-forward; "
        "the abbreviations may be longer)",
        *[f"       *\trefs/tags/v{g.version}:refs/tags/v{g.version}\t[new tag]" for g in gated],
        "",
        "4. The push, atomic:",
        "",
        f"       git push --porcelain {push_args}",
        "",
        "   Expected: the same lines as step 3, then `Done`, exit 0.",
        "",
        "5. Verify:",
        "",
        f"       git ls-remote {remote} refs/heads/main 'refs/tags/v*'",
        "",
        "   Expected:",
        "",
        f"       {tip.export}\trefs/heads/main",
        *[f"       {line}" for line in expected_tags],
        "",
        "## Recovery",
        "",
        "- An atomic push that fails moved nothing: run step 1 again, and if the remote still "
        "matches it, retry the push once; if it fails again, park the push for TJ.",
        "- Any mismatch after the push (step 5): park it and report; no retry loop.",
        "- Never `--force`, never `--no-verify`, never rebuild a published version.",
        "- One atomic push carries every gate-passing version above.",
        "",
        "Notes: TJ's look at the README on GitHub is the mermaid render check.",
    ]
    text = "\n".join(lines) + "\n"
    root.mkdir(parents=True, exist_ok=True)
    write_output(root / "PUSH-PLAN.md", text)
    emit(text, end="")
    emit(f"written: {root / 'PUSH-PLAN.md'}")
    return EXIT_OK


def verify_push(
    repo: Path,
    profile_path: Path,
    through: str,
    report_root: Path | None,
    remote: str,
    use_remote: bool,
) -> int:
    """`verify-push --through <v>`: the last checks before the lead's push (R12); exit 1 on FAIL."""
    profile = load_profile(profile_path)
    index, _ = profile.release(through)
    if not REMOTE_RE.match(remote):
        raise Refusal(f"bad remote name {remote!r}")
    root = report_root or default_report_root()
    planned = profile.releases[: index + 1]
    results: list[tuple[bool | None, str]] = []

    def check(ok: bool, what: str, why: str = "") -> None:
        results.append((ok, what if ok else f"{what}: {why}"))

    exports = {r.version: rev(repo, f"refs/tags/v{r.version}^{{commit}}") for r in planned}
    tags = {r.version: rev(repo, f"refs/tags/v{r.version}") for r in planned}
    missing = [v for v, sha in exports.items() if sha is None]
    check(not missing, "every planned version has an export tag", f"missing v{', v'.join(missing)}")
    tip = exports[through]
    if tip is not None and not missing:
        listing = git(repo, "rev-list", "--parents", "--reverse", tip, f"^{profile.base}").stdout
        rows = [line.split() for line in listing.splitlines()]
        want = [exports[r.version] for r in planned]
        check(
            [r[0] for r in rows] == want,
            "rev-list from the base lists exactly the exports",
            f"got {[r[0][:12] for r in rows]}, want {[w[:12] for w in want]}",
        )
        single = all(len(r) == 2 for r in rows)
        check(
            single, "each export has exactly one parent", "a merge or a root commit is in the chain"
        )
        chained = single and [r[1] for r in rows] == [profile.base] + want[:-1]
        check(
            chained,
            f"the chain starts at the base {profile.base[:12]}",
            "a parent is out of the chain",
        )
        check(
            rev(repo, PUBLIC_MAIN) == tip,
            f"public/main is the v{through} export",
            f"public/main is {str(rev(repo, PUBLIC_MAIN))[:12]}",
        )
    for release in planned:
        version, export, tag = release.version, exports[release.version], tags[release.version]
        if export is None or tag is None:
            continue
        report = read_report(root, version, export)
        tree = git(repo, "rev-parse", f"{export}^{{tree}}").stdout.strip()
        check(
            report is not None and report["verdict"] == "PASS" and report.get("tree") == tree,
            f"v{version}: a PASS report with the same tree",
            "no report"
            if report is None
            else f"verdict {report['verdict']}, tree {report.get('tree', '?')[:12]}",
        )
        check(
            report is not None and report.get("tag object") == tag,
            f"v{version}: the tag object is the gated one",
            "the tag was re-pointed after the gate",
        )
        check(
            git(repo, "cat-file", "-t", tag).stdout.strip() == "tag"
            and rev(repo, f"{tag}^{{commit}}") == export,
            f"v{version}: the tag peels to its export",
            "the tag does not peel to the export",
        )
    if not use_remote:
        results.append((None, "skipped (--no-remote): the remote's main and tags"))
    else:
        proc = git(
            repo,
            "ls-remote",
            "--end-of-options",
            remote,
            "refs/heads/main",
            "refs/tags/v*",
            check=False,
        )
        if proc.returncode != 0:
            check(False, f"read {remote} with ls-remote", "ls-remote failed")
        else:
            refs = {}
            for line in proc.stdout.splitlines():
                sha, _, ref = line.partition("\t")
                refs[ref] = sha
            main = refs.get("refs/heads/main")
            chain = [profile.base] + [exports[r.version] for r in planned]
            check(
                main in chain,
                f"{remote}'s main is the base or a planned export",
                f"{remote}'s main is {main}",
            )
            published = chain.index(main) if main in chain else len(chain)
            for n, release in enumerate(planned, start=1):
                remote_tag = refs.get(f"refs/tags/v{release.version}")
                if n > published:
                    check(
                        remote_tag is None,
                        f"v{release.version} is not on {remote} yet",
                        f"{remote} already has v{release.version}",
                    )
                else:
                    check(
                        remote_tag == tags[release.version],
                        f"v{release.version} on {remote} is the local tag",
                        f"{remote} has {remote_tag}",
                    )
    for ok, text in results:
        emit({True: "ok   ", False: "FAIL ", None: ""}[ok] + text)
    return EXIT_FAIL if any(ok is False for ok, _ in results) else EXIT_OK


# --------------------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code."""
    parser = argparse.ArgumentParser(prog="release_export.py", description=__doc__.split("\n")[0])
    parser.add_argument("--repo", type=Path, default=ROOT, help="the private repository")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    sub = parser.add_subparsers(dest="command", required=True)
    profile_cmd = sub.add_parser("profile", help="profile commands")
    profile_cmd.add_argument("action", choices=["check"])
    build_cmd = sub.add_parser("build", help="build one version's export commit and tag")
    build_cmd.add_argument("--version", required=True)
    build_cmd.add_argument("--replace-unpushed", action="store_true")
    build_cmd.add_argument("--remote", default="origin")
    scan_cmd = sub.add_parser(
        "scan", help="scan a version's would-be export tree, or HEAD or the work tree (read only)"
    )
    scan_what = scan_cmd.add_mutually_exclusive_group(required=True)
    scan_what.add_argument("--version")
    scan_what.add_argument(
        "--tree",
        choices=["worktree", "head"],
        help="scan the work tree or HEAD with the latest release row's excludes; exit 3 "
        "(unknown) without a digest file",
    )
    scan_cmd.add_argument("--overlay-commit")
    scan_cmd.add_argument("--digests")
    gate_cmd = sub.add_parser("gate", help="prove one export; write its REPORT.md")
    gate_cmd.add_argument("--version", required=True)
    gate_cmd.add_argument("--digests")
    gate_cmd.add_argument("--report-root", type=Path)
    gate_cmd.add_argument("--remote", default="origin")
    plan_cmd = sub.add_parser("plan", help="write PUSH-PLAN.md for the gated releases")
    plan_cmd.add_argument("--report-root", type=Path)
    plan_cmd.add_argument("--remote", default="origin")
    plan_cmd.add_argument("--digests")
    line_cmd = sub.add_parser(
        "digest-line",
        help="print one private digest-file line for a literal read from stdin",
        description="Read a literal (or a regex) from stdin and print one digest-file line. "
        "Append the printed line to the private digest file by hand; the literal never appears "
        "in argv, the output or an error.",
    )
    line_cmd.add_argument("--label", required=True)
    line_cmd.add_argument("--kind", required=True, choices=["window", "regex"])
    line_cmd.add_argument("--level", required=True, choices=["strict", "advisory"])
    verify_cmd = sub.add_parser("verify-push", help="the last checks before the lead's push")
    verify_cmd.add_argument("--through", required=True)
    verify_cmd.add_argument("--report-root", type=Path)
    verify_cmd.add_argument("--remote", default="origin")
    verify_cmd.add_argument("--no-remote", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    try:
        if args.command == "digest-line":
            print(digest_line(args.label, args.kind, args.level, sys.stdin.read()))
        elif args.command == "profile":
            profile_check(repo, args.profile)
        elif args.command == "build":
            emit(build(repo, args.profile, args.version, args.replace_unpushed, args.remote))
        elif args.command == "scan" and args.tree:
            return scan_tree_command(
                repo, args.profile, args.tree, args.digests, args.overlay_commit
            )
        elif args.command == "scan":
            return scan_command(repo, args.profile, args.version, args.overlay_commit, args.digests)
        elif args.command == "gate":
            return gate(
                repo, args.profile, args.version, args.digests, args.report_root, args.remote
            )
        elif args.command == "plan":
            return plan(repo, args.profile, args.report_root, args.remote, args.digests)
        else:
            return verify_push(
                repo, args.profile, args.through, args.report_root, args.remote, not args.no_remote
            )
    except Refusal as exc:
        emit(f"release_export: {exc}", err=True)
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
