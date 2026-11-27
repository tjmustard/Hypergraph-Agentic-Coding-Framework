"""dev/openrig_watch.sh, one watcher run per test (release-0.6.2 slice 4, R16 and R20).

Release-export slice 1 made the notifier, the row destination and the evidence root settings.
Offline and in tmp dirs only: stub `npm`, `rig`, a stub notifier and `pgrep` on PATH (no real npm,
daemon or notification is ever reached: PATH holds only the stubs and /usr/bin:/bin), a stub
`--compat` that writes an evidence dir with a verdict.json, a fake meminfo and tmp state and
evidence dirs. The sandbox's default environment is a configured install (a notifier and a row
destination set); the neutral defaults are tested with both unset.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from openrig_versions import unlisted_in_range

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

WATCH = REPO_ROOT / "dev" / "openrig_watch.sh"
INTEGRITY = "sha512-" + "A" * 86 + "=="
DESTINATION = "maintainer@example"
BROKE = "OpenRig watcher: checker broke"
SELF_TEST = "OpenRig checker self-test:"  # slice 03's tail rows, tested in their own file
ROW_PREFIX = ["queue", "create", "--source", "hacf-openrig-watch@hacf",
              "--destination", DESTINATION]  # fmt: skip

NPM = r"""#!/usr/bin/env python3
import json, os, sys
stub = os.environ["STUB_DIR"]
with open(os.path.join(stub, "npm.log"), "a") as h:
    h.write(json.dumps(sys.argv[1:]) + "\n")
plan = json.load(open(os.path.join(stub, "npm.json")))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--cache" in sys.argv:  # the value after --cache is not a package
    args.remove(sys.argv[sys.argv.index("--cache") + 1])
if "--registry" in sys.argv:
    args.remove(sys.argv[sys.argv.index("--registry") + 1])
if args[:1] != ["view"]:
    sys.exit(9)
pkg, field = args[1], args[2]
if field == "version" and pkg == "@openrig/cli":
    if plan["latest"] is None:
        sys.exit(1)
    print(plan["latest"])
elif field == "dist.integrity":
    print(plan["integrity"][pkg.split("@")[-1]])
else:
    sys.exit(9)
"""

RIG = r"""#!/usr/bin/env python3
import json, os, sys
stub = os.environ["STUB_DIR"]
argv = sys.argv[1:]
if argv == ["--version"]:  # logged apart: rig.log holds the rows. Unset: unparsable
    open(os.path.join(stub, "rig-version.log"), "a").write("--version\n")
    path = os.path.join(stub, "rig.version")
    if os.path.exists(path):
        print(open(path).read())
    sys.exit(0)
body = argv[argv.index("--body-file") + 1] if "--body-file" in argv else None
with open(os.path.join(stub, "rig.log"), "a") as h:
    h.write(json.dumps({"argv": argv, "body": open(body).read() if body else None,
                        "notified_first": os.path.exists(os.path.join(stub, "notify.log"))}) + "\n")
rc = os.path.join(stub, "rig.rc")
sys.exit(int(open(rc).read()) if os.path.exists(rc) else 0)
"""

NOTIFIER = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.path.join(os.environ["STUB_DIR"], "notify.log"), "a") as h:
    h.write(json.dumps(sys.argv[1:]) + "\n")
"""

PGREP = r"""#!/bin/sh
echo "$*" >>"$STUB_DIR/pgrep.log"
[ -f "$STUB_DIR/pgrep.match" ] && exit 0
exit 1
"""

# The stub compat run: consumes one outcome per call from compat-plan.json, writes an evidence dir
# (verdict.json as report() and accept would) and prints the final line; logs its argv.
COMPAT = r"""#!/usr/bin/env python3
import json, os, signal, sys, time
stub = os.environ["STUB_DIR"]
with open(os.path.join(stub, "compat.log"), "a") as h:
    h.write(json.dumps({"argv": sys.argv[1:], "tmpdir": os.environ.get("TMPDIR")}) + "\n")
plan = json.load(open(os.path.join(stub, "compat-plan.json")))
n = sum(1 for _ in open(os.path.join(stub, "compat.log"))) - 1
step = plan[min(n, len(plan) - 1)]
version = sys.argv[1]
if step.get("sleep"):
    open(os.path.join(stub, "compat.sleeping"), "w").write(str(os.getpid()))
    time.sleep(step["sleep"])
root = os.environ["HACF_COMPAT_EVIDENCE_ROOT"]
ev = os.path.join(root, f"openrig-compat-{version}-2026112{n}T000000Z")
os.makedirs(os.path.join(ev, "diffs"), exist_ok=True)
verdict = step["verdict"]
steps = [{"name": s, "result": "ok", "rc": 0, "seconds": 1.0, "detail": ""} for s in step["names"]]
base = step.get("baselines", ["0.7.21", "0.7.21"])
data = {"version": version, "verdict": verdict,
        "failed_step": "help" if verdict == "FAIL" else None,
        "baseline": base[0], "person_baseline": base[1],
        "watched_diff": {k: {"0.7.21": "differs"} for k in step.get(
            "watched", ["scope-audit.js"] if verdict == "REVIEW" else [])},
        "help_diff": {}, "json_paths_added": {},
        "harness": step.get("harness", {"agy": "ok" if verdict == "PASS" else "changed"}),
        "setup_error": step.get("setup_error", "npm: exit 2") if verdict == "SETUP-ERROR" else None,
        "timings": {},
        "steps": steps, "integrity": step.get("integrity", "sha512-" + "A" * 86 + "==")}
if "accept" in step:
    data["accept"] = {"result": step["accept"], "reason": "stub", "sha": step.get("sha", "f" * 40)}
if verdict == "REVIEW":
    open(os.path.join(ev, "diffs", "watched-scope-audit.js-0.7.21.diff"), "w").write("-a\n+b\n")
if step.get("raw") is not None:
    open(os.path.join(ev, "verdict.json"), "w").write(step["raw"])
elif not step.get("no_verdict"):
    json.dump(data, open(os.path.join(ev, "verdict.json"), "w"))
tail = " help" if verdict == "FAIL" else ""
print(f"openrig-compat {version}: {verdict}{tail} ({ev})")
if "accept" in step:
    print(f"openrig-compat {version}: accept {step['accept']} (x): stub")
sys.exit(step["rc"])
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _person_contract_bytes() -> bytes:
    """The shipped contract without its watcher entries, so a listed watcher never moves CAND."""
    text = (REPO_ROOT / oc.CONTRACT_PATH).read_text(encoding="utf-8")
    return "".join(
        ln for ln in text.splitlines(keepends=True)
        if not (oc._ENTRY_RE.match(ln) and "accepted_by: watcher" in ln)
    ).encode()  # fmt: skip


def _contract_integrities() -> dict[str, str]:
    contract = oc._shipped(REPO_ROOT)._parse_contract(_person_contract_bytes())
    return {e.version: e.integrity for e in contract.accepted}


CAND = unlisted_in_range(oc._shipped(REPO_ROOT)._parse_contract(_person_contract_bytes()))[0]


class Watch:
    """A watcher sandbox: stubs, a fake worktree, state, evidence, meminfo."""

    def __init__(self, tmp: Path) -> None:
        """Build the sandbox under tmp."""
        self.tmp = tmp
        self.stub = tmp / "stub"
        self.bin = tmp / "bin"
        self.stub.mkdir()
        self.bin.mkdir()
        _write(self.bin / "npm", NPM)
        _write(self.bin / "notify-stub", NOTIFIER)
        _write(self.bin / "pgrep", PGREP)
        self.rig = _write(tmp / "rig", RIG)
        _write(tmp / "compat.py", COMPAT)
        # the watcher runs `bash <compat>`, as it runs dev/openrig_compat.sh
        self.compat = _write(tmp / "compat.sh", f'exec python3 "{tmp}/compat.py" "$@"\n')
        self.worktree = tmp / "worktree"
        (self.worktree / ".agents/scripts").mkdir(parents=True)
        (self.worktree / ".agents/config").mkdir(parents=True)
        (self.worktree / "dev").mkdir()
        (self.worktree / "dev/openrig_compat.sh").write_text("# placeholder\n", encoding="utf-8")
        for name in ("hyper_openrig.py", "_runtime.py"):
            if (REPO_ROOT / ".agents/scripts" / name).exists():
                (self.worktree / ".agents/scripts" / name).write_bytes(
                    (REPO_ROOT / ".agents/scripts" / name).read_bytes()
                )
        (self.worktree / oc.CONTRACT_PATH).write_bytes(_person_contract_bytes())
        self.state = tmp / "state"
        self.evidence = tmp / "evidence"
        self.tmpdir = tmp / "tmpdir"
        self.meminfo = tmp / "meminfo"
        self.mem(8192)
        self.npm(f"{CAND}")

    def mem(self, mb: int) -> None:
        """Set the fake MemAvailable in MB."""
        self.meminfo.write_text(f"MemTotal:       65000000 kB\nMemAvailable:   {mb * 1024} kB\n",
                                encoding="utf-8")  # fmt: skip

    def npm(self, latest: str | None, integrity: dict[str, str] | None = None) -> None:
        """Set what the stub npm reports: the latest version and integrities."""
        plan = {"latest": latest, "integrity": {**_contract_integrities(), **(integrity or {})}}
        (self.stub / "npm.json").write_text(json.dumps(plan), encoding="utf-8")

    def plan(self, *steps: dict) -> None:
        """Set the stub compat run's outcomes, one per call."""
        full = [{"names": list(oc.REQUIRED_STEPS), **s} for s in steps]
        (self.stub / "compat-plan.json").write_text(json.dumps(full), encoding="utf-8")
        (self.stub / "compat.log").unlink(missing_ok=True)

    def env(self, **extra: str | None) -> dict[str, str]:
        """The watcher's environment: stubs first on PATH, fake HOME, a configured install.

        A keyword set to None removes that variable (the unset, neutral default).
        """
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp / "home"),
               "STUB_DIR": str(self.stub), "TMPDIR": str(self.tmpdir),
               "HACF_WATCH_WORKTREE": str(self.worktree), "HACF_WATCH_RIG": str(self.rig),
               "HACF_WATCH_NOTIFY_CMD": "notify-stub", "HACF_WATCH_ROW_DESTINATION": DESTINATION,
               "USER": os.environ.get("USER", "nobody"), "LANG": "C.UTF-8"}  # fmt: skip
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def argv(self, *extra: str, evidence_root: bool = True) -> list[str]:
        """The watcher's argv with every test seam pointed into tmp."""
        root = ["--evidence-root", str(self.evidence)] if evidence_root else []
        return ["bash", str(WATCH), "--compat", str(self.compat), "--state-dir", str(self.state),
                *root, "--meminfo", str(self.meminfo),
                "--gate-wait", "0", "--gate-max", "0", *extra]  # fmt: skip

    def run(self, *extra: str, evidence_root: bool = True, **env: str | None
            ) -> subprocess.CompletedProcess:  # fmt: skip
        """Run the script; return the completed process."""
        return subprocess.run(self.argv(*extra, evidence_root=evidence_root), capture_output=True,
                              text=True, env=self.env(**env), timeout=120, check=False)  # fmt: skip

    def lines(self, name: str) -> list:
        """The JSON lines a stub logged."""
        path = self.stub / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def rows(self) -> list[dict]:
        """The rows the stub rig received, the tail's self-test rows left out."""
        return [r for r in self.lines("rig.log") if not _summary(r).startswith(SELF_TEST)]

    def compat_calls(self) -> list[dict]:
        """The stub compat run's calls."""
        return self.lines("compat.log")

    def history(self) -> list[str]:
        """history.log's outcome lines (the `notify:` lines left out)."""
        return [line for line in self.history_all() if not line.startswith("notify: ")]

    def history_all(self) -> list[str]:
        """history.log's lines, the tail's self-test and upgrade lines left out."""
        path = self.state / "history.log"
        lines = path.read_text().splitlines() if path.exists() else []
        tail = ("selftest ", "upgrade:", f"notify: {SELF_TEST}", f"row-failed {SELF_TEST}")
        return [line for line in lines if not line.startswith(tail)]

    def counters(self) -> dict:
        """counters.json."""
        return json.loads((self.state / "counters.json").read_text())

    def stream(self, name: str = "new-version") -> dict:
        """One stream's counters."""
        return self.counters()["streams"][name]

    def reset_logs(self) -> None:
        """Forget what the stubs logged."""
        for name in ("rig.log", "notify.log", "compat.log", "npm.log", "pgrep.log"):
            (self.stub / name).unlink(missing_ok=True)


@pytest.fixture
def w(tmp_path: Path) -> Watch:
    """A fresh watcher sandbox."""
    return Watch(tmp_path)


def _summary(row: dict) -> str:
    argv = row["argv"]
    return argv[argv.index("--summary") + 1]


def _assert_row_argv(row: dict, summary: str, evidence: str) -> None:
    """Check the exact R20 argv: queue create, source, destination, summary, body, evidence."""
    argv = row["argv"]
    assert argv[:6] == ROW_PREFIX
    assert argv[6:8] == ["--summary", summary]
    assert argv[8] == "--body-file" and argv[9].endswith(".md")
    assert argv[10:] == ["--evidence-ref", evidence]
    assert not Path(argv[9]).exists()  # removed after the call
    assert row["notified_first"]  # the notifier ran before the row


# ---------------------------------------------------------------------------------------------
# The verdict branches


def test_pass_patch_is_accepted_and_the_update_row_is_exact(w: Watch) -> None:
    """PASS patch is accepted and the update row is exact."""
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    proc = w.run()
    assert proc.returncode == 0, proc.stderr
    calls = w.compat_calls()
    assert len(calls) == 1 and calls[0]["argv"] == [f"{CAND}", "--accept", "--harness", "agy"]
    assert calls[0]["tmpdir"] == str(w.tmpdir)
    rows = w.rows()
    assert len(rows) == 1
    evidence = str(w.evidence / f"openrig-compat-{CAND}-20261120T000000Z")
    _assert_row_argv(rows[0], f"OpenRig {CAND}: accepted locally (watcher)", evidence)
    assert "ffffffffffff" in rows[0]["body"] and evidence in rows[0]["body"]
    notified = w.lines("notify.log")
    assert notified == [[f"OpenRig {CAND}: accepted locally (watcher)",
                         "PASS; commit ffffffffffff on swimlane/openrig-accepted"]]  # fmt: skip
    assert json.loads((w.state / f"verdicts/{CAND}.json").read_text())["outcome"] == "accepted"
    assert (w.state / "latest.md").read_text().split()[:2] == [f"{CAND}", "accepted"]
    assert (w.state / "last-run").exists()
    assert len(w.history()) == 1
    assert list(w.tmpdir.iterdir()) == []  # $T removed


def test_registry_is_passed_through(w: Watch) -> None:
    """Registry is passed through."""
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run("--registry", "http://127.0.0.1:9/")
    assert w.compat_calls()[0]["argv"] == [
        f"{CAND}",
        "--accept",
        "--registry",
        "http://127.0.0.1:9/",
        "--harness",
        "agy",
    ]
    assert all("--registry" in call for call in w.lines("npm.log"))


def test_review_is_a_decision_row(w: Watch) -> None:
    """REVIEW is a decision row."""
    w.plan({"verdict": "REVIEW", "rc": 3})
    w.run()
    rows = w.rows()
    assert [_summary(r) for r in rows] == [f"OpenRig {CAND}: REVIEW: decision needed"]
    assert "watched-scope-audit.js-0.7.21.diff" in rows[0]["body"]
    assert "Verdict: REVIEW" in rows[0]["body"]


def test_fail_then_fail_is_one_retry_then_a_decision_row(w: Watch) -> None:
    """FAIL then FAIL is one retry then a decision row."""
    w.plan({"verdict": "FAIL", "rc": 1}, {"verdict": "FAIL", "rc": 1})
    w.run()
    assert len(w.compat_calls()) == 2
    rows = w.rows()
    assert [_summary(r) for r in rows] == [f"OpenRig {CAND}: FAIL: decision needed"]
    assert "failed step: help" in rows[0]["body"]
    assert json.loads((w.state / f"verdicts/{CAND}.json").read_text())["outcome"] == "FAIL"
    assert w.counters()["fails"] == {}


def test_fail_then_pass_takes_the_pass_branch(w: Watch) -> None:
    """FAIL then PASS takes the PASS branch."""
    w.plan({"verdict": "FAIL", "rc": 1}, {"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert len(w.compat_calls()) == 2
    assert [_summary(r) for r in w.rows()] == [f"OpenRig {CAND}: accepted locally (watcher)"]


def test_fail_with_the_gate_closed_before_the_retry_is_fail_pending(w: Watch) -> None:
    """FAIL with the gate closed before the retry is FAIL pending."""
    w.plan({"verdict": "FAIL", "rc": 1, "close_gate": True})
    # close the gate after the first run: the stub pgrep matches once compat has run
    pgrep = w.bin / "pgrep"
    _write(pgrep, '#!/bin/sh\n[ -f "$STUB_DIR/compat.log" ] && exit 0\nexit 1\n')
    w.run()
    assert len(w.compat_calls()) == 1 and w.rows() == []
    assert w.history()[-1].split()[1] == "fail-pending"
    assert not (w.state / f"verdicts/{CAND}.json").exists()
    assert w.counters()["fails"] == {f"{CAND}": 1}
    # tomorrow: the gate is open, and one more FAIL is final (no second retry)
    _write(pgrep, PGREP)
    w.reset_logs()
    w.plan({"verdict": "FAIL", "rc": 1})
    w.run()
    assert len(w.compat_calls()) == 1
    assert [_summary(r) for r in w.rows()] == [f"OpenRig {CAND}: FAIL: decision needed"]


def test_not_a_patch_is_a_decision_row(w: Watch) -> None:
    """Not a patch is a decision row."""
    w.npm("0.8.0")
    w.plan({"verdict": "PASS", "accept": "not-a-patch", "rc": 0})
    w.run()
    assert w.compat_calls()[0]["argv"] == ["0.8.0", "--accept", "--harness", "agy"]
    assert [_summary(r) for r in w.rows()] == ["OpenRig 0.8.0: not a patch: decision needed"]


@pytest.mark.parametrize("result", ["review-streak", "tests-failed", "wrong-branch", "dirty-tree",
                                    "error", "commit-failed"])  # fmt: skip
def test_a_refused_accept_is_a_review_row(result: str, w: Watch) -> None:
    """A refused accept is a REVIEW row."""
    rc = 3 if result == "review-streak" else 0
    w.plan({"verdict": "PASS", "accept": result, "rc": rc})
    w.run()
    rows = w.rows()
    assert [_summary(r) for r in rows] == [f"OpenRig {CAND}: REVIEW: decision needed"]
    assert f"Accept: {result}" in rows[0]["body"]


def test_pass_without_an_accept_result_is_never_reported_accepted(w: Watch) -> None:
    """PASS without an accept result is never reported accepted."""
    w.plan({"verdict": "PASS", "rc": 0})
    w.run()
    rows = w.rows()
    assert [_summary(r) for r in rows] == [f"OpenRig {CAND}: REVIEW: decision needed"]
    assert "Accept: accept-missing" in rows[0]["body"]


def test_an_already_final_verdict_only_writes_last_run(w: Watch) -> None:
    """An already final verdict only writes last run."""
    w.plan({"verdict": "REVIEW", "rc": 3})
    w.run()
    history = w.history()
    (w.state / "last-run").unlink()
    w.reset_logs()
    proc = w.run()
    assert proc.returncode == 0
    assert w.compat_calls() == [] and w.rows() == [] and w.history() == history
    assert (w.state / "last-run").exists()


def test_an_accepted_version_is_never_run(w: Watch) -> None:
    """An accepted version is never run."""
    w.npm("0.7.21")
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert w.compat_calls() == [] and w.rows() == [] and w.history() == []
    assert (w.state / "last-run").exists()


# ---------------------------------------------------------------------------------------------
# Runs without a verdict: the gate, setup errors


def test_gate_closed_is_deferred_without_a_row(w: Watch) -> None:
    """Gate closed is deferred without a row."""
    w.mem(6143)
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    proc = w.run("--gate-max", "1", "--gate-wait", "1")
    assert proc.returncode == 0
    assert w.compat_calls() == [] and w.rows() == []
    assert w.history()[-1].split()[1] == "deferred"
    assert w.stream()["deferred"] == 1


def test_gate_closed_by_another_pytest(w: Watch) -> None:
    """Gate closed by another pytest."""
    (w.stub / "pgrep.match").write_text("", encoding="utf-8")
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert w.compat_calls() == [] and w.history()[-1].split()[1] == "deferred"
    patterns = (w.stub / "pgrep.log").read_text()
    assert "-x pytest" in patterns


def test_gate_patterns_are_exact() -> None:
    """R16 step 3's patterns, not the old `pgrep -af 'check.sh|pytest'`."""
    text = WATCH.read_text(encoding="utf-8")
    assert 'pgrep -u "$user" -x pytest' in text
    assert "'^([^ ]*/)?python[0-9.]* -m pytest( |$)'" in text
    assert "'^([^ ]*/)?bash [^ ]*dev/scripts/check\\.sh( |$)'" in text
    assert "pgrep -af" not in text


@pytest.mark.parametrize("meminfo", ["", "MemAvailable: NaN kB\n",
                                     "MemAvailable: 99999999999999999999 MB\n",
                                     "garbage\n"])  # fmt: skip
def test_hostile_meminfo_keeps_the_gate_closed(meminfo: str, w: Watch) -> None:
    """Hostile meminfo keeps the gate closed."""
    w.meminfo.write_text(meminfo, encoding="utf-8")
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert w.compat_calls() == [] and w.history()[-1].split()[1] == "deferred"


def test_the_first_setup_error_is_one_row_and_the_next_none(w: Watch) -> None:
    """One checker-broke row per stream until a verdict resets it (replaces the 2nd-run latch)."""
    w.mem(100)
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert w.rows() == []  # one deferral: no row yet
    w.npm(None)  # an npm setup error: the first setup error of the stream
    w.run()
    rows = w.rows()
    assert [_summary(r) for r in rows] == [BROKE]
    _assert_row_argv(rows[0], BROKE, str(w.state))
    assert "new-version" in rows[0]["body"] and "npm view" in rows[0]["body"]
    w.run()
    assert len(w.rows()) == 1
    assert w.stream()["no_verdict"] == 3
    # a verdict resets it; the next setup error sends one more row
    w.npm(f"{CAND}")
    w.mem(8192)
    w.run()
    assert w.stream()["no_verdict"] == 0
    w.reset_logs()
    w.npm(None)
    w.run()
    w.run()
    assert [_summary(r) for r in w.rows()] == [BROKE]


def test_two_deferrals_in_a_row_send_one_row(w: Watch) -> None:
    """The deferred latch: the second run without a verdict is one row, the third none."""
    w.mem(100)
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
    w.run()
    assert w.rows() == []
    w.run()
    assert [_summary(r) for r in w.rows()] == ["OpenRig watcher: 2 runs without a verdict"]
    w.run()
    assert len(w.rows()) == 1


@pytest.mark.parametrize("rc", [137, 124, 143, 2])
def test_an_oom_timeout_or_signal_exit_is_a_setup_error_never_fail(rc: int, w: Watch) -> None:
    """An OOM timeout or signal exit is a setup error never FAIL."""
    verdict = "SETUP-ERROR" if rc == 2 else "FAIL"  # a killed run may leave a FAIL-looking file
    w.plan({"verdict": verdict, "rc": rc})
    w.run()
    assert len(w.compat_calls()) == 1  # no FAIL retry
    assert [_summary(r) for r in w.rows()] == [BROKE]  # R139: the first setup error is a row
    assert w.history()[-1].split()[1] == "setup"
    assert w.stream()["setup_errors"] == 1 and w.counters()["fails"] == {}
    assert not (w.state / f"verdicts/{CAND}.json").exists()


@pytest.mark.parametrize("step", [
    {"verdict": "PASS", "rc": 0, "no_verdict": True},
    {"verdict": "PASS", "rc": 0, "raw": "{"},
    {"verdict": "PASS", "rc": 0, "raw": '{"verdict": NaN}'},
    {"verdict": "PASS", "rc": 0, "raw": "[]"},
    {"verdict": "PASS", "accept": "accepted", "rc": 1},
    {"verdict": "REVIEW", "rc": 0},
    {"verdict": "SEED", "rc": 4},
])  # fmt: skip
def test_a_malformed_or_inconsistent_verdict_is_a_setup_error(step: dict, w: Watch) -> None:
    """A malformed or inconsistent verdict is a setup error."""
    w.plan(step)
    w.run()
    assert [_summary(r) for r in w.rows()] == [BROKE]  # R139: the first setup error is a row
    assert w.history()[-1].split()[1] == "setup"


def test_hostile_npm_version_is_a_setup_error(w: Watch) -> None:
    """Hostile npm version is a setup error."""
    for latest in ("v0.7.22", "0.7.22\n0.7.23", "", "0.7.22-rc.1"):
        w.npm(latest)
        w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
        w.run()
        assert w.compat_calls() == [] and w.history()[-1].split()[1] == "setup", latest


def test_integrity_change_of_an_accepted_entry_is_a_decision_row_once(w: Watch) -> None:
    """Integrity change of an accepted entry is a decision row once."""
    w.npm("0.7.21", {"0.7.21": INTEGRITY})
    w.run()
    rows = w.rows()
    assert [_summary(r) for r in rows] == ["OpenRig 0.7.21: integrity changed: decision needed"]
    _assert_row_argv(rows[0], "OpenRig 0.7.21: integrity changed: decision needed", str(w.state))
    assert INTEGRITY in rows[0]["body"]
    w.run()
    assert len(w.rows()) == 1
    w.npm("0.7.21", {"0.7.21": "sha512-" + "B" * 86 + "=="})  # a second new value: one more row
    w.run()
    assert len(w.rows()) == 2


def test_a_failed_row_makes_the_run_fail(w: Watch) -> None:
    """A failed row makes the run FAIL."""
    (w.stub / "rig.rc").write_text("1", encoding="utf-8")
    w.plan({"verdict": "REVIEW", "rc": 3})
    proc = w.run()
    assert proc.returncode == 1
    assert "the row failed" in (w.state / "run.log").read_text()


def test_an_unresolvable_notifier_is_logged_not_fatal(w: Watch) -> None:
    """An unresolvable notifier is logged, the run goes on and exits as the verdict's run would."""
    w.plan({"verdict": "REVIEW", "rc": 3})
    proc = w.run(HACF_WATCH_NOTIFY_CMD="no-such-notifier")
    assert proc.returncode == 0 and len(w.rows()) == 1
    assert w.lines("notify.log") == []
    assert "notifier no-such-notifier not found (not fatal)" in (w.state / "run.log").read_text()
    assert w.history()[-1].split()[1] == "REVIEW"


def test_the_watcher_calls_rig_only_for_queue_create(w: Watch) -> None:
    """The watcher calls rig only for queue create."""
    for step in (
        {"verdict": "PASS", "accept": "accepted", "rc": 0},
        {"verdict": "REVIEW", "rc": 3},
    ):
        w.plan(step)
        w.run()
        (w.state / f"verdicts/{CAND}.json").unlink(missing_ok=True)
    rows = w.lines("rig.log")
    assert rows and all(r["argv"][:2] == ["queue", "create"] for r in rows)
    assert (w.stub / "rig-version.log").read_text().split() == ["--version"] * 2


def test_corrupt_counters_are_moved_aside(w: Watch) -> None:
    """Corrupt counters are moved aside."""
    w.state.mkdir(parents=True)
    (w.state / "counters.json").write_text('{"deferred": NaN}', encoding="utf-8")
    w.plan({"verdict": "REVIEW", "rc": 3})
    proc = w.run()
    assert proc.returncode == 0 and len(w.rows()) == 1
    assert list(w.state.glob("counters.json.bad-*"))
    assert w.stream()["no_verdict"] == 0


# ---------------------------------------------------------------------------------------------
# The environment, the lock and signals


@pytest.mark.parametrize("unset", ["HACF_WATCH_WORKTREE", "HACF_WATCH_RIG", "TMPDIR"])
def test_missing_env_exits_2(unset: str, w: Watch) -> None:
    """Missing env exits 2."""
    env = w.env()
    del env[unset]
    proc = subprocess.run(
        w.argv(), capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert proc.returncode == 2 and unset in proc.stderr
    assert w.rows() == [] and w.compat_calls() == []


def test_check_env(w: Watch) -> None:
    """Check env."""
    node = _write(w.bin / "node", "#!/bin/sh\n")
    uv = _write(w.bin / "uv", "#!/bin/sh\n")
    proc = w.run("--check-env")
    assert proc.returncode == 0, proc.stdout
    out = dict(line.split(": ", 1) for line in proc.stdout.splitlines())
    assert out == {"npm": str(w.bin / "npm"), "node": str(node), "uv": str(uv),
                   "rig": str(w.rig), "notifier": str(w.bin / "notify-stub"),
                   "row destination": DESTINATION, "upgrade destination": "none",
                   "harnesses": "agy", "failure notifier": "none",
                   "evidence root": str(w.evidence),
                   "worktree": str(w.worktree)}  # fmt: skip
    (w.bin / "notify-stub").unlink()
    proc = w.run("--check-env")
    assert proc.returncode == 0  # the notifier is optional
    assert "notifier: unresolved notify-stub" in proc.stdout.splitlines()
    proc = w.run("--check-env", evidence_root=False, HACF_WATCH_NOTIFY_CMD=None,
                 HACF_WATCH_ROW_DESTINATION=None)  # fmt: skip
    assert proc.returncode == 0
    out = dict(line.split(": ", 1) for line in proc.stdout.splitlines())
    assert out["notifier"] == "none" and out["row destination"] == "none"
    assert out["evidence root"] == str(w.tmp / "home/.local/state/hacf/evidence")
    (w.bin / "npm").unlink()
    proc = w.run("--check-env")
    assert proc.returncode != 0 and "npm: missing" in proc.stdout
    assert w.rows() == [] and not w.state.exists()


def test_check_env_without_rig_or_worktree_fails(w: Watch) -> None:
    """Check env without rig or worktree fails."""
    _write(w.bin / "node", "#!/bin/sh\n")
    _write(w.bin / "uv", "#!/bin/sh\n")
    assert w.run("--check-env", HACF_WATCH_RIG="relative/rig").returncode != 0
    assert w.run("--check-env", HACF_WATCH_WORKTREE=str(w.tmp / "nowhere")).returncode != 0


def test_one_run_at_a_time(w: Watch) -> None:
    """One run at a time."""
    w.state.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(w.state / "lock"), "sleep", "30"])  # noqa: S603, S607
    try:
        time.sleep(0.3)
        w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0})
        proc = w.run()
        assert proc.returncode == 0 and "another run" in proc.stderr
        assert w.compat_calls() == [] and w.rows() == []
    finally:
        holder.kill()
        holder.wait()


def test_term_removes_the_temp_dir_and_stops_compat(w: Watch) -> None:
    """Term removes the temp dir and stops compat."""
    w.plan({"verdict": "PASS", "accept": "accepted", "rc": 0, "sleep": 60})
    proc = subprocess.Popen(w.argv(), env=w.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 30
    while not (w.stub / "compat.sleeping").exists():
        assert time.monotonic() < deadline, "the stub compat never started"
        time.sleep(0.1)
    temp = [p for p in w.tmpdir.iterdir() if p.name.startswith("openrig-watch-")]
    assert len(temp) == 1 and temp[0].is_dir()
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=30)
    assert rc == 143
    assert not temp[0].exists()
    pid = int((w.stub / "compat.sleeping").read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert w.rows() == []
    assert w.history()[-1].split()[1] == "interrupted"


# ---------------------------------------------------------------------------------------------
# The settings and their neutral defaults (release-export slice 1)


def test_unset_settings_call_no_notifier_and_no_rig_but_are_never_silent(w: Watch) -> None:
    """With no notifier and no row destination, a REVIEW calls neither and still records it."""
    w.plan({"verdict": "REVIEW", "rc": 3})
    proc = w.run(HACF_WATCH_NOTIFY_CMD=None, HACF_WATCH_ROW_DESTINATION=None)
    assert proc.returncode == 0, proc.stderr
    assert w.lines("notify.log") == [] and w.rows() == []
    assert not (w.stub / "rig.log").exists()
    line = (f"notify: OpenRig {CAND}: REVIEW: decision needed"
            " | every step green; watched files differ")  # fmt: skip
    assert proc.stdout.splitlines() == [line]
    assert w.history_all()[-1] == line
    assert w.history()[-1].split()[:2] == [f"{CAND}", "REVIEW"]
    assert (w.state / "latest.md").read_text().split()[:2] == [f"{CAND}", "REVIEW"]


def test_the_notifier_and_row_argv_are_exact(w: Watch) -> None:
    """A configured notifier gets <summary> <line>; the row goes to the configured destination."""
    w.plan({"verdict": "FAIL", "rc": 1}, {"verdict": "FAIL", "rc": 1})
    proc = w.run(HACF_WATCH_ROW_DESTINATION="someone-else@rig")
    assert proc.returncode == 0, proc.stderr
    summary = f"OpenRig {CAND}: FAIL: decision needed"
    assert w.lines("notify.log") == [[summary, "FAIL twice: help"]]
    rows = w.rows()
    assert len(rows) == 1
    argv = rows[0]["argv"]
    assert argv[:6] == ["queue", "create", "--source", "hacf-openrig-watch@hacf",
                        "--destination", "someone-else@rig"]  # fmt: skip
    assert argv[6:8] == ["--summary", summary] and argv[8] == "--body-file"
    evidence = w.evidence / f"openrig-compat-{CAND}-20261121T000000Z"
    assert argv[10:] == ["--evidence-ref", str(evidence)]
    assert f"notify: {summary} | FAIL twice: help" in proc.stdout.splitlines()


def test_only_a_notifier_or_only_a_destination(w: Watch) -> None:
    """Each setting works alone."""
    w.plan({"verdict": "REVIEW", "rc": 3})
    w.run(HACF_WATCH_ROW_DESTINATION=None)
    assert len(w.lines("notify.log")) == 1 and w.rows() == []
    (w.state / f"verdicts/{CAND}.json").unlink()
    w.reset_logs()
    w.plan({"verdict": "REVIEW", "rc": 3})
    w.run(HACF_WATCH_NOTIFY_CMD=None)
    assert w.lines("notify.log") == [] and len(w.rows()) == 1


@pytest.mark.parametrize("source", ["flag", "env", "xdg", "home"])
def test_evidence_root_precedence(source: str, w: Watch) -> None:
    """--evidence-root wins, else HACF_COMPAT_EVIDENCE_ROOT, else the XDG state default."""
    flag, env_root, xdg = w.tmp / "flag-root", w.tmp / "env-root", w.tmp / "xdg"
    want = {"flag": flag, "env": env_root, "xdg": xdg / "hacf/evidence",
            "home": w.tmp / "home/.local/state/hacf/evidence"}[source]  # fmt: skip
    extra = ("--evidence-root", str(flag)) if source == "flag" else ()
    env = {"HACF_COMPAT_EVIDENCE_ROOT": str(env_root) if source in ("flag", "env") else None,
           "XDG_STATE_HOME": str(xdg) if source != "home" else None}  # fmt: skip
    proc = w.run("--check-env", *extra, evidence_root=False, **env)
    assert f"evidence root: {want}" in proc.stdout.splitlines()
    w.plan({"verdict": "REVIEW", "rc": 3})
    proc = w.run(*extra, evidence_root=False, **env)
    assert proc.returncode == 0, proc.stderr
    assert w.compat_calls()[0]["argv"] == [f"{CAND}", "--accept", "--harness", "agy"]
    assert w.history()[-1].split()[3] == str(want / f"openrig-compat-{CAND}-20261120T000000Z")
    assert [p.name for p in want.iterdir()] == [f"openrig-compat-{CAND}-20261120T000000Z"]


# Host names that must never reach a public tree (mission R16, R22). Stored only as
# (sha256 hex of the UTF-8 bytes, length in bytes), so this source carries none of them in
# readable form; the last one counts only as a whole word.
HOST_NAME_DIGESTS = (
    ("bcb1fd73a9e9def453a3fc749589823ce8542eab5c0508d15b29084ccc2c6898", 19, False),
    ("8a9c0ea794afa662050a186248380b00cf4a6d0524f3fb6cdd3e4629aa00b05a", 9, False),
    ("671414de98ee39038f662e4d1df5ba28ca85f79aa25a784104558f660612bbd8", 15, False),
    ("56a719d088fa0e7180e40f2a6ef5b7e76c9e894a50d5efd786cd79461394018b", 10, False),
    ("4985842a779748cfa0f2b32c6f9420960984295fef685d06378639a3ea2008ce", 2, True),
)
GENERIC_ACCOUNTS = {"root", "runner", "user", "ubuntu", "ci", "build", "nobody"}
WORD_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
TOKEN_BYTES = WORD_BYTES | frozenset(b".-")
SCANNED = (
    "dev/openrig_watch.sh",
    "dev/openrig_watch_install.sh",
    "dev/openrig_compat.sh",
    "dev/openrig_compat.py",
    "DEVELOPMENT.md",
    "dev/tests/compat/test_openrig_watch.py",
    "dev/tests/compat/test_openrig_watch_install.py",
    "dev/tests/compat/test_openrig_watch_rows.py",
    "dev/tests/compat/test_openrig_watch_upgrade.py",
    "dev/tests/compat/test_openrig_compat.py",
)


def account_name() -> str | None:
    """This host's account name, read at run time; None when generic or too short to matter."""
    import getpass

    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no LOGNAME/USER: fall back to the passwd entry
        import pwd

        name = pwd.getpwuid(os.getuid()).pw_name
    return None if len(name) < 3 or name in GENERIC_ACCOUNTS else name


def _bounded(data: bytes, at: int, size: int, inner: frozenset[int]) -> bool:
    """True when the bytes just before and just after data[at:at+size] are not in `inner`."""
    before = data[at - 1] if at > 0 else None
    after = data[at + size] if at + size < len(data) else None
    return before not in inner and after not in inner


def digest_hits(data: bytes, needles=HOST_NAME_DIGESTS) -> list[tuple[int, int]]:
    """(byte offset, needle index) of every window whose sha256 matches a stored needle."""
    import hashlib

    hits = []
    for index, (digest, size, whole_word) in enumerate(needles):
        for at in range(len(data) - size + 1):
            if hashlib.sha256(data[at : at + size]).hexdigest() != digest:
                continue
            if not whole_word or _bounded(data, at, size, WORD_BYTES):
                hits.append((at, index))
    return hits


def account_hits(data: bytes, name: str | None) -> list[int]:
    """Byte offsets where the account name stands as a whole token."""
    if name is None:
        return []
    needle, found, at = name.encode(), [], data.find(name.encode())
    while at != -1:
        if _bounded(data, at, len(needle), TOKEN_BYTES):
            found.append(at)
        at = data.find(needle, at + 1)
    return found


def test_the_digest_scanner_finds_what_it_should() -> None:
    """The scanner flags a stored needle by offset and index, and honours the word bounds."""
    import hashlib

    needles = (
        (hashlib.sha256(b"needle-one").hexdigest(), 10, False),
        (hashlib.sha256(b"AB").hexdigest(), 2, True),
    )
    assert digest_hits(b"xx needle-one yy", needles) == [(3, 0)]
    assert digest_hits(b"AB, xABx, AB_", needles) == [(0, 1)]
    assert account_hits(b"a/zq9name/b zq9name.x zq9namez", "zq9name") == [2]
    assert account_hits(b"anything", None) == []


def test_the_watcher_files_carry_no_host_names() -> None:
    """No host-specific name or account name in the watcher files, the docs or these tests."""
    paths = [REPO_ROOT / rel for rel in SCANNED]
    paths += sorted((REPO_ROOT / "dev/systemd").iterdir())
    name = account_name()
    hits = []
    for path in paths:
        data = path.read_bytes()
        hits += [f"{path.name}@{at}: needle {i}" for at, i in digest_hits(data)]
        hits += [f"{path.name}@{at}: account name" for at in account_hits(data, name)]
    assert len(paths) == 13 and hits == []
