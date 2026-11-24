"""openrig/hacf/bin/view-layout.sh arranges a HACF team's Herdr view (v064-process OPR.99.0.9.3).

No real `rig` or `herdr`: a fake `rig` serves canned `rig ps --nodes --json`, and a fake `herdr`
serves canned workspaces, panes and layouts from a state file and logs every argv. The fake's
`pane layout` reports the arranged rows once all six return moves of that workspace have been made.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "openrig" / "hacf" / "bin" / "view-layout.sh"
RIG = "demo-hacf"  # a stand-in rig name
SEATS = [
    "orch.lead",
    "build.execute",
    "build.audit",
    "desk.scribe",
    "spec.architect",
    "spec.red-team",
    "spec.resolve",
]
ARRANGED = [
    ("orch.lead", 0, 0),
    ("build.execute", 0, 10),
    ("build.audit", 10, 10),
    ("desk.scribe", 20, 10),
    ("spec.architect", 0, 20),
    ("spec.red-team", 10, 20),
    ("spec.resolve", 20, 20),
]
# The default OpenRig grid: spec.resolve alone along the bottom.
SCRAMBLED = [
    ("orch.lead", 0, 0),
    ("build.execute", 10, 0),
    ("build.audit", 20, 0),
    ("desk.scribe", 0, 10),
    ("spec.architect", 10, 10),
    ("spec.red-team", 20, 10),
    ("spec.resolve", 0, 20),
]

FAKE_HERDR = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state = json.load(open(os.environ["FAKE_STATE"]))
log = os.environ["FAKE_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps(args) + "\n")
calls = [json.loads(l) for l in open(log)]
def out(result):
    print(json.dumps({"result": result}))
    sys.exit(0)
if args[:2] == ["workspace", "list"]:
    out({"workspaces": state["workspaces"]})
if args[:2] == ["pane", "list"]:
    out({"panes": state["panes"][args[args.index("--workspace") + 1]]})
if args[:2] == ["pane", "move"]:
    n = sum(1 for c in calls if c[:2] == ["pane", "move"])
    if n == state.get("fail_move"):
        print("error: refused", file=sys.stderr)
        sys.exit(1)
    out({"moved": args[2]})
if args[:2] == ["pane", "layout"]:
    pane = args[args.index("--pane") + 1]
    wid = next(w for w, ps in state["panes"].items() if any(p["pane_id"] == pane for p in ps))
    ids = {p["pane_id"] for p in state["panes"][wid]}
    back = sum(
        1 for c in calls if c[:2] == ["pane", "move"] and "--target-pane" in c and c[2] in ids
    )
    rects = state["layouts"][wid]
    if back >= 6:
        rects = state["arranged"][wid]
    out({"layout": {"panes": [{"pane_id": p, "rect": {"x": x, "y": y}} for p, x, y in rects]}})
sys.exit(3)
"""


def nodes(seats=SEATS, rig=RIG):
    """Canned `rig ps --nodes --json` rows for these seats."""
    return [{"rigName": rig, "logicalId": s, "canonicalSessionName": s} for s in seats]


def workspace(wid, *, layout=SCRAMBLED, labels=SEATS, tabs=None):
    """One workspace's panes (pane ids `<wid>-<seat>`) and its current and arranged rects."""
    tabs = tabs or {}
    panes = [
        {"pane_id": f"{wid}-{s}", "tab_id": tabs.get(s, f"{wid}-t1"), "label": s} for s in labels
    ]
    return (
        panes,
        [(f"{wid}-{s}", x, y) for s, x, y in layout if s in labels],
        [(f"{wid}-{s}", x, y) for s, x, y in ARRANGED if s in labels],
    )


def setup(tmp_path, wss, *, rig_nodes=None, rig_exit=0, fail_move=None, herdr=True):
    """Fake binaries and state; return the env for the script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rig").write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$FAKE_RIG_LOG"\n'
        f"[ {rig_exit} -eq 0 ] || exit {rig_exit}\n"
        'cat "$FAKE_RIG_JSON"\n'
    )
    (tmp_path / "rig.json").write_text(json.dumps(nodes() if rig_nodes is None else rig_nodes))
    if herdr:
        (bin_dir / "herdr").write_text(FAKE_HERDR)
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    state = {"workspaces": [], "panes": {}, "layouts": {}, "arranged": {}, "fail_move": fail_move}
    for wid, (panes, layout, arranged) in wss.items():
        state["workspaces"].append({"workspace_id": wid, "label": f"label-{wid}"})
        state["panes"][wid], state["layouts"][wid], state["arranged"][wid] = panes, layout, arranged
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "herdr.log").write_text("")
    return {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "LC_ALL": "C",
        "FAKE_STATE": str(tmp_path / "state.json"),
        "FAKE_LOG": str(tmp_path / "herdr.log"),
        "FAKE_RIG_JSON": str(tmp_path / "rig.json"),
        "FAKE_RIG_LOG": str(tmp_path / "rig.log"),
    }


def run(env, *args):
    """Run the script with these arguments."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def herdr_calls(tmp_path):
    """Every argv the fake herdr saw, in order."""
    return [json.loads(line) for line in (tmp_path / "herdr.log").read_text().splitlines()]


def moves(tmp_path):
    """The `pane move` calls only."""
    return [c for c in herdr_calls(tmp_path) if c[:2] == ["pane", "move"]]


def p(seat, wid="w1"):
    """The fake pane id of a seat."""
    return f"{wid}-{seat}"


def out_tab(seat, wid="w1"):
    """The move of a seat's pane to a temporary tab."""
    return ["pane", "move", p(seat, wid), "--new-tab", "--workspace", wid, "--no-focus"]


def back(seat, target, split, ratio, wid="w1"):
    """The move of a seat's pane back into the view's tab."""
    return [
        "pane", "move", p(seat, wid), "--tab", f"{wid}-t1", "--target-pane", p(target, wid),
        "--split", split, "--ratio", ratio, "--no-focus",
    ]  # fmt: skip


EXPECTED_MOVES = [out_tab(s) for s in SEATS[1:]] + [
    back("build.execute", "orch.lead", "down", "0.3333"),
    back("spec.architect", "build.execute", "down", "0.5"),
    back("build.audit", "build.execute", "right", "0.3333"),
    back("desk.scribe", "build.audit", "right", "0.5"),
    back("spec.red-team", "spec.architect", "right", "0.3333"),
    back("spec.resolve", "spec.red-team", "right", "0.5"),
]


def test_script_is_executable() -> None:
    """The shipped script has the owner execute bit."""
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_scrambled_view_is_arranged(tmp_path) -> None:
    """A scrambled 7-pane workspace: the exact moves and ratios, then a verified layout."""
    env = setup(tmp_path, {"w1": workspace("w1")})
    r = run(env, "--rig", RIG)
    assert r.returncode == 0, r.stderr
    assert moves(tmp_path) == EXPECTED_MOVES
    assert herdr_calls(tmp_path)[-1] == ["pane", "layout", "--pane", p("orch.lead")]
    assert "rearranging" in r.stdout and "-> OK" in r.stdout
    assert (tmp_path / "rig.log").read_text().split() == ["ps", "--nodes", "--rig", RIG, "--json"]


def test_arranged_view_is_left_alone(tmp_path) -> None:
    """A view already in the rows: no pane move."""
    env = setup(tmp_path, {"w1": workspace("w1", layout=ARRANGED)})
    r = run(env, "--rig", RIG)
    assert r.returncode == 0, r.stderr
    assert moves(tmp_path) == []
    assert "already in layout" in r.stdout


def test_dry_run_moves_nothing(tmp_path) -> None:
    """--dry-run prints the plan and calls only read verbs."""
    env = setup(tmp_path, {"w1": workspace("w1")})
    r = run(env, "--rig", RIG, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert moves(tmp_path) == []
    assert {c[1] for c in herdr_calls(tmp_path)} <= {"list", "layout"}
    assert "dry run" in r.stdout and "--ratio 0.3333" in r.stdout


def test_partial_or_split_views_are_skipped(tmp_path) -> None:
    """Six labels, or the seven seats across two tabs: skipped; a full view is still arranged."""
    six = workspace("w1", labels=SEATS[:-1])
    split = workspace("w2", tabs={"spec.resolve": "w2-t2"})
    env = setup(tmp_path, {"w1": six, "w2": split})
    r = run(env, "--rig", RIG)
    assert r.returncode == 0, r.stderr
    assert moves(tmp_path) == []
    assert "no Herdr view of the team found" in r.stdout


def test_only_the_full_view_is_arranged(tmp_path) -> None:
    """Of two workspaces, only the one with all seven seats is moved."""
    env = setup(tmp_path, {"w1": workspace("w1", labels=SEATS[:-1]), "w3": workspace("w3")})
    r = run(env, "--rig", RIG)
    assert r.returncode == 0, r.stderr
    assert {c[2].split("-", 1)[0] for c in moves(tmp_path)} == {"w3"}


def test_non_standard_rig_is_not_touched(tmp_path) -> None:
    """A rig missing desk.scribe: exit 0 naming it, and no herdr call."""
    env = setup(
        tmp_path,
        {"w1": workspace("w1")},
        rig_nodes=nodes([s for s in SEATS if s != "desk.scribe"]),
    )
    r = run(env, "--rig", RIG)
    assert r.returncode == 0
    assert f"{RIG}: not a standard HACF team: missing desk.scribe" in r.stdout
    assert herdr_calls(tmp_path) == []


def test_other_rigs_nodes_do_not_count(tmp_path) -> None:
    """Seats reported for another rig name are not this rig's seats."""
    env = setup(tmp_path, {"w1": workspace("w1")}, rig_nodes=nodes(rig="other-rig"))
    r = run(env, "--rig", RIG)
    assert r.returncode == 0 and "not a standard HACF team" in r.stdout
    assert herdr_calls(tmp_path) == []


@pytest.mark.parametrize("rig_exit", [1, None], ids=["exit-1", "bad-json"])
def test_unreadable_rig_ps_exits_1(tmp_path, rig_exit) -> None:
    """`rig ps` failing or printing no JSON: exit 1, no herdr call."""
    env = setup(tmp_path, {"w1": workspace("w1")}, rig_exit=rig_exit or 0)
    if rig_exit is None:
        (tmp_path / "rig.json").write_text("{not json")
    r = run(env, "--rig", RIG)
    assert r.returncode == 1
    assert "unreadable; nothing moved" in r.stderr
    assert herdr_calls(tmp_path) == []


def test_failed_move_recovers_panes(tmp_path) -> None:
    """The third move fails: the two panes already out come back beside orch.lead; exit 1."""
    env = setup(tmp_path, {"w1": workspace("w1")}, fail_move=3)
    r = run(env, "--rig", RIG)
    assert r.returncode == 1
    recover = [
        "pane", "move", None, "--tab", "w1-t1", "--target-pane", p("orch.lead"),
        "--split", "down", "--no-focus",
    ]  # fmt: skip
    want = [out_tab("build.execute"), out_tab("build.audit"), out_tab("desk.scribe")] + [
        [p(s) if x is None else x for x in recover] for s in ("build.execute", "build.audit")
    ]
    assert moves(tmp_path) == want
    assert "label-w1 (w1)" in r.stderr and "FAILED" in r.stderr


def test_failed_return_move_recovers_the_rest(tmp_path) -> None:
    """A failed return move: every pane still away (five) is brought back."""
    env = setup(tmp_path, {"w1": workspace("w1")}, fail_move=8)
    r = run(env, "--rig", RIG)
    assert r.returncode == 1
    recovered = [c[2] for c in moves(tmp_path)[8:]]
    assert recovered == [p(s) for s in SEATS[2:]]
    assert all("--split" in c and c[c.index("--split") + 1] == "down" for c in moves(tmp_path)[8:])


def test_no_herdr_exits_0(tmp_path) -> None:
    """No herdr binary: exit 0 with one line."""
    env = setup(tmp_path, {}, herdr=False)
    r = run(env, "--rig", RIG)
    assert r.returncode == 0
    assert r.stdout.strip() == "herdr: not installed; nothing to arrange"


def test_tmux_is_herdr_only(tmp_path) -> None:
    """MR 4: OpenRig gives each seat its own tmux session, so tmux is documented as Herdr-only."""
    env = setup(tmp_path, {"w1": workspace("w1")})
    r = run(env, "--rig", RIG, "--provider", "tmux")
    assert r.returncode == 0
    assert r.stdout.strip() == (
        "tmux: not supported; the recommended view is Herdr-only (see docs/OpenRig-Team.md)"
    )
    assert herdr_calls(tmp_path) == [] and not (tmp_path / "rig.log").exists()


@pytest.mark.parametrize(
    "args", [[], ["--rig"], ["--rig", RIG, "--provider", "cmux"], ["--rig", ""]]
)
def test_usage_exits_2(tmp_path, args) -> None:
    """Bad arguments: exit 2, no herdr call."""
    env = setup(tmp_path, {"w1": workspace("w1")})
    assert run(env, *args).returncode == 2
    assert herdr_calls(tmp_path) == []


def test_script_has_no_host_literals() -> None:
    """No host path, nvm path or PATH override in the script."""
    text = SCRIPT.read_text(encoding="utf-8")
    for bad in ("/" + "home/", ".nvm", "PATH="):
        assert bad not in text
