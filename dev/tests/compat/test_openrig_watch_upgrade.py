"""The watcher's tail (OPR.99.0.13.3): the installed version, the self-test, the upgrade request.

Mission SPEC 5.3 and 5.4. Offline and in tmp dirs only: test_openrig_watch's sandbox with a rig
stub that answers --version and prints request ids, a real git repo only under tmp_path, a fake
clock (--now) and fake evidence. No test reaches a real rig, npm, network or watcher worktree.
"""

from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import time
from pathlib import Path

import pytest
from test_openrig_watch import (
    CAND,
    DESTINATION,
    INTEGRITY,
    Watch,
    _contract_integrities,
    _summary,
    _write,
    oc,
)

OPERATOR = "operator@example"
NOW = "2026-11-26T06:20:00Z"
LATER = "2026-12-03T06:20:00Z"  # exactly seven days on
OTHER = "sha512-" + "C" * 86 + "=="
LIVE = "0.7.23"
INSTALLED = "0.1.0"  # below every candidate here

RIG = r"""#!/usr/bin/env python3
import json, os, sys, time
stub = os.environ["STUB_DIR"]
argv = sys.argv[1:]
if argv == ["--version"]:
    path = os.path.join(stub, "rig.version")
    if os.path.exists(path):
        print(open(path).read())
    sys.exit(0)
dest = argv[argv.index("--destination") + 1]
if os.path.exists(os.path.join(stub, "rig.hang-" + dest)):
    open(os.path.join(stub, "rig.hanging"), "w").write(str(os.getpid()))
    time.sleep(60)
body = open(argv[argv.index("--body-file") + 1]).read()
log = os.path.join(stub, "rig.log")
n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
with open(log, "a") as h:
    h.write(json.dumps({"argv": argv, "body": body}) + "\n")
print(f"Queue create request ID: qitem-20261126000000-{n:04x} (not proof of persistence)")
sys.exit(1 if os.path.exists(os.path.join(stub, "rig.fail-" + dest)) else 0)
"""


def _arg(row: dict, flag: str) -> str | None:
    argv = row["argv"]
    return argv[argv.index(flag) + 1] if flag in argv else None


def _git(root: Path, *args: str) -> str:
    env = {"HOME": str(root.parent), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}  # fmt: skip
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env, timeout=60, check=True)  # fmt: skip
    return proc.stdout.strip()


class Box(Watch):
    """The watcher sandbox with a git worktree, an operator destination and a fixed clock."""

    def __init__(self, tmp: Path) -> None:
        """Build it: commit `sha` is on the accepted branch, `side` is not."""
        super().__init__(tmp)
        _write(self.rig, RIG)
        _git(self.worktree, "init", "-q", "-b", "swimlane/openrig-accepted")
        _git(self.worktree, "commit", "-q", "--allow-empty", "-m", "accept")
        self.sha = _git(self.worktree, "rev-parse", "HEAD")
        _git(self.worktree, "checkout", "-q", "-b", "side")
        _git(self.worktree, "commit", "-q", "--allow-empty", "-m", "side")
        self.side = _git(self.worktree, "rev-parse", "HEAD")
        _git(self.worktree, "checkout", "-q", "swimlane/openrig-accepted")

    def installed(self, raw: str | None) -> None:
        """What `rig --version` prints."""
        path = self.stub / "rig.version"
        path.write_text(raw, encoding="utf-8") if raw else path.unlink(missing_ok=True)

    def day(self, *extra: str, now: str = NOW, **env: str | None) -> subprocess.CompletedProcess:
        """One run with the operator destination set."""
        env.setdefault("HACF_WATCH_UPGRADE_DESTINATION", OPERATOR)
        return self.run("--now", now, *extra, **env)

    def rows(self) -> list[dict]:
        """Every row, in order."""
        return self.lines("rig.log")

    def to(self, dest: str) -> list[dict]:
        """The rows to one destination."""
        return [r for r in self.rows() if _arg(r, "--destination") == dest]

    def summaries(self) -> list[str]:
        """Every row's summary."""
        return [_summary(r) for r in self.rows()]

    def fail(self, dest: str, fail: bool = True) -> None:
        """Make the rig refuse rows to dest."""
        path = self.stub / f"rig.fail-{dest}"
        path.write_text("", encoding="utf-8") if fail else path.unlink(missing_ok=True)

    def evidence_for(self, version: str, name: str = "ev", **over: object) -> Path:
        """A fabricated evidence dir with a schema-valid verdict.json."""
        path = self.evidence / f"{name}-{version}"
        path.mkdir(parents=True, exist_ok=True)
        verdict = over.pop("verdict", "PASS")
        data = {"version": version, "verdict": verdict, "failed_step": None,
                "baseline": "0.7.21", "person_baseline": "0.7.16", "watched_diff": {},
                "help_diff": {}, "json_paths_added": {}, "harness": {"agy": "ok"},
                "setup_error": None, "timings": {}, "steps": [], "integrity": INTEGRITY,
                "accept": {"result": "accepted", "sha": self.sha}}  # fmt: skip
        data.update(over)
        (path / "verdict.json").write_text(json.dumps(data), encoding="utf-8")
        return path

    def accepted(self, version: str, **over: object) -> Path:
        """A final `accepted` record with its evidence; returns the evidence dir."""
        ev = self.evidence_for(version)
        record = {"version": version, "outcome": "accepted", "final": True, "evidence": str(ev),
                  "time": NOW, **oc.comparison_fields(ev, version), **over}  # fmt: skip
        oc.write_json(self.state / "verdicts" / f"{version}.json", record)
        return ev

    def plan(self, installed: str | None = INSTALLED, integrity: dict | None = None,
             now: str = NOW, dest: str = OPERATOR, unknown: bool = False) -> dict:  # fmt: skip
        """One in-process reconcile; the integrity map defaults to the records' own."""
        file = "-"
        if not unknown:
            if integrity is None:
                integrity = {p.stem: INTEGRITY for p in (self.state / "verdicts").glob("*.json")}
            file = str(self.tmp / "integrity.tsv")
            Path(file).write_text(
                "".join(f"{v}\t{i}\n" for v, i in integrity.items()), encoding="utf-8"
            )
        self.state.mkdir(parents=True, exist_ok=True)
        oc.upgrade_plan(self.state, self.worktree, installed or "-", file, now, dest, DESTINATION)
        return self.upgrade()

    def upgrade(self) -> dict:
        """upgrade.json."""
        return json.loads((self.state / "upgrade.json").read_text())

    def pending(self) -> list[list[str]]:
        """`watch-upgrade pending`'s lines, split."""
        out = self.tmp / "pending"
        out.mkdir(exist_ok=True)
        return [line.split("\t") for line in oc.upgrade_pending(self.state, out)]

    def send(self, ok: bool = True, request_id: str = "qitem-20261126000000-aa") -> None:
        """Record the first owed send."""
        kind, key, _, _ = self.pending()[0]
        oc.upgrade_sent(self.state, kind, key, ok, request_id, DESTINATION)

    def fyis(self) -> list[str]:
        """The outbox's summaries (the FYIs plan and sent queued)."""
        return [line.split("\t")[2] for line in oc.outbox_list(self.state, 50)]

    def notes(self) -> str:
        """history.log."""
        return (self.state / "history.log").read_text()


@pytest.fixture
def b(tmp_path: Path) -> Box:
    """A fresh sandbox."""
    return Box(tmp_path)


# ---------------------------------------------------------------------------------------------
# MR 1: the installed version


def test_installed_first_same_changed_unparsed(tmp_path: Path) -> None:
    """Each answer, and what installed.json keeps."""
    assert oc.watch_installed(tmp_path, "0.7.23 (484cbffb)\n") == "first 0.7.23"
    data = json.loads((tmp_path / "installed.json").read_text())
    assert data["version"] == "0.7.23" and data["raw"] == "0.7.23 (484cbffb)" and data["seen"]
    assert oc.watch_installed(tmp_path, "0.7.23") == "same 0.7.23"
    assert oc.watch_installed(tmp_path, "0.7.24 (abc)") == "changed 0.7.23 0.7.24"
    before = (tmp_path / "installed.json").read_bytes()
    for raw in ("", "rig 0.7.25", "0.7", "v0.7.25", "0.7.25 junk", "0.7.25\nwarning", "00.1.2"):
        assert oc.watch_installed(tmp_path, raw) == "unparsed", raw
    assert (tmp_path / "installed.json").read_bytes() == before


def test_an_unparsable_version_defers_the_self_test_twice_then_one_row(b: Box) -> None:
    """I unknown: a self-test deferral with the raw text; the second in a row is a row."""
    b.installed("rig: broken " + "x" * 200)
    b.npm(next(iter(_contract_integrities())))
    for _ in range(3):
        assert b.day().returncode == 0
    (row,) = b.rows()
    assert _summary(row) == "OpenRig checker self-test: 2 runs without a verdict"
    assert "rig --version unparsable: rig: broken" in row["body"]
    assert "x" * 90 not in row["body"]
    assert b.notes().count("selftest deferred: rig --version unparsable") == 3
    assert b.compat_calls() == [] and not (b.state / "installed.json").exists()
    assert b.stream("new-version")["no_verdict"] == 0


# ---------------------------------------------------------------------------------------------
# MR 2: the final record


def test_a_final_record_holds_the_comparison_fields(b: Box) -> None:
    """The fields, and accept_sha only for `accepted`."""
    ev = b.evidence_for(CAND, help_diff={"z": 1, "a": 2}, watched_diff={"w": {}})
    oc.watch_record(b.state, CAND, "accepted", str(ev), True)
    rec = json.loads((b.state / f"verdicts/{CAND}.json").read_text())
    assert (rec["verdict"], rec["watched"], rec["help"]) == ("PASS", ["w"], ["a", "z"])
    assert rec["harness"] == {"agy": "ok"} and rec["baselines"] == ["0.7.21", "0.7.16"]
    assert rec["integrity"] == INTEGRITY and rec["accept_sha"] == b.sha
    oc.watch_record(b.state, CAND, "REVIEW", str(ev), True)
    assert "accept_sha" not in json.loads((b.state / f"verdicts/{CAND}.json").read_text())
    oc.watch_record(b.state, "9.9.9", "REVIEW", str(ev), False)
    assert not (b.state / "verdicts/9.9.9.json").exists()


@pytest.mark.parametrize("text", ["{", "[]", '{"version": "x"}', None])
def test_an_unreadable_verdict_is_recorded_without_the_fields(text: str | None, b: Box) -> None:
    """The record is written; history says so."""
    ev = b.evidence / "bad"
    ev.mkdir(parents=True)
    if text is not None:
        (ev / "verdict.json").write_text(text, encoding="utf-8")
    b.state.mkdir()
    oc.watch_record(b.state, CAND, "REVIEW", str(ev), True)
    rec = json.loads((b.state / f"verdicts/{CAND}.json").read_text())
    assert rec["final"] is True and "verdict" not in rec
    assert f"record: {CAND} without comparison fields" in b.notes()


# ---------------------------------------------------------------------------------------------
# MR 3: the self-test helper, rule by rule

REVIEWED = {"verdict": "REVIEW", "watched_diff": {"SLICE_MANIFEST": {}},
            "harness": {"agy": "unreferenced"}}  # fmt: skip


def _judge(b: Box, version: str = "9.0.0", rc: int | None = None, **over: object):
    ev = b.evidence_for(version, "self", **over)
    data = json.loads((ev / "verdict.json").read_text())
    data.pop("accept")
    (ev / "verdict.json").write_text(json.dumps(data), encoding="utf-8")
    rc = oc.VERDICT_EXIT[data["verdict"]] if rc is None else rc
    return oc.watch_selftest(b.state, b.worktree, version, str(ev), rc, ["agy"])


def _expectation(b: Box, version: str = "9.0.0") -> dict | None:
    path = b.state / f"selftest/{version}.json"
    return json.loads(path.read_text()) if path.exists() else None


@pytest.mark.parametrize("verdict", ["PASS", "REVIEW"])
def test_rule_3_records_a_first_result_then_matches_it(verdict: str, b: Box) -> None:
    """No basis: PASS or REVIEW is recorded (unchecked); the same result is then ok."""
    result, text = _judge(b, verdict=verdict)
    assert result == "recorded-first" and "source=first-result" in text
    held = _expectation(b)
    assert held["source"] == "first-result" and held["verdict"] == verdict and held["recorded"]
    assert _judge(b, verdict=verdict)[0] == "ok"


def test_a_difference_is_broke_and_lists_each_field(b: Box) -> None:
    """Same baselines: every differing field, and the expectation is untouched."""
    _judge(b)
    held = _expectation(b)
    result, text = _judge(b, **REVIEWED, help_diff={"h": 1})
    assert result == "broke"
    for part in ('verdict: expected "PASS" got "REVIEW"', "watched: expected [] got",
                 'help: expected [] got ["h"]', "harness: expected"):  # fmt: skip
        assert part in text
    assert "baselines:" not in text and _expectation(b) == held


def test_changed_baselines_record_again(b: Box) -> None:
    """A new baselines pair re-records."""
    _judge(b)
    result, _ = _judge(b, **REVIEWED, baseline="0.7.23")
    assert result == "recorded-first"
    assert _expectation(b)["baselines"] == ["0.7.23", "0.7.16"]


def test_a_setup_error_and_a_fail_are_never_expectations(b: Box) -> None:
    """SETUP-ERROR is broke; FAIL is nobasis-fail; neither is written."""
    result, text = _judge(b, verdict="SETUP-ERROR", setup_error="npm: exit 2")
    assert result == "broke" and "npm: exit 2" in text
    assert _judge(b, verdict="FAIL")[0] == "nobasis-fail"
    assert oc.watch_selftest(b.state, b.worktree, "9.0.0", "-", 124, ["agy"])[0] == "broke"
    assert _judge(b, rc=7)[0] == "broke"  # the exit code disagrees: not a valid result
    assert _judge(b, harness="ok")[0] == "broke"
    assert _expectation(b) is None


def test_a_setup_error_against_an_expectation_is_broke(b: Box) -> None:
    """A held expectation is not overwritten by a setup error."""
    _judge(b)
    result, text = _judge(b, verdict="SETUP-ERROR", setup_error="boom")
    assert result == "broke" and "setup error: boom" in text
    assert _expectation(b)["verdict"] == "PASS"


def _listed_with_baseline(b: Box, harness: bool) -> str:
    version = next(iter(_contract_integrities()))
    base = b.worktree / "dev/tests/fixtures/openrig-compat" / version
    (base / "harness/agy" if harness else base).mkdir(parents=True)
    return version


@pytest.mark.parametrize("harness", [False, True])
def test_rule_1_a_listed_version_has_a_fixed_expectation(harness: bool, b: Box) -> None:
    """The rollback case (no harness dir: REVIEW absent) and a baseline that holds it (PASS)."""
    version = _listed_with_baseline(b, harness)
    good = {} if harness else {"verdict": "REVIEW", "harness": {"agy": "absent"}}
    result, text = _judge(b, version, **good)
    assert result == "recorded-fixed" and "source=fixed" in text
    assert _judge(b, version, **good)[0] == "ok"


def test_rule_1_a_mismatch_is_broke_and_not_recorded(b: Box) -> None:
    """A listed version that differs from its fixed expectation."""
    version = _listed_with_baseline(b, False)
    result, text = _judge(b, version, **REVIEWED)
    assert result == "broke" and "watched: expected [] got" in text
    assert _expectation(b, version) is None


def _verdict_record(b: Box, outcome: str, **over: object) -> None:
    ev = b.evidence_for("9.0.0", "step3", **over)
    oc.watch_record(b.state, "9.0.0", outcome, str(ev), True)


def test_rule_2_the_watchers_own_verdict_is_the_basis(b: Box) -> None:
    """A matching final record is recorded; a differing result is broke."""
    b.state.mkdir()
    _verdict_record(b, "REVIEW", **REVIEWED)
    result, text = _judge(b)
    assert result == "broke" and "verdict: expected" in text and _expectation(b) is None
    result, text = _judge(b, **REVIEWED)
    assert result == "recorded-watcher" and "source=watcher-verdict" in text
    assert _expectation(b)["source"] == "watcher-verdict"


def test_rule_2_ignores_a_fail_record(b: Box) -> None:
    """A FAIL record is no basis: rule 3."""
    b.state.mkdir()
    _verdict_record(b, "FAIL", verdict="FAIL")
    assert _judge(b)[0] == "recorded-first"


# ---------------------------------------------------------------------------------------------
# MR 3 through the watcher: rows, latches, skips and the live sequence


def _unlist(b: Box, version: str) -> None:
    """Drop `version` from the fixture worktree's contract, whatever the shipped one lists."""
    path = b.worktree / oc.CONTRACT_PATH
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if f'version: "{version}"' not in ln]
    path.write_text("".join(kept), encoding="utf-8")
    assert version not in [entry[0] for entry in oc.contract_entries(b.worktree)]


def _live(b: Box) -> None:
    """The live case: OpenRig LIVE installed and latest, and not yet in the watcher's contract."""
    _unlist(b, LIVE)
    b.installed(f"{LIVE} (484cbffb)")
    b.npm(LIVE)
    b.plan_steps = {"verdict": "REVIEW", "rc": 3, "watched": ["SLICE_MANIFEST"],
                    "harness": {"agy": "unreferenced"}}  # fmt: skip
    b.plan_compat(b.plan_steps)


Box.plan_compat = Watch.plan  # Watch.plan sets the stub compat's outcomes; Box.plan reconciles


def test_the_live_0_7_23_sequence(b: Box) -> None:
    """Day 1: the REVIEW row and the recorded expectation. Day 2: quiet."""
    _live(b)
    proc = b.day()
    assert proc.returncode == 0, proc.stderr
    assert b.summaries() == [
        f"OpenRig {LIVE}: REVIEW: decision needed",
        f"OpenRig checker self-test: expectation recorded for {LIVE}",
    ]
    assert "source=watcher-verdict" in b.rows()[1]["body"]
    assert [c["argv"][:2] for c in b.compat_calls()] == [[LIVE, "--accept"]]  # no second run
    assert _expectation(b, LIVE)["source"] == "watcher-verdict"
    proc = b.day(now="2026-11-27T06:20:00Z")
    assert proc.returncode == 0, proc.stderr
    assert len(b.rows()) == 2
    assert b.compat_calls()[1]["argv"] == [LIVE, "--harness", "agy"]  # without --accept
    assert f"selftest {LIVE} ok " in b.notes()
    assert b.to(OPERATOR) == []


def test_a_rollback_to_a_listed_version_is_not_checker_broke(b: Box) -> None:
    """R135: recorded fixed once, then quiet."""
    version = _listed_with_baseline(b, False)
    b.installed(version)
    b.npm(version)
    b.plan_compat({"verdict": "REVIEW", "rc": 3, "watched": [], "harness": {"agy": "absent"}})
    b.day()
    b.day()
    assert b.summaries() == [f"OpenRig checker self-test: expectation recorded for {version}"]


def test_broke_is_one_row_until_a_match_resets_it(b: Box) -> None:
    """The self-test stream's latch."""
    version = _listed_with_baseline(b, True)
    b.installed(version)
    b.npm(version)
    bad = {"verdict": "REVIEW", "rc": 3}
    good = {"verdict": "PASS", "rc": 0}
    broke = f"OpenRig watcher: checker broke (self-test {version})"
    b.plan_compat(bad)
    b.day()
    b.day()
    assert b.summaries() == [broke]
    assert "watched: expected [] got" in b.rows()[0]["body"]
    b.plan_compat(good)
    b.day()
    b.plan_compat(bad)
    b.day()
    assert b.summaries()[-1] == broke and b.summaries().count(broke) == 2
    assert b.stream("new-version")["setup_errors"] == 0


def test_a_self_test_setup_error_is_one_immediate_row(b: Box) -> None:
    """SETUP-ERROR is the stream's setup event."""
    version = next(iter(_contract_integrities()))
    b.installed(version)
    b.npm(version)
    b.plan_compat({"verdict": "SETUP-ERROR", "rc": 2})
    b.day()
    b.day()
    assert b.summaries() == [f"OpenRig watcher: checker broke (self-test {version})"]
    assert _expectation(b, version) is None


def test_a_fail_without_a_basis_is_one_row_per_version(b: Box) -> None:
    """The .nobasis latch, then daily history lines."""
    version = next(iter(_contract_integrities()))
    b.installed(version)
    b.npm(version)
    b.plan_compat({"verdict": "FAIL", "rc": 1})
    b.day()
    b.day()
    assert b.summaries() == [
        f"OpenRig checker self-test: no expectation for {version} (a FAIL is never an expectation)"
    ]
    assert (b.state / f"selftest/{version}.nobasis").exists()
    assert b.notes().count(f"selftest {version} no-expectation FAIL") == 2


@pytest.mark.parametrize("why", ["npm unavailable", "budget", "gate"])
def test_a_skipped_self_test_is_deferred_then_one_row(why: str, b: Box) -> None:
    """Each skip condition: no compat run, the two-in-a-row rule."""
    b.installed(INSTALLED)
    b.npm(next(iter(_contract_integrities())))
    extra: tuple[str, ...] = ()
    if why == "npm unavailable":
        b.npm(None)
    elif why == "budget":
        extra = ("--deadline", "100")
    else:
        b.mem(100)
    b.day(*extra)
    b.day(*extra)
    assert b.compat_calls() == []
    assert b.notes().count(f"selftest deferred: {why}") == 2
    summary = "OpenRig checker self-test: 2 runs without a verdict"
    assert b.summaries().count(summary) == 1


# ---------------------------------------------------------------------------------------------
# MR 5: eligibility and the reconcile


def test_an_eligible_version_opens_one_request(b: Box) -> None:
    """The highest eligible accepted version above I; expires in seven days."""
    b.accepted("1.0.0")
    ev = b.accepted("1.0.2")
    b.accepted("1.0.1")
    x = b.plan()["open"]
    assert x["version"] == "1.0.2" and x["from"] == INSTALLED and x["status"] == "unsent"
    assert x["key"] == f"owr-1.0.2-{b.sha[:12]}" and x["request_ids"] == []
    assert x["expires_at"] == LATER and x["before_sent"] is False
    assert x["verdict_sha256"] == hashlib.sha256((ev / "verdict.json").read_bytes()).hexdigest()
    assert b.fyis() == []  # the before-FYI follows the send
    body = x["body"]
    assert b.plan()["open"]["body"] == body  # never rendered again


@pytest.mark.parametrize(
    "clause",
    ["not-final", "not-accepted", "no-sha", "no-integrity", "installed-unknown", "below",
     "equal", "integrity-unknown", "integrity-absent", "integrity-differs", "not-on-branch",
     "unknown-commit", "done"],
)  # fmt: skip
def test_every_eligibility_clause(clause: str, b: Box) -> None:
    """Each clause alone makes V ineligible."""
    over = {"not-final": {"final": False}, "not-accepted": {"outcome": "review-streak"},
            "no-sha": {"accept_sha": None}, "no-integrity": {"integrity": None},
            "not-on-branch": {"accept_sha": b.side},
            "unknown-commit": {"accept_sha": "f" * 40}}.get(clause, {})  # fmt: skip
    b.accepted("1.0.0", **over)
    kwargs: dict = {}
    if clause == "installed-unknown":
        kwargs["installed"] = None
    elif clause in ("below", "equal"):
        kwargs["installed"] = "2.0.0" if clause == "below" else "1.0.0"
    elif clause == "integrity-unknown":
        kwargs["unknown"] = True
    elif clause == "integrity-absent":
        kwargs["integrity"] = {}
    elif clause == "integrity-differs":
        kwargs["integrity"] = {"1.0.0": OTHER}
    elif clause == "done":
        oc.write_json(b.state / "upgrade.json",
                      {"open": None, "done": {"1.0.0": "expired"}, "withdrawals": []})  # fmt: skip
    data = b.plan(**kwargs)
    assert data["open"] is None and b.pending() == []
    if clause in ("below", "equal"):
        assert f"upgrade: none: 1.0.0 <= installed {kwargs['installed']}" in b.notes()


def test_no_destination_raises_nothing(b: Box) -> None:
    """History only."""
    b.accepted("1.0.0")
    assert b.plan(dest="-")["open"] is None
    assert "upgrade: no destination for 1.0.0" in b.notes() and b.fyis() == []


def _opened(b: Box, sent: bool = True) -> dict:
    b.accepted("1.0.0")
    b.plan()
    if sent:
        b.send()
    for name in (b.state / "outbox").glob("*.json"):
        name.unlink()
    return b.upgrade()["open"]


def test_sent_records_the_id_and_queues_the_before_fyi(b: Box) -> None:
    """ok: sent, the id, one verified FYI to the advisor."""
    b.accepted("1.0.0")
    b.plan()
    b.send()
    x = b.upgrade()["open"]
    assert x["status"] == "sent" and x["before_sent"] is True
    assert x["request_ids"] == ["qitem-20261126000000-aa"]
    (line,) = oc.outbox_list(b.state)
    name, dest, summary, _, verify, _ = line.split("\t")
    assert (dest, verify) == (DESTINATION, "yes")
    assert summary == f"OpenRig 1.0.0: upgrade requested from operator (from {INSTALLED})"
    body = json.loads((b.state / "outbox" / name).read_text())["body"]
    assert x["key"] in body and "qitem-20261126000000-aa" in body
    assert b.pending() == []


def test_a_failed_send_stays_owed_with_its_id(b: Box) -> None:
    """Ok no: unsent, the id kept for the retry, no FYI."""
    b.accepted("1.0.0")
    b.plan()
    b.send(ok=False)
    x = b.upgrade()["open"]
    assert x["status"] == "unsent" and x["request_ids"] == ["qitem-20261126000000-aa"]
    assert b.fyis() == [] and b.pending()[0][3] == "qitem-20261126000000-aa"


def test_fulfilled_when_the_host_reaches_the_version(b: Box) -> None:
    """I == X.version."""
    x = _opened(b)
    data = b.plan(installed="1.0.0")
    assert data["open"] is None and data["done"] == {"1.0.0": "fulfilled"}
    assert data["fulfilled"]["key"] == x["key"] and x["key"] in oc.upgrade_fulfilled(b.state)
    assert b.fyis() == [] and data["withdrawals"] == []
    assert b.plan(installed="1.0.0")["fulfilled"] is None
    assert oc.upgrade_fulfilled(b.state) == "no request"


def test_expiry_at_seven_days(b: Box) -> None:
    """Now >= expires_at: one FYI, done, no withdrawal."""
    x = _opened(b)
    assert b.plan(now="2026-12-03T06:19:59Z")["open"] is not None
    data = b.plan(now=LATER)
    assert data["open"] is None and data["done"] == {"1.0.0": "expired"}
    assert b.fyis() == [f"OpenRig 1.0.0: upgrade request expired [{x['key']}]"]
    assert data["withdrawals"] == []
    assert b.plan(now=LATER)["open"] is None  # done: never raised again


@pytest.mark.parametrize(
    "reason",
    ["verdict-not-accepted", "integrity-changed", "accept-commit-gone", "installed-at-or-above"],
)
@pytest.mark.parametrize("sent", [True, False])
def test_each_withdrawal_trigger(reason: str, sent: bool, b: Box) -> None:
    """A sent request is withdrawn; a never-sent one is dropped with an FYI only."""
    x = _opened(b, sent)
    kwargs: dict = {}
    if reason == "verdict-not-accepted":
        b.accepted("1.0.0", outcome="REVIEW")
    elif reason == "integrity-changed":
        kwargs["integrity"] = {"1.0.0": OTHER}
    elif reason == "accept-commit-gone":
        _git(b.worktree, "commit", "-q", "--amend", "--allow-empty", "-m", "rewritten")
    else:
        kwargs["installed"] = "1.1.0"
    data = b.plan(**kwargs)
    assert data["open"] is None and data["done"] == {"1.0.0": f"withdrawn:{reason}"}
    assert b.fyis() == [f"OpenRig 1.0.0: upgrade request withdrawn [{x['key']}]: {reason}"]
    word = "withdrawn" if sent else "dropped"
    assert f"upgrade: {word} {x['key']}: {reason}" in b.notes()
    if not sent:
        assert data["withdrawals"] == [] and b.pending() == []
        (name,) = (b.state / "outbox").glob("*.json")
        assert "dropped before sending" in json.loads(name.read_text())["body"]
        return
    ((kind, key, path, last),) = b.pending()
    assert (kind, key, last) == ("withdrawal", x["key"], "-")
    parts = Path(path)
    assert parts.with_suffix(".summary").read_text() == (
        f"OpenRig 1.0.0: WITHDRAWN: do not upgrade [{x['key']}]"
    )
    body = parts.read_text()
    for text in (x["key"], "qitem-20261126000000-aa", reason, "Do not act on any row"):
        assert text in body
    b.send(ok=False, request_id="qitem-20261126000000-bb")
    assert b.pending()[0][3] == "qitem-20261126000000-bb"
    assert b.plan(**kwargs)["withdrawals"][0]["body"] == body  # stored, re-sent until it lands
    b.send()
    assert b.upgrade()["withdrawals"] == []


def test_an_unsent_request_with_an_id_is_withdrawn_not_dropped(b: Box) -> None:
    """Handed to rig once: the operator may have it."""
    b.accepted("1.0.0")
    b.plan()
    b.send(ok=False)
    assert b.plan(installed="1.1.0")["withdrawals"]


def test_a_higher_target_supersedes(b: Box) -> None:
    """Withdraw X, then raise the target in the same run."""
    x = _opened(b)
    b.accepted("1.0.1")
    data = b.plan()
    assert data["done"] == {"1.0.0": "withdrawn:superseded-by-1.0.1"}
    assert data["open"]["version"] == "1.0.1"
    assert [p[0] for p in b.pending()] == ["withdrawal", "request"]
    assert f"[{x['key']}]: superseded-by-1.0.1" in b.fyis()[0]


@pytest.mark.parametrize("unknown", ["installed", "integrity", "integrity-absent"])
@pytest.mark.parametrize("sent", [True, False])
def test_an_unknown_fact_never_withdraws_and_never_sends(unknown: str, sent: bool, b: Box) -> None:
    """The request stays open; an unsent one is held until eligibility can be checked."""
    _opened(b, sent)
    kwargs: dict = {"installed": {"installed": None}, "integrity": {"unknown": True},
                    "integrity-absent": {"integrity": {}}}  # fmt: skip
    arg = kwargs[unknown]
    data = b.plan(**(arg if unknown != "installed" else {"installed": None}))
    assert data["open"] is not None and data["done"] == {} and b.fyis() == []
    assert b.pending() == []
    assert b.plan()["open"] is not None
    assert [p[0] for p in b.pending()] == ([] if sent else ["request"])


def test_an_unreadable_upgrade_state_is_never_overwritten(b: Box) -> None:
    """A broken upgrade.json raises: nothing is raised and nothing is lost."""
    b.accepted("1.0.0")
    (b.state / "upgrade.json").write_text('{"open": 1}', encoding="utf-8")
    with pytest.raises(oc.CompatInputError):
        b.plan()
    assert (b.state / "upgrade.json").read_text() == '{"open": 1}'


# ---------------------------------------------------------------------------------------------
# MR 5 to 7 through the watcher


def _accept_day(b: Box) -> None:
    """The host runs INSTALLED; today CAND passes and is accepted."""
    b.installed(f"{INSTALLED} (abc)")
    b.plan_compat({"verdict": "PASS", "accept": "accepted", "rc": 0, "sha": b.sha})


def _list_candidate(b: Box) -> None:
    """What the accept commit does: CAND joins the worktree's contract file."""
    path = b.worktree / oc.CONTRACT_PATH
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    at = max(i for i, ln in enumerate(lines) if oc._ENTRY_RE.match(ln))
    new = lines[at]
    for version, integrity in _contract_integrities().items():
        if integrity in new:
            new = new.replace(integrity, INTEGRITY).replace(version, CAND)
    lines.insert(at + 1, new)
    path.write_text("".join(lines), encoding="utf-8")
    assert oc.contract_entries(b.worktree)[-1][:2] == (CAND, INTEGRITY)
    b.npm(CAND, {CAND: INTEGRITY})


def _key(b: Box) -> str:
    return f"owr-{CAND}-{b.sha[:12]}"


def test_the_request_row_and_every_body_field(b: Box) -> None:
    """--verify, --expires-at, --tags; the verify block; the before-FYI."""
    _accept_day(b)
    proc = b.day()
    assert proc.returncode == 0, proc.stderr + b.notes()
    (row,) = b.to(OPERATOR)
    key = _key(b)
    assert _summary(row) == (
        f"OpenRig {CAND}: PASS: upgrade the host when idle (from {INSTALLED}) [{key}]"
    )
    assert "--verify" in row["argv"] and _arg(row, "--id") is None
    assert _arg(row, "--expires-at") == LATER
    assert _arg(row, "--tags") == f"openrig-upgrade,openrig:{CAND}"
    assert _arg(row, "--source") == "hacf-openrig-watch@hacf"
    ev = _arg(row, "--evidence-ref")
    digest = hashlib.sha256((Path(ev) / "verdict.json").read_bytes()).hexdigest()
    body = row["body"]
    wanted = (
        f"Request key: {key}",
        "Act only if no WITHDRAWN row for this key exists. A duplicate with the same key is the "
        "same request.",
        "Verify before acting.",
        "claimed, not authenticated",
        f"- evidence {ev}",
        f"`sha256sum {ev}/verdict.json` = {digest}",
        f"`verdict` = `PASS`, `accept.result` = `accepted` and `accept.sha` = `{b.sha}`",
        f"`git -C {b.worktree} merge-base --is-ancestor {b.sha} swimlane/openrig-accepted`",
        f"`npm view @openrig/cli@{CAND} dist.integrity` prints {INTEGRITY}",
        f"Requested: @openrig/cli@{CAND}, from {INSTALLED} (abc).",
        '"upgrade through the supported path (openrig-upgrade skill: backup, upgrade, verify '
        "daemon and every rig healthy, rollback on failure) at a moment when every running rig's "
        'seats are idle between slices. Paused rigs count as idle." The operator decides idleness.',
        "HACF status: accepted locally by the watcher only; installed projects reach "
        f"{CAND} through the range probe until a HACF release lists it.",
        f"Rollback target: {INSTALLED}.",
        f"Do not act after {LATER}.",
        "Close this row with the outcome (upgraded / rolled back / not done); the watcher's next "
        "run reports the host's version to the advisor.",
    )
    positions = [body.index(text) for text in wanted]
    assert positions == sorted(positions)
    before = [r for r in b.to(DESTINATION) if "upgrade requested" in _summary(r)]
    assert len(before) == 1 and "--verify" in before[0]["argv"]
    assert key in before[0]["body"] and "qitem-20261126000000-" in before[0]["body"]
    assert _arg(before[0], "--tags") is None and _arg(before[0], "--expires-at") is None
    assert not any("upgrade the host" in e.read_text() for e in b.state.glob("outbox/*"))
    _list_candidate(b)
    assert b.day().returncode == 0
    assert len(b.to(OPERATOR)) == 1  # one open request


def test_a_failed_request_is_resent_identically_with_its_id(b: Box) -> None:
    """Kept in upgrade.json, re-sent with --id and the same bytes."""
    _accept_day(b)
    b.fail(OPERATOR)
    proc = b.day()
    assert proc.returncode == 1
    assert "row-failed " in b.notes()
    assert not any(r for r in b.to(DESTINATION) if "upgrade requested" in _summary(r))
    _list_candidate(b)
    b.fail(OPERATOR, False)
    assert b.day().returncode == 0
    first, again = b.to(OPERATOR)
    assert _arg(first, "--id") is None
    assert _arg(again, "--id").startswith("qitem-20261126000000-")
    assert again["body"] == first["body"] and _summary(again) == _summary(first)
    assert b.upgrade()["open"]["status"] == "sent"


def test_a_request_gone_stale_before_its_retry_is_dropped(b: Box) -> None:
    """F3: re-checked first. A changed integrity: no re-send."""
    _accept_day(b)
    b.fail(OPERATOR)
    b.day()
    _list_candidate(b)
    b.npm(CAND, {CAND: OTHER})
    b.fail(OPERATOR, False)
    b.day()
    summaries = [_summary(r) for r in b.to(OPERATOR)]
    assert len(summaries) == 2 and "WITHDRAWN: do not upgrade" in summaries[1]
    assert b.upgrade()["done"] == {CAND: "withdrawn:integrity-changed"}
    assert any("withdrawn [" in s for s in b.summaries())


def test_a_term_between_plan_and_send_gives_one_request_next_run(b: Box) -> None:
    """R148."""
    _accept_day(b)
    hang = b.stub / f"rig.hang-{OPERATOR}"
    hang.write_text("", encoding="utf-8")
    env = b.env(HACF_WATCH_UPGRADE_DESTINATION=OPERATOR)
    proc = subprocess.Popen(
        b.argv("--now", NOW), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 60
    while not (b.stub / "rig.hanging").exists():
        assert time.monotonic() < deadline, "the stub rig never started"
        time.sleep(0.1)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=30) == 143
    proc.communicate(timeout=30)
    assert b.to(OPERATOR) == [] and b.upgrade()["open"]["status"] == "unsent"
    body = b.upgrade()["open"]["body"]
    hang.unlink()
    _list_candidate(b)
    b.day()
    b.day()
    (row,) = b.to(OPERATOR)
    assert row["body"] == body


def test_an_npm_failure_raises_and_withdraws_nothing(b: Box) -> None:
    """Integrity unknown: the open request stays."""
    _accept_day(b)
    b.day()
    b.npm(None)
    b.day()
    assert len(b.to(OPERATOR)) == 1 and b.upgrade()["open"]["status"] == "sent"


def test_the_after_fyi_with_a_matching_request(b: Box) -> None:
    """The host reaches the version: the FYI names the request and the self-test."""
    _accept_day(b)
    b.day()
    _list_candidate(b)
    b.installed(f"{CAND} (def)")
    b.plan_compat({"verdict": "PASS", "rc": 0})
    assert b.day().returncode == 0
    (row,) = [r for r in b.rows() if _summary(r).startswith("OpenRig host:")]
    assert _summary(row) == f"OpenRig host: {INSTALLED} -> {CAND}"
    assert _arg(row, "--destination") == DESTINATION and "--verify" in row["argv"]
    assert f"Request: {_key(b)} (request ids: qitem-" in row["body"]
    assert (
        f"checker self-test (HACF's checker against a fresh npm copy of {CAND}, not the host's "
        "install): " in row["body"]
    )
    assert b.upgrade()["done"] == {CAND: "fulfilled"} and len(b.to(OPERATOR)) == 1


def test_the_after_fyi_without_a_request(b: Box) -> None:
    """A change nobody requested; the first run records silently (R141)."""
    version = next(iter(_contract_integrities()))
    b.installed(INSTALLED)
    b.npm(version)
    b.mem(100)
    b.day()
    assert not any(s.startswith("OpenRig host:") for s in b.summaries())
    b.installed(version)
    b.day()
    (row,) = [r for r in b.rows() if _summary(r).startswith("OpenRig host:")]
    assert "Request: no request" in row["body"] and "deferred: gate" in row["body"]
    assert "--verify" in row["argv"]


def test_without_an_upgrade_destination_no_request_row(b: Box) -> None:
    """History says so."""
    _accept_day(b)
    b.day(HACF_WATCH_UPGRADE_DESTINATION=None)
    assert b.to(OPERATOR) == [] and f"upgrade: no destination for {CAND}" in b.notes()


@pytest.mark.parametrize("verdict", ["REVIEW", "FAIL", "SETUP-ERROR"])
def test_no_request_without_an_accept(verdict: str, b: Box) -> None:
    """Only an accepted PASS is eligible."""
    b.installed(INSTALLED)
    b.plan_compat({"verdict": verdict, "rc": oc.VERDICT_EXIT[verdict]})
    b.day()
    assert b.to(OPERATOR) == []


@pytest.mark.parametrize(
    "case", ["npm", "contract", "listed", "final", "gate", "budget", "setup", "fail-pending"]
)
def test_the_tail_steps_run_after_each_early_exit(case: str, b: Box) -> None:
    """F9: the installed version, the self-test and the reconcile after every early exit."""
    b.installed(INSTALLED)
    extra: tuple[str, ...] = ()
    b.plan_compat({"verdict": "REVIEW", "rc": 3})
    if case == "npm":
        b.npm(None)
    elif case == "contract":
        (b.worktree / oc.CONTRACT_PATH).write_text("{", encoding="utf-8")
    elif case == "listed":
        b.npm(next(iter(_contract_integrities())))
    elif case == "final":
        b.day()
        (b.state / "installed.json").unlink()
        (b.state / "upgrade.json").unlink()
    elif case == "gate":
        b.mem(100)
    elif case == "budget":
        extra = ("--deadline", "100")
    elif case == "setup":
        b.plan_compat({"verdict": "SETUP-ERROR", "rc": 2})
    else:
        b.plan_compat({"verdict": "FAIL", "rc": 1, "sleep": 12})
        extra = ("--deadline", "770", "--compat-max", "100")
    proc = b.day(*extra)
    assert proc.returncode == 0, proc.stderr
    assert (b.state / "installed.json").is_file() and (b.state / "upgrade.json").is_file()
    log = (b.state / "run.log").read_text()
    assert log.index("tail: start") < log.index("installed: ")
    assert "selftest " in b.notes()


def test_the_now_seam_is_validated(b: Box) -> None:
    """--now takes one UTC stamp."""
    for bad in ("now", "2026-11-26", "2026-11-26 06:20:00"):
        assert b.run("--now", bad).returncode == 2
