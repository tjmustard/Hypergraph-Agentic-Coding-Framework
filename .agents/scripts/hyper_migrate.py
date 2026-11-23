#!/usr/bin/env python3
"""hyper_migrate.py — migrate a v0.5.x HACF project's specs to the v0.6.0 layout (S13a).

Normative design: HACF's v0.6.0 design notes (not shipped): migration, target layout, spec
frontmatter, progress/proof/notes and OpenRig manifests. The project is the git
work tree of the working directory. S13a builds the safety frame (detect, plan, preflight, lock,
backup, journal, confirmed apply, rollback) and the spec steps of APPLY (1 scaffold, 2-5 specs,
10 move the converted flat files); S13b adds the ledger, lore, memory, notes, instruction-file
and firewall steps (6-9) and `verify`.

CLI:
    detect
    plan [--json] [--include-archived]
    apply [--yes] [--allow-dirty] [--no-chain] [--include-archived]
    verify
    rollback --journal <path> [--force]
    move-slice <slice> --to-mission <dir>

Exit codes (the ledger and lore convention):
    0 ok (detect: nothing to migrate)
    1 findings (detect: at least one legacy signal; a step that could not be completed)
    2 usage
    3 lock timeout (`<git-common-dir>/hacf/locks/migrate.lock`)
    4 refused: no confirmation, or `--yes` under HACF_AUTONOMOUS=1
    5 conflict: a foreign root item, a dirty or mid-merge tree, drift since the journal, a
      backup whose SHA256SUMS does not verify

Semantics:
- detect and plan are read-only: no state dir, no lock file, no bytecode. plan prints a
  deterministic action list (repo-relative paths, no timestamps) and a human summary; `--json`
  prints the list as one JSON object whose `actions` the apply runs in order.
- Classification (step 2) reads every heading of `spec/compiled/SuperPRD*.md`, ignoring a leading
  section number and a trailing parenthetical: two or more baseline headings (the v0.5.9
  /hyper-baseline format) make BASELINE, then an `Execution Checklist` or `Technical
  Specifications` heading makes FEATURE, else the file is unclassified (asked about in an
  interactive apply, otherwise skipped and reported).
- BASELINE becomes the root SPEC.md through `hyper_scope.py project update --body-file`, only when
  SPEC.md is absent (the scaffold creates it) or still the scaffold default. FEATURE becomes a
  mission through `hyper_scope.py mission create`; its directory is the kebab case of the H1 title
  after `SuperPRD:`, cut at the first ` — ` or ` - `, punctuation dropped (MiniPRD_22 clarifying
  question 2, option a), and `release-X.Y.Z` when the title holds `vX.Y.Z`.
- Membership comes only from a FEATURE's Execution Checklist section: an exact basename
  `MiniPRD_<name>.md` (also matching `MiniPRD_<name>_AUDITED.md`), or an exact
  `**MiniPRD: <name>**` with the whitespace inside the bold markers trimmed. Every MiniPRD that no
  checklist names goes to the mission `legacy-unassigned`. With `--include-archived`, each
  `spec/archive/MiniPRD_*_AUDITED.md` is a slice too (hacf_status audited, PROOF.md and
  proof/00-legacy-audit.md); a compiled file whose archived twin exists makes no slice (R38).
- Slice order is checklist order, then NN, then lexical. The slice dir is `<NN>-<slug>`: NN is the
  file's own `MiniPRD_NN_` number when every slice of the mission has one that is a valid,
  unique rig ordinal (1 or more); otherwise every slice of the mission takes its 1-based position,
  so `MiniPRD_00_*` never asks rig for the invalid ordinal 0.
- apply needs a typed `yes` on a TTY; `--yes` skips the prompt and is refused (exit 4) under
  HACF_AUTONOMOUS=1. An agent shell can still pass `--yes`, so in agent shells the rule is
  procedural. Before the first write it takes the lock, checks the tree (git work tree, no merge
  or rebase in progress, the tracked files it touches clean unless --allow-dirty, no foreign root
  item), ensures the `.gitignore` managed block, then writes and verifies (hashlib, never
  `sha256sum`) `.agents/state/migrations/<UTC>/backup/` with spec, adr, memory and instructions
  tarballs and SHA256SUMS. The tarballs hold the pre-apply bytes, `.gitignore` included.
- The journal `.agents/state/migrations/v0.6.0.json` records the options, the plan, one entry per
  step (inputs, outputs with sha256 before and after, created and deleted paths), the per-path
  pre-apply and current shas, the created directories, the backup and the top-level `legacy_map`
  that `hyper_spec.py resolve` reads. It is replaced atomically after every step. A re-run takes
  the plan from the journal: a step already done is skipped when its files still hash as
  recorded, stops with exit 5 when a human changed one, and a step not yet done runs, so a
  second apply on a migrated tree changes nothing. A step that fails midway puts back what it had
  written (from the bytes earlier steps wrote, or the backup) and is journaled as failed with
  whatever it could not put back, so a re-run after the cause is fixed runs that step again; when
  it left paths behind, the re-run stops with exit 5 naming the step and those paths.
- rollback verifies SHA256SUMS, hashes every path the journal records as changed, and refuses on
  drift (exit 5, listing the paths) unless --force. It restores from the tarballs exactly the
  paths apply modified or deleted, deletes the created files whose sha still matches, removes the
  directories apply created when they are empty and the merge-driver git config keys
  apply added, and reports every path it could not restore or remove. The backup stays; the
  journal is renamed `v0.6.0.rolled-back.<UTC>.json` so `resolve` stops reading its legacy_map.
- Steps 6-9 (S13b) run between step 5 (slices) and step 10 (archive moves); every store keeps its
  single writer, so they only call hyper_ledger.py, hyper_lore.py, hyper_blocks.py and
  hyper_firewall.py, and are journaled, resumed and rolled back like the spec steps.
  6 ledger: when spec/compiled/architecture.yml does not parse, `repair-text --dry-run` (its diff
    is printed and journaled; an interactive run asks again) then `repair-text`; then
    `migrate-v2 --dry-run` and `migrate-v2` unless it is already hacf.ledger/v2; then every node
    whose associated_file is a legacy path in the journal's legacy_map gets
    `ensure-node --id --dimension --associated-file <slice SPEC.md> --spec-refs <slice dot-ID>`
    (R28). A missing ledger is reported (run /hyper-discover), never created.
  7 lore (R25): every **/docs/adr outside .git, node_modules, .venv and vendor is scanned; a dir
    inside a gitlink (`git ls-files -s` mode 160000), inside a nested repository (an ancestor with
    its own .git) or with no tracked file is skipped and reported. An interactive run asks once
    per remaining dir; a non-interactive one (`--yes`) imports only the root docs/adr. Each dir
    goes through `hyper_lore.py import --adr-dirs <dir> [--context <path before /docs/adr>]`,
    whose status mapping (proposed, accepted -> PROPOSED; deprecated -> RETIRED; superseded ->
    SUPERSEDED, bodies in lore/canon/) is the R8 rule. Every file it reports as imported gets a
    per-file redirect stub naming its new lore/ADRs/ path, the migration and adr.tar.gz; a file it
    left in place keeps its bytes and gets no stub. The migration never writes lore/CANON.md,
    lore/canon/** or lore_hypergraph.yml; `hyper_lore.py index` runs once after the lore writes.
  8 memory: a productContext.md that is not a template goes into root SPEC.md as the HACF managed
    block `migrated-product-context` under `## Product Context` (hyper_blocks grammar and
    compare-and-swap); a systemPatterns.md that is not a template becomes a PROPOSED canon via
    `hyper_lore.py propose --type canon`; memory files byte-identical to a template are removed
    (activeContext.md always stays); each `## Field Notes (legacy)` entry whose source MiniPRD is
    in legacy_map is appended to its mission's NOTES.md through hyper_lore's note writer with its
    original date, kind, agent, text and anchors, then removed from activeContext.md; an entry
    without a mapping stays and is reported.
  9 AGENTS.md, CLAUDE.md and GEMINI.md (those present) go through `hyper_blocks.py instruction`
    with `.agents/install-templates/<name>` (R9: never appended to; `--yes` or no TTY writes the
    `<STEM>.hacf-v0.6.0.md` sidecar); the `.gitignore` and `.agentignore` blocks are upserted; the
    `merge-drivers` block of `<git-common-dir>/info/attributes` (written by step 1's
    install-drivers) is checked and reported when missing; no step writes a .gitattributes (R10);
    `hyper_firewall.py merge-settings` (when .claude/ exists) and `render --ides` (ides from
    .agents/local/hacf.yml, else the IDE dirs present). CLAUDE.local.md and
    .claude/settings.local.json are never touched.
- verify runs `hyper_spec.py lint --all`, `hyper_ledger.py validate`, `hyper_lore.py validate`,
  `hyper_firewall.py check` and, only when `rig --version` is a version
  .agents/config/openrig_contract.yml accepts, or an in-range one whose range probe passed
  (hyper_openrig's version rule; verify waits for a running probe), `rig scope --workspace .
  audit --mission <m> --json` per mission; a probe still running is `skipped: rig probe
  pending`. It writes the report into the journal (when there is one) and exits 1 on any
  finding.
- move-slice wraps `hyper_scope.py slice move` and repoints the journal's legacy_map.
- Every subprocess is an argv list with shell=False; HACF tools run as
  `[sys.executable, '.agents/scripts/<tool>.py', ...]` in the project.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # neither this run nor its tools leave __pycache__ in the project

import argparse  # noqa: E402
import contextlib  # noqa: E402
import fcntl  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import tarfile  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Iterator  # noqa: E402

import _runtime  # noqa: E402

# Imported by _load_modules() after _runtime.require_runtime() (R19.4).
yaml: Any = None
hyper_blocks: Any = None
hyper_ledger: Any = None
hyper_lore: Any = None
hyper_openrig: Any = None
hyper_scaffold: Any = None
hyper_scope: Any = None
hyper_spec: Any = None

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_LOCK_TIMEOUT = 3
EXIT_REFUSED = 4
EXIT_CONFLICT = 5

SCRIPTS_REL = ".agents/scripts"
STATE_REL = ".agents/state"
MIGRATIONS_REL = ".agents/state/migrations"
JOURNAL_REL = ".agents/state/migrations/v0.6.0.json"
JOURNAL_SCHEMA = "hacf.migration-journal/v1"
PLAN_SCHEMA = "hacf.migration-plan/v1"
LOCK_REL = "hacf/locks/migrate.lock"  # under the git common dir (R22.1)
LOCK_TIMEOUT = 30.0
LOCK_POLL = 0.05
TOOL_TIMEOUT = 300
ARCHIVE_LABEL = "v060_migrated"
UNASSIGNED = "legacy-unassigned"
UNASSIGNED_INTENT = "Legacy MiniPRDs that no SuperPRD Execution Checklist names"
PROOF_NAME = "00-legacy-audit.md"
LEGACY_UNRECORDED = "legacy-unrecorded"
AUTONOMOUS_ENV = "HACF_AUTONOMOUS"
TEST_CORRUPT_ENV = "HACF_MIGRATE_TEST_CORRUPT_BACKUP"  # test hook: a tarball name to damage

# The v0.5.9 /hyper-baseline output format:
# `git show 984ca46:.agents/skills/hyper-baseline/SKILL.md`, lines 21-24.
BASELINE_HEADINGS = (
    "Current Core Value Loop",
    "Existing Data Models",
    "Established Patterns",
    "Identified Technical Debt",
)
FEATURE_HEADINGS = ("Execution Checklist", "Technical Specifications")
CHECKLIST_HEADING = "Execution Checklist"
BASELINE, FEATURE, UNCLASSIFIED = "BASELINE", "FEATURE", "UNCLASSIFIED"

# sha256 of the three v0.5.9 memory templates (`git show 984ca46:.agents/memory/<file>`) and of the
# current activeContext template; a memory file with another sha is the user's (DETECT). The
# set is keyed by sha alone: S10 retired the two template names from code (R44).
MEMORY_TEMPLATE_SHAS = frozenset(
    {
        "49d3f6e06b7339424a0fe361292e333f4cd33d4921238f148717166d8c137a61",
        "23c4249b099db03ea89bb93fe65728c35c4efe3118f77a4baf9d6759ffb1ee58",
        "2a6c13c3a3631eb0dda32e00e6decd6db6328bd1019b6b22b31d42cc5f4285fa",
        "c99e9141306dd14d431f59f14181e231ef4b59af8788125ca46bb0f1c1aed39e",
    }
)
FIELD_NOTES_HEADING = "## Field Notes (legacy)"
ADR_SKIP_DIRS = {".git", "node_modules", ".venv", "vendor"}
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
# What hyper_blocks' R9 decision may create next to them: the sidecar and the `.v059.bak` copy.
INSTRUCTION_SIDE_FILES = (
    *(f"{Path(name).stem}.hacf-v0.6.0.md" for name in INSTRUCTION_FILES),
    *(f"{name}.v059.bak" for name in INSTRUCTION_FILES),
)
IDE_IGNORE_FILES = (".cursorignore", ".rooignore", ".codeiumignore", ".geminiignore")
GIT_ATTRIBUTES_KEY = "$GIT_COMMON_DIR/info/attributes"  # journal key of a file outside the tree

# S13b: steps 6-9.
LEDGER_REL = "spec/compiled/architecture.yml"
MEMORY_REL = ".agents/memory"
ACTIVE_CONTEXT_REL = ".agents/memory/activeContext.md"
PRODUCT_CONTEXT_REL = ".agents/memory/productContext.md"
SYSTEM_PATTERNS_REL = ".agents/memory/systemPatterns.md"
PRODUCT_BLOCK_ID = "migrated-product-context"
PRODUCT_HEADING = "## Product Context"
CANON_TITLE = "System patterns (migrated)"
TEMPLATES_REL = ".agents/install-templates"
SETTINGS_FRAGMENT_REL = ".agents/install-templates/claude-settings.fragment.json"
LOCAL_CONFIG_REL = ".agents/local/hacf.yml"
ROOT_ADR_DIR = "docs/adr"
# The installer's IDE dirs (HACF-install.sh IDE_DEFS) and the firewall id each stands for.
IDE_DIRS = (
    (".claude", "claude"),
    (".windsurf", "windsurf"),
    (".cursor", "cursor"),
    (".clinerules", "cline"),
    (".roo", "roo"),
)
# The `.agentignore` HACF block: the shipped .agentignore's Read-and-Edit patterns (S11).
AGENTIGNORE_LINES = (
    "# HACF: historical drafts and unverified outputs agents must not read (hyper_firewall.py).",
    "spec/archive/",
    "tests/candidate_outputs/",
)
# A `## Field Notes (legacy)` entry, as `hyper_lore.py note --legacy` writes it.
LEGACY_NOTE_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) \[([^\]]+)\] \(agent:([^,()]+), slice ([^()\s]+)\) (.*) anchors: (.*)$"
)

# The paths S13a and S13b may change; snapshots, drift checks and the backup cover them.
TRACK_ROOTS = (
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    "missions",
    "lore",
    "spec",
    "tests",
    ".agents/local",
    ".agents/memory",
    ".gitignore",
    ".agentignore",
    ".claude/settings.json",
    ".agents/.backup",  # hyper_firewall saves .claude/settings.json here before it writes
    *INSTRUCTION_FILES,
    *INSTRUCTION_SIDE_FILES,
    *IDE_IGNORE_FILES,
)
TARBALLS = ("spec.tar.gz", "adr.tar.gz", "memory.tar.gz", "instructions.tar.gz")
SPEC_BACKUP_ROOTS = (
    "spec",
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "missions",
    "lore",
    "lore_hypergraph.yml",
)
INSTRUCTION_BACKUP_ROOTS = (
    *INSTRUCTION_FILES,
    ".claude/settings.json",
    ".gitignore",
    ".agentignore",
    *IDE_IGNORE_FILES,
)

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
SECTION_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
NN_RE = re.compile(r"^MiniPRD_(\d+)_(.+)\.md$")
COMPILED_RE = re.compile(r"^MiniPRD_(.+)\.md$")
ARCHIVED_RE = re.compile(r"^MiniPRD_(.+)_AUDITED\.md$")
FILENAME_REF_RE = re.compile(r"(?:^|[^A-Za-z0-9_])(MiniPRD_[^\s`'\"*()\[\]<>/\\|,;]+?\.md)")
BOLD_REF_RE = re.compile(r"\*\*MiniPRD:(.*?)\*\*")
RELEASE_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")
TITLE_CUT_RE = re.compile(r" (?:—|-) ")
SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BOOTSTRAP_FENCE = "```hacf-bootstrap"
DRIVER_SECTIONS = ("merge.hacf-lore", "merge.hacf-generated")


class MigrateError(Exception):
    """A failure that ends the command; `code` is its exit code."""

    def __init__(self, message: str, code: int = EXIT_CONFLICT) -> None:
        """Keep the message and the exit code."""
        super().__init__(message)
        self.code = code


def _load_modules() -> None:
    """Import PyYAML and the sibling HACF modules (after require_runtime)."""
    global yaml, hyper_blocks, hyper_ledger, hyper_lore, hyper_openrig
    global hyper_scaffold, hyper_scope, hyper_spec
    import hyper_blocks as _blocks
    import hyper_ledger as _ledger
    import hyper_lore as _lore
    import hyper_openrig as _openrig
    import hyper_scaffold as _scaffold
    import hyper_scope as _scope
    import hyper_spec as _spec
    import yaml as _yaml

    yaml, hyper_blocks, hyper_ledger, hyper_openrig = _yaml, _blocks, _ledger, _openrig
    hyper_lore, hyper_scaffold, hyper_scope, hyper_spec = _lore, _scaffold, _scope, _spec


# ---------------------------------------------------------------------------
# Repository, subprocesses (argv lists only) and hashing
# ---------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(argv: list[str], cwd: Path, timeout: int = TOOL_TIMEOUT) -> subprocess.CompletedProcess:
    """Run argv (shell=False) in cwd and capture its output; never raises on a non-zero exit."""
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def git(top: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git with an argv list in top."""
    return _run(["git", *args], top, timeout=60)


def tool(top: Path, name: str, *args: str) -> subprocess.CompletedProcess:
    """Run the project's own `.agents/scripts/<name>` under this interpreter."""
    return _run([sys.executable, f"{SCRIPTS_REL}/{name}", *args], top)


def repo_top() -> Path:
    """Return the git work-tree top of the working directory (exit 5 outside one)."""
    proc = _run(["git", "rev-parse", "--show-toplevel"], Path.cwd(), timeout=60)
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        raise MigrateError("not inside a git work tree; run hyper_migrate.py in the project", 5)
    return Path(top).resolve()


def common_dir(top: Path) -> Path:
    """Return `$(git rev-parse --git-common-dir)` as an absolute path."""
    proc = git(top, "rev-parse", "--git-common-dir")
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise MigrateError(f"cannot find the git common dir of {top}")
    path = Path(text)
    return (path if path.is_absolute() else top / path).resolve()


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of data."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    """Return a regular file's sha256 (a symlink hashes its target text), or None when absent."""
    if path.is_symlink():
        return sha256_bytes(b"symlink:" + os.readlink(path).encode())
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def key_path(top: Path, key: str) -> Path:
    """Return the file a journal key names (the info/attributes key lives in the git dir)."""
    if key == GIT_ATTRIBUTES_KEY:
        return common_dir(top) / "info" / "attributes"
    return top / key


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _rel(path: Path, top: Path) -> str:
    return Path(os.path.relpath(path, top)).as_posix()


# ---------------------------------------------------------------------------
# Snapshots: the files and directories the migration may change
# ---------------------------------------------------------------------------


def extra_roots(actions: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return the roots a plan adds to TRACK_ROOTS: the docs/adr dirs step 7 may import."""
    return tuple(a["target"] for a in actions if a.get("op") == "adr-import")


def snapshot(top: Path, extra: tuple[str, ...] = ()) -> tuple[dict[str, str], set[str]]:
    """Return {repo-relative path: sha256} and the directories under TRACK_ROOTS and extra.

    `.agents/state/` (backup, journal) is never part of a snapshot. The info/attributes file of
    the git common dir is included under GIT_ATTRIBUTES_KEY.
    """
    files: dict[str, str] = {}
    dirs: set[str] = set()
    for root in (*TRACK_ROOTS, *extra):
        path = top / root
        if path.is_symlink() or path.is_file():
            files[root] = sha256_file(path) or ""
            continue
        if not path.is_dir():
            continue
        dirs.add(root)
        for dirpath, dirnames, filenames in os.walk(path):
            here = Path(dirpath)
            dirnames.sort()
            for name in list(dirnames):
                child = here / name
                if child.is_symlink():
                    dirnames.remove(name)
                    files[_rel(child, top)] = sha256_file(child) or ""
                else:
                    dirs.add(_rel(child, top))
            for name in sorted(filenames):
                child = here / name
                files[_rel(child, top)] = sha256_file(child) or ""
    attributes = sha256_file(key_path(top, GIT_ATTRIBUTES_KEY))
    if attributes is not None:
        files[GIT_ATTRIBUTES_KEY] = attributes
    return files, dirs


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def _fence_flags(lines: list[str]) -> list[bool]:
    """Return, per line, True when it is inside (or delimits) a fenced code block."""
    flags, fence = [], None
    for line in lines:
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                flags.append(True)
                continue
            flags.append(False)
        else:
            flags.append(True)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
    return flags


def headings(text: str) -> list[tuple[int, str, int]]:
    """Return (level, text, line index) for every ATX heading outside fenced code."""
    lines = text.replace("\r\n", "\n").split("\n")
    found = []
    for index, (line, fenced) in enumerate(zip(lines, _fence_flags(lines))):
        match = None if fenced else HEADING_RE.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2), index))
    return found


def normalize_heading(text: str) -> str:
    """Drop a leading section number and a trailing parenthetical: `4.3 X (Y)` -> `X`."""
    text = SECTION_NUMBER_RE.sub("", text.strip())
    return TRAILING_PAREN_RE.sub("", text).strip()


def h1_text(text: str) -> str | None:
    """Return the first H1's text (outside fences), or None."""
    for level, value, _ in headings(text):
        if level == 1:
            return value.strip() or None
    return None


def split_sections(text: str) -> tuple[str | None, list[str], list[tuple[str, list[str]]]]:
    """Split Markdown into (H1 line, preamble lines, [(## heading line, body lines)]).

    Only `## ` headings outside fenced code start a section; deeper headings stay in it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    flags = _fence_flags(lines)
    h1, preamble, sections = None, [], []
    current: tuple[str, list[str]] | None = None
    for line, fenced in zip(lines, flags):
        if not fenced and line.startswith("## "):
            current = (line.rstrip(), [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)
        elif h1 is None and not fenced and line.startswith("# ") and not _trim(preamble):
            h1 = line.rstrip()  # the H1 is the first non-blank line
        else:
            preamble.append(line)
    return h1, preamble, sections


def _trim(lines: list[str]) -> list[str]:
    """Drop leading and trailing blank lines."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _join(blocks: list[list[str]]) -> str:
    """Join blocks of lines with one blank line between them; one final newline."""
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def _section(heading: str, body: list[str]) -> list[str]:
    body = _trim(body)
    return [heading, "", *body] if body else [heading]


def kebab(text: str, split_camel: bool = False) -> str:
    """Kebab case: whitespace, `_` and `-` separate words; other punctuation is dropped.

    split_camel (slice slugs only) also separates CamelCase words: `TokenBudget` -> `token-budget`.
    """
    if split_camel:
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"[\s_-]+", " ", text.lower())
    words = re.sub(r"[^a-z0-9 ]", "", text).split()
    return "-".join(words)[:64].strip("-")


def one_line(text: str) -> str:
    """Return text as one line without control characters (hyper_scope's --intent rule)."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


# ---------------------------------------------------------------------------
# Classification, membership and the plan
# ---------------------------------------------------------------------------


def classify(text: str) -> str:
    """BASELINE (two or more baseline headings) first, then FEATURE, else UNCLASSIFIED."""
    names = {normalize_heading(value) for _, value, _ in headings(text)}
    if len(names & set(BASELINE_HEADINGS)) >= 2:
        return BASELINE
    if names & set(FEATURE_HEADINGS):
        return FEATURE
    return UNCLASSIFIED


def superprd_title(text: str, path: Path) -> str:
    """Return the H1 title after `SuperPRD:` (else the H1, else the file name's suffix)."""
    title = h1_text(text)
    if title is None:
        stem = path.stem
        return stem[len("SuperPRD_") :] if stem.startswith("SuperPRD_") else stem
    if title.startswith("SuperPRD:"):
        title = title[len("SuperPRD:") :]
    return one_line(title) or path.stem


def mission_name(title: str) -> str:
    """Question 2 (a): `release-X.Y.Z` for a `vX.Y.Z` title, else the kebab of the cut title."""
    release = RELEASE_RE.search(title)
    if release:
        return f"release-{release.group(1)}"
    cut = TITLE_CUT_RE.split(title, maxsplit=1)[0]
    return kebab(cut) or "mission"


def checklist_lines(text: str) -> list[str]:
    """Return the lines of the Execution Checklist section (heading of any level, any number)."""
    lines = text.replace("\r\n", "\n").split("\n")
    found = headings(text)
    for position, (level, value, index) in enumerate(found):
        if normalize_heading(value) != CHECKLIST_HEADING:
            continue
        end = len(lines)
        for later_level, _, later_index in found[position + 1 :]:
            if later_level <= level:
                end = later_index
                break
        flags = _fence_flags(lines)
        return [line for line, fenced in zip(lines[index + 1 : end], flags[index + 1 : end])]
    return []


def checklist_refs(text: str) -> list[tuple[str, str]]:
    """Return the checklist's MiniPRD references in order: ('file', basename) or ('name', name)."""
    refs: list[tuple[int, int, tuple[str, str]]] = []
    for row, line in enumerate(checklist_lines(text)):
        for match in FILENAME_REF_RE.finditer(line):
            refs.append((row, match.start(1), ("file", match.group(1))))
        for match in BOLD_REF_RE.finditer(line):
            refs.append((row, match.start(), ("name", match.group(1).strip())))
    return [ref for _, _, ref in sorted(refs)]


class Unit:
    """One legacy MiniPRD that becomes (at most) one slice."""

    def __init__(self, name: str) -> None:
        """Start a unit with neither a compiled nor an archived file."""
        self.name = name
        self.compiled: str | None = None  # repo-relative path
        self.archived: str | None = None

    def source(self, include_archived: bool) -> str | None:
        """The file the slice is made from, or None (an archived file not imported)."""
        if self.archived and include_archived:
            return self.archived
        if self.archived and self.compiled:
            return None  # a twin without --include-archived: no slice (R38)
        return self.compiled

    def nn(self) -> int | None:
        """Return the NN of `MiniPRD_NN_<Slug>`, or None."""
        match = NN_RE.match(f"MiniPRD_{self.name}.md")
        return int(match.group(1)) if match else None

    def slug(self) -> str:
        """Return the slice slug: the kebab of the name without its NN."""
        match = NN_RE.match(f"MiniPRD_{self.name}.md")
        return kebab(match.group(2) if match else self.name, split_camel=True) or "slice"


def legacy_units(top: Path) -> dict[str, Unit]:
    """Return every flat compiled and archived MiniPRD, keyed by its <name>."""
    units: dict[str, Unit] = {}
    for folder, pattern, attr in (
        ("spec/compiled", COMPILED_RE, "compiled"),
        ("spec/archive", ARCHIVED_RE, "archived"),
    ):
        directory = top / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            match = pattern.match(path.name)
            if match is None or (attr == "compiled" and path.name.endswith("_AUDITED.md")):
                continue
            unit = units.setdefault(match.group(1), Unit(match.group(1)))
            setattr(unit, attr, f"{folder}/{path.name}")
    return units


def match_ref(ref: tuple[str, str], units: dict[str, Unit]) -> Unit | None:
    """Return the unit an exact checklist reference names (no fragments, no slugs)."""
    kind, value = ref
    if kind == "name":
        return units.get(value)
    for unit in units.values():
        names = {Path(p).name for p in (unit.compiled, unit.archived) if p}
        if value in names or (unit.archived and value == f"MiniPRD_{unit.name}.md"):
            return unit
    return None


def bootstrap_sid(text: str) -> str | None:
    """Return the `slice` key of the file's hacf-bootstrap block (yaml.safe_load), or None."""
    lines = text.replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        if line.strip() != BOOTSTRAP_FENCE:
            continue
        block = []
        for inner in lines[index + 1 :]:
            if inner.strip().startswith("```"):
                break
            block.append(inner)
        try:
            data = yaml.safe_load("\n".join(block))
        except yaml.YAMLError:
            return None
        sid = data.get("slice") if isinstance(data, dict) else None
        return sid if isinstance(sid, str) and SID_RE.match(sid) else None
    return None


def slice_commit(top: Path, sid: str) -> str | None:
    """Return the commit carrying `HACF-Slice: <sid>` (the slice ref first, else the newest)."""

    def carries(sha: str) -> bool:
        proc = git(top, "log", "-1", "--format=%(trailers:key=HACF-Slice,valueonly)", sha)
        return proc.returncode == 0 and sid in [v.strip() for v in proc.stdout.splitlines()]

    proc = git(top, "rev-parse", "--verify", "--quiet", f"refs/hacf/v0.6.0/slices/{sid}^{{commit}}")
    sha = proc.stdout.strip()
    if proc.returncode == 0 and sha and carries(sha):
        return sha
    proc = git(top, "log", "--all", "--format=%x1e%H%x00%(trailers:key=HACF-Slice,valueonly)")
    if proc.returncode != 0:
        return None
    for record in proc.stdout.split("\x1e")[1:]:
        sha, _, values = record.partition("\x00")
        if sid in [v.strip() for v in values.splitlines()]:
            return sha.strip()
    return None


def slice_intent(text: str, name: str) -> str:
    """Task 17: the H1 after `# MiniPRD: `, else the H1 after `# `, else <name>."""
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("# MiniPRD: ") and one_line(line[len("# MiniPRD: ") :]):
            return one_line(line[len("# MiniPRD: ") :])
        if line.startswith("# ") and one_line(line[2:]):
            return one_line(line[2:])
    return name


def legacy_header(top: Path, rel: str) -> tuple[str | None, str | None]:
    """Return (node_id, parent) of a legacy MiniPRD through `hyper_spec.py resolve --json`."""
    proc = tool(top, "hyper_spec.py", "resolve", rel, "--json")
    if proc.returncode != 0:
        raise MigrateError(
            f"hyper_spec.py resolve {rel} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    record = json.loads(proc.stdout)
    return record.get("node_id"), record.get("parent")


def is_scaffold_default(top: Path) -> bool:
    """True when the root SPEC.md is HACF's and still what `project init` writes by default."""
    path = top / "SPEC.md"
    if hyper_scope.classify_spec(path)[0] != hyper_scope.HACF:
        return False
    text = _read_text(path)
    if (hyper_openrig.read_frontmatter(path) or {}).get(
        "intent"
    ) != hyper_scope.DEFAULT_PROJECT_INTENT:
        return False
    default = hyper_scope.project_spec_text(top, hyper_scope.DEFAULT_PROJECT_INTENT, None)
    return hyper_scope._normalized_body(text) == hyper_scope._normalized_body(default)


def existing_mission_ids(top: Path) -> list[Any]:
    """Return the ids of the missions that already exist."""
    mroot = hyper_spec.missions_root(top)
    return [
        (hyper_openrig.read_frontmatter(m / "SPEC.md") if (m / "SPEC.md").is_file() else {}).get(
            "id"
        )
        for m in hyper_spec.mission_dirs(mroot)
    ]


def build_plan(
    top: Path,
    include_archived: bool,
    chain: bool = True,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the deterministic migration plan; reads the tree and writes nothing."""
    overrides = overrides or {}
    reports: list[str] = []
    actions: list[dict[str, Any]] = [{"step": 1, "op": "scaffold"}]
    compiled = top / "spec" / "compiled"
    superprds = (
        sorted(p for p in compiled.glob("SuperPRD*.md") if p.is_file() and not p.is_symlink())
        if compiled.is_dir()
        else []
    )
    classified = []
    for path in superprds:
        rel = _rel(path, top)
        text = _read_text(path)
        cls = overrides.get(rel) or classify(text)
        classified.append((rel, text, cls))
    unclassified = [rel for rel, _, cls in classified if cls == UNCLASSIFIED]
    for rel in unclassified:
        reports.append(f"{rel}: unclassified (no baseline or feature headings); skipped")

    converted: list[str] = []
    baseline_done = False
    spec_path = top / "SPEC.md"
    spec_ok = not os.path.lexists(spec_path) or is_scaffold_default(top)
    for rel, text, cls in classified:
        if cls != BASELINE:
            continue
        if baseline_done or not spec_ok:
            why = "another baseline went first" if baseline_done else "SPEC.md is the user's"
            reports.append(f"{rel}: BASELINE not migrated ({why}); both files left as they are")
            continue
        title = superprd_title(text, top / rel)
        actions.append(
            {"step": 2, "op": "project-spec", "source": rel, "target": "SPEC.md", "intent": title}
        )
        converted.append(rel)
        baseline_done = True

    units = legacy_units(top)
    prefix = hyper_scope.id_prefix(top)
    mroot_rel = _rel(hyper_spec.missions_root(top), top)
    ids = existing_mission_ids(top)
    claimed: dict[str, str] = {}
    missions: list[dict[str, Any]] = []
    used_names = {m.name for m in hyper_spec.mission_dirs(hyper_spec.missions_root(top))}
    for rel, text, cls in classified:
        if cls != FEATURE:
            continue
        title = superprd_title(text, top / rel)
        name = mission_name(title)
        if name in used_names or name == UNASSIGNED:
            reports.append(f"{rel}: mission {name} already exists; FEATURE not migrated")
            continue
        used_names.add(name)
        members: list[Unit] = []
        for ref in checklist_refs(text):
            unit = match_ref(ref, units)
            if unit is None or unit in members:
                continue
            if unit.name in claimed:
                reports.append(
                    f"{rel}: MiniPRD_{unit.name} is already in mission {claimed[unit.name]}"
                )
                continue
            claimed[unit.name] = name
            members.append(unit)
        missions.append({"name": name, "intent": title, "source": rel, "units": members})
        converted.append(rel)
    loose = [u for n, u in sorted(units.items()) if n not in claimed]
    loose.sort(key=lambda u: (u.nn() is None, u.nn() or 0, f"MiniPRD_{u.name}.md"))
    if any(u.source(include_archived) for u in loose):
        missions.append(
            {"name": UNASSIGNED, "intent": UNASSIGNED_INTENT, "source": None, "units": loose}
        )

    twins = [
        {"compiled": u.compiled, "archived": u.archived}
        for _, u in sorted(units.items())
        if u.compiled and u.archived
    ]
    not_imported = [
        u.archived for _, u in sorted(units.items()) if u.archived and not include_archived
    ]
    if not_imported:
        reports.append(
            f"{len(not_imported)} archived MiniPRD(s) not imported; --include-archived imports them"
        )

    slice_actions: list[dict[str, Any]] = []
    status_actions: list[dict[str, Any]] = []
    legacy_map: dict[str, str] = {}
    to_archive = [rel for rel in converted]
    for mission in missions:
        name = mission["name"]
        if hyper_openrig.RELEASE_NAME_RE.match(name):
            mission_id = hyper_openrig.mission_dot_id(name, prefix=prefix)
        else:
            ordinal = hyper_openrig.next_escape_band_ordinal(ids, prefix)
            mission_id = hyper_openrig.mission_dot_id(name, ordinal, prefix)
        ids.append(mission_id)
        mission_rel = f"{mroot_rel}/{name}"
        actions.append(
            {
                "step": 3,
                "op": "mission-create",
                "mission": mission_rel,
                "id": mission_id,
                "intent": mission["intent"],
                "source": mission["source"],
            }
        )
        units_here = [u for u in mission["units"] if u.source(include_archived)]
        numbers = [u.nn() for u in units_here]
        use_nn = all(n is not None and n >= 1 for n in numbers) and len(set(numbers)) == len(
            numbers
        )
        previous = None
        statuses = []
        for position, unit in enumerate(units_here, start=1):
            source = unit.source(include_archived)
            ordinal = unit.nn() if use_nn else position
            slice_name = f"{ordinal:02d}-{unit.slug()}"
            slice_rel = f"{mission_rel}/slices/{slice_name}"
            text = _read_text(top / source)
            node_id, parent = legacy_header(top, source)
            for field, value in (("node_id", node_id), ("parent", parent)):
                if value is not None and not hyper_ledger.ID_RE.match(value):
                    reports.append(
                        f"{source}: header {field} {value!r} is not one ledger node id; "
                        "the slice gets none"
                    )
            node_id = node_id if node_id and hyper_ledger.ID_RE.match(node_id) else None
            parent = parent if parent and hyper_ledger.ID_RE.match(parent) else None
            audited = source == unit.archived
            action: dict[str, Any] = {
                "step": 5,
                "op": "slice-create",
                "mission": mission_rel,
                "slice": slice_rel,
                "id": f"{mission_id}.{ordinal}",
                "source": source,
                "legacy_order": position,
                "intent": slice_intent(text, unit.name),
                "node_id": node_id,
                "parent": parent,
                "depends_on": [previous] if (chain and previous) else [],
                "hacf_status": "audited" if audited else "compiled",
            }
            if audited:
                sid = bootstrap_sid(text)
                commit = slice_commit(top, sid) if sid else None
                action["candidate_sha"] = commit or LEGACY_UNRECORDED
                action["swimlane_sha"] = commit
            slice_actions.append(action)
            statuses.append(action["hacf_status"])
            previous = action["id"]
            legacy_map[source] = f"{slice_rel}/SPEC.md"
            if unit.compiled and unit.compiled.startswith("spec/compiled/"):
                legacy_map.setdefault(unit.compiled, f"{slice_rel}/SPEC.md")
                if unit.compiled not in to_archive:
                    to_archive.append(unit.compiled)
        actions.extend(a for a in slice_actions if a["mission"] == mission_rel)
        if statuses and all(s == "audited" for s in statuses):
            status_actions.append(
                {"step": 5, "op": "mission-status", "mission": mission_rel, "status": "complete"}
            )
        elif any(s != "compiled" for s in statuses):
            status_actions.append(
                {"step": 5, "op": "mission-status", "mission": mission_rel, "status": "active"}
            )
        actions.extend(a for a in status_actions if a["mission"] == mission_rel)
    # Compiled files that made no slice: twins without --include-archived stay put only when
    # they are not twins; every twin moves with the converted files (R38).
    for twin in twins:
        if twin["compiled"] not in to_archive:
            to_archive.append(twin["compiled"])
    actions.extend(store_actions(top, legacy_map))
    if to_archive:
        actions.append(
            {"step": 10, "op": "archive", "paths": sorted(to_archive), "label": ARCHIVE_LABEL}
        )
    return {
        "schema": PLAN_SCHEMA,
        "include_archived": include_archived,
        "chain": chain,
        "actions": actions,
        "legacy_map": dict(sorted(legacy_map.items())),
        "twins": twins,
        "unclassified": unclassified,
        "not_imported": not_imported,
        "reports": reports,
    }


def _user_memory(top: Path, rel: str) -> bool:
    """True when a memory file exists and is not byte-identical to a known template."""
    path = top / rel
    return (
        path.is_file() and not path.is_symlink() and sha256_file(path) not in MEMORY_TEMPLATE_SHAS
    )


def has_legacy_notes(top: Path) -> bool:
    """True when activeContext.md has an entry under `## Field Notes (legacy)`."""
    path = top / ACTIVE_CONTEXT_REL
    if path.is_symlink() or not path.is_file():
        return False
    _, _, sections = split_sections(_read_text(path))
    return any(
        heading == FIELD_NOTES_HEADING and any(line.startswith("- ") for line in body)
        for heading, body in sections
    )


def configured_ides(top: Path) -> list[str]:
    """Return the firewall IDE ids: `ides` of .agents/local/hacf.yml, else the IDE dirs present."""
    path = top / LOCAL_CONFIG_REL
    data: Any = None
    if path.is_file() and not path.is_symlink():
        with contextlib.suppress(yaml.YAMLError, UnicodeDecodeError):
            data = yaml.safe_load(_read_text(path))
    value = data.get("ides") if isinstance(data, dict) else None
    if isinstance(value, str):
        value = [part for part in re.split(r"[,\s]+", value) if part]
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    return [ide for folder, ide in IDE_DIRS if (top / folder).is_dir()]


def ledger_actions(top: Path, legacy_map: dict[str, str]) -> list[dict[str, Any]]:
    """Step 6 actions: repair when the ledger does not parse, migrate-v2, spec_refs (R28)."""
    path = top / LEDGER_REL
    if path.is_symlink() or not path.is_file():
        return [{"step": 6, "op": "ledger-missing", "target": LEDGER_REL}]
    text = path.read_bytes().decode("utf-8", errors="replace")
    try:
        data, parses = yaml.safe_load(text), True
    except yaml.YAMLError:
        data, parses = None, False
    actions: list[dict[str, Any]] = []
    if not parses:
        actions.append({"step": 6, "op": "ledger-repair", "target": LEDGER_REL})
    if not parses or not isinstance(data, dict) or data.get("schema") != hyper_ledger.SCHEMA_V2:
        actions.append({"step": 6, "op": "ledger-migrate-v2", "target": LEDGER_REL})
    if any(legacy in text for legacy in legacy_map):
        actions.append({"step": 6, "op": "ledger-spec-refs", "target": LEDGER_REL})
    return actions


def store_actions(top: Path, legacy_map: dict[str, str]) -> list[dict[str, Any]]:
    """Return the actions of APPLY steps 6-9 (S13b); reads the tree and writes nothing."""
    actions = ledger_actions(top, legacy_map)
    candidates, skipped = scan_adr_dirs(top)
    if candidates or skipped:
        actions.append(
            {"step": 7, "op": "adr-scan", "dirs": [c["dir"] for c in candidates],
             "skipped": skipped}
        )  # fmt: skip
    for candidate in candidates:
        actions.append(
            {"step": 7, "op": "adr-import", "target": candidate["dir"],
             "context": candidate["context"]}
        )  # fmt: skip
    if _user_memory(top, PRODUCT_CONTEXT_REL):
        actions.append(
            {"step": 8, "op": "product-context", "source": PRODUCT_CONTEXT_REL, "target": "SPEC.md"}
        )
    if _user_memory(top, SYSTEM_PATTERNS_REL):
        actions.append({"step": 8, "op": "system-patterns", "source": SYSTEM_PATTERNS_REL})
    memory = top / MEMORY_REL
    templates = sorted(
        _rel(path, top)
        for path in (memory.glob("*.md") if memory.is_dir() else [])
        if path.is_file()
        and not path.is_symlink()
        and path.name != Path(ACTIVE_CONTEXT_REL).name
        and sha256_file(path) in MEMORY_TEMPLATE_SHAS
    )
    if templates:
        actions.append({"step": 8, "op": "memory-remove", "paths": templates})
    if has_legacy_notes(top):
        actions.append({"step": 8, "op": "legacy-notes", "source": ACTIVE_CONTEXT_REL})
    lore_ops = {"adr-import", "system-patterns", "legacy-notes"}
    if any(a["op"] in lore_ops for a in actions):
        actions.append({"step": 8, "op": "lore-index"})  # Task 9: once, after the lore writes
    for name in INSTRUCTION_FILES:
        if os.path.lexists(top / name):
            actions.append({"step": 9, "op": "instruction-file", "target": name})
    actions.append({"step": 9, "op": "blocks", "files": [".gitignore", ".agentignore"]})
    actions.append({"step": 9, "op": "drivers-check"})
    actions.append(
        {"step": 9, "op": "firewall", "ides": configured_ides(top),
         "settings": (top / ".claude").is_dir()}
    )  # fmt: skip
    return actions


def plan_summary(plan: dict[str, Any]) -> list[str]:
    """Return the human summary lines of a plan."""
    lines = []
    for action in plan["actions"]:
        op = action["op"]
        if op == "scaffold":
            lines.append(
                "1. scaffold: hyper_scaffold.py apply "
                "(SPEC.md, project.yaml, workspace.yaml, missions/, lore/, ...)"
            )
        elif op == "project-spec":
            lines.append(f"2. baseline {action['source']} -> SPEC.md")
        elif op == "mission-create":
            source = action["source"] or "(MiniPRDs no checklist names)"
            lines.append(f"3. mission {action['mission']} ({action['id']}) <- {source}")
        elif op == "slice-create":
            deps = ", ".join(action["depends_on"]) or "none"
            lines.append(
                f"5.   slice {action['slice']} ({action['id']}, {action['hacf_status']}) "
                f"<- {action['source']}; depends on {deps}"
            )
        elif op == "mission-status":
            lines.append(f"5.   mission {action['mission']} status -> {action['status']}")
        elif op == "archive":
            lines.append(
                f"10. move {len(action['paths'])} converted file(s) "
                f"to spec/archive/<ts>_{action['label']}/"
            )
        else:
            lines.append(_store_summary(action))
    for twin in plan["twins"]:
        lines.append(f"twin: {twin['compiled']} (its slice is {twin['archived']})")
    for report in plan["reports"]:
        lines.append(f"report: {report}")
    return lines


def _store_summary(action: dict[str, Any]) -> str:
    """Return the summary line of a step 6-9 action."""
    op = action["op"]
    texts = {
        "ledger-missing": f"6. ledger: {LEDGER_REL} is missing (run /hyper-discover); not created",
        "ledger-repair": f"6. ledger: repair-text {LEDGER_REL} (it does not parse; diff first)",
        "ledger-migrate-v2": f"6. ledger: migrate-v2 {LEDGER_REL} to {hyper_ledger.SCHEMA_V2}",
        "ledger-spec-refs": "6. ledger: repoint associated_file of migrated MiniPRDs; spec_refs",
        "system-patterns": f"8. memory: {SYSTEM_PATTERNS_REL} -> PROPOSED canon (lore/proposed/)",
        "legacy-notes": "8. notes: Field Notes (legacy) -> mission NOTES.md where mapped",
        "lore-index": "8. lore: hyper_lore.py index (lore/MAP.md, lore/CANON.md)",
        "drivers-check": "9. drivers: check merge-drivers in <git-common-dir>/info/attributes",
    }
    if op in texts:
        return texts[op]
    if op == "adr-scan":
        skipped = "; ".join(f"{s['dir']} ({s['reason']})" for s in action["skipped"]) or "none"
        return f"7. lore: docs/adr dirs {', '.join(action['dirs']) or 'none'}; skipped {skipped}"
    if op == "adr-import":
        context = f" --context {action['context']}" if action["context"] else ""
        return f"7. lore: import --adr-dirs {action['target']}{context}"
    if op == "product-context":
        return f"8. memory: {action['source']} -> SPEC.md block {PRODUCT_BLOCK_ID}"
    if op == "memory-remove":
        return f"8. memory: remove template-identical {', '.join(action['paths'])}"
    if op == "instruction-file":
        return f"9. instructions: {action['target']} (R9 decision; never appended to)"
    if op == "blocks":
        return f"9. blocks: {', '.join(action['files'])}"
    if op == "firewall":
        ides = ", ".join(action["ides"]) or "none"
        settings = "merge-settings, " if action["settings"] else ""
        return f"9. firewall: {settings}render --ides {ides}"
    return f"{action.get('step', '?')}. {op}"


# ---------------------------------------------------------------------------
# Converted bodies (heading conversion, question 3)
# ---------------------------------------------------------------------------


def slice_body(text: str, intent: str, source: str, hacf_status: str, depends_on: list[str]) -> str:
    """Return the slice SPEC.md body: the legacy text with every schema heading present.

    The two legacy headings become their v6 aliases; every missing slice heading is added; the
    schema sections come first in schema order and every other section follows `## Dependencies`
    verbatim.
    """
    h1, preamble, sections = split_sections(text)
    present: dict[str, list[str]] = {}
    others: list[list[str]] = []
    for heading, body in sections:
        mapped = hyper_spec.LEGACY_HEADING_ALIASES.get(heading, heading)
        if mapped in hyper_spec.SLICE_HEADINGS and mapped not in present:
            present[mapped] = body
        else:
            others.append(_section(heading, body))
    if h1 is None or not h1.startswith(hyper_spec.SLICE_H1_PREFIX):
        title_line = f"{hyper_spec.SLICE_H1_PREFIX}{intent}"
        preamble = ([h1, ""] if h1 else []) + preamble
    else:
        title_line = h1
    added = {
        "## Intent": [intent],
        "## Source material": [f"- `{source}` (legacy flat MiniPRD, migrated by hyper_migrate.py)"],
        "## Intent visual": ["N/A (migrated legacy MiniPRD)."],
        "## Status": [f"- {hacf_status}"],
        "## Dependencies": [f"- {dep}" for dep in depends_on] or ["- (none)"],
    }
    blocks = [[title_line], _trim(preamble)]
    for heading in hyper_spec.SLICE_HEADINGS:
        if heading in present:
            blocks.append(_section(heading, present[heading]))
        else:
            blocks.append(_section(heading, added.get(heading, ["(not in the legacy MiniPRD)"])))
    return _join(blocks + others)


def mission_body(text: str, intent: str) -> str:
    """Return the mission SPEC.md body: the SuperPRD with `## Intent`, `## Slices`, `## Status`."""
    h1, preamble, sections = split_sections(text)
    names = {heading for heading, _ in sections}
    title_line = h1 if h1 and h1.startswith("# SuperPRD: ") else f"# SuperPRD: {intent}"
    blocks = [[title_line]]
    if h1 and h1 != title_line:
        blocks.append([h1])
    blocks.append(_trim(preamble))
    if "## Intent" not in names:
        blocks.append(_section("## Intent", [intent]))
    blocks += [_section(heading, body) for heading, body in sections]
    if "## Slices" not in names:
        blocks.append(["## Slices"])
    if "## Status" not in names:
        blocks.append(_section("## Status", ["- compiled"]))
    return _join(blocks)


def project_body(text: str, intent: str, name: str, source: str) -> str:
    """Return the root SPEC.md body from a baseline SuperPRD (`## Intent`, `## Product Context`)."""
    h1, preamble, sections = split_sections(text)
    names = {heading for heading, _ in sections}
    blocks = [[f"# Project - {name}"]]
    if "## Intent" not in names:
        blocks.append(_section("## Intent", [intent]))
    note = f"Migrated from `{source}`" + (f" ({h1.lstrip('# ').strip()})." if h1 else ".")
    blocks.append([note])
    blocks.append(_trim(preamble))
    blocks += [_section(heading, body) for heading, body in sections]
    if "## Product Context" not in names:
        blocks.append(_section("## Product Context", ["(not in the baseline SuperPRD)"]))
    return _join(blocks)


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------


def adr_dirs(top: Path) -> list[str]:
    """Return every `docs/adr` directory under the work tree (skipping .git, vendored trees)."""
    found = []
    for dirpath, dirnames, _ in os.walk(top):
        dirnames[:] = sorted(
            d for d in dirnames if d not in ADR_SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
        )
        here = Path(dirpath)
        if here.name == "adr" and here.parent.name == "docs":
            found.append(_rel(here, top))
    return found


def scan_adr_dirs(top: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Task 5 (R25): return the importable docs/adr dirs and the skipped ones with their reason.

    A dir is skipped when it lies inside a gitlink (`git ls-files -s` mode 160000), inside a
    nested repository (it or an ancestor below the top has its own `.git`), or when no file under
    it is tracked. An importable dir's context is the path before `/docs/adr` (None at the root).
    """
    proc = git(top, "ls-files", "-s", "-z")
    gitlinks: list[str] = []
    tracked: list[str] = []
    if proc.returncode == 0:
        for record in proc.stdout.split("\0"):
            meta, _, path = record.partition("\t")
            if path:
                (gitlinks if meta.split(" ", 1)[0] == "160000" else tracked).append(path)
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for rel in adr_dirs(top):
        reason = _adr_skip_reason(top, rel, gitlinks, tracked)
        if reason:
            skipped.append({"dir": rel, "reason": reason})
            continue
        parent = rel[: -len(ROOT_ADR_DIR)].rstrip("/")
        candidates.append({"dir": rel, "context": parent or None})
    return candidates, skipped


def _adr_skip_reason(top: Path, rel: str, gitlinks: list[str], tracked: list[str]) -> str | None:
    for link in gitlinks:
        if rel == link or rel.startswith(link + "/"):
            return f"inside the submodule {link} (a gitlink)"
    parts = rel.split("/")
    for depth in range(1, len(parts) + 1):
        sub = "/".join(parts[:depth])
        if os.path.lexists(top / sub / ".git"):
            return f"inside the nested repository {sub}"
    if not any(path.startswith(rel + "/") for path in tracked):
        return "an untracked tree (no file under it is tracked)"
    return None


def _block_missing(path: Path, block_id: str) -> bool:
    try:
        return hyper_blocks.find_block(hyper_blocks.check_file(path), block_id) is None
    except hyper_blocks.BlocksError:
        return True


def detect(top: Path) -> list[tuple[str, str]]:
    """Return (signal, detail) for every DETECT signal; writes nothing."""
    signals: list[tuple[str, str]] = []
    units = legacy_units(top)
    for _, unit in sorted(units.items()):
        if unit.compiled:
            signals.append(("flat_compiled_miniprd", unit.compiled))
    for _, unit in sorted(units.items()):
        if unit.archived:
            signals.append(("archived_miniprd", unit.archived))
    for rel in adr_dirs(top):
        signals.append(("adr_dir", rel))
    memory = top / ".agents" / "memory"
    if memory.is_dir():
        for path in sorted(memory.glob("*.md")):
            if path.is_file() and sha256_file(path) not in MEMORY_TEMPLATE_SHAS:
                signals.append(("memory_file", _rel(path, top)))
    ledger = top / "spec" / "compiled" / "architecture.yml"
    if ledger.is_file():
        try:
            data = yaml.safe_load(_read_text(ledger))
            if not isinstance(data, dict) or "schema" not in data:
                signals.append(("legacy_ledger", "spec/compiled/architecture.yml (no schema key)"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            first = str(exc).splitlines()[0] if str(exc) else "parse error"
            signals.append(
                ("legacy_ledger", f"spec/compiled/architecture.yml (does not parse: {first})")
            )
    proc = tool(top, "hyper_scaffold.py", "check", "--json")
    try:
        items = json.loads(proc.stdout).get("items", [])
    except ValueError:
        items = []
        signals.append(("scaffold", f"hyper_scaffold.py check exited {proc.returncode}"))
    for item in items:
        if item.get("class") == "missing":
            signals.append(("missing_scaffold", item.get("path", "")))
        elif item.get("class") == "foreign":
            signals.append(("foreign_root_item", item.get("path", "")))
    for name in INSTRUCTION_FILES:
        path = top / name
        if path.is_file() and _block_missing(path, hyper_blocks.INSTRUCTION_BLOCK_ID):
            signals.append(("missing_hacf_block", name))
    if _block_missing(top / ".gitignore", hyper_blocks.GITIGNORE_BLOCK_ID):
        signals.append(("missing_hacf_block", ".gitignore"))
    if _block_missing(top / ".agentignore", hyper_blocks.AGENTIGNORE_BLOCK_ID):
        signals.append(("missing_firewall_rules", ".agentignore"))
    active = top / ".agents" / "memory" / "activeContext.md"
    if active.is_file():
        _, _, sections = split_sections(_read_text(active))
        for heading, body in sections:
            if heading == FIELD_NOTES_HEADING and any(line.startswith("- ") for line in body):
                signals.append(("legacy_field_notes", ".agents/memory/activeContext.md"))
    return signals


# ---------------------------------------------------------------------------
# Lock, preflight, journal
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def migrate_lock(top: Path, timeout: float = LOCK_TIMEOUT) -> Iterator[Path]:
    """Hold `<git-common-dir>/hacf/locks/migrate.lock` (created on first use); exit 3 on timeout."""
    lock = common_dir(top) / LOCK_REL
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
                    raise MigrateError(
                        f"timed out after {timeout:g}s waiting for {lock}", EXIT_LOCK_TIMEOUT
                    ) from None
                time.sleep(LOCK_POLL)
        yield lock
    finally:
        os.close(fd)


def preflight_git(top: Path) -> None:
    """Refuse (exit 5) outside a work tree or while a merge or rebase is in progress."""
    proc = git(top, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise MigrateError("not inside a git work tree")
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
        proc = git(top, "rev-parse", "--git-path", name)
        path = Path(proc.stdout.strip())
        path = path if path.is_absolute() else top / path
        if proc.returncode == 0 and os.path.lexists(path):
            raise MigrateError(f"a merge or rebase is in progress ({name}); finish it first")


def touched_paths(plan: dict[str, Any]) -> list[str]:
    """Return the repo paths whose tracked files the plan reads or rewrites."""
    paths = {"SPEC.md", "project.yaml", "workspace.yaml", ".gitignore", "missions"}
    for action in plan["actions"]:
        for key in ("source", "target"):
            if action.get(key):
                paths.add(action[key])
        paths.update(action.get("paths", []))
        paths.update(action.get("files", []))
        if action.get("op") == "firewall" and action.get("settings"):
            paths.add(".claude/settings.json")
    return sorted(paths)


def preflight_clean(top: Path, plan: dict[str, Any]) -> None:
    """Refuse (exit 5) when a tracked file the plan touches has uncommitted changes."""
    proc = git(top, "status", "--porcelain=v1", "--untracked-files=no", "--", *touched_paths(plan))
    if proc.returncode != 0:
        raise MigrateError(f"git status failed: {proc.stderr.strip()}")
    dirty = [line[3:] for line in proc.stdout.splitlines() if line.strip()]
    if dirty:
        raise MigrateError(
            "tracked files the migration touches have uncommitted changes (commit them, or pass "
            "--allow-dirty): " + ", ".join(dirty)
        )


def preflight_scaffold(top: Path) -> None:
    """Refuse (exit 5) when `hyper_scaffold.py check` finds a foreign root item."""
    proc = tool(top, "hyper_scaffold.py", "check")
    if proc.returncode == 5:
        raise MigrateError(
            "a foreign root item stops the migration (HACF never overwrites it):\n"
            + proc.stdout.rstrip()
        )
    if proc.returncode != 0:
        raise MigrateError(
            f"hyper_scaffold.py check exited {proc.returncode}: {proc.stderr.strip()}"
        )


def journal_path(top: Path) -> Path:
    """Return the journal path of the project."""
    return top / JOURNAL_REL


def load_journal(path: Path) -> dict[str, Any] | None:
    """Return the parsed journal, or None when it does not exist."""
    try:
        data = json.loads(_read_text(path))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise MigrateError(f"{path} is not a readable journal ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != JOURNAL_SCHEMA:
        raise MigrateError(f"{path} is not an hacf migration journal")
    return data


def write_journal(path: Path, journal: dict[str, Any]) -> None:
    """Write the journal atomically: temp file, fsync, os.replace, fsync the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    journal["updated"] = _runtime.utc_now_iso()
    data = (json.dumps(journal, indent=2, sort_keys=False) + "\n").encode()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".journal-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def record_changes(
    journal: dict[str, Any],
    before: tuple[dict[str, str], set[str]],
    after: tuple[dict[str, str], set[str]],
) -> dict[str, Any]:
    """Fold one step's snapshot diff into the journal; return the step's outputs record."""
    old_files, old_dirs = before
    new_files, new_dirs = after
    outputs, created, deleted = [], [], []
    for key in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(key), new_files.get(key)
        if old == new:
            continue
        outputs.append({"path": key, "before": old, "after": new})
        entry = journal["files"].setdefault(key, {"before": old})
        entry["after"] = new
        if old is None:
            created.append(key)
        if new is None:
            deleted.append(key)
    for folder in sorted(new_dirs - old_dirs):
        if folder not in journal["created_dirs"]:
            journal["created_dirs"].append(folder)
    journal["created"] = sorted(
        k for k, v in journal["files"].items() if v["before"] is None and v["after"] is not None
    )
    return {"outputs": outputs, "created": created, "deleted": deleted}


def drifted(top: Path, journal: dict[str, Any], keys: list[str] | None = None) -> list[str]:
    """Return the journal paths whose current sha differs from the recorded post-step sha."""
    found = []
    for key in keys if keys is not None else sorted(journal["files"]):
        entry = journal["files"].get(key)
        if entry is None or entry.get("before") == entry.get("after"):
            continue
        if sha256_file(key_path(top, key)) != entry.get("after"):
            found.append(key)
    return found


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def _files_under(top: Path, rel: str) -> list[str]:
    path = top / rel
    if path.is_symlink() or path.is_file():
        return [rel]
    if not path.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            child = Path(dirpath) / name
            if child.is_file() and not child.is_symlink():
                found.append(_rel(child, top))
    return found


def write_backup(
    top: Path, backup_dir: Path, gitignore_before: bytes | None
) -> tuple[dict[str, str], dict[str, str]]:
    """Write the four tarballs (Python tarfile) and SHA256SUMS; return (sums, path index).

    `.gitignore` is stored with its pre-apply bytes (the managed block is written first, so the
    backup dir is ignored); a file that did not exist is simply absent from the tarballs.
    """
    backup_dir.mkdir(parents=True, exist_ok=False)
    groups: dict[str, list[str]] = {
        "spec.tar.gz": [f for r in SPEC_BACKUP_ROOTS for f in _files_under(top, r)],
        "adr.tar.gz": [f for r in adr_dirs(top) for f in _files_under(top, r)],
        "memory.tar.gz": _files_under(top, ".agents/memory"),
        "instructions.tar.gz": [
            f for r in INSTRUCTION_BACKUP_ROOTS if r != ".gitignore" for f in _files_under(top, r)
        ],
    }
    index: dict[str, str] = {}
    for name in TARBALLS:
        with tarfile.open(str(backup_dir / name), "w:gz") as archive:
            for rel in groups[name]:
                archive.add(str(top / rel), arcname=rel, recursive=False)
                index[rel] = name
            if name == "instructions.tar.gz":
                if gitignore_before is not None:
                    info = tarfile.TarInfo(".gitignore")
                    info.size = len(gitignore_before)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(gitignore_before))
                    index[".gitignore"] = name
                attributes = key_path(top, GIT_ATTRIBUTES_KEY)
                if attributes.is_file():
                    archive.add(str(attributes), arcname=GIT_ATTRIBUTES_KEY, recursive=False)
                    index[GIT_ATTRIBUTES_KEY] = name
    sums = {name: sha256_file(backup_dir / name) or "" for name in TARBALLS}
    text = "".join(f"{sums[name]}  {name}\n" for name in TARBALLS)
    (backup_dir / "SHA256SUMS").write_text(text, encoding="utf-8")
    corrupt = os.environ.get(TEST_CORRUPT_ENV)
    if corrupt in TARBALLS:  # test hook (Test 8): damage one byte after the backup step
        target = backup_dir / corrupt
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0xFF
        target.write_bytes(bytes(data))
    return sums, index


def verify_backup(backup_dir: Path) -> None:
    """Check every tarball against SHA256SUMS with hashlib (never `sha256sum -c`); exit 5."""
    sums_path = backup_dir / "SHA256SUMS"
    try:
        lines = _read_text(sums_path).splitlines()
    except FileNotFoundError:
        raise MigrateError(f"{sums_path} is missing; the backup cannot be verified") from None
    expected = {}
    for line in lines:
        digest, _, name = line.partition("  ")
        expected[name.strip()] = digest.strip()
    bad = [name for name in TARBALLS if sha256_file(backup_dir / name) != expected.get(name)]
    if bad:
        raise MigrateError(
            f"backup {backup_dir} does not match its SHA256SUMS ({', '.join(bad)}); nothing else "
            "was written"
        )


def backup_member(top: Path, journal: dict[str, Any], key: str) -> bytes | None:
    """Return the backed-up bytes of a journal path, or None when the backup lacks it."""
    name = journal["backup"]["index"].get(key)
    if name is None:
        return None
    with tarfile.open(str(top / journal["backup"]["dir"] / name), "r:gz") as archive:
        try:
            member = archive.getmember(key)
        except KeyError:
            return None
        handle = archive.extractfile(member)
        return handle.read() if handle is not None else None


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def step_id(action: dict[str, Any]) -> str:
    """Return the stable journal id of a plan action."""
    target = action.get("slice") or action.get("mission") or action.get("target") or ""
    return f"{action['op']}:{target}" if target else action["op"]


def _check(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        code = EXIT_LOCK_TIMEOUT if proc.returncode == EXIT_LOCK_TIMEOUT else EXIT_CONFLICT
        detail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
        raise MigrateError(f"{what} exited {proc.returncode}: {' / '.join(detail[-5:])}", code)


def _driver_keys(top: Path) -> set[str]:
    proc = git(top, "config", "--local", "--get-regexp", r"^merge\.hacf-")
    return {line.split(" ", 1)[0] for line in proc.stdout.splitlines() if line.strip()}


def run_action(
    top: Path,
    action: dict[str, Any],
    scratch: Path,
    journal: dict[str, Any],
    interactive: bool = False,
) -> dict[str, Any]:
    """Run one plan action; return its journal inputs."""
    op = action["op"]
    if op in STORE_OPS:
        return STORE_OPS[op](top, action, scratch, journal, interactive)
    if op == "scaffold":
        keys_before = _driver_keys(top)
        try:
            _check(tool(top, "hyper_scaffold.py", "apply", "--yes"), "hyper_scaffold.py apply")
        finally:  # keys a failed scaffold added are journaled too, so rollback removes them
            added = sorted(_driver_keys(top) - keys_before)
            journal["git_config_added"] = sorted(
                set(journal.get("git_config_added", [])) | set(added)
            )
        return {"argv": ["hyper_scaffold.py", "apply", "--yes"], "git_config_added": added}
    if op == "project-spec":
        text = _read_text(top / action["source"])
        body = scratch / "project-body.md"
        body.write_text(
            project_body(text, action["intent"], top.name, action["source"]), encoding="utf-8"
        )
        argv = ["project", "update", "--intent", action["intent"], "--body-file", str(body)]
        _check(tool(top, "hyper_scope.py", *argv), "hyper_scope.py project update")
        return {"argv": ["hyper_scope.py", *argv[:-1], "<body>"], "source": action["source"]}
    if op == "mission-create":
        name = Path(action["mission"]).name
        argv = ["mission", "create", "--name", name, "--intent", action["intent"]]
        if action["source"]:
            body = scratch / f"mission-{name}.md"
            body.write_text(
                mission_body(_read_text(top / action["source"]), action["intent"]), encoding="utf-8"
            )
            argv += ["--body-file", str(body)]
        _check(tool(top, "hyper_scope.py", *argv), f"hyper_scope.py mission create {name}")
        made = hyper_openrig.read_frontmatter(top / action["mission"] / "SPEC.md").get("id")
        if made != action["id"]:
            raise MigrateError(f"{action['mission']} got id {made}, the plan said {action['id']}")
        return {"argv": ["hyper_scope.py", *argv[:6]], "source": action["source"]}
    if op == "slice-create":
        return _slice_create(top, action, scratch)
    if op == "mission-status":
        hyper_openrig.set_mission_status(top / action["mission"], action["status"])
        return {
            "call": f"hyper_openrig.set_mission_status({action['mission']}, {action['status']})"
        }
    if op == "archive":
        argv = ["--paths", *action["paths"], "--label", action["label"]]
        proc = tool(top, "archive_specs.py", *argv)
        _check(proc, "archive_specs.py --paths")
        moved = [
            line.split(" -> ", 1)[1]
            for line in proc.stdout.splitlines()
            if line.startswith("Archived: ")
        ]
        journal["archived"] = sorted(set(journal.get("archived", [])) | set(moved))
        return {"argv": ["archive_specs.py", *argv], "archived": moved}
    raise MigrateError(f"unknown plan action {op!r}", EXIT_USAGE)


def _slice_create(top: Path, action: dict[str, Any], scratch: Path) -> dict[str, Any]:
    text = _read_text(top / action["source"])
    slice_dir = action["slice"]
    name = Path(slice_dir).name
    body = scratch / f"slice-{name}.md"
    body.write_text(
        slice_body(
            text, action["intent"], action["source"], action["hacf_status"], action["depends_on"]
        ),
        encoding="utf-8",
    )
    argv = [
        "slice",
        "create",
        "--mission",
        action["mission"],
        "--name",
        name,
        "--intent",
        action["intent"],
    ]
    if action["node_id"]:
        argv += ["--node-id", action["node_id"]]
    if action["parent"]:
        argv += ["--parent", action["parent"]]
    if action["depends_on"]:
        argv += ["--depends-on", ",".join(action["depends_on"])]
    argv += ["--legacy-source", action["source"], "--legacy-order", str(action["legacy_order"])]
    _check(
        tool(top, "hyper_scope.py", *argv, "--body-file", str(body)),
        f"hyper_scope.py slice create {name}",
    )
    inputs: dict[str, Any] = {"argv": ["hyper_scope.py", *argv], "source": action["source"]}
    if action["hacf_status"] != "audited":
        return inputs
    proof_body = scratch / f"proof-{name}.md"
    proof_body.write_text(
        "# Legacy audit\n\n"
        f"Imported by hyper_migrate.py from `{action['source']}`, the MiniPRD that the v0.5.x "
        "/hyper-audit archived as `_AUDITED` after a PASS.\n",
        encoding="utf-8",
    )
    proof = [
        "proof", "--slice", slice_dir, "--artifact-type", "qa", "--verdict", "PASS",
        "--candidate-sha", action["candidate_sha"], "--money-evidence", action["source"],
        "--file", str(proof_body), "--name", PROOF_NAME,
    ]  # fmt: skip
    if action.get("swimlane_sha"):
        proof += ["--swimlane-sha", action["swimlane_sha"]]
    _check(tool(top, "hyper_spec.py", *proof), f"hyper_spec.py proof {name}")
    for event in ("executing", "executed", "audited"):
        _check(
            tool(top, "hyper_spec.py", "progress", "--slice", slice_dir, "--event", event),
            f"hyper_spec.py progress {name} {event}",
        )
    inputs["proof"] = ["hyper_spec.py", *proof[:-4], *proof[-2:]]
    inputs["events"] = ["executing", "executed", "audited"]
    return inputs


# ---------------------------------------------------------------------------
# APPLY steps 6-9 (S13b): ledger, lore, memory and notes, instruction files, blocks, firewall
# ---------------------------------------------------------------------------

IMPORTED_RE = re.compile(r"^imported (\S+) -> (\S+) \(")


def _ask(question: str) -> bool:
    """Interactive only: True for a typed y or yes (the default is no)."""
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _report(line: str) -> str:
    print(f"report: {line}")
    return line


def _echo(proc: subprocess.CompletedProcess) -> list[str]:
    """Print a tool's stdout lines (indented, without `owned:` lines); return them."""
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith("owned: "):
            print(f"  {line}")
    return lines


def _write_file(path: Path, data: bytes) -> None:
    """Replace a file atomically (temp file in the same directory, then os.replace)."""
    tmp = path.with_name(f".{path.name}.hacf-migrate")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _slice_id(top: Path, spec: str) -> str | None:
    """Return the dot-ID in a migrated slice SPEC.md's frontmatter, or None."""
    value = hyper_openrig.read_frontmatter(top / spec).get("id")
    return value if hyper_openrig.is_slice_dot_id(value) else None


def _ledger_missing(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    return {"report": _report(f"{LEDGER_REL} is missing; run /hyper-discover (not created)")}


def _ledger_repair(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 3: the dry-run diff is printed and journaled; an interactive run asks again."""
    dry = tool(top, "hyper_ledger.py", "repair-text", "--dry-run")
    _check(dry, "hyper_ledger.py repair-text --dry-run")
    print(dry.stdout.rstrip())
    if interactive and not _ask(f"Write this repair to {LEDGER_REL}?"):
        raise MigrateError(
            f"the repair of {LEDGER_REL} was declined; the ledger does not parse, so the "
            "migration stops here (apply again to be asked again)",
            EXIT_REFUSED,
        )
    _check(tool(top, "hyper_ledger.py", "repair-text"), "hyper_ledger.py repair-text")
    return {
        "argv": [
            ["hyper_ledger.py", "repair-text", "--dry-run"],
            ["hyper_ledger.py", "repair-text"],
        ],
        "diff": dry.stdout,
        "confirmed_by": "the repair prompt" if interactive else "the apply confirmation",
    }


def _ledger_migrate_v2(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    dry = tool(top, "hyper_ledger.py", "migrate-v2", "--dry-run")
    _check(dry, "hyper_ledger.py migrate-v2 --dry-run")
    _check(tool(top, "hyper_ledger.py", "migrate-v2"), "hyper_ledger.py migrate-v2")
    return {
        "argv": [["hyper_ledger.py", "migrate-v2", "--dry-run"], ["hyper_ledger.py", "migrate-v2"]],
        "summary": dry.stdout,
    }


def _ledger_spec_refs(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 4 (R28): repoint associated_file and append the slice dot-ID via ensure-node."""
    try:
        data = yaml.safe_load(_read_text(top / LEDGER_REL))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise MigrateError(f"{LEDGER_REL} does not parse after step 6 ({exc})") from None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    commands: list[list[str]] = []
    reports: list[str] = []
    for node in nodes if isinstance(nodes, list) else []:
        legacy = node.get("associated_file") if isinstance(node, dict) else None
        spec = journal["legacy_map"].get(legacy) if isinstance(legacy, str) else None
        if spec is None:
            continue
        slice_id = _slice_id(top, spec)
        if slice_id is None:
            reports.append(_report(f"node {node.get('id')}: {spec} has no slice dot-ID; left"))
            continue
        argv = [
            "ensure-node", "--id", str(node.get("id")), "--dimension", str(node.get("dimension")),
            "--associated-file", spec, "--spec-refs", slice_id,
        ]  # fmt: skip
        _check(tool(top, "hyper_ledger.py", *argv), f"hyper_ledger.py ensure-node {node.get('id')}")
        commands.append(["hyper_ledger.py", *argv])
        print(f"  ensure-node {node.get('id')}: associated_file {spec}, spec_refs += {slice_id}")
    return {"commands": commands, "reports": reports}


def _adr_scan(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 5: report each skipped docs/adr dir and why."""
    for skipped in action["skipped"]:
        _report(f"{skipped['dir']}: not imported ({skipped['reason']})")
    return {"dirs": action["dirs"], "skipped": action["skipped"]}


def _stub_title(path: Path) -> str:
    """Return the title of the importer's stub (`# Moved: <title>`), else the file's stem."""
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in _read_text(path).split("\n")[:3]:
            if line.startswith("# Moved: "):
                return line[len("# Moved: ") :].strip()
    return path.stem


def migration_stub(old: str, new: str, node_id: str, title: str, backup_dir: str) -> str:
    """Task 8: the per-file redirect stub left at a moved ADR's old path.

    Its first line is hyper_lore's redirect marker, so a later `import --adr-dirs` skips it.
    """
    folder = Path(old).parent.as_posix()
    link = os.path.relpath(new, folder).replace(os.sep, "/")
    return (
        f"{hyper_lore.REDIRECT_MARK}{new} -->\n"
        f"# Moved: {title}\n\n"
        f"This ADR moved to [{new}]({link}), lore node {node_id} in `lore_hypergraph.yml`, when "
        "`hyper_migrate.py apply` migrated this project to HACF v0.6.0 (`hyper_lore.py import "
        f"--adr-dirs {folder}`). Its original bytes are in `{backup_dir}/adr.tar.gz`.\n"
    )


def _adr_import(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Tasks 6-8: select, import through hyper_lore.py, then leave a stub per moved file."""
    folder, context = action["target"], action["context"]
    if interactive:
        where = f" (context {context})" if context else ""
        if not _ask(f"Import the ADRs of {folder}{where} into lore?"):
            return {"dir": folder, "selected": False, "report": _report(f"{folder}: declined")}
    elif folder != ROOT_ADR_DIR:
        line = _report(
            f"{folder}: not imported (a non-interactive run imports only {ROOT_ADR_DIR})"
        )
        return {"dir": folder, "selected": False, "report": line}
    argv = ["import", "--adr-dirs", folder, *(["--context", context] if context else [])]
    proc = tool(top, "hyper_lore.py", *argv)
    _check(proc, f"hyper_lore.py import --adr-dirs {folder}")
    lines = _echo(proc)
    imported = [m.groups() for m in map(IMPORTED_RE.match, lines) if m]
    left = [line for line in lines if line.startswith("skipped ")]
    graph = yaml.safe_load(_read_text(hyper_lore.graph_path(top))) if imported else {}
    pointers = {
        node.get("id"): (node.get("pointer") or {}).get("path")
        for node in (graph or {}).get("nodes") or []
        if isinstance(node, dict)
    }
    stubs = []
    for old, node_id in imported:
        new = pointers.get(node_id)
        if not isinstance(new, str):
            raise MigrateError(f"hyper_lore.py imported {old} as {node_id}, which has no pointer")
        path = top / old
        stub = migration_stub(old, new, node_id, _stub_title(path), journal["backup"]["dir"])
        _write_file(path, stub.encode("utf-8"))
        stubs.append({"path": old, "to": new, "id": node_id})
    return {
        "argv": ["hyper_lore.py", *argv],
        "selected": True,
        "stubs": stubs,
        "left_in_place": left,
    }


def _insert_block(doc: Any, heading: str, block_id: str, lines: list[bytes]) -> bytes:
    """Return doc's bytes with a new Markdown HACF block at the end of the `heading` section.

    The block goes after the section's own text and before any HACF region that ends it; a
    missing heading is appended with the block. Every other byte stays.
    """
    eol, md = doc.eol, hyper_blocks.MARKDOWN
    block = [
        hyper_blocks.marker(md, "BEGIN", block_id) + eol,
        *(line + eol for line in lines),
        hyper_blocks.marker(md, "END", block_id) + eol,
    ]
    out = list(doc.lines)
    texts = [line.decode("utf-8").rstrip("\r\n") for line in out]
    flags = _fence_flags(texts)
    start = next(
        (i for i, (t, f) in enumerate(zip(texts, flags)) if not f and t.rstrip() == heading), None
    )
    if start is None:
        if out and not out[-1].endswith(b"\n"):
            out[-1] += eol
        if out and texts[-1].strip():
            out.append(eol)
        return doc.bom + b"".join([*out, heading.encode("utf-8") + eol, eol, *block])
    end = next(
        (i for i in range(start + 1, len(texts)) if not flags[i] and texts[i].startswith("## ")),
        len(texts),
    )
    stop = next((i for i in range(start + 1, end) if texts[i].startswith("<!-- BEGIN HACF ")), end)
    at = stop
    while at > start + 1 and not texts[at - 1].strip():
        at -= 1
    if at == len(out) and out and not out[-1].endswith(b"\n"):
        out[-1] += eol
    insert = [eol, *block]
    if at < len(out) and at == stop:
        insert.append(eol)  # keep one empty line before what follows
    return doc.bom + b"".join(out[:at] + insert + out[at:])


def _product_context(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 10: productContext.md into SPEC.md's `migrated-product-context` block (R26)."""
    body = (top / action["source"]).read_bytes()
    spec = top / action["target"]
    try:
        data = hyper_blocks.read_optional(hyper_blocks.guard(spec))
        if data is None:
            raise MigrateError(f"{action['target']} is missing; step 1 (scaffold) creates it")
        doc = hyper_blocks.parse_doc(data)
        if hyper_blocks.find_block(hyper_blocks.scan(doc, hyper_blocks.MARKDOWN), PRODUCT_BLOCK_ID):
            outcome = hyper_blocks.upsert(spec, PRODUCT_BLOCK_ID, body, assume_yes=True)
            written = outcome.action
        else:
            new = _insert_block(
                doc, PRODUCT_HEADING, PRODUCT_BLOCK_ID, hyper_blocks.body_lines(body)
            )
            hyper_blocks.scan(hyper_blocks.parse_doc(new), hyper_blocks.MARKDOWN)  # grammar check
            hyper_blocks.cas_write(spec, data, new)
            written = "inserted"
    except hyper_blocks.BlocksError as exc:
        raise MigrateError(f"{action['target']}: {exc}", exc.code) from None
    print(f"  {written}: {action['target']} block {PRODUCT_BLOCK_ID} under {PRODUCT_HEADING}")
    return {"source": action["source"], "target": action["target"], "block": PRODUCT_BLOCK_ID}


def _system_patterns(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 11: systemPatterns.md becomes a PROPOSED canon (body in lore/proposed/)."""
    argv = [
        "propose", "--type", "canon", "--title", CANON_TITLE,
        "--body-file", action["source"], "--source", action["source"],
    ]  # fmt: skip
    proc = tool(top, "hyper_lore.py", *argv)
    _check(proc, "hyper_lore.py propose --type canon")
    return {"argv": ["hyper_lore.py", *argv], "result": _echo(proc)}


def _memory_remove(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 12: remove template-identical memory files (their bytes are in memory.tar.gz)."""
    removed = []
    for rel in action["paths"]:
        path = top / rel
        if path.is_symlink() or sha256_file(path) not in MEMORY_TEMPLATE_SHAS:
            if os.path.lexists(path):
                _report(f"{rel}: no longer a template; kept")
            continue
        path.unlink()
        removed.append(rel)
        print(f"  removed {rel} (template-identical; kept in memory.tar.gz)")
    return {"removed": removed}


def _legacy_notes(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 13: move mapped Field Notes (legacy) entries to their mission NOTES.md."""
    path = top / action["source"]
    text = _read_text(path)
    lines = text.split("\n")
    flags = _fence_flags(lines)
    moved: list[int] = []
    notes: list[dict[str, str]] = []
    kept: list[str] = []
    inside = False
    for index, (line, fenced) in enumerate(zip(lines, flags)):
        bare = line.rstrip("\r")
        if not fenced and bare.startswith("## "):
            inside = bare.rstrip() == FIELD_NOTES_HEADING
            continue
        if not inside or fenced or not bare.startswith("- "):
            continue
        match = LEGACY_NOTE_RE.match(bare)
        spec = journal["legacy_map"].get(match.group(4)) if match else None
        slice_id = _slice_id(top, spec) if spec else None
        if match is None or spec is None or slice_id is None:
            why = "not a note line" if match is None else f"no migrated slice for {match.group(4)}"
            kept.append(_report(f"{action['source']}: kept the legacy note ({why}): {bare}"))
            continue
        date, kind, agent, _, note, anchors = match.groups()
        mission = Path(spec).parents[2].as_posix()
        args = {
            "legacy": False, "mission": mission, "source": None, "text": note, "kind": kind,
            "agent": agent, "slice": slice_id, "date": date,
            "anchors": [] if anchors.strip() == "none" else [a.strip() for a in anchors.split(",")],
        }  # fmt: skip
        try:
            hyper_lore.do_note(top, hyper_lore.normalize_note(args))
        except hyper_lore.LoreError as exc:
            raise MigrateError(f"hyper_lore note --mission {mission}: {exc}") from None
        moved.append(index)
        notes.append({"from": bare, "mission": mission, "slice": slice_id})
        print(f"  moved a legacy note to {mission}/NOTES.md")
    if moved:
        if _read_text(path) != text:
            raise MigrateError(f"{action['source']} changed while its notes were moved")
        skip = set(moved)
        new = "\n".join(line for index, line in enumerate(lines) if index not in skip)
        _write_file(path, new.encode("utf-8"))
    return {"moved": notes, "kept": kept, "writer": "hyper_lore.do_note (note --mission)"}


def _lore_index(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 9: CANON.md and MAP.md are regenerated by hyper_lore.py index, never written here."""
    proc = tool(top, "hyper_lore.py", "index")
    _check(proc, "hyper_lore.py index")
    return {"argv": ["hyper_lore.py", "index"], "result": _echo(proc)}


def _instruction_file(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 14 (R9): the whole-file decision of hyper_blocks.py; never appended to."""
    name = action["target"]
    block = f"{TEMPLATES_REL}/{name}"
    if not (top / block).is_file():
        return {
            "report": _report(f"{name}: left unchanged; {block} is missing (run HACF-install.sh)")
        }
    argv = ["instruction", name, "--block-file", block, *([] if interactive else ["--yes"])]
    proc = tool(top, "hyper_blocks.py", *argv)
    _check(proc, f"hyper_blocks.py instruction {name}")
    outcome = proc.stdout.strip()
    print(f"  {outcome}")
    report = None
    if outcome.startswith("sidecar:"):
        report = _report(
            f"{name}: customized, so it was left unchanged; merge "
            f"{Path(name).stem}.hacf-v0.6.0.md into it, then delete that file"
        )
    return {"argv": ["hyper_blocks.py", *argv], "outcome": outcome, "report": report}


def _blocks(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 15: the `.gitignore` and `.agentignore` HACF blocks (bytes outside them unchanged)."""
    bodies = {
        ".gitignore": (hyper_blocks.GITIGNORE_BLOCK_ID, hyper_scaffold.GITIGNORE_LINES),
        ".agentignore": (hyper_blocks.AGENTIGNORE_BLOCK_ID, AGENTIGNORE_LINES),
    }
    results = []
    for rel in action["files"]:
        block_id, lines = bodies[rel]
        try:
            outcome = hyper_blocks.upsert(top / rel, block_id, "".join(f"{x}\n" for x in lines))
        except hyper_blocks.BlocksError as exc:
            raise MigrateError(f"{rel}: {exc}", exc.code) from None
        results.append(f"{outcome.action}: {rel} block {block_id}")
        print(f"  {results[-1]}")
    return {"results": results}


def _drivers_check(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 16 (R10): check the merge-drivers block; nothing writes a .gitattributes."""
    path = key_path(top, GIT_ATTRIBUTES_KEY)
    proc = tool(top, "hyper_blocks.py", "check", str(path))
    found = f"MANAGED BLOCK {hyper_blocks.ATTRIBUTES_BLOCK_ID}"
    present = proc.returncode == 0 and found in proc.stdout
    report = None
    if not present:
        report = _report(
            f"the {hyper_blocks.ATTRIBUTES_BLOCK_ID} block is missing from "
            "<git-common-dir>/info/attributes; run `python3 .agents/scripts/hyper_lore.py "
            "install-drivers` (HACF never writes a .gitattributes)"
        )
    return {"argv": ["hyper_blocks.py", "check", GIT_ATTRIBUTES_KEY], "present": present,
            "report": report}  # fmt: skip


def _firewall(top, action, scratch, journal, interactive) -> dict[str, Any]:  # noqa: ANN001
    """Task 17: merge S12b's settings fragment (when .claude/ exists), then render the IDEs."""
    argvs: list[list[str]] = []
    reports: list[str] = []
    if action["settings"]:
        if (top / SETTINGS_FRAGMENT_REL).is_file():
            argv = ["merge-settings", SETTINGS_FRAGMENT_REL]
            proc = tool(top, "hyper_firewall.py", *argv)
            _check(proc, "hyper_firewall.py merge-settings")
            _echo(proc)
            argvs.append(["hyper_firewall.py", *argv])
        else:
            reports.append(
                _report(f".claude/settings.json: not merged; {SETTINGS_FRAGMENT_REL} is missing")
            )
    if action["ides"]:
        argv = ["render", "--ides", ",".join(action["ides"])]
        proc = tool(top, "hyper_firewall.py", *argv)
        _check(proc, "hyper_firewall.py render")
        _echo(proc)
        argvs.append(["hyper_firewall.py", *argv])
    else:
        reports.append(
            _report("firewall: no IDE selected or present; run hyper_firewall.py render")
        )
    return {"argv": argvs, "reports": reports}


STORE_OPS = {
    "ledger-missing": _ledger_missing,
    "ledger-repair": _ledger_repair,
    "ledger-migrate-v2": _ledger_migrate_v2,
    "ledger-spec-refs": _ledger_spec_refs,
    "adr-scan": _adr_scan,
    "adr-import": _adr_import,
    "product-context": _product_context,
    "system-patterns": _system_patterns,
    "memory-remove": _memory_remove,
    "legacy-notes": _legacy_notes,
    "lore-index": _lore_index,
    "instruction-file": _instruction_file,
    "blocks": _blocks,
    "drivers-check": _drivers_check,
    "firewall": _firewall,
}


def confirm(plan: dict[str, Any], assume_yes: bool) -> None:
    """R25.4: --yes is refused under HACF_AUTONOMOUS=1; otherwise a TTY must type `yes`."""
    if assume_yes:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise MigrateError(
            "apply needs a TTY confirmation (or --yes, which is refused under HACF_AUTONOMOUS=1)",
            EXIT_REFUSED,
        )
    for line in plan_summary(plan):
        print(line)
    answer = input("Type yes to migrate this project: ").strip()
    if answer != "yes":
        raise MigrateError("not confirmed; nothing was written", EXIT_REFUSED)


def ask_unclassified(top: Path, plan: dict[str, Any]) -> dict[str, str]:
    """Interactive only: ask how to treat each unclassified SuperPRD (default: skip)."""
    overrides: dict[str, str] = {}
    for rel in plan["unclassified"]:
        answer = input(f"{rel} is unclassified. Treat it as [b]aseline, [f]eature or [s]kip? ")
        choice = answer.strip().lower()[:1]
        if choice == "b":
            overrides[rel] = BASELINE
        elif choice == "f":
            overrides[rel] = FEATURE
    return overrides


def cmd_apply(ns: argparse.Namespace) -> int:
    """Confirm, preflight, back up, then run (or resume) the plan's steps."""
    top = repo_top()
    if ns.yes and os.environ.get(AUTONOMOUS_ENV) == "1":
        raise MigrateError(
            "--yes is refused under HACF_AUTONOMOUS=1 (R25.4); nothing was written", EXIT_REFUSED
        )
    preflight_git(top)
    jpath = journal_path(top)
    journal = load_journal(jpath)
    options = {"include_archived": ns.include_archived, "no_chain": ns.no_chain}
    if journal is None:
        plan = build_plan(top, ns.include_archived, chain=not ns.no_chain)
        if plan["unclassified"] and not ns.yes and sys.stdin.isatty() and sys.stdout.isatty():
            overrides = ask_unclassified(top, plan)
            if overrides:
                plan = build_plan(top, ns.include_archived, not ns.no_chain, overrides)
        confirm(plan, ns.yes)
    else:
        if journal.get("options") != options:
            raise MigrateError(
                f"{JOURNAL_REL} was made with other options ({journal.get('options')}); "
                "rerun with those, or roll back first"
            )
        plan = {"actions": journal["plan"], "twins": [], "reports": []}
        confirm(plan, ns.yes)
    with migrate_lock(top):
        journal = load_journal(jpath)  # re-read under the lock
        if journal is None:
            if not ns.allow_dirty:
                preflight_clean(top, plan)
            preflight_scaffold(top)
            journal = start_journal(top, plan, options)
            write_journal(jpath, journal)
        interactive = not ns.yes and sys.stdin.isatty() and sys.stdout.isatty()
        return run_steps(top, journal, jpath, interactive)


def start_journal(top: Path, plan: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Ensure the .gitignore block, write and verify the backup; return the new journal."""
    gitignore = top / ".gitignore"
    gitignore_before = gitignore.read_bytes() if gitignore.is_file() else None
    extra = extra_roots(plan["actions"])
    before = snapshot(top, extra)
    body = "".join(line + "\n" for line in hyper_scaffold.GITIGNORE_LINES)
    try:
        hyper_blocks.upsert(gitignore, hyper_blocks.GITIGNORE_BLOCK_ID, body)
    except hyper_blocks.BlocksError as exc:
        raise MigrateError(f".gitignore: {exc}", exc.code) from None
    after = snapshot(top, extra)
    stamp = _runtime.utc_now_iso()
    backup_dir = top / MIGRATIONS_REL / stamp / "backup"
    sums, index = write_backup(top, backup_dir, gitignore_before)
    verify_backup(backup_dir)
    journal: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "version": "v0.6.0",
        "started": stamp,
        "updated": stamp,
        "options": options,
        "backup": {
            "dir": _rel(backup_dir, top),
            "sha256sums": sums,
            "index": dict(sorted(index.items())),
        },
        "plan": plan["actions"],
        "steps": [],
        "files": {},
        "created": [],
        "created_dirs": [],
        "git_config_added": [],
        "archived": [],
        "legacy_map": plan.get("legacy_map", {}),
    }
    change = record_changes(journal, before, after)
    journal["steps"].append(
        {"id": "gitignore-block", "op": "gitignore-block", "status": "done", "at": stamp,
         "inputs": {"file": ".gitignore", "block": hyper_blocks.GITIGNORE_BLOCK_ID}, **change}
    )  # fmt: skip
    return journal


def remember(top: Path, store: dict[str, bytes], key: str, sha: str | None) -> None:
    """Keep the bytes of a journal path in the content store when they still hash as `sha`."""
    if sha is None or sha in store:
        return
    path = key_path(top, key)
    if path.is_symlink() or not path.is_file():
        return
    try:
        data = path.read_bytes()
    except OSError:
        return
    if sha256_bytes(data) == sha:
        store[sha] = data


def content_store(top: Path, journal: dict[str, Any]) -> dict[str, bytes]:
    """Return {sha: bytes} of the files apply has written so far (what a failed step undoes to)."""
    store: dict[str, bytes] = {}
    for key, entry in journal["files"].items():
        if entry.get("before") != entry.get("after"):
            remember(top, store, key, entry.get("after"))
    return store


def undo_step(
    top: Path,
    journal: dict[str, Any],
    before: tuple[dict[str, str], set[str]],
    store: dict[str, bytes],
) -> None:
    """Put back the paths a failed step changed, from the content store or the backup."""
    old_files, old_dirs = before
    new_files, new_dirs = snapshot(top, extra_roots(journal["plan"]))
    for key in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(key), new_files.get(key)
        if old == new:
            continue
        path = key_path(top, key)
        if old is None:
            if path.is_symlink() or path.is_file():
                with contextlib.suppress(OSError):
                    path.unlink()
            continue
        data = store.get(old)
        if data is None:
            member = backup_member(top, journal, key)
            data = member if member is not None and sha256_bytes(member) == old else None
        if data is None or os.path.isdir(path) and not path.is_symlink():
            continue  # left; record_changes journals it
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            tmp = path.with_name(f".{path.name}.hacf-undo")
            tmp.write_bytes(data)
            os.replace(tmp, path)
    for folder in sorted(new_dirs - old_dirs, key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):
            (top / folder).rmdir()


def fail_step(
    top: Path,
    journal: dict[str, Any],
    jpath: Path,
    action: dict[str, Any],
    before: tuple[dict[str, str], set[str]],
    store: dict[str, bytes],
    exc: BaseException,
) -> None:
    """Undo what a failed step wrote, journal what is left, and record the step as failed."""
    try:
        undo_step(top, journal, before, store)
    finally:
        after = snapshot(top, extra_roots(journal["plan"]))
        change = record_changes(journal, before, after)
        dirs_left = sorted(after[1] - before[1])
        error = str(exc) or type(exc).__name__
        journal["steps"].append(
            {"id": step_id(action), "op": action["op"], "status": "failed",
             "at": _runtime.utc_now_iso(), "error": error, "dirs_left": dirs_left, **change}
        )  # fmt: skip
        write_journal(jpath, journal)
        left = [o["path"] for o in change["outputs"]] + dirs_left
        if left:
            print(f"failed: {step_id(action)}; left (journaled): {', '.join(left)}")
        else:
            print(f"failed: {step_id(action)}; its partial writes were put back")


def run_steps(top: Path, journal: dict[str, Any], jpath: Path, interactive: bool = False) -> int:
    """Run every plan action not yet done; skip done ones whose files still hash as recorded."""
    done = {step["id"]: step for step in journal["steps"] if step.get("status") == "done"}
    latest = {step["id"]: step for step in journal["steps"]}
    store = content_store(top, journal)
    extra = extra_roots(journal["plan"])
    with tempfile.TemporaryDirectory(prefix="hacf-migrate-") as scratch_name:
        scratch = Path(scratch_name)
        for action in journal["plan"]:
            sid = step_id(action)
            last = latest.get(sid)
            if sid not in done and last is not None and last.get("status") == "failed":
                left = [o["path"] for o in last.get("outputs", [])] + last.get("dirs_left", [])
                if left:
                    raise MigrateError(
                        f"step {sid} failed in an earlier run ({last.get('error')}) and left "
                        f"{len(left)} path(s) it could not put back: {', '.join(left)}; roll "
                        f"back with `hyper_migrate.py rollback --journal {JOURNAL_REL}`, then "
                        "apply again"
                    )
                print(f"resume: {sid} (failed in an earlier run; nothing was left)")
            if sid in done:
                keys = [o["path"] for o in done[sid]["outputs"]]
                drift = drifted(top, journal, keys)
                if drift:
                    raise MigrateError(
                        f"step {sid}: changed since the journal recorded it (a human edit?): "
                        + ", ".join(drift)
                    )
                print(f"done: {sid}")
                continue
            before = snapshot(top, extra)
            try:
                inputs = run_action(top, action, scratch, journal, interactive)
            except BaseException as exc:  # journal the partial step, then let it propagate
                fail_step(top, journal, jpath, action, before, store, exc)
                raise
            change = record_changes(journal, before, snapshot(top, extra))
            journal["steps"].append(
                {"id": sid, "op": action["op"], "status": "done", "at": _runtime.utc_now_iso(),
                 "inputs": inputs, **change}
            )  # fmt: skip
            write_journal(jpath, journal)
            for out in change["outputs"]:
                remember(top, store, out["path"], out["after"])
            print(f"applied: {sid}")
    write_journal(jpath, journal)
    print(f"apply: complete; journal {JOURNAL_REL}, backup {journal['backup']['dir']}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def cmd_rollback(ns: argparse.Namespace) -> int:
    """Verify the backup, refuse on drift unless --force, then restore the pre-apply tree."""
    top = repo_top()
    preflight_git(top)
    jpath = Path(ns.journal)
    jpath = jpath if jpath.is_absolute() else Path.cwd() / jpath
    with migrate_lock(top):
        journal = load_journal(jpath)
        if journal is None:
            raise MigrateError(f"{ns.journal}: no such journal", EXIT_USAGE)
        verify_backup(top / journal["backup"]["dir"])
        drift = drifted(top, journal)
        if drift and not ns.force:
            for key in drift:
                print(f"drift: {key}")
            raise MigrateError(
                f"{len(drift)} path(s) changed since apply; nothing was restored (--force proceeds)"
            )
        problems = restore(top, journal, set(drift))
        stamp = _runtime.utc_now_iso()
        os.replace(jpath, jpath.with_name(f"v0.6.0.rolled-back.{stamp}.json"))
    for problem in problems:
        print(f"not restored: {problem}")
    print(f"rollback: done; the backup stays at {journal['backup']['dir']}")
    return EXIT_FINDINGS if problems else EXIT_OK


def restore(top: Path, journal: dict[str, Any], drift: set[str]) -> list[str]:
    """Restore modified and deleted paths from the backup; delete unchanged created files."""
    problems = []
    for key, entry in sorted(journal["files"].items()):
        before, after = entry.get("before"), entry.get("after")
        if before == after:
            continue
        path = key_path(top, key)
        if before is None:
            if sha256_file(path) == after:
                path.unlink()
            elif os.path.lexists(path):
                problems.append(f"{key} (created by apply, changed since; left in place)")
            continue
        data = backup_member(top, journal, key)
        if data is None or sha256_bytes(data) != before:
            problems.append(f"{key} (not in the backup)")
            continue
        if key in drift:
            problems.append(f"{key} (changed since apply; overwritten by --force)")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.hacf-restore")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    for folder in sorted(journal.get("created_dirs", []), key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):
            (top / folder).rmdir()
        if os.path.isdir(top / folder):
            problems.append(f"{folder}/ (created by apply, not empty; left in place)")
    added = set(journal.get("git_config_added", []))
    for key in sorted(added):
        git(top, "config", "--local", "--unset-all", key)
    for section in DRIVER_SECTIONS:
        if any(key.startswith(section + ".") for key in added) and not any(
            key.startswith(section + ".") for key in _driver_keys(top)
        ):
            git(top, "config", "--local", "--remove-section", section)
    return problems


# ---------------------------------------------------------------------------
# move-slice, detect, plan
# ---------------------------------------------------------------------------


def cmd_move_slice(ns: argparse.Namespace) -> int:
    """Move a slice with `hyper_scope.py slice move` and repoint the journal's legacy_map."""
    top = repo_top()
    with migrate_lock(top):
        source = Path(ns.slice)
        source = source if source.is_absolute() else Path.cwd() / source
        old_dir = source.parent if source.name == "SPEC.md" else source
        old_rel = _rel(old_dir, top) if old_dir.is_dir() else None
        jpath = journal_path(top)
        journal = load_journal(jpath)
        extra = extra_roots(journal["plan"]) if journal else ()
        before = snapshot(top, extra)
        _check(
            tool(top, "hyper_scope.py", "slice", "move", ns.slice, "--to-mission", ns.to_mission),
            "hyper_scope.py slice move",
        )
        if journal is None or old_rel is None:
            print("move-slice: moved; no migration journal to update")
            return EXIT_OK
        target = Path(ns.to_mission)
        target = target if target.is_absolute() else Path.cwd() / target
        if not target.is_dir():
            target = hyper_spec.missions_root(top) / ns.to_mission
        new_rel = f"{_rel(target, top)}/slices/{old_dir.name}"
        if not (top / new_rel / "SPEC.md").is_file():
            raise MigrateError(f"the moved slice is not at {new_rel}; the journal was not updated")
        changed = 0
        for legacy, spec in list(journal["legacy_map"].items()):
            if spec == f"{old_rel}/SPEC.md":
                journal["legacy_map"][legacy] = f"{new_rel}/SPEC.md"
                changed += 1
        # The move is part of the migration: rollback undoes it with the rest (Task 21).
        change = record_changes(journal, before, snapshot(top, extra))
        journal["steps"].append(
            {"id": f"move-slice:{old_rel}", "op": "move-slice", "status": "done",
             "at": _runtime.utc_now_iso(), "inputs": {"from": old_rel, "to": new_rel}, **change}
        )  # fmt: skip
        write_journal(jpath, journal)
    print(f"move-slice: {old_rel} -> {new_rel}; legacy_map entries updated: {changed}")
    return EXIT_OK


def cmd_detect(ns: argparse.Namespace) -> int:
    """Print the legacy signals; exit 1 when there is at least one."""
    top = repo_top()
    signals = detect(top)
    for signal, detail in signals:
        print(f"{signal}: {detail}")
    if not signals:
        print("detect: nothing to migrate")
        return EXIT_OK
    print(f"detect: {len(signals)} signal(s); run `hyper_migrate.py plan` to see the migration")
    return EXIT_FINDINGS


VERIFY_CHECKS = (
    ("hyper_spec.py", "lint", "--all"),
    ("hyper_ledger.py", "validate"),
    ("hyper_lore.py", "validate"),
    ("hyper_firewall.py", "check"),
)


def cmd_verify(ns: argparse.Namespace) -> int:
    """Task 18: run every validator (and rig's scope audit under an accepted rig); exit 1 on any."""
    top = repo_top()
    checks: list[dict[str, Any]] = []
    for name, *args in VERIFY_CHECKS:
        proc = tool(top, name, *args)
        checks.append(
            {"argv": [name, *args], "exit": proc.returncode,
             "output": (proc.stdout + proc.stderr).strip().splitlines()[-20:]}
        )  # fmt: skip
    # R22.7 and release-0.6.2 R7: a rig the contract file accepts, or an in-range one whose probe
    # passed. verify waits for a running probe (prepare_rig), then reads its result.
    hyper_openrig.prepare_rig(wait=True)
    rig = hyper_openrig.plan_rig()
    if rig.use_rig:
        invalidated = False
        for mission in hyper_spec.mission_dirs(hyper_spec.missions_root(top)):
            argv = hyper_openrig.argv_scope_audit(rig.prefix, top, mission.name)
            proc = hyper_openrig.run_rig(argv, cwd=top)
            if (
                rig.via_probe
                and not invalidated
                and not hyper_openrig.probe_output_ok("scope_audit", proc)
            ):
                invalidated = True  # R10: exit outside {0, 1}, or JSON without the key paths
                hyper_openrig.invalidate_probe(rig.version)
            checks.append(
                {"argv": ["rig", *argv[len(rig.prefix) :]], "exit": proc.returncode,
                 "output": (proc.stdout + proc.stderr).strip().splitlines()[-20:]}
            )  # fmt: skip
    else:
        if rig.probe == "pending":
            reason = f"skipped: rig probe pending ({rig.version})"
        else:
            reason = f"skipped: rig is {rig.mode}" + (f" ({rig.version})" if rig.version else "")
        checks.append({"argv": ["rig", "scope", "audit"], "exit": None, "output": [reason]})
    failed = [c for c in checks if c["exit"] not in (0, None)]
    for check in checks:
        state = "skipped" if check["exit"] is None else ("ok" if check["exit"] == 0 else "FAILED")
        suffix = f" (exit {check['exit']})" if state == "FAILED" else ""
        print(f"{state}: {' '.join(check['argv'])}{suffix}")
        for line in check["output"] if state != "ok" else []:
            print(f"  {line}")
    report = {"at": _runtime.utc_now_iso(), "ok": not failed, "checks": checks}
    jpath = journal_path(top)
    if jpath.exists():
        with migrate_lock(top):
            journal = load_journal(jpath)
            if journal is not None:
                journal["verify"] = report
                write_journal(jpath, journal)
                print(f"verify: report written to {JOURNAL_REL}")
    print(f"verify: {'ok' if not failed else f'{len(failed)} check(s) failed'}")
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_plan(ns: argparse.Namespace) -> int:
    """Print the plan (human summary, or JSON); write nothing."""
    top = repo_top()
    plan = build_plan(top, ns.include_archived)
    if ns.json:
        print(json.dumps(plan, indent=2))
    else:
        for line in plan_summary(plan):
            print(line)
        print("plan: nothing was written; `hyper_migrate.py apply` runs it")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="hyper_migrate.py",
        description="Migrate a v0.5.x HACF project's specs to the v0.6.0 layout.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("detect", help="report the legacy signals; write nothing")
    p.set_defaults(handler=cmd_detect)
    p = sub.add_parser("plan", help="print the deterministic action list; write nothing")
    p.add_argument("--json", action="store_true", help="print the plan as JSON")
    p.add_argument("--include-archived", action="store_true", help="import _AUDITED MiniPRDs")
    p.set_defaults(handler=cmd_plan)
    p = sub.add_parser(
        "apply",
        help="run the plan (backup and journal first)",
        description=(
            "Run the migration. It needs a typed `yes` on a TTY; --yes skips the prompt and is "
            "refused under HACF_AUTONOMOUS=1. An agent shell can still pass --yes, so there the "
            "rule is procedural: only a person decides to migrate."
        ),
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="skip the TTY confirmation (refused under HACF_AUTONOMOUS=1)",
    )
    p.add_argument(
        "--allow-dirty", action="store_true", help="allow uncommitted changes in touched files"
    )
    p.add_argument(
        "--no-chain", action="store_true", help="do not chain each slice to its predecessor"
    )
    p.add_argument("--include-archived", action="store_true", help="import _AUDITED MiniPRDs")
    p.set_defaults(handler=cmd_apply)
    p = sub.add_parser(
        "verify", help="run lint, ledger and lore validate, firewall check (and rig's scope audit)"
    )
    p.set_defaults(handler=cmd_verify)
    p = sub.add_parser("rollback", help="restore the pre-apply tree from the backup")
    p.add_argument("--journal", required=True, help=f"the journal ({JOURNAL_REL})")
    p.add_argument(
        "--force", action="store_true", help="restore even over paths changed since apply"
    )
    p.set_defaults(handler=cmd_rollback)
    p = sub.add_parser("move-slice", help="move a migrated slice to another mission")
    p.add_argument("slice", help="the slice dir, its SPEC.md or its dot-ID")
    p.add_argument("--to-mission", required=True, help="the target mission dir (or its name)")
    p.set_defaults(handler=cmd_move_slice)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _runtime.require_runtime()  # R19.4: before PyYAML or any sibling module is imported
    _load_modules()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return ns.handler(ns)
    except MigrateError as exc:
        print(f"hyper_migrate.py: {exc}", file=sys.stderr)
        return exc.code
    except (hyper_spec.SpecError, hyper_openrig.OpenRigError) as exc:
        print(f"hyper_migrate.py: {exc}", file=sys.stderr)
        return getattr(exc, "code", EXIT_CONFLICT)


if __name__ == "__main__":
    sys.exit(main())
