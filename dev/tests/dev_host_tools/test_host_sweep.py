"""dev/scripts/host_sweep.py: the sweep of HACF's own leftovers (OPR.99.0.16.10, MR 3, P93).

The sweep removes dirs, signals processes and deletes rigs, so no test runs it against the host:
every run has HACF_SWEEP_ROOTS under tmp_path (which also confines the listeners it may signal to
run dirs there) and a PATH whose only `rig` is a fake. The listener guard is driven over a fake
/proc with the signals recorded instead of sent; the real signals go only to children these tests
spawn.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import REPO_ROOT

TOOL = REPO_ROOT / "dev" / "scripts" / "host_sweep.py"
COMPAT = REPO_ROOT / "dev" / "openrig_compat.py"
NOT_SWEPT = "not swept: name does not match the test-rig pattern"

FAKE_RIG = """\
#!{python}
import json, sys
from pathlib import Path
base = Path({base!r})
argv = sys.argv[1:]
with open(base / "calls.jsonl", "a") as log:
    log.write(json.dumps(argv) + "\\n")
how = json.loads((base / "behavior.json").read_text())
if argv == ["ps", "--json"]:
    names = json.loads((base / "rigs.json").read_text())
    rows = [{{"name": n, "rigName": n, "status": "running"}} for n in names]
    sys.stdout.write(how.get("ps_out", json.dumps(rows)))
    sys.exit(how.get("ps_rc", 0))
if argv[:1] == ["down"]:
    sys.exit(how.get("down_rc", 0))
sys.exit(99)
"""


def stamp(hours_ago: float) -> str:
    """A rig name's UTC stamp, so many hours old."""
    then = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return then.strftime("%Y%m%dT%H%M%S")


def dead_pid() -> int:
    """The pid of a child that has exited and been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    return child.pid


class Box:
    """One isolated sweep: two roots under tmp_path and a fake `rig`."""

    def __init__(self, base: Path) -> None:
        """Write everything under `base`."""
        self.base = base
        self.root = base / "root-a"
        self.other = base / "root-b"
        self.outside = base / "outside"
        for path in (self.root, self.other, self.outside):
            path.mkdir(parents=True)
        self.fake = base / "fake-bin"
        self.fake.mkdir()
        rig = self.fake / "rig"
        rig.write_text(FAKE_RIG.format(python=sys.executable, base=str(self.fake)))
        rig.chmod(0o755)
        self.rigs([])
        self.behave()
        self.env = {
            "PATH": str(self.fake),
            "HOME": str(base / "home"),
            "TMPDIR": str(base),
            "HACF_SWEEP_ROOTS": os.pathsep.join([str(self.root), str(self.other)]),
            "OPENRIG_HOME": str(base / "openrig-home"),
            "OPENRIG_URL": "http://127.0.0.1:9",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        assert shutil.which("rig", path=self.env["PATH"]) == str(rig)

    def rigs(self, names: list[str]) -> None:
        """The rigs the fake host lists."""
        (self.fake / "rigs.json").write_text(json.dumps(names))

    def behave(self, **how: object) -> None:
        """Set the fake rig's behavior."""
        (self.fake / "behavior.json").write_text(json.dumps(how))

    @property
    def calls(self) -> list[list[str]]:
        """The argv of every `rig` call, in order."""
        path = self.fake / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    @property
    def downs(self) -> list[list[str]]:
        """Every `rig down` call."""
        return [c for c in self.calls if c[:1] == ["down"]]

    def run_dir(
        self,
        name: str = "openrig-compat-Ab12Cd",
        *,
        root: Path | None = None,
        owner: object = None,
        age_h: float = 0,
    ) -> Path:
        """A compat run dir; `owner` is the owner record (a pid, raw text, or none)."""
        path = (root or self.root) / name
        (path / "state").mkdir(parents=True)
        (path / "payload").write_text("x" * 1000)
        record = path / "state" / "owner.json"
        if isinstance(owner, int):
            record.write_text(json.dumps({"pid": owner, "started_at": "2026-11-27T00:00:00Z"}))
        elif isinstance(owner, str):
            record.write_text(owner)
        if age_h:
            then = time.time() - age_h * 3600
            os.utime(path, (then, then))
        return path

    def sweep(self, *args: str, rc: int | None = None) -> subprocess.CompletedProcess:
        """Run the sweep as a script."""
        for root in self.env["HACF_SWEEP_ROOTS"].split(os.pathsep):
            assert Path(root).is_relative_to(self.base)
        done = subprocess.run(
            [sys.executable, str(TOOL), *args],
            env=self.env,
            cwd=self.base,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if rc is not None:
            assert done.returncode == rc, (done.returncode, done.stdout, done.stderr)
        return done


@pytest.fixture
def box(tmp_path: Path) -> Box:
    """An isolated sweep for one test."""
    return Box(tmp_path)


@pytest.fixture
def tool(box: Box, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fresh in-process copy of the sweep, under the box's environment."""
    for name in list(os.environ):
        if name.startswith(("OPENRIG_", "HACF_")):
            monkeypatch.delenv(name)
    for name, value in box.env.items():
        monkeypatch.setenv(name, value)
    assert shutil.which("rig") == str(box.fake / "rig")
    module = types.ModuleType("hacf_host_sweep")
    module.__file__ = str(TOOL)
    exec(compile(TOOL.read_text(encoding="utf-8"), str(TOOL), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------------------------ run dirs


def test_a_live_owners_dir_is_kept(box: Box) -> None:
    """A dir whose owner pid is alive is not stale, however old, and --apply keeps it."""
    path = box.run_dir(owner=os.getpid(), age_h=48)
    box.sweep(rc=0)
    box.sweep("--apply", rc=0)
    assert (path / "payload").exists()


def test_a_dead_owners_dir_is_removed_only_with_apply(box: Box) -> None:
    """Listing exits 1 and changes nothing; --apply removes it."""
    path = box.run_dir(owner=dead_pid())
    listed = box.sweep(rc=1)
    assert str(path) in listed.stdout and "is dead" in listed.stdout
    assert (path / "payload").exists()
    applied = box.sweep("--apply", rc=0)
    assert "removed" in applied.stdout
    assert not path.exists() and box.root.is_dir()


def test_an_old_ownerless_dir_is_removed_and_a_young_one_kept(box: Box) -> None:
    """Without an owner record the dir's mtime decides: over 24 h is stale."""
    old = box.run_dir("openrig-compat-old001", age_h=25)
    young = box.run_dir("openrig-compat-new001", root=box.other, age_h=23)
    box.sweep(rc=1)
    assert old.exists()
    box.sweep("--apply", rc=0)
    assert not old.exists() and young.exists()


@pytest.mark.parametrize(
    "owner",
    ["", "not json", "[1]", "{}", '{"pid": "7"}', '{"pid": true}', '{"pid": 0}', '{"pid": -1}'],
)
def test_an_unreadable_owner_record_keeps_the_dir(box: Box, owner: str) -> None:
    """A record that names no pid is unknown, and unknown is never deleted."""
    path = box.run_dir(owner=owner, age_h=72)
    listed = box.sweep("--apply", rc=0)
    assert "unreadable owner record" in listed.stdout
    assert path.exists()


def test_a_symlinked_owner_record_is_not_followed(box: Box) -> None:
    """The record is never read through a symlink."""
    elsewhere = box.outside / "owner.json"
    elsewhere.write_text(json.dumps({"pid": dead_pid()}))
    path = box.run_dir()
    (path / "state" / "owner.json").symlink_to(elsewhere)
    box.sweep("--apply", rc=0)
    assert path.exists() and elsewhere.exists()


def test_a_symlinked_entry_is_ignored(box: Box) -> None:
    """A matching name that is a symlink is neither listed nor followed."""
    real = box.run_dir(root=box.outside, owner=dead_pid())
    link = box.root / "openrig-compat-link01"
    link.symlink_to(real)
    listed = box.sweep("--apply", rc=0)
    assert "link01" not in listed.stdout
    assert link.is_symlink() and (real / "payload").exists()


def test_a_symlink_inside_a_stale_dir_keeps_its_target(box: Box) -> None:
    """Removing a stale dir removes the links in it, never what they point at."""
    kept = box.outside / "kept"
    kept.mkdir()
    (kept / "file").write_text("keep\n")
    path = box.run_dir(owner=dead_pid())
    (path / "out").symlink_to(kept)
    box.sweep("--apply", rc=0)
    assert not path.exists() and (kept / "file").read_text() == "keep\n"


@pytest.mark.parametrize(
    "name",
    [
        "openrig-compat-1234567",
        "openrig-compat-12345",
        "openrig-compat-0.7.23-20261127T000000Z",
        "openrig-compat-ab-cde",
        "xopenrig-compat-Ab12Cd",
        "openrig-compat",
    ],
)
def test_a_wrong_pattern_name_is_ignored(box: Box, name: str) -> None:
    """Only openrig-compat-?????? is a run dir."""
    path = box.run_dir(name, owner=dead_pid(), age_h=48)
    listed = box.sweep("--apply", rc=0)
    assert name not in listed.stdout and path.exists()


def test_only_a_direct_child_of_a_root_is_a_run_dir(box: Box) -> None:
    """A matching dir deeper down is not looked at."""
    path = box.run_dir(root=box.root / "nested", owner=dead_pid())
    box.sweep("--apply", rc=0)
    assert path.exists()


def test_a_file_with_a_matching_name_is_ignored(box: Box) -> None:
    """Only directories are run dirs."""
    path = box.root / "openrig-compat-file01"
    path.write_text("x")
    os.utime(path, (0, 0))
    box.sweep("--apply", rc=0)
    assert path.exists()


def test_another_uids_dir_is_ignored(
    box: Box, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dir another user owns is not listed."""
    path = box.run_dir(owner=dead_pid())
    monkeypatch.setattr(tool, "MY_UID", os.getuid() + 1)
    assert tool.list_run_dirs(tool.sweep_roots()) == []
    assert path.exists()


def test_a_dir_that_stops_being_stale_before_removal_is_kept(
    box: Box, tool: types.ModuleType
) -> None:
    """--apply judges the dir again just before removing it."""
    path = box.run_dir(owner=dead_pid())
    roots = tool.sweep_roots()
    (entry,) = tool.list_run_dirs(roots)
    assert entry["stale"]
    (path / "state" / "owner.json").write_text(json.dumps({"pid": os.getpid()}))
    assert tool.remove_run_dir(entry, roots).startswith("not removed")
    assert path.exists()


def test_a_dir_swapped_for_a_symlink_before_removal_is_kept(
    box: Box, tool: types.ModuleType
) -> None:
    """The realpath is checked again just before the removal."""
    path = box.run_dir(owner=dead_pid())
    target = box.run_dir(root=box.outside, owner=dead_pid())
    roots = tool.sweep_roots()
    (entry,) = tool.list_run_dirs(roots)
    shutil.rmtree(path)
    path.symlink_to(target)
    assert tool.remove_run_dir(entry, roots).startswith("not removed")
    assert (target / "payload").exists()


@pytest.mark.parametrize("bad", ["outside", "pattern"])
def test_removal_refuses_a_path_outside_the_roots_or_the_pattern(
    box: Box, tool: types.ModuleType, bad: str
) -> None:
    """A forged entry is refused by the removal itself."""
    if bad == "outside":
        path = box.run_dir(root=box.outside, owner=dead_pid())
    else:
        path = box.run_dir("other-dir", owner=dead_pid())
    entry = {"path": str(path), "stale": True}
    assert tool.remove_run_dir(entry, tool.sweep_roots()).startswith("not removed")
    assert path.exists()


def test_a_failed_removal_is_reported(
    box: Box, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """An rmtree error leaves exit 1 and says why."""
    box.run_dir(owner=dead_pid())

    def fail(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tool.shutil, "rmtree", fail)
    assert tool.main(["--apply"]) == 1
    assert "failed: Permission denied" in capsys.readouterr().out


def test_the_size_is_shown(box: Box) -> None:
    """The size is the files' bytes, links not followed."""
    path = box.run_dir(owner=dead_pid())
    (box.outside / "big").write_text("y" * 100000)
    (path / "big").symlink_to(box.outside / "big")
    report = json.loads(box.sweep("--json", rc=1).stdout)
    (entry,) = report["run_dirs"]
    assert entry["path"] == str(path) and entry["stale"] is True
    assert 1000 < entry["size_bytes"] < 5000
    assert report["applied"] is False


def test_the_default_roots(
    box: Box, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the seam: every real dir under ~/.cache/hacf-tmp, /dev/shm and /tmp."""
    cache = box.base / "home" / ".cache" / "hacf-tmp"
    (cache / "seat-a").mkdir(parents=True)
    (cache / "seat-b").mkdir()
    (cache / "a-file").write_text("")
    (cache / "linked").symlink_to(box.outside)
    monkeypatch.delenv("HACF_SWEEP_ROOTS")
    roots = [str(r) for r in tool.sweep_roots()]
    expected = [str((cache / n).resolve()) for n in ("seat-a", "seat-b")]
    expected += [os.path.realpath(p) for p in ("/dev/shm", "/tmp") if os.path.isdir(p)]
    assert roots == list(dict.fromkeys(expected))


def test_an_empty_roots_seam_means_no_roots(
    tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set but empty HACF_SWEEP_ROOTS never falls back to the host's roots."""
    monkeypatch.setenv("HACF_SWEEP_ROOTS", "")
    assert tool.sweep_roots() == []
    monkeypatch.setenv("HACF_SWEEP_ROOTS", "relative/dir")
    assert tool.sweep_roots() == []


# ------------------------------------------------------------------------------------ test rigs


def test_rigs_are_handled_by_name_and_age(box: Box) -> None:
    """P93: only a full match stamped over 6 h ago is deleted, with exactly that name."""
    old = f"hacf-test-proj.a_b-c-{stamp(7)}"
    young = f"hacf-test-proj-{stamp(5)}"
    odd = ["hacf-test-x", "hacf-test-foo-bar", f"hacf-test-X-{stamp(48)}"]
    odd += [f"hacf-test-proj-{stamp(48)}-extra", "hacf-test-proj-20261399T000000"]
    others = ["hacf", "other-project-hacf", f"my-hacf-test-p-{stamp(48)}"]
    box.rigs([*others, old, young, *odd])
    listed = box.sweep(rc=1)
    assert box.downs == []
    assert old in listed.stdout and young in listed.stdout
    box.sweep("--apply", rc=0)
    assert box.downs == [["down", old, "--delete", "--yes", "--json"]]
    report = json.loads(box.sweep("--json").stdout)
    rows = {r["name"]: r for r in report["rigs"]}
    assert set(rows) == {old, young, *odd}
    for name in odd:
        assert rows[name]["stale"] is False and rows[name]["reason"].startswith(NOT_SWEPT)
    assert rows[young]["stale"] is False and 4.9 < rows[young]["age_h"] < 5.2


@pytest.mark.parametrize("name", ["hacf-test-x", "hacf-test-foo-bar"])
def test_an_unstamped_test_name_is_listed_not_swept(box: Box, name: str) -> None:
    """P93: no `rig down`, and the line says why."""
    box.rigs([name])
    done = box.sweep("--apply", rc=0)
    assert f"{NOT_SWEPT}: {name}" in done.stdout
    assert box.downs == []


def test_a_young_rig_alone_is_not_stale(box: Box) -> None:
    """Under 6 h: exit 0, no delete."""
    box.rigs([f"hacf-test-proj-{stamp(5.9)}"])
    box.sweep(rc=0)
    box.sweep("--apply", rc=0)
    assert box.downs == []


def test_the_pattern_is_the_test_rigs(tool: types.ModuleType) -> None:
    """P93: the sweep deletes by dev/test_rig.py's own P87 pattern."""
    source = (REPO_ROOT / "dev" / "test_rig.py").read_text(encoding="utf-8")
    assert f'NAME_RE = re.compile(r"{tool.RIG_RE.pattern}")' in source
    assert tool.RIG_RE.pattern == r"hacf-test-[a-z0-9][a-z0-9._-]*-[0-9]{8}T[0-9]{6}"
    assert tool.RIG_AGE_H == 6 and tool.GRACE_S == 5 and tool.OWNERLESS_AGE_S == 86400


def test_the_age_boundary(tool: types.ModuleType) -> None:
    """Exactly 6 h is not over 6 h; a stamp in the future is not stale."""
    name = "hacf-test-p-20261127T000000"
    base = datetime(2026, 11, 27, tzinfo=timezone.utc)
    assert tool.judge_rig(name, base + timedelta(hours=6))["stale"] is False
    assert tool.judge_rig(name, base + timedelta(hours=6, seconds=1))["stale"] is True
    assert tool.judge_rig(name, base - timedelta(hours=9))["stale"] is False
    assert tool.judge_rig(name + "\n", base + timedelta(hours=9))["stale"] is False
    assert tool.judge_rig("hacf", base) is None


@pytest.mark.parametrize("name", ["hacf", "hacf-test-x", "young"])
def test_delete_refuses_a_name_it_did_not_judge_stale(
    box: Box, tool: types.ModuleType, name: str
) -> None:
    """The one `rig down` call judges the name again itself."""
    name = f"hacf-test-p-{stamp(1)}" if name == "young" else name
    assert tool.delete_rig(name).startswith("not deleted")
    assert box.calls == []


@pytest.mark.parametrize(
    "how", [{"ps_rc": 1}, {"ps_out": "not json"}, {"ps_out": "{}"}, {"ps_out": "[1]"}]
)
def test_an_unreadable_rig_list_is_unknown_and_deletes_nothing(box: Box, how: dict) -> None:
    """Exit 2: neither clean nor stale; the dirs are still swept."""
    box.rigs([f"hacf-test-p-{stamp(48)}"])
    box.behave(**how)
    path = box.run_dir(owner=dead_pid())
    done = box.sweep("--apply", rc=2)
    assert "rig ps" in done.stdout and box.downs == []
    assert not path.exists()


def test_a_missing_rig_command_is_unknown(box: Box) -> None:
    """No `rig` on PATH: exit 2."""
    (box.fake / "rig").unlink()
    assert "rigs: unknown: rig ps" in box.sweep(rc=2).stdout


def test_a_failed_rig_down_is_reported(box: Box) -> None:
    """A delete that fails leaves exit 1."""
    box.rigs([f"hacf-test-p-{stamp(48)}"])
    box.behave(down_rc=1)
    done = box.sweep("--apply", rc=1)
    assert "failed: rig down exited 1" in done.stdout


def test_a_clean_host_exits_0_and_a_bad_option_2(box: Box) -> None:
    """Nothing stale: 0. An unknown or abbreviated option: 2, nothing done."""
    box.rigs(["hacf"])
    box.sweep(rc=0)
    box.run_dir(owner=dead_pid())
    box.sweep("--app", rc=2)
    box.sweep("--frob", rc=2)
    assert (box.root / "openrig-compat-Ab12Cd").exists()


# ------------------------------------------------------------------------------------ listeners


class FakeProc:
    """A fake /proc tree the in-process sweep reads, and the signals it asked for."""

    def __init__(self, base: Path) -> None:
        """An empty tree."""
        self.root = base / "proc"
        self.root.mkdir()
        self.signals: list[tuple[int, int]] = []

    def add(
        self,
        pid: int,
        port: str,
        *,
        start: int = 1000,
        state: str = "S",
        argv: list[str] | None = None,
    ) -> None:
        """Add or replace one process."""
        entry = self.root / str(pid)
        entry.mkdir(exist_ok=True)
        if argv is None:
            argv = ["/usr/bin/python3", "/t/dev/openrig_compat.py", "listen", "--port-file", port]
            argv += ["--log", "/dev/null"]
        (entry / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        # stat: pid (comm) state, then fields 4..21, then field 22, the start time.
        fields = [state, *["0"] * 18, str(start), "0", "0"]
        (entry / "stat").write_text(f"{pid} (py) thon) {' '.join(fields)}\n")

    def drop(self, pid: int) -> None:
        """The process is gone."""
        shutil.rmtree(self.root / str(pid))


@pytest.fixture
def proc(box: Box, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> FakeProc:
    """The sweep over a fake /proc: no real signal can be sent."""
    fake = FakeProc(box.base)
    monkeypatch.setattr(tool, "PROC", fake.root)
    monkeypatch.setattr(tool, "send_signal", lambda pid, sig: fake.signals.append((pid, sig)))
    monkeypatch.setattr(tool, "GRACE_S", 0.2)
    monkeypatch.setattr(tool, "POLL_S", 0.02)
    return fake


def port_of(root: Path, name: str = "openrig-compat-gone01") -> str:
    """The port file of a run dir under `root` (the dir need not exist)."""
    return str(root / name / "state" / "port")


def one_listener(tool: types.ModuleType) -> dict:
    """The sweep's one stale listener."""
    (entry,) = tool.list_listeners(tool.sweep_roots())
    assert entry["stale"]
    return entry


def test_the_start_time_is_field_22(box: Box, tool: types.ModuleType, proc: FakeProc) -> None:
    """Parsed after the last `)`, whatever the command name holds."""
    proc.add(4242, port_of(box.root), start=987654)
    entry = one_listener(tool)
    assert (entry["pid"], entry["start_time"]) == (4242, 987654)
    assert entry["run_dir"] == str(box.root / "openrig-compat-gone01")


def test_which_listeners_are_stale(box: Box, tool: types.ModuleType, proc: FakeProc) -> None:
    """A missing run dir or a dead owner is stale; a live or ownerless dir is not."""
    live = box.run_dir("openrig-compat-live01", owner=os.getpid())
    dead = box.run_dir("openrig-compat-dead01", owner=dead_pid())
    bare = box.run_dir("openrig-compat-bare01", age_h=48)
    for pid, path in enumerate((live, dead, bare, box.other / "openrig-compat-none01"), 101):
        proc.add(pid, str(path / "state" / "port"))
    found = {e["pid"]: e["stale"] for e in tool.list_listeners(tool.sweep_roots())}
    assert found == {101: False, 102: True, 103: False, 104: True}


def test_what_is_not_a_listener(
    box: Box, tool: types.ModuleType, proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the script's own command shape, over a run dir under a root, of this uid."""
    port = port_of(box.root)
    py, mod = "/usr/bin/python3", "/t/dev/openrig_compat.py"
    shapes = [
        ["grep", "openrig_compat.py", "listen", "--port-file", port],
        [py, "/t/other.py", "listen", "--port-file", port],
        [py, "-c", "x", mod, "listen", "--port-file", port],
        [py, mod, "connections", "--port-file", port],
        [py, mod, "listen", "--log", port],
        [py, mod, "listen", "--port-file"],
        [py, f"{mod} listen --port-file {port}"],
        [py, mod, "listen", "--port-file", port_of(box.outside)],
        [py, mod, "listen", "--port-file", port_of(box.root / "nested")],
        [py, mod, "listen", "--port-file", str(box.root / "openrig-compat-gone01" / "port")],
        [py, mod, "listen", "--port-file", port_of(box.root, "pytest-0")],
        [py, mod, "listen", "--port-file", "openrig-compat-gone01/state/port"],
        [py, mod, "listen", "--port-file", f"{box.root}/x/../openrig-compat-gone01/state/port"],
    ]
    for pid, argv in enumerate(shapes, 200):
        proc.add(pid, port, argv=argv)
    assert tool.list_listeners(tool.sweep_roots()) == []
    proc.add(300, port, argv=[py, "-I", "-B", mod, "listen", "--port-file", port])
    assert one_listener(tool)["pid"] == 300
    monkeypatch.setattr(tool, "MY_UID", os.getuid() + 1)
    assert tool.list_listeners(tool.sweep_roots()) == []


def test_a_symlinked_run_dir_is_never_a_listeners(
    box: Box, tool: types.ModuleType, proc: FakeProc
) -> None:
    """A symlink in a root that bears a run dir's name is not followed."""
    real = box.run_dir(root=box.outside, owner=dead_pid())
    link = box.root / "openrig-compat-link01"
    link.symlink_to(real)
    proc.add(400, str(link / "state" / "port"))
    assert tool.list_listeners(tool.sweep_roots()) == []


def test_a_matching_orphan_gets_sigterm_then_sigkill(
    box: Box, tool: types.ModuleType, proc: FakeProc
) -> None:
    """Still the same process after the grace: SIGKILL."""
    proc.add(500, port_of(box.root))
    assert tool.stop_listener(one_listener(tool)) == "killed 500"
    assert proc.signals == [(500, signal.SIGTERM), (500, signal.SIGKILL)]


@pytest.mark.parametrize("state", ["gone", "Z"])
def test_a_listener_that_exits_on_sigterm_gets_no_sigkill(
    box: Box,
    tool: types.ModuleType,
    proc: FakeProc,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Gone, or a zombie, within the grace: done."""
    proc.add(501, port_of(box.root))

    def send(pid, sig):
        proc.signals.append((pid, sig))
        if state == "gone":
            proc.drop(pid)
        else:
            proc.add(pid, port_of(box.root), state="Z", argv=[])

    monkeypatch.setattr(tool, "send_signal", send)
    assert tool.stop_listener(one_listener(tool)) == "terminated 501"
    assert proc.signals == [(501, signal.SIGTERM)]


CHANGES = {
    "cmdline": {"argv": ["/usr/bin/sleep", "100"]},
    "start": {"start": 1001},
    "run dir": {"port": "openrig-compat-else01"},
}


def _change(proc: FakeProc, box: Box, pid: int, what: str) -> None:
    how = dict(CHANGES[what])
    port = port_of(box.root, how.pop("port", "openrig-compat-gone01"))
    proc.add(pid, port, **how)


@pytest.mark.parametrize("what", [*CHANGES, "uid"])
def test_a_changed_process_gets_no_signal(
    box: Box,
    tool: types.ModuleType,
    proc: FakeProc,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    what: str,
) -> None:
    """P93: changed between listing and the SIGTERM: nothing is sent."""
    proc.add(600, port_of(box.root))
    entry = one_listener(tool)
    if what == "uid":
        monkeypatch.setattr(tool, "MY_UID", os.getuid() + 1)
    else:
        _change(proc, box, 600, what)
    assert tool.stop_listener(entry) == "skip 600: not the listener"
    assert proc.signals == []
    assert "skip 600: not the listener" in capsys.readouterr().err.splitlines()


@pytest.mark.parametrize("what", [*CHANGES, "uid"])
def test_a_change_between_sigterm_and_sigkill_gets_no_sigkill(
    box: Box,
    tool: types.ModuleType,
    proc: FakeProc,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    what: str,
) -> None:
    """P93: the check runs again before the SIGKILL."""
    proc.add(601, port_of(box.root))

    def send(pid, sig):
        proc.signals.append((pid, sig))
        if what == "uid":
            monkeypatch.setattr(tool, "MY_UID", os.getuid() + 1)
        else:
            _change(proc, box, pid, what)

    monkeypatch.setattr(tool, "send_signal", send)
    assert tool.stop_listener(one_listener(tool)) == "skip 601: not the listener"
    assert proc.signals == [(601, signal.SIGTERM)]
    assert "skip 601: not the listener" in capsys.readouterr().err.splitlines()


def test_a_pid_gone_before_the_sigterm(
    box: Box, tool: types.ModuleType, proc: FakeProc, capsys
) -> None:
    """Nothing is sent to a pid that is gone."""
    proc.add(602, port_of(box.root))
    entry = one_listener(tool)
    proc.drop(602)
    assert tool.stop_listener(entry) == "skip 602: gone"
    assert proc.signals == []
    assert "skip 602: gone" in capsys.readouterr().err.splitlines()


@pytest.mark.parametrize("pid", [0, 1, -1, -5000, "self"])
def test_never_a_signal_to_a_group_init_or_itself(
    box: Box, tool: types.ModuleType, proc: FakeProc, pid: object
) -> None:
    """Whatever the entry says."""
    number = os.getpid() if pid == "self" else pid
    proc.add(number, port_of(box.root))
    entry = {"pid": number, "start_time": 1000, "stale": True}
    entry["run_dir"] = str(box.root / "openrig-compat-gone01")
    assert tool.stop_listener(entry) == f"skip {number}: not the listener"
    assert proc.signals == []


def _spawn_listener(run_dir: Path) -> subprocess.Popen:
    port = run_dir / "state" / "port"
    child = subprocess.Popen(
        [sys.executable, str(COMPAT), "listen", "--port-file", str(port)]
        + ["--log", str(run_dir / "state" / "connections.log")],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    deadline = time.monotonic() + 20
    while not port.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert port.exists()
    return child


def test_a_stale_listener_is_terminated(box: Box) -> None:
    """A spawned `listen` whose dir was removed is listed, then stopped by --apply."""
    run_dir = box.run_dir("openrig-compat-orph01", owner=os.getpid())
    child = _spawn_listener(run_dir)
    try:
        box.sweep(rc=0)  # its owner lives
        shutil.rmtree(run_dir)
        listed = box.sweep(rc=1)
        assert f"pid {child.pid}" in listed.stdout and child.poll() is None
        applied = box.sweep("--apply", rc=0)
        assert f"terminated {child.pid}" in applied.stdout
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_apply_stops_a_dead_owners_listener_and_removes_its_dir(box: Box) -> None:
    """Both leftovers of one killed run go in one pass."""
    run_dir = box.run_dir("openrig-compat-orph02", owner=dead_pid())
    child = _spawn_listener(run_dir)
    try:
        box.sweep("--apply", rc=0)
        assert child.wait(timeout=10) == 0 and not run_dir.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
