#!/usr/bin/env python3
"""hyper_scaffold.py — idempotent v0.6.0 project scaffold with root-collision detection (S12a).

Normative design: HACF's v0.6.0 design notes (not shipped): target layout, the installer
(FLOW step 6, ROOT COLLISIONS) and D2. It runs in the git work tree of the working directory.
HACF-install.sh (S12b) and the migration call it; it is never run on the HACF repository itself.

CLI:
    check [--json]
    apply [--yes] [--dry-run]

Exit codes: 0 ok, 2 usage, 5 foreign collision (nothing written).

check classifies the root items SPEC.md, project.yaml, workspace.yaml and the missions root
through `hyper_scope.py project init --check-only --json` (S03b), and lore_hypergraph.yml and
lore/ itself, as missing, hacf, openrig (adopted) or foreign. lore_hypergraph.yml is hacf when
it is a regular file declaring `schema: hacf.lore/v1`; lore/ is hacf when it is a real directory
and the graph is hacf, or the graph is missing and every file under lore/ is a `.gitkeep` (a
scaffold that has not created its graph yet). Anything else that exists is foreign. Any foreign
item exits 5 and prints the report.

apply runs check first and exits 5 with nothing written on any foreign item. Then, each step
reporting `created`, `kept`, `adopted` or `skipped`:
1. `hyper_scope.py project init` creates the missing root items; openrig items are adopted
   byte-identical.
2. lore/ADRs/, lore/STORIES/, lore/proposed/ and lore/canon/ (R8), each with a `.gitkeep` when
   empty. lore_hypergraph.yml, lore/MAP.md and lore/CANON.md are written only by hyper_lore.py
   (it has one writer). A missing graph is `created` by `hyper_lore.py init`, which writes the
   empty hacf.lore/v1 graph and generates MAP.md and CANON.md (both reported `created`). An
   existing hacf graph is `kept`, and `hyper_lore.py index` creates a missing MAP.md or
   CANON.md (R49).
3. Local files, each only when missing: `.agents/local/hacf.yml` (team defaults
   `openrig.id_prefix: OPR` and `code_graph.backend: none`, never a security key),
   `.agents/state/.gitignore` (`*`), `.agents/memory/activeContext.md` (from
   `.agents/schemas/activeContext_template.md`), spec/{active,archive,compiled,process}/ and
   tests/{candidate_outputs,fixtures,integration}/ (each with a `.gitkeep` when empty).
4. The `.gitignore` managed block `gitignore` through hyper_blocks, then
   `hyper_lore.py install-drivers` (repo-local merge-driver config and the `merge-drivers` block
   of `<git-common-dir>/info/attributes`, never a tracked .gitattributes, R10), whose block is
   then linted with hyper_blocks.

A second apply writes nothing (same bytes, same mtimes). --dry-run prints the planned writes and
writes nothing. --yes is accepted for the installer's non-interactive runs; apply never asks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import _runtime

_runtime.require_runtime()  # R19

import hyper_blocks  # noqa: E402
import yaml  # noqa: E402

EXIT_OK, EXIT_USAGE, EXIT_FOREIGN = 0, 2, 5
MISSING, HACF, OPENRIG, FOREIGN = "missing", "hacf", "openrig", "foreign"
CREATED, KEPT, ADOPTED, SKIPPED = "created", "kept", "adopted", "skipped"

SCRIPTS_DIR = Path(__file__).resolve().parent
SCOPE_SCRIPT = SCRIPTS_DIR / "hyper_scope.py"
LORE_SCRIPT = SCRIPTS_DIR / "hyper_lore.py"
ACTIVE_TEMPLATE = SCRIPTS_DIR.parent / "schemas" / "activeContext_template.md"

LORE_SCHEMA = "hacf.lore/v1"
GRAPH_REL = "lore_hypergraph.yml"
LORE_DIR = "lore"
LORE_SUBDIRS = ("lore/ADRs", "lore/STORIES", "lore/proposed", "lore/canon")
LORE_GENERATED = ("lore/MAP.md", "lore/CANON.md")
SPEC_DIRS = ("spec/active", "spec/archive", "spec/compiled", "spec/process")
TEST_DIRS = ("tests/candidate_outputs", "tests/fixtures", "tests/integration")
GITKEEP = ".gitkeep"
LOCAL_CONFIG_REL = ".agents/local/hacf.yml"
LOCAL_CONFIG = (
    "# HACF per-project selections: team defaults only (tracked; never shipped or overwritten).\n"
    "openrig:\n"
    "  id_prefix: OPR\n"
    "code_graph:\n"
    "  backend: none\n"
)
STATE_IGNORE_REL = ".agents/state/.gitignore"
ACTIVE_CONTEXT_REL = ".agents/memory/activeContext.md"
GITIGNORE_REL = ".gitignore"
GITIGNORE_LINES = (
    ".agents/state/",
    ".agents/.backup/",
    ".agents/logs/",
    "/exhaust/",
    "/.openrig/",
    ".agents/**/__pycache__/",
)


class ScaffoldError(Exception):
    """A failure that ends the command; `code` is its exit code."""

    code = EXIT_USAGE


class Item(NamedTuple):
    """One classified root item."""

    item: str
    path: str
    cls: str
    reason: str

    def record(self) -> dict[str, str]:
        """Return the item as a JSON record."""
        return {"item": self.item, "path": self.path, "class": self.cls, "reason": self.reason}


# ---------------------------------------------------------------------------
# Repository and subprocesses (argv lists only)
# ---------------------------------------------------------------------------


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run argv in cwd and capture its output; never raises on a non-zero exit."""
    try:
        return subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def repo_top(cwd: Optional[Path] = None) -> Path:
    """Return the git work-tree top of cwd, or raise ScaffoldError (exit 2)."""
    proc = _run(["git", "rev-parse", "--show-toplevel"], cwd or Path.cwd())
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        raise ScaffoldError("not inside a git work tree; run hyper_scaffold.py in the project")
    return Path(top).resolve()


def common_dir(top: Path) -> Path:
    """Return `$(git rev-parse --git-common-dir)` as an absolute path."""
    proc = _run(["git", "rev-parse", "--git-common-dir"], top)
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise ScaffoldError(f"cannot find the git common dir of {top}")
    path = Path(text)
    return (path if path.is_absolute() else top / path).resolve()


def _script(script: Path, *args: str, top: Path) -> subprocess.CompletedProcess:
    """Run one of the sibling HACF scripts under this interpreter in the project."""
    return _run([sys.executable, str(script), *args], top)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def classify_root_items(top: Path) -> list[Item]:
    """Classify SPEC.md, project.yaml, workspace.yaml and the missions root (hyper_scope)."""
    proc = _script(SCOPE_SCRIPT, "project", "init", "--check-only", "--json", top=top)
    try:
        payload = json.loads(proc.stdout)
        records = payload["items"]
        return [Item(r["item"], r["path"], r["class"], r["reason"]) for r in records]
    except (ValueError, KeyError, TypeError):
        detail = (proc.stderr or proc.stdout).strip()
        raise ScaffoldError(
            f"hyper_scope.py project init --check-only failed (exit {proc.returncode}): {detail}"
        ) from None


def classify_graph(top: Path) -> tuple[str, str]:
    """Classify lore_hypergraph.yml: hacf when it declares hacf.lore/v1, else foreign."""
    path = top / GRAPH_REL
    if not os.path.lexists(path):
        return MISSING, "absent"
    if path.is_symlink() or not path.is_file():
        return FOREIGN, "is not a regular file"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return FOREIGN, f"does not parse: {exc.__class__.__name__}"
    if isinstance(data, dict) and data.get("schema") == LORE_SCHEMA:
        return HACF, f"schema {LORE_SCHEMA}"
    return FOREIGN, f"does not declare schema {LORE_SCHEMA}"


def _only_gitkeeps(folder: Path) -> bool:
    """Return True when every non-directory entry under folder is a regular `.gitkeep`."""
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames:
            if (Path(dirpath) / name).is_symlink():
                return False
        for name in filenames:
            path = Path(dirpath) / name
            if name != GITKEEP or path.is_symlink():
                return False
    return True


def classify_lore_dir(top: Path, graph_cls: str) -> tuple[str, str]:
    """Classify lore/ against the graph's class."""
    path = top / LORE_DIR
    if not os.path.lexists(path):
        return MISSING, "absent"
    if path.is_symlink() or not path.is_dir():
        return FOREIGN, "is not a real directory"
    if graph_cls == HACF:
        return HACF, f"{GRAPH_REL} is HACF's"
    if graph_cls == MISSING and _only_gitkeeps(path):
        return HACF, "an HACF scaffold without its graph yet (only .gitkeep files)"
    return FOREIGN, f"holds files and {GRAPH_REL} is {graph_cls}"


def check(top: Path) -> list[Item]:
    """Return every classified item: the four root items, then lore_hypergraph.yml and lore/."""
    items = classify_root_items(top)
    graph_cls, graph_reason = classify_graph(top)
    lore_cls, lore_reason = classify_lore_dir(top, graph_cls)
    items.append(Item(GRAPH_REL, GRAPH_REL, graph_cls, graph_reason))
    items.append(Item("lore/", "lore/", lore_cls, lore_reason))
    return items


def print_items(items: list[Item], as_json: bool) -> None:
    """Print the classification as text lines or as JSON."""
    if as_json:
        foreign = [item.item for item in items if item.cls == FOREIGN]
        print(json.dumps({"items": [i.record() for i in items], "foreign": foreign}, indent=2))
        return
    for item in items:
        print(f"{item.item}: {item.cls} ({item.reason})")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class Step(NamedTuple):
    """One planned step: its report path, its status, and the write it needs (None: none)."""

    path: str
    status: str
    write: Optional[Callable[[], None]]
    detail: str = ""


def _mkdir_steps(top: Path, rels: tuple[str, ...]) -> list[Step]:
    """Plan each directory, created when missing, with a `.gitkeep` when it is empty."""
    steps = []
    for rel in rels:
        folder = top / rel
        if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
            raise ScaffoldError(f"{rel} exists and is not a real directory; nothing was written")
        empty = not folder.exists() or not any(folder.iterdir())
        if not empty:
            steps.append(Step(f"{rel}/", KEPT, None))
            continue

        def write(folder: Path = folder) -> None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / GITKEEP).touch(exist_ok=True)

        steps.append(Step(f"{rel}/{GITKEEP}", CREATED, write))
    return steps


def _plain_dir_steps(top: Path, rels: tuple[str, ...]) -> list[Step]:
    """Plan each directory, created when missing (no .gitkeep)."""
    steps = []
    for rel in rels:
        folder = top / rel
        if os.path.lexists(folder):
            if folder.is_symlink() or not folder.is_dir():
                raise ScaffoldError(
                    f"{rel} exists and is not a real directory; nothing was written"
                )
            steps.append(Step(f"{rel}/", KEPT, None))
        else:
            steps.append(Step(f"{rel}/", CREATED, lambda f=folder: f.mkdir(parents=True)))
    return steps


def _file_step(top: Path, rel: str, content: Callable[[], Optional[bytes]]) -> Step:
    """Plan one file created only when missing; content() None means skipped."""
    path = top / rel
    if os.path.lexists(path):
        return Step(rel, KEPT, None)
    data = content()
    if data is None:
        return Step(rel, SKIPPED, None, "its template is missing")

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        hyper_blocks.cas_write(path, None, data)

    return Step(rel, CREATED, write)


def _template() -> Optional[bytes]:
    """Return the activeContext template's bytes, or None when it is missing."""
    try:
        return ACTIVE_TEMPLATE.read_bytes()
    except OSError:
        return None


def _root_steps(top: Path, items: list[Item]) -> list[Step]:
    """Plan the root items: project init for the missing ones, adopted and kept for the rest."""
    root = [item for item in items if item.item not in (GRAPH_REL, "lore/")]
    missing = [item for item in root if item.cls == MISSING]

    def init() -> None:
        proc = _script(SCOPE_SCRIPT, "project", "init", top=top)
        if proc.returncode != 0:
            error = ScaffoldError(
                f"hyper_scope.py project init exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
            error.code = EXIT_FOREIGN if proc.returncode == EXIT_FOREIGN else EXIT_USAGE
            raise error

    steps = []
    for item in root:
        if item.cls == MISSING:
            write = init if item is missing[0] else None  # one project init creates them all
            steps.append(Step(item.path, CREATED, write))
        elif item.cls == OPENRIG:
            steps.append(Step(item.path, ADOPTED, None, item.reason))
        else:
            steps.append(Step(item.path, KEPT, None))
    return steps


def _lore_steps(top: Path, graph_cls: str) -> list[Step]:
    """Plan the lore tree; the graph, MAP.md and CANON.md only through hyper_lore.py (R49)."""
    steps = _mkdir_steps(top, LORE_SUBDIRS)
    if graph_cls != HACF:  # check() refused a foreign graph, so it is missing

        def init() -> None:
            proc = _script(LORE_SCRIPT, "init", top=top)
            if proc.returncode != 0:
                raise ScaffoldError(
                    f"hyper_lore.py init exited {proc.returncode}: {proc.stderr.strip()}"
                )

        steps.append(Step(GRAPH_REL, CREATED, init))
        for rel in LORE_GENERATED:  # init writes them
            steps.append(Step(rel, CREATED, None))
        return steps
    steps.append(Step(GRAPH_REL, KEPT, None))
    missing = [rel for rel in LORE_GENERATED if not os.path.lexists(top / rel)]

    def index() -> None:
        proc = _script(LORE_SCRIPT, "index", top=top)
        if proc.returncode != 0:
            raise ScaffoldError(
                f"hyper_lore.py index exited {proc.returncode}: {proc.stderr.strip()}"
            )

    for rel in LORE_GENERATED:
        if rel in missing:
            steps.append(Step(rel, CREATED, index if rel == missing[0] else None))
        else:
            steps.append(Step(rel, KEPT, None))
    return steps


def _gitignore_step(top: Path) -> Step:
    """Plan the `.gitignore` managed block through hyper_blocks."""
    path = top / GITIGNORE_REL
    body = "".join(line + "\n" for line in GITIGNORE_LINES)
    data = hyper_blocks.read_optional(hyper_blocks.guard(path))
    if data is not None:
        doc = hyper_blocks.parse_doc(data)
        new = hyper_blocks.render_upsert(
            doc, hyper_blocks.HASH, hyper_blocks.GITIGNORE_BLOCK_ID, hyper_blocks.body_lines(body)
        )
        if new.to_bytes() == data:
            return Step(f"{GITIGNORE_REL} block {hyper_blocks.GITIGNORE_BLOCK_ID}", KEPT, None)

    def write() -> None:
        hyper_blocks.upsert(path, hyper_blocks.GITIGNORE_BLOCK_ID, body)

    return Step(f"{GITIGNORE_REL} block {hyper_blocks.GITIGNORE_BLOCK_ID}", CREATED, write)


def _drivers_current(top: Path) -> bool:
    """Return True when the merge-driver config and the info/attributes block are in place."""
    import hyper_lore  # the one writer of both; read here only to plan

    for key, value in hyper_lore.DRIVER_CONFIG:
        proc = _run(["git", "config", "--local", "--get", key], top)
        if proc.returncode != 0 or proc.stdout.rstrip("\n") != value:
            return False
    attributes = common_dir(top) / "info" / "attributes"
    data = hyper_blocks.read_optional(attributes)
    if data is None:
        return False
    doc = hyper_blocks.parse_doc(data)
    block = hyper_blocks.find_block(
        hyper_blocks.scan(doc, hyper_blocks.HASH), hyper_blocks.ATTRIBUTES_BLOCK_ID
    )
    if block is None:
        return False
    return hyper_blocks.body_of(doc, block) == [
        line.encode() for line in hyper_lore.ATTRIBUTE_LINES
    ]


def _drivers_step(top: Path) -> Step:
    """Plan `hyper_lore.py install-drivers` and the R26 lint of the block it writes."""
    label = "<git-common-dir>/info/attributes block merge-drivers"
    attributes = common_dir(top) / "info" / "attributes"

    def verify() -> None:
        blocks = hyper_blocks.check_file(attributes)
        if hyper_blocks.find_block(blocks, hyper_blocks.ATTRIBUTES_BLOCK_ID) is None:
            raise ScaffoldError(f"{attributes} has no merge-drivers block after install-drivers")

    if _drivers_current(top):
        verify()
        return Step(label, KEPT, None)

    def write() -> None:
        proc = _script(LORE_SCRIPT, "install-drivers", top=top)
        if proc.returncode != 0:
            raise ScaffoldError(
                f"hyper_lore.py install-drivers exited {proc.returncode}: {proc.stderr.strip()}"
            )
        verify()

    return Step(label, CREATED, write)


def plan(top: Path, items: list[Item]) -> list[Step]:
    """Return every apply step in order, writing nothing."""
    graph_cls = next(item.cls for item in items if item.item == GRAPH_REL)
    steps = _root_steps(top, items)
    steps += _lore_steps(top, graph_cls)
    steps.append(_file_step(top, LOCAL_CONFIG_REL, lambda: LOCAL_CONFIG.encode()))
    steps.append(_file_step(top, STATE_IGNORE_REL, lambda: b"*\n"))
    steps.append(_file_step(top, ACTIVE_CONTEXT_REL, _template))
    steps += _plain_dir_steps(top, SPEC_DIRS)
    steps += _mkdir_steps(top, TEST_DIRS)
    steps.append(_gitignore_step(top))
    steps.append(_drivers_step(top))
    return steps


def _line(step: Step, prefix: str = "") -> str:
    """Return one report line."""
    return f"{prefix}{step.status}: {step.path}" + (f" ({step.detail})" if step.detail else "")


def apply(top: Path, dry_run: bool = False) -> int:
    """Run check, then every step; exit 5 with nothing written on a foreign item."""
    items = check(top)
    if any(item.cls == FOREIGN for item in items):
        print_items(items, as_json=False)
        foreign = ", ".join(item.item for item in items if item.cls == FOREIGN)
        print(
            f"hyper_scaffold.py: foreign item(s): {foreign}; nothing was written (D2)",
            file=sys.stderr,
        )
        return EXIT_FOREIGN
    steps = plan(top, items)
    if dry_run:
        for step in steps:
            print(_line(step, "would be " if step.status == CREATED else ""))
        print("dry run: nothing was written")
        return EXIT_OK
    for step in steps:
        if step.write is not None:
            step.write()
        print(_line(step))
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="hyper_scaffold.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="classify the root items; write nothing")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("apply", help="create the missing scaffold items")
    p.add_argument("--yes", "-y", action="store_true", help="never ask (apply never asks anyway)")
    p.add_argument("--dry-run", action="store_true", help="print the planned writes only")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Parse argv, run the command and map errors to exit codes."""
    ns = build_parser().parse_args(argv)
    try:
        top = repo_top()
        if ns.command == "check":
            items = check(top)
            print_items(items, ns.json)
            return EXIT_FOREIGN if any(item.cls == FOREIGN for item in items) else EXIT_OK
        return apply(top, dry_run=ns.dry_run)
    except (ScaffoldError, hyper_blocks.BlocksError) as exc:
        print(f"hyper_scaffold.py: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
