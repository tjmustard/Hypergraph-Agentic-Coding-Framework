"""Tests for .agents/scripts/hypergraph_updater.py (S01c) and the semantic_graph_merger guard.

The positional form is compared with the 984ca46 updater (golden), `--from-diff` runs in
throwaway repositories under tmp_path, and the scenarios in dev/tests/fixtures/diffs/ are
replayed as commits against the real ledger fixture migrated to v2. Merger runs use a copy
of the scripts under tmp_path, because the merger writes FAILED_WORKFLOWS.md beside them.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import hypergraph_updater
import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, clean_env

LEDGER = "spec/compiled/architecture.yml"
UPDATER = SCRIPTS_DIR / "hypergraph_updater.py"
HYPER_LEDGER = SCRIPTS_DIR / "hyper_ledger.py"
REAL = "ledger_v1_real_repaired.yml"
V059_UPDATER = "984ca46:.agents/scripts/hypergraph_updater.py"
DIFFS_DIR = FIXTURES_DIR / "diffs"
BLAST_LINE = re.compile(r"(?m)^Total nodes needing review \(Blast Radius\): (.*)$")
INIT_HINT = "python3 .agents/scripts/hyper_ledger.py init"

# Positional calls compared with 984ca46, as (ledger, ids). On the real fixture: a plain
# radius, implements plus depends_on, the v1 Layer fan-out (18 nodes in set order) and an
# unknown id (FATAL, exit 1). On IMPERFECT: ids hyper_ledger's id pattern rejects, named
# and radius-only. On UNPARSEABLE: 984ca46 dies with a traceback (exit 1).
GOLDEN_CALLS = {
    "updater": (REAL, ["hypergraph_updater"]),
    "implements_and_depends_on": (REAL, ["hyper_update_core_logic", "model_router"]),
    "layer_fan_out": (REAL, ["orchestration_layer"]),
    "unknown_id": (REAL, ["hypergraph_updater", "no_such_node"]),
    "non_snake_case_named": ("IMPERFECT", ["user-auth"]),
    "non_snake_case_radius": ("IMPERFECT", ["core"]),
    "unparseable": ("UNPARSEABLE", ["core"]),
}
UNPARSEABLE = "nodes:\n- id: core\n  edges: [depends_on\n"


def _node(node_id: str, dimension: str, **fields: object) -> dict:
    node = {"id": node_id, "dimension": dimension, "status": "clean"}
    if "associated_file" in fields:
        node["associated_file"] = fields["associated_file"]
    if "files" in fields:
        node["files"] = fields["files"]
    node.update(
        description=f"The {node_id} test component.",
        inputs=[],
        outputs=[],
        edges={
            "depends_on": list(fields.get("depends_on", [])),
            "implements": [],
            "member_of": list(fields.get("member_of", [])),
        },
    )
    return node


def _small_ledger() -> dict:
    """Return a v2 ledger: cli depends on core, legacy (no files) on cli; one Layer."""
    return {
        "schema": "hacf.ledger/v2",
        "nodes": [
            _node("app_layer", "Layer"),
            _node("core", "Module", files=["src/core.py"], member_of=["app_layer"]),
            _node("cli", "Module", files=["src/cli/**"], depends_on=["core"]),
            _node("legacy", "Module", associated_file="src/legacy.py", depends_on=["cli"]),
            _node("docs", "Module", files=["docs/"], member_of=["app_layer"]),
        ],
    }


def _imperfect_ledger() -> str:
    """Return a v1 ledger with ids the id pattern rejects (WebUI on core, api on user-auth)."""

    def v1_node(node_id: str, *depends_on: str) -> dict:
        node = _node(node_id, "Module", associated_file=f"src/{node_id}.py")
        node["edges"] = {"depends_on": list(depends_on), "implements": []}
        return node

    nodes = [v1_node("core"), v1_node("WebUI", "core"), v1_node("user-auth")]
    nodes.append(v1_node("api", "user-auth"))
    return yaml.safe_dump({"nodes": nodes}, sort_keys=False)


def _golden_ledger(name: str) -> bytes:
    if name == "IMPERFECT":
        return _imperfect_ledger().encode("utf-8")
    if name == "UNPARSEABLE":
        return UNPARSEABLE.encode("utf-8")
    return (FIXTURES_DIR / name).read_bytes()


def _seed_small(repo) -> Path:
    """Commit the small ledger and a file for each of its components; return the ledger."""
    path = repo.write(LEDGER, yaml.safe_dump(_small_ledger(), sort_keys=False))
    for rel in ("src/core.py", "src/cli/main.py", "src/legacy.py", "docs/index.md"):
        repo.write(rel, f"# {rel}\n")
    repo.commit("seed the ledger and its components")
    return path


def _updater(repo, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run the new updater in the repository; HACF_OUTBOX is empty unless env sets it."""
    return repo.run([sys.executable, str(UPDATER), *args], **{"HACF_OUTBOX": "", **env})


def _ledger_cli(repo, *args: str) -> subprocess.CompletedProcess:
    return repo.run([sys.executable, str(HYPER_LEDGER), *args], HACF_OUTBOX="")


def _lines(stdout: str) -> dict[str, list]:
    """Parse the diff form's `key: [json list]` lines."""
    return {
        key: json.loads(value)
        for key, value in (line.split(": ", 1) for line in stdout.splitlines())
    }


def _statuses(path: Path) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {node["id"]: node["status"] for node in data["nodes"]}


# -- positional form -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def v059_updater(tmp_path_factory) -> Path:
    """Return the 984ca46 updater, written to a temporary file."""
    show = subprocess.run(
        ["git", "show", V059_UPDATER],
        cwd=REPO_ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    path = tmp_path_factory.mktemp("v059") / "hypergraph_updater_v059.py"
    path.write_text(show.stdout, encoding="utf-8")
    return path


def _run_positional(script: Path, workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=workdir,
        env=clean_env(PYTHONHASHSEED="0", HACF_OUTBOX=""),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("case", sorted(GOLDEN_CALLS))
def test_legacy_cli_golden(tmp_path, v059_updater, case):
    """Same exit code and stdout as 984ca46; only the blast-radius line is sorted (as a set)."""
    ledger, ids = GOLDEN_CALLS[case]
    before = _golden_ledger(ledger)
    runs = {}
    for name, script in (("v059", v059_updater), ("new", UPDATER)):
        workdir = tmp_path / name
        workdir.mkdir()
        (workdir / "architecture.yml").write_bytes(before)
        runs[name] = _run_positional(script, workdir, "architecture.yml", *ids)
    old, new = runs["v059"], runs["new"]
    assert new.returncode == old.returncode, new.stdout + new.stderr
    assert BLAST_LINE.sub("<blast>", new.stdout) == BLAST_LINE.sub("<blast>", old.stdout)
    old_radius = [ast.literal_eval(match) for match in BLAST_LINE.findall(old.stdout)]
    new_radius = [ast.literal_eval(match) for match in BLAST_LINE.findall(new.stdout)]
    assert [set(radius) for radius in new_radius] == [set(radius) for radius in old_radius]
    assert all(radius == sorted(radius) for radius in new_radius)
    assert "Traceback" not in new.stderr
    if new.returncode:
        assert (tmp_path / "new" / "architecture.yml").read_bytes() == before
    else:
        old_data, new_data = (
            yaml.safe_load((tmp_path / name / "architecture.yml").read_text(encoding="utf-8"))
            for name in ("v059", "new")
        )
        assert new_data == old_data


def test_missing_ledger_message_names_init(tmp_path, tmp_git_repo, v059_updater):
    """No ledger: the 984ca46 line, then a line naming hyper_ledger.py init; exit 1."""
    old = _run_positional(v059_updater, tmp_path, "missing.yml", "hypergraph_updater")
    new = _run_positional(UPDATER, tmp_path, "missing.yml", "hypergraph_updater")
    assert old.returncode == new.returncode == 1
    assert old.stdout == "CRITICAL ERROR: Hypergraph not found at missing.yml\n"
    assert new.stdout.startswith(old.stdout)
    hint = new.stdout[len(old.stdout) :]
    assert INIT_HINT in hint and "/hyper-discover" in hint
    assert not (tmp_path / "missing.yml").exists()

    proc = _updater(tmp_git_repo, "--from-diff", "HEAD", "--json")
    assert proc.returncode == 1 and proc.stdout == ""
    assert "CRITICAL ERROR: Hypergraph not found at" in proc.stderr
    assert INIT_HINT in proc.stderr


def test_provenance_subcommands_removed(tmp_path):
    """The uncalled --write/--merge/--cleanup-provenance subcommands are usage errors now."""
    for flag in ("--write-provenance", "--merge-provenance", "--cleanup-provenance"):
        proc = _run_positional(UPDATER, tmp_path, flag, "branch")
        assert proc.returncode == 2, flag
    assert not (tmp_path / ".provenance_staging").exists()
    assert "PROVENANCE_STAGING_DIR" not in UPDATER.read_text(encoding="utf-8")


# -- --from-diff ---------------------------------------------------------------------------


def test_from_diff(tmp_git_repo):
    """A changed file maps to its node; the radius is sorted; nothing is written."""
    ledger = _seed_small(tmp_git_repo)
    before = ledger.read_bytes()
    tmp_git_repo.write("src/core.py", "# core, changed\n")
    tmp_git_repo.commit("change core")

    proc = _updater(tmp_git_repo, "--from-diff", "HEAD^..HEAD")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        'nodes: ["core"]',
        'blast_radius: ["cli", "core", "legacy"]',
        "unmapped: []",
    ]
    # One revision compares the working tree, untracked files included; a v1-style node
    # without files matches its associated_file. The leading ledger path is optional.
    tmp_git_repo.write("src/legacy.py", "# legacy, changed\n")
    tmp_git_repo.write("src/cli/extra.py", "# untracked\n")
    proc = _updater(tmp_git_repo, LEDGER, "--from-diff", "HEAD", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "nodes": ["cli", "legacy"],
        "blast_radius": ["cli", "legacy"],
        "unmapped": [],
    }
    assert ledger.read_bytes() == before


def test_from_diff_apply(tmp_git_repo):
    """--apply marks the nodes dirty and their radius needs_review, and records --slice."""
    ledger = _seed_small(tmp_git_repo)
    tmp_git_repo.write("src/core.py", "# core, changed\n")
    tmp_git_repo.commit("change core")

    proc = _updater(tmp_git_repo, "--from-diff", "HEAD^..HEAD", "--apply", "--slice", "S01c")
    assert proc.returncode == 0, proc.stderr
    assert _lines(proc.stdout)["blast_radius"] == ["cli", "core", "legacy"]
    assert _statuses(ledger) == {
        "app_layer": "clean",
        "core": "dirty",
        "cli": "needs_review",
        "legacy": "needs_review",
        "docs": "clean",
    }
    data = yaml.safe_load(ledger.read_text(encoding="utf-8"))
    slices = {node["id"]: node.get("provenance", {}).get("last_slice") for node in data["nodes"]}
    assert slices == {
        "app_layer": None,
        "core": "S01c",
        "cli": "S01c",
        "legacy": "S01c",
        "docs": None,
    }
    assert _ledger_cli(tmp_git_repo, "validate").returncode == 0


def test_from_diff_unmapped(tmp_git_repo):
    """Paths no node covers are listed under unmapped, and empty lists are printed."""
    _seed_small(tmp_git_repo)
    tmp_git_repo.write("HACF-install.sh", "#!/bin/sh\n")
    tmp_git_repo.write(".agentignore", "secrets/\n")
    tmp_git_repo.commit("installer files")

    proc = _updater(tmp_git_repo, "--from-diff", "HEAD^..HEAD")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "nodes: []",
        "blast_radius: []",
        'unmapped: [".agentignore", "HACF-install.sh"]',
    ]
    proc = _updater(tmp_git_repo, "--from-diff", "HEAD^..HEAD", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "nodes": [],
        "blast_radius": [],
        "unmapped": [".agentignore", "HACF-install.sh"],
    }


def test_from_diff_golden_fixtures(tmp_git_repo):
    """Each dev/tests/fixtures/diffs/ scenario, replayed as a commit, gives its expected output."""
    fixture = (FIXTURES_DIR / REAL).read_text(encoding="utf-8")
    seedable = {
        node["id"]: node["associated_file"]
        for node in yaml.safe_load(fixture)["nodes"]
        if node["dimension"] in ("Module", "Atomic")
    }
    for node_id, rel in seedable.items():
        tmp_git_repo.write(rel, f"# placeholder for {node_id}\n")
    ledger = tmp_git_repo.write(LEDGER, fixture)
    proc = _ledger_cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    migrated = yaml.safe_load(ledger.read_text(encoding="utf-8"))
    # The same 18 seeded files: entries as the real ledger after S01b.
    seeded = {node["id"]: node["files"] for node in migrated["nodes"] if "files" in node}
    assert seeded == {node_id: [rel] for node_id, rel in seedable.items()}
    assert len(seeded) == 18
    tmp_git_repo.commit("placeholders and the migrated ledger")

    scenarios = sorted(DIFFS_DIR.glob("*.yml"))
    assert {path.stem for path in scenarios} >= {
        "hypergraph_updater",
        "hyper_update_core",
        "unmapped_installer",
    }
    for path in scenarios:
        scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
        for rel, content in scenario["changes"].items():
            tmp_git_repo.write(rel, content)
        tmp_git_repo.commit(f"scenario {path.stem}")
        changed = tmp_git_repo.git("diff", "--name-only", "HEAD^", "HEAD").split()
        assert sorted(changed) == sorted(scenario["changes"]), path.stem
        proc = _updater(tmp_git_repo, "--from-diff", "HEAD^..HEAD", "--json")
        assert proc.returncode == 0, f"{path.stem}: {proc.stderr}"
        assert json.loads(proc.stdout) == scenario["expected"], path.stem


# -- outbox and the post-impact hook ------------------------------------------------------


def _entries(outbox: Path) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(outbox.iterdir())]


def test_outbox_routing(tmp_git_repo, tmp_path):
    """With HACF_OUTBOX set the ledger is byte-identical and the outbox holds the write."""
    ledger = _seed_small(tmp_git_repo)
    before = ledger.read_bytes()

    # A radius of just the named node is one entry.
    single = tmp_path / "outbox-single"
    proc = _updater(tmp_git_repo, LEDGER, "docs", HACF_OUTBOX=str(single))
    assert proc.returncode == 0, proc.stderr
    assert "HYPERGRAPH UPDATER: SUCCESS" in proc.stdout
    assert ledger.read_bytes() == before
    assert _entries(single) == [
        {"tool": "hyper_ledger", "op": "mark", "args": {"status": "dirty", "ids": ["docs"]}}
    ]

    # A wider radius adds a needs_review entry; apply-outbox replays both.
    outbox = tmp_path / "outbox"
    proc = _updater(tmp_git_repo, LEDGER, "core", HACF_OUTBOX=str(outbox))
    assert proc.returncode == 0, proc.stderr
    assert ledger.read_bytes() == before
    assert [entry["args"] for entry in _entries(outbox)] == [
        {"status": "dirty", "ids": ["core"]},
        {"status": "needs_review", "ids": ["cli", "legacy"]},
    ]
    assert _ledger_cli(tmp_git_repo, "apply-outbox", str(outbox)).returncode == 0
    assert _statuses(ledger) == {
        "app_layer": "clean",
        "core": "dirty",
        "cli": "needs_review",
        "legacy": "needs_review",
        "docs": "clean",
    }

    # --from-diff --apply --outbox routes the write too; --slice cannot travel in a mark entry.
    after_replay = ledger.read_bytes()
    tmp_git_repo.write("docs/index.md", "# docs, changed\n")
    tmp_git_repo.commit("change docs")
    flagged = tmp_path / "outbox-diff"
    proc = _updater(
        tmp_git_repo,
        "--from-diff",
        "HEAD^..HEAD",
        "--apply",
        "--slice",
        "S01c",
        "--outbox",
        str(flagged),
    )
    assert proc.returncode == 0, proc.stderr
    assert ledger.read_bytes() == after_replay
    assert [entry["args"] for entry in _entries(flagged)] == [{"status": "dirty", "ids": ["docs"]}]
    assert "--slice S01c is not recorded" in proc.stderr


def test_outbox_refuses_unreplayable_ids(tmp_git_repo, tmp_path):
    """Under an outbox, a named or radius id apply-outbox cannot replay exits 2; nothing is kept."""
    ledger = tmp_git_repo.write(LEDGER, _imperfect_ledger())
    before = ledger.read_bytes()
    for ids, refused in ((["user-auth"], "user-auth"), (["core"], "WebUI")):
        outbox = tmp_path / f"outbox-{ids[0]}"
        proc = _updater(tmp_git_repo, LEDGER, *ids, HACF_OUTBOX=str(outbox))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert proc.stdout == ""
        assert f"node id(s) {refused} do not match" in proc.stderr
        assert ledger.read_bytes() == before
        assert not list(outbox.glob("*"))
    # Without an outbox the same ledger is marked, as in 984ca46.
    proc = _updater(tmp_git_repo, LEDGER, "core")
    assert proc.returncode == 0, proc.stderr
    assert _statuses(ledger) == {
        "core": "dirty",
        "WebUI": "needs_review",
        "user-auth": "clean",
        "api": "clean",
    }


def test_post_impact_hook(tmp_git_repo, monkeypatch, capsys):
    """stale_from_impact gets the radius and changed paths; a missing hyper_lore is skipped."""
    ledger = _seed_small(tmp_git_repo)
    tmp_git_repo.write("src/core.py", "# core, changed\n")
    tmp_git_repo.commit("change core")
    for key, value in tmp_git_repo.env.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("HACF_OUTBOX", "")
    monkeypatch.chdir(tmp_git_repo.path)

    calls = []

    def stale_from_impact(node_ids, changed_paths):
        calls.append((node_ids, changed_paths, os.environ.get("HACF_OUTBOX")))
        return ["ADR-0003"]

    fake = types.ModuleType("hyper_lore")
    fake.stale_from_impact = stale_from_impact
    monkeypatch.setitem(sys.modules, "hyper_lore", fake)
    assert hypergraph_updater.main(["--from-diff", "HEAD^..HEAD", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["blast_radius"] == ["cli", "core", "legacy"]
    assert "lore: marked stale: ADR-0003" in captured.err
    assert hypergraph_updater.main([LEDGER, "docs"]) == 0
    outbox = tmp_git_repo.scratch / "outbox"
    args = ["--from-diff", "HEAD^..HEAD", "--apply", "--outbox", str(outbox)]
    assert hypergraph_updater.main(args) == 0
    assert calls == [
        (["cli", "core", "legacy"], ["src/core.py"], ""),
        (["docs"], [], ""),
        (["cli", "core", "legacy"], ["src/core.py"], str(outbox)),
    ]
    assert _statuses(ledger)["docs"] == "dirty"
    capsys.readouterr()

    def broken(node_ids, changed_paths):
        raise RuntimeError("lore graph unreadable")

    fake.stale_from_impact = broken
    assert hypergraph_updater.main(["--from-diff", "HEAD^..HEAD"]) == 0
    assert "stale_from_impact failed: lore graph unreadable" in capsys.readouterr().err

    monkeypatch.setitem(sys.modules, "hyper_lore", None)  # import hyper_lore raises ImportError
    assert hypergraph_updater.main(["--from-diff", "HEAD^..HEAD", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["nodes"] == ["core"]
    assert captured.err == ""


# -- semantic_graph_merger -----------------------------------------------------------------


@pytest.fixture
def merger(tmp_path) -> Path:
    """Copy the merger and its imports to tmp_path, where it writes FAILED_WORKFLOWS.md."""
    scripts = tmp_path / "hacf" / ".agents" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("semantic_graph_merger.py", "hyper_ledger.py", "_runtime.py"):
        shutil.copyfile(SCRIPTS_DIR / name, scripts / name)
    return scripts / "semantic_graph_merger.py"


def _merge(script: Path, *paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *map(str, paths)],
        cwd=script.parent,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _write_ledger(path: Path, nodes: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": "hacf.ledger/v2", "nodes": nodes}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _merge_inputs(tmp_path: Path, name: str, branch_nodes: list[dict]) -> tuple[Path, ...]:
    main = _write_ledger(tmp_path / "main" / name, [_node("core", "Module")])
    branch = _write_ledger(tmp_path / "branch" / name, [_node("core", "Module"), *branch_nodes])
    output = _write_ledger(tmp_path / "out" / name, [_node("core", "Module")])
    return main, branch, output


def test_merger_guard(tmp_path, merger):
    """Only architecture.yml/.yaml basenames are merged; other YAML files exit 1 untouched."""
    for name in ("lore_hypergraph.yml", "docker-compose.yml"):
        main, branch, output = _merge_inputs(tmp_path / name, name, [_node("cli", "Module")])
        before = output.read_bytes()
        proc = _merge(merger, main, branch, output)
        assert proc.returncode == 1, name
        assert f"Rejected: {main}" in proc.stdout
        assert "architecture.yml or architecture.yaml" in proc.stdout
        assert output.read_bytes() == before

    accepted = tmp_path / "accepted"
    main, branch, output = _merge_inputs(accepted, "architecture.yml", [_node("cli", "Module")])
    proc = _merge(merger, main, branch, output)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    merged = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert [node["id"] for node in merged["nodes"]] == ["core", "cli"]

    # The one-argument conflict-marker form keeps the basename for its temporary copies.
    conflicted = accepted / "conflict" / "architecture.yaml"
    conflicted.parent.mkdir()
    ours, theirs = (
        yaml.safe_dump([_node(node_id, "Module")], sort_keys=False) for node_id in ("api", "web")
    )
    conflicted.write_text(
        f"schema: hacf.ledger/v2\nnodes:\n<<<<<<< HEAD\n{ours}=======\n{theirs}>>>>>>> branch\n",
        encoding="utf-8",
    )
    proc = _merge(merger, conflicted)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    merged = yaml.safe_load(conflicted.read_text(encoding="utf-8"))
    assert [node["id"] for node in merged["nodes"]] == ["api", "web"]


def test_merger_validation_non_regressive(tmp_path, merger):
    """A merge keeps the main input's own errors but is refused when it adds a new one."""
    dangling = _node("core", "Module", depends_on=["ghost"])  # a pre-existing error in main
    main = _write_ledger(tmp_path / "main" / "architecture.yml", [dangling])
    branch = _write_ledger(
        tmp_path / "branch" / "architecture.yml", [dangling, _node("cli", "Module")]
    )
    output = _write_ledger(tmp_path / "out" / "architecture.yml", [dangling])
    proc = _merge(merger, main, branch, output)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    bad = {**_node("web", "Module"), "dimension": "Service"}
    _write_ledger(branch, [dangling, bad])
    before = output.read_bytes()
    proc = _merge(merger, main, branch, output)
    assert proc.returncode == 1
    assert "ARCH_SCHEMA_VALIDATION_FAILURE" in proc.stdout and "[bad_dimension]" in proc.stdout
    assert output.read_bytes() == before
    failed = tmp_path / "hacf" / "spec" / "active" / "FAILED_WORKFLOWS.md"
    assert "ARCH_SCHEMA_VALIDATION_FAILURE" in failed.read_text(encoding="utf-8")
