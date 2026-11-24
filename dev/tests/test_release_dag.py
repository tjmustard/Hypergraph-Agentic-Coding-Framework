"""S15b release check that holds on any clone: the untracked workbench (R54).

The DAG and migrated-proof checks read HACF's private mission records and live in
private_records/test_release_dag_records.py.
"""

from __future__ import annotations

import subprocess

from conftest import REPO_ROOT, clean_env


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        env=clean_env(GIT_NO_REPLACE_OBJECTS="1"),
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace')}")
    return proc


def test_release_tree_untracked_paths():
    """Test 5: spec/, tests/ and a root .gitattributes are not tracked."""
    assert _git("ls-files", "--", "spec", "tests").stdout == b""
    assert _git("ls-files", "--", ".gitattributes").stdout == b""
