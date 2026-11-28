"""Tests of pause/resume, park/restore and their one engine (OPR.99.0.16.5).

Every test runs on the isolated host of conftest.py: the state dir, config, lock and fake `rig` are
under tmp_path, and the host's `rig` is never called. A fake clock stands in for every wait, and
the bound project holds a fake seat-config.py that prints the routing a test chooses.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from conftest import REPO_ROOT

RIG = "demo-hacf"
LEAD = f"orch-lead@{RIG}"
MANUAL = "snap-manual-1"
DOWN = "snap-down-2"
ADVISOR = "advisor-lead@kernel"
PERSON = "someone@external"

NODES = f"ps --nodes --rig {RIG} --full --json"
KERNEL = "ps --nodes --rig kernel --full --json"
IN_PROGRESS = "queue list -A --state in-progress --json --limit 10000"
ACTIVE = "queue list -A --state pending,in-progress,blocked --json --limit 10000"
SNAPSHOT = f"snapshot {RIG}"
SNAPSHOTS = f"snapshot list {RIG}"
DOWN_CMD = f"down {RIG} --json"
UP_CMD = f"up {RIG} --existing --json --timeout 900"
RESTORE_CMD = f"restore {MANUAL} --rig {RIG}"
STATUS_CMD = f"restore status 41 --rig {RIG} --json"
CREATE = "queue create *"
SEND = "send *"

SEAT_CONFIG = """\
import json, sys
open({log!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
reply = json.load(open({reply!r}))
sys.stdout.write(reply.get("stdout", ""))
sys.exit(reply.get("rc", 0))
"""


def nodes(status: str = "running", *, lead: str = "running", activity: str = "idle") -> list:
    """Two seats of the rig as `rig ps --nodes` shows them."""
    found = []
    for logical, name, session in (("orch.lead", LEAD, lead), ("build.execute", "x", status)):
        found.append(
            {
                "rigName": RIG,
                "logicalId": logical,
                "canonicalSessionName": LEAD if logical == "orch.lead" else f"build-execute@{RIG}",
                "sessionStatus": session,
                "lifecycleState": "running" if session == "running" else "detached",
                "agentActivity": {"state": activity},
                "needsAttention": False,
                "arbitratedNeedsInput": False,
                # top level, as a live 0.7.23 `rig ps --nodes --full --json` has it
                "lastActivityAt": "2026-11-01T00:00:00.000Z" if session == "running" else None,
            }
        )
    return found


def stopped_nodes() -> list:
    """The two seats after `rig down`."""
    return nodes("exited", lead="exited")


def as_reply(value: object, rc: int = 0, **extra: object) -> dict:
    """One canned reply for a `then` mapping."""
    text = value if isinstance(value, str) else json.dumps(value)
    return {"stdout": text, "rc": rc, **extra}


def rigs_reply(status: str) -> dict:
    """`rig ps --json` with the rig in `status`."""
    return as_reply([{"name": RIG, "rigName": RIG, "status": status}])


def now_stopped() -> dict:
    """What the reads show once the rig is down."""
    return {NODES: as_reply(stopped_nodes()), "ps --json": rigs_reply("stopped")}


def now_running() -> dict:
    """What the reads show once the rig is back."""
    return {NODES: as_reply(nodes()), "ps --json": rigs_reply("running")}


def snapshot_table(snapshot: str = MANUAL, status: str = "complete") -> str:
    """`rig snapshot list` as OpenRig 0.7.23 prints it."""
    return (
        "ID             Kind           Status    Created\n"
        f"snap-old       auto-pre-down  complete  2026-11-01T00:00:00Z\n"
        f"{snapshot}  manual         {status}  2026-11-02T00:00:00Z\n"
    )


def up_result(result: str = "fully_restored", selected: str = DOWN) -> dict:
    """`rig up --existing --json`'s result body, with placeholder values.

    The keys and nesting are those of the OpenRig 0.7.23 result recorded verbatim in the
    OPR.99.0.16.11 live-trial report's Appendix.
    """
    return {
        "status": "restored",
        "rigId": "rig-id",
        "rigName": RIG,
        "snapshotId": selected,
        "snapshotKind": "auto-pre-down",
        "rigResult": result,
        "nodes": [{"nodeId": "node-id", "logicalId": "orch.lead", "status": "resumed"}],
        "excludedNodes": [],
        "warnings": ["a warning"],
        "attachCommand": f"tmux attach -t {LEAD}",
    }


def without_snapshot_id(**extra: object) -> dict:
    """A fully restored body with no top-level `snapshotId`, plus `extra`."""
    body = {k: v for k, v in up_result().items() if k != "snapshotId"}
    return {**body, **extra}


class Park:
    """The isolated host with one mapped rig, idle and at a boundary, and a fake clock."""

    def __init__(self, host, tool, monkeypatch: pytest.MonkeyPatch) -> None:
        """Script a rig whose park and restore both succeed."""
        self.host, self.tool, self.rig = host, tool, host.rig
        self.monkeypatch = monkeypatch
        self.root = host.project("demo")
        self.waits: list[float] = []
        self.now = 1000.0
        self.meminfo = host.base / "meminfo"
        self.memory(32768)
        monkeypatch.setenv("HACF_HOST_MEMINFO", str(self.meminfo))
        monkeypatch.setenv("USER", "a-person")
        for name in ("HACF_HOST_MIN_MB", "HACF_HEAVY_LOCK_HELD"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(tool, "pause_for", self.sleep, raising=False)
        monkeypatch.setattr(tool, "clock", lambda: self.now, raising=False)
        self.config()
        self.routing_log = host.base / "seat-config.log"
        self.routing_file = host.base / "routing.json"
        script = self.root / "openrig" / "hacf" / "bin" / "seat-config.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            SEAT_CONFIG.format(log=str(self.routing_log), reply=str(self.routing_file))
        )
        self.routing({"session": ADVISOR})
        self.running()
        self.rig.reply(IN_PROGRESS, [])
        self.rig.reply(ACTIVE, [])
        self.rig.reply(SNAPSHOT, f"Snapshot created: {MANUAL}\nTo restore: rig restore ...\n")
        self.rig.reply(SNAPSHOTS, snapshot_table())
        self.rig.reply(
            DOWN_CMD, {"snapshotId": DOWN, "errors": [], "sessionsKilled": 2}, then=now_stopped()
        )
        self.rig.reply(UP_CMD, up_result(), then=now_running())
        self.rig.reply(RESTORE_CMD, "Restore attempt id: 41\nStatus: started\n", then=now_running())
        self.rig.reply(STATUS_CMD, {"ok": True, "originalResult": {"rigResult": "fully_restored"}})
        self.rig.reply(CREATE, {"qitemId": "qitem-1"}, queue_rules=True)
        self.rig.reply(SEND, "Sent\n")

    def config(self, extra: str = "") -> None:
        """The host config: the rig bound to its project."""
        self.host.write_config(f"projects: {{{RIG}: {self.root}}}\n{extra}")

    def routing(self, destination: object, rc: int = 0, tags: dict | None = None) -> None:
        """What the project's seat-config.py prints for the routing section."""
        value = {"question.destination": {"value": destination, "source": "default"}}
        if tags is not None:
            value["tags"] = {"value": tags, "source": "override"}
        self.routing_file.write_text(
            json.dumps({"stdout": json.dumps({"routing": value}), "rc": rc})
        )

    def running(self) -> None:
        """The rig is up and every seat idle."""
        self.rig.reply("ps --json", [{"name": RIG, "rigName": RIG, "status": "running"}])
        self.rig.reply(NODES, nodes())

    def stopped(self) -> None:
        """The rig is down."""
        self.rig.reply("ps --json", [{"name": RIG, "rigName": RIG, "status": "stopped"}])
        self.rig.reply(NODES, stopped_nodes())

    def sleep(self, seconds: float) -> None:
        """The fake wait: the clock moves, and `on_sleep` may change the host."""
        self.waits.append(seconds)
        self.now += seconds
        self.on_sleep()

    def on_sleep(self) -> None:
        """Replaced by a test that frees memory or changes a reply during a wait."""

    def memory(self, mb: int) -> None:
        """Set the fake MemAvailable."""
        self.meminfo.write_text(f"MemAvailable: {mb * 1024} kB\n")

    def record(self) -> dict:
        """`rigs/<rig>.json`, or {}."""
        path = self.host.state / "rigs" / f"{RIG}.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def put_record(self, state: str, down: str | None = DOWN, **extra: object) -> None:
        """Write the rig's state record."""
        path = self.host.state / "rigs" / f"{RIG}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": "hacf.host/v1", "state": state, **extra}
        if down != "none":
            data["snapshot"] = {"manual": MANUAL, "down": down}
        path.write_text(json.dumps(data))

    def parked(self, state: str = "parked-waiting", down: str | None = DOWN) -> None:
        """A stopped rig with a record, as a park leaves it."""
        self.stopped()
        self.put_record(state, down)

    def calls(self, *verbs: str) -> list[str]:
        """The fake rig's calls as lines, only those starting with one of `verbs` when given."""
        lines = [" ".join(c) for c in self.rig.calls]
        return [x for x in lines if not verbs or x.startswith(verbs)]

    def creates(self) -> list[dict]:
        """Each `queue create` call as {flag: value}."""
        found = []
        for argv in self.rig.calls:
            if argv[:2] == ["queue", "create"]:
                found.append(dict(zip(argv[2::2], argv[3::2], strict=True)))
        return found

    def log(self) -> str:
        """supervisor.log, or ""."""
        path = self.host.state / "supervisor.log"
        return path.read_text() if path.exists() else ""


@pytest.fixture
def park(host, tool, monkeypatch: pytest.MonkeyPatch) -> Park:
    """One mapped rig on the isolated host."""
    return Park(host, tool, monkeypatch)


def run(park: Park, capsys, *argv: str) -> tuple[int, str, str]:
    """Run one verb in-process: its exit code, stdout and stderr."""
    code = park.tool.main(list(argv))
    done = capsys.readouterr()
    return code, done.out, done.err


def mutations(park: Park) -> list[str]:
    """The calls that change the host."""
    return park.calls("snapshot ", "down ", "up ", "restore s", "queue create", "send ")


# ------------------------------------------------------------------------------- park


def test_park_happy_path_in_order(park, capsys) -> None:
    """MR 1: snapshot, list, re-check, down, per-node verify, then the record."""
    code, out, err = run(park, capsys, "park", RIG)
    assert code == 0, out + err
    calls = park.calls()
    print("\n".join(calls))
    at = calls.index(SNAPSHOT)
    boundary = [NODES, IN_PROGRESS]
    assert calls[at:] == [SNAPSHOT, SNAPSHOTS, *boundary, DOWN_CMD, NODES, "ps --json"]
    record = park.record()
    assert record["state"] == "parked-waiting" and record["by"] == "person"
    assert record["snapshot"] == {"manual": MANUAL, "down": DOWN}
    resume_point = Path(record["resume_point"])
    assert resume_point.read_text().startswith(f"# Resume point: {RIG}\n")
    assert park.creates() == []


@pytest.mark.parametrize(
    "replies",
    [
        {SNAPSHOT: {"stdout": "Snapshot failed\n", "rc": 1}},
        {SNAPSHOT: {"stdout": "nothing useful\n"}},
        {SNAPSHOTS: {"stdout": snapshot_table(status="failed")}},
        {SNAPSHOTS: {"stdout": snapshot_table(status="unusable")}},
        {SNAPSHOTS: {"stdout": snapshot_table("snap-other")}},
        {SNAPSHOTS: {"stdout": snapshot_table(), "rc": 1}},
        {SNAPSHOTS: {"stdout": "No snapshots\n"}},
    ],
)
def test_snapshot_not_verified_leaves_the_rig_running(park, capsys, replies: dict) -> None:
    """MR 1 step 2: no verified manual snapshot, no `rig down`."""
    for argv, reply in replies.items():
        park.rig.reply(argv, reply["stdout"], rc=reply.get("rc", 0))
    code, out, _ = run(park, capsys, "park", RIG)
    assert code == 1
    assert "snapshot not verified" in out
    assert park.calls("down ") == [] and park.creates() == []
    assert park.record() == {}


def test_the_recheck_aborts_when_a_row_turns_in_progress(park, capsys) -> None:
    """MR 1 step 3 (R191): the boundary is read again just before `rig down`."""
    row = {"qitemId": "qitem-late", "destinationSession": f"build-execute@{RIG}"}
    park.rig.reply(SNAPSHOTS, snapshot_table(), then={IN_PROGRESS: as_reply([row])})
    code, out, _ = run(park, capsys, "park", RIG)
    assert code == 1
    assert "qitem-late" in out
    assert park.calls(SNAPSHOT) and park.calls("down ") == []
    assert park.record() == {}


@pytest.mark.parametrize(
    ("p3", "downs"),
    [(None, 0), (lambda: "waits_on changed", 0), (lambda: "", 1)],
)
def test_a_supervisor_park_needs_the_p3_check(park, p3, downs: int) -> None:
    """MR 1 step 3: the supervisor's park also re-checks P3, and none given aborts."""
    result = park.tool.park_rig(RIG, "supervisor", "waits", p3=p3)
    assert result.ok is bool(downs)
    assert len(park.calls("down ")) == downs
    assert park.record().get("state") == ("parked-waiting" if downs else None)


def test_a_paused_line_added_during_the_park_aborts(park, capsys) -> None:
    """R183: a rig listed in a paused file by the re-check is not stopped."""
    listed = park.host.base / "paused.txt"
    listed.write_text("")
    park.config(f"paused_files: [{listed}]\n")
    real = park.tool.boundary
    seen = []

    def boundary(*args):
        seen.append(1)
        if len(seen) == 3:  # the verb's check, the resume point's, then the re-check
            listed.write_text(f"{RIG}\n")
        return real(*args)

    park.monkeypatch.setattr(park.tool, "boundary", boundary)
    code, out, _ = run(park, capsys, "park", RIG)
    assert code == 1 and str(listed) in out
    assert park.calls("down ") == []


def test_a_snapshot_error_in_down_records_no_down_id(park, capsys) -> None:
    """R188: `errors` non-empty (exit 2) means no down id; the stop is still verified."""
    body = {"snapshotId": DOWN, "errors": ["Snapshot failed: disk"]}
    park.rig.reply(DOWN_CMD, body, rc=2, then=now_stopped())
    code, out, err = run(park, capsys, "park", RIG)
    assert code == 0, out + err
    assert park.record()["snapshot"] == {"manual": MANUAL, "down": None}
    code, out, err = run(park, capsys, "restore", RIG)
    assert code == 0, out + err
    assert park.calls("up ") == []
    assert park.calls("restore ") == [RESTORE_CMD, STATUS_CMD]
    assert park.record()["state"] == "running"


@pytest.mark.parametrize(
    "after",
    [
        {},  # refused: every seat still runs
        {NODES: as_reply(nodes("running", lead="exited"))},  # partly down
        {NODES: as_reply(stopped_nodes())},  # the rig row never says stopped
        {NODES: as_reply("not json"), "ps --json": rigs_reply("stopped")},
    ],
)
def test_an_unverified_stop_is_restore_failed_and_escalates(park, capsys, after: dict) -> None:
    """MR 1 steps 4-5: a stop that cannot be verified is marked and sent to the person once."""
    park.rig.reply(DOWN_CMD, {"errors": ["kill failed"]}, rc=2, then=after)
    code, _, _ = run(park, capsys, "park", RIG)
    assert code == 1
    record = park.record()
    assert record["state"] == "restore-failed"
    assert record["snapshot"] == {"manual": MANUAL, "down": None}
    assert len(park.creates()) == 1 and len(park.calls("down ")) == 1


def test_an_interrupted_down_is_marked_restore_failed(park) -> None:
    """A park stopped after `rig down` was sent never leaves the rig looking parked or running."""
    real = park.tool.rig

    def rig(*argv, **kwargs):
        if argv[0] == "down":
            raise KeyboardInterrupt
        return real(*argv, **kwargs)

    park.monkeypatch.setattr(park.tool, "rig", rig)
    with pytest.raises(KeyboardInterrupt):
        park.tool.park_rig(RIG, "person", "manual park")
    assert park.record()["state"] == "restore-failed"


# ------------------------------------------------------------------------------- restore


def test_restore_fully_restored_with_the_matching_selection(park, capsys) -> None:
    """(a) The recorded shape with the down id is verified: the lead message, then `running`."""
    park.parked()
    code, out, err = run(park, capsys, "restore", RIG)
    assert code == 0, out + err
    assert park.calls("up ", "restore ") == [UP_CMD]
    assert len(park.calls(f"send {LEAD} ")) == 1
    assert f"snapshot {DOWN} kind auto-pre-down (expected {DOWN})" in out
    record = park.record()
    assert record["state"] == "running" and "snapshot" not in record
    assert park.creates() == [] and park.waits == []


def test_the_lead_message_argv(park, capsys) -> None:
    """R206: one `rig send` to the orch.lead seat, with the resume point."""
    park.stopped()
    point = str(park.host.state / "rigs" / f"{RIG}.resume.md")
    park.put_record("parked-waiting", resume_point=point)
    assert run(park, capsys, "restore", RIG)[0] == 0
    (argv,) = [c for c in park.rig.calls if c[0] == "send"]
    assert argv[:2] == ["send", LEAD] and len(argv) == 3
    pattern = (
        r"restored from park at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ; resume point: "
        + re.escape(point)
        + r"; re-read your queue"
    )
    assert re.fullmatch(pattern, argv[2]), argv[2]


def test_a_failed_lead_message_still_leaves_the_rig_running(park, capsys) -> None:
    """The restore is verified; a message that cannot be sent is reported, not retried."""
    park.parked()
    park.rig.reply(SEND, "", rc=1, stderr="busy")
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 0 and "lead message: failed" in out
    assert park.record()["state"] == "running" and len(park.calls("send ")) == 1


@pytest.mark.parametrize(
    ("body", "rc"),
    [
        (up_result("partially_restored"), 1),
        (up_result("partially_restored"), 0),
        (up_result("failed"), 1),
        (up_result(selected="snap-other"), 0),  # (b) another snapshotId
        (without_snapshot_id(), 0),
        # (c) only the shape no OpenRig sends on this path
        (without_snapshot_id(snapshotSelection={"snapshotId": DOWN}), 0),
        (up_result(selected=None), 0),
        (up_result(selected=[DOWN]), 0),  # not a string
        (up_result(), 1),  # fully_restored with a non-zero exit
        ("not json", 0),
        ("The CLI timed out waiting for the daemon", 1),
    ],
)
def test_anything_but_a_verified_restore_is_restore_failed(park, capsys, body, rc: int) -> None:
    """R188, (b)-(d): one `rig up`, one escalation, no retry."""
    park.parked()
    park.rig.reply(UP_CMD, body, rc=rc, then=now_running())
    code, _, _ = run(park, capsys, "restore", RIG)
    assert code == 1
    assert park.record()["state"] == "restore-failed"
    assert park.record()["snapshot"] == {"manual": MANUAL, "down": DOWN}
    assert park.calls("up ") == [UP_CMD]
    assert len(park.creates()) == 1
    assert park.calls("send ") == [] and park.waits == []


def test_a_partial_restore_shows_one_up_and_one_escalation_row(park, capsys) -> None:
    """MR 4: the row's destination, summary, tags, options and the result."""
    park.parked()
    park.routing({"session": ADVISOR}, tags={"question": "ask:me", "extra": ["x:y"]})
    point = park.host.state / "rigs" / f"{RIG}.resume.md"
    point.write_text("# Resume point\n")
    park.put_record("parked-waiting", resume_point=str(point))
    park.rig.reply(UP_CMD, up_result("partially_restored"), rc=1, then=now_running())
    assert run(park, capsys, "restore", RIG)[0] == 1
    print("\n".join(park.calls("up ", "queue create")))
    assert len(park.calls("up ")) == 1
    (row,) = park.creates()
    assert row["--destination"] == ADVISOR
    summary = f"HACF host: restore of {RIG} needs the person (resume point {point})"
    assert row["--summary"] == summary
    assert row["--tags"] == f"ask:me,rig:{RIG},x:y"
    assert row["--evidence-ref"] == str(point)
    assert row["--source"] == "hacf-host@host"
    assert "--human-intent" not in row  # P74: OpenRig refuses it for an agent seat
    body = row["--body"]
    assert f"A (recommended): restore it again (restore {RIG})" in body
    assert "B: leave it stopped" in body
    assert str(point) in body and '"partially_restored"' in body
    assert json.loads(park.routing_log.read_text().splitlines()[-1]) == [
        "resolve", "--dir", str(park.root / "openrig" / "hacf"), "--section", "routing", "--json",
    ]  # fmt: skip
    assert f" escalate {RIG} sent to {ADVISOR}" in park.log()


@pytest.mark.parametrize(
    ("destination", "intent"),
    [
        (PERSON, "decision"),
        ("human@host", "decision"),
        ("human-ops@kernel", "decision"),
        ("chat:someone@external", "decision"),  # OpenRig's rule allows ":"; SESSION_RE does not
        (ADVISOR, None),
        ("humane@host", None),
    ],
)
def test_the_escalations_two_routes_are_both_accepted(park, capsys, destination, intent) -> None:
    """P74: a human destination gets --human-intent decision, an agent none; neither is refused."""
    park.parked()
    park.routing({"session": destination})
    park.rig.reply(UP_CMD, up_result("failed"), rc=1)
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 1 and f"escalation: sent to {destination}" in out
    (row,) = park.creates()
    assert row["--destination"] == destination
    assert row.get("--human-intent") == intent
    assert row["--tags"] == f"hacf:question,rig:{RIG}"
    point = str(park.host.state / "rigs" / f"{RIG}.resume.md")
    assert row["--evidence-ref"] == point and Path(point).is_file()
    assert row["--summary"].endswith(f"needs the person (resume point {point})")
    assert f"A (recommended): restore it again (restore {RIG})" in row["--body"]
    assert "B: leave it stopped" in row["--body"]


@pytest.mark.parametrize("intent", [["question"], ["update"]])
def test_the_fake_queue_refuses_what_openrig_refuses(park, intent: list) -> None:
    """The fake applies both refusals, so an accepted row above means something."""
    to = PERSON if intent == ["question"] else ADVISOR
    argv = ("queue", "create", "--destination", to, "--human-intent", *intent)
    assert park.tool.rig(*argv, timeout=30)[0] == 1


def test_a_seat_that_escalates_is_the_rows_source(park) -> None:
    """From a seat (the kernel's operator), OpenRig takes the seat as the source."""
    park.monkeypatch.setenv("OPENRIG_SESSION_NAME", "operator-agent@kernel")
    park.parked()
    park.rig.reply(UP_CMD, up_result("failed"), rc=1)
    park.tool.restore_rig(RIG)
    (row,) = park.creates()
    assert "--source" not in row


ADVISOR_NODE = {
    "logicalId": "advisor.lead",
    "canonicalSessionName": ADVISOR,
    "sessionStatus": "running",
}
PEOPLE = "gateway human list --json"


@pytest.mark.parametrize(
    ("kernel", "people", "destination"),
    [
        ([ADVISOR_NODE], [], ADVISOR),
        ([{**ADVISOR_NODE, "sessionStatus": "exited"}], [PERSON], PERSON),
        ([], [PERSON], PERSON),
    ],
)
def test_discover_takes_the_advisor_else_the_person(
    park, capsys, kernel: list, people: list, destination: str
) -> None:
    """R199: protocol.md's discovery steps."""
    park.parked()
    park.routing({"rule": "discover"})
    park.rig.reply(KERNEL, kernel)
    park.rig.reply(PEOPLE, {"ok": True, "humans": [{"address": a} for a in people]})
    park.rig.reply(UP_CMD, up_result("failed"), rc=1)
    assert run(park, capsys, "restore", RIG)[0] == 1
    (row,) = park.creates()
    assert row["--destination"] == destination


def no_routing(park) -> None:
    """seat-config.py fails."""
    park.routing({"session": ADVISOR}, rc=1)


def odd_routing(park) -> None:
    """An unknown rule."""
    park.routing({"rule": "something"})


def no_kit(park) -> None:
    """The project holds no seat-config.py."""
    (park.root / "openrig" / "hacf" / "bin" / "seat-config.py").unlink()


def no_kernel(park) -> None:
    """The kernel cannot be read."""
    park.routing({"rule": "discover"})
    park.rig.reply(KERNEL, "", rc=1)


def nobody(park) -> None:
    """No advisor and no person."""
    park.routing({"rule": "discover"})
    park.rig.reply(KERNEL, [])
    park.rig.reply(PEOPLE, {"ok": True, "humans": []})


def two_people(park) -> None:
    """Two people: neither is chosen."""
    park.routing({"rule": "discover"})
    park.rig.reply(KERNEL, [])
    park.rig.reply(PEOPLE, {"ok": True, "humans": [{"address": PERSON}, {"address": "b@external"}]})


def refused_row(park) -> None:
    """OpenRig refuses the row."""
    park.rig.reply(CREATE, "", rc=1, stderr="invalid_human_notification")


@pytest.mark.parametrize(
    ("break_it", "creates"),
    [
        (no_routing, 0),
        (odd_routing, 0),
        (no_kit, 0),
        (no_kernel, 0),
        (nobody, 0),
        (two_people, 0),
        (refused_row, 1),
    ],
)
def test_an_unsent_escalation_is_logged(park, capsys, break_it, creates: int) -> None:
    """R199: never guessed; supervisor.log says why, and the rig stays restore-failed."""
    park.parked()
    break_it(park)
    park.rig.reply(UP_CMD, up_result("failed"), rc=1)
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 1 and "escalation: not sent" in out
    assert len(park.creates()) == creates
    line = [x for x in park.log().splitlines() if " escalate " in x]
    assert len(line) == 1 and f" {RIG} not sent: " in line[0]
    assert park.record()["state"] == "restore-failed"


def test_the_kits_own_seat_config_answers_the_routing_call(park) -> None:
    """The shipped seat-config.py accepts the argv and answers with a destination."""
    destination, tags = park.tool.question_routing(RIG, str(REPO_ROOT))
    assert destination == {"rule": "discover"}
    assert tags["question"] == "hacf:question"


def test_a_cli_timeout_is_restore_failed_with_no_retry(park, capsys) -> None:
    """The adapter's own timeout: the operation may still run."""
    park.config("autopark: {restore_timeout_s: 1}\n")
    park.monkeypatch.setattr(park.tool, "RESTORE_MARGIN_S", 0)
    park.parked()
    argv = f"up {RIG} --existing --json --timeout 1"
    park.rig.reply(argv, up_result(), sleep=30)
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 1 and "timed out" in out
    assert park.calls("up ") == [argv]
    assert park.record()["state"] == "restore-failed" and len(park.creates()) == 1


def test_an_interrupted_restore_is_marked_restore_failed(park) -> None:
    """Nothing retries a restore that may still be running."""
    park.parked()
    real = park.tool.rig

    def rig(*argv, **kwargs):
        if argv[0] == "up":
            raise KeyboardInterrupt
        return real(*argv, **kwargs)

    park.monkeypatch.setattr(park.tool, "rig", rig)
    with pytest.raises(KeyboardInterrupt):
        park.tool.restore_rig(RIG)
    assert park.record()["state"] == "restore-failed"


REFUSED_409 = {"error": "Already in progress for this source", "code": "conflict"}
NOT_ATTEMPTED = {
    "status": "not_attempted",
    "code": "pre_restore_validation_failed",
    "rigResult": "not_attempted",
}


@pytest.mark.parametrize(
    ("body", "rc"), [(REFUSED_409, 1), (NOT_ATTEMPTED, 1), ({"error": "x", "code": "y"}, 2)]
)
def test_a_pre_launch_refusal_is_retried_then_succeeds(park, capsys, body: dict, rc: int) -> None:
    """R188: nothing launched, so it is retried after 2 minutes."""
    park.parked()
    success = {UP_CMD: as_reply(up_result(), then=now_running())}
    park.rig.reply(UP_CMD, body, rc=rc, then=success)
    code, out, err = run(park, capsys, "restore", RIG)
    assert code == 0, out + err
    assert park.waits == [120]
    assert park.calls("up ") == [UP_CMD, UP_CMD]
    assert park.record()["state"] == "running" and park.creates() == []


def test_a_lasting_refusal_ends_after_three_retries(park, capsys) -> None:
    """Retries after 2, 4 and 8 minutes, then restore-failed and one escalation."""
    park.parked()
    park.rig.reply(UP_CMD, REFUSED_409, rc=1)
    assert run(park, capsys, "restore", RIG)[0] == 1
    assert park.waits == [120, 240, 480]
    assert len(park.calls("up ")) == 4 and len(park.creates()) == 1
    assert park.record()["state"] == "restore-failed"


@pytest.mark.parametrize(
    "seen", [as_reply(nodes("running", lead="exited")), as_reply("", rc=1), as_reply([])]
)
def test_a_refusal_with_a_seat_running_or_unknown_is_not_retried(park, capsys, seen) -> None:
    """Only `rig ps` showing no node running allows a retry."""
    park.parked()
    park.rig.reply(UP_CMD, REFUSED_409, rc=2, then={NODES: seen})
    assert run(park, capsys, "restore", RIG)[0] == 1
    assert park.waits == [] and len(park.calls("up ")) == 1
    assert park.record()["state"] == "restore-failed"


def test_a_seat_starting_during_the_retry_wait_stops_the_retry(park, capsys) -> None:
    """The check is made again after the wait, just before the next `rig up`."""
    park.parked()
    park.rig.reply(UP_CMD, REFUSED_409, rc=1)
    park.on_sleep = lambda: park.rig.reply(NODES, nodes("running", lead="exited"))
    assert run(park, capsys, "restore", RIG)[0] == 1
    assert park.waits == [120] and len(park.calls("up ")) == 1


@pytest.mark.parametrize(("final", "ok"), [("fully_restored", True), ("partially_restored", False)])
def test_restore_by_the_manual_snapshot_polls_the_attempt(park, capsys, final, ok) -> None:
    """MR 1: with no down id, `rig restore` and a poll every 10 s until it completes."""
    park.parked(down=None)
    done = {"ok": True, "originalResult": {"rigResult": final}}
    park.rig.reply(STATUS_CMD, "attempt incomplete", rc=1, then={STATUS_CMD: as_reply(done)})
    code, _, _ = run(park, capsys, "restore", RIG)
    assert (code == 0) is ok
    assert park.calls("restore ") == [RESTORE_CMD, STATUS_CMD, STATUS_CMD]
    assert park.waits == [10]
    assert park.record()["state"] == ("running" if ok else "restore-failed")
    assert len(park.creates()) == (0 if ok else 1)


@pytest.mark.parametrize(
    ("argv", "reply", "waits"),
    [
        (STATUS_CMD, as_reply("attempt incomplete", rc=1), 3),  # never completes: the timeout
        (STATUS_CMD, as_reply("not json"), 0),
        (STATUS_CMD, as_reply({"ok": True}), 0),
        (RESTORE_CMD, as_reply("Restore complete:\n"), 0),
        (RESTORE_CMD, as_reply("Restore conflict: rig may still be running", rc=1), 0),
    ],
)
def test_a_manual_restore_that_cannot_be_verified_fails(park, capsys, argv, reply, waits) -> None:
    """Unparsable output, a refusal or the poll timeout: restore-failed, one `rig restore`."""
    park.config("autopark: {restore_timeout_s: 30}\n")
    park.parked(down=None)
    park.rig.reply(argv, reply["stdout"], rc=reply["rc"])
    assert run(park, capsys, "restore", RIG)[0] == 1
    assert park.waits == [10] * waits
    assert park.calls(RESTORE_CMD) == [RESTORE_CMD]
    assert park.record()["state"] == "restore-failed" and len(park.creates()) == 1


def test_a_restore_with_no_recorded_snapshot_launches_nothing(park, capsys) -> None:
    """R188: there is no expected snapshot to verify."""
    park.stopped()
    park.put_record("restore-failed", down="none")
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 1 and "no recorded snapshot" in out
    assert mutations(park) == []
    assert park.record()["state"] == "restore-failed"


# ------------------------------------------------------------------------------- the RAM wait


def test_restore_waits_for_ram_then_runs(park, capsys) -> None:
    """R204: seats × seat_ram_mb + reserve_mb (2 × 330 + 2048) before `rig up`."""
    park.parked()
    park.memory(2707)
    seen = []

    def free() -> None:
        seen.append(park.calls("up "))
        park.memory(2708)

    park.on_sleep = free
    code, out, err = run(park, capsys, "restore", RIG)
    assert code == 0, out + err
    assert park.waits == [10] and seen == [[]]
    assert "2707 MB < 2708 MB" in out


@pytest.mark.parametrize("why", ["memory", "nodes"])
def test_a_ram_wait_that_runs_out_launches_nothing(park, capsys, why: str) -> None:
    """R203: it waits, parks no other rig, and the record is left as it was (exit 75)."""
    park.config("autopark: {restore_timeout_s: 30}\n")
    park.parked()
    if why == "memory":
        park.memory(100)
    else:
        park.rig.reply(NODES, "", rc=1)
    before = park.record()
    code, out, _ = run(park, capsys, "restore", RIG)
    assert code == 75, out
    assert park.waits == [10, 10, 10] and mutations(park) == []
    assert park.record() == before


# ------------------------------------------------------------------------------- pause, resume


def test_pause_marks_the_rig_and_stops_nothing(park, capsys) -> None:
    """MR 2: the resume point, then paused-by-person with who and why."""
    code, out, err = run(park, capsys, "pause", RIG, "--reason", "lunch")
    assert code == 0, out + err
    record = park.record()
    assert record["state"] == "paused-by-person"
    assert (record["by"], record["reason"]) == ("a-person", "lunch")
    assert Path(record["resume_point"]).is_file()
    assert mutations(park) == []


def test_pause_by_a_seat_of_an_unmapped_rig_records_the_session(park, capsys) -> None:
    """R200: the kernel's operator may pause on the person's word."""
    park.monkeypatch.setenv("OPENRIG_SESSION_NAME", "operator-agent@kernel")
    assert run(park, capsys, "pause", RIG, "--reason", "t")[0] == 0
    assert park.record()["by"] == "operator-agent@kernel"


def test_pause_stop_parks_and_stays_paused(park, capsys) -> None:
    """MR 2: `--stop` runs the engine; the rig stays paused-by-person."""
    code, out, err = run(park, capsys, "pause", RIG, "--reason", "t", "--stop")
    assert code == 0, out + err
    record = park.record()
    assert record["state"] == "paused-by-person" and record["by"] == "a-person"
    assert record["snapshot"] == {"manual": MANUAL, "down": DOWN}
    assert park.calls("down ") == [DOWN_CMD] and park.creates() == []


def test_pause_stop_with_an_unverified_snapshot_stays_paused_and_up(park, capsys) -> None:
    """A park that aborts leaves the rig running, marked as the person left it."""
    park.rig.reply(SNAPSHOTS, "No snapshots\n")
    assert run(park, capsys, "pause", RIG, "--reason", "t", "--stop")[0] == 1
    assert park.record()["state"] == "paused-by-person" and park.calls("down ") == []


def seat(park) -> None:
    """The caller is a seat of the rig."""
    park.monkeypatch.setenv("OPENRIG_SESSION_NAME", f"build-execute@{RIG}")


def other_mapped_seat(park) -> None:
    """The caller is a seat of another mapped rig."""
    park.monkeypatch.setenv("OPENRIG_SESSION_NAME", "orch-lead@other")
    park.host.write_config(f"projects: {{{RIG}: {park.root}, other: {park.root}}}\n")


def unmapped(park) -> None:
    """The rig has no binding."""
    park.host.write_config("projects: {}\n")


def ignored(park) -> None:
    """The rig is ignored."""
    park.config(f"ignore_rigs: [{RIG}]\n")


def already(park) -> None:
    """The rig is marked paused."""
    park.put_record("paused-by-person", down="none")


def listed(park) -> None:
    """A paused file names the rig."""
    path = park.host.base / "paused.txt"
    path.write_text(f"# paused\nother\n  {RIG}  # since Monday\n")
    park.config(f"paused_files: [{path}]\n")


def unreadable_file(park) -> None:
    """A paused file cannot be read."""
    park.config(f"paused_files: [{park.host.base / 'absent.txt'}]\n")


def bad_paused_files(park) -> None:
    """The paused_files entry is invalid."""
    park.config("paused_files: [relative.txt]\n")


def bad_yaml(park) -> None:
    """The config is not YAML."""
    park.host.write_config("projects: [\n")


def bad_projects(park) -> None:
    """A binding of the wrong type."""
    park.host.write_config(f"projects: {{{RIG}: {park.root}, other: 5}}\n")


def bad_record(park) -> None:
    """The record holds an unknown state."""
    park.put_record("sleeping")


REFUSALS = [
    (seat, "a seat of the mapped rig"),
    (other_mapped_seat, "a seat of the mapped rig"),
    (unmapped, "is not mapped"),
    (ignored, "is not mapped"),
    (already, "already paused"),
    (listed, "remove that line (HACF never edits it)"),
    (unreadable_file, "cannot be read"),
    (bad_paused_files, "paused_files"),
    (bad_yaml, "host.yml"),
    (bad_record, "sleeping"),
    (bad_projects, "projects.other"),
]


@pytest.mark.parametrize("argv", [["pause", RIG, "--reason", "t", "--stop"], ["park", RIG]])
@pytest.mark.parametrize(("arrange", "words"), REFUSALS)
def test_pause_and_park_refusals(park, capsys, argv: list, arrange, words: str) -> None:
    """MR 2, MR 4: exit 2, nothing called and nothing written."""
    arrange(park)
    before = park.record()
    code, out, err = run(park, capsys, *argv)
    assert code == 2, out + err
    assert words in err
    assert park.rig.calls == [] and park.record() == before


@pytest.mark.parametrize("argv", [["pause", RIG, "--reason", "t"], ["park", RIG]])
def test_pause_and_park_need_a_boundary(park, capsys, argv: list) -> None:
    """MR 2, MR 4: exit 1 with the failing checks; nothing is written."""
    park.rig.reply(NODES, nodes(activity="running"))
    code, out, _ = run(park, capsys, *argv)
    assert code == 1
    assert "B1: no - " in out and "agentActivity.state running" in out
    assert mutations(park) == [] and park.record() == {}


@pytest.mark.parametrize("name", ["-x", "a/b", ".hidden", "spec.yaml", ""])
def test_a_name_rig_could_misread_is_refused(park, capsys, name: str) -> None:
    """A name that is a flag or a path to `rig up` never reaches it."""
    park.host.write_config(f"projects: {{{json.dumps(name)}: {park.root}}}\n")
    code, _, _ = run(park, capsys, "restore", "--", name)
    assert code == 2 and park.rig.calls == []


def test_resume_of_a_stopped_rig_restores_it_and_clears_the_mark(park, capsys) -> None:
    """MR 3: a stopped paused rig is restored; one lead message."""
    park.parked("paused-by-person")
    code, out, err = run(park, capsys, "resume", RIG)
    assert code == 0, out + err
    assert park.calls("up ") == [UP_CMD] and len(park.calls("send ")) == 1
    assert park.record()["state"] == "running"


def test_resume_of_a_running_rig_clears_the_mark(park, capsys) -> None:
    """MR 3: nothing to restore; the mark is cleared and the lead told."""
    park.put_record("paused-by-person", down="none")
    code, out, err = run(park, capsys, "resume", RIG)
    assert code == 0, out + err
    assert park.calls("up ", "restore ") == [] and len(park.calls("send ")) == 1
    assert park.record()["state"] == "running"


def test_resume_of_a_failed_restore_stays_failed(park, capsys) -> None:
    """The restore's verdict stands: one escalation, no `running`."""
    park.parked("paused-by-person")
    park.rig.reply(UP_CMD, up_result("partially_restored"), rc=1, then=now_running())
    assert run(park, capsys, "resume", RIG)[0] == 1
    assert park.record()["state"] == "restore-failed" and len(park.creates()) == 1


@pytest.mark.parametrize(
    "seen", [as_reply(nodes("running", lead="exited")), as_reply("", rc=1), as_reply([])]
)
def test_resume_of_a_partly_running_or_unknown_rig_changes_nothing(park, capsys, seen) -> None:
    """A partial restore is never cleared as a success."""
    park.put_record("restore-failed")
    park.rig.reply(NODES, seen["stdout"], rc=seen["rc"])
    before = park.record()
    code, _, _ = run(park, capsys, "resume", RIG)
    assert code == 1
    assert mutations(park) == [] and park.record() == before


def test_resume_of_a_pause_that_never_stopped_the_rig_needs_no_whole_rig(park, capsys) -> None:
    """A seat that exited during a plain pause does not keep the mark on."""
    park.put_record("paused-by-person", down="none")
    park.rig.reply(NODES, nodes("exited"))
    assert run(park, capsys, "resume", RIG)[0] == 0
    assert park.record()["state"] == "running" and park.calls("up ", "restore ") == []


def test_resume_of_a_stopped_rig_with_no_snapshot_is_refused(park, capsys) -> None:
    """HACF starts nothing it cannot verify, and says how to bring it back."""
    park.stopped()
    park.put_record("paused-by-person", down="none")
    code, _, err = run(park, capsys, "resume", RIG)
    assert code == 2 and f"rig up {RIG} --existing" in err
    assert mutations(park) == []


@pytest.mark.parametrize("state", [None, "running"])
def test_resume_of_an_unmarked_rig_does_nothing(park, capsys, state) -> None:
    """No mark, no message that claims a restore."""
    if state:
        park.put_record(state, down="none")
    code, out, _ = run(park, capsys, "resume", RIG)
    assert code == 0 and "nothing to resume" in out
    assert park.rig.calls == []


@pytest.mark.parametrize(
    ("arrange", "words"),
    [
        (seat, "a seat of the mapped rig"),
        (unmapped, "is not mapped"),
        (listed, "remove that line (HACF never edits it)"),
        (unreadable_file, "cannot be read"),
    ],
)
def test_resume_refusals(park, capsys, arrange, words: str) -> None:
    """MR 3, R183: a rig listed in a paused file is resumed only by the person's own edit."""
    park.parked("paused-by-person")
    arrange(park)
    before = park.record()
    code, _, err = run(park, capsys, "resume", RIG)
    assert code == 2 and words in err
    assert park.rig.calls == [] and park.record() == before


def test_resume_of_a_file_listed_rig_names_the_file(park, capsys) -> None:
    """R183's exact line, and the file is not edited."""
    listed(park)
    path = park.host.base / "paused.txt"
    text = path.read_bytes()
    code, _, err = run(park, capsys, "resume", RIG)
    assert code == 2
    assert f"{RIG} is listed in {path}; remove that line (HACF never edits it)" in err
    assert path.read_bytes() == text


@pytest.mark.parametrize(
    ("state", "words"),
    [
        ("paused-by-person", "already paused"),
        ("running", "not parked"),
        (None, "not parked"),
    ],
)
def test_restore_runs_only_on_a_parked_or_failed_rig(park, capsys, state, words) -> None:
    """MR 4."""
    if state:
        park.put_record(state)
    code, _, err = run(park, capsys, "restore", RIG)
    assert code == 2 and words in err
    assert mutations(park) == []


def test_restore_of_a_failed_rig_is_the_persons_to_run_again(park, capsys) -> None:
    """The escalation's option A."""
    park.parked("restore-failed")
    assert run(park, capsys, "restore", RIG)[0] == 0
    assert park.record()["state"] == "running"


@pytest.mark.parametrize("argv", [["park", RIG], ["restore", RIG], ["resume", RIG]])
def test_one_operation_per_rig_at_a_time(park, capsys, argv: list) -> None:
    """A second verb on a rig waits for none: exit 75, nothing called."""
    park.put_record("parked-waiting")
    with park.tool.rig_operation(RIG):
        code, _, err = run(park, capsys, *argv)
    assert code == 75 and "another operation" in err
    assert park.rig.calls == []


@pytest.mark.parametrize(
    "argv", [["pause", RIG, "--reason", "t"], ["resume", RIG], ["park", RIG], ["restore", RIG]]
)
def test_newer_state_writes_nothing_and_exits_75(park, capsys, argv: list) -> None:
    """US-014: no call and no write."""
    newer = park.host.state / "a.json"
    newer.parent.mkdir(parents=True)
    newer.write_text('{"schema": "hacf.host/v2"}')
    assert run(park, capsys, *argv)[0] == 75
    assert park.rig.calls == []
    assert sorted(os.listdir(park.host.state)) == ["a.json"]


@pytest.mark.parametrize(
    "argv", [["pause", RIG], ["pause"], ["resume"], ["park", RIG, "--force"], ["restore"]]
)
def test_usage_errors(park, capsys, argv: list) -> None:
    """`pause` needs --reason, and each verb exactly one rig."""
    with pytest.raises(SystemExit) as error:
        park.tool.main(argv)
    assert error.value.code == 2 and park.rig.calls == []


# ------------------------------------------------------------------------------- paused files


def test_paused_rigs_is_the_union_of_records_and_files(park) -> None:
    """MR 5 (R183): records and every readable file; an unreadable one warns."""
    one = park.host.base / "one.txt"
    one.write_text("# a comment\n\nalpha\n  beta # why\n")
    two = park.host.base / "home" / "two.txt"
    two.parent.mkdir(parents=True, exist_ok=True)
    two.write_text("gamma\n")
    gone = park.host.base / "gone.txt"
    park.config(f"paused_files: [{one}, '~/two.txt', {gone}]\n")
    park.put_record("paused-by-person")
    other = park.host.state / "rigs" / "delta.json"
    other.write_text(json.dumps({"schema": "hacf.host/v1", "state": "parked-waiting"}))
    (park.host.state / "rigs" / "bad.json").write_text("{")
    before = (one.read_bytes(), two.read_bytes())
    names, warnings = park.tool.paused_rigs()
    assert names == {RIG, "alpha", "beta", "gamma"}
    assert len(warnings) == 2
    assert any(str(gone) in w for w in warnings) and any("bad.json" in w for w in warnings)
    assert (one.read_bytes(), two.read_bytes()) == before and not gone.exists()
