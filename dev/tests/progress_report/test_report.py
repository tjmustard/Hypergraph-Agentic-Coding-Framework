"""The golden report, every number in it, the Total rows, the host roll-up and the schema."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from . import progress_fixture as fx

GOLDEN = Path(__file__).resolve().parent / "expected" / "progress.md"

LINES_1 = {"added": 3, "removed": 0, "binary_files": 1}
LINES_2 = {"added": 3, "removed": 1, "binary_files": 0}
NOT_RUN = "collection not run (--collect-cases)"
PREDATES = (
    "v{}'s slice records predate HACF's audit and replan records "
    "(no escape_tracking: v1 marker; HACF v0.6.4)"
)


def untracked(slices: int, reason: str) -> dict:
    """Return a slices value whose replans are not recorded."""
    return {"slices": slices, "replans": None, "replans_reason": reason}


def kind(functions: int) -> dict:
    """Return a per-kind value without collection."""
    return {"functions": functions, "cases": None, "by_marker": {}, "cases_reason": NOT_RUN}


# (section, key) -> (v0.1.0, v0.2.0, Total); None is 'not recorded'.
EXPECTED = {
    ("per_release", "date"): ("2026-01-03", "2026-01-06", None),
    ("per_release", "days"): (2.0, 3.5, 5.5),
    ("per_release", "commits"): (2, 3, 5),
    ("per_release", "source_lines"): (
        LINES_1,
        LINES_2,
        {"added": 6, "removed": 1, "binary_files": 1},
    ),
    ("per_release", "test_lines"): (
        {"added": 9, "removed": 0, "binary_files": 0},
        {"added": 2, "removed": 0, "binary_files": 0},
        {"added": 11, "removed": 0, "binary_files": 0},
    ),
    ("per_release", "unit"): (kind(2), kind(2), kind(2)),
    ("per_release", "integration"): (None, kind(1), kind(1)),
    ("per_release", "e2e"): (kind(1), kind(1), kind(1)),
    ("per_release", "features"): (2, 1, 3),
    ("per_release", "slices"): (
        untracked(3, PREDATES.format("0.1.0")),
        untracked(1, PREDATES.format("0.2.0")),
        untracked(4, "not recorded for v0.1.0, v0.2.0"),
    ),
    # the fixture's records carry no marker; test_absent_sources pins the marked values
    ("per_release", "first_pass_audit_pct"): (None, None, None),
    ("per_release", "adrs_stories"): (
        {"adrs": 1, "stories": 0},
        {"adrs": 2, "stories": 1},
        {"adrs": 2, "stories": 1},
    ),
    ("per_release", "tokens_cost"): (None, None, None),
    ("quality", "tests_passing_collected"): (None, None, None),
    ("quality", "lint_format_types"): (None, None, None),
    ("quality", "licence_check"): (None, None, None),
    ("quality", "red_team_findings"): (
        {"findings": 3, "resolved": 2},
        {"findings": 1, "resolved": 1},
        {"findings": 4, "resolved": 3},
    ),
    ("quality", "audit_rework_rounds"): (None, None, None),
    ("quality", "regressions_caught"): (None, None, None),
    ("quality", "known_issues_carried"): (1, None, None),  # 0.2.0 has no heading
    ("human", "questions"): (None, None, None),
    ("human", "decisions"): (None, None, None),
    ("human", "phase3_gates"): (None, None, None),
    ("human", "lore"): (
        {"approved": 1, "proposed": 3},
        {"approved": 2, "proposed": 1},
        {"approved": 3, "proposed": 4},
    ),
}


@pytest.mark.parametrize(("section", "key"), sorted(EXPECTED))
def test_every_release_and_total_number(run, section: str, key: str) -> None:
    """Each metric of both releases and the Total equals the fixture's known count."""
    report = run()
    for row, want in zip(("v0.1.0", "v0.2.0", "Total"), EXPECTED[(section, key)], strict=True):
        metric = report.metric(section, key, row)
        if want is None and not (key == "date" and row == "Total"):
            assert metric["value"] is None and metric["status"] == "not recorded", (row, metric)
        elif key == "date" and row == "Total":
            assert metric["value"] == {"first": "2026-01-03", "last": "2026-01-06"}
        else:
            assert metric["value"] == want, (row, metric)
            assert metric["source"], (row, metric)


def test_headline_numbers(run) -> None:
    """The headline row equals the fixture's known counts."""
    headline = run().project["headline"]
    values = {k: m["value"] for k, m in headline.items()}
    assert values == {
        "releases": 2,
        "span_days": 5.5,
        "source_lines_net": 5,
        "test_lines_net": 11,
        "tests": {"functions": 4, "cases": None, "cases_reason": NOT_RUN},
        "features_shipped": 3,
        "slices": 4,
        "human_decisions": None,
    }
    assert headline["human_decisions"]["reason"] == "no queue source given"


def test_release_identity_and_window(run) -> None:
    """A release records its tag, commit and window; the first window starts at the root."""
    report = run()
    one, two = report.project["releases"]
    assert (one["version"], one["tag"], two["version"]) == ("0.1.0", "v0.1.0", "0.2.0")
    assert re.fullmatch(r"[0-9a-f]{40}", one["commit"]) and one["commit"] != two["commit"]
    assert one["window"] == {"since": "2026-01-01T00:00:00Z", "until": "2026-01-03T00:00:00Z"}
    assert two["window"] == {"since": "2026-01-03T00:00:00Z", "until": "2026-01-06T12:00:00Z"}
    assert report.project["head"] == two["commit"]
    assert report.data["schema"] == "hacf.progress-report/v1"
    assert report.data["generated_at"] == "2026-01-07T00:00:00Z"
    assert report.data["host"] is None and report.project["rig"] is None


def test_golden_markdown(run) -> None:
    """progress.md equals the golden byte for byte."""
    assert (run().out / "progress.md").read_bytes() == GOLDEN.read_bytes()


def test_markdown_cells_and_headings(run) -> None:
    """The template headings and header lines are verbatim, with the rendered cells."""
    report = run()
    for heading in fx.SECTIONS.values():
        assert f"\n{heading}\n" in report.md
    for header in (
        "| Project | Releases | Span (days) | Source lines (net) | Test lines (net) | "
        "Tests (functions / collected cases) | Features shipped | Slices | Human decisions |",
        "| Version | Date | Days | Commits | Source +/− | Test +/− | Unit | Integration | "
        "E2E/acceptance | Features | Slices (replans) | First-pass audit % | ADRs / stories | "
        "Tokens / cost |",
        "| Version | Tests passing / collected | Lint / format / types | Licence check | "
        "Red-team findings (resolved) | Audit rework rounds | Regressions caught | "
        "Known issues carried |",
        "| Version | Questions to the human | Decisions (recommended / not) | Phase 3 gates | "
        "Lore approved / proposed |",
    ):
        assert header in report.md
    cell = report.md_cell
    assert cell("per_release", "Date", "Total") == "2026-01-03 → 2026-01-06"
    assert cell("per_release", "Source +/−", "v0.2.0") == "3 / 1"
    assert cell("per_release", "Unit", "v0.1.0") == "2 / not recorded"
    assert cell("per_release", "Integration", "v0.1.0") == "not recorded"
    assert cell("per_release", "Slices (replans)", "Total") == "4 (not recorded)"
    assert cell("per_release", "ADRs / stories", "Total") == "2 / 1"
    assert cell("quality", "Red-team findings (resolved)", "v0.1.0") == "3 (2)"
    assert cell("human", "Lore approved / proposed", "Total") == "3 / 4"
    assert cell("headline", "Tests (functions / collected cases)", "Headline") == (
        "4 / not recorded"
    )
    assert report.html_cell("per_release", "Slices (replans)", "Total")[1] == "4 (not recorded)"
    assert "## Not recorded" in report.md and "## Sources" in report.md


def test_html_is_self_contained(run) -> None:
    """The HTML has no script and no external resource, and the same tables as the Markdown."""
    page = run().html
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page and "prefers-color-scheme" in page
    assert "<script" not in page and "http://" not in page and "https://" not in page
    for heading in fx.SECTIONS.values():
        assert heading.removeprefix("## ") in page


def test_thousands_separator_and_escaping(tool) -> None:
    """Integers render with separators, and HTML text and Markdown pipes are escaped."""
    model = {
        "schema": "hacf.progress-report/v1",
        "generated_at": "2026-01-07T00:00:00Z",
        "projects": [],
        "host": {
            "projects": ["a<b|c"],
            "headline": {
                key: {"value": 1234567, "source": "<s>"}
                for key in ("releases", "features_shipped", "slices")
            }
            | {
                key: {"value": None, "status": "not recorded", "reason": "r<"}
                for key in (
                    "span_days",
                    "source_lines_net",
                    "test_lines_net",
                    "tests",
                    "human_decisions",
                )
            },
        },
    }
    md, page = tool.render_markdown(model), tool.render_html(model)
    assert "1,234,567" in md and "1,234,567" in page
    assert "&lt;s&gt;" in page and "<s>" not in page and "r&lt;" in page


def test_total_is_not_recorded_when_a_release_is(run) -> None:
    """A release with a 'not recorded' cell makes that Total 'not recorded' and names it."""
    report = run("version_section")
    total = report.metric("quality", "known_issues_carried", "Total")
    assert total == {
        "value": None,
        "status": "not recorded",
        "reason": "not recorded for v0.1.0, v0.2.0",
    }
    assert report.md_cell("quality", "Known issues carried", "Total") == "not recorded"


def test_host_rollup(tool, tmp_path: Path) -> None:
    """--host sums the projects, takes the largest span and names a project with a gap."""
    first = fx.build(tmp_path, name="demo")
    second = fx.build(tmp_path / "b", omit="changelog_and_progress", name="other")
    out = tmp_path / "out"
    out.mkdir()
    argv = ["--project", str(first), "--project", str(second), "--host", "--out-dir", str(out)]
    assert tool.main([*argv, "--rig", f"{first}=demo-hacf"]) == 0
    report = fx.Report(out)
    host = report.data["host"]
    assert host["projects"] == ["demo", "other"]
    assert [p["rig"] for p in report.data["projects"]] == ["demo-hacf", None]
    head = host["headline"]
    assert head["releases"]["value"] == 4
    assert head["span_days"]["value"] == 5.5
    assert head["source_lines_net"]["value"] == 10
    assert head["tests"]["value"] == {"functions": 8, "cases": None, "cases_reason": NOT_RUN}
    assert head["features_shipped"]["reason"] == "not recorded for other"
    assert head["slices"]["reason"] == "not recorded for other"
    assert "| Host | 4 | 5.5 | 10 | 22 | 8 / not recorded | not recorded |" in report.md
    assert tool.main(["--validate", str(out / "progress.json")]) == 0


def test_source_and_test_path_options(run) -> None:
    """--source-path, --test-path, --exclude and --kind change what is counted."""
    report = run(None, "--source-path", "tests/unit", "--exclude", "tests/unit/helper.py")
    assert report.metric("per_release", "source_lines", "v0.1.0")["value"]["added"] == 6


def test_kind_option_replaces_the_default_dirs(run) -> None:
    """--kind unit=<dir> replaces the unit dirs."""
    report = run(None, "--kind", "unit=tests/e2e")
    assert report.metric("per_release", "unit", "v0.1.0")["value"]["functions"] == 1


def test_schema_file_matches_the_model(run, tool) -> None:
    """The schema parses, names the contract, and lists every key of every section."""
    schema = json.loads(fx.SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["$id"] == "hacf.progress-report/v1"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "hacf.progress-report/v1"
    assert len(schema["$defs"]["metric"]["oneOf"]) == 2
    project = run().project
    for section, keys in (
        ("headline", project["headline"]),
        ("per_release", project["releases"][0]["per_release"]),
        ("quality", project["releases"][0]["quality"]),
        ("human", project["releases"][0]["human"]),
    ):
        spec = schema["$defs"][section]
        assert list(spec["properties"]) == list(keys) == spec["required"]
        assert {v["$ref"] for v in spec["properties"].values()} == {"#/$defs/metric"}


def test_validate(run, tool, tmp_path: Path, capsys) -> None:
    """--validate passes the produced JSON and names the path of a bad metric."""
    report = run()
    good = report.out / "progress.json"
    assert tool.main(["--validate", str(good)]) == 0
    assert capsys.readouterr().out == "ok\n"
    broken = json.loads(report.json_text)
    broken["projects"][0]["releases"][1]["quality"]["red_team_findings"] = {"value": None}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(broken), encoding="utf-8")
    before = sorted(tmp_path.rglob("*"))
    assert tool.main(["--validate", str(bad)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("$.projects[0].releases[1].quality.red_team_findings: ")
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "text", ["not json", "[]", '{"schema": "x"}', '{"projects": [1], "host": 3}']
)
def test_validate_refuses_other_shapes(tool, tmp_path: Path, capsys, text: str) -> None:
    """A file of another shape exits 1 with one line per problem, never a traceback."""
    path = tmp_path / "x.json"
    path.write_text(text, encoding="utf-8")
    assert tool.main(["--validate", str(path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("$") and str(tmp_path) not in out
