"""The watcher's rows always reach someone (OPR.99.0.13.2).

The outbox, the per-stream latches, the run budget, the tail, --row-check, the install's row-path
proof and the failure notification. The sandboxes are test_openrig_watch's and
test_openrig_watch_install's: offline, tmp dirs only, stub tools on PATH. No test reaches a real
rig, npm, systemd, notifier or session bus.
"""

from __future__ import annotations

import configparser
import json
import signal
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_openrig_watch import (
    BROKE,
    CAND,
    DESTINATION,
    WATCH,
    Watch,
    _contract_integrities,
    _summary,
    _write,
    oc,
)
from test_openrig_watch_install import SERVICE, Host, _install_args

FAILED_UNIT = REPO_ROOT / "dev" / "systemd" / "hacf-openrig-watch-failed.service"
REQUEST_ID = "qitem-20261126010203-0a1b2c3d"
REVIEW = {"verdict": "REVIEW", "rc": 3}
SETUP = {"verdict": "SETUP-ERROR", "rc": oc.VERDICT_EXIT["SETUP-ERROR"]}
FAIL = {"verdict": "FAIL", "rc": 1}

# A rig that prints the request id before "sending", fails when rig.rc says so, and can hang
# before it records anything (a send that never happened).
RIG = r"""#!/usr/bin/env python3
import json, os, sys, time
stub = os.environ["STUB_DIR"]
argv = sys.argv[1:]
if argv == ["--version"]:
    print("rig 9.9.9-stub")
    sys.exit(0)
if os.path.exists(os.path.join(stub, "rig.hang")):
    open(os.path.join(stub, "rig.hanging"), "w").write(str(os.getpid()))
    time.sleep(60)
body = open(argv[argv.index("--body-file") + 1]).read()
with open(os.path.join(stub, "rig.log"), "a") as h:
    h.write(json.dumps({"argv": argv, "body": body}) + "\n")
rc_file = os.path.join(stub, "rig.rc")
rc = int(open(rc_file).read()) if os.path.exists(rc_file) else 0
for n in range(1, 7):
    print(f"rig line {n}")
print("Queue create request ID: REQUEST_ID (not proof of persistence)")
if rc:
    print("error: the daemon refused")
sys.exit(rc)
""".replace("REQUEST_ID (", REQUEST_ID + " (")

# A notifier for --failure-notify: records its arguments, exits as fail.rc says.
FAILURE_NOTIFIER = r"""#!/usr/bin/env python3
import json, os, sys
stub = os.environ["STUB_DIR"]
with open(os.path.join(stub, "failure.log"), "a") as h:
    h.write(json.dumps(sys.argv[1:]) + "\n")
rc = os.path.join(stub, "fail.rc")
sys.exit(int(open(rc).read()) if os.path.exists(rc) else 0)
"""


@pytest.fixture
def w(tmp_path: Path) -> Watch:
    """A watcher sandbox whose rig prints a request id."""
    box = Watch(tmp_path)
    _write(box.rig, RIG)
    return box


def _refuse(w: Watch, refuse: bool = True) -> None:
    if refuse:
        (w.stub / "rig.rc").write_text("1", encoding="utf-8")
    else:
        (w.stub / "rig.rc").unlink(missing_ok=True)


def _entries(w: Watch) -> list[dict]:
    """The outbox entries, oldest first."""
    return [json.loads(p.read_text()) for p in sorted((w.state / "outbox").glob("*.json"))]


def _arg(row: dict, flag: str) -> str | None:
    argv = row["argv"]
    return argv[argv.index(flag) + 1] if flag in argv else None


def _fill(w: Watch, count: int) -> None:
    for n in range(count):
        oc.outbox_add(w.state, DESTINATION, f"old {n:02d}", str(w.state), f"body {n}", False)


# ---------------------------------------------------------------------------------------------
# MR 1: counters schema 2, the v1 migration, one latch per stream

# The live file's shape at plan time: latched, two runs without a verdict.
LIVE_V1 = {
    "deferred": 0, "setup_errors": 2, "no_verdict": 2, "no_verdict_notified": True,
    "fails": {"0.9.1": 1}, "integrity_alerts": {"0.7.21": ["sha512-" + "B" * 86 + "=="]},
}  # fmt: skip


def _seed_counters(w: Watch, data: dict) -> None:
    w.state.mkdir(parents=True, exist_ok=True)
    (w.state / "counters.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_latched_v1_file_migrates_with_every_latch_cleared(w: Watch) -> None:
    """The first setup error after the migration is not silent."""
    _seed_counters(w, LIVE_V1)
    w.npm(None)
    proc = w.run()
    assert proc.returncode == 0, proc.stderr
    assert [_summary(r) for r in w.rows()] == [BROKE]
    data = w.counters()
    assert data["schema"] == 2 and data["outbox_dropped"] == 0
    assert data["fails"] == LIVE_V1["fails"]
    assert data["integrity_alerts"] == LIVE_V1["integrity_alerts"]
    assert data["streams"]["new-version"] == {
        "deferred": 0, "setup_errors": 3, "no_verdict": 3, "notified": True}  # fmt: skip
    assert data["streams"]["self-test"] == {  # only the tail's own deferral: nothing migrated here
        "deferred": 1, "setup_errors": 0, "no_verdict": 1, "notified": False}  # fmt: skip
    lines = [ln for ln in w.history_all() if ln.startswith("- counters-migrated ")]
    assert len(lines) == 1 and lines[0].endswith(" v1->v2")
    assert not list(w.state.glob("counters.json.bad-*"))


@pytest.mark.parametrize(
    "data",
    [{**LIVE_V1, "extra": 1}, {**LIVE_V1, "schema": 2}, {**LIVE_V1, "no_verdict": -1},
     {**oc.COUNTER_DEFAULTS, "schema": 3}],
    ids=["extra-key", "v1-claiming-v2", "negative", "schema-3"],
)  # fmt: skip
def test_any_other_unreadable_file_is_moved_aside(data: dict, tmp_path: Path) -> None:
    """Only a file that passes the v1 check migrates."""
    assert oc._counters_v1_ok(LIVE_V1)  # each case below differs from a good file in one way
    (tmp_path / "counters.json").write_text(json.dumps(data), encoding="utf-8")
    got, warning = oc.load_counters(tmp_path)
    assert got == oc.COUNTER_DEFAULTS and "unreadable" in warning
    assert list(tmp_path.glob("counters.json.bad-*"))
    assert not (tmp_path / "history.log").exists()


def test_each_stream_has_its_own_latch(tmp_path: Path) -> None:
    """Setup notifies on the first, deferred on the second; verdict resets one stream only."""

    def event(name: str, stream: str) -> str:
        return oc.watch_state(tmp_path, name, None, None, stream)

    assert event("setup", "self-test") == "notify-setup 1"
    assert event("setup", "self-test") == "quiet 2"
    assert event("deferred", "new-version") == "quiet 1"
    assert event("deferred", "new-version") == "notify-deferred 2"
    assert event("deferred", "new-version") == "quiet 3"
    assert event("setup", "new-version") == "quiet 4"  # the stream is latched
    event("verdict", "new-version")
    assert event("setup", "self-test") == "quiet 3"  # untouched by the other stream's verdict
    assert event("setup", "new-version") == "notify-setup 1"
    with pytest.raises(oc.CompatInputError):
        event("setup", "no-such-stream")


def test_the_stream_flag_defaults_to_new_version(tmp_path: Path) -> None:
    """watch-state --stream picks the stream; without it, new-version."""

    def cli(*extra: str) -> str:
        argv = ["python3", str(REPO_ROOT / "dev/openrig_compat.py"), "watch-state",
                "--state-dir", str(tmp_path), "--event", "setup", *extra]  # fmt: skip
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=60, check=True
        ).stdout.strip()

    assert cli() == "notify-setup 1"
    assert cli("--stream", "self-test") == "notify-setup 1"
    assert cli("--stream", "new-version") == "quiet 2"


# ---------------------------------------------------------------------------------------------
# MR 2 and MR 3: the sender and the outbox


def test_one_place_creates_a_queue_row() -> None:
    """send_row is the only caller of `rig queue create`, bounded by a timeout."""
    text = WATCH.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert sum("queue create" in ln for ln in code) == 1
    assert sum('"$rig" "${args[@]}"' in ln for ln in code) == 1
    assert 'run_bg timeout 60 "$rig" "${args[@]}"' in text


def test_a_sent_row_leaves_the_outbox(w: Watch) -> None:
    """Add, send, remove."""
    w.plan(REVIEW)
    proc = w.run()
    assert proc.returncode == 0, proc.stderr
    (row,) = w.rows()
    assert _arg(row, "--id") is None and "--verify" not in row["argv"]
    assert row["body"].splitlines()[-1].startswith("First attempted: 2")
    assert _entries(w) == []
    log = (w.state / "run.log").read_text()
    assert "row sent: " in log and f"Queue create request ID: {REQUEST_ID}" in log


def test_a_failed_row_is_kept_and_resent_with_its_id_and_body(w: Watch) -> None:
    """A refused row stays; the next run re-sends the identical body under the same id."""
    _refuse(w)
    w.plan(REVIEW)
    proc = w.run()
    assert proc.returncode == 1
    summary = f"OpenRig {CAND}: REVIEW: decision needed"
    (entry,) = _entries(w)
    assert entry["summary"] == summary and entry["request_id"] == REQUEST_ID
    failed = [ln for ln in w.history_all() if ln.startswith("row-failed ")]
    assert len(failed) == 1 and failed[0].endswith(summary)
    latest = (w.state / "latest.md").read_text().split(maxsplit=3)
    assert latest[:2] == [CAND, "row-failed"] and latest[3].strip() == summary
    first = w.rows()[0]
    w.reset_logs()
    _refuse(w, False)
    proc = w.run()  # the verdict is final: only the flush sends
    assert proc.returncode == 0, proc.stderr
    (again,) = w.rows()
    assert _arg(again, "--id") == REQUEST_ID and again["body"] == first["body"]
    assert _summary(again) == summary
    assert _entries(w) == []


def test_a_send_without_a_request_id_is_resent_without_one(w: Watch) -> None:
    """A rig that prints nothing leaves request_id null."""
    _write(w.rig, "#!/bin/sh\nexit 1\n")
    w.plan(REVIEW)
    assert w.run().returncode == 1
    (entry,) = _entries(w)
    assert entry["request_id"] is None


def test_the_flush_sends_twenty_oldest_first_before_step_1(w: Watch) -> None:
    """20 waiting entries, oldest first, before npm is asked; the final flush takes the rest."""
    _fill(w, 23)
    w.npm(next(iter(_contract_integrities())))  # a listed version: step 2 sends nothing
    proc = w.run()
    assert proc.returncode == 0, proc.stderr
    assert [_summary(r) for r in w.rows()] == [f"old {n:02d}" for n in range(23)]
    assert _entries(w) == []
    log = (w.state / "run.log").read_text()
    assert log.index("row sent: old 19") < log.index("latest: ") < log.index("row sent: old 20")


def test_a_full_outbox_drops_the_oldest_and_later_says_so(w: Watch) -> None:
    """The cap exits 1 and records the drop; the next working run sends one 'dropped' row."""
    _fill(w, oc.OUTBOX_MAX)
    _refuse(w)
    w.plan(REVIEW)
    proc = w.run()
    assert proc.returncode == 1
    assert len(_entries(w)) == oc.OUTBOX_MAX
    assert _entries(w)[0]["summary"] == "old 01"
    dropped = [ln for ln in w.history_all() if ln.startswith("outbox-dropped ")]
    assert len(dropped) == 1 and dropped[0].endswith(" old 00")
    assert w.counters()["outbox_dropped"] == 1
    summary = "OpenRig watcher: 1 alerts dropped (outbox full)"
    assert summary not in [_summary(r) for r in w.rows()]  # no successful send: no row yet
    _refuse(w, False)
    w.reset_logs()
    proc = w.run()
    assert proc.returncode == 0, proc.stderr
    assert [_summary(r) for r in w.rows()].count(summary) == 1
    assert w.counters()["outbox_dropped"] == 0


def test_a_drop_alone_fails_the_run(w: Watch) -> None:
    """An add that drops exits 1 even when every send works."""
    _fill(w, oc.OUTBOX_MAX)
    spare = sorted((w.state / "outbox").glob("*.json"))[-1].read_bytes()
    for n in range(25):  # 75 waiting: the flush removes 20, so the verdict's add still finds 55
        (w.state / f"outbox/29990101T000000000000Z-{n:04d}.json").write_bytes(spare)
    w.plan(REVIEW)
    proc = w.run()
    assert proc.returncode == 1, proc.stderr
    assert not any(ln.startswith("row-failed ") for ln in w.history_all())
    assert sum(ln.startswith("outbox-dropped ") for ln in w.history_all()) == 6


def test_an_interrupted_run_keeps_the_entry_and_the_next_sends_it_once(w: Watch) -> None:
    """TERM between the add and the send (R148)."""
    (w.stub / "rig.hang").write_text("", encoding="utf-8")
    w.plan(REVIEW)
    proc = subprocess.Popen(w.argv(), env=w.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 30
    while not (w.stub / "rig.hanging").exists():
        assert time.monotonic() < deadline, "the stub rig never started"
        time.sleep(0.1)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=30) == 143
    proc.communicate(timeout=30)
    assert w.rows() == [] and len(_entries(w)) == 1
    body = _entries(w)[0]["body"]
    (w.stub / "rig.hang").unlink()
    again = w.run()
    assert again.returncode == 0, again.stderr
    (row,) = w.rows()
    assert row["body"] == body and _entries(w) == []


def test_a_malformed_entry_is_set_aside_and_a_bad_id_refused(tmp_path: Path) -> None:
    """A broken entry never blocks the others; mark takes only a request id."""
    name, _ = oc.outbox_add(tmp_path, DESTINATION, "good", "ref", "body", False)
    (tmp_path / "outbox/20200101T000000000000Z-0000.json").write_text("{", encoding="utf-8")
    assert [ln.split("\t")[2] for ln in oc.outbox_list(tmp_path)] == ["good"]
    assert list((tmp_path / "outbox").glob("*.bad"))
    with pytest.raises(oc.CompatInputError):
        oc.outbox_mark(tmp_path, name, "--verify")
    with pytest.raises(oc.CompatInputError):
        oc.outbox_add(tmp_path, DESTINATION, "two\nlines", "ref", "body", False)


# ---------------------------------------------------------------------------------------------
# MR 4: "checker broke"


def test_the_first_setup_error_verdict_is_a_checker_broke_row(w: Watch) -> None:
    """The row names the stream, the cause, the version, the evidence and the state."""
    w.plan(SETUP)
    w.run()
    (row,) = w.rows()
    assert _summary(row) == BROKE
    evidence = str(w.evidence / f"openrig-compat-{CAND}-20261120T000000Z")
    for text in ("Stream: new-version", f"compat {CAND}: npm: exit 2", f"Version: {CAND}",
                 f"Evidence: {evidence}", f"State: {w.state}",
                 f"History: {w.state}/history.log"):  # fmt: skip
        assert text in row["body"]
    assert _arg(row, "--evidence-ref") == evidence


def test_a_harness_cause_says_the_step_could_not_run(w: Watch) -> None:
    """R154: a harness setup error does not claim the checker is broken."""
    detail = "harness: agy: the package ships no parser module"
    w.plan({**SETUP, "setup_error": detail})
    w.run()
    (row,) = w.rows()
    assert _summary(row) == f"OpenRig watcher: checker could not run the harness step on {CAND}"
    for text in ("could not run the harness step", "community-harness-presets.js",
                 "not by itself evidence that the checker is broken", f"Detail: {detail}",
                 "Stream: new-version", "Evidence: "):  # fmt: skip
        assert text in row["body"]
    w.run()
    assert len(w.rows()) == 1  # the same latch


# ---------------------------------------------------------------------------------------------
# MR 5: the budget, the compat time limit and the harness list


def test_no_budget_left_defers_before_the_gate(w: Watch) -> None:
    """A run that cannot fit a gate wait and a compat run is skipped and recorded."""
    w.plan(REVIEW)
    proc = w.run("--deadline", "100")
    assert proc.returncode == 0, proc.stderr
    assert w.compat_calls() == [] and w.lines("pgrep.log") == []
    assert w.history()[-1].split()[1] == "deferred"
    assert "notify: " not in proc.stdout
    w.run("--deadline", "100")
    (row,) = w.rows()
    assert _summary(row) == "OpenRig watcher: 2 runs without a verdict"
    assert "budget: " in row["body"]


def test_no_budget_left_makes_the_retry_pending(w: Watch) -> None:
    """The FAIL retry is gated on the budget too."""
    w.plan({**FAIL, "sleep": 12})
    # fits when the run starts (770 - elapsed >= 0 + 100 + 660), not after a 12 s compat run
    proc = w.run("--deadline", "770", "--compat-max", "100")
    assert proc.returncode == 0, proc.stderr
    assert len(w.compat_calls()) == 1
    assert w.history()[-1].split()[1] == "fail-pending"


def test_a_compat_run_over_its_limit_is_a_setup_error(w: Watch) -> None:
    """Each compat run runs under timeout --compat-max."""
    w.plan({**REVIEW, "sleep": 30})
    started = time.monotonic()
    w.run("--compat-max", "1")
    assert time.monotonic() - started < 25
    assert w.history()[-1].split()[1] == "setup"
    assert [_summary(r) for r in w.rows()] == [BROKE]
    assert "compat exit 124" in (w.state / "run.log").read_text()


def test_the_harness_list_is_passed_through(w: Watch) -> None:
    """HACF_WATCH_HARNESSES becomes one --harness per name."""
    w.plan(REVIEW)
    w.run(HACF_WATCH_HARNESSES="agy,other-one")
    assert w.compat_calls()[0]["argv"] == [
        CAND, "--accept", "--harness", "agy", "--harness", "other-one"]  # fmt: skip


@pytest.mark.parametrize("bad", ["", "agy,", "Agy", "a b", "-x", "../x", "a" * 64])
def test_a_bad_harness_list_is_a_usage_error(bad: str, w: Watch) -> None:
    """A name outside the pattern is exit 2 before anything runs."""
    proc = w.run(HACF_WATCH_HARNESSES=bad)
    assert proc.returncode == 2 and "HACF_WATCH_HARNESSES" in proc.stderr
    assert w.compat_calls() == [] and not w.state.exists()


@pytest.mark.parametrize("flag", ["--deadline", "--compat-max"])
def test_the_budget_seams_take_whole_seconds(flag: str, w: Watch) -> None:
    """--deadline and --compat-max are whole seconds."""
    for bad in ("x", "-1", "1234567"):
        assert w.run(flag, bad).returncode == 2


# ---------------------------------------------------------------------------------------------
# MR 6: the tail runs after every outcome

TAIL_CASES = (
    "npm", "contract", "integrity-read", "listed", "final", "gate", "budget", "setup",
    "accepted", "not-a-patch", "refused", "review", "fail", "fail-pending",
)  # fmt: skip


@pytest.mark.parametrize("case", TAIL_CASES)
def test_the_tail_runs_after_every_outcome(case: str, w: Watch) -> None:
    """Every early exit and every verdict branch is followed by the tail and last-run."""
    extra: tuple[str, ...] = ()
    steps = [REVIEW]
    outcome = None
    if case == "npm":
        w.npm(None)
        outcome = "setup"
    elif case == "contract":
        (w.worktree / oc.CONTRACT_PATH).write_text("{", encoding="utf-8")
        outcome = "setup"
    elif case == "integrity-read":
        (w.stub / "npm.json").write_text(
            json.dumps({"latest": CAND, "integrity": {}}), encoding="utf-8"
        )
        outcome = "setup"
    elif case == "listed":
        w.npm(next(iter(_contract_integrities())))
    elif case == "final":
        w.plan(REVIEW)
        w.run()
        (w.state / "last-run").unlink()
    elif case == "gate":
        w.mem(100)
        outcome = "deferred"
    elif case == "budget":
        extra, outcome = ("--deadline", "0"), "deferred"
    elif case == "setup":
        steps, outcome = [SETUP], "setup"
    elif case == "accepted":
        steps, outcome = [{"verdict": "PASS", "accept": "accepted", "rc": 0}], "accepted"
    elif case == "not-a-patch":
        steps, outcome = [{"verdict": "PASS", "accept": "not-a-patch", "rc": 0}], "not-a-patch"
    elif case == "refused":
        steps = [{"verdict": "PASS", "accept": "review-streak", "rc": 3}]
        outcome = "review-streak"
    elif case == "review":
        outcome = "REVIEW"
    elif case == "fail":
        steps, outcome = [FAIL, FAIL], "FAIL"
    else:
        steps, extra = [{**FAIL, "sleep": 12}], ("--deadline", "770", "--compat-max", "100")
        outcome = "fail-pending"
    w.plan(*steps)
    proc = w.run(*extra)
    assert proc.returncode == 0, proc.stderr
    log = (w.state / "run.log").read_text()
    assert log.count("tail: start") == 1
    assert (w.state / "last-run").is_file()
    if outcome:
        assert w.history()[-1].split()[1] == outcome
    if outcome == "setup":
        assert [_summary(r) for r in w.rows()] == [BROKE]


def test_a_held_lock_skips_the_tail(w: Watch) -> None:
    """Only a held lock (or no state dir) skips the tail."""
    w.state.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(w.state / "lock"), "sleep", "30"])  # noqa: S603, S607
    try:
        time.sleep(0.3)
        assert w.run().returncode == 0
        assert not (w.state / "run.log").exists()
    finally:
        holder.kill()
        holder.wait()


# ---------------------------------------------------------------------------------------------
# MR 7: --row-check


def test_row_check_sends_one_verified_row_and_writes_no_state(w: Watch) -> None:
    """One --verify row through the host rig; no state, no lock, no notifier."""
    proc = w.run("--row-check")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [f"row-check: sent {REQUEST_ID}"]
    (row,) = w.rows()
    assert row["argv"][:6] == ["queue", "create", "--source", "hacf-openrig-watch@hacf",
                               "--destination", DESTINATION]  # fmt: skip
    assert _summary(row) == "OpenRig watcher installed: row path check"
    assert row["argv"][-1] == "--verify" and _arg(row, "--id") is None
    assert _arg(row, "--evidence-ref") == str(w.state)
    for text in (str(w.worktree), str(w.state), str(w.rig), "rig 9.9.9-stub"):
        assert text in row["body"]
    assert not w.state.exists() and w.lines("notify.log") == []
    assert list(w.tmpdir.iterdir()) == []


def test_row_check_failure_shows_the_last_five_lines(w: Watch) -> None:
    """A refused row is exit 1 with the rig's last 5 lines."""
    _refuse(w)
    proc = w.run("--row-check")
    assert proc.returncode == 1
    out = proc.stdout.splitlines()
    assert out[0] == "row-check: failed" and len(out) == 6
    assert out[-1] == "error: the daemon refused"
    assert not w.state.exists()


def test_row_check_takes_no_lock(w: Watch) -> None:
    """A running watcher does not block the check."""
    w.state.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(w.state / "lock"), "sleep", "30"])  # noqa: S603, S607
    try:
        time.sleep(0.3)
        assert w.run("--row-check").returncode == 0
        assert len(w.rows()) == 1
    finally:
        holder.kill()
        holder.wait()


def test_row_check_needs_a_destination(w: Watch) -> None:
    """Unset destination is exit 2."""
    proc = w.run("--row-check", HACF_WATCH_ROW_DESTINATION=None)
    assert proc.returncode == 2 and "HACF_WATCH_ROW_DESTINATION" in proc.stderr
    assert w.rows() == []


def test_check_env_shows_the_new_settings(w: Watch) -> None:
    """The upgrade destination, the harnesses and the failure notifier."""
    _write(w.bin / "node", "#!/bin/sh\n")
    _write(w.bin / "uv", "#!/bin/sh\n")
    out = w.run("--check-env", HACF_WATCH_UPGRADE_DESTINATION="op@kernel",
                HACF_WATCH_HARNESSES="agy,b",
                HACF_WATCH_FAILURE_NOTIFY_CMD="notify-stub").stdout.splitlines()  # fmt: skip
    for line in ("upgrade destination: op@kernel", "harnesses: agy,b",
                 f"failure notifier: {w.bin / 'notify-stub'}"):  # fmt: skip
        assert line in out
    out = w.run("--check-env", HACF_WATCH_FAILURE_NOTIFY_CMD="no-such").stdout.splitlines()
    assert "failure notifier: unresolved no-such" in out


# ---------------------------------------------------------------------------------------------
# MR 11: the failure notification


def _unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def test_the_unit_files_wire_the_failure_notification() -> None:
    """OnFailure= on the watcher unit; the failed unit runs --failure-notify and nothing else."""
    assert _unit(SERVICE)["Unit"]["OnFailure"] == FAILED_UNIT.name
    failed = _unit(FAILED_UNIT)
    assert dict(failed["Service"]) == {
        "Type": "oneshot",
        "EnvironmentFile": "%h/.config/hacf-openrig-watch/env",
        "ExecStart": "/bin/bash ${HACF_WATCH_WORKTREE}/dev/openrig_watch.sh --failure-notify",
        "TimeoutStartSec": "60",
    }
    assert dict(failed["Unit"]) == {
        "Description": "Notify the desktop that the HACF OpenRig watcher run failed"
    }
    assert "Install" not in failed
    assert "logged in" in FAILED_UNIT.read_text(encoding="utf-8")


def _failure_notify(w: Watch, cmd: str | None) -> subprocess.CompletedProcess:
    """--failure-notify with no TMPDIR, rig or worktree."""
    _write(w.bin / "fail-notify", FAILURE_NOTIFIER)
    return w.run("--failure-notify", HACF_WATCH_FAILURE_NOTIFY_CMD=cmd, TMPDIR=None,
                 HACF_WATCH_RIG=None, HACF_WATCH_WORKTREE=None)  # fmt: skip


def test_failure_notify_without_a_notifier_is_a_no_op(w: Watch) -> None:
    """Unset: logged on stdout, exit 0."""
    proc = _failure_notify(w, None)
    assert proc.returncode == 0 and proc.stdout == "failure-notify: no notifier\n"
    assert w.lines("failure.log") == [] and not w.state.exists()


def test_failure_notify_with_an_unresolvable_notifier_fails(w: Watch) -> None:
    """Unresolvable: exit 1."""
    proc = _failure_notify(w, "no-such-notifier")
    assert proc.returncode == 1
    assert proc.stdout == "failure-notify: unresolved no-such-notifier\n"


@pytest.mark.parametrize("rc", [0, 7])
def test_failure_notify_passes_the_last_line_and_the_rc(rc: int, w: Watch) -> None:
    """Two arguments, a history line, and the notifier's own exit status."""
    w.state.mkdir(parents=True)
    (w.state / "latest.md").write_text("older\n9.9.9 row-failed T x\n", encoding="utf-8")
    (w.stub / "fail.rc").write_text(str(rc), encoding="utf-8")
    proc = _failure_notify(w, "fail-notify")
    assert proc.returncode == rc, proc.stderr
    assert w.lines("failure.log") == [
        ["HACF OpenRig watcher: run failed",
         f"9.9.9 row-failed T x; see {w.state}/history.log"]]  # fmt: skip
    (line,) = w.history_all()
    assert line.startswith("- failure-notify ") and line.endswith(f" rc {rc}")
    assert w.rows() == [] and not (w.state / "lock").exists()


def test_failure_notify_without_state_and_with_a_long_line(w: Watch) -> None:
    """No latest.md is said; the line is cut to 300 characters; no state dir is created."""
    assert _failure_notify(w, "fail-notify").returncode == 0
    assert w.lines("failure.log")[0][1] == f"no latest.md; see {w.state}/history.log"
    assert not w.state.exists()
    w.state.mkdir(parents=True)
    (w.state / "latest.md").write_text("y" * 900 + "\n", encoding="utf-8")
    _failure_notify(w, "fail-notify")
    assert w.lines("failure.log")[1][1] == "y" * 300


# ---------------------------------------------------------------------------------------------
# MR 8: the install


def _unit_environment(home: Path) -> str:
    return _unit(SERVICE)["Service"]["Environment"].replace("%h", str(home))


def _row_check_line(host: Host) -> str:
    env_file = host.home / ".config/hacf-openrig-watch/env"
    return (f'systemd-run --user --wait --pipe -p EnvironmentFile={env_file} -p '
            f'"Environment={_unit_environment(host.home)}" /bin/bash '
            f"{host.worktree}/dev/openrig_watch.sh --row-check")  # fmt: skip


def test_dry_run_renders_the_new_flags_and_step_7(tmp_path: Path) -> None:
    """The env lines, the third unit and the row check under the unit's own Environment=."""
    host = Host(tmp_path)
    notifier = tmp_path / "failbin/fail-notify"
    notifier.parent.mkdir()
    _write(notifier, "#!/bin/sh\n")
    host.path = f"{notifier.parent}:{host.path}"
    proc = host.run(*_install_args(
        host, "--dry-run", "--row-destination", "lead@kernel",
        "--upgrade-destination", "op@kernel", "--harness", "agy", "--harness", "two",
        "--harness", "agy", "--failure-notify-cmd", "fail-notify"))  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    for line in ("HACF_WATCH_ROW_DESTINATION=lead@kernel",
                 "HACF_WATCH_UPGRADE_DESTINATION=op@kernel", "HACF_WATCH_HARNESSES=agy,two",
                 f"HACF_WATCH_FAILURE_NOTIFY_CMD={notifier}"):  # fmt: skip
        assert f"    {line}" in out
    path = next(ln for ln in out if ln.startswith("    PATH="))
    assert str(notifier.parent) in path.split("=", 1)[1].split(":")
    units = host.home / ".config/systemd/user"
    assert (f"dry-run: copy {FAILED_UNIT} -> {units}/{FAILED_UNIT.name}") in out
    assert f"dry-run: run {_row_check_line(host)}" in out
    assert host.files() == [] and host.calls() == []


def test_without_the_new_flags_nothing_new_is_rendered(tmp_path: Path) -> None:
    """Each setting only when given; step 7 only with a row destination."""
    host = Host(tmp_path)
    out = host.run(*_install_args(host, "--dry-run")).stdout
    for text in ("UPGRADE_DESTINATION", "HACF_WATCH_HARNESSES", "FAILURE_NOTIFY", "--row-check"):
        assert text not in out


def test_the_install_runs_the_row_check_last(tmp_path: Path) -> None:
    """With stubs: the row check runs after --check-env, and the failed unit is copied."""
    host = Host(tmp_path)
    proc = host.run(*_install_args(host, "--row-destination", "lead@kernel"))
    assert proc.returncode == 0, proc.stderr
    assert host.calls()[-1] == _row_check_line(host).replace('"', "")
    assert host.calls()[-2].endswith("--check-env")
    copied = host.home / ".config/systemd/user" / FAILED_UNIT.name
    assert copied.read_bytes() == FAILED_UNIT.read_bytes()


def test_a_failed_row_check_fails_the_install_with_the_retry(tmp_path: Path) -> None:
    """The install says how to retry the row check."""
    host = Host(tmp_path)
    _write(host.stubs / "systemd-run",
           '#!/bin/sh\ncase "$*" in *--row-check*) exit 1 ;; esac\nexit 0\n')  # fmt: skip
    proc = host.run(*_install_args(host, "--row-destination", "lead@kernel"))
    assert proc.returncode == 1
    assert f"the row path check failed; retry with: {_row_check_line(host)}" in proc.stderr
    assert "installed;" not in proc.stdout


@pytest.mark.parametrize("bad", ["Agy", "a,b", "-x", "../x", "a" * 64])
def test_a_bad_harness_name_writes_nothing(bad: str, tmp_path: Path) -> None:
    """Exit 2, nothing written."""
    host = Host(tmp_path)
    proc = host.run(*_install_args(host, "--harness", bad))
    assert proc.returncode == 2
    assert host.files() == [] and host.calls() == []


def test_an_unresolvable_failure_notifier_writes_nothing(tmp_path: Path) -> None:
    """Exit 1, nothing written."""
    host = Host(tmp_path)
    proc = host.run(*_install_args(host, "--failure-notify-cmd", "no-such-notifier"))
    assert proc.returncode == 1 and "--failure-notify-cmd" in proc.stderr
    assert host.files() == [] and host.calls() == []


@pytest.mark.parametrize("flag", ["--upgrade-destination", "--harness", "--failure-notify-cmd"])
def test_uninstall_refuses_the_new_flags(flag: str, tmp_path: Path) -> None:
    """--uninstall takes no install option."""
    host = Host(tmp_path)
    proc = host.run("--uninstall", flag, "agy")
    assert proc.returncode == 2 and host.calls() == []
