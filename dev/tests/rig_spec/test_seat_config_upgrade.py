"""openrig/hacf/bin/seat-config-upgrade.py: plan, apply and restore on stand-in live specs.

Mission v064-upgrade-path OPR.99.0.10.9. Offline: a temp HACF source checkout (a copy of
openrig/hacf, with an older commit tagged v0.0.1 whose scribe guidance is the fixture's old text), a
fake `rig` on PATH (ps, seat status, skill loadout, spec validate; argv logged), HOME under
tmp_path, and the live specs copied from dev/tests/fixtures/seat_config/ into a stand-in spec
library under tmp_path.

Every run sets HACF_SEAT_CONFIG_SANDBOX=1 (autouse fixture below; lead's brief, orch-lead 19:2xZ),
so the script refuses any path under the real OpenRig home before it reads anything.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HACF = REPO_ROOT / "openrig" / "hacf"
FIXTURES = REPO_ROOT / "dev" / "tests" / "fixtures" / "seat_config"
RIG = "demo-hacf"  # a stand-in rig name
SANDBOX = "HACF_SEAT_CONFIG_SANDBOX"
SCRIBE = "agents/hacf/guidance/scribe.md"
ALWAYS = (
    "roles.yml",
    "caps.yml",
    "seat-config.yml",
    "OPS.md",
    "bin/context-guard.sh",
    "bin/seat-rules.py",
    "bin/brief.sh",
    "bin/view-layout.sh",
    "bin/herdr-view.py",
    "bin/seat-config-upgrade.py",
    "bin/seat-config.py",
    "bin/progress-report.py",
    "bin/hacf-host.py",
    "host.example.yml",
)
LEAD, EXEC = f"orch-lead@{RIG}", f"build-execute@{RIG}"
RECORDED = FIXTURES / "rig-ps-full-0.7.21.json"  # a real 0.7.21 record, normalised (R79)
NO_PROFILE = FIXTURES / "rig-ps-full-no-profile.json"

FAKE_RIG = r"""#!/usr/bin/env python3
import json, os, sys, time
d = os.environ["FAKE_RIG_DIR"]
argv = sys.argv[1:]
with open(os.path.join(d, "argv.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv[:1] == ["ps"]:
    nodes = json.load(open(os.path.join(d, "ps.json")))
    if "--full" not in argv:  # the real 0.7.21 CLI leaves these out without --full
        for n in nodes:
            for key in ("profile", "role", "model", "effort", "effortSource"):
                n.pop(key, None)
    print(json.dumps(nodes))
    sys.exit(0)
if argv[:2] == ["seat", "status"]:
    path = os.path.join(d, "status-%s.json" % argv[2])
    if not os.path.exists(path):
        sys.exit(1)
    print(open(path).read())
    sys.exit(0)
if argv[:2] == ["spec", "validate"]:
    code = os.path.join(d, "validate.exit")
    nap = os.path.join(d, "validate.sleep")
    if os.path.exists(nap):
        time.sleep(float(open(nap).read()))
    print("validate: stand-in output")
    sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
if argv[:2] == ["skill", "loadout"]:
    if len(argv) != 5 or argv[2] != "--seat" or argv[4] != "--json":
        sys.exit(98)  # only the read-only inspection; --apply is a write
    path = os.path.join(d, "loadout-%s.json" % argv[3])
    if not os.path.exists(path):
        sys.exit(1)
    print(open(path).read())
    sys.exit(0)
if argv[:2] in (["seat", "set-model"], ["seat", "set-effort"], ["seat", "set-runtime"]):
    if "--help" in argv:  # the host probe (MR 5); `help-<verb>.exit` makes it fail
        code = os.path.join(d, "help-%s.exit" % argv[1])
        if os.path.exists(os.path.join(d, "help-%s.parent" % argv[1])):
            print("Usage: rig seat [options] [command]")  # an unknown verb falls back to this
            sys.exit(0)
        print("Usage: rig seat %s [options] <seat>" % argv[1])
        sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
    sys.exit(98)  # a write verb must never reach the fake: the tests assert it did not
if argv == ["--version"]:
    print("0.7.21 (fake)")
    sys.exit(0)
sys.exit(97)
"""


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lead's brief (orch-lead 19:2xZ): every run refuses paths under the real OpenRig home."""
    monkeypatch.setenv(SANDBOX, "1")


def _git(cwd: Path, *args: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid")
    env.update(GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


def _pin_opus(path: Path) -> None:
    """Test-local pin (lead ruling P40): these tests assert against all-Opus defaults.

    The shipped seat-config.yml may carry tiered values (OPR.99.0.11.8); only the copy changes.
    """
    out, in_seats = [], False
    for line in path.read_text().splitlines(keepends=True):
        if line.startswith("seats:"):
            in_seats = True
        elif in_seats and line.strip() and not line.startswith(" "):
            in_seats = False
        if in_seats:
            line = re.sub(r"^(  [\w-]+:\s+)\{[^}]*\}", r"\1{model: claude-opus-5-5}", line)
        out.append(line)
    path.write_text("".join(out))


class World:
    """A temp source checkout, a fake rig, a HOME and a spec library holding one live spec."""

    def __init__(self, tmp: Path, fixture: str) -> None:
        """Build the source (v0.0.1 tag, then HEAD), the fake rig and the live spec."""
        self.tmp = tmp
        src = tmp / "src"
        shutil.copytree(HACF, src / "openrig" / "hacf")
        _pin_opus(src / "openrig" / "hacf" / "seat-config.yml")
        _git(src, "init", "-q")
        real_scribe = (src / "openrig/hacf" / SCRIBE).read_bytes()
        real = {rel: (src / "openrig/hacf" / rel).read_bytes() for rel in ("roles.yml", "caps.yml")}
        shutil.copyfile(FIXTURES / "old-scribe.md", src / "openrig/hacf" / SCRIBE)
        for rel in real:  # a fixture's old defaults files are HACF text of the old tag (R87)
            if (FIXTURES / fixture / rel).is_file():
                shutil.copyfile(FIXTURES / fixture / rel, src / "openrig/hacf" / rel)
        _git(src, "add", "-A")
        _git(src, "commit", "-qm", "old")
        _git(src, "tag", "v0.0.1")
        (src / "openrig/hacf" / SCRIBE).write_bytes(real_scribe)
        for rel, data in real.items():
            (src / "openrig/hacf" / rel).write_bytes(data)
        _git(src, "commit", "-qam", "head")
        self.src = src / "openrig" / "hacf"
        self.script = self.src / "bin" / "seat-config-upgrade.py"
        self.home = tmp / "home"
        self.home.mkdir()
        self.library = tmp / "library"
        self.live = self.library / RIG
        shutil.copytree(FIXTURES / fixture, self.live, symlinks=True)
        self.fake = tmp / "fake"
        self.fake.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "rig").write_text(FAKE_RIG)
        (bin_dir / "rig").chmod(0o755)
        recorded = {n["logicalId"]: n for n in json.loads(RECORDED.read_text())}
        other = dict(recorded["build.audit"], rigName="other-rig")
        other["canonicalSessionName"] = "build-audit@other-rig"
        nodes = [recorded["orch.lead"], recorded["build.execute"], other]
        (self.fake / "ps.json").write_text(json.dumps(nodes))
        (self.fake / f"status-{LEAD}.json").write_text(
            json.dumps({"seat_ref": LEAD, "permissions": {"selectionState": "inherit"}})
        )
        (self.fake / f"status-{EXEC}.json").write_text(
            json.dumps(
                {"seat_ref": EXEC, "permissions": {"desired": {"allow": [], "deny": ["Monitor"]}}}
            )
        )
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
            "FAKE_RIG_DIR": str(self.fake),
            "LC_ALL": "C",
        }

    def run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run the script from the temp source, always under the sandbox."""
        assert os.environ.get(SANDBOX) == "1"
        e = dict(self.env, **{SANDBOX: os.environ[SANDBOX]}, **(env or {}))
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            env=e,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def plan(self, *extra: str) -> subprocess.CompletedProcess:
        """Run plan on the live spec."""
        return self.run("plan", "--live-spec", str(self.live), "--rig", RIG, *extra)

    def apply(self, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run apply on the live spec."""
        return self.run("apply", "--live-spec", str(self.live), "--rig", RIG, *extra, env=env)

    def argv(self) -> list[list[str]]:
        """Every argv the fake rig received."""
        log = self.fake / "argv.log"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def backups(self) -> list[Path]:
        """The backup directories apply wrote."""
        root = self.home / ".hacf" / RIG / "backups"
        return sorted(root.iterdir()) if root.exists() else []


def tree(root: Path) -> dict[str, tuple[str, int]]:
    """Each file below root: (sha256, mode)."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            out[path.relative_to(root).as_posix()] = (
                hashlib.sha256(data).hexdigest(),
                path.stat().st_mode & 0o7777,
            )
    return out


@pytest.fixture
def eta(tmp_path: Path) -> World:
    """The eta-like live spec."""
    return World(tmp_path, "eta-like")


# --- (a) plan writes nothing -------------------------------------------------------------------


def test_a_plan_writes_nothing(eta: World) -> None:
    """(a) plan: exit 1 (changes), every change listed, the live tree and HOME byte-identical."""
    live, home = tree(eta.live), tree(eta.home)
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert tree(eta.live) == live and tree(eta.home) == home and eta.backups() == []
    for rel in ALWAYS:
        assert f"  add: {rel}" in r.stdout
    assert f"  replace (unmodified HACF text): {SCRIBE}" in r.stdout
    assert "  keep (locally edited; --take-hacf startup/protocol.md replaces it)" in r.stdout
    assert "+++ HEAD/startup/protocol.md" in r.stdout  # the unified diff against HEAD
    assert "  keep (only in the live spec): startup/local-notes.md" in r.stdout
    assert 'rig.yaml: add env block env.ENABLE_CLAUDEAI_MCP_SERVERS: "false"' in r.stdout
    assert (
        f"--rig {RIG} " in r.stdout and "the operator's step, under the standing policy" in r.stdout
    )
    assert f"rig seat set-permissions {EXEC} --rules <backup>/seats/{EXEC}.json" in r.stdout


# --- (b) apply on eta-like ---------------------------------------------------------------------


def test_b_apply_eta_like(eta: World) -> None:
    """(b) always-set added, tagged file replaced, edited one kept, rig.yaml changed at env only."""
    before_rig = (eta.live / "rig.yaml").read_text()
    protocol = (eta.live / "startup/protocol.md").read_bytes()
    r = eta.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    for rel in ALWAYS:
        assert (eta.live / rel).read_bytes() == (eta.src / rel).read_bytes(), rel
        if rel.startswith("bin/"):
            assert (eta.live / rel).stat().st_mode & 0o777 == 0o755, rel
    assert (eta.live / SCRIBE).read_bytes() == (eta.src / SCRIBE).read_bytes()
    assert (eta.live / "startup/protocol.md").read_bytes() == protocol
    assert (eta.live / "startup/local-notes.md").exists()
    after_rig = (eta.live / "rig.yaml").read_text()
    assert "cwd: /abs/stand-in/path" in after_rig
    added = [ln for ln in after_rig.splitlines() if ln not in before_rig.splitlines()]
    assert added == ["env:", '  ENABLE_CLAUDEAI_MCP_SERVERS: "false"']
    assert [ln for ln in before_rig.splitlines() if ln not in after_rig.splitlines()] == []
    old, new = yaml.safe_load(before_rig), yaml.safe_load(after_rig)
    assert new.pop("env") == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"} and new == old
    assert ["spec", "validate", str(eta.live / "rig.yaml")] in eta.argv()


def test_b_cleanroom_like_sets_the_key_beside_the_other(tmp_path: Path) -> None:
    """cleanroom-like: old HACF files replaced; the key added to the env block, OTHER_FLAG kept."""
    w = World(tmp_path, "cleanroom-like")
    r = w.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    data = yaml.safe_load((w.live / "rig.yaml").read_text())
    assert data["env"] == {"OTHER_FLAG": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    assert all(m["cwd"] == "../.." for p in data["pods"] for m in p["members"])
    assert (w.live / "roles.yml").read_bytes() == (w.src / "roles.yml").read_bytes()
    assert "  replace: bin/brief.sh" in r.stdout


# --- (c) restore is byte-identical ---------------------------------------------------------------


def test_c_restore_is_byte_identical(eta: World) -> None:
    """(c) apply, then restore: every file's sha256 and mode equal; per-seat rollback printed."""
    original = tree(eta.live)
    assert eta.apply().returncode == 0
    assert tree(eta.live) != original
    (backup,) = eta.backups()
    assert json.loads((backup / "seats" / f"{EXEC}.rollback.json").read_text()) == {
        "allow": [],
        "deny": ["Monitor"],
    }
    assert json.loads((backup / "seats" / f"{LEAD}.rollback.json").read_text()) == {
        "allow": [],
        "deny": [],
    }
    for name in ("SHA256SUMS", "plan.txt", f"seats/{LEAD}.before.json", "spec/rig.yaml"):
        assert (backup / name).exists(), name
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stderr
    assert tree(eta.live) == original
    assert not (eta.live / "bin").exists() and not (eta.live / "roles.yml").exists()
    assert "restored files only; seat records are unchanged: run these per seat:" in r.stdout
    for seat in (LEAD, EXEC):
        assert f"rig seat set-permissions {seat} --rules {backup}/seats/{seat}.rollback.json" in (
            r.stdout
        )
        assert f"rig seat launch {seat} --fresh --stop" in r.stdout


# --- (d) a failed validate auto-restores ---------------------------------------------------------


def test_d_failed_validate_auto_restores(eta: World) -> None:
    """(d) spec validate exits 1: the files are restored, the backup kept, exit 1."""
    original = tree(eta.live)
    (eta.fake / "validate.exit").write_text("1")
    r = eta.apply()
    assert r.returncode == 1
    assert tree(eta.live) == original
    (backup,) = eta.backups()
    assert f"auto-restored from {backup}" in r.stdout
    assert "validate: stand-in output" in r.stdout


# --- (e) a held lock ------------------------------------------------------------------------------


def test_e_held_lock_exits_1(eta: World) -> None:
    """(e) another apply holds the lock: exit 1, nothing written, no backup."""
    original = tree(eta.live)
    path = eta.home / ".hacf" / RIG / "seat-config.lock"
    path.parent.mkdir(parents=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = eta.apply()
    assert r.returncode == 1
    assert "another apply or restore is running" in r.stderr
    assert tree(eta.live) == original and eta.backups() == []


# --- (f) a backup under the spec library ----------------------------------------------------------


def test_f_backup_under_spec_library_refused(eta: World) -> None:
    """(f) HOME inside the spec library puts the backup there: refused, nothing written."""
    original = tree(eta.live)
    r = eta.apply(env={"HOME": str(eta.library)})
    assert r.returncode == 1
    assert "is under the spec library" in r.stderr
    assert tree(eta.live) == original
    assert not (eta.library / ".hacf").exists()


def test_f_backup_under_another_rigs_spec_refused(eta: World) -> None:
    """(f) HOME inside a directory holding another rig's rig.yaml: refused."""
    other = eta.tmp / "elsewhere" / "other-spec"
    other.mkdir(parents=True)
    (other / "rig.yaml").write_text("name: other\n")
    r = eta.apply(env={"HOME": str(other)})
    assert r.returncode == 1
    assert "which holds a rig.yaml" in r.stderr


# --- (g) --take-hacf ------------------------------------------------------------------------------


def test_g_take_hacf_replaces_a_kept_file(eta: World) -> None:
    """(g) --take-hacf startup/protocol.md replaces the locally edited file."""
    r = eta.apply("--take-hacf", "startup/protocol.md")
    assert r.returncode == 0, r.stdout + r.stderr
    path = "startup/protocol.md"
    assert (eta.live / path).read_bytes() == (eta.src / path).read_bytes()
    assert f"  replace (--take-hacf): {path}" in r.stdout


@pytest.mark.parametrize("rel", ["bin/brief.sh", "rig.yaml", "startup/nope.md", "../x"])
def test_g_take_hacf_outside_the_adapted_set_exits_2(eta: World, rel: str) -> None:
    """(g) a --take-hacf path outside the adapted and defaults sets is a usage error.

    roles.yml moved out of this list: it is a defaults file now (R87), so --take-hacf takes it;
    bin/brief.sh (always replaced, never kept) stands in as the refused always-set file.
    """
    original = tree(eta.live)
    assert eta.plan("--take-hacf", rel).returncode == 2
    assert tree(eta.live) == original


# --- (h) no claude_mcp, no set-permissions --------------------------------------------------------


@pytest.mark.parametrize("fixture", ["eta-like", "cleanroom-like", "odd"])
def test_h_no_claude_mcp_and_no_set_permissions(tmp_path: Path, fixture: str) -> None:
    """(h) no claude_mcp written anywhere; set-permissions never reaches the fake rig."""
    w = World(tmp_path, fixture)
    r = w.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "claude_mcp" not in (w.live / "rig.yaml").read_text()
    for path in w.live.rglob("*.yaml"):
        assert "claude_mcp:" not in path.read_text(), path
    assert w.argv() and not [a for a in w.argv() if "set-permissions" in a]
    assert {a[0] for a in w.argv()} <= {"ps", "seat", "spec"}
    # the host probes (`seat set-model|set-effort --help`, MR 5) are the only other seat calls
    seat_calls = [a for a in w.argv() if a[0] == "seat" and "--help" not in a]
    assert seat_calls == [["seat", "status", LEAD, "--json"], ["seat", "status", EXEC, "--json"]]
    assert {tuple(a) for a in w.argv() if "--help" in a} == {
        ("seat", "set-model", "--help"),
        ("seat", "set-effort", "--help"),
        ("seat", "set-runtime", "--help"),  # OPR.99.0.14.3 MR 3: the harness verb is probed too
    }


# --- the seat half: orch-lead last (audit iteration 1, MR 6 / OPS.md) ---------------------------

SEVEN = [  # rig ps lists them by name, so the lead is 4th
    ("build-audit", "audit"),
    ("build-execute", "execute"),
    ("desk-scribe", "scribe"),
    ("orch-lead", "lead"),
    ("spec-architect", "architect"),
    ("spec-red-team", "red-team"),
    ("spec-resolve", "resolve"),
]


def _seven(w: World) -> list[str]:
    """Give the fake rig the seven real seat names (stand-in rig); return them, lead last."""
    nodes = json.loads(RECORDED.read_text())
    assert {(n["canonicalSessionName"].split("@")[0], n["profile"]) for n in nodes} == set(SEVEN)
    (w.fake / "ps.json").write_text(json.dumps(nodes))
    for m, _ in SEVEN:
        status = {"seat_ref": f"{m}@{RIG}", "permissions": {"allow": [], "deny": []}}
        (w.fake / f"status-{m}@{RIG}.json").write_text(json.dumps(status))
    names = [f"{m}@{RIG}" for m, _ in SEVEN]
    return [n for n in names if n != LEAD] + [LEAD]


def _order(text: str, prefix: str) -> list[str]:
    """The seat named after prefix, per line that holds it, in output order."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            out.append(line[len(prefix) :].split()[0])
    return out


def test_seat_half_puts_the_lead_last(eta: World) -> None:
    """plan, apply and restore list orch-lead's lines after every other seat's."""
    want = _seven(eta)
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert _order(r.stdout, "rig seat set-permissions ") == want
    assert eta.apply().returncode == 0
    (backup,) = eta.backups()
    assert json.loads((backup / "seats" / "order.json").read_text()) == want
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stderr
    assert _order(r.stdout, "rig seat set-permissions ") == want
    assert _order(r.stdout, "rig seat launch ")[::2] == want


def test_restore_of_a_backup_without_order_puts_orch_lead_last(eta: World) -> None:
    """An older backup (no seats/order.json): restore still lists orch-lead@<rig> last."""
    want = _seven(eta)
    assert eta.apply().returncode == 0
    (backup,) = eta.backups()
    (backup / "seats" / "order.json").unlink()
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stderr
    assert _order(r.stdout, "rig seat set-permissions ") == want


# --- (j)-(o): lead rulings P29/P30 (R60) -------------------------------------------------------

FAULTS = r"""
import os, pathlib, subprocess  # pathlib first: 3.10 binds os functions at import
_mode = os.environ.get("FAKE_FAULT", "")
_live = os.environ.get("FAKE_FAULT_LIVE", "")
_state = {"writes": 0, "fired": False}
_replace, _unlink, _run = os.replace, pathlib.Path.unlink, subprocess.run


def _under(path):
    return bool(_live) and os.path.abspath(os.fspath(path)).startswith(_live + os.sep)


def replace(src, dst, *a, **k):
    if "write" in _mode and _under(dst):
        _state["writes"] += 1
        if _state["writes"] == 3:
            _state["fired"] = True
            raise OSError(5, "injected write error", os.fspath(dst))
    return _replace(src, dst, *a, **k)


def unlink(self, *a, **k):  # replaces pathlib.Path.unlink (same on 3.10 and 3.12+)
    if "restore" in _mode and _state["fired"] and _under(self) and not self.name.startswith("."):
        raise OSError(5, "injected restore error", os.fspath(self))
    return _unlink(self, *a, **k)


def run(args, *a, **k):
    if "timeout" in _mode and list(args[:3]) == ["rig", "spec", "validate"]:
        k["timeout"] = 1
    return _run(args, *a, **k)


if _mode:
    os.replace, pathlib.Path.unlink, subprocess.run = replace, unlink, run
"""


def _faults(w: World, mode: str) -> dict:
    """Env that loads a test-only sitecustomize injecting faults into the script's process.

    write: the 3rd os.replace into the live spec raises; restore: after that, Path.unlink of a
    live file raises; timeout: rig spec validate gets a 1 s timeout (the shipped one is 60 s).
    """
    site = w.tmp / "faults"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(FAULTS)
    return {"PYTHONPATH": str(site), "FAKE_FAULT": mode, "FAKE_FAULT_LIVE": str(w.live.resolve())}


def _failed_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("apply failed (")]


@pytest.mark.parametrize("dirty", ["modified", "untracked"])
def test_j_dirty_source_refuses(eta: World, dirty: str) -> None:
    """(j) P29: a change under a copied source path: plan and apply exit 1, nothing written."""
    original = tree(eta.live)
    if dirty == "modified":
        rel = "openrig/hacf/roles.yml"
        with open(eta.src.parents[1] / rel, "a") as handle:
            handle.write("# local edit\n")
    else:
        rel = "openrig/hacf/startup/new-note.md"
        (eta.src.parents[1] / rel).write_text("untracked\n")
    for r in (eta.plan(), eta.apply()):
        assert r.returncode == 1
        assert f"source checkout has uncommitted changes under: {rel}; commit or clean them" in (
            r.stderr
        )
    assert tree(eta.live) == original
    assert not (eta.home / ".hacf").exists()  # no backup and no lock file
    assert eta.argv() == []


def test_j_dirty_file_outside_the_copied_paths_does_not_refuse(eta: World) -> None:
    """(j) P29: an untracked file outside the copied paths: plan proceeds and names HEAD."""
    (eta.src.parents[1] / "scratch.txt").write_text("not copied\n")
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert "uncommitted" not in r.stderr
    assert "; source HEAD " in r.stdout.splitlines()[0]


def test_j_non_git_source_is_labelled_and_round_trips(eta: World) -> None:
    """P29: a source outside git says so, never claims a HEAD; apply/restore still work."""
    plain = eta.tmp / "plain"
    shutil.copytree(eta.src, plain / "openrig" / "hacf")
    script = plain / "openrig" / "hacf" / "bin" / "seat-config-upgrade.py"
    original = tree(eta.live)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), *args],
            env=dict(eta.env, **{SANDBOX: "1"}),
            capture_output=True, text=True, timeout=120,
        )  # fmt: skip

    r = run("plan", "--live-spec", str(eta.live), "--rig", RIG)
    assert r.returncode == 1, r.stderr
    head = r.stdout.splitlines()[0]
    assert head.endswith("; source: not a git checkout (files as found)") and "HEAD" not in head
    assert run("apply", "--live-spec", str(eta.live), "--rig", RIG).returncode == 0
    (backup,) = eta.backups()
    assert run("restore", str(backup)).returncode == 0
    assert tree(eta.live) == original


def test_k_write_error_auto_restores(eta: World) -> None:
    """(k) P30: an OSError on the third live write: byte-identical, exit 1, one line, no marker."""
    original = tree(eta.live)
    r = eta.apply(env=_faults(eta, "write"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert tree(eta.live) == original
    (backup,) = eta.backups()
    (line,) = _failed_lines(r.stdout)
    assert "injected write error" in line and line.endswith(f"; auto-restored from {backup}")
    assert (backup / "SHA256SUMS").is_file() and not (backup / "APPLY_IN_PROGRESS").exists()


def test_l_validate_timeout_auto_restores(eta: World) -> None:
    """(l) P30: validate hangs past the timeout: auto-restored, exit 1, no marker."""
    original = tree(eta.live)
    (eta.fake / "validate.sleep").write_text("30")
    r = eta.apply(env=_faults(eta, "timeout"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert tree(eta.live) == original
    (backup,) = eta.backups()
    (line,) = _failed_lines(r.stdout)
    assert (
        line
        == f"apply failed (rig spec validate timed out after 60 s); auto-restored from {backup}"
    )
    assert not (backup / "APPLY_IN_PROGRESS").exists()


def test_m_sigterm_during_validate_auto_restores(eta: World) -> None:
    """(m) P30: SIGTERM while validate hangs: auto-restored, exit 1, no marker."""
    original = tree(eta.live)
    (eta.fake / "validate.sleep").write_text("60")
    env = dict(eta.env, **{SANDBOX: "1"})
    args = [sys.executable, str(eta.script), "apply", "--live-spec", str(eta.live), "--rig", RIG]
    proc = subprocess.Popen(
        args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + 30
    while not any(a[:2] == ["spec", "validate"] for a in eta.argv()):
        assert time.monotonic() < deadline and proc.poll() is None
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 1, out + err
    assert tree(eta.live) == original
    (backup,) = eta.backups()
    (line,) = _failed_lines(out)
    assert line == f"apply failed (interrupted (SIGTERM)); auto-restored from {backup}"
    assert not (backup / "APPLY_IN_PROGRESS").exists()


def test_n_failed_auto_restore_keeps_backup_and_marker(eta: World) -> None:
    """(n) P30: the auto-restore fails too: AUTO-RESTORE FAILED, exit 1, backup and marker kept."""
    original = tree(eta.live)
    r = eta.apply(env=_faults(eta, "write,restore"))
    assert r.returncode == 1, r.stdout + r.stderr
    (backup,) = eta.backups()
    (line,) = _failed_lines(r.stdout)
    assert "AUTO-RESTORE FAILED (" in line and line.endswith(f"run restore {backup} by hand")
    assert (backup / "APPLY_IN_PROGRESS").is_file() and (backup / "SHA256SUMS").is_file()
    r = eta.run("restore", str(backup))  # the by-hand restore, no fault
    assert r.returncode == 0, r.stderr
    assert tree(eta.live) == original and not (backup / "APPLY_IN_PROGRESS").exists()


def test_o_leftover_marker_refuses_until_restore(eta: World) -> None:
    """(o) a leftover APPLY_IN_PROGRESS: plan and apply exit 1 naming it; restore clears it."""
    assert eta.apply().returncode == 0
    (backup,) = eta.backups()
    (backup / "APPLY_IN_PROGRESS").write_text("stand-in: killed mid-apply\n")
    for r in (eta.plan(), eta.apply()):
        assert r.returncode == 1
        assert f"an earlier apply did not finish: run restore {backup}" in r.stderr
    assert eta.backups() == [backup]
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stderr
    assert not (backup / "APPLY_IN_PROGRESS").exists()
    assert "an earlier apply did not finish" not in eta.plan().stderr


# --- (i) the odd fixture --------------------------------------------------------------------------


def test_i_odd_rig_yaml_is_manual_and_the_rest_applies(tmp_path: Path) -> None:
    """(i) a flow-style env: rig.yaml: manual; the other files still apply; rig.yaml untouched."""
    w = World(tmp_path, "odd")
    before = (w.live / "rig.yaml").read_bytes()
    r = w.plan()
    assert r.returncode == 1
    assert "rig.yaml: manual (env: is not a plain block mapping)" in r.stdout
    r = w.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    assert (w.live / "rig.yaml").read_bytes() == before
    assert (w.live / "roles.yml").read_bytes() == (w.src / "roles.yml").read_bytes()


# --- the sandbox guard (lead's brief, orch-lead 19:2xZ) -------------------------------------------


def test_sandbox_refuses_the_real_openrig_home(eta: World) -> None:
    """Sandbox: a live spec or backup under ~/.openrig or $OPENRIG_HOME is refused first."""
    real = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".openrig" / "specs" / "__hacf_sandbox_probe__"
    r = eta.run("plan", "--live-spec", str(real), "--rig", RIG)
    assert r.returncode == 1 and "sandbox:" in r.stderr
    r = eta.run("restore", str(real))
    assert r.returncode == 1 and "sandbox:" in r.stderr
    fake_home = eta.tmp / "openrig-home"
    r = eta.run(
        "apply", "--live-spec", str(fake_home / "specs" / RIG), "--rig", RIG,
        env={"OPENRIG_HOME": str(fake_home)},
    )  # fmt: skip
    assert r.returncode == 1 and "sandbox:" in r.stderr
    assert not real.exists() and not fake_home.exists()
    assert eta.argv() == []  # refused before any rig call


# --- usage ----------------------------------------------------------------------------------------


def test_source_must_be_a_hacf_checkout(eta: World) -> None:
    """Run from a copy outside a checkout (no openrig/hacf/rig.yaml above it): exit 2."""
    lone = eta.tmp / "lone" / "a" / "b" / "seat-config-upgrade.py"
    lone.parent.mkdir(parents=True)
    shutil.copyfile(eta.script, lone)
    r = subprocess.run(
        [sys.executable, str(lone), "plan", "--live-spec", str(eta.live), "--rig", RIG],
        env=dict(eta.env, **{SANDBOX: "1"}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2 and "HACF checkout" in r.stderr


def test_up_to_date_plan_exits_0(eta: World) -> None:
    """After an apply, plan has nothing to change and exits 0."""
    assert eta.apply().returncode == 0
    r = eta.plan()
    assert r.returncode == 0, r.stdout
    assert "rig.yaml: unchanged" in r.stdout


def test_script_is_executable_and_has_no_host_literals() -> None:
    """The shipped script is 0755 and names no host path."""
    script = HACF / "bin" / "seat-config-upgrade.py"
    assert script.stat().st_mode & 0o111
    assert "/" + "home/" not in script.read_text()


# --- OPR.99.0.11.2: the seat half reads `rig ps --full` and takes the role from `profile` -------

ROLES = sorted(role for _m, role in SEVEN)
RAW_KEYS = {"profile", "role", "model", "effort", "effortSource"}  # `ps` omits these without --full


def _planned_roles(text: str) -> list[str]:
    """The roles a plan's seat half lists, from its `  <session> (<role>):` lines."""
    found = []
    for line in text.splitlines():
        if line.startswith("  ") and line.rstrip().endswith("):") and "@" in line:
            found.append(line.rsplit("(", 1)[1][:-2])
    return sorted(found)


def _load_script():
    """Import seat-config-upgrade.py (a hyphenated file) as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seat_config_upgrade", HACF / "bin" / "seat-config-upgrade.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_a_recorded_fixture_plans_all_seven_roles(eta: World) -> None:
    """(a) With the recorded 0.7.21 record, the plan lists all 7 roles and skips none."""
    _seven(eta)
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert _planned_roles(r.stdout) == ROLES
    assert ": skipped (" not in r.stdout
    assert ["ps", "--nodes", "--rig", RIG, "--full", "--json"] in eta.argv()


def test_b_no_profile_falls_back_to_the_logical_id(eta: World) -> None:
    """(b) Without `profile`, the member id of the logicalId (last dot) maps all 7."""
    nodes = json.loads(NO_PROFILE.read_text())
    assert all("profile" not in n for n in nodes)
    (eta.fake / "ps.json").write_text(json.dumps(nodes))
    for m, _role in SEVEN:
        (eta.fake / f"status-{m}@{RIG}.json").write_text(json.dumps({"permissions": {}}))
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert _planned_roles(r.stdout) == ROLES
    assert ": skipped (" not in r.stdout


def test_profile_wins_over_the_logical_id() -> None:
    """The role is `profile` whenever it is present; the logicalId is only the fallback."""
    mod = _load_script()
    assert mod.node_role({"profile": "execute", "logicalId": "build.audit"}) == "execute"
    assert mod.node_role({"profile": "", "logicalId": "build.execute"}) == "execute"
    assert mod.node_role({"profile": None, "logicalId": "a.b.red-team"}) == "red-team"
    assert mod.node_role({"logicalId": "lead"}) == "lead"
    assert mod.node_role({}) is None


def test_the_seat_keeps_model_effort_and_source(eta: World, monkeypatch) -> None:
    """MR 1: each seat dict carries model, effort and effortSource as the record gave them."""
    _seven(eta)
    mod = _load_script()
    monkeypatch.setenv("PATH", f"{eta.fake.parent / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_RIG_DIR", str(eta.fake))
    out = {s["session"]: s for s in mod.seats(RIG, eta.src)}
    execute = out[EXEC]
    assert (execute["model"], execute["effort"], execute["effortSource"]) == (
        "claude-sonnet-5-5",
        "medium",
        "seat_choice",
    )
    audit = out[f"build-audit@{RIG}"]
    assert (audit["model"], audit["effort"], audit["effortSource"]) == (
        None,
        None,
        "runtime_default",
    )


def test_e_unknown_profile_is_skipped_and_reported(eta: World) -> None:
    """(e) A node whose profile is not in roles.yml is skipped, and the plan says so."""
    nodes = json.loads(RECORDED.read_text())
    nodes[0]["profile"] = "bogus"
    (eta.fake / "ps.json").write_text(json.dumps(nodes))
    for m, _role in SEVEN:
        (eta.fake / f"status-{m}@{RIG}.json").write_text(json.dumps({"permissions": {}}))
    r = eta.plan()
    assert f"{nodes[0]['canonicalSessionName']}: skipped (no role 'bogus' in roles.yml)" in r.stdout
    assert len(_planned_roles(r.stdout)) == 6


def test_f_the_fake_serves_only_the_recorded_shape(eta: World) -> None:
    """(f) Every node the fake serves has the key set of the recorded fixture's nodes."""
    want = {frozenset(n) for n in json.loads(RECORDED.read_text())}
    assert len(want) == 1
    (shape,) = want
    served = json.loads((eta.fake / "ps.json").read_text())
    assert served and all(set(n) == shape for n in served)
    assert {frozenset(n) for n in json.loads(NO_PROFILE.read_text())} == {shape - {"profile"}}
    # without --full the fake answers as the real CLI does: those five keys are gone
    bin_rig = eta.fake.parent / "bin" / "rig"
    env = {"FAKE_RIG_DIR": str(eta.fake), "PATH": os.environ["PATH"]}
    plain = subprocess.run(
        [str(bin_rig), "ps", "--nodes", "--rig", RIG, "--json"],
        env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert all(set(n) == shape - RAW_KEYS for n in json.loads(plain.stdout))


def test_g_the_normaliser_keeps_only_the_keep_list() -> None:
    """(g) No key outside the keep-list; a synthetic host name is gone from every string."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "normalise_rig_ps", FIXTURES / "normalise_rig_ps.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    host = "zzhost-7f3a"
    raw = [
        {
            "nodeId": "01SECRETID",
            "rigName": host,
            "logicalId": "pod.member",
            "canonicalSessionName": f"pod-member@{host}",
            "profile": "execute",
            "role": None,
            "model": "m-1",
            "effort": "low",
            "effortSource": "seat_choice",
            "cwd": f"/srv/{host}/proj",
            "resumeToken": f"tok-{host}",
            "tmuxAttachCommand": f"tmux attach -t pod-member@{host}",
            "modelObservation": {
                "model": "m-1",
                "observedAt": "2026-01-01T00:00:00Z",
                "generation": host,
            },
        },
        {"rigName": host, "logicalId": "a.b", "canonicalSessionName": f"a-b@{host}"},
    ]
    out = mod.normalise(raw)
    assert [n["logicalId"] for n in out] == ["a.b", "pod.member"]
    assert all(set(n) == set(mod.OUT_KEYS) for n in out)
    assert host not in json.dumps(out)
    assert (
        out[1]["canonicalSessionName"] == "pod-member@demo-hacf"
        and out[1]["rigName"] == "demo-hacf"
    )
    assert out[1]["modelObservation"] == {"model": "m-1"}
    assert "profile" not in mod.normalise(raw, drop_profile=True)[0]
    # the committed fixtures obey the same rule
    for path in (RECORDED, NO_PROFILE):
        for node in json.loads(path.read_text()):
            assert set(node) <= set(mod.OUT_KEYS)
            assert node["rigName"] == "demo-hacf" and node["canonicalSessionName"].endswith(
                "@demo-hacf"
            )


# --- the model half (OPR.99.0.11.7): live state against the resolved defaults and overrides -------

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
ROLE_OF = {f"{m}@{RIG}": r for m, r in SEVEN}


def _live(w: World, models: dict | None = None, drop_model: bool = False) -> list[str]:
    """Seven seats on the fake rig; `models` maps role -> (model, effort), the rest are on Opus."""
    want = _seven(w)
    nodes = json.loads((w.fake / "ps.json").read_text())
    for n in nodes:
        if drop_model:
            n.pop("model", None)
            n.pop("effort", None)
        else:
            n["model"], n["effort"] = (models or {}).get(n["profile"], (OPUS, None))
    (w.fake / "ps.json").write_text(json.dumps(nodes))
    return want


def _models(out: str) -> list[str]:
    """The model section of a plan or apply output (stripped), up to the host lines."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("models ("))
    end = next(  # the keys section (OPR.99.0.14.3), else the host lines, ends the model section
        i for i, ln in enumerate(lines) if ln.startswith(("keys (", "host lines"))
    )
    return [ln.strip() for ln in lines[start + 1 : end]]


def _block(lines: list[str], seat: str) -> list[str]:
    """The lines of one seat's block: its `model <role> <seat>:` line and the commands under it."""
    start = next(i for i, ln in enumerate(lines) if f" {seat}:" in ln and ln.startswith("model "))
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith("model "):
            break
        out.append(ln)
    return out


def _no_writes(w: World) -> None:
    """No `rig seat` verb that writes a seat ever reached the fake: only --help probes ran."""
    for a in w.argv():
        write = (["seat", "set-model"], ["seat", "set-effort"], ["seat", "set-runtime"])
        assert not (a[:2] in write and "--help" not in a)
        assert not (a[:1] == ["skill"] and "--apply" in a)
        assert a[:2] != ["seat", "launch"] and "set-permissions" not in a


def test_m_a_a_new_team_prints_every_seat_with_the_lead_last(eta: World) -> None:
    """(a) live models all unset, shipped defaults: each seat prints commands; the lead is last."""
    want = _live(eta, {r: (None, None) for _, r in SEVEN})
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    out = _models(r.stdout)
    for seat in want:
        role = ROLE_OF[seat]
        block = _block(out, seat)
        assert block[0] == (
            f"model {role} {seat}: unset (harness default) -> {OPUS} (source: default)"
        )
        assert f'rig seat set-model {seat} --model {OPUS} --reason "hacf seat-config v0.6.6"' in (
            block
        )
        if seat != LEAD:
            assert (
                f"rig seat launch {seat} --fresh --stop   (at an idle point, never mid-MiniPRD)"
            ) in block
        assert (
            f'rollback: rig seat set-model {seat} --model {OPUS} --reason "hacf seat-config '
            'rollback", then the same launch line'
        ) in block
        assert not [ln for ln in block if "set-effort" in ln]  # no effort is set by default
    assert _order("\n".join(out), "rig seat set-model ") == want and want[-1] == LEAD
    assert (
        f"rig seat launch {LEAD} --fresh --stop   "
        "(planned handover by the operator; not by the lead itself)"
    ) in _block(out, LEAD)
    # apply prints the same model lines and runs none of them
    a = eta.apply()
    assert a.returncode == 0, a.stdout + a.stderr
    assert _models(a.stdout) == out
    _no_writes(eta)


def test_m_b_an_override_reverts_a_seat_to_opus(eta: World) -> None:
    """(b) execute is live on Sonnet, overrides.yml says Opus: Sonnet -> Opus (override)."""
    _live(eta, {"execute": (SONNET, "medium")})
    (eta.live / "overrides.yml").write_text(f"seats:\n  execute: {{model: {OPUS}}}\n")
    out = _models(eta.plan().stdout)
    block = _block(out, EXEC)
    assert block[0] == f"model execute {EXEC}: {SONNET} -> {OPUS} (source: override)"
    assert f'rig seat set-model {EXEC} --model {OPUS} --reason "hacf seat-config v0.6.6"' in block
    assert len(out) == len(block) + 6  # the six seats on Opus read `(unchanged)`
    assert sum(ln.endswith("(unchanged)") for ln in out) == 6


def test_m_c_a_half_run_prints_only_what_is_left_and_replans_idempotently(eta: World) -> None:
    """(c) two of the three changed seats already match: only the third prints; then none do."""
    _live(eta, {"lead": (SONNET, "medium")})  # execute and scribe already on Opus
    out = _models(eta.plan().stdout)
    assert [ln for ln in out if ln.startswith("model ") and "->" in ln] == [
        f"model lead {LEAD}: {SONNET} -> {OPUS} (source: default)"
    ]
    assert sum(ln.endswith("(unchanged)") for ln in out) == 6
    _live(eta)  # the third now matches
    again = _models(eta.plan().stdout)
    assert again and all(ln.endswith("(unchanged)") for ln in again)
    assert not [ln for ln in again if "rig seat" in ln]


def test_m_d_hacf_own_rig_set_by_hand_goes_back_to_the_default(eta: World) -> None:
    """(d) execute was set to Sonnet by hand, the defaults say Opus: Sonnet -> Opus (default)."""
    _live(eta, {"execute": (SONNET, "medium")})
    block = _block(_models(eta.plan().stdout), EXEC)
    assert block[0] == f"model execute {EXEC}: {SONNET} -> {OPUS} (source: default)"
    assert not [ln for ln in block if "set-effort" in ln]  # no effort in the defaults


def _new_default(w: World) -> None:
    """The live spec holds HACF's seat-config.yml (tagged v0.0.2), then the source moves execute."""
    shutil.copyfile(w.src / "seat-config.yml", w.live / "seat-config.yml")
    root = w.src.parents[1]
    _git(root, "tag", "v0.0.2")
    path = w.src / "seat-config.yml"
    old = f"execute:   {{model: {OPUS}}}"
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, f"execute:   {{model: {SONNET}}}"))
    _git(root, "commit", "-qam", "execute moves")


def test_m_e_a_new_default_hidden_by_an_override_is_listed(eta: World) -> None:
    """(e) the source default for execute.model moves; the override hides it, or now equals it."""
    _live(eta)
    _new_default(eta)
    (eta.live / "overrides.yml").write_text(f"seats:\n  execute: {{model: {HAIKU}}}\n")
    r = eta.plan()
    assert (
        f"  hidden by your override: execute.model default {OPUS} -> {SONNET}; yours: {HAIKU} "
        "(overrides.yml); to adopt it, delete that line"
    ) in r.stdout
    assert "override now equals the default" not in r.stdout
    assert f"model execute {EXEC}: {OPUS} -> {HAIKU} (source: override)" in _models(r.stdout)
    (eta.live / "overrides.yml").write_text(f"seats:\n  execute: {{model: {SONNET}}}\n")
    r = eta.plan()
    assert "  override now equals the default: execute.model" in r.stdout
    assert "hidden by your override:" not in r.stdout
    (eta.live / "overrides.yml").unlink()  # no override: the new default simply applies
    r = eta.plan()
    assert "hidden by your override" not in r.stdout and "override now equals" not in r.stdout
    assert f"model execute {EXEC}: {OPUS} -> {SONNET} (source: default)" in _models(r.stdout)


def test_m_f_overrides_yml_is_never_copied_backed_up_restored_or_checksummed(eta: World) -> None:
    """(f) its bytes survive plan, apply, restore and an auto-restore; it is in no backup or sum."""
    _live(eta, {"execute": (SONNET, "medium")})
    path = eta.live / "overrides.yml"
    mine = f"# mine\nseats:\n  execute: {{model: {OPUS}}}\n".encode()
    path.write_bytes(mine)
    mtime = path.stat().st_mtime_ns
    assert eta.plan().returncode == 1 and path.read_bytes() == mine
    # an auto-restore after a failed validate
    (eta.fake / "validate.exit").write_text("1")
    assert eta.apply().returncode == 1
    assert path.read_bytes() == mine and path.stat().st_mtime_ns == mtime
    for backup in eta.backups():
        shutil.rmtree(backup)
    (eta.fake / "validate.exit").unlink()
    # a good apply, then the file is edited, then restore
    time.sleep(1.1)  # backups are named to the second
    assert eta.apply().returncode == 0
    assert path.read_bytes() == mine
    (backup,) = eta.backups()
    assert not (backup / "spec" / "overrides.yml").exists()
    assert "overrides.yml" not in (backup / "SHA256SUMS").read_text()
    assert "overrides.yml" not in (backup / MANIFEST).read_text()
    edited = mine + b"# edited between apply and restore\n"
    path.write_bytes(edited)
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stdout + r.stderr
    assert path.read_bytes() == edited
    # removing the file between apply and restore is fine too, and restore does not recreate it
    time.sleep(1.1)
    assert eta.apply().returncode == 0
    path.unlink()
    r = eta.run("restore", str(eta.backups()[-1]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not path.exists()


MANIFEST = "added.json"


def test_m_g_a_locally_edited_defaults_file_is_kept(eta: World) -> None:
    """(g) an edited caps.yml is kept with a proposed override; --take-hacf replaces it."""
    _live(eta)
    caps = (eta.src / "caps.yml").read_text()
    edited = caps.replace("cap_tokens: 330000", "cap_tokens: 123456")
    assert edited != caps
    (eta.live / "caps.yml").write_text(edited)
    guard = eta.live / "bin" / "context-guard.sh"
    guard.parent.mkdir()
    guard.write_text("#!/bin/sh\n# a local edit\n")
    guard.chmod(0o755)
    r = eta.plan()
    assert (
        "  keep (locally edited defaults; move your changes to overrides.yml, then "
        "--take-hacf caps.yml): caps.yml"
    ) in r.stdout
    assert "  proposed override: execute: {cap_tokens: 123456}" in r.stdout
    assert "  replace: bin/context-guard.sh" in r.stdout  # an always-HACF file is still replaced
    assert eta.apply().returncode == 0
    assert (eta.live / "caps.yml").read_text() == edited
    assert guard.read_bytes() == (eta.src / "bin/context-guard.sh").read_bytes()
    time.sleep(1.1)  # backups are named to the second
    r = eta.apply("--take-hacf", "caps.yml")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "  replace (--take-hacf): caps.yml" in r.stdout
    assert (eta.live / "caps.yml").read_text() == caps


def test_m_g_an_edited_seat_config_yml_is_proposed(eta: World) -> None:
    """(g) a seat-config.yml edit proposes its key; the file is kept.

    The roles.yml half of the earlier test (kept with its diff) is replaced by the R158 refusal
    tests below (OPR.99.0.14.3).
    """
    _live(eta)
    edited = (
        (eta.src / "seat-config.yml")
        .read_text()
        .replace(f"execute:   {{model: {OPUS}}}", f"execute:   {{model: {HAIKU}}}")
    )
    (eta.live / "seat-config.yml").write_text(edited)
    r = eta.plan()
    assert "--take-hacf seat-config.yml): seat-config.yml" in r.stdout
    assert f"  proposed override: execute: {{model: {HAIKU}}}" in r.stdout
    assert eta.apply().returncode == 0
    assert (eta.live / "seat-config.yml").read_text() == edited


# --- the host's verbs and a record without a model (MR 5) ---------------------------------------


def test_m_h_a_host_without_set_model_prints_manual_and_the_rest_still_prints(eta: World) -> None:
    """(h) `rig seat set-model --help` exits 1: a manual line replaces the command, nothing else."""
    _live(eta, {"execute": (SONNET, "medium")})
    (eta.fake / "help-set-model.exit").write_text("1")
    r = eta.plan()
    block = _block(_models(r.stdout), EXEC)
    assert block[0] == f"model execute {EXEC}: {SONNET} -> {OPUS} (source: default)"
    assert "model: manual (rig seat set-model not available on 0.7.21 (fake))" in block
    assert not [ln for ln in block if ln.startswith("rig seat set-model")]
    assert f"rig seat launch {EXEC} --fresh --stop   (at an idle point, never mid-MiniPRD)" in block
    assert "rig seat set-permissions" in r.stdout and "host lines" in r.stdout  # the rest prints
    assert ["--version"] in eta.argv()


def test_m_h_a_record_without_a_model_key_reads_live_unknown(eta: World) -> None:
    """(h) no `model` key at all: every seat reads `live unknown`, with how to verify it."""
    want = _live(eta, drop_model=True)
    out = _models(eta.plan().stdout)
    for seat in want:
        block = _block(out, seat)
        assert block[0] == f"model {ROLE_OF[seat]} {seat}: live unknown -> {OPUS} (source: default)"
        assert f"verify with: rig seat effort {seat}" in block
    _no_writes(eta)


def test_m_h_an_effort_default_prints_its_own_command(eta: World) -> None:
    """A source default that sets effort adds the effort line and its set-effort command."""
    _live(eta, {"execute": (OPUS, "low")})
    path = eta.src / "seat-config.yml"
    path.write_text(
        path.read_text().replace(
            f"execute:   {{model: {OPUS}}}", f"execute:   {{model: {OPUS}, effort: medium}}"
        )
    )
    _git(eta.src.parents[1], "commit", "-qam", "effort")
    block = _block(_models(eta.plan().stdout), EXEC)
    assert block[0] == f"model execute {EXEC}: {OPUS} (unchanged)"
    assert f"effort execute {EXEC}: low -> medium (source: default)" in block
    assert f'rig seat set-effort {EXEC} --effort medium --reason "hacf seat-config v0.6.6"' in block
    assert not [ln for ln in block if ln.startswith("rig seat set-model")]


def test_m_an_invalid_overrides_file_is_reported_not_crashed(eta: World) -> None:
    """A malformed overrides.yml gives one manual line for the model half; the plan still ends."""
    _live(eta)
    (eta.live / "overrides.yml").write_text("seats: [not, a, mapping]\n")
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    (line,) = _models(r.stdout)
    assert line.startswith("model: manual (seat-config resolve refused: overrides.yml")


def test_m_h_an_unknown_verb_whose_help_falls_back_to_the_parent_usage_is_manual(
    eta: World,
) -> None:
    """The real rig exits 0 for `seat <unknown> --help` and prints `rig seat`'s usage: manual."""
    _live(eta, {"execute": (SONNET, "medium")})
    (eta.fake / "help-set-model.parent").write_text("")
    block = _block(_models(eta.plan().stdout), EXEC)
    assert "model: manual (rig seat set-model not available on 0.7.21 (fake))" in block
    assert not [ln for ln in block if ln.startswith("rig seat set-model")]


# --- OPR.99.0.14.3 MR 1: an edited roles.yml is refused (R158) ----------------------------------

ETA67 = FIXTURES / "eta-v067"
AGENT = "agents/hacf/agent.yaml"
RENDERED = "seat-config.rendered.json"
SKILL = "adversarial-agent-hardening"
REFUSED = (
    "refused: roles.yml is locally edited; replacing it would drop your change at the next fresh "
    "launch, and keeping it would hide HACF's new defaults (R158)"
)
CARRY = [
    f"    {role}: {{tools: {{remove_deny: [Monitor, TaskStop]}}}}"
    for role in ("lead", "execute", "audit")  # the order of roles.yml
]


def _eta_edit(w: World, overrides: bool = False) -> None:
    """Put eta's edited roles.yml (and, asked, its overrides.yml) into the live spec."""
    shutil.copyfile(ETA67 / "roles.yml", w.live / "roles.yml")
    if overrides:
        shutil.copyfile(ETA67 / "overrides.yml", w.live / "overrides.yml")


def test_r_an_edited_roles_yml_is_refused_with_its_diff_and_override_lines(eta: World) -> None:
    """MR 1: plan prints the header and only the refusal block; apply writes nothing."""
    _eta_edit(eta)
    live, home = tree(eta.live), tree(eta.home)
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    out = r.stdout.splitlines()
    assert out[0].startswith(f"seat-config-upgrade plan: rig {RIG}; live spec {eta.live}; ")
    assert out[1:3] == [REFUSED, "  diff -u live/roles.yml HEAD/roles.yml:"]
    at = out.index("  the overrides.yml lines that carry it:")
    diff = out[3:at]
    assert diff[:2] == ["    --- live/roles.yml", "    +++ HEAD/roles.yml"]
    assert all(line.startswith("    ") for line in diff)
    assert any(line.startswith("    +") and "Monitor" in line for line in diff)
    assert out[at + 1 :] == [
        "    seats:",
        *("  " + line for line in CARRY),
        f"  next: put those lines in {eta.live}/overrides.yml yourself, then re-run with "
        "--take-hacf roles.yml",
    ]
    assert "not expressible" not in r.stdout
    # OPR.99.0.14.8 MR 4: the shared treg deny is no part of this refusal's edit. The one line
    # naming it is HACF's own header entry, which the diff shows because the live file lacks it.
    assert [line for line in out if TREG in line] == [TREG_HEADER]
    a = eta.apply()
    assert a.returncode == 1
    assert REFUSED in a.stderr and CARRY[1].strip() in a.stderr
    assert tree(eta.live) == live and tree(eta.home) == home and eta.backups() == []


def test_r_overrides_that_already_carry_the_edit_are_named(eta: World) -> None:
    """MR 1: with eta's overrides the next line changes; --take-hacf roles.yml proceeds."""
    _eta_edit(eta, overrides=True)
    r = eta.plan()
    assert r.returncode == 1
    assert r.stdout.splitlines()[-1] == (
        "  next: overrides.yml already carries these; re-run with --take-hacf roles.yml"
    )
    r = eta.plan("--take-hacf", "roles.yml")
    assert r.returncode == 1, r.stderr
    assert "  replace (--take-hacf): roles.yml" in r.stdout and "refused:" not in r.stdout
    assert "seats (the seat half" in r.stdout
    a = eta.apply("--take-hacf", "roles.yml")
    assert a.returncode == 0, a.stdout + a.stderr
    assert (eta.live / "roles.yml").read_bytes() == (eta.src / "roles.yml").read_bytes()


def test_r_a_partial_override_is_not_reported_as_carrying_it(eta: World) -> None:
    """MR 1: `already carries` needs exactly the computed edits for every printed role."""
    _eta_edit(eta)
    (eta.live / "overrides.yml").write_text(
        "seats:\n  execute: {tools: {remove_deny: [Monitor, TaskStop]}}\n"
    )
    assert "next: put those lines in " in eta.plan().stdout


def _roles_edit(w: World, change) -> None:
    data = yaml.safe_load((w.src / "roles.yml").read_text())
    change(data["roles"])
    (w.live / "roles.yml").write_text(yaml.safe_dump(data, sort_keys=False))


def test_r_other_differences_are_not_expressible(eta: World) -> None:
    """MR 1: tools_allow, mcp, skills, a one-sided role and a floor edit are named, not carried."""

    def change(roles: dict) -> None:
        roles["execute"]["tools_allow"].append("Monitor")
        roles["execute"]["tools_deny"].append("Bash")
        roles["audit"]["mcp"] = "on"
        roles["lead"]["skills"] = []
        roles["resolve"]["tools_deny"].remove("AskUserQuestion")
        roles["scribe"]["tools_deny"].append("mcp__codebase-memory-mcp")
        roles["extra"] = dict(roles["scribe"])
        del roles["architect"]

    _roles_edit(eta, change)
    out = eta.plan().stdout.splitlines()
    at = out.index("  the overrides.yml lines that carry it:")
    assert out[at + 1 : at + 3] == [
        "    seats:",
        "      execute: {tools: {add_deny: [Bash]}}",
    ]
    assert out[at + 3] == (
        "  not expressible in overrides.yml (move them by hand, then use --take-hacf roles.yml): "
        "lead.skills, architect (only in HEAD/roles.yml), resolve.tools_deny (AskUserQuestion), "
        "execute.tools_allow, audit.mcp, scribe.tools_deny (mcp__codebase-memory-mcp), "
        "extra (only in live/roles.yml)"
    )
    assert len(out) == at + 5


def test_r_a_text_only_edit_says_so(eta: World) -> None:
    """OPR.99.0.14.8 MR 3: parsed-equal files are replaced, and the lost text is shown."""
    (eta.live / "roles.yml").write_text((eta.src / "roles.yml").read_text() + "# mine\n")
    r = eta.plan()
    assert r.returncode == 1
    out = r.stdout.splitlines()
    at = out.index(TEXT_ONLY)
    assert out[at + 1 : at + 3] == ["    --- live/roles.yml", "    +++ HEAD/roles.yml"]
    assert "    -# mine" in out
    assert "refused:" not in r.stdout and "not expressible" not in r.stdout
    assert "  the overrides.yml lines that carry it:" not in out


# --- OPR.99.0.14.8: a hand-applied deny that HEAD now ships (R227) -----------------------------

HAND_DENY = FIXTURES / "hand-deny" / "roles.yml"
TREG = "mcp__treg"
TREG_HEADER = (
    "    +#   - v0.6.7: `mcp__treg` is denied on every role as well: an external paid tool service;"
)
TEXT_ONLY = "  replace (text only; no setting differs from HEAD): roles.yml"


def _hand_deny(w: World, old: str = "", new: str = "") -> bytes:
    """Put the hand-edited roles.yml into the live spec, optionally with one change."""
    text = HAND_DENY.read_text()
    if old:
        assert old in text
        text = text.replace(old, new, 1)
    (w.live / "roles.yml").write_text(text)
    return text.encode()


def test_x_a_hand_applied_deny_is_replaced_with_its_diff(eta: World) -> None:
    """MR 3 and 5: plan shows the replacement and the diff; apply leaves one deny per role."""
    live = _hand_deny(eta)
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    out = r.stdout.splitlines()
    at = out.index(TEXT_ONLY)
    assert out[at + 1 : at + 3] == ["    --- live/roles.yml", "    +++ HEAD/roles.yml"]
    assert TREG_HEADER in out
    assert (
        "    +#     project seats run in bypass mode, so deny it by default (TJ 2026-11-26)." in out
    )
    assert "refused:" not in r.stdout
    assert (eta.live / "roles.yml").read_bytes() == live
    a = eta.apply()
    assert a.returncode == 0, a.stdout + a.stderr
    assert (eta.live / "roles.yml").read_bytes() == (eta.src / "roles.yml").read_bytes()
    (backup,) = eta.backups()
    assert (backup / "spec" / "roles.yml").read_bytes() == live
    assert TEXT_ONLY in (backup / "plan.txt").read_text().splitlines()
    roles = yaml.safe_load((eta.live / "roles.yml").read_text())["roles"]
    assert len(roles) == 7
    assert {name: e["tools_deny"].count(TREG) for name, e in roles.items()} == dict.fromkeys(
        roles, 1
    )


def test_x_a_hand_deny_with_one_more_deny_is_refused_for_that_deny_only(eta: World) -> None:
    """MR 6: a setting difference is still refused (R158), and treg is no part of it."""
    live = _hand_deny(eta, "mcp__treg]", "mcp__treg, mcp__other]")
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    out = r.stdout.splitlines()
    assert out[1] == REFUSED and TEXT_ONLY not in out
    at = out.index("  the overrides.yml lines that carry it:")
    assert out[at + 1 : at + 3] == ["    seats:", "      lead: {tools: {add_deny: [mcp__other]}}"]
    assert len(out) == at + 4 and out[-1].startswith("  next: put those lines in ")
    assert not [line for line in out[at:] if TREG in line]
    assert eta.apply().returncode == 1
    assert (eta.live / "roles.yml").read_bytes() == live and eta.backups() == []


def test_x_a_hand_deny_that_is_not_yaml_is_refused(eta: World) -> None:
    """MR 6: an unparseable file is refused, never read as equal."""
    live = _hand_deny(eta, "roles:\n", "roles: [\n")
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    out = r.stdout.splitlines()
    assert out[1] == REFUSED and TEXT_ONLY not in out
    assert (
        "  not expressible in overrides.yml (move them by hand, then use --take-hacf roles.yml): "
        "roles.yml (not readable as YAML)"
    ) in out
    assert eta.apply().returncode == 1
    assert (eta.live / "roles.yml").read_bytes() == live and eta.backups() == []


# --- MR 2 and the tools effect line -------------------------------------------------------------


def _rules(out: str, seat: str) -> dict:
    lines = out.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith(f"  {seat} ("))
    return json.loads(lines[at + 1].strip().removeprefix("rules: "))


def test_s_seat_rules_are_the_resolved_ones(eta: World) -> None:
    """MR 2: with eta's overrides and roles.yml taken, execute may use Monitor and TaskStop."""
    _live(eta)
    _eta_edit(eta, overrides=True)
    r = eta.plan("--take-hacf", "roles.yml")
    assert r.returncode == 1, r.stderr
    for role in ("execute", "audit", "lead"):
        seat = next(s for s, x in ROLE_OF.items() if x == role)
        deny = _rules(r.stdout, seat)["deny"]
        assert "AskUserQuestion" in deny and not {"Monitor", "TaskStop"} & set(deny), role
    assert "Monitor" in _rules(r.stdout, f"spec-resolve@{RIG}")["deny"]
    lines = r.stdout.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("rig seat set-permissions "):
            assert lines[i + 1] == "      effect: takes effect at the next fresh launch"
    assert eta.apply("--take-hacf", "roles.yml").returncode == 0
    (backup,) = eta.backups()
    saved = json.loads((backup / "seats" / f"{EXEC}.json").read_text())
    assert "Monitor" not in saved["deny"]


def test_s_a_refused_config_prints_manual_rules_never_unresolved_ones(eta: World) -> None:
    """A config the resolver refuses gives no rules and no set-permissions command."""
    _live(eta)
    (eta.live / "overrides.yml").write_text(
        "seats:\n  execute: {tools: {remove_deny: [AskUserQuestion]}}\n"
    )
    r = eta.plan()
    assert r.returncode == 1, r.stderr
    assert "    rules: manual (seat-config refused: " in r.stdout
    assert not [
        x for x in r.stdout.splitlines() if x.strip().startswith("rig seat set-permissions")
    ]
    assert "  keys: manual (seat-config resolve refused: overrides.yml" in r.stdout
    assert eta.apply().returncode == 0
    (backup,) = eta.backups()
    assert not (backup / "seats" / f"{EXEC}.json").exists()
    assert (backup / "seats" / f"{EXEC}.rollback.json").exists()


def _skills_override(w: World, name: str = "my-skill") -> None:
    (w.live / "overrides.yml").write_text(f"seats:\n  execute: {{skills: {{add: [{name}]}}}}\n")


def _project(w: World, name: str = "my-skill") -> Path:
    root = w.tmp / "project"
    (root / ".agents" / "skills" / name).mkdir(parents=True)
    (root / ".agents" / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: stand-in\n---\n# stand-in\n"
    )
    return root


def _loadouts(w: World, ids: list[str]) -> None:
    for seat in ROLE_OF:
        (w.fake / f"loadout-{seat}.json").write_text(
            json.dumps({"skills": [{"id": i} for i in ids]})
        )


def test_s_loadout_and_project_root_reach_the_resolver(eta: World) -> None:
    """MR 2: a skill no set knows is refused; the loadout or the project makes it known."""
    _live(eta)
    _skills_override(eta)
    _loadouts(eta, [SKILL])
    r = eta.plan()
    assert (
        "seat-config resolve refused: overrides.yml: seats.execute.skills.add: unknown" in r.stdout
    )
    calls = [a for a in eta.argv() if a[:1] == ["skill"]]
    assert sorted(calls) == sorted(["skill", "loadout", "--seat", s, "--json"] for s in ROLE_OF)
    out = eta.plan("--project-root", str(_project(eta))).stdout  # P44: claude-code
    assert (
        "seat-config resolve refused: overrides.yml: seats.execute.skills.add: my-skill is only "
        "in <project>/.agents/skills, which OpenRig 0.7.23 does not scan for claude-code" in out
    )
    _loadouts(eta, [SKILL, "my-skill"])
    assert "skills execute " in eta.plan().stdout
    _loadouts(eta, [SKILL])
    (eta.fake / f"loadout-{EXEC}.json").unlink()  # a failed call: that role is unverified (R162)
    out = eta.plan().stdout
    assert _keys(out)[0] == (
        "  warning: overrides.yml: seats.execute.skills.add.my-skill: unverified: rig skill "
        "loadout unavailable"
    )
    assert "skills execute " in out


def test_s_a_project_only_skill_is_held_from_the_render(eta: World) -> None:
    """P44: with no loadout the plan warns, and apply leaves the held name out of uses.skills."""
    _installed(eta)
    _skills_override(eta)
    root = str(_project(eta))
    out = eta.plan("--project-root", root).stdout
    assert _keys(out)[0].startswith(
        "  warning: overrides.yml: seats.execute.skills.add.my-skill: unverified: rig skill "
        "loadout unavailable; my-skill is only in <project>/.agents/skills, which OpenRig "
        "0.7.23 does not scan for claude-code (scan roots: <cwd>/.claude/skills, "
    )
    r = eta.apply("--project-root", root)
    assert r.returncode in (0, 1), r.stderr
    doc = yaml.safe_load((eta.live / AGENT).read_text())
    assert doc["profiles"]["execute"]["uses"]["skills"] == [SKILL]


# --- OPR.99.0.14.4 MR 5: presets/ are carried like the other adapted files ----------------------

PRESET_FILES = [
    f"presets/{n}.yml"
    for n in ("agy", "claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5", "codex", "tiered")
]


def test_p_plan_adds_the_six_presets_and_apply_writes_them(eta: World) -> None:
    """A live spec without presets/ gets all six."""
    r = eta.plan()
    for rel in PRESET_FILES:
        assert f"  add: {rel}" in r.stdout.splitlines()
    assert eta.apply().returncode == 0
    for rel in PRESET_FILES:
        assert (eta.live / rel).read_bytes() == (eta.src / rel).read_bytes()


def test_p_an_edited_preset_is_kept_until_taken_and_a_live_only_one_is_listed(eta: World) -> None:
    """The adapted rules: keep with the diff, --take-hacf replaces, live-only is kept."""
    _installed(eta)
    mine = (eta.live / "presets/agy.yml").read_text() + "# mine\n"
    (eta.live / "presets/agy.yml").write_text(mine)
    (eta.live / "presets/own.yml").write_text("schema: hacf.seat-preset/v2\n")
    out = eta.plan().stdout
    assert (
        "  keep (locally edited; --take-hacf presets/agy.yml replaces it): presets/agy.yml" in out
    )
    assert "+++ HEAD/presets/agy.yml" in out
    assert "  keep (only in the live spec): presets/own.yml" in out
    time.sleep(1.1)  # backups are named to the second
    r = eta.apply("--take-hacf", "presets/agy.yml")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "  replace (--take-hacf): presets/agy.yml" in r.stdout
    assert (eta.live / "presets/agy.yml").read_bytes() == (eta.src / "presets/agy.yml").read_bytes()
    assert (eta.live / "presets/own.yml").is_file()


def test_p_a_dirty_source_preset_is_refused(eta: World) -> None:
    """source_paths() covers presets/."""
    with (eta.src / "presets/agy.yml").open("a") as handle:
        handle.write("# dirty\n")
    r = eta.plan()
    assert r.returncode == 1
    assert "openrig/hacf/presets/agy.yml" in r.stdout + r.stderr


def test_s_no_skills_layer_means_no_loadout_call(eta: World) -> None:
    """MR 2: `skill loadout` runs only when a layer sets skills."""
    _live(eta)
    eta.plan()
    assert not [a for a in eta.argv() if a[:1] == ["skill"]]


# --- MR 3: one apply line per key, with its effect time -----------------------------------------

KEYS_HEAD = "keys (the operator runs these per seat; this script never does):"
ROUTING = (
    "routing: read live by each seat through bin/seat-config.py resolve --section routing; no "
    "apply step"
)


def _keys(out: str) -> list[str]:
    """The keys section's lines, as printed."""
    lines = out.splitlines()
    start = lines.index(KEYS_HEAD)
    end = next(i for i, ln in enumerate(lines) if ln.startswith("host lines"))
    return lines[start + 1 : end]


def _installed(w: World) -> None:
    """Seven live seats on a spec that is up to date (applied once, no overrides)."""
    _live(w)
    r = w.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    for backup in w.backups():
        shutil.rmtree(backup)


def _local(w: World, *names: str) -> list[str]:
    (w.live / "agents/hacf/local").mkdir(parents=True, exist_ok=True)
    for name in names:
        (w.live / "agents/hacf/local" / name).write_text(f"# {name}\n")
    return [f"local/{n}" for n in names]


def test_t_defaults_print_only_the_routing_line(eta: World) -> None:
    """MR 3: nothing differs: the keys section holds the routing line alone; plan exits 0."""
    _installed(eta)
    r = eta.plan()
    assert r.returncode == 0, r.stdout
    assert _keys(r.stdout) == [ROUTING]


def test_t_a_skills_override_prints_its_line(eta: World) -> None:
    """MR 3: the skills line, exactly."""
    _installed(eta)
    _skills_override(eta)
    _loadouts(eta, [SKILL, "my-skill"])  # P44: claude-code finds it through the loadout
    r = eta.plan("--project-root", str(_project(eta)))
    assert _keys(r.stdout) == [
        f"  skills execute {EXEC}: [{SKILL}] -> [{SKILL}, my-skill] (source: default+override); "
        "rendered into agents/hacf/agent.yaml profiles.execute.uses.skills at apply; takes effect "
        "at the next fresh launch",
        ROUTING,
    ]
    assert "  render: agents/hacf/agent.yaml (resolved skills and instructions)" in r.stdout
    assert r.returncode == 1  # agent.yaml itself would change


def test_t_an_instructions_override_prints_its_line(eta: World) -> None:
    """MR 3: the instructions line, exactly."""
    _installed(eta)
    files = _local(eta, "one.md", "two.md")
    (eta.live / "overrides.yml").write_text(
        f"seats:\n  execute: {{instructions: [{', '.join(files)}]}}\n"
    )
    assert _keys(eta.plan().stdout) == [
        f"  instructions execute {EXEC}: [] -> [local/one.md, local/two.md] (source: override); "
        "rendered into agents/hacf/agent.yaml profiles.execute.startup.files at apply; takes "
        "effect at the next fresh launch",
        ROUTING,
    ]


def test_t_a_harness_override_prints_set_runtime_lead_last(eta: World) -> None:
    """MR 3: the harness lines, exactly, per seat; plan's exit code stays 0."""
    _installed(eta)
    (eta.live / "overrides.yml").write_text(
        "seats:\n  lead: {harness: pi}\n  execute: {harness: codex}\n"
    )
    r = eta.plan()
    assert r.returncode == 0, r.stdout
    reason = '--reason "hacf seat-config v0.6.7"   (successor-first switch;'
    effect = "      effect: takes effect when the successor confirms the handover"
    assert _keys(r.stdout) == [
        f"  harness execute {EXEC}: claude-code -> codex (source: override)",
        f"      rig seat set-runtime {EXEC} --runtime codex {reason} at an idle point, never "
        "mid-MiniPRD)",
        effect,
        f"  harness lead {LEAD}: claude-code -> pi (source: override)",
        f"      rig seat set-runtime {LEAD} --runtime pi {reason} planned handover by the "
        "operator; not by the lead itself)",
        effect,
        ROUTING,
    ]
    a = eta.apply()
    assert a.returncode == 0 and _keys(a.stdout) == _keys(r.stdout)
    _no_writes(eta)


def test_t_a_default_harness_switches_a_seat_back(eta: World) -> None:
    """MR 3: a seat live on codex with no override is told to go back to the default."""
    _installed(eta)
    nodes = json.loads((eta.fake / "ps.json").read_text())
    for n in nodes:
        if n["profile"] == "execute":
            n["runtime"] = "codex"
    (eta.fake / "ps.json").write_text(json.dumps(nodes))
    assert _keys(eta.plan().stdout)[0] == (
        f"  harness execute {EXEC}: codex -> claude-code (source: default)"
    )


def test_t_a_record_without_a_runtime_prints_only_a_set_harness(eta: World) -> None:
    """No `runtime` key: a default says nothing; an override reads `live unknown`."""
    _installed(eta)
    nodes = json.loads((eta.fake / "ps.json").read_text())
    for n in nodes:
        del n["runtime"]
    (eta.fake / "ps.json").write_text(json.dumps(nodes))
    assert _keys(eta.plan().stdout) == [ROUTING]
    (eta.live / "overrides.yml").write_text("seats:\n  execute: {harness: codex}\n")
    assert _keys(eta.plan().stdout)[0] == (
        f"  harness execute {EXEC}: live unknown -> codex (source: override)"
    )


def test_t_a_host_without_set_runtime_prints_manual(eta: World) -> None:
    """MR 3: a failed set-runtime probe replaces the command."""
    _installed(eta)
    (eta.live / "overrides.yml").write_text("seats:\n  execute: {harness: codex}\n")
    (eta.fake / "help-set-runtime.exit").write_text("1")
    assert _keys(eta.plan().stdout) == [
        f"  harness execute {EXEC}: claude-code -> codex (source: override)",
        "      harness: manual (rig seat set-runtime not available on 0.7.21 (fake))",
        ROUTING,
    ]


def test_t_a_community_harness_is_deferred(eta: World) -> None:
    """R161: agy is reported as deferred and no set-runtime is printed."""
    _installed(eta)
    (eta.live / "overrides.yml").write_text("seats:\n  execute: {harness: agy}\n")
    r = eta.plan()
    assert r.returncode == 0, r.stdout
    assert _keys(r.stdout) == [
        "  harness agy for execute: deferred in v0.6.7 (OpenRig 0.7.23 has no per-seat path for a "
        "community harness); not applied",
        ROUTING,
    ]
    assert "rig seat set-runtime " not in r.stdout


# --- MR 4: the render at apply ------------------------------------------------------------------


def _render_override(w: World) -> Path:
    files = _local(w, "one.md", "two.md")
    (w.live / "overrides.yml").write_text(
        "seats:\n  execute:\n    skills: {add: [my-skill]}\n"
        f"    instructions: [{', '.join(files)}]\n"
    )
    _loadouts(w, [SKILL, "my-skill"])  # P44: claude-code finds it through the loadout
    return _project(w)


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_u_apply_renders_and_restore_returns_the_bytes(eta: World) -> None:
    """MR 4: render, an unchanged second plan, and a byte-identical restore."""
    _installed(eta)
    before = (eta.live / AGENT).read_bytes()
    root = _render_override(eta)
    pre = tree(eta.live)
    time.sleep(1.1)  # backups are named to the second
    r = eta.apply("--project-root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    after = (eta.live / AGENT).read_bytes()
    old, new = yaml.safe_load(before), yaml.safe_load(after)
    execute = new["profiles"]["execute"]
    assert execute["uses"]["skills"] == [SKILL, "my-skill"]
    assert execute["startup"]["files"][1:] == [
        {"path": f"local/{n}.md", "delivery_hint": "send_text", "required": True}
        for n in ("one", "two")
    ]
    assert execute["startup"]["files"][0]["path"] == "guidance/execute.md"
    old["profiles"]["execute"] = execute
    assert new == old  # only execute's two lists changed
    added = [ln for ln in after.decode().splitlines() if ln not in before.decode().splitlines()]
    assert added[0] == f"      skills: [{SKILL}, my-skill]"
    assert added[1] == "        - path: local/one.md"
    assert json.loads((eta.live / RENDERED).read_text()) == {
        AGENT: {"rendered": _blob(after), "base": _blob(before)}
    }
    assert ["spec", "validate", str(eta.live / "rig.yaml")] in eta.argv()
    (backup,) = eta.backups()
    assert RENDERED in json.loads((backup / MANIFEST).read_text())
    p = eta.plan("--project-root", str(root))
    assert p.returncode == 0, p.stdout
    assert AGENT + "\n" not in p.stdout and "  render:" not in p.stdout
    assert _keys(p.stdout) == [ROUTING]
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree(eta.live) == pre and not (eta.live / RENDERED).exists()


def test_u_a_rendered_file_is_hacf_text_for_the_next_source(eta: World) -> None:
    """MR 4: after a source change the rendered file is replaced and re-rendered, not kept."""
    _installed(eta)
    root = _render_override(eta)
    time.sleep(1.1)
    assert eta.apply("--project-root", str(root)).returncode == 0
    path = eta.src / AGENT
    path.write_text(path.read_text() + "# a newer HACF\n")
    _git(eta.src.parents[1], "commit", "-qam", "newer agent.yaml")
    r = eta.plan("--project-root", str(root))
    assert f"  replace (unmodified HACF text): {AGENT}" in r.stdout
    assert f"--take-hacf {AGENT}" not in r.stdout
    time.sleep(1.1)
    assert eta.apply("--project-root", str(root)).returncode == 0
    text = (eta.live / AGENT).read_text()
    assert text.endswith("# a newer HACF\n") and "- path: local/one.md" in text
    assert json.loads((eta.live / RENDERED).read_text())[AGENT]["base"] == _blob(path.read_bytes())


def test_u_removing_the_override_returns_the_source_text(eta: World) -> None:
    """MR 4: with the override gone the rendered file goes back to HACF's bytes."""
    _installed(eta)
    root = _render_override(eta)
    time.sleep(1.1)
    assert eta.apply("--project-root", str(root)).returncode == 0
    (eta.live / "overrides.yml").unlink()
    time.sleep(1.1)
    assert eta.apply().returncode == 0
    assert (eta.live / AGENT).read_bytes() == (eta.src / AGENT).read_bytes()
    assert eta.plan().returncode == 0


def test_u_a_refused_config_keeps_the_rendered_file(eta: World) -> None:
    """A config the resolver refuses never drops what an earlier apply rendered."""
    _installed(eta)
    root = _render_override(eta)
    time.sleep(1.1)
    assert eta.apply("--project-root", str(root)).returncode == 0
    rendered = (eta.live / AGENT).read_bytes()
    (eta.live / "overrides.yml").write_text("seats: [not, a, mapping]\n")
    time.sleep(1.1)
    r = eta.apply()
    assert r.returncode == 0, r.stdout + r.stderr
    assert (eta.live / AGENT).read_bytes() == rendered
    assert f"  keep (an earlier apply rendered it; the seat config is refused): {AGENT}" in r.stdout


def test_u_a_kept_file_is_rendered_and_stays_kept(eta: World) -> None:
    """MR 4: the live bytes of a locally edited agent.yaml are rendered; it stays `keep`."""
    _installed(eta)
    mine = (eta.live / AGENT).read_text() + "# mine\n"
    (eta.live / AGENT).write_text(mine)
    root = _render_override(eta)
    time.sleep(1.1)
    r = eta.apply("--project-root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    text = (eta.live / AGENT).read_text()
    assert text.endswith("# mine\n") and "- path: local/two.md" in text
    p = eta.plan("--project-root", str(root))
    assert p.returncode == 0, p.stdout
    assert f"  keep (locally edited; --take-hacf {AGENT} replaces it): {AGENT}" in p.stdout


@pytest.mark.parametrize(
    ("old", "new", "key", "why"),
    [
        (
            f"      skills: [{SKILL}]\n",
            f"      skills:\n        - {SKILL}\n",
            "skills",
            "profiles.execute.uses.skills is not a one-line flow list",
        ),
        (
            "        - path: guidance/execute.md\n",
            "        - path: guidance/other.md\n",
            "instructions",
            "no guidance/execute.md entry under profiles.execute.startup.files",
        ),
        (
            "        - path: guidance/execute.md\n          delivery_hint: send_text\n"
            "          required: true\n",
            "        - path: guidance/execute.md\n          delivery_hint: send_text\n"
            "          required: true\n        - path: guidance/mine.md\n"
            "          delivery_hint: send_text\n          required: true\n",
            "instructions",
            "an entry after guidance/execute.md is not under local/: guidance/mine.md",
        ),
    ],
)
def test_u_an_unrecognised_shape_is_manual_and_the_rest_applies(
    eta: World, old: str, new: str, key: str, why: str
) -> None:
    """MR 4: manual, the role is not rendered, agent.yaml stays as the file plan left it."""
    _installed(eta)
    text = (eta.live / AGENT).read_text()
    head, mark, tail = text.partition("\n  execute:\n")
    assert mark and old in tail
    tail = tail.replace(old, new, 1)
    mine = head + mark + tail
    (eta.live / AGENT).write_text(mine)
    root = _render_override(eta)
    (eta.live / "OPS.md").write_text("old\n")
    time.sleep(1.1)
    r = eta.apply("--project-root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"  {key} execute: manual ({why})" in r.stdout.splitlines()
    assert (eta.live / AGENT).read_text() == mine
    assert not (eta.live / RENDERED).exists()
    assert (eta.live / "OPS.md").read_bytes() == (eta.src / "OPS.md").read_bytes()


# --- MR 5: the defaults an override hides, for the new keys -------------------------------------

HIDDEN = "defaults hidden by your overrides (overrides.yml is never changed):"


def _hidden(out: str) -> list[str]:
    lines = out.splitlines()
    start = lines.index(HIDDEN)
    end = next(i for i, ln in enumerate(lines) if ln.startswith("models ("))
    return lines[start + 1 : end]


def _denies(w: World, role: str) -> int:
    return len(yaml.safe_load((w.src / "roles.yml").read_text())["roles"][role]["tools_deny"])


def test_v_the_eta_overrides_list_default_and_yours(eta: World) -> None:
    """MR 5: the three tools lines, exactly."""
    _installed(eta)
    shutil.copyfile(ETA67 / "overrides.yml", eta.live / "overrides.yml")
    assert _hidden(eta.plan().stdout) == [
        f"  default vs override: {role}.tools default {_denies(eta, role)} denies; yours: "
        "{remove_deny: [Monitor, TaskStop]} (overrides.yml) -> resolved "
        f"{_denies(eta, role) - 2} denies"
        for role in ("audit", "execute", "lead")
    ]


def test_v_one_line_per_key_and_the_no_effect_edits(eta: World) -> None:
    """MR 5: every new key and each routing key, and the edits the default already holds."""
    _installed(eta)
    files = _local(eta, "one.md")
    (eta.live / "overrides.yml").write_text(
        "seats:\n"
        "  execute:\n"
        "    tools: {add_deny: [AskUserQuestion, Bash], remove_deny: [NoSuchTool]}\n"
        f"    skills: {{add: [{SKILL}], remove: [absent-skill]}}\n"
        f"    instructions: [{files[0]}]\n"
        "    harness: codex\n"
        "routing:\n"
        "  question: {destination: {session: advisor-lead@kernel}}\n"
        "  update: {destination: {rule: discover}}\n"
        "  tags: {question: 'hacf:q'}\n"
        "  format: {options: A/B/C, recommended: required}\n"
    )
    n = _denies(eta, "execute")
    yours = "(overrides.yml) -> resolved"
    session = "{session: advisor-lead@kernel}"
    assert _hidden(eta.plan().stdout) == [
        f"  default vs override: execute.tools default {n} denies; yours: {{add_deny: "
        f"[AskUserQuestion, Bash], remove_deny: [NoSuchTool]}} {yours} {n + 1} denies",
        "  override now equals the default: execute.tools.remove_deny NoSuchTool",
        "  override now equals the default: execute.tools.add_deny AskUserQuestion",
        f"  default vs override: execute.skills default [{SKILL}]; yours: {{add: [{SKILL}], "
        f"remove: [absent-skill]}} {yours} [{SKILL}]",
        "  override now equals the default: execute.skills.remove absent-skill",
        f"  override now equals the default: execute.skills.add {SKILL}",
        f"  default vs override: execute.instructions default []; yours: [local/one.md] {yours} "
        "[local/one.md]",
        f"  default vs override: execute.harness default claude-code; yours: codex {yours} codex",
        "  default vs override: routing.question.destination default {rule: discover}; yours: "
        f"{session} {yours} {session}",
        "  default vs override: routing.update.destination default {rule: discover}; yours: "
        f"{{rule: discover}} {yours} {{rule: discover}}",
        "  default vs override: routing.tags default {question: 'hacf:question', update: "
        "'hacf:update', answer: 'hacf:answer', extra: []}; yours: {question: 'hacf:q'} "
        f"{yours} {{question: 'hacf:q'}}",
        "  default vs override: routing.format default {options: A/B/C, recommended: required}; "
        "yours: {options: A/B/C, "
        f"recommended: required}} {yours} {{options: A/B/C, recommended: required}}",
    ]


def test_v_a_removed_then_denied_again_default_is_reported(eta: World) -> None:
    """MR 5: removing a skill the default holds has an effect, so only the key line prints."""
    _installed(eta)
    (eta.live / "overrides.yml").write_text(
        f"seats:\n  execute: {{skills: {{remove: [{SKILL}]}}}}\n"
    )
    assert _hidden(eta.plan().stdout) == [
        f"  default vs override: execute.skills default [{SKILL}]; yours: {{remove: [{SKILL}]}} "
        "(overrides.yml) -> resolved []"
    ]


# --- MR 6 and MR 8 ------------------------------------------------------------------------------


def _users(w: World) -> dict[str, tuple[str, int]]:
    """overrides.yml and every file under agents/hacf/local/: sha256 and mtime."""
    paths = [w.live / "overrides.yml", *sorted((w.live / "agents/hacf/local").rglob("*"))]
    return {
        str(p.relative_to(w.live)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in paths
        if p.is_file()
    }


def test_w_overrides_and_local_are_never_written(eta: World) -> None:
    """MR 6: byte-identical across plan, apply, a failed apply and restore; in no backup."""
    _installed(eta)
    root = _render_override(eta)
    (eta.live / "agents/hacf/local/empty-dir").mkdir()
    mine = _users(eta)
    assert len(mine) == 3
    args = ("--project-root", str(root))
    eta.plan(*args)
    assert _users(eta) == mine
    (eta.fake / "validate.exit").write_text("1")
    time.sleep(1.1)
    assert eta.apply(*args).returncode == 1
    assert _users(eta) == mine
    for backup in eta.backups():
        shutil.rmtree(backup)
    (eta.fake / "validate.exit").unlink()
    time.sleep(1.1)
    assert eta.apply(*args).returncode == 0
    assert _users(eta) == mine
    (backup,) = eta.backups()
    assert not (backup / "spec" / "agents" / "hacf" / "local").exists()
    assert "agents/hacf/local" not in (backup / "SHA256SUMS").read_text()
    assert "agents/hacf/local" not in (backup / MANIFEST).read_text()
    assert "overrides.yml" not in (backup / MANIFEST).read_text()
    later = eta.live / "agents/hacf/local/later.md"
    later.write_text("written between apply and restore\n")
    (eta.live / "agents/hacf/local/one.md").write_text("edited after apply\n")
    mine = _users(eta)
    r = eta.run("restore", str(backup))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _users(eta) == mine
    assert (eta.live / "agents/hacf/local/empty-dir").is_dir()


def test_s_the_resolution_dir_copies_local_and_keeps_a_link_a_link(eta: World) -> None:
    """MR 2: local/ is a copy in the temp dir; a symlinked local stays a symlink there."""
    mod = _load_script()
    _local(eta, "one.md")
    with mod.resolution_dir(eta.src, eta.live) as work:
        copied = work / "agents/hacf/local"
        assert (copied / "one.md").read_text() == "# one.md\n" and not copied.is_symlink()
        for rel in ("roles.yml", "caps.yml", "seat-config.yml", AGENT):
            assert (work / rel).read_bytes() == (eta.src / rel).read_bytes(), rel
    real = eta.tmp / "elsewhere"
    (eta.live / "agents/hacf/local").rename(real)
    (eta.live / "agents/hacf/local").symlink_to(real)
    with mod.resolution_dir(eta.src, eta.live) as work:
        assert (work / "agents/hacf/local").is_symlink()
    assert (real / "one.md").read_text() == "# one.md\n"


def test_w_the_eta_overrides_validate_on_the_shipped_defaults(tmp_path: Path) -> None:
    """MR 8: seat-config.py validate exits 0 on the shipped kit plus eta's overrides.yml."""
    work = tmp_path / "hacf"
    shutil.copytree(HACF, work)
    shutil.copyfile(ETA67 / "overrides.yml", work / "overrides.yml")
    r = subprocess.run(
        [sys.executable, str(work / "bin" / "seat-config.py"), "validate", "--dir", str(work)],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0 and r.stderr == "", r.stderr


def test_w_the_eta_fixture_differs_only_by_the_allowed_tools() -> None:
    """MR 8: eta's roles.yml is the shipped table less Monitor and TaskStop on three roles."""
    shipped = yaml.safe_load((HACF / "roles.yml").read_text())["roles"]
    mine = yaml.safe_load((ETA67 / "roles.yml").read_text())["roles"]
    for role, entry in shipped.items():
        if role in ("execute", "audit", "lead"):
            entry["tools_deny"] = [
                t for t in entry["tools_deny"] if t not in ("Monitor", "TaskStop")
            ]
    assert mine == shipped
