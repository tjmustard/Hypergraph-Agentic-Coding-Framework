"""Tests of dev/telemetry/collect_metrics.py, the read-only token telemetry collector.

Slice OPR.99.0.7.1.

Everything runs on fixtures under tmp_path: a WAL-mode sqlite database built from the OpenRig 0.7.21
column lists, Claude Code style JSONL transcripts and a git repo with fixed commit dates. HOME and
HACF_TELEMETRY_DIR point under tmp_path, so no test reads the live OpenRig database or a live
transcript, and nothing is written outside tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, clean_env

TOOL_DIR = REPO_ROOT / "dev" / "telemetry"
TOOL = TOOL_DIR / "collect_metrics.py"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import collect_metrics as cm  # noqa: E402

SENTINEL = "SENTINEL-TEXT-7f3a"
SINCE = "2026-11-01T00:00:00Z"
UNTIL = "2026-11-02T00:00:00Z"
ADVISOR = "advisor-lead@kernel"
KEY_ORDER = ["schema", "rig", "window", "generated_at", "sources", "seats", "slices", "unknowns"]

USAGE_DDL = """
CREATE TABLE usage_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lane TEXT NOT NULL CHECK (lane IN ('context', 'provider_window')),
  seat_session TEXT NOT NULL, node_id TEXT, source TEXT, sampled_at TEXT,
  captured_at TEXT NOT NULL, total_input_tokens INTEGER, total_output_tokens INTEGER,
  used_percentage REAL, window TEXT, window_used_percent REAL, resets_at TEXT
  {native}
)"""
CONTEXT_DDL = """
CREATE TABLE context_usage (
  node_id TEXT PRIMARY KEY, session_id TEXT, session_name TEXT,
  availability TEXT NOT NULL DEFAULT 'unknown', reason TEXT, source TEXT, used_percentage REAL,
  remaining_percentage REAL, context_window_size INTEGER, total_input_tokens INTEGER,
  total_output_tokens INTEGER, current_usage TEXT, transcript_path TEXT, sampled_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""
QUEUE_DDL = """
CREATE TABLE queue_items (
  qitem_id TEXT PRIMARY KEY, ts_created TEXT NOT NULL, ts_updated TEXT NOT NULL,
  source_session TEXT NOT NULL, destination_session TEXT NOT NULL, state TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'routine', tier TEXT, tags TEXT, blocked_on TEXT,
  handed_off_to TEXT, handed_off_from TEXT, expires_at TEXT, chain_of_record TEXT,
  body TEXT NOT NULL, closure_reason TEXT, closure_target TEXT, closure_required_at TEXT,
  claimed_at TEXT, last_nudge_attempt TEXT, last_nudge_result TEXT, last_heartbeat TEXT,
  resolution TEXT
)"""


# --------------------------------------------------------------------------- fixture builders


def usage_line(ts: str, msg_id: str, req: str, fresh=0, cw=0, cr=0, out=0, model="m-1") -> str:
    """Return one assistant JSONL line carrying usage numbers and the sentinel text."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "requestId": req,
            "sessionId": "ignored",
            "message": {
                "id": msg_id,
                "model": model,
                "role": "assistant",
                "content": [{"type": "text", "text": SENTINEL}],
                "usage": {
                    "input_tokens": fresh,
                    "cache_creation_input_tokens": cw,
                    "cache_read_input_tokens": cr,
                    "output_tokens": out,
                },
            },
        }
    )


def user_line(ts: str) -> str:
    """Return one user JSONL line (never counted) holding the sentinel."""
    return json.dumps({"type": "user", "timestamp": ts, "message": {"content": SENTINEL}})


class World:
    """A fixture world: database, transcripts, repo, HOME and the output dir."""

    def __init__(self, tmp: Path) -> None:
        """Lay out the paths under tmp."""
        self.tmp = tmp
        self.home = tmp / "home"
        self.out = tmp / "telemetry-out"
        self.db = tmp / "db" / "openrig.sqlite"
        self.projects = tmp / "projects"
        self.repo = tmp / "repo"
        self.home.mkdir()
        self.db.parent.mkdir()
        self.projects.mkdir()
        self.writer: sqlite3.Connection | None = None

    def args(self, *extra: str) -> list[str]:
        """Return the CLI arguments that point every source at this world."""
        return [
            "--rig",
            "hacf",
            "--db",
            str(self.db),
            "--claude-projects",
            str(self.projects),
            "--repo",
            str(self.repo),
            *extra,
        ]

    def outputs(self) -> list[Path]:
        """Return the JSON files the run wrote."""
        return sorted(self.out.glob("*.json")) if self.out.exists() else []


def build_db(
    world: World,
    tables=("usage_samples", "context_usage", "queue_items"),
    native=True,
    keep_open=True,
) -> None:
    """Build the fixture database in WAL mode; keep a writer open so -wal and -shm exist."""
    conn = sqlite3.connect(str(world.db))
    conn.execute("PRAGMA journal_mode=WAL")
    transcript_a = world.projects / "proj-a" / "sa.jsonl"
    if "usage_samples" in tables:
        conn.execute(USAGE_DDL.format(native=", native_session_id TEXT" if native else ""))
        rows = [
            ("a@hacf", "2026-11-01T09:00:00.000Z", "sa"),
            ("b@hacf", "2026-11-01T09:00:00.000Z", "sb"),
            ("b@hacf", "2026-11-01T12:30:00.000Z", "sb"),
            ("c@other", "2026-11-01T09:00:00.000Z", "sc"),
            ("d@hacf-exp", "2026-11-01T09:00:00.000Z", "sd"),
        ]
        for seat, captured, native_id in rows:
            if native:
                conn.execute(
                    "INSERT INTO usage_samples (lane, seat_session, captured_at, "
                    "native_session_id) VALUES ('context', ?, ?, ?)",
                    (seat, captured, native_id),
                )
            else:
                conn.execute(
                    "INSERT INTO usage_samples (lane, seat_session, captured_at) "
                    "VALUES ('context', ?, ?)",
                    (seat, captured),
                )
    if "context_usage" in tables:
        conn.execute(CONTEXT_DDL)
        conn.execute(
            "INSERT INTO context_usage (node_id, session_id, session_name, transcript_path) "
            "VALUES ('n-a', 'sa', 'a@hacf', ?)",
            (str(transcript_a),),
        )
    if "queue_items" in tables:
        conn.execute(QUEUE_DDL)
        queue = [
            ("q1", "2026-11-01T09:30:00.000Z", "a@hacf", ADVISOR),
            ("q2", "2026-11-01T10:00:00.000Z", "a@hacf", ADVISOR),
            ("q3", "2026-10-30T10:00:00.000Z", "a@hacf", ADVISOR),
            ("q4", "2026-11-01T09:30:00.000Z", "c@other", ADVISOR),
            ("q5", "2026-11-01T12:00:00.000Z", "b@hacf", ADVISOR),
            ("q6", "2026-11-01T09:45:00.000Z", "a@hacf", "orch-lead@hacf"),
        ]
        for qid, created, source, dest in queue:
            conn.execute(
                "INSERT INTO queue_items (qitem_id, ts_created, ts_updated, source_session, "
                "destination_session, state, body) VALUES (?, ?, ?, ?, ?, 'done', ?)",
                (qid, created, created, source, dest, SENTINEL),
            )
    conn.commit()
    if keep_open:
        world.writer = conn
    else:
        conn.close()


def build_transcripts(world: World) -> None:
    """Write the seats' transcripts: a via transcript_path, b via the glob plus a subagent file."""
    a = world.projects / "proj-a" / "sa.jsonl"
    a.parent.mkdir(parents=True, exist_ok=True)
    dup = usage_line("2026-11-01T09:00:00.000Z", "msg-a2", "req-a2", 100, 40, 1000, 10)
    a.write_text(
        "\n".join(
            [
                user_line("2026-11-01T06:59:00.000Z"),
                usage_line("2026-11-01T07:00:00.000Z", "msg-a1", "req-a1", fresh=1, out=1),
                dup,
                dup,
                dup,
                "{not json",
                usage_line("2026-11-01T23:00:00.000Z", "msg-a3", "req-a3", fresh=5, out=1),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    b = world.projects / "proj-b" / "sb.jsonl"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text(
        "\n".join(
            [
                usage_line("2026-10-31T12:00:00.000Z", "msg-b0", "req-b0", fresh=999),
                usage_line("2026-11-01T12:00:00.000Z", "msg-b1", "req-b1", fresh=10, out=2),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sub = world.projects / "proj-b" / "sb" / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text(
        usage_line("2026-11-01T13:00:00.000Z", "msg-b2", "req-b2", fresh=20, out=4, model="m-2")
        + "\n",
        encoding="utf-8",
    )
    for sid in ("sc", "sd"):
        other = world.projects / f"proj-{sid}" / f"{sid}.jsonl"
        other.parent.mkdir(parents=True)
        other.write_text(
            usage_line("2026-11-01T09:00:00.000Z", f"msg-{sid}", f"req-{sid}", fresh=77777) + "\n",
            encoding="utf-8",
        )


def git_env(date: str) -> dict[str, str]:
    """Return an isolated git environment with fixed author and committer dates."""
    return clean_env(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        GIT_AUTHOR_DATE=date,
        GIT_COMMITTER_DATE=date,
    )


def commit(repo: Path, date: str, message: str) -> None:
    """Make an empty commit with a fixed date."""
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", message],
        env=git_env(date),
        check=True,
    )


def build_repo(world: World, progress=True) -> None:
    """Build the repo: a resolve compile, a subject slice, a trailer slice and a later repin."""
    repo = world.repo
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], env=git_env(SINCE), check=True)
    commit(repo, "2026-10-01T00:00:00Z", "base")
    commit(repo, "2026-11-01T08:00:00Z", "m1 resolve: compile m1")
    commit(repo, "2026-11-01T10:00:00Z", "m1 OPR.9.1.1: first slice")
    commit(repo, "2026-11-01T14:00:00Z", "m1 slice two\n\nHACF-Slice: OPR.9.1.2")
    commit(repo, "2026-11-01T21:00:00Z", "repin a block\n\nHACF-Repin: OPR.9.1.1 block x")
    commit(repo, "2026-11-01T21:30:00Z", "repin another\n\nHACF-Repin: OPR.9.1.10 block y")
    slice_dir = repo / "missions" / "m1" / "slices" / "01-first"
    slice_dir.mkdir(parents=True)
    (slice_dir / "SPEC.md").write_text("---\nid: OPR.9.1.1\n---\n# x\n", encoding="utf-8")
    if progress:
        (slice_dir / "PROGRESS.md").write_text(
            "# Progress\n\n## Outcomes\n\n"
            "- 2026-11-01T09:00:00+00:00 executed - built\n"
            "- 2026-11-01T09:10:00+00:00 audit_failed - one\n"
            "- 2026-11-01T09:20:00+00:00 audit_failed - two\n"
            "- 2026-11-01T09:30:00+00:00 audited - pass\n",
            encoding="utf-8",
        )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A complete fixture world with HOME and HACF_TELEMETRY_DIR under tmp_path."""
    w = World(tmp_path)
    monkeypatch.setenv("HOME", str(w.home))
    monkeypatch.setenv("HACF_TELEMETRY_DIR", str(w.out))
    build_transcripts(w)
    build_repo(w)
    yield w
    if w.writer is not None:
        w.writer.close()


def run_window(world: World, *extra: str) -> tuple[int, dict]:
    """Run one scorecard in-process over the default window and return (exit, scorecard)."""
    code = cm.main(world.args("--since", SINCE, "--until", UNTIL, *extra))
    cards = [p for p in world.outputs() if p.name.startswith(("window-", "lbl-"))]
    assert len(cards) == 1, cards
    return code, json.loads(cards[0].read_text(encoding="utf-8"))


def seat_slices(card: dict, seat: str) -> dict:
    """Return one seat's slices keyed by slice id."""
    (entry,) = [s for s in card["seats"] if s["seat"] == seat]
    return {s["slice_id"]: s for s in entry["slices"]}


def slices_of(card: dict) -> dict:
    """Return the scorecard's slices keyed by slice id."""
    return {s["slice_id"]: s for s in card["slices"]}


# --------------------------------------------------------------------------- happy path


def test_happy_path_numbers(world: World, capsys: pytest.CaptureFixture) -> None:
    """Exact per-seat, per-slice numbers; dedupe; the §8 weights; foreign seats left out."""
    build_db(world)
    code, card = run_window(world)
    assert code == 0
    assert list(card)[: len(KEY_ORDER)] == KEY_ORDER
    assert card["schema"] == "hacf.telemetry/v1"
    assert card["rig"] == "hacf"
    assert card["window"] == {"since": SINCE, "until": UNTIL}
    assert card["sources"]["snapshot"] == "ok"
    assert card["sources"]["jsonl"] == 3
    assert card["sources"]["git"].startswith(f"{SINCE}..{UNTIL} HEAD=")
    assert [s["seat"] for s in card["seats"]] == ["a@hacf", "b@hacf"]

    a = seat_slices(card, "a@hacf")
    assert a["OPR.9.1.1"] == {
        "slice_id": "OPR.9.1.1",
        "fresh_in": 100,
        "cache_read": 1000,
        "cache_write": 40,
        "output": 10,
        "total": 1150,
        "cost_weighted": 300,
        "turns": 1,
        "max_context": 1140,
        "wall_s": 0,
    }
    assert a["m1/resolve"]["total"] == 2
    assert a["m1/resolve"]["cost_weighted"] == 6
    assert a["unattributed"]["total"] == 6
    b = seat_slices(card, "b@hacf")
    assert list(b) == ["OPR.9.1.2"]
    assert b["OPR.9.1.2"]["fresh_in"] == 30
    assert b["OPR.9.1.2"]["output"] == 6
    assert b["OPR.9.1.2"]["turns"] == 2
    assert b["OPR.9.1.2"]["cost_weighted"] == 60
    assert b["OPR.9.1.2"]["wall_s"] == 3600

    assert [s["slice_id"] for s in card["slices"]] == [
        "m1/resolve",
        "OPR.9.1.1",
        "OPR.9.1.2",
        "unattributed",
    ]
    assert slices_of(card)["OPR.9.1.1"]["total"] == 1150
    text = json.dumps(card)
    assert "c@other" not in text and "d@hacf-exp" not in text and "77777" not in text
    assert any("unparsable" in reason for reason in card["unknowns"])
    out = capsys.readouterr().out
    assert out.strip().endswith(".json")


def test_records_after_last_boundary_are_unattributed(world: World) -> None:
    """A record after the last boundary goes to the pseudo-slice `unattributed`."""
    build_db(world)
    _code, card = run_window(world)
    assert seat_slices(card, "a@hacf")["unattributed"]["fresh_in"] == 5
    assert slices_of(card)["unattributed"]["total"] == 6


def test_no_message_text_or_queue_body_in_output(world: World, capsys) -> None:
    """The sentinel in every JSONL message and queue body reaches no output file or stdout."""
    build_db(world)
    run_window(world)
    cm.main(world.args("--b0", "--window", f"w={SINCE}..{UNTIL}"))
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    files = world.outputs()
    assert files
    for path in files:
        assert SENTINEL not in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- snapshot (F12)


def sha_and_mtime(path: Path) -> tuple[str, int]:
    """Return a file's sha256 and mtime_ns."""
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def test_source_database_untouched(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Source files keep sha256 and mtime, an open writer still works, no connect names them."""
    build_db(world)
    sources = [world.db, Path(f"{world.db}-wal"), Path(f"{world.db}-shm")]
    assert all(p.exists() for p in sources)
    before = {p: sha_and_mtime(p) for p in sources}
    calls: list[str] = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        """Record every connect target, then connect."""
        calls.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    code, card = run_window(world)
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    assert code == 0 and card["sources"]["snapshot"] == "ok"
    assert calls, "the copy was opened"
    for target in calls:
        assert Path(target).resolve() not in {p.resolve() for p in sources}
        assert str(world.db.parent) not in target
    assert {p: sha_and_mtime(p) for p in sources} == before
    world.writer.execute(
        "INSERT INTO queue_items VALUES ('q9','t','t','x','y','s','r',"
        "NULL,NULL,NULL,NULL,NULL,NULL,NULL,'b',NULL,NULL,NULL,NULL,NULL,"
        "NULL,NULL,NULL)"
    )
    world.writer.commit()
    assert world.writer.execute("SELECT count(*) FROM queue_items").fetchone()[0] == 7
    assert not list(world.out.glob("snapshot-*"))


def test_no_wal_or_shm_is_ok(world: World) -> None:
    """A database with no -wal and -shm beside it still snapshots ok."""
    build_db(world, keep_open=False)
    assert not Path(f"{world.db}-wal").exists() and not Path(f"{world.db}-shm").exists()
    code, card = run_window(world)
    assert code == 0
    assert card["sources"]["snapshot"] == "ok"
    assert slices_of(card)["OPR.9.1.1"]["total"] == 1150


def truncating_copy(fail_times: int):
    """Return a copy_db_files stand-in that ruins the copy on its first fail_times calls.

    The copy is cut to 100 bytes of non-sqlite data and its copied -wal and -shm are truncated to
    zero. A truncated main file alone is not enough: with a writer open, every page, page 1
    included, can still live in the copied -wal, and quick_check would pass.
    """
    real = cm.copy_db_files
    state = {"calls": 0}

    def fake(source: Path, dest_dir: Path) -> Path:
        """Copy, then truncate the copy to a non-database while failures remain."""
        copy = real(source, dest_dir)
        state["calls"] += 1
        if state["calls"] <= fail_times:
            copy.write_bytes(b"not a database\n" * 6 + b"tail")
            with copy.open("r+b") as handle:
                handle.truncate(100)
            for suffix in ("-wal", "-shm"):
                side = Path(f"{copy}{suffix}")
                if side.exists():
                    side.write_bytes(b"")
        return copy

    return fake, state


def test_quick_check_fails_once_then_ok(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad copy is retried and the second copy is used."""
    build_db(world)
    fake, state = truncating_copy(1)
    monkeypatch.setattr(cm, "copy_db_files", fake)
    code, card = run_window(world)
    assert code == 0
    assert state["calls"] == 2
    assert card["sources"]["snapshot"] == "ok"
    assert slices_of(card)["OPR.9.1.1"]["total"] == 1150


def test_quick_check_fails_twice(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two bad copies: snapshot unknown, a reason, exit 0 and the snapshot dir gone."""
    build_db(world)
    fake, state = truncating_copy(2)
    monkeypatch.setattr(cm, "copy_db_files", fake)
    code, card = run_window(world)
    assert code == 0
    assert state["calls"] == 2
    assert card["sources"]["snapshot"] == "unknown"
    assert any(r.startswith("snapshot: two failed checks") for r in card["unknowns"])
    assert card["seats"] == []
    for entry in card["slices"]:
        assert entry["total"] == "unknown"
        assert entry["advisor_rows"] == "unknown"
    assert not list(world.out.glob("snapshot-*"))


# --------------------------------------------------------------------------- schema check (R5)


def test_usage_samples_missing(world: World) -> None:
    """A missing usage_samples table is a reason, exit 0; context_usage still names seat a."""
    build_db(world, tables=("context_usage", "queue_items"))
    code, card = run_window(world)
    assert code == 0
    assert "usage_samples missing" in card["unknowns"]
    assert [s["seat"] for s in card["seats"]] == ["a@hacf"]


def test_native_session_id_missing(world: World) -> None:
    """A missing native_session_id column is a reason, exit 0; seat b loses its session."""
    build_db(world, native=False)
    code, card = run_window(world)
    assert code == 0
    assert "usage_samples.native_session_id missing" in card["unknowns"]
    assert seat_slices(card, "b@hacf") == {}
    assert seat_slices(card, "a@hacf")["OPR.9.1.1"]["total"] == 1150


# --------------------------------------------------------------------------- transcripts


def test_transcripts_by_path_glob_and_subagents(world: World) -> None:
    """Seat a by transcript_path, b by the glob, b's subagent file counted to b."""
    build_db(world)
    (world.projects / "proj-a" / "sa.jsonl").rename(world.tmp / "elsewhere.jsonl")
    world.writer.execute(
        "UPDATE context_usage SET transcript_path = ? WHERE session_id = 'sa'",
        (str(world.tmp / "elsewhere.jsonl"),),
    )
    world.writer.commit()
    _code, card = run_window(world)
    assert seat_slices(card, "a@hacf")["OPR.9.1.1"]["total"] == 1150
    assert seat_slices(card, "b@hacf")["OPR.9.1.2"]["fresh_in"] == 30


def test_transcript_missing_is_a_reason(world: World) -> None:
    """No transcript for a session: a reason naming the seat and the id."""
    build_db(world)
    (world.projects / "proj-a" / "sa.jsonl").unlink()
    code, card = run_window(world)
    assert code == 0
    assert "a@hacf: no transcript for session sa" in card["unknowns"]


# --------------------------------------------------------------------------- escapes, advisor


def test_escapes_progress_plus_repin(world: World) -> None:
    """Two audit_failed lines + one later HACF-Repin naming the slice = 3; OPR.9.1.10 ignored."""
    build_db(world)
    _code, card = run_window(world)
    assert slices_of(card)["OPR.9.1.1"]["escapes"] == 3
    assert slices_of(card)["OPR.9.1.2"]["escapes"] == 0
    assert "OPR.9.1.2: no PROGRESS.md; escapes count git only" in card["unknowns"]


def test_advisor_rows(world: World) -> None:
    """Two rows to the advisor from a@hacf inside the interval; outside and c@other not counted."""
    build_db(world)
    _code, card = run_window(world)
    rows = slices_of(card)
    assert rows["OPR.9.1.1"]["advisor_rows"] == 2
    assert rows["OPR.9.1.2"]["advisor_rows"] == 1
    assert rows["m1/resolve"]["advisor_rows"] == 0


# --------------------------------------------------------------------------- B0 and --compare


def test_b0_two_windows(world: World, capsys) -> None:
    """--b0 with two windows: two scorecards and B0.json; the resolve compile split evenly."""
    build_db(world)
    guard = world.home / ".hacf" / "hacf" / "context-guard.log"
    guard.parent.mkdir(parents=True)
    guard.write_text(
        "2026-11-01T09:00:00Z rig=hacf mode=apply seat=a@hacf tokens=160000 action=restart "
        "reason=over\n"
        "2026-11-01T09:05:00Z rig=hacf mode=dry-run seat=a@hacf tokens=160000 action=none "
        "reason=dry\n"
        "2026-10-01T09:00:00Z rig=hacf mode=apply seat=a@hacf tokens=160000 action=restart "
        "reason=old\n",
        encoding="utf-8",
    )
    code = cm.main(
        world.args(
            "--b0",
            "--window",
            f"w1={SINCE}..2026-11-01T11:00:00Z",
            "--window",
            f"w2=2026-11-01T11:00:00Z..{UNTIL}",
        )
    )
    assert code == 0
    names = [p.name for p in world.outputs()]
    assert "B0.json" in names
    assert len([n for n in names if n.startswith("w1-")]) == 1
    assert len([n for n in names if n.startswith("w2-")]) == 1
    b0 = json.loads((world.out / "B0.json").read_text(encoding="utf-8"))
    assert b0["kind"] == "B0"
    slices = {s["slice_id"]: s for s in b0["slices"]}
    assert slices["OPR.9.1.1"]["total"] == 1151
    assert slices["OPR.9.1.1"]["cost_weighted"] == 303
    assert slices["OPR.9.1.2"]["total"] == 37
    assert slices["OPR.9.1.2"]["cost_weighted"] == 63
    assert b0["total"] == {"n": 2, "median": 594, "min": 37, "max": 1151}
    assert b0["cost_weighted"] == {"n": 2, "median": 183, "min": 63, "max": 303}
    assert b0["escapes_per_slice"] == 1.5
    assert b0["unattributed"]["total"] == 6
    assert b0["confounds"]["windows"] == ["w1", "w2"]
    assert b0["confounds"]["guard_restarts"] == 1
    assert b0["confounds"]["models"] == {"a@hacf": ["m-1"], "b@hacf": ["m-1", "m-2"]}
    assert b0["confounds"]["sessions"] == {"a@hacf": 1, "b@hacf": 1}


def test_guard_log_absent_is_unknown(world: World) -> None:
    """Without the context-guard log, guard_restarts is unknown."""
    build_db(world)
    cm.main(world.args("--b1", "--window", f"w={SINCE}..{UNTIL}"))
    b1 = json.loads((world.out / "B1.json").read_text(encoding="utf-8"))
    assert b1["kind"] == "B1"
    assert b1["confounds"]["guard_restarts"] == "unknown"


def aggregate_file(path: Path, kind: str, medians: tuple[float, float], n: int, escapes) -> Path:
    """Write a minimal aggregate file with the given medians, n and escapes per slice."""
    data = {
        "schema": cm.AGGREGATE_SCHEMA,
        "kind": kind,
        "rig": "hacf",
        "total": {"n": n, "median": medians[0], "min": 0, "max": 0},
        "cost_weighted": {"n": n, "median": medians[1], "min": 0, "max": 0},
        "escapes_per_slice": escapes,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("b1_medians", "b1_escapes", "n", "expected"),
    [
        ((600, 300), 1.0, 5, "met"),
        ((800, 400), 1.0, 5, "not met"),
        ((600, 300), 1.2, 5, "not met"),
        ((600, 300), 1.0, 2, "unknown"),
    ],
)
def test_compare(tmp_path, monkeypatch, capsys, b1_medians, b1_escapes, n, expected) -> None:
    """40% lower and equal escapes is met; 20% lower or more escapes is not; n < 3 is unknown."""
    out = tmp_path / "out"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HACF_TELEMETRY_DIR", str(out))
    b0 = aggregate_file(tmp_path / "B0.json", "B0", (1000, 500), 5, 1.0)
    b1 = aggregate_file(tmp_path / "B1.json", "B1", b1_medians, n, b1_escapes)
    assert cm.main(["--compare", str(b0), str(b1)]) == 0
    printed = capsys.readouterr().out
    assert f"verdict: {expected} (directional" in printed
    (verdict_file,) = out.glob("verdict-*.json")
    data = json.loads(verdict_file.read_text(encoding="utf-8"))
    assert data["verdict"] == expected
    assert data["directional"] is True
    assert "not a release gate" in data["note"]


# --------------------------------------------------------------------------- output and CLI


def test_output_modes_and_key_order(world: World) -> None:
    """The out dir is 0700, files 0600, valid JSON with the §5.4 keys in order, label last."""
    build_db(world)
    code, card = run_window(world, "--label", "lbl")
    assert code == 0
    assert list(card) == [*KEY_ORDER, "label"]
    assert stat.S_IMODE(world.out.stat().st_mode) == 0o700
    for path in world.outputs():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(world.out.glob(".*"))
    assert not (world.repo / "telemetry").exists()


def fake_rig(bin_dir: Path, body: str) -> None:
    """Install a fake `rig` executable on bin_dir."""
    bin_dir.mkdir()
    rig = bin_dir / "rig"
    rig.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    rig.chmod(0o755)


def run_cli(world: World, bin_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the collector as a subprocess with the fake rig first on PATH."""
    env = clean_env(
        HOME=str(world.home),
        HACF_TELEMETRY_DIR=str(world.out),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )
    return subprocess.run(
        [sys.executable, "-I", str(TOOL), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_rig_from_whoami(world: World) -> None:
    """Without --rig, the rig comes from `rig whoami --json`."""
    build_db(world)
    bin_dir = world.tmp / "bin"
    fake_rig(bin_dir, """echo '{"identity":{"rigName":"hacf"}}'""")
    args = world.args("--since", SINCE, "--until", UNTIL)[2:]
    result = run_cli(world, bin_dir, *args)
    assert result.returncode == 0, result.stderr
    card = json.loads(Path(result.stdout.strip()).read_text(encoding="utf-8"))
    assert card["rig"] == "hacf"
    assert [s["seat"] for s in card["seats"]] == ["a@hacf", "b@hacf"]


def test_rig_whoami_failure_exits_2(world: World) -> None:
    """A failing `rig whoami` without --rig exits 2 with one line and writes nothing."""
    build_db(world)
    bin_dir = world.tmp / "bin"
    fake_rig(bin_dir, "echo boom >&2; exit 1")
    args = world.args("--since", SINCE, "--until", UNTIL)[2:]
    result = run_cli(world, bin_dir, *args)
    assert result.returncode == 2
    assert len(result.stderr.strip().splitlines()) == 1
    assert not world.out.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--since", "yesterday", "--until", UNTIL],
        ["--since", UNTIL, "--until", SINCE],
        ["--since", SINCE, "--until", SINCE],
        ["--b0", "--window", "w1:2026-11-01..2026-11-02"],
        ["--b0", "--window", f"w1={SINCE}"],
        ["--b0", "--window", f"bad/name={SINCE}..{UNTIL}"],
    ],
)
def test_bad_arguments_exit_2(world: World, capsys, extra: list[str]) -> None:
    """A bad timestamp, since >= until or a malformed --window exits 2 and writes nothing."""
    build_db(world)
    assert cm.main(world.args(*extra)) == 2
    assert "collect_metrics:" in capsys.readouterr().err
    assert not world.out.exists()
