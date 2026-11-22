#!/usr/bin/env python3
"""hyper_lore.py — the single validated writer of the HACF lore hypergraph.

The graph is `lore_hypergraph.yml` (`schema: hacf.lore/v1`) at the top level of the git work
tree of the working directory, never of the directory holding this file; the bodies it points
at live under `lore/`. Normative design: HACF's v0.6.0 design notes (not shipped): the lore
hypergraph, progress/proof/notes and lore sections.

CLI:
    validate [--strict] [--json]
    show <ID>
    list [--status S] [--stale] [--json] [--with-commit]
    graph --json
    propose --type decision|story|canon --title T --body-file F [--anchors-components a,b]
            [--anchors-files g1,g2] [--anchors-symbols s1,s2] [--planned-components a,b]
            [--planned-files g1,g2] [--relates-to ids] [--supersedes ids] [--source S]
            [--context C]
    note (--mission <dir> | --legacy --source <MiniPRD path>) --text T [--kind K]
         [--agent A] [--slice S] [--anchors a,b]
    stale [--nodes a,b] [--files g1,g2] --reason R
    resolve-anchors
    approval-request <ID>
    approval-cancel <ID>
    approve | reject | retire | reopen <ID> --nonce N --warrant-file F
    review <ID> --still-valid --nonce N --warrant-file F
    apply-outbox <dir>
    sync
    index
    init
    import --drafts [<path> ...] | --adr-dirs <dir> [<dir> ...] [--context C]
    merge-driver <base> <ours> <theirs> <path>        (git's hacf-lore driver: %O %A %B %P)
    merge-generated <base> <ours> <theirs> <path>     (git's hacf-generated driver)
    renumber <old> <new>
    install-drivers

Exit codes: 0 ok, 1 validation, 2 usage, 3 lock, 4 refused, 5 conflict.

Write protocol (R22.1): every graph write takes the fcntl lock
`$(git rev-parse --git-common-dir)/hacf/locks/lore.lock` before the graph is opened (a bounded
wait, then exit 3), mutates it in memory, validates it (a write that adds an error, deletes a
node, rewrites history or lowers an id counter exits 1 with nothing written) and replaces the
file compare-and-swap (temp file, fsync, os.replace, directory fsync, file mode kept; a change
by an unlocked writer since the read exits 5). With HACF_OUTBOX set, propose, note and stale
write one entry `<6-digit sequence>-<op>.json` = `{"tool": "hyper_lore", "op", "args"}`
(hyper_ledger's outbox format) and take no lock; resolve-anchors, sync, approval-request and
the gated verbs are refused (exit 4). `apply-outbox` replays only propose, note and stale.

Semantics:
- No graph: validate, show, list and graph print "no lore graph (lore_hypergraph.yml missing)"
  and exit 0; stale, resolve-anchors, sync and stale_from_impact() do nothing; propose and note
  create an empty hacf.lore/v1 graph on first use.
- `init` (R49) creates the lore store of a fresh project: holding lore.lock it writes the empty
  hacf.lore/v1 graph compare-and-swap, then generates lore/MAP.md and lore/CANON.md as `index`
  does, and prints the files written. When lore_hypergraph.yml exists in any form (a graph, an
  empty or unparsable file, a symlink) it exits 4 and writes nothing. hyper_scaffold.py apply
  runs it when the graph is missing.
- validate is read-only. Anchors to component ids tombstoned in the ledger's `removed_nodes`,
  and files anchors that match no existing path, are stale warnings for PROPOSED and CANON
  nodes; an anchor to an id that is neither a ledger node nor tombstoned is an error. A missing
  or unreadable ledger leaves component anchors unchecked, with a warning. Pointers must be
  repo-relative paths under `lore/`, not symlinks, and exist; a sha mismatch is a warning
  (`content_changed_since_approval` for CANON). A CANON node without `lore/canon/<ID>.md` is
  an error (R8). CANON nodes that are stale, or would be marked stale, are listed for the
  gated stale review. Exit 0 with only warnings, 1 on errors; --strict fails on warnings too.
- stale, stale_from_impact() and sync mark only PROPOSED and CANON nodes, write only when a
  node's stale state changes and keep the first reason. Stale is cleared only by a gated
  `review --still-valid` (CANON) or by supersession; approve keeps it.
- sync recomputes `content_sha256` of PROPOSED nodes from their pointer files and marks CANON
  nodes stale for a sha mismatch (content_changed_since_approval) or a removed anchor.
- resolve-anchors moves planned components that are ledger nodes, and planned files globs
  that match an existing path, into `anchors`.
- Bodies: decision and story bodies live in `lore/ADRs/` and `lore/STORIES/` (the pointer);
  canon bodies wait in `lore/proposed/<ID>.md`. On approval a canon body moves to the immutable
  `lore/canon/<ID>.md` (the pointer follows); a decision or story keeps its file and approval
  writes a byte-identical copy to `lore/canon/<ID>.md`, so every node that has held CANON has
  an immutable canon file. Nothing writes a canon file after it exists (renumber renames one
  byte-for-byte).
- note line: `- <YYYY-MM-DD> [<kind>] (agent:<A>, slice <S>) <text> anchors: <a, b|none>`.
  `--mission` appends it under `## Notes` of `<dir>/NOTES.md` (which must exist); `--legacy`
  appends it under `## Field Notes (legacy)` of `.agents/memory/activeContext.md` (the section
  is created at the end when missing; S is the --source path) and refuses any change to the
  bytes from `## v0.6.0 Bootstrap Operating Procedure (read first)` through
  `<!-- END v060-completed -->`. Both take lore.lock and write compare-and-swap. --kind
  defaults to field-note, --agent to unknown, --slice to $HACF_SLICE, else unknown. Notes never
  go to spec/process/.

Gate (R6, R7, R23; D7). The guarantee is procedural, not a security boundary: a deliberately
forged ticket and a direct Python write to lore_hypergraph.yml remain possible.
- `approval-request <ID>` prints the exact hashed body (the pointer file), its sha256, the nonce
  and every id the approval would supersede (the CANON ids in the node's `supersedes`), retire
  (the node itself when it is CANON) or re-anchor (always empty: no hacf.lore/v1 verb changes
  another node's anchors). It writes `.agents/state/lore/pending/<ID>.json`:
      {"schema": "hacf.lore-pending/v1", "id", "title", "content_sha256", "nonce",
       "created_at", "expires_at", "affects": {"supersedes": [..], "retires": [..],
       "reanchors": [..]}}
  with `nonce` = secrets.token_hex(16) and `expires_at` = `created_at` + 30 minutes. Only one
  unexpired request may be pending; expired and unreadable records are cleared automatically.
  It is refused under HACF_OUTBOX and HACF_AUTONOMOUS=1. `approval-cancel <ID>` deletes it.
- Gated verbs: approve (PROPOSED -> CANON), reject (PROPOSED -> REJECTED, node and file kept),
  retire (CANON -> RETIRED), reopen (REJECTED -> PROPOSED) and `review --still-valid` (clears
  stale on a stale CANON node). Every other (status, verb) pair exits 4 with nothing written.
  Before any write each one requires: HACF_AUTONOMOUS unset (or 0) and HACF_OUTBOX unset; one
  <ID>; the pending record of that id with the same nonce, unexpired, whose sha equals the
  pointer file's current sha; a --warrant-file that is non-empty and differs from the title;
  git config user.name and user.email (approved_by = "Name <email>": attribution, not
  identity, never from arguments). Then a ticket, else a TTY confirmation:
- Ticket: a file under `<repo>/.agents/state/lore/tickets/` (written by the PreToolUse hook)
  whose JSON is
      {"schema": "hacf.lore-ticket/v1", "verb", "id", "nonce", "created_at", "expires_at"}
  with `verb` one of approve|reject|retire|reopen|review and `expires_at` at most 120 s after
  `created_at`. It must match this invocation's verb, id and nonce and be unexpired; it is
  deleted on use (method `AskUserQuestion+PreToolUse-ask+ticket`). Expired or malformed
  tickets never match.
- Without a matching ticket the verb proceeds only when stdin and stdout are TTYs and the user
  types the node id at the prompt (method `tty-confirm`); otherwise exit 4. The script never
  tries to learn Claude Code's permission mode (R7).
- Timestamps are written with `+00:00` and parsed through `_runtime.parse_ts()`, which also
  accepts `Z` (R19).

Index (S05b; R8, R20.6, R26):
- `lore/MAP.md` is a hand-written intro plus the generated region `lore-map` (markers
  `<!-- BEGIN HACF GENERATED: lore-map -->` / `<!-- END HACF GENERATED: lore-map -->`, counted
  only as whole lines at column 0 outside fenced code; duplicate, nested or unbalanced HACF
  markers exit 5 with nothing written; EOL style, BOM and final newline kept). The region lists
  Canon, Proposed, Stale, Graveyard (SUPERSEDED and RETIRED nodes, then the newest 20 REJECTED
  nodes with their warrants, then ``full list: `hyper_lore.py list --status REJECTED` ``),
  Stories and "Where things live", sorted by id; it is built from the graph alone.
- `lore/CANON.md` is, in id order, `<!-- lore:<ID> -->`, the bytes of `lore/canon/<ID>.md` (a
  newline added when they lack one) and `<!-- /lore:<ID> -->` for every CANON node; nothing else.
- `index` writes MAP.md's region (creating MAP.md with a one-line intro) and CANON.md where they
  differ, clears `.agents/state/lore/needs-index`, then runs resolve-anchors. Every graph write
  regenerates both files afterwards when `lore/MAP.md` exists; a failure there leaves the write
  in place and touches needs-index. validate reports a stale MAP.md region, a CANON.md that
  differs from the rebuild (or is missing while MAP.md exists), unbalanced or duplicate CANON.md
  markers and a needs-index file as errors naming `hyper_lore.py index`.

Importers, merges and maintenance (S05b; R10, R22.6, R25.3):
- `import --drafts` reads files under lore/ADRs/ and lore/STORIES/ whose frontmatter has
  `hacf_lore_draft: 1` (or the given paths) without changing them: one PROPOSED node each,
  pointer = the file, content_sha256 = its bytes; id_counters rise to the highest imported
  number; then resolve-anchors. The same id and sha again is skipped; another sha or pointer
  exits 5.
- `import --adr-dirs` moves each `NNNN-slug.md` body verbatim to `lore/ADRs/ADR-NNNN-slug.md`
  (a taken id is renumbered from id_counters), keeps `legacy: {source, status}`, maps statuses
  (proposed and accepted -> PROPOSED, deprecated -> RETIRED, "superseded by N" -> SUPERSEDED
  when N was imported from the same directory, anything else -> PROPOSED), writes
  `lore/canon/<ID>.md` for RETIRED and SUPERSEDED imports, leaves a redirect stub at each old
  path and a stub README.md in each source directory (an existing README.md is kept).
- `merge-driver` merges lore_hypergraph.yml by id: union of nodes, 3-way merge of each field
  (list fields as sets), union of history ordered by `at`, the maximum id_counters. A status
  conflict, an id added on both sides with another pointer or sha, or any other double change
  exits 1 with a report naming `renumber`, leaving ours.
- `merge-generated` rebuilds lore/CANON.md, and MAP.md's region after a `git merge-file` of the
  hand-written parts, from the merged graph, which it computes from HEAD, the merge head
  (GITHEAD_<sha> or MERGE_HEAD) and their merge base, reading canon files from either side.
  Without that (rebase, cherry-pick, octopus, a graph conflict) it keeps ours, touches
  `.agents/state/lore/needs-index` and exits 0.
- `renumber <old> <new>` refuses an existing <new> (exit 5), renames the node, rewrites other
  nodes' relates_to, supersedes and superseded_by and `lore_refs` in missions/**/SPEC.md
  (hyper_spec's splice, compare-and-swap, under spec.lock), renames the pointer and canon files
  byte-for-byte, and reports commit messages and lore bodies naming <old> without rewriting.
- `install-drivers` writes repo-local `git config --local merge.hacf-lore.*` and
  `merge.hacf-generated.*` and the `merge-drivers` managed block of
  `$(git rev-parse --git-common-dir)/info/attributes`; idempotent; never a .gitattributes.
- index, init, import, renumber and install-drivers are refused under HACF_OUTBOX (exit 4).
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple

import _runtime
import hyper_ledger
import hyper_openrig
import hyper_scope
import hyper_spec

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2
EXIT_LOCK = 3
EXIT_REFUSED = 4
EXIT_CONFLICT = 5

GRAPH_REL = "lore_hypergraph.yml"
SCHEMA = "hacf.lore/v1"
PENDING_SCHEMA = "hacf.lore-pending/v1"
TICKET_SCHEMA = "hacf.lore-ticket/v1"
PENDING_REL = ".agents/state/lore/pending"
TICKETS_REL = ".agents/state/lore/tickets"
STATE_REL = ".agents/state"
ACTIVE_CONTEXT_REL = ".agents/memory/activeContext.md"
NO_GRAPH = "no lore graph (lore_hypergraph.yml missing)"

LOCK_NAME = "lore.lock"
LOCK_TIMEOUT = 30.0  # seconds a write waits for lore.lock before exit 3
LOCK_POLL = 0.05
GIT_TIMEOUT = 60
PENDING_TTL = timedelta(minutes=30)
TICKET_TTL = timedelta(seconds=120)
CLOCK_SKEW = timedelta(seconds=5)  # a ticket created this far in the future still counts

OUTBOX_ENV = hyper_ledger.OUTBOX_ENV
OUTBOX_TOOL = "hyper_lore"
OUTBOX_OPS = ("propose", "note", "stale")
AUTONOMOUS_ENV = "HACF_AUTONOMOUS"
SLICE_ENV = "HACF_SLICE"
RUN_ID_ENV = "HACF_RUN_ID"

TYPE_PREFIX = {"decision": "ADR", "story": "STORY", "canon": "CANON"}
PREFIX_TYPE = {prefix: kind for kind, prefix in TYPE_PREFIX.items()}
TYPE_DIRS = {"decision": "lore/ADRs", "story": "lore/STORIES"}
PROPOSED_DIR = "lore/proposed"
CANON_DIR = "lore/canon"
STATUSES = ("PROPOSED", "CANON", "REJECTED", "SUPERSEDED", "RETIRED")
LIVE = ("PROPOSED", "CANON")  # the statuses stale marking applies to
ORIGINS = ("user-decision", "design-choice", "agent", "import")
TICKET_METHOD = "AskUserQuestion+PreToolUse-ask+ticket"
TTY_METHOD = "tty-confirm"
METHODS = (TICKET_METHOD, TTY_METHOD)

NODE_KEYS = (
    "id",
    "type",
    "title",
    "status",
    "origin",
    "context",
    "pointer",
    "content_sha256",
    "anchors",
    "planned",
    "relates_to",
    "supersedes",
    "superseded_by",
    "stale",
    "stale_reason",
    "legacy",
    "provenance",
    "approval",
    "history",
)
ANCHOR_KEYS = ("components", "files", "symbols")
PLANNED_KEYS = ("components", "files")
PROVENANCE_KEYS = ("created_by", "created_at", "source", "session_id", "run_id")
APPROVAL_KEYS = (
    "approved_by",
    "approved_at",
    "warrant",
    "warrant_sha256",
    "method",
    "content_sha256",
    "nonce",
)
HISTORY_KEYS = ("at", "from", "to", "by", "warrant", "ref")
TOP_KEYS = ("schema", "id_counters", "nodes")

# Gated verb -> (required status, new status). review also requires the node to be stale.
TRANSITIONS = {
    "approve": ("PROPOSED", "CANON"),
    "reject": ("PROPOSED", "REJECTED"),
    "retire": ("CANON", "RETIRED"),
    "reopen": ("REJECTED", "PROPOSED"),
    "review": ("CANON", "CANON"),
}

ID_RE = re.compile(r"^(ADR|STORY|CANON)-(\d{4,})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}$")  # --agent and --slice values
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SLUG_MAX = 60

NOTES_HEADING = "## Notes"
LEGACY_NOTES_HEADING = "## Field Notes (legacy)"
PROTECTED_START = "## v0.6.0 Bootstrap Operating Procedure (read first)"
PROTECTED_END = "<!-- END v060-completed -->"
BODY_BEGIN = "----- BEGIN HASHED BODY -----"
BODY_END = "----- END HASHED BODY -----"

# Index (S05b)
MAP_REL = "lore/MAP.md"
CANON_MD_REL = "lore/CANON.md"
MAP_REGION = "lore-map"
MAP_INTRO = "# Lore map\n"  # the one-line intro of a MAP.md that index creates
NEEDS_INDEX_REL = ".agents/state/lore/needs-index"
GRAVEYARD_CAP = 20  # newest REJECTED nodes MAP.md lists (R20.6)
FULL_LIST_LINE = "full list: `hyper_lore.py list --status REJECTED`"
INDEX_HINT = "run `python3 .agents/scripts/hyper_lore.py index`"
SUMMARY_MAX = 200  # characters of a title, warrant or stale reason in MAP.md
GENERATED_NOTE = (
    "Generated by `hyper_lore.py index` from `lore_hypergraph.yml`; "
    "edits inside this region are overwritten."
)
WHERE_THINGS_LIVE = (
    "- `lore_hypergraph.yml`: the lore graph (`hacf.lore/v1`); only `hyper_lore.py` writes it",
    "- `lore/ADRs/` and `lore/STORIES/`: decision and story bodies (each node's pointer)",
    "- `lore/proposed/`: canon bodies waiting for approval",
    "- `lore/canon/`: approved bodies, immutable after approval",
    "- `lore/CANON.md`: every CANON body in id order, generated by `hyper_lore.py index`",
    "- `SPEC.md` and `missions/`: the project spec and its missions",
    "- `.agents/memory/activeContext.md`: what is being worked on now",
)
CANON_MARK_RE = re.compile(r"^<!-- (/?)lore:([A-Za-z]+-\d+) -->$")
LINK_SAFE_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Importers
DRAFT_KEY = "hacf_lore_draft"
LEGACY_ADR_RE = re.compile(r"^(\d{1,9})-(.+)\.md$")
REDIRECT_MARK = "<!-- hacf-lore-redirect: "
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
STATUS_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?status(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*?)\s*$",
    re.IGNORECASE,
)

# Merge drivers and install-drivers (R10)
ATTRIBUTES_BLOCK = "merge-drivers"
ATTRIBUTE_LINES = (
    "lore_hypergraph.yml merge=hacf-lore",
    "lore/MAP.md merge=hacf-generated",
    "lore/CANON.md merge=hacf-generated",
)
DRIVER_CONFIG = (
    ("merge.hacf-lore.name", "HACF lore graph: by-id merge with a union of history"),
    ("merge.hacf-lore.driver", "python3 .agents/scripts/hyper_lore.py merge-driver %O %A %B %P"),
    ("merge.hacf-generated.name", "HACF generated lore files: regenerated, never one side kept"),
    (
        "merge.hacf-generated.driver",
        "python3 .agents/scripts/hyper_lore.py merge-generated %O %A %B %P",
    ),
)
HASH_MARKER_RE = re.compile(
    r"^# (BEGIN|END) HACF (MANAGED BLOCK|GENERATED): ([a-z][a-z0-9-]{0,63})$"
)
GITHEAD_RE = re.compile(r"^GITHEAD_([0-9a-f]{40}|[0-9a-f]{64})$")
MERGE_HINT = (
    "resolve a status conflict in lore_hypergraph.yml by hand; resolve an id collision with "
    "`python3 .agents/scripts/hyper_lore.py renumber <old> <new>` on one side; then run "
    "`hyper_lore.py validate`"
)

Finding = hyper_ledger.Finding


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LoreError(Exception):
    """A failure that ends a command; `code` is its exit code."""

    code = EXIT_USAGE

    def __init__(self, message: str, details: list[str] | tuple[str, ...] = ()) -> None:
        """Keep the one-line message and the detail lines printed under it."""
        super().__init__(message)
        self.details = list(details)


class UsageError(LoreError):
    """Bad arguments, an unknown node or a missing file (exit 2)."""

    code = EXIT_USAGE


class ValidationFailed(LoreError):
    """The graph does not parse, or a write would add an error (exit 1)."""

    code = EXIT_VALIDATION


class LockTimeout(LoreError):
    """lore.lock was not free within the bounded wait (exit 3)."""

    code = EXIT_LOCK


class Refused(LoreError):
    """A gate precondition, a forbidden transition or a mode refusal (exit 4)."""

    code = EXIT_REFUSED


class Conflict(LoreError):
    """A file changed since it was read, or a target file already exists (exit 5)."""

    code = EXIT_CONFLICT


# ---------------------------------------------------------------------------
# Repository, paths, lock and small helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run git with an argv list (never a shell) and capture its output; never raises on exit."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 127, "", str(exc))


def repo_top() -> Path:
    """Return the work-tree top level of the working directory (never this file's)."""
    top = hyper_ledger.repo_toplevel(Path.cwd())
    if top is None:
        raise UsageError("not inside a git work tree; run hyper_lore.py from the repository")
    return top.resolve()


def graph_path(top: Path) -> Path:
    """Return `<top>/lore_hypergraph.yml`."""
    return top / GRAPH_REL


def common_dir(top: Path) -> Path:
    """Return `$(git rev-parse --git-common-dir)`, shared by every worktree, as a real path."""
    proc = _git(["rev-parse", "--git-common-dir"], top)
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise UsageError(f"cannot find the git common dir of {top}")
    common = Path(text)
    return (common if common.is_absolute() else top / common).resolve()


def lock_path(top: Path) -> Path:
    """Return `<git common dir>/hacf/locks/lore.lock`, shared by every worktree (R22.1)."""
    return common_dir(top) / "hacf" / "locks" / LOCK_NAME


@contextlib.contextmanager
def lore_lock(top: Path, timeout: float | None = None) -> Iterator[Path]:
    """Hold lore.lock; raise LockTimeout after `timeout` (LOCK_TIMEOUT) seconds.

    Callers take it before they open the files they write. The lock directory and
    `.agents/state/` are created on first use.
    """
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    lock = lock_path(top)
    lock.parent.mkdir(parents=True, exist_ok=True)
    (top / STATE_REL).mkdir(parents=True, exist_ok=True)
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
                        f"timed out after {timeout:g}s waiting for the lore lock {lock}"
                    ) from None
                time.sleep(LOCK_POLL)
        yield lock
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _now() -> datetime:
    """Return the current UTC time (to the second) through _runtime's clock."""
    return _runtime.parse_ts(_runtime.utc_now_iso())


def _parse_aware(value: Any) -> datetime | None:
    """Parse a timestamp with an offset (`Z` or `+00:00`); None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = _runtime.parse_ts(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _read_text(path: Path) -> str:
    """Return a file's text exactly (UTF-8, no newline translation)."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError(f"{path} is not UTF-8 text") from None


def _read_optional(path: Path) -> str | None:
    """Return a file's exact text, or None when it does not exist."""
    try:
        return _read_text(path)
    except FileNotFoundError:
        return None


def _write_plain(path: Path, text: str) -> None:
    """Write text to a file git handed a merge driver (%A), with no newline translation."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _git_blob(top: Path, rev: str, rel: str) -> str | None:
    """Return the exact text of `<rev>:<rel>` (UTF-8, no newline translation), or None."""
    try:
        proc = subprocess.run(
            ["git", "cat-file", "blob", f"{rev}:{rel}"],
            cwd=str(top),
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


@contextlib.contextmanager
def _spec_errors() -> Iterator[None]:
    """Re-raise hyper_spec's errors as this module's, keeping the exit code."""
    try:
        yield
    except hyper_spec.SpecError as exc:
        kind = {
            EXIT_VALIDATION: ValidationFailed,
            EXIT_LOCK: LockTimeout,
            EXIT_REFUSED: Refused,
            EXIT_CONFLICT: Conflict,
        }.get(getattr(exc, "code", EXIT_USAGE), UsageError)
        raise kind(str(exc)) from None


def _printable(text: str) -> str:
    """Return text without ANSI sequences and control characters, for prompts."""
    return CONTROL_RE.sub("", ANSI_RE.sub("", text))


def _single_line(value: str, what: str) -> str:
    """Return value stripped; refuse an empty value or one holding control characters."""
    value = value.strip()
    if not value:
        raise UsageError(f"{what} is empty")
    if CONTROL_RE.search(value):
        raise UsageError(f"{what} must be one line without control characters")
    return value


def _split(value: str | None) -> list[str]:
    """Split a comma-separated flag value into its non-empty, de-duplicated items."""
    if not value:
        return []
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _cas_write(path: Path, original: str | None, text: str) -> None:
    """Write text compare-and-swap (hyper_openrig.cas_write); a change is a Conflict (exit 5)."""
    try:
        hyper_openrig.cas_write(path, original, text)
    except hyper_openrig.OpenRigError as exc:
        raise Conflict(str(exc)) from None


def _symlink_in(top: Path, rel: str) -> str | None:
    """Return the first path component of rel (under top) that is a symlink, or None."""
    current = top
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            return str(current.relative_to(top).as_posix())
    return None


def _pointer_problem(rel: Any) -> str | None:
    """Return why a pointer is not a plain repo-relative path under lore/, or None."""
    if not isinstance(rel, str) or not rel:
        return "is not a non-empty string"
    if rel.startswith("/") or "\\" in rel:
        return "is not a repo-relative posix path"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "has an empty, . or .. segment"
    if parts[0] != "lore" or len(parts) < 2:
        return "is not under lore/"
    if set("*?[]") & set(rel):
        return "holds glob characters"
    return None


def _ensure_lore_dir(top: Path, rel_dir: str) -> Path:
    """Create a directory under lore/ (refusing one reached through a symlink); return it."""
    link = _symlink_in(top, rel_dir)
    if link is not None:
        raise Refused(f"{link} is a symlink; hyper_lore.py writes only regular files")
    directory = top / rel_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


# ---------------------------------------------------------------------------
# Graph load and dump
# ---------------------------------------------------------------------------


def empty_graph() -> dict:
    """Return a new, empty hacf.lore/v1 graph."""
    return {"schema": SCHEMA, "id_counters": {"ADR": 0, "STORY": 0, "CANON": 0}, "nodes": []}


def parse_graph(text: str, label: str = GRAPH_REL) -> dict:
    """Parse graph text with yaml.safe_load; raise ValidationFailed naming the line."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        where = f"{label}:{mark.line + 1}:{mark.column + 1}" if mark is not None else label
        raise ValidationFailed(f"{where}: YAML parse error: {problem}") from None
    if not isinstance(data, dict):
        raise ValidationFailed(f"{label}: the graph root must be a mapping")
    return data


def read_graph(top: Path) -> tuple[str | None, dict | None]:
    """Return (text, data) of the graph, or (None, None) when there is no graph."""
    path = graph_path(top)
    if path.is_symlink():
        raise ValidationFailed(f"{GRAPH_REL} is a symlink; the graph must be a regular file")
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return None, None
    return text, parse_graph(text)


_DUMPER: Any = None


def _dumper() -> Any:
    """Return the SafeDumper subclass the graph is written with (built on first use)."""
    global _DUMPER
    if _DUMPER is None:

        class LoreDumper(yaml.SafeDumper):
            """SafeDumper that never writes anchors or aliases."""

            def ignore_aliases(self, data: Any) -> bool:
                """Write every shared object in full, never as an alias."""
                return True

        _DUMPER = LoreDumper
    return _DUMPER


def dump_graph(data: dict) -> str:
    """Return the graph as YAML with key order kept."""
    return yaml.dump(
        data, Dumper=_dumper(), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def nodes_of(data: dict | None) -> list[dict]:
    """Return the graph's node mappings (empty when absent)."""
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def node_map(data: dict | None) -> dict[str, dict]:
    """Map each node id to its node (the first one when an id repeats)."""
    index: dict[str, dict] = {}
    for node in nodes_of(data):
        if isinstance(node.get("id"), str):
            index.setdefault(node["id"], node)
    return index


def binding(node: dict) -> bool:
    """Return True for a binding node: CANON (stale or not), or a PROPOSED user decision."""
    status = node.get("status")
    return status == "CANON" or (status == "PROPOSED" and node.get("origin") == "user-decision")


def _anchor_list(node: dict, group: str, key: str) -> list[str]:
    value = node.get(group)
    items = value.get(key) if isinstance(value, dict) else None
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []


def _id_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Ledger and work-tree views (read-only)
# ---------------------------------------------------------------------------


class LedgerView(NamedTuple):
    """The component ids anchors are checked against."""

    ids: set[str] | None  # None when the ledger is missing or unreadable
    removed: set[str]
    problem: str | None


def read_ledger_view(top: Path) -> LedgerView:
    """Return the ledger's node ids and `removed_nodes` tombstones (v1 or v2)."""
    path = top / hyper_ledger.LEDGER_REL
    if not path.is_file():
        return LedgerView(None, set(), f"no ledger at {hyper_ledger.LEDGER_REL}")
    try:
        _, data = hyper_ledger.load_ledger(path)
    except hyper_ledger.LedgerError:
        return LedgerView(None, set(), f"the ledger {hyper_ledger.LEDGER_REL} does not load")
    return LedgerView(set(hyper_ledger.node_index(data)), hyper_ledger.removed_ids(data), None)


class TreeView:
    """The work tree's existing paths, listed once on first use."""

    def __init__(self, top: Path) -> None:
        """Remember the top level; nothing is listed yet."""
        self.top = top
        self._paths: list[str] | None = None

    def paths(self) -> list[str]:
        """Return the tracked and untracked (not ignored) paths that exist on disk."""
        if self._paths is None:
            proc = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], self.top)
            names = proc.stdout.split("\0") if proc.returncode == 0 else []
            self._paths = sorted(
                {name for name in names if name and os.path.lexists(self.top / name)}
            )
        return self._paths

    def matches(self, pattern: str) -> bool:
        """Return True when the glob matches an existing path (a literal path may be ignored)."""
        if not set("*?[]") & set(pattern) and os.path.lexists(self.top / pattern.rstrip("/")):
            return True
        return any(hyper_ledger.glob_match(pattern, path) for path in self.paths())


def _files_intersect(anchor: str, given: str) -> bool:
    """Return True when an anchor glob and a given path or glob overlap."""
    return (
        anchor == given
        or hyper_ledger.glob_match(anchor, given)
        or hyper_ledger.glob_match(given, anchor)
    )


# ---------------------------------------------------------------------------
# Validation (read-only)
# ---------------------------------------------------------------------------


class Report(NamedTuple):
    """What validate found: the findings and the CANON nodes for the gated stale review."""

    findings: list[Finding]
    review: list[tuple[str, list[str]]]  # (CANON id, reasons)


class _Checker:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, severity: str, rule: str, node: str, detail: str, message: str) -> None:
        self.findings.append(Finding(severity, rule, node, detail, message))

    def error(self, rule: str, node: str, detail: str, message: str) -> None:
        self.add("error", rule, node, detail, message)

    def warning(self, rule: str, node: str, detail: str, message: str) -> None:
        self.add("warning", rule, node, detail, message)


def _canon_rel(node_id: str) -> str:
    return f"{CANON_DIR}/{node_id}.md"


def _location_problem(node: dict, rel: str) -> str | None:
    """Return where a node's pointer must be when rel breaks that rule, else None.

    Canon bodies: lore/proposed/<ID>.md while PROPOSED or REJECTED, else lore/canon/<ID>.md
    (R8). Decisions and stories: lore/ADRs/<ID>[-<slug>].md and lore/STORIES/<ID>[-<slug>].md.
    """
    node_id, kind, status = node.get("id"), node.get("type"), node.get("status")
    if not isinstance(node_id, str) or kind not in TYPE_PREFIX:
        return None
    if kind == "canon":
        folder = PROPOSED_DIR if status in ("PROPOSED", "REJECTED") else CANON_DIR
        expected = f"{folder}/{node_id}.md"
        return None if rel == expected else expected
    folder, name = rel.rsplit("/", 1)
    named = name == f"{node_id}.md" or (name.startswith(f"{node_id}-") and name.endswith(".md"))
    return (
        None if folder == TYPE_DIRS[kind] and named else f"{TYPE_DIRS[kind]}/{node_id}[-<slug>].md"
    )


def _file_state(top: Path, rel: str) -> tuple[str, str | None]:
    """Return ("ok", sha) or (problem rule, None) for a pointer-like path under top."""
    if _symlink_in(top, rel) is not None:
        return "pointer_symlink", None
    path = top / rel
    if not path.is_file():
        return "pointer_missing", None
    return "ok", _sha_bytes(path.read_bytes())


def anchor_causes(node: dict, ledger: LedgerView, tree: TreeView) -> list[tuple[str, str, str]]:
    """Return (rule, detail, message) for each removed or unknown anchor of a node."""
    causes = []
    if ledger.ids is not None:
        for component in _anchor_list(node, "anchors", "components"):
            if component in ledger.ids:
                continue
            if component in ledger.removed:
                causes.append(
                    (
                        "anchor_removed",
                        component,
                        f"anchor to removed component {component} (tombstoned in the ledger)",
                    )
                )
            else:
                causes.append(
                    (
                        "anchor_unknown",
                        component,
                        f"anchor to unknown component {component} "
                        "(neither a ledger node nor tombstoned)",
                    )
                )
    for pattern in _anchor_list(node, "anchors", "files"):
        if hyper_ledger.glob_problem(pattern) is None and not tree.matches(pattern):
            causes.append(
                ("anchor_file_missing", pattern, f"files anchor {pattern} matches no existing path")
            )
    return causes


def stale_causes(node: dict, top: Path, ledger: LedgerView, tree: TreeView) -> list[str]:
    """Return why sync would mark a CANON node stale (sha mismatch, removed anchors)."""
    reasons = []
    pointer = node.get("pointer")
    rel = pointer.get("path") if isinstance(pointer, dict) else None
    shas = []
    if _pointer_problem(rel) is None:
        shas.append(_file_state(top, rel)[1])
    if node.get("type") != "canon" and isinstance(node.get("id"), str):
        shas.append(_file_state(top, _canon_rel(node["id"]))[1])
    expected = node.get("content_sha256")
    if any(sha is not None and sha != expected for sha in shas):
        reasons.append("content_changed_since_approval")
    for rule, detail, _ in anchor_causes(node, ledger, tree):
        if rule == "anchor_removed":
            reasons.append(f"anchor_removed:{detail}")
        elif rule == "anchor_file_missing":
            reasons.append(f"anchor_file_missing:{detail}")
    return reasons


def _check_top(out: _Checker, data: dict) -> None:
    if data.get("schema") != SCHEMA:
        out.error("schema", "", "", f"schema must be {SCHEMA}")
    for key in data:
        if key not in TOP_KEYS:
            out.error("unknown_key", "", str(key), f"unknown top-level key {key!r}")
    counters = data.get("id_counters")
    if not isinstance(counters, dict) or set(counters) != set(PREFIX_TYPE):
        out.error("id_counters", "", "", "id_counters must map exactly ADR, STORY and CANON")
    else:
        for prefix, value in counters.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                out.error("id_counters", "", prefix, f"id_counters.{prefix} must be an int >= 0")
    if not isinstance(data.get("nodes"), list):
        out.error("nodes", "", "", "nodes must be a list")


def _check_lists(out: _Checker, nid: str, node: dict) -> None:
    for group, keys in (("anchors", ANCHOR_KEYS), ("planned", PLANNED_KEYS)):
        value = node.get(group)
        if not isinstance(value, dict) or set(value) != set(keys):
            out.error(group, nid, "", f"{group} must map exactly {', '.join(keys)}")
            continue
        for key in keys:
            items = value[key]
            if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
                out.error(group, nid, key, f"{group}.{key} must be a list of strings")
                continue
            if key == "files":
                for pattern in items:
                    problem = hyper_ledger.glob_problem(pattern)
                    if problem:
                        out.error(group, nid, pattern, f"{group}.files {pattern!r} {problem}")
    for key in ("relates_to", "supersedes"):
        items = node.get(key)
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            out.error(key, nid, "", f"{key} must be a list of node ids")


def _check_record(
    out: _Checker, nid: str, name: str, value: Any, keys: tuple[str, ...], stamps: tuple[str, ...]
) -> bool:
    """Check a mapping field holds exactly keys and parseable timestamps; return True if usable."""
    if not isinstance(value, dict) or set(value) != set(keys):
        out.error(name, nid, "", f"{name} must map exactly {', '.join(keys)}")
        return False
    for key in stamps:
        if value[key] is not None and _parse_aware(value[key]) is None:
            out.error(name, nid, key, f"{name}.{key} is not a timestamp with an offset")
    return True


def _check_node(out: _Checker, index: int, node: Any, counters: dict) -> str | None:
    """Check one node's fields; return its id when it has a usable one."""
    if not isinstance(node, dict):
        out.error("node", f"nodes[{index}]", "", "a node must be a mapping")
        return None
    raw_id = node.get("id")
    nid = raw_id if isinstance(raw_id, str) else f"nodes[{index}]"
    missing = [key for key in NODE_KEYS if key not in node]
    extra = [str(key) for key in node if key not in NODE_KEYS]
    for key in missing:
        out.error("missing_key", nid, key, f"missing key {key}")
    for key in extra:
        out.error("unknown_key", nid, key, f"unknown key {key!r}")
    match = ID_RE.match(raw_id) if isinstance(raw_id, str) else None
    kind = node.get("type")
    if kind not in TYPE_PREFIX:
        out.error("type", nid, "", f"type must be one of {', '.join(TYPE_PREFIX)}")
    if match is None:
        out.error("id", nid, "", "id must be ADR-NNNN, STORY-NNNN or CANON-NNNN")
    else:
        prefix, number = match.group(1), int(match.group(2))
        if kind in TYPE_PREFIX and TYPE_PREFIX[kind] != prefix:
            out.error("id", nid, "", f"a {kind} node's id must start with {TYPE_PREFIX[kind]}-")
        counter = counters.get(prefix) if isinstance(counters, dict) else None
        if isinstance(counter, int) and number > counter:
            out.error("id_counter", nid, "", f"id is above id_counters.{prefix} ({counter})")
    if node.get("status") not in STATUSES:
        out.error("status", nid, "", f"status must be one of {', '.join(STATUSES)}")
    if node.get("origin") not in ORIGINS:
        out.error("origin", nid, "", f"origin must be one of {', '.join(ORIGINS)}")
    title = node.get("title")
    if not isinstance(title, str) or not title.strip() or CONTROL_RE.search(title):
        out.error("title", nid, "", "title must be a non-empty single line")
    if node.get("context") is not None and not isinstance(node.get("context"), str):
        out.error("context", nid, "", "context must be null or a string")
    if not isinstance(node.get("content_sha256"), str) or not SHA_RE.match(node["content_sha256"]):
        out.error("content_sha256", nid, "", "content_sha256 must be a lowercase sha256")
    _check_lists(out, nid, node)
    if not isinstance(node.get("stale"), bool):
        out.error("stale", nid, "", "stale must be a boolean")
    if node.get("stale_reason") is not None and not isinstance(node.get("stale_reason"), str):
        out.error("stale_reason", nid, "", "stale_reason must be null or a string")
    if node.get("legacy") is not None and not isinstance(node.get("legacy"), dict):
        out.error("legacy", nid, "", "legacy must be null or a mapping")
    _check_record(out, nid, "provenance", node.get("provenance"), PROVENANCE_KEYS, ("created_at",))
    _check_approval(out, nid, node)
    _check_history(out, nid, node)
    return raw_id if match is not None else None


def _check_approval(out: _Checker, nid: str, node: dict) -> None:
    approval = node.get("approval")
    if approval is None:
        if node.get("status") == "CANON":
            out.error("approval", nid, "", "a CANON node needs an approval record")
        return
    if not _check_record(out, nid, "approval", approval, APPROVAL_KEYS, ("approved_at",)):
        return
    if approval["method"] not in METHODS:
        out.error("approval", nid, "method", f"approval.method must be one of {', '.join(METHODS)}")
    if not isinstance(approval["nonce"], str) or not approval["nonce"]:
        out.error("approval", nid, "nonce", "approval.nonce is missing")
    for key in ("approved_by", "warrant"):
        if not isinstance(approval[key], str) or not approval[key].strip():
            out.error("approval", nid, key, f"approval.{key} is missing")
    if isinstance(approval["warrant"], str) and approval["warrant_sha256"] != _sha_text(
        approval["warrant"]
    ):
        out.error("approval", nid, "warrant_sha256", "approval.warrant_sha256 does not match")
    if node.get("status") == "CANON" and approval["content_sha256"] != node.get("content_sha256"):
        out.error("approval", nid, "content_sha256", "approval.content_sha256 != content_sha256")


def _check_history(out: _Checker, nid: str, node: dict) -> None:
    history = node.get("history")
    if not isinstance(history, list):
        out.error("history", nid, "", "history must be a list")
        return
    for position, entry in enumerate(history):
        name = f"history[{position}]"
        if not _check_record(out, nid, name, entry, HISTORY_KEYS, ("at",)):
            continue
        if entry["to"] not in STATUSES or (
            entry["from"] is not None and entry["from"] not in STATUSES
        ):
            out.error("history", nid, name, f"{name} from/to must be statuses")
    last = history[-1] if history else None
    if isinstance(last, dict) and last.get("to") != node.get("status"):
        out.error("history", nid, "", "the last history entry does not end in the node's status")


def _check_pointer(out: _Checker, top: Path, nid: str, node: dict) -> None:
    pointer = node.get("pointer")
    rel = pointer.get("path") if isinstance(pointer, dict) else None
    if not isinstance(pointer, dict) or set(pointer) != {"path"}:
        out.error("pointer", nid, "", "pointer must map exactly path")
    problem = _pointer_problem(rel)
    if problem is not None:
        out.error("pointer_outside_lore", nid, str(rel), f"pointer {rel!r} {problem}")
        return
    expected = _location_problem(node, rel)
    if expected is not None:
        out.error("pointer_location", nid, rel, f"pointer {rel} must be {expected}")
    state, sha = _file_state(top, rel)
    if state == "pointer_symlink":
        out.error(state, nid, rel, f"pointer {rel} is or passes through a symlink")
    elif state == "pointer_missing":
        out.error(state, nid, rel, f"pointer {rel} does not exist")
    elif sha != node.get("content_sha256"):
        if node.get("status") == "CANON":
            out.warning(
                "content_changed_since_approval",
                nid,
                rel,
                f"{rel} changed since approval (sha mismatch)",
            )
        else:
            out.warning(
                "content_sha_mismatch",
                nid,
                rel,
                f"content_sha256 does not match {rel} (PROPOSED: run `hyper_lore.py sync`)",
            )


def _check_canon_file(out: _Checker, top: Path, nid: str, node: dict) -> None:
    """A CANON node needs lore/canon/<ID>.md (R8); a decision's copy must keep its sha."""
    if node.get("status") != "CANON":
        return
    rel = _canon_rel(nid)
    state, sha = _file_state(top, rel)
    if state == "pointer_symlink":
        out.error("canon_file_symlink", nid, rel, f"{rel} is or passes through a symlink")
    elif state == "pointer_missing":
        out.error("canon_file_missing", nid, rel, f"CANON node without its canon file {rel}")
    elif node.get("type") != "canon" and sha != node.get("content_sha256"):
        out.warning("content_changed_since_approval", nid, rel, f"{rel} changed since approval")


def validate_graph(
    data: dict, top: Path, ledger: LedgerView | None = None, tree: TreeView | None = None
) -> Report:
    """Check a graph's schema, references, pointers, canon files and anchors (writes nothing)."""
    ledger = read_ledger_view(top) if ledger is None else ledger
    tree = TreeView(top) if tree is None else tree
    out = _Checker()
    _check_top(out, data)
    counters = data.get("id_counters")
    raw_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    seen: dict[str, dict] = {}
    for index, node in enumerate(raw_nodes):
        nid = _check_node(out, index, node, counters if isinstance(counters, dict) else {})
        if nid is None:
            continue
        if nid in seen:
            out.error("duplicate_id", nid, "", f"id {nid} appears more than once")
            continue
        seen[nid] = node
    review: list[tuple[str, list[str]]] = []
    anchors_seen = False
    for nid, node in seen.items():
        _check_pointer(out, top, nid, node)
        _check_canon_file(out, top, nid, node)
        for key in ("relates_to", "supersedes"):
            for target in _id_list(node.get(key)):
                if target == nid:
                    out.error(key, nid, target, f"{key} names the node itself")
                elif target not in seen:
                    out.warning(
                        f"{key}_dangling", nid, target, f"{key} names unknown node {target}"
                    )
        target = node.get("superseded_by")
        if node.get("status") == "SUPERSEDED":
            if not isinstance(target, str) or target not in seen:
                out.error("superseded_by", nid, "", "a SUPERSEDED node needs superseded_by")
        elif target is not None:
            out.error("superseded_by", nid, "", "superseded_by is set on a node not SUPERSEDED")
        anchors_seen = anchors_seen or bool(_anchor_list(node, "anchors", "components"))
        for rule, detail, message in anchor_causes(node, ledger, tree):
            if rule == "anchor_unknown":
                out.error(rule, nid, detail, message)
            elif node.get("status") in LIVE:
                out.warning(rule, nid, detail, message)
        if node.get("status") == "CANON":
            reasons = list(
                dict.fromkeys(
                    (
                        [str(node.get("stale_reason") or "stale")]
                        if node.get("stale") is True
                        else []
                    )
                    + stale_causes(node, top, ledger, tree)
                )
            )
            if reasons:
                review.append((nid, reasons))
    if ledger.problem is not None and anchors_seen:
        out.warning("ledger_unavailable", "", "", f"{ledger.problem}; component anchors unchecked")
    return Report(_sorted_findings(out.findings), sorted(review))


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    """Return findings errors first, then by node, rule, detail and message."""
    return sorted(
        findings, key=lambda f: (f.severity != "error", f.node, f.rule, f.detail, f.message)
    )


def _errors(report: Report) -> list[Finding]:
    return [finding for finding in report.findings if finding.severity == "error"]


def _invariant_problems(
    before: dict | None, after: dict, renamed: dict[str, str] | None = None
) -> list[str]:
    """Return the immutability rules a write breaks: deleted nodes, ids, history, counters.

    renamed maps an old id to its new id (renumber); that node is checked under its new id.
    """
    if before is None:
        return []
    problems = []
    after_index = node_map(after)
    renamed = renamed or {}
    for nid, old in node_map(before).items():
        new = after_index.get(renamed.get(nid, nid))
        if new is None:
            problems.append(f"node {nid} would be deleted (nodes are never deleted)")
            continue
        if new.get("type") != old.get("type"):
            problems.append(f"node {nid} would change type")
        old_history, new_history = old.get("history"), new.get("history")
        if isinstance(old_history, list) and (
            not isinstance(new_history, list) or new_history[: len(old_history)] != old_history
        ):
            problems.append(f"node {nid}: history is append-only")
    old_counters, new_counters = before.get("id_counters"), after.get("id_counters")
    if isinstance(old_counters, dict) and isinstance(new_counters, dict):
        for prefix, value in old_counters.items():
            new_value = new_counters.get(prefix)
            if isinstance(value, int) and (not isinstance(new_value, int) or new_value < value):
                problems.append(f"id_counters.{prefix} would decrease")
    return problems


# ---------------------------------------------------------------------------
# Locked, validated graph writes
# ---------------------------------------------------------------------------


class _Txn:
    """File effects of one graph write: undo steps on failure, follow-ups after the commit."""

    def __init__(self) -> None:
        self.undo: list[Callable[[], None]] = []
        self.after: list[Callable[[], None]] = []

    def rollback(self) -> None:
        for step in reversed(self.undo):
            with contextlib.suppress(OSError):
                step()

    def finish(self) -> None:
        for step in self.after:
            try:
                step()
            except OSError as exc:
                print(f"warning: hyper_lore.py: follow-up step failed: {exc}", file=sys.stderr)


class WriteResult(NamedTuple):
    """What a locked graph write did."""

    changed: bool
    label: str
    data: dict


def _commit(
    top: Path,
    before_text: str | None,
    before: dict | None,
    after: dict,
    renamed: dict[str, str] | None = None,
) -> bool:
    """Validate after against before and replace the graph; call with lore.lock held."""
    if before is not None and after == before:
        return False
    problems = _invariant_problems(before, after, renamed)
    if problems:
        raise ValidationFailed("refused: the write breaks the graph's invariants", problems)
    ledger, tree = read_ledger_view(top), TreeView(top)
    known = set()
    if before is not None:
        known = {f.identity for f in _errors(validate_graph(before, top, ledger, tree))}
    added = [
        f for f in _errors(validate_graph(after, top, ledger, tree)) if f.identity not in known
    ]
    if added:
        raise ValidationFailed(
            f"refused: the write would add {len(added)} error(s); nothing was written",
            [finding.line() for finding in added],
        )
    text = dump_graph(after)
    if yaml.safe_load(text) != after:
        raise ValidationFailed("refused: the dumped graph does not load back to the same data")
    _cas_write(graph_path(top), before_text, text)
    return True


def write_graph(
    top: Path,
    mutate: Callable[[dict, _Txn], str],
    *,
    create: bool = False,
    timeout: float | None = None,
    renamed: dict[str, str] | None = None,
) -> WriteResult | None:
    """Run one locked, validated, compare-and-swap write of the graph.

    mutate changes the loaded data in place, may record file effects in the _Txn, and returns
    a label. Without a graph the write returns None unless create is True (an empty graph is
    then mutated). Undo steps run when the write fails; follow-ups run after it succeeds, and
    then MAP.md and CANON.md are regenerated when lore/MAP.md exists (S05b).
    """
    txn = _Txn()
    with lore_lock(top, timeout):
        before_text, before = read_graph(top)
        if before is None and not create:
            return None
        after = copy.deepcopy(before) if before is not None else empty_graph()
        try:
            label = mutate(after, txn)
            changed = _commit(top, before_text, before, after, renamed)
        except BaseException:
            txn.rollback()
            raise
        txn.finish()
        if changed:
            _after_write(top, after)
    return WriteResult(changed, label, after)


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


def outbox_dir() -> Path | None:
    """Return the HACF_OUTBOX directory, or None when it is not set."""
    value = os.environ.get(OUTBOX_ENV)
    return Path(value) if value else None


def write_outbox_entry(directory: Path, op: str, args: dict) -> Path:
    """Write `{"tool": "hyper_lore", "op", "args"}` as the next numbered outbox entry.

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
    raise Conflict(f"could not claim an outbox entry name in {directory}")


def _refuse_outbox(command: str) -> None:
    if outbox_dir() is not None:
        raise Refused(
            f"{command} is refused under {OUTBOX_ENV}: an engine phase records only propose, "
            "note and stale"
        )


# ---------------------------------------------------------------------------
# Argument normalization (shared by the CLI and apply-outbox)
# ---------------------------------------------------------------------------


def _str_list(args: dict, key: str) -> list[str]:
    value = args.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise UsageError(f"{key} must be a list of strings")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _components(args: dict, key: str) -> list[str]:
    items = _str_list(args, key)
    for item in items:
        if not hyper_ledger.ID_RE.match(item):
            raise UsageError(f"{key}: {item!r} is not a component id")
    return items


def _globs(args: dict, key: str) -> list[str]:
    items = _str_list(args, key)
    for item in items:
        problem = hyper_ledger.glob_problem(item)
        if problem:
            raise UsageError(f"{key}: {item!r} {problem}")
    return items


def _lore_ids(args: dict, key: str) -> list[str]:
    items = _str_list(args, key)
    for item in items:
        if not ID_RE.match(item):
            raise UsageError(f"{key}: {item!r} is not a lore id (ADR-NNNN, STORY-NNNN, CANON-NNNN)")
    return items


def _optional_line(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise UsageError(f"{key} must be a string")
    return _single_line(value, key)


def normalize_propose(args: dict) -> dict:
    """Return checked propose arguments (body text, not a file path)."""
    kind = args.get("type")
    if kind not in TYPE_PREFIX:
        raise UsageError(f"--type must be one of {', '.join(TYPE_PREFIX)}")
    body = args.get("body")
    if not isinstance(body, str) or not body.strip():
        raise UsageError("the body is empty")
    symbols = _str_list(args, "anchors_symbols")
    for symbol in symbols:
        _single_line(symbol, "anchors_symbols")
    created_at = args.get("created_at")
    if created_at is not None and _parse_aware(created_at) is None:
        raise UsageError("created_at is not a timestamp with an offset")
    return {
        "type": kind,
        "title": _single_line(str(args.get("title") or ""), "--title"),
        "body": body,
        "anchors_components": _components(args, "anchors_components"),
        "anchors_files": _globs(args, "anchors_files"),
        "anchors_symbols": symbols,
        "planned_components": _components(args, "planned_components"),
        "planned_files": _globs(args, "planned_files"),
        "relates_to": _lore_ids(args, "relates_to"),
        "supersedes": _lore_ids(args, "supersedes"),
        "source": _optional_line(args, "source"),
        "context": _optional_line(args, "context"),
        "created_at": created_at,
        "run_id": _optional_line(args, "run_id"),
    }


def normalize_note(args: dict) -> dict:
    """Return checked note arguments."""
    legacy = bool(args.get("legacy"))
    source = _optional_line(args, "source")
    mission = _optional_line(args, "mission")
    if legacy == (mission is not None):
        raise UsageError("note needs exactly one of --mission <dir> or --legacy")
    if legacy and source is None:
        raise UsageError("note --legacy needs --source <MiniPRD path>")
    kind = _optional_line(args, "kind") or "field-note"
    if not KIND_RE.match(kind):
        raise UsageError(f"--kind {kind!r} must match {KIND_RE.pattern}")
    agent = _optional_line(args, "agent") or "unknown"
    slice_id = source if legacy else (_optional_line(args, "slice") or "unknown")
    for flag, value in (("--agent", agent), ("--slice", slice_id)):
        if not TOKEN_RE.match(value):
            raise UsageError(f"{flag} {value!r} must match {TOKEN_RE.pattern}")
    date = args.get("date")
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise UsageError("date must be YYYY-MM-DD")
    anchors = _str_list(args, "anchors")
    for anchor in anchors:
        _single_line(anchor, "--anchors")
    return {
        "legacy": legacy,
        "mission": mission,
        "source": source,
        "text": _single_line(str(args.get("text") or ""), "--text"),
        "kind": kind,
        "agent": agent,
        "slice": slice_id,
        "anchors": anchors,
        "date": date,
    }


def normalize_stale(args: dict) -> dict:
    """Return checked stale arguments."""
    nodes = _components(args, "nodes")
    files = _str_list(args, "files")
    for item in files:
        _single_line(item, "--files")
    if not nodes and not files:
        raise UsageError("stale needs --nodes or --files")
    return {
        "nodes": nodes,
        "files": files,
        "reason": _single_line(str(args.get("reason") or ""), "--reason"),
    }


NORMALIZE = {"propose": normalize_propose, "note": normalize_note, "stale": normalize_stale}


# ---------------------------------------------------------------------------
# Agent writes: propose, note, stale, resolve-anchors, sync
# ---------------------------------------------------------------------------


def slugify(title: str) -> str:
    """Return a file-name slug of a title (lowercase ASCII words joined by -)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-")


def _allocate(data: dict, prefix: str) -> str:
    """Return the next id of a prefix and raise its counter (counters never decrease)."""
    counters = data.setdefault("id_counters", {"ADR": 0, "STORY": 0, "CANON": 0})
    used = [
        int(match.group(2))
        for nid in node_map(data)
        if (match := ID_RE.match(nid)) and match.group(1) == prefix
    ]
    current = counters.get(prefix, 0)
    number = max([current if isinstance(current, int) else 0, *used]) + 1
    counters[prefix] = number
    return f"{prefix}-{number:04d}"


def _body_rel(kind: str, node_id: str, title: str) -> str:
    if kind == "canon":
        return f"{PROPOSED_DIR}/{node_id}.md"
    slug = slugify(title)
    return f"{TYPE_DIRS[kind]}/{node_id}{'-' + slug if slug else ''}.md"


def do_propose(top: Path, args: dict) -> WriteResult:
    """Add one PROPOSED node and write its body verbatim (normalized args)."""
    at = args["created_at"] or _runtime.utc_now_iso()

    def mutate(data: dict, txn: _Txn) -> str:
        kind = args["type"]
        node_id = _allocate(data, TYPE_PREFIX[kind])
        rel = _body_rel(kind, node_id, args["title"])
        _ensure_lore_dir(top, rel.rsplit("/", 1)[0])
        target = top / rel
        if os.path.lexists(target):
            raise Conflict(f"refused: {rel} already exists; nothing was written")
        _cas_write(target, None, args["body"])
        txn.undo.append(lambda: _unlink_quietly(target))
        source = args["source"]
        data["nodes"].append(
            {
                "id": node_id,
                "type": kind,
                "title": args["title"],
                "status": "PROPOSED",
                "origin": "agent",
                "context": args["context"],
                "pointer": {"path": rel},
                "content_sha256": _sha_text(args["body"]),
                "anchors": {
                    "components": args["anchors_components"],
                    "files": args["anchors_files"],
                    "symbols": args["anchors_symbols"],
                },
                "planned": {
                    "components": args["planned_components"],
                    "files": args["planned_files"],
                },
                "relates_to": args["relates_to"],
                "supersedes": args["supersedes"],
                "superseded_by": None,
                "stale": False,
                "stale_reason": None,
                "legacy": None,
                "provenance": {
                    "created_by": "agent",
                    "created_at": at,
                    "source": source,
                    "session_id": None,
                    "run_id": args["run_id"],
                },
                "approval": None,
                "history": [
                    {
                        "at": at,
                        "from": None,
                        "to": "PROPOSED",
                        "by": "agent",
                        "warrant": None,
                        "ref": source,
                    }
                ],
            }
        )
        return f"propose: {node_id} written ({rel})"

    result = write_graph(top, mutate, create=True)
    assert result is not None  # create=True always writes
    return result


def _protected_span(text: str) -> str | None:
    """Return the bytes from the protected procedure heading through the END marker line."""
    if text.startswith(PROTECTED_START):
        start = 0
    else:
        found = text.find("\n" + PROTECTED_START)
        if found < 0:
            return None
        start = found + 1
    end = text.find(PROTECTED_END, start)
    if end < 0:
        return None
    newline = text.find("\n", end)
    return text[start : len(text) if newline < 0 else newline + 1]


def _note_target(top: Path, args: dict) -> tuple[Path, str]:
    """Return (file, heading) a note goes to; refuse spec/process and missing files."""
    if args["legacy"]:
        return top / ACTIVE_CONTEXT_REL, LEGACY_NOTES_HEADING
    mission = Path(args["mission"])
    folder = (mission if mission.is_absolute() else top / mission).resolve()
    try:
        rel = folder.relative_to(top).as_posix()
    except ValueError:
        raise UsageError(f"--mission {args['mission']} is outside the repository") from None
    if rel == "spec/process" or rel.startswith("spec/process/"):
        raise Refused("notes never go to spec/process/")
    if not folder.is_dir():
        raise UsageError(f"--mission {args['mission']} is not a directory")
    return folder / "NOTES.md", NOTES_HEADING


def note_line(args: dict) -> str:
    """Return the NOTES line of normalized note arguments."""
    anchors = ", ".join(args["anchors"]) or "none"
    return (
        f"- {args['date']} [{args['kind']}] (agent:{args['agent']}, slice {args['slice']}) "
        f"{args['text']} anchors: {anchors}"
    )


def do_note(top: Path, args: dict) -> str:
    """Append one note line (normalized args) under lore.lock, compare-and-swap."""
    target, heading = _note_target(top, args)
    line = note_line(args)
    with lore_lock(top):
        if not graph_path(top).exists():  # clarifying question 4: note creates the graph too
            _cas_write(graph_path(top), None, dump_graph(empty_graph()))
            _after_write(top, empty_graph())
        if target.is_symlink() or not target.is_file():
            raise UsageError(
                f"{target.relative_to(top).as_posix()} does not exist or is not a regular file"
                + ("" if args["legacy"] else "; create the mission with hyper_scope.py")
            )
        text = _read_text(target)
        new = hyper_spec.append_line(text, (heading,), line)
        protected = _protected_span(text)
        if protected is not None and _protected_span(new) != protected:
            raise Refused(
                "refused: the note would change the protected v0.6.0 procedure section of "
                f"{ACTIVE_CONTEXT_REL}; nothing was written"
            )
        _cas_write(target, text, new)
    return f"note: appended to {target.relative_to(top).as_posix()}"


def _mark_stale(data: dict, select: Callable[[dict], str | None]) -> list[str]:
    """Mark each live, not yet stale node that select gives a reason for; return their ids."""
    marked = []
    for node in nodes_of(data):
        if node.get("status") not in LIVE or node.get("stale") is True:
            continue
        reason = select(node)
        if reason:
            node["stale"] = True
            node["stale_reason"] = reason
            marked.append(str(node.get("id")))
    return marked


def _stale_selector(args: dict) -> Callable[[dict], str | None]:
    nodes, files = set(args["nodes"]), args["files"]

    def select(node: dict) -> str | None:
        if nodes & set(_anchor_list(node, "anchors", "components")):
            return args["reason"]
        for anchor in _anchor_list(node, "anchors", "files"):
            if any(_files_intersect(anchor, given) for given in files):
                return args["reason"]
        return None

    return select


def do_stale(top: Path, args: dict) -> tuple[list[str], WriteResult | None]:
    """Mark the live nodes whose anchors intersect the given components or files."""
    marked: list[str] = []

    def mutate(data: dict, txn: _Txn) -> str:
        marked.extend(_mark_stale(data, _stale_selector(args)))
        return f"stale: marked {', '.join(marked)}" if marked else "stale: no change"

    return marked, write_graph(top, mutate)


def stale_from_impact(node_ids: list[str], changed_paths: list[str]) -> list[str]:
    """Mark lore anchored to an impact stale (reason `impact`); return the marked ids.

    Called by hypergraph_updater's post-impact hook. It does nothing without a graph, writes
    nothing when nothing changes, records an outbox entry under HACF_OUTBOX, and never raises:
    a failure is a warning on stderr and an empty result.
    """
    try:
        found = hyper_ledger.repo_toplevel(Path.cwd())
        top = found.resolve() if found is not None else None
        if top is None or not graph_path(top).exists():
            return []
        args = {
            "nodes": [str(item) for item in node_ids if hyper_ledger.ID_RE.match(str(item))],
            "files": [str(item) for item in changed_paths if str(item).strip()],
            "reason": "impact",
        }
        if not args["nodes"] and not args["files"]:
            return []
        outbox = outbox_dir()
        if outbox is not None:
            _, data = read_graph(top)
            preview = copy.deepcopy(data)
            marked = _mark_stale(preview, _stale_selector(args))
            if marked:
                write_outbox_entry(outbox, "stale", args)
            return marked
        return do_stale(top, args)[0]
    except Exception as exc:  # the updater must never fail because of lore
        print(f"warning: hyper_lore.stale_from_impact: {exc}", file=sys.stderr)
        return []


def do_resolve_anchors(top: Path) -> WriteResult | None:
    """Move planned components and files that now exist into anchors (idempotent)."""
    moved: list[str] = []

    def mutate(data: dict, txn: _Txn) -> str:
        ledger, tree = read_ledger_view(top), TreeView(top)
        for node in nodes_of(data):
            planned, anchors = node.get("planned"), node.get("anchors")
            if not isinstance(planned, dict) or not isinstance(anchors, dict):
                continue
            for key, exists in (
                ("components", lambda c: ledger.ids is not None and c in ledger.ids),
                ("files", tree.matches),
            ):
                items = planned.get(key)
                if not isinstance(items, list) or not isinstance(anchors.get(key), list):
                    continue
                ready = [item for item in items if isinstance(item, str) and exists(item)]
                if not ready:
                    continue
                planned[key] = [item for item in items if item not in ready]
                anchors[key] = list(dict.fromkeys([*anchors[key], *ready]))
                moved.extend(f"{node.get('id')}:{item}" for item in ready)
        return (
            f"resolve-anchors: moved {', '.join(moved)}" if moved else "resolve-anchors: no change"
        )

    return write_graph(top, mutate)


def do_sync(top: Path) -> WriteResult | None:
    """Recompute PROPOSED shas; mark CANON nodes stale for a changed body or removed anchor."""
    changes: list[str] = []

    def mutate(data: dict, txn: _Txn) -> str:
        ledger, tree = read_ledger_view(top), TreeView(top)
        for node in nodes_of(data):
            nid = str(node.get("id"))
            pointer = node.get("pointer")
            rel = pointer.get("path") if isinstance(pointer, dict) else None
            if node.get("status") == "PROPOSED" and _pointer_problem(rel) is None:
                state, sha = _file_state(top, rel)
                if state == "ok" and sha != node.get("content_sha256"):
                    node["content_sha256"] = sha
                    changes.append(f"{nid} sha")
            elif node.get("status") == "CANON" and node.get("stale") is not True:
                reasons = stale_causes(node, top, ledger, tree)
                if reasons:
                    node["stale"] = True
                    node["stale_reason"] = "; ".join(reasons)
                    changes.append(f"{nid} stale")
        return f"sync: {', '.join(changes)}" if changes else "sync: no change"

    return write_graph(top, mutate)


# ---------------------------------------------------------------------------
# Index: lore/MAP.md's generated region and lore/CANON.md (R8, R20.6, R26)
# ---------------------------------------------------------------------------


def _id_key(node_id: Any) -> tuple[str, int, str]:
    """Sort key of a lore id: its prefix, then its number; ids that do not parse sort last."""
    match = ID_RE.match(node_id) if isinstance(node_id, str) else None
    if match is None:
        return ("~", 0, str(node_id))
    return (match.group(1), int(match.group(2)), node_id)


def _by_id(nodes: list[dict]) -> list[dict]:
    return sorted(nodes, key=lambda node: _id_key(node.get("id")))


def _summary(value: Any) -> str:
    """Return text as one printable line (whitespace collapsed), capped at SUMMARY_MAX."""
    text = _printable(" ".join(str(value).split()))
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 3].rstrip() + "..."


def _map_entry(node: dict, note: str = "") -> str:
    """Return `- [ID](path) title` plus note; the path is relative to lore/MAP.md.

    A CANON node links to its canon file, any other node to its pointer.
    """
    nid = str(node.get("id"))
    if node.get("status") == "CANON":
        rel = _canon_rel(nid)
    else:
        pointer = node.get("pointer")
        rel = pointer.get("path") if isinstance(pointer, dict) else None
    label = nid
    if _pointer_problem(rel) is None:
        target = rel[len("lore/") :]
        label = f"[{nid}]({target if LINK_SAFE_RE.match(target) else f'<{target}>'})"
    return f"- {label} {_summary(node.get('title'))}{note}"


def _rejection(node: dict) -> tuple[datetime | None, Any]:
    """Return (time, warrant) of the history entry that last moved a node to REJECTED."""
    history = node.get("history")
    for entry in reversed(history if isinstance(history, list) else []):
        if isinstance(entry, dict) and entry.get("to") == "REJECTED":
            return _parse_aware(entry.get("at")), entry.get("warrant")
    return None, None


def map_rows(data: dict) -> list[str]:
    """Return the lines of MAP.md's generated region; they depend on the graph alone."""
    nodes = _by_id(nodes_of(data))

    def section(title: str, entries: list[str]) -> list[str]:
        return [f"## {title}", "", *(entries or ["- (none)"]), ""]

    canon = [
        _map_entry(n, " (stale; still binding)" if n.get("stale") is True else "")
        for n in nodes
        if n.get("status") == "CANON"
    ]
    proposed = [
        _map_entry(n, f" ({n.get('type')}{'; binding: user decision' if binding(n) else ''})")
        for n in nodes
        if n.get("status") == "PROPOSED" and n.get("type") != "story"
    ]
    stale = [
        _map_entry(n, f" ({n.get('status')}): {_summary(n.get('stale_reason') or 'stale')}")
        for n in nodes
        if n.get("status") in LIVE and n.get("stale") is True
    ]
    buried = [
        _map_entry(
            n,
            f" (SUPERSEDED by {n.get('superseded_by')})"
            if n.get("status") == "SUPERSEDED"
            else " (RETIRED)",
        )
        for n in nodes
        if n.get("status") in ("SUPERSEDED", "RETIRED")
    ]
    rejected = sorted(
        (n for n in nodes if n.get("status") == "REJECTED"),
        key=lambda n: (_rejection(n)[0] or EPOCH, _id_key(n.get("id"))),
        reverse=True,
    )[:GRAVEYARD_CAP]
    for node in rejected:
        at, warrant = _rejection(node)
        day = at.date().isoformat() if at is not None else "date unknown"
        said = _summary(warrant) if isinstance(warrant, str) and warrant.strip() else "(none)"
        buried.append(_map_entry(node, f" (REJECTED {day}) warrant: {said}"))
    stories = [
        _map_entry(n) for n in nodes if n.get("type") == "story" and n.get("status") == "PROPOSED"
    ]
    return [
        GENERATED_NOTE,
        "",
        *section("Canon", canon),
        *section("Proposed", proposed),
        *section("Stale", stale),
        *section("Graveyard", buried),
        FULL_LIST_LINE,
        "",
        *section("Stories", stories),
        "## Where things live",
        "",
        *WHERE_THINGS_LIVE,
    ]


def render_map(text: str | None, data: dict) -> str:
    """Return MAP.md text with its generated region rebuilt from data (bytes outside kept).

    text None (no MAP.md yet) starts from MAP_INTRO. Bad HACF markers are a Conflict (exit 5).
    """
    base = MAP_INTRO if text is None else text
    try:
        return hyper_scope.set_generated_region(base, MAP_REGION, map_rows(data))
    except hyper_spec.SpecError as exc:
        raise Conflict(f"{MAP_REL}: {exc}; nothing was written") from None


def canon_md_text(data: dict, read_canon: Callable[[str], str]) -> str:
    """Return CANON.md: each CANON node's canon file between its lore markers, in id order."""
    parts = []
    for node in _by_id([n for n in nodes_of(data) if n.get("status") == "CANON"]):
        nid = str(node.get("id"))
        body = read_canon(nid)
        tail = "" if not body or body.endswith("\n") else "\n"
        parts.append(f"<!-- lore:{nid} -->\n{body}{tail}<!-- /lore:{nid} -->\n")
    return "".join(parts)


def _canon_reader(top: Path) -> Callable[[str], str]:
    """Return a reader of `lore/canon/<ID>.md` in the work tree (a missing file is refused)."""

    def read(node_id: str) -> str:
        rel = _canon_rel(node_id)
        if _file_state(top, rel)[0] != "ok":
            raise ValidationFailed(
                f"CANON node {node_id} has no canon file {rel} (R8); nothing was written"
            )
        return _read_text(top / rel)

    return read


def canon_marker_problems(text: str) -> list[str]:
    """Return why CANON.md's `<!-- lore:ID -->` markers are not unique and balanced."""
    problems, seen, open_id = [], set(), None
    for number, line in enumerate(text.split("\n"), start=1):
        match = CANON_MARK_RE.match(line.rstrip("\r"))
        if match is None:
            continue
        closing, nid = match.group(1) == "/", match.group(2)
        if not closing:
            if open_id is not None:
                problems.append(f"line {number}: lore:{nid} opens inside lore:{open_id}")
            elif nid in seen:
                problems.append(f"line {number}: lore:{nid} appears more than once")
            seen.add(nid)
            open_id = nid
        elif open_id != nid:
            problems.append(f"line {number}: /lore:{nid} closes no open lore:{nid}")
        else:
            open_id = None
    if open_id is not None:
        problems.append(f"lore:{open_id} is never closed")
    return problems


def _flag_needs_index(top: Path) -> None:
    """Touch `.agents/state/lore/needs-index`; validate fails until index succeeds."""
    path = top / NEEDS_INDEX_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _regenerate(top: Path, data: dict) -> list[str]:
    """Write MAP.md's region and CANON.md from data where they differ; call with lore.lock held.

    Both files are rendered before either is written, so a refusal writes nothing. A missing
    MAP.md is created from MAP_INTRO. needs-index is cleared. Returns the written paths.
    """
    for rel in (MAP_REL, CANON_MD_REL):
        link = _symlink_in(top, rel)
        if link is not None:
            raise Refused(f"{link} is a symlink; hyper_lore.py writes only regular files")
    old_map = _read_optional(top / MAP_REL)
    old_canon = _read_optional(top / CANON_MD_REL)
    new_map = render_map(old_map, data)
    new_canon = canon_md_text(data, _canon_reader(top))
    _ensure_lore_dir(top, "lore")
    written = []
    for rel, old, new in ((MAP_REL, old_map, new_map), (CANON_MD_REL, old_canon, new_canon)):
        if new != old:
            _cas_write(top / rel, old, new)
            written.append(rel)
    _unlink_quietly(top / NEEDS_INDEX_REL)
    return written


def _after_write(top: Path, data: dict) -> None:
    """Regenerate MAP.md and CANON.md after a graph write when lore/MAP.md exists (lock held).

    A failure does not undo the write; it touches needs-index so validate reports it until
    `index` succeeds.
    """
    if not os.path.lexists(top / MAP_REL):
        return
    try:
        _regenerate(top, data)
    except (LoreError, OSError) as exc:
        with contextlib.suppress(OSError):
            _flag_needs_index(top)
        print(
            f"warning: hyper_lore.py: lore/MAP.md and lore/CANON.md were not regenerated "
            f"({exc}); {INDEX_HINT}",
            file=sys.stderr,
        )


def do_index(top: Path) -> str:
    """Regenerate MAP.md's region (creating MAP.md) and CANON.md, then run resolve-anchors.

    The files are written first, so resolve-anchors sees them; when it moves an anchor the
    graph write regenerates them again, with the same bytes (the region ignores anchors).
    """
    with lore_lock(top):
        _, data = read_graph(top)
        if data is None:
            return f"index: {NO_GRAPH}"
        written = _regenerate(top, data)
    parts = [f"index: wrote {', '.join(written)}" if written else "index: no change"]
    resolved = do_resolve_anchors(top)
    if resolved is not None and resolved.changed:
        parts.append(resolved.label)
    return "; ".join(parts)


def do_init(top: Path) -> str:
    """Create the empty graph, lore/MAP.md and lore/CANON.md; refuse when a graph exists (R49).

    Holding lore.lock: any lore_hypergraph.yml (os.path.lexists: a graph, an empty or
    unparsable file, a symlink) exits 4 with nothing written. Otherwise the empty hacf.lore/v1
    graph is written compare-and-swap and both index files are generated in the same hold.
    The check also runs before the lock, which creates `.agents/state/`, so a refusal of an
    existing graph creates nothing.
    """
    path = graph_path(top)
    refusal = f"{path} already exists; init never overwrites a lore graph"
    if os.path.lexists(path):
        raise Refused(refusal)
    with lore_lock(top):
        if os.path.lexists(path):
            raise Refused(refusal)
        for rel in (MAP_REL, CANON_MD_REL):  # refuse before the graph is written
            link = _symlink_in(top, rel)
            if link is not None:
                raise Refused(f"{link} is a symlink; hyper_lore.py writes only regular files")
        data = empty_graph()
        _cas_write(path, None, dump_graph(data))
        written = [GRAPH_REL, *_regenerate(top, data)]
    return f"init: wrote {', '.join(written)}"


def index_findings(top: Path, data: dict) -> list[Finding]:
    """Return validate's index errors: stale MAP.md region or CANON.md, markers, needs-index."""
    out = _Checker()
    if os.path.lexists(top / NEEDS_INDEX_REL):
        out.error(
            "needs_index",
            "",
            NEEDS_INDEX_REL,
            f"{NEEDS_INDEX_REL} is set (a merge could not regenerate the lore index); {INDEX_HINT}",
        )
    active = os.path.lexists(top / MAP_REL)
    for rel, rule in ((MAP_REL, "map"), (CANON_MD_REL, "canon_md")):
        present = os.path.lexists(top / rel)
        if not present:
            if active and rel == CANON_MD_REL:
                out.error("canon_md_missing", "", rel, f"{rel} is missing; {INDEX_HINT}")
            continue
        if _symlink_in(top, rel) is not None or not (top / rel).is_file():
            out.error(f"{rule}_symlink", "", rel, f"{rel} must be a regular file, not a symlink")
            continue
        try:
            text = _read_text(top / rel)
        except UsageError as exc:
            out.error(f"{rule}_unreadable", "", rel, str(exc))
            continue
        if rel == MAP_REL:
            try:
                expected = render_map(text, data)
            except Conflict as exc:
                out.error("map_markers", "", rel, str(exc).replace("; nothing was written", ""))
                continue
            if expected != text:
                out.error(
                    "map_stale",
                    "",
                    rel,
                    f"{rel}'s generated region differs from what index builds; {INDEX_HINT}",
                )
            continue
        for problem in canon_marker_problems(text):
            out.error("canon_md_markers", "", problem, f"{rel}: {problem}")
        try:
            expected = canon_md_text(data, _canon_reader(top))
        except LoreError:
            continue  # validate_graph reports the CANON node without its canon file
        if expected != text:
            out.error(
                "canon_md_stale", "", rel, f"{rel} differs from what index builds; {INDEX_HINT}"
            )
    return out.findings


# ---------------------------------------------------------------------------
# Gate: pending records, tickets, TTY confirmation and the gated verbs
# ---------------------------------------------------------------------------


def _pending_dir(top: Path) -> Path:
    return top / PENDING_REL


def _load_json(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pending_valid(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and record.get("schema") == PENDING_SCHEMA
        and isinstance(record.get("id"), str)
        and isinstance(record.get("nonce"), str)
        and isinstance(record.get("content_sha256"), str)
        and _parse_aware(record.get("created_at")) is not None
        and _parse_aware(record.get("expires_at")) is not None
    )


def _live_pending(top: Path, now: datetime) -> dict[str, dict]:
    """Return the unexpired pending records by id; delete expired and unreadable ones."""
    directory = _pending_dir(top)
    if not directory.is_dir():
        return {}
    live = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = directory / name
        record = _load_json(path)
        if not _pending_valid(record) or _parse_aware(record["expires_at"]) <= now:
            _unlink_quietly(path)
            continue
        live[record["id"]] = record
    return live


def _body_of(top: Path, node: dict) -> tuple[str, str]:
    """Return (pointer path, body text) of a node; refuse an unusable pointer."""
    pointer = node.get("pointer")
    rel = pointer.get("path") if isinstance(pointer, dict) else None
    problem = _pointer_problem(rel)
    if problem is not None:
        raise Refused(f"{node.get('id')}: pointer {rel!r} {problem}")
    state, _ = _file_state(top, rel)
    if state != "ok":
        raise Refused(f"{node.get('id')}: pointer {rel} is missing or a symlink")
    return rel, _read_text(top / rel)


def affects(data: dict, node: dict) -> dict[str, list[str]]:
    """Return the ids an approval request names: supersedes, retires and reanchors (R23.3)."""
    index = node_map(data)
    supersedes = [
        target
        for target in _id_list(node.get("supersedes"))
        if index.get(target, {}).get("status") == "CANON"
    ]
    retires = [node["id"]] if node.get("status") == "CANON" else []
    return {"supersedes": supersedes, "retires": retires, "reanchors": []}


def _gate_node(top: Path, node_id: str) -> tuple[dict, dict]:
    """Return (graph, node) for a gated command; refuse a bad id or a missing node."""
    if not ID_RE.match(node_id):
        raise Refused(f"{node_id!r} is not one lore id (ADR-NNNN, STORY-NNNN or CANON-NNNN)")
    _, data = read_graph(top)
    if data is None:
        raise Refused(NO_GRAPH)
    node = node_map(data).get(node_id)
    if node is None:
        raise Refused(f"no lore node {node_id}")
    return data, node


def _refuse_modes(command: str) -> None:
    if os.environ.get(AUTONOMOUS_ENV, "") not in ("", "0"):
        raise Refused(f"{command} is refused under {AUTONOMOUS_ENV}=1 (R7)")
    _refuse_outbox(command)


def do_approval_request(top: Path, node_id: str) -> tuple[dict, str, str]:
    """Write the pending record of one node; return (record, pointer path, body)."""
    _refuse_modes("approval-request")
    data, node = _gate_node(top, node_id)
    if node.get("status") not in ("PROPOSED", "CANON", "REJECTED"):
        raise Refused(f"{node_id} is {node.get('status')}; no gated verb applies to it")
    with lore_lock(top):
        now = _now()
        pending = _live_pending(top, now)
        if pending:
            other = sorted(pending)[0]
            raise Refused(
                f"refused: an approval request is pending for {other} (expires "
                f"{pending[other]['expires_at']}); cancel it with `hyper_lore.py approval-cancel "
                f"{other}`"
            )
        data, node = _gate_node(top, node_id)
        rel, body = _body_of(top, node)
        record = {
            "schema": PENDING_SCHEMA,
            "id": node_id,
            "title": node.get("title"),
            "content_sha256": _sha_text(body),
            "nonce": secrets.token_hex(16),
            "created_at": now.isoformat(),
            "expires_at": (now + PENDING_TTL).isoformat(),
            "affects": affects(data, node),
        }
        directory = _pending_dir(top)
        directory.mkdir(parents=True, exist_ok=True)
        _cas_write(directory / f"{node_id}.json", None, json.dumps(record, indent=2) + "\n")
    return record, rel, body


def do_approval_cancel(top: Path, node_id: str) -> bool:
    """Delete one pending record; return True when there was one."""
    if not ID_RE.match(node_id):
        raise UsageError(f"{node_id!r} is not a lore id")
    with lore_lock(top):
        path = _pending_dir(top) / f"{node_id}.json"
        existed = os.path.lexists(path)
        _unlink_quietly(path)
    return existed


def _ticket_matches(ticket: Any, verb: str, node_id: str, nonce: str, now: datetime) -> bool:
    if not isinstance(ticket, dict) or ticket.get("schema") != TICKET_SCHEMA:
        return False
    if (ticket.get("verb"), ticket.get("id"), ticket.get("nonce")) != (verb, node_id, nonce):
        return False
    created, expires = (
        _parse_aware(ticket.get("created_at")),
        _parse_aware(ticket.get("expires_at")),
    )
    if created is None or expires is None:
        return False
    if not timedelta(0) < expires - created <= TICKET_TTL:
        return False
    return created <= now + CLOCK_SKEW and now < expires


def consume_ticket(top: Path, verb: str, node_id: str, nonce: str) -> bool:
    """Delete the first unexpired ticket matching verb, id and nonce; True if one was used."""
    directory = top / TICKETS_REL
    if _symlink_in(top, TICKETS_REL) is not None or not directory.is_dir():
        return False
    now = _now()
    for name in sorted(os.listdir(directory)):
        path = directory / name
        if not _ticket_matches(_load_json(path), verb, node_id, nonce, now):
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:  # another invocation used it first
            continue
        return True
    return False


def tty_confirm(verb: str, node_id: str, title: str) -> bool:
    """Ask for the node id; True only when stdin and stdout are TTYs and the answer matches."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    sys.stdout.write(
        f"hyper_lore.py {verb} {node_id}: {_printable(title)}\n"
        f"No hook ticket. Type the node id ({node_id}) to confirm: "
    )
    sys.stdout.flush()
    answer = sys.stdin.readline()
    return answer.strip() == node_id


def _git_identity(top: Path) -> str:
    """Return "Name <email>" from git config (attribution, not identity: R23.6)."""
    values = []
    for key in ("user.name", "user.email"):
        proc = _git(["config", "--get", key], top)
        value = proc.stdout.strip() if proc.returncode == 0 else ""
        if not value or CONTROL_RE.search(value):
            raise Refused(f"git config {key} is not set; approved_by comes only from git config")
        values.append(value)
    return f"{values[0]} <{values[1]}>"


def _read_warrant(path: str, title: str) -> str:
    """Return the warrant text; refuse a missing, empty or title-equal warrant file."""
    try:
        text = Path(path).read_bytes().decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"--warrant-file {path} is not a readable UTF-8 file ({exc})") from None
    if not text:
        raise Refused(f"--warrant-file {path} is empty; the warrant must be the user's words")
    if " ".join(text.split()).casefold() == " ".join(title.split()).casefold():
        raise Refused("the warrant equals the node's title; it must say why, in the user's words")
    return text


def _check_transition(verb: str, node: dict) -> None:
    required, _ = TRANSITIONS[verb]
    status = node.get("status")
    if status != required:
        raise Refused(f"refused: {verb} is not allowed on a {status} node ({node.get('id')})")
    if verb == "review" and node.get("stale") is not True:
        raise Refused(f"refused: review --still-valid needs a stale CANON node ({node.get('id')})")


def _check_pending(top: Path, node: dict, nonce: str) -> tuple[dict, str]:
    """Return (pending record, body sha) when the request matches nonce and the unchanged body."""
    node_id = node["id"]
    path = _pending_dir(top) / f"{node_id}.json"
    record = _load_json(path)
    if not _pending_valid(record) or record["id"] != node_id:
        raise Refused(f"refused: no pending approval request for {node_id} (run approval-request)")
    if _parse_aware(record["expires_at"]) <= _now():
        _unlink_quietly(path)
        raise Refused(f"refused: the approval request for {node_id} expired; request it again")
    if not secrets.compare_digest(record["nonce"].encode(), nonce.encode()):
        raise Refused(f"refused: --nonce does not match the pending request for {node_id}")
    _, body = _body_of(top, node)
    sha = _sha_text(body)
    if sha != record["content_sha256"]:
        raise Refused(f"refused: the body of {node_id} changed since the request; request again")
    return record, sha


def _append_history(
    node: dict, at: str, old: str, new: str, by: str, warrant: str | None, ref: str | None
) -> None:
    history = node.get("history")
    if not isinstance(history, list):
        raise ValidationFailed(f"node {node.get('id')}: history is not a list; nothing was written")
    history.append({"at": at, "from": old, "to": new, "by": by, "warrant": warrant, "ref": ref})


def _place_canon_file(top: Path, node: dict, body: str, txn: _Txn) -> None:
    """Write lore/canon/<ID>.md once (R8); refuse an existing file with other bytes."""
    rel = _canon_rel(node["id"])
    _ensure_lore_dir(top, CANON_DIR)
    target = top / rel
    if os.path.lexists(target):
        if target.is_symlink() or _sha_bytes(target.read_bytes()) != _sha_text(body):
            raise Conflict(f"refused: {rel} already exists with other content; nothing was written")
        return
    _cas_write(target, None, body)
    txn.undo.append(lambda: _unlink_quietly(target))
    if node.get("type") == "canon":
        old = node["pointer"]["path"]
        node["pointer"] = {"path": rel}
        txn.after.append(lambda: _unlink_quietly(top / old))


def do_gated(top: Path, verb: str, ids: list[str], nonce: str, warrant_file: str) -> str:
    """Run one gated verb: preconditions, then a ticket or a TTY confirmation, then the write."""
    _refuse_modes(verb)
    if len(ids) != 1:
        raise Refused(f"refused: {verb} takes exactly one id per invocation (there is no batch)")
    node_id = ids[0]
    _, node = _gate_node(top, node_id)
    _check_transition(verb, node)
    record, sha = _check_pending(top, node, nonce)
    warrant = _read_warrant(warrant_file, str(node.get("title") or ""))
    by = _git_identity(top)
    if consume_ticket(top, verb, node_id, nonce):
        method = TICKET_METHOD
    elif tty_confirm(verb, node_id, str(node.get("title") or "")):
        method = TTY_METHOD
    else:
        raise Refused(
            f"refused: {verb} {node_id} needs a matching hook ticket or a TTY confirmation (R6)"
        )
    _, new_status = TRANSITIONS[verb]
    pending_path = _pending_dir(top) / f"{node_id}.json"

    def mutate(data: dict, txn: _Txn) -> str:
        current = node_map(data).get(node_id)
        if current is None:
            raise Refused(f"no lore node {node_id}")
        _check_transition(verb, current)
        _, body = _body_of(top, current)
        if _sha_text(body) != sha:
            raise Refused(f"refused: the body of {node_id} changed during the request")
        at = _runtime.utc_now_iso()
        old_status = current["status"]
        if verb == "approve":
            _place_canon_file(top, current, body, txn)
            current["content_sha256"] = sha
            current["approval"] = {
                "approved_by": by,
                "approved_at": at,
                "warrant": warrant,
                "warrant_sha256": _sha_text(warrant),
                "method": method,
                "content_sha256": sha,
                "nonce": nonce,
            }
            index = node_map(data)
            for target in record.get("affects", {}).get("supersedes", []) or []:
                old = index.get(target)
                if old is None or old.get("status") != "CANON":
                    continue
                old["status"] = "SUPERSEDED"
                old["superseded_by"] = node_id
                old["stale"] = False
                old["stale_reason"] = None
                _append_history(old, at, "CANON", "SUPERSEDED", by, None, node_id)
        if verb == "review":
            current["stale"] = False
            current["stale_reason"] = None
        current["status"] = new_status
        _append_history(current, at, old_status, new_status, by, warrant, f"{verb} nonce {nonce}")
        txn.after.append(lambda: _unlink_quietly(pending_path))
        return f"{verb}: {node_id} {old_status} -> {new_status} ({method})"

    result = write_graph(top, mutate)  # regenerates MAP.md and CANON.md when MAP.md exists
    if result is None:
        raise Refused(NO_GRAPH)
    return result.label


# ---------------------------------------------------------------------------
# Importers: lore drafts and docs/adr directories
# ---------------------------------------------------------------------------


def _repo_rel(top: Path, value: str, what: str) -> str:
    """Return a CLI path (relative to the working directory) as a repo-relative posix path."""
    absolute = Path(os.path.abspath(value))
    path = Path(os.path.realpath(absolute.parent)) / absolute.name
    try:
        rel = path.relative_to(top).as_posix()
    except ValueError:
        raise UsageError(f"{what} {value} is outside the repository") from None
    if rel in ("", "."):
        raise UsageError(f"{what} {value} is the repository itself")
    return rel


def _draft_flag(front: Any) -> bool:
    value = front.get(DRAFT_KEY) if isinstance(front, dict) else None
    return value == 1 and not isinstance(value, bool)


def find_drafts(top: Path) -> list[str]:
    """Return the files under lore/ADRs/ and lore/STORIES/ whose frontmatter marks a draft."""
    found = []
    for folder in (TYPE_DIRS["decision"], TYPE_DIRS["story"]):
        directory = top / folder
        if _symlink_in(top, folder) is not None or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                front = hyper_spec.parse_frontmatter(_read_text(path)).data
            except (LoreError, hyper_spec.SpecError):
                continue
            if _draft_flag(front):
                found.append(f"{folder}/{path.name}")
    return found


def _timestamp(value: Any, default: str) -> str:
    """Return a timestamp written with its offset (`+00:00` for UTC), else default."""
    parsed = value if isinstance(value, datetime) else _parse_aware(value)
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        return default
    return parsed.isoformat()


def _opt_line(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return _printable(text) or None


def draft_node(top: Path, rel: str, at: str) -> dict:
    """Return the PROPOSED node of one draft (status forced; the file is never changed)."""
    folder = rel.rsplit("/", 1)[0]
    if _pointer_problem(rel) is not None or folder not in TYPE_DIRS.values():
        raise UsageError(f"{rel} is not a file under lore/ADRs/ or lore/STORIES/")
    if _symlink_in(top, rel) is not None or not (top / rel).is_file():
        raise UsageError(f"{rel} does not exist or is a symlink")
    raw = (top / rel).read_bytes()
    try:
        front = hyper_spec.parse_frontmatter(raw.decode("utf-8")).data
    except UnicodeDecodeError:
        raise UsageError(f"{rel} is not UTF-8 text") from None
    except hyper_spec.SpecError as exc:
        raise ValidationFailed(f"{rel}: {exc}") from None
    if not _draft_flag(front):
        raise UsageError(f"{rel} is not a lore draft (its frontmatter lacks `{DRAFT_KEY}: 1`)")
    problems: list[str] = []

    def checked(read: Callable[[dict, str], list[str]], value: Any, key: str) -> list[str]:
        try:
            return read({key: value if value is not None else []}, key)
        except UsageError as exc:
            problems.append(str(exc))
            return []

    nid, kind = front.get("id"), front.get("type")
    match = ID_RE.match(nid) if isinstance(nid, str) else None
    if kind not in TYPE_DIRS:
        problems.append("type must be decision or story")
    elif match is None or match.group(1) != TYPE_PREFIX[kind]:
        problems.append(f"id must be {TYPE_PREFIX[kind]}-NNNN")
    else:
        expected = _location_problem({"id": nid, "type": kind, "status": "PROPOSED"}, rel)
        if expected is not None:
            problems.append(f"the file of {nid} must be {expected}")
    title = front.get("title")
    if not isinstance(title, str) or not title.strip() or CONTROL_RE.search(title):
        problems.append("title must be a non-empty single line")
    origin = front.get("origin", "import")
    if origin not in ORIGINS:
        problems.append(f"origin must be one of {', '.join(ORIGINS)}")
    context = front.get("context")
    if context is not None and not isinstance(context, str):
        problems.append("context must be a string")
    groups = {}
    for group, keys in (("anchors", ANCHOR_KEYS), ("planned", PLANNED_KEYS)):
        value = front.get(group) or {}
        if not isinstance(value, dict):
            problems.append(f"{group} must be a mapping")
            value = {}
        groups[group] = {
            key: checked(
                _components if key == "components" else _globs if key == "files" else _str_list,
                value.get(key),
                f"{group}.{key}",
            )
            for key in keys
        }
    relates_to = checked(_lore_ids, front.get("relates_to"), "relates_to")
    supersedes = checked(_lore_ids, front.get("supersedes"), "supersedes")
    if problems:
        raise ValidationFailed(f"refused: {rel} is not an importable draft", problems)
    given = front.get("provenance") if isinstance(front.get("provenance"), dict) else {}
    created_at = _timestamp(given.get("created_at"), at)
    return {
        "id": nid,
        "type": kind,
        "title": title.strip(),
        "status": "PROPOSED",
        "origin": origin,
        "context": context,
        "pointer": {"path": rel},
        "content_sha256": _sha_bytes(raw),
        "anchors": groups["anchors"],
        "planned": groups["planned"],
        "relates_to": relates_to,
        "supersedes": supersedes,
        "superseded_by": None,
        "stale": False,
        "stale_reason": None,
        "legacy": None,
        "provenance": {
            "created_by": _opt_line(given.get("created_by")) or "import",
            "created_at": created_at,
            "source": _opt_line(given.get("source")),
            "session_id": _opt_line(given.get("session_id")),
            "run_id": _opt_line(given.get("run_id")),
        },
        "approval": None,
        "history": [
            {
                "at": created_at,
                "from": None,
                "to": "PROPOSED",
                "by": "import",
                "warrant": None,
                "ref": f"import --drafts {rel}",
            }
        ],
    }


def _raise_counter(data: dict, node_id: str) -> None:
    """Raise id_counters to the number of node_id (counters never decrease)."""
    match = ID_RE.match(node_id)
    if match is None:
        return
    counters = data.setdefault("id_counters", {"ADR": 0, "STORY": 0, "CANON": 0})
    current = counters.get(match.group(1))
    counters[match.group(1)] = max(current if isinstance(current, int) else 0, int(match.group(2)))


def do_import_drafts(top: Path, rels: list[str]) -> list[str]:
    """Import draft files as PROPOSED nodes, then resolve anchors; return the report lines."""
    at = _runtime.utc_now_iso()
    nodes = [draft_node(top, rel, at) for rel in dict.fromkeys(rels)]
    ids = [node["id"] for node in nodes]
    repeated = sorted({nid for nid in ids if ids.count(nid) > 1})
    if repeated:
        raise UsageError(f"refused: more than one draft claims {', '.join(repeated)}")
    report: list[str] = []

    def mutate(data: dict, txn: _Txn) -> str:
        index = node_map(data)
        conflicts = []
        for node in nodes:
            old = index.get(node["id"])
            if old is not None and (
                old.get("pointer") != node["pointer"]
                or old.get("content_sha256") != node["content_sha256"]
            ):
                conflicts.append(
                    f"{node['id']}: the graph has {old.get('pointer')} sha "
                    f"{str(old.get('content_sha256'))[:12]}; {node['pointer']['path']} has sha "
                    f"{node['content_sha256'][:12]} (run `hyper_lore.py sync` after editing a "
                    "PROPOSED body, or renumber the colliding id)"
                )
        if conflicts:
            raise Conflict("refused: a draft differs from its node; nothing was written", conflicts)
        added = []
        for node in _by_id(nodes):
            if node["id"] in index:
                report.append(f"skipped {node['id']}: already imported with the same content")
                continue
            data["nodes"].append(node)
            _raise_counter(data, node["id"])
            added.append(node["id"])
            report.append(f"imported {node['id']} (PROPOSED) from {node['pointer']['path']}")
        return f"import --drafts: {len(added)} imported, {len(nodes) - len(added)} skipped"

    result = write_graph(top, mutate, create=True)
    assert result is not None  # create=True always writes
    resolved = do_resolve_anchors(top)
    report.append(result.label)
    if resolved is not None:
        report.append(resolved.label)
    return report


class LegacyAdr(NamedTuple):
    """One `NNNN-slug.md` of a docs/adr directory, read for import."""

    rel: str  # repo-relative path of the file
    folder: str  # repo-relative path of its directory
    number: int
    slug: str
    text: str
    title: str
    status: str  # the graph status it maps to
    legacy_status: str | None
    successor: int | None  # the legacy number of the ADR that supersedes it


def legacy_status_text(text: str) -> str | None:
    """Return a legacy ADR's status: frontmatter `status`, a `Status:` line or `## Status`."""
    try:
        front = hyper_spec.parse_frontmatter(text)
        data, body = front.data, front.body
    except hyper_spec.SpecError:
        data, body = None, text
    if isinstance(data, dict) and data.get("status") is not None:
        return str(data["status"])
    lines = body.split("\n")
    in_section = False
    for line, code in zip(lines, hyper_spec._fenced(lines)):
        if code:
            continue
        stripped = line.strip()
        heading = HEADING_RE.match(stripped) if stripped.startswith("#") else None
        if heading:
            if in_section:
                return None  # an empty Status section
            in_section = heading.group(1).strip().casefold() == "status"
            continue
        if in_section and stripped:
            return stripped
        match = STATUS_LINE_RE.match(line)
        if match and match.group(1):
            return match.group(1)
    return None


def map_legacy_status(raw: str | None) -> tuple[str, str | None, int | None]:
    """Map a legacy status to (graph status, legacy.status word, superseding legacy number)."""
    clean = re.sub(r"[*`]", "", raw or "").strip().casefold()
    word = re.match(r"[a-z]+", clean)
    if word is None:
        return "PROPOSED", None, None
    first = word.group(0)
    if first == "superseded":
        found = re.search(r"\bby\b\D*?(\d+)", clean)
        return "SUPERSEDED", first, int(found.group(1)) if found else None
    if first == "deprecated":
        return "RETIRED", first, None
    return "PROPOSED", first, None  # proposed, accepted (Q36) and anything else


def _legacy_title(text: str, slug: str) -> str:
    """Return a legacy ADR's H1 without its `N.` numbering, else its slug as words."""
    try:
        body = hyper_spec.parse_frontmatter(text).body
    except hyper_spec.SpecError:
        body = text
    lines = body.split("\n")
    for line, code in zip(lines, hyper_spec._fenced(lines)):
        if not code and line.startswith("# "):
            title = re.sub(r"^\d+\.\s*", "", " ".join(_printable(line[2:]).split()))
            if title:
                return title
    return " ".join(slug.replace("-", " ").replace("_", " ").split()) or "Untitled decision"


def legacy_adrs(top: Path, folder: str) -> tuple[list[LegacyAdr], list[str]]:
    """Return the ADR files of one directory in number order, and lines for skipped files.

    Redirect stubs (files already moved) are skipped silently; other unreadable files are
    left in place and reported.
    """
    entries, report = [], []
    for path in sorted((top / folder).iterdir(), key=lambda p: p.name):
        match = LEGACY_ADR_RE.match(path.name)
        if match is None:
            continue
        rel = f"{folder}/{path.name}"
        if path.is_symlink() or not path.is_file():
            report.append(f"skipped {rel}: not a regular file (left in place)")
            continue
        try:
            text = _read_text(path)
        except UsageError:
            report.append(f"skipped {rel}: not UTF-8 text (left in place)")
            continue
        if text.startswith(REDIRECT_MARK):
            continue
        status, word, successor = map_legacy_status(legacy_status_text(text))
        number, slug = int(match.group(1)), match.group(2)
        entries.append(
            LegacyAdr(
                rel, folder, number, slug, text, _legacy_title(text, slug), status, word, successor
            )
        )
    entries.sort(key=lambda entry: (entry.number, entry.rel))
    return entries, report


def _relative_link(target: str, folder: str) -> str:
    """Return the posix path from a repo-relative folder to a repo-relative target."""
    return os.path.relpath(target, folder or ".").replace(os.sep, "/")


def redirect_stub(entry: LegacyAdr, node: dict) -> str:
    """Return the stub left at a moved ADR's old path (its first line marks it as a stub)."""
    rel = node["pointer"]["path"]
    return (
        f"{REDIRECT_MARK}{rel} -->\n"
        f"# Moved: {entry.title}\n\n"
        f"This ADR moved, byte-for-byte, to [{rel}]({_relative_link(rel, entry.folder)}). "
        f"It is lore node {node['id']} in `lore_hypergraph.yml`, imported by "
        "`hyper_lore.py import --adr-dirs`.\n"
    )


def readme_stub(folder: str) -> str:
    """Return the README.md left in an imported docs/adr directory."""
    target = TYPE_DIRS["decision"]
    return (
        f"{REDIRECT_MARK}{target}/ -->\n"
        f"# These decision records moved to {target}/\n\n"
        "The architecture decision records of this directory were imported into the HACF lore "
        f"graph (`lore_hypergraph.yml`). Their bodies now live, byte-for-byte, in "
        f"[{target}/]({_relative_link(target, folder)}/); each file left here is a redirect "
        "stub naming its new path. Propose new decisions with "
        "`python3 .agents/scripts/hyper_lore.py propose --type decision`.\n"
    )


def _undo_write(path: Path, written: str, original: str | None) -> Callable[[], None]:
    """Return an undo step that puts back original (None: removes the file it created)."""

    def undo() -> None:
        with contextlib.suppress(LoreError, OSError):
            if original is None:
                _unlink_quietly(path)
            else:
                _cas_write(path, written, original)

    return undo


def _check_adr_dir(top: Path, folder: str) -> None:
    if folder == "lore" or folder.startswith("lore/"):
        raise UsageError(f"--adr-dirs {folder} is inside lore/; import a docs/adr directory")
    if _symlink_in(top, folder) is not None:
        raise Refused(f"--adr-dirs {folder} is or passes through a symlink")
    if not (top / folder).is_dir():
        raise UsageError(f"--adr-dirs {folder} is not a directory")


def _legacy_index(data: dict) -> dict[tuple[str, int], str]:
    """Map (source directory, legacy number) to the node id of every imported legacy ADR."""
    found: dict[tuple[str, int], str] = {}
    for node in nodes_of(data):
        legacy = node.get("legacy")
        source = legacy.get("source") if isinstance(legacy, dict) else None
        if not isinstance(source, str) or not isinstance(node.get("id"), str):
            continue
        folder, _, name = source.rpartition("/")
        match = LEGACY_ADR_RE.match(name)
        if match:
            found.setdefault((folder, int(match.group(1))), node["id"])
    return found


def do_import_adr_dirs(top: Path, folders: list[str], context: str | None) -> list[str]:
    """Import the docs/adr directories given (only those) and return the report lines."""
    context = _single_line(context, "--context") if context is not None else None
    at = _runtime.utc_now_iso()
    run_id = os.environ.get(RUN_ID_ENV) or None
    report: list[str] = []
    batches: list[tuple[str, list[LegacyAdr]]] = []
    for folder in dict.fromkeys(folders):
        _check_adr_dir(top, folder)
        entries, skipped = legacy_adrs(top, folder)
        report.extend(skipped)
        batches.append((folder, entries))

    def mutate(data: dict, txn: _Txn) -> str:
        known = _legacy_index(data)
        taken = set(node_map(data))
        created: list[tuple[LegacyAdr, dict]] = []
        for _, entries in batches:
            for entry in entries:
                node_id = f"ADR-{entry.number:04d}"
                slug = slugify(entry.slug)
                rel = f"{TYPE_DIRS['decision']}/{node_id}{'-' + slug if slug else ''}.md"
                if node_id in taken or os.path.lexists(top / rel):
                    node_id = _allocate(data, "ADR")
                    rel = f"{TYPE_DIRS['decision']}/{node_id}{'-' + slug if slug else ''}.md"
                    report.append(
                        f"renumbered {entry.rel}: ADR-{entry.number:04d} is taken; it is {node_id}"
                    )
                else:
                    _raise_counter(data, node_id)
                if os.path.lexists(top / rel):
                    raise Conflict(f"refused: {rel} already exists; nothing was written")
                _ensure_lore_dir(top, TYPE_DIRS["decision"])
                _cas_write(top / rel, None, entry.text)
                txn.undo.append(_undo_write(top / rel, entry.text, None))
                taken.add(node_id)
                known[(entry.folder, entry.number)] = node_id
                node = {
                    "id": node_id,
                    "type": "decision",
                    "title": entry.title,
                    "status": entry.status,
                    "origin": "import",
                    "context": context,
                    "pointer": {"path": rel},
                    "content_sha256": _sha_text(entry.text),
                    "anchors": {"components": [], "files": [], "symbols": []},
                    "planned": {"components": [], "files": []},
                    "relates_to": [],
                    "supersedes": [],
                    "superseded_by": None,
                    "stale": False,
                    "stale_reason": None,
                    "legacy": {"source": entry.rel, "status": entry.legacy_status},
                    "provenance": {
                        "created_by": "import",
                        "created_at": at,
                        "source": entry.rel,
                        "session_id": None,
                        "run_id": run_id,
                    },
                    "approval": None,
                    "history": [
                        {
                            "at": at,
                            "from": None,
                            "to": entry.status,
                            "by": "import",
                            "warrant": None,
                            "ref": f"import --adr-dirs {entry.rel}",
                        }
                    ],
                }
                data["nodes"].append(node)
                created.append((entry, node))
        index = node_map(data)
        for entry, node in created:
            if node["status"] != "SUPERSEDED":
                continue
            successor = known.get((entry.folder, entry.successor))
            if successor is None or successor == node["id"] or successor not in index:
                node["status"] = node["history"][0]["to"] = "PROPOSED"
                report.append(
                    f"{entry.rel}: its superseding ADR was not imported from {entry.folder}; "
                    "imported as PROPOSED (legacy.status superseded)"
                )
                continue
            node["superseded_by"] = successor
            supersedes = index[successor].get("supersedes")
            if isinstance(supersedes, list) and node["id"] not in supersedes:
                supersedes.append(node["id"])
        for entry, node in created:
            if node["status"] in ("RETIRED", "SUPERSEDED"):
                _place_canon_file(top, node, entry.text, txn)  # R8: every ex-canon status
            stub = redirect_stub(entry, node)
            _cas_write(top / entry.rel, entry.text, stub)
            txn.undo.append(_undo_write(top / entry.rel, stub, entry.text))
            legacy = f", legacy.status {entry.legacy_status}" if entry.legacy_status else ""
            report.append(f"imported {entry.rel} -> {node['id']} ({node['status']}{legacy})")
        moved = {entry.folder for entry, _ in created}
        for folder, _ in batches:
            readme = top / folder / "README.md"
            try:
                current = _read_optional(readme) if not readme.is_symlink() else ""
            except (UsageError, OSError):
                current = ""  # unreadable: kept, like any README that is not an HACF stub
            if current is not None and current.startswith(REDIRECT_MARK):
                continue
            if current is not None:
                report.append(f"kept {folder}/README.md (it is not an HACF stub)")
            elif folder in moved:
                stub = readme_stub(folder)
                _cas_write(readme, None, stub)
                txn.undo.append(_undo_write(readme, stub, None))
                report.append(f"wrote the stub {folder}/README.md")
        return f"import --adr-dirs: {len(created)} imported"

    result = write_graph(top, mutate, create=True)
    assert result is not None  # create=True always writes
    report.append(result.label)
    return report


# ---------------------------------------------------------------------------
# Merge drivers: hacf-lore (the graph) and hacf-generated (MAP.md, CANON.md)
# ---------------------------------------------------------------------------

_MISSING = object()
_CONFLICT = object()
LIST_KEYS = ("relates_to", "supersedes")
GROUP_KEYS = ("anchors", "planned")


def _merge_scalar(base: Any, ours: Any, theirs: Any) -> Any:
    """3-way merge of one value; _CONFLICT when both sides changed it differently."""
    if ours == theirs:
        return ours
    if base is not _MISSING and ours == base:
        return theirs
    if base is not _MISSING and theirs == base:
        return ours
    return _CONFLICT


def _merge_list(base: Any, ours: Any, theirs: Any) -> list:
    """3-way merge of a list as a set: what either side removed goes, what either added stays."""
    b, o, t = (value if isinstance(value, list) else [] for value in (base, ours, theirs))
    removed = [item for item in b if item not in o or item not in t]
    merged: list = []
    for item in o + t:
        if item not in removed and item not in merged:
            merged.append(item)
    return merged


def _merge_history(ours: Any, theirs: Any) -> list:
    """Return the union of two histories, de-duplicated and ordered by `at` (stable)."""
    seen, merged = set(), []
    for entry in (ours if isinstance(ours, list) else []) + (
        theirs if isinstance(theirs, list) else []
    ):
        key = json.dumps(entry, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            merged.append(entry)
    return sorted(
        merged,
        key=lambda e: (_parse_aware(e.get("at")) if isinstance(e, dict) else None) or EPOCH,
    )


def _merge_node(nid: str, base: dict | None, ours: dict, theirs: dict, problems: list[str]) -> dict:
    """3-way merge of one node present on both sides; double changes go to problems."""
    merged: dict = {}
    for key in dict.fromkeys([*ours, *theirs]):
        b = base.get(key, _MISSING) if base is not None else _MISSING
        o, t = ours.get(key, _MISSING), theirs.get(key, _MISSING)
        if key == "history":
            value: Any = _merge_history(o, t)
        elif key in LIST_KEYS:
            value = _merge_list(b, o, t)
        elif key in GROUP_KEYS and isinstance(o, dict) and isinstance(t, dict):
            b_group = b if isinstance(b, dict) else {}
            value = {
                sub: _merge_list(b_group.get(sub), o.get(sub), t.get(sub))
                for sub in dict.fromkeys([*o, *t])
            }
        else:
            value = _merge_scalar(b, o, t)
            if value is _CONFLICT and key == "stale_reason":
                value = o if o is not None else t  # stale itself never conflicts
            elif value is _CONFLICT:
                if key == "status":
                    was = b if b is not _MISSING else "(added on both sides)"
                    problems.append(f"status conflict on {nid}: base {was}, ours {o}, theirs {t}")
                else:
                    problems.append(f"conflict on {nid}: both sides changed {key} differently")
                value = o
        if value is not _MISSING:
            merged[key] = value
    return merged


def _structure_errors(data: dict) -> set[str]:
    """Return the field-level errors of a graph (validate's checks that need no files)."""
    out = _Checker()
    _check_top(out, data)
    counters = data.get("id_counters") if isinstance(data.get("id_counters"), dict) else {}
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    for position, node in enumerate(nodes):
        _check_node(out, position, node, counters)
    return {finding.line() for finding in out.findings if finding.severity == "error"}


def merge_graphs(base: dict | None, ours: dict, theirs: dict) -> tuple[dict, list[str]]:
    """Merge three graphs by id; return (merged, problems). Any problem is a conflict.

    Nodes are never deleted: a node one side lacks is taken from the other. A node added on
    both sides with another pointer or content_sha256 is an id collision (renumber one).
    """
    problems = [
        f"{label}: the schema is not {SCHEMA}"
        for label, data in (("ours", ours), ("theirs", theirs))
        if data.get("schema") != SCHEMA
    ]
    b_index, o_index, t_index = node_map(base), node_map(ours), node_map(theirs)
    nodes = []
    for nid in dict.fromkeys([*o_index, *t_index, *b_index]):
        b, o, t = b_index.get(nid), o_index.get(nid), t_index.get(nid)
        if o is None or t is None or o == t:
            nodes.append(copy.deepcopy(o or t or b))
            continue
        same_body = o.get("content_sha256") == t.get("content_sha256")
        if b is None and (o.get("pointer") != t.get("pointer") or not same_body):
            problems.append(
                f"id collision: {nid} was added on both sides with another pointer or content "
                f"(ours {o.get('pointer')}, theirs {t.get('pointer')}); renumber one side's node "
                f"with `hyper_lore.py renumber {nid} <new id>`"
            )
            nodes.append(copy.deepcopy(o))
            continue
        nodes.append(_merge_node(nid, b, o, t, problems))
    counters = {}
    for prefix in PREFIX_TYPE:
        values = [
            data["id_counters"].get(prefix)
            for data in (base, ours, theirs)
            if isinstance(data, dict) and isinstance(data.get("id_counters"), dict)
        ]
        counters[prefix] = max(
            [v for v in values if isinstance(v, int) and not isinstance(v, bool)], default=0
        )
    merged = {"schema": SCHEMA, "id_counters": counters, "nodes": nodes}
    known = _structure_errors(ours) | _structure_errors(theirs)
    problems.extend(
        f"the merged graph would hold an error: {line}"
        for line in sorted(_structure_errors(merged) - known)
    )
    return merged, problems


def _read_side(path: str, label: str) -> dict | None:
    """Parse one merge-driver input; an empty file (no common ancestor) is None."""
    try:
        text = _read_text(Path(path))
    except OSError as exc:
        raise UsageError(f"{label} {path} is not readable ({exc})") from None
    return parse_graph(text, label) if text.strip() else None


def merge_head(top: Path) -> str | None:
    """Return the commit being merged in, or None for rebase, cherry-pick, octopus or no merge.

    `git merge` exports one GITHEAD_<sha> variable to merge drivers (MERGE_HEAD is written
    only after the drivers ran); MERGE_HEAD is the fallback.
    """
    heads = sorted({m.group(1) for key in os.environ if (m := GITHEAD_RE.match(key))})
    if len(heads) == 1:
        return heads[0]
    if heads:
        return None
    proc = _git(["rev-parse", "-q", "--verify", "MERGE_HEAD^{commit}"], top)
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def merged_state(top: Path) -> tuple[dict, Callable[[str], str]] | None:
    """Return (merged graph, canon reader) of the merge in progress, or None.

    git (merge-ort) runs drivers before it writes the merged graph or the other side's new
    canon files to the work tree, so both come from git objects: the graph is merged from
    HEAD, the merge head and their single merge base with merge_graphs; a canon file is read
    from either side (the two must agree), else from the work tree.
    """
    theirs = merge_head(top)
    if theirs is None:
        return None
    bases = _git(["merge-base", "--all", "HEAD", theirs], top).stdout.split()
    if len(bases) > 1:
        return None  # a criss-cross merge: index rebuilds after it
    texts = [_git_blob(top, rev, GRAPH_REL) for rev in (*bases[:1], "HEAD", theirs)]
    base_text = texts[0] if bases else None
    ours_text, theirs_text = texts[-2], texts[-1]
    if ours_text is None and theirs_text is None:
        return None
    try:
        graphs = [
            parse_graph(text) if text is not None and text.strip() else None
            for text in (base_text, ours_text, theirs_text)
        ]
    except ValidationFailed:
        return None
    data, problems = merge_graphs(graphs[0], graphs[1] or empty_graph(), graphs[2] or empty_graph())
    if problems:
        return None

    def read(node_id: str) -> str:
        rel = _canon_rel(node_id)
        found = {_git_blob(top, rev, rel) for rev in ("HEAD", theirs)} - {None}
        if len(found) > 1:
            raise Conflict(f"{rel} differs between the two sides")
        if found:
            return found.pop()
        return _canon_reader(top)(node_id)

    return data, read


def _strip_region(text: str) -> str:
    """Return MAP.md text with its generated region emptied (markers kept), when it has one."""
    begin = f"<!-- BEGIN HACF GENERATED: {MAP_REGION} -->"
    if not any(line.rstrip("\r") == begin for line in text.split("\n")):
        return text
    try:
        return hyper_scope.set_generated_region(text, MAP_REGION, [])
    except hyper_spec.SpecError as exc:
        raise Conflict(f"{MAP_REL}: {exc}") from None


def merge_file(top: Path, base: str, ours: str, theirs: str) -> tuple[str, bool]:
    """Run `git merge-file -p` on three texts; return (result, clean)."""
    with tempfile.TemporaryDirectory(prefix="hacf-lore-merge-") as folder:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(folder) / name
            _write_plain(path, text)
            paths.append(str(path))
        try:
            proc = subprocess.run(
                ["git", "merge-file", "-p", "-L", "ours", "-L", "base", "-L", "theirs", *paths],
                cwd=str(top),
                capture_output=True,
                timeout=GIT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refused(f"git merge-file failed: {exc}") from None
    if proc.returncode >= 128 or proc.returncode < 0:
        raise Refused(f"git merge-file failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    try:
        return proc.stdout.decode("utf-8"), proc.returncode == 0
    except UnicodeDecodeError:
        raise Refused("git merge-file printed text that is not UTF-8") from None


def _keep_ours(top: Path, rel: str, why: str) -> int:
    """The hacf-generated fallback: keep ours, touch needs-index, exit 0 (R8)."""
    _flag_needs_index(top)
    print(
        f"merge-generated: {rel}: kept ours ({why}); {NEEDS_INDEX_REL} is set, so validate "
        f"fails until `hyper_lore.py index` runs after the merge",
        file=sys.stderr,
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# renumber and install-drivers
# ---------------------------------------------------------------------------


def _renamed_rel(rel: str, old: str, new: str) -> str:
    """Return a pointer-like path with the leading old id of its file name replaced by new."""
    folder, _, name = rel.rpartition("/")
    if name == f"{old}.md" or name.startswith(f"{old}-"):
        return f"{folder}/{new}{name[len(old) :]}"
    return rel


def _mentions(text: str, node_id: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(node_id)}(?!\d)", text) is not None


def lore_ref_writes(top: Path, old: str, new: str) -> list[hyper_spec.Write]:
    """Plan the `lore_refs` rewrites of missions/**/SPEC.md (mission and slice frontmatter)."""
    with _spec_errors():
        mroot = hyper_spec.missions_root(top)
        writes = []
        for path in hyper_spec.spec_files(mroot):
            if path.is_symlink():
                continue
            text = _read_text(path)
            refs = (hyper_spec.parse_frontmatter(text).data or {}).get("lore_refs")
            if not isinstance(refs, list) or old not in refs:
                continue
            new_refs = list(dict.fromkeys(new if ref == old else ref for ref in refs))
            kind = hyper_spec.MISSION if path.parent.parent == mroot else hyper_spec.SLICE
            writes.append(
                hyper_spec.Write(
                    path, text, hyper_spec.splice_owned(text, kind, {"lore_refs": new_refs})
                )
            )
    return writes


def commit_mentions(top: Path, node_id: str) -> list[str]:
    """Return `<short sha> <subject>` for each commit on HEAD whose message names node_id."""
    proc = _git(["log", "-F", f"--grep={node_id}", "--format=%H%x1f%B%x1e"], top)
    found = []
    for record in proc.stdout.split("\x1e") if proc.returncode == 0 else []:
        sha, _, message = record.strip().partition("\x1f")
        if sha and _mentions(message, node_id):
            subject = message.strip().splitlines()[0] if message.strip() else ""
            found.append(f"{sha[:12]} {_summary(subject)}")
    return found


def do_renumber(top: Path, old: str, new: str) -> list[str]:
    """Rename one node and rewrite every in-repo reference to it; return the report lines."""
    _refuse_outbox("renumber")
    old_match, new_match = ID_RE.match(old), ID_RE.match(new)
    if old_match is None or new_match is None:
        raise UsageError("renumber takes two lore ids (ADR-NNNN, STORY-NNNN or CANON-NNNN)")
    if old_match.group(1) != new_match.group(1):
        raise UsageError(f"refused: {old} and {new} have different prefixes (types never change)")
    if old == new:
        raise UsageError("renumber: the old and the new id are the same")
    if os.path.lexists(_pending_dir(top) / f"{old}.json"):
        raise Refused(
            f"refused: an approval request is pending for {old}; cancel it first with "
            f"`hyper_lore.py approval-cancel {old}`"
        )
    report: list[str] = []

    def mutate(data: dict, txn: _Txn) -> str:
        index = node_map(data)
        if new in index:
            raise Conflict(f"refused: {new} already exists; nothing was written")
        node = index.get(old)
        if node is None:
            raise UsageError(f"no lore node {old}")
        pointer = node.get("pointer")
        rel = pointer.get("path") if isinstance(pointer, dict) else None
        if _pointer_problem(rel) is not None:
            raise Refused(f"{old}: pointer {rel!r} {_pointer_problem(rel)}")
        moves = [(rel, _renamed_rel(rel, old, new))]
        if rel != _canon_rel(old) and os.path.lexists(top / _canon_rel(old)):
            moves.append((_canon_rel(old), _canon_rel(new)))
        for src, dst in moves:
            if src == dst:
                raise Refused(f"{src} does not start with {old}; rename it by hand first")
            if _symlink_in(top, src) is not None or not (top / src).is_file():
                raise Refused(f"{src} is missing or a symlink; nothing was written")
            if os.path.lexists(top / dst):
                raise Conflict(f"refused: {dst} already exists; nothing was written")
        for src, dst in moves:
            os.rename(top / src, top / dst)  # byte-for-byte; canon bodies are never rewritten
            txn.undo.append(lambda s=top / src, d=top / dst: os.rename(d, s))
            report.append(f"renamed {src} -> {dst}")
        node["id"] = new
        node["pointer"] = {"path": moves[0][1]}
        _raise_counter(data, new)
        for other in nodes_of(data):
            for key in LIST_KEYS:
                items = other.get(key)
                if isinstance(items, list) and old in items:
                    other[key] = list(dict.fromkeys(new if item == old else item for item in items))
                    report.append(f"rewrote {key} of {other.get('id')}")
            if other.get("superseded_by") == old:
                other["superseded_by"] = new
                report.append(f"rewrote superseded_by of {other.get('id')}")
        writes = lore_ref_writes(top, old, new)
        with _spec_errors():
            hyper_spec.apply_writes(writes)
        for write in writes:
            txn.undo.append(_undo_write(write.path, write.text, write.original))
            report.append(f"rewrote lore_refs in {write.path.relative_to(top).as_posix()}")
        return f"renumber: {old} -> {new}"

    with _spec_errors(), hyper_spec.spec_lock(top):
        result = write_graph(top, mutate, renamed={old: new})
    if result is None:
        raise UsageError(NO_GRAPH)
    report.append(result.label)
    for line in commit_mentions(top, old):
        report.append(f"reported, not rewritten: commit {line} names {old}")
    lore_dir = top / "lore"
    for path in sorted(lore_dir.rglob("*.md")) if lore_dir.is_dir() else []:
        if path.is_file() and not path.is_symlink():
            with contextlib.suppress(LoreError, OSError):
                if _mentions(_read_text(path), old):
                    report.append(
                        f"reported, not rewritten: {path.relative_to(top).as_posix()} names {old} "
                        "(bodies are verbatim)"
                    )
    return report


def set_hash_block(text: str, block: str, rows: list[str]) -> str:
    """Return a hash-comment file's text with its HACF managed block set to rows (R26).

    Markers are exact `# BEGIN|END HACF MANAGED BLOCK: <id>` lines at column 0; duplicate,
    nested or unbalanced HACF markers are a Conflict (exit 5). A missing block is appended.
    EOL style, a UTF-8 BOM, the final-newline state and every byte outside the block are kept.
    """
    bom, rest = (text[:1], text[1:]) if text.startswith(hyper_scope.BOM) else ("", text)
    lines, cr = rest.split("\n"), "\r" if "\r\n" in rest else ""
    span, open_key, seen, start = None, None, set(), None
    for number, line in enumerate(lines):
        match = HASH_MARKER_RE.match(line.rstrip("\r"))
        if match is None:
            continue
        key = (match.group(2), match.group(3))
        name = f"HACF {key[0]}: {key[1]}"
        if match.group(1) == "BEGIN":
            if open_key is not None:
                raise Conflict(f"{name} is nested in HACF {open_key[0]}: {open_key[1]}")
            if key in seen:
                raise Conflict(f"duplicate {name} markers")
            seen.add(key)
            open_key, start = key, number
        elif open_key != key:
            raise Conflict(f"unbalanced {name} markers (END without its BEGIN)")
        else:
            if key == ("MANAGED BLOCK", block):
                span = (start, number)
            open_key = None
    if open_key is not None:
        raise Conflict(f"unbalanced HACF {open_key[0]}: {open_key[1]} markers (no END)")
    begin, end = f"# BEGIN HACF MANAGED BLOCK: {block}", f"# END HACF MANAGED BLOCK: {block}"
    # Each element carries its own cr, so "\n".join() gives cr + "\n" between lines.
    new = [line + cr for line in (begin, *rows, end)]
    if span is not None:
        tail = lines[span[1] + 1 :]
        if not tail:
            new[-1] = end  # the block ends a file that has no final newline
        return bom + "\n".join(lines[: span[0]] + new + tail)
    head = rest if not rest or rest.endswith("\n") else rest + cr + "\n"
    result = head + "\n".join(new) + "\n"
    if rest and not rest.endswith("\n"):
        result = result[: -len(cr + "\n")]
    return bom + result


def do_install_drivers(top: Path) -> list[str]:
    """Write the repo-local merge-driver config and the info/attributes block (R10)."""
    _refuse_outbox("install-drivers")
    report = []
    for key, value in DRIVER_CONFIG:
        current = _git(["config", "--local", "--get", key], top)
        if current.returncode == 0 and current.stdout.rstrip("\n") == value:
            continue
        proc = _git(["config", "--local", "--replace-all", key, value], top)
        if proc.returncode != 0:
            raise Refused(f"git config --local {key} failed: {proc.stderr.strip()}")
        report.append(f"set git config --local {key}")
    info = common_dir(top) / "info"
    attributes = info / "attributes"
    if info.is_symlink() or attributes.is_symlink():
        raise Refused(f"{attributes} is or passes through a symlink; nothing was written")
    old = _read_optional(attributes)
    new = set_hash_block(old or "", ATTRIBUTES_BLOCK, list(ATTRIBUTE_LINES))
    where = os.path.relpath(attributes, top) if attributes.is_relative_to(top) else str(attributes)
    if new != old:
        info.mkdir(parents=True, exist_ok=True)
        _cas_write(attributes, old, new)
        report.append(f"wrote the {ATTRIBUTES_BLOCK} block to {where}")
    if not report:
        report.append(f"install-drivers: no change ({where} and git config are current)")
    return report


# ---------------------------------------------------------------------------
# Read-only commands and their helpers
# ---------------------------------------------------------------------------


def canon_commits(top: Path, ids: list[str]) -> dict[str, str]:
    """Map each CANON id to the oldest commit whose lore_hypergraph.yml has it CANON (R23.5)."""
    proc = _git(["log", "--format=%H", "--", GRAPH_REL], top)
    if proc.returncode != 0:
        return {}
    remaining, found = set(ids), {}
    for sha in reversed(proc.stdout.split()):
        if not remaining:
            break
        shown = _git(["show", f"{sha}:{GRAPH_REL}"], top)
        if shown.returncode != 0:
            continue
        try:
            data = yaml.safe_load(shown.stdout)
        except yaml.YAMLError:
            continue
        for nid, node in node_map(data if isinstance(data, dict) else None).items():
            if nid in remaining and node.get("status") == "CANON":
                found[nid] = sha
                remaining.discard(nid)
    return found


def graph_edges(data: dict) -> list[dict]:
    """Return the edge list: the anchors hyperedge, relates_to, supersedes and superseded_by."""
    edges: list[dict] = []
    for node in nodes_of(data):
        nid = node.get("id")
        anchors = {key: _anchor_list(node, "anchors", key) for key in ANCHOR_KEYS}
        if any(anchors.values()):
            edges.append({"kind": "anchors", "from": nid, **anchors})
        for key in ("relates_to", "supersedes"):
            edges.extend({"kind": key, "from": nid, "to": t} for t in _id_list(node.get(key)))
        if isinstance(node.get("superseded_by"), str):
            edges.append({"kind": "superseded_by", "from": nid, "to": node["superseded_by"]})
    return edges


def _with_binding(node: dict) -> dict:
    return {**node, "binding": binding(node)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_validate(ns: argparse.Namespace) -> int:
    """Validate the graph (read-only); exit 1 on an error (on any warning with --strict).

    The text report is one line per finding, one per CANON node for the gated stale review and
    a fixed status line, with no counts or absolute paths: v060_gate.py finish compares these
    lines between this script's scope-base copy and the working tree. The index findings
    (MAP.md, CANON.md, needs-index) are checked here, not in validate_graph, so a graph write
    is never refused for a MAP.md that only the write itself makes stale.
    """
    top = repo_top()
    try:
        _, data = read_graph(top)
    except ValidationFailed as exc:
        if ns.json:
            print(json.dumps({"ok": False, "parse_error": str(exc)}, indent=2))
        else:
            print(f"error: {exc} [parse_error]")
            print("validate: failed")
        return EXIT_VALIDATION
    if data is None:
        if ns.json:
            print(json.dumps({"ok": True, "notice": NO_GRAPH, "errors": [], "warnings": []}))
        else:
            print(NO_GRAPH)
        return EXIT_OK
    report = validate_graph(data, top)
    report = Report(_sorted_findings(report.findings + index_findings(top, data)), report.review)
    errors = _errors(report)
    warnings = [f for f in report.findings if f.severity == "warning"]
    failed = bool(errors) or (ns.strict and bool(warnings))
    if ns.json:
        print(
            json.dumps(
                {
                    "ok": not failed,
                    "strict": ns.strict,
                    "errors": [f.as_json() for f in errors],
                    "warnings": [f.as_json() for f in warnings],
                    "stale_review": [{"id": i, "reasons": r} for i, r in report.review],
                },
                indent=2,
            )
        )
        return EXIT_VALIDATION if failed else EXIT_OK
    for finding in report.findings:
        print(finding.line())
    for nid, reasons in report.review:
        print(f"stale review (gated): {nid}: {'; '.join(reasons)}")
    print(f"validate{' --strict' if ns.strict else ''}: {'failed' if failed else 'ok'}")
    return EXIT_VALIDATION if failed else EXIT_OK


def cmd_show(ns: argparse.Namespace) -> int:
    """Print one node, with its binding flag."""
    _, data = read_graph(repo_top())
    if data is None:
        print(NO_GRAPH)
        return EXIT_OK
    node = node_map(data).get(ns.id)
    if node is None:
        raise UsageError(f"no lore node {ns.id}")
    print(dump_graph(_with_binding(node)), end="")
    return EXIT_OK


def cmd_list(ns: argparse.Namespace) -> int:
    """List nodes (optionally by status or stale), with binding and the CANON commit."""
    top = repo_top()
    _, data = read_graph(top)
    if data is None:
        if ns.json:
            print("[]")
            print(NO_GRAPH, file=sys.stderr)
        else:
            print(NO_GRAPH)
        return EXIT_OK
    nodes = [
        node
        for node in nodes_of(data)
        if (ns.status is None or node.get("status") == ns.status)
        and (not ns.stale or node.get("stale") is True)
    ]
    commits: dict[str, str] = {}
    if ns.with_commit:
        canon = [str(n.get("id")) for n in nodes if n.get("status") == "CANON"]
        commits = canon_commits(top, canon)
    if ns.json:
        rows = []
        for node in nodes:
            row = _with_binding(node)
            if ns.with_commit:
                row["canon_commit"] = commits.get(str(node.get("id")))
            rows.append(row)
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    for node in nodes:
        nid = str(node.get("id"))
        columns = [
            f"{nid:<11}",
            f"{str(node.get('status')):<10}",
            f"{str(node.get('type')):<8}",
            f"{'binding' if binding(node) else '-':<7}",
            f"{'stale' if node.get('stale') is True else '-':<5}",
        ]
        if ns.with_commit:
            columns.append(commits.get(nid, "-") if node.get("status") == "CANON" else "-")
        print(" ".join(columns + [str(node.get("title"))]))
    return EXIT_OK


def cmd_graph(ns: argparse.Namespace) -> int:
    """Print the nodes and the derived edge list as JSON."""
    _, data = read_graph(repo_top())
    if data is None:
        print(json.dumps({"schema": SCHEMA, "nodes": [], "edges": []}))
        print(NO_GRAPH, file=sys.stderr)
        return EXIT_OK
    nodes = [
        {
            "id": node.get("id"),
            "type": node.get("type"),
            "status": node.get("status"),
            "title": node.get("title"),
            "binding": binding(node),
            "stale": node.get("stale") is True,
        }
        for node in nodes_of(data)
    ]
    print(json.dumps({"schema": SCHEMA, "nodes": nodes, "edges": graph_edges(data)}, indent=2))
    return EXIT_OK


def _print_result(result: WriteResult | None, command: str) -> None:
    print(f"{command}: {NO_GRAPH}" if result is None else result.label)


def _recorded(op: str, entry: Path) -> None:
    print(f"{op}: recorded as outbox entry {entry} ({OUTBOX_ENV} is set; lore not written)")


def cmd_propose(ns: argparse.Namespace) -> int:
    """Propose one decision, story or canon node."""
    try:
        body = _read_text(Path(ns.body_file))
    except OSError as exc:
        raise UsageError(f"--body-file {ns.body_file} is not readable ({exc})") from None
    args = normalize_propose(
        {
            "type": ns.type,
            "title": ns.title,
            "body": body,
            "anchors_components": _split(ns.anchors_components),
            "anchors_files": _split(ns.anchors_files),
            "anchors_symbols": _split(ns.anchors_symbols),
            "planned_components": _split(ns.planned_components),
            "planned_files": _split(ns.planned_files),
            "relates_to": _split(ns.relates_to),
            "supersedes": _split(ns.supersedes),
            "source": ns.source,
            "context": ns.context,
            "created_at": _runtime.utc_now_iso(),
            "run_id": os.environ.get(RUN_ID_ENV) or None,
        }
    )
    outbox = outbox_dir()
    if outbox is not None:
        _recorded("propose", write_outbox_entry(outbox, "propose", args))
        return EXIT_OK
    print(do_propose(repo_top(), args).label)
    return EXIT_OK


def cmd_note(ns: argparse.Namespace) -> int:
    """Append one note to a mission NOTES.md or to activeContext's legacy section."""
    top = repo_top()
    mission = None
    if ns.mission is not None:
        folder = Path(os.path.abspath(ns.mission))
        try:
            mission = folder.resolve().relative_to(top).as_posix()
        except ValueError:
            raise UsageError(f"--mission {ns.mission} is outside the repository") from None
    args = normalize_note(
        {
            "legacy": ns.legacy,
            "mission": mission,
            "source": ns.source,
            "text": ns.text,
            "kind": ns.kind,
            "agent": ns.agent,
            "slice": ns.slice or os.environ.get(SLICE_ENV) or None,
            "anchors": _split(ns.anchors),
            "date": _runtime.utc_now_iso()[:10],
        }
    )
    _note_target(top, args)  # refuse spec/process and a missing dir before recording anything
    outbox = outbox_dir()
    if outbox is not None:
        _recorded("note", write_outbox_entry(outbox, "note", args))
        return EXIT_OK
    print(do_note(top, args))
    return EXIT_OK


def cmd_stale(ns: argparse.Namespace) -> int:
    """Mark the nodes whose anchors intersect the given components or files stale."""
    args = normalize_stale(
        {"nodes": _split(ns.nodes), "files": _split(ns.files), "reason": ns.reason}
    )
    outbox = outbox_dir()
    if outbox is not None:
        _recorded("stale", write_outbox_entry(outbox, "stale", args))
        return EXIT_OK
    _, result = do_stale(repo_top(), args)
    _print_result(result, "stale")
    return EXIT_OK


def cmd_resolve_anchors(ns: argparse.Namespace) -> int:
    """Move planned anchors that now exist into anchors."""
    _refuse_outbox("resolve-anchors")
    _print_result(do_resolve_anchors(repo_top()), "resolve-anchors")
    return EXIT_OK


def cmd_sync(ns: argparse.Namespace) -> int:
    """Recompute PROPOSED shas and mark changed or orphaned CANON nodes stale."""
    _refuse_outbox("sync")
    _print_result(do_sync(repo_top()), "sync")
    return EXIT_OK


def cmd_approval_request(ns: argparse.Namespace) -> int:
    """Print the hashed body, its sha, the nonce and the affected ids; write the pending record."""
    record, rel, body = do_approval_request(repo_top(), ns.id)
    affected = record["affects"]
    print(f"approval-request {record['id']}: {_printable(str(record['title']))}")
    print(f"pointer: {rel}")
    print(f"content_sha256: {record['content_sha256']}")
    print(f"nonce: {record['nonce']}")
    print(f"expires_at: {record['expires_at']}")
    for key in ("supersedes", "retires", "reanchors"):
        print(f"{key}: {', '.join(affected[key]) or 'none'}")
    print(f"pending record: {PENDING_REL}/{record['id']}.json")
    print(BODY_BEGIN)
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n(no newline at the end of the body)\n")
    print(BODY_END)
    return EXIT_OK


def cmd_approval_cancel(ns: argparse.Namespace) -> int:
    """Delete one pending approval request."""
    if do_approval_cancel(repo_top(), ns.id):
        print(f"approval-cancel: the pending request for {ns.id} is deleted")
    else:
        print(f"approval-cancel: no pending request for {ns.id}")
    return EXIT_OK


def cmd_gated(ns: argparse.Namespace) -> int:
    """Run approve, reject, retire, reopen or review --still-valid on one node."""
    print(do_gated(repo_top(), ns.verb, ns.ids, ns.nonce, ns.warrant_file))
    return EXIT_OK


def cmd_apply_outbox(ns: argparse.Namespace) -> int:
    """Replay an outbox's propose, note and stale entries; refuse any other hyper_lore entry."""
    if outbox_dir() is not None:
        raise UsageError(f"apply-outbox writes lore; unset {OUTBOX_ENV} to run it")
    top = repo_top()
    try:
        entries = hyper_ledger.read_outbox(Path(ns.dir))
    except hyper_ledger.LedgerError as exc:
        raise UsageError(str(exc)) from None
    plan, refused, report = [], [], []
    for name, entry in entries:
        if entry.get("tool") != OUTBOX_TOOL:
            report.append(f"skipped {name}: an entry of tool {entry.get('tool')!r}")
            continue
        op, args = entry.get("op"), entry.get("args")
        if op not in OUTBOX_OPS:
            refused.append(f"{name}: op {op!r} is not replayable (approvals are never replayed)")
            continue
        if not isinstance(args, dict):
            raise UsageError(f"outbox entry {name}: args is not an object")
        try:
            plan.append((name, op, NORMALIZE[op](args)))
        except UsageError as exc:
            raise UsageError(f"outbox entry {name}: {exc}") from None
    if refused:
        raise Refused(
            "refused: the outbox holds entries that are not replayable; nothing applied", refused
        )
    for line in report:
        print(line)
    for name, op, args in plan:
        if op == "propose":
            label = do_propose(top, args).label
        elif op == "note":
            label = do_note(top, args)
        else:
            _, result = do_stale(top, args)
            label = result.label if result is not None else f"stale: {NO_GRAPH}"
        print(f"applied {name}: {label}")
    print(f"apply-outbox: {len(plan)} entr{'y' if len(plan) == 1 else 'ies'} applied")
    return EXIT_OK


def cmd_index(ns: argparse.Namespace) -> int:
    """Regenerate MAP.md's region and CANON.md, then resolve planned anchors."""
    _refuse_outbox("index")
    print(do_index(repo_top()))
    return EXIT_OK


def cmd_init(ns: argparse.Namespace) -> int:
    """Create an empty lore graph with lore/MAP.md and lore/CANON.md (never overwrites)."""
    _refuse_outbox("init")
    print(do_init(repo_top()))
    return EXIT_OK


def cmd_import(ns: argparse.Namespace) -> int:
    """Import lore drafts in place, or the docs/adr directories given."""
    _refuse_outbox("import")
    top = repo_top()
    if ns.adr_dirs is not None:
        folders = [_repo_rel(top, value, "--adr-dirs") for value in ns.adr_dirs]
        lines = do_import_adr_dirs(top, folders, ns.context)
    else:
        if ns.context is not None:
            raise UsageError("--context goes with --adr-dirs")
        rels = [_repo_rel(top, value, "--drafts") for value in ns.drafts] or find_drafts(top)
        if not rels:
            print("import --drafts: no drafts under lore/ADRs/ or lore/STORIES/")
            return EXIT_OK
        lines = do_import_drafts(top, rels)
    for line in lines:
        print(line)
    return EXIT_OK


def cmd_merge_driver(ns: argparse.Namespace) -> int:
    """Run git's hacf-lore driver: merge lore_hypergraph.yml by id into <ours> (1: conflict)."""
    try:
        base = _read_side(ns.base, "base (%O)")
        ours = _read_side(ns.ours, "ours (%A)")
        theirs = _read_side(ns.theirs, "theirs (%B)")
    except ValidationFailed as exc:
        _report(exc)
        print(f"  {MERGE_HINT}", file=sys.stderr)
        return EXIT_VALIDATION
    merged, problems = merge_graphs(base, ours or empty_graph(), theirs or empty_graph())
    if problems:
        print(
            f"hyper_lore.py merge-driver: {ns.path}: {len(problems)} conflict(s); ours is kept",
            file=sys.stderr,
        )
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(f"  {MERGE_HINT}", file=sys.stderr)
        return EXIT_VALIDATION
    _write_plain(Path(ns.ours), dump_graph(merged))
    return EXIT_OK


def cmd_merge_generated(ns: argparse.Namespace) -> int:
    """Run git's hacf-generated driver: rebuild CANON.md or MAP.md's region from the merge."""
    top = repo_top()
    rel = ns.path
    if rel not in (MAP_REL, CANON_MD_REL):
        print(
            f"hyper_lore.py merge-generated: {rel} is not {MAP_REL} or {CANON_MD_REL}; "
            "left as a conflict",
            file=sys.stderr,
        )
        return EXIT_VALIDATION
    state = merged_state(top)
    if state is None:
        return _keep_ours(top, rel, "the merged lore graph is not available to the driver")
    data, read = state
    clean = True
    try:
        if rel == CANON_MD_REL:
            text = canon_md_text(data, read)
        else:
            sides = [_strip_region(_read_text(Path(p))) for p in (ns.base, ns.ours, ns.theirs)]
            merged_text, clean = merge_file(top, *sides)
            try:
                text = render_map(merged_text, data)
            except Conflict:
                if clean:
                    raise
                text = merged_text  # the hand-written conflict hid the markers; index later
                _flag_needs_index(top)
    except (LoreError, OSError) as exc:
        return _keep_ours(top, rel, str(exc))
    _write_plain(Path(ns.ours), text)
    if not clean:
        print(
            f"hyper_lore.py merge-generated: {rel}: the hand-written parts conflict; resolve "
            "them, then run `hyper_lore.py index`",
            file=sys.stderr,
        )
        return EXIT_VALIDATION
    return EXIT_OK


def cmd_renumber(ns: argparse.Namespace) -> int:
    """Renumber one node and rewrite its in-repo references; report the others."""
    for line in do_renumber(repo_top(), ns.old, ns.new):
        print(line)
    return EXIT_OK


def cmd_install_drivers(ns: argparse.Namespace) -> int:
    """Install the repo-local merge drivers and their info/attributes block."""
    for line in do_install_drivers(repo_top()):
        print(line)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_lore.py",
        description="Validated, locked writer of lore_hypergraph.yml and the CANON gate.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name: str, handler: Callable[[argparse.Namespace], int], text: str) -> Any:
        p = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        p.set_defaults(handler=handler)
        return p

    p = command("validate", cmd_validate, "validate the lore graph (read-only)")
    p.add_argument("--strict", action="store_true", help="fail on warnings too")
    p.add_argument("--json", action="store_true", help="print the report as JSON")

    p = command("show", cmd_show, "print one node")
    p.add_argument("id", help="node id")

    p = command("list", cmd_list, "list nodes")
    p.add_argument("--status", choices=STATUSES, help="only nodes with this status")
    p.add_argument("--stale", action="store_true", help="only stale nodes")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--with-commit", action="store_true", help="the commit that made each CANON")

    p = command("graph", cmd_graph, "print nodes and edges as JSON")
    p.add_argument("--json", action="store_true", help="print JSON (the only format)")

    p = command("propose", cmd_propose, "propose a decision, story or canon node")
    p.add_argument("--type", required=True, choices=tuple(TYPE_PREFIX), help="node type")
    p.add_argument("--title", required=True, help="one-line title")
    p.add_argument("--body-file", required=True, metavar="F", help="body, written verbatim")
    for flag in (
        "--anchors-components",
        "--anchors-files",
        "--anchors-symbols",
        "--planned-components",
        "--planned-files",
        "--relates-to",
        "--supersedes",
    ):
        p.add_argument(flag, metavar="a,b", help="comma-separated list")
    p.add_argument("--source", metavar="S", help="slice id or legacy path that proposes it")
    p.add_argument("--context", metavar="C", help="bounded context name")

    p = command("note", cmd_note, "append a field note")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--mission", metavar="<dir>", help="mission directory holding NOTES.md")
    group.add_argument("--legacy", action="store_true", help="activeContext Field Notes (legacy)")
    p.add_argument("--source", metavar="<path>", help="the MiniPRD path (with --legacy)")
    p.add_argument("--text", required=True, help="one-line note")
    p.add_argument("--kind", help="note kind (default field-note)")
    p.add_argument("--agent", help="the skill writing it (default unknown)")
    p.add_argument("--slice", help=f"slice id (default ${SLICE_ENV}, else unknown)")
    p.add_argument("--anchors", metavar="a,b", help="what the note is about")

    p = command("stale", cmd_stale, "mark nodes anchored to components or files stale")
    p.add_argument("--nodes", metavar="a,b", help="component ids")
    p.add_argument("--files", metavar="g1,g2", help="paths or globs")
    p.add_argument("--reason", required=True, metavar="R", help="why")

    command("resolve-anchors", cmd_resolve_anchors, "move planned anchors that now exist")
    command("sync", cmd_sync, "recompute PROPOSED shas; mark changed CANON nodes stale")

    p = command("approval-request", cmd_approval_request, "start the gate for one node")
    p.add_argument("id", help="node id")
    p = command("approval-cancel", cmd_approval_cancel, "delete a pending approval request")
    p.add_argument("id", help="node id")

    for verb, text in (
        ("approve", "PROPOSED -> CANON (gated)"),
        ("reject", "PROPOSED -> REJECTED (gated; node and file kept)"),
        ("retire", "CANON -> RETIRED (gated)"),
        ("reopen", "REJECTED -> PROPOSED (gated)"),
        ("review", "clear stale on a CANON node (gated; needs --still-valid)"),
    ):
        p = command(verb, cmd_gated, text)
        p.set_defaults(verb=verb)
        p.add_argument("ids", nargs="+", metavar="ID", help="exactly one node id")
        if verb == "review":
            p.add_argument("--still-valid", required=True, action="store_true", help="required")
        p.add_argument("--nonce", required=True, help="nonce printed by approval-request")
        p.add_argument("--warrant-file", required=True, metavar="F", help="the user's warrant")

    p = command("apply-outbox", cmd_apply_outbox, "replay an outbox's propose/note/stale entries")
    p.add_argument("dir", help="outbox directory")

    command("index", cmd_index, "regenerate lore/MAP.md's region and lore/CANON.md")
    command("init", cmd_init, "create an empty lore graph, lore/MAP.md and lore/CANON.md")

    p = command("import", cmd_import, "import lore drafts, or docs/adr directories")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--drafts", nargs="*", metavar="PATH", help="draft files (default: every lore draft)"
    )
    group.add_argument("--adr-dirs", nargs="+", metavar="DIR", help="docs/adr directories")
    p.add_argument("--context", metavar="C", help="bounded context of the imported ADRs")

    for name, handler, text in (
        ("merge-driver", cmd_merge_driver, "git hacf-lore driver for lore_hypergraph.yml"),
        ("merge-generated", cmd_merge_generated, "git hacf-generated driver (MAP.md, CANON.md)"),
    ):
        p = command(name, handler, text)
        p.add_argument("base", help="%%O: the common ancestor's version")
        p.add_argument("ours", help="%%A: our version; the result is written here")
        p.add_argument("theirs", help="%%B: their version")
        p.add_argument("path", help="%%P: the path being merged")

    p = command("renumber", cmd_renumber, "renumber a node and rewrite its in-repo references")
    p.add_argument("old", help="the node id to renumber")
    p.add_argument("new", help="the new id (must not exist)")

    command("install-drivers", cmd_install_drivers, "install the repo-local lore merge drivers")
    return parser


def _report(exc: LoreError) -> None:
    print(f"hyper_lore.py: {exc}", file=sys.stderr)
    for line in exc.details:
        print(f"  {line}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code (0 ok, 1 validation, 2-5 failures)."""
    _runtime.require_runtime()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return ns.handler(ns)
    except LoreError as exc:
        _report(exc)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
