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
H5_SEAT_KEYS = {"models", "mixed", "usage_by_model"}
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
    # OPR.99.0.11.1 MR 2: the H5 keys are additive; every earlier key is still compared exactly
    h5 = {k: a["OPR.9.1.1"].pop(k) for k in H5_SEAT_KEYS}
    assert h5 == {
        "models": ["m-1"],
        "mixed": False,
        "usage_by_model": {
            "m-1": {"fresh_in": 100, "cache_write": 40, "cache_read": 1000, "output": 10}
        },
    }
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
    # OPR.99.0.8.3 MR 2: `context` is added after every v0.6.3 key
    assert list(card) == [*KEY_ORDER, "label", "context"]
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


# --------------------------------------------------------------------------- context (OPR.99.0.8.3)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "scorecard_v063_lbl.json"


def ctx_line(ts: str, msg_id: str, context: int) -> str:
    """One assistant line whose resident context is `context` (fresh 1000, rest split)."""
    return usage_line(ts, msg_id, f"req-{msg_id}", fresh=1000, cw=0, cr=context - 1000, out=7)


def write_b(world: World, lines: list[str], sub: list[str] | None = None, sid: str = "sb") -> None:
    """Replace seat b's transcript (session sid) and, optionally, its subagent transcript."""
    path = world.projects / "proj-b" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if sub is not None:
        sub_path = world.projects / "proj-b" / sid / "subagents" / "agent-1.jsonl"
        sub_path.parent.mkdir(parents=True, exist_ok=True)
        sub_path.write_text("\n".join(sub) + "\n", encoding="utf-8")


def test_context_per_occupant(world: World) -> None:
    """10k, 25k, 18k + a duplicate id + a 90k subagent turn: turns 3, peak 25000."""
    build_db(world)
    dup = ctx_line("2026-11-01T11:00:00.000Z", "msg-c2", 25000)
    write_b(
        world,
        [
            ctx_line("2026-11-01T10:30:00.000Z", "msg-c1", 10000),
            dup,
            dup,
            ctx_line("2026-11-01T11:30:00.000Z", "msg-c3", 18000),
        ],
        sub=[ctx_line("2026-11-01T11:10:00.000Z", "msg-sub", 90000)],
    )
    _code, card = run_window(world)
    b = card["context"]["b@hacf"]
    assert b == {
        "occupants": [
            {
                "session_id": "sb",
                "turns": 3,
                "peak": 25000,
                "first_ts": "2026-11-01T10:30:00Z",
                "last_ts": "2026-11-01T11:30:00Z",
            }
        ],
        "peak_max": 25000,
        "turns": 3,
    }
    # the subagent's turn still counts in the token numbers, never in the context
    assert seat_slices(card, "b@hacf")["OPR.9.1.2"]["turns"] == 4


def test_context_two_sessions(world: World) -> None:
    """Two sessions for one seat: two occupants; peak_max is the higher peak."""
    build_db(world)
    world.writer.execute(
        "INSERT INTO usage_samples (lane, seat_session, captured_at, native_session_id) "
        "VALUES ('context', 'b@hacf', '2026-11-01T15:00:00.000Z', 'sb2')"
    )
    world.writer.commit()
    write_b(world, [ctx_line("2026-11-01T12:00:00.000Z", "msg-x1", 40000)])
    write_b(
        world,
        [
            ctx_line("2026-11-01T15:00:00.000Z", "msg-y1", 70000),
            ctx_line("2026-11-01T15:05:00.000Z", "msg-y2", 65000),
        ],
        sid="sb2",
    )
    _code, card = run_window(world)
    b = card["context"]["b@hacf"]
    assert [(o["session_id"], o["turns"], o["peak"]) for o in b["occupants"]] == [
        ("sb", 1, 40000),
        ("sb2", 2, 70000),
    ]
    assert b["peak_max"] == 70000 and b["turns"] == 3


def test_context_every_seat_listed(world: World) -> None:
    """Every scorecard seat has a context entry; a seat without a turn has peak_max null."""
    build_db(world)
    (world.projects / "proj-a" / "sa.jsonl").unlink()
    _code, card = run_window(world)
    assert set(card["context"]) == {"a@hacf", "b@hacf"}
    assert card["context"]["a@hacf"] == {"occupants": [], "peak_max": None, "turns": 0}


def test_context_missing_count_is_a_reason(world: World) -> None:
    """A turn without cache_read_input_tokens counts it as 0 and adds an unknowns reason."""
    build_db(world)
    line = json.loads(ctx_line("2026-11-01T11:00:00.000Z", "msg-m", 5000))
    del line["message"]["usage"]["cache_read_input_tokens"]
    write_b(world, [json.dumps(line)])
    _code, card = run_window(world)
    assert card["context"]["b@hacf"]["peak_max"] == 1000
    assert "b@hacf: 1 turns of session sb lack a context count (taken as 0)" in card["unknowns"]


def test_context_backward_compatible(world: World) -> None:
    """Every v0.6.3 key of the fixture's scorecard is byte-identical to v0.6.3's output."""
    build_db(world)
    _code, card = run_window(world, "--label", "lbl")
    golden = GOLDEN.read_text(encoding="utf-8")
    old = {k: v for k, v in card.items() if k not in ("generated_at", "context")}
    # OPR.99.0.8.4 MR 4 adds escape_parts (and its sources) to each slice entry: additive keys,
    # left out here so every v0.6.3 value is still compared byte for byte
    for entry in old["slices"]:
        assert set(entry) >= {"escape_parts", "escape_sources"}
        del entry["escape_parts"], entry["escape_sources"]
    # OPR.99.0.11.1 MR 2 adds models, mixed and usage_by_model to each per-seat slice entry:
    # additive too, left out here so every earlier value is still compared byte for byte
    for seat_entry in old["seats"]:
        for entry in seat_entry["slices"]:
            assert set(entry) >= H5_SEAT_KEYS
            for key in H5_SEAT_KEYS:
                del entry[key]
    assert json.dumps(old, indent=2) + "\n" == golden


def test_context_series(world: World) -> None:
    """--context-series writes <label>.context.json under --out-dir only."""
    build_db(world)
    write_b(
        world,
        [
            ctx_line("2026-11-01T10:30:00.000Z", "msg-c1", 10000),
            ctx_line("2026-11-01T11:00:00.000Z", "msg-c2", 25000),
        ],
    )
    out = world.tmp / "series-out"
    before = sorted(p for p in world.tmp.rglob("*") if p.is_file())
    code = cm.main(
        world.args(
            "--since",
            SINCE,
            "--until",
            UNTIL,
            "--label",
            "lbl",
            "--out-dir",
            str(out),
            "--context-series",
        )
    )
    assert code == 0
    series = json.loads((out / "lbl.context.json").read_text(encoding="utf-8"))
    assert series["b@hacf"] == {
        "sb": [["2026-11-01T10:30:00Z", 10000], ["2026-11-01T11:00:00Z", 25000]]
    }
    assert stat.S_IMODE((out / "lbl.context.json").stat().st_mode) == 0o600
    after = sorted(p for p in world.tmp.rglob("*") if p.is_file())
    new = [p for p in after if p not in before]
    assert new and all(out in p.parents for p in new), new
    assert SENTINEL not in (out / "lbl.context.json").read_text(encoding="utf-8")


def test_no_series_without_flag(world: World) -> None:
    """Without --context-series no .context.json is written."""
    build_db(world)
    run_window(world)
    assert not list(world.out.glob("*.context.json"))


# --------------------------------------------------------------------------- escapes (OPR.99.0.8.4)

R063 = [f"OPR.0.6.3.{i}" for i in range(1, 7)]
REAL_ITERATIONS = [1, 1, 2, 2, 1, 1]  # the shape of the real release-0.6.3 loop records
MARKER = "escape_tracking: v1"


def release_repo(
    tmp: Path, events: dict[str, list[str]], replan: bool = True, marker: set[str] = frozenset()
) -> Path:
    """A repo shaped like release-0.6.3: six slice commits; PROGRESS.md events per slice.

    events maps a slice id to its Outcomes event names; a slice in `marker` gets an executing
    line carrying the P22 tracking marker. With replan, a resolve commit after the slices carries
    HACF-Replan-Of: OPR.0.6.3.5.
    """
    repo = tmp / "repo063"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], env=git_env(SINCE), check=True)
    commit(repo, "2026-10-01T00:00:00Z", "base")
    commit(repo, "2026-11-01T01:00:00Z", "release-0.6.3 resolve: compile")
    for i, sid in enumerate(R063, start=1):
        commit(repo, f"2026-11-01T0{i + 1}:00:00Z", f"release-0.6.3 {sid}: slice {i}")
        slice_dir = repo / "missions" / "release-0.6.3" / "slices" / f"0{i}-s{i}"
        slice_dir.mkdir(parents=True)
        (slice_dir / "SPEC.md").write_text(f"---\nid: {sid}\n---\n# x\n", encoding="utf-8")
        lines = ["- 2026-11-01T00:00:00+00:00 compiled - created"]
        if sid in marker:
            lines.append(f"- 2026-11-01T00:01:00+00:00 executing - <base> abc; {MARKER}")
        for n, event in enumerate(events.get(sid, [])):
            lines.append(f"- 2026-11-01T00:0{n + 2}:00+00:00 {event} - x")
        (slice_dir / "PROGRESS.md").write_text(
            "# Progress\n\n## Outcomes\n\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
    if replan:
        commit(
            repo,
            "2026-11-01T09:00:00Z",
            "release-0.6.3 resolve: replan slice 6\n\nHACF-Replan-Of: OPR.0.6.3.5",
        )
    return repo


def loops_dir(tmp: Path, iterations=REAL_ITERATIONS) -> Path:
    """Six loop records in the shape of the real ones, all PASS."""
    d = tmp / "loops"
    d.mkdir()
    for sid, it in zip(R063, iterations):
        (d / f"hacf-loop-{sid}.md").write_text(
            f"# Loop: {sid}\n- MiniPRD: missions/x/SPEC.md\n- Iteration: {it} of 5\n"
            f"- Latest verdict: PASS\n- Range judged: a..b\n- Unmet: none\n",
            encoding="utf-8",
        )
    return d


def run_repo(world: World, repo: Path, *extra: str) -> dict:
    """One scorecard over the default window against repo."""
    world.repo = repo
    code, card = run_window(world, *extra)
    assert code == 0
    return card


ALL_RECORDS = {
    "OPR.0.6.3.2": ["executing", "blocked", "executing", "blocked"],
    "OPR.0.6.3.3": ["executed", "audit_failed"],
    "OPR.0.6.3.4": ["executed", "audit_failed"],
}


def test_escapes_all_records_release_063(world: World, tmp_path: Path) -> None:
    """audit_failed x2, blocked x2 and one HACF-Replan-Of: exactly 5, part by part."""
    build_db(world)
    card = run_repo(world, release_repo(tmp_path, ALL_RECORDS))
    rows = slices_of(card)
    assert sum(rows[s]["escapes"] for s in R063) == 5
    assert rows["OPR.0.6.3.2"]["escape_parts"]["blocked"] == 2
    assert rows["OPR.0.6.3.3"]["escape_parts"]["audit_failed"] == 1
    assert rows["OPR.0.6.3.4"]["escape_parts"]["audit_failed"] == 1
    assert rows["OPR.0.6.3.5"]["escape_parts"]["git"] == 1
    for sid in ("OPR.0.6.3.1", "OPR.0.6.3.6"):
        assert rows[sid]["escapes"] == 0
        assert rows[sid]["escape_parts"]["blocked"] is None
    assert rows["OPR.0.6.3.2"]["escape_sources"]["blocked"] == "progress"
    assert "OPR.0.6.3.1: blocked unknown (no halt records before v0.6.4)" in card["unknowns"]


def test_escapes_all_records_with_markers_still_5(world: World, tmp_path: Path) -> None:
    """P22: with the marker on every slice the total is still exactly 5 and no part is null."""
    build_db(world)
    card = run_repo(world, release_repo(tmp_path, ALL_RECORDS, marker=set(R063)))
    rows = slices_of(card)
    assert sum(rows[s]["escapes"] for s in R063) == 5
    assert all(v is not None for s in R063 for v in rows[s]["escape_parts"].values())


def test_escapes_loops_only(world: World, tmp_path: Path) -> None:
    """No event lines + --loops-dir: audit_failed 2 from loops, blocked null."""
    build_db(world)
    repo = release_repo(tmp_path, {}, replan=False)
    card = run_repo(world, repo, "--loops-dir", str(loops_dir(tmp_path)))
    rows = slices_of(card)
    assert sum(rows[s]["escape_parts"]["audit_failed"] for s in R063) == 2
    assert {rows[s]["escape_sources"]["audit_failed"] for s in R063} == {"loops"}
    assert rows["OPR.0.6.3.3"]["escape_parts"]["audit_failed"] == 1
    assert all(rows[s]["escape_parts"]["blocked"] is None for s in R063)


def test_escapes_loops_only_compares_unknown(world: World, tmp_path: Path, capsys) -> None:
    """The loops-only aggregate marks escapes unknown, so compare cannot say met or not met."""
    build_db(world)
    world.repo = release_repo(tmp_path, {}, replan=False)
    loops = loops_dir(tmp_path)
    assert (
        cm.main(world.args("--b0", "--window", f"w={SINCE}..{UNTIL}", "--loops-dir", str(loops)))
        == 0
    )
    b0 = json.loads((world.out / "B0.json").read_text(encoding="utf-8"))
    assert b0["escapes_unknown"] is True
    verdict = cm.verdict(
        b0 | {"total": {"n": 5, "median": 1000}, "cost_weighted": {"n": 5, "median": 500}},
        b0 | {"total": {"n": 5, "median": 600}, "cost_weighted": {"n": 5, "median": 300}},
    )
    assert verdict["verdict"] == "met (escapes unknown)"


def test_escapes_no_double_count(world: World, tmp_path: Path) -> None:
    """A PROGRESS.md audit_failed line and a loop record for the same slice count once."""
    build_db(world)
    repo = release_repo(tmp_path, {"OPR.0.6.3.3": ["executed", "audit_failed"]}, replan=False)
    card = run_repo(world, repo, "--loops-dir", str(loops_dir(tmp_path, [1, 1, 3, 2, 1, 1])))
    row = slices_of(card)["OPR.0.6.3.3"]
    assert row["escape_parts"]["audit_failed"] == 1
    assert row["escape_sources"]["audit_failed"] == "progress"
    assert slices_of(card)["OPR.0.6.3.4"]["escape_sources"]["audit_failed"] == "loops"


def test_escapes_without_loops_dir(world: World, tmp_path: Path) -> None:
    """Without --loops-dir and no lines: audit_failed 0 from PROGRESS.md, blocked null."""
    build_db(world)
    card = run_repo(world, release_repo(tmp_path, {}, replan=False))
    row = slices_of(card)["OPR.0.6.3.3"]
    assert row["escape_parts"] == {"audit_failed": 0, "blocked": None, "git": 0}
    assert row["escape_sources"]["audit_failed"] == "progress"


@pytest.mark.parametrize(
    ("events", "marked", "parts"),
    [
        ([], True, {"audit_failed": 0, "blocked": 0}),
        (["executed", "audit_failed"], True, {"audit_failed": 1, "blocked": 0}),
        ([], False, {"audit_failed": 0, "blocked": None}),
    ],
    ids=["marker-no-lines", "marker-one-fail", "no-marker"],
)
def test_tracking_marker(world: World, tmp_path: Path, events, marked: bool, parts) -> None:
    """P22 (lead ruling): an executing line with escape_tracking: v1 makes no-halt mean 0."""
    build_db(world)
    sid = "OPR.0.6.3.1"
    repo = release_repo(tmp_path, {sid: events}, replan=False, marker={sid} if marked else set())
    row = slices_of(run_repo(world, repo))[sid]
    assert {k: row["escape_parts"][k] for k in parts} == parts


def test_marker_must_be_on_an_executing_line(world: World, tmp_path: Path) -> None:
    """The marker counts only in an executing line's detail."""
    build_db(world)
    repo = release_repo(tmp_path, {}, replan=False)
    progress = repo / "missions" / "release-0.6.3" / "slices" / "01-s1" / "PROGRESS.md"
    progress.write_text(
        progress.read_text(encoding="utf-8") + f"- 2026-11-01T00:09:00+00:00 executed - {MARKER}\n",
        encoding="utf-8",
    )
    row = slices_of(run_repo(world, repo))["OPR.0.6.3.1"]
    assert row["escape_parts"]["blocked"] is None


def test_replan_of_counted_once(world: World, tmp_path: Path) -> None:
    """A commit with both HACF-Replan-Of and a `resolve: replan` subject naming it is one escape."""
    build_db(world)
    repo = release_repo(tmp_path, {}, replan=False)
    commit(
        repo,
        "2026-11-01T09:00:00Z",
        "release-0.6.3 resolve: replan OPR.0.6.3.5\n\nHACF-Replan-Of: OPR.0.6.3.5",
    )
    row = slices_of(run_repo(world, repo))["OPR.0.6.3.5"]
    assert row["escape_parts"]["git"] == 1


def test_loops_dir_must_exist(world: World, tmp_path: Path, capsys) -> None:
    """A --loops-dir that is not a directory is a usage error (exit 2)."""
    build_db(world)
    code = cm.main(
        world.args("--since", SINCE, "--until", UNTIL, "--loops-dir", str(tmp_path / "nope"))
    )
    assert code == 2
    assert "--loops-dir" in capsys.readouterr().err


def test_loops_dir_has_no_default() -> None:
    """--loops-dir defaults to None, never to a path under $HOME."""
    args = cm.build_parser().parse_args(["--since", SINCE, "--until", UNTIL])
    assert args.loops_dir is None
    assert "/home" not in TOOL.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("b0_escapes", "b1_escapes", "expected"),
    [(None, 0.2, "met (escapes unknown)"), (0.4, 0.2, "met"), (0.4, 0.6, "not met")],
    ids=["b0-unknown", "fewer", "more"],
)
def test_compare_escapes_b2(b0_escapes, b1_escapes, expected: str) -> None:
    """Tokens down 35%: unknown B0 escapes -> met (escapes unknown); fewer -> met; more -> not."""

    def agg(medians, escapes) -> dict:
        return {
            "total": {"n": 5, "median": medians[0]},
            "cost_weighted": {"n": 5, "median": medians[1]},
            "escapes_per_slice": escapes,
        }

    result = cm.verdict(agg((1000, 1000), b0_escapes), agg((650, 650), b1_escapes))
    assert result["verdict"] == expected
    assert set(result["details"]) == {"total", "cost_weighted", "escapes_per_slice"}


SKILLS = REPO_ROOT / ".agents" / "skills"


@pytest.mark.parametrize(
    ("skill", "needle"),
    [
        ("hyper-audit", "--event audit_failed"),
        ("hyper-execute", "--event blocked"),
        ("hyper-execute", "escape_tracking: v1"),
        ("hyper-resolve", "HACF-Replan-Of: <failed slice id>"),
    ],
)
def test_skill_sentences_host_neutral(skill: str, needle: str) -> None:
    """Each shipped skill carries its new sentence, with no /home path, rig or seat name."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if needle in ln]
    assert lines, needle
    for line in lines:
        assert "/home" not in line
        for word in ("rig", "seat", "orch-lead", "build-audit", "build-execute", "@hacf"):
            assert word not in line.lower().replace("trigger", ""), (word, line)


@pytest.mark.parametrize(
    ("events", "marked", "escapes", "blocked"),
    [
        (["executing", "blocked", "executing", "blocked"], False, 2, 2),
        ([], True, 0, 0),
        ([], False, 0, None),
    ],
    ids=["two-blocked", "clean-marker", "no-marker"],
)
def test_slice_escapes_equals_scorecard(
    world: World, tmp_path: Path, events, marked: bool, escapes: int, blocked
) -> None:
    """MR 4 (fix round 1): slice_escapes sums audit_failed + blocked + git, like the scorecard."""
    build_db(world)
    sid = "OPR.0.6.3.2"
    repo = release_repo(tmp_path, {sid: events}, replan=False, marker={sid} if marked else set())
    card = run_repo(world, repo)
    row = slices_of(card)[sid]
    sha = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "-1", "--grep", f"{sid}:"],
        capture_output=True,
        text=True,
        check=True,
        env=git_env(SINCE),
    ).stdout.strip()
    unknowns: list[str] = []
    direct = cm.slice_escapes(repo, sid, sha, unknowns)
    assert direct == row["escapes"] == escapes
    assert isinstance(direct, int)
    parts, _sources = cm.slice_escape_parts(repo, sid, sha, [])
    assert parts["blocked"] == row["escape_parts"]["blocked"] == blocked


# --------------------------------------------------------------------------- H5 instrument
# Slice OPR.99.0.11.1: dollars and tokens per slice and per seat, by effective model.

SHIPPED_PRICES = TOOL_DIR / "prices.yml"
# $1 per fresh token, $2 per cache write, $0.50 per cache read, $10 per output token (rates are per
# million tokens), so a fixture's dollars are small whole numbers a person can add up by hand
FIXTURE_PRICES = """\
schema: hacf.prices/v1
currency: USD
unit: per_million_tokens
source: https://example.invalid/pricing
retrieved: "2026-11-25"
models:
  m-1: {fresh_in: 1000000, cache_write: 2000000, cache_read: 500000, output: 10000000}
  m-2: {fresh_in: 3000000, cache_write: 6000000, cache_read: 1500000, output: 30000000}
"""


def write_prices(world: World, text: str = FIXTURE_PRICES) -> Path:
    """Write a price table under the world and return its path."""
    path = world.tmp / "prices.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_h5_mixed_bucket_and_usage_by_model(world: World) -> None:
    """(a) A bucket served by two models is `mixed`; usage_by_model splits the counts."""
    build_db(world)
    _code, card = run_window(world)
    b = seat_slices(card, "b@hacf")["OPR.9.1.2"]
    assert b["models"] == ["m-1", "m-2"]
    assert b["mixed"] is True
    assert b["usage_by_model"] == {
        "m-1": {"fresh_in": 10, "cache_write": 0, "cache_read": 0, "output": 2},
        "m-2": {"fresh_in": 20, "cache_write": 0, "cache_read": 0, "output": 4},
    }
    a = seat_slices(card, "a@hacf")["OPR.9.1.1"]
    assert a["mixed"] is False and a["models"] == ["m-1"]


def test_h5_dollars_match_a_hand_computed_value(world: World) -> None:
    """(b) usd per seat entry, per slice and per_slice summary equal the hand sum to the cent."""
    build_db(world)
    prices = write_prices(world)
    code = cm.main(world.args("--b1", "--window", f"w={SINCE}..{UNTIL}", "--prices", str(prices)))
    assert code == 0
    card = json.loads(next(p for p in world.outputs() if p.name.startswith("w-")).read_text())
    # a, slice 1: 100 fresh + 40 write x2 + 1000 read x0.5 + 10 out x10 = 100+80+500+100
    assert seat_slices(card, "a@hacf")["OPR.9.1.1"]["usd"] == 780.0
    # b, slice 2: m-1 10 fresh + 2 out x10 = 30; m-2 20 fresh x3 + 4 out x30 = 180
    assert seat_slices(card, "b@hacf")["OPR.9.1.2"]["usd"] == 210.0
    b1 = json.loads((world.out / "B1.json").read_text(encoding="utf-8"))
    rows = {s["slice_id"]: s for s in b1["slices"]}
    # the resolve compile's one record (1 fresh + 1 out = 11) is split evenly over both slices
    assert rows["OPR.9.1.1"]["usd"] == 785.5
    assert rows["OPR.9.1.2"]["usd"] == 215.5
    assert rows["OPR.9.1.2"]["mixed"] is True and rows["OPR.9.1.1"]["mixed"] is False
    assert b1["usd_per_slice"]["n"] == 2
    assert b1["usd_per_slice"]["median"] == 500.5 and b1["usd_per_slice"]["mean"] == 500.5


def test_h5_haiku_long_prompt_rate_above_threshold_only() -> None:
    """(c) The long-prompt rates apply per record when its prompt is above the threshold."""
    table = cm.load_prices(SHIPPED_PRICES)
    haiku = table["claude-haiku-5-5"]

    def record(fresh: int, out: int = 1000) -> dict:
        return {
            "input_tokens": fresh,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": out,
        }

    assert cm.record_usd(record(100_000), haiku) == pytest.approx(
        100_000 * 0.10 / 1e6 + 1000 * 0.50 / 1e6
    )
    assert cm.record_usd(record(100_001), haiku) == pytest.approx(
        100_001 * 0.50 / 1e6 + 1000 * 2.50 / 1e6
    )
    # the prompt counts cache reads and writes too
    split = {**record(0), "cache_read_input_tokens": 60_000, "cache_creation_input_tokens": 50_000}
    assert cm.record_usd(split, haiku) == pytest.approx(
        50_000 * 0.625 / 1e6 + 60_000 * 0.05 / 1e6 + 1000 * 2.50 / 1e6
    )
    # a model without long_prompt never switches
    opus = table["claude-opus-5-5"]
    assert cm.record_usd(record(500_000), opus) == pytest.approx(
        500_000 * 4 / 1e6 + 1000 * 20 / 1e6
    )


def test_h5_unknown_model_is_null_with_a_reason(world: World) -> None:
    """(d) A model the table lacks gives usd null and an unknowns reason, never zero."""
    build_db(world)
    only_m1 = FIXTURE_PRICES.split("  m-2:")[0]
    prices = write_prices(world, only_m1)
    code = cm.main(world.args("--b1", "--window", f"w={SINCE}..{UNTIL}", "--prices", str(prices)))
    assert code == 0
    card = json.loads(next(p for p in world.outputs() if p.name.startswith("w-")).read_text())
    assert seat_slices(card, "a@hacf")["OPR.9.1.1"]["usd"] == 780.0
    assert seat_slices(card, "b@hacf")["OPR.9.1.2"]["usd"] is None
    assert any("usd unknown" in r and "m-2" in r for r in card["unknowns"])
    b1 = json.loads((world.out / "B1.json").read_text(encoding="utf-8"))
    rows = {s["slice_id"]: s for s in b1["slices"]}
    assert rows["OPR.9.1.2"]["usd"] is None
    assert b1["usd_per_slice"]["unknown"] == 1


def test_h5_no_prices_means_no_usd(world: World) -> None:
    """(e) Without --prices no `usd` key appears anywhere; earlier keys keep their values."""
    build_db(world)
    code = cm.main(world.args("--b1", "--window", f"w={SINCE}..{UNTIL}"))
    assert code == 0
    for path in world.outputs():
        assert '"usd' not in path.read_text(encoding="utf-8"), path.name
    b1 = json.loads((world.out / "B1.json").read_text(encoding="utf-8"))
    row = {s["slice_id"]: s for s in b1["slices"]}["OPR.9.1.1"]
    # 1150 of its own + half of the resolve compile's 2 tokens (as in test_b0_two_windows)
    assert row["total"] == 1151 and row["cost_weighted"] == 303 and row["escapes"] == 3
    assert row["mixed"] is False


@pytest.mark.parametrize(
    ("edit", "key"),
    [
        (("schema: hacf.prices/v1", "schema: other/v1"), "schema"),
        (("currency: USD", "currency: EUR"), "currency"),
        (("unit: per_million_tokens", "unit: per_token"), "unit"),
        (('retrieved: "2026-11-25"\n', ""), "retrieved"),
        (("cache_read: 500000, ", ""), "models.m-1.cache_read"),
        (("output: 10000000}", "output: -1}"), "models.m-1.output"),
        (("fresh_in: 1000000", "fresh_in: {usd: 1}"), "models.m-1.fresh_in"),
        (("  m-1: {fresh_in", "  m-1: 7\n  m-x: {fresh_in"), "models.m-1"),
    ],
)
def test_h5_malformed_prices_exit_2(world: World, capsys, edit, key: str) -> None:
    """(f) A malformed table exits 2 with one line naming the key, and writes nothing."""
    build_db(world)
    text = FIXTURE_PRICES.replace(*edit)
    assert text != FIXTURE_PRICES
    path = write_prices(world, text)
    code = cm.main(world.args("--since", SINCE, "--until", UNTIL, "--prices", str(path)))
    err = capsys.readouterr().err
    assert code == 2
    assert len(err.strip().splitlines()) == 1 and key in err, err
    assert world.outputs() == []


def test_h5_missing_prices_file_exits_2(world: World, capsys) -> None:
    """(f) An unreadable --prices file is a usage error too."""
    code = cm.main(
        world.args("--since", SINCE, "--until", UNTIL, "--prices", str(world.tmp / "none.yml"))
    )
    assert code == 2 and "--prices" in capsys.readouterr().err


def test_h5_shipped_prices_parse() -> None:
    """(g) The shipped table parses, has the three models, a source and a retrieval date."""
    import yaml

    raw = yaml.safe_load(SHIPPED_PRICES.read_text(encoding="utf-8"))
    assert raw["source"].startswith("https://") and raw["retrieved"]
    table = cm.load_prices(SHIPPED_PRICES)
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"} <= set(table)
    for model in table.values():
        assert set(model["rates"]) == set(cm.RATE_KEYS)
    assert table["claude-haiku-5-5"]["long_prompt"]["threshold_tokens"] == 100_000
    assert table["claude-opus-5-5"]["long_prompt"] is None


def test_h5_slice_size_from_numstat(tmp_path: Path) -> None:
    """MR 4: size is {files, lines} over the slice's commits; unreadable commits give a reason."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], env=git_env(SINCE), check=True)
    (repo / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "a.txt", "b.txt"], env=git_env(SINCE), check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "one"], env=git_env(SINCE), check=True
    )
    first = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    (repo / "a.txt").write_text("1\n2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], env=git_env(SINCE), check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "two"], env=git_env(SINCE), check=True
    )
    second = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    # commit one adds 3 + 1 lines in 2 files; commit two deletes 1 line of a.txt: 2 distinct files
    assert cm.slice_size(repo, [first, second]) == ({"files": 2, "lines": 5}, None)
    size, reason = cm.slice_size(repo, ["0" * 40])
    assert size is None and "git show failed" in reason
    assert cm.slice_size(repo, [])[0] is None
    assert cm.slice_size(None, [first])[0] is None


def test_h5_aggregate_size_unknown_is_a_reason(world: World) -> None:
    """The fixture's boundary commits are empty: size is 0 files, 0 lines, a known value."""
    build_db(world)
    cm.main(world.args("--b1", "--window", f"w={SINCE}..{UNTIL}"))
    b1 = json.loads((world.out / "B1.json").read_text(encoding="utf-8"))
    assert {s["slice_id"]: s["size"] for s in b1["slices"]}["OPR.9.1.1"] == {
        "files": 0,
        "lines": 0,
    }
