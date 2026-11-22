"""Tests for .agents/scripts/pre-commit: staged-only lint and the forbidden-path rules."""

from __future__ import annotations

import hashlib
import os
import shutil

import pytest
from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".agents" / "scripts" / "pre-commit"
REAL_RUFF = REPO_ROOT / ".venv" / "bin" / "ruff"
LINT_ERROR = "import os\n"  # F401: unused import
CLEAN = "VALUE = 1\n"
SKIP_WARNING = (
    "⚠️  ruff not found (.venv/bin/ruff or PATH) — skipping lint check. "
    "HACF never downloads ruff at commit time."
)


def run_hook(repo, **env):
    """Run the repository's pre-commit hook the way git does: from the work tree root."""
    return repo.run(["bash", str(HOOK)], **env)


def require_real_ruff():
    """Skip the calling test when this repository has no .venv/bin/ruff to wrap."""
    if not (REAL_RUFF.is_file() and os.access(REAL_RUFF, os.X_OK)):
        pytest.skip(f"{REAL_RUFF} does not exist; run `uv sync` in the HACF dev repo first")


def write_script(path, body):
    """Write an executable bash script at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def add_venv_ruff(repo):
    """Create .venv/bin/ruff in repo as a two-line wrapper around this project's ruff."""
    require_real_ruff()
    write_script(repo.path / ".venv" / "bin" / "ruff", f'exec "{REAL_RUFF}" "$@"\n')


@pytest.fixture
def lint_repo(tmp_git_repo):
    """Return tmp_git_repo with this project's pyproject.toml committed and a .venv/bin/ruff.

    The pyproject.toml carries the ruff configuration; no uv.lock is copied, because the
    hook never runs uv. The .venv/bin/ruff wrapper execs this repository's own ruff.
    """
    shutil.copyfile(REPO_ROOT / "pyproject.toml", tmp_git_repo.path / "pyproject.toml")
    tmp_git_repo.commit("add the project files", "pyproject.toml")
    add_venv_ruff(tmp_git_repo)
    return tmp_git_repo


@pytest.fixture
def shims(tmp_path):
    """Return (shim dir, log file): `uv` and `uvx` shims that log their argv and exit 0."""
    shim_dir = tmp_path / "shims"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    for name in ("uv", "uvx"):
        write_script(shim_dir / name, f'echo "{name} $*" >> "{log}"\nexit 0\n')
    return shim_dir, log


def shim_path(shim_dir):
    """Return a PATH whose first entry is shim_dir, followed by the inherited PATH."""
    return os.pathsep.join([str(shim_dir), os.environ.get("PATH", "")])


def tree_hashes(root):
    """Return the sorted (relative path, sha256) of every file under root."""
    return sorted(
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*")
        if path.is_file()
    )


def test_pre_commit_staged_only(lint_repo):
    """An unstaged file with a lint error is ignored; once it is staged, the hook fails."""
    lint_repo.write("legacy.py", LINT_ERROR)
    lint_repo.commit("committed lint debt is not re-linted", "legacy.py")
    lint_repo.write("bad.py", LINT_ERROR)
    lint_repo.write("dir with space/good.py", CLEAN)
    lint_repo.git("add", "dir with space/good.py")

    proc = run_hook(lint_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 staged file(s)" in proc.stdout

    lint_repo.git("add", "bad.py")
    proc = run_hook(lint_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Lint failed" in proc.stdout
    assert "bad.py" in proc.stdout
    assert "legacy.py" not in proc.stdout


def test_pre_commit_format_check_staged(lint_repo):
    """A staged file that ruff format would change fails the format check."""
    lint_repo.write("ugly.py", "VALUE = {'a':1}\n")
    lint_repo.git("add", "ugly.py")
    proc = run_hook(lint_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Format check failed" in proc.stdout


def test_pre_commit_no_python_staged(tmp_git_repo):
    """With no staged *.py file the hook passes without running ruff."""
    tmp_git_repo.write("bad.py", LINT_ERROR)
    tmp_git_repo.write("notes.md", "# Notes\n")
    tmp_git_repo.git("add", "notes.md")
    proc = run_hook(tmp_git_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ruff" not in proc.stdout


def test_pre_commit_blocks_spec_process(tmp_git_repo):
    """A staged spec/process/ file is blocked."""
    tmp_git_repo.write("spec/process/x.md", "# Process notes\n")
    tmp_git_repo.git("add", "spec/process/x.md")
    proc = run_hook(tmp_git_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "COMMIT BLOCKED" in proc.stdout
    assert "spec/process/x.md" in proc.stdout


def test_pre_commit_allows_spec_handoffs(tmp_git_repo):
    """A staged spec/handoffs/ file is allowed (D9)."""
    tmp_git_repo.write("spec/handoffs/x.md", "# Handoff\n")
    tmp_git_repo.git("add", "spec/handoffs/x.md")
    proc = run_hook(tmp_git_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "COMMIT BLOCKED" not in proc.stdout


def test_pre_commit_uses_venv_ruff(lint_repo, shims):
    """Test A: .venv/bin/ruff lints staged files; uv and uvx are never called."""
    shim_dir, log = shims
    lint_repo.write("bad.py", LINT_ERROR)
    lint_repo.git("add", "bad.py")
    proc = run_hook(lint_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Lint failed" in proc.stdout
    assert ".venv/bin/ruff" in proc.stdout

    lint_repo.git("rm", "-q", "--cached", "bad.py")
    lint_repo.write("good.py", CLEAN)
    lint_repo.git("add", "good.py")
    proc = run_hook(lint_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Lint passed" in proc.stdout
    assert log.read_text(encoding="utf-8") == ""


def test_pre_commit_uses_path_ruff(tmp_git_repo, shims):
    """Test B: without .venv, a ruff on PATH is used; .venv is not created."""
    require_real_ruff()
    shim_dir, log = shims
    write_script(shim_dir / "ruff", f'exec "{REAL_RUFF}" "$@"\n')
    shutil.copyfile(REPO_ROOT / "pyproject.toml", tmp_git_repo.path / "pyproject.toml")
    tmp_git_repo.commit("add the project files", "pyproject.toml")
    tmp_git_repo.write("bad.py", LINT_ERROR)
    tmp_git_repo.git("add", "bad.py")
    proc = run_hook(tmp_git_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Lint failed. Run: ruff check" in proc.stdout
    assert "using ruff" in proc.stdout
    assert not (tmp_git_repo.path / ".venv").exists()
    assert log.read_text(encoding="utf-8") == ""


def test_pre_commit_skips_without_ruff(tmp_git_repo, shims):
    """Test C: with uv but no uv.lock, .venv or ruff, the hook warns once and passes."""
    shim_dir, log = shims
    tool_dirs = {os.path.dirname(shutil.which(tool)) for tool in ("git", "bash")}
    path_dirs = [str(shim_dir), *sorted(tool_dirs)]
    for directory in path_dirs:
        assert not os.path.exists(os.path.join(directory, "ruff")), directory
    tmp_git_repo.write("bad.py", LINT_ERROR)
    tmp_git_repo.git("add", "bad.py")
    proc = run_hook(tmp_git_repo, PATH=os.pathsep.join(path_dirs))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines() == [SKIP_WARNING]
    assert not (tmp_git_repo.path / ".venv").exists()
    assert not (tmp_git_repo.path / "uv.lock").exists()
    assert log.read_text(encoding="utf-8") == ""


def test_pre_commit_leaves_venv_and_lock_untouched(lint_repo, shims):
    """Test D: a passing run changes neither uv.lock nor any file under .venv."""
    shim_dir, log = shims
    lock = lint_repo.write("uv.lock", "# any content\n")
    venv = lint_repo.path / ".venv"
    lock_before = hashlib.sha256(lock.read_bytes()).hexdigest()
    venv_before = tree_hashes(venv)
    lint_repo.write("good.py", CLEAN)
    lint_repo.git("add", "good.py")
    proc = run_hook(lint_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == lock_before
    assert tree_hashes(venv) == venv_before
    assert log.read_text(encoding="utf-8") == ""


def test_pre_commit_reports_ruff_failure(tmp_git_repo, shims):
    """Test E: ruff exiting 2 is reported as a tool failure, not a lint failure."""
    shim_dir, _log = shims
    write_script(tmp_git_repo.path / ".venv" / "bin" / "ruff", "exit 2\n")
    tmp_git_repo.write("good.py", CLEAN)
    tmp_git_repo.git("add", "good.py")
    proc = run_hook(tmp_git_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ruff could not run (exit 2)" in proc.stdout
    assert "Lint failed" not in proc.stdout


# ---------------------------------------------------------------------------
# OPR.0.6.1.8 (E4, US-016): the project's ruff exclusions and the hacf.lint switch
# ---------------------------------------------------------------------------

EXCLUDING_PYPROJECT = '[tool.ruff]\nextend-exclude = ["docs/prd"]\n'  # no force-exclude
LINT_SKIPPED = "⚠️  lint skipped (git config hacf.lint=false)"


@pytest.fixture
def excluding_repo(tmp_git_repo):
    """Return tmp_git_repo with a root ruff config excluding docs/prd, and a .venv/bin/ruff."""
    tmp_git_repo.write("pyproject.toml", EXCLUDING_PYPROJECT)
    tmp_git_repo.commit("add the ruff config", "pyproject.toml")
    add_venv_ruff(tmp_git_repo)
    return tmp_git_repo


def stage(repo, rel, text):
    """Write rel in repo and stage it."""
    repo.write(rel, text)
    repo.git("add", rel)


def test_pre_commit_honours_extend_exclude(excluding_repo):
    """(a) A staged F401 file under an extend-excluded dir passes; one under src/ fails."""
    stage(excluding_repo, "docs/prd/bad.py", LINT_ERROR)
    proc = run_hook(excluding_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Lint failed" not in proc.stdout

    stage(excluding_repo, "src/bad.py", LINT_ERROR)
    proc = run_hook(excluding_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Lint failed" in proc.stdout
    assert "src/bad.py" in proc.stdout
    assert "docs/prd/bad.py" not in proc.stdout


def test_pre_commit_format_check_honours_extend_exclude(excluding_repo):
    """(b) A staged format-only violation under an extend-excluded dir passes."""
    stage(excluding_repo, "docs/prd/ugly.py", "VALUE = {'a':1}\n")
    proc = run_hook(excluding_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Format check failed" not in proc.stdout


def test_pre_commit_lint_switch_off(tmp_git_repo, shims):
    """(c) hacf.lint=false skips ruff with one line; spec/process/ is still blocked."""
    shim_dir, log = shims
    ruff_shim = tmp_git_repo.path / ".venv" / "bin" / "ruff"
    write_script(ruff_shim, f'echo "ruff $*" >> "{log}"\nexit 1\n')
    tmp_git_repo.git("config", "hacf.lint", "false")
    stage(tmp_git_repo, "bad.py", LINT_ERROR)
    proc = run_hook(tmp_git_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines() == [LINT_SKIPPED]
    assert log.read_text(encoding="utf-8") == ""

    stage(tmp_git_repo, "spec/process/x.md", "# Process notes\n")
    proc = run_hook(tmp_git_repo, PATH=shim_path(shim_dir))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "COMMIT BLOCKED" in proc.stdout
    assert LINT_SKIPPED not in proc.stdout
    assert log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("value", [None, "true", "no", "0"])
def test_pre_commit_lint_switch_on(lint_repo, value):
    """(d) hacf.lint unset, true or any value other than false keeps the lint on."""
    if value is not None:
        lint_repo.git("config", "hacf.lint", value)
    stage(lint_repo, "bad.py", LINT_ERROR)
    proc = run_hook(lint_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Lint failed" in proc.stdout
    assert LINT_SKIPPED not in proc.stdout


def test_pre_commit_nested_config_with_own_exclude(excluding_repo):
    """(e) Task 1 variant D: a nested config with exclude = ["*"] is excluded (`ruff check .`)."""
    excluding_repo.write("docs/prd/ref/pyproject.toml", "[tool.ruff]\nline-length = 88\n")
    excluding_repo.write("docs/prd/ref/.ruff.toml", 'exclude = ["*"]\n')
    stage(excluding_repo, "docs/prd/ref/bad.py", LINT_ERROR)
    proc = run_hook(excluding_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Lint failed" not in proc.stdout


def test_pre_commit_known_limit_k11_nested_config_is_linted(excluding_repo):
    """(e) Task 1 variant B, known limit K11 (R19): a bare nested ruff config is still linted.

    `ruff check .` prunes docs/prd, but for an explicit path ruff takes the nested config's
    (empty) exclusions. If this starts passing, K11 is closed: update the CHANGELOG known issue.
    """
    excluding_repo.write("docs/prd/ref/pyproject.toml", "[tool.ruff]\nline-length = 88\n")
    stage(excluding_repo, "docs/prd/ref/bad.py", LINT_ERROR)
    proc = run_hook(excluding_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "docs/prd/ref/bad.py" in proc.stdout
