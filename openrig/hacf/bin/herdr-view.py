#!/usr/bin/env python3
"""One command for a HACF team's Herdr view.

    herdr-view.py --rig <name> [--fix-size-only] [--dry-run]

Run it after `rig up`, after `rig up <name> --existing` and after a resume. In order, it

1. fixes each seat's tmux window size (`window-size latest`; a window left `manual` at 80x24 shows
   as dotted filler on the right of its pane);
2. finds the Herdr space labelled exactly <name>, and reopens it with `rig terminal open` when it is
   missing or its panes are detached;
3. lays the seats out from the rig's own spec: the lead full width on top, then rows of three (pods
   orch, build, desk, spec, then any other pod; members in spec order);
4. closes idle shells only, and puts the focus back on the space that had it.

An idle shell is a pane that is not a seat's, for which Herdr reports no agent, whose one foreground
process is its own shell (bash, zsh, sh, fish or dash), and whose shell has no child process.
Nothing else is ever closed, and no other space is written to.

--fix-size-only runs step 1 alone and calls no Herdr. --dry-run writes nothing and lists the plan.

Herdr 0.9.5 treats a move within one tab as a no-op, so each seat goes to a new tab and comes back
with --split and --ratio. When a call fails, every seat still away is moved back beside the lead.

Exit 0: arranged, already arranged, or nothing to do (one line says which). Exit 1: a rig, tmux or
herdr call failed, the final layout is not the planned one, or focus was not restored.
Exit 2: usage.

Standard library only. HACF_HERDR_VIEW_PROC (default /proc) is the proc root and
HACF_HERDR_VIEW_SETTLE (default 0.4) the seconds between re-checks; both are seams for tests.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SHELLS = {"bash", "zsh", "sh", "fish", "dash"}
LEAD = "orch.lead"
POD_ORDER = ["orch", "build", "desk", "spec"]
ROW = 3
DETACHED = "detached (from session"
NO_SESSION = ("no such window", "no such session", "can't find", "no server running")


class CallError(Exception):
    """A rig, tmux or herdr call failed, or printed something unreadable."""


def settle():
    """Sleep the settle time between re-checks."""
    try:
        seconds = float(os.environ.get("HACF_HERDR_VIEW_SETTLE", "0.4"))
    except ValueError:
        seconds = 0.4
    time.sleep(max(seconds, 0))


def call(argv, timeout=30):
    """Run one command; return its stdout, or raise CallError."""
    shown = " ".join(argv)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CallError(f"{shown}: {exc.__class__.__name__}") from None
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()
        raise CallError(f"{shown}: exit {r.returncode}" + (f": {detail[0]}" if detail else ""))
    return r.stdout


def call_json(argv, timeout=30):
    """Run one command and parse its stdout as JSON."""
    out = call(argv, timeout)
    try:
        return json.loads(out)
    except ValueError:
        raise CallError(f"{' '.join(argv)}: no JSON") from None


# --- the rig -------------------------------------------------------------------------------------


def rig_nodes(rig):
    """The rig's rows of `rig ps --nodes`."""
    argv = ["rig", "ps", "--nodes", "--rig", rig, "--json"]
    rows = call_json(argv, 60)
    if not isinstance(rows, list):
        raise CallError(f"{' '.join(argv)}: not a list")
    return [n for n in rows if isinstance(n, dict) and n.get("rigName") == rig]


def spec_seats(rig_id):
    """Every `<pod>.<member>` of the rig's spec, in spec order."""
    argv = ["rig", "spec", "show", str(rig_id), "--json"]
    spec = call_json(argv, 60)
    try:
        return [f"{pod['id']}.{m['id']}" for pod in spec["pods"] for m in pod["members"]]
    except (KeyError, TypeError):
        raise CallError(f"{' '.join(argv)}: no pods and members") from None


def plan_rows(seats):
    """The seats (spec order) as rows: the lead alone, then the others in rows of three."""
    if not seats:
        return []
    lead = LEAD if LEAD in seats else seats[0]

    def rank(seat):
        pod = seat.split(".", 1)[0]
        return POD_ORDER.index(pod) if pod in POD_ORDER else len(POD_ORDER)

    # sorted() is stable: other pods, and the members of every pod, keep spec order
    others = sorted((s for s in seats if s != lead), key=rank)
    return [[lead]] + [others[i : i + ROW] for i in range(0, len(others), ROW)]


# --- the window size -----------------------------------------------------------------------------


def fix_sizes(sessions, dry):
    """Set `window-size latest` on each seat's tmux window; return False when a call failed."""
    if shutil.which("tmux") is None:
        print("size: tmux not installed; skipped")
        return True
    ok = True
    for s in sessions:
        try:
            shown = call(["tmux", "show", "-w", "-t", f"{s}:", "window-size"])
        except CallError as exc:
            text = str(exc)
            gone = "error connecting" in text and "No such file" in text
            if gone or any(m in text for m in NO_SESSION):
                print(f"size: no tmux session {s}; skipped")
            else:
                print(f"size: {exc}", file=sys.stderr)
                ok = False
            continue
        if shown.strip() == "window-size latest":
            continue
        if dry:
            print(f"size: would set window-size latest on {s}")
            continue
        try:
            call(["tmux", "set", "-w", "-t", f"{s}:", "window-size", "latest"])
            call(["tmux", "resize-window", "-A", "-t", s])
            print(f"size: set window-size latest on {s}")
        except CallError as exc:
            print(f"size: {exc}", file=sys.stderr)
            ok = False
    return ok


# --- Herdr ---------------------------------------------------------------------------------------


def herdr(*args):
    """Run one herdr read and return its JSON `result`."""
    argv = ["herdr", *args]
    data = call_json(argv)
    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
        raise CallError(f"{' '.join(argv)}: no result")
    return data["result"]


def herdr_list(key, *args):
    """A herdr read whose result holds a list of objects under `key`."""
    items = herdr(*args).get(key)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise CallError(f"herdr {' '.join(args)}: no {key}")
    return items


def workspaces():
    """Every Herdr space."""
    return herdr_list("workspaces", "workspace", "list")


def space_panes(wid):
    """The panes of one space."""
    return [p for p in herdr_list("panes", "pane", "list") if p.get("workspace_id") == wid]


def label(item):
    """A pane's or space's label, stripped."""
    return (item.get("label") or "").strip()


def focused(spaces):
    """The focused space's id, or None."""
    return next((w["workspace_id"] for w in spaces if w.get("focused")), None)


class View:
    """One run against Herdr, for one rig."""

    def __init__(self, rig, seats, order, sessions, dry):
        """Hold the rig's seats and whether this is a dry run."""
        self.rig = rig
        self.sessions = sessions  # the seats' tmux sessions, sized again after a reopen
        self.seats = seats  # every seat id the rig knows: their panes are never closed
        self.order = order  # the live seats, in spec order
        self.dry = dry
        self.wrote = False  # a Herdr write or a reopen happened, so focus may have moved

    def write(self, *args):
        """Run one herdr write."""
        self.wrote = True
        call(["herdr", *args])

    def idle_shell(self, pane):
        """(shell name, pid) when the pane is an idle shell, else None."""
        if label(pane) in self.seats or pane.get("agent") is not None:
            return None
        pid = pane["pane_id"]
        info = herdr("pane", "process-info", "--pane", pid).get("process_info")
        try:
            shell = int(info["shell_pid"])
            procs = info["foreground_processes"]
            if len(procs) != 1 or int(procs[0]["pid"]) != shell:
                return None
            name = os.path.basename(procs[0]["name"])
        except (KeyError, TypeError, ValueError):
            return None
        if name not in SHELLS or has_child(shell):
            return None
        return name, shell

    def close(self, pane, idle):
        """Close an idle shell, or list the close in a dry run."""
        if self.dry:
            print(f"close {pane['pane_id']} (idle shell {idle[0]} pid {idle[1]})")
        else:
            self.write("pane", "close", pane["pane_id"])

    # --- a detached or missing space ---

    def detached(self, panes):
        """True when a seat pane's screen says its tmux client was detached."""
        for p in panes:
            if label(p) not in self.seats:
                continue
            # the visible screen only: old scrollback must not close a live space
            if DETACHED in call(["herdr", "pane", "read", p["pane_id"], "--source", "visible"]):
                return True
        return False

    def reopen(self, before, had_focus):
        """`rig terminal open` the rig; return the new space's id, labelled with the rig."""
        self.wrote = True
        argv = ["rig", "terminal", "open", self.rig, "--provider", "herdr", "--json"]
        reply = call_json(argv, 120)
        if (
            not isinstance(reply, dict)
            or reply.get("ok") is not True
            or reply.get("absent")
            or reply.get("degraded")
        ):
            raise CallError(f"{' '.join(argv)}: not a complete view")
        settle()
        spaces = workspaces()
        new = [w for w in spaces if w["workspace_id"] not in before]
        pick = [w for w in new if label(w) == self.rig] or new
        if len(pick) > 1:
            pick = [w for w in pick if w.get("focused")]
        if not new:
            # An open that added nothing focused a live view. Never rename a space that does not
            # show this team: the one the person was already on, or one without a seat pane.
            pick = [
                w
                for w in spaces
                if w.get("focused")
                and (
                    label(w) == self.rig
                    or (
                        w["workspace_id"] != had_focus
                        and any(label(p) in self.seats for p in space_panes(w["workspace_id"]))
                    )
                )
            ]
        if len(pick) != 1:
            raise CallError(f"{self.rig}: reopened space not found")
        wid = pick[0]["workspace_id"]
        if label(pick[0]) != self.rig:
            self.write("workspace", "rename", wid, self.rig)
        return wid

    # --- the layout ---

    def rows_of(self, lead_pane, names):
        """The lead tab's panes as rows of labels, top to bottom and left to right."""
        layout = herdr("pane", "layout", "--pane", lead_pane).get("layout")
        rows = {}
        try:
            for p in layout["panes"]:
                cell = (p["rect"]["x"], names.get(p["pane_id"]) or "?")
                rows.setdefault(p["rect"]["y"], []).append(cell)
        except (KeyError, TypeError):
            raise CallError(f"herdr pane layout --pane {lead_pane}: no rects") from None
        return [[n for _, n in sorted(r)] for _, r in sorted(rows.items())]

    def arrange(self, wid):
        """Lay the space out; return the exit code."""
        name = f"{self.rig} ({wid})"
        panes = space_panes(wid)
        seat_pane = {}
        for p in panes:
            seat_pane.setdefault(label(p), p)
        present = [s for s in self.order if s in seat_pane]
        for s in self.order:
            if s not in seat_pane:
                print(f"missing pane: {s}")
        if not present:
            print(f"{name}: no seat panes; nothing to arrange")
            return 0
        rows = plan_rows(present)
        lead = rows[0][0]
        pid = {s: seat_pane[s]["pane_id"] for s in present}
        names = {p["pane_id"]: label(p) for p in panes}
        tab = seat_pane[lead]["tab_id"]
        if self.rows_of(pid[lead], names) == rows:
            print(f"{name}: already in layout")
            return 0
        if self.dry:
            print(f"{name}: plan (dry run)")
        others = [p for p in panes if p["tab_id"] == tab and p["pane_id"] not in pid.values()]
        for p in others:
            idle = self.idle_shell(p)
            if idle:
                self.close(p, idle)
            elif self.dry:
                print(f"move {p['pane_id']} ({label(p) or 'unlabelled'}) to a new tab")
            else:
                self.write(
                    "pane", "move", p["pane_id"], "--new-tab", "--workspace", wid, "--no-focus"
                )
        self.move_seats(wid, tab, rows, pid)
        if self.dry:
            for p in panes:
                if p["tab_id"] != tab and p["pane_id"] not in pid.values():
                    idle = self.idle_shell(p)
                    if idle:
                        self.close(p, idle)
            return 0
        self.close_idle(wid)
        if self.rows_of(pid[lead], names) != rows:
            print(f"{name}: NOT in the planned layout", file=sys.stderr)
            return 1
        print(f"{name}: arranged")
        return 0

    def move_seats(self, wid, tab, rows, pid):
        """Send every non-lead seat out to its own tab, then bring them back into the rows."""
        lead = rows[0][0]
        order = [s for row in rows[1:] for s in row]
        # (seat, target, split, ratio): first the row heads, top to bottom, then each row
        places = [
            (rows[k][0], rows[k - 1][0], "down", ratio(len(rows) - k + 1))
            for k in range(1, len(rows))
        ]
        places += [
            (row[j], row[j - 1], "right", ratio(len(row) - j + 1))
            for row in rows
            for j in range(1, len(row))
        ]
        if self.dry:
            for s in order:
                print(f"move {s} to a new tab")
            for s, target, split, r in places:
                print(f"move {s} back: --target-pane {target} --split {split} --ratio {r}")
            return
        away = []  # seats now in a temporary tab, in the order they left
        try:
            for s in order:
                self.write("pane", "move", pid[s], "--new-tab", "--workspace", wid, "--no-focus")
                away.append(s)
            for s, target, split, r in places:
                self.write(
                    "pane", "move", pid[s], "--tab", tab, "--target-pane", pid[target],
                    "--split", split, "--ratio", r, "--no-focus",
                )  # fmt: skip
                away.remove(s)
        except CallError:
            print(f"moving {len(away)} pane(s) back beside {lead}", file=sys.stderr)
            for s in away:
                try:
                    self.write(
                        "pane", "move", pid[s], "--tab", tab, "--target-pane", pid[lead],
                        "--split", "down", "--no-focus",
                    )  # fmt: skip
                except CallError as again:
                    print(f"{again}; {s} is still in a temporary tab", file=sys.stderr)
            raise

    def close_idle(self, wid):
        """Close the space's idle shells, in up to four rounds."""
        for _ in range(4):
            found = False
            for p in space_panes(wid):
                idle = self.idle_shell(p)
                if idle:
                    found = True
                    self.close(p, idle)
            if not found:
                return
            settle()

    # --- the run ---

    def run(self):
        """Everything after the size fix; return the exit code."""
        spaces = workspaces()
        mine = [w for w in spaces if label(w) == self.rig]
        if len(mine) > 1:
            print(
                f"{self.rig}: {len(mine)} Herdr spaces are labelled {self.rig}; "
                "close the extra ones; nothing changed",
                file=sys.stderr,
            )
            return 1
        had_focus = focused(spaces)
        try:
            code = self.open_and_arrange(spaces, mine, had_focus)
        except (CallError, KeyError, TypeError) as exc:  # the last two: an unexpected shape
            print(f"{self.rig}: {exc}", file=sys.stderr)
            code = 1
        if self.wrote and not self.dry and not restore_focus(had_focus):
            code = 1
        return code

    def open_and_arrange(self, spaces, mine, had_focus):
        """Reopen a missing or detached space, then lay it out; return the exit code."""
        wid = mine[0]["workspace_id"] if mine else None
        if wid is not None:
            panes = space_panes(wid)
            if not self.detached(panes):
                return self.arrange(wid)
            # all() stops at the first pane that may not be closed
            if not all(label(p) in self.seats or self.idle_shell(p) for p in panes):
                print(
                    f"{self.rig} ({wid}): detached, but holds other panes; not closed",
                    file=sys.stderr,
                )
                return 1
        if self.dry:
            print(f"{self.rig} ({wid or 'no space'}): plan (dry run)")
            if wid is not None:
                print(f"close space {wid} (detached)")
            print(f"reopen: rig terminal open {self.rig} --provider herdr")
            return 0
        if wid is not None:
            self.write("workspace", "close", wid)
        new = self.reopen({w["workspace_id"] for w in spaces}, had_focus)
        sized = fix_sizes(self.sessions, False)
        return max(self.arrange(new), 0 if sized else 1)


def ratio(parts):
    """1/parts as Herdr's --ratio: four places, trailing zeros stripped."""
    return format(1 / parts, ".4f").rstrip("0").rstrip(".")


def has_child(pid):
    """True unless every thread of the process certainly has no child."""
    root = Path(os.environ.get("HACF_HERDR_VIEW_PROC") or "/proc") / str(pid)
    try:
        files = list(root.glob("task/*/children"))
        return not files or any(f.read_text().strip() for f in files)
    except OSError:
        return True


def restore_focus(wid):
    """Focus the space that had the focus, when it still exists; False when that did not hold."""
    if wid is None:
        return True
    try:
        spaces = workspaces()
        if focused(spaces) == wid or all(w["workspace_id"] != wid for w in spaces):
            return True
        call(["herdr", "workspace", "focus", wid])
        for _ in range(8):
            settle()
            if focused(workspaces()) == wid:
                return True
        print(f"focus not restored: {wid}", file=sys.stderr)
    except CallError as exc:
        print(f"focus not restored: {exc}", file=sys.stderr)
    return False


def main(argv=None):
    """Parse the arguments, fix the window sizes, then arrange the view."""
    ap = argparse.ArgumentParser(
        prog="herdr-view.py",
        description="Fix, reopen and arrange one team's Herdr view.",
        allow_abbrev=False,
    )
    ap.add_argument("--rig", required=True, help="the rig; also the Herdr space's label")
    ap.add_argument("--fix-size-only", action="store_true", help="only fix the tmux window sizes")
    ap.add_argument("--dry-run", action="store_true", help="write nothing; list the plan")
    a = ap.parse_args(argv)
    rig = a.rig.strip()
    if not rig:
        ap.error("--rig must not be empty")
    try:
        nodes = rig_nodes(rig)
    except CallError as exc:
        print(f"rig ps --nodes --rig {rig} --json: unreadable; nothing changed ({exc})",
              file=sys.stderr)  # fmt: skip
        return 1
    if not nodes:
        print(f"{rig}: no seats; nothing to do")
        return 0
    sessions = [n["canonicalSessionName"] for n in nodes if n.get("canonicalSessionName")]
    if a.fix_size_only:  # this step needs no spec and no herdr
        if not fix_sizes(sessions, a.dry_run):
            return 1
        print(f"{rig}: window sizes checked")
        return 0
    try:
        in_spec = spec_seats(nodes[0].get("rigId"))
    except CallError as exc:
        print(f"rig spec show: unreadable; nothing changed ({exc})", file=sys.stderr)
        return 1
    live = {n.get("logicalId") for n in nodes}
    order = [s for s in in_spec if s in live]
    if not order:
        print(f"{rig}: no seat of the rig spec is in rig ps; nothing changed", file=sys.stderr)
        return 1
    sized = fix_sizes(sessions, a.dry_run)
    code = 0
    if shutil.which("herdr") is None:
        print("herdr: not installed; nothing to arrange")
    else:
        view = View(rig, live | set(in_spec), order, sessions, a.dry_run)
        try:
            code = view.run()
        except (CallError, KeyError, TypeError) as exc:
            print(f"{rig}: {exc}", file=sys.stderr)
            code = 1
    return code if sized else 1


if __name__ == "__main__":
    sys.exit(main())
