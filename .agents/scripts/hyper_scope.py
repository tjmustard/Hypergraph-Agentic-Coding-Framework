#!/usr/bin/env python3
"""hyper_scope.py — the scope manager: HACF's project, mission and slice scopes in OpenRig's layout.

It creates the v6 layout at the repository root (the root SPEC.md, project.yaml, workspace.yaml,
missions/<mission>/ and missions/<mission>/slices/<NN-slug>/) with root-collision detection, and
it prints the slice DAG. Normative design: HACF's v0.6.0 design notes (not shipped): target
layout, spec frontmatter, progress/proof/notes, OpenRig manifests and key ownership, and
decision D2. The repository is the git work tree of the working directory, as for
hyper_spec.py. The templates are `.agents/schemas/openrig/*.md` next to this script's
`.agents/`, derived from OpenRig 0.6.13 (see their NOTICE).

CLI:
    project init [--check-only] [--json] [--intent TEXT] [--body-file F]
    project update [--intent TEXT] [--body-file F]
    mission create --name <slug> [--intent TEXT] [--from-draft <path>] [--body-file F]
    slice create --mission <dir> --name <NN-slug> --intent TEXT [--node-id ID] [--parent ID]
                 [--depends-on a,b] [--touches g1,g2] [--oracle-json F] [--body-file F]
                 [--legacy-source PATH] [--legacy-order N]
    slice close <slice> --reason superseded [--rewrite-dependents-to <slice id>]
    slice move <slice> --to-mission <dir>
    dag [--json] [--mission <dir>]

Exit codes: 0 ok, 1 findings (a dag cycle or a dangling dependency), 2 usage, 3 lock timeout,
4 refused, 5 conflict or foreign item.

Semantics:
- project init classifies the four root items SPEC.md, project.yaml, workspace.yaml and
  missions/ (project.yaml's missions.root, else missions/) as missing, hacf, openrig or foreign.
  SPEC.md is hacf with `hacf_type: SystemOfRecord`, openrig with an `intent` and no `hacf_*`
  key. A manifest is hacf when byte-identical to hyper_openrig's render, openrig when it parses
  with the matching `openrig.*/v0alpha1` schema. missions/ is hacf when empty or when every
  child is a mission dir with a valid mission.yaml whose SPEC.md says `hacf_type: SuperPRD`, and
  openrig when every child is a valid mission dir but not all are HACF's. Anything else,
  including a symlink or a parse error, is foreign. `--check-only` prints the classification and
  writes nothing (exit 5 when an item is foreign); otherwise a foreign item exits 5 with nothing
  written, and only missing items are created, so a second run is a no-op. openrig items are
  adopted and left byte-identical.
- project update rewrites only an hacf root SPEC.md: `hacf_updated`, `intent` (shared, so
  compare-and-swap) and, with --body-file, the body, which keeps the regenerated missions index.
- mission create writes SPEC.md (`hacf_type: SuperPRD`, `hacf_status: compiled`), mission.yaml
  (planned, hyper_openrig's bytes), PROGRESS.md and NOTES.md, and regenerates the root missions
  index when the root SPEC.md is HACF's. The dot-ID is <PFX>.X.Y[.Z] for release-X.Y[.Z], else
  <PFX>.99.0.<n>; PFX is `.agents/local/hacf.yml` openrig.id_prefix, default OPR. --from-draft
  reads the draft's frontmatter `intent` and `lore_refs` and records `hacf_source.draft`. A rerun
  with the same inputs is a no-op; an existing mission with other content exits 5.
- slice create writes SPEC.md (`hacf_type: MiniPRD`, `hacf_status: compiled`, id
  <mission-id>.<NN>), slice.yaml (hyper_openrig's bytes), PROGRESS.md and PROOF.md, appends the
  composition entry with hyper_openrig.add_slice_to_mission (orders 10, 20, ...) and regenerates
  the mission's tables (SPEC.md `## Slices`, PROGRESS.md slice table).
- slice close supersedes a live slice through hyper_openrig.close_superseded (moved to
  <mission>/closed/<NN-slug>, `status: closed-superseded`, `closed-on`, composition entry
  removed), sets `hacf_status: superseded` and regenerates the tables. A live slice that depends
  on it, in any mission, refuses the close (exit 5) unless --rewrite-dependents-to is given.
- slice move moves a live slice to another mission: the id becomes <target-id>.<NN>, both
  compositions and every live dependent's `depends_on` follow, and an id or dir collision exits
  5. Proof artifacts keep the old id as historical evidence.
- dag orders the live slices (slices/ dirs) by Kahn's algorithm, ties in mission and directory
  order; slice.yaml `execution.depends_on` wins over the frontmatter `depends_on`. A cycle or a
  dependency that names no slice exits 1; a cycle is reported with its path.
- Every command runs its checks before its first write; a failure after that undoes what the
  command wrote. Writes take `$(git rev-parse --git-common-dir)/hacf/locks/spec.lock` and are
  compare-and-swap (sha256 at read, re-read before os.replace, exit 5 on a change), because rig
  writes SPEC.md frontmatter, PROGRESS.md and mission.yaml without HACF's locks. HACF never
  writes slice.yaml `status` or `execution.depends_on`. The commands that write refuse to run
  under HACF_OUTBOX (exit 2): they have no outbox form. Generated regions follow the R26 marker
  grammar: exact markers at column 0 outside fenced code; duplicate, nested or unbalanced HACF
  markers exit 5 with nothing written; EOL style, BOM and final newline are kept.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import heapq
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, NamedTuple

import _runtime
import hyper_ledger
import hyper_openrig
import hyper_spec

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

# hyper_spec's errors carry this CLI's exit codes: usage 2, lock timeout 3, refused 4, conflict 5.
SpecError = hyper_spec.SpecError
UsageError = hyper_spec.UsageError
Refused = hyper_spec.Refused
Conflict = hyper_spec.Conflict
Write = hyper_spec.Write
PROJECT, MISSION, SLICE = hyper_spec.PROJECT, hyper_spec.MISSION, hyper_spec.SLICE

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "schemas" / "openrig"
TEMPLATE_NOTICE_RE = re.compile(r"\A<!-- HACF template [^\n]*-->\r?\n")  # dropped on render
TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")
LOCAL_CONFIG_REL = ".agents/local/hacf.yml"
BOM = "﻿"

ROOT_SPEC, PROJECT_YAML, WORKSPACE_YAML, MISSIONS_ITEM = (
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "missions/",
)
MISSING, HACF, OPENRIG, FOREIGN = "missing", "hacf", "openrig", "foreign"
DEFAULT_PROJECT_INTENT = "Describe the project in one sentence."

MISSIONS_INDEX = "missions-index"  # the root SPEC.md region (schemas.md#spec-frontmatter)
MISSION_SLICES = "slices"  # the mission SPEC.md region under `## Slices`
SLICES_HEADING = "## Slices"
NO_MISSIONS = "- (none)"
SLICE_NAME_RE = re.compile(r"^(\d{2,})-[a-z0-9][a-z0-9-]{0,63}$")
# R26: an HACF marker is a whole line at column 0 outside fenced code, exact and case-sensitive.
HACF_MARKER_RE = re.compile(
    r"^<!-- (BEGIN|END) HACF (MANAGED BLOCK|GENERATED): ([a-z][a-z0-9-]{0,63}) -->$"
)
FENCE_RE = hyper_spec.FENCE_RE
CLOSE_REASONS = ("superseded",)  # the only close reason with an hacf_status mapping
MISSION_IDENTITY = ("id", "mission", "intent", "hacf_type", "lore_refs", "hacf_source")
SLICE_CREATED = "created by hyper_scope.py slice create"


# ---------------------------------------------------------------------------
# Files, text and inputs
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Return a file's exact text (UTF-8, no newline translation); other bytes are refused."""
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


def _sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path, top: Path) -> str:
    """Return path relative to top in posix form, or as given when it lies outside top."""
    for candidate in (Path(os.path.abspath(path)), Path(os.path.realpath(path))):
        with contextlib.suppress(ValueError):
            return candidate.relative_to(top).as_posix()
    return str(path)


def _split_bom(text: str) -> tuple[str, str]:
    return (BOM, text[1:]) if text.startswith(BOM) else ("", text)


def _front(text: str) -> dict | None:
    """Return a document's frontmatter mapping (None without one); a bad block is refused."""
    return hyper_spec.parse_frontmatter(_split_bom(text)[1]).data


def _front_of(path: Path) -> dict:
    """Return a file's frontmatter mapping, or {} when it is missing or does not parse."""
    try:
        return _front(_read(path)) or {}
    except (OSError, SpecError):
        return {}


def _today() -> str:
    """Return today's UTC date, YYYY-MM-DD."""
    return _runtime.utc_now_iso()[:10]


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").strip()


def _is_live(status: Any) -> bool:
    """Rig's active filter (scope-fs.ts): not done, not closed*, not shipped*."""
    text = str(status or "").lower()
    return text != "done" and not text.startswith(("closed", "shipped"))


def _one_line(value: Any, what: str) -> str:
    """Return value stripped, or refuse text that is empty or holds control characters."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise UsageError(f"{what} must be one non-empty line of text")
    return text


def _csv(value: str | None, what: str) -> list[str] | None:
    """Split one comma-separated flag value (the ensure-node --files a,b convention, R28)."""
    if value is None:
        return None
    items = [item.strip() for item in value.split(",")]
    if any(not item for item in items):
        raise UsageError(f"{what} takes one comma-separated list without empty items")
    return list(dict.fromkeys(items))


def _node_id(value: str | None, what: str) -> str | None:
    if value is not None and not hyper_ledger.ID_RE.match(value):
        raise UsageError(f"{what} {value!r} is not a ledger node id (^[a-z][a-z0-9_]{{1,63}}$)")
    return value


def _repo_path(value: str | None, what: str) -> str | None:
    """Return a safe repo-relative posix path (no leading /, no . or .. segment)."""
    if value is None:
        return None
    text = _one_line(value, what)
    segments = text.split("/")
    if text.startswith("/") or "\\" in text or any(s in ("", ".", "..") for s in segments):
        raise UsageError(f"{what} {value!r} must be a repo-relative path")
    return text


def _read_body(path_arg: str | None) -> str | None:
    """Return a --body-file's Markdown (LF line endings, final newline), or None."""
    if path_arg is None:
        return None
    try:
        text = _split_bom(_read(Path(path_arg)))[1]
    except FileNotFoundError:
        raise UsageError(f"--body-file {path_arg}: no such file") from None
    except IsADirectoryError:
        raise UsageError(f"--body-file {path_arg} is a directory") from None
    text = text.replace("\r\n", "\n")
    if hyper_openrig.FRONTMATTER_RE.match(text):
        raise UsageError(
            f"--body-file {path_arg} starts with frontmatter; pass the Markdown body only "
            "(hyper_scope.py writes the frontmatter)"
        )
    if not text.strip():
        raise UsageError(f"--body-file {path_arg} is empty")
    return text if text.endswith("\n") else text + "\n"


def _read_oracle(path_arg: str | None) -> list | None:
    """Return the --oracle-json list of argv lists, or None."""
    if path_arg is None:
        return None
    try:
        data = json.loads(_read(Path(path_arg)))
    except FileNotFoundError:
        raise UsageError(f"--oracle-json {path_arg}: no such file") from None
    except ValueError as exc:
        raise UsageError(f"--oracle-json {path_arg} is not JSON: {exc}") from None
    good = isinstance(data, list) and bool(data)
    for argv in data if good else []:
        good = good and isinstance(argv, list) and bool(argv)
        good = good and all(isinstance(arg, str) and arg for arg in argv)
    if not good:
        raise UsageError(f"--oracle-json {path_arg} must hold a non-empty list of argv lists")
    return data


def _refuse_outbox(command: str) -> None:
    """Refuse a writing command under HACF_OUTBOX, which allows no shared lock (R22.1)."""
    if os.environ.get(hyper_ledger.OUTBOX_ENV):
        raise UsageError(
            f"{command} writes scope files under the shared spec lock and has no outbox form; "
            f"unset {hyper_ledger.OUTBOX_ENV}"
        )


def id_prefix(top: Path) -> str:
    """Return `.agents/local/hacf.yml` openrig.id_prefix, else OPR (rig's hardcoded default)."""
    path = top / LOCAL_CONFIG_REL
    try:
        text = _read_optional(path)
    except OSError as exc:
        raise Refused(f"{LOCAL_CONFIG_REL} is not readable: {exc}") from None
    if text is None:
        return hyper_openrig.DEFAULT_ID_PREFIX
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Refused(f"{LOCAL_CONFIG_REL} is not valid YAML: {exc}") from None
    section = data.get("openrig") if isinstance(data, dict) else None
    prefix = section.get("id_prefix") if isinstance(section, dict) else None
    if prefix is None:
        return hyper_openrig.DEFAULT_ID_PREFIX
    if not isinstance(prefix, str) or not hyper_openrig.PREFIX_RE.match(prefix):
        raise UsageError(f"{LOCAL_CONFIG_REL} openrig.id_prefix {prefix!r} must be 2-3 capitals")
    return prefix


# ---------------------------------------------------------------------------
# Generated regions (R26 marker grammar)
# ---------------------------------------------------------------------------


def _code_flags(lines: list[str]) -> list[bool]:
    """Flag each line inside a fenced code block, fence lines included (hyper_spec's rule)."""
    flags, fence = [], None
    for line in lines:
        text = line.rstrip("\r")
        if fence is None:
            match = FENCE_RE.match(text)
            fence = match.group(1) if match else None
            flags.append(fence is not None)
            continue
        flags.append(True)
        stripped = text.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
    return flags


def check_markers(text: str) -> None:
    """Refuse duplicate, nested or unbalanced HACF markers (exit 5), anywhere in the text.

    A marker counts only as a whole line at column 0 outside fenced code, matched exactly and
    case-sensitively, so an indented, fenced, prefixed or case-folded look-alike (such as
    `<!-- BEGIN OpenRig MANAGED BLOCK: role -->`) is ordinary text.
    """
    lines = _split_bom(text)[1].split("\n")
    open_block: tuple[str, str] | None = None
    seen: set[tuple[str, str]] = set()
    for line, code in zip(lines, _code_flags(lines)):
        match = None if code else HACF_MARKER_RE.match(line.rstrip("\r"))
        if match is None:
            continue
        edge, key = match.group(1), (match.group(2), match.group(3))
        name = f"HACF {key[0]}: {key[1]}"
        if edge == "BEGIN":
            if open_block is not None:
                raise Conflict(f"{name} is nested in HACF {open_block[0]}: {open_block[1]}")
            if key in seen:
                raise Conflict(f"duplicate {name} markers")
            seen.add(key)
            open_block = key
        elif open_block != key:
            raise Conflict(f"unbalanced {name} markers (END without its BEGIN)")
        else:
            open_block = None
    if open_block is not None:
        raise Conflict(f"unbalanced HACF {open_block[0]}: {open_block[1]} markers (no END)")


def set_generated_region(
    text: str, region: str, rows: list[str], heading: str | None = None
) -> str:
    """Return text with the HACF GENERATED region's lines replaced by rows.

    Every HACF marker in the file is checked first (check_markers); then hyper_spec.set_region
    replaces the region, or adds it after `heading` (else at the end) when it is absent. Bytes
    outside the region, the EOL style, a UTF-8 BOM and the final-newline state are kept.
    """
    bom, rest = _split_bom(text)
    check_markers(rest)
    new = hyper_spec.set_region(rest, region, rows, heading)
    if rest and not rest.endswith("\n") and new.endswith("\n"):
        new = new[:-1]
        new = new[:-1] if new.endswith("\r") else new
    return bom + new


def _normalized_body(text: str) -> str:
    """Return a document's body with LF endings, no edge blank lines and empty HACF regions."""
    body = hyper_spec.parse_frontmatter(_split_bom(text)[1]).body.replace("\r\n", "\n")
    kept, inside = [], False
    for line in body.split("\n"):
        match = HACF_MARKER_RE.match(line)
        if match:
            inside = match.group(1) == "BEGIN"
        elif inside:
            continue
        kept.append(line)
    return "\n".join(kept).strip("\n")


# ---------------------------------------------------------------------------
# Templates (.agents/schemas/openrig/*.md)
# ---------------------------------------------------------------------------


def load_template(name: str) -> str:
    """Return a template's text without its one-line derivation notice."""
    path = TEMPLATE_DIR / name
    try:
        text = _read(path)
    except FileNotFoundError:
        raise Refused(
            f"template {path} is missing; run HACF-install.sh to complete the upgrade"
        ) from None
    return TEMPLATE_NOTICE_RE.sub("", text, count=1)


def render(name: str, values: dict[str, str]) -> str:
    """Fill a template's {{tokens}} in one pass, so a value is never expanded again."""

    def value(match: re.Match) -> str:
        if match.group(1) not in values:
            raise Refused(f"template {name}: no value for {{{{{match.group(1)}}}}}")
        return values[match.group(1)]

    return TOKEN_RE.sub(value, load_template(name))


def _yaml(value: Any) -> str:
    """Render a frontmatter value the way hyper_openrig's splice writes it (flow style)."""
    return hyper_openrig.flow_value(value)


def _with_body(text: str, body: str | None) -> str:
    """Return a rendered template's frontmatter followed by body (the template's own if None)."""
    if body is None:
        return text
    match = hyper_openrig.FRONTMATTER_RE.match(text)
    return text[: match.end()] + "\n\n" + body.lstrip("\n")


def _check_rendered(text: str, expected: dict[str, Any], what: str) -> None:
    """Refuse a rendered file whose frontmatter does not read back as the intended values."""
    data = _front(text) or {}
    wrong = [key for key, value in expected.items() if key not in data or data[key] != value]
    if wrong:
        raise Refused(f"{what}: the template renders {', '.join(wrong)} wrongly ({TEMPLATE_DIR})")


def _body_title(body: str | None, prefix: str) -> str | None:
    """Return the text of the body's first H1 after prefix (or after `# `), or None."""
    for line in (body or "").split("\n"):
        if line.startswith("# "):
            title = line[len(prefix) :] if line.startswith(prefix) else line[2:]
            return title.strip() or None
    return None


# ---------------------------------------------------------------------------
# Undo log
# ---------------------------------------------------------------------------


def _restore(path: Path, written: str, original: str | None) -> Callable[[], None]:
    """Return the undo of one write: put original back unless the file changed again."""

    def undo() -> None:
        if original is None:
            if _sha(_read_optional(path)) != _sha(written):
                raise OSError(f"{path} changed again")
            os.unlink(path)
        else:
            hyper_openrig.cas_write(path, written, original)

    return undo


def _rmdir_if_empty(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.rmdir(path)


class Txn:
    """The undo log of one command: every step it took, undone in reverse order on a failure."""

    def __init__(self) -> None:
        """Start with nothing to undo."""
        self.steps: list[tuple[str, Callable[[], None]]] = []

    def add(self, label: str, undo: Callable[[], None]) -> None:
        """Log an undo step for a change made outside this class."""
        self.steps.append((label, undo))

    def mkdir(self, path: Path) -> None:
        """Create one directory that must not exist yet."""
        try:
            os.mkdir(path)
        except FileExistsError:
            raise Conflict(f"{path} appeared while the command ran; nothing was written") from None
        self.add(str(path), lambda: os.rmdir(path))

    def rename(self, src: Path, dst: Path) -> None:
        """Move a directory to a path that must not exist yet."""
        if os.path.lexists(dst):
            raise Conflict(f"{dst} already exists; nothing was written")
        os.rename(src, dst)
        self.add(str(src), lambda: os.rename(dst, src))

    def writes(self, writes: list[Write]) -> None:
        """Write files compare-and-swap, all or nothing (hyper_spec.apply_writes)."""
        hyper_spec.apply_writes(writes)
        for write in writes:
            if write.text != write.original:
                self.add(str(write.path), _restore(write.path, write.text, write.original))

    def track(self, path: Path, step: Callable[[], Any]) -> Any:
        """Run a hyper_openrig call that rewrites path itself, and log how to put it back."""
        before = _read(path)
        result = step()
        after = _read(path)
        if after != before:
            self.add(str(path), _restore(path, after, before))
        return result

    def rollback(self) -> list[str]:
        """Undo every step in reverse order; return the labels of the steps that failed."""
        left = []
        for label, undo in reversed(self.steps):
            try:
                undo()
            except (OSError, SpecError, hyper_openrig.OpenRigError):
                left.append(label)
        self.steps.clear()
        return left


def _transaction(steps: Callable[[Txn], None]) -> None:
    """Run steps with an undo log; any failure undoes what they wrote, then re-raises."""
    txn = Txn()
    try:
        steps(txn)
    except BaseException:
        left = txn.rollback()
        if left:
            print(f"hyper_scope.py: could not undo: {', '.join(left)}", file=sys.stderr)
        raise


# ---------------------------------------------------------------------------
# Layout: missions root, mission and slice arguments
# ---------------------------------------------------------------------------


def _lexical_missions_root(top: Path) -> Path:
    try:
        return Path(hyper_openrig.missions_root(top))
    except hyper_openrig.OpenRigError as exc:
        raise Refused(str(exc)) from None


def _missions_root(top: Path) -> Path:
    """Return the real missions root, which must be an existing, real directory."""
    lexical = _lexical_missions_root(top)
    if lexical.is_symlink():
        raise Refused(f"{_rel(lexical, top)}/ is a symlink; HACF writes only real directories")
    if not lexical.is_dir():
        raise Refused(
            f"the missions root {_rel(lexical, top)}/ does not exist; "
            "run hyper_scope.py project init first"
        )
    return Path(os.path.realpath(lexical))


def _mission_arg(arg: str, top: Path, mroot: Path) -> Path:
    """Return the real mission dir an argument names: a path, or a bare mission name."""
    path = Path(arg)
    if not os.path.lexists(path) and "/" not in arg:
        path = mroot / arg
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not path.is_dir():
        raise UsageError(f"{arg}: not a mission directory")
    real = Path(os.path.realpath(path))
    if real.parent != mroot:
        raise UsageError(f"{arg}: a mission directory lies directly under {_rel(mroot, top)}/")
    if not (real / "mission.yaml").is_file():
        raise Refused(f"{_rel(real, top)} has no mission.yaml")
    return real


def _mission_dot_id(mission_dir: Path, top: Path) -> str:
    """Return the mission dot-ID in a mission's SPEC.md frontmatter."""
    try:
        front = _front(_read(mission_dir / "SPEC.md")) or {}
    except FileNotFoundError:
        raise Refused(f"{_rel(mission_dir, top)} has no SPEC.md") from None
    mission_id = front.get("id")
    if not hyper_openrig.is_mission_dot_id(mission_id):
        raise Refused(f"{_rel(mission_dir, top)}/SPEC.md id {mission_id!r} is not a mission dot-ID")
    return mission_id


def _slice_arg(arg: str, top: Path, mroot: Path) -> Path:
    """Return the real dir of a live slice named by its path, its SPEC.md or its dot-ID."""
    path = Path(arg)
    if os.path.lexists(path):
        folder = path.parent if path.name == "SPEC.md" and not path.is_dir() else path
    elif hyper_openrig.DOT_ID_RE.match(arg):
        folder = hyper_spec.find_by_dot_id(arg, mroot).parent
    else:
        raise UsageError(f"{arg}: no such slice directory or dot-ID")
    folder = Path(os.path.abspath(folder))
    if folder.is_symlink() or not folder.is_dir():
        raise UsageError(f"{arg}: not a slice directory")
    real = Path(os.path.realpath(folder))
    if real.parent.name != "slices" or real.parent.parent.parent != mroot:
        raise UsageError(f"{arg}: not a live slice directory (<mission>/slices/<NN-slug>)")
    if not (real / "SPEC.md").is_file():
        raise Refused(f"{_rel(real, top)} has no SPEC.md")
    return real


def _live_slice_dirs(mission_dir: Path) -> list[Path]:
    folder = Path(mission_dir) / "slices"
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir()) if p.is_dir() and not p.name.startswith(".")]


def _slice_ids(mroot: Path) -> set[str]:
    """Return the ids of every slice SPEC.md, closed/ included, in every mission."""
    ids = set()
    for mission in hyper_spec.mission_dirs(mroot):
        for folder in hyper_spec.slice_dirs(mission):
            slice_id = _front_of(folder / "SPEC.md").get("id")
            if isinstance(slice_id, str):
                ids.add(slice_id)
    return ids


def _dependents(mroot: Path, slice_id: str, skip: Path | None = None) -> list[tuple]:
    """Return (dir, depends_on, source) of every live slice that depends on slice_id.

    A mission dir equal to skip is left out (hyper_openrig.close_superseded handles its own).
    """
    found = []
    for mission in hyper_spec.mission_dirs(mroot):
        if skip is not None and os.path.realpath(mission) == os.path.realpath(skip):
            continue
        for folder in _live_slice_dirs(mission):
            if not _is_live(_front_of(folder / "SPEC.md").get("status")):
                continue
            deps, source = hyper_openrig.slice_dependencies(folder)
            if slice_id in deps:
                found.append((folder, deps, source))
    return found


def _repoint(dependents: list[tuple], old: str, new: str, top: Path) -> list[Write]:
    """Return the SPEC.md writes that repoint each dependent's depends_on from old to new."""
    writes = []
    for folder, deps, source in dependents:
        if source == "slice.yaml":
            raise Conflict(
                f"{_rel(folder, top)}/slice.yaml execution.depends_on names {old}; HACF never "
                "writes slice.yaml, so repoint it there first"
            )
        text = _read(folder / "SPEC.md")
        new_deps = list(dict.fromkeys(new if dep == old else dep for dep in deps))
        writes.append(
            Write(
                folder / "SPEC.md",
                text,
                hyper_spec.splice_owned(text, SLICE, {"depends_on": new_deps}),
            )
        )
    return writes


# ---------------------------------------------------------------------------
# Root classification (Task 8)
# ---------------------------------------------------------------------------


class Item(NamedTuple):
    """One root item and its class."""

    item: str  # SPEC.md | project.yaml | workspace.yaml | missions/
    path: str  # repo-relative
    cls: str  # missing | hacf | openrig | foreign
    reason: str

    def record(self) -> dict[str, str]:
        """Return the item as a JSON record."""
        return {"item": self.item, "path": self.path, "class": self.cls, "reason": self.reason}


def _entry_problem(path: Path, *, directory: bool) -> str | None:
    if path.is_symlink():
        return "is a symlink"
    if directory and not path.is_dir():
        return "is not a directory"
    if not directory and not path.is_file():
        return "is not a regular file"
    return None


def classify_spec(path: Path) -> tuple[str, str]:
    """Classify the root SPEC.md (or any SPEC.md) as missing, hacf, openrig or foreign."""
    if not os.path.lexists(path):
        return MISSING, "absent"
    problem = _entry_problem(path, directory=False)
    if problem:
        return FOREIGN, problem
    try:
        front = _front(_read(path))
    except (OSError, SpecError) as exc:
        return FOREIGN, f"does not parse: {exc}"
    if front is None:
        return FOREIGN, "has no frontmatter"
    if front.get("hacf_type") == hyper_spec.HACF_TYPES[PROJECT]:
        return HACF, "hacf_type SystemOfRecord"
    if "intent" in front and not any(str(key).startswith("hacf_") for key in front):
        return OPENRIG, "an OpenRig intent and no hacf_* key"
    return FOREIGN, "neither HACF's SystemOfRecord nor an OpenRig intent frontmatter"


def classify_manifest(path: Path, rendered: str, schema: str) -> tuple[str, str]:
    """Classify project.yaml or workspace.yaml against HACF's render and rig's schema."""
    if not os.path.lexists(path):
        return MISSING, "absent"
    problem = _entry_problem(path, directory=False)
    if problem:
        return FOREIGN, problem
    try:
        text = _read(path)
    except (OSError, SpecError) as exc:
        return FOREIGN, f"is not readable: {exc}"
    if text == rendered:
        return HACF, "byte-identical to hyper_openrig's render"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return FOREIGN, "does not parse as YAML"
    if isinstance(data, dict) and data.get("schema") == schema:
        return OPENRIG, f"schema {schema}"
    return FOREIGN, f"its schema is not {schema}"


def _mission_kind(folder: Path) -> tuple[str, str]:
    """Classify one child of the missions root as hacf, openrig or foreign."""
    name = folder.name
    if not hyper_openrig.NAME_RE.match(name):
        return FOREIGN, f"{name} is not a mission directory name"
    problem = _entry_problem(folder, directory=True)
    if problem:
        return FOREIGN, f"{name} {problem}"
    manifest = folder / "mission.yaml"
    if not os.path.lexists(manifest):
        return FOREIGN, f"{name} has no mission.yaml"
    problem = _entry_problem(manifest, directory=False)
    if problem:
        return FOREIGN, f"{name}/mission.yaml {problem}"
    try:
        data = yaml.safe_load(_read(manifest))
        if not isinstance(data, dict) or data.get("schema") != hyper_openrig.SCHEMA_MISSION:
            return FOREIGN, f"{name}/mission.yaml is not {hyper_openrig.SCHEMA_MISSION}"
        hyper_openrig.validate_mission_composition(data, manifest)
    except (OSError, SpecError, yaml.YAMLError, hyper_openrig.OpenRigError) as exc:
        return FOREIGN, f"{name}/mission.yaml is not a valid mission manifest: {exc}"
    spec = folder / "SPEC.md"
    if not os.path.lexists(spec):
        return OPENRIG, f"{name} has no SPEC.md hacf_type"
    problem = _entry_problem(spec, directory=False)
    if problem:
        return FOREIGN, f"{name}/SPEC.md {problem}"
    try:
        hacf_type = (_front(_read(spec)) or {}).get("hacf_type")
    except (OSError, SpecError) as exc:
        return FOREIGN, f"{name}/SPEC.md does not parse: {exc}"
    if hacf_type is None:
        return OPENRIG, f"{name}/SPEC.md has no hacf_type"
    if hacf_type == hyper_spec.HACF_TYPES[MISSION]:
        return HACF, f"{name} is a SuperPRD mission"
    return FOREIGN, f"{name}/SPEC.md hacf_type {hacf_type!r} is not SuperPRD"


def classify_missions(path: Path) -> tuple[str, str]:
    """Classify the missions root: hacf when empty or all HACF missions, else openrig/foreign."""
    if not os.path.lexists(path):
        return MISSING, "absent"
    problem = _entry_problem(path, directory=True)
    if problem:
        return FOREIGN, problem
    try:
        children = sorted(os.listdir(path))
    except OSError as exc:
        return FOREIGN, f"is not readable: {exc}"
    if not children:
        return HACF, "empty"
    kinds = []
    for name in children:
        kind, reason = _mission_kind(Path(path) / name)
        if kind == FOREIGN:
            return FOREIGN, reason
        kinds.append(kind)
    if all(kind == HACF for kind in kinds):
        return HACF, "every mission is an HACF SuperPRD mission"
    return OPENRIG, "valid OpenRig missions, not all of them HACF's"


def classify_root(top: Path) -> list[Item]:
    """Classify SPEC.md, project.yaml, workspace.yaml and the missions root, in that order."""
    refused = None
    try:
        mroot = Path(hyper_openrig.missions_root(top))
    except hyper_openrig.OpenRigError as exc:
        # R39 F1: rig refuses this project.yaml (missions.root outside the workspace), so it is
        # foreign here too, and check-only and init both stop with exit 5.
        refused = str(exc)
        mroot = top / "missions"
    checks = (
        (ROOT_SPEC, top / ROOT_SPEC, classify_spec),
        (
            PROJECT_YAML,
            top / PROJECT_YAML,
            lambda p: classify_manifest(
                p, hyper_openrig.render_project_yaml(), hyper_openrig.SCHEMA_PROJECT
            ),
        ),
        (
            WORKSPACE_YAML,
            top / WORKSPACE_YAML,
            lambda p: classify_manifest(
                p, hyper_openrig.render_workspace_yaml(), hyper_openrig.SCHEMA_WORKSPACE
            ),
        ),
        (MISSIONS_ITEM, mroot, classify_missions),
    )
    items = []
    for item, path, classify in checks:
        cls, reason = classify(path)
        if item == PROJECT_YAML and refused is not None and cls != FOREIGN:
            cls, reason = FOREIGN, refused
        rel = _rel(path, top) + ("/" if item == MISSIONS_ITEM else "")
        items.append(Item(item, rel, cls, reason))
    return items


def _print_items(items: list[Item], as_json: bool, created: list[str] | None = None) -> None:
    if as_json:
        payload: dict[str, Any] = {
            "items": [item.record() for item in items],
            "foreign": [item.item for item in items if item.cls == FOREIGN],
        }
        if created is not None:
            payload["created"] = created
        print(json.dumps(payload, indent=2))
        return
    for item in items:
        print(f"{item.item}: {item.cls} ({item.reason})")
    for path in created or []:
        print(f"created: {path}")


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def missions_index(top: Path) -> list[str]:
    """Return the root SPEC.md missions-index lines: `- <mission> (<id>)` per mission."""
    mroot = _lexical_missions_root(top)
    if not mroot.is_dir():
        return [NO_MISSIONS]
    rows = []
    for mission in hyper_spec.mission_dirs(Path(os.path.realpath(mroot))):
        mission_id = _front_of(mission / "SPEC.md").get("id")
        rows.append(f"- {mission.name} ({mission_id})" if mission_id else f"- {mission.name}")
    return rows or [NO_MISSIONS]


def project_spec_text(top: Path, intent: str, body: str | None) -> str:
    """Return a new root SPEC.md (SystemOfRecord) from project_spec.md, or with body."""
    today = _today()
    text = render(
        "project_spec.md",
        {
            "intent_yaml": _yaml(intent),
            "date_yaml": _yaml(today),
            "name": top.name,
            "intent": intent,
            "missions_index": NO_MISSIONS,
        },
    )
    expected = {"intent": intent, "hacf_schema": 1, "hacf_type": "SystemOfRecord"}
    _check_rendered(text, {**expected, "hacf_updated": today}, "project SPEC.md")
    return set_generated_region(_with_body(text, body), MISSIONS_INDEX, missions_index(top))


def updated_project_spec(text: str, intent: str | None, body: str | None, rows: list[str]) -> str:
    """Return the root SPEC.md with hacf_updated, intent and the body updated (Task 10)."""
    bom, rest = _split_bom(text)
    updates: dict[str, Any] = {"hacf_updated": _today()}
    if intent is not None:
        updates["intent"] = intent
    rest = hyper_spec.splice_owned(rest, PROJECT, updates)
    if body is not None:
        eol = "\r\n" if "\r\n" in rest else "\n"
        end = hyper_openrig.FRONTMATTER_RE.match(rest).end()
        rest = rest[:end] + eol + eol + body.lstrip("\n").replace("\n", eol)
    return set_generated_region(bom + rest, MISSIONS_INDEX, rows)


def cmd_project_init(ns: argparse.Namespace) -> int:
    """Classify the root items; create the missing ones unless one is foreign or --check-only."""
    top = hyper_spec.repo_top()
    items = classify_root(top)
    foreign = [item.item for item in items if item.cls == FOREIGN]
    if ns.check_only or foreign:
        _print_items(items, ns.json)
        if foreign:
            print(
                f"hyper_scope.py: project init: foreign root item(s): {', '.join(foreign)}; "
                "nothing was written (HACF never overwrites a foreign file, D2)",
                file=sys.stderr,
            )
            return EXIT_CONFLICT
        return EXIT_OK
    missing = [item for item in items if item.cls == MISSING]
    names = {item.item for item in missing}
    if ROOT_SPEC not in names and (ns.intent is not None or ns.body_file is not None):
        print("project init: SPEC.md exists; --intent and --body-file apply to project update")
    if not missing:
        _print_items(items, ns.json, created=[])
        if not ns.json:
            print("project init: every root item exists; nothing written")
        return EXIT_OK
    _refuse_outbox("project init")
    intent = DEFAULT_PROJECT_INTENT if ns.intent is None else _one_line(ns.intent, "--intent")
    body = _read_body(ns.body_file) if ROOT_SPEC in names else None
    renders = {
        PROJECT_YAML: hyper_openrig.render_project_yaml,
        WORKSPACE_YAML: hyper_openrig.render_workspace_yaml,
    }
    with hyper_spec.spec_lock(top):
        if classify_root(top) != items:
            raise Conflict("the root items changed while project init ran; nothing was written")

        def steps(txn: Txn) -> None:
            # R39 F2: render every file before the first mkdir, so a render failure creates
            # nothing; the rollback stays as a second line of defense. A missions root that
            # init creates is empty, so the index rendered before its mkdir is the same.
            writes = [
                Write(top / item.path, None, renders[item.item]())
                for item in missing
                if item.item in renders
            ]
            if ROOT_SPEC in names:
                writes.append(Write(top / ROOT_SPEC, None, project_spec_text(top, intent, body)))
            for item in missing:
                if item.item == MISSIONS_ITEM:
                    txn.mkdir(top / item.path.rstrip("/"))
            txn.writes(writes)

        _transaction(steps)
    created = [item.path for item in missing]
    _print_items(classify_root(top), ns.json, created=created)
    return EXIT_OK


def cmd_project_update(ns: argparse.Namespace) -> int:
    """Rewrite HACF's root SPEC.md: hacf_updated, intent, body and the missions index."""
    top = hyper_spec.repo_top()
    path = top / ROOT_SPEC
    cls, reason = classify_spec(path)
    if cls != HACF:
        raise Conflict(
            f"SPEC.md is {cls} ({reason}); project update rewrites only HACF's SystemOfRecord "
            "SPEC.md (project init creates a missing one)"
        )
    _refuse_outbox("project update")
    intent = None if ns.intent is None else _one_line(ns.intent, "--intent")
    body = _read_body(ns.body_file)
    with hyper_spec.spec_lock(top):
        text = _read(path)
        if classify_spec(path)[0] != HACF:
            raise Conflict("SPEC.md changed while project update ran; nothing was written")
        new = updated_project_spec(text, intent, body, missions_index(top))
        written = hyper_spec.apply_writes([Write(path, text, new)])
    print(f"project update: {'wrote' if written else 'unchanged'} {ROOT_SPEC}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


def slice_rows(mission_dir: Path) -> list[str]:
    """Return the mission SPEC.md `## Slices` table: id | slice | intent, closed/ included."""
    rows = ["| id | slice | intent |", "|---|---|---|"]
    for folder in hyper_spec.slice_dirs(mission_dir):
        front = _front_of(folder / "SPEC.md")
        cells = (front.get("id"), f"{folder.parent.name}/{folder.name}", front.get("intent"))
        rows.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return rows


def _table_writes(mission_dir: Path, spec_text: str | None, progress_text: str | None) -> list:
    """Return the writes that regenerate a mission's SPEC.md and PROGRESS.md slice tables.

    The SPEC.md table is HACF's only in an HACF mission; an OpenRig mission keeps its SPEC.md.
    A missing PROGRESS.md is created from mission_progress.md.
    """
    writes = []
    front = _front(spec_text) if spec_text is not None else None
    if front and front.get("hacf_type") == hyper_spec.HACF_TYPES[MISSION]:
        rows = slice_rows(mission_dir)
        new = set_generated_region(spec_text, MISSION_SLICES, rows, SLICES_HEADING)
        writes.append(Write(mission_dir / "SPEC.md", spec_text, new))
    table = hyper_spec.mission_table(mission_dir, {})
    if progress_text is None:
        values = {"mission": mission_dir.name, "slice_table": "\n".join(table)}
        new = render("mission_progress.md", values)
    else:
        new = set_generated_region(progress_text, hyper_spec.SLICE_TABLE, table)
    writes.append(Write(mission_dir / "PROGRESS.md", progress_text, new))
    return writes


def _mission_texts(mission_dir: Path) -> tuple[str | None, str | None]:
    return _read_optional(mission_dir / "SPEC.md"), _read_optional(mission_dir / "PROGRESS.md")


def _read_draft(arg: str, top: Path) -> tuple[str, dict]:
    """Return a draft's repo-relative path and its frontmatter ({} without one)."""
    path = Path(os.path.abspath(arg))
    if path.is_symlink() or not path.is_file():
        raise UsageError(f"--from-draft {arg}: not a regular file")
    rel = _rel(path, top)
    if Path(rel).is_absolute():
        raise UsageError(f"--from-draft {arg} lies outside the repository")
    return rel, _front(_read(path)) or {}


def _mission_ids(mroot: Path, skip: Path | None = None) -> list[Any]:
    return [
        _front_of(mission / "SPEC.md").get("id")
        for mission in hyper_spec.mission_dirs(mroot)
        if skip is None or os.path.realpath(mission) != os.path.realpath(skip)
    ]


def plan_mission(
    mission_dir: Path,
    mission_id: str,
    intent: str,
    lore_refs: list[str],
    source: dict | None,
    body: str | None,
) -> list[Write]:
    """Return the four files of a new mission (SPEC.md first); writes nothing."""
    name, today = mission_dir.name, _today()
    spec = render(
        "mission_spec.md",
        {
            "id_yaml": _yaml(mission_id),
            "mission_yaml": _yaml(name),
            "verified_yaml": _yaml(f"{today} against scaffold (hyper_scope mission create)"),
            "created_yaml": _yaml(today),
            "intent_yaml": _yaml(intent),
            "lore_refs_yaml": _yaml(lore_refs),
            "hacf_source_yaml": _yaml(source),
            "title": name,
            "intent": intent,
            "slices": "",
        },
    )
    expected = {"id": mission_id, "mission": name, "intent": intent, "lore_refs": lore_refs}
    expected.update(hacf_type="SuperPRD", hacf_status="compiled", hacf_source=source)
    _check_rendered(spec, expected, "mission SPEC.md")
    spec = set_generated_region(
        _with_body(spec, body), MISSION_SLICES, slice_rows(mission_dir), SLICES_HEADING
    )
    progress = render(
        "mission_progress.md",
        {"mission": name, "slice_table": "\n".join(hyper_spec.mission_table(mission_dir, {}))},
    )
    notes = render(
        "notes.md",
        {
            "mission_id_yaml": _yaml(mission_id),
            "name_yaml": _yaml(name),
            "created_yaml": _yaml(today),
            "name": name,
            "created": today,
        },
    )
    return [
        Write(mission_dir / "SPEC.md", None, spec),
        Write(mission_dir / "mission.yaml", None, hyper_openrig.render_mission_yaml()),
        Write(mission_dir / "PROGRESS.md", None, progress),
        Write(mission_dir / "NOTES.md", None, notes),
    ]


def _mission_differences(mission_dir: Path, intended: str) -> list[str]:
    """Return what differs between an existing mission and the SPEC.md a rerun would write."""
    missing = [
        name
        for name in ("SPEC.md", "mission.yaml", "PROGRESS.md", "NOTES.md")
        if _entry_problem(mission_dir / name, directory=False)
    ]
    if missing:
        return [f"missing {', '.join(missing)}"]
    text = _read(mission_dir / "SPEC.md")
    have, want = _front(text) or {}, _front(intended) or {}
    diffs = [key for key in MISSION_IDENTITY if have.get(key) != want.get(key)]
    if _normalized_body(text) != _normalized_body(intended):
        diffs.append("body")
    return diffs


def cmd_mission_create(ns: argparse.Namespace) -> int:
    """Create a mission (SPEC.md, mission.yaml, PROGRESS.md, NOTES.md); a rerun is a no-op."""
    _refuse_outbox("mission create")
    top = hyper_spec.repo_top()
    mroot = _missions_root(top)
    name = ns.name
    if not hyper_openrig.NAME_RE.match(name or ""):
        raise UsageError(
            f"--name {name!r} is not a mission directory name ({hyper_openrig.NAME_RE.pattern})"
        )
    draft_rel, draft = _read_draft(ns.from_draft, top) if ns.from_draft else (None, {})
    intent_value = ns.intent if ns.intent is not None else draft.get("intent")
    if intent_value is None:
        raise UsageError(
            "mission create needs --intent, or a --from-draft whose frontmatter has one"
        )
    intent = _one_line(intent_value, "the mission intent")
    lore_refs = draft.get("lore_refs") or []
    if not isinstance(lore_refs, list) or not all(isinstance(r, str) for r in lore_refs):
        raise Refused(f"--from-draft {ns.from_draft}: lore_refs must be a list of lore ids")
    source = {"draft": draft_rel} if draft_rel else None
    body = _read_body(ns.body_file)
    prefix = id_prefix(top)
    mission_dir = mroot / name
    rel = _rel(mission_dir, top)
    with hyper_spec.spec_lock(top):
        ids = _mission_ids(mroot, skip=mission_dir)
        release = bool(hyper_openrig.RELEASE_NAME_RE.match(name))
        if os.path.lexists(mission_dir):
            if mission_dir.is_symlink() or not mission_dir.is_dir():
                raise Conflict(f"{rel} exists and is not a mission directory; nothing was written")
            if release:
                mission_id = hyper_openrig.mission_dot_id(name, prefix=prefix)
            else:  # an escape-band mission keeps the ordinal it was given
                mission_id = _front_of(mission_dir / "SPEC.md").get("id")
                band = re.compile(rf"^{re.escape(prefix)}\.99\.0\.\d+$")
                if not isinstance(mission_id, str) or not band.match(mission_id):
                    raise Conflict(f"{rel} exists with different content (id); nothing was written")
            intended = plan_mission(mission_dir, mission_id, intent, lore_refs, source, body)
            diffs = _mission_differences(mission_dir, intended[0].text)
            if diffs:
                raise Conflict(
                    f"{rel} exists with different content ({', '.join(diffs)}); nothing was written"
                )
            print(f"mission create: {rel} exists with the same content; nothing written")
            return EXIT_OK
        ordinal = None if release else hyper_openrig.next_escape_band_ordinal(ids, prefix)
        mission_id = hyper_openrig.mission_dot_id(name, ordinal, prefix)
        if mission_id in ids:
            raise Conflict(f"mission id {mission_id} is already used; nothing was written")
        writes = plan_mission(mission_dir, mission_id, intent, lore_refs, source, body)
        root_spec = top / ROOT_SPEC
        root_text = _read(root_spec) if classify_spec(root_spec)[0] == HACF else None
        if root_text is not None:
            check_markers(root_text)  # bad markers stop the command before its first write

        def steps(txn: Txn) -> None:
            txn.mkdir(mission_dir)
            txn.writes(writes)
            if root_text is not None:
                index = set_generated_region(root_text, MISSIONS_INDEX, missions_index(top))
                txn.writes([Write(root_spec, root_text, index)])

        _transaction(steps)
    for write in writes:
        print(f"mission create: wrote {_rel(write.path, top)}")
    if root_text is None:
        print("mission create: the root SPEC.md is not HACF's; its missions index was not updated")
    print(f"mission create: {rel} is {mission_id}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Slices
# ---------------------------------------------------------------------------


def plan_slice(
    slice_dir: Path,
    slice_id: str,
    fields: dict[str, Any],
    body: str | None,
    at: str,
) -> list[Write]:
    """Return a new slice's SPEC.md, slice.yaml, PROGRESS.md and PROOF.md; writes nothing."""
    name, today = slice_dir.name, at[:10]
    depends_on = fields["depends_on"]
    title = _body_title(body, hyper_spec.SLICE_H1_PREFIX) or name
    spec = render(
        "slice_spec.md",
        {
            "id_yaml": _yaml(slice_id),
            "slice_yaml": _yaml(name),
            "mission_yaml": _yaml(slice_dir.parent.parent.name),
            "verified_yaml": _yaml(f"{today} against scaffold (hyper_scope slice create)"),
            "created_yaml": _yaml(today),
            "intent_yaml": _yaml(fields["intent"]),
            "depends_on_yaml": _yaml(depends_on),
            "node_id_yaml": _yaml(fields["node_id"]),
            "parent_yaml": _yaml(fields["parent"]),
            "touches_yaml": _yaml(fields["hacf_touches"]),
            "oracle_yaml": _yaml(fields["hacf_oracle"]),
            "legacy_source_yaml": _yaml(fields["hacf_legacy_source"]),
            "legacy_order_yaml": _yaml(fields["hacf_legacy_order"]),
            "title": title,
            "intent": fields["intent"],
            "dependencies": "\n".join(f"- {dep}" for dep in depends_on) or "- (none)",
        },
    )
    expected = {"id": slice_id, "slice": name, "mission": slice_dir.parent.parent.name}
    expected.update(fields, status="active", hacf_type="MiniPRD", hacf_status="compiled")
    _check_rendered(spec, expected, "slice SPEC.md")
    outcome = hyper_spec.outcome_line(at, "compiled", SLICE_CREATED)
    return [
        Write(slice_dir / "SPEC.md", None, _with_body(spec, body)),
        Write(slice_dir / "slice.yaml", None, hyper_openrig.render_slice_yaml()),
        Write(
            slice_dir / "PROGRESS.md",
            None,
            render("slice_progress.md", {"slice": name, "at": at, "outcome": outcome}),
        ),
        Write(slice_dir / "PROOF.md", None, render("proof.md", {"id": slice_id, "title": title})),
    ]


def cmd_slice_create(ns: argparse.Namespace) -> int:
    """Create a slice in a mission and append it to the mission's composition."""
    _refuse_outbox("slice create")
    top = hyper_spec.repo_top()
    mroot = _missions_root(top)
    mission_dir = _mission_arg(ns.mission, top, mroot)
    match = SLICE_NAME_RE.match(ns.name or "")
    if match is None:
        raise UsageError(f"--name {ns.name!r} must be <NN>-<slug>: two or more digits, a slug")
    name = ns.name
    slice_id = hyper_openrig.slice_dot_id(_mission_dot_id(mission_dir, top), int(match.group(1)))
    depends_on = _csv(ns.depends_on, "--depends-on") or []
    bad = [dep for dep in depends_on if not hyper_openrig.is_slice_dot_id(dep) or dep == slice_id]
    if bad:
        raise UsageError(f"--depends-on {', '.join(bad)}: not another slice's dot-ID")
    touches = _csv(ns.touches, "--touches")
    for pattern in touches or []:
        problem = hyper_ledger.glob_problem(pattern)
        if problem:
            raise UsageError(f"--touches {pattern!r} {problem}")
    if ns.legacy_order is not None and ns.legacy_order < 0:
        raise UsageError("--legacy-order must be 0 or more")
    fields = {
        "intent": _one_line(ns.intent, "--intent"),
        "depends_on": depends_on,
        "node_id": _node_id(ns.node_id, "--node-id"),
        "parent": _node_id(ns.parent, "--parent"),
        "hacf_touches": touches,
        "hacf_oracle": _read_oracle(ns.oracle_json),
        "hacf_legacy_source": _repo_path(ns.legacy_source, "--legacy-source"),
        "hacf_legacy_order": ns.legacy_order,
    }
    body = _read_body(ns.body_file)
    slice_dir = mission_dir / "slices" / name
    with hyper_spec.spec_lock(top):
        for existing in (slice_dir, mission_dir / "closed" / name):
            if os.path.lexists(existing):
                raise Conflict(f"{_rel(existing, top)} exists; HACF never overwrites a slice")
        ids = _slice_ids(mroot)
        if slice_id in ids:
            raise Conflict(f"slice id {slice_id} is already used; nothing was written")
        unknown = [dep for dep in depends_on if dep not in ids]
        if unknown:
            raise Refused(f"--depends-on {', '.join(unknown)} names no slice")
        members = hyper_openrig.read_mission_members(mission_dir)
        ref = os.path.normpath(f"slices/{name}/slice.yaml")
        if any(os.path.normpath(member.ref) == ref for member in members):
            raise Conflict(f"{_rel(mission_dir, top)}/mission.yaml already lists {ref}")
        files = plan_slice(slice_dir, slice_id, fields, body, _runtime.utc_now_iso())
        spec_text, progress_text = _mission_texts(mission_dir)
        _table_writes(mission_dir, spec_text, progress_text)  # bad markers stop it here

        def steps(txn: Txn) -> None:
            if not (mission_dir / "slices").is_dir():
                txn.mkdir(mission_dir / "slices")
            txn.mkdir(slice_dir)
            txn.writes(files)
            txn.track(
                mission_dir / "mission.yaml",
                lambda: hyper_openrig.add_slice_to_mission(mission_dir, name),
            )
            txn.writes(_table_writes(mission_dir, spec_text, progress_text))

        _transaction(steps)
    for write in files:
        print(f"slice create: wrote {_rel(write.path, top)}")
    print(f"slice create: {_rel(slice_dir, top)} is {slice_id}")
    return EXIT_OK


def _closed_progress(text: str, progress: Path, detail: str, at: str) -> str:
    """Return a closed slice's PROGRESS.md: current state superseded, one Outcomes line."""
    event = hyper_spec.last_event(progress)
    region = ["- hacf_status: superseded", f"- status: {hyper_openrig.STATUS_MAP['superseded']}"]
    if event:
        region.append(f"- last event: {event['at']} {event['event']}")
    text = set_generated_region(
        text, hyper_spec.CURRENT_STATE, region, hyper_spec.CURRENT_STATE_HEADING
    )
    line = hyper_spec.outcome_line(at, "superseded", detail)
    return hyper_spec.append_line(text, (hyper_spec.OUTCOMES_HEADING,), line)


def cmd_slice_close(ns: argparse.Namespace) -> int:
    """Supersede a live slice: move it to closed/ and update its mission (Task 13)."""
    _refuse_outbox("slice close")
    top = hyper_spec.repo_top()
    mroot = _missions_root(top)
    slice_dir = _slice_arg(ns.slice, top, mroot)
    mission_dir, name = slice_dir.parent.parent, slice_dir.name
    target = ns.rewrite_dependents_to
    with hyper_spec.spec_lock(top):
        spec_text = _read(slice_dir / "SPEC.md")
        slice_id = (_front(spec_text) or {}).get("id")
        if not hyper_openrig.is_slice_dot_id(slice_id):
            raise Refused(f"{_rel(slice_dir, top)}/SPEC.md id {slice_id!r} is not a slice dot-ID")
        if target is not None:
            if not hyper_openrig.is_slice_dot_id(target) or target == slice_id:
                raise UsageError(f"--rewrite-dependents-to {target!r} is not another slice dot-ID")
            if target not in _slice_ids(mroot):
                raise Refused(f"--rewrite-dependents-to {target} names no slice")
            dependent_ids = {
                _front_of(d[0] / "SPEC.md").get("id") for d in _dependents(mroot, slice_id)
            }
            if target in dependent_ids:
                raise UsageError(
                    f"--rewrite-dependents-to {target} depends on {slice_id} itself; repointing "
                    "it would make it depend on itself"
                )
        hyper_spec.splice_owned(spec_text, SLICE, {"hacf_status": "superseded"})  # can it take it
        others = _dependents(mroot, slice_id, skip=mission_dir)
        if others and target is None:
            names = ", ".join(_rel(folder, top) for folder, _, _ in others)
            raise Conflict(
                f"live slices depend on {slice_id}: {names}; pass --rewrite-dependents-to "
                "<slice id> to repoint them"
            )
        repoints = _repoint(others, slice_id, target, top) if others else []
        progress_text = _read_optional(slice_dir / "PROGRESS.md")
        mission_spec, mission_progress = _mission_texts(mission_dir)
        _table_writes(mission_dir, mission_spec, mission_progress)  # bad markers stop it here
        closed_dir = mission_dir / "closed"
        dest = closed_dir / name
        at = _runtime.utc_now_iso()
        detail = "closed by hyper_scope.py slice close" + (
            f"; dependents now use {target}" if target else ""
        )
        new_progress = (
            None
            if progress_text is None
            else _closed_progress(progress_text, slice_dir / "PROGRESS.md", detail, at)
        )
        snapshot = {mission_dir / "mission.yaml": _read(mission_dir / "mission.yaml")}
        for folder in _live_slice_dirs(mission_dir):
            snapshot[folder / "SPEC.md"] = _read_optional(folder / "SPEC.md")

        def steps(txn: Txn) -> None:
            if not closed_dir.is_dir():  # close_superseded creates it; remove it if left empty
                txn.add(str(closed_dir), lambda: _rmdir_if_empty(closed_dir))
            hyper_openrig.close_superseded(
                mission_dir, name, rewrite_dependents_to=target, today=at[:10]
            )
            txn.add(str(dest), lambda: os.rename(dest, slice_dir))
            for path, before in snapshot.items():
                now = dest / "SPEC.md" if path == slice_dir / "SPEC.md" else path
                after = _read_optional(now)
                if before is not None and after is not None and after != before:
                    txn.add(str(now), _restore(now, after, before))
            closed = _read(dest / "SPEC.md")
            spliced = hyper_spec.splice_owned(closed, SLICE, {"hacf_status": "superseded"})
            writes = [Write(dest / "SPEC.md", closed, spliced), *repoints]
            if new_progress is not None:
                writes.append(Write(dest / "PROGRESS.md", progress_text, new_progress))
            txn.writes(writes)
            # The tables read the slices' new frontmatter; their CAS base is the text read above.
            txn.writes(_table_writes(mission_dir, mission_spec, mission_progress))

        _transaction(steps)
    print(f"slice close: {slice_id} moved to {_rel(dest, top)} (closed-superseded)")
    return EXIT_OK


def cmd_slice_move(ns: argparse.Namespace) -> int:
    """Move a live slice to another mission; both compositions and dependents follow (Task 14)."""
    _refuse_outbox("slice move")
    top = hyper_spec.repo_top()
    mroot = _missions_root(top)
    slice_dir = _slice_arg(ns.slice, top, mroot)
    source, name = slice_dir.parent.parent, slice_dir.name
    target = _mission_arg(ns.to_mission, top, mroot)
    if target == source:
        raise UsageError(f"{ns.slice} is already in {_rel(target, top)}")
    match = SLICE_NAME_RE.match(name)
    if match is None:
        raise Refused(f"{name} is not an <NN>-<slug> directory, so it has no ordinal to keep")
    new_id = hyper_openrig.slice_dot_id(_mission_dot_id(target, top), int(match.group(1)))
    dest = target / "slices" / name
    with hyper_spec.spec_lock(top):
        spec_text = _read(slice_dir / "SPEC.md")
        old_id = (_front(spec_text) or {}).get("id")
        for existing in (dest, target / "closed" / name):
            if os.path.lexists(existing):
                raise Conflict(f"{_rel(existing, top)} exists; nothing was written")
        if new_id != old_id and new_id in _slice_ids(mroot):
            raise Conflict(f"slice id {new_id} is already used; nothing was written")
        hyper_openrig.read_mission_members(target)  # the target composition must be valid
        ref = f"slices/{name}/slice.yaml"
        if not any(
            os.path.normpath(m.ref) == os.path.normpath(ref)
            for m in hyper_openrig.read_mission_members(source)
        ):
            raise Refused(f"{_rel(source, top)}/mission.yaml does not list {ref}")
        moved = hyper_spec.splice_owned(spec_text, SLICE, {"id": new_id, "mission": target.name})
        dependents = [d for d in _dependents(mroot, old_id) if d[0] != slice_dir]
        repoints = _repoint(dependents, old_id, new_id, top) if old_id != new_id else []
        texts = {mission: _mission_texts(mission) for mission in (source, target)}
        for mission, (spec, progress) in texts.items():
            _table_writes(mission, spec, progress)  # bad markers stop it here

        def steps(txn: Txn) -> None:
            txn.track(
                source / "mission.yaml",
                lambda: hyper_openrig.remove_slice_from_mission(source, ref),
            )
            if not (target / "slices").is_dir():
                txn.mkdir(target / "slices")
            txn.rename(slice_dir, dest)
            txn.track(
                target / "mission.yaml", lambda: hyper_openrig.add_slice_to_mission(target, name)
            )
            txn.writes([Write(dest / "SPEC.md", spec_text, moved), *repoints])
            # The tables read the slices' new frontmatter; their CAS base is the text read above.
            tables: list[Write] = []
            for mission, (spec, progress) in texts.items():
                tables += _table_writes(mission, spec, progress)
            txn.writes(tables)

        _transaction(steps)
    print(f"slice move: {old_id} is now {new_id} at {_rel(dest, top)}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# dag
# ---------------------------------------------------------------------------


class Node(NamedTuple):
    """One live slice in the DAG."""

    id: str
    folder: Path
    front: dict
    depends_on: list[str]
    source: str  # slice.yaml | SPEC.md


def _find_cycle(remaining: set[int], deps: dict[int, list[int]]) -> list[int]:
    """Return one dependency cycle among the nodes Kahn's algorithm could not order.

    Each remaining node has a remaining dependency, so following the lowest one from the lowest
    node must revisit a node; the path from that node on is the cycle (first node repeated).
    """
    node = min(remaining)
    path, seen = [node], {node: 0}
    while True:
        node = min(dep for dep in deps[node] if dep in remaining)
        if node in seen:
            return path[seen[node] :] + [node]
        seen[node] = len(path)
        path.append(node)


def build_dag(missions: list[Path], mroot: Path, top: Path) -> dict[str, Any]:
    """Return the DAG report: Kahn order, per-slice records, a cycle and findings."""
    nodes: list[Node] = []
    findings: list[str] = []
    for mission in missions:
        for folder in _live_slice_dirs(mission):
            front = _front_of(folder / "SPEC.md")
            slice_id = front.get("id")
            if not hyper_openrig.is_slice_dot_id(slice_id):
                findings.append(f"{_rel(folder, top)}: id {slice_id!r} is not a slice dot-ID")
                continue
            depends_on, source = hyper_openrig.slice_dependencies(folder)
            nodes.append(Node(slice_id, folder, front, list(dict.fromkeys(depends_on)), source))
    index: dict[str, int] = {}
    for position, node in enumerate(nodes):
        if node.id in index:
            findings.append(f"{_rel(node.folder, top)}: id {node.id} is used by another slice")
        else:
            index[node.id] = position
    known = _slice_ids(mroot)
    deps: dict[int, list[int]] = {}
    external: dict[int, list[str]] = {}
    for position, node in enumerate(nodes):
        deps[position], external[position] = [], []
        for dep in node.depends_on:
            if dep in index:
                deps[position].append(index[dep])
            elif dep in known:
                external[position].append(dep)
            else:
                findings.append(
                    f"{_rel(node.folder, top)}: {node.source} depends_on {dep} names no slice"
                )
    indegree = {position: len(found) for position, found in deps.items()}
    dependents: dict[int, list[int]] = {position: [] for position in deps}
    for position, found in deps.items():
        for dep in found:
            dependents[dep].append(position)
    ready = [position for position, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        position = heapq.heappop(ready)
        order.append(position)
        for dependent in dependents[position]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    remaining = set(deps) - set(order)
    cycle = [nodes[p].id for p in _find_cycle(remaining, deps)] if remaining else None
    records = [
        {
            "id": nodes[p].id,
            "slice": nodes[p].folder.name,
            "mission": nodes[p].folder.parent.parent.name,
            "path": _rel(nodes[p].folder, top),
            "hacf_status": nodes[p].front.get("hacf_status"),
            "depends_on": nodes[p].depends_on,
            "depends_on_source": nodes[p].source,
            "external_depends_on": external[p],
        }
        for p in order + sorted(remaining)
    ]
    return {
        "order": [nodes[p].id for p in order],
        "slices": records,
        "cycle": cycle,
        "findings": findings,
    }


def cmd_dag(ns: argparse.Namespace) -> int:
    """Print the live slices in dependency order; exit 1 on a cycle or a dangling dependency."""
    top = hyper_spec.repo_top()
    mroot = _missions_root(top)
    missions = [_mission_arg(ns.mission, top, mroot)] if ns.mission else None
    report = build_dag(missions or hyper_spec.mission_dirs(mroot), mroot, top)
    if ns.json:
        print(json.dumps(report, indent=2))
    else:
        for record in report["slices"][: len(report["order"])]:
            print(f"{record['id']} {record['path']}")
    if report["cycle"]:
        print(f"hyper_scope.py: dag: cycle: {' -> '.join(report['cycle'])}", file=sys.stderr)
    for finding in report["findings"]:
        print(f"hyper_scope.py: dag: {finding}", file=sys.stderr)
    return EXIT_FINDINGS if report["cycle"] or report["findings"] else EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_scope.py",
        description="Create HACF project, mission and slice scopes and print the slice DAG.",
        allow_abbrev=False,
    )
    groups = parser.add_subparsers(dest="group", metavar="<group>", required=True)

    def group(name: str, text: str) -> Any:
        p = groups.add_parser(name, help=text, description=text, allow_abbrev=False)
        return p.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(sub: Any, name: str, handler: Callable[[argparse.Namespace], int], text: str):
        p = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        p.set_defaults(handler=handler)
        return p

    body_help = "a file holding the Markdown body (no frontmatter) that replaces the template's"
    project = group("project", "the root SPEC.md, project.yaml, workspace.yaml and missions/")
    p = command(project, "init", cmd_project_init, "classify the root items; create missing ones")
    p.add_argument("--check-only", action="store_true", help="print the classification only")
    p.add_argument("--json", action="store_true", help="print the classification as JSON")
    p.add_argument("--intent", help="the project intent, one sentence (for a new SPEC.md)")
    p.add_argument("--body-file", help=body_help + " (for a new SPEC.md)")
    p = command(project, "update", cmd_project_update, "rewrite HACF's root SPEC.md")
    p.add_argument("--intent", help="the new project intent, one sentence")
    p.add_argument("--body-file", help=body_help + "; the missions index is kept")

    mission = group("mission", "missions/<mission>/")
    p = command(mission, "create", cmd_mission_create, "create a mission scope")
    p.add_argument("--name", required=True, help="the mission dir: release-X.Y[.Z] or a slug")
    p.add_argument("--intent", help="one sentence; default: the --from-draft frontmatter intent")
    p.add_argument("--from-draft", help="the Draft PRD; records hacf_source.draft")
    p.add_argument("--body-file", help=body_help)

    slices = group("slice", "missions/<mission>/slices/<NN-slug>/")
    p = command(slices, "create", cmd_slice_create, "create a slice scope in a mission")
    p.add_argument("--mission", required=True, help="the mission dir (or its name)")
    p.add_argument("--name", required=True, help="<NN>-<slug>; the slice id is <mission id>.<NN>")
    p.add_argument("--intent", required=True, help="the slice intent, one sentence")
    p.add_argument("--node-id", help="the ledger node the slice implements")
    p.add_argument("--parent", help="the ledger node's parent")
    p.add_argument("--depends-on", help="comma-separated slice dot-IDs")
    p.add_argument("--touches", help="comma-separated safe repo-relative globs (hacf_touches)")
    p.add_argument("--oracle-json", help="a JSON file holding the oracle: a list of argv lists")
    p.add_argument("--body-file", help=body_help)
    p.add_argument("--legacy-source", help="the legacy flat MiniPRD path (hacf_legacy_source)")
    p.add_argument("--legacy-order", type=int, help="the legacy order (hacf_legacy_order)")
    p = command(slices, "close", cmd_slice_close, "close a live slice as superseded")
    p.add_argument("slice", help="the slice dir, its SPEC.md or its dot-ID")
    p.add_argument("--reason", required=True, choices=CLOSE_REASONS)
    p.add_argument("--rewrite-dependents-to", help="the slice dot-ID dependents switch to")
    p = command(slices, "move", cmd_slice_move, "move a live slice to another mission")
    p.add_argument("slice", help="the slice dir, its SPEC.md or its dot-ID")
    p.add_argument("--to-mission", required=True, help="the target mission dir (or its name)")

    p = groups.add_parser(
        "dag", help="print the live slices in dependency order", allow_abbrev=False
    )
    p.set_defaults(handler=cmd_dag)
    p.add_argument("--json", action="store_true", help="print the DAG report as JSON")
    p.add_argument("--mission", help="one mission dir (or its name); default: every mission")
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
        print(f"hyper_scope.py: {exc}", file=sys.stderr)
        return exc.code
    except hyper_openrig.OpenRigError as exc:  # its 2 usage, 4 parse and 5 refused match ours
        print(f"hyper_scope.py: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
