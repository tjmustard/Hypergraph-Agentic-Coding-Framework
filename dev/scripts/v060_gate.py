#!/usr/bin/env python3
"""Per-slice gate for the HACF v0.6.0 bootstrap (process tooling, never shipped).

HACF v0.6.0 lands as 29 ordered MiniPRDs (NN 00-28) on the push-blocked branch
swimlane/v0.6.0. The operating procedure (dev/design/v0.6.0/operating-procedure.md) runs
this gate once after /hyper-resolve and once at the end of every slice audit. It works on
the repository named by --repo (default: the git toplevel of the current directory), the
backup directory and its own temporary directories, and nothing else. post-resolve, finish
and restore-ledger refuse to run unless HEAD is on the branch swimlane/v0.6.0.

Usage:
    python3 dev/scripts/v060_gate.py [--repo PATH] post-resolve [--expect N]
        [--repin REASON]
    python3 dev/scripts/v060_gate.py [--repo PATH] finish SID --miniprd PATH
        [--accept-scope REASON] [--accept-block-change REASON]
        [--allow-empty REASON] [--refinish REASON] [--out-of-order REASON]
        [--accept-validator-change REASON] [--no-track-spec]
    python3 dev/scripts/v060_gate.py [--repo PATH] restore-ledger
    python3 dev/scripts/v060_gate.py [--repo PATH] status
    python3 dev/scripts/v060_gate.py selftest

Commands:
    post-resolve    Check the compiled MiniPRDs, their hacf-bootstrap blocks (no YAML
                    merge keys) and guards.json, and refuse any changed path outside
                    spec/compiled, spec/archive, spec/active and dev/design/v0.6.0 (never
                    guards.json, whose content and mode must equal HEAD's; never
                    spec/process). Each MiniPRD needs the Overrides banner between its
                    Parent Node line and '## 1. The Confidence Mandate', pointing to
                    '## Pipeline Operating Notes', and must cover its seed in slices.md:
                    the seed's files, test names and depends_on, unless a row of the
                    SuperPRD's Seed deviations table names the SID and the item. It also
                    refuses a symlink or other non-regular file
                    among the changed paths, the MiniPRDs and the SuperPRD, an
                    assume-unchanged, skip-worktree or intent-to-add index entry, a
                    grafts file, and a run with nothing to commit. It runs once: when a
                    post-resolve commit or refs/hacf/v0.6.0/post-resolve already exists it
                    refuses unless --repin REASON, and --repin is refused while a
                    post-resolve commit lacks the shape described under Refs, and when
                    it would change or drop the block of a SID that has a slice ref. Then it
                    renames a bare SuperPRD.md to SuperPRD_v0_6_0.md, backs up
                    spec/compiled and the newest *_v0_6_0* archive folder, stages only
                    those four directories, checks that every file it parsed is staged
                    byte-for-byte, commits, checks that the commit's tree is the staged
                    tree, and records the commit in refs/hacf/v0.6.0/post-resolve. That
                    commit pins every block. A validation failure writes and commits
                    nothing.
    finish          Check one slice, add it to the activeContext.md completed list,
                    commit it with an HACF-Slice trailer and record the commit in
                    refs/hacf/v0.6.0/slices/<SID>. It refuses unless:
                    - the index has no assume-unchanged, skip-worktree or intent-to-add
                      entry, there is no grafts file, and HEAD is not a finish of this
                      SID that lacks its slice ref (checked before any validator runs);
                    - refs/hacf/v0.6.0/post-resolve names the newest post-resolve
                      commit, an ancestor of HEAD, and every post-resolve commit is
                      shaped as described under Refs;
                    - every commit a slice ref records changes, versus the gate commit
                      before it, only paths its pinned files globs, the files value its
                      block-change record names and its accepted-path record allow;
                    - --miniprd is the audited copy under spec/archive/, a regular file
                      (MiniPRD_NN_<Slug>_AUDITED.md; the final slice may lack _AUDITED),
                      no spec/compiled/MiniPRD_NN_*.md is left behind, and its block
                      equals the SID's block in the post-resolve commit (slice and nn
                      can never change); --no-track-spec needs a pinned block with
                      track_spec: false;
                    - the SID has no slice ref yet (a re-finish needs --refinish), and
                      every depends_on SID is a finished slice (else --out-of-order);
                    - the ledger, activeContext.md and guards.json are regular files;
                      the ledger keeps every node and key of the scope base and the
                      validators pass. Each validator that exists at the scope base
                      must exist in the working tree, and its scope-base copy, run on
                      the working tree, must report nothing it does not report on a
                      throwaway checkout of the scope base (else
                      --accept-validator-change);
                    - guards.json equals its post-resolve copy and each guarded file is
                      unchanged or moved byte-for-byte under spec/archive/;
                    - every path changed since the scope base matches a block files
                      glob, except the ledger, this slice's own MiniPRD paths and
                      activeContext.md outside its protected procedure section, and
                      something besides that bookkeeping changed;
                    - activeContext.md has no whitespace-, case- or format-character-
                      folded lookalike of its protected heading or completed-list
                      markers, and git ignores no *.py file under dev/tests or
                      .agents/scripts (outside __pycache__/);
                    - what finish would commit has no symlink (120000) or gitlink
                      (160000) entry, and each committed file's bytes on disk (git
                      hash-object --no-filters) equal its staged blob;
                    - dev/scripts/check.sh (required once the scope base has it) passes,
                      leaves HEAD, the branch, the index (entries and flags) and every
                      path finish checked as they were, and changes no other path that
                      git add would pick up. It runs in a new session, and finish kills
                      its process group afterwards.
                    Just before check.sh it builds, in a temporary copy of the index,
                    the tree its own staging would commit. It then stages and refuses
                    any staged path the scope check did not validate, and any staged
                    tree that differs from that one by more than the text it wrote to
                    activeContext.md. It commits with --cleanup=verbatim and checks that
                    the commit has exactly the staged tree, paths and message (a
                    mismatch records no ref; git reset --soft HEAD^ is advised only
                    while HEAD is that commit, on top of where finish started). The
                    commit body records the first line of claude --version when that
                    command exists. After the slice ref, finish writes <backup
                    root>/hacf-v060-<SID>.bundle (every branch and refs/hacf/*); a
                    bundle failure is a warning.
                    If the commit step fails or is interrupted (Ctrl-C, SIGTERM,
                    SIGHUP), activeContext.md and the index are restored. SIGTERM and
                    SIGHUP stay trapped until the slice ref is recorded: an interrupt
                    after the commit says whether the slice is finished or how to undo
                    the commit. A retry after a killed finish accepts this slice's own
                    completed-list line.
    restore-ledger  Save the working-tree ledger to the backup directory, then restore
                    HEAD's copy.
    status          Read-only summary: branch, post-resolve pin, completed slices,
                    waivers (with accepted paths and changed keys), remaining MiniPRDs,
                    ledger, guards. It warns about a broken pin (and prints the command
                    that records a lost post-resolve ref again), about commits that look
                    like gate commits but have no ref (HEAD: an interrupted finish), and
                    about replacement refs and a grafts file.
    selftest        Run every command against throwaway git repositories in a temp dir.

Refs: the gate records its commits under refs/hacf/v0.6.0/ (post-resolve, slices/<SID>,
and superseded/<SID>/<sha> for a commit that --refinish replaced). A completed slice is a
SID whose slice ref names an ancestor of HEAD carrying HACF-Slice: <SID>. The scope base
is the newest of the post-resolve commit and the completed slices' commits. A commit with
an HACF-Slice trailer that no ref names is not a completed slice; finish and status warn
about it, and the next finish checks its changes. When that commit is HEAD, it is most
likely a finish killed after its commit: finish of that SID refuses until git reset
--soft HEAD^. Every post-resolve commit (subject prefix 'bootstrap(v0.6.0): compiled')
must have one parent and change only post-resolve paths, and every one after the first
needs an HACF-Repin trailer. When refs/hacf/v0.6.0/post-resolve is missing but that
history is sound, finish, post-resolve and status print the git update-ref command that
records the newest post-resolve commit again; the gate never runs it, because only the
user knows whether the ref was lost or removed on purpose. The refs make a hand-made or
forged gate commit visible, but they cannot prevent one.

A slice ref counts only for a SID that the post-resolve commit pins (exactly one of its
spec/compiled MiniPRD blocks declares it).

History rewrites: every git command the gate runs ignores replacement objects
(GIT_NO_REPLACE_OBJECTS=1), and status, finish and post-resolve warn while refs/replace/
has refs. finish and post-resolve refuse to run while a grafts file exists, and status
warns about it.

Hooks: the gate's commits run with --no-verify, and its commits and ref updates with
core.hooksPath set to an empty temporary directory, so no repository or global hook runs.

Waivers: --accept-scope, --accept-block-change, --allow-empty, --refinish,
--out-of-order, --accept-validator-change and (for post-resolve) --repin each take a
REASON that becomes one commit trailer: HACF-Scope-Accepted, HACF-Block-Changed,
HACF-Empty, HACF-Refinish, HACF-Out-Of-Order, HACF-Validator-Change or HACF-Repin. After
collapsing whitespace, a REASON needs a letter or digit and no control, format or
separator character. The commit body records what a waiver accepted: the out-of-scope
paths, each changed block key with its old and new value (JSON), the unfinished
dependencies, or the validators whose scope-base copy reported something new. status
lists every waiver in history. Pass a waiver only after the user approves it in
AskUserQuestion, with the user's words as REASON. That rule is procedural: the gate cannot
verify the approval, it only records the waiver. No flag waives a guard, ledger,
working-tree validator, file-type, index-flag, grafts, staged-content, committed-bytes,
entry-mode, lookalike-marker, recorded-slice or check.sh problem, a change to a block's
slice or nn, a re-pin that changes a finished slice's block, or a seed gap that the
SuperPRD's Seed deviations table does not name.

Backups go to $HACF_BACKUP_ROOT, else ~/.local/state/hacf/backups/v0.6.0. A root inside
the work tree (but not inside the git directory) or one that is not writable falls back
to <git-common-dir>/hacf-backups.

Exit codes: 0 ok, 1 validation failure or refusal, 2 usage error, 128+N when signal N
(SIGTERM or SIGHUP) interrupted the finish commit step (after the rollback) or the
verification and ref update after it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NamedTuple

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANNEX = "dev/design/v0.6.0"
GUARDS = f"{ANNEX}/guards.json"
LEDGER = "spec/compiled/architecture.yml"
ACTIVE = ".agents/memory/activeContext.md"
PROCEDURE_HEADING = "## v0.6.0 Bootstrap Operating Procedure (read first)"
COMPLETED_BEGIN = "<!-- BEGIN v060-completed (appended by dev/scripts/v060_gate.py finish) -->"
COMPLETED_END = "<!-- END v060-completed -->"
PROCEDURE_SCOPE = f"{ACTIVE} (procedure section)"
DEFAULT_BACKUP_ROOT = "~/.local/state/hacf/backups/v0.6.0"
GATE_BRANCH = "swimlane/v0.6.0"

COMPILED_DIR = "spec/compiled"
ARCHIVE_DIR = "spec/archive"
SUPERPRD_BARE = "spec/compiled/SuperPRD.md"
SUPERPRD_V060 = "spec/compiled/SuperPRD_v0_6_0.md"
POST_RESOLVE_ADD_DIRS = ("spec/compiled", "spec/archive", "spec/active")
POST_RESOLVE_DIRS = (*POST_RESOLVE_ADD_DIRS, ANNEX)
FINISH_ADD_DIRS = ("spec/compiled", "spec/archive")
CHECK_SCRIPT = "dev/scripts/check.sh"
SLICES = f"{ANNEX}/slices.md"
SCOPE_BASE_ARG = "<scope-base>"
VALIDATORS = (
    (".agents/scripts/hyper_ledger.py", ("validate", "--against-ref", SCOPE_BASE_ARG)),
    (".agents/scripts/hyper_lore.py", ("validate",)),
    (".agents/scripts/hyper_spec.py", ("lint", "--all")),
)
# Directories whose ignored *.py files pytest could import but finish never commits.
TESTED_SOURCE_DIRS = ("dev/tests", ".agents/scripts")
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"

POST_RESOLVE_PREFIX = "bootstrap(v0.6.0): compiled"
SLICE_TRAILER = "HACF-Slice: "
SCOPE_TRAILER = "HACF-Scope-Accepted: "
BLOCK_TRAILER = "HACF-Block-Changed: "
EMPTY_TRAILER = "HACF-Empty: "
REFINISH_TRAILER = "HACF-Refinish: "
OUT_OF_ORDER_TRAILER = "HACF-Out-Of-Order: "
VALIDATOR_TRAILER = "HACF-Validator-Change: "
REPIN_TRAILER = "HACF-Repin: "
REPIN_FLAG = "--repin"
# The finish waivers; post-resolve has one more, REPIN_FLAG.
WAIVER_FLAGS = (
    ("--accept-scope", SCOPE_TRAILER),
    ("--accept-block-change", BLOCK_TRAILER),
    ("--allow-empty", EMPTY_TRAILER),
    ("--refinish", REFINISH_TRAILER),
    ("--out-of-order", OUT_OF_ORDER_TRAILER),
    ("--accept-validator-change", VALIDATOR_TRAILER),
)
WAIVER_TRAILERS = (*(trailer for _, trailer in WAIVER_FLAGS), REPIN_TRAILER)
# Waiver records in a slice commit body: a heading line, then one '- item' line each.
ACCEPTED_PATHS_HEADING = "Accepted out-of-scope paths:"
CHANGED_KEYS_HEADING = "Changed hacf-bootstrap block keys:"  # '- key: <old JSON> -> <new JSON>'
UNFINISHED_DEPS_HEADING = "Dependencies without a slice ref:"
VALIDATOR_CHANGE_HEADING = "Validators whose scope-base copy reports new findings:"
TOOL_VERSION_LABEL = "claude --version: "

# Refs that record the gate's own commits (git update-ref with compare-and-swap).
REF_ROOT = "refs/hacf/v0.6.0"
POST_RESOLVE_REF = f"{REF_ROOT}/post-resolve"
SLICE_REF_PREFIX = f"{REF_ROOT}/slices/"
SUPERSEDED_REF_PREFIX = f"{REF_ROOT}/superseded/"
# History rewriting that the gate refuses (grafts) or ignores and warns about (replace).
REPLACE_REFS = "refs/replace"
GRAFTS = "info/grafts"
# Always in scope: the ledger (finish checks its content) and activeContext.md, whose
# protected procedure section scope_check compares with the scope base separately.
ALWAYS_IN_SCOPE = (LEDGER, ACTIVE)
OWN_MINIPRD_GLOBS = (
    "spec/compiled/MiniPRD_{nn:02d}_*.md",
    "spec/archive/**/MiniPRD_{nn:02d}_*.md",
)
UNSAFE_GLOBS = ("**", "*", "**/*", "**/**", "*/**", "/**")
GLOB_CANARIES = (
    "v060-canary",
    "v060-canary/v060-canary",
    "v060-canary/v060-canary/v060-canary",
    "v060-canary.md",
    ".v060-canary",
    "a/b/v060-canary.py",
)
# Unicode categories that never belong in a title or REASON (they reach commit messages):
# control, format, private use, surrogate, line and paragraph separators.
FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})

BLOCK_INFO = "hacf-bootstrap"
BLOCK_KEYS = (
    "slice",
    "nn",
    "title",
    "depends_on",
    "node_id",
    "parent",
    "ensure_node",
    "files",
    "oracle",
    "audit_phase3",
    "removes_nodes",
    "track_spec",
    "lore_refs",
    "annex_refs",
)
# Block keys that no waiver may change: they select the pin and the own-MiniPRD exemption.
IDENTITY_KEYS = ("slice", "nn")
AUDIT_PHASE3_MODES = ("legacy-haiku", "review-patch", "skip")

BANNER_PREFIX = "> **Overrides (read first):**"
PARENT_NODE_PREFIX = "**Parent Node:**"
CONFIDENCE_HEADING = "1. The Confidence Mandate"
NOTES_POINTER = "## Pipeline Operating Notes"
SEED_FILE_SECTIONS = ("Files to create", "Files to modify", "Files to delete")
SEED_TESTS_SECTION = "Tests (deterministic)"
SEED_ENTRY_PUNCTUATION = ".,;:!?"
DEVIATIONS_MARKER = "**Seed deviations**"

SID_RE = re.compile(r"S[0-9]{2}[a-z]?[0-9]?")
SID_TOKEN_RE = re.compile(r"\bS[0-9]{2}[a-z]?[0-9]?\b")
SEED_ANCHOR_RE = re.compile(r'<a id="(s[0-9]{2}[a-z]?[0-9]?)"></a>')
ANY_ANCHOR_RE = re.compile(r'<a id="[^"]*"></a>')
SEED_DEPENDS_RE = re.compile(r"- \*\*Depends on:\*\*(.*)")
TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+\*?")
R_ROW_RE = re.compile(r"\bR[0-9]+")
MINIPRD_NAME_RE = re.compile(r"MiniPRD_([0-9]{2})_[A-Za-z0-9_-]+\.md")
AUDITED_NAME_RE = re.compile(r"MiniPRD_([0-9]{2})_[A-Za-z0-9_-]+_AUDITED\.md")
NOTES_TITLE_RE = re.compile(r"(?:[0-9]+\.?[ \t]+)?Pipeline Operating Notes")
LORE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DATE_RE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
ATX_HEADING_RE = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*")
FENCE_OPEN_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
FENCE_CLOSE_RE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")

GIT_TIMEOUT = 600
VALIDATOR_TIMEOUT = 900
CHECK_TIMEOUT = 3600
TOOL_TIMEOUT = 30
STOP_GRACE = 5  # seconds an interrupted git child gets to exit after SIGTERM
OUTPUT_LINES = 100  # lines of a validator's output that a refusal prints


class GateError(Exception):
    """A validation failure or refusal; the process exits with status 1."""

    code = 1


class UsageError(GateError):
    """A bad command line or target; the process exits with status 2."""

    code = 2


class _Interrupted(BaseException):
    """Raised by finish's SIGTERM/SIGHUP handlers, from its commit step until its slice ref."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by {signal.Signals(signum).name}")
        self.signum = signum


# ---------------------------------------------------------------------------
# Output, subprocess and path helpers
# ---------------------------------------------------------------------------


def _info(message: str) -> None:
    print(message, flush=True)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr, flush=True)


def _report(title: str, problems: list[str]) -> None:
    print(f"{title} ({len(problems)} problem(s)):", file=sys.stderr, flush=True)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr, flush=True)


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _describe(value: Any) -> str:
    text = repr(value)
    if len(text) > 60:
        text = text[:57] + "..."
    return f"{type(value).__name__} {text}"


def _key(trailer: str) -> str:
    """Return a trailer's key, e.g. 'HACF-Scope-Accepted' for SCOPE_TRAILER."""
    return trailer.split(":", 1)[0]


def _safe(text: str) -> str:
    """Return text with control, format and separator characters escaped, for printing."""
    return "".join(
        ascii(char)[1:-1] if unicodedata.category(char) in FORBIDDEN_CATEGORIES else char
        for char in text
    )


def _text_problem(text: str) -> str | None:
    """Return why text cannot go into a commit message (title or REASON), or None."""
    bad = sorted({char for char in text if unicodedata.category(char) in FORBIDDEN_CATEGORIES})
    if bad:
        listed = ", ".join(f"U+{ord(char):04X}" for char in bad)
        return f"contains a control, format or separator character ({listed})"
    if not any(char.isalnum() for char in text):
        return "has no letter or digit"
    return None


@contextlib.contextmanager
def _signals(handler: Any, names: tuple[str, ...]) -> Iterator[None]:
    """Install handler for the named signals and restore the previous handlers afterwards.

    Outside the main thread (where Python cannot set handlers) this does nothing.
    """
    saved: dict[int, Any] = {}
    try:
        for name in names:
            signum = getattr(signal, name, None)
            if signum is not None:
                saved[signum] = signal.signal(signum, handler)
    except ValueError:
        pass
    try:
        yield
    finally:
        for signum, previous in saved.items():
            signal.signal(signum, signal.SIG_DFL if previous is None else previous)


def _raise_interrupted(signum: int, _frame: Any) -> None:
    raise _Interrupted(signum)


def _trapped_signals() -> contextlib.AbstractContextManager[None]:
    """Make SIGTERM and SIGHUP raise _Interrupted, so a rollback can run."""
    return _signals(_raise_interrupted, ("SIGTERM", "SIGHUP"))


def _ignored_signals() -> contextlib.AbstractContextManager[None]:
    """Ignore SIGINT, SIGTERM and SIGHUP, e.g. while a rollback runs."""
    return _signals(signal.SIG_IGN, ("SIGINT", "SIGTERM", "SIGHUP"))


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Ask an interrupted child to exit (SIGTERM: git then removes its lock files).

    After STOP_GRACE seconds the child is killed.
    """
    with _ignored_signals():
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _git_run(
    repo: Path, args: tuple[str, ...], data: bytes | None, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    """Run git with an argv list in repo; return (exit code, stdout, stderr).

    data is written to stdin (None: stdin is /dev/null). Every git child runs with
    GIT_NO_REPLACE_OBJECTS=1, so replacement objects (git replace) cannot change what the
    gate reads; env adds variables such as GIT_INDEX_FILE. If the gate is interrupted while
    git runs, git is stopped with _stop before the interrupt propagates.
    """
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=str(repo),
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", **(env or {})},
            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise UsageError("git is not installed or not on PATH") from exc
    with proc:
        try:
            out, err = proc.communicate(data, timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            _stop(proc)
            raise GateError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT}s") from exc
        except BaseException:
            _stop(proc)
            raise
    return proc.returncode, out, err


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    stdin: str | bytes = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git with an argv list in repo; raise GateError on failure when check is set.

    A str stdin is encoded as UTF-8. Output is decoded as UTF-8 (undecodable bytes
    replaced); newlines are not translated.
    """
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    code, out, err = _git_run(repo, args, data, env)
    proc = subprocess.CompletedProcess(
        ["git", *args],
        code,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )
    if check and code != 0:
        detail = _one_line(proc.stderr or proc.stdout)
        raise GateError(f"git {' '.join(args)} failed (exit {code}): {detail}")
    return proc


def _git_bytes(repo: Path, *args: str) -> bytes | None:
    """Run git with an argv list in repo; return its raw stdout, or None when it fails."""
    code, out, _ = _git_run(repo, args, None)
    return out if code == 0 else None


def _git_paths(repo: Path, *args: str) -> set[str]:
    """Run a NUL-separated (-z) git listing and return its paths."""
    return {path for path in _git(repo, *args).stdout.split("\0") if path}


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the process group that proc leads, then reap proc (it may already be gone)."""
    with _ignored_signals():
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _run_script(argv: list[str], cwd: Path, timeout: int, capture: bool = False) -> tuple[int, str]:
    """Run argv in cwd in a new session; return (exit code, captured output).

    The repository's own scripts (check.sh, the validators) run this way. The child leads
    a new session and process group, which is killed once the child exits, times out (exit
    124) or the gate is interrupted, so no background process it started can write into
    the repository later. capture False inherits stdout and stderr (the output is then
    ''); capture True collects both through a temporary file, which a background child
    holding the stream open cannot block. A missing argv[0] gives exit 127.
    """
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=sink if capture else None,
                stderr=subprocess.STDOUT if capture else None,
                start_new_session=True,
            )
        except FileNotFoundError:
            return 127, ""
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _warn(f"{' '.join(argv)} timed out after {timeout}s")
            code = 124
        finally:
            _kill_group(proc)
        sink.seek(0)
        output = sink.read().decode("utf-8", errors="replace") if capture else ""
    return code, output


@contextlib.contextmanager
def _no_hooks() -> Iterator[tuple[str, str]]:
    """Yield the git options that point core.hooksPath at an empty temporary directory.

    The gate's commits and ref updates use them (commits also pass --no-verify), so no
    repository or global hook can run while the gate writes.
    """
    with tempfile.TemporaryDirectory(prefix="v060-gate-no-hooks-") as folder:
        yield "-c", f"core.hooksPath={folder}"


def _python() -> str:
    """Return the running interpreter as an absolute path (venv symlinks kept)."""
    return os.path.abspath(sys.executable)


def resolve_repo(arg: str | None) -> Path:
    """Return the git toplevel of --repo, or of the current directory when it is absent."""
    start = Path(arg).expanduser() if arg else Path.cwd()
    if not start.is_dir():
        raise UsageError(f"--repo {arg}: not a directory")
    proc = _git(start, "rev-parse", "--show-toplevel", check=False)
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        where = f"--repo {arg}" if arg else f"the current directory ({start})"
        raise UsageError(f"{where} is not inside a git work tree")
    return Path(top).resolve()


def _inside_repo(repo: Path, rel: str) -> Path | None:
    """Return repo/rel resolved, or None when rel is absolute or leaves the repository.

    A path that cannot be resolved (a NUL byte, a symlink loop) also gives None.
    """
    if not rel or Path(rel).is_absolute():
        return None
    root = repo.resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_relative_to(root) else None


def _repo_rel(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    """Return the content of path, or None when it is missing or unreadable."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _plain_file_problem(repo: Path, rel: str) -> str | None:
    """Return why repo/rel is not a regular file reached without symlinks, else None.

    An absent path is not a problem here; each command handles a missing file itself.
    """
    path = repo / rel
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return f"cannot be inspected: {_one_line(exc)}"
    if stat.S_ISLNK(mode):
        return "is a symlink"
    if not stat.S_ISREG(mode):
        return "is not a regular file"
    if path.resolve() != repo.resolve() / os.path.normpath(rel):
        return "is reached through a symlinked directory"
    return None


def _file_type_problems(repo: Path) -> list[str]:
    """Return the ledger, activeContext.md and guards.json file-type problems.

    Each of them, when it exists, must be a regular file reached without symlinks. No
    flag waives these problems.
    """
    problems: list[str] = []
    for rel in (LEDGER, ACTIVE, GUARDS):
        problem = _plain_file_problem(repo, rel)
        if problem:
            problems.append(f"{rel} {problem}; the gate reads and commits only regular files")
    return problems


class _PathState(NamedTuple):
    kind: str  # absent, file, symlink, directory or other
    mode: int
    detail: str  # sha256 of a file, target of a symlink


def _path_state(path: Path) -> _PathState:
    """Return what lstat sees at path: type and mode, plus the sha256 or link target."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return _PathState("absent", 0, "")
    except OSError as exc:
        return _PathState("other", 0, _one_line(exc))
    if stat.S_ISLNK(info.st_mode):
        return _PathState("symlink", info.st_mode, os.readlink(path))
    if stat.S_ISREG(info.st_mode):
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            digest = f"unreadable: {_one_line(exc)}"
        return _PathState("file", info.st_mode, digest)
    kind = "directory" if stat.S_ISDIR(info.st_mode) else "other"
    return _PathState(kind, info.st_mode, "")


def _state_change(old: _PathState, new: _PathState) -> str | None:
    """Describe how a path changed between two _path_state calls, or None."""
    if old == new:
        return None
    if old.kind == "absent":
        return "created"
    if new.kind == "absent":
        return "deleted"
    if old.kind != new.kind:
        return f"changed from a {old.kind} to a {new.kind}"
    if old.mode != new.mode:
        return f"mode changed from {old.mode:o} to {new.mode:o}"
    return "link target changed" if new.kind == "symlink" else "content changed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _blob(repo: Path, rev: str, rel: str) -> bytes | None:
    """Return the bytes of rel at commit rev, or None when rev or the path is absent."""
    return _git_bytes(repo, "cat-file", "blob", f"{rev}:{rel}")


def _blob_text(repo: Path, rev: str, rel: str) -> str | None:
    """Return rel at rev as text (undecodable bytes replaced), or None when it is absent."""
    data = _blob(repo, rev, rel)
    return None if data is None else data.decode("utf-8", errors="replace")


def _current_branch(repo: Path) -> str | None:
    """Return the checked-out branch, or None when HEAD is detached."""
    proc = _git(repo, "symbolic-ref", "--quiet", "HEAD", check=False)
    ref = proc.stdout.strip()
    if proc.returncode != 0 or not ref.startswith("refs/heads/"):
        return None
    return ref[len("refs/heads/") :]


def _head_state(branch: str | None) -> str:
    return f"HEAD is on branch {branch}" if branch else "HEAD is detached"


def _require_branch(repo: Path) -> None:
    """Refuse (exit 1) unless HEAD is on GATE_BRANCH; commands call it before any write."""
    branch = _current_branch(repo)
    if branch != GATE_BRANCH:
        raise GateError(
            f"refused; nothing was written: {_head_state(branch)}; the gate runs only on "
            f"{GATE_BRANCH} (git switch {GATE_BRANCH})"
        )


# ---------------------------------------------------------------------------
# Path globs (scope check)
# ---------------------------------------------------------------------------


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a repo-relative path glob into a regex for re.fullmatch.

    `*` and `?` stay inside one path segment, `**` crosses segments and `**/` also matches
    zero directories. A trailing `/` means everything below that directory. A leading `./`
    or `/` is ignored. All other characters, brackets included, match literally. Every
    wildcard also matches a newline (git allows one in a path).
    """
    pat = pattern.strip()
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    out: list[str] = []
    index = 0
    while index < len(pat):
        char = pat[index]
        if pat.startswith("**/", index):
            out.append("(?:[^/]*/)*")
            index += 3
            continue
        if pat.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("".join(out), re.DOTALL)


def _strip_dot_slash(pattern: str) -> str:
    text = pattern.strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _glob_problem(value: Any) -> str | None:
    """Return why a block files entry is not a safe glob, or None when it is safe.

    A safe glob is a single-line relative path pattern with no leading or trailing
    whitespace, '..' segment, backslash or NUL, and a '**' only as a whole path segment
    ('dev/tests/**', not 'dev/tests**'). It must not match everything ('**', '*/**',
    './**'), its first path segment must have no '*' or '?' (so '**/*.*' and '*.md' are
    refused; name the top-level directory or file), and it must not match any of the
    canary paths.
    """
    if not _is_line(value):
        return "must be a non-empty single-line string"
    if value != value.strip():
        return "has leading or trailing whitespace"
    if "\\" in value or "\0" in value:
        return "contains a backslash or NUL"
    pattern = _strip_dot_slash(value)
    if pattern.startswith("/"):
        return "is absolute (starts with '/')"
    if ".." in pattern.split("/"):
        return "has a '..' segment"
    if pattern.endswith("/"):
        pattern += "**"
    if any("**" in segment and segment != "**" for segment in pattern.split("/")):
        return "has a '**' that is not a whole path segment (write 'dir/**', not 'dir**')"
    if pattern in UNSAFE_GLOBS:
        return "matches every path"
    first = pattern.split("/", 1)[0]
    if "*" in first or "?" in first:
        return "has a '*' or '?' in its first path segment (name the top-level directory or file)"
    regex = glob_to_regex(pattern)
    if any(regex.fullmatch(path) for path in GLOB_CANARIES):
        return "matches every path"
    return None


def _is_safe_glob(value: Any) -> bool:
    return _glob_problem(value) is None


def _names_path(globs: list[str], rel: str) -> bool:
    """Return True when a files entry names rel itself rather than a wildcard over it."""
    return any(_strip_dot_slash(pattern) == rel for pattern in globs)


def _normalized_glob(pattern: str) -> str:
    """Return pattern without a leading './', with a trailing '/' written as '/**'."""
    text = _strip_dot_slash(pattern)
    return f"{text}**" if text.endswith("/") else text


def own_miniprd_globs(nn: int) -> list[str]:
    """Return the globs of slice nn's own MiniPRD paths (compiled and archived copies)."""
    return [pattern.format(nn=nn) for pattern in OWN_MINIPRD_GLOBS]


def out_of_scope(paths: Iterable[str], globs: list[str], nn: int) -> list[str]:
    """Return the changed paths that neither the gate's exemptions nor the files globs allow.

    The exemptions are the ledger, activeContext.md (scope_check checks its protected
    procedure section separately) and slice nn's own MiniPRD paths.
    """
    patterns = [glob_to_regex(g) for g in (*own_miniprd_globs(nn), *globs)]
    return sorted(
        path
        for path in paths
        if path not in ALWAYS_IN_SCOPE and not any(regex.fullmatch(path) for regex in patterns)
    )


def _bookkeeping(path: str, nn: int) -> bool:
    """Return True for a path the gate exempts: ledger, activeContext.md, own MiniPRD."""
    return not out_of_scope([path], [], nn)


# ---------------------------------------------------------------------------
# Markdown: headings, fences, anchors
# ---------------------------------------------------------------------------


class _Heading(NamedTuple):
    line: int
    level: int
    text: str


class _Fence(NamedTuple):
    line: int
    info: str
    body: str
    closed: bool
    end: int  # the closing fence line (len(lines) when the fence is never closed)


def _fenced_lines(fences: list[_Fence]) -> set[int]:
    """Return the line numbers that fences cover, their fence lines included."""
    return {number for fence in fences for number in range(fence.line, fence.end + 1)}


def _outline(lines: list[str]) -> tuple[list[_Heading], list[_Fence]]:
    """Return the ATX headings outside fenced code and the fenced code blocks."""
    headings: list[_Heading] = []
    fences: list[_Fence] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        opening = FENCE_OPEN_RE.fullmatch(line)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            marker = opening.group(1)
            end = index + 1
            while end < len(lines):
                closing = FENCE_CLOSE_RE.fullmatch(lines[end])
                if (
                    closing
                    and closing.group(1)[0] == marker[0]
                    and len(closing.group(1)) >= len(marker)
                ):
                    break
                end += 1
            body = "\n".join(lines[index + 1 : end])
            fences.append(_Fence(index, opening.group(2).strip(), body, end < len(lines), end))
            index = end + 1
            continue
        heading = ATX_HEADING_RE.fullmatch(line)
        if heading:
            text = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading.group(2) or "").strip()
            headings.append(_Heading(index, len(heading.group(1)), text))
        index += 1
    return headings, fences


def github_slug(text: str) -> str:
    """Return the GitHub-style anchor slug of a heading text."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def resolve_annex_ref(
    repo: Path,
    ref: str,
    cache: dict[str, tuple[str, set[str]]],
    parsed: dict[str, bytes] | None = None,
) -> str | None:
    """Return None when 'path#anchor' resolves, else the reason it does not.

    parsed, when given, receives {repo-relative path: bytes} of each file read.
    """
    rel, _, anchor = ref.partition("#")
    target = _inside_repo(repo, rel)
    if target is None:
        return "the path must be relative and stay inside the repository"
    if not target.is_file():
        return f"{rel} does not exist"
    if rel not in cache:
        try:
            data = target.read_bytes()
        except OSError as exc:
            return f"cannot read {rel}: {_one_line(exc)}"
        text = data.decode("utf-8", errors="replace")
        headings, _ = _outline(text.splitlines())
        cache[rel] = (text, {github_slug(heading.text) for heading in headings})
        if parsed is not None:
            parsed[target.relative_to(repo.resolve()).as_posix()] = data
    text, slugs = cache[rel]
    if f'<a id="{anchor}"></a>' in text or anchor in slugs:
        return None
    return f'{rel} has no <a id="{anchor}"></a> and no heading with that slug'


def resolve_lore_ref(repo: Path, ref: str) -> str | None:
    """Return None when lore/ADRs/<ref>-*.md or lore/STORIES/<ref>-*.md exists."""
    if not LORE_REF_RE.fullmatch(ref):
        return "not a plain lore id such as ADR-0003 or STORY-0001"
    for folder in ("lore/ADRs", "lore/STORIES"):
        if any(path.is_file() for path in (repo / folder).glob(f"{ref}-*.md")):
            return None
    return f"no lore/ADRs/{ref}-*.md or lore/STORIES/{ref}-*.md"


# ---------------------------------------------------------------------------
# hacf-bootstrap block
# ---------------------------------------------------------------------------

YAML_MERGE_TAG = "tag:yaml.org,2002:merge"


def _mapping_nodes(root: yaml.Node) -> Iterator[yaml.MappingNode]:
    """Yield each mapping node of a composed YAML node tree once (aliases included)."""
    pending = [root]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, yaml.MappingNode):
            yield node
            for key_node, value_node in node.value:
                pending += [key_node, value_node]
        elif isinstance(node, yaml.SequenceNode):
            pending += node.value


def _refuse_duplicate_keys(root: yaml.Node) -> None:
    """Raise a ConstructorError when any mapping in the node tree repeats a scalar key.

    A second '<<' merge key in one mapping is a repeated key too (PyYAML would silently
    keep the last merge).
    """
    for node in _mapping_nodes(root):
        keys: set[tuple[str, str]] = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = (key_node.tag, key_node.value)
                if key in keys:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {key_node.value!r}",
                        key_node.start_mark,
                    )
                keys.add(key)


def _refuse_merge_keys(root: yaml.Node) -> None:
    """Raise a ConstructorError at a '<<' merge key anywhere in the node tree."""
    for node in _mapping_nodes(root):
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.tag == YAML_MERGE_TAG:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found a '<<' merge key, which an {BLOCK_INFO} block may not use",
                    key_node.start_mark,
                )


class _UniqueKeyLoader(yaml.SafeLoader):
    """A SafeLoader that refuses duplicate mapping keys (plain PyYAML keeps the last one).

    The check runs on the composed nodes before construction, so a key that overrides a
    '<<' merge is not a duplicate, but a second '<<' in one mapping is. Works with PyYAML
    5.4 and 6.
    """

    def construct_document(self, node: yaml.Node) -> Any:
        """Refuse duplicate keys anywhere in the document, then construct it."""
        _refuse_duplicate_keys(node)
        return super().construct_document(node)


class _BlockLoader(_UniqueKeyLoader):
    """A _UniqueKeyLoader that also refuses '<<' merge keys: a block spells out every key."""

    def construct_document(self, node: yaml.Node) -> Any:
        """Refuse merge keys anywhere in the block, then construct it."""
        _refuse_merge_keys(node)
        return super().construct_document(node)


def _load_yaml(text: str, loader: type[_UniqueKeyLoader] = _UniqueKeyLoader) -> Any:
    """Parse one YAML document like yaml.safe_load, but refuse duplicate mapping keys.

    loader _BlockLoader also refuses merge keys (for hacf-bootstrap blocks).
    """
    parser = loader(text)
    try:
        return parser.get_single_data()
    finally:
        parser.dispose()


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_line(value: Any) -> bool:
    return _is_text(value) and "\n" not in value and "\r" not in value


def _title_problem(value: Any) -> str | None:
    """Return why a block title cannot go into a commit subject, or None when it can."""
    if not _is_line(value):
        return "must be a non-empty single-line string"
    problem = _text_problem(value)
    if problem:
        return problem
    if "hacf-" in value.lower():
        return "must not contain 'HACF-' (the prefix of the gate's commit trailers)"
    return None


def _is_sid(value: Any) -> bool:
    return isinstance(value, str) and SID_RE.fullmatch(value) is not None


def _is_nn(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 99


def _is_argv(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


def _is_annex_ref(value: Any) -> bool:
    if not isinstance(value, str) or value.count("#") != 1:
        return False
    path, anchor = value.split("#")
    return bool(path.strip()) and bool(anchor.strip())


def _list_of(item: Callable[[Any], bool], non_empty: bool = False) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        if not isinstance(value, list) or (non_empty and not value):
            return False
        return all(item(entry) for entry in value)

    return check


_BLOCK_RULES: tuple[tuple[str, Callable[[Any], bool], str], ...] = (
    ("slice", _is_sid, "a SID such as S00, S01a, S09a1 or S15b"),
    ("nn", _is_nn, "an integer 0-99"),
    ("depends_on", _list_of(_is_sid), "a list of SIDs"),
    ("node_id", _is_text, "a non-empty string ('none' when there is no node)"),
    ("parent", _is_text, "a non-empty string ('none' when there is no parent)"),
    ("ensure_node", _is_text, "a non-empty string ('none' when there is no command)"),
    ("files", _list_of(_is_safe_glob, non_empty=True), "a non-empty list of safe glob strings"),
    ("oracle", _list_of(_is_argv), "a list of argv lists of strings"),
    ("audit_phase3", lambda v: v in AUDIT_PHASE3_MODES, "legacy-haiku, review-patch or skip"),
    ("removes_nodes", _list_of(_is_text), "a list of ledger node ids"),
    ("track_spec", lambda v: isinstance(v, bool), "true or false"),
    ("lore_refs", _list_of(_is_text), "a list of lore ids"),
    ("annex_refs", _list_of(_is_annex_ref), "a list of 'path#anchor' strings"),
)


def validate_block(data: Any) -> list[str]:
    """Return the schema problems of a parsed hacf-bootstrap block (empty when valid)."""
    if not isinstance(data, dict):
        return [f"the {BLOCK_INFO} block must be a YAML mapping (got {_describe(data)})"]
    problems: list[str] = []
    missing = [key for key in BLOCK_KEYS if key not in data]
    extra = sorted(str(key) for key in data if key not in BLOCK_KEYS)
    if missing:
        problems.append("missing key(s): " + ", ".join(missing))
    if extra:
        problems.append("unexpected key(s): " + ", ".join(extra))
    for key, valid, expected in _BLOCK_RULES:
        if key in data and not valid(data[key]):
            problems.append(f"{key} must be {expected} (got {_describe(data[key])})")
    title = _title_problem(data["title"]) if "title" in data else None
    if title:
        problems.append(f"title {title} (got {_describe(data['title'])})")
    if isinstance(data.get("files"), list):
        for entry in data["files"]:
            reason = _glob_problem(entry)
            if reason:
                problems.append(f"files entry {entry!r}: {reason}")
    nn = data.get("nn")
    if isinstance(nn, str) and nn.isdigit():
        problems.append(f"write nn as a plain integer ({int(nn)}), not {nn!r}")
    return problems


def parse_block_text(text: str, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and validate the hacf-bootstrap block in MiniPRD text; return (block, problems)."""
    lines = text.splitlines()
    headings, fences = _outline(lines)
    notes = [h for h in headings if h.level == 2 and NOTES_TITLE_RE.fullmatch(h.text)]
    if len(notes) != 1:
        found = f"found {len(notes)}"
        return None, [f"{label}: expected one '## Pipeline Operating Notes' section, {found}"]
    start = notes[0].line
    end = next((h.line for h in headings if h.line > start and h.level <= 2), len(lines))
    blocks = [fence for fence in fences if fence.info.split()[:1] == [BLOCK_INFO]]
    if not blocks:
        return None, [f"{label}: no ```{BLOCK_INFO} fenced block"]
    if len(blocks) > 1:
        return None, [f"{label}: {len(blocks)} {BLOCK_INFO} blocks; expected exactly one"]
    block = blocks[0]
    where = f"{label}:{block.line + 1}"
    if not start < block.line < end:
        return None, [f"{where}: the {BLOCK_INFO} block is outside '## Pipeline Operating Notes'"]
    if not block.closed:
        return None, [f"{where}: the {BLOCK_INFO} fence is never closed"]
    try:
        data = _load_yaml(block.body, _BlockLoader)
    except yaml.YAMLError as exc:
        return None, [f"{where}: the {BLOCK_INFO} block is not valid YAML: {_one_line(exc)}"]
    problems = [f"{where}: {problem}" for problem in validate_block(data)]
    if problems:
        return None, problems
    return data, []


def parse_block(path: Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and validate the hacf-bootstrap block of a MiniPRD file; return (block, problems)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"{label}: cannot read: {_one_line(exc)}"]
    return parse_block_text(text, label)


# ---------------------------------------------------------------------------
# MiniPRD shape: the Overrides banner and the seed cross-check
# ---------------------------------------------------------------------------


def banner_problem(text: str) -> str | None:
    """Return why MiniPRD text lacks a valid Overrides banner (R15), or None.

    Between the first '**Parent Node:**' line and the '## 1. The Confidence Mandate'
    heading (both outside fenced code) a line must begin '> **Overrides (read first):**'.
    Its blockquote paragraph (the '>' lines from there to a blank or bare '>' line), with
    the markers dropped and whitespace collapsed, must contain '## Pipeline Operating
    Notes', so a wrapped pointer still counts.
    """
    lines = text.splitlines()
    headings, fences = _outline(lines)
    fenced = _fenced_lines(fences)
    parent = next(
        (
            number
            for number, line in enumerate(lines)
            if number not in fenced and line.startswith(PARENT_NODE_PREFIX)
        ),
        None,
    )
    mandate = next(
        (h.line for h in headings if h.level == 2 and h.text == CONFIDENCE_HEADING), None
    )
    if parent is None:
        return f"no '{PARENT_NODE_PREFIX}' line"
    if mandate is None:
        return f"no '## {CONFIDENCE_HEADING}' heading"
    start = next(
        (
            number
            for number in range(parent + 1, mandate)
            if number not in fenced and lines[number].startswith(BANNER_PREFIX)
        ),
        None,
    )
    if start is None:
        return (
            f"no line beginning '{BANNER_PREFIX}' between the '{PARENT_NODE_PREFIX}' line "
            f"and '## {CONFIDENCE_HEADING}' (the Overrides banner, R15)"
        )
    quoted: list[str] = []
    for line in lines[start:mandate]:
        stripped = line.strip()
        content = stripped[1:].strip() if stripped.startswith(">") else ""
        if not content:
            break
        quoted.append(content)
    if NOTES_POINTER not in " ".join(" ".join(quoted).split()):
        return f"the Overrides banner's blockquote paragraph does not point to '{NOTES_POINTER}'"
    return None


class _Seed(NamedTuple):
    sid: str
    files: list[tuple[str, str]]  # (section, cleaned entry)
    tests: list[str]  # test_ tokens, a trailing '*' kept
    depends: list[str] | None  # SIDs on the '- **Depends on:**' line; None without one


def _seed_entry(text: str) -> str | None:
    """Return the path of a seed file item: its first token, cleaned; None for '(none)'.

    Backticks and trailing punctuation are stripped (repeatedly, so '`a.py`,' gives
    'a.py'), and a leading './' is dropped.
    """
    words = text.split()
    if not words:
        return None
    token = words[0]
    while True:
        cleaned = token.strip("`").rstrip(SEED_ENTRY_PUNCTUATION)
        if cleaned == token:
            break
        token = cleaned
    token = _strip_dot_slash(token)
    return None if not token or token == "(none)" else token


def _parse_seed(sid: str, region: list[str]) -> _Seed:
    """Parse one seed's lines (outside fenced code) into its files, tests and depends_on."""
    files: list[tuple[str, str]] = []
    tests: list[str] = []
    depends: list[str] | None = None
    section: str | None = None
    for line in region:
        depends_line = SEED_DEPENDS_RE.match(line)
        if depends_line:
            if depends is None:
                depends = SID_TOKEN_RE.findall(depends_line.group(1))
            continue
        stripped = line.strip()
        if stripped.startswith("**") or ATX_HEADING_RE.fullmatch(line):
            label = stripped[2:-2] if len(stripped) > 4 and stripped.endswith("**") else ""
            known = label in (*SEED_FILE_SECTIONS, SEED_TESTS_SECTION)
            section = label if known else None
            continue
        if section in SEED_FILE_SECTIONS and line.startswith("- "):
            entry = _seed_entry(line[2:])
            if entry is not None and (section, entry) not in files:
                files.append((section, entry))
        elif section == SEED_TESTS_SECTION:
            tests += TEST_NAME_RE.findall(line)
    return _Seed(sid, files, list(dict.fromkeys(tests)), depends)


def parse_seeds(text: str) -> list[_Seed]:
    """Return the seeds of slices.md text, in file order.

    A seed runs from its '<a id="sNN">' anchor to the next '<a id=...>' anchor (or the
    end of the file); anchors and lines inside fenced code do not count. Its section
    'Files to create', 'Files to modify' or 'Files to delete' (a '**...**' line) lists
    one path per '- ' item, and 'Tests (deterministic)' holds the test_ tokens; each
    section ends at the next '**' line or heading.
    """
    lines = text.splitlines()
    _, fences = _outline(lines)
    fenced = _fenced_lines(fences)
    anchors = [
        number
        for number, line in enumerate(lines)
        if number not in fenced and ANY_ANCHOR_RE.search(line)
    ]
    seeds: list[_Seed] = []
    for position, number in enumerate(anchors):
        match = SEED_ANCHOR_RE.search(lines[number])
        if not match:
            continue
        end = anchors[position + 1] if position + 1 < len(anchors) else len(lines)
        region = [lines[index] for index in range(number + 1, end) if index not in fenced]
        seeds.append(_parse_seed(f"S{match.group(1)[1:]}", region))
    return seeds


def parse_deviations(text: str) -> set[tuple[str, str]]:
    """Return the (SID, item) rows of the SuperPRD's Seed deviations table.

    The table is the first Markdown table after the line that contains '**Seed
    deviations**' (before the next heading). Cells are trimmed and lose surrounding
    backticks; a row counts only when its third cell names an R-row such as R14.
    """
    lines = text.splitlines()
    start = next((n for n, line in enumerate(lines) if DEVIATIONS_MARKER in line), None)
    if start is None:
        return set()
    rows: set[tuple[str, str]] = set()
    in_table = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table or ATX_HEADING_RE.fullmatch(line):
                break
            continue
        in_table = True
        cells = [cell.strip().strip("`").strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 3 and R_ROW_RE.search(cells[2]):
            rows.add((cells[0], cells[1]))
    return rows


def _seed_gaps(seed: _Seed, block: dict[str, Any], text: str) -> list[tuple[str, str]]:
    """Return (item, problem) for each way the MiniPRD (block, text) misses its seed.

    A seed file must match a block files glob; one that is itself a glob (or ends with
    '/', which means '/**') must equal a files entry. The gate's exemptions (the ledger,
    activeContext.md and the slice's own MiniPRD paths) are skipped. Every seed test name
    must appear in the MiniPRD text; one ending in '*' needs a longer name with that
    prefix. depends_on must equal, as a set, the SIDs of the seed's Depends on line.
    """
    gaps: list[tuple[str, str]] = []
    nn = block["nn"]
    for section, item in seed.files:
        pattern = f"{item}**" if item.endswith("/") else item
        if "*" in pattern or "?" in pattern:
            if pattern in own_miniprd_globs(nn):
                continue
            if pattern not in {_normalized_glob(g) for g in block["files"]}:
                gaps.append((item, f"seed file {item} ({section}) equals no files entry"))
        elif not _bookkeeping(pattern, nn):
            if not any(glob_to_regex(g).fullmatch(pattern) for g in block["files"]):
                gaps.append((item, f"seed file {item} ({section}) matches no files glob"))
    names = {name for name in TEST_NAME_RE.findall(text) if not name.endswith("*")}
    for token in seed.tests:
        prefix = token[:-1]
        if not token.endswith("*"):
            if token not in names:
                gaps.append((token, f"seed test {token} does not appear in the MiniPRD"))
        elif not any(name.startswith(prefix) and len(name) > len(prefix) for name in names):
            gaps.append((token, f"seed test {token} has no test name with the prefix {prefix}"))
    if seed.depends is not None:
        wanted, actual = set(seed.depends), set(block["depends_on"])
        for dep in sorted(wanted - actual):
            gaps.append((dep, f"depends_on lacks {dep}, which the seed's Depends on line lists"))
        for dep in sorted(actual - wanted):
            gaps.append((dep, f"depends_on lists {dep}, which the seed's Depends on line lacks"))
    return gaps


def _seed_problems(
    repo: Path, blocks: dict[str, tuple[str, dict[str, Any], str]], read: dict[str, bytes]
) -> list[str]:
    """Cross-check every seed of slices.md with the MiniPRD that declares its SID (RT-104).

    blocks maps each SID to (MiniPRD label, block, MiniPRD text). A gap passes only when
    the SuperPRD's Seed deviations table has a row naming the SID and the exact item (the
    seed file, the test token or the dependency SID). A seed without a MiniPRD or without
    a Depends on line is refused outright. read receives the bytes of slices.md and of the
    SuperPRD (under its committed name), so post-resolve checks they are staged as read.
    """
    try:
        data = (repo / SLICES).read_bytes()
        text = data.decode("utf-8")
    except FileNotFoundError:
        return [f"{SLICES} does not exist; post-resolve cross-checks every seed with its MiniPRD"]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read {SLICES}: {_one_line(exc)}"]
    read[SLICES] = data
    bare, v060 = repo / SUPERPRD_BARE, repo / SUPERPRD_V060
    deviations: set[tuple[str, str]] = set()
    try:
        superprd = (bare if bare.exists() else v060).read_bytes()
    except OSError:
        superprd = None  # the SuperPRD checks of post-resolve report the missing file
    if superprd is not None:
        if not (bare.exists() and v060.exists()):
            read[SUPERPRD_V060] = superprd
        deviations = parse_deviations(superprd.decode("utf-8", errors="replace"))
    problems: list[str] = []
    for seed in parse_seeds(text):
        where = f"{SLICES}#{seed.sid.lower()}"
        found = blocks.get(seed.sid)
        if found is None:
            problems.append(f"{where}: no {COMPILED_DIR} MiniPRD declares slice {seed.sid}")
            continue
        if seed.depends is None:
            problems.append(f"{where}: the seed has no '- **Depends on:**' line")
        label, block, miniprd = found
        for item, gap in _seed_gaps(seed, block, miniprd):
            if (seed.sid, item) not in deviations:
                problems.append(
                    f"{label}: {gap} (seed {seed.sid}; only a SuperPRD Seed deviations row "
                    f"'| {seed.sid} | {item} | R<n> |' accepts this)"
                )
    return problems


# ---------------------------------------------------------------------------
# Ledger, guards, git history
# ---------------------------------------------------------------------------


def load_ledger(text: str, label: str) -> tuple[dict[str, set[str]] | None, list[str]]:
    """Parse ledger text; return ({node id: top-level keys}, problems).

    The mapping is None when the text does not parse (a duplicate key included) or has no
    'nodes' list.
    """
    try:
        data = _load_yaml(text)
    except yaml.YAMLError as exc:
        return None, [f"{label} does not parse: {_one_line(exc)}"]
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None, [f"{label} must be a mapping with a 'nodes' list"]
    nodes: dict[str, set[str]] = {}
    problems: list[str] = []
    for index, node in enumerate(data["nodes"]):
        if not isinstance(node, dict):
            problems.append(f"{label}: nodes[{index}] is not a mapping")
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            problems.append(f"{label}: nodes[{index}] has no string id")
            continue
        if node_id in nodes:
            problems.append(f"{label}: node {node_id} appears more than once")
        nodes[node_id] = {str(key) for key in node}
        edges = node.get("edges")
        if not isinstance(edges, dict):
            problems.append(f"{label}: node {node_id}: edges must be a mapping")
            continue
        for key in ("depends_on", "implements"):
            if not isinstance(edges.get(key), list):
                got = _describe(edges.get(key))
                problems.append(f"{label}: node {node_id}: edges.{key} must be a list ({got})")
    return nodes, problems


def compare_ledgers(
    old: dict[str, set[str]],
    current: dict[str, set[str]],
    removes_nodes: list[str],
    where: str = "HEAD",
) -> list[str]:
    """Return node ids and per-node keys present at `where` but missing now (non-regression)."""
    problems: list[str] = []
    declared = set(removes_nodes)
    for node_id in sorted(set(old) - set(current)):
        if node_id not in declared:
            problems.append(
                f"{LEDGER}: node {node_id} was removed but is not listed in removes_nodes"
            )
    for node_id in sorted(set(old) & set(current)):
        lost = sorted(old[node_id] - current[node_id])
        if lost:
            keys = ", ".join(lost)
            problems.append(f"{LEDGER}: node {node_id} lost key(s) present at {where}: {keys}")
    for node_id in sorted(declared & set(current)):
        _warn(f"removes_nodes lists {node_id}, but it is still in the ledger")
    return problems


def check_ledger(repo: Path, removes_nodes: list[str], tag: str, base: str) -> list[str]:
    """Check that the ledger parses, edges are lists and nothing was dropped versus base.

    base is the scope base commit. HEAD stands in only when the base has no ledger; a
    committed ledger that exists but does not load is a problem, because then nothing
    can show that a node or key was dropped.
    """
    path = repo / LEDGER
    if not path.is_file():
        return [f"{LEDGER} is missing (restore it with: v060_gate.py restore-ledger)"]
    text = path.read_text(encoding="utf-8", errors="replace")
    current, problems = load_ledger(text, LEDGER)
    if current is None:
        return problems
    where = f"the scope base {base[:12]}"
    old_text = _blob_text(repo, base, LEDGER)
    if old_text is None:
        _warn(f"{where} has no {LEDGER}; comparing with HEAD instead")
        where = "HEAD"
        old_text = _blob_text(repo, "HEAD", LEDGER)
    if old_text is None:
        _warn(f"HEAD has no {LEDGER} either; skipped the non-regression comparison")
        compared = "no committed ledger to compare with"
    else:
        old, old_problems = load_ledger(old_text, f"the ledger at {where}")
        if old is None:
            problems.append(
                f"{old_problems[0]}; without it finish cannot check that no node or key was dropped"
            )
            compared = ""
        else:
            problems += compare_ledgers(old, current, removes_nodes, where)
            compared = f"no node ids or keys dropped versus {where}"
    if not problems:
        _info(f"{tag}: ledger parses ({len(current)} nodes, edges are lists, {compared})")
    return problems


class _ValidatorChange(NamedTuple):
    label: str  # the validator and its arguments
    new: list[str]  # what its scope-base copy reports on the working tree only


@contextlib.contextmanager
def _base_checkout(repo: Path, base: str) -> Iterator[Path]:
    """Check out commit base in a throwaway repository and yield its work tree.

    The repository borrows repo's objects (objects/info/alternates), so git inside it
    resolves base and its ancestors; HEAD is base, detached. Nothing is written to repo,
    and no hook runs. The directory is removed afterwards.
    """
    with tempfile.TemporaryDirectory(prefix="v060-gate-base-") as folder:
        tree = Path(folder) / "base"
        _git(Path(folder), "init", "-q", str(tree))
        objects = (repo / _git(repo, "rev-parse", "--git-path", "objects").stdout.strip()).resolve()
        alternates = tree / ".git" / "objects" / "info" / "alternates"
        alternates.write_text(f"{objects}\n", encoding="utf-8")
        with _no_hooks() as hooks:
            _git(tree, *hooks, "checkout", "-q", "--detach", base)
        yield tree


def _findings(output: str, roots: tuple[Path, ...]) -> list[str]:
    """Return the non-blank output lines, whitespace collapsed and each root as '<root>'."""
    forms = {form for root in roots for form in (str(root), str(root.resolve()))}
    text = output
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "<root>")
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _print_output(title: str, code: int, output: str) -> None:
    """Print a captured validator output (at most OUTPUT_LINES lines) to stderr."""
    lines = output.splitlines()
    print(f"{title} (exit {code}, {len(lines)} line(s)):", file=sys.stderr, flush=True)
    for line in lines[:OUTPUT_LINES]:
        print(f"    | {_safe(line)}", file=sys.stderr, flush=True)
    if len(lines) > OUTPUT_LINES:
        print(f"    | ... {len(lines) - OUTPUT_LINES} more line(s)", file=sys.stderr, flush=True)


def run_validators(repo: Path, tag: str, base: str) -> tuple[list[str], list[_ValidatorChange]]:
    """Run the validators and their scope-base copies; return (problems, changes).

    Each validator of VALIDATORS that exists in the working tree must exit 0 (the ledger
    validator compares against the scope base: --against-ref base). One that exists at
    the scope base must also exist in the working tree. Its scope-base copy (the file
    from a throwaway checkout of the scope base) then runs twice with the same
    arguments: in the repository, on the working tree, and in the checkout, on the scope
    base's own tree. A finding (output line) or a failing exit that only the working-tree
    run reports is a change: finish prints both outputs and refuses it unless
    --accept-validator-change. The copy finds the tree from its working directory, as
    the engine's base-revision copies must (R4); a validator that located the
    repository from its own path would compare the checkout with itself. problems are
    never waivable.
    """
    problems: list[str] = []
    at_base: list[tuple[str, list[str]]] = []
    for rel, template in VALIDATORS:
        args = [base if arg == SCOPE_BASE_ARG else arg for arg in template]
        exists_at_base = _blob(repo, base, rel) is not None
        if exists_at_base:
            at_base.append((rel, args))
        tool = repo / rel
        if tool.is_file():
            _info(f"{tag}: running {_python()} {rel} {' '.join(args)}")
            code, _ = _run_script([_python(), str(tool), *args], repo, VALIDATOR_TIMEOUT)
            if code != 0:
                problems.append(f"{rel} {' '.join(args)} exited {code}")
        elif exists_at_base:
            problems.append(
                f"{rel} exists at the scope base {base[:12]} but not in the working tree; "
                f"finish never skips a validator once it exists (restore it: git checkout "
                f"{base[:12]} -- {rel}; no flag waives this)"
            )
    changes: list[_ValidatorChange] = []
    if not at_base:
        return problems, changes
    with _base_checkout(repo, base) as tree:
        for rel, args in at_base:
            label = f"{rel} {' '.join(args)}"
            argv = [_python(), str(tree / rel), *args]
            _info(f"{tag}: running the scope-base copy of {rel} on the scope base and here")
            old_code, old_output = _run_script(argv, tree, VALIDATOR_TIMEOUT, capture=True)
            new_code, new_output = _run_script(argv, repo, VALIDATOR_TIMEOUT, capture=True)
            if old_code != 0:
                _warn(
                    f"{tag}: the scope-base copy of {rel} exits {old_code} on the scope base's "
                    "own tree"
                )
            known = set(_findings(old_output, (tree,)))
            new = [line for line in _findings(new_output, (repo, tree)) if line not in known]
            if new_code != 0 and old_code == 0:
                new.append(f"(exit {new_code}; exit 0 on the scope base)")
            new = list(dict.fromkeys(new))
            if not new:
                _info(f"{tag}: the scope-base copy of {rel} reports nothing new here")
                continue
            where = f"{tag}: the scope-base copy of {label}"
            _print_output(f"{where}, on the scope base {base[:12]}", old_code, old_output)
            _print_output(f"{where}, on the working tree", new_code, new_output)
            changes.append(_ValidatorChange(label, new))
    return problems, changes


class _Guard(NamedTuple):
    path: str
    state: str
    detail: str
    expected: str


def _guard_states(repo: Path) -> list[_Guard]:
    """Return one entry per guards.json entry; state is ok, missing, mismatch or invalid.

    A guarded path (or guards.json itself) that is a symlink, is not a regular file or is
    reached through a symlinked directory is invalid.
    """
    problem = _plain_file_problem(repo, GUARDS)
    if problem:
        return [_Guard(GUARDS, "invalid", problem, "")]
    try:
        data = json.loads((repo / GUARDS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [_Guard(GUARDS, "missing", "the guards file does not exist", "")]
    except (OSError, ValueError) as exc:
        return [_Guard(GUARDS, "invalid", _one_line(exc), "")]
    if not isinstance(data, dict):
        return [_Guard(GUARDS, "invalid", "expected a JSON object {relpath: sha256}", "")]
    states: list[_Guard] = []
    for rel, expected in sorted(data.items()):
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected.lower()):
            states.append(_Guard(rel, "invalid", f"not a sha256: {expected!r}", ""))
            continue
        digest = expected.lower()
        if any(unicodedata.category(char) in FORBIDDEN_CATEGORIES for char in rel):
            detail = "the path has a NUL, control, format or separator character"
            states.append(_Guard(rel, "invalid", detail, digest))
            continue
        target = _inside_repo(repo, rel)
        problem = _plain_file_problem(repo, rel) if target is not None else None
        if target is None:
            states.append(_Guard(rel, "invalid", "the path leaves the repository", digest))
        elif problem:
            states.append(_Guard(rel, "invalid", problem, digest))
        elif not target.is_file():
            states.append(_Guard(rel, "missing", "the file does not exist", digest))
        else:
            actual = _sha256_file(target)
            if actual == digest:
                states.append(_Guard(rel, "ok", actual, digest))
            else:
                detail = f"sha256 is {actual}, guards.json has {expected}"
                states.append(_Guard(rel, "mismatch", detail, digest))
    return states


def _archive_digests(repo: Path) -> dict[str, list[str]]:
    """Return {sha256: paths} for the regular files under spec/archive/ (ignored ones too)."""
    found: dict[str, list[str]] = {}
    top = repo / ARCHIVE_DIR
    if top.is_symlink() or not top.is_dir():
        return found
    for folder, dirs, names in os.walk(top):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(repo).as_posix()
            found.setdefault(_sha256_file(path), []).append(rel)
    return found


class _Commit(NamedTuple):
    sha: str
    subject: str
    body: list[str]  # the message lines after the subject line
    trailers: list[str]  # the lines of the final paragraph (none without a body)


def _finish_guards(repo: Path, post: _Commit, tag: str) -> list[str]:
    """Return the guard problems of a finish; no waiver flag accepts any of them.

    guards.json must equal its copy in the post-resolve commit. Each guarded file must be
    unchanged, or missing with a byte-identical copy under spec/archive/ (S15b moves the
    hyper_update SuperPRD there).
    """
    problems: list[str] = []
    pinned = _blob(repo, post.sha, GUARDS)
    if pinned is None:
        problems.append(f"the post-resolve commit {post.sha[:12]} has no {GUARDS}")
    elif _read_bytes(repo / GUARDS) != pinned:
        problems.append(
            f"{GUARDS} differs from its copy in the post-resolve commit {post.sha[:12]} "
            "(no flag waives this)"
        )
    archived: dict[str, list[str]] | None = None
    for guard in _guard_states(repo):
        name = _safe(guard.path)
        if guard.state == "ok":
            continue
        if guard.state == "missing" and guard.expected:
            if archived is None:
                archived = _archive_digests(repo)
            copies = archived.get(guard.expected, [])
            if copies:
                moved = ", ".join(_safe(copy) for copy in copies)
                _info(f"{tag}: guard {name}: moved byte-for-byte to {moved}")
                continue
            problems.append(
                f"guard {name}: missing, and no file under {ARCHIVE_DIR}/ has its sha256 (no "
                "flag waives this)"
            )
            continue
        problems.append(f"guard {name}: {guard.state}: {guard.detail} (no flag waives this)")
    return problems


def _parse_message(sha: str, message: str) -> _Commit:
    """Split a commit message into lines at LF only; trailers come from its final paragraph.

    Other line separators (U+2028, U+0085, ...) stay inside a line, as they do for git. A
    message that is a single paragraph (only a subject) has no trailers, as in git.
    """
    lines = message.split("\n")
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip(" \t\r"):
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    subject = paragraphs[0][0] if paragraphs else ""
    trailers = paragraphs[-1] if len(paragraphs) > 1 else []
    return _Commit(sha, subject, lines[1:], trailers)


def _log(repo: Path) -> list[_Commit]:
    """Return the commits reachable from HEAD, newest first (topological order).

    Records are NUL-separated (git refuses a NUL byte in a commit message), so no
    character inside a message can split or forge a record. Empty without HEAD.
    """
    if _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
        return []
    out = _git(
        repo,
        "log",
        "-z",
        "--topo-order",
        "--no-color",
        "--no-show-signature",
        "--format=%H%n%B",
    ).stdout
    commits: list[_Commit] = []
    for record in out.split("\0"):
        sha, newline, message = record.partition("\n")
        if not newline:
            continue
        if not OBJECT_ID_RE.fullmatch(sha):
            raise GateError(f"cannot parse git log output (record starts with {sha[:40]!r})")
        commits.append(_parse_message(sha, message))
    return commits


def _slice_trailers(commit: _Commit) -> list[str]:
    return [
        line[len(SLICE_TRAILER) :].strip()
        for line in commit.trailers
        if line.startswith(SLICE_TRAILER)
    ]


def _waiver_trailers(commits: list[_Commit]) -> list[tuple[_Commit, str, str]]:
    """Return (commit, trailer, reason) for every waiver trailer, oldest commit first."""
    found: list[tuple[_Commit, str, str]] = []
    for commit in reversed(commits):
        for line in commit.trailers:
            for trailer in WAIVER_TRAILERS:
                if line.startswith(trailer):
                    found.append((commit, trailer, line[len(trailer) :].strip()))
    return found


def _listed(commit: _Commit, heading: str) -> list[str]:
    """Return the '- item' lines under heading in a commit body (the waiver records)."""
    items: list[str] = []
    collecting = False
    for line in commit.body:
        if line == heading and not collecting:
            collecting = True
        elif collecting and line.startswith("- "):
            items.append(line[2:])
        elif collecting:
            break
    return items


def _gate_refs(repo: Path) -> dict[str, str]:
    """Return {ref name: object id} for the refs under REF_ROOT (read-only)."""
    out = _git(repo, "for-each-ref", "--format=%(objectname) %(refname)", REF_ROOT).stdout
    refs: dict[str, str] = {}
    for line in out.split("\n"):
        sha, _, name = line.partition(" ")
        if name:
            refs[name] = sha
    return refs


def _commit_label(commit: _Commit) -> str:
    return f"{commit.sha[:12]} {_safe(commit.subject)}"


def _post_resolve_pin(
    commits: list[_Commit], refs: dict[str, str]
) -> tuple[_Commit | None, list[str]]:
    """Return (the commit POST_RESOLVE_REF names, why that pin cannot be trusted).

    The pin holds when the ref names an ancestor of HEAD that is the newest commit whose
    subject starts with POST_RESOLVE_PREFIX, and every such commit except the oldest has
    an HACF-Repin trailer. Before the first post-resolve there is neither a ref nor such a
    commit, which is not a problem.
    """
    found = [commit for commit in commits if commit.subject.startswith(POST_RESOLVE_PREFIX)]
    ref = refs.get(POST_RESOLVE_REF)
    if ref is None and not found:
        return None, []
    post = next((commit for commit in commits if commit.sha == ref), None)
    problems: list[str] = []
    if ref is None:
        problems.append(
            f"{POST_RESOLVE_REF} is missing; only v060_gate.py post-resolve records the "
            "post-resolve commit"
        )
    elif post is None:
        problems.append(
            f"{POST_RESOLVE_REF} names {ref[:12]}, which is not an ancestor of HEAD (was the "
            "post-resolve commit amended or reset?)"
        )
    if ref is not None and (not found or found[0].sha != ref):
        newest = _commit_label(found[0]) if found else "none"
        problems.append(
            f"the newest commit whose subject starts with '{POST_RESOLVE_PREFIX}' ({newest}) "
            f"is not the one {POST_RESOLVE_REF} names ({ref[:12]}); was it amended or made "
            "by hand?"
        )
    for commit in found[:-1]:
        if not _has_repin(commit):
            problems.append(
                f"{_commit_label(commit)} has the post-resolve subject but no "
                f"{_key(REPIN_TRAILER)} trailer; only post-resolve {REPIN_FLAG} REASON makes a "
                "later post-resolve commit"
            )
    return post, problems


def _has_repin(commit: _Commit) -> bool:
    return any(line.startswith(REPIN_TRAILER) for line in commit.trailers)


def _parents(repo: Path, sha: str) -> list[str]:
    """Return the parents of commit sha (read-only)."""
    return _git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _post_resolve_shape(repo: Path, resolves: list[_Commit]) -> list[str]:
    """Return why post-resolve-subject commits cannot be commits post-resolve made (read-only).

    post-resolve makes a commit with exactly one parent that changes, versus that parent,
    only paths _post_resolve_allows accepts. Any other such commit (an amend that added a
    path, a merge, a hand-made commit) may carry changes that no check saw, so each one is
    a pin problem that names the commit and the paths.
    """
    problems: list[str] = []
    for commit in resolves:
        parents = _parents(repo, commit.sha)
        if len(parents) != 1:
            problems.append(
                f"{_commit_label(commit)} has the post-resolve subject but {len(parents)} "
                "parents; post-resolve makes commits with exactly one"
            )
            continue
        listing = ("diff-tree", "-r", "--no-commit-id", "--name-only", "--no-renames", "-z")
        changed = _git_paths(repo, *listing, commit.sha)
        stray = sorted(path for path in changed if not _post_resolve_allows(path))
        if stray:
            listed = ", ".join(_safe(path) for path in stray)
            problems.append(
                f"{_commit_label(commit)} has the post-resolve subject but changes path(s) that "
                f"post-resolve never commits: {listed}"
            )
    return problems


def _pin_recovery(resolves: list[_Commit], refs: dict[str, str], reshaped: list[str]) -> list[str]:
    """Return the command that records a lost POST_RESOLVE_REF again ([] when none applies).

    It applies when the ref is missing but HEAD's history has post-resolve commits, each
    one shaped like a commit post-resolve makes, and every one but the oldest carries an
    HACF-Repin trailer. Only the user knows whether the ref was lost or removed on purpose,
    so the gate prints the command and never runs it.
    """
    if POST_RESOLVE_REF in refs or not resolves or reshaped:
        return []
    if not all(_has_repin(commit) for commit in resolves[:-1]):
        return []
    newest = resolves[0]
    return [
        f"the newest post-resolve commit is {_commit_label(newest)}; record it again with: "
        f'git update-ref {POST_RESOLVE_REF} {newest.sha} "" (run it only if the ref was lost, '
        "not removed on purpose)"
    ]


def _interrupted_advice(sha: str, sid: str) -> str:
    """Return the advice for HEAD sha, a finish of sid that did not record its slice ref."""
    return (
        f"HEAD {sha[:12]} is a finish of {_safe(sid)} that did not record its slice ref "
        f"(interrupted after the commit?); inspect it (git show --stat {sha[:12]}), then run "
        "git reset --soft HEAD^ and run finish again"
    )


_Pins = dict[str, list[tuple[str, dict[str, Any]]]]  # SID -> [(MiniPRD path, block)]


class _History(NamedTuple):
    commits: list[_Commit]  # reachable from HEAD, newest first
    refs: dict[str, str]  # the refs under REF_ROOT
    post: _Commit | None  # the commit POST_RESOLVE_REF names, if it is an ancestor of HEAD
    pin_problems: list[str]  # why the post-resolve pin cannot be trusted
    reshaped: list[str]  # post-resolve-subject commits that post-resolve cannot have made
    recovery: list[str]  # the command that records a lost POST_RESOLVE_REF again, if any
    completed: list[tuple[str, str]]  # (SID, sha) of the completed slices, oldest first
    base: _Commit | None  # the scope base
    warnings: list[str]  # ignored slice refs and gate-like commits that no ref names
    unrecorded: list[str]  # SIDs in HEAD's HACF-Slice trailers when no gate ref names HEAD
    pin_sha: str | None  # the commit whose blocks pin the SIDs: post, else the newest pin
    pinned: _Pins  # the blocks of pin_sha's spec/compiled MiniPRDs, by SID
    unreadable: list[str]  # pin_sha's MiniPRDs whose block does not parse


def _pinned_blocks(repo: Path, sha: str) -> tuple[_Pins, list[str]]:
    """Return ({SID: [(path, block), ...]}, problems) for the spec/compiled MiniPRDs at sha."""
    found: _Pins = {}
    unreadable: list[str] = []
    for rel, _ in _compiled_at(repo, sha):
        label = f"{sha[:12]}:{rel}"
        try:
            text = (_blob(repo, sha, rel) or b"").decode("utf-8")
        except UnicodeDecodeError as exc:
            unreadable.append(f"{label}: cannot read: {_one_line(exc)}")
            continue
        block, problems = parse_block_text(text, label)
        if block is None:
            unreadable += problems
        else:
            found.setdefault(block["slice"], []).append((rel, block))
    return found, unreadable


def _unique_pins(pinned: _Pins) -> dict[str, tuple[str, dict[str, Any]]]:
    """Return {SID: (path, block)} for the SIDs that exactly one pinned block declares."""
    return {sid: entries[0] for sid, entries in pinned.items() if len(entries) == 1}


def _history(repo: Path) -> _History:
    """Read the commits reachable from HEAD and the gate refs (read-only).

    A completed slice is a SID whose slice ref names an ancestor of HEAD carrying
    'HACF-Slice: <SID>', for a SID the post-resolve commit pins (exactly one of its
    spec/compiled blocks declares it; while POST_RESOLVE_REF is broken, the newest
    post-resolve-subject commit stands in). The scope base is the newest of the
    post-resolve commit and the completed slices' commits. A commit after the
    post-resolve commit with an HACF-Slice trailer that no slice or superseded ref names
    was made outside the gate: it is neither completed nor a scope base, so the next
    finish checks its changes. When that commit is HEAD, its SIDs go to unrecorded
    instead of the warnings: it is most likely a finish that was killed between its
    commit and its slice ref.
    """
    commits = _log(repo)
    refs = _gate_refs(repo)
    resolves = [commit for commit in commits if commit.subject.startswith(POST_RESOLVE_PREFIX)]
    reshaped = _post_resolve_shape(repo, resolves)
    recovery = _pin_recovery(resolves, refs, reshaped)
    post, pin_problems = _post_resolve_pin(commits, refs)
    pin_problems += [*reshaped, *recovery]
    pin_sha = post.sha if post is not None else (resolves[0].sha if resolves else None)
    pinned, unreadable = _pinned_blocks(repo, pin_sha) if pin_sha else ({}, [])
    pins = _unique_pins(pinned)
    position = {commit.sha: index for index, commit in enumerate(commits)}
    warnings: list[str] = []
    completed: list[tuple[str, str]] = []
    for name, sha in sorted(refs.items()):
        if not name.startswith(SLICE_REF_PREFIX):
            continue
        sid = name[len(SLICE_REF_PREFIX) :]
        if not SID_RE.fullmatch(sid):
            warnings.append(f"{name} does not name a slice id; ignored")
        elif sha not in position:
            warnings.append(
                f"{name} names {sha[:12]}, which is not an ancestor of HEAD; {sid} does not "
                "count as finished"
            )
        elif sid not in _slice_trailers(commits[position[sha]]):
            warnings.append(
                f"{name} names {_commit_label(commits[position[sha]])}, which has no "
                f"'{SLICE_TRAILER}{sid}' trailer; {sid} does not count as finished"
            )
        elif sid not in pins:
            where = (
                f"the post-resolve commit {pin_sha[:12]}" if pin_sha else "no post-resolve commit"
            )
            warnings.append(
                f"{name}: {where} does not pin {sid} (no single spec/compiled block declares "
                f"it); {sid} does not count as finished"
            )
        else:
            completed.append((sid, sha))
    completed.sort(key=lambda item: position[item[1]], reverse=True)
    candidates = [position[sha] for _, sha in completed]
    if post is not None:
        candidates.append(position[post.sha])
    base = commits[min(candidates)] if candidates else None
    named = {
        sha
        for name, sha in refs.items()
        if name.startswith((SLICE_REF_PREFIX, SUPERSEDED_REF_PREFIX))
    }
    found = [
        i for i, commit in enumerate(commits) if commit.subject.startswith(POST_RESOLVE_PREFIX)
    ]
    boundary = position[post.sha] if post is not None else (found[0] if found else 0)
    unrecorded: list[str] = []
    for commit in commits[:boundary]:
        sids = _slice_trailers(commit)
        if sids and commit.sha not in named:
            if commit is commits[0]:
                unrecorded = sids
                continue
            listed = ", ".join(f"{SLICE_TRAILER}{_safe(sid)}" for sid in sids)
            warnings.append(
                f"{_commit_label(commit)} has '{listed}' but no gate ref names it (made "
                "outside the gate?); it is not a finished slice, and the next finish checks "
                "its changes"
            )
    return _History(
        commits,
        refs,
        post,
        pin_problems,
        reshaped,
        recovery,
        completed,
        base,
        warnings,
        unrecorded,
        pin_sha,
        pinned,
        unreadable,
    )


def _record_value(item: str) -> str:
    """Return a '- item' record's value: JSON-quoted items (see _record_path) are decoded."""
    if not item.startswith('"'):
        return item
    try:
        value = json.loads(item)
    except ValueError:
        return item
    return value if isinstance(value, str) else item


def _key_change(key: str, old: Any, new: Any) -> str:
    """Return the body record of a changed block key: 'key: <old JSON> -> <new JSON>'."""
    return f"{key}: {json.dumps(old, ensure_ascii=True)} -> {json.dumps(new, ensure_ascii=True)}"


def _parse_key_change(item: str) -> tuple[str, Any, Any] | None:
    """Return (key, old, new) from a _key_change record, or None when it is not one."""
    key, sep, rest = item.partition(": ")
    if not sep:
        return None
    decoder = json.JSONDecoder()
    try:
        old, end = decoder.raw_decode(rest)
        if not rest.startswith(" -> ", end):
            return None
        new, end = decoder.raw_decode(rest, end + len(" -> "))
    except ValueError:
        return None
    return (key, old, new) if end == len(rest) else None


def _recorded_globs(commit: _Commit, block: dict[str, Any]) -> tuple[list[str], set[str]]:
    """Return (files globs, accepted paths) that a recorded slice commit was checked with.

    The globs are the pinned block's files plus, under an HACF-Block-Changed trailer, the
    new files value its body records; the accepted paths are those its body lists under
    an HACF-Scope-Accepted trailer.
    """
    trailers = {
        trailer
        for trailer in WAIVER_TRAILERS
        for line in commit.trailers
        if line.startswith(trailer)
    }
    globs = list(block["files"])
    if BLOCK_TRAILER in trailers:
        for item in _listed(commit, CHANGED_KEYS_HEADING):
            change = _parse_key_change(item)
            if change and change[0] == "files" and isinstance(change[2], list):
                globs += [glob for glob in change[2] if _is_safe_glob(glob)]
    accepted: set[str] = set()
    if SCOPE_TRAILER in trailers:
        accepted = {_record_value(item) for item in _listed(commit, ACCEPTED_PATHS_HEADING)}
    return globs, accepted


def _recorded_slice_problems(repo: Path, state: _History) -> list[str]:
    """Re-diff each completed slice's commit against what finish allowed it (RT-99).

    The commit is compared with the scope base its finish used: the gate commit before it
    on HEAD's first-parent chain (a post-resolve commit, a completed slice's commit, or a
    commit a superseded ref names, which was completed until --refinish replaced it).
    Every path that changed must be a gate exemption (the ledger, activeContext.md, its
    own MiniPRD paths) or allowed by _recorded_globs. A problem names the commit and the
    paths; no flag waives it.
    """
    pins = _unique_pins(state.pinned)
    by_sha = {commit.sha: commit for commit in state.commits}
    gate = {
        commit.sha for commit in state.commits if commit.subject.startswith(POST_RESOLVE_PREFIX)
    }
    gate |= {sha for _, sha in state.completed}
    gate |= {sha for name, sha in state.refs.items() if name.startswith(SUPERSEDED_REF_PREFIX)}
    chain = _git(repo, "rev-list", "--first-parent", "HEAD").stdout.split()
    index = {sha: number for number, sha in enumerate(chain)}
    problems: list[str] = []
    for sid, sha in state.completed:
        commit, (_, block) = by_sha[sha], pins[sid]
        label = f"{_commit_label(commit)} (the commit {SLICE_REF_PREFIX}{sid} records)"
        start = index.get(sha)
        base = None if start is None else next((c for c in chain[start + 1 :] if c in gate), None)
        if base is None:
            problems.append(
                f"{label} has no earlier gate commit on HEAD's first-parent chain to compare "
                "with (no flag waives this)"
            )
            continue
        listing = ("diff", "--name-only", "--no-renames", "--no-color", "-z", base, sha, "--")
        globs, accepted = _recorded_globs(commit, block)
        stray = [
            path
            for path in out_of_scope(_git_paths(repo, *listing), globs, block["nn"])
            if path not in accepted
        ]
        if stray:
            listed = ", ".join(_safe(path) for path in stray)
            problems.append(
                f"{label} changes path(s) versus {base[:12]} that neither {sid}'s pinned files "
                f"globs nor its waiver records allow: {listed} (made or moved outside the "
                "gate?; no flag waives this)"
            )
    return problems


def _unsliced_commits(repo: Path, base: _Commit, commits: list[_Commit]) -> list[_Commit]:
    """Return the commits in base..HEAD without an HACF-Slice trailer (manual commits)."""
    shas = _git(repo, "rev-list", f"{base.sha}..HEAD").stdout.split()
    by_sha = {commit.sha: commit for commit in commits}
    return [by_sha[sha] for sha in shas if sha in by_sha and not _slice_trailers(by_sha[sha])]


def changed_paths(repo: Path, base: str, force_dirs: Iterable[str]) -> set[str]:
    """Return the paths that differ from base and that git add would pick up.

    That is tracked changes versus base (working tree included), untracked files, and the
    ignored files under force_dirs that a following git add -f would add.
    """
    paths = _git_paths(repo, "diff", "--name-only", "--no-renames", "--no-color", "-z", base, "--")
    paths |= _git_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
    present = [folder for folder in force_dirs if (repo / folder).exists()]
    if present:
        paths |= _git_paths(
            repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *present
        )
    return paths


def _pathspecs(repo: Path, folders: Iterable[str], env: dict[str, str] | None = None) -> list[str]:
    """Return the folders that exist on disk or have index entries (git add needs a match)."""
    return [
        folder
        for folder in folders
        if (repo / folder).exists() or _git(repo, "ls-files", "-z", "--", folder, env=env).stdout
    ]


def _add(
    repo: Path,
    add_dirs: Iterable[str] | None,
    force_dirs: Iterable[str],
    env: dict[str, str] | None = None,
) -> None:
    """Run the gate's git add commands, in the index that env names (default: the real one).

    add_dirs None runs git add -A on the whole tree, otherwise git add -A -- add_dirs. Then
    git add -f -- force_dirs adds their ignored files. A folder that neither exists nor has
    index entries is skipped, because git refuses a pathspec that matches nothing.
    """
    if add_dirs is None:
        _git(repo, "add", "-A", env=env)
    else:
        present = _pathspecs(repo, add_dirs, env)
        if present:
            _git(repo, "add", "-A", "--", *present, env=env)
    forced = _pathspecs(repo, force_dirs, env)
    if forced:
        _git(repo, "add", "-f", "--", *forced, env=env)


def _stage(repo: Path, add_dirs: Iterable[str] | None, force_dirs: Iterable[str]) -> list[str]:
    """Stage changes with _add and return the staged paths (versus HEAD)."""
    _add(repo, add_dirs, force_dirs)
    return sorted(
        _git_paths(repo, "diff", "--cached", "--name-only", "--no-renames", "--no-color", "-z")
    )


def _valid_tree(repo: Path, force_dirs: Iterable[str]) -> str:
    """Return the tree that finish's own staging would make of the work tree as it is now.

    _add runs as in the commit step (git add -A, then git add -f -- force_dirs), but in a
    temporary copy of the index (GIT_INDEX_FILE; an empty index when there is none yet),
    and git write-tree writes the result. The real index and the work tree do not change,
    and the copy is removed afterwards. The copy keeps the index file's mtime: git compares
    it with the entries' stat data to spot racily clean entries, so a fresh mtime would
    let git skip a same-size edit made in the same second.
    """
    index = repo / _git(repo, "rev-parse", "--git-path", "index").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="v060-gate-index-") as folder:
        copy = Path(folder) / "index"
        if index.is_file():
            shutil.copy2(index, copy)
        env = {"GIT_INDEX_FILE": str(copy)}
        _add(repo, None, force_dirs, env)
        return _git(repo, "write-tree", env=env).stdout.strip()


def _commit_staged(repo: Path, message: str) -> str:
    """Commit the index with message as written; return the commit HEAD names afterwards.

    --cleanup=verbatim keeps git (commit.cleanup, core.commentChar) from editing a message
    that the gate compares or records. No hook runs: --no-verify skips pre-commit and
    commit-msg, and an empty core.hooksPath the rest (prepare-commit-msg, post-commit,
    reference-transaction).
    """
    with _no_hooks() as hooks:
        args = ("commit", "-q", "--no-verify", "--cleanup=verbatim", "-F", "-")
        _git(repo, *hooks, *args, stdin=message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _index_tree(repo: Path) -> str:
    """Snapshot the index as a tree object (git write-tree), index-only state included.

    A tree cannot hold intent-to-add entries or the assume-unchanged and skip-worktree
    flags; finish and post-resolve refuse to start while the index has any of them.
    """
    return _git(repo, "write-tree").stdout.strip()


def _blob_id(repo: Path, rel: str, data: bytes) -> str:
    """Return the object id git add gives data at rel (hash-object --path: rel's filters).

    The filters (core.autocrlf, clean filters) are the ones git add applied to the staged
    blob, so data matches it exactly when git add would store data there.
    """
    args = ("hash-object", "-t", "blob", "--stdin", f"--path={rel}")
    return _git(repo, *args, stdin=data).stdout.strip()


def _tree_blob(repo: Path, tree: str, rel: str) -> str | None:
    """Return the object id of rel in tree, or None when tree has no such path."""
    proc = _git(repo, "rev-parse", "--verify", "-q", f"{tree}:{rel}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _blob_problems(repo: Path, tree: str, expected: dict[str, bytes]) -> list[str]:
    """Return the paths whose blob in tree is missing or not what git add makes of the bytes."""
    return sorted(
        _safe(rel)
        for rel, data in expected.items()
        if _tree_blob(repo, tree, rel) != _blob_id(repo, rel, data)
    )


def _restore_index(repo: Path, tree: str) -> str | None:
    """Put the index back to a _index_tree snapshot; return git's error, or None."""
    proc = _git(repo, "read-tree", tree, check=False)
    return None if proc.returncode == 0 else _one_line(proc.stderr or proc.stdout)


def _update_refs(repo: Path, commands: list[str]) -> str | None:
    """Apply update-ref --stdin commands as one atomic transaction; return git's error.

    No reference-transaction hook runs (empty core.hooksPath).
    """
    with _no_hooks() as hooks:
        proc = _git(
            repo,
            *hooks,
            "update-ref",
            "--stdin",
            check=False,
            stdin="".join(f"{c}\n" for c in commands),
        )
    return None if proc.returncode == 0 else _one_line(proc.stderr or proc.stdout)


def _set_ref(ref: str, sha: str, old: str | None) -> str:
    """Return the update-ref command that moves ref from old to sha (old None: create)."""
    return f"create {ref} {sha}" if old is None else f"update {ref} {sha} {old}"


def _intent_to_add(repo: Path) -> list[str]:
    """Return the paths of the intent-to-add entries (git add -N) in the index.

    git ls-files shows such an entry like a staged empty file. git status --porcelain=v2,
    without rename detection (which would pair the entry with a deleted file), reports it
    as an ordinary entry with XY '.A', or 'DA' when HEAD has the path. Once its work-tree
    file is gone it is 'DD', or '.D', which is an intent-to-add entry only when HEAD
    lacks the path.
    """
    out = _git(
        repo,
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "-z",
        "--no-renames",
        "--untracked-files=no",
        "--ignore-submodules=all",
    ).stdout
    added: list[str] = []
    deleted: list[str] = []
    for record in out.split("\0"):
        fields = record.split(" ", 8)
        if len(fields) != 9 or fields[0] != "1":
            continue
        if fields[1] in (".A", "DA", "DD"):
            added.append(fields[8])
        elif fields[1] == ".D":
            deleted.append(fields[8])
    if deleted:
        proc = _git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD", check=False)
        tracked = set(proc.stdout.split("\0")) if proc.returncode == 0 else set()
        added += [path for path in deleted if path not in tracked]
    return sorted(added)


def _index_flag_problems(repo: Path) -> list[str]:
    """Return the index entries that would let a commit differ from what the gate checked.

    git add skips the work-tree file of an assume-unchanged or skip-worktree entry (git
    ls-files -v: a lowercase tag, or 'S'), and no rollback can restore an intent-to-add
    entry. Each problem names the path and the command that clears it; no flag waives
    these problems.
    """
    problems: list[str] = []
    for record in _git(repo, "ls-files", "-v", "-z").stdout.split("\0"):
        tag, _, path = record.partition(" ")
        if not path:
            continue
        quoted = _safe(shlex.quote(path))
        if tag.islower():
            problems.append(
                f"{_safe(path)} is marked assume-unchanged in the index (clear it: git "
                f"update-index --no-assume-unchanged -- {quoted}; no flag waives this)"
            )
        if tag.upper() == "S":
            problems.append(
                f"{_safe(path)} is marked skip-worktree in the index (clear it: git "
                f"update-index --no-skip-worktree -- {quoted}; no flag waives this)"
            )
    for path in _intent_to_add(repo):
        quoted = _safe(shlex.quote(path))
        problems.append(
            f"{_safe(path)} is an intent-to-add entry (git add -N) in the index (stage it: git "
            f"add -- {quoted}, or drop it: git rm --cached -- {quoted}; no flag waives this)"
        )
    return problems


def _graft_problems(repo: Path) -> list[str]:
    """Return a problem while a git grafts file exists: grafts rewrite the history git shows."""
    rel = _git(repo, "rev-parse", "--git-path", GRAFTS).stdout.strip()
    if not os.path.lexists(repo / rel):
        return []
    return [
        f"{_safe(rel)} exists; grafts change what git shows as history, so finish and "
        "post-resolve refuse to run while it exists (remove it; no flag waives this)"
    ]


def _replace_warning(repo: Path) -> str | None:
    """Return a warning when refs/replace/ has refs (git replace), else None (read-only).

    The gate ignores replacement objects (GIT_NO_REPLACE_OBJECTS=1), but git log and git
    show do not, so they may show content other than what the gate checked.
    """
    out = _git(repo, "for-each-ref", "--format=%(refname)", REPLACE_REFS).stdout
    names = [line for line in out.split("\n") if line]
    if not names:
        return None
    return (
        f"{REPLACE_REFS}/ has {len(names)} ref(s) (git replace -l lists them); the gate ignores "
        "replacement objects, so git log and git show may show content the gate did not check"
    )


# ---------------------------------------------------------------------------
# Backups and activeContext.md
# ---------------------------------------------------------------------------


def _ensure_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        handle, probe = tempfile.mkstemp(prefix=".v060-gate-probe-", dir=str(path))
        os.close(handle)
        os.unlink(probe)
    except OSError:
        return False
    return True


def backup_root(repo: Path) -> Path:
    """Return a writable backup root outside the work tree.

    That is $HACF_BACKUP_ROOT or the default. A root that resolves (symlinks followed)
    inside the work tree but outside the git directory, or one that is not writable,
    falls back to <git-common-dir>/hacf-backups, which git never stages.
    """
    configured = os.environ.get("HACF_BACKUP_ROOT", "").strip()
    primary = Path(os.path.expanduser(configured or DEFAULT_BACKUP_ROOT))
    common = _git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    git_dir = (repo / common).resolve()
    fallback = git_dir / "hacf-backups"
    if str(primary).startswith("~"):
        reason = f"{primary} cannot be expanded (no home directory)"
    else:
        resolved = primary.resolve()
        if resolved.is_relative_to(repo.resolve()) and not resolved.is_relative_to(git_dir):
            reason = f"{resolved} is inside the work tree, where git add would pick it up"
        elif _ensure_writable_dir(resolved):
            return resolved
        else:
            reason = f"{primary} is not writable"
    if _ensure_writable_dir(fallback):
        _warn(f"{reason}; backups go to {fallback}")
        return fallback
    raise GateError(f"no writable backup directory ({reason}; {fallback} is not writable)")


def _new_backup_dir(root: Path, prefix: str) -> Path:
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    for attempt in range(1000):
        path = root / (f"{prefix}-{stamp}" if attempt == 0 else f"{prefix}-{stamp}-{attempt}")
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise GateError(f"cannot create a unique {prefix} backup directory in {root}")


def _newest_v060_archive(repo: Path) -> str | None:
    """Return the name of the newest spec/archive/*_v0_6_0* folder (archive names sort by time)."""
    folder = repo / "spec/archive"
    if not folder.is_dir():
        return None
    names = sorted(path.name for path in folder.glob("*_v0_6_0*") if path.is_dir())
    return names[-1] if names else None


def _verify_sums(folder: Path) -> None:
    """Re-hash every file listed in folder/SHA256SUMS and check each tarball opens."""
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        target = folder / name
        if not name or not target.is_file() or _sha256_file(target) != digest:
            raise GateError(f"backup check failed: {target} does not match SHA256SUMS")
        try:
            with tarfile.open(target, "r:gz") as tar:
                empty = not tar.getnames()
        except tarfile.TarError as exc:
            raise GateError(f"backup check failed: {target}: {_one_line(exc)}") from exc
        if empty:
            raise GateError(f"backup check failed: {target} is empty")


def _post_resolve_backup(repo: Path, root: Path) -> Path:
    """Tar spec/compiled and the newest v0_6_0 archive folder, write and verify SHA256SUMS."""
    dest = _new_backup_dir(root, "post-resolve")
    items = [("spec-compiled.tar.gz", COMPILED_DIR)]
    newest = _newest_v060_archive(repo)
    if newest is not None:
        items.append((f"spec-archive-{newest}.tar.gz", f"spec/archive/{newest}"))
    for name, rel in items:
        with tarfile.open(dest / name, "w:gz") as tar:
            tar.add(str(repo / rel), arcname=rel)
    sums = "".join(f"{_sha256_file(dest / name)}  {name}\n" for name, _ in items)
    (dest / "SHA256SUMS").write_text(sums, encoding="utf-8")
    _verify_sums(dest)
    return dest


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _completed_region(text: str) -> tuple[int, int]:
    """Return the (start, end) offsets between the activeContext completed-list markers."""
    if text.count(COMPLETED_BEGIN) != 1 or text.count(COMPLETED_END) != 1:
        raise GateError(f"{ACTIVE} needs exactly one '{COMPLETED_BEGIN}' and one '{COMPLETED_END}'")
    start = text.index(COMPLETED_BEGIN) + len(COMPLETED_BEGIN)
    end = text.index(COMPLETED_END)
    if end < start:
        raise GateError(f"{ACTIVE}: the END marker comes before the BEGIN marker")
    return start, end


def _fold(text: str) -> str:
    """Return text without whitespace or FORBIDDEN_CATEGORIES characters, case-folded."""
    kept = (
        char
        for char in "".join(text.split())
        if unicodedata.category(char) not in FORBIDDEN_CATEGORIES
    )
    return "".join(kept).casefold()


def lookalike_problems(text: str) -> list[str]:
    """Return the lines of activeContext.md text that imitate its protected markers (RT-100).

    A line that folds (_fold: whitespace, case, control and format characters) to the
    protected heading without being it, or that holds more folded copies of a
    completed-list marker than exact ones, is a lookalike: a reader or an agent could
    take it for the real heading or marker, which the gate's exemption keys on.
    """
    problems: list[str] = []
    heading = _fold(PROCEDURE_HEADING)
    markers = [(marker, _fold(marker)) for marker in (COMPLETED_BEGIN, COMPLETED_END)]
    for number, line in enumerate(text.split("\n"), start=1):
        bare = line.rstrip("\r")
        folded = _fold(bare)
        if folded == heading and bare != PROCEDURE_HEADING:
            problems.append(
                f"{ACTIVE}:{number} imitates the protected heading '{PROCEDURE_HEADING}' "
                f"(it differs only in whitespace, case or invisible characters): "
                f"'{_safe(bare)}'; remove it (no flag waives this)"
            )
        for marker, folded_marker in markers:
            if folded.count(folded_marker) > bare.count(marker):
                problems.append(
                    f"{ACTIVE}:{number} imitates the completed-list marker '{marker}' (it "
                    f"differs only in whitespace, case or invisible characters): "
                    f"'{_safe(bare)}'; remove it (no flag waives this)"
                )
    return problems


def _check_active(repo: Path) -> list[str]:
    path = repo / ACTIVE
    try:
        text = _read_text_exact(path)
        _completed_region(text)
    except FileNotFoundError:
        return [f"{ACTIVE} is missing"]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read {ACTIVE}: {_one_line(exc)}"]
    except GateError as exc:
        return [str(exc)]
    return lookalike_problems(text)


def _procedure_region(data: bytes | None) -> bytes | None:
    """Return activeContext.md's protected region, or None when it is absent or malformed.

    The region runs from the PROCEDURE_HEADING line through the end of the COMPLETED_END
    line. The heading line and both markers must each occur exactly once, in that order.
    """
    if data is None:
        return None
    heading = PROCEDURE_HEADING.encode("utf-8")
    begin = COMPLETED_BEGIN.encode("utf-8")
    end = COMPLETED_END.encode("utf-8")
    starts: list[int] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        if line.rstrip(b"\r\n") == heading:
            starts.append(offset)
        offset += len(line)
    if len(starts) != 1 or data.count(begin) != 1 or data.count(end) != 1:
        return None
    start, end_at = starts[0], data.index(end)
    if not start < data.index(begin) < end_at:
        return None
    newline = data.find(b"\n", end_at)
    return data[start : len(data) if newline < 0 else newline + 1]


def _completed_entry(sid: str, nn: int, title: str, date: str) -> str:
    return f"- {sid} (NN {nn:02d}): {title} — finished {date}"


def _with_completed(text: str, sid: str, nn: int, title: str, date: str) -> str | None:
    """Return activeContext.md text with SID's completed line added before the END marker.

    Returns None when the completed list already has a line for SID.
    """
    start, end = _completed_region(text)
    for line in text[start:end].splitlines():
        words = line.strip().split()
        if len(words) >= 2 and words[0] == "-" and words[1] == sid:
            return None
    newline = "\r\n" if "\r\n" in text else "\n"
    entry = _completed_entry(sid, nn, title, date)
    line_start = text.rfind("\n", start, end) + 1
    if line_start == 0:
        return f"{text[:end]}{newline}{entry}{newline}{text[end:]}"
    return f"{text[:line_start]}{entry}{newline}{text[line_start:]}"


def _only_own_line(old_text: bytes, new_region: bytes, own: tuple[str, int, str]) -> bool:
    """Return True when new_region is old_text's region plus SID's own completed line.

    That is the state a finish killed during its commit leaves behind (any date), so a
    retry treats the procedure section as unchanged.
    """
    sid, nn, title = own
    try:
        text = old_text.decode("utf-8")
    except UnicodeDecodeError:
        return False
    pattern = re.compile(re.escape(_completed_entry(sid, nn, title, "")) + DATE_RE)
    region = new_region.decode("utf-8", errors="replace")
    for line in region.split("\n"):
        if not pattern.fullmatch(line.rstrip("\r")):
            continue
        date = line.rstrip("\r")[-10:]
        try:
            simulated = _with_completed(text, sid, nn, title, date)
        except GateError:
            return False
        if simulated is not None and _procedure_region(simulated.encode("utf-8")) == new_region:
            return True
    return False


def _procedure_change(
    repo: Path, base: str, own: tuple[str, int, str] | None = None
) -> tuple[str | None, bool]:
    """Return (how the protected procedure section differs from base, own line found).

    The reason is None when the section is unchanged. own is (SID, nn, title): a section
    that differs only by that slice's own completed line counts as unchanged, and the
    second value is then True.
    """
    old_text = _blob(repo, base, ACTIVE)
    old = _procedure_region(old_text)
    if old_text is None or old is None:
        return f"the scope base {base[:12]} has no well-formed procedure section", False
    new = _procedure_region(_read_bytes(repo / ACTIVE))
    if new is None:
        return (
            "its heading or a completed-list marker is missing, duplicated or out of order",
            False,
        )
    if new == old:
        return None, False
    if own is not None and _only_own_line(old_text, new, own):
        return None, True
    return f"it differs from the scope base {base[:12]}", False


def append_completed(
    repo: Path, sid: str, nn: int, title: str, text: str
) -> tuple[str | None, str]:
    """Write text (activeContext.md as read) back with '- SID (NN nn): title — finished DATE'.

    Nothing is written when the completed list already has a line for SID. Returns (the
    added line, or None when the SID was already listed; the text activeContext.md holds).
    """
    date = _utc_now().strftime("%Y-%m-%d")
    updated = _with_completed(text, sid, nn, title, date)
    if updated is None:
        return None, text
    with (repo / ACTIVE).open("w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
    return _completed_entry(sid, nn, title, date), updated


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _compiled_miniprds(repo: Path) -> tuple[list[tuple[int, str]], list[str]]:
    """Return ((nn, name) sorted by NN for well-named MiniPRDs, names that do not match)."""
    folder = repo / COMPILED_DIR
    if not folder.is_dir():
        return [], []
    named: list[tuple[int, str]] = []
    unnamed: list[str] = []
    for path in sorted(folder.glob("MiniPRD_*.md")):
        match = MINIPRD_NAME_RE.fullmatch(path.name)
        if match:
            named.append((int(match.group(1)), path.name))
        else:
            unnamed.append(path.name)
    return sorted(named), unnamed


_Blocks = dict[str, tuple[str, dict[str, Any], str]]  # SID -> (MiniPRD label, block, text)


def _check_blocks(
    repo: Path, named: list[tuple[int, str]]
) -> tuple[list[str], dict[str, bytes], _Blocks]:
    """Check each MiniPRD's Overrides banner and its block's nn, SID, depends_on and refs.

    The block checks are SID uniqueness, depends_on order, annex refs and lore refs.
    Returns (problems, {path: bytes} of every file the checks parsed: the MiniPRDs and the
    files the annex refs point to, {SID: (label, block, text)} for the first MiniPRD that
    declares each SID with a valid block).
    """
    problems: list[str] = []
    read: dict[str, bytes] = {}
    parsed: list[tuple[str, dict[str, Any], str]] = []
    for nn, name in named:
        label = f"{COMPILED_DIR}/{name}"
        try:
            data = (repo / label).read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{label}: cannot read: {_one_line(exc)}")
            continue
        read[label] = data
        banner = banner_problem(text)
        if banner:
            problems.append(f"{label}: {banner}")
        block, errors = parse_block_text(text, label)
        problems += errors
        if block is None:
            continue
        if block["nn"] != nn:
            problems.append(f"{label}: block nn {block['nn']} does not match filename NN {nn:02d}")
        parsed.append((label, block, text))
    owner: _Blocks = {}
    sid_nn: dict[str, int] = {}
    for label, block, text in parsed:
        sid = block["slice"]
        if sid in owner:
            problems.append(f"{label}: slice {sid} is already declared by {owner[sid][0]}")
        else:
            owner[sid] = (label, block, text)
            sid_nn[sid] = block["nn"]
    cache: dict[str, tuple[str, set[str]]] = {}
    for label, block, _ in parsed:
        for dep in block["depends_on"]:
            if dep not in sid_nn:
                problems.append(f"{label}: depends_on {dep} is not declared by any MiniPRD")
            elif sid_nn[dep] >= block["nn"]:
                problems.append(
                    f"{label}: depends_on {dep} has nn {sid_nn[dep]}, which is not lower than "
                    f"{block['slice']}'s nn {block['nn']}"
                )
        for ref in block["annex_refs"]:
            reason = resolve_annex_ref(repo, ref, cache, read)
            if reason:
                problems.append(f"{label}: annex_ref {ref} does not resolve: {reason}")
        for ref in block["lore_refs"]:
            reason = resolve_lore_ref(repo, ref)
            if reason:
                problems.append(f"{label}: lore_ref {ref} does not resolve: {reason}")
    return problems, read, owner


def _post_resolve_allows(path: str) -> bool:
    """Return True for a path post-resolve may commit: POST_RESOLVE_DIRS minus guards.json."""
    return path != GUARDS and any(path.startswith(f"{folder}/") for folder in POST_RESOLVE_DIRS)


def _post_resolve_paths(repo: Path) -> list[str]:
    """Return the problems of the paths post-resolve would commit (checked before any write).

    Every path that differs from HEAD (working tree, index, untracked files and the
    ignored files git add -f would add) must be under POST_RESOLVE_DIRS, guards.json must
    equal HEAD's copy in content and mode, and something must differ (or there is nothing
    to commit). Each changed path that still exists, each compiled MiniPRD and the
    SuperPRD must be a regular file reached without symlinks (the ledger is checked with
    activeContext.md and guards.json).
    """
    if _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
        return ["the repository has no commit yet"]
    changed = changed_paths(repo, "HEAD", POST_RESOLVE_ADD_DIRS)
    changed |= _git_paths(
        repo, "diff", "--cached", "--name-only", "--no-renames", "--no-color", "-z", "HEAD", "--"
    )
    problems: list[str] = []
    stray = sorted(path for path in changed if path != GUARDS and not _post_resolve_allows(path))
    for path in stray:
        if path.startswith("spec/process/"):
            problems.append(f"{path}: spec/process/** is never committed (policy)")
        else:
            problems.append(f"{path}: outside the post-resolve paths")
    if stray:
        allowed = ", ".join(f"{folder}/**" for folder in POST_RESOLVE_DIRS)
        problems.append(
            f"post-resolve commits only {allowed} (never {GUARDS}); commit, stash or remove "
            "the stray files above first (move spec/process/** files out of the repository)"
        )
    head_copy = _blob(repo, "HEAD", GUARDS)
    if head_copy is None:
        problems.append(f"{GUARDS} is not committed at HEAD; commit it before post-resolve")
    elif GUARDS in changed or _read_bytes(repo / GUARDS) != head_copy:
        problems.append(
            f"{GUARDS} differs from HEAD's copy (content or mode); post-resolve never commits it"
        )
    allowed = {path for path in changed if _post_resolve_allows(path)}
    if not allowed:
        problems.append(
            f"nothing to commit: no path under {', '.join(POST_RESOLVE_DIRS)} differs from HEAD"
        )
    compiled = {
        f"{COMPILED_DIR}/{path.name}" for path in (repo / COMPILED_DIR).glob("MiniPRD_*.md")
    }
    for path in sorted((allowed | compiled | {SUPERPRD_BARE, SUPERPRD_V060}) - {LEDGER}):
        problem = _plain_file_problem(repo, path)
        if problem:
            problems.append(f"{_safe(path)} {problem}; post-resolve commits only regular files")
    return problems


def _repin_problems(state: _History, repin: str | None) -> list[str]:
    """Return why post-resolve may not run (again) with this --repin REASON (or None).

    post-resolve runs once. A second run needs --repin, and is refused anyway when an
    earlier post-resolve commit other than the first lacks an HACF-Repin trailer, or when
    a post-resolve-subject commit is not shaped like one post-resolve makes, because
    finish would then refuse the new pin too.
    """
    earlier = [c for c in state.commits if c.subject.startswith(POST_RESOLVE_PREFIX)]
    previous = state.refs.get(POST_RESOLVE_REF)
    if not earlier and previous is None:
        return []
    if repin is None:
        where = _commit_label(earlier[0]) if earlier else f"{POST_RESOLVE_REF} {previous}"
        return [
            f"post-resolve already ran ({where}); it pins the blocks once. Re-pin only with "
            f"{REPIN_FLAG} REASON after the user approves it",
            *state.reshaped,
        ]
    unpinned = [
        f"{_commit_label(commit)} has the post-resolve subject but no {_key(REPIN_TRAILER)} "
        f"trailer, so finish would refuse any later pin; remove it from {GATE_BRANCH} first"
        for commit in earlier[:-1]
        if not _has_repin(commit)
    ]
    return [*state.reshaped, *unpinned]


def _finished_block_problems(state: _History, blocks: _Blocks) -> list[str]:
    """Return why a re-pin would change or drop the block of a SID with a slice ref (R14).

    Every SID that has a slice ref (counted or not) and a block in the current pin must
    still be declared by a spec/compiled MiniPRD whose block equals the pinned one. The
    audit moved a finished slice's MiniPRD to spec/archive/, so the problem names the
    command that restores the pinned copy. No flag waives these problems.
    """
    pins = _unique_pins(state.pinned)
    problems: list[str] = []
    for name in sorted(state.refs):
        sid = name[len(SLICE_REF_PREFIX) :]
        if not name.startswith(SLICE_REF_PREFIX) or sid not in pins or state.pin_sha is None:
            continue
        rel, pinned = pins[sid]
        where = f"the post-resolve commit {state.pin_sha[:12]}"
        if sid not in blocks:
            problems.append(
                f"{sid} has a slice ref ({name}), but no {COMPILED_DIR} MiniPRD declares it; a "
                f"re-pin keeps the block of every slice with a slice ref (restore it: git show "
                f"{state.pin_sha[:12]}:{rel} > {rel}; no flag waives this)"
            )
            continue
        label, block, _ = blocks[sid]
        changed = [key for key in BLOCK_KEYS if pinned[key] != block[key]]
        if changed:
            problems.append(
                f"{label}: {sid} has a slice ref ({name}), but its {BLOCK_INFO} block changes "
                f"key(s) {', '.join(changed)} versus {where}; a re-pin may change only the "
                "blocks of slices without a slice ref (no flag waives this)"
            )
    return problems


def _restored_index(repo: Path, snapshot: str) -> str:
    """Restore the index to snapshot (git read-tree); return how that went, for a message."""
    error = _restore_index(repo, snapshot)
    if error is None:
        return "the index was restored"
    return f"git read-tree {snapshot} failed ({error}), so the index may hold staged paths"


def _head_is(repo: Path, sha: str, parent: str) -> bool:
    """Return True when HEAD is sha on GATE_BRANCH and HEAD^ is parent (read-only)."""
    now = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip()
    before = _git(repo, "rev-parse", "--verify", "-q", "HEAD^", check=False).stdout.strip()
    return now == sha and before == parent and _current_branch(repo) == GATE_BRANCH


def _undo_advice(repo: Path, sha: str, head: str, command: str) -> str:
    """Return how to undo commit sha, which command made on top of head.

    git reset --soft HEAD^ is advised only while HEAD is sha on GATE_BRANCH and HEAD^ is
    head. Otherwise HEAD is not the gate's commit (a hook moved it), so the advice gives
    both shas and points to git reflog instead.
    """
    if _head_is(repo, sha, head):
        return (
            f"inspect it (git show --stat {sha[:12]}); to undo it, run git reset --soft HEAD^ "
            f"(the changes stay staged), then run {command} again"
        )
    now = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip()
    if now == head:
        return (
            f"HEAD is back at {head[:12]}, where {command} started, so do not reset it; the "
            f"gate's commit is only in git reflog: inspect it there, then run {command} again"
        )
    return (
        f"HEAD is {now[:12] or 'unborn'}, which is not the gate's commit on {GATE_BRANCH} on "
        f"top of {head[:12]} (where {command} started), so do not reset it; find the gate's "
        f"commit with git reflog and inspect it, then move {GATE_BRANCH} back to {head[:12]} "
        f"yourself before you run {command} again"
    )


def cmd_post_resolve(repo: Path, expect: int, repin: str | None = None) -> int:
    """Validate the /hyper-resolve output, then rename the SuperPRD, back up and commit.

    Only spec/compiled, spec/archive, spec/active and dev/design/v0.6.0 are staged; any
    other changed path, a guards.json change, a symlink or other non-regular file, an
    index flag or a grafts file refuses the run before anything is written, and so does
    having nothing to commit. It runs once: an earlier post-resolve commit or
    POST_RESOLVE_REF refuses the run unless repin (the --repin REASON) is given, which
    becomes an HACF-Repin trailer. After staging, every file the checks parsed must be
    staged byte-for-byte; after committing, the commit's tree must be the staged tree.
    The commit is then recorded in POST_RESOLVE_REF (compare-and-swap).
    """
    tag = "post-resolve"
    _require_branch(repo)
    replaced = _replace_warning(repo)
    if replaced:
        _warn(f"{tag}: {replaced}")
    problems: list[str] = []
    named, unnamed = _compiled_miniprds(repo)
    problems += [f"{COMPILED_DIR}/{name}: not named MiniPRD_NN_<Slug>.md" for name in unnamed]
    total = len(named) + len(unnamed)
    if total != expect:
        problems.append(f"expected {expect} MiniPRD_*.md files in {COMPILED_DIR}, found {total}")
    by_nn: dict[int, list[str]] = {}
    for nn, name in named:
        by_nn.setdefault(nn, []).append(name)
    for nn, group in sorted(by_nn.items()):
        if len(group) > 1:
            problems.append(f"NN {nn:02d} is used by {len(group)} files: {', '.join(group)}")
    missing = sorted(set(range(expect)) - set(by_nn))
    if missing:
        problems.append("missing NN: " + ", ".join(f"{nn:02d}" for nn in missing))
    stray = sorted(set(by_nn) - set(range(expect)))
    if stray:
        listed = ", ".join(f"{nn:02d}" for nn in stray)
        problems.append(f"NN outside 00-{expect - 1:02d}: {listed}")
    block_problems, parsed, blocks = _check_blocks(repo, named)
    problems += block_problems
    problems += _seed_problems(repo, blocks, parsed)
    bare, v060 = repo / SUPERPRD_BARE, repo / SUPERPRD_V060
    if bare.exists() and v060.exists():
        problems.append(f"refusing to rename: both {SUPERPRD_BARE} and {SUPERPRD_V060} exist")
    elif not bare.exists() and not v060.exists():
        problems.append(f"{SUPERPRD_V060} does not exist and there is no {SUPERPRD_BARE}")
    problems += [
        f"guard {_safe(guard.path)}: {guard.state}: {guard.detail}"
        for guard in _guard_states(repo)
        if guard.state != "ok"
    ]
    problems += _index_flag_problems(repo)
    problems += _graft_problems(repo)
    problems += _file_type_problems(repo)
    problems += _post_resolve_paths(repo)
    state = _history(repo)
    problems += _repin_problems(state, repin)
    if repin is not None:
        problems += _finished_block_problems(state, blocks)
    if problems:
        _report(f"{tag}: refused; nothing was written or committed", problems + state.recovery)
        return 1
    previous = state.refs.get(POST_RESOLVE_REF)
    rerun = previous is not None or any(
        commit.subject.startswith(POST_RESOLVE_PREFIX) for commit in state.commits
    )
    if repin is not None and not rerun:
        _info(f"{tag}: nothing to re-pin; no {_key(REPIN_TRAILER)} trailer is added")
        repin = None
    _info(f"{tag}: {expect} MiniPRDs (NN 00-{expect - 1:02d}) and their blocks are valid")
    _info(f"{tag}: every MiniPRD has the Overrides banner and covers its seed in {SLICES}")
    _info(f"{tag}: annex refs, lore refs and guards.json are ok")
    _info(f"{tag}: every changed path is under {', '.join(POST_RESOLVE_DIRS)}")
    root = backup_root(repo)
    start = _git(repo, "rev-parse", "HEAD").stdout.strip()
    snapshot = _index_tree(repo)
    if bare.exists():
        os.rename(bare, v060)
        _info(f"{tag}: renamed {SUPERPRD_BARE} -> {SUPERPRD_V060}")
    if not v060.is_file():
        raise GateError(f"{SUPERPRD_V060} is missing")
    backup = _post_resolve_backup(repo, root)
    _info(f"{tag}: backup {backup} (SHA256SUMS verified)")
    staged = _stage(repo, POST_RESOLVE_DIRS, POST_RESOLVE_ADD_DIRS)
    outside = [path for path in staged if not _post_resolve_allows(path)]
    if outside:
        raise GateError(
            f"staged path(s) that post-resolve never commits: {', '.join(outside)}; "
            f"{_restored_index(repo, snapshot)} and nothing was committed"
        )
    if not staged:
        raise GateError("nothing staged; no commit was made")
    tree = _index_tree(repo)
    # The files the checks parsed and that post-resolve stages must be staged as parsed.
    differs = _blob_problems(
        repo, tree, {rel: data for rel, data in parsed.items() if _post_resolve_allows(rel)}
    )
    if differs:
        raise GateError(
            f"staged content differs from what post-resolve checked: {', '.join(differs)} "
            f"(changed while post-resolve ran?); {_restored_index(repo, snapshot)} and nothing "
            "was committed"
        )
    message = (
        f"{POST_RESOLVE_PREFIX} SuperPRD_v0_6_0 + {expect} MiniPRDs (post-resolve)\n\n"
        f"Checked by dev/scripts/v060_gate.py post-resolve: MiniPRD names and NN 00-"
        f"{expect - 1:02d},\none {BLOCK_INFO} block per MiniPRD, depends_on order, annex and "
        f"lore references,\nand {GUARDS}. Backup outside git: {backup.name}.\n"
    )
    if repin is not None:
        message += f"\n{REPIN_TRAILER}{repin}\n"
    sha = _commit_staged(repo, message)
    committed = _git(repo, "rev-parse", f"{sha}^{{tree}}").stdout.strip()
    if committed != tree:
        raise GateError(
            f"committed {sha}, but its tree {committed[:12]} is not the tree post-resolve "
            f"staged ({tree[:12]}; another process?), so {POST_RESOLVE_REF} was not recorded; "
            + _undo_advice(repo, sha, start, tag)
        )
    error = _update_refs(repo, [_set_ref(POST_RESOLVE_REF, sha, previous)])
    if error:
        raise GateError(
            f"committed {sha}, but could not record it in {POST_RESOLVE_REF} ({error}); "
            "finish refuses until that ref names the newest post-resolve commit. Find out "
            f"what changed the ref, then run: git update-ref {POST_RESOLVE_REF} {sha}"
        )
    _info(f"{tag}: committed {sha} ({len(staged)} paths); recorded in {POST_RESOLVE_REF}")
    if repin is not None:
        _info(f"{tag}: waiver applied: {REPIN_TRAILER}{repin}")
    return 0


def _reason(flag: str, value: str | None) -> str | None:
    """Return a waiver REASON with whitespace collapsed, or a usage error.

    After collapsing, a REASON must be non-empty, have a letter or digit, and have no
    control, format or separator character.
    """
    if value is None:
        return None
    reason = " ".join(value.split())
    if not reason:
        raise UsageError(f"{flag} needs a non-empty REASON")
    problem = _text_problem(reason)
    if problem:
        raise UsageError(f"{flag} REASON {problem}: '{_safe(reason)}'")
    return reason


def _locate(repo: Path, arg: str) -> Path:
    """Return --miniprd as found (symlinks kept), tried relative to the repo, then the cwd."""
    given = Path(arg).expanduser()
    candidates = [given] if given.is_absolute() else [repo / given, Path.cwd() / given]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.absolute()
    raise UsageError(f"--miniprd {arg}: no such file (tried relative to {repo} and the cwd)")


def _miniprd_location(repo: Path, path: Path, arg: str) -> tuple[str, list[str]]:
    """Return (repo-relative path, problems); --miniprd must resolve under spec/archive/.

    It must also be a regular file: not a symlink, and inside the repository not reached
    through a symlinked directory.
    """
    rel = _repo_rel(repo, path)
    given = os.path.relpath(path, repo)
    if given == os.pardir or given.startswith(f"{os.pardir}{os.sep}"):
        kind = "is a symlink" if path.is_symlink() else None
    else:
        kind = _plain_file_problem(repo, given)
    typed = [f"--miniprd {arg} {kind}; finish reads only a regular file"] if kind else []
    if rel is None:
        resolved = path.resolve()
        return str(resolved), [
            f"--miniprd {arg} resolves to {resolved}, outside the repository",
            *typed,
        ]
    if typed:
        return rel, typed
    if rel.startswith(f"{COMPILED_DIR}/"):
        return rel, [
            f"--miniprd {rel} is the un-audited compiled copy; finish takes the audited copy "
            f"that /hyper-audit moves to {ARCHIVE_DIR}/"
        ]
    if not rel.startswith(f"{ARCHIVE_DIR}/"):
        return rel, [f"--miniprd {rel} is not under {ARCHIVE_DIR}/"]
    return rel, []


def _miniprd_name_problems(rel: str, nn: int, final: bool) -> list[str]:
    """Check the --miniprd basename: MiniPRD_<nn>_<Slug>_AUDITED.md (final slice: or bare)."""
    name = rel.rsplit("/", 1)[-1]
    audited = AUDITED_NAME_RE.fullmatch(name)
    plain = MINIPRD_NAME_RE.fullmatch(name)
    if audited and int(audited.group(1)) == nn:
        return []
    if final and plain and int(plain.group(1)) == nn:
        return []
    wanted = f"MiniPRD_{nn:02d}_<Slug>_AUDITED.md"
    if final:
        wanted += f" or, for the final slice, MiniPRD_{nn:02d}_<Slug>.md"
    return [f"--miniprd {rel}: the name must be {wanted} (the block's nn is {nn})"]


def _compiled_at(repo: Path, sha: str) -> list[tuple[str, int]]:
    """Return (path, NN) for each MiniPRD_NN_<Slug>.md file in spec/compiled/ at commit sha."""
    out = _git(repo, "ls-tree", "-z", sha, "--", f"{COMPILED_DIR}/").stdout
    found: list[tuple[str, int]] = []
    for record in out.split("\0"):
        meta, _, path = record.partition("\t")
        match = MINIPRD_NAME_RE.fullmatch(path.rsplit("/", 1)[-1])
        if meta.split()[1:2] == ["blob"] and match:
            found.append((path, int(match.group(1))))
    return found


def _pinned_block(state: _History, sid: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Return the block that declares slice sid among the post-resolve commit's MiniPRDs.

    Exactly one spec/compiled MiniPRD block of the post-resolve commit must declare sid;
    the working block's nn plays no part in the lookup.
    """
    found = state.pinned.get(sid, [])
    if len(found) == 1:
        return found[0][1], []
    return None, [
        f"the post-resolve commit {(state.pin_sha or '')[:12]} has {len(found)} {COMPILED_DIR} "
        f"MiniPRD(s) whose block declares slice {sid}; expected exactly one",
        *state.unreadable,
    ]


class _FinishBlock(NamedTuple):
    block: dict[str, Any] | None  # the working block; None on a refusal
    pinned: dict[str, Any] | None  # the SID's block in the post-resolve commit
    problems: list[str]
    changed: list[str]  # the keys in which block differs from pinned


def _finish_block(
    repo: Path,
    path: Path,
    arg: str,
    sid: str,
    state: _History,
    accept: str | None,
    tag: str,
) -> _FinishBlock:
    """Return the working and the pinned block of --miniprd; block is None on a refusal.

    The path must resolve under spec/archive/ with the audited name for the block's nn,
    no spec/compiled/MiniPRD_<nn>_*.md may remain (the audit moves the file), and the
    block must equal the SID's block pinned in the post-resolve commit (state.post)
    unless accept (the --accept-block-change REASON) is given; the working block is used
    either way. No flag accepts a change to slice or nn.
    """
    post = state.post
    if post is None:
        raise GateError("internal error: _finish_block needs the post-resolve commit")
    rel, problems = _miniprd_location(repo, path, arg)
    if problems:
        return _FinishBlock(None, None, problems, [])
    block, problems = parse_block(path, rel)
    if block is None:
        return _FinishBlock(None, None, problems, [])
    if block["slice"] != sid:
        return _FinishBlock(
            None, None, [f"{rel}: the block declares slice {block['slice']}, not {sid}"], []
        )
    compiled = _compiled_at(repo, post.sha)
    pinned, problems = _pinned_block(state, sid)
    if pinned is None:
        return _FinishBlock(None, None, problems, [])
    changed = [key for key in BLOCK_KEYS if pinned[key] != block[key]]
    where = f"the post-resolve commit {post.sha[:12]}"
    identity = [
        f"{key} {pinned[key]!r} -> {block[key]!r}" for key in IDENTITY_KEYS if key in changed
    ]
    if identity:
        problem = (
            f"{rel}: the {BLOCK_INFO} block changes {', '.join(identity)} versus {where}; "
            "no flag waives a change to slice or nn"
        )
        return _FinishBlock(None, pinned, [problem], changed)
    nn = block["nn"]
    final = nn == max((number for _, number in compiled), default=-1)
    problems = _miniprd_name_problems(rel, nn, final)
    if problems:
        return _FinishBlock(None, pinned, problems, [])
    leftover = sorted(copy.name for copy in (repo / COMPILED_DIR).glob(f"MiniPRD_{nn:02d}_*.md"))
    if leftover:
        problems = [
            f"{COMPILED_DIR}/{name} still exists; /hyper-audit moves the MiniPRD to "
            f"{ARCHIVE_DIR}/ (move it there, do not copy it)"
            for name in leftover
        ]
        return _FinishBlock(None, pinned, problems, [])
    keys = ", ".join(changed)
    if changed and accept is None:
        problem = (
            f"{rel}: the {BLOCK_INFO} block differs from the block pinned in {where} "
            f"(key(s): {keys}); restore it, or record a change the user approved with "
            "--accept-block-change REASON"
        )
        return _FinishBlock(None, pinned, [problem], changed)
    if changed:
        _info(f"{tag}: accepting a {BLOCK_INFO} block that differs from {where} ({keys}): {accept}")
    else:
        _info(f"{tag}: {BLOCK_INFO} block ok ({rel}, NN {nn:02d}, equal to {where})")
        if accept:
            _info(f"{tag}: nothing to accept; no {_key(BLOCK_TRAILER)} trailer is added")
    return _FinishBlock(block, pinned, [], changed)


def scope_check(
    repo: Path, base: _Commit, block: dict[str, Any], force_dirs: tuple[str, ...]
) -> tuple[set[str], list[str], str | None]:
    """Return (changed paths, out-of-scope paths, note) since the scope base commit.

    A change to activeContext.md's protected procedure section is reported as the
    out-of-scope path PROCEDURE_SCOPE, unless a files entry names activeContext.md
    itself; then the note says that the section changed. A section that differs only by
    this slice's own completed-list line (left by a finish killed during its commit)
    counts as unchanged, with a note.
    """
    changed = changed_paths(repo, base.sha, force_dirs)
    outside = out_of_scope(changed, block["files"], block["nn"])
    note: str | None = None
    if ACTIVE in changed:
        own = (block["slice"], block["nn"], block["title"].strip())
        reason, own_line = _procedure_change(repo, base.sha, own)
        if reason and _names_path(block["files"], ACTIVE):
            note = f"{PROCEDURE_SCOPE} changed ({reason}); allowed because files lists {ACTIVE}"
        elif reason:
            outside.append(PROCEDURE_SCOPE)
        elif own_line:
            note = (
                f"{ACTIVE} already lists {block['slice']} (left by an interrupted finish); "
                "otherwise the procedure section is unchanged"
            )
    return changed, sorted(outside), note


def _check_script_problem(repo: Path, base: _Commit) -> str | None:
    """Return why dev/scripts/check.sh cannot run as finish requires, or None.

    Once the scope base has the script, finish never skips it; it must be a regular file.
    """
    problem = _plain_file_problem(repo, CHECK_SCRIPT)
    if problem:
        return f"{CHECK_SCRIPT} {problem}; finish runs only a regular file"
    if not (repo / CHECK_SCRIPT).exists() and _blob(repo, base.sha, CHECK_SCRIPT) is not None:
        return (
            f"{CHECK_SCRIPT} exists at the scope base {base.sha[:12]} but not in the working "
            f"tree; finish never skips it once it exists (restore it: git checkout "
            f"{base.sha[:12]} -- {CHECK_SCRIPT})"
        )
    return None


class _Fingerprint(NamedTuple):
    head: str
    branch: str  # HEAD's full symbolic ref, or '(detached)'
    index: str  # sha256 of the index entries and their flags
    paths: dict[str, _PathState]


def _fingerprint(repo: Path, paths: Iterable[str]) -> _Fingerprint:
    """Record HEAD, its symbolic ref, the index and each path's lstat state.

    The index part covers git ls-files -s -v (the entries with their assume-unchanged and
    skip-worktree tags) and the intent-to-add entries, which git ls-files cannot show.
    """
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip()
    branch = _git(repo, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    entries = _git_bytes(repo, "ls-files", "-s", "-v", "-z") or b""
    added = "\0".join(_intent_to_add(repo)).encode("utf-8")
    index = hashlib.sha256(entries + b"\0\0" + added).hexdigest()
    states = {rel: _path_state(repo / rel) for rel in sorted(set(paths))}
    return _Fingerprint(head, branch or "(detached)", index, states)


def _drift(before: _Fingerprint, after: _Fingerprint) -> list[str]:
    """List what changed between two fingerprints of the same paths."""
    items: list[str] = []
    if before.head != after.head:
        items.append(f"HEAD moved ({before.head[:12]} -> {after.head[:12]})")
    if before.branch != after.branch:
        items.append(f"branch changed ({before.branch} -> {after.branch})")
    if before.index != after.index:
        items.append(
            "index changed (an entry, or its assume-unchanged, skip-worktree or intent-to-add flag)"
        )
    for rel, old in before.paths.items():
        change = _state_change(old, after.paths[rel])
        if change:
            items.append(f"{_safe(rel)}: {change}")
    return items


def _record_path(path: str) -> str:
    """Return path as a commit-body item.

    It is JSON-quoted when it has a control, format or separator character, or leading or
    trailing whitespace, so the record shows what the path really is.
    """
    if path != path.strip() or any(
        unicodedata.category(char) in FORBIDDEN_CATEGORIES for char in path
    ):
        return json.dumps(path)
    return path


def _slice_message(
    sid: str,
    title: str,
    applied: dict[str, str],
    records: list[tuple[str, list[str]]],
    tool: str | None,
) -> str:
    """Return a slice commit message.

    The paragraphs are the subject, one (heading, '- item' lines) paragraph per waiver
    record, the claude --version line when there is one, then the trailer paragraph.
    """
    paragraphs = [f"v0.6.0 {sid}: {title}"]
    paragraphs += [
        "\n".join([heading, *(f"- {item}" for item in items)]) for heading, items in records
    ]
    if tool:
        paragraphs.append(f"{TOOL_VERSION_LABEL}{tool}")
    trailers = [f"{SLICE_TRAILER}{sid}"]
    trailers += [
        f"{trailer}{applied[trailer]}" for trailer in WAIVER_TRAILERS if trailer in applied
    ]
    paragraphs.append("\n".join(trailers))
    return "\n\n".join(paragraphs) + "\n"


def _tool_version(tag: str) -> str | None:
    """Return the first line of `claude --version` for the commit body (RT-09), or None.

    None when claude is not on PATH; a failing or silent command is a warning. Control,
    format and separator characters are escaped and the line is cut at 200 characters.
    """
    claude = shutil.which("claude")
    if claude is None:
        return None
    try:
        proc = subprocess.run(
            [claude, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _warn(f"{tag}: claude --version failed ({_one_line(exc)}); no tool version recorded")
        return None
    lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
    first = _safe(_one_line(lines[0]))[:200] if lines else ""
    if proc.returncode != 0 or not first:
        _warn(f"{tag}: claude --version exited {proc.returncode}; no tool version recorded")
        return None
    return first


def _ignored_python(repo: Path) -> list[str]:
    """Return the ignored *.py files under TESTED_SOURCE_DIRS (outside __pycache__/) (RT-97).

    pytest may import such a file while check.sh runs, but finish never commits it, so
    the tested bytes would differ from the committed ones. No flag waives these problems.
    """
    listing = ("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--")
    found = _git_paths(repo, *listing, *TESTED_SOURCE_DIRS)
    return [
        f"{_safe(path)} is an ignored Python file that check.sh may import but finish never "
        "commits (remove it, or stop ignoring it; no flag waives this)"
        for path in sorted(found)
        if path.endswith(".py") and "__pycache__" not in path.split("/")
    ]


def _committed_problems(repo: Path, head: str, tree: str) -> list[str]:
    """Return the entry-mode and byte problems of the paths tree changes versus head.

    A committed path may not be a symlink (120000) or a gitlink (160000) entry (RT-98),
    and the bytes on disk of every committed file, hashed without filters (git
    hash-object --no-filters), must equal its blob in tree (RT-97): a clean filter or
    core.autocrlf would otherwise commit bytes other than those check.sh tested. No flag
    waives these problems.
    """
    listing = ("diff-tree", "-r", "-z", "--name-only", "--no-renames", head, tree)
    committed = _git_paths(repo, *listing)
    problems: list[str] = []
    for record in _git(repo, "ls-tree", "-r", "-z", tree).stdout.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if path not in committed or len(fields) != 3:
            continue
        mode, _, oid = fields
        name = _safe(path)
        if mode == SYMLINK_MODE:
            problems.append(f"{name} is a symlink (mode {mode}); finish commits only regular files")
        elif mode == GITLINK_MODE:
            problems.append(
                f"{name} is a gitlink (mode {mode}, a nested repository); finish commits only "
                "regular files"
            )
        else:
            proc = _git(repo, "hash-object", "--no-filters", "--", path, check=False)
            if proc.returncode != 0 or proc.stdout.strip() != oid:
                problems.append(
                    f"{name}: its bytes on disk (git hash-object --no-filters) differ from the "
                    "blob git add stages (a clean filter or core.autocrlf?), so check.sh would "
                    "test other bytes than finish commits"
                )
    return [f"{problem} (no flag waives this)" for problem in problems]


def _bundle(repo: Path, sid: str, tag: str) -> None:
    """Write <backup root>/hacf-v060-<SID>.bundle with every branch and refs/hacf/* (RT-58).

    The slice is finished either way, so a failure is only a warning.
    """
    try:
        path = backup_root(repo) / f"hacf-v060-{sid}.bundle"
        proc = _git(
            repo, "bundle", "create", str(path), "--branches", "--glob=refs/hacf", check=False
        )
    except (GateError, OSError) as exc:
        _warn(f"{tag}: no bundle was written ({_one_line(exc)}); {sid} is finished")
        return
    if proc.returncode != 0:
        detail = _one_line(proc.stderr or proc.stdout)
        _warn(f"{tag}: git bundle create {path} failed ({detail}); {sid} is finished")
        return
    _info(f"{tag}: bundle {path} (all branches and refs/hacf/*; copy it off this machine)")


def _rollback(repo: Path, active: Path, original: str, head: str, snapshot: str, tag: str) -> None:
    """Undo a finish commit step that stopped before it committed.

    Restores activeContext.md and the index snapshot (git read-tree), so index-only state
    such as a 'git rm --cached' survives; signals are ignored meanwhile. When HEAD moved,
    the commit was made and nothing is rolled back.
    """
    with _ignored_signals():
        now = _git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
        if now != head:
            _warn(
                f"{tag}: HEAD moved to {now[:12]} although the commit step did not finish; "
                "nothing was rolled back and no slice ref was recorded; "
                + _undo_advice(repo, now, head, "finish")
            )
            return
        restored: list[str] = []
        try:
            with active.open("w", encoding="utf-8", newline="") as handle:
                handle.write(original)
            restored.append(ACTIVE)
        except OSError as exc:
            _warn(f"{tag}: could not restore {ACTIVE}: {_one_line(exc)}")
        error = _restore_index(repo, snapshot)
        if error is None:
            restored.append("the index")
        else:
            _warn(f"{tag}: git read-tree {snapshot} failed ({error}); the index may hold changes")
        _warn(f"{tag}: nothing was committed; restored {' and '.join(restored) or 'nothing'}")


def _unvalidated(repo: Path, staged: list[str], validated: set[str], base: str) -> list[str]:
    """Return the staged paths that are not validated, activeContext.md or equal to base.

    A path staged only because an earlier manual commit changed it, and now staged back
    to the scope base's content, is not a new change.
    """
    candidates = [path for path in staged if path != ACTIVE and path not in validated]
    if not candidates:
        return []
    differs = _git_paths(
        repo, "diff", "--cached", "--name-only", "--no-renames", "--no-color", "-z", base, "--"
    )
    return sorted(path for path in candidates if path in differs)


class _Checked(NamedTuple):
    """What finish checked before its commit step, for the commit step to compare with."""

    paths: set[str]  # the paths changed since the scope base, at the last scope check
    tree: str  # the tree finish's own staging made of the checked work tree
    active: _PathState  # activeContext.md as the fingerprints around check.sh saw it


def _staged_content_problems(repo: Path, checked: _Checked, tree: str, written: str) -> list[str]:
    """Return the paths whose staged content is not what finish checked or wrote.

    tree (the staged index) may differ from checked.tree only in activeContext.md, and
    its staged blob must be exactly written, the text finish wrote there.
    """
    listed = _git_paths(
        repo, "diff-tree", "-r", "--no-renames", "--name-only", "-z", checked.tree, tree
    )
    problems = sorted(_safe(path) for path in listed if path != ACTIVE)
    return problems + _blob_problems(repo, tree, {ACTIVE: written.encode("utf-8")})


def _commit_slice(
    repo: Path,
    sid: str,
    block: dict[str, Any],
    force_dirs: tuple[str, ...],
    message: str,
    tag: str,
    checked: _Checked,
    base: str,
) -> tuple[str, str, list[str], str]:
    """Add the slice to the completed list, stage, check what is staged and commit.

    Returns (new commit, previous HEAD, staged paths, staged tree). activeContext.md must
    still be what the checks saw; every staged path must be in checked.paths,
    activeContext.md, or staged with the scope base's content; and the staged tree may
    differ from checked.tree only by the text finish wrote to activeContext.md. Any
    failure or interrupt before the commit restores activeContext.md and the index, then
    propagates; cmd_finish traps SIGTERM and SIGHUP around this step, so they raise here.
    """
    active = repo / ACTIVE
    original = _read_text_exact(active)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    snapshot = _index_tree(repo)
    try:
        if hashlib.sha256(original.encode("utf-8")).hexdigest() != checked.active.detail:
            raise GateError(
                f"staged content differs from what finish checked: {ACTIVE} (it changed "
                "after the checks ran)"
            )
        title = block["title"].strip()
        entry, written = append_completed(repo, sid, block["nn"], title, original)
        added = f"added {entry!r}" if entry else f"{sid} is already listed"
        _info(f"{tag}: {ACTIVE}: {added}")
        staged = _stage(repo, None, force_dirs)
        if not staged:
            raise GateError("nothing to commit")
        stray = _unvalidated(repo, staged, checked.paths, base)
        if stray:
            raise GateError(
                "staged path(s) that the scope check did not validate (written while "
                f"finish ran?): {', '.join(_safe(path) for path in stray)}"
            )
        tree = _index_tree(repo)
        differs = _staged_content_problems(repo, checked, tree, written)
        if differs:
            raise GateError(
                "staged content differs from what finish checked (written while finish "
                f"ran?): {', '.join(differs)}"
            )
        committed = _committed_problems(repo, head, tree)
        if committed:
            raise GateError("the staged commit is refused: " + "; ".join(committed))
        sha = _commit_staged(repo, message)
    except BaseException:
        _rollback(repo, active, original, head, snapshot, tag)
        raise
    return sha, head, staged, tree


def _verify_commit(
    repo: Path, sha: str, head: str, staged: list[str], message: str, tree: str
) -> list[str]:
    """Return how commit sha differs from what finish staged and wrote (empty when equal).

    Compares its parent with head, its tree with tree (what finish staged and checked),
    its changed paths (git diff-tree) with staged, its stored message with message, and
    checks that HEAD is still on GATE_BRANCH.
    """
    problems: list[str] = []
    header, _, stored = _git(repo, "cat-file", "commit", sha).stdout.partition("\n\n")
    parents = [line[len("parent ") :] for line in header.split("\n") if line.startswith("parent ")]
    if parents != [head]:
        listed = ", ".join(parent[:12] for parent in parents) or "none"
        problems.append(f"its parent(s) ({listed}) are not the commit finish started from")
    stored_tree = _git(repo, "rev-parse", f"{sha}^{{tree}}").stdout.strip()
    if stored_tree != tree:
        problems.append(
            f"its tree {stored_tree[:12]} is not the tree finish staged and checked "
            f"({tree[:12]}; another process?)"
        )
    if stored != message:
        wanted, got = message.split("\n"), stored.split("\n")
        added = " | ".join(_safe(line) for line in got if line not in wanted) or "-"
        removed = " | ".join(_safe(line) for line in wanted if line not in got) or "-"
        problems.append(
            "its message differs from the one finish wrote (another process?); "
            f"added: {added}; removed: {removed}"
        )
    committed = _git_paths(
        repo, "diff-tree", "-r", "--no-commit-id", "--name-only", "--no-renames", "-z", sha
    )
    extra = sorted(committed - set(staged))
    missing = sorted(set(staged) - committed)
    if extra:
        listed = ", ".join(_safe(path) for path in extra)
        problems.append(
            f"it changes path(s) that finish did not stage (another process?): {listed}"
        )
    if missing:
        listed = ", ".join(_safe(path) for path in missing)
        problems.append(f"it lacks path(s) that finish staged: {listed}")
    branch = _current_branch(repo)
    if branch != GATE_BRANCH:
        problems.append(f"after the commit, {_head_state(branch)}, not {GATE_BRANCH}")
    return problems


def _interrupted_after_commit(repo: Path, tag: str, sid: str, sha: str, head: str) -> None:
    """Say what an interrupt after finish committed sha (on top of head) left behind.

    The slice ref decides: when it names sha the slice is finished; otherwise HEAD is a
    finish without its ref, which the next finish refuses until it is reset. Signals are
    ignored meanwhile.
    """
    with _ignored_signals():
        slice_ref = f"{SLICE_REF_PREFIX}{sid}"
        if _gate_refs(repo).get(slice_ref) == sha:
            _warn(
                f"{tag}: interrupted after it recorded {sha[:12]} in {slice_ref}; {sid} is finished"
            )
        elif _head_is(repo, sha, head):
            _warn(f"{tag}: {_interrupted_advice(sha, sid)}")
        else:
            _warn(
                f"{tag}: interrupted after the commit; no slice ref was recorded; "
                + _undo_advice(repo, sha, head, "finish")
            )


def cmd_finish(
    repo: Path, sid: str, miniprd: str, waivers: dict[str, str | None], no_track_spec: bool
) -> int:
    """Validate one finished slice, commit it with an HACF-Slice trailer, record its ref.

    waivers maps each flag of WAIVER_FLAGS to its REASON, or to None when it is absent.
    """
    if not SID_RE.fullmatch(sid):
        raise UsageError(f"SID {sid!r} is not like S00, S01a, S09a1 or S15b")
    reasons = {flag: _reason(flag, waivers.get(flag)) for flag, _ in WAIVER_FLAGS}
    _require_branch(repo)
    tag = f"finish {sid}"
    refused = f"{tag}: refused; nothing was committed"
    path = _locate(repo, miniprd)
    state = _history(repo)
    for warning in state.warnings:
        _warn(f"{tag}: {warning}")
    for other in state.unrecorded:
        if other != sid:
            _warn(f"{tag}: {_interrupted_advice(state.commits[0].sha, other)}")
    replaced = _replace_warning(repo)
    if replaced:
        _warn(f"{tag}: {replaced}")
    problems = _file_type_problems(repo)
    # Refused before the validators (the repository's own scripts) run; no flag waives these.
    blocking = _index_flag_problems(repo) + _graft_problems(repo)
    if sid in state.unrecorded:
        blocking.append(_interrupted_advice(state.commits[0].sha, sid))
    if blocking:
        _report(refused, problems + blocking)
        return 1
    post = state.post
    if post is None or state.pin_problems:
        unpinned = [f"no '{POST_RESOLVE_PREFIX}' commit pins the blocks; run post-resolve"]
        _report(refused, problems + (state.pin_problems or unpinned))
        return 1
    recorded = _recorded_slice_problems(repo, state)
    if recorded:
        _report(refused, problems + recorded)
        return 1
    found = _finish_block(repo, path, miniprd, sid, state, reasons["--accept-block-change"], tag)
    block, pinned, block_changes = found.block, found.pinned, found.changed
    if block is None or pinned is None:
        _report(refused, problems + found.problems)
        return 1
    if no_track_spec and pinned["track_spec"] is not False:
        problem = (
            f"--no-track-spec is refused: the block pinned in the post-resolve commit "
            f"{post.sha[:12]} has track_spec: {pinned['track_spec']!r}; only a slice whose "
            "pinned block has track_spec: false may leave spec/ untracked (no flag waives this)"
        )
        _report(refused, [*problems, problem])
        return 1
    applied: dict[str, str] = {}
    if block_changes:
        applied[BLOCK_TRAILER] = reasons["--accept-block-change"] or ""
    nn = block["nn"]
    finished = dict(state.completed)
    slice_ref = f"{SLICE_REF_PREFIX}{sid}"
    previous = state.refs.get(slice_ref)
    refinish = reasons["--refinish"]
    if previous is not None and refinish is None:
        if sid in finished:
            problems.append(
                f"{sid} already has an HACF-Slice commit ({finished[sid][:12]}, {slice_ref}); "
                "finish it again only with --refinish REASON"
            )
        else:
            problems.append(
                f"{slice_ref} already exists ({previous[:12]}), although {sid} is not a "
                "finished slice in HEAD's history; finish it again only with --refinish REASON"
            )
    elif previous is not None:
        applied[REFINISH_TRAILER] = refinish or ""
        _info(f"{tag}: finishing {sid} again (its slice ref names {previous[:12]}): {refinish}")
    elif refinish is not None:
        _info(f"{tag}: {sid} has no slice ref yet; no {_key(REFINISH_TRAILER)} trailer is added")
    pending = [dep for dep in block["depends_on"] if dep not in finished]
    out_of_order = reasons["--out-of-order"]
    if pending and out_of_order is None:
        problems.append(
            f"depends_on names slice(s) without a slice ref that counts: {', '.join(pending)} "
            "(finish them first; use --out-of-order REASON only after the user approves)"
        )
    elif pending:
        applied[OUT_OF_ORDER_TRAILER] = out_of_order or ""
        _info(f"{tag}: finishing before {', '.join(pending)}: {out_of_order}")
    elif out_of_order is not None:
        _info(f"{tag}: every dependency is finished; no {_key(OUT_OF_ORDER_TRAILER)} trailer")
    track_spec = block["track_spec"] and not no_track_spec
    force_dirs = FINISH_ADD_DIRS if track_spec else ()
    base = state.base or post
    manual = _unsliced_commits(repo, base, state.commits)
    if manual:
        _warn(
            f"{tag}: {len(manual)} commit(s) since the scope base have no HACF-Slice trailer "
            "(manual commits; this finish checks their changes as part of the slice): "
            + "; ".join(_commit_label(commit) for commit in manual)
        )
    # The validators are the repository's own scripts: run them before the checks below,
    # so the checks also see anything they wrote.
    validator_problems, validator_changes = run_validators(repo, tag, base.sha)
    problems += validator_problems
    accept_validators = reasons["--accept-validator-change"]
    if validator_changes and accept_validators is None:
        listing = "".join(
            f"\n      {_safe(change.label)}: {_safe(item)}"
            for change in validator_changes
            for item in change.new
        )
        problems.append(
            f"{len(validator_changes)} validator(s) whose scope-base copy reports, on the "
            f"working tree, what it does not report on the scope base:{listing}\n    (a "
            "weakened validator, or data the older one rejects; fix it, or use "
            "--accept-validator-change REASON only after the user approves)"
        )
    elif validator_changes:
        applied[VALIDATOR_TRAILER] = accept_validators or ""
        names = ", ".join(change.label for change in validator_changes)
        _info(f"{tag}: accepting what the scope-base copies report ({names}): {accept_validators}")
    elif accept_validators is not None:
        _info(f"{tag}: no validator change to accept; no {_key(VALIDATOR_TRAILER)} trailer")
    problems += check_ledger(repo, block["removes_nodes"], tag, base.sha)
    problems += _finish_guards(repo, post, tag)
    changed, outside, note = scope_check(repo, base, block, force_dirs)
    _info(f"{tag}: scope base {_commit_label(base)}; {len(changed)} changed path(s)")
    if note:
        _info(f"{tag}: {note}")
    accept = reasons["--accept-scope"]
    listing = "".join(f"\n      {_safe(item)}" for item in outside)
    if outside and accept is None:
        problems.append(
            f"{len(outside)} changed path(s) match no files glob in the block:{listing}"
            "\n    (fix the change within the globs; use --accept-scope REASON only after "
            "the user approves these paths)"
        )
    elif outside:
        applied[SCOPE_TRAILER] = accept or ""
        _info(f"{tag}: accepting {len(outside)} out-of-scope path(s): {accept}{listing}")
    else:
        _info(f"{tag}: every changed path is in scope")
        if accept:
            _info(f"{tag}: nothing to accept; no {_key(SCOPE_TRAILER)} trailer is added")
    empty = reasons["--allow-empty"]
    if all(_bookkeeping(item, nn) for item in changed):
        if empty is None:
            problems.append(
                f"only bookkeeping changed since the scope base ({LEDGER}, {ACTIVE} and this "
                "slice's own MiniPRD paths); finish an empty slice only with --allow-empty REASON"
            )
        else:
            applied[EMPTY_TRAILER] = empty
            _info(f"{tag}: accepting a slice that changed only bookkeeping: {empty}")
    elif empty is not None:
        _info(f"{tag}: the slice is not empty; no {_key(EMPTY_TRAILER)} trailer is added")
    problems += _check_active(repo)
    problems += _ignored_python(repo)
    check_problem = _check_script_problem(repo, base)
    if check_problem:
        problems.append(check_problem)
    if problems:
        _report(refused, problems)
        return 1
    rel = _repo_rel(repo, path) or str(path)
    checked = _run_check_script(repo, base, block, force_dirs, changed, outside, rel, tag)
    if checked is None:
        return 1
    records: list[tuple[str, list[str]]] = []
    if SCOPE_TRAILER in applied:
        records.append((ACCEPTED_PATHS_HEADING, [_record_path(item) for item in outside]))
    if BLOCK_TRAILER in applied:
        changes = [_key_change(key, pinned[key], block[key]) for key in block_changes]
        records.append((CHANGED_KEYS_HEADING, changes))
    if OUT_OF_ORDER_TRAILER in applied:
        records.append((UNFINISHED_DEPS_HEADING, pending))
    if VALIDATOR_TRAILER in applied:
        labels = [_record_path(change.label) for change in validator_changes]
        records.append((VALIDATOR_CHANGE_HEADING, labels))
    title = block["title"].strip()
    message = _slice_message(sid, title, applied, records, _tool_version(tag))
    sha = head = ""
    # SIGTERM and SIGHUP stay trapped until the slice ref is recorded, so an interrupt
    # after the commit still says what to do.
    with _trapped_signals():
        try:
            sha, head, staged, tree = _commit_slice(
                repo, sid, block, force_dirs, message, tag, checked, base.sha
            )
            mismatch = _verify_commit(repo, sha, head, staged, message, tree)
            if mismatch:
                _report(
                    f"{tag}: git commit succeeded, but HEAD ({sha[:12]}) is not what finish "
                    "staged and wrote; no slice ref was recorded",
                    [*mismatch, _undo_advice(repo, sha, head, "finish")],
                )
                return 1
            commands = [_set_ref(slice_ref, sha, previous)]
            if previous is not None:
                commands.insert(0, f"update {SUPERSEDED_REF_PREFIX}{sid}/{previous} {previous}")
            error = _update_refs(repo, commands)
        except (_Interrupted, KeyboardInterrupt):
            if sha:
                _interrupted_after_commit(repo, tag, sid, sha, head)
            raise
    if error:
        raise GateError(
            f"committed {sha}, but could not record it in {slice_ref} ({error}); until that "
            f"ref names it, {sid} is not a finished slice. Find out what changed the ref, "
            f"then run: git update-ref {slice_ref} {sha}"
        )
    _info(f"{tag}: committed {sha} ({len(staged)} paths); recorded in {slice_ref}")
    for trailer in WAIVER_TRAILERS:
        if trailer in applied:
            _info(f"{tag}: waiver applied: {trailer}{applied[trailer]}")
    try:
        _bundle(repo, sid, tag)
    except KeyboardInterrupt:
        _warn(f"{tag}: interrupted while writing the bundle; {sid} is finished")
    return 0


def _run_check_script(
    repo: Path,
    base: _Commit,
    block: dict[str, Any],
    force_dirs: tuple[str, ...],
    changed: set[str],
    outside: list[str],
    miniprd: str,
    tag: str,
) -> _Checked | None:
    """Run dev/scripts/check.sh; return what finish checked, or None on a refusal.

    Before the script, finish fingerprints HEAD, its symbolic ref, the index (entries and
    flags) and every path it checked (the changed paths, the ledger, activeContext.md,
    guards.json, each guarded path, the --miniprd file and the script), then builds the
    tree its own staging would commit from that work tree (_valid_tree) and refuses a
    symlink or gitlink entry in it, or a file whose bytes on disk differ from its blob
    (_committed_problems). The script runs in a new session; its process group is killed
    afterwards. Any difference afterwards, or a path that changed versus the scope base
    only while the script ran (out of scope or in scope), refuses the finish; nothing is
    written.
    """
    refused = f"{tag}: refused; nothing was committed"
    guarded = [guard.path for guard in _guard_states(repo) if _inside_repo(repo, guard.path)]
    watched = {*changed, LEDGER, ACTIVE, GUARDS, CHECK_SCRIPT, miniprd, *guarded}
    before = _fingerprint(repo, watched)
    tree = _valid_tree(repo, force_dirs)
    committed = _committed_problems(repo, before.head, tree)
    if committed:
        _report(refused, committed)
        return None
    if (repo / CHECK_SCRIPT).is_file():
        _info(f"{tag}: running bash {CHECK_SCRIPT}")
        code, _ = _run_script(["bash", CHECK_SCRIPT], repo, CHECK_TIMEOUT)
        if code != 0:
            _report(refused, [f"{CHECK_SCRIPT} exited {code}"])
            return None
    else:
        _info(f"{tag}: {CHECK_SCRIPT} does not exist yet; skipped")
    drift = _drift(before, _fingerprint(repo, watched))
    after, after_outside, _ = scope_check(repo, base, block, force_dirs)
    appeared = sorted(set(after_outside) - set(outside))
    # In-scope paths that changed only while the script ran (a test that writes into the
    # repo): the fingerprint covers just the paths that had changed before it ran.
    written = sorted(after - watched - set(after_outside))
    problems: list[str] = []
    if drift:
        listing = "".join(f"\n      {item}" for item in drift)
        problems.append(
            f"{CHECK_SCRIPT} changed {len(drift)} item(s) that finish had already checked:"
            f"{listing}\n    (restore them, e.g. git switch {GATE_BRANCH} and git restore, "
            "then run finish again)"
        )
    if appeared:
        listing = "".join(f"\n      {_safe(item)}" for item in appeared)
        problems.append(
            f"{len(appeared)} out-of-scope path(s) appeared while the checks ran:{listing}"
            "\n    (remove them, or re-run finish with an approved --accept-scope REASON)"
        )
    if written:
        listing = "".join(f"\n      {_safe(item)}" for item in written)
        problems.append(
            f"{len(written)} in-scope path(s) changed while the checks ran:{listing}"
            f"\n    ({CHECK_SCRIPT} must not write into the repository; remove or restore "
            "them, or keep the ones this slice needs, then run finish again)"
        )
    if problems:
        _report(refused, problems)
        return None
    # Without drift, both fingerprints saw activeContext.md the same way.
    return _Checked(after, tree, before.paths[ACTIVE])


def cmd_restore_ledger(repo: Path) -> int:
    """Save the working-tree ledger to the backup directory and restore HEAD's copy."""
    tag = "restore-ledger"
    _require_branch(repo)
    if _blob(repo, "HEAD", LEDGER) is None:
        _report(f"{tag}: refused", [f"HEAD has no {LEDGER}"])
        return 1
    current = repo / LEDGER
    if current.is_file():
        dest = _new_backup_dir(backup_root(repo), "restore") / "architecture.yml"
        shutil.copy2(current, dest)
        _info(f"{tag}: saved the working-tree ledger to {dest}")
    else:
        _info(f"{tag}: there is no working-tree {LEDGER} to save")
    _git(repo, "checkout", "HEAD", "--", LEDGER)
    nodes, problems = load_ledger(current.read_text(encoding="utf-8", errors="replace"), LEDGER)
    if nodes is None or problems:
        _report(f"{tag}: restored {LEDGER} from HEAD, but it does not validate", problems)
        return 1
    _info(f"{tag}: restored {LEDGER} from HEAD ({len(nodes)} nodes, parses)")
    return 0


def cmd_status(repo: Path) -> int:
    """Print a read-only summary of the v0.6.0 bootstrap state.

    It also warns about a post-resolve pin that finish would refuse (with the command that
    records a lost post-resolve ref again), slice refs that do not count, recorded slice
    commits that change paths their block and waivers do not allow, commits with an
    HACF-Slice trailer that no gate ref names (HEAD first: a finish interrupted after its
    commit), lookalike markers in activeContext.md, replacement refs and a grafts file.
    """
    state = _history(repo)
    print(f"v0.6.0 gate status: {repo}")
    replaced = _replace_warning(repo)
    if replaced:
        _warn(replaced)
    for problem in _graft_problems(repo):
        _warn(problem)
    branch = _current_branch(repo)
    if branch == GATE_BRANCH:
        print(f"  branch: {branch}")
    else:
        print(f"  branch: {_safe(branch or 'detached HEAD')} (not {GATE_BRANCH})")
        _warn(
            f"{_head_state(branch)}; post-resolve, finish and restore-ledger run only "
            f"on {GATE_BRANCH}"
        )
    pinned = state.refs.get(POST_RESOLVE_REF)
    if state.post is not None:
        print(f"  post-resolve commit: {_commit_label(state.post)} ({POST_RESOLVE_REF})")
    elif pinned is not None:
        print(f"  post-resolve commit: {POST_RESOLVE_REF} names {pinned[:12]}, not in HEAD")
    else:
        print("  post-resolve commit: none yet")
    for problem in state.pin_problems:
        _warn(f"post-resolve pin: {problem} (finish refuses until this is fixed)")
    names = ", ".join(f"{sid} ({sha[:12]})" for sid, sha in state.completed) or "none"
    print(f"  completed slices ({len(state.completed)}): {names}")
    for sid in state.unrecorded:
        _warn(_interrupted_advice(state.commits[0].sha, sid))
    for warning in state.warnings:
        _warn(warning)
    for problem in _recorded_slice_problems(repo, state):
        _warn(f"{problem}; finish refuses until this is fixed")
    active = _read_bytes(repo / ACTIVE)
    for problem in lookalike_problems((active or b"").decode("utf-8", errors="replace")):
        _warn(problem)
    waivers = _waiver_trailers(state.commits)
    print(f"  waivers ({len(waivers)}):" + ("" if waivers else " none"))
    records = {
        SCOPE_TRAILER: (ACCEPTED_PATHS_HEADING, "accepted path"),
        BLOCK_TRAILER: (CHANGED_KEYS_HEADING, "changed key"),
        OUT_OF_ORDER_TRAILER: (UNFINISHED_DEPS_HEADING, "unfinished dependency"),
        VALIDATOR_TRAILER: (VALIDATOR_CHANGE_HEADING, "changed validator"),
    }
    for commit, trailer, reason in waivers:
        print(f"    {commit.sha[:12]} {trailer}{_safe(reason)}")
        if trailer in records:
            heading, kind = records[trailer]
            for item in _listed(commit, heading):
                print(f"      {kind}: {_safe(item)}")
    base = state.base
    print(f"  scope base: {_commit_label(base) if base else 'none (run post-resolve first)'}")
    named, unnamed = _compiled_miniprds(repo)
    print(f"  MiniPRDs remaining in {COMPILED_DIR}: {len(named) + len(unnamed)}")
    for nn, name in named:
        block, _ = parse_block(repo / COMPILED_DIR / name, name)
        print(f"    {nn:02d} {name} [{block['slice'] if block else 'invalid block'}]")
    for name in unnamed:
        print(f"    ?? {name} (not named MiniPRD_NN_<Slug>.md)")
    print(f"  next NN: {f'{named[0][0]:02d} ({named[0][1]})' if named else 'none'}")
    superprd = "present" if (repo / SUPERPRD_V060).is_file() else "absent"
    if (repo / SUPERPRD_BARE).exists():
        superprd += f"; bare {SUPERPRD_BARE} present (post-resolve renames it)"
    print(f"  {SUPERPRD_V060}: {superprd}")
    ledger = repo / LEDGER
    if not ledger.is_file():
        print(f"  ledger: {LEDGER} is missing")
    else:
        text = ledger.read_text(encoding="utf-8", errors="replace")
        nodes, problems = load_ledger(text, LEDGER)
        if nodes is None:
            print(f"  ledger: does not load: {problems[0]}")
        else:
            print(f"  ledger: parses ({len(nodes)} nodes, {len(problems)} structural problem(s))")
            for problem in problems:
                print(f"    {problem}")
    states = _guard_states(repo)
    if not states:
        print("  guards: none listed")
    for guard in states:
        detail = "" if guard.state == "ok" else f" ({guard.detail})"
        print(f"  guards: {guard.state} {_safe(guard.path)}{detail}")
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


class _Fail(Exception):
    """A selftest expectation that did not hold."""


_SELFTEST_GUARDED = "# SuperPRD: hyper_update (selftest copy)\n\nThis file is guarded.\n"
_SELFTEST_ACTIVE = (
    f"# Active Context\n\n{PROCEDURE_HEADING}\n\n"
    f"### Completed v0.6.0 slices\n\n{COMPLETED_BEGIN}\n{COMPLETED_END}\n\n"
    "## Purpose\nSelftest copy.\n"
)
_SELFTEST_SLICES = (
    "# v0.6.0 Slice Seeds (selftest)\n\n"
    '<a id="s00"></a>\n## S00 — NN 00: Test harness\n\n'
    "- **Ledger node:** none\n- **Depends on:** —\n\n"
    "**Files to create**\n\n- dev/tests/test_harness.py\n\n"
    "**Files to modify**\n\n- `src/app.py`, the value\n"
    "- spec/compiled/architecture.yml (via the script only)\n\n"
    "**Files to delete**\n\n- (none)\n\n"
    "**Tests (deterministic)**\n\n- test_harness_ok; every test_harness_* case.\n\n"
    "**Negative constraints**\n\n- DO NOT touch lib/ (test_not_a_seed_test).\n\n"
    '<a id="s01a"></a>\n## S01a — NN 01: Ledger core\n\n'
    "- **Depends on:** S00\n\n"
    "**Files to create**\n\n- lib/\n\n"
    "**Files to modify**\n\n- (none)\n\n"
    "**Files to delete**\n\n- (none)\n\n"
    "**Tests (deterministic)**\n\n- test_ledger_core\n\n"
    '<a id="s01b"></a>\n## S01b — NN 02: Ledger migrate\n\n'
    "- **Depends on:** S01a\n\n"
    "**Files to create**\n\n- (none)\n\n"
    "**Files to modify**\n\n- src/legacy.py\n\n"
    "**Files to delete**\n\n- (none)\n\n"
    "**Tests (deterministic)**\n\n- test_migrate_*\n\n"
    '```text\n<a id="s99"></a>\n## S99 — NN 99: inside a fence\n\n'
    "**Files to create**\n\n- fenced/never.py\n```\n"
)
# The test name each fixture MiniPRD lists (its seed's test tokens need it).
_SELFTEST_TESTS = {"S00": "test_harness_ok", "S01a": "test_ledger_core", "S01b": "test_migrate_v2"}
_SELFTEST_BANNER = (
    "> **Overrides (read first):** the Operating Notes below replace the Phase 3 mode, the\n"
    "> Phase 4 archive command and the updater command; read `## Pipeline\n"
    "> Operating Notes` first.\n"
)
_SELFTEST_SUPERPRD = (
    "# SuperPRD: HACF v0.6.0 (selftest)\n\n"
    "**Seed deviations** (C4.12 reads this table.)\n\n"
    "| SID | Item | R-row |\n|---|---|---|\n"
)
_SELFTEST_TOOL = f"{TOOL_VERSION_LABEL}2.1.305 (selftest claude stub)"
_SELFTEST_CHECK = (
    "#!/usr/bin/env bash\n# selftest stand-in for dev/scripts/check.sh\n"
    'if [ -n "${V060_SELFTEST_CHECK_TOUCH:-}" ]; then echo x > "$V060_SELFTEST_CHECK_TOUCH"; fi\n'
    'case "${V060_SELFTEST_CHECK_MODE:-}" in\n'
    "  ledger) echo 'nodes: []' > spec/compiled/architecture.yml ;;\n"
    "  branch) git checkout -q -b selftest-elsewhere ;;\n"
    "  stage) git add src/app.py ;;\n"
    "  assume) git update-index --assume-unchanged src/app.py ;;\n"
    "  intent) git rm -q --cached src/empty.py && git add -N src/empty.py ;;\n"
    "  chmod) chmod +x src/app.py ;;\n"
    "  commit) git commit -q --allow-empty -m 'selftest: a commit made by check.sh' ;;\n"
    '  exclude) echo src/new.py >> "$(git rev-parse --git-path info/exclude)" ;;\n'
    '  background) sleep 60 >/dev/null 2>&1 & echo "$!" > "$V060_SELFTEST_CHECK_PIDFILE" ;;\n'
    "esac\n"
    'exit "${V060_SELFTEST_CHECK_RC:-0}"\n'
)
_SELFTEST_CHECK_ENV = (
    "V060_SELFTEST_CHECK_RC",
    "V060_SELFTEST_CHECK_TOUCH",
    "V060_SELFTEST_CHECK_MODE",
    "V060_SELFTEST_CHECK_PIDFILE",
)
_SELFTEST_HARNESS = "MiniPRD_00_Harness.md"
_SELFTEST_CORE = "MiniPRD_01_Ledger_Core.md"
_SELFTEST_MIGRATE = "MiniPRD_02_Ledger-Migrate.md"


def _selftest_ledger(drop_node: str = "", drop_key: tuple[str, str] = ("", "")) -> str:
    nodes: list[dict[str, Any]] = []
    for node_id, dimension, depends in (
        ("alpha", "Layer", []),
        ("beta", "Module", ["alpha"]),
        ("gamma", "Module", ["alpha"]),
    ):
        if node_id == drop_node:
            continue
        node: dict[str, Any] = {
            "id": node_id,
            "dimension": dimension,
            "status": "clean",
            "associated_file": f"src/{node_id}.py",
            "description": f"Selftest node {node_id}.",
            "inputs": [],
            "outputs": [],
            "edges": {"depends_on": list(depends), "implements": []},
        }
        if drop_key[0] == node_id:
            del node[drop_key[1]]
        nodes.append(node)
    return yaml.safe_dump({"nodes": nodes}, sort_keys=False)


def _selftest_block(
    sid: str, nn: int, title: str, depends_on: list[str], files: list[str], audit: str
) -> dict[str, Any]:
    return {
        "slice": sid,
        "nn": nn,
        "title": title,
        "depends_on": depends_on,
        "node_id": "none",
        "parent": "none",
        "ensure_node": "none",
        "files": files,
        "oracle": [["true"]],
        "audit_phase3": audit,
        "removes_nodes": [],
        "track_spec": True,
        "lore_refs": ["ADR-0001"] if nn == 0 else [],
        "annex_refs": [],
    }


def _selftest_blocks() -> dict[str, dict[str, Any]]:
    harness = _selftest_block("S00", 0, "Test harness", [], ["src/**", "dev/tests/**"], "skip")
    core = _selftest_block("S01a", 1, "Ledger core", ["S00"], ["lib/**"], "legacy-haiku")
    migrate = _selftest_block(
        "S01b", 2, "Ledger migrate", ["S01a"], ["src/legacy.py", "src/app.py"], "review-patch"
    )
    harness["annex_refs"] = [f"{ANNEX}/slices.md#s00"]
    core["annex_refs"] = [f"{ANNEX}/slices.md#s01a"]
    migrate["annex_refs"] = [f"{ANNEX}/slices.md#s01b--nn-02-ledger-migrate"]
    return {_SELFTEST_HARNESS: harness, _SELFTEST_CORE: core, _SELFTEST_MIGRATE: migrate}


def _selftest_miniprd(block: dict[str, Any], extra: str = "") -> str:
    """Return a MiniPRD with block; extra is raw YAML appended to the block body."""
    body = yaml.safe_dump(block, sort_keys=False, default_flow_style=None) + extra
    test = _SELFTEST_TESTS.get(block["slice"], "test_selftest_unseeded")
    return (
        f"# MiniPRD: {block['title']}\n\n"
        f"**Hypergraph Node ID:** {block['node_id']}\n**Parent Node:** {block['parent']}\n\n"
        f"{_SELFTEST_BANNER}\n"
        "## 1. The Confidence Mandate\n\nSelftest fixture.\n\n"
        f"## 5. Integration Tests & Verification\n\n- {test}: selftest fixture.\n\n"
        "## Pipeline Operating Notes\n\nSelftest notes for the execute and audit sessions.\n\n"
        f"```{BLOCK_INFO}\n{body}```\n"
    )


def _selftest_files() -> dict[str, str]:
    guard = hashlib.sha256(_SELFTEST_GUARDED.encode("utf-8")).hexdigest()
    ignores = ["spec/active/*", "!spec/active/.gitkeep", "spec/archive/*"]
    ignores += ["!spec/archive/.gitkeep", "spec/compiled/*", "!spec/compiled/.gitkeep"]
    return {
        ".gitignore": "\n".join([*ignores, "__pycache__/", ""]),
        ACTIVE: _SELFTEST_ACTIVE,
        f"{ANNEX}/slices.md": _SELFTEST_SLICES,
        GUARDS: json.dumps({"spec/compiled/SuperPRD_hyper_update.md": guard}, indent=2) + "\n",
        CHECK_SCRIPT: _SELFTEST_CHECK,
        "lore/ADRs/ADR-0001-x.md": "# ADR-0001: selftest decision\n",
        "spec/active/.gitkeep": "",
        "spec/archive/.gitkeep": "",
        "spec/compiled/.gitkeep": "",
        LEDGER: _selftest_ledger(),
        "spec/compiled/SuperPRD_hyper_update.md": _SELFTEST_GUARDED,
        "src/app.py": "VALUE = 1\n",
        "src/legacy.py": "OLD = True\n",
        "docs/other.md": "# Other\n",
    }


def _selftest_resolver_output() -> dict[str, str]:
    files = {
        f"{COMPILED_DIR}/{name}": _selftest_miniprd(block)
        for name, block in _selftest_blocks().items()
    }
    files[SUPERPRD_BARE] = _SELFTEST_SUPERPRD
    files["spec/archive/20261020_000000_v0_6_0/Draft_PRD.md"] = "# Draft (older archive)\n"
    files["spec/archive/20261021_000000_v0_6_0/Draft_PRD.md"] = "# Draft (newest archive)\n"
    return files


class _Selftest:
    """Scenarios for the selftest command, run against throwaway repositories under root."""

    def __init__(self, script: Path, root: Path) -> None:
        self.script = script
        self.root = root
        self.no_hooks = root / "no-hooks"
        self.no_hooks.mkdir()
        gitconfig = root / "gitconfig"
        gitconfig.write_text("", encoding="utf-8")
        self.env = {key: val for key, val in os.environ.items() if not key.startswith("GIT_")}
        for key in _SELFTEST_CHECK_ENV:
            self.env.pop(key, None)
        self.env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(gitconfig),
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CEILING_DIRECTORIES": str(root.parent),
                "GIT_TERMINAL_PROMPT": "0",
                "XDG_CONFIG_HOME": str(root),
                "HACF_BACKUP_ROOT": str(root / "backups" / "default"),
                # Validators the scenarios run must not leave __pycache__/ in a snapshot.
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        # A stub claude makes the commit body's tool line known; PATH drops the real one.
        self.no_claude = os.pathsep.join(
            folder
            for folder in os.environ.get("PATH", "").split(os.pathsep)
            if folder and not os.path.exists(os.path.join(folder, "claude"))
        )
        stub = root / "bin"
        stub.mkdir()
        claude = stub / "claude"
        claude.write_text(
            "#!/bin/sh\necho '2.1.305 (selftest claude stub)'\necho 'a second line'\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)
        self.env["PATH"] = os.pathsep.join([str(stub), self.no_claude])
        self.main: Path | None = None
        self.audited: dict[str, str] = {}

    # -- plumbing -----------------------------------------------------------

    def git(self, repo: Path, *args: str, extra: dict[str, str] | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            env={**self.env, **(extra or {})},
            input="",
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise _Fail(f"git {' '.join(args)} failed: {_one_line(proc.stderr)}")
        return proc.stdout

    def script_run(
        self, *args: str, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env.update(extra or {})
        return subprocess.run(
            [_python(), str(self.script), *args],
            cwd=str(self.root),
            env=env,
            input="",
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )

    def gate(
        self,
        repo: Path,
        *args: str,
        backups: Path | None = None,
        extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(extra or {})
        if backups is not None:
            env["HACF_BACKUP_ROOT"] = str(backups)
        return self.script_run("--repo", str(repo), *args, extra=env)

    def write(self, repo: Path, rel: str, content: str) -> None:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def init_repo(self, repo: Path, files: dict[str, str]) -> None:
        for rel, content in files.items():
            self.write(repo, rel, content)
        self.git(repo, "init", "-q", "-b", GATE_BRANCH)
        for key, value in (
            ("user.name", "v060 gate selftest"),
            ("user.email", "selftest@example.invalid"),
            ("commit.gpgsign", "false"),
            ("core.hooksPath", str(self.no_hooks)),
            ("core.excludesFile", os.devnull),
            ("core.attributesFile", os.devnull),
        ):
            self.git(repo, "config", key, value)
        self.git(repo, "add", "-A")
        spec = [folder for folder in POST_RESOLVE_ADD_DIRS if (repo / folder).exists()]
        if spec:
            self.git(repo, "add", "-f", "--", *spec)
        self.git(repo, "commit", "-q", "-m", "selftest fixture: pre-migration state")
        tracked = set(self.git(repo, "ls-files", "-z").split("\0"))
        lost = sorted(set(files) - tracked)
        self.check(not lost, f"fixture files were not committed (a foreign ignore rule?): {lost}")

    def fixture(self, name: str) -> Path:
        repo = self.root / "repos" / name
        self.init_repo(repo, _selftest_files())
        for rel, content in _selftest_resolver_output().items():
            self.write(repo, rel, content)
        return repo

    def resolved(self, name: str, mutate: Callable[[Path], None] | None = None) -> Path:
        """Return a fresh fixture after a successful post-resolve (mutate runs first)."""
        repo = self.fixture(name)
        if mutate is not None:
            mutate(repo)
        proc = self.gate(
            repo, "post-resolve", "--expect", "3", backups=self.root / "backups" / name
        )
        self.expect_rc(proc, 0)
        return repo

    def rewrite_block(
        self, repo: Path, rel: str, base: str, drop: tuple[str, ...] = (), **changes: Any
    ) -> None:
        block = dict(_selftest_blocks()[base])
        block.update(changes)
        for key in drop:
            del block[key]
        self.write(repo, rel, _selftest_miniprd(block))

    def archive(self, repo: Path, name: str, target: str = "") -> str:
        """Move a compiled MiniPRD under spec/archive (default <name>_AUDITED.md); return it."""
        target = target or f"spec/archive/{name[: -len('.md')]}_AUDITED.md"
        (repo / target).parent.mkdir(parents=True, exist_ok=True)
        os.replace(repo / COMPILED_DIR / name, repo / target)
        return target

    def audit_move(self, repo: Path, name: str) -> str:
        target = self.archive(repo, name)
        self.audited[_selftest_blocks()[name]["slice"]] = target
        return target

    def head(self, repo: Path) -> str:
        return self.git(repo, "rev-parse", "HEAD").strip()

    def message(self, repo: Path) -> str:
        """Return HEAD's commit message exactly as stored in the commit object."""
        return self.git(repo, "cat-file", "commit", "HEAD").split("\n\n", 1)[1]

    def committed(self, repo: Path) -> list[str]:
        """Return the paths that HEAD's commit changed (a move lists both of its paths)."""
        return self.git(repo, "show", "--name-only", "--no-renames", "--format=", "HEAD").split()

    def snapshot(self, repo: Path) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        files: list[tuple[str, str]] = []
        for folder, dirs, names in os.walk(repo):
            dirs[:] = sorted(entry for entry in dirs if entry != ".git")
            for name in sorted(names):
                path = Path(folder) / name
                files.append((path.relative_to(repo).as_posix(), _sha256_file(path)))
        return self.head(repo), self.git(repo, "ls-files", "-s"), tuple(files)

    @staticmethod
    def listing(folder: Path | None) -> list[str]:
        if folder is None or not folder.is_dir():
            return []
        return sorted(str(path) for path in folder.rglob("*"))

    def ref(self, repo: Path, name: str) -> str:
        """Return the object id that ref name holds, or '' when it does not exist."""
        return self.git(repo, "for-each-ref", "--format=%(objectname)", name).strip()

    def hook(self, repo: Path, name: str, body: str) -> None:
        """Install one sh hook for repo (through core.hooksPath)."""
        folder = self.root / "hooks" / repo.name
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / name
        script.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        script.chmod(0o755)
        self.git(repo, "config", "core.hooksPath", str(folder))

    def unhook(self, repo: Path) -> None:
        """Make core.hooksPath name the empty hooks directory again."""
        self.git(repo, "config", "core.hooksPath", str(self.no_hooks))

    @contextlib.contextmanager
    def in_process(self, backups: Path) -> Iterator[io.StringIO]:
        """Run gate functions in this process with the selftest environment; capture output."""
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)
        os.environ["HACF_BACKUP_ROOT"] = str(backups)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                yield output
        finally:
            os.environ.clear()
            os.environ.update(saved)

    @staticmethod
    @contextlib.contextmanager
    def patched(name: str, value: Any) -> Iterator[None]:
        """Replace the module-level function name with value (in-process scenarios)."""
        namespace = globals()
        saved = namespace[name]
        namespace[name] = value
        try:
            yield
        finally:
            namespace[name] = saved

    def completed_count(self, repo: Path, sid: str) -> int:
        text = (repo / ACTIVE).read_text(encoding="utf-8")
        region = text.split(COMPLETED_BEGIN, 1)[1].split(COMPLETED_END, 1)[0]
        return sum(1 for line in region.splitlines() if line.startswith(f"- {sid} ("))

    def need_main(self) -> Path:
        if self.main is None:
            raise _Fail("skipped: the post-resolve fixture is not available")
        return self.main

    @staticmethod
    def check(condition: bool, message: str) -> None:
        if not condition:
            raise _Fail(message)

    @staticmethod
    def expect_rc(proc: subprocess.CompletedProcess[str], code: int) -> None:
        if proc.returncode != code:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
            raise _Fail(f"exit {proc.returncode}, expected {code}; output tail:\n{tail}")

    @staticmethod
    def expect_output(proc: subprocess.CompletedProcess[str], needle: str) -> None:
        if needle not in proc.stdout + proc.stderr:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
            raise _Fail(f"output lacks {needle!r}; output tail:\n{tail}")

    def expect_refused(
        self,
        repo: Path,
        args: tuple[str, ...],
        needles: tuple[str, ...],
        backups: Path | None = None,
    ) -> None:
        """Run the gate, expect exit 1 with every needle, and no change to repo or backups."""
        before = self.snapshot(repo)
        saved = self.listing(backups)
        proc = self.gate(repo, *args, backups=backups)
        self.expect_rc(proc, 1)
        for needle in needles:
            self.expect_output(proc, needle)
        self.check(self.snapshot(repo) == before, f"the refused {args[0]} changed the repository")
        self.check(self.listing(backups) == saved, f"the refused {args[0]} wrote a backup")

    def refused_post_resolve(
        self, name: str, mutate: Callable[[Path], None], needles: tuple[str, ...], expect: int = 3
    ) -> None:
        repo = self.fixture(name)
        mutate(repo)
        backups = self.root / "backups" / name
        before = self.snapshot(repo)
        proc = self.gate(repo, "post-resolve", "--expect", str(expect), backups=backups)
        self.expect_rc(proc, 1)
        for needle in needles:
            self.expect_output(proc, needle)
        self.check(self.snapshot(repo) == before, "the refused post-resolve changed the repo")
        self.check(not backups.exists(), "the refused post-resolve wrote a backup")

    # -- scenarios: post-resolve --------------------------------------------

    def post_resolve_ok(self) -> None:
        repo = self.fixture("main")
        slices = f"{ANNEX}/slices.md"
        edited = (repo / slices).read_text(encoding="utf-8") + "\nEdited by /hyper-resolve.\n"
        self.write(repo, slices, edited)
        backups = self.root / "backups" / "main"
        proc = self.gate(repo, "post-resolve", "--expect", "3", backups=backups)
        self.expect_rc(proc, 0)
        self.check(not (repo / SUPERPRD_BARE).exists(), "SuperPRD.md was not renamed")
        self.check((repo / SUPERPRD_V060).is_file(), "SuperPRD_v0_6_0.md is missing")
        subject = self.git(repo, "log", "-1", "--format=%s").strip()
        wanted = f"{POST_RESOLVE_PREFIX} SuperPRD_v0_6_0 + 3 MiniPRDs (post-resolve)"
        self.check(subject == wanted, f"unexpected subject {subject!r}")
        tracked = set(self.git(repo, "ls-files", "-z", "spec").split("\0"))
        newest = "spec/archive/20261021_000000_v0_6_0/Draft_PRD.md"
        for rel in (SUPERPRD_V060, newest, *(f"{COMPILED_DIR}/{n}" for n in _selftest_blocks())):
            self.check(rel in tracked, f"{rel} was not committed")
        committed = self.committed(repo)
        stray = [path for path in committed if not path.startswith("spec/") and path != slices]
        self.check(not stray, f"post-resolve committed paths outside spec/: {stray}")
        self.check(slices in committed, "post-resolve did not commit the annex edit")
        self.check(self.git(repo, "status", "--porcelain") == "", "the tree is not clean")
        made = sorted(backups.glob("post-resolve-*"))
        self.check(len(made) == 1, f"expected one backup directory, found {len(made)}")
        sums = (made[0] / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        for line in sums:
            digest, _, name = line.partition("  ")
            self.check(_sha256_file(made[0] / name) == digest, f"{name} does not match")
        names = sorted(line.partition("  ")[2] for line in sums)
        wanted_names = ["spec-archive-20261021_000000_v0_6_0.tar.gz", "spec-compiled.tar.gz"]
        self.check(names == wanted_names, f"the backup holds {names}")
        with tarfile.open(made[0] / "spec-compiled.tar.gz", "r:gz") as tar:
            members = set(tar.getnames())
        self.check(SUPERPRD_V060 in members, "spec-compiled.tar.gz lacks SuperPRD_v0_6_0.md")
        self.main = repo

    def post_resolve_higher_dependency(self) -> None:
        self.refused_post_resolve(
            "dep-higher",
            lambda repo: self.rewrite_block(
                repo,
                f"{COMPILED_DIR}/{_SELFTEST_HARNESS}",
                _SELFTEST_HARNESS,
                depends_on=["S01b"],
            ),
            ("depends_on S01b has nn 2, which is not lower than S00's nn 0",),
        )

    def post_resolve_guard_mismatch(self) -> None:
        self.refused_post_resolve(
            "guard",
            lambda repo: self.write(repo, "spec/compiled/SuperPRD_hyper_update.md", "edited\n"),
            ("guard spec/compiled/SuperPRD_hyper_update.md: mismatch",),
        )

    def post_resolve_both_superprds(self) -> None:
        self.refused_post_resolve(
            "both-superprds",
            lambda repo: self.write(repo, SUPERPRD_V060, "# SuperPRD from an earlier run\n"),
            (f"refusing to rename: both {SUPERPRD_BARE} and {SUPERPRD_V060} exist",),
        )

    def post_resolve_malformed_blocks(self) -> None:
        def mutate(repo: Path) -> None:
            core, migrate = _SELFTEST_CORE, _SELFTEST_MIGRATE
            self.rewrite_block(repo, f"{COMPILED_DIR}/{core}", core, extra="x")
            self.rewrite_block(
                repo, f"{COMPILED_DIR}/{migrate}", migrate, drop=("oracle",), track_spec="yes"
            )

        self.refused_post_resolve(
            "malformed",
            mutate,
            ("unexpected key(s): extra", "missing key(s): oracle", "track_spec must be"),
        )

    def post_resolve_bad_refs(self) -> None:
        self.refused_post_resolve(
            "bad-refs",
            lambda repo: self.rewrite_block(
                repo,
                f"{COMPILED_DIR}/{_SELFTEST_HARNESS}",
                _SELFTEST_HARNESS,
                annex_refs=[
                    f"{ANNEX}/slices.md#nope",
                    f"{ANNEX}/slices.md#s99--nn-99-inside-a-fence",
                ],
                lore_refs=["ADR-0099"],
            ),
            (
                "slices.md#nope does not resolve",
                "#s99--nn-99-inside-a-fence does not resolve",
                "lore_ref ADR-0099 does not resolve",
            ),
        )

    def post_resolve_wrong_count(self) -> None:
        self.refused_post_resolve(
            "count",
            lambda repo: None,
            ("expected 4 MiniPRD_*.md files", "missing NN: 03"),
            expect=4,
        )

    def post_resolve_stray_paths(self) -> None:
        def mutate(repo: Path) -> None:
            self.write(repo, "notes/stray.txt", "stray scratch file\n")
            self.write(repo, "spec/process/2026-10-21_session.md", "# process notes\n")
            self.write(repo, "docs/other.md", "# Other\n\nEdited during resolve.\n")
            self.write(repo, "src/app.py", "VALUE = 99\n")
            self.git(repo, "add", "--", "src/app.py")
            self.write(repo, "src/app.py", "VALUE = 1\n")

        self.refused_post_resolve(
            "stray",
            mutate,
            (
                "notes/stray.txt: outside the post-resolve paths",
                "spec/process/2026-10-21_session.md: spec/process/** is never committed",
                "docs/other.md: outside the post-resolve paths",
                "src/app.py: outside the post-resolve paths",
                "commit, stash or remove the stray files",
            ),
        )

    def post_resolve_guards_edit(self) -> None:
        self.refused_post_resolve(
            "guards-edit",
            lambda repo: self.write(
                repo, GUARDS, (repo / GUARDS).read_text(encoding="utf-8") + "\n"
            ),
            (f"{GUARDS} differs from HEAD's copy",),
        )

    def post_resolve_unsafe_globs(self) -> None:
        def mutate(repo: Path) -> None:
            for name, files in (
                (_SELFTEST_HARNESS, ["**", "**/*.*", "dev/tests**", " src/**"]),
                (_SELFTEST_CORE, ["/etc/**", "*.md", "lib/**.py", "lib/** "]),
                (_SELFTEST_MIGRATE, ["../x/**", "./**", "?*/**", "src/a/***/b"]),
            ):
                self.rewrite_block(repo, f"{COMPILED_DIR}/{name}", name, files=files)

        first_segment = "has a '*' or '?' in its first path segment"
        partial = "has a '**' that is not a whole path segment"
        spaced = "has leading or trailing whitespace"
        self.refused_post_resolve(
            "globs",
            mutate,
            (
                "files entry '**': matches every path",
                f"files entry '**/*.*': {first_segment}",
                f"files entry 'dev/tests**': {partial}",
                f"files entry ' src/**': {spaced}",
                "files entry '/etc/**': is absolute",
                f"files entry '*.md': {first_segment}",
                f"files entry 'lib/**.py': {partial}",
                f"files entry 'lib/** ': {spaced}",
                "files entry '../x/**': has a '..' segment",
                "files entry './**': matches every path",
                f"files entry '?*/**': {first_segment}",
                f"files entry 'src/a/***/b': {partial}",
            ),
        )
        for pattern in ("src/**", "dev/tests/**", "spec/archive/**/MiniPRD_00_*.md", "a/**/b"):
            self.check(_glob_problem(pattern) is None, f"the safe glob {pattern!r} was refused")
        newline = "src/new\nline.py"
        self.check(bool(glob_to_regex("src/**").fullmatch(newline)), "'**' missed a newline")
        self.check(bool(glob_to_regex("src/*").fullmatch(newline)), "'*' missed a newline")

    def post_resolve_backup_inside(self) -> None:
        repo = self.fixture("backup-inside")
        inside = repo / "spec/archive/gate-backups"
        proc = self.gate(repo, "post-resolve", "--expect", "3", backups=inside)
        self.expect_rc(proc, 0)
        self.expect_output(proc, "is inside the work tree")
        fallback = repo / ".git" / "hacf-backups"
        made = sorted(fallback.glob("post-resolve-*/SHA256SUMS"))
        self.check(len(made) == 1, f"expected one backup under .git/hacf-backups, found {made}")
        self.check(not inside.exists(), "post-resolve wrote a backup inside the work tree")
        leaked = [path for path in self.committed(repo) if "gate-backups" in path]
        self.check(not leaked, f"post-resolve committed backup files: {leaked}")
        link = self.root / "link-to-backup-inside"
        link.symlink_to(repo)
        proc = self.gate(repo, "restore-ledger", backups=link / "backups")
        self.expect_rc(proc, 0)
        saved = sorted(fallback.glob("restore-*/architecture.yml"))
        self.check(len(saved) == 1, "a symlinked root inside the work tree was not redirected")
        self.check(not (repo / "backups").exists(), "restore-ledger wrote into the work tree")

    def branch_refusals(self) -> None:
        repo = self.fixture("branch")
        backups = self.root / "backups" / "branch"
        resolve = ("post-resolve", "--expect", "3")
        self.git(repo, "checkout", "-q", "-b", "feature/other")
        self.expect_refused(repo, resolve, ("HEAD is on branch feature/other",), backups)
        self.check(not backups.exists(), "the refused post-resolve wrote a backup")
        self.git(repo, "checkout", "-q", GATE_BRANCH)
        self.expect_rc(self.gate(repo, *resolve, backups=backups), 0)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited)
        self.git(repo, "checkout", "-q", "-b", "feature/after-resolve")
        for args in (finish, ("restore-ledger",)):
            self.expect_refused(repo, args, ("HEAD is on branch feature/after-resolve",), backups)
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        self.expect_output(proc, "warning: HEAD is on branch feature/after-resolve")
        self.git(repo, "checkout", "-q", "--detach")
        for args in (finish, ("restore-ledger",), resolve):
            self.expect_refused(repo, args, ("HEAD is detached",), backups)
        self.git(repo, "checkout", "-q", GATE_BRANCH)
        self.expect_rc(self.gate(repo, *finish), 0)

    def usage_errors(self) -> None:
        repo = self.fixture("usage")
        miniprd = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
        cases = (
            ("--repo", str(repo), "finish", "s00", "--miniprd", "x.md"),
            ("--repo", str(repo), "finish", "S00", "--miniprd", "spec/archive/missing.md"),
            ("--repo", str(repo), "post-resolve", "--expect", "0"),
            ("--repo", str(self.root / "no-such-dir"), "status"),
            ("--repo", str(repo), "no-such-command"),
            *(
                ("--repo", str(repo), "finish", "S00", "--miniprd", miniprd, flag, reason)
                for flag, _ in WAIVER_FLAGS
                for reason in (" \t ", "​⁠", "\x1b\x1b", "\x1b[8mhidden\x1b[0m", "-- .")
            ),
            *(
                ("--repo", str(repo), "post-resolve", REPIN_FLAG, reason)
                for reason in (" ", "​", "\x1b[0m")
            ),
        )
        for args in cases:
            proc = self.script_run(*args)
            self.check(proc.returncode == 2, f"{args[2:]!r}: exit {proc.returncode}")
        proc = self.script_run(*cases[5])
        self.expect_output(proc, "--accept-scope needs a non-empty REASON")
        proc = self.script_run(*cases[6])
        self.expect_output(proc, "REASON contains a control, format or separator character")
        proc = self.script_run(*cases[9])
        self.expect_output(proc, "REASON has no letter or digit")

    # -- scenarios: finish on the main fixture ---------------------------------

    def finish_check_fails(self) -> None:
        repo = self.need_main()
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.write(repo, "dev/tests/test_harness.py", "def test_ok():\n    assert True\n")
        ledger = repo / LEDGER
        ledger.write_text(ledger.read_text(encoding="utf-8") + "# S00\n", encoding="utf-8")
        audited = self.audit_move(repo, _SELFTEST_HARNESS)
        head = self.head(repo)
        proc = self.gate(
            repo, "finish", "S00", "--miniprd", audited, extra={"V060_SELFTEST_CHECK_RC": "3"}
        )
        self.expect_rc(proc, 1)
        self.expect_output(proc, f"{CHECK_SCRIPT} exited 3")
        self.check(self.head(repo) == head, "a failing check.sh still produced a commit")
        self.check(self.completed_count(repo, "S00") == 0, "activeContext.md changed anyway")

    def finish_sid_mismatch(self) -> None:
        repo = self.need_main()
        head = self.head(repo)
        proc = self.gate(repo, "finish", "S01a", "--miniprd", self.audited.get("S00", "x"))
        self.expect_rc(proc, 1)
        self.expect_output(proc, "the block declares slice S00, not S01a")
        self.check(self.head(repo) == head, "a SID mismatch still produced a commit")

    def finish_check_creates_file(self) -> None:
        repo = self.need_main()
        made = "docs/made-by-check.md"
        head = self.head(repo)
        proc = self.gate(
            repo,
            "finish",
            "S00",
            "--miniprd",
            self.audited.get("S00", "x"),
            extra={"V060_SELFTEST_CHECK_TOUCH": made},
        )
        self.expect_rc(proc, 1)
        self.expect_output(proc, "1 out-of-scope path(s) appeared while the checks ran")
        self.expect_output(proc, made)
        self.check(self.head(repo) == head, "a file created by check.sh was committed")
        self.check(self.completed_count(repo, "S00") == 0, "activeContext.md changed anyway")
        (repo / made).unlink()

    def finish_in_scope(self) -> None:
        repo = self.need_main()
        audited = self.audited.get("S00", "x")
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited)
        self.expect_rc(proc, 0)
        sha = self.head(repo)
        self.check(sha in proc.stdout, "finish did not print the new commit sha")
        message = self.message(repo)
        wanted = f"v0.6.0 S00: Test harness\n\n{_SELFTEST_TOOL}\n\nHACF-Slice: S00\n"
        self.check(message == wanted, f"unexpected commit message {message!r}")
        trailer = self.git(repo, "log", "-1", "--format=%(trailers:key=HACF-Slice,valueonly)")
        self.check(trailer.strip() == "S00", f"git does not parse the trailer ({trailer!r})")
        self.check(self.completed_count(repo, "S00") == 1, "activeContext.md lacks S00")
        self.check(self.git(repo, "status", "--porcelain") == "", "the tree is not clean")
        tracked = self.git(repo, "ls-files", audited).strip()
        self.check(tracked == audited, "track_spec did not force-add the audited MiniPRD")
        self.write(repo, "src/app.py", "VALUE = 3\n")
        again = ("finish", "S00", "--miniprd", audited)
        self.expect_refused(repo, again, ("S00 already has an HACF-Slice commit",))
        reason = "selftest: the user approved a follow-up fix to S00"
        proc = self.gate(repo, *again, "--refinish", reason)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"waiver applied: {REFINISH_TRAILER}{reason}")
        self.check(self.completed_count(repo, "S00") == 1, "a re-finish duplicated the entry")
        message = self.message(repo)
        wanted = (
            f"v0.6.0 S00: Test harness\n\n{_SELFTEST_TOOL}\n\n"
            f"HACF-Slice: S00\n{REFINISH_TRAILER}{reason}\n"
        )
        self.check(message == wanted, f"unexpected re-finish message {message!r}")
        head = self.head(repo)
        proc = self.gate(repo, *again, "--refinish", reason, "--allow-empty", reason)
        self.expect_rc(proc, 1)
        self.expect_output(proc, "nothing to commit")
        self.check(self.head(repo) == head, "an empty finish produced a commit")

    def finish_out_of_scope(self) -> None:
        repo = self.need_main()
        self.write(repo, "lib/ledger.py", "X = 1\n")
        self.write(repo, "docs/other.md", "# Other\n\nEdited outside the slice scope.\n")
        audited = self.audit_move(repo, _SELFTEST_CORE)
        head = self.head(repo)
        proc = self.gate(repo, "finish", "S01a", "--miniprd", audited)
        self.expect_rc(proc, 1)
        self.expect_output(proc, "1 changed path(s) match no files glob in the block")
        self.expect_output(proc, "docs/other.md")
        self.check("lib/ledger.py" not in proc.stdout + proc.stderr, "in-scope path reported")
        self.check(self.head(repo) == head, "an out-of-scope finish produced a commit")
        self.check(self.completed_count(repo, "S01a") == 0, "activeContext.md changed anyway")

    def finish_accept_scope(self) -> None:
        repo = self.need_main()
        reason = "selftest: docs edit accepted on purpose"
        audited = self.audited.get("S01a", "x")
        proc = self.gate(repo, "finish", "S01a", "--miniprd", audited, "--accept-scope", reason)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"waiver applied: {SCOPE_TRAILER}{reason}")
        message = self.message(repo)
        wanted = (
            f"v0.6.0 S01a: Ledger core\n\n{ACCEPTED_PATHS_HEADING}\n- docs/other.md\n\n"
            f"{_SELFTEST_TOOL}\n\nHACF-Slice: S01a\n{SCOPE_TRAILER}{reason}\n"
        )
        self.check(message == wanted, f"unexpected commit message {message!r}")
        self.check("docs/other.md" in self.committed(repo), "the accepted path was not committed")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S01a") == self.head(repo), "no slice ref")

    def finish_dropped_key(self) -> None:
        repo = self.need_main()
        audited = self.audit_move(repo, _SELFTEST_MIGRATE)
        self.write(repo, LEDGER, _selftest_ledger(drop_key=("beta", "status")))
        head = self.head(repo)
        proc = self.gate(repo, "finish", "S01b", "--miniprd", audited)
        self.expect_rc(proc, 1)
        self.expect_output(proc, "node beta lost key(s) present at the scope base")
        self.check(self.head(repo) == head, "a dropped key still produced a commit")

    def finish_dropped_node(self) -> None:
        repo = self.need_main()
        audited = self.audited.get("S01b", "x")
        self.write(repo, LEDGER, _selftest_ledger(drop_node="gamma"))
        (repo / "src/legacy.py").unlink()
        head = self.head(repo)
        proc = self.gate(repo, "finish", "S01b", "--miniprd", audited)
        self.expect_rc(proc, 1)
        self.expect_output(proc, "node gamma was removed but is not listed in removes_nodes")
        self.check(self.head(repo) == head, "an undeclared node removal produced a commit")
        self.rewrite_block(repo, audited, _SELFTEST_MIGRATE, removes_nodes=["gamma"])
        finish = ("finish", "S01b", "--miniprd", audited)
        self.expect_refused(repo, finish, ("(key(s): removes_nodes)",))
        reason = "selftest: the user approved removing node gamma"
        proc = self.gate(repo, *finish, "--accept-block-change", reason)
        self.expect_rc(proc, 0)
        self.check(self.message(repo).endswith(f"\n{BLOCK_TRAILER}{reason}\n"), "no block trailer")
        record = f'\n\n{CHANGED_KEYS_HEADING}\n- removes_nodes: [] -> ["gamma"]\n\n'
        self.check(record in self.message(repo), "the commit body lacks the old and new value")
        tracked = self.git(repo, "ls-files", audited).strip() == audited
        self.check(tracked, "track_spec did not force-add the audited MiniPRD")
        ids = [
            node["id"] for node in yaml.safe_load(self.git(repo, "show", f"HEAD:{LEDGER}"))["nodes"]
        ]
        self.check(ids == ["alpha", "beta"], f"HEAD's ledger has nodes {ids}")

    def restore_ledger(self) -> None:
        repo = self.need_main()
        at_head = self.git(repo, "show", f"HEAD:{LEDGER}")
        broken = "nodes: [unclosed\n"
        self.write(repo, LEDGER, broken)
        backups = self.root / "backups" / "restore"
        proc = self.gate(repo, "restore-ledger", backups=backups)
        self.expect_rc(proc, 0)
        restored = (repo / LEDGER).read_text(encoding="utf-8")
        self.check(restored == at_head, "the ledger does not equal HEAD's copy")
        saved = sorted(backups.glob("restore-*/architecture.yml"))
        self.check(len(saved) == 1, f"expected one saved ledger, found {len(saved)}")
        self.check(saved[0].read_text(encoding="utf-8") == broken, "the saved copy differs")
        self.check(self.git(repo, "status", "--porcelain") == "", "restore-ledger left changes")
        bare = self.root / "repos" / "no-ledger"
        self.init_repo(bare, {"README.md": "# no ledger here\n"})
        proc = self.gate(bare, "restore-ledger", backups=self.root / "backups" / "no-ledger")
        self.expect_rc(proc, 1)
        self.expect_output(proc, f"HEAD has no {LEDGER}")

    def status(self) -> None:
        repo = self.need_main()
        before = self.snapshot(repo)
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        for needle in (
            f"branch: {GATE_BRANCH}",
            "completed slices (3): S00 (",
            "S01a (",
            "S01b (",
            "waivers (3):",
            f"{SCOPE_TRAILER}selftest: docs edit accepted on purpose",
            "accepted path: docs/other.md",
            f"{REFINISH_TRAILER}selftest: the user approved a follow-up fix to S00",
            f"{BLOCK_TRAILER}selftest: the user approved removing node gamma",
            "changed key: removes_nodes",
            f"({POST_RESOLVE_REF})",
            "MiniPRDs remaining in spec/compiled: 0",
            "ledger: parses (2 nodes, 0 structural problem(s))",
            "guards: ok spec/compiled/SuperPRD_hyper_update.md",
        ):
            self.expect_output(proc, needle)
        self.check(proc.stderr == "", f"status warned about the gate's own history: {proc.stderr}")
        self.check(self.snapshot(repo) == before, "status changed the repository")
        refs = self.git(repo, "for-each-ref", "--format=%(refname)", REF_ROOT).split()
        wanted = [
            POST_RESOLVE_REF,
            *(f"{SLICE_REF_PREFIX}{sid}" for sid in ("S00", "S01a", "S01b")),
        ]
        self.check(sorted(wanted) == sorted(ref for ref in refs if "/superseded/" not in ref), refs)
        superseded = [ref for ref in refs if ref.startswith(f"{SUPERSEDED_REF_PREFIX}S00/")]
        self.check(len(superseded) == 1, f"the re-finished S00 commit is not kept: {refs}")

    # -- scenarios: finish on their own fixtures --------------------------------

    def finish_block_pinning(self) -> None:
        repo = self.resolved("pin")
        compiled = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
        audited = "spec/archive/MiniPRD_00_Harness_AUDITED.md"
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.expect_refused(repo, ("finish", "S00", "--miniprd", compiled), ("un-audited",))
        outside = self.root / "outside-pin" / "MiniPRD_00_Harness_AUDITED.md"
        outside.parent.mkdir(parents=True)
        shutil.copyfile(repo / compiled, outside)
        for arg in (str(outside), audited):
            if arg == audited:
                (repo / audited).symlink_to(outside)
            self.expect_refused(repo, ("finish", "S00", "--miniprd", arg), ("outside the repo",))
        (repo / audited).unlink()
        wrong_name = "the name must be MiniPRD_00_<Slug>_AUDITED.md"
        bare = self.archive(repo, _SELFTEST_HARNESS, f"spec/archive/{_SELFTEST_HARNESS}")
        self.expect_refused(repo, ("finish", "S00", "--miniprd", bare), (wrong_name,))
        other_nn = "spec/archive/MiniPRD_01_Harness_AUDITED.md"
        os.replace(repo / bare, repo / other_nn)
        self.expect_refused(repo, ("finish", "S00", "--miniprd", other_nn), (wrong_name,))
        os.replace(repo / other_nn, repo / audited)
        real = repo / "spec/archive/real-copy" / "MiniPRD_00_Harness_AUDITED.md"
        real.parent.mkdir()
        os.replace(repo / audited, real)
        (repo / audited).symlink_to(Path(real.parent.name) / real.name)
        link = f"--miniprd {audited} is a symlink; finish reads only a regular file"
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), (link,))
        (repo / audited).unlink()
        os.replace(real, repo / audited)
        real.parent.rmdir()
        wide = ["src/**", "dev/tests/**", "docs/**"]
        self.rewrite_block(repo, audited, _SELFTEST_HARNESS, files=wide)
        self.write(repo, "docs/other.md", "# Other\n\nEdited by the S00 agent.\n")
        self.expect_refused(
            repo,
            ("finish", "S00", "--miniprd", audited),
            ("differs from the block pinned in the post-resolve commit", "(key(s): files)"),
        )
        reason = "selftest: the user approved docs/** for S00"
        proc = self.gate(
            repo, "finish", "S00", "--miniprd", audited, "--accept-block-change", reason
        )
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"waiver applied: {BLOCK_TRAILER}{reason}")
        wanted = f"\n\n{SLICE_TRAILER}S00\n{BLOCK_TRAILER}{reason}\n"
        self.check(self.message(repo).endswith(wanted), "the HACF-Block-Changed trailer is missing")
        record = '- files: ["src/**", "dev/tests/**"] -> ["src/**", "dev/tests/**", "docs/**"]'
        self.check(f"\n{record}\n" in self.message(repo), "the body lacks the old and new files")
        self.check("docs/other.md" in self.committed(repo), "the changed block was not used")
        # The next finish re-diffs the S00 commit: the recorded new files value allows docs/.
        self.write(repo, "src/legacy.py", "OLD = False\n")
        migrated = "spec/archive/20261022_000000_v060_migrated/MiniPRD_02_Ledger-Migrate.md"
        self.archive(repo, _SELFTEST_MIGRATE, migrated)
        order = ("--out-of-order", "selftest: the user approved S01b before S01a")
        proc = self.gate(repo, "finish", "S01b", "--miniprd", migrated, *order)
        self.expect_rc(proc, 0)
        self.check(migrated in self.committed(repo), "the final slice's moved MiniPRD is missing")

    def finish_guards(self) -> None:
        repo = self.resolved("guards")
        guarded = "spec/compiled/SuperPRD_hyper_update.md"
        moved = "spec/archive/legacy/SuperPRD_hyper_update.md"
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited, "--accept-scope", "selftest: guard paths")
        content = (repo / guarded).read_bytes()
        (repo / guarded).write_bytes(b"overwritten\n")
        self.expect_refused(repo, finish, (f"guard {guarded}: mismatch",))
        (repo / guarded).unlink()
        self.expect_refused(repo, finish, (f"guard {guarded}: missing, and no file under",))
        (repo / guarded).write_bytes(content)
        pinned = (repo / GUARDS).read_bytes()
        (repo / GUARDS).write_bytes(pinned + b"\n")
        self.expect_refused(repo, finish, (f"{GUARDS} differs from its copy in the post-resolve",))
        (repo / GUARDS).write_bytes(pinned)
        (repo / moved).parent.mkdir(parents=True)
        os.replace(repo / guarded, repo / moved)
        proc = self.gate(repo, *finish)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"guard {guarded}: moved byte-for-byte to {moved}")

    def finish_exemptions(self) -> None:
        def list_active(repo: Path) -> None:
            files = [*_selftest_blocks()[_SELFTEST_CORE]["files"], ACTIVE]
            self.rewrite_block(
                repo, f"{COMPILED_DIR}/{_SELFTEST_CORE}", _SELFTEST_CORE, files=files
            )

        repo = self.resolved("exempt", list_active)
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        other = f"{COMPILED_DIR}/{_SELFTEST_MIGRATE}"
        draft = "spec/archive/20261021_000000_v0_6_0/Draft_PRD.md"
        originals = {rel: (repo / rel).read_text(encoding="utf-8") for rel in (other, draft)}
        originals[SUPERPRD_V060] = (repo / SUPERPRD_V060).read_text(encoding="utf-8")
        for rel, text in originals.items():
            self.write(repo, rel, text + "\nEdited outside the S00 scope.\n")
        active = (repo / ACTIVE).read_text(encoding="utf-8")
        heading = "### Completed v0.6.0 slices"
        self.write(repo, ACTIVE, active.replace(heading, "### Completed slices (edited)"))
        self.expect_refused(
            repo,
            ("finish", "S00", "--miniprd", audited),
            ("4 changed path(s) match no files glob", other, draft, SUPERPRD_V060, PROCEDURE_SCOPE),
        )
        for rel, text in originals.items():
            self.write(repo, rel, text)
        notes = "\n## Field Notes (legacy)\n\n- S00: a note outside the procedure section.\n"
        self.write(repo, ACTIVE, active + notes)
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited)
        self.expect_rc(proc, 0)
        self.expect_output(proc, "every changed path is in scope")
        committed = self.committed(repo)
        for rel in (ACTIVE, audited, f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"):
            self.check(rel in committed, f"{rel} is not in the S00 commit")
        active = (repo / ACTIVE).read_text(encoding="utf-8")
        self.write(repo, ACTIVE, active.replace(heading, f"{heading} (edited by S01a)"))
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        proc = self.gate(repo, "finish", "S01a", "--miniprd", core)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"allowed because files lists {ACTIVE}")

    def finish_in_process(
        self, repo: Path, sid: str, miniprd: str, name: str, wrapper: Callable[..., Any]
    ) -> tuple[int | None, str, str]:
        """Run cmd_finish here with the module function name replaced by wrapper.

        Returns (its exit code, or None when it raised GateError; the GateError text; the
        captured output).
        """
        waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
        code: int | None = None
        error = ""
        backups = self.root / "backups" / repo.name
        with self.in_process(backups) as output, self.patched(name, wrapper):
            try:
                code = cmd_finish(repo, sid, miniprd, waivers, False)
            except GateError as exc:
                error = str(exc)
        return code, error, output.getvalue()

    def finish_commit_rollback(self) -> None:
        repo = self.resolved("rollback")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        head = self.head(repo)
        active = (repo / ACTIVE).read_bytes()

        def refuse(*_args: Any) -> str:
            raise GateError("selftest: git commit refuses")

        code, error, output = self.finish_in_process(repo, "S00", audited, "_commit_staged", refuse)
        self.check(code is None and "selftest: git commit refuses" in error, repr((code, error)))
        self.check(f"nothing was committed; restored {ACTIVE} and the index" in output, output)
        self.check(self.head(repo) == head, "the failed commit moved HEAD")
        self.check((repo / ACTIVE).read_bytes() == active, f"{ACTIVE} was not rolled back")
        self.check(self.git(repo, "diff", "--cached", "--name-only") == "", "the index is staged")
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)
        self.check(self.completed_count(repo, "S00") == 1, "the retry did not list S00 once")

    def finish_empty_slice(self) -> None:
        repo = self.resolved("empty")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        ledger = repo / LEDGER
        ledger.write_text(ledger.read_text(encoding="utf-8") + "# S00 only\n", encoding="utf-8")
        finish = ("finish", "S00", "--miniprd", audited)
        self.expect_refused(repo, finish, ("only bookkeeping changed", "--allow-empty REASON"))
        reason = "selftest: the user approved an S00 with no code change"
        proc = self.gate(repo, *finish, "--allow-empty", reason)
        self.expect_rc(proc, 0)
        self.check(self.message(repo).endswith(f"\n{EMPTY_TRAILER}{reason}\n"), "no empty trailer")

    def finish_manual_commit(self) -> None:
        repo = self.resolved("manual")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)
        self.write(repo, LEDGER, _selftest_ledger(drop_node="gamma"))
        self.git(repo, "commit", "-q", "-am", "manual commit between slices: drop gamma")
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        self.expect_refused(
            repo,
            ("finish", "S01a", "--miniprd", core),
            (
                "node gamma was removed but is not listed in removes_nodes",
                "1 commit(s) since the scope base have no HACF-Slice trailer",
            ),
        )

    # -- scenarios: refs, check.sh side effects, interrupts, hooks, file types ----

    def widen(self, repo: Path) -> None:
        """Widen the compiled S00 block to docs/** (the edit a re-pin would smuggle in)."""
        wide = ["src/**", "dev/tests/**", "docs/**"]
        self.rewrite_block(
            repo, f"{COMPILED_DIR}/{_SELFTEST_HARNESS}", _SELFTEST_HARNESS, files=wide
        )

    def post_resolve_repin(self) -> None:
        repo = self.resolved("repin")
        backups = self.root / "backups" / "repin"
        first = self.head(repo)
        self.check(self.ref(repo, POST_RESOLVE_REF) == first, "post-resolve recorded no ref")
        self.widen(repo)
        resolve = ("post-resolve", "--expect", "3")
        refusal = ("post-resolve already ran", f"{REPIN_FLAG} REASON")
        self.expect_refused(repo, resolve, refusal, backups)
        reason = "selftest: the user approved re-pinning the S00 block"
        proc = self.gate(repo, *resolve, REPIN_FLAG, reason, backups=backups)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"waiver applied: {REPIN_TRAILER}{reason}")
        trailer = f"\n\n{REPIN_TRAILER}{reason}\n"
        self.check(self.message(repo).endswith(trailer), "the re-pin has no HACF-Repin trailer")
        second = self.head(repo)
        moved = second != first and self.ref(repo, POST_RESOLVE_REF) == second
        self.check(moved, "the post-resolve ref does not name the re-pin commit")
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"{second[:12]} {REPIN_TRAILER}{reason}")
        self.check(proc.stderr == "", f"status warned after an approved re-pin: {proc.stderr}")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.write(repo, "docs/other.md", "# Other\n\nEdited under the re-pinned S00 block.\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)
        wanted = f"v0.6.0 S00: Test harness\n\n{_SELFTEST_TOOL}\n\nHACF-Slice: S00\n"
        self.check(self.message(repo) == wanted, "the re-pinned block still needed a waiver")

    def finish_amended_pin(self) -> None:
        repo = self.resolved("amend")
        self.widen(repo)
        self.git(repo, "add", "-f", "--", f"{COMPILED_DIR}/{_SELFTEST_HARNESS}")
        self.git(repo, "commit", "-q", "--amend", "--no-edit")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.write(repo, "docs/other.md", "# Other\n\nEdited under the amended block.\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        needles = ("which is not an ancestor of HEAD", f"is not the one {POST_RESOLVE_REF} names")
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), needles)
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        self.expect_output(proc, "warning: post-resolve pin:")

    def finish_forged_pin(self) -> None:
        repo = self.resolved("forged-pin")
        self.widen(repo)
        self.write(repo, "docs/other.md", "# Other\n\nCommitted inside a hand-made pin.\n")
        self.git(repo, "add", "-f", "--", f"{COMPILED_DIR}/{_SELFTEST_HARNESS}", "docs/other.md")
        self.git(repo, "commit", "-q", "-m", f"{POST_RESOLVE_PREFIX} by hand")
        repin = ("post-resolve", "--expect", "3", REPIN_FLAG, "selftest: re-pin over it")
        self.expect_refused(repo, repin, ("remove it from",), self.root / "backups" / "forged-pin")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        needles = (
            f"has the post-resolve subject but no {_key(REPIN_TRAILER)} trailer",
            f"is not the one {POST_RESOLVE_REF} names",
        )
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), needles)
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        for needle in needles:
            self.expect_output(proc, needle)

    def finish_check_side_effects(self) -> None:
        def scoped(repo: Path) -> None:
            files = [*_selftest_blocks()[_SELFTEST_HARNESS]["files"], CHECK_SCRIPT]
            rel = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
            self.rewrite_block(repo, rel, _SELFTEST_HARNESS, files=files)

        repo = self.resolved("check-effects", scoped)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited)
        head = self.head(repo)
        moved = f"branch changed (refs/heads/{GATE_BRANCH} -> refs/heads/selftest-elsewhere)"
        for mode, needle in (("ledger", f"{LEDGER}: content changed"), ("branch", moved)):
            proc = self.gate(repo, *finish, extra={"V060_SELFTEST_CHECK_MODE": mode})
            self.expect_rc(proc, 1)
            for text in (f"{CHECK_SCRIPT} changed 1 item(s)", needle, "then run finish again"):
                self.expect_output(proc, text)
            committed = self.git(repo, "rev-parse", GATE_BRANCH).strip() != head
            self.check(not committed, f"check.sh mode {mode}: finish committed anyway")
            self.check(self.completed_count(repo, "S00") == 0, f"{ACTIVE} changed ({mode})")
            self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", f"slice ref ({mode})")
            self.git(repo, "checkout", "-q", GATE_BRANCH)
            self.git(repo, "checkout", "-q", "HEAD", "--", LEDGER)
        self.git(repo, "branch", "-q", "-D", "selftest-elsewhere")
        # A new in-scope file, and an unchanged tracked in-scope file that check.sh rewrites.
        for made, tracked in (("src/made-by-check.py", False), ("src/legacy.py", True)):
            proc = self.gate(repo, *finish, extra={"V060_SELFTEST_CHECK_TOUCH": made})
            self.expect_rc(proc, 1)
            wanted = ("1 in-scope path(s) changed while the checks ran", made, "finish again")
            for text in wanted:
                self.expect_output(proc, text)
            committed = self.git(repo, "rev-parse", GATE_BRANCH).strip() != head
            self.check(not committed, f"check.sh wrote {made}: finish committed anyway")
            self.check(self.completed_count(repo, "S00") == 0, f"{ACTIVE} changed ({made})")
            self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", f"slice ref ({made})")
            if tracked:
                self.git(repo, "checkout", "-q", "HEAD", "--", made)
            else:
                (repo / made).unlink()
        # check.sh stages a file, sets an index flag, flips a mode or commits: each is drift.
        app = repo / "src/app.py"
        app_mode = app.stat().st_mode
        undo: dict[str, Callable[[], object]] = {
            "stage": lambda: self.git(repo, "reset", "-q"),
            "assume": lambda: self.git(repo, "update-index", "--no-assume-unchanged", "src/app.py"),
            "chmod": lambda: app.chmod(app_mode),
            "commit": lambda: self.git(repo, "reset", "-q", "--soft", "HEAD^"),
        }
        for mode_name, needle in (
            ("stage", "index changed"),
            ("assume", "index changed"),
            ("chmod", "src/app.py: mode changed"),
            ("commit", "HEAD moved"),
        ):
            proc = self.gate(repo, *finish, extra={"V060_SELFTEST_CHECK_MODE": mode_name})
            self.expect_rc(proc, 1)
            for text in (f"{CHECK_SCRIPT} changed 1 item(s)", needle):
                self.expect_output(proc, text)
            self.check(self.completed_count(repo, "S00") == 0, f"{ACTIVE} changed ({mode_name})")
            self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", f"slice ref ({mode_name})")
            undo[mode_name]()
            self.check(self.head(repo) == head, f"check.sh mode {mode_name} left a commit")
        # check.sh turns a staged empty file into an intent-to-add entry, which git ls-files
        # shows as before: only the fingerprint's list of intent-to-add entries sees it.
        self.write(repo, "src/empty.py", "")
        self.git(repo, "add", "--", "src/empty.py")
        proc = self.gate(repo, *finish, extra={"V060_SELFTEST_CHECK_MODE": "intent"})
        self.expect_rc(proc, 1)
        self.expect_output(proc, "index changed")
        self.git(repo, "rm", "-q", "--cached", "--", "src/empty.py")
        (repo / "src/empty.py").unlink()
        # check.sh hides a checked new file from git add (info/exclude): only the
        # comparison of the staged tree with the checked one notices that it is missing.
        exclude = repo / self.git(repo, "rev-parse", "--git-path", "info/exclude").strip()
        excluded = exclude.read_bytes()
        self.write(repo, "src/new.py", "NEW = 1\n")
        proc = self.gate(repo, *finish, extra={"V060_SELFTEST_CHECK_MODE": "exclude"})
        self.expect_rc(proc, 1)
        self.expect_output(proc, "staged content differs from what finish checked")
        self.expect_output(proc, "ran?): src/new.py")
        self.check(self.head(repo) == head, "finish committed a slice without src/new.py")
        self.check(self.completed_count(repo, "S00") == 0, f"{ACTIVE} was not rolled back")
        exclude.write_bytes(excluded)
        (repo / "src/new.py").unlink()
        script = (repo / CHECK_SCRIPT).read_bytes()
        stand_in = self.root / "check-effects-always-passes.sh"
        stand_in.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (repo / CHECK_SCRIPT).unlink()
        (repo / CHECK_SCRIPT).symlink_to(stand_in)
        refusal = f"{CHECK_SCRIPT} is a symlink; finish runs only a regular file"
        self.expect_refused(repo, finish, (refusal,))
        (repo / CHECK_SCRIPT).unlink()
        self.expect_refused(repo, finish, (f"{CHECK_SCRIPT} exists at the scope base",))
        (repo / CHECK_SCRIPT).write_bytes(script)
        self.expect_rc(self.gate(repo, *finish), 0)

    def finish_forged_slice(self) -> None:
        repo = self.resolved("forged-slice")
        self.write(repo, "docs/other.md", "# Other\n\nCommitted by hand, outside the gate.\n")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.git(repo, "add", "-A")
        # Trailer text outside the final paragraph, or after a U+2028 inside a line, is no
        # trailer: git splits lines at LF only.
        paragraphs = (
            "v0.6.0 S00: Test harness",
            f"{SCOPE_TRAILER}forged outside the final paragraph",
            f"Note {REFINISH_TRAILER}forged after a line separator\n{SLICE_TRAILER}S00",
        )
        self.git(repo, "commit", "-q", *(arg for text in paragraphs for arg in ("-m", text)))
        forged = f"{self.head(repo)[:12]} v0.6.0 S00: Test harness has '{SLICE_TRAILER}S00'"
        # A manual commit on top keeps the forged one from being HEAD, which finish would
        # treat as its own interrupted commit (see finish_interrupted_after_commit).
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "manual commit after the forgery")
        proc = self.gate(repo, "status")
        self.expect_rc(proc, 0)
        self.expect_output(proc, "completed slices (0): none")
        self.expect_output(proc, f"{forged} but no gate ref names it")
        self.expect_output(proc, "waivers (0): none")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 3\n")
        needles = ("1 changed path(s) match no files glob", "docs/other.md", forged)
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), needles)

    def finish_interrupt_rollback(self) -> None:
        repo = self.resolved("interrupt")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.git(repo, "rm", "-q", "--cached", "src/legacy.py")
        before = self.snapshot(repo)
        real_stage = _stage

        def stage_then_ctrl_c(*args: Any) -> list[str]:
            real_stage(*args)
            raise KeyboardInterrupt

        def stage_then_sigterm(*args: Any) -> list[str]:
            real_stage(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            raise _Fail("SIGTERM did not interrupt the commit step")

        def untrapped(signum: int, _frame: Any) -> None:
            raise _Fail(f"finish did not trap signal {signum} during its commit step")

        waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
        backups = self.root / "backups" / "interrupt"
        restored = f"nothing was committed; restored {ACTIVE} and the index"
        for wrapper, expected in (
            (stage_then_ctrl_c, KeyboardInterrupt),
            (stage_then_sigterm, _Interrupted),
        ):
            caught: BaseException | None = None
            with _signals(untrapped, ("SIGTERM",)), self.in_process(backups) as output:
                with self.patched("_stage", wrapper):
                    try:
                        cmd_finish(repo, "S00", audited, waivers, False)
                    except (KeyboardInterrupt, _Interrupted) as exc:
                        caught = exc
            self.check(isinstance(caught, expected), f"{expected.__name__} did not propagate")
            self.check(self.snapshot(repo) == before, "HEAD, the index or the work tree changed")
            kept = "src/legacy.py" not in self.git(repo, "ls-files").split()
            self.check(kept, "the index-only state (git rm --cached) was lost")
            self.check(restored in output.getvalue(), output.getvalue()[-800:])

    def finish_late_writer(self) -> None:
        repo = self.resolved("late")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        before = self.snapshot(repo)
        real_append = append_completed

        def append_then_write(*args: Any) -> tuple[str | None, str]:
            appended = real_append(*args)
            self.write(repo, "docs/late.md", "written after the last scope check\n")
            return appended

        waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
        error = ""
        backups = self.root / "backups" / "late"
        with self.in_process(backups), self.patched("append_completed", append_then_write):
            try:
                cmd_finish(repo, "S00", audited, waivers, False)
            except GateError as exc:
                error = str(exc)
        self.check("validate (written while finish ran?): docs/late.md" in error, repr(error))
        self.check(self.head(repo) == before[0], "the late file was committed")
        (repo / "docs/late.md").unlink()
        self.check(self.snapshot(repo) == before, f"{ACTIVE} or the index was not restored")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", "a slice ref was recorded")

    def finish_base_ledger_unloadable(self) -> None:
        repo = self.resolved("base-ledger", lambda repo: self.write(repo, LEDGER, "nodes: [x\n"))
        self.write(repo, LEDGER, _selftest_ledger(drop_node="gamma"))
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        needles = ("the ledger at the scope base", "without it finish cannot check")
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), needles)

    def finish_retry_after_kill(self) -> None:
        repo = self.resolved("retry")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        newline = "src/new\nline.py"
        self.write(repo, newline, "X = 1\n")
        finish = ("finish", "S00", "--miniprd", audited)
        text = (repo / ACTIVE).read_text(encoding="utf-8")
        self.write(
            repo, ACTIVE, _with_completed(text, "S01a", 1, "Ledger core", "2026-01-01") or ""
        )
        self.expect_refused(repo, finish, (PROCEDURE_SCOPE,))
        self.write(
            repo, ACTIVE, _with_completed(text, "S00", 0, "Test harness", "2026-01-01") or ""
        )
        self.git(repo, "add", "-A")
        head = self.head(repo)
        proc = self.gate(repo, *finish)
        self.expect_rc(proc, 0)
        self.expect_output(proc, f"{ACTIVE} already lists S00 (left by an interrupted finish)")
        once = self.git(repo, "rev-parse", "HEAD^").strip() == head
        self.check(once, "the retry did not commit exactly once")
        self.check(self.completed_count(repo, "S00") == 1, "the retry listed S00 twice")
        tracked = self.git(repo, "ls-files", "-z").split("\0")
        self.check(newline in tracked, "the in-scope path with a newline was not committed")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == self.head(repo), "no slice ref")

    def finish_commit_verified(self) -> None:
        # No hook runs during the gate's commit (gate_commits_skip_hooks), so another
        # process stands in: each wrapper around git commit changes what gets committed.
        repo = self.resolved("verify-commit")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited)
        slice_ref = f"{SLICE_REF_PREFIX}S00"
        real_commit = _commit_staged

        def stage_extra(target: Path, message: str) -> str:
            self.write(target, "docs/hooked.md", "staged by another process\n")
            self.git(target, "add", "docs/hooked.md")
            return real_commit(target, message)

        def rewrite_message(target: Path, _message: str) -> str:
            return real_commit(target, "v0.6.0 S00: Test harness\n\nHACF-Slice: S01b\n")

        status = None
        for wrapper, needles in (
            (stage_extra, ("did not stage (another process?): docs/hooked.md",)),
            (
                rewrite_message,
                (
                    "its message differs",
                    "added: HACF-Slice: S01b",
                    f"removed: {_SELFTEST_TOOL} | HACF-Slice: S00",
                ),
            ),
        ):
            head = self.head(repo)
            code, error, output = self.finish_in_process(
                repo, "S00", audited, "_commit_staged", wrapper
            )
            self.check(code == 1 and not error, repr((code, error)))
            for needle in (*needles, "no slice ref was recorded", "git reset --soft HEAD^"):
                self.check(needle in output, f"{wrapper.__name__}: no {needle!r}: {output}")
            self.check(self.ref(repo, slice_ref) == "", f"the {wrapper.__name__} commit got a ref")
            self.check(self.git(repo, "rev-parse", "HEAD^").strip() == head, "unexpected history")
            status = self.gate(repo, "status")
            self.expect_output(status, "completed slices (0): none")
            self.git(repo, "reset", "-q", "--soft", "HEAD^")
            if wrapper is stage_extra:
                self.git(repo, "rm", "-q", "-f", "--", "docs/hooked.md")
        if status is not None:
            self.expect_output(status, "is a finish of S01b that did not record its slice ref")
        # Restaging a staged path with other content keeps the path list: only the tree
        # check notices. Then unstaging a path, and moving HEAD to another branch.
        self.write(repo, LEDGER, _selftest_ledger() + "# S00 touched the ledger\n")
        dropped = _selftest_ledger(drop_node="gamma")

        def restage_ledger(target: Path, message: str) -> str:
            self.write(target, LEDGER, dropped)
            self.git(target, "add", LEDGER)
            return real_commit(target, message)

        def unstage(target: Path, message: str) -> str:
            self.git(target, "reset", "-q", "--", "src/app.py")
            return real_commit(target, message)

        def move_branch(target: Path, message: str) -> str:
            sha = real_commit(target, message)
            self.git(target, "checkout", "-q", "-b", "selftest-moved")
            return sha

        reset = "to undo it, run git reset --soft HEAD^"
        for wrapper, needle, advice in (
            (restage_ledger, "is not the tree finish staged and checked", reset),
            (unstage, "it lacks path(s) that finish staged: src/app.py", reset),
            (
                move_branch,
                f"HEAD is on branch selftest-moved, not {GATE_BRANCH}",
                f"which is not the gate's commit on {GATE_BRANCH}",
            ),
        ):
            head = self.head(repo)
            code, error, output = self.finish_in_process(
                repo, "S00", audited, "_commit_staged", wrapper
            )
            self.check(code == 1 and not error, repr((code, error)))
            for text in (needle, advice, "no slice ref was recorded"):
                self.check(text in output, f"{wrapper.__name__}: no {text!r}: {output}")
            if advice != reset:
                self.check(reset not in output, "finish advised a reset on another branch")
            self.check(self.ref(repo, slice_ref) == "", f"the {wrapper.__name__} commit got a ref")
            self.check(self.git(repo, "rev-parse", "HEAD^").strip() == head, "unexpected history")
            self.git(repo, "checkout", "-q", GATE_BRANCH)
            self.git(repo, "reset", "-q", "--soft", "HEAD^")
            self.git(repo, "checkout", "-q", "HEAD", "--", LEDGER)
        self.git(repo, "branch", "-q", "-D", "selftest-moved")
        self.expect_rc(self.gate(repo, *finish), 0)
        self.check(self.ref(repo, slice_ref) == self.head(repo), "no slice ref after the retry")
        self.check(self.completed_count(repo, "S00") == 1, "S00 is not listed exactly once")

    def finish_symlinks(self) -> None:
        repo = self.resolved("symlinks")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        guarded = "spec/compiled/SuperPRD_hyper_update.md"
        finish = ("finish", "S00", "--miniprd", audited, "--accept-scope", "selftest: typechanges")
        for rel, target, needle in (
            (guarded, repo / ".git" / "guard-copy.md", f"guard {guarded}: invalid: is a symlink"),
            (LEDGER, repo / ".git" / "ledger-copy.yml", f"{LEDGER} is a symlink"),
            (ACTIVE, self.root / "outside-activeContext.md", f"{ACTIVE} is a symlink"),
        ):
            path = repo / rel
            original = path.read_bytes()
            target.write_bytes(original)
            path.unlink()
            path.symlink_to(target)
            self.expect_refused(repo, finish, (needle,))
            self.check(target.read_bytes() == original, f"finish wrote through the {rel} symlink")
            path.unlink()
            path.write_bytes(original)
        memory, moved = repo / ".agents" / "memory", repo / ".agents" / "memory-real"
        os.replace(memory, moved)
        memory.symlink_to(moved.name)
        self.expect_refused(repo, finish, (f"{ACTIVE} is reached through a symlinked directory",))
        memory.unlink()
        os.replace(moved, memory)
        self.expect_rc(self.gate(repo, *finish[:4]), 0)

    def finish_duplicate_keys(self) -> None:
        harness = _selftest_blocks()[_SELFTEST_HARNESS]
        twice = "files: [src/**, dev/tests/**, docs/**]\n"
        self.refused_post_resolve(
            "dup-block",
            lambda repo: self.write(
                repo, f"{COMPILED_DIR}/{_SELFTEST_HARNESS}", _selftest_miniprd(harness, twice)
            ),
            ("found duplicate key 'files'",),
        )
        merged = {key: value for key, value in harness.items() if key != "audit_phase3"}
        self.refused_post_resolve(
            "merge-block",
            lambda repo: self.write(
                repo,
                f"{COMPILED_DIR}/{_SELFTEST_HARNESS}",
                _selftest_miniprd(merged, "<<: {audit_phase3: skip}\n"),
            ),
            (f"found a '<<' merge key, which an {BLOCK_INFO} block may not use",),
        )
        repo = self.resolved("dup-finish")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited)
        original = (repo / audited).read_text(encoding="utf-8")
        self.write(repo, audited, _selftest_miniprd(harness, "nn: 0\n"))
        self.expect_refused(repo, finish, ("found duplicate key 'nn'",))
        self.write(repo, audited, original)
        ledger = (repo / LEDGER).read_text(encoding="utf-8")
        self.write(repo, LEDGER, ledger + "nodes: []\n")
        needles = (f"{LEDGER} does not parse", "found duplicate key 'nodes'")
        self.expect_refused(repo, finish, needles)
        merges = "- id: alpha\n  <<: {note: first}\n  <<: {note: second}\n"
        self.write(repo, LEDGER, ledger.replace("- id: alpha\n", merges, 1))
        needles = (f"{LEDGER} does not parse", "found duplicate key '<<'")
        self.expect_refused(repo, finish, needles)
        self.write(repo, LEDGER, ledger)
        self.expect_rc(self.gate(repo, *finish), 0)

    def post_resolve_forged_titles(self) -> None:
        titles = {
            _SELFTEST_HARNESS: "Test harness HACF-Slice: S01b",
            _SELFTEST_CORE: "Test harness\x85HACF-Scope-Accepted: forged waiver",
            _SELFTEST_MIGRATE: "Test\x1eharness",
        }

        def mutate(repo: Path) -> None:
            for name, title in titles.items():
                self.rewrite_block(repo, f"{COMPILED_DIR}/{name}", name, title=title)

        bad = "title contains a control, format or separator character"
        needles = tuple(f"{bad} ({code})" for code in ("U+2028", "U+0085", "U+001E"))
        self.refused_post_resolve("titles", mutate, needles)
        for title, wanted in (
            ("Harness HACF-Slice: S01b", "must not contain 'HACF-'"),
            ("-- .", "has no letter or digit"),
            ("Test\tharness", "(U+0009)"),
        ):
            problem = _title_problem(title) or ""
            self.check(wanted in problem, f"title {title!r}: {problem!r}")
        self.check(_title_problem("Test harness (é, 2)") is None, "a plain title was refused")

    def finish_identity_keys(self) -> None:
        repo = self.resolved("identity")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        target = "spec/archive/MiniPRD_02_Harness_AUDITED.md"
        self.archive(repo, _SELFTEST_HARNESS, target)
        self.rewrite_block(repo, target, _SELFTEST_HARNESS, nn=2)
        reason = "selftest: the user approved a files change"
        waived = ("--accept-block-change", reason, "--accept-scope", reason)
        needles = ("changes nn 0 -> 2", "no flag waives a change to slice or nn")
        self.expect_refused(repo, ("finish", "S00", "--miniprd", target, *waived), needles)

    def finish_copy_not_move(self) -> None:
        repo = self.resolved("copy")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        compiled = repo / COMPILED_DIR / _SELFTEST_HARNESS
        audited = "spec/archive/MiniPRD_00_Harness_AUDITED.md"
        shutil.copyfile(compiled, repo / audited)
        finish = ("finish", "S00", "--miniprd", audited)
        self.expect_refused(repo, finish, (f"{COMPILED_DIR}/{_SELFTEST_HARNESS} still exists",))
        compiled.unlink()
        self.expect_rc(self.gate(repo, *finish), 0)
        # A slice ref that names a commit without that SID's trailer does not count.
        self.git(repo, "update-ref", f"{SLICE_REF_PREFIX}S01a", "HEAD")
        proc = self.gate(repo, "status")
        self.expect_output(proc, "completed slices (1): S00 (")
        self.expect_output(proc, f"which has no '{SLICE_TRAILER}S01a' trailer; S01a does not count")

    def post_resolve_guards_mode(self) -> None:
        self.refused_post_resolve(
            "guards-mode",
            lambda repo: (repo / GUARDS).chmod(0o755),
            (f"{GUARDS} differs from HEAD's copy (content or mode)",),
        )

    def post_resolve_staging_restricted(self) -> None:
        repo = self.fixture("staging")
        before = (self.head(repo), self.git(repo, "ls-files", "-s"))
        real_backup = _post_resolve_backup

        def backup_then_strays(target: Path, root: Path) -> Path:
            made = real_backup(target, root)
            self.write(repo, "stray-untracked.txt", "appeared after validation\n")
            self.write(repo, "stray-staged.txt", "staged after validation\n")
            self.git(repo, "add", "--", "stray-staged.txt")
            return made

        error = ""
        backups = self.root / "backups" / "staging"
        with self.in_process(backups), self.patched("_post_resolve_backup", backup_then_strays):
            try:
                cmd_post_resolve(repo, 3)
            except GateError as exc:
                error = str(exc)
        named = "staged path(s) that post-resolve never commits: stray-staged.txt"
        self.check(named in error, f"no GateError naming the staged stray: {error!r}")
        self.check("the index was restored" in error, error)
        self.check((self.head(repo), self.git(repo, "ls-files", "-s")) == before, "HEAD or index")
        self.check("stray-untracked.txt" not in self.git(repo, "ls-files"), "stray committed")

    # -- scenarios: committed content, interrupted commits, lost refs, rewrites ---

    def finish_index_flags(self) -> None:
        repo = self.resolved("index-flags")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        ledger = (repo / LEDGER).read_text(encoding="utf-8")
        active = (repo / ACTIVE).read_text(encoding="utf-8")
        # The index holds a ledger without node gamma behind assume-unchanged, and an
        # edited procedure section behind skip-worktree; the work tree looks clean.
        self.write(repo, LEDGER, _selftest_ledger(drop_node="gamma"))
        self.git(repo, "add", "--", LEDGER)
        self.git(repo, "update-index", "--assume-unchanged", LEDGER)
        self.write(repo, LEDGER, ledger)
        heading = f"{PROCEDURE_HEADING}\n\nAgents may skip the gate."
        self.write(repo, ACTIVE, active.replace(PROCEDURE_HEADING, heading))
        self.git(repo, "add", "--", ACTIVE)
        self.git(repo, "update-index", "--skip-worktree", ACTIVE)
        self.write(repo, ACTIVE, active)
        self.write(repo, "src/ita.py", "ITA = 1\n")
        self.git(repo, "add", "-N", "--", "src/ita.py")
        # Intent-to-add entries for a path HEAD has ('DA'; 'DD' once the file is gone) and
        # for a new path whose file is gone ('.D').
        for rel in ("docs/other.md", "src/legacy.py"):
            self.git(repo, "rm", "-q", "--cached", "--", rel)
            self.git(repo, "add", "-N", "--", rel)
        (repo / "src/legacy.py").unlink()
        self.write(repo, "src/gone.py", "GONE = 1\n")
        self.git(repo, "add", "-N", "--", "src/gone.py")
        (repo / "src/gone.py").unlink()
        reason = "selftest: the user approved every waiver"
        finish = ("finish", "S00", "--miniprd", audited)
        before = self.snapshot(repo)
        proc = self.gate(repo, *finish, "--accept-scope", reason, "--allow-empty", reason)
        self.expect_rc(proc, 1)
        for needle in (
            f"{LEDGER} is marked assume-unchanged in the index (clear it: git update-index "
            f"--no-assume-unchanged -- {LEDGER};",
            f"{ACTIVE} is marked skip-worktree in the index (clear it: git update-index "
            f"--no-skip-worktree -- {ACTIVE};",
            "src/ita.py is an intent-to-add entry (git add -N) in the index (stage it: git add "
            "-- src/ita.py, or drop it: git rm --cached -- src/ita.py;",
            "docs/other.md is an intent-to-add entry",
            "src/legacy.py is an intent-to-add entry",
            "src/gone.py is an intent-to-add entry",
        ):
            self.expect_output(proc, needle)
        self.check("ledger parses" not in proc.stdout, "finish ran its checks before refusing")
        self.check(self.snapshot(repo) == before, "the refused finish changed the repository")
        self.git(repo, "update-index", "--no-assume-unchanged", LEDGER)
        self.git(repo, "update-index", "--no-skip-worktree", ACTIVE)
        self.git(repo, "rm", "-q", "--cached", "--", "src/ita.py", "src/gone.py")
        self.write(repo, "src/legacy.py", "OLD = True\n")
        self.git(repo, "add", "--", "docs/other.md", "src/legacy.py")
        self.expect_rc(self.gate(repo, *finish), 0)
        ids = [
            node["id"] for node in yaml.safe_load(self.git(repo, "show", f"HEAD:{LEDGER}"))["nodes"]
        ]
        self.check(ids == ["alpha", "beta", "gamma"], f"HEAD's ledger has nodes {ids}")
        committed = self.git(repo, "show", f"HEAD:{ACTIVE}")
        self.check("Agents may skip the gate." not in committed, "the hidden section edit landed")

        def flagged(repo: Path) -> None:
            self.git(repo, "update-index", "--assume-unchanged", "src/app.py")
            self.write(repo, "docs/ita.md", "intent to add\n")
            self.git(repo, "add", "-N", "--", "docs/ita.md")

        needles = ("src/app.py is marked assume-unchanged", "docs/ita.md is an intent-to-add entry")
        self.refused_post_resolve("resolve-flags", flagged, needles)

    def finish_staged_content(self) -> None:
        repo = self.resolved("staged-content")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        before = self.snapshot(repo)
        active = (repo / ACTIVE).read_bytes()
        real_append, real_check = append_completed, _run_check_script
        note = "\n## Field Notes (legacy)\n\n- written after the checks ran\n"

        def append_then_rewrite(*args: Any) -> tuple[str | None, str]:
            appended = real_append(*args)
            self.write(repo, "src/app.py", "VALUE = 3\n")
            return appended

        def append_then_note(*args: Any) -> tuple[str | None, str]:
            appended = real_append(*args)
            with (repo / ACTIVE).open("a", encoding="utf-8") as handle:
                handle.write(note)
            return appended

        def check_then_note(*args: Any) -> _Checked | None:
            checked = real_check(*args)
            with (repo / ACTIVE).open("a", encoding="utf-8") as handle:
                handle.write(note)
            return checked

        waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
        backups = self.root / "backups" / "staged-content"
        for name, wrapper, needle in (
            ("append_completed", append_then_rewrite, "ran?): src/app.py"),
            ("append_completed", append_then_note, f"ran?): {ACTIVE}"),
            ("_run_check_script", check_then_note, f"{ACTIVE} (it changed after the checks ran)"),
        ):
            error = ""
            with self.in_process(backups), self.patched(name, wrapper):
                try:
                    cmd_finish(repo, "S00", audited, waivers, False)
                except GateError as exc:
                    error = str(exc)
            wanted = "staged content differs from what finish checked"
            self.check(wanted in error and needle in error, repr(error))
            self.check(self.head(repo) == before[0], f"{wrapper.__name__}: finish committed")
            self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", "a slice ref was recorded")
            self.write(repo, "src/app.py", "VALUE = 2\n")
            (repo / ACTIVE).write_bytes(active)
            self.check(self.snapshot(repo) == before, f"{wrapper.__name__}: not rolled back")
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)

    def finish_valid_tree(self) -> None:
        repo = self.root / "repos" / "valid-tree"
        self.init_repo(repo, {"README.md": "# checked tree\n"})
        self.git(repo, "config", "core.trustctime", "false")
        app = repo / "app.py"
        index = repo / self.git(repo, "rev-parse", "--git-path", "index").strip()
        app.write_text("VALUE = 1\n", encoding="utf-8")
        past = app.stat().st_mtime_ns - 10 * 10**9
        os.utime(app, ns=(past, past))
        self.git(repo, "add", "--", "app.py")
        # A same-size edit with the recorded stat data looks clean to git's stat check;
        # only an index file no newer than the entry makes git read the file again.
        app.write_text("VALUE = 2\n", encoding="utf-8")
        os.utime(app, ns=(past, past))
        os.utime(index, ns=(past, past))
        backups = self.root / "backups" / "valid-tree"
        with self.in_process(backups):
            tree = _valid_tree(repo, ())
        seen = self.git(repo, "cat-file", "blob", f"{tree}:app.py")
        self.check(seen == "VALUE = 2\n", "the checked tree missed a racily clean edit")
        index.unlink()
        with self.in_process(backups):
            tree = _valid_tree(repo, ())
        names = self.git(repo, "ls-tree", "-r", "--name-only", tree).split()
        self.check(names == ["README.md", "app.py"], f"without an index the tree holds {names}")
        self.check(not index.exists(), "building the checked tree wrote the real index")

    def finish_moved_head(self) -> None:
        repo = self.resolved("moved-head")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        start = self.head(repo)
        real_commit = _commit_staged

        def commit_then_reset(target: Path, message: str) -> str:
            real_commit(target, message)
            self.git(target, "reset", "-q", "--soft", "HEAD^")
            return self.head(target)

        finish = ("finish", "S00", "--miniprd", audited)
        code, error, output = self.finish_in_process(
            repo, "S00", audited, "_commit_staged", commit_then_reset
        )
        self.check(code == 1 and not error, repr((code, error)))
        for needle in (
            "are not the commit finish started from",
            f"HEAD is back at {start[:12]}, where finish started, so do not reset it",
            "the gate's commit is only in git reflog",
        ):
            self.check(needle in output, f"no {needle!r}: {output}")
        reset = "to undo it, run git reset --soft HEAD^"
        self.check(reset not in output, "finish advised resetting HEAD")
        self.check(self.head(repo) == start, "the wrapper did not move HEAD back")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", "a slice ref was recorded")
        self.expect_rc(self.gate(repo, *finish), 0)
        once = self.git(repo, "rev-parse", "HEAD^").strip() == start
        self.check(once and self.completed_count(repo, "S00") == 1, "the retry did not commit once")
        # A commit made between the checks and the gate's commit: HEAD is the gate's
        # commit, but HEAD^ is not where finish started, so there is no reset advice.
        real_problems = _staged_content_problems

        def commit_then_compare(*args: Any) -> list[str]:
            tree = self.git(repo, "rev-parse", "HEAD^{tree}").strip()
            extra = self.git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "selftest: between")
            self.git(repo, "update-ref", "HEAD", extra.strip())
            return real_problems(*args)

        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        start = self.head(repo)
        waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
        with self.in_process(self.root / "backups" / "moved-head") as output:
            with self.patched("_staged_content_problems", commit_then_compare):
                code = cmd_finish(repo, "S01a", core, waivers, False)
        between, text = self.git(repo, "rev-parse", "HEAD^").strip(), output.getvalue()
        self.check(code == 1 and f"its parent(s) ({between[:12]})" in text, text[-800:])
        advice = f"which is not the gate's commit on {GATE_BRANCH} on top of {start[:12]}"
        self.check(advice in text and "git reset --soft" not in text, text[-800:])

    def finish_interrupted_after_commit(self) -> None:
        repo = self.resolved("after-commit")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        start = self.head(repo)
        real_verify, real_update = _verify_commit, _update_refs

        def verify_then_sigterm(*args: Any) -> list[str]:
            problems = real_verify(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return problems

        def update_then_sigterm(*args: Any) -> str | None:
            error = real_update(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return error

        def untrapped(signum: int, _frame: Any) -> None:
            raise _Fail(f"finish did not trap signal {signum} after its commit")

        def interrupted(name: str, wrapper: Callable[..., Any], sid: str, miniprd: str) -> str:
            """Run finish in this process with name wrapped; return its output."""
            waivers: dict[str, str | None] = {flag: None for flag, _ in WAIVER_FLAGS}
            caught: BaseException | None = None
            backups = self.root / "backups" / "after-commit"
            with _signals(untrapped, ("SIGTERM",)), self.in_process(backups) as output:
                with self.patched(name, wrapper):
                    try:
                        cmd_finish(repo, sid, miniprd, waivers, False)
                    except _Interrupted as exc:
                        caught = exc
            self.check(isinstance(caught, _Interrupted), f"{name}: SIGTERM did not propagate")
            return output.getvalue()

        # Interrupted after the commit, before the slice ref: the next finish refuses with
        # the advice, and after git reset --soft HEAD^ it commits once.
        output = interrupted("_verify_commit", verify_then_sigterm, "S00", audited)
        advice = _interrupted_advice(self.head(repo), "S00")
        self.check(advice in output, output[-800:])
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == "", "a slice ref was recorded")
        finish = ("finish", "S00", "--miniprd", audited)
        self.expect_refused(repo, finish, (advice,))
        self.expect_output(self.gate(repo, "status"), f"warning: {advice}")
        # finish of another SID warns with the same advice (its scope check then sees
        # the changes of that commit).
        core = self.archive(repo, _SELFTEST_CORE)
        self.expect_refused(repo, ("finish", "S01a", "--miniprd", core), (f"S01a: {advice}",))
        os.replace(repo / core, repo / COMPILED_DIR / _SELFTEST_CORE)
        self.git(repo, "reset", "-q", "--soft", "HEAD^")
        self.expect_rc(self.gate(repo, *finish), 0)
        once = self.git(repo, "rev-parse", "HEAD^").strip() == start
        self.check(once and self.completed_count(repo, "S00") == 1, "the retry did not commit once")
        # Interrupted after the slice ref was recorded: the slice is finished.
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        output = interrupted("_update_refs", update_then_sigterm, "S01a", core)
        self.check(f"in {SLICE_REF_PREFIX}S01a; S01a is finished" in output, output[-800:])
        proc = self.gate(repo, "status")
        self.expect_output(proc, "completed slices (2): S00 (")
        self.check(proc.stderr == "", f"status warned after the recorded slice: {proc.stderr}")

        # Interrupted after something moved HEAD off the gate's commit: no reset advice.
        def reset_then_sigterm(*args: Any) -> list[str]:
            self.git(repo, "reset", "-q", "--soft", "HEAD^")
            return verify_then_sigterm(*args)

        self.write(repo, "src/legacy.py", "OLD = False\n")
        migrated = "spec/archive/20261022_000000_v060_migrated/MiniPRD_02_Ledger-Migrate.md"
        self.archive(repo, _SELFTEST_MIGRATE, migrated)
        output = interrupted("_verify_commit", reset_then_sigterm, "S01b", migrated)
        moved = "interrupted after the commit; no slice ref was recorded; HEAD is back at"
        self.check(moved in output and "git reset --soft" not in output, output[-800:])

    def post_resolve_lost_ref(self) -> None:
        repo = self.resolved("lost-ref")
        backups = self.root / "backups" / "lost-ref"
        sha = self.head(repo)
        self.git(repo, "update-ref", "-d", POST_RESOLVE_REF)
        command = f'git update-ref {POST_RESOLVE_REF} {sha} ""'
        resolve = ("post-resolve", "--expect", "3")
        self.expect_refused(repo, resolve, ("post-resolve already ran", command), backups)
        repin = (*resolve, REPIN_FLAG, "selftest: the user approved re-recording the pin")
        self.expect_refused(repo, repin, ("nothing to commit", command), backups)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited)
        missing = f"{POST_RESOLVE_REF} is missing"
        self.expect_refused(repo, finish, (missing, command))
        proc = self.gate(repo, "status")
        for needle in (f"post-resolve pin: {missing}", command):
            self.expect_output(proc, needle)
        self.git(repo, "update-ref", POST_RESOLVE_REF, sha, "")
        self.expect_rc(self.gate(repo, *finish), 0)
        # A later post-resolve-subject commit without HACF-Repin: no recovery command.
        self.write(repo, f"{ANNEX}/slices.md", "# Slices (edited by hand)\n")
        self.git(repo, "commit", "-q", "-am", f"{POST_RESOLVE_PREFIX} by hand")
        self.git(repo, "update-ref", "-d", POST_RESOLVE_REF)
        proc = self.gate(repo, "status")
        self.expect_output(proc, f"has the post-resolve subject but no {_key(REPIN_TRAILER)}")
        self.check("record it again with" not in proc.stderr, proc.stderr)

    def post_resolve_amended_paths(self) -> None:
        repo = self.resolved("amend-paths")
        self.write(repo, "docs/other.md", "# Other\n\nChanged inside the amended post-resolve.\n")
        self.git(repo, "add", "--", "docs/other.md")
        self.git(repo, "commit", "-q", "--amend", "--no-edit")
        amended = f"{self.head(repo)[:12]} {POST_RESOLVE_PREFIX}"
        stray = "post-resolve never commits: docs/other.md"
        rel = f"{COMPILED_DIR}/{_SELFTEST_CORE}"
        self.rewrite_block(repo, rel, _SELFTEST_CORE, title="Ledger core (re-pinned)")
        resolve = ("post-resolve", "--expect", "3")
        repin = (*resolve, REPIN_FLAG, "selftest: re-pin S01a's title")
        backups = self.root / "backups" / "amend-paths"
        self.expect_refused(repo, resolve, ("post-resolve already ran", stray), backups)
        self.expect_refused(repo, repin, (amended, stray), backups)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.expect_refused(repo, ("finish", "S00", "--miniprd", audited), (amended, stray))
        self.expect_output(self.gate(repo, "status"), f"warning: post-resolve pin: {amended}")
        # Without the ref, a reshaped post-resolve commit gets no recovery command.
        self.git(repo, "update-ref", "-d", POST_RESOLVE_REF)
        proc = self.gate(repo, "status")
        self.expect_output(proc, f"warning: post-resolve pin: {amended}")
        self.check("record it again with" not in proc.stderr, proc.stderr)
        # A merge with the post-resolve subject is no commit that post-resolve makes either.
        repo = self.resolved("merge-pin")
        self.git(repo, "checkout", "-q", "-b", "side")
        self.write(repo, "docs/other.md", "# Other\n\nChanged on a side branch.\n")
        self.git(repo, "commit", "-q", "-am", "selftest: a side commit")
        self.git(repo, "checkout", "-q", GATE_BRANCH)
        merge = f"{POST_RESOLVE_PREFIX} merged by hand"
        self.git(repo, "merge", "-q", "--no-ff", "-m", merge, "side")
        proc = self.gate(repo, "status")
        self.expect_output(proc, f"{merge} has the post-resolve subject but 2 parents")

    def post_resolve_symlinks(self) -> None:
        def linked(rel: str) -> Callable[[Path], None]:
            def mutate(repo: Path) -> None:
                target = repo / ".git" / f"{Path(rel).name}.real"
                shutil.copyfile(repo / rel, target)
                (repo / rel).unlink()
                (repo / rel).symlink_to(target)

            return mutate

        core = f"{COMPILED_DIR}/{_SELFTEST_CORE}"
        only = "post-resolve commits only regular files"
        for name, rel, needle in (
            ("link-miniprd", core, f"{core} is a symlink; {only}"),
            ("link-superprd", SUPERPRD_BARE, f"{SUPERPRD_BARE} is a symlink; {only}"),
            ("link-ledger", LEDGER, f"{LEDGER} is a symlink; the gate reads and commits only"),
        ):
            self.refused_post_resolve(name, linked(rel), (needle,))

    def guards_invalid_keys(self) -> None:
        repo = self.root / "repos" / "guard-keys"
        files = _selftest_files()
        guards = json.loads(files[GUARDS])
        for key in ("spec/compiled/a\0b.md", "spec/compiled/\x1b[2Jcleared.md"):
            guards[key] = "0" * 64
        files[GUARDS] = json.dumps(guards, indent=2) + "\n"
        self.init_repo(repo, files)
        for rel, content in _selftest_resolver_output().items():
            self.write(repo, rel, content)
        backups = self.root / "backups" / "guard-keys"
        before = self.snapshot(repo)
        proc = self.gate(repo, "post-resolve", "--expect", "3", backups=backups)
        self.expect_rc(proc, 1)
        invalid = "invalid: the path has a NUL, control, format or separator character"
        for name in ("spec/compiled/a\\x00b.md", "spec/compiled/\\x1b[2Jcleared.md"):
            self.expect_output(proc, f"guard {name}: {invalid}")
        status = self.gate(repo, "status")
        self.expect_rc(status, 0)
        self.expect_output(status, "guards: invalid spec/compiled/a\\x00b.md (the path has a NUL")
        for done in (proc, status):
            self.check("Traceback" not in done.stdout + done.stderr, "a guards.json key crashed")
        self.check(self.snapshot(repo) == before and not backups.exists(), "post-resolve wrote")
        self.check(_inside_repo(repo, "spec/a\0b.md") is None, "_inside_repo accepted a NUL")

    def finish_verbatim_message(self) -> None:
        repo = self.resolved("verbatim")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.write(repo, "docs/notes ", "a doc whose name ends with a space\n")
        # git's own cleanup would strip the '- path' lines here; finish commits verbatim.
        self.git(repo, "config", "commit.cleanup", "strip")
        self.git(repo, "config", "core.commentChar", "-")
        reason = "selftest: the user approved docs/notes"
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited, "--accept-scope", reason)
        self.expect_rc(proc, 0)
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == self.head(repo), "no slice ref")
        wanted = (
            f'v0.6.0 S00: Test harness\n\n{ACCEPTED_PATHS_HEADING}\n- "docs/notes "\n\n'
            f"{_SELFTEST_TOOL}\n\n{SLICE_TRAILER}S00\n{SCOPE_TRAILER}{reason}\n"
        )
        message = self.message(repo)
        self.check(message == wanted, f"unexpected commit message {message!r}")
        self.expect_output(self.gate(repo, "status"), 'accepted path: "docs/notes "')

    def finish_replace_objects(self) -> None:
        repo = self.resolved("replace")
        post = self.head(repo)
        # A replacement for the post-resolve commit whose tree widens the S00 block.
        compiled = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
        wide = dict(
            _selftest_blocks()[_SELFTEST_HARNESS], files=["src/**", "dev/tests/**", "docs/**"]
        )
        source = self.root / "replace-wide.md"
        source.write_text(_selftest_miniprd(wide), encoding="utf-8")
        blob = self.git(repo, "hash-object", "-w", str(source)).strip()
        index = {"GIT_INDEX_FILE": str(self.root / "replace.index")}
        self.git(repo, "read-tree", post, extra=index)
        self.git(repo, "update-index", "--cacheinfo", f"100644,{blob},{compiled}", extra=index)
        tree = self.git(repo, "write-tree", extra=index).strip()
        subject = self.git(repo, "log", "-1", "--format=%s", post).strip()
        forged = self.git(repo, "commit-tree", tree, "-p", f"{post}^", "-m", subject).strip()
        self.git(repo, "replace", post, forged)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        self.write(repo, "docs/other.md", "# Other\n\nOutside the pinned S00 scope.\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, audited, source.read_text(encoding="utf-8"))
        finish = ("finish", "S00", "--miniprd", audited)
        replaced = f"{REPLACE_REFS}/ has 1 ref(s)"
        self.expect_refused(repo, finish, ("(key(s): files)", replaced))
        self.expect_output(self.gate(repo, "status"), f"warning: {replaced}")
        resolve = ("post-resolve", "--expect", "3", REPIN_FLAG, "selftest: re-pin")
        backups = self.root / "backups" / "replace"
        self.expect_refused(repo, resolve, (f"post-resolve: {replaced}",), backups)
        # Grafts rewrite history too; the gate does not run while a grafts file exists.
        self.git(repo, "replace", "-d", post)
        self.rewrite_block(repo, audited, _SELFTEST_HARNESS)
        self.git(repo, "checkout", "-q", "HEAD", "--", "docs/other.md")
        grafts = repo / self.git(repo, "rev-parse", "--git-path", GRAFTS).strip()
        grafts.write_text(f"{post} {self.git(repo, 'rev-parse', f'{post}^').strip()}\n")
        needle = f"{GRAFTS} exists; grafts change what git shows as history"
        self.expect_refused(repo, finish, (needle,))
        self.expect_refused(repo, resolve, (needle,), backups)
        self.expect_output(self.gate(repo, "status"), f"warning: .git/{needle}")
        grafts.unlink()
        self.expect_rc(self.gate(repo, *finish), 0)

    def finish_untracked_spec(self) -> None:
        def spec_scope(repo: Path) -> None:
            files = ["spec/**", "src/**", ".gitignore"]
            rel = f"{COMPILED_DIR}/{_SELFTEST_MIGRATE}"
            self.rewrite_block(repo, rel, _SELFTEST_MIGRATE, files=files, track_spec=False)

        # The final slice (S15b in v0.6.0) untracks spec/ and keeps its files on disk; its
        # pinned block has track_spec: false, which --no-track-spec requires.
        repo = self.resolved("untrack-spec", spec_scope)
        self.write(
            repo, ".gitignore", (repo / ".gitignore").read_text(encoding="utf-8") + "spec/\n"
        )
        self.git(repo, "rm", "-r", "-q", "--cached", "spec")
        migrated = "spec/archive/20261022_000000_v060_migrated/MiniPRD_02_Ledger-Migrate.md"
        self.archive(repo, _SELFTEST_MIGRATE, migrated)
        self.write(repo, "src/legacy.py", "OLD = False\n")

        def on_disk() -> list[str]:
            return sorted(path.as_posix() for path in (repo / "spec").rglob("*") if path.is_file())

        files = on_disk()
        order = ("--out-of-order", "selftest: the user approved S01b first")
        proc = self.gate(repo, "finish", "S01b", "--miniprd", migrated, "--no-track-spec", *order)
        self.expect_rc(proc, 0)
        left = self.git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "spec")
        self.check(left == "", f"the commit's tree still has spec/ paths: {left.split()}")
        deleted = [path for path in self.committed(repo) if path.startswith("spec/")]
        self.check({LEDGER, SUPERPRD_V060} <= set(deleted), f"spec/** not deleted: {deleted}")
        self.check(on_disk() == files, "the spec/ files in the work tree changed")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S01b") == self.head(repo), "no slice ref")

    def post_resolve_staged_content(self) -> None:
        compiled = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
        slices = f"{ANNEX}/slices.md"
        real_backup = _post_resolve_backup

        def backup_then_widen(target: Path, root: Path) -> Path:
            made = real_backup(target, root)
            self.widen(target)
            return made

        def backup_then_note(target: Path, root: Path) -> Path:
            made = real_backup(target, root)
            with (target / slices).open("a", encoding="utf-8") as handle:
                handle.write("\nEdited while post-resolve ran.\n")
            return made

        # A MiniPRD and an annex file that the checks parsed, each changed after the checks.
        for name, wrapper, rel in (
            ("resolve-content", backup_then_widen, compiled),
            ("resolve-annex", backup_then_note, slices),
        ):
            repo = self.fixture(name)
            before = (self.head(repo), self.git(repo, "ls-files", "-s"))
            error = ""
            with self.in_process(self.root / "backups" / name):
                with self.patched("_post_resolve_backup", wrapper):
                    try:
                        cmd_post_resolve(repo, 3)
                    except GateError as exc:
                        error = str(exc)
            wanted = f"staged content differs from what post-resolve checked: {rel}"
            self.check(wanted in error and "the index was restored" in error, repr(error))
            after = (self.head(repo), self.git(repo, "ls-files", "-s"))
            self.check(after == before, f"{name}: HEAD or the index changed")
        # core.autocrlf makes git add store a CRLF MiniPRD with LF endings; the check
        # compares with what git add makes of the parsed bytes, so post-resolve commits.
        repo = self.fixture("resolve-crlf")
        self.git(repo, "config", "core.autocrlf", "input")
        (repo / compiled).write_bytes((repo / compiled).read_bytes().replace(b"\n", b"\r\n"))
        backups = self.root / "backups" / "resolve-crlf"
        self.expect_rc(self.gate(repo, "post-resolve", "--expect", "3", backups=backups), 0)
        # Another process restages a MiniPRD during the commit: the commit's tree is not the
        # staged one (no hook can run there; gate_commits_skip_hooks).
        repo = self.fixture("resolve-restaged")
        real_commit = _commit_staged

        def restage_then_commit(target: Path, message: str) -> str:
            self.widen(target)
            self.git(target, "add", "-f", compiled)
            return real_commit(target, message)

        start = self.head(repo)
        error = ""
        with self.in_process(self.root / "backups" / "resolve-restaged"):
            with self.patched("_commit_staged", restage_then_commit):
                try:
                    cmd_post_resolve(repo, 3)
                except GateError as exc:
                    error = str(exc)
        for needle in (
            "is not the tree post-resolve staged",
            f"so {POST_RESOLVE_REF} was not recorded",
            "to undo it, run git reset --soft HEAD^ (the changes stay staged), then run "
            "post-resolve again",
        ):
            self.check(needle in error, f"no {needle!r}: {error!r}")
        self.check(self.ref(repo, POST_RESOLVE_REF) == "", "the post-resolve ref was recorded")
        self.check(self.git(repo, "rev-parse", "HEAD^").strip() == start, "unexpected history")

    # -- scenarios: gate amendment C4 -----------------------------------------------

    def finish_validator_copies(self) -> None:
        # C4.1: the validators exist at the post-resolve commit, so every finish also runs
        # their scope-base copies, on the working tree and on a checkout of the scope base.
        lore = (
            "import sys\nfrom pathlib import Path\n\nfrom _lore_rules import BAD, WARN\n\n"
            "items = Path('lore/items.txt')\n"
            "lines = items.read_text().splitlines() if items.is_file() else []\n"
            "failed = False\nfor line in lines:\n"
            "    if BAD in line:\n        print(f'error: {line}')\n        failed = True\n"
            "    elif WARN in line:\n        print(f'warning: {line}')\n"
            "sys.exit(1 if failed else 0)\n"
        )
        ledger = (
            "import subprocess\nimport sys\n\n"
            "ref = sys.argv[sys.argv.index('--against-ref') + 1]\n"
            "found = subprocess.run(['git', 'cat-file', '-e', ref + '^{commit}']).returncode == 0\n"
            "print('ledger ok' if found else f'error: {ref} does not resolve here')\n"
            "sys.exit(0 if found else 1)\n"
        )
        lore_rel, ledger_rel = ".agents/scripts/hyper_lore.py", ".agents/scripts/hyper_ledger.py"
        files = {
            **_selftest_files(),
            lore_rel: lore,
            ".agents/scripts/_lore_rules.py": "BAD = 'bad'\nWARN = 'warn'\n",
            ledger_rel: ledger,
            "lore/items.txt": "an item\nwarn: a known warning\n",
        }
        repo = self.root / "repos" / "validators"
        self.init_repo(repo, files)
        for rel, content in _selftest_resolver_output().items():
            self.write(repo, rel, content)
        for name in (_SELFTEST_HARNESS, _SELFTEST_CORE):
            wide = [*_selftest_blocks()[name]["files"], ".agents/scripts/**", "lore/**"]
            self.rewrite_block(repo, f"{COMPILED_DIR}/{name}", name, files=wide)
        backups = self.root / "backups" / "validators"
        self.expect_rc(self.gate(repo, "post-resolve", "--expect", "3", backups=backups), 0)
        # S00: the copies report the known warning on both trees, so nothing is new.
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited)
        self.expect_rc(proc, 0)
        for rel in (lore_rel, ledger_rel):
            self.expect_output(proc, f"the scope-base copy of {rel} reports nothing new here")
        self.check("on the scope base's own tree" not in proc.stderr, proc.stderr)
        # S01a weakens the rule module the validator imports and adds a bad item: the
        # working-tree validator passes, but the scope-base copy (which imports the scope
        # base's rule module) reports the new error.
        self.write(repo, "lib/ledger.py", "X = 1\n")
        self.write(repo, ".agents/scripts/_lore_rules.py", "BAD = 'never'\nWARN = 'warn'\n")
        self.write(repo, "lore/items.txt", "an item\nwarn: a known warning\nbad: a new error\n")
        core = self.archive(repo, _SELFTEST_CORE)
        finish = ("finish", "S01a", "--miniprd", core)
        needles = (
            "1 validator(s) whose scope-base copy reports",
            f"{lore_rel} validate: error: bad: a new error",
            "(exit 1; exit 0 on the scope base)",
            "on the working tree (exit 1, 2 line(s))",
            "--accept-validator-change REASON",
        )
        self.expect_refused(repo, finish, needles)
        # A validator that exists at the scope base cannot be deleted to skip it.
        script = (repo / lore_rel).read_text(encoding="utf-8")
        (repo / lore_rel).unlink()
        self.expect_refused(repo, finish, (f"{lore_rel} exists at the scope base",))
        self.write(repo, lore_rel, script)
        reason = "selftest: the user approved the new lore rule"
        proc = self.gate(repo, *finish, "--accept-validator-change", reason)
        self.expect_rc(proc, 0)
        message = self.message(repo)
        record = f"\n\n{VALIDATOR_CHANGE_HEADING}\n- {lore_rel} validate\n\n"
        self.check(record in message, f"the body lacks the validator record: {message!r}")
        self.check(message.endswith(f"\n{VALIDATOR_TRAILER}{reason}\n"), "no validator trailer")
        self.expect_output(self.gate(repo, "status"), f"changed validator: {lore_rel} validate")

    def post_resolve_repin_finished(self) -> None:
        # C4.2: a re-pin keeps the block of every slice that has a slice ref.
        repo = self.resolved("repin-finished")
        post = self.head(repo)
        backups = self.root / "backups" / "repin-finished"
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)
        # A replan re-pins S01a; the audit moved S00's MiniPRD to spec/archive/.
        rel = f"{COMPILED_DIR}/{_SELFTEST_CORE}"
        self.rewrite_block(repo, rel, _SELFTEST_CORE, title="Ledger core (replanned)")
        repin = ("post-resolve", "--expect", "3", REPIN_FLAG, "selftest: the user approved it")
        harness = f"{COMPILED_DIR}/{_SELFTEST_HARNESS}"
        restore = f"git show {post[:12]}:{harness} > {harness}"
        self.expect_refused(repo, repin, ("S00 has a slice ref", restore), backups)
        pinned = self.git(repo, "show", f"{post}:{harness}")
        self.widen(repo)
        changed = "S00 has a slice ref (refs/hacf/v0.6.0/slices/S00), but its hacf-bootstrap"
        self.expect_refused(repo, repin, (changed, "changes key(s) files"), backups)
        self.write(repo, harness, pinned)
        self.expect_rc(self.gate(repo, *repin, backups=backups), 0)
        self.expect_output(self.gate(repo, "status"), "completed slices (1): S00 (")
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        self.expect_rc(self.gate(repo, "finish", "S01a", "--miniprd", core), 0)
        subject = self.message(repo).split("\n", 1)[0]
        self.check(subject == "v0.6.0 S01a: Ledger core (replanned)", repr(subject))

    def post_resolve_banner(self) -> None:
        # C4.3: the Overrides banner sits between the header lines and section 1.
        blocks = _selftest_blocks()
        pointer = "`## Pipeline\n> Operating Notes`"
        edits: dict[str, Callable[[str], str]] = {
            _SELFTEST_HARNESS: lambda text: text.replace(_SELFTEST_BANNER, ""),
            _SELFTEST_CORE: lambda text: text.replace(_SELFTEST_BANNER, "").replace(
                "Selftest fixture.\n", f"Selftest fixture.\n\n{_SELFTEST_BANNER}"
            ),
            _SELFTEST_MIGRATE: lambda text: text.replace(pointer, "the notes"),
        }

        def mutate(repo: Path) -> None:
            for name, edit in edits.items():
                self.write(repo, f"{COMPILED_DIR}/{name}", edit(_selftest_miniprd(blocks[name])))

        missing = f"no line beginning '{BANNER_PREFIX}' between"
        pointless = f"blockquote paragraph does not point to '{NOTES_POINTER}'"
        self.refused_post_resolve(
            "banner",
            mutate,
            (
                f"{COMPILED_DIR}/{_SELFTEST_HARNESS}: {missing}",
                f"{COMPILED_DIR}/{_SELFTEST_CORE}: {missing}",
                f"{COMPILED_DIR}/{_SELFTEST_MIGRATE}: the Overrides banner's {pointless}",
            ),
        )
        text = _selftest_miniprd(blocks[_SELFTEST_HARNESS])
        self.check(banner_problem(text) is None, "the fixture banner was refused")
        fenced = text.replace(_SELFTEST_BANNER, f"```text\n{_SELFTEST_BANNER}```\n")
        self.check(missing in (banner_problem(fenced) or ""), "a fenced banner counted")
        bare = "# MiniPRD\n\n## 1. The Confidence Mandate\n"
        self.check(banner_problem(bare) == f"no '{PARENT_NODE_PREFIX}' line", "no Parent Node")

    def finish_out_of_order(self) -> None:
        # C4.4: every depends_on SID needs a slice ref that counts.
        repo = self.resolved("out-of-order")
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        finish = ("finish", "S01a", "--miniprd", core)
        needles = ("without a slice ref that counts: S00", "--out-of-order REASON")
        self.expect_refused(repo, finish, needles)
        # A slice ref that names no ancestor of HEAD does not count.
        message = f"v0.6.0 S00: off the branch\n\n{SLICE_TRAILER}S00"
        tree = self.git(repo, "rev-parse", "HEAD^{tree}").strip()
        stray = self.git(repo, "commit-tree", tree, "-p", "HEAD", "-m", message).strip()
        self.git(repo, "update-ref", f"{SLICE_REF_PREFIX}S00", stray)
        self.expect_refused(repo, finish, needles)
        self.git(repo, "update-ref", "-d", f"{SLICE_REF_PREFIX}S00")
        reason = "selftest: the user approved S01a before S00"
        self.expect_rc(self.gate(repo, *finish, "--out-of-order", reason), 0)
        message = self.message(repo)
        self.check(f"\n\n{UNFINISHED_DEPS_HEADING}\n- S00\n\n" in message, repr(message))
        self.check(message.endswith(f"\n{OUT_OF_ORDER_TRAILER}{reason}\n"), repr(message))
        self.expect_output(self.gate(repo, "status"), "unfinished dependency: S00")

    def finish_no_track_spec_pinned(self) -> None:
        # C4.5: --no-track-spec needs a pinned block with track_spec: false
        # (finish_untracked_spec covers the allowed case).
        repo = self.resolved("track-spec")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited, "--no-track-spec")
        refusal = "--no-track-spec is refused: the block pinned in the post-resolve commit"
        self.expect_refused(repo, finish, (refusal, "has track_spec: True"))
        # A working block with track_spec: false (an approved block change) does not
        # unlock the flag; only the pinned block counts.
        self.rewrite_block(repo, audited, _SELFTEST_HARNESS, track_spec=False)
        reason = "selftest: the user approved track_spec: false"
        self.expect_refused(repo, (*finish, "--accept-block-change", reason), (refusal,))
        self.rewrite_block(repo, audited, _SELFTEST_HARNESS)
        self.expect_rc(self.gate(repo, *finish[:4]), 0)
        tracked = self.git(repo, "ls-files", audited).strip() == audited
        self.check(tracked, "the audited MiniPRD is not tracked")

    def gate_commits_skip_hooks(self) -> None:
        # C4.6: hooks that would fail, rewrite or record run for plain git commits, but
        # never for the gate's commits and ref updates.
        repo = self.fixture("skip-hooks")
        marker = self.root / "skip-hooks-ran.txt"
        for name in (
            "pre-commit",
            "prepare-commit-msg",
            "commit-msg",
            "post-commit",
            "reference-transaction",
        ):
            self.hook(repo, name, f"echo {name} >> '{marker}'\nexit 1\n")
        backups = self.root / "backups" / "skip-hooks"
        self.expect_rc(self.gate(repo, "post-resolve", "--expect", "3", backups=backups), 0)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.expect_rc(self.gate(repo, "finish", "S00", "--miniprd", audited), 0)
        ran = marker.read_text(encoding="utf-8") if marker.exists() else ""
        self.check(not ran, f"hooks ran during the gate's commits: {ran!r}")
        wanted = f"v0.6.0 S00: Test harness\n\n{_SELFTEST_TOOL}\n\nHACF-Slice: S00\n"
        self.check(self.message(repo) == wanted, repr(self.message(repo)))
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S00") == self.head(repo), "no slice ref")
        plain = subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "selftest: a plain commit"],
            cwd=str(repo),
            env=self.env,
            capture_output=True,
            check=False,
        )
        live = marker.exists() and "pre-commit" in marker.read_text(encoding="utf-8")
        self.check(plain.returncode != 0 and live, "the hooks are not live for plain commits")

    def finish_committed_bytes(self) -> None:
        # C4.7: the bytes check.sh tests are the bytes finish commits.
        repo = self.resolved("raw-bytes")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited)
        self.git(repo, "config", "core.autocrlf", "input")
        (repo / "src/app.py").write_bytes(b"VALUE = 2\r\n")
        filtered = "src/app.py: its bytes on disk (git hash-object --no-filters) differ"
        self.expect_refused(repo, finish, (filtered,))
        self.git(repo, "config", "--unset", "core.autocrlf")
        (repo / "src/app.py").write_bytes(b"VALUE = 2\n")
        exclude = repo / self.git(repo, "rev-parse", "--git-path", "info/exclude").strip()
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("dev/tests/local_*.py\n")
        self.write(repo, "dev/tests/local_conftest.py", "import os\n")
        self.write(repo, "dev/tests/__pycache__/cached.py", "# a cache file\n")
        ignored = "dev/tests/local_conftest.py is an ignored Python file"
        self.expect_refused(repo, finish, (ignored,))
        proc = self.gate(repo, *finish)
        self.check("cached.py" not in proc.stdout + proc.stderr, "a __pycache__ file was refused")
        (repo / "dev/tests/local_conftest.py").unlink()
        self.expect_rc(self.gate(repo, *finish), 0)

    def finish_entry_modes(self) -> None:
        # C4.8: no symlink or gitlink entry among the committed paths.
        repo = self.resolved("entry-modes")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited)
        (repo / "src/link.py").symlink_to("app.py")
        self.expect_refused(repo, finish, ("src/link.py is a symlink (mode 120000)",))
        (repo / "src/link.py").unlink()
        nested = repo / "src" / "nested"
        nested.mkdir()
        self.git(nested, "init", "-q")
        identity = ("-c", "user.name=selftest", "-c", "user.email=selftest@example.invalid")
        self.git(nested, *identity, "commit", "-q", "--allow-empty", "-m", "a nested commit")
        self.expect_refused(repo, finish, ("src/nested is a gitlink (mode 160000",))
        shutil.rmtree(nested)
        self.expect_rc(self.gate(repo, *finish), 0)

    def finish_slice_ref_rules(self) -> None:
        # C4.9: a ref for a SID the post-resolve commit does not pin does not count, so
        # its commit is no scope base and the next finish checks its changes.
        repo = self.resolved("slice-refs")
        self.write(repo, "docs/other.md", "# Other\n\nChanged by a commit for an unpinned SID.\n")
        self.git(repo, "commit", "-q", "-am", f"v0.6.0 S77: not pinned\n\n{SLICE_TRAILER}S77")
        self.git(repo, "update-ref", f"{SLICE_REF_PREFIX}S77", "HEAD")
        status = self.gate(repo, "status")
        self.expect_output(status, "does not pin S77")
        self.expect_output(status, "completed slices (0): none")
        self.write(repo, "src/app.py", "VALUE = 2\n")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        finish = ("finish", "S00", "--miniprd", audited)
        self.expect_refused(repo, finish, ("1 changed path(s) match no files glob", "docs/other"))
        reason = "selftest: the user approved the S77 commit's docs change"
        self.expect_rc(self.gate(repo, *finish, "--accept-scope", reason), 0)
        # A commit made outside the gate under a pinned SID's ref counts, but every later
        # finish re-diffs it against that SID's files and waiver records.
        self.write(repo, "lib/ledger.py", "X = 1\n")
        self.write(repo, "docs/other.md", "# Other\n\nSmuggled in by a hand-made S01a commit.\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", f"v0.6.0 S01a: Ledger core\n\n{SLICE_TRAILER}S01a")
        forged = self.head(repo)
        self.git(repo, "update-ref", f"{SLICE_REF_PREFIX}S01a", forged)
        self.write(repo, "src/legacy.py", "OLD = False\n")
        migrated = "spec/archive/20261022_000000_v060_migrated/MiniPRD_02_Ledger-Migrate.md"
        self.archive(repo, _SELFTEST_MIGRATE, migrated)
        label = (
            f"{forged[:12]} v0.6.0 S01a: Ledger core (the commit {SLICE_REF_PREFIX}S01a records)"
        )
        needles = (f"{label} changes path(s)", "waiver records allow: docs/other.md")
        self.expect_refused(repo, ("finish", "S01b", "--miniprd", migrated), needles)
        self.expect_output(self.gate(repo, "status"), needles[0])

    def finish_lookalike_markers(self) -> None:
        # C4.10: lookalikes of the protected heading or markers are refused anywhere.
        repo = self.resolved("lookalike")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited)
        active = (repo / ACTIVE).read_text(encoding="utf-8")
        heading, marker = "imitates the protected heading", "imitates the completed-list marker"
        for extra, needle in (
            ("##  v0.6.0 bootstrap OPERATING procedure (read first)", heading),
            ("## v0.6.0 Bootstrap​ Operating Procedure (read first)", heading),
            (COMPLETED_BEGIN.replace("BEGIN", "begin"), marker),
            (f"Note: {COMPLETED_END.replace(' -->', '-->')}", marker),
        ):
            self.write(repo, ACTIVE, f"{active}\n{extra}\n")
            self.expect_refused(repo, finish, (needle,))
            self.expect_output(self.gate(repo, "status"), needle)
        self.write(repo, ACTIVE, f"{active}\n## Notes\n\nThe procedure section is protected.\n")
        self.expect_rc(self.gate(repo, *finish), 0)

    @staticmethod
    def alive(pid: int) -> bool:
        """Return True while process pid exists and is not a zombie."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return True
        return state.rsplit(")", 1)[-1].split()[:1] != ["Z"]

    def finish_check_process_group(self) -> None:
        # C4.11 (the glob rules are in post_resolve_unsafe_globs): a background process
        # that check.sh starts is killed with check.sh's process group.
        repo = self.resolved("process-group")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        pidfile = self.root / "process-group.pid"
        extra = {
            "V060_SELFTEST_CHECK_MODE": "background",
            "V060_SELFTEST_CHECK_PIDFILE": str(pidfile),
        }
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited, extra=extra)
        self.expect_rc(proc, 0)
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 10
        while self.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            raise _Fail(f"check.sh's background process {pid} outlived finish")

    def post_resolve_seed_cross_check(self) -> None:
        # C4.12: every seed's files, test names and depends_on reach its MiniPRD.
        seeds = {seed.sid: seed for seed in parse_seeds(_SELFTEST_SLICES)}
        self.check(sorted(seeds) == ["S00", "S01a", "S01b"], f"seeds: {sorted(seeds)}")
        harness_files = [
            ("Files to create", "dev/tests/test_harness.py"),
            ("Files to modify", "src/app.py"),
            ("Files to modify", LEDGER),
        ]
        self.check(seeds["S00"].files == harness_files, repr(seeds["S00"].files))
        self.check(seeds["S00"].tests == ["test_harness_ok", "test_harness_*"], "S00 tests")
        self.check(seeds["S00"].depends == [] and seeds["S01b"].depends == ["S01a"], "depends")
        blocks = _selftest_blocks()

        def introduce(repo: Path, orphan_seed: bool) -> None:
            harness = dict(blocks[_SELFTEST_HARNESS], files=["src/**"])
            self.write(repo, f"{COMPILED_DIR}/{_SELFTEST_HARNESS}", _selftest_miniprd(harness))
            core = _selftest_miniprd(blocks[_SELFTEST_CORE])
            core = core.replace("test_ledger_core", "test_ledger_other")
            self.write(repo, f"{COMPILED_DIR}/{_SELFTEST_CORE}", core)
            migrate = _selftest_miniprd(dict(blocks[_SELFTEST_MIGRATE], depends_on=["S00"]))
            migrate = migrate.replace("test_migrate_v2", "test_migrate_*")
            self.write(repo, f"{COMPILED_DIR}/{_SELFTEST_MIGRATE}", migrate)
            if orphan_seed:
                extra = '\n<a id="s03"></a>\n## S03 — NN 03: no MiniPRD\n\n- **Depends on:** S00\n'
                self.write(repo, SLICES, (repo / SLICES).read_text(encoding="utf-8") + extra)

        row = "only a SuperPRD Seed deviations row '| S00 | dev/tests/test_harness.py | R<n> |'"
        self.refused_post_resolve(
            "seeds",
            lambda repo: introduce(repo, True),
            (
                "seed file dev/tests/test_harness.py (Files to create) matches no files glob",
                row,
                "seed test test_ledger_core does not appear in the MiniPRD",
                "seed test test_migrate_* has no test name with the prefix test_migrate_",
                "depends_on lacks S01a, which the seed's Depends on line lists",
                "depends_on lists S00, which the seed's Depends on line lacks",
                f"{SLICES}#s03: no {COMPILED_DIR} MiniPRD declares slice S03",
            ),
        )
        # Seed deviations rows accept the gaps, but only a row that names an R-row counts.
        rows = (
            "| S00 | `dev/tests/test_harness.py` | R14 |\n| S01a | test_ledger_core | n/a |\n"
            "| S01b | test_migrate_* | R27 |\n| S01b | S01a | R14 |\n| S01b | S00 | R14 |\n"
        )
        repo = self.fixture("seeds-waived")
        introduce(repo, False)
        self.write(repo, SUPERPRD_BARE, _SELFTEST_SUPERPRD + rows)
        backups = self.root / "backups" / "seeds-waived"
        proc = self.gate(repo, "post-resolve", "--expect", "3", backups=backups)
        self.expect_rc(proc, 1)
        self.expect_output(proc, "seed test test_ledger_core does not appear")
        self.check(proc.stderr.count("only a SuperPRD Seed deviations row") == 1, proc.stderr)
        self.write(repo, SUPERPRD_BARE, _SELFTEST_SUPERPRD + rows.replace("n/a", "R14"))
        self.expect_rc(self.gate(repo, "post-resolve", "--expect", "3", backups=backups), 0)

    def finish_tool_version(self) -> None:
        # C4.13: the body records the first line of claude --version when claude exists.
        repo = self.resolved("tool-version")
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        finish = ("finish", "S00", "--miniprd", audited)
        self.expect_rc(self.gate(repo, *finish, extra={"PATH": self.no_claude}), 0)
        plain = "v0.6.0 S00: Test harness\n\nHACF-Slice: S00\n"
        self.check(self.message(repo) == plain, repr(self.message(repo)))
        failing = self.root / "bin-failing"
        failing.mkdir()
        (failing / "claude").write_text("#!/bin/sh\necho broken\nexit 3\n", encoding="utf-8")
        (failing / "claude").chmod(0o755)
        self.write(repo, "src/app.py", "VALUE = 3\n")
        again = (*finish, "--refinish", "selftest: the user approved a follow-up")
        path = os.pathsep.join([str(failing), self.no_claude])
        proc = self.gate(repo, *again, extra={"PATH": path})
        self.expect_rc(proc, 0)
        self.expect_output(proc, "claude --version exited 3; no tool version recorded")
        self.check(TOOL_VERSION_LABEL not in self.message(repo), "a failing claude was recorded")
        self.write(repo, "src/app.py", "VALUE = 4\n")
        self.expect_rc(self.gate(repo, *again), 0)
        message = self.message(repo)
        self.check(f"\n\n{_SELFTEST_TOOL}\n\n" in message, repr(message))
        self.check("a second line" not in message, "more than the first line was recorded")

    def finish_bundles(self) -> None:
        # C4.14: after the slice ref, finish bundles every branch and refs/hacf/*.
        repo = self.resolved("bundles")
        backups = self.root / "backups" / "bundles-root"
        audited = self.archive(repo, _SELFTEST_HARNESS)
        self.write(repo, "src/app.py", "VALUE = 2\n")
        proc = self.gate(repo, "finish", "S00", "--miniprd", audited, backups=backups)
        self.expect_rc(proc, 0)
        bundle = backups / "hacf-v060-S00.bundle"
        self.expect_output(proc, f"bundle {bundle}")
        heads = self.git(repo, "bundle", "list-heads", str(bundle))
        for ref in (f"refs/heads/{GATE_BRANCH}", POST_RESOLVE_REF, f"{SLICE_REF_PREFIX}S00"):
            self.check(f"{self.git(repo, 'rev-parse', ref).strip()} {ref}\n" in heads, heads)
        # A bundle that cannot be written is a warning; the slice is finished anyway.
        (backups / "hacf-v060-S01a.bundle").mkdir()
        self.write(repo, "lib/ledger.py", "X = 1\n")
        core = self.archive(repo, _SELFTEST_CORE)
        proc = self.gate(repo, "finish", "S01a", "--miniprd", core, backups=backups)
        self.expect_rc(proc, 0)
        self.expect_output(proc, "warning: finish S01a: git bundle create")
        self.expect_output(proc, "S01a is finished")
        self.check(self.ref(repo, f"{SLICE_REF_PREFIX}S01a") == self.head(repo), "no slice ref")

    def procedure_section_c4(self) -> None:
        # C4.15: the repository's own activeContext.md procedure section (read-only).
        active = self.script.parents[2] / ACTIVE
        self.check(active.is_file(), f"{active} does not exist")
        data = active.read_bytes()
        region = _procedure_region(data)
        self.check(region is not None, f"{ACTIVE}: the heading or markers are not byte-identical")
        text = (region or b"").decode("utf-8")
        sentence = (
            "A MiniPRD's Operating Notes override the skill text where they differ; an annex "
            "divergence without an R-row is a defect: stop and ask."
        )
        self.check(sentence in " ".join(text.split()), "the precedence bullet lacks the sentence")
        missing = [flag for flag, _ in WAIVER_FLAGS if f"`{flag}`" not in text]
        self.check(not missing, f"the waiver bullet lacks {missing}")
        problems = lookalike_problems(data.decode("utf-8"))
        self.check(not problems, f"{ACTIVE} has lookalike markers: {problems}")

    def run(self) -> int:
        scenarios: list[tuple[str, Callable[[], None]]] = [
            (
                "post-resolve --expect 3 validates, renames SuperPRD.md, backs up, commits",
                self.post_resolve_ok,
            ),
            (
                "post-resolve refuses a depends_on that points to a higher NN",
                self.post_resolve_higher_dependency,
            ),
            (
                "post-resolve refuses a guards.json sha256 mismatch",
                self.post_resolve_guard_mismatch,
            ),
            (
                "post-resolve refuses a bare SuperPRD.md when SuperPRD_v0_6_0.md exists",
                self.post_resolve_both_superprds,
            ),
            (
                "post-resolve refuses blocks with extra, missing or wrong-type keys",
                self.post_resolve_malformed_blocks,
            ),
            ("post-resolve refuses unresolvable annex and lore refs", self.post_resolve_bad_refs),
            (
                "post-resolve refuses a MiniPRD count other than --expect",
                self.post_resolve_wrong_count,
            ),
            (
                "post-resolve refuses stray, spec/process and staged paths outside its dirs",
                self.post_resolve_stray_paths,
            ),
            ("post-resolve refuses an edited guards.json", self.post_resolve_guards_edit),
            (
                "post-resolve refuses files globs '**', '/etc/**', '../x/**' and equivalents",
                self.post_resolve_unsafe_globs,
            ),
            (
                "a backup root inside the work tree falls back to .git/hacf-backups",
                self.post_resolve_backup_inside,
            ),
            (
                "post-resolve, finish and restore-ledger refuse off swimlane/v0.6.0",
                self.branch_refusals,
            ),
            ("usage errors exit 2", self.usage_errors),
            ("finish refuses when dev/scripts/check.sh fails", self.finish_check_fails),
            ("finish refuses a SID that does not match the block", self.finish_sid_mismatch),
            (
                "finish refuses out-of-scope files that appear while check.sh runs",
                self.finish_check_creates_file,
            ),
            (
                "finish commits an in-scope change; a repeat needs --refinish",
                self.finish_in_scope,
            ),
            ("finish refuses an out-of-scope change", self.finish_out_of_scope),
            (
                "finish --accept-scope commits with the HACF-Scope-Accepted trailer",
                self.finish_accept_scope,
            ),
            (
                "finish refuses a ledger node that lost a key present at the scope base",
                self.finish_dropped_key,
            ),
            (
                "finish refuses a dropped ledger node unless removes_nodes lists it; the body "
                "records the changed key's old and new value",
                self.finish_dropped_node,
            ),
            (
                "restore-ledger saves the working copy and restores HEAD's ledger",
                self.restore_ledger,
            ),
            ("status reports state and waivers without changing the repository", self.status),
            (
                "finish pins the block to the post-resolve commit and the audited path",
                self.finish_block_pinning,
            ),
            (
                "finish refuses guard changes even with --accept-scope; accepts an archive move",
                self.finish_guards,
            ),
            (
                "finish exempts only the ledger, own MiniPRD paths and activeContext.md "
                "outside the procedure section",
                self.finish_exemptions,
            ),
            (
                "finish rolls activeContext.md and the index back when the commit fails",
                self.finish_commit_rollback,
            ),
            (
                "finish refuses a bookkeeping-only slice without --allow-empty",
                self.finish_empty_slice,
            ),
            (
                "finish checks the ledger against the scope base across manual commits",
                self.finish_manual_commit,
            ),
            (
                "post-resolve refuses a second run; --repin re-pins with an HACF-Repin trailer",
                self.post_resolve_repin,
            ),
            ("finish refuses an amended post-resolve commit", self.finish_amended_pin),
            (
                "finish refuses a hand-made post-resolve commit, and --repin cannot hide it",
                self.finish_forged_pin,
            ),
            (
                "finish refuses when check.sh changes the ledger, the branch, HEAD, the index, a "
                "mode, an in-scope path or what git add stages, or is deleted or a symlink",
                self.finish_check_side_effects,
            ),
            (
                "a hand-made HACF-Slice commit is not a finished slice; its changes are checked",
                self.finish_forged_slice,
            ),
            (
                "an interrupt after the append restores activeContext.md and the index",
                self.finish_interrupt_rollback,
            ),
            (
                "finish refuses a staged path that appeared after the scope check",
                self.finish_late_writer,
            ),
            (
                "finish refuses when the scope base's ledger does not load",
                self.finish_base_ledger_unloadable,
            ),
            ("a retry after a killed finish commits exactly once", self.finish_retry_after_kill),
            (
                "finish checks its commit (paths, tree, message, branch) against what it staged "
                "and wrote",
                self.finish_commit_verified,
            ),
            (
                "finish refuses a symlinked guard, ledger, activeContext.md or memory directory",
                self.finish_symlinks,
            ),
            (
                "post-resolve and finish refuse duplicate YAML keys (a second '<<' included) and "
                "block merge keys",
                self.finish_duplicate_keys,
            ),
            (
                "post-resolve refuses titles with control, format or separator characters",
                self.post_resolve_forged_titles,
            ),
            ("--accept-block-change cannot change a block's nn", self.finish_identity_keys),
            (
                "finish refuses an audited copy while the compiled MiniPRD remains; a slice ref "
                "without its trailer does not count",
                self.finish_copy_not_move,
            ),
            (
                "post-resolve refuses a mode-only guards.json change before any write",
                self.post_resolve_guards_mode,
            ),
            (
                "post-resolve stages only its directories when strays appear after validation",
                self.post_resolve_staging_restricted,
            ),
            (
                "finish and post-resolve refuse assume-unchanged, skip-worktree and "
                "intent-to-add index entries before any check",
                self.finish_index_flags,
            ),
            (
                "finish refuses staged content that differs from what it checked or wrote",
                self.finish_staged_content,
            ),
            (
                "the checked tree sees racily clean edits and works without an index",
                self.finish_valid_tree,
            ),
            (
                "a commit step that moves HEAD back gets reflog advice, not a reset",
                self.finish_moved_head,
            ),
            (
                "an interrupt after the commit leaves advice; the next finish refuses until "
                "the reset, then commits once",
                self.finish_interrupted_after_commit,
            ),
            (
                "a lost post-resolve ref: finish, post-resolve and status print the recovery "
                "command; post-resolve refuses nothing to commit before any write",
                self.post_resolve_lost_ref,
            ),
            (
                "a post-resolve commit amended with other paths refuses --repin and finish",
                self.post_resolve_amended_paths,
            ),
            (
                "post-resolve refuses a symlinked MiniPRD, SuperPRD or ledger",
                self.post_resolve_symlinks,
            ),
            (
                "a guards.json key with a NUL or control character is an invalid guard",
                self.guards_invalid_keys,
            ),
            (
                "finish commits its message verbatim and quotes an accepted path ending in a space",
                self.finish_verbatim_message,
            ),
            (
                "finish ignores git replace objects (and warns) and refuses grafts",
                self.finish_replace_objects,
            ),
            (
                "the final slice untracks spec/ (git rm -r --cached) and keeps its files on disk",
                self.finish_untracked_spec,
            ),
            (
                "post-resolve refuses staged content other than what it parsed, and a commit tree "
                "another process changed",
                self.post_resolve_staged_content,
            ),
            (
                "C4.1 finish runs each validator's scope-base copy on both trees; a new finding "
                "needs --accept-validator-change, a deleted validator is refused",
                self.finish_validator_copies,
            ),
            (
                "C4.2 post-resolve --repin refuses to change or drop the block of a finished slice",
                self.post_resolve_repin_finished,
            ),
            (
                "C4.3 post-resolve refuses a missing, misplaced or pointerless Overrides banner",
                self.post_resolve_banner,
            ),
            (
                "C4.4 finish refuses an unfinished dependency unless --out-of-order",
                self.finish_out_of_order,
            ),
            (
                "C4.5 --no-track-spec needs a pinned block with track_spec: false",
                self.finish_no_track_spec_pinned,
            ),
            ("C4.6 no git hook runs during the gate's commits", self.gate_commits_skip_hooks),
            (
                "C4.7 finish refuses filtered committed bytes and ignored *.py test sources",
                self.finish_committed_bytes,
            ),
            ("C4.8 finish refuses symlink and gitlink entries", self.finish_entry_modes),
            (
                "C4.9 slice refs count only for pinned SIDs; recorded slice commits are re-diffed",
                self.finish_slice_ref_rules,
            ),
            (
                "C4.10 finish refuses lookalikes of the protected heading and markers",
                self.finish_lookalike_markers,
            ),
            (
                "C4.11 check.sh runs in its own process group, which finish kills",
                self.finish_check_process_group,
            ),
            (
                "C4.12 post-resolve cross-checks every seed; a Seed deviations row accepts a gap",
                self.post_resolve_seed_cross_check,
            ),
            (
                "C4.13 the slice commit body records the first line of claude --version",
                self.finish_tool_version,
            ),
            (
                "C4.14 finish writes a bundle after the slice ref; a failure only warns",
                self.finish_bundles,
            ),
            (
                "C4.15 the repository's procedure section has the C4 precedence and waivers",
                self.procedure_section_c4,
            ),
        ]
        print(f"selftest: throwaway repositories under {self.root}", flush=True)
        failed = 0
        for title, scenario in scenarios:
            try:
                scenario()
            except _Fail as exc:
                failed += 1
                print(f"FAIL {title}\n{textwrap.indent(str(exc), '     ')}", flush=True)
            except Exception as exc:  # report the crash and continue with the next scenario
                failed += 1
                print(f"FAIL {title}: {type(exc).__name__}: {_one_line(exc)}", flush=True)
            else:
                print(f"PASS {title}", flush=True)
        passed = len(scenarios) - failed
        print(f"selftest: {passed}/{len(scenarios)} scenarios passed", flush=True)
        return 0 if failed == 0 else 1


def cmd_selftest() -> int:
    """Run every command against throwaway repositories; return 0 only if all scenarios pass."""
    script = Path(__file__).resolve()
    root = Path(tempfile.mkdtemp(prefix="v060-gate-selftest-")).resolve()
    try:
        home = script.parents[2] if len(script.parents) > 2 else None
        if home is not None and root.is_relative_to(home):
            print(f"selftest: refusing to run: {root} is inside {home}", file=sys.stderr)
            return 1
        return _Selftest(script, root).run()
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="v060_gate.py",
        description="Per-slice gate for the HACF v0.6.0 bootstrap.",
        epilog=(
            "Waivers (--accept-scope, --accept-block-change, --allow-empty, --refinish, "
            "--out-of-order, --accept-validator-change, and --repin for post-resolve) need "
            "the user's approval first; REASON quotes the user and becomes a commit trailer. "
            "The gate records its commits in refs under "
            f"{REF_ROOT}/, runs no git hooks and ignores git replace objects; post-resolve and "
            "finish refuse to "
            "run while a grafts file or an assume-unchanged, skip-worktree or intent-to-add "
            "index entry exists. Exit codes: 0 ok, 1 validation failure or refusal, 2 usage "
            "error, 128+N after signal N "
            "interrupted the finish commit step (rolled back) or the steps after the commit "
            "(see the advice it prints)."
        ),
    )
    parser.add_argument(
        "--repo", metavar="PATH", help="target repository (default: git toplevel of the cwd)"
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    post = commands.add_parser("post-resolve", help="validate /hyper-resolve output and commit")
    post.add_argument(
        "--expect", type=int, default=29, metavar="N", help="number of MiniPRDs (default 29)"
    )
    post.add_argument(
        REPIN_FLAG,
        metavar="REASON",
        help="waiver: run post-resolve again and re-pin every block (HACF-Repin trailer)",
    )
    finish = commands.add_parser(
        "finish", help="validate one slice, commit it and record its slice ref"
    )
    finish.add_argument("sid", metavar="SID", help="slice id from the block, e.g. S01a")
    finish.add_argument(
        "--miniprd", required=True, metavar="PATH", help="audited MiniPRD under spec/archive/"
    )
    finish.add_argument(
        "--accept-scope", metavar="REASON", help="waiver: commit out-of-scope paths"
    )
    finish.add_argument(
        "--accept-block-change",
        metavar="REASON",
        help="waiver: use a block that differs from the post-resolve commit's",
    )
    finish.add_argument(
        "--allow-empty", metavar="REASON", help="waiver: commit a bookkeeping-only slice"
    )
    finish.add_argument(
        "--refinish", metavar="REASON", help="waiver: finish a SID that already has a commit"
    )
    finish.add_argument(
        "--out-of-order",
        metavar="REASON",
        help="waiver: finish before a depends_on slice has a slice ref",
    )
    finish.add_argument(
        "--accept-validator-change",
        metavar="REASON",
        help="waiver: accept findings that only the scope-base validator copy reports here",
    )
    finish.add_argument(
        "--no-track-spec",
        action="store_true",
        help="do not force-add spec/compiled, spec/archive (pinned track_spec: false only)",
    )
    commands.add_parser("restore-ledger", help="save the ledger, then restore HEAD's copy")
    commands.add_parser("status", help="read-only summary of the bootstrap state")
    commands.add_parser("selftest", help="run all commands against throwaway repositories")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run one command and return its exit code."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "post-resolve" and not 1 <= args.expect <= 100:
        parser.error("--expect must be between 1 and 100")
    try:
        if args.command == "selftest":
            return cmd_selftest()
        if args.command == "post-resolve":
            repin = _reason(REPIN_FLAG, args.repin)
            return cmd_post_resolve(resolve_repo(args.repo), args.expect, repin)
        repo = resolve_repo(args.repo)
        if args.command == "finish":
            waivers = {flag: getattr(args, flag[2:].replace("-", "_")) for flag, _ in WAIVER_FLAGS}
            return cmd_finish(repo, args.sid, args.miniprd, waivers, args.no_track_spec)
        if args.command == "restore-ledger":
            return cmd_restore_ledger(repo)
        return cmd_status(repo)
    except GateError as exc:
        print(f"v060_gate {args.command}: {exc}", file=sys.stderr, flush=True)
        return exc.code
    except _Interrupted as exc:
        print(f"v060_gate {args.command}: {exc}", file=sys.stderr, flush=True)
        return 128 + exc.signum


if __name__ == "__main__":
    sys.exit(main())
