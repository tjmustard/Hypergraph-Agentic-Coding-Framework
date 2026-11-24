#!/usr/bin/env python3
"""Propose per-role context caps from hacf.telemetry/v1 scorecards, for TJ's OK. Read-only.

Dev tooling only (v0.6.4, OPR.99.0.8.3); never installed into projects. Stdlib + PyYAML,
Python 3.10+.

Usage:
  recompute_caps.py --scorecard <file> [--scorecard <file> ...] --caps <caps.yml>
                    --rig-spec <rig.yaml> [--json]

Each scorecard's `context` object lists, per seat, its occupants (session ids) with their peak
resident context. A seat `<pod>-<member>@<rig>` maps to that member's `profile` in --rig-spec; a
seat with no such member is listed under `unmapped`. Per role it prints n (occupants with turns),
low / median / mean / p90 (nearest rank) / high of their peaks, the current cap_tokens from
--caps and a proposal: max(100000, p90 rounded up to 10000) when n >= 5, else none. An occupant
seen in several scorecards counts once, with its highest peak.

It never writes: --caps is opened for reading only, and nothing else is written. A change to
caps.yml is made by hand after TJ approves it. Exit 0, or 2 on a usage error or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import yaml

SCHEMA = "hacf.telemetry/v1"
MIN_N = 5
FLOOR = 100000
STEP = 10000
NOTICE = "Proposal only: caps.yml is unchanged. TJ approves each change (ADR-0044)."
INSUFFICIENT = "insufficient data (n<5)"


class InputError(Exception):
    """A usage error or an unreadable input: exit 2 with one line."""


class Parser(argparse.ArgumentParser):
    """argparse that raises InputError instead of exiting."""

    def error(self, message: str):
        """Raise InputError with argparse's message."""
        raise InputError(message)


def read_yaml(path: str, what: str) -> dict:
    """Load a YAML mapping read-only.

    Raises:
        InputError: when the file cannot be read or is not a mapping.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise InputError(f"cannot read {what} {path}: {exc.strerror or exc}") from None
    except yaml.YAMLError as exc:
        raise InputError(f"{what} {path} is not valid YAML") from exc
    if not isinstance(data, dict):
        raise InputError(f"{what} {path} is not a mapping")
    return data


def read_caps(path: str) -> dict[str, int]:
    """Return {role: cap_tokens} from caps.yml.

    Raises:
        InputError: on an unreadable or malformed file.
    """
    data = read_yaml(path, "--caps")
    seats = data.get("seats")
    if data.get("schema") != "hacf.caps/v1" or not isinstance(seats, dict) or not seats:
        raise InputError(f"--caps {path} is not a hacf.caps/v1 file")
    caps = {}
    for role, entry in seats.items():
        cap = entry.get("cap_tokens") if isinstance(entry, dict) else None
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise InputError(f"--caps {path}: seats.{role}.cap_tokens is not a positive int")
        caps[str(role)] = cap
    return caps


def read_rig_spec(path: str) -> dict[str, str]:
    """Return {"<pod>-<member>": profile} from a rig.yaml.

    Raises:
        InputError: on an unreadable file or one without pods.
    """
    data = read_yaml(path, "--rig-spec")
    pods = data.get("pods")
    if not isinstance(pods, list):
        raise InputError(f"--rig-spec {path} has no pods list")
    members = {}
    for pod in pods:
        if not isinstance(pod, dict):
            continue
        for member in pod.get("members") or []:
            if isinstance(member, dict) and member.get("id") and member.get("profile"):
                members[f"{pod.get('id')}-{member['id']}"] = str(member["profile"])
    return members


def read_scorecard(path: str) -> dict:
    """Return a scorecard's `context` object.

    Raises:
        InputError: when the file is unreadable, not a scorecard, or has no context.
    """
    try:
        card = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read --scorecard {path}: {exc.strerror or exc}") from None
    except ValueError:
        raise InputError(f"--scorecard {path} is not JSON") from None
    if not isinstance(card, dict) or card.get("schema") != SCHEMA:
        raise InputError(f"--scorecard {path} is not a {SCHEMA} scorecard")
    context = card.get("context")
    if not isinstance(context, dict):
        raise InputError(f"--scorecard {path} has no context (made before v0.6.4?)")
    return context


def number(value: float) -> int | float:
    """Round to one decimal place and drop a zero fraction."""
    value = round(value, 1)
    return int(value) if value == int(value) else value


def role_stats(peaks: list[int], cap: int | None) -> dict:
    """Return n, the five statistics, the current cap and the proposal for one role."""
    n = len(peaks)
    out: dict = {"n": n, "current": cap}
    if n:
        ordered = sorted(peaks)
        p90 = ordered[math.ceil(0.9 * n) - 1]
        out.update(
            low=ordered[0],
            median=number(statistics.median(ordered)),
            mean=number(statistics.mean(ordered)),
            p90=p90,
            high=ordered[-1],
        )
    else:
        out.update(low=None, median=None, mean=None, p90=None, high=None)
    if n >= MIN_N:
        out["proposed"] = max(FLOOR, math.ceil(out["p90"] / STEP) * STEP)
    else:
        out["proposed"] = None
        out["note"] = INSUFFICIENT
    return out


def recompute(contexts: list[dict], caps: dict[str, int], members: dict[str, str]) -> dict:
    """Return the per-role proposal and the unmapped seats."""
    peaks: dict[str, dict[tuple[str, str], int]] = {}
    unmapped: set[str] = set()
    for context in contexts:
        for seat, entry in context.items():
            local = seat.split("@", 1)[0]
            role = members.get(local)
            if role is None:
                unmapped.add(seat)
                continue
            occupants = entry.get("occupants") if isinstance(entry, dict) else None
            for occ in occupants or []:
                if not isinstance(occ, dict):
                    continue
                turns, peak = occ.get("turns"), occ.get("peak")
                if not isinstance(turns, int) or turns <= 0 or not isinstance(peak, int):
                    continue
                key = (seat, str(occ.get("session_id")))
                slot = peaks.setdefault(role, {})
                slot[key] = max(slot.get(key, 0), peak)
    roles = {}
    for role in [*caps, *sorted(set(peaks) - set(caps))]:
        roles[role] = role_stats(list(peaks.get(role, {}).values()), caps.get(role))
    return {"roles": roles, "unmapped": sorted(unmapped), "notice": NOTICE}


def table(result: dict) -> str:
    """Render the result as a plain-text table ending with the notice line."""
    cols = ("role", "n", "low", "median", "mean", "p90", "high", "current", "proposed")
    rows = [cols]
    for role, s in result["roles"].items():
        cells = [role] + ["-" if s[c] is None else str(s[c]) for c in cols[1:]]
        if s.get("note"):
            cells[-1] += f"  ({s['note']})"
        rows.append(tuple(cells))
    widths = [max(len(r[i]) for r in rows) for i in range(len(cols) - 1)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r[:-1], widths)) + "  " + r[-1] for r in rows]
    if result["unmapped"]:
        lines.append("unmapped: " + ", ".join(result["unmapped"]))
    lines.append(NOTICE)
    return "\n".join(line.rstrip() for line in lines)


def run(argv: list[str]) -> int:
    """Run the CLI; return the exit code.

    Raises:
        InputError: on a usage error or unreadable input.
    """
    parser = Parser(prog="recompute_caps.py", description="Propose caps; never writes caps.yml.")
    parser.add_argument("--scorecard", action="append", required=True)
    parser.add_argument("--caps", required=True)
    parser.add_argument("--rig-spec", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    caps = read_caps(args.caps)
    members = read_rig_spec(args.rig_spec)
    contexts = [read_scorecard(p) for p in args.scorecard]
    result = recompute(contexts, caps, members)
    print(json.dumps(result, indent=2) if args.json else table(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: exit 2 with one line on a usage error or unreadable input."""
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except InputError as exc:
        print(f"recompute_caps: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
