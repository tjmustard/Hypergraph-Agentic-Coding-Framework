"""dev/openrig_watch_install.sh and the unit templates in dev/systemd/ (release-0.6.2 slice 4, R17).

Release-export slice 1 added --notify-cmd, --row-destination and --evidence-root.
The installer never runs against the host here: every run has a fake HOME, and PATH holds fake
npm/node/uv/rig dirs plus a stub dir with `git`, `systemctl` and `systemd-run` (logging, never the
real ones) ahead of /usr/bin:/bin, so even a non-dry run cannot reach the user's systemd or the real
repository's worktrees.
"""

from __future__ import annotations

import configparser
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_openrig_watch import account_hits, account_name

INSTALL = REPO_ROOT / "dev" / "openrig_watch_install.sh"
SERVICE = REPO_ROOT / "dev" / "systemd" / "hacf-openrig-watch.service"
TIMER = REPO_ROOT / "dev" / "systemd" / "hacf-openrig-watch.timer"

LOGGER = """#!/bin/sh
printf '%s\\n' "$(basename "$0") $*" >>"$STUB_LOG"
exit "${STUB_RC:-0}"
"""


def _exe(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


class Host:
    """A fake HOME and fake tool dirs; git/systemctl/systemd-run are logging stubs."""

    def __init__(self, tmp: Path, tools: tuple[str, ...] = ("npm", "node", "uv", "rig")) -> None:
        """Build the sandbox under tmp."""
        self.home = tmp / "home"
        self.home.mkdir(parents=True)
        self.dirs = {"npm": tmp / "nvm/bin", "node": tmp / "nvm/bin", "uv": tmp / "uvbin",
                     "rig": tmp / "rigbin", "notify-stub": tmp / "notifybin"}  # fmt: skip
        for tool in tools:
            _exe(self.dirs[tool] / tool)
        self.stubs = tmp / "stubs"
        for name in ("git", "systemctl", "systemd-run"):
            _exe(self.stubs / name, LOGGER)
        self.log = tmp / "stub.log"
        self.worktree = tmp / "watch-wt"
        path = [self.stubs, *dict.fromkeys(self.dirs.values())]
        self.path = ":".join(str(p) for p in path) + ":/usr/bin:/bin"

    def run(self, *args: str, rc: int = 0) -> subprocess.CompletedProcess:
        """Run the script; return the completed process."""
        env = {"HOME": str(self.home), "PATH": self.path, "STUB_LOG": str(self.log),
               "STUB_RC": str(rc), "LANG": "C.UTF-8"}  # fmt: skip
        return subprocess.run(["bash", str(INSTALL), *args], capture_output=True, text=True,
                              env=env, timeout=60, check=False)  # fmt: skip

    def calls(self) -> list[str]:
        """The stub commands' log lines."""
        return self.log.read_text().splitlines() if self.log.exists() else []

    def files(self) -> list[str]:
        """Every file under the fake HOME."""
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())


def _install_args(host: Host, *extra: str) -> list[str]:
    return ["--worktree", str(host.worktree), "--base", "5d1e6dc", *extra]


def test_dry_run_prints_every_path_and_the_env_file(tmp_path: Path) -> None:
    """Dry run prints every path and the env file."""
    host = Host(tmp_path)
    _exe(host.dirs["notify-stub"] / "notify-stub")
    proc = host.run(*_install_args(host, "--dry-run", "--notify-cmd", "notify-stub"))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    env_file = host.home / ".config/hacf-openrig-watch/env"
    units = host.home / ".config/systemd/user"
    want_env = [
        f"PATH={host.dirs['npm']}:{host.dirs['uv']}:{host.dirs['notify-stub']}:/usr/bin:/bin",
        f"HACF_WATCH_RIG={host.dirs['rig']}/rig",
        f"HACF_WATCH_WORKTREE={host.worktree}",
    ]
    assert f"dry-run: write {env_file}:" in out
    for line in want_env:
        assert f"    {line}" in out.splitlines()
    assert (f"dry-run: run git -C {REPO_ROOT} worktree add -b swimlane/openrig-accepted "
            f"{host.worktree} 5d1e6dc") in out  # fmt: skip
    for unit in ("hacf-openrig-watch.service", "hacf-openrig-watch.timer"):
        assert f"dry-run: copy {REPO_ROOT}/dev/systemd/{unit} -> {units}/{unit}" in out
    assert "dry-run: run systemctl --user daemon-reload" in out
    assert "dry-run: run systemctl --user enable --now hacf-openrig-watch.timer" in out
    assert (f"dry-run: run systemd-run --user --wait --pipe -p EnvironmentFile={env_file} "
            f"/bin/bash {host.worktree}/dev/openrig_watch.sh --check-env") in out  # fmt: skip
    # nothing written, nothing run
    assert host.files() == [] and host.calls() == [] and not host.worktree.exists()


def test_dry_run_without_notify_cmd_leaves_it_off_path(tmp_path: Path) -> None:
    """Without --notify-cmd no notifier is detected, even one on PATH (no auto-detection)."""
    host = Host(tmp_path)
    _exe(host.dirs["notify-stub"] / "notify-stub")
    proc = host.run(*_install_args(host, "--dry-run"))
    assert proc.returncode == 0
    assert (
        f"    PATH={host.dirs['npm']}:{host.dirs['uv']}:/usr/bin:/bin" in proc.stdout.splitlines()
    )
    assert "notifier=none" in proc.stdout


@pytest.mark.parametrize("missing", ["npm", "node", "uv", "rig"])
@pytest.mark.parametrize("dry", [True, False])
def test_a_missing_tool_aborts_with_nothing_written(
    missing: str, dry: bool, tmp_path: Path
) -> None:
    """A missing tool aborts with nothing written."""
    tools = tuple(t for t in ("npm", "node", "uv", "rig") if t != missing)
    if missing == "node":  # node shares npm's dir in the fake host: give npm its own
        tools = ("npm", "uv", "rig")
    host = Host(tmp_path, tools)
    args = _install_args(host, "--dry-run") if dry else _install_args(host)
    proc = host.run(*args)
    assert proc.returncode == 1
    assert f"missing on PATH: {missing}" in proc.stderr
    assert host.files() == [] and host.calls() == [] and not host.worktree.exists()


def test_install_with_stubs_writes_and_runs_in_order_and_is_idempotent(tmp_path: Path) -> None:
    """Install with stubs writes and runs in order and is idempotent."""
    host = Host(tmp_path)
    proc = host.run(*_install_args(host))
    assert proc.returncode == 0, proc.stderr
    env_file = host.home / ".config/hacf-openrig-watch/env"
    assert env_file.read_text().splitlines() == [
        f"PATH={host.dirs['npm']}:{host.dirs['uv']}:/usr/bin:/bin",
        f"HACF_WATCH_RIG={host.dirs['rig']}/rig", f"HACF_WATCH_WORKTREE={host.worktree}",
    ]  # fmt: skip
    for src in (SERVICE, TIMER):
        assert (host.home / ".config/systemd/user" / src.name).read_bytes() == src.read_bytes()
    assert host.calls() == [
        f"git -C {REPO_ROOT} worktree add -b swimlane/openrig-accepted {host.worktree} 5d1e6dc",
        "systemctl --user daemon-reload",
        "systemctl --user enable --now hacf-openrig-watch.timer",
        f"systemd-run --user --wait --pipe -p EnvironmentFile={env_file} /bin/bash "
        f"{host.worktree}/dev/openrig_watch.sh --check-env",
    ]  # fmt: skip
    assert "linger" not in INSTALL.read_text().replace("Linger stays off", "")


def test_an_existing_path_must_be_the_watcher_worktree(tmp_path: Path) -> None:
    """An existing path must be the watcher worktree."""
    host = Host(tmp_path)
    host.worktree.mkdir()
    proc = host.run(*_install_args(host))  # the stub git prints nothing: not a worktree
    assert proc.returncode == 1 and "is not a worktree on swimlane/openrig-accepted" in proc.stderr
    assert not any("worktree add" in c for c in host.calls())


def test_a_failed_check_env_fails_the_install(tmp_path: Path) -> None:
    """A failed check env fails the install."""
    host = Host(tmp_path)
    proc = host.run(*_install_args(host), rc=1)
    assert proc.returncode == 1 and "failed: run git" in proc.stderr
    host2 = Host(tmp_path / "b")
    _exe(host2.stubs / "systemd-run", LOGGER.replace('exit "${STUB_RC:-0}"', "exit 1"))
    proc = host2.run(*_install_args(host2))
    assert proc.returncode == 1 and "failed: run systemd-run" in proc.stderr


def test_uninstall_dry_run_and_real_with_stubs(tmp_path: Path) -> None:
    """Uninstall dry run and real with stubs."""
    host = Host(tmp_path)
    assert host.run(*_install_args(host)).returncode == 0
    before = host.files()
    proc = host.run("--uninstall", "--dry-run")
    assert proc.returncode == 0
    units = host.home / ".config/systemd/user"
    for line in ("dry-run: run systemctl --user disable --now hacf-openrig-watch.timer",
                 f"dry-run: remove {units}/hacf-openrig-watch.service",
                 f"dry-run: remove {units}/hacf-openrig-watch.timer",
                 f"dry-run: remove {host.home}/.config/hacf-openrig-watch/env",
                 "dry-run: run systemctl --user daemon-reload",
                 f"git worktree remove {host.worktree}"):  # fmt: skip
        assert line in proc.stdout, line
    assert host.files() == before
    calls = len(host.calls())
    proc = host.run("--uninstall")
    assert proc.returncode == 0
    assert host.files() == []
    assert host.calls()[calls:] == ["systemctl --user disable --now hacf-openrig-watch.timer",
                                    "systemctl --user daemon-reload"]  # fmt: skip
    assert f"git worktree remove {host.worktree}" in proc.stdout
    assert host.run("--uninstall").returncode == 0  # idempotent


@pytest.mark.parametrize("args", [[], ["--worktree", "/x"], ["--base", "abc"], ["--frob"],
                                  ["--uninstall", "--worktree", "/x"]])  # fmt: skip
def test_usage_errors(args: list[str], tmp_path: Path) -> None:
    """Usage errors."""
    host = Host(tmp_path)
    assert host.run(*args).returncode == 2
    assert host.files() == [] and host.calls() == []


def _rendered_env(out: str, env_file: Path) -> list[str]:
    """The env file lines a --dry-run printed under `write <env file>:`."""
    lines = out.splitlines()
    start = lines.index(f"dry-run: write {env_file}:") + 1
    rendered = []
    for line in lines[start:]:
        if not line.startswith("    "):
            break
        rendered.append(line[4:])
    return rendered


@pytest.mark.parametrize("given", [(), ("notify",), ("row",), ("evidence",),
                                   ("notify", "row", "evidence")])  # fmt: skip
def test_dry_run_renders_each_setting_only_when_given(
    given: tuple[str, ...], tmp_path: Path
) -> None:
    """Each of the three settings is in the rendered env file only when its option is given."""
    host = Host(tmp_path)
    _exe(host.dirs["notify-stub"] / "notify-stub")
    args = []
    if "notify" in given:
        args += ["--notify-cmd", "notify-stub"]
    if "row" in given:
        args += ["--row-destination", "maintainer@example"]
    if "evidence" in given:
        args += ["--evidence-root", str(tmp_path / "evidence")]
    proc = host.run(*_install_args(host, "--dry-run", *args))
    assert proc.returncode == 0, proc.stderr
    notify_dir = f"{host.dirs['notify-stub']}:" if "notify" in given else ""
    want = [
        f"PATH={host.dirs['npm']}:{host.dirs['uv']}:{notify_dir}/usr/bin:/bin",
        f"HACF_WATCH_RIG={host.dirs['rig']}/rig",
        f"HACF_WATCH_WORKTREE={host.worktree}",
    ]
    if "notify" in given:
        want.append("HACF_WATCH_NOTIFY_CMD=notify-stub")
    if "row" in given:
        want.append("HACF_WATCH_ROW_DESTINATION=maintainer@example")
    if "evidence" in given:
        want.append(f"HACF_COMPAT_EVIDENCE_ROOT={tmp_path / 'evidence'}")
    assert _rendered_env(proc.stdout, host.home / ".config/hacf-openrig-watch/env") == want
    assert host.files() == [] and host.calls() == []


def test_install_writes_the_settings_to_the_env_file(tmp_path: Path) -> None:
    """A real install (stubbed systemd) writes exactly the rendered env file."""
    host = Host(tmp_path)
    _exe(host.dirs["notify-stub"] / "notify-stub")
    proc = host.run(
        *_install_args(
            host,
            "--notify-cmd",
            "notify-stub",
            "--row-destination",
            "maintainer@example",
            "--evidence-root",
            str(tmp_path / "ev"),
        )
    )
    assert proc.returncode == 0, proc.stderr
    assert (host.home / ".config/hacf-openrig-watch/env").read_text().splitlines() == [
        f"PATH={host.dirs['npm']}:{host.dirs['uv']}:{host.dirs['notify-stub']}:/usr/bin:/bin",
        f"HACF_WATCH_RIG={host.dirs['rig']}/rig", f"HACF_WATCH_WORKTREE={host.worktree}",
        "HACF_WATCH_NOTIFY_CMD=notify-stub", "HACF_WATCH_ROW_DESTINATION=maintainer@example",
        f"HACF_COMPAT_EVIDENCE_ROOT={tmp_path / 'ev'}",
    ]  # fmt: skip


@pytest.mark.parametrize("dry", [True, False])
def test_an_unresolvable_notify_cmd_aborts_with_nothing_written(dry: bool, tmp_path: Path) -> None:
    """An unresolvable --notify-cmd exits 1 before anything is written or run."""
    host = Host(tmp_path)
    args = ["--notify-cmd", "no-such-notifier", *(["--dry-run"] if dry else [])]
    proc = host.run(*_install_args(host, *args))
    assert proc.returncode == 1
    assert "--notify-cmd no-such-notifier does not resolve" in proc.stderr
    assert "write" not in proc.stdout
    assert host.files() == [] and host.calls() == [] and not host.worktree.exists()


# ---------------------------------------------------------------------------------------------
# The unit templates (mission R17)


def _ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def test_service_template_keys() -> None:
    """Service template keys."""
    service = _ini(SERVICE)
    assert dict(service["Service"]) == {
        "Type": "oneshot",
        "EnvironmentFile": "%h/.config/hacf-openrig-watch/env",
        "ExecStart": "/bin/bash ${HACF_WATCH_WORKTREE}/dev/openrig_watch.sh",
        "TimeoutStartSec": "2700",
        "Nice": "10",
        "IOSchedulingClass": "idle",
        "MemoryMax": "1536M",
        "MemorySwapMax": "0",
        "OOMPolicy": "continue",
        "Environment": "TMPDIR=%h/.cache/hacf-tmp/openrig-watch "
                       "XDG_CACHE_HOME=%h/.cache/hacf-openrig-watch",
    }  # fmt: skip
    assert "Install" not in service  # the timer is what is enabled


def test_timer_template_keys() -> None:
    """Timer template keys."""
    timer = _ini(TIMER)
    assert dict(timer["Timer"]) == {"OnCalendar": "*-*-* 06:20", "Persistent": "true",
                                    "RandomizedDelaySec": "15min"}  # fmt: skip
    assert dict(timer["Install"]) == {"WantedBy": "timers.target"}


def test_memory_max_follows_the_spec_formula() -> None:
    """ceil(1.5 x memory.peak) rounded up to 256 MiB, at least 1536M (slice 4 SPEC §1)."""
    peak_mib = 434765824 / 2**20  # slice 3 proof/03-compat.md, Task 10
    rounded = -(-int(-(-1.5 * peak_mib // 1)) // 256) * 256
    assert rounded == 768
    assert f"MemoryMax={max(rounded, 1536)}M" in SERVICE.read_text()
    assert "03-compat.md" in SERVICE.read_text() and "434765824" in SERVICE.read_text()


@pytest.mark.parametrize("path", [SERVICE, TIMER], ids=lambda p: p.name)
def test_templates_hold_no_host_path(path: Path) -> None:
    """Templates hold no host path."""
    text = path.read_text(encoding="utf-8")
    for needle in ("/home/", ".nvm", "python", "/usr/local", "~"):
        assert needle not in text, needle
    assert account_hits(text.encode(), account_name()) == []
    assert "%t" not in text  # XDG_CACHE_HOME and TMPDIR on disk, not the runtime dir
    env = _ini(SERVICE)["Service"]["Environment"]
    assert dict(item.split("=", 1) for item in env.split())["XDG_CACHE_HOME"].startswith(
        "%h/.cache/"
    )


def test_installer_and_watcher_hold_no_host_path() -> None:
    """Installer and watcher hold no host path."""
    for path in (INSTALL, REPO_ROOT / "dev/openrig_watch.sh"):
        text = path.read_text(encoding="utf-8")
        for needle in ("/home/", ".nvm", "hacf-py310"):
            assert needle not in text, (path.name, needle)
        assert account_hits(text.encode(), account_name()) == [], path.name
