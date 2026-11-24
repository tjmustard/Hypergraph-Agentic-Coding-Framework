"""A read-only updater run writes no lore stale mark and reports the would-be ids (OPR.99.0.10.6).

NEW-2 / F14. The updater runs as a subprocess in a temp git repository holding the lore fixture
(`dev/tests/fixtures/lore/base/`: a ledger, a graph and lore files, plus a generated
`lore/MAP.md`), with one commit that changes `src/alpha.py`. Nothing touches this repository's lore.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

import hyper_gates
import hyper_lore
import pytest
import yaml
from conftest import FIXTURES_DIR, SCRIPTS_DIR, GitRepo

UPDATER = SCRIPTS_DIR / "hypergraph_updater.py"
LORE_SCRIPT = SCRIPTS_DIR / "hyper_lore.py"
BASE = FIXTURES_DIR / "lore" / "base"
GRAPH = "lore_hypergraph.yml"
MAP = "lore/MAP.md"
LEDGER = "spec/compiled/architecture.yml"
CHANGED = ["src/alpha.py"]
SID = "S99"
HACF_ENV = ("HACF_OUTBOX", "HACF_AUTONOMOUS", "HACF_SLICE", "HACF_RUN_ID")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep outer git and engine variables away from the in-process lore calls."""
    for key in list(os.environ):
        if key.startswith("GIT_") or key in HACF_ENV:
            monkeypatch.delenv(key)


def _env(repo: GitRepo) -> dict[str, str]:
    return {key: value for key, value in repo.env.items() if key not in HACF_ENV}


def _setup(repo: GitRepo, monkeypatch: pytest.MonkeyPatch, graph: bool = True) -> GitRepo:
    """Commit the fixture (and its generated MAP.md), then a change to src/alpha.py."""
    root = repo.path
    repo.git("config", "user.name", "Lore Tester")
    repo.git("config", "user.email", "lore@example.invalid")
    shutil.copytree(BASE / "src", root / "src")
    (root / "spec" / "compiled").mkdir(parents=True)
    shutil.copyfile(BASE / "ledger.yml", root / LEDGER)
    if graph:
        shutil.copytree(BASE / "lore", root / "lore")
        shutil.copyfile(BASE / "graph.yml", root / GRAPH)
        proc = subprocess.run(
            [sys.executable, str(LORE_SCRIPT), "index"],
            cwd=root,
            env=_env(repo),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert (root / MAP).is_file()
    repo.commit("fixture lore")
    repo.write("src/alpha.py", "# alpha, changed\n")
    repo.commit("change alpha")
    for key, value in _env(repo).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(root)
    return repo


@pytest.fixture
def repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """A repository with the lore fixture and a committed change to src/alpha.py."""
    return _setup(tmp_git_repo, monkeypatch)


def updater(repo: GitRepo, *args: str) -> subprocess.CompletedProcess:
    """Run the updater's diff form over the last commit."""
    proc = subprocess.run(
        [sys.executable, str(UPDATER), "--from-diff", "HEAD^..HEAD", *args],
        cwd=repo.path,
        env=_env(repo),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def report(repo: GitRepo, *args: str) -> dict:
    """Run the updater with --json and return its report."""
    return json.loads(updater(repo, "--json", *args).stdout)


def lore_sha(repo: GitRepo) -> dict[str, str]:
    """sha256 of the graph and MAP.md."""
    return {rel: hashlib.sha256((repo.path / rel).read_bytes()).hexdigest() for rel in (GRAPH, MAP)}


def stale_ids(repo: GitRepo) -> set[str]:
    """The ids the graph marks stale."""
    data = yaml.safe_load((repo.path / GRAPH).read_text(encoding="utf-8"))
    return {node["id"] for node in data["nodes"] if node.get("stale") is True}


def selection(repo: GitRepo, radius: list[str], candidates: list[str]) -> set[str]:
    """Phase 3.5's selection: lore-check's rows (proposed + stale in radius) plus the candidates."""
    _, data = hyper_lore.read_graph(hyper_lore.repo_top())
    rows = hyper_gates.select_nodes(hyper_lore.nodes_of(data), {SID}, set(radius))
    return {row["id"] for row in rows} | set(candidates)


def test_a_read_only_runs_write_no_lore(repo: GitRepo) -> None:
    """(a) Two read-only runs leave the graph and MAP.md byte-identical and agree on the ids."""
    before = lore_sha(repo)
    assert stale_ids(repo) == set()
    first = report(repo)
    assert lore_sha(repo) == before
    second = updater(repo, "--json")
    assert lore_sha(repo) == before
    assert "lore: marked stale" not in second.stderr
    assert first["lore_stale_candidates"]
    assert json.loads(second.stdout)["lore_stale_candidates"] == first["lore_stale_candidates"]
    lines = updater(repo).stdout.splitlines()
    assert f"lore_stale_candidates: {json.dumps(first['lore_stale_candidates'])}" in lines
    assert lore_sha(repo) == before


def test_b_apply_marks_exactly_the_candidates(repo: GitRepo) -> None:
    """(b) --apply reports the candidates and marks exactly those ids stale."""
    candidates = report(repo)["lore_stale_candidates"]
    applied = updater(repo, "--json", "--apply")
    assert json.loads(applied.stdout)["lore_stale_candidates"] == candidates
    assert f"lore: marked stale: {', '.join(candidates)}" in applied.stderr
    assert stale_ids(repo) == set(candidates)


@pytest.mark.parametrize("execute_applied", [True, False])
def test_c_audit_selection_unchanged(repo: GitRepo, execute_applied: bool) -> None:
    """(c) The audit's read-only run gives the Phase 3.5 selection the old marking run gave.

    New: lore-check on the unchanged graph, plus lore_stale_candidates. Old: the read-only run
    marked the candidates itself (stale_from_impact), then lore-check alone.
    """
    if execute_applied:
        report(repo, "--apply")
    audit = report(repo)
    new = selection(repo, audit["blast_radius"], audit["lore_stale_candidates"])
    assert (
        hyper_lore.stale_from_impact(audit["blast_radius"], CHANGED)
        == (audit["lore_stale_candidates"])
    )  # what the old read-only run wrote
    old = selection(repo, audit["blast_radius"], [])
    assert new == old
    assert old  # the selection is not vacuous
    assert bool(audit["lore_stale_candidates"]) is not execute_applied


def test_d_no_lore_graph(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """(d) Without a lore graph the candidates are [] and both forms exit 0."""
    bare = _setup(tmp_git_repo, monkeypatch, graph=False)
    assert report(bare)["lore_stale_candidates"] == []
    assert report(bare, "--apply")["lore_stale_candidates"] == []
    assert not (bare.path / GRAPH).exists()
    assert hyper_lore.stale_candidates(["alpha_engine"], CHANGED) == []


def test_stale_candidates_never_raises(repo: GitRepo, monkeypatch, capsys) -> None:
    """stale_candidates warns on stderr and returns [] when the graph cannot be read."""

    def broken(top):
        raise RuntimeError("graph unreadable")

    monkeypatch.setattr(hyper_lore, "read_graph", broken)
    assert hyper_lore.stale_candidates(["alpha_engine"], CHANGED) == []
    assert "hyper_lore.stale_candidates: graph unreadable" in capsys.readouterr().err
