"""A release that predates a record shows 'not recorded', never a value the record would give."""

from __future__ import annotations

from pathlib import Path

import pytest

from . import progress_fixture as fx

TRACKED_GOLDEN = Path(__file__).resolve().parent / "expected" / "progress_tracked.md"
PREDATES = (
    "v{}'s slice records predate HACF's audit and replan records "
    "(no escape_tracking: v1 marker; HACF v0.6.4)"
)
PARTLY = "only {} of {} audited slices of v{} carry the escape_tracking: v1 marker"
CLEAN = "- t audited - ok\n"
FAILED = "- t audit_failed - x\n" + CLEAN


def record(*lines: str) -> str:
    """Return a slice PROGRESS.md with these Outcomes lines."""
    return "## Outcomes\n\n" + "".join(lines)


def release(version: str, slices: dict) -> dict:
    """Return the files of one release: its slice table and its audited slices' records."""
    base = f"missions/release-{version}"
    files = {f"{base}/PROGRESS.md": fx.table([(name, "audited") for name in slices])}
    for name, text in slices.items():
        files[f"{base}/slices/{name}/PROGRESS.md"] = text
    return files


def report_of(tool, tmp_path: Path, root: Path) -> fx.Report:
    """Run the tool on one project; the run must exit 0 and its JSON must validate."""
    out = tmp_path / "out"
    out.mkdir()
    assert tool.main(["--project", str(root), "--out-dir", str(out)]) == 0
    assert tool.main(["--validate", str(out / "progress.json")]) == 0
    return fx.Report(out)


def project(tmp_path: Path, releases: list) -> Path:
    """Build a project from (version, files, subjects of the commits before the tag)."""
    repo = fx.Repo(tmp_path / "demo")
    repo.commit("root", "2026-01-01T00:00:00+0000", {"README.md": "demo\n"})
    for day, (version, files, subjects) in enumerate(releases, 2):
        for subject in subjects:
            repo.commit(subject, f"2026-01-0{day}T00:00:00+0000")
        repo.commit(f"work for {version}", f"2026-01-0{day}T01:00:00+0000", files)
        repo.git("tag", f"v{version}")
    return repo.root


def counts(report: fx.Report, row: str) -> tuple:
    """Return (replans, first-pass %, rework rounds) of one row."""
    return (
        report.metric("per_release", "slices", row)["value"]["replans"],
        report.metric("per_release", "first_pass_audit_pct", row)["value"],
        report.metric("quality", "audit_rework_rounds", row)["value"],
    )


def listed(report: fx.Report, reason: str) -> str:
    """Return the cell list of one 'Not recorded' reason."""
    block = report.md.split("## Not recorded", 1)[1].split("\n## ", 1)[0]
    line = next(x for x in block.splitlines() if x.startswith(f"- {reason} (cells: "))
    return line.removeprefix(f"- {reason} (cells: ").removesuffix(")")


def test_records_before_the_marker_are_not_recorded(tool, tmp_path: Path) -> None:
    """Unmarked and partly marked releases show no replans, first-pass % or rework."""
    replan = "replan one\n\nHACF-Replan-Of: OPR.1.1"
    root = project(
        tmp_path,
        [
            ("0.1.0", release("0.1.0", {"01-a": record(CLEAN), "02-b": record(CLEAN)}), [replan]),
            (
                "0.2.0",
                release("0.2.0", {"01-c": record(fx.MARKER, CLEAN), "02-d": record(CLEAN)}),
                [],
            ),
            (
                "0.3.0",
                release(
                    "0.3.0",
                    {"01-e": record(fx.MARKER, CLEAN), "02-f": record(fx.MARKER, FAILED)},
                ),
                [replan],
            ),
        ],
    )
    report = report_of(tool, tmp_path, root)
    reasons = {"v0.1.0": PREDATES.format("0.1.0"), "v0.2.0": PARTLY.format(1, 2, "0.2.0")}
    for tag, reason in reasons.items():
        assert report.metric("per_release", "slices", tag)["value"] == {
            "slices": 2,
            "replans": None,
            "replans_reason": reason,
        }
        for section, key, column in (
            ("per_release", "first_pass_audit_pct", "First-pass audit %"),
            ("quality", "audit_rework_rounds", "Audit rework rounds"),
        ):
            assert report.metric(section, key, tag) == {
                "value": None,
                "status": "not recorded",
                "reason": reason,
            }
            assert report.md_cell(section, column, tag) == "not recorded"
            attrs, text = report.html_cell(section, column, tag)
            assert 'class="nr"' in attrs and text == "not recorded"
        for read in (report.md_cell, lambda *a: report.html_cell(*a)[1]):
            assert read("per_release", "Slices (replans)", tag) == "2 (not recorded)"
        assert listed(report, reason).split("; ") == [
            f"demo {tag} Slices (replans)",
            f"demo {tag} First-pass audit %",
            f"demo {tag} Audit rework rounds",
        ]
    assert counts(report, "v0.3.0") == (1, 50.0, 1)
    assert report.md_cell("per_release", "Slices (replans)", "v0.3.0") == "2 (1)"

    gap = "not recorded for v0.1.0, v0.2.0"
    assert report.metric("per_release", "slices", "Total")["value"] == {
        "slices": 6,
        "replans": None,
        "replans_reason": gap,
    }
    assert report.metric("per_release", "first_pass_audit_pct", "Total")["reason"] == gap
    assert report.metric("quality", "audit_rework_rounds", "Total")["reason"] == gap
    assert report.md_cell("per_release", "Slices (replans)", "Total") == "6 (not recorded)"
    assert report.html_cell("per_release", "Slices (replans)", "Total")[1] == "6 (not recorded)"
    assert listed(report, gap).split("; ") == [
        "demo Total Slices (replans)",
        "demo Total First-pass audit %",
        "demo Total Audit rework rounds",
    ]
    assert report.project["headline"]["slices"]["value"] == 6


@pytest.mark.parametrize(
    "marker",
    [
        '- t executing - {"base":"abc","escape_tracking":"v1"}\n',
        '- t executing - {"base": "abc", "escape_tracking": "v1"}\n',
    ],
    ids=["compact", "spaced"],
)
def test_json_marker_counts_and_look_alikes_do_not(tool, tmp_path: Path, marker: str) -> None:
    """The JSON form is tracked; another version or another event's line is not."""
    others = {
        "01-b": record("- t executing - base: abc escape_tracking: v10\n", CLEAN),
        "02-c": record("- t executed - escape_tracking: v1\n", CLEAN),
    }
    root = project(
        tmp_path,
        [
            ("0.1.0", release("0.1.0", {"01-a": record(marker, CLEAN)}), []),
            ("0.2.0", release("0.2.0", others), []),
        ],
    )
    report = report_of(tool, tmp_path, root)
    assert counts(report, "v0.1.0") == (0, 100.0, 0)
    assert counts(report, "v0.2.0") == (None, None, None)
    assert report.metric("quality", "audit_rework_rounds", "v0.2.0")["reason"] == (
        PREDATES.format("0.2.0")
    )


def test_a_tracked_release_keeps_a_true_zero(tool, tmp_path: Path) -> None:
    """With the marker and no replan or failure, 0, 100.0 and 0 are real values."""
    slices = {"01-a": record(fx.MARKER, CLEAN)}
    root = project(tmp_path, [("0.1.0", release("0.1.0", slices), [])])
    report = report_of(tool, tmp_path, root)
    for row in ("v0.1.0", "Total"):
        assert counts(report, row) == (0, 100.0, 0)
        assert report.md_cell("per_release", "Slices (replans)", row) == "1 (0)"


def test_tracked_fixture_report_is_unchanged(tool, tmp_path: Path) -> None:
    """The marked fixture gives the report as it was: no other metric changes."""
    report = report_of(tool, tmp_path, fx.build(tmp_path, tracked=True))
    assert (report.out / "progress.md").read_bytes() == TRACKED_GOLDEN.read_bytes()
    assert counts(report, "v0.1.0") == (0, 50.0, 1)
    assert counts(report, "v0.2.0") == (2, 100.0, 0)
    assert counts(report, "Total") == (2, 66.7, 1)
    assert report.html_cell("per_release", "Slices (replans)", "Total")[1] == "4 (2)"


def changelog_report(tool, tmp_path: Path, body: str, table: bool) -> fx.Report:
    """Run on one release whose CHANGELOG section holds `body`."""
    files = {"CHANGELOG.md": f"# Changelog\n\n## [0.1.0]\n\n{body}"}
    if table:
        files.update(release("0.1.0", {"01-a": record(CLEAN), "02-b": record(CLEAN)}))
    return report_of(tool, tmp_path, project(tmp_path, [("0.1.0", files, [])]))


def test_no_added_heading_falls_back_to_the_audited_slices(tool, tmp_path: Path) -> None:
    """A section without an Added heading counts the audited slices, and says so."""
    report = changelog_report(tool, tmp_path, "### Fixed\n- a fix\n", table=True)
    assert report.metric("per_release", "features", "v0.1.0") == {
        "value": 2,
        "source": "fallback: audited slices in missions/release-0.1.0/PROGRESS.md",
    }


def test_no_added_heading_and_no_table_is_not_recorded(tool, tmp_path: Path) -> None:
    """With neither an Added heading nor a slice table, features are not recorded."""
    report = changelog_report(tool, tmp_path, "### Fixed\n- a fix\n", table=False)
    reason = "no Added heading in CHANGELOG.md [0.1.0] and no slice table for v0.1.0"
    assert report.metric("per_release", "features", "v0.1.0") == {
        "value": None,
        "status": "not recorded",
        "reason": reason,
    }
    assert report.md_cell("per_release", "Features", "v0.1.0") == "not recorded"
    assert "demo v0.1.0 Features" in listed(report, reason)


def test_an_empty_added_heading_is_zero(tool, tmp_path: Path) -> None:
    """A heading with no bullets is a recorded 0, even beside a slice table."""
    report = changelog_report(tool, tmp_path, "### Added\n\n### Fixed\n- a fix\n", table=True)
    assert report.metric("per_release", "features", "v0.1.0") == {
        "value": 0,
        "source": "CHANGELOG.md [0.1.0] '### Added' bullets at the tag",
    }
