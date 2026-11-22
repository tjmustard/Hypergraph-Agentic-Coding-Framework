"""Tests for dev/scripts/check.sh, the R13 lint baseline and the S00 state ignores."""

from __future__ import annotations

import re
import subprocess
import sys

from conftest import REPO_ROOT, clean_env

CHECK_SH = REPO_ROOT / "dev" / "scripts" / "check.sh"
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
    assert "cannot import pytest and yaml" in output
    assert "This is a failure, not a skip" in output
    assert "[2/4]" not in output, "check.sh ran a pytest leg after the preflight failed"


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
