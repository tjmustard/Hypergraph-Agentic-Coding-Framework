"""Fake `rig`, `tmux`, `claude` and `dev/test_rig.py` for the advisor kit's tests.

One script plays all four, chosen by the name it is run as. Its whole state is `state.json` in
HACF_FAKE_DIR; every call is appended to `calls.jsonl` there. Nothing here reaches a real rig.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ["HACF_FAKE_DIR"])
RIG_NAME = "hacf-test-proj-20261127T000000"
MODELS = {
    "orch.lead": "claude-opus-5-5",
    "build.execute": "claude-sonnet-5-5",
    "build.audit": "claude-opus-5-5",
}


def load() -> dict:
    """Return the fake world's state."""
    return json.loads((ROOT / "state.json").read_text(encoding="utf-8"))


def save(state: dict) -> None:
    """Write the fake world's state."""
    (ROOT / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def log(tool: str, argv: list[str], stdin: str = "") -> None:
    """Record one call."""
    with (ROOT / "calls.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"tool": tool, "argv": argv, "stdin": stdin}) + "\n")


def now() -> str:
    """Return the current UTC time."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record(model: str, advisor_call: bool = False, field: bool = True, uid: str = "m") -> dict:
    """One assistant transcript record, with the misleading `advisorModel` field by default."""
    usage = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 1000,
        "output_tokens": 50,
    }
    if advisor_call:
        usage["iterations"] = [
            {"type": "message", "input_tokens": 100, "output_tokens": 50},
            {
                "type": "advisor_message",
                "model": "claude-opus-5-5",
                "input_tokens": 2000,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        ]
    entry = {
        "type": "assistant",
        "timestamp": now(),
        "requestId": f"req-{uid}",
        "message": {"id": uid, "model": model, "usage": usage, "content": []},
    }
    if field:
        entry["advisorModel"] = "claude-opus-5-5"
    return entry


def transcript(session: str) -> Path:
    """Return a session's transcript path under the fake HOME."""
    return Path(os.environ["HOME"]) / ".claude" / "projects" / "fake" / f"{session}.jsonl"


def append(session: str, entry: dict) -> None:
    """Append one record to a transcript."""
    path = transcript(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")


def process(state: dict, seat: str, cwd: str, argv: list[str]) -> None:
    """Give a seat a pane pid and a runtime process in the fake /proc."""
    state["pid"] += 10
    pane, child = state["pid"], state["pid"] + 1
    proc = ROOT / "proc"
    for pid, parent, words in ((pane, 1, ["bash"]), (child, pane, argv)):
        entry = proc / str(pid)
        entry.mkdir(parents=True)
        (entry / "stat").write_text(f"{pid} (x) S {parent} 0 0\n", encoding="utf-8")
        (entry / "cmdline").write_bytes(b"\0".join(w.encode() for w in words) + b"\0")
        (entry / "cwd").symlink_to(cwd)
    state["seats"][seat]["pane"] = pane


def start_seat(state: dict, seat: str, cwd: str) -> None:
    """Launch a fresh occupant: a new session, a process and a first turn."""
    state["sessions"] += 1
    session = f"sess-{state['sessions']}"
    state["seats"].setdefault(seat, {})["session"] = session
    argv = ["/opt/bin/claude", "--settings", '{"permissions":{}}']
    argv += state.get("argv", {}).get(seat, [])
    process(state, seat, state.get("proc_cwd", {}).get(seat, cwd), argv)
    model = state.get("models", {}).get(seat, MODELS[seat])
    called = state.get("advisor_calls", {}).get(seat, False)
    append(session, record(model, advisor_call=called, uid=f"{session}-1"))


def name_of(session_name: str) -> str:
    """`build-execute@x` -> `build.execute`."""
    return session_name.split("@")[0].replace("-", ".", 1)


def step(state: dict) -> None:
    """Apply the next scripted step, once per poll of the lead's cost."""
    if not state.get("working") or not state["script"]:
        return
    action = state["script"].pop(0)
    copy = Path(state["copy"])
    for slice_dir, status in action.get("status", []):
        slice_path = copy / "missions" / "advisor-workload" / "slices" / slice_dir
        spec = slice_path / "SPEC.md"
        text = re.sub(r"(?m)^hacf_status: \S+", f"hacf_status: {status}", spec.read_text())
        spec.write_text(text)
        if status == "audit_failed":
            with (slice_path / "PROGRESS.md").open("a") as out:
                out.write("- 2026-11-27T00:00:00+00:00 audit_failed - one\n")
    for seat, value in action.get("cost", {}).items():
        state["cost"][seat] = value
    for key in ("weekly", "five_hour"):
        if key in action:
            state[key] = action[key]
    if "settings" in action:
        path = Path(os.environ["HOME"]) / ".claude" / "settings.json"
        path.write_text(action["settings"])
    if "report" in action:
        (copy / "replay-audit-report.md").write_text(action["report"].replace("{copy}", str(copy)))
    for seat, entry in action.get("turn", []):
        session = state["seats"][seat]["session"]
        state["sessions"] += 1
        uid = f"{session}-{state['sessions']}"
        made = record(MODELS[seat], entry.get("advisor", False), uid=uid)
        made["message"]["content"] = entry.get("content", [])
        append(session, made)
    for seat, new in action.get("replaced", []):  # /clear or a relaunch: a new session
        state["sessions"] += 1
        session = f"sess-{state['sessions']}"
        state["seats"][seat]["session"] = session
        made = record(new.get("model", MODELS[seat]), new.get("advisor", False), uid=f"{session}-1")
        append(session, made)
    for seat, kind, model in action.get("subagent", []):
        session = state["seats"][seat]["session"]
        folder = transcript(session).parent / session / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"agentType": kind, "model": model}
        (folder / "agent-1.meta.json").write_text(json.dumps(meta))
        (folder / "agent-1.jsonl").write_text(json.dumps(record(model, uid="sub-1")) + "\n")
    if action.get("commit"):
        subprocess.run(
            [
                "git",
                "-C",
                str(copy),
                "-c",
                "user.name=fake",
                "-c",
                "user.email=fake@example.invalid",
            ]
            + ["commit", "-q", "--allow-empty", "-m", "scripted task"],
            check=True,
        )
    if action.get("needs_input"):
        state["needs_input"] = action["needs_input"]


def seat_arg(argv: list[str]) -> str:
    """The value after --seat."""
    return argv[argv.index("--seat") + 1]


def fake_rig(argv: list[str]) -> int:
    """The `rig` verbs the runner uses."""
    stdin = sys.stdin.read() if "-" in argv else ""
    log("rig", argv, stdin)
    state = load()
    verb = argv[:2]
    out: object = {}
    if verb == ["provider", "signals"]:
        reset = state["resets_at"]
        out = {
            "signals": [
                {
                    "provider": "claude",
                    "window": window,
                    "usedPercent": state[window],
                    "resetsAt": reset[window],
                    "asOf": now(),
                }
                for window in ("weekly", "five_hour")
                if state.get(window) is not None
            ]
        }
    elif verb == ["usage", "series"]:
        out = {"rows": state["series"]}
    elif verb == ["usage", "cost"]:
        seat = name_of(seat_arg(argv))
        if seat == state["poll_seat"]:
            step(state)
        value = state["cost"].get(seat)
        window = {"nativeSessionId": state["seats"].get(seat, {}).get("session")}
        row = {"seatSession": seat_arg(argv), "window": window}
        if value is not None:
            row["costUsd"] = value
        out = {"seats": [row]}
    elif verb == ["terminal", "status"]:
        out = {"tmux": {"socketPath": str(ROOT / "tmux.sock")}}
    elif argv[0] == "ps":
        needs = state.get("needs_input")
        out = [
            {
                "logicalId": seat,
                "canonicalSessionName": f"{seat.replace('.', '-')}@{RIG_NAME}",
                "resumeToken": info["session"],
                "activityState": {"needsInput": {"count": 1 if seat == needs else 0}},
            }
            for seat, info in state["seats"].items()
        ]
    elif verb == ["seat", "set-cwd"]:
        state["next_cwd"] = argv[argv.index("--cwd") + 1]
    elif verb == ["seat", "launch"]:
        start_seat(state, name_of(argv[2]), state.pop("next_cwd"))
    elif verb == ["queue", "create"]:
        destination = argv[argv.index("--destination") + 1]
        if destination.endswith(RIG_NAME):
            state["working"] = True
            state["poll_seat"] = state.get("poll_seat") or "orch.lead"
    else:
        save(state)
        return 2
    save(state)
    print(json.dumps(out))
    return 0


def fake_tmux(argv: list[str]) -> int:
    """`tmux -S <socket> list-panes -s -t =<session> -F ...`."""
    log("tmux", argv)
    target = argv[argv.index("-t") + 1].lstrip("=").rstrip(":")
    print(load()["seats"][name_of(target)]["pane"])
    return 0


def fake_claude(argv: list[str]) -> int:
    """The probe's one `claude -p` call."""
    log("claude", argv)
    state = load()
    session = "probe-session"
    called = state.get("probe_called", True)
    append(session, record("claude-sonnet-5-5", advisor_call=called, uid="probe-1"))
    save(state)
    result = state.get("probe_result", "done")
    print(json.dumps({"session_id": session, "total_cost_usd": 0.05, "result": result}))
    return 0


def fake_test_rig(argv: list[str]) -> int:
    """`test_rig.py run --project p --seats s -- cmd`: a real copy, fake seats, no rig."""
    log("test_rig", argv)
    state = load()
    cut = argv.index("--")
    options, command = argv[:cut], argv[cut + 1 :]
    project = Path(options[options.index("--project") + 1])
    seats = options[options.index("--seats") + 1].split(",")
    state["sessions"] += 1
    copy = Path(os.environ["TMPDIR"]) / f"hacf-test-rig-{state['sessions']}"
    shutil.copytree(project, copy, symlinks=True)
    state.update(copy=str(copy), project=str(project), project_hash=tree_hash(project))
    state["poll_seat"] = seats[0]
    for seat in seats:
        start_seat(state, seat, str(copy.resolve()))
    save(state)
    env = dict(os.environ, HACF_TEST_RIG=RIG_NAME, HACF_TEST_RIG_COPY=str(copy.resolve()))
    done = subprocess.run(command, env=env)
    state = load()
    state["project_hash_after"] = tree_hash(project)
    save(state)
    return done.returncode


def tree_hash(root: Path) -> str:
    """A sha256 over every file's path and bytes."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def main() -> int:
    """Dispatch on the name this script is run as."""
    name = Path(sys.argv[0]).name
    handler = {"rig": fake_rig, "tmux": fake_tmux, "claude": fake_claude}.get(name, fake_test_rig)
    return handler(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
