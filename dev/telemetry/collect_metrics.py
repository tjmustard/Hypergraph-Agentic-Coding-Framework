#!/usr/bin/env python3
"""Read-only token telemetry for an HACF rig: hacf.telemetry/v1 scorecards and the B0/B1 metric.

Dev tooling only (v0.6.3, OPR.99.0.7.1); never installed into projects. Stdlib only, Python 3.10+.

What it reads, and nothing else:
- a SNAPSHOT COPY of the OpenRig database (`openrig.sqlite` plus `-wal` and `-shm`, copied with
  plain file reads into a private dir, checked with `PRAGMA quick_check`, removed before exit). The
  source path is never passed to sqlite. Only the `usage_samples`, `context_usage` and `queue_items`
  columns named in REQUIRED_COLUMNS and the `sessions` columns named in SESSION_COLUMNS are
  selected; never a text column such as `queue_items.body`.
- from the seats' Claude Code JSONL transcripts: per assistant record only the timestamp, request
  id, message id, model id and the four `usage` token counts; the model and the same four counts
  of each `usage.iterations` entry of type `advisor_message`; and whether the record carries an
  `advisorModel` field (its value is not read). Never message text.
- from a subagent transcript's `<name>.meta.json`: only `agentType` and `model`.
- `git log` of the repository (commit times, subjects, trailers) and the slices' PROGRESS.md
  `audit_failed` and `blocked` Outcomes lines.
- only when `--loops-dir <dir>` is given (it has no default): the audit's loop records
  `hacf-loop-<slice id>.md` in that dir, as a backfill for slices whose PROGRESS.md has no
  `audit_failed` line (FAILs = Iteration - 1 after a PASS, else Iteration).
A slice's `blocked` part is unknown (null) when its PROGRESS.md has neither an `audit_failed` nor a
`blocked` line, unless an `executing` line's detail carries `escape_tracking: v1` (the contract
text; the JSON form `"escape_tracking":"v1"` is read too), which the builder writes once halts are
recorded.

A slice's escapes are its audit FAILs, its builder halts (`blocked`) and the later commits that
repin, block-change or replan it (an `HACF-Repin`, `HACF-Block-Changed` or `HACF-Replan-Of`
trailer, or a `resolve: replan` subject naming it), one per commit; `escape_parts` gives each part
(null when its source is missing) and `escapes` their known sum.

A seat's session ids come from three sources, as one set: in-window `context` samples,
`context_usage`, and (v0.6.7, OPR.99.0.15.4) the `sessions` table's `claude_id` rows whose occupancy
overlaps the window. The occupancy only chooses which transcripts to open: a record counts by its
own timestamp.

A missing table, column or file becomes a reason in `unknowns`, never a crash or a wrong number;
only an absent `sessions` table adds none (older OpenRig databases). Seats with no transcript record
in the window give unknown token totals, never 0.

OpenRig's `provider_window` samples (the rate-limit windows) carry no session and no tokens by
OpenRig's design, so they are not a gap. A `context` sample without a session is counted in the
scorecard's `samples` and never attributed to a session by time.

v0.6.8 (OPR.99.0.16.12), additive and only where there is something to say: a per-seat entry
with advisor evidence gets `advisor_*` columns (priced at each iteration's own model, never added
to the seat's columns) and `advisor_model_field_records`; subagents with a meta file are split in
`subagents_by_type`, and `hacf-scout`'s sums also appear as `helper_*` (both stay inside the
seat's totals). `--advisor-configured-json <file>` names the sessions the runner configured with
an advisor: one without any advisor iteration has `unknown` advisor tokens, never 0. The
`advisorModel` record field never marks a session configured. `--client-cost-json <file>` (saved
`rig usage cost --json` answers) adds `client_cost`: per session the client's figure minus the
priced cost, labelled `estimate`, or `unknown`.

Usage:
  collect_metrics.py --since <ISO> --until <ISO> [--label <name>] [options]   one scorecard
  collect_metrics.py --b0 --window <name>=<since>..<until> [--window ...]      scorecards + B0.json
  collect_metrics.py --b1 --window <name>=<since>..<until> [--window ...]      scorecards + B1.json
  collect_metrics.py --compare <B0.json> <B1.json>                              the §8 verdict

Every scorecard also carries `context` (v0.6.4, OPR.99.0.8.3): per seat, per occupant (session
id), the turns and the peak resident context of the seat's MAIN transcript, where one assistant
record's context is input_tokens + cache_read_input_tokens + cache_creation_input_tokens.
`--context-series` also writes `<label>.context.json`, each turn's [ts, context], to --out-dir.

v0.6.6 (OPR.99.0.11.1), additive: every per-seat, per-slice entry carries `models`, `mixed` and
`usage_by_model` (the four token counts split by the model id that served them), and each
aggregate slice row carries `mixed` and `size` ({files, lines} of the slice's own commits, read
with `git show --numstat`). With `--prices <file>` (a `hacf.prices/v1` YAML, see
dev/telemetry/prices.yml; read with PyYAML, the one non-stdlib import, taken only then) the
entries also carry `usd` and the aggregate `usd_per_slice`. A model the table lacks gives
`usd: null` and a reason, never zero.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "hacf.telemetry/v1"
AGGREGATE_SCHEMA = "hacf.telemetry.aggregate/v1"
VERDICT_SCHEMA = "hacf.telemetry.verdict/v1"
PRICES_SCHEMA = "hacf.prices/v1"
RATE_KEYS = ("fresh_in", "cache_write", "cache_read", "output")
UNATTRIBUTED = "unattributed"
UNKNOWN = "unknown"
ADVISOR_STEP = "advisor_message"
HELPER_TYPE = "hacf-scout"
# column -> usage key, for the advisor_* and helper_* columns
SPLIT_COLUMNS = {
    "fresh_in": "input_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
    "output": "output_tokens",
}
DEFAULT_ADVISOR = "advisor-lead@kernel"
TARGET_REDUCTION = 0.30
MIN_N = 3
# Cost weights of mission SPEC §8: fresh input x1, cache write x1.25, cache read x0.1, output x5.
WEIGHTS = {"fresh_in": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
REQUIRED_COLUMNS = {
    "usage_samples": ("seat_session", "lane", "captured_at", "native_session_id"),
    "context_usage": ("session_name", "session_id", "transcript_path"),
    "queue_items": ("qitem_id", "ts_created", "source_session", "destination_session"),
}
# optional: a database without the table is read as before
SESSION_COLUMNS = ("session_name", "resume_type", "resume_token", "created_at", "last_seen_at")
CONTEXT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
USAGE_KEYS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUBJECT_SLICE_RE = re.compile(r"^\S+ (OPR\.\d+(?:\.\d+)+):")
SUBJECT_RESOLVE_RE = re.compile(r"^(\S+) resolve:")
FRONTMATTER_ID_RE = re.compile(r"^id:\s*['\"]?([^'\"\s]+)['\"]?\s*$", re.MULTILINE)
AUDIT_FAILED_RE = re.compile(r"^- \S+ audit_failed\b", re.MULTILINE)
BLOCKED_RE = re.compile(r"^- \S+ blocked\b", re.MULTILINE)
LOOP_ITERATION_RE = re.compile(r"^- Iteration:\s*(\d+)\b", re.MULTILINE)
LOOP_VERDICT_RE = re.compile(r"^- Latest verdict:\s*(\S+)", re.MULTILINE)
NO_HALT_RECORDS = "no halt records before v0.6.4"
# ruling P22: a builder that records halts writes this into its `executing` Outcomes detail
# the contract text is `escape_tracking: v1`; a detail file written as JSON says the same
TRACKING_MARKER_RE = re.compile(
    r"""^- \S+ executing - .*\bescape_tracking"?\s*:\s*"?v1\b""", re.MULTILINE
)
GUARD_LINE_RE = re.compile(r"^(\S+) .*\baction=restart\b")


class UsageError(Exception):
    """A bad argument: the CLI exits 2 with one line and writes nothing."""


# --------------------------------------------------------------------------- time


def parse_ts(text: str) -> datetime:
    """Parse an ISO-8601 timestamp to an aware UTC datetime; a naive one is taken as UTC.

    Raises:
        ValueError: when the text is not an ISO-8601 timestamp.
    """
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    # Python 3.10's fromisoformat takes only 3 or 6 fraction digits: pad or cut to 6.
    match = re.match(r"^(.*T\d\d:\d\d:\d\d)\.(\d+)(.*)$", raw)
    if match:
        raw = f"{match.group(1)}.{match.group(2)[:6].ljust(6, '0')}{match.group(3)}"
    value = datetime.fromisoformat(raw.replace(" ", "T", 1) if "T" not in raw else raw)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def try_ts(text: object) -> datetime | None:
    """Parse a timestamp, or return None when it is missing or malformed."""
    if not isinstance(text, str):
        return None
    try:
        return parse_ts(text)
    except ValueError:
        return None


def iso(value: datetime) -> str:
    """Format a UTC datetime as `YYYY-MM-DDTHH:MM:SSZ` (microseconds kept when present)."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    """Return the current UTC time as a file-name stamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- output files


def ensure_private_dir(path: Path) -> Path:
    """Create a directory with mode 0700 (and tighten an existing one we own)."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_json(out_dir: Path, name: str, data: dict) -> Path:
    """Write JSON atomically (temp file + rename) with mode 0600 and return its path."""
    ensure_private_dir(out_dir)
    target = out_dir / name
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=out_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return target


def unique_name(out_dir: Path, base: str) -> str:
    """Return `<base>-<UTC ts>.json`, with a counter when that name already exists."""
    name = f"{base}-{stamp()}.json"
    counter = 2
    while (out_dir / name).exists():
        name = f"{base}-{stamp()}-{counter}.json"
        counter += 1
    return name


# --------------------------------------------------------------------------- snapshot (F12)


def copy_db_files(source: Path, dest_dir: Path) -> Path:
    """Copy the database and its -wal/-shm with plain file reads; return the copy's path.

    Never opens the source through sqlite. A missing -wal or -shm is not an error.
    """
    copy = dest_dir / "openrig.sqlite"
    shutil.copyfile(source, copy)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{source}{suffix}")
        if side.exists():
            shutil.copyfile(side, Path(f"{copy}{suffix}"))
    return copy


def _remove_copy(copy: Path) -> None:
    """Delete one snapshot copy and its -wal/-shm, each by its exact path."""
    for path in (copy, Path(f"{copy}-wal"), Path(f"{copy}-shm"), Path(f"{copy}-journal")):
        if path.exists():
            path.unlink()


def take_snapshot(source: Path, snap_dir: Path, unknowns: list[str]) -> sqlite3.Connection | None:
    """Copy the database into snap_dir, check it with quick_check (one retry) and open the copy.

    Returns the open connection on the copy, or None (with a reason) after two failed checks.
    """
    if not source.is_file():
        unknowns.append(f"snapshot: database {source.name} not found")
        return None
    last_error = ""
    for _attempt in range(2):
        conn = None
        try:
            copy = copy_db_files(source, snap_dir)
            conn = sqlite3.connect(str(copy))
            result = conn.execute("PRAGMA quick_check").fetchone()
            if result and result[0] == "ok":
                return conn
            last_error = f"quick_check returned {result[0] if result else 'nothing'}"
        except sqlite3.Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except OSError as exc:
            last_error = f"copy failed: {exc.strerror or exc}"
        if conn is not None:
            conn.close()
        _remove_copy(snap_dir / "openrig.sqlite")
    unknowns.append(f"snapshot: two failed checks ({last_error}); database numbers unknown")
    return None


def schema_gaps(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Return, per required table, the set of required columns present on the copy (R5).

    A missing table maps to an empty set.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    present: dict[str, set[str]] = {}
    for table, columns in REQUIRED_COLUMNS.items():
        if table not in tables:
            present[table] = set()
            continue
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        present[table] = {column for column in columns if column in have}
    return present


def session_columns(conn: sqlite3.Connection) -> set[str] | None:
    """Return the SESSION_COLUMNS the copy's `sessions` table has, or None without the table."""
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
    ).fetchone()
    if found is None:
        return None
    have = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    return {column for column in SESSION_COLUMNS if column in have}


def like_rig(rig: str) -> str:
    """Return the LIKE pattern `%@<rig>` with backslash, `%` and `_` escaped for ESCAPE."""
    escaped = rig.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%@{escaped}"


# --------------------------------------------------------------------------- database reads


class DbView:
    """The facts this tool takes from the snapshot copy, or their unknown reasons."""

    def __init__(self) -> None:
        """Start with no database: no seats, no sessions and unknown advisor rows."""
        self.available = False
        self.seat_source = False
        self.queue_ok = False
        self.seats: list[str] = []
        self.sessions: dict[str, list[str]] = {}
        self.transcripts: dict[str, str] = {}
        self.advisor_times: list[datetime] = []
        self.samples: dict[str, dict] = {}


def read_db(
    conn: sqlite3.Connection,
    rig: str,
    since: datetime,
    until: datetime,
    advisor: str,
    unknowns: list[str],
) -> DbView:
    """Read seats, native session ids, transcript paths and advisor-row times from the copy."""
    view = DbView()
    view.available = True
    present = schema_gaps(conn)
    for table, columns in REQUIRED_COLUMNS.items():
        if not present[table]:
            unknowns.append(f"{table} missing")
            continue
        for column in columns:
            if column not in present[table]:
                unknowns.append(f"{table}.{column} missing")
    pattern = like_rig(rig)
    suffix = f"@{rig}"  # LIKE ignores ASCII case in sqlite; the exact suffix decides.
    seats: set[str] = set()
    sessions: dict[str, set[str]] = {}

    usage = present["usage_samples"]
    if {"seat_session", "captured_at"} <= usage:
        view.seat_source = True
        has_native = "native_session_id" in usage
        native = "native_session_id" if has_native else "NULL"
        has_lane = "lane" in usage
        # without a lane column every row is a context row
        lane_column = "lane" if has_lane else "'context'"
        query = (
            f"SELECT seat_session, captured_at, {native}, {lane_column} FROM usage_samples "
            "WHERE seat_session LIKE ? ESCAPE '\\'"
        )
        missing_ids: dict[str, int] = {}
        for seat, captured, native_id, lane in conn.execute(query, (pattern,)):
            when = try_ts(captured)
            if not seat.endswith(suffix) or when is None or not since <= when < until:
                continue
            seats.add(seat)
            counts = view.samples.setdefault(
                seat,
                {
                    "context": 0,
                    "context_without_session": 0 if has_native else None,
                    "provider_window": 0 if has_lane else None,
                },
            )
            if lane != "context":
                # the rate-limit windows: no session and no tokens, so never a gap
                if lane == "provider_window":
                    counts["provider_window"] += 1
                continue
            counts["context"] += 1
            if native_id:
                sessions.setdefault(seat, set()).add(native_id)
            elif has_native:
                counts["context_without_session"] += 1
                missing_ids[seat] = missing_ids.get(seat, 0) + 1
        for seat in sorted(missing_ids):
            unknowns.append(f"{seat}: {missing_ids[seat]} samples without a session id")

    ctx = present["context_usage"]
    if "session_name" in ctx:
        view.seat_source = True
        cols = [c for c in ("session_name", "session_id", "transcript_path") if c in ctx]
        query = f"SELECT {', '.join(cols)} FROM context_usage WHERE session_name LIKE ? ESCAPE '\\'"
        for row in conn.execute(query, (pattern,)):
            record = dict(zip(cols, row))
            seat = record["session_name"]
            if not seat.endswith(suffix):
                continue
            seats.add(seat)
            session_id = record.get("session_id")
            if session_id:
                sessions.setdefault(seat, set()).add(session_id)
                if record.get("transcript_path"):
                    view.transcripts[session_id] = record["transcript_path"]

    occupancy = session_columns(conn)
    if occupancy is not None:
        gaps = [column for column in SESSION_COLUMNS if column not in occupancy]
        unknowns.extend(f"sessions.{column} missing" for column in gaps)
        if not gaps:
            view.seat_source = True
            query = (
                f"SELECT {', '.join(SESSION_COLUMNS)} FROM sessions "
                "WHERE session_name LIKE ? ESCAPE '\\'"
            )
            for seat, kind, token, created, last_seen in conn.execute(query, (pattern,)):
                if not seat.endswith(suffix) or kind != "claude_id":
                    continue
                if not isinstance(token, str) or not token:
                    continue
                # an unreadable end of an occupancy keeps the row: opening one more is harmless
                began, ended = try_ts(created), try_ts(last_seen)
                if (began is not None and began >= until) or (ended is not None and ended < since):
                    continue
                seats.add(seat)
                sessions.setdefault(seat, set()).add(token)

    queue = present["queue_items"]
    if set(REQUIRED_COLUMNS["queue_items"]) <= queue:
        view.queue_ok = True
        query = (
            "SELECT qitem_id, ts_created FROM queue_items "
            "WHERE destination_session = ? AND source_session LIKE ? ESCAPE '\\'"
        )
        for _qitem, created in conn.execute(query, (advisor, pattern)):
            when = try_ts(created)
            if when is not None:
                view.advisor_times.append(when)

    view.seats = sorted(seats)
    view.sessions = {seat: sorted(ids) for seat, ids in sessions.items()}
    return view


# --------------------------------------------------------------------------- JSONL usage


def find_transcripts(
    seat: str, session_id: str, view: DbView, projects: Path, unknowns: list[str]
) -> list[Path]:
    """Return a session's transcript plus its subagent transcripts; a reason when none is found."""
    main: Path | None = None
    recorded = view.transcripts.get(session_id)
    if recorded and Path(recorded).is_file():
        main = Path(recorded)
    else:
        matches = sorted(glob.glob(str(projects / "*" / f"{glob.escape(session_id)}.jsonl")))
        if len(matches) == 1:
            main = Path(matches[0])
        elif not matches:
            unknowns.append(f"{seat}: no transcript for session {session_id}")
        else:
            unknowns.append(f"{seat}: {len(matches)} transcripts for session {session_id}")
    if main is None:
        return []
    subagents = sorted((main.parent / session_id / "subagents").glob("*.jsonl"))
    return [main, *subagents]


def usage_counts(usage: dict) -> dict[str, int]:
    """Return the four token counts of a usage mapping; a missing or bad one is 0."""
    counts = {}
    for key in USAGE_KEYS:
        value = usage.get(key)
        counts[key] = value if isinstance(value, int) and value >= 0 else 0
    return counts


def advisor_steps(usage: dict) -> list[dict]:
    """Return the model and counts of each `advisor_message` entry of `usage.iterations`."""
    steps = usage.get("iterations")
    found = []
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict) and step.get("type") == ADVISOR_STEP:
            model = step.get("model")
            model = model if isinstance(model, str) else None
            found.append({"model": model, **usage_counts(step)})
    return found


def read_usage(path: Path, since: datetime, until: datetime, unknowns: list[str]) -> list[dict]:
    """Return the usage records of one transcript inside [since, until).

    Only assistant lines with a `message.usage` object count, and only the timestamp, request id,
    message id, model id, four token counts, advisor iterations (model and counts) and the
    presence of an `advisorModel` field are kept; nothing else of a line is retained.
    """
    records: list[dict] = []
    bad = 0
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        unknowns.append(f"transcript {path.name} unreadable: {exc.strerror or exc}")
        return records
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            when = try_ts(entry.get("timestamp"))
            if when is None:
                bad += 1
                continue
            if not since <= when < until:
                continue
            counts = usage_counts(usage)
            model = message.get("model")
            extra = {}
            if advisor := advisor_steps(usage):
                extra["advisor"] = advisor
            if "advisorModel" in entry:
                extra["advisor_field"] = True
            records.append(
                {
                    "ts": when,
                    "key": (message.get("id"), entry.get("requestId")),
                    "model": model if isinstance(model, str) else None,
                    "context_missing": [
                        key
                        for key in CONTEXT_KEYS
                        if not (isinstance(usage.get(key), int) and usage.get(key) >= 0)
                    ],
                    **counts,
                    **extra,
                }
            )
    if bad:
        unknowns.append(f"transcript {path.name}: {bad} unparsable lines skipped")
    return records


def dedupe(records: list[dict]) -> list[dict]:
    """Keep one record per (message.id, requestId); records with neither id are all kept.

    A message's advisor iterations may be on a later line than its first: the kept record takes
    the longest list, and the `advisorModel` mark of any of its lines.
    """
    seen: dict[tuple, int] = {}
    kept: list[dict] = []
    for record in sorted(records, key=lambda r: r["ts"]):
        key = record["key"]
        if key != (None, None):
            if key in seen:
                first = dict(kept[seen[key]])
                if len(record.get("advisor", [])) > len(first.get("advisor", [])):
                    first["advisor"] = record["advisor"]
                if record.get("advisor_field"):
                    first["advisor_field"] = True
                kept[seen[key]] = first
                continue
            seen[key] = len(kept)
        kept.append(record)
    return kept


def agent_meta(transcript: Path) -> dict | None:
    """Return {agent_type, agent_model} from a subagent transcript's meta file, or None."""
    try:
        meta = json.loads(
            transcript.with_name(f"{transcript.stem}.meta.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("agentType"), str):
        return None
    model = meta.get("model")
    return {
        "agent_type": meta["agentType"],
        "agent_model": model if isinstance(model, str) else None,
    }


def turn_context(record: dict) -> int:
    """Return one assistant record's resident context: fresh input + cache read + cache write."""
    return sum(record[key] for key in CONTEXT_KEYS)


def occupant_context(
    seat: str, session_id: str, main_records: list[dict], unknowns: list[str]
) -> tuple[dict | None, list[list]]:
    """Return one occupant's context summary (None without a turn) and its [ts, context] series.

    main_records are the session's MAIN transcript records only; they are de-duplicated here.
    """
    records = dedupe(main_records)
    if not records:
        return None, []
    missing = sum(1 for r in records if r.get("context_missing"))
    if missing:
        unknowns.append(
            f"{seat}: {missing} turns of session {session_id} lack a context count (taken as 0)"
        )
    series = [[iso(r["ts"]), turn_context(r)] for r in records]
    summary_ = {
        "session_id": session_id,
        "turns": len(records),
        "peak": max(c for _ts, c in series),
        "first_ts": series[0][0],
        "last_ts": series[-1][0],
    }
    return summary_, series


# --------------------------------------------------------------------------- git


def git(repo: Path, *args: str) -> str:
    """Run a read-only git command in repo and return stdout.

    Raises:
        subprocess.CalledProcessError: when git fails.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class Boundary:
    """One boundary commit: a slice's commit or a mission's `resolve:` compile."""

    def __init__(self, sha: str, when: datetime, slice_id: str, token: str) -> None:
        """Hold the commit's sha, committer time, the slice id and the subject's first token."""
        self.sha = sha
        self.when = when
        self.slice_id = slice_id
        self.token = token


def read_boundaries(
    repo: Path, since: datetime, until: datetime, unknowns: list[str]
) -> tuple[list[Boundary], str]:
    """Return the boundary commits in [since, until] (oldest first) and the sources.git string."""
    fmt = "%H%x1f%cI%x1f%s%x1f%(trailers:key=HACF-Slice,valueonly,separator=%x1d)%x1e"
    try:
        head = git(repo, "rev-parse", "HEAD").strip()
        out = git(repo, "log", f"--since={iso(since)}", f"--until={iso(until)}", f"--format={fmt}")
    except (OSError, subprocess.CalledProcessError):
        unknowns.append("git: log unavailable; no slice boundaries")
        return [], UNKNOWN
    boundaries = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, committed, subject, trailer = (chunk.split("\x1f") + ["", "", ""])[:4]
        when = try_ts(committed)
        if when is None or not since <= when <= until:
            continue
        token = subject.split(" ", 1)[0] if subject else ""
        slice_values = [v.strip() for v in trailer.split("\x1d") if v.strip()]
        if slice_values:
            boundaries.append(Boundary(sha, when, slice_values[0], token))
        elif match := SUBJECT_SLICE_RE.match(subject):
            boundaries.append(Boundary(sha, when, match.group(1), token))
        elif match := SUBJECT_RESOLVE_RE.match(subject):
            boundaries.append(Boundary(sha, when, f"{match.group(1)}/resolve", token))
    boundaries.sort(key=lambda b: b.when)
    return boundaries, f"{iso(since)}..{iso(until)} HEAD={head}"


def names_slice(text: str, slice_id: str) -> bool:
    """Tell whether text names slice_id as a whole id (OPR.1.2 does not name OPR.1.20)."""
    return re.search(rf"(?<![\w.]){re.escape(slice_id)}(?!\.?\d)", text) is not None


def git_escapes(repo: Path, boundary_sha: str, slice_id: str) -> int:
    """Count commits after the boundary up to HEAD that repin, block-change or replan the slice."""
    fmt = (
        "%s%x1f%(trailers:key=HACF-Repin,valueonly,separator=%x1d)"
        "%x1f%(trailers:key=HACF-Block-Changed,valueonly,separator=%x1d)"
        "%x1f%(trailers:key=HACF-Replan-Of,valueonly,separator=%x1d)%x1e"
    )
    out = git(repo, "log", f"{boundary_sha}..HEAD", f"--format={fmt}")
    count = 0
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        subject, repin, block, replan_of = (chunk.split("\x1f") + ["", "", ""])[:4]
        trailers = f"{repin}\x1d{block}\x1d{replan_of}"
        replan = "resolve: replan" in subject and names_slice(subject, slice_id)
        if names_slice(trailers, slice_id) or replan:
            count += 1
    return count


def progress_events(repo: Path, slice_id: str) -> tuple[int, int, bool] | None:
    """Count the `audit_failed` and `blocked` Outcomes lines of the slice's PROGRESS.md.

    The third value tells whether an `executing` line carries the marker, as the text
    `escape_tracking: v1` or as JSON `"escape_tracking":"v1"`.
    None when the slice or its PROGRESS.md is not found.
    """
    for spec in sorted((repo / "missions").glob("*/slices/*/SPEC.md")):
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        front = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        match = FRONTMATTER_ID_RE.search(front)
        if not match or match.group(1) != slice_id:
            continue
        progress = spec.parent / "PROGRESS.md"
        if not progress.is_file():
            return None
        text = progress.read_text(encoding="utf-8")
        tracked = TRACKING_MARKER_RE.search(text) is not None
        return len(AUDIT_FAILED_RE.findall(text)), len(BLOCKED_RE.findall(text)), tracked
    return None


def progress_escapes(repo: Path, slice_id: str) -> int | None:
    """Count `audit_failed` Outcomes lines of the slice's PROGRESS.md; None when there is none."""
    events = progress_events(repo, slice_id)
    return None if events is None else events[0]


def loop_fails(loops_dir: Path, slice_id: str, unknowns: list[str]) -> int | None:
    """Return the audit FAILs a loop record shows (Iteration - 1 after a PASS); None without one."""
    path = loops_dir / f"hacf-loop-{slice_id}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        unknowns.append(f"{slice_id}: loop record unreadable: {exc.strerror or exc}")
        return None
    iteration = LOOP_ITERATION_RE.search(text)
    verdict_ = LOOP_VERDICT_RE.search(text)
    if not iteration or not verdict_:
        unknowns.append(f"{slice_id}: loop record without Iteration or Latest verdict")
        return None
    count = int(iteration.group(1))
    return max(count - 1, 0) if verdict_.group(1).upper() == "PASS" else count


# --------------------------------------------------------------------------- prices (H5)


def _rate(value: object, where: str) -> float:
    """Return one rate: a non-negative number, or {usd: <number>, derived: <rule>}.

    Raises:
        UsageError: naming the key when the rate is malformed.
    """
    if isinstance(value, dict):
        if not isinstance(value.get("derived"), str) or not value["derived"]:
            raise UsageError(f"malformed --prices: {where} needs a derived: rule")
        value = value.get("usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise UsageError(f"malformed --prices: {where} is not a non-negative number")
    return float(value)


def load_prices(path: Path) -> dict[str, dict]:
    """Read and validate a `hacf.prices/v1` table; return {model: {rates, long_prompt}}.

    Raises:
        UsageError: with one line naming the bad key when the file is missing or malformed.
    """
    try:
        import yaml  # dev tooling dependency (pyproject dev group); only --prices needs it

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ImportError, ValueError) as exc:
        raise UsageError(f"cannot read --prices {path}: {exc}") from None
    except Exception as exc:  # yaml.YAMLError
        raise UsageError(f"malformed --prices: not valid YAML ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise UsageError("malformed --prices: top level is not a mapping")
    if data.get("schema") != PRICES_SCHEMA:
        raise UsageError(f"malformed --prices: schema must be {PRICES_SCHEMA}")
    for key, want in (("currency", "USD"), ("unit", "per_million_tokens")):
        if data.get(key) != want:
            raise UsageError(f"malformed --prices: {key} must be {want}")
    for key in ("source", "retrieved"):
        if not data.get(key):
            raise UsageError(f"malformed --prices: {key} is missing")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise UsageError("malformed --prices: models is missing")
    table: dict[str, dict] = {}
    for model, entry in models.items():
        if not isinstance(entry, dict):
            raise UsageError(f"malformed --prices: models.{model} is not a mapping")
        rates = {k: _rate(entry.get(k), f"models.{model}.{k}") for k in RATE_KEYS}
        long_prompt = None
        raw = entry.get("long_prompt")
        if raw is not None:
            threshold = raw.get("threshold_tokens") if isinstance(raw, dict) else None
            if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
                raise UsageError(f"malformed --prices: models.{model}.long_prompt.threshold_tokens")
            long_prompt = {"threshold_tokens": threshold}
            for k in RATE_KEYS:
                if k in raw:
                    long_prompt[k] = _rate(raw[k], f"models.{model}.long_prompt.{k}")
                elif k in ("fresh_in", "output"):
                    raise UsageError(f"malformed --prices: models.{model}.long_prompt.{k}")
                else:
                    long_prompt[k] = rates[k]
        table[str(model)] = {"rates": rates, "long_prompt": long_prompt}
    return table


def zero_token(record: dict) -> bool:
    """Tell whether a record's four priced counts are all 0, so it costs $0 at every rate."""
    return not any(record[key] for key in USAGE_KEYS)


def record_usd(record: dict, model_prices: dict) -> float:
    """Return one record's dollars at a model's rates (long-prompt rates above the threshold)."""
    rates = model_prices["rates"]
    long_prompt = model_prices["long_prompt"]
    if long_prompt is not None and turn_context(record) > long_prompt["threshold_tokens"]:
        rates = long_prompt
    return (
        record["input_tokens"] * rates["fresh_in"]
        + record["cache_creation_input_tokens"] * rates["cache_write"]
        + record["cache_read_input_tokens"] * rates["cache_read"]
        + record["output_tokens"] * rates["output"]
    ) / 1_000_000


# --------------------------------------------------------------------------- runner inputs


def read_input(flag: str, path: Path) -> object:
    """Return a runner input file's JSON.

    Raises:
        UsageError: naming the flag when the file cannot be read or is not JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read {flag} {path}: {type(exc).__name__}") from None


def load_configured(path: Path) -> set[str]:
    """Return the session ids of --advisor-configured-json: a list, or {"sessions": [...]}.

    Raises:
        UsageError: when the file is not one of the two shapes.
    """
    data = read_input("--advisor-configured-json", path)
    if isinstance(data, dict):
        data = data.get("sessions")
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise UsageError("malformed --advisor-configured-json: want a list of session ids")
    return set(data)


def load_client_costs(path: Path) -> dict[str, tuple[str, float]]:
    """Return {session id: (seat, highest costUsd)} from saved `rig usage cost --json` answers.

    The file holds one answer ({"seats": [...]}) or a list of them. A row without a number is
    left out, so its session reads `unknown`.

    Raises:
        UsageError: when the file holds neither shape.
    """
    data = read_input("--client-cost-json", path)
    answers = data if isinstance(data, list) else [data]
    if not all(isinstance(a, dict) and isinstance(a.get("seats"), list) for a in answers):
        raise UsageError('malformed --client-cost-json: want {"seats": [...]} answers')
    costs: dict[str, tuple[str, float]] = {}
    for row in (row for answer in answers for row in answer["seats"]):
        if not isinstance(row, dict):
            continue
        value, window = row.get("costUsd"), row.get("window")
        session = window.get("nativeSessionId") if isinstance(window, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(session, str) and value >= costs.get(session, ("", 0.0))[1]:
            costs[session] = (str(row.get("seatSession")), float(value))
    return costs


def client_cost_entry(
    seat: str, session_id: str, records: list[dict], costs: dict | None, prices: dict | None
) -> dict:
    """Return one session's client figure, priced figure and their difference, an estimate."""
    client = costs.get(session_id, (None, None))[1] if costs is not None else None
    priced = priced_sum(records, prices)[0] if prices is not None and records else None
    known = client is not None and priced is not None
    return {
        "seat": seat,
        "client_usd": UNKNOWN if client is None else client,
        "priced_usd": UNKNOWN if priced is None else priced,
        "advisor_residual_usd": round(client - priced, 6) if known else UNKNOWN,
        "label": "estimate",
    }


# --------------------------------------------------------------------------- scorecard


def seat_numbers(
    records: list[dict],
    prices: dict[str, dict] | None = None,
    reasons: list[str] | None = None,
    where: str = "",
) -> dict:
    """Return the per-seat, per-slice numbers of Mini-requirement 6 for a list of records.

    The H5 keys (`models`, `mixed`, `usage_by_model`, and `usd` with a price table) come last;
    a record with no model id is counted under the model "unknown". A zero-token record (such as
    Claude Code's `<synthetic>` limit messages) adds $0 to `usd` and needs no price; any other
    record whose model has no price makes `usd` None.
    """
    fresh = sum(r["input_tokens"] for r in records)
    cache_read = sum(r["cache_read_input_tokens"] for r in records)
    cache_write = sum(r["cache_creation_input_tokens"] for r in records)
    output = sum(r["output_tokens"] for r in records)
    times = [r["ts"] for r in records]
    entry = {
        "fresh_in": fresh,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "output": output,
        "total": fresh + cache_read + cache_write + output,
        "cost_weighted": cost_weighted(fresh, cache_write, cache_read, output),
        "turns": len(records),
        "max_context": max(
            r["input_tokens"] + r["cache_read_input_tokens"] + r["cache_creation_input_tokens"]
            for r in records
        ),
        "wall_s": round((max(times) - min(times)).total_seconds(), 3),
    }
    by_model: dict[str, dict] = {}
    for r in records:
        slot = by_model.setdefault(
            r["model"] or UNKNOWN, {"fresh_in": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        )
        slot["fresh_in"] += r["input_tokens"]
        slot["cache_write"] += r["cache_creation_input_tokens"]
        slot["cache_read"] += r["cache_read_input_tokens"]
        slot["output"] += r["output_tokens"]
    models = sorted({r["model"] for r in records if r["model"]})
    entry["models"] = models
    entry["mixed"] = len(models) > 1
    entry["usage_by_model"] = dict(sorted(by_model.items()))
    if prices is not None:
        priced = [r for r in records if not zero_token(r)]
        missing = sorted({(r["model"] or UNKNOWN) for r in priced} - set(prices))
        if missing:
            entry["usd"] = None
            if reasons is not None:
                reasons.append(f"{where}: usd unknown: no price for {', '.join(missing)}")
        else:
            entry["usd"] = round(
                sum((record_usd(r, prices[r["model"] or UNKNOWN]) for r in priced), 0.0), 6
            )
    entry.update(advisor_columns(records, prices, reasons, where))
    entry.update(subagent_columns(records, prices))
    return entry


def priced_sum(records: list[dict], prices: dict[str, dict]) -> tuple[float | None, list[str]]:
    """Return the dollars of records at their own models' rates, else None and the models."""
    priced = [r for r in records if not zero_token(r)]
    missing = sorted({(r["model"] or UNKNOWN) for r in priced} - set(prices))
    if missing:
        return None, missing
    usd = sum((record_usd(r, prices[r["model"] or UNKNOWN]) for r in priced), 0.0)
    return round(usd, 6), []


def split_sums(prefix: str, records: list[dict]) -> dict:
    """Return the `<prefix>_*` token columns and `<prefix>_tokens` of a list of records."""
    sums = {
        f"{prefix}_{column}": sum(r[key] for r in records) for column, key in SPLIT_COLUMNS.items()
    }
    sums[f"{prefix}_tokens"] = sum(sums.values())
    return sums


def advisor_columns(
    records: list[dict], prices: dict[str, dict] | None, reasons: list[str] | None, where: str
) -> dict:
    """Return the advisor_* columns of an entry, or nothing without any advisor evidence.

    A record of a session the runner configured, which has no advisor iteration anywhere, makes
    the token columns `unknown` and advisor_usd null.
    """
    steps = [step for r in records for step in r.get("advisor", [])]
    fields = sum(1 for r in records if r.get("advisor_field"))
    unread = any(r.get("advisor_unread") for r in records)
    if not (steps or fields or unread):
        return {}
    columns = split_sums("advisor", steps)
    if unread:
        columns = {name: UNKNOWN for name in columns}
    columns["advisor_model_field_records"] = fields
    if prices is not None:
        usd, missing = (None, []) if unread else priced_sum(steps, prices)
        columns["advisor_usd"] = usd
        if reasons is not None and (missing or unread):
            why = f"no price for {', '.join(missing)}" if missing else "no advisor iterations"
            reasons.append(f"{where}: advisor usd unknown: {why}")
    return columns


def subagent_columns(records: list[dict], prices: dict[str, dict] | None) -> dict:
    """Return `subagents_by_type` and the `helper_*` columns, or nothing without a meta file."""
    by_type: dict[str, list[dict]] = {}
    for record in records:
        if "agent_type" in record:
            by_type.setdefault(record["agent_type"], []).append(record)
    if not by_type:
        return {}
    split = {}
    for kind, found in sorted(by_type.items()):
        slot = split_sums("sum", found)
        slot = {name.removeprefix("sum_"): value for name, value in slot.items()}
        slot["turns"] = len(found)
        slot["models"] = sorted({r["model"] for r in found if r["model"]})
        slot["meta_models"] = sorted({r["agent_model"] for r in found if r.get("agent_model")})
        if prices is not None:
            slot["usd"] = priced_sum(found, prices)[0]
        split[kind] = slot
    columns: dict = {"subagents_by_type": split}
    if HELPER_TYPE in by_type:
        helper = split[HELPER_TYPE]
        columns.update(split_sums("helper", by_type[HELPER_TYPE]))
        columns["helper_models"] = helper["models"]
        if prices is not None:
            columns["helper_usd"] = helper["usd"]
    return columns


def cost_weighted(fresh: int, cache_write: int, cache_read: int, output: int) -> float | int:
    """Return the §8 cost-weighted total, as an int when it is whole."""
    value = (
        fresh * WEIGHTS["fresh_in"]
        + cache_write * WEIGHTS["cache_write"]
        + cache_read * WEIGHTS["cache_read"]
        + output * WEIGHTS["output"]
    )
    value = round(value, 4)
    return int(value) if value == int(value) else value


def attribute(when: datetime, boundaries: list[Boundary], until: datetime) -> int | None:
    """Return the index of the first boundary at or after `when` (and <= until), else None."""
    for index, boundary in enumerate(boundaries):
        if boundary.when >= when and boundary.when <= until:
            return index
    return None


def build_scorecard(
    rig: str,
    since: datetime,
    until: datetime,
    label: str | None,
    db: Path,
    projects: Path,
    repo: Path,
    advisor: str,
    out_dir: Path,
    loops_dir: Path | None = None,
    prices: dict[str, dict] | None = None,
    configured: set[str] | None = None,
    client_costs: dict | None = None,
) -> tuple[dict, dict]:
    """Build one hacf.telemetry/v1 scorecard and the side facts the B0/B1 aggregate needs.

    The snapshot dir is created under out_dir and removed by its exact path on every exit path.
    """
    unknowns: list[str] = []
    ensure_private_dir(out_dir)
    snap_dir = out_dir / f"snapshot-{stamp()}-{os.getpid()}"
    view = DbView()
    snapshot_state = UNKNOWN
    try:
        snap_dir.mkdir(mode=0o700)
        conn = take_snapshot(db, snap_dir, unknowns)
        if conn is not None:
            try:
                snapshot_state = "ok"
                view = read_db(conn, rig, since, until, advisor, unknowns)
            finally:
                conn.close()
    finally:
        if snap_dir.exists():
            shutil.rmtree(snap_dir)

    boundaries, git_range = read_boundaries(repo, since, until, unknowns)

    files_read = 0
    sessions_read: set[str] = set()
    per_seat: dict[str, list[dict]] = {}
    sessions_used: dict[str, set[str]] = {}
    models: dict[str, set[str]] = {}
    context_out: dict[str, dict] = {}
    context_series: dict[str, dict[str, list[list]]] = {}
    session_costs: dict[str, dict] = {}
    advised = False  # any advisor iteration or configured session in the window
    for seat in view.seats:
        records: list[dict] = []
        occupants = []
        for session_id in view.sessions.get(seat, []):
            found = []
            main_records: list[dict] = []
            paths = find_transcripts(seat, session_id, view, projects, unknowns)
            if paths:
                sessions_read.add(session_id)
            for index, path in enumerate(paths):
                files_read += 1
                read = read_usage(path, since, until, unknowns)
                if index == 0:  # find_transcripts lists the main transcript first
                    main_records = read
                elif meta := agent_meta(path):
                    read = [{**record, **meta} for record in read]
                found.extend(read)
            has_steps = any(r.get("advisor") for r in found)
            if configured and session_id in configured:
                advised = True
                if not has_steps:
                    found = [{**record, "advisor_unread": True} for record in found]
            advised = advised or has_steps
            session_costs[session_id] = client_cost_entry(
                seat, session_id, dedupe(found), client_costs, prices
            )
            if found:
                sessions_used.setdefault(seat, set()).add(session_id)
            records.extend(found)
            occupant, series = occupant_context(seat, session_id, main_records, unknowns)
            if occupant is not None:
                occupants.append(occupant)
                context_series.setdefault(seat, {})[session_id] = series
        occupants.sort(key=lambda o: (o["first_ts"], o["session_id"]))
        context_out[seat] = {
            "occupants": occupants,
            "peak_max": max((o["peak"] for o in occupants), default=None),
            "turns": sum(o["turns"] for o in occupants),
        }
        records = dedupe(records)
        per_seat[seat] = records
        models[seat] = {r["model"] for r in records if r["model"]}

    order = []
    for boundary in boundaries:
        if boundary.slice_id not in order:
            order.append(boundary.slice_id)
    buckets: dict[str, dict[str, list[dict]]] = {}
    for seat, records in per_seat.items():
        for record in records:
            index = attribute(record["ts"], boundaries, until)
            slice_id = boundaries[index].slice_id if index is not None else UNATTRIBUTED
            buckets.setdefault(seat, {}).setdefault(slice_id, []).append(record)

    advisor_counts: dict[str, int] = {}
    for when in view.advisor_times:
        if not since <= when <= until:
            continue
        index = attribute(when, boundaries, until)
        slice_id = boundaries[index].slice_id if index is not None else UNATTRIBUTED
        advisor_counts[slice_id] = advisor_counts.get(slice_id, 0) + 1

    has_unattributed = any(UNATTRIBUTED in b for b in buckets.values()) or (
        UNATTRIBUTED in advisor_counts
    )
    slice_ids = order + ([UNATTRIBUTED] if has_unattributed else [])

    seats_out = []
    slice_extra: dict[str, dict] = {}
    for seat in view.seats:
        seat_slices = []
        for slice_id in slice_ids:
            records = buckets.get(seat, {}).get(slice_id)
            if records:
                entry = seat_numbers(records, prices, unknowns, f"{seat}: {slice_id}")
                seat_slices.append({"slice_id": slice_id, **entry})
                extra = slice_extra.setdefault(slice_id, {"mixed": False, "usd": 0.0})
                extra["mixed"] = extra["mixed"] or entry["mixed"]
                if prices is not None:
                    extra["usd"] = (
                        None
                        if extra["usd"] is None or entry["usd"] is None
                        else extra["usd"] + entry["usd"]
                    )
        seats_out.append({"seat": seat, "slices": seat_slices})

    first_boundary = {}
    for boundary in boundaries:
        first_boundary.setdefault(boundary.slice_id, boundary)
    source_known = view.available and view.seat_source
    # no record at all is a gap in the sources, never a measured 0
    tokens_known = source_known and any(per_seat.values())
    slices_out = []
    for slice_id in slice_ids:
        total = 0
        weighted = 0.0
        for seat in seats_out:
            for entry in seat["slices"]:
                if entry["slice_id"] == slice_id:
                    total += entry["total"]
                    weighted += entry["cost_weighted"]
        escapes = 0
        parts: dict = {"audit_failed": 0, "blocked": 0, "git": 0}
        sources: dict = {"audit_failed": None, "blocked": None, "git": None}
        if slice_id != UNATTRIBUTED and not slice_id.endswith("/resolve"):
            parts, sources = slice_escape_parts(
                repo, slice_id, first_boundary[slice_id].sha, unknowns, loops_dir
            )
            escapes = known_sum(parts)
        if view.queue_ok:
            advisor_rows: int | str = advisor_counts.get(slice_id, 0)
        else:
            advisor_rows = UNKNOWN
        slices_out.append(
            {
                "slice_id": slice_id,
                "total": total if tokens_known else UNKNOWN,
                "cost_weighted": round_num(weighted) if tokens_known else UNKNOWN,
                "escapes": escapes,
                "advisor_rows": advisor_rows,
                "escape_parts": parts,
                "escape_sources": sources,
            }
        )
    if not source_known:
        unknowns.append("token totals unknown: no seat or session source in the database")
    elif not tokens_known:
        unknowns.append(
            "token totals unknown: no transcript record in the window "
            f"({len(sessions_read)} sessions read)"
        )
    if not view.queue_ok:
        unknowns.append("advisor_rows unknown: queue_items unavailable")

    scorecard: dict = {
        "schema": SCHEMA,
        "rig": rig,
        "window": {"since": iso(since), "until": iso(until)},
        "generated_at": iso(datetime.now(timezone.utc).replace(microsecond=0)),
        "sources": {"snapshot": snapshot_state, "jsonl": files_read, "git": git_range},
        "seats": seats_out,
        "slices": slices_out,
        "unknowns": unknowns,
    }
    if client_costs is not None or advised:  # v0.6.8, additive
        for session_id, (seat, _usd) in (client_costs or {}).items():
            if session_id not in session_costs and seat.endswith(f"@{rig}"):
                session_costs[session_id] = client_cost_entry(
                    seat, session_id, [], client_costs, prices
                )
        scorecard["client_cost"] = dict(sorted(session_costs.items()))
    if label:
        scorecard["label"] = label
    scorecard["context"] = context_out  # v0.6.4, additive: after every v0.6.3 key
    scorecard["samples"] = dict(sorted(view.samples.items()))  # v0.6.7, additive
    side = {
        "subject_token": {b.slice_id: b.token for b in boundaries},
        "models": models,
        "sessions": sessions_used,
        "context_series": context_series,
        "slice_extra": slice_extra,
        "boundary_shas": {sid: [b.sha for b in boundaries if b.slice_id == sid] for sid in order},
    }
    return scorecard, side


def round_num(value: float) -> float | int:
    """Round to 4 places and drop a zero fraction."""
    value = round(value, 4)
    return int(value) if value == int(value) else value


def slice_escape_parts(
    repo: Path,
    slice_id: str,
    boundary_sha: str,
    unknowns: list[str],
    loops_dir: Path | None = None,
) -> tuple[dict, dict]:
    """Return a slice's escape parts (audit_failed, blocked, git; None when unknown) and sources.

    audit_failed comes from PROGRESS.md, or from the loop record under loops_dir when PROGRESS.md
    has no audit_failed line; blocked is unknown when PROGRESS.md has neither line and no marker
    (`escape_tracking: v1`, or JSON `"escape_tracking":"v1"`) on an `executing` line (ruling P22).
    """
    parts: dict = {"audit_failed": None, "blocked": None, "git": None}
    sources: dict = {"audit_failed": None, "blocked": None, "git": None}
    try:
        parts["git"] = git_escapes(repo, boundary_sha, slice_id)
        sources["git"] = "git"
    except (OSError, subprocess.CalledProcessError):
        unknowns.append(f"{slice_id}: escapes from git unavailable")
    events = progress_events(repo, slice_id)
    if events is None:
        unknowns.append(f"{slice_id}: no PROGRESS.md; escapes count git only")
        return parts, sources
    failed, blocked, tracked = events
    parts["audit_failed"], sources["audit_failed"] = failed, "progress"
    if failed == 0 and loops_dir is not None:
        from_loops = loop_fails(loops_dir, slice_id, unknowns)
        if from_loops is not None:
            parts["audit_failed"], sources["audit_failed"] = from_loops, "loops"
    if failed == 0 and blocked == 0 and not tracked:
        unknowns.append(f"{slice_id}: blocked unknown ({NO_HALT_RECORDS})")
    else:
        parts["blocked"], sources["blocked"] = blocked, "progress"
    return parts, sources


def known_sum(parts: dict) -> int:
    """Return the sum of an escape_parts dict's known (non-null) parts."""
    return sum(v for v in parts.values() if v is not None)


def slice_escapes(
    repo: Path,
    slice_id: str,
    boundary_sha: str,
    unknowns: list[str],
    loops_dir: Path | None = None,
) -> int:
    """Return a slice's escapes: audit_failed lines + blocked lines + git escapes (MR 4).

    The known-part sum of slice_escape_parts, the same number the scorecard's `escapes` holds;
    a null part (P22: no marker and no lines) adds nothing and is a reason in unknowns.
    """
    parts, _sources = slice_escape_parts(repo, slice_id, boundary_sha, unknowns, loops_dir)
    return known_sum(parts)


# --------------------------------------------------------------------------- §8 aggregate (F16)


def summary(values: list[float]) -> dict:
    """Return n, median, min and max of a list (None for an empty list's statistics)."""
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": round_num(statistics.median(values)),
        "min": round_num(min(values)),
        "max": round_num(max(values)),
    }


def guard_restarts(rig: str, windows: list[tuple[str, datetime, datetime]]) -> int | str:
    """Count the guard log's `action=restart` lines inside any window; unknown when absent."""
    log = Path.home() / ".hacf" / rig / "context-guard.log"
    if not log.is_file():
        return UNKNOWN
    count = 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = GUARD_LINE_RE.match(line)
        when = try_ts(match.group(1)) if match else None
        if when is not None and any(since <= when < until for _n, since, until in windows):
            count += 1
    return count


def slice_size(repo: Path | None, shas: list[str]) -> tuple[dict | None, str | None]:
    """Return a slice's {files, lines} from `git show --numstat` of its commits, or a reason.

    files counts distinct paths; lines is added + deleted (a binary file adds 0 lines).
    """
    if repo is None or not shas:
        return None, "no slice commits to read"
    paths: set[str] = set()
    lines = 0
    for sha in shas:
        try:
            out = git(repo, "show", "--numstat", "--format=", sha)
        except (OSError, subprocess.CalledProcessError):
            return None, f"git show failed for {sha[:12]}"
        for row in out.splitlines():
            added, _tab, rest = row.partition("\t")
            deleted, _tab, path = rest.partition("\t")
            if not path:
                continue
            paths.add(path)
            lines += (int(added) if added.isdigit() else 0) + (
                int(deleted) if deleted.isdigit() else 0
            )
    return {"files": len(paths), "lines": lines}, None


def slice_row(sid: str, s: dict, priced: bool, size: dict | None) -> dict:
    """Return one aggregate slice row; `usd` only with a price table, `size` always."""
    row = {
        "slice_id": sid,
        "total": round_num(s["total"]),
        "cost_weighted": round_num(s["cost_weighted"]),
        "escapes": s["escapes"],
        "mixed": s["mixed"],
        "size": size,
    }
    if priced:
        row["usd"] = None if s["usd"] is None else round(s["usd"], 6)
    return row


def usd_summary(slices: dict[str, dict], priced: bool) -> dict:
    """Return {usd_per_slice: {n, median, mean, unknown}} over the priced slices, or nothing."""
    if not priced:
        return {}
    values = [s["usd"] for s in slices.values() if s["usd"] is not None]
    unknown = len(slices) - len(values)
    if not values:
        return {"usd_per_slice": {"n": 0, "median": None, "mean": None, "unknown": unknown}}
    return {
        "usd_per_slice": {
            "n": len(values),
            "median": round(statistics.median(values), 6),
            "mean": round(statistics.mean(values), 6),
            "unknown": unknown,
        }
    }


def aggregate(
    kind: str,
    rig: str,
    windows: list[tuple[str, datetime, datetime]],
    cards: list[tuple[dict, dict, str]],
    repo: Path | None = None,
    priced: bool = False,
) -> dict:
    """Build the §8 aggregate (B0 or B1) over a run's scorecards.

    Each `<token>/resolve` pseudo-slice is split evenly over the slices whose boundary subject
    starts with the same first token; with none, it is kept as unattributed.
    """
    unknowns: list[str] = []
    slices: dict[str, dict] = {}
    tokens_of: dict[str, str] = {}
    resolves: list[tuple[str, dict]] = []
    unattributed = {"total": 0, "cost_weighted": 0.0}
    escapes_unknown = False
    shas: dict[str, list[str]] = {}
    resolve_side: dict[str, dict] = {}

    def new_slot() -> dict:
        return {"total": 0, "cost_weighted": 0.0, "escapes": 0, "mixed": False, "usd": 0.0}

    def add_extra(slot: dict, side: dict, sid: str, share: int) -> None:
        extra = side.get("slice_extra", {}).get(sid, {})
        slot["mixed"] = slot["mixed"] or bool(extra.get("mixed"))
        if priced:
            usd = extra.get("usd")
            slot["usd"] = None if slot["usd"] is None or usd is None else slot["usd"] + usd / share

    for card, side, _name in cards:
        tokens_of.update(side["subject_token"])
        for sid, found in side.get("boundary_shas", {}).items():
            known = shas.setdefault(sid, [])
            known.extend(sha for sha in found if sha not in known)
        for entry in card["slices"]:
            sid = entry["slice_id"]
            if entry["total"] == UNKNOWN:
                unknowns.append(f"{card.get('label', 'window')}: {sid} tokens unknown")
                continue
            if sid == UNATTRIBUTED:
                unattributed["total"] += entry["total"]
                unattributed["cost_weighted"] += entry["cost_weighted"]
            elif sid.endswith("/resolve"):
                resolves.append((sid, entry))
                resolve_side[sid] = side
            else:
                slot = slices.setdefault(sid, new_slot())
                slot["total"] += entry["total"]
                slot["cost_weighted"] += entry["cost_weighted"]
                add_extra(slot, side, sid, 1)
                slot["escapes"] = max(slot["escapes"], entry["escapes"])
                parts = entry.get("escape_parts")
                if isinstance(parts, dict) and any(v is None for v in parts.values()):
                    escapes_unknown = True
    for sid, entry in resolves:
        token = sid[: -len("/resolve")]
        targets = [s for s in slices if tokens_of.get(s) == token]
        if not targets:
            unattributed["total"] += entry["total"]
            unattributed["cost_weighted"] += entry["cost_weighted"]
            unknowns.append(f"{sid}: no slice with subject token {token}; kept as unattributed")
            continue
        for target in targets:
            slices[target]["total"] += entry["total"] / len(targets)
            slices[target]["cost_weighted"] += entry["cost_weighted"] / len(targets)
            add_extra(slices[target], resolve_side[sid], sid, len(targets))

    def size_of(sid: str) -> dict | None:
        size, reason = slice_size(repo, shas.get(sid, []))
        if reason:
            unknowns.append(f"{sid}: size unknown: {reason}")
        return size

    totals = [s["total"] for s in slices.values()]
    weighted = [s["cost_weighted"] for s in slices.values()]
    escapes = [s["escapes"] for s in slices.values()]
    models: dict[str, set[str]] = {}
    sessions: dict[str, set[str]] = {}
    for _card, side, _name in cards:
        for seat, ids in side["models"].items():
            models.setdefault(seat, set()).update(ids)
        for seat, ids in side["sessions"].items():
            sessions.setdefault(seat, set()).update(ids)
    for card, _side, _name in cards:
        unknowns.extend(f"{card.get('label', 'window')}: {r}" for r in card["unknowns"])
    return {
        "schema": AGGREGATE_SCHEMA,
        "kind": kind,
        "rig": rig,
        "generated_at": iso(datetime.now(timezone.utc).replace(microsecond=0)),
        "windows": [
            {"name": name, "since": iso(since), "until": iso(until), "scorecard": file}
            for (name, since, until), (_c, _s, file) in zip(windows, cards)
        ],
        "total": summary(totals),
        "cost_weighted": summary(weighted),
        "escapes_per_slice": round_num(statistics.mean(escapes)) if escapes else None,
        **usd_summary(slices, priced),
        "escapes_unknown": escapes_unknown,
        "slices": [slice_row(sid, s, priced, size_of(sid)) for sid, s in slices.items()],
        "unattributed": {
            "total": round_num(unattributed["total"]),
            "cost_weighted": round_num(unattributed["cost_weighted"]),
        },
        "confounds": {
            "models": {seat: sorted(ids) for seat, ids in sorted(models.items())},
            "sessions": {seat: len(ids) for seat, ids in sorted(sessions.items())},
            "guard_restarts": guard_restarts(rig, windows),
            "windows": [name for name, _s, _u in windows],
        },
        "unknowns": unknowns,
    }


def verdict(b0: dict, b1: dict) -> dict:
    """Return the §8 verdict of B1 against B0: met, not met or unknown (directional only)."""
    details = {}
    for metric in ("total", "cost_weighted"):
        before, after = b0[metric]["median"], b1[metric]["median"]
        change = None
        if before and after is not None:
            change = round_num((after - before) / before)
        details[metric] = {"b0_median": before, "b1_median": after, "change": change}
    details["escapes_per_slice"] = {
        "b0": b0["escapes_per_slice"],
        "b1": b1["escapes_per_slice"],
    }
    small = min(
        b0["total"]["n"], b0["cost_weighted"]["n"], b1["total"]["n"], b1["cost_weighted"]["n"]
    )
    if small < MIN_N:
        result = UNKNOWN
    else:
        reduced = all(
            details[m]["change"] is not None and details[m]["change"] <= -TARGET_REDUCTION
            for m in ("total", "cost_weighted")
        )
        e0, e1 = b0["escapes_per_slice"], b1["escapes_per_slice"]
        if e0 is None or e1 is None or b0.get("escapes_unknown") or b1.get("escapes_unknown"):
            escapes_ok = "unknown"
        else:
            escapes_ok = "ok" if e1 <= e0 else "rise"
        if reduced and escapes_ok == "ok":
            result = "met"
        elif reduced and escapes_ok == "unknown":
            result = "met (escapes unknown)"
        else:
            result = "not met"
    return {
        "schema": VERDICT_SCHEMA,
        "verdict": result,
        "directional": True,
        "note": "directional only (n is small); not a release gate",
        "n": {"b0": b0["total"]["n"], "b1": b1["total"]["n"]},
        "details": details,
        "generated_at": iso(datetime.now(timezone.utc).replace(microsecond=0)),
    }


# --------------------------------------------------------------------------- CLI


def rig_from_whoami() -> str:
    """Return `.identity.rigName` from `rig whoami --json`.

    Raises:
        UsageError: when the command fails or prints no rig name.
    """
    try:
        result = subprocess.run(
            ["rig", "whoami", "--json"], capture_output=True, text=True, timeout=30, check=False
        )
        name = json.loads(result.stdout)["identity"]["rigName"] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        name = None
    if not isinstance(name, str) or not name:
        raise UsageError("cannot tell the rig: `rig whoami --json` failed; pass --rig")
    return name


def parse_window(text: str) -> tuple[str, datetime, datetime]:
    """Parse `<name>=<since>..<until>`.

    Raises:
        UsageError: when the window is malformed or empty.
    """
    name, sep, bounds = text.partition("=")
    since_text, dots, until_text = bounds.partition("..")
    if not sep or not dots or not NAME_RE.match(name):
        raise UsageError(f"malformed --window {text!r}: want <name>=<since>..<until>")
    since, until = parse_bounds(since_text, until_text)
    return name, since, until


def parse_bounds(since_text: str, until_text: str) -> tuple[datetime, datetime]:
    """Parse and order a since/until pair.

    Raises:
        UsageError: when a bound is not a timestamp or since >= until.
    """
    try:
        since, until = parse_ts(since_text), parse_ts(until_text)
    except ValueError as exc:
        raise UsageError(f"bad timestamp: {exc}") from None
    if since >= until:
        raise UsageError("--since must be earlier than --until")
    return since, until


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="collect_metrics.py",
        description="Read-only hacf.telemetry/v1 scorecards and the B0/B1 exit metric.",
    )
    parser.add_argument("--rig")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--label")
    parser.add_argument("--db")
    parser.add_argument("--claude-projects")
    parser.add_argument("--repo")
    parser.add_argument("--advisor-session", default=DEFAULT_ADVISOR)
    parser.add_argument("--out-dir")
    parser.add_argument("--context-series", action="store_true")
    parser.add_argument("--loops-dir")
    parser.add_argument("--prices")
    parser.add_argument("--client-cost-json")
    parser.add_argument("--advisor-configured-json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--b0", action="store_true")
    mode.add_argument("--b1", action="store_true")
    mode.add_argument("--compare", nargs=2, metavar=("B0_JSON", "B1_JSON"))
    parser.add_argument("--window", action="append", default=[])
    return parser


def out_dir_for(args: argparse.Namespace, rig: str) -> Path:
    """Return --out-dir, else $HACF_TELEMETRY_DIR, else $HOME/.hacf/<rig>/telemetry."""
    if args.out_dir:
        return Path(args.out_dir)
    env = os.environ.get("HACF_TELEMETRY_DIR")
    if env:
        return Path(env)
    return Path.home() / ".hacf" / rig / "telemetry"


def run(argv: list[str]) -> int:
    """Run the CLI; return the exit code.

    Raises:
        UsageError: on a bad argument, before anything is written.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.label is not None and not NAME_RE.match(args.label):
        raise UsageError(f"bad --label {args.label!r}: letters, digits, '.', '_' and '-' only")

    if args.compare:
        loaded = []
        for path in args.compare:
            try:
                loaded.append(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise UsageError(f"cannot read {path}: {exc}") from None
        b0, b1 = loaded
        if any(d.get("schema") != AGGREGATE_SCHEMA for d in loaded):
            raise UsageError("--compare takes two B0/B1 aggregate files")
        rig = args.rig or b0.get("rig") or rig_from_whoami()
        result = verdict(b0, b1)
        result["b0"], result["b1"] = args.compare
        out_dir = out_dir_for(args, rig)
        path = write_json(ensure_private_dir(out_dir), f"verdict-{stamp()}.json", result)
        print(f"verdict: {result['verdict']} (directional; not a release gate)")
        print(path)
        return 0

    if args.b0 or args.b1:
        if not args.window:
            raise UsageError("--b0/--b1 need at least one --window <name>=<since>..<until>")
        if args.since or args.until:
            raise UsageError("--b0/--b1 take --window, not --since/--until")
        windows = [parse_window(w) for w in args.window]
        if len({name for name, _s, _u in windows}) != len(windows):
            raise UsageError("window names must be unique")
    else:
        if args.window:
            raise UsageError("--window needs --b0 or --b1")
        if not args.since or not args.until:
            raise UsageError("--since and --until are required")
        since, until = parse_bounds(args.since, args.until)
        windows = [(args.label or "window", since, until)]

    rig = args.rig or rig_from_whoami()
    out_dir = out_dir_for(args, rig)
    db = Path(args.db) if args.db else Path.home() / ".openrig" / "openrig.sqlite"
    projects = (
        Path(args.claude_projects) if args.claude_projects else Path.home() / ".claude" / "projects"
    )
    repo = Path(args.repo) if args.repo else Path.cwd()
    loops_dir = Path(args.loops_dir) if args.loops_dir else None
    if loops_dir is not None and not loops_dir.is_dir():
        raise UsageError(f"--loops-dir {args.loops_dir} is not a directory")

    prices = None
    if args.prices:
        prices = load_prices(Path(args.prices))
    configured = client_costs = None
    if args.advisor_configured_json:
        configured = load_configured(Path(args.advisor_configured_json))
    if args.client_cost_json:
        client_costs = load_client_costs(Path(args.client_cost_json))

    cards = []
    for name, since, until in windows:
        label = name if (args.b0 or args.b1) else args.label
        card, side = build_scorecard(
            rig,
            since,
            until,
            label,
            db,
            projects,
            repo,
            args.advisor_session,
            out_dir,
            loops_dir,
            prices,
            configured,
            client_costs,
        )
        path = write_json(out_dir, unique_name(out_dir, label or "window"), card)
        print(path)
        if args.context_series:
            print(write_json(out_dir, f"{label or 'window'}.context.json", side["context_series"]))
        cards.append((card, side, path.name))
    if args.b0 or args.b1:
        kind = "B0" if args.b0 else "B1"
        result = aggregate(kind, rig, windows, cards, repo, prices is not None)
        print(write_json(out_dir, f"{kind}.json", result))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: exit 2 with one line on a usage error."""
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        print(f"collect_metrics: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
