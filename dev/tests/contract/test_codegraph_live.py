"""Live contract test: hyper_codegraph.py against the real codebase-memory-mcp 0.12.1 (S04).

Opt-in only: markers `live` and `codegraph`, and it runs only when HACF_LIVE_CODEGRAPH=1, which
the user sets after consenting to download and run `uvx codebase-memory-mcp@0.12.1` (the PyPI
shim fetches and verifies the native runtime on its first run):

    HACF_LIVE_CODEGRAPH=1 uv run --locked pytest -m 'live and codegraph' \
        dev/tests/contract/test_codegraph_live.py

The harness, never the model, writes one verbatim capture per backend call to
dev/tests/fixtures/recorded-raw/S04/<NN>-<step>.json (argv, cwd, stdout, stderr, exit code, the
backend's `--version`, UTC date), computes contract_verdict from the checks (`pass` only when
every check passed) and writes verdict.json there (R27). Calls the adapter makes are captured
by wrapping hyper_codegraph._run, so each capture is exactly what the adapter ran and read.
The backend indexes a throwaway git repository under tmp_path. CBM_CACHE_DIR points at
tmp_path too, so the index (deleted at teardown) and the backend's config and logs never reach
~/.cache/codebase-memory-mcp. `observations` in verdict.json record facts that do not gate the
verdict (Q10-Q12, and R34's `base_commit_id_accepted`: whether `detect_changes --base-branch
<the HEAD commit id>` sees an uncommitted edit).

After a live run, write the cleaned copies with `python3 dev/tests/fixtures/normalize_recorded.py
--set S04`: only those (dev/tests/fixtures/recorded/S04/) are committed for export; the raw
captures in recorded-raw/ never ship.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _runtime
import hyper_codegraph
import hypergraph_updater
import pytest
from conftest import FIXTURES_DIR, REPO_ROOT, GitRepo

RECORDED = FIXTURES_DIR / "recorded-raw" / "S04"
SCRIPT = REPO_ROOT / ".agents" / "scripts" / "hyper_codegraph.py"
PREFIX = list(hyper_codegraph.CBM_PREFIX)
FIRST_CALL_TIMEOUT = 900.0  # the first uvx call downloads the shim and its native runtime
CALL_TIMEOUT = 120.0

pytestmark = [
    pytest.mark.live,
    pytest.mark.codegraph,
    pytest.mark.skipif(
        os.environ.get("HACF_LIVE_CODEGRAPH") != "1",
        reason="live codebase-memory-mcp contract test: set HACF_LIVE_CODEGRAPH=1 after the "
        "user consents (S04 clarifying question 1)",
    ),
]

CORE_PY = '''"""Fixture module: the callee."""


def helper(value):
    """Return value doubled."""
    return value * 2
'''

CORE_PY_EDITED = '''"""Fixture module: the callee."""


def helper(value):
    """Return value tripled."""
    return value * 3
'''

CALLER_PY = '''"""Fixture module: the caller."""

from pkg.core import helper


def use(value):
    """Call helper and add one."""
    return helper(value) + 1
'''

LEDGER = """schema: hacf.ledger/v2
nodes:
  - id: core_mod
    dimension: Module
    status: clean
    associated_file: pkg/core.py
    files: ['pkg/core.py']
    description: >-
      Fixture module that defines helper().
    inputs: []
    outputs: []
    edges: {depends_on: [], implements: [], member_of: []}
  - id: caller_mod
    dimension: Module
    status: clean
    associated_file: pkg/caller.py
    files: ['pkg/caller.py']
    description: >-
      Fixture module that calls helper(); no ledger edge links it to core_mod.
    inputs: []
    outputs: []
    edges: {depends_on: [], implements: [], member_of: []}
"""

HACF_YML = """code_graph:
  backend: codebase-memory-mcp
  install_method: uvx
  base_branch: main
"""


class Contract:
    """Runs backend calls, writes their captures and collects the check results."""

    def __init__(self) -> None:
        """Start an empty capture set; the caller cleared RECORDED first."""
        self.seq = 0
        self.tool_version: str | None = None
        self.captures: list[str] = []
        self.checks: dict[str, bool] = {}
        self.observations: dict[str, object] = {}
        self.original_run = hyper_codegraph._run

    def record(
        self,
        step: str,
        proc: hyper_codegraph.Completed,
        cwd: Path | None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Write one capture exactly as the call returned it."""
        self.seq += 1
        name = f"{self.seq:02d}-{step}.json"
        record = {
            "step": step,
            "argv": proc.argv,
            "cwd": None if cwd is None else str(cwd),
            "env": env or {},
            "exit_code": proc.returncode,
            "timed_out": proc.timed_out,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "tool_version": self.tool_version,
            "captured_at": _runtime.utc_now_iso(),
        }
        (RECORDED / name).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.captures.append(name)

    def direct(
        self, step: str, argv: list[str], cwd: Path, timeout: float = CALL_TIMEOUT
    ) -> hyper_codegraph.Completed:
        """Run argv through the adapter's own runner and capture it."""
        proc = self.original_run(argv, cwd=cwd, timeout=timeout)
        self.record(step, proc, cwd)
        return proc

    def spy(self, argv: list[str], *, cwd: Path | None, timeout: float):
        """Stand in for hyper_codegraph._run: capture every call that runs the backend."""
        proc = self.original_run(argv, cwd=cwd, timeout=timeout)
        if argv[: len(PREFIX)] == PREFIX:
            rest = argv[len(PREFIX) :]
            tool = rest[1] if rest[:1] == ["cli"] and len(rest) > 1 else "version"
            self.record(f"adapter-{tool}", proc, cwd)
        return proc

    def check(self, name: str, ok: object) -> bool:
        """Record one named check; the verdict is pass only when every check is True."""
        self.checks[name] = bool(ok)
        return bool(ok)

    def backend_calls(self) -> int:
        """Return how many captures so far ran the backend through the adapter."""
        return sum(1 for name in self.captures if "-adapter-" in name)


def _scaffold(repo: GitRepo) -> None:
    """Write the fixture package, ledger and code-graph config, and commit them on main."""
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", CORE_PY)
    repo.write("pkg/caller.py", CALLER_PY)
    repo.write("spec/compiled/architecture.yml", LEDGER)
    repo.write(".agents/local/hacf.yml", HACF_YML)
    defaults = REPO_ROOT / hyper_codegraph.DEFAULTS_REL
    repo.write(hyper_codegraph.DEFAULTS_REL, defaults.read_text(encoding="utf-8"))
    repo.write(".gitignore", ".agents/state/\n")
    repo.commit("code-graph contract fixture")


def _flag_listed(flag: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


def _payload(capture: str) -> object:
    data = json.loads((RECORDED / capture).read_text(encoding="utf-8"))
    try:
        return json.loads(data["stdout"])
    except ValueError:
        return None


def _run_contract(c: Contract, repo: GitRepo, capsys: pytest.CaptureFixture) -> str | None:
    """Run every contract step; return the indexed project name (for teardown)."""
    root = repo.path
    proc = c.direct("version", [*PREFIX, "--version"], root, FIRST_CALL_TIMEOUT)
    match = hyper_codegraph.VERSION_RE.search(proc.stdout)
    c.tool_version = match.group(0) if match else None
    c.check("uvx_prefix_runs", proc.returncode == 0)
    c.check("version_pinned", c.tool_version == hyper_codegraph.PINNED_VERSION)

    templates = hyper_codegraph.EMBEDDED_DEFAULTS["backends"]["codebase-memory-mcp"]
    for capability, template in templates["capabilities"].items():
        tool = template[1]
        proc = c.direct(f"help-{tool}", [*PREFIX, "cli", tool, "--help"], root)
        text = proc.stdout + proc.stderr
        flags = [element for element in template if element.startswith("--")]
        missing = [flag for flag in flags if not _flag_listed(flag, text)]
        c.check(f"help_{capability}_spellings", proc.returncode == 0 and not missing)
        if missing:
            c.observations[f"help_{capability}_missing_flags"] = missing

    index = hyper_codegraph.run_capability("index", repo_root=root)
    state, _ = hyper_codegraph.load_state(root)
    project = state.get("project_name")
    c.check("index_ok", index["status"] == "ok" and project and index["project"] == project)
    c.observations["index_result"] = {k: index[k] for k in ("status", "project", "error")}
    if not project:
        return None

    status = hyper_codegraph.run_capability("status", repo_root=root)
    c.check("status_ok", status["status"] == "ok")

    trace = hyper_codegraph.run_capability("symbol_impact", repo_root=root, symbol="helper")
    c.observations["symbol_impact_result"] = {
        k: trace[k] for k in ("status", "impacted", "impacted_files", "components", "error")
    }
    # R32: trace_path names no file, so the caller shows up as a symbol at distance 1 only.
    c.check(
        "trace_finds_caller",
        trace["status"] == "ok"
        and any(
            item["symbol"].endswith("use") and item["distance"] == 1 and item["file"] is None
            for item in trace["impacted"]
        )
        and trace["impacted_files"] == []
        and trace["components"] == []
        and hyper_codegraph.SYMBOL_IMPACT_WARNING in trace["warnings"],
    )

    search = hyper_codegraph.run_capability("search", repo_root=root, pattern=".*helper.*")
    c.observations["search_result"] = {
        k: search[k] for k in ("status", "impacted", "impacted_files", "error")
    }
    c.check(
        "search_finds_helper",
        search["status"] == "ok"
        and any(
            item["symbol"].endswith("helper") and item["file"] == "pkg/core.py"
            for item in search["impacted"]
        ),
    )

    repo.write("pkg/core.py", CORE_PY_EDITED)  # an uncommitted edit, as during /hyper-execute
    diff = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    c.observations["diff_impact_result"] = {
        k: diff[k]
        for k in ("status", "changed_files", "impacted", "impacted_files", "components", "error")
    }
    c.check(
        "detect_changes_sees_edit",
        diff["status"] == "ok" and "pkg/core.py" in diff["changed_files"],
    )

    # R34: does 0.12.1 accept a full commit id as --base-branch? The updater passes one.
    head = repo.git("rev-parse", "HEAD").strip()
    template = templates["capabilities"]["diff_impact"]
    argv = hyper_codegraph.build_argv(PREFIX, template, {"project": project, "base": head})
    proc = c.direct("detect_changes-base-commit", argv, root)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    c.observations["base_commit_id_accepted"] = (
        proc.returncode == 0
        and isinstance(payload, dict)
        and "pkg/core.py" in (payload.get("changed_files") or [])
    )

    capsys.readouterr()
    ledger = root / "spec" / "compiled" / "architecture.yml"
    code = hypergraph_updater.main([str(ledger), "--from-diff", "HEAD", "--json"])
    out = capsys.readouterr()
    c.observations["from_diff"] = {"exit_code": code, "stdout": out.out, "stderr": out.err}
    try:
        report = json.loads(out.out)
    except ValueError:
        report = {}
    graph = report.get("code_graph") or {}
    c.check(
        "from_diff_marks_caller",
        code == 0
        and graph.get("status") == "ok"
        and "caller_mod" in graph.get("components", [])
        and "caller_mod" in report.get("blast_radius", [])
        and "caller_mod" not in report.get("nodes", []),
    )

    before = c.backend_calls()
    for step, flag in (
        ("refuse-project-dash", "--project=-x"),
        ("refuse-project-option", "--project=--format"),
        ("refuse-pattern-dash", "--pattern=-x"),
        ("refuse-symbol-dash", "--symbol=--direction"),
    ):
        capability = "symbol_impact" if flag.startswith("--symbol") else "search"
        argv = [sys.executable, str(SCRIPT), capability, "--repo-root", str(root), flag]
        if capability == "search" and not flag.startswith("--pattern"):
            argv.append("--pattern=x")
        proc = c.direct(step, argv, root)
        c.check(
            f"leading_dash_{step.removeprefix('refuse-').replace('-', '_')}_refused",
            proc.returncode == 2 and "refused" in proc.stderr,
        )
    c.check("leading_dash_runs_nothing", c.backend_calls() == before)

    proc = c.direct(
        "index-status-unknown-project",
        [*PREFIX, "cli", "index_status", "--project", "hacf-no-such-project", "--format", "json"],
        root,
    )
    c.observations["unknown_project_exit_code"] = proc.returncode
    _offline(c, root)
    return project


def _offline(c: Contract, root: Path) -> None:
    """Observe whether the uvx prefix runs with uv offline (and, when possible, no network)."""
    env = {"UV_OFFLINE": "1"}
    argv = [*PREFIX, "--version"]
    if (
        shutil.which("unshare")
        and subprocess.run(["unshare", "-rn", "true"], capture_output=True, check=False).returncode
        == 0
    ):
        argv = ["unshare", "-rn", *argv]
    try:
        done = subprocess.run(
            argv,
            cwd=str(root),
            env={**os.environ, **env},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT,
            check=False,
        )
        proc = hyper_codegraph.Completed(argv, done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired as exc:
        proc = hyper_codegraph.Completed(argv, None, str(exc.stdout or ""), "timeout", True)
    c.record("offline-version", proc, root, env)
    c.observations["offline_uvx_runs"] = proc.returncode == 0
    c.observations["offline_network_namespace"] = argv[0] == "unshare"


def _teardown(c: Contract, root: Path, project: str | None) -> None:
    """Delete the fixture's index and check list_projects no longer names it."""
    if project is None:
        return
    deleted = hyper_codegraph.run_capability("delete", repo_root=root)
    c.check("delete_ok", deleted["status"] == "ok")
    state, _ = hyper_codegraph.load_state(root)
    c.check("delete_forgets_project", "project_name" not in state)
    listing = hyper_codegraph.run_capability("list_projects", repo_root=root)
    c.check("list_projects_after_delete_ok", listing["status"] == "ok")
    payload = _payload(c.captures[-1])
    try:
        names = [p["name"] for p in hyper_codegraph.normalize_list_projects(payload, root)]
    except hyper_codegraph.ShapeError:
        names = None
    c.check("index_deleted", names is not None and project not in names)


def test_codegraph_contract(
    tmp_path: Path,
    tmp_git_repo: GitRepo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Run the consented contract test, write captures and verdict.json, and require a pass."""
    RECORDED.mkdir(parents=True, exist_ok=True)
    for stale in RECORDED.glob("*.json"):
        stale.unlink()
    c = Contract()
    _scaffold(tmp_git_repo)
    cache = tmp_path / "cbm-cache"
    monkeypatch.setenv("CBM_CACHE_DIR", str(cache))
    c.observations["cbm_cache_dir"] = str(cache)
    monkeypatch.setattr(hyper_codegraph, "_run", c.spy)
    project = None
    try:
        project = _run_contract(c, tmp_git_repo, capsys)
    finally:
        _teardown(c, tmp_git_repo.path, project)
        verdict = {
            "contract_verdict": "pass" if c.checks and all(c.checks.values()) else "fail",
            "backend": "codebase-memory-mcp",
            "tool_version": c.tool_version,
            "exe_argv_prefix": PREFIX,
            "captured_at": _runtime.utc_now_iso(),
            "checks": c.checks,
            "observations": c.observations,
            "captures": c.captures,
        }
        (RECORDED / "verdict.json").write_text(
            json.dumps(verdict, indent=2) + "\n", encoding="utf-8"
        )
    failed = [name for name, ok in c.checks.items() if not ok]
    assert verdict["contract_verdict"] == "pass", f"failed checks: {failed}"
