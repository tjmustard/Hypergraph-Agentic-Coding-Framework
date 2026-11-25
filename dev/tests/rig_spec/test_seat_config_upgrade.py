"""openrig/hacf/bin/seat-config-upgrade.py: plan, apply and restore on stand-in live specs.

Mission v064-upgrade-path OPR.99.0.10.9. Offline: a temp HACF source checkout (a copy of
openrig/hacf, with an older commit tagged v0.0.1 whose scribe guidance is the fixture's old text), a
fake `rig` on PATH (ps, seat status, spec validate; argv logged), HOME under tmp_path, and the live
specs copied from dev/tests/fixtures/seat_config/ into a stand-in spec library under tmp_path.

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
    "bin/seat-config-upgrade.py",
    "bin/seat-config.py",
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
if argv[:2] in (["seat", "set-model"], ["seat", "set-effort"]):
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
    end = next(i for i, ln in enumerate(lines) if ln.startswith("host lines"))
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
        assert not (a[:2] in (["seat", "set-model"], ["seat", "set-effort"]) and "--help" not in a)
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


def test_m_g_an_edited_roles_yml_is_kept_with_its_diff_and_a_seat_config_one_is_proposed(
    eta: World,
) -> None:
    """(g) roles.yml keeps with the unified diff; a seat-config.yml edit proposes its key."""
    _live(eta)
    (eta.live / "roles.yml").write_text("roles: {}  # mine\n")
    (eta.live / "seat-config.yml").write_text(
        (eta.src / "seat-config.yml")
        .read_text()
        .replace(f"execute:   {{model: {OPUS}}}", f"execute:   {{model: {HAIKU}}}")
    )
    r = eta.plan()
    assert "--take-hacf roles.yml): roles.yml" in r.stdout
    assert "+++ HEAD/roles.yml" in r.stdout
    assert f"  proposed override: execute: {{model: {HAIKU}}}" in r.stdout
    assert eta.apply().returncode == 0
    assert (eta.live / "roles.yml").read_text() == "roles: {}  # mine\n"


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
