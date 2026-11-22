#!/usr/bin/env python3
"""hyper_spec.py — the one deterministic reader and writer of HACF's spec files.

It reads the project, mission and slice `SPEC.md` files of the v6 layout and the legacy flat
`MiniPRD_*.md` files, and it is the only writer of slice and mission progress and of C1 proof
artifacts, so no skill parses or edits a spec by hand. Normative design:
HACF's v0.6.0 design notes (not shipped): spec frontmatter, progress/proof/notes, key
ownership and OpenRig manifests. The repository is the git work tree of the working directory,
never the directory holding this file, so a copy of the script checks the tree it runs in.

CLI:
    resolve <path | dot-id | legacy path> [--json]
    lint (<path> | --mission <dir> | --all) [--json]
    progress --slice <dir> --event compiled|executing|executed|audit_failed|audited|blocked
             [--detail-file <f>]
    proof --slice <dir> --artifact-type guard|qa|rev1-r1|rev1-r2|adjudication
          --verdict CLEAR|BLOCKING|CONCERNING|PASS|NOT-CLEAR --candidate-sha <sha|auto>
          --money-evidence <line> --file <f> --name <NN>-<type>.md [--swimlane-sha <sha>]
    status [--json]

Exit codes: 0 ok, 1 findings (lint errors), 2 usage, 3 lock timeout, 4 refused, 5 conflict.

Semantics:
- resolve takes an existing path (a spec file or its directory), a dot-ID (scanned in the `id`
  frontmatter of every mission and slice, closed/ included) or a legacy flat path that no longer
  exists. A legacy path is looked up in the migration journal's top-level `legacy_map`
  (`.agents/state/migrations/v0.6.0.json`), else by an exact repo-relative match on
  `hacf_legacy_source`; never by a slug or a name fragment. Fields a legacy flat MiniPRD lacks
  are null; nothing is inferred from prose.
- lint checks frontmatter, dot-IDs, `depends_on` targets, safe `hacf_touches` globs, the
  per-type headings as exact strings (the alias table maps the two legacy headings and applies
  only in legacy flat files), rig's two content rules and the C1 proof headers. Findings in
  legacy flat files are warnings, so only errors in v6 files make it exit 1. Its output holds
  no counts, line numbers or absolute paths, because the bootstrap gate compares it line by
  line with an older copy's output.
- progress applies one transition of the table in TRANSITIONS, then rewrites the slice SPEC.md
  `hacf_status` and its mapped OpenRig `status`, the generated regions of the slice and mission
  PROGRESS.md, one appended Outcomes line, and on the first executing event mission.yaml
  `metadata.status` planned -> active (bytes from hyper_openrig.splice_mission_status). Every
  check runs before the first write.
- proof writes `proof/<name>` with the C1 header (delegated to `rig proof add` under an accepted
  rig, 0.6.13 or 0.7.16) and lists it in PROOF.md `## Artifacts`. `--candidate-sha auto` is HEAD
  when the slice's `hacf_touches` are clean, else a snapshot commit from a temp index seeded from
  HEAD, stored at `refs/hacf/candidates/<slice-id>`; the user's index and work tree are untouched.
- Writes take `$(git rev-parse --git-common-dir)/hacf/locks/spec.lock` and are compare-and-swap
  (hyper_openrig.cas_write: sha256 at read, re-read just before os.replace, exit 5 on a change),
  because rig writes the same files without HACF's locks. With HACF_OUTBOX set, progress writes
  one outbox entry (`{"tool": "hyper_spec", "op": "progress", "args"}`, hyper_ledger's format)
  and takes no lock, and `proof --candidate-sha auto` is refused (exit 4): the engine writes
  PROOF and candidate refs (R1).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple

import _runtime
import hyper_ledger
import hyper_openrig

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_LOCK_TIMEOUT = 3
EXIT_REFUSED = 4
EXIT_CONFLICT = 5

LOCK_NAME = "spec.lock"
LOCK_TIMEOUT = 30.0  # seconds a write waits for spec.lock before exit 3
LOCK_POLL = 0.05
GIT_TIMEOUT = 60
OUTBOX_TOOL = "hyper_spec"
JOURNAL_REL = ".agents/state/migrations/v0.6.0.json"
JOURNAL_MAP_KEY = "legacy_map"
LEGACY_DIRS = ("spec/compiled", "spec/archive")
LEGACY_NAME_RE = re.compile(r"^MiniPRD_.+\.md$")
LEGACY_ORDER_RE = re.compile(r"^MiniPRD_(\d+)_")
LEGACY_PROGRESS_NOTICE = "legacy flat MiniPRD: progress skipped (no slice dir)"
LEGACY_PROOF_NOTICE = "legacy flat MiniPRD: proof skipped (no slice dir)"

PROJECT, MISSION, SLICE, LEGACY = "project", "mission", "slice", "legacy"
HACF_TYPES = {PROJECT: "SystemOfRecord", MISSION: "SuperPRD", SLICE: "MiniPRD"}
HACF_STATUSES = {
    MISSION: ("draft", "compiled", "in_progress", "complete", "superseded"),
    SLICE: (
        "compiled",
        "executing",
        "executed",
        "audit_failed",
        "audited",
        "blocked",
        "superseded",
    ),
}
STAGES = ("wip", "provisional", "established", "canonical", "superseded", "retired")
SLICE_DIR_KINDS = ("slices", "closed")  # closed/ is rig's close convention

# schemas.md#key-ownership: the frontmatter keys HACF owns (fnmatch patterns) and the keys it
# shares with rig, per file type. splice_owned() rewrites no other key.
KEY_OWNERSHIP = {
    PROJECT: {"owned": ("hacf_schema", "hacf_type", "hacf_updated"), "shared": ("intent",)},
    MISSION: {
        "owned": ("hacf_*", "node_id", "lore_refs"),
        "shared": ("id", "mission", "stage", "verified", "created", "intent", "depends_on"),
    },
    SLICE: {
        "owned": ("hacf_*", "node_id", "parent", "lore_refs"),
        "shared": (
            "status",  # only values of the hacf_status mapping (hyper_openrig.STATUS_MAP)
            "stage",
            "verified",
            "id",
            "slice",
            "mission",
            "created",
            "intent",
            "depends_on",
        ),
    },
}

# Headings (schemas.md#spec-frontmatter "Lint section rules"), compared as exact strings.
SLICE_HEADINGS = (
    "## Intent",
    "## 1. The Confidence Mandate",
    "## Mini-requirements",
    "## 3. Implementation Plan (Task List)",
    "## 4. The Negative Space (Constraints)",
    "## Proof contract",
    "## Source material",
    "## Intent visual",
    "## Status",
    "## Dependencies",
)
REQUIRED_HEADINGS = {
    PROJECT: ("## Intent", "## Product Context"),
    MISSION: ("## Intent", "## Slices", "## Status"),
    SLICE: SLICE_HEADINGS,
}
# The exact alias table: legacy heading -> v6 heading, accepted only in legacy flat files.
LEGACY_HEADING_ALIASES = {
    "## 2. Atomic User Stories": "## Mini-requirements",
    "## 5. Integration Tests & Verification": "## Proof contract",
}
SLICE_H1_PREFIX = "# MiniPRD: "
TITLE_PREFIXES = ("# MiniPRD: ", "# SuperPRD: ", "# Project - ", "# ")
MINI_REQUIREMENTS = "## Mini-requirements"
PROOF_CONTRACT = "## Proof contract"
# Rig's content rules (scope-audit.ts and scaffold-placeholder.ts @v0.6.13; unchanged at 0.7.16 per
# release-0.6.1 slice 9 proof/contract-compare.md (f)): an authored numbered item (`1.` or `1)`)
# and an authored checkbox item (`- [ ]`, `- [x]`); a fully bracket-wrapped text is a scaffold
# placeholder, not authored content.
NUMBERED_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
CHECKBOX_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.*)$")
PLACEHOLDER_RE = re.compile(r"^\[.*\]$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
REGION_MARKER_RE = re.compile(r"^<!-- (?:BEGIN|END) HACF GENERATED: [a-z][a-z0-9-]{0,63} -->$")

# progress (Task 17): hacf_status -> the events allowed from it. None is a slice SPEC.md with no
# hacf_status yet, whose first record is compiled.
EVENTS = ("compiled", "executing", "executed", "audit_failed", "audited", "blocked")
TRANSITIONS = {
    None: ("compiled",),
    "compiled": ("executing",),
    "executing": ("executed", "blocked"),
    "executed": ("audited", "audit_failed", "executing"),
    "audit_failed": ("executing",),
    "blocked": ("executing",),
}
CURRENT_STATE = "current-state"  # the slice PROGRESS.md region
SLICE_TABLE = "slice-table"  # the mission PROGRESS.md region
CURRENT_STATE_HEADING = "## Current state"
OUTCOMES_HEADING = "## Outcomes"
OUTCOME_RE = re.compile(r"^(?:- )?(\S+) ([a-z_]+)(?: - (.*))?$")
MISSION_ACCEPTANCE = (
    "- [ ] Scope complete",
    "- [ ] Implementation in progress",
    "- [ ] QA / review pass",
    "- [ ] Merge / ship",
)

# proof
PROOF_NAME_RE = re.compile(r"^\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
ARTIFACTS_HEADINGS = ("## Artifacts", "## Artifacts (media in proof/)")  # HACF's, rig's template
CANDIDATE_REF = "refs/hacf/candidates/"
LEGACY_UNRECORDED = "legacy-unrecorded"

RESOLVE_KEYS = (
    "layout",
    "id",
    "slice",
    "mission",
    "path",
    "title",
    "node_id",
    "parent",
    "hacf_status",
    "status",
    "depends_on",
    "touches",
    "oracle",
    "legacy_source",
    "legacy_order",
    "warnings",
    "errors",
)


class SpecError(Exception):
    """A failure the CLI reports with its exit code."""

    code = EXIT_REFUSED


class UsageError(SpecError):
    """Bad arguments, or a target that names no spec (exit 2)."""

    code = EXIT_USAGE


class LockTimeout(SpecError):
    """spec.lock stayed taken for LOCK_TIMEOUT seconds (exit 3)."""

    code = EXIT_LOCK_TIMEOUT


class Refused(SpecError):
    """A refused change or an unreadable spec; nothing was written (exit 4)."""

    code = EXIT_REFUSED


class Conflict(SpecError):
    """A file changed between read and replace, or bad region markers (exit 5)."""

    code = EXIT_CONFLICT


class Finding(NamedTuple):
    """One lint or proof-check finding about a repo-relative file."""

    severity: str  # error | warning
    path: str
    code: str
    message: str

    def line(self) -> str:
        """Return the finding as one output line (no line numbers, no counts)."""
        return f"{self.severity}: {self.path}: {self.code}: {self.message}"


# ---------------------------------------------------------------------------
# git, paths and files
# ---------------------------------------------------------------------------


def _git(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run git with an argv list (never a shell) and capture its output; never raises on exit."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 127, "", str(exc))


def _git_out(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Return git's stdout, or refuse with its stderr when it fails."""
    proc = _git(args, cwd, env)
    if proc.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_top() -> Path:
    """Return the work-tree top level of the working directory (never this file's)."""
    proc = _git(["rev-parse", "--show-toplevel"], Path.cwd())
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        raise UsageError("not inside a git work tree; run hyper_spec.py from the repository")
    return Path(top).resolve()


def missions_root(top: Path) -> Path:
    """Return the missions directory rig uses (project.yaml missions.root, else missions/)."""
    try:
        return Path(os.path.realpath(hyper_openrig.missions_root(top)))
    except hyper_openrig.OpenRigError as exc:
        raise Refused(str(exc)) from None


def _rel(path: Path, top: Path) -> str:
    """Return path relative to top in posix form, or as given when it lies outside top."""
    for candidate in (Path(os.path.abspath(path)), Path(path).resolve()):
        with contextlib.suppress(ValueError):
            return candidate.relative_to(top).as_posix()
    return str(path)


def _read(path: Path) -> str:
    """Return a file's text exactly (UTF-8, no newline translation), so its sha is the file's."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{path} is not UTF-8 text") from None


def _read_optional(path: Path) -> str | None:
    """Return a file's text, or None when it does not exist."""
    try:
        return _read(path)
    except FileNotFoundError:
        return None


def _text_sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _lines(text: str) -> list[str]:
    """Split text on LF, keeping any CR, so joining with LF gives the same bytes back."""
    return text.split("\n")


def _fenced(lines: list[str]) -> list[bool]:
    """Flag each line that belongs to a fenced code block (``` or ~~~, fence lines included)."""
    flags, fence = [], None
    for line in lines:
        text = line.rstrip("\r")
        match = FENCE_RE.match(text)
        if fence is None:
            if match:
                fence = match.group(1)
            flags.append(fence is not None)
            continue
        flags.append(True)
        stripped = text.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
    return flags


def _heading_lines(text: str) -> list[tuple[int, str]]:
    """Return (index, text) for every `#` and `##` heading line outside fenced code."""
    lines = _lines(text)
    return [
        (index, line.rstrip())
        for index, (line, code) in enumerate(zip(lines, _fenced(lines)))
        if not code and (line.startswith("# ") or line.startswith("## "))
    ]


def _section(text: str, headings: tuple[str, ...]) -> tuple[int, int] | None:
    """Return the line span (heading index, end) of the first section titled one of headings.

    The section ends at the next `#` or `##` heading outside fenced code, else at the end.
    """
    found = _heading_lines(text)
    for position, (index, heading) in enumerate(found):
        if heading in headings:
            end = found[position + 1][0] if position + 1 < len(found) else len(_lines(text))
            return index, end
    return None


def _section_items(text: str, heading: str) -> list[str] | None:
    """Return the lines of a section outside fenced code (None when the heading is absent)."""
    span = _section(text, (heading,))
    if span is None:
        return None
    lines = _lines(text)
    flags = _fenced(lines)
    return [lines[i].rstrip("\r") for i in range(span[0] + 1, span[1]) if not flags[i]]


def _h1(text: str) -> str | None:
    return next((line for _, line in _heading_lines(text) if line.startswith("# ")), None)


def _title(text: str) -> str | None:
    """Return the H1 text after its known prefix (`# MiniPRD: `, `# SuperPRD: `, ...)."""
    h1 = _h1(text)
    if h1 is None:
        return None
    prefix = next(p for p in TITLE_PREFIXES if h1.startswith(p))
    return h1[len(prefix) :].strip() or None


# ---------------------------------------------------------------------------
# Frontmatter: reader and owned-key splice writer
# ---------------------------------------------------------------------------


class Frontmatter(NamedTuple):
    """A document's frontmatter mapping, its keys in file order, and the body after it."""

    data: dict | None  # None when the document has no leading `---` block
    keys: list[str]
    body: str


def parse_frontmatter(text: str) -> Frontmatter:
    """Split a leading `---` block, parse it with yaml.safe_load and keep its key order.

    The keys come from the composed node, that is from the raw lines, so their order is the
    file's. A block that does not parse, or is not a mapping, is refused (exit 4).
    """
    match = hyper_openrig.FRONTMATTER_RE.match(text)
    if match is None:
        return Frontmatter(None, [], text)
    try:
        root = yaml.compose(match.group(1), Loader=yaml.SafeLoader)
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise Refused(f"frontmatter is not valid YAML: {exc}") from None
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise Refused("frontmatter is not a mapping")
    keys = [key.value for key, _ in root.value] if isinstance(root, yaml.MappingNode) else []
    body = text[match.end() :]
    body = body[2:] if body.startswith("\r\n") else body[1:] if body.startswith("\n") else body
    return Frontmatter(data, keys, body)


def may_write(kind: str, key: str) -> bool:
    """Return True when HACF owns or shares a frontmatter key of a kind's SPEC.md."""
    rules = KEY_OWNERSHIP[kind]
    owned = any(fnmatch.fnmatchcase(key, pattern) for pattern in rules["owned"])
    return owned or key in rules["shared"]


def splice_owned(text: str, kind: str, updates: dict[str, Any]) -> str:
    """Return SPEC.md text with only the given frontmatter keys rewritten.

    Each key must be one HACF owns or shares for the kind (KEY_OWNERSHIP); every other key keeps
    its bytes and its place (OpenRig's updateFrontmatter rule, done by
    hyper_openrig.splice_frontmatter). A slice's `status` takes only a value of the hacf_status
    mapping.
    """
    for key in updates:
        if not may_write(kind, key):
            raise UsageError(
                f"{kind} SPEC.md key {key!r} is neither HACF-owned nor shared "
                "(schemas.md#key-ownership)"
            )
    mapped = set(hyper_openrig.STATUS_MAP.values())
    if kind == SLICE and "status" in updates and updates["status"] not in mapped:
        raise UsageError(f"slice status {updates['status']!r} is not a mapped OpenRig status")
    try:
        return hyper_openrig.splice_frontmatter(text, updates)
    except hyper_openrig.UsageError as exc:
        raise UsageError(str(exc)) from None
    except hyper_openrig.OpenRigError as exc:
        raise Refused(str(exc)) from None


# ---------------------------------------------------------------------------
# Lock, compare-and-swap writes and the outbox
# ---------------------------------------------------------------------------


def lock_path(top: Path) -> Path:
    """Return `<git common dir>/hacf/locks/spec.lock`, shared by every worktree (R22.1)."""
    common = _git(["rev-parse", "--git-common-dir"], top)
    text = common.stdout.strip()
    if common.returncode != 0 or not text:
        raise UsageError(f"cannot find the git common dir of {top}")
    path = Path(text)
    return (path if path.is_absolute() else top / path).resolve() / "hacf" / "locks" / LOCK_NAME


@contextlib.contextmanager
def spec_lock(top: Path, timeout: float | None = None) -> Iterator[Path]:
    """Hold spec.lock; raise LockTimeout after `timeout` (LOCK_TIMEOUT) seconds.

    Callers take it before they read the files they write; the lock directory is created on
    first use. rig never takes it, which is why every write is also compare-and-swap.
    """
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    lock = lock_path(top)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"timed out after {timeout:g}s waiting for the spec lock {lock}"
                    ) from None
                time.sleep(LOCK_POLL)
        yield lock
    finally:
        os.close(fd)  # closing the descriptor releases the lock


class Write(NamedTuple):
    """One planned file write: the text read (None: the file did not exist) and the new text."""

    path: Path
    original: str | None
    text: str


def cas_write(path: Path, original: str | None, text: str) -> None:
    """Replace path with text unless its bytes changed since they were read as original.

    The protocol is hyper_openrig.cas_write's: a temp file in the same directory, fsync, a
    re-read and sha comparison just before os.replace, an fsync of the directory, and the file
    mode kept. original None means the file must not exist yet. A change is a Conflict (exit 5).
    """
    try:
        hyper_openrig.cas_write(path, original, text)
    except hyper_openrig.Refused as exc:
        raise Conflict(str(exc)) from None


def _undo(done: list[Write]) -> list[str]:
    """Put back what apply_writes wrote; return the paths it could not restore."""
    left = []
    for write in reversed(done):
        try:
            if write.original is None:
                if _current_sha(write.path) != _text_sha(write.text):
                    raise OSError("changed again")
                os.unlink(write.path)
            else:
                hyper_openrig.cas_write(write.path, write.text, write.original)
        except (OSError, hyper_openrig.OpenRigError):
            left.append(str(write.path))
    return left


def apply_writes(writes: list[Write]) -> list[Path]:
    """Write every changed file compare-and-swap, all or nothing; return the written paths.

    Every file is checked against the bytes read before the first write; a write that fails
    part-way puts back the files already written.
    """
    pending = [write for write in writes if write.text != write.original]
    for write in pending:
        if write.path.is_symlink():
            raise Refused(f"{write.path} is a symlink; HACF writes only regular files")
        if _current_sha(write.path) != _text_sha(write.original):
            raise Conflict(
                f"{write.path} changed since it was read; nothing was written (re-read, retry)"
            )
    done: list[Write] = []
    try:
        for write in pending:
            cas_write(write.path, write.original, write.text)
            done.append(write)
    except SpecError as exc:
        left = _undo(done)
        if left:
            raise type(exc)(f"{exc}; could not restore {', '.join(left)}") from None
        raise
    except BaseException:
        _undo(done)
        raise
    return [write.path for write in pending]


def write_outbox_entry(directory: Path, op: str, args: dict) -> Path:
    """Write `{"tool": "hyper_spec", "op", "args"}` as the next numbered outbox entry.

    The format is hyper_ledger.write_outbox_entry's (`<6-digit sequence>-<op>.json`, numbered
    after every tool's entries in the directory); the name is claimed with O_EXCL, so no lock
    is taken.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"tool": OUTBOX_TOOL, "op": op, "args": args}, indent=2, sort_keys=True)
    for _ in range(10000):
        numbers = [
            int(match.group(1))
            for name in os.listdir(directory)
            if (match := hyper_ledger.OUTBOX_ENTRY_RE.match(name))
        ]
        target = directory / f"{max(numbers, default=0) + 1:06d}-{op}.json"
        try:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return target
    raise Refused(f"could not claim an outbox entry name in {directory}")


# ---------------------------------------------------------------------------
# Spec files and layout detection
# ---------------------------------------------------------------------------


class Spec(NamedTuple):
    """A spec file with its layout: project, mission, slice or legacy."""

    layout: str
    path: Path  # the file itself, absolute
    rel: str  # repo-relative
    text: str
    front: dict | None
    body: str  # the text after the frontmatter (the whole text when there is none)
    front_error: str | None  # why the frontmatter did not parse, for lint


def _is_legacy_path(rel: str) -> bool:
    """A legacy flat MiniPRD is a `MiniPRD_*.md` under spec/compiled/ or spec/archive/."""
    name = rel.rsplit("/", 1)[-1]
    return bool(LEGACY_NAME_RE.match(name)) and any(rel.startswith(d + "/") for d in LEGACY_DIRS)


def _location_kind(path: Path, top: Path, mroot: Path) -> str | None:
    """Return the layout a SPEC.md path implies by where it lives, or None."""
    if path.name != "SPEC.md":
        return None
    folder = path.parent
    if folder == top:
        return PROJECT
    if folder.parent == mroot:
        return MISSION
    if folder.parent.name in SLICE_DIR_KINDS and folder.parent.parent.parent == mroot:
        return SLICE
    return None


def load_spec(path: Path, top: Path, mroot: Path) -> Spec:
    """Read a spec file (or a directory's SPEC.md) and detect its layout (Task 7).

    A SPEC.md takes its layout from where it lives (root, mission or slice dir), else from its
    `hacf_type`; a MiniPRD SPEC.md must live in a mission's slices/ or closed/. A
    `MiniPRD_*.md` under spec/compiled/ or spec/archive/ is a legacy flat file.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "SPEC.md"
    if not path.is_file():
        raise UsageError(f"{path}: no such spec file")
    path = path.parent.resolve() / path.name
    rel = _rel(path, top)
    text = _read(path)
    if _is_legacy_path(rel):
        return Spec(LEGACY, path, rel, text, None, text, None)
    try:
        front, error = parse_frontmatter(text), None
    except Refused as exc:
        front, error = Frontmatter(None, [], text), str(exc)
    kind = _location_kind(path, top, mroot)
    if kind is None:
        declared = front.data.get("hacf_type") if front.data else None
        kind = next((k for k, v in HACF_TYPES.items() if v == declared), None)
        if kind == SLICE:
            raise UsageError(
                f"{rel}: a MiniPRD SPEC.md must live in <missions>/<mission>/slices/<NN-slug>/ "
                "or closed/<NN-slug>/"
            )
        if kind is None:
            raise UsageError(
                f"{rel} is not a spec: neither a SPEC.md of the v6 layout nor a legacy flat "
                "MiniPRD_*.md in spec/compiled/ or spec/archive/"
            )
    return Spec(kind, path, rel, text, front.data, front.body, error)


def mission_dirs(mroot: Path) -> list[Path]:
    """Return the mission directories (holding SPEC.md or mission.yaml), sorted by name."""
    if not mroot.is_dir():
        return []
    return [
        entry
        for entry in sorted(mroot.iterdir())
        if entry.is_dir()
        and not entry.name.startswith(".")
        and ((entry / "SPEC.md").is_file() or (entry / "mission.yaml").is_file())
    ]


def slice_dirs(mission_dir: Path) -> list[Path]:
    """Return a mission's slice directories: slices/ then closed/, each sorted by name."""
    found = []
    for kind in SLICE_DIR_KINDS:
        folder = Path(mission_dir) / kind
        if folder.is_dir():
            found += [
                entry
                for entry in sorted(folder.iterdir())
                if entry.is_dir() and not entry.name.startswith(".")
            ]
    return found


def spec_files(mroot: Path) -> list[Path]:
    """Return every mission and slice SPEC.md under the missions root, in a fixed order."""
    files = []
    for mission in mission_dirs(mroot):
        files += [p for p in [mission / "SPEC.md"] if p.is_file()]
        files += [d / "SPEC.md" for d in slice_dirs(mission) if (d / "SPEC.md").is_file()]
    return files


def _front_of(path: Path) -> dict:
    """Return a file's frontmatter mapping, or {} when it is missing or does not parse."""
    try:
        return parse_frontmatter(_read(path)).data or {}
    except (OSError, SpecError):
        return {}


def _mission_status(mission_dir: Path) -> Any:
    """Return mission.yaml metadata.status, or None when it is absent or unreadable."""
    try:
        data = yaml.safe_load(_read(Path(mission_dir) / "mission.yaml"))
    except (OSError, SpecError, yaml.YAMLError):
        return None
    metadata = data.get("metadata") if isinstance(data, dict) else None
    return metadata.get("status") if isinstance(metadata, dict) else None


def _dependencies(slice_dir: Path, front: dict) -> tuple[Any, str]:
    """Return a slice's raw depends_on and its source; slice.yaml execution.depends_on wins."""
    manifest = Path(slice_dir) / "slice.yaml"
    if manifest.is_file():
        try:
            data = yaml.safe_load(_read(manifest))
        except yaml.YAMLError as exc:
            raise Refused(f"{manifest} is not valid YAML: {exc}") from None
        execution = data.get("execution") if isinstance(data, dict) else None
        if isinstance(execution, dict) and "depends_on" in execution:
            return execution["depends_on"], "slice.yaml"
    return front.get("depends_on"), "SPEC.md"


def _id_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Proof checks (shared by resolve, status and lint)
# ---------------------------------------------------------------------------


def commit_exists(top: Path, sha: str) -> bool:
    """Return True when sha names a commit in the repository (`git cat-file -e <sha>^{commit}`)."""
    return _git(["cat-file", "-e", f"{sha}^{{commit}}"], top).returncode == 0


def candidate_findings(header: dict, rel: str, top: Path) -> list[Finding]:
    """Check one C1 header's candidate_sha (R22.8).

    `legacy-unrecorded` is valid. An unreachable candidate_sha is a historical warning when the
    proof carries swimlane_sha (a squash landing dropped the build branch) and an error without.
    """
    candidate, swimlane = header.get("candidate_sha"), header.get("swimlane_sha")
    if swimlane is not None and not (
        isinstance(swimlane, str) and hyper_openrig.SHA_RE.match(swimlane)
    ):
        return [Finding("error", rel, "proof_c1_invalid", f"swimlane_sha {swimlane!r} is no sha")]
    if candidate == LEGACY_UNRECORDED:
        return []
    if not isinstance(candidate, str) or not hyper_openrig.SHA_RE.match(candidate):
        return [
            Finding(
                "error",
                rel,
                "proof_c1_invalid",
                f"candidate_sha {candidate!r} is neither a commit sha nor {LEGACY_UNRECORDED}",
            )
        ]
    if commit_exists(top, candidate):
        return []
    if swimlane:
        return [
            Finding(
                "warning",
                rel,
                "proof_candidate_historical",
                f"candidate_sha {candidate} is not reachable here; historical "
                f"(swimlane_sha {swimlane})",
            )
        ]
    return [
        Finding(
            "error",
            rel,
            "proof_candidate_unreachable",
            f"candidate_sha {candidate} is not a reachable commit and the proof has no "
            "swimlane_sha",
        )
    ]


def proof_findings(slice_dir: Path, top: Path) -> list[Finding]:
    """Check the C1 header and candidate_sha of every proof/*.md of a slice."""
    proof = Path(slice_dir) / "proof"
    if not proof.is_dir():
        return []
    found = []
    for entry in sorted(proof.iterdir()):
        if entry.suffix.lower() != ".md" or not entry.is_file():
            continue
        rel = _rel(entry, top)
        try:
            text = _read(entry)
        except SpecError as exc:
            found.append(Finding("error", rel, "proof_c1_invalid", str(exc)))
            continue
        problems = hyper_openrig.c1_problems(text)
        if problems:
            found.append(Finding("error", rel, "proof_c1_invalid", "; ".join(problems)))
            continue
        found += candidate_findings(hyper_openrig.split_frontmatter(text)[2], rel, top)
    return found


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def _header_value(match: re.Match | None) -> str | None:
    """Clean a legacy header value; `none` (any case) and an empty value mean no value."""
    if match is None:
        return None
    value = match.group(1).strip().strip("`'\"").strip()
    return None if not value or value.lower() == "none" else value


def describe(spec: Spec, top: Path) -> dict[str, Any]:
    """Return the resolve record of a spec (Task 11); fields a layout lacks are null."""
    result: dict[str, Any] = dict.fromkeys(RESOLVE_KEYS)
    result.update(layout=spec.layout, path=spec.rel, title=_title(spec.body))
    warnings: list[str] = []
    errors: list[str] = []
    result["warnings"], result["errors"] = warnings, errors
    if spec.layout == LEGACY:
        order = LEGACY_ORDER_RE.match(spec.path.name)
        result.update(
            node_id=_header_value(hyper_ledger.LEGACY_ID_RE.search(spec.text)),
            parent=_header_value(hyper_ledger.LEGACY_PARENT_RE.search(spec.text)),
            legacy_order=int(order.group(1)) if order else None,
        )
        return result
    if spec.front_error:
        warnings.append(f"{spec.rel}: frontmatter_invalid: {spec.front_error}")
    front = spec.front or {}
    result.update(hacf_status=front.get("hacf_status"), node_id=front.get("node_id"))
    if spec.layout == PROJECT:
        return result
    result["id"] = front.get("id")
    if spec.layout == MISSION:
        result.update(
            mission=front.get("mission") or spec.path.parent.name,
            status=_mission_status(spec.path.parent),
            depends_on=_id_list(front.get("depends_on")),
        )
        return result
    slice_dir = spec.path.parent
    depends_on, _ = _dependencies(slice_dir, front)
    hacf_status = front.get("hacf_status")
    status = hyper_openrig.STATUS_MAP.get(hacf_status) if isinstance(hacf_status, str) else None
    if status is None:
        warnings.append(f"{spec.rel}: hacf_status {hacf_status!r} has no OpenRig status mapping")
    result.update(
        slice=front.get("slice") or slice_dir.name,
        mission=front.get("mission") or slice_dir.parent.parent.name,
        parent=front.get("parent"),
        status=status,
        depends_on=_id_list(depends_on),
        touches=front.get("hacf_touches"),
        oracle=front.get("hacf_oracle"),
        legacy_source=front.get("hacf_legacy_source"),
        legacy_order=front.get("hacf_legacy_order"),
    )
    for finding in proof_findings(slice_dir, top):
        (errors if finding.severity == "error" else warnings).append(
            f"{finding.path}: {finding.code}: {finding.message}"
        )
    return result


def find_by_dot_id(dot_id: str, mroot: Path) -> Path:
    """Return the one mission or slice SPEC.md whose frontmatter id is dot_id (closed/ too)."""
    matches = [path for path in spec_files(mroot) if _front_of(path).get("id") == dot_id]
    if not matches:
        raise UsageError(f"no mission or slice SPEC.md has id {dot_id}")
    if len(matches) > 1:
        raise Refused(
            f"{dot_id} is the id of more than one SPEC.md: {', '.join(map(str, matches))}"
        )
    return matches[0]


def _journal_map(top: Path) -> dict:
    """Return the migration journal's legacy_map ({} when the machine-local journal is absent)."""
    journal = top / JOURNAL_REL
    try:
        data = json.loads(_read(journal))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, SpecError) as exc:
        print(f"hyper_spec.py: warning: {journal} is not readable ({exc})", file=sys.stderr)
        return {}
    mapping = data.get(JOURNAL_MAP_KEY) if isinstance(data, dict) else None
    return mapping if isinstance(mapping, dict) else {}


def find_by_legacy_path(rel: str, top: Path, mroot: Path) -> Path | None:
    """Return the slice a migrated legacy flat path became, or None (Task 10).

    The journal's legacy_map wins; without it (teammates and CI have no journal) the path must
    equal a slice's `hacf_legacy_source` exactly. No slug or name-fragment matching.
    """
    mapped = _journal_map(top).get(rel)
    if isinstance(mapped, str):
        target = top / mapped
        target = target / "SPEC.md" if target.is_dir() else target
        if target.is_file():
            return target
    matches = [
        path
        for path in spec_files(mroot)
        if path.parent.parent.name in SLICE_DIR_KINDS
        and _front_of(path).get("hacf_legacy_source") == rel
    ]
    if len(matches) > 1:
        raise Refused(f"{rel} is the hacf_legacy_source of more than one slice")
    return matches[0] if matches else None


def locate(target: str, top: Path, mroot: Path) -> Path:
    """Return the spec file a resolve argument names: a path, a dot-ID or a legacy path."""
    path = Path(target)
    if path.exists():
        return path
    if hyper_openrig.DOT_ID_RE.match(target):
        return find_by_dot_id(target, mroot)
    found = find_by_legacy_path(_rel(path, top), top, mroot)
    if found is None:
        raise UsageError(
            f"cannot resolve {target!r}: no such path, not a dot-ID, and no migrated slice "
            "records it (journal legacy_map or hacf_legacy_source)"
        )
    return found


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def known_ids(mroot: Path) -> set[str]:
    """Return the ids of every mission and slice SPEC.md (closed/ included)."""
    return {i for i in (_front_of(p).get("id") for p in spec_files(mroot)) if isinstance(i, str)}


def _has_authored(items: list[str], pattern: re.Pattern, *, empty_counts: bool) -> bool:
    """Rig's authored-item test: a matching line whose text is not a bracketed placeholder."""
    for line in items:
        match = pattern.match(line)
        if match:
            text = match.group(1).strip()
            if (text or empty_counts) and not PLACEHOLDER_RE.match(text):
                return True
    return False


def _lint_headings(spec: Spec, add: Callable[[str, str, str], None]) -> None:
    """Check the H1, the required headings and their order, and rig's content rules."""
    legacy = spec.layout == LEGACY
    kind = SLICE if legacy else spec.layout
    migrated = kind == SLICE and bool(spec.front and spec.front.get("hacf_legacy_source"))
    required = REQUIRED_HEADINGS[kind]
    strict = "warning" if legacy else "error"
    first: dict[str, int] = {}
    for index, line in _heading_lines(spec.body):
        if line in LEGACY_HEADING_ALIASES:
            alias = LEGACY_HEADING_ALIASES[line]
            if legacy:
                add("warning", "legacy_heading", f"{line!r} is the legacy form of {alias!r}")
                first.setdefault(alias, index)
            else:
                add("error", "legacy_heading", f"{line!r} is accepted only in legacy flat files")
        elif line in required:
            first.setdefault(line, index)
    if kind == SLICE and not (_h1(spec.body) or "").startswith(SLICE_H1_PREFIX):
        add(strict, "h1_invalid", f"the H1 must start with {SLICE_H1_PREFIX!r}")
    for heading in required:
        if heading not in first:
            add(strict, "heading_missing", f"missing required heading {heading!r}")
    found = sorted(first, key=first.get)
    expected = [heading for heading in required if heading in first]
    if found != expected:
        k = next(i for i, (a, b) in enumerate(zip(found, expected)) if a != b)
        add("warning", "heading_order", f"{found[k]!r} comes where {expected[k]!r} belongs")
    if kind != SLICE or legacy:
        return  # legacy flat files skip rig's content rules
    level = "warning" if migrated else "error"
    items = _section_items(spec.body, MINI_REQUIREMENTS)
    if items is not None and not _has_authored(items, NUMBERED_ITEM_RE, empty_counts=False):
        add(
            level,
            "mini_requirements_missing_or_malformed",
            f"{MINI_REQUIREMENTS!r} carries no numbered items ('1. ...')",
        )
    items = _section_items(spec.body, PROOF_CONTRACT)
    if items is not None and not _has_authored(items, CHECKBOX_ITEM_RE, empty_counts=True):
        add(
            level,
            "proof_contract_missing_or_malformed",
            f"{PROOF_CONTRACT!r} carries no checkbox items ('- [ ] ...')",
        )


def _lint_frontmatter(spec: Spec, ids: set[str], add: Callable[[str, str, str], None]) -> None:
    """Check intent, hacf_type, hacf_status, stage, the dot-IDs, depends_on and hacf_touches."""
    front, kind = spec.front or {}, spec.layout
    intent = front.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        add("error", "intent_missing", "intent must be a non-empty string")
    if front.get("hacf_type") != HACF_TYPES[kind]:
        add(
            "error",
            "hacf_type_invalid",
            f"hacf_type must be {HACF_TYPES[kind]} (found {front.get('hacf_type')!r})",
        )
    if kind in HACF_STATUSES and front.get("hacf_status") not in HACF_STATUSES[kind]:
        add(
            "error",
            "hacf_status_invalid",
            f"hacf_status {front.get('hacf_status')!r} not in {'|'.join(HACF_STATUSES[kind])}",
        )
    if "stage" in front and front["stage"] not in STAGES:
        add("error", "stage_invalid", f"stage {front['stage']!r} not in {'|'.join(STAGES)}")
    if kind == PROJECT:
        return
    check = hyper_openrig.is_mission_dot_id if kind == MISSION else hyper_openrig.is_slice_dot_id
    if not check(front.get("id")):
        add("error", "dot_id_invalid", f"id {front.get('id')!r} is not a {kind} dot-ID")
    if kind == SLICE:
        try:
            depends_on, source = _dependencies(spec.path.parent, front)
        except Refused as exc:
            add("error", "depends_on_invalid", str(exc))
            depends_on, source = [], "slice.yaml"
    else:
        depends_on, source = front.get("depends_on"), "SPEC.md"
    if depends_on is not None and not isinstance(depends_on, list):
        add("error", "depends_on_invalid", f"{source} depends_on must be a list of dot-IDs")
    for dep in depends_on if isinstance(depends_on, list) else []:
        if not isinstance(dep, str) or not hyper_openrig.DOT_ID_RE.match(dep):
            add("error", "dot_id_invalid", f"{source} depends_on item {dep!r} is not a dot-ID")
        elif dep not in ids:
            add("error", "depends_on_dangling", f"{source} depends_on {dep} names no spec")
    touches = front.get("hacf_touches") if kind == SLICE else None
    if touches is not None and not isinstance(touches, list):
        add("error", "touch_unsafe", "hacf_touches must be a list of globs")
    for pattern in touches if isinstance(touches, list) else []:
        problem = hyper_ledger.glob_problem(pattern)
        if problem:
            add("error", "touch_unsafe", f"hacf_touches entry {pattern!r} {problem}")


def lint_spec(spec: Spec, ids: set[str], top: Path) -> list[Finding]:
    """Return the findings for one spec file (Tasks 13-15)."""
    found: list[Finding] = []

    def add(severity: str, code: str, message: str) -> None:
        found.append(Finding(severity, spec.rel, code, message))

    if spec.layout != LEGACY:
        if spec.front_error:
            add("error", "frontmatter_invalid", spec.front_error)
        elif spec.front is None:
            add("error", "frontmatter_missing", "a v6 SPEC.md needs a YAML frontmatter block")
        else:
            _lint_frontmatter(spec, ids, add)
    _lint_headings(spec, add)
    if spec.layout == SLICE:
        found += proof_findings(spec.path.parent, top)
    return found


def _mission_specs(mission_dir: Path, top: Path, mroot: Path) -> tuple[list[Spec], list[Finding]]:
    """Return the mission SPEC.md and its slices' SPEC.md files, plus spec_missing findings."""
    specs, missing = [], []
    for folder in [Path(mission_dir), *slice_dirs(mission_dir)]:
        if (folder / "SPEC.md").is_file():
            specs.append(load_spec(folder / "SPEC.md", top, mroot))
        else:
            missing.append(Finding("error", _rel(folder, top), "spec_missing", "no SPEC.md"))
    return specs, missing


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def _cr(text: str) -> str:
    """Return the CR each new line takes: a carriage return in a CRLF file, else nothing."""
    return "\r" if "\r\n" in text else ""


def _markers(region: str) -> tuple[str, str]:
    return f"<!-- BEGIN HACF GENERATED: {region} -->", f"<!-- END HACF GENERATED: {region} -->"


def set_region(text: str, region: str, content: list[str], heading: str | None = None) -> str:
    """Return text with the generated region's lines replaced by content.

    Markers count only as whole lines at column 0 outside fenced code; a duplicate, nested or
    unbalanced marker is a Conflict (exit 5). Bytes outside the region stay as they were. A file
    without the region gets it right after `heading` when that heading is present, else at the
    end. The file's line endings are kept.
    """
    begin, end = _markers(region)
    lines, cr = _lines(text), _cr(text)
    flags = _fenced(lines)
    live = [(i, line.rstrip("\r")) for i, (line, code) in enumerate(zip(lines, flags)) if not code]
    starts = [i for i, line in live if line == begin]
    ends = [i for i, line in live if line == end]
    new = [line + cr for line in (begin, *content, end)]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            raise Conflict(f"duplicate or unbalanced HACF GENERATED: {region} markers")
        inner = [line for i, line in live if starts[0] < i < ends[0]]
        if any(REGION_MARKER_RE.match(line) for line in inner):
            raise Conflict(f"another HACF GENERATED marker is nested in {region}")
        return "\n".join(lines[: starts[0]] + new + lines[ends[0] + 1 :])
    anchor = next((i for i, line in live if line.rstrip() == heading), None) if heading else None
    if anchor is not None:
        return "\n".join(lines[: anchor + 1] + [cr] + new + lines[anchor + 1 :])
    head = text if not text or text.endswith("\n") else text + cr + "\n"
    return head + cr + "\n" + "\n".join(new) + "\n"


def append_line(text: str, headings: tuple[str, ...], line: str) -> str:
    """Return text with line added to the first section titled one of headings.

    The line goes after the section's last non-blank line (rig's addProgressRow rule), so no
    existing line changes; a missing section is appended at the end with headings[0].
    """
    lines, cr = _lines(text), _cr(text)
    span = _section(text, headings)
    if span is None:
        head = text if not text or text.endswith("\n") else text + cr + "\n"
        return head + cr + "\n" + headings[0] + cr + "\n" + cr + "\n" + line + cr + "\n"
    at = span[0] + 1
    for index in range(span[0] + 1, span[1]):
        if lines[index].strip():
            at = index + 1
    added = [line + cr] if at > span[0] + 1 else [cr, line + cr]
    return "\n".join(lines[:at] + added + lines[at:])


def outcome_line(at: str, event: str, detail: str | None) -> str:
    """Return one Outcomes line: `- <ts> <event>` plus ` - <detail>` when there is a detail."""
    return f"- {at} {event}" + (f" - {detail}" if detail else "")


def last_event(progress: Path) -> dict | None:
    """Return the last `<ts> <event> - <detail>` line of a PROGRESS.md's Outcomes section."""
    text = _read_optional(progress)
    items = _section_items(text, OUTCOMES_HEADING) if text is not None else None
    for line in reversed(items or []):
        match = OUTCOME_RE.match(line.strip())
        if match and match.group(2) in EVENTS:
            with contextlib.suppress(ValueError):
                _runtime.parse_ts(match.group(1))
                return {"at": match.group(1), "event": match.group(2), "detail": match.group(3)}
    return None


def _cell(value: str) -> str:
    return value.replace("|", "\\|").strip()


def mission_table(mission_dir: Path, override: dict[str, tuple[str, str]]) -> list[str]:
    """Return the mission PROGRESS.md slice table (id | slice | hacf_status | depends_on | last).

    override maps a slice dir's real path to the (hacf_status, last event) being written now.
    """
    rows = ["| id | slice | hacf_status | depends_on | last event |", "|---|---|---|---|---|"]
    for folder in slice_dirs(mission_dir):
        front = _front_of(folder / "SPEC.md")
        try:
            depends_on = _id_list(_dependencies(folder, front)[0])
        except Refused:
            depends_on = _id_list(front.get("depends_on"))
        event = last_event(folder / "PROGRESS.md")
        last = f"{event['at']} {event['event']}" if event else ""
        status, last = override.get(os.path.realpath(folder), (front.get("hacf_status"), last))
        cells = (str(front.get("id") or ""), folder.name, str(status or ""), ", ".join(depends_on))
        rows.append("| " + " | ".join(_cell(c) for c in (*cells, last)) + " |")
    return rows


def _new_slice_progress(name: str, region: list[str], line: str) -> str:
    begin, end = _markers(CURRENT_STATE)
    parts = [f"# Progress - {name}", "", CURRENT_STATE_HEADING, "", begin, *region, end]
    return "\n".join([*parts, "", OUTCOMES_HEADING, "", line, ""])


def _new_mission_progress(name: str, table: list[str]) -> str:
    begin, end = _markers(SLICE_TABLE)
    parts = [f"# Progress - {name}", "", "## Acceptance", "", *MISSION_ACCEPTANCE, ""]
    return "\n".join([*parts, begin, *table, end, ""])


def plan_progress(slice_dir: Path, event: str, detail: str | None, at: str) -> list[Write]:
    """Check one progress event and return every write it makes (Tasks 17-19); writes nothing."""
    slice_dir = Path(slice_dir)
    spec_path = slice_dir / "SPEC.md"
    text = _read(spec_path)
    front = parse_frontmatter(text).data
    if front is None:
        raise Refused(f"{spec_path} has no frontmatter")
    current = front.get("hacf_status")
    allowed = TRANSITIONS.get(current, ()) if current is None or isinstance(current, str) else ()
    if event not in allowed:
        raise Refused(
            f"{spec_path}: hacf_status {current!r} -> {event} is not an allowed transition "
            f"(allowed: {', '.join(allowed) or 'none'}); nothing was written"
        )
    if event == "audited" and not (
        (slice_dir / "PROOF.md").is_file() and hyper_openrig.has_c1_artifact(slice_dir)
    ):
        raise Refused(f"{slice_dir}: audited (status done) needs PROOF.md and a C1 proof artifact")
    status = hyper_openrig.openrig_status(event)
    writes = [
        Write(spec_path, text, splice_owned(text, SLICE, {"hacf_status": event, "status": status}))
    ]

    line = outcome_line(at, event, detail)
    progress = slice_dir / "PROGRESS.md"
    old = _read_optional(progress)
    region = [f"- hacf_status: {event}", f"- status: {status}", f"- last event: {at} {event}"]
    if old is None:
        new = _new_slice_progress(slice_dir.name, region, line)
    else:
        new = append_line(
            set_region(old, CURRENT_STATE, region, CURRENT_STATE_HEADING), (OUTCOMES_HEADING,), line
        )
    writes.append(Write(progress, old, new))

    mission_dir = slice_dir.parent.parent
    table = mission_table(mission_dir, {os.path.realpath(slice_dir): (event, f"{at} {event}")})
    mission_progress = mission_dir / "PROGRESS.md"
    old = _read_optional(mission_progress)
    if old is None:
        new = _new_mission_progress(mission_dir.name, table)
    else:
        new = set_region(old, SLICE_TABLE, table)
    writes.append(Write(mission_progress, old, new))

    manifest = mission_dir / "mission.yaml"
    if event == "executing" and manifest.is_file() and _mission_status(mission_dir) == "planned":
        manifest_text = _read(manifest)
        try:
            spliced = hyper_openrig.splice_mission_status(manifest_text, "active")
        except hyper_openrig.OpenRigError as exc:
            raise Refused(f"{manifest}: {exc}") from None
        writes.append(Write(manifest, manifest_text, spliced))
    return writes


def _read_detail(path: str | None) -> str | None:
    """Return the --detail-file text on one line (whitespace runs collapsed), or None."""
    if path is None:
        return None
    try:
        text = _read(Path(path))
    except FileNotFoundError:
        raise UsageError(f"--detail-file {path}: no such file") from None
    return " ".join(text.split()) or None


# ---------------------------------------------------------------------------
# proof
# ---------------------------------------------------------------------------


def slice_files_clean(top: Path, touches: Any) -> bool:
    """Return True when the slice's hacf_touches (the whole tree without them) are unchanged.

    `--no-optional-locks` keeps git status from refreshing the user's index file.
    """
    specs = [
        ":(glob)" + (t + "**" if t.endswith("/") else t)
        for t in (touches if isinstance(touches, list) else [])
        if isinstance(t, str) and t
    ]
    args = ["--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all"]
    return _git_out([*args, "--", *specs], top) == ""


def candidate_auto(top: Path, touches: Any, slice_id: str) -> str:
    """Return HEAD when the slice's files are clean, else a snapshot commit of the work tree.

    The snapshot never touches the user's index or work tree (R22.2): a temp index is seeded
    with `git read-tree HEAD`, so force-tracked files under ignored directories stay in the
    tree, then `git add -A` and `git write-tree` fill it, `git commit-tree -p HEAD` commits it
    and `git update-ref refs/hacf/candidates/<slice-id>` keeps it.
    """
    head = _git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], top).stdout.strip()
    if not head:
        raise Refused("--candidate-sha auto needs a HEAD commit")
    if slice_files_clean(top, touches):
        return head
    with tempfile.TemporaryDirectory(prefix="hacf-candidate-") as folder:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(folder) / "index")}
        _git_out(["read-tree", "HEAD"], top, env)
        _git_out(["add", "-A"], top, env)
        tree = _git_out(["write-tree"], top, env).strip()
    message = f"hacf candidate {slice_id}"
    commit = _git_out(["commit-tree", tree, "-p", head, "-m", message], top).strip()
    _git_out(["update-ref", CANDIDATE_REF + slice_id, commit], top)
    return commit


def _check_line(value: str, what: str) -> str:
    """Refuse text that is empty, starts with '-' (rig would read an option) or spans lines."""
    if (
        not value.strip()
        or value.startswith("-")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise UsageError(f"{what} must be one line of text that does not start with '-'")
    return value


def proof_md_text(old: str | None, slice_id: str, title: str, entry: str) -> str:
    """Return PROOF.md with entry added under `## Artifacts` (a new PROOF.md when old is None)."""
    if old is None:
        parts = [f"# PROOF - {slice_id} {title}", "", "## What this proves", ""]
        return "\n".join([*parts, "## Artifacts", "", entry, "", "## Residue / caveats", ""])
    return append_line(old, ARTIFACTS_HEADINGS, entry)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _context() -> tuple[Path, Path]:
    top = repo_top()
    return top, missions_root(top)


def _print_record(record: dict[str, Any]) -> None:
    for key, value in record.items():
        shown = value if isinstance(value, str) else json.dumps(value)
        print(f"{key}: {shown}")


def cmd_resolve(ns: argparse.Namespace) -> int:
    """Print the resolve record of a path, a dot-ID or a migrated legacy path."""
    top, mroot = _context()
    record = describe(load_spec(locate(ns.target, top, mroot), top, mroot), top)
    if ns.json:
        print(json.dumps(record, indent=2))
    else:
        _print_record(record)
    return EXIT_OK


def cmd_lint(ns: argparse.Namespace) -> int:
    """Lint one spec, one mission or the whole tree; exit 1 when there is an error."""
    chosen = [bool(ns.path), bool(ns.mission), ns.all]
    if chosen.count(True) != 1:
        raise UsageError("lint takes exactly one of <path>, --mission <dir> or --all")
    top, mroot = _context()
    findings: list[Finding] = []
    specs: list[Spec] = []
    if ns.all:
        if (top / "SPEC.md").is_file():
            specs.append(load_spec(top / "SPEC.md", top, mroot))
        for mission in mission_dirs(mroot):
            found, missing = _mission_specs(mission, top, mroot)
            specs += found
            findings += missing
        compiled = top / "spec" / "compiled"
        if compiled.is_dir():
            legacy = sorted(p for p in compiled.glob("MiniPRD_*.md") if p.is_file())
            specs += [load_spec(path, top, mroot) for path in legacy]
    elif ns.mission:
        if not Path(ns.mission).is_dir():
            raise UsageError(f"--mission {ns.mission}: no such directory")
        specs, findings = _mission_specs(Path(ns.mission), top, mroot)
    else:
        specs = [load_spec(Path(ns.path), top, mroot)]
    ids = known_ids(mroot)
    for spec in specs:
        findings += lint_spec(spec, ids, top)
    failed = any(finding.severity == "error" for finding in findings)
    if ns.json:
        report = {"ok": not failed, "findings": [finding._asdict() for finding in findings]}
        print(json.dumps(report, indent=2))
    else:
        for finding in findings:
            print(finding.line())
        print("lint: failed" if failed else "lint: ok")
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_progress(ns: argparse.Namespace) -> int:
    """Record one slice event in SPEC.md, PROGRESS.md and the mission files (or the outbox)."""
    top, mroot = _context()
    spec = load_spec(Path(ns.slice), top, mroot)
    if spec.layout == LEGACY:
        print(LEGACY_PROGRESS_NOTICE)
        return EXIT_OK
    if spec.layout != SLICE:
        raise UsageError(f"--slice {ns.slice}: {spec.rel} is a {spec.layout} SPEC.md, not a slice")
    detail = _read_detail(ns.detail_file)
    slice_dir = spec.path.parent
    outbox = os.environ.get(hyper_ledger.OUTBOX_ENV)
    if outbox:
        args = {"slice": _rel(slice_dir, top), "event": ns.event, "detail": detail}
        entry = write_outbox_entry(Path(outbox), "progress", args)
        print(
            f"progress: {ns.event} for {_rel(slice_dir, top)} recorded in the outbox ({entry.name})"
        )
        return EXIT_OK
    with spec_lock(top):
        writes = plan_progress(slice_dir, ns.event, detail, _runtime.utc_now_iso())
        written = apply_writes(writes)
    for path in written:
        print(f"progress: wrote {_rel(path, top)}")
    print(f"progress: {_rel(slice_dir, top)} is now {ns.event}")
    return EXIT_OK


def cmd_proof(ns: argparse.Namespace) -> int:
    """Write one C1 proof artifact and list it in PROOF.md."""
    if ns.candidate_sha == "auto" and os.environ.get(hyper_ledger.OUTBOX_ENV):
        raise Refused(
            "--candidate-sha auto is refused under HACF_OUTBOX: the engine writes PROOF and "
            "candidate refs (R1)"
        )
    top, mroot = _context()
    spec = load_spec(Path(ns.slice), top, mroot)
    if spec.layout == LEGACY:
        print(LEGACY_PROOF_NOTICE)
        return EXIT_OK
    if spec.layout != SLICE:
        raise UsageError(f"--slice {ns.slice}: {spec.rel} is a {spec.layout} SPEC.md, not a slice")
    if not PROOF_NAME_RE.match(ns.name):
        raise UsageError(f"--name {ns.name!r} must look like <NN>-<type>.md (e.g. 01-qa.md)")
    sha_ok = hyper_openrig.SHA_RE.match
    if ns.candidate_sha not in ("auto", LEGACY_UNRECORDED) and not sha_ok(ns.candidate_sha):
        raise UsageError(
            f"--candidate-sha {ns.candidate_sha!r} is not a commit sha, auto or {LEGACY_UNRECORDED}"
        )
    if ns.swimlane_sha is not None and not sha_ok(ns.swimlane_sha):
        raise UsageError(f"--swimlane-sha {ns.swimlane_sha!r} is not a commit sha")
    _check_line(ns.money_evidence, "--money-evidence")
    body = Path(ns.file)
    if not body.is_file():
        raise UsageError(f"--file {ns.file}: no such file")
    slice_dir, front = spec.path.parent, spec.front or {}
    slice_id = front.get("id")
    if not hyper_openrig.is_slice_dot_id(slice_id):
        raise Refused(f"{spec.rel} has no slice dot-ID in its frontmatter")
    target = slice_dir / "proof" / ns.name
    if os.path.lexists(target):
        raise Refused(f"{_rel(target, top)} already exists; pick another --name")
    with spec_lock(top):
        proof_md = slice_dir / "PROOF.md"
        old = _read_optional(proof_md)
        candidate = ns.candidate_sha
        if candidate == "auto":
            candidate = candidate_auto(top, front.get("hacf_touches"), slice_id)
        entry = f"- proof/{ns.name} - {ns.artifact_type} {ns.verdict}: {ns.money_evidence}"
        new_proof = proof_md_text(old, slice_id, _title(spec.body) or slice_dir.name, entry)
        try:
            artifact = hyper_openrig.write_proof_artifact(
                slice_dir,
                workspace=top,
                plan=hyper_openrig.plan_rig(),
                artifact_type=ns.artifact_type,
                verdict=ns.verdict,
                candidate_sha=candidate,
                money_evidence=ns.money_evidence,
                body_file=body.resolve(),
                name=ns.name,
            )
        except hyper_openrig.UsageError as exc:
            raise UsageError(str(exc)) from None
        except hyper_openrig.OpenRigError as exc:
            raise Refused(str(exc)) from None
        if not artifact.is_file():
            raise Refused(f"rig proof add wrote no {_rel(artifact, top)}")
        try:
            if ns.swimlane_sha:
                text = _read(artifact)
                spliced = hyper_openrig.splice_frontmatter(text, {"swimlane_sha": ns.swimlane_sha})
                cas_write(artifact, text, spliced)
            cas_write(proof_md, old, new_proof)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(artifact)  # the artifact this call wrote; PROOF.md never listed it
            raise
    print(f"proof: wrote {_rel(artifact, top)} (candidate_sha {candidate})")
    return EXIT_OK


def slice_status(folder: Path, top: Path) -> dict[str, Any]:
    """Return one slice's status record for `status`."""
    front = _front_of(folder / "SPEC.md")
    hacf_status = front.get("hacf_status")
    try:
        depends_on = _id_list(_dependencies(folder, front)[0])
    except Refused:
        depends_on = _id_list(front.get("depends_on"))
    record = {
        "id": front.get("id"),
        "slice": folder.name,
        "path": _rel(folder, top),
        "hacf_status": hacf_status,
        "status": hyper_openrig.STATUS_MAP.get(hacf_status)
        if isinstance(hacf_status, str)
        else None,
        "depends_on": depends_on,
        "last_event": last_event(folder / "PROGRESS.md"),
        "warnings": [],
        "errors": [],
    }
    for finding in proof_findings(folder, top):
        key = "errors" if finding.severity == "error" else "warnings"
        record[key].append(f"{finding.path}: {finding.code}: {finding.message}")
    return record


def cmd_status(ns: argparse.Namespace) -> int:
    """Print every mission with its slices, statuses, dependencies and last events."""
    top, mroot = _context()
    missions = []
    for mission in mission_dirs(mroot):
        front = _front_of(mission / "SPEC.md")
        missions.append(
            {
                "id": front.get("id"),
                "mission": mission.name,
                "path": _rel(mission, top),
                "hacf_status": front.get("hacf_status"),
                "status": _mission_status(mission),
                "slices": [slice_status(folder, top) for folder in slice_dirs(mission)],
            }
        )
    if ns.json:
        print(json.dumps({"missions": missions}, indent=2))
        return EXIT_OK
    for mission in missions:
        print(
            f"{mission['id']} {mission['mission']} ({mission['hacf_status']}, {mission['status']})"
        )
        for item in mission["slices"]:
            last = item["last_event"]
            shown = f"{last['at']} {last['event']}" if last else "-"
            deps = ", ".join(item["depends_on"]) or "-"
            state = f"{item['hacf_status']} ({item['status']})"
            print(f"  {item['id']} {item['slice']} {state} depends_on: {deps} last: {shown}")
            for message in item["errors"] + item["warnings"]:
                print(f"    {message}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_spec.py",
        description="Read, lint and record progress and proof for HACF spec files.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name: str, handler: Callable[[argparse.Namespace], int], text: str) -> Any:
        p = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        p.set_defaults(handler=handler)
        return p

    p = command("resolve", cmd_resolve, "describe a spec named by path, dot-ID or legacy path")
    p.add_argument("target", help="a spec path, a dot-ID or a migrated legacy flat path")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = command("lint", cmd_lint, "check spec files against the v6 schema")
    p.add_argument("path", nargs="?", help="one spec file or slice directory")
    p.add_argument("--mission", help="a mission directory: its SPEC.md and every slice")
    p.add_argument("--all", action="store_true", help="root SPEC.md, every mission, legacy files")
    p.add_argument("--json", action="store_true", help="print the findings as JSON")
    p = command("progress", cmd_progress, "record one slice event")
    p.add_argument("--slice", required=True, help="the slice directory (or a legacy flat file)")
    p.add_argument("--event", required=True, choices=EVENTS)
    p.add_argument("--detail-file", help="a file whose text becomes the Outcomes detail")
    p = command("proof", cmd_proof, "write one C1 proof artifact")
    p.add_argument("--slice", required=True, help="the slice directory (or a legacy flat file)")
    p.add_argument("--artifact-type", required=True, choices=hyper_openrig.C1_ARTIFACT_TYPES)
    p.add_argument("--verdict", required=True, choices=hyper_openrig.C1_VERDICTS)
    p.add_argument("--candidate-sha", required=True, help="a commit sha, auto or legacy-unrecorded")
    p.add_argument("--money-evidence", required=True, help="one line of evidence")
    p.add_argument("--file", required=True, help="the artifact body")
    p.add_argument("--name", required=True, help="the artifact file name, <NN>-<type>.md")
    p.add_argument("--swimlane-sha", help="the build-branch commit the proof was made against")
    p = command("status", cmd_status, "list missions and slices with their statuses")
    p.add_argument("--json", action="store_true", help="print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code (0 ok, 1 findings, 2-5 failures)."""
    _runtime.require_runtime()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return ns.handler(ns)
    except SpecError as exc:
        print(f"hyper_spec.py: {exc}", file=sys.stderr)
        return exc.code
    except hyper_openrig.OpenRigError as exc:  # its 2 usage, 4 parse and 5 refused match ours
        print(f"hyper_spec.py: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
