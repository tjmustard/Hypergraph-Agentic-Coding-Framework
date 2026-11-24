"""Tests for the thin engine wrappers and the retirement of hyper_fix (S09b).

hyper_orchestrator.py and hyper_daemon.py run in-process against throwaway repositories under
tmp_path with the S08 stub executor (HACF_TEST=1, R41); neither ever runs against this
repository. The ledger sequence and the lore check run on temp copies. Deterministic, both
interpreters.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import engine_support as es
import hyper_daemon
import hyper_orchestrator
import hyper_resolve_conflict
import pytest
import semantic_graph_merger
import yaml
from conftest import REPO_ROOT, SCRIPTS_DIR, GitRepo

WRAPPERS = ("hyper_orchestrator.py", "hyper_daemon.py")
CONFLICT_TOOLING = (
    "hyper_orchestrator.py",
    "hyper_daemon.py",
    "hyper_resolve_conflict.py",
    "semantic_graph_merger.py",
)
CHAIN = ("01_Alpha", "02_Beta", "03_Gamma")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    es.engine_env(monkeypatch, tmp_path)


def _tree_digest(repo: GitRepo) -> dict[str, str]:
    """Return {path: sha256} for every file of the main checkout outside .git."""
    found = {}
    for path in sorted(repo.path.rglob("*")):
        rel = path.relative_to(repo.path).as_posix()
        if rel == ".git" or rel.startswith(".git/") or not path.is_file():
            continue
        found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _engine_traces(repo: GitRepo) -> list[str]:
    """Return what an engine run leaves: run dirs, locks, extra worktrees, swimlane branches."""
    traces = []  # hyper_ledger.py itself makes .agents/state/ and .git/hacf/locks/ledger.lock
    if (repo.path / ".agents" / "state" / "runs").exists():
        traces.append(".agents/state/runs")
    if (repo.path / ".git" / "hacf" / "locks" / "engine.lock").exists():
        traces.append("engine.lock")
    worktrees = repo.git("worktree", "list", "--porcelain").count("worktree ")
    if worktrees != 1:
        traces.append(f"{worktrees} worktrees")
    traces += [ref for ref in es.refs(repo) if ref.startswith("refs/heads/swimlane/")]
    return traces


# ---------------------------------------------------------------------------
# hyper_orchestrator.py
# ---------------------------------------------------------------------------


def test_orchestrator_dry_run(tmp_git_repo: GitRepo, run_script, capsys) -> None:
    """--dry-run prints the chain in DAG order and the engine command; it writes nothing."""
    repo = es.engine_repo(tmp_git_repo, run_script, names=tuple(reversed(CHAIN)))
    before = _tree_digest(repo)
    compiled = repo.path / "spec" / "compiled"
    code = hyper_orchestrator.main([str(compiled), "--dry-run", "--max-workers", "12"])
    out, err = capsys.readouterr()
    assert code == 0, err
    order = [out.index(f"MiniPRD_{name}") for name in CHAIN]
    assert order == sorted(order)
    assert "hyper_engine.py run" in out
    assert "max_parallel: 8" in out and "capped at 8" in err
    assert _engine_traces(repo) == []
    assert _tree_digest(repo) == before


def test_orchestrator_removed_max_api_calls(tmp_git_repo: GitRepo, run_script, capsys) -> None:
    """--max-api-calls exits 2 and names --max-budget-usd-per-run."""
    repo = es.engine_repo(tmp_git_repo, run_script)
    target = str(repo.path / "spec" / "compiled")
    assert hyper_orchestrator.main([target, "--max-api-calls", "5"]) == 2
    assert "--max-budget-usd-per-run" in capsys.readouterr().err
    assert _engine_traces(repo) == []


def test_orchestrator_run_records_blocked_slice(tmp_git_repo: GitRepo, run_script) -> None:
    """A run through the engine; the blocked slice gets one FAILED_WORKFLOWS entry."""
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    probes = es.make_probes({"MiniPRD_02_Beta": "scope_violation"})
    target = str(repo.path / "spec" / "compiled")
    argv = [target, "--executor", "stub", "--unverified-executor", "--max-workers", "1"]
    assert hyper_orchestrator.main(argv, probes=probes) == 1
    data = es.state(repo)
    statuses = {sid: record["status"] for sid, record in data["slices"].items()}
    assert statuses == {"MiniPRD_01_Alpha": "audited", "MiniPRD_02_Beta": "blocked"}
    log = (repo.path / hyper_orchestrator.FAILED_WORKFLOWS_REL).read_text(encoding="utf-8")
    assert log.count("## [FAILED]") == 1
    assert "## [FAILED] MiniPRD_02_Beta" in log and "scope_violation" in log
    assert f"**Run:** {data['run_id']}" in log
    assert "spec/compiled/MiniPRD_02_Beta.md" in log


# ---------------------------------------------------------------------------
# hyper_daemon.py
# ---------------------------------------------------------------------------


def test_daemon_stub(tmp_git_repo: GitRepo, run_script, capsys) -> None:
    """The daemon runs exactly one slice through the engine; the old form exits 2."""
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    alpha = repo.path / "spec" / "compiled" / "MiniPRD_01_Alpha.md"
    beta = repo.path / "spec" / "compiled" / "MiniPRD_02_Beta.md"
    before = _tree_digest(repo)
    head = repo.git("rev-parse", "HEAD")
    status = repo.git("status", "--porcelain")  # the untracked local config

    for old in (["Super", str(alpha)], ["--orchestrated", "Super", str(alpha)]):
        assert hyper_daemon.main(old) == 2
        assert "usage: hyper_daemon.py" in capsys.readouterr().err
    assert hyper_daemon.main([str(beta)]) == 2  # its dependency is not audited
    assert "MiniPRD_01_Alpha" in capsys.readouterr().err
    assert _engine_traces(repo) == []

    argv = [str(alpha), "--executor", "stub", "--unverified-executor"]
    assert hyper_daemon.main(argv, probes=es.make_probes()) == 0
    data = es.state(repo)
    assert list(data["slices"]) == ["MiniPRD_01_Alpha"]
    assert data["slices"]["MiniPRD_01_Alpha"]["status"] == "audited"
    after = {k: v for k, v in _tree_digest(repo).items() if not k.startswith(".agents/state/")}
    assert after == before
    assert repo.git("rev-parse", "HEAD") == head
    assert repo.git("status", "--porcelain") == status


# ---------------------------------------------------------------------------
# Retiring hyper_fix
# ---------------------------------------------------------------------------


def _scanned_files() -> list[Path]:
    """Return the files test_no_refs_to_hyper_fix_in_code scans (Clarifying question 6)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         ".agents", ".claude", ".windsurf", "HACF-install.sh", "HACF-uninstall.sh"],
        cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout  # fmt: skip
    files = []
    for rel in filter(None, out.decode().split("\0")):
        path = REPO_ROOT / rel
        if rel.startswith(".agents/memory/") or "__pycache__" in rel or not path.is_file():
            continue
        # v0.5.x deletion data for the S12c uninstaller, not a code reference; R44.
        if rel == ".agents/config/legacy_file_hashes.json":
            continue
        files.append(path)
    return files


def test_no_refs_to_hyper_fix_in_code() -> None:
    """No code, skill, config or installer names hyper_fix (docs are S15a2's)."""
    files = _scanned_files()
    assert any(p.name == "hyper_orchestrator.py" for p in files)
    found = [p.relative_to(REPO_ROOT).as_posix() for p in files if b"hyper_fix" in p.read_bytes()]
    assert found == []
    assert not (SCRIPTS_DIR / "hyper_fix.py").exists()
    assert not (REPO_ROOT / ".provenance_staging").exists()


def _ledger(repo: GitRepo, run_script, *args: str) -> subprocess.CompletedProcess:
    return run_script(".agents/scripts/hyper_ledger.py", *args, cwd=repo.path, env=repo.env)


def _ledger_with_hyper_fix(repo: GitRepo, run_script) -> None:
    """Commit a temp ledger where hyper_daemon depends on hyper_fix."""
    for args in (
        ("init",),
        ("ensure-node", "--id", "orchestration_layer", "--dimension", "Layer"),
        ("ensure-node", "--id", "hyper_fix", "--dimension", "Module",
         "--member-of", "orchestration_layer"),
        ("ensure-node", "--id", "hyper_daemon", "--dimension", "Module",
         "--member-of", "orchestration_layer", "--depends-on", "hyper_fix"),
    ):  # fmt: skip
        proc = _ledger(repo, run_script, *args)
        assert proc.returncode == 0, proc.stderr
    repo.commit("temp ledger")


def _tombstones(repo: GitRepo) -> list[dict]:
    text = (repo.path / "spec" / "compiled" / "architecture.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text).get("removed_nodes") or []


def test_remove_node_sequence(tmp_git_repo: GitRepo, run_script) -> None:
    """remove-node is refused while hyper_daemon depends on hyper_fix; in order it tombstones."""
    repo = tmp_git_repo
    _ledger_with_hyper_fix(repo, run_script)
    head = repo.git("rev-parse", "HEAD").strip()
    early = _ledger(repo, run_script, "remove-node", "hyper_fix", "--reason", "D8")
    assert early.returncode == 5
    assert "hyper_daemon" in early.stdout + early.stderr
    assert _tombstones(repo) == []
    proc = _ledger(repo, run_script, "set-edges", "--id", "hyper_daemon",
                   "--remove-depends-on", "hyper_fix")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    proc = _ledger(repo, run_script, "remove-node", "hyper_fix", "--reason", "D8",
                   "--slice", "S09b")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert _tombstones(repo) == [{"id": "hyper_fix", "reason": "D8", "slice": "S09b",
                                  "commit": head}]  # fmt: skip


# ---------------------------------------------------------------------------
# Extra checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", WRAPPERS)
def test_wrappers_no_anthropic_import(name: str) -> None:
    """Neither wrapper imports anthropic or hyper_resolve_conflict."""
    tree = ast.parse((SCRIPTS_DIR / name).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "hyper_engine" in imported
    assert not imported & {"anthropic", "hyper_resolve_conflict", "hyper_fix"}


@pytest.mark.parametrize("name", CONFLICT_TOOLING)
def test_no_stale_model_ids_in_conflict_tooling(name: str) -> None:
    """No retired model id is left in the four scripts this slice modifies."""
    text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    stale = re.findall(r"claude-sonnet-4-6|claude-opus-4-[\w-]*|claude-haiku-4-5[\w-]*", text)
    assert stale == []


def test_resolve_conflict_model_from_config(monkeypatch, tmp_path: Path, capsys) -> None:
    """models.sonnet is the default; HYPER_RESOLVE_MODEL overrides; a missing file falls back."""
    config = tmp_path / "orchestration.yml"
    config.write_text("models:\n  sonnet: claude-sonnet-test\n", encoding="utf-8")
    monkeypatch.delenv("HYPER_RESOLVE_MODEL", raising=False)
    monkeypatch.setattr(hyper_resolve_conflict, "CONFIG_PATH", config)
    assert hyper_resolve_conflict.default_model() == "claude-sonnet-test"
    monkeypatch.setenv("HYPER_RESOLVE_MODEL", "claude-env")
    assert hyper_resolve_conflict.default_model() == "claude-env"
    monkeypatch.delenv("HYPER_RESOLVE_MODEL")
    monkeypatch.setattr(hyper_resolve_conflict, "CONFIG_PATH", tmp_path / "missing.yml")
    assert hyper_resolve_conflict.default_model() == "claude-sonnet-5-5"
    assert "run HACF-install.sh to complete the upgrade" in capsys.readouterr().err
    assert hyper_resolve_conflict._parse_args(["f.py", "--model", "m"]).model == "m"


def _rotation_repo(tmp_git_repo: GitRepo) -> tuple[Path, Path]:
    """Return (archive_specs.py copy, FAILED_WORKFLOWS.md with 50 entries) in a temp repo."""
    scripts = tmp_git_repo.path / ".agents" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("archive_specs.py", "_runtime.py"):
        shutil.copyfile(SCRIPTS_DIR / name, scripts / name)
    entries = "".join(f"\n## [FAILED] old-{i} — 2026-01-01T00:00:00Z\n\n---\n" for i in range(50))
    log = tmp_git_repo.write("spec/active/FAILED_WORKFLOWS.md", "# Failed Workflows\n" + entries)
    return scripts / "archive_specs.py", log


def _append_with(writer: str, monkeypatch, archive: Path, log: Path) -> None:
    """Append the 51st entry through one of the three FAILED_WORKFLOWS writers."""
    if writer == "orchestrator":
        top = log.parents[2]
        hyper_orchestrator.append_failed_workflow(top, "\n## [FAILED] new — now\n", archive)
    elif writer == "merger":
        monkeypatch.setattr(semantic_graph_merger, "FAILED_WORKFLOWS_PATH", str(log))
        monkeypatch.setattr(semantic_graph_merger, "ARCHIVE_SCRIPT", str(archive))
        semantic_graph_merger._append_failed_workflow("## [FAILED] new — now\n")
    else:
        monkeypatch.setattr(hyper_resolve_conflict, "FAILED_WORKFLOWS_PATH", log)
        monkeypatch.setattr(hyper_resolve_conflict, "ARCHIVE_SCRIPT", archive)
        hyper_resolve_conflict._append_failed_workflow("new", "S", "m.md", "LOW_CONFIDENCE", 1)


@pytest.mark.parametrize("writer", ["orchestrator", "merger", "resolve_conflict"])
def test_failed_workflows_rotation_uses_archive_specs(
    writer: str, tmp_git_repo: GitRepo, monkeypatch
) -> None:
    """The 51st entry rotates through archive_specs.py --paths, then a fresh header follows."""
    archive, log = _rotation_repo(tmp_git_repo)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        calls.append([str(a) for a in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    _append_with(writer, monkeypatch, archive, log)
    assert len(calls) == 1
    assert calls[0][1:3] == [str(archive), "--paths"]
    assert calls[0][-2:] == ["--label", "failed_workflows"]
    archived = [p for p in (tmp_git_repo.path / "spec" / "archive").rglob("*") if p.is_file()]
    assert len(archived) == 1
    assert archived[0].name == "FAILED_WORKFLOWS.md"
    assert archived[0].parent.name.endswith("_failed_workflows")
    assert archived[0].read_text(encoding="utf-8").count("## [FAILED]") == 50
    fresh = log.read_text(encoding="utf-8")
    assert fresh.startswith("# Failed Workflows\n")
    assert fresh.count("## [FAILED]") == 1 and "new" in fresh
