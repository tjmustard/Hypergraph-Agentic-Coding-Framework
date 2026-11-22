"""Deterministic tests for hyper_scope.py, the scope manager (S03b), and archive_specs.py.

Every test works in a temp git repository (conftest's tmp_git_repo) or a temp directory; the
scripts run as subprocesses from the repository's .agents/scripts, except the compare-and-swap
tests, which call hyper_scope.main() in-process to inject a concurrent writer. Nothing runs rig,
and archive_specs.py runs from a copy inside the temp directory, because it archives the spec/
beside its own .agents/. The 984ca46 archive_specs.py is the golden fixture
fixtures/archive_specs_984ca46.py.txt, pinned by its git blob sha.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import hyper_openrig
import hyper_scope
import pytest
import yaml
from conftest import SCRIPTS_DIR, GitRepo, clean_env

SCRIPT = SCRIPTS_DIR / "hyper_scope.py"
SPEC_SCRIPT = SCRIPTS_DIR / "hyper_spec.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
OLD_ARCHIVE = FIXTURES / "archive_specs_984ca46.py.txt"
OLD_ARCHIVE_BLOB = (
    "21a52ac0a3ea74f29a7c38803cbc99f8fb603f48"  # 984ca46:.agents/scripts/archive_specs.py
)
MISSION = "missions/release-0.6.0"
ITEMS = ("SPEC.md", "project.yaml", "workspace.yaml", "missions/")
DATE = r"\d{4}-\d{2}-\d{2}"

SLICE_BODY = """\
# MiniPRD: {title}

## Intent

{title} does one thing.

## 1. The Confidence Mandate

Confidence 9/10.

## Mini-requirements

1. US-001: {title} works.

## 3. Implementation Plan (Task List)

- [ ] Task 1: write it.

## 4. The Negative Space (Constraints)

- **DO NOT** touch other modules.

## Proof contract

- [ ] test_{title} passes.

## Source material

- (none)

## Intent visual

N/A

## Status

- compiled

## Dependencies

- (none)
"""


@pytest.fixture(autouse=True)
def _no_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an outer git's GIT_* variables and an engine's HACF_OUTBOX away from the tests."""
    for key in list(os.environ):
        if key.startswith("GIT_") or key == "HACF_OUTBOX":
            monkeypatch.delenv(key)


def run(repo: GitRepo, *args: str, script: Path = SCRIPT, **env: str):
    """Run a script (hyper_scope.py by default) in the repository under this interpreter."""
    full = {key: value for key, value in repo.env.items() if key != "HACF_OUTBOX"}
    full.update(env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=repo.path,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def ok(repo: GitRepo, *args: str) -> subprocess.CompletedProcess:
    """Run hyper_scope.py and assert it exits 0."""
    proc = run(repo, *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def snapshot(root: Path) -> dict[str, tuple]:
    """Return every path under root except .git: its kind and bytes (or a link's target)."""
    found: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", path.read_bytes())
    return found


def front(path: Path) -> dict:
    """Return a Markdown file's frontmatter mapping."""
    return hyper_openrig.read_frontmatter(path)


def mission_repo(repo: GitRepo) -> Path:
    """Run project init and create missions/release-0.6.0; return the mission dir."""
    ok(repo, "project", "init")
    ok(repo, "mission", "create", "--name", "release-0.6.0", "--intent", "Ship HACF v0.6.0.")
    return repo.path / MISSION


def make_slice(repo: GitRepo, name: str, *extra: str, mission: str = MISSION) -> Path:
    """Create a lint-clean slice through `slice create`; return its dir."""
    title = name.split("-", 1)[1]
    body = repo.scratch / f"{name}.md"
    body.write_text(SLICE_BODY.format(title=title), encoding="utf-8")
    ok(
        repo,
        "slice",
        "create",
        "--mission",
        mission,
        "--name",
        name,
        "--intent",
        f"The {title} slice.",
        "--body-file",
        str(body),
        *extra,
    )
    return repo.path / mission / "slices" / name


def lint_all(repo: GitRepo) -> subprocess.CompletedProcess:
    """Run `hyper_spec.py lint --all` in the repository."""
    return run(repo, "lint", "--all", script=SPEC_SCRIPT)


# ---------------------------------------------------------------------------
# project init: the root collision matrix
# ---------------------------------------------------------------------------

HACF_SPEC = """\
---
intent: 'A project.'
hacf_schema: 1
hacf_type: SystemOfRecord
hacf_updated: '2026-10-21'
---

# Project - x

## Intent

A project.

## Product Context

<!-- BEGIN HACF GENERATED: missions-index -->
- (none)
<!-- END HACF GENERATED: missions-index -->
"""
HACF_MISSION_SPEC = "---\nid: OPR.1.0\nintent: 'One.'\nhacf_type: SuperPRD\n---\n\n# M\n"
OPENRIG_MISSION_SPEC = "---\nid: OPR.1.0\nintent: 'One.'\n---\n\n# M\n"
ROOT_TEXTS = {
    "SPEC.md": {
        "hacf": HACF_SPEC,
        "openrig": "---\nintent: 'An OpenRig project.'\nstage: wip\n---\n\n# OpenRig project\n",
        "foreign": "# My own spec\n\nNo frontmatter here.\n",
        "broken": "---\nintent: [unclosed\n---\n\n# Broken\n",
    },
    "project.yaml": {
        "hacf": hyper_openrig.render_project_yaml(),
        "openrig": "schema: openrig.project/v0alpha1\nkind: project\nmissions:\n  root: missions\n",
        "foreign": "name: something else\n",
        "broken": "schema: [unclosed\n",
    },
    "workspace.yaml": {
        "hacf": hyper_openrig.render_workspace_yaml(),
        "openrig": "schema: openrig.workspace/v0alpha1\nprojects:\n  - id: mine\n    root: .\n",
        "foreign": "schema: openrig.project/v0alpha1\n",
        "broken": "projects: [unclosed\n",
    },
}
STATES = {
    "missing": "missing",
    "hacf": "hacf",
    "openrig": "openrig",
    "foreign": "foreign",
    "symlink": "foreign",
    "broken": "foreign",
}


def _write_missions(root: Path, state: str) -> None:
    """Build missions/ in a state (hacf, openrig, foreign or broken) under root."""
    missions = root / "missions"
    missions.mkdir(parents=True)
    if state == "foreign":
        (missions / "notes.txt").write_text("not a mission\n", encoding="utf-8")
        return
    mission = missions / "release-1.0"
    mission.mkdir()
    manifest = hyper_openrig.render_mission_yaml()
    if state == "broken":
        manifest = manifest.replace("slices: []", "slices:\n    - ref: slices/gone/slice.yaml")
    (mission / "mission.yaml").write_text(manifest, encoding="utf-8")
    text = HACF_MISSION_SPEC if state in ("hacf", "broken") else OPENRIG_MISSION_SPEC
    (mission / "SPEC.md").write_text(text, encoding="utf-8")


def build_root_item(repo: GitRepo, item: str, state: str) -> None:
    """Put one root item into a state; a symlink points at an HACF-valid copy outside root."""
    if state == "missing":
        return
    root = repo.path
    if state == "symlink":
        elsewhere = repo.scratch / "elsewhere"
        elsewhere.mkdir()
        if item == "missions/":
            _write_missions(elsewhere, "hacf")
            os.symlink(elsewhere / "missions", root / "missions")
        else:
            (elsewhere / item).write_text(ROOT_TEXTS[item]["hacf"], encoding="utf-8")
            os.symlink(elsewhere / item, root / item)
        return
    if item == "missions/":
        _write_missions(root, state)
    else:
        (root / item).write_text(ROOT_TEXTS[item][state], encoding="utf-8")


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize("item", ITEMS)
def test_project_init_collision_matrix(tmp_git_repo: GitRepo, item: str, state: str) -> None:
    """Each root item in each state classifies as expected; foreign stops init untouched."""
    repo = tmp_git_repo
    build_root_item(repo, item, state)
    expected = dict.fromkeys(ITEMS, "missing")
    expected[item] = STATES[state]
    is_foreign = expected[item] == "foreign"
    before = snapshot(repo.path)

    proc = run(repo, "project", "init", "--check-only", "--json")
    report = json.loads(proc.stdout)
    assert {entry["item"]: entry["class"] for entry in report["items"]} == expected
    assert report["foreign"] == ([item] if is_foreign else [])
    assert proc.returncode == (5 if is_foreign else 0), proc.stderr
    assert snapshot(repo.path) == before  # --check-only never writes

    proc = run(repo, "project", "init", "--json")
    if is_foreign:
        assert proc.returncode == 5
        assert "nothing was written" in proc.stderr
        assert snapshot(repo.path) == before
        return
    assert proc.returncode == 0, proc.stderr
    after = snapshot(repo.path)
    assert {rel: after[rel] for rel in before} == before  # existing items are byte-identical
    created = {rel.split("/")[0] for rel in set(after) - set(before)}
    assert created == {i.rstrip("/") for i in ITEMS if expected[i] == "missing"}
    classes = {entry["item"]: entry["class"] for entry in json.loads(proc.stdout)["items"]}
    assert classes == {i: ("openrig" if expected[i] == "openrig" else "hacf") for i in ITEMS}

    again = run(repo, "project", "init")
    assert again.returncode == 0, again.stderr
    assert "nothing written" in again.stdout
    assert snapshot(repo.path) == after


def test_project_init_writes_hacf_root(tmp_git_repo: GitRepo) -> None:
    """A fresh init writes the pinned manifests, an empty missions/ and a lint-clean SPEC.md."""
    repo = tmp_git_repo
    ok(repo, "project", "init", "--intent", "A tool that does things.")
    root = repo.path
    assert (root / "project.yaml").read_text() == hyper_openrig.render_project_yaml()
    assert (root / "workspace.yaml").read_text() == hyper_openrig.render_workspace_yaml()
    assert (root / "missions").is_dir() and not list((root / "missions").iterdir())
    spec = front(root / "SPEC.md")
    assert spec["intent"] == "A tool that does things."
    assert spec["hacf_type"] == "SystemOfRecord" and spec["hacf_schema"] == 1
    assert re.fullmatch(DATE, spec["hacf_updated"])
    text = (root / "SPEC.md").read_text()
    assert "<!-- HACF template" not in text  # the derivation notice line is dropped
    assert "- (none)\n<!-- END HACF GENERATED: missions-index -->" in text
    assert lint_all(repo).returncode == 0


# ---------------------------------------------------------------------------
# project update and the R26 region grammar
# ---------------------------------------------------------------------------


def test_project_update_rewrites_owned_keys_and_body(tmp_git_repo: GitRepo) -> None:
    """Update splices intent and hacf_updated, replaces the body and keeps the missions index."""
    repo = tmp_git_repo
    mission_repo(repo)
    body = repo.scratch / "body.md"
    body.write_text("# Project - Renamed\n\n## Intent\n\nNew.\n\n## Product Context\n\nCtx.\n")
    ok(repo, "project", "update", "--intent", "A sharper intent.", "--body-file", str(body))
    text = (repo.path / "SPEC.md").read_text()
    assert front(repo.path / "SPEC.md")["intent"] == "A sharper intent."
    assert "# Project - Renamed" in text and "## 1. Introduction & Goals" not in text
    assert text.endswith(
        "<!-- BEGIN HACF GENERATED: missions-index -->\n- release-0.6.0 (OPR.0.6.0)\n"
        "<!-- END HACF GENERATED: missions-index -->\n"
    )
    assert lint_all(repo).returncode == 0

    (repo.path / "SPEC.md").write_text(ROOT_TEXTS["SPEC.md"]["openrig"])
    before = snapshot(repo.path)
    proc = run(repo, "project", "update", "--intent", "Mine now.")
    assert proc.returncode == 5 and "openrig" in proc.stderr
    assert snapshot(repo.path) == before


GRAMMAR_SPEC = (
    "﻿---\r\nintent: 'Grammar.'\r\nhacf_schema: 1\r\nhacf_type: SystemOfRecord\r\n"
    "hacf_updated: '2026-10-21'\r\n---\r\n\r\n# Project - g\r\n\r\n## Intent\r\n\r\nG.\r\n\r\n"
    "## Product Context\r\n\r\n"
    "  <!-- BEGIN HACF GENERATED: missions-index -->\r\n"
    "```\r\n<!-- BEGIN HACF GENERATED: missions-index -->\r\n```\r\n"
    "x<!-- END HACF GENERATED: missions-index -->\r\n"
    "<!-- begin hacf generated: missions-index -->\r\n"
    "<!-- BEGIN OpenRig MANAGED BLOCK: role -->\r\nrig text\r\n"
    "<!-- END OpenRig MANAGED BLOCK: role -->\r\n"
    "<!-- BEGIN HACF GENERATED: missions-index -->\r\n- stale\r\n"
    "<!-- END HACF GENERATED: missions-index -->\r\ntail line"
)


def test_region_grammar_preserves_bytes_outside_the_region(tmp_git_repo: GitRepo) -> None:
    """Only exact column-0 markers outside fences count; BOM, CRLF and no final newline stay."""
    repo = tmp_git_repo
    (repo.path / "SPEC.md").write_bytes(GRAMMAR_SPEC.encode("utf-8"))
    ok(repo, "project", "init")  # SPEC.md is HACF's: adopted untouched
    assert (repo.path / "SPEC.md").read_bytes() == GRAMMAR_SPEC.encode("utf-8")
    ok(repo, "mission", "create", "--name", "release-0.6.0", "--intent", "Ship it.")
    new = (repo.path / "SPEC.md").read_bytes().decode("utf-8")
    expected = GRAMMAR_SPEC.replace("- stale\r\n", "- release-0.6.0 (OPR.0.6.0)\r\n")
    assert new == expected


@pytest.mark.parametrize(
    "tail",
    [
        "<!-- BEGIN HACF GENERATED: missions-index -->\n"
        "<!-- END HACF GENERATED: missions-index -->\n"
        "<!-- BEGIN HACF GENERATED: missions-index -->\n"
        "<!-- END HACF GENERATED: missions-index -->\n",
        "<!-- BEGIN HACF MANAGED BLOCK: notes -->\n"
        "<!-- BEGIN HACF GENERATED: missions-index -->\n"
        "<!-- END HACF GENERATED: missions-index -->\n"
        "<!-- END HACF MANAGED BLOCK: notes -->\n",
        "<!-- BEGIN HACF GENERATED: missions-index -->\n- x\n",
        "<!-- END HACF GENERATED: missions-index -->\n",
    ],
    ids=["duplicate", "nested", "no-end", "no-begin"],
)
def test_region_grammar_bad_markers_write_nothing(tmp_git_repo: GitRepo, tail: str) -> None:
    """Duplicate, nested or unbalanced HACF markers stop mission create with exit 5."""
    repo = tmp_git_repo
    head = HACF_SPEC.split("<!-- BEGIN", 1)[0]
    (repo.path / "SPEC.md").write_text(head + tail, encoding="utf-8")
    ok(repo, "project", "init")
    before = snapshot(repo.path)
    proc = run(repo, "mission", "create", "--name", "release-0.6.0", "--intent", "Ship it.")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert snapshot(repo.path) == before


# ---------------------------------------------------------------------------
# mission create
# ---------------------------------------------------------------------------


def test_mission_create_tree_idempotent(tmp_git_repo: GitRepo) -> None:
    """release-0.6.0 -> OPR.0.6.0 with four files; a rerun is a no-op; other content exits 5."""
    repo = tmp_git_repo
    mission = mission_repo(repo)
    assert sorted(p.name for p in mission.iterdir()) == [
        "NOTES.md",
        "PROGRESS.md",
        "SPEC.md",
        "mission.yaml",
    ]
    spec = front(mission / "SPEC.md")
    assert spec["id"] == "OPR.0.6.0" and spec["mission"] == "release-0.6.0"
    assert spec["hacf_type"] == "SuperPRD" and spec["hacf_status"] == "compiled"
    assert spec["intent"] == "Ship HACF v0.6.0." and spec["hacf_source"] is None
    assert re.fullmatch(DATE, spec["created"])
    manifest = (mission / "mission.yaml").read_text()
    assert manifest == hyper_openrig.render_mission_yaml()
    assert yaml.safe_load(manifest)["metadata"]["status"] == "planned"
    progress = (mission / "PROGRESS.md").read_text()
    assert progress.startswith("# Progress - release-0.6.0\n")
    assert "## Acceptance\n\n- [ ] Scope complete\n" in progress
    assert "| id | slice | hacf_status | depends_on | last event |" in progress
    assert front(mission / "NOTES.md") == {
        "mission": "OPR.0.6.0",
        "name": "release-0.6.0",
        "created": spec["created"],
    }
    assert "- release-0.6.0 (OPR.0.6.0)\n" in (repo.path / "SPEC.md").read_text()
    assert lint_all(repo).returncode == 0

    before = snapshot(repo.path)
    again = run(
        repo, "mission", "create", "--name", "release-0.6.0", "--intent", "Ship HACF v0.6.0."
    )
    assert again.returncode == 0 and "same content" in again.stdout
    assert snapshot(repo.path) == before
    other = run(repo, "mission", "create", "--name", "release-0.6.0", "--intent", "Other.")
    assert other.returncode == 5 and "intent" in other.stderr
    assert snapshot(repo.path) == before


def test_mission_create_escape_band_and_draft(tmp_git_repo: GitRepo) -> None:
    """Non-release names take OPR.99.0.<n>; --from-draft records its path, intent, lore_refs."""
    repo = tmp_git_repo
    mission_repo(repo)
    ok(repo, "mission", "create", "--name", "hyper-update", "--intent", "Update.")
    ok(repo, "mission", "create", "--name", "legacy-unassigned", "--intent", "Leftovers.")
    assert front(repo.path / "missions/hyper-update/SPEC.md")["id"] == "OPR.99.0.1"
    assert front(repo.path / "missions/legacy-unassigned/SPEC.md")["id"] == "OPR.99.0.2"
    before = snapshot(repo.path)
    ok(repo, "mission", "create", "--name", "hyper-update", "--intent", "Update.")
    assert snapshot(repo.path) == before

    draft = repo.write(
        "spec/active/Draft_PRD.md",
        "---\nintent: 'Release 0.7.0.'\nhacf_schema: 1\nhacf_type: SuperPRD\n"
        "hacf_status: draft\nlore_refs: [ADR-0001]\n---\n\n# Draft\n",
    )
    body = repo.scratch / "superprd.md"
    body.write_text("# SuperPRD: v0.7.0\n\n## Intent\n\nI.\n\n## Slices\n\n## Status\n\ncompiled\n")
    ok(
        repo,
        "mission",
        "create",
        "--name",
        "release-0.7.0",
        "--from-draft",
        str(draft),
        "--body-file",
        str(body),
    )
    spec_path = repo.path / "missions/release-0.7.0/SPEC.md"
    spec = front(spec_path)
    assert spec["hacf_source"] == {"draft": "spec/active/Draft_PRD.md"}
    assert spec["intent"] == "Release 0.7.0." and spec["lore_refs"] == ["ADR-0001"]
    assert spec["hacf_status"] == "compiled"
    assert "## Slices\n\n<!-- BEGIN HACF GENERATED: slices -->\n" in spec_path.read_text()
    assert lint_all(repo).returncode == 0


# ---------------------------------------------------------------------------
# slice create, close and move
# ---------------------------------------------------------------------------


def test_slice_create_files_and_refusals(tmp_git_repo: GitRepo) -> None:
    """Slice create writes four files and appends orders 10, 20; bad input writes nothing."""
    repo = tmp_git_repo
    mission = mission_repo(repo)
    oracle = repo.scratch / "oracle.json"
    oracle.write_text(json.dumps([["python3", "-m", "pytest", "tests/alpha", "-q"]]))
    alpha = make_slice(
        repo,
        "01-alpha",
        "--node-id",
        "alpha_module",
        "--parent",
        "orchestration_layer",
        "--touches",
        "src/alpha.py,tests/alpha/**",
        "--oracle-json",
        str(oracle),
        "--legacy-source",
        "spec/compiled/MiniPRD_01_Alpha.md",
        "--legacy-order",
        "1",
    )
    beta = make_slice(repo, "02-beta", "--depends-on", "OPR.0.6.0.1")
    assert sorted(p.name for p in alpha.iterdir()) == [
        "PROGRESS.md",
        "PROOF.md",
        "SPEC.md",
        "slice.yaml",
    ]
    spec = front(alpha / "SPEC.md")
    assert spec["id"] == "OPR.0.6.0.1" and spec["slice"] == "01-alpha"
    assert spec["status"] == "active" and spec["hacf_status"] == "compiled"
    assert spec["hacf_touches"] == ["src/alpha.py", "tests/alpha/**"]
    assert spec["hacf_oracle"] == [["python3", "-m", "pytest", "tests/alpha", "-q"]]
    assert spec["hacf_legacy_source"] == "spec/compiled/MiniPRD_01_Alpha.md"
    assert spec["hacf_legacy_order"] == 1
    assert front(beta / "SPEC.md")["depends_on"] == ["OPR.0.6.0.1"]
    assert (alpha / "slice.yaml").read_text() == hyper_openrig.render_slice_yaml()
    assert (alpha / "PROOF.md").read_text().startswith("# PROOF - OPR.0.6.0.1 alpha\n")
    progress = (alpha / "PROGRESS.md").read_text()
    assert re.search(
        r"\n- \S+\+00:00 compiled - created by hyper_scope\.py slice create\n", progress
    )
    members = hyper_openrig.read_mission_members(mission)
    assert [(m.ref, m.order) for m in members] == [
        ("slices/01-alpha/slice.yaml", 10),
        ("slices/02-beta/slice.yaml", 20),
    ]
    assert (
        "| OPR.0.6.0.2 | 02-beta | compiled | OPR.0.6.0.1 |"
        in (mission / "PROGRESS.md").read_text()
    )
    assert "| OPR.0.6.0.2 | slices/02-beta | The beta slice. |" in (mission / "SPEC.md").read_text()
    assert lint_all(repo).returncode == 0

    before = snapshot(repo.path)
    base = ["slice", "create", "--mission", MISSION, "--intent", "X."]
    cases = [
        (["--name", "03-gamma", "--touches", "/abs/path"], 2),
        (["--name", "03-gamma", "--depends-on", "OPR.0.6.0.9"], 4),
        (["--name", "00-zero"], 2),
        (["--name", "gamma"], 2),
        (["--name", "01-alpha"], 5),
        (["--name", "01-other"], 5),  # the id OPR.0.6.0.1 is taken
    ]
    for extra, code in cases:
        proc = run(repo, *base, *extra)
        assert proc.returncode == code, (extra, proc.stdout + proc.stderr)
    proc = run(repo, *base, "--name", "03-gamma", HACF_OUTBOX=str(repo.scratch / "outbox"))
    assert proc.returncode == 2 and "HACF_OUTBOX" in proc.stderr
    assert snapshot(repo.path) == before


def test_slice_close_superseded(tmp_git_repo: GitRepo) -> None:
    """A live dependent refuses the close; with a rewrite the slice moves to closed/."""
    repo = tmp_git_repo
    mission = mission_repo(repo)
    alpha = make_slice(repo, "01-alpha")
    beta = make_slice(repo, "02-beta", "--depends-on", "OPR.0.6.0.1")
    make_slice(repo, "03-gamma")
    before = snapshot(repo.path)
    proc = run(repo, "slice", "close", f"{MISSION}/slices/01-alpha", "--reason", "superseded")
    assert proc.returncode == 5 and "02-beta" in proc.stderr
    assert snapshot(repo.path) == before

    ok(
        repo,
        "slice",
        "close",
        "OPR.0.6.0.1",
        "--reason",
        "superseded",
        "--rewrite-dependents-to",
        "OPR.0.6.0.3",
    )
    closed = mission / "closed" / "01-alpha"
    assert closed.is_dir() and not alpha.exists()
    spec = front(closed / "SPEC.md")
    assert spec["status"] == "closed-superseded" and spec["hacf_status"] == "superseded"
    assert re.fullmatch(DATE, spec["closed-on"])
    refs = [m.ref for m in hyper_openrig.read_mission_members(mission)]
    assert refs == ["slices/02-beta/slice.yaml", "slices/03-gamma/slice.yaml"]
    assert front(beta / "SPEC.md")["depends_on"] == ["OPR.0.6.0.3"]
    assert (
        "| OPR.0.6.0.1 | closed/01-alpha | The alpha slice. |" in (mission / "SPEC.md").read_text()
    )
    assert "| OPR.0.6.0.1 | 01-alpha | superseded |" in (mission / "PROGRESS.md").read_text()
    assert (
        "superseded - closed by hyper_scope.py slice close" in (closed / "PROGRESS.md").read_text()
    )
    assert lint_all(repo).returncode == 0

    # A live slice in another mission that depends on gamma also refuses the close.
    ok(repo, "mission", "create", "--name", "release-0.7.0", "--intent", "Next.")
    make_slice(repo, "01-next", "--depends-on", "OPR.0.6.0.3", mission="missions/release-0.7.0")
    before = snapshot(repo.path)
    proc = run(repo, "slice", "close", f"{MISSION}/slices/03-gamma", "--reason", "superseded")
    assert proc.returncode == 5 and "release-0.7.0/slices/01-next" in proc.stderr
    assert snapshot(repo.path) == before
    proc = run(repo, *argv_close(MISSION, "03-gamma"), "--rewrite-dependents-to", "OPR.0.6.0.2")
    assert proc.returncode == 2 and "depend on itself" in proc.stderr  # beta depends on gamma
    assert snapshot(repo.path) == before

    make_slice(repo, "04-delta")
    ok(repo, *argv_close(MISSION, "03-gamma"), "--rewrite-dependents-to", "OPR.0.6.0.4")
    assert front(beta / "SPEC.md")["depends_on"] == ["OPR.0.6.0.4"]
    nxt = repo.path / "missions/release-0.7.0/slices/01-next/SPEC.md"
    assert front(nxt)["depends_on"] == ["OPR.0.6.0.4"]
    assert lint_all(repo).returncode == 0


def argv_close(mission: str, name: str) -> list[str]:
    """Return the `slice close <dir> --reason superseded` argv for a slice of a mission."""
    return ["slice", "close", f"{mission}/slices/{name}", "--reason", "superseded"]


def test_slice_move_between_missions(tmp_git_repo: GitRepo) -> None:
    """Move renumbers the slice in the target, updates both compositions and its dependents."""
    repo = tmp_git_repo
    mission = mission_repo(repo)
    make_slice(repo, "01-alpha")
    beta = make_slice(repo, "02-beta", "--depends-on", "OPR.0.6.0.1")
    ok(repo, "mission", "create", "--name", "hyper-update", "--intent", "Update.")
    target = repo.path / "missions" / "hyper-update"
    ok(repo, "slice", "move", f"{MISSION}/slices/01-alpha", "--to-mission", "hyper-update")
    moved = target / "slices" / "01-alpha"
    spec = front(moved / "SPEC.md")
    assert spec["id"] == "OPR.99.0.1.1" and spec["mission"] == "hyper-update"
    assert [m.ref for m in hyper_openrig.read_mission_members(mission)] == [
        "slices/02-beta/slice.yaml"
    ]
    assert [m.ref for m in hyper_openrig.read_mission_members(target)] == [
        "slices/01-alpha/slice.yaml"
    ]
    assert front(beta / "SPEC.md")["depends_on"] == ["OPR.99.0.1.1"]
    assert "| OPR.99.0.1.1 | slices/01-alpha |" in (target / "SPEC.md").read_text()
    assert lint_all(repo).returncode == 0

    make_slice(repo, "01-other")  # release-0.6.0 again holds an 01 slice
    before = snapshot(repo.path)
    proc = run(repo, "slice", "move", "OPR.99.0.1.1", "--to-mission", MISSION)
    assert proc.returncode == 5
    assert snapshot(repo.path) == before


# ---------------------------------------------------------------------------
# dag
# ---------------------------------------------------------------------------


def test_dag_cycle_precedence(tmp_git_repo: GitRepo) -> None:
    """Kahn order; slice.yaml execution.depends_on wins; a cycle exits 1 with its path."""
    repo = tmp_git_repo
    mission_repo(repo)
    alpha = make_slice(repo, "01-alpha")
    beta = make_slice(repo, "02-beta", "--depends-on", "OPR.0.6.0.1")
    make_slice(repo, "03-gamma", "--depends-on", "OPR.0.6.0.2")
    proc = ok(repo, "dag", "--json")
    assert json.loads(proc.stdout)["order"] == ["OPR.0.6.0.1", "OPR.0.6.0.2", "OPR.0.6.0.3"]

    # rig owns slice.yaml; HACF reads execution.depends_on and lets it win over SPEC.md.
    manifest = hyper_openrig.render_slice_yaml()
    (beta / "slice.yaml").write_text(manifest + "execution:\n  depends_on: []\n")
    (alpha / "slice.yaml").write_text(manifest + "execution:\n  depends_on: [OPR.0.6.0.2]\n")
    report = json.loads(ok(repo, "dag", "--json").stdout)
    assert report["order"] == ["OPR.0.6.0.2", "OPR.0.6.0.1", "OPR.0.6.0.3"]
    sources = {record["id"]: record["depends_on_source"] for record in report["slices"]}
    assert sources == {
        "OPR.0.6.0.1": "slice.yaml",
        "OPR.0.6.0.2": "slice.yaml",
        "OPR.0.6.0.3": "SPEC.md",
    }

    (alpha / "slice.yaml").write_text(manifest)
    (beta / "slice.yaml").write_text(manifest + "execution:\n  depends_on: [OPR.0.6.0.3]\n")
    proc = run(repo, "dag", "--json")
    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert report["order"] == ["OPR.0.6.0.1"]
    assert report["cycle"] == ["OPR.0.6.0.2", "OPR.0.6.0.3", "OPR.0.6.0.2"]
    assert "cycle: OPR.0.6.0.2 -> OPR.0.6.0.3 -> OPR.0.6.0.2" in proc.stderr

    (beta / "slice.yaml").write_text(manifest + "execution:\n  depends_on: [OPR.0.6.0.8]\n")
    proc = run(repo, "dag")
    assert proc.returncode == 1 and "OPR.0.6.0.8 names no slice" in proc.stderr


# ---------------------------------------------------------------------------
# compare-and-swap
# ---------------------------------------------------------------------------


def test_scope_cas_refuses_concurrent_change(
    tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mission PROGRESS.md changed between read and replace: exit 5, the other bytes kept."""
    repo = tmp_git_repo
    mission = mission_repo(repo)
    make_slice(repo, "01-alpha")
    make_slice(repo, "02-beta", "--depends-on", "OPR.0.6.0.1")
    make_slice(repo, "03-gamma")
    monkeypatch.chdir(repo.path)
    progress = mission / "PROGRESS.md"
    other = "# Progress - rewritten by another writer\n"

    def concurrently(real):
        def wrapper(*args, **kwargs):
            result = real(*args, **kwargs)
            progress.write_text(other)  # rig writes PROGRESS.md while HACF holds old bytes
            return result

        return wrapper

    # slice create: the write lands between add_slice_to_mission and the table rewrite.
    before = snapshot(repo.path)
    real_add = hyper_openrig.add_slice_to_mission
    monkeypatch.setattr(hyper_openrig, "add_slice_to_mission", concurrently(real_add))
    argv = ["slice", "create", "--mission", MISSION, "--name", "04-delta", "--intent", "D."]
    assert hyper_scope.main(argv) == 5
    assert progress.read_text() == other
    expected = {**before, "missions/release-0.6.0/PROGRESS.md": ("file", other.encode())}
    assert snapshot(repo.path) == expected  # the slice dir and its composition entry are undone
    monkeypatch.setattr(hyper_openrig, "add_slice_to_mission", real_add)

    # slice close: the write lands after close_superseded moved the slice; the move is undone.
    progress.write_bytes(before["missions/release-0.6.0/PROGRESS.md"][1])
    real_close = hyper_openrig.close_superseded
    monkeypatch.setattr(hyper_openrig, "close_superseded", concurrently(real_close))
    argv = ["slice", "close", "OPR.0.6.0.1", "--reason", "superseded"]
    assert hyper_scope.main([*argv, "--rewrite-dependents-to", "OPR.0.6.0.3"]) == 5
    assert progress.read_text() == other
    assert snapshot(repo.path) == expected


# ---------------------------------------------------------------------------
# archive_specs.py
# ---------------------------------------------------------------------------


def archive_root(tmp_path: Path, name: str, script: str | None = None) -> Path:
    """Return a temp repo root holding a copy of archive_specs.py (the new one by default)."""
    root = tmp_path / name
    scripts = root / ".agents" / "scripts"
    scripts.mkdir(parents=True)
    text = script if script is not None else (SCRIPTS_DIR / "archive_specs.py").read_text()
    (scripts / "archive_specs.py").write_text(text, encoding="utf-8")
    shutil.copyfile(SCRIPTS_DIR / "_runtime.py", scripts / "_runtime.py")
    return root


def run_archive(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the archive_specs.py copy inside root, with root as the working directory."""
    return subprocess.run(
        [sys.executable, str(root / ".agents" / "scripts" / "archive_specs.py"), *args],
        cwd=root,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def spec_tree(root: Path) -> dict[str, tuple]:
    """Snapshot spec/ with the archive timestamps replaced by <TS>."""
    tree = snapshot(root)
    return {
        re.sub(r"\d{8}_\d{6}_", "<TS>_", rel): entry
        for rel, entry in tree.items()
        if rel.startswith("spec")
    }


def _normalized(text: str, root: Path) -> str:
    return re.sub(r"\d{8}_\d{6}_", "<TS>_", text.replace(str(root), "<REPO>"))


def _build_active(root: Path, case: str) -> None:
    if case == "no-active":
        return
    active = root / "spec" / "active"
    active.mkdir(parents=True)
    if case in ("with-gitkeep", "empty"):
        (active / ".gitkeep").write_bytes(b"")
    if case in ("with-gitkeep", "no-gitkeep"):
        (active / "Draft_PRD.md").write_bytes(b"# Draft\r\nbody without final newline")
        (active / "RedTeam_Report.md").write_bytes(b"# Red Team\n")
        (active / "notes").mkdir()
        (active / "notes" / "kept.md").write_bytes(b"directories stay\n")
    if case == "with-gitkeep":
        (root / "spec" / "archive").mkdir()


@pytest.mark.parametrize("case", ["with-gitkeep", "no-gitkeep", "empty", "no-active", "no-args"])
def test_archive_positional_golden(tmp_path: Path, case: str) -> None:
    """The 984ca46 script and the new one agree except for the two S03b changes."""
    old_text = OLD_ARCHIVE.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(old_text) + old_text).hexdigest()
    assert blob == OLD_ARCHIVE_BLOB  # the fixture is the 984ca46 script, byte for byte
    old_root = archive_root(tmp_path, "old", old_text.decode("utf-8"))
    new_root = archive_root(tmp_path, "new")
    args = [] if case == "no-args" else ["v0.6.0 Feature"]
    results = {}
    for root in (old_root, new_root):
        _build_active(root, case)
        proc = run_archive(root, *args)
        results[root] = (proc.returncode, _normalized(proc.stdout, root), spec_tree(root))
    old, new = results[old_root], results[new_root]
    assert new[0] == old[0]
    assert new[1] == old[1]
    differences = {
        rel: (old[2].get(rel), new[2].get(rel))
        for rel in set(old[2]) | set(new[2])
        if old[2].get(rel) != new[2].get(rel)
    }
    allowed = {
        "no-gitkeep": {"spec/active/.gitkeep": (("file", b""), None)},  # no .gitkeep recreated
        "empty": {"spec/archive": (("dir",), None)},  # no archive dir when nothing moves
    }
    assert differences == allowed.get(case, {})
    if case == "with-gitkeep":
        moved = "spec/archive/<TS>_v0_6_0_Feature/Draft_PRD.md"
        assert new[2][moved] == ("file", b"# Draft\r\nbody without final newline")
        assert new[2]["spec/active/notes/kept.md"] == ("file", b"directories stay\n")


def test_no_gitkeep_recreation(tmp_path: Path) -> None:
    """A positional run on a spec/active/ without .gitkeep leaves no .gitkeep behind."""
    root = archive_root(tmp_path, "repo")
    _build_active(root, "no-gitkeep")
    proc = run_archive(root, "feature")
    assert proc.returncode == 0, proc.stdout
    assert sorted(p.name for p in (root / "spec" / "active").iterdir()) == ["notes"]
    assert not (root / "spec" / "active" / ".gitkeep").exists()


def test_archive_miniprd(tmp_path: Path) -> None:
    """--miniprd moves one compiled MiniPRD to its _AUDITED name; refusals move nothing."""
    root = archive_root(tmp_path, "repo")
    compiled = root / "spec" / "compiled"
    compiled.mkdir(parents=True)
    body = b"# MiniPRD: X\r\nbytes kept\n"
    (compiled / "MiniPRD_X.md").write_bytes(body)
    proc = run_archive(root, "--miniprd", "spec/compiled/MiniPRD_X.md")
    assert proc.returncode == 0, proc.stdout
    assert "spec/archive/MiniPRD_X_AUDITED.md" in proc.stdout
    assert (root / "spec" / "archive" / "MiniPRD_X_AUDITED.md").read_bytes() == body
    assert not (compiled / "MiniPRD_X.md").exists()

    (compiled / "MiniPRD_X.md").write_bytes(b"a second copy\n")  # its target exists now
    (tmp_path / "elsewhere.md").write_bytes(b"outside\n")
    os.symlink(tmp_path / "elsewhere.md", compiled / "MiniPRD_Link.md")
    (root / "spec" / "active").mkdir()
    (root / "spec" / "active" / "MiniPRD_Y.md").write_bytes(b"not compiled\n")
    (compiled / "SuperPRD_Z.md").write_bytes(b"not a MiniPRD\n")
    before = snapshot(root)
    for path in (
        "spec/compiled/MiniPRD_X.md",
        "spec/compiled/MiniPRD_Link.md",
        "spec/active/MiniPRD_Y.md",
        "spec/compiled/SuperPRD_Z.md",
        "spec/compiled/MiniPRD_Missing.md",
        str(tmp_path / "elsewhere.md"),
    ):
        proc = run_archive(root, "--miniprd", path)
        assert proc.returncode == 1 and "nothing was moved" in proc.stdout, path
    assert snapshot(root) == before


def test_archive_paths_label(tmp_path: Path) -> None:
    """--paths --label moves named files into spec/archive/<ts>_<label>/; refusals move none."""
    root = archive_root(tmp_path, "repo")
    (root / "spec" / "active").mkdir(parents=True)
    (root / "spec" / "compiled").mkdir()
    failed = root / "spec" / "active" / "FAILED_WORKFLOWS.md"
    failed.write_bytes(b"# Failed\r\n")
    legacy = root / "spec" / "compiled" / "MiniPRD_01_A.md"
    legacy.write_bytes(b"# MiniPRD: A\n")
    (tmp_path / "outside.md").write_bytes(b"outside\n")
    os.symlink(tmp_path / "outside.md", root / "spec" / "active" / "link.md")
    (root / "spec" / "compiled" / "FAILED_WORKFLOWS.md").write_bytes(b"same name\n")
    before = snapshot(root)
    for args in (
        ["--paths", "spec/active/link.md", "--label", "x"],
        ["--paths", str(tmp_path / "outside.md"), "--label", "x"],
        ["--paths", str(failed), "spec/compiled/FAILED_WORKFLOWS.md", "--label", "x"],
        ["--paths", "spec/active/missing.md", "--label", "x"],
        ["--paths", str(failed)],
    ):
        proc = run_archive(root, *args)
        assert proc.returncode == 1, (args, proc.stdout)
    assert snapshot(root) == before

    proc = run_archive(
        root, "--paths", "spec/active/FAILED_WORKFLOWS.md", str(legacy), "--label", "v060 migrated"
    )
    assert proc.returncode == 0, proc.stdout
    folders = list((root / "spec" / "archive").iterdir())
    assert len(folders) == 1 and re.fullmatch(r"\d{8}_\d{6}_v060_migrated", folders[0].name)
    assert (folders[0] / "FAILED_WORKFLOWS.md").read_bytes() == b"# Failed\r\n"
    assert (folders[0] / "MiniPRD_01_A.md").read_bytes() == b"# MiniPRD: A\n"
    assert not failed.exists() and not legacy.exists()
    rel = folders[0].relative_to(root).as_posix()
    assert proc.stdout.splitlines() == [
        f"Archived: spec/active/FAILED_WORKFLOWS.md -> {rel}/FAILED_WORKFLOWS.md",
        f"Archived: spec/compiled/MiniPRD_01_A.md -> {rel}/MiniPRD_01_A.md",
    ]


# ---------------------------------------------------------------------------
# R39: S03b audit notes F1 and F2 (S12a)
# ---------------------------------------------------------------------------


def test_out_of_workspace_missions_root_foreign(tmp_git_repo: GitRepo) -> None:
    """F1: missions.root outside the workspace makes project.yaml foreign; init writes nothing."""
    repo = tmp_git_repo
    text = hyper_openrig.render_project_yaml().replace(
        "  root: missions\n", "  root: ../elsewhere\n"
    )
    assert "root: ../elsewhere" in text
    repo.write("project.yaml", text)
    before = snapshot(repo.path)

    proc = run(repo, "project", "init", "--check-only", "--json")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    entry = next(e for e in report["items"] if e["item"] == "project.yaml")
    assert entry["class"] == "foreign"
    assert "outside the workspace" in entry["reason"]
    assert report["foreign"] == ["project.yaml"]
    assert snapshot(repo.path) == before

    proc = run(repo, "project", "init")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "nothing was written" in proc.stderr
    assert snapshot(repo.path) == before
    assert not (repo.path.parent / "elsewhere").exists()


def test_project_init_renders_before_mkdir(
    tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2: a SPEC.md render failure happens before any mkdir, so no path is created."""
    repo = tmp_git_repo
    monkeypatch.chdir(repo.path)
    before = snapshot(repo.path)
    seen: list[bool] = []

    def failing_render(*args, **kwargs):
        seen.append((repo.path / "missions").exists())
        raise hyper_scope.SpecError("injected SPEC.md render failure")

    monkeypatch.setattr(hyper_scope, "project_spec_text", failing_render)
    assert hyper_scope.main(["project", "init"]) != 0
    assert seen == [False]  # rendered before missions/ was created, not rolled back after
    assert not (repo.path / "missions").exists()
    assert snapshot(repo.path) == before
