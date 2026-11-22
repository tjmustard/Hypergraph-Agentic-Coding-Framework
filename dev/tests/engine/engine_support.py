"""Shared helpers for the S09a2 engine tests (worktrees, guard, integration, land).

Every repository is a throwaway git repo under tmp_path holding a ledger, a chain of legacy
MiniPRDs and the S08 stub executor at its shipped argv path, so `--executor stub` runs as
shipped. Worktrees go under tmp_path (git.worktree_root in an untracked .agents/local/hacf.yml),
so nothing is written to the user's cache, this repository or the uv cache.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import hyper_engine as he
import hyper_executors as hx
import yaml
from conftest import FIXTURES_DIR, GitRepo

SKIP_BWRAP = "bwrap cannot run here (`bwrap --ro-bind / / true` failed); argv checked only"


def bwrap_ok() -> bool:
    """Return True when `bwrap --ro-bind / / true` exits 0 (Clarifying question 5)."""
    if shutil.which("bwrap") is None:
        return False
    argv = ["bwrap", "--ro-bind", "/", "/", "true"]
    return subprocess.run(argv, capture_output=True, check=False).returncode == 0


def engine_env(monkeypatch, tmp_path: Path) -> None:
    """Set HACF_TEST=1 for the stub (R41); keep stub state and bytecode out of the repo.

    /usr/bin comes first on PATH, so `python3` in the stub argv and in probe oracles is the
    system interpreter, whose stdlib a bwrap profile can see.
    """
    monkeypatch.setenv("HACF_TEST", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("TMPDIR", str(tmp_path))  # the stub's audit counters
    monkeypatch.setenv("PATH", f"/usr/bin:/bin{os.pathsep}{os.environ.get('PATH', '')}")


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def write_local(repo: GitRepo, extra: dict | None = None) -> None:
    """Write the untracked .agents/local/hacf.yml: worktrees under tmp, no oracle by default."""
    config = {
        "git": {"worktree_root": str(repo.scratch / "worktrees" / "{repo_hash}")},
        "oracle": {"default": [], "prepare": []},
    }
    _merge(config, extra or {})
    repo.write(".agents/local/hacf.yml", yaml.safe_dump({"orchestration": config}))


def engine_repo(
    repo: GitRepo, run_script, names=("01_Alpha",), local=None, tracked_ledger: bool = True
) -> GitRepo:
    """Commit a ledger (or gitignore it), legacy MiniPRDs and the stub; write the local config."""
    if not tracked_ledger:
        repo.write(".gitignore", "spec/compiled/architecture.yml\n")
    proc = run_script(".agents/scripts/hyper_ledger.py", "init", cwd=repo.path, env=repo.env)
    assert proc.returncode == 0, proc.stderr
    for name in names:
        text = f"# MiniPRD: {name}\n\n**Hypergraph Node ID:** {name.lower()}\n"
        repo.write(f"spec/compiled/MiniPRD_{name}.md", text)
    stub = repo.path / "dev" / "tests" / "fixtures" / "stub_executor.py"
    stub.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES_DIR / "stub_executor.py", stub)
    repo.git("config", "user.name", "HACF Test")
    repo.git("config", "user.email", "test@example.invalid")
    repo.commit("engine fixture")
    write_local(repo, local)
    assert ledger_tracked(repo) is tracked_ledger
    return repo


def ledger_tracked(repo: GitRepo) -> bool:
    """Return True when the repository tracks its ledger."""
    return bool(repo.git("ls-files", "spec/compiled/architecture.yml").strip())


def ledger_cli(repo: GitRepo, run_script, *args: str) -> None:
    """Run hyper_ledger.py in the main checkout (a manual ledger change)."""
    proc = run_script(".agents/scripts/hyper_ledger.py", *args, cwd=repo.path, env=repo.env)
    assert proc.returncode == 0, proc.stderr


def v6_mission(repo: GitRepo, run_script, *slices: tuple[str, list[str]]) -> list[str]:
    """Commit a lint-clean v6 mission m of (NN-slug, hacf_touches) slices; return their ids.

    hyper_scope.py scaffolds it; the requirement and proof-contract placeholders are filled.
    """

    def scope(*args: str) -> None:
        proc = run_script(".agents/scripts/hyper_scope.py", *args, cwd=repo.path, env=repo.env)
        assert proc.returncode == 0, proc.stderr

    scope("project", "init", "--intent", "engine test project")
    scope("mission", "create", "--name", "m", "--intent", "engine test mission")
    ids = []
    for name, touches in slices:
        node = f"node_{name.split('-', 1)[1]}"
        scope("slice", "create", "--mission", "missions/m", "--name", name, "--intent", name,
              "--node-id", node, "--touches", ",".join(touches))  # fmt: skip
        spec = repo.path / "missions" / "m" / "slices" / name / "SPEC.md"
        text = re.sub(
            r"^1\. \[US-001.*$", "1. US-001: the change lands", spec.read_text(), flags=re.M
        )
        text = re.sub(r"^- \[ \] \[One promised.*$", "- [ ] the change lands", text, flags=re.M)
        spec.write_text(text, encoding="utf-8")
        ids.append(yaml.safe_load(text.split("---")[1])["id"])
    repo.commit("mission", "SPEC.md", "project.yaml", "workspace.yaml", "missions")
    return ids


def make_probes(scenarios: dict | None = None, *, run=None, executor=None) -> he.Probes:
    """Real git, scripts and stub; `which` finds bwrap and socat for the agent-sandbox check.

    scenarios maps a slice id to the stub scenario its phases play (default success).
    """
    scenarios = scenarios or {}

    def stub(name, config, allow_unsandboxed=False):
        made = hx.get_executor(name, config, allow_unsandboxed=allow_unsandboxed)
        prepare = made.prepare

        def with_scenario(phase, ctx, directory):
            chosen = scenarios.get(ctx["slice"], "success")
            return prepare(phase, {**ctx, "scenario": chosen}, directory)

        made.prepare = with_scenario
        return made

    return he.Probes(
        which=lambda tool: shutil.which(tool) or f"/usr/bin/{tool}",
        run=run or he._run_argv,
        executor=executor or stub,
    )


def run(repo: GitRepo, probes: he.Probes, resume: str | None = None, **flags) -> int:
    """Run the engine with the stub executor (unverified, so the flag is set)."""
    flags = {"executor": "stub", "unverified_executor": True, **flags}
    before = set(_listed(repo))
    code = he.run_engine(repo.path, flags, he.WorktreeRunner(), probes=probes, resume=resume)
    # Run ids from one second sort by their random suffix, so remember the creation order.
    repo.__dict__.setdefault("runs", []).extend(sorted(set(_listed(repo)) - before))
    return code


def _listed(repo: GitRepo) -> list[Path]:
    root = repo.path / ".agents" / "state" / "runs"
    return sorted(root.iterdir()) if root.is_dir() else []


def run_dirs(repo: GitRepo) -> list[Path]:
    """Return the run directories es.run made, oldest first."""
    return repo.__dict__.get("runs") or _listed(repo)


def state(repo: GitRepo, index: int = -1) -> dict:
    """Return a run's run.json (default: the latest run)."""
    return json.loads((run_dirs(repo)[index] / "run.json").read_text(encoding="utf-8"))


def events(repo: GitRepo, index: int = -1) -> list[dict]:
    """Return a run's events."""
    lines = (run_dirs(repo)[index] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def slice_record(repo: GitRepo, sid: str = "MiniPRD_01_Alpha") -> dict:
    """Return one slice's record of the latest run."""
    return state(repo)["slices"][sid]


def refs(repo: GitRepo) -> dict[str, str]:
    """Return every ref of repo as {name: sha}."""
    text = repo.git("for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in text.splitlines())


def fake_executor(script: str):
    """Return a Probes.executor whose phases run `<python> <script> <phase>` in the worktree.

    It builds the real claude-cli argv and settings first (kept beside them as
    claude_argv.json), then runs the script instead of claude. The script sees HACF_OUTBOX,
    HACF_SLICE and the rest of the engine env and prints a claude JSON result; it lives under
    tmp_path, never in the repo (Clarifying question 6).
    """

    class Fake(hx.ClaudeCli):
        def preflight(self, environ=None):
            return {"version": None, "verified": False, "warnings": []}

        def prepare(self, phase, ctx, directory):
            argv = super().prepare(phase, ctx, directory)
            (Path(directory) / "claude_argv.json").write_text(json.dumps(argv), encoding="utf-8")
            return [sys.executable, script, phase]

    def make(name, config, allow_unsandboxed=False):
        return Fake("claude-cli", config, allow_unsandboxed=allow_unsandboxed)

    return make


RESULT = (
    'print(json.dumps({"type": "result", "subtype": "success", "is_error": False, '
    '"num_turns": 1, "result": "fake", "session_id": "fake", "total_cost_usd": 0.0}))\n'
)


def fake_script(tmp_path: Path, body: str, name: str = "fake_executor.py") -> str:
    """Write a fake executor script: imports, `phase` and `outbox`, then body, then a result."""
    head = (
        "import json, os, sys\nfrom pathlib import Path\n"
        "phase = sys.argv[1]\noutbox = Path(os.environ['HACF_OUTBOX'])\n"
        "slice_id = os.environ['HACF_SLICE']\n"
    )
    path = tmp_path / name
    path.write_text(head + body + "\n" + RESULT, encoding="utf-8")
    return str(path)


AUDIT_PASS = (
    "if phase == 'audit':\n"
    "    (outbox / 'audit.json').write_text(json.dumps({'slice': slice_id, 'verdict': 'PASS'}))\n"
)
