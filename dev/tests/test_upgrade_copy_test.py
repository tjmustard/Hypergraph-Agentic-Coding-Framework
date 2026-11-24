"""Tests for dev/scripts/upgrade_copy_test.py (OPR.99.0.10.11).

Fixture mode end to end and its negative cases, and live mode's bwrap wrapper, unit-tested only
(live mode never runs here).

Every run's work dir is under tmp_path (TMPDIR), and each test checks it was removed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, clean_env

SCRIPT = REPO_ROOT / "dev" / "scripts" / "upgrade_copy_test.py"


def load():
    """Import the script as a module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("upgrade_copy_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def uct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The module, with TMPDIR (so the work dir) under tmp_path/t."""
    tmp = tmp_path / "t"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    return load()


def lines(out: str) -> dict[int, str]:
    """Each check line by its number, and the verdict under 0."""
    found = {}
    for line in out.splitlines():
        parts = line.split(" ", 2)
        if parts[0] in ("PASS", "FAIL") and len(parts) > 1 and parts[1].isdigit():
            found[int(parts[1])] = parts[0]
        elif line.startswith("VERDICT: "):
            found[0] = line.removeprefix("VERDICT: ")
    return found


def test_fixture_mode_passes_end_to_end(tmp_path: Path) -> None:
    """fixture: nine PASS lines in order, VERDICT: PASS, exit 0, and the work dir removed."""
    tmp = tmp_path / "t"
    tmp.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "fixture"],
        env=clean_env(TMPDIR=str(tmp)),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout.splitlines()
    assert [x.split(" ", 2)[:2] for x in out[:9]] == [["PASS", str(n)] for n in range(1, 10)]
    assert out[9] == "VERDICT: PASS" and len(out) == 10
    assert "4 .mcp.json byte-identical after install and re-install" in out[3]
    assert list(tmp.iterdir()) == []


def test_planted_change_to_an_original_fails_check_8(uct, tmp_path, capsys, monkeypatch) -> None:
    """A change to an original during the run fails check 8 (and only 8), exit 1."""
    restore = uct.CopyTest.restore

    def restore_then_plant(self, copy, home, backup, pre):
        restore(self, copy, home, backup, pre)
        with open(self.specs[0] / "CULTURE.md", "a") as handle:
            handle.write("planted\n")

    monkeypatch.setattr(uct.CopyTest, "restore", restore_then_plant)
    assert uct.main(["fixture"]) == 1
    out = capsys.readouterr().out
    found = lines(out)
    assert found[8] == "FAIL" and found[0] == "FAIL"
    assert all(found[n] == "PASS" for n in (1, 2, 3, 4, 5, 6, 7, 9)), out
    assert "spec-1-eta-like (CULTURE.md)" in out
    assert list((tmp_path / "t").iterdir()) == []


def test_validate_failure_fails_check_6(uct, tmp_path, capsys, monkeypatch) -> None:
    """A fake `rig spec validate` failure on the applied copy fails check 6 only; exit 1."""
    validate_applied = uct.CopyTest.validate_applied

    def validate_with_a_failing_rig(self, copy, cwd_before):
        flag = self.work / "fake" / "validate.exit"
        flag.write_text("1")
        try:
            validate_applied(self, copy, cwd_before)
        finally:
            flag.unlink()

    monkeypatch.setattr(uct.CopyTest, "validate_applied", validate_with_a_failing_rig)
    assert uct.main(["fixture"]) == 1
    out = capsys.readouterr().out
    found = lines(out)
    assert found[6] == "FAIL" and found[0] == "FAIL"
    assert all(found[n] == "PASS" for n in (1, 2, 3, 4, 5, 7, 8, 9)), out
    assert "1-eta-like validate exit 1" in out and "2-cleanroom-like validate exit 1" in out
    assert list((tmp_path / "t").iterdir()) == []


def test_live_without_bwrap_exits_2(tmp_path: Path) -> None:
    """Live mode with no bwrap on PATH: exit 2 and the missing tool named; nothing is made."""
    tmp = tmp_path / "t"
    tmp.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("rsync", "git", "rig"):
        (bindir / tool).write_text("#!/bin/sh\nexit 0\n")
        (bindir / tool).chmod(0o755)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "live", "--project", str(tmp_path / "p"),
         "--live-spec", str(tmp_path / "s"), "--rig", "demo-hacf"],
        env=clean_env(PATH=str(bindir), TMPDIR=str(tmp)),
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "missing tool(s) on PATH: bwrap" in proc.stderr
    assert proc.stdout == "" and list(tmp.iterdir()) == []


@pytest.mark.parametrize("argv", [[], ["live"], ["live", "--project", "x", "--rig", "r"]])
def test_usage_exits_2(uct, argv: list[str]) -> None:
    """A usage error exits 2."""
    assert uct.main(argv) == 2


def test_evidence_inside_the_checkout_is_refused(uct, tmp_path, capsys, monkeypatch) -> None:
    """Live --evidence inside this checkout: exit 2 before anything runs."""
    monkeypatch.setattr(uct, "need_tools", lambda *names: None)
    code = uct.main(["live", "--project", str(tmp_path), "--live-spec", str(tmp_path),
                     "--rig", "r", "--evidence", str(REPO_ROOT / "dev" / "x")])  # fmt: skip
    assert code == 2
    assert "--evidence" in capsys.readouterr().err
    assert not (REPO_ROOT / "dev" / "x").exists()


def test_bwrap_argv_isolates_checks_3_to_7(uct, tmp_path: Path) -> None:
    """The live wrapper: no network, a private /tmp before every bind, originals read-only."""
    work, project, spec, ev = (tmp_path / n for n in ("w", "proj", "spec", "ev"))
    argv = uct.bwrap_argv(work, [project, spec], ev, ["python3", "inner"])
    assert argv[0] == "bwrap" and "--unshare-net" in argv and "--die-with-parent" in argv
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs + 1] == "/tmp"
    binds = [i for i, a in enumerate(argv) if a in ("--bind", "--ro-bind")]
    assert binds and min(binds) > tmpfs
    pairs = {(argv[i], argv[i + 1]) for i in binds}
    assert ("--ro-bind", str(project)) in pairs and ("--ro-bind", str(spec)) in pairs
    assert ("--bind", str(project)) not in pairs and ("--bind", str(spec)) not in pairs
    assert {p for kind, p in pairs if kind == "--bind"} == {str(work), str(ev)}
    assert argv[argv.index("--") + 1 :] == ["python3", "inner"]


def test_live_rig_wrapper_serves_reads_and_passes_only_validate(uct, tmp_path: Path) -> None:
    """Inside bwrap, `rig` serves recorded reads, runs only `spec validate`, refuses the rest.

    The real rig runs only for `spec validate`; any other verb exits 97.
    """
    rec = tmp_path / "rec"
    rec.mkdir()
    recorded = {json.dumps(["specs", "ls"]): {"rc": 0, "stdout": "rig a x\n"}}
    (rec / "recorded.json").write_text(json.dumps(recorded))
    real = tmp_path / "real-rig"
    real.write_text('#!/bin/sh\necho "real $*"\nexit 3\n')
    real.chmod(0o755)
    wrapper = tmp_path / "rig"
    uct.write_exe(wrapper, f"#!{sys.executable}\n{uct.LIVE_RIG}")
    env = dict(os.environ, HACF_COPYTEST_RIGREC=str(rec), HACF_COPYTEST_REAL_RIG=str(real))

    def call(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(wrapper), *args], env=env, capture_output=True, text=True,
                              timeout=60, check=False)  # fmt: skip

    served = call("specs", "ls")
    assert (served.returncode, served.stdout) == (0, "rig a x\n")
    validate = call("spec", "validate", "/x/rig.yaml")
    assert (validate.returncode, validate.stdout) == (3, "real spec validate /x/rig.yaml\n")
    for argv in (["seat", "launch", "x"], ["down"], ["specs", "ls", "--json"]):
        refused = call(*argv)
        assert refused.returncode == 97 and "not a recorded read-only verb" in refused.stderr
    logged = [json.loads(x) for x in (rec / "argv.log").read_text().splitlines()]
    assert logged[0] == ["specs", "ls"] and len(logged) == 5
