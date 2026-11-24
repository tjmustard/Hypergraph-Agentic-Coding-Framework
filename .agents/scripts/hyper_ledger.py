#!/usr/bin/env python3
"""hyper_ledger.py — the single validated writer of the HACF component ledger.

The ledger is `spec/compiled/architecture.yml` under the repository top level, which every
subcommand finds with `git rev-parse --show-toplevel` from the working directory; only `init`
takes a path. Every write takes the ledger lock, loads the file, applies the change in memory,
refuses it when the result has an error the ledger did not already have, and replaces the file
atomically. Normative design: the architecture.yml section of HACF's v0.6.0 design notes
(not shipped).

CLI contract (frozen for v0.6.x)
--------------------------------
Later slices copy these subcommand names and flag spellings verbatim into their pinned
`ensure_node` commands. Changing a subcommand name or a flag spelling needs a replan R-row
(R18.12; the replan procedure in HACF's v0.6.0 design notes (not shipped)).

    init [path]
    show
    validate [--strict] [--json] [--against-ref REF | --against-file F]
    impact (--nodes a,b | --files p1,p2 | --from-diff <rev-range>)
    ensure-node --id --dimension [--parent] [--member-of] [--depends-on a,b] [--files a,b]
                [--associated-file] [--description-file] [--spec-refs a,b] [--from-spec <file>]
                [--allow-shared]
    set-edges --id [--add-depends-on] [--remove-depends-on] [--set-member-of] [--set-implements]
    review-template [--status needs_review,dirty] --json
    apply-review --patch <json> --slice <id>
    mark --dirty|--needs-review|--clean --ids a,b
    remove-node <id> --reason R [--slice SID]
    apply-outbox <dir> [--base-file <ledger>]
    migrate-v2 [--dry-run]     (added by S01b)
    repair-text [--dry-run]    (added by S01b)

Exit codes: 0 ok, 1 findings, 2 usage, 3 lock timeout, 4 parse error, 5 refused.

Semantics:
- Every list flag takes one comma-separated list (`--files a,b`, `--depends-on a,b`,
  `--set-member-of a,b`); an empty value is an empty list.
- ensure-node creates a missing node (`--dimension` is then required, and the new node starts
  `dirty`) and otherwise changes only the fields it is given, so a repeat is a no-op.
  `--id` may come from `--from-spec` instead: a legacy MiniPRD's `**Hypergraph Node ID:**` and
  `**Parent Node:**` lines (`none` means no parent), or a v6 SPEC.md's frontmatter `node_id`,
  `parent` and `hacf_touches`, which are merged into `files`: each exact path the node lacks
  is appended, a glob touch (`*`, `?` or `[`) is skipped and named on stderr, and no file is
  removed; `--files` with `--from-spec` exits 2. `--files` replaces the node's `files`;
  `--depends-on` and `--member-of` add the ids the edge list lacks; `--spec-refs` adds the
  dot-IDs `spec_refs` lacks, keeping order, and is the only writer of `spec_refs` (R28).
  `--parent X` writes `member_of: [X]` for a Layer or System X, and `implements: [X]` only for
  a Module X and an Atomic node; any other combination exits 2 (R21.2). A tombstoned id is
  refused.
- ensure-node never gives a path a second owner by accident (OPR.99.0.6.4). A path the node is
  about to gain (one its current `files` lacks; a new node's every path) is checked against
  every other node's `files`: an equal entry, a glob entry that matches it (`glob_match`), or,
  for a glob path, a literal entry it matches. A `--from-spec` touch owned that way is skipped
  with `ensure-node: skipped <path>: owned by <id>[, <id>]` on stderr and the rest are merged.
  A `--files` entry owned that way refuses the whole run (exit 2, nothing written) unless
  `--allow-shared` is given, which keeps it and prints `ensure-node: shared <path> with <id>`;
  `--allow-shared` has no effect without `--files`. Paths the node already has are never
  re-checked, so existing shared paths stay valid, and `validate` has no ownership rule. Limit:
  two different glob strings that overlap are not detected (that needs the file list).
- apply-review takes a JSON file `{<node id>: {description?, inputs?, outputs?, files?,
  edges?}}`; each given key replaces the node's value (for `edges`, each given edge list), and
  every patched node becomes `clean` with a `review` record (R27.4).
- With HACF_OUTBOX set, a write subcommand records one JSON entry in that directory
  (`<6-digit sequence>-<subcommand>.json` holding `{"tool": "hyper_ledger", "op", "args"}`)
  instead of writing the ledger, and takes no shared lock (R22.1); only the argument checks run
  then, and apply-outbox runs the ledger checks when it replays the entry.
- Blast radius (impact) keeps the v0.5.9 BFS: `implements` parents plus reverse `depends_on`;
  `member_of` is not traversed. The radius includes the start nodes and is printed sorted.
- migrate-v2 converts a v1 or mixed v1/v2 ledger: it adds `schema: hacf.ledger/v2`, moves each
  `depends_on` edge to a Layer node into `member_of`, turns each endpoint naming no node into
  `{external: <label>}` (a camelCase label becomes snake_case; a label that still does not
  match EXTERNAL_RE stops it, exit 5), renames `_provenance` to `provenance`, seeds a Module's
  or Atomic node's `files` from an `associated_file` that is a regular file, and keeps
  `removed_nodes`. It prints a summary and a unified diff; a second run prints `no change` and
  leaves the file byte-identical.
- repair-text works on the raw text: each plain `description:` scalar holding ` #` or `: `
  becomes `>-` with its text on the next line, and no other line changes. A result that still
  does not parse exits 4 with nothing written.
- `--dry-run` makes both print and write nothing. Neither records an outbox entry.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import difflib
import fcntl
import fnmatch
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple

import _runtime

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_LOCK_TIMEOUT = 3
EXIT_PARSE = 4
EXIT_REFUSED = 5

LEDGER_REL = "spec/compiled/architecture.yml"
SCHEMA_V2 = "hacf.ledger/v2"
LOCK_TIMEOUT = 30.0  # seconds a write waits for the ledger lock before exit 3
LOCK_POLL = 0.05
OUTBOX_ENV = "HACF_OUTBOX"
OUTBOX_TOOL = "hyper_ledger"
OUTBOX_ENTRY_RE = re.compile(r"^(\d+)-([A-Za-z0-9][A-Za-z0-9_.-]*)\.json$")

DIMENSION_RANK = {"Atomic": 1, "Module": 2, "Layer": 3, "System": 4}
DIMENSIONS = ("System", "Layer", "Module", "Atomic")
STATUSES = ("clean", "dirty", "needs_review")
EDGE_KINDS = ("depends_on", "implements", "member_of")
REQUIRED_EDGE_KINDS = ("depends_on", "implements")
ENDPOINT_KEYS = {"inputs": "source_id", "outputs": "target_id"}
PATCH_KEYS = ("description", "inputs", "outputs", "files", "edges")
V2_NODE_KEYS = ("files", "provenance", "review", "spec_refs")
WRITE_OPS = ("init", "ensure-node", "set-edges", "apply-review", "mark", "remove-node")

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")  # activeContext -> active_Context
DOT_ID_RE = re.compile(r"^([A-Z]{2,3})\.(\d+(?:\.\d+){1,4})$")  # schemas.md#spec-frontmatter
DESCRIPTION_MAX = 2000
GLOB_CHARS = frozenset("*?[]")
# A slice touch holding one of these is a glob, which ensure-node --from-spec never stores (R11).
TOUCH_GLOB_CHARS = frozenset("*?[")
FILES_AND_FROM_SPEC = "--files replaces files and --from-spec merges them; pass one"
LEGACY_ID_RE = re.compile(r"^\s*\*\*Hypergraph Node ID:\*\*[ \t]*(.*?)\s*$", re.MULTILINE)
LEGACY_PARENT_RE = re.compile(r"^\s*\*\*Parent Node:\*\*[ \t]*(.*?)\s*$", re.MULTILINE)
# repair-text: a `description:` key line, a block-scalar header line, and the characters that
# end a plain scalar early (` #` starts a comment; `:` before a blank or line end is a key).
DESCRIPTION_LINE_RE = re.compile(r"^(?P<lead> *(?:- +)?)description:[ \t]+(?P<value>.*?)[ \t]*$")
BLOCK_HEADER_RE = re.compile(
    r"^(?P<lead> *(?:- +)?)[^\s#'\"][^:]*:[ \t]+[|>][0-9+-]*[ \t]*(?:#.*)?$"
)
PLAIN_BREAKER_RE = re.compile(r"[ \t]#|:(?:[ \t]|$)")
PLAIN_FIRST_FORBIDDEN = frozenset("'\"[]{}>|&*!%@`#,")
SEEDED_DIMENSIONS = ("Module", "Atomic")  # never Layer or System nodes


# ---------------------------------------------------------------------------
# Errors and findings
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """A failure that ends a command; `code` is its exit code."""

    code = EXIT_USAGE

    def __init__(self, message: str, details: list[str] | tuple[str, ...] = ()) -> None:
        """Keep the one-line message and the detail lines printed under it."""
        super().__init__(message)
        self.details = list(details)


class UsageError(LedgerError):
    """Bad arguments, an unknown node or a missing ledger (exit 2)."""

    code = EXIT_USAGE


class LockTimeout(LedgerError):
    """The ledger lock was not free within the bounded wait (exit 3)."""

    code = EXIT_LOCK_TIMEOUT


class ParseError(LedgerError):
    """The ledger does not load as YAML or its root is unusable (exit 4)."""

    code = EXIT_PARSE


class Refused(LedgerError):
    """A write that would add an error, reuse a tombstone or overwrite a ledger (exit 5)."""

    code = EXIT_REFUSED


class Finding(NamedTuple):
    """One validation result; (rule, node, detail) is its stable identity."""

    severity: str  # "error" or "warning"
    rule: str
    node: str  # the node id, "nodes[<i>]" for a node without a usable id, "" for the ledger
    detail: str
    message: str

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return (rule, node, detail), which a write compares before and after."""
        return (self.rule, self.node, self.detail)

    def line(self, severity: str | None = None) -> str:
        """Return the one-line report form, optionally under another severity label."""
        where = f"node {self.node}: " if self.node else ""
        return f"{severity or self.severity}: {where}{self.message} [{self.rule}]"

    def as_json(self) -> dict[str, str]:
        """Return the finding as a JSON object."""
        return {
            "rule": self.rule,
            "node": self.node,
            "detail": self.detail,
            "message": self.message,
        }


class WriteResult(NamedTuple):
    """What a locked write did: whether the file changed, and what to report."""

    changed: bool
    message: str
    carried: list[Finding]  # pre-existing errors, reported as warnings
    warnings: int


# ---------------------------------------------------------------------------
# Repository, paths and git
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run git with an argv list in cwd and capture its output (never raises on exit codes)."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)


def _existing_dir(path: Path) -> Path:
    """Return path or its nearest ancestor that is an existing directory."""
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return Path(path.anchor or "/")


def repo_toplevel(start: Path | None = None) -> Path | None:
    """Return the work-tree top level containing start (default: cwd), or None outside git."""
    try:
        proc = _git(["rev-parse", "--show-toplevel"], _existing_dir(start or Path.cwd()))
    except OSError:  # git is not installed
        return None
    top = proc.stdout.strip()
    return Path(top) if proc.returncode == 0 and top else None


def _repo(start: Path | None = None) -> Path:
    top = repo_toplevel(start)
    if top is None:
        raise UsageError(
            "not inside a git work tree; run hyper_ledger.py from the repository "
            "(only init takes a ledger path)"
        )
    return top


def default_ledger_path(start: Path | None = None) -> Path:
    """Return `<top level>/spec/compiled/architecture.yml` for the repository at start (cwd)."""
    return _repo(start) / LEDGER_REL


def _display(path: Path) -> str:
    """Return path relative to the working directory when it lies under it."""
    try:
        return str(Path(os.path.abspath(path)).relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _head_commit(path: Path) -> str | None:
    """Return the HEAD commit of the repository holding path, or None."""
    try:
        proc = _git(["rev-parse", "--verify", "-q", "HEAD"], _existing_dir(path.parent))
    except OSError:
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def _lock_location(ledger: Path) -> tuple[Path, Path | None]:
    """Return (lock file, work-tree top level or None) for the ledger at path.

    Inside a git work tree the lock is `<git common dir>/hacf/locks/ledger.lock`, which every
    linked worktree shares (R22.1). Outside one it is `.<ledger name>.lock` beside the ledger.
    """
    ledger = Path(os.path.abspath(ledger))
    top = repo_toplevel(ledger.parent)
    if top is not None:
        # Asked from the top level, a relative answer is relative to it on every git version.
        proc = _git(["rev-parse", "--git-common-dir"], top)
        common = proc.stdout.strip()
        if proc.returncode == 0 and common:
            common_dir = Path(common)
            if not common_dir.is_absolute():
                common_dir = top / common_dir
            return common_dir.resolve() / "hacf" / "locks" / "ledger.lock", top
    return ledger.parent / f".{ledger.name}.lock", None


def lock_file_for(ledger: Path) -> Path:
    """Return the lock file that guards writes to the ledger at path."""
    return _lock_location(ledger)[0]


@contextlib.contextmanager
def ledger_lock(ledger: Path, timeout: float | None = None) -> Iterator[Path]:
    """Hold the exclusive ledger lock; raise LockTimeout after `timeout` (LOCK_TIMEOUT) seconds.

    Callers take it before they open the ledger. The lock directory (and, in a work tree,
    `.agents/state/`) is created on first use.
    """
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    lock, top = _lock_location(ledger)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if top is not None:
        (top / ".agents" / "state").mkdir(parents=True, exist_ok=True)
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
                        f"timed out after {timeout:g}s waiting for the ledger lock {lock}"
                    ) from None
                time.sleep(LOCK_POLL)
        yield lock
    finally:
        os.close(fd)  # closing the descriptor releases the lock


# ---------------------------------------------------------------------------
# Load and dump
# ---------------------------------------------------------------------------


def _kind(value: Any) -> str:
    """Return a short name for value's YAML type, for messages."""
    if value is None:
        return "null"
    return {dict: "a mapping", list: "a list", str: "a string", bool: "a boolean"}.get(
        type(value), type(value).__name__
    )


def parse_ledger(text: str, label: str) -> dict:
    """Parse ledger text with yaml.safe_load; raise ParseError naming the line and column."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        where = f"{label}:{mark.line + 1}:{mark.column + 1}" if mark is not None else label
        raise ParseError(f"{where}: YAML parse error: {problem}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ParseError(f"{label}: the ledger root must be a mapping, not {_kind(data)}")
    if "nodes" in data and not isinstance(data["nodes"], list):
        raise ParseError(f"{label}: nodes must be a list, not {_kind(data['nodes'])}")
    return data


def read_ledger_text(path: Path) -> str:
    """Return the ledger file's text exactly as stored (line endings untranslated)."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise UsageError(
            f"no ledger at {_display(path)}; create one with "
            "`python3 .agents/scripts/hyper_ledger.py init` or run /hyper-discover"
        ) from None
    except UnicodeDecodeError as exc:
        raise ParseError(f"{_display(path)}: not UTF-8 text ({exc.reason})") from None


def load_ledger(path: Path) -> tuple[str, dict]:
    """Read and parse the ledger at path; return (text, data)."""
    text = read_ledger_text(path)
    return text, parse_ledger(text, _display(path))


class _Folded(str):
    """A description string, emitted as a folded (`>-`) block scalar."""


_DUMPER: Any = None


def _dumper() -> Any:
    """Return the SafeDumper subclass the ledger is written with (built on first use)."""
    global _DUMPER
    if _DUMPER is None:

        class LedgerDumper(yaml.SafeDumper):
            """SafeDumper that folds descriptions and never writes anchors or aliases."""

            def ignore_aliases(self, data: Any) -> bool:
                """Write every shared object in full, never as an alias."""
                return True

        def represent_folded(dumper: Any, data: _Folded) -> Any:
            return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style=">")

        LedgerDumper.add_representer(_Folded, represent_folded)
        _DUMPER = LedgerDumper
    return _DUMPER


def dump_ledger(data: dict) -> str:
    """Return the ledger as YAML: key order kept, each non-empty description folded (`>-`).

    PyYAML falls back to a quoted scalar for the rare text a block scalar cannot carry (for
    example trailing spaces); the text still round-trips.
    """
    out = dict(data)
    if isinstance(data.get("nodes"), list):
        nodes = []
        for node in data["nodes"]:
            if isinstance(node, dict) and isinstance(node.get("description"), str):
                if node["description"]:
                    node = {**node, "description": _Folded(node["description"])}
            nodes.append(node)
        out["nodes"] = nodes
    return yaml.dump(
        out, Dumper=_dumper(), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def _fsync_dir(directory: Path) -> None:
    """Flush a directory entry change (the os.replace) to disk."""
    fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    """Replace path with text: temp file in the same directory, fsync, os.replace, fsync dir.

    An existing file keeps its mode; a new one gets 0666 minus the umask.
    """
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
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------


def is_v2(data: dict) -> bool:
    """Return True for a `schema: hacf.ledger/v2` ledger (a file without `schema` is v1)."""
    return data.get("schema") == SCHEMA_V2


def node_list(data: dict) -> list:
    """Return the ledger's nodes list (empty when absent)."""
    nodes = data.get("nodes")
    return nodes if isinstance(nodes, list) else []


def node_index(data: dict) -> dict[str, dict]:
    """Map each node id to its node (the first one when an id repeats)."""
    index: dict[str, dict] = {}
    for node in node_list(data):
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            index.setdefault(node["id"], node)
    return index


def removed_nodes(data: dict) -> list[dict]:
    """Return the top-level `removed_nodes` tombstones (empty when absent)."""
    removed = data.get("removed_nodes")
    if not isinstance(removed, list):
        return []
    return [item for item in removed if isinstance(item, dict)]


def removed_ids(data: dict) -> set[str]:
    """Return the ids the ledger's tombstones declare removed."""
    return {item["id"] for item in removed_nodes(data) if isinstance(item.get("id"), str)}


def edge_list(node: dict, kind: str) -> list[str]:
    """Return node's edge targets of one kind (only the string items of a list value)."""
    edges = node.get("edges")
    value = edges.get(kind) if isinstance(edges, dict) else None
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def endpoint(item: Any, id_key: str) -> tuple[str, Any] | None:
    """Classify one inputs/outputs entry as ("node", id) or ("external", label).

    Returns None when the entry is not a mapping holding exactly one of id_key or external.
    """
    if not isinstance(item, dict) or (id_key in item) == ("external" in item):
        return None
    return ("node", item[id_key]) if id_key in item else ("external", item["external"])


def needs_review_ids(data: dict) -> list[str]:
    """Return the sorted ids of the nodes whose status is needs_review."""
    return sorted(
        {
            node["id"]
            for node in node_list(data)
            if isinstance(node, dict)
            and isinstance(node.get("id"), str)
            and node.get("status") == "needs_review"
        }
    )


# ---------------------------------------------------------------------------
# Globs and blast radius
# ---------------------------------------------------------------------------


def glob_problem(pattern: Any) -> str | None:
    """Return why a `files` entry is not a safe repo-relative glob, or None when it is."""
    if not isinstance(pattern, str) or not pattern.strip():
        return "is not a non-empty string"
    if pattern.startswith("/"):
        return "is absolute (leading /)"
    if "\\" in pattern:
        return "contains a backslash"
    segments = pattern.rstrip("/").split("/")
    if ".." in segments:
        return "has a .. segment"
    if any(segment in ("", ".") for segment in segments):
        return "has an empty or . segment"
    if all(set(segment) <= GLOB_CHARS for segment in segments):
        return "is a catch-all"
    if GLOB_CHARS & set(segments[0]):
        return "does not start with a literal segment"
    return None


def _match_parts(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_parts(rest, parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _match_parts(rest, parts[1:])


def glob_match(pattern: str, path: str) -> bool:
    """Match a repo-relative path against a glob; `**` spans segments, a trailing / a tree."""
    if pattern.endswith("/"):
        pattern += "**"
    return _match_parts(pattern.split("/"), path.split("/"))


def _normalize_path(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def map_paths(data: dict, paths: list[str]) -> tuple[list[str], list[str]]:
    """Return (sorted ids of the nodes covering paths, sorted paths no node covers).

    A node with a `files` list covers the paths its safe globs match; a node without `files`
    (a v1 node) covers the path equal to its `associated_file`.
    """
    index = node_index(data)
    matched: set[str] = set()
    unmapped: set[str] = set()
    for path in paths:
        norm = _normalize_path(path)
        hit = False
        for node_id, node in index.items():
            files = node.get("files")
            if isinstance(files, list):
                covers = any(
                    glob_problem(glob) is None and glob_match(glob, norm) for glob in files
                )
            else:
                associated = node.get("associated_file")
                covers = isinstance(associated, str) and _normalize_path(associated) == norm
            if covers:
                matched.add(node_id)
                hit = True
        if not hit:
            unmapped.add(path)
    return sorted(matched), sorted(unmapped)


def _is_glob(path: str) -> bool:
    return bool(TOUCH_GLOB_CHARS & set(path)) or path.endswith("/")


def owners_of(data: dict, path: str, exclude_id: str) -> list[str]:
    """Return the ids of every node but exclude_id whose `files` already cover path.

    An entry covers path when it equals it, when it is a safe glob that matches it
    (`glob_match`, as `map_paths` does), or, when path is itself a glob, when the entry is a
    literal path the glob matches. Two different overlapping globs are not detected.
    """
    norm = _normalize_path(path)
    owners: list[str] = []
    for node_id, node in node_index(data).items():
        files = node.get("files")
        if node_id == exclude_id or not isinstance(files, list):
            continue
        for entry in files:
            if not isinstance(entry, str):
                continue
            entry_norm = _normalize_path(entry)
            if (
                entry_norm == norm
                or (glob_problem(entry) is None and glob_match(entry_norm, norm))
                or (_is_glob(norm) and not _is_glob(entry_norm) and glob_match(norm, entry_norm))
            ):
                owners.append(node_id)
                break
    return owners


def blast_radius(data: dict, start_ids: list[str]) -> list[str]:
    """Return the sorted v0.5.9 blast radius of start_ids, the start nodes included.

    From each node the BFS follows its `implements` parents and the nodes that list it in
    `depends_on`; `member_of` is not traversed. Unknown start ids are ignored.
    """
    index = node_index(data)
    dependents: dict[str, list[str]] = {node_id: [] for node_id in index}
    for node_id, node in index.items():
        for target in edge_list(node, "depends_on"):
            if target in dependents:
                dependents[target].append(node_id)
    start = [node_id for node_id in start_ids if node_id in index]
    processed = set(start)
    queue = list(start)
    while queue:
        current = queue.pop(0)
        for nxt in [*edge_list(index[current], "implements"), *dependents[current]]:
            if nxt in index and nxt not in processed:
                processed.add(nxt)
                queue.append(nxt)
    return sorted(processed)


def changed_paths(rev_range: str, top: Path) -> list[str]:
    """Return the sorted repo-relative paths a revision range changed.

    `A..B` (or `A...B`) is passed to `git diff --name-only`. A single revision compares the
    working tree against it and adds the untracked, non-ignored files, so an audit can run it
    on uncommitted work.
    """
    if not rev_range or rev_range.startswith("-"):
        raise UsageError("--from-diff takes a revision or a rev-range such as base..HEAD")
    single = ".." not in rev_range
    if single:
        check = _git(["rev-parse", "--verify", "-q", f"{rev_range}^{{commit}}"], top)
        if check.returncode != 0:
            raise UsageError(f"--from-diff {rev_range}: not a commit")
    proc = _git(
        ["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", rev_range, "--"], top
    )
    if proc.returncode != 0:
        raise UsageError(f"git diff {rev_range} failed: {proc.stderr.strip()}")
    paths = {path for path in proc.stdout.split("\0") if path}
    if single:
        others = _git(["ls-files", "--others", "--exclude-standard", "-z"], top)
        if others.returncode != 0:
            raise UsageError(f"git ls-files failed: {others.stderr.strip()}")
        paths |= {path for path in others.stdout.split("\0") if path}
    return sorted(paths)


def impact_from_diff(ledger: Path, rev_range: str) -> dict[str, list[str]]:
    """Return {changed, nodes, blast_radius, unmapped} for a diff of the ledger's repository.

    `unmapped` lists the changed paths that no node's `files` globs or `associated_file`
    cover (R21.1); hypergraph_updater.py --from-diff reports the same list.
    """
    _, data = load_ledger(ledger)
    top = repo_toplevel(Path(os.path.abspath(ledger)).parent)
    if top is None:
        raise UsageError(f"{_display(ledger)} is not inside a git work tree")
    paths = changed_paths(rev_range, top)
    nodes, unmapped = map_paths(data, paths)
    return {
        "changed": paths,
        "nodes": nodes,
        "blast_radius": blast_radius(data, nodes),
        "unmapped": unmapped,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _cycles(index: dict[str, dict], kind: str) -> list[list[str]]:
    """Return each cycle of one edge kind as the sorted ids of its strongly connected set."""
    succ = {
        node_id: [t for t in edge_list(node, kind) if t in index] for node_id, node in index.items()
    }
    reach: dict[str, set[str]] = {}
    for start, first in succ.items():
        seen: set[str] = set()
        stack = list(first)
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(succ[current])
        reach[start] = seen
    groups = {
        frozenset({u} | {v for v in reach[u] if u in reach[v]}) for u in succ if u in reach[u]
    }
    return sorted(sorted(group) for group in groups)


def _severity_order(finding: Finding) -> tuple:
    return (
        finding.severity != "error",
        finding.node,
        finding.rule,
        finding.detail,
        finding.message,
    )


class _Report:
    """Collects the findings of one validation run."""

    def __init__(self, index: dict[str, dict], tombstones: set[str], v2: bool) -> None:
        """Keep the node index, the tombstoned ids and the ledger version the rules need."""
        self.index = index
        self.tombstones = tombstones
        self.v2 = v2
        self.mixed: set[str] = set()  # v2-only fields seen (a warning in a v1 file)
        self.findings: list[Finding] = []

    def error(self, rule: str, node: str, detail: str, message: str) -> None:
        """Record an error."""
        self.findings.append(Finding("error", rule, node, detail, message))

    def warning(self, rule: str, node: str, detail: str, message: str) -> None:
        """Record a warning."""
        self.findings.append(Finding("warning", rule, node, detail, message))


def _check_enums(out: _Report, node_id: str, node: dict) -> None:
    dimension, status = node.get("dimension"), node.get("status")
    if dimension not in DIMENSION_RANK:
        message = f"dimension {dimension!r} is not one of {', '.join(DIMENSIONS)}"
        out.error("bad_dimension", node_id, repr(dimension), message)
    if status not in STATUSES:
        message = f"status {status!r} is not one of {', '.join(STATUSES)}"
        out.error("bad_status", node_id, repr(status), message)


def _check_edges(out: _Report, node_id: str, node: dict) -> None:
    edges = node.get("edges")
    if not isinstance(edges, dict):
        out.error("bad_edges", node_id, "", f"edges is {_kind(edges)}, not a mapping")
        return
    if "member_of" in edges:
        out.mixed.add("member_of")
    for kind in REQUIRED_EDGE_KINDS:
        if kind not in edges:
            out.error("missing_edges", node_id, kind, f"edges.{kind} is missing")
    for key, value in edges.items():
        kind = str(key)
        if value is None:  # the B5 class: `depends_on:` with nothing after it
            out.error("null_edges", node_id, kind, f"edges.{kind} is null, not a list")
        elif not isinstance(value, list):
            out.error("edges_not_list", node_id, kind, f"edges.{kind} is {_kind(value)}")
        else:
            for target in value:
                if not isinstance(target, str):
                    message = f"edges.{kind} holds {target!r}, not a node id"
                    out.error("bad_edge_target", node_id, f"{kind}->{target!r}", message)
                elif target not in out.index:
                    why = " (it is in removed_nodes)" if target in out.tombstones else ""
                    message = f"edges.{kind} names {target}, which is not a node{why}"
                    out.error("dangling_edge", node_id, f"{kind}->{target}", message)
    rank = DIMENSION_RANK.get(node.get("dimension"))
    for target in edge_list(node, "implements"):
        parent_dim = out.index.get(target, {}).get("dimension")
        if rank and parent_dim in DIMENSION_RANK and DIMENSION_RANK[parent_dim] <= rank:
            message = f"implements {target} ({parent_dim}), which is not above {node['dimension']}"
            out.error("inverted_implements", node_id, f"implements->{target}", message)


def _check_fields(out: _Report, node_id: str, node: dict) -> None:
    out.mixed.update(key for key in V2_NODE_KEYS if key in node)
    files = node.get("files", [])
    if not isinstance(files, list):
        out.error("bad_files", node_id, "", f"files is {_kind(files)}, not a list")
    else:
        for glob in files:
            problem = glob_problem(glob)
            if problem:
                out.error("unsafe_glob", node_id, repr(glob), f"files entry {glob!r} {problem}")
    description = node.get("description", "")
    if not isinstance(description, str):
        out.error("bad_description", node_id, "", f"description is {_kind(description)}")
    elif len(description) > DESCRIPTION_MAX:
        message = f"description has {len(description)} characters (limit {DESCRIPTION_MAX})"
        out.error("description_too_long", node_id, "", message)
    refs = node.get("spec_refs", [])
    if not isinstance(refs, list):
        out.error("bad_spec_refs", node_id, "", f"spec_refs is {_kind(refs)}, not a list")
    else:
        for ref in refs:
            if not isinstance(ref, str) or not DOT_ID_RE.match(ref):
                out.error(
                    "bad_spec_refs", node_id, repr(ref), f"spec_refs entry {ref!r} is not a dot-ID"
                )


def _check_endpoints(out: _Report, node_id: str, node: dict) -> None:
    for key, id_key in ENDPOINT_KEYS.items():
        items = node.get(key, [])
        if not isinstance(items, list):
            out.error("bad_endpoints", node_id, key, f"{key} is {_kind(items)}, not a list")
            continue
        for item in items:
            form = endpoint(item, id_key)
            if form is None:
                message = f"{key} entry {item!r} needs exactly one of {id_key} or external"
                out.error("bad_endpoint", node_id, f"{key}:{item!r}", message)
                continue
            shape, value = form
            if shape == "external":
                out.mixed.add("external")
                if not isinstance(value, str) or not EXTERNAL_RE.match(value):
                    message = f"{key}.external {value!r} does not match {EXTERNAL_RE.pattern}"
                    out.error("bad_external_label", node_id, f"{key}:{value!r}", message)
                elif value in out.index:
                    message = f"{key}.external {value!r} is also a node id"
                    out.warning("external_is_node", node_id, f"{key}:{value}", message)
            elif not isinstance(value, str) or not value:
                message = f"{key}.{id_key} {value!r} is not a string"
                out.error("bad_endpoint", node_id, f"{key}:{value!r}", message)
            elif value not in out.index and out.v2:
                message = f"{key}.{id_key} names {value}, which is not a node"
                out.error("dangling_endpoint", node_id, f"{key}:{value}", message)
            elif value not in out.index:
                message = f"{key}.{id_key} {value!r} is not a node (a v1 label endpoint)"
                out.warning("v1_label_endpoint", node_id, f"{key}:{value}", message)


def validate_data(data: dict) -> list[Finding]:
    """Return every error and warning for a loaded ledger, errors first, in a stable order."""
    schema = data.get("schema")
    v2 = is_v2(data)
    out = _Report({}, removed_ids(data), v2)
    if schema is not None and not v2:
        out.error("unknown_schema", "", repr(schema), f"schema is {schema!r}, not {SCHEMA_V2}")

    labelled: list[tuple[str, dict]] = []
    for position, node in enumerate(node_list(data)):
        label = f"nodes[{position}]"
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node, dict):
            out.error("node_not_mapping", label, "", f"is {_kind(node)}, not a mapping")
        elif not isinstance(node_id, str) or not node_id:
            out.error("bad_id", label, "", "has no string id")
        else:
            if not ID_RE.match(node_id):
                out.error("bad_id", node_id, "", f"id does not match {ID_RE.pattern}")
            if node_id in out.index:
                out.error("duplicate_id", node_id, "", "id appears more than once")
            out.index.setdefault(node_id, node)
            labelled.append((node_id, node))

    if "removed_nodes" in data:
        out.mixed.add("removed_nodes")
        removed = data["removed_nodes"]
        if not isinstance(removed, list):
            out.error("bad_removed_nodes", "", "", f"removed_nodes is {_kind(removed)}")
        for position, item in enumerate(removed if isinstance(removed, list) else []):
            tomb_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(tomb_id, str) or not tomb_id:
                message = f"removed_nodes[{position}] has no id"
                out.error("bad_tombstone", "", str(position), message)
            elif tomb_id in out.index:
                message = "is in removed_nodes but is still a node"
                out.error("tombstone_reused", tomb_id, "", message)

    for node_id, node in labelled:
        _check_enums(out, node_id, node)
        _check_edges(out, node_id, node)
        _check_fields(out, node_id, node)
        _check_endpoints(out, node_id, node)

    for kind in EDGE_KINDS:
        for members in _cycles(out.index, kind):
            message = f"edges.{kind} forms a cycle through {', '.join(members)}"
            out.error("cycle", members[0], f"{kind}:{','.join(members)}", message)

    if out.mixed and schema is None:
        fields = ", ".join(sorted(out.mixed))
        message = (
            f"mixed v1/v2 ledger: v2-only fields ({fields}) in a file without "
            f"schema: {SCHEMA_V2} (migrate-v2 normalizes it)"
        )
        out.warning("mixed_v1_v2", "", "", message)
    return sorted(out.findings, key=_severity_order)


def compare_with(current: dict, reference: dict, where: str) -> list[Finding]:
    """Return the node ids and per-node keys present in reference but missing from current.

    Ids in current's `removed_nodes` are declared removals, not drops (R21.4).
    """
    findings: list[Finding] = []
    declared = removed_ids(current)
    now = node_index(current)
    before = node_index(reference)
    for node_id in sorted(set(before) - set(now) - declared):
        findings.append(
            Finding(
                "error",
                "dropped_node",
                node_id,
                "",
                f"is in the ledger at {where} but missing here (declare it with remove-node)",
            )
        )
    for node_id in sorted(set(before) & set(now)):
        for key in sorted(str(key) for key in before[node_id] if key not in now[node_id]):
            findings.append(
                Finding(
                    "error",
                    "dropped_key",
                    node_id,
                    key,
                    f"key {key} is in the ledger at {where} but missing here",
                )
            )
    return findings


def reference_from_ref(ref: str, top: Path) -> tuple[dict | None, list[Finding]]:
    """Load `git show REF:spec/compiled/architecture.yml`; return (data or None, findings)."""
    if not ref or ref.startswith("-"):
        raise UsageError("--against-ref takes a revision")
    if _git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], top).returncode != 0:
        raise UsageError(f"--against-ref {ref}: not a commit")
    spec = f"{ref}:{LEDGER_REL}"
    if _git(["cat-file", "-e", spec], top).returncode != 0:
        note = f"{ref} has no {LEDGER_REL}; nothing to compare"
        return None, [Finding("warning", "against_missing", "", ref, note)]
    proc = _git(["show", spec], top)
    if proc.returncode != 0:
        raise UsageError(f"git show {spec} failed: {proc.stderr.strip()}")
    try:
        return parse_ledger(proc.stdout, spec), []
    except ParseError as exc:
        return None, [Finding("error", "reference_parse", "", ref, str(exc))]


def reference_from_file(path: Path) -> tuple[dict | None, list[Finding]]:
    """Load the ledger file F for `--against-file F`; return (data or None, findings)."""
    if not path.is_file():
        raise UsageError(f"--against-file {path}: no such file")
    try:
        return load_ledger(path)[1], []
    except ParseError as exc:
        return None, [Finding("error", "reference_parse", "", str(path), str(exc))]


# ---------------------------------------------------------------------------
# Non-regressive locked writes
# ---------------------------------------------------------------------------


def check_non_regression(
    before: dict | None, after: dict
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Return (new errors, pre-existing errors, warnings) of after compared with before."""
    old = set()
    if before is not None:
        old = {f.identity for f in validate_data(before) if f.severity == "error"}
    found = validate_data(after)
    errors = [f for f in found if f.severity == "error"]
    added = [f for f in errors if f.identity not in old]
    carried = [f for f in errors if f.identity in old]
    return added, carried, [f for f in found if f.severity == "warning"]


def _commit(path: Path, before: dict | None, after: dict, label: str) -> WriteResult:
    """Validate after against before and replace the file; call with the lock held."""
    if before is not None and after == before:
        return WriteResult(False, f"{label}: no change", [], 0)
    added, carried, warnings = check_non_regression(before, after)
    if added:
        raise Refused(
            f"refused: {label} would add {len(added)} new error(s); the ledger is unchanged",
            [finding.line() for finding in added],
        )
    text = dump_ledger(after)
    if parse_ledger(text, "<dump>") != after:
        raise Refused(f"refused: {label}: the dumped ledger does not load back to the same data")
    _atomic_write(path, text)
    return WriteResult(True, f"{label}: written", carried, len(warnings))


def update(
    path: Path,
    mutate: Callable[[dict], str],
    *,
    timeout: float | None = None,
) -> WriteResult:
    """Run one validated, locked, atomic write of the ledger at path.

    mutate changes the loaded data in place and returns a label for the report. The change
    is refused (Refused, file byte-identical) when it adds an error the ledger did not have.
    """
    path = Path(os.path.abspath(path))
    with ledger_lock(path, timeout):
        _, before = load_ledger(path)
        after = copy.deepcopy(before)
        label = mutate(after)
        return _commit(path, before, after, label)


def write_op(path: Path, op: str, args: dict, *, timeout: float | None = None) -> WriteResult:
    """Apply one write subcommand (normalized args) to the ledger at path, locked."""
    args = normalize_args(op, args)
    path = Path(os.path.abspath(path))
    if op == "init":
        with ledger_lock(path, timeout):
            if os.path.lexists(path):
                raise Refused(f"refused: a ledger already exists at {_display(path)}")
            path.parent.mkdir(parents=True, exist_ok=True)
            return _commit(path, None, _empty_ledger(), f"init {_display(path)}")
    return update(path, lambda data: APPLY[op](data, args, path), timeout=timeout)


def submit_op(path: Path | None, op: str, args: dict) -> tuple[WriteResult | None, Path | None]:
    """Route a write: to the HACF_OUTBOX directory when it is set, else to write_op.

    Returns (result, None) for a ledger write, or (None, entry path) for an outbox entry.
    path None means the repository ledger (resolved only for a ledger write).
    """
    outbox = os.environ.get(OUTBOX_ENV)
    if outbox:
        return None, write_outbox_entry(Path(outbox), op, normalize_args(op, args))
    return write_op(path if path is not None else default_ledger_path(), op, args), None


# ---------------------------------------------------------------------------
# Write operations (each mutates the data in place and returns a label)
# ---------------------------------------------------------------------------


def _empty_ledger() -> dict:
    return {"schema": SCHEMA_V2, "nodes": []}


def _edges_of(node: dict) -> dict:
    """Return node's edges mapping, creating it when absent; refuse a non-mapping value."""
    edges = node.setdefault("edges", {"depends_on": [], "implements": []})
    if not isinstance(edges, dict):
        raise Refused(f"refused: node {node.get('id')} has edges that are not a mapping; fix it")
    return edges


def _add_edges(edges: dict, kind: str, targets: list[str]) -> None:
    """Append each target the edge list lacks (a missing or null list becomes a list)."""
    current = edges.get(kind)
    if not isinstance(current, list):
        current = []
    for target in targets:
        if target not in current:
            current.append(target)
    edges[kind] = current


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _require_node(data: dict, node_id: str) -> dict:
    node = node_index(data).get(node_id)
    if node is None:
        gone = " (it is in removed_nodes)" if node_id in removed_ids(data) else ""
        raise UsageError(f"no node {node_id} in the ledger{gone}")
    return node


def _merge_files(node: dict, add_files: list[str]) -> None:
    """Append each path of add_files the node's files lack, in order; never remove one."""
    current = node.get("files") or []
    if not isinstance(current, list):
        raise Refused(f"refused: node {node.get('id')} has files that are not a list; fix it")
    merged = list(current)
    merged.extend(p for p in _dedupe(add_files) if p not in merged)
    if merged != current or "files" not in node:
        node["files"] = merged


def _guard_owners(data: dict, args: dict, node: dict | None) -> None:
    """Keep ensure-node from giving a path a second owner (OPR.99.0.6.4); see the docstring.

    Only the paths the node is about to gain are checked. `--from-spec` paths (add_files) that
    another node owns are dropped from args with a warning; owned `--files` entries refuse the
    run (UsageError, exit 2) unless allow_shared, which keeps them and says so.
    """
    node_id = args["id"]
    current = (node or {}).get("files")
    have = set(current) if isinstance(current, list) else set()
    if args.get("add_files"):
        kept = []
        for item in args["add_files"]:
            owners = [] if item in have else owners_of(data, item, node_id)
            if owners:
                print(f"ensure-node: skipped {item}: owned by {', '.join(owners)}", file=sys.stderr)
            else:
                kept.append(item)
        args["add_files"] = kept
    if args.get("files") is not None:
        owned = []
        for item in _dedupe(args["files"]):
            owners = [] if item in have else owners_of(data, item, node_id)
            if owners:
                owned.append((item, owners))
        if owned and not args.get("allow_shared"):
            raise UsageError(
                "\n".join(
                    f"refused: {item} is owned by {', '.join(owners)}; "
                    "pass --allow-shared to share it"
                    for item, owners in owned
                )
            )
        for item, owners in owned:
            print(f"ensure-node: shared {item} with {', '.join(owners)}", file=sys.stderr)


def _apply_ensure_node(data: dict, args: dict, path: Path) -> str:
    node_id = args["id"]
    if node_id in removed_ids(data):
        raise Refused(f"refused: {node_id} is in removed_nodes; a removed id is never reused")
    index = node_index(data)
    node = index.get(node_id)
    created = node is None
    args = dict(args)  # the guard may drop owned add_files; never mutate the caller's args
    _guard_owners(data, args, node)
    if created:
        if not args.get("dimension"):
            raise UsageError(f"{node_id} is a new node; --dimension is required")
        node = {"id": node_id, "dimension": args["dimension"], "status": "dirty"}
        if args.get("associated_file") is not None:
            node["associated_file"] = args["associated_file"]
        if args.get("files") is not None:
            node["files"] = list(args["files"])
        elif args.get("add_files"):
            node["files"] = _dedupe(args["add_files"])
        node["description"] = args.get("description") or ""
        node["inputs"] = []
        node["outputs"] = []
        node["edges"] = {"depends_on": [], "implements": []}
        if is_v2(data):
            node["edges"]["member_of"] = []
        data.setdefault("nodes", []).append(node)
    else:
        for key in ("dimension", "associated_file", "description"):
            if args.get(key) is not None:
                node[key] = args[key]
        if args.get("files") is not None:
            node["files"] = list(args["files"])
        elif args.get("add_files"):
            _merge_files(node, args["add_files"])
    parent_id = args.get("parent")
    if parent_id:
        parent = index.get(parent_id)
        if parent is None or parent_id == node_id:
            raise UsageError(f"--parent {parent_id}: no such node")
        parent_dim, node_dim = parent.get("dimension"), node.get("dimension")
        if parent_dim in ("Layer", "System"):
            _add_edges(_edges_of(node), "member_of", [parent_id])
        elif parent_dim == "Module" and node_dim == "Atomic":
            _add_edges(_edges_of(node), "implements", [parent_id])
        else:
            raise UsageError(
                f"--parent {parent_id}: a {node_dim} node cannot take a {parent_dim} parent "
                "(a Layer or System parent gives member_of; a Module parent is only for an "
                "Atomic node, which gets implements)"
            )
    for kind in ("member_of", "depends_on"):
        if args.get(kind):
            _add_edges(_edges_of(node), kind, args[kind])
    if args.get("spec_refs"):
        refs = node.get("spec_refs")
        if refs is None:
            refs = []
        if not isinstance(refs, list):
            raise Refused(f"refused: node {node_id} has spec_refs that are not a list; fix it")
        refs.extend(ref for ref in _dedupe(args["spec_refs"]) if ref not in refs)
        node["spec_refs"] = refs
    return f"ensure-node {node_id}" + (" (new node)" if created else "")


def _apply_set_edges(data: dict, args: dict, path: Path) -> str:
    node_id = args["id"]
    edges = _edges_of(_require_node(data, node_id))
    if args.get("remove_depends_on"):
        current = edges.get("depends_on")
        current = current if isinstance(current, list) else []
        edges["depends_on"] = [t for t in current if t not in args["remove_depends_on"]]
    if args.get("add_depends_on"):
        _add_edges(edges, "depends_on", args["add_depends_on"])
    if args.get("set_member_of") is not None:
        edges["member_of"] = _dedupe(args["set_member_of"])
    if args.get("set_implements") is not None:
        edges["implements"] = _dedupe(args["set_implements"])
    return f"set-edges {node_id}"


def _apply_mark(data: dict, args: dict, path: Path) -> str:
    ids = args["ids"]
    index = node_index(data)
    unknown = [node_id for node_id in ids if node_id not in index]
    if unknown:
        raise UsageError(f"mark: no such node(s): {', '.join(unknown)}")
    for node in node_list(data):
        if isinstance(node, dict) and node.get("id") in ids:
            node["status"] = args["status"]
    return f"mark {args['status']} {', '.join(ids)}"


def _apply_remove_node(data: dict, args: dict, path: Path) -> str:
    node_id = args["id"]
    _require_node(data, node_id)
    inbound = [
        f"{other['id']}: edges.{kind} -> {node_id}"
        for other in node_list(data)
        if isinstance(other, dict) and isinstance(other.get("id"), str) and other["id"] != node_id
        for kind in EDGE_KINDS
        if node_id in edge_list(other, kind)
    ]
    if inbound:
        raise Refused(
            f"refused: {node_id} still has {len(inbound)} inbound edge(s); remove them with "
            "set-edges first",
            inbound,
        )
    tombstones = data.get("removed_nodes")
    if tombstones is None:
        tombstones = data["removed_nodes"] = []
    if not isinstance(tombstones, list):
        raise Refused("refused: removed_nodes is not a list; fix it first")
    data["nodes"] = [
        node
        for node in node_list(data)
        if not (isinstance(node, dict) and node.get("id") == node_id)
    ]
    tombstones.append(
        {
            "id": node_id,
            "reason": args["reason"],
            "slice": args.get("slice") or None,
            "commit": _head_commit(path),
        }
    )
    return f"remove-node {node_id}"


def _apply_review(data: dict, args: dict, path: Path) -> str:
    patch, slice_id = args["patch"], args["slice"]
    for node_id in patch:
        _require_node(data, node_id)
    for node_id, change in patch.items():
        node = node_index(data)[node_id]
        patched: list[str] = []
        for key in PATCH_KEYS:
            if key not in change:
                continue
            if key == "edges":
                edges = _edges_of(node)
                for kind, targets in change["edges"].items():
                    edges[kind] = _dedupe(targets)
                    patched.append(f"edges.{kind}")
            else:
                node[key] = copy.deepcopy(change[key])
                patched.append(key)
        node["status"] = "clean"
        node["review"] = {
            "reviewed_at": _runtime.utc_now_iso(),
            "reviewed_by": f"audit:{slice_id}",
            "summary": f"patched {', '.join(patched)}" if patched else "reviewed; nothing patched",
        }
    return f"apply-review {', '.join(patch) or '(no nodes)'}"


APPLY: dict[str, Callable[[dict, dict, Path], str]] = {
    "ensure-node": _apply_ensure_node,
    "set-edges": _apply_set_edges,
    "apply-review": _apply_review,
    "mark": _apply_mark,
    "remove-node": _apply_remove_node,
}


def _touched(op: str, args: dict) -> list[str]:
    """Return the node ids an operation writes (for apply-outbox --base-file conflicts)."""
    if op in ("ensure-node", "set-edges", "remove-node"):
        return [args["id"]]
    if op == "mark":
        return list(args["ids"])
    if op == "apply-review":
        return list(args["patch"])
    return []


# ---------------------------------------------------------------------------
# Argument normalization (shared by the CLI and apply-outbox)
# ---------------------------------------------------------------------------


def split_list(value: str | None) -> list[str] | None:
    """Split one comma-separated flag value into its items (None stays None)."""
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _check_ids(flag: str, ids: list[str] | None) -> None:
    for node_id in ids or []:
        if not ID_RE.match(node_id):
            raise UsageError(f"{flag}: {node_id!r} is not a node id ({ID_RE.pattern})")


def check_patch(patch: Any) -> None:
    """Raise UsageError unless patch has the apply-review shape (keys and value types)."""
    problems: list[str] = []
    if not isinstance(patch, dict):
        raise UsageError(f"the patch must be a JSON object, not {_kind(patch)}")
    for node_id, change in patch.items():
        where = f"patch[{node_id!r}]"
        if not isinstance(change, dict):
            problems.append(f"{where} must be an object")
            continue
        for key in change:
            if key not in PATCH_KEYS:
                problems.append(f"{where}: unknown key {key!r} (allowed: {', '.join(PATCH_KEYS)})")
        if "description" in change and not isinstance(change["description"], str):
            problems.append(f"{where}.description must be a string")
        for key, id_key in ENDPOINT_KEYS.items():
            if key not in change:
                continue
            items = change[key]
            if not isinstance(items, list):
                problems.append(f"{where}.{key} must be a list")
                continue
            for item in items:
                allowed = {"data_type", id_key, "external"}
                if not isinstance(item, dict) or not set(item) <= allowed:
                    problems.append(f"{where}.{key}: {item!r} may hold only {sorted(allowed)}")
                elif not all(isinstance(value, str) for value in item.values()):
                    problems.append(f"{where}.{key}: {item!r} values must be strings")
        if "files" in change and not (
            isinstance(change["files"], list) and all(isinstance(g, str) for g in change["files"])
        ):
            problems.append(f"{where}.files must be a list of strings")
        if "edges" in change:
            edges = change["edges"]
            if not isinstance(edges, dict):
                problems.append(f"{where}.edges must be an object")
            else:
                for kind, targets in edges.items():
                    if kind not in EDGE_KINDS:
                        problems.append(f"{where}.edges: unknown key {kind!r}")
                    elif not (
                        isinstance(targets, list) and all(isinstance(t, str) for t in targets)
                    ):
                        problems.append(f"{where}.edges.{kind} must be a list of node ids")
    if problems:
        raise UsageError("the patch does not match the apply-review shape", problems)


def normalize_args(op: str, args: dict) -> dict:
    """Check one write operation's arguments (no ledger needed); raise UsageError (exit 2)."""
    if op == "init":
        return {"path": args.get("path")}
    if op == "ensure-node":
        _check_ids("--id", [args.get("id") or ""])
        if args.get("dimension") is not None and args["dimension"] not in DIMENSION_RANK:
            raise UsageError(f"--dimension must be one of {', '.join(DIMENSIONS)}")
        if args.get("parent"):
            _check_ids("--parent", [args["parent"]])
        _check_ids("--member-of", args.get("member_of"))
        _check_ids("--depends-on", args.get("depends_on"))
        bad = [ref for ref in args.get("spec_refs") or [] if not DOT_ID_RE.match(ref)]
        if bad:
            raise UsageError(
                f"--spec-refs: {', '.join(repr(ref) for ref in bad)} is not a dot-ID "
                f"({DOT_ID_RE.pattern})"
            )
        if args.get("description") is not None and not isinstance(args["description"], str):
            raise UsageError("the description must be a string")
        add_files = args.get("add_files")
        if add_files is not None:
            if args.get("files") is not None:
                raise UsageError(FILES_AND_FROM_SPEC)
            if not isinstance(add_files, list):
                raise UsageError("add_files must be a list of repo-relative paths")
            for item in add_files:
                problem = glob_problem(item)
                if problem is None and TOUCH_GLOB_CHARS & set(item):
                    problem = "is a glob; only exact paths are merged"
                if problem:
                    raise UsageError(f"add_files: {item!r} {problem}")
        keys = (
            "id",
            "dimension",
            "parent",
            "member_of",
            "depends_on",
            "files",
            "add_files",
            "associated_file",
            "description",
            "spec_refs",
            "allow_shared",
        )
        return {key: args.get(key) for key in keys}
    if op == "set-edges":
        _check_ids("--id", [args.get("id") or ""])
        keys = ("add_depends_on", "remove_depends_on", "set_member_of", "set_implements")
        if all(args.get(key) is None for key in keys):
            raise UsageError("set-edges needs at least one edge flag")
        for key in keys:
            _check_ids(f"--{key.replace('_', '-')}", args.get(key))
        return {"id": args["id"], **{key: args.get(key) for key in keys}}
    if op == "apply-review":
        check_patch(args.get("patch"))
        if not isinstance(args.get("slice"), str) or not args["slice"].strip():
            raise UsageError("--slice must name the slice (for example S01a)")
        # A stripped description can always be written as a folded block scalar.
        patch = {
            node_id: {**change, "description": change["description"].strip()}
            if "description" in change
            else change
            for node_id, change in args["patch"].items()
        }
        return {"patch": patch, "slice": args["slice"].strip()}
    if op == "mark":
        if args.get("status") not in STATUSES:
            raise UsageError("mark needs one of --dirty, --needs-review or --clean")
        if not args.get("ids"):
            raise UsageError("--ids needs at least one node id")
        _check_ids("--ids", args["ids"])
        return {"status": args["status"], "ids": _dedupe(args["ids"])}
    if op == "remove-node":
        _check_ids("remove-node", [args.get("id") or ""])
        if not isinstance(args.get("reason"), str) or not args["reason"].strip():
            raise UsageError("--reason must say why the node is removed")
        return {"id": args["id"], "reason": args["reason"].strip(), "slice": args.get("slice")}
    raise UsageError(f"unknown write operation {op!r}")


def _clean_header_value(value: str) -> str | None:
    value = value.strip().strip("`'\"").strip()
    return None if not value or value.lower() == "none" else value


def read_spec_node(path: Path) -> dict:
    """Return {id, parent, files} from a legacy MiniPRD header or a v6 SPEC.md frontmatter.

    A v6 frontmatter gives `node_id`, `parent` and `hacf_touches` (as files, which
    ensure-node merges into the node's `files`: exact paths appended when missing, glob
    touches skipped and named on stderr); otherwise the `**Hypergraph Node ID:**` and
    `**Parent Node:**` lines give id and parent (`none` = no parent). Edges are never
    inferred from prose.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"--from-spec {path}: cannot read it ({exc})") from None
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is not None:
            try:
                front = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError as exc:
                raise UsageError(f"--from-spec {path}: frontmatter does not parse: {exc}") from None
            if isinstance(front, dict) and front.get("node_id") is not None:
                node_id, parent, touches = (
                    front.get("node_id"),
                    front.get("parent"),
                    front.get("hacf_touches"),
                )
                if not isinstance(node_id, str):
                    raise UsageError(f"--from-spec {path}: node_id must be a string")
                if parent is not None and not isinstance(parent, str):
                    raise UsageError(f"--from-spec {path}: parent must be a string")
                if touches is not None and not (
                    isinstance(touches, list) and all(isinstance(t, str) for t in touches)
                ):
                    raise UsageError(f"--from-spec {path}: hacf_touches must be a list of paths")
                return {
                    "id": _clean_header_value(node_id),
                    "parent": _clean_header_value(parent) if parent else None,
                    "files": list(touches) if touches is not None else None,
                }
    match = LEGACY_ID_RE.search(text)
    if match is None or _clean_header_value(match.group(1)) is None:
        raise UsageError(
            f"--from-spec {path}: no frontmatter node_id and no **Hypergraph Node ID:** line"
        )
    parent = LEGACY_PARENT_RE.search(text)
    return {
        "id": _clean_header_value(match.group(1)),
        "parent": _clean_header_value(parent.group(1)) if parent else None,
        "files": None,
    }


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


def _next_sequence(directory: Path) -> int:
    numbers = [
        int(match.group(1))
        for name in os.listdir(directory)
        if (match := OUTBOX_ENTRY_RE.match(name))
    ]
    return max(numbers, default=0) + 1


def write_outbox_entry(directory: Path, op: str, args: dict) -> Path:
    """Write `{"tool": "hyper_ledger", "op", "args"}` as the next numbered entry; return it.

    No lock is taken: the name is claimed with O_EXCL, so concurrent writers never share one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"tool": OUTBOX_TOOL, "op": op, "args": args}, indent=2, sort_keys=True)
    for _ in range(10000):
        target = directory / f"{_next_sequence(directory):06d}-{op}.json"
        try:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return target
    raise UsageError(f"could not claim an outbox entry name in {directory}")


def read_outbox(directory: Path) -> list[tuple[str, dict]]:
    """Return the outbox entries of every tool as (file name, entry), in sequence order."""
    if not directory.is_dir():
        raise UsageError(f"apply-outbox {directory}: no such directory")
    names = []
    for name in os.listdir(directory):
        match = OUTBOX_ENTRY_RE.match(name)
        if match:
            names.append((int(match.group(1)), name))
    entries = []
    for _, name in sorted(names):
        try:
            entry = json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UsageError(f"outbox entry {name} is not readable JSON ({exc})") from None
        if not isinstance(entry, dict):
            raise UsageError(f"outbox entry {name} is not a JSON object")
        entries.append((name, entry))
    return entries


def apply_outbox(
    path: Path,
    directory: Path,
    base_file: Path | None = None,
    *,
    timeout: float | None = None,
) -> tuple[list[str], WriteResult]:
    """Replay the hyper_ledger entries of an outbox onto the ledger at path, all or nothing.

    Each entry goes through the same checks as the subcommand it records, and a step that
    adds an error refuses the whole replay. With base_file, an entry touching a node whose
    content differs between base_file and the current ledger is a conflict: nothing is
    written and Refused lists every conflicting node (R5). Returns (report lines, result).
    """
    path = Path(os.path.abspath(path))
    report: list[str] = []
    ops: list[tuple[str, str, dict]] = []
    for name, entry in read_outbox(directory):
        if entry.get("tool") != OUTBOX_TOOL:
            report.append(f"skipped {name}: an entry of tool {entry.get('tool')!r}")
            continue
        op, args = entry.get("op"), entry.get("args")
        if op not in WRITE_OPS or not isinstance(args, dict):
            raise UsageError(f"outbox entry {name}: unknown op {op!r} or args not an object")
        try:
            ops.append((name, op, normalize_args(op, args)))
        except UsageError as exc:
            raise UsageError(f"outbox entry {name}: {exc}", exc.details) from None
    base = load_ledger(base_file)[1] if base_file is not None else None
    with ledger_lock(path, timeout):
        exists = os.path.lexists(path)
        before = load_ledger(path)[1] if exists else None
        if base is not None:
            base_index = node_index(base)
            now_index = node_index(before) if before is not None else {}
            conflicts = _dedupe(
                [
                    f"conflict: node {node_id} differs between the base file and the ledger "
                    f"({name}, {op})"
                    for name, op, args in ops
                    for node_id in _touched(op, args)
                    if base_index.get(node_id) != now_index.get(node_id)
                ]
            )
            if conflicts:
                raise Refused(
                    f"refused: {len(conflicts)} outbox conflict(s); the ledger is unchanged",
                    conflicts,
                )
        state = copy.deepcopy(before)
        for name, op, args in ops:
            if op == "init":
                if state is not None:
                    raise Refused(f"refused: {name}: init, but a ledger already exists")
                state = _empty_ledger()
                report.append(f"applied {name}: init")
                continue
            if state is None:
                raise UsageError(f"{name}: no ledger at {_display(path)} to apply {op} to")
            step_before = copy.deepcopy(state)
            label = APPLY[op](state, args, path)
            added = check_non_regression(step_before, state)[0]
            if added:
                raise Refused(
                    f"refused: {name} ({label}) would add {len(added)} new error(s); the "
                    "ledger is unchanged",
                    [finding.line() for finding in added],
                )
            report.append(f"applied {name}: {label}")
        if state is None:
            return report, WriteResult(False, "apply-outbox: nothing to apply", [], 0)
        if before is None:
            path.parent.mkdir(parents=True, exist_ok=True)
        count = sum(1 for line in report if line.startswith("applied "))
        return report, _commit(path, before, state, f"apply-outbox ({count} entries)")


# ---------------------------------------------------------------------------
# Migration (S01b): migrate-v2 and repair-text
# ---------------------------------------------------------------------------


class Migration(NamedTuple):
    """What migrate_data changed in a ledger, for the migrate-v2 summary."""

    schema_added: bool
    member_of: list[str]  # "<node> -> <layer>" for each depends_on edge moved to member_of
    externals: dict[str, int]  # endpoints turned into {external: <label>}, per inputs/outputs
    renamed: list[str]  # "<node>.<key>[<i>] <old> -> <label>" for each snake_cased label
    provenance: list[str]  # nodes whose _provenance became provenance
    files: list[str]  # nodes given files: [<associated_file>]
    empty_member_of: list[str]  # nodes given an empty edges.member_of list


def _unified_diff(old: str, new: str, label: str) -> str:
    """Return the unified diff between two texts of the ledger ('' when they are equal)."""
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _rebuild(mapping: dict, items: list[tuple[Any, Any]]) -> None:
    """Replace mapping's items in place, in the given order."""
    mapping.clear()
    mapping.update(items)


def _seedable_file(top: Path, associated: Any) -> str | None:
    """Return associated_file as a `files` entry when it names a regular file under top.

    A directory, a symlink, a missing path, or a path that is not a safe literal glob
    (absolute, `..`, glob characters) is never seeded.
    """
    if not isinstance(associated, str):
        return None
    rel = _normalize_path(associated.strip())
    if not rel or glob_problem(rel) is not None or GLOB_CHARS & set(rel):
        return None
    try:
        return rel if stat.S_ISREG(os.lstat(top / rel).st_mode) else None
    except OSError:
        return None


def external_label(value: str) -> str | None:
    """Return value as an external label, or None when it cannot be one.

    A value matching EXTERNAL_RE is kept; a camelCase value becomes snake_case
    (activeContext -> active_context) when that matches; anything else is None.
    """
    if EXTERNAL_RE.match(value):
        return value
    snake = CAMEL_BOUNDARY_RE.sub("_", value).lower()
    return snake if EXTERNAL_RE.match(snake) else None


def migrate_data(data: dict, top: Path) -> Migration:
    """Convert a loaded v1 (or mixed v1/v2) ledger to hacf.ledger/v2 in place.

    The schema key goes first; each depends_on edge to a Layer node moves to member_of
    (once); each endpoint naming no node becomes `{external: <label>}` in place of its
    source_id/target_id, a camelCase label turned snake_case (external_label);
    `_provenance` is renamed `provenance`; a Module or Atomic node without `files` gets
    `files: [<associated_file>]` when that is a regular file under top.
    v2 fields that ensure-node wrote before the migration are kept without duplicates, and
    `removed_nodes` is untouched (R21). Edges are never inferred from prose. Raises Refused
    (data may be half-changed; callers work on a copy) for an endpoint label that does not
    match EXTERNAL_RE even in snake_case, two differing provenance records on one node, or
    a foreign schema.
    """
    schema = data.get("schema")
    if schema is not None and schema != SCHEMA_V2:
        raise Refused(f"refused: schema is {schema!r}; migrate-v2 converts only v1 ledgers")
    index = node_index(data)
    layers = {node_id for node_id, node in index.items() if node.get("dimension") == "Layer"}
    result = Migration(schema is None, [], {key: 0 for key in ENDPOINT_KEYS}, [], [], [], [])
    problems: list[str] = []
    for position, node in enumerate(node_list(data)):
        if not isinstance(node, dict):
            continue
        label = node["id"] if isinstance(node.get("id"), str) else f"nodes[{position}]"

        edges = node.get("edges")
        if isinstance(edges, dict):
            depends = edges.get("depends_on")
            if isinstance(depends, list):
                to_layers = _dedupe([t for t in depends if isinstance(t, str) and t in layers])
                if to_layers:
                    edges["depends_on"] = [t for t in depends if t not in to_layers]
                    _add_edges(edges, "member_of", to_layers)
                    result.member_of.extend(f"{label} -> {target}" for target in to_layers)
            if "member_of" not in edges:
                edges["member_of"] = []
                result.empty_member_of.append(label)

        for key, id_key in ENDPOINT_KEYS.items():
            items = node.get(key)
            for slot, item in enumerate(items if isinstance(items, list) else []):
                form = endpoint(item, id_key)
                if form is None or form[0] != "node":
                    continue
                value = form[1]
                if isinstance(value, str) and value in index:
                    continue
                external = external_label(value) if isinstance(value, str) else None
                if external is None:
                    problems.append(
                        f"node {label}: {key}[{slot}].{id_key} {value!r} names no node and is "
                        f"not an external label ({EXTERNAL_RE.pattern}), even in snake_case"
                    )
                    continue
                if external != value:
                    result.renamed.append(f"{label}.{key}[{slot}] {value} -> {external}")
                items[slot] = {
                    ("external" if k == id_key else k): (external if k == id_key else v)
                    for k, v in item.items()
                }
                result.externals[key] += 1

        if "_provenance" in node:
            if "provenance" in node and node["provenance"] != node["_provenance"]:
                problems.append(
                    f"node {label}: has both _provenance and provenance, and they differ"
                )
            else:
                renamed = "provenance" not in node
                _rebuild(
                    node,
                    [
                        ("provenance" if k == "_provenance" else k, v)
                        for k, v in node.items()
                        if k != "_provenance" or renamed
                    ],
                )
                result.provenance.append(label)

        if node.get("dimension") in SEEDED_DIMENSIONS and "files" not in node:
            seed = _seedable_file(top, node.get("associated_file"))
            if seed is not None:
                pairs = list(node.items())
                keys = [k for k, _ in pairs]
                at = keys.index("associated_file") + 1 if "associated_file" in keys else len(keys)
                _rebuild(node, [*pairs[:at], ("files", [seed]), *pairs[at:]])
                result.files.append(label)

    if problems:
        raise Refused(
            f"refused: migrate-v2 stopped on {len(problems)} problem(s); nothing written", problems
        )
    if result.schema_added:
        _rebuild(data, [("schema", SCHEMA_V2), *((k, v) for k, v in data.items() if k != "schema")])
    return result


def migration_summary(result: Migration, data: dict) -> list[str]:
    """Return the summary lines migrate-v2 prints above its diff."""
    total = sum(
        len(edge_list(node, "member_of")) for node in node_list(data) if isinstance(node, dict)
    )
    inputs, outputs = result.externals["inputs"], result.externals["outputs"]
    return [
        f"schema: {SCHEMA_V2} ({'added' if result.schema_added else 'already set'})",
        f"member_of: {len(result.member_of)} depends_on edge(s) to a Layer node converted; "
        f"{total} member_of edge(s) in total",
        *(f"  {edge}" for edge in result.member_of),
        f"external: {inputs + outputs} endpoint(s) converted ({inputs} inputs, {outputs} outputs)",
        f"labels: {len(result.renamed)} camelCase label(s) turned snake_case",
        *(f"  {rename}" for rename in result.renamed),
        f"provenance: {len(result.provenance)} _provenance key(s) renamed"
        + (f": {', '.join(result.provenance)}" if result.provenance else ""),
        f"files: {len(result.files)} node(s) seeded from associated_file"
        + (f": {', '.join(result.files)}" if result.files else ""),
        f"edges.member_of: [] added to {len(result.empty_member_of)} node(s)",
    ]


def migrate_v2(
    path: Path, *, dry_run: bool = False, timeout: float | None = None
) -> tuple[list[str], str, WriteResult]:
    """Run migrate-v2 on the ledger at path; return (summary lines, diff, result).

    The write holds the ledger lock from the read to the atomic replace and is
    non-regressive like every other one. A ledger that is already migrated gives
    `migrate-v2: no change` and stays byte-identical. dry_run takes no lock and writes
    nothing, but still refuses (exit 5) a migration that would add an error.
    """
    path = Path(os.path.abspath(path))
    top = repo_toplevel(path.parent) or path.parent
    with contextlib.nullcontext() if dry_run else ledger_lock(path, timeout):
        text, before = load_ledger(path)
        after = copy.deepcopy(before)
        result = migrate_data(after, top)
        if after == before:
            return [], "", WriteResult(False, "migrate-v2: no change", [], 0)
        summary = migration_summary(result, after)
        diff = _unified_diff(text, dump_ledger(after), _display(path))
        if not dry_run:
            return summary, diff, _commit(path, before, after, "migrate-v2")
        added, carried, warnings = check_non_regression(before, after)
        if added:
            raise Refused(
                f"refused: migrate-v2 would add {len(added)} new error(s); nothing written",
                [finding.line() for finding in added],
            )
        message = "migrate-v2 --dry-run: nothing written"
        return summary, diff, WriteResult(False, message, carried, len(warnings))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _value_end(lines: list[str], start: int, key_col: int) -> int:
    """Return the index after the last line of the value whose key is on lines[start].

    The value's lines are the following ones indented past key_col; a blank line belongs to
    it only when a deeper line follows.
    """
    end = probe = start + 1
    while probe < len(lines):
        if lines[probe].strip():
            if _indent(lines[probe]) <= key_col:
                break
            end = probe + 1
        probe += 1
    return end


def _is_plain(value: str) -> bool:
    """Return True when the text after `key: ` starts a plain (unquoted, unindicated) scalar."""
    return (
        bool(value)
        and value[0] not in PLAIN_FIRST_FORBIDDEN
        and not value.startswith(("- ", "? ", ": "))
    )


def fold_broken_descriptions(text: str, label: str) -> tuple[str, list[int]]:
    """Fold each plain `description:` scalar holding ` #` or `: ` into a `>-` block scalar.

    It works on the raw text, since the file may not parse. The key line becomes
    `description: >-`, its text moves to the next line indented two spaces past the key, and
    the continuation lines stay as they are; no other line changes. Block scalars are
    skipped whole. Returns (new text, 1-based numbers of the folded key lines). Raises
    ParseError (exit 4) for a broken scalar it cannot fold safely: a continuation line not
    indented exactly two spaces past the key would change the folded text.
    """
    lines = text.splitlines(keepends=True)
    newline = "\n"  # for a key line that ends the file without one
    out: list[str] = []
    folded: list[int] = []
    i = 0
    while i < len(lines):
        body = lines[i].rstrip("\r\n")
        eol = lines[i][len(body) :]
        header = BLOCK_HEADER_RE.match(body)
        match = None if header else DESCRIPTION_LINE_RE.match(body)
        if header:
            end = _value_end(lines, i, len(header.group("lead")))
        elif match is not None and _is_plain(match.group("value")):
            end = _value_end(lines, i, len(match.group("lead")))
        else:
            out.append(lines[i])
            i += 1
            continue
        continuation = lines[i + 1 : end]
        parts = [match.group("value"), *(line.strip() for line in continuation)] if match else []
        if not any(PLAIN_BREAKER_RE.search(part) for part in parts):
            out.extend(lines[i:end])
            i = end
            continue
        indent = len(match.group("lead")) + 2
        uneven = [
            str(i + 2 + n)
            for n, line in enumerate(continuation)
            if line.strip() and _indent(line) != indent
        ]
        if uneven:
            raise ParseError(
                f"{label}:{i + 1}: repair-text cannot fold this description safely: line(s) "
                f"{', '.join(uneven)} are not indented {indent} spaces; nothing written"
            )
        out.append(f"{match.group('lead')}description: >-{eol or newline}")
        out.append(f"{' ' * indent}{match.group('value')}{eol}")
        out.extend(continuation)
        folded.append(i + 1)
        i = end
    return "".join(out), folded


def repair_text(
    path: Path, *, dry_run: bool = False, timeout: float | None = None
) -> tuple[str, WriteResult]:
    """Run repair-text on the ledger at path; return (diff, result).

    The repaired text is written under the ledger lock with the same atomic save as every
    other write (temp file, fsync, os.replace, fsync of the directory, mode kept). Raises
    ParseError (exit 4, nothing written) when the result still does not parse, and Refused
    (exit 5) when a ledger that parsed before would gain an error. dry_run takes no lock.
    """
    path = Path(os.path.abspath(path))
    label = _display(path)
    with contextlib.nullcontext() if dry_run else ledger_lock(path, timeout):
        text = read_ledger_text(path)
        new_text, folded = fold_broken_descriptions(text, label)
        try:
            before, broken = parse_ledger(text, label), None
        except ParseError as exc:
            before, broken = None, exc
        if not folded:
            if broken is not None:
                raise ParseError(f"{broken}; repair-text found no description to fold")
            return "", WriteResult(False, "repair-text: no change", [], 0)
        try:
            after = parse_ledger(new_text, label)
        except ParseError as exc:
            raise ParseError(
                f"repair-text folded {len(folded)} description(s), but the result still does "
                f"not parse (line numbers of the repaired text): {exc}; nothing written"
            ) from None
        added, carried, warnings = check_non_regression(before, after)
        if before is None:  # the old text did not parse, so it had no comparable findings
            added, carried = [], added
        if added:
            raise Refused(
                f"refused: repair-text would add {len(added)} new error(s); nothing written",
                [finding.line() for finding in added],
            )
        diff = _unified_diff(text, new_text, label)
        lines = ", ".join(str(n) for n in folded)
        if dry_run:
            message = f"repair-text --dry-run: would fold line(s) {lines}; nothing written"
            return diff, WriteResult(False, message, carried, len(warnings))
        _atomic_write(path, new_text)
        return diff, WriteResult(
            True, f"repair-text: folded line(s) {lines}; written", carried, len(warnings)
        )


# ---------------------------------------------------------------------------
# Review template
# ---------------------------------------------------------------------------


def patch_schema(v2: bool) -> dict:
    """Return the JSON Schema of an apply-review patch for a v1 or v2 ledger."""
    ids = {"type": "array", "items": {"type": "string"}}

    def endpoints(id_key: str) -> dict:
        node_form = {
            "type": "object",
            "required": ["data_type", id_key],
            "properties": {"data_type": {"type": "string"}, id_key: {"type": "string"}},
            "additionalProperties": False,
        }
        external = {
            "type": "object",
            "required": ["data_type", "external"],
            "properties": {
                "data_type": {"type": "string"},
                "external": {"type": "string", "pattern": EXTERNAL_RE.pattern},
            },
            "additionalProperties": False,
        }
        return {"type": "array", "items": {"oneOf": [node_form, external] if v2 else [node_form]}}

    return {
        "type": "object",
        "description": "{<node id>: {description?, inputs?, outputs?, files?, edges?}}",
        "additionalProperties": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": DESCRIPTION_MAX},
                "inputs": endpoints("source_id"),
                "outputs": endpoints("target_id"),
                "files": ids,
                "edges": {
                    "type": "object",
                    "properties": {kind: ids for kind in EDGE_KINDS},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    }


REVIEW_INSTRUCTIONS = (
    "Read each listed node's associated_file and files, then answer with only one JSON object "
    "in the patch_schema shape: {<node id>: {<keys whose value must change>}}. Each key you "
    "give replaces that value (edges: each given edge list). List every node; {} keeps a node "
    "as it is. A node-form endpoint (source_id/target_id) must name a node in a v2 ledger; use "
    "{external: <label>} for anything else. Never infer edges from prose. apply-review sets "
    "each listed node clean and records the review."
)


def review_template(data: dict, statuses: list[str]) -> dict:
    """Return the review-template document for the nodes whose status is in statuses."""
    keys = ("dimension", "status", "associated_file", "files", "description", "inputs", "outputs")
    nodes = [
        {"id": node["id"], **{key: node.get(key) for key in keys}, "edges": node.get("edges")}
        for node in node_list(data)
        if isinstance(node, dict)
        and isinstance(node.get("id"), str)
        and node.get("status") in statuses
    ]
    return {
        "schema": SCHEMA_V2 if is_v2(data) else "v1",
        "statuses": statuses,
        "nodes": nodes,
        "instructions": REVIEW_INSTRUCTIONS,
        "patch_schema": patch_schema(is_v2(data)),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _list_text(items: list[str]) -> str:
    return json.dumps(items)


def _print_write(result: WriteResult) -> None:
    print(result.message)
    for finding in result.carried:
        print(f"{finding.line('warning')} (pre-existing error)")
    if result.warnings:
        print(
            f"note: the ledger has {result.warnings} warning(s); list them with "
            "`python3 .agents/scripts/hyper_ledger.py validate`"
        )


def _submit_cli(op: str, args: dict, path: Path | None = None) -> int:
    result, entry = submit_op(path, op, args)
    if entry is not None:
        print(f"{op}: recorded as outbox entry {entry} ({OUTBOX_ENV} is set; ledger not written)")
    else:
        _print_write(result)
    return EXIT_OK


def cmd_init(ns: argparse.Namespace) -> int:
    """Write an empty hacf.ledger/v2 ledger; refuse when one exists."""
    path = Path(os.path.abspath(ns.path)) if ns.path else None
    return _submit_cli("init", {"path": ns.path}, path)


def cmd_show(ns: argparse.Namespace) -> int:
    """Print a summary of the ledger: one row per node with its edges."""
    path = default_ledger_path()
    _, data = load_ledger(path)
    nodes = [n for n in node_list(data) if isinstance(n, dict)]
    tombstones = removed_nodes(data)
    version = SCHEMA_V2 if is_v2(data) else "v1 (no schema key)"
    print(f"{_display(path)}: {version}, {len(nodes)} node(s), {len(tombstones)} removed")
    width = max([len(str(n.get("id"))) for n in nodes] + [2])
    for node in nodes:
        edges = [
            f"{kind}: {', '.join(edge_list(node, kind))}"
            for kind in EDGE_KINDS
            if edge_list(node, kind)
        ]
        print(
            f"  {str(node.get('id')):<{width}}  {str(node.get('dimension')):<9} "
            f"{str(node.get('status')):<12}  {'; '.join(edges) or '-'}"
        )
    for item in tombstones:
        print(f"  removed: {item.get('id')} (slice {item.get('slice')}): {item.get('reason')}")
    return EXIT_OK


def cmd_validate(ns: argparse.Namespace) -> int:
    """Validate the ledger; exit 1 on an error (on any finding with --strict).

    The text report is one line per finding plus a fixed status line, with no counts or
    paths that vary: v060_gate.py finish compares these lines between this script's
    scope-base copy and the working tree, so any varying line reads as a new finding.
    """
    top = _repo()
    path = top / LEDGER_REL
    try:
        _, data = load_ledger(path)
    except ParseError as exc:
        if ns.json:
            print(json.dumps({"ok": False, "parse_error": str(exc)}, indent=2))
        else:
            print(f"error: {exc} [parse_error]")
            print("validate: failed")
        return EXIT_PARSE
    findings = validate_data(data)
    against = ns.against_ref or ns.against_file
    if ns.against_ref:
        reference, notes = reference_from_ref(ns.against_ref, top)
    elif ns.against_file:
        reference, notes = reference_from_file(Path(ns.against_file))
    else:
        reference, notes = None, []
    findings += notes
    if reference is not None:
        findings += compare_with(data, reference, against)
    findings.sort(key=_severity_order)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    leftover = needs_review_ids(data)
    failed = bool(errors) or (ns.strict and bool(warnings or leftover))
    if ns.json:
        print(
            json.dumps(
                {
                    "ledger": _display(path),
                    "schema": SCHEMA_V2 if is_v2(data) else "v1",
                    "ok": not failed,
                    "strict": ns.strict,
                    "against": against,
                    "errors": [f.as_json() for f in errors],
                    "warnings": [f.as_json() for f in warnings],
                    "leftover_needs_review": leftover if ns.strict else [],
                },
                indent=2,
            )
        )
        return EXIT_FINDINGS if failed else EXIT_OK
    for finding in findings:
        print(finding.line())
    if ns.strict and leftover:
        print("leftover needs_review:")
        for node_id in leftover:
            print(f"  - {node_id}")
    print(f"validate{' --strict' if ns.strict else ''}: {'failed' if failed else 'ok'}")
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_impact(ns: argparse.Namespace) -> int:
    """Print the blast radius of nodes, of paths, or of a diff (with its unmapped paths)."""
    path = default_ledger_path()
    if ns.from_diff is not None:
        result = impact_from_diff(path, ns.from_diff)
        print(f"changed: {_list_text(result['changed'])}")
        print(f"nodes: {_list_text(result['nodes'])}")
        print(f"blast_radius: {_list_text(result['blast_radius'])}")
        print(f"unmapped: {_list_text(result['unmapped'])}")
        return EXIT_OK
    _, data = load_ledger(path)
    if ns.nodes is not None:
        ids = split_list(ns.nodes) or []
        unknown = [node_id for node_id in ids if node_id not in node_index(data)]
        if unknown:
            raise UsageError(f"impact --nodes: no such node(s): {', '.join(unknown)}")
        print(f"nodes: {_list_text(sorted(set(ids)))}")
        print(f"blast_radius: {_list_text(blast_radius(data, ids))}")
        return EXIT_OK
    ids, unmapped = map_paths(data, split_list(ns.files) or [])
    print(f"nodes: {_list_text(ids)}")
    print(f"blast_radius: {_list_text(blast_radius(data, ids))}")
    print(f"unmapped: {_list_text(unmapped)}")
    return EXIT_OK


def cmd_ensure_node(ns: argparse.Namespace) -> int:
    """Create or update one node; only the given fields change."""
    args = {
        "id": ns.id,
        "dimension": ns.dimension,
        "parent": ns.parent,
        "member_of": split_list(ns.member_of),
        "depends_on": split_list(ns.depends_on),
        "files": split_list(ns.files),
        "associated_file": ns.associated_file,
        "spec_refs": split_list(ns.spec_refs),
        "description": None,
        "allow_shared": ns.allow_shared,
    }
    if ns.from_spec:
        spec = read_spec_node(Path(ns.from_spec))
        for key in ("id", "parent"):
            if spec[key] is None:
                continue
            if args[key] is not None and args[key] != spec[key]:
                raise UsageError(
                    f"--from-spec gives {key} {spec[key]!r}, but the flag gives {args[key]!r}"
                )
            args[key] = spec[key]
        if spec["files"] is not None:
            if args["files"] is not None:
                raise UsageError(FILES_AND_FROM_SPEC)
            # A slice touch is merged only as an exact path; a glob would claim other nodes' files.
            args["add_files"] = []
            for touch in spec["files"]:
                if TOUCH_GLOB_CHARS & set(touch):
                    print(
                        f"ensure-node: skipped glob touch {touch}; set globs with --files",
                        file=sys.stderr,
                    )
                else:
                    args["add_files"].append(touch)
    if not args["id"]:
        raise UsageError("ensure-node needs --id (or --from-spec)")
    if ns.description_file:
        try:
            args["description"] = Path(ns.description_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"--description-file: cannot read it ({exc})") from None
    return _submit_cli("ensure-node", args)


def cmd_set_edges(ns: argparse.Namespace) -> int:
    """Add or remove depends_on edges, or replace member_of or implements."""
    args = {
        "id": ns.id,
        "add_depends_on": split_list(ns.add_depends_on),
        "remove_depends_on": split_list(ns.remove_depends_on),
        "set_member_of": split_list(ns.set_member_of),
        "set_implements": split_list(ns.set_implements),
    }
    return _submit_cli("set-edges", args)


def cmd_review_template(ns: argparse.Namespace) -> int:
    """Print the nodes awaiting review and the patch schema a sub-agent answers with."""
    statuses = split_list(ns.status) or []
    bad = [status for status in statuses if status not in STATUSES]
    if bad or not statuses:
        raise UsageError(f"--status takes a comma-separated subset of {', '.join(STATUSES)}")
    path = default_ledger_path()
    _, data = load_ledger(path)
    document = {"ledger": _display(path), **review_template(data, statuses)}
    if ns.json:
        print(json.dumps(document, indent=2, default=str))
    else:
        for node in document["nodes"]:
            print(f"{node['id']} ({node['status']}): {node['associated_file']}")
        print(f"{len(document['nodes'])} node(s); add --json for the template and patch schema")
    return EXIT_OK


def cmd_apply_review(ns: argparse.Namespace) -> int:
    """Apply a sub-agent's JSON review patch through the non-regressive writer."""
    try:
        patch = json.loads(Path(ns.patch).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"--patch {ns.patch}: cannot read it ({exc})") from None
    except ValueError as exc:
        raise UsageError(f"--patch {ns.patch}: not JSON ({exc})") from None
    return _submit_cli("apply-review", {"patch": patch, "slice": ns.slice})


def cmd_mark(ns: argparse.Namespace) -> int:
    """Set the status of the given nodes."""
    return _submit_cli("mark", {"status": ns.status, "ids": split_list(ns.ids)})


def cmd_remove_node(ns: argparse.Namespace) -> int:
    """Remove a node without inbound edges and leave a tombstone in removed_nodes."""
    return _submit_cli("remove-node", {"id": ns.id, "reason": ns.reason, "slice": ns.slice})


def cmd_apply_outbox(ns: argparse.Namespace) -> int:
    """Replay an outbox's hyper_ledger entries onto the ledger (all or nothing)."""
    if os.environ.get(OUTBOX_ENV):
        raise UsageError(f"apply-outbox writes the ledger; unset {OUTBOX_ENV} to run it")
    base = Path(ns.base_file) if ns.base_file else None
    report, result = apply_outbox(default_ledger_path(), Path(ns.dir), base)
    for line in report:
        print(line)
    _print_write(result)
    return EXIT_OK


def _refuse_outbox(command: str) -> None:
    if os.environ.get(OUTBOX_ENV):
        raise UsageError(
            f"{command} rewrites the whole ledger; unset {OUTBOX_ENV} to run it (or add --dry-run)"
        )


def cmd_migrate_v2(ns: argparse.Namespace) -> int:
    """Convert the ledger to hacf.ledger/v2; print the summary and the diff."""
    if not ns.dry_run:
        _refuse_outbox("migrate-v2")
    summary, diff, result = migrate_v2(default_ledger_path(), dry_run=ns.dry_run)
    if summary:
        print("migrate-v2 summary:")
        for line in summary:
            print(f"  {line}")
    print(diff, end="")
    _print_write(result)
    return EXIT_OK


def cmd_repair_text(ns: argparse.Namespace) -> int:
    """Fold the plain description scalars that break the YAML; print the diff."""
    if not ns.dry_run:
        _refuse_outbox("repair-text")
    diff, result = repair_text(default_ledger_path(), dry_run=ns.dry_run)
    print(diff, end="")
    _print_write(result)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser of the frozen CLI contract (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_ledger.py",
        description="Validated, locked writer of spec/compiled/architecture.yml.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name: str, handler: Callable[[argparse.Namespace], int], text: str) -> Any:
        p = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        p.set_defaults(handler=handler)
        return p

    p = command("init", cmd_init, "write an empty hacf.ledger/v2 ledger")
    p.add_argument("path", nargs="?", help=f"ledger path (default: <top level>/{LEDGER_REL})")

    command("show", cmd_show, "print the ledger summary")

    p = command("validate", cmd_validate, "validate the ledger")
    p.add_argument("--strict", action="store_true", help="fail on any finding or needs_review")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--against-ref", metavar="REF", help="report ids and keys dropped vs REF")
    group.add_argument("--against-file", metavar="F", help="report ids and keys dropped vs F")

    p = command("impact", cmd_impact, "print a blast radius")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--nodes", metavar="a,b", help="start from these node ids")
    group.add_argument("--files", metavar="p1,p2", help="start from the nodes covering paths")
    group.add_argument("--from-diff", metavar="<rev-range>", help="start from a diff's paths")

    p = command("ensure-node", cmd_ensure_node, "create or update one node")
    p.add_argument("--id", help="node id (or from --from-spec)")
    p.add_argument("--dimension", choices=DIMENSIONS, help="required for a new node")
    p.add_argument("--parent", help="Layer/System parent (member_of) or Module parent (implements)")
    p.add_argument("--member-of", metavar="a,b", help="add member_of edges")
    p.add_argument("--depends-on", metavar="a,b", help="add depends_on edges")
    p.add_argument("--files", metavar="a,b", help="replace the node's files globs")
    p.add_argument("--associated-file", help="set associated_file")
    p.add_argument("--description-file", help="set the description from this file")
    p.add_argument("--spec-refs", metavar="a,b", help="add these dot-IDs to spec_refs")
    p.add_argument("--from-spec", metavar="<file>", help="legacy MiniPRD or v6 SPEC.md")
    p.add_argument(
        "--allow-shared",
        action="store_true",
        help="with --files: keep a new path another node already owns (default: refuse, "
        "exit 2); --from-spec always skips such a path with a warning. A glob that overlaps "
        "another node's different glob is not detected",
    )

    p = command("set-edges", cmd_set_edges, "edit one node's edges (cycle-checked)")
    p.add_argument("--id", required=True, help="node id")
    p.add_argument("--add-depends-on", metavar="a,b", help="add depends_on edges")
    p.add_argument("--remove-depends-on", metavar="a,b", help="remove depends_on edges")
    p.add_argument("--set-member-of", metavar="a,b", help="replace member_of")
    p.add_argument("--set-implements", metavar="a,b", help="replace implements")

    p = command("review-template", cmd_review_template, "list nodes for a review patch")
    p.add_argument("--status", default="needs_review,dirty", help="statuses to list")
    p.add_argument("--json", action="store_true", help="print the template as JSON")

    p = command("apply-review", cmd_apply_review, "apply a JSON review patch")
    p.add_argument("--patch", required=True, metavar="<json>", help="patch file")
    p.add_argument("--slice", required=True, metavar="<id>", help="slice id, e.g. S01a")

    p = command("mark", cmd_mark, "set node statuses")
    group = p.add_mutually_exclusive_group(required=True)
    for flag, status in (
        ("--dirty", "dirty"),
        ("--needs-review", "needs_review"),
        ("--clean", "clean"),
    ):
        group.add_argument(flag, dest="status", action="store_const", const=status)
    p.add_argument("--ids", required=True, metavar="a,b", help="node ids")

    p = command("remove-node", cmd_remove_node, "remove a node and leave a tombstone")
    p.add_argument("id", help="node id")
    p.add_argument("--reason", required=True, metavar="R", help="why it is removed")
    p.add_argument("--slice", metavar="SID", help="the slice removing it")

    p = command("apply-outbox", cmd_apply_outbox, "replay an outbox onto the ledger")
    p.add_argument("dir", help="outbox directory")
    p.add_argument("--base-file", metavar="<ledger>", help="refuse entries on nodes changed since")

    p = command("migrate-v2", cmd_migrate_v2, "convert a v1 or mixed ledger to hacf.ledger/v2")
    p.add_argument("--dry-run", action="store_true", help="print the summary and diff only")

    p = command("repair-text", cmd_repair_text, "fold plain description scalars that break YAML")
    p.add_argument("--dry-run", action="store_true", help="print the diff only")
    return parser


def _report(exc: LedgerError) -> None:
    print(str(exc), file=sys.stderr)
    for line in exc.details:
        print(f"  {line}", file=sys.stderr)


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
    except LedgerError as exc:
        _report(exc)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
