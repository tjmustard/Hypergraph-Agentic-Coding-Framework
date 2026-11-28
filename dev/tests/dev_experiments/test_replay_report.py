"""The sealed material, the audit-only replay, scoring, the tool-path reader and the report."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import run_arm
import tool_paths

from .conftest import KIT, REPO_ROOT, World

SECRET = "SECRET-PLANTED-7c1f"
FAKE_DEFECTS = [
    {"id": f"d-fake-{n}", "class": cls, "file": "stockroom/importer.py", "lines": [n * 20, n * 20]}
    for n, cls in enumerate(("spec-deviation", "untested-edge-case", "negative-constraint"), 1)
]


def new_file_patch(path: str, lines: list[str]) -> str:
    """A git patch that adds one file."""
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}"
    )


def fake_sealed(root: Path) -> None:
    """A sealed dir of stubs: no solution, only enough for the oracles and the mechanics."""
    root.mkdir(parents=True, mode=0o700)
    reference = new_file_patch(
        "stockroom/importer.py",
        ['"""Fake stub."""', "", "", "def import_movements(ledger, path):", '    """Stub."""'],
    ) + new_file_patch(
        "stockroom/fake_report.py",
        ['"""Fake stub."""', "", "", "def low_stock(items):", '    """Stub."""'],
    )
    (root / "reference.patch").write_text(reference)
    (root / "defects.patch").write_text(
        new_file_patch("stockroom/fake_defect.py", ['"""Fake planted line, kept sealed."""'])
    )
    (root / "key.yml").write_text(json.dumps({"defects": FAKE_DEFECTS}))
    (root / "check_defects.py").write_text("# fake checker line, kept sealed\n")


def hashes(root: Path) -> str:
    """sealed.sha256's format for a sealed dir."""
    names = ("reference.patch", "defects.patch", "key.yml", "check_defects.py")
    lines = [f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  file:{n}" for n in names]
    keys = json.loads((root / "key.yml").read_text())["defects"]
    lines += sorted(f"{hashlib.sha256(d['id'].encode()).hexdigest()}  defect-id" for d in keys)
    return "\n".join(lines) + "\n"


def committed_files() -> list[Path]:
    """Every file of the kit and its tests."""
    roots = (KIT, REPO_ROOT / "dev" / "tests" / "dev_experiments")
    return [
        p
        for root in roots
        for p in root.rglob("*")
        if p.is_file() and not {"__pycache__", ".venv"} & set(p.parts)
    ]


def test_sealed_hashes(world: World) -> None:
    """The fake sealed dir matches its sha256 file, and one changed byte does not."""
    fake_sealed(world.sealed)
    listing = world.tmp / "sealed.sha256"
    listing.write_text(hashes(world.sealed))
    assert run_arm.sealed_problems(world.sealed, listing) == []
    (world.sealed / "key.yml").write_text(json.dumps({"defects": FAKE_DEFECTS[:2]}))
    assert run_arm.sealed_problems(world.sealed, listing)


def test_committed_sealed_listing_is_hashes_only() -> None:
    """The committed file holds four file hashes and three id hashes, nothing else."""
    lines = (KIT / "sealed.sha256").read_text().splitlines()
    assert len(lines) == 7
    assert sum(line.endswith("  defect-id") for line in lines) == 3
    for line in lines:
        digest, _gap, label = line.partition("  ")
        assert len(digest) == 64 and (label.startswith("file:") or label == "defect-id")
    assert not list(KIT.rglob("*.patch")) and not list(KIT.rglob("key.yml"))


def test_no_sealed_line_is_committed(world: World) -> None:
    """No committed kit file holds a line of the fake key or patches (fake sealed dir only)."""
    fake_sealed(world.sealed)
    added = []
    for name in ("reference.patch", "defects.patch", "key.yml", "check_defects.py"):
        for line in (world.sealed / name).read_text().splitlines():
            text = line.lstrip("+").strip()
            if "Fake planted" in text or "fake checker" in text or '"lines"' in text:
                added.append(text)
    assert added
    me = Path(__file__).resolve()
    for path in committed_files():
        if path.resolve() == me:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        assert not any(line in body for line in added), path


def test_tool_paths_returns_paths_only(tmp_path: Path) -> None:
    """Only tool names' path inputs; never message text or tool results."""
    root = tmp_path / "copy"
    content = [
        {"type": "text", "text": f"{SECRET} /etc/{SECRET}"},
        {"type": "tool_use", "name": "Read", "input": {"file_path": f"{root}/stockroom/store.py"}},
        {"type": "tool_use", "name": "Grep", "input": {"path": "stockroom", "pattern": SECRET}},
        {"type": "tool_use", "name": "NotebookEdit", "input": {"notebook_path": "/other/n.ipynb"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": f"cat {SECRET}"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": f"{root}/../escape.py"}},
    ]
    lines = [
        {"type": "assistant", "message": {"content": content}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": SECRET}]}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines) + "{bad\n")
    got = tool_paths.opened([path], root)
    assert got["paths"] == ["stockroom", "stockroom/store.py"]
    assert got["outside"] == sorted(["/other/n.ipynb", f"{tmp_path}/escape.py"])
    assert SECRET not in json.dumps(got)
    out = subprocess.run(
        ["python3", "-B", str(KIT / "tool_paths.py"), "--root", str(root), str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == got


REPORT = """# Audit

Read `stockroom/store.py:3`, which is fine.

## Findings
- stockroom/importer.py:24 - one
- {copy}/stockroom/importer.py:66 - two
- stockroom/other.py:40 - elsewhere
"""


def test_catches() -> None:
    """A finding names the file and a line within the range plus or minus 5."""
    got = run_arm.catches(REPORT.replace("{copy}", "/c"), FAKE_DEFECTS)
    assert got == ["d-fake-1"]
    close = REPORT.replace(":66", ":65").replace("{copy}", "/c")
    assert run_arm.catches(close, FAKE_DEFECTS) == ["d-fake-1", "d-fake-3"]
    assert run_arm.catches("## Findings\nnone\n", FAKE_DEFECTS) == []
    assert (
        run_arm.catches("stockroom/importer.py:20 with no findings heading", FAKE_DEFECTS) is None
    )


def replay(world: World, arm: int, run: int) -> None:
    """One fake replay with an audit that reports one defect."""
    opened = [{"type": "tool_use", "name": "Read", "input": {"file_path": "stockroom/importer.py"}}]
    world.set(
        script=[
            {"turn": [["build.audit", {"content": opened}]]},
            {"report": REPORT, "status": [["02-medium", "audit_failed"]]},
        ],
        working=False,
    )
    done = world.run(
        "replay", "--arm", str(arm), "--run", str(run), HACF_ADVISOR_SEALED_SHA=str(world.listing)
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture
def sealed_world(world: World) -> World:
    """A world with a fake sealed dir and its listing."""
    fake_sealed(world.sealed)
    world.listing = world.tmp / "sealed.sha256"
    world.listing.write_text(hashes(world.sealed))
    return world


def test_replay_score_report(sealed_world: World) -> None:
    """Both patches are applied as the builder's one commit; only path sets are recorded."""
    world = sealed_world
    replay(world, 0, 1)
    copy = Path(world.state()["copy"])
    assert (copy / "stockroom/importer.py").is_file()
    assert (copy / "stockroom/fake_defect.py").is_file()
    log = subprocess.run(
        ["git", "-C", str(copy), "log", "--format=%an|%s"], capture_output=True, text=True
    ).stdout.splitlines()
    assert log[0].startswith("build-execute|") and "defect" not in log[0].lower()
    spec = copy / "missions/advisor-workload/slices/02-medium/SPEC.md"
    assert "hacf_status: audit_failed" in spec.read_text()
    sealed = str(world.sealed)
    for call in world.calls("rig") + world.calls("test_rig"):
        assert sealed not in json.dumps(call)
    (audit_row,) = [c for c in world.calls("rig") if c["argv"][:2] == ["queue", "create"]]
    assert "## Findings" in audit_row["stdin"] and "defect" not in audit_row["stdin"].lower()
    (record,) = world.runs()
    assert record["kind"] == "replay" and record["status"] == "done"
    assert record["verdict"] == "audit_failed"
    run_dir = world.evidence / "runs" / record["id"]
    paths = json.loads((run_dir / "tool_paths-build.audit.json").read_text())
    assert paths == {"paths": ["stockroom/importer.py"], "outside": []}

    replay(world, 2, 1)
    done = world.run("score", HACF_ADVISOR_SEALED_SHA=str(world.listing))
    assert done.returncode == 0, done.stderr
    catch = json.loads((run_dir / "catches.json").read_text())
    assert (catch["caught"], catch["of"], catch["opened_all_defect_files"]) == (1, 3, True)
    assert catch["caught_id_sha256"] == [hashlib.sha256(b"d-fake-1").hexdigest()]
    text = json.dumps(catch)
    assert "d-fake" not in text and "importer" not in text

    out = world.tmp / "report.md"
    done = world.run("report", "--out", str(out))
    assert done.returncode == 0, done.stderr
    report = out.read_text()
    assert "adopt" not in report.lower()
    for heading in ("## Probes", "## Runs", "## A/A spread", "## Replay", "## Verdicts", "$90"):
        assert heading in report
    assert "U5" in report and "U6" in report and "d-fake" not in report


def test_replay_refuses_a_changed_sealed_dir(sealed_world: World) -> None:
    """A sealed dir that does not match its hashes stops before any rig."""
    world = sealed_world
    (world.sealed / "defects.patch").write_text("changed\n")
    done = world.run(
        "replay", "--arm", "0", "--run", "1", HACF_ADVISOR_SEALED_SHA=str(world.listing)
    )
    assert done.returncode == 3 and world.calls("test_rig") == []


def arm_run(arm: int, run: int, usd: float, rounds: int, first: int) -> dict:
    """A recorded arm run."""
    return {
        "id": f"arm{arm}-run{run}",
        "kind": "arm",
        "arm": arm,
        "run": run,
        "status": "done",
        "mixed": [],
        "void": False,
        "usd": usd,
        "rounds": {"01-small": rounds, "02-medium": 0},
        "first_pass": first,
    }


@pytest.mark.parametrize(
    ("one", "verdict"),
    [
        ((5.0, 0, 2), "promising"),
        ((20.0, 0, 2), "not promising"),
        ((9.5, 0, 2), "unknown"),
        ((5.0, 2, 1), "not promising"),
    ],
)
def test_builder_verdict(one: tuple, verdict: str) -> None:
    """R212: beyond the A/A spread, better and nothing worse is promising."""
    runs = [arm_run(0, 1, 9.0, 0, 2), arm_run(0, 2, 10.0, 0, 2)]
    runs += [arm_run(1, 1, *one), arm_run(1, 2, *one)]
    assert run_arm.builder_verdict(runs, 1) == verdict
    assert run_arm.builder_verdict(runs[:3], 1) == "unknown"


@pytest.mark.parametrize(
    ("caught", "usd", "opened", "verdict"),
    [
        (2, 1.0, True, "promising"),
        (1, 1.0, True, "not promising"),
        (2, 1.55, True, "unknown"),
        (2, 1.0, False, "unknown"),
    ],
)
def test_audit_verdict(caught: int, usd: float, opened: bool, verdict: str) -> None:
    """R212: catches no lower, cheaper beyond arm 0's spread, every defect file opened."""
    rows = [
        {"arm": 0, "usd": 1.5, "caught": 2, "opened_all": True},
        {"arm": 0, "usd": 1.7, "caught": 2, "opened_all": True},
        {"arm": 2, "usd": usd, "caught": caught, "opened_all": opened},
        {"arm": 2, "usd": usd, "caught": caught, "opened_all": opened},
    ]
    assert run_arm.audit_verdict(rows) == verdict
    assert run_arm.audit_verdict(rows[:2]) == "unknown"


def test_report_shows_the_residual_as_an_estimate(world: World) -> None:
    """Each seat's advisor residual is its own column, labelled; a missing one is unknown."""
    folder = world.evidence / "runs" / "arm1-run1"
    (folder / "telemetry").mkdir(parents=True)
    card = {
        "seats": [
            {"seat": "build-execute@x", "slices": [{"total": 5, "usd": 1.0}]},
            {"seat": "build-audit@x", "slices": [{"total": 6, "usd": 2.0}]},
        ],
        "client_cost": {
            "s1": {"seat": "build-execute@x", "advisor_residual_usd": 0.25},
            "s2": {"seat": "build-execute@x", "advisor_residual_usd": 0.5},
            "s3": {"seat": "build-audit@x", "advisor_residual_usd": 0.125},
            "s4": {"seat": "build-audit@x", "advisor_residual_usd": "unknown"},
        },
    }
    (folder / "telemetry" / "arm1-run1.json").write_text(json.dumps(card))
    (world.evidence / "spend.json").write_text(json.dumps({"runs": [arm_run(1, 1, 3.0, 0, 2)]}))
    out = world.tmp / "report.md"
    assert world.run("report", "--out", str(out)).returncode == 0
    lines = out.read_text().splitlines()
    assert any("advisor residual USD (estimate)" in line for line in lines)
    (execute,) = [line for line in lines if "| build.execute |" in line]
    (audit,) = [line for line in lines if "| build.audit |" in line]
    assert execute.endswith("| 0.75 |") and audit.endswith("| unknown |")
