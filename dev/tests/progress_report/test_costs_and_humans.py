"""Tokens and cost, the human Q&A, test results and Phase 3 gates (OPR.99.0.15.2 MR 2-7)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from . import progress_fixture as fx
from .test_not_recorded import assert_not_recorded
from .test_safety import GIT_VERBS, exit_code, script
from .test_safety import fake_bin as safety_fake_bin

RIG = "demo-hacf"
WINDOWS = {
    "0.1.0": ("2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"),
    "0.2.0": ("2026-01-03T00:00:00Z", "2026-01-06T12:00:00Z"),
}
LABEL = "list-price equivalent"
QUEUE_ARGV = ["queue", "list", "-A", "-a", "--json", "--limit", "20000"]
SENTINEL = "SENTINEL-ROW-TEXT-91c2"
fake_bin = safety_fake_bin  # the fixture of test_safety.py, shared


def card(version: str, usd: tuple = (1.25, 0.25), without: int | None = 0, **change) -> dict:
    """Return a scorecard of one release: seat a with two slice entries, seat b with one."""
    since, until = WINDOWS[version]
    first, second = usd
    made = {
        "schema": "hacf.telemetry/v1",
        "rig": RIG,
        "label": f"v{version}",
        "window": {"since": since, "until": until},
        "seats": [
            {
                "seat": f"a@{RIG}",
                "slices": [
                    {"slice_id": "OPR.1.1", "total": 1000, "usd": first},
                    {"slice_id": "unattributed", "total": 200, "usd": second},
                ],
            },
            {"seat": f"b@{RIG}", "slices": [{"slice_id": "OPR.1.2", "total": 34, "usd": 0.5}]},
        ],
        "unknowns": [],
    }
    if without is not None:
        made["samples"] = {
            f"a@{RIG}": {"context": 5, "context_without_session": without, "provider_window": 9},
            f"b@{RIG}": {"context": 1, "context_without_session": 0, "provider_window": 0},
        }
    made.update(change)
    return made


def cards(tmp_path: Path, *made: dict) -> Path:
    """Write the scorecards into a telemetry dir and return it."""
    folder = tmp_path / "telemetry"
    folder.mkdir()
    for number, content in enumerate(made):
        (folder / f"card{number}.json").write_text(json.dumps(content), encoding="utf-8")
    return folder


def row(
    number: int,
    when: str,
    tags: list | None,
    rig: str = RIG,
    intent: str | None = None,
    **change,
) -> dict:
    """Return one compact queue row, with text keys the report must drop."""
    made = {
        "qitemId": f"qitem-{number}",
        "tsCreated": when,
        "sourceSession": f"orch-lead@{rig}",
        "destinationSession": "advisor-lead@kernel",
        "tags": tags,
        "humanIntent": intent,
        "state": "done",
        "summary": SENTINEL,
        "body": SENTINEL,
    }
    made.update(change)
    return made


ONE = "2026-01-02T00:00:00.000Z"
TWO = "2026-01-04T00:00:00.000Z"


def rows() -> list:
    """Return rows of two rigs, both tag namings, a release tag, a late row and a decision."""
    return [
        row(1, ONE, ["question-for-tj"]),
        row(2, ONE, ["tj-answer"]),
        row(3, ONE, ["hacf:question"], intent="decision"),
        # the release tag wins over the creation time
        row(4, TWO, ["hacf:answer", "release:0.1.0"]),
        row(5, TWO, ["hacf:question", "release:v0.2.0"]),
        row(6, "2026-01-03T00:00:00.000Z", ["hacf:question"]),  # (since, until]: v0.1.0
        row(7, "2026-01-07T00:00:00.000Z", ["hacf:question"]),  # after the latest tag
        row(8, TWO, None, intent="decision"),
        # a summary is never read, and no other tag counts
        row(9, ONE, ["question", "answer", "hacf:update"], summary="a question for tj"),
        row(10, ONE, ["hacf:question"], rig="other"),
        row(11, ONE, ["hacf:question"], rig=f"x-{RIG}"),
        # in by its rig: tag alone
        row(12, TWO, ["hacf:answer", f"rig:{RIG}"], rig="kernel"),
        # in by its destination alone
        row(13, TWO, ["tj-answer"], rig="kernel", destinationSession=f"orch-lead@{RIG}"),
        row(14, "2025-12-31T00:00:00.000Z", ["hacf:question"]),  # before the first window
    ]


def export(tmp_path: Path, content: object = None) -> Path:
    """Write a queue export file."""
    path = tmp_path / "queue.json"
    text = (
        content if isinstance(content, str) else json.dumps(rows() if content is None else content)
    )
    path.write_text(text, encoding="utf-8")
    return path


def junit(path: Path, tests: int, failures: int = 0, errors: int = 0, skipped: int = 0) -> Path:
    """Write a pytest junit XML file with one suite under <testsuites>."""
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="pytest" tests="{tests}" failures="{failures}" errors="{errors}" '
        f'skipped="{skipped}"></testsuite></testsuites>',
        encoding="utf-8",
    )
    return path


def gates(tmp_path: Path, *lines: object) -> Path:
    """Write a gates.jsonl; a string line is written as it is."""
    path = tmp_path / "gates.jsonl"
    text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    path.write_text(text, encoding="utf-8")
    return path


def gate(slice_id: str, name: str = "novel_fixture_needed") -> dict:
    """Return one gate record."""
    return {"gate": name, "slice": slice_id, "reason": "r", "at": "2026-01-02T00:00:00+00:00"}


@pytest.fixture
def go(tool, tmp_path: Path):
    """Run on the fixture project with --rig; return the Report."""

    def run_it(*extra: str, rig: bool = True, omit: str | None = None) -> fx.Report:
        root = fx.build(tmp_path, omit)
        out = tmp_path / "out"
        out.mkdir()
        argv = ["--project", str(root), "--out-dir", str(out), *extra]
        if rig:
            argv += ["--rig", f"{root}={RIG}"]
        assert tool.main(argv) == 0
        assert tool.main(["--validate", str(out / "progress.json")]) == 0
        return fx.Report(out)

    return run_it


def refused(tool, tmp_path: Path, *extra: str, code: int = 1) -> None:
    """Assert the run exits with `code` and writes nothing."""
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(root), "--out-dir", str(out), "--rig", f"{root}={RIG}", *extra]
    assert exit_code(tool, argv) == code
    assert list(out.iterdir()) == []


# ------------------------------------------------------------------------------ tokens and cost


def test_scorecard_match(go, tmp_path: Path) -> None:
    """The matching scorecard gives the release's tokens, cost and seats; the Total adds up."""
    folder = cards(tmp_path, card("0.1.0"), card("0.2.0", usd=(2.0, 0.0)))
    report = go("--telemetry", str(folder))
    metric = report.metric("per_release", "tokens_cost", "v0.1.0")
    assert metric == {
        "value": {
            "tokens": 1234,
            "usd": 2.0,
            "price_basis": LABEL,
            "by_seat": {
                f"a@{RIG}": {"tokens": 1200, "usd": 1.5},
                f"b@{RIG}": {"tokens": 34, "usd": 0.5},
            },
            "unattributed_samples": 0,
        },
        "source": "hacf.telemetry/v1 scorecard card0.json",
    }
    total = report.metric("per_release", "tokens_cost", "Total")["value"]
    assert (total["tokens"], total["usd"], total["price_basis"]) == (2468, 4.5, LABEL)
    assert total["by_seat"][f"a@{RIG}"] == {"tokens": 2400, "usd": 3.5}
    assert total["unattributed_samples"] == 0
    for label, want in (
        ("v0.1.0", f"1,234 / $2.00 {LABEL}"),
        ("Total", f"2,468 / $4.50 {LABEL}"),
    ):
        assert report.md_cell("per_release", "Tokens / cost", label) == want
        assert report.html_cell("per_release", "Tokens / cost", label)[1] == want
    assert LABEL in report.json_text
    assert "token samples without a session" not in report.md + report.html


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"label": "v0.9.0"}, f"no scorecard v0.1.0 for rig {RIG}"),
        ({"rig": "other"}, f"no scorecard v0.1.0 for rig {RIG}"),
        (
            {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-03T00:00:01Z"}},
            "scorecard window differs",
        ),
        (
            {"window": {"since": "2025-12-31T23:59:59Z", "until": "2026-01-03T00:00:00Z"}},
            "scorecard window differs",
        ),
        ({"schema": "hacf.telemetry.aggregate/v1"}, f"no scorecard v0.1.0 for rig {RIG}"),
        (
            {"unknowns": ["token totals unknown: no seat or session source in the database"]},
            "token totals unknown: no seat or session source in the database",
        ),
    ],
)
def test_scorecard_not_used(go, tmp_path: Path, change: dict, reason: str) -> None:
    """A scorecard of another label, rig, window or kind, or without totals, is not used."""
    folder = cards(tmp_path, card("0.1.0", **change))
    (folder / "notes.json").write_text("not json", encoding="utf-8")
    (folder / "list.json").write_text("[1]", encoding="utf-8")
    (folder / "sub.json").mkdir()
    report = go("--telemetry", str(folder))
    assert_not_recorded(report, "per_release", "tokens_cost")
    assert report.metric("per_release", "tokens_cost", "v0.1.0")["reason"] == reason
    assert report.metric("per_release", "tokens_cost", "Total")["value"] is None


def empty_card(version: str, **change) -> dict:
    """Return a scorecard with seats but no record in its window: what 0 totals once meant."""
    seats = [f"a@{RIG}", f"b@{RIG}"]
    made = card(
        version,
        seats=[{"seat": seat, "slices": []} for seat in seats],
        context={seat: {"occupants": [], "peak_max": None, "turns": 0} for seat in seats},
        samples={
            seat: {"context": 0, "context_without_session": 0, "provider_window": 8}
            for seat in seats
        },
    )
    made.update(change)
    return made


def assert_release_not_recorded(report: fx.Report, label: str, reason: str) -> None:
    """One release's tokens and cost is 'not recorded' in all three outputs, never a 0."""
    metric = report.metric("per_release", "tokens_cost", label)
    assert metric["value"] is None and metric["reason"] == reason
    assert report.md_cell("per_release", "Tokens / cost", label) == "not recorded"
    assert report.html_cell("per_release", "Tokens / cost", label)[1] == "not recorded"
    assert f"- {reason}" in report.md.split("## Not recorded", 1)[1]
    assert "$0.00" not in report.md + report.html + report.json_text


@pytest.mark.parametrize("drop", [(), ("context",), ("samples",), ("context", "samples")])
def test_scorecard_without_data_is_not_recorded(go, tmp_path: Path, drop: tuple) -> None:
    """(h) No token, no turn and no context sample is 'not recorded', not 0 / $0.00."""
    bare = empty_card("0.2.0")
    for key in drop:
        del bare[key]
    report = go("--telemetry", str(cards(tmp_path, card("0.1.0"), bare)))
    assert_release_not_recorded(
        report, "v0.2.0", "the scorecard holds no telemetry record in this release's window"
    )


def test_scorecard_with_no_record_unknown(go, tmp_path: Path) -> None:
    """(i) The collector's own reason is the one shown."""
    why = "token totals unknown: no transcript record in the window (14 sessions read)"
    folder = cards(tmp_path, card("0.1.0"), empty_card("0.2.0", unknowns=[why]))
    assert_release_not_recorded(go("--telemetry", str(folder)), "v0.2.0", why)


def test_release_without_data_makes_the_total_unknown(go, tmp_path: Path) -> None:
    """(j) The Total names the release it lacks and is never a partial sum."""
    report = go("--telemetry", str(cards(tmp_path, card("0.1.0"), empty_card("0.2.0"))))
    assert report.metric("per_release", "tokens_cost", "v0.1.0")["value"]["tokens"] == 1234
    total = report.metric("per_release", "tokens_cost", "Total")
    assert total["value"] is None and total["reason"] == "not recorded for v0.2.0"
    assert report.md_cell("per_release", "Tokens / cost", "Total") == "not recorded"


@pytest.mark.parametrize("sign", ["tokens", "turns", "context samples"])
def test_scorecard_with_any_record_keeps_its_value(go, tmp_path: Path, sign: str) -> None:
    """(k) One token, one turn or one context sample is data: the value renders as before."""
    made = empty_card("0.2.0")
    if sign == "tokens":
        made["seats"][0]["slices"] = [{"slice_id": "OPR.1.1", "total": 7, "usd": 0.5}]
    elif sign == "turns":
        made["context"][f"a@{RIG}"]["turns"] = 1
    else:
        made["samples"][f"a@{RIG}"]["context"] = 1
    report = go("--telemetry", str(cards(tmp_path, card("0.1.0"), made)))
    value = report.metric("per_release", "tokens_cost", "v0.2.0")["value"]
    assert value["tokens"] == (7 if sign == "tokens" else 0)


def test_two_matching_scorecards(go, tmp_path: Path) -> None:
    """Two scorecards of one release are not chosen between."""
    report = go("--telemetry", str(cards(tmp_path, card("0.1.0"), card("0.1.0"))))
    assert_not_recorded(report, "per_release", "tokens_cost")
    metric = report.metric("per_release", "tokens_cost", "v0.1.0")
    assert metric["reason"] == "2 scorecards for v0.1.0"


def test_one_release_without_telemetry_makes_the_total_unknown(go, tmp_path: Path) -> None:
    """The Total of tokens and cost follows the Total rule."""
    report = go("--telemetry", str(cards(tmp_path, card("0.1.0"))))
    total = report.metric("per_release", "tokens_cost", "Total")
    assert total["value"] is None and total["reason"] == "not recorded for v0.2.0"
    assert report.md_cell("per_release", "Tokens / cost", "Total") == "not recorded"


def test_null_usd(go, tmp_path: Path) -> None:
    """One entry without a price leaves the release's cost unknown, with the card's reasons."""
    why = f"a@{RIG}: OPR.1.1: usd unknown: no price for m-9"
    folder = cards(
        tmp_path,
        card("0.1.0", usd=(None, 0.25), unknowns=[why, "unrelated"]),
        card("0.2.0"),
    )
    report = go("--telemetry", str(folder))
    value = report.metric("per_release", "tokens_cost", "v0.1.0")["value"]
    assert value["tokens"] == 1234 and value["usd"] is None
    assert value["usd_reasons"] == [why]
    assert value["by_seat"][f"a@{RIG}"] == {"tokens": 1200, "usd": None}
    assert value["by_seat"][f"b@{RIG}"] == {"tokens": 34, "usd": 0.5}
    total = report.metric("per_release", "tokens_cost", "Total")["value"]
    assert total["tokens"] == 2468 and total["usd"] is None
    want = f"1,234 / not recorded {LABEL}"
    assert report.md_cell("per_release", "Tokens / cost", "v0.1.0") == want
    assert report.html_cell("per_release", "Tokens / cost", "v0.1.0")[1] == want
    assert f"- {why}" in report.md.split("## Not recorded", 1)[1]
    assert "$" not in report.md_cell("per_release", "Tokens / cost", "v0.1.0")


def test_scorecard_without_prices(go, tmp_path: Path) -> None:
    """A scorecard made without --prices has no usd: unknown, with that reason."""
    bare = card("0.1.0")
    for seat in bare["seats"]:
        for entry in seat["slices"]:
            del entry["usd"]
    report = go("--telemetry", str(cards(tmp_path, bare)))
    value = report.metric("per_release", "tokens_cost", "v0.1.0")["value"]
    assert value["usd"] is None
    assert value["usd_reasons"] == ["the scorecard carries no usd (made without --prices)"]


def test_unattributed_samples_note(go, tmp_path: Path) -> None:
    """Sessionless token samples are named under section 2 in the Markdown and the HTML."""
    folder = cards(tmp_path, card("0.1.0", without=3), card("0.2.0", without=None))
    report = go("--telemetry", str(folder))
    one = report.metric("per_release", "tokens_cost", "v0.1.0")["value"]
    two = report.metric("per_release", "tokens_cost", "v0.2.0")["value"]
    assert one["unattributed_samples"] == 3 and two["unattributed_samples"] is None
    total = report.metric("per_release", "tokens_cost", "Total")["value"]
    assert total["unattributed_samples"] is None
    note = "v0.1.0: 3 token samples without a session: their tokens are not recorded"
    for text, end in ((report.md, "## 3."), (report.html, "<h2>3.")):
        start = text.index("2. Per release")
        assert note in text[start : text.index(end)]
        assert text.count(note) == 1 and "v0.2.0: None" not in text
        assert LABEL in text


def test_telemetry_hint(go) -> None:
    """Each release carries the collect_metrics arguments that would make its scorecard."""
    report = go()
    since, until = WINDOWS["0.1.0"]
    assert report.project["releases"][0]["telemetry_hint"] == (
        f"--rig {RIG} --label v0.1.0 --since {since} --until {until} "
        "--repo <project root> --prices <prices.yml>"
    )


def test_telemetry_hint_without_rig(go) -> None:
    """Without --rig the hint leaves the rig as a placeholder."""
    report = go(rig=False)
    assert report.project["releases"][0]["telemetry_hint"].startswith("--rig <rig> --label ")


@pytest.mark.parametrize("bad", [{"seats": 3}, {"seats": [{"seat": "a", "slices": [{}]}]}])
def test_malformed_matching_scorecard_exits_1(tool, tmp_path: Path, bad: dict) -> None:
    """A release's own scorecard that cannot be read is exit 1."""
    refused(tool, tmp_path, "--telemetry", str(cards(tmp_path, card("0.1.0", **bad))))


def test_missing_telemetry_dir_exits_1(tool, tmp_path: Path) -> None:
    """A telemetry dir that cannot be listed is exit 1."""
    refused(tool, tmp_path, "--telemetry", str(tmp_path / "absent"))


# --------------------------------------------------------------------------------- the queue


def assert_queue_numbers(report: fx.Report) -> None:
    """Assert every queue cell of the export of rows()."""
    # v0.1.0: questions 1, 3, 6; answers 2, 4. v0.2.0: question 5; answers 12, 13.
    want = {"v0.1.0": (3, 1, 2), "v0.2.0": (1, 1, 2), "Total": (4, 2, 4)}
    for label, (questions, decisions, _) in want.items():
        assert report.metric("human", "questions", label)["value"] == questions, label
        assert report.metric("human", "decisions", label)["value"] == {
            "decisions": decisions,
            "recommended": None,
            "not_recommended": None,
        }
        text = f"{decisions} (not recorded / not recorded)"
        assert report.md_cell("human", "Decisions (recommended / not)", label) == text
        assert report.html_cell("human", "Decisions (recommended / not)", label)[1] == text
    headline = report.metric("headline", "human_decisions", "Headline")
    assert headline["value"] == {"questions": 4, "answers": 4}
    assert report.md_cell("headline", "Human decisions", "Headline") == "4 / 4"
    assert report.html_cell("headline", "Human decisions", "Headline")[1] == "4 / 4"
    assert report.project["after_latest_tag"] == 1
    listed = report.md.split("## Not recorded", 1)[1].split("## Sources", 1)[0]
    assert "- the queue records no chosen option (OpenRig 0.7.23)" in listed
    assert "the queue records no chosen option (OpenRig 0.7.23)" in report.html
    assert SENTINEL not in report.json_text + report.md + report.html
    assert "qitem-" not in report.json_text + report.md + report.html


def test_queue_export(go, tmp_path: Path) -> None:
    """The tag rule, the release rule and the project rule on an export file."""
    assert_queue_numbers(go("--queue-export", str(export(tmp_path))))


def test_zero_counts_are_values(go, tmp_path: Path) -> None:
    """With a tagged row in the project, a release without any shows 0."""
    report = go("--queue-export", str(export(tmp_path, [row(1, ONE, ["tj-answer"])])))
    assert report.metric("human", "questions", "v0.2.0")["value"] == 0
    assert report.md_cell("human", "Questions to the human", "v0.2.0") == "0"
    assert report.metric("headline", "human_decisions", "Headline")["value"] == {
        "questions": 0,
        "answers": 1,
    }


def fake_rig(bin_dir: Path, log: Path, body: str) -> None:
    """Install a `rig` that records its argv and then runs `body`."""
    (bin_dir / "rig").unlink(missing_ok=True)
    script(
        bin_dir / "rig",
        "import json, sys, time\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n{body}\n",
    )


def test_queue_rig(go, tmp_path: Path, fake_bin: Path) -> None:
    """--queue rig runs exactly the read-only list command, once."""
    log = tmp_path / "rig.log"
    source = export(tmp_path)
    fake_rig(fake_bin, log, f"sys.stdout.write(open({str(source)!r}).read())")
    report = go("--queue", "rig")
    assert [json.loads(line) for line in log.read_text().splitlines()] == [QUEUE_ARGV]
    assert_queue_numbers(report)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("sys.exit(3)", "rig queue list failed: 3"),
        ("print('oops')", "rig queue list failed: bad json"),
        ("print('{}')", "rig queue list failed: bad json"),
        ("print('[1]')", "rig queue list failed: bad json"),
        ("print(json.dumps([{}] * 20000))", "queue list truncated at 20000"),
        ("time.sleep(30)", "rig queue list failed: timeout"),
    ],
)
def test_queue_rig_failures(
    tool, go, tmp_path: Path, fake_bin: Path, monkeypatch, body: str, reason: str
) -> None:
    """A failed, unreadable or truncated list leaves every queue cell unknown, with exit 0."""
    if "sleep" in body:
        monkeypatch.setattr(tool, "QUEUE_TIMEOUT", 0.2)
    fake_rig(fake_bin, tmp_path / "rig.log", body)
    report = go("--queue", "rig")
    assert_queue_unknown(report, reason)


def assert_queue_unknown(report: fx.Report, reason: str) -> None:
    """Assert every queue cell is 'not recorded' with the reason, in all three outputs."""
    for section, key, label in (
        ("human", "questions", "v0.1.0"),
        ("human", "decisions", "v0.1.0"),
        ("human", "questions", "Total"),
        ("human", "decisions", "Total"),
        ("headline", "human_decisions", "Headline"),
    ):
        assert_not_recorded(report, section, key, label)
        assert report.metric(section, key, label)["reason"] == reason, (key, label)


@pytest.mark.parametrize(
    ("content", "reason"),
    [("nonsense", "rig queue list failed: bad json"), ("[]", None)],
)
def test_queue_export_unreadable_or_untagged(
    go, tmp_path: Path, content: str, reason: str | None
) -> None:
    """Bad JSON in an export is unknown; an export without a tagged row says so."""
    report = go("--queue-export", str(export(tmp_path, content)))
    untagged = "no row carries the hacf:question/hacf:answer tags or their legacy names"
    assert_queue_unknown(report, reason or untagged)


def test_missing_queue_export_exits_1(tool, tmp_path: Path) -> None:
    """An export file that cannot be read is exit 1."""
    refused(tool, tmp_path, "--queue-export", str(tmp_path / "absent.json"))


def test_two_queue_sources_exit_2(tool, tmp_path: Path) -> None:
    """--queue and --queue-export are two sources: one at a time."""
    refused(tool, tmp_path, "--queue", "rig", "--queue-export", str(export(tmp_path)), code=2)
    assert exit_code(tool, ["--project", ".", "--out-dir", ".", "--queue", "sqlite"]) == 2


# --------------------------------------------------------------------------- junit and gates


def test_junit(go, tmp_path: Path) -> None:
    """Passing and collected per release, two files added; the Total is the latest tag's."""
    first = junit(tmp_path / "unit.xml", 10, failures=1, errors=2, skipped=3)
    second = tmp_path / "e2e.xml"
    second.write_text(
        '<testsuite tests="5" failures="0" errors="0" skipped="0"/>', encoding="utf-8"
    )
    latest = junit(tmp_path / "later.xml", 7)
    report = go(
        "--junit", f"0.1.0={first}", "--junit", f"0.1.0={second}", "--junit", f"0.2.0={latest}"
    )
    assert report.metric("quality", "tests_passing_collected", "v0.1.0") == {
        "value": {"passing": 9, "collected": 15},
        "source": "junit unit.xml, e2e.xml",
    }
    assert report.metric("quality", "tests_passing_collected", "Total") == {
        "value": {"passing": 7, "collected": 7},
        "source": "junit later.xml",
    }
    assert report.md_cell("quality", "Tests passing / collected", "v0.1.0") == "9 / 15"
    assert report.html_cell("quality", "Tests passing / collected", "Total")[1] == "7 / 7"


def test_version_without_junit(go, tmp_path: Path) -> None:
    """A release without a --junit file is unknown while the other is a value."""
    report = go("--junit", f"0.1.0={junit(tmp_path / 'a.xml', 2)}")
    assert_not_recorded(report, "quality", "tests_passing_collected", "v0.2.0")
    assert report.metric("quality", "tests_passing_collected", "v0.2.0")["reason"] == (
        "no test-result record (HACF's pre-push hook records none)"
    )


@pytest.mark.parametrize(
    "text",
    [
        "<testsuites><testsuite",
        '<testsuite tests="1" failures="0" errors="0"/>',
        '<testsuite tests="x" failures="0" errors="0" skipped="0"/>',
        "<testsuites/>",
        "<report/>",
    ],
)
def test_bad_junit_exits_1(tool, tmp_path: Path, text: str) -> None:
    """A junit file that does not parse or lacks the counts is exit 1."""
    path = tmp_path / "bad.xml"
    path.write_text(text, encoding="utf-8")
    refused(tool, tmp_path, "--junit", f"0.1.0={path}")


@pytest.mark.parametrize("item", ["0.1.0", "v0.1.0=x", "0.1=x", "0.1.0="])
def test_junit_usage(tool, tmp_path: Path, item: str) -> None:
    """--junit takes X.Y.Z=FILE."""
    refused(tool, tmp_path, "--junit", item, code=2)


def test_gates(go, tmp_path: Path) -> None:
    """Novel-fixture gates count in the releases whose slice table names them."""
    first = gates(tmp_path, gate("OPR.1.2"), gate("OPR.1.3"), gate("OPR.9.9"), "")
    second = tmp_path / "more.jsonl"
    second.write_text(
        json.dumps(gate("OPR.1.1")) + "\n" + json.dumps(gate("OPR.1.2", "low_confidence")) + "\n",
        encoding="utf-8",
    )
    report = go("--gates-file", str(first), "--gates-file", str(second))
    # the fixture's v0.1.0 table holds OPR.1.1-1.3 and v0.2.0's holds OPR.1.1
    for label, count in (("v0.1.0", 3), ("v0.2.0", 1), ("Total", 4)):
        assert report.metric("human", "phase3_gates", label)["value"] == count, label
        assert report.md_cell("human", "Phase 3 gates", label) == str(count)
        assert report.html_cell("human", "Phase 3 gates", label)[1] == str(count)
    assert report.project["gates_unmapped"] == 1


def test_gates_without_a_slice_table(go, tmp_path: Path) -> None:
    """A release without a slice table cannot map a gate."""
    report = go("--gates-file", str(gates(tmp_path, gate("OPR.1.1"))), omit="table_block")
    assert_not_recorded(report, "human", "phase3_gates", "v0.1.0")
    assert report.metric("human", "phase3_gates", "v0.1.0")["reason"] == (
        "no slice table in missions/release-0.1.0/PROGRESS.md at v0.1.0"
    )


@pytest.mark.parametrize(
    "line", ["not json", "[1]", '{"gate": "x", "slice": "y", "reason": "z"}', '{"gate": 1}']
)
def test_bad_gates_line_exits_1(tool, tmp_path: Path, line: str) -> None:
    """A malformed gate line is exit 1."""
    refused(tool, tmp_path, "--gates-file", str(gates(tmp_path, gate("OPR.1.1"), line)))


def test_missing_gates_file_exits_1(tool, tmp_path: Path) -> None:
    """A gates file that cannot be read is exit 1."""
    refused(tool, tmp_path, "--gates-file", str(tmp_path / "absent.jsonl"))


@pytest.mark.parametrize("flag", ["--junit", "--gates-file"])
def test_per_project_records_take_one_project(
    tool, tmp_path: Path, capsys, monkeypatch, flag: str
) -> None:
    """A record that names no project is refused beside two projects, before any read."""
    other = fx.build(tmp_path / "b", name="other")
    # neither file exists: reading it would exit 1, not 2
    given = {"--junit": f"0.1.0={tmp_path / 'absent.xml'}", "--gates-file": str(tmp_path / "no")}

    def no_git(*args, **kwargs):
        raise AssertionError("a project was read")

    monkeypatch.setattr(tool.subprocess, "run", no_git)
    root = tmp_path / "demo"
    root.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(root), "--project", str(other), "--out-dir", str(out)]
    assert exit_code(tool, [*argv, flag, given[flag]]) == 2
    assert f"error: {flag} needs exactly one --project\n" in capsys.readouterr().err
    assert list(out.iterdir()) == []


# ------------------------------------------------------------------------------- the schema


def test_new_keys_are_optional_and_typed(go, tool, tmp_path: Path, capsys) -> None:
    """The three new keys validate, may be absent, and no other key is let in."""
    report = go("--queue-export", str(export(tmp_path)), "--gates-file", str(gates(tmp_path)))
    schema = json.loads(fx.SCHEMA_FILE.read_text(encoding="utf-8"))
    added = (("release", "telemetry_hint", "string"), ("project", "after_latest_tag", "integer"),
             ("project", "gates_unmapped", "integer"))  # fmt: skip
    for part, key, kind in added:
        spec = schema["$defs"][part]
        assert spec["properties"][key]["type"] == kind, key
        assert key not in spec["required"] and spec["additionalProperties"] is False
    assert schema["$id"] == "hacf.progress-report/v1"
    full = json.loads(report.json_text)
    assert full["projects"][0]["after_latest_tag"] == 1
    assert full["projects"][0]["gates_unmapped"] == 0
    assert full["projects"][0]["releases"][0]["telemetry_hint"]

    def check(model: dict) -> tuple:
        path = tmp_path / "model.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        capsys.readouterr()
        return tool.main(["--validate", str(path)]), capsys.readouterr().out

    assert check(full) == (0, "ok\n")
    old = json.loads(report.json_text)
    del old["projects"][0]["after_latest_tag"], old["projects"][0]["gates_unmapped"]
    for release in old["projects"][0]["releases"]:
        del release["telemetry_hint"]
    assert check(old) == (0, "ok\n")
    for spot, key, bad in (
        ("project", "extra", 1),
        ("release", "extra", 1),
        ("release", "after_latest_tag", 1),
        ("project", "telemetry_hint", "x"),
        ("project", "after_latest_tag", "1"),
        ("project", "gates_unmapped", None),
        ("release", "telemetry_hint", 1),
    ):
        broken = json.loads(report.json_text)
        target = broken["projects"][0]
        if spot == "release":
            target = target["releases"][0]
        target[key] = bad
        code, out = check(broken)
        assert code == 1 and f".{key}: " in out, (spot, key, out)


def test_without_sources_no_count_is_written(go) -> None:
    """A count with no source is absent, never zero."""
    project = go().project
    assert "after_latest_tag" not in project and "gates_unmapped" not in project


# --------------------------------------------------------------- one 'not recorded' per source


@pytest.mark.parametrize(
    ("missing", "section", "key", "row_label", "reason"),
    [
        ("telemetry", "per_release", "tokens_cost", "v0.1.0", "no telemetry source given"),
        ("queue", "human", "questions", "v0.1.0", "no queue source given"),
        ("queue", "headline", "human_decisions", "Headline", "no queue source given"),
        ("rig", "human", "decisions", "v0.1.0", "no --rig for demo"),
        (
            "tags",
            "human",
            "questions",
            "v0.1.0",
            "no row carries the hacf:question/hacf:answer tags or their legacy names",
        ),
        (
            "junit",
            "quality",
            "tests_passing_collected",
            "v0.1.0",
            "no test-result record (HACF's pre-push hook records none)",
        ),
        ("gates", "human", "phase3_gates", "v0.1.0", "no Phase 3 gate record given"),
    ],
)
def test_one_source_missing(
    go, tmp_path: Path, missing: str, section: str, key: str, row_label: str, reason: str
) -> None:
    """With every other source given, the missing source's cell is 'not recorded' everywhere."""
    untagged = [row(1, ONE, ["other"])]
    options = {
        "telemetry": [
            "--telemetry",
            str(cards(tmp_path, card("0.1.0"), card("0.2.0"))),
        ],
        "queue": ["--queue-export", str(export(tmp_path, untagged if missing == "tags" else None))],
        "junit": ["--junit", f"0.1.0={junit(tmp_path / 'a.xml', 2)}"],
        "gates": ["--gates-file", str(gates(tmp_path, gate("OPR.1.2")))],
    }
    options.pop(missing, None)
    argv = [item for values in options.values() for item in values]
    report = go(*argv, rig=missing != "rig")
    assert_not_recorded(report, section, key, row_label)
    assert report.metric(section, key, row_label)["reason"] == reason


# ----------------------------------------------------------- safety, with every new option


def every_option(tmp_path: Path) -> list:
    """Return every new option but --queue rig, which excludes --queue-export."""
    return [
        "--telemetry",
        str(cards(tmp_path, card("0.1.0", without=2), card("0.2.0"))),
        "--junit",
        f"0.1.0={junit(tmp_path / 'a.xml', 2)}",
        "--gates-file",
        str(gates(tmp_path, gate("OPR.1.2"))),
    ]


@pytest.mark.parametrize("queue", ["export", "rig"])
def test_offline_zero_token_and_project_unchanged(
    tool, tmp_path: Path, fake_bin: Path, monkeypatch, queue: str
) -> None:
    """With every new source: no network, no model tool, no project byte changed, no stray write."""

    def refuse(*args, **kwargs):
        raise AssertionError("network use")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    source = export(tmp_path)
    extra = every_option(tmp_path)
    log = tmp_path / "rig.log"
    if queue == "rig":
        fake_rig(fake_bin, log, f"sys.stdout.write(open({str(source)!r}).read())")
        extra += ["--queue", "rig"]
    else:
        extra += ["--queue-export", str(source)]
    calls = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(tool.subprocess, "run", spy)
    inputs = [source, tmp_path / "telemetry", tmp_path / "a.xml", tmp_path / "gates.jsonl"]
    before = fx.tree_hash(root)
    kept = {path for base in inputs for path in [base, *base.rglob("*")]}
    contents = {path: path.read_bytes() for path in kept if path.is_file()}
    argv = ["--project", str(root), "--rig", f"{root}={RIG}", "--out-dir", str(out)]
    assert tool.main([*argv, "--host", *extra]) == 0
    assert fx.tree_hash(root) == before
    assert {path: path.read_bytes() for path in contents} == contents
    assert sorted(p.name for p in out.iterdir()) == [
        "progress.html",
        "progress.json",
        "progress.md",
    ]
    assert not (tmp_path / "calls.log").exists()
    others = [c for c in calls if c[0] != "git"]
    assert others == ([["rig", *QUEUE_ARGV]] if queue == "rig" else [])
    assert all(c[4] in GIT_VERBS for c in calls if c[0] == "git")
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text and os.environ["HOME"] not in text
        assert LABEL in text and SENTINEL not in text
    assert fx.Report(out).data["host"]["headline"]["human_decisions"]["value"] == {
        "questions": 4,
        "answers": 4,
    }


def test_command_line_runs_with_sources(tmp_path: Path) -> None:
    """The program reads junit XML under a plain interpreter."""
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            str(fx.TOOL),
            "--project",
            str(root),
            "--out-dir",
            str(out),
            "--junit",
            f"0.1.0={junit(tmp_path / 'a.xml', 2)}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
