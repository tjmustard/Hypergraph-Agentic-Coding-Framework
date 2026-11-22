"""Deterministic tests for hyper_migrate.py, the spec part of the v0.6.0 migration (S13a).

Every project is a temp git repository under tmp_path: S12a's v0.5.9 fixture builder
(`dev/tests/fixtures/build_user_project_v059.py`), the HACF `spec/` of bootstrap commit 8e9464e
(`git archive`), or a small hand-made tree. Each one gets this checkout's `.agents/scripts` and
`.agents/schemas` copied in and committed (the state after the v0.6.0 scripts were installed),
and the migration runs as a subprocess of the project's own copy. Nothing reads or writes this
checkout's `spec/`; PATH holds no `rig`.

The golden plans in `golden/` are generated, never hand-edited. Regenerate them (S13b extends
plan and apply) with:

    HACF_REGEN_GOLDEN=1 uv run --locked pytest dev/tests/migration -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, clean_env

GOLDEN = Path(__file__).resolve().parent / "golden"
REGEN = os.environ.get("HACF_REGEN_GOLDEN") == "1"
SNAPSHOT_COMMIT = "8e9464e"
JOURNAL = ".agents/state/migrations/v0.6.0.json"
_spec = importlib.util.spec_from_file_location(
    "build_user_project_v059", FIXTURES_DIR / "build_user_project_v059.py"
)
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

BASELINE_SUPERPRD = """\
# SuperPRD: Demo billing baseline

## 1. Current Core Value Loop

Shops bill customers monthly.

## 2. Existing Data Models

- Invoice, Payment.

## 3. Established Patterns

- Hexagonal layout.

## 4. Identified Technical Debt

- No retries on payment calls.
"""
UNREFERENCED = """\
# MiniPRD: Reports

**Hypergraph Node ID:** reports
**Parent Node:** billing

## 1. The Confidence Mandate

Confidence 9/10.

## 3. Implementation Plan (Task List)

- [ ] Task 1: build Reports.
"""


def _no_rig_path() -> str:
    """Return PATH without any directory that holds a `rig` executable."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(d for d in dirs if d and not (Path(d) / "rig").exists())


def _env(**extra: str) -> dict[str, str]:
    """Return the child environment: no git config, a fixed identity, no rig, not autonomous."""
    env = clean_env(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="HACF Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="HACF Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        PATH=_no_rig_path(),
    )
    env.pop("HACF_AUTONOMOUS", None)
    env.update(extra)
    return env


def git(project: Path, *args: str) -> str:
    """Run git in the project and return stdout; fail the test on a non-zero exit."""
    proc = subprocess.run(
        ["git", *args], cwd=project, env=_env(), capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout


def commit(project: Path, message: str) -> str:
    """Stage everything and commit it; return the sha."""
    git(project, "add", "-A", "--", ".")
    git(project, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
    return git(project, "rev-parse", "HEAD").strip()


def migrate(project: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run the project's own hyper_migrate.py with stdin not a TTY."""
    return subprocess.run(
        [sys.executable, str(project / ".agents/scripts/hyper_migrate.py"), *args],
        cwd=project,
        env=_env(**env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def tool(project: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of the project's own HACF scripts."""
    return subprocess.run(
        [sys.executable, str(project / ".agents/scripts" / script), *args],
        cwd=project,
        env=_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def install_scripts(project: Path) -> None:
    """Copy this checkout's .agents/scripts/*.py and .agents/schemas/ into the project."""
    scripts = project / ".agents" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for path in SCRIPTS_DIR.glob("*.py"):
        shutil.copy2(path, scripts / path.name)
    shutil.copytree(
        REPO_ROOT / ".agents" / "schemas", project / ".agents" / "schemas", dirs_exist_ok=True
    )


def init_repo(project: Path) -> Path:
    """Create an empty repository with the v0.6.0 scripts committed."""
    project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    install_scripts(project)
    return project


def write(project: Path, rel: str, text: str) -> Path:
    """Write text to project/rel, creating parent directories."""
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fixture_project(tmp_path: Path) -> Path:
    """Return S12a's default v0.5.9 fixture plus the Test 1 extras, with the v0.6.0 scripts.

    The extras (a baseline SuperPRD and an unreferenced MiniPRD) are built on top of the
    builder's output; the builder itself is not edited.
    """
    project = builder.build(tmp_path / "project", "default")
    write(project, "spec/compiled/SuperPRD.md", BASELINE_SUPERPRD)
    write(project, "spec/compiled/MiniPRD_Reports.md", UNREFERENCED)
    install_scripts(project)
    commit(project, "fixture: baseline SuperPRD, unreferenced MiniPRD, v0.6.0 scripts")
    return project


def miniprd(title: str, node: str, sid: str | None = None) -> str:
    """Return a small legacy MiniPRD, with an hacf-bootstrap block when sid is given."""
    text = (
        f"# MiniPRD: {title}\n\n**Hypergraph Node ID:** {node}\n**Parent Node:** core\n\n"
        "## 1. The Confidence Mandate\n\nConfidence 9/10.\n\n"
        "## 2. Atomic User Stories\n\n- **US-001:** As a user, I want it.\n\n"
        "## 3. Implementation Plan (Task List)\n\n- [ ] Task 1: build it.\n\n"
        "## 4. The Negative Space (Constraints)\n\n- **DO NOT** add a dependency.\n\n"
        "## 5. Integration Tests & Verification\n\n- **Test 1:** it works.\n"
    )
    if sid:
        text += f"\n## Pipeline Operating Notes\n\n```hacf-bootstrap\nslice: {sid}\nnn: 1\n```\n"
    return text


def superprd(title: str, *entries: str) -> str:
    """Return a FEATURE SuperPRD whose Execution Checklist holds the given lines."""
    lines = "\n".join(entries)
    return (
        f"# SuperPRD: {title}\n\n## 1. Introduction & Goals\n\nGoals.\n\n"
        f"## 5. Technical Specifications\n\n### 5.3 Execution Checklist (MiniPRDs)\n\n{lines}\n\n"
        "## 6. Negative Constraints\n\n- none\n"
    )


def tree(project: Path) -> dict[str, tuple]:
    """Return every work-tree path (bytes and mtime) except .git and .agents/state.

    It also records the git paths the migration may create: info/attributes, hacf/ and the
    merge-driver config.
    """
    found: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(project):
        here = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if (here / d).relative_to(project).as_posix() not in (".git", ".agents/state")
        ]
        for name in dirnames:
            found[(here / name).relative_to(project).as_posix()] = ("dir",)
        for name in filenames:
            path = here / name
            found[path.relative_to(project).as_posix()] = (
                "file",
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
    for rel in (".git/info/attributes", ".git/hacf"):
        found[rel] = ("present",) if (project / rel).exists() else ("absent",)
    config = git(project, "config", "--local", "--list")
    found["git-config-merge"] = tuple(
        sorted(x for x in config.splitlines() if x.startswith("merge."))
    )
    return found


def content(snap: dict[str, tuple]) -> dict[str, tuple]:
    """Drop mtimes and the lock dir: compare bytes and presence only.

    `.git/hacf/locks/` is the repository's shared lock directory (R22.1); a rollback keeps it.
    """
    return {key: value[:2] for key, value in snap.items() if key != ".git/hacf"}


def front(path: Path) -> dict:
    """Return the YAML frontmatter of a Markdown file."""
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---\n", 2)[1])


def plan_json(project: Path, *args: str) -> dict:
    """Run `plan --json` and return the parsed plan."""
    proc = migrate(project, "plan", "--json", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def check_golden(name: str, text: str) -> None:
    """Compare text with golden/<name> byte-for-byte (or rewrite it under HACF_REGEN_GOLDEN=1)."""
    path = GOLDEN / name
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, f"{name} differs; see the module docstring"


def slice_actions(plan: dict) -> list[dict]:
    """Return the plan's slice-create actions."""
    return [a for a in plan["actions"] if a["op"] == "slice-create"]


def h1_intent(text: str) -> str:
    """Return the Task 17 intent of a legacy MiniPRD: the H1 after `# MiniPRD: `."""
    for line in text.split("\n"):
        if line.startswith("# MiniPRD: "):
            return line[len("# MiniPRD: ") :].strip()
        if line.startswith("# "):
            return line[2:].strip()
    raise AssertionError("no H1")


# ---------------------------------------------------------------------------
# Test 1 and Test 2: golden plans
# ---------------------------------------------------------------------------


def test_golden_plan_fixture(tmp_path: Path) -> None:
    """Test 1 and Task 36: the golden plan, no writes, and each slice's intent is its H1."""
    project = fixture_project(tmp_path)
    before = tree(project)
    proc = migrate(project, "plan", "--json")
    assert proc.returncode == 0, proc.stderr
    check_golden("plan_fixture.json", proc.stdout)
    assert tree(project) == before  # plan wrote nothing: no state dir, no lock, no bytecode
    plan = json.loads(proc.stdout)
    detect = migrate(project, "detect")
    assert detect.returncode == 1, detect.stderr
    assert "flat_compiled_miniprd: spec/compiled/MiniPRD_01_Invoices.md" in detect.stdout
    assert tree(project) == before

    ops = [(a["op"], a.get("source")) for a in plan["actions"]]
    assert ("project-spec", "spec/compiled/SuperPRD.md") in ops
    missions = [a["mission"] for a in plan["actions"] if a["op"] == "mission-create"]
    assert missions == ["missions/demo-billing-service", "missions/legacy-unassigned"]
    # The fixture's checklist names MiniPRDs by bare name only (`MiniPRD_01_Invoices`, no `.md`,
    # not bold), which is neither an exact filename nor `**MiniPRD: <name>**`: no fragments.
    assert {a["mission"] for a in slice_actions(plan)} == {"missions/legacy-unassigned"}
    assert [a["source"] for a in slice_actions(plan)] == [
        "spec/compiled/MiniPRD_01_Invoices.md",
        "spec/compiled/MiniPRD_02_Payments.md",
        "spec/compiled/MiniPRD_Reports.md",
    ]
    assert plan["not_imported"] == ["spec/archive/MiniPRD_00_Setup_AUDITED.md"]

    # R37: every created slice's intent is the legacy H1 after `# MiniPRD: `.
    proc = migrate(project, "apply", "--yes")
    assert proc.returncode == 0, proc.stderr
    for action in slice_actions(plan):
        legacy = git(project, "show", f"HEAD:{action['source']}")
        spec = front(project / action["slice"] / "SPEC.md")
        assert spec["intent"] == h1_intent(legacy) == action["intent"]
        assert spec["hacf_legacy_source"] == action["source"]
        assert spec["hacf_legacy_order"] == action["legacy_order"]


def _snapshot_project(tmp_path: Path) -> Path:
    if (
        subprocess.run(
            ["git", "cat-file", "-e", f"{SNAPSHOT_COMMIT}^{{commit}}"], cwd=REPO_ROOT, check=False
        ).returncode
        != 0
    ):
        pytest.skip(
            f"bootstrap commit {SNAPSHOT_COMMIT} is absent (a clone without swimlane history)"
        )
    project = init_repo(tmp_path / "hacf")
    data = subprocess.run(
        ["git", "archive", "--format=tar", SNAPSHOT_COMMIT, "--", "spec"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isfile():
                target = project / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    commit(project, f"spec/ of {SNAPSHOT_COMMIT}")
    return project


def test_golden_plan_hacf_snapshot(tmp_path: Path) -> None:
    """Test 2: the 8e9464e snapshot's plan equals its golden, with the expected membership."""
    project = _snapshot_project(tmp_path)
    before = tree(project)
    proc = migrate(project, "plan", "--include-archived", "--json")
    assert proc.returncode == 0, proc.stderr
    check_golden("plan_hacf_snapshot.json", proc.stdout)
    assert tree(project) == before
    plan = json.loads(proc.stdout)
    by_mission: dict[str, list[str]] = {}
    for action in slice_actions(plan):
        by_mission.setdefault(action["mission"], []).append(Path(action["source"]).name)
        assert action["hacf_status"] == "audited"
    assert by_mission["missions/hyper-update"] == [
        f"MiniPRD_hyper-update-{n}_AUDITED.md"
        for n in ("core-logic", "merge-engine", "recovery", "upstream-verification", "integration")
    ]
    assert by_mission["missions/dynamic-workflow-parallel-execution-engine"] == [
        "MiniPRD_Dynamic_Orchestrator_AUDITED.md",
        "MiniPRD_Autonomous_Resolution_AUDITED.md",
        "MiniPRD_Provenance_Integration_AUDITED.md",
    ]
    unassigned = by_mission["missions/legacy-unassigned"]
    assert len(unassigned) == 7 and "MiniPRD_TokenBudgetEnforcement_AUDITED.md" in unassigned


# ---------------------------------------------------------------------------
# Tests 3-8: apply, idempotency, rollback, confirmation, backup
# ---------------------------------------------------------------------------


def test_apply_then_lint_clean(tmp_path: Path) -> None:
    """Test 3: apply, then `hyper_spec.py lint --all` exits 0 with no error."""
    project = fixture_project(tmp_path)
    proc = migrate(project, "apply", "--yes")
    assert proc.returncode == 0, proc.stderr
    lint = tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert not [line for line in lint.stdout.splitlines() if line.startswith("error")]
    spec = (project / "SPEC.md").read_text(encoding="utf-8")
    assert "## 1. Current Core Value Loop" in spec and "## Product Context" in spec
    assert not list((project / "spec" / "compiled").glob("MiniPRD_*.md"))
    resolve = tool(
        project, "hyper_spec.py", "resolve", "spec/compiled/MiniPRD_01_Invoices.md", "--json"
    )
    assert resolve.returncode == 0, resolve.stderr
    assert (
        json.loads(resolve.stdout)["path"]
        == "missions/legacy-unassigned/slices/01-invoices/SPEC.md"
    )
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    assert journal["legacy_map"]["spec/compiled/MiniPRD_01_Invoices.md"].endswith(
        "01-invoices/SPEC.md"
    )


def test_apply_idempotent(tmp_path: Path) -> None:
    """Test 4: a second apply changes nothing and reports every step done."""
    project = fixture_project(tmp_path)
    assert migrate(project, "apply", "--yes").returncode == 0
    after_first = tree(project)
    proc = migrate(project, "apply", "--yes")
    assert proc.returncode == 0, proc.stderr
    assert tree(project) == after_first
    steps = [line for line in proc.stdout.splitlines() if line.startswith(("done:", "applied:"))]
    assert steps and all(line.startswith("done:") for line in steps)


def test_rollback_byte_identical(tmp_path: Path) -> None:
    """Test 5: rollback restores the pre-apply tree byte-for-byte."""
    project = fixture_project(tmp_path)
    before = content(tree(project))
    assert migrate(project, "apply", "--yes").returncode == 0
    assert (project / "missions").is_dir() and (project / ".git/info/attributes").exists()
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert content(tree(project)) == before
    assert list((project / ".agents/state/migrations").glob("v0.6.0.rolled-back.*.json"))


def test_rollback_refuses_drift(tmp_path: Path) -> None:
    """Test 6: a migrated file edited after apply makes rollback refuse; --force proceeds."""
    project = fixture_project(tmp_path)
    before = content(tree(project))
    assert migrate(project, "apply", "--yes").returncode == 0
    edited = "missions/legacy-unassigned/slices/01-invoices/SPEC.md"
    with open(project / edited, "a", encoding="utf-8") as handle:
        handle.write("\nA human edit.\n")
    drifted = tree(project)
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 5
    assert f"drift: {edited}" in proc.stdout
    assert tree(project) == drifted  # nothing was restored
    proc = migrate(project, "rollback", "--journal", JOURNAL, "--force")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # proceeds; reports the kept file
    assert f"not restored: {edited}" in proc.stdout
    restored = content(tree(project))
    for key, value in before.items():
        assert restored.get(key) == value, key
    assert (project / edited).is_file()  # the edited created file is left, not deleted


def test_apply_yes_refused_autonomous(tmp_path: Path) -> None:
    """Test 7: --yes under HACF_AUTONOMOUS=1, or no TTY without --yes, exits 4; tree unchanged."""
    project = fixture_project(tmp_path)
    before = tree(project)
    proc = migrate(project, "apply", "--yes", HACF_AUTONOMOUS="1")
    assert proc.returncode == 4, proc.stderr
    assert tree(project) == before
    proc = migrate(project, "apply")  # no TTY and no --yes
    assert proc.returncode == 4, proc.stderr
    assert tree(project) == before
    assert not (project / ".agents/state").exists()


def test_backup_verified_with_hashlib(tmp_path: Path) -> None:
    """Test 8: a corrupt tarball stops apply (exit 5) before any spec write; no sha256sum runs."""
    project = fixture_project(tmp_path)
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    marker = tmp_path / "sha256sum-ran"
    stub = fake / "sha256sum"
    stub.write_text(f"#!/bin/sh\necho ran > '{marker}'\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    path = f"{fake}{os.pathsep}{_no_rig_path()}"
    proc = migrate(
        project, "apply", "--yes", HACF_MIGRATE_TEST_CORRUPT_BACKUP="spec.tar.gz", PATH=path
    )
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "SHA256SUMS" in proc.stderr
    assert not marker.exists()  # no sha256sum process was started
    assert not (project / "missions").exists() and not (project / "SPEC.md").exists()
    assert not (project / JOURNAL).exists()
    assert sorted(p.name for p in (project / "spec/compiled").glob("MiniPRD_*.md")) == [
        "MiniPRD_01_Invoices.md",
        "MiniPRD_02_Payments.md",
        "MiniPRD_Reports.md",
    ]
    assert git(project, "status", "--porcelain", "--", "spec") == ""


# ---------------------------------------------------------------------------
# Tests 9-12: archived imports, mission status, twins
# ---------------------------------------------------------------------------


def test_archived_proof_sets_swimlane_sha(tmp_path: Path) -> None:
    """Test 9: the HACF-Slice commit is the proof's candidate_sha and swimlane_sha."""
    project = init_repo(tmp_path / "proj")
    write(project, "spec/archive/MiniPRD_01_Ledger_AUDITED.md", miniprd("Ledger", "ledger", "S01a"))
    write(project, "spec/archive/MiniPRD_02_Other_AUDITED.md", miniprd("Other", "other", "S99z"))
    commit(project, "legacy specs")
    write(project, "src/ledger.py", "print('ledger')\n")
    commit(project, "Build the ledger\n\nHACF-Slice: S01a")
    sha = git(project, "rev-parse", "HEAD").strip()
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    slices = project / "missions/legacy-unassigned/slices"
    found = front(slices / "01-ledger/proof/00-legacy-audit.md")
    assert found["candidate_sha"] == sha and found["swimlane_sha"] == sha
    other = front(slices / "02-other/proof/00-legacy-audit.md")
    assert other["candidate_sha"] == "legacy-unrecorded" and "swimlane_sha" not in other


def test_archived_import_name_matched_checklists(tmp_path: Path) -> None:
    """Test 10: exact bold names and filenames join the mission; `alphabet` does not."""
    project = init_repo(tmp_path / "proj")
    write(
        project,
        "spec/compiled/SuperPRD_feat.md",
        superprd(
            "Feat",
            "1. **MiniPRD: alpha** — first",
            "2. **MiniPRD:  beta ** — second",
            "3. `spec/compiled/MiniPRD_gamma.md` — third",
        ),
    )
    for name in ("alpha", "beta", "gamma", "alphabet"):
        write(project, f"spec/archive/MiniPRD_{name}_AUDITED.md", miniprd(name.title(), name))
    commit(project, "legacy specs")
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    feat = project / "missions/feat/slices"
    assert sorted(p.name for p in feat.iterdir()) == ["01-alpha", "02-beta", "03-gamma"]
    for folder in feat.iterdir():
        spec = front(folder / "SPEC.md")
        assert spec["hacf_status"] == "audited" and spec["status"] == "done"
        proof = front(folder / "proof/00-legacy-audit.md")
        assert proof["artifact_type"] == "qa" and proof["verdict"] == "PASS"
        assert proof["money_evidence"] == spec["hacf_legacy_source"]
        assert (folder / "PROOF.md").is_file()
    unassigned = project / "missions/legacy-unassigned/slices"
    assert [p.name for p in unassigned.iterdir()] == ["01-alphabet"]
    lint = tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout
    for name in ("alpha", "beta", "gamma", "alphabet"):  # archived inputs are never moved
        assert (project / f"spec/archive/MiniPRD_{name}_AUDITED.md").is_file()


def test_historical_mission_status_complete(tmp_path: Path) -> None:
    """Test 11: mission status complete, active and planned (no write) by slice status."""
    project = init_repo(tmp_path / "proj")
    write(
        project,
        "spec/compiled/SuperPRD_a_old.md",
        superprd("Old", "- `MiniPRD_a1.md`", "- `MiniPRD_a2.md`"),
    )
    write(
        project,
        "spec/compiled/SuperPRD_b_mixed.md",
        superprd("Mixed", "- `MiniPRD_b1.md`", "- `MiniPRD_b2.md`"),
    )
    write(project, "spec/compiled/SuperPRD_c_new.md", superprd("New", "- `MiniPRD_c1.md`"))
    for name in ("a1", "a2", "b1"):
        write(project, f"spec/archive/MiniPRD_{name}_AUDITED.md", miniprd(name, name))
    for name in ("b2", "c1"):
        write(project, f"spec/compiled/MiniPRD_{name}.md", miniprd(name, name))
    commit(project, "legacy specs")
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    def status(mission: str) -> str:
        """Return the mission.yaml metadata.status of a mission."""
        return yaml.safe_load((project / f"missions/{mission}/mission.yaml").read_text())[
            "metadata"
        ]["status"]

    assert status("old") == "complete"
    assert status("mixed") == "active"
    assert status("new") == "planned"
    sys.path.insert(0, str(project / ".agents/scripts"))
    try:
        import hyper_openrig

        rendered = hyper_openrig.render_mission_yaml("new")
    finally:
        sys.path.pop(0)
    new_yaml = (project / "missions/new/mission.yaml").read_text(encoding="utf-8")
    composed = yaml.safe_load(new_yaml)
    assert composed["metadata"]["status"] == "planned"
    # Byte-identical to what `mission create` wrote, apart from the composition entry that
    # `slice create` appends: the migration wrote no status there.
    assert new_yaml.split("composition:")[0] == rendered.split("composition:")[0]
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    status_steps = [s["id"] for s in journal["steps"] if s["op"] == "mission-status"]
    assert status_steps == ["mission-status:missions/old", "mission-status:missions/mixed"]


def test_compiled_twin_of_archived(tmp_path: Path) -> None:
    """Test 12: a compiled twin makes no slice, is listed and moved; the _AUDITED file is kept."""
    project = init_repo(tmp_path / "proj")
    write(project, "spec/compiled/SuperPRD_feat.md", superprd("Feat", "- [x] `MiniPRD_alpha.md`"))
    alpha = miniprd("Alpha engine", "alpha")
    write(project, "spec/compiled/MiniPRD_alpha.md", alpha)
    audited = write(project, "spec/archive/MiniPRD_alpha_AUDITED.md", alpha)
    commit(project, "legacy specs")
    audited_sha = hashlib.sha256(audited.read_bytes()).hexdigest()
    plan = plan_json(project, "--include-archived")
    actions = slice_actions(plan)
    assert [(a["source"], a["hacf_status"]) for a in actions] == [
        ("spec/archive/MiniPRD_alpha_AUDITED.md", "audited")
    ]
    assert plan["twins"] == [
        {
            "compiled": "spec/compiled/MiniPRD_alpha.md",
            "archived": "spec/archive/MiniPRD_alpha_AUDITED.md",
        }
    ]
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (project / "spec/compiled/MiniPRD_alpha.md").exists()
    moved = list((project / "spec/archive").glob("*_v060_migrated/MiniPRD_alpha.md"))
    assert len(moved) == 1 and moved[0].read_text(encoding="utf-8") == alpha
    assert hashlib.sha256(audited.read_bytes()).hexdigest() == audited_sha
    spec = front(project / actions[0]["slice"] / "SPEC.md")
    assert spec["intent"] == "Alpha engine"
    assert [p.name for p in (project / "missions/feat/slices").iterdir()] == ["01-alpha"]


def test_move_slice_follows_resolve_and_rollback(tmp_path: Path) -> None:
    """Task 21: move-slice repoints the journal's legacy_map; rollback still restores the tree."""
    project = fixture_project(tmp_path)
    before = content(tree(project))
    assert migrate(project, "apply", "--yes").returncode == 0
    proc = migrate(
        project,
        "move-slice",
        "missions/legacy-unassigned/slices/02-payments",
        "--to-mission",
        "demo-billing-service",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    moved = "missions/demo-billing-service/slices/02-payments/SPEC.md"
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    assert journal["legacy_map"]["spec/compiled/MiniPRD_02_Payments.md"] == moved
    resolve = tool(
        project, "hyper_spec.py", "resolve", "spec/compiled/MiniPRD_02_Payments.md", "--json"
    )
    assert resolve.returncode == 0 and json.loads(resolve.stdout)["path"] == moved
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert content(tree(project)) == before


# ---------------------------------------------------------------------------
# U1 regression: a step that fails midway (Tasks 26 and 29)
# ---------------------------------------------------------------------------


def _fail_progress(project: Path) -> str:
    """Make the project's own `hyper_spec.py progress` exit 1; return the original text.

    Like Test 8's hook, the failure lives in the temp project only: the slice create and the
    proof of an archived (audited) import succeed, then its first progress event fails, so the
    step fails after the tools already wrote the slice and rewrote the mission files.
    """
    spec = project / ".agents/scripts/hyper_spec.py"
    original = spec.read_text(encoding="utf-8")
    patched = original.replace(
        'if __name__ == "__main__":',
        'if __name__ == "__main__":\n    if sys.argv[1:2] == ["progress"]:\n        sys.exit(1)',
        1,
    )
    assert patched != original
    spec.write_text(patched, encoding="utf-8")
    commit(project, "test: progress fails")
    return original


def _without(snap: dict[str, tuple], rel: str) -> dict[str, tuple]:
    return {key: value for key, value in snap.items() if key != rel}


def test_failed_step_is_journaled_resumes_and_rolls_back(tmp_path: Path) -> None:
    """U1: a failed step leaves nothing unjournaled; a re-run resumes; rollback restores all."""
    project = fixture_project(tmp_path)
    original = _fail_progress(project)
    before = content(tree(project))
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "progress" in proc.stderr
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    failed = [s for s in journal["steps"] if s["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["op"] == "slice-create"
    assert failed[0]["outputs"] == [] and failed[0]["dirs_left"] == []  # its writes were put back
    assert not (project / failed[0]["id"].split(":", 1)[1]).exists()
    # Every path on disk that differs from the pre-apply tree is in the journal.
    assert migrate(project, "rollback", "--journal", JOURNAL).returncode == 0
    assert content(tree(project)) == before

    # Fail again, fix the cause, re-run: it resumes at the failed step and completes.
    assert migrate(project, "apply", "--yes", "--include-archived").returncode == 5
    sid = failed[0]["id"]
    (project / ".agents/scripts/hyper_spec.py").write_text(original, encoding="utf-8")
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"resume: {sid}" in proc.stdout and f"applied: {sid}" in proc.stdout
    assert "human edit" not in proc.stdout + proc.stderr
    lint = tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rel = ".agents/scripts/hyper_spec.py"
    assert _without(content(tree(project)), rel) == _without(before, rel)


def test_failed_step_left_paths_stops_rerun_naming_step(tmp_path: Path) -> None:
    """U1: when a failed step left paths, a re-run stops naming that step; rollback clears them."""
    project = fixture_project(tmp_path)
    _fail_progress(project)
    before = content(tree(project))
    assert migrate(project, "apply", "--yes", "--include-archived").returncode == 5
    jpath = project / JOURNAL
    journal = json.loads(jpath.read_text(encoding="utf-8"))
    step = next(s for s in journal["steps"] if s["status"] == "failed")
    # A step that could not put a path back: the leftover is journaled like any change.
    left = step["id"].split(":", 1)[1] + "/SPEC.md"
    made = [p.as_posix() for p in Path(left).parents if not (project / p).exists()]
    write(project, left, "partial\n")
    sha = hashlib.sha256(b"partial\n").hexdigest()
    step["outputs"] = [{"path": left, "before": None, "after": sha}]
    journal["files"][left] = {"before": None, "after": sha}
    journal["created_dirs"] += made
    jpath.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    proc = migrate(project, "apply", "--yes", "--include-archived")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert f"step {step['id']} failed in an earlier run" in proc.stderr
    assert left in proc.stderr and "human edit" not in proc.stderr
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert content(tree(project)) == before


def test_rollback_keeps_user_merge_driver_keys(tmp_path: Path) -> None:
    """Rollback removes only the merge-driver keys apply added, not a section the user had."""
    project = fixture_project(tmp_path)
    git(project, "config", "--local", "merge.hacf-lore.recursive", "binary")
    before = content(tree(project))
    assert migrate(project, "apply", "--yes").returncode == 0
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    assert any(k.startswith("merge.hacf-lore.") for k in journal["git_config_added"])
    proc = migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert content(tree(project)) == before
    assert git(project, "config", "--local", "merge.hacf-lore.recursive").strip() == "binary"
