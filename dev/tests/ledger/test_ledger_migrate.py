"""Tests for hyper_ledger.py migrate-v2 and repair-text (S01b).

Every test works on copies of S00's real-ledger fixtures in a throwaway repository under
tmp_path (`tmp_git_repo`): `ledger_v1_real_broken.yml` does not parse until repair-text folds
three broken description scalars, and `ledger_v1_real_repaired.yml` is the v1 ledger
migrate-v2 converts (17 depends_on edges into the Layer node orchestration_layer, 81 endpoints
that name no node, one of them the camelCase label activeContext).
"""

from __future__ import annotations

import difflib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import hyper_ledger
import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, clean_env

LEDGER = "spec/compiled/architecture.yml"
SCRIPT = str(SCRIPTS_DIR / "hyper_ledger.py")
REAL = "ledger_v1_real_repaired.yml"
BROKEN = "ledger_v1_real_broken.yml"
SAMPLE = "ledger_v2_sample.yml"
LAYER = "orchestration_layer"
V059_UPDATER = "984ca46:.agents/scripts/hypergraph_updater.py"
DESCRIPTION_RE = re.compile(r"^( *)description: (\S.*)$")


def _cli(repo, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run hyper_ledger.py in the repository; HACF_OUTBOX is empty unless env sets it."""
    return repo.run([sys.executable, SCRIPT, *args], **{"HACF_OUTBOX": "", **env})


def _seed(repo, fixture: str = REAL) -> Path:
    """Copy a fixture to the repository's ledger path and return that path."""
    return repo.write(LEDGER, (FIXTURES_DIR / fixture).read_text(encoding="utf-8"))


def _seed_data(repo, data: dict) -> Path:
    return repo.write(LEDGER, yaml.safe_dump(data, sort_keys=False))


def _fixture(name: str = REAL) -> dict:
    return yaml.safe_load((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _read(repo) -> dict:
    return yaml.safe_load((repo.path / LEDGER).read_text(encoding="utf-8"))


def _node(data: dict, node_id: str) -> dict:
    return next(node for node in data["nodes"] if node["id"] == node_id)


def _member_of(data: dict) -> list[tuple[str, str]]:
    """Return every member_of edge as (node, target)."""
    return [
        (node["id"], target)
        for node in data["nodes"]
        for target in node["edges"].get("member_of", [])
    ]


def _externals(data: dict) -> list[str]:
    """Return the label of every external endpoint."""
    return [
        item["external"]
        for node in data["nodes"]
        for key in ("inputs", "outputs")
        for item in node[key]
        if "external" in item
    ]


# -- repair-text ----------------------------------------------------------------------------


def test_repair_text_real(tmp_git_repo):
    """The broken real ledger parses after repair-text, which changes only description lines."""
    path = _seed(tmp_git_repo, BROKEN)
    path.chmod(0o640)
    before = path.read_text(encoding="utf-8")
    proc = _cli(tmp_git_repo, "repair-text", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "would fold line(s) 311, 454, 517; nothing written" in proc.stdout
    assert path.read_text(encoding="utf-8") == before

    proc = _cli(tmp_git_repo, "repair-text")
    assert proc.returncode == 0, proc.stderr
    assert f"--- a/{LEDGER}\n+++ b/{LEDGER}\n" in proc.stdout
    assert "repair-text: folded line(s) 311, 454, 517; written" in proc.stdout
    after = path.read_text(encoding="utf-8")
    data = yaml.safe_load(after)
    assert len(data["nodes"]) == 19
    # Every change replaces one plain `description: <text>` line with `description: >-` and
    # the same text on the next line, two spaces deeper; the continuation lines are untouched.
    old, new = before.splitlines(), after.splitlines()
    changes = [op for op in difflib.SequenceMatcher(a=old, b=new).get_opcodes() if op[0] != "equal"]
    assert len(changes) == 3
    for _, i1, i2, j1, j2 in changes:
        expected = []
        for line in old[i1:i2]:
            match = DESCRIPTION_RE.match(line)
            assert match, line
            lead, text = match.groups()
            expected += [f"{lead}description: >-", f"{lead}  {text}"]
        assert new[j1:j2] == expected
    # The folded text is what C2's hand repair of the same file carries.
    repaired = {node["id"]: node["description"] for node in _fixture(REAL)["nodes"]}
    assert {node["id"]: node["description"] for node in data["nodes"]} == repaired
    # Same lock and atomic save as every write: mode kept, no temp file left, lock taken.
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert sorted(os.listdir(path.parent)) == ["architecture.yml"]
    assert hyper_ledger.lock_file_for(path).is_file()

    proc = _cli(tmp_git_repo, "repair-text")
    assert proc.returncode == 0 and proc.stdout == "repair-text: no change\n"
    assert path.read_text(encoding="utf-8") == after


def test_repair_text_stops_when_it_cannot_repair(tmp_git_repo):
    """A file that still fails after folding, or a fold that is unsafe, exits 4 unchanged."""
    text = (FIXTURES_DIR / BROKEN).read_text(encoding="utf-8")
    still_broken = text.replace("  status: clean\n", "  status: clean: yes\n", 1)
    path = tmp_git_repo.write(LEDGER, still_broken)
    proc = _cli(tmp_git_repo, "repair-text")
    assert proc.returncode == 4
    assert "still does not parse" in proc.stderr and "nothing written" in proc.stderr
    assert path.read_text(encoding="utf-8") == still_broken

    uneven = text.replace("\n    user approval. (A)", "\n      user approval. (A)", 1)
    assert uneven != text
    tmp_git_repo.write(LEDGER, uneven)
    proc = _cli(tmp_git_repo, "repair-text")
    assert proc.returncode == 4
    assert f"{LEDGER}:311: repair-text cannot fold this description safely" in proc.stderr
    assert path.read_text(encoding="utf-8") == uneven

    tmp_git_repo.write(LEDGER, still_broken.replace("description: ", "summary: "))
    proc = _cli(tmp_git_repo, "repair-text", "--dry-run")
    assert proc.returncode == 4 and "found no description to fold" in proc.stderr


def test_fold_touches_only_broken_plain_descriptions():
    """Quoted, block and clean plain scalars stay; line endings and other lines are kept."""
    text = (
        "nodes:\r\n"
        "- description: Lists key: value pairs\r\n"
        "  notes: >-\r\n"
        "    description: inside a block scalar: untouched\r\n"
        "  inputs: []\r\n"
        "- id: b\r\n"
        "  description: 'quoted: fine'\r\n"
        "- id: c\r\n"
        "  description: plain and clean\r\n"
        "- id: d\r\n"
        "  description: ends the file # with a comment"
    )
    folded, lines = hyper_ledger.fold_broken_descriptions(text, "ledger.yml")
    assert lines == [2, 11]
    assert folded == (
        "nodes:\r\n"
        "- description: >-\r\n"
        "    Lists key: value pairs\r\n"
        "  notes: >-\r\n"
        "    description: inside a block scalar: untouched\r\n"
        "  inputs: []\r\n"
        "- id: b\r\n"
        "  description: 'quoted: fine'\r\n"
        "- id: c\r\n"
        "  description: plain and clean\r\n"
        "- id: d\r\n"
        "  description: >-\n"
        "    ends the file # with a comment"
    )
    data = yaml.safe_load(folded)
    assert data["nodes"][0]["description"] == "Lists key: value pairs"
    assert data["nodes"][3]["description"] == "ends the file # with a comment"


# -- migrate-v2 -----------------------------------------------------------------------------


def test_migrate_real(tmp_git_repo):
    """17 member_of edges, 81 externals and the v2 schema; a second run is a byte no-op."""
    path = _seed(tmp_git_repo, REAL)
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "migrate-v2", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert f"--- a/{LEDGER}\n+++ b/{LEDGER}\n" in proc.stdout
    assert "migrate-v2 --dry-run: nothing written" in proc.stdout
    assert path.read_bytes() == before

    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0, proc.stderr
    for line in (
        "  schema: hacf.ledger/v2 (added)",
        "  member_of: 17 depends_on edge(s) to a Layer node converted; "
        "17 member_of edge(s) in total",
        "  external: 81 endpoint(s) converted (32 inputs, 49 outputs)",
        "  labels: 1 camelCase label(s) turned snake_case",
        "    context_clearing.outputs[0] activeContext -> active_context",
        "migrate-v2: written",
    ):
        assert f"{line}\n" in proc.stdout, line
    data = _read(tmp_git_repo)
    assert list(data) == ["schema", "nodes"] and data["schema"] == "hacf.ledger/v2"
    original = _fixture(REAL)
    members = [n["id"] for n in original["nodes"] if LAYER in n["edges"]["depends_on"]]
    assert _member_of(data) == [(node_id, LAYER) for node_id in members]
    assert len(members) == 17
    assert not any(LAYER in node["edges"]["depends_on"] for node in data["nodes"])
    externals = _externals(data)
    assert len(externals) == 81 and "active_context" in externals
    ids = {node["id"] for node in data["nodes"]}
    for node in data["nodes"]:
        assert set(node["edges"]) == {"depends_on", "implements", "member_of"}
        for key, id_key in (("inputs", "source_id"), ("outputs", "target_id")):
            assert all(item[id_key] in ids for item in node[key] if id_key in item)
    # No source file exists in this repository, so no node is seeded with files.
    assert not any("files" in node for node in data["nodes"])
    proc = _cli(tmp_git_repo, "validate", "--strict")
    assert proc.returncode == 0 and proc.stdout == "validate --strict: ok\n", proc.stdout
    proc = _cli(tmp_git_repo, "impact", "--nodes", LAYER)
    assert f'blast_radius: ["{LAYER}"]\n' in proc.stdout

    migrated = path.read_bytes()
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0 and proc.stdout == "migrate-v2: no change\n"
    assert path.read_bytes() == migrated
    proc = _cli(tmp_git_repo, "migrate-v2", "--dry-run")
    assert proc.returncode == 0 and proc.stdout == "migrate-v2: no change\n"


def test_migrate_v2_sample_is_a_no_op(tmp_git_repo):
    """An already normalized v2 ledger (with tombstones) gives no change, byte-identical."""
    path = _seed(tmp_git_repo, SAMPLE)
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0 and proc.stdout == "migrate-v2: no change\n"
    assert path.read_bytes() == before


def test_v059_updater_still_runs_on_v2(tmp_git_repo, tmp_path):
    """The v0.5.9 updater (984ca46) exits 0 on the migrated ledger and keeps its v2 keys."""
    _seed(tmp_git_repo, REAL)
    assert _cli(tmp_git_repo, "migrate-v2").returncode == 0
    show = subprocess.run(
        ["git", "show", V059_UPDATER],
        cwd=REPO_ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    updater = tmp_path / "hypergraph_updater_v059.py"
    updater.write_text(show.stdout, encoding="utf-8")
    ledger = tmp_path / "migrated.yml"
    ledger.write_bytes((tmp_git_repo.path / LEDGER).read_bytes())
    proc = subprocess.run(
        ["python3", str(updater), str(ledger), LAYER],
        cwd=tmp_path,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # member_of is not traversed, so the 17 former dependents are no longer in the radius.
    assert f"Total nodes needing review (Blast Radius): ['{LAYER}']" in proc.stdout
    data = yaml.safe_load(ledger.read_text(encoding="utf-8"))
    assert data["schema"] == "hacf.ledger/v2"
    assert len(_member_of(data)) == 17 and len(_externals(data)) == 81
    assert _node(data, LAYER)["status"] == "dirty"


def test_migrate_mixed_ledger(tmp_git_repo):
    """v2 fields written by ensure-node before migration: 18 member_of edges, no duplicates."""
    path = _seed(tmp_git_repo, REAL)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tombstones = [{"id": "old_component", "reason": "folded in", "slice": "S00", "commit": None}]
    data["removed_nodes"] = tombstones
    _seed_data(tmp_git_repo, data)
    tmp_git_repo.write(".agents/scripts/hyper_ledger.py", "# ledger\n")
    for args in (
        (
            "ensure-node",
            "--id",
            "component_ledger",
            "--dimension",
            "Module",
            "--parent",
            LAYER,
            "--associated-file",
            ".agents/scripts/hyper_ledger.py",
            "--files",
            ".agents/scripts/hyper_ledger.py",
        ),
        ("ensure-node", "--id", "hypergraph_updater", "--member-of", LAYER),
    ):
        proc = _cli(tmp_git_repo, *args)
        assert proc.returncode == 0, proc.stderr
    mixed = _read(tmp_git_repo)
    assert _node(mixed, "hypergraph_updater")["edges"]["depends_on"] == [LAYER]
    assert _node(mixed, "hypergraph_updater")["edges"]["member_of"] == [LAYER]
    assert "mixed v1/v2 ledger" in _cli(tmp_git_repo, "validate").stdout

    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0, proc.stderr
    assert (
        "  member_of: 17 depends_on edge(s) to a Layer node converted; "
        "18 member_of edge(s) in total\n"
    ) in proc.stdout
    data = _read(tmp_git_repo)
    edges = _member_of(data)
    assert len(edges) == 18 and len(set(edges)) == 18
    assert {target for _, target in edges} == {LAYER}
    assert _node(data, "hypergraph_updater")["edges"] == {
        "depends_on": [],
        "implements": [],
        "member_of": [LAYER],
    }
    assert _node(data, "component_ledger")["files"] == [".agents/scripts/hyper_ledger.py"]
    assert data["removed_nodes"] == tombstones
    proc = _cli(tmp_git_repo, "validate")
    assert proc.returncode == 0 and proc.stdout == "validate: ok\n", proc.stdout

    migrated = path.read_bytes()
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0 and proc.stdout == "migrate-v2: no change\n"
    assert path.read_bytes() == migrated


def test_migrate_seeds_files_only_from_regular_files(tmp_git_repo):
    """Seeded files come from a Module or Atomic node's regular associated_file, after it."""
    data = _fixture(REAL)
    _node(data, LAYER)["associated_file"] = "README.md"  # a regular file, but a Layer node
    _seed_data(tmp_git_repo, data)
    tmp_git_repo.write(".agents/scripts/hypergraph_updater.py", "# updater\n")
    tmp_git_repo.write(".agents/scripts/hyper_update_core.py", "# core\n")
    (tmp_git_repo.path / ".agents/skills/hyper-clear/SKILL.md").mkdir(parents=True)
    (tmp_git_repo.path / "CLAUDE.md").symlink_to("README.md")
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0, proc.stderr
    assert (
        "  files: 2 node(s) seeded from associated_file: hypergraph_updater, "
        "hyper_update_core_logic\n"
    ) in proc.stdout
    data = _read(tmp_git_repo)
    seeded = {node["id"]: node["files"] for node in data["nodes"] if "files" in node}
    assert seeded == {
        "hypergraph_updater": [".agents/scripts/hypergraph_updater.py"],
        "hyper_update_core_logic": [".agents/scripts/hyper_update_core.py"],
    }
    keys = list(_node(data, "hypergraph_updater"))
    assert keys[keys.index("associated_file") + 1] == "files"


def test_migrate_renames_provenance(tmp_git_repo):
    """_provenance becomes provenance in place; two different records stop the run."""
    data = _fixture(REAL)
    record = {"last_slice": "S00", "last_commit": None}
    node = _node(data, "hyper_fix")
    items = list(node.items())
    node.clear()
    node.update([*items[:3], ("_provenance", record), *items[3:]])
    _seed_data(tmp_git_repo, data)
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 0, proc.stderr
    assert "  provenance: 1 _provenance key(s) renamed: hyper_fix\n" in proc.stdout
    migrated = _node(_read(tmp_git_repo), "hyper_fix")
    assert list(migrated)[3] == "provenance" and migrated["provenance"] == record
    assert "_provenance" not in migrated

    node["provenance"] = {"last_slice": "S01a"}
    path = _seed_data(tmp_git_repo, data)
    before = path.read_bytes()
    proc = _cli(tmp_git_repo, "migrate-v2")
    assert proc.returncode == 5
    assert "node hyper_fix: has both _provenance and provenance, and they differ" in proc.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize("label", ["File System", "user-interface", "9lives", "Has.Dot"])
def test_migrate_refuses_a_bad_label(tmp_git_repo, label):
    """A label that is not ^[a-z][a-z0-9_]{0,63}$ even in snake_case exits 5, unchanged."""
    data = _fixture(REAL)
    _node(data, "hyper_update")["inputs"][0]["source_id"] = label
    path = _seed_data(tmp_git_repo, data)
    before = path.read_bytes()
    for args in (("migrate-v2", "--dry-run"), ("migrate-v2",)):
        proc = _cli(tmp_git_repo, *args)
        assert proc.returncode == 5, proc.stdout
        assert f"node hyper_update: inputs[0].source_id {label!r} names no node" in proc.stderr
        assert path.read_bytes() == before


def test_whole_file_rewrites_refuse_the_outbox(tmp_git_repo, tmp_path):
    """With HACF_OUTBOX set, migrate-v2 and repair-text exit 2; their dry runs still work."""
    path = _seed(tmp_git_repo, REAL)
    before = path.read_bytes()
    outbox = str(tmp_path / "outbox")
    for command in ("migrate-v2", "repair-text"):
        proc = _cli(tmp_git_repo, command, HACF_OUTBOX=outbox)
        assert proc.returncode == 2 and f"unset {hyper_ledger.OUTBOX_ENV}" in proc.stderr
        assert _cli(tmp_git_repo, command, "--dry-run", HACF_OUTBOX=outbox).returncode == 0
    assert path.read_bytes() == before
    assert not (tmp_path / "outbox").exists()
