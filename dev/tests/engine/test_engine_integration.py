"""Tests for engine commits and hooks, the oracle sandbox, retries and the integrator (S09a2).

Covered: R2, R3 and R40 (bwrap profiles, uv without a cache), oracle retry, merge conflicts and
ledger rollback. Deterministic, both interpreters, throwaway repositories under tmp_path. The
generated bwrap argv is always checked; running inside bwrap (and uv there) happens only where
`bwrap --ro-bind / / true` works, else that part is skipped with the reason printed
(Clarifying question 5).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import engine_support as es
import hyper_engine as he
import hyper_executors as hx
import pytest
import yaml
from conftest import REPO_ROOT, GitRepo, clean_env

BWRAP = es.bwrap_ok()
LEDGER_CLI = (
    f"import subprocess\nledger = [sys.executable, {str(he.SCRIPTS / 'hyper_ledger.py')!r}]\n"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    es.engine_env(monkeypatch, tmp_path)


def _unless_bwrap(flags: dict) -> dict:
    """Run oracles unsandboxed where bwrap cannot, for tests that are not about the sandbox."""
    if not BWRAP:
        print(es.SKIP_BWRAP)
        return {**flags, "allow_unsandboxed_oracle": True}
    return flags


def _sandboxed_runs(repo: GitRepo) -> list[list[str]]:
    return [e["detail"]["argv"] for e in es.events(repo) if e["event"] == "sandboxed_run"]


def _hook(repo: GitRepo, rel: str, text: str) -> None:
    path = repo.write(rel, text)
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# Engine commits and base-revision hooks (R3)
# ---------------------------------------------------------------------------

PRE_COMMIT = (
    '#!/bin/sh\ntest ! -e STUB_HOOK_REJECT || { echo "base hook: rejected" >&2; exit 1; }\n'
)


def test_engine_commit_hooks_disabled_and_base_hooks_sandboxed(
    tmp_git_repo: GitRepo, run_script, tmp_path
) -> None:
    """An executor's edit to the tracked pre-commit hook has no effect.

    `.githooks/` falls under the always-protected `.git*`, so the edit is refused before any
    commit. The base revision's copy runs in bwrap and rejects a change it refuses; git itself
    runs no repo hook (a post-commit marker never appears).
    """
    repo = es.engine_repo(tmp_git_repo, run_script, names=())
    marker = tmp_path / "post-commit-ran"
    _hook(repo, ".githooks/pre-commit", PRE_COMMIT)
    _hook(repo, ".githooks/post-commit", f"#!/bin/sh\ntouch {marker}\n")
    repo.write("spec/compiled/MiniPRD_01_Alpha.md", "# MiniPRD: 01_Alpha\n")
    repo.commit("hooks", ".githooks", "spec/compiled")
    repo.git("config", "core.hooksPath", ".githooks")  # after the test's own commit
    flags = _unless_bwrap({})
    edit = "if phase == 'execute':\n    Path('.githooks/pre-commit').write_text('exit 0\\n')\n"
    script = es.fake_script(tmp_path, edit, "edit.py")
    assert es.run(repo, es.make_probes(executor=es.fake_executor(script)), **flags) == 1
    record = es.slice_record(repo)
    assert record["failure_type"] == "scope_violation" and _sandboxed_runs(repo) == []
    assert ".githooks/pre-commit: always protected" in json.dumps(es.events(repo))

    reject = "if phase == 'execute':\n    Path('STUB_HOOK_REJECT').write_text('refused\\n')\n"
    script = es.fake_script(tmp_path, reject, "reject.py")
    assert es.run(repo, es.make_probes(executor=es.fake_executor(script)), **flags) == 1
    assert es.slice_record(repo)["failure_type"] == "commit_rejected"
    base_hook = es.run_dirs(repo)[-1] / "base" / "hooks" / "pre-commit"
    assert base_hook.read_text() == PRE_COMMIT and os.access(base_hook, os.X_OK)
    assert not (es.run_dirs(repo)[-1] / "base" / "hooks" / "post-commit").exists()
    (argv,) = _sandboxed_runs(repo)
    assert argv[-1] == str(base_hook)
    if BWRAP:
        assert argv[0] == "bwrap" and "--unshare-net" in argv
        tmpfs = [argv[i + 1] for i, part in enumerate(argv) if part == "--tmpfs"]
        assert tmpfs == ["/tmp", os.environ["HOME"]]
    assert "base hook: rejected" in json.dumps(es.events(repo))

    # A change the base hook accepts commits, and git ran no repo hook for it.
    assert es.run(repo, es.make_probes(), **flags) == 0
    assert es.slice_record(repo)["status"] == "audited" and not marker.exists()


def test_commit_rejected_by_hook(tmp_git_repo: GitRepo, run_script) -> None:
    """The stub's hook_reject scenario is commit_rejected by a base hook (R3).

    Untracked <common>/hooks run as copied at run start; commit-msg sees, and may edit, the
    engine's message.
    """
    repo = es.engine_repo(tmp_git_repo, run_script)
    repo.git("config", "--unset", "core.hooksPath")
    _hook(repo, ".git/hooks/pre-commit", PRE_COMMIT)
    commit_msg = (
        '#!/bin/sh\ngrep -q "^HACF-Run: " "$1" || exit 1\necho "Hook-Checked: yes" >> "$1"\n'
    )
    _hook(repo, ".git/hooks/commit-msg", commit_msg)
    flags = _unless_bwrap({})
    assert es.run(repo, es.make_probes(), **flags) == 0
    record = es.slice_record(repo)
    message = repo.git("log", "-1", "--format=%B", record["commit"])
    assert "HACF-Run: " in message and "Hook-Checked: yes" in message
    hooks_run = [
        argv[argv.index("--") + 1] if "--" in argv else argv[0] for argv in _sandboxed_runs(repo)
    ]
    assert [Path(hook).name for hook in hooks_run] == ["pre-commit", "commit-msg"]

    assert es.run(repo, es.make_probes({"MiniPRD_01_Alpha": "hook_reject"}), **flags) == 1
    record = es.slice_record(repo)
    assert (record["status"], record["failure_type"]) == ("blocked", "commit_rejected")
    assert "commit" not in record  # nothing was committed
    assert Path(record["worktree"], "STUB_HOOK_REJECT").is_file()


# ---------------------------------------------------------------------------
# Oracle sandbox (R2, R40)
# ---------------------------------------------------------------------------

PROBE = """
import os, socket, sys
from pathlib import Path
found = []
try:
    socket.create_connection(("192.0.2.1", 53), timeout=3)
    found.append("network")
except OSError:
    pass
if (Path(os.environ["HOME"]) / "planted.txt").exists():
    found.append("home")
try:
    Path(".git").write_text("x")
    found.append("git pointer")
except OSError:
    pass
Path("oracle_output.txt").write_text("the worktree is writable")
print("escaped:", found)
sys.exit(1 if found else 0)
"""


def test_oracle_runs_in_bwrap_without_network_or_home(
    tmp_git_repo: GitRepo, run_script, tmp_path, monkeypatch
) -> None:
    """The oracle profile: no network, a tmpfs $HOME, read-only .git, a writable worktree."""
    worktree, common = tmp_path / "wt", tmp_path / "repo.git"
    env = {"HOME": "/home/someone", "PATH": "/usr/bin:/bin"}
    argv = he.bwrap_argv(["python3", "-c", "pass"], worktree=worktree, common_dir=common, env=env)
    assert argv[:4] == ["bwrap", "--ro-bind", "/", "/"] and argv[-4:] == [
        "--",
        "python3",
        "-c",
        "pass",
    ]
    for flag in ("--unshare-net", "--die-with-parent"):
        assert flag in argv
    assert [argv[i + 1] for i, part in enumerate(argv) if part == "--tmpfs"] == [
        "/tmp",
        "/home/someone",
    ]
    pairs = list(zip(argv, argv[1:], argv[2:]))
    assert ("--bind", str(worktree), str(worktree)) in pairs
    assert ("--ro-bind", f"{worktree}/.git", f"{worktree}/.git") in pairs
    assert ("--ro-bind", str(common), str(common)) in pairs
    assert argv.index(f"{worktree}/.git") > argv.index(str(worktree))  # over the rw bind
    assert argv[argv.index("--chdir") + 1] == str(worktree)

    if not BWRAP:
        pytest.skip(es.SKIP_BWRAP)
    home = tmp_path / "home"
    home.mkdir()
    (home / "planted.txt").write_text("a secret in $HOME\n")
    monkeypatch.setenv("HOME", str(home))
    oracle = {"oracle": {"default": [["python3", "-c", PROBE]]}}
    repo = es.engine_repo(tmp_git_repo, run_script, local=oracle)
    assert es.run(repo, es.make_probes()) == 0
    (run,) = _sandboxed_runs(repo)
    assert run[0] == "bwrap"
    assert [run[i + 1] for i, part in enumerate(run) if part == "--tmpfs"] == ["/tmp", str(home)]
    assert [e["detail"]["exit"] for e in es.events(repo) if e["event"] == "oracle"] == [0]
    assert es.slice_record(repo)["status"] == "audited"


def test_oracle_none_requires_cli_flag(tmp_git_repo: GitRepo, run_script, capsys) -> None:
    """oracle.sandbox: none from config is ignored; only --allow-unsandboxed-oracle drops bwrap."""
    local = {"oracle": {"sandbox": "none", "default": [["python3", "-c", "pass"]]}}
    repo = es.engine_repo(tmp_git_repo, run_script, local=local)
    config, ignored = he.resolve_config(repo.path)
    assert config["oracle"]["sandbox"] == "bwrap"
    assert any("oracle.sandbox cannot loosen" in line for line in ignored)

    no_bwrap = es.make_probes()
    no_bwrap.which = lambda tool: None if tool == "bwrap" else f"/usr/bin/{tool}"
    assert es.run(repo, no_bwrap, allow_unsandboxed=True) == 5
    assert '"failure_type": "sandbox_unavailable"' in capsys.readouterr().out

    assert es.run(repo, es.make_probes(), allow_unsandboxed_oracle=True) == 0
    assert _sandboxed_runs(repo) == [["python3", "-c", "pass"]]
    assert es.state(repo)["security"]["oracle.sandbox"] == "none"
    if not BWRAP:
        pytest.skip(es.SKIP_BWRAP)
    assert es.run(repo, es.make_probes()) == 0
    (argv,) = _sandboxed_runs(repo)
    assert argv[0] == "bwrap" and argv[-3:] == ["python3", "-c", "pass"]


UV_PROBE = "import sys; print(sys.prefix)"


def test_oracle_uv_run_inside_bwrap(tmp_path: Path) -> None:
    """A `uv run --frozen --no-sync` oracle works in the profile without a uv cache (R40).

    The env has UV_NO_CACHE=1, nothing binds a uv cache, and uv's real path and the venv
    interpreter's prefix are bound read-only; where bwrap and uv work, the probe exits 0.
    """
    project = tmp_path / "project"
    project.mkdir()
    venv_home = tmp_path / "pythons" / "cpython-3.12-x" / "bin"
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text(f"home = {venv_home}\nversion = 3.12.0\n")
    environ = {"PATH": os.environ["PATH"], "HOME": "/home/someone", "TMPDIR": "/var/tmp/x"}
    env = he.oracle_env(environ, he.DEFAULTS["autonomy"], "oauth", sandboxed=True)
    assert env == {
        **{k: environ[k] for k in ("PATH", "HOME")},
        "TMPDIR": "/tmp",
        "UV_NO_CACHE": "1",
    }
    argv = ["uv", "run", "--frozen", "--no-sync", "python", "-c", UV_PROBE]
    wrapped = he.bwrap_argv(argv, worktree=project, common_dir=project / ".git", env=env)
    readonly = [wrapped[i + 1] for i, part in enumerate(wrapped) if part == "--ro-bind"]
    assert str(venv_home.parent) in readonly
    uv = shutil.which("uv", path=env["PATH"])
    if uv:
        assert os.path.realpath(uv) in readonly
    assert not [part for part in wrapped if ".cache/uv" in part or "uv/cache" in part]

    if not BWRAP or uv is None:
        pytest.skip(es.SKIP_BWRAP if not BWRAP else "uv is not on PATH")
    # A real temp project; uv's cache lives in tmp, so the user's uv cache is never written.
    shutil.rmtree(project / ".venv")
    subprocess.run(["git", "init", "-q", str(project)], check=True, env=clean_env())
    (project / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
        "dependencies = []\n\n[tool.uv]\npackage = false\n"
    )
    uv_env = clean_env(UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_PYTHON_DOWNLOADS="never")
    for step in (["uv", "lock", "--offline"], ["uv", "sync", "--frozen", "--offline"]):
        done = subprocess.run(step, cwd=project, env=uv_env, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    assert he._venv_home(project)
    env = he.oracle_env(dict(os.environ), he.DEFAULTS["autonomy"], "oauth", sandboxed=True)
    wrapped = he.bwrap_argv(argv, worktree=project, common_dir=project / ".git", env=env)
    completed = hx.run(wrapped, env=env, cwd=project, timeout=120, kill_grace_s=5)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(project / ".venv")


def test_agent_env_sets_uv_no_cache(tmp_git_repo: GitRepo, run_script, monkeypatch) -> None:
    """Agent phases get UV_NO_CACHE=1 from scrub_env with ctx, and only with ctx (R40).

    The shipped config's autonomy.env and the engine DEFAULTS name it too.
    """
    ctx = {"run_id": "r", "slice": "s", "outbox": "/wt/.hacf-outbox/"}
    autonomy = he.DEFAULTS["autonomy"]
    assert hx.scrub_env({"PATH": "/bin"}, autonomy, "oauth", ctx)["UV_NO_CACHE"] == "1"
    assert "UV_NO_CACHE" not in hx.scrub_env(
        {"PATH": "/bin", "UV_NO_CACHE": "0"}, autonomy, "oauth"
    )
    shipped = yaml.safe_load((REPO_ROOT / he.CONFIG_REL).read_text(encoding="utf-8"))
    assert shipped["autonomy"]["env"]["UV_NO_CACHE"] == "1" == autonomy["env"]["UV_NO_CACHE"]

    seen = []
    real = hx.run

    def record(argv, *, env, **kwargs):
        seen.append((argv, env))
        return real(argv, env=env, **kwargs)

    monkeypatch.setattr(hx, "run", record)
    repo = es.engine_repo(tmp_git_repo, run_script)
    assert es.run(repo, es.make_probes()) == 0
    phases = [env for argv, env in seen if "--prompt" in argv]
    assert len(phases) == 2 and all(env["UV_NO_CACHE"] == "1" for env in phases)
    assert all(env["HACF_AUTONOMOUS"] == "1" for env in phases)
    assert all(env["HACF_OUTBOX"].endswith("/.hacf-outbox/") for env in phases)
    assert "HACF_TEST" not in phases[0]  # the scrubbed env passes only the passthrough names


# ---------------------------------------------------------------------------
# Retries and the integrator
# ---------------------------------------------------------------------------

ORACLE_FAIL = "import pathlib, sys; sys.exit(pathlib.Path('test_stub_oracle_fail.py').exists())"


def test_oracle_fail_retry(tmp_git_repo: GitRepo, run_script, monkeypatch) -> None:
    """A failing oracle gets one re-execute, then the slice is blocked as oracle_failed.

    The re-execute carries the punch list and HACF_ALLOW_REIMPLEMENT=1.
    """
    seen = []
    real = hx.run
    monkeypatch.setattr(
        hx, "run", lambda argv, *, env, **kw: seen.append(env) or real(argv, env=env, **kw)
    )
    local = {"oracle": {"default": [["python3", "-c", ORACLE_FAIL]]}}
    repo = es.engine_repo(tmp_git_repo, run_script, local=local)
    flags = _unless_bwrap({})
    assert es.run(repo, es.make_probes({"MiniPRD_01_Alpha": "oracle_fail"}), **flags) == 1
    record = es.slice_record(repo)
    assert (record["status"], record["failure_type"]) == ("blocked", "oracle_failed")
    assert record["attempts"] == 2 and record["retries"] == {"oracle_failed": 1}
    assert [(p["attempt"], p["phase"]) for p in record["phases"]] == [
        (1, "execute"),
        (2, "execute"),
    ]
    directory = es.run_dirs(repo)[-1]
    assert "exited 1" in (directory / "punch" / "MiniPRD_01_Alpha.md").read_text()
    first = (directory / "phases" / "MiniPRD_01_Alpha.1.execute" / "autonomy.md").read_text()
    second = (directory / "phases" / "MiniPRD_01_Alpha.2.execute" / "autonomy.md").read_text()
    assert "punch list" not in first and "The previous attempt failed" in second
    executes = [env for env in seen if "HACF_SLICE" in env]
    assert [env.get("HACF_ALLOW_REIMPLEMENT") for env in executes] == [None, "1"]


BARRIER = """
import time
mark = Path({arrivals!r})
mark.mkdir(exist_ok=True)
if phase == 'execute':
    (mark / slice_id).write_text('here')
    deadline = time.monotonic() + 30
    while len(list(mark.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    Path('shared.txt').write_text('changed by ' + slice_id + '\\n')
"""


def test_merge_conflict_keeps_worktree(tmp_git_repo: GitRepo, run_script, tmp_path) -> None:
    """Two parallel slices edit the same line: the second integration is merge_conflict.

    Its worktree is kept, and the integration branch keeps only the first slice.
    """
    repo = es.engine_repo(tmp_git_repo, run_script, names=())
    repo.write("shared.txt", "base line\n")
    repo.commit("shared", "shared.txt")
    es.v6_mission(repo, run_script, ("01-a", ["src/a/**"]), ("02-b", ["src/b/**"]))
    body = BARRIER.format(arrivals=str(tmp_path / "arrivals")) + es.AUDIT_PASS
    script = es.fake_script(tmp_path, body)
    assert es.run(repo, es.make_probes(executor=es.fake_executor(script))) == 1
    records = es.state(repo)["slices"]
    statuses = sorted((r["status"], r["failure_type"]) for r in records.values())
    assert statuses == [("audited", None), ("blocked", "merge_conflict")]
    (won,) = [r for r in records.values() if r["status"] == "audited"]
    (lost,) = [r for r in records.values() if r["status"] == "blocked"]
    assert lost["worktree_kept"] is True and Path(lost["worktree"]).is_dir()
    integration = f"swimlane/hacf/{es.state(repo)['run_id']}/integration"
    assert repo.git("rev-parse", integration).strip() == won["integration_commit"]
    integration_dir = Path(won["worktree"]).parent / "integration"
    assert repo.git("-C", str(integration_dir), "status", "--porcelain") == ""
    assert not (repo.path / ".git" / "worktrees" / "integration" / "MERGE_HEAD").exists()
    assert repo.git("show", f"{integration}:shared.txt").startswith("changed by ")


def test_ledger_invalid_rollback(tmp_git_repo: GitRepo, run_script, tmp_path) -> None:
    """An outbox entry the ledger refuses rolls the integration back: ledger_invalid.

    reset --hard runs inside the integration worktree only; the main checkout is untouched.
    """
    repo = es.engine_repo(tmp_git_repo, run_script)
    ledger = (repo.path / "spec" / "compiled" / "architecture.yml").read_bytes()
    body = (
        LEDGER_CLI
        + (
            "if phase == 'execute':\n"
            "    Path('stub_change.txt').write_text('a change\\n')\n"
            "    subprocess.run(ledger + ['set-edges', '--id', 'ghost_node', '--add-depends-on',"
            " 'other_node'], check=True)\n"
        )
        + es.AUDIT_PASS
    )
    script = es.fake_script(tmp_path, body)
    head = repo.git("rev-parse", "HEAD")
    assert es.run(repo, es.make_probes(executor=es.fake_executor(script))) == 1
    record = es.slice_record(repo)
    assert (record["status"], record["failure_type"]) == ("blocked", "ledger_invalid")
    outbox = es.run_dirs(repo)[-1] / "outbox" / "MiniPRD_01_Alpha" / "execute"
    assert [p.name for p in outbox.iterdir()] == ["000001-set-edges.json"]
    integration = f"swimlane/hacf/{es.state(repo)['run_id']}/integration"
    assert repo.git("rev-parse", integration) == head  # rolled back to the base
    integration_dir = Path(record["worktree"]).parent / "integration"
    assert repo.git("-C", str(integration_dir), "status", "--porcelain") == ""
    assert not (integration_dir / "stub_change.txt").exists()
    assert repo.git("rev-parse", "HEAD") == head
    assert repo.git("status", "--porcelain", "-uall") == "?? .agents/local/hacf.yml\n"
    assert (repo.path / "spec" / "compiled" / "architecture.yml").read_bytes() == ledger
