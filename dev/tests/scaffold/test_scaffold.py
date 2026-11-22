"""Deterministic tests for hyper_scaffold.py and the v0.5.9 fixture builder (S12a; D2, R8, R10).

Every project is built under tmp_path by dev/tests/fixtures/build_user_project_v059.py; the
scaffold runs as a subprocess in it, under this interpreter, with git's user and system config
switched off. Nothing writes into the repository.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import hyper_blocks
import pytest
import yaml
from conftest import FIXTURES_DIR, SCRIPTS_DIR, clean_env

SCRIPT = SCRIPTS_DIR / "hyper_scaffold.py"
BUILDER_PATH = FIXTURES_DIR / "build_user_project_v059.py"
_spec = importlib.util.spec_from_file_location("build_user_project_v059", BUILDER_PATH)
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

STATUSES = ("created", "kept", "adopted", "skipped")
LORE_SCRIPT = SCRIPTS_DIR / "hyper_lore.py"
LORE_INIT_FILES = ("lore_hypergraph.yml", "lore/MAP.md", "lore/CANON.md")
EMPTY_LORE_GRAPH = {
    "schema": "hacf.lore/v1",
    "id_counters": {"ADR": 0, "STORY": 0, "CANON": 0},
    "nodes": [],
}
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


def scaffold(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Run hyper_scaffold.py in the project."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(project),
        env=clean_env(**GIT_ENV),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def lore_cli(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Run hyper_lore.py in the project."""
    return subprocess.run(
        [sys.executable, str(LORE_SCRIPT), *args],
        cwd=str(project),
        env=clean_env(**GIT_ENV),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def git(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in the project."""
    return subprocess.run(
        ["git", *args],
        cwd=str(project),
        env=clean_env(**GIT_ENV),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def snapshot(root: Path) -> dict[str, tuple]:
    """Return every path under root (bytes and mtime per file), plus .git's config and info/."""
    found: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    for rel in (".git/config", ".git/info/attributes"):
        path = root / rel
        found[rel] = ("file", path.read_bytes()) if path.exists() else ("absent",)
    return found


def build(tmp_path: Path, variant: str = "default", name: str | None = None) -> Path:
    """Build a fixture project under tmp_path."""
    return builder.build(tmp_path / (name or variant), variant)


def report(proc: subprocess.CompletedProcess) -> dict[str, str]:
    """Map each report line's path to its status; assert every line uses the vocabulary."""
    statuses = {}
    for line in proc.stdout.splitlines():
        status, _, rest = line.partition(": ")
        assert status in STATUSES, line
        statuses[rest.split(" (")[0]] = status
    return statuses


# ---------------------------------------------------------------------------
# Collisions
# ---------------------------------------------------------------------------


def _lore_graph_collision(project: Path) -> str:
    (project / "lore_hypergraph.yml").write_text("nodes: []\n", encoding="utf-8")
    return "lore_hypergraph.yml"


def _lore_dir_collision(project: Path) -> str:
    (project / "lore").mkdir()
    (project / "lore" / "notes.md").write_text("# our folklore\n", encoding="utf-8")
    return "lore/"


COLLISIONS = {
    "collision-spec": "SPEC.md",
    "collision-project-yaml": "project.yaml",
    "collision-workspace-yaml": "workspace.yaml",
    "collision-missions": "missions/",
    "lore-graph": _lore_graph_collision,
    "lore-dir": _lore_dir_collision,
}


@pytest.mark.parametrize("case", list(COLLISIONS))
def test_collision_matrix(tmp_path: Path, case: str) -> None:
    """Each foreign root item: check says foreign and exits 5; apply exits 5 writing nothing."""
    expected = COLLISIONS[case]
    if callable(expected):
        project = build(tmp_path)
        expected = expected(project)
    else:
        project = build(tmp_path, case)
    before = snapshot(project)

    proc = scaffold(project, "check", "--json")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["foreign"] == [expected]
    classes = {item["item"]: item["class"] for item in payload["items"]}
    assert classes[expected] == "foreign"
    assert set(classes) == {
        "SPEC.md",
        "project.yaml",
        "workspace.yaml",
        "missions/",
        "lore_hypergraph.yml",
        "lore/",
    }

    proc = scaffold(project, "apply", "--yes")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "nothing was written" in proc.stderr
    assert snapshot(project) == before
    assert git(project, "config", "--local", "--get", "merge.hacf-lore.driver").returncode != 0


def test_openrig_adopted(tmp_path: Path) -> None:
    """OpenRig project.yaml and workspace.yaml are adopted byte-identical; the rest applies."""
    project = build(tmp_path)
    project_yaml = (
        "schema: openrig.project/v0alpha1\nkind: project\n"
        "install:\n  intent: SPEC.md\n  context: [docs/overview.md]\n  skills: []\n"
        "missions:\n  root: missions\n"
    ).encode()
    workspace_yaml = (
        "schema: openrig.workspace/v0alpha1\n# written by rig\nprojects:\n  - id: billing\n"
        "    root: .\n"
    ).encode()
    (project / "project.yaml").write_bytes(project_yaml)
    (project / "workspace.yaml").write_bytes(workspace_yaml)

    proc = scaffold(project, "check", "--json")
    assert proc.returncode == 0, proc.stderr
    classes = {item["item"]: item["class"] for item in json.loads(proc.stdout)["items"]}
    assert classes["project.yaml"] == classes["workspace.yaml"] == "openrig"

    proc = scaffold(project, "apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    statuses = report(proc)
    assert statuses["project.yaml"] == statuses["workspace.yaml"] == "adopted"
    assert statuses["SPEC.md"] == statuses["missions/"] == "created"
    assert (project / "project.yaml").read_bytes() == project_yaml
    assert (project / "workspace.yaml").read_bytes() == workspace_yaml
    assert (project / "SPEC.md").is_file() and (project / "missions").is_dir()
    assert (project / "lore" / "canon" / ".gitkeep").is_file()
    assert "# BEGIN HACF MANAGED BLOCK: gitignore" in (project / ".gitignore").read_text()


# ---------------------------------------------------------------------------
# A full apply, idempotency and the attributes rule
# ---------------------------------------------------------------------------


def test_idempotent(tmp_path: Path) -> None:
    """Apply twice: the second run reports nothing created and changes no bytes and no mtimes."""
    project = build(tmp_path)
    before = snapshot(project)
    proc = scaffold(project, "apply", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "would be created: lore/canon/.gitkeep" in proc.stdout
    assert "would be created: lore_hypergraph.yml" in proc.stdout  # through hyper_lore.py init
    assert snapshot(project) == before  # --dry-run writes nothing

    first = scaffold(project, "apply", "--yes")
    assert first.returncode == 0, first.stdout + first.stderr
    statuses = report(first)
    for rel in (
        "SPEC.md",
        "project.yaml",
        "workspace.yaml",
        "missions/",
        "lore/ADRs/.gitkeep",
        "lore/STORIES/.gitkeep",
        "lore/proposed/.gitkeep",
        "lore/canon/.gitkeep",
        ".agents/local/hacf.yml",
        ".agents/state/.gitignore",
        "spec/active/",
        "spec/process/",
        "tests/candidate_outputs/.gitkeep",
        "tests/fixtures/.gitkeep",
        "tests/integration/.gitkeep",
        ".gitignore block gitignore",
        "<git-common-dir>/info/attributes block merge-drivers",
    ):
        assert statuses[rel] == "created", rel
    # The v0.5.9 project already had these; they are kept byte-identical.
    assert statuses[".agents/memory/activeContext.md"] == "kept"
    assert statuses["spec/compiled/"] == statuses["spec/archive/"] == "kept"
    assert before[".agents/memory/activeContext.md"][1] == (
        (project / ".agents/memory/activeContext.md").read_bytes()
    )
    # The lore graph has one writer, hyper_lore.py: `init` creates it with MAP.md and CANON.md.
    for rel in LORE_INIT_FILES:
        assert statuses[rel] == "created", rel
    assert yaml.safe_load((project / "lore_hypergraph.yml").read_text()) == EMPTY_LORE_GRAPH
    validate = lore_cli(project, "validate")
    assert validate.returncode == 0, validate.stdout + validate.stderr
    local = (project / ".agents/local/hacf.yml").read_text()
    assert "id_prefix: OPR" in local and "backend: none" in local
    assert "security" not in local and "sandbox" not in local
    assert (project / ".agents/state/.gitignore").read_text() == "*\n"
    gitignore = (project / ".gitignore").read_text()
    for line in builder_gitignore_lines():
        assert f"\n{line}\n" in gitignore

    after = snapshot(project)
    second = scaffold(project, "apply")
    assert second.returncode == 0, second.stdout + second.stderr
    statuses = report(second)
    assert "created" not in statuses.values()
    for rel in LORE_INIT_FILES:
        assert statuses[rel] == "kept", rel
    assert snapshot(project) == after
    check = scaffold(project, "check")
    assert check.returncode == 0, check.stdout + check.stderr


def test_scaffold_keeps_existing_lore_graph(tmp_path: Path) -> None:
    """Test 11: a valid graph already present is kept byte-identical and init is not run."""
    project = build(tmp_path)
    init = lore_cli(project, "init")
    assert init.returncode == 0, init.stdout + init.stderr
    body = tmp_path / "body.md"
    body.write_text("# Kept\n\nAn existing decision.\n", encoding="utf-8")
    proposed = lore_cli(
        project, "propose", "--type", "decision", "--title", "Kept", "--body-file", str(body)
    )
    assert proposed.returncode == 0, proposed.stdout + proposed.stderr
    graph = (project / "lore_hypergraph.yml").read_bytes()
    stamp = (project / "lore_hypergraph.yml").stat().st_mtime_ns

    proc = scaffold(project, "apply", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    statuses = report(proc)
    assert statuses["lore_hypergraph.yml"] == "kept"
    assert statuses["lore/MAP.md"] == statuses["lore/CANON.md"] == "kept"
    assert (project / "lore_hypergraph.yml").read_bytes() == graph
    assert (project / "lore_hypergraph.yml").stat().st_mtime_ns == stamp
    assert "hyper_lore.py init" not in proc.stdout + proc.stderr


def builder_gitignore_lines() -> tuple[str, ...]:
    """Return the .gitignore block lines the scaffold must write."""
    return (
        ".agents/state/",
        ".agents/.backup/",
        ".agents/logs/",
        "/exhaust/",
        "/.openrig/",
        ".agents/**/__pycache__/",
    )


def test_scaffold_writes_info_attributes_not_gitattributes(tmp_path: Path) -> None:
    """The merge-drivers block goes to <git-common-dir>/info/attributes; .gitattributes is kept."""
    project = build(tmp_path)
    lfs = (project / ".gitattributes").read_bytes()
    assert b"filter=lfs" in lfs
    proc = scaffold(project, "apply", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    common = git(project, "rev-parse", "--git-common-dir").stdout.strip()
    attributes = (project / common / "info" / "attributes").resolve()
    blocks = hyper_blocks.check_file(attributes)
    block = hyper_blocks.find_block(blocks, "merge-drivers")
    assert block is not None
    doc = hyper_blocks.parse_doc(attributes.read_bytes())
    assert hyper_blocks.body_of(doc, block) == [
        b"lore_hypergraph.yml merge=hacf-lore",
        b"lore/MAP.md merge=hacf-generated",
        b"lore/CANON.md merge=hacf-generated",
    ]
    assert (project / ".gitattributes").read_bytes() == lfs
    assert git(project, "status", "--porcelain", "--", ".gitattributes").stdout == ""
    found = [
        p for p in project.rglob(".gitattributes") if ".git" not in p.relative_to(project).parts
    ]
    assert found == [project / ".gitattributes"]
    driver = git(project, "config", "--local", "--get", "merge.hacf-lore.driver")
    assert driver.stdout.strip().endswith("hyper_lore.py merge-driver %O %A %B %P")


# ---------------------------------------------------------------------------
# The fixture builder (extra)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", builder.VARIANTS)
def test_fixture_builder_variants(tmp_path: Path, variant: str) -> None:
    """Every variant builds into tmp_path from local history, with its distinguishing item."""
    dest = builder.build(tmp_path / "p", variant)
    assert dest == tmp_path / "p"
    assert (dest / builder.DISTINGUISHING[variant]).exists()
    assert git(dest, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert git(dest, "status", "--porcelain").stdout == ""
    hacf = (dest / ".agents/scripts/hypergraph_updater.py").is_file()
    if variant == "openrig-only-skills":
        assert not hacf and not (dest / "AGENTS.md").exists()
        assert sorted(p.name for p in (dest / ".agents").iterdir()) == ["skills"]
        return
    assert hacf and (dest / "HACF-uninstall.sh").is_file()
    assert (dest / ".claude/commands/hyper-execute.md").is_file()
    assert (dest / "spec/archive/MiniPRD_00_Setup_AUDITED.md").is_file()
    assert "Execution Checklist" in (dest / "spec/compiled/SuperPRD_demo.md").read_text()
    assert (dest / "spec/compiled/architecture.yml").read_bytes() == (
        (FIXTURES_DIR / "ledger_v1_real_broken.yml").read_bytes()
    )
    assert (dest / "docs/adr").is_dir() and (dest / "services/billing/docs/adr").is_dir()
    assert b"filter=lfs" in (dest / ".gitattributes").read_bytes()
    for name in ("pyproject.toml", "uv.lock", "LICENSE", ".claude/settings.json"):
        assert (dest / name).is_file(), name
    agents = (dest / "AGENTS.md").read_bytes()
    templates = {
        name: hyper_blocks.known_template_shas(name)[0] for name in ("AGENTS.md", "CLAUDE.md")
    }
    if variant == "custom-instructions":
        for name in ("AGENTS.md", "CLAUDE.md"):
            data = (dest / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() not in templates[name]
            assert hyper_blocks.check_file(dest / name) == []
    else:
        assert b"<!-- BEGIN OpenRig MANAGED BLOCK: role -->" in agents
        claude = (dest / "CLAUDE.md").read_bytes()
        assert hashlib.sha256(claude).hexdigest() in templates["CLAUDE.md"]


def test_fixture_builder_refuses_a_nonempty_dest(tmp_path: Path) -> None:
    """The builder writes only into a missing or empty dest."""
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "keep.txt").write_text("x")
    with pytest.raises(builder.BuildError):
        builder.build(tmp_path / "busy")
    assert sorted(p.name for p in (tmp_path / "busy").iterdir()) == ["keep.txt"]
