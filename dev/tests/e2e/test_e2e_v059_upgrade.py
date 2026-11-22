"""The v0.5.9 -> v0.6.0 upgrade, end to end (SuperPRD §8; subsystems.md#verification END-TO-END).

Ten ordered steps, one test each (`test_e2e_step01_*` ... `test_e2e_step10_*`). Every test builds
its own project under its own tmp_path with S12a's fixture builder
(`dev/tests/fixtures/build_user_project_v059.py`) and replays the earlier steps it needs through
the helpers below (install, plan, apply, ...), so no test depends on another test's order or
state. The only shared input is the module's read-only temp upstream: a bare clone of a commit
built from this working tree (S12b's harness, `dev/tests/installer/conftest.py`).

Reused, not reinvented: the installer harness (temp upstream, HACF_TEST=1 + HACF_REPO_URL,
failing download shims for curl, wget, pip, pip3, uv, uvx, npx and npm first on PATH,
`unshare -rn` when this host allows it, no controlling terminal), the migration helpers of
`dev/tests/migration/test_migrate_specs.py` and `test_migrate_stores.py` (the golden-plan
extras, the clean-ledger edits, tree snapshots) and the engine helpers of
`dev/tests/engine/engine_support.py` (the S08 stub executor under HACF_TEST=1, R41).

Notes on the fixture:
- Steps 3-10 use the clean-ledger fixture of `test_migrate_stores.py` (the ` ###` text break
  kept, its three pre-existing graph errors removed), because `hyper_ledger.py validate` exits 1
  on those errors and no migration step may remove an edge. Step 2 uses the exact inputs of the
  golden plan (`dev/tests/migration/golden/plan_fixture.json`).
- Commits the test makes in the temp project pass --no-verify: the HACF pre-commit hook the
  installer puts in `.git/hooks` runs `uv run --locked ruff`, which the fixture's own (toy)
  uv.lock cannot satisfy and which would reach for the network.

Nothing is written inside this checkout, no network is used, OpenRig is never called (PATH holds
no `rig`) and neither hyper_orchestrator.py nor hyper_daemon.py runs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, GitRepo

TESTS = REPO_ROOT / "dev" / "tests"
for _sub in ("migration", "engine"):  # the same sys.path entries pytest adds for those dirs
    if str(TESTS / _sub) not in sys.path:
        sys.path.insert(0, str(TESTS / _sub))

import engine_support as es  # noqa: E402
import hyper_engine as he  # noqa: E402
import hyper_openrig  # noqa: E402
import test_migrate_specs as specs  # noqa: E402
import test_migrate_stores as stores  # noqa: E402


def _load(name: str, path: Path):
    """Import a helper module from a path under its own name (never as a pytest conftest)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


inst = _load("e2e_installer_harness", TESTS / "installer" / "conftest.py")

GOLDEN_PLAN = TESTS / "migration" / "golden" / "plan_fixture.json"
UNINSTALLER = REPO_ROOT / "HACF-uninstall.sh"
JOURNAL = specs.JOURNAL
LEDGER = stores.LEDGER
MANIFEST = ".agents/hacf-manifest.json"
SIDECAR = "AGENTS.hacf-v0.6.0.md"
MISSION = "missions/legacy-unassigned"
USER_FILES = ("pyproject.toml", "uv.lock", "LICENSE", ".gitattributes")
COLLISION_VARIANTS = (
    "collision-spec",
    "collision-project-yaml",
    "collision-workspace-yaml",
    "collision-missions",
)
LOCAL_SELECTIONS = (
    "openrig:\n  id_prefix: XYZ\n"
    "code_graph:\n  backend: codebase-memory-mcp\n  base_branch: develop\n"
)
CBM_ENTRY = {"type": "stdio", "command": "uvx", "args": ["codebase-memory-mcp@0.12.1"]}


# ---------------------------------------------------------------------------
# Shared helpers (each test calls them on its own tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def upstream(tmp_path_factory: pytest.TempPathFactory):
    """Return the read-only temp upstream (a bare clone of the working tree's files)."""
    root = tmp_path_factory.mktemp("e2e-upstream")
    return inst.build_upstream(root, root / "scratch")


def sha256(path: Path) -> str:
    """Return the sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit_all(project: Path, message: str, *exclude: str) -> str:
    """Stage everything (minus exclude pathspecs) and commit without hooks; return the sha."""
    specs.git(project, "add", "-A", "--", ".", *(f":(exclude){rel}" for rel in exclude))
    specs.git(
        project, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--allow-empty",
        "-m", message,
    )  # fmt: skip
    return specs.git(project, "rev-parse", "HEAD").strip()


def build_fixture(
    root: Path,
    variant: str = "default",
    *,
    golden: bool = True,
    clean_ledger: bool = True,
    extras: Optional[dict[str, str]] = None,
) -> Path:
    """Build a v0.5.9 fixture project under root and commit the test's additions.

    golden adds the golden plan's extras (a baseline SuperPRD and an unreferenced MiniPRD);
    clean_ledger applies test_migrate_stores' clean-ledger edits; extras are more files.
    """
    project = specs.builder.build(root / f"v059-{variant}", variant)
    if golden:
        specs.write(project, "spec/compiled/SuperPRD.md", specs.BASELINE_SUPERPRD)
        specs.write(project, "spec/compiled/MiniPRD_Reports.md", specs.UNREFERENCED)
    if clean_ledger:
        ledger = project / LEDGER
        text = ledger.read_text(encoding="utf-8")
        for old, new in stores.LEDGER_FIXES:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        ledger.write_text(text + stores.INVOICES_NODE, encoding="utf-8")
    for rel, text in (extras or {}).items():
        specs.write(project, rel, text)
    if golden or clean_ledger or extras:
        commit_all(project, "fixture: e2e additions")
    return project


def scratch_dir(root: Path) -> Path:
    """Return the dir that holds the installer shims, their log and an empty git config."""
    path = root / "run-scratch"
    path.mkdir(parents=True, exist_ok=True)
    return path


def no_rig_env(scratch: Path, path_prefix: Optional[Path] = None) -> dict[str, str]:
    """Return the env override: path_prefix, the download shims, then PATH without any `rig`.

    The user's own rig (and its OpenRig state) is never reachable from a test run.
    """
    dirs = [*([str(path_prefix)] if path_prefix else []), str(scratch / "bin")]
    return {"PATH": os.pathsep.join([*dirs, specs._no_rig_path()])}


def install(root: Path, upstream, project: Path, *args: str, path_prefix=None, **kwargs):
    """Run HACF-install.sh in project against the temp upstream (inst.run_installer)."""
    scratch = scratch_dir(root)
    env = no_rig_env(scratch, path_prefix)
    return inst.run_installer(scratch, upstream.url, project, *args, env=env, **kwargs)


def upgrade(root: Path, upstream, project: Path, *args: str) -> str:
    """Upgrade-install (-y --ides=claude), assert exit 0 and no download, commit; return output."""
    run = install(root, upstream, project, "-y", "--ides=claude", *args)
    assert run.returncode == 0, run.output
    assert run.shim_calls == [], run.shim_calls
    assert "UPGRADE mode" in run.output
    commit_all(project, "upgrade to HACF v0.6.0")
    return run.output


def apply(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the project's own `hyper_migrate.py apply --yes --include-archived`; assert exit 0."""
    proc = specs.migrate(project, "apply", "--yes", "--include-archived", *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def upgraded(root: Path, upstream) -> Path:
    """Steps 1 and 3's state before apply: the clean-ledger fixture, upgraded and committed."""
    project = build_fixture(root)
    upgrade(root, upstream, project)
    return project


def migrated(root: Path, upstream) -> Path:
    """Step 6's state: upgraded, applied (archived slices included) and committed."""
    project = upgraded(root, upstream)
    apply(project)
    commit_all(project, "migrate to v0.6.0")
    return project


def run_tool(project: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of the project's own HACF scripts (no TTY, no rig on PATH)."""
    return specs.tool(project, script, *args)


def front(path: Path) -> dict:
    """Return a Markdown file's YAML frontmatter."""
    return specs.front(path)


def slice_dirs(project: Path, mission: str = MISSION) -> list[Path]:
    """Return a mission's slice folders in order."""
    return sorted(p for p in (project / mission / "slices").iterdir() if p.is_dir())


def git_common_dir(project: Path) -> Path:
    """Return the project's git common dir as an absolute path."""
    common = Path(specs.git(project, "rev-parse", "--git-common-dir").strip())
    return common if common.is_absolute() else project / common


def run_uninstaller(root: Path, upstream, project: Path, *args: str):
    """Run this checkout's HACF-uninstall.sh like S12c's tests (no terminal, shims first)."""
    scratch = scratch_dir(root)
    proc = subprocess.run(
        [*inst.network_wrapper(), "bash", str(UNINSTALLER), *args],
        cwd=project,
        env=inst.install_env(scratch, upstream.url, no_rig_env(scratch)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=300,
        check=False,
    )
    return inst.Run(proc.returncode, proc.stdout.decode("utf-8", "replace"), [])


def openrig_block(data: bytes) -> bytes:
    """Return the OpenRig managed block of a file, markers included."""
    match = re.search(
        rb"<!-- BEGIN OpenRig MANAGED BLOCK: role -->.*?<!-- END OpenRig MANAGED BLOCK: role -->\n",
        data,
        re.S,
    )
    assert match is not None
    return match.group(0)


def yaml_hidden_bin(root: Path) -> Path:
    """Return a bin dir whose python3 cannot import yaml (S12b Test 2's shim)."""
    lib = root / "yaml-shim-lib"
    (lib / "yaml").mkdir(parents=True)
    (lib / "yaml" / "__init__.py").write_text(
        'raise ImportError("PyYAML is not installed (test shim)")\n', encoding="utf-8"
    )
    bin_dir = root / "yaml-shim-bin"
    bin_dir.mkdir()
    shim = bin_dir / "python3"
    shim.write_text(
        f'#!/bin/sh\nPYTHONPATH="{lib}" exec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    shim.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# Step 1: the non-interactive upgrade install
# ---------------------------------------------------------------------------


def test_e2e_step01_upgrade_install(tmp_path: Path, upstream) -> None:
    """Non-interactive upgrade from a local bare clone: no downloads, user bytes kept (R9, R24).

    Four installer runs, each on its own fixture: the selections upgrade, the customized
    AGENTS.md sidecar, the PyYAML-hidden abort and the `bash -s` consent read from /dev/tty.
    """
    # (a) -y upgrade of claude and cursor with the user's selections in .agents/local/hacf.yml
    # (the codebase-memory-mcp backend, an id prefix): only the selected IDEs get the MCP entry.
    project = build_fixture(
        tmp_path / "a", golden=False, clean_ledger=False,
        extras={".agents/local/hacf.yml": LOCAL_SELECTIONS},
    )  # fmt: skip
    agents_before = (project / "AGENTS.md").read_bytes()
    local_sha = sha256(project / ".agents/local/hacf.yml")
    user_shas = {rel: sha256(project / rel) for rel in USER_FILES}
    run = install(tmp_path / "a", upstream, project, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    assert run.shim_calls == []  # every download tool on PATH fails and logs: none was called
    assert "UPGRADE mode" in run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output
    assert openrig_block((project / "AGENTS.md").read_bytes()) == openrig_block(agents_before)
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "sonnet"
    assert "Bash(make lint)" in settings["permissions"]["allow"]
    assert "Read(./secrets/**)" in settings["permissions"]["deny"]
    assert sha256(project / ".agents/local/hacf.yml") == local_sha  # selections kept
    for rel, sha in user_shas.items():
        assert sha256(project / rel) == sha, rel
    for rel in (".mcp.json", ".cursor/mcp.json"):
        servers = json.loads((project / rel).read_text(encoding="utf-8"))["mcpServers"]
        assert servers["codebase-memory-mcp"] == CBM_ENTRY, rel
    for absent in (".roo", ".windsurf", ".clinerules", ".roo/mcp.json"):
        assert not (project / absent).exists(), absent
    manifest = json.loads((project / MANIFEST).read_text(encoding="utf-8"))
    assert set(manifest["mcp_entries"]) == {".mcp.json", ".cursor/mcp.json"}
    assert manifest["installed_commit"] == upstream.sha

    # (b) A customized (unknown-sha, marker-free) AGENTS.md is untouched; a sidecar is written.
    custom = build_fixture(tmp_path / "b", "custom-instructions", golden=False, clean_ledger=False)
    before = (custom / "AGENTS.md").read_bytes()
    run = install(tmp_path / "b", upstream, custom, "-y")  # every IDE, so AGENTS.md too
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    assert (custom / "AGENTS.md").read_bytes() == before
    assert b"<!-- BEGIN HACF MANAGED BLOCK: hacf -->" in (custom / SIDECAR).read_bytes()

    # (c) PyYAML hidden by a shim python3: the installer aborts before any write.
    hidden = build_fixture(tmp_path / "c", golden=False, clean_ledger=False)
    snap = inst.snapshot(hidden)
    run = install(
        tmp_path / "c", upstream, hidden, "-y", "--ides=claude",
        path_prefix=yaml_hidden_bin(tmp_path / "c"),
    )  # fmt: skip
    assert run.returncode != 0, run.output
    assert "PyYAML is not installed" in run.output and "Nothing was written" in run.output
    assert run.shim_calls == []
    assert inst.snapshot(hidden) == snap

    # (d) `bash -s < HACF-install.sh` with no terminal: consent is read from /dev/tty, so the
    # code-graph install is declined and nothing is downloaded.
    piped = build_fixture(tmp_path / "d", golden=False, clean_ledger=False)
    run = install(
        tmp_path / "d", upstream, piped, "--ides=claude", "--code-graph=codebase-memory-mcp",
        script_on_stdin=True,
    )  # fmt: skip
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    assert "declined (no terminal to ask on)" in run.output
    assert not (piped / ".agents/state/codegraph/project.json").exists()


# ---------------------------------------------------------------------------
# Step 2: the golden plan
# ---------------------------------------------------------------------------


def test_e2e_step02_plan_equals_golden(tmp_path: Path, upstream) -> None:
    """After the upgrade, `hyper_migrate.py plan --json` equals the golden plan and writes nothing.

    The project is the golden plan's own input (S13a Test 1: the default fixture plus the
    baseline SuperPRD and the unreferenced MiniPRD), here upgraded by the installer rather than
    by copying the scripts in.
    """
    project = build_fixture(tmp_path, clean_ledger=False)
    upgrade(tmp_path, upstream, project)
    before = specs.tree(project)
    proc = specs.migrate(project, "plan", "--json")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == GOLDEN_PLAN.read_text(encoding="utf-8")
    assert specs.tree(project) == before
    # The plan step 3 applies (--include-archived) adds the archived MiniPRD as an audited slice.
    archived = specs.plan_json(project, "--include-archived")
    assert archived["not_imported"] == []
    audited = [a for a in specs.slice_actions(archived) if a["hacf_status"] == "audited"]
    assert [a["source"] for a in audited] == ["spec/archive/MiniPRD_00_Setup_AUDITED.md"]
    assert specs.tree(project) == before


# ---------------------------------------------------------------------------
# Step 3: apply
# ---------------------------------------------------------------------------


def test_e2e_step03_apply(tmp_path: Path, upstream) -> None:
    """Apply: missions, chained and audited slices, SPEC.md, ledger v2, lore, firewall, drivers."""
    project = upgraded(tmp_path, upstream)
    gitattributes = (project / ".gitattributes").read_bytes()
    apply(project)

    # Missions: the SuperPRD's mission and legacy-unassigned; chained slices; the archived
    # MiniPRD is an audited (done) slice with a C1 proof.
    missions = sorted(p.name for p in (project / "missions").iterdir() if p.is_dir())
    assert missions == ["demo-billing-service", "legacy-unassigned"]
    folders = slice_dirs(project)
    assert [p.name for p in folders] == ["01-setup", "02-invoices", "03-payments", "04-reports"]
    fronts = [front(p / "SPEC.md") for p in folders]
    assert fronts[0]["hacf_status"] == "audited" and fronts[0]["status"] == "done"
    assert fronts[0]["hacf_legacy_source"] == "spec/archive/MiniPRD_00_Setup_AUDITED.md"
    assert hyper_openrig.has_c1_artifact(folders[0])
    proof = folders[0] / "proof" / "00-legacy-audit.md"
    assert hyper_openrig.c1_problems(proof.read_text(encoding="utf-8")) == []
    assert (folders[0] / "PROOF.md").is_file()
    for previous, current in zip(fronts, fronts[1:]):
        assert current["depends_on"] == [previous["id"]], current["id"]
    assert [f["hacf_status"] for f in fronts[1:]] == ["compiled"] * 3

    # SPEC.md from the baseline SuperPRD, with the folded product context.
    spec = (project / "SPEC.md").read_text(encoding="utf-8")
    assert "## 1. Current Core Value Loop" in spec and "## Product Context" in spec
    assert not list((project / "spec/compiled").glob("*PRD*.md"))  # archived by the migration

    # Ledger v2, valid.
    ledger = yaml.safe_load((project / LEDGER).read_text(encoding="utf-8"))
    assert ledger["schema"] == "hacf.ledger/v2"
    validate = run_tool(project, "hyper_ledger.py", "validate")
    assert validate.returncode == 0, validate.stdout + validate.stderr

    # Lore: imported ADRs and the canon proposal are PROPOSED; nothing reached the canon.
    adrs = stores.adr_nodes(project)
    assert sorted(adrs) == ["docs/adr/0001-use-postgres.md", "docs/adr/0002-monthly-billing.md"]
    assert {node["status"] for node in adrs.values()} == {"PROPOSED"}
    canon = [n for n in stores.graph(project)["nodes"] if n["type"] == "canon"]
    assert len(canon) == 1 and canon[0]["status"] == "PROPOSED"
    assert canon[0]["pointer"]["path"].startswith("lore/proposed/")
    assert not list((project / "lore/canon").glob("*.md"))

    # Firewall: the .agentignore rules and the gate hook are in .claude/settings.json.
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert any("spec/archive" in rule for rule in deny), deny
    hooks = json.dumps(settings.get("hooks", {}))
    assert "hyper_gate_hook.py" in hooks
    check = run_tool(project, "hyper_firewall.py", "check")
    assert check.returncode == 0, check.stdout + check.stderr

    # Lint clean, and `verify` agrees (lint, ledger, lore, firewall).
    lint = run_tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    verify = specs.migrate(project, "verify")
    assert verify.returncode == 0, verify.stdout + verify.stderr

    # R10: no HACF .gitattributes; the merge drivers are in <git-common-dir>/info/attributes.
    assert (project / ".gitattributes").read_bytes() == gitattributes
    assert b"merge=hacf" not in gitattributes
    tracked = specs.git(project, "ls-files", "--", ":(glob)**/.gitattributes").split()
    assert tracked == [".gitattributes"]  # the user's own Git LFS file only
    found = [
        p for p in project.rglob(".gitattributes") if ".git" not in p.relative_to(project).parts
    ]
    assert found == [project / ".gitattributes"]
    info = (git_common_dir(project) / "info" / "attributes").read_text(encoding="utf-8")
    assert "# BEGIN HACF MANAGED BLOCK: merge-drivers" in info
    assert "lore_hypergraph.yml merge=hacf-lore" in info


# ---------------------------------------------------------------------------
# Step 4: a re-run is a no-op
# ---------------------------------------------------------------------------


def test_e2e_step04_rerun_noop(tmp_path: Path, upstream) -> None:
    """A second apply changes nothing (bytes and mtimes) and reports every step done."""
    project = upgraded(tmp_path, upstream)
    apply(project)
    after_first = specs.tree(project)
    journal = (project / JOURNAL).read_bytes()
    proc = apply(project)
    assert specs.tree(project) == after_first
    steps = [line for line in proc.stdout.splitlines() if line.startswith(("done:", "applied:"))]
    assert steps and all(line.startswith("done:") for line in steps), steps
    assert json.loads((project / JOURNAL).read_bytes())["steps"] == json.loads(journal)["steps"]


# ---------------------------------------------------------------------------
# Step 5: rollback
# ---------------------------------------------------------------------------


def test_e2e_step05_rollback(tmp_path: Path, upstream) -> None:
    """Rollback is byte-identical; after a user edit it refuses (listing it) unless --force."""
    project = upgraded(tmp_path, upstream)
    before = specs.content(specs.tree(project))
    apply(project)
    assert (project / "missions").is_dir() and (project / ".git/info/attributes").exists()
    proc = specs.migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert specs.content(specs.tree(project)) == before
    assert list((project / ".agents/state/migrations").glob("v0.6.0.rolled-back.*.json"))

    # R25: apply again, edit a migrated file, and the rollback refuses (exit 5) listing it.
    apply(project)
    edited = f"{MISSION}/slices/02-invoices/SPEC.md"
    with open(project / edited, "a", encoding="utf-8") as handle:
        handle.write("\nA human edit after the migration.\n")
    drifted = specs.tree(project)
    proc = specs.migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert f"drift: {edited}" in proc.stdout
    assert specs.tree(project) == drifted  # nothing was restored
    proc = specs.migrate(project, "rollback", "--journal", JOURNAL, "--force")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # proceeds; reports the kept file
    assert f"not restored: {edited}" in proc.stdout
    restored = specs.content(specs.tree(project))
    for key, value in before.items():
        assert restored.get(key) == value, key
    assert (project / edited).is_file()  # the user's edited file is kept


# ---------------------------------------------------------------------------
# Step 6: re-apply
# ---------------------------------------------------------------------------


ARCHIVE_STAMP = re.compile(r"\d{8}_\d{6}_v060_migrated")
ISO_STAMP = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00")


def archive_stamp(snap: dict) -> dict:
    """Return snap with the archive_specs.py run stamp in paths and bytes replaced by STAMP."""
    result = {}
    for key, value in snap.items():
        if len(value) > 1 and isinstance(value[1], bytes):
            data = ARCHIVE_STAMP.sub("STAMP", value[1].decode("utf-8", "replace")).encode()
            value = (value[0], ISO_STAMP.sub(b"ISO", data))
        result[ARCHIVE_STAMP.sub("STAMP", key)] = value
    return result


def test_e2e_step06_reapply(tmp_path: Path, upstream) -> None:
    """Apply, roll back, apply again: the second migration reproduces the first one."""
    project = upgraded(tmp_path, upstream)
    apply(project)
    first = specs.content(specs.tree(project))
    proc = specs.migrate(project, "rollback", "--journal", JOURNAL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = apply(project)
    assert "apply: complete" in proc.stdout
    second = specs.content(specs.tree(project))
    first, second = archive_stamp(first), archive_stamp(second)
    assert sorted(second) == sorted(first)
    assert {k: v for k, v in second.items() if first[k] != v} == {}
    lint = run_tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    verify = specs.migrate(project, "verify")
    assert verify.returncode == 0, verify.stdout + verify.stderr


# ---------------------------------------------------------------------------
# Steps 7 and 8: the engine on the migrated project (stub executor, HACF_TEST=1, R41)
# ---------------------------------------------------------------------------

# A test-local wrapper around the stub executor's execute phase: before the stub runs, it
# records one ledger change and one mission note in $HACF_OUTBOX through the project's own
# hyper_ledger.py and hyper_lore.py, the way an agent phase does, then runs the stub argv
# unchanged and passes its result through. It lives under tmp_path, never in the repo.
OUTBOX_WRAPPER = """\
import os, subprocess, sys
slice_id = os.environ["HACF_SLICE"]
node = "e2e_" + "".join(c if c.isalnum() else "_" for c in slice_id.lower())
for argv in (
    [sys.executable, "-B", ".agents/scripts/hyper_ledger.py", "ensure-node", "--id", node,
     "--dimension", "Module"],
    [sys.executable, "-B", ".agents/scripts/hyper_lore.py", "note", "--mission", MISSION,
     "--text", "e2e note from " + slice_id],
):
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL)
proc = subprocess.run(sys.argv[1:], check=False, stdout=subprocess.PIPE)
sys.stdout.write(proc.stdout.decode("utf-8"))
sys.exit(proc.returncode)
"""


def engine_project(root: Path, upstream, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return the migrated project, ready for the engine (engine_support's discipline).

    The S08 stub is committed at its shipped argv path; the ledger is gitignored, so a run
    stages it until `land` (R5); worktrees go under tmp (git.worktree_root in the project's
    .agents/local/hacf.yml orchestration block) and no oracle runs.
    """
    project = migrated(root, upstream)
    (root / "git-scratch").mkdir()
    repo = GitRepo(project, root / "git-scratch")
    stub = project / "dev" / "tests" / "fixtures" / "stub_executor.py"
    stub.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES_DIR / "stub_executor.py", stub)
    specs.git(project, "rm", "-q", "--cached", "--", LEDGER)
    with open(project / ".gitignore", "a", encoding="utf-8") as handle:
        handle.write(f"{LEDGER}\n")
    local = project / ".agents/local/hacf.yml"
    config = yaml.safe_load(local.read_text(encoding="utf-8")) if local.is_file() else {}
    config["orchestration"] = {
        "git": {"worktree_root": str(repo.scratch / "worktrees" / "{repo_hash}")},
        "oracle": {"default": [], "prepare": []},
    }
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(yaml.safe_dump(config), encoding="utf-8")
    for key, value in (
        ("user.name", "HACF Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        specs.git(project, "config", key, value)
    commit_all(project, "engine fixture: stub executor, untracked ledger, local config")
    es.engine_env(monkeypatch, root)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("PATH", specs._no_rig_path())
    monkeypatch.setenv("PATH", f"/usr/bin:/bin{os.pathsep}{os.environ['PATH']}")
    assert not es.ledger_tracked(repo)
    return repo


def compiled_slices(project: Path) -> list[str]:
    """Return the ids of the migrated slices the engine schedules (not yet audited), in order."""
    return [
        front(p / "SPEC.md")["id"]
        for p in slice_dirs(project)
        if front(p / "SPEC.md")["hacf_status"] == "compiled"
    ]


def outbox_executor(root: Path):
    """Return a Probes.executor: the stub, whose execute phase first records outbox entries."""
    script = root / "outbox_wrapper.py"
    script.write_text(OUTBOX_WRAPPER.replace("MISSION", repr(MISSION)), encoding="utf-8")

    def make(name, config, allow_unsandboxed=False):
        made = es.hx.get_executor(name, config, allow_unsandboxed=allow_unsandboxed)
        prepare = made.prepare

        def with_outbox(phase, ctx, directory):
            argv = prepare(phase, {**ctx, "scenario": "success"}, directory)
            return [sys.executable, "-B", str(script), *argv] if phase == "execute" else argv

        made.prepare = with_outbox
        return made

    return make


def checkout_state(repo: GitRepo) -> tuple:
    """Return the main checkout's index bytes, files, HEAD and status (S09a2's _checkout)."""
    repo.git("status")  # settle the index's racy entries first
    index = (repo.path / ".git" / "index").read_bytes()
    names = repo.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    files = {name: (repo.path / name).read_bytes() for name in names if name}
    ledger = (repo.path / LEDGER).read_bytes()
    head = (repo.path / ".git" / "HEAD").read_bytes()
    status = repo.git("status", "--porcelain", "--untracked-files=all")
    return index, files, ledger, head, status


def test_e2e_step07_engine_stub(tmp_path: Path, upstream, monkeypatch) -> None:
    """The engine with the stub executor on the migrated project (R1, R5, R41).

    Two runs on one project. The first plays the stub's git_meta_write on the first slice: the
    guard restores the worktree's .git pointer and the slice is ref_violation, its dependents
    upstream_blocked. The second runs every migrated slice with the stub (plus outbox entries):
    DAG order, a worktree per slice, integration commits with trailers, PROGRESS and PROOF,
    NOTES, the staged ledger, the byte-identical main checkout, then land refusing drift and
    landing once the drift is gone.
    """
    repo = engine_project(tmp_path, upstream, monkeypatch)
    project = repo.path
    ids = compiled_slices(project)
    assert len(ids) == 3

    # The git-metadata guard (R1).
    assert es.run(repo, es.make_probes({ids[0]: "git_meta_write"})) == 1
    first = es.state(repo)
    record = first["slices"][ids[0]]
    assert (record["status"], record["failure_type"]) == ("blocked", "ref_violation")
    assert [first["slices"][i]["status"] for i in ids[1:]] == ["upstream_blocked"] * 2
    pointer = Path(record["worktree"]) / ".git"
    assert pointer.read_text(encoding="utf-8").startswith("gitdir: ")
    assert not any(e["event"] == "integrated" for e in es.events(repo))

    # The full run with the stub executor.
    ledger = project / LEDGER
    ledger_before = ledger.read_bytes()
    before = checkout_state(repo)
    refs_before = es.refs(repo)
    assert es.run(repo, es.make_probes(executor=outbox_executor(tmp_path))) == 0
    data = es.state(repo)
    run_id = data["run_id"]
    integration = f"swimlane/hacf/{run_id}/integration"
    records = [data["slices"][sid] for sid in ids]
    assert [r["status"] for r in records] == ["audited"] * 3
    assert checkout_state(repo) == before  # main checkout byte-identical before land
    after = es.refs(repo)
    assert {name: after[name] for name in refs_before} == refs_before
    for name in set(after) - set(refs_before):
        assert name.startswith(("refs/heads/swimlane/hacf/", "refs/hacf/candidates/")), name

    # DAG order: each slice integrates after the one it depends on.
    integrated = [e["slice"] for e in es.events(repo) if e["event"] == "integrated"]
    assert integrated == ids
    seqs = [r["integrated_seq"] for r in records]
    assert seqs == sorted(seqs)

    root = repo.scratch / "worktrees"
    folders = {front(p / "SPEC.md")["id"]: p.name for p in slice_dirs(project)}
    for sid, rec in zip(ids, records):
        # Worktrees: one branch per slice, outside the main checkout, removed on success.
        assert rec["branch"] == f"swimlane/hacf/{run_id}/{folders[sid]}"
        assert Path(rec["worktree"]).is_relative_to(root)
        assert not Path(rec["worktree"]).exists()
        # Integration commits with trailers.
        message = repo.git("log", "-1", "--format=%B", rec["commit"])
        for trailer in (f"HACF-Run: {run_id}", f"HACF-Slice: {sid}", "HACF-Executor: stub"):
            assert trailer in message, (sid, message)
        merge = repo.git("log", "-1", "--format=%B", rec["integration_commit"])
        assert f"HACF-Slice: {sid}" in merge and f"HACF-Run: {run_id}" in merge
        assert repo.git("rev-parse", rec["candidate_ref"]).strip() == rec["commit"]
    assert repo.git("rev-parse", integration).strip() == records[-1]["integration_commit"]

    # PROGRESS and PROOF on the integration branch; NOTES from the outbox.
    paths = {p.name: p.relative_to(project).as_posix() for p in slice_dirs(project)}
    for folder in ("02-invoices", "03-payments", "04-reports"):
        rel = paths[folder]
        progress = repo.git("show", f"{integration}:{rel}/PROGRESS.md")
        assert "executed" in progress and "audited" in progress, progress
        assert repo.git("show", f"{integration}:{rel}/PROOF.md").strip()
        names = repo.git("ls-tree", "--name-only", f"{integration}:{rel}/proof").split()
        qa = [n for n in names if n.endswith("-qa.md")]
        assert len(qa) == 1, names
        text = repo.git("show", f"{integration}:{rel}/proof/{qa[0]}")
        assert hyper_openrig.c1_problems(text) == []
    notes = repo.git("show", f"{integration}:{MISSION}/NOTES.md")
    for sid in ids:
        assert f"e2e note from {sid}" in notes

    # The staged ledger: the run's ledger lives in the run dir until land (R5).
    directory = es.run_dirs(repo)[-1]
    staged = directory / "ledger" / "architecture.yml"
    staged_nodes = yaml.safe_load(staged.read_text(encoding="utf-8"))["nodes"]
    expected = {"e2e_" + re.sub(r"[^0-9a-z]", "_", sid.lower()) for sid in ids}
    assert expected <= {n["id"] for n in staged_nodes}
    assert ledger.read_bytes() == ledger_before
    assert LEDGER not in repo.git("ls-tree", "-r", "--name-only", integration).split()

    # Land refuses ledger drift (R5) and writes nothing.
    probes = es.make_probes()
    ledger.write_bytes(ledger_before + b"\n# a manual edit\n")
    drifted, head = ledger.read_bytes(), repo.git("rev-parse", "HEAD")
    code, report = he.land(he.discover(project, probes), run_id, probes, merge=True)
    assert code == 5 and not report["ok"] and "--rebase-ledger" in report["message"]
    assert ledger.read_bytes() == drifted and repo.git("rev-parse", "HEAD") == head
    assert "landed_at" not in es.state(repo)

    # Without the drift, land applies the staged ledger and fast-forwards main.
    ledger.write_bytes(ledger_before)
    code, report = he.land(he.discover(project, probes), run_id, probes, merge=True)
    assert code == 0 and report["ok"] and report["ledger"] == "applied", report
    assert ledger.read_bytes() == staged.read_bytes()
    assert repo.git("rev-parse", "HEAD") == repo.git("rev-parse", integration)
    assert es.state(repo)["landed_at"]
    validate = run_tool(project, "hyper_ledger.py", "validate")
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_e2e_step08_gate_low_confidence(tmp_path: Path, upstream, monkeypatch) -> None:
    """The stub reports low_confidence on slice 01: 02 is upstream_blocked, nothing integrates."""
    repo = engine_project(tmp_path, upstream, monkeypatch)
    ids = compiled_slices(repo.path)
    head = repo.git("rev-parse", "HEAD").strip()
    assert es.run(repo, es.make_probes({ids[0]: "low_confidence"})) == 1
    data = es.state(repo)
    gated = data["slices"][ids[0]]
    assert (gated["status"], gated["failure_type"]) == ("blocked", "gate_stopped")
    for sid in ids[1:]:
        assert data["slices"][sid]["status"] == "upstream_blocked", sid
    found = es.events(repo)
    assert not [e for e in found if e["event"] == "integrated"]
    assert {e["slice"] for e in found if e["event"] == "upstream_blocked"} == set(ids[1:])
    integration = f"swimlane/hacf/{data['run_id']}/integration"
    assert repo.git("rev-parse", integration).strip() == head  # nothing was integrated
    assert repo.git("rev-parse", "HEAD").strip() == head


# ---------------------------------------------------------------------------
# Step 9: uninstall
# ---------------------------------------------------------------------------


def test_e2e_step09_uninstall_preserves(tmp_path: Path, upstream) -> None:
    """Uninstall keeps every user byte (manifest path); without a manifest it uses legacy hashes."""
    # With the manifest: the migrated project.
    project = migrated(tmp_path / "manifest", upstream)
    manifest = json.loads((project / MANIFEST).read_text(encoding="utf-8"))
    user = [*USER_FILES, "SPEC.md", "project.yaml", "workspace.yaml", "lore_hypergraph.yml"]
    for top in ("missions", "lore", "docs", "services", "spec", ".agents/memory"):
        user += [f"{top}/{rel}" for rel in inst.snapshot(project / top) if not rel.endswith("/")]
    user = sorted(set(user) - set(manifest["files"]))
    before = {rel: sha256(project / rel) for rel in user}
    agents = (project / "AGENTS.md").read_bytes()
    run = run_uninstaller(tmp_path / "manifest", upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output
    assert {rel: sha256(project / rel) for rel in before} == before
    assert (project / "AGENTS.md").read_bytes() == agents  # customized (OpenRig block): kept
    assert not (project / MANIFEST).exists()
    for rel in manifest["files"]:
        assert not (project / rel).exists(), rel
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "sonnet" and "Bash(make lint)" in settings["permissions"]["allow"]
    assert "hyper_gate_hook.py" not in json.dumps(settings)
    assert not (git_common_dir(project) / "info/attributes").exists()

    # Without a manifest (a v0.5.9 project): only files whose sha a v0.5.x release shipped go.
    legacy = build_fixture(tmp_path / "legacy", golden=False, clean_ledger=False)
    shipped = json.loads((REPO_ROOT / ".agents/config/legacy_file_hashes.json").read_bytes())
    unmodified = ".agents/scripts/hypergraph_updater.py"
    edited = ".agents/skills/hyper-execute/SKILL.md"
    assert sha256(legacy / unmodified) in shipped[unmodified]
    with open(legacy / edited, "a", encoding="utf-8") as handle:
        handle.write("\nOur team's extra step.\n")
    assert not (legacy / MANIFEST).exists()
    keep = [*USER_FILES, "AGENTS.md"]
    for top in ("spec", "docs", ".agents/memory"):
        keep += [f"{top}/{rel}" for rel in inst.snapshot(legacy / top) if not rel.endswith("/")]
    before = {rel: sha256(legacy / rel) for rel in keep}
    run = run_uninstaller(tmp_path / "legacy", upstream, legacy, "-y")
    assert run.returncode == 0, run.output
    assert not (legacy / unmodified).exists()
    assert (legacy / edited).is_file()
    assert f"kept (changed since HACF installed it): {edited}" in run.output
    assert {rel: sha256(legacy / rel) for rel in before} == before


# ---------------------------------------------------------------------------
# Step 10: collision variants
# ---------------------------------------------------------------------------


def test_e2e_step10_collision_variants(tmp_path: Path, upstream) -> None:
    """Each foreign root item: exit 5 and nothing written; the install stays in legacy mode."""
    for variant in COLLISION_VARIANTS:
        root = tmp_path / variant
        project = build_fixture(root, variant, golden=False)
        foreign = specs.builder.COLLISIONS[variant][0]
        foreign_bytes = (project / foreign).read_bytes()

        # The installer skips the scaffold (legacy mode) and completes.
        run = install(root, upstream, project, "-y", "--ides=claude")
        assert run.returncode == 0, (variant, run.output)
        assert run.shim_calls == []
        assert "staying in legacy mode" in run.output, variant
        assert (project / foreign).read_bytes() == foreign_bytes
        commit_all(project, "upgrade to HACF v0.6.0 (legacy mode)")

        # The scaffold and the migration both refuse with exit 5 and write nothing.
        before = specs.tree(project)
        check = run_tool(project, "hyper_scaffold.py", "check", "--json")
        assert check.returncode == 5, (variant, check.stdout + check.stderr)
        assert json.loads(check.stdout)["foreign"] == [
            foreign if variant != "collision-missions" else "missions/"
        ]
        scaffold = run_tool(project, "hyper_scaffold.py", "apply", "--yes")
        assert scaffold.returncode == 5, (variant, scaffold.stdout + scaffold.stderr)
        assert "nothing was written" in scaffold.stderr
        assert specs.tree(project) == before, variant
        proc = specs.migrate(project, "apply", "--yes")
        assert proc.returncode == 5, (variant, proc.stdout + proc.stderr)
        assert "foreign root item" in proc.stderr
        # The work tree, .git/info/attributes and the merge-driver config are unchanged. The
        # migration's lock directory (.git/hacf/locks/, R22.1) is the one path it may create
        # before it refuses; specs.content leaves it out, as the rollback tests do.
        assert specs.content(specs.tree(project)) == specs.content(before), variant
        assert not (project / ".agents/state/migrations").exists()

        # Legacy mode works: the flat specs stay where they are and the v0.6.0 tools read them.
        resolve = run_tool(
            project, "hyper_spec.py", "resolve", "spec/compiled/MiniPRD_01_Invoices.md", "--json"
        )
        assert resolve.returncode == 0, (variant, resolve.stdout + resolve.stderr)
        assert json.loads(resolve.stdout)["path"] == "spec/compiled/MiniPRD_01_Invoices.md"
        for name in ("MiniPRD_01_Invoices.md", "MiniPRD_02_Payments.md"):
            # Findings in a legacy flat file are warnings (exit 0). `lint --all` is not used:
            # it would also lint the foreign root SPEC.md of collision-spec as a v6 file.
            lint = run_tool(project, "hyper_spec.py", "lint", f"spec/compiled/{name}")
            assert lint.returncode == 0, (variant, name, lint.stdout + lint.stderr)
        assert specs.content(specs.tree(project)) == specs.content(before), variant
