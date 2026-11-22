"""Tests for the engine's worktrees, git discipline, base copies and gc (S09a2; R1, R4, R20.6).

Every test is deterministic and runs under both interpreters on throwaway repositories under
tmp_path, with the S08 stub executor (HACF_TEST=1, R41) or a test-local fake executor.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import engine_support as es
import hyper_engine as he
import pytest
from conftest import GitRepo


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    es.engine_env(monkeypatch, tmp_path)


def _worktrees(repo: GitRepo) -> dict[str, str]:
    """Return {branch: path} of the repository's linked worktrees."""
    found, path = {}, None
    for line in repo.git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch refs/heads/"):
            found[line[len("branch refs/heads/") :]] = path
    return found


def test_worktree_lifecycle(tmp_git_repo: GitRepo, run_script) -> None:
    """Each slice gets swimlane/hacf/<run>/<slice> in its own worktree from the integration tip.

    Success removes the worktree; failure keeps it.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    probes = es.make_probes({"MiniPRD_02_Beta": "scope_violation"})
    assert es.run(repo, probes) == 1
    data = es.state(repo)
    run_id = data["run_id"]
    alpha, beta = data["slices"]["MiniPRD_01_Alpha"], data["slices"]["MiniPRD_02_Beta"]
    integration = f"swimlane/hacf/{run_id}/integration"
    assert alpha["branch"] == f"swimlane/hacf/{run_id}/MiniPRD_01_Alpha"
    assert beta["branch"] == f"swimlane/hacf/{run_id}/MiniPRD_02_Beta"
    assert (alpha["status"], beta["status"]) == ("audited", "blocked")
    assert beta["failure_type"] == "scope_violation"

    worktrees = _worktrees(repo)
    root = repo.scratch / "worktrees"
    assert Path(worktrees[integration]).is_relative_to(root)  # never inside the main checkout
    assert alpha["branch"] not in worktrees and not Path(alpha["worktree"]).exists()
    assert worktrees[beta["branch"]] == beta["worktree"] and beta["worktree_kept"] is True
    assert Path(beta["worktree"]).is_relative_to(root / os.listdir(root)[0] / run_id)

    # Beta started from the integration tip that already held Alpha's merge.
    fork = repo.git("merge-base", beta["branch"], integration).strip()
    assert fork == alpha["integration_commit"] == repo.git("rev-parse", integration).strip()
    message = repo.git("log", "-1", "--format=%B", alpha["commit"])
    for trailer in (f"HACF-Run: {run_id}", "HACF-Slice: MiniPRD_01_Alpha", "HACF-Executor: stub"):
        assert trailer in message
    assert "HACF-Session: stub-success" in message
    assert repo.git("rev-parse", alpha["candidate_ref"]).strip() == alpha["commit"]
    assert [p["phase"] for p in alpha["phases"]] == ["execute", "audit"]
    assert alpha["phases"][0]["session_id"] == "stub-success"


def _checkout(repo: GitRepo) -> tuple:
    """Return the main checkout's index bytes, files (tracked and untracked), HEAD and status."""
    index = (repo.path / ".git" / "index").read_bytes()  # first: status may refresh it
    names = repo.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    files = {name: (repo.path / name).read_bytes() for name in names if name}
    head = (repo.path / ".git" / "HEAD").read_bytes()
    return index, files, head, repo.git("status", "--porcelain", "--untracked-files=all")


def test_main_checkout_byte_identical(tmp_git_repo: GitRepo, run_script) -> None:
    """A full stub run that ends before land leaves the main checkout byte-identical."""
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    repo.git("status")  # settle the index's racy entries before the snapshot
    before, refs = _checkout(repo), es.refs(repo)
    assert es.run(repo, es.make_probes()) == 0
    assert _checkout(repo) == before
    after = es.refs(repo)
    assert {name: after[name] for name in refs} == refs  # no existing ref moved
    for name in set(after) - set(refs):
        assert name.startswith(("refs/heads/swimlane/hacf/", "refs/hacf/candidates/")), name
    assert before[3] == "?? .agents/local/hacf.yml\n"  # .agents/state/ ignores itself


def test_engine_git_uses_explicit_git_dir(tmp_git_repo: GitRepo, run_script, capsys) -> None:
    """Every engine git call names GIT_DIR and GIT_WORK_TREE and disables repo hooks (R1, R3).

    The recording covers a run, a land and a gc.
    """
    repo = es.engine_repo(tmp_git_repo, run_script)
    calls = []

    def record(argv, *, cwd, env=None):
        if argv[0] == "git":
            calls.append((argv, env or {}))
        return he._run_argv(argv, cwd=cwd, env=env)

    probes = es.make_probes(run=record)
    assert es.run(repo, probes) == 0
    run_id = es.state(repo)["run_id"]
    repo_obj = he.discover(repo.path, probes)
    assert he.land(repo_obj, run_id, probes)[0] == 0
    he.gc(repo_obj, probes, older_than=0)
    hooks_read = ["git", "config", "--get", "core.hooksPath"]  # reads the repo's own setting
    commands = {argv[7] for argv, _ in calls if argv != hooks_read}
    assert {"rev-parse", "worktree", "commit", "merge", "update-ref", "for-each-ref"} <= commands
    for argv, env in calls:
        assert env.get("GIT_DIR") and env.get("GIT_WORK_TREE"), argv
        assert Path(env["GIT_DIR"]).is_absolute() and Path(env["GIT_WORK_TREE"]).is_absolute()
        if argv != hooks_read:
            assert argv[1] == "-c" and argv[2].startswith("core.hooksPath="), argv
    main = [env for argv, env in calls if env["GIT_WORK_TREE"] == str(repo.path)]
    assert main and all(env["GIT_DIR"] == str(repo.path / ".git") for env in main)
    capsys.readouterr()


AUDIT_PROMPT_EXECUTOR = (
    "if phase == 'execute':\n"
    "    Path('AGENTS.md').write_text('edited by the executor\\n')\n"
    "    Path('.agents/skills/hyper-audit/SKILL.md').write_text('edited by the executor\\n')\n"
    + es.AUDIT_PASS
)


def test_audit_reads_base_revision_copies(tmp_git_repo: GitRepo, run_script, tmp_path) -> None:
    """The audit prompt and settings point at the read-only base/ copies (R4).

    The executor edits AGENTS.md and the audit SKILL.md in its worktree; base/ stays unchanged.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, names=())
    originals = {
        "AGENTS.md": "# Agents\n",
        ".agents/skills/hyper-audit/SKILL.md": "# audit skill\n",
        ".agents/skills/hyper-execute/SKILL.md": "# execute skill\n",
        ".agents/scripts/hyper_gate_hook.py": "print('base hook')\n",
    }
    for rel, text in originals.items():
        repo.write(rel, text)
    repo.commit("control files", *originals)
    touches = ["AGENTS.md", ".agents/skills/**"]
    (sid,) = es.v6_mission(repo, run_script, ("01-a", touches))
    script = es.fake_script(tmp_path, AUDIT_PROMPT_EXECUTOR)
    assert es.run(repo, es.make_probes(executor=es.fake_executor(script))) == 0
    directory = es.run_dirs(repo)[-1]
    base = directory / "base"
    for rel, text in originals.items():
        assert (base / rel).read_text() == text, rel
        assert stat.S_IMODE((base / rel).stat().st_mode) == 0o444, rel
    audit = directory / "phases" / f"{sid}.1.audit"
    argv = json.loads((audit / "claude_argv.json").read_text())
    prompt = argv[argv.index("-p") + 1]
    assert f"{base}/AGENTS.md" in prompt
    assert f"{base}/.agents/skills/hyper-audit/SKILL.md" in prompt
    settings = json.loads((audit / "settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert hook == f"python3 {base}/.agents/scripts/hyper_gate_hook.py"
    assert argv[argv.index("--setting-sources") + 1] == "user"
    deny = settings["sandbox"]["filesystem"]["denyWrite"]
    worktree = es.slice_record(repo, sid)["worktree"]
    assert f"{worktree}/.git" in deny and str(repo.path / ".git") in deny
    integration = f"swimlane/hacf/{es.state(repo)['run_id']}/integration"
    assert repo.git("show", f"{integration}:AGENTS.md") == "edited by the executor\n"


def test_dirty_worktree_after_audit_refused(tmp_git_repo: GitRepo, run_script) -> None:
    """An audit that leaves a file behind is refused; the outbox alone is not dirty (R4)."""
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    probes = es.make_probes({"MiniPRD_01_Alpha": "dirty_after_audit"})
    assert es.run(repo, probes) == 1
    alpha = es.slice_record(repo)
    assert (alpha["status"], alpha["failure_type"]) == ("blocked", "scope_violation")
    found = [e for e in es.events(repo) if e["event"] == "dirty_after_audit"]
    assert found and found[0]["detail"]["paths"] == ["stub_audit_scratch.txt"]
    assert Path(alpha["worktree"], "stub_audit_scratch.txt").is_file()  # kept as evidence
    assert es.slice_record(repo, "MiniPRD_02_Beta")["status"] == "upstream_blocked"

    # A passing audit writes only its outbox audit.json, which the engine moves out first.
    assert es.run(repo, es.make_probes()) == 0
    outbox = es.run_dirs(repo)[-1] / "outbox" / "MiniPRD_01_Alpha" / "audit" / "audit.json"
    assert json.loads(outbox.read_text())["verdict"] == "PASS"


def test_gc_lists_then_applies_and_skips_running(
    tmp_git_repo: GitRepo, run_script, monkeypatch
) -> None:
    """Garbage collection lists, then with --apply removes, a landed run's leftovers (R20.6).

    A run holding engine.lock is never listed or touched.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, names=("01_Alpha", "02_Beta"))
    probes = es.make_probes({"MiniPRD_02_Beta": "scope_violation"})
    assert es.run(repo, probes) == 1
    landed = es.state(repo)
    main = he.discover(repo.path, probes)
    assert he.land(main, landed["run_id"], probes)[0] == 0
    # A second run that ends without landing (abandoned), which then "holds" engine.lock.
    assert es.run(repo, es.make_probes({"MiniPRD_01_Alpha": "scope_violation"})) == 1
    running = es.state(repo)
    assert running["status"] == "finished" and "landed_at" not in running
    assert he.gc(main, probes)["runs"] == []  # both runs are younger than 14 days

    common = repo.path / ".git"
    with he.engine_lock(common, running["run_id"]):  # this run counts as still running
        listed = he.gc(main, probes, older_than=0)
    assert [item["run_id"] for item in listed["runs"]] == [landed["run_id"]]
    item = listed["runs"][0]
    beta = landed["slices"]["MiniPRD_02_Beta"]
    integration = str(Path(beta["worktree"]).parent / "integration")
    assert item["reason"] == "landed"
    assert sorted(item["worktrees"]) == sorted([beta["worktree"], integration])
    prefix = f"refs/heads/swimlane/hacf/{landed['run_id']}/"
    assert sorted(item["branches"]) == sorted(
        prefix + name for name in ("MiniPRD_01_Alpha", "MiniPRD_02_Beta", "integration")
    )
    assert item["refs"] == ["refs/hacf/candidates/MiniPRD_01_Alpha"]
    assert Path(beta["worktree"]).is_dir() and Path(item["run_dir"]).is_dir()  # listing only

    with he.engine_lock(common, running["run_id"]):
        applied = he.gc(main, probes, older_than=0, apply=True)
    assert [entry["run_id"] for entry in applied["runs"]] == [landed["run_id"]]
    assert not Path(beta["worktree"]).exists() and not Path(item["run_dir"]).exists()
    remaining = es.refs(repo)
    assert not [name for name in remaining if name.startswith(prefix)]
    assert "refs/hacf/candidates/MiniPRD_01_Alpha" not in remaining
    kept = running["slices"]["MiniPRD_01_Alpha"]
    assert f"refs/heads/{kept['branch']}" in remaining and Path(kept["worktree"]).is_dir()
    assert (es.run_dirs(repo)[-1] / "run.json").is_file()

    # The CLI: unlocked, the abandoned run is listed now; nothing is removed without --apply.
    monkeypatch.chdir(repo.path)
    assert he.main(["gc", "--older-than", "0d"], probes=probes) == 0
    assert Path(kept["worktree"]).is_dir()
