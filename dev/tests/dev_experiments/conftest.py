"""A fake world for the advisor-experiment kit: a fake HOME, fake tools and no live call."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
KIT = REPO_ROOT / "dev" / "experiments" / "advisor"
RUN_ARM = KIT / "run_arm.py"
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))


def stamp(hours: float = 0) -> str:
    """Return now plus some hours, in ISO form."""
    when = datetime.now(timezone.utc) + timedelta(hours=hours)
    return when.isoformat().replace("+00:00", "Z")


class World:
    """One test's fake host: HOME, TMPDIR, tools on PATH and the main checkout the gate reads."""

    def __init__(self, tmp: Path) -> None:
        """Lay out the fake host."""
        self.home = tmp / "home"
        self.tmp = tmp / "scratch"
        self.fake = tmp / "fake"
        self.bin = tmp / "bin"
        self.main = tmp / "main-checkout"
        self.project = tmp / "mapped-project"
        for path in (self.home / ".claude", self.tmp, self.fake, self.bin):
            path.mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text('{"theme": "dark"}\n')
        for name in ("rig", "tmux", "claude", "test_rig.py"):
            script = self.bin / name
            fakes = Path(__file__).with_name("fakes.py")
            script.write_text(f"#!{sys.executable}\n" + fakes.read_text(encoding="utf-8"))
            script.chmod(0o755)
        self.slice(self.main, "v068-host-ops/slices/12-advisor-kit", "audited")
        self.slice(self.main, "v068-host-ops/slices/13-advisor-run", "executing")
        self.slice(self.project, "m/slices/01-one", "compiled")
        config = self.home / ".config" / "hacf"
        config.mkdir(parents=True)
        (config / "host.yml").write_text(
            f"schema: hacf.host-config/v1\nprojects:\n  some-rig: {self.project}\n"
        )
        weekly_reset, five_reset = stamp(10), stamp(2)
        series = [
            {
                "window": "weekly",
                "windowUsedPercent": used,
                "resetsAt": weekly_reset,
                "sampledAt": stamp(-ago),
            }
            for used, ago in ((28, 23.9), (34, 12), (40, 0))
        ]
        self.set(
            weekly=40,
            five_hour=10,
            resets_at=dict(weekly=weekly_reset, five_hour=five_reset),
            series=series,
            sessions=0,
            pid=1000,
            seats={},
            cost={"orch.lead": 0.5, "build.execute": 0.4, "build.audit": 0.3},
            script=[],
        )

    @staticmethod
    def slice(root: Path, rel: str, status: str) -> Path:
        """Write one slice SPEC under a checkout's missions/."""
        path = root / "missions" / rel / "SPEC.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nid: OPR.1.1\nhacf_status: {status}\n---\n# x\n")
        return path

    def state(self) -> dict:
        """Return the fake tools' state."""
        return json.loads((self.fake / "state.json").read_text())

    def set(self, **changes) -> None:
        """Change the fake tools' state."""
        path = self.fake / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        state.update(changes)
        path.write_text(json.dumps(state))

    def calls(self, tool: str | None = None) -> list[dict]:
        """Return the logged calls of the fake tools."""
        path = self.fake / "calls.jsonl"
        if not path.exists():
            return []
        found = [json.loads(line) for line in path.read_text().splitlines()]
        return [c for c in found if tool is None or c["tool"] == tool]

    @property
    def evidence(self) -> Path:
        """The runner's evidence dir under the fake HOME."""
        return self.home / ".hacf" / "hacf" / "evidence" / "advisor-v068"

    @property
    def sealed(self) -> Path:
        """The sealed dir under the fake HOME."""
        return self.home / ".hacf" / "hacf" / "experiments" / "advisor-v068" / "sealed"

    def env(self, **extra: str) -> dict[str, str]:
        """Return the runner's environment: nothing of the real host's rig or HOME."""
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENRIG_", "HACF_", "GIT_", "TMUX"))
            and key not in ("XDG_CONFIG_HOME", "VIRTUAL_ENV")
        }
        env.update(
            HOME=str(self.home),
            TMPDIR=str(self.tmp),
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            PYTHONDONTWRITEBYTECODE="1",
            HACF_FAKE_DIR=str(self.fake),
            HACF_ADVISOR_MAIN=str(self.main),
            HACF_ADVISOR_TEST_RIG=str(self.bin / "test_rig.py"),
            HACF_ADVISOR_PROC=str(self.fake / "proc"),
            HACF_ADVISOR_POLL_S="0",
            HACF_ADVISOR_WAIT_S="20",
        )
        env.update(extra)
        return env

    def run(self, *argv: str, **extra: str) -> subprocess.CompletedProcess:
        """Run run_arm.py in the fake world."""
        return subprocess.run(
            [sys.executable, str(RUN_ARM), *argv],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=240,
        )

    def runs(self) -> list[dict]:
        """Return every recorded run.json, oldest first."""
        found = sorted(self.evidence.glob("runs/*/run.json"), key=lambda p: p.stat().st_mtime)
        return [json.loads(path.read_text()) for path in found]

    def probes_passed(self) -> None:
        """Record a passed mechanism probe and arm 0 run 1, as slice 13's order does."""
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.evidence / "probe-mechanism.json").write_text('{"go": true}')

    def baseline(self, slice_cost: float = 100.0) -> None:
        """Record arm 0 run 1 in spend.json, for D6."""
        self.evidence.mkdir(parents=True, exist_ok=True)
        run = {
            "id": "arm0-run1-x",
            "kind": "arm",
            "arm": 0,
            "run": 1,
            "usd": 1.0,
            "status": "done",
            "slice_cost": {"01-small": slice_cost, "02-medium": slice_cost},
        }
        (self.evidence / "spend.json").write_text(json.dumps({"runs": [run]}))


def full_script() -> list[dict]:
    """Both slices through execute and audit, one status change per poll."""
    steps = []
    for name in ("01-small", "02-medium"):
        for status in ("executing", "executed", "audited"):
            steps.append({"status": [[name, status]]})
    return steps


@pytest.fixture
def world(tmp_path: Path) -> World:
    """A fresh fake host whose gate says go."""
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _no_real_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process tests never see a real rig either."""
    monkeypatch.setenv("HACF_HOST_RIG", shutil.which("false") or "false")
