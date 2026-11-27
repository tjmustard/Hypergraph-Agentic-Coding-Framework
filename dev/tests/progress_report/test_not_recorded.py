"""Remove one source at a time: the cell is 'not recorded' in the JSON, the MD and the HTML."""

from __future__ import annotations

import pytest

COLUMNS = {
    "span_days": "Span (days)",
    "source_lines_net": "Source lines (net)",
    "test_lines_net": "Test lines (net)",
    "tests": "Tests (functions / collected cases)",
    "features_shipped": "Features shipped",
    "human_decisions": "Human decisions",
    "source_lines": "Source +/−",
    "test_lines": "Test +/−",
    "unit": "Unit",
    "integration": "Integration",
    "features": "Features",
    "slices": "Slices (replans)",
    "first_pass_audit_pct": "First-pass audit %",
    "adrs_stories": "ADRs / stories",
    "tokens_cost": "Tokens / cost",
    "tests_passing_collected": "Tests passing / collected",
    "lint_format_types": "Lint / format / types",
    "licence_check": "Licence check",
    "red_team_findings": "Red-team findings (resolved)",
    "audit_rework_rounds": "Audit rework rounds",
    "regressions_caught": "Regressions caught",
    "known_issues_carried": "Known issues carried",
    "questions": "Questions to the human",
    "decisions": "Decisions (recommended / not)",
    "phase3_gates": "Phase 3 gates",
    "lore": "Lore approved / proposed",
}


def assert_not_recorded(report, section: str, key: str, row: str = "v0.1.0") -> None:
    """Assert one cell is 'not recorded' in all three outputs, with its reason listed."""
    metric = report.metric(section, key, row)
    assert set(metric) == {"value", "status", "reason"}, metric
    assert metric["value"] is None and metric["status"] == "not recorded" and metric["reason"]
    assert report.md_cell(section, COLUMNS[key], row) == "not recorded"
    attrs, text = report.html_cell(section, COLUMNS[key], row)
    assert 'class="nr"' in attrs and text == "not recorded"
    assert f"- {metric['reason']}" in report.md.split("## Not recorded", 1)[1]


@pytest.mark.parametrize(
    ("omit", "section", "key", "row"),
    [
        ("tag", "headline", "span_days", "Headline"),
        ("tag", "headline", "source_lines_net", "Headline"),
        ("tag", "per_release", "commits_total", "Total"),
        ("src", "per_release", "source_lines", "v0.1.0"),
        ("src", "headline", "source_lines_net", "Headline"),
        ("tests", "per_release", "test_lines", "v0.1.0"),
        ("tests", "per_release", "unit", "v0.1.0"),
        ("tests", "headline", "tests", "Headline"),
        (None, "per_release", "integration", "v0.1.0"),  # the kind dir is absent at that tag
        ("changelog", "quality", "known_issues_carried", "v0.1.0"),
        ("changelog_and_progress", "per_release", "features", "v0.1.0"),
        ("changelog_and_progress", "headline", "features_shipped", "Headline"),
        ("version_section", "quality", "known_issues_carried", "v0.1.0"),
        ("release_progress", "per_release", "slices", "v0.1.0"),
        ("release_progress", "quality", "audit_rework_rounds", "v0.1.0"),
        ("table_block", "per_release", "slices", "v0.1.0"),
        ("slice_progress", "per_release", "first_pass_audit_pct", "v0.1.0"),
        ("slice_progress", "quality", "audit_rework_rounds", "v0.1.0"),
        ("lore", "per_release", "adrs_stories", "v0.1.0"),
        ("lore", "human", "lore", "v0.1.0"),
        ("archive", "quality", "red_team_findings", "v0.1.0"),
        (None, "per_release", "tokens_cost", "v0.1.0"),
        (None, "quality", "tests_passing_collected", "v0.1.0"),
        (None, "quality", "lint_format_types", "v0.1.0"),
        (None, "quality", "licence_check", "v0.1.0"),
        (None, "quality", "regressions_caught", "v0.1.0"),
        (None, "human", "questions", "v0.1.0"),
        (None, "human", "decisions", "v0.1.0"),
        (None, "human", "phase3_gates", "v0.1.0"),
        (None, "headline", "human_decisions", "Headline"),
    ],
)
def test_source_removed(run, omit, section: str, key: str, row: str) -> None:
    """The named cell is 'not recorded' when its source is gone."""
    report = run(omit)
    if key == "commits_total":  # no tag: no release row, every Total is 'not recorded'
        assert report.project["releases"] == []
        assert report.project["headline"]["releases"]["value"] == 0
        for part in report.project["totals"].values():
            for metric in part.values():
                assert metric["value"] is None, metric
        return
    assert_not_recorded(report, section, key, row)


@pytest.mark.parametrize("omit", ["changelog", "version_section"])
def test_features_fall_back_to_audited_slices(run, omit: str) -> None:
    """Without a CHANGELOG section the features are the audited slices, and the source says so."""
    metric = run(omit).metric("per_release", "features", "v0.1.0")
    assert metric == {
        "value": 2,
        "source": "fallback: audited slices in missions/release-0.1.0/PROGRESS.md",
    }


def test_resolved_alone_is_not_recorded_without_superprd(run) -> None:
    """Without a SuperPRD.md only the resolved count is unknown."""
    report = run("superprd")
    for row in ("v0.1.0", "Total"):
        metric = report.metric("quality", "red_team_findings", row)
        assert metric["value"]["resolved"] is None
        want = f"{metric['value']['findings']} (not recorded)"
        assert report.md_cell("quality", "Red-team findings (resolved)", row) == want
        assert report.html_cell("quality", "Red-team findings (resolved)", row)[1] == want


def test_cases_not_recorded_without_collect(run) -> None:
    """Without --collect-cases the cases half is unknown and the source says why."""
    report = run()
    metric = report.metric("per_release", "unit", "v0.1.0")
    assert metric["value"] == {
        "functions": 2,
        "cases": None,
        "by_marker": {},
        "cases_reason": "collection not run (--collect-cases)",
    }
    assert "collection not run (--collect-cases)" in metric["source"]
    assert report.md_cell("per_release", "Unit", "v0.1.0") == "2 / not recorded"
    assert report.html_cell("per_release", "Unit", "v0.1.0")[1] == "2 / not recorded"


def test_headline_zero_releases_renders(run) -> None:
    """A project with no tag still writes all three files."""
    report = run("tag")
    assert report.md_cell("headline", "Releases", "Headline") == "0"
    assert "**Total**" in report.md
