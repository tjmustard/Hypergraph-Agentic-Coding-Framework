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
import hashlib
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
from collections.abc import Sequence
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

# Names a watched template is also known by in a later OpenRig (0.7.23 renamed SLICE_MANIFEST to
# `sliceManifest`). WATCHED stays as it is; an item's names are its own name then these.
WATCHED_ALIASES: dict[str, tuple[str, ...]] = {"SLICE_MANIFEST": ("sliceManifest",)}

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
    match = re.search(_template_start(name), source)
    if not match:
        return None
    return _literal_from(source, match.end() - 1)


def _template_start(name: str) -> bytes:
    """The pattern that opens `const <name> = `...`` (optionally an arrow function)."""
    return rb"\bconst\s+" + re.escape(name.encode()) + rb"\s*=\s*(?:\([^()]{0,200}\)\s*=>\s*)?`"


def _literal_from(source: bytes, start: int) -> bytes | None:
    """Return the template literal whose opening backtick is at `start`, or None."""
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


def _read_watched_file(path: Path) -> tuple[str, bytes | str]:
    """Read one watched file: ("ok", bytes), ("missing", "") or ("setup", the harness problem)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", ""
    except OSError as exc:
        return "setup", f"{path.name}: cannot stat: {exc.strerror or exc}"
    if not stat.S_ISREG(info.st_mode):
        return "setup", f"{path.name}: not a regular file"
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_WATCHED_BYTES + 1)
    except OSError as exc:
        return "setup", f"{path.name}: cannot read: {exc.strerror or exc}"
    if len(data) > MAX_WATCHED_BYTES:
        return "setup", f"{path.name}: larger than {MAX_WATCHED_BYTES} bytes"
    return "ok", data


def _find_template(data: bytes, template: str) -> tuple[str, bytes | str]:
    """Find one template by its name or aliases: exactly one match, or missing, or setup."""
    starts = [
        m.end() - 1
        for name in (template, *WATCHED_ALIASES.get(template, ()))
        for m in re.finditer(_template_start(name), data)
    ]
    if not starts:
        return "missing", ""
    if len(starts) > 1:
        return "setup", f"ambiguous: {len(starts)} matches"
    literal = _literal_from(data, starts[0])
    if literal is None:
        return "setup", "template literal could not be parsed"
    return "ok", literal


def watched_items(package: Path) -> dict[str, tuple[str, bytes | str]]:
    """Read the 8 watched items of an installed package, splitting the product from the harness.

    Each value is ("ok", bytes), ("missing", "") (the package no longer has the item: the product
    changed) or ("setup", why) (the harness could not read it, or a template is ambiguous or does
    not parse: never blamed on the product). An unreadable package root makes every item setup.
    """
    if not (os.path.isdir(package) and os.access(package, os.R_OK | os.X_OK)):
        return {key: ("setup", "package root unreadable") for key, _, _ in WATCHED}
    out: dict[str, tuple[str, bytes | str]] = {}
    cache: dict[str, tuple[str, bytes | str]] = {}
    for key, rel, template in WATCHED:
        if rel not in cache:
            cache[rel] = _read_watched_file(package / rel)
        kind, data = cache[rel]
        if kind != "ok" or template is None:
            out[key] = (kind, data)
        else:
            assert isinstance(data, bytes)
            out[key] = _find_template(data, template)
    return out


def watched_bytes(package: Path) -> dict[str, bytes | None]:
    """The watched items' bytes (None: missing or unreadable); see watched_items for the split."""
    return {k: (v if kind == "ok" else None) for k, (kind, v) in watched_items(package).items()}  # type: ignore[misc]


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
    "harness": "compare",
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
    "install", "tree", "live", "help", "spec-validate", "harness", "delegate-probe",
    "delegate-contract", "delegate-verify", "delegate-proof", "delegate-oracle", "daemon-contact",
    "host-touched",
)  # fmt: skip


def decide(
    steps: list[dict], watched_diff: dict, seed: bool, harness_review: Any = ()
) -> tuple[str, str | None, str | None]:
    """Return (verdict, failed_step, setup_error) from the classified steps.

    A safety FAIL (the candidate contacted a daemon, or the host's ~/.openrig changed) wins over
    everything; then a setup error (never reported as the product's); then any FAIL; then a
    required step that never ran (SETUP-ERROR, named); then SEED; then REVIEW when a watched item
    differs from either baseline or a harness is in harness_review; else PASS. PASS, REVIEW and
    SEED need every step of the mode (REQUIRED_STEPS, SEED_REQUIRED_STEPS) present and ok.
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
    if watched_diff or harness_review:
        return "REVIEW", None, None
    return "PASS", None, None


# ---------------------------------------------------------------------------------------------
# Community harness smoke (OPR.99.0.13.1)

HARNESS_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# What a harness's smoke needs: its preset names, launcher and runner.
HARNESS_SMOKES: dict[str, dict[str, Any]] = {
    "agy": {
        "presets": ["agy", "agy-plain"],
        "launcher": "openrig-agy-launch",
        "runner": "openrig-agy-runner.mjs",
    }
}
HARNESS_STATUSES = ("setup", "fail", "absent", "changed", "unreferenced", "ok")
HARNESS_REVIEW = ("absent", "changed", "unreferenced")
HARNESS_EXAMPLES = "daemon/docs/reference/community-harness-examples"
HARNESS_PARSER = "daemon/dist/domain/community-harness-presets.js"
HARNESS_STUB = "dev/tests/compat/stubs/agy"
HARNESS_TIMEOUT = 30
MAX_HARNESS_ENTRIES = 32
MAX_HARNESS_BYTES = 1024 * 1024


class _HarnessProblem(Exception):
    """A harness check that ended as `status` (setup: the checker; fail: the product)."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, " ".join(detail.split())[:400]


def _harness_files(directory: Path) -> dict[str, bytes] | None:
    """Read a harness example dir (None: it does not exist); anything odd is a setup problem."""
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _HarnessProblem("setup", f"{directory.name}: cannot stat: {exc}") from None
    if not stat.S_ISDIR(info.st_mode):
        raise _HarnessProblem("setup", f"{directory.name}: not a plain directory")
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise _HarnessProblem("setup", f"{directory.name}: cannot list: {exc}") from None
    if len(names) > MAX_HARNESS_ENTRIES:
        raise _HarnessProblem("setup", f"{directory.name}: over {MAX_HARNESS_ENTRIES} entries")
    files = {}
    for name in names:
        try:
            files[name] = read_bytes(directory / name, MAX_HARNESS_BYTES)
        except CompatInputError as exc:
            raise _HarnessProblem("setup", str(exc)) from None
    return files


def _harness_run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run one smoke subprocess in its own session; a timeout or launch failure is setup."""
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        raise _HarnessProblem("setup", f"{argv[0]} could not run: {exc}") from None
    try:
        out, err = proc.communicate(timeout=HARNESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        raise _HarnessProblem("setup", f"{argv[0]} timed out after {HARNESS_TIMEOUT} s") from None
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _parse_presets(package: Path, copy: Path, work: Path) -> tuple[list, list[Path]]:
    """Load the copy's presets with the candidate's own parser, in empty HOME and OPENRIG_HOME."""
    module = os.path.abspath(package / HARNESS_PARSER)
    code = (
        'import { pathToFileURL } from "node:url";\n'
        f"const m = await import(pathToFileURL({json.dumps(module)}).href);\n"
        "console.log(JSON.stringify(m.listCommunityHarnessPresets(process.argv.at(-1))));\n"
    )
    homes = [work / "home-parse", work / "openrig-home-parse"]
    for home in homes:
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
    env = {**os.environ, "HOME": str(homes[0]), "OPENRIG_HOME": str(homes[1])}
    got = _harness_run(["node", "--input-type=module", "-e", code, str(copy)], env)
    if got.returncode != 0:
        raise _HarnessProblem("setup", f"the preset parser did not load: {_last_line(got)}")
    try:
        results = json.loads(got.stdout)
    except ValueError:
        results = None
    if not isinstance(results, list):
        raise _HarnessProblem("setup", "the preset parser did not print a JSON array")
    return results, homes


def _check_presets(results: list, files: dict[str, bytes], smoke: dict) -> None:
    """Every *.yaml parses ok with its stem as the name, and the names are the expected set."""
    yamls = sorted(n for n in files if n.endswith(".yaml"))
    names = []
    for index, file in enumerate(yamls):
        item = results[index] if index < len(results) else None
        preset = item.get("preset") if isinstance(item, dict) else None
        if not (isinstance(item, dict) and item.get("ok") is True and isinstance(preset, dict)):
            message = item.get("message") if isinstance(item, dict) else "no result"
            raise _HarnessProblem("fail", f"{file}: the preset did not load: {message}")
        if preset.get("name") != file.removesuffix(".yaml"):
            raise _HarnessProblem("fail", f"{file}: preset name {preset.get('name')!r}")
        names.append(preset["name"])
    if len(results) != len(yamls) or sorted(names) != sorted(smoke["presets"]):
        raise _HarnessProblem(
            "fail", f"preset names {sorted(names)} are not {sorted(smoke['presets'])}"
        )


def _launcher_case(
    case: str, copy: Path, smoke: dict, stub: Path, work: Path, resume: str | None
) -> tuple[int, Any, list[str]]:
    """Run one launcher case against the stub: (exit code, result JSON or None, stub calls)."""
    slug = case.replace(" ", "-")
    home = work / f"home-{slug}"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    result, log = work / f"{slug}.json", work / f"{slug}.log"
    for old in (result, log):
        old.unlink(missing_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("OPENRIG_RESUME_REF", "AGY_EXTRA_PATH", "AGY_HOME", "OPENRIG_AGY")
    }
    env.update(
        OPENRIG_LAUNCH_RESULT=str(result),
        OPENRIG_RUNTIME="agy",
        OPENRIG_LAUNCH_ATTEMPT=f"smoke-{slug}",
        STUB_AGY_LOG=str(log),
    )
    if resume is not None:
        env["OPENRIG_RESUME_REF"] = resume
    if case == "resume present":
        db = home / ".gemini/antigravity-cli/conversations/stubconv-0002.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"")
    launcher = str(copy / smoke["launcher"])
    got = _harness_run(["sh", launcher, "--agy-home", str(home), "--agy", str(stub)], env)
    try:
        data = json.loads(read_text(result)) if result.exists() else None
    except (ValueError, CompatInputError):
        data = "unreadable"
    calls = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
    return got.returncode, data, calls


def _check_launcher(copy: Path, smoke: dict, stub: Path, work: Path) -> None:
    """The four launcher cases of mini-requirement 5; the first wrong one is a product failure."""

    def native(data: Any, ref: str) -> bool:
        return isinstance(data, dict) and data.get("nativeRef") == ref

    def bad(case: str, why: str) -> _HarnessProblem:
        return _HarnessProblem("fail", f"launcher case {case}: {why}")

    rc, data, calls = _launcher_case("fresh", copy, smoke, stub, work, None)
    if rc != 0 or not native(data, "stubconv-0001"):
        raise bad("fresh", f"exit {rc}, result {data!r}")
    if data.get("format") != 1 or data.get("harness") != "agy":
        raise bad("fresh", f"result {data!r}")
    first = f" {calls[0]} " if calls else ""
    if (
        len(calls) != 2
        or " -p " not in first
        or " --output-format json " not in first
        or calls[1] != "--conversation stubconv-0001"
    ):
        raise bad("fresh", f"stub calls {calls}")
    rc, data, calls = _launcher_case("resume missing", copy, smoke, stub, work, "stubconv-missing")
    if rc != 1 or not (isinstance(data, dict) and data.get("error") == "missing") or calls:
        raise bad("resume missing", f"exit {rc}, result {data!r}, stub calls {calls}")
    rc, data, calls = _launcher_case("unusual id", copy, smoke, stub, work, "bad/id")
    if rc != 2 or data is not None or calls:
        raise bad("unusual id", f"exit {rc}, result {data!r}, stub calls {calls}")
    rc, data, calls = _launcher_case("resume present", copy, smoke, stub, work, "stubconv-0002")
    if rc != 0 or not native(data, "stubconv-0002") or calls != ["--conversation stubconv-0002"]:
        raise bad("resume present", f"exit {rc}, result {data!r}, stub calls {calls}")


def _smoke_harness(
    root: Path, package: Path, name: str, files: dict[str, bytes] | None, work: Path
) -> None:
    """Run the preset, syntax and launcher checks for one harness; raise _HarnessProblem."""
    smoke = HARNESS_SMOKES.get(name)
    if smoke is None:
        raise _HarnessProblem("setup", f"no smoke is defined for harness {name!r}")
    if shutil.which("node") is None:
        raise _HarnessProblem("setup", "node is not on PATH")
    if not (package / HARNESS_PARSER).is_file():
        raise _HarnessProblem("setup", "the preset parser module is missing from the package")
    copy = work / name
    try:
        shutil.rmtree(copy, ignore_errors=True)
        copy.mkdir(parents=True)
        for file, data in (files or {}).items():
            (copy / file).write_bytes(data)
        stub = work / "stub-agy"
        shutil.copyfile(root / HARNESS_STUB, stub)
        stub.chmod(0o755)
        results, homes = _parse_presets(package, copy if files is not None else work / "absent",
                                        work)  # fmt: skip
        if files is None:
            return
        for needed in (smoke["launcher"], smoke["runner"]):
            if needed not in files:
                raise _HarnessProblem("fail", f"{needed} is not in the example dir")
        _check_presets(results, files, smoke)
        if _harness_run(["sh", "-n", str(copy / smoke["launcher"])]).returncode != 0:
            raise _HarnessProblem("fail", f"sh -n {smoke['launcher']} failed")
        if _harness_run(["node", "--check", str(copy / smoke["runner"])]).returncode != 0:
            raise _HarnessProblem("fail", f"node --check {smoke['runner']} failed")
        _check_launcher(copy, smoke, stub, work)
        for home in homes:
            if any(home.iterdir()):
                label = "OPENRIG_HOME" if home.name == "openrig-home-parse" else "HOME"
                raise _HarnessProblem("fail", f"the preset parser wrote into {label}")
    except OSError as exc:
        raise _HarnessProblem("setup", f"the work dir cannot be used: {exc}") from None


def _compare_harness(
    root: Path, name: str, files: dict[str, bytes], baselines: list[str], diff_dir: Path
) -> tuple[str, dict]:
    """Byte-compare the example files with each chosen baseline that holds harness/<name>/."""
    diff: dict[str, dict[str, str]] = {}
    held = False
    for version in dict.fromkeys(baselines):
        want = _harness_files(baseline_dir(root, version) / "harness" / name)
        if want is None:
            continue
        held = True
        for file in sorted(set(files) | set(want)):
            if file not in files:
                diff.setdefault(file, {})[version] = "missing"
            elif file not in want:
                diff.setdefault(file, {})[version] = "extra"
            elif files[file] != want[file]:
                diff.setdefault(file, {})[version] = "differs"
                text = unified(
                    want[file], files[file], f"baseline {version}/{file}", f"candidate/{file}"
                )
                diff_dir.mkdir(parents=True, exist_ok=True)
                target = diff_dir / f"harness-{name}-{file}-{version}.diff"
                target.write_text(text, encoding="utf-8")
    if diff:
        return "changed", diff
    return ("ok" if held else "unreferenced"), diff


def run_harness(
    root: Path,
    package: Path,
    baselines: list[str],
    work: Path,
    out: Path,
    diff_dir: Path,
    names: list[str],
    seed: bool,
) -> int:
    """Smoke each configured harness and compare its example files; write harness.json.

    Precedence per name: setup > fail > absent > changed > unreferenced > ok. Exit 2 when any
    harness is setup, else 1 when any is fail, else 0. A seed run compares nothing.
    """
    entries: dict[str, dict] = {}
    setup: list[str] = []
    for name in dict.fromkeys(names):
        entry: dict[str, Any] = {"status": "ok", "detail": "", "files": [], "diff": {}}
        try:
            if not HARNESS_NAME_RE.fullmatch(name):
                raise _HarnessProblem("setup", f"bad harness name {name[:70]!r}")
            if not (os.path.isdir(package) and os.access(package, os.R_OK | os.X_OK)):
                raise _HarnessProblem("setup", "package root unreadable")
            files = _harness_files(package / HARNESS_EXAMPLES / name)
            entry["files"] = sorted(files or {})
            _smoke_harness(root, package, name, files, work)
            if files is None:
                entry.update(status="absent", detail="the package ships no example for it")
            elif not seed:
                entry["status"], entry["diff"] = _compare_harness(
                    root, name, files, baselines, diff_dir
                )
                if entry["status"] == "changed":
                    entry["detail"] = "differs from a baseline: " + ", ".join(sorted(entry["diff"]))
                elif entry["status"] == "unreferenced":
                    entry["detail"] = "no chosen baseline holds this harness"
        except _HarnessProblem as exc:
            entry.update(status=exc.status, detail=exc.detail)
            if exc.status == "setup":
                setup.append(f"harness: {name}: {exc.detail}")
        entries[name] = entry
        print(f"harness {name}: {entry['status']} {entry['detail']}".rstrip())
    write_json(out, {"harness": entries, "setup": setup})
    if setup:
        return EXIT_SETUP
    return EXIT_FAIL if any(e["status"] == "fail" for e in entries.values()) else EXIT_OK


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


def watcher_versions(contract: Path, plus: str | None = None, root: Path | None = None) -> str:
    """The `accepted_by: watcher` versions of a contract file, then `plus`, comma-joined.

    The file is read with the tree's own parser. The tree is `root` when given; otherwise the
    directory three levels above the file (right for <tree>/.agents/config/openrig_contract.yml,
    wrong for a copy kept elsewhere, such as the harness's state/contract.orig). `plus`
    is appended unless a watcher entry already has it. Empty when there are none.
    """
    if plus is not None:
        check_version(plus)
    path = Path(os.path.abspath(contract))
    if root is None and len(path.parents) < 3:
        raise CompatInputError(f"{contract}: not inside a tree")
    raw = read_bytes(path, 64 * 1024)
    try:
        parsed = _shipped(root if root is not None else path.parents[2])._parse_contract(raw)
    except Exception as exc:  # noqa: BLE001 - the tree's parser raises its own error types
        raise CompatInputError(f"{contract}: not a valid contract: {exc}") from None
    versions = [e.version for e in parsed.accepted if e.accepted_by == "watcher"]
    if plus is not None and plus not in versions:
        versions.append(plus)
    return ",".join(versions)


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
    before it is closed, so the count survives the listener being stopped. The listener also
    exits when its parent is gone (checked on each accept timeout), so a killed run leaves none.
    """
    parent = os.getppid()
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
            if os.getppid() != parent:
                # stderr, never the log: every line of the log counts as a connection.
                print("parent gone", file=sys.stderr, flush=True)
                break
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


_ANCHOR_IDS_CODE = """import json, sys
from pathlib import Path
sys.path.insert(0, ".agents/scripts")
import hyper_lore as h
_, data = h.read_graph(Path("."))
ids = set()
for node in h.nodes_of(data):
    ids.update(h._anchor_list(node, "anchors", "components"))
print(json.dumps(sorted(ids)))
"""


def _tree_python(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run python3 with `args` in root; a launch failure or a timeout is a CompatInputError."""
    try:
        return subprocess.run(
            ["python3", *args], cwd=root, capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompatInputError(f"python3 {args[0]} could not run: {exc}") from None


def provision_ledger(root: Path) -> str:
    """Give a tree under test the ledger it lacks, from its own lore anchors (OPR.99.0.12.1).

    A tree that already has spec/compiled/architecture.yml is left alone ("ledger: present").
    Otherwise the clone's own hyper_lore.py reads its lore graph, and the clone's own
    hyper_ledger.py writes one System node per distinct anchor component id. Anything that goes
    wrong is a CompatInputError (a setup error of the harness, never a product FAIL). Nothing
    outside root is read or written, and git history is untouched.
    """
    if os.path.lexists(root / "spec" / "compiled" / "architecture.yml"):
        return "ledger: present"
    ledger_py = root / ".agents" / "scripts" / "hyper_ledger.py"
    if not ledger_py.is_file():
        raise CompatInputError(".agents/scripts/hyper_ledger.py is missing from the tree")
    ids: list[str] = []
    if os.path.lexists(root / "lore_hypergraph.yml"):
        if not (root / ".agents" / "scripts" / "hyper_lore.py").is_file():
            raise CompatInputError(".agents/scripts/hyper_lore.py is missing from the tree")
        got = _tree_python(root, "-c", _ANCHOR_IDS_CODE)
        try:
            ids = json.loads(got.stdout) if got.returncode == 0 else None
        except ValueError:
            ids = None
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise CompatInputError("the lore graph did not load: " + _last_line(got))
    steps = [("init",), *(("ensure-node", "--id", i, "--dimension", "System") for i in ids)]
    for step in (*steps, ("validate",)):
        got = _tree_python(root, ".agents/scripts/hyper_ledger.py", *step)
        if got.returncode != 0:
            raise CompatInputError(
                f"hyper_ledger.py {' '.join(step[:2])} failed: {_last_line(got)}"
            )
    return f"ledger: provisioned {len(ids)} node(s) from lore anchors"


def _last_line(done: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (done.stderr + done.stdout).splitlines() if ln.strip()]
    return lines[-1][:200] if lines else f"exit {done.returncode}"


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
    items = watched_items(package)
    cand = {k: (v if kind == "ok" else None) for k, (kind, v) in items.items()}
    problem_keys = [k for k, (kind, _) in items.items() if kind == "setup"]
    for key in problem_keys:
        result["setup"].append(f"watched: {key}: {items[key][1]}")
    loaded = {}
    for version in dict.fromkeys(baselines):
        try:
            loaded[version] = load_baseline_watched(baseline_dir(root, version))
        except CompatInputError as exc:
            result["setup"].append(f"baseline {version}: {exc}")
    differs, diffs = compare_watched(cand, loaded)
    for key in problem_keys:  # a harness problem is not "missing" (the setup entry says it)
        differs.pop(key, None)
    diff_dir.mkdir(parents=True, exist_ok=True)
    for name, text in diffs.items():
        (diff_dir / f"watched-{name}.diff").write_text(text, encoding="utf-8")
    result["watched_diff"] = differs
    write_json(out, result)
    return EXIT_SETUP if result["setup"] else EXIT_OK  # a difference is REVIEW, not a step failure


def write_baseline_candidate(state: Path, package: Path, dest: Path, meta: dict,
                             roots: tuple[str, ...], seed: bool = False,
                             harnesses: Any = ("agy",)) -> list[str]:  # fmt: skip
    """Write baseline-candidate/: normalised captures, help texts, watched items and meta.json.

    A watched item the harness could not read is a problem. One the package no longer has is a
    problem only in a seed run; otherwise it is left out and listed in meta["watched_missing"].
    """
    problems = []
    watched_missing: list[str] = []
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
    for key, (kind, data) in watched_items(package).items():
        if kind == "ok":
            assert isinstance(data, bytes)
            (dest / "watched" / watched_file_name(key)).write_bytes(data)
        elif kind == "setup":
            problems.append(f"watched: {key}: {data}")
        elif seed:
            problems.append(f"watched: {key} missing")
        else:
            watched_missing.append(key)
    absent = []
    for name in harnesses:
        try:
            files = _harness_files(package / HARNESS_EXAMPLES / name)
        except _HarnessProblem as exc:
            problems.append(f"harness: {name}: {exc.detail}")
            continue
        if files is None:
            absent.append(name)
            continue
        (dest / "harness" / name).mkdir(parents=True)
        for file, data in files.items():
            (dest / "harness" / name / file).write_bytes(data)
    write_json(
        dest / "meta.json",
        {
            **meta,
            "watched_missing": sorted(watched_missing),
            "harnesses": list(harnesses),
            "harness_absent": sorted(absent),
        },
    )
    return problems


HELP_DIFF_LISTS = ("removed_flags", "added_flags", "removed_commands", "added_commands",
                   "hacf_flags_missing")  # fmt: skip


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _paths_added(value: dict, what: str) -> None:
    """json_paths_added is {capture name: [key path, ...]}, as compare_captures writes it."""
    if not all(_strs(paths) and paths for paths in value.values()):
        raise CompatInputError(f"{what}: json_paths_added is not {{capture: [key paths]}}")


def check_state(
    name: str, data: Any, chosen: list[str], step_ok: bool, seed: bool, harnesses: Any = ("agy",)
) -> None:
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
        compare_only = name in ("watched.json", "harness.json")
        for key in ("setup",) if compare_only else ("setup", "problems"):
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
    if name == "harness.json":
        entries = _expect(data, "harness", (dict,), name)
        for wanted in harnesses:
            if wanted not in entries:
                raise CompatInputError(f"{name}: harness {wanted!r} is missing")
        for key, entry in entries.items():
            status = entry.get("status") if isinstance(entry, dict) else None
            if status not in HARNESS_STATUSES or not isinstance(entry.get("detail"), str):
                raise CompatInputError(f"{name}: harness {str(key)[:40]!r} is malformed")
            if step_ok and status in ("setup", "fail"):
                raise CompatInputError(f"{name}: {status} recorded but its step passed")
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
        "help.json", "harness.json"
    ]  # fmt: skip
    harnesses = list(dict.fromkeys(getattr(ns, "harness", None) or ["agy"]))
    loaded = {name: part(name) for name in required}
    data: dict[str, dict] = {name: {} for name in required}
    chosen: list[str] = ["(unknown)"]  # until baselines.json is read: no per_baseline covers it
    results = {s["name"]: s["result"] for s in steps}
    for name in required:  # every required file against its schema, empty or not
        if loaded[name] is None:
            continue  # part() already recorded the setup step
        data[name] = loaded[name]
        try:
            check_state(
                name, data[name], chosen, results.get(name[:-5]) == "ok", ns.seed, harnesses
            )
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
                                            meta, tuple(ns.root_alias), ns.seed,
                                            harnesses)  # fmt: skip
        if problems:
            steps.append({"name": "baseline-candidate", "result": "setup", "rc": None,
                          "seconds": None, "detail": "; ".join(problems)[:500]})  # fmt: skip
    watched_diff = watched.get("watched_diff", {})
    smoked = data.get("harness.json", {}).get("harness", {})
    harness_review = sorted(n for n, e in smoked.items() if e["status"] in HARNESS_REVIEW)
    verdict, failed, setup_error = decide(steps, watched_diff, ns.seed, harness_review)
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
        "harness": {} if ns.seed else {n: e["status"] for n, e in smoked.items()},
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
    gone = sorted(
        k
        for k, per in (watched_diff.items() if isinstance(watched_diff, dict) else ())
        if isinstance(per, dict) and "missing" in per.values()
    )
    if gone:
        lines.append(f"Watched items missing from the candidate (REVIEW): {', '.join(gone)}.")
    if watched_diff:
        lines.append(f"Watched items that differ (REVIEW): {json.dumps(watched_diff)}.")
    for name, entry in sorted(smoked.items()):
        if entry["status"] != "ok":
            lines.append(f"Harness {name}: {entry['status']}: {entry['detail']}")
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
    harness = data.get("harness")
    if not isinstance(harness, dict) or not harness or any(v != "ok" for v in harness.values()):
        raise CompatInputError("verdict.json: harness not ok")
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

STREAMS = ("new-version", "self-test")
_STREAM_DEFAULTS: dict[str, Any] = {
    "deferred": 0, "setup_errors": 0, "no_verdict": 0, "notified": False,
}  # fmt: skip
COUNTER_DEFAULTS: dict[str, Any] = {
    "schema": 2,
    "streams": {name: dict(_STREAM_DEFAULTS) for name in STREAMS},
    "fails": {},
    "integrity_alerts": {},
    "outbox_dropped": 0,
}
_V1_KEYS = {
    "deferred", "setup_errors", "no_verdict", "no_verdict_notified", "fails", "integrity_alerts",
}  # fmt: skip
MAX_COUNT = 1_000_000


def _count(v: Any) -> bool:
    return type(v) is int and 0 <= v <= MAX_COUNT


def _maps_ok(data: dict) -> bool:
    fails, alerts = data["fails"], data["integrity_alerts"]
    if not isinstance(fails, dict) or not all(
        VERSION_RE.match(k) and _count(v) for k, v in fails.items()
    ):  # fmt: skip
        return False
    return isinstance(alerts, dict) and all(
        VERSION_RE.match(k) and _strs(v) and len(v) <= 100 for k, v in alerts.items()
    )  # fmt: skip


def _counters_ok(data: Any) -> bool:
    """True for a schema-2 counters file."""
    if not isinstance(data, dict) or set(data) != set(COUNTER_DEFAULTS):
        return False
    if type(data["schema"]) is not int or data["schema"] != 2:
        return False
    streams = data["streams"]
    if not isinstance(streams, dict) or set(streams) != set(STREAMS):
        return False
    for got in streams.values():
        if not isinstance(got, dict) or set(got) != set(_STREAM_DEFAULTS):
            return False
        if not all(_count(got[k]) for k in ("deferred", "setup_errors", "no_verdict")):
            return False
        if type(got["notified"]) is not bool:
            return False
    return _count(data["outbox_dropped"]) and _maps_ok(data)


def _counters_v1_ok(data: Any) -> bool:
    """True for the schema-less (v1) counters file slice 4 wrote."""
    if not isinstance(data, dict) or set(data) != _V1_KEYS:
        return False
    if not all(_count(data[k]) for k in ("deferred", "setup_errors", "no_verdict")):
        return False
    return type(data["no_verdict_notified"]) is bool and _maps_ok(data)


def _migrate_v1(state: Path, old: dict) -> dict:
    """Move v1 counts into the new-version stream, clear every latch and note it in history.log."""
    data = json.loads(json.dumps(COUNTER_DEFAULTS))
    for key in ("deferred", "setup_errors", "no_verdict"):
        data["streams"]["new-version"][key] = old[key]
    data["fails"], data["integrity_alerts"] = old["fails"], old["integrity_alerts"]
    write_json(state / "counters.json", data)
    _history(state, f"- counters-migrated {_utc()} v1->v2")
    return data


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history(state: Path, line: str) -> None:
    with (state / "history.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_counters(state: Path) -> tuple[dict, str | None]:
    """counters.json (a v1 file is migrated), or the defaults; an unreadable file is moved aside."""
    path = state / "counters.json"
    if not path.exists():
        return json.loads(json.dumps(COUNTER_DEFAULTS)), None
    try:
        data = load_json_file(path, 1024 * 1024)
        if _counters_ok(data):
            return data, None
        if isinstance(data, dict) and "schema" not in data and _counters_v1_ok(data):
            return _migrate_v1(state, data), None
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


def watch_state(
    state: Path, event: str, version: str | None, value: str | None, stream: str = "new-version"
) -> str:
    """Apply one watcher event to counters.json; return what the shell should do.

    verdict, deferred and setup act on one stream. verdict resets it. setup and deferred count one
    run without a verdict: setup returns `notify-setup <n>` while the stream is not latched;
    deferred returns `notify-deferred <n>` once n reaches 2 and it is not latched; else `quiet <n>`.
    fail: count a FAIL of version; returns the count. final: clear version's FAIL count.
    integrity: `new` the first time (version, value) is seen, else `seen`.
    """
    if stream not in STREAMS:
        raise CompatInputError(f"unknown watcher stream {stream!r}")
    state.mkdir(parents=True, exist_ok=True)
    data, warning = load_counters(state)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    out = "ok"
    got = data["streams"][stream]
    if event == "verdict":
        data["streams"][stream] = dict(_STREAM_DEFAULTS)
    elif event in ("deferred", "setup"):
        key = "deferred" if event == "deferred" else "setup_errors"
        got[key] = min(got[key] + 1, MAX_COUNT)
        got["no_verdict"] = min(got["no_verdict"] + 1, MAX_COUNT)
        wanted = event == "setup" or got["no_verdict"] >= 2
        if wanted and not got["notified"]:
            got["notified"] = True
            out = f"notify-{event} {got['no_verdict']}"
        else:
            out = f"quiet {got['no_verdict']}"
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


# ---------------------------------------------------------------------------------------------
# The outbox: a queue row is written here before it is sent and removed only once it landed

OUTBOX_MAX = 50
OUTBOX_FLUSH = 20
MAX_OUTBOX_BYTES = 256 * 1024
OUTBOX_NAME_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9]{4}\.json")
REQUEST_ID_RE = re.compile(r"qitem-[0-9]{14}-[0-9a-f]+")


def _outbox_names(state: Path) -> list[str]:
    box = state / "outbox"
    if not box.is_dir():
        return []
    return sorted(n for n in os.listdir(box) if OUTBOX_NAME_RE.fullmatch(n))


def _outbox_entry(state: Path, name: str) -> dict:
    """Read and validate one outbox entry; anything wrong is a CompatInputError."""
    if not OUTBOX_NAME_RE.fullmatch(name):
        raise CompatInputError(f"bad outbox entry name {name[:60]!r}")
    data = load_json_file(state / "outbox" / name, MAX_OUTBOX_BYTES)
    ok = (
        isinstance(data, dict)
        and set(data)
        == {
            "destination",
            "summary",
            "evidence_ref",
            "body",
            "verify",
            "request_id",
            "created",
        }  # fmt: skip
        and all(
            isinstance(data[k], str)
            for k in ("destination", "summary", "evidence_ref", "body", "created")
        )  # fmt: skip
        and type(data["verify"]) is bool
        and (
            data["request_id"] is None
            or (
                isinstance(data["request_id"], str)
                and REQUEST_ID_RE.fullmatch(data["request_id"]) is not None
            )
        )  # fmt: skip
        and all(
            data[k] and not set("\t\n\r") & set(data[k])
            for k in ("destination", "summary", "evidence_ref")
        )  # fmt: skip
    )
    if not ok:
        raise CompatInputError(f"outbox entry {name} is malformed")
    return data


def outbox_add(
    state: Path, destination: str, summary: str, evidence_ref: str, body: str, verify: bool
) -> tuple[str, int]:
    """Write a new entry (dropping the oldest when the outbox is full); return (name, dropped)."""
    for what, text in (("destination", destination), ("summary", summary), ("ref", evidence_ref)):
        if not text or set("\t\n\r") & set(text):
            raise CompatInputError(f"outbox {what} must be one non-empty line")
    box = state / "outbox"
    box.mkdir(parents=True, exist_ok=True)
    names, dropped = _outbox_names(state), 0
    while len(names) >= OUTBOX_MAX:
        oldest = names.pop(0)
        try:
            lost = _outbox_entry(state, oldest)["summary"]
        except CompatInputError:
            lost = oldest
        (box / oldest).unlink()
        dropped += 1
        data, warning = load_counters(state)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        data["outbox_dropped"] = min(data["outbox_dropped"] + 1, MAX_COUNT)
        write_json(state / "counters.json", data)
        _history(state, f"outbox-dropped {_utc()} {lost}")
    created = _utc()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    seq = 0
    while (box / f"{stamp}-{seq:04d}.json").exists():
        seq += 1
    name = f"{stamp}-{seq:04d}.json"
    entry = dict(
        destination=destination,
        summary=summary,
        evidence_ref=evidence_ref,
        body=f"{body.rstrip(chr(10))}\nFirst attempted: {created}\n",
        verify=verify,
        request_id=None,  # `rig`'s retry id, once a send printed one
        created=created,
    )
    write_json(box / name, entry)
    return name, dropped


def outbox_list(state: Path, limit: int = OUTBOX_FLUSH) -> list[str]:
    """Up to `limit` entries, oldest first, as TSV lines; a malformed entry is set aside."""
    lines = []
    for name in _outbox_names(state):
        if len(lines) >= limit:
            break
        try:
            e = _outbox_entry(state, name)
        except CompatInputError as exc:
            os.replace(state / "outbox" / name, state / "outbox" / f"{name}.bad")
            print(f"warning: {exc}", file=sys.stderr)
            continue
        lines.append(
            "\t".join(
                (
                    name,
                    e["destination"],
                    e["summary"],
                    e["evidence_ref"],
                    "yes" if e["verify"] else "no",
                    e["request_id"] or "-",
                )
            )  # fmt: skip
        )
    return lines


def outbox_mark(state: Path, name: str, request_id: str) -> None:
    """Record the retry identity of an entry's last send."""
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise CompatInputError(f"bad request id {request_id[:60]!r}")
    entry = _outbox_entry(state, name)
    entry["request_id"] = request_id
    write_json(state / "outbox" / name, entry)


def outbox_dropped(state: Path, reset: bool = False) -> int:
    """How many alerts the outbox dropped; `reset` sets the count back to 0."""
    data, warning = load_counters(state)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    count = data["outbox_dropped"]
    if reset and count:
        data["outbox_dropped"] = 0
        write_json(state / "counters.json", data)
    return count


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
# The installed version, the final record and the checker self-test (OPR.99.0.13.3; mission 5.3)

INSTALLED_RE = re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)(?: \(.*\))?")
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
RESULT_FIELDS = ("verdict", "watched", "help", "harness", "baselines")
ACCEPTED_BRANCH = "refs/heads/swimlane/openrig-accepted"
# Marks a record whose `help` lists only the baselines with a difference (OPR.99.0.16.16). A record
# without it listed every compared baseline, so its `help` is never compared as it stands.
HELP_RULE = "differing"


def watch_installed(state: Path, raw: str) -> str:
    """Record `rig --version`'s output in installed.json; say how it changed."""
    text = raw.strip()
    found = INSTALLED_RE.fullmatch(text)
    if not found or not VERSION_RE.match(found.group(1)):
        return "unparsed"
    version = found.group(1)
    path = state / "installed.json"
    old = _read_record(path)
    before = old.get("version") if old else None
    if not isinstance(before, str) or not VERSION_RE.match(before):
        before = None
    if before == version:
        out = f"same {version}"
    else:
        out = f"changed {before} {version}" if before else f"first {version}"
    write_json(path, {"version": version, "raw": text, "seen": _utc()})
    return out


def _read_record(path: Path) -> dict | None:
    """A JSON object from a state file, or None when it is absent or unreadable."""
    try:
        data = load_json_file(path, 1024 * 1024)
    except CompatInputError:
        return None
    return data if isinstance(data, dict) else None


def _fields_ok(data: Any) -> bool:
    """True when data holds the five compared fields with their shapes."""
    if not isinstance(data, dict) or not isinstance(data.get("verdict"), str):
        return False
    harness, baselines = data.get("harness"), data.get("baselines")
    return (
        _strs(data.get("watched"))
        and _strs(data.get("help"))
        and isinstance(harness, dict)
        and all(isinstance(v, str) for v in harness.values())
        and isinstance(baselines, list)
        and len(baselines) == 2
        and all(v is None or isinstance(v, str) for v in baselines)
    )


def comparison_fields(evidence: Path, version: str) -> dict:
    """The compared fields, integrity and accept sha of a run's verdict.json; else raise."""
    data = load_json_file(evidence / "verdict.json")
    if not isinstance(data, dict):
        raise CompatInputError("verdict.json: not a JSON object")
    for key, kinds in VERDICT_KEYS.items():
        _expect(data, key, kinds, "verdict.json")
    if data["version"] != version:
        raise CompatInputError(f"verdict.json: not about {version}")
    out = {
        "verdict": data["verdict"],
        "watched": sorted(data["watched_diff"]),
        # help_diff keeps one key per compared baseline; an empty object means no difference
        "help": sorted(v for v, d in data["help_diff"].items() if isinstance(d, dict) and d),
        "harness": data.get("harness"),
        "baselines": [data["baseline"], data["person_baseline"]],
    }
    if not _fields_ok(out):
        raise CompatInputError("verdict.json: harness or baselines have the wrong shape")
    out["integrity"] = data["integrity"]
    accept_result = data.get("accept")
    if isinstance(accept_result, dict) and isinstance(accept_result.get("sha"), str):
        out["accept_sha"] = accept_result["sha"]
    return out


def watch_record(state: Path, version: str, outcome: str, evidence: str, final: bool) -> None:
    """Write verdicts/<version>.json for a final outcome, with the comparison fields."""
    record = {"version": check_version(version), "outcome": outcome, "final": final,
              "evidence": evidence, "time": _utc()}  # fmt: skip
    if not final:
        return
    try:
        fields = comparison_fields(Path(evidence), version)
        if outcome != "accepted":
            fields.pop("accept_sha", None)
        record.update(fields, help_rule=HELP_RULE)
    except CompatInputError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        _history(state, f"record: {version} without comparison fields")
    write_json(state / "verdicts" / f"{version}.json", record)


def _one_line(text: str) -> str:
    return " ".join(text.split())[:600]


def _stated(data: dict) -> str:
    return "; ".join(f"{k}={json.dumps(data[k], sort_keys=True)}" for k in RESULT_FIELDS)


def _differences(expected: dict, got: dict, fields: Sequence[str] = RESULT_FIELDS) -> str:
    return "; ".join(
        f"{k}: expected {json.dumps(expected[k], sort_keys=True)} got "
        f"{json.dumps(got[k], sort_keys=True)}"
        for k in fields
        if expected[k] != got[k]
    )


def _fixed_expectation(worktree: Path, version: str, harnesses: Sequence[str]) -> dict | None:
    """Rule 1: what a version listed in the contract with its own baseline dir must give."""
    base = worktree / "dev" / "tests" / "fixtures" / "openrig-compat" / version
    if version not in {row[0] for row in contract_entries(worktree)} or not base.is_dir():
        return None
    harness = {n: "ok" if (base / "harness" / n).is_dir() else "absent" for n in harnesses}
    verdict = "PASS" if all(v == "ok" for v in harness.values()) else "REVIEW"
    return {"verdict": verdict, "watched": [], "help": [], "harness": harness}


def _with_current_help(final: dict, version: str) -> dict | None:
    """A final record with `help` under HELP_RULE; None when it cannot be derived.

    An older record's `help` is re-derived from its evidence dir. Nothing is written.
    """
    if "help_rule" in final:
        return final
    evidence = final.get("evidence")
    if not isinstance(evidence, str):
        return None
    try:
        return {**final, "help": comparison_fields(Path(evidence), version)["help"]}
    except CompatInputError:
        return None


def watch_selftest(
    state: Path, worktree: Path, version: str, evidence: str, rc: int, harnesses: Sequence[str]
) -> tuple[str, str]:
    """Judge a self-test run on the installed version against its expectation: (result, text).

    A SETUP-ERROR or a FAIL is never written as an expectation.
    """
    check_version(version)
    setup, got = "no evidence dir", None
    if evidence != "-":
        verdict, _, detail = watch_verdict(Path(evidence), version, rc)
        if verdict == "SETUP-ERROR":
            setup = detail or "setup error"
        try:
            got = comparison_fields(Path(evidence), version)
            got = {k: got[k] for k in RESULT_FIELDS}
            got["verdict"] = verdict
        except CompatInputError as exc:
            setup = setup if verdict == "SETUP-ERROR" else str(exc)
    path = state / "selftest" / f"{version}.json"
    held = _read_record(path)
    if held is not None and not _fields_ok(held):
        held = None
    replaces = ""
    if held is not None and "help_rule" not in held:  # recorded under the old rule: absent
        held, replaces = None, "replaces an expectation recorded under the old help rule; "
    if got is None:
        return "broke", _one_line(f"SETUP-ERROR: {setup}")
    why = f"setup error: {setup}; " if got["verdict"] == "SETUP-ERROR" else ""
    if held is not None and held["baselines"] == got["baselines"]:
        if all(held[k] == got[k] for k in RESULT_FIELDS):
            return "ok", "matches the expectation"
        return "broke", _one_line(why + _differences(held, got))

    def record(source: str, fields: dict) -> tuple[str, str]:
        entry = {k: fields[k] for k in RESULT_FIELDS}
        meta = {"help_rule": HELP_RULE, "source": source, "recorded": _utc()}
        write_json(path, {"version": version, **entry, **meta})
        short = {"watcher-verdict": "watcher", "first-result": "first"}.get(source, source)
        return f"recorded-{short}", _one_line(f"{replaces}source={source}; {_stated(entry)}")

    try:
        fixed = _fixed_expectation(worktree, version, harnesses)
    except CompatInputError as exc:
        return "broke", _one_line(f"the worktree contract is unreadable: {exc}")
    if fixed is not None:
        names = ("verdict", "watched", "help", "harness")
        if all(fixed[k] == got[k] for k in names):
            return record("fixed", got)
        return "broke", _one_line(why + _differences(fixed, got, names))
    final = _read_record(state / "verdicts" / f"{version}.json")
    if (
        final is not None
        and final.get("final") is True
        and _fields_ok(final)
        and "FAIL" not in (final["verdict"], final.get("outcome"))
        and (final := _with_current_help(final, version)) is not None
    ):
        if all(final[k] == got[k] for k in RESULT_FIELDS):
            return record("watcher-verdict", final)
        return "broke", _one_line(why + _differences(final, got))
    if got["verdict"] in ("PASS", "REVIEW"):
        return record("first-result", got)
    if got["verdict"] == "FAIL":
        return "nobasis-fail", _one_line(_stated(got))
    return "broke", _one_line(f"SETUP-ERROR: {setup}")


# ---------------------------------------------------------------------------------------------
# The upgrade reconcile (OPR.99.0.13.3; mission 5.4): at most one open, verifiable request.
# Upgrade-driving rows live only in upgrade.json and are offered only after `plan` re-checked them.

STAMP = "%Y-%m-%dT%H:%M:%SZ"
STAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
UPGRADE_DAYS = 7
OPEN_KEYS = ("key", "version", "from", "integrity", "accept_sha", "evidence", "verdict_sha256",
             "expires_at", "body", "status")  # fmt: skip
CONDITION = (
    "upgrade through the supported path (openrig-upgrade skill: backup, upgrade, verify daemon "
    "and every rig healthy, rollback on failure) at a moment when every running rig's seats are "
    "idle between slices. Paused rigs count as idle."
)


def load_upgrade(state: Path) -> dict:
    """upgrade.json, or an empty one. An unreadable file raises: it is never overwritten."""
    path = state / "upgrade.json"
    if not path.exists():
        return {"open": None, "done": {}, "withdrawals": []}
    data = load_json_file(path, 4 * 1024 * 1024)
    ok = (
        isinstance(data, dict)
        and isinstance(data.get("done"), dict)
        and isinstance(data.get("withdrawals"), list)
        and all(
            isinstance(w, dict)
            and all(isinstance(w.get(k), str) for k in ("key", "version", "evidence", "body"))
            and _strs(w.get("request_ids"))
            for w in data["withdrawals"]
        )
        and "open" in data
        and (
            data["open"] is None
            or (
                isinstance(data["open"], dict)
                and all(isinstance(data["open"].get(k), str) for k in OPEN_KEYS)
                and VERSION_RE.match(data["open"]["version"]) is not None
                and _strs(data["open"].get("request_ids"))
            )
        )
    )
    if not ok:
        raise CompatInputError("upgrade.json: not the upgrade state schema")
    return data


def _accepted_records(state: Path) -> dict[str, dict]:
    """(a): the final `accepted` records that hold an accept sha and an integrity."""
    out: dict[str, dict] = {}
    box = state / "verdicts"
    for name in sorted(os.listdir(box)) if box.is_dir() else []:
        version = name.removesuffix(".json")
        if name == version or not VERSION_RE.match(version):
            continue
        rec = _read_record(box / name)
        if (
            rec is not None
            and rec.get("version") == version
            and rec.get("final") is True
            and rec.get("outcome") == "accepted"
            and isinstance(rec.get("accept_sha"), str)
            and SHA_RE.fullmatch(rec["accept_sha"])
            and isinstance(rec.get("integrity"), str)
            and isinstance(rec.get("evidence"), str)
        ):
            out[version] = rec
    return out


def _accept_present(worktree: Path, sha: str) -> bool:
    """(d): the accept commit is still on the accepted branch."""
    if not SHA_RE.fullmatch(sha):
        return False
    argv = ["git", "-C", str(worktree), "merge-base", "--is-ancestor", sha, ACCEPTED_BRANCH]
    return subprocess.run(argv, capture_output=True, check=False).returncode == 0  # noqa: S603


def _integrities(path: str) -> dict[str, str] | None:
    """This run's npm integrities, or None when unknown."""
    if path == "-":
        return None
    out: dict[str, str] = {}
    for line in read_bytes(Path(path), 1024 * 1024).decode("utf-8", "replace").splitlines():
        version, _, value = line.partition("\t")
        if value:
            out[version] = value
    return out


def _fyi(state: Path, row_dest: str, summary: str, evidence: str, body: str) -> None:
    """One verified FYI row to the advisor, into the generic outbox."""
    if row_dest != "-":
        _, dropped = outbox_add(state, row_dest, summary, evidence or str(state), body, True)
        if dropped:
            print(f"warning: the outbox dropped {dropped}", file=sys.stderr)


def _ids(request_ids: Sequence[str]) -> str:
    return ", ".join(request_ids) if request_ids else "none recorded"


def _withdraw(state: Path, data: dict, reason: str, row_dest: str) -> None:
    """Close the open request as no longer eligible; withdraw it if it may have left."""
    x = data["open"]
    key, version = x["key"], x["version"]
    never_sent = x["status"] == "unsent" and not x["request_ids"]
    summary = f"OpenRig {version}: upgrade request withdrawn [{key}]: {reason}"
    why = f"Request key: {key}\nRequest ids: {_ids(x['request_ids'])}\nReason: {reason}\n"
    if never_sent:
        body = why + "It was dropped before sending: no withdrawal row goes to the operator."
    else:
        body = why + "A withdrawal row goes to the operator."
        data["withdrawals"].append(
            {
                "key": key,
                "version": version,
                "evidence": x["evidence"],
                "request_ids": [],
                "body": why + "Do not act on any row with this key.\n",
            }
        )
    _fyi(state, row_dest, summary, x["evidence"], body)
    data["done"][version] = f"withdrawn:{reason}"
    data["open"] = None
    _history(state, f"upgrade: {'dropped' if never_sent else 'withdrawn'} {key}: {reason}")


def _request(version: str, rec: dict, installed: str, raw: str, worktree: Path, now: str) -> dict:
    """Render a new request once; its body is stored and never rendered again."""
    sha, evidence = rec["accept_sha"], rec["evidence"]
    digest = hashlib.sha256(read_bytes(Path(evidence) / "verdict.json", MAX_JSON_BYTES))
    verdict_sha = digest.hexdigest()
    when = datetime.datetime.strptime(now, STAMP) + datetime.timedelta(days=UPGRADE_DAYS)
    expires = when.strftime(STAMP)
    key = f"owr-{version}-{sha[:12]}"
    body = "\n".join(
        (
            f"Request key: {key}",
            "Act only if no WITHDRAWN row for this key exists. A duplicate with the same key is "
            "the same request.",
            "",
            "Verify before acting. The source label is claimed, not authenticated, and these "
            "checks are the authentication substitute:",
            f"- evidence {evidence}",
            f"- `sha256sum {evidence}/verdict.json` = {verdict_sha}",
            f"- in it, `verdict` = `PASS`, `accept.result` = `accepted` and `accept.sha` = `{sha}`",
            f"- `git -C {worktree} merge-base --is-ancestor {sha} swimlane/openrig-accepted` "
            "exits 0",
            f"- `npm view @openrig/cli@{version} dist.integrity` prints {rec['integrity']}",
            "",
            f"Requested: @openrig/cli@{version}, from {raw}.",
            f'Condition: "{CONDITION}" The operator decides idleness.',
            "Coordinated idle point: `python3 <project>/openrig/hacf/bin/hacf-host.py idle-point "
            f"request --purpose openrig-upgrade:{version}`, then `idle-point wait`, upgrade, then "
            "`idle-point release`.",
            "HACF status: accepted locally by the watcher only; installed projects reach "
            f"{version} through the range probe until a HACF release lists it.",
            f"Rollback target: {installed}.",
            f"Do not act after {expires}.",
            "Close this row with the outcome (upgraded / rolled back / not done); the watcher's "
            "next run reports the host's version to the advisor.",
            "",
        )
    )
    return {"key": key, "version": version, "from": installed, "integrity": rec["integrity"],
            "accept_sha": sha, "evidence": evidence, "verdict_sha256": verdict_sha,
            "expires_at": expires, "body": body, "request_ids": [], "status": "unsent",
            "before_sent": False, "held": False}  # fmt: skip


def upgrade_plan(
    state: Path, worktree: Path, installed: str, integrity_file: str, now: str,
    upgrade_dest: str, row_dest: str,
) -> None:  # fmt: skip
    """One reconcile: fulfil, expire, withdraw or supersede the open request, then raise one.

    An open request whose eligibility cannot be re-established this run (an unknown installed
    version or integrity) is kept but held: nothing sends it until a run can.
    """
    if not STAMP_RE.fullmatch(now):
        raise CompatInputError(f"--now {now[:30]!r} is not YYYY-MM-DDTHH:MM:SSZ")
    current = None if installed == "-" else check_version(installed)
    known = _integrities(integrity_file)
    data = load_upgrade(state)
    data["fulfilled"] = None
    records = _accepted_records(state)

    def above(version: str) -> bool:
        return current is not None and version_key(version) > version_key(current)

    def eligible(version: str) -> bool:
        rec = records[version]
        return (
            above(version)
            and known is not None
            and known.get(version) == rec["integrity"]
            and version not in data["done"]
            and _accept_present(worktree, rec["accept_sha"])
        )

    target = max((v for v in records if eligible(v)), key=version_key, default=None)
    x = data["open"]
    if x is not None:
        version, rec = x["version"], records.get(x["version"])
        reason = None
        if rec is None or rec["accept_sha"] != x["accept_sha"]:
            reason = "verdict-not-accepted"
        elif known is not None and version in known and known[version] != x["integrity"]:
            reason = "integrity-changed"
        elif not _accept_present(worktree, x["accept_sha"]):
            reason = "accept-commit-gone"
        elif current is not None and not above(version):
            reason = "installed-at-or-above"
        if current == version:
            data["fulfilled"] = {"key": x["key"], "request_ids": x["request_ids"]}
            data["done"][version], data["open"] = "fulfilled", None
            _history(state, f"upgrade: fulfilled {x['key']}")
        elif now >= x["expires_at"]:
            _fyi(state, row_dest, f"OpenRig {version}: upgrade request expired [{x['key']}]",
                 x["evidence"], f"Request key: {x['key']}\nRequest ids: "
                 f"{_ids(x['request_ids'])}\nExpired: {x['expires_at']}")  # fmt: skip
            data["done"][version], data["open"] = "expired", None
            _history(state, f"upgrade: expired {x['key']}")
        elif reason:
            _withdraw(state, data, reason, row_dest)
        elif target is not None and version_key(target) > version_key(version):
            _withdraw(state, data, f"superseded-by-{target}", row_dest)
        else:
            x["held"] = not (above(version) and known is not None and version in known)
    if data["open"] is None:
        # again: a version this run fulfilled, expired or withdrew is done now
        target = max((v for v in records if eligible(v)), key=version_key, default=None)
        if target is not None and upgrade_dest == "-":
            _history(state, f"upgrade: no destination for {target}")
        elif target is not None:
            raw = (_read_record(state / "installed.json") or {}).get("raw")
            try:
                data["open"] = _request(
                    target, records[target], current, str(raw or current), worktree, now
                )
            except CompatInputError as exc:
                _history(state, f"upgrade: cannot build the request for {target}: {exc}")
    if target is None and current is not None and records:
        latest = max(records, key=version_key)
        if not above(latest):
            _history(state, f"upgrade: none: {latest} <= installed {current}")
    write_json(state / "upgrade.json", data)


def upgrade_pending(state: Path, out_dir: Path) -> list[str]:
    """One TSV line per send owed (withdrawals first), its row's parts written into out_dir."""
    data = load_upgrade(state)
    owed = [("withdrawal", w, {
        "summary": f"OpenRig {w['version']}: WITHDRAWN: do not upgrade [{w['key']}]",
        "tags": "", "expires": ""}) for w in data["withdrawals"]]  # fmt: skip
    x = data["open"]
    if x is not None and x["status"] == "unsent" and not x.get("held"):
        owed.append(("request", x, {
            "summary": f"OpenRig {x['version']}: PASS: upgrade the host when idle "
                       f"(from {x['from']}) [{x['key']}]",
            "tags": f"openrig-upgrade,openrig:{x['version']}",
            "expires": x["expires_at"]}))  # fmt: skip
    lines = []
    for n, (kind, entry, parts) in enumerate(owed):
        body = out_dir / f"upgrade-{n}.md"
        body.write_text(entry["body"], encoding="utf-8")
        for suffix, value in (*parts.items(), ("evidence", entry["evidence"])):
            body.with_suffix(f".{suffix}").write_text(value, encoding="utf-8")
        last = entry["request_ids"][-1] if entry["request_ids"] else "-"
        lines.append(f"{kind}\t{entry['key']}\t{body}\t{last}")
    return lines


def upgrade_sent(
    state: Path, kind: str, key: str, ok: bool, request_id: str, row_dest: str
) -> None:
    """Record one send of a request or withdrawal."""
    data = load_upgrade(state)
    if kind == "request":
        entry = data["open"] if data["open"] and data["open"]["key"] == key else None
    else:
        entry = next((w for w in data["withdrawals"] if w["key"] == key), None)
    if entry is None:
        raise CompatInputError(f"upgrade.json holds no {kind} {key[:60]!r}")
    if REQUEST_ID_RE.fullmatch(request_id) and request_id not in entry["request_ids"]:
        entry["request_ids"].append(request_id)
    if ok and kind == "withdrawal":
        data["withdrawals"].remove(entry)
    elif ok:
        entry["status"] = "sent"
        version = entry["version"]
        _fyi(state, row_dest,
             f"OpenRig {version}: upgrade requested from operator (from {entry['from']})",
             entry["evidence"], f"Request key: {key}\nRequest ids: "
             f"{_ids(entry['request_ids'])}\nExpires: {entry['expires_at']}")  # fmt: skip
        entry["before_sent"] = True
    write_json(state / "upgrade.json", data)


def upgrade_fulfilled(state: Path) -> str:
    """The request the last plan fulfilled, in words, or `no request`."""
    done = load_upgrade(state).get("fulfilled")
    if not isinstance(done, dict):
        return "no request"
    return f"{done['key']} (request ids: {_ids(done['request_ids'])})"


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
    s = sub.add_parser("provision-ledger")
    s.add_argument("--root", required=True)
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
    s = sub.add_parser("harness")
    s.add_argument("--root", required=True)
    s.add_argument("--package", required=True)
    s.add_argument("--baselines", default="")
    s.add_argument("--work", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--diff-dir", required=True)
    s.add_argument("--harness", action="append")
    s.add_argument("--seed", action="store_true")
    s = sub.add_parser("watcher-versions")
    s.add_argument("--contract", required=True)
    s.add_argument("--plus", default=None)
    s.add_argument("--root", default=None)
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
    s.add_argument("--harness", action="append")
    s = sub.add_parser("accept")
    s.add_argument("--root", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--expect-head", default=None)
    s.add_argument("--started-clean", choices=("yes", "no"), default="yes")
    s.add_argument("--refuse", default=None)
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
    s.add_argument("--stream", choices=STREAMS, default="new-version")
    s = sub.add_parser("watch-outbox")
    s.add_argument("action", choices=("add", "list", "body", "mark", "remove", "dropped"))
    s.add_argument("--state-dir", required=True)
    s.add_argument("--name", default=None)
    s.add_argument("--destination", default=None)
    s.add_argument("--summary", default=None)
    s.add_argument("--evidence-ref", default=None)
    s.add_argument("--body-file", default=None)
    s.add_argument("--verify", choices=("yes", "no"), default="no")
    s.add_argument("--request-id", default=None)
    s.add_argument("--reset", action="store_true")
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
    s = sub.add_parser("watch-installed")
    s.add_argument("--state-dir", required=True)
    s.add_argument("--raw", required=True)
    s = sub.add_parser("watch-selftest")
    for flag in ("--state-dir", "--worktree", "--version", "--evidence", "--rc", "--harness"):
        s.add_argument(flag, required=True)
    s = sub.add_parser("watch-upgrade")
    s.add_argument("action", choices=("plan", "pending", "sent", "fulfilled"))
    s.add_argument("--state-dir", required=True)
    s.add_argument("--worktree", default=None)
    s.add_argument("--installed", default="-")
    s.add_argument("--integrity-file", default="-")
    s.add_argument("--now", default=None)
    s.add_argument("--upgrade-dest", default="-")
    s.add_argument("--row-dest", default="-")
    s.add_argument("--out-dir", default=None)
    s.add_argument("--kind", choices=("request", "withdrawal"), default=None)
    s.add_argument("--key", default=None)
    s.add_argument("--ok", choices=("yes", "no"), default=None)
    s.add_argument("--request-id", default="-")
    return p


def _utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _outbox_cli(ns: argparse.Namespace) -> int:
    """The watch-outbox subcommands."""
    state = Path(ns.state_dir)
    if ns.action == "add":
        if None in (ns.destination, ns.summary, ns.evidence_ref, ns.body_file):
            raise CompatInputError(
                "add needs --destination, --summary, --evidence-ref, --body-file"
            )
        name, dropped = outbox_add(
            state, ns.destination, ns.summary, ns.evidence_ref,
            read_text(Path(ns.body_file)), ns.verify == "yes",
        )  # fmt: skip
        if dropped:
            print(f"dropped {dropped}")
        print(f"entry {name}")
    elif ns.action == "list":
        print("".join(f"{line}\n" for line in outbox_list(state)), end="")
    elif ns.action == "dropped":
        print(outbox_dropped(state, ns.reset))
    else:
        if ns.name is None:
            raise CompatInputError(f"{ns.action} needs --name")
        if ns.action == "body":
            sys.stdout.write(_outbox_entry(state, ns.name)["body"])
        elif ns.action == "mark":
            outbox_mark(state, ns.name, ns.request_id or "")
        else:
            _outbox_entry(state, ns.name)
            (state / "outbox" / ns.name).unlink()
    return EXIT_OK


def _upgrade_cli(ns: argparse.Namespace) -> int:
    """The watch-upgrade subcommands."""
    state = Path(ns.state_dir)
    if ns.action == "plan":
        if ns.worktree is None:
            raise CompatInputError("plan needs --worktree")
        upgrade_plan(state, Path(ns.worktree), ns.installed, ns.integrity_file,
                     ns.now or _utc(), ns.upgrade_dest, ns.row_dest)  # fmt: skip
    elif ns.action == "pending":
        if ns.out_dir is None:
            raise CompatInputError("pending needs --out-dir")
        print("".join(f"{line}\n" for line in upgrade_pending(state, Path(ns.out_dir))), end="")
    elif ns.action == "sent":
        if None in (ns.kind, ns.key, ns.ok):
            raise CompatInputError("sent needs --kind, --key and --ok")
        upgrade_sent(state, ns.kind, ns.key, ns.ok == "yes", ns.request_id, ns.row_dest)
    else:
        print(upgrade_fulfilled(state))
    return EXIT_OK


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
    if ns.cmd == "provision-ledger":
        print(provision_ledger(Path(ns.root)))
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
    if ns.cmd == "harness":
        return run_harness(
            Path(ns.root),
            Path(ns.package),
            _baseline_list(ns.baselines) if ns.baselines else [],
            Path(ns.work),
            Path(ns.out),
            Path(ns.diff_dir),
            ns.harness or ["agy"],
            ns.seed,
        )
    if ns.cmd == "watcher-versions":
        print(watcher_versions(Path(ns.contract), ns.plus, Path(ns.root) if ns.root else None))
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
        if ns.refuse is not None:  # the harness could not build the marker: record, never commit
            result = {"result": "error", "reason": ns.refuse[:500], "sha": None}
        else:
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
        print(watch_state(Path(ns.state_dir), ns.event, ns.version, ns.value, ns.stream))
        return EXIT_OK
    if ns.cmd == "watch-outbox":
        return _outbox_cli(ns)
    if ns.cmd == "watch-final":
        return EXIT_OK if watch_final(Path(ns.state_dir), ns.version) else EXIT_FAIL
    if ns.cmd == "watch-verdict":
        rc = _parse_int(ns.rc) if re.fullmatch(r"-?[0-9]{1,6}", ns.rc) else -1
        print("\t".join(watch_verdict(Path(ns.evidence), check_version(ns.version), rc)))
        return EXIT_OK
    if ns.cmd == "watch-record":
        if not re.fullmatch(r"[A-Za-z-]{1,40}", ns.outcome):
            raise CompatInputError(f"outcome {ns.outcome[:40]!r} is not a word")
        watch_record(Path(ns.state_dir), check_version(ns.version), ns.outcome, ns.evidence,
                     ns.final == "yes")  # fmt: skip
        return EXIT_OK
    if ns.cmd == "watch-installed":
        print(watch_installed(Path(ns.state_dir), ns.raw[:200]))
        return EXIT_OK
    if ns.cmd == "watch-selftest":
        rc = _parse_int(ns.rc) if re.fullmatch(r"-?[0-9]{1,6}", ns.rc) else -1
        names = [n for n in ns.harness.split(",") if HARNESS_NAME_RE.fullmatch(n)]
        print("\t".join(watch_selftest(Path(ns.state_dir), Path(ns.worktree), ns.version,
                                        ns.evidence, rc, names)))  # fmt: skip
        return EXIT_OK
    if ns.cmd == "watch-upgrade":
        return _upgrade_cli(ns)
    raise CompatInputError(f"unknown subcommand {ns.cmd}")  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
