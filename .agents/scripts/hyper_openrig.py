#!/usr/bin/env python3
"""hyper_openrig.py — HACF's only writer of OpenRig manifests, pinned to rig 0.6.13.

The adapter freezes HACF's contract with OpenRig (github.com/mvschwarz/openrig, tag v0.6.13,
commit 9eeb2e42) in one module. Normative design: HACF's v0.6.0 design notes (not shipped): OpenRig
manifests, key ownership and spec frontmatter. The transcribed manifests and their sources live in
`dev/tests/fixtures/openrig-0.6.13/`; the live contract test is
`dev/tests/contract/test_openrig_live.py`.

Library (for later slices):
- render_project_yaml(), render_workspace_yaml(), render_mission_yaml(), render_slice_yaml():
  the v0alpha1 manifests, byte-identical to rig's templates.
- validate_mission_composition(), read_mission_members(), add_slice_to_mission(),
  remove_slice_from_mission(): mission.yaml membership under rig's rules. Nothing is cached:
  every call reads mission.yaml from disk, because `rig scope slice create` appends membership
  itself.
- mission_dot_id(), slice_dot_id(), is_mission_dot_id(), is_slice_dot_id(): dot-IDs (prefix OPR).
- openrig_status(), set_slice_status(), close_superseded(): the hacf_status mapping. HACF writes a
  status only into SPEC.md frontmatter, never into slice.yaml (which has no status field).
- resolve_rig(), plan_rig(): rig on PATH, or npx only with explicit download consent; the version
  rule (R22.7).
- argv_* builders, run_rig(), rig_slice_create(), rig_slice_close(): rig calls as argv lists with
  a timeout; every artifact-naming call passes --name (R22.4).
- write_proof_artifact(): delegates to `rig proof add` under the pinned rig, else writes the same
  C1 header itself.

Every write to a file rig may also write is compare-and-swap (#key-ownership): the bytes read are
hashed, the file is re-read just before os.replace, and a changed file is refused (exit 5) with
nothing written.

CLI:
    register-project --catalog <path> --id <slug> [--dry-run]
    unregister --catalog <path> --id <slug>
    doctor --catalog <path>

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
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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

# --- Pinned contract (rig 0.6.13) ----------------------------------------------------------------
RIG_VERSION = "0.6.13"
RIG_COMMIT = "9eeb2e42"
RIG_PACKAGE = "@openrig/cli"
SCHEMA_PROJECT = "openrig.project/v0alpha1"
SCHEMA_WORKSPACE = "openrig.workspace/v0alpha1"
SCHEMA_MISSION = "openrig.mission/v0alpha1"
SCHEMA_SLICE = "openrig.slice/v0alpha1"
DEFAULT_ID_PREFIX = "OPR"  # rig hardcodes DEFAULT_PROJECT_PREFIX and has no config for it
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
# Manifests (schemas.md#openrig-manifests), byte-identical to rig 0.6.13's templates
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


def render_mission_yaml() -> str:
    """Return a new mission's mission.yaml (MISSION_MANIFEST): status planned, no slices."""
    return _MISSION_YAML


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


def version_decision(version_text: str | None) -> tuple[str, str | None]:
    """Return (mode, warning) for a `rig --version` output under the version rule.

    delegate: rig is the pinned 0.6.13, so HACF calls it. native: another 0.6.x; HACF does not call
    it and writes the v0alpha1 manifests and C1 artifacts itself. stop: another major.minor (or
    an unreadable version); HACF never calls it until a re-pin after the contract test passes.
    """
    pinned, found = parse_version(RIG_VERSION), parse_version(version_text)
    shown = (version_text or "").strip() or "(no output)"
    if found == pinned:
        return "delegate", None
    if found is not None and found[:2] == pinned[:2]:
        return "native", (
            f"rig {shown} is not the pinned {RIG_VERSION}; HACF does not call it and writes the "
            "v0alpha1 manifests and C1 artifacts itself"
        )
    return "stop", (
        f"rig {shown} is outside the pinned contract (rig {RIG_VERSION}); HACF never calls it. "
        "Supporting a new rig version means re-pinning hyper_openrig.py after "
        "dev/tests/contract/test_openrig_live.py passes on it"
    )


class RigPlan(NamedTuple):
    """How HACF uses rig: mode delegate, native, stop or absent, with the prefix to call."""

    mode: str
    prefix: list[str] | None
    version: str | None
    warning: str | None

    @property
    def use_rig(self) -> bool:
        """True only for the pinned rig (mode delegate)."""
        return self.mode == "delegate"


def plan_rig(*, allow_download: bool = False, env: dict[str, str] | None = None) -> RigPlan:
    """Resolve rig, read `rig --version` and apply the version rule.

    With no rig (and no download consent) the plan is `absent`: HACF writes natively, silently.
    """
    prefix = resolve_rig(allow_download=allow_download)
    if prefix is None:
        return RigPlan("absent", None, None, None)
    proc = run_rig([*prefix, "--version"], env=env)
    version = proc.stdout.strip() if proc.returncode == 0 else None
    mode, warning = version_decision(version)
    return RigPlan(mode, prefix if mode == "delegate" else None, version, warning)


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

    Under the pinned rig the write is delegated to `rig proof add ... --name <name>`. Otherwise
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


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser (no abbreviated flags)."""
    parser = argparse.ArgumentParser(
        prog="hyper_openrig.py",
        description="Register this repository with a user's OpenRig catalog (rig 0.6.13).",
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
