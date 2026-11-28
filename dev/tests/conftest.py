"""Shared fixtures for the HACF dev test suite (run by dev/scripts/check.sh).

Tests never write inside the repository: every file they create lives under `tmp_path`, and
bytecode caching is off for modules imported from the repository. pytest's own `.pytest_cache/`
at the root is the exception, and it ignores itself.

Tests never write the user's cache either: importing this file points XDG_CACHE_HOME at a
per-session temp dir (each xdist worker imports it too, and subprocesses inherit it), so the
OpenRig range probe's cache (`${XDG_CACHE_HOME:-~/.cache}/hacf/rig-probe`, release-0.6.2 R8) is a
throwaway. REAL_PROBE_CACHE and REAL_PROBE_CACHE_STATE record the user's cache as it was.

Nor the code-graph runtime (lead ruling P32): the run's controller makes one short private dir,
`$XDG_RUNTIME_DIR/hacf-test-<pid>` (else under the temp dir), mode 0700, and exports it as
CBM_RUNTIME_DIR. xdist workers and subprocesses inherit it, and hyper_codegraph.py keeps a
caller's value, so no test creates a per-project dir under `$XDG_RUNTIME_DIR/hacf-cbm/`.
pytest_unconfigure removes the dir by its literal path.

Nor the user's host state: a heavy job a test starts queues its ticket under HACF_HOST_STATE_DIR,
which `_isolated_host_state` points at a temp dir after the run itself has taken the real lock.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.dont_write_bytecode = True


def _probe_cache_state(path: Path) -> tuple[float | None, list[tuple[str, int]]]:
    """Return a cache dir's mtime and (name, mtime_ns) listing; (None, []) when it is absent."""
    try:
        listing = sorted((p.name, p.stat().st_mtime_ns) for p in path.iterdir())
        return path.stat().st_mtime, listing
    except OSError:
        return None, []


_REAL_XDG = os.environ.get("XDG_CACHE_HOME")
REAL_PROBE_CACHE = (
    (Path(_REAL_XDG) if _REAL_XDG and os.path.isabs(_REAL_XDG) else Path.home() / ".cache")
    / "hacf"
    / "rig-probe"
)
REAL_PROBE_CACHE_STATE = _probe_cache_state(REAL_PROBE_CACHE)
SESSION_CACHE_HOME = tempfile.mkdtemp(prefix="hacf-test-xdg-cache-")
os.environ["XDG_CACHE_HOME"] = SESSION_CACHE_HOME
atexit.register(shutil.rmtree, SESSION_CACHE_HOME, True)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / ".agents" / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PY310 = "/usr/bin/python3"
VENV_PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")
FROZEN_NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

# `import _runtime` (and later slices' modules) resolves to .agents/scripts.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DEV_SCRIPTS_DIR = REPO_ROOT / "dev" / "scripts"
_HEAVY_LEASE = pytest.StashKey[object]()
# The test modules that take a heavy lock of their own: each of their tests also gets its own
# host state and reads no host config of the user's.
HEAVY_LOCK_TESTS = frozenset({"test_host_gate.py", "test_check_sh.py", "test_gate.py"})


def pytest_configure(config: pytest.Config) -> None:
    """Take the host heavy-job lock (dev/scripts/host_gate.py) for this pytest run (F9).

    Only the xdist controller or a plain run takes it, and not when a valid re-entrancy token is
    inherited (check.sh, the release gate). The token goes into os.environ, so subprocesses this run
    starts (a nested check.sh or pytest) run inside the same lease. HACF_HEAVY_LOCK=0 turns it off
    for one run. A wait that runs out ends the run with exit 75: the host was busy, retry later.

    First, on the controller only, it picks the run's CBM_RUNTIME_DIR (lead rulings P32, R65): the
    first that fits hyper_codegraph.py's socket budget (dir + 48 bytes <= 107) of (i) the caller's
    CBM_RUNTIME_DIR, when it is absolute and an existing dir, kept as it is and never created,
    chmod-ed or removed; (ii) `$XDG_RUNTIME_DIR/hacf-test-<pid>`, when XDG_RUNTIME_DIR is an
    absolute existing dir; (iii) `<temp dir>/hacf-test-<pid>`. Only (ii) and (iii) are the run's
    own: made 0700, exported, and removed in pytest_unconfigure. None fitting is exit 4. The code
    stays inside the hook because test_host_gate.py copies these two hooks by source.
    """
    controller = not (hasattr(config, "workerinput") or os.environ.get("PYTEST_XDIST_WORKER"))
    if controller:
        import tempfile

        own = f"hacf-test-{os.getpid()}"
        caller = os.environ.get("CBM_RUNTIME_DIR")
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        candidates = []  # (dir, the run's own)
        if caller and os.path.isabs(caller) and os.path.isdir(caller):
            candidates.append((caller, False))
        if xdg and os.path.isabs(xdg) and os.path.isdir(xdg):
            candidates.append((os.path.join(xdg, own), True))
        candidates.append((os.path.join(tempfile.gettempdir(), own), True))
        fits = [c for c in candidates if len(os.fsencode(c[0])) + 48 <= 107]
        if not fits:
            named = "; ".join(f"{d} ({len(os.fsencode(d))} bytes)" for d, _ in candidates)
            pytest.exit(f"no test CBM_RUNTIME_DIR is short enough for a socket path: {named}", 4)
        runtime, made = fits[0]
        if made:
            os.mkdir(runtime, 0o700)
            os.chmod(runtime, 0o700)
            config.hacf_cbm_runtime = (runtime, caller)
            os.environ["CBM_RUNTIME_DIR"] = runtime
    if os.environ.get("HACF_HEAVY_LOCK") == "0":
        return
    if not controller:
        return
    if str(DEV_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(DEV_SCRIPTS_DIR))
    import host_gate

    if host_gate.token_valid():
        return
    try:
        lease = host_gate.acquire("pytest")
    except host_gate.HostBusy as exc:
        pytest.exit(f"host busy: {exc.reason}; retry later (exit 75)", returncode=75)
    config.stash[_HEAVY_LEASE] = (lease, os.environ.get(host_gate.TOKEN_VAR))
    os.environ.update(lease.env())


def pytest_unconfigure(config: pytest.Config) -> None:
    """Release the lease pytest_configure took, restore the token variable, drop the runtime dir."""
    runtime = getattr(config, "hacf_cbm_runtime", None)
    if runtime is not None:  # P32: remove the run's own dir by its literal path (never a caller's)
        import shutil

        path, inherited = runtime
        shutil.rmtree(path, ignore_errors=True)
        if inherited is None:
            os.environ.pop("CBM_RUNTIME_DIR", None)
        else:
            os.environ["CBM_RUNTIME_DIR"] = inherited
        del config.hacf_cbm_runtime
    held = config.stash.get(_HEAVY_LEASE, None)
    if held is None:
        return
    lease, inherited = held
    import host_gate

    lease.release()
    if inherited is None:
        os.environ.pop(host_gate.TOKEN_VAR, None)
    else:
        os.environ[host_gate.TOKEN_VAR] = inherited
    del config.stash[_HEAVY_LEASE]


@pytest.fixture(autouse=True, scope="session")
def _isolated_host_state() -> Iterator[None]:
    """Point HACF_HOST_STATE_DIR at a temp dir for the tests (never for the run's own lease).

    pytest_configure has already taken the real lock by then, and the value is restored before
    pytest_unconfigure releases it.
    """
    state = tempfile.mkdtemp(prefix="hacf-test-host-state-")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HACF_HOST_STATE_DIR", state)
        yield
    shutil.rmtree(state, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_host_per_test(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh host state and an empty config dir for each test of HEAVY_LOCK_TESTS."""
    if request.path.name not in HEAVY_LOCK_TESTS:
        return
    tmp_path = request.getfixturevalue("tmp_path")
    monkeypatch.setenv("HACF_HOST_STATE_DIR", str(tmp_path / "hacf-host-state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "hacf-xdg-config"))


def clean_env(**extra: str) -> dict[str, str]:
    """Return os.environ without git, uv and venv state that could leak into a child process."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "UV_")) and key not in ("VIRTUAL_ENV", "PYTHONPATH")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


class GitRepo:
    """A throwaway git repository with a fixed author, an isolated config and no hooks."""

    def __init__(self, path: Path, scratch: Path) -> None:
        """Remember the work tree and build the environment every git call uses.

        HOME stays as it is (uv needs its cache); git reads no global or system config, and
        tmp_git_repo points core.excludesFile and core.attributesFile at os.devnull.
        """
        self.path = path
        self.scratch = scratch
        empty = scratch / "gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = clean_env(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty),
            GIT_CEILING_DIRECTORIES=str(path.parent),
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME="HACF Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_AUTHOR_DATE="2026-10-21T12:00:00+00:00",
            GIT_COMMITTER_NAME="HACF Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
            GIT_COMMITTER_DATE="2026-10-21T12:00:00+00:00",
        )

    def git(self, *args: str, check: bool = True, **kwargs: object) -> str:
        """Run git in the repository and return its stdout."""
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def write(self, rel: str, text: str) -> Path:
        """Write text to a path relative to the work tree, creating parent directories."""
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit(self, message: str, *paths: str) -> str:
        """Stage paths (everything when none are given), commit and return the new sha."""
        self.git("add", "--", *(paths or (".",)))
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def run(self, argv: list[str], stdin: str = "", **env: str) -> subprocess.CompletedProcess:
        """Run argv inside the work tree with the repository's environment."""
        return subprocess.run(
            argv,
            cwd=self.path,
            env={**self.env, **env},
            input=stdin,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> GitRepo:
    """Return a repository under tmp_path on branch main with one commit (README.md)."""
    scratch = tmp_path / "git-scratch"
    (scratch / "no-hooks").mkdir(parents=True)
    path = tmp_path / "repo"
    path.mkdir()
    repo = GitRepo(path, scratch)
    repo.git("init", "-q", "-b", "main")
    for key, value in (
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(scratch / "no-hooks")),
        ("core.excludesFile", os.devnull),
        ("core.attributesFile", os.devnull),
    ):
        repo.git("config", key, value)
    repo.write("README.md", "# Throwaway test repository\n")
    repo.commit("initial commit")
    return repo


@pytest.fixture
def copy_fixture(tmp_path: Path):
    """Return a function that copies dev/tests/fixtures/<name> into tmp_path."""

    def copy(name: str, dest: Path | None = None) -> Path:
        target = dest if dest is not None else tmp_path / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES_DIR / name, target)
        return target

    return copy


@pytest.fixture
def run_script(tmp_path: Path):
    """Return a function that runs a script under a chosen interpreter.

    `interpreter` is None for the interpreter running pytest, `PY310` for /usr/bin/python3 or
    `VENV_PYTHON` for .venv/bin/python. A relative script path is resolved against the
    repository root; the default working directory is tmp_path.
    """

    def run(
        script: str | Path,
        *args: str,
        interpreter: str | None = None,
        cwd: Path | None = None,
        stdin: str = "",
        env: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess:
        path = Path(script)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return subprocess.run(
            [interpreter or sys.executable, str(path), *args],
            cwd=cwd or tmp_path,
            env=clean_env(**(env or {})),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    return run


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze `_runtime`'s clock at FROZEN_NOW and return that instant."""
    import _runtime

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            """Return FROZEN_NOW in tz (naive UTC when tz is None)."""
            if tz is None:
                return FROZEN_NOW.replace(tzinfo=None)
            return FROZEN_NOW.astimezone(tz)

    monkeypatch.setattr(_runtime, "datetime", FrozenDatetime)
    return FROZEN_NOW
