"""Tests for dev/scripts/upgrade_copy_test.py (OPR.99.0.10.11).

Fixture mode end to end and its negative cases, and live mode's bwrap wrapper, unit-tested only
(live mode never runs here).

Every run's work dir is under tmp_path (TMPDIR), and each test checks it was removed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, clean_env

SCRIPT = REPO_ROOT / "dev" / "scripts" / "upgrade_copy_test.py"


def load(script: Path = SCRIPT):
    """Import the script as a module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("upgrade_copy_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git_env() -> dict[str, str]:
    """An isolated git environment for the throwaway commit."""
    return clean_env(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Copy Test",
        GIT_AUTHOR_EMAIL="copy-test@example.invalid",
        GIT_COMMITTER_NAME="Copy Test",
        GIT_COMMITTER_EMAIL="copy-test@example.invalid",
    )


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git in cwd (the isolated environment); a failure fails the test."""
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=stdin, capture_output=True, env=_git_env(), check=True,
    )  # fmt: skip
    return done.stdout


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway clone whose HEAD commits this checkout's working tree (P34/R64, v0.6.6).

    The copy test installs this checkout with `--ref HEAD` and seat-config-upgrade.py refuses a
    dirty source, so on an uncommitted tree (a version bump, a slice in progress) both fail for a
    reason that is not a defect. Each test that runs the installer therefore runs the script from
    this clone: the clone's HEAD is the working tree, committed with hooks run (never
    `--no-verify`; a fresh clone has only the hooks the clone itself carries).
    """
    dest = tmp_path_factory.mktemp("checkout") / "repo"
    subprocess.run(
        ["git", "clone", "-q", str(REPO_ROOT), str(dest)], env=_git_env(), check=True,
    )  # fmt: skip
    changed = _git(REPO_ROOT, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = [e for e in changed.decode().split("\0") if e]
    paths: list[str] = []
    skip_next = False
    for entry in entries:
        if skip_next:  # the origin path of a rename: handled as a deletion below
            paths.append(entry)
            skip_next = False
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        skip_next = "R" in code or "C" in code
    for path in paths:
        source, target = REPO_ROOT / path, dest / path
        if source.is_file() or source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            shutil.copy2(source, target, follow_symlinks=False)
        elif target.exists() or target.is_symlink():
            target.unlink()
    if paths:
        _git(dest, "add", "--all", "--", *paths)
        _git(dest, "commit", "-q", "-m", "throwaway: the working tree of this checkout")
    return dest


@pytest.fixture
def uct_wt(checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The module from the throwaway clone, with TMPDIR (so the work dir) under tmp_path/t."""
    tmp = tmp_path / "t"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    return load(checkout / "dev" / "scripts" / "upgrade_copy_test.py")


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


def test_fixture_mode_passes_end_to_end(tmp_path: Path, checkout: Path) -> None:
    """fixture: nine PASS lines in order, VERDICT: PASS, exit 0, and the work dir removed."""
    tmp = tmp_path / "t"
    tmp.mkdir()
    proc = subprocess.run(
        [sys.executable, str(checkout / "dev" / "scripts" / "upgrade_copy_test.py"), "fixture"],
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


def test_planted_change_to_an_original_fails_check_8(uct_wt, tmp_path, capsys, monkeypatch) -> None:
    """A change to an original during the run fails check 8 (and only 8), exit 1."""
    uct = uct_wt
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


def test_validate_failure_fails_check_6(uct_wt, tmp_path, capsys, monkeypatch) -> None:
    """A fake `rig spec validate` failure on the applied copy fails check 6 only; exit 1."""
    uct = uct_wt
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


# --- OPR.99.0.11.2: check 5 is a coverage assertion ----------------------------------------------

ROLES = ["architect", "audit", "execute", "lead", "red-team", "resolve", "scribe"]


def _after_setup(uct, monkeypatch, change) -> None:
    """Run `change(self)` right after FixtureTest.setup (the fake rig and the originals exist)."""
    setup = uct.FixtureTest.setup

    def setup_then_change(self):
        setup(self)
        change(self)

    monkeypatch.setattr(uct.FixtureTest, "setup", setup_then_change)


def test_check_5_fails_on_the_v065_defect_and_names_every_role(
    uct_wt, tmp_path, capsys, monkeypatch
) -> None:
    """(c) A seat half that reads `rig ps` without --full (the v0.6.5 script) fails check 5."""
    uct = uct_wt
    stale = tmp_path / "stale"
    shutil.copytree(uct.REPO_ROOT / "openrig" / "hacf", stale / "openrig" / "hacf")
    script = stale / "openrig" / "hacf" / "bin" / "seat-config-upgrade.py"
    text = script.read_text(encoding="utf-8")
    assert text.count('"--full", ') == 1 and text.count("node_role(node)") == 1
    # the v0.6.5 reads: no --full, the role from `profile` alone
    script.write_text(
        text.replace('"--full", ', "").replace("node_role(node)", 'node.get("profile")'),
        encoding="utf-8",
    )
    monkeypatch.setattr(uct, "SEAT_CONFIG", script)
    assert uct.main(["fixture"]) == 1
    out = capsys.readouterr().out
    found = lines(out)
    assert found[5] == "FAIL" and found[0] == "FAIL"
    check_5 = next(line for line in out.splitlines() if line.startswith("FAIL 5"))
    assert "the plan lists zero seats" in check_5
    for role in ROLES:
        assert f"no planned seat for role {role}" in check_5
    assert "seat build-execute@demo-hacf skipped" in check_5


def test_check_5_fails_on_zero_nodes(uct_wt, tmp_path, capsys, monkeypatch) -> None:
    """(d) A rig with no nodes plans zero seats; check 5 (i) fails."""
    uct = uct_wt
    _after_setup(uct, monkeypatch, lambda self: (self.fake / "ps.json").write_text("[]"))
    assert uct.main(["fixture"]) == 1
    out = capsys.readouterr().out
    assert lines(out)[5] == "FAIL"
    assert "the plan lists zero seats" in out
    assert list((tmp_path / "t").iterdir()) == []


def test_check_5_names_a_skipped_seat(uct_wt, capsys, monkeypatch) -> None:
    """(e) A node with an unknown profile is skipped, and check 5 (ii)/(iv) names the seat."""

    def bogus(self) -> None:
        nodes = json.loads((self.fake / "ps.json").read_text())
        (node,) = [n for n in nodes if n["logicalId"] == "spec.resolve"]
        node["profile"] = "bogus"
        (self.fake / "ps.json").write_text(json.dumps(nodes))

    _after_setup(uct_wt, monkeypatch, bogus)
    assert uct_wt.main(["fixture"]) == 1
    out = capsys.readouterr().out
    assert lines(out)[5] == "FAIL"
    assert "seat spec-resolve@demo-hacf skipped (no role 'bogus' in roles.yml)" in out
    assert "no planned seat for role resolve" in out


def test_f_the_fake_serves_only_the_recorded_shape(uct_wt, capsys, monkeypatch) -> None:
    """(f) Every node the fake serves has the key set of the recorded fixture's nodes."""
    uct = uct_wt
    recorded = json.loads(uct.RECORDED_PS.read_text())
    assert len({frozenset(n) for n in recorded}) == 1
    shape = set(recorded[0])
    seen: list[set[str]] = []

    def check(self) -> None:
        seen.extend(set(n) for n in json.loads((self.fake / "ps.json").read_text()))
        env = {"FAKE_RIG_DIR": str(self.fake), "PATH": os.environ["PATH"]}
        plain = subprocess.run(
            [sys.executable, "-c", uct.FAKE_RIG, "ps", "--nodes", "--rig", "demo-hacf", "--json"],
            env=env, capture_output=True, text=True, check=True,
        )  # fmt: skip
        gone = {"profile", "role", "model", "effort", "effortSource"}
        assert all(set(n) == shape - gone for n in json.loads(plain.stdout))

    _after_setup(uct, monkeypatch, check)
    assert uct.main(["fixture"]) == 0, capsys.readouterr().out
    assert seen and all(keys == shape for keys in seen)


# --- OPR.99.0.11.7: check 5's model half ---------------------------------------------------------


def _pin_opus(path: Path) -> None:
    """Test-local pin (lead ruling P40): rewrite a seat-config.yml copy to all-Opus defaults.

    These tests assert the model half against Opus defaults whatever tiered values the shipped
    file carries (OPR.99.0.11.8); only the copy changes.
    """
    out, in_seats = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith("seats:"):
            in_seats = True
        elif in_seats and line.strip() and not line.startswith(" "):
            in_seats = False
        if in_seats:
            line = re.sub(r"^(  [\w-]+:\s+)\{[^}]*\}", r"\1{model: claude-opus-5-5}", line)
        out.append(line)
    path.write_text("".join(out), encoding="utf-8")


def _opus_defaults(uct, tmp_path: Path, monkeypatch) -> None:
    """Point the module's HACF_DIR at a copy of openrig/hacf whose defaults are all Opus."""
    pinned = tmp_path / "opus-hacf"
    shutil.copytree(uct.HACF_DIR, pinned)
    _pin_opus(pinned / "seat-config.yml")
    monkeypatch.setattr(uct, "HACF_DIR", pinned)


def _patched_script(uct, tmp_path: Path, old: str, new: str) -> Path:
    """A copy of openrig/hacf with one source line of seat-config-upgrade.py replaced."""
    stale = tmp_path / "stale"
    shutil.copytree(uct.REPO_ROOT / "openrig" / "hacf", stale / "openrig" / "hacf")
    script = stale / "openrig" / "hacf" / "bin" / "seat-config-upgrade.py"
    text = script.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    script.write_text(text.replace(old, new), encoding="utf-8")
    _pin_opus(stale / "openrig" / "hacf" / "seat-config.yml")
    return script


def test_check_5_fails_when_the_plan_loses_its_model_lines(
    uct_wt, tmp_path, capsys, monkeypatch
) -> None:
    """(i) fixture mode: a plan with no model block fails check 5, naming each seat."""
    script = _patched_script(
        uct_wt,
        tmp_path,
        'lines += seat_model_block(seat, resolved.get(seat["role"], {}), verbs)',
        "pass",
    )
    monkeypatch.setattr(uct_wt, "SEAT_CONFIG", script)
    _opus_defaults(uct_wt, tmp_path, monkeypatch)
    assert uct_wt.main(["fixture"]) == 1
    out = capsys.readouterr().out
    assert lines(out)[5] == "FAIL" and lines(out)[0] == "FAIL"
    check_5 = next(line for line in out.splitlines() if line.startswith("FAIL 5"))
    for seat in ("build-execute@demo-hacf", "orch-lead@demo-hacf", "spec-resolve@demo-hacf"):
        assert f"no model line for seat {seat}" in check_5


def test_check_5_fails_when_the_lead_is_not_last(uct_wt, tmp_path, capsys, monkeypatch) -> None:
    """(i) fixture mode: a lead's model block that is not last fails check 5."""
    script = _patched_script(
        uct_wt,
        tmp_path,
        '    return lead_last(out, lambda s: s.get("role") == "lead" or is_lead(s["session"]))',
        "    return out",
    )
    monkeypatch.setattr(uct_wt, "SEAT_CONFIG", script)
    assert uct_wt.main(["fixture"]) == 1
    out = capsys.readouterr().out
    assert lines(out)[5] == "FAIL"
    assert "the lead's block (orch-lead@demo-hacf) is not last" in out


def _live_test(uct, tmp_path: Path):
    """A LiveTest whose `rig` is the fixture fake (the recorded ps), for coverage_failures only."""
    work = tmp_path / "work"
    fake = work / "fake"
    fake.mkdir(parents=True)
    (work / "bin").mkdir()
    uct.write_exe(work / "bin" / "rig", f"#!{sys.executable}\n{uct.FAKE_RIG}")
    (fake / "ps.json").write_text(uct.RECORDED_PS.read_text(encoding="utf-8"))
    test = uct.LiveTest(work, work / "p", [work / "s"], "demo-hacf", work / "ev", None)
    test.env = dict(
        test.env, FAKE_RIG_DIR=str(fake), PATH=f"{work / 'bin'}{os.pathsep}{test.env['PATH']}"
    )
    return test


def _good_plan(uct) -> str:
    """A plan text that satisfies check 5 for the recorded rig: seats, then each model block."""
    nodes = json.loads(uct.RECORDED_PS.read_text(encoding="utf-8"))
    nodes.sort(key=lambda n: n["logicalId"] == "orch.lead")  # the lead last
    seats = "".join(
        f"  {n['canonicalSessionName']} ({n['profile']}):\n    rules: {{}}\n" for n in nodes
    )
    models = ""
    for n in nodes:
        name = n["canonicalSessionName"]
        models += (
            f"  model {n['profile']} {name}: {n['model']} -> claude-opus-5-5 (source: default)\n"
            f'      rig seat set-model {name} --model claude-opus-5-5 --reason "x"\n'
            f"      rig seat launch {name} --fresh --stop   (at an idle point)\n"
            f'      rollback: rig seat set-model {name} --model claude-opus-5-5 --reason "y"\n'
        )
    return f"plan\nseats (the seat half):\n{seats}models (live state):\n{models}host lines (x):\n"


def test_check_5_model_half_in_live_mode_names_a_missing_line(
    uct, tmp_path: Path, monkeypatch
) -> None:
    """(i) live mode shares the check: the good plan passes; a removed model line names its seat."""
    _opus_defaults(uct, tmp_path, monkeypatch)
    test = _live_test(uct, tmp_path)
    good = _good_plan(uct)
    assert test.coverage_failures(good) == []
    seat = "build-execute@demo-hacf"
    gone = "\n".join(ln for ln in good.splitlines() if f"model execute {seat}:" not in ln)
    assert test.coverage_failures(gone) == [f"no model line for seat {seat} (execute)"]
    no_cmd = "\n".join(ln for ln in good.splitlines() if f"rig seat set-model {seat} " not in ln)
    assert test.coverage_failures(no_cmd) == [f"no set-model line for seat {seat}"]
    manual = good.replace(
        f'      rig seat set-model {seat} --model claude-opus-5-5 --reason "x"\n'
        f"      rig seat launch {seat} --fresh --stop   (at an idle point)\n"
        f'      rollback: rig seat set-model {seat} --model claude-opus-5-5 --reason "y"\n',
        "      model: manual (rig seat set-model not available on 0.6.13)\n",
    )
    assert test.coverage_failures(manual) == []  # a host without the verb gets the manual line
    head, tail = good.split("models (live state):\n")
    blocks, rest = (
        tail.split("host lines")[0].splitlines(keepends=True),
        "host lines" + (tail.split("host lines", 1)[1]),
    )
    lead_at = next(i for i, ln in enumerate(blocks) if "model lead orch-lead@demo-hacf" in ln)
    lead_first = blocks[lead_at : lead_at + 4] + blocks[:lead_at] + blocks[lead_at + 4 :]
    moved = head + "models (live state):\n" + "".join(lead_first) + rest
    assert test.coverage_failures(moved) == ["the lead's block (orch-lead@demo-hacf) is not last"]


def test_live_mode_records_the_hosts_help_probes(uct, tmp_path, monkeypatch) -> None:
    """The live recorder reads `seat set-model --help`, `set-effort --help` and `--version`."""
    work = tmp_path / "work"
    (work / "bin").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rig").write_text('#!/bin/sh\nif [ "$1" = ps ]; then echo "[]"; else echo ok; fi\n')
    (bin_dir / "rig").chmod(0o755)
    test = uct.LiveTest(work, work / "p", [work / "s"], "demo-hacf", work / "ev", None)
    test.outer = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    test.record_daemon_reads()
    recorded = json.loads((work / "rigrec" / "recorded.json").read_text())
    for argv in (["seat", "set-model", "--help"], ["seat", "set-effort", "--help"], ["--version"]):
        assert recorded[json.dumps(argv)]["rc"] == 0
