"""Shared fixtures of the host tool tests: an isolated host, a fake `rig` and a fake `systemctl`.

Every test points the state dir, the XDG dirs, the heavy lock and HACF_HOST_RIG under tmp_path,
so no test reads the user's host state or config or calls the host's `rig`. A fake `systemctl` is
first on PATH and the user bus variables are unset, so no test reaches the host's systemd.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest
from conftest import REPO_ROOT

TOOL = REPO_ROOT / "openrig" / "hacf" / "bin" / "hacf-host.py"
EXAMPLE = REPO_ROOT / "openrig" / "hacf" / "host.example.yml"

# The fake `rig`: it logs each call's argv and replays the canned reply keyed on that argv (or on
# a key ending in " *" that starts it). A reply's `then` replaces replies once it is used, so a
# verb that changes the host (`down`, `up`) changes what the next reads see.
FAKE_RIG = """\
#!{python}
import json, re, sys, time
base = {base!r}
argv = sys.argv[1:]
with open(base + "/calls.jsonl", "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(base + "/replies.json"))
line = " ".join(argv)
reply = replies.get(line)
if reply is None:
    starts = [k for k in replies if k.endswith(" *") and line.startswith(k[:-1])]
    reply = replies[starts[0]] if starts else None
if reply is None:
    sys.stderr.write("fake rig: no reply for " + " ".join(argv) + "\\n")
    sys.exit(99)
if reply.get("queue_rules") and "--human-intent" in argv:
    # OpenRig 0.7.23's two refusals (queue-repository.js:2416-2425).
    intent = argv[argv.index("--human-intent") + 1]
    to = argv[argv.index("--destination") + 1]
    human = re.fullmatch(r"human(-[A-Za-z0-9._-]+)?@(kernel|host)|[A-Za-z0-9._:-]+@external", to)
    if intent not in ("decision", "update") or not human:
        sys.stderr.write("invalid_human_notification\\n")
        sys.exit(1)
if "then" in reply:
    json.dump({{**replies, **reply["then"]}}, open(base + "/replies.json", "w"))
time.sleep(reply.get("sleep", 0))
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("rc", 0))
"""


class FakeRig:
    """The canned replies of the fake `rig` and the calls it received."""

    def __init__(self, base: Path) -> None:
        """Write the executable under `base`, with no reply yet."""
        base.mkdir()
        self.base = base
        self.path = base / "rig"
        self.path.write_text(FAKE_RIG.format(python=sys.executable, base=str(base)))
        self.path.chmod(0o755)
        self.replies: dict[str, dict] = {}
        self._save()

    def _save(self) -> None:
        (self.base / "replies.json").write_text(json.dumps(self.replies))

    def reply(self, argv: str, stdout: object = "", **extra: object) -> None:
        """Answer `rig <argv>`; a non-string `stdout` is sent as JSON."""
        text = stdout if isinstance(stdout, str) else json.dumps(stdout)
        self.replies[argv] = {"stdout": text, **extra}
        self._save()

    def running(self, *names: str) -> None:
        """Answer `rig ps --json` with one running rig per name."""
        self.reply("ps --json", [{"name": n, "rigName": n, "status": "running"} for n in names])

    @property
    def calls(self) -> list[list[str]]:
        """The argv of every call, in order."""
        log = self.base / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]


# The fake `systemctl`: it logs each call's argv and replies by the verb after `--user`.
FAKE_SYSTEMCTL = """\
#!{python}
import json, sys
base = {base!r}
argv = sys.argv[1:]
with open(base + "/calls.jsonl", "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(base + "/replies.json"))
reply = replies.get(argv[1] if len(argv) > 1 else "", {{}})
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("rc", 0))
"""


class FakeSystemctl:
    """The canned replies of the fake `systemctl`, by verb, and the calls it received."""

    def __init__(self, base: Path) -> None:
        """Write the executable under `base`; every verb succeeds until told otherwise."""
        base.mkdir()
        self.base = base
        self.path = base / "systemctl"
        self.path.write_text(FAKE_SYSTEMCTL.format(python=sys.executable, base=str(base)))
        self.path.chmod(0o755)
        self.replies: dict[str, dict] = {}
        self._save()

    def _save(self) -> None:
        (self.base / "replies.json").write_text(json.dumps(self.replies))

    def reply(self, verb: str, stdout: str = "", **extra: object) -> None:
        """Answer `systemctl --user <verb> ...`."""
        self.replies[verb] = {"stdout": stdout, **extra}
        self._save()

    @property
    def calls(self) -> list[list[str]]:
        """The argv of every call, in order."""
        log = self.base / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]


class Host:
    """One isolated host: its dirs, its config file, its fake `rig` and fake `systemctl`."""

    def __init__(self, base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point every seam under `base`."""
        self.base = base
        self.state = base / "host-state"
        self.config = base / "xdg-config" / "hacf" / "host.yml"
        self.rig = FakeRig(base / "fake-rig")
        self.rig.running()
        self.systemctl = FakeSystemctl(base / "fake-systemctl")
        path = f"{self.systemctl.base}{os.pathsep}{os.environ.get('PATH', '')}"
        monkeypatch.setenv("PATH", path)
        for name in ("OPENRIG_SESSION_NAME", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
            monkeypatch.delenv(name, raising=False)
        for name, value in (
            ("HOME", base / "home"),
            ("OPENRIG_HOME", base / "openrig-home"),  # never the real specs dir
            ("HACF_HOST_STATE_DIR", self.state),
            ("XDG_CONFIG_HOME", base / "xdg-config"),
            ("XDG_STATE_HOME", base / "xdg-state"),
            ("XDG_DATA_HOME", base / "xdg-data"),
            ("HACF_HEAVY_LOCK_PATH", base / "xdg-state" / "hacf" / "heavy-job.lock"),
            ("HACF_HOST_RIG", self.rig.path),
        ):
            monkeypatch.setenv(name, str(value))

    def write_config(self, text: str) -> Path:
        """Write the host config and return its path."""
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(text, encoding="utf-8")
        return self.config

    def project(self, name: str) -> Path:
        """A project root with a missions/ dir."""
        root = self.base / "projects" / name
        (root / "missions").mkdir(parents=True)
        return root


@pytest.fixture(autouse=True)
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    """Every test runs on its own isolated host."""
    return Host(tmp_path, monkeypatch)


@pytest.fixture
def tool() -> types.ModuleType:
    """The tool, compiled in-process so no bytecode lands beside the shipped file."""
    module = types.ModuleType("hacf_host")
    module.__file__ = str(TOOL)
    exec(compile(TOOL.read_text(encoding="utf-8"), str(TOOL), "exec"), module.__dict__)
    return module
