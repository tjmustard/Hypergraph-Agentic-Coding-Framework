"""Tests for .agents/scripts/hyper_ledger.py (S01a), the single validated ledger writer.

They cover validation, non-regressive locked writes, edge editing, review patches,
tombstones, the outbox and blast radius. Every test works in a throwaway repository under
tmp_path (`tmp_git_repo`); the real-ledger fixtures are S00's copies of the HACF ledger
(`ledger_v1_real_repaired.yml` parses, `ledger_v1_real_broken.yml` does not).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import _runtime
import hyper_ledger
import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR

LEDGER = "spec/compiled/architecture.yml"
SCRIPT = str(SCRIPTS_DIR / "hyper_ledger.py")
REAL = "ledger_v1_real_repaired.yml"
BROKEN = "ledger_v1_real_broken.yml"
SAMPLE = "ledger_v2_sample.yml"

WORKER = """\
import sys
sys.path.insert(0, SCRIPTS)
import hyper_ledger
for n in range(1, 51):
    ref = "OPR.%s.%d" % (sys.argv[1], n)
    code = hyper_ledger.main(["ensure-node", "--id", "component_ledger", "--spec-refs", ref])
    if code:
        sys.exit(code)
"""


def _cli(repo, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run hyper_ledger.py in the repository; HACF_OUTBOX is empty unless env sets it."""
    return repo.run([sys.executable, SCRIPT, *args], **{"HACF_OUTBOX": "", **env})


def _seed(repo, fixture: str = SAMPLE) -> Path:
    """Copy a fixture to the repository's ledger path and return that path."""
    return repo.write(LEDGER, (FIXTURES_DIR / fixture).read_text(encoding="utf-8"))


def _seed_data(repo, data: dict) -> Path:
    return repo.write(LEDGER, yaml.safe_dump(data, sort_keys=False))


def _sample() -> dict:
    return yaml.safe_load((FIXTURES_DIR / SAMPLE).read_text(encoding="utf-8"))


def _read(repo) -> dict:
    return yaml.safe_load((repo.path / LEDGER).read_text(encoding="utf-8"))


def _node(data: dict, node_id: str) -> dict:
    return next(node for node in data["nodes"] if node["id"] == node_id)


def _validate(repo, *args: str) -> tuple[int, dict]:
    """Return (exit code, JSON report) of `validate --json`."""
    proc = _cli(repo, "validate", "--json", *args)
    return proc.returncode, json.loads(proc.stdout)


def _rules(findings: list[dict]) -> set[str]:
    return {finding["rule"] for finding in findings}


def _lines(stdout: str) -> dict[str, list]:
    """Parse impact's `key: [json list]` lines."""
    return {
        key: json.loads(value)
        for key, value in (line.split(": ", 1) for line in stdout.splitlines())
    }


# -- validate -----------------------------------------------------------------------------


def _set_edges(data: dict, node_id: str, **edges: object) -> None:
    _node(data, node_id)["edges"].update(edges)


def _cycle(data: dict) -> None:
    _set_edges(data, "component_ledger", depends_on=["ledger_validate"])
    _set_edges(data, "ledger_validate", depends_on=["component_ledger"])


DEFECTS = {
    "dangling_edge": lambda d: _set_edges(d, "component_ledger", depends_on=["ghost_node"]),
    "inverted_implements": lambda d: _set_edges(
        d, "orchestration_layer", implements=["component_ledger"]
    ),
    "duplicate_id": lambda d: d["nodes"].append(copy.deepcopy(_node(d, "ledger_validate"))),
    "cycle": _cycle,
    "bad_dimension": lambda d: _node(d, "ledger_validate").update(dimension="Service"),
    "unsafe_glob": lambda d: _node(d, "ledger_validate").update(files=["../outside.py"]),
    "null_edges": lambda d: _set_edges(d, "component_ledger", depends_on=None),
}


@pytest.mark.parametrize("rule", sorted(DEFECTS))
def test_validate_detects(tmp_git_repo, rule):
    """Each defect class is an error: validate exits 1 and names the rule."""
    data = _sample()
    DEFECTS[rule](data)
    _seed_data(tmp_git_repo, data)
    code, report = _validate(tmp_git_repo)
    assert code == 1
    assert rule in _rules(report["errors"]), report["errors"]


@pytest.mark.parametrize(
    "glob", ["/etc/passwd", "src/../x.py", "src\\ledger.py", "**", "*/ledger.py", "./src/a.py"]
)
def test_validate_detects_unsafe_globs(tmp_git_repo, glob):
    """Absolute, `..`, backslash, catch-all, wildcard-first and `.` globs are errors."""
    data = _sample()
    _node(data, "ledger_validate")["files"] = [glob]
    _seed_data(tmp_git_repo, data)
    code, report = _validate(tmp_git_repo)
    assert code == 1
    assert [(f["rule"], f["detail"]) for f in report["errors"]] == [("unsafe_glob", repr(glob))]


def test_validate_sample_clean_and_parse_error(tmp_git_repo):
    """The v2 sample has no finding; an unparseable ledger exits 4 with line and column."""
    _seed(tmp_git_repo)
    proc = _cli(tmp_git_repo, "validate", "--strict")
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout == "validate --strict: ok\n"
    _seed(tmp_git_repo, BROKEN)
    proc = _cli(tmp_git_repo, "validate")
    assert proc.returncode == 4
    assert f"{LEDGER}:314:5: YAML parse error" in proc.stdout
    assert _cli(tmp_git_repo, "mark", "--dirty", "--ids", "hyper_fix").returncode == 4


# -- non-regression -----------------------------------------------------------------------


def test_non_regression(tmp_git_repo):
    """On the real ledger a status write succeeds; a new dangling edge is refused unchanged."""
    path = _seed(tmp_git_repo, REAL)
    proc = _cli(tmp_git_repo, "mark", "--dirty", "--ids", "hyper_fix")
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "hyper_fix")["status"] == "dirty"
    code, report = _validate(tmp_git_repo)
    assert code == 0 and report["errors"] == []
    labels = [f for f in report["warnings"] if f["rule"] == "v1_label_endpoint"]
    assert len(labels) == 81
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "set-edges", "--id", "hyper_fix", "--add-depends-on", "ghost_node")
    assert proc.returncode == 5
    assert "[dangling_edge]" in proc.stderr
    assert path.read_bytes() == before


def test_non_regression_carries_preexisting_errors(tmp_git_repo):
    """A pre-existing error prints as a warning; only a new error refuses a write."""
    data = _sample()
    _set_edges(data, "ledger_validate", depends_on=["ghost_node"])
    path = _seed_data(tmp_git_repo, data)
    proc = _cli(tmp_git_repo, "mark", "--dirty", "--ids", "ledger_validate")
    assert proc.returncode == 0, proc.stderr
    assert "warning: node ledger_validate: edges.depends_on names ghost_node" in proc.stdout
    assert "(pre-existing error)" in proc.stdout
    before = path.read_bytes()
    proc = _cli(
        tmp_git_repo, "set-edges", "--id", "component_ledger", "--add-depends-on", "other_ghost"
    )
    assert proc.returncode == 5
    assert path.read_bytes() == before


def test_write_keeps_mode_and_leaves_no_temp_file(tmp_git_repo):
    """An atomic save keeps the file mode, leaves only the ledger and creates .agents/state."""
    path = _seed(tmp_git_repo)
    path.chmod(0o640)
    assert _cli(tmp_git_repo, "mark", "--dirty", "--ids", "ledger_validate").returncode == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert sorted(os.listdir(path.parent)) == ["architecture.yml"]
    assert (tmp_git_repo.path / ".agents" / "state").is_dir()
    text = path.read_text(encoding="utf-8")
    assert text.count("  description: >-\n") == len(_sample()["nodes"])


def test_against_ref_reports_dropped_keys(tmp_git_repo, tmp_path):
    """--against-ref and --against-file report a dropped per-node key and a dropped id."""
    _seed(tmp_git_repo)
    tmp_git_repo.commit("ledger", LEDGER)
    code, report = _validate(tmp_git_repo, "--against-ref", "HEAD")
    assert code == 0 and report["errors"] == []
    data = _sample()
    del _node(data, "ledger_validate")["outputs"]
    _seed_data(tmp_git_repo, data)
    reference = tmp_path / "base.yml"
    reference.write_text((FIXTURES_DIR / SAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
    for flag, value in (("--against-ref", "HEAD"), ("--against-file", str(reference))):
        code, report = _validate(tmp_git_repo, flag, value)
        assert code == 1
        found = [(f["rule"], f["node"], f["detail"]) for f in report["errors"]]
        assert found == [("dropped_key", "ledger_validate", "outputs")]
    data = _sample()
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "ledger_validate"]
    _node(data, "component_ledger")["outputs"] = []
    _seed_data(tmp_git_repo, data)
    code, report = _validate(tmp_git_repo, "--against-ref", "HEAD")
    assert code == 1
    assert [(f["rule"], f["node"]) for f in report["errors"]] == [
        ("dropped_node", "ledger_validate")
    ]
    assert _cli(tmp_git_repo, "validate", "--against-ref", "no-such-ref").returncode == 2


# -- lock -----------------------------------------------------------------------------------


def test_lock_before_open(tmp_git_repo, tmp_path):
    """2 processes x 50 read-modify-write cycles: no lost update, the file always parses."""
    path = _seed(tmp_git_repo)
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER.replace("SCRIPTS", repr(str(SCRIPTS_DIR))), encoding="utf-8")
    env = {**tmp_git_repo.env, "HACF_OUTBOX": ""}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(k)],
            cwd=tmp_git_repo.path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for k in (1, 2)
    ]
    reads = 0
    while any(proc.poll() is None for proc in procs):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # never a torn file
        assert isinstance(data, dict) and len(data["nodes"]) == 4
        reads += 1
    errors = [proc.communicate(timeout=60)[1] for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0], errors
    refs = _node(_read(tmp_git_repo), "component_ledger")["spec_refs"]
    expected = {f"OPR.{k}.{n}" for k in (1, 2) for n in range(1, 51)}
    assert len(refs) == 101 and set(refs) == {"OPR.0.6.0.1"} | expected
    assert reads > 0


def test_lock_shared_across_worktrees(tmp_git_repo, tmp_path):
    """Two linked worktrees resolve the same <git-common-dir>/hacf/locks/ledger.lock."""
    _seed(tmp_git_repo)
    tmp_git_repo.commit("ledger", LEDGER)
    linked = tmp_path / "linked"
    tmp_git_repo.git("worktree", "add", "-q", "-b", "linked", str(linked))
    expected = (tmp_git_repo.path / ".git").resolve() / "hacf" / "locks" / "ledger.lock"
    assert hyper_ledger.lock_file_for(tmp_git_repo.path / LEDGER) == expected
    assert hyper_ledger.lock_file_for(linked / LEDGER) == expected
    proc = subprocess.run(
        [sys.executable, SCRIPT, "mark", "--dirty", "--ids", "ledger_validate"],
        cwd=linked,
        env={**tmp_git_repo.env, "HACF_OUTBOX": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert expected.is_file()
    assert _node(_read(tmp_git_repo), "ledger_validate")["status"] == "clean"  # other tree


def test_lock_falls_back_beside_a_ledger_outside_git(tmp_path, monkeypatch):
    """Outside any work tree the lock is `.<ledger name>.lock` beside the ledger."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    ledger = tmp_path / "loose" / "architecture.yml"
    assert hyper_ledger.lock_file_for(ledger) == tmp_path / "loose" / ".architecture.yml.lock"


def test_lock_timeout_exits_3(tmp_git_repo, monkeypatch):
    """A write that cannot take the lock within the bounded wait exits 3, file untouched."""
    path = _seed(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo.path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_git_repo.path.parent))
    monkeypatch.setenv("HACF_OUTBOX", "")
    monkeypatch.setattr(hyper_ledger, "LOCK_TIMEOUT", 0.2)
    before = path.read_bytes()
    with hyper_ledger.ledger_lock(path):
        assert hyper_ledger.main(["mark", "--dirty", "--ids", "ledger_validate"]) == 3
    assert path.read_bytes() == before


# -- edges and tombstones -------------------------------------------------------------------


def test_set_edges_cycle_refused(tmp_git_repo):
    """An --add-depends-on that closes a cycle exits 5 and leaves the file unchanged."""
    path = _seed(tmp_git_repo)
    proc = _cli(
        tmp_git_repo, "set-edges", "--id", "component_ledger", "--add-depends-on", "ledger_validate"
    )
    assert proc.returncode == 0, proc.stderr
    before = path.read_bytes()
    proc = _cli(
        tmp_git_repo, "set-edges", "--id", "ledger_validate", "--add-depends-on", "component_ledger"
    )
    assert proc.returncode == 5
    assert "[cycle]" in proc.stderr
    assert path.read_bytes() == before


def test_remove_node_refused_then_ok_after_set_edges(tmp_git_repo):
    """remove-node lists the inbound edges it refuses on; after set-edges it succeeds."""
    path = _seed(tmp_git_repo)
    for args in (
        ("ensure-node", "--id", "ledger_cli", "--dimension", "Module"),
        ("ensure-node", "--id", "ledger_cli", "--parent", "orchestration_layer"),
        ("set-edges", "--id", "component_ledger", "--add-depends-on", "ledger_cli"),
    ):
        assert _cli(tmp_git_repo, *args).returncode == 0
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "remove-node", "ledger_cli", "--reason", "merged")
    assert proc.returncode == 5
    assert "component_ledger: edges.depends_on -> ledger_cli" in proc.stderr
    assert path.read_bytes() == before
    proc = _cli(
        tmp_git_repo, "set-edges", "--id", "component_ledger", "--remove-depends-on", "ledger_cli"
    )
    assert proc.returncode == 0, proc.stderr
    proc = _cli(tmp_git_repo, "remove-node", "ledger_cli", "--reason", "merged")
    assert proc.returncode == 0, proc.stderr
    assert "ledger_cli" not in {node["id"] for node in _read(tmp_git_repo)["nodes"]}


def test_remove_node_writes_tombstone(tmp_git_repo):
    """removed_nodes gains {id, reason, slice, commit}; --against-ref does not call it dropped."""
    _seed(tmp_git_repo)
    args = ("ensure-node", "--id", "ledger_cli", "--dimension", "Module")
    assert _cli(tmp_git_repo, *args, "--parent", "orchestration_layer").returncode == 0
    head = tmp_git_repo.commit("ledger", LEDGER)
    proc = _cli(
        tmp_git_repo, "remove-node", "ledger_cli", "--reason", "folded in", "--slice", "S01a"
    )
    assert proc.returncode == 0, proc.stderr
    data = _read(tmp_git_repo)
    assert data["removed_nodes"][-1] == {
        "id": "ledger_cli",
        "reason": "folded in",
        "slice": "S01a",
        "commit": head,
    }
    code, report = _validate(tmp_git_repo, "--against-ref", "HEAD")
    assert code == 0 and report["errors"] == [], report
    path = tmp_git_repo.path / LEDGER
    before = path.read_bytes()
    assert _cli(tmp_git_repo, *args).returncode == 5  # a tombstoned id is never reused
    assert path.read_bytes() == before
    data["removed_nodes"].pop()
    _seed_data(tmp_git_repo, data)
    code, report = _validate(tmp_git_repo, "--against-ref", "HEAD")
    assert code == 1 and _rules(report["errors"]) == {"dropped_node"}


# -- ensure-node ----------------------------------------------------------------------------


def test_parent_member_of_for_layer(tmp_git_repo):
    """--parent with a Layer (or System) parent writes member_of, never implements."""
    _seed(tmp_git_repo, REAL)
    proc = _cli(
        tmp_git_repo,
        "ensure-node",
        "--id",
        "component_ledger",
        "--dimension",
        "Module",
        "--parent",
        "orchestration_layer",
    )
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "component_ledger")["edges"] == {
        "depends_on": [],
        "implements": [],
        "member_of": ["orchestration_layer"],
    }
    _seed(tmp_git_repo)
    args = (
        "ensure-node",
        "--id",
        "other_layer",
        "--dimension",
        "Layer",
        "--parent",
        "sample_system",
    )
    assert _cli(tmp_git_repo, *args).returncode == 0
    edges = _node(_read(tmp_git_repo), "other_layer")["edges"]
    assert edges["member_of"] == ["sample_system"] and edges["implements"] == []


def test_parent_implements_for_module(tmp_git_repo):
    """An Atomic node with a Module parent gets implements; other combinations exit 2."""
    path = _seed(tmp_git_repo)
    args = ("ensure-node", "--id", "ledger_lock", "--dimension", "Atomic")
    proc = _cli(tmp_git_repo, *args, "--parent", "component_ledger")
    assert proc.returncode == 0, proc.stderr
    edges = _node(_read(tmp_git_repo), "ledger_lock")["edges"]
    assert edges == {"depends_on": [], "implements": ["component_ledger"], "member_of": []}
    before = path.read_bytes()
    for bad in (
        (
            "ensure-node",
            "--id",
            "x_module",
            "--dimension",
            "Module",
            "--parent",
            "component_ledger",
        ),
        ("ensure-node", "--id", "x_atomic", "--dimension", "Atomic", "--parent", "ledger_validate"),
        ("ensure-node", "--id", "x_atomic", "--dimension", "Atomic", "--parent", "ghost_node"),
        ("ensure-node", "--id", "x_new", "--parent", "orchestration_layer"),  # no --dimension
    ):
        assert _cli(tmp_git_repo, *bad).returncode == 2, bad
    assert path.read_bytes() == before


def test_mixed_v1_v2_warning(tmp_git_repo):
    """ensure-node writes v2-only fields into a v1 ledger; validate warns but exits 0."""
    _seed(tmp_git_repo, REAL)
    code, report = _validate(tmp_git_repo)
    assert code == 0 and "mixed_v1_v2" not in _rules(report["warnings"])
    proc = _cli(
        tmp_git_repo,
        "ensure-node",
        "--id",
        "component_ledger",
        "--dimension",
        "Module",
        "--parent",
        "orchestration_layer",
        "--files",
        ".agents/scripts/hyper_ledger.py",
    )
    assert proc.returncode == 0, proc.stderr
    code, report = _validate(tmp_git_repo)
    assert code == 0 and report["errors"] == []
    assert "mixed_v1_v2" in _rules(report["warnings"])
    proc = _cli(tmp_git_repo, "validate")
    assert proc.returncode == 0 and "mixed v1/v2 ledger" in proc.stdout


def test_strict_fails_on_needs_review(tmp_git_repo):
    """A node left needs_review fails validate --strict and is listed separately."""
    _seed(tmp_git_repo)
    assert _cli(tmp_git_repo, "mark", "--needs-review", "--ids", "ledger_validate").returncode == 0
    assert _cli(tmp_git_repo, "validate").returncode == 0
    proc = _cli(tmp_git_repo, "validate", "--strict")
    assert proc.returncode == 1
    assert "leftover needs_review:\n  - ledger_validate\n" in proc.stdout
    code, report = _validate(tmp_git_repo, "--strict")
    assert code == 1 and report["leftover_needs_review"] == ["ledger_validate"]


def test_init_writes_empty_v2_and_refuses_existing(tmp_git_repo):
    """Init writes a valid empty v2 ledger; a second init exits 5 and changes nothing."""
    proc = _cli(tmp_git_repo, "init")
    assert proc.returncode == 0, proc.stderr
    path = tmp_git_repo.path / LEDGER
    assert path.read_text(encoding="utf-8") == "schema: hacf.ledger/v2\nnodes: []\n"
    code, report = _validate(tmp_git_repo, "--strict")
    assert code == 0 and report["errors"] == report["warnings"] == []
    before = path.read_bytes()
    assert _cli(tmp_git_repo, "init").returncode == 5
    assert path.read_bytes() == before
    assert _cli(tmp_git_repo, "init", "elsewhere/ledger.yml").returncode == 0
    elsewhere = tmp_git_repo.path / "elsewhere" / "ledger.yml"
    assert elsewhere.read_bytes() == before


def test_ensure_node_from_legacy_header(tmp_git_repo, tmp_path):
    """--from-spec reads the legacy header (or v6 frontmatter); prose never becomes an edge."""
    _seed(tmp_git_repo, REAL)
    spec = tmp_path / "MiniPRD_99_X.md"
    spec.write_text(
        "# MiniPRD: X\n\n**Hypergraph Node ID:** x\n**Parent Node:** orchestration_layer\n\n"
        "## 1. The Confidence Mandate\nThis module depends on hypergraph_updater.\n",
        encoding="utf-8",
    )
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec), "--dimension", "Module")
    assert proc.returncode == 2  # `x` is not a valid id
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("** x\n", "** x_node\n"), encoding="utf-8"
    )
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec), "--dimension", "Module")
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "x_node")["edges"] == {
        "depends_on": [],
        "implements": [],
        "member_of": ["orchestration_layer"],
    }
    orphan = tmp_path / "MiniPRD_98_Y.md"
    orphan.write_text("**Hypergraph Node ID:** `y_node`\n**Parent Node:** none\n", encoding="utf-8")
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(orphan), "--dimension", "Module")
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "y_node")["edges"] == {"depends_on": [], "implements": []}
    v6 = tmp_path / "SPEC.md"
    v6.write_text(
        "---\nnode_id: z_node\nparent: orchestration_layer\nhacf_touches:\n  - src/z.py\n"
        "  - src/z/**\n---\n# Slice z\n",
        encoding="utf-8",
    )
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(v6), "--dimension", "Module")
    assert proc.returncode == 0, proc.stderr
    z_node = _node(_read(tmp_git_repo), "z_node")
    assert z_node["files"] == ["src/z.py"]
    assert "skipped glob touch src/z/**; set globs with --files" in proc.stderr
    assert z_node["edges"]["member_of"] == ["orchestration_layer"]
    args = ("ensure-node", "--from-spec", str(spec), "--id", "other_node")
    assert _cli(tmp_git_repo, *args).returncode == 2


def _v6_spec(tmp_path, touches: list[str], node_id: str = "component_ledger") -> Path:
    """Write a v6 SPEC.md whose frontmatter maps node_id to the given hacf_touches."""
    spec = tmp_path / "SPEC.md"
    front = {"node_id": node_id, "parent": "orchestration_layer", "hacf_touches": touches}
    spec.write_text(f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n# Slice\n", "utf-8")
    return spec


def _seed_files(repo, files: list[str]) -> Path:
    """Seed the v2 sample ledger with component_ledger's files set to the given list."""
    data = _sample()
    _node(data, "component_ledger")["files"] = files
    return _seed_data(repo, data)


def test_ensure_node_from_spec_merges_exact_touches(tmp_git_repo, tmp_path):
    """--from-spec appends the exact touches a node lacks, keeps its files, skips globs (R11)."""
    path = _seed_files(tmp_git_repo, ["a.py", "b.py"])
    spec = _v6_spec(tmp_path, ["b.py", "c.py", "dev/tests/**/*.py"])
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert "skipped glob touch dev/tests/**/*.py; set globs with --files" in proc.stderr
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["a.py", "b.py", "c.py"]
    once = path.read_bytes()
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert path.read_bytes() == once  # a repeat is byte-identical


def test_ensure_node_from_spec_all_glob_touches_keep_files(tmp_git_repo, tmp_path):
    """A spec whose touches are all globs leaves an existing node's files unchanged."""
    _seed_files(tmp_git_repo, ["a.py", "b.py"])
    spec = _v6_spec(tmp_path, ["src/**", "dev/tests/test_?.py", "src/[ab].py"])
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count("ensure-node: skipped glob touch ") == 3
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["a.py", "b.py"]


def test_ensure_node_files_with_from_spec_exits_2(tmp_git_repo, tmp_path):
    """--files replaces and --from-spec merges: together they exit 2; --files alone replaces."""
    path = _seed_files(tmp_git_repo, ["a.py", "b.py"])
    before = path.read_bytes()
    spec = _v6_spec(tmp_path, ["c.py"])
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec), "--files", "x.py")
    assert proc.returncode == 2
    assert "--files replaces files and --from-spec merges them; pass one" in proc.stderr
    assert path.read_bytes() == before
    proc = _cli(tmp_git_repo, "ensure-node", "--id", "component_ledger", "--files", "x.py")
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["x.py"]


def test_ensure_node_from_spec_outbox_records_exact_add_files(tmp_git_repo, tmp_path):
    """The outbox entry holds add_files (exact paths only); apply-outbox replays it the same."""
    path = _seed_files(tmp_git_repo, ["a.py", "b.py"])
    before = path.read_bytes()
    spec = _v6_spec(tmp_path, ["b.py", "c.py", "dev/tests/**/*.py"])
    outbox = tmp_path / "outbox"
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec), HACF_OUTBOX=str(outbox))
    assert proc.returncode == 0, proc.stderr
    assert path.read_bytes() == before
    entry = json.loads((outbox / "000001-ensure-node.json").read_text(encoding="utf-8"))
    assert entry["args"]["add_files"] == ["b.py", "c.py"]
    assert entry["args"]["files"] is None
    proc = _cli(tmp_git_repo, "apply-outbox", str(outbox))
    assert proc.returncode == 0, proc.stderr
    replayed = path.read_bytes()
    path.write_bytes(before)
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert path.read_bytes() == replayed
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["a.py", "b.py", "c.py"]


def test_ensure_node_list_flags_and_spec_refs(tmp_git_repo):
    """--files is one comma list; --spec-refs appends missing dot-IDs; a bad item exits 2."""
    path = _seed(tmp_git_repo, REAL)
    base = (
        "ensure-node",
        "--id",
        "x_mod",
        "--dimension",
        "Module",
        "--parent",
        "orchestration_layer",
    )
    assert _cli(tmp_git_repo, *base, "--files", "a.py,b.py").returncode == 0
    assert _node(_read(tmp_git_repo), "x_mod")["files"] == ["a.py", "b.py"]
    assert (
        _cli(tmp_git_repo, "ensure-node", "--id", "x_mod", "--spec-refs", "OPR.0.6.0.1").returncode
        == 0
    )
    assert _node(_read(tmp_git_repo), "x_mod")["spec_refs"] == ["OPR.0.6.0.1"]
    refs = ("ensure-node", "--id", "x_mod", "--spec-refs", "OPR.0.6.0.1,OPR.0.6.0.2")
    assert _cli(tmp_git_repo, *refs).returncode == 0
    assert _node(_read(tmp_git_repo), "x_mod")["spec_refs"] == ["OPR.0.6.0.1", "OPR.0.6.0.2"]
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, *refs)
    assert proc.returncode == 0 and "no change" in proc.stdout
    assert path.read_bytes() == before  # a repeat is a no-op
    proc = _cli(tmp_git_repo, "ensure-node", "--id", "x_mod", "--spec-refs", "not-an-id")
    assert proc.returncode == 2
    assert path.read_bytes() == before


# -- ensure-node owner guard (OPR.99.0.6.4) ------------------------------------------------


def _owned_ledger(repo) -> Path:
    """Seed the v2 sample with other_owner holding a literal path and an openrig/hacf/** glob."""
    data = _sample()
    _node(data, "component_ledger")["files"] = ["a.py", "shared.md"]
    _node(data, "ledger_validate")["files"] = ["owned.py", "openrig/hacf/**", "shared.md"]
    return _seed_data(repo, data)


def test_owners_of_literal_glob_and_glob_path():
    """owners_of matches an equal entry, a glob entry and (for a glob path) a literal entry."""
    data = _sample()
    _node(data, "ledger_validate")["files"] = ["owned.py", "openrig/hacf/**"]
    owners = hyper_ledger.owners_of
    assert owners(data, "owned.py", "component_ledger") == ["ledger_validate"]
    assert owners(data, "openrig/hacf/rig.yml", "component_ledger") == ["ledger_validate"]
    assert owners(data, "openrig/hacf/**", "component_ledger") == ["ledger_validate"]
    assert owners(data, "*.py", "x_node") == ["ledger_validate"]
    assert owners(data, "src/ledger.py", "ledger_validate") == ["component_ledger"]
    assert owners(data, "free.py", "component_ledger") == []
    assert owners(data, "owned.py", "ledger_validate") == []  # the node itself is excluded


def test_ensure_node_from_spec_skips_owned_touches(tmp_git_repo, tmp_path):
    """--from-spec skips a touch another node owns (literal or glob) and names the owner."""
    path = _owned_ledger(tmp_git_repo)
    spec = _v6_spec(tmp_path, ["owned.py", "openrig/hacf/rig.yml", "c.py", "shared.md"])
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert "ensure-node: skipped owned.py: owned by ledger_validate" in proc.stderr
    assert "ensure-node: skipped openrig/hacf/rig.yml: owned by ledger_validate" in proc.stderr
    assert "shared.md" not in proc.stderr  # already the node's own path: never re-checked
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["a.py", "shared.md", "c.py"]
    once = path.read_bytes()
    assert _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec)).returncode == 0
    assert path.read_bytes() == once


def test_ensure_node_files_refuses_owned_path_unless_allow_shared(tmp_git_repo):
    """--files with an owned new path exits 2 and writes nothing; --allow-shared shares it."""
    path = _owned_ledger(tmp_git_repo)
    before = path.read_bytes()
    args = ("ensure-node", "--id", "component_ledger", "--files", "a.py,owned.py,openrig/hacf/x.md")
    proc = _cli(tmp_git_repo, *args)
    assert proc.returncode == 2
    assert (
        "refused: owned.py is owned by ledger_validate; pass --allow-shared to share it"
        in proc.stderr
    )
    assert "refused: openrig/hacf/x.md is owned by ledger_validate" in proc.stderr
    assert path.read_bytes() == before
    proc = _cli(tmp_git_repo, *args, "--allow-shared")
    assert proc.returncode == 0, proc.stderr
    assert "ensure-node: shared owned.py with ledger_validate" in proc.stderr
    assert "ensure-node: shared openrig/hacf/x.md with ledger_validate" in proc.stderr
    files = _node(_read(tmp_git_repo), "component_ledger")["files"]
    assert files == ["a.py", "owned.py", "openrig/hacf/x.md"]


def test_ensure_node_files_keeps_existing_shared_path(tmp_git_repo):
    """An entry the node already has is not re-checked, even when another node owns it."""
    _owned_ledger(tmp_git_repo)
    proc = _cli(
        tmp_git_repo, "ensure-node", "--id", "component_ledger", "--files", "shared.md,b.py"
    )
    assert proc.returncode == 0, proc.stderr
    assert "shared" not in proc.stderr.replace("shared.md", "")
    assert _node(_read(tmp_git_repo), "component_ledger")["files"] == ["shared.md", "b.py"]


def test_ensure_node_new_node_owner_guard(tmp_git_repo, tmp_path):
    """A new node's initial files follow the same rules for --files and --from-spec."""
    path = _owned_ledger(tmp_git_repo)
    before = path.read_bytes()
    new = (
        "ensure-node",
        "--id",
        "x_mod",
        "--dimension",
        "Module",
        "--parent",
        "orchestration_layer",
    )
    proc = _cli(tmp_git_repo, *new, "--files", "x.py,owned.py")
    assert proc.returncode == 2
    assert "refused: owned.py is owned by ledger_validate" in proc.stderr
    assert path.read_bytes() == before
    spec = _v6_spec(tmp_path, ["x.py", "openrig/hacf/a.yml"], node_id="x_mod")
    proc = _cli(tmp_git_repo, "ensure-node", "--from-spec", str(spec), "--dimension", "Module")
    assert proc.returncode == 0, proc.stderr
    assert "ensure-node: skipped openrig/hacf/a.yml: owned by ledger_validate" in proc.stderr
    assert _node(_read(tmp_git_repo), "x_mod")["files"] == ["x.py"]
    proc = _cli(tmp_git_repo, *new[:2], "y_mod", *new[3:], "--files", "owned.py", "--allow-shared")
    assert proc.returncode == 0, proc.stderr
    assert _node(_read(tmp_git_repo), "y_mod")["files"] == ["owned.py"]


def test_validate_real_ledger_copy_ok(tmp_git_repo):
    """Validate is ok on copies of the real ledger (no ownership rule; shared paths stay valid).

    The copies are S00's fixture and, when present, this checkout's own compiled ledger.
    """
    copies = [FIXTURES_DIR / REAL]
    live = REPO_ROOT / LEDGER
    if live.is_file():
        copies.append(live)
    for source in copies:
        tmp_git_repo.write(LEDGER, source.read_text(encoding="utf-8"))
        proc = _cli(tmp_git_repo, "validate")
        assert proc.returncode == 0, (source.name, proc.stdout + proc.stderr)
        assert "validate: ok" in proc.stdout, source.name


# -- review patches -------------------------------------------------------------------------


def test_apply_review_roundtrip_with_edges(tmp_git_repo, tmp_path):
    """review-template -> patch (description, inputs, edges) -> apply-review: clean + review."""
    path = _seed(tmp_git_repo)
    assert (
        _cli(
            tmp_git_repo, "mark", "--dirty", "--ids", "component_ledger,ledger_validate"
        ).returncode
        == 0
    )
    proc = _cli(tmp_git_repo, "review-template", "--json")
    assert proc.returncode == 0, proc.stderr
    template = json.loads(proc.stdout)
    assert [node["id"] for node in template["nodes"]] == ["component_ledger", "ledger_validate"]
    assert set(template["nodes"][0]) >= {
        "id",
        "dimension",
        "associated_file",
        "files",
        "description",
        "inputs",
        "outputs",
        "edges",
    }
    patch_keys = template["patch_schema"]["additionalProperties"]["properties"]
    assert set(patch_keys) == {"description", "inputs", "outputs", "files", "edges"}
    patch = {
        "component_ledger": {
            "description": "Single validated writer of the component ledger.\n",
            "inputs": [
                {"data_type": "ledger_yaml", "external": "filesystem"},
                {"data_type": "review_patch", "external": "audit_agent"},
            ],
            "edges": {"depends_on": ["sample_system"]},
        },
        "ledger_validate": {},
    }
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps(patch), encoding="utf-8")
    proc = _cli(tmp_git_repo, "apply-review", "--patch", str(patch_file), "--slice", "S01a")
    assert proc.returncode == 0, proc.stderr
    data = _read(tmp_git_repo)
    ledger = _node(data, "component_ledger")
    assert ledger["status"] == "clean"
    assert ledger["description"] == "Single validated writer of the component ledger."
    assert ledger["inputs"] == patch["component_ledger"]["inputs"]
    assert ledger["edges"] == {
        "depends_on": ["sample_system"],
        "implements": [],
        "member_of": ["orchestration_layer"],
    }
    review = ledger["review"]
    assert review["reviewed_by"] == "audit:S01a"
    assert review["summary"] == "patched description, inputs, edges.depends_on"
    assert review["reviewed_at"].endswith("+00:00")
    assert _runtime.parse_ts(review["reviewed_at"]).utcoffset() is not None
    untouched = _node(data, "ledger_validate")
    assert untouched["status"] == "clean"
    assert untouched["review"]["summary"] == "reviewed; nothing patched"
    assert _cli(tmp_git_repo, "validate", "--strict").returncode == 0
    for bad, code in (
        ({"ghost_node": {}}, 2),
        ({"component_ledger": {"status": "clean"}}, 2),
        ({"component_ledger": {"edges": {"implements": "component_ledger"}}}, 2),
        ({"component_ledger": {"edges": {"depends_on": ["ghost_node"]}}}, 5),
    ):
        before = path.read_bytes()
        patch_file.write_text(json.dumps(bad), encoding="utf-8")
        proc = _cli(tmp_git_repo, "apply-review", "--patch", str(patch_file), "--slice", "S01a")
        assert proc.returncode == code, (bad, proc.stderr)
        assert path.read_bytes() == before


# -- outbox ---------------------------------------------------------------------------------


def test_outbox_routes_writes_and_apply_outbox_replays(tmp_git_repo, tmp_path):
    """With HACF_OUTBOX set, writes become numbered entries (no lock); apply-outbox replays."""
    path = _seed(tmp_git_repo)
    before = path.read_bytes()
    outbox = tmp_path / "outbox"
    description = tmp_path / "description.txt"
    description.write_text("Command-line front end of the ledger.\n", encoding="utf-8")
    proc = _cli(
        tmp_git_repo,
        "ensure-node",
        "--id",
        "ledger_cli",
        "--dimension",
        "Module",
        "--parent",
        "orchestration_layer",
        "--files",
        "src/cli.py",
        "--description-file",
        str(description),
        HACF_OUTBOX=str(outbox),
    )
    assert proc.returncode == 0, proc.stderr
    args = ("set-edges", "--id", "component_ledger", "--add-depends-on", "ledger_cli")
    assert _cli(tmp_git_repo, *args, HACF_OUTBOX=str(outbox)).returncode == 0
    assert path.read_bytes() == before
    assert not (tmp_git_repo.path / ".git" / "hacf").exists()  # no shared lock taken
    assert sorted(os.listdir(outbox)) == ["000001-ensure-node.json", "000002-set-edges.json"]
    entry = json.loads((outbox / "000001-ensure-node.json").read_text(encoding="utf-8"))
    assert (entry["tool"], entry["op"]) == ("hyper_ledger", "ensure-node")
    assert entry["args"]["files"] == ["src/cli.py"]
    assert entry["args"]["description"] == "Command-line front end of the ledger."
    foreign = {"tool": "hyper_lore", "op": "note", "args": {}}
    (outbox / "000003-note.json").write_text(json.dumps(foreign), encoding="utf-8")
    proc = _cli(tmp_git_repo, "apply-outbox", str(outbox), HACF_OUTBOX=str(outbox))
    assert proc.returncode == 2 and path.read_bytes() == before
    proc = _cli(tmp_git_repo, "apply-outbox", str(outbox))
    assert proc.returncode == 0, proc.stderr
    assert "skipped 000003-note.json" in proc.stdout
    data = _read(tmp_git_repo)
    assert _node(data, "ledger_cli")["edges"]["member_of"] == ["orchestration_layer"]
    assert _node(data, "ledger_cli")["description"] == "Command-line front end of the ledger."
    assert "ledger_cli" in _node(data, "component_ledger")["edges"]["depends_on"]


def test_apply_outbox_validates_and_refuses_conflicts(tmp_git_repo, tmp_path):
    """Replay is validated and all-or-nothing; --base-file turns a changed node into a conflict."""
    path = _seed(tmp_git_repo)
    base = tmp_path / "base.yml"
    base.write_bytes(path.read_bytes())
    bad = tmp_path / "bad-outbox"
    args = ("set-edges", "--id", "component_ledger", "--add-depends-on", "ghost_node")
    assert _cli(tmp_git_repo, *args, HACF_OUTBOX=str(bad)).returncode == 0
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "apply-outbox", str(bad))
    assert proc.returncode == 5 and "[dangling_edge]" in proc.stderr
    assert path.read_bytes() == before
    outbox = tmp_path / "outbox"
    for args in (
        ("mark", "--needs-review", "--ids", "component_ledger"),
        ("set-edges", "--id", "ledger_validate", "--add-depends-on", "sample_system"),
    ):
        assert _cli(tmp_git_repo, *args, HACF_OUTBOX=str(outbox)).returncode == 0
    assert _cli(tmp_git_repo, "mark", "--dirty", "--ids", "component_ledger").returncode == 0
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "apply-outbox", str(outbox), "--base-file", str(base))
    assert proc.returncode == 5
    assert "conflict: node component_ledger" in proc.stderr
    assert "ledger_validate" not in proc.stderr
    assert path.read_bytes() == before
    proc = _cli(tmp_git_repo, "apply-outbox", str(outbox))
    assert proc.returncode == 0, proc.stderr
    data = _read(tmp_git_repo)
    assert _node(data, "component_ledger")["status"] == "needs_review"
    assert _node(data, "ledger_validate")["edges"]["depends_on"] == ["sample_system"]


# -- impact ---------------------------------------------------------------------------------


def test_impact_bfs_keeps_v059_semantics(tmp_git_repo):
    """Implements parents plus reverse depends_on; member_of is not traversed; sorted output."""
    _seed(tmp_git_repo, REAL)
    proc = _cli(tmp_git_repo, "impact", "--nodes", "hypergraph_updater")
    assert proc.returncode == 0, proc.stderr
    assert _lines(proc.stdout)["blast_radius"] == [
        "hyper_orchestrator",
        "hypergraph_updater",
        "semantic_graph_merger",
    ]
    proc = _cli(tmp_git_repo, "impact", "--files", ".agents/scripts/hyper_fix.py,README.md")
    lines = _lines(proc.stdout)
    assert lines["nodes"] == ["hyper_fix"]  # v1 nodes map by associated_file
    assert lines["blast_radius"] == ["hyper_daemon", "hyper_fix", "hyper_orchestrator"]
    assert lines["unmapped"] == ["README.md"]
    _seed(tmp_git_repo)
    lines = _lines(_cli(tmp_git_repo, "impact", "--nodes", "ledger_validate").stdout)
    assert lines["blast_radius"] == ["component_ledger", "ledger_validate"]
    assert _cli(tmp_git_repo, "impact", "--nodes", "ghost_node").returncode == 2


def test_impact_from_diff_reports_unmapped(tmp_git_repo):
    """--from-diff maps changed paths by files globs and prints unmapped, even when empty."""
    _seed(tmp_git_repo)
    tmp_git_repo.write("src/ledger.py", "x = 1\n")
    base = tmp_git_repo.commit("ledger and code")
    tmp_git_repo.write("src/ledger.py", "x = 2\n")
    tmp_git_repo.write("src/ledger/validate.py", "y = 1\n")  # untracked
    tmp_git_repo.write("HACF-install.sh", "#!/bin/sh\n")
    tmp_git_repo.write(".agentignore", "*.log\n")
    proc = _cli(tmp_git_repo, "impact", "--from-diff", "HEAD")
    assert proc.returncode == 0, proc.stderr
    lines = _lines(proc.stdout)
    assert lines["nodes"] == ["component_ledger", "ledger_validate"]
    assert lines["blast_radius"] == ["component_ledger", "ledger_validate"]
    assert lines["unmapped"] == [".agentignore", "HACF-install.sh"]
    head = tmp_git_repo.commit("more")
    result = hyper_ledger.impact_from_diff(tmp_git_repo.path / LEDGER, f"{base}..{head}")
    assert result["nodes"] == ["component_ledger", "ledger_validate"]
    assert result["unmapped"] == [".agentignore", "HACF-install.sh"]
    proc = _cli(tmp_git_repo, "impact", "--from-diff", "HEAD")
    assert "unmapped: []\n" in proc.stdout
    assert _cli(tmp_git_repo, "impact", "--from-diff", "--cached").returncode == 2


# -- dump and contract ----------------------------------------------------------------------


def test_folded_scalar_round_trip():
    """Long descriptions dump as `>-` and load back equal (check.sh runs both PyYAMLs)."""
    data = yaml.safe_load((FIXTURES_DIR / REAL).read_text(encoding="utf-8"))
    texts = [
        "Validated, locked writer of the component ledger. " * 12,
        "Parses key: value pairs, keeps a # comment marker, a 3:1 ratio and C# names. " * 4,
        "Execute→Audit→Oracle→Fix loop with é accents. " * 5,
        "First paragraph.\nSecond line after a break, long enough to need a fold here. " * 3,
        "It's \"quoted\" and 'single' quoted text ending in a colon: " * 3,
        "- starts like a list item but is prose and runs on long enough to fold the line",
    ]
    for node, text in zip(data["nodes"], texts):
        node["description"] = text.strip()
    dumped = hyper_ledger.dump_ledger(data)
    assert yaml.safe_load(dumped) == data
    assert hyper_ledger.dump_ledger(yaml.safe_load(dumped)) == dumped
    assert dumped.count("  description: >-\n") == len(data["nodes"])
    assert "Execute→Audit" in dumped


CONTRACT = {
    "init": ["path"],
    "show": [],
    "validate": ["--against-file", "--against-ref", "--json", "--strict"],
    "impact": ["--files", "--from-diff", "--nodes"],
    "ensure-node": [
        "--allow-shared",
        "--associated-file",
        "--depends-on",
        "--description-file",
        "--dimension",
        "--files",
        "--from-spec",
        "--id",
        "--member-of",
        "--parent",
        "--spec-refs",
    ],
    "set-edges": [
        "--add-depends-on",
        "--id",
        "--remove-depends-on",
        "--set-implements",
        "--set-member-of",
    ],
    "review-template": ["--json", "--status"],
    "apply-review": ["--patch", "--slice"],
    "mark": ["--clean", "--dirty", "--ids", "--needs-review"],
    "remove-node": ["--reason", "--slice", "id"],
    "apply-outbox": ["--base-file", "dir"],
    "migrate-v2": ["--dry-run"],
    "repair-text": ["--dry-run"],
}


def test_cli_contract_frozen():
    """The parser exposes exactly the frozen subcommands and flags (R18.12), unabbreviated."""
    parser = hyper_ledger.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    found = {
        name: sorted(
            option
            for action in command._actions
            for option in (action.option_strings or [action.dest])
            if option not in ("-h", "--help")
        )
        for name, command in sub.choices.items()
    }
    assert found == CONTRACT
    assert not parser.allow_abbrev and not any(c.allow_abbrev for c in sub.choices.values())
    assert "CLI contract (frozen for v0.6.x)" in hyper_ledger.__doc__
    for name in (*CONTRACT, "migrate-v2", "repair-text"):
        assert f"    {name}" in hyper_ledger.__doc__, name


def test_outside_git_is_a_usage_error(tmp_path, run_script):
    """A subcommand run outside any work tree exits 2 (only init takes a path)."""
    env = {"GIT_CEILING_DIRECTORIES": str(tmp_path.parent), "HACF_OUTBOX": ""}
    proc = run_script(SCRIPT, "validate", cwd=tmp_path, env=env)
    assert proc.returncode == 2 and "not inside a git work tree" in proc.stderr
    proc = run_script(SCRIPT, "init", str(tmp_path / "ledger.yml"), cwd=tmp_path, env=env)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / ".ledger.yml.lock").is_file()
