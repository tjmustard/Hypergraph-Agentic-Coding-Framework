"""The OpenRig range probe (OPR.0.6.2.2; release-0.6.2 R4-R11).

An unlisted rig inside the contract's range (0.7.x, x >= 16) is delegated to only after an
isolated, cached, content-keyed probe of the delegated surface passed; every other outcome is
native plus a warning, and no probe error escapes into hyper_spec or hyper_migrate. Every `rig`
here is a fake script on PATH that logs its argv and environment; no test runs the host's rig,
and every cache lives under tmp_path (the suite's XDG_CACHE_HOME is a session temp dir).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import hyper_openrig as h
import pytest
from conftest import (
    REAL_PROBE_CACHE,
    REAL_PROBE_CACHE_STATE,
    REPO_ROOT,
    SCRIPTS_DIR,
    SESSION_CACHE_HOME,
    _probe_cache_state,
    clean_env,
)

IN_RANGE = "0.7.22 (fake0722)"
# The baseline the fake rig prints: JSON key paths per JSON step, the help text's flags, and the
# exit code of every step.
BASE_JSON = {
    "scope_audit": ["ok", "findings", "findings[].code", "findings[].path", "summary.count"],
    "slice_ls": ["slices", "slices[].id", "slices[].state"],
    "scope_audit_after": ["ok", "findings", "findings[].code", "findings[].path", "summary.count"],
}
BASE_HELP = {
    "help_scope_audit": "--mission <m> --json",
    "help_slice_ls": "--mission <m> --state <s> --json",
    "help_proof_add": (
        "--artifact-type --verdict --candidate-sha --money-evidence --file --name --json"
    ),
}
BASE_EXIT = {step: 0 for step in h.PROBE_STEPS} | {"scope_audit": 1}

# The fake rig. Its config (JSON next to it) names the JSON key paths to print, the help texts,
# exit codes and mutations; it appends {"argv", "env"} to the log on every call.
FAKE_RIG = r"""#!{python}
import json, os, re, sys, time
from pathlib import Path

cfg = json.loads(Path({config!r}).read_text(encoding="utf-8"))
args = sys.argv[1:]
with open(cfg["log"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"argv": args, "env": dict(os.environ)}}) + "\n")
if os.environ.get("HACF_FAKE_HOLD"):
    Path(os.environ["HACF_FAKE_HOLD"]).write_text("running", encoding="utf-8")
mut = cfg.get("mutations", {{}})


def build(paths):
    tree = {{}}
    for path in paths:
        node = tree
        for tok in re.findall(r"\[\]|[^.\[\]]+", path):
            node = node.setdefault(tok, {{}})

    def render(node):
        if not node:
            return None
        if list(node) == ["[]"]:
            return [render(node["[]"])]
        return {{k: render(v) for k, v in node.items()}}

    return render(tree) or {{}}


if args == ["--version"]:
    print(cfg["version"])
    sys.exit(0)
if args[-1] == "--help":
    step = {{"scope audit": "help_scope_audit", "scope slice ls": "help_slice_ls",
             "proof add": "help_proof_add"}}[" ".join(args[:-1])]
    text = cfg["help"][step]
    for flag in mut.get("drop_flags", {{}}).get(step, []):
        text = text.replace(flag, "")
    print("Usage: rig " + " ".join(args[:-1]) + " [options]\n" + text)
    sys.exit(cfg["exit"][step])
time.sleep(mut.get("sleep", 0))
ws = Path(args[args.index("--workspace") + 1])
artifact = ws / "missions/release-9.9/slices/01-probe-active/proof/01-probe.md"
if args[0] == "scope" and "audit" in args:
    step = "scope_audit_after" if artifact.exists() else "scope_audit"
elif args[0] == "scope":
    step = "slice_ls"
else:
    step = "proof_add"
if step == "proof_add":
    opts = {{args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}}
    target = Path(args[args.index("add") + 1]) / "proof" / opts["--name"]
    target.parent.mkdir(exist_ok=True)
    header = cfg.get("header") or {{
        "slice": "OPR.9.9.1", "candidate_sha": opts["--candidate-sha"],
        "artifact_type": opts["--artifact-type"], "verdict": opts["--verdict"],
        "money_evidence": opts["--money-evidence"], "recorded_at": "<volatile>",
    }}
    for key, value in mut.get("header", {{}}).items():
        header[key] = value
    lines = [k + ": " + json.dumps(str(time.time_ns()) if v == "<volatile>" else v)
             for k, v in header.items()]
    body = Path(opts["--file"]).read_text(encoding="utf-8")
    target.write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body, encoding="utf-8")
    for rel in cfg.get("extra_writes", []):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("extra\n", encoding="utf-8")
    if mut.get("extra_write"):
        (ws / "rig-extra.json").write_text("{{}}\n", encoding="utf-8")
    if mut.get("rewrite"):
        spec = ws / "missions/release-9.9/slices/01-probe-active/SPEC.md"
        spec.write_text(spec.read_text(encoding="utf-8") + "\n", encoding="utf-8")
else:
    paths = [p for p in cfg["json"][step] if p not in mut.get("drop_keys", {{}}).get(step, [])]
    print(json.dumps(build(paths)))
if mut.get("crash") == step:
    os.kill(os.getpid(), 9)
sys.exit(mut.get("exit", {{}}).get(step, cfg["exit"][step]))
"""


class FakeRig:
    """A fake `rig` in its own bin dir, configured through a JSON file beside it."""

    def __init__(self, root: Path, version: str = IN_RANGE, **config: object) -> None:
        """Write the script and its config under root."""
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        self.log = root / "rig.log"
        self.config_path = root / "rig.json"
        self.config = {"version": version, "log": str(self.log), "json": BASE_JSON,
                       "help": BASE_HELP, "exit": BASE_EXIT, "mutations": {}}  # fmt: skip
        self.config.update(config)
        self.save()
        self.path = self.bin / "rig"
        self.path.write_text(
            FAKE_RIG.format(python=sys.executable, config=str(self.config_path)),
            encoding="utf-8",
        )
        self.path.chmod(0o755)

    def save(self) -> None:
        """Write the config (the script reads it on every call)."""
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def mutate(self, **mutations: object) -> None:
        """Replace the mutations."""
        self.config["mutations"] = mutations
        self.save()

    def calls(self) -> list[dict]:
        """Return every logged call ({"argv", "env"})."""
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

    def argvs(self) -> list[list[str]]:
        """Return every logged argv."""
        return [c["argv"] for c in self.calls()]


def derive_expect(observed: dict, volatile: list[str]) -> dict:
    """Return an expectation file from one record run (what dev/openrig_probe_record.sh writes)."""
    return {
        "rig_version": "0.7.22",
        "probe_schema": 1,
        "generator": "test",
        "exit_codes": observed["exit_codes"],
        "write_set": observed["write_set"],
        "volatile_keys": volatile,
        "c1_header": {
            k: h.PROBE_VOLATILE if k in volatile else v for k, v in observed["c1_header"].items()
        },
        "json_key_paths": observed["json_key_paths"],
        "help_flags": observed["help_flags"],
    }


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return (fake rig, expectation path): the fake first on PATH, its own expectation and cache.

    The expectation file is recorded from the fake itself with the shipped record mode, so a probe
    of the unchanged fake passes; tests then mutate the fake.
    """
    rig = FakeRig(tmp_path / "fake")
    monkeypatch.setenv("PATH", f"{rig.bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(h.tempfile, "tempdir", None)
    recorded = h.run_probe([str(rig.path)], IN_RANGE, record=True)
    assert recorded.outcome == "pass", recorded.detail
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(derive_expect(recorded.observed, ["recorded_at"])), "utf-8")
    monkeypatch.setattr(h, "PROBE_EXPECT_PATH", expect)
    monkeypatch.setattr(h, "_warned", set())
    rig.log.unlink()
    return rig, expect


def cache_dir(tmp_path: Path) -> Path:
    """Return the probe cache dir under tmp_path's XDG cache."""
    return tmp_path / "xdg" / "hacf" / "rig-probe"


def entries(tmp_path: Path) -> list[dict]:
    """Return the cache entries (not the evidence files) under tmp_path's XDG cache."""
    found = sorted(cache_dir(tmp_path).glob("*.json")) if cache_dir(tmp_path).is_dir() else []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in found
        if not p.name.endswith(".evidence.json")
    ]


def probe_argvs(rig: FakeRig) -> list[list[str]]:
    """Return the logged calls that are not `--version`."""
    return [a for a in rig.argvs() if a != ["--version"]]


# --- the rule and the outcomes --------------------------------------------------------------------


def test_pass_delegates(probe, tmp_path: Path, capsys) -> None:
    """A clean probe pass writes a PASS entry, and plan_rig() delegates (via_probe)."""
    rig, _ = probe
    assert h.plan_rig().probe == "pending"
    h.prepare_rig(wait=False)
    plan = h.plan_rig()
    assert (plan.mode, plan.probe, plan.via_probe) == ("delegate", "pass", True)
    assert plan.prefix == [str(rig.path)] and plan.warning is None
    (entry,) = entries(tmp_path)
    assert entry["outcome"] == "pass" and entry["material"]["rig_version_output"] == IN_RANGE
    evidence = json.loads((cache_dir(tmp_path) / entry["evidence"]).read_text("utf-8"))
    assert evidence["outcome"] == "pass" and evidence["exit_codes"]["proof_add"] == 0
    assert set(evidence["argv"]) == set(h.PROBE_STEPS)
    steps = [a[:2] for a in probe_argvs(rig)]
    assert steps.count(["scope", "--workspace"]) == 3 and ["proof", "--workspace"] in steps
    assert "warning" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutation", "named"),
    [
        ({"exit": {"proof_add": 3}}, "exit code: proof_add exited 3, expected 0"),
        ({"exit": {"scope_audit": 0}}, "exit code: scope_audit exited 0, expected 1"),
        ({"extra_write": True}, "write set: "),
        ({"rewrite": True}, "rendered bytes: rig rewrote missions/release-9.9/slices/"),
        ({"header": {"verdict": "CLEAR"}}, "c1 header: verdict is 'CLEAR', expected 'PASS'"),
        ({"drop_flags": {"help_proof_add": ["--name"]}}, "help: help_proof_add lacks --name"),
        ({"drop_keys": {"slice_ls": ["slices[].state"]}}, "json: slice_ls lacks slices[].state"),
        ({"crash": "slice_ls"}, "exit code: slice_ls exited -9, expected 0"),
    ],
    ids=["exit", "exit-audit", "extra-file", "rendered-byte", "header", "help-flag", "json-key",
         "rig-killed"],
)  # fmt: skip
def test_fail_is_native_naming_the_check(probe, tmp_path: Path, mutation, named) -> None:
    """Each differing check is a FAIL entry; the decision is native and names the check."""
    rig, _ = probe
    rig.mutate(**mutation)
    h.prepare_rig(wait=False)
    plan = h.plan_rig()
    assert (plan.mode, plan.probe, plan.prefix) == ("native", "fail", None)
    assert named in plan.warning, plan.warning
    assert "inside the probed range" in plan.warning and ".evidence.json" in plan.warning
    (entry,) = entries(tmp_path)
    assert entry["outcome"] == "fail" and named in entry["detail"]


def test_timeout(probe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe past its total is a timeout entry (24 h) and native."""
    rig, _ = probe
    monkeypatch.setattr(h, "PROBE_TIMEOUT", 1.5)
    rig.mutate(sleep=1.0)
    began = time.monotonic()
    h.prepare_rig(wait=False)
    assert time.monotonic() - began < 10
    plan = h.plan_rig()
    assert (plan.mode, plan.probe) == ("native", "timeout")
    assert "probe timeout" in plan.warning
    assert entries(tmp_path)[0]["outcome"] == "timeout"


def test_hacf_side_crash_is_an_error_not_an_exception(probe, monkeypatch, tmp_path) -> None:
    """A crash inside the probe (here: rendering) is an error entry; nothing escapes."""

    def boom(ws: Path) -> None:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(h, "_probe_tree", boom)
    h.prepare_rig(wait=False)
    plan = h.plan_rig()
    assert (plan.mode, plan.probe) == ("native", "error")
    assert "renderer exploded" in plan.warning


def test_unexpected_failure_in_prepare_never_raises(probe, monkeypatch, capsys) -> None:
    """Any exception in prepare_rig (here: the key material) is one warning and native."""

    def boom(*args, **kwargs):
        raise OSError(5, "disk on fire")

    monkeypatch.setattr(h, "probe_key_material", boom)
    h.prepare_rig(wait=True)  # returns
    assert "range probe failed" in capsys.readouterr().err
    plan = h.plan_rig()  # the cache read fails the same way: native, never a crash
    assert (plan.mode, plan.probe) == ("native", "error")


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong-keys"])
def test_bad_expectation_file_is_an_error(probe, tmp_path: Path, damage: str) -> None:
    """A missing, non-JSON or wrongly shaped expectation file is an error outcome and native."""
    rig, expect = probe
    if damage == "missing":
        expect.unlink()
    elif damage == "corrupt":
        expect.write_text("{not json", encoding="utf-8")
    else:
        expect.write_text(json.dumps({"rig_version": "0.7.22"}), encoding="utf-8")
    h.prepare_rig(wait=False)
    plan = h.plan_rig()
    assert (plan.mode, plan.probe) == ("native", "error")
    assert probe_argvs(rig) == [], "no rig call runs without valid expectations"


def test_version_decision_rule() -> None:
    """Only "pass" delegates an in-range version; listed, other-minor and stop are unchanged."""
    contract = h.load_contract()
    assert h.version_decision("0.7.22", contract, "pass") == ("delegate", None)
    for result in (None, "pending", "fail", "timeout", "error", "off", "unwritable"):
        mode, warning = h.version_decision("0.7.22", contract, result, probe_note="x")
        assert mode == "native" and "inside the probed range" in warning
    assert "probe pending" in h.version_decision("0.7.22", contract)[1]
    assert "probe off" in h.version_decision("0.7.22", contract, "off")[1]
    assert h.version_decision("0.7.21", contract, "fail") == ("delegate", None)
    assert h.version_decision("0.7.15", contract, "pass")[0] == "native"
    assert h.version_decision("0.6.14", contract, "pass")[0] == "native"
    assert h.version_decision("0.8.0", contract, "pass")[0] == "stop"


# --- placement and the lock -----------------------------------------------------------------------


def test_plan_rig_never_probes(probe) -> None:
    """plan_rig() reads the cache only: a miss runs `--version` and nothing else."""
    rig, _ = probe
    for _ in range(3):
        assert h.plan_rig().probe == "pending"
    assert rig.argvs() == [["--version"]] * 3


def hold_lock(tmp_path: Path) -> int:
    """Take the probe lock the way another process would; return its descriptor."""
    cache = cache_dir(tmp_path)
    cache.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_held_lock_proof_is_native_at_once(probe, tmp_path: Path) -> None:
    """prepare_rig(wait=False) with the probe lock held returns at once without probing."""
    rig, _ = probe
    fd = hold_lock(tmp_path)
    try:
        began = time.monotonic()
        h.prepare_rig(wait=False)
        assert time.monotonic() - began < 2
        assert probe_argvs(rig) == []
        assert h.plan_rig().mode == "native"
    finally:
        os.close(fd)


def test_held_lock_verify_waits_then_reports_pending(probe, tmp_path, monkeypatch) -> None:
    """prepare_rig(wait=True) waits up to PROBE_TIMEOUT + 5 s, then leaves `probe pending`."""
    rig, _ = probe
    monkeypatch.setattr(h, "PROBE_TIMEOUT", -3.5)  # a 1.5 s wait
    fd = hold_lock(tmp_path)
    try:
        began = time.monotonic()
        h.prepare_rig(wait=True)
        waited = time.monotonic() - began
        assert 1.3 <= waited < 5, waited
        assert probe_argvs(rig) == []
        assert h.plan_rig().probe == "pending"
    finally:
        os.close(fd)


def test_held_lock_verify_reads_the_other_probes_result(probe, tmp_path) -> None:
    """A verify waiting on another process's probe reads the PASS it wrote."""
    rig, _ = probe
    fd = hold_lock(tmp_path)

    def other() -> None:  # the other process: probe, write, then release the lock
        time.sleep(0.5)
        material = h.probe_key_material(str(rig.path), IN_RANGE, h.load_contract())
        result = h.run_probe([str(rig.path)], IN_RANGE)
        h.write_probe_entry(cache_dir(tmp_path), h.probe_key(material), material, result)
        os.close(fd)

    thread = threading.Thread(target=other)
    thread.start()
    h.prepare_rig(wait=True)
    thread.join()
    assert h.plan_rig().mode == "delegate"


def test_unwritable_cache_never_probes(probe, tmp_path: Path) -> None:
    """An unwritable cache dir: native, no rig subprocess beyond `--version`."""
    rig, _ = probe
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    xdg.chmod(0o500)
    try:
        h.prepare_rig(wait=True)
        assert rig.argvs() == [["--version"]]
        plan = h.plan_rig()
        assert (plan.mode, plan.probe) == ("native", "unwritable")
        assert "not writable" in plan.warning
    finally:
        xdg.chmod(0o700)


# --- the cache ------------------------------------------------------------------------------------


def test_same_key_probes_once_and_changed_key_reprobes(probe, tmp_path: Path) -> None:
    """Two prepare_rig() calls with one key make one probe; a changed rig binary re-probes."""
    rig, _ = probe

    def probes() -> int:
        return sum(1 for a in rig.argvs() if a[:2] == ["proof", "--workspace"])

    h.prepare_rig(wait=False)
    h.prepare_rig(wait=True)
    assert probes() == 1
    rig.path.write_text(rig.path.read_text(encoding="utf-8") + "\n# changed\n", "utf-8")
    h.prepare_rig(wait=False)
    assert probes() == 2 and len(entries(tmp_path)) == 2


@pytest.mark.parametrize("damage", ["corrupt", "partial", "wrong-key", "wrong-material"])
def test_damaged_entry_is_a_miss(probe, tmp_path: Path, damage: str) -> None:
    """A corrupt, partial or mismatched entry is a miss: pending, and the next probe replaces it."""
    rig, _ = probe
    h.prepare_rig(wait=False)
    (path,) = [p for p in cache_dir(tmp_path).glob("*.json") if ".evidence" not in p.name]
    data = json.loads(path.read_text(encoding="utf-8"))
    if damage == "corrupt":
        path.write_text("{]", encoding="utf-8")
    elif damage == "partial":
        path.write_text(path.read_text(encoding="utf-8")[:40], encoding="utf-8")
    elif damage == "wrong-key":
        data["key"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data["material"]["rig_size"] += 1
        path.write_text(json.dumps(data), encoding="utf-8")
    assert h.plan_rig().probe == "pending"
    h.prepare_rig(wait=False)
    assert h.plan_rig().probe == "pass"


NON_FINITE_AT = ["NaN", "Infinity", "-Infinity", "1e999", "1" + "0" * 400]


def _set_at(path: Path, literal: str) -> None:
    """Rewrite a cache entry's `at` as a raw JSON literal (json.dumps cannot spell 1e999)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["at"] = "@AT@"
    path.write_text(json.dumps(data).replace('"@AT@"', literal), encoding="utf-8")


@pytest.mark.parametrize("literal", NON_FINITE_AT, ids=["nan", "inf", "-inf", "1e999", "huge-int"])
def test_non_finite_time_is_a_miss(probe, tmp_path: Path, literal: str) -> None:
    """A PASS entry whose `at` is NaN, +-Infinity, 1e999 or a huge int is a miss, never delegate."""
    rig, _ = probe
    h.prepare_rig(wait=False)
    assert h.plan_rig().mode == "delegate"
    (path,) = [p for p in cache_dir(tmp_path).glob("*.json") if ".evidence" not in p.name]
    _set_at(path, literal)
    material = json.loads(path.read_text(encoding="utf-8"))["material"]
    assert h.read_probe_entry(cache_dir(tmp_path), path.stem, material) is None
    plan = h.plan_rig()
    assert (plan.mode, plan.probe) == ("native", "pending"), plan


def test_key_that_changes_during_the_probe_is_discarded(probe, monkeypatch, tmp_path) -> None:
    """When the key material differs after the probe, nothing is cached and the call is native."""
    rig, _ = probe
    real = h.run_probe

    def probe_then_touch(*args, **kwargs):
        result = real(*args, **kwargs)
        rig.path.write_text(rig.path.read_text(encoding="utf-8") + "\n", "utf-8")
        return result

    monkeypatch.setattr(h, "run_probe", probe_then_touch)
    h.prepare_rig(wait=False)
    assert entries(tmp_path) == []
    assert h.plan_rig().mode == "native"


def test_ttls(probe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAIL older than 24 h and a PASS older than 7 days re-probe (a fake clock)."""
    rig, _ = probe
    now = [1_000_000.0]
    monkeypatch.setattr(h, "_clock", lambda: now[0])
    rig.mutate(exit={"proof_add": 3})
    h.prepare_rig(wait=False)
    assert h.plan_rig().probe == "fail"
    rig.mutate()
    now[0] += 23 * 3600
    h.prepare_rig(wait=False)
    assert h.plan_rig().probe == "fail", "a FAIL younger than 24 h is kept"
    now[0] += 2 * 3600
    h.prepare_rig(wait=False)
    assert h.plan_rig().probe == "pass", "a FAIL older than 24 h re-probes"
    now[0] += 6 * 24 * 3600
    assert h.plan_rig().probe == "pass", "a PASS younger than 7 days is kept"
    now[0] += 2 * 24 * 3600
    assert h.plan_rig().probe == "pending", "a PASS older than 7 days is a miss"
    now[0] = 0.0
    assert h.plan_rig().probe == "pending", "an entry from the future is a miss"


# --- the opt-out ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "state"),
    [
        (None, "allowed"),
        ("ides: [claude]\n", "allowed"),
        ("openrig:\n  id_prefix: OPR\n", "allowed"),
        ("openrig:\n  range_probe: true\n", "allowed"),
        ("openrig:\n  range_probe: false\n", "disabled"),
        ("openrig:\n  range_probe: \"no\"\n", "off"),
        ("openrig:\n  range_probe: 0\n", "off"),
        ("openrig: [\n", "off"),
        ("openrig: 3\n", "off"),
        ("x" * (h.CONTRACT_MAX_BYTES + 1), "off"),
    ],
    ids=["missing", "no-key", "other-key", "true", "false", "string-no", "zero", "malformed",
         "not-mapping", "oversize"],
)  # fmt: skip
def test_opt_out_setting(tmp_path: Path, monkeypatch, text, state) -> None:
    """Only the boolean false disables; unreadable, malformed or non-boolean is off."""
    monkeypatch.setattr(h, "_warned", set())
    if text is not None:
        (tmp_path / ".agents/local").mkdir(parents=True)
        (tmp_path / ".agents/local/hacf.yml").write_text(text, encoding="utf-8")
    assert h.range_probe_setting(tmp_path)[0] == state


@pytest.mark.parametrize("text", ["openrig:\n  range_probe: false\n", "openrig: [\n",
                                  "openrig:\n  range_probe: \"no\"\n"])  # fmt: skip
def test_opt_out_never_probes(probe, tmp_path: Path, monkeypatch, capsys, text: str) -> None:
    """false, a malformed hacf.yml and a string value: no probe, native."""
    rig, _ = probe
    top = tmp_path / "proj"
    (top / ".agents/local").mkdir(parents=True)
    (top / ".agents/local/hacf.yml").write_text(text, encoding="utf-8")
    h.prepare_rig(wait=True, top=top)
    assert rig.argvs() == [["--version"]]
    plan = h.plan_rig(top=top)
    assert plan.mode == "native"
    err = capsys.readouterr().err
    if "false" in text:
        assert plan.probe is None and "inside the probed range" not in plan.warning
        assert err == ""
    else:
        assert plan.probe == "off" and "probe off" in plan.warning
        assert err.count("range probe is off (fail closed)") == 1


def test_adapter_does_not_import_engine_or_scope() -> None:
    """The opt-out is read without a new graph edge (no hyper_engine or hyper_scope import)."""
    text = (SCRIPTS_DIR / "hyper_openrig.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+hyper_(engine|scope)\b", text, re.M)


# --- R10 ------------------------------------------------------------------------------------------


def test_delegate_error_invalidates_the_entry(probe, tmp_path: Path, capsys) -> None:
    """Under a probe-earned delegate, a failing proof add drops the PASS entry with one warning."""
    rig, _ = probe
    h.prepare_rig(wait=False)
    plan = h.plan_rig()
    assert plan.via_probe
    rig.mutate(exit={"proof_add": 2})
    slice_dir = tmp_path / "ws" / "s"
    slice_dir.mkdir(parents=True)
    body = tmp_path / "body.md"
    body.write_text("b\n", encoding="utf-8")
    with pytest.raises(h.Refused, match="rig proof add failed"):
        h.write_proof_artifact(
            slice_dir, workspace=tmp_path / "ws", plan=plan, artifact_type="qa", verdict="PASS",
            candidate_sha="abcdef0", money_evidence="m", body_file=body, name="01-qa.md",
        )  # fmt: skip
    assert entries(tmp_path) == []
    assert capsys.readouterr().err.count("probe-earned delegate") == 1
    assert h.plan_rig().mode == "native"


def test_probe_output_ok(probe) -> None:
    """Exit 0 and 1 with the key paths are fine; exit 2, no JSON or a missing path are not."""
    ok = h.Completed([], 1, json.dumps({"ok": 1, "findings": [{"code": 1, "path": 2}],
                                        "summary": {"count": 1}, "extra": 1}), "")  # fmt: skip
    assert h.probe_output_ok("scope_audit", ok)
    assert not h.probe_output_ok("scope_audit", ok._replace(returncode=2))
    assert not h.probe_output_ok("scope_audit", ok._replace(stdout="no json"))
    assert not h.probe_output_ok("scope_audit", ok._replace(stdout=json.dumps({"ok": 1})))


# --- the probe environment ------------------------------------------------------------------------


def test_probe_env(probe, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every probe call runs without OPENRIG_*/RIGGED_*, with the dead URL and homes under tmp."""
    rig, _ = probe
    monkeypatch.setenv("OPENRIG_URL", "http://127.0.0.1:7777")
    monkeypatch.setenv("OPENRIG_SESSION_NAME", "someone@rig")
    monkeypatch.setenv("RIGGED_SEAT", "x")
    monkeypatch.setenv("GIT_DIR", "/nonexistent/.git")
    result = h.run_probe([str(rig.path)], IN_RANGE)
    assert result.outcome == "pass", result.detail
    calls = rig.calls()
    assert len(calls) == len(h.PROBE_STEPS)
    tmp_root = os.path.realpath(os.environ["TMPDIR"])
    for call in calls:
        env = call["env"]
        assert not [k for k in env if k.startswith(("OPENRIG_", "RIGGED_")) and k not in
                    ("OPENRIG_URL", "OPENRIG_HOME")]  # fmt: skip
        assert env["OPENRIG_URL"] == "http://127.0.0.1:9"
        for key in ("OPENRIG_HOME", "NODE_COMPILE_CACHE"):
            assert os.path.realpath(env[key]).startswith(tmp_root + os.sep), (key, env[key])
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "GIT_DIR" not in env and env["HOME"] == os.environ["HOME"]
        assert env["PATH"] == os.environ["PATH"]
    assert os.listdir(tmp_root) == [], "the probe removed its temp dir"


def test_rig_slice_writers_are_never_probed(probe) -> None:
    """The probe calls only --version, scope audit, slice ls, proof add and --help."""
    rig, _ = probe
    h.run_probe([str(rig.path)], IN_RANGE)
    for argv in rig.argvs():
        assert not {"create", "progress", "close"} & set(argv), argv


# --- callers: hyper_spec proof and hyper_migrate verify -------------------------------------------


def _project(tmp_path: Path) -> Path:
    """Return a git repo holding a copy of HACF's scripts, config and templates."""
    root = tmp_path / "proj"
    for sub in ("scripts", "config", "schemas"):
        shutil.copytree(
            REPO_ROOT / ".agents" / sub,
            root / ".agents" / sub,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return root


def _env(rig: FakeRig, tmp_path: Path, **extra: str) -> dict[str, str]:
    return clean_env(
        PATH=f"{rig.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
        XDG_CACHE_HOME=str(tmp_path / "xdg"),
        TMPDIR=str(tmp_path / "tmp"),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="T",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="T",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        **extra,
    )


SLICE_BODY = """\
# MiniPRD: probe caller

## Intent

The caller slice.

## 1. The Confidence Mandate

Confidence 9/10.

## Mini-requirements

1. US-001: it works.

## 3. Implementation Plan (Task List)

- [ ] Task 1: write it.

## 4. The Negative Space (Constraints)

- **DO NOT** touch other modules.

## Proof contract

- [ ] it passes.
"""


@pytest.fixture
def project(probe, tmp_path: Path):
    """Return (root, env, fake rig, run): a project using the fake's expectations."""
    rig, expect = probe
    root = _project(tmp_path)
    (root / ".agents/config/openrig_probe_expect.json").write_bytes(expect.read_bytes())
    env = _env(rig, tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], env=env, check=True)
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, str(root / ".agents/scripts" / a[0]), *a[1:]], cwd=root, env=env,
        capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
    for args in (
        ("hyper_scope.py", "project", "init"),
        ("hyper_scope.py", "mission", "create", "--name", "release-9.9", "--intent", "Ship."),
    ):
        proc = run(*args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    body = tmp_path / "slice-body.md"
    body.write_text(SLICE_BODY, encoding="utf-8")
    proc = run("hyper_scope.py", "slice", "create", "--mission", "missions/release-9.9", "--name",
               "01-caller", "--intent", "The caller slice.", "--body-file", str(body))  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for argv in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(argv, cwd=root, env=env, check=True, capture_output=True)
    rig.log.unlink(missing_ok=True)
    return root, env, rig, run


def test_proof_probes_before_spec_lock(project, tmp_path: Path) -> None:
    """`hyper_spec.py proof` probes first: spec.lock is absent or free while the probe runs.

    The fake rig records on every call whether spec.lock is held (a non-blocking flock on it
    fails); the probe's calls see it absent or free, the delegated `proof add` sees it held, and
    rig answers `--version` once per command outside the probe (plan_rig reuses prepare_rig's).
    """
    root, env, rig, run = project
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=root, env=env, capture_output=True,
        text=True, check=True,
    ).stdout.strip()  # fmt: skip
    lock = (root / common / "hacf" / "locks" / "spec.lock").resolve()
    lock.unlink(missing_ok=True)  # the fixture's hyper_scope calls created it
    seen = tmp_path / "lock-seen.txt"
    check = (
        "import fcntl\n"
        f"_lock = Path({str(lock)!r})\n"
        "if not _lock.exists():\n    _state = 'absent'\n"
        "else:\n    _fd = os.open(_lock, os.O_RDWR)\n"
        "    try:\n        fcntl.flock(_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "        _state = 'free'\n"
        "    except BlockingIOError:\n        _state = 'held'\n"
        "    os.close(_fd)\n"
        f"open({str(seen)!r}, 'a').write(' '.join(args[:2]) + ' ' + _state + '\\n')\n"
    )
    script = rig.path.read_text(encoding="utf-8").replace(
        "mut = cfg.get(", check + "mut = cfg.get(", 1
    )
    rig.path.write_text(script, encoding="utf-8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, env=env, capture_output=True,
                          text=True, check=True).stdout.strip()  # fmt: skip
    body = tmp_path / "proof-body.md"
    body.write_text("The probe test ran.\n", encoding="utf-8")
    proc = run("hyper_spec.py", "proof", "--slice", "missions/release-9.9/slices/01-caller",
               "--artifact-type", "qa", "--verdict", "PASS", "--candidate-sha", head,
               "--money-evidence", "fine", "--file", str(body), "--name", "01-qa.md")  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = seen.read_text(encoding="utf-8").splitlines()
    # prepare_rig's --version, then the probe's eight calls: spec.lock absent or free.
    assert len(lines) == 1 + len(h.PROBE_STEPS) + 1, lines
    assert all(line.endswith(("absent", "free")) for line in lines[:-1]), lines
    # plan_rig() reused prepare_rig's --version; the delegated proof add ran under spec_lock.
    assert lines[-1] == "proof --workspace held", lines
    argv = rig.argvs()[-1]
    assert argv[:4] == ["proof", "--workspace", str(root.resolve()), "add"]
    assert (root / "missions/release-9.9/slices/01-caller/proof/01-qa.md").is_file()


def test_verify_probe_pending_line(project, tmp_path: Path) -> None:
    """Verify with the probe lock held by another process prints `skipped: rig probe pending`."""
    root, env, rig, run = project
    run_env = dict(env)
    fd = hold_lock(tmp_path)
    try:
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import hyper_openrig as h; "
             "h.PROBE_TIMEOUT = -4; import hyper_migrate as m; sys.exit(m.main(['verify']))",
             str(root / ".agents/scripts")],
            cwd=root, env=run_env, capture_output=True, text=True, timeout=120, check=False,
        )  # fmt: skip
    finally:
        os.close(fd)
    assert f"skipped: rig probe pending ({IN_RANGE})" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped: rig is native" not in proc.stdout
    assert [a for a in rig.argvs() if a != ["--version"]] == []


def test_verify_delegates_after_a_pass_and_invalidates_on_error(project, tmp_path: Path) -> None:
    """Verify probes on a miss, delegates scope audit; a delegate-mode exit 2 drops the entry."""
    root, env, rig, run = project
    proc = run("hyper_migrate.py", "verify")
    audits = [a for a in rig.argvs() if a[:1] == ["scope"] and str(root.resolve()) in a]
    assert audits and audits[0][-1] == "--json", rig.argvs()
    assert "skipped: rig" not in proc.stdout, proc.stdout
    assert len(entries(tmp_path)) == 1
    # An exit 1 (findings) keeps the entry; an exit 2 drops it.
    rig.mutate(exit={"scope_audit": 1})
    run("hyper_migrate.py", "verify")
    assert len(entries(tmp_path)) == 1
    rig.mutate(exit={"scope_audit": 2})
    proc = run("hyper_migrate.py", "verify")
    assert "probe-earned delegate" in proc.stderr, proc.stderr
    assert entries(tmp_path) == []


# --- the CLI --------------------------------------------------------------------------------------


def test_contract_show_and_probe(project, tmp_path: Path) -> None:
    """`contract show` reports the opt-out, rig, decision and entries; `contract probe` exits."""
    root, env, rig, run = project
    show = run("hyper_openrig.py", "contract", "show", "--json")
    assert show.returncode == 0, show.stderr
    data = json.loads(show.stdout)
    assert data["opt_out"]["state"] == "allowed" and data["probes"] == []
    assert data["rig"] == {"path": str(rig.path.resolve()), "version": IN_RANGE}
    assert data["decision"]["mode"] == "native" and data["decision"]["probe"] == "pending"
    assert [a for a in rig.argvs() if a != ["--version"]] == [], "show never probes"

    probe = run("hyper_openrig.py", "contract", "probe")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"rig {IN_RANGE}: pass (decision delegate)" in probe.stdout
    text = run("hyper_openrig.py", "contract", "show")
    assert "decision: delegate (probe pass)" in text.stdout
    assert f"  {IN_RANGE}  pass  age 0.0 h  evidence: " in text.stdout
    data = json.loads(run("hyper_openrig.py", "contract", "show", "--json").stdout)
    (entry,) = data["probes"]
    assert entry["version"] == IN_RANGE and entry["outcome"] == "pass"
    assert Path(entry["evidence"]).is_file()

    for literal in NON_FINITE_AT:
        (path,) = [p for p in cache_dir(tmp_path).glob("*.json") if ".evidence" not in p.name]
        good = path.read_text(encoding="utf-8")
        _set_at(path, literal)
        text = run("hyper_openrig.py", "contract", "show")
        assert text.returncode == 0, literal + text.stderr
        bad = "ValueError: at is not a finite number"
        assert f"  bad entry {path}: {bad}" in text.stdout, text.stdout
        shown = run("hyper_openrig.py", "contract", "show", "--json")
        assert shown.returncode == 0, literal + shown.stderr
        assert json.loads(shown.stdout)["probes"] == [{"path": str(path), "bad": bad}]
        assert "decision: native (probe pending)" in text.stdout
        path.write_text(good, encoding="utf-8")
    _set_at(path, "Infinity")
    reran = run("hyper_openrig.py", "contract", "probe")
    assert reran.returncode == 0 and "pass (decision delegate)" in reran.stdout, reran.stderr
    rig.mutate(exit={"proof_add": 3})
    again = run("hyper_openrig.py", "contract", "probe", "--json")
    assert again.returncode == 0 and json.loads(again.stdout)["outcome"] == "pass", "cached"
    reprobe = run("hyper_openrig.py", "contract", "probe", "--reprobe", "--json")
    assert reprobe.returncode == 1 and json.loads(reprobe.stdout)["outcome"] == "fail"

    rig.config["version"] = "0.7.21 (255fa708)"
    rig.save()
    listed = run("hyper_openrig.py", "contract", "probe")
    assert listed.returncode == 2 and "nothing to probe" in listed.stderr
    absent = subprocess.run(
        [sys.executable, str(root / ".agents/scripts/hyper_openrig.py"), "contract", "probe"],
        cwd=root, env={**env, "PATH": "/usr/bin:/bin"} if not Path("/usr/bin/rig").exists()
        else env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert absent.returncode == 2


# --- F11: the suite never writes the user's cache -------------------------------------------------


def test_suite_cache_is_isolated() -> None:
    """XDG_CACHE_HOME is this session's temp dir, and ~/.cache/hacf/rig-probe is untouched."""
    assert os.environ["XDG_CACHE_HOME"] == SESSION_CACHE_HOME
    assert Path(SESSION_CACHE_HOME).is_dir()
    assert str(h.probe_cache_dir()).startswith(SESSION_CACHE_HOME + os.sep)
    assert REAL_PROBE_CACHE != h.probe_cache_dir()
    assert _probe_cache_state(REAL_PROBE_CACHE) == REAL_PROBE_CACHE_STATE
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['XDG_CACHE_HOME'])"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert child.stdout.strip() == SESSION_CACHE_HOME
