"""Deterministic tests for hyper_lore.py (S05a): state machine, gate, anchors, notes, outbox.

Every test works in a temp git repository. The fixture tree `dev/tests/fixtures/lore/base/` holds a
ledger with a `removed_nodes` tombstone, a graph with two CANON canon nodes, a PROPOSED decision
that supersedes both, a PROPOSED story and a PROPOSED canon body, plus the files they point at.
Tickets and pending records are written only inside the temp repositories; nothing approves
against this repository's lore.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _runtime
import hyper_ledger
import hyper_lore
import pytest
import yaml
from conftest import FIXTURES_DIR, FROZEN_NOW, PY310, SCRIPTS_DIR, GitRepo

SCRIPT = SCRIPTS_DIR / "hyper_lore.py"
UPDATER = SCRIPTS_DIR / "hypergraph_updater.py"
BASE = FIXTURES_DIR / "lore" / "base"
BODIES = FIXTURES_DIR / "lore" / "bodies"
GRAPH = "lore_hypergraph.yml"
LEDGER = "spec/compiled/architecture.yml"
TICKETS = ".agents/state/lore/tickets"
PENDING = ".agents/state/lore/pending"
TS_PLUS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
APPROVER = "Lore Tester <lore@example.invalid>"
HACF_ENV = ("HACF_OUTBOX", "HACF_AUTONOMOUS", "HACF_SLICE", "HACF_RUN_ID")
VERBS = ("approve", "reject", "retire", "reopen", "review")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep outer git and engine variables away, and never give the CLI a TTY in-process."""
    for key in list(os.environ):
        if key.startswith("GIT_") or key in HACF_ENV:
            monkeypatch.delenv(key)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Freeze _runtime's clock at FROZEN_NOW; tests move it by setting `.now`."""
    state = _Clock(FROZEN_NOW)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            """Return the test clock's time in tz (naive UTC when tz is None)."""
            return state.now.astimezone(tz) if tz else state.now.replace(tzinfo=None)

    monkeypatch.setattr(_runtime, "datetime", Frozen)
    return state


def _enter(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo.git("config", "user.name", "Lore Tester")
    repo.git("config", "user.email", "lore@example.invalid")
    for key, value in repo.env.items():
        if key not in HACF_ENV:
            monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def bare_repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return tmp_git_repo with git identity set and the working directory inside it."""
    return _enter(tmp_git_repo, monkeypatch)


@pytest.fixture
def lore_repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return tmp_git_repo holding the fixture ledger, graph and lore files, committed."""
    root = tmp_git_repo.path
    for name in ("lore", "src"):
        shutil.copytree(BASE / name, root / name)
    (root / "spec" / "compiled").mkdir(parents=True)
    shutil.copyfile(BASE / "ledger.yml", root / LEDGER)
    shutil.copyfile(BASE / "graph.yml", root / GRAPH)
    tmp_git_repo.commit("fixture lore")
    return _enter(tmp_git_repo, monkeypatch)


def lore(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, str, str]:
    """Run hyper_lore.main in-process; return (exit code, stdout, stderr)."""
    capsys.readouterr()
    code = hyper_lore.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def subprocess_env(repo: GitRepo, **extra: str) -> dict[str, str]:
    """Return the repository's environment without engine variables, plus extra."""
    env = {key: value for key, value in repo.env.items() if key not in HACF_ENV}
    env.update(extra)
    return env


def run_cli(repo: GitRepo, *args: str, interpreter: str | None = None, **extra: str):
    """Run hyper_lore.py as a subprocess in the repository."""
    return subprocess.run(
        [interpreter or sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=subprocess_env(repo, **extra),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def load(repo: GitRepo) -> dict:
    """Return the parsed graph of the repository."""
    return yaml.safe_load((repo.path / GRAPH).read_text(encoding="utf-8"))


def save(repo: GitRepo, data: dict) -> None:
    """Write data as the repository's graph."""
    (repo.path / GRAPH).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def node(repo: GitRepo, node_id: str) -> dict:
    """Return one node of the repository's graph."""
    return next(n for n in load(repo)["nodes"] if n["id"] == node_id)


def graph_bytes(repo: GitRepo) -> bytes:
    """Return the graph file's bytes."""
    return (repo.path / GRAPH).read_bytes()


def tree_bytes(repo: GitRepo, rel: str = "lore") -> dict[str, bytes]:
    """Return {path: bytes} of every file under rel."""
    root = repo.path / rel
    return {
        p.relative_to(repo.path).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() or p.is_symlink()
    }


def sha(data: bytes) -> str:
    """Return the sha256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()


def request(capsys: pytest.CaptureFixture, repo: GitRepo, node_id: str) -> str:
    """Run approval-request and return the nonce of the pending record."""
    code, out, err = lore(capsys, "approval-request", node_id)
    assert code == 0, err
    record = json.loads((repo.path / PENDING / f"{node_id}.json").read_text(encoding="utf-8"))
    assert f"nonce: {record['nonce']}" in out
    return record["nonce"]


def write_pending(repo: GitRepo, node_id: str, nonce: str = "a" * 32) -> str:
    """Write a pending record directly (the documented hacf.lore-pending/v1 format)."""
    current = node(repo, node_id)
    now = hyper_lore._now()
    record = {
        "schema": "hacf.lore-pending/v1",
        "id": node_id,
        "title": current["title"],
        "content_sha256": sha((repo.path / current["pointer"]["path"]).read_bytes()),
        "nonce": nonce,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "affects": {"supersedes": [], "retires": [], "reanchors": []},
    }
    target = repo.path / PENDING / f"{node_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record), encoding="utf-8")
    return nonce


def ticket(
    repo: GitRepo,
    verb: str,
    node_id: str,
    nonce: str,
    *,
    created: datetime | None = None,
    ttl: int = 120,
    name: str | None = None,
    z: bool = False,
) -> Path:
    """Write a hook ticket (the documented hacf.lore-ticket/v1 format)."""
    created = created or hyper_lore._now()

    def stamp(value: datetime) -> str:
        text = value.isoformat()
        return text.replace("+00:00", "Z") if z else text

    body = {
        "schema": "hacf.lore-ticket/v1",
        "verb": verb,
        "id": node_id,
        "nonce": nonce,
        "created_at": stamp(created),
        "expires_at": stamp(created + timedelta(seconds=ttl)),
    }
    target = repo.path / TICKETS / (name or f"{verb}-{node_id}-{nonce[:6]}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body), encoding="utf-8")
    return target


def warrant(repo: GitRepo, text: str = "The user said: this rule still holds.") -> str:
    """Write a warrant file outside the work tree and return its path."""
    path = repo.scratch / f"warrant-{sha(text.encode())[:8]}.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def gated(
    capsys, repo: GitRepo, verb: str, node_id: str, nonce: str, warrant_file: str | None = None
):
    """Run one gated verb in-process with --nonce and a warrant file."""
    extra = ["--still-valid"] if verb == "review" else []
    return lore(
        capsys,
        verb,
        node_id,
        *extra,
        "--nonce",
        nonce,
        "--warrant-file",
        warrant_file or warrant(repo),
    )


def approve_with_ticket(capsys, repo: GitRepo, node_id: str, verb: str = "approve") -> str:
    """Request, ticket and run one gated verb; assert it succeeds and return its output."""
    nonce = request(capsys, repo, node_id)
    ticket(repo, verb, node_id, nonce)
    code, out, err = gated(capsys, repo, verb, node_id, nonce)
    assert code == 0, err
    return out


def findings(capsys) -> tuple[int, dict]:
    """Run validate --json in-process; return (exit code, report)."""
    code, out, _ = lore(capsys, "validate", "--json")
    return code, json.loads(out)


def rules_of(report: dict, severity: str) -> set[tuple[str, str]]:
    """Return the (node, rule) pairs of one severity in a validate report."""
    return {(f["node"], f["rule"]) for f in report[severity]}


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _canon_node(repo: GitRepo, node_id: str, status: str, text: str) -> dict:
    """Return a canon-type node that has held CANON, with its canon file written."""
    body = f"# {node_id}\n\n{text}\n".encode()
    (repo.path / "lore" / "canon" / f"{node_id}.md").write_bytes(body)
    warrant_text = f"approved {node_id}"
    at = "2026-10-21T12:00:00+00:00"
    approval = {
        "approved_by": APPROVER,
        "approved_at": at,
        "warrant": warrant_text,
        "warrant_sha256": sha(warrant_text.encode()),
        "method": "tty-confirm",
        "content_sha256": sha(body),
        "nonce": "b" * 32,
    }
    history = [
        {"at": at, "from": None, "to": "PROPOSED", "by": "agent", "warrant": None, "ref": None},
        {
            "at": at,
            "from": "PROPOSED",
            "to": "CANON",
            "by": APPROVER,
            "warrant": warrant_text,
            "ref": None,
        },
    ]
    if status != "CANON":
        history.append(
            {"at": at, "from": "CANON", "to": status, "by": APPROVER, "warrant": None, "ref": None}
        )
    return {
        "id": node_id,
        "type": "canon",
        "title": f"{node_id} title",
        "status": status,
        "origin": "agent",
        "context": None,
        "pointer": {"path": f"lore/canon/{node_id}.md"},
        "content_sha256": sha(body),
        "anchors": {"components": [], "files": [], "symbols": []},
        "planned": {"components": [], "files": []},
        "relates_to": [],
        "supersedes": [],
        "superseded_by": "ADR-0001" if status == "SUPERSEDED" else None,
        "stale": False,
        "stale_reason": None,
        "legacy": None,
        "provenance": {
            "created_by": "agent",
            "created_at": at,
            "source": None,
            "session_id": None,
            "run_id": None,
        },
        "approval": approval,
        "history": history,
    }


def test_transitions_matrix(lore_repo: GitRepo, capsys, clock) -> None:
    """Every (status, verb) pair: the allowed transitions succeed, the rest exit 4 untouched."""
    data = load(lore_repo)
    by_id = {n["id"]: n for n in data["nodes"]}
    by_id["CANON-0001"]["stale"] = True
    by_id["CANON-0001"]["stale_reason"] = "impact"
    story = by_id["STORY-0001"]
    story["status"] = "REJECTED"
    story["history"].append(
        {
            "at": "2026-10-21T12:00:00+00:00",
            "from": "PROPOSED",
            "to": "REJECTED",
            "by": APPROVER,
            "warrant": "no",
            "ref": None,
        }
    )
    data["nodes"].append(_canon_node(lore_repo, "CANON-0004", "SUPERSEDED", "Old gamma."))
    data["nodes"].append(_canon_node(lore_repo, "CANON-0005", "RETIRED", "Old delta."))
    data["id_counters"]["CANON"] = 5
    save(lore_repo, data)
    assert findings(capsys)[0] == 0
    states = {
        "PROPOSED": "CANON-0003",
        "CANON": "CANON-0002",
        "CANON (stale)": "CANON-0001",
        "REJECTED": "STORY-0001",
        "SUPERSEDED": "CANON-0004",
        "RETIRED": "CANON-0005",
    }
    allowed = {
        ("PROPOSED", "approve"): "CANON",
        ("PROPOSED", "reject"): "REJECTED",
        ("CANON", "retire"): "RETIRED",
        ("CANON (stale)", "retire"): "RETIRED",
        ("CANON (stale)", "review"): "CANON",
        ("REJECTED", "reopen"): "PROPOSED",
    }
    saved_graph, saved_tree = graph_bytes(lore_repo), tree_bytes(lore_repo)
    for state, node_id in states.items():
        for verb in VERBS:
            nonce = write_pending(lore_repo, node_id)
            ticket(lore_repo, verb, node_id, nonce)
            code, _, err = gated(capsys, lore_repo, verb, node_id, nonce)
            expected = allowed.get((state, verb))
            if expected is None:
                assert code == 4, (state, verb, err)
                assert graph_bytes(lore_repo) == saved_graph, (state, verb)
                assert tree_bytes(lore_repo) == saved_tree, (state, verb)
            else:
                assert code == 0, (state, verb, err)
                after = node(lore_repo, node_id)
                assert after["status"] == expected, (state, verb)
                if verb == "review":
                    assert after["stale"] is False and after["stale_reason"] is None
                assert after["history"][-1]["to"] == expected
                assert not (lore_repo.path / PENDING / f"{node_id}.json").exists()
            # Restore the starting state for the next pair.
            (lore_repo.path / GRAPH).write_bytes(saved_graph)
            shutil.rmtree(lore_repo.path / "lore")
            for rel, content in saved_tree.items():
                (lore_repo.path / rel).parent.mkdir(parents=True, exist_ok=True)
                (lore_repo.path / rel).write_bytes(content)
            shutil.rmtree(lore_repo.path / ".agents" / "state" / "lore", ignore_errors=True)


def test_reject_keeps_node(lore_repo: GitRepo, capsys) -> None:
    """Reject keeps the node and its body file, with status REJECTED and a history entry."""
    body = (lore_repo.path / "lore/proposed/CANON-0003.md").read_bytes()
    nonce = request(capsys, lore_repo, "CANON-0003")
    ticket(lore_repo, "reject", "CANON-0003", nonce)
    code, out, err = gated(
        capsys, lore_repo, "reject", "CANON-0003", nonce, warrant(lore_repo, "Too vague.")
    )
    assert code == 0, err
    assert "PROPOSED -> REJECTED" in out
    rejected = node(lore_repo, "CANON-0003")
    assert rejected["status"] == "REJECTED"
    assert rejected["approval"] is None
    assert rejected["history"][-1]["from"] == "PROPOSED"
    assert rejected["history"][-1]["to"] == "REJECTED"
    assert rejected["history"][-1]["warrant"] == "Too vague."
    assert rejected["history"][-1]["by"] == APPROVER
    assert (lore_repo.path / "lore/proposed/CANON-0003.md").read_bytes() == body
    assert not (lore_repo.path / "lore/canon/CANON-0003.md").exists()
    assert findings(capsys)[0] == 0


def test_approve_moves_body_to_canon_file(lore_repo: GitRepo, capsys) -> None:
    """Canon bodies move to lore/canon/<ID>.md; decisions and stories get a byte-identical copy."""
    proposed = lore_repo.path / "lore/proposed/CANON-0003.md"
    body = proposed.read_bytes()
    approve_with_ticket(capsys, lore_repo, "CANON-0003")
    canon_file = lore_repo.path / "lore/canon/CANON-0003.md"
    assert canon_file.read_bytes() == body
    assert not proposed.exists()
    approved = node(lore_repo, "CANON-0003")
    assert approved["status"] == "CANON"
    assert approved["pointer"] == {"path": "lore/canon/CANON-0003.md"}
    assert approved["approval"]["approved_by"] == APPROVER
    assert approved["approval"]["method"] == "AskUserQuestion+PreToolUse-ask+ticket"
    assert approved["approval"]["content_sha256"] == sha(body)
    assert TS_PLUS.match(approved["approval"]["approved_at"])

    approve_with_ticket(capsys, lore_repo, "CANON-0003", verb="retire")
    assert node(lore_repo, "CANON-0003")["status"] == "RETIRED"
    assert canon_file.read_bytes() == body

    story_file = lore_repo.path / "lore/STORIES/STORY-0001-first-run.md"
    story_body = story_file.read_bytes()
    approve_with_ticket(capsys, lore_repo, "STORY-0001")
    assert story_file.read_bytes() == story_body
    assert (lore_repo.path / "lore/canon/STORY-0001.md").read_bytes() == story_body
    assert (
        node(lore_repo, "STORY-0001")["pointer"]["path"] == "lore/STORIES/STORY-0001-first-run.md"
    )
    assert findings(capsys)[0] == 0

    # One id per invocation: there is no batch approve.
    saved = graph_bytes(lore_repo)
    code, _, err = lore(
        capsys,
        "approve",
        "ADR-0001",
        "STORY-0001",
        "--nonce",
        "x",
        "--warrant-file",
        warrant(lore_repo),
    )
    assert code == 4 and "exactly one id" in err
    assert graph_bytes(lore_repo) == saved


# ---------------------------------------------------------------------------
# Gate: nonce, sha, pending, warrant, autonomy, ticket and TTY
# ---------------------------------------------------------------------------


def test_gate_nonce_sha_single_pending_cancel_expiry(lore_repo: GitRepo, capsys, clock) -> None:
    """A wrong nonce, a changed body, a second request, a cancel and an expiry are refused."""
    saved = graph_bytes(lore_repo)
    nonce = request(capsys, lore_repo, "CANON-0003")
    ticket(lore_repo, "approve", "CANON-0003", "f" * 32)
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", "f" * 32)
    assert code == 4 and "nonce" in err

    code, _, err = lore(capsys, "approval-request", "CANON-0002")
    assert code == 4 and "pending" in err
    assert not (lore_repo.path / PENDING / "CANON-0002.json").exists()

    body = lore_repo.path / "lore/proposed/CANON-0003.md"
    original = body.read_bytes()
    body.write_bytes(original + b"\nEdited after the request.\n")
    ticket(lore_repo, "approve", "CANON-0003", nonce)
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "changed" in err
    body.write_bytes(original)

    code, out, _ = lore(capsys, "approval-cancel", "CANON-0003")
    assert code == 0 and "deleted" in out
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "no pending" in err
    assert graph_bytes(lore_repo) == saved

    nonce = request(capsys, lore_repo, "CANON-0003")
    clock.now = FROZEN_NOW + timedelta(minutes=31)
    ticket(lore_repo, "approve", "CANON-0003", nonce)
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "expired" in err
    assert not (lore_repo.path / PENDING / "CANON-0003.json").exists()
    assert graph_bytes(lore_repo) == saved

    # An expired record no longer blocks a new request, and a fresh one goes through.
    nonce = request(capsys, lore_repo, "CANON-0003")
    ticket(lore_repo, "approve", "CANON-0003", nonce)
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 0, err


def test_warrant_file_required_and_not_title(lore_repo: GitRepo, capsys) -> None:
    """A missing, empty or title-equal warrant file exits 4 with nothing written."""
    nonce = request(capsys, lore_repo, "CANON-0003")
    used = ticket(lore_repo, "approve", "CANON-0003", nonce)
    saved_graph, saved_tree = graph_bytes(lore_repo), tree_bytes(lore_repo)
    for path in (
        str(lore_repo.scratch / "no-such-warrant.md"),
        warrant(lore_repo, ""),
        warrant(lore_repo, "  \n\t\n"),
        warrant(lore_repo, "Gamma waits"),
        warrant(lore_repo, "  gamma   WAITS \n"),
    ):
        code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce, path)
        assert code == 4, (path, err)
        assert "warrant" in err
        assert graph_bytes(lore_repo) == saved_graph
        assert tree_bytes(lore_repo) == saved_tree
        assert used.exists()
    code, _, err = gated(
        capsys, lore_repo, "approve", "CANON-0003", nonce, warrant(lore_repo, "Ship it.")
    )
    assert code == 0, err
    assert node(lore_repo, "CANON-0003")["approval"]["warrant"] == "Ship it."


def test_autonomous_refusal(lore_repo: GitRepo, capsys, monkeypatch) -> None:
    """HACF_AUTONOMOUS=1: every gated verb exits 4, even with a valid ticket."""
    monkeypatch.setenv("HACF_AUTONOMOUS", "1")
    saved = graph_bytes(lore_repo)
    code, _, err = lore(capsys, "approval-request", "CANON-0003")
    assert code == 4 and "HACF_AUTONOMOUS" in err
    for verb, node_id in (
        ("approve", "CANON-0003"),
        ("reject", "CANON-0003"),
        ("retire", "CANON-0002"),
        ("reopen", "CANON-0003"),
        ("review", "CANON-0001"),
    ):
        nonce = write_pending(lore_repo, node_id)
        used = ticket(lore_repo, verb, node_id, nonce)
        code, _, err = gated(capsys, lore_repo, verb, node_id, nonce)
        assert code == 4 and "HACF_AUTONOMOUS" in err, (verb, err)
        assert used.exists()
        assert graph_bytes(lore_repo) == saved


def test_gated_verb_requires_ticket(lore_repo: GitRepo, capsys, clock) -> None:
    """No ticket and no TTY exits 4; only an unexpired ticket for this verb, id and nonce works."""
    saved = graph_bytes(lore_repo)
    nonce = request(capsys, lore_repo, "CANON-0003")
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "ticket" in err
    now = hyper_lore._now()
    wrong = [
        ticket(lore_repo, "approve", "CANON-0002", nonce, name="other-id.json"),
        ticket(lore_repo, "reject", "CANON-0003", nonce, name="other-verb.json"),
        ticket(lore_repo, "approve", "CANON-0003", "e" * 32, name="other-nonce.json"),
        ticket(
            lore_repo,
            "approve",
            "CANON-0003",
            nonce,
            created=now - timedelta(seconds=200),
            name="expired.json",
        ),
        ticket(lore_repo, "approve", "CANON-0003", nonce, ttl=600, name="too-long.json"),
        ticket(
            lore_repo,
            "approve",
            "CANON-0003",
            nonce,
            created=now + timedelta(minutes=10),
            name="future.json",
        ),
    ]
    malformed = lore_repo.path / TICKETS / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "ticket" in err
    assert graph_bytes(lore_repo) == saved
    assert all(path.exists() for path in [*wrong, malformed])

    good = ticket(lore_repo, "approve", "CANON-0003", nonce, name="good.json")
    code, out, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 0, err
    assert "AskUserQuestion+PreToolUse-ask+ticket" in out
    assert not good.exists()
    assert all(path.exists() for path in [*wrong, malformed])
    assert node(lore_repo, "CANON-0003")["status"] == "CANON"


def _run_in_pty(
    repo: GitRepo, args: list[str], answer: str, *, deadline_s: float = 60
) -> tuple[int, str, str]:
    """Run hyper_lore.py with a pty as stdin and stdout, type answer, and return its result.

    The child leads its own session (hyper_lore.py judges the tty by isatty() only), so its
    process group can be killed without reaching pytest. A child still running at deadline_s has
    its group killed and reaped and the call raises AssertionError; stderr is read only after the
    child is reaped, so a stuck child (or a grandchild holding stderr) cannot block the suite.
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=subprocess_env(repo),
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    output = b""
    sent = False
    deadline = time.monotonic() + deadline_s
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 1)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO: the child closed the terminal
                break
            if not chunk:
                break
            output += chunk
            if not sent and b"to confirm:" in output:
                os.write(master, answer.encode())
                sent = True
        try:
            code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _killpg(proc.pid)
            proc.wait()
            raise AssertionError(
                f"_run_in_pty: hyper_lore.py (pid {proc.pid}) still running after {deadline_s}s;"
                f" killed\n{output.decode(errors='replace')}"
            ) from None
        stderr = proc.stderr.read().decode()
    finally:
        if proc.poll() is None:
            _killpg(proc.pid)
            proc.wait()
        proc.stderr.close()
        os.close(master)
    return code, output.decode(errors="replace"), stderr


def _killpg(pgid: int) -> None:
    """SIGKILL a process group; one that is already gone is fine."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_run_in_pty_kills_on_deadline_lore(lore_repo: GitRepo, tmp_path: Path, monkeypatch) -> None:
    """A child (and its grandchild) still running at the deadline is killed; the helper raises."""
    pidfile = tmp_path / "grandchild.pid"
    stuck = tmp_path / "stuck.py"
    stuck.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '600'])  # inherits stderr and never closes it\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "SCRIPT", stuck)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="still running after"):
        _run_in_pty(lore_repo, [], "", deadline_s=2)
    assert time.monotonic() - started < 2 + 10
    grandchild = int(pidfile.read_text(encoding="utf-8"))
    # SIGKILLed with its group; its new parent (init or a subreaper) reaps it within moments.
    gone_by = time.monotonic() + 5
    while Path(f"/proc/{grandchild}").exists() and time.monotonic() < gone_by:
        time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)


def test_ticketless_tty_confirmation(lore_repo: GitRepo, capsys) -> None:
    """With a pty, typing the node id proceeds (method tty-confirm); anything else refuses."""
    nonce = request(capsys, lore_repo, "CANON-0003")
    args = ["approve", "CANON-0003", "--nonce", nonce, "--warrant-file", warrant(lore_repo)]
    saved = graph_bytes(lore_repo)
    code, output, err = _run_in_pty(lore_repo, args, "CANON-0002\n")
    assert "Type the node id (CANON-0003) to confirm" in output
    assert code == 4, (output, err)
    assert graph_bytes(lore_repo) == saved
    code, output, err = _run_in_pty(lore_repo, args, "CANON-0003\n")
    assert code == 0, (output, err)
    approved = node(lore_repo, "CANON-0003")
    assert approved["status"] == "CANON"
    assert approved["approval"]["method"] == "tty-confirm"


def test_approval_request_lists_superseded_ids(lore_repo: GitRepo, capsys) -> None:
    """A node superseding two CANON nodes names both in the output and in the pending record."""
    code, out, err = lore(capsys, "approval-request", "ADR-0001")
    assert code == 0, err
    assert "supersedes: CANON-0001, CANON-0002" in out
    assert "retires: none" in out and "reanchors: none" in out
    body = (lore_repo.path / "lore/ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md").read_text()
    assert f"----- BEGIN HASHED BODY -----\n{body}----- END HASHED BODY -----" in out
    record = json.loads((lore_repo.path / PENDING / "ADR-0001.json").read_text())
    assert record["schema"] == "hacf.lore-pending/v1"
    assert record["affects"] == {
        "supersedes": ["CANON-0001", "CANON-0002"],
        "retires": [],
        "reanchors": [],
    }
    assert record["title"] == "Replace the alpha and beta canon"
    assert re.fullmatch(r"[0-9a-f]{32}", record["nonce"])
    assert hyper_lore._parse_aware(record["expires_at"]) - hyper_lore._parse_aware(
        record["created_at"]
    ) == timedelta(minutes=30)

    canon_files = tree_bytes(lore_repo, "lore/canon")
    ticket(lore_repo, "approve", "ADR-0001", record["nonce"])
    code, _, err = gated(capsys, lore_repo, "approve", "ADR-0001", record["nonce"])
    assert code == 0, err
    for old in ("CANON-0001", "CANON-0002"):
        superseded = node(lore_repo, old)
        assert superseded["status"] == "SUPERSEDED"
        assert superseded["superseded_by"] == "ADR-0001"
        assert superseded["history"][-1]["to"] == "SUPERSEDED"
    after = tree_bytes(lore_repo, "lore/canon")
    assert {k: v for k, v in after.items() if k in canon_files} == canon_files
    assert findings(capsys)[0] == 0

    # A CANON node's request names itself under retires.
    code, out, _ = lore(capsys, "approval-request", "ADR-0001")
    assert code == 0 and "retires: ADR-0001" in out


# ---------------------------------------------------------------------------
# validate, list and pointers
# ---------------------------------------------------------------------------


def test_validate_tombstoned_anchor_warns(lore_repo: GitRepo, capsys) -> None:
    """A tombstoned id or a deleted file warns and exits 0; an unknown id exits 1."""
    data = load(lore_repo)
    by_id = {n["id"]: n for n in data["nodes"]}
    by_id["CANON-0001"]["anchors"]["components"] = ["gamma_old"]
    save(lore_repo, data)
    lore_repo.git("rm", "-q", "src/beta.py")
    lore_repo.commit("remove gamma and beta", GRAPH)
    saved = graph_bytes(lore_repo)

    code, report = findings(capsys)
    assert code == 0
    assert ("CANON-0001", "anchor_removed") in rules_of(report, "warnings")
    assert ("CANON-0002", "anchor_file_missing") in rules_of(report, "warnings")
    assert report["errors"] == []
    review = {item["id"]: item["reasons"] for item in report["stale_review"]}
    assert "anchor_removed:gamma_old" in review["CANON-0001"]
    assert "anchor_file_missing:src/beta.py" in review["CANON-0002"]
    code, out, _ = lore(capsys, "validate")
    assert code == 0 and out.splitlines()[-1] == "validate: ok"
    assert "stale review (gated): CANON-0001: anchor_removed:gamma_old" in out
    assert lore(capsys, "validate", "--strict")[0] == 1
    # validate is read-only: nothing marked stale, nothing written.
    assert graph_bytes(lore_repo) == saved
    assert lore_repo.git("status", "--porcelain") == ""

    by_id["CANON-0001"]["anchors"]["components"] = ["no_such_component"]
    save(lore_repo, data)
    code, report = findings(capsys)
    assert code == 1
    assert ("CANON-0001", "anchor_unknown") in rules_of(report, "errors")


def test_validate_canon_without_file_errors(lore_repo: GitRepo, capsys) -> None:
    """A CANON node whose lore/canon/<ID>.md is missing makes validate exit 1."""
    data = load(lore_repo)
    story = next(n for n in data["nodes"] if n["id"] == "STORY-0001")
    nonce_text = "approved story"
    story["status"] = "CANON"
    story["approval"] = {
        "approved_by": APPROVER,
        "approved_at": "2026-10-21T12:00:00+00:00",
        "warrant": nonce_text,
        "warrant_sha256": sha(nonce_text.encode()),
        "method": "tty-confirm",
        "content_sha256": story["content_sha256"],
        "nonce": "c" * 32,
    }
    story["history"].append(
        {
            "at": "2026-10-21T12:00:00+00:00",
            "from": "PROPOSED",
            "to": "CANON",
            "by": APPROVER,
            "warrant": nonce_text,
            "ref": None,
        }
    )
    save(lore_repo, data)
    code, report = findings(capsys)
    assert code == 1
    assert ("STORY-0001", "canon_file_missing") in rules_of(report, "errors")
    (lore_repo.path / "lore/canon/CANON-0002.md").unlink()
    code, report = findings(capsys)
    assert code == 1
    assert ("CANON-0002", "canon_file_missing") in rules_of(report, "errors")


def test_list_canon_with_commit(bare_repo: GitRepo, capsys) -> None:
    """Three commits (propose, approve, unrelated edit): the approve commit is printed."""
    code, _, err = lore(
        capsys,
        "propose",
        "--type",
        "canon",
        "--title",
        "Lock first",
        "--body-file",
        str(BODIES / "canon_body.md"),
    )
    assert code == 0, err
    bare_repo.commit("propose CANON-0001", GRAPH, "lore")
    approve_with_ticket(capsys, bare_repo, "CANON-0001")
    approve_commit = bare_repo.commit("approve CANON-0001", GRAPH, "lore")
    bare_repo.write("README.md", "# Edited\n")
    bare_repo.commit("unrelated edit", "README.md")
    code, out, _ = lore(capsys, "list", "--status", "CANON", "--with-commit")
    assert code == 0
    line = next(line for line in out.splitlines() if line.startswith("CANON-0001"))
    assert approve_commit in line.split()
    code, out, _ = lore(capsys, "list", "--status", "CANON", "--with-commit", "--json")
    rows = json.loads(out)
    assert [(row["id"], row["canon_commit"], row["binding"]) for row in rows] == [
        ("CANON-0001", approve_commit, True)
    ]


def test_pointer_validation(lore_repo: GitRepo, capsys) -> None:
    """A missing or symlinked pointer, a path outside lore/ and a sha mismatch are reported."""
    data = load(lore_repo)
    by_id = {n["id"]: n for n in data["nodes"]}
    by_id["STORY-0001"]["pointer"]["path"] = "lore/STORIES/STORY-0001-missing.md"
    adr = lore_repo.path / "lore/ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md"
    adr.unlink()
    adr.symlink_to(lore_repo.path / "lore/canon/CANON-0001.md")
    by_id["CANON-0003"]["pointer"]["path"] = "docs/CANON-0003.md"
    save(lore_repo, data)
    (lore_repo.path / "lore/canon/CANON-0002.md").write_text("# Rewritten\n", encoding="utf-8")
    code, report = findings(capsys)
    assert code == 1
    errors, warnings = rules_of(report, "errors"), rules_of(report, "warnings")
    assert ("STORY-0001", "pointer_missing") in errors
    assert ("ADR-0001", "pointer_symlink") in errors
    assert ("CANON-0003", "pointer_outside_lore") in errors
    assert ("CANON-0002", "content_changed_since_approval") in warnings


def test_show_list_graph_binding(lore_repo: GitRepo, capsys) -> None:
    """Show and list mark CANON and PROPOSED user decisions binding; graph derives the edges."""
    code, out, _ = lore(capsys, "list", "--json")
    rows = {row["id"]: row["binding"] for row in json.loads(out)}
    assert rows == {
        "CANON-0001": True,
        "CANON-0002": True,
        "ADR-0001": True,
        "STORY-0001": False,
        "CANON-0003": False,
    }
    code, out, _ = lore(capsys, "show", "CANON-0002")
    assert code == 0 and yaml.safe_load(out)["binding"] is True
    assert lore(capsys, "show", "ADR-0099")[0] == 2
    code, out, _ = lore(capsys, "graph", "--json")
    edges = json.loads(out)["edges"]
    assert {"kind": "supersedes", "from": "ADR-0001", "to": "CANON-0002"} in edges
    assert {"kind": "relates_to", "from": "ADR-0001", "to": "STORY-0001"} in edges
    assert {
        "kind": "anchors",
        "from": "CANON-0001",
        "components": ["alpha_engine"],
        "files": ["src/alpha.py"],
        "symbols": [],
    } in edges


# ---------------------------------------------------------------------------
# Timestamps, outbox, anchors, concurrency
# ---------------------------------------------------------------------------


def test_timestamps_z_suffix_on_310(lore_repo: GitRepo) -> None:
    """A pending record and ticket with Z timestamps parse under 3.10; new records use +00:00."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    body = (lore_repo.path / "lore/proposed/CANON-0003.md").read_bytes()
    record = {
        "schema": "hacf.lore-pending/v1",
        "id": "CANON-0003",
        "title": "Gamma waits",
        "content_sha256": sha(body),
        "nonce": "d" * 32,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        "affects": {"supersedes": [], "retires": [], "reanchors": []},
    }
    (lore_repo.path / PENDING).mkdir(parents=True)
    (lore_repo.path / PENDING / "CANON-0003.json").write_text(json.dumps(record), encoding="utf-8")
    ticket(lore_repo, "approve", "CANON-0003", "d" * 32, created=now, z=True)
    args = ["approve", "CANON-0003", "--nonce", "d" * 32, "--warrant-file", warrant(lore_repo)]
    proc = run_cli(lore_repo, *args, interpreter=PY310)
    assert proc.returncode == 0, proc.stderr
    assert TS_PLUS.match(node(lore_repo, "CANON-0003")["approval"]["approved_at"])

    proc = run_cli(lore_repo, "approval-request", "CANON-0002", interpreter=PY310)
    assert proc.returncode == 0, proc.stderr
    record = json.loads((lore_repo.path / PENDING / "CANON-0002.json").read_text())
    assert TS_PLUS.match(record["created_at"]) and TS_PLUS.match(record["expires_at"])


def test_outbox_rejects_approvals(lore_repo: GitRepo, capsys, monkeypatch, tmp_path) -> None:
    """An outbox with an approve entry is refused whole; propose, note and stale entries apply."""
    mission = lore_repo.path / "missions" / "m1"
    mission.mkdir(parents=True)
    notes = mission / "NOTES.md"
    notes.write_text(
        "---\nmission: m1\n---\n\n# Notes - m1\n\n## Notes\n\n- 2026-10-21 - mission scaffolded.\n",
        encoding="utf-8",
    )
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("HACF_OUTBOX", str(outbox))
    saved_graph, saved_notes = graph_bytes(lore_repo), notes.read_bytes()

    code, out, err = lore(
        capsys,
        "propose",
        "--type",
        "decision",
        "--title",
        "Outbox decision",
        "--body-file",
        str(BODIES / "decision_body.md"),
        "--anchors-components",
        "beta_store",
    )
    assert code == 0 and "outbox entry" in out, err
    assert (
        lore(
            capsys,
            "note",
            "--mission",
            "missions/m1",
            "--text",
            "seen in a phase",
            "--agent",
            "hyper-execute",
            "--slice",
            "S99",
        )[0]
        == 0
    )
    assert lore(capsys, "stale", "--nodes", "beta_store", "--reason", "engine phase")[0] == 0
    assert hyper_lore.stale_from_impact(["alpha_engine"], []) == ["CANON-0001", "ADR-0001"]
    for args in (("approval-request", "CANON-0003"), ("resolve-anchors",), ("sync",)):
        assert lore(capsys, *args)[0] == 4, args
    nonce = write_pending(lore_repo, "CANON-0003")
    ticket(lore_repo, "approve", "CANON-0003", nonce)
    code, _, err = gated(capsys, lore_repo, "approve", "CANON-0003", nonce)
    assert code == 4 and "HACF_OUTBOX" in err
    assert graph_bytes(lore_repo) == saved_graph and notes.read_bytes() == saved_notes
    assert sorted(p.name for p in outbox.iterdir()) == [
        "000001-propose.json",
        "000002-note.json",
        "000003-stale.json",
        "000004-stale.json",
    ]

    hyper_ledger.write_outbox_entry(outbox, "mark", {"ids": ["alpha_engine"], "status": "dirty"})
    approve_entry = hyper_lore.write_outbox_entry(outbox, "approve", {"id": "CANON-0003"})
    monkeypatch.delenv("HACF_OUTBOX")
    code, _, err = lore(capsys, "apply-outbox", str(outbox))
    assert code == 4 and "000006-approve.json" in err
    assert graph_bytes(lore_repo) == saved_graph and notes.read_bytes() == saved_notes
    assert not list((lore_repo.path / "lore/ADRs").glob("ADR-0002*"))

    approve_entry.unlink()
    code, out, err = lore(capsys, "apply-outbox", str(outbox))
    assert code == 0, err
    assert "skipped 000005-mark.json" in out
    assert node(lore_repo, "ADR-0002")["title"] == "Outbox decision"
    assert node(lore_repo, "CANON-0002")["stale_reason"] == "engine phase"
    assert node(lore_repo, "CANON-0001")["stale_reason"] == "impact"
    last = notes.read_text().splitlines()[-1]
    assert re.fullmatch(
        r"- \d{4}-\d{2}-\d{2} \[field-note\] \(agent:hyper-execute, slice S99\) "
        r"seen in a phase anchors: none",
        last,
    )


def test_resolve_planned_anchors(lore_repo: GitRepo, capsys) -> None:
    """Planned anchors that now exist move into anchors; a second run changes nothing."""
    saved = graph_bytes(lore_repo)
    code, out, _ = lore(capsys, "resolve-anchors")
    assert code == 0 and "no change" in out
    assert graph_bytes(lore_repo) == saved
    ledger = yaml.safe_load((lore_repo.path / LEDGER).read_text())
    ledger["nodes"].append(
        {
            "id": "delta_future",
            "dimension": "Module",
            "status": "dirty",
            "description": "Now built.",
            "inputs": [],
            "outputs": [],
            "edges": {"depends_on": [], "implements": [], "member_of": ["orchestration_layer"]},
        }
    )
    (lore_repo.path / LEDGER).write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")
    lore_repo.write("src/delta.py", '"""Delta."""\n')
    code, out, err = lore(capsys, "resolve-anchors")
    assert code == 0, err
    resolved = node(lore_repo, "ADR-0001")
    assert resolved["anchors"]["components"] == ["alpha_engine", "delta_future"]
    assert resolved["anchors"]["files"] == ["src/delta.py"]
    assert resolved["planned"] == {"components": [], "files": []}
    once = graph_bytes(lore_repo)
    code, out, _ = lore(capsys, "resolve-anchors")
    assert code == 0 and "no change" in out
    assert graph_bytes(lore_repo) == once


_WORKER = """\
import sys
sys.path.insert(0, sys.argv[1])
import hyper_lore
for i in range(25):
    title = f"{sys.argv[2]} {i}"
    code = hyper_lore.main(
        ["propose", "--type", "decision", "--title", title, "--body-file", sys.argv[3]]
    )
    if code != 0:
        sys.exit(code)
"""


def test_concurrent_writers(bare_repo: GitRepo, tmp_path: Path) -> None:
    """Two processes x 25 proposes: 50 unique ids, and the graph parses after every write."""
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    body = str(BODIES / "decision_body.md")
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(SCRIPTS_DIR), name, body],
            cwd=bare_repo.path,
            env=subprocess_env(bare_repo),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for name in ("left", "right")
    ]
    reads = 0
    deadline = time.monotonic() + 240
    while any(proc.poll() is None for proc in procs) and time.monotonic() < deadline:
        path = bare_repo.path / GRAPH
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert isinstance(data, dict) and isinstance(data["nodes"], list)
            reads += 1
        time.sleep(0.005)
    for proc in procs:
        assert proc.wait(timeout=60) == 0, proc.stderr.read().decode()
        proc.stderr.close()
    data = load(bare_repo)
    ids = [n["id"] for n in data["nodes"]]
    assert len(ids) == 50 and len(set(ids)) == 50
    assert sorted(ids) == [f"ADR-{n:04d}" for n in range(1, 51)]
    assert data["id_counters"]["ADR"] == 50
    assert len(list((bare_repo.path / "lore/ADRs").iterdir())) == 50
    assert reads > 0
    assert run_cli(bare_repo, "validate").returncode == 0


# ---------------------------------------------------------------------------
# Notes, stale, sync and the missing graph
# ---------------------------------------------------------------------------


def test_note_mission_and_legacy(bare_repo: GitRepo, capsys, clock, monkeypatch) -> None:
    """Notes land in the mission NOTES.md or activeContext's legacy section, never elsewhere."""
    mission = bare_repo.path / "missions" / "release-1"
    mission.mkdir(parents=True)
    (mission / "NOTES.md").write_text(
        "# Notes - r1\n\n## Notes\n\n- 2026-10-01 - mission scaffolded.\n\n## Later\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HACF_SLICE", "S42")
    code, _, err = lore(
        capsys,
        "note",
        "--mission",
        "missions/release-1",
        "--text",
        "lock order matters",
        "--agent",
        "hyper-execute",
        "--anchors",
        "alpha_engine,src/a.py",
    )
    assert code == 0, err
    lines = (mission / "NOTES.md").read_text().splitlines()
    assert (
        lines[5] == "- 2026-10-21 [field-note] (agent:hyper-execute, slice S42) lock order matters "
        "anchors: alpha_engine, src/a.py"
    )
    assert lines[-1] == "## Later"
    assert (bare_repo.path / GRAPH).exists()  # note creates the empty graph on first use

    context = bare_repo.path / ".agents/memory/activeContext.md"
    context.parent.mkdir(parents=True)
    protected = (
        "## v0.6.0 Bootstrap Operating Procedure (read first)\n\nRules.\n\n"
        "<!-- BEGIN v060-completed (appended by dev/scripts/v060_gate.py finish) -->\n"
        "- S00 (NN 00): done\n<!-- END v060-completed -->\n"
    )
    context.write_text(f"\n# Active Context\n\n{protected}\n## Purpose\n\nNow.\n", encoding="utf-8")
    for text in ("first legacy note", "second legacy note"):
        code, _, err = lore(
            capsys,
            "note",
            "--legacy",
            "--source",
            "spec/compiled/MiniPRD_08_X.md",
            "--text",
            text,
            "--kind",
            "gotcha",
        )
        assert code == 0, err
    content = context.read_text()
    prefix = "- 2026-10-21 [gotcha] (agent:unknown, slice spec/compiled/MiniPRD_08_X.md)"
    assert protected in content
    assert content.endswith(
        "## Field Notes (legacy)\n\n"
        f"{prefix} first legacy note anchors: none\n"
        f"{prefix} second legacy note anchors: none\n"
    )

    # A Field Notes section inside the protected section is refused rather than written there.
    context.write_text(
        protected.replace("Rules.", "## Field Notes (legacy)\n\nRules.\n\n## Completed"),
        encoding="utf-8",
    )
    saved = context.read_bytes()
    code, _, err = lore(capsys, "note", "--legacy", "--source", "x.md", "--text", "nope")
    assert code == 4 and "protected" in err
    assert context.read_bytes() == saved

    (bare_repo.path / "spec/process").mkdir(parents=True)
    (bare_repo.path / "spec/process/NOTES.md").write_text("## Notes\n", encoding="utf-8")
    assert lore(capsys, "note", "--mission", "spec/process", "--text", "x")[0] == 4
    (bare_repo.path / "missions/empty").mkdir()
    assert lore(capsys, "note", "--mission", "missions/empty", "--text", "x")[0] == 2
    assert lore(capsys, "note", "--mission", "missions/release-1", "--text", "two\nlines")[0] == 2


def test_stale_and_stale_from_impact(lore_repo: GitRepo, capsys, monkeypatch) -> None:
    """Stale and the updater's hook mark live anchored nodes once and write only on change."""
    code, out, err = lore(capsys, "stale", "--files", "src/beta.py", "--reason", "rewrote beta")
    assert code == 0, err
    assert node(lore_repo, "CANON-0002")["stale_reason"] == "rewrote beta"
    assert node(lore_repo, "CANON-0001")["stale"] is False
    code, out, _ = lore(capsys, "stale", "--files", "src/**", "--reason", "again")
    assert out.strip() == "stale: marked CANON-0001"  # src/** reaches CANON-0001's src/alpha.py
    assert node(lore_repo, "CANON-0002")["stale_reason"] == "rewrote beta"  # the first reason stays
    code, out, _ = lore(capsys, "stale", "--nodes", "alpha_engine", "--reason", "again")
    assert out.strip() == "stale: marked ADR-0001"  # the PROPOSED user decision
    saved = graph_bytes(lore_repo)
    code, out, _ = lore(capsys, "stale", "--nodes", "alpha_engine", "--reason", "again")
    assert out.strip() == "stale: no change"
    assert graph_bytes(lore_repo) == saved
    assert hyper_lore.stale_from_impact(["alpha_engine", "beta_store"], ["src/alpha.py"]) == []
    assert graph_bytes(lore_repo) == saved
    assert lore(capsys, "stale", "--reason", "nothing")[0] == 2


def test_updater_hook_marks_stale(lore_repo: GitRepo) -> None:
    """The updater's post-impact hook reaches stale_from_impact (reason impact)."""
    proc = subprocess.run(
        [sys.executable, str(UPDATER), LEDGER, "alpha_engine"],
        cwd=lore_repo.path,
        env=subprocess_env(lore_repo),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "lore: marked stale: CANON-0001, CANON-0002, ADR-0001" in proc.stderr
    assert {n["id"]: n["stale_reason"] for n in load(lore_repo)["nodes"] if n["stale"]} == {
        "CANON-0001": "impact",
        "CANON-0002": "impact",
        "ADR-0001": "impact",
    }


def test_sync_marks_changed_canon(lore_repo: GitRepo, capsys) -> None:
    """Sync refreshes PROPOSED shas and marks a CANON node whose body changed stale."""
    adr = lore_repo.path / "lore/ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md"
    adr.write_text(adr.read_text() + "\nA PROPOSED edit.\n", encoding="utf-8")
    (lore_repo.path / "lore/canon/CANON-0002.md").write_text("# Changed\n", encoding="utf-8")
    code, out, err = lore(capsys, "sync")
    assert code == 0, err
    assert node(lore_repo, "ADR-0001")["content_sha256"] == sha(adr.read_bytes())
    changed = node(lore_repo, "CANON-0002")
    assert changed["stale"] is True
    assert changed["stale_reason"] == "content_changed_since_approval"
    assert changed["content_sha256"] != sha(
        (lore_repo.path / "lore/canon/CANON-0002.md").read_bytes()
    )
    saved = graph_bytes(lore_repo)
    code, out, _ = lore(capsys, "sync")
    assert code == 0 and "no change" in out
    assert graph_bytes(lore_repo) == saved


def test_missing_graph(bare_repo: GitRepo, capsys) -> None:
    """Without a graph, read commands print the notice and writers other than propose/note no-op."""
    for args in (("validate",), ("show", "ADR-0001"), ("list",)):
        code, out, _ = lore(capsys, *args)
        assert code == 0 and "no lore graph (lore_hypergraph.yml missing)" in out, args
    code, out, err = lore(capsys, "graph", "--json")
    assert code == 0 and json.loads(out)["nodes"] == [] and "no lore graph" in err
    for args in (("stale", "--nodes", "a_b", "--reason", "r"), ("resolve-anchors",), ("sync",)):
        code, out, _ = lore(capsys, *args)
        assert code == 0 and "no lore graph" in out, args
    assert hyper_lore.stale_from_impact(["a_b"], ["x.py"]) == []
    assert lore(capsys, "approval-request", "ADR-0001")[0] == 4
    assert not (bare_repo.path / GRAPH).exists()


def test_propose_writes_body_and_refuses_bad_writes(bare_repo: GitRepo, capsys) -> None:
    """Propose writes the body verbatim; an existing target or a new error writes nothing."""
    body = BODIES / "story_body.md"
    code, out, err = lore(
        capsys,
        "propose",
        "--type",
        "story",
        "--title",
        "The First Run!",
        "--body-file",
        str(body),
        "--source",
        "S05a",
        "--anchors-files",
        "README.md",
    )
    assert code == 0, err
    created = bare_repo.path / "lore/STORIES/STORY-0001-the-first-run.md"
    assert created.read_bytes() == body.read_bytes()
    story = node(bare_repo, "STORY-0001")
    assert story["status"] == "PROPOSED" and story["origin"] == "agent"
    assert story["provenance"]["source"] == "S05a"
    assert story["history"] == [
        {
            "at": story["provenance"]["created_at"],
            "from": None,
            "to": "PROPOSED",
            "by": "agent",
            "warrant": None,
            "ref": "S05a",
        }
    ]
    saved = graph_bytes(bare_repo)
    bare_repo.write("lore/STORIES/STORY-0002-taken.md", "# Taken\n")
    code, _, err = lore(
        capsys, "propose", "--type", "story", "--title", "Taken", "--body-file", str(body)
    )
    assert code == 5 and "already exists" in err
    assert graph_bytes(bare_repo) == saved
    # An anchor to an unknown component adds an error, so nothing is written (no ledger here
    # leaves components unchecked; give it one).
    bare_repo.write(LEDGER, (BASE / "ledger.yml").read_text())
    code, _, err = lore(
        capsys,
        "propose",
        "--type",
        "decision",
        "--title",
        "Bad anchor",
        "--body-file",
        str(body),
        "--anchors-components",
        "nope_component",
    )
    assert code == 1 and "anchor_unknown" in err
    assert graph_bytes(bare_repo) == saved
    assert not list((bare_repo.path / "lore").glob("ADRs/*"))
