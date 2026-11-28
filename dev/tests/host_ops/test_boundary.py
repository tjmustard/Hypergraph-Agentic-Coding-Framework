"""Tests of hacf-host.py's slice boundary and resume point (OPR.99.0.16.2)."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from .conftest import TOOL

RIG = "alpha"
PS = f"ps --nodes --rig {RIG} --full --json"
BUSY_ROWS = "queue list -A --state in-progress --json --limit 10000"
ACTIVE_ROWS = "queue list -A --state pending,in-progress,blocked --json --limit 10000"
SKEW_LINE = "host state is schema v2; this hacf-host is v1: newer HACF owns it; waiting"
AS_ROOT = os.geteuid() == 0


def node(member: str = "lead", activity: dict | None = None, **fields: object) -> dict:
    """One seat of `rig ps --nodes --full --json`, at rest unless told otherwise."""
    return {
        "rigName": RIG,
        "logicalId": f"orch.{member}",
        "canonicalSessionName": f"orch-{member}@{RIG}",
        "lifecycleState": "running",
        "needsAttention": False,
        "arbitratedNeedsInput": False,
        "agentActivity": {"state": "idle", "stale": False, **(activity or {})},
        **fields,
    }


def row(qitem: str, source: str, destination: str, state: str = "pending", **fields) -> dict:
    """One row of `rig queue list --json`."""
    return {
        "qitemId": qitem,
        "state": state,
        "sourceSession": source,
        "destinationSession": destination,
        "tags": None,
        "summary": f"summary of {qitem}",
        **fields,
    }


class Rig:
    """A mapped rig on the isolated host, at a boundary until a test changes something."""

    def __init__(self, host) -> None:
        """Map the rig to a fixture project and let every check pass."""
        self.host = host
        self.root = host.project("one")
        host.write_config(f"projects: {{{RIG}: {self.root}}}\n")
        self.nodes(node("lead"), node("dev"))
        host.rig.reply(BUSY_ROWS, [])
        self.slice("01-first", "audited")
        self.slice("02-second", "compiled")
        self.slice("03-third", "superseded", mission="later")

    def nodes(self, *nodes: dict) -> None:
        """Answer `rig ps --nodes` with these seats."""
        self.host.rig.reply(PS, list(nodes))

    def slice(self, name: str, status: str | None = None, *, mission: str = "now", text=None):
        """Write a slice SPEC.md with this hacf_status (or this whole text)."""
        directory = self.root / "missions" / mission / "slices" / name
        directory.mkdir(parents=True, exist_ok=True)
        if text is None:
            key = "" if status is None else f"hacf_status: {status}\n"
            text = f"---\nid: {name}\n{key}---\n\n# MiniPRD: {name}\n"
        (directory / "SPEC.md").write_text(text, encoding="utf-8")
        return directory

    def ticket(self, name: str, data: object) -> Path:
        """Write `queue/<name>.json` in the state dir; a str is written as it is."""
        path = self.host.state / "queue" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def holder(self, data: object) -> Path:
        """Write the heavy lock's holder record; a str is written as it is."""
        path = Path(os.environ["HACF_HEAVY_LOCK_PATH"]).with_suffix(".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path


@pytest.fixture
def rig(host) -> Rig:
    """A mapped rig at a boundary."""
    return Rig(host)


def cli(*args: str) -> subprocess.CompletedProcess:
    """Run the script as a user does, with the isolated environment."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
        check=False,
    )


def boundary() -> tuple[int, dict]:
    """`boundary <rig> --json`: the exit code and the report."""
    done = cli("boundary", RIG, "--json")
    assert done.returncode in (0, 1), done.stderr
    return done.returncode, json.loads(done.stdout)


def only_failure(check: str) -> str:
    """The reason of `check`, asserting it is the one check that fails and exit is 1."""
    rc, report = boundary()
    failed = [name for name, result in report["checks"].items() if not result["ok"]]
    assert (rc, report["at_boundary"], failed) == (1, False, [check]), report
    return report["checks"][check]["reason"]


def dead_pid() -> int:
    """The pid of a process that has ended and been reaped."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def ticket(pid: int | None = None, rig_name: object = RIG) -> dict:
    """A scheduler ticket (SuperPRD §5.4)."""
    return {
        "schema": "hacf.host/v1",
        "job": "pytest",
        "rig": rig_name,
        "pid": os.getpid() if pid is None else pid,
        "token": "t",
    }


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` and its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------------- at a boundary


def test_all_checks_ok(rig) -> None:
    """Exit 0, every check ok, and nothing written."""
    before = tree(rig.host.base / "projects")
    rc, report = boundary()
    assert rc == 0, report
    assert sorted(report) == ["at_boundary", "checks", "rig"]
    assert (report["rig"], report["at_boundary"]) == (RIG, True)
    assert list(report["checks"]) == ["B1", "B2", "B3", "B4"]
    for result in report["checks"].values():
        assert sorted(result) == ["ok", "reason"]
        assert result["ok"] is True and result["reason"]
    assert not rig.host.state.exists()
    assert tree(rig.host.base / "projects") == before
    # Read-only `rig` calls, and exactly these.
    assert [" ".join(c) for c in rig.host.rig.calls] == [PS, BUSY_ROWS]


def test_text_output(rig) -> None:
    """One line per check, then the verdict."""
    done = cli("boundary", RIG)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split(":")[0] for line in lines] == ["B1", "B2", "B3", "B4", "at boundary"]
    assert all(line.startswith(f"B{i}: ok - ") for i, line in enumerate(lines[:4], start=1))
    assert lines[4] == "at boundary: yes"
    rig.slice("01-first", "executing")
    done = cli("boundary", RIG)
    assert done.returncode == 1
    lines = done.stdout.splitlines()
    assert lines[2] == "B3: no - missions/now/slices/01-first: hacf_status executing"
    assert lines[4] == "at boundary: no"


def test_each_call_reads_afresh(rig) -> None:
    """A change between two runs is seen by the second."""
    assert boundary()[0] == 0
    rig.nodes(node(activity={"state": "running"}))
    assert boundary()[0] == 1


# --------------------------------------------------------------------------------------------- B1


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"activity": {"state": "running"}}, "agentActivity.state running"),
        ({"activity": {"state": "needs_input"}}, "agentActivity.state needs_input"),
        ({"activity": {"state": "unknown"}}, "agentActivity.state unknown"),
        ({"activity": {"stale": True}}, "agentActivity.stale true"),
        ({"activity": {"stale": None}}, "agentActivity.stale null"),
        ({"needsAttention": True}, "needsAttention true"),
        ({"needsAttention": None}, "needsAttention null"),
        ({"arbitratedNeedsInput": True}, "arbitratedNeedsInput true"),
        ({"arbitratedNeedsInput": None}, "arbitratedNeedsInput null"),
        ({"arbitratedNeedsInput": 0}, "arbitratedNeedsInput 0"),
        ({"lifecycleState": "detached"}, "lifecycleState detached"),
        ({"lifecycleState": "attention_required"}, "lifecycleState attention_required"),
        ({"agentActivity": None}, "agentActivity missing"),
        ({"agentActivity": {"stale": False}}, "agentActivity.state missing"),
    ],
)
def test_b1_one_seat_not_at_rest(rig, changes: dict, reason: str) -> None:
    """One seat that is not at rest fails B1 alone, and is named."""
    rig.nodes(node("dev"), node("lead", **changes))
    assert only_failure("B1") == f"orch-lead@{RIG}: {reason}"


@pytest.mark.parametrize("key", ["needsAttention", "arbitratedNeedsInput", "lifecycleState"])
def test_b1_a_missing_field(rig, key: str) -> None:
    """A field `rig` does not give is unknown, not at rest."""
    seat = node()
    del seat[key]
    rig.nodes(seat)
    assert only_failure("B1") == f"orch-lead@{RIG}: {key} missing"


def test_b1_a_reading_without_stale_is_fresh(rig) -> None:
    """`stale` is only set on a stale reading (OpenRig types it optional)."""
    seat = node()
    del seat["agentActivity"]["stale"]
    rig.nodes(seat)
    assert boundary()[0] == 0


def test_b1_names_every_seat_not_at_rest(rig) -> None:
    """Each seat is reported; one without a session name goes by its logical id."""
    rig.nodes(
        node("lead", activity={"state": "running"}),
        node("dev", canonicalSessionName=None, needsAttention=True),
    )
    assert only_failure("B1") == (
        f"orch-lead@{RIG}: agentActivity.state running; orch.dev: needsAttention true"
    )


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"stdout": "[]"}, "rig ps lists no node"),
        ({"stdout": "[]", "rc": 3, "stderr": "no daemon\n"}, "exit 3: no daemon"),
        ({"stdout": "{broken"}, "not a JSON list of objects"),
        ({"stdout": '{"items": []}'}, "not a JSON list of objects"),
        ({"stdout": "[1]"}, "not a JSON list of objects"),
    ],
)
def test_b1_unknown(rig, reply: dict, reason: str) -> None:
    """No seat, a failed call and a parse error are all not at a boundary."""
    rig.host.rig.reply(PS, **reply)
    assert reason in only_failure("B1")


def test_b1_a_missing_rig_binary(rig, monkeypatch) -> None:
    """Without `rig`, both live checks are unknown."""
    monkeypatch.setenv("HACF_HOST_RIG", str(rig.host.base / "no-such-rig"))
    rc, report = boundary()
    assert rc == 1
    assert [report["checks"][c]["ok"] for c in ("B1", "B2", "B3", "B4")] == [
        False,
        False,
        True,
        True,
    ]


# --------------------------------------------------------------------------------------------- B2


def test_b2_an_in_progress_row_of_the_rig(rig) -> None:
    """The row is named."""
    rig.host.rig.reply(BUSY_ROWS, [row("qitem-1", "x@other", f"orch-dev@{RIG}", "in-progress")])
    assert only_failure("B2") == "in-progress: qitem-1"


def test_b2_rows_of_other_rigs_do_not_count(rig) -> None:
    """Only a destination ending in @<rig> counts; a row the rig sent does not."""
    rig.host.rig.reply(
        BUSY_ROWS,
        [
            row("qitem-1", f"orch-lead@{RIG}", "x@other", "in-progress"),
            row("qitem-2", "x@other", f"orch-lead@not-{RIG}", "in-progress"),
            row("qitem-3", "x@other", f"orch-lead@{RIG}-two", "in-progress"),
        ],
    )
    assert boundary()[0] == 0


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"stdout": "[]", "rc": 1, "stderr": "daemon down\n"}, "exit 1: daemon down"),
        ({"stdout": "nope"}, "not a JSON list of objects"),
        ({"stdout": '[{"qitemId": "q"}]'}, "a row has no destinationSession"),
    ],
)
def test_b2_unknown(rig, reply: dict, reason: str) -> None:
    """A failed or unreadable queue list is unknown."""
    rig.host.rig.reply(BUSY_ROWS, **reply)
    assert reason in only_failure("B2")


def test_b2_a_full_page_is_unknown(rig) -> None:
    """A list that reaches --limit may have left a row out."""
    rig.host.rig.reply(BUSY_ROWS, [row(f"q{i}", "a@o", "b@o") for i in range(10000)])
    assert "--limit 10000" in only_failure("B2")


# --------------------------------------------------------------------------------------------- B3


def test_the_at_rest_set_is_pinned_to_hyper_spec(tool) -> None:
    """R197: at rest is exactly three of hyper_spec.py's slice statuses."""
    source = (REPO_ROOT / ".agents" / "scripts" / "hyper_spec.py").read_text(encoding="utf-8")
    [statuses] = [
        n.value
        for n in ast.parse(source).body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "HACF_STATUSES"
    ]
    slice_values = next(
        ast.literal_eval(v) for k, v in zip(statuses.keys, statuses.values) if k.id == "SLICE"
    )
    assert tool.AT_REST == {"compiled", "audited", "superseded"}
    assert set(slice_values) - tool.AT_REST == {"executing", "executed", "audit_failed", "blocked"}
    assert re.search(r"hyper_spec\.py:103-114", TOOL.read_text(encoding="utf-8"))


@pytest.mark.parametrize("status", ["executing", "executed", "audit_failed", "blocked", "done"])
def test_b3_a_slice_in_flight(rig, status: str) -> None:
    """Every other value is in flight, named by its slice dir."""
    rig.slice("02-second", status)
    assert only_failure("B3") == f"missions/now/slices/02-second: hacf_status {status}"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("---\nid: x\n---\n", "no hacf_status"),
        ("---\nhacf_status:\n---\n", "hacf_status is not a string"),
        ("---\nhacf_status: [compiled]\n---\n", "hacf_status is not a string"),
        ("---\nhacf_status: [compiled\n---\n", "front matter is not valid YAML"),
        ("---\n- compiled\n---\n", "front matter is not a mapping"),
        ("---\n\n---\n", "front matter is not a mapping"),
        ("# no front matter\n", "no front matter"),
        ("# title\n---\nhacf_status: compiled\n---\n", "no front matter"),
        ("---\nhacf_status: compiled\n", "no front matter"),
        ("", "no front matter"),
    ],
)
def test_b3_an_unparsable_spec(rig, text: str, reason: str) -> None:
    """A missing key or an unparsable file is in flight."""
    rig.slice("02-second", text=text)
    assert only_failure("B3") == f"missions/now/slices/02-second: {reason}"


def test_b3_a_spec_that_is_not_utf8(rig) -> None:
    """An unreadable file is in flight."""
    directory = rig.slice("02-second", "compiled")
    (directory / "SPEC.md").write_bytes(b"---\nhacf_status: compiled\n---\n\xff\xfe")
    assert only_failure("B3").startswith("missions/now/slices/02-second: SPEC.md cannot be read")


@pytest.mark.parametrize("kind", ["directory", "dangling link", "fifo"])
def test_b3_a_spec_that_is_not_a_file(rig, kind: str) -> None:
    """Something else under the name SPEC.md is in flight, and is never opened."""
    spec = rig.slice("02-second", "compiled") / "SPEC.md"
    spec.unlink()
    if kind == "directory":
        spec.mkdir()
    elif kind == "fifo":
        os.mkfifo(spec)
    else:
        spec.symlink_to(spec.parent / "gone")
    assert only_failure("B3").startswith("missions/now/slices/02-second: SPEC.md ")


@pytest.mark.skipif(AS_ROOT, reason="root reads a mode 000 path")
@pytest.mark.parametrize(
    "locked",
    [
        "missions/now/slices/02-second/SPEC.md",
        "missions/now/slices/02-second",
        "missions/now/slices",
        "missions/now",
    ],
)
def test_b3_an_unreadable_path(rig, locked: str) -> None:
    """A path that cannot be read is never skipped."""
    path = rig.root / locked
    mode = path.stat().st_mode
    path.chmod(0)
    try:
        assert "missions/now" in only_failure("B3")
    finally:
        path.chmod(mode)


@pytest.mark.parametrize("link", ["missions/gone", "missions/now/slices/gone"])
def test_b3_a_dangling_link(rig, link: str) -> None:
    """A mission or slice that links nowhere is unknown."""
    (rig.root / link).symlink_to(rig.root / "nowhere")
    assert f"{link}: " in only_failure("B3")


def test_b3_what_holds_no_spec_is_not_a_slice(rig) -> None:
    """Loose files, a mission without slices and a slice dir without a SPEC.md are passed over."""
    (rig.root / "missions" / "README.md").write_text("x")
    (rig.root / "missions" / "empty").mkdir()
    (rig.root / "missions" / "now" / "slices" / "notes.md").write_text("x")
    (rig.root / "missions" / "now" / "slices" / "04-unwritten").mkdir()
    assert boundary()[0] == 0


def test_b3_no_missions_dir_is_unknown(rig) -> None:
    """R185: never "nothing in flight"."""
    root = rig.host.base / "projects" / "bare"
    root.mkdir()
    rig.host.write_config(f"projects: {{{RIG}: {root}}}\n")
    assert only_failure("B3") == f"unknown: {root}/missions cannot be read: no such file"


@pytest.mark.skipif(AS_ROOT, reason="root reads a mode 000 path")
def test_b3_an_unreadable_missions_dir_is_unknown(rig) -> None:
    """The same when it exists and cannot be listed."""
    missions = rig.root / "missions"
    missions.chmod(0)
    try:
        assert only_failure("B3").startswith(f"unknown: {missions} cannot be read")
    finally:
        missions.chmod(0o755)


def test_b3_a_relative_root_is_unknown(rig, monkeypatch) -> None:
    """A bound root is never read against the current directory."""
    rig.host.write_config(f"projects: {{{RIG}: one}}\n")
    monkeypatch.chdir(rig.root.parent)
    assert only_failure("B3") == "unknown: the project root one is not absolute"


# --------------------------------------------------------------------------------------------- B4


def test_b4_a_ticket_of_the_rig(rig) -> None:
    """A live ticket carrying the rig fails B4."""
    rig.ticket("t1", ticket())
    assert only_failure("B4") == f"queued job pytest (pid {os.getpid()}, queue/t1.json)"


@pytest.mark.parametrize("rig_name", ["other", None])
def test_b4_another_rigs_ticket(rig, rig_name) -> None:
    """A ticket of another rig, or of none, does not count."""
    rig.ticket("t1", ticket(rig_name=rig_name))
    assert boundary()[0] == 0


def test_b4_a_dead_ticket(rig) -> None:
    """A ticket whose process is gone is not queued."""
    rig.ticket("t1", ticket(pid=dead_pid()))
    assert boundary()[0] == 0


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ("{broken", "unreadable"),
        ("[]", "not a JSON object"),
        ({"rig": RIG}, "not a hacf.host/v1 ticket"),
        ({"schema": "hacf.host/v2", "rig": "other"}, "newer"),
        (ticket(rig_name=[RIG]), "rig is not a name"),
        ({**ticket(), "pid": "7"}, "no usable pid"),
        ({**ticket(), "pid": 0}, "no usable pid"),
    ],
)
def test_b4_a_ticket_it_cannot_judge(rig, data, reason: str) -> None:
    """Unknown is never at rest."""
    rig.ticket("t1", data)
    assert reason in only_failure("B4")


def test_b4_newer_state_anywhere_is_unknown(rig) -> None:
    """A newer HACF may keep its tickets elsewhere."""
    path = rig.host.state / "supervisor.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema": "hacf.host/v2"}')
    assert "schema v2" in only_failure("B4")


@pytest.mark.skipif(AS_ROOT, reason="root reads a mode 000 path")
def test_b4_an_unreadable_queue(rig) -> None:
    """A queue that cannot be listed is unknown."""
    queue = rig.ticket("t1", ticket(rig_name="other")).parent
    queue.chmod(0)
    try:
        assert only_failure("B4").startswith("unknown: the ticket queue cannot be read")
    finally:
        queue.chmod(0o755)


def holder(**fields: object) -> dict:
    """The holder record host_gate writes, with slice 03's `rig`."""
    return {"schema": "hacf.heavy-lock/v1", "job": "check", "pid": os.getpid(), **fields}


def test_b4_the_rig_holds_the_heavy_lock(rig) -> None:
    """A live holder record naming the rig fails B4."""
    rig.holder(holder(rig=RIG))
    assert only_failure("B4") == f"holds the heavy lock: job check (pid {os.getpid()})"


@pytest.mark.parametrize("fields", [{}, {"rig": None}, {"rig": "other"}])
def test_b4_a_holder_that_is_not_the_rig(rig, fields: dict) -> None:
    """An old client's record names no rig and is attributed to none."""
    rig.holder(holder(**fields))
    assert boundary()[0] == 0


def test_b4_a_stale_holder_record(rig) -> None:
    """A record whose process is gone holds nothing."""
    rig.holder(holder(rig=RIG, pid=dead_pid()))
    assert boundary()[0] == 0


@pytest.mark.parametrize(
    "data", ["{broken", "[]", holder(rig=7), holder(rig=RIG, pid="x"), holder(rig=RIG, pid=None)]
)
def test_b4_a_holder_record_it_cannot_judge(rig, data) -> None:
    """Unknown is never at rest."""
    rig.holder(data)
    assert only_failure("B4").startswith("unknown: ")


def test_the_lock_path_is_host_gates(tool, tmp_path: Path) -> None:
    """R190: the same file host_gate locks."""
    assert tool.heavy_lock_path({"HACF_HEAVY_LOCK_PATH": "/x/l.lock"}) == Path("/x/l.lock")
    assert tool.heavy_lock_path({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path(
        "/s/hacf/heavy-job.lock"
    )
    for env in ({"HOME": "/h"}, {"HOME": "/h", "XDG_STATE_HOME": "relative"}):
        assert tool.heavy_lock_path(env) == Path("/h/.local/state/hacf/heavy-job.lock")


# ----------------------------------------------------------------------------------------- usage


@pytest.mark.parametrize(
    "config", ["", "projects: {other: /tmp}\n", "ignore_rigs: [alpha]\n", ": not yaml: [\n"]
)
def test_an_unmapped_rig_is_refused(host, config: str) -> None:
    """Exit 2 with the line, and no call and no write."""
    host.write_config(config)
    done = cli("boundary", RIG)
    assert done.returncode == 2
    assert f"{RIG} is not mapped in host.yml projects" in done.stderr.splitlines()
    assert done.stdout == ""
    assert host.rig.calls == []
    assert not host.state.exists()


def test_an_ignored_rig_is_refused_even_when_mapped(rig) -> None:
    """`ignore_rigs` wins over a binding."""
    rig.host.write_config(f"projects: {{{RIG}: {rig.root}}}\nignore_rigs: [{RIG}]\n")
    done = cli("boundary", RIG)
    assert done.returncode == 2
    assert f"{RIG} is not mapped in host.yml projects" in done.stderr.splitlines()


@pytest.mark.parametrize("argv", [["boundary"], ["boundary", "a", "b"], ["boundary", "a", "--x"]])
def test_usage_error(rig, argv: list[str]) -> None:
    """Exactly one rig."""
    done = cli(*argv)
    assert done.returncode == 2
    assert "not in this build" not in done.stderr
    assert rig.host.rig.calls == []


# ---------------------------------------------------------------------------------- resume point


def git(root: Path, *args: str) -> str:
    """Run git in `root` with no user or system config."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()


def test_resume_point(tool, rig, monkeypatch) -> None:
    """The time, root, HEAD, slices, the rig's active rows and the four checks."""
    git(rig.root, "init", "-q")
    git(rig.root, "commit", "-q", "--allow-empty", "-m", "base")
    head = git(rig.root, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(REPO_ROOT / ".git"))  # -C decides, not the caller's env
    rig.slice("04-open", "blocked")
    rig.slice("05-bare", None)
    rig.host.rig.reply(
        ACTIVE_ROWS,
        [
            row("qitem-to", "x@other", f"orch-dev@{RIG}", "blocked", tags=["hacf:update"]),
            row(
                "qitem-from",
                f"orch-lead@{RIG}",
                "person@external",
                tags=["a", "b"],
                summary="two\nlines",
            ),
            row("qitem-else", "x@other", "y@other"),
        ],
    )
    path = tool.write_resume_point(RIG)
    assert path == rig.host.state / "rigs" / f"{RIG}.resume.md"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == f"# Resume point: {RIG}"
    assert "- schema: hacf.host/v1" in lines
    assert re.search(r"^- written: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", text, re.M)
    assert f"- project: {rig.root}" in lines
    assert f"- HEAD: {head}" in lines
    for expected in (
        "- at boundary: no",
        "- B1: ok - 2 seats idle",
        "- missions/later/slices/03-third: superseded (at rest)",
        "- missions/now/slices/01-first: audited (at rest)",
        "- missions/now/slices/02-second: compiled (at rest)",
        "- missions/now/slices/04-open: blocked (in flight)",
        "- missions/now/slices/05-bare: unknown, no hacf_status (in flight)",
        f"- qitem-to (blocked) x@other → orch-dev@{RIG}: summary of qitem-to [tags: hacf:update]",
        f"- qitem-from (pending) orch-lead@{RIG} → person@external: two lines [tags: a, b]",
    ):
        assert expected in lines
    assert any(line.startswith("- B3: no - ") for line in lines)
    assert "qitem-else" not in text
    calls = [" ".join(c) for c in rig.host.rig.calls]
    assert calls.count(ACTIVE_ROWS) == 1
    assert set(calls) == {PS, BUSY_ROWS, ACTIVE_ROWS}
    # Only the resume point (and the lock every state write takes) is in the state dir.
    assert sorted(tree(rig.host.state)) == [f"rigs/{RIG}.resume.md", "state.lock"]


def test_resume_point_marks_what_is_unknown(tool, rig) -> None:
    """No git, a failed queue list and no missions/ are said, never left out."""
    root = rig.host.base / "projects" / "bare"
    root.mkdir()
    rig.host.write_config(f"projects: {{{RIG}: {root}}}\n")
    rig.host.rig.reply(ACTIVE_ROWS, "", rc=1, stderr="daemon down\n")
    lines = tool.write_resume_point(RIG).read_text(encoding="utf-8").splitlines()
    assert "- HEAD: unknown" in lines
    assert f"- unknown: {root}/missions cannot be read: no such file" in lines
    assert "- unknown: rig queue list failed: exit 1: daemon down" in lines


def test_resume_point_with_nothing_to_list(tool, host) -> None:
    """An empty list reads `none`."""
    root = host.project("empty")
    host.write_config(f"projects: {{{RIG}: {root}}}\n")
    host.rig.reply(PS, [node()])
    host.rig.reply(BUSY_ROWS, [])
    host.rig.reply(ACTIVE_ROWS, [])
    text = tool.write_resume_point(RIG).read_text(encoding="utf-8")
    assert "- at boundary: yes" in text.splitlines()
    assert text.count("\n- none\n") == 2


def test_resume_point_of_a_full_page_says_so(tool, rig) -> None:
    """A list that reaches --limit is marked as possibly incomplete."""
    rows = [row(f"q{i}", "a@o", "b@o") for i in range(9999)]
    rig.host.rig.reply(ACTIVE_ROWS, [*rows, row("qitem-mine", "a@o", f"orch-dev@{RIG}")])
    text = tool.write_resume_point(RIG).read_text(encoding="utf-8")
    assert "qitem-mine" in text
    assert "--limit 10000" in text


def test_resume_point_replaces_the_last_one(tool, rig) -> None:
    """The second write replaces the first and leaves no temp file."""
    rig.host.rig.reply(ACTIVE_ROWS, [row("qitem-old", "a@o", f"orch-dev@{RIG}")])
    tool.write_resume_point(RIG)
    rig.host.rig.reply(ACTIVE_ROWS, [])
    path = tool.write_resume_point(RIG)
    assert "qitem-old" not in path.read_text(encoding="utf-8")
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("name", ["beta", "../x", "a/b", ""])
def test_resume_point_refuses_a_rig_it_may_not_write(tool, rig, name: str) -> None:
    """An unmapped rig, or a name that leaves the state dir, writes nothing."""
    if name != "beta":
        rig.host.write_config(f"projects: {{'{name}': {rig.root}}}\n")
    with pytest.raises(tool.StateError):
        tool.write_resume_point(name)
    assert not rig.host.state.exists()


def test_resume_point_under_newer_state_exits_75(tool, rig, monkeypatch, capsys) -> None:
    """The real write under newer state writes nothing; through `main` it is exit 75."""
    rig.host.rig.reply(ACTIVE_ROWS, [])
    newer = rig.host.state / "rigs" / "a.json"
    newer.parent.mkdir(parents=True)
    newer.write_text('{"schema": "hacf.host/v2"}')
    before = tree(rig.host.state)
    with pytest.raises(tool.NewerSchema):
        tool.write_resume_point(RIG)
    # A stand-in handler takes the place of `resume`, so only the writer is judged.
    monkeypatch.setitem(tool.HANDLERS, "resume", lambda args: tool.write_resume_point(RIG))
    assert tool.main(["resume", RIG]) == 75
    assert capsys.readouterr().err == SKEW_LINE + "\n"
    assert tree(rig.host.state) == before
