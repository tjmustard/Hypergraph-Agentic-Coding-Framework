"""Deterministic tests for hyper_lore.py (S05b): index, importers, merge drivers, renumber.

Every test works in a temp git repository. The base tree is S05a's `dev/tests/fixtures/lore/base/`
(two CANON canon nodes, a PROPOSED decision that supersedes both, a PROPOSED story and a PROPOSED
canon body); `dev/tests/fixtures/lore/adr/` holds legacy docs/adr trees, and
test_bootstrap_drafts_import copies this repository's own lore drafts, read-only. The merge tests
point the repo-local drivers at this checkout's hyper_lore.py with the running interpreter. Gated
verbs run only inside the temp repositories, with tickets written there.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

import _runtime
import hyper_lore
import pytest
import yaml
from conftest import FIXTURES_DIR, FROZEN_NOW, SCRIPTS_DIR, GitRepo

SCRIPT = SCRIPTS_DIR / "hyper_lore.py"
BASE = FIXTURES_DIR / "lore" / "base"
ADR_FIXTURES = FIXTURES_DIR / "lore" / "adr"
GRAPH = "lore_hypergraph.yml"
LEDGER = "spec/compiled/architecture.yml"
MAP = "lore/MAP.md"
CANON_MD = "lore/CANON.md"
NEEDS_INDEX = ".agents/state/lore/needs-index"
PENDING = ".agents/state/lore/pending"
TICKETS = ".agents/state/lore/tickets"
BEGIN = "<!-- BEGIN HACF GENERATED: lore-map -->"
END = "<!-- END HACF GENERATED: lore-map -->"
FULL_LIST = "full list: `hyper_lore.py list --status REJECTED`"
HACF_ENV = ("HACF_OUTBOX", "HACF_AUTONOMOUS", "HACF_SLICE", "HACF_RUN_ID")
AT = "2026-10-21T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep outer git, merge and engine variables away; never give the CLI a TTY in-process."""
    for key in list(os.environ):
        if key.startswith(("GIT_", "GITHEAD_")) or key in HACF_ENV:
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
    repo.write(".gitignore", ".agents/state/\n")
    for key, value in repo.env.items():
        if key not in HACF_ENV:
            monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def bare_repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return tmp_git_repo with git identity, an ignored .agents/state/ and cwd inside it."""
    return _enter(tmp_git_repo, monkeypatch)


@pytest.fixture
def lore_repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return tmp_git_repo holding S05a's fixture ledger, graph and lore files, committed."""
    root = tmp_git_repo.path
    for name in ("lore", "src"):
        shutil.copytree(BASE / name, root / name)
    (root / "spec" / "compiled").mkdir(parents=True)
    shutil.copyfile(BASE / "ledger.yml", root / LEDGER)
    shutil.copyfile(BASE / "graph.yml", root / GRAPH)
    repo = _enter(tmp_git_repo, monkeypatch)
    repo.commit("fixture lore")
    return repo


def lore(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, str, str]:
    """Run hyper_lore.main in-process; return (exit code, stdout, stderr)."""
    capsys.readouterr()
    code = hyper_lore.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def load(repo: GitRepo) -> dict:
    """Return the parsed graph of the repository."""
    return yaml.safe_load((repo.path / GRAPH).read_text(encoding="utf-8"))


def save(repo: GitRepo, data: dict) -> None:
    """Write data as the repository's graph."""
    (repo.path / GRAPH).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def nodes(repo: GitRepo) -> dict[str, dict]:
    """Return the repository's graph nodes by id."""
    return {n["id"]: n for n in load(repo)["nodes"]}


def read(repo: GitRepo, rel: str) -> bytes:
    """Return the bytes of a repository file."""
    return (repo.path / rel).read_bytes()


def sha(data: bytes) -> str:
    """Return the sha256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()


def region(text: str) -> list[str]:
    """Return the lines between the lore-map markers (CR stripped)."""
    lines = [line.rstrip("\r") for line in text.split("\n")]
    return lines[lines.index(BEGIN) + 1 : lines.index(END)]


def section(text: str, heading: str) -> list[str]:
    """Return the non-blank lines of one `## heading` section of the region."""
    lines = region(text)
    start = lines.index(f"## {heading}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [line for line in lines[start:end] if line.strip()]


def validate(capsys) -> tuple[int, str]:
    """Run validate; return (exit code, stdout)."""
    code, out, _ = lore(capsys, "validate")
    return code, out


def gate(capsys, repo: GitRepo, node_id: str, verb: str = "approve") -> str:
    """Run approval-request, write the matching hook ticket and run one gated verb."""
    code, _, err = lore(capsys, "approval-request", node_id)
    assert code == 0, err
    record = json.loads((repo.path / PENDING / f"{node_id}.json").read_text(encoding="utf-8"))
    now = hyper_lore._now()
    ticket = {
        "schema": "hacf.lore-ticket/v1",
        "verb": verb,
        "id": node_id,
        "nonce": record["nonce"],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=120)).isoformat(),
    }
    target = repo.path / TICKETS / f"{verb}-{node_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ticket), encoding="utf-8")
    warrant = repo.scratch / f"warrant-{verb}-{node_id}.md"
    warrant.write_text(f"The user said: {verb} {node_id}, it is right.", encoding="utf-8")
    code, out, err = lore(
        capsys, verb, node_id, "--nonce", record["nonce"], "--warrant-file", str(warrant)
    )
    assert code == 0, err
    return out


def propose(capsys, repo: GitRepo, kind: str, title: str) -> str:
    """Propose one node through the CLI and return its output."""
    body = repo.scratch / f"body-{sha(title.encode())[:8]}.md"
    body.write_text(f"# {title}\n\nBody of {title}.\n", encoding="utf-8")
    code, out, err = lore(
        capsys, "propose", "--type", kind, "--title", title, "--body-file", str(body)
    )
    assert code == 0, err
    return out


def with_map(capsys, repo: GitRepo, intro: str = "# Map\n") -> None:
    """Write MAP.md's intro and run index."""
    repo.write(MAP, intro)
    code, _, err = lore(capsys, "index")
    assert code == 0, err


def install_drivers(capsys, repo: GitRepo) -> None:
    """Run install-drivers, then point both drivers at this checkout's script."""
    code, _, err = lore(capsys, "install-drivers")
    assert code == 0, err
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT))}"
    for name, verb in (("hacf-lore", "merge-driver"), ("hacf-generated", "merge-generated")):
        repo.git("config", f"merge.{name}.driver", f"{command} {verb} %O %A %B %P")


def merge(repo: GitRepo, branch: str) -> subprocess.CompletedProcess:
    """Run `git merge` of branch into the current branch (the drivers run as subprocesses)."""
    return repo.run(["git", "merge", "--no-edit", "-q", branch])


def story_node(node_id: str, rel: str, body: bytes, rejected_at: str, warrant: str) -> dict:
    """Return a REJECTED story node whose rejection happened at rejected_at."""
    return {
        "id": node_id,
        "type": "story",
        "title": f"Rejected story {node_id}",
        "status": "REJECTED",
        "origin": "agent",
        "context": None,
        "pointer": {"path": rel},
        "content_sha256": sha(body),
        "anchors": {"components": [], "files": [], "symbols": []},
        "planned": {"components": [], "files": []},
        "relates_to": [],
        "supersedes": [],
        "superseded_by": None,
        "stale": False,
        "stale_reason": None,
        "legacy": None,
        "provenance": {
            "created_by": "agent",
            "created_at": AT,
            "source": "S99",
            "session_id": None,
            "run_id": None,
        },
        "approval": None,
        "history": [
            {"at": AT, "from": None, "to": "PROPOSED", "by": "agent", "warrant": None, "ref": None},
            {
                "at": rejected_at,
                "from": "PROPOSED",
                "to": "REJECTED",
                "by": "Lore Tester <lore@example.invalid>",
                "warrant": warrant,
                "ref": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# index and validate
# ---------------------------------------------------------------------------


def test_index_idempotent_preserves_manual(lore_repo: GitRepo, capsys) -> None:
    """Index twice gives the same bytes; the intro, outro, BOM, CRLF and final newline stay."""
    intro = "\ufeff# Our lore\r\n\r\nHand-written intro.\r\n\r\n"
    outro = "\r\n## Notes\r\n\r\nHand-written outro, no final newline."
    (lore_repo.path / MAP).write_bytes((intro + BEGIN + "\r\nold\r\n" + END + outro).encode())

    code, out, err = lore(capsys, "index")
    assert code == 0, err
    assert "lore/MAP.md" in out and "lore/CANON.md" in out
    first = read(lore_repo, MAP)
    code, out, _ = lore(capsys, "index")
    assert code == 0 and out.strip() == "index: no change"
    assert read(lore_repo, MAP) == first

    text = first.decode("utf-8")
    assert text.startswith(intro + BEGIN + "\r\n")
    assert text.endswith(END + outro)
    assert "\n" not in text.replace("\r\n", "")  # every new line kept the CRLF style
    assert section(text, "Canon") == [
        "- [CANON-0001](canon/CANON-0001.md) Alpha writes through the engine",
        "- [CANON-0002](canon/CANON-0002.md) Beta is append-only",
    ]
    assert section(text, "Proposed") == [
        "- [ADR-0001](ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md) Replace the alpha and "
        "beta canon (decision; binding: user decision)",
        "- [CANON-0003](proposed/CANON-0003.md) Gamma waits (canon)",
    ]
    assert section(text, "Stories") == ["- [STORY-0001](STORIES/STORY-0001-first-run.md) First run"]
    assert "- `.agents/memory/activeContext.md`: what is being worked on now" in region(text)
    assert validate(capsys)[0] == 0

    # A hand edit inside the region is stale until index runs again.
    (lore_repo.path / MAP).write_bytes(text.replace("First run", "Hand edit", 1).encode())
    code, out = validate(capsys)
    assert code == 1
    assert "[map_stale]" in out and "hyper_lore.py index" in out
    assert lore(capsys, "index")[0] == 0
    assert read(lore_repo, MAP) == first


def test_canon_md_rebuilt_from_canon_files(lore_repo: GitRepo, capsys) -> None:
    """CANON.md is the CANON nodes' canon files between markers; index rebuilds it exactly."""
    with_map(capsys, lore_repo)
    expected = b""
    for nid in ("CANON-0001", "CANON-0002"):
        body = read(lore_repo, f"lore/canon/{nid}.md")
        tail = b"" if body.endswith(b"\n") else b"\n"
        expected += f"<!-- lore:{nid} -->\n".encode() + body + tail
        expected += f"<!-- /lore:{nid} -->\n".encode()
    original = read(lore_repo, CANON_MD)
    assert original == expected

    (lore_repo.path / CANON_MD).unlink()
    code, out = validate(capsys)
    assert code == 1 and "[canon_md_missing]" in out
    assert lore(capsys, "index")[0] == 0
    assert read(lore_repo, CANON_MD) == original

    (lore_repo.path / CANON_MD).write_bytes(original.replace(b"Beta", b"Gamma"))
    code, out = validate(capsys)
    assert code == 1 and "[canon_md_stale]" in out and "hyper_lore.py index" in out
    first = original.split(b"<!-- /lore:CANON-0001 -->\n")[0] + b"<!-- /lore:CANON-0001 -->\n"
    (lore_repo.path / CANON_MD).write_bytes(original + first)
    code, out = validate(capsys)
    assert code == 1 and "[canon_md_markers]" in out and "more than once" in out
    assert lore(capsys, "index")[0] == 0
    assert read(lore_repo, CANON_MD) == original

    # Approval rebuilds CANON.md on its own once MAP.md exists (the gated verb runs index).
    gate(capsys, lore_repo, "CANON-0003")
    canon = read(lore_repo, CANON_MD)
    body = read(lore_repo, "lore/canon/CANON-0003.md")
    assert b"<!-- lore:CANON-0003 -->\n" + body in canon
    assert canon.startswith(original)
    assert "[CANON-0003](canon/CANON-0003.md)" in read(lore_repo, MAP).decode()
    assert validate(capsys)[0] == 0


def test_graveyard_capped_at_20(bare_repo: GitRepo, capsys) -> None:
    """25 REJECTED nodes: the newest 20 by rejection time are listed, then the full-list line."""
    data = {"schema": "hacf.lore/v1", "id_counters": {"ADR": 0, "STORY": 25, "CANON": 0}}
    data["nodes"], when = [], {}
    for number in range(1, 26):
        nid = f"STORY-{number:04d}"
        rel = f"lore/STORIES/{nid}-rejected.md"
        body = f"# {nid}\n".encode()
        bare_repo.write(rel, body.decode())
        minute = (number * 7) % 25  # rejection order differs from id order
        when[nid] = minute
        stamp = (FROZEN_NOW + timedelta(minutes=minute)).isoformat()
        data["nodes"].append(story_node(nid, rel, body, stamp, f"warrant number {number}"))
    save(bare_repo, data)
    with_map(capsys, bare_repo)

    text = read(bare_repo, MAP).decode()
    lines = region(text)
    start = lines.index("## Graveyard") + 2
    entries = lines[start : start + 20]
    newest = sorted(when, key=lambda nid: when[nid], reverse=True)[:20]
    assert [line.split("]")[0][3:] for line in entries] == newest
    for line, nid in zip(entries, newest):
        assert f"(REJECTED 2026-10-21) warrant: warrant number {int(nid[-4:])}" in line
    assert lines[start + 20] == ""
    assert lines[start + 21] == FULL_LIST
    assert sum(line.startswith("- [STORY-") for line in section(text, "Graveyard")) == 20
    assert section(text, "Stories") == ["- (none)"]
    code, out, _ = lore(capsys, "list", "--status", "REJECTED")
    assert code == 0 and len(out.splitlines()) == 25
    assert validate(capsys)[0] == 0


def test_generated_markers_column0_outside_fences(lore_repo: GitRepo, capsys) -> None:
    """Fenced, indented, prefixed and case-folded markers are text; bad real ones exit 5."""
    decoy = (
        "# Map\n\n```text\n" + BEGIN + "\n" + END + "\n```\n\n"
        "  " + BEGIN + "\n" + BEGIN.lower() + "\n> " + BEGIN + "\n"
        "<!-- BEGIN OpenRig MANAGED BLOCK: role -->\nkept\n"
        "<!-- END OpenRig MANAGED BLOCK: role -->\n"
    )
    lore_repo.write(MAP, decoy)
    code, _, err = lore(capsys, "index")
    assert code == 0, err
    text = read(lore_repo, MAP).decode()
    assert text.startswith(decoy + "\n" + BEGIN + "\n")
    assert text.endswith(END + "\n")
    assert text.count(BEGIN) == 4  # three decoys, one region
    assert validate(capsys)[0] == 0

    (lore_repo.path / CANON_MD).unlink()
    for bad in (
        text + "\n" + BEGIN + "\n" + END + "\n",  # duplicate
        "# Map\n\n" + BEGIN + "\nno end marker\n",  # unbalanced
        "# Map\n\n" + END + "\n" + BEGIN + "\n",  # END before BEGIN
        "# Map\n\n" + BEGIN + "\n<!-- BEGIN HACF MANAGED BLOCK: other -->\n"
        "<!-- END HACF MANAGED BLOCK: other -->\n" + END + "\n",  # nested
    ):
        lore_repo.write(MAP, bad)
        code, _, err = lore(capsys, "index")
        assert code == 5, err
        assert read(lore_repo, MAP) == bad.encode()
        assert not (lore_repo.path / CANON_MD).exists()
        code, out = validate(capsys)
        assert code == 1 and "[map_markers]" in out


# ---------------------------------------------------------------------------
# importers
# ---------------------------------------------------------------------------


def test_import_adr_mapping_multicontext(bare_repo: GitRepo, capsys) -> None:
    """Two docs/adr dirs, two contexts: status mapping, legacy, renumbering, stubs, README."""
    shutil.copytree(ADR_FIXTURES / "core", bare_repo.path / "docs" / "adr")
    shutil.copytree(ADR_FIXTURES / "billing", bare_repo.path / "services/billing/docs/adr")
    bare_repo.commit("legacy ADRs")
    originals = {
        p.relative_to(bare_repo.path).as_posix(): p.read_bytes()
        for p in sorted((bare_repo.path).rglob("*.md"))
        if "docs/adr" in p.as_posix()
    }

    code, out, err = lore(capsys, "import", "--adr-dirs", "docs/adr", "--context", "core")
    assert code == 0, err
    code, out2, err = lore(
        capsys, "import", "--adr-dirs", "services/billing/docs/adr", "--context", "billing"
    )
    assert code == 0, err
    graph = nodes(bare_repo)
    expected = {
        "ADR-0001": ("docs/adr/0001-record-architecture-decisions.md", "PROPOSED", "accepted"),
        "ADR-0002": ("docs/adr/0002-use-postgres.md", "SUPERSEDED", "superseded"),
        "ADR-0003": ("docs/adr/0003-use-sqlite.md", "PROPOSED", "proposed"),
        "ADR-0004": ("docs/adr/0004-old-cache.md", "RETIRED", "deprecated"),
        "ADR-0005": ("docs/adr/0005-logging.md", "PROPOSED", None),
        "ADR-0006": ("services/billing/docs/adr/0001-billing-ledger.md", "PROPOSED", "accepted"),
    }
    assert set(graph) == set(expected)
    assert load(bare_repo)["id_counters"] == {"ADR": 6, "STORY": 0, "CANON": 0}
    for nid, (source, status, legacy) in expected.items():
        node = graph[nid]
        assert (node["status"], node["legacy"]) == (status, {"source": source, "status": legacy})
        assert node["origin"] == "import" and node["approval"] is None
        assert node["context"] == ("billing" if nid == "ADR-0006" else "core")
        assert node["pointer"]["path"].startswith(f"lore/ADRs/{nid}-")
        assert read(bare_repo, node["pointer"]["path"]) == originals[source]  # verbatim
        stub = read(bare_repo, source).decode()
        assert stub.startswith(f"<!-- hacf-lore-redirect: {node['pointer']['path']} -->\n")
        canon = bare_repo.path / "lore" / "canon" / f"{nid}.md"
        if status in ("RETIRED", "SUPERSEDED"):
            assert canon.read_bytes() == originals[source]
        else:
            assert not canon.exists()
    assert graph["ADR-0001"]["title"] == "Record architecture decisions"
    assert graph["ADR-0004"]["title"] == "Keep a process-local cache"
    assert graph["ADR-0005"]["title"] == "Pick a logging library"
    assert graph["ADR-0006"]["title"] == "Keep billing in a double-entry ledger"
    assert graph["ADR-0002"]["superseded_by"] == "ADR-0003"
    assert graph["ADR-0003"]["supersedes"] == ["ADR-0002"]
    assert not any(node["status"] == "CANON" for node in graph.values())
    assert "renumbered services/billing/docs/adr/0001-billing-ledger.md" in out2
    assert "it is ADR-0006" in out2
    readme = read(bare_repo, "docs/adr/README.md").decode()
    assert readme.startswith("<!-- hacf-lore-redirect: lore/ADRs/ -->")
    assert "../../lore/ADRs/" in readme
    billing_readme = "services/billing/docs/adr/README.md"
    assert read(bare_repo, billing_readme) == originals[billing_readme]
    assert f"kept {billing_readme}" in out2
    assert not (bare_repo.path / ".gitattributes").exists()
    assert validate(capsys)[0] == 0

    # A re-run finds only stubs: nothing is imported and nothing changes.
    before = read(bare_repo, GRAPH)
    code, out, err = lore(capsys, "import", "--adr-dirs", "docs/adr")
    assert code == 0, err
    assert "0 imported" in out and read(bare_repo, GRAPH) == before


# ---------------------------------------------------------------------------
# merge drivers, renumber, install-drivers
# ---------------------------------------------------------------------------


def test_merge_driver_union_and_conflict(lore_repo: GitRepo, capsys, clock) -> None:
    """Two branches each propose a node and both reject+reopen one: union; status conflict 1."""
    with_map(capsys, lore_repo)
    install_drivers(capsys, lore_repo)
    lore_repo.commit("index")

    lore_repo.git("checkout", "-q", "-b", "a")
    clock.now = FROZEN_NOW + timedelta(minutes=1)
    propose(capsys, lore_repo, "decision", "Branch a decision")
    clock.now = FROZEN_NOW + timedelta(minutes=2)
    gate(capsys, lore_repo, "STORY-0001", "reject")
    clock.now = FROZEN_NOW + timedelta(minutes=3)
    gate(capsys, lore_repo, "STORY-0001", "reopen")
    lore_repo.commit("branch a")

    lore_repo.git("checkout", "-q", "-b", "b", "main")
    clock.now = FROZEN_NOW + timedelta(minutes=4)
    propose(capsys, lore_repo, "story", "Branch b story")
    clock.now = FROZEN_NOW + timedelta(minutes=5)
    gate(capsys, lore_repo, "STORY-0001", "reject")
    clock.now = FROZEN_NOW + timedelta(minutes=6)
    gate(capsys, lore_repo, "STORY-0001", "reopen")
    lore_repo.commit("branch b")

    lore_repo.git("checkout", "-q", "a")
    proc = merge(lore_repo, "b")
    assert proc.returncode == 0, proc.stderr
    graph = nodes(lore_repo)
    assert {"ADR-0002", "STORY-0002"} <= set(graph)
    history = graph["STORY-0001"]["history"]
    assert [entry["to"] for entry in history] == [
        "PROPOSED",
        "REJECTED",
        "PROPOSED",
        "REJECTED",
        "PROPOSED",
    ]
    assert [entry["at"][14:16] for entry in history[1:]] == ["02", "03", "05", "06"]
    assert graph["STORY-0001"]["status"] == "PROPOSED"
    assert load(lore_repo)["id_counters"] == {"ADR": 2, "STORY": 2, "CANON": 3}
    assert "Branch b story" in read(lore_repo, MAP).decode()
    assert not (lore_repo.path / NEEDS_INDEX).exists()
    assert validate(capsys)[0] == 0

    # c approves CANON-0003 and d rejects it: a status conflict exits 1 with a report.
    lore_repo.git("checkout", "-q", "-b", "c", "main")
    gate(capsys, lore_repo, "CANON-0003", "approve")
    lore_repo.commit("branch c")
    lore_repo.git("checkout", "-q", "-b", "d", "main")
    gate(capsys, lore_repo, "CANON-0003", "reject")
    lore_repo.commit("branch d")
    proc = merge(lore_repo, "c")
    assert proc.returncode != 0
    assert "status conflict on CANON-0003: base PROPOSED, ours REJECTED, theirs CANON" in (
        proc.stderr
    )
    assert "renumber" in proc.stderr
    lore_repo.git("merge", "--abort")

    # An id added on both sides with other content is a collision naming renumber.
    base_text = lore_repo.git("show", "main:" + GRAPH)
    sides = {}
    for label in ("ours", "theirs"):
        data = yaml.safe_load(base_text)
        node = json.loads(json.dumps(nodes(lore_repo)["ADR-0001"]))
        node.update(id="ADR-0009", content_sha256=sha(label.encode()), relates_to=[])
        node["pointer"] = {"path": f"lore/ADRs/ADR-0009-{label}.md"}
        data["nodes"].append(node)
        data["id_counters"]["ADR"] = 9
        sides[label] = lore_repo.scratch / f"{label}.yml"
        sides[label].write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    base_file = lore_repo.scratch / "base.yml"
    base_file.write_text(base_text, encoding="utf-8")
    ours_before = sides["ours"].read_bytes()
    code, _, err = lore(
        capsys, "merge-driver", str(base_file), str(sides["ours"]), str(sides["theirs"]), GRAPH
    )
    assert code == 1
    assert "id collision: ADR-0009" in err and "renumber ADR-0009 <new id>" in err
    assert sides["ours"].read_bytes() == ours_before


def test_merge_generated_regenerates(lore_repo: GitRepo, capsys) -> None:
    """Two branches approve different nodes: the merged CANON.md holds both bodies (R8)."""
    with_map(capsys, lore_repo)
    install_drivers(capsys, lore_repo)
    lore_repo.commit("index")
    lore_repo.git("checkout", "-q", "-b", "a")
    gate(capsys, lore_repo, "CANON-0003")
    lore_repo.commit("approve CANON-0003")
    lore_repo.git("checkout", "-q", "-b", "b", "main")
    gate(capsys, lore_repo, "STORY-0001")
    lore_repo.commit("approve STORY-0001")
    theirs_body = read(lore_repo, "lore/canon/STORY-0001.md")

    lore_repo.git("checkout", "-q", "a")
    assert not (lore_repo.path / "lore/canon/STORY-0001.md").exists()
    proc = merge(lore_repo, "b")
    assert proc.returncode == 0, proc.stderr
    canon = read(lore_repo, CANON_MD)
    ours_body = read(lore_repo, "lore/canon/CANON-0003.md")
    assert b"<!-- lore:CANON-0003 -->\n" + ours_body in canon
    assert b"<!-- lore:STORY-0001 -->\n" + theirs_body in canon
    markers = [line for line in canon.split(b"\n") if line.startswith(b"<!-- lore:")]
    assert markers == [
        f"<!-- lore:{nid} -->".encode()
        for nid in ("CANON-0001", "CANON-0002", "CANON-0003", "STORY-0001")
    ]
    canon_section = section(read(lore_repo, MAP).decode(), "Canon")
    assert [line.split("]")[0][3:] for line in canon_section] == [
        "CANON-0001",
        "CANON-0002",
        "CANON-0003",
        "STORY-0001",
    ]
    assert not (lore_repo.path / NEEDS_INDEX).exists()
    assert validate(capsys)[0] == 0
    assert lore(capsys, "index")[1].strip() == "index: no change"

    # Outside a merge the driver cannot see the merged graph: it keeps ours, flags needs-index.
    files = {}
    for label in ("base", "ours", "theirs"):
        files[label] = lore_repo.scratch / f"{label}-canon.md"
        files[label].write_text(f"{label}\n", encoding="utf-8")
    code, _, err = lore(
        capsys,
        "merge-generated",
        str(files["base"]),
        str(files["ours"]),
        str(files["theirs"]),
        CANON_MD,
    )
    assert code == 0 and "kept ours" in err
    assert files["ours"].read_text(encoding="utf-8") == "ours\n"
    assert (lore_repo.path / NEEDS_INDEX).exists()
    code, out = validate(capsys)
    assert code == 1 and "[needs_index]" in out
    assert lore(capsys, "index")[0] == 0
    assert not (lore_repo.path / NEEDS_INDEX).exists()
    assert validate(capsys)[0] == 0


def test_renumber_rewrites_references(lore_repo: GitRepo, capsys) -> None:
    """lore_refs, relates_to, supersedes and superseded_by follow; commit messages reported."""
    data = load(lore_repo)
    old = next(n for n in data["nodes"] if n["id"] == "CANON-0001")
    old.update(status="SUPERSEDED", superseded_by="ADR-0001")
    entry = {"at": AT, "from": "CANON", "to": "SUPERSEDED", "by": "Fixture", "warrant": None}
    old["history"].append({**entry, "ref": None})
    save(lore_repo, data)
    mission = "missions/release-1/SPEC.md"
    slice_spec = "missions/release-1/slices/01-first/SPEC.md"
    lore_repo.write(
        mission,
        "---\nid: OPR.1\nmission: release-1\nlore_refs: [ADR-0001]\nhacf_type: SuperPRD\n---\n"
        "# SuperPRD: Release 1\n\nNames ADR-0001 in prose.\n",
    )
    lore_repo.write(
        slice_spec,
        "---\nid: OPR.1.1\nlore_refs: [ADR-0001, STORY-0001]\nhacf_type: MiniPRD\n---\n"
        "# MiniPRD: First\n",
    )
    lore_repo.commit("Adopt ADR-0001 for the first release")
    adr_body = read(lore_repo, "lore/ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md")
    canon_body = read(lore_repo, "lore/canon/CANON-0002.md")

    code, out, err = lore(capsys, "renumber", "ADR-0001", "ADR-0005")
    assert code == 0, err
    graph = nodes(lore_repo)
    assert "ADR-0001" not in graph
    assert graph["ADR-0005"]["pointer"]["path"] == (
        "lore/ADRs/ADR-0005-replace-the-alpha-and-beta-canon.md"
    )
    assert read(lore_repo, graph["ADR-0005"]["pointer"]["path"]) == adr_body
    assert not (lore_repo.path / "lore/ADRs/ADR-0001-replace-the-alpha-and-beta-canon.md").exists()
    assert graph["CANON-0001"]["superseded_by"] == "ADR-0005"
    assert load(lore_repo)["id_counters"]["ADR"] == 5
    mission_text = read(lore_repo, mission).decode()
    assert "lore_refs: [ADR-0005]\n" in mission_text
    assert "Names ADR-0001 in prose." in mission_text  # only the frontmatter key is rewritten
    assert "lore_refs: [ADR-0005, STORY-0001]\n" in read(lore_repo, slice_spec).decode()
    assert "reported, not rewritten: commit" in out and "Adopt ADR-0001" in out
    assert "ADR-0001" in lore_repo.git("log", "-1", "--format=%B")

    assert lore(capsys, "renumber", "STORY-0001", "STORY-0004")[0] == 0
    assert nodes(lore_repo)["ADR-0005"]["relates_to"] == ["STORY-0004"]
    assert "lore_refs: [ADR-0005, STORY-0004]\n" in read(lore_repo, slice_spec).decode()

    assert lore(capsys, "renumber", "CANON-0002", "CANON-0009")[0] == 0
    graph = nodes(lore_repo)
    assert graph["ADR-0005"]["supersedes"] == ["CANON-0001", "CANON-0009"]
    assert graph["CANON-0009"]["pointer"]["path"] == "lore/canon/CANON-0009.md"
    assert read(lore_repo, "lore/canon/CANON-0009.md") == canon_body
    assert not (lore_repo.path / "lore/canon/CANON-0002.md").exists()

    before = read(lore_repo, GRAPH)
    code, _, err = lore(capsys, "renumber", "CANON-0003", "CANON-0009")
    assert code == 5 and "CANON-0009 already exists" in err
    assert lore(capsys, "renumber", "ADR-0005", "STORY-0009")[0] == 2
    assert read(lore_repo, GRAPH) == before
    assert (lore_repo.path / "lore/proposed/CANON-0003.md").exists()
    assert validate(capsys)[0] == 0


def test_install_drivers_writes_info_attributes(
    bare_repo: GitRepo, capsys, tmp_path, monkeypatch
) -> None:
    """The block lands in <git-common-dir>/info/attributes; a rerun changes nothing (R10)."""
    common = bare_repo.path / ".git"
    attributes = common / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_bytes(b"*.png binary")  # no final newline
    code, out, err = lore(capsys, "install-drivers")
    assert code == 0, err
    assert attributes.read_bytes() == (
        b"*.png binary\n# BEGIN HACF MANAGED BLOCK: merge-drivers\n"
        b"lore_hypergraph.yml merge=hacf-lore\nlore/MAP.md merge=hacf-generated\n"
        b"lore/CANON.md merge=hacf-generated\n# END HACF MANAGED BLOCK: merge-drivers"
    )
    expected = {
        "merge.hacf-lore.driver": "python3 .agents/scripts/hyper_lore.py merge-driver %O %A %B %P",
        "merge.hacf-generated.driver": (
            "python3 .agents/scripts/hyper_lore.py merge-generated %O %A %B %P"
        ),
    }
    for key, value in expected.items():
        assert bare_repo.git("config", "--local", "--get", key).strip() == value
    for key in ("merge.hacf-lore.name", "merge.hacf-generated.name"):
        assert bare_repo.git("config", "--local", "--get", key).strip()
    check = bare_repo.git("check-attr", "merge", "--", GRAPH, MAP, CANON_MD)
    assert check.splitlines() == [
        f"{GRAPH}: merge: hacf-lore",
        f"{MAP}: merge: hacf-generated",
        f"{CANON_MD}: merge: hacf-generated",
    ]

    state = (attributes.read_bytes(), (common / "config").read_bytes())
    code, out, err = lore(capsys, "install-drivers")
    assert code == 0, err
    assert "no change" in out
    assert (attributes.read_bytes(), (common / "config").read_bytes()) == state
    assert not (bare_repo.path / ".gitattributes").exists()
    assert bare_repo.git("ls-files", ".gitattributes") == ""

    # From a linked worktree the block still goes to the shared git common dir.
    attributes.unlink()
    worktree = tmp_path / "wt"
    bare_repo.git("worktree", "add", "-q", str(worktree), "-b", "wt")
    monkeypatch.chdir(worktree)
    code, _, err = lore(capsys, "install-drivers")
    assert code == 0, err
    assert b"# BEGIN HACF MANAGED BLOCK: merge-drivers\n" in attributes.read_bytes()
    assert not (common / "worktrees" / "wt" / "info" / "attributes").exists()
    assert not (worktree / ".gitattributes").exists()

    # Duplicate HACF markers: exit 5, nothing written.
    duplicate = attributes.read_bytes() + b"\n" + attributes.read_bytes()
    attributes.write_bytes(duplicate)
    assert lore(capsys, "install-drivers")[0] == 5
    assert attributes.read_bytes() == duplicate


CRLF_BLOCK = (
    b"# BEGIN HACF MANAGED BLOCK: merge-drivers\r\n"
    b"lore_hypergraph.yml merge=hacf-lore\r\nlore/MAP.md merge=hacf-generated\r\n"
    b"lore/CANON.md merge=hacf-generated\r\n# END HACF MANAGED BLOCK: merge-drivers"
)
STALE_CRLF_BLOCK = (
    b"# BEGIN HACF MANAGED BLOCK: merge-drivers\r\n*.old merge=hacf-lore\r\n"
    b"# END HACF MANAGED BLOCK: merge-drivers"
)
CRLF_HEAD = b"*.png binary\r\n*.pdf binary"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            CRLF_HEAD + b"\r\n", CRLF_HEAD + b"\r\n" + CRLF_BLOCK + b"\r\n", id="final-newline"
        ),
        pytest.param(CRLF_HEAD, CRLF_HEAD + b"\r\n" + CRLF_BLOCK, id="no-final-newline"),
        pytest.param(
            CRLF_HEAD + b"\r\n" + STALE_CRLF_BLOCK,
            CRLF_HEAD + b"\r\n" + CRLF_BLOCK,
            id="block-ends-file-no-final-newline",
        ),
        pytest.param(
            CRLF_HEAD + b"\r\n" + STALE_CRLF_BLOCK + b"\r\n*.svg binary",
            CRLF_HEAD + b"\r\n" + CRLF_BLOCK + b"\r\n*.svg binary",
            id="block-mid-file-no-final-newline",
        ),
    ],
)
def test_install_drivers_crlf_attributes(
    bare_repo: GitRepo, capsys, before: bytes, after: bytes
) -> None:
    """A CRLF info/attributes keeps CRLF, its final-newline state and a byte-stable rerun (R26).

    Every line the block writes ends CRLF, a file without a final newline ends at the END
    marker, and the bytes outside the block are the input's.
    """
    attributes = bare_repo.path / ".git" / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_bytes(before)
    for _ in range(2):
        code, _, err = lore(capsys, "install-drivers")
        assert code == 0, err
        data = attributes.read_bytes()
        assert data == after
        assert b"\r\r" not in data and data.count(b"\n") == data.count(b"\r\n")
        assert not data.endswith(b"\r")
    check = bare_repo.git("check-attr", "merge", "--", GRAPH, MAP, CANON_MD)
    assert check.splitlines() == [
        f"{GRAPH}: merge: hacf-lore",
        f"{MAP}: merge: hacf-generated",
        f"{CANON_MD}: merge: hacf-generated",
    ]
