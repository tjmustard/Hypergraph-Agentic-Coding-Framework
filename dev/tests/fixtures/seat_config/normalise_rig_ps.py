#!/usr/bin/env python3
"""Normalise a `rig ps --nodes --rig <rig> --full --json` record into a committed fixture.

OPR.99.0.11.2 (R79). Reads the raw record (a JSON file, or stdin with `-`), keeps only the keys
the seat half reads, renames the rig to `demo-hacf` in `rigName` and in each session name, sorts
the nodes by `logicalId` and prints JSON. Nothing else of a node (ids, timestamps, hosts, paths,
transcripts, tokens) is carried over.

Usage:
  rig ps --nodes --rig <rig> --full --json | normalise_rig_ps.py - > rig-ps-full-0.7.21.json
  normalise_rig_ps.py --drop-profile <raw.json> > rig-ps-full-no-profile.json
"""

from __future__ import annotations

import json
import sys

STAND_IN_RIG = "demo-hacf"
KEEP = (
    "logicalId",
    "canonicalSessionName",
    "rigName",
    "profile",
    "role",
    "model",
    "effort",
    "effortSource",
)
# `modelObservation` is kept only as {model}; it is not in KEEP because it is rebuilt.
OUT_KEYS = (*KEEP, "modelObservation")


def normalise(nodes: list, drop_profile: bool = False) -> list[dict]:
    """Return the normalised nodes, sorted by logicalId.

    Raises:
        ValueError: when nodes is not a list of objects.
    """
    if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
        raise ValueError("expected a JSON list of node objects")
    out = []
    for node in nodes:
        real = node.get("rigName")
        row = {key: node.get(key) for key in KEEP}
        row["rigName"] = STAND_IN_RIG
        session = row["canonicalSessionName"]
        if isinstance(session, str) and isinstance(real, str) and session.endswith(f"@{real}"):
            row["canonicalSessionName"] = session[: -len(real)] + STAND_IN_RIG
        observed = node.get("modelObservation")
        row["modelObservation"] = (
            {"model": observed.get("model")} if isinstance(observed, dict) else {"model": None}
        )
        if drop_profile:
            del row["profile"]
        out.append(row)
    return sorted(out, key=lambda r: str(r["logicalId"]))


def main(argv: list[str]) -> int:
    """Entry point: print the normalised record; exit 2 on a usage or input error."""
    args = [a for a in argv if a != "--drop-profile"]
    if len(args) != 1:
        print("usage: normalise_rig_ps.py [--drop-profile] <raw.json | ->", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
        rows = normalise(json.loads(text), "--drop-profile" in argv)
    except (OSError, ValueError) as exc:
        print(f"normalise_rig_ps: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
