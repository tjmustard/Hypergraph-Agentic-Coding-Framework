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
    "OPS.md",
    "bin/context-guard.sh",
    "bin/seat-rules.py",
    "bin/brief.sh",
    "bin/view-layout.sh",
    "bin/seat-config-upgrade.py",
)
LEAD, EXEC = f"orch-lead@{RIG}", f"build-execute@{RIG}"

FAKE_RIG = r"""#!/usr/bin/env python3
import json, os, sys, time
d = os.environ["FAKE_RIG_DIR"]
argv = sys.argv[1:]
with open(os.path.join(d, "argv.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv[:1] == ["ps"]:
    print(open(os.path.join(d, "ps.json")).read())
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


class World:
    """A temp source checkout, a fake rig, a HOME and a spec library holding one live spec."""

    def __init__(self, tmp: Path, fixture: str) -> None:
        """Build the source (v0.0.1 tag, then HEAD), the fake rig and the live spec."""
        self.tmp = tmp
        src = tmp / "src"
        shutil.copytree(HACF, src / "openrig" / "hacf")
        _git(src, "init", "-q")
        real_scribe = (src / "openrig/hacf" / SCRIBE).read_bytes()
        shutil.copyfile(FIXTURES / "old-scribe.md", src / "openrig/hacf" / SCRIBE)
        _git(src, "add", "-A")
        _git(src, "commit", "-qm", "old")
        _git(src, "tag", "v0.0.1")
        (src / "openrig/hacf" / SCRIBE).write_bytes(real_scribe)
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
        nodes = [
            {"rigName": RIG, "canonicalSessionName": LEAD, "profile": "lead"},
            {"rigName": RIG, "canonicalSessionName": EXEC, "profile": "execute"},
            {"rigName": "other-rig", "canonicalSessionName": "build-audit@other-rig"},
        ]
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


@pytest.mark.parametrize("rel", ["roles.yml", "rig.yaml", "startup/nope.md", "../x"])
def test_g_take_hacf_outside_the_adapted_set_exits_2(eta: World, rel: str) -> None:
    """(g) a --take-hacf path outside the adapted set is a usage error."""
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
    assert [a for a in w.argv() if a[0] == "seat"] == [
        ["seat", "status", LEAD, "--json"],
        ["seat", "status", EXEC, "--json"],
    ]


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
    nodes = [
        {"rigName": RIG, "canonicalSessionName": f"{m}@{RIG}", "profile": role} for m, role in SEVEN
    ]
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
