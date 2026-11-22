"""Tests for the staged ledger, land, --rebase-ledger and resume (S09a2; R5, R42).

Deterministic, both interpreters, throwaway repositories under tmp_path. Most use a gitignored
ledger, the case the run stages in .agents/state/runs/<id>/ledger/ until `land`. The fake
executor records ledger writes the way an agent phase does: through hyper_ledger.py with
HACF_OUTBOX set.
"""

from __future__ import annotations

import json
from pathlib import Path

import engine_support as es
import hyper_engine as he
import hyper_ledger
import pytest
from conftest import GitRepo

LEDGER_CLI = (
    f"import subprocess\nledger = [sys.executable, {str(he.SCRIPTS / 'hyper_ledger.py')!r}]\n"
)
READ_ONLY_COPY = (
    "    copy = Path('spec/compiled/architecture.yml')\n"
    "    assert copy.is_file() and not os.access(copy, os.W_OK), 'no read-only ledger copy'\n"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    es.engine_env(monkeypatch, tmp_path)


def _ledger_run(repo: GitRepo, tmp_path: Path, name: str, *args: str) -> int:
    """Run one slice whose execute phase records `hyper_ledger.py <args>` in the outbox."""
    body = LEDGER_CLI + (
        "if phase == 'execute':\n"
        + READ_ONLY_COPY
        + f"    Path('{name}.txt').write_text('a change\\n')\n"
        + f"    subprocess.run(ledger + {list(args)!r}, check=True)\n"
        + es.AUDIT_PASS
    )
    script = es.fake_script(tmp_path, body, f"{name}.py")
    return es.run(repo, es.make_probes(executor=es.fake_executor(script)))


def _nodes(path: Path) -> dict:
    return hyper_ledger.node_index(hyper_ledger.load_ledger(path)[1])


def _land(repo: GitRepo, **kwargs) -> tuple[int, dict]:
    probes = es.make_probes()
    return he.land(he.discover(repo.path, probes), es.state(repo)["run_id"], probes, **kwargs)


def test_untracked_ledger_staged_until_land(tmp_git_repo: GitRepo, run_script, tmp_path) -> None:
    """A gitignored ledger is staged in the run dir until land applies it.

    The main checkout's ledger stays byte-identical until land applies the staged one with
    validation; --merge then fast-forwards the base branch.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, tracked_ledger=False)
    ledger = repo.path / "spec" / "compiled" / "architecture.yml"
    before = ledger.read_bytes()
    args = ("ensure-node", "--id", "node_new", "--dimension", "Module", "--files", "new.txt")
    assert _ledger_run(repo, tmp_path, "new", *args) == 0
    directory = es.run_dirs(repo)[-1]
    staged = directory / "ledger" / "architecture.yml"
    assert "node_new" in _nodes(staged) and ledger.read_bytes() == before
    assert (directory / "ledger" / "base.yml").read_bytes() == before
    run_id = es.state(repo)["run_id"]
    integration = f"swimlane/hacf/{run_id}/integration"
    assert "spec/compiled/architecture.yml" not in repo.git(
        "ls-tree", "-r", "--name-only", integration
    )

    code, report = _land(repo, merge=True)
    assert code == 0 and report["ok"] and report["ledger"] == "applied" and report["merged"]
    assert ledger.read_bytes() == staged.read_bytes() and "node_new" in _nodes(ledger)
    es.ledger_cli(repo, run_script, "validate")
    assert repo.git("rev-parse", "HEAD") == repo.git("rev-parse", integration)
    assert (repo.path / "new.txt").is_file()
    assert es.state(repo)["landed_at"]


def test_land_refuses_ledger_drift(
    tmp_git_repo: GitRepo, run_script, tmp_path, monkeypatch, capsys
) -> None:
    """The main ledger changes after preflight: land exits 5 with a report, writes nothing."""
    repo = es.engine_repo(tmp_git_repo, run_script, tracked_ledger=False)
    args = ("ensure-node", "--id", "node_new", "--dimension", "Module")
    assert _ledger_run(repo, tmp_path, "new", *args) == 0
    es.ledger_cli(repo, run_script, "ensure-node", "--id", "manual_node", "--dimension", "Module")
    ledger = repo.path / "spec" / "compiled" / "architecture.yml"
    drifted, head = ledger.read_bytes(), repo.git("rev-parse", "HEAD")
    code, report = _land(repo, merge=True)
    assert code == 5 and not report["ok"] and "--rebase-ledger" in report["message"]
    assert report["ledger_sha256"] != report["base_ledger_sha256"]
    assert ledger.read_bytes() == drifted and repo.git("rev-parse", "HEAD") == head
    assert "landed_at" not in es.state(repo)

    capsys.readouterr()
    monkeypatch.chdir(repo.path)
    assert he.main(["land", es.state(repo)["run_id"]], probes=es.make_probes()) == 5
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert ledger.read_bytes() == drifted


def test_land_rebase_ledger_replays_outbox(tmp_git_repo: GitRepo, run_script, tmp_path) -> None:
    """Land --rebase-ledger replays the run's ledger entries onto a changed ledger (R5).

    A manual change to an untouched node survives; a manual change to a node the run changed
    is a conflict, exit 5, with a per-node report.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, tracked_ledger=False)
    for node in ("node_a", "node_b", "node_c"):
        es.ledger_cli(repo, run_script, "ensure-node", "--id", node, "--dimension", "Module")
    ledger = repo.path / "spec" / "compiled" / "architecture.yml"
    args = ("set-edges", "--id", "node_a", "--add-depends-on", "node_b")
    assert _ledger_run(repo, tmp_path, "one", *args) == 0
    es.ledger_cli(repo, run_script, "ensure-node", "--id", "node_b", "--files", "b.py")
    code, report = _land(repo, rebase=True)
    assert code == 0 and report["ledger"] == "replayed"
    nodes = _nodes(ledger)
    assert nodes["node_a"]["edges"]["depends_on"] == ["node_b"]  # the run's change
    assert nodes["node_b"]["files"] == ["b.py"]  # the manual change survives
    es.ledger_cli(repo, run_script, "validate")

    args = ("set-edges", "--id", "node_b", "--add-depends-on", "node_c")
    assert _ledger_run(repo, tmp_path, "two", *args) == 0
    es.ledger_cli(repo, run_script, "ensure-node", "--id", "node_b", "--files", "c.py")
    before = ledger.read_bytes()
    code, report = _land(repo, rebase=True)
    assert code == 5 and report["conflicts"] == [
        "conflict: node node_b differs between the base file and the ledger "
        "(000001-set-edges.json, set-edges)"
    ]
    assert ledger.read_bytes() == before and "landed_at" not in es.state(repo)


def test_resume_refuses_ledger_drift(
    tmp_git_repo: GitRepo, run_script, monkeypatch, capsys
) -> None:
    """The main ledger changes, then --resume <run_id>: exit 5 with a report (R5)."""
    repo = es.engine_repo(tmp_git_repo, run_script, tracked_ledger=False)
    assert es.run(repo, es.make_probes({"MiniPRD_01_Alpha": "scope_violation"})) == 1
    first = es.state(repo)
    run_id = first["run_id"]
    es.ledger_cli(repo, run_script, "ensure-node", "--id", "manual_node", "--dimension", "Module")
    capsys.readouterr()
    monkeypatch.chdir(repo.path)
    argv = ["run", "--resume", run_id, "--executor", "stub", "--unverified-executor"]
    assert he.main(argv, probes=es.make_probes()) == 5
    failed = json.loads(capsys.readouterr().out)["checks"][-1]
    assert failed["name"] == "ledger_drift" and "--resume refuses (R5)" in failed["message"]
    data = es.state(repo)
    assert "resumed" not in data and data["slices"] == first["slices"]


def test_resume_records_security_values(tmp_git_repo: GitRepo, run_script) -> None:
    """A resume records its own security values in resumed[] (R42).

    The resume lowers the run budget; the top-level security stays the first run's.
    """
    repo = es.engine_repo(tmp_git_repo, run_script)
    assert es.run(repo, es.make_probes({"MiniPRD_01_Alpha": "scope_violation"})) == 1
    first = es.state(repo)
    resumed = es.run(repo, es.make_probes(), first["run_id"], max_budget_usd_per_run=20.0)
    assert resumed == 0
    after = es.state(repo)
    assert after["run_id"] == first["run_id"] and after["security"] == first["security"]
    assert first["security"]["autonomy.max_budget_usd_per_run"] == 50.0
    (entry,) = after["resumed"]
    flags = {"executor": "stub", "unverified_executor": True, "max_budget_usd_per_run": 20.0}
    assert entry["security"] == he.security_values(he.resolve_config(repo.path, flags)[0])
    assert entry["security"]["autonomy.max_budget_usd_per_run"] == 20.0
    alpha = after["slices"]["MiniPRD_01_Alpha"]
    assert (alpha["status"], alpha["attempts"]) == ("audited", 2)  # the kept worktree was reset
