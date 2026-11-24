"""Harness for the HACF-install.sh tests (S12b Tasks 1-4).

Every installer run happens in a temp project against a temp upstream built from this working
tree (uncommitted edits included), with failing download shims first on PATH, no controlling
terminal and, when the host allows it, no network (`unshare -rn`). Nothing is written inside the
HACF checkout.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "HACF-install.sh"
BUILDER = REPO_ROOT / "dev" / "tests" / "fixtures" / "build_user_project_v059.py"
SHIMS = ("curl", "wget", "pip", "pip3", "uv", "uvx", "npx", "npm")
DEFAULT_URL = "https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git"


def git_env(scratch: Path, **extra: str) -> dict[str, str]:
    """Return an environment with no outer git state, no user git config and a fixed identity."""
    empty = scratch / "gitconfig"
    if not empty.exists():
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "UV_", "HACF_"))
        and key not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(empty),
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="HACF Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="HACF Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    env.update(extra)
    return env


def git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Run git with an argv list and return stdout; fail the test on a non-zero exit."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Upstream:
    """A temp upstream: a bare clone of a commit built from the working tree."""

    bare: Path
    sha: str

    @property
    def url(self) -> str:
        """Return the file:// URL the installer clones."""
        return f"file://{self.bare}"


def working_tree_files() -> list[str]:
    """Return the paths `git ls-files -co --exclude-standard` lists for this checkout (Task 1)."""
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    return [rel for rel in out.decode("utf-8").split("\0") if rel]


def build_upstream(dest: Path, scratch: Path, extra: Optional[dict[str, str]] = None) -> Upstream:
    """Copy the working tree's files into dest/src, commit them and return a bare clone."""
    src = dest / "src"
    src.mkdir(parents=True)
    for rel in working_tree_files():
        source = REPO_ROOT / rel
        if not source.is_file() or source.is_symlink() or "__pycache__" in rel:
            continue
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for rel, text in (extra or {}).items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = git_env(scratch)
    git(["init", "-q", "-b", "main"], src, env)
    git(["add", "-A", "-f", "--", "."], src, env)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "temp upstream"], src, env)
    bare = dest / "upstream.git"
    git(["clone", "-q", "--bare", str(src), str(bare)], dest, env)
    sha = git(["rev-parse", "HEAD"], src, env).strip()
    return Upstream(bare, sha)


@pytest.fixture(scope="session")
def upstream(tmp_path_factory: pytest.TempPathFactory) -> Upstream:
    """Return the session's temp upstream built from the working tree."""
    root = tmp_path_factory.mktemp("upstream")
    return build_upstream(root, root / "scratch")


@pytest.fixture
def make_upstream(tmp_path: Path) -> Callable[[dict[str, str]], Upstream]:
    """Return a function that builds a temp upstream with extra files added."""

    def make(extra: dict[str, str]) -> Upstream:
        return build_upstream(tmp_path / "custom-upstream", tmp_path / "scratch", extra)

    return make


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return an empty temp user project (`git init`, no commits) (Task 2)."""
    path = tmp_path / "project"
    path.mkdir()
    git(["init", "-q", "-b", "main"], path, git_env(tmp_path / "scratch"))
    return path


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_user_project_v059", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def v059_project(tmp_path: Path) -> Callable[..., Path]:
    """Return a function that builds a v0.5.9 user project (S12a's builder) in a temp dir.

    `extras` adds the variant data the builder lacks (Task 2), written on top of its output and
    committed, never by editing the builder.
    """

    def build(variant: str = "default", extras: Optional[dict[str, str]] = None) -> Path:
        dest = tmp_path / f"v059-{variant}"
        _load_builder().build(dest, variant)
        if extras:
            env = git_env(tmp_path / "scratch")
            for rel, text in extras.items():
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            git(["add", "-A", "--", "."], dest, env)
            git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "extras"], dest, env)
        return dest

    return build


@dataclass
class Run:
    """One installer run: exit code, combined output and the download-shim log."""

    returncode: int
    output: str
    shim_calls: list[str]


def make_shims(bin_dir: Path, log: Path) -> None:
    """Write failing shims that record their argv (Task 3) and point python3 at this pytest."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in SHIMS:
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n', encoding="utf-8")
        shim.chmod(0o755)
    # A wrapper, not a symlink: a venv interpreter reached through a symlink elsewhere loses
    # its pyvenv.cfg and with it the venv's site-packages.
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)


def network_wrapper() -> list[str]:
    """Return ["unshare", "-rn"] when this host lets an unprivileged user drop the network."""
    if shutil.which("unshare") is None:
        return []
    proc = subprocess.run(["unshare", "-rn", "true"], capture_output=True, check=False)
    return ["unshare", "-rn"] if proc.returncode == 0 else []


def install_env(
    scratch: Path,
    url: str,
    env: Optional[dict[str, Optional[str]]] = None,
    path_prefix: Optional[Path] = None,
) -> dict[str, str]:
    """Return the installer's environment: HACF_TEST=1, the temp upstream and the shims first.

    `env` entries set to None are removed (for example HACF_TEST, to test the real defaults).
    """
    bin_dir = scratch / "bin"
    if not bin_dir.exists():
        make_shims(bin_dir, scratch / "shims.log")
    base = git_env(
        scratch,
        HACF_TEST="1",
        HACF_REPO_URL=url,
        PATH=os.pathsep.join(
            [*([str(path_prefix)] if path_prefix else []), str(bin_dir), os.environ["PATH"]]
        ),
    )
    for key, value in (env or {}).items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def run_installer(
    scratch: Path,
    url: str,
    cwd: Path,
    *args: str,
    env: Optional[dict[str, Optional[str]]] = None,
    script_on_stdin: bool = False,
    path_prefix: Optional[Path] = None,
    timeout: int = 300,
) -> Run:
    """Run HACF-install.sh in cwd with no terminal and no network when possible (Task 3).

    stdin is /dev/null (or the script itself with `script_on_stdin`, as `bash -s -- <args>`),
    the run starts a new session, so it has no controlling terminal, and it is wrapped in
    `unshare -rn` when that works on this host.
    """
    log = scratch / "shims.log"
    full_env = install_env(scratch, url, env, path_prefix)
    if log.exists():
        log.unlink()
    argv = [*network_wrapper(), "bash"]
    if script_on_stdin:
        argv += ["-s", "--", *args]
        stdin = INSTALLER.open("rb")
    else:
        argv += [str(INSTALLER), *args]
        stdin = subprocess.DEVNULL
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=full_env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            timeout=timeout,
            check=False,
        )
    finally:
        if script_on_stdin:
            stdin.close()
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return Run(proc.returncode, proc.stdout.decode("utf-8", "replace"), calls)


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """Return the directory that holds the shims, their log and the git config of a test."""
    path = tmp_path / "run-scratch"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def run_install(scratch: Path, upstream: Upstream) -> Callable[..., Run]:
    """Return run_installer bound to this test's scratch dir and (by default) the upstream.

    Call it as run_install(cwd, *args, env=..., script_on_stdin=..., path_prefix=...,
    source=<another Upstream>).
    """

    def run(cwd: Path, *args: str, source: Optional[Upstream] = None, **kwargs) -> Run:
        return run_installer(scratch, (source or upstream).url, cwd, *args, **kwargs)

    return run


def snapshot(root: Path) -> dict[str, str]:
    """Return {relative path: sha256} for every file and symlink under root (Task 4).

    Directories are listed too (value "dir"), so a new empty directory counts as a write.
    """
    result: dict[str, str] = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in dirs:
            path = Path(base, name)
            result[path.relative_to(root).as_posix() + "/"] = "dir"
        for name in sorted(files):
            path = Path(base, name)
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[rel] = "symlink:" + os.readlink(path)
            else:
                result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sha256(path: Path) -> str:
    """Return the sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Recorded local sources (slice 32)
# ---------------------------------------------------------------------------

UNINSTALLER = REPO_ROOT / "HACF-uninstall.sh"


def make_local_source(dest: Path, scratch: Path) -> tuple[Path, str]:
    """Return (checkout, HEAD) of a non-bare local HACF source built from the working tree."""
    upstream = build_upstream(dest, scratch)
    return dest / "src", upstream.sha


def add_commit(src: Path, env: dict[str, str], message: str) -> str:
    """Commit HEAD's tree again on the current branch and return the new sha (no file changes)."""
    git(["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message], src, env)
    return git(["rev-parse", "HEAD"], src, env).strip()


def git_argv_logger(bin_parent: Path, log: Path) -> Path:
    """Return a bin dir whose `git` appends its argv to log, then runs the real git."""
    bin_dir = bin_parent / "git-argv-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    real = shutil.which("git")
    assert real is not None
    script = bin_dir / "git"
    script.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log}"\nexec "{real}" "$@"\n', encoding="utf-8"
    )
    script.chmod(0o755)
    return bin_dir


def run_uninstall_script(
    scratch: Path,
    url: str,
    cwd: Path,
    *args: str,
    env: Optional[dict[str, Optional[str]]] = None,
    path_prefix: Optional[Path] = None,
    script: Path = UNINSTALLER,
    wrapper: bool = True,
) -> Run:
    """Run HACF-uninstall.sh in cwd with no terminal and, when possible, no network.

    `script` runs another copy (R64: the project's own ./HACF-uninstall.sh). `wrapper=False`
    skips `unshare -rn`, whose mapped root ignores directory permissions.
    """
    proc = subprocess.run(
        [*(network_wrapper() if wrapper else []), "bash", str(script), *args],
        cwd=cwd,
        env=install_env(scratch, url, env, path_prefix),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=300,
        check=False,
    )
    return Run(proc.returncode, proc.stdout.decode("utf-8", "replace"), [])
