#!/usr/bin/env python3
"""Gate helpers shared by /hyper-execute, /hyper-audit and /hyper-lore-approve.

Usage:
    hyper_gates.py mode
    hyper_gates.py record --gate <name> --slice <id> --reason <text>
    hyper_gates.py lore-check --slice <id> [--source <legacy path>] [--nodes a,b] [--json]

- mode prints `autonomous` only when HACF_AUTONOMOUS=1 (the engine sets it), otherwise
  `interactive`. It reads no other variable and never tries to learn Claude Code's permission
  mode; the lore gate's hook ticket does the enforcing (R7).
- record builds {gate, slice, reason, at} and appends it as one JSON line to
  $HACF_OUTBOX/gates.jsonl; without HACF_OUTBOX (an interactive session) it prints the record and
  writes nothing.
- lore-check reads the lore graph and selects the live nodes that need a gate decision: PROPOSED
  or stale nodes whose provenance.source is the slice id or the legacy path, plus stale nodes
  anchored to a --nodes component. It writes nothing.

Exit codes: 0 ok, 2 usage; lore-check passes on hyper_lore's code when the graph cannot be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import _runtime
import hyper_lore

AUTONOMOUS_ENV = "HACF_AUTONOMOUS"
OUTBOX_ENV = "HACF_OUTBOX"
GATES_FILE = "gates.jsonl"


def cmd_mode(_ns: argparse.Namespace) -> int:
    """Print autonomous only under HACF_AUTONOMOUS=1."""
    print("autonomous" if os.environ.get(AUTONOMOUS_ENV) == "1" else "interactive")
    return 0


def cmd_record(ns: argparse.Namespace) -> int:
    """Append one gate record to the outbox, or print it when there is no outbox."""
    record = {
        "gate": ns.gate,
        "slice": ns.slice,
        "reason": ns.reason,
        "at": _runtime.utc_now_iso(),
    }
    line = json.dumps(record)
    outbox = os.environ.get(OUTBOX_ENV)
    if not outbox:
        print(line)
        return 0
    Path(outbox).mkdir(parents=True, exist_ok=True)
    with open(Path(outbox) / GATES_FILE, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return 0


def select_nodes(nodes: list[dict], sources: set[str], radius: set[str]) -> list[dict]:
    """Return one row per live node that needs a gate decision, in graph order."""
    rows = []
    for node in nodes:
        status = node.get("status")
        if status not in hyper_lore.LIVE:
            continue
        stale = node.get("stale") is True
        provenance = node.get("provenance")
        source = provenance.get("source") if isinstance(provenance, dict) else None
        anchors = node.get("anchors")
        components = anchors.get("components") if isinstance(anchors, dict) else None
        selected_by = []
        if source in sources and (stale or status == "PROPOSED"):
            selected_by.append("source")
        if stale and radius.intersection(components or []):
            selected_by.append("blast_radius")
        if selected_by:
            rows.append(
                {
                    "id": node.get("id"),
                    "type": node.get("type"),
                    "status": status,
                    "title": node.get("title"),
                    "stale": stale,
                    "stale_reason": node.get("stale_reason"),
                    "selected_by": selected_by,
                }
            )
    return rows


def cmd_lore_check(ns: argparse.Namespace) -> int:
    """Print the lore nodes this slice's audit must put through the gate."""
    try:
        _, data = hyper_lore.read_graph(hyper_lore.repo_top())
        nodes = hyper_lore.nodes_of(data)
    except hyper_lore.LoreError as exc:
        print(f"lore-check: {exc}", file=sys.stderr)
        return exc.code
    sources = {ns.slice} | ({ns.source} if ns.source else set())
    radius = {item.strip() for item in (ns.nodes or "").split(",") if item.strip()}
    rows = select_nodes(nodes, sources, radius)
    if ns.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        print(f"{row['id']} {row['status']} ({', '.join(row['selected_by'])}): {row['title']}")
    print(f"{len(rows)} node(s) selected")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI."""
    parser = argparse.ArgumentParser(description="Gate helpers for the HACF pipeline skills.")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p = commands.add_parser("mode", help="print autonomous or interactive")
    p.set_defaults(handler=cmd_mode)

    p = commands.add_parser("record", help="record a gate in the outbox, or print it")
    p.add_argument("--gate", required=True, help="gate name, e.g. low_confidence")
    p.add_argument("--slice", required=True, metavar="<id>", help="slice id")
    p.add_argument("--reason", required=True, metavar="<text>", help="why the gate stopped")
    p.set_defaults(handler=cmd_record)

    p = commands.add_parser("lore-check", help="select the lore nodes the audit gate must ask")
    p.add_argument("--slice", required=True, metavar="<id>", help="slice id")
    p.add_argument("--source", metavar="<path>", help="the legacy MiniPRD path")
    p.add_argument("--nodes", metavar="a,b", help="the blast-radius component ids")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(handler=cmd_lore_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _runtime.require_runtime()
    ns = build_parser().parse_args(argv)
    return ns.handler(ns)


if __name__ == "__main__":
    sys.exit(main())
