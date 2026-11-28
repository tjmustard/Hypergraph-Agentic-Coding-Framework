"""dev/test_rig.py: a throwaway rig from a project copy, one scheduler job, always deleted.

Every test uses the fake `rig` and the stand-in scheduler of conftest.py; no rig is launched or
deleted on the host.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

from .conftest import HOST_TOOL, LIVE_RIGS

NAME_RE = r"hacf-test-[a-z0-9][a-z0-9._-]*-[0-9]{8}T[0-9]{6}"
ALL_MEMBERS = [
    "orch.lead",
    "spec.architect",
    "spec.red-team",
    "spec.resolve",
    "build.execute",
    "build.audit",
    "desk.scribe",
]
NEEDS_APPROVAL = "a full-size test rig needs TJ's say-so: --approval <qitem id>"
ROW = "qitem-20261127000000-abcdef12"
CLEAN = ["queue list", "down", "ps", "queue list"]  # P109: rows closed, delete, rows checked
RUN = ["spec validate", "up", *CLEAN]

# The command under test: record what it was given, then exit with the code asked for.
PROBE = """\
import json, os, sys
from pathlib import Path
name = os.environ.get("HACF_TEST_RIG", "")
copy = os.environ.get("HACF_TEST_RIG_COPY", "")
seen = {"rig": name, "argv": sys.argv[3:], "copy": copy, "copy_is_dir": os.path.isdir(copy)}
Path(sys.argv[1]).write_text(json.dumps(seen))
sys.exit(int(sys.argv[2]))
"""

SLEEPER = """\
import sys, time
from pathlib import Path
Path(sys.argv[1]).write_text("")
time.sleep(60)
"""


def probe(bench, code: int = 0, *extra: str) -> tuple[str, ...]:
    """The probe command: it writes what it was given, then exits `code`."""
    return (sys.executable, "-c", PROBE, str(bench.base / "probe.json"), str(code), *extra)


def probed(bench) -> dict:
    """What the probe wrote."""
    return json.loads((bench.base / "probe.json").read_text())


def wait_for(condition, seconds: float = 20.0) -> None:
    """Wait until `condition` holds."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


def members(spec: dict) -> list[str]:
    """A spec's member ids."""
    return [f"{p['id']}.{m['id']}" for p in spec["pods"] for m in p["members"]]


def edges(spec: dict) -> set[tuple[str, str, str]]:
    """A spec's edges, pod edges with qualified ends."""
    found = {(e["kind"], e["from"], e["to"]) for e in spec.get("edges") or []}
    for pod in spec["pods"]:
        for edge in pod.get("edges") or []:
            ends = (f"{pod['id']}.{edge['from']}", f"{pod['id']}.{edge['to']}")
            found.add((edge["kind"], *ends))
    return found


def validated(bench) -> dict:
    """The spec `rig spec validate` was shown."""
    return yaml.safe_load((bench.fake / "validated.yaml").read_text())


def assert_refused(done) -> None:
    """Exit 2 with the tool's own line (the interpreter also exits 2, for a missing file)."""
    assert done.returncode == 2, done.stderr
    assert re.search(r"^test_rig: ", done.stderr, re.M), done.stderr


def assert_nothing_launched(bench) -> None:
    """No `rig up` or `down` ran and the host's rigs are as they were."""
    assert not [c for c in bench.calls if c[0] in ("up", "down")], bench.calls
    assert bench.rigs == list(LIVE_RIGS)


# ------------------------------------------------------------------------------- the happy path


def test_happy_path_argv_sequence_name_and_cleanup(bench):
    """MR 8: the argv sequence; the delete names exactly the `rig up --name` value (P87)."""
    before = bench.source()
    done = bench.run(test=probe(bench, 0, "--seats", "--", "x"))
    assert done.returncode == 0, done.stderr
    assert bench.verbs() == RUN
    up = bench.call("up")
    name = up[up.index("--name") + 1]
    assert re.fullmatch(NAME_RE, name)
    assert name.startswith("hacf-test-hypergraph-agentic-coding-framework-")
    copy = bench.tmp / f"hacf-test-rig-{name[-15:]}"
    spec = str(copy / "openrig" / "hacf" / "rig.yaml")
    assert bench.calls[0] == ["spec", "validate", spec]
    assert up == ["up", spec, "--name", name, "--cwd", str(copy)]
    assert bench.call("down") == ["down", name, "--delete", "--yes", "--json"]
    assert bench.call("ps") == ["ps", "--json"]
    listing = ["queue", "list", "-A", "--state", "pending,in-progress,blocked"]
    listing += ["--json", "--limit", "10000"]
    assert [c for c in bench.calls if c[:2] == ["queue", "list"]] == [listing, listing]
    assert probed(bench) == {
        "rig": name,
        "argv": ["--seats", "--", "x"],
        "copy": str(copy.resolve()),
        "copy_is_dir": True,
    }
    assert bench.rigs == list(LIVE_RIGS)
    assert bench.copies() == []
    assert bench.source() == before
    assert name in bench.log_text()
    print("\nfake rig log:")
    for call in bench.calls:
        print(json.dumps(call))


def test_the_run_is_one_scheduler_job_declaring_its_ram(bench):
    """MR 3: one `job run` around `_inner`."""
    done = bench.run()
    assert done.returncode == 0, done.stderr
    (job,) = bench.jobs
    assert job[:6] == ["job", "run", "--job", "test-rig", "--ram-mb", "330"]
    assert job[6] == "--"
    assert job[8:10] == [str(bench.tool), "_inner"]
    assert job[-2:] == ["--", "true"]
    real = types.ModuleType("hacf_host")
    exec(compile(HOST_TOOL.read_text(), str(HOST_TOOL), "exec"), real.__dict__)
    args, extra = real.build_parser().parse_known_args(job)  # the shipped scheduler takes it
    assert (args.action, args.job, args.ram_mb, extra) == ("run", "test-rig", 330, [])
    assert args.argv[args.argv.index(str(bench.tool)) + 1] == "_inner"


@pytest.mark.parametrize(
    ("seats", "ram"),
    [
        (["--seats", "orch.lead,build.execute,build.audit"], "990"),
        (["--seats", "build.execute", "build.audit"], "660"),
    ],
)
def test_the_ram_declared_is_seats_times_330(bench, seats, ram):
    """MR 8: seats x 330."""
    done = bench.run(*seats)
    assert done.returncode == 0, done.stderr
    assert bench.jobs[0][5] == ram


def test_the_ram_declared_follows_the_host_config(bench):
    """The host's `seat_ram_mb` is used."""
    config = Path(bench.env["XDG_CONFIG_HOME"]) / "hacf" / "host.yml"
    config.parent.mkdir(parents=True)
    config.write_text("seat_ram_mb: 500\n")
    assert bench.run("--seats", "orch.lead,build.audit").returncode == 0
    assert bench.jobs[0][5] == "1000"


def test_a_busy_scheduler_is_exit_75_and_launches_nothing(bench):
    """MR 1: exit 75."""
    done = bench.run(FAKE_JOB_BUSY="1")
    assert done.returncode == 75
    assert bench.calls == []
    assert bench.copies() == []


def test_the_test_command_gets_the_resolved_copy_path(bench):
    """P88: under a TMPDIR reached through a link, the path is the real one and exists."""
    link = bench.base / "tmp-link"
    link.symlink_to(bench.tmp)
    done = bench.run(test=probe(bench), TMPDIR=str(link))
    assert done.returncode == 0, done.stderr
    seen = probed(bench)
    assert seen["copy"] == str(bench.tmp.resolve() / f"hacf-test-rig-{seen['rig'][-15:]}")
    assert seen["copy_is_dir"] is True


# ---------------------------------------------------------------------------------- the copy


def test_the_default_copy_keeps_only_the_lead(bench):
    """MR 2 and MR 1's default seat."""
    assert bench.run().returncode == 0
    spec = validated(bench)
    assert members(spec) == ["orch.lead"]
    assert [p["id"] for p in spec["pods"]] == ["orch"]
    assert edges(spec) == set()
    assert spec["env"] == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    assert spec["name"] == "hacf" and spec["culture_file"] == "CULTURE.md"


def test_the_trimmed_spec_drops_edges_that_name_dropped_members(bench):
    """MR 8: members and edges."""
    assert bench.run("--seats", "orch.lead,build.execute,build.audit").returncode == 0
    spec = validated(bench)
    assert members(spec) == ["orch.lead", "build.execute", "build.audit"]
    assert edges(spec) == {
        ("delegates_to", "build.execute", "build.audit"),
        ("delegates_to", "orch.lead", "build.execute"),
        ("delegates_to", "orch.lead", "build.audit"),
        ("can_observe", "orch.lead", "build.audit"),
    }


def test_a_pod_edge_to_a_dropped_member_is_dropped(bench):
    """MR 2: an edge through a dropped member goes."""
    assert bench.run("--seats", "spec.architect,spec.resolve").returncode == 0
    spec = validated(bench)
    assert members(spec) == ["spec.architect", "spec.resolve"]
    assert edges(spec) == set()


def test_the_copy_keeps_symlinks_and_the_source_is_untouched(bench):
    """MR 2 and `--keep-copy`."""
    before = bench.source()
    done = bench.run("--keep-copy")
    assert done.returncode == 0, done.stderr
    (copy,) = bench.copies()
    assert os.readlink(copy / "link.txt") == "notes.txt"
    assert (copy / "notes.txt").read_text() == "source\n"
    assert members(yaml.safe_load((copy / "openrig/hacf/rig.yaml").read_text())) == ["orch.lead"]
    assert bench.source() == before
    assert bench.rigs == list(LIVE_RIGS)


def test_a_spec_that_is_a_link_out_of_the_copy_is_refused(bench):
    """The trim never writes through a link."""
    spec = bench.project / "openrig" / "hacf" / "rig.yaml"
    real = bench.base / "elsewhere.yaml"
    real.write_text(spec.read_text())
    spec.unlink()
    spec.symlink_to(real)
    text = real.read_text()
    done = bench.run()
    assert_refused(done)
    assert real.read_text() == text
    assert_nothing_launched(bench)
    assert bench.copies() == []


def test_an_invalid_trimmed_spec_launches_nothing(bench):
    """MR 2: validate is the gate."""
    bench.behave(validate_rc=1)
    done = bench.run()
    assert_refused(done)
    assert "validate says" in done.stderr
    assert bench.verbs() == ["spec validate"]
    assert_nothing_launched(bench)
    assert bench.copies() == []


def _absolute_link(root: Path, base: Path) -> str:
    (root / "abs-link").symlink_to(root / "notes.txt")  # absolute, back into the source
    return "abs-link"


def _escaping_link(root: Path, base: Path) -> str:
    (root / "sub").mkdir()
    (root / "sub" / "out").symlink_to("../../x")
    return "sub/out"


def _chained_link(root: Path, base: Path) -> str:
    (root / "a-hop").symlink_to("z-out")
    (root / "z-out").symlink_to("../x")
    return "a-hop"


def _git_file(root: Path, base: Path) -> str:
    (root / "sub" / "module").mkdir(parents=True)
    (root / "sub" / "module" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/m\n")
    return "sub/module/.git"


@pytest.mark.parametrize("make", [_absolute_link, _escaping_link, _chained_link, _git_file])
@pytest.mark.parametrize("options", [[], ["--keep-copy"]])
def test_a_copy_that_reaches_outside_itself_is_refused_and_removed(bench, make, options):
    """P90: exit 2 naming the path, before validate and launch; the copy goes even if kept."""
    named = make(bench.project, bench.base)
    before = bench.source()
    done = bench.run(*options)
    assert_refused(done)
    assert f"refusing a copy that reaches outside itself: {named}\n" in done.stderr
    assert bench.calls == []
    assert bench.rigs == list(LIVE_RIGS)
    assert bench.copies() == []
    assert bench.source() == before


def test_the_first_offending_path_in_sorted_walk_order_is_named(bench):
    """P90: one path, the first."""
    for name in ("zz", "b/late", "b/early", "c"):
        path = bench.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/nowhere")
    done = bench.run()
    assert_refused(done)
    assert "refusing a copy that reaches outside itself: b/early\n" in done.stderr


def test_links_that_stay_inside_the_copy_and_a_git_dir_are_accepted(bench):
    """P90: only an absolute or escaping link, or a `.git` file."""
    (bench.project / "sub").mkdir()
    (bench.project / "sub" / "back").symlink_to("../notes.txt")
    (bench.project / "sub" / "root").symlink_to("..")
    (bench.project / "dangling").symlink_to("not-there")
    (bench.project / ".git").mkdir()
    (bench.project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    done = bench.run()
    assert done.returncode == 0, done.stderr
    assert bench.verbs() == RUN


# ------------------------------------------------------------------------------------ refusals


@pytest.mark.parametrize(
    "options",
    [
        ["--full"],
        ["--seats", ",".join(ALL_MEMBERS)],
        ["--seats", ",".join(ALL_MEMBERS), "--approval", ROW],
    ],
)
def test_a_full_size_rig_without_approval_is_refused(bench, options):
    """MR 6, also for every member named by --seats."""
    done = bench.run(*options)
    assert done.returncode == 2
    assert NEEDS_APPROVAL in done.stderr
    assert bench.calls == [] and bench.jobs == [] and bench.copies() == []


@pytest.mark.parametrize("row", ["--help", "hacf", "qitem-", "qitem-1 --help", ""])
def test_an_approval_that_is_no_row_id_is_refused(bench, row):
    """An option-like value never reaches `rig`."""
    done = bench.run("--full", f"--approval={row}")
    assert done.returncode == 2
    assert NEEDS_APPROVAL in done.stderr
    assert bench.calls == [] and bench.jobs == []


@pytest.mark.parametrize(
    "how",
    [
        {"queue_rc": 1},
        {"queue_show": "not json"},
        {"queue_show": json.dumps({"qitemId": "qitem-another"})},
    ],
)
def test_an_approval_that_rig_cannot_show_is_refused(bench, how):
    """MR 6: `rig queue show` must succeed."""
    bench.behave(**how)
    done = bench.run("--full", "--approval", ROW)
    assert done.returncode == 2
    assert NEEDS_APPROVAL in done.stderr
    assert bench.jobs == []
    assert_nothing_launched(bench)


def test_an_approved_full_rig_keeps_every_member_and_names_the_row(bench):
    """MR 6: printed and logged."""
    done = bench.run("--full", "--approval", ROW)
    assert done.returncode == 0, done.stderr
    assert ["queue", "show", ROW, "--json"] in bench.calls
    assert ROW in done.stderr and ROW in bench.log_text()
    assert members(validated(bench)) == ALL_MEMBERS
    assert bench.jobs[0][5] == str(7 * 330)
    assert bench.rigs == list(LIVE_RIGS)


@pytest.mark.parametrize(
    "options",
    [
        ["--seats", "orch.nobody"],
        ["--seats", "lead"],
        ["--seats", ""],
        ["--full", "--approval", ROW, "--seats", "orch.lead"],
        ["--approval", ROW],
        ["--name", "hacf"],
        ["--stamp", "20260101T000000"],
    ],
)
def test_a_usage_error_runs_nothing(bench, options):
    """MR 1: exit 2."""
    done = bench.run(*options)
    assert_refused(done)
    assert bench.jobs == [] and bench.copies() == []
    assert_nothing_launched(bench)


def test_a_run_without_a_test_command_is_a_usage_error(bench):
    """MR 1: exit 2."""
    done = bench.run(test=())
    assert_refused(done)
    assert bench.jobs == [] and bench.calls == []


def test_a_project_without_a_rig_spec_is_refused(bench):
    """Nothing to launch."""
    done = bench.run(project=bench.base)
    assert_refused(done)
    assert bench.jobs == [] and bench.calls == []


@pytest.mark.parametrize("tmpdir", [None, "", "/tmp", "/tmp/"])
def test_tmpdir_must_be_set_and_not_tmp(bench, tmpdir):
    """MR 2: the compat script's TMPDIR rule."""
    if tmpdir is None:
        del bench.env["TMPDIR"]
        done = bench.run()
    else:
        done = bench.run(TMPDIR=tmpdir)
    assert_refused(done)
    assert "TMPDIR" in done.stderr
    assert bench.jobs == [] and bench.calls == []


def test_tmpdir_inside_the_project_is_refused(bench):
    """The source is never written."""
    done = bench.run(TMPDIR=str(bench.project / "scratch"))
    assert_refused(done)
    assert not (bench.project / "scratch").exists()
    assert bench.jobs == [] and bench.calls == []


# ------------------------------------------------------------------------------ always cleanup


def test_a_failing_test_is_cleaned_up_and_its_code_returned(bench):
    """MR 8: cleanup on a failing test."""
    done = bench.run(test=probe(bench, 7))
    assert done.returncode == 7, done.stderr
    assert bench.verbs() == RUN
    assert bench.rigs == list(LIVE_RIGS)
    assert bench.copies() == []


def test_a_test_command_that_cannot_run_is_cleaned_up(bench):
    """Exit 127, still cleaned up."""
    done = bench.run(test=(str(bench.base / "no-such-command"),))
    assert done.returncode == 127
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_a_failed_rig_up_runs_no_test_and_is_cleaned_up(bench):
    """A rig may be half launched."""
    bench.behave(up_rc=1)
    done = bench.run(test=probe(bench))
    assert done.returncode == 1, done.stderr
    assert not (bench.base / "probe.json").exists()
    assert bench.verbs() == RUN
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_a_failed_down_that_left_no_rig_keeps_the_test_code(bench):
    """`rig ps` decides."""
    bench.behave(down_rc=1)
    done = bench.run()
    assert done.returncode == 0, done.stderr
    assert bench.copies() == []


@pytest.mark.parametrize(
    "how",
    [
        {"sticky": True},
        {"ps_rc": 2},
        {"ps_out": "not json"},
        {"ps_out": json.dumps({"rigs": []})},
        {"ps_out": json.dumps([{"status": "running"}])},
    ],
)
def test_an_unverified_deletion_is_exit_3_with_the_name(bench, how):
    """MR 8: exit 3 when `rig ps` cannot clear it."""
    bench.behave(**how)
    done = bench.run(test=probe(bench, 7))
    assert done.returncode == 3, done.stderr
    name = bench.call("up")[3]
    assert f"could not verify that {name} is deleted" in done.stderr
    assert name in bench.log_text()
    assert bench.copies() == []


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_stop_signal_during_the_test_cleans_up(bench, signum):
    """MR 8: cleanup on SIGTERM (and SIGINT, SIGHUP), relayed past the scheduler (P89)."""
    started = bench.base / "started"
    process = bench.start(test=(sys.executable, "-c", SLEEPER, str(started)))
    wait_for(started.exists)
    process.send_signal(signum)
    _, err = process.communicate(timeout=30)
    assert process.returncode == 128 + signum, err
    assert bench.verbs() == RUN
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []
    assert list(bench.tmp.glob("*.inner.pid*")) == []


def test_a_test_that_ignores_sigterm_is_killed_and_cleaned_up(bench):
    """A stubborn command is killed."""
    started = bench.base / "started"
    stubborn = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + SLEEPER
    process = bench.start(test=(sys.executable, "-c", stubborn, str(started)))
    wait_for(started.exists)
    process.send_signal(signal.SIGTERM)
    _, err = process.communicate(timeout=30)
    assert process.returncode == 143, err
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_a_signal_during_rig_up_stops_it_then_deletes(bench):
    """P91: `up` still running when cleanup starts is stopped before the delete."""
    bench.behave(up_sleep=60, up_late=3.0)
    process = bench.start(test=probe(bench))
    wait_for((bench.fake / "up-started").exists)
    process.send_signal(signal.SIGTERM)
    _, err = process.communicate(timeout=30)
    assert process.returncode == 143, err
    assert not (bench.fake / "up-done").exists()
    assert not (bench.base / "probe.json").exists()
    assert bench.verbs() == ["spec validate", "up", "up-killed", *CLEAN]
    time.sleep(3.5)  # past the late registration
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def inner(bench, tool) -> int:
    """`_inner` in this process, as the scheduler would run it."""
    argv = ["_inner", "--project", str(bench.project), "--stamp", "20260101T000000"]
    return tool.main([*argv, "--", "true"], fork=False)


def gone(pid: int) -> bool:
    """No live process (a zombie is dead)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


@pytest.mark.parametrize("ignores_term", [False, True])
def test_a_rig_up_past_its_timeout_is_stopped_before_the_delete(
    bench, tool, monkeypatch, ignores_term
):
    """P91: the whole group (SIGTERM, a grace, SIGKILL), reaped; no rig registers afterwards."""
    monkeypatch.setattr(tool, "UP_TIMEOUT_S", 1.0)
    monkeypatch.setattr(tool, "UP_GRACE_S", 0.5)
    bench.behave(up_sleep=60, up_late=3.0, up_ignores_term=ignores_term)
    assert inner(bench, tool) == 1
    late = int((bench.fake / "late.pid").read_text())
    assert gone(late)
    expected = RUN
    if not ignores_term:
        expected = [*expected[:2], "up-killed", *expected[2:]]
    assert bench.verbs() == expected
    time.sleep(2.5)  # past the late registration
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_a_failed_rig_up_has_its_group_stopped_before_the_delete(bench, tool, monkeypatch):
    """P91: a child left behind by a failed `up` cannot register the rig after its delete."""
    monkeypatch.setattr(tool, "UP_GRACE_S", 0.5)
    bench.behave(up_rc=1, up_late=2.0)
    assert inner(bench, tool) == 1
    assert gone(int((bench.fake / "late.pid").read_text()))
    time.sleep(2.5)
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_the_up_grace_is_ten_seconds(tool):
    """P91: the shipped values."""
    assert (tool.UP_GRACE_S, tool.UP_TIMEOUT_S) == (10.0, 600.0)


def test_a_signal_to_the_scheduler_itself_still_lets_cleanup_finish(bench):
    """The P89 residual: `job run` signalled directly kills its job's group after its grace."""
    bench.behave(down_sleep=2.0)
    started = bench.base / "started"
    process = bench.start(test=(sys.executable, "-c", SLEEPER, str(started)), FAKE_JOB_GRACE="0.3")
    wait_for(started.exists)
    os.kill(int(bench.job_pid.read_text()), signal.SIGTERM)
    process.communicate(timeout=30)
    wait_for(lambda: "cleanup done" in bench.log_text())
    assert process.returncode == 128 + signal.SIGKILL
    assert bench.verbs() == RUN
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_the_scheduler_is_in_its_own_session_and_never_signalled(bench):
    """P89: a signal to the run's whole process group reaches neither `job run` nor `rig`."""
    bench.behave(down_sleep=1.0)
    started = bench.base / "started"
    process = bench.start(test=(sys.executable, "-c", SLEEPER, str(started)), FAKE_JOB_GRACE="0.3")
    wait_for(started.exists)
    job = int(bench.job_pid.read_text())
    assert os.getsid(job) == job != os.getsid(process.pid)
    os.killpg(process.pid, signal.SIGINT)  # what a terminal's Ctrl-C does
    _, err = process.communicate(timeout=30)
    assert process.returncode == 130, err
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def lock_is_held(lock: Path) -> bool:
    """True when a non-blocking exclusive lock fails."""
    with lock.open("a") as probe_file:
        try:
            fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe_file, fcntl.LOCK_UN)
    return False


def test_the_heavy_lock_is_held_until_cleanup_has_finished(bench, tool):
    """P89, with the shipped `job run`: a cleanup longer than its kill grace keeps the lock."""
    lock = bench.use_real_scheduler()
    grace = types.ModuleType("hacf_host")
    exec(compile(HOST_TOOL.read_text(), str(HOST_TOOL), "exec"), grace.__dict__)
    bench.behave(down_sleep=grace.KILL_GRACE_S + 2)
    started = bench.base / "started"
    process = bench.start(test=(sys.executable, "-c", SLEEPER, str(started)))
    wait_for(started.exists)
    assert lock_is_held(lock)
    begun = time.monotonic()
    process.send_signal(signal.SIGTERM)
    held_for = 0.0
    while process.poll() is None:
        held = lock_is_held(lock)
        if held:
            held_for = time.monotonic() - begun
        else:  # free only once `rig down` has returned and `rig ps` has been checked
            assert (bench.fake / "down-done").exists()
            assert (bench.fake / "ps-done").exists()
        assert time.monotonic() - begun < 60
        time.sleep(0.05)
    _, err = process.communicate(timeout=10)
    assert process.returncode == 143, err
    assert held_for > grace.KILL_GRACE_S
    assert not lock_is_held(lock)
    assert bench.verbs()[-4:] == CLEAN
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


def test_a_stop_while_queued_is_relayed_once_the_job_starts(bench):
    """P89: the scheduler is not signalled; the stop reaches the job once it is admitted."""
    lock = bench.use_real_scheduler()
    bench.behave(up_sleep=30)  # the relayed stop lands before any test command can start
    lock.parent.mkdir(parents=True)
    with lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        process = bench.start(test=probe(bench))
        queue = bench.base / "host-state" / "queue"
        wait_for(lambda: queue.is_dir() and list(queue.glob("*.json")))
        process.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        assert process.poll() is None
        assert list(queue.glob("*.json"))  # still waiting its turn
    _, err = process.communicate(timeout=30)
    assert process.returncode == 143, err
    assert not (bench.base / "probe.json").exists()
    assert_nothing_launched_or_deleted_after(bench)


def assert_nothing_launched_or_deleted_after(bench) -> None:
    """Any rig a late stop launched was deleted again; the host is as it was."""
    verbs = bench.verbs()
    assert ("up" in verbs) == ("down" in verbs), verbs
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []


@pytest.mark.parametrize(
    "content",
    ["", "not-a-pid", "{other}", "{self}"],
)
def test_a_stop_is_relayed_only_to_this_runs_inner(bench, tool, content):
    """P89: the pid file's process must be `_inner` of this stamp."""
    path = bench.tmp / "x.inner.pid"
    path.write_text(content.format(other=os.getppid(), self=os.getpid()))
    assert tool.inner_pid(path, "20260101T000000") is None


# ------------------------------------------------------------------------- the copy's removal


@pytest.mark.parametrize("where", ["outside", "wrong-name", "nested", "link", "tmpdir"])
def test_only_a_copy_dir_under_tmpdir_is_removed(bench, tool, where):
    """MR 8: removal only under TMPDIR."""
    target = {
        "outside": bench.base / "hacf-test-rig-20260101T000000",
        "wrong-name": bench.tmp / "keep-me",
        "nested": bench.tmp / "other" / "hacf-test-rig-20260101T000000",
        "link": bench.tmp / "hacf-test-rig-20260101T000000",
        "tmpdir": bench.tmp,
    }[where]
    if where == "link":
        real = bench.tmp / "hacf-test-rig-20260101T000001"
        real.mkdir()
        (real / "file").write_text("x")
        target.symlink_to(real)
    else:
        target.mkdir(parents=True, exist_ok=True)
        (target / "file").write_text("x")
    assert tool.remove_copy(target, bench.tmp) is False
    assert (target / "file").read_text() == "x"


def test_a_copy_dir_under_tmpdir_is_removed(bench, tool):
    """The one dir that is removed."""
    copy = bench.tmp / "hacf-test-rig-20260101T000000"
    (copy / "sub").mkdir(parents=True)
    assert tool.remove_copy(copy, bench.tmp) is True
    assert not copy.exists()


# --------------------------------------------------------------------------- the delete guard

MINE = "hacf-test-mine-20261127T120000"
FORGED = [
    "hacf",
    "other-project-hacf",
    "hacf-test-x",
    "hacf-test-other-20261127T115959",
    f"{MINE}\n",
    f"{MINE}0",
    "",
]


@pytest.mark.parametrize("claimed", [MINE, None])
@pytest.mark.parametrize("name", FORGED)
def test_a_forged_name_reaching_cleanup_deletes_nothing(bench, tool, capsys, name, claimed):
    """P87: no `rig down`, the refusal line, exit 3."""
    if claimed:
        tool.claim_run_name(claimed)
    copy = bench.tmp / "hacf-test-rig-20261127T120000"
    copy.mkdir()
    assert tool.cleanup(name, copy, bench.tmp, keep=False) == 3
    assert f"refusing to delete {name}: not this run's test rig" in capsys.readouterr().err
    assert bench.calls == []
    assert "--delete" not in json.dumps(bench.calls)
    assert bench.rigs == list(LIVE_RIGS)


@pytest.mark.parametrize("name", ["hacf", "other-project-hacf", "hacf-test-x"])
def test_a_forged_name_is_refused_even_when_it_is_the_runs_variable(bench, tool, capsys, name):
    """P87 (a) alone."""
    tool._RUN_NAME = name  # the pattern alone must stop it
    assert tool.delete_rig(name) is False
    assert "refusing to delete" in capsys.readouterr().err
    assert bench.calls == []


def test_the_runs_name_is_claimed_once_and_must_match(bench, tool):
    """P87: one variable, set once."""
    with pytest.raises(tool.Refused):
        tool.claim_run_name("hacf")
    assert tool._RUN_NAME is None
    tool.claim_run_name(MINE)
    with pytest.raises(tool.Refused):
        tool.claim_run_name("hacf-test-second-20261127T120001")
    assert tool._RUN_NAME == MINE


def test_the_runs_own_name_is_deleted(bench, tool):
    """The guard lets this run's name through."""
    tool.claim_run_name(MINE)
    (bench.fake / "rigs.json").write_text(json.dumps([*LIVE_RIGS, MINE]))
    assert tool.delete_rig(MINE) is True
    assert bench.calls == [["down", MINE, "--delete", "--yes", "--json"], ["ps", "--json"]]
    assert bench.rigs == list(LIVE_RIGS)


@pytest.mark.parametrize(
    ("basename", "slug"),
    [
        ("Hypergraph-Agentic-Coding-Framework", "hypergraph-agentic-coding-framework"),
        ("_My  Project (v2)", "my-project-v2-"),
        ("a.b_c", "a.b_c"),
    ],
)
def test_the_slug(tool, basename, slug):
    """P87: the project's part of the name."""
    assert tool.project_slug(basename) == slug


def test_a_basename_with_an_empty_slug_launches_nothing(bench):
    """P87: exit 2."""
    project = bench.make_project("___")
    done = bench.run(project=project)
    assert_refused(done)
    assert bench.jobs == [] and bench.calls == []


def test_a_name_failing_the_pattern_before_launch_runs_no_rig_up(bench, tool, monkeypatch):
    """P87: exit 2 before `rig up`."""
    monkeypatch.setattr(tool, "project_slug", lambda _name: "Not A Slug")
    assert inner(bench, tool) == 2
    assert_nothing_launched(bench)
    assert bench.copies() == []


# ------------------------------------------------------------------------- the stub destination

STAMP = "20260101T000000"
SLUG = "hypergraph-agentic-coding-framework"
FIXED = f"hacf-test-{SLUG}-{STAMP}"  # the name of an `inner` run
STUB = f"orch-lead@{FIXED}"
ENV_PROBE = """\
import json, os, sys
from pathlib import Path
import yaml
copy = Path(os.environ["HACF_TEST_RIG_COPY"])
kit = copy / "openrig" / "hacf"
seen = {"stub": os.environ.get("HACF_TEST_RIG_STUB"), "rig": os.environ["HACF_TEST_RIG"]}
seen["overrides"] = yaml.safe_load((kit / "overrides.yml").read_text())
Path(sys.argv[1]).write_text(json.dumps(seen))
"""


def env_probe(bench) -> tuple[str, ...]:
    """A command recording the stub and the copy's overrides.yml."""
    return (sys.executable, "-c", ENV_PROBE, str(bench.base / "probe.json"))


def own_overrides(bench, text: str) -> Path:
    """Write the source project's overrides.yml."""
    path = bench.project / "openrig" / "hacf" / "overrides.yml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("existing", [False, True])
def test_the_copy_pins_both_destinations_to_the_stub(bench, existing):
    """(k), (o): with and without an overrides.yml; its other keys are kept."""
    if existing:
        own_overrides(
            bench,
            "seats: {lead: {effort: high}}\n"
            "routing:\n"
            "  question: {destination: {session: someone@external}}\n"
            "  tags: {question: 'ask:me', update: u, answer: a, extra: []}\n",
        )
    before = bench.source()
    done = bench.run(test=env_probe(bench))
    assert done.returncode == 0, done.stderr
    seen = probed(bench)
    stub = f"orch-lead@{seen['rig']}"
    assert seen["stub"] == stub
    pinned = {"destination": {"session": stub}}
    routing = seen["overrides"]["routing"]
    assert (routing["question"], routing["update"]) == (pinned, pinned)
    if existing:
        assert seen["overrides"]["seats"] == {"lead": {"effort": "high"}}
        assert routing["tags"]["question"] == "ask:me"
    else:
        assert seen["overrides"] == {"routing": {"question": pinned, "update": pinned}}
    assert f"stub destination {stub}" in bench.log_text()
    assert bench.source() == before


@pytest.mark.parametrize(
    "text",
    [
        "",
        "- a\n",
        "just text\n",
        "a: [\n",
        "routing: 5\n",
        "routing: {question: 5}\n",
        "routing:\n",
    ],
)
def test_an_overrides_file_that_is_no_mapping_is_refused_before_launch(bench, text):
    """(l): exit 2, nothing launched."""
    own_overrides(bench, text)
    done = bench.run()
    assert_refused(done)
    assert "cannot pin the routing" in done.stderr
    assert bench.calls == [] and bench.copies() == []
    assert_nothing_launched(bench)


@pytest.mark.parametrize(
    ("stdout", "rc"),
    [
        ({"question.destination": {"rule": "discover"}, "update.destination": None}, 0),
        ({"question.destination": None, "update.destination": {"session": "advisor@kernel"}}, 0),
        ({"question.destination": None}, 0),
        ({"question.destination": None, "update.destination": None}, 1),
        ("not json", 0),
    ],
)
def test_a_resolve_that_does_not_return_the_stub_is_refused_before_launch(bench, stdout, rc):
    """(m): both destinations must be the stub; None below stands for the right one."""
    script = bench.project / "openrig" / "hacf" / "bin" / "seat-config.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "import re\n"
        "kit = Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "stub = re.search(r'session: (\\S+)', (kit / 'overrides.yml').read_text())[1]\n"
        f"said = {stdout!r}\n"
        "if isinstance(said, dict):\n"
        "    said = json.dumps({'routing': {k: {'value': v or {'session': stub}}\n"
        "                                   for k, v in said.items()}})\n"
        "print(said)\n"
        f"sys.exit({rc})\n"
    )
    done = bench.run()
    assert_refused(done)
    assert "does not resolve" in done.stderr
    assert bench.calls == [] and bench.copies() == []
    assert_nothing_launched(bench)


@pytest.mark.parametrize("seats", ["build.execute,orch.lead", "orch.lead,build.execute"])
def test_the_stub_is_the_first_chosen_seat_in_the_specs_order(bench, seats):
    """(n): deterministic whatever order the seats are typed in."""
    done = bench.run("--seats", seats, test=env_probe(bench))
    assert done.returncode == 0, done.stderr
    seen = probed(bench)
    assert seen["stub"] == f"orch-lead@{seen['rig']}"


def test_the_stub_without_the_lead_is_that_runs_own_seat(bench):
    """(n): the stub is always a seat of the run."""
    done = bench.run("--seats", "build.audit,build.execute", test=env_probe(bench))
    assert done.returncode == 0, done.stderr
    seen = probed(bench)
    assert seen["stub"] == f"build-execute@{seen['rig']}"


# --------------------------------------------------------------------------- the run's rows (P109)


def queue_row(qitem: str, source: str, destination: str, state: str = "pending") -> dict:
    """One compact queue row."""
    return {
        "qitemId": f"qitem-{qitem}",
        "state": state,
        "sourceSession": source,
        "destinationSession": destination,
    }


MINE_ROWS = [
    queue_row("to-stub", "hacf-host@host", STUB),
    queue_row("from-seat", f"build-execute@{FIXED}", "advisor-lead@kernel", "blocked"),
    queue_row("from-stub", STUB, STUB, "in-progress"),
]
OTHER_ROWS = [
    queue_row("real", "orch-lead@hacf", "advisor-lead@kernel"),
    queue_row("longer", f"orch-lead@{FIXED}0", f"orch-lead@{FIXED}0"),
    queue_row("suffix", f"orch-lead@{FIXED}-hacf", f"x{STUB}"),
    queue_row("no-at", FIXED, f"build-execute@{FIXED}"),  # to a seat that is not the stub
]


def updates(bench) -> list[list[str]]:
    """The `rig queue update` calls."""
    return [c for c in bench.calls if c[:2] == ["queue", "update"]]


def test_cleanup_closes_the_runs_rows_before_the_delete(bench, tool, capsys):
    """(p): rows to the stub or from the run's seats, and no other row."""
    bench.behave(rows=MINE_ROWS + OTHER_ROWS)
    assert inner(bench, tool) == 0
    assert [c[2] for c in updates(bench)] == [r["qitemId"] for r in MINE_ROWS]
    for call in updates(bench):
        assert call[3:7] == ["--state", "done", "--closure-reason", "canceled"]
        assert call[7] == "--note" and FIXED in call[8]
    verbs = bench.verbs()
    assert verbs.index("down") > max(i for i, v in enumerate(verbs) if v == "queue update")
    log = bench.log_text()
    assert all(f"closed row {r['qitemId']} (" in log for r in MINE_ROWS)
    assert f"no row of {FIXED} is open" in log
    assert not any(r["qitemId"] in log for r in OTHER_ROWS)


def test_a_failed_close_still_deletes_and_exits_3_naming_the_row(bench, tool):
    """(p)."""
    bench.behave(rows=MINE_ROWS, close_fails=["qitem-from-seat"])
    assert inner(bench, tool) == 3
    assert len(updates(bench)) == 3 and "down" in bench.verbs()
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []
    log = bench.log_text()
    assert "could not close row qitem-from-seat" in log
    assert "rows still open: qitem-from-seat" in log


@pytest.mark.parametrize(
    "how",
    [
        {"list_rc": 1},
        {"list_out": "not json"},
        {"list_out": json.dumps([queue_row("x", "a@b", "c@d")] * 10000)},
        {"list_out": json.dumps([{"qitemId": "--help", "state": "pending",
                                  "destinationSession": STUB}])},
    ],
)  # fmt: skip
def test_an_unknown_row_list_still_deletes_and_exits_3(bench, tool, how):
    """(p): unknown rows are never called closed."""
    bench.behave(**how)
    assert inner(bench, tool) == 3
    assert "down" in bench.verbs() and updates(bench) == []
    assert bench.rigs == list(LIVE_RIGS) and bench.copies() == []
    assert f"could not verify that no row of {FIXED} is open" in bench.log_text()
