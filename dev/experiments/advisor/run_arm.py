#!/usr/bin/env python3
"""Run one arm of the advisor + Haiku-helper experiment on a throwaway rig. A dev tool, not shipped.

    run_arm.py gate --arm <0|1|2|3> [--cap-usd 90] [--json]   may an arm start now? (R214)
    run_arm.py probe cli | mechanism                          slice 13's two probes (R215)
    run_arm.py run --arm <0|1|2|3> --run <1|2> [--cap-usd 90] one arm run
    run_arm.py replay --arm <0|2> --run <k> [--cap-usd 90]    one audit-only replay (R211, R217)
    run_arm.py score                                          score the replays' audit reports
    run_arm.py report --out <file.md>                         the tables of slice 13's report

Exit: 0 done; 75 the gate says wait; 2 usage or a refusal; 3 cleanup or verification failed
(such a run is void); 4 the settings guard tripped.

Slice 13 (the lead) runs it, never a build seat: probes, arms and replays cost real money.
Arms 1 and 3 need a passed `probe mechanism`. The runner types no slash command into any seat; the
advisor is set only by a settings file in the test rig's copy, and a settings guard restores
~/.claude's settings files and halts the experiment if either changes (R210).

Writes: evidence under $HOME/.hacf/hacf/evidence/advisor-v068/, scratch under $TMPDIR, the copy
dev/test_rig.py hands it, and, only when the guard trips, the changed settings file. The sealed dir
is read by `replay` and `score` alone, and no seat is given its path.

Standard library only (Python 3.10+). The host config and the at-rest set are read through
openrig/hacf/bin/hacf-host.py, loaded by path, which uses PyYAML.

Test seams (never set them for a real run): HACF_ADVISOR_MAIN, HACF_ADVISOR_TEST_RIG,
HACF_ADVISOR_PROC, HACF_ADVISOR_POLL_S, HACF_ADVISOR_WAIT_S, HACF_ADVISOR_WALL_S,
HACF_ADVISOR_SEALED_SHA.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True

SELF = Path(__file__).resolve()
KIT = SELF.parent
CHECKOUT = SELF.parents[3]
sys.path.insert(0, str(KIT))

import tool_paths  # noqa: E402

EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_GUARD, EXIT_WAIT = 0, 2, 3, 4, 75
MISSION = "advisor-workload"
SLICES = ("01-small", "02-medium")
SLICE_IDS = {"01-small": "OPR.99.0.1.1", "02-medium": "OPR.99.0.1.2"}
ADVISOR_MODEL = "claude-opus-5-5"
ADVISOR_ARMS = (1, 3)
SCOUT_ARMS = (2, 3)
SCOUT = "hacf-scout"
SEATS = ("orch.lead", "build.execute", "build.audit")
MODELS = {  # R218, the arm table
    "orch.lead": "claude-opus-5-5",
    "build.execute": "claude-sonnet-5-5",
    "build.audit": "claude-opus-5-5",
}
UPPER_USD = {0: 8.70, 1: 13.70, 2: 9.00, 3: 14.00}  # R213
REPLAY_UPPER_USD = 1.80
USD_PER_PERCENT = 1.70  # R214: the lowest $ per weekly % seen
MARGIN = 1.15
CAP_USD = 90.0
FIVE_HOUR_GATE, FIVE_HOUR_STOP, WEEKLY_STOP = 50.0, 90.0, 95.0
RATE_MIN_SPAN_H, DROP_POINTS = 6.0, 5.0
SIGNAL_MAX_AGE_H = 6.0
SERIES_LIMIT = 100000
AUDIT_ROUNDS = 3
WALL_S, REPLAY_WALL_S, PROBE_WALL_S = 4 * 3600, 90 * 60, 30 * 60
ARM_GAP_S = 3600  # R223
CATCH_SLACK = 5
LEAD_ADDRESS = "orch-lead@hacf"
EXCLUDED_SLICE = Path("missions/v068-host-ops/slices/13-advisor-run")
SEALED_FILES = ("reference.patch", "defects.patch", "key.yml", "check_defects.py")
GUARDED = ("settings.json", "settings.local.json")
REPORT_NAME = "replay-audit-report.md"
CREDITS_RE = re.compile(r"usage[ _-]?credits|awaits_usage_credits|extra usage|consent", re.I)
CITATION_RE = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?")
STATUS_RE = re.compile(r"(?m)^hacf_status:[ \t]*(\S+)[ \t]*$")


class Stop(Exception):
    """End the command with an exit code and one line saying why."""

    def __init__(self, code: int, why: str) -> None:
        """Hold the exit code and the reason."""
        super().__init__(why)
        self.code = code


def note(text: str) -> None:
    """A line of the tool's own, on stderr."""
    print(f"run_arm: {text}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- places and files


def home() -> Path:
    """The user's HOME."""
    return Path.home()


def evidence() -> Path:
    """The evidence dir."""
    return home() / ".hacf" / "hacf" / "evidence" / "advisor-v068"


def sealed_dir() -> Path:
    """The sealed material's dir, outside every tree."""
    return home() / ".hacf" / "hacf" / "experiments" / "advisor-v068" / "sealed"


def seam(name: str, default: str) -> str:
    """A test seam's value, else the default."""
    return os.environ.get(f"HACF_ADVISOR_{name}") or default


def poll_s() -> float:
    """Seconds between two polls of a running rig."""
    return float(seam("POLL_S", "60"))


def wait_s(default: float = 600) -> float:
    """Seconds to wait for a seat to launch, take its first turn or finish a probe."""
    return float(seam("WAIT_S", str(default)))


def now() -> datetime:
    """The current UTC time."""
    return datetime.now(timezone.utc)


def iso(when: datetime) -> str:
    """An ISO-8601 UTC stamp."""
    return when.isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    """An aware datetime from an ISO stamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def private_dir(path: Path) -> Path:
    """Make a 0700 directory."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Replace a file in one step, mode 0600."""
    private_dir(path.parent)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "wb") as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(data)
    os.replace(temporary, path)


def write_json(path: Path, data: object) -> None:
    """Write JSON privately."""
    write_private(path, (json.dumps(data, indent=2) + "\n").encode())


def read_json(path: Path) -> object | None:
    """Parsed JSON, or None when the file is missing, unreadable or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- rig


def rig(*argv: str, stdin: str | None = None, timeout: float = 120) -> tuple[int, str]:
    """Run `rig <argv>` from PATH: (exit code, stdout); 127 not run, 124 timed out."""
    try:
        done = subprocess.run(
            ["rig", *argv],
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except OSError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout


def rig_json(*argv: str) -> object | None:
    """A `rig ... --json` answer, or None."""
    code, out = rig(*argv, "--json")
    if code != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def send_row(destination: str, summary: str, body: str, *extra: str) -> bool:
    """Create one queue row; the body goes by stdin."""
    argv = ["queue", "create", "--destination", destination, "--summary", summary, *extra]
    code, _out = rig(*argv, "--body-file", "-", stdin=body)
    return code == 0


# --------------------------------------------------------------------------- usage windows


def windows() -> dict[str, dict | None]:
    """The weekly and five_hour readings: {used, resets_at}, None when unknown.

    Of the signals of the current window (the latest future reset), the highest use counts; a
    reading older than SIGNAL_MAX_AGE_H is unknown.
    """
    answer = rig_json("provider", "signals")
    signals = answer.get("signals") if isinstance(answer, dict) else None
    out: dict[str, dict | None] = dict.fromkeys(("weekly", "five_hour"))
    for window in out:
        found = []
        for item in signals if isinstance(signals, list) else []:
            if not isinstance(item, dict) or item.get("provider") != "claude":
                continue
            used, resets = item.get("usedPercent"), parse_time(item.get("resetsAt"))
            seen = parse_time(item.get("asOf"))
            if item.get("window") != window or not isinstance(used, (int, float)):
                continue
            if resets is not None and seen is not None and resets > now():
                found.append((resets, float(used), seen))
        if not found:
            continue
        latest = max(resets for resets, _used, _seen in found)
        current = [(used, seen) for resets, used, seen in found if resets == latest]
        if now() - max(seen for _used, seen in current) <= timedelta(hours=SIGNAL_MAX_AGE_H):
            out[window] = {"used": max(used for used, _seen in current), "resets_at": iso(latest)}
    return out


def weekly_rate(resets_at: str) -> tuple[float | None, str]:
    """r24, the weekly % gained per hour over the last 24 h, or None with the reason.

    Only samples of the current window count, and after a drop of DROP_POINTS or more only those
    since it. The gain is divided by the hours the samples span; under 6 h is unknown.
    """
    since = iso(now() - timedelta(hours=24))
    answer = rig_json(
        "usage", "series", "--lane", "provider_window", "--since", since,
        "--limit", str(SERIES_LIMIT),
    )  # fmt: skip
    rows = answer.get("rows") if isinstance(answer, dict) else None
    if not isinstance(rows, list) or len(rows) >= SERIES_LIMIT:
        return None, "no complete series"
    wanted = parse_time(resets_at)
    samples = []
    for row in rows:
        if not isinstance(row, dict) or row.get("window") != "weekly":
            continue
        when, used = parse_time(row.get("sampledAt")), row.get("windowUsedPercent")
        if when and isinstance(used, (int, float)) and parse_time(row.get("resetsAt")) == wanted:
            samples.append((when, float(used)))
    samples.sort()
    segment: list[tuple[datetime, float]] = []
    for when, used in samples:
        if segment and used <= max(u for _w, u in segment) - DROP_POINTS:
            segment = []
        segment.append((when, used))
    if not segment:
        return None, "no weekly sample"
    span = (segment[-1][0] - segment[0][0]).total_seconds() / 3600
    if span < RATE_MIN_SPAN_H:
        return None, f"samples span {span:.1f} h, under 6 h"
    gained = max(used for _w, used in segment) - min(used for _w, used in segment)
    return gained / min(span, 24.0), ""


# --------------------------------------------------------------------------- spend


def spend_runs() -> list[dict] | None:
    """The recorded runs; [] before the first, None when the ledger cannot be read."""
    path = evidence() / "spend.json"
    if not path.exists():
        return []
    data = read_json(path)
    runs = data.get("runs") if isinstance(data, dict) else None
    return runs if isinstance(runs, list) and all(isinstance(r, dict) for r in runs) else None


def total_usd(runs: Sequence[dict] | None) -> float | None:
    """The measured total, or None when any run's cost is unknown (never 0)."""
    if runs is None or any(not isinstance(r.get("usd"), (int, float)) for r in runs):
        return None
    return round(sum(r["usd"] for r in runs), 6)


def record_spend(entry: dict) -> None:
    """Append one run to spend.json with the new running total.

    Raises:
        Stop: When the existing ledger cannot be read; it is never overwritten.
    """
    runs = spend_runs()
    if runs is None:
        raise Stop(EXIT_FAILED, f"spend.json is unreadable; not recorded: {json.dumps(entry)}")
    runs.append(entry)
    write_json(evidence() / "spend.json", {"runs": runs, "total_usd": total_usd(runs)})


def estimate_usd(arm: int, kind: str, runs: Sequence[dict]) -> float:
    """The upper estimate: R213's, or the highest measured cost of this arm when that is higher."""
    table = REPLAY_UPPER_USD if kind == "replay" else UPPER_USD[arm]
    measured = [
        r["usd"]
        for r in runs
        if r.get("kind") == kind
        and (kind == "replay" or r.get("arm") == arm)
        and isinstance(r.get("usd"), (int, float))
    ]
    return max([table, *measured])


# --------------------------------------------------------------------------- the gate (R214)


def host_tool() -> types.ModuleType:
    """hacf-host.py of this checkout, loaded by path with no bytecode written."""
    path = CHECKOUT / "openrig" / "hacf" / "bin" / "hacf-host.py"
    spec = importlib.util.spec_from_file_location("hacf_host", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def in_flight() -> tuple[list[str], list[str]]:
    """Slices in flight and unknowns, over the HACF checkout and every mapped project root."""
    tool = host_tool()
    config, problems = tool.load_config()
    busy: list[str] = []
    unknown = [f"unknown: {problem}" for problem in problems]
    projects = [Path(root) for root in (config.get("projects") or {}).values()]
    if not projects:
        unknown.append("unknown: host.yml names no mapped project")
    main = Path(seam("MAIN", str(CHECKOUT))).resolve()
    for root in dict.fromkeys([main, *(p.resolve() for p in projects)]):
        try:
            specs = sorted((root / "missions").glob("*/slices/*/SPEC.md"))
        except OSError:
            specs = []
        if not specs:
            unknown.append(f"unknown: no slice SPEC.md read under {root}")
        for spec in specs:
            if spec.parent == main / EXCLUDED_SLICE:
                continue
            status = slice_status(spec)
            if status not in tool.AT_REST:
                busy.append(f"{spec.parent.relative_to(root)} in {root.name}: {status}")
    return busy, unknown


def slice_status(spec: Path) -> str:
    """A slice's hacf_status from its frontmatter, else `unreadable`."""
    try:
        text = spec.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return "unreadable"
    parts = text.split("---\n", 2)
    found = STATUS_RE.search(parts[1]) if len(parts) == 3 and not parts[0] else None
    return found.group(1) if found else "unreadable"


def gate(arm: int, kind: str = "arm", cap: float = CAP_USD) -> dict:
    """Return the gate's reading; `go` only when no reason to wait was found."""
    reasons: list[str] = []
    runs = spend_runs()
    estimate = estimate_usd(arm, kind, runs or [])
    reading: dict = {"arm": arm, "kind": kind, "at": iso(now()), "estimate_usd": estimate}

    busy, unknown = in_flight()
    reading["in_flight"] = busy
    reasons += [f"slice in flight: {item}" for item in busy] + unknown

    seen = windows()
    reading["windows"] = seen
    measured = [r.get("usd_per_percent") for r in runs or []]
    per_percent = min([USD_PER_PERCENT, *(v for v in measured if isinstance(v, float) and v > 0)])
    need = estimate / per_percent * MARGIN
    reading.update(usd_per_percent=per_percent, need_percent=round(need, 3))
    if seen["weekly"] is None:
        reasons.append("weekly: unknown")
    else:
        used, resets = seen["weekly"]["used"], parse_time(seen["weekly"]["resets_at"])
        hours = (resets - now()).total_seconds() / 3600
        rate, why = weekly_rate(seen["weekly"]["resets_at"])
        reading.update(hours_to_reset=round(hours, 2), rate_per_hour=rate)
        if rate is None:
            reasons.append(f"weekly rate: unknown ({why})")
        else:
            headroom = 100 - used - rate * hours
            reading["headroom_percent"] = round(headroom, 3)
            if headroom < need:
                reasons.append(f"headroom {headroom:.2f} % is short of the {need:.2f} % needed")
    if seen["five_hour"] is None:
        reasons.append("five_hour: unknown")
    elif seen["five_hour"]["used"] > FIVE_HOUR_GATE:
        reasons.append(f"five_hour is {seen['five_hour']['used']:g} %, over {FIVE_HOUR_GATE:g} %")

    spent = total_usd(runs)
    reading.update(spent_usd=spent, cap_usd=cap)
    if spent is None:
        reasons.append("cap: unknown (a recorded run has no cost; see spend.json)")
    elif spent + estimate * MARGIN > cap:
        reasons.append(f"cap: ${spent:.2f} + ${estimate * MARGIN:.2f} is over ${cap:g}")
    if (evidence() / "halted.json").exists():
        reasons.append("halted: halted.json is present; the lead removes it")
    reading.update(go=not reasons, reasons=reasons)
    return reading


# --------------------------------------------------------------------------- the guard (MR 6)


def file_hash(path: Path) -> str:
    """sha256 of a file, `absent`, or `unreadable`."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"


class Guard:
    """The user's two settings files: copied before a run, re-hashed during it, restored."""

    def __init__(self, user_home: Path, folder: Path) -> None:
        """Watch `<home>/.claude`'s settings; keep the copies in `folder`."""
        self.live = user_home / ".claude"
        self.folder = folder

    def copies(self) -> list[Path]:
        """The before-copies that exist."""
        return [self.folder / name for name in GUARDED if (self.folder / name).exists()]

    def before(self) -> None:
        """Hash and copy the files.

        Raises:
            Stop: When one cannot be read.
        """
        private_dir(self.folder)
        record = {}
        for name in GUARDED:
            digest = file_hash(self.live / name)
            if digest == "unreadable":
                raise Stop(EXIT_FAILED, f"the guard cannot read ~/.claude/{name}")
            mode = None
            if digest != "absent":
                data = (self.live / name).read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                mode = (self.live / name).stat().st_mode & 0o7777
                write_private(self.folder / name, data)
            record[name] = {"sha256": digest, "mode": mode}
        write_json(self.folder / "before.json", record)

    def changed(self) -> list[dict]:
        """Every file whose hash differs from before; an unreadable record counts as changed."""
        record = read_json(self.folder / "before.json")
        found = []
        for name in GUARDED:
            was = record[name]["sha256"] if isinstance(record, dict) else "unrecorded"
            is_now = file_hash(self.live / name)
            if is_now != was:
                found.append({"file": name, "before": was, "after": is_now})
        return found

    def restore(self, changed: Sequence[dict]) -> bool:
        """Put each changed file back byte for byte (or remove one that was absent); verify."""
        record = read_json(self.folder / "before.json")
        good = isinstance(record, dict)
        for item in changed if good else []:
            name = item["file"]
            live = self.live / name
            try:
                if record[name]["sha256"] == "absent":
                    live.unlink(missing_ok=True)
                else:
                    target = live.resolve()
                    temporary = target.with_name(f".{target.name}.restore")
                    temporary.write_bytes((self.folder / name).read_bytes())
                    os.chmod(temporary, record[name]["mode"])
                    os.replace(temporary, target)
            except OSError as error:
                note(f"could not restore ~/.claude/{name}: {error.strerror}")
            good = file_hash(live) == record[name]["sha256"] and good
        return good

    def check(self) -> None:
        """Do nothing when unchanged; else restore, record, tell the lead and halt.

        Raises:
            Stop: Exit 4, after the trip.
        """
        changed = self.changed()
        tripped = self.folder / "guard-tripped.json"
        if tripped.exists():
            raise Stop(EXIT_GUARD, "the settings guard tripped")
        if not changed:
            return
        restored = self.restore(changed)
        event = {"at": iso(now()), "changed": changed, "restored": restored}
        write_json(tripped, event)
        write_json(evidence() / "halted.json", {**event, "run": self.folder.name})
        lines = [f"~/.claude/{c['file']}: before {c['before']} after {c['after']}" for c in changed]
        body = (
            "The experiment's settings guard tripped; the run is stopped and the experiment is "
            "halted (halted.json in the evidence dir) until you remove it.\n\n"
            + "\n".join(lines)
            + f"\n\nRestored byte for byte and re-hashed: {'yes' if restored else 'NO'}."
            + f"\nRun: {self.folder.name}; guard-tripped.json is in its guard dir.\n"
        )
        row = send_row(
            LEAD_ADDRESS,
            "experiment halted: a user settings file changed",
            body,
            "--tags",
            "advisor-experiment,guard",
        )
        if not row:
            note("the queue row to the lead was NOT created")
        raise Stop(EXIT_GUARD, f"the settings guard tripped (restored: {restored})")


# --------------------------------------------------------------------------- the workload (MR 5.3)


def clean_env(scratch: Path, **extra: str) -> dict[str, str]:
    """An environment for git and the installer: no outer git or HACF state, a fixed identity."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "HACF_", "UV_", "OPENRIG_"))
        and key not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="HACF Experiment",
        GIT_AUTHOR_EMAIL="experiment@example.invalid",
        GIT_COMMITTER_NAME="HACF Experiment",
        GIT_COMMITTER_EMAIL="experiment@example.invalid",
    )
    env.update(extra)
    return env


def must(argv: Sequence[str], cwd: Path, env: dict[str, str]) -> str:
    """Run a preparation command.

    Raises:
        Stop: Exit 3 on a non-zero exit.
    """
    done = subprocess.run(
        list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise Stop(
            EXIT_FAILED, f"{' '.join(argv[:3])} failed: {(done.stderr or done.stdout)[-800:]}"
        )
    return done.stdout


def commit_all(project: Path, env: dict[str, str], message: str) -> None:
    """Commit everything in the scratch project; the installed hooks run."""
    must(["git", "add", "-A"], project, env)
    must(["git", "commit", "-q", "-m", message], project, env)


def prepare_project(scratch: Path, arm: int) -> Path:
    """Make `<scratch>/proj`: the workload, the installed kit and the arm's copy edits."""
    project = scratch / "proj"
    ignored = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache")
    shutil.copytree(KIT / "workload" / "seed", project, ignore=ignored)
    shutil.copytree(KIT / "workload" / "missions", project / "missions", ignore=ignored)
    env = clean_env(scratch)
    must(["git", "init", "-q", "-b", "main"], project, env)
    commit_all(project, env, "workload: seed and missions")
    installer_home = private_dir(scratch / "install-home")
    must(
        ["bash", str(CHECKOUT / "HACF-install.sh"), f"--source={CHECKOUT}", "--rig", "-y",
         "--ides=claude"],
        project,
        clean_env(scratch, HOME=str(installer_home)),
    )  # fmt: skip
    commit_all(project, env, "workload: install the kit")
    arm_edits(project, scratch, arm)
    commit_all(project, env, "workload: arm settings")
    return project


def arm_edits(project: Path, scratch: Path, arm: int) -> None:
    """The arm's edits of the copy (MR 5.4; R225)."""
    spec = project / "openrig" / "hacf" / "rig.yaml"
    private = [
        f"  HACF_HEAVY_LOCK_PATH: {json.dumps(str(scratch / 'arm-heavy.lock'))}",
        f"  HACF_HOST_STATE_DIR: {json.dumps(str(scratch / 'arm-host-state'))}",
    ]
    lines = spec.read_text(encoding="utf-8").splitlines()
    if "env:" in lines:
        at = lines.index("env:") + 1
        lines[at:at] = private
    else:
        lines += ["env:", *private]
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exclude = project / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as out:
        out.write(".hacf-exec/\n")
    if arm in SCOUT_ARMS:
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT / f"{SCOUT}.md", agents / f"{SCOUT}.md")
        guidance = (KIT / "scout-guidance.md").read_text(encoding="utf-8")
        for role in ("lead", "audit"):
            path = project / "openrig" / "hacf" / "agents" / "hacf" / "guidance" / f"{role}.md"
            text = path.read_text(encoding="utf-8")
            path.write_text(f"{text.rstrip()}\n\n{guidance}", encoding="utf-8")


def execute_folder(copy: Path, arm: int) -> Path:
    """Make `COPY/.hacf-exec/`, the execute seat's own folder (R210, P92)."""
    folder = copy / ".hacf-exec"
    shutil.copytree(
        copy / ".claude",
        folder / ".claude",
        ignore=shutil.ignore_patterns(f"{SCOUT}.md"),
        symlinks=True,
    )
    local = folder / ".claude" / "settings.local.json"
    settings = read_json(local) if local.exists() else {}
    if not isinstance(settings, dict):
        raise Stop(EXIT_FAILED, "the copy's settings.local.json is not a JSON object")
    settings.pop("advisorModel", None)
    permissions = settings.get("permissions")
    settings["permissions"] = {
        **(permissions if isinstance(permissions, dict) else {}),
        "additionalDirectories": [str(copy)],
    }
    if arm in ADVISOR_ARMS:
        settings["advisorModel"] = ADVISOR_MODEL
    local.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- transcripts, advisor


def transcript_files(session: str) -> tuple[Path | None, list[Path]]:
    """A session's main transcript (None unless exactly one) and its subagent transcripts."""
    projects = home() / ".claude" / "projects"
    found = sorted(projects.glob(f"*/{session}.jsonl")) if re.fullmatch(r"[\w-]+", session) else []
    if len(found) != 1:
        return None, []
    return found[0], sorted((found[0].parent / session / "subagents").glob("*.jsonl"))


def assistant_records(path: Path) -> list[dict]:
    """The assistant records of a transcript; unparsable lines are skipped."""
    records = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("type") == "assistant":
                if isinstance(entry.get("message"), dict):
                    records.append(entry)
    return records


def advisor_activity(transcripts: Sequence[Path]) -> tuple[int, int]:
    """(advisor activity, records carrying the `advisorModel` field), per message (P98).

    Activity is `advisor_message` iterations plus advisor tool uses. The field is counted only:
    it is on records of seats that have no advisor, so it never decides anything.
    """
    iterations: dict[object, int] = {}
    tools: set[object] = set()
    field: set[object] = set()
    for number, path in enumerate(transcripts):
        for index, entry in enumerate(assistant_records(path)):
            message = entry["message"]
            key = (message.get("id"), entry.get("requestId"))
            if key == (None, None):
                key = (number, index)
            if "advisorModel" in entry:
                field.add(key)
            usage = message.get("usage")
            steps = usage.get("iterations") if isinstance(usage, dict) else None
            calls = sum(
                1
                for step in (steps if isinstance(steps, list) else [])
                if isinstance(step, dict) and step.get("type") == "advisor_message"
            )
            iterations[key] = max(iterations.get(key, 0), calls)
            content = message.get("content")
            for at, block in enumerate(content if isinstance(content, list) else []):
                if not isinstance(block, dict):
                    continue
                kind, name = block.get("type"), block.get("name")
                if kind in ("tool_use", "server_tool_use") and name == "advisor":
                    tools.add(block.get("id") or (key, at))
    return sum(iterations.values()) + len(tools), len(field)


def _holds_advisor(source: object) -> bool | None:
    """True/False for a settings object's `advisorModel` key; None when it is no object."""
    return "advisorModel" in source if isinstance(source, dict) else None


def advisor_configured(
    argv: Sequence[str], cwd: Path, copy: Path, user_settings: Sequence[Path]
) -> bool | None:
    """The config side of P98: True, False, or None when a source cannot be read.

    The seat's argv (`--advisor`, `--settings` inline or a file), the guard's copies of the user's
    settings, and every .claude settings file from the cwd up to the copy.
    """
    found: list[bool | None] = []
    words = list(argv)
    for at, word in enumerate(words):
        flag, equals, value = word.partition("=")
        if flag == "--advisor":
            found.append(True)
        if flag != "--settings":
            continue
        if not equals:
            value = words[at + 1] if at + 1 < len(words) else ""
        if value.lstrip().startswith("{"):
            try:
                found.append(_holds_advisor(json.loads(value)))
            except ValueError:
                found.append(None)
        else:
            path = Path(value) if os.path.isabs(value) else cwd / value
            found.append(_holds_advisor(read_json(path)))
    files = list(user_settings)
    folders = [cwd, *cwd.parents]
    folders = folders[: folders.index(copy) + 1] if copy in folders else [cwd]
    files += [folder / ".claude" / name for folder in folders for name in GUARDED]
    found += [_holds_advisor(read_json(path)) for path in files if os.path.lexists(path)]
    if True in found:
        return True
    return None if None in found else False


def advisor_state(configured: bool | None, activity: int) -> str:
    """present, absent, advisor-unused, mixed (activity with no config) or unknown."""
    if configured is None:
        return "unknown"
    if configured:
        return "present" if activity else "advisor-unused"
    return "mixed" if activity else "absent"


# --------------------------------------------------------------------------- the test rig's seats


class Rig:
    """The running test rig, as the inner verbs see it."""

    def __init__(self, name: str, copy: Path, seats: Sequence[str]) -> None:
        """Remember the rig's name, its copy and the seats this run uses."""
        self.name, self.copy, self.seats = name, copy, tuple(seats)
        self.session_names: dict[str, str] = {}

    def nodes(self) -> dict[str, dict]:
        """The rig's nodes by logical id; {} when unreadable."""
        rows = rig_json("ps", "--nodes", "--rig", self.name, "--full")
        if not isinstance(rows, list):
            return {}
        return {r["logicalId"]: r for r in rows if isinstance(r, dict) and r.get("logicalId")}

    def find_seats(self) -> None:
        """Learn each seat's session name.

        Raises:
            Stop: When a seat is missing.
        """
        nodes = self.nodes()
        for seat in self.seats:
            name = nodes.get(seat, {}).get("canonicalSessionName")
            if not name:
                raise Stop(EXIT_FAILED, f"void: seat {seat} is not in rig ps")
            self.session_names[seat] = name

    def session(self, seat: str) -> str | None:
        """The seat's native conversation id."""
        token = self.nodes().get(seat, {}).get("resumeToken")
        return token if isinstance(token, str) and token else None

    def runtime(self, seat: str) -> tuple[list[str], Path] | None:
        """(argv, cwd) of the harness process under the seat's tmux pane."""
        status = rig_json("terminal", "status")
        tmux = status.get("tmux") if isinstance(status, dict) else None
        socket = tmux.get("socketPath") if isinstance(tmux, dict) else None
        if not socket:
            return None
        command = ["tmux", "-S", socket, "list-panes", "-s", "-t", f"={self.session_names[seat]}"]
        done = subprocess.run([*command, "-F", "#{pane_pid}"], capture_output=True, text=True)
        panes = [int(word) for word in done.stdout.split() if word.isdigit()]
        if done.returncode != 0 or not panes:
            return None
        proc = Path(seam("PROC", "/proc"))
        children: dict[int, list[int]] = {}
        for stat in proc.glob("[0-9]*/stat"):
            try:
                parent = int(stat.read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(parent, []).append(int(stat.parent.name))
        todo = list(panes)
        while todo:
            pid = todo.pop(0)
            todo += sorted(children.get(pid, []))
            try:
                raw = (proc / str(pid) / "cmdline").read_bytes()
                argv = [w.decode(errors="replace") for w in raw.split(b"\0") if w]
                if argv and (Path(argv[0]).name == "claude" or "/claude/versions/" in argv[0]):
                    return argv, Path(os.readlink(proc / str(pid) / "cwd"))
            except OSError:
                continue
        return None

    def wait_seat(self, seat: str, cwd: Path, not_session: str | None) -> tuple[list[str], str]:
        """Wait for the seat's process in `cwd` and its first turn: (argv, session id).

        Raises:
            Stop: Void, when the cwd stays wrong or no turn appears in time.
        """
        deadline = time.monotonic() + wait_s()
        why = "no process"
        while True:
            runtime = self.runtime(seat)
            if runtime is not None and runtime[1] != cwd:
                why = f"cwd is {runtime[1]}, not {cwd}"
            elif runtime is not None:
                session = self.session(seat)
                main = transcript_files(session)[0] if session else None
                if session != not_session and main and assistant_records(main):
                    return runtime[0], session
                why = "no first turn"
            if time.monotonic() >= deadline:
                raise Stop(EXIT_FAILED, f"void: {seat}: {why}")
            time.sleep(max(poll_s() / 10, 0.05))


class Costs:
    """Each seat's cost per native session, as `rig usage cost` reports it (R216)."""

    def __init__(self, folder: Path) -> None:
        """Append every reading to the run's costs.jsonl."""
        self.log = folder / "costs.jsonl"
        self.rows: dict[tuple[str, str], dict] = {}

    def read(self, the_rig: Rig) -> None:
        """Read every seat once."""
        for seat in the_rig.seats:
            answer = rig_json("usage", "cost", "--seat", the_rig.session_names[seat])
            rows = answer.get("seats") if isinstance(answer, dict) else None
            for row in rows if isinstance(rows, list) else []:
                value = row.get("costUsd") if isinstance(row, dict) else None
                window = row.get("window") if isinstance(value, (int, float)) else None
                session = window.get("nativeSessionId") if isinstance(window, dict) else None
                if not session:
                    continue
                with self.log.open("a", encoding="utf-8") as out:
                    out.write(json.dumps({"at": iso(now()), "seat": seat, "row": row}) + "\n")
                key = (seat, session)
                if key not in self.rows or value >= self.rows[key]["costUsd"]:
                    self.rows[key] = row

    def total(self) -> float:
        """The sum of every session's highest figure: a lower bound while any is missing."""
        return round(sum(row["costUsd"] for row in self.rows.values()), 6)

    def complete(self, sessions: dict[str, list[str]]) -> bool:
        """True when each session that must report has (an initial one, or one with a turn)."""
        for seat, ids in sessions.items():
            for index, session in enumerate(ids):
                main = transcript_files(session)[0]
                turns = index == 0 or main is None or bool(assistant_records(main))
                if turns and (seat, session) not in self.rows:
                    return False
        return True


class Run:
    """The state of one run inside the test rig: the checks and the record."""

    def __init__(self, arm: int, folder: Path, the_rig: Rig) -> None:
        """Start a record in the run's evidence folder."""
        self.arm, self.folder, self.rig = arm, folder, the_rig
        self.guard = Guard(home(), evidence() / "guard" / folder.name)
        self.costs = Costs(folder)
        self.sessions: dict[str, list[str]] = {}  # per checked seat, every session it has had
        self.mixed: list[str] = []
        self.activity: dict[str, int] = {}
        self.started = now()
        self.record: dict = {"status": "unfinished", "void": False, "mixed": self.mixed}

    def presence(self, seat: str) -> bool:
        """True for the one seat that must have the advisor."""
        return seat == "build.execute" and self.arm in ADVISOR_ARMS

    def mark(self, label: str, why: str) -> None:
        """Mark a slice (or the run) mixed."""
        note(f"mixed: {label}: {why}")
        if label not in self.mixed:
            self.mixed.append(label)

    def check_cwd_and_config(self, seat: str, cwd: Path, not_session: str | None = None) -> None:
        """The cwd, the first turn and P98's config side for one seat.

        Raises:
            Stop: Void (exit 3) on a wrong cwd or advisor config.
        """
        argv, session = self.rig.wait_seat(seat, cwd, not_session)
        self.sessions[seat] = [session]
        configured = advisor_configured(argv, cwd, self.rig.copy, self.guard.copies())
        if configured is not self.presence(seat):
            found = {True: "configured", False: "not configured", None: "unreadable"}[configured]
            raise Stop(EXIT_FAILED, f"void: advisor config: {seat} is {found}")

    def follow(self) -> None:
        """Note a checked seat's new session (a clear or a relaunch), so no check misses it."""
        nodes = self.rig.nodes()
        for seat, ids in self.sessions.items():
            token = nodes.get(seat, {}).get("resumeToken")
            if isinstance(token, str) and token and token not in ids:
                ids.append(token)

    def seat_transcripts(self, seat: str, subagents: bool = True) -> list[Path]:
        """The transcripts of every session the seat has had since its check."""
        found = []
        for session in self.sessions[seat]:
            main, subs = transcript_files(session)
            found += ([main] if main else []) + (subs if subagents else [])
        return found

    def check_activity(self, label: str) -> dict[str, tuple[int, int]]:
        """P98's activity side: new activity on an absence seat marks the slice mixed."""
        self.follow()
        seen = {}
        for seat in self.rig.seats:
            absence = not self.presence(seat)
            files = self.seat_transcripts(seat, subagents=absence)
            activity, field = advisor_activity(files)
            if absence and activity > self.activity.get(seat, 0):
                self.mark(label, f"advisor activity on {seat}")
            self.activity[seat] = activity
            seen[seat] = (activity, field)
        return seen

    def check_models_and_helpers(self) -> None:
        """F8 and US-A04: each seat's model, and every scout on Haiku and never under execute."""
        helpers, models = [], {}
        for seat in self.rig.seats:
            served: set[str] = set()
            subagents: list[Path] = []
            for session in self.sessions[seat]:
                main, subs = transcript_files(session)
                if main:
                    served |= {str(r["message"].get("model")) for r in assistant_records(main)}
                subagents += subs
            models[seat] = sorted(served - {"None", "<synthetic>"})
            if models[seat] != [MODELS[seat]]:
                self.mark("run", f"{seat} ran {models[seat]}, not {MODELS[seat]}")
            for path in subagents:
                meta = read_json(path.with_name(f"{path.stem}.meta.json"))
                if not isinstance(meta, dict) or meta.get("agentType") != SCOUT:
                    continue
                used = {r["message"].get("model") for r in assistant_records(path)}
                used = sorted(str(m) for m in used | {meta.get("model")} if m)
                helpers.append({"seat": seat, "agentType": SCOUT, "model": used})
                if seat == "build.execute" or self.arm not in SCOUT_ARMS:
                    self.mark("run", f"a {SCOUT} ran under {seat}, which has none in this arm")
                elif not used or any("haiku" not in model for model in used):
                    self.mark("run", f"{SCOUT} under {seat} ran {used}")
        self.record.update(models=models, helpers=helpers)

    def window_stop(self) -> bool:
        """True when a usage window is at its stop line (R214)."""
        seen = windows()
        weekly, five = seen["weekly"], seen["five_hour"]
        if weekly is None or five is None:
            self.record["window_unknown_polls"] = self.record.get("window_unknown_polls", 0) + 1
        elif "weekly_start" not in self.record:
            self.record["weekly_start"] = weekly["used"]
        if weekly:
            self.record["weekly_end"] = weekly["used"]
        return bool(weekly and weekly["used"] >= WEEKLY_STOP) or bool(
            five and five["used"] >= FIVE_HOUR_STOP
        )

    def finish(self) -> None:
        """The end-of-run checks, telemetry, tool paths and the cost."""
        seen = self.check_activity("end")
        self.check_models_and_helpers()
        advisor = {}
        for seat, (activity, field) in seen.items():
            configured = self.presence(seat)
            advisor[seat] = {
                "state": advisor_state(configured, activity),
                "configured": configured,
                "activity": activity,
            }
        unused = any(v["state"] == "advisor-unused" for v in advisor.values())
        self.record.update(
            advisor=advisor,
            advisor_unused=unused,
            advisor_model_field_records={seat: field for seat, (_a, field) in seen.items()},
        )
        self.costs.read(self.rig)
        usd = self.costs.total() if self.costs.complete(self.sessions) else None
        self.record["usd"] = usd
        delta = self.record.get("weekly_end", 0) - self.record.get("weekly_start", 0)
        if usd and delta >= 1:
            self.record["usd_per_percent"] = round(usd / delta, 4)
        self.collect()
        if "build.audit" in self.sessions:
            files = self.seat_transcripts("build.audit", subagents=False)
            paths = tool_paths.opened(files, self.rig.copy)
            write_json(self.folder / "tool_paths-build.audit.json", paths)

    def collect(self) -> None:
        """Save the client costs and run collect_metrics.py over the test rig's sessions."""
        client = self.folder / "client-cost.json"
        write_json(client, {"seats": list(self.costs.rows.values())})
        configured = [i for seat, ids in self.sessions.items() if self.presence(seat) for i in ids]
        write_json(self.folder / "advisor-configured.json", {"sessions": configured})
        command = [
            sys.executable, "-B", str(CHECKOUT / "dev" / "telemetry" / "collect_metrics.py"),
            "--rig", self.rig.name,
            "--since", iso(self.started - timedelta(minutes=30)),
            "--until", iso(now() + timedelta(minutes=1)),
            "--label", self.folder.name,
            "--repo", str(self.rig.copy),
            "--out-dir", str(self.folder / "telemetry"),
            "--prices", str(CHECKOUT / "dev" / "telemetry" / "prices.yml"),
            "--client-cost-json", str(client),
            "--advisor-configured-json", str(self.folder / "advisor-configured.json"),
        ]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode != 0:
            self.record["telemetry_error"] = done.stderr.strip()[-500:]

    def save(self) -> None:
        """Write the run's record."""
        write_json(self.folder / "run-inner.json", self.record)


def inner_inputs(seats: Sequence[str], folder: Path, arm: int) -> Run:
    """The inner verbs' checked environment (P92).

    Raises:
        Stop: Exit 2 before any rig call when the test rig's name or copy is missing.
    """
    name, value = os.environ.get("HACF_TEST_RIG"), os.environ.get("HACF_TEST_RIG_COPY")
    if not name or not value or not Path(value).is_dir():
        raise Stop(EXIT_USAGE, "HACF_TEST_RIG and HACF_TEST_RIG_COPY (a directory) are needed")
    if not folder.is_dir():
        raise Stop(EXIT_USAGE, f"no evidence folder: {folder}")
    return Run(arm, folder, Rig(name, Path(value).resolve(), seats))


def with_record(run: Run, body) -> int:
    """Run an inner verb's body, always leaving its record and, when possible, its cost."""
    try:
        body(run)
    except Stop as stop:
        run.record["status"] = "guard" if stop.code == EXIT_GUARD else str(stop)
        run.record["void"] = True
        if run.sessions:
            run.costs.read(run.rig)
            complete = run.costs.complete(run.sessions)
            run.record.setdefault("usd", run.costs.total() if complete else None)
        run.save()
        raise
    run.save()
    return EXIT_OK


# --------------------------------------------------------------------------- _drive (MR 5.6)


def launch_execute(run: Run) -> None:
    """Move execute into its folder with a fresh occupant, then check every seat.

    Raises:
        Stop: Void when a verb fails, a cwd is wrong or an advisor config is wrong.
    """
    the_rig = run.rig
    folder = execute_folder(the_rig.copy, run.arm)
    the_rig.find_seats()
    run.costs.read(the_rig)  # the replaced occupant's cost counts too
    old = the_rig.session("build.execute")
    execute = the_rig.session_names["build.execute"]
    reason = ["--reason", "experiment: the execute seat's own folder"]
    for argv in (
        ["seat", "set-cwd", execute, "--cwd", str(folder), *reason, "--json"],
        ["seat", "launch", execute, "--fresh", "--stop", *reason, "--json"],
    ):
        if rig(*argv, timeout=600)[0] != 0:
            raise Stop(EXIT_FAILED, f"void: rig {' '.join(argv[:2])} failed")
    for seat in the_rig.seats:
        moved = seat == "build.execute"
        run.check_cwd_and_config(seat, folder if moved else the_rig.copy, old if moved else None)


def statuses(copy: Path) -> dict[str, tuple[str, int]]:
    """Each workload slice's status and its count of audit FAILs."""
    out = {}
    for name in SLICES:
        folder = copy / "missions" / MISSION / "slices" / name
        try:
            progress = (folder / "PROGRESS.md").read_text(encoding="utf-8")
        except OSError:
            progress = ""
        fails = len(re.findall(r"(?m)^- \S+ audit_failed\b", progress))
        out[name] = (slice_status(folder / "SPEC.md"), fails)
    return out


def terminal(status: str, fails: int) -> bool:
    """A slice is over: audited, or its audit rounds are used up."""
    return status == "audited" or (status == "audit_failed" and fails >= AUDIT_ROUNDS)


def baseline_costs() -> dict[str, float]:
    """Arm 0 run 1's cost per slice (D6's yardstick), from the latest such run that has both."""
    for entry in reversed(spend_runs() or []):
        costs = entry.get("slice_cost")
        if (entry.get("kind"), entry.get("arm"), entry.get("run")) == ("arm", 0, 1):
            if isinstance(costs, dict) and all(isinstance(costs.get(s), float) for s in SLICES):
                return costs
    return {}


def work_body(copy: Path) -> str:
    """The one row that starts the work."""
    ids = " then ".join(f"{name} ({SLICE_IDS[name]})" for name in SLICES)
    return (
        f"Project root: {copy}\n\nRun the slices of the {MISSION} mission: {ids}. Take each "
        "through your build seat's execute stage and then your audit seat's audit, with up to "
        f"{AUDIT_ROUNDS} audit rounds per slice; after a third FAIL, go on to the next slice. "
        "The build seat works from a folder inside the project, so name the project root above "
        "in every handoff. Only these three seats exist; nothing is pushed or published.\n"
    )


def drive(run: Run) -> None:
    """MR 5.6: the folder, the seats, the checks, the work and the poll loop."""
    launch_execute(run)
    run.check_activity(SLICES[0])
    run.guard.check()
    baseline = baseline_costs() if run.arm else {}
    run.costs.read(run.rig)
    starts: dict[str, float] = {}
    slice_cost: dict[str, float] = {}
    previous = statuses(run.rig.copy)
    lead = run.rig.session_names["orch.lead"]
    if not send_row(lead, "workload: run the two slices", work_body(run.rig.copy)):
        raise Stop(EXIT_FAILED, "void: the work row was not created")
    began = time.monotonic()
    wall = float(seam("WALL_S", str(WALL_S)))
    total = run.costs.total()
    while True:
        time.sleep(poll_s())
        before_total = total
        run.costs.read(run.rig)
        run.follow()
        total = run.costs.total()
        run.guard.check()
        current = statuses(run.rig.copy)
        active = next((s for s in SLICES if not terminal(*previous[s])), SLICES[-1])
        for name in SLICES:
            if name not in starts and current[name][0] != "compiled":
                starts[name] = before_total
            if name in starts and not terminal(*previous[name]):
                slice_cost[name] = round(total - starts[name], 6)
        if current != previous:
            run.check_activity(active)
        previous = current
        run.record.update(
            slice_cost=slice_cost,
            rounds={name: current[name][1] for name in SLICES},
            first_pass=sum(1 for s, f in current.values() if s == "audited" and f == 0),
        )
        if all(terminal(*current[name]) for name in SLICES):
            status = "done"
        elif run.window_stop():
            status = "stopped: window"
            run.record["void"] = True
        elif any(
            name in slice_cost and name in baseline and slice_cost[name] > 2 * baseline[name]
            for name in SLICES
        ):
            status = "stopped: D6"
        elif any(s == "blocked" for s, _f in current.values()):
            status = "stopped: blocked"
        elif time.monotonic() - began >= wall:
            status = "stopped: wall"
        else:
            continue
        break
    run.record.update(status=status, wall_s=round(time.monotonic() - began, 1))
    run.finish()


# --------------------------------------------------------------------------- _replay (MR 7)


def replay_body(copy: Path) -> str:
    """The one audit row of a replay."""
    return (
        f"Project root: {copy}\n\nAudit slice {SLICE_IDS[SLICES[1]]} ({SLICES[1]}) of the "
        f"{MISSION} mission with your audit stage. The build is the last commit (HEAD); its "
        "Start is HEAD~1. There is no build seat here: record your verdict as usual and hand "
        f"nothing back.\n\nAlso write your whole audit report to {copy / REPORT_NAME}. In it, "
        "under a heading `## Findings`, put one line per finding as `- <path>:<line> - <what>`; "
        "write `none` under it when there is none.\n"
    )


def replay_inner(run: Run) -> None:
    """MR 7 `_replay`: one audit row, its verdict, the report and the tool paths."""
    the_rig = run.rig
    the_rig.find_seats()
    run.check_cwd_and_config("build.audit", the_rig.copy)
    run.check_activity(SLICES[1])
    run.guard.check()
    run.costs.read(the_rig)
    auditor = the_rig.session_names["build.audit"]
    if not send_row(auditor, "audit the built slice", replay_body(the_rig.copy)):
        raise Stop(EXIT_FAILED, "void: the audit row was not created")
    began = time.monotonic()
    wall = float(seam("WALL_S", str(REPLAY_WALL_S)))
    report = the_rig.copy / REPORT_NAME
    while True:
        time.sleep(poll_s())
        run.costs.read(the_rig)
        run.follow()
        run.guard.check()
        verdict = statuses(the_rig.copy)[SLICES[1]][0]
        if verdict in ("audited", "audit_failed") and report.is_file():
            status = "done"
        elif run.window_stop():
            status = "stopped: window"
            run.record["void"] = True
        elif time.monotonic() - began >= wall:
            status = "stopped: wall"
        else:
            continue
        break
    verdict = verdict if verdict in ("audited", "audit_failed") else "unknown"
    run.record.update(status=status, verdict=verdict, wall_s=round(time.monotonic() - began, 1))
    if report.is_file() and not report.is_symlink():
        write_private(run.folder / "audit-report.md", report.read_bytes())
    run.finish()


# --------------------------------------------------------------------------- the probes (R215)


def probe_cli() -> int:
    """One `claude -p` call that must call the advisor once."""
    refuse_when_halted()
    stamp = now().strftime("%Y%m%dT%H%M%S%f")
    guard = Guard(home(), evidence() / "guard" / f"probe-cli-{stamp}")
    guard.before()
    scratch = Path(tempfile.mkdtemp(prefix=f"advisor-probe-cli-{stamp}-", dir=tmp_root()))
    argv = [
        "claude", "-p", "--model", "sonnet", "--advisor", "opus", "--output-format", "json",
        "Before you answer, call the advisor tool exactly once. Then answer with the word done.",
    ]  # fmt: skip
    try:
        done = subprocess.run(
            argv,
            cwd=scratch,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=PROBE_WALL_S,
        )
        code, out, err = done.returncode, done.stdout, done.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        code, out, err = 1, "", str(error)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        answer = json.loads(out)
        answer = answer if isinstance(answer, dict) else {}
    except ValueError:
        answer = {}
    session = str(answer.get("session_id") or "")
    main, subs = transcript_files(session)
    activity, field = advisor_activity([main, *subs] if main else [])
    records = assistant_records(main) if main else []
    kept = any("iterations" in (r["message"].get("usage") or {}) for r in records)
    cost = answer.get("total_cost_usd")
    cost = cost if isinstance(cost, (int, float)) else None
    said = f"{out}\n{err}"
    result = {
        "at": iso(now()),
        "exit": code,
        "active": code == 0 and "Skipping advisor" not in said,
        "called": activity > 0,
        "activity": activity,
        "advisor_model_field_records": field,
        "credits_notice": bool(CREDITS_RE.search(said)),
        "transcript_found": main is not None,
        "iterations_kept": kept if main else "unknown",
        "total_cost_usd": cost if cost is not None else "unknown",
    }
    write_json(evidence() / "probe-cli.json", result)
    write_private(evidence() / f"probe-cli-{stamp}.stdout", out.encode())
    write_private(evidence() / f"probe-cli-{stamp}.stderr", err.encode())
    record_spend({"id": f"probe-cli-{stamp}", "kind": "probe", "usd": cost, "status": "done"})
    guard.check()
    print(json.dumps(result, indent=2))
    return EXIT_OK if code == 0 else EXIT_FAILED


def probe_inner(run: Run) -> None:
    """`_probe`: the scripted execute task on a two-seat arm 1 rig."""
    points = {"cwds": False}
    run.record["points"] = points
    launch_execute(run)
    points["cwds"] = True
    run.check_activity("probe")
    the_rig = run.rig
    env = clean_env(run.folder)
    head = ["git", "-C", str(the_rig.copy), "rev-parse", "HEAD"]
    start = must(head, the_rig.copy, env)
    body = (
        f"Project root: {the_rig.copy}\n\nA short scripted task. Read the SPEC of slice "
        f"{SLICE_IDS[SLICES[0]]} through your slice skill. Add one line to README.md in the "
        "project root above your folder, run the project's tests, call the advisor tool once, "
        "then commit that file.\n"
    )
    execute = the_rig.session_names["build.execute"]
    if not send_row(execute, "probe: one scripted task", body):
        raise Stop(EXIT_FAILED, "void: the task row was not created")
    began = time.monotonic()
    prompted = finished = False
    while not finished and time.monotonic() - began < wait_s(PROBE_WALL_S):
        time.sleep(poll_s())
        run.costs.read(the_rig)
        run.follow()
        run.guard.check()
        state = the_rig.nodes().get("build.execute", {}).get("activityState") or {}
        prompted = prompted or bool((state.get("needsInput") or {}).get("count"))
        finished = must(head, the_rig.copy, env) != start
        if run.window_stop():
            break
    run.record.update(status="done")
    run.finish()
    states = {seat: info["state"] for seat, info in run.record["advisor"].items()}
    points.update(
        task_finished=finished,
        no_permission_prompt=not prompted,
        advisor_present_on_execute=states.get("build.execute") == "present",
        advisor_absent_on_audit=states.get("build.audit") == "absent" and not run.mixed,
    )


def probe_mechanism() -> int:
    """Run `_probe` on a test rig and write the go/no-go."""
    refuse_when_halted()
    entry, folder = outer_run("probe", 1, 0, ("build.execute", "build.audit"), "_probe")
    record = read_json(folder / "run-inner.json") or {}
    points = dict(record.get("points") or {})
    cli = read_json(evidence() / "probe-cli.json") or {}
    guard = Guard(home(), evidence() / "guard" / folder.name)
    points["hashes_unchanged"] = entry["code"] != EXIT_GUARD and not guard.changed()
    points["cli_active"] = cli.get("active") is True
    points["cli_no_credits_notice"] = cli.get("credits_notice") is False
    needed = (
        "cwds", "task_finished", "no_permission_prompt", "advisor_present_on_execute",
        "advisor_absent_on_audit", "hashes_unchanged", "cli_active", "cli_no_credits_notice",
    )  # fmt: skip
    points = {name: points.get(name) is True for name in needed}
    go = entry["code"] == EXIT_OK and all(points.values())
    result = {"at": iso(now()), "go": go, "points": points, "run": folder.name}
    write_json(evidence() / "probe-mechanism.json", result)
    print(json.dumps(result, indent=2))
    return entry["code"]


# --------------------------------------------------------------------------- the outer verbs


def tmp_root() -> Path:
    """$TMPDIR, which must be set and not /tmp (test_rig.py refuses it too).

    Raises:
        Stop: Exit 2 otherwise.
    """
    value = os.environ.get("TMPDIR")
    if not value or Path(value).resolve() == Path("/tmp").resolve():
        raise Stop(EXIT_USAGE, "TMPDIR must point at an on-disk directory, not /tmp")
    path = Path(value).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def refuse_when_halted() -> None:
    """Nothing runs while the experiment is halted.

    Raises:
        Stop: Exit 75.
    """
    if (evidence() / "halted.json").exists():
        raise Stop(EXIT_WAIT, "halted: halted.json is present; the lead removes it")


def outer_run(kind, arm, number, seats, verb, prepare_more=None) -> tuple[dict, Path]:
    """Guard, prepare, run the test rig, guard again, record the spend.

    Returns:
        ({"code": exit code, ...}, the run's evidence folder).
    """
    stamp = now().strftime("%Y%m%dT%H%M%S%f")
    label = {"arm": f"arm{arm}-run{number}", "replay": f"replay-arm{arm}-run{number}"}.get(
        kind, "probe-mechanism"
    )
    run_id = f"{label}-{stamp}"
    scratch_root = tmp_root()
    guard = Guard(home(), evidence() / "guard" / run_id)
    guard.before()
    folder = private_dir(evidence() / "runs" / run_id)
    previous = [parse_time(r.get("started_at")) for r in spend_runs() or []]
    previous = [when for when in previous if when]
    started = now()
    entry: dict = {
        "id": run_id,
        "kind": kind,
        "arm": arm,
        "run": number,
        "started_at": iso(started),
        "gap_s": round((started - max(previous)).total_seconds()) if previous else None,
    }
    if entry["gap_s"] is not None and entry["gap_s"] < ARM_GAP_S:
        note(f"only {entry['gap_s']} s since the last start (R223 asks for {ARM_GAP_S}); recorded")
    scratch = Path(
        tempfile.mkdtemp(prefix=f"advisor-arm-{arm}-{number}-{stamp}-", dir=scratch_root)
    )
    try:
        project = prepare_project(scratch, arm)
        if prepare_more:
            prepare_more(project, scratch)
        test_rig = seam("TEST_RIG", str(CHECKOUT / "dev" / "test_rig.py"))
        command = [
            sys.executable, test_rig, "run", "--project", str(project),
            "--seats", ",".join(seats), "--",
            sys.executable, str(SELF), verb, "--arm", str(arm), "--run", str(number),
            "--evidence", str(folder),
        ]  # fmt: skip
        sys.stderr.flush()
        code = subprocess.run(command, stdin=subprocess.DEVNULL).returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        guard.check()
    except Stop as stop:
        note(str(stop))
        code = EXIT_GUARD
    inner = read_json(folder / "run-inner.json")
    if code not in (EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_GUARD, EXIT_WAIT):
        code = EXIT_FAILED
    entry.update(inner if isinstance(inner, dict) else {"status": "no result", "usd": None})
    entry.setdefault("usd", None)
    if code == EXIT_GUARD:
        entry.update(status="guard", void=True)
    entry.update(code=code, ended_at=iso(now()))
    write_json(folder / "run.json", entry)
    if isinstance(inner, dict) or code not in (EXIT_USAGE, EXIT_WAIT):
        record_spend(entry)
    note(f"{run_id}: {entry.get('status')}; ${entry.get('usd')}; exit {code}")
    return entry, folder


def wait_if_gated(arm: int, kind: str, cap: float) -> dict:
    """The gate before a run.

    Raises:
        Stop: Exit 75 with the reasons.
    """
    reading = gate(arm, kind, cap)
    if not reading["go"]:
        print(json.dumps(reading, indent=2))
        raise Stop(EXIT_WAIT, "wait: " + "; ".join(reading["reasons"]))
    return reading


def cmd_run(arm: int, number: int, cap: float) -> int:
    """`run`: the refusals, the gate, then the arm."""
    if arm in ADVISOR_ARMS:
        probe = read_json(evidence() / "probe-mechanism.json")
        if not isinstance(probe, dict) or probe.get("go") is not True:
            raise Stop(EXIT_USAGE, f"arm {arm} needs a passed `probe mechanism` (R210)")
    if arm and not baseline_costs():
        raise Stop(EXIT_USAGE, "D6 needs arm 0 run 1's cost of both slices; run it first")
    reading = wait_if_gated(arm, "arm", cap)
    entry, folder = outer_run("arm", arm, number, SEATS, "_drive")
    write_json(folder / "gate.json", reading)
    return entry["code"]


def sealed_problems(folder: Path, listing: Path) -> list[str]:
    """Why the sealed dir does not match the committed hashes; [] when it does."""
    try:
        wanted = sorted(listing.read_text(encoding="utf-8").split("\n"))
        found = [f"{file_hash(folder / name)}  file:{name}" for name in SEALED_FILES]
        key = json.loads((folder / "key.yml").read_text(encoding="utf-8"))
        found += [
            f"{hashlib.sha256(d['id'].encode()).hexdigest()}  defect-id" for d in key["defects"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"cannot read the sealed material: {type(error).__name__}"]
    if sorted([*found, ""]) != wanted:
        return ["the sealed material does not match sealed.sha256"]
    return []


def checked_sealed() -> Path:
    """The sealed dir, verified against sealed.sha256.

    Raises:
        Stop: Exit 3 on a mismatch.
    """
    listing = Path(seam("SEALED_SHA", str(KIT / "sealed.sha256")))
    problems = sealed_problems(sealed_dir(), listing)
    if problems:
        raise Stop(EXIT_FAILED, problems[0])
    return sealed_dir()


def build_replay(project: Path, scratch: Path) -> None:
    """Apply the sealed build as the builder's one commit and mark 02-medium executed (MR 7)."""
    sealed = sealed_dir()
    env = clean_env(
        scratch,
        GIT_AUTHOR_NAME="build-execute",
        GIT_AUTHOR_EMAIL="build-execute@example.invalid",
    )
    base = must(["git", "rev-parse", "HEAD"], project, env).strip()
    for name in ("reference.patch", "defects.patch"):
        done = subprocess.run(
            ["git", "apply", str(sealed / name)], cwd=project, env=env, capture_output=True
        )
        if done.returncode != 0:  # git's own message would quote the patch
            raise Stop(EXIT_FAILED, f"a sealed patch did not apply (exit {done.returncode})")
    detail = scratch / "executing.txt"
    detail.write_text(f"base {base}; escape_tracking: v1\n", encoding="utf-8")
    slice_dir = f"missions/{MISSION}/slices/{SLICES[1]}"
    progress = [sys.executable, "-B", ".agents/scripts/hyper_spec.py", "progress"]
    progress += ["--slice", slice_dir]
    must([*progress, "--event", "executing", "--detail-file", str(detail)], project, env)
    must([*progress, "--event", "executed"], project, env)
    commit_all(project, env, f"{MISSION} {SLICE_IDS[SLICES[1]]}: build the slice")


def cmd_replay(arm: int, number: int, cap: float) -> int:
    """`replay`: the sealed check, the gate, then the audit alone."""
    checked_sealed()
    reading = wait_if_gated(arm, "replay", cap)
    entry, folder = outer_run("replay", arm, number, ("build.audit",), "_replay", build_replay)
    write_json(folder / "gate.json", reading)
    return entry["code"]


# --------------------------------------------------------------------------- score (MR 8)


def findings_section(report: str) -> str | None:
    """The text under a `Findings` heading up to the next heading of its level, or None."""
    heading = re.search(r"(?im)^(#{1,6})[ \t]*Findings\b.*$", report)
    if heading is None:
        return None
    rest = report[heading.end() :]
    ends = re.search(rf"(?m)^#{{1,{len(heading.group(1))}}}[ \t]", rest)
    return rest[: ends.start()] if ends else rest


def catches(report: str, defects: Sequence[dict]) -> list[str] | None:
    """The caught defect ids: a finding names the file and a line within the range ± 5.

    None (unknown, never 0) when the report has no Findings section.
    """
    section = findings_section(report)
    if section is None:
        return None
    cited = [
        (os.path.normpath(path), int(first), int(last or first))
        for path, first, last in CITATION_RE.findall(section)
    ]
    caught = []
    for defect in defects:
        name, (low, high) = defect["file"], defect["lines"]
        for path, first, last in cited:
            same = path == name or path.endswith(f"/{name}") or name.endswith(f"/{path}")
            if same and first <= high + CATCH_SLACK and last >= low - CATCH_SLACK:
                caught.append(defect["id"])
                break
    return caught


def cmd_score() -> int:
    """Write catches.json per replay: counts and id hashes, never a defect's text."""
    sealed = checked_sealed()
    defects = json.loads((sealed / "key.yml").read_text(encoding="utf-8"))["defects"]
    for entry in spend_runs() or []:
        if entry.get("kind") != "replay":
            continue
        folder = evidence() / "runs" / entry["id"]
        try:
            report = (folder / "audit-report.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            report = None
        caught = None if report is None else catches(report, defects)
        paths = read_json(folder / "tool_paths-build.audit.json")
        opened = "unknown"
        if isinstance(paths, dict):
            opened = all(d["file"] in paths.get("paths", []) for d in defects)
        result = {
            "replay": entry["id"],
            "arm": entry.get("arm"),
            "caught": "unknown" if caught is None else len(caught),
            "of": len(defects),
            "caught_id_sha256": [hashlib.sha256(i.encode()).hexdigest() for i in caught or []],
            "opened_all_defect_files": opened,
        }
        write_json(folder / "catches.json", result)
        print(f"{entry['id']}: caught {result['caught']} of {result['of']}")
    return EXIT_OK


# --------------------------------------------------------------------------- report (R212)


def latest_valid(runs: Sequence[dict], arm: int) -> list[dict]:
    """The latest finished, unmixed run 1 and run 2 of an arm (those that exist)."""
    found: dict[int, dict] = {}
    for entry in runs:
        if entry.get("kind") != "arm" or entry.get("arm") != arm:
            continue
        if entry.get("status") == "done" and not entry.get("void") and not entry.get("mixed"):
            found[entry.get("run")] = entry
    return [found[number] for number in (1, 2) if number in found]


def metrics(entry: dict) -> dict[str, float | None]:
    """The builder claim's metrics of one run."""
    rounds = entry.get("rounds")
    return {
        "cost": entry.get("usd"),
        "rework": sum(rounds.values()) if isinstance(rounds, dict) else None,
        "first_pass": entry.get("first_pass"),
    }


def differences(runs: Sequence[dict], arm: int) -> dict[str, tuple[float, float] | None]:
    """Per metric: (mean of the arm − mean of arm 0, the A/A spread), None when unknown."""
    base, other = latest_valid(runs, 0), latest_valid(runs, arm)
    out: dict[str, tuple[float, float] | None] = {}
    for name in ("cost", "rework", "first_pass"):
        a = [metrics(r)[name] for r in base]
        b = [metrics(r)[name] for r in other]
        if len(a) < 2 or len(b) < 2 or None in a or None in b:
            out[name] = None
        else:
            out[name] = (statistics.mean(b) - statistics.mean(a), abs(a[0] - a[1]))
    return out


def builder_verdict(runs: Sequence[dict], arm: int) -> str:
    """promising, not promising or unknown for arms 1 and 3 against arm 0."""
    found = differences(runs, arm)
    if any(value is None for value in found.values()):
        return "unknown"
    cost, rework, first = (found[name] for name in ("cost", "rework", "first_pass"))
    if cost[0] > cost[1] or rework[0] > rework[1] or first[0] < -first[1]:
        return "not promising"
    if cost[0] < -cost[1] or rework[0] < -rework[1]:
        return "promising"
    return "unknown"


def audit_verdict(rows: Sequence[dict]) -> str:
    """The audit-context verdict: arm 2 against arm 0 over the replays."""
    base = [r for r in rows if r["arm"] == 0]
    other = [r for r in rows if r["arm"] == 2]
    if not base or not other:
        return "unknown"
    if any(not isinstance(r["caught"], int) for r in base + other):
        return "unknown"
    if statistics.mean(r["caught"] for r in other) < statistics.mean(r["caught"] for r in base):
        return "not promising"
    costs = [r["usd"] for r in base]
    if len(base) < 2 or any(not isinstance(r["usd"], (int, float)) for r in base + other):
        return "unknown"
    cheaper = statistics.mean(r["usd"] for r in other) - statistics.mean(costs)
    if cheaper < -(max(costs) - min(costs)) and all(r["opened_all"] is True for r in other):
        return "promising"
    return "unknown"


def combined_verdict(builder: str, audit: str) -> str:
    """Arm 3 is promising only when both claims are."""
    if builder == audit == "promising":
        return "promising"
    return "not promising" if "not promising" in (builder, audit) else "unknown"


def seat_usage(folder: Path) -> dict[str, dict]:
    """Per seat, the sums over the run's scorecard slices; an unknown part stays unknown."""
    cards = sorted((folder / "telemetry").glob("*.json"))
    cards = [c for c in cards if not c.name.endswith(".context.json")]
    card = read_json(cards[-1]) if cards else None
    columns = (
        "total", "cache_read", "usd", "advisor_tokens", "advisor_usd", "helper_tokens",
        "helper_usd",
    )  # fmt: skip
    out: dict[str, dict] = {}
    for seat in card.get("seats", []) if isinstance(card, dict) else []:
        sums: dict = {}
        for column in columns:
            values = [s[column] for s in seat["slices"] if column in s]
            if not values:
                sums[column] = None
            elif any(not isinstance(v, (int, float)) for v in values):
                sums[column] = "unknown"
            else:
                sums[column] = round(sum(values), 4)
        sums["advisor_residual_usd"] = residual(card, seat["seat"])
        out[seat["seat"]] = sums
    return out


def residual(card: dict, seat: str) -> float | str | None:
    """A seat's advisor residual over its sessions, an estimate; one unknown makes it unknown."""
    entries = card.get("client_cost")
    entries = entries.values() if isinstance(entries, dict) else []
    values = [e.get("advisor_residual_usd") for e in entries if e.get("seat") == seat]
    if not values:
        return None
    if any(not isinstance(v, (int, float)) for v in values):
        return "unknown"
    return round(sum(values), 4)


def cell(value: object) -> str:
    """A table cell: unknown for None, never 0."""
    if value is None:
        return "unknown"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {cell(v)}" for k, v in value.items()) or "none"
    if isinstance(value, list):
        return ", ".join(cell(v) for v in value) or "none"
    return str(value).replace("|", "/").replace("\n", " ")


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """A Markdown table, or a line saying there is nothing."""
    if not rows:
        return ["none recorded", ""]
    lines = [f"| {' | '.join(header)} |", f"|{'---|' * len(header)}"]
    lines += [f"| {' | '.join(cell(value) for value in row)} |" for row in rows]
    return [*lines, ""]


def replay_rows(runs: Sequence[dict]) -> list[dict]:
    """Every finished, unmixed replay with its score."""
    rows = []
    for entry in runs:
        if entry.get("kind") != "replay":
            continue
        folder = evidence() / "runs" / entry["id"]
        score = read_json(folder / "catches.json") or {}
        paths = read_json(folder / "tool_paths-build.audit.json") or {}
        rows.append(
            {
                "id": entry["id"],
                "arm": entry.get("arm"),
                "status": entry.get("status"),
                "valid": entry.get("status") == "done" and not entry.get("mixed"),
                "usd": entry.get("usd"),
                "caught": score.get("caught", "unknown"),
                "opened_all": score.get("opened_all_defect_files", "unknown"),
                "paths": paths.get("paths"),
            }
        )
    return rows


def render_report() -> str:
    """The Markdown tables of slice 13's MR 5, from the evidence dir."""
    runs = spend_runs() or []
    lines = ["# Advisor and Haiku-helper experiment: measured tables", "", "## Probes", ""]
    for name in ("probe-cli.json", "probe-mechanism.json"):
        probe = read_json(evidence() / name)
        lines += [f"### {name}", ""]
        rows = list(probe.items()) if isinstance(probe, dict) else []
        lines += table(("field", "value"), rows)

    lines += ["## Runs", ""]
    rows, seats = [], []
    for entry in runs:
        if entry.get("kind") == "probe" and "models" not in entry:
            continue
        folder = evidence() / "runs" / str(entry.get("id"))
        reading = read_json(folder / "gate.json") or {}
        gate_text = {k: reading.get(k) for k in ("headroom_percent", "need_percent", "spent_usd")}
        rows.append(
            [entry.get(k) for k in ("id", "status", "void", "mixed", "advisor_unused", "usd")]
            + [entry.get(k) for k in ("rounds", "first_pass", "wall_s", "gap_s")]
            + [gate_text]
        )
        for seat, used in seat_usage(folder).items():
            logical = seat.split("@")[0].replace("-", ".", 1)
            effort = "medium (configured, unverified)" if logical == "build.execute" else "default"
            model = (entry.get("models") or {}).get(logical)
            seats.append([entry.get("id"), logical, model, effort, *used.values()])
    lines += table(
        ("run", "status", "void", "mixed", "advisor-unused", "USD", "audit FAILs", "first-pass",
         "wall s", "gap s", "gate"),
        rows,
    )  # fmt: skip
    lines += ["Per seat (USD from list prices; the advisor residual column is an estimate).", ""]
    lines += table(
        ("run", "seat", "model", "effort", "tokens", "cache read", "USD", "advisor tokens",
         "advisor USD", "helper tokens", "helper USD", "advisor residual USD (estimate)"),
        seats,
    )  # fmt: skip

    base = latest_valid(runs, 0)
    lines += ["## A/A spread", ""]
    spreads = []
    for name in ("cost", "rework", "first_pass"):
        values = [metrics(r)[name] for r in base]
        known = len(values) == 2 and None not in values
        spreads.append([name, abs(values[0] - values[1]) if known else None])
    lines += table(("metric", "|arm 0 run 1 − arm 0 run 2|"), spreads)
    rows = []
    for arm in (1, 2, 3):
        for name, found in differences(runs, arm).items():
            rows.append([arm, name, *((round(found[0], 4), found[1]) if found else (None, None))])
    lines += table(("arm", "metric", "difference from arm 0", "A/A spread"), rows)

    replays = replay_rows(runs)
    lines += ["## Replay", ""]
    lines += table(
        ("replay", "arm", "status", "USD", "caught of 3", "opened every key file"),
        [[r[k] for k in ("id", "arm", "status", "usd", "caught", "opened_all")] for r in replays],
    )
    opened = {
        arm: {p for r in replays if r["arm"] == arm and r["valid"] for p in r["paths"] or []}
        for arm in (0, 2)
    }
    lines += table(
        ("paths", "list"),
        [
            ["opened by arm 0's audits, not arm 2's", sorted(opened[0] - opened[2])],
            ["opened by arm 2's audits, not arm 0's", sorted(opened[2] - opened[0])],
        ],
    )
    if not replays:
        lines += ["The replay is not run: no replay is recorded.", ""]

    audit = audit_verdict([r for r in replays if r["valid"]])
    lines += ["## Verdicts", ""]
    lines += table(
        ("claim", "arm", "verdict"),
        [
            ["builder", 1, builder_verdict(runs, 1)],
            ["audit context", 2, audit],
            ["builder and audit", 3, combined_verdict(builder_verdict(runs, 3), audit)],
        ],
    )

    spent = total_usd(runs)
    lines += ["## Spend", "", f"Total: {cell(spent)} USD of the $90 cap.", ""]
    missing = [
        f"arm {arm} run {number}"
        for arm in (0, 1, 2, 3)
        for number in (1, 2)
        if not any(
            (r.get("kind"), r.get("arm"), r.get("run"), r.get("status"))
            == ("arm", arm, number, "done")
            for r in runs
        )
    ]
    lines += [f"Not finished: {cell(missing)}. A stopped or void run's reason is in the table.", ""]
    lines += [
        "## Upstream drafts (not filed)",
        "",
        "- U5 (OpenRig): `advisorModel` in the per-seat `--settings` OpenRig already composes.",
        "- U6 (Claude Code): document whether the transcript keeps `usage.iterations` for advisor "
        "turns; a per-process way to set the advisor without a shared settings file; and that the "
        "advisor slash command in a terminal writes user settings.",
        "",
    ]
    return re.sub("(?i)adopt", "take up", "\n".join(lines))


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        prog="run_arm.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    gated = verbs.add_parser("gate", help="may an arm start now?")
    gated.add_argument("--arm", type=int, choices=(0, 1, 2, 3), required=True)
    gated.add_argument("--cap-usd", type=float, default=CAP_USD)
    gated.add_argument("--json", action="store_true")
    probe = verbs.add_parser("probe", help="slice 13's probes")
    probe.add_argument("which", choices=("cli", "mechanism"))
    for name, arms in (("run", (0, 1, 2, 3)), ("replay", (0, 2))):
        made = verbs.add_parser(name)
        made.add_argument("--arm", type=int, choices=arms, required=True)
        made.add_argument("--run", type=int, required=True)
        made.add_argument("--cap-usd", type=float, default=CAP_USD)
    for name in ("_drive", "_replay", "_probe"):
        made = verbs.add_parser(name)
        made.add_argument("--arm", type=int, choices=(0, 1, 2, 3), required=True)
        made.add_argument("--run", type=int, required=True)
        made.add_argument("--evidence", type=Path, required=True)
    verbs.add_parser("score", help="score the replays")
    report = verbs.add_parser("report", help="write the tables")
    report.add_argument("--out", type=Path, required=True)
    return parser


def dispatch(args: argparse.Namespace) -> int:
    """Carry out one parsed verb."""
    if args.verb == "gate":
        reading = gate(args.arm, "arm", args.cap_usd)
        if args.json:
            print(json.dumps(reading, indent=2))
        else:
            print("go" if reading["go"] else "wait: " + "; ".join(reading["reasons"]))
        return EXIT_OK if reading["go"] else EXIT_WAIT
    if args.verb == "probe":
        return probe_cli() if args.which == "cli" else probe_mechanism()
    if args.verb in ("run", "replay"):
        if args.run < 1 or (args.verb == "run" and args.run > 2):
            raise Stop(EXIT_USAGE, "--run is 1 or 2 for an arm, and at least 1 for a replay")
        verb = cmd_run if args.verb == "run" else cmd_replay
        return verb(args.arm, args.run, args.cap_usd)
    if args.verb == "score":
        return cmd_score()
    if args.verb == "report":
        args.out.write_text(render_report(), encoding="utf-8")
        print(args.out)
        return EXIT_OK
    bodies = {
        "_drive": (SEATS, drive),
        "_replay": (("build.audit",), replay_inner),
        "_probe": (("build.execute", "build.audit"), probe_inner),
    }
    seats, body = bodies[args.verb]
    return with_record(inner_inputs(seats, args.evidence, args.arm), body)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except Stop as stop:
        note(str(stop))
        return stop.code


if __name__ == "__main__":
    sys.exit(main())
