"""Tests of dev/telemetry/recompute_caps.py, the read-only cap proposal (OPR.99.0.8.3).

Scorecards are written under tmp_path; --caps and --rig-spec are the shipped files, whose bytes
and mtime are checked unchanged after every run.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, clean_env

TOOL_DIR = REPO_ROOT / "dev" / "telemetry"
TOOL = TOOL_DIR / "recompute_caps.py"
CAPS = REPO_ROOT / "openrig" / "hacf" / "caps.yml"
RIG_SPEC = REPO_ROOT / "openrig" / "hacf" / "rig.yaml"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import recompute_caps as rc  # noqa: E402

NOTICE = "Proposal only: caps.yml is unchanged. TJ approves each change (ADR-0044)."
REEVALUATE = [
    "claude-code minor version change",
    "openrig minor version change",
    "start-up trim change (roles, rig keys, CULTURE/protocol/role files)",
    "a seat's model change",
    "each B-metric verdict",
    "each HACF release",
]


def occupant(sid: str, peak: int, turns: int = 4) -> dict:
    """One scorecard occupant."""
    return {
        "session_id": sid,
        "turns": turns,
        "peak": peak,
        "first_ts": "2026-11-01T10:00:00Z",
        "last_ts": "2026-11-01T11:00:00Z",
    }


def scorecard(tmp: Path, name: str, context: dict) -> Path:
    """Write a minimal hacf.telemetry/v1 scorecard with this context object."""
    seats = {}
    for seat, occs in context.items():
        seats[seat] = {
            "occupants": occs,
            "peak_max": max((o["peak"] for o in occs), default=None),
            "turns": sum(o["turns"] for o in occs),
        }
    path = tmp / name
    path.write_text(
        json.dumps({"schema": "hacf.telemetry/v1", "rig": "hacf", "context": seats}),
        encoding="utf-8",
    )
    return path


def fingerprint(path: Path) -> tuple[str, int]:
    """sha256 and mtime_ns."""
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def run(*args: str) -> subprocess.CompletedProcess:
    """Run the script as a subprocess."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=clean_env(),
    )


def run_on(
    *cards: Path, extra: tuple[str, ...] = (), caps: Path = CAPS
) -> subprocess.CompletedProcess:
    """Run on these scorecards with the caps (shipped by default) and rig spec; caps untouched."""
    before = fingerprint(caps)
    args = []
    for card in cards:
        args += ["--scorecard", str(card)]
    result = run(*args, "--caps", str(caps), "--rig-spec", str(RIG_SPEC), *extra)
    assert fingerprint(caps) == before
    return result


# The caps.yml values before TJ's 2026-11-24 approval (OPR.99.0.10.1). The statistics tests read
# this synthetic copy, so a cap change shows in test_caps.py alone (ADR-0044).
OLD_CAPS = {
    "lead": 350000,
    "architect": 300000,
    "red-team": 250000,
    "resolve": 300000,
    "execute": 300000,
    "audit": 250000,
    "scribe": 150000,
}


def old_caps(tmp: Path) -> Path:
    """A copy of the shipped caps document with the old cap_tokens, written under tmp."""
    doc = yaml.safe_load(CAPS.read_text())
    for role, cap in OLD_CAPS.items():
        doc["seats"][role]["cap_tokens"] = cap
    path = tmp / "old-caps.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


SIX = [occupant(f"s{i}", i * 100000) for i in range(1, 7)]


def test_six_execute_occupants(tmp_path: Path) -> None:
    """Peaks 100k..600k: median 350000, p90 600000, proposed 600000."""
    card = scorecard(tmp_path, "a.json", {"build-execute@hacf": SIX})
    result = run_on(card, extra=("--json",), caps=old_caps(tmp_path))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    e = data["roles"]["execute"]
    assert e == {
        "n": 6,
        "current": 300000,
        "low": 100000,
        "median": 350000,
        "mean": 350000,
        "p90": 600000,
        "high": 600000,
        "proposed": 600000,
    }
    assert data["notice"] == NOTICE
    assert data["unmapped"] == []


def test_four_occupants_no_proposal(tmp_path: Path) -> None:
    """Fewer than 5 occupants: proposed null with the note."""
    card = scorecard(tmp_path, "a.json", {"build-execute@hacf": SIX[:4]})
    data = json.loads(run_on(card, extra=("--json",)).stdout)
    e = data["roles"]["execute"]
    assert e["n"] == 4 and e["proposed"] is None
    assert e["note"] == "insufficient data (n<5)"
    assert e["p90"] == 400000  # nearest rank: sorted[ceil(3.6) - 1]


def test_floor_and_rounding(tmp_path: Path) -> None:
    """The proposal rounds p90 up to 10000 and never goes under 100000."""
    low = [occupant(f"l{i}", 20000 + i) for i in range(5)]
    odd = [occupant(f"o{i}", 123001 + i) for i in range(5)]
    card = scorecard(tmp_path, "a.json", {"desk-scribe@hacf": low, "build-audit@hacf": odd})
    roles = json.loads(run_on(card, extra=("--json",)).stdout)["roles"]
    assert roles["scribe"]["proposed"] == 100000
    assert roles["audit"]["proposed"] == 130000


def test_every_caps_role_listed(tmp_path: Path) -> None:
    """Each caps.yml role is listed with its current cap, n 0 when it has no data."""
    card = scorecard(tmp_path, "a.json", {"build-execute@hacf": SIX})
    roles = json.loads(run_on(card, extra=("--json",)).stdout)["roles"]
    caps = yaml.safe_load(CAPS.read_text())["seats"]
    assert list(roles) == list(caps)
    for role, entry in caps.items():
        assert roles[role]["current"] == entry["cap_tokens"]
    assert roles["lead"]["n"] == 0 and roles["lead"]["proposed"] is None


def test_occupant_counted_once_and_zero_turns_skipped(tmp_path: Path) -> None:
    """One session in two scorecards counts once (highest peak); turns 0 is not an occupant."""
    a = scorecard(tmp_path, "a.json", {"build-audit@hacf": [occupant("x", 100000)]})
    b = scorecard(
        tmp_path,
        "b.json",
        {"build-audit@hacf": [occupant("x", 150000), occupant("z", 900000, turns=0)]},
    )
    audit = json.loads(run_on(a, b, extra=("--json",)).stdout)["roles"]["audit"]
    assert audit["n"] == 1 and audit["high"] == 150000


def test_unmapped_seat(tmp_path: Path) -> None:
    """A seat with no member in the rig spec is listed under unmapped."""
    card = scorecard(
        tmp_path,
        "a.json",
        {"build-execute@hacf": SIX, "pm-planner@hacf": [occupant("p", 500000)]},
    )
    data = json.loads(run_on(card, extra=("--json",)).stdout)
    assert data["unmapped"] == ["pm-planner@hacf"]
    assert data["roles"]["execute"]["n"] == 6


def test_table_output(tmp_path: Path) -> None:
    """The default output is a table with the five statistics, ending with the notice."""
    card = scorecard(
        tmp_path, "a.json", {"build-execute@hacf": SIX, "pm-planner@hacf": [occupant("p", 1)]}
    )
    result = run_on(card, caps=old_caps(tmp_path))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0].split() == [
        "role",
        "n",
        "low",
        "median",
        "mean",
        "p90",
        "high",
        "current",
        "proposed",
    ]
    execute = next(ln for ln in lines if ln.startswith("execute "))
    assert execute.split() == [
        "execute",
        "6",
        "100000",
        "350000",
        "350000",
        "600000",
        "600000",
        "300000",
        "600000",
    ]
    assert "insufficient data (n<5)" in next(ln for ln in lines if ln.startswith("lead "))
    assert "unmapped: pm-planner@hacf" in lines
    assert lines[-1] == NOTICE


def test_caps_never_opened_for_writing(tmp_path: Path, monkeypatch, capsys) -> None:
    """In-process: every open() of --caps is read-only."""
    card = scorecard(tmp_path, "a.json", {"build-execute@hacf": SIX})
    modes = []
    real_open = open

    def spy(file, mode="r", *args, **kwargs):
        """Record the mode of each open of caps.yml."""
        if Path(file).resolve() == CAPS.resolve():
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    before = fingerprint(CAPS)
    code = rc.main(["--scorecard", str(card), "--caps", str(CAPS), "--rig-spec", str(RIG_SPEC)])
    assert code == 0
    assert modes and all(m == "r" for m in modes)
    assert fingerprint(CAPS) == before
    assert capsys.readouterr().out.strip().endswith(NOTICE)


@pytest.mark.parametrize(
    "args",
    [
        ["--scorecard", "CARD", "--rig-spec", str(RIG_SPEC)],
        ["--caps", str(CAPS), "--rig-spec", str(RIG_SPEC)],
        ["--scorecard", "CARD", "--caps", str(CAPS)],
        ["--scorecard", "MISSING", "--caps", str(CAPS), "--rig-spec", str(RIG_SPEC)],
        ["--scorecard", "OLD", "--caps", str(CAPS), "--rig-spec", str(RIG_SPEC)],
        ["--scorecard", "CARD", "--caps", "MISSING", "--rig-spec", str(RIG_SPEC)],
    ],
    ids=["no-caps", "no-scorecard", "no-rig-spec", "missing-card", "no-context", "missing-caps"],
)
def test_usage_errors_exit_2(tmp_path: Path, args: list[str]) -> None:
    """A missing argument or an unreadable input exits 2 with one stderr line."""
    card = scorecard(tmp_path, "a.json", {"build-execute@hacf": SIX})
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": "hacf.telemetry/v1", "seats": []}), encoding="utf-8")
    subst = {"CARD": str(card), "MISSING": str(tmp_path / "nope.json"), "OLD": str(old)}
    before = fingerprint(CAPS)
    result = run(*[subst.get(a, a) for a in args])
    assert result.returncode == 2
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stdout == ""
    assert fingerprint(CAPS) == before


def test_caps_reevaluate_triggers() -> None:
    """caps.yml's reevaluate holds the six triggers of OPR.99.0.8.3 MR 5."""
    assert yaml.safe_load(CAPS.read_text())["reevaluate"] == REEVALUATE
