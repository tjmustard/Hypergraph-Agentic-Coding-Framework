"""Tests of the cross-rig status view (OPR.99.0.16.8): `status --json` and `status --md`."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import TOOL

SCHEMA = "hacf.host/v1"


def cli(*argv: str) -> subprocess.CompletedProcess:
    """Run the tool."""
    return subprocess.run(
        [sys.executable, str(TOOL), *argv], capture_output=True, text=True, timeout=60, check=False
    )


def utc(hours_ago: float = 0.0) -> str:
    """A state file time, that many hours ago."""
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def put(path: Path, data: object) -> None:
    """Write a state file; a string is written as it is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def record(host, rig_name: str, state: str, **extra: object) -> None:
    """A rig record."""
    put(host.state / "rigs" / f"{rig_name}.json", {"schema": SCHEMA, "state": state, **extra})


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def report_of(done: subprocess.CompletedProcess) -> tuple[dict, dict]:
    """The report of a successful `status --json`, and its rows by name."""
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    return report, {row["name"]: row for row in report["rigs"]}


@pytest.fixture
def three(host) -> Path:
    """A mapped, an unmapped and an ignored rig, all running."""
    """A mapped, an unmapped and an ignored rig, all running."""
    root = host.project("one")
    host.write_config(f"projects: {{m-rig: {root}}}\nignore_rigs: [i-rig]\n")
    host.rig.running("m-rig", "u-rig", "i-rig")
    return root


# ------------------------------------------------------------------------------ MR 3: the rigs


def test_three_rigs_each_with_its_state(host, three) -> None:
    """MR 3: all three appear, from one `rig ps`."""
    report, rows = report_of(cli("status", "--json"))
    assert {name: row["state"] for name, row in rows.items()} == {
        "m-rig": "running",
        "u-rig": "unmapped",
        "i-rig": "ignored",
    }
    assert rows["m-rig"] == {
        "name": "m-rig",
        "mapped": True,
        "project": str(three),
        "ignored": False,
        "tier": 0,
        "state": "running",
        "since": None,
        "reason": None,
        "waits_on": [],
        "parks_24h": 0,
        "last_restore_s": None,
        "ps_status": "running",
    }
    assert report["rig_ps_error"] is None
    assert host.rig.calls == [["ps", "--json"]]


def test_a_stopped_unlisted_rig_is_not_listed(host) -> None:
    """Nothing names it and it does not run."""
    host.rig.reply("ps --json", [{"name": "gone", "status": "stopped"}])
    _, rows = report_of(cli("status", "--json"))
    assert rows == {}


def test_a_mapped_stopped_rig_says_so(host, three) -> None:
    """The HACF mark and what `rig ps` shows are both there."""
    host.rig.reply("ps --json", [{"name": "m-rig", "status": "stopped"}])
    _, rows = report_of(cli("status", "--json"))
    assert (rows["m-rig"]["state"], rows["m-rig"]["ps_status"]) == ("running", "stopped")


def test_failed_rig_ps_is_named_never_empty(host, three) -> None:
    """Unknown is said, in both forms."""
    host.rig.reply("ps --json", "", rc=3, stderr="daemon down\n")
    done = cli("status", "--json")
    report, rows = report_of(done)
    assert report["rig_ps_error"] == "exit 3: daemon down"
    assert rows["m-rig"]["ps_status"] is None
    assert "rig ps failed" in done.stderr
    md = cli("status", "--md")
    assert md.returncode == 0, md.stderr
    assert "rig ps failed: exit 3: daemon down" in (host.state / "status.md").read_text()


# ------------------------------------------------------------------------- the HACF states (R183)


def test_parked_and_file_listed_shows_both(host, three, tmp_path) -> None:
    """R183: a parked, file-listed rig shows both."""
    listing = tmp_path / "paused.txt"
    listing.write_text("m-rig  # on hold\nonly-listed\n")
    host.write_config(f"projects: {{m-rig: {three}}}\npaused_files: [{listing}]\n")
    host.rig.running()
    record(host, "m-rig", "parked-waiting", since=utc(1), reason="waits", waits_on=["q-1"])
    _, rows = report_of(cli("status", "--json"))
    row = rows["m-rig"]
    assert (row["state"], row["in_paused_file"]) == ("parked-waiting", str(listing))
    assert (row["reason"], row["waits_on"]) == ("waits", ["q-1"])
    assert row["since"] is not None
    assert rows["only-listed"]["state"] == "unmapped"
    assert rows["only-listed"]["in_paused_file"] == str(listing)


@pytest.mark.parametrize("state", [None, "running"])
def test_a_file_listed_rig_is_paused(host, three, tmp_path, state) -> None:
    """R183: the file alone pauses a running rig."""
    listing = tmp_path / "paused.txt"
    listing.write_text("m-rig\n")
    host.write_config(f"projects: {{m-rig: {three}}}\npaused_files: [{listing}]\n")
    if state:
        record(host, "m-rig", state)
    _, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["state"] == "paused-by-person"
    assert rows["m-rig"]["in_paused_file"] == str(listing)


def test_a_recorded_pause_has_no_file_key(host, three) -> None:
    """`in_paused_file` only when a file names it."""
    record(host, "m-rig", "paused-by-person", reason="holiday")
    _, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["state"] == "paused-by-person"
    assert "in_paused_file" not in rows["m-rig"]


def test_an_unreadable_paused_file_is_said(host, three, tmp_path) -> None:
    """A running rig may be paused by that file."""
    host.write_config(f"projects: {{m-rig: {three}}}\npaused_files: [{tmp_path / 'absent'}]\n")
    done = cli("status", "--json")
    _, rows = report_of(done)
    assert "may be paused" in rows["m-rig"]["reason"]
    assert "cannot be read" in done.stderr


def test_restore_failed(host, three) -> None:
    """The failed mark and its reason."""
    record(host, "m-rig", "restore-failed", reason="partial", since=utc(2))
    _, rows = report_of(cli("status", "--json"))
    assert (rows["m-rig"]["state"], rows["m-rig"]["reason"]) == ("restore-failed", "partial")


@pytest.mark.parametrize(
    "content", ["{broken", '{"schema": "hacf.host/v1", "state": "odd"}', '{"schema": "x/v9"}']
)
def test_an_unreadable_record_has_no_state(host, three, content: str) -> None:
    """Never guessed, never rewritten (P85)."""
    path = host.state / "rigs" / "m-rig.json"
    put(path, content)
    done = cli("status", "--json")
    _, rows = report_of(done)
    assert rows["m-rig"]["state"] is None
    assert rows["m-rig"]["parks_24h"] is None
    assert rows["m-rig"]["reason"] == f"unreadable record: {path}"
    assert f"unreadable record: {path}" in done.stderr
    assert path.read_text() == content


def test_an_unmapped_rig_with_a_record_stays_unmapped(host) -> None:
    """R185, with the record in the reason."""
    record(host, "left", "parked-waiting")
    _, rows = report_of(cli("status", "--json"))
    assert rows["left"]["state"] == "unmapped"
    assert "parked-waiting" in rows["left"]["reason"]


# ------------------------------------------------------------------ park cycles and restore time


def test_parks_24h_counts_from_parks(host, three) -> None:
    """MR 6: only the last 24 h count."""
    record(host, "m-rig", "running", parks=[utc(30), utc(23), utc(1)])
    _, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["parks_24h"] == 2
    assert rows["m-rig"]["reason"] is None


def test_the_park_cap_is_shown(host, three) -> None:
    """R181: `park cap reached`."""
    record(host, "m-rig", "running", parks=[utc(3), utc(2), utc(1)])
    _, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["parks_24h"] == 3
    assert "park cap reached" in rows["m-rig"]["reason"]


def test_a_park_list_that_cannot_be_read_is_unknown(host, three) -> None:
    """Not zero."""
    record(host, "m-rig", "running", parks="three")
    _, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["parks_24h"] is None


def log_lines() -> list[str]:
    """A log with cycles, other decisions and a day of skips."""
    lines = [
        "2026-01-01T00:00:00Z restore m-rig done seconds=99.0 trigger=q-0",
        "2026-01-01T00:01:00Z park m-rig done seconds=12.5 waits_on=q-1",
        "2026-01-01T00:02:00Z restore m-rig done seconds=41.5 trigger=q-1",
        "2026-01-01T00:03:00Z restore m-rig failed seconds=900.0 trigger=q-2 timed out",
        "2026-01-01T00:04:00Z restore deferred m-rig: ram seconds=0.1 trigger=q-3",
        "2026-01-01T00:04:00Z restore queued: m-rig (next tick)",
        "2026-01-01T00:04:00Z park queued: m-rig (next tick)",
        "2026-01-01T00:04:00Z would-park m-rig waits_on=q-4",
        "2026-01-01T00:04:00Z would-restore m-rig trigger=q-4",
        "2026-01-01T00:05:00Z supervise - tick start",
    ]
    # A day of skip lines buries the cycles far past the log's last 256 KB.
    return (
        lines
        + [f"2026-01-01T00:06:00Z skip m-rig: {'x' * 200}"] * 2000
        + [
            "2026-01-01T00:07:00Z supervise - tick start (dry-run)",
            "2026-01-01T00:07:00Z park m-rig aborted seconds=1.0 waits_on=q-5 changed",
        ]
    )


def test_last_restore_and_the_park_log(host, three) -> None:
    """Only parks and restores that ran; the last done one."""
    put(host.state / "supervisor.log", "\n".join(log_lines()) + "\n")
    report, rows = report_of(cli("status", "--json"))
    assert rows["m-rig"]["last_restore_s"] == 41.5
    assert [line.split()[0][11:16] for line in report["park_log"]] == [
        "00:00", "00:01", "00:02", "00:03", "00:07",
    ]  # fmt: skip
    assert report["supervisor"]["last_tick"] == "2026-01-01T00:07:00Z"


def test_the_park_log_keeps_the_last_20(host) -> None:
    """MR 2: the last 20."""
    lines = [f"2026-01-01T00:00:{i:02d}Z park r done seconds={i}.0" for i in range(25)]
    put(host.state / "supervisor.log", "\n".join(lines) + "\n")
    report, _ = report_of(cli("status", "--json"))
    assert report["park_log"] == lines[-20:]


# --------------------------------------------------------------------------- MR 1: the host keys


def ticket(host, name: str, pid: int, **extra: object) -> None:
    """A queue ticket too large to fit."""
    data = {
        "schema": SCHEMA,
        "job": name,
        "rig": "m-rig",
        "pid": pid,
        "tier": 0,
        "token": "secret-token",
        "needs": {"ram_mb": 900000000, "gpu": False},
        "enqueued_at": utc(1),
        **extra,
    }
    put(host.state / "queue" / f"{name}.json", data)


def test_an_empty_host(host) -> None:
    """Every MR 1 key, with nothing to show, and no write."""
    report, rows = report_of(cli("status", "--json"))
    assert rows == {}
    for key in ("holder", "blocked_head", "reserved_for", "idle_point"):
        assert report[key] is None, key
    assert report["queue"] == [] and report["foreign_heavy"] == [] and report["park_log"] == []
    assert report["supervisor"] == {"record": None, "is_active": "unknown", "last_tick": None}
    assert isinstance(report["mem_available_mb"], int)
    assert not host.state.exists()


def test_the_queue_a_reservation_and_the_drain(host, three) -> None:
    """R194: wait_s and the reservation, no token."""
    ticket(host, "big", os.getpid())
    report, _ = report_of(cli("status", "--json"))
    [entry] = report["queue"]
    assert entry["job"] == "big" and 3500 <= entry["wait_s"] <= 3700
    assert "waiting_s" not in entry and "secret-token" not in json.dumps(report)
    reserved = report["reserved_for"]
    assert sorted(reserved) == ["job", "rig", "since", "tier"]
    assert (reserved["job"], reserved["rig"], reserved["tier"]) == ("big", "m-rig", 0)
    assert report["blocked_head"] is None


def test_a_blocked_head_before_the_reservation(host, three) -> None:
    """R194: the blocked head."""
    ticket(host, "big", os.getpid(), enqueued_at=utc(0))
    report, _ = report_of(cli("status", "--json"))
    assert report["blocked_head"] == "blocked head: big needs 900000000 MB"
    assert report["reserved_for"] is None


def test_status_deletes_no_dead_ticket(host, three) -> None:
    """`status` writes nothing."""
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    ticket(host, "dead", dead.pid)
    host.write_config("foreign_heavy_patterns: [nothing-matches-this-zz]\n")
    before = tree(host.state)
    report, _ = report_of(cli("status", "--json"))
    assert report["queue"] == []
    assert tree(host.state) == before


def test_idle_point_supervisor_and_foreign(host, three) -> None:
    """The drain, the record and `is-active`."""
    put(host.state / "idle-point.json", {"schema": SCHEMA, "purpose": "upgrade"})
    put(host.state / "supervisor.json", {"schema": SCHEMA, "version": "9.9.9"})
    host.systemctl.reply("is-active", "active\n")
    report, _ = report_of(cli("status", "--json"))
    assert report["idle_point"]["purpose"] == "upgrade"
    assert report["supervisor"]["record"]["version"] == "9.9.9"
    assert report["supervisor"]["is_active"] == "active"
    assert host.systemctl.calls == [["--user", "is-active", "hacf-host-supervise.timer"]]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"stdout": "inactive\n", "rc": 3}, "inactive"),
        ({"stdout": "", "rc": 1, "stderr": "no bus\n"}, "unknown"),
    ],
)
def test_is_active(host, reply: dict, expected: str) -> None:
    """A stopped timer is named; no bus is unknown."""
    host.systemctl.reply("is-active", **reply)
    report, _ = report_of(cli("status", "--json"))
    assert report["supervisor"]["is_active"] == expected


def test_without_systemctl_it_is_unknown(tool, host, monkeypatch, capsys) -> None:
    """R195: off Linux."""
    monkeypatch.setenv("PATH", str(host.rig.base))
    assert tool.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["supervisor"]["is_active"] == "unknown"


# ------------------------------------------------------------------------------ MR 2: status --md


def populate(host, three: Path, tmp_path: Path) -> None:
    """One rig of each kind, a cycle in the log and a dry-run supervisor."""
    listing = tmp_path / "paused.txt"
    listing.write_text("p-rig\n")
    host.write_config(
        f"priority: [m-rig]\nprojects: {{m-rig: {three}, p-rig: {three}}}\n"
        f"ignore_rigs: [i-rig]\npaused_files: [{listing}]\n"
    )
    record(
        host, "m-rig", "parked-waiting", since="2026-01-01T00:01:00Z",
        reason="waits | on\n1 row", waits_on=["q-1"], parks=[utc(1)],
    )  # fmt: skip
    put(host.state / "supervisor.log", "\n".join(log_lines()[:4]) + "\n")
    put(
        host.state / "supervisor.json",
        {"schema": SCHEMA, "version": "0.6.7", "dry_run_mode": True},
    )


def test_status_md_writes_both_files(host, three, tmp_path) -> None:
    """MR 2: the files, the sections and the printed path."""
    populate(host, three, tmp_path)
    done = cli("status", "--md")
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{host.state / 'status.md'}\n"
    text = (host.state / "status.md").read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## Rigs", "## Heavy job", "## Idle point", "## Supervisor", "## Park log"]
    assert "schema: hacf.host/v1" in text and "\x1b" not in text
    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| ")}
    assert "parked-waiting" in rows["m-rig"] and "41.5" in rows["m-rig"]
    assert "waits \\| on 1 row" in rows["m-rig"]
    assert "paused-by-person" in rows["p-rig"] and "paused.txt" in rows["p-rig"]
    assert "unmapped" in rows["u-rig"] and "ignored" in rows["i-rig"]
    for words in ("holder: none", "queue: empty", "version: 0.6.7", "mode: dry-run"):
        assert words in text, words
    assert "restore m-rig done seconds=41.5 trigger=q-1" in text
    stored = json.loads((host.state / "status.json").read_text(encoding="utf-8"))
    assert stored["schema"] == "hacf.host/v1"
    assert stored["report"] == "hacf.host-status/v1"
    assert [r["name"] for r in stored["rigs"]] == ["i-rig", "m-rig", "p-rig", "u-rig"]
    assert not list(host.state.glob(".*.tmp"))
    assert host.rig.calls == [["ps", "--json"]]


def test_status_md_twice_keeps_unknown_keys(host, three) -> None:
    """A same-major writer keeps what it does not know."""
    put(host.state / "status.json", {"schema": SCHEMA, "added_later": 7})
    for _ in range(2):
        done = cli("status", "--md")
        assert done.returncode == 0, done.stderr
    assert json.loads((host.state / "status.json").read_text())["added_later"] == 7


def test_status_md_writes_nothing_over_newer_state(host, three) -> None:
    """US-014: exit 75."""
    put(host.state / "rigs" / "m-rig.json", {"schema": "hacf.host/v2"})
    before = tree(host.state)
    done = cli("status", "--md")
    assert done.returncode == 75, done.stderr
    assert tree(host.state) == before


def test_status_md_leaves_a_foreign_status_json(host, three) -> None:
    """An unreadable one is not replaced."""
    put(host.state / "status.json", "{broken")
    done = cli("status", "--md")
    assert done.returncode == 2, done.stderr
    assert (host.state / "status.json").read_text() == "{broken"
    assert not (host.state / "status.md").exists()


@pytest.mark.parametrize(
    ("seed", "code"),
    [
        (None, 0),
        (("rigs/m-rig.json", {"schema": "hacf.host/v2"}), 75),
        (("status.json", "{broken"), 2),
    ],
)
def test_bare_status_is_status_md(
    tool, host, three, tmp_path, monkeypatch, capsys, seed, code: int
) -> None:
    """P86: the same exit, output and file bytes, with the time and the memory pinned."""
    populate(host, three, tmp_path)
    if seed:
        put(host.state / seed[0], seed[1])
    monkeypatch.setattr(tool, "_utc", lambda: "2026-01-02T00:00:00Z")
    monkeypatch.setattr(tool, "mem_available_mb", lambda: 4096)
    start = tree(host.state)
    runs = []
    for argv in (["status", "--md"], ["status"]):
        exit_code = tool.main(argv)
        runs.append((exit_code, capsys.readouterr(), tree(host.state)))
        for name in ("status.md", "status.json"):
            if name not in start:
                (host.state / name).unlink(missing_ok=True)
    md, bare = runs
    assert bare == md
    assert md[0] == code
    if code == 0:
        assert md[1].out == f"{host.state / 'status.md'}\n"
        assert {"status.md", "status.json"} <= set(md[2])
    else:
        assert "status.md" not in md[2]
        assert {name: md[2][name] for name in start} == start


def test_status_json_writes_nothing(host, three, tmp_path) -> None:
    """The state dir is byte for byte as before."""
    populate(host, three, tmp_path)
    before = tree(host.state)
    assert cli("status", "--json").returncode == 0
    assert tree(host.state) == before


# ------------------------------------------------------------------------------ MR 4: zero-token


@pytest.mark.parametrize("form", ["--json", "--md"])
def test_status_opens_no_connection(tool, host, three, monkeypatch, form: str) -> None:
    """MR 4: no socket connect, and only local reads are run."""
    tried: list[object] = []

    def refuse(self, address, *args):
        tried.append(address)
        raise AssertionError(f"status connected to {address}")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse)
    commands: list[str] = []
    real = subprocess.Popen

    class Watched(real):
        def __init__(self, argv, *args, **kwargs):
            commands.append(Path(argv[0]).name)
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Watched)
    with contextlib.redirect_stdout(None):
        assert tool.main(["status", form]) == 0
    assert tried == []
    assert set(commands) <= {"rig", "systemctl", "ps"}
    assert [call[0] for call in host.rig.calls] == ["ps"]
