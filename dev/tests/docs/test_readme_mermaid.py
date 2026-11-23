"""The README's mermaid diagram of HACF's OpenRig team stays equal to openrig/hacf/rig.yaml.

The diagram must be exactly one fenced ```mermaid block in README.md, written in a small,
GitHub-safe flowchart subset: a `flowchart` header, `subgraph <id>["label"]` ... `end`, one node
declaration per line (`<id>["label"]`, `<id>(["label"])` or `<id>[("label")]`), one arrow per line
between two declared ids (`-->`, `-.->` or `==>`, with an optional `|"label"|`), `%%` comments and
at most one `classDef`. Anything else in the block is a failure, so a renderer-only feature cannot
slip in. Seat nodes are `<pod>_<member>` (hyphens become underscores), declared inside their
pod's subgraph; every spec edge is an arrow labelled with its kind; every other node sits in the
`OpenRig (generic)` subgraph or is the `human` node.

Stdlib and PyYAML only; the checks read files and write nothing in the repository. Each
`assert_*` function takes the rig spec and README paths, so the mutation tests below run the
same checks on temp copies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT

RIG_YAML = REPO_ROOT / "openrig" / "hacf" / "rig.yaml"
README = REPO_ROOT / "README.md"

GENERIC_LABEL = "OpenRig (generic)"
HUMAN_NODE = "human"
ID = r"[A-Za-z][A-Za-z0-9_]*"
FLOWCHART = re.compile(r"^flowchart\s+(TB|TD|BT|LR|RL)$")
SUBGRAPH = re.compile(rf'^subgraph\s+({ID})(?:\["([^"]*)"\])?$')
NODE = re.compile(rf'^({ID})(\["|\(\["|\[\(")([^"]*)("\]|"\]\)|"\)\])$')
NODE_CLOSERS = {'["': '"]', '(["': '"])', '[("': '")]'}
ARROW = re.compile(rf'^({ID})\s+(-->|-\.->|==>)(?:\|"([^"|]*)"\|)?\s+({ID})$')
FORBIDDEN = (
    (re.compile(r"<\s*/?\s*[A-Za-z][^>]*>"), "an HTML tag"),
    (re.compile(r"^\s*click\b", re.MULTILINE), "a click line"),
    (re.compile(r"%%\{"), "an %%{init} directive"),
    (re.compile(r"^\s*style\b", re.MULTILINE), "a style line"),
    (re.compile(r"^\s*linkStyle\b", re.MULTILINE), "a linkStyle line"),
)
# Seats of the private OpenRig kernel, never part of HACF's team diagram.
KERNEL_WORDS = re.compile(r"\b(advisor|operator|status-\w+|@kernel)\b", re.IGNORECASE)


def _node_id(pod: str, member: str) -> str:
    """Return the diagram id of a seat: `<pod>_<member>` with hyphens as underscores."""
    return f"{pod}_{member}".replace("-", "_")


def load_spec(rig_path: Path) -> tuple[dict[str, dict], set[tuple[str, str, str]]]:
    """Return the spec's pods and its edges as (kind, from node id, to node id).

    Args:
        rig_path: the rig.yaml to read.

    Returns:
        ({pod id: {"label": str, "seats": {node id: "<pod>-<member>"}}}, edges).
    """
    data = yaml.safe_load(rig_path.read_text(encoding="utf-8"))
    pods: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()
    for pod in data["pods"]:
        seats = {_node_id(pod["id"], m["id"]): f"{pod['id']}-{m['id']}" for m in pod["members"]}
        pods[pod["id"]] = {"label": pod.get("label", ""), "seats": seats}
        for edge in pod.get("edges") or []:
            edges.add(
                (edge["kind"], _node_id(pod["id"], edge["from"]), _node_id(pod["id"], edge["to"]))
            )
    for edge in data.get("edges") or []:
        ends = [_node_id(*str(edge[key]).split(".", 1)) for key in ("from", "to")]
        edges.add((edge["kind"], ends[0], ends[1]))
    return pods, edges


def mermaid_blocks(readme_path: Path) -> list[list[str]]:
    """Return the lines of every fenced ```mermaid block in the README."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in readme_path.read_text(encoding="utf-8").splitlines():
        if current is None:
            if re.match(r"^\s*```\s*mermaid\s*$", line):
                current = []
        elif re.match(r"^\s*```\s*$", line):
            blocks.append(current)
            current = None
        else:
            current.append(line)
    assert current is None, "README.md has an unclosed ```mermaid block"
    return blocks


def parse_diagram(lines: list[str]) -> dict:
    """Parse one block in the GitHub-safe subset; any other line is an AssertionError.

    Returns:
        {"subgraphs": {id: label}, "nodes": {id: (label, enclosing subgraph ids)},
        "arrows": [(from, to, label)], "classdefs": int}
    """
    subgraphs: dict[str, str] = {}
    nodes: dict[str, tuple[str, tuple[str, ...]]] = {}
    arrows: list[tuple[str, str, str]] = []
    stack: list[str] = []
    classdefs = 0
    body = [line.strip() for line in lines if line.strip() and not line.strip().startswith("%%")]
    assert body and FLOWCHART.match(body[0]), f"the diagram must open with flowchart: {body[:1]}"
    for line in body[1:]:
        if match := SUBGRAPH.match(line):
            subgraph_id, label = match.group(1), match.group(2) or match.group(1)
            assert subgraph_id not in subgraphs, f"subgraph {subgraph_id!r} declared twice"
            subgraphs[subgraph_id] = label
            stack.append(subgraph_id)
        elif line == "end":
            assert stack, "an `end` without a subgraph"
            stack.pop()
        elif match := NODE.match(line):
            node_id, opener, label, closer = match.groups()
            assert NODE_CLOSERS[opener] == closer, f"mismatched node shape: {line!r}"
            assert node_id not in nodes, f"node {node_id!r} declared twice"
            nodes[node_id] = (label, tuple(stack))
        elif match := ARROW.match(line):
            arrows.append((match.group(1), match.group(4), match.group(3) or ""))
        elif line.startswith("classDef "):
            classdefs += 1
        elif line.startswith("class "):
            continue
        else:
            raise AssertionError(f"line outside the GitHub-safe subset: {line!r}")
    assert not stack, f"subgraphs left open: {stack}"
    return {"subgraphs": subgraphs, "nodes": nodes, "arrows": arrows, "classdefs": classdefs}


def the_diagram(readme_path: Path) -> tuple[list[str], dict]:
    """Return the one mermaid block of the README and its parse."""
    blocks = mermaid_blocks(readme_path)
    assert len(blocks) == 1, f"README.md has {len(blocks)} mermaid blocks; exactly one expected"
    return blocks[0], parse_diagram(blocks[0])


def assert_safe_syntax(rig_path: Path, readme_path: Path) -> None:
    """The block holds none of the forbidden syntax and parses in the subset."""
    blocks = mermaid_blocks(readme_path)
    assert len(blocks) == 1, f"README.md has {len(blocks)} mermaid blocks; exactly one expected"
    text = "\n".join(blocks[0])
    found = [what for pattern, what in FORBIDDEN if pattern.search(text)]
    assert not found, f"forbidden mermaid syntax: {found}"
    diagram = parse_diagram(blocks[0])
    assert diagram["classdefs"] <= 1, f"{diagram['classdefs']} classDef lines; at most one"
    kernel = KERNEL_WORDS.findall(text)
    assert not kernel, f"private kernel seat names in the diagram: {kernel}"


def assert_seats_and_pods(rig_path: Path, readme_path: Path) -> None:
    """Seat nodes equal the spec's seats, each named and declared inside its pod's subgraph."""
    pods, _ = load_spec(rig_path)
    _, diagram = the_diagram(readme_path)
    nodes, subgraphs = diagram["nodes"], diagram["subgraphs"]
    expected = {node for pod in pods.values() for node in pod["seats"]}
    seat_shaped = {node for node in nodes if node.split("_", 1)[0] in pods and "_" in node}
    assert seat_shaped == expected, (
        f"seat nodes differ from rig.yaml: missing {sorted(expected - seat_shaped)}, "
        f"extra {sorted(seat_shaped - expected)}"
    )
    for pod_id, pod in pods.items():
        assert pod_id in subgraphs, f"no subgraph for pod {pod_id!r}"
        label = subgraphs[pod_id]
        assert pod_id in label and pod["label"] in label, (
            f"subgraph {pod_id!r} label {label!r} does not name the pod ({pod_id}, {pod['label']})"
        )
        for node, seat in pod["seats"].items():
            node_label, enclosing = nodes[node]
            assert enclosing and enclosing[-1] == pod_id, (
                f"seat {node!r} is declared in {enclosing}, not in subgraph {pod_id!r}"
            )
            assert seat in node_label, f"seat {node!r} label {node_label!r} does not name {seat!r}"


def assert_spec_edges(rig_path: Path, readme_path: Path) -> None:
    """Every spec edge is an arrow between its two seat nodes, labelled with its kind."""
    _, edges = load_spec(rig_path)
    _, diagram = the_diagram(readme_path)
    missing = [
        edge
        for edge in sorted(edges)
        if not any(
            src == edge[1] and dst == edge[2] and edge[0] in label
            for src, dst, label in diagram["arrows"]
        )
    ]
    assert not missing, f"spec edges without a labelled arrow (kind, from, to): {missing}"


def assert_other_nodes(rig_path: Path, readme_path: Path) -> None:
    """Non-seat nodes are generic OpenRig parts or the human; arrows join declared nodes."""
    pods, _ = load_spec(rig_path)
    _, diagram = the_diagram(readme_path)
    nodes, subgraphs = diagram["nodes"], diagram["subgraphs"]
    generic = [sid for sid, label in subgraphs.items() if GENERIC_LABEL in label]
    assert len(generic) == 1, f"{len(generic)} subgraphs labelled {GENERIC_LABEL!r}; one expected"
    assert HUMAN_NODE in nodes, f"no {HUMAN_NODE!r} node for the person"
    assert set(subgraphs) == set(pods) | set(generic), (
        f"subgraphs other than the pods and the generic one: {sorted(set(subgraphs) - set(pods))}"
    )
    seats = {node for pod in pods.values() for node in pod["seats"]}
    for node, (_, enclosing) in nodes.items():
        if node in seats or node == HUMAN_NODE:
            continue
        assert generic[0] in enclosing, f"node {node!r} is neither a seat, generic nor the human"
    loose = sorted({end for src, dst, _ in diagram["arrows"] for end in (src, dst)} - set(nodes))
    assert not loose, f"arrows to undeclared nodes: {loose}"


def assert_readme_matches_rig(rig_path: Path, readme_path: Path) -> None:
    """Run every check on one rig spec and README pair."""
    assert_safe_syntax(rig_path, readme_path)
    assert_seats_and_pods(rig_path, readme_path)
    assert_spec_edges(rig_path, readme_path)
    assert_other_nodes(rig_path, readme_path)


def test_readme_mermaid_safe_syntax():
    """Exactly one block, in the GitHub-safe subset, with no private kernel seat."""
    assert_safe_syntax(RIG_YAML, README)


def test_readme_mermaid_seats_and_pods():
    """The seat nodes are rig.yaml's seats, each inside its pod's subgraph."""
    assert_seats_and_pods(RIG_YAML, README)


def test_readme_mermaid_spec_edges():
    """Every rig.yaml edge is drawn and labelled with its kind."""
    assert_spec_edges(RIG_YAML, README)


def test_readme_mermaid_other_nodes():
    """The rest of the diagram is the generic OpenRig subgraph and the human node."""
    assert_other_nodes(RIG_YAML, README)


def _copies(tmp_path: Path) -> tuple[Path, Path]:
    """Copy rig.yaml and README.md into tmp_path and return the copies."""
    rig, readme = tmp_path / "rig.yaml", tmp_path / "README.md"
    rig.write_text(RIG_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    readme.write_text(README.read_text(encoding="utf-8"), encoding="utf-8")
    return rig, readme


def test_mutation_seat_renamed_in_rig(tmp_path):
    """A seat renamed in a temp rig.yaml fails the checks."""
    rig, readme = _copies(tmp_path)
    data = yaml.safe_load(rig.read_text(encoding="utf-8"))
    desk = next(pod for pod in data["pods"] if pod["id"] == "desk")
    desk["members"][0]["id"] = "writer"
    data["edges"] = [
        {**edge, "to": "desk.writer"} if edge["to"] == "desk.scribe" else edge
        for edge in data["edges"]
    ]
    rig.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert_readme_matches_rig(RIG_YAML, readme)  # the untouched spec still passes
    with pytest.raises(AssertionError, match="desk_writer"):
        assert_readme_matches_rig(rig, readme)


def test_mutation_seat_removed_from_readme(tmp_path):
    """A seat removed from a temp README fails the checks."""
    rig, readme = _copies(tmp_path)
    lines = readme.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if "desk_scribe" not in line]
    assert len(kept) < len(lines)
    readme.write_text("".join(kept), encoding="utf-8")
    with pytest.raises(AssertionError, match="desk_scribe"):
        assert_readme_matches_rig(rig, readme)


def test_mutation_click_line_added(tmp_path):
    """An added click line fails the checks."""
    rig, readme = _copies(tmp_path)
    text = readme.read_text(encoding="utf-8")
    marker = "```mermaid\nflowchart"
    assert text.count(marker) == 1
    head, tail = text.split(marker)
    first, rest = tail.split("\n", 1)
    click = '    click orch_lead "https://example.com"\n'
    readme.write_text(head + marker + first + "\n" + click + rest, encoding="utf-8")
    with pytest.raises(AssertionError, match="a click line"):
        assert_readme_matches_rig(rig, readme)
