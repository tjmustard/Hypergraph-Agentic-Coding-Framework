"""Tests for slice 35 (R67): HACF bytecode and the lore merge-driver config (D1, D2).

D1: the install wrote `.agents/scripts/__pycache__/*.pyc` that the managed `.gitignore` block did
not ignore, and the uninstaller left it. D2: the uninstaller left `merge.hacf-lore` and
`merge.hacf-generated` in `.git/config` and an empty `<git-common-dir>/info/attributes`.

The harness sets PYTHONDONTWRITEBYTECODE=1 (conftest.py `git_env`), which is why no earlier test
saw D1, so every install and uninstall run here removes it (`NO_DONTWRITE`). Every run happens in
a temp project against the session's temp upstream; nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .conftest import REPO_ROOT, git, install_env, snapshot
from .test_uninstall import attributes_file, run_uninstaller

sys.path.insert(0, str(REPO_ROOT / ".agents" / "scripts"))
import hyper_blocks  # noqa: E402
import hyper_lore  # noqa: E402
import hyper_scaffold  # noqa: E402

NO_DONTWRITE = {"PYTHONDONTWRITEBYTECODE": None}
OLD_GITIGNORE_LINES = (
    ".agents/state/",
    ".agents/.backup/",
    ".agents/logs/",
    "/exhaust/",
    "/.openrig/",
)
SECTIONS = ("merge.hacf-lore", "merge.hacf-generated")


def git_proc(project: Path, scratch: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in the project with the test environment; return the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=project,
        env=install_env(scratch, "file:///nonexistent", NO_DONTWRITE),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def hacf_driver_config(project: Path, scratch: Path) -> subprocess.CompletedProcess:
    """Return `git config --local --get-regexp` of the HACF merge-driver keys."""
    return git_proc(project, scratch, "config", "--local", "--get-regexp", r"^merge\.hacf-")


def local_config(project: Path, scratch: Path) -> str:
    """Return `git config --local --list` for the project."""
    proc = git_proc(project, scratch, "config", "--local", "--list")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def install(run_install, project: Path) -> None:
    """Install `-y --ides=claude` with bytecode writes allowed; the run must exit 0."""
    run = run_install(project, "-y", "--ides=claude", env=NO_DONTWRITE)
    assert run.returncode == 0, run.output


def uninstall(scratch: Path, upstream, project: Path, *args: str):
    """Run the uninstaller with bytecode writes allowed."""
    return run_uninstaller(scratch, upstream.url, project, *args, env=NO_DONTWRITE)


def write_real_bytecode(project: Path, scratch: Path) -> None:
    """Run a project script without PYTHONDONTWRITEBYTECODE so real .pyc files appear."""
    env = install_env(scratch, "file:///nonexistent", NO_DONTWRITE)
    subprocess.run(
        [sys.executable, ".agents/scripts/hyper_lore.py", "list", "--json"],
        cwd=project,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    cache = project / ".agents/scripts/__pycache__"
    assert cache.is_dir() and list(cache.glob("*.pyc")), "no bytecode was written"


def pycache_paths(project: Path) -> list[str]:
    """Return every path under the project, outside .git/, with a __pycache__ component."""
    found = []
    for base, dirs, _files in os.walk(project):
        rel = Path(base).relative_to(project)
        if rel.parts[:1] == (".git",):
            dirs[:] = []
            continue
        for name in dirs:
            if name == "__pycache__":
                found.append((rel / name).as_posix())
    return found


def test_install_writes_no_bytecode(project: Path, run_install) -> None:
    """Test 2: after a fresh install, no path outside .git/ has a __pycache__ component."""
    install(run_install, project)
    assert pycache_paths(project) == []


def test_ignore_line_is_narrow(project: Path, run_install, scratch: Path) -> None:
    """Test 1: the line ignores HACF's bytecode and not the user's own __pycache__."""
    install(run_install, project)
    hacf = (
        ".agents/scripts/__pycache__/a.cpython-310.pyc",
        ".agents/skills/hyper-publish/scripts/__pycache__/b.cpython-310.pyc",
    )
    user = "src/__pycache__/c.cpython-310.pyc"
    for rel in (*hacf, user):
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_bytes(b"")
    for rel in hacf:
        assert git_proc(project, scratch, "check-ignore", "-q", rel).returncode == 0, rel
    assert git_proc(project, scratch, "check-ignore", "-q", user).returncode == 1


def test_upgrade_gains_ignore_line(project: Path, run_install, scratch: Path) -> None:
    """Test 3: an install with the five-line block (0deecd8) gains the line on an upgrade."""
    install(run_install, project)
    gitignore = project / ".gitignore"
    body = "".join(line + "\n" for line in OLD_GITIGNORE_LINES)
    hyper_blocks.upsert(gitignore, hyper_blocks.GITIGNORE_BLOCK_ID, body)

    def outside(data: bytes) -> bytes:
        doc = hyper_blocks.parse_doc(data)
        result = hyper_blocks.render_remove(doc, hyper_blocks.HASH, hyper_blocks.GITIGNORE_BLOCK_ID)
        assert result is not None
        return result.to_bytes()

    before = outside(gitignore.read_bytes())
    run = run_install(project, "-y", env=NO_DONTWRITE)
    assert run.returncode == 0, run.output
    data = gitignore.read_bytes()
    doc = hyper_blocks.parse_doc(data)
    block = hyper_blocks.find_block(
        hyper_blocks.scan(doc, hyper_blocks.HASH), hyper_blocks.GITIGNORE_BLOCK_ID
    )
    assert block is not None
    lines = [line.rstrip(b"\r\n").decode() for line in hyper_blocks.body_of(doc, block)]
    assert tuple(lines) == hyper_scaffold.GITIGNORE_LINES
    assert len(hyper_scaffold.GITIGNORE_LINES) == 6
    assert outside(data) == before


def test_uninstall_removes_hacf_bytecode(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Test 4: --dry-run lists HACF's __pycache__ and changes nothing; -y deletes and prunes it."""
    install(run_install, project)
    write_real_bytecode(project, scratch)
    before = snapshot(project)
    run = uninstall(scratch, upstream, project, "--dry-run")
    assert run.returncode == 0, run.output
    assert "would delete .agents/scripts/__pycache__/" in run.output
    assert snapshot(project) == before
    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert not (project / ".agents/scripts").exists()


def test_uninstall_keeps_mixed_pycache(project: Path, run_install, scratch: Path, upstream) -> None:
    """Test 5: a __pycache__ holding anything but *.pyc files is kept and reported."""
    install(run_install, project)
    write_real_bytecode(project, scratch)
    notes = project / ".agents/scripts/__pycache__/notes.txt"
    notes.write_text("mine\n", encoding="utf-8")
    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert notes.is_file()
    assert "kept (not only HACF bytecode): .agents/scripts/__pycache__/" in run.output


def test_uninstall_removes_merge_drivers(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Test 6: both sections and the HACF-only info/attributes go; other local config stays."""
    assert git_proc(project, scratch, "config", "--local", "user.own.key", "x").returncode == 0
    l0 = local_config(project, scratch)
    install(run_install, project)
    proc = hacf_driver_config(project, scratch)
    assert proc.returncode == 0, proc.stderr
    keys = sorted(line.split(" ", 1)[0] for line in proc.stdout.splitlines())
    assert keys == sorted(key for key, _ in hyper_lore.DRIVER_CONFIG)

    installed = local_config(project, scratch)
    run = uninstall(scratch, upstream, project, "--dry-run")
    assert run.returncode == 0, run.output
    for section in SECTIONS:
        assert f"git config section {section}" in run.output
    assert local_config(project, scratch) == installed

    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert hacf_driver_config(project, scratch).returncode == 1
    assert local_config(project, scratch) == l0
    assert not attributes_file(project).exists()
    assert "deleted (it held only the HACF block)" in run.output


def test_uninstall_keeps_attributes_with_user_lines(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Test 7: an info/attributes with the user's own lines is kept, byte for byte."""
    attributes = attributes_file(project)
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_bytes(b"*.bin binary\n")
    install(run_install, project)
    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert attributes.read_bytes() == b"*.bin binary\n"


def test_uninstall_keeps_changed_driver(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Test 8: a section changed since install is kept and reported; the other one goes."""
    install(run_install, project)
    proc = git_proc(project, scratch, "config", "--local", "merge.hacf-lore.driver", "my own")
    assert proc.returncode == 0, proc.stderr
    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    proc = git_proc(project, scratch, "config", "--local", "--get", "merge.hacf-lore.driver")
    assert proc.stdout.strip() == "my own"
    assert (
        git_proc(
            project, scratch, "config", "--local", "--get-regexp", r"^merge\.hacf-generated\."
        ).returncode
        == 1
    )
    assert "kept (changed since HACF installed it): git config merge.hacf-lore" in run.output


def test_legacy_uninstall_removes_merge_drivers(v059_project, scratch: Path, upstream) -> None:
    """Test 9: without a manifest, hand-set DRIVER_CONFIG sections are removed as well."""
    project = v059_project("default")
    env = install_env(scratch, "file:///nonexistent", NO_DONTWRITE)
    for key, value in hyper_lore.DRIVER_CONFIG:
        git(["config", "--local", key, value], project, env)
    run = uninstall(scratch, upstream, project, "-y")
    assert run.returncode == 0, run.output
    assert hacf_driver_config(project, scratch).returncode == 1
