"""Tests for dev/scripts/v060_gate.py, run as a subprocess against throwaway repositories.

The repositories are built under tmp_path (never this one) from the gate's own selftest
builders, imported read-only, so the fixtures follow the gate's schema without copying it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

GATE_PATH = REPO_ROOT / "dev" / "scripts" / "v060_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("v060_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


gate = _load_gate()
HARNESS = gate._SELFTEST_HARNESS  # MiniPRD_00_Harness.md, slice S00
CORE = gate._SELFTEST_CORE  # MiniPRD_01_Ledger_Core.md, slice S01a


class Gate:
    """Runs the gate against one throwaway repository with an isolated environment."""

    def __init__(self, repo, tmp_path) -> None:
        """Build the env: no fixed commit dates, a stub claude, backups under tmp_path."""
        self.repo = repo
        self.cwd = tmp_path
        stub = tmp_path / "bin"
        stub.mkdir()
        claude = stub / "claude"
        claude.write_text("#!/bin/sh\necho '2.1.305 (test claude stub)'\n", encoding="utf-8")
        claude.chmod(0o755)
        self.env = {key: val for key, val in repo.env.items() if not key.endswith("_DATE")}
        self.env["HACF_BACKUP_ROOT"] = str(tmp_path / "backups")
        self.env["PATH"] = f"{stub}{os.pathsep}{self.env.get('PATH', '')}"

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Run `v060_gate.py --repo <repo> <args>` and return the completed process."""
        return subprocess.run(
            [sys.executable, str(GATE_PATH), "--repo", str(self.repo.path), *args],
            cwd=self.cwd,
            env=self.env,
            input="",
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    def head(self) -> str:
        """Return HEAD's sha."""
        return self.repo.git("rev-parse", "HEAD").strip()

    def ref(self, name: str) -> str:
        """Return the sha ref name holds, or '' when it does not exist."""
        return self.repo.git("for-each-ref", "--format=%(objectname)", name).strip()

    def archive(self, name: str) -> str:
        """Move a compiled MiniPRD to spec/archive/<name>_AUDITED.md, as the audit does."""
        target = f"spec/archive/{name[: -len('.md')]}_AUDITED.md"
        os.replace(self.repo.path / gate.COMPILED_DIR / name, self.repo.path / target)
        return target


@pytest.fixture
def compiled(tmp_git_repo, tmp_path):
    """Return a Gate for a pre-migration fixture repository holding fresh resolver output."""
    repo = tmp_git_repo
    repo.git("checkout", "-q", "-b", gate.GATE_BRANCH)
    for rel, content in gate._selftest_files().items():
        repo.write(rel, content)
    repo.git("add", "-A")
    repo.git("add", "-f", "--", *gate.POST_RESOLVE_ADD_DIRS)
    repo.git("commit", "-q", "-m", "fixture: pre-migration state")
    for rel, content in gate._selftest_resolver_output().items():
        repo.write(rel, content)
    return Gate(repo, tmp_path)


@pytest.fixture
def resolved(compiled):
    """Return the compiled fixture after a successful post-resolve."""
    proc = compiled.run("post-resolve", "--expect", "3")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return compiled


def test_v060_gate_finish_trailer(resolved):
    """Finish commits the slice with an `HACF-Slice: <SID>` trailer and records its ref."""
    resolved.repo.write("src/app.py", "VALUE = 2\n")
    resolved.repo.write("dev/tests/test_harness.py", "def test_ok():\n    assert True\n")
    audited = resolved.archive(HARNESS)
    proc = resolved.run("finish", "S00", "--miniprd", audited)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    head = resolved.head()
    trailer = resolved.repo.git("log", "-1", "--format=%(trailers:key=HACF-Slice,valueonly)")
    assert trailer.strip() == "S00"
    assert resolved.ref(f"{gate.SLICE_REF_PREFIX}S00") == head
    assert resolved.repo.git("status", "--porcelain") == ""


def test_v060_gate_scope_report(resolved):
    """A path outside the block's files globs is reported and finish exits 1."""
    resolved.repo.write("src/app.py", "VALUE = 2\n")
    resolved.repo.write("docs/other.md", "# Other\n\nEdited outside the slice scope.\n")
    audited = resolved.archive(HARNESS)
    head = resolved.head()
    proc = resolved.run("finish", "S00", "--miniprd", audited)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "1 changed path(s) match no files glob in the block" in output
    assert "docs/other.md" in output
    assert "src/app.py" not in output
    assert resolved.head() == head
    assert resolved.ref(f"{gate.SLICE_REF_PREFIX}S00") == ""


@pytest.mark.parametrize(
    ("ledger", "needle"),
    [
        (
            {"drop_node": "gamma"},
            "node gamma was removed but is not listed in removes_nodes",
        ),
        (
            {"drop_key": ("beta", "status")},
            "node beta lost key(s) present at the scope base",
        ),
    ],
)
def test_v060_gate_ledger_non_regression(resolved, ledger, needle):
    """A dropped ledger node id or per-node key is refused."""
    resolved.repo.write("src/app.py", "VALUE = 2\n")
    resolved.repo.write(gate.LEDGER, gate._selftest_ledger(**ledger))
    audited = resolved.archive(HARNESS)
    head = resolved.head()
    proc = resolved.run("finish", "S00", "--miniprd", audited)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert needle in output
    assert resolved.head() == head


def test_v060_gate_post_resolve_checks(compiled):
    """Post-resolve refuses a MiniPRD whose hacf-bootstrap block is malformed."""
    block = dict(gate._selftest_blocks()[CORE])
    block["extra"] = "x"
    del block["oracle"]
    compiled.repo.write(f"{gate.COMPILED_DIR}/{CORE}", gate._selftest_miniprd(block))
    head = compiled.head()
    proc = compiled.run("post-resolve", "--expect", "3")
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "unexpected key(s): extra" in output
    assert "missing key(s): oracle" in output
    assert compiled.head() == head
    assert compiled.ref(f"{gate.REF_ROOT}/post-resolve") == ""
