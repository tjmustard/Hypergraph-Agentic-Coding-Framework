"""openrig/hacf/bin/herdr-view.py: one command for a team's Herdr view (v067 OPR.99.0.14.7).

No real `rig`, `tmux` or `herdr`: the script's PATH holds only a fake of each, plus a `python3`
link. The fakes serve the recorded, scrubbed fixtures, log every argv and apply each write to a
state file. The fake `herdr` keeps every tab as a split tree, so `pane layout` shows what the
moves and ratios really build. A fake proc tree backs HACF_HERDR_VIEW_PROC.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BIN = REPO_ROOT / "openrig" / "hacf" / "bin"
SCRIPT = BIN / "herdr-view.py"
OLD_SCRIPT = BIN / "view-layout.sh"
FIXTURES = REPO_ROOT / "dev" / "tests" / "fixtures" / "herdr_view"
RIG = "demo-hacf"  # a stand-in rig name
WID, TAB = "wK", "wK:t2"
OTHER = "w1"
SEATS = [
    "orch.lead",
    "build.execute",
    "build.audit",
    "desk.scribe",
    "spec.architect",
    "spec.red-team",
    "spec.resolve",
]
ROWS = [SEATS[:1], SEATS[1:4], SEATS[4:]]
SCRAMBLED = [SEATS[:3], SEATS[3:6], SEATS[6:]]
DEPRECATION = "view-layout.sh is deprecated; use herdr-view.py"

FAKE_COMMON = r"""
import json, os, sys
d = os.environ["FAKE_DIR"]
args = sys.argv[1:]
state = json.load(open(os.path.join(d, "state.json")))
tool = os.path.basename(sys.argv[0])
with open(os.path.join(d, "calls.log"), "a") as fh:
    fh.write(json.dumps([tool, *args]) + "\n")
def save():
    json.dump(state, open(os.path.join(d, "state.json"), "w"))
def die(code, text="error"):
    print(text, file=sys.stderr)
    sys.exit(code)
"""

FAKE_HERDR = r"""
def out(result):
    save()
    print(json.dumps({"id": "x", "result": result}))
    sys.exit(0)
def pane(pid):
    for p in state["panes"]:
        if p["pane_id"] == pid:
            return p
    die(1, "no such pane")
def leaves(t):
    return [t] if isinstance(t, str) else leaves(t["a"]) + leaves(t["b"])
def drop(t, pid):
    if isinstance(t, str):
        return None if t == pid else t
    a, b = drop(t["a"], pid), drop(t["b"], pid)
    if a is None:
        return b
    if b is None:
        return a
    return {**t, "a": a, "b": b}
def split(t, target, new):
    if isinstance(t, str):
        return {**new, "a": t} if t == target else t
    return {**t, "a": split(t["a"], target, new), "b": split(t["b"], target, new)}
def rects(t, x, y, w, h):
    if isinstance(t, str):
        return [{"pane_id": t, "rect": {"x": x, "y": y, "width": w, "height": h}}]
    if t["dir"] == "down":
        first = round(h * t["ratio"])
        return rects(t["a"], x, y, w, first) + rects(t["b"], x, y + first, w, h - first)
    first = round(w * t["ratio"])
    return rects(t["a"], x, y, first, h) + rects(t["b"], x + first, y, w - first, h)
def remove(pid):
    tab = pane(pid)["tab_id"]
    tree = drop(state["trees"].get(tab, pid), pid)
    state["fixed"].pop(tab, None)
    if tree is None:
        state["trees"].pop(tab, None)
    else:
        state["trees"][tab] = tree
verb = args[:2]
if verb == ["workspace", "list"]:
    out({"workspaces": state["workspaces"]})
if verb == ["workspace", "close"]:
    gone = args[2]
    state["workspaces"] = [w for w in state["workspaces"] if w["workspace_id"] != gone]
    state["panes"] = [p for p in state["panes"] if p["workspace_id"] != gone]
    out({})
if verb == ["workspace", "rename"]:
    for w in state["workspaces"]:
        if w["workspace_id"] == args[2]:
            w["label"] = args[3]
    out({})
if verb == ["workspace", "focus"]:
    if not state.get("focus_stuck"):
        for w in state["workspaces"]:
            w["focused"] = w["workspace_id"] == args[2]
    out({})
if verb == ["pane", "list"]:
    out({"panes": state["panes"]})
if verb == ["pane", "read"]:
    if len(args) < 3 or args[2].startswith("-"):
        die(2, "unknown option: " + args[2])  # Herdr 0.9.5: the pane id is positional
    pane(args[2])
    source = args[args.index("--source") + 1] if "--source" in args else "recent"
    seen = state["screens"].get(args[2], "$ idle")
    if source != "visible":
        seen = state["scrollback"].get(args[2], "") + seen
    print(seen)
    sys.exit(0)
if verb == ["pane", "process-info"]:
    pid = args[args.index("--pane") + 1]
    out({"process_info": state["procs"][pid]})
if verb == ["pane", "layout"]:
    pid = args[args.index("--pane") + 1]
    tab = pane(pid)["tab_id"]
    if tab in state["fixed"]:
        panes = state["fixed"][tab]
    else:
        panes = rects(state["trees"].get(tab, pid), 0, 0, 280, 65)
    out({"layout": {"tab_id": tab, "workspace_id": pane(pid)["workspace_id"], "panes": panes}})
if verb == ["pane", "close"]:
    pane(args[2])
    if args[2] == state.get("fail_close"):
        die(1, "refused")
    remove(args[2])
    state["panes"] = [p for p in state["panes"] if p["pane_id"] != args[2]]
    out({})
if verb == ["pane", "move"]:
    pid = args[2]
    state["moves"] += 1
    save()
    if state["moves"] == state.get("fail_move"):
        die(1, "refused")
    opt = {args[i]: args[i + 1] for i in range(3, len(args) - 1) if args[i].startswith("--")}
    p = pane(pid)
    if state.get("steal_focus"):
        for w in state["workspaces"]:
            w["focused"] = w["workspace_id"] == state["steal_focus"]
    remove(pid)
    if "--new-tab" in args:
        p["tab_id"] = "%s:n%d" % (p["workspace_id"], state["moves"])
        state["trees"][p["tab_id"]] = pid
    else:
        tab = opt["--tab"]
        target = opt["--target-pane"]
        assert pane(target)["tab_id"] == tab, "target not in tab"
        ratio = float(opt.get("--ratio", "0.5"))
        if state.get("ignore_ratio"):
            opt["--split"], ratio = "down", 0.5
        new = {"dir": opt["--split"], "ratio": ratio, "b": pid}
        state["trees"][tab] = split(state["trees"].get(tab, target), target, new)
        state["fixed"].pop(tab, None)
        p["tab_id"] = tab
    out({"move_result": {"pane": p}})
die(3, "unexpected herdr call")
"""

FAKE_RIG = r"""
if args[:2] == ["ps", "--nodes"]:
    if state.get("ps_exit"):
        die(state["ps_exit"])
    print(state.get("ps_text") or json.dumps(state["nodes"]))
    sys.exit(0)
if args[:2] == ["spec", "show"]:
    if state.get("spec_exit"):
        die(state["spec_exit"])
    print(json.dumps(state["spec"]))
    sys.exit(0)
if args[:2] == ["terminal", "open"]:
    new = state.get("on_open")
    if new:
        for w in state["workspaces"]:
            w["focused"] = False
        state["workspaces"].append(new["workspace"])
        state["panes"] += new["panes"]
        state["trees"].update(new["trees"])
        state["procs"].update(new["procs"])
        state["sizes"] = {s: "manual" for s in state["sizes"]}
    save()
    print(json.dumps(state.get("open_reply", {"ok": True, "absent": [], "degraded": False})))
    sys.exit(0)
die(3, "unexpected rig call")
"""

FAKE_TMUX = r"""
session = args[args.index("-t") + 1].rstrip(":")
if session not in state["sizes"]:
    die(1, "no such window: %s:" % session)
if args[0] == "show":
    if state["sizes"][session]:
        print("window-size " + state["sizes"][session])
    sys.exit(0)
if args[0] == "set":
    if state.get("tmux_set_exit"):
        die(1, "refused")
    state["sizes"][session] = args[-1]
    save()
    sys.exit(0)
if args[0] == "resize-window":
    sys.exit(0)
die(3, "unexpected tmux call")
"""


def fixture(name):
    """One recorded fixture, freshly parsed."""
    return json.loads((FIXTURES / name).read_text())


def tree_of(rows):
    """A split tree that lays pane ids out in these rows (equal heights and widths)."""

    def chain(items, direction):
        if len(items) == 1:
            return items[0]
        return {
            "dir": direction,
            "ratio": 1 / len(items),
            "a": items[0],
            "b": chain(items[1:], direction),
        }

    return chain([chain(row, "right") for row in rows], "down")


class World:
    """The recorded team view as mutable state, with the fakes that serve it."""

    def __init__(self, tmp_path, rows=None):
        """Write the fakes and load the fixtures; `rows` replaces the recorded layout."""
        self.dir = tmp_path
        self.bin = tmp_path / "bin"
        self.proc = tmp_path / "proc"
        self.bin.mkdir(parents=True)
        self.proc.mkdir()
        for name, body in (("herdr", FAKE_HERDR), ("rig", FAKE_RIG), ("tmux", FAKE_TMUX)):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n{FAKE_COMMON}\n{body}")
            path.chmod(0o755)
        (self.bin / "python3").symlink_to(sys.executable)
        nodes = fixture("rig-ps.json")
        layout = fixture("pane-layout.json")["result"]["layout"]
        procs = {k: v["result"]["process_info"] for k, v in fixture("process-info.json").items()}
        self.state = {
            "nodes": nodes,
            "spec": fixture("rig-spec-show.json"),
            "workspaces": fixture("workspace-list.json")["result"]["workspaces"],
            "panes": fixture("pane-list.json")["result"]["panes"],
            "procs": procs,
            "fixed": {layout["tab_id"]: layout["panes"]},
            "trees": {},
            "screens": {},
            "scrollback": {},
            "sizes": {n["canonicalSessionName"]: "latest" for n in nodes},
            "moves": 0,
        }
        for info in procs.values():
            self.proc_dir(info["shell_pid"])
        if rows is not None:
            self.arrange_as(rows)

    def proc_dir(self, pid, children=""):
        """A process in the fake proc tree, with its children file."""
        task = self.proc / str(pid) / "task" / str(pid)
        task.mkdir(parents=True, exist_ok=True)
        (task / "children").write_text(children)

    def pane_id(self, label):
        """The first pane carrying this label."""
        return next(p["pane_id"] for p in self.state["panes"] if p.get("label") == label)

    def arrange_as(self, rows):
        """Replace the recorded rects of the team's tab by these rows."""
        self.state["fixed"].pop(TAB, None)
        extra = [
            p["pane_id"]
            for p in self.state["panes"]
            if p["tab_id"] == TAB and p.get("label") not in sum(rows, [])
        ]
        ids = [[self.pane_id(s) for s in row] for row in rows]
        if extra:
            ids.append(extra)
        self.state["trees"][TAB] = tree_of(ids)

    def add_pane(self, pid, *, tab=TAB, wid=WID, label=None, agent=None, names=("zsh",),
                 shell_pid=None, fg_pids=None, children="", proc=True):  # fmt: skip
        """A pane that is not in the recording; by default an idle shell."""
        number = 900000 + len(self.state["panes"])
        shell = shell_pid or number
        pane = {"pane_id": pid, "tab_id": tab, "workspace_id": wid}
        if label:
            pane["label"] = label
        if agent:
            pane["agent"] = agent
        pids = fg_pids or [shell + i for i in range(len(names))]
        self.state["panes"].append(pane)
        self.state["procs"][pid] = {
            "pane_id": pid,
            "shell_pid": shell,
            "foreground_processes": [{"pid": i, "name": n} for i, n in zip(pids, names)],
        }
        if proc:
            self.proc_dir(shell, children)

    def add_seat(self, pod, member, *, tab=TAB):
        """One more seat: in the spec, in `rig ps`, and as a pane."""
        logical = f"{pod}.{member}"
        pods = {p["id"]: p for p in self.state["spec"]["pods"]}
        if pod not in pods:
            self.state["spec"]["pods"].append({"id": pod, "members": []})
        next(p for p in self.state["spec"]["pods"] if p["id"] == pod)["members"].append(
            {"id": member}
        )
        session = f"{pod}-{member}@{RIG}"
        self.state["nodes"].append(
            dict(self.state["nodes"][0], logicalId=logical, canonicalSessionName=session)
        )
        self.state["sizes"][session] = "latest"
        self.add_pane(f"{WID}:{member}", tab=tab, label=logical, agent="claude", names=("sh",))

    def drop_seat(self, logical):
        """A smaller team: no node, no spec member and no pane."""
        pod, member = logical.split(".")
        for p in self.state["spec"]["pods"]:
            if p["id"] == pod:
                p["members"] = [m for m in p["members"] if m["id"] != member]
        self.state["nodes"] = [n for n in self.state["nodes"] if n["logicalId"] != logical]
        self.state["panes"] = [p for p in self.state["panes"] if p.get("label") != logical]

    def focus(self, wid):
        """Give this space the focus."""
        for w in self.state["workspaces"]:
            w["focused"] = w["workspace_id"] == wid

    def run(self, *args, herdr=True, tmux=True):
        """Run the script against the fakes, then reload the state."""
        for name, wanted in (("herdr", herdr), ("tmux", tmux)):
            if not wanted:
                (self.bin / name).unlink()
        (self.dir / "state.json").write_text(json.dumps(self.state))
        (self.dir / "calls.log").write_text("")
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.dir),
            "LC_ALL": "C",
            "FAKE_DIR": str(self.dir),
            "HACF_HERDR_VIEW_PROC": str(self.proc),
            "HACF_HERDR_VIEW_SETTLE": "0",
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True,
            timeout=60,
        )  # fmt: skip
        self.state = json.loads((self.dir / "state.json").read_text())
        return result

    def go(self, *args, **kw):
        """Run for the stand-in rig."""
        return self.run("--rig", RIG, *args, **kw)

    def calls(self, tool=None):
        """Every logged argv, or one tool's."""
        lines = (self.dir / "calls.log").read_text().splitlines()
        calls = [json.loads(line) for line in lines]
        return calls if tool is None else [c[1:] for c in calls if c[0] == tool]

    def writes(self):
        """Every Herdr write call, in order."""
        reads = {("workspace", "list"), ("pane", "list"), ("pane", "read"),
                 ("pane", "layout"), ("pane", "process-info")}  # fmt: skip
        return [c for c in self.calls("herdr") if tuple(c[:2]) not in reads]

    def labels_by_row(self, tab=TAB):
        """The team tab's final rows, as seat labels."""
        names = {p["pane_id"]: p.get("label") for p in self.state["panes"]}
        tree = self.state["trees"][tab]

        def leaves(t, x, y, w, h):
            if isinstance(t, str):
                return [(y, x, names[t])]
            down = t["dir"] == "down"
            first = round((h if down else w) * t["ratio"])
            if down:
                return leaves(t["a"], x, y, w, first) + leaves(t["b"], x, y + first, w, h - first)
            return leaves(t["a"], x, y, first, h) + leaves(t["b"], x + first, y, w - first, h)

        grouped = {}
        for y, x, name in leaves(tree, 0, 0, 280, 65):
            grouped.setdefault(y, []).append((x, name))
        return [[n for _, n in sorted(v)] for _, v in sorted(grouped.items())]


@pytest.fixture
def world(tmp_path):
    """The recorded view, already arranged."""
    return World(tmp_path)


@pytest.fixture
def scrambled(tmp_path):
    """The recorded view, scrambled."""
    return World(tmp_path, rows=SCRAMBLED)


def out_tab(w, seat):
    """The move of a seat's pane to a new tab."""
    return ["pane", "move", w.pane_id(seat), "--new-tab", "--workspace", WID, "--no-focus"]


def back(w, seat, target, split, ratio):
    """The move of a seat's pane back into the team's tab."""
    return [
        "pane", "move", w.pane_id(seat), "--tab", TAB, "--target-pane", w.pane_id(target),
        "--split", split, "--ratio", ratio, "--no-focus",
    ]  # fmt: skip


PLACES = [
    ("build.execute", "orch.lead", "down", "0.3333"),
    ("spec.architect", "build.execute", "down", "0.5"),
    ("build.audit", "build.execute", "right", "0.3333"),
    ("desk.scribe", "build.audit", "right", "0.5"),
    ("spec.red-team", "spec.architect", "right", "0.3333"),
    ("spec.resolve", "spec.red-team", "right", "0.5"),
]


def expected_moves(w):
    """The twelve moves that arrange a scrambled seven-seat view."""
    return [out_tab(w, s) for s in SEATS[1:]] + [back(w, *p) for p in PLACES]


def places(w):
    """(seat, target, split, ratio) of every return move, read from the log."""
    names = {p["pane_id"]: p.get("label") for p in w.state["panes"]}
    out = []
    for c in w.writes():
        if c[:2] == ["pane", "move"] and "--target-pane" in c:
            opt = {k: c[c.index(k) + 1] for k in ("--target-pane", "--split", "--ratio")}
            out.append((names[c[2]], names[opt["--target-pane"]], opt["--split"], opt["--ratio"]))
    return out


# --- MR 2: the seats and their order -----------------------------------------------------------


def test_script_is_executable() -> None:
    """The shipped script has the owner execute bit."""
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_seven_seats_are_one_three_three(scrambled) -> None:
    """MR 2: the standard seven end as 1 + 3 + 3."""
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.labels_by_row() == ROWS
    assert scrambled.calls("rig") == [
        ["ps", "--nodes", "--rig", RIG, "--json"],
        ["spec", "show", "01DEMORIGID0000000000000000", "--json"],
    ]


def test_order_is_the_specs_not_rig_ps(scrambled) -> None:
    """MR 2: the order of `rig ps` rows does not matter."""
    scrambled.state["nodes"].reverse()
    assert scrambled.go().returncode == 0
    assert scrambled.labels_by_row() == ROWS


def test_eight_seats_give_a_fourth_row(scrambled) -> None:
    """MR 2: an extra pod gets its own last row."""
    scrambled.add_seat("judge", "gemini")
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.labels_by_row() == [*ROWS, ["judge.gemini"]]
    assert [p[2:] for p in places(scrambled)[:3]] == [
        ("down", "0.25"),
        ("down", "0.3333"),
        ("down", "0.5"),
    ]


def test_five_seats_give_one_three_one(scrambled) -> None:
    """MR 2: five seats end as 1 + 3 + 1."""
    scrambled.drop_seat("spec.red-team")
    scrambled.drop_seat("spec.resolve")
    scrambled.arrange_as([["spec.architect", "orch.lead"], SEATS[1:4]])
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.labels_by_row() == [*ROWS[:2], ["spec.architect"]]


def test_a_last_row_of_two_is_split_evenly(scrambled) -> None:
    """MR 7: a row of two splits in half."""
    scrambled.drop_seat("spec.resolve")
    scrambled.arrange_as([SEATS[:3], SEATS[3:6]])
    assert scrambled.go().returncode == 0
    assert places(scrambled)[-1] == ("spec.red-team", "spec.architect", "right", "0.5")


def test_without_orch_lead_the_first_seat_leads(scrambled) -> None:
    """MR 2: the lead falls back to the first seat."""
    scrambled.drop_seat("orch.lead")
    scrambled.arrange_as([SEATS[1:3], SEATS[3:]])
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    # spec order is orch, spec, build, desk: the first seat leads, then build, desk, spec
    assert scrambled.labels_by_row() == [
        ["spec.architect"],
        ["build.execute", "build.audit", "desk.scribe"],
        ["spec.red-team", "spec.resolve"],
    ]


def test_a_seat_without_a_pane_is_named_and_left_out(scrambled) -> None:
    """MR 7: a missing pane is printed, not planned."""
    scrambled.state["panes"] = [
        p for p in scrambled.state["panes"] if p.get("label") != "desk.scribe"
    ]
    scrambled.arrange_as([SEATS[:3], SEATS[4:]])
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing pane: desk.scribe" in r.stdout
    assert scrambled.labels_by_row() == [
        ["orch.lead"],
        ["build.execute", "build.audit", "spec.architect"],
        ["spec.red-team", "spec.resolve"],
    ]


@pytest.mark.parametrize(
    "broken", [{"ps_exit": 1}, {"ps_text": "not json"}, {"ps_text": '{"a": 1}'}]
)
def test_unreadable_rig_ps_changes_nothing(scrambled, broken) -> None:
    """MR 2: no tmux or Herdr call after a bad `rig ps`."""
    scrambled.state.update(broken)
    r = scrambled.go()
    assert r.returncode == 1
    assert "unreadable; nothing changed" in r.stderr and "rig ps" in r.stderr
    assert scrambled.calls("herdr") == [] and scrambled.calls("tmux") == []


def test_unreadable_spec_show_changes_nothing(scrambled) -> None:
    """MR 2: the same after a failed `rig spec show`."""
    scrambled.state["spec_exit"] = 1
    r = scrambled.go()
    assert r.returncode == 1 and "rig spec show" in r.stderr
    assert scrambled.calls("herdr") == [] and scrambled.calls("tmux") == []


def test_no_seats_is_nothing_to_do(scrambled) -> None:
    """MR 2: another rig's rows are not this rig's."""
    scrambled.state["nodes"] = [dict(scrambled.state["nodes"][0], rigName="another")]
    r = scrambled.go()
    assert r.returncode == 0 and f"{RIG}: no seats; nothing to do" in r.stdout
    assert scrambled.calls("herdr") == [] and scrambled.calls("tmux") == []


# --- MR 3: the window size ---------------------------------------------------------------------


def test_a_manual_window_is_set_then_resized_and_a_latest_one_is_left(world) -> None:
    """MR 3: exact tmux calls."""
    first, second = (n["canonicalSessionName"] for n in world.state["nodes"][:2])
    world.state["sizes"][first] = "manual"
    r = world.go()
    assert r.returncode == 0, r.stdout + r.stderr
    tmux = world.calls("tmux")
    assert tmux[:3] == [
        ["show", "-w", "-t", f"{first}:", "window-size"],
        ["set", "-w", "-t", f"{first}:", "window-size", "latest"],
        ["resize-window", "-A", "-t", first],
    ]
    assert [c for c in tmux[3:] if c[0] != "show"] == []
    assert tmux[3] == ["show", "-w", "-t", f"{second}:", "window-size"]
    assert f"size: set window-size latest on {first}" in r.stdout
    # the size fix comes before any herdr call
    tools = [c[0] for c in world.calls()]
    assert max(i for i, t in enumerate(tools) if t == "tmux") < tools.index("herdr")


def test_an_unset_window_size_is_set(world) -> None:
    """MR 3: empty output is not `window-size latest`."""
    first = world.state["nodes"][0]["canonicalSessionName"]
    world.state["sizes"][first] = ""
    assert world.go("--fix-size-only").returncode == 0
    assert world.state["sizes"][first] == "latest"


def test_fix_size_only_makes_no_herdr_call(scrambled) -> None:
    """MR 3: a missing session is skipped, and nothing but `rig ps` and tmux runs."""
    scrambled.state["spec_exit"] = 1  # not read for this step
    gone = scrambled.state["nodes"][1]["canonicalSessionName"]
    del scrambled.state["sizes"][gone]
    r = scrambled.go("--fix-size-only")
    assert r.returncode == 0, r.stderr
    assert f"size: no tmux session {gone}; skipped" in r.stdout
    assert scrambled.calls("herdr") == []
    assert [c[0] for c in scrambled.calls("rig")] == ["ps"]


def test_no_tmux_is_not_a_failure(world) -> None:
    """MR 3: no tmux on PATH."""
    r = world.go("--fix-size-only", tmux=False)
    assert r.returncode == 0 and "size: tmux not installed; skipped" in r.stdout


def test_a_failed_tmux_set_exits_1(world) -> None:
    """MR 1: a failed tmux call."""
    for session in world.state["sizes"]:
        world.state["sizes"][session] = "manual"
    world.state["tmux_set_exit"] = 1
    r = world.go("--fix-size-only")
    assert r.returncode == 1 and "refused" not in r.stdout and "tmux set" in r.stderr


def test_dry_run_size_fix_writes_nothing(world) -> None:
    """MR 3 and MR 9."""
    first = world.state["nodes"][0]["canonicalSessionName"]
    world.state["sizes"][first] = "manual"
    r = world.go("--fix-size-only", "--dry-run")
    assert r.returncode == 0
    assert f"size: would set window-size latest on {first}" in r.stdout
    assert {c[0] for c in world.calls("tmux")} == {"show"}


# --- MR 5: a detached or missing space ---------------------------------------------------------


def reopened(w, wid="wZ", label="openrig-view"):
    """What `rig terminal open` adds: a space of seat panes, scrambled, under another label."""
    panes, procs = [], {}
    for p in w.state["panes"]:
        if p["workspace_id"] != WID or not p.get("label"):
            continue
        pid = p["pane_id"].replace(WID, wid)
        panes.append(dict(p, pane_id=pid, tab_id=f"{wid}:t1", workspace_id=wid))
        procs[pid] = dict(w.state["procs"][p["pane_id"]], pane_id=pid)
    return {
        "workspace": {"workspace_id": wid, "label": label, "focused": True},
        "panes": panes,
        "procs": procs,
        "trees": {f"{wid}:t1": tree_of([[p["pane_id"] for p in panes]])},
    }


def detach(w, seat="build.audit"):
    """Show the detached message on one seat's screen."""
    w.state["screens"][w.pane_id(seat)] = "[detached (from session $204)]"


def test_a_detached_space_is_closed_reopened_renamed_and_sized_again(world) -> None:
    """MR 5."""
    world.focus(OTHER)
    detach(world)
    world.add_pane("wK:spare", tab="wK:t9")
    world.state["on_open"] = reopened(world)
    r = world.go()
    assert r.returncode == 0, r.stdout + r.stderr
    writes = world.writes()
    assert writes[0] == ["workspace", "close", WID]
    assert writes[1] == ["workspace", "rename", "wZ", RIG]
    assert writes[-1] == ["workspace", "focus", OTHER]
    assert ["terminal", "open", RIG, "--provider", "herdr", "--json"] in world.calls("rig")
    assert set(world.state["sizes"].values()) == {"latest"}
    assert [c for c in world.calls("tmux") if c[0] == "set"]
    assert world.labels_by_row("wZ:t1") == ROWS


def test_pane_read_uses_the_positional_id_and_the_visible_screen(world) -> None:
    """P50: the exact read argv."""
    assert world.go().returncode == 0
    reads = [c for c in world.calls("herdr") if c[:2] == ["pane", "read"]]
    assert reads == [["pane", "read", world.pane_id(s), "--source", "visible"] for s in
                     [p["label"] for p in world.state["panes"] if p.get("label")]]  # fmt: skip


def test_old_scrollback_does_not_make_a_space_detached(world) -> None:
    """P50: only the visible screen counts."""
    world.state["scrollback"][world.pane_id("build.audit")] = "[detached (from session $204)]\n"
    r = world.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert world.writes() == []
    assert not [c for c in world.calls("rig") if c[0] == "terminal"]


def test_detached_with_a_busy_pane_is_not_closed(world) -> None:
    """MR 5: such a space is left alone."""
    detach(world)
    world.add_pane("wK:busy", tab="wK:t9", names=("node",))
    r = world.go()
    assert r.returncode == 1
    assert f"{RIG} ({WID}): detached, but holds other panes; not closed" in r.stderr
    assert world.writes() == []
    assert not [c for c in world.calls("rig") if c[0] == "terminal"]


def test_a_missing_space_is_opened(world) -> None:
    """MR 5: no rename when the label already matches."""
    new = reopened(world, label=RIG)
    world.state["workspaces"] = [w for w in world.state["workspaces"] if w["workspace_id"] != WID]
    world.state["panes"] = [p for p in world.state["panes"] if p["workspace_id"] != WID]
    world.state["on_open"] = new
    world.focus(OTHER)
    r = world.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [c for c in world.writes() if c[:2] == ["workspace", "rename"]]
    assert world.labels_by_row("wZ:t1") == ROWS


@pytest.mark.parametrize(
    "reply",
    [{"ok": False}, {"ok": True, "absent": ["x"]}, {"ok": True, "degraded": True}, []],
)
def test_a_bad_open_reply_exits_1(world, reply) -> None:
    """MR 5: a partial or unreadable open."""
    detach(world)
    world.state["open_reply"] = reply
    r = world.go()
    assert r.returncode == 1 and "rig terminal open" in r.stderr
    assert world.writes() == [["workspace", "close", WID]]


def test_an_open_that_shows_no_new_space_renames_nothing_else(world) -> None:
    """MR 8: the person's space is safe."""
    world.focus(OTHER)
    detach(world)
    r = world.go()  # no on_open: the only focused space is the person's own
    assert r.returncode == 1 and "reopened space not found" in r.stderr
    assert world.writes() == [["workspace", "close", WID]]


# --- MR 6: idle shells -------------------------------------------------------------------------


def test_an_idle_shell_in_the_lead_tab_is_closed(scrambled) -> None:
    """MR 6: all conditions hold."""
    scrambled.add_pane("wK:x")
    scrambled.arrange_as(SCRAMBLED)
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.writes()[0] == ["pane", "close", "wK:x"]


@pytest.mark.parametrize(
    "flip",
    [
        {"agent": "claude"},
        {"label": "build.audit"},
        {"names": ("zsh", "vim")},
        {"names": ("zsh",), "fg_pids": [5]},
        {"names": ("node",)},
        {"children": "123 "},
        {"proc": False},
    ],
    ids=["agent", "seat-label", "two-foreground", "not-the-shell", "node", "child", "no-proc"],
)
def test_anything_else_is_never_closed(scrambled, flip) -> None:
    """MR 6: one condition flipped; the pane moves instead."""
    scrambled.add_pane("wK:x", **flip)
    scrambled.arrange_as(SCRAMBLED)
    scrambled.state["trees"][TAB] = {
        "dir": "down", "ratio": 0.9, "a": scrambled.state["trees"][TAB], "b": "wK:x",
    } if "label" in flip else scrambled.state["trees"][TAB]  # fmt: skip
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.writes()[0] == [
        "pane", "move", "wK:x", "--new-tab", "--workspace", WID, "--no-focus",
    ]  # fmt: skip
    assert not [c for c in scrambled.writes() if c[:2] == ["pane", "close"]]


@pytest.mark.parametrize("name", ["bash", "zsh", "sh", "fish", "dash", "/usr/bin/zsh"])
def test_each_shell_name_counts(scrambled, name) -> None:
    """MR 6: the shell names, as basenames."""
    scrambled.add_pane("wK:x", tab="wK:t9", names=(name,))
    assert scrambled.go().returncode == 0
    assert scrambled.writes()[-1] == ["pane", "close", "wK:x"]


def test_a_child_of_another_thread_counts(scrambled) -> None:
    """MR 6: every thread's children file is read."""
    scrambled.add_pane("wK:x", tab="wK:t9", shell_pid=777)
    other = scrambled.proc / "777" / "task" / "778"
    other.mkdir(parents=True)
    (other / "children").write_text("779")
    assert scrambled.go().returncode == 0
    assert not [c for c in scrambled.writes() if c[:2] == ["pane", "close"]]


# --- MR 7: the layout --------------------------------------------------------------------------


def test_the_exact_moves_and_ratios(scrambled) -> None:
    """MR 7: the whole write sequence."""
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.writes() == expected_moves(scrambled)
    assert f"{RIG} ({WID}): arranged" in r.stdout


def test_the_recorded_view_is_already_in_layout(world) -> None:
    """MR 7: no move or close."""
    world.add_pane("wK:x", tab="wK:t9")
    r = world.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{RIG} ({WID}): already in layout" in r.stdout
    assert world.writes() == []


def test_a_second_run_changes_nothing(scrambled) -> None:
    """MR 7: the result is recognised."""
    assert scrambled.go().returncode == 0
    r = scrambled.go()
    assert r.returncode == 0 and "already in layout" in r.stdout
    assert scrambled.writes() == []


def test_a_failing_move_brings_the_seats_back(scrambled) -> None:
    """MR 7: recovery."""
    scrambled.state["fail_move"] = 8  # the second return move
    r = scrambled.go()
    assert r.returncode == 1
    recovery = scrambled.writes()[8:]
    lead = scrambled.pane_id("orch.lead")
    # build.execute is back already; every other seat is still out
    assert recovery == [
        ["pane", "move", scrambled.pane_id(s), "--tab", TAB, "--target-pane", lead,
         "--split", "down", "--no-focus"]
        for s in SEATS[2:]
    ]  # fmt: skip
    assert {p["tab_id"] for p in scrambled.state["panes"] if p.get("label")} == {TAB}


def test_a_wrong_final_layout_exits_1(scrambled) -> None:
    """MR 7: the final check."""
    scrambled.state["ignore_ratio"] = True
    r = scrambled.go()
    assert r.returncode == 1 and "NOT in the planned layout" in r.stderr


def test_an_idle_shell_outside_the_lead_tab_is_closed_after_the_moves(scrambled) -> None:
    """MR 7: the rounds."""
    scrambled.add_pane("wK:x", tab="wK:t9")
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    assert scrambled.writes() == [*expected_moves(scrambled), ["pane", "close", "wK:x"]]


def test_a_failing_close_exits_1(scrambled) -> None:
    """MR 1: a failed Herdr call."""
    scrambled.add_pane("wK:x", tab="wK:t9")
    scrambled.state["fail_close"] = "wK:x"
    r = scrambled.go()
    assert r.returncode == 1 and "pane close" in r.stderr


def test_no_swap_and_every_move_keeps_focus(scrambled) -> None:
    """MR 7: never `pane swap`."""
    scrambled.add_pane("wK:x")
    scrambled.add_pane("wK:y", tab="wK:t9")
    scrambled.arrange_as(SCRAMBLED)
    assert scrambled.go().returncode == 0
    calls = scrambled.calls("herdr")
    assert not [c for c in calls if c[:2] == ["pane", "swap"]]
    assert all("--no-focus" in c for c in calls if c[:2] == ["pane", "move"])
    assert "swap" not in re.findall(r'"([a-z-]+)"', SCRIPT.read_text())


# --- MR 8: focus, and other spaces -------------------------------------------------------------


def test_focus_is_restored_and_no_other_space_is_written(scrambled) -> None:
    """MR 8."""
    scrambled.focus(OTHER)
    scrambled.add_pane("w1:x", tab="w1:t1", wid=OTHER)
    scrambled.state["steal_focus"] = WID  # something takes focus while panes move
    r = scrambled.go()
    assert r.returncode == 0, r.stdout + r.stderr
    writes = scrambled.writes()
    assert writes[-1] == ["workspace", "focus", OTHER]
    foreign = {p["pane_id"] for p in scrambled.state["panes"] if p["workspace_id"] == OTHER}
    for call in writes[:-1]:
        assert OTHER not in call and not foreign & set(call), call


def test_focus_not_held_exits_1(scrambled) -> None:
    """MR 8: eight re-checks, then exit 1."""
    scrambled.focus(OTHER)
    scrambled.state["focus_stuck"] = True
    scrambled.state["steal_focus"] = WID
    r = scrambled.go()
    assert r.returncode == 1 and "focus not restored" in r.stderr
    after = scrambled.calls("herdr")
    last_focus = max(i for i, c in enumerate(after) if c[:2] == ["workspace", "focus"])
    assert after[last_focus + 1 :] == [["workspace", "list"]] * 8


# --- MR 9: dry run -----------------------------------------------------------------------------


def dry_world(path):
    """A scrambled view: manual sizes, an idle and a busy extra pane, an idle shell elsewhere."""
    w = World(path, rows=SCRAMBLED)
    w.add_pane("wK:idle")
    w.add_pane("wK:busy", names=("node",))
    w.add_pane("wK:spare", tab="wK:t9")
    w.arrange_as(SCRAMBLED)
    for session in w.state["sizes"]:
        w.state["sizes"][session] = "manual"
    return w


def test_dry_run_lists_exactly_what_a_real_run_does(tmp_path) -> None:
    """MR 9."""
    dry, real = dry_world(tmp_path / "dry"), dry_world(tmp_path / "real")
    r = dry.go("--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert dry.writes() == []
    assert {c[0] for c in dry.calls("tmux")} == {"show"}
    assert not [c for c in dry.calls("rig") if c[0] == "terminal"]
    lines = r.stdout.splitlines()
    listed = lines[lines.index(f"{RIG} ({WID}): plan (dry run)") + 1 :]

    assert real.go().returncode == 0
    names = {p["pane_id"]: p.get("label") for p in dry.state["panes"]}
    procs = dry.state["procs"]
    done = []
    for c in real.writes():
        if c[:2] == ["pane", "close"]:
            info = procs[c[2]]
            done.append(f"close {c[2]} (idle shell zsh pid {info['shell_pid']})")
        elif "--target-pane" in c:
            target = names[c[c.index("--target-pane") + 1]]
            split, ratio = c[c.index("--split") + 1], c[c.index("--ratio") + 1]
            done.append(
                f"move {names[c[2]]} back: --target-pane {target} --split {split} --ratio {ratio}"
            )
        elif names[c[2]] in SEATS:
            done.append(f"move {names[c[2]]} to a new tab")
        else:
            done.append(f"move {c[2]} (unlabelled) to a new tab")
    assert listed == done
    assert listed[0].startswith("close wK:idle") and listed[-1].startswith("close wK:spare")


def test_dry_run_of_a_detached_space(world) -> None:
    """MR 9: stops at the reopen."""
    detach(world)
    r = world.go("--dry-run")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-3:] == [
        f"{RIG} ({WID}): plan (dry run)",
        f"close space {WID} (detached)",
        f"reopen: rig terminal open {RIG} --provider herdr",
    ]
    assert world.writes() == []
    assert not [c for c in world.calls("rig") if c[0] == "terminal"]


# --- MR 1 and MR 4 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args", [[], ["--rig", "  "], ["--rig"], ["--rig", RIG, "--nope"], ["--rig", RIG, "extra"]]
)
def test_usage_errors_make_no_call(world, args) -> None:
    """MR 1."""
    r = world.run(*args)
    assert r.returncode == 2, r.stdout
    assert world.calls() == []


def test_two_spaces_with_the_label_change_nothing(scrambled) -> None:
    """MR 4."""
    scrambled.state["workspaces"][1]["label"] = f" {RIG} "
    r = scrambled.go()
    assert r.returncode == 1
    assert (
        f"{RIG}: 2 Herdr spaces are labelled {RIG}; close the extra ones; nothing changed"
        in r.stderr
    )
    assert scrambled.writes() == []


def test_no_herdr_is_nothing_to_arrange(world) -> None:
    """MR 4."""
    r = world.go(herdr=False)
    assert r.returncode == 0 and "herdr: not installed; nothing to arrange" in r.stdout


# --- MR 10 -------------------------------------------------------------------------------------


def test_view_layout_says_it_is_deprecated(world) -> None:
    """MR 10."""
    (world.bin / "herdr").unlink()
    r = subprocess.run(
        ["/bin/bash", str(OLD_SCRIPT), "--rig", "x"],
        env={"PATH": str(world.bin), "HOME": str(world.dir), "LC_ALL": "C"},
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert r.stderr == DEPRECATION + "\n"
    assert r.stdout == "herdr: not installed; nothing to arrange\n"


# --- privacy and literals ----------------------------------------------------------------------


def test_the_script_has_no_host_literal() -> None:
    """No host path or PATH literal in the tool."""
    text = SCRIPT.read_text()
    for literal in ("/" + "home/", ".nvm", "PATH="):
        assert literal not in text
