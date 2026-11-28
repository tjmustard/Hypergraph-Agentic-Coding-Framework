"""Shared fixtures of the dev test rig's tests: an isolated host, a fake `rig` and a fake job run.

`dev/test_rig.py` runs `rig up` and `rig down --delete`, and every rig on a developer's host may
be live. So no test runs it from the repository against the host's OpenRig: each test runs a copy
under tmp_path, with an environment built from nothing, a fake `rig` first on a PATH that holds no
other `rig`, and OPENRIG_HOME and OPENRIG_URL pointed at nothing. The copy's `hacf-host.py` is a
stand-in: its `job run` passes the command through as the scheduler does (its own process group, a
stop signal forwarded, SIGKILL after a grace), and its `load_config` is the shipped one. The P89
lock test swaps the shipped scheduler in (`use_real_scheduler`), with its lock and state under
tmp_path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from conftest import REPO_ROOT

TOOL = REPO_ROOT / "dev" / "test_rig.py"
HOST_TOOL = REPO_ROOT / "openrig" / "hacf" / "bin" / "hacf-host.py"
KIT_SPEC = REPO_ROOT / "openrig" / "hacf" / "rig.yaml"
PROJECT_NAME = "Hypergraph-Agentic-Coding-Framework"  # the real basename: it has capitals
# The rigs the fake host already runs. The second stands for another project's live rig: a real
# project's name may not appear here (the privacy scan refuses it).
LIVE_RIGS = ("hacf", "other-project-hacf")

# The fake `rig`. It logs each call, keeps the rigs "running" in rigs.json and reads its
# behavior from behavior.json. `spec validate` also saves the spec it was shown.
FAKE_RIG = """\
#!{python}
import json, signal, subprocess, sys, time
from pathlib import Path
LATE = (
    "import json, sys, time\\n"
    "from pathlib import Path\\n"
    "base = Path(sys.argv[1])\\n"
    "time.sleep(float(sys.argv[3]))\\n"
    "rigs = json.loads((base / 'rigs.json').read_text())\\n"
    "(base / 'rigs.json').write_text(json.dumps([*rigs, sys.argv[2]]))\\n"
)
base = Path({base!r})
argv = sys.argv[1:]
with open(base / "calls.jsonl", "a") as log:
    log.write(json.dumps(argv) + "\\n")
how = json.loads((base / "behavior.json").read_text())
rigs = json.loads((base / "rigs.json").read_text())
def save():
    (base / "rigs.json").write_text(json.dumps(rigs))
def done(rc=0, out=""):
    sys.stdout.write(out)
    sys.exit(rc)
if argv[:2] == ["spec", "validate"]:
    (base / "validated.yaml").write_text(Path(argv[2]).read_text())
    done(how.get("validate_rc", 0), "validate says\\n")
def queued():
    path = base / "queue.json"
    return json.loads(path.read_text()) if path.exists() else how.get("rows", [])
if argv[:2] == ["queue", "list"]:
    open_now = [r for r in queued() if r["state"] != "done"]
    done(how.get("list_rc", 0), how.get("list_out", json.dumps(open_now)))
if argv[:2] == ["queue", "update"]:
    if argv[2] in how.get("close_fails", []):
        done(1, "refused\\n")
    rows = queued()
    for row in rows:
        if row["qitemId"] == argv[2]:
            row["state"] = "done"
    (base / "queue.json").write_text(json.dumps(rows))
    done(0, "{{}}")
if argv[:2] == ["queue", "show"]:
    row = how.get("queue_show", json.dumps({{"qitemId": argv[2]}}))
    done(how.get("queue_rc", 0), row)
if argv[:1] == ["up"]:
    name = argv[argv.index("--name") + 1]
    if how.get("up_ignores_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        def killed(number, frame):
            with open(base / "calls.jsonl", "a") as log:
                log.write(json.dumps(["up-killed"]) + "\\n")
            sys.exit(143)
        signal.signal(signal.SIGTERM, killed)
    if "up_late" in how:  # a child of `up` that registers the rig later, unless it is stopped
        child = subprocess.Popen([sys.executable, "-c", LATE, str(base), name, str(how["up_late"])])
        (base / "late.pid").write_text(str(child.pid))
    (base / "up-started").write_text("")
    time.sleep(how.get("up_sleep", 0))
    rigs = json.loads((base / "rigs.json").read_text())
    rigs.append(name)
    save()
    (base / "up-done").write_text("")
    done(how.get("up_rc", 0), "{{}}")
if argv[:1] == ["down"]:
    (base / "down-started").write_text("")
    time.sleep(how.get("down_sleep", 0))
    rigs = json.loads((base / "rigs.json").read_text())
    if not how.get("sticky") and argv[1] in rigs:
        rigs.remove(argv[1])
        save()
    (base / "down-done").write_text("")
    done(how.get("down_rc", 0), "{{}}")
if argv == ["ps", "--json"]:
    rows = [{{"name": n, "rigName": n, "status": "running"}} for n in rigs]
    (base / "ps-done").write_text("")
    done(how.get("ps_rc", 0), how.get("ps_out", json.dumps(rows)))
sys.stderr.write("fake rig: no reply for " + " ".join(argv) + "\\n")
sys.exit(99)
"""

# The stand-in for hacf-host.py: the shipped load_config, and a `job run` that passes through.
FAKE_HOST_TOOL = """\
import importlib.util, json, os, signal, subprocess, sys, time
from pathlib import Path

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("hacf_host_real", {real!r})
_real = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _real
_spec.loader.exec_module(_real)
load_config = _real.load_config


def main(argv):
    with open({log!r}, "a") as log:
        log.write(json.dumps(argv) + "\\n")
    if os.environ.get("FAKE_JOB_BUSY"):
        return 75
    Path({pid!r}).write_text(str(os.getpid()))
    command = argv[argv.index("--") + 1 :]
    received = []
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda number, _frame: received.append(number))
    child = subprocess.Popen(command, start_new_session=True)
    deadline = None
    while child.poll() is None:
        if received and deadline is None:
            os.killpg(child.pid, signal.SIGTERM)
            deadline = time.monotonic() + float(os.environ.get("FAKE_JOB_GRACE", "10"))
        if deadline is not None and time.monotonic() >= deadline:
            os.killpg(child.pid, signal.SIGKILL)
        time.sleep(0.02)
    code = child.returncode
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
"""


def _lines(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class Bench:
    """One isolated host: a copy of the tool, a fixture project, a fake `rig` and its TMPDIR."""

    def __init__(self, base: Path) -> None:
        """Write everything under `base`."""
        self.base = base
        self.repo = base / "repo"
        (self.repo / "dev").mkdir(parents=True)
        self.tool = self.repo / "dev" / "test_rig.py"
        if TOOL.exists():
            shutil.copy(TOOL, self.tool)
        host_dir = self.repo / "openrig" / "hacf" / "bin"
        host_dir.mkdir(parents=True)
        self.job_log = base / "job.jsonl"
        self.job_pid = base / "job.pid"
        self.host_tool = host_dir / "hacf-host.py"
        self.host_tool.write_text(
            FAKE_HOST_TOOL.format(real=str(HOST_TOOL), log=str(self.job_log), pid=str(self.job_pid))
        )
        self.fake = base / "fake-bin"
        self.fake.mkdir()
        rig = self.fake / "rig"
        rig.write_text(FAKE_RIG.format(python=sys.executable, base=str(self.fake)))
        rig.chmod(0o755)
        (self.fake / "rigs.json").write_text(json.dumps(list(LIVE_RIGS)))
        self.behave()
        self.project = self.make_project(PROJECT_NAME)
        self.tmp = base / "tmpdir"
        self.tmp.mkdir()
        self.env = {
            "PATH": os.pathsep.join([str(self.fake), "/usr/bin", "/bin"]),
            "HOME": str(base / "home"),
            "TMPDIR": str(self.tmp),
            "XDG_CONFIG_HOME": str(base / "xdg-config"),
            "XDG_STATE_HOME": str(base / "xdg-state"),
            "OPENRIG_HOME": str(base / "openrig-home"),
            "OPENRIG_URL": "http://127.0.0.1:9",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # The guard of every test: the only `rig` it can find is the fake one.
        assert shutil.which("rig", path=self.env["PATH"]) == str(rig)

    def make_project(self, name: str) -> Path:
        """A project with the shipped kit, a file and a relative symlink."""
        root = self.base / "projects" / name
        shutil.copytree(
            KIT_SPEC.parent, root / "openrig" / "hacf", ignore=shutil.ignore_patterns("__pycache__")
        )
        (root / "notes.txt").write_text("source\n")
        (root / "link.txt").symlink_to("notes.txt")
        return root

    def use_real_scheduler(self) -> Path:
        """Swap the shipped hacf-host.py in, all its state under this bench; the lock's path."""
        shutil.copy(HOST_TOOL, self.host_tool)
        lock = self.base / "heavy" / "heavy-job.lock"
        meminfo = self.base / "meminfo"
        meminfo.write_text("MemAvailable: 33554432 kB\n")
        self.env.update(
            HACF_HEAVY_LOCK_PATH=str(lock),
            HACF_HOST_STATE_DIR=str(self.base / "host-state"),
            HACF_HOST_RIG=str(self.fake / "rig"),
            HACF_HOST_MEMINFO=str(meminfo),
            XDG_DATA_HOME=str(self.base / "xdg-data"),
            HACF_HEAVY_POLL_S="0.05",
            HACF_HEAVY_WAIT_S="20",
        )
        return lock

    def behave(self, **how: object) -> None:
        """Set the fake rig's behavior."""
        (self.fake / "behavior.json").write_text(json.dumps(how))

    @property
    def calls(self) -> list[list[str]]:
        """The argv of every `rig` call, in order."""
        return _lines(self.fake / "calls.jsonl")

    def verbs(self) -> list[str]:
        """Each `rig` call's verb."""
        return [" ".join(c[:2]) if c[0] in ("spec", "queue") else c[0] for c in self.calls]

    def call(self, verb: str) -> list[str]:
        """The one `rig` call of this verb."""
        found = [c for c, v in zip(self.calls, self.verbs()) if v == verb]
        assert len(found) == 1, (verb, self.calls)
        return found[0]

    @property
    def rigs(self) -> list[str]:
        """The rigs the fake host still has."""
        return json.loads((self.fake / "rigs.json").read_text())

    @property
    def jobs(self) -> list[list[str]]:
        """The argv of every scheduler call."""
        return _lines(self.job_log)

    def copies(self) -> list[Path]:
        """The copy dirs left under TMPDIR."""
        return sorted(p for p in self.tmp.glob("hacf-test-rig-*") if p.is_dir())

    def log_text(self) -> str:
        """The run logs under TMPDIR."""
        return "".join(p.read_text() for p in sorted(self.tmp.glob("hacf-test-rig-*.log")))

    def source(self) -> dict[str, str]:
        """The fixture project as it is: each path and its content or link target."""
        found = {}
        for path in sorted(self.project.rglob("*")):
            rel = str(path.relative_to(self.project))
            if path.is_symlink():
                found[rel] = f"-> {os.readlink(path)}"
            elif path.is_file():
                found[rel] = path.read_text()
        return found

    def argv(self, *options: str, test: tuple[str, ...] = ("true",), project=None) -> list[str]:
        """The command line of a `run`."""
        root = str(self.project if project is None else project)
        return [sys.executable, str(self.tool), "run", "--project", root, *options, "--", *test]

    def run(self, *options: str, test: tuple[str, ...] = ("true",), project=None, **env: str):
        """Run the copied tool to its end."""
        return subprocess.run(
            self.argv(*options, test=test, project=project),
            env={**self.env, **env},
            cwd=self.base,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def start(self, *options: str, test: tuple[str, ...], **env: str) -> subprocess.Popen:
        """Start the copied tool, for a test that signals it."""
        return subprocess.Popen(
            self.argv(*options, test=test),
            env={**self.env, **env},
            cwd=self.base,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    """An isolated host for one test."""
    return Bench(tmp_path)


@pytest.fixture
def tool(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fresh in-process copy of the tool, under the bench's environment."""
    for name in list(os.environ):
        if name.startswith(("OPENRIG_", "HACF_")):
            monkeypatch.delenv(name)
    for name, value in bench.env.items():
        monkeypatch.setenv(name, value)
    assert shutil.which("rig") == str(bench.fake / "rig")
    module = types.ModuleType("hacf_test_rig")
    module.__file__ = str(bench.tool)
    exec(compile(bench.tool.read_text(encoding="utf-8"), str(bench.tool), "exec"), module.__dict__)
    return module
