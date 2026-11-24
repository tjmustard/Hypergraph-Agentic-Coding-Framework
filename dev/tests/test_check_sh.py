"""Tests for check.sh and check_env.sh, the R13 lint baseline and the S00 state ignores."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, clean_env

CHECK_SH = REPO_ROOT / "dev" / "scripts" / "check.sh"
CHECK_ENV = REPO_ROOT / "dev" / "scripts" / "check_env.sh"
LEGACY_SCRIPTS = [
    f".agents/scripts/{name}"
    for name in (
        "hyper_update_verification.py",
        "hyper_orchestrator.py",
        "hyper_daemon.py",
        "hyper_update_merge_engine.py",
        "hyper_resolve_conflict.py",
        "token_budget_checker.py",
        "token_budget_reconciler.py",
        "semantic_graph_merger.py",
        "archive_specs.py",
        "hypergraph_updater.py",
        "hyper_update_core.py",
        "model_router.py",
    )
]
STATE_PATHS = [
    ".agents/state/x",
    ".agents/.backup/x",
    ".agents/logs/x",
    ".agents/hacf-manifest.json",
    "exhaust/x",
    ".openrig/x",
]


def _run(argv: list[str], **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        env=clean_env(**env),
        input="",
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_check_sh_fails_without_310_leg(tmp_path):
    """A 3.10 interpreter that cannot import pytest makes check.sh fail before any pytest leg."""
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "pytest.py").write_text(
        'raise ImportError("pytest hidden by the check.sh test shim")\n', encoding="utf-8"
    )
    shim = tmp_path / "python3"
    shim.write_text(f'#!/bin/sh\nPYTHONPATH="{poison}" exec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)

    # --collect-only keeps a broken preflight from re-running this whole suite.
    proc = _run(["bash", str(CHECK_SH)], HACF_PY310=str(shim), PYTEST_ADDOPTS="--collect-only")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, output
    assert "cannot import pytest, yaml and xdist" in output
    assert "This is a failure, not a skip" in output
    assert "[2/5]" not in output, "check.sh ran a pytest leg after the preflight failed"


def _env_fn(call: str, unset: tuple[str, ...] = (), **env: str) -> subprocess.CompletedProcess:
    """Run one check_env.sh function call in a bash that has only sourced the file.

    `unset` names variables removed from the child's environment, so they are truly unset.
    """
    child = clean_env(**env)
    for name in unset:
        child.pop(name, None)
    return subprocess.run(
        ["bash", "-c", f'source "{CHECK_ENV}"; {call}'],
        cwd=REPO_ROOT,
        env=child,
        input="",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("nproc", "mem", "expected"),
    [(20, "6144", "4"), (20, "2000", "1"), (1, "64000", "1"), (20, "64000", "10"), (8, "", "4")],
)
def test_worker_budget(nproc, mem, expected):
    """The budget is max(1, min(nproc/2, (mem - 4096) / 512)); an empty mem drops that term."""
    proc = _env_fn(f'hacf_worker_budget {nproc} "{mem}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{expected}\n"


def test_test_workers_overrides():
    """HACF_TEST_WORKERS: unset is the budget, auto and N pass, 0 is serial, others status 2."""
    proc = _env_fn('hacf_test_workers 20 "6144"', unset=("HACF_TEST_WORKERS",))
    assert (proc.returncode, proc.stdout) == (0, "4\n"), proc.stderr
    for value, out in (("", "4\n"), ("auto", "auto\n"), ("6", "6\n"), ("0", "")):
        proc = _env_fn('hacf_test_workers 20 "6144"', HACF_TEST_WORKERS=value)
        assert (proc.returncode, proc.stdout) == (0, out), (value, proc.stderr)
    for value in ("abc", "-1", "1.5", "07"):
        proc = _env_fn('hacf_test_workers 20 "6144"', HACF_TEST_WORKERS=value)
        assert proc.returncode == 2, value
        assert proc.stdout == "", value
        assert "HACF_TEST_WORKERS" in proc.stderr and "auto" in proc.stderr, proc.stderr


def test_pick_tmpdir(tmp_path):
    """Override, then a non-tmpfs inherited TMPDIR, then the XDG default; never inside the tree."""
    xdg = tmp_path / "xdg"
    pick = f'hacf_pick_tmpdir "{REPO_ROOT}"'
    unset = ("HACF_TEST_TMPDIR", "TMPDIR")
    override = tmp_path / "override" / "t"

    proc = _env_fn(pick, HACF_TEST_TMPDIR=str(override), TMPDIR=str(REPO_ROOT.parent))
    assert (proc.returncode, proc.stdout) == (0, f"{override}\tHACF_TEST_TMPDIR\n"), proc.stderr

    proc = _env_fn(pick, unset=("HACF_TEST_TMPDIR",), TMPDIR="/tmp/hacf-x", XDG_CACHE_HOME=str(xdg))
    default = xdg / "hacf-tmp" / "check"
    tmp_is_tmpfs = (
        subprocess.run(["stat", "-f", "-c", "%T", "/tmp"], capture_output=True, text=True).stdout
        == "tmpfs\n"
    )
    reason = "is on tmpfs" if tmp_is_tmpfs else "is /tmp"
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{default}\tdefault; inherited TMPDIR /tmp/hacf-x {reason}\n"

    proc = _env_fn(pick, unset=("HACF_TEST_TMPDIR",), TMPDIR=str(REPO_ROOT.parent))
    assert (proc.returncode, proc.stdout) == (0, f"{REPO_ROOT.parent}\tinherited TMPDIR\n")

    proc = _env_fn(pick, unset=unset, XDG_CACHE_HOME=str(xdg))
    assert (proc.returncode, proc.stdout) == (0, f"{default}\tdefault\n"), proc.stderr

    inside = REPO_ROOT / ".hacf-temp-probe"
    for env in ({"HACF_TEST_TMPDIR": str(inside)}, {"TMPDIR": str(inside)}):
        drop = tuple(name for name in unset if name not in env)
        proc = _env_fn(pick, unset=drop, **env)
        assert proc.returncode == 1, env
        assert proc.stdout == ""
        assert "is inside the work tree" in proc.stderr and "refusing" in proc.stderr

    assert not override.exists() and not xdg.exists() and not inside.exists()
    assert not os.path.exists("/tmp/hacf-x")


def _xdist_shim(tmp_path):
    """A HACF_PY310 stand-in that execs this interpreter (which has pytest, yaml and xdist)."""
    shim = tmp_path / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)
    return shim


def test_check_sh_refuses_temp_inside_tree(tmp_path):
    """check.sh exits 1 before any pytest leg when the test temp would be inside the work tree."""
    probe = REPO_ROOT / ".hacf-temp-probe"
    proc = _run(
        ["bash", str(CHECK_SH)],
        HACF_PY310=str(_xdist_shim(tmp_path)),
        PYTEST_ADDOPTS="--collect-only",
        HACF_TEST_TMPDIR=str(probe),
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "is inside the work tree" in output
    assert "[2/5]" not in output, "check.sh ran a pytest leg with its temp inside the tree"
    assert not probe.exists()


def test_check_sh_rejects_bad_workers(tmp_path):
    """check.sh exits 2 before any pytest leg on an invalid HACF_TEST_WORKERS."""
    proc = _run(
        ["bash", str(CHECK_SH)],
        HACF_PY310=str(_xdist_shim(tmp_path)),
        PYTEST_ADDOPTS="--collect-only",
        HACF_TEST_WORKERS="abc",
        HACF_TEST_TMPDIR=str(tmp_path / "temp"),
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 2, output
    assert "HACF_TEST_WORKERS=abc is not valid" in output
    assert "[2/5]" not in output, "check.sh ran a pytest leg with an invalid worker count"


def test_check_sh_header_documents_the_host_lock():
    """The header names the lock, its env vars, `status` and exit 75 (retry later, no verdict)."""
    header = CHECK_SH.read_text(encoding="utf-8").split("set -euo pipefail", 1)[0]
    for needle in (
        "dev/scripts/host_gate.py run --job check.sh",
        "hacf/heavy-job.lock",
        "HACF_HOST_MIN_MB",
        "HACF_HEAVY_WAIT_S",
        "HACF_HEAVY_LOCK_HELD",
        "HACF_TEST_WORKERS_SHARED",
        "host_gate.py status",
        "Exit 75",
        "never a test verdict",
        "Live tests (marker `live`) never run here.",
    ):
        assert needle in header, needle


def _lock_env(tmp_path, **extra):
    """check.sh's environment with the host lock under tmp_path and the memory floor met."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 16777216 kB\n", encoding="utf-8")
    return dict(
        HACF_HEAVY_LOCK_PATH=str(tmp_path / "lock" / "heavy-job.lock"),
        HACF_HOST_MEMINFO=str(meminfo),
        HACF_HEAVY_LOCK_HELD="",
        HACF_PY310=str(_xdist_shim(tmp_path)),
        PYTEST_ADDOPTS="--collect-only",
        HACF_TEST_WORKERS="abc",  # stops check.sh right after the lock (exit 2), no pytest leg
        HACF_TEST_TMPDIR=str(tmp_path / "temp"),
        **extra,
    )


def _lock_log(tmp_path):
    log = tmp_path / "lock" / "heavy-job.log"
    return [line.split()[1:4:2] for line in log.read_text().splitlines()] if log.exists() else []


def test_check_sh_reruns_itself_under_the_host_lock(tmp_path):
    """Without a lease, check.sh re-runs itself under host_gate.py (job check.sh)."""
    proc = _run(["bash", str(CHECK_SH)], **_lock_env(tmp_path))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _lock_log(tmp_path) == [["acquire", "job=check.sh"], ["release", "job=check.sh"]]


def test_check_sh_with_a_valid_token_does_not_rerun(tmp_path, monkeypatch):
    """Inside a lease (a valid HACF_HEAVY_LOCK_HELD, as under the gate) check.sh runs in place."""
    env = _lock_env(tmp_path)
    for key in ("HACF_HEAVY_LOCK_PATH", "HACF_HOST_MEMINFO"):
        monkeypatch.setenv(key, env[key])
    sys.path.insert(0, str(REPO_ROOT / "dev" / "scripts"))
    import host_gate

    with host_gate.acquire("test-lease") as lease:
        env.update(lease.env())
        proc = _run(["bash", str(CHECK_SH)], **env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "HACF_TEST_WORKERS=abc is not valid" in proc.stderr
    assert _lock_log(tmp_path) == [["acquire", "job=test-lease"], ["release", "job=test-lease"]]


STUB = r"""
import os, re, sys
from pathlib import Path

args = sys.argv[1:]
tool = Path(sys.argv[0]).name


def leg(name):
    mode = os.environ.get(f"STUB_{name.upper()}", "pass")
    print(f"STUB FULL OUTPUT {name}")
    for i in range(1, 51):
        print(f"stub {name} line {i}")
    xml = next((a.split("=", 1)[1] for a in args if a.startswith("--junitxml=")), None)
    if xml and mode != "noxml":
        bad = mode == "fail"
        case = '<testcase classname="dev.tests.x.test_a" name="test_b"><failure message="m">' \
            'stub traceback\nassert 1 == 2</failure></testcase>' if bad else ""
        Path(xml).write_text(
            '<testsuites><testsuite name="pytest" time="1.5">'
            '<testcase classname="dev.tests.x.test_a" name="test_ok"/>'
            f"{case}</testsuite></testsuites>"
        )
    sys.exit(0 if mode == "pass" else 1)


if tool == "uv" and args[:3] == ["run", "--locked", "python"]:
    print("3.8.0")
elif tool == "uv" and args[:3] == ["run", "--locked", "pytest"]:
    leg("uv")
elif tool == "uv" and args[:3] == ["run", "--locked", "ruff"]:
    print("stub ruff")
    sys.exit(int(os.environ.get("STUB_RUFF_RC", "0")))
elif tool == "python3" and args and args[0].endswith("release_export.py"):
    mode = os.environ.get("STUB_SCAN", "pass")
    if mode == "unknown":
        print("scan worktree: unknown (no digest file)")
        sys.exit(3)
    print("scan worktree: 3 files, latest row 0.1.0")
    print("stub scan table")
    sys.exit(0 if mode == "pass" else 1)
elif tool == "python3":
    os.execv(sys.executable, [sys.executable, *args])
elif tool == "py310" and args[:2] == ["-m", "pytest"]:
    leg("py310")
elif tool == "py310" and args[0] == "-c":
    if "print" in args[1]:
        print("3.8.0")
else:
    sys.exit(f"stub {tool}: unexpected {args}")
"""


def _stub_env(tmp_path, **extra):
    """check.sh's environment with `uv`, HACF_PY310 and `python3` stubs, lock and logs in tmp_path.

    The `python3` stub fakes only the privacy scan (STUB_SCAN) and runs every other script.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("uv", "py310", "python3"):
        stub = bin_dir / name
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 16777216 kB\n", encoding="utf-8")
    env = dict(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        HACF_PY310=str(bin_dir / "py310"),
        HACF_HEAVY_LOCK_PATH=str(tmp_path / "lock" / "heavy-job.lock"),
        HACF_HOST_MEMINFO=str(meminfo),
        HACF_HEAVY_LOCK_HELD="",
        HACF_TEST_WORKERS="0",
        HACF_TEST_TMPDIR=str(tmp_path / "temp"),
        HACF_LOG_DIR=str(tmp_path / "logs"),
        HACF_CHECK_VERBOSE="",
    )
    env.update(extra)
    return env


def _run_dir(tmp_path) -> Path:
    (run,) = (tmp_path / "logs").iterdir()
    return run


def test_check_sh_failing_leg_runs_every_leg(tmp_path):
    """The uv leg fails: the 3.10 leg and ruff still run, the summary names the id, exit 1."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path, STUB_UV="fail"))
    out = proc.stdout
    assert proc.returncode == 1, out + proc.stderr
    run = _run_dir(tmp_path)
    assert f"==> logs: {run}" in out
    for header in ("==> [2/5] pytest (uv venv)", "==> [3/5] pytest (", "==> [4/5] ruff"):
        assert header in out, header
    assert (
        "    [2/5] pytest (uv venv): FAIL (exit 1) 1 passed, 1 failed, 0 errors, 0 skipped in 1.5s"
        in out
    )
    assert "      FAILED dev/tests/x/test_a.py::test_b" in out
    assert "        assert 1 == 2" in out
    assert f"      log: {run}/2-pytest-uv.log  junit: {run}/2-pytest-uv.xml" in out
    assert "[3/5] pytest (" in out and "): PASS (exit 0) 1 passed, 0 failed" in out
    assert "    [4/5] ruff: PASS (exit 0)" in out
    assert "❌  check.sh failed: [2/5] pytest (uv venv)" in proc.stderr
    assert "✅" not in out
    assert "STUB FULL OUTPUT" not in out, "the full output belongs only in the log"
    log = (run / "2-pytest-uv.log").read_text()
    assert "STUB FULL OUTPUT uv" in log and "stub uv line 50" in log
    assert "STUB FULL OUTPUT py310" in (run / "3-pytest-py310.log").read_text()
    assert (run / "4-ruff.log").exists()
    assert run.stat().st_mode & 0o777 == 0o700


def test_check_sh_all_pass(tmp_path):
    """Every leg passes: exit 0 and the ✅ line."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "✅  check.sh passed (uv venv and " in proc.stdout
    assert "STUB FULL OUTPUT" not in proc.stdout


def test_check_sh_ruff_failure_fails_the_run(tmp_path):
    """A failing ruff leg is a FAIL with its log tail, after both pytest legs ran."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path, STUB_RUFF_RC="1"))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "    [4/5] ruff: FAIL (exit 1) " in proc.stdout
    assert "        stub ruff" in proc.stdout
    assert "❌  check.sh failed: [4/5] ruff" in proc.stderr


def test_check_sh_privacy_scan_passes(tmp_path):
    """Leg 5 exit 0: a PASS with the scan's first line, and the final line names the scan."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path))
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "==> [5/5] privacy scan of the work tree" in out
    assert "    [5/5] privacy scan: PASS (exit 0) scan worktree: 3 files, latest row 0.1.0" in out
    assert out.rstrip().endswith("legs, ruff, privacy scan).")
    assert "stub scan table" in (_run_dir(tmp_path) / "5-privacy-scan.log").read_text()


def test_check_sh_privacy_scan_failure_fails_the_run(tmp_path):
    """Leg 5 exit 1: a FAIL with its log tail, and check.sh exits 1 at the end."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path, STUB_SCAN="fail"))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "    [5/5] privacy scan: FAIL (exit 1) " in proc.stdout
    assert "        stub scan table" in proc.stdout
    assert "❌  check.sh failed: [5/5] privacy scan" in proc.stderr
    assert "✅" not in proc.stdout


def test_check_sh_privacy_scan_unknown(tmp_path):
    """Leg 5 exit 3: unknown (no digest file), named in the summary and the final line."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path, STUB_SCAN="unknown"))
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "    [5/5] privacy scan: unknown (no digest file)" in out
    assert "[5/5] privacy scan: PASS" not in out and "[5/5] privacy scan: FAIL" not in out
    assert out.rstrip().endswith("legs, ruff; privacy scan unknown).")
    assert "failed" not in proc.stderr


def test_check_sh_no_junit_falls_back_to_the_tail(tmp_path):
    """A pytest leg that writes no junit XML: the log's last 40 lines in its summary."""
    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path, STUB_PY310="noxml"))
    out = proc.stdout
    assert proc.returncode == 1, out + proc.stderr
    assert "      (no junit XML; the last 40 lines of the log)" in out
    assert "        stub py310 line 50" in out and "        stub py310 line 11" in out
    assert "stub py310 line 10\n" not in out
    assert "stub uv line" not in out


def test_check_sh_verbose_prints_everything(tmp_path):
    """HACF_CHECK_VERBOSE=1: the legs' full output on stdout, and the summaries too."""
    env = _stub_env(tmp_path, HACF_CHECK_VERBOSE="1", STUB_UV="fail")
    proc = _run(["bash", str(CHECK_SH)], **env)
    out = proc.stdout
    assert proc.returncode == 1, out + proc.stderr
    for name in ("uv", "py310"):
        assert f"STUB FULL OUTPUT {name}" in out and f"stub {name} line 50" in out
    assert "stub ruff" in out
    assert "      FAILED dev/tests/x/test_a.py::test_b" in out
    assert "STUB FULL OUTPUT uv" in (_run_dir(tmp_path) / "2-pytest-uv.log").read_text()


def test_check_sh_keeps_the_newest_ten_run_dirs(tmp_path):
    """12 old run dirs, a `notes` dir and an `old-run` symlink: 10 run dirs stay, the rest too."""
    logs = tmp_path / "logs"
    old = [f"2020010{d}T000000Z-{100 + h}" for d in range(1, 7) for h in (1, 2)]
    for name in old:
        (logs / name).mkdir(parents=True)
        (logs / name / "2-pytest-uv.log").write_text("old\n")
    (logs / "notes").mkdir()
    (logs / "notes" / "keep.txt").write_text("mine\n")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("mine\n")
    (logs / "old-run").symlink_to(target)
    (logs / "20200101T000000Z-9").symlink_to(target, target_is_directory=True)

    proc = _run(["bash", str(CHECK_SH)], **_stub_env(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runs = sorted(p.name for p in logs.iterdir() if p.is_dir() and not p.is_symlink())
    runs.remove("notes")
    assert len(runs) == 10
    assert runs[:9] == old[3:]  # the oldest three of 12+1 are gone, the new run kept
    assert runs[9] > old[-1]
    assert (logs / "notes" / "keep.txt").read_text() == "mine\n"
    assert (logs / "old-run").is_symlink() and (logs / "20200101T000000Z-9").is_symlink()
    assert (target / "keep.txt").read_text() == "mine\n"


def test_check_sh_header_documents_the_log_dir():
    """The header documents HACF_CHECK_VERBOSE, HACF_LOG_DIR, the retention and every-leg runs."""
    header = CHECK_SH.read_text(encoding="utf-8").split("set -euo pipefail", 1)[0]
    for needle in (
        "HACF_CHECK_VERBOSE=1",
        "HACF_LOG_DIR",
        "newest 10 run dirs",
        "every leg runs",
        "Live tests (marker `live`) never run here.",
    ):
        assert needle in header, needle


def test_xdist_group_needs_loadgroup():
    """Any xdist_group mark under dev/tests requires HACF_TEST_DIST=loadgroup (R5)."""
    this = Path(__file__).resolve()
    users = [
        path
        for path in sorted((REPO_ROOT / "dev" / "tests").rglob("*.py"))
        if path.resolve() != this and "xdist_group" in path.read_text(encoding="utf-8")
    ]
    if users:
        dist = re.search(r"^HACF_TEST_DIST=(\S+)$", CHECK_ENV.read_text(encoding="utf-8"), re.M)
        assert dist and dist.group(1) == "loadgroup", [str(p) for p in users]


def test_legacy_lint_baseline():
    """Ruff check and format --check pass on the 12 normalized files with their ignores."""
    for argv in (["ruff", "check"], ["ruff", "format", "--check"]):
        proc = _run(["uv", "run", "--locked", *argv, "--no-cache", "--", *LEGACY_SCRIPTS])
        assert proc.returncode == 0, proc.stdout + proc.stderr


SHIPPED_SCRIPTS = [
    *(
        f".agents/scripts/{name}"
        for name in (
            "hyper_update_recovery.py",
            "ceiling_enforcer.py",
            "heuristic_classifier.py",
            "override_manager.py",
            "meta_loader.py",
            "ceiling_resolver.py",
            "thinking_token_monitor.py",
        )
    ),
    *(
        f".agents/skills/hyper-publish/scripts/{name}"
        for name in (
            "generate_commit_message.py",
            "generate_pr_description.py",
            "setup_permissions.py",
        )
    ),
]


def test_shipped_lint_baseline():
    """Ruff check and format --check pass on the 10 shipped scripts F2 fixed, with no ignores."""
    for argv in (["ruff", "check"], ["ruff", "format", "--check"]):
        flags = ["--force-exclude", "--no-cache"]
        proc = _run(["uv", "run", "--locked", *argv, *flags, "--", *SHIPPED_SCRIPTS])
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pyproject_excludes_prds_from_ruff():
    """`[tool.ruff]` holds extend-exclude = ["PRDs/"] (F2: historical, non-code records)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[tool.ruff]\n", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^extend-exclude = \["PRDs/"\]$', section, flags=re.MULTILINE), section


def _ruff_files(repo) -> list[str]:
    """Source check_env.sh in repo's work tree and return hacf_ruff_files' NUL-separated paths."""
    proc = repo.run(["bash", "-c", f'source "{CHECK_ENV}"; hacf_ruff_files'])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "" or proc.stdout.endswith("\0"), repr(proc.stdout)
    return proc.stdout.split("\0")[:-1]


def _py_repo(repo):
    """Commit a.py, b.py, c.py and README.md on main; return the repo."""
    for name in ("a.py", "b.py", "c.py"):
        repo.write(name, "X = 1\n")
    repo.commit("python files")
    return repo


def test_ruff_files_without_main_lists_every_python_file(tmp_git_repo):
    """No `main` (branch trunk): every tracked .py plus untracked .py, never the .md."""
    repo = tmp_git_repo
    repo.write("a.py", "A = 1\n")
    repo.write("pkg/b.py", "B = 1\n")
    repo.commit("two python files")
    repo.git("branch", "-m", "main", "trunk")
    repo.write("new.py", "N = 1\n")
    assert _ruff_files(repo) == ["a.py", "new.py", "pkg/b.py"]


def test_ruff_files_on_main_lists_every_tracked_python_file(tmp_git_repo):
    """`main` checked out at HEAD (merge-base == HEAD): every tracked .py, not an empty list."""
    repo = _py_repo(tmp_git_repo)
    assert _ruff_files(repo) == ["a.py", "b.py", "c.py"]


def test_ruff_files_ahead_of_main_lists_the_changed_files(tmp_git_repo):
    """One commit ahead of main changing b.py and deleting c.py: b.py only."""
    repo = _py_repo(tmp_git_repo)
    repo.git("switch", "-q", "-c", "feature")
    repo.write("b.py", "X = 2\n")
    repo.git("rm", "-q", "c.py")
    repo.commit("change b, delete c", "b.py")  # git rm already staged c.py's deletion
    assert _ruff_files(repo) == ["b.py"]


def test_ruff_files_skips_a_python_file_deleted_in_the_work_tree(tmp_git_repo):
    """A tracked .py deleted in the work tree (not committed) is not listed."""
    repo = _py_repo(tmp_git_repo)
    (repo.path / "b.py").unlink()
    assert _ruff_files(repo) == ["a.py", "c.py"]


def test_ruff_files_fails_without_a_merge_base(tmp_git_repo):
    """A `main` that shares no merge-base with HEAD: status 1, as check.sh's leg expects."""
    repo = _py_repo(tmp_git_repo)
    repo.git("switch", "-q", "--orphan", "other")
    repo.commit("unrelated root")
    proc = repo.run(["bash", "-c", f'source "{CHECK_ENV}"; hacf_ruff_files'])
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout == ""


def test_check_sh_ruff_leg_force_excludes_and_never_vacuous():
    """Both ruff argvs pass --force-exclude; `nothing to lint` only in the changed-files branch."""
    text = CHECK_SH.read_text(encoding="utf-8")
    leg = text.split("# --- 4. ruff", 1)[1]
    ruff_calls = re.findall(r"uv run --locked ruff (check|format --check)(.*)", leg)
    assert [call[0] for call in ruff_calls] == ["check", "format --check"], ruff_calls
    for _, rest in ruff_calls:
        assert rest.startswith(" --force-exclude -- "), rest
    assert text.count("nothing to lint") == 1
    branches = leg.split("elif [[ ${#ruff_files[@]} -eq 0 ]]; then", 1)
    assert len(branches) == 2, "the changed-files empty branch moved"
    assert "nothing to lint" not in branches[0]
    assert "nothing to lint" in branches[1].split("else", 1)[0]
    assert "all tracked Python files (${#ruff_files[@]}): fresh clone or HEAD on main" in leg


def test_per_file_ignores_cover_only_legacy_scripts():
    """The per-file-ignores table names exactly the 12 legacy scripts (R13)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[tool.ruff.lint.per-file-ignores]", 1)[1].split("\n[", 1)[0]
    paths = re.findall(r'^"([^"]+)"\s*=', section, flags=re.MULTILINE)
    assert sorted(paths) == sorted(LEGACY_SCRIPTS)


def test_state_ignored():
    """`git check-ignore` reports every S00 state path as ignored by the repository .gitignore."""
    isolated = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    for path in STATE_PATHS:
        proc = _run(["git", "check-ignore", "--no-index", "-v", path], **isolated)
        assert proc.returncode == 0, f"{path} is not ignored"
        assert proc.stdout.startswith(".gitignore:"), proc.stdout
    for path in ("dev/tests/conftest.py", "dev/tests/fixtures/ledger_v1_real_broken.yml"):
        proc = _run(["git", "check-ignore", "--no-index", path], **isolated)
        assert proc.returncode == 1, f"{path} must not be ignored"
