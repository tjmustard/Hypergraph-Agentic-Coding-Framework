#!/usr/bin/env python3
"""hyper_openrig.py — HACF's only writer of OpenRig manifests, for the accepted rig versions.

The adapter freezes HACF's contract with OpenRig (github.com/mvschwarz/openrig) in one module. Its
accepted set lives in .agents/config/openrig_contract.yml (load_contract(); ACCEPTED_RIG_VERSIONS is
computed from it at import); the templates were transcribed from tag v0.6.13, commit 9eeb2e42, and
compared against 0.7.16 (release-0.6.1 slice 9). Normative design:
HACF's v0.6.0 design notes (not shipped): OpenRig manifests, key ownership and spec frontmatter. The
transcribed manifests and their sources live in `dev/tests/fixtures/openrig-0.6.13/`; the live
contract test is `dev/tests/contract/test_openrig_live.py`.

Library (for later slices):
- render_project_yaml(), render_workspace_yaml(), render_mission_yaml(), render_slice_yaml():
  the v0alpha1 manifests, byte-identical to rig's templates (mission.yaml in rig 0.7.16's named
  shape; a 0.6.13 mission.yaml without metadata.name stays valid and is never rewritten).
- validate_mission_composition(), read_mission_members(), add_slice_to_mission(),
  remove_slice_from_mission(): mission.yaml membership under rig's rules. Nothing is cached:
  every call reads mission.yaml from disk, because `rig scope slice create` appends membership
  itself.
- mission_dot_id(), slice_dot_id(), is_mission_dot_id(), is_slice_dot_id(): dot-IDs (prefix OPR).
- openrig_status(), set_slice_status(), close_superseded(): the hacf_status mapping. HACF writes a
  status only into SPEC.md frontmatter, never into slice.yaml (which has no status field).
- resolve_rig(), plan_rig(): rig on PATH, or npx only with explicit download consent; the version
  rule (R22.7). plan_rig() reads the range-probe cache only.
- prepare_rig(), run_probe(), invalidate_probe(): the range probe (release-0.6.2 R4-R11). An
  unlisted rig inside the contract's range delegates only after an isolated, cached, content-keyed
  probe of the delegated surface passed against .agents/config/openrig_probe_expect.json; every
  other outcome is native plus a warning. `.agents/local/hacf.yml` `openrig.range_probe: false`
  turns the range off.
- argv_* builders, run_rig(), rig_slice_create(), rig_slice_close(): rig calls as argv lists with
  a timeout; every artifact-naming call passes --name (R22.4).
- write_proof_artifact(): delegates to `rig proof add` under an accepted rig, else writes the same
  C1 header itself.

Every write to a file rig may also write is compare-and-swap (#key-ownership): the bytes read are
hashed, the file is re-read just before os.replace, and a changed file is refused (exit 5) with
nothing written.

CLI:
    register-project --catalog <path> --id <slug> [--dry-run]
    unregister --catalog <path> --id <slug>
    doctor --catalog <path>
    contract show [--json]
    contract probe [--reprobe] [--json]

register-project and unregister change the user's OpenRig catalog (their workspace.yaml) only
after a terminal confirmation and a backup into .agents/state/openrig/catalog-backups/; the
fingerprint (repo realpath and root-commit shas) is kept in
.agents/state/openrig/registrations.json.

Exit codes: 0 ok, 1 findings (doctor), 2 usage, 4 parse error, 5 refused.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import difflib
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple

import _runtime

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_PARSE = 4
EXIT_REFUSED = 5

# --- Contract (accepted rig versions) -------------------------------------------------------------
# The accepted set and the probed range live in .agents/config/openrig_contract.yml (release-0.6.2
# R3); load_contract() reads it and narrows to FALLBACK_RIG_VERSIONS on any doubt. A new version
# joins that file only after dev/tests/contract/test_openrig_live.py passes on it.
CONTRACT_REL = ".agents/config/openrig_contract.yml"
FALLBACK_RIG_VERSIONS = ("0.6.13", "0.7.16", "0.7.21", "0.7.23")
CONTRACT_MAX_BYTES = 64 * 1024
CONTRACT_KEYS = ("schema", "accepted", "range", "probe")
CONTRACT_ENTRY_KEYS = ("version", "integrity", "verdict", "accepted_by")
CONTRACT_RANGE_KEYS = ("major", "minor", "min_patch")
CONTRACT_ACCEPTED_BY = ("person", "watcher")
CONTRACT_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ContractEntry(NamedTuple):
    """One accepted rig version of the contract file."""

    version: str
    integrity: str
    verdict: str
    accepted_by: str


class Contract(NamedTuple):
    """The parsed contract: accepted entries in file order, the probed range and where it came from.

    source is "file", or "fallback" (the compiled-in tuple, no range) with a one-line warning.
    """

    accepted: tuple[ContractEntry, ...]
    range: tuple[tuple[int, int, int], ...]
    probe_schema: int
    source: str
    warning: str | None

    @property
    def versions(self) -> tuple[str, ...]:
        """The accepted versions, in file order."""
        return tuple(entry.version for entry in self.accepted)


_contract_warned = False


class _ContractInvalid(Exception):
    pass


def _contract_int(value: Any, what: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _ContractInvalid(f"{what} must be an integer >= {minimum}")
    return value


def _contract_quoted_versions(text: str) -> bool:
    """True when each accepted[].version is a quoted scalar (unquoted 0.7 or 0.10.1 is not)."""
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    for key, value in root.value if isinstance(root, yaml.MappingNode) else ():
        if getattr(key, "value", None) != "accepted" or not isinstance(value, yaml.SequenceNode):
            continue
        for item in value.value:
            for k, v in item.value if isinstance(item, yaml.MappingNode) else ():
                if getattr(k, "value", None) == "version":
                    if not isinstance(v, yaml.ScalarNode) or v.style not in ("'", '"'):
                        return False
    return True


def _parse_contract(raw: bytes) -> Contract:
    """Validate the contract file under every R3 rule; raise _ContractInvalid on the first one."""
    if len(raw) > CONTRACT_MAX_BYTES:
        raise _ContractInvalid(f"larger than {CONTRACT_MAX_BYTES} bytes")
    if yaml is None:
        raise _ContractInvalid("PyYAML is not available")
    try:
        text = raw.decode("utf-8")
        data = yaml.safe_load(text)
        quoted = _contract_quoted_versions(text)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError) as exc:
        first = (str(exc).splitlines() or [type(exc).__name__])[0]
        raise _ContractInvalid(f"cannot parse: {first}") from None
    if not isinstance(data, dict):
        raise _ContractInvalid("not a mapping")
    if set(data) != set(CONTRACT_KEYS):
        raise _ContractInvalid(f"top-level keys must be exactly {', '.join(CONTRACT_KEYS)}")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise _ContractInvalid("schema must be 1")
    if not isinstance(data["accepted"], list) or not data["accepted"]:
        raise _ContractInvalid("accepted must be a non-empty list")
    if not quoted:
        raise _ContractInvalid("every accepted version must be a quoted string")
    entries: list[ContractEntry] = []
    for item in data["accepted"]:
        if not isinstance(item, dict) or set(item) != set(CONTRACT_ENTRY_KEYS):
            raise _ContractInvalid(
                f"each accepted entry must have exactly {', '.join(CONTRACT_ENTRY_KEYS)}"
            )
        version = item["version"]
        if not isinstance(version, str) or not CONTRACT_VERSION_RE.match(version):
            raise _ContractInvalid(f"version {version!r} is not a plain M.m.p string")
        if not isinstance(item["integrity"], str) or not isinstance(item["verdict"], str):
            raise _ContractInvalid(f"{version}: integrity and verdict must be strings")
        if item["accepted_by"] not in CONTRACT_ACCEPTED_BY:
            raise _ContractInvalid(f"{version}: accepted_by must be person or watcher")
        if any(entry.version == version for entry in entries):
            raise _ContractInvalid(f"duplicate version {version}")
        entry = ContractEntry(version, item["integrity"], item["verdict"], item["accepted_by"])
        entries.append(entry)
    if not isinstance(data["range"], list):
        raise _ContractInvalid("range must be a list")
    minors = {tuple(int(x) for x in entry.version.split(".")[:2]) for entry in entries}
    ranges: list[tuple[int, int, int]] = []
    for item in data["range"]:
        if not isinstance(item, dict) or set(item) != set(CONTRACT_RANGE_KEYS):
            keys = ", ".join(CONTRACT_RANGE_KEYS)
            raise _ContractInvalid(f"each range entry must have exactly {keys}")
        triple = tuple(_contract_int(item[k], f"range {k}", 0) for k in CONTRACT_RANGE_KEYS)
        if any(r[:2] == triple[:2] for r in ranges):
            raise _ContractInvalid(f"two range entries for {triple[0]}.{triple[1]}")
        if triple[:2] not in minors:
            raise _ContractInvalid(f"range {triple[0]}.{triple[1]} has no accepted version")
        ranges.append(triple)  # type: ignore[arg-type]
    probe = data["probe"]
    if not isinstance(probe, dict) or set(probe) != {"schema"}:
        raise _ContractInvalid("probe must be a mapping with exactly schema")
    schema = _contract_int(probe["schema"], "probe.schema", 1)
    return Contract(tuple(entries), tuple(ranges), schema, "file", None)


def load_contract(path: Path | None = None) -> Contract:
    """Return the contract from `path` (default: the file in this script's own installed tree).

    Any doubt (missing file, read error, size, YAML error, any R3 rule) gives the fallback:
    FALLBACK_RIG_VERSIONS as person entries, no range, and a warning printed at most once per
    process. The fallback is never wider than the file could be.
    """
    global _contract_warned
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "openrig_contract.yml"
    try:
        with open(path, "rb") as handle:
            raw = handle.read(CONTRACT_MAX_BYTES + 1)
        return _parse_contract(raw)
    except FileNotFoundError:
        reason = "missing"
    except OSError as exc:
        reason = f"cannot read: {exc.strerror or exc}"
    except _ContractInvalid as exc:
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - any other doubt about the file narrows, never crashes
        first = (str(exc).splitlines() or [""])[0]
        reason = f"cannot parse: {type(exc).__name__}: {first}"[:300]
    warning = (
        f"{path}: {reason}; using the compiled-in accepted set "
        f"({', '.join(FALLBACK_RIG_VERSIONS)}) with no probed range"
    )
    if not _contract_warned:
        _contract_warned = True
        print(f"hyper_openrig: warning: {warning}", file=sys.stderr)
    entries = tuple(ContractEntry(v, "", "", "person") for v in FALLBACK_RIG_VERSIONS)
    return Contract(entries, (), 1, "fallback", warning)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def contract_rig_version(contract: Contract) -> str:
    """The npx download target: the highest accepted_by: person version (numeric compare)."""
    people = [e.version for e in contract.accepted if e.accepted_by == "person"]
    return max(people or FALLBACK_RIG_VERSIONS, key=_version_key)


_CONTRACT = load_contract()
# HACF delegates to rig only when `rig --version` is one of these (computed from the contract file).
ACCEPTED_RIG_VERSIONS = _CONTRACT.versions
# The version npx downloads, only with the user's explicit download consent.
RIG_VERSION = contract_rig_version(_CONTRACT)
RIG_COMMIT = "9eeb2e42"  # the source of the transcribed templates (tag v0.6.13)
RIG_PACKAGE = "@openrig/cli"
SCHEMA_PROJECT = "openrig.project/v0alpha1"
SCHEMA_WORKSPACE = "openrig.workspace/v0alpha1"
SCHEMA_MISSION = "openrig.mission/v0alpha1"
SCHEMA_SLICE = "openrig.slice/v0alpha1"
# rig 0.7.21 has project.yaml scope.idPrefix; HACF does not use it (a known-issues-0.6 note).
DEFAULT_ID_PREFIX = "OPR"
NPX_PREFIX = ("npx", "-y", f"--package={RIG_PACKAGE}@{RIG_VERSION}", "rig")
RIG_TIMEOUT = 300  # seconds per rig call; the first npx call also downloads the package
GIT_TIMEOUT = 60

C1_ARTIFACT_TYPES = ("guard", "qa", "rev1-r1", "rev1-r2", "adjudication")
C1_VERDICTS = ("CLEAR", "BLOCKING", "CONCERNING", "PASS", "NOT-CLEAR")
C1_REQUIRED_FIELDS = ("slice", "candidate_sha", "artifact_type", "verdict", "money_evidence")
CLOSE_REASONS = ("wontfix", "deferred", "superseded", "stale")
SLICE_STATES = ("active", "closed", "shipped", "all")
MISSION_STATUSES = ("planned", "active", "complete")  # mission.yaml metadata.status (R29)

# hacf_status -> the status rig reads from slice SPEC.md frontmatter (schemas.md#spec-frontmatter)
# Accepted difference (R21b): rig 0.7.16 lists `done` under `slice ls --state shipped`; 0.6.13
# lists it only under `all`.
STATUS_MAP = {
    "compiled": "active",
    "executing": "active",
    "executed": "active",
    "audit_failed": "active",
    "blocked": "active",
    "audited": "done",
    "superseded": "closed-superseded",
}

DOT_ID_RE = re.compile(r"^([A-Z]{2,3})\.(\d+(?:\.\d+){1,4})$")
RELEASE_NAME_RE = re.compile(r"^release-(\d+\.\d+(?:\.\d+)?)$")
PREFIX_RE = re.compile(r"^[A-Z]{2,3}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")  # mission and slice directory names
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")  # project ids and new slice slugs
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?=\r?\n|$)")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# OpenRig's scalar() rule: plain only for a simple name or path YAML reads back unchanged.
SIMPLE_SCALAR_RE = re.compile(r"^[A-Za-z0-9_/][A-Za-z0-9._/-]*$|^\.{1,2}(/[A-Za-z0-9._/-]*)?$")

STATE_REL = ".agents/state"
OPENRIG_STATE_REL = ".agents/state/openrig"
REGISTRATIONS_SCHEMA = "hacf.openrig-registrations/v1"


class OpenRigError(Exception):
    """A failure the CLI reports with its exit code."""

    code = EXIT_REFUSED


class UsageError(OpenRigError):
    """Bad arguments or a call the contract does not allow (exit 2)."""

    code = EXIT_USAGE


class ParseError(OpenRigError):
    """A YAML or JSON file that does not parse (exit 4)."""

    code = EXIT_PARSE


class Refused(OpenRigError):
    """A refused change; nothing was written (exit 5)."""

    code = EXIT_REFUSED


class CompositionError(Refused):
    """A mission.yaml composition that breaks rig's rules; kind is rig's issue code."""

    def __init__(self, kind: str, message: str) -> None:
        """Keep rig's issue code (for example lifecycle_path_escape) next to the message."""
        super().__init__(f"{kind}: {message}")
        self.kind = kind


# ---------------------------------------------------------------------------
# Processes, files and git
# ---------------------------------------------------------------------------


class Completed(NamedTuple):
    """The outcome of one subprocess: its argv, exit code and captured output."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> Completed:
    """Run argv, a list of strings and never a shell string, with a timeout.

    Every subprocess this module starts goes through here (test_no_shell checks that). It never
    raises on an exit code: a missing executable comes back as 127 and a timeout as 124, with the
    reason in stderr.
    """
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings")
    try:
        proc = subprocess.run(
            argv,
            cwd=None if cwd is None else str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        return Completed(argv, 127, "", f"{argv[0]}: {exc.strerror}")
    except subprocess.TimeoutExpired as exc:
        stderr = _text(exc.stderr) + f"\ntimed out after {timeout} seconds"
        return Completed(argv, 124, _text(exc.stdout), stderr)
    return Completed(argv, proc.returncode, proc.stdout, proc.stderr)


def _git(args: list[str], cwd: Path) -> Completed:
    return _run(["git", *args], cwd=cwd, timeout=GIT_TIMEOUT)


def repo_toplevel(start: Path | None = None) -> Path:
    """Return the git work-tree top level containing start (default: the working directory)."""
    proc = _git(["rev-parse", "--show-toplevel"], start or Path.cwd())
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        raise UsageError("not inside a git work tree; run hyper_openrig.py from the repository")
    return Path(top)


def root_commits(repo: Path) -> list[str]:
    """Return the sorted root-commit shas of repo (`git rev-list --max-parents=0 HEAD`), or []."""
    proc = _git(["rev-list", "--max-parents=0", "HEAD"], repo)
    if proc.returncode != 0:
        return []
    return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _read_text(path: Path) -> str:
    """Return a file's text exactly (no newline translation), so its sha256 is the file's."""
    return Path(path).read_bytes().decode("utf-8")


def _sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace_file(path: Path, text: str, expected: str | None, *, check: bool) -> None:
    """Write text to a temp file beside path, then os.replace it (after the CAS check)."""
    path = Path(path)
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        if check and _current_sha(path) != expected:
            raise Refused(f"{path} changed since it was read; nothing was written (re-read, retry)")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def cas_write(path: Path, original: str | None, text: str) -> None:
    """Replace path with text unless its bytes changed since they were read as original.

    original None means the file must not exist yet. A mismatch raises Refused (exit 5).
    """
    _replace_file(path, text, _sha256(original), check=True)


def _restore_file(path: Path, text: str) -> None:
    _replace_file(path, text, None, check=False)


def _load_yaml(path: Path) -> tuple[str, Any]:
    """Return (text, parsed) for a YAML file that is a regular file, not a symlink."""
    path = Path(path)
    if path.is_symlink():
        raise Refused(f"{path} may not be a symlink")
    if not path.is_file():
        raise Refused(f"{path} does not exist")
    text = _read_text(path)
    try:
        return text, yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseError(f"{path} is not valid YAML: {exc}") from exc


# ---------------------------------------------------------------------------
# YAML text helpers (byte-preserving edits)
# ---------------------------------------------------------------------------


def yaml_scalar(value: str) -> str:
    """Return value as a YAML scalar: plain when YAML reads it back unchanged, else double-quoted.

    The same rule as OpenRig's scalar() in project-registration.ts; a JSON string is valid YAML.
    """
    if SIMPLE_SCALAR_RE.match(value):
        with contextlib.suppress(yaml.YAMLError):
            if yaml.safe_load(value) == value:
                return value
    return json.dumps(value)


def flow_value(value: Any) -> str:
    """Render a frontmatter value in YAML flow style (scalars, lists and mappings)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return yaml_scalar(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(flow_value(item) for item in value) + "]"
    if isinstance(value, dict):
        items = (f"{yaml_scalar(str(k))}: {flow_value(v)}" for k, v in value.items())
        return "{" + ", ".join(items) + "}"
    raise UsageError(f"cannot render {type(value).__name__} as a frontmatter value")


def _mapping_item(node: Any, key: str) -> tuple[Any, Any]:
    """Return the (key node, value node) pair for key in a composed mapping, or (None, None)."""
    if not isinstance(node, yaml.MappingNode):
        return None, None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node, value_node
    return None, None


def _node_end(node: Any) -> int:
    """Return the index just after a composed node's own text.

    A block collection's end mark can lie past trailing comments, so its end is taken from the
    last scalar inside it instead.
    """
    if isinstance(node, yaml.ScalarNode) or node.flow_style:
        return node.end_mark.index
    if isinstance(node, yaml.SequenceNode):
        children = node.value
    else:
        children = [part for pair in node.value for part in pair]
    return max((_node_end(child) for child in children), default=node.end_mark.index)


def _value_span(text: str, key_node: Any, value_node: Any) -> tuple[int, int]:
    """Return the span from just after the key's colon to the end of its value."""
    return text.index(":", key_node.end_mark.index) + 1, _node_end(value_node)


def split_frontmatter(text: str) -> tuple[int, int, dict]:
    """Return (start, end, data) for the YAML block between a document's `---` lines."""
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise Refused("the document has no YAML frontmatter")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ParseError(f"frontmatter is not valid YAML: {exc}") from exc
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ParseError("frontmatter is not a mapping")
    return match.start(1), match.end(1), data


def read_frontmatter(path: Path) -> dict:
    """Return a Markdown file's frontmatter mapping ({} when the file is missing)."""
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return {}
    return split_frontmatter(text)[2]


def splice_frontmatter(text: str, updates: dict[str, Any]) -> str:
    """Return text with only the given top-level frontmatter keys rewritten.

    Every other key keeps its bytes and its place, the splice rule of
    schemas.md#spec-frontmatter (rig's updateFrontmatter works the same way). A missing key is
    appended at the end of the block; values are written in flow style. The result must re-parse
    to the old mapping with the updates applied, or nothing is returned (Refused).
    """
    start, end, data = split_frontmatter(text)
    block = text[start:end]
    newline = "\r\n" if "\r\n" in text else "\n"
    root = yaml.compose(block, Loader=yaml.SafeLoader)
    edits, appended = [], []
    for key, value in updates.items():
        if not KEY_RE.match(key):
            raise UsageError(f"frontmatter key {key!r} is not a simple key")
        key_node, value_node = _mapping_item(root, key)
        if key_node is None:
            appended.append(f"{key}: {flow_value(value)}")
        else:
            begin, finish = _value_span(block, key_node, value_node)
            edits.append((begin, finish, " " + flow_value(value)))
    for begin, finish, replacement in sorted(edits, reverse=True):
        block = block[:begin] + replacement + block[finish:]
    if appended:
        block = newline.join([block, *appended]) if block.strip() else newline.join(appended)
    result = text[:start] + block + text[end:]
    if split_frontmatter(result)[2] != {**data, **updates}:
        raise Refused("could not rewrite the frontmatter without changing other keys")
    return result


# ---------------------------------------------------------------------------
# Manifests (schemas.md#openrig-manifests), byte-identical to rig's templates: 0.6.13's, and for
# mission.yaml 0.7.16's missionManifest(name), which adds metadata.name (R21a)
# ---------------------------------------------------------------------------

_PROJECT_YAML = """\
schema: openrig.project/v0alpha1
kind: project
install:
  intent: SPEC.md
  context: []
  skills: []
missions:
  root: missions
# Add ordered project Markdown addresses and stable catalog skill IDs above.
# Skill source and the System World remain outside the project workspace.
"""

_WORKSPACE_YAML = """\
schema: openrig.workspace/v0alpha1
projects:
  - id: default
    root: .
"""

_MISSION_YAML = """\
schema: openrig.mission/v0alpha1
kind: mission
# The project proof rollup counts only missions whose status is active or
# release-candidate. A new mission is planned, so it stays outside the rollup
# until you change this to active (or release-candidate).
metadata:
  status: planned
composition:
  mission_markdown:
    spec: SPEC.md
  slices: []
# Optional team and SDLC sections are added here.
"""

_SLICE_YAML = """\
schema: openrig.slice/v0alpha1
kind: slice
composition:
  mission: ../../mission.yaml
  slice_markdown:
    spec: SPEC.md
    progress: PROGRESS.md
    proof: PROOF.md
# Optional assignment, SDLC, and evidence sections are added here.
"""


def render_project_yaml() -> str:
    """Return project.yaml as rig's default workspace scaffold writes it (PROJECT_MANIFEST)."""
    return _PROJECT_YAML


def render_workspace_yaml() -> str:
    """Return workspace.yaml, the catalog with one project {id: default, root: .}."""
    return _WORKSPACE_YAML


def render_mission_yaml(name: str) -> str:
    """Return a new mission's mission.yaml: status planned, no slices, named `name`.

    The bytes of rig 0.7.16's missionManifest(name) (dist/commands/scope.js:30): 0.6.13's
    MISSION_MANIFEST with `  name: "<name>"` as the first key under `metadata:` (R21a). NAME_RE
    admits only [A-Za-z0-9._-], so json.dumps quotes it exactly as JSON.stringify does. A 0.6.13
    mission.yaml (no metadata.name) stays valid; HACF never rewrites it or adds the key.
    """
    named = f"metadata:\n  name: {json.dumps(_check_name(name, 'mission'))}\n"
    return _MISSION_YAML.replace("metadata:\n", named, 1)


def render_slice_yaml() -> str:
    """Return a slice's slice.yaml (SLICE_MANIFEST); HACF never adds status or depends_on."""
    return _SLICE_YAML


def missions_root(workspace: Path) -> Path:
    """Return the missions directory rig uses for a workspace: project.yaml missions.root.

    The fallback is `missions/`; a declared root outside the workspace is refused.
    """
    workspace = Path(workspace)
    rel = "missions"
    manifest = workspace / "project.yaml"
    if manifest.is_file():
        _, data = _load_yaml(manifest)
        missions = data.get("missions") if isinstance(data, dict) else None
        declared = missions.get("root") if isinstance(missions, dict) else None
        if isinstance(declared, str) and declared:
            rel = declared
    root = Path(os.path.normpath(workspace / rel))
    if os.path.relpath(root, workspace).split(os.sep)[0] == "..":
        raise Refused(f"{manifest}: missions.root {rel!r} lies outside the workspace")
    return root


# ---------------------------------------------------------------------------
# Mission composition (rig's validateMissionComposition)
# ---------------------------------------------------------------------------


class Member(NamedTuple):
    """One mission.yaml composition entry."""

    ref: str
    order: int
    active: bool


def validate_mission_composition(mission: Any, mission_path: Path) -> list[Member]:
    """Return the composition members of a parsed mission.yaml, under rig's rules.

    Each ref is a safe relative path (not absolute, no `..` once normalized) that is unique,
    exists, is not a symlink and resolves inside the mission; each order is an integer, unique
    and strictly increasing. Raises CompositionError with rig's issue code on the first breach.
    """
    if not isinstance(mission, dict) or mission.get("kind") != "mission":
        raise CompositionError(
            "lifecycle_manifest_kind_mismatch", f"{mission_path}: expected kind mission"
        )
    composition = mission.get("composition")
    if not isinstance(composition, dict) or not isinstance(composition.get("slices"), list):
        raise CompositionError(
            "lifecycle_membership_missing", f"{mission_path}: composition.slices must be a list"
        )
    mission_dir = Path(mission_path).parent
    mission_real = os.path.realpath(mission_dir)
    seen: set[str] = set()
    members: list[Member] = []
    for index, raw in enumerate(composition["slices"]):
        where = f"{mission_path}: composition.slices[{index}]"
        if not isinstance(raw, dict):
            raise CompositionError("lifecycle_membership_invalid", f"{where} must be a mapping")
        ref = raw.get("ref")
        if not isinstance(ref, str) or not ref:
            raise CompositionError("lifecycle_field_missing", f"{where}.ref must be a string")
        normalized = os.path.normpath(ref)
        if os.path.isabs(ref) or ".." in normalized.split(os.sep):
            raise CompositionError(
                "lifecycle_path_escape", f"{where}.ref must be a safe relative path without '..'"
            )
        if normalized in seen:
            raise CompositionError(
                "lifecycle_membership_duplicate", f"{mission_path}: duplicate slice ref {ref}"
            )
        order = raw.get("order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise CompositionError(
                "lifecycle_order_invalid", f"{mission_path}: {ref} requires an integer order"
            )
        if members and order <= members[-1].order:
            raise CompositionError(
                "lifecycle_order_invalid",
                f"{mission_path}: slice orders must be unique and strictly increasing; "
                f"{ref} has {order} after {members[-1].order}",
            )
        target = mission_dir / normalized
        if not target.exists():
            raise CompositionError(
                "lifecycle_member_missing", f"{mission_path}: {ref} does not exist at {target}"
            )
        if target.is_symlink():
            raise CompositionError(
                "lifecycle_member_symlink", f"{mission_path}: {ref} may not be a symlink"
            )
        target_real = os.path.realpath(target)
        if target_real != mission_real and not target_real.startswith(mission_real + os.sep):
            raise CompositionError(
                "lifecycle_path_escape", f"{mission_path}: {ref} escapes the mission root"
            )
        seen.add(normalized)
        members.append(Member(ref, order, raw.get("active") is not False))
    return members


def read_mission_members(mission_dir: Path) -> list[Member]:
    """Read mission.yaml from disk (never a cached copy) and return its validated members."""
    path = Path(mission_dir) / "mission.yaml"
    _, data = _load_yaml(path)
    return validate_mission_composition(data, path)


def splice_composition_slices(text: str, members: list[Member]) -> str:
    """Return mission.yaml text with only composition.slices replaced by members.

    Bytes before and after the list stay as they were. The list is written in block style the
    way rig's YAML document writer does, or as `[]` when it is empty.
    """
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    _, composition = _mapping_item(root, "composition")
    key_node, value_node = _mapping_item(composition, "slices")
    if key_node is None or not isinstance(value_node, yaml.SequenceNode):
        raise Refused("mission.yaml has no composition.slices list to edit")
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = " " * (key_node.start_mark.column + 2)
    lines = []
    for member in members:
        lines += [
            f"{indent}- ref: {yaml_scalar(member.ref)}",
            f"{indent}  order: {member.order}",
            f"{indent}  active: {'true' if member.active else 'false'}",
        ]
    rendered = newline + newline.join(lines) if lines else " []"
    begin, finish = _value_span(text, key_node, value_node)
    return text[:begin] + rendered + text[finish:]


def _composition_text(path: Path, text: str, data: dict, members: list[Member]) -> str:
    """Return mission.yaml text for members after checking it changes nothing else."""
    result = splice_composition_slices(text, members)
    expected = copy.deepcopy(data)
    expected["composition"]["slices"] = [member._asdict() for member in members]
    parsed = yaml.safe_load(result)
    if parsed != expected:
        raise Refused(f"{path}: could not edit composition.slices without changing other content")
    validate_mission_composition(parsed, path)
    return result


def add_slice_to_mission(mission_dir: Path, slice_dir_name: str) -> Member:
    """Append `slices/<slice_dir_name>/slice.yaml` to the mission's composition and return it.

    The order is the last member's order + 10 (10 for the first), as rig's
    planMissionMembershipAdd does. The new composition is validated, so the slice's slice.yaml
    must exist, and mission.yaml is written compare-and-swap. A ref already present is refused.
    """
    _check_name(slice_dir_name, "slice directory")
    path = Path(mission_dir) / "mission.yaml"
    text, data = _load_yaml(path)
    members = validate_mission_composition(data, path)
    ref = f"slices/{slice_dir_name}/slice.yaml"
    if any(os.path.normpath(m.ref) == os.path.normpath(ref) for m in members):
        raise Refused(f"{path}: composition already contains {ref}")
    member = Member(ref, (members[-1].order if members else 0) + 10, True)
    cas_write(path, text, _composition_text(path, text, data, [*members, member]))
    return member


def remove_slice_from_mission(mission_dir: Path, ref: str) -> None:
    """Remove the composition entry for ref from mission.yaml (compare-and-swap)."""
    path = Path(mission_dir) / "mission.yaml"
    text, data = _load_yaml(path)
    members = validate_mission_composition(data, path)
    kept = [m for m in members if os.path.normpath(m.ref) != os.path.normpath(ref)]
    if len(kept) == len(members):
        raise Refused(f"{path}: composition does not contain {ref}")
    cas_write(path, text, _composition_text(path, text, data, kept))


# ---------------------------------------------------------------------------
# Mission status (R29): mission.yaml metadata.status
# ---------------------------------------------------------------------------


def splice_mission_status(text: str, status: str) -> str:
    """Return mission.yaml text with only the bytes of the `metadata.status` scalar replaced.

    Comments, key order and every other byte are kept, and setting the current status returns
    text unchanged. A status outside MISSION_STATUSES is a UsageError (exit 2). A file without
    a `metadata` mapping holding a `status` scalar is a ParseError (exit 4): the key is never
    inserted.
    """
    if status not in MISSION_STATUSES:
        raise UsageError(f"mission status {status!r} not in {', '.join(MISSION_STATUSES)}")
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseError(f"mission.yaml is not valid YAML: {exc}") from exc
    _, metadata = _mapping_item(root, "metadata")
    key_node, value_node = _mapping_item(metadata, "status")
    if (
        key_node is None
        or not isinstance(value_node, yaml.ScalarNode)
        or value_node.tag == "tag:yaml.org,2002:null"
    ):
        raise ParseError("mission.yaml has no metadata.status to set; HACF never inserts it")
    if value_node.value == status:
        return text
    # The scalar's own bytes (quotes included) end where _value_span says the value ends.
    finish = _value_span(text, key_node, value_node)[1]
    result = text[: value_node.start_mark.index] + status + text[finish:]
    expected = copy.deepcopy(data)
    expected["metadata"]["status"] = status
    if yaml.safe_load(result) != expected:
        raise Refused("could not set metadata.status without changing other content")
    return result


def set_mission_status(mission_dir: Path, status: str) -> str:
    """Set `<mission_dir>/mission.yaml` metadata.status (compare-and-swap); return the status.

    The bytes come from splice_mission_status(); like set_slice_status() it takes no lock, and a
    file changed between the read and the replace is refused (exit 5) with nothing written. The
    current status writes nothing.
    """
    path = Path(mission_dir) / "mission.yaml"
    text, _ = _load_yaml(path)
    result = splice_mission_status(text, status)
    if result != text:
        cas_write(path, text, result)
    return status


# ---------------------------------------------------------------------------
# Dot-IDs (dot-id.ts)
# ---------------------------------------------------------------------------


def _mission_version_ok(segments: list[str]) -> bool:
    """Rig's isValidMissionVerSegments: 2-3 segments; the escape band is exactly 99.0.<n>."""
    if not 2 <= len(segments) <= 3:
        return False
    if segments[0] == "99":
        return len(segments) == 3 and segments[1] == "0"
    return True


def is_mission_dot_id(value: Any) -> bool:
    """Return True for a mission dot-ID such as OPR.0.6.0 or OPR.99.0.2."""
    match = DOT_ID_RE.match(value) if isinstance(value, str) else None
    return bool(match) and _mission_version_ok(match.group(2).split("."))


def is_slice_dot_id(value: Any) -> bool:
    """Return True for a slice dot-ID: a mission dot-ID plus one ordinal (OPR.0.6.0.4)."""
    match = DOT_ID_RE.match(value) if isinstance(value, str) else None
    if not match:
        return False
    segments = match.group(2).split(".")
    return len(segments) >= 3 and _mission_version_ok(segments[:-1])


def _check_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not PREFIX_RE.match(prefix):
        raise UsageError(f"id prefix {prefix!r} must be 2-3 capital letters (default OPR)")
    return prefix


def mission_dot_id(
    mission_name: str, escape_ordinal: int | None = None, prefix: str = DEFAULT_ID_PREFIX
) -> str:
    """Return a mission's dot-ID: release-X.Y[.Z] -> <PFX>.X.Y[.Z]; others <PFX>.99.0.<n>.

    escape_ordinal is the <n> of a non-release mission (see next_escape_band_ordinal).
    """
    _check_prefix(prefix)
    match = RELEASE_NAME_RE.match(mission_name)
    if match:
        return f"{prefix}.{match.group(1)}"
    if (
        isinstance(escape_ordinal, bool)
        or not isinstance(escape_ordinal, int)
        or escape_ordinal < 1
    ):
        raise UsageError(
            f"mission {mission_name!r} is not release-X.Y[.Z]; pass its escape-band ordinal"
        )
    return f"{prefix}.99.0.{escape_ordinal}"


def next_escape_band_ordinal(
    existing_ids: list[str | None], prefix: str = DEFAULT_ID_PREFIX
) -> int:
    """Return max(<n>) + 1 over existing <PFX>.99.0.<n> mission ids, starting at 1."""
    pattern = re.compile(rf"^{re.escape(_check_prefix(prefix))}\.99\.0\.(\d+)$")
    found = [int(m.group(1)) for m in (pattern.match(i or "") for i in existing_ids) if m]
    return max(found, default=0) + 1


def slice_dot_id(mission_id: str, k: int) -> str:
    """Return the dot-ID of slice k of a mission: <mission-id>.<k>."""
    if not is_mission_dot_id(mission_id):
        raise UsageError(f"{mission_id!r} is not a mission dot-ID")
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise UsageError(f"slice ordinal {k!r} must be a positive integer")
    slice_id = f"{mission_id}.{k}"
    if not is_slice_dot_id(slice_id):
        raise UsageError(f"{slice_id} is not a valid slice dot-ID")
    return slice_id


# ---------------------------------------------------------------------------
# Status (hacf_status -> OpenRig), proof checks, supersede
# ---------------------------------------------------------------------------


def openrig_status(hacf_status: str) -> str:
    """Return the OpenRig status rig reads for a slice's hacf_status."""
    try:
        return STATUS_MAP[hacf_status]
    except KeyError:
        raise UsageError(
            f"unknown hacf_status {hacf_status!r}; expected one of {', '.join(STATUS_MAP)}"
        ) from None


def c1_problems(text: str) -> list[str]:
    """Return what is wrong with a proof artifact's C1 header ([] when it is valid)."""
    try:
        header = split_frontmatter(text)[2]
    except OpenRigError as exc:
        return [str(exc)]
    problems = [
        f"missing field {field}"
        for field in C1_REQUIRED_FIELDS
        if not isinstance(header.get(field), str) or not header[field].strip()
    ]
    if isinstance(header.get("artifact_type"), str):
        if header["artifact_type"] not in C1_ARTIFACT_TYPES:
            problems.append(f"artifact_type {header['artifact_type']!r} not in the closed set")
    if isinstance(header.get("verdict"), str) and header["verdict"] not in C1_VERDICTS:
        problems.append(f"verdict {header['verdict']!r} not in the closed set")
    return problems


def has_c1_artifact(slice_dir: Path) -> bool:
    """Return True when the slice's proof/ holds a Markdown artifact with a valid C1 header."""
    proof = Path(slice_dir) / "proof"
    if not proof.is_dir():
        return False
    return any(
        entry.suffix.lower() == ".md" and entry.is_file() and not c1_problems(_read_text(entry))
        for entry in proof.iterdir()
    )


def set_slice_status(slice_dir: Path, hacf_status: str) -> str:
    """Write the OpenRig status for hacf_status into the slice's SPEC.md; return that status.

    `audited` (done) needs PROOF.md and a C1 artifact in proof/, or rig's scope audit raises
    missing_proof. `superseded` goes through close_superseded(), which also moves the slice.
    slice.yaml is never written.
    """
    status = openrig_status(hacf_status)
    slice_dir = Path(slice_dir)
    if hacf_status == "superseded":
        raise UsageError("a superseded slice is closed with close_superseded(), which moves it")
    if hacf_status == "audited":
        if not (slice_dir / "PROOF.md").is_file():
            raise Refused(f"{slice_dir}: status done needs PROOF.md")
        if not has_c1_artifact(slice_dir):
            raise Refused(f"{slice_dir}: status done needs a C1 proof artifact in proof/")
    spec = slice_dir / "SPEC.md"
    text = _read_text(spec)
    cas_write(spec, text, splice_frontmatter(text, {"status": status}))
    return status


def _today() -> str:
    """Return today's UTC date (YYYY-MM-DD), the form rig writes into closed-on."""
    return _runtime.utc_now_iso()[:10]


def _is_live(status: Any) -> bool:
    """Rig's active filter (scope-fs.ts): not done, not closed*, not shipped*."""
    text = str(status or "").lower()
    return text != "done" and not text.startswith(("closed", "shipped"))


def _id_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def slice_dependencies(slice_dir: Path) -> tuple[list[str], str]:
    """Return a slice's dependency dot-IDs and their source file.

    slice.yaml execution.depends_on wins when present (HACF reads it and never writes it);
    otherwise the SPEC.md frontmatter depends_on.
    """
    slice_dir = Path(slice_dir)
    manifest = slice_dir / "slice.yaml"
    if manifest.is_file():
        _, data = _load_yaml(manifest)
        execution = data.get("execution") if isinstance(data, dict) else None
        if isinstance(execution, dict) and "depends_on" in execution:
            return _id_list(execution["depends_on"]), "slice.yaml"
    return _id_list(read_frontmatter(slice_dir / "SPEC.md").get("depends_on")), "SPEC.md"


def close_superseded(
    mission_dir: Path,
    slice_name: str,
    *,
    rewrite_dependents_to: str | None = None,
    today: str | None = None,
) -> Path:
    """Close a superseded slice the way `rig scope slice close --reason superseded` does.

    The slice dir moves to <mission>/closed/<NN-slug>, its composition entry is removed, and its
    SPEC.md gets status closed-superseded plus closed-on. Live slices that depend on it are
    refused unless rewrite_dependents_to names the slice dot-ID to repoint their SPEC.md
    depends_on to; a dependency declared in slice.yaml execution.depends_on is always refused,
    because HACF never writes slice.yaml. Every change is planned before the first write, and a
    failure part-way puts back what was written. Returns the closed slice dir.
    """
    mission_dir = Path(mission_dir)
    _check_name(slice_name, "slice directory")
    slice_dir = mission_dir / "slices" / slice_name
    dest = mission_dir / "closed" / slice_name
    if slice_dir.is_symlink() or not slice_dir.is_dir():
        raise Refused(f"{slice_dir} is not a slice directory")
    if os.path.lexists(dest):
        raise Refused(f"{dest} already exists")
    spec_text = _read_text(slice_dir / "SPEC.md")
    slice_id = split_frontmatter(spec_text)[2].get("id")
    path = mission_dir / "mission.yaml"
    text, data = _load_yaml(path)
    members = validate_mission_composition(data, path)
    ref = os.path.normpath(f"slices/{slice_name}/slice.yaml")
    kept = [m for m in members if os.path.normpath(m.ref) != ref]
    if len(kept) == len(members):
        raise Refused(f"{path}: composition does not contain slices/{slice_name}/slice.yaml")

    dependents = []
    for member in kept:
        other = (mission_dir / member.ref).parent
        if not _is_live(read_frontmatter(other / "SPEC.md").get("status")):
            continue
        deps, source = slice_dependencies(other)
        if isinstance(slice_id, str) and slice_id in deps:
            dependents.append((other, deps, source))
    rewrites = []
    if dependents:
        names = ", ".join(other.name for other, _, _ in dependents)
        if rewrite_dependents_to is None:
            raise Refused(
                f"live slices depend on {slice_id}: {names}; pass rewrite_dependents_to "
                "(--rewrite-dependents-to <slice id>) to repoint them"
            )
        if not is_slice_dot_id(rewrite_dependents_to) or rewrite_dependents_to == slice_id:
            raise UsageError(f"{rewrite_dependents_to!r} is not another slice dot-ID")
        for other, deps, source in dependents:
            if source == "slice.yaml":
                raise Refused(
                    f"{other / 'slice.yaml'} execution.depends_on names {slice_id}; HACF never "
                    "writes slice.yaml, so repoint it there first"
                )
            new_deps = list(
                dict.fromkeys(rewrite_dependents_to if d == slice_id else d for d in deps)
            )
            old = _read_text(other / "SPEC.md")
            rewrites.append(
                (other / "SPEC.md", old, splice_frontmatter(old, {"depends_on": new_deps}))
            )

    manifest_text = _composition_text(path, text, data, kept)
    closed = {"status": STATUS_MAP["superseded"], "closed-on": today or _today()}
    closed_text = splice_frontmatter(spec_text, closed)

    undo: list[Callable[[], None]] = []
    try:
        cas_write(path, text, manifest_text)
        undo.append(lambda: _restore_file(path, text))
        dest.parent.mkdir(exist_ok=True)
        os.rename(slice_dir, dest)
        undo.append(lambda: os.rename(dest, slice_dir))
        cas_write(dest / "SPEC.md", spec_text, closed_text)
        undo.append(lambda: _restore_file(dest / "SPEC.md", spec_text))
        for spec, old, new in rewrites:
            cas_write(spec, old, new)
            undo.append(lambda spec=spec, old=old: _restore_file(spec, old))
    except BaseException:
        for step in reversed(undo):
            with contextlib.suppress(OSError):
                step()
        raise
    return dest


# ---------------------------------------------------------------------------
# rig resolution and the version rule (R22.7)
# ---------------------------------------------------------------------------


def resolve_rig(*, allow_download: bool = False) -> list[str] | None:
    """Return the argv prefix that runs rig, or None.

    `rig` on PATH wins. Otherwise the npx prefix is returned only when the caller passes
    allow_download=True, which must stand for the user's explicit consent: `-y` only stops npx
    from asking; it is not consent. Without consent nothing is ever downloaded.
    """
    found = shutil.which("rig")
    if found:
        return [found]
    if allow_download and shutil.which("npx"):
        return list(NPX_PREFIX)
    return None


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """Return the leading semver of `rig --version` output ("0.6.13 (9eeb2e42)" -> (0, 6, 13))."""
    match = VERSION_RE.match(text or "")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _in_range(found: tuple[int, int, int] | None, contract: Contract) -> bool:
    """True when a parsed version lies inside one of the contract's probed ranges."""
    return found is not None and any(
        found[:2] == r[:2] and found[2] >= r[2] for r in contract.range
    )


def version_decision(
    version_text: str | None,
    contract: Contract | None = None,
    probe_result: str | None = None,
    *,
    probe_note: str | None = None,
) -> tuple[str, str | None]:
    """Return (mode, warning) for a `rig --version` output under the version rule.

    The accepted set and the range come from `contract` (default: load_contract(), the file
    .agents/config/openrig_contract.yml). delegate: rig is a listed version, or an unlisted version
    inside a probed range whose probe_result is "pass" (a fresh, key-matching cache entry), so HACF
    calls it. native: an in-range version with any other probe_result (None or "pending": no
    result yet; "off"; "unwritable"; "fail", "timeout", "error"; probe_note names the check and the
    evidence file), or another version with a listed major.minor; HACF does not call it and writes
    the v0alpha1 manifests and C1 artifacts itself. stop: any other major.minor (or an unreadable
    version); HACF never calls it until the version is added to the contract file after the
    contract test passes on it.
    """
    if contract is None:
        contract = load_contract()
    listed = contract.versions
    accepted = [parse_version(v) for v in listed]
    found = parse_version(version_text)
    shown = (version_text or "").strip() or "(no output)"
    named = " or ".join(listed)
    if found in accepted:
        return "delegate", None
    if _in_range(found, contract):
        if probe_result == "pass":
            return "delegate", None
        if probe_result in (None, "pending"):
            state = (
                "probe pending: no probe result for this rig yet, and this call does not probe "
                "(`python3 .agents/scripts/hyper_openrig.py contract probe` runs it)"
            )
        elif probe_result == "off":
            state = f"probe off ({probe_note or 'disabled'})"
        elif probe_result == "unwritable":
            state = f"probe off ({probe_note or 'the probe cache is not writable'})"
        else:
            state = f"probe {probe_result}" + (f" ({probe_note})" if probe_note else "")
        return "native", (
            f"rig {shown} is inside the probed range but not an accepted version (rig {named}); "
            f"{state}, so HACF does not call it and writes the v0alpha1 manifests and C1 "
            "artifacts itself"
        )
    if found is not None and any(found[:2] == v[:2] for v in accepted if v is not None):
        return "native", (
            f"rig {shown} is not an accepted version (rig {named}); HACF does not call it and "
            "writes the v0alpha1 manifests and C1 artifacts itself"
        )
    return "stop", (
        f"rig {shown} is outside the pinned contract (rig {named}); HACF never calls it. "
        f"A new rig version is accepted by adding it to {CONTRACT_REL} in a HACF release after "
        "dev/tests/contract/test_openrig_live.py passes on it"
    )


class RigPlan(NamedTuple):
    """How HACF uses rig: mode delegate, native, stop or absent, with the prefix to call.

    probe is the range probe's state for an unlisted in-range version (pass, fail, timeout,
    error, pending, off, unwritable), else None; via_probe is True when the delegate was earned
    by a probe (R10 drops that cache entry on a delegate-mode error).
    """

    mode: str
    prefix: list[str] | None
    version: str | None
    warning: str | None
    probe: str | None = None
    via_probe: bool = False

    @property
    def use_rig(self) -> bool:
        """True only for an accepted rig (mode delegate)."""
        return self.mode == "delegate"


def plan_rig(
    *, allow_download: bool = False, env: dict[str, str] | None = None, top: Path | None = None
) -> RigPlan:
    """Resolve rig, read `rig --version` and apply the version rule.

    For an unlisted in-range version the probe result comes from the cache only: plan_rig never
    starts a probe (prepare_rig() does, outside spec_lock). With no rig (and no download consent)
    the plan is `absent`: HACF writes natively, silently.
    """
    prefix = resolve_rig(allow_download=allow_download)
    if prefix is None:
        return RigPlan("absent", None, None, None)
    version = _take_version(prefix) if env is None else _NO_HANDOFF
    if version is _NO_HANDOFF:
        proc = run_rig([*prefix, "--version"], env=env)
        version = proc.stdout.strip() if proc.returncode == 0 else None
    contract = load_contract()
    try:
        contract, probe, note = _probe_state(prefix, version, contract, top)
    except Exception as exc:  # noqa: BLE001 - a cache doubt is native, never a crash
        probe, note = "error", f"the probe cache could not be read: {type(exc).__name__}: {exc}"
    mode, warning = version_decision(version, contract, probe, probe_note=note)
    via_probe = mode == "delegate" and probe == "pass"
    return RigPlan(mode, prefix if mode == "delegate" else None, version, warning, probe, via_probe)


# ---------------------------------------------------------------------------
# The range probe (release-0.6.2 R4-R11): an unlisted rig inside the contract's range is called
# only after a probe of exactly the surface HACF delegates (`scope audit`, `slice ls`, `proof add`
# over HACF-rendered bytes) passed. The probe runs isolated from the daemon and the seat's
# identity, in a throwaway repo under $TMPDIR, and its result is cached machine-wide by content.
# Every doubt (no result, a failed check, a timeout, a crash, an unreadable file, a cache error)
# is native plus a warning, never an exception into the caller.
#
# What the probe cannot cover (slice 5's docs): daemon paths, rules only real content triggers,
# a same-shape semantic change, rig sources HACF does not exercise, real-scale performance, and
# an in-place package edit that keeps the binary's mtime and size.
# ---------------------------------------------------------------------------

PROBE_EXPECT_REL = ".agents/config/openrig_probe_expect.json"
PROBE_EXPECT_PATH = Path(__file__).resolve().parents[1] / "config" / "openrig_probe_expect.json"
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "openrig_contract.yml"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "schemas" / "openrig"
LOCAL_CONFIG_REL = ".agents/local/hacf.yml"
# R7: min(60, max(20, ceil(3 x slowest of five probes))) seconds, measured on this host
# (missions/release-0.6.2/slices/02-range-probe/proof/02-probe.md).
PROBE_TIMEOUT = 20
PROBE_PASS_TTL = 7 * 24 * 3600
PROBE_OTHER_TTL = 24 * 3600
PROBE_OUTCOMES = ("pass", "fail", "timeout", "error")
PROBE_ENTRY_SCHEMA = 1
PROBE_EXPECT_KEYS = (
    "rig_version",
    "probe_schema",
    "generator",
    "exit_codes",
    "write_set",
    "volatile_keys",
    "c1_header",
    "json_key_paths",
    "help_flags",
)
PROBE_STEPS = (
    "version",
    "scope_audit",
    "slice_ls",
    "proof_add",
    "scope_audit_after",
    "help_scope_audit",
    "help_slice_ls",
    "help_proof_add",
)
PROBE_JSON_STEPS = ("scope_audit", "slice_ls", "scope_audit_after")
PROBE_HELP_STEPS = ("help_scope_audit", "help_slice_ls", "help_proof_add")
# The JSON key paths HACF's checks read (dev/tests/contract/test_openrig_live.py, verify's R10
# check): the expectation file holds these, and a rig whose JSON has more passes.
_AUDIT_READ = (
    "ok",
    "mission",
    "mission.findings",
    "mission.findings[].kind",
    "mission.findings[].severity",
    "slices",
    "slices[].findings",
    "slices[].name",
)
PROBE_READ_PATHS = {
    "scope_audit": _AUDIT_READ,
    "slice_ls": ("ok", "slices", "slices[].mission", "slices[].name", "slices[].status"),
    "scope_audit_after": _AUDIT_READ,
}
PROBE_DEAD_URL = "http://127.0.0.1:9"  # the discard port: nothing listens, no connection succeeds
PROBE_VOLATILE = "<volatile>"
PROBE_MISSION = "release-9.9"
PROBE_ACTIVE = "01-probe-active"
PROBE_CLOSED = "02-probe-superseded"
PROBE_PROOF_NAME = "01-probe.md"
_PROBE_DATE = "2026-01-01"
_PROBE_AT = "2026-01-01T00:00:00Z"
_PROBE_GIT_DATE = "2026-01-01T00:00:00+00:00"
_TEMPLATE_NOTICE_RE = re.compile(r"\A<!-- HACF template [^\n]*-->\r?\n")
_TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")
_PROBE_SLICE_BODY = """\
# MiniPRD: {title}

## Intent

{intent}

## 1. The Confidence Mandate

Confidence 9/10: the probe slice is fixed text.

## Mini-requirements

1. US-001: the {title} slice reads back the way HACF wrote it.

## 3. Implementation Plan (Task List)

- [ ] Task 1: render the slice with HACF's templates.

## 4. The Negative Space (Constraints)

- **DO NOT** change a rendered byte.

## Proof contract

- [ ] proof/{proof} carries a C1 header.

## Source material

- .agents/scripts/hyper_openrig.py (run_probe)

## Intent visual

N/A (non-visual slice).

## Status

- compiled

## Dependencies

- None.
"""

_warned: set[str] = set()


def _warn_once(tag: str, text: str) -> None:
    """Print one hyper_openrig warning per tag per process."""
    if tag not in _warned:
        _warned.add(tag)
        print(f"hyper_openrig.py: warning: {text}", file=sys.stderr)


def _clock() -> float:
    """The wall clock the cache TTLs use (tests replace it)."""
    return time.time()


class ProbeResult(NamedTuple):
    """One probe run: outcome pass, fail, timeout or error; detail names what differed.

    evidence holds the argv, exit codes, timings and failures (written beside the cache entry);
    observed holds what record mode derives an expectation file from.
    """

    outcome: str
    detail: str
    evidence: dict
    observed: dict


class _ProbeError(Exception):
    """An error outcome: the probe could not run (unreadable expectations, unwritable temp)."""


class _ProbeTimeout(Exception):
    """The whole probe ran past PROBE_TIMEOUT."""


def range_probe_setting(top: Path | None = None) -> tuple[str, str | None]:
    """Return (state, reason) for `<top>/.agents/local/hacf.yml` `openrig.range_probe` (R9).

    top defaults to the project this script is installed in. state is "allowed" (missing file or
    key, or true), "disabled" (the boolean false: the range is ignored, the probe never runs) or
    "off" (an unreadable or malformed file, over 64 KiB, or a non-boolean value: fail closed,
    with one warning per process). Read with yaml.safe_load here, not through hyper_engine.
    """
    top = Path(top) if top is not None else Path(__file__).resolve().parents[2]
    path = top / LOCAL_CONFIG_REL
    try:
        with open(path, "rb") as handle:
            raw = handle.read(CONTRACT_MAX_BYTES + 1)
    except FileNotFoundError:
        return "allowed", None
    except OSError as exc:
        reason = f"cannot read: {exc.strerror or exc}"
    else:
        reason = None
        if len(raw) > CONTRACT_MAX_BYTES:
            reason = f"larger than {CONTRACT_MAX_BYTES} bytes"
        elif yaml is None:
            reason = "PyYAML is not available"
        else:
            try:
                data = yaml.safe_load(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - any parse doubt is "off"
                first = (str(exc).splitlines() or [type(exc).__name__])[0]
                reason = f"cannot parse: {first}"[:300]
            else:
                section = data.get("openrig") if isinstance(data, dict) else None
                if data is not None and not isinstance(data, dict):
                    reason = "not a mapping"
                elif section is None:
                    return "allowed", None
                elif not isinstance(section, dict):
                    reason = "openrig is not a mapping"
                elif "range_probe" not in section or section["range_probe"] is True:
                    return "allowed", None
                elif section["range_probe"] is False:
                    return "disabled", None
                else:
                    reason = f"openrig.range_probe is {section['range_probe']!r}, not a boolean"
    _warn_once("optout", f"{path}: {reason}; the OpenRig range probe is off (fail closed)")
    return "off", reason


def probe_env(base: dict[str, str], tmp: Path) -> dict[str, str]:
    """Return the probe's environment (R6), built from base.

    Every OPENRIG_* and RIGGED_* variable is dropped (and every GIT_* one, so no hook's
    GIT_DIR or GIT_INDEX_FILE can point the throwaway repo's git at a real one); OPENRIG_URL is
    the dead discard port; rig's home and Node's compile cache live under tmp; git reads no user
    or system config and commits under a fixed author. PATH and HOME stay.
    """
    env = {
        key: value
        for key, value in base.items()
        if not key.startswith(("OPENRIG_", "RIGGED_", "GIT_"))
    }
    env.update(
        OPENRIG_URL=PROBE_DEAD_URL,
        OPENRIG_HOME=str(Path(tmp) / "home"),
        NODE_COMPILE_CACHE=str(Path(tmp) / "node-cache"),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CEILING_DIRECTORIES=str(tmp),
        GIT_AUTHOR_NAME="HACF Probe",
        GIT_AUTHOR_EMAIL="probe@hacf.invalid",
        GIT_AUTHOR_DATE=_PROBE_GIT_DATE,
        GIT_COMMITTER_NAME="HACF Probe",
        GIT_COMMITTER_EMAIL="probe@hacf.invalid",
        GIT_COMMITTER_DATE=_PROBE_GIT_DATE,
    )
    return env


# --- The probe tree: HACF's own renderers and templates ------------------------------------------


def _probe_template(name: str, values: dict[str, str]) -> str:
    """Fill one .agents/schemas/openrig template the way hyper_scope.render does."""
    text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    text = _TEMPLATE_NOTICE_RE.sub("", text, count=1)

    def value(match: re.Match) -> str:
        if match.group(1) not in values:
            raise _ProbeError(f"template {name}: no value for {match.group(1)}")
        return values[match.group(1)]

    return _TOKEN_RE.sub(value, text)


def _probe_slice_files(mission_id: str, k: int, name: str, title: str) -> dict[str, str]:
    """Return one slice's files (slice.yaml, SPEC.md, PROGRESS.md, PROOF.md) as HACF writes them."""
    slice_id = slice_dot_id(mission_id, k)
    intent = f"The {title} slice of the HACF range probe."
    spec = _probe_template(
        "slice_spec.md",
        {
            "id_yaml": flow_value(slice_id),
            "slice_yaml": flow_value(name),
            "mission_yaml": flow_value(PROBE_MISSION),
            "verified_yaml": flow_value(f"{_PROBE_DATE} against scaffold"),
            "created_yaml": flow_value(_PROBE_DATE),
            "intent_yaml": flow_value(intent),
            "depends_on_yaml": flow_value([]),
            "node_id_yaml": flow_value(None),
            "parent_yaml": flow_value(None),
            "touches_yaml": flow_value([]),
            "oracle_yaml": flow_value([]),
            "legacy_source_yaml": flow_value(None),
            "legacy_order_yaml": flow_value(None),
            "title": title,
            "intent": intent,
            "dependencies": "- None.",
        },
    )
    head = FRONTMATTER_RE.match(spec)
    if head is None:
        raise _ProbeError("the slice template has no frontmatter")
    body = _PROBE_SLICE_BODY.format(title=title, intent=intent, proof=PROBE_PROOF_NAME)
    outcomes = f"- {_PROBE_AT} compiled\n- {_PROBE_AT} executing - base 0000000\n"
    return {
        "slice.yaml": render_slice_yaml(),
        "SPEC.md": spec[: head.end()] + "\n\n" + body,
        "PROGRESS.md": _probe_template("slice_progress.md", {"slice": slice_id, "at": _PROBE_AT,
                                                             "outcome": outcomes}),
        "PROOF.md": _probe_template("proof.md", {"id": slice_id, "title": title}),
    }  # fmt: skip


def _probe_tree(ws: Path) -> None:
    """Write the probe workspace: a project, one mission, an active and a superseded slice.

    Every byte comes from HACF's renderers (render_*_yaml, add_slice_to_mission,
    splice_mission_status, close_superseded) and its SPEC/PROGRESS/PROOF templates.
    """
    mission_id = mission_dot_id(PROBE_MISSION)
    files = {
        "project.yaml": render_project_yaml(),
        "workspace.yaml": render_workspace_yaml(),
        "SPEC.md": _probe_template(
            "project_spec.md",
            {"intent_yaml": flow_value("The HACF range probe project."),
             "date_yaml": flow_value(_PROBE_DATE), "name": "probe",
             "intent": "The HACF range probe project.",
             "missions_index": f"- [{PROBE_MISSION}](missions/{PROBE_MISSION}/SPEC.md)"},
        ),
    }  # fmt: skip
    mission = f"missions/{PROBE_MISSION}"
    files[f"{mission}/mission.yaml"] = splice_mission_status(
        render_mission_yaml(PROBE_MISSION), "active"
    )
    files[f"{mission}/SPEC.md"] = _probe_template(
        "mission_spec.md",
        {"id_yaml": flow_value(mission_id), "mission_yaml": flow_value(PROBE_MISSION),
         "verified_yaml": flow_value(f"{_PROBE_DATE} against scaffold"),
         "created_yaml": flow_value(_PROBE_DATE), "intent_yaml": flow_value("Probe rig."),
         "lore_refs_yaml": flow_value([]), "hacf_source_yaml": flow_value(None),
         "title": "Probe", "intent": "Probe rig.",
         "slices": f"- {PROBE_ACTIVE}\n- {PROBE_CLOSED}"},
    )  # fmt: skip
    files[f"{mission}/PROGRESS.md"] = _probe_template(
        "mission_progress.md",
        {"mission": mission_id,
         "slice_table": "| Slice | Status |\n|---|---|\n| 01-probe-active | executing |"},
    )  # fmt: skip
    for k, name, title in ((1, PROBE_ACTIVE, "active"), (2, PROBE_CLOSED, "superseded")):
        for rel, text in _probe_slice_files(mission_id, k, name, title).items():
            files[f"{mission}/slices/{name}/{rel}"] = text
    for rel, text in files.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for name in (PROBE_ACTIVE, PROBE_CLOSED):
        add_slice_to_mission(ws / mission, name)
    close_superseded(ws / mission, PROBE_CLOSED, today=_PROBE_DATE)


def _snapshot(ws: Path) -> dict[str, str]:
    """Return {relative path: sha256} for every file under ws outside .git/."""
    found = {}
    for path in sorted(ws.rglob("*")):
        rel = path.relative_to(ws).as_posix()
        if rel == ".git" or rel.startswith(".git/") or not path.is_file() or path.is_symlink():
            continue
        found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def json_key_paths(value: Any, prefix: str = "") -> set[str]:
    """Return every key path in a JSON value: `a.b` for objects, `a[]` for list items."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= json_key_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            paths |= json_key_paths(item, f"{prefix}[]")
    return paths


def _has_flag(text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


def load_probe_expect(path: Path | None = None) -> dict:
    """Return the expectation file (R11); raise _ProbeError when it is unreadable or invalid."""
    path = Path(path) if path is not None else PROBE_EXPECT_PATH
    try:
        with open(path, "rb") as handle:
            raw = handle.read(CONTRACT_MAX_BYTES + 1)
    except OSError as exc:
        raise _ProbeError(f"{path}: cannot read: {exc.strerror or exc}") from None
    if len(raw) > CONTRACT_MAX_BYTES:
        raise _ProbeError(f"{path}: larger than {CONTRACT_MAX_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _ProbeError(f"{path}: not JSON: {exc}") from None
    problems = []
    if not isinstance(data, dict) or set(data) != set(PROBE_EXPECT_KEYS):
        raise _ProbeError(f"{path}: keys must be exactly {', '.join(PROBE_EXPECT_KEYS)}")
    if not isinstance(data["rig_version"], str) or not isinstance(data["generator"], str):
        problems.append("rig_version and generator must be strings")
    if type(data["probe_schema"]) is not int:
        problems.append("probe_schema must be an integer")
    codes = data["exit_codes"]
    if not isinstance(codes, dict) or set(codes) != set(PROBE_STEPS):
        problems.append(f"exit_codes must name exactly {', '.join(PROBE_STEPS)}")
    elif not all(type(c) is int for c in codes.values()):
        problems.append("exit_codes must be integers")
    for key in ("write_set", "volatile_keys"):
        if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
            problems.append(f"{key} must be a list of strings")
    header = data["c1_header"]
    if not isinstance(header, dict) or not all(isinstance(v, str) for v in header.values()):
        problems.append("c1_header must map keys to strings")
    for key, steps in (("json_key_paths", PROBE_JSON_STEPS), ("help_flags", PROBE_HELP_STEPS)):
        value = data[key]
        if not isinstance(value, dict) or set(value) != set(steps):
            problems.append(f"{key} must name exactly {', '.join(steps)}")
        elif not all(
            isinstance(v, list) and all(isinstance(x, str) for x in v) for v in value.values()
        ):
            problems.append(f"{key} values must be lists of strings")
    if problems:
        raise _ProbeError(f"{path}: {'; '.join(problems)}")
    return data


def _probe_calls(prefix: list[str], ws: Path, slice_dir: Path, body: Path, head: str) -> dict:
    """Return {step: argv} for every rig call the probe makes, in PROBE_STEPS order."""
    audit = argv_scope_audit(prefix, ws, PROBE_MISSION)
    proof = argv_proof_add(
        prefix,
        ws,
        slice_dir.resolve(),
        artifact_type="guard",
        verdict="PASS",
        candidate_sha=head,
        money_evidence="the HACF range probe",
        file=body.resolve(),
        name=PROBE_PROOF_NAME,
    )
    return {
        "version": [*prefix, "--version"],
        "scope_audit": audit,
        "slice_ls": argv_slice_ls(prefix, ws, mission=PROBE_MISSION, state="all"),
        "proof_add": proof,
        "scope_audit_after": list(audit),
        "help_scope_audit": [*prefix, "scope", "audit", "--help"],
        "help_slice_ls": [*prefix, "scope", "slice", "ls", "--help"],
        "help_proof_add": [*prefix, "proof", "add", "--help"],
    }


def _hacf_flags(calls: dict) -> dict[str, list[str]]:
    """Return the flags HACF passes in the call each help text documents."""
    source = {"help_scope_audit": "scope_audit", "help_slice_ls": "slice_ls",
              "help_proof_add": "proof_add"}  # fmt: skip
    return {
        step: [a for a in calls[source[step]] if a.startswith("--")] for step in PROBE_HELP_STEPS
    }


def _c1_header(path: Path, volatile: list[str]) -> dict[str, str] | None:
    """Return a written artifact's header with the volatile values replaced, or None."""
    try:
        header = split_frontmatter(path.read_text(encoding="utf-8"))[2]
    except (OSError, UnicodeDecodeError, OpenRigError):
        return None
    return {
        str(k): PROBE_VOLATILE if k in volatile else ("" if v is None else str(v))
        for k, v in header.items()
    }


def run_probe(
    prefix: list[str],
    version_text: str,
    *,
    record: bool = False,
    env: dict[str, str] | None = None,
    expect_path: Path | None = None,
    timeout: float | None = None,
) -> ProbeResult:
    """Probe the rig at prefix (R5) and return its outcome; never raises.

    In a tempfile.mkdtemp() under $TMPDIR (removed afterwards): `rig --version`, a committed
    `git init` repo rendered by HACF, `scope audit --json`, `slice ls --state all --json`,
    `proof add` with the argv write_proof_artifact builds on the active slice, a final
    `scope audit --json` and `--help` for the three commands. Each call gets the time left of
    the probe's total (PROBE_TIMEOUT). Outcomes: pass; fail (a check differed, named in detail);
    timeout; error (a crash, an unreadable or invalid expectation file, an unwritable temp dir).
    With record=True nothing is checked: observed carries what an expectation file is made of.
    """
    total = PROBE_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + total
    evidence: dict[str, Any] = {"rig": list(prefix), "version": version_text, "argv": {},
                                "exit_codes": {}, "timings": {}, "failures": []}  # fmt: skip
    observed: dict[str, Any] = {}
    tmp: Path | None = None
    try:
        expect = None if record else load_probe_expect(expect_path)
        try:
            tmp = Path(tempfile.mkdtemp(prefix="hacf-rig-probe-"))
        except OSError as exc:
            raise _ProbeError(f"cannot create a temp dir: {exc.strerror or exc}") from None
        penv = probe_env(dict(os.environ if env is None else env), tmp)
        ws = tmp / "ws"
        ws.mkdir()
        _probe_tree(ws)
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "probe"]):
            git = _run(["git", *args], cwd=ws, env=penv, timeout=GIT_TIMEOUT)
            if git.returncode != 0:
                raise _ProbeError(f"git {args[0]} failed: {git.stderr.strip()[:200]}")
        head = _run(["git", "rev-parse", "HEAD"], cwd=ws, env=penv, timeout=GIT_TIMEOUT)
        head_sha = head.stdout.strip()
        body = tmp / "body.md"
        body.write_text("The HACF range probe wrote this artifact.\n", encoding="utf-8")
        slice_dir = ws / "missions" / PROBE_MISSION / "slices" / PROBE_ACTIVE
        before = _snapshot(ws)
        calls = _probe_calls(prefix, ws, slice_dir, body, head_sha)
        outputs: dict[str, Completed] = {}
        for step, argv in calls.items():
            left = deadline - time.monotonic()
            if left <= 0:
                raise _ProbeTimeout(f"the probe ran past {total} s before {step}")
            began = time.monotonic()
            proc = _run(argv, cwd=ws, env=penv, timeout=left)
            evidence["timings"][step] = round(time.monotonic() - began, 3)
            evidence["argv"][step] = argv[len(prefix) :]
            evidence["exit_codes"][step] = proc.returncode
            if proc.returncode == 124 and proc.stderr.endswith(f"timed out after {left} seconds"):
                raise _ProbeTimeout(f"{step} ran past the probe's {total} s")
            outputs[step] = proc
        after = _snapshot(ws)
        write_set = sorted(
            rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)
        )
        rewritten = sorted(rel for rel in before if after.get(rel) != before[rel])
        paths: dict[str, list[str]] = {}
        for step in PROBE_JSON_STEPS:
            try:
                paths[step] = sorted(json_key_paths(json.loads(outputs[step].stdout)))
            except ValueError:
                paths[step] = None  # type: ignore[assignment]
        flags = _hacf_flags(calls)
        artifact = slice_dir / "proof" / PROBE_PROOF_NAME
        observed = {
            "exit_codes": dict(evidence["exit_codes"]),
            "write_set": write_set,
            "rewritten": rewritten,
            "c1_header": _c1_header(artifact, []),
            "json_key_paths": paths,
            "help_flags": {
                step: [f for f in flags[step] if _has_flag(outputs[step].stdout, f)]
                for step in PROBE_HELP_STEPS
            },
            "version_output": outputs["version"].stdout.strip(),
        }
        if record:
            return ProbeResult("pass", "recorded", evidence, observed)
        failures = _probe_failures(expect, observed, outputs, flags, artifact, version_text)
        evidence["failures"] = failures
        if failures:
            return ProbeResult("fail", "; ".join(failures[:3]), evidence, observed)
        return ProbeResult("pass", "every check matched", evidence, observed)
    except _ProbeTimeout as exc:
        evidence["failures"] = [str(exc)]
        return ProbeResult("timeout", str(exc), evidence, observed)
    except _ProbeError as exc:
        evidence["failures"] = [str(exc)]
        return ProbeResult("error", str(exc), evidence, observed)
    except Exception as exc:  # noqa: BLE001 - a crash is an error outcome, never an exception
        text = f"{type(exc).__name__}: {exc}"[:300]
        evidence["failures"] = [text]
        return ProbeResult("error", text, evidence, observed)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _probe_failures(
    expect: dict,
    observed: dict,
    outputs: dict[str, Completed],
    flags: dict[str, list[str]],
    artifact: Path,
    version_text: str,
) -> list[str]:
    """Return every check that differs from the expectation file (R5), named."""
    failures = []
    if observed["version_output"] != version_text.strip():
        failures.append(f"version: rig --version printed {observed['version_output']!r}")
    for step in PROBE_STEPS:
        want, got = expect["exit_codes"][step], observed["exit_codes"][step]
        if want != got:
            failures.append(f"exit code: {step} exited {got}, expected {want}")
    if observed["write_set"] != sorted(expect["write_set"]):
        failures.append(
            f"write set: {', '.join(observed['write_set']) or '(nothing)'}, expected "
            f"{', '.join(sorted(expect['write_set'])) or '(nothing)'}"
        )
    for rel in observed["rewritten"]:
        failures.append(f"rendered bytes: rig rewrote {rel}")
    header = _c1_header(artifact, expect["volatile_keys"])
    if header is None:
        failures.append(f"c1 header: no readable proof/{PROBE_PROOF_NAME}")
    else:
        for key in sorted(set(header) | set(expect["c1_header"])):
            if header.get(key) != expect["c1_header"].get(key):
                failures.append(
                    f"c1 header: {key} is {header.get(key)!r}, expected "
                    f"{expect['c1_header'].get(key)!r}"
                )
    for step in PROBE_JSON_STEPS:
        got = observed["json_key_paths"][step]
        if got is None:
            failures.append(f"json: {step} printed no JSON")
            continue
        missing = sorted(set(expect["json_key_paths"][step]) - set(got))
        if missing:
            failures.append(f"json: {step} lacks {', '.join(missing[:5])}")
    for step in PROBE_HELP_STEPS:
        text = outputs[step].stdout
        missing = [f for f in expect["help_flags"][step] if not _has_flag(text, f)]
        if missing:
            failures.append(f"help: {step} lacks {', '.join(missing)}")
    return failures


# --- The cache (R8) ------------------------------------------------------------------------------


def probe_cache_dir() -> Path:
    """Return ${XDG_CACHE_HOME:-~/.cache}/hacf/rig-probe (a relative XDG_CACHE_HOME is ignored)."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".cache"
    return root / "hacf" / "rig-probe"


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def probe_key_material(rig_path: str, version_text: str, contract: Contract) -> dict:
    """Return the cache key material: rig's output, binary and HACF's own probe inputs (R8).

    Content, not provenance: the same rig binary and the same HACF files give the same key in
    every tree. Raises OSError when the rig binary is gone.
    """
    real = os.path.realpath(rig_path)
    info = os.stat(real)
    return {
        "rig_version_output": version_text.strip(),
        "rig_realpath": real,
        "rig_mtime_ns": info.st_mtime_ns,
        "rig_size": info.st_size,
        "adapter_sha256": _file_sha(Path(__file__).resolve()),
        "contract_sha256": _file_sha(CONTRACT_PATH),
        "expect_sha256": _file_sha(PROBE_EXPECT_PATH),
        "probe_schema": contract.probe_schema,
    }


def probe_key(material: dict) -> str:
    """Return the sha256 of the key material (canonical JSON)."""
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _finite_time(value: Any) -> bool:
    """True for a real, finite number (json reads NaN, Infinity, -Infinity and 1e999 as floats)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def read_probe_entry(cache: Path, key: str, material: dict) -> dict | None:
    """Return the fresh cache entry for key, or None (a miss).

    A missing, unreadable, partial or non-JSON file, another schema, a key or material that does
    not match, an unknown outcome, a time in the future or an expired TTL (PASS 7 days, every
    other outcome 24 h) is a miss.
    """
    try:
        with open(Path(cache) / f"{key}.json", "rb") as handle:
            raw = handle.read(CONTRACT_MAX_BYTES + 1)
        entry = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(entry, dict) or entry.get("schema") != PROBE_ENTRY_SCHEMA:
        return None
    if entry.get("key") != key or entry.get("material") != material or probe_key(material) != key:
        return None
    outcome, at = entry.get("outcome"), entry.get("at")
    if outcome not in PROBE_OUTCOMES or not _finite_time(at):
        return None
    age = _clock() - at
    ttl = PROBE_PASS_TTL if outcome == "pass" else PROBE_OTHER_TTL
    if age < 0 or age >= ttl:
        return None
    return entry


def _atomic_json(path: Path, data: Any) -> None:
    """Write JSON to path atomically: a temp file in the same dir, fsync, os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_probe_entry(cache: Path, key: str, material: dict, result: ProbeResult) -> Path:
    """Write the evidence and then the entry for key; return the evidence path."""
    cache = Path(cache)
    evidence = cache / f"{key}.evidence.json"
    _atomic_json(evidence, {"key": key, "outcome": result.outcome, "detail": result.detail,
                            **result.evidence})  # fmt: skip
    _atomic_json(
        cache / f"{key}.json",
        {"schema": PROBE_ENTRY_SCHEMA, "key": key, "material": material,
         "outcome": result.outcome, "detail": result.detail, "at": _clock(),
         "evidence": evidence.name},
    )  # fmt: skip
    return evidence


def _drop_entry(cache: Path, key: str) -> bool:
    removed = False
    for name in (f"{key}.json", f"{key}.evidence.json"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(Path(cache) / name)
            removed = True
    return removed


def _cache_writable(cache: Path) -> bool:
    """True when cache exists writable, or its nearest existing ancestor is writable."""
    path = Path(cache)
    while not path.exists():
        if path.parent == path:
            return False
        path = path.parent
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def _probe_state(
    prefix: list[str], version: str | None, contract: Contract, top: Path | None
) -> tuple[Contract, str | None, str | None]:
    """Return (contract, probe state, note) for plan_rig: reads the cache, never probes."""
    found = parse_version(version)
    if version is None or found in [parse_version(v) for v in contract.versions]:
        return contract, None, None
    if not _in_range(found, contract):
        return contract, None, None
    setting, reason = range_probe_setting(top)
    if setting == "disabled":
        return contract._replace(range=()), None, None
    if setting == "off":
        return contract, "off", f"{LOCAL_CONFIG_REL}: {reason}"
    if len(prefix) != 1:
        return contract, "off", "rig runs through npx, which is never probed"
    cache = probe_cache_dir()
    material = probe_key_material(prefix[0], version, contract)
    key = probe_key(material)
    entry = read_probe_entry(cache, key, material)
    if entry is not None:
        note = f"{entry.get('detail', '')}; evidence {cache / f'{key}.evidence.json'}"
        return contract, entry["outcome"], note
    if not _cache_writable(cache):
        return contract, "unwritable", f"the probe cache {cache} is not writable"
    return contract, "pending", None


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


_NO_HANDOFF: Any = object()
_handoff: tuple[tuple, str | None] | None = None


def _rig_identity(prefix: list[str]) -> tuple | None:
    """Return (argv prefix, realpath, mtime_ns, size) of the rig binary, or None."""
    try:
        real = os.path.realpath(prefix[0])
        info = os.stat(real)
    except (OSError, IndexError):
        return None
    return (tuple(prefix), real, info.st_mtime_ns, info.st_size)


def _take_version(prefix: list[str]) -> Any:
    """Return the `--version` text prepare_rig() just read for this same binary, once.

    The hand-over spares the caller's next plan_rig() a second `rig --version` call; any change
    of the binary (realpath, mtime, size) or a second take reads it again (_NO_HANDOFF).
    """
    global _handoff
    held, _handoff = _handoff, None
    if held is None or held[0] is None or held[0] != _rig_identity(prefix):
        return _NO_HANDOFF
    return held[1]


def prepare_rig(
    *,
    wait: bool,
    top: Path | None = None,
    env: dict[str, str] | None = None,
    reprobe: bool = False,
) -> None:
    """Probe an unlisted in-range rig on a cache miss, so plan_rig() can read the result (R7).

    Resolves rig (never npx) and reads `--version`; nothing else runs unless the version is
    unlisted, inside the contract's range and the opt-out allows it. A held probe lock returns at
    once (wait=False: that call is native) or waits up to PROBE_TIMEOUT + 5 s for the other
    probe (wait=True) and leaves the reading to plan_rig(). An unwritable cache returns without
    probing. Called outside spec_lock. Never raises: any error is one warning and native.
    """
    try:
        _prepare_rig(wait=wait, top=top, env=env, reprobe=reprobe)
    except Exception as exc:  # noqa: BLE001 - every probe error is native, never a crash
        _warn_once(
            "prepare",
            f"the OpenRig range probe failed ({type(exc).__name__}: {exc}); HACF writes natively",
        )


def _prepare_rig(
    *, wait: bool, top: Path | None, env: dict[str, str] | None, reprobe: bool
) -> None:
    global _handoff
    _handoff = None
    prefix = resolve_rig()
    if prefix is None or len(prefix) != 1:
        return
    identity = _rig_identity(prefix)
    proc = run_rig([*prefix, "--version"], env=env)
    version = proc.stdout.strip() if proc.returncode == 0 else None
    if env is None and identity is not None and identity == _rig_identity(prefix):
        _handoff = (identity, version)  # plan_rig() takes it instead of a second --version
    contract = load_contract()
    found = parse_version(version)
    if version is None or found in [parse_version(v) for v in contract.versions]:
        return
    if not _in_range(found, contract) or range_probe_setting(top)[0] != "allowed":
        return
    cache = probe_cache_dir()
    material = probe_key_material(prefix[0], version, contract)
    key = probe_key(material)
    if not reprobe and read_probe_entry(cache, key, material) is not None:
        return
    try:
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(cache / ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        _warn_once(
            "cache",
            f"the OpenRig probe cache {cache} is not writable ({exc.strerror or exc}); "
            "HACF does not probe and writes natively",
        )
        return
    try:
        if not _try_lock(fd):
            if wait:
                deadline = time.monotonic() + PROBE_TIMEOUT + 5
                while time.monotonic() < deadline:
                    time.sleep(0.2)
                    if _try_lock(fd):
                        break
            return  # the other probe's result, if any, is plan_rig()'s to read
        if reprobe:
            _drop_entry(cache, key)
        elif read_probe_entry(cache, key, material) is not None:
            return  # another process finished the probe just before the lock was ours
        result = run_probe(prefix, version, env=env)
        try:
            again = probe_key_material(prefix[0], version, contract)
        except OSError:
            again = None
        if again != material:
            _warn_once(
                "key",
                f"rig {version} or HACF's probe inputs changed during the probe; its result "
                "is discarded and HACF writes natively",
            )
            return
        write_probe_entry(cache, key, material, result)
    finally:
        os.close(fd)


def invalidate_probe(version_text: str | None) -> bool:
    """Drop the cache entry that let rig `version_text` delegate (R10); True when one existed.

    Called when a probe-earned delegate errs (scope audit outside {0, 1} or without the JSON key
    paths, proof add non-zero), so the next call is native until a new probe passes. Never raises.
    """
    try:
        prefix = resolve_rig()
        if prefix is None or len(prefix) != 1 or not version_text:
            return False
        material = probe_key_material(prefix[0], version_text, load_contract())
        removed = _drop_entry(probe_cache_dir(), probe_key(material))
    except Exception:  # noqa: BLE001 - invalidation is best effort
        return False
    _warn_once(
        "invalidate",
        f"rig {version_text.strip()} erred under a probe-earned delegate; its probe result is "
        "dropped, so HACF writes natively until a new probe passes",
    )
    return removed


def probe_output_ok(step: str, proc: Completed) -> bool:
    """True when a delegated scope audit or slice ls looks like the probe saw it (R10).

    Exit 0 or 1 (1 is findings, not an error) and JSON holding the expectation file's key paths
    for that step. An unreadable expectation file checks the exit code only.
    """
    if proc.returncode not in (0, 1):
        return False
    try:
        want = set(load_probe_expect()["json_key_paths"][step])
    except (_ProbeError, KeyError):
        want = set()
    try:
        got = json_key_paths(json.loads(proc.stdout))
    except ValueError:
        return False
    return want <= got


def probe_cache_entries() -> list[dict]:
    """Return every cache entry: version, outcome, age in seconds and evidence path, or bad.

    An entry that cannot be read or does not have the entry's shape (a non-string version, outcome
    or evidence, or a time that is not a finite number) is listed as {"path", "bad": reason}; one
    bad file in the shared cache never stops the listing.
    """
    cache = probe_cache_dir()
    entries: list[dict] = []
    try:
        names = sorted(
            p
            for p in cache.glob("*.json")
            if not p.name.endswith(".evidence.json") and not p.name.startswith(".tmp-")
        )
    except OSError:
        return []
    for path in names:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version, outcome = data["material"]["rig_version_output"], data["outcome"]
            at, evidence = data["at"], data["evidence"]
            if not all(isinstance(v, str) for v in (version, outcome, evidence)):
                raise TypeError("version, outcome and evidence must be strings")
            if not _finite_time(at):
                raise ValueError("at is not a finite number")
            age = round(_clock() - at)
            entries.append(
                {
                    "version": version,
                    "outcome": outcome,
                    "age_seconds": age,
                    "evidence": str(cache / evidence),
                }
            )
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
            first = (str(exc).splitlines() or [type(exc).__name__])[0]
            entries.append({"path": str(path), "bad": f"{type(exc).__name__}: {first}"[:300]})
    return entries


# ---------------------------------------------------------------------------
# rig argv builders (flag spellings copied from the v0.6.13 CLI source)
# ---------------------------------------------------------------------------


def _check_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise UsageError(f"{what} {value!r} must match {NAME_RE.pattern}")
    return value


def _check_slug(value: Any, what: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise UsageError(f"{what} {value!r} must be lowercase letters, digits and hyphens")
    return value


def _check_text(value: Any, what: str) -> str:
    """Refuse free text rig could read as an option, or that spans lines."""
    if not isinstance(value, str) or not value.strip() or value.startswith("-"):
        raise UsageError(f"{what} must be non-empty text that does not start with '-'")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise UsageError(f"{what} may not contain control characters or newlines")
    return value


def _check_workspace(workspace: Any) -> str:
    path = Path(workspace)
    if not path.is_absolute():
        raise UsageError(f"workspace {workspace!s} must be an absolute path")
    return str(path)


def _check_slice_arg(slice_path: Any) -> str:
    """A slice argument is an absolute slice path or an NN-slug directory name."""
    text = str(slice_path)
    return text if Path(text).is_absolute() else _check_name(text, "slice")


def _check_artifact_name(name: Any) -> str:
    """A proof artifact name is a plain Markdown filename (rig refuses paths too)."""
    if not isinstance(name, str) or not NAME_RE.match(name) or not name.lower().endswith(".md"):
        raise UsageError(f"--name {name!r} must be a plain Markdown filename such as 01-qa.md")
    return name


def _check_proof_fields(
    artifact_type: str, verdict: str, candidate_sha: str, money_evidence: str
) -> None:
    if artifact_type not in C1_ARTIFACT_TYPES:
        raise UsageError(f"artifact_type {artifact_type!r} not in {', '.join(C1_ARTIFACT_TYPES)}")
    if verdict not in C1_VERDICTS:
        raise UsageError(f"verdict {verdict!r} not in {', '.join(C1_VERDICTS)}")
    if not isinstance(candidate_sha, str) or not (
        SHA_RE.match(candidate_sha) or candidate_sha == "legacy-unrecorded"
    ):
        raise UsageError(f"candidate_sha {candidate_sha!r} must be a hex commit sha")
    _check_text(money_evidence, "money_evidence")


def argv_scope_audit(prefix: list[str], workspace: Path, mission: str) -> list[str]:
    """`rig scope --workspace <root> audit --mission <m> --json` (read-only)."""
    return [
        *prefix,
        "scope",
        "--workspace",
        _check_workspace(workspace),
        "audit",
        "--mission",
        _check_name(mission, "mission"),
        "--json",
    ]


def argv_slice_ls(
    prefix: list[str], workspace: Path, *, mission: str | None = None, state: str = "active"
) -> list[str]:
    """`rig scope --workspace <root> slice ls [--mission <m>] --state <s> --json` (read-only)."""
    if state not in SLICE_STATES:
        raise UsageError(f"state {state!r} not in {', '.join(SLICE_STATES)}")
    argv = [*prefix, "scope", "--workspace", _check_workspace(workspace), "slice", "ls"]
    if mission is not None:
        argv += ["--mission", _check_name(mission, "mission")]
    return [*argv, "--state", state, "--json"]


def argv_slice_create(
    prefix: list[str],
    workspace: Path,
    mission: str,
    slug: str,
    *,
    title: str | None = None,
    intent: str | None = None,
    depends_on: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """`rig scope --workspace <root> slice create <mission> <slug> ... --json`.

    The slice directory is named by the required slug (rig numbers it NN-slug); `--depends-on`
    writes SPEC.md frontmatter, never slice.yaml execution.depends_on.
    """
    argv = [*prefix, "scope", "--workspace", _check_workspace(workspace), "slice", "create"]
    argv += [_check_name(mission, "mission"), _check_slug(slug, "slug")]
    if title is not None:
        argv += ["--title", _check_text(title, "title")]
    if intent is not None:
        argv += ["--intent", _check_text(intent, "intent")]
    for dep in depends_on:
        if not is_slice_dot_id(dep):
            raise UsageError(f"--depends-on {dep!r} is not a slice dot-ID")
    if depends_on:
        argv += ["--depends-on", *depends_on]
    return [*argv, "--json"]


def argv_slice_close(
    prefix: list[str],
    workspace: Path,
    slice_path: str | Path,
    *,
    reason: str,
    mission: str | None = None,
    note: str | None = None,
) -> list[str]:
    """`rig scope --workspace <root> slice close <slice> --reason <r> ... --json`."""
    if reason not in CLOSE_REASONS:
        raise UsageError(f"reason {reason!r} not in {', '.join(CLOSE_REASONS)}")
    argv = [*prefix, "scope", "--workspace", _check_workspace(workspace), "slice", "close"]
    argv += [_check_slice_arg(slice_path), "--reason", reason]
    if mission is not None:
        argv += ["--mission", _check_name(mission, "mission")]
    if note is not None:
        argv += ["--note", _check_text(note, "note")]
    return [*argv, "--json"]


def argv_slice_progress(
    prefix: list[str],
    workspace: Path,
    slice_path: str | Path,
    *,
    add: str,
    mission: str | None = None,
) -> list[str]:
    """`rig scope --workspace <root> slice progress <slice> [--mission <m>] --add <text> --json`."""
    argv = [*prefix, "scope", "--workspace", _check_workspace(workspace), "slice", "progress"]
    argv.append(_check_slice_arg(slice_path))
    if mission is not None:
        argv += ["--mission", _check_name(mission, "mission")]
    return [*argv, "--add", _check_text(add, "progress row"), "--json"]


def argv_proof_add(
    prefix: list[str],
    workspace: Path,
    slice_path: str | Path,
    *,
    artifact_type: str,
    verdict: str,
    candidate_sha: str,
    money_evidence: str,
    file: Path,
    name: str,
    mission: str | None = None,
) -> list[str]:
    """`rig proof --workspace <root> add <slice> ... --file <f> --name <n> --json`.

    --workspace is an option of the parent `proof` command. --name is always passed, so rig never
    writes its default `<type>-<verdict>-<ISO>.md` into a tracked mission dir (R22.4).
    """
    _check_proof_fields(artifact_type, verdict, candidate_sha, money_evidence)
    if not Path(file).is_absolute():
        raise UsageError(f"--file {file!s} must be an absolute path")
    argv = [*prefix, "proof", "--workspace", _check_workspace(workspace), "add"]
    argv.append(_check_slice_arg(slice_path))
    if mission is not None:
        argv += ["--mission", _check_name(mission, "mission")]
    return [
        *argv,
        "--artifact-type",
        artifact_type,
        "--verdict",
        verdict,
        "--candidate-sha",
        candidate_sha,
        "--money-evidence",
        money_evidence,
        "--file",
        str(file),
        "--name",
        _check_artifact_name(name),
        "--json",
    ]


# Wrappers whose rig call creates or names an artifact; each passes --name (R22.4).
ARTIFACT_NAMING_WRAPPERS = (argv_proof_add,)


def run_rig(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = RIG_TIMEOUT,
    env: dict[str, str] | None = None,
) -> Completed:
    """Run one rig argv list with a timeout (never through a shell)."""
    return _run(argv, cwd=cwd, timeout=timeout, env=env)


def rig_slice_create(
    plan: RigPlan, workspace: Path, mission: str, slug: str, **options: Any
) -> tuple[Completed, list[Member]]:
    """Run `rig scope slice create`, then re-read the mission's composition from disk.

    rig appends the membership itself, so the caller gets the members as rig left them.
    """
    if not plan.use_rig:
        raise Refused(plan.warning or "rig is not available; HACF does not create slices via rig")
    proc = run_rig(argv_slice_create(plan.prefix, workspace, mission, slug, **options))
    return proc, read_mission_members(missions_root(workspace) / mission)


def rig_slice_close(
    plan: RigPlan, workspace: Path, mission: str, slice_name: str, *, reason: str
) -> tuple[Completed, list[Member]]:
    """Run `rig scope slice close`, then re-read the mission's composition from disk."""
    if not plan.use_rig:
        raise Refused(plan.warning or "rig is not available; use close_superseded() instead")
    argv = argv_slice_close(plan.prefix, workspace, slice_name, reason=reason, mission=mission)
    proc = run_rig(argv)
    return proc, read_mission_members(missions_root(workspace) / mission)


def render_c1_artifact(header: dict[str, str], body: str) -> str:
    """Return a proof artifact: the C1 header as frontmatter, a blank line, then the body.

    The layout `rig proof add` writes (header lines between `---` lines, then an empty line),
    with the keys in rig's order.
    """
    lines = [f"{key}: {yaml_scalar(header[key])}" for key in C1_REQUIRED_FIELDS]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def write_proof_artifact(
    slice_dir: Path,
    *,
    workspace: Path,
    plan: RigPlan,
    artifact_type: str,
    verdict: str,
    candidate_sha: str,
    money_evidence: str,
    body_file: Path,
    name: str,
) -> Path:
    """Write proof/<name> with a C1 header and return its path, applying the version rule.

    Under an accepted rig the write is delegated to `rig proof add ... --name <name>`. Otherwise
    (no rig, another 0.6.x, another major.minor) HACF writes the same header itself and prints
    the plan's warning, if any, to stderr. An existing artifact is refused either way.
    """
    slice_dir = Path(slice_dir)
    _check_proof_fields(artifact_type, verdict, candidate_sha, money_evidence)
    target = slice_dir / "proof" / _check_artifact_name(name)
    if plan.use_rig:
        argv = argv_proof_add(
            plan.prefix,
            workspace,
            slice_dir.resolve(),
            artifact_type=artifact_type,
            verdict=verdict,
            candidate_sha=candidate_sha,
            money_evidence=money_evidence,
            file=Path(body_file).resolve(),
            name=name,
        )
        proc = run_rig(argv)
        if proc.returncode != 0:
            if plan.via_probe:
                invalidate_probe(plan.version)  # R10: a probe-earned delegate erred
            raise Refused(f"rig proof add failed (exit {proc.returncode}): {proc.stderr.strip()}")
        return target
    if plan.warning:
        print(f"hyper_openrig.py: warning: {plan.warning}", file=sys.stderr)
    slice_id = read_frontmatter(slice_dir / "SPEC.md").get("id")
    if not is_slice_dot_id(slice_id):
        raise Refused(f"{slice_dir / 'SPEC.md'} has no slice dot-ID in its frontmatter")
    header = {
        "slice": slice_id,
        "candidate_sha": candidate_sha,
        "artifact_type": artifact_type,
        "verdict": verdict,
        "money_evidence": money_evidence,
    }
    target.parent.mkdir(exist_ok=True)
    cas_write(target, None, render_c1_artifact(header, _read_text(body_file)))
    return target


# ---------------------------------------------------------------------------
# Registration with the user's OpenRig catalog (R22.5)
# ---------------------------------------------------------------------------


def confirm(prompt: str) -> bool:
    """Ask on the terminal; True only for an explicit yes. Refuses without a TTY.

    This is the one confirmation point of the CLI; tests replace it.
    """
    if not sys.stdin.isatty():
        raise Refused(
            "no terminal to confirm on; run the command interactively (--dry-run previews it)"
        )
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def load_catalog(path: Path) -> tuple[str, list[dict]]:
    """Return (text, projects) for an OpenRig workspace.yaml catalog."""
    text, data = _load_yaml(Path(path))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_WORKSPACE:
        raise Refused(f"{path} is not an {SCHEMA_WORKSPACE} catalog")
    projects = data.get("projects")
    if not isinstance(projects, list) or not all(isinstance(e, dict) for e in projects):
        raise Refused(f"{path}: projects must be a list of mappings")
    return text, projects


def _entry_root(catalog: Path, entry: dict) -> str:
    """Return the real path an entry's root names; rig resolves it against the catalog's dir."""
    return os.path.realpath(os.path.join(Path(catalog).parent, str(entry.get("root", ""))))


_PROJECTS_LINE_RE = re.compile(r"^projects:\s*(#.*)?$")
_ITEM_RE = re.compile(r"^(\s*)-(\s+)\S")


def _projects_lines(lines: list[str]) -> int | None:
    """Return the index of a `projects:` line that starts the last top-level key, else None."""
    starts = [i for i, line in enumerate(lines) if _PROJECTS_LINE_RE.match(line.rstrip("\r\n"))]
    if len(starts) != 1:
        return None
    if any(re.match(r"^[^\s#]", line) for line in lines[starts[0] + 1 :]):
        return None
    return starts[0]


def append_catalog_entry(text: str, entry: dict[str, str]) -> str | None:
    """Append {id, root} to a block-style `projects:` list as text; None when that is not possible.

    OpenRig's own rule (project-registration.ts): every existing byte stays, the entry takes the
    file's indentation and line endings, `projects:` must be the last top-level key with at least
    one block item, and re-parsing must give the old list plus the entry.
    """
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    start = _projects_lines(lines)
    if start is None:
        return None
    first = next((m for m in (_ITEM_RE.match(line) for line in lines[start + 1 :]) if m), None)
    if first is None:
        return None
    dash, gap = first.group(1), first.group(2)
    inner = " " * (len(dash) + 1 + len(gap))
    added = (
        f"{dash}-{gap}id: {yaml_scalar(entry['id'])}{eol}"
        f"{inner}root: {yaml_scalar(entry['root'])}{eol}"
    )
    result = (text if text.endswith("\n") else text + eol) + added
    before = yaml.safe_load(text).get("projects")
    after = yaml.safe_load(result).get("projects")
    return result if after == [*before, entry] else None


def remove_catalog_entry(text: str, project_id: str) -> str | None:
    """Remove the block item whose id is project_id; None when that is not possible as text.

    Only that item's lines go (trailing comment and blank lines stay). Removing the last item
    turns `projects:` into `projects: []`. Re-parsing must give the old list minus the entry.
    """
    lines = text.splitlines(keepends=True)
    start = _projects_lines(lines)
    if start is None:
        return None
    before = yaml.safe_load(text).get("projects")
    expected = [e for e in before if e.get("id") != project_id]
    items = [i for i in range(start + 1, len(lines)) if _ITEM_RE.match(lines[i])]
    if not items:
        return None
    column = len(_ITEM_RE.match(lines[items[0]]).group(1))
    items = [i for i in items if len(_ITEM_RE.match(lines[i]).group(1)) == column]
    for index, first in enumerate(items):
        last = items[index + 1] if index + 1 < len(items) else len(lines)
        while last > first + 1 and (
            not lines[last - 1].strip() or lines[last - 1].lstrip()[:1] == "#"
        ):
            last -= 1
        kept = lines[:first] + lines[last:]
        if not expected:
            kept[start] = re.sub(r"^projects:", "projects: []", kept[start], count=1)
        result = "".join(kept)
        with contextlib.suppress(yaml.YAMLError, AttributeError):
            if yaml.safe_load(result).get("projects") == expected:
                return result
    return None


def _state_dir(top: Path) -> Path:
    """Return .agents/state/openrig, creating it and the state dir's own `*` .gitignore (R22.1)."""
    state = top / STATE_REL
    state.mkdir(parents=True, exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    openrig = top / OPENRIG_STATE_REL
    openrig.mkdir(exist_ok=True)
    return openrig


def _registrations_path(top: Path) -> Path:
    return top / OPENRIG_STATE_REL / "registrations.json"


def load_registrations(top: Path) -> list[dict]:
    """Return the fingerprints in .agents/state/openrig/registrations.json ([] when absent)."""
    path = _registrations_path(top)
    if not path.exists():
        return []
    try:
        data = json.loads(_read_text(path))
    except ValueError as exc:
        raise ParseError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != REGISTRATIONS_SCHEMA:
        raise ParseError(f"{path} is not a {REGISTRATIONS_SCHEMA} file")
    entries = data.get("registrations")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ParseError(f"{path}: registrations must be a list of objects")
    return entries


def save_registrations(top: Path, entries: list[dict]) -> None:
    """Write registrations.json atomically."""
    _state_dir(top)
    data = {"schema": REGISTRATIONS_SCHEMA, "registrations": entries}
    _restore_file(_registrations_path(top), json.dumps(data, indent=2, sort_keys=True) + "\n")


def backup_catalog(top: Path, catalog: Path, text: str) -> Path:
    """Copy the catalog's bytes into .agents/state/openrig/catalog-backups/ and return the copy."""
    backups = _state_dir(top) / "catalog-backups"
    backups.mkdir(exist_ok=True)
    stamp = _runtime.utc_now_iso().replace("-", "").replace(":", "").replace("+0000", "Z")
    for n in range(1000):
        target = backups / f"{stamp}-{n:03d}-{Path(catalog).name}"  # sorts by time
        with contextlib.suppress(FileExistsError):
            with open(target, "x", encoding="utf-8", newline="") as handle:
                handle.write(text)
            return target
    raise Refused(f"could not name a backup in {backups}")


def _diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), label, label
        )
    )


def _same_registration(entry: dict, project_id: str, catalog: str) -> bool:
    return entry.get("id") == project_id and entry.get("catalog") == catalog


def cmd_register_project(ns: argparse.Namespace) -> int:
    """Append {id, root} to the user's catalog after confirmation and a backup."""
    top = repo_toplevel()
    root = os.path.realpath(top)
    project_id = _check_slug(ns.id, "--id")
    catalog = Path(os.path.realpath(Path(ns.catalog).expanduser()))
    text, projects = load_catalog(catalog)
    if any(e.get("id") == project_id for e in projects):
        raise Refused(f"project id {project_id!r} already exists in {catalog}; nothing was changed")
    taken = [e.get("id") for e in projects if _entry_root(catalog, e) == root]
    if taken:
        raise Refused(f"{root} is already registered in {catalog} as {taken[0]!r}")
    commits = root_commits(top)
    if not commits:
        raise Refused("the repository has no commit to fingerprint; commit first")
    entry = {"id": project_id, "root": root}
    result = append_catalog_entry(text, entry)
    if result is None:
        raise Refused(
            f"cannot append to {catalog} without rewriting it (projects must be the last "
            "top-level key, a block list with at least one entry); nothing was changed. "
            f"Add this entry under projects by hand: {{id: {yaml_scalar(project_id)}, "
            f"root: {yaml_scalar(root)}}}"
        )
    print(f"catalog: {catalog}")
    print(f"entry:   {{id: {yaml_scalar(project_id)}, root: {yaml_scalar(root)}}}")
    print(_diff(text, result, str(catalog)), end="")
    if ns.dry_run:
        print("dry run: nothing was written")
        return EXIT_OK
    if not confirm(f"Append this entry to {catalog}?"):
        raise Refused("not confirmed; nothing was written")
    print(f"backup:  {backup_catalog(top, catalog, text)}")
    cas_write(catalog, text, result)
    entries = [
        e for e in load_registrations(top) if not _same_registration(e, project_id, str(catalog))
    ]
    entries.append(
        {
            "id": project_id,
            "catalog": str(catalog),
            "root": root,
            "root_commits": commits,
            "registered_at": _runtime.utc_now_iso(),
        }
    )
    save_registrations(top, entries)
    print(f"registered {project_id} -> {root}")
    return EXIT_OK


def cmd_unregister(ns: argparse.Namespace) -> int:
    """Remove this repository's catalog entry and its fingerprint (confirmation, backup first)."""
    top = repo_toplevel()
    root = os.path.realpath(top)
    project_id = _check_slug(ns.id, "--id")
    catalog = Path(os.path.realpath(Path(ns.catalog).expanduser()))
    text, projects = load_catalog(catalog)
    entry = next((e for e in projects if e.get("id") == project_id), None)
    if entry is None:
        raise Refused(f"project id {project_id!r} is not in {catalog}; nothing was changed")
    registrations = load_registrations(top)
    known = any(_same_registration(e, project_id, str(catalog)) for e in registrations)
    if not known and _entry_root(catalog, entry) != root:
        raise Refused(
            f"{project_id!r} in {catalog} names {entry.get('root')}, not this repository; "
            "nothing was changed"
        )
    result = remove_catalog_entry(text, project_id)
    if result is None:
        raise Refused(
            f"cannot remove {project_id!r} from {catalog} as text; nothing was changed. "
            "Remove its entry under projects by hand."
        )
    print(f"catalog: {catalog}")
    print(_diff(text, result, str(catalog)), end="")
    if not confirm(f"Remove {project_id!r} from {catalog}?"):
        raise Refused("not confirmed; nothing was written")
    print(f"backup:  {backup_catalog(top, catalog, text)}")
    cas_write(catalog, text, result)
    if known:
        save_registrations(
            top, [e for e in registrations if not _same_registration(e, project_id, str(catalog))]
        )
    print(f"unregistered {project_id}")
    return EXIT_OK


def cmd_doctor(ns: argparse.Namespace) -> int:
    """Report registrations whose root moved, whose fingerprint changed or whose entry is gone."""
    top = repo_toplevel()
    catalog = Path(os.path.realpath(Path(ns.catalog).expanduser()))
    registrations = [e for e in load_registrations(top) if e.get("catalog") == str(catalog)]
    try:
        projects = load_catalog(catalog)[1]
    except Refused as exc:
        projects = None
        findings = [f"catalog: {exc}"]
    else:
        findings = []
    for reg in registrations:
        project_id, root = reg.get("id"), reg.get("root")
        if not isinstance(root, str) or not os.path.isdir(root):
            findings.append(f"{project_id}: registered root {root} no longer exists (moved?)")
        else:
            found = root_commits(Path(root))
            if found != reg.get("root_commits"):
                findings.append(
                    f"{project_id}: the root-commit fingerprint at {root} differs "
                    f"(registered {reg.get('root_commits')}, found {found})"
                )
        if projects is not None:
            entry = next((e for e in projects if e.get("id") == project_id), None)
            if entry is None:
                findings.append(f"{project_id}: no longer in the catalog")
            elif _entry_root(catalog, entry) != root:
                findings.append(f"{project_id}: the catalog root is now {entry.get('root')}")
    for finding in findings:
        print(f"doctor: {finding}")
    if not findings:
        print(f"doctor: ok ({len(registrations)} registration(s) checked)")
    return EXIT_FINDINGS if findings else EXIT_OK


def _rig_report() -> dict[str, Any]:
    """Return the opt-out state, the resolved rig, its version and the decision (cache only)."""
    setting, reason = range_probe_setting()
    top = Path(__file__).resolve().parents[2]
    plan = plan_rig()
    rig = None if plan.mode == "absent" else os.path.realpath(shutil.which("rig") or "")
    return {
        "opt_out": {"path": str(top / LOCAL_CONFIG_REL), "state": setting, "reason": reason},
        "rig": {"path": rig, "version": plan.version},
        "decision": {"mode": plan.mode, "probe": plan.probe, "warning": plan.warning},
        "probes": probe_cache_entries(),
    }


def cmd_contract_show(ns: argparse.Namespace) -> int:
    """Print the contract as HACF reads it: path, source, warning, entries, range, RIG_VERSION.

    Also the range-probe opt-out, the resolved rig with its version and decision, and every probe
    cache entry. Reads the cache only; it never probes.
    """
    path = CONTRACT_PATH
    contract = load_contract(path)
    rig_version = contract_rig_version(contract)
    ranges = [{"major": a, "minor": b, "min_patch": c} for a, b, c in contract.range]
    report = _rig_report()
    if ns.json:
        print(
            json.dumps(
                {
                    "path": str(path),
                    "source": contract.source,
                    "warning": contract.warning,
                    "accepted": [entry._asdict() for entry in contract.accepted],
                    "range": ranges,
                    "rig_version": rig_version,
                    **{k: report[k] for k in ("opt_out", "rig", "decision", "probes")},
                },
                indent=2,
            )
        )
        return EXIT_OK
    print(f"contract: {path}")
    print(f"source: {contract.source}")
    if contract.warning:
        print(f"warning: {contract.warning}")
    print("accepted:")
    for entry in contract.accepted:
        verdict = entry.verdict or "-"
        print(f"  {entry.version}  accepted_by: {entry.accepted_by}  verdict: {verdict}")
    shown = ", ".join(f"{a}.{b}.x (x >= {c})" for a, b, c in contract.range) or "none"
    print(f"range: {shown}")
    print(f"RIG_VERSION: {rig_version}")
    opt = report["opt_out"]
    print(f"range probe: {opt['state']}" + (f" ({opt['reason']})" if opt["reason"] else ""))
    rig, decision = report["rig"], report["decision"]
    print(f"rig: {rig['path'] or 'absent'}" + (f" ({rig['version']})" if rig["version"] else ""))
    probe = f" (probe {decision['probe']})" if decision["probe"] else ""
    print(f"decision: {decision['mode']}{probe}")
    if decision["warning"]:
        print(f"  {decision['warning']}")
    print(f"probe cache: {probe_cache_dir()}")
    for entry in report["probes"]:
        if "bad" in entry:
            print(f"  bad entry {entry['path']}: {entry['bad']}")
            continue
        hours = entry["age_seconds"] / 3600
        print(
            f"  {entry['version']}  {entry['outcome']}  age {hours:.1f} h  "
            f"evidence: {entry['evidence']}"
        )
    if not report["probes"]:
        print("  (no entries)")
    return EXIT_OK


def cmd_contract_probe(ns: argparse.Namespace) -> int:
    """Probe the resolved rig now (cache first; --reprobe drops its entry) and print the outcome.

    Exit 0 on pass, 1 on any other outcome, 2 when rig is absent or not an unlisted in-range
    version (a listed version needs no probe; the opt-out false removes the range).
    """
    before = plan_rig()
    if before.mode == "absent" or before.probe is None:
        why = (
            "rig is not on PATH"
            if before.mode == "absent"
            else f"rig {before.version} is not an unlisted version inside the probed range "
            f"(decision {before.mode}); nothing to probe"
        )
        if ns.json:
            print(json.dumps({"version": before.version, "outcome": None, "reason": why}))
        else:
            print(f"contract probe: {why}", file=sys.stderr)
        return EXIT_USAGE
    prepare_rig(wait=True, reprobe=ns.reprobe)
    plan = plan_rig()
    entries = [e for e in probe_cache_entries() if e.get("version") == (plan.version or "").strip()]
    if ns.json:
        print(
            json.dumps(
                {"version": plan.version, "outcome": plan.probe, "decision": plan.mode,
                 "warning": plan.warning, "entries": entries},
                indent=2,
            )
        )  # fmt: skip
    else:
        print(f"contract probe: rig {plan.version}: {plan.probe} (decision {plan.mode})")
        if plan.warning:
            print(f"  {plan.warning}")
        for entry in entries:
            print(f"  evidence: {entry['evidence']}")
    return EXIT_OK if plan.probe == "pass" else EXIT_FINDINGS


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_openrig.py",
        description=(
            "Register this repository with a user's OpenRig catalog (the rig versions in "
            f"{CONTRACT_REL})."
        ),
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name: str, handler: Callable[[argparse.Namespace], int], text: str) -> Any:
        p = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        p.set_defaults(handler=handler)
        p.add_argument("--catalog", required=True, help="the user's OpenRig workspace.yaml")
        return p

    p = command("register-project", cmd_register_project, "add this repo to the catalog")
    p.add_argument("--id", required=True, help="the catalog project id (a slug)")
    p.add_argument("--dry-run", action="store_true", help="print the change and write nothing")
    p = command("unregister", cmd_unregister, "remove this repo's catalog entry")
    p.add_argument("--id", required=True, help="the catalog project id")
    command("doctor", cmd_doctor, "check registered roots and fingerprints")
    text = "show the accepted rig versions and the probed range"
    p = sub.add_parser("contract", help=text, description=text, allow_abbrev=False)
    contract_sub = p.add_subparsers(dest="contract_command", metavar="<command>", required=True)
    text = f"print {CONTRACT_REL} as HACF reads it (exit 0, also on fallback)"
    p = contract_sub.add_parser("show", help=text, description=text, allow_abbrev=False)
    p.set_defaults(handler=cmd_contract_show)
    p.add_argument("--json", action="store_true", help="print one JSON object")
    text = (
        "probe the resolved rig if it is an unlisted version inside the range (exit 0 pass, "
        "1 otherwise, 2 when there is nothing to probe)"
    )
    p = contract_sub.add_parser("probe", help=text, description=text, allow_abbrev=False)
    p.set_defaults(handler=cmd_contract_probe)
    p.add_argument("--reprobe", action="store_true", help="drop the cached result first")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code (0 ok, 1 findings, 2 usage, 4 parse, 5 refused)."""
    _runtime.require_runtime()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return ns.handler(ns)
    except OpenRigError as exc:
        print(f"hyper_openrig.py: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
