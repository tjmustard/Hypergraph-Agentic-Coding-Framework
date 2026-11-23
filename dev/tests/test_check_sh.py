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
    assert "[2/4]" not in output, "check.sh ran a pytest leg after the preflight failed"


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
    assert "[2/4]" not in output, "check.sh ran a pytest leg with its temp inside the tree"
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
    assert "[2/4]" not in output, "check.sh ran a pytest leg with an invalid worker count"


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
