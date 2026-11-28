"""Tests of auto-park: `supervise` and `supervisor install|remove|status` (OPR.99.0.16.6).

Every test runs on the isolated host of conftest.py: the state dir, config and XDG dirs are under
tmp_path, `rig` and `systemctl` are fakes, and the host's are never called. A settable UTC clock
stands in for the grace, and each bound project holds a fake seat-config.py.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from .test_pause_resume import SEAT_CONFIG, as_reply, snapshot_table

ADVISOR = "advisor-lead@kernel"
PERSON = "someone@external"
RIG = "demo-hacf"
LOW = "side-hacf"
ACTIVE = "queue list -A --state pending,in-progress,blocked --json --limit 10000"
IN_PROGRESS = "queue list -A --state in-progress --json --limit 10000"
CREATE = "queue create *"
START = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
MUTATING = ("snapshot", "down", "up", "restore", "send", "queue create")


def stamp(moment: datetime) -> str:
    """A UTC time as the tool writes it."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def row(
    qitem: str,
    state: str = "pending",
    source: str = f"orch-lead@{RIG}",
    destination: str = ADVISOR,
    tags: tuple[str, ...] = (),
) -> dict:
    """One queue row as the compact `rig queue list --json` shows it."""
    return {
        "qitemId": qitem,
        "state": state,
        "sourceSession": source,
        "destinationSession": destination,
        "tags": list(tags),
    }


def question(qitem: str = "q-1", rig: str = RIG, **extra: object) -> dict:
    """A rig's question to the person."""
    return row(qitem, source=f"orch-lead@{rig}", **extra)


def work(qitem: str = "w-1", rig: str = RIG, state: str = "pending", **extra: object) -> dict:
    """A row addressed to a seat of the rig."""
    return row(qitem, state, source=ADVISOR, destination=f"build-execute@{rig}", **extra)


class Fleet:
    """The isolated host with mapped rigs, idle and running, a fake queue and a settable clock."""

    def __init__(self, host, tool, monkeypatch: pytest.MonkeyPatch, names: tuple) -> None:
        """Script `names`, in priority order, each at a boundary."""
        self.host, self.tool, self.rig = host, tool, host.rig
        self.monkeypatch = monkeypatch
        self.names = names
        self.now = START
        self.mono = 1000.0
        self.waits: list[float] = []
        self.status: dict[str, str] = {}
        self.roots: dict[str, Path] = {}
        self.queue: list[dict] = []
        meminfo = host.base / "meminfo"
        meminfo.write_text(f"MemAvailable: {32768 * 1024} kB\n")
        monkeypatch.setenv("HACF_HOST_MEMINFO", str(meminfo))
        self.meminfo = meminfo
        for name in ("HACF_HOST_MIN_MB", "HACF_HEAVY_LOCK_HELD"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(tool, "utcnow", lambda: self.now, raising=False)
        monkeypatch.setattr(tool, "clock", lambda: self.mono)
        monkeypatch.setattr(tool, "pause_for", self.sleep)
        for name in names:
            self.add(name)
        self.config()
        self.set_queue()

    def add(self, name: str) -> None:
        """A project, its routing and every reply a park and a restore of the rig need."""
        root = self.host.project(name)
        self.roots[name] = root
        script = root / "openrig" / "hacf" / "bin" / "seat-config.py"
        script.parent.mkdir(parents=True)
        reply = self.host.base / f"routing-{name}.json"
        script.write_text(
            SEAT_CONFIG.format(log=str(self.host.base / "seat-config.log"), reply=str(reply))
        )
        self.routing(name)
        self.status[name] = "running"
        self.seats(name)
        self.rig.reply(
            f"snapshot {name}", f"Snapshot created: snap-m-{name}\nTo restore: rig restore\n"
        )
        self.rig.reply(f"snapshot list {name}", snapshot_table(f"snap-m-{name}"))
        self.rig.reply(CREATE, {"qitemId": "qitem-1"}, queue_rules=True)
        self.rig.reply("send *", "Sent\n")

    def routing(
        self,
        name: str,
        question: object = None,
        update: object = None,
        tags: dict | None = None,
        rc: int = 0,
    ) -> None:
        """What the project's seat-config.py prints for the routing section."""
        value = {
            "question.destination": {"value": question or {"session": ADVISOR}},
            "update.destination": {"value": update or {"session": ADVISOR}},
        }
        if tags is not None:
            value["tags"] = {"value": tags}
        (self.host.base / f"routing-{name}.json").write_text(
            json.dumps({"stdout": json.dumps({"routing": value}), "rc": rc})
        )

    def nodes(self, name: str, state: str = "running", activity: str = "idle") -> list:
        """Two seats of the rig as `rig ps --nodes` shows them."""
        return [
            {
                "rigName": name,
                "logicalId": logical,
                "canonicalSessionName": f"{logical.replace('.', '-')}@{name}",
                "sessionStatus": state,
                "lifecycleState": "running" if state == "running" else "detached",
                "agentActivity": {"state": activity},
                "needsAttention": False,
                "arbitratedNeedsInput": False,
                # top level, as a live 0.7.23 `rig ps --nodes --full --json` has it
                "lastActivityAt": stamp(START - timedelta(hours=1)) if state == "running" else None,
            }
            for logical in ("orch.lead", "build.execute")
        ]

    def rigs(self, **changed: str) -> list:
        """`rig ps --json`'s rows, with `changed` statuses (keys with _ for -)."""
        status = {**self.status, **{k.replace("_", "-"): v for k, v in changed.items()}}
        return [{"name": n, "rigName": n, "status": s} for n, s in status.items()]

    def seats(self, name: str, state: str = "running", activity: str = "idle") -> None:
        """Set one rig's seats and its row in `rig ps --json`."""
        self.status[name] = "running" if state == "running" else "stopped"
        nodes = f"ps --nodes --rig {name} --full --json"
        self.rig.reply(nodes, self.nodes(name, state, activity))
        self.rig.reply("ps --json", self.rigs())
        # `down` and `up` change what the next reads show, for this rig only.
        stopped = {
            nodes: as_reply(self.nodes(name, "exited")),
            "ps --json": as_reply(self.rigs(**{name.replace("-", "_"): "stopped"})),
        }
        running = {
            nodes: as_reply(self.nodes(name)),
            "ps --json": as_reply(self.rigs(**{name.replace("-", "_"): "running"})),
        }
        down = {"snapshotId": f"snap-d-{name}", "errors": []}
        self.rig.reply(f"down {name} --json", down, then=stopped)
        # The keys and nesting of OpenRig 0.7.23's result, recorded verbatim in the
        # OPR.99.0.16.11 live-trial report's Appendix; placeholder values.
        result = {
            "status": "restored",
            "rigId": f"id-{name}",
            "rigName": name,
            "snapshotId": f"snap-d-{name}",
            "snapshotKind": "auto-pre-down",
            "rigResult": "fully_restored",
            "nodes": [{"nodeId": "node-id", "logicalId": "orch.lead", "status": "resumed"}],
            "excludedNodes": [],
            "warnings": ["a warning"],
            "attachCommand": f"tmux attach -t orch-lead@{name}",
        }
        self.rig.reply(f"up {name} --existing --json --timeout 900", result, then=running)

    def config(self, autopark: str = "", extra: str = "", enabled: bool = True) -> None:
        """The host config: every rig bound, the first the highest priority."""
        projects = ", ".join(f"{n}: {r}" for n, r in self.roots.items())
        settings = f"enabled: {'true' if enabled else 'false'}"
        if autopark:
            settings += f", {autopark}"
        self.host.write_config(
            f"priority: [{', '.join(self.names)}]\nprojects: {{{projects}}}\n"
            f"autopark: {{{settings}}}\n{extra}"
        )

    def set_queue(self, *rows: dict) -> None:
        """The host's active rows, as both queue reads show them."""
        self.queue = list(rows)
        self.rig.reply(ACTIVE, self.queue)
        self.rig.reply(IN_PROGRESS, [r for r in self.queue if r["state"] == "in-progress"])

    def sleep(self, seconds: float) -> None:
        """The fake wait of the engine."""
        self.waits.append(seconds)
        self.mono += seconds

    def tick(self, capsys, *extra: str, after: float = 0) -> int:
        """Move the clock `after` seconds and run one tick; its exit code."""
        self.now += timedelta(seconds=after)
        code = self.tool.main(["supervise", "--once", *extra])
        done = capsys.readouterr()
        self.output = done.out + done.err
        return code

    def run_for(self, capsys, minutes: int, *extra: str) -> None:
        """Tick every 2 minutes for `minutes`."""
        for _ in range(minutes // 2):
            self.tick(capsys, *extra, after=120)

    def hold(self, capsys, *extra: str) -> None:
        """One tick now and one when the 15-minute grace has run out."""
        assert self.tick(capsys, *extra) == 0, self.output
        self.run_for(capsys, 14, *extra)
        self.tick(capsys, *extra, after=60)

    def record(self, name: str = RIG) -> dict:
        """`rigs/<rig>.json`, or {}."""
        path = self.host.state / "rigs" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def put_record(self, name: str = RIG, **fields: object) -> None:
        """Write a rig's record."""
        path = self.host.state / "rigs" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "hacf.host/v1", **fields}))

    def parked(self, name: str = RIG, waits_on: tuple = ("q-1",), **extra: object) -> None:
        """A stopped rig as a supervisor's park leaves it."""
        self.seats(name, "exited")
        for other in self.names:  # `up` of one rig leaves the others as they are now
            if other != name:
                self.seats(other, "running" if self.status[other] == "running" else "exited")
        self.seats(name, "exited")
        fields = {
            "state": "parked-waiting",
            "by": "supervisor",
            "since": stamp(START - timedelta(hours=1)),
            "waits_on": list(waits_on),
            "parks": [stamp(START - timedelta(hours=1))],
            "snapshot": {"manual": f"snap-m-{name}", "down": f"snap-d-{name}"},
        }
        self.put_record(name, **{**fields, **extra})

    def calls(self, *verbs: str) -> list[str]:
        """The fake rig's calls as lines, only those starting with one of `verbs` when given."""
        lines = [" ".join(c) for c in self.rig.calls]
        return [x for x in lines if not verbs or x.startswith(verbs)]

    def mutations(self) -> list[str]:
        """The calls that change the host."""
        found = self.calls(*[f"{verb} " for verb in MUTATING])
        return [c for c in found if not c.startswith("snapshot list ")]

    def creates(self) -> list[dict]:
        """Each `queue create` call as {flag: value}."""
        return [
            dict(zip(argv[2::2], argv[3::2], strict=True))
            for argv in self.rig.calls
            if argv[:2] == ["queue", "create"]
        ]

    def log(self) -> list[str]:
        """supervisor.log's lines without their time."""
        path = self.host.state / "supervisor.log"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        assert all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", line) for line in lines), lines
        return [line.split(" ", 1)[1] for line in lines]

    def logged(self, text: str) -> list[str]:
        """The log lines holding `text`."""
        return [line for line in self.log() if text in line]


@pytest.fixture
def fleet(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """One mapped rig."""
    return Fleet(host, tool, monkeypatch, (RIG,))


@pytest.fixture
def two(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """Two mapped rigs: RIG above LOW in priority."""
    return Fleet(host, tool, monkeypatch, (RIG, LOW))


# ------------------------------------------------------------------------------- park


def test_a_question_row_by_destination_parks_after_the_grace_not_before(fleet, capsys) -> None:
    """MR 2, P3 and P5."""
    fleet.set_queue(question())
    assert fleet.tick(capsys) == 0, fleet.output
    assert fleet.record()["eligible_since"] == stamp(START)
    fleet.run_for(capsys, 14)
    assert fleet.mutations() == []
    assert fleet.tick(capsys, after=60) == 0, fleet.output
    assert fleet.calls("down ") == [f"down {RIG} --json"]
    record = fleet.record()
    assert (record["state"], record["by"]) == ("parked-waiting", "supervisor")
    assert record["waits_on"] == ["q-1"]
    assert record["parks"] == [stamp(fleet.now)]
    assert "eligible_since" not in record
    (line,) = fleet.logged(f"park {RIG} done")
    assert re.search(r"seconds=\d", line) and "waits_on=q-1" in line


def test_a_question_row_by_tag_only_parks(fleet, capsys) -> None:
    """MR 2: a human tag is enough, whatever the destination."""
    fleet.config(extra="human: {tags: [ask:me]}\n")
    fleet.set_queue(question(destination="someone-else@other", tags=("ask:me",)))
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting"


def test_the_routing_tag_and_a_configured_destination_count(fleet, capsys) -> None:
    """R186: routing tags.question, and human.destinations adds destinations."""
    fleet.routing(RIG, tags={"question": "proj:q"})
    fleet.config(extra=f"human: {{destinations: [{PERSON}]}}\n")
    fleet.set_queue(
        question("q-tag", destination="x@other", tags=("proj:q",)),
        question("q-dest", destination=PERSON, state="blocked"),
    )
    fleet.hold(capsys)
    assert fleet.record()["waits_on"] == ["q-dest", "q-tag"]


def test_a_discovered_destination_counts(fleet, capsys) -> None:
    """R199: {rule: discover} adds the running advisor's address."""
    fleet.routing(RIG, question={"rule": "discover"})
    advisor = {
        "logicalId": "advisor.lead",
        "canonicalSessionName": ADVISOR,
        "sessionStatus": "running",
    }
    fleet.rig.reply("ps --nodes --rig kernel --full --json", [advisor])
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting"


def test_a_failed_routing_resolve_makes_p3_unknown(fleet, capsys) -> None:
    """MR 2: unknown never parks."""
    fleet.routing(RIG, rc=3)
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and "eligible_since" not in fleet.record()
    assert fleet.logged(f"skip {RIG}: P3: unknown: seat-config.py")


@pytest.mark.parametrize("state", ["pending", "in-progress"])
def test_a_row_addressed_to_the_rig_blocks_the_park(fleet, capsys, state: str) -> None:
    """MR 2: an actionable row means the rig has work."""
    fleet.set_queue(question(), work(state=state))
    fleet.hold(capsys)
    assert fleet.mutations() == [] and "eligible_since" not in fleet.record()
    assert fleet.logged("w-1")


def test_a_pending_fyi_row_and_a_blocked_row_do_not_block(fleet, capsys) -> None:
    """MR 2, R186: neither is work."""
    fleet.set_queue(
        question(),
        work("w-fyi", tags=("hacf:update",)),
        work("w-ext", state="blocked", tags=("external:roadmap",)),
    )
    fleet.hold(capsys)
    assert fleet.record()["waits_on"] == ["q-1"]


def test_a_blocked_row_addressed_to_the_rig_does_not_park_it(fleet, capsys) -> None:
    """MR 2: with no question row there is nothing to wait on."""
    fleet.set_queue(work("w-ext", state="blocked", tags=("external:roadmap",)))
    fleet.hold(capsys)
    assert fleet.mutations() == []
    assert fleet.logged(f"skip {RIG}: P3: no human-question row")


def test_an_fyi_the_rig_sends_the_person_is_not_a_question(fleet, capsys) -> None:
    """P80 Q1: a row with an FYI tag is never a human-question row."""
    fleet.set_queue(question(tags=("hacf:update",)))
    fleet.hold(capsys)
    assert fleet.mutations() == []
    assert fleet.logged(f"skip {RIG}: P3: no human-question row")
    fleet.set_queue(question("q-fyi", tags=("hacf:update",)), question("q-2"))
    fleet.hold(capsys)
    assert fleet.record()["waits_on"] == ["q-2"]


def test_a_row_another_rig_sent_is_not_this_rigs_question(fleet, capsys) -> None:
    """R186: the source must be a seat of the rig."""
    fleet.set_queue(question(rig="other-rig"))
    fleet.hold(capsys)
    assert fleet.mutations() == []


@pytest.mark.parametrize(
    ("bad", "words"),
    [
        ({"state": "odd"}, "state"),
        ({"qitemId": None}, "no id"),
    ],
)
def test_a_row_that_cannot_be_judged_never_parks(fleet, capsys, bad: dict, words: str) -> None:
    """Unknown never parks."""
    odd = work() if "state" in bad else question("q-2")
    fleet.set_queue(question(), {**odd, **bad})
    fleet.hold(capsys)
    assert fleet.mutations() == []
    assert any(words in line for line in fleet.logged(f"skip {RIG}: P3: unknown"))


@pytest.mark.parametrize(
    "reply",
    [
        {"stdout": "daemon down", "rc": 1},
        {"stdout": "not json"},
        {"stdout": json.dumps([question()] * 10000)},
    ],
)
def test_an_unknown_queue_never_parks(fleet, capsys, reply: dict) -> None:
    """MR 2: a failed or full list is unknown."""
    fleet.set_queue(question())
    fleet.rig.reply(ACTIVE, reply["stdout"], rc=reply.get("rc", 0))
    assert fleet.tick(capsys) == 1
    assert fleet.tick(capsys, after=900) == 1
    assert fleet.mutations() == [] and fleet.logged(f"skip {RIG}: P3: unknown")


def test_an_unknown_rig_list_does_nothing_and_resets_the_grace(fleet, capsys) -> None:
    """A failed `rig ps` is unknown for every rig; the tick exits 1."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    fleet.rig.reply("ps --json", "no daemon", rc=1)
    assert fleet.tick(capsys, after=120) == 1
    assert "eligible_since" not in fleet.record() and fleet.mutations() == []
    assert fleet.logged(f"grace reset {RIG}: unknown: rig ps failed")


def test_a_seat_going_running_resets_the_grace_with_its_log_line(fleet, capsys) -> None:
    """R208: the reset names the seat and the reason, and the grace starts again."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    fleet.seats(RIG, activity="running")
    fleet.tick(capsys, after=600)
    (line,) = fleet.logged(f"grace reset {RIG}: ")
    assert f"orch-lead@{RIG}: agentActivity.state running" in line
    assert "eligible_since" not in fleet.record()
    fleet.seats(RIG)
    fleet.tick(capsys, after=120)
    assert fleet.record()["eligible_since"] == stamp(fleet.now)
    fleet.tick(capsys, after=600)  # 22 minutes after the first tick, 10 after the new start
    assert fleet.mutations() == []


def test_a_mapped_rig_shown_stopped_loses_its_grace(fleet, capsys) -> None:
    """A rig that is not running has not held P2."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    assert "eligible_since" in fleet.record()
    fleet.seats(RIG, "exited")
    fleet.tick(capsys, after=120)
    assert "eligible_since" not in fleet.record()


@pytest.mark.parametrize(
    ("gap", "reset"),
    [(1260 + 120, False), (1260 + 121, True)],
)
def test_a_gap_with_no_check_restarts_the_grace(fleet, capsys, gap: int, reset: bool) -> None:
    """P80 Q2: older than TimeoutStartSec + 120 s, the grace starts again."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    fleet.tick(capsys, after=gap)
    assert bool(fleet.mutations()) is not reset
    assert bool(fleet.logged(f"grace reset {RIG}: no check since {stamp(START)}")) is reset
    if reset:
        assert fleet.record()["eligible_since"] == stamp(fleet.now)


@pytest.mark.parametrize("since", ["yesterday", stamp(START + timedelta(hours=1)), 5])
def test_an_unusable_eligible_since_restarts_the_grace(fleet, capsys, since: object) -> None:
    """A time that cannot be read, or lies ahead, is not a start."""
    fleet.set_queue(question())
    fleet.put_record(state="running", eligible_since=since, eligible_checked=stamp(START))
    fleet.tick(capsys)
    assert fleet.record()["eligible_since"] == stamp(START) and fleet.mutations() == []


def last_output(fleet: Fleet, *times: object, state: str = "running") -> None:
    """Set each seat's `lastActivityAt`, in node order; `...` leaves the key out."""
    nodes = fleet.nodes(RIG, state)
    for node, at in zip(nodes, times, strict=True):
        if at is ...:
            del node["lastActivityAt"]
        else:
            node["lastActivityAt"] = stamp(at) if isinstance(at, datetime) else at
    fleet.rig.reply(f"ps --nodes --rig {RIG} --full --json", nodes)


def test_output_between_two_ticks_restarts_the_grace(fleet, capsys) -> None:
    """(e) P107, the trial's episode 2: output 12 minutes before a tick otherwise past grace."""
    fleet.set_queue(question())
    old = START - timedelta(hours=1)
    fleet.tick(capsys)
    fleet.run_for(capsys, 4)
    seen = START + timedelta(minutes=4, seconds=30)  # between two ticks; B1 stays idle
    last_output(fleet, seen, old)
    fleet.now = START + timedelta(minutes=14)
    fleet.tick(capsys, after=0)
    assert fleet.tick(capsys, after=150) == 0, fleet.output  # 16.5 min eligible, 12 since output
    assert fleet.mutations() == []
    line = fleet.logged(f"eligible {RIG}:")[-1]
    assert f"since {stamp(START)} last_activity={seen.isoformat()}" in line
    fleet.tick(capsys, after=180)  # 15 minutes since the output
    assert fleet.calls("down ") == [f"down {RIG} --json"]


@pytest.mark.parametrize("dry", [False, True])
def test_output_older_than_the_grace_parks(fleet, capsys, dry: bool) -> None:
    """(f) Output 16 minutes ago and eligible for 16 minutes parks; a dry run says would-park."""
    fleet.set_queue(question())
    extra = ("--dry-run",) if dry else ()
    last_output(fleet, START, START - timedelta(hours=1))
    fleet.tick(capsys, *extra)
    fleet.run_for(capsys, 14, *extra)
    assert fleet.mutations() == [] and not fleet.logged("would-park")
    fleet.tick(capsys, *extra, after=120)
    assert bool(fleet.logged(f"would-park {RIG}")) is dry
    assert bool(fleet.calls("down ")) is not dry


@pytest.mark.parametrize("bad", [None, ..., "just now", "2026-11-01T11:00:00", 5])
def test_a_running_seat_with_no_last_output_time_parks_nothing(fleet, capsys, bad) -> None:
    """(g) Unknown is never quiet: not eligible, with the reason, and the grace resets."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    assert "eligible_since" in fleet.record()
    last_output(fleet, START - timedelta(hours=1), bad)
    fleet.run_for(capsys, 20)
    assert fleet.mutations() == [] and "eligible_since" not in fleet.record()
    reason = f"P5: unknown: OpenRig gives no lastActivityAt for build-execute@{RIG}"
    assert fleet.logged(f"skip {RIG}: {reason}")
    assert fleet.logged(f"grace reset {RIG}: {reason}")


def test_a_future_last_output_counts_as_now(fleet, capsys) -> None:
    """(h) A skewed time delays the park while it lies ahead, and no longer than now."""
    fleet.set_queue(question())
    last_output(fleet, START + timedelta(days=1), START - timedelta(hours=1))
    fleet.hold(capsys)
    assert fleet.mutations() == []
    last_output(fleet, fleet.now - timedelta(minutes=15), START - timedelta(hours=1))
    fleet.tick(capsys, after=0)
    assert fleet.calls("down ") == [f"down {RIG} --json"]


def test_a_stopped_seats_missing_output_time_is_ignored(fleet, capsys) -> None:
    """(i) Only running seats are asked; a seat that is not running has `null`."""
    nodes = fleet.nodes(RIG)
    nodes[1].update(sessionStatus="exited", lifecycleState="detached", lastActivityAt=None)
    fleet.rig.reply(f"ps --nodes --rig {RIG} --full --json", nodes)
    # B1 refuses a stopped seat itself, so P5's read is judged on its own.
    newest, why = fleet.tool._last_output(RIG)
    assert (newest, why) == (START - timedelta(hours=1), "")


def test_the_tool_names_no_session_log_path() -> None:
    """(j) P107's privacy rule: no session log, transcript or harness history is named."""
    source = (REPO_ROOT / "openrig" / "hacf" / "bin" / "hacf-host.py").read_text().lower()
    for name in (".jsonl", ".claude/projects", ".codex", "transcript"):
        assert name not in source, name


@pytest.mark.parametrize(
    ("hours", "parked"),
    [((1, 2, 3), False), ((1, 2), True), ((1, 2, 25), True)],
)
def test_the_park_cap(fleet, capsys, hours: tuple, parked: bool) -> None:
    """P1: fewer than max_parks_per_day parks in the last 24 h."""
    parks = [stamp(START - timedelta(hours=h)) for h in hours]
    fleet.put_record(state="running", parks=parks)
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert (fleet.record()["state"] == "parked-waiting") is parked
    assert bool(fleet.logged("park cap reached")) is not parked
    if parked:
        assert fleet.record()["parks"][: len(parks)] == parks
        assert len(fleet.record()["parks"]) == len(parks) + 1


@pytest.mark.parametrize("parks", ["many", [1, 2, 3], ["?", "?", "?"]])
def test_an_unreadable_park_list_counts_against_the_cap(fleet, capsys, parks: object) -> None:
    """Unknown never parks."""
    fleet.put_record(state="running", parks=parks)
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.mutations() == []


def test_a_restore_failed_rig_is_never_parked_or_restored(fleet, capsys) -> None:
    """P1."""
    fleet.put_record(state="restore-failed")
    fleet.set_queue(question(), work())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and fleet.record()["state"] == "restore-failed"
    assert fleet.logged(f"skip {RIG}: P1: restore-failed")


@pytest.mark.parametrize("parked", [False, True])
def test_a_file_listed_rig_is_never_touched(fleet, capsys, parked: bool) -> None:
    """R183: no park, restore or state write."""
    listed = fleet.host.base / "paused.txt"
    listed.write_text(f"{RIG}  # mine\n")
    fleet.config(extra=f"paused_files: [{listed}]\n")
    if parked:
        fleet.parked()
    fleet.set_queue(question(), *([work()] if parked else []))
    before = fleet.record()
    fleet.hold(capsys)
    assert fleet.mutations() == [] and fleet.record() == before
    assert fleet.logged(f"skip {RIG}: paused by the person: listed in {listed}")
    assert listed.read_text() == f"{RIG}  # mine\n"


def test_a_pause_marked_rig_is_never_touched(fleet, capsys) -> None:
    """R183: the record's pause."""
    fleet.put_record(state="paused-by-person", by="a-person")
    fleet.set_queue(question(), work())
    before = fleet.record()
    fleet.hold(capsys)
    assert fleet.mutations() == [] and fleet.record() == before


def test_an_unreadable_paused_file_stops_every_park_and_restore(two, capsys) -> None:
    """Unreadable is never "nothing paused"."""
    two.config(extra=f"paused_files: [{two.host.base / 'absent.txt'}]\n")
    two.parked(LOW)
    two.set_queue(question(), work(rig=LOW))
    two.hold(capsys)
    assert two.mutations() == [] and two.record() == {}
    assert two.logged(f"skip {RIG}: paused file") and two.logged(f"skip {LOW}: paused file")


def test_an_unmapped_or_ignored_rig_never_parks(fleet, capsys) -> None:
    """R185."""
    fleet.status["stray-hacf"] = "running"
    fleet.rig.reply("ps --json", fleet.rigs())
    fleet.set_queue(question(rig="stray-hacf"))
    fleet.hold(capsys)
    assert fleet.mutations() == []
    assert not fleet.calls("ps --nodes --rig stray-hacf")
    assert fleet.record("stray-hacf") == {}


def test_an_ignored_rig_that_is_also_bound_never_parks(fleet, capsys) -> None:
    """Ignored wins."""
    fleet.config(extra=f"ignore_rigs: [{RIG}]\n")
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and fleet.record() == {}


def test_one_unreadable_record_skips_only_that_rig(two, capsys) -> None:
    """A record that cannot be judged: nothing acts on its rig."""
    path = two.host.state / "rigs" / f"{LOW}.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    two.set_queue(question(), question("q-2", rig=LOW))
    assert two.tick(capsys) == 1  # the unreadable record is reported
    two.tick(capsys, after=900)
    assert two.calls("down ") == [f"down {RIG} --json"]
    assert two.logged(f"skip {LOW}: ") and path.read_text() == "not json"


@pytest.mark.parametrize(
    "arrival",
    [
        [question(), question("q-new")],
        [question(), work()],
        [],
    ],
)
def test_a_change_before_rig_down_aborts_and_restarts_the_grace(fleet, capsys, arrival) -> None:
    """R191 and P80 Q3: any change aborts; no park, no FYI, and the grace starts again."""
    fleet.set_queue(question())
    fleet.rig.reply(
        f"snapshot list {RIG}",
        snapshot_table(f"snap-m-{RIG}"),
        then={ACTIVE: as_reply(arrival)},
    )
    fleet.hold(capsys)
    assert fleet.calls("snapshot ") and fleet.calls("down ") == [] and fleet.creates() == []
    record = fleet.record()
    assert record["state"] == "running" and "eligible_since" not in record
    assert record.get("parks", []) == []
    assert fleet.logged(f"grace reset {RIG}: park aborted: ")


def test_an_unverified_snapshot_restarts_the_grace_and_sends_nothing(fleet, capsys) -> None:
    """P80 Q3: the next try waits a whole grace."""
    fleet.set_queue(question())
    fleet.rig.reply(f"snapshot list {RIG}", snapshot_table(f"snap-m-{RIG}", "failed"))
    fleet.hold(capsys)
    assert fleet.logged(f"grace reset {RIG}: park aborted: snapshot not verified")
    fleet.run_for(capsys, 14)
    assert len(fleet.calls(f"snapshot {RIG}")) == 1 and fleet.creates() == []
    assert fleet.calls("down ") == []


def test_an_unverified_stop_is_restore_failed_and_the_tick_exits_1(fleet, capsys) -> None:
    """§5.4: a park that ended restore-failed; no FYI, one question row from the engine."""
    fleet.set_queue(question())
    fleet.rig.reply(f"down {RIG} --json", {"errors": ["no"]}, rc=2)
    fleet.tick(capsys)
    assert fleet.tick(capsys, after=900) == 1
    assert fleet.record()["state"] == "restore-failed"
    (sent,) = fleet.creates()
    assert "needs the person" in sent["--summary"]
    assert fleet.logged(f"park {RIG} failed")


# ------------------------------------------------------------------------------- the FYI


@pytest.mark.parametrize(("destination", "intent"), [(PERSON, "update"), (ADVISOR, None)])
def test_the_park_fyis_two_routes_are_both_accepted(fleet, capsys, destination, intent) -> None:
    """P74: a human destination gets --human-intent update, an agent none."""
    fleet.routing(RIG, update={"session": destination}, tags={"update": "p:u", "extra": ["x:y"]})
    fleet.config("fyi_tags: [p:u]")
    fleet.set_queue(question(), question("q-2"))
    fleet.hold(capsys)
    (sent,) = fleet.creates()
    point = fleet.record()["resume_point"]
    assert sent["--destination"] == destination
    assert sent.get("--human-intent") == intent
    summary = f"HACF host: parked {RIG} (waits on 2 question rows; resume point {point})"
    assert sent["--summary"] == summary
    assert sent["--tags"] == f"p:u,rig:{RIG},x:y"
    assert sent["--evidence-ref"] == point and Path(point).is_file()
    assert sent["--source"] == "hacf-host@host" and sent["--body"]
    assert fleet.logged(f"fyi {RIG} sent to {destination}")


def test_an_unresolved_update_destination_sends_nothing(fleet, capsys) -> None:
    """R199: never guessed; the park stands."""
    fleet.routing(RIG, update={"rule": "other"})
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting" and fleet.creates() == []
    assert fleet.logged(f"fyi {RIG} not sent")


# ------------------------------------------------------------------------------- restore


def up(name: str = RIG) -> str:
    """The restore's `rig up` call."""
    return f"up {name} --existing --json --timeout 900"


def test_an_actionable_row_that_arrived_before_the_park_restores(fleet, capsys) -> None:
    """MR 3, R191: level-triggered."""
    fleet.parked()
    early = {**work(), "tsCreated": stamp(START - timedelta(days=2))}
    fleet.set_queue(question(), early)
    assert fleet.tick(capsys) == 0, fleet.output
    assert fleet.calls("up ") == [up()]
    record = fleet.record()
    assert record["state"] == "running" and "eligible_since" not in record
    (line,) = fleet.logged(f"restore {RIG} done")
    assert "trigger=w-1" in line and re.search(r"seconds=\d", line)


@pytest.mark.parametrize("left", [(), ("q-other",)])
def test_a_restore_when_every_waits_on_row_is_over(fleet, capsys, left: tuple) -> None:
    """MR 3: done rows leave the active list."""
    fleet.parked(waits_on=("q-1", "q-2"))
    fleet.set_queue(*[question(q, rig="other-rig") for q in left])
    fleet.tick(capsys)
    assert fleet.calls("up ") == [up()]
    assert fleet.logged("trigger=waits_over")


@pytest.mark.parametrize(
    "rows",
    [
        [question(state="in-progress")],  # a claim of the waited row
        [question(), work(tags=("hacf:update",))],  # an FYI row
        [question(), work(state="blocked")],
        [question("q-2", state="blocked")],  # one of two waits is still there
    ],
)
def test_no_restore_without_an_actionable_row_or_an_over_wait(fleet, capsys, rows) -> None:
    """MR 3, R181."""
    fleet.parked(waits_on=("q-1", "q-2"))
    fleet.set_queue(*rows)
    assert fleet.tick(capsys) == 0, fleet.output
    assert fleet.mutations() == [] and fleet.record()["state"] == "parked-waiting"
    assert fleet.logged(f"skip {RIG}: still waiting on q-1, q-2")


def test_an_empty_waits_on_is_never_over(fleet, capsys) -> None:
    """A supervisor park always waits on a row; without one only work restores."""
    fleet.parked(waits_on=())
    assert fleet.tick(capsys) == 0, fleet.output
    assert fleet.mutations() == []
    assert fleet.logged(f"skip {RIG}: still waiting on no row")


@pytest.mark.parametrize(
    ("rows", "restored"),
    [([], False), ([work()] + [question("q-x", rig="o")] * 9999, True)],
)
def test_a_list_that_may_be_short_proves_only_work(fleet, capsys, rows, restored) -> None:
    """An absent row in a full page is not over; an actionable row in it is work."""
    fleet.parked()
    if rows:
        fleet.set_queue(*rows)
    else:
        fleet.rig.reply(ACTIVE, "no daemon", rc=1)
    assert fleet.tick(capsys) == 1, fleet.output  # the list is unknown either way
    assert bool(fleet.calls("up ")) is restored


def test_a_persons_park_is_never_auto_restored(fleet, capsys) -> None:
    """P75 b: left parked across ticks; `restore` restores it."""
    fleet.parked(by="person", waits_on=())
    fleet.set_queue(work())
    for _ in range(3):
        fleet.tick(capsys, after=120)
    assert fleet.mutations() == [] and fleet.record()["state"] == "parked-waiting"
    assert len(fleet.logged(f"skip {RIG}: parked by person")) == 3
    assert fleet.tool.main(["restore", RIG]) == 0
    assert fleet.calls("up ") == [up()] and fleet.record()["state"] == "running"


def test_a_parked_rig_started_by_hand_is_left(fleet, capsys) -> None:
    """Only a rig `rig ps` shows stopped is restored."""
    fleet.parked()
    fleet.status[RIG] = "running"
    fleet.rig.reply("ps --json", fleet.rigs())
    fleet.set_queue(work())
    fleet.tick(capsys)
    assert fleet.mutations() == []
    assert fleet.logged(f"skip {RIG}: parked, and rig ps does not show it stopped")


def test_restores_are_deferred_during_a_drain_and_parks_proceed(two, capsys) -> None:
    """MR 3, R187."""
    two.parked(LOW)
    two.set_queue(question(), work(rig=LOW))
    drain = two.host.state / "idle-point.json"
    drain.write_text(json.dumps({"schema": "hacf.host/v1", "purpose": "upgrade"}))
    two.hold(capsys)
    assert two.calls("up ") == [] and two.calls("down ") == [f"down {RIG} --json"]
    assert two.logged(f"skip {LOW}: restore deferred by idle point")
    drain.unlink()
    two.tick(capsys, after=120)
    assert two.calls("up ") == [up(LOW)]


def test_a_restore_that_fails_is_restore_failed_with_no_retry(fleet, capsys) -> None:
    """The engine's rule, through the supervisor."""
    fleet.parked()
    fleet.rig.reply(up(), {"rigResult": "partially_restored"}, rc=1)
    fleet.set_queue(work())
    assert fleet.tick(capsys) == 1
    assert fleet.tick(capsys, after=120) == 0
    assert len(fleet.calls("up ")) == 1 and fleet.record()["state"] == "restore-failed"
    assert fleet.logged(f"restore {RIG} failed")


def test_one_restore_per_tick_the_highest_tier_first(two, capsys) -> None:
    """P76."""
    two.parked(LOW, since=stamp(START - timedelta(hours=5)))
    two.parked(RIG)
    two.set_queue(work(), work("w-2", rig=LOW))
    assert two.tick(capsys) == 0, two.output
    assert two.calls("up ") == [up(RIG)]
    assert two.logged(f"restore queued: {LOW} (next tick)")
    two.tick(capsys, after=120)
    assert two.calls("up ") == [up(RIG), up(LOW)]


def test_park_time_breaks_a_restore_tie(two, capsys) -> None:
    """P76: within a tier, the earlier park first."""
    two.host.write_config(
        two.host.config.read_text().replace(f"[{RIG}, {LOW}]", f"[[{RIG}, {LOW}]]")
    )
    two.parked(RIG, since=stamp(START - timedelta(hours=1)))
    two.parked(LOW, since=stamp(START - timedelta(hours=2)))
    two.set_queue(work(), work("w-2", rig=LOW))
    two.tick(capsys)
    assert two.calls("up ") == [up(LOW)]


# ------------------------------------------------------------------------------- P81


@pytest.mark.parametrize(
    ("broken", "words"),
    [
        ({"snapshot": None}, "no snapshot id"),
        ({"snapshot": {}}, "no snapshot id"),
        ({"snapshot": {"manual": None, "down": None}}, "no snapshot id"),
        ({"snapshot": {"manual": "", "down": 7}}, "no snapshot id"),
        ({"parks": None}, "no parks entry"),
        ({"parks": []}, "no parks entry"),
        ({"parks": "once"}, "no parks entry"),
    ],
)
def test_an_unusable_record_does_not_hold_the_restore_slot(two, capsys, broken, words) -> None:
    """MR 8, P83: restore-failed and one row for it, and the same tick serves the next due rig."""
    two.parked(LOW)
    two.parked(RIG, **broken)
    two.set_queue(work(), work("w-2", rig=LOW))
    assert two.tick(capsys) == 1, two.output
    assert two.calls("up ") == [up(LOW)]
    assert two.calls("restore ") == []
    record = two.record()
    assert record["state"] == "restore-failed" and words in record["reason"]
    assert two.record(LOW)["state"] == "running"
    (sent,) = two.creates()
    path = two.host.state / "rigs" / f"{RIG}.json"
    assert sent["--evidence-ref"] == str(path)
    assert sent["--destination"] == ADVISOR and "--human-intent" not in sent
    assert RIG in sent["--summary"] and words in sent["--body"]
    assert two.logged(f"restore {RIG} failed")
    assert not two.logged("restore queued:")
    assert two.tick(capsys, after=120) == 0, two.output
    assert len(two.creates()) == 1 and two.calls("up ") == [up(LOW)]


def test_an_unusable_records_row_goes_to_the_person_as_a_decision(fleet, capsys) -> None:
    """P74's routing."""
    fleet.routing(RIG, question={"session": PERSON})
    fleet.parked(snapshot={})
    fleet.set_queue(work())
    assert fleet.tick(capsys) == 1
    (sent,) = fleet.creates()
    assert (sent["--destination"], sent["--human-intent"]) == (PERSON, "decision")


def test_a_dry_run_only_logs_an_unusable_record(two, capsys) -> None:
    """MR 8 under --dry-run: nothing is marked or sent, and the next rig is still shown."""
    two.parked(LOW)
    two.parked(RIG, snapshot={})
    two.set_queue(work(), work("w-2", rig=LOW))
    before = two.record()
    assert two.tick(capsys, "--dry-run") == 0, two.output
    assert two.mutations() == [] and two.record() == before
    assert two.logged(f"would-fail {RIG} no snapshot id")
    assert two.logged(f"would-restore {LOW} trigger=w-2")


def test_an_unusable_record_that_is_not_due_is_left(fleet, capsys) -> None:
    """No restore would start, so nothing is marked."""
    fleet.parked(snapshot={})
    fleet.set_queue(question())
    assert fleet.tick(capsys) == 0, fleet.output
    assert fleet.record()["state"] == "parked-waiting" and fleet.creates() == []


def test_a_launch_failure_still_takes_the_slot(two, capsys) -> None:
    """MR 8: a `rig up` that ran is slices 05 and 06's case."""
    two.parked(LOW)
    two.parked(RIG)
    two.rig.reply(up(), {"rigResult": "partially_restored"}, rc=1)
    two.set_queue(work(), work("w-2", rig=LOW))
    assert two.tick(capsys) == 1
    assert two.calls("up ") == [up(RIG)]
    assert two.logged(f"restore queued: {LOW} (next tick)")


def test_a_stop_during_an_unusable_records_row_still_asks(fleet, capsys) -> None:
    """P81: no rig is left restore-failed with nobody told."""
    fleet.parked(snapshot={})
    fleet.set_queue(work())
    sent = []
    real = fleet.tool.escalate

    def interrupted(outcome, root, resume_point, **extra):
        sent.append(outcome.reason)
        if len(sent) == 1:
            os.kill(os.getpid(), signal.SIGTERM)
        return real(outcome, root, resume_point, **extra)

    fleet.monkeypatch.setattr(fleet.tool, "escalate", interrupted)
    assert fleet.tick(capsys) == 1
    assert len(fleet.creates()) == 1 and fleet.record()["state"] == "restore-failed"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1]",
        '{"schema": "hacf.host/v1", "state": "weird", "by": "supervisor"}',
        '{"schema": "other/v1", "state": "parked-waiting", "by": "supervisor"}',
    ],
)
def test_an_unreadable_record_is_listed_and_never_rewritten(two, capsys, text: str) -> None:
    """MR 8, P85: it may be a person's pause; it holds no slot, and status names it."""
    two.parked(LOW)
    two.parked(RIG)
    path = two.host.state / "rigs" / f"{RIG}.json"
    path.write_text(text)
    two.set_queue(work(), work("w-2", rig=LOW))
    assert two.tick(capsys) == 1, two.output
    assert two.calls("up ") == [up(LOW)] and two.creates() == []
    assert path.read_text() == text
    assert two.logged(f"skip {RIG}: ")
    line = f"unreadable record: {path}"
    assert two.tool.main(["supervisor", "status"]) == 0
    assert line in capsys.readouterr().out.splitlines()
    assert two.tool.main(["status", "--json"]) == 0
    assert line in capsys.readouterr().err.splitlines()


def test_status_lists_no_unreadable_record_when_all_read(fleet, capsys) -> None:
    """MR 8, P85: nothing is listed for a good record."""
    fleet.parked()
    assert fleet.tool.main(["status", "--json"]) == 0
    assert "unreadable record" not in capsys.readouterr().err
    assert fleet.tool.main(["supervisor", "status"]) == 0
    assert "unreadable record" not in capsys.readouterr().out


def test_the_supervisors_restore_never_waits_for_memory(two, capsys) -> None:
    """P81 (i): short of memory, nothing starts, nothing waits, and the next tick looks again."""
    two.parked(LOW)
    two.parked(RIG)
    two.set_queue(work(), work("w-2", rig=LOW))
    two.meminfo.write_text("MemAvailable: 1024 kB\n")
    assert two.tick(capsys) == 0, two.output
    assert two.waits == [] and two.mutations() == []
    assert two.record()["state"] == two.record(LOW)["state"] == "parked-waiting"
    (line,) = two.logged(f"restore deferred {RIG}: ram")
    assert "MemAvailable 1 MB" in line
    assert two.logged(f"restore queued: {LOW} (next tick)")
    two.meminfo.write_text(f"MemAvailable: {32768 * 1024} kB\n")
    two.tick(capsys, after=120)
    assert two.calls("up ") == [up(RIG)]


def test_a_manual_restore_still_waits_for_memory(fleet, capsys) -> None:
    """P81: only the supervisor's restore is bounded to no wait."""
    fleet.parked()
    fleet.meminfo.write_text("MemAvailable: 1024 kB\n")
    assert fleet.tool.main(["restore", RIG]) == 75
    assert sum(fleet.waits) == 900


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
@pytest.mark.parametrize(("verb", "command"), [("park", "down"), ("restore", "up")])
def test_a_stop_signal_marks_the_cut_operation_failed_and_asks(
    fleet, capsys, verb: str, command: str, signum: int
) -> None:
    """P81 (ii): restore-failed and the one question row; the tick exits 1."""
    if verb == "restore":
        fleet.parked()
        fleet.set_queue(work())
    else:
        fleet.set_queue(question())
        fleet.tick(capsys)
        fleet.now += timedelta(seconds=900)
    real = fleet.tool.rig

    def rig(*argv, **kwargs):
        if argv[0] == command:
            os.kill(os.getpid(), signum)
        return real(*argv, **kwargs)

    fleet.monkeypatch.setattr(fleet.tool, "rig", rig)
    before = signal.getsignal(signum)
    assert fleet.tool.main(["supervise", "--once"]) == 1
    assert signal.getsignal(signum) == before
    record = fleet.record()
    assert record["state"] == "restore-failed"
    assert record["reason"] == f"{verb} was interrupted"
    (sent,) = fleet.creates()
    assert sent["--destination"] == ADVISOR and "needs the person" in sent["--summary"]
    assert fleet.logged(f"supervise - stopped by {signal.Signals(signum).name}")
    assert fleet.calls(f"{command} ") == []


def test_a_stop_signal_before_anything_changed_marks_nothing(fleet, capsys) -> None:
    """A park cut before `rig down` leaves the rig running, with no row."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    fleet.now += timedelta(seconds=900)
    real = fleet.tool.rig

    def rig(*argv, **kwargs):
        if argv[0] == "snapshot":
            os.kill(os.getpid(), signal.SIGTERM)
        return real(*argv, **kwargs)

    fleet.monkeypatch.setattr(fleet.tool, "rig", rig)
    assert fleet.tool.main(["supervise", "--once"]) == 1
    assert fleet.record()["state"] == "running" and fleet.creates() == []


@pytest.mark.parametrize(("verb", "step"), [("park", "observe"), ("restore", "send_lead")])
def test_an_error_that_cuts_an_operation_also_asks(fleet, capsys, verb: str, step: str) -> None:
    """A restore-failed record is never left without its question row."""
    if verb == "restore":
        fleet.parked()
        fleet.set_queue(work())
    else:
        fleet.set_queue(question())
        fleet.tick(capsys)
        fleet.now += timedelta(seconds=900)

    def broken(*_args):
        raise OSError("disk full")

    fleet.monkeypatch.setattr(fleet.tool, step, broken)
    assert fleet.tick(capsys) == 1
    record = fleet.record()
    assert (record["state"], record["reason"]) == ("restore-failed", f"{verb} was interrupted")
    (sent,) = fleet.creates()
    assert "needs the person" in sent["--summary"]
    (line,) = fleet.logged(f"{verb} {RIG} failed")
    assert "disk full" in line


def test_a_rig_listed_during_the_tick_gets_no_snapshot(fleet, capsys) -> None:
    """R183: the paused files are read again before the park starts."""
    listed = fleet.host.base / "paused.txt"
    listed.write_text("")
    fleet.config(extra=f"paused_files: [{listed}]\n")
    fleet.set_queue(question())
    fleet.tick(capsys)
    real = fleet.tool.boundary

    def boundary(*args):
        listed.write_text(f"{RIG}\n")
        return real(*args)

    fleet.monkeypatch.setattr(fleet.tool, "boundary", boundary)
    assert fleet.tick(capsys, after=900) == 0, fleet.output
    assert fleet.mutations() == [] and not fleet.calls("snapshot ")
    assert fleet.logged(f"skip {RIG}: its state changed during this tick")


# ------------------------------------------------------------------------------- one per tick


def test_one_park_per_tick_the_lowest_tier_first(two, capsys) -> None:
    """P77: the other is queued, and parked next tick if still eligible."""
    two.set_queue(question(), question("q-2", rig=LOW))
    two.hold(capsys)
    assert two.calls("down ") == [f"down {LOW} --json"]
    assert two.logged(f"park queued: {RIG} (next tick)")
    two.tick(capsys, after=120)
    assert two.calls("down ") == [f"down {LOW} --json", f"down {RIG} --json"]


def test_a_queued_rig_that_gets_work_is_not_parked(two, capsys) -> None:
    """P77: the next tick re-checks, and logs the reset."""
    two.set_queue(question(), question("q-2", rig=LOW))
    two.hold(capsys)
    two.set_queue(question(), question("q-2", rig=LOW), work())
    two.tick(capsys, after=120)
    assert two.calls("down ") == [f"down {LOW} --json"]
    assert two.logged(f"grace reset {RIG}: ")


def test_eligible_since_breaks_a_park_tie(two, capsys) -> None:
    """P77: within a tier, the earliest eligible first."""
    two.host.write_config(
        two.host.config.read_text().replace(f"[{RIG}, {LOW}]", f"[[{RIG}, {LOW}]]")
    )
    for name, minutes in ((RIG, 16), (LOW, 20)):
        two.put_record(
            name,
            state="running",
            eligible_since=stamp(START - timedelta(minutes=minutes)),
            eligible_checked=stamp(START),
        )
    two.set_queue(question(), question("q-2", rig=LOW))
    two.tick(capsys)
    assert two.calls("down ") == [f"down {LOW} --json"]


def test_a_tick_reads_the_rig_list_and_the_active_rows_once(two, capsys) -> None:
    """MR 1: one `rig ps --json` and one active list when nothing is parked or restored."""
    two.set_queue(question(), question("q-2", rig=LOW))
    two.tick(capsys)
    assert two.calls("ps --json") == ["ps --json"] and two.calls(ACTIVE) == [ACTIVE]


# ------------------------------------------------------------------------------- the verb


def test_disabled_logs_and_does_nothing(fleet, capsys) -> None:
    """MR 1: off by default."""
    fleet.config(enabled=False)
    fleet.set_queue(question())
    assert fleet.tick(capsys) == 0
    assert fleet.rig.calls == [] and fleet.record() == {}
    assert fleet.logged("autopark disabled")


def test_autopark_is_off_without_a_config(host, tool, capsys) -> None:
    """The default."""
    assert tool.main(["supervise", "--once"]) == 0
    assert "autopark disabled" in capsys.readouterr().out and host.rig.calls == []


@pytest.mark.parametrize("enabled", [True, False])
def test_dry_run_calls_no_mutating_verb(two, capsys, enabled: bool) -> None:
    """MR 4: every evaluation, only log lines and eligible_since, enabled or not."""
    two.config(enabled=enabled)
    two.parked(LOW)
    two.set_queue(question(), work(rig=LOW))
    before = two.record(LOW)
    two.hold(capsys, "--dry-run")
    print("\n".join(sorted(set(two.calls()))))
    print("\n".join(two.log()))
    reads = ("ps ", "queue list ")
    assert all(c.startswith(reads) or c == "specs ls --json" for c in two.calls())
    assert two.logged(f"would-park {RIG} waits_on=q-1")
    assert two.logged(f"would-restore {LOW} trigger=w-1")
    assert two.record(LOW) == before
    record = two.record()
    assert set(record) == {"schema", "state", "since", "eligible_since", "eligible_checked"}
    assert sorted(p.name for p in (two.host.state / "rigs").iterdir()) == [
        f"{RIG}.json",
        f"{LOW}.json",
    ]


def test_dry_run_logs_the_skip_reasons_and_the_queues(two, capsys) -> None:
    """MR 4 and the one-per-tick rules in a dry run."""
    two.parked(RIG)
    two.parked(LOW)
    two.set_queue(work(), work("w-2", rig=LOW))
    two.tick(capsys, "--dry-run")
    assert two.logged(f"would-restore {RIG} trigger=w-1")
    assert two.logged(f"restore queued: {LOW} (next tick)")
    assert two.mutations() == []


@pytest.mark.parametrize(
    "argv",
    [
        ["supervise", "--once"],
        ["supervise", "--once", "--dry-run"],
        ["supervisor", "install"],
        ["supervisor", "remove"],
    ],
)
def test_a_seat_of_a_mapped_rig_is_refused(fleet, capsys, argv: list) -> None:
    """R200."""
    fleet.monkeypatch.setenv("OPENRIG_SESSION_NAME", f"orch-lead@{RIG}")
    assert fleet.tool.main(argv) == 2
    assert "is a seat of the mapped rig" in capsys.readouterr().err
    assert fleet.rig.calls == [] and fleet.host.systemctl.calls == []
    assert not fleet.host.state.exists()


@pytest.mark.parametrize(
    "text",
    [
        "autopark: {enabled: true, grace_minutes: '90'}",
        "autopark: {enabled: true, max_parks_per_day: 0}",
        "autopark: {enabled: true}\npriority: hacf",
        "autopark: {enabled: true}\nignore_rigs: kernel",
        ": : :",
    ],
)
def test_a_config_the_supervisor_cannot_trust_is_refused(fleet, capsys, text: str) -> None:
    """A dropped key would fall back to a default that parks sooner."""
    fleet.host.write_config(f"projects: {{{RIG}: {fleet.roots[RIG]}}}\n{text}\n")
    assert fleet.tool.main(["supervise", "--once"]) == 2
    assert "refused: host.yml" in capsys.readouterr().err
    assert fleet.rig.calls == []


@pytest.mark.parametrize("dry", [[], ["--dry-run"]])
def test_newer_state_exits_75(fleet, capsys, dry: list) -> None:
    """MR 1: nothing read from `rig`, nothing written."""
    path = fleet.host.state / "supervisor.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema": "hacf.host/v2"}')
    assert fleet.tool.main(["supervise", "--once", *dry]) == 75
    assert fleet.rig.calls == [] and sorted(fleet.host.state.iterdir()) == [path]


def test_a_second_tick_at_the_same_time_exits_75(fleet, capsys) -> None:
    """Two ticks never park and restore side by side."""
    fleet.set_queue(question())
    with fleet.tool.supervise_lock():
        assert fleet.tool.main(["supervise", "--once"]) == 75
    assert fleet.rig.calls == []


def test_a_rig_another_operation_holds_is_skipped(fleet, capsys) -> None:
    """The per-rig lock of the engine's verbs."""
    fleet.set_queue(question())
    fleet.tick(capsys)
    with fleet.tool.rig_operation(RIG):
        assert fleet.tick(capsys, after=900) == 0
    assert fleet.mutations() == []
    assert fleet.logged(f"skip {RIG}: {RIG}: another operation")


def test_supervise_needs_once(capsys, tool) -> None:
    """There is no loop mode."""
    with pytest.raises(SystemExit) as stop:
        tool.main(["supervise"])
    assert stop.value.code == 2


def test_a_resume_or_pause_clears_the_grace(fleet) -> None:
    """The grace starts again after the person acted."""
    keys = {"eligible_since": stamp(START), "eligible_checked": stamp(START)}
    fleet.put_record(state="running", **keys)
    fleet.tool.mark_running(RIG)
    assert not keys.keys() & fleet.record().keys()
    fleet.put_record(state="running", **keys)
    assert fleet.tool.main(["pause", RIG, "--reason", "r"]) == 0
    assert not keys.keys() & fleet.record().keys()


# ------------------------------------------------------------------------------- supervisor

SERVICE = "hacf-host-supervise.service"
TIMER = "hacf-host-supervise.timer"


class Units:
    """Where install writes."""

    def __init__(self, host, tool) -> None:
        """The unit dir, the copy and the record under tmp_path."""
        self.dir = host.base / "xdg-config" / "systemd" / "user"
        self.service = self.dir / SERVICE
        self.timer = self.dir / TIMER
        self.bin = host.base / "xdg-data" / "hacf" / "host" / "bin"
        self.copy = self.bin / f"hacf-host-{getattr(tool, 'HACF_HOST_VERSION', '?')}.py"
        self.record = host.state / "supervisor.json"

    def timeout(self) -> int:
        """The installed TimeoutStartSec."""
        (value,) = re.findall(r"^TimeoutStartSec=(\d+)$", self.service.read_text(), re.M)
        return int(value)


@pytest.fixture
def units(host, tool) -> Units:
    """The install's paths."""
    return Units(host, tool)


def supervisor(tool, capsys, *argv: str) -> tuple[int, str]:
    """Run one `supervisor` action: its exit code and output."""
    code = tool.main(["supervisor", *argv])
    done = capsys.readouterr()
    return code, done.out + done.err


def test_the_host_version_is_the_installers(tool) -> None:
    """MR 5: the release bumps both together."""
    text = (REPO_ROOT / "HACF-install.sh").read_text(encoding="utf-8")
    assert re.findall(r'^HACF_VERSION="([^"]+)"', text, re.M) == [tool.HACF_HOST_VERSION]


@pytest.mark.parametrize("dry", [False, True])
def test_install_writes_the_copy_the_units_and_the_record(
    host, tool, units, capsys, dry: bool
) -> None:
    """MR 5."""
    flags = ["--dry-run-mode"] if dry else []
    code, out = supervisor(tool, capsys, "install", *flags)
    assert code == 0, out
    assert units.copy.read_bytes() == Path(tool.__file__).read_bytes()
    assert stat.S_IMODE(units.copy.stat().st_mode) == 0o644
    service = units.service.read_text()
    tail = " --dry-run" if dry else ""
    assert f'\nExecStart=/usr/bin/env python3 "{units.copy}" supervise --once{tail}\n' in service
    assert "\nType=oneshot\n" in service and "\nNice=10\n" in service
    timer = units.timer.read_text()
    assert "\nOnBootSec=2min\n" in timer and "\nOnUnitActiveSec=2min\n" in timer
    assert "\nWantedBy=timers.target\n" in timer
    assert host.systemctl.calls == [
        ["--user", "show-environment"],
        ["--user", "daemon-reload"],
        ["--user", "enable", "--now", TIMER],
    ]
    record = json.loads(units.record.read_text())
    assert record["version"] == tool.HACF_HOST_VERSION
    assert record["source"] == tool.__file__ and record["dry_run_mode"] is dry
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["installed_at"])
    assert host.rig.calls == [] and not host.config.exists()


def test_a_path_systemd_would_expand_is_escaped(host, tool, units, monkeypatch, capsys) -> None:
    """% and $ in the data dir stay themselves."""
    data = host.base / "da ta%h$x"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    assert supervisor(tool, capsys, "install")[0] == 0
    assert "da ta%%h$$x/hacf/host/bin/" in units.service.read_text()


@pytest.mark.parametrize("restore", [900, 1800])
def test_the_timeout_relation(host, tool, units, capsys, restore: int) -> None:
    """P75 a: TimeoutStartSec = restore_timeout_s + 360, above restore_timeout_s."""
    if restore != 900:
        host.write_config(f"autopark: {{restore_timeout_s: {restore}}}\n")
    assert supervisor(tool, capsys, "install")[0] == 0
    assert units.timeout() == restore + 360 > restore
    assert "below restore_timeout_s" not in supervisor(tool, capsys, "status")[1]
    host.write_config(f"autopark: {{restore_timeout_s: {restore + 1}}}\n")
    code, out = supervisor(tool, capsys, "status")
    assert code == 0
    assert "supervisor timeout below restore_timeout_s: re-run supervisor install" in out


@pytest.mark.parametrize("missing", [False, True])
def test_install_refuses_off_a_systemd_user_session(
    host, tool, units, monkeypatch, capsys, missing: bool
) -> None:
    """R195: Linux with a user session only; nothing is written."""
    if missing:
        monkeypatch.setenv("PATH", str(host.base / "empty"))
    else:
        host.systemctl.reply("show-environment", rc=1, stderr="Failed to connect to bus")
    code, out = supervisor(tool, capsys, "install")
    assert code == 2
    assert "supervisor needs a systemd user session (systemctl --user)" in out
    assert not units.dir.exists() and not units.bin.exists() and not host.state.exists()


@pytest.mark.parametrize("version", ["9.0.0", "0.6.70", "next", None])
def test_install_refuses_over_a_newer_record(host, tool, units, capsys, version) -> None:
    """R193: a version that is newer, or cannot be read, is never installed over."""
    host.state.mkdir()
    record = {"schema": "hacf.host/v1", "version": version}
    units.record.write_text(json.dumps(record))
    code, out = supervisor(tool, capsys, "install")
    assert code == 2 and "refused" in out
    assert not units.dir.exists() and not units.bin.exists()
    assert json.loads(units.record.read_text()) == record


def test_install_replaces_an_older_install(host, tool, units, capsys) -> None:
    """An older record is replaced."""
    host.state.mkdir()
    units.record.write_text(json.dumps({"schema": "hacf.host/v1", "version": "0.0.1", "x": 1}))
    assert supervisor(tool, capsys, "install")[0] == 0
    record = json.loads(units.record.read_text())
    assert record["version"] == tool.HACF_HOST_VERSION and record["x"] == 1


def test_a_failed_enable_records_nothing(host, tool, units, capsys) -> None:
    """The record says what is installed."""
    host.systemctl.reply("enable", rc=1, stderr="nope")
    code, out = supervisor(tool, capsys, "install")
    assert code == 1 and "nope" in out and not units.record.exists()


def test_remove_disables_and_removes(host, tool, units, capsys) -> None:
    """MR 5: the two units, the copy and the record."""
    assert supervisor(tool, capsys, "install")[0] == 0
    other = units.dir / "other.timer"
    other.write_text("keep")
    before = len(host.systemctl.calls)
    code, out = supervisor(tool, capsys, "remove")
    assert code == 0, out
    assert host.systemctl.calls[before:] == [
        ["--user", "show-environment"],
        ["--user", "disable", "--now", TIMER],
        ["--user", "daemon-reload"],
    ]
    assert not units.service.exists() and not units.timer.exists()
    assert not units.copy.exists() and not units.record.exists() and other.exists()
    assert supervisor(tool, capsys, "remove")[0] == 0  # nothing left to disable


def test_a_failed_disable_removes_nothing(host, tool, units, capsys) -> None:
    """A timer that still runs keeps its files."""
    assert supervisor(tool, capsys, "install")[0] == 0
    host.systemctl.reply("disable", rc=1, stderr="busy")
    assert supervisor(tool, capsys, "remove")[0] == 1
    assert units.service.exists() and units.copy.exists() and units.record.exists()


def test_status_shows_the_record_and_the_timer(host, tool, units, capsys) -> None:
    """MR 5: the record and `is-active`; no seat is refused."""
    code, out = supervisor(tool, capsys, "status")
    assert code == 0 and "not installed" in out
    assert supervisor(tool, capsys, "install", "--dry-run-mode")[0] == 0
    host.systemctl.reply("is-active", "active\n")
    code, out = supervisor(tool, capsys, "status")
    assert code == 0
    for word in (tool.HACF_HOST_VERSION, tool.__file__, '"dry_run_mode": true', "timer: active"):
        assert word in out, out
    assert ["--user", "is-active", TIMER] in host.systemctl.calls
    host.systemctl.reply("is-active", "inactive\n", rc=3)
    assert "timer: inactive" in supervisor(tool, capsys, "status")[1]


def environment(units: Units) -> dict:
    """The installed unit's Environment= lines."""
    found = re.findall(r'^Environment="([A-Z_]+)=(.*)"$', units.service.read_text(), re.M)
    return dict(found)


def shim(base: Path) -> Path:
    """A seat-bin dir holding a `rig` shim, as a seat's PATH has."""
    directory = base / "home" / ".openrig" / "run" / "seat-bin" / "n1"
    directory.mkdir(parents=True)
    (directory / "rig").write_text("#!/bin/sh\nexit 1\n")
    (directory / "rig").chmod(0o755)
    return directory


def test_install_writes_the_environment_the_ticks_need(
    host, tool, units, monkeypatch, capsys
) -> None:
    """P82: the PATH without seat-bin entries, and the host variables that are set."""
    path = os.environ["PATH"]
    seat = shim(host.base)
    link = host.base / "linked-seat-bin"
    link.symlink_to(seat)
    monkeypatch.setenv("PATH", f"{seat}{os.pathsep}{link}{os.pathsep}relative::{path}")
    monkeypatch.delenv("HACF_HEAVY_LOCK_PATH")
    assert supervisor(tool, capsys, "install")[0] == 0
    written = environment(units)
    kept = [p for p in path.split(os.pathsep) if os.path.isabs(p) and "seat-bin" not in p]
    assert written.pop("PATH").split(os.pathsep) == kept
    assert written == {
        name: os.environ[name]
        for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "HACF_HOST_STATE_DIR", "HACF_HOST_RIG")
    }


def test_install_resolves_rig_on_that_path(host, tool, units, monkeypatch, capsys) -> None:
    """P82: with no HACF_HOST_RIG, `rig` from the unit's PATH; status shows both."""
    monkeypatch.delenv("HACF_HOST_RIG")
    path = f"{host.rig.base}{os.pathsep}{host.systemctl.base}"
    monkeypatch.setenv("PATH", f"{shim(host.base)}{os.pathsep}{path}")
    assert supervisor(tool, capsys, "install")[0] == 0
    assert "HACF_HOST_RIG" not in environment(units)
    out = supervisor(tool, capsys, "status")[1]
    assert f"unit PATH: {path}\n" in out and f"unit rig: {host.rig.path}\n" in out
    host.rig.path.unlink()
    assert "unit rig: not found on the unit's PATH" in supervisor(tool, capsys, "status")[1]


@pytest.mark.parametrize("named", [None, "shim", "absent", "bin/rig"])
def test_install_refuses_when_rig_does_not_resolve(
    host, tool, units, monkeypatch, capsys, named: str | None
) -> None:
    """P82: exit 2, nothing written: no rig, only a seat-bin shim, or an unusable name."""
    seat = shim(host.base)
    monkeypatch.setenv("PATH", f"{seat}{os.pathsep}{host.systemctl.base}")
    if named is None:
        monkeypatch.delenv("HACF_HOST_RIG")
    else:
        value = {"shim": str(seat / "rig"), "absent": str(host.base / "no-rig")}
        monkeypatch.setenv("HACF_HOST_RIG", value.get(named, named))
    code, out = supervisor(tool, capsys, "install")
    assert code == 2 and "rig" in out, out
    assert not units.dir.exists() and not units.bin.exists() and not host.state.exists()


def test_status_shows_the_last_ticks_queued_lines(two, capsys) -> None:
    """P76, P77: the same lines as the log, for the last tick only."""
    two.parked(RIG)
    two.parked(LOW)
    two.set_queue(work(), work("w-2", rig=LOW))
    two.tick(capsys)
    assert f"restore queued: {LOW} (next tick)" in supervisor(two.tool, capsys, "status")[1]
    two.tick(capsys, after=120)
    assert "queued" not in supervisor(two.tool, capsys, "status")[1]
    for name in (RIG, LOW, RIG):  # both are up again in every canned reply
        two.seats(name)
    two.set_queue(question(), question("q-2", rig=LOW))
    two.hold(capsys)
    assert f"park queued: {RIG} (next tick)" in supervisor(two.tool, capsys, "status")[1]
