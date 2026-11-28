"""Tests of the idle point: `idle-point request|wait|release|status` (OPR.99.0.16.7).

Every test runs on the isolated host of conftest.py, with test_autopark's fleet of fake rigs. The
UTC clock, the monotonic clock and the engine's waits are fakes, so no test waits.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from .test_autopark import IN_PROGRESS, LOW, RIG, START, Fleet, stamp, work
from .test_core import cli, put

THIRD = "third-hacf"
REQUEST = ["request", "--purpose", "openrig-upgrade:9.9.9"]


@pytest.fixture
def three(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """Three mapped rigs, running and at a boundary; a wait moves both fake clocks."""
    fleet = Fleet(host, tool, monkeypatch, (RIG, LOW, THIRD))

    def sleep(seconds: float) -> None:
        fleet.sleep(seconds)
        fleet.now += timedelta(seconds=seconds)

    monkeypatch.setattr(tool, "pause_for", sleep)
    return fleet


def verb(fleet: Fleet, capsys, *argv: str) -> tuple[int, str, str]:
    """`idle-point <argv>` in-process: (exit code, stdout, stderr)."""
    capsys.readouterr()
    code = fleet.tool.main(["idle-point", *argv])
    done = capsys.readouterr()
    return code, done.out, done.err


def point_path(fleet: Fleet) -> Path:
    """The idle point's file."""
    return fleet.host.state / "idle-point.json"


def point(fleet: Fleet) -> dict:
    """`idle-point.json`."""
    return json.loads(point_path(fleet).read_text())


def request(fleet: Fleet, capsys, *extra: str) -> None:
    """Put an idle point in force."""
    code, _, err = verb(fleet, capsys, *REQUEST, *extra)
    assert code == 0, err


def status(fleet: Fleet, capsys) -> dict:
    """`idle-point status --json`."""
    code, out, err = verb(fleet, capsys, "status", "--json")
    assert code == 0, err
    return json.loads(out)


def paused(fleet: Fleet, name: str) -> None:
    """The rig is marked paused by the person, and still runs."""
    fleet.put_record(name, state="paused-by-person", by="person")


def waited(fleet: Fleet) -> float:
    """The seconds the engine waited."""
    return sum(fleet.waits)


# ------------------------------------------------------------------------------- request


def test_request_writes_the_idle_point_for_six_hours(three, capsys, monkeypatch) -> None:
    """MR 1: the four fields, in a host state file."""
    monkeypatch.setenv("OPENRIG_SESSION_NAME", "operator-agent@kernel")
    request(three, capsys)
    assert point(three) == {
        "schema": "hacf.host/v1",
        "purpose": "openrig-upgrade:9.9.9",
        "requested_by": "operator-agent@kernel",
        "at": stamp(START),
        "expires_at": stamp(START + timedelta(hours=6)),
    }
    assert three.rig.calls == []


def test_request_outside_a_seat_names_the_user_and_takes_a_ttl(three, capsys, monkeypatch) -> None:
    """MR 1: `--ttl` is in hours."""
    monkeypatch.setenv("USER", "someone")
    request(three, capsys, "--ttl", "1.5")
    assert point(three)["requested_by"] == "someone"
    assert point(three)["expires_at"] == stamp(START + timedelta(minutes=90))


@pytest.mark.parametrize("ttl", ["0", "-1", "nan", "inf", "1e300", "soon"])
def test_request_refuses_an_unusable_ttl(three, capsys, ttl: str) -> None:
    """A drain always ends."""
    try:
        code = three.tool.main(["idle-point", *REQUEST, "--ttl", ttl])
    except SystemExit as raised:
        code = raised.code
    assert code == 2 and "--ttl" in capsys.readouterr().err
    assert not point_path(three).exists()


@pytest.mark.parametrize("purpose", ["", "  "])
def test_request_refuses_an_empty_purpose(three, capsys, purpose: str) -> None:
    """The purpose is what a held job and a deferred lead are told."""
    code, _, err = verb(three, capsys, "request", "--purpose", purpose)
    assert code == 2 and "purpose" in err
    assert not point_path(three).exists()


def test_request_needs_a_purpose() -> None:
    """MR 1: `--purpose` is required."""
    done = cli("idle-point", "request")
    assert done.returncode == 2 and "--purpose" in done.stderr


def test_a_second_request_exits_1_naming_the_first(three, capsys) -> None:
    """MR 1: the one in force is left as it is."""
    request(three, capsys)
    before = point_path(three).read_bytes()
    code, _, err = verb(three, capsys, "request", "--purpose", "other")
    assert code == 1
    assert "openrig-upgrade:9.9.9" in err and stamp(START + timedelta(hours=6)) in err
    assert point_path(three).read_bytes() == before


def test_an_unreadable_idle_point_refuses_a_request(three, capsys) -> None:
    """Unreadable counts as in force; only `release` removes it."""
    three.host.state.mkdir(parents=True)
    point_path(three).write_text("{broken")
    code, _, err = verb(three, capsys, *REQUEST)
    assert code == 1 and "unknown" in err
    assert point_path(three).read_text() == "{broken"
    assert status(three, capsys)["in_force"] is True
    assert verb(three, capsys, "release")[0] == 0
    assert not point_path(three).exists()


# ------------------------------------------------------------------------------- release, expiry


def test_release_removes_it_and_is_quiet_without_one(three, capsys) -> None:
    """MR 1."""
    request(three, capsys)
    code, out, _ = verb(three, capsys, "release")
    assert code == 0 and "released" in out and "openrig-upgrade:9.9.9" in out
    assert not point_path(three).exists()
    code, out, _ = verb(three, capsys, "release")
    assert code == 0 and "no idle point" in out


def test_an_expired_idle_point_counts_as_absent_everywhere(three, capsys) -> None:
    """MR 1: status says so, the scheduler and restores ignore it, wait refuses, request works."""
    request(three, capsys, "--ttl", "1")
    three.now += timedelta(hours=1)
    report = status(three, capsys)
    assert (report["in_force"], report["expired"]) == (False, True)
    assert report["idle_point"]["purpose"] == "openrig-upgrade:9.9.9"
    code, out, _ = verb(three, capsys, "status")
    assert code == 0 and "expired" in out
    assert three.tool.idle_point(three.host.state) is None
    assert three.tool.job_status()["idle_point"] is None
    code, _, err = verb(three, capsys, "wait")
    assert code == 1 and "request first" in err
    request(three, capsys)
    assert point(three)["at"] == stamp(three.now)


def test_status_shows_the_one_in_force_and_none(three, capsys) -> None:
    """MR 1: both forms; status writes nothing."""
    report = status(three, capsys)
    assert report == {
        "schema": "hacf.host-idle-point/v1",
        "in_force": False,
        "expired": False,
        "idle_point": None,
    }
    code, out, _ = verb(three, capsys, "status")
    assert code == 0 and "none" in out
    assert not three.host.state.exists()
    request(three, capsys)
    report = status(three, capsys)
    assert (report["in_force"], report["expired"]) == (True, False)
    assert report["idle_point"]["expires_at"] == stamp(START + timedelta(hours=6))
    code, out, _ = verb(three, capsys, "status")
    assert code == 0 and "openrig-upgrade:9.9.9" in out


# ------------------------------------------------------------------------------- R200


@pytest.mark.parametrize("argv", [REQUEST, ["release"]])
def test_a_seat_of_a_mapped_rig_is_refused(three, capsys, monkeypatch, argv: list) -> None:
    """R200: a HACF seat never drains the host, or ends a drain."""
    request(three, capsys)
    before = point_path(three).read_bytes()
    monkeypatch.setenv("OPENRIG_SESSION_NAME", f"orch-lead@{RIG}")
    code, _, err = verb(three, capsys, *argv)
    assert code == 2 and f"orch-lead@{RIG} is a seat of the mapped rig {RIG}" in err
    assert point_path(three).read_bytes() == before
    assert three.rig.calls == []


def test_a_seat_of_a_mapped_rig_may_read_the_status(three, capsys, monkeypatch) -> None:
    """lead.md's read before each new slice handoff."""
    request(three, capsys)
    monkeypatch.setenv("OPENRIG_SESSION_NAME", f"orch-lead@{RIG}")
    assert status(three, capsys)["in_force"] is True


@pytest.mark.parametrize("argv", [REQUEST, ["release"], ["wait", "--timeout", "0"]])
def test_a_newer_host_state_exits_75(three, capsys, argv: list) -> None:
    """Slice 01's rule: nothing is written and nothing is judged."""
    put(three.host.state / "rigs" / "x.json", {"schema": "hacf.host/v2"})
    code, _, err = verb(three, capsys, *argv)
    assert code == 75 and "newer HACF" in err
    assert not point_path(three).exists()


# ------------------------------------------------------------------------------- wait


def test_wait_without_a_request_exits_1(three, capsys) -> None:
    """MR 2."""
    code, _, err = verb(three, capsys, "wait")
    assert code == 1 and "request first" in err
    assert three.rig.calls == [] and three.waits == []


def test_wait_reaches_idle_with_a_boundary_a_parked_and_a_paused_rig(three, capsys) -> None:
    """MR 2 and 6: each rig with why it is idle, and no `rig` call that changes anything."""
    three.parked(LOW)
    paused(three, THIRD)
    three.set_queue(work("w-3", rig=THIRD, state="in-progress"))
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait")
    assert code == 0, err
    assert out == f"idle reached: {RIG}: boundary, {LOW}: parked, {THIRD}: paused\n"
    assert three.waits == [] and three.mutations() == []


def test_wait_counts_a_file_listed_rig_as_paused(three, capsys) -> None:
    """R183: the person's paused file."""
    listed = three.host.base / "paused.txt"
    listed.write_text(f"{RIG} until Monday\n")
    three.config(extra=f"paused_files: [{listed}]\n")
    three.seats(RIG, activity="working")
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait")
    assert code == 0, err
    assert f"{RIG}: paused" in out


def test_wait_with_no_running_rig_is_idle(three, capsys) -> None:
    """Nothing runs, so nothing is in flight."""
    three.rig.reply("ps --json", [])
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait")
    assert code == 0, err
    assert out == "idle reached: no rig is running\n"


def test_wait_times_out_naming_each_rig_in_flight(three, capsys) -> None:
    """MR 2: exit 75, polling every 30 s."""
    three.seats(LOW, activity="working")
    three.set_queue(work("w-9", rig=THIRD, state="in-progress"))
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait", "--timeout", "90")
    assert code == 75, out
    assert three.waits == [30, 30, 30]
    assert "idle reached" not in out
    lines = err.splitlines()
    assert any(x.startswith(f"  {LOW}: B1: ") and "working" in x for x in lines), err
    assert any(x.startswith(f"  {THIRD}: B2: ") and "w-9" in x for x in lines), err
    assert not any(x.startswith(f"  {RIG}: ") for x in lines), err
    assert three.mutations() == []


def test_wait_waits_one_hour_by_default(three, capsys) -> None:
    """MR 2."""
    three.seats(LOW, activity="working")
    request(three, capsys)
    assert verb(three, capsys, "wait")[0] == 75
    assert waited(three) == 3600


def test_the_last_wait_ends_at_the_timeout(three, capsys) -> None:
    """A timeout shorter than the poll is honoured."""
    three.seats(LOW, activity="working")
    request(three, capsys)
    assert verb(three, capsys, "wait", "--timeout", "45")[0] == 75
    assert three.waits == [30, 15]


def test_wait_ends_when_the_rig_reaches_its_boundary(three, capsys, monkeypatch) -> None:
    """A later poll sees the finished slice."""
    three.seats(LOW, activity="working")
    request(three, capsys)

    def finish(seconds: float) -> None:
        three.sleep(seconds)
        three.seats(LOW)

    monkeypatch.setattr(three.tool, "pause_for", finish)
    code, out, err = verb(three, capsys, "wait")
    assert code == 0, err
    assert f"{LOW}: boundary" in out and three.waits == [30]


@pytest.mark.parametrize("how", ["expires", "released"])
def test_wait_exits_1_when_the_idle_point_ends(three, capsys, monkeypatch, how: str) -> None:
    """No idle is reported for a drain that is over."""
    three.seats(LOW, activity="working")
    request(three, capsys, "--ttl", "1")

    def end(seconds: float) -> None:
        three.sleep(seconds)
        three.seats(LOW)
        if how == "expires":
            three.now += timedelta(hours=2)
        else:
            point_path(three).unlink()

    monkeypatch.setattr(three.tool, "pause_for", end)
    code, out, err = verb(three, capsys, "wait")
    assert code == 1 and "request first" in err and "idle reached" not in out


def test_an_unmapped_running_rig_is_in_flight(three, capsys) -> None:
    """R185: even when the person's paused file names it."""
    three.status["stranger"] = "running"
    three.rig.reply("ps --json", three.rigs())
    listed = three.host.base / "paused.txt"
    listed.write_text("stranger\n")
    three.config(extra=f"paused_files: [{listed}]\n")
    three.roots.pop("stranger", None)
    request(three, capsys)
    code, _, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75
    assert "  stranger: unmapped" in err.splitlines()


def test_an_ignored_rig_is_not_counted(three, capsys) -> None:
    """MR 2: ignored wins, mapped or not."""
    three.status["kernel"] = "running"
    three.seats(LOW, activity="working")
    three.config(extra=f"ignore_rigs: [kernel, {LOW}]\n")
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 0, err
    assert "kernel" not in out and LOW not in out
    assert not [c for c in three.calls() if LOW in c or "kernel" in c]


def test_a_partly_running_rig_is_counted(three, capsys) -> None:
    """Only a rig `rig ps` shows stopped is out of the count."""
    three.seats(LOW, activity="working")
    rows = three.rigs()
    for row in rows:
        row["status"] = "partial" if row["name"] == LOW else row["status"]
    three.rig.reply("ps --json", rows)
    request(three, capsys)
    code, _, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75 and f"  {LOW}: B1: " in err


def test_a_parked_record_with_a_running_seat_is_judged_at_its_boundary(three, capsys) -> None:
    """A parked rig someone started again may be working."""
    three.put_record(LOW, state="parked-waiting", by="supervisor")
    three.seats(LOW, activity="working")
    request(three, capsys)
    code, _, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75 and f"  {LOW}: B1: " in err


def test_a_parked_record_with_no_running_seat_is_parked(three, capsys) -> None:
    """The rig list may still show it up."""
    three.put_record(LOW, state="parked-waiting", by="supervisor")
    three.rig.reply(f"ps --nodes --rig {LOW} --full --json", three.nodes(LOW, "exited"))
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 0, err
    assert f"{LOW}: parked" in out


def test_an_unknown_rig_list_is_never_idle(three, capsys) -> None:
    """Unknown is not idle."""
    three.rig.reply("ps --json", "", rc=1, stderr="daemon down\n")
    request(three, capsys)
    code, out, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75 and "unknown: rig ps failed" in err and "daemon down" in err
    assert "idle reached" not in out


def test_an_unknown_queue_is_never_idle(three, capsys) -> None:
    """B2 unknown holds the wait."""
    three.rig.reply(IN_PROGRESS, "", rc=1)
    request(three, capsys)
    code, _, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75 and "B2: unknown" in err


def lock_path() -> Path:
    """The heavy lock of the isolated host."""
    return Path(os.environ["HACF_HEAVY_LOCK_PATH"])


def test_a_heavy_holder_blocks_idle(three, capsys) -> None:
    """MR 2 and 6: held by anyone, with or without a record."""
    request(three, capsys)
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        code, out, err = verb(three, capsys, "wait", "--timeout", "0")
        assert code == 75 and "idle reached" not in out
        assert any(x.startswith("  heavy job: ") for x in err.splitlines()), err
    finally:
        os.close(fd)
    code, out, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 0, err


def test_a_real_job_holds_the_idle_point(three, capsys) -> None:
    """A lease taken through the scheduler is a holder, named in the timeout."""
    with three.tool.acquire("bench", 5, rig_name=None):
        request(three, capsys)
        code, _, err = verb(three, capsys, "wait", "--timeout", "0")
    assert code == 75 and "heavy job: " in err and "bench" in err


@pytest.mark.parametrize("argv", [["wait", "--timeout", "-1"], ["wait", "--timeout", "x"]])
def test_wait_refuses_a_bad_timeout(three, argv: list) -> None:
    """A usage error."""
    with pytest.raises(SystemExit) as raised:
        three.tool.main(["idle-point", *argv])
    assert raised.value.code == 2
