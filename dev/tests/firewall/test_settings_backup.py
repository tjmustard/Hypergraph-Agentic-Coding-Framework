"""KI-6: `--written-sha` skips a settings.json backup only for bytes this same run wrote.

KI-6 must never lose the user's only settings backup. Every test works in a temp git repository.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import REPO_ROOT, SCRIPTS_DIR

SHIPPED = (REPO_ROOT / ".agentignore").read_text(encoding="utf-8")
USER = {"env": {"MY_VAR": "1"}, "permissions": {"allow": ["Bash(ls *)"]}}
FRAGMENT = {"permissions": {"allow": ["Bash(touch *)"]}}
REL = ".claude/settings.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def firewall(tmp_git_repo, run_script):
    """Return a function that runs hyper_firewall.py in the temp repository."""
    tmp_git_repo.write(".agentignore", SHIPPED)
    tmp_git_repo.write("fragment.json", json.dumps(FRAGMENT))

    def run(*args: str):
        return run_script(SCRIPTS_DIR / "hyper_firewall.py", *args, cwd=tmp_git_repo.path)

    return run


def _backups(repo) -> list[Path]:
    root = repo.path / ".agents/.backup"
    return sorted(p for p in root.rglob("settings.json")) if root.exists() else []


def test_a_fresh_install_makes_no_backup(firewall, tmp_git_repo):
    """(a) No pre-existing file: merge then render with the recorded sha backs up nothing."""
    settings = tmp_git_repo.path / REL
    assert firewall("merge-settings", "fragment.json").returncode == 0
    first = _sha(settings)
    proc = firewall("render", "--ides", "claude", "--written-sha", first)
    assert proc.returncode == 0, proc.stderr
    assert "no backup: .claude/settings.json (this run wrote these bytes)" in proc.stdout
    assert _backups(tmp_git_repo) == []


def test_b_a_users_file_is_backed_up_once_byte_equal(firewall, tmp_git_repo):
    """(b) KI-6 must never lose the user's only settings backup: one backup, the original bytes."""
    original = json.dumps(USER).encode()
    tmp_git_repo.write(REL, original.decode())
    settings = tmp_git_repo.path / REL
    assert firewall("merge-settings", "fragment.json").returncode == 0
    shas = ["--written-sha", _sha(settings)]
    assert firewall("merge-settings", "fragment.json", *shas).returncode == 0
    assert firewall("render", "--ides", "claude", *shas).returncode == 0
    backups = _backups(tmp_git_repo)
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_c_two_recorded_shas_cover_two_writes(firewall, tmp_git_repo):
    """(c) A repeatable flag: the file after write 2 matches, after write 1 would also match."""
    original = json.dumps(USER).encode()
    tmp_git_repo.write(REL, original.decode())
    settings = tmp_git_repo.path / REL
    firewall("merge-settings", "fragment.json")
    one = _sha(settings)
    tmp_git_repo.write("fragment2.json", json.dumps({"env": {"TWO": "2"}}))
    assert firewall("merge-settings", "fragment2.json", "--written-sha", one).returncode == 0
    two = _sha(settings)
    assert one != two
    proc = firewall("render", "--ides", "claude", "--written-sha", one, "--written-sha", two)
    assert proc.returncode == 0, proc.stderr
    backups = _backups(tmp_git_repo)
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_d_an_edit_between_writes_is_backed_up(firewall, tmp_git_repo):
    """(d) The user edits the file after HACF's write: the stale sha does not match, so back up."""
    settings = tmp_git_repo.path / REL
    firewall("merge-settings", "fragment.json")
    stale = _sha(settings)
    edited = json.loads(settings.read_text(encoding="utf-8"))
    edited["env"] = {"MINE": "precious"}
    settings.write_text(json.dumps(edited), encoding="utf-8")
    edited_bytes = settings.read_bytes()
    assert firewall("render", "--ides", "claude", "--written-sha", stale).returncode == 0
    backups = _backups(tmp_git_repo)
    assert len(backups) == 1 and backups[0].read_bytes() == edited_bytes


def test_e_a_non_matching_sha_never_skips(firewall, tmp_git_repo):
    """(e) A well-formed sha that is not the file's still backs up the user's file."""
    original = json.dumps(USER).encode()
    tmp_git_repo.write(REL, original.decode())
    proc = firewall("render", "--ides", "claude", "--written-sha", "0" * 64)
    assert proc.returncode == 0, proc.stderr
    assert "no backup" not in proc.stdout
    backups = _backups(tmp_git_repo)
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_f_without_the_flag_every_write_backs_up(firewall, tmp_git_repo):
    """(f) Unchanged behaviour: no flag, one backup per write of an existing file."""
    firewall("merge-settings", "fragment.json")
    assert firewall("render", "--ides", "claude").returncode == 0
    assert len(_backups(tmp_git_repo)) == 1  # the file the merge wrote, backed up by render


@pytest.mark.parametrize("value", ["abc", "G" * 64, "a" * 63, "A" * 64, ""])
def test_g_a_malformed_sha_exits_2_and_writes_nothing(firewall, tmp_git_repo, value):
    """(g) A malformed --written-sha is a usage error before anything is written."""
    proc = firewall("render", "--ides", "claude", "--written-sha", value)
    assert proc.returncode == 2
    assert "written-sha" in proc.stderr
    assert not (tmp_git_repo.path / REL).exists()
    assert _backups(tmp_git_repo) == []
    merge = firewall("merge-settings", "fragment.json", "--written-sha", value)
    assert merge.returncode == 2 and not (tmp_git_repo.path / REL).exists()
