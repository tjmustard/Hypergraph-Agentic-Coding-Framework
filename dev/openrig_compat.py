#!/usr/bin/env python3
"""The comparison, normalisation and verdict logic of dev/openrig_compat.sh (release-0.6.2 R12-R14).

dev/openrig_compat.sh owns the arguments, the temp dir, the isolated environment and the order of
the steps; every decision it needs is a subcommand here, so it can be unit-tested
(dev/tests/compat/test_openrig_compat.py). Stdlib plus PyYAML; runs on Python 3.10.

Every input this module reads is treated as hostile: npm output, package files, the live suite's
captures, the committed baselines, the contract file and the run's own state files. A malformed
input (unreadable, too large, not UTF-8, NaN or infinite numbers, huge integers, deep nesting,
missing keys, wrong types) becomes a clear SETUP-ERROR (the harness or a baseline is broken) or a
FAIL (the candidate's output is wrong), never a crash and never a PASS.

Exit codes of the comparison subcommands: 0 ok, 1 fail (the candidate), 2 setup error (the
harness or a baseline). `report` exits with the verdict's code: 0 PASS, 1 FAIL, 2 SETUP-ERROR,
3 REVIEW, 4 SEED.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dev dependency; its absence is a setup error
    yaml = None  # type: ignore[assignment]

EXIT_OK, EXIT_FAIL, EXIT_SETUP = 0, 1, 2
VERDICT_EXIT = {"PASS": 0, "FAIL": 1, "SETUP-ERROR": 2, "REVIEW": 3, "SEED": 4}

# A plain M.m.p version: no prerelease, build tag, `v` prefix or leading zero, and at most 6 digits
# per part, so a hostile version can never become a huge integer.
VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z")

MAX_JSON_BYTES = 8 * 1024 * 1024  # one capture, verdict or meta file
MAX_TEXT_BYTES = 4 * 1024 * 1024  # one help text or package.json
MAX_WATCHED_BYTES = 16 * 1024 * 1024  # one watched package file
MAX_DIFF_BYTES = 256 * 1024  # one unified diff in the evidence
MAX_INT_DIGITS = 30
MAX_DEPTH = 64
MAX_KEY_PATHS = 100_000
MAX_LIST_FILES = 10_000

# The 8 watched items (mission R12; the maintainer's red-team Q1 keeps the rule strict). A file is
# compared byte for byte; a template is the bytes of one JavaScript template literal in its file.
WATCHED: tuple[tuple[str, str, str | None], ...] = (
    ("lifecycle-manifest.js", "daemon/dist/domain/lifecycle-manifest.js", None),
    ("mission-composition.js", "dist/lib/scope/mission-composition.js", None),
    ("scope-audit.js", "dist/lib/scope/scope-audit.js", None),
    ("scaffold-placeholder.js", "dist/lib/scope/scaffold-placeholder.js", None),
    ("missionManifest", "dist/commands/scope.js", "missionManifest"),
    ("SLICE_MANIFEST", "dist/commands/scope.js", "SLICE_MANIFEST"),
    ("PROJECT_MANIFEST", "daemon/dist/domain/workspace/default-workspace-scaffold.js",
     "PROJECT_MANIFEST"),
    ("WORKSPACE_CATALOG", "daemon/dist/domain/workspace/default-workspace-scaffold.js",
     "WORKSPACE_CATALOG"),
)  # fmt: skip

# The files directly in ~/.openrig/ that the run may not change; the daemon's own logs and
# databases change on their own and are left out (R13).
OPENRIG_HOME_EXCLUDED = re.compile(r"(\.log|\.db|\.db-.*|\.sqlite.*|\.pid)$")

# Capture keys that differ run to run by design; never compared.
CAPTURE_VOLATILE = ("cwd", "captured_at", "rig_version")
RIG_PACKAGE = "@openrig/cli"


class CompatInputError(Exception):
    """A hostile or malformed input; the message says which file and why."""


# ---------------------------------------------------------------------------------------------
# Safe readers


def check_version(text: Any) -> str:
    """Return text when it is a plain M.m.p version, else raise CompatInputError."""
    if not isinstance(text, str) or not VERSION_RE.match(text):
        raise CompatInputError(f"version {str(text)[:40]!r} is not a plain M.m.p version")
    return text


def version_key(version: str) -> tuple[int, int, int]:
    """Return the numeric key of a checked version."""
    major, minor, patch = check_version(version).split(".")
    return int(major), int(minor), int(patch)


def read_bytes(path: Path, limit: int) -> bytes:
    """Read at most `limit` bytes of a regular file; anything else is a CompatInputError."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise CompatInputError(f"{path}: not a regular file")
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except FileNotFoundError:
        raise CompatInputError(f"{path}: missing") from None
    except OSError as exc:
        raise CompatInputError(f"{path}: cannot read: {exc.strerror or exc}") from None
    if len(data) > limit:
        raise CompatInputError(f"{path}: larger than {limit} bytes")
    return data


def read_text(path: Path, limit: int = MAX_TEXT_BYTES) -> str:
    """Read a UTF-8 text file under the size cap."""
    try:
        return read_bytes(path, limit).decode("utf-8")
    except UnicodeDecodeError:
        raise CompatInputError(f"{path}: not UTF-8") from None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _parse_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        raise ValueError(f"integer with more than {MAX_INT_DIGITS} digits")
    return int(text)


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {text[:20]}")
    return value


def loads_json(text: str, what: str) -> Any:
    """Parse JSON strictly: no NaN/Infinity, huge integers, overflowing floats or deep nesting."""
    try:
        data = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
    except RecursionError:
        raise CompatInputError(f"{what}: JSON nested too deeply") from None
    except ValueError as exc:
        first = (str(exc).splitlines() or ["invalid"])[0][:200]
        raise CompatInputError(f"{what}: invalid JSON: {first}") from None
    _check_depth(data, what)
    return data


def _check_depth(data: Any, what: str) -> None:
    stack = [(data, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise CompatInputError(f"{what}: JSON nested deeper than {MAX_DEPTH}")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)


def load_json_file(path: Path, limit: int = MAX_JSON_BYTES) -> Any:
    """Read and strictly parse one JSON file."""
    try:
        text = read_bytes(path, limit).decode("utf-8")
    except UnicodeDecodeError:
        raise CompatInputError(f"{path}: not UTF-8") from None
    return loads_json(text, str(path))


def write_json(path: Path, data: Any) -> None:
    """Write JSON (sorted, indented) atomically enough for evidence: temp file then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------
# Normalisation (idempotent: normalise(normalise(x)) == normalise(x))

_NORM_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/[^\s\"']*?/pytest-\d+/test_openrig_cli_spike\d*"), "<TMP>"),
    (re.compile(r"/[^\s\"']*?test_openrig_cli_spike\d*"), "<TMP>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<SHA>"),
    (re.compile(re.escape(RIG_PACKAGE) + r"@[0-9][0-9A-Za-z.+-]*"), f"{RIG_PACKAGE}@<V>"),
)


def normalise_text(text: Any, roots: tuple[str, ...] = ()) -> Any:
    """Replace run-specific values (temp paths, timestamps, dates, shas, the package version)."""
    if not isinstance(text, str):
        return text
    for root in sorted((r for r in roots if r), key=len, reverse=True):
        text = text.replace(root, "<T>")
    for pattern, repl in _NORM_RULES:
        text = pattern.sub(repl, text)
    return text


def normalise_value(value: Any, roots: tuple[str, ...] = ()) -> Any:
    """Normalise every string inside a JSON value (keys too)."""
    if isinstance(value, str):
        return normalise_text(value, roots)
    if isinstance(value, list):
        return [normalise_value(v, roots) for v in value]
    if isinstance(value, dict):
        return {normalise_text(k, roots): normalise_value(v, roots) for k, v in value.items()}
    return value


def _expect(record: dict, key: str, kinds: tuple[type, ...], what: str) -> Any:
    if key not in record:
        raise CompatInputError(f"{what}: missing key {key!r}")
    value = record[key]
    if isinstance(value, bool) and bool not in kinds:
        raise CompatInputError(f"{what}: {key} has the wrong type (bool)")
    if not isinstance(value, kinds):
        raise CompatInputError(f"{what}: {key} has the wrong type ({type(value).__name__})")
    return value


def normalise_capture(record: Any, what: str, roots: tuple[str, ...] = ()) -> dict:
    """Return the comparable form of one live-suite capture (argv[0] by basename, no volatiles)."""
    if not isinstance(record, dict):
        raise CompatInputError(f"{what}: a capture must be a JSON object")
    argv = _expect(record, "argv", (list,), what)
    if not argv or not all(isinstance(a, str) for a in argv):
        raise CompatInputError(f"{what}: argv must be a non-empty list of strings")
    exit_code = _expect(record, "exit_code", (int,), what)
    out: dict[str, Any] = {
        "step": normalise_text(_expect(record, "step", (str,), what), roots),
        "argv": [os.path.basename(argv[0]), *(normalise_text(a, roots) for a in argv[1:])],
        "exit_code": exit_code,
        "stdout": normalise_text(_expect(record, "stdout", (str,), what), roots),
        "stderr": normalise_text(_expect(record, "stderr", (str,), what), roots),
    }
    if "git_status_after" in record:
        status = record["git_status_after"]
        if not isinstance(status, list) or not all(isinstance(s, str) for s in status):
            raise CompatInputError(f"{what}: git_status_after must be a list of strings")
        out["git_status_after"] = [normalise_text(s, roots) for s in status]
    return out


def normalise_verdict(record: Any, what: str, roots: tuple[str, ...] = ()) -> dict:
    """Return the comparable form of the live suite's verdict.json."""
    if not isinstance(record, dict):
        raise CompatInputError(f"{what}: verdict.json must be a JSON object")
    verdict = _expect(record, "spike_verdict_cli", (str,), what)
    checks = _expect(record, "checks", (dict,), what)
    if not all(isinstance(v, bool) for v in checks.values()):
        raise CompatInputError(f"{what}: every check must be true or false")
    write_sets = _expect(record, "rig_write_sets", (dict,), what)
    captures = _expect(record, "captures", (list,), what)
    if not all(isinstance(c, str) for c in captures):
        raise CompatInputError(f"{what}: captures must be a list of names")
    for key, value in (("checks", checks), ("rig_write_sets", write_sets), ("captures", captures)):
        if not value:  # the live suite always records some: an empty one compares nothing
            raise CompatInputError(f"{what}: {key} is empty")
    return {
        "spike_verdict_cli": verdict,
        "checks": dict(sorted(checks.items())),
        "rig_write_sets": normalise_value(write_sets, roots),
        "captures": captures,
    }


CAPTURE_NAME_RE = re.compile(r"^[0-9]{2}-[a-z0-9-]{1,80}\.json$")


def load_capture_dir(directory: Path, roots: tuple[str, ...] = ()) -> dict[str, dict]:
    """Load and normalise every NN-step.json and verdict.json of a capture dir."""
    if not directory.is_dir():
        raise CompatInputError(f"{directory}: no capture directory")
    names = sorted(p.name for p in directory.iterdir())
    if len(names) > 500:
        raise CompatInputError(f"{directory}: more than 500 entries")
    out: dict[str, dict] = {}
    for name in names:
        if CAPTURE_NAME_RE.match(name):
            data = load_json_file(directory / name)
            out[name] = normalise_capture(data, str(directory / name), roots)
    if "verdict.json" not in names:
        raise CompatInputError(f"{directory}: no verdict.json (the live suite recorded nothing)")
    out["verdict.json"] = normalise_verdict(
        load_json_file(directory / "verdict.json"), str(directory / "verdict.json"), roots
    )
    listed, present = set(out["verdict.json"]["captures"]), set(out) - {"verdict.json"}
    if listed != present:
        raise CompatInputError(
            f"{directory}: the captures verdict.json lists are not the files present "
            f"(listed only: {sorted(listed - present)[:5]}, "
            f"present only: {sorted(present - listed)[:5]})"
        )
    return out


# ---------------------------------------------------------------------------------------------
# Comparisons


def key_paths(data: Any, what: str = "json") -> set[str]:
    """Every key path of a JSON value; list items collapse to `[]`. Bounded, never recursive."""
    paths: set[str] = set()
    stack: list[tuple[Any, str, int]] = [(data, "", 0)]
    while stack:
        node, prefix, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise CompatInputError(f"{what}: JSON nested deeper than {MAX_DEPTH}")
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}/{key}"
                paths.add(path)
                stack.append((value, path, depth + 1))
        elif isinstance(node, list):
            for value in node:
                stack.append((value, prefix + "[]", depth + 1))
        if len(paths) > MAX_KEY_PATHS:
            raise CompatInputError(f"{what}: more than {MAX_KEY_PATHS} JSON key paths")
    return paths


def _stdout_json(text: str, what: str) -> Any:
    """Return the parsed stdout, or None when it is not JSON at all."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    return loads_json(stripped, what)


def compare_captures(baseline: dict[str, dict], candidate: dict[str, dict]) -> dict:
    """Compare a candidate's normalised captures with one baseline's.

    Equal: the capture set, each exit code, stderr, argv and `git status` after the call, the
    live suite's checks and write sets. JSON stdout: key paths may only grow (additions are
    reported). Non-JSON stdout (the version, the integrity) is not compared.
    Returns {"ok": bool, "problems": [...], "json_paths_added": {capture: [paths]}}.
    """
    problems: list[str] = []
    added: dict[str, list[str]] = {}
    missing = sorted(set(baseline) - set(candidate))
    extra = sorted(set(candidate) - set(baseline))
    if missing:
        problems.append(f"captures missing from the candidate: {', '.join(missing)}")
    if extra:
        problems.append(f"captures the baseline does not have: {', '.join(extra)}")
    for name in sorted(set(baseline) & set(candidate)):
        a, b = baseline[name], candidate[name]
        if name == "verdict.json":
            if a.get("spike_verdict_cli") != "pass":
                raise CompatInputError(
                    "baseline verdict.json: the live suite's verdict is not pass"
                )
            if b.get("spike_verdict_cli") != "pass":
                problems.append("verdict.json: the live suite's verdict is not pass")
            for key in ("checks", "rig_write_sets", "captures"):
                if a.get(key) != b.get(key):
                    problems.append(f"verdict.json: {key} differs")
            continue
        for key in ("step", "argv", "exit_code", "stderr", "git_status_after"):
            if a.get(key) != b.get(key):
                problems.append(f"{name}: {key} differs: {a.get(key)!r} -> {b.get(key)!r}"[:600])
        try:
            ja = _stdout_json(a.get("stdout", ""), f"baseline {name} stdout")
        except CompatInputError as exc:
            raise CompatInputError(f"baseline {name}: {exc}") from None
        try:
            jb = _stdout_json(b.get("stdout", ""), f"candidate {name} stdout")
        except CompatInputError as exc:
            problems.append(f"{name}: candidate stdout: {exc}")
            continue
        if ja is None and jb is None:
            continue
        if (ja is None) != (jb is None):
            problems.append(f"{name}: stdout is JSON on one side only")
            continue
        pa, pb = key_paths(ja, f"baseline {name}"), key_paths(jb, f"candidate {name}")
        gone = sorted(pa - pb)
        if gone:
            problems.append(f"{name}: JSON key paths removed: {', '.join(gone)}"[:600])
        grown = sorted(pb - pa)
        if grown:
            added[name] = grown
    return {"ok": not problems, "problems": problems, "json_paths_added": added}


def parse_help(text: str) -> dict[str, list[str]]:
    """Return the flags (`--x`) and subcommands a commander-style `--help` text lists."""
    flags: set[str] = set()
    commands: set[str] = set()
    section = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            head = line.strip().lower()
            section = "options" if head.startswith("options") else (
                "commands" if head.startswith("commands") else None
            )  # fmt: skip
            continue
        if section == "options":
            match = re.match(r"^\s+(?:-[A-Za-z0-9], )?(--[A-Za-z0-9][A-Za-z0-9-]*)", line)
            if match:
                flags.add(match.group(1))
        elif section == "commands":
            match = re.match(r"^\s{1,4}([a-z][a-z0-9-]*)(?:\|[a-z0-9-]+)?(?:\s|$)", line)
            if match:
                commands.add(match.group(1))
    return {"flags": sorted(flags), "commands": sorted(commands)}


def check_help_text(text: str, what: str) -> dict[str, list[str]]:
    """Parse a help text that must be whole: an `Options:` section listing at least `--help`.

    Commander always lists `-h, --help` last in Options, so an empty file, one truncated mid-Usage
    or one cut before the end of its Options section is a CompatInputError, never a text with no
    flags to lose. A command with subcommands (its Usage line ends in `[command]`) prints a
    `Commands:` section after Options whose last row is always `help [command]`, so a text cut
    anywhere after Options, or one with no Commands section, is a CompatInputError too.
    """
    usage = re.search(r"(?m)^usage:[^\n]*", text, re.IGNORECASE)
    if not usage:
        raise CompatInputError(f"{what}: no Usage: line (empty or truncated help text)")
    if not re.search(r"(?m)^options:", text, re.IGNORECASE):
        raise CompatInputError(f"{what}: no Options: section (empty or truncated help text)")
    parsed = parse_help(text)
    if "--help" not in parsed["flags"]:
        raise CompatInputError(f"{what}: Options lists no --help (truncated help text)")
    if usage.group(0).rstrip().endswith("[command]") and "help" not in parsed["commands"]:
        raise CompatInputError(
            f"{what}: Usage ends in [command] but no Commands: section lists help "
            "(truncated help text)"
        )
    return parsed


def help_diff(base_text: str, cand_text: str, hacf_flags: list[str]) -> dict:
    """Classify one command's help change: removals (and HACF flags gone) fail, additions report.

    A broken baseline help text (see check_help_text) is a CompatInputError: a setup error.
    """
    a, b = check_help_text(base_text, "baseline help"), parse_help(cand_text)
    out = {
        "removed_flags": sorted(set(a["flags"]) - set(b["flags"])),
        "added_flags": sorted(set(b["flags"]) - set(a["flags"])),
        "removed_commands": sorted(set(a["commands"]) - set(b["commands"])),
        "added_commands": sorted(set(b["commands"]) - set(a["commands"])),
        "hacf_flags_missing": sorted(set(hacf_flags) - set(b["flags"])),
    }
    out["ok"] = not (out["removed_flags"] or out["removed_commands"] or out["hacf_flags_missing"])
    return out


def help_name(words: list[str]) -> str:
    """The file name of one command's help text: `scope_slice_ls.txt`."""
    return "_".join(words) + ".txt"


def extract_template(source: bytes, name: str) -> bytes | None:
    r"""Return the bytes of the template literal assigned to `const <name>`, or None.

    Handles `const X = \\`...\\`` and `const X = (args) => \\`...\\``, backslash escapes and nested
    `${...}` (with nested template literals inside them).
    """
    match = re.search(
        rb"\bconst\s+" + re.escape(name.encode()) + rb"\s*=\s*(?:\([^()]{0,200}\)\s*=>\s*)?`",
        source,
    )
    if not match:
        return None
    start = match.end() - 1
    i, depth_stack = start + 1, [0]  # depth_stack[-1]: brace depth inside the current ${...}
    n = len(source)
    in_template = [True]
    while i < n:
        c = source[i : i + 1]
        if c == b"\\":
            i += 2
            continue
        if in_template[-1]:
            if c == b"`":
                in_template.pop()
                depth_stack.pop()
                if not in_template:
                    return source[start : i + 1]
            elif source[i : i + 2] == b"${":
                in_template.append(False)
                depth_stack.append(0)
                i += 2
                continue
        else:
            if c == b"{":
                depth_stack[-1] += 1
            elif c == b"}":
                if depth_stack[-1] == 0:
                    in_template.pop()
                    depth_stack.pop()
                else:
                    depth_stack[-1] -= 1
            elif c == b"`":
                in_template.append(True)
                depth_stack.append(0)
        if len(in_template) > MAX_DEPTH:
            return None
        i += 1
    return None


def watched_bytes(package: Path) -> dict[str, bytes | None]:
    """Read the 8 watched items of an installed package (None: missing or unreadable)."""
    out: dict[str, bytes | None] = {}
    cache: dict[str, bytes | None] = {}
    for key, rel, template in WATCHED:
        if rel not in cache:
            try:
                cache[rel] = read_bytes(package / rel, MAX_WATCHED_BYTES)
            except CompatInputError:
                cache[rel] = None
        data = cache[rel]
        out[key] = data if template is None or data is None else extract_template(data, template)
    return out


def watched_file_name(key: str) -> str:
    """A watched item's file name inside a baseline's watched/ dir."""
    return key if key.endswith(".js") else f"{key}.tmpl"


def load_baseline_watched(baseline: Path) -> dict[str, bytes]:
    """Read a baseline's 8 watched items; a missing one is a broken baseline."""
    return {
        key: read_bytes(baseline / "watched" / watched_file_name(key), MAX_WATCHED_BYTES)
        for key, _, _ in WATCHED
    }


def unified(a: bytes, b: bytes, label_a: str, label_b: str) -> str:
    """A unified diff of two byte strings, capped at MAX_DIFF_BYTES."""
    lines = difflib.unified_diff(
        a.decode("utf-8", "replace").splitlines(keepends=True),
        b.decode("utf-8", "replace").splitlines(keepends=True),
        label_a,
        label_b,
    )
    text = "".join(lines)
    if len(text) > MAX_DIFF_BYTES:
        text = text[:MAX_DIFF_BYTES] + "\n... (diff truncated)\n"
    return text


def compare_watched(candidate: dict[str, bytes | None], baselines: dict[str, dict[str, bytes]]):
    """Return {key: {baseline_version: "differs"|"missing"}} and the diffs, per baseline."""
    differs: dict[str, dict[str, str]] = {}
    diffs: dict[str, str] = {}
    for version, items in baselines.items():
        for key, _, _ in WATCHED:
            got, want = candidate.get(key), items[key]
            if got is None:
                differs.setdefault(key, {})[version] = "missing"
            elif got != want:
                differs.setdefault(key, {})[version] = "differs"
                diffs[f"{key}-vs-{version}"] = unified(
                    want, got, f"baseline {version}/{key}", f"candidate/{key}"
                )
    return differs, diffs


# ---------------------------------------------------------------------------------------------
# Verdict and classification


def classify_exit(rc: Any, oom_grew: bool, kind: str) -> str:
    """Return ok, fail or setup for one step's exit code.

    A signal death (exit >= 128 or negative), 124 from `timeout` or an OOM kill recorded in the
    cgroup's memory.events is always setup, never the product's fail. `kind` "harness" (npm, the
    tree copy, the run's own bookkeeping) makes every other non-zero exit setup too; "compare"
    (this module's comparisons) maps 1 to fail and anything else to setup; "product" maps every
    other non-zero exit to fail.
    """
    if isinstance(rc, bool) or not isinstance(rc, int):
        return "setup"
    if oom_grew or rc >= 128 or rc < 0 or rc == 124:
        return "setup"
    if rc == 0:
        return "ok"
    if kind == "harness":
        return "setup"
    if kind == "compare":
        return "fail" if rc == EXIT_FAIL else "setup"
    return "fail"


# The order of the run, and each step's kind; daemon-contact and host-touched are safety checks.
STEP_KINDS = {
    "install": "harness",
    "tree": "harness",
    "baselines": "harness",
    "live": "product",
    "captures": "compare",
    "help": "compare",
    "watched": "compare",
    "spec-validate": "compare",
    "delegate-probe": "product",
    "delegate-contract": "harness",
    "delegate-verify": "product",
    "delegate-proof": "product",
    "delegate-oracle": "product",
    "daemon-contact": "product",
    "host-touched": "product",
}
SAFETY_STEPS = ("daemon-contact", "host-touched")
# The steps each mode must have run, all ok, before its green verdict. A missing one is a
# SETUP-ERROR naming it: the verdict fails closed, and slice 4 auto-accepts on PASS.
REQUIRED_STEPS = tuple(STEP_KINDS)
SEED_REQUIRED_STEPS = (
    "install", "tree", "live", "help", "spec-validate", "delegate-probe", "delegate-contract",
    "delegate-verify", "delegate-proof", "delegate-oracle", "daemon-contact", "host-touched",
)  # fmt: skip


def decide(steps: list[dict], watched_diff: dict, seed: bool) -> tuple[str, str | None, str | None]:
    """Return (verdict, failed_step, setup_error) from the classified steps.

    A safety FAIL (the candidate contacted a daemon, or the host's ~/.openrig changed) wins over
    everything; then a setup error (never reported as the product's); then any FAIL; then a
    required step that never ran (SETUP-ERROR, named); then SEED; then REVIEW when a watched item
    differs from either baseline; else PASS. PASS, REVIEW and SEED need every step of the mode
    (REQUIRED_STEPS, SEED_REQUIRED_STEPS) present and ok.
    """
    for step in steps:
        if step["name"] in SAFETY_STEPS and step["result"] == "fail":
            return "FAIL", step["name"], None
    for step in steps:
        if step["result"] not in ("ok", "fail"):
            return "SETUP-ERROR", None, f"{step['name']}: {step.get('detail') or 'setup error'}"
    for step in steps:
        if step["result"] == "fail":
            return "FAIL", step["name"], None
    ran = {step["name"] for step in steps}
    missing = [
        name for name in (SEED_REQUIRED_STEPS if seed else REQUIRED_STEPS) if name not in ran
    ]
    if missing:
        return "SETUP-ERROR", None, f"required steps never ran: {', '.join(missing)}"
    if not isinstance(watched_diff, dict):
        return "SETUP-ERROR", None, "watched: the watched diff is not an object"
    if seed:
        return "SEED", None, None
    if watched_diff:
        return "REVIEW", None, None
    return "PASS", None, None


SPEC_VALIDATE_STEP = "spec-validate"
SPEC_INVALID = "spec-invalid"
SPEC_VALIDATE_TIMEOUT = 120


def run_spec_validate(rig: str, spec: Path, out: Path) -> int:
    """Run the candidate's `rig spec validate --json <spec>` and record {exit, valid} in out.

    The candidate runs in the caller's isolated environment (the shell drops every OPENRIG_* and
    RIGGED_* variable and points OPENRIG_URL at a dead listener); `spec validate` needs no daemon.
    Exit 0 when it exits 0 with `"valid": true`, EXIT_FAIL when it exits non-zero or says
    `"valid": false` (the verdict's `spec-invalid`), EXIT_SETUP when it cannot run or its output
    is not the JSON object it documents.
    """
    record: dict = {"exit": None, "valid": None}
    try:
        result = subprocess.run(
            [rig, "spec", "validate", "--json", str(spec)],
            capture_output=True, text=True, timeout=SPEC_VALIDATE_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        write_json(out, {**record, "error": type(exc).__name__})
        return EXIT_SETUP
    record["exit"] = result.returncode
    try:
        data = json.loads(result.stdout[:MAX_JSON_BYTES]) if result.stdout.strip() else None
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("valid"), bool):
        record["valid"] = data["valid"]
    write_json(out, record)
    if result.returncode != 0 or record["valid"] is False:
        return EXIT_FAIL
    return EXIT_OK if record["valid"] is True else EXIT_SETUP


def read_spec_validate(state: Path) -> dict | None:
    """The spec-validate step's {exit, valid}, or None when it wrote nothing readable."""
    path = state / "spec_validate.json"
    if not path.exists():
        return None
    try:
        data = load_json_file(path)
    except CompatInputError:
        return None
    if not isinstance(data, dict):
        return None
    return {"exit": data.get("exit"), "valid": data.get("valid")}


def read_steps(path: Path) -> list[dict]:
    """Read the shell's steps.tsv (name, rc, seconds, oom_kill delta, detail) and classify each row.

    A malformed row becomes a setup step, never a silent pass.
    """
    steps: list[dict] = []
    try:
        text = read_text(path, MAX_TEXT_BYTES) if path.exists() else ""
    except CompatInputError as exc:
        return [{"name": "steps", "result": "setup", "rc": None, "seconds": None,
                 "detail": str(exc)}]  # fmt: skip
    # The shell ends each row with "\n" and nothing else: split on it alone. str.splitlines()
    # would also break a row at \f, \x1c-\x1e or U+2028 inside a step's detail text.
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    for n, line in enumerate(rows, 1):
        parts = line.split("\t", 4)
        if len(parts) < 4:
            steps.append({"name": f"steps.tsv:{n}", "result": "setup", "rc": None,
                          "seconds": None, "detail": "malformed row"})  # fmt: skip
            continue
        name, rc_text, secs_text, oom_text = parts[:4]
        detail = parts[4] if len(parts) > 4 else ""
        kind = STEP_KINDS.get(name)
        try:
            rc = _parse_int(rc_text) if re.fullmatch(r"-?[0-9]{1,6}", rc_text) else None
            secs_ok = re.fullmatch(r"[0-9]{1,7}(\.[0-9]{1,9})?", secs_text)
            seconds = float(secs_text) if secs_ok else None
            oom = _parse_int(oom_text) if re.fullmatch(r"-?[0-9]{1,9}", oom_text) else None
        except ValueError:
            rc = seconds = oom = None
        if kind is None or rc is None:
            result = "setup"
            detail = detail or f"unknown step or exit code ({name!r}, {rc_text!r})"
        elif oom is None or oom < 0:
            # The cgroup's oom_kill counter was not measured (memory.events unreadable) or went
            # backwards: an OOM kill cannot be ruled out, so the step is a setup error, never ok.
            result = "setup"
            detail = f"oom_kill not measured ({oom_text[:20]!r}); {detail}".rstrip("; ")
        else:
            result = classify_exit(rc, bool(oom and oom > 0), kind)
            if result == "setup" and not detail:
                detail = (
                    "an OOM kill (memory.events oom_kill grew)" if oom and oom > 0
                    else f"exit {rc} (signal, timeout or harness error)"
                )  # fmt: skip
        steps.append({"name": name, "result": result, "rc": rc, "seconds": seconds,
                      "oom_kill_delta": oom, "detail": detail[:500]})  # fmt: skip
    return steps


# ---------------------------------------------------------------------------------------------
# Baselines, contract, argv-derived help commands


def load_contract_entries(root: Path) -> dict[str, str]:
    """Return {version: accepted_by} from the tree's contract file, validated by the shipped parser.

    Any doubt is a CompatInputError (a SETUP-ERROR): the compat run never falls back silently.
    """
    parse = _shipped(root)._parse_contract
    raw = read_bytes(root / ".agents" / "config" / "openrig_contract.yml", 64 * 1024)
    try:
        contract = parse(raw)
    except Exception as exc:  # noqa: BLE001 - the shipped parser's own error, or any other doubt
        raise CompatInputError(f"openrig_contract.yml: {exc}") from None
    return {entry.version: entry.accepted_by for entry in contract.accepted}


_SHIPPED: dict[str, Any] = {}


def _shipped(root: Path) -> Any:
    """Import the tree's own hyper_openrig.py (once per root)."""
    key = str(root.resolve())
    if key not in _SHIPPED:
        scripts = root / ".agents" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location("hyper_openrig", scripts / "hyper_openrig.py")
        if spec is None or spec.loader is None:
            raise CompatInputError(f"{scripts}/hyper_openrig.py cannot be imported")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("hyper_openrig", module)
        spec.loader.exec_module(module)
        _SHIPPED[key] = module
    return _SHIPPED[key]


def select_baselines(root: Path, version: str) -> dict[str, str]:
    """Return {"baseline": v, "person_baseline": v} among the committed baseline dirs.

    Candidates: dev/tests/fixtures/openrig-compat/<v>/ with the same M.m and v <= version. The
    first is the newest; the second the newest whose contract entry is accepted_by: person. None
    is a CompatInputError naming --seed.
    """
    want = version_key(version)
    base_dir = root / "dev" / "tests" / "fixtures" / "openrig-compat"
    found: list[tuple[tuple[int, int, int], str]] = []
    if base_dir.is_dir():
        for child in base_dir.iterdir():
            if child.is_dir() and not child.is_symlink() and VERSION_RE.match(child.name):
                key = version_key(child.name)
                if key[:2] == want[:2] and key <= want:
                    found.append((key, child.name))
    if not found:
        raise CompatInputError(
            f"no baseline for {version} under dev/tests/fixtures/openrig-compat/ "
            f"(same {want[0]}.{want[1]}, <= {version}); run --seed"
        )
    found.sort()
    accepted = load_contract_entries(root)
    people = [name for _, name in found if accepted.get(name) == "person"]
    if not people:
        raise CompatInputError(
            f"no baseline for {version} whose contract entry is accepted_by: person; run --seed"
        )
    return {"baseline": found[-1][1], "person_baseline": people[-1]}


# Sample arguments for each argv_* builder: only the argv shape matters (the words and the flags).
_SENTINEL_WS = "/zzcompat-ws"
_SENTINELS = {"zzcompat-mission", "zzcompat-slug", "01-zzcompat-slice", "zzcompat text",
              "zzcompat.md", "OPR.0.6.2.1", _SENTINEL_WS}  # fmt: skip


def _builder_calls(h: Any) -> dict[str, tuple[tuple, dict]]:
    ws = Path(_SENTINEL_WS)
    return {
        "argv_scope_audit": ((["rig"], ws, "zzcompat-mission"), {}),
        "argv_slice_ls": ((["rig"], ws), {"mission": "zzcompat-mission", "state": "all"}),
        "argv_slice_create": (
            (["rig"], ws, "zzcompat-mission", "zzcompat-slug"),
            {"title": "zzcompat text", "intent": "zzcompat text", "depends_on": ["OPR.0.6.2.1"]},
        ),
        "argv_slice_close": (
            (["rig"], ws, "01-zzcompat-slice"),
            {"reason": h.CLOSE_REASONS[0], "mission": "zzcompat-mission", "note": "zzcompat text"},
        ),
        "argv_slice_progress": (
            (["rig"], ws, "01-zzcompat-slice"),
            {"add": "zzcompat text", "mission": "zzcompat-mission"},
        ),
        "argv_proof_add": (
            (["rig"], ws, "01-zzcompat-slice"),
            {"artifact_type": "qa", "verdict": "PASS", "candidate_sha": "a" * 40,
             "money_evidence": "zzcompat text", "file": Path("/zzcompat/body.md"),
             "name": "zzcompat.md", "mission": "zzcompat-mission"},
        ),
    }  # fmt: skip


def help_commands(root: Path) -> list[dict]:
    """Derive the rig commands HACF calls, with the flags it passes, from the argv_* builders.

    Each builder is called with sample arguments; its command words are the leading non-option
    tokens (the sample values left out) and its flags the `--x` tokens. `--workspace` belongs to
    the first word (`scope`, `proof`). Every parent command is listed too. A builder with no sample
    call is a CompatInputError, so a new builder cannot be silently left out.
    """
    h = _shipped(root)
    calls = _builder_calls(h)
    names = sorted(n for n in dir(h) if n.startswith("argv_") and callable(getattr(h, n)))
    unknown = sorted(set(names) - set(calls))
    if unknown:
        raise CompatInputError(f"argv builders with no sample call: {', '.join(unknown)}")
    flags: dict[tuple[str, ...], set[str]] = {}
    for name in names:
        args, kwargs = calls[name]
        try:
            argv = getattr(h, name)(*args, **kwargs)[1:]
        except Exception as exc:  # noqa: BLE001 - a builder refusing its sample is a setup error
            raise CompatInputError(f"{name}: the sample call failed: {exc}") from None
        words: list[str] = []
        i = 0
        while i < len(argv):
            token = argv[i]
            if token == "--workspace":
                flags.setdefault(tuple(words[:1]), set()).add(token)
                i += 2
                continue
            if token.startswith("-") or token in _SENTINELS:
                break
            words.append(token)
            i += 1
        if not words:
            raise CompatInputError(f"{name}: no command words in {argv}")
        for n in range(1, len(words) + 1):
            flags.setdefault(tuple(words[:n]), set())
        flags[tuple(words)].update(t for t in argv if t.startswith("--") and t != "--workspace")
    if not flags:
        raise CompatInputError("no argv_* builders: no rig command to check")
    return [{"words": list(w), "flags": sorted(f)} for w, f in sorted(flags.items())]


# ---------------------------------------------------------------------------------------------
# Host state: ~/.openrig, the cgroup's OOM count, the daemon-contact listener


def openrig_home_list(directory: Path) -> list[str]:
    """`<name> <mtime> <size>` of each regular file directly in directory, logs and dbs left out.

    The same fields as `stat -c '%n %Y %s'`. A missing directory lists nothing.
    """
    if not directory.is_dir():
        return []
    rows = []
    for n, entry in enumerate(sorted(os.scandir(directory), key=lambda e: e.name)):
        if n >= MAX_LIST_FILES:
            rows.append(f"... more than {MAX_LIST_FILES} entries")
            break
        if OPENRIG_HOME_EXCLUDED.search(entry.name):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            rows.append(f"{entry.path} {int(info.st_mtime)} {info.st_size}")
    return rows


def oom_kill_count(proc_cgroup: Path = Path("/proc/self/cgroup"),
                   cgroup_root: Path = Path("/sys/fs/cgroup")) -> int | None:  # fmt: skip
    """The oom_kill counter of this process's cgroup v2 memory.events, or None when unknown."""
    try:
        text = read_text(proc_cgroup, 64 * 1024)
    except CompatInputError:
        return None
    rel = None
    for line in text.splitlines():
        if line.startswith("0::"):
            rel = line[3:].strip()
    if not rel or ".." in rel.split("/"):
        return None
    try:
        events = read_text(cgroup_root / rel.lstrip("/") / "memory.events", 64 * 1024)
    except CompatInputError:
        return None
    for line in events.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill" and re.fullmatch(r"[0-9]{1,12}", parts[1]):
            return int(parts[1])
    return None


def serve(port_file: Path, log: Path, stop_after: float | None = None) -> int:
    """Listen on an ephemeral loopback port; log one line per accepted connection until SIGTERM.

    The port is written to port_file once listening. Each connection is logged (and fsynced)
    before it is closed, so the count survives the listener being stopped.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    log.touch()
    tmp = port_file.with_name(port_file.name + ".tmp")
    tmp.write_text(f"{sock.getsockname()[1]}\n", encoding="utf-8")
    os.replace(tmp, port_file)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    sock.settimeout(0.2)
    deadline = None if stop_after is None else _now() + stop_after
    while not stopping and (deadline is None or _now() < deadline):
        try:
            conn, peer = sock.accept()
        except (socket.timeout, InterruptedError):
            continue
        except OSError:
            continue
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.datetime.now(datetime.timezone.utc).isoformat()} {peer}\n")
            handle.flush()
            os.fsync(handle.fileno())
        conn.close()
    sock.close()
    return 0


def _now() -> float:
    return time.monotonic()


def connection_count(log: Path) -> int | None:
    """The number of connections the listener logged; None when the log is unreadable."""
    try:
        return len(read_text(log, MAX_TEXT_BYTES).splitlines())
    except CompatInputError:
        return None


# ---------------------------------------------------------------------------------------------
# The tree copy and the clone's contract edits


def copy_tree(src: Path, dst: Path, listing: bytes) -> int:
    """Copy the NUL-separated repo-relative paths of `listing` from src into dst.

    Missing paths (a tracked file deleted in the work tree) are skipped; symlinks are copied as
    links; a path that escapes the tree is a CompatInputError. Returns the number copied.
    """
    count = 0
    src_real = os.path.realpath(src)
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        if os.path.isabs(rel) or ".." in Path(rel).parts:
            raise CompatInputError(f"path {rel!r} escapes the tree")
        source = src / rel
        if not os.path.lexists(source):
            continue
        if not os.path.realpath(source.parent).startswith(src_real):
            raise CompatInputError(f"path {rel!r} resolves outside the tree")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            continue
        count += 1
    return count


_ENTRY_RE = re.compile(r'^\s*-\s*\{\s*version:\s*"(?P<v>[0-9.]+)".*\}\s*$')


def contract_unlist(path: Path, root: Path, version: str) -> str:
    """Remove version's accepted entry from a clone's contract file when the result stays valid.

    Returns "unlisted", "not-listed" (nothing to do) or "sole" (it is the only accepted version of
    its minor, or the edit would not parse: left as it is). The file is validated by the tree's
    own parser after the edit and restored on any doubt.
    """
    check_version(version)
    original = read_bytes(path, 64 * 1024)
    lines = original.decode("utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if (m := _ENTRY_RE.match(ln)) is None or m.group("v") != version]
    if len(kept) == len(lines):
        return "not-listed"
    new = "".join(kept).encode("utf-8")
    try:
        _shipped(root)._parse_contract(new)
    except Exception:  # noqa: BLE001 - an edit that would not parse is not made
        return "sole"
    path.write_bytes(new)
    return "unlisted"


def contract_add_watcher(
    path: Path, root: Path, version: str, integrity: str, verdict: str
) -> None:
    """Append version to a clone's contract file as an accepted_by: watcher entry (validated)."""
    check_version(version)
    for what, text in (("integrity", integrity), ("verdict", verdict)):
        if not re.fullmatch(r"[A-Za-z0-9+/=._:-]{1,200}", text):
            raise CompatInputError(f"{what} {text[:60]!r} is not a plain token")
    original = read_bytes(path, 64 * 1024).decode("utf-8")
    lines = original.splitlines(keepends=True)
    last = max((i for i, ln in enumerate(lines) if _ENTRY_RE.match(ln)), default=None)
    if last is None:
        raise CompatInputError(f"{path}: no accepted entry line to append after")
    entry = (
        f'  - {{version: "{version}", integrity: {json.dumps(integrity)}, '
        f"verdict: {json.dumps(verdict)}, accepted_by: watcher}}\n"
    )
    new = "".join([*lines[: last + 1], entry, *lines[last + 1 :]]).encode("utf-8")
    try:
        contract = _shipped(root)._parse_contract(new)
    except Exception as exc:  # noqa: BLE001
        raise CompatInputError(f"the watcher entry does not parse: {exc}") from None
    if version not in contract.versions:
        raise CompatInputError("the watcher entry was not read back")
    path.write_bytes(new)


def in_range(root: Path, version: str) -> bool:
    """True when version lies in the tree's contract range (unlisted patch >= min_patch)."""
    h = _shipped(root)
    contract = h._parse_contract(read_bytes(root / ".agents/config/openrig_contract.yml", 65536))
    return bool(h._in_range(version_key(version), contract))


def probe_result(text: str, expect_in_range: bool) -> tuple[bool, str]:
    """Judge `contract probe --json` output: pass is required in range; out of range is n/a."""
    try:
        data = loads_json(text.strip() or "null", "contract probe --json")
    except CompatInputError as exc:
        return False, str(exc)
    if not isinstance(data, dict):
        return False, "contract probe --json printed no JSON object"
    outcome = data.get("outcome")
    if expect_in_range:
        return outcome == "pass", f"probe outcome {outcome!r} (in range: pass required)"
    return outcome is None, f"n/a: outside the range (outcome {outcome!r})"


# ---------------------------------------------------------------------------------------------
# Package files


def package_meta(package: Path, version: str) -> dict:
    """Read the installed package.json; its name and version must match the candidate."""
    data = loads_json(read_text(package / "package.json"), f"{package}/package.json")
    if not isinstance(data, dict):
        raise CompatInputError(f"{package}/package.json: not an object")
    if data.get("name") != RIG_PACKAGE:
        raise CompatInputError(f"{package}/package.json: name {str(data.get('name'))[:60]!r}")
    if data.get("version") != version:
        raise CompatInputError(
            f"{package}/package.json: version {str(data.get('version'))[:40]!r}, not {version}"
        )
    return {"name": data["name"], "version": data["version"]}


def integrity_from_npm(text: str) -> str:
    """Validate `npm view <pkg> dist.integrity` output: one sha512-<base64> token."""
    value = text.strip()
    if not re.fullmatch(r"sha512-[A-Za-z0-9+/]{80,100}={0,2}", value):
        raise CompatInputError(f"npm view printed {value[:80]!r}, not a sha512 integrity")
    return value


# ---------------------------------------------------------------------------------------------
# The steps the shell calls


def _status(problems: list[str], setup: list[str]) -> int:
    return EXIT_SETUP if setup else (EXIT_FAIL if problems else EXIT_OK)


def run_captures(root: Path, recorded: Path, baselines: list[str], roots: tuple[str, ...],
                 out: Path) -> int:  # fmt: skip
    """Compare the candidate's captures with each baseline's; write the result to out."""
    result: dict[str, Any] = {"per_baseline": {}, "json_paths_added": {}, "setup": []}
    if not baselines:
        result["setup"].append("no baseline to compare with")
        write_json(out, result)
        return EXIT_SETUP
    try:
        cand = load_capture_dir(recorded, roots)
    except CompatInputError as exc:
        result["problems"] = [f"candidate captures: {exc}"]
        write_json(out, result)
        return EXIT_FAIL
    problems: list[str] = []
    for version in dict.fromkeys(baselines):
        try:
            base = load_capture_dir(baseline_dir(root, version) / "captures")
            got = compare_captures(base, cand)
        except CompatInputError as exc:
            result["setup"].append(f"baseline {version}: {exc}")
            continue
        result["per_baseline"][version] = got
        problems += [f"vs {version}: {p}" for p in got["problems"]]
        for name, paths in got["json_paths_added"].items():
            result["json_paths_added"].setdefault(name, sorted(set(paths)))
    result["problems"] = problems
    write_json(out, result)
    return _status(problems, result["setup"])


def baseline_list(path: Path) -> list[str]:
    """The two baselines select-baselines chose, from its JSON; any doubt is a CompatInputError."""
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise CompatInputError(f"{path}: not a JSON object")
    return [check_version(data.get(key)) for key in ("baseline", "person_baseline")]


def baseline_dir(root: Path, version: str) -> Path:
    """The committed baseline dir of one version."""
    return root / "dev" / "tests" / "fixtures" / "openrig-compat" / check_version(version)


def capture_help(rig: str, commands: list[dict], out_dir: Path, timeout: float = 60) -> list[str]:
    """Run `rig <words> --help` for each command into out_dir; return the failures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for item in commands:
        argv = [rig, *item["words"], "--help"]
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise CompatInputError(f"{' '.join(argv[1:])}: timed out after {timeout} s") from None
        if proc.returncode < 0 or proc.returncode >= 128:
            raise CompatInputError(f"{' '.join(argv[1:])}: killed (exit {proc.returncode})")
        text = proc.stdout.decode("utf-8", "replace")
        (out_dir / help_name(item["words"])).write_text(text, encoding="utf-8")
        if proc.returncode != 0:
            failures.append(f"{' '.join(item['words'])} --help exited {proc.returncode}")
    return failures


def run_help(root: Path, rig: str, out_dir: Path, baselines: list[str], out: Path,
             seed: bool = False) -> int:  # fmt: skip
    """Capture the help of each derived command and diff it against each baseline.

    Outside --seed an empty baseline list is a setup error; in --seed each captured text must be
    whole (check_help_text), because it becomes the next baseline.
    """
    result: dict[str, Any] = {"commands": [], "per_baseline": {}, "setup": [], "problems": []}
    try:
        if not baselines and not seed:
            raise CompatInputError("no baseline to compare with")
        commands = help_commands(root)
        result["commands"] = commands
        problems = capture_help(rig, commands, out_dir)
    except CompatInputError as exc:
        result["setup"].append(str(exc))
        write_json(out, result)
        return EXIT_SETUP
    for item in commands:  # the candidate alone: every flag HACF passes is listed
        text = (out_dir / help_name(item["words"])).read_text(encoding="utf-8")
        missing = sorted(set(item["flags"]) - set(parse_help(text)["flags"]))
        if missing:
            problems.append(f"{' '.join(item['words'])}: HACF's flags missing: {missing}")
        if seed:
            try:
                check_help_text(text, f"candidate {' '.join(item['words'])} --help")
            except CompatInputError as exc:
                result["setup"].append(str(exc))
    for version in dict.fromkeys(baselines):
        diffs = {}
        for item in commands:
            name = help_name(item["words"])
            try:
                base_text = read_text(baseline_dir(root, version) / "help" / name)
            except CompatInputError as exc:
                result["setup"].append(f"baseline {version}: {exc}")
                continue
            cand_text = (out_dir / name).read_text(encoding="utf-8")
            try:
                got = help_diff(base_text, cand_text, item["flags"])
            except CompatInputError as exc:
                result["setup"].append(f"baseline {version} {name}: {exc}")
                continue
            diffs[" ".join(item["words"])] = got
            if not got["ok"]:
                problems.append(f"vs {version}: {' '.join(item['words'])}: {got}"[:600])
        result["per_baseline"][version] = diffs
    result["problems"] = problems
    write_json(out, result)
    return _status(problems, result["setup"])


def run_watched(root: Path, package: Path, baselines: list[str], out: Path, diff_dir: Path) -> int:
    """Compare the 8 watched items with both baselines; any difference makes the verdict REVIEW."""
    result: dict[str, Any] = {"watched": [list(w) for w in WATCHED], "setup": []}
    if not baselines:
        result["setup"].append("no baseline to compare with")
    cand = watched_bytes(package)
    loaded = {}
    for version in dict.fromkeys(baselines):
        try:
            loaded[version] = load_baseline_watched(baseline_dir(root, version))
        except CompatInputError as exc:
            result["setup"].append(f"baseline {version}: {exc}")
    differs, diffs = compare_watched(cand, loaded)
    diff_dir.mkdir(parents=True, exist_ok=True)
    for name, text in diffs.items():
        (diff_dir / f"watched-{name}.diff").write_text(text, encoding="utf-8")
    result["watched_diff"] = differs
    write_json(out, result)
    return EXIT_SETUP if result["setup"] else EXIT_OK  # a difference is REVIEW, not a step failure


def write_baseline_candidate(state: Path, package: Path, dest: Path, meta: dict,
                             roots: tuple[str, ...]) -> list[str]:  # fmt: skip
    """Write baseline-candidate/: normalised captures, help texts, watched items and meta.json."""
    problems = []
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "captures").mkdir(parents=True)
    try:
        for name, data in load_capture_dir(state / "recorded", roots).items():
            write_json(dest / "captures" / name, data)
    except CompatInputError as exc:
        problems.append(f"captures: {exc}")
    if (state / "help").is_dir():
        shutil.copytree(state / "help", dest / "help")
    else:
        problems.append("help: no help texts")
    (dest / "watched").mkdir()
    for key, data in watched_bytes(package).items():
        if data is None:
            problems.append(f"watched: {key} missing")
        else:
            (dest / "watched" / watched_file_name(key)).write_bytes(data)
    write_json(dest / "meta.json", meta)
    return problems


HELP_DIFF_LISTS = ("removed_flags", "added_flags", "removed_commands", "added_commands",
                   "hacf_flags_missing")  # fmt: skip


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _paths_added(value: dict, what: str) -> None:
    """json_paths_added is {capture name: [key path, ...]}, as compare_captures writes it."""
    if not all(_strs(paths) and paths for paths in value.values()):
        raise CompatInputError(f"{what}: json_paths_added is not {{capture: [key paths]}}")


def check_state(name: str, data: Any, chosen: list[str], step_ok: bool, seed: bool) -> None:
    """Validate one required state JSON against its schema; any doubt is a CompatInputError.

    The check never depends on truthiness: `{}`, `[]`, `""`, null and a missing key are all errors.
    Required keys (including each step's own `setup` and `problems` lists, which the steps always
    write) must be present with their types, required collections non-empty, and (outside
    --seed) per_baseline must cover every chosen baseline. When the file's step passed, the file
    must not contradict it: no recorded problem, no failed comparison and an entry for every
    command and baseline. A file that records a setup error is a setup error.
    """
    if not isinstance(data, dict):
        raise CompatInputError(f"{name}: not a JSON object")
    if name != "baselines.json":  # the step's own lists: setup always, problems where compared
        for key in ("setup", "problems") if name != "watched.json" else ("setup",):
            if not _strs(_expect(data, key, (list,), name)):
                raise CompatInputError(f"{name}: {key} is not a list of strings")
    if data.get("setup"):
        raise CompatInputError(f"{name}: its step recorded a setup error: {data['setup'][0]}")
    if step_ok and data.get("problems"):
        raise CompatInputError(f"{name}: records problems but its step passed")
    if name == "baselines.json":
        for key in ("baseline", "person_baseline"):
            check_version(data.get(key))
        return
    if name == "watched.json":
        keys = {key for key, _, _ in WATCHED}
        for key, per in _expect(data, "watched_diff", (dict,), name).items():
            if key not in keys or not isinstance(per, dict) or not per or not all(
                isinstance(v, str) and v in ("differs", "missing") for v in per.values()
            ):  # fmt: skip
                raise CompatInputError(f"{name}: watched_diff {str(key)[:40]!r} is malformed")
        return
    per = _expect(data, "per_baseline", (dict,), name)
    if not seed and not set(chosen) <= set(per):
        raise CompatInputError(f"{name}: not compared with every chosen baseline {chosen}")
    if name == "captures.json":
        _paths_added(_expect(data, "json_paths_added", (dict,), name), name)
        for version, got in per.items():
            what = f"{name}: per_baseline {str(version)[:20]}"
            if not isinstance(got, dict):
                raise CompatInputError(f"{what}: not an object")
            ok = _expect(got, "ok", (bool,), what)
            if not _strs(_expect(got, "problems", (list,), what)):
                raise CompatInputError(f"{what}: problems is not a list of strings")
            _paths_added(_expect(got, "json_paths_added", (dict,), what), what)
            if step_ok and not ok:
                raise CompatInputError(f"{what}: the comparison failed but its step passed")
        return
    # help.json
    commands = _expect(data, "commands", (list,), name)
    if not commands:
        raise CompatInputError(f"{name}: no rig command was checked")
    words = []
    for item in commands:
        if not (isinstance(item, dict) and _strs(item.get("words")) and item["words"]
                and _strs(item.get("flags"))):  # fmt: skip
            raise CompatInputError(f"{name}: a command is not {{words: [..], flags: [..]}}")
        words.append(" ".join(item["words"]))
    for version, diffs in per.items():
        what = f"{name}: per_baseline {str(version)[:20]}"
        if not isinstance(diffs, dict):
            raise CompatInputError(f"{what}: not an object")
        if step_ok and not set(words) <= set(diffs):
            raise CompatInputError(f"{what}: not every command was compared")
        for command, diff in diffs.items():
            if not isinstance(diff, dict):
                raise CompatInputError(f"{what} {str(command)[:40]}: not an object")
            ok = _expect(diff, "ok", (bool,), f"{what} {command}")
            for key in HELP_DIFF_LISTS:
                if not _strs(_expect(diff, key, (list,), f"{what} {command}")):
                    raise CompatInputError(f"{what} {command}: {key} is not a list of strings")
            if step_ok and not ok:
                raise CompatInputError(f"{what} {command}: the diff failed but its step passed")


def check_seed_help_texts(state: Path, commands: list[dict]) -> None:
    """In --seed, each captured help text becomes a baseline: every one must be whole."""
    for item in commands:
        name = help_name(item["words"])
        check_help_text(read_text(state / "help" / name), f"help/{name}")


def _obj(value: Any) -> dict:
    """Return value when it is an object, else {} (the report's informational fields)."""
    return value if isinstance(value, dict) else {}


def report(ns: argparse.Namespace) -> int:
    """Assemble verdict.json, REPORT.md and baseline-candidate/; print the final line."""
    state, evidence = Path(ns.state), Path(ns.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    steps = read_steps(state / "steps.tsv")

    def setup(name: str, detail: str) -> None:
        steps.append({"name": name, "result": "setup", "rc": None, "seconds": None,
                      "detail": detail[:500]})  # fmt: skip

    def part(name: str) -> dict | None:
        """A required state JSON object; missing, unreadable or not an object is a setup step."""
        if not (state / name).exists():
            setup(name, f"{name}: missing (its step wrote nothing)")
            return None
        try:
            data = load_json_file(state / name)
        except CompatInputError as exc:
            setup(name, str(exc))
            return None
        if not isinstance(data, dict):
            setup(name, f"{name}: not a JSON object")
            return None
        return data

    compared = not ns.seed
    # baselines.json first: the others must cover the baselines it chose
    required = (["baselines.json", "watched.json", "captures.json"] if compared else []) + [
        "help.json"
    ]  # fmt: skip
    loaded = {name: part(name) for name in required}
    data: dict[str, dict] = {name: {} for name in required}
    chosen: list[str] = ["(unknown)"]  # until baselines.json is read: no per_baseline covers it
    results = {s["name"]: s["result"] for s in steps}
    for name in required:  # every required file against its schema, empty or not
        if loaded[name] is None:
            continue  # part() already recorded the setup step
        data[name] = loaded[name]
        try:
            check_state(name, data[name], chosen, results.get(name[:-5]) == "ok", ns.seed)
            if name == "baselines.json":
                chosen = sorted(set(baseline_list(state / name)))
            if name == "help.json" and ns.seed:
                check_seed_help_texts(state, data[name]["commands"])
        except CompatInputError as exc:
            setup(name, str(exc))
            data[name] = {}
    watched, helped = data.get("watched.json", {}), data["help.json"]
    captured, baselines = data.get("captures.json", {}), data.get("baselines.json", {})
    meta = {
        "version": ns.version,
        "integrity": ns.integrity,
        "rig_version_output": ns.rig_version_output,
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "help_commands": helped.get("commands", []),
        "watched": [list(w) for w in WATCHED],
        "hacf_tree": ns.tree_sha,
    }
    package = Path(ns.package)
    if package.is_dir() and (state / "recorded").is_dir():
        problems = write_baseline_candidate(state, package, evidence / "baseline-candidate",
                                            meta, tuple(ns.root_alias))  # fmt: skip
        if problems:
            steps.append({"name": "baseline-candidate", "result": "setup", "rc": None,
                          "seconds": None, "detail": "; ".join(problems)[:500]})  # fmt: skip
    watched_diff = watched.get("watched_diff", {})
    verdict, failed, setup_error = decide(steps, watched_diff, ns.seed)
    if failed == SPEC_VALIDATE_STEP:
        failed = SPEC_INVALID
    spec_validate = read_spec_validate(state)
    timings = {s["name"]: s["seconds"] for s in steps if s.get("seconds") is not None}
    out = {
        "version": ns.version,
        "verdict": verdict,
        "failed_step": failed,
        "baseline": baselines.get("baseline"),
        "person_baseline": baselines.get("person_baseline"),
        "watched_diff": watched_diff,
        "help_diff": {v: {c: d for c, d in diffs.items()
                          if isinstance(d, dict) and (
                              d.get("removed_flags") or d.get("added_flags")
                              or d.get("removed_commands") or d.get("added_commands")
                              or d.get("hacf_flags_missing"))}
                      for v, diffs in _obj(helped.get("per_baseline")).items()
                      if isinstance(diffs, dict)},
        "json_paths_added": _obj(captured.get("json_paths_added")),
        "setup_error": setup_error,
        "spec_validate": spec_validate,
        "timings": timings,
        "steps": steps,
        "integrity": ns.integrity,
        "problems": {"captures": captured.get("problems", []), "help": helped.get("problems", [])},
    }  # fmt: skip
    write_json(evidence / "verdict.json", out)
    rows = ["| step | result | exit | time (s) | detail |", "|---|---|---|---|---|"]
    for s in steps:
        secs = "" if s.get("seconds") is None else f"{s['seconds']:.1f}"
        detail = str(s.get("detail") or "").replace("|", "\\|")
        rows.append(f"| {s['name']} | {s['result']} | {s.get('rc')} | {secs} | {detail} |")
    lines = [
        f"# openrig-compat {ns.version}: {verdict}",
        "",
        f"Baselines: newest {out['baseline']}, newest person-accepted {out['person_baseline']}."
        f" Integrity: {ns.integrity}.",
        "",
        *rows,
        "",
    ]
    if failed:
        lines.append(f"Failed step: {failed}.")
    if setup_error:
        lines.append(f"Setup error: {setup_error}.")
    if watched_diff:
        lines.append(f"Watched items that differ (REVIEW): {json.dumps(watched_diff)}.")
    if spec_validate is None:
        lines.append("Spec validate: not recorded.")
    else:
        lines.append(
            f"Spec validate (openrig/hacf/rig.yaml): exit {spec_validate['exit']},"
            f" valid {json.dumps(spec_validate['valid'])}."
        )
    (evidence / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tail = f" {failed}" if verdict == "FAIL" else ""
    print(f"openrig-compat {ns.version}: {verdict}{tail} ({evidence})")
    return VERDICT_EXIT[verdict]


# ---------------------------------------------------------------------------------------------
# Auto-accept (release-0.6.2 R15; slice 4)

ACCEPT_BRANCH = "swimlane/openrig-accepted"
CONTRACT_PATH = ".agents/config/openrig_contract.yml"
FIXTURE_ROOT = "dev/tests/fixtures/openrig-compat"
# More than this many consecutive watcher entries after the newest person entry of a minor force
# REVIEW (F13: the 4th in a row needs a person).
MAX_WATCHER_STREAK = 3
# The verdict.json keys of mission §5.4, with the types report() writes them with.
VERDICT_KEYS: dict[str, tuple[type, ...]] = {
    "version": (str,), "verdict": (str,), "failed_step": (str, type(None)),
    "baseline": (str, type(None)), "person_baseline": (str, type(None)), "watched_diff": (dict,),
    "help_diff": (dict,), "json_paths_added": (dict,), "setup_error": (str, type(None)),
    "timings": (dict,), "steps": (list,), "integrity": (str,),
}  # fmt: skip


def is_patch_of_accepted_minor(version: Any, contract: Any) -> bool:
    """True when version is a plain M.m.p whose M.m is accepted and whose p is above every patch.

    `contract` is the shipped parser's Contract (or anything with `.accepted` entries carrying a
    `.version`); watcher entries count like person entries (mission R15). A prerelease, build tag,
    `v` prefix, leading zero or empty string is never a patch.
    """
    if not isinstance(version, str) or not VERSION_RE.match(version):
        return False
    major, minor, patch = version_key(version)
    patches = []
    for entry in getattr(contract, "accepted", ()):
        text = getattr(entry, "version", None)
        if isinstance(text, str) and VERSION_RE.match(text):
            key = version_key(text)
            if key[:2] == (major, minor):
                patches.append(key[2])
    return bool(patches) and patch > max(patches)


def watcher_streak(version: str, contract: Any) -> int:
    """Count the accepted_by: watcher entries above the newest person entry of version's minor."""
    want = version_key(version)[:2]
    same = [(version_key(e.version), e.accepted_by) for e in contract.accepted
            if VERSION_RE.match(e.version) and version_key(e.version)[:2] == want]  # fmt: skip
    people = [key for key, by in same if by == "person"]
    floor = max(people) if people else (-1, -1, -1)
    return sum(1 for key, by in same if by == "watcher" and key > floor)


def load_pass_verdict(evidence: Path, version: str) -> dict:
    """Return evidence/verdict.json when it is a schema-valid green PASS for version; else raise.

    The accept fails closed (slice 3's lesson): a missing, unparseable or wrongly shaped file, any
    verdict but PASS, a failed step, a setup error, a watched diff, a step that is not ok, a
    required step that never ran, or an integrity that is not a registry sha512 all refuse.
    """
    data = load_json_file(evidence / "verdict.json")
    if not isinstance(data, dict):
        raise CompatInputError("verdict.json: not a JSON object")
    for key, kinds in VERDICT_KEYS.items():
        _expect(data, key, kinds, "verdict.json")
    if data["version"] != version:
        raise CompatInputError(f"verdict.json: version {data['version'][:40]!r}, not {version}")
    if data["verdict"] != "PASS":
        raise CompatInputError(f"verdict.json: verdict {data['verdict'][:40]!r}, not PASS")
    if data["failed_step"] is not None or data["setup_error"] is not None or data["watched_diff"]:
        raise CompatInputError("verdict.json: PASS with a failed step, setup error or watched diff")
    for key in ("baseline", "person_baseline"):
        check_version(data[key])
    ran = set()
    for step in data["steps"]:
        if not isinstance(step, dict) or step.get("result") != "ok":
            raise CompatInputError("verdict.json: a step is not ok")
        ran.add(step.get("name"))
    missing = [name for name in REQUIRED_STEPS if name not in ran]
    if missing:
        raise CompatInputError(f"verdict.json: steps never ran: {', '.join(missing)}")
    integrity_from_npm(data["integrity"])
    if "accept" in data:
        raise CompatInputError("verdict.json: already has an accept result")
    return data


def _git(root: Path, *args: str, check: bool = True) -> str:
    """Run git in root; return stdout. A failure raises CompatInputError naming the command."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        why = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1][:200]
        raise CompatInputError(f"git {' '.join(args[:3])}: {why}")
    return proc.stdout


def _porcelain(root: Path) -> list[str]:
    """List the paths `git status --porcelain=v1 --untracked-files=all -z` reports."""
    items = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z").split("\0")
    paths, i = [], 0
    while i < len(items):
        item = items[i]
        i += 1
        if not item:
            continue
        paths.append(item[3:])
        if item[0] in "RC":  # a rename or copy: its source path follows as its own field
            if i < len(items) and items[i]:
                paths.append(items[i])
            i += 1
    return paths


def _record_accept(evidence: Path, result: dict) -> None:
    """Write the accept result into verdict.json (`accept`), or accept.json if it is unreadable."""
    try:
        data = load_json_file(evidence / "verdict.json")
    except CompatInputError:
        data = None
    if isinstance(data, dict):
        data["accept"] = result
        write_json(evidence / "verdict.json", data)
    else:
        write_json(evidence / "accept.json", result)


def accept(root: Path, version: str, evidence: Path, test_argv: list[str], *,
           expect_head: str | None = None, started_clean: bool = True) -> dict:  # fmt: skip
    """Commit a PASS patch of an accepted minor to the watcher's branch (mission R15).

    Returns {"result": ..., "reason": ..., "sha": ...}; it never pushes and never commits unless
    every check holds and the test argv exits 0 on the tree to be committed. The commit holds
    exactly the contract file and dev/tests/fixtures/openrig-compat/<version>/; on any refusal after
    the tree was touched, both are restored (`git checkout -- <file>`, `rm -rf <dir>`).
    """

    def done(result: str, reason: str = "", sha: str | None = None) -> dict:
        return {"result": result, "reason": reason[:500], "sha": sha}

    try:
        check_version(version)
        data = load_pass_verdict(evidence, version)
    except CompatInputError as exc:
        return done("not-pass", str(exc))
    try:
        top = _git(root, "rev-parse", "--show-toplevel").strip()
        if os.path.realpath(top) != os.path.realpath(root):
            return done("not-a-worktree", f"{root} is not the top of a git work tree")
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        if branch != ACCEPT_BRANCH:
            return done("wrong-branch", f"on {branch or 'a detached HEAD'!r}, not {ACCEPT_BRANCH}")
        if not started_clean or _porcelain(root):
            return done("dirty-tree", "the work tree was not clean (git status --porcelain)")
        head = _git(root, "rev-parse", "HEAD").strip()
        if expect_head is not None and head != expect_head:
            return done("head-moved", f"HEAD is {head[:12]}, the run tested {expect_head[:12]}")
        contract = _shipped(root)._parse_contract(read_bytes(root / CONTRACT_PATH, 64 * 1024))
    except CompatInputError as exc:
        return done("error", str(exc))
    except Exception as exc:  # noqa: BLE001 - the shipped parser's refusal: no fallback here
        return done("error", f"openrig_contract.yml: {exc}")
    if not is_patch_of_accepted_minor(version, contract):
        return done("not-a-patch", f"{version} is not a patch above an accepted minor's patches")
    streak = watcher_streak(version, contract)
    if streak >= MAX_WATCHER_STREAK:
        return done("review-streak", f"{streak} watcher entries already follow the person entry")
    candidate = evidence / "baseline-candidate"
    fixture = root / FIXTURE_ROOT / version
    contract_file = root / CONTRACT_PATH
    if not candidate.is_dir() or candidate.is_symlink():
        return done("error", "the evidence has no baseline-candidate/")
    if os.path.lexists(fixture):
        return done("error", f"{FIXTURE_ROOT}/{version} already exists")

    touched = False

    def restore() -> None:
        _git(root, "reset", "-q", "--", CONTRACT_PATH, f"{FIXTURE_ROOT}/{version}", check=False)
        _git(root, "checkout", "--", CONTRACT_PATH, check=False)
        shutil.rmtree(fixture, ignore_errors=True)

    try:
        touched = True
        contract_add_watcher(contract_file, root, version, data["integrity"], evidence.name)
        shutil.copytree(candidate, fixture, symlinks=True)
        rc = subprocess.run(test_argv, cwd=root, check=False).returncode  # noqa: S603
        if rc != 0:
            restore()
            return done("tests-failed", f"{' '.join(test_argv)[:200]} exited {rc}")
        fixture_files = sorted(
            str(p.relative_to(root)) for p in fixture.rglob("*") if p.is_file() or p.is_symlink()
        )
        expected = sorted([CONTRACT_PATH, *fixture_files])
        if sorted(_porcelain(root)) != expected:
            restore()
            return done("error", "the tests changed the tree beyond the two accept paths")
        _git(root, "add", "--", CONTRACT_PATH, f"{FIXTURE_ROOT}/{version}")
        staged = sorted(_git(root, "diff", "--cached", "--name-only", "-z").split("\0")[:-1])
        if staged != expected:
            restore()
            return done("error", f"staged {len(staged)} paths, not exactly the two accept paths")
        message = evidence / "accept-commit-message.txt"
        message.write_text(f"openrig-compat: accept rig {version} (watcher)\n\n"
                           f"HACF-Compat-Verdict: {evidence.name}\n", encoding="utf-8")  # fmt: skip
        proc = subprocess.run(  # noqa: S603 - the repository's hooks run (no hook bypass)
            ["git", "-C", str(root), "commit", "-q", "-F", str(message)],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if proc.returncode != 0:
            restore()
            why = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1][:200]
            return done("commit-failed", why)
        touched = False
        sha = _git(root, "rev-parse", "HEAD").strip()
        shown = sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split())
        if shown != expected:  # never amend or reset a commit: report it
            return done("accepted-unexpected", f"commit {sha[:12]} holds {len(shown)} paths", sha)
        return done("accepted", f"one commit on {ACCEPT_BRANCH}", sha)
    except (CompatInputError, OSError, subprocess.SubprocessError) as exc:
        if touched:
            restore()
        return done("error", str(exc))


# ---------------------------------------------------------------------------------------------
# Watcher state (dev/openrig_watch.sh; mission R16)

COUNTER_DEFAULTS: dict[str, Any] = {
    "deferred": 0, "setup_errors": 0, "no_verdict": 0, "no_verdict_notified": False,
    "fails": {}, "integrity_alerts": {},
}  # fmt: skip
MAX_COUNT = 1_000_000


def _counters_ok(data: Any) -> bool:
    def count(v: Any) -> bool:
        return type(v) is int and 0 <= v <= MAX_COUNT

    if not isinstance(data, dict) or set(data) != set(COUNTER_DEFAULTS):
        return False
    if not all(count(data[k]) for k in ("deferred", "setup_errors", "no_verdict")):
        return False
    if type(data["no_verdict_notified"]) is not bool:
        return False
    fails, alerts = data["fails"], data["integrity_alerts"]
    if not isinstance(fails, dict) or not all(
        VERSION_RE.match(k) and count(v) for k, v in fails.items()
    ):  # fmt: skip
        return False
    return isinstance(alerts, dict) and all(
        VERSION_RE.match(k) and _strs(v) and len(v) <= 100 for k, v in alerts.items()
    )  # fmt: skip


def load_counters(state: Path) -> tuple[dict, str | None]:
    """counters.json, or the defaults; an unreadable file is moved aside with a warning."""
    path = state / "counters.json"
    if not path.exists():
        return json.loads(json.dumps(COUNTER_DEFAULTS)), None
    try:
        data = load_json_file(path, 1024 * 1024)
        if _counters_ok(data):
            return data, None
        why = "not the counters schema"
    except CompatInputError as exc:
        why = str(exc)
    aside = path.with_name(
        f"counters.json.bad-{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%S%fZ}"
    )
    os.replace(path, aside)
    return json.loads(
        json.dumps(COUNTER_DEFAULTS)
    ), f"counters.json unreadable ({why}); moved to {aside.name}"


def watch_state(state: Path, event: str, version: str | None, value: str | None) -> str:
    """Apply one watcher event to counters.json; return what the shell should do.

    verdict: any verdict resets the runs-without-a-verdict counters. deferred / setup: count one
    run without a verdict; returns `notify <n>` the first time n reaches 2, else `quiet <n>`.
    fail: count a FAIL of version; returns the count. final: clear version's FAIL count.
    integrity: `new` the first time (version, value) is seen, else `seen`.
    """
    state.mkdir(parents=True, exist_ok=True)
    data, warning = load_counters(state)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    out = "ok"
    if event == "verdict":
        data.update(deferred=0, setup_errors=0, no_verdict=0, no_verdict_notified=False)
    elif event in ("deferred", "setup"):
        key = "deferred" if event == "deferred" else "setup_errors"
        data[key] = min(data[key] + 1, MAX_COUNT)
        data["no_verdict"] = min(data["no_verdict"] + 1, MAX_COUNT)
        if data["no_verdict"] >= 2 and not data["no_verdict_notified"]:
            data["no_verdict_notified"] = True
            out = f"notify {data['no_verdict']}"
        else:
            out = f"quiet {data['no_verdict']}"
    elif event == "fail":
        check_version(version)
        data["fails"][version] = min(data["fails"].get(version, 0) + 1, MAX_COUNT)
        out = str(data["fails"][version])
    elif event == "final":
        check_version(version)
        data["fails"].pop(version, None)
    elif event == "integrity":
        check_version(version)
        integrity_from_npm(value or "")
        seen = data["integrity_alerts"].setdefault(version, [])
        out = "seen" if value in seen else "new"
        if out == "new":
            seen.append(value)
            del seen[:-100]
    else:
        raise CompatInputError(f"unknown watcher event {event!r}")
    write_json(state / "counters.json", data)
    return out


def watch_final(state: Path, version: str) -> bool:
    """True when verdicts/<version>.json records a final verdict (any doubt: not final)."""
    try:
        data = load_json_file(state / "verdicts" / f"{check_version(version)}.json", 1024 * 1024)
    except CompatInputError:
        return False
    return isinstance(data, dict) and data.get("version") == version and data.get("final") is True


def watch_verdict(evidence: Path, version: str, rc: int) -> tuple[str, str, str]:
    """Read a compat run's verdict.json for the watcher: (verdict, accept result, detail).

    Fails closed: anything that is not a schema-valid verdict.json for version whose verdict
    agrees with the exit code is SETUP-ERROR. A PASS without an accept result is `accept-missing`.
    """
    try:
        data = load_json_file(evidence / "verdict.json")
        if not isinstance(data, dict):
            raise CompatInputError("verdict.json: not a JSON object")
        for key, kinds in VERDICT_KEYS.items():
            _expect(data, key, kinds, "verdict.json")
    except CompatInputError as exc:
        return "SETUP-ERROR", "-", str(exc)
    verdict = data["verdict"]
    if data["version"] != version or verdict not in ("PASS", "REVIEW", "FAIL", "SETUP-ERROR"):
        return "SETUP-ERROR", "-", f"verdict.json: {verdict[:20]!r} for {data['version'][:20]!r}"
    accepted = data.get("accept")
    result = accepted.get("result") if isinstance(accepted, dict) else None
    result = result if isinstance(result, str) and re.fullmatch(r"[a-z-]{1,40}", result) else None
    want = VERDICT_EXIT[verdict]
    if verdict == "PASS" and result == "review-streak":
        want = VERDICT_EXIT["REVIEW"]
    if rc != want:
        return "SETUP-ERROR", "-", f"exit {rc} does not match verdict.json {verdict}"
    if verdict == "SETUP-ERROR":
        return verdict, "-", str(data["setup_error"] or "setup error")[:300]
    if verdict == "FAIL":
        return verdict, "-", str(data["failed_step"] or "?")[:100]
    if verdict == "PASS":
        reason = accepted.get("reason", "") if isinstance(accepted, dict) else ""
        return verdict, result or "accept-missing", str(reason)[:300]
    return verdict, "-", ""


def contract_entries(root: Path) -> list[tuple[str, str, str]]:
    """(version, integrity, accepted_by) of the tree's contract file, by the shipped parser."""
    try:
        contract = _shipped(root)._parse_contract(read_bytes(root / CONTRACT_PATH, 64 * 1024))
    except CompatInputError:
        raise
    except Exception as exc:  # noqa: BLE001 - the shipped parser's refusal
        raise CompatInputError(f"openrig_contract.yml: {exc}") from None
    return [(e.version, e.integrity, e.accepted_by) for e in contract.accepted]


def npm_version(text: str) -> str:
    """Validate `npm view @openrig/cli version`: exactly one plain M.m.p line."""
    value = text.strip()
    if "\n" in value:
        raise CompatInputError(f"npm view printed more than one line: {value[:80]!r}")
    return check_version(value)


# ---------------------------------------------------------------------------------------------
# CLI


def _cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="openrig_compat.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check-version")
    s.add_argument("version")
    s = sub.add_parser("listen")
    s.add_argument("--port-file", required=True)
    s.add_argument("--log", required=True)
    s = sub.add_parser("connections")
    s.add_argument("--log", required=True)
    s = sub.add_parser("openrig-list")
    s.add_argument("directory")
    sub.add_parser("oom-count")
    s = sub.add_parser("copy-tree")
    s.add_argument("--src", required=True)
    s.add_argument("--dst", required=True)
    s = sub.add_parser("package-meta")
    s.add_argument("--package", required=True)
    s.add_argument("--version", required=True)
    s = sub.add_parser("integrity")
    s = sub.add_parser("select-baselines")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("baseline-list")
    s.add_argument("--file", required=True)
    s = sub.add_parser("captures")
    s.add_argument("--root", required=True)
    s.add_argument("--recorded", required=True)
    s.add_argument("--baselines", required=True)
    s.add_argument("--root-alias", action="append", default=[])
    s.add_argument("--out", required=True)
    s = sub.add_parser("help")
    s.add_argument("--root", required=True)
    s.add_argument("--rig", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--baselines", default="")
    s.add_argument("--out", required=True)
    s.add_argument("--seed", action="store_true")
    s = sub.add_parser("spec-validate")
    s.add_argument("--rig", required=True)
    s.add_argument("--spec", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("watched")
    s.add_argument("--root", required=True)
    s.add_argument("--package", required=True)
    s.add_argument("--baselines", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--diff-dir", required=True)
    s = sub.add_parser("contract-unlist")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s = sub.add_parser("contract-add")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--integrity", required=True)
    s.add_argument("--verdict", required=True)
    s = sub.add_parser("in-range")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s = sub.add_parser("probe-result")
    s.add_argument("--file", required=True)
    s.add_argument("--in-range", choices=("yes", "no"), required=True)
    s = sub.add_parser("report")
    s.add_argument("--state", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--integrity", default="unknown")
    s.add_argument("--rig-version-output", default="")
    s.add_argument("--package", required=True)
    s.add_argument("--tree-sha", default="")
    s.add_argument("--root-alias", action="append", default=[])
    s.add_argument("--seed", action="store_true")
    s = sub.add_parser("accept")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--expect-head", default=None)
    s.add_argument("--started-clean", choices=("yes", "no"), default="yes")
    s.add_argument("test_argv", nargs=argparse.REMAINDER)
    s = sub.add_parser("contract-entries")
    s.add_argument("--root", required=True)
    s = sub.add_parser("npm-version")
    s = sub.add_parser("watch-state")
    s.add_argument("--state-dir", required=True)
    s.add_argument(
        "--event",
        required=True,
        choices=("verdict", "deferred", "setup", "fail", "final", "integrity"),
    )
    s.add_argument("--version", default=None)
    s.add_argument("--value", default=None)
    s = sub.add_parser("watch-final")
    s.add_argument("--state-dir", required=True)
    s.add_argument("--version", required=True)
    s = sub.add_parser("watch-verdict")
    s.add_argument("--evidence", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--rc", required=True)
    s = sub.add_parser("watch-record")
    s.add_argument("--state-dir", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--outcome", required=True)
    s.add_argument("--evidence", default="")
    s.add_argument("--final", choices=("yes", "no"), required=True)
    return p


def _utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _baseline_list(text: str) -> list[str]:
    return [check_version(v) for v in text.split(",") if v]


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand; a CompatInputError prints one line and exits 2 (setup) unless noted."""
    ns = _cli().parse_args(argv)
    try:
        return _dispatch(ns)
    except CompatInputError as exc:
        print(f"openrig_compat.py {ns.cmd}: {exc}", file=sys.stderr)
        return EXIT_SETUP


def _dispatch(ns: argparse.Namespace) -> int:  # noqa: C901 - a flat table of subcommands
    if ns.cmd == "check-version":
        check_version(ns.version)
        return EXIT_OK
    if ns.cmd == "listen":
        return serve(Path(ns.port_file), Path(ns.log))
    if ns.cmd == "connections":
        count = connection_count(Path(ns.log))
        print("unknown" if count is None else count)
        return EXIT_OK if count == 0 else EXIT_FAIL
    if ns.cmd == "openrig-list":
        print("\n".join(openrig_home_list(Path(ns.directory))))
        return EXIT_OK
    if ns.cmd == "oom-count":
        count = oom_kill_count()
        print("unknown" if count is None else count)
        return EXIT_OK
    if ns.cmd == "copy-tree":
        listing = sys.stdin.buffer.read(64 * 1024 * 1024)
        print(copy_tree(Path(ns.src), Path(ns.dst), listing))
        return EXIT_OK
    if ns.cmd == "package-meta":
        print(json.dumps(package_meta(Path(ns.package), check_version(ns.version))))
        return EXIT_OK
    if ns.cmd == "integrity":
        print(integrity_from_npm(sys.stdin.read(4096)))
        return EXIT_OK
    if ns.cmd == "select-baselines":
        chosen = select_baselines(Path(ns.root), check_version(ns.version))
        write_json(Path(ns.out), chosen)
        print(f"{chosen['baseline']},{chosen['person_baseline']}")
        return EXIT_OK
    if ns.cmd == "baseline-list":
        print(",".join(baseline_list(Path(ns.file))))
        return EXIT_OK
    if ns.cmd == "captures":
        return run_captures(Path(ns.root), Path(ns.recorded), _baseline_list(ns.baselines),
                            tuple(ns.root_alias), Path(ns.out))  # fmt: skip
    if ns.cmd == "help":
        return run_help(Path(ns.root), ns.rig, Path(ns.out_dir), _baseline_list(ns.baselines),
                        Path(ns.out), seed=ns.seed)  # fmt: skip
    if ns.cmd == "spec-validate":
        return run_spec_validate(ns.rig, Path(ns.spec), Path(ns.out))
    if ns.cmd == "watched":
        out = Path(ns.out)
        return run_watched(Path(ns.root), Path(ns.package), _baseline_list(ns.baselines), out,
                           Path(ns.diff_dir))  # fmt: skip
    if ns.cmd == "contract-unlist":
        root = Path(ns.root)
        print(contract_unlist(root / ".agents/config/openrig_contract.yml", root, ns.version))
        return EXIT_OK
    if ns.cmd == "contract-add":
        root = Path(ns.root)
        contract_add_watcher(root / ".agents/config/openrig_contract.yml", root,
                             ns.version, ns.integrity, ns.verdict)  # fmt: skip
        return EXIT_OK
    if ns.cmd == "in-range":
        try:
            inside = in_range(Path(ns.root), check_version(ns.version))
        except CompatInputError:
            raise
        except Exception as exc:  # noqa: BLE001 - the shipped parser's refusal
            raise CompatInputError(f"openrig_contract.yml: {exc}") from None
        print("yes" if inside else "no")
        return EXIT_OK
    if ns.cmd == "probe-result":
        ok, why = probe_result(read_text(Path(ns.file), MAX_JSON_BYTES), ns.in_range == "yes")
        print(why)
        return EXIT_OK if ok else EXIT_FAIL
    if ns.cmd == "report":
        check_version(ns.version)
        return report(ns)
    if ns.cmd == "accept":
        argv = ns.test_argv[1:] if ns.test_argv[:1] == ["--"] else ns.test_argv
        if not argv:
            raise CompatInputError("accept needs the test argv after --")
        evidence = Path(ns.evidence)
        result = accept(Path(ns.root), ns.version, evidence, argv, expect_head=ns.expect_head,
                        started_clean=ns.started_clean == "yes")  # fmt: skip
        _record_accept(evidence, result)
        sha = f" {result['sha']}" if result["sha"] else ""
        print(f"openrig-compat {ns.version}: accept {result['result']}{sha}: {result['reason']}")
        return EXIT_OK if result["result"] == "accepted" else EXIT_FAIL
    if ns.cmd == "contract-entries":
        for row in contract_entries(Path(ns.root)):
            print("\t".join(row))
        return EXIT_OK
    if ns.cmd == "npm-version":
        print(npm_version(sys.stdin.read(4096)))
        return EXIT_OK
    if ns.cmd == "watch-state":
        print(watch_state(Path(ns.state_dir), ns.event, ns.version, ns.value))
        return EXIT_OK
    if ns.cmd == "watch-final":
        return EXIT_OK if watch_final(Path(ns.state_dir), ns.version) else EXIT_FAIL
    if ns.cmd == "watch-verdict":
        rc = _parse_int(ns.rc) if re.fullmatch(r"-?[0-9]{1,6}", ns.rc) else -1
        print("\t".join(watch_verdict(Path(ns.evidence), check_version(ns.version), rc)))
        return EXIT_OK
    if ns.cmd == "watch-record":
        if not re.fullmatch(r"[A-Za-z-]{1,40}", ns.outcome):
            raise CompatInputError(f"outcome {ns.outcome[:40]!r} is not a word")
        record = {"version": check_version(ns.version), "outcome": ns.outcome,
                  "final": ns.final == "yes", "evidence": ns.evidence,
                  "time": _utc_stamp()}  # fmt: skip
        if record["final"]:
            write_json(Path(ns.state_dir) / "verdicts" / f"{ns.version}.json", record)
        return EXIT_OK
    raise CompatInputError(f"unknown subcommand {ns.cmd}")  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
