"""Lore status from the lore graph at each tag (OPR.99.0.15.2 MR 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from . import progress_fixture as fx
from .test_not_recorded import assert_not_recorded

GRAPH = "lore_hypergraph.yml"
GRAPH_SOURCE = "lore graph (lore_hypergraph.yml) at the tag"
LINE_SOURCE = "each file's Status line at the tag (no lore graph)"


def node(identifier: str, kind: str, status: str) -> str:
    """Return one node as hyper_lore.dump_graph writes it, with nested and folded values."""
    return (
        f"- id: {identifier}\n"
        f"  type: {kind}\n"
        "  title: a title long enough to fold onto a second line, which mentions\n"
        "    status: REJECTED and - id: nothing\n"
        f"  status: {status}\n"
        "  anchors:\n"
        "    components:\n"
        "    - a\n"
        "  provenance:\n"
        "    type: nested\n"
        "    status: nested\n"
    )


def graph(*nodes: str) -> str:
    """Return a lore graph holding the nodes."""
    body = "".join(nodes) if nodes else ""
    head = "schema: hacf.lore/v1\nid_counters:\n  ADR: 9\n"
    return head + ("nodes:\n" + body if nodes else "nodes: []\n") + "edges:\n- from: x\n"


def run_on(tool, tmp_path: Path, *releases: dict) -> fx.Report:
    """Tag one commit per file set as v0.1.0, v0.2.0, ... and report on the repository."""
    repo = fx.Repo(tmp_path / "demo")
    for number, files in enumerate(releases, 1):
        repo.commit(f"work {number}", f"2026-01-0{number}T00:00:00+0000", files)
        repo.git("tag", f"v0.{number}.0")
    out = tmp_path / "out"
    out.mkdir()
    assert tool.main(["--project", str(repo.root), "--out-dir", str(out)]) == 0
    return fx.Report(out)


def check_cells(report: fx.Report, label: str, adrs_stories: tuple, change: tuple) -> None:
    """Assert both lore cells of a release in JSON, MD and HTML."""
    adrs = report.metric("per_release", "adrs_stories", label)
    assert adrs["value"] == dict(zip(("adrs", "stories"), adrs_stories)), adrs
    lore = report.metric("human", "lore", label)
    assert lore["value"] == dict(zip(("approved", "proposed"), change)), lore
    for section, column, pair in (
        ("per_release", "ADRs / stories", adrs_stories),
        ("human", "Lore approved / proposed", change),
    ):
        want = f"{pair[0]} / {pair[1]}"
        assert report.md_cell(section, column, label) == want
        assert report.html_cell(section, column, label)[1] == want


def test_graph_wins_over_status_lines(tool, tmp_path: Path) -> None:
    """With a graph at the tag its statuses count, whatever the files' Status lines say."""
    report = run_on(
        tool,
        tmp_path,
        {
            f"lore/ADRs/{fx.adr(1)}-one.md": fx.lore_doc("Accepted"),
            f"lore/ADRs/{fx.adr(2)}-two.md": "# no status line\n",
            f"lore/ADRs/{fx.adr(3)}-three.md": fx.lore_doc("Accepted"),
            GRAPH: graph(
                node(fx.adr(1), "decision", "PROPOSED"),
                node(fx.adr(2), "decision", "CANON"),
                node(fx.adr(3), "decision", "REJECTED"),
                node(fx.adr(4), "decision", "SUPERSEDED"),
                node(fx.adr(5), "decision", "RETIRED"),
                node(fx.story(1), "story", "CANON"),
                node(f"CANON-{1:04d}", "canon", "CANON"),
            ),
        },
    )
    # approved: every node CANON now; proposed: new ids that are CANON or PROPOSED
    check_cells(report, "v0.1.0", (1, 1), (3, 4))
    assert report.metric("per_release", "adrs_stories", "v0.1.0")["source"] == GRAPH_SOURCE
    assert report.metric("human", "lore", "v0.1.0")["source"].startswith(GRAPH_SOURCE)
    for text in (report.md, report.html):
        assert GRAPH_SOURCE in text.replace("&#x27;", "'")
        assert LINE_SOURCE not in text.replace("&#x27;", "'")


def test_a_graph_alone_is_read(tool, tmp_path: Path) -> None:
    """A tag with the graph and no lore/ is read from the graph; an empty graph counts zero."""
    report = run_on(
        tool,
        tmp_path,
        {GRAPH: graph()},
        {GRAPH: graph(node(fx.story(1), "story", "CANON"))},
    )
    check_cells(report, "v0.1.0", (0, 0), (0, 0))
    check_cells(report, "v0.2.0", (0, 1), (1, 1))


def test_status_lines_without_a_graph(tool, tmp_path: Path) -> None:
    """Without a graph the Status lines count as before, and the label says so."""
    report = run_on(
        tool,
        tmp_path,
        {
            f"lore/ADRs/{fx.adr(1)}-one.md": fx.lore_doc("ACCEPTED"),
            f"lore/ADRs/{fx.adr(2)}-two.md": "# no status line\n",
        },
    )
    check_cells(report, "v0.1.0", (1, 0), (1, 2))
    assert report.metric("per_release", "adrs_stories", "v0.1.0")["source"] == LINE_SOURCE
    assert report.metric("human", "lore", "v0.1.0")["source"].startswith(LINE_SOURCE)


def assert_lore_unknown(report: fx.Report, label: str, reason: str) -> None:
    """Assert both lore cells are 'not recorded' with the reason, in all three outputs."""
    for section, key in (("per_release", "adrs_stories"), ("human", "lore")):
        assert_not_recorded(report, section, key, label)
        assert report.metric(section, key, label)["reason"] == reason


@pytest.mark.parametrize(
    "files",
    [
        {f"lore/ADRs/{fx.adr(1)}-one.md": "# no status line\n"},
        {"lore/MAP.md": "# map\n"},
    ],
)
def test_no_graph_and_no_status_line(tool, tmp_path: Path, files: dict) -> None:
    """No graph and no Status line: neither lore cell has a source."""
    report = run_on(tool, tmp_path, files)
    assert_lore_unknown(report, "v0.1.0", "no lore graph and no Status line at v0.1.0")


def test_neither_lore_nor_graph(tool, tmp_path: Path) -> None:
    """The 0.15.1 reason stays for a tag with neither lore/ nor the graph."""
    report = run_on(tool, tmp_path, {"README.md": "x\n"})
    assert_lore_unknown(report, "v0.1.0", "no lore/ at v0.1.0")


@pytest.mark.parametrize(
    "text",
    [
        "nodes:\n- {id: X-1, type: decision, status: CANON}\n",
        "nodes:\n  - id: X-1\n    type: decision\n    status: CANON\n",
        "nodes: [{id: X-1, type: decision, status: CANON}]\n",
        "nodes:\n- id: X-1\n  type: decision\n",
        "nodes:\n- id: X-1\n  status: CANON\n",
        "nodes:\n- type: decision\n  status: CANON\n",
        "nodes:\n- id: X-1\n  type: decision\n  status: ACCEPTED\n",
        "schema: hacf.lore/v1\n",
    ],
)
def test_graph_not_in_written_form(tool, tmp_path: Path, text: str) -> None:
    """Another form is 'not recorded', and the Status lines are never read instead."""
    report = run_on(
        tool,
        tmp_path,
        {f"lore/ADRs/{fx.adr(1)}-one.md": fx.lore_doc("ACCEPTED"), GRAPH: text},
        {GRAPH: graph(node(fx.adr(1), "decision", "CANON"))},
    )
    assert_lore_unknown(
        report, "v0.1.0", "lore graph at v0.1.0 is not in hyper_lore.py's written form"
    )
    # the next release's change cannot be taken against an unread graph
    assert report.metric("per_release", "adrs_stories", "v0.2.0")["value"] == {
        "adrs": 1,
        "stories": 0,
    }
    assert report.metric("human", "lore", "v0.2.0")["value"] is None


def test_a_node_turning_canon(tool, tmp_path: Path) -> None:
    """A node that becomes CANON between two tags is the second release's one approval."""
    report = run_on(
        tool,
        tmp_path,
        {GRAPH: graph(node(fx.adr(1), "decision", "PROPOSED"))},
        {GRAPH: graph(node(fx.adr(1), "decision", "CANON"))},
    )
    check_cells(report, "v0.1.0", (0, 0), (0, 1))
    check_cells(report, "v0.2.0", (1, 0), (1, 0))
    total = report.metric("human", "lore", "Total")["value"]
    assert total == {"approved": 1, "proposed": 1}
