"""Deselected cases, the legend lines, every partly-unknown cell listed, and Known issues."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import pytest

from . import progress_fixture as fx
from .test_costs_and_humans import RIG, card, cards, export, gate
from .test_safety import fake_bin as safety_fake_bin
from .test_safety import fake_bwrap, real_bwrap_works

fake_bin = safety_fake_bin  # the fixture of test_safety.py, shared

HUMAN_LEGEND = (
    "Human decisions: questions / answers, the queue rows tagged hacf:question / hacf:answer "
    "(or their legacy names question-for-tj / tj-answer). Not the same count as §4's Decisions."
)
CASES_LEGEND = (
    "Collected cases are the cases the project's own pytest configuration selects; "
    '"(N deselected)" counts the cases it deselects.'
)
LORE_LEGEND = (
    "ADRs / stories: approved (CANON) lore at each tag; lore approved after a tag appears with "
    "the next release."
)
DECISIONS_LEGEND = (
    "Decisions (recommended / not): queue rows raised with the decision intent. Not the same "
    "count as §1's Human decisions."
)
LEGENDS = {
    "headline": [HUMAN_LEGEND, CASES_LEGEND],
    "per_release": [CASES_LEGEND, LORE_LEGEND],
    "quality": [],
    "human": [DECISIONS_LEGEND],
}
NO_SUPERPRD = "no SuperPRD.md beside the release's RedTeam_Report.md"
SOURCE_TEXT = (
    "pytest --collect-only at the {}, the project's own addopts (marker deselection) in force"
)
TESTS_COLUMN = "Tests (functions / collected cases)"
FIXTURE_OMITS = (
    "tag",
    "src",
    "tests",
    "changelog",
    "changelog_and_progress",
    "version_section",
    "release_progress",
    "table_block",
    "slice_progress",
    "lore",
    "archive",
    "superprd",
)
GIVEN_SOURCES = ("rig", "telemetry", "queue", "junit", "gates", "collect")


def printing(line: str, code: int = 0) -> str:
    """Return a fake bwrap body that prints one summary line and exits with `code`."""
    return f"print({line!r})\nsys.exit({code})"


# the v0.2.0 tree alone has tests/integration, so the two tags collect differently
PER_TAG = (
    "import os\n"
    "tmp = sys.argv[sys.argv.index('--chdir') + 1]\n"
    "if os.path.isdir(os.path.join(tmp, 'tests', 'integration')):\n"
    "    print('7/10 tests collected (3 deselected) in 0.1s')\n"
    "else:\n"
    "    print('405/503 tests collected (98 deselected) in 0.1s')\n"
)


@pytest.fixture
def collect(run, fake_bin: Path, tmp_path: Path):
    """Run with --collect-cases against a fake bwrap with the given body."""

    def go(body: str, omit: str | None = None) -> fx.Report:
        fake_bwrap(fake_bin, tmp_path / "bwrap.log", body)
        return run(omit, "--collect-cases", "--collect-python", sys.executable)

    return go


def assert_cell(report: fx.Report, section: str, column: str, row: str, want: str) -> None:
    """Assert one cell's text in the Markdown and the HTML."""
    assert report.md_cell(section, column, row) == want
    assert report.html_cell(section, column, row)[1] == want


def listed(markdown: str) -> dict:
    """Return {reason: [cell names]} of the Markdown 'Not recorded' list."""
    block = markdown.split("## Not recorded\n", 1)[1].split("\n## ", 1)[0]
    found = {}
    for line in block.splitlines():
        if line.startswith("- "):
            reason, _, names = line[2:].partition(" (cells: ")
            found[reason] = names.removesuffix(")").split("; ")
    return found


# ------------------------------------------------------------------------------ deselected cases


@pytest.mark.parametrize(
    ("line", "code", "cases", "deselected", "unit", "headline"),
    [
        (
            "405/503 tests collected (98 deselected) in 0.1s",
            0,
            405,
            98,
            "2 / 405 (98 deselected)",
            "4 / 405 (98 deselected)",
        ),
        (
            "no tests collected (29 deselected) in 0.1s",
            5,
            0,
            29,
            "2 / 0 (29 deselected)",
            "4 / 0 (29 deselected)",
        ),
        (
            "1000/3000 tests collected in 0.1s",
            0,
            1000,
            2000,
            "2 / 1,000 (2,000 deselected)",
            "4 / 1,000 (2,000 deselected)",
        ),
    ],
)
def test_deselected_cases_are_shown(
    collect, line: str, code: int, cases: int, deselected: int, unit: str, headline: str
) -> None:
    """(a) The selected count stays, and the deselected cases stand beside it."""
    report = collect(printing(line, code))
    metric = report.metric("per_release", "unit", "v0.1.0")
    assert metric["value"] == {
        "functions": 2,
        "cases": cases,
        "by_marker": {},
        "deselected": deselected,
    }
    assert metric["source"].split("; cases: ")[1].startswith(SOURCE_TEXT.format("tag"))
    head = report.metric("headline", "tests", "Headline")
    assert head["value"] == {"functions": 4, "cases": cases, "deselected": deselected}
    assert head["source"].endswith("cases: " + SOURCE_TEXT.format("latest tag"))
    assert_cell(report, "per_release", "Unit", "v0.1.0", unit)
    assert_cell(report, "headline", TESTS_COLUMN, "Headline", headline)


@pytest.mark.parametrize(
    "line", ["3 tests collected in 0.1s", "no tests collected in 0.1s"], ids=["some", "none"]
)
def test_cell_without_deselection_is_unchanged(collect, line: str) -> None:
    """(b) With nothing deselected the cells read as before."""
    report = collect(printing(line))
    cases = 3 if line.startswith("3") else 0
    assert_cell(report, "per_release", "Unit", "v0.1.0", f"2 / {cases}")
    assert_cell(report, "headline", TESTS_COLUMN, "Headline", f"4 / {cases}")


def test_nothing_deselected_records_zero(collect) -> None:
    """(b) A collected count always carries 'deselected', 0 when there is none."""
    report = collect(printing("3 tests collected in 0.1s"))
    assert report.metric("per_release", "unit", "v0.1.0")["value"]["deselected"] == 0
    assert report.metric("headline", "tests", "Headline")["value"]["deselected"] == 0


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("405/503 tests collected (98 deselected) in 0.32s\n", (405, 98)),
        ("2/5 tests collected in 0.01s\n", (2, 3)),
        ("29 tests collected in 0.05s\n", (29, 0)),
        ("no tests collected (29 deselected) in 0.05s\n", (0, 29)),
        ("no tests collected in 0.00s\n", (0, 0)),
        ("1/2 tests collected (1 deselected) in 1s\n4 tests collected in 1s\n", (4, 0)),
        ("nothing useful\n", None),
    ],
)
def test_summary_line_pairs(tool, text: str, want: tuple | None) -> None:
    """MR 1: (selected, deselected) of the last summary line."""
    assert tool.parse_collected(text) == want


def test_by_marker_keeps_the_case_count_only(tool, fake_bin: Path, tmp_path: Path) -> None:
    """A marker's collection records its selected count, no pair."""
    fake_bwrap(fake_bin, tmp_path / "bwrap.log", PER_TAG)
    root = fx.build(tmp_path)
    repo = fx.Repo.__new__(fx.Repo)
    repo.root = root
    marked = '[tool.pytest.ini_options]\nmarkers = ["e2e: x"]\n'
    repo.commit("markers", "2026-01-06T18:00:00+0000", {"pyproject.toml": marked})
    repo.git("tag", "v0.3.0")
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(root), "--out-dir", str(out), "--collect-cases"]
    assert tool.main([*argv, "--collect-python", sys.executable]) == 0
    value = fx.Report(out).metric("per_release", "e2e", "v0.3.0")["value"]
    # Python 3.10 has no tomllib, so no marker can be read there
    assert value["by_marker"] == ({"e2e": 7} if sys.version_info >= (3, 11) else {})
    assert (value["cases"], value["deselected"]) == (7, 3)


def test_headline_and_total_carry_the_latest_tag(collect) -> None:
    """(c) The headline and the LATEST Totals show the latest tag's deselected count."""
    report = collect(PER_TAG)
    assert_cell(report, "per_release", "Unit", "v0.1.0", "2 / 405 (98 deselected)")
    assert_cell(report, "per_release", "Unit", "v0.2.0", "2 / 7 (3 deselected)")
    assert report.metric("per_release", "unit", "Total")["value"]["deselected"] == 3
    assert_cell(report, "per_release", "Unit", "Total", "2 / 7 (3 deselected)")
    assert_cell(report, "per_release", "E2E/acceptance", "Total", "1 / 7 (3 deselected)")
    assert_cell(report, "headline", TESTS_COLUMN, "Headline", "4 / 7 (3 deselected)")


def test_older_value_without_deselected_renders_as_before(tool) -> None:
    """A value without 'deselected' renders as it did."""
    metric = {"value": {"functions": 2, "cases": 3, "by_marker": {}}, "source": "s"}
    assert tool.cell("unit", metric) == "2 / 3"


def host_report(tool, tmp_path: Path, *extra: str, omit: str | None = None) -> fx.Report:
    """Run --host over two fixture projects; the second leaves out `omit`."""
    first = fx.build(tmp_path, name="demo")
    second = fx.build(tmp_path / "b", omit=omit, name="other")
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(first), "--project", str(second), "--host", "--out-dir", str(out)]
    assert tool.main([*argv, *extra]) == 0
    assert tool.main(["--validate", str(out / "progress.json")]) == 0
    return fx.Report(out)


def test_host_sums_the_deselected_cases(tool, fake_bin: Path, tmp_path: Path) -> None:
    """The host row adds the deselected cases beside the selected ones."""
    fake_bwrap(fake_bin, tmp_path / "bwrap.log", PER_TAG)
    report = host_report(tool, tmp_path, "--collect-cases", "--collect-python", sys.executable)
    value = report.data["host"]["headline"]["tests"]["value"]
    assert value == {"functions": 8, "cases": 14, "deselected": 6}
    assert "| 8 / 14 (6 deselected) |" in report.md


def test_host_partly_unknown_cell_is_listed(tool, tmp_path: Path) -> None:
    """A host part that no project records keeps its reason and is listed."""
    report = host_report(tool, tmp_path)
    reason = "collection not run (--collect-cases)"
    assert report.data["host"]["headline"]["tests"]["value"] == {
        "functions": 8,
        "cases": None,
        "cases_reason": reason,
    }
    assert f"Host {TESTS_COLUMN}" in listed(report.md)[reason]


# ------------------------------------------------------------------------------------ the legend


@pytest.mark.parametrize("projects", [1, 2])
def test_legend_lines(run, tool, tmp_path: Path, projects: int) -> None:
    """(d) Each legend line stands once under its section's tables, verbatim."""
    report = run() if projects == 1 else host_report(tool, tmp_path)
    for key, title in fx.SECTIONS.items():
        block = report.md.split(title + "\n", 1)[1].split("\n## ", 1)[0]
        heading = html.escape(title.removeprefix("## "))
        page = report.html.split(f"<h2>{heading}</h2>", 1)[1].split("<h2>", 1)[0]
        after_md = block[block.rindex("\n|") :]
        after_html = html.unescape(page.rsplit("</table>", 1)[1])
        want = LEGENDS[key]
        assert [line for line in after_md.splitlines() if line[2:] in want] == [
            f"- {line}" for line in want
        ]
        for line in want:
            assert block.count(line) == 1 and after_html.count(f"<p>{line}</p>") == 1
        for line in {HUMAN_LEGEND, CASES_LEGEND, LORE_LEGEND, DECISIONS_LEGEND} - set(want):
            assert line not in block and line not in html.unescape(page)
    assert "deselected" not in report.json_text and "Not the same count" not in report.json_text


# ------------------------------------------------------------------------- partly-unknown cells


def test_unresolved_red_team_is_listed(run) -> None:
    """(e) 'N (not recorded)' has its reason and cell name in the list."""
    report = run("superprd")
    names = listed(report.md)[NO_SUPERPRD]
    for row, findings in (("v0.1.0", 3), ("v0.2.0", 1), ("Total", 4)):
        metric = report.metric("quality", "red_team_findings", row)
        assert metric["value"] == {
            "findings": findings,
            "resolved": None,
            "resolved_reason": NO_SUPERPRD,
        }
        assert f"demo {row} Red-team findings (resolved)" in names
        assert_cell(
            report, "quality", "Red-team findings (resolved)", row, f"{findings} (not recorded)"
        )
    assert f"<li>{html.escape(NO_SUPERPRD)} (cells: " in report.html


def test_failed_collection_is_listed(collect) -> None:
    """A failed collection's reason is listed for each cell it leaves partly unknown."""
    report = collect("sys.exit(2)")
    names = listed(report.md)["collection failed at v0.1.0: exit 2"]
    assert "demo v0.1.0 Unit" in names and "demo v0.1.0 E2E/acceptance" in names


def full_arguments(tmp_path: Path, root: Path, without: str | None) -> list:
    """Return every source's arguments but those of `without`."""
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite tests="3" failures="0" errors="0" skipped="0"/>', encoding="utf-8")
    gates = tmp_path / "gates.jsonl"
    gates.write_text(json.dumps(gate("OPR.1.1")) + "\n", encoding="utf-8")
    unpriced = card("0.1.0", usd=(None, 0.25), unknowns=["usd unknown for a model"])
    given = {
        "rig": ["--rig", f"{root}={RIG}"],
        "telemetry": ["--telemetry", str(cards(tmp_path, unpriced, card("0.2.0")))],
        "queue": ["--queue-export", str(export(tmp_path))],
        "junit": ["--junit", f"0.1.0={junit}", "--junit", f"0.2.0={junit}"],
        "gates": ["--gates-file", str(gates)],
        "collect": ["--collect-cases", "--collect-python", sys.executable],
    }
    return [part for name, parts in given.items() if name != without for part in parts]


@pytest.mark.parametrize("without", [None, "collect_fails", *FIXTURE_OMITS, *GIVEN_SOURCES])
def test_every_not_recorded_cell_is_listed(
    tool, fake_bin: Path, tmp_path: Path, without: str | None
) -> None:
    """(f) Whatever source is gone, each cell that says 'not recorded' is listed by name."""
    body = "sys.exit(2)" if without == "collect_fails" else PER_TAG
    fake_bwrap(fake_bin, tmp_path / "bwrap.log", body)
    root = fx.build(tmp_path, without if without in FIXTURE_OMITS else None)
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(root), "--out-dir", str(out), "--host"]
    assert tool.main([*argv, *full_arguments(tmp_path, root, without)]) == 0
    report = fx.Report(out)
    named = {name for names in listed(report.md).values() for name in names}
    _, seen = tool.tables(report.data)
    unknown = {name for name, key, metric in seen if "not recorded" in tool.cell(key, metric)}
    assert unknown, "the fixture always has a cell without a source"
    assert unknown - named == set()
    assert named - unknown == set()
    for names in listed(report.md).values():
        assert len(names) == len(set(names)), names


# ---------------------------------------------------------------------------------- known issues


@pytest.mark.parametrize(
    ("body", "count"),
    [
        ("### Fixed\n- a\n", None),
        ("### Known issues\n", 0),
        ("### Known issues\n\n### Fixed\n- a\n", 0),
        ("### Known issues\n- one\n- two\n", 2),
    ],
)
def test_known_issues_heading(tool, body: str, count: int | None) -> None:
    """(g) No heading is 'not recorded'; an empty heading is 0; bullets are counted."""
    text = f"# Changelog\n\n## [0.1.0] - 2026-01-03\n\n{body}"
    metric = tool.known_issues(tool.changelog_section(text, "0.1.0"), "0.1.0")
    if count is None:
        assert metric == tool.missing("no Known issues heading in CHANGELOG.md [0.1.0]")
    else:
        assert metric["value"] == count


def test_known_issues_without_heading_in_the_report(run) -> None:
    """(g) The release is 'not recorded' with the reason, and its Total names it."""
    report = run()
    reason = "no Known issues heading in CHANGELOG.md [0.2.0]"
    assert report.metric("quality", "known_issues_carried", "v0.2.0")["reason"] == reason
    assert report.metric("quality", "known_issues_carried", "Total")["reason"] == (
        "not recorded for v0.2.0"
    )
    assert listed(report.md)[reason] == ["demo v0.2.0 Known issues carried"]


@pytest.mark.skipif(not real_bwrap_works(), reason="bwrap cannot sandbox on this host")
def test_real_pytest_deselection(tool, tmp_path: Path) -> None:
    """Real pytest, with the project's own addopts, gives both counts, exit 5 included."""
    repo = fx.Repo(tmp_path / "demo")
    config = '[tool.pytest.ini_options]\naddopts = "-m \'not slow\'"\nmarkers = ["slow: s"]\n'
    lines = ["import pytest", "", "", "@pytest.mark.slow", "def test_slow():", "    pass", ""]
    slow = "\n".join(lines)
    repo.commit(
        "tests",
        "2026-01-01T00:00:00+0000",
        {
            "pyproject.toml": config,
            "tests/unit/test_fast.py": "def test_fast():\n    pass\n",
            "tests/unit/test_slow_unit.py": slow,
            "tests/e2e/test_slow_e2e.py": slow,
        },
    )
    repo.git("tag", "v0.1.0")
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(repo.root), "--out-dir", str(out)]
    assert tool.main([*argv, "--collect-cases", "--collect-python", sys.executable]) == 0
    report = fx.Report(out)
    assert_cell(report, "per_release", "Unit", "v0.1.0", "2 / 1 (1 deselected)")
    assert_cell(report, "per_release", "E2E/acceptance", "v0.1.0", "1 / 0 (1 deselected)")
    assert_cell(report, "headline", TESTS_COLUMN, "Headline", "3 / 1 (2 deselected)")
