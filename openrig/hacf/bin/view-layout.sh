#!/usr/bin/env bash
# usage: view-layout.sh --rig <name> [--provider herdr|tmux] [--dry-run]
#
# Arranges a HACF OpenRig team's Herdr view into the recommended rows:
#
#   orch.lead                                  (full width)
#   build.execute  | build.audit   | desk.scribe
#   spec.architect | spec.red-team | spec.resolve
#
# OpenRig has no layout option for a view yet, so this runs after `rig terminal open <rig>
# --provider herdr`, and again after the view is re-opened. It only MOVES panes: the agents keep
# running and nothing is typed into them. A view already in the rows is left alone.
#
# The rig's seats come from `rig ps --nodes --rig <name> --json`. A rig without all seven standard
# HACF seats is not touched. A Herdr workspace is arranged only when one of its tabs holds the seven
# seat panes (labelled with the seats' logical ids).
#
# Herdr refuses a move within one tab, so each pane goes to a temporary tab and comes back with
# --split and --ratio. When a Herdr call fails, every pane still in a temporary tab is moved back
# beside orch.lead, so none is stranded.
#
# --provider tmux: OpenRig runs each seat in its own tmux session, so no tmux window holds the
# team's panes and `tmux select-layout` has nothing to arrange; the view is Herdr-only.
#
# Exit 0: arranged, already arranged, not a standard HACF team, no view found, or the provider is
# not installed (one line says which). Exit 1: `rig ps` or a Herdr call failed, or the result is
# not the layout. Exit 2: usage.
set -u
exec python3 -I - "$@" <<'PY'
import argparse
import json
import shutil
import subprocess
import sys

ROWS = [
    ["orch.lead"],
    ["build.execute", "build.audit", "desk.scribe"],
    ["spec.architect", "spec.red-team", "spec.resolve"],
]
SEATS = [s for row in ROWS for s in row]
LEAD = "orch.lead"
# (seat, target, split, ratio): rebuild around orch.lead, three equal rows, then equal thirds.
PLACES = [
    ("build.execute", "orch.lead", "down", "0.3333"),
    ("spec.architect", "build.execute", "down", "0.5"),
    ("build.audit", "build.execute", "right", "0.3333"),
    ("desk.scribe", "build.audit", "right", "0.5"),
    ("spec.red-team", "spec.architect", "right", "0.3333"),
    ("spec.resolve", "spec.red-team", "right", "0.5"),
]
TMUX_LINE = "tmux: not supported; the recommended view is Herdr-only (see docs/OpenRig-Team.md)"


class HerdrError(Exception):
    """A herdr call exited non-zero or printed no JSON `result`."""


def herdr(*args):
    """Run one herdr verb and return its JSON `result`."""
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HerdrError(f"herdr {' '.join(args)}: exit {r.returncode}")
        result = json.loads(r.stdout)["result"]
    except HerdrError:
        raise
    except Exception as exc:
        raise HerdrError(f"herdr {' '.join(args)}: {exc.__class__.__name__}") from None
    if result is None:
        raise HerdrError(f"herdr {' '.join(args)}: no result")
    return result


def rig_seats(rig):
    """The logical ids of the rig's nodes, or None when `rig ps` cannot be read."""
    try:
        r = subprocess.run(
            ["rig", "ps", "--nodes", "--rig", rig, "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return None
        nodes = json.loads(r.stdout)
        if not isinstance(nodes, list):
            return None
        return {n.get("logicalId") for n in nodes
                if isinstance(n, dict) and n.get("rigName", rig) == rig}
    except Exception:
        return None


def label(pane):
    return (pane.get("label") or pane.get("title") or "").strip()


def arrangement(lead_pane, names):
    """The tab's panes as rows of seat names, top to bottom and left to right."""
    rows = {}
    for p in herdr("pane", "layout", "--pane", lead_pane)["layout"]["panes"]:
        rows.setdefault(p["rect"]["y"], []).append((p["rect"]["x"], names.get(p["pane_id"], "?")))
    return [[n for _, n in sorted(r)] for _, r in sorted(rows.items())]


def arrange(wid, name, pid, tab, dry):
    """Arrange one workspace; return True when it ends in the layout."""
    names = {v: k for k, v in pid.items()}
    if arrangement(pid[LEAD], names) == ROWS:
        print(f"{name}: already in layout")
        return True
    if dry:
        print(f"{name}: would rearrange (dry run); plan:")
        for s in SEATS[1:]:
            print(f"  move {s} to a temporary tab")
        for seat, target, split, ratio in PLACES:
            print(f"  move {seat} back: --target-pane {target} --split {split} --ratio {ratio}")
        return True
    print(f"{name}: rearranging")
    away = []  # seats now in a temporary tab, in the order they left
    try:
        for s in SEATS[1:]:
            herdr("pane", "move", pid[s], "--new-tab", "--workspace", wid, "--no-focus")
            away.append(s)
        for seat, target, split, ratio in PLACES:
            herdr("pane", "move", pid[seat], "--tab", tab, "--target-pane", pid[target],
                  "--split", split, "--ratio", ratio, "--no-focus")
            away.remove(seat)
        ok = arrangement(pid[LEAD], names) == ROWS
    except HerdrError as exc:
        print(f"{name}: {exc}; moving {len(away)} pane(s) back beside {LEAD}", file=sys.stderr)
        for s in away:
            try:
                herdr("pane", "move", pid[s], "--tab", tab, "--target-pane", pid[LEAD],
                      "--split", "down", "--no-focus")
            except HerdrError as again:
                print(f"{name}: {again}; {s} is still in a temporary tab", file=sys.stderr)
        print(f"{name}: FAILED; not in the layout", file=sys.stderr)
        return False
    print(f"  -> {'OK' if ok else 'NOT in the expected layout'}")
    return ok


def main():
    ap = argparse.ArgumentParser(prog="view-layout.sh")
    ap.add_argument("--rig", required=True)
    ap.add_argument("--provider", choices=["herdr", "tmux"], default="herdr")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.rig.strip():
        ap.error("--rig must not be empty")
    if a.provider == "tmux":
        print(TMUX_LINE)
        return 0
    if shutil.which("herdr") is None:
        print("herdr: not installed; nothing to arrange")
        return 0
    seats = rig_seats(a.rig)
    if seats is None:
        print(f"rig ps --nodes --rig {a.rig} --json: unreadable; nothing moved", file=sys.stderr)
        return 1
    missing = [s for s in SEATS if s not in seats]
    if missing:
        print(f"{a.rig}: not a standard HACF team: missing {' '.join(missing)}")
        return 0
    try:
        workspaces = herdr("workspace", "list")["workspaces"]
    except HerdrError as exc:
        print(f"{exc}; nothing moved", file=sys.stderr)
        return 1
    found, failed = 0, []
    for w in workspaces:
        wid = w["workspace_id"]
        name = f"{w.get('label') or '?'} ({wid})"
        try:
            panes = herdr("pane", "list", "--workspace", wid)["panes"]
            by = {}
            for p in panes:
                by.setdefault(label(p), p)
            if not all(s in by for s in SEATS):
                continue  # not a view of a HACF team
            tab = by[LEAD]["tab_id"]
            if any(by[s]["tab_id"] != tab for s in SEATS):
                continue  # the seats are spread over tabs
            found += 1
            pid = {s: by[s]["pane_id"] for s in SEATS}
            if not arrange(wid, name, pid, tab, a.dry_run):
                failed.append(name)
        except HerdrError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failed.append(name)
    if not found and not failed:
        print(f"{a.rig}: no Herdr view of the team found; open one with "
              f"`rig terminal open {a.rig} --provider herdr`")
    return 1 if failed else 0


sys.exit(main())
PY
