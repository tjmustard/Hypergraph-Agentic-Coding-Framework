#!/usr/bin/env python3
"""The upgrade copy test (v0.6.5, C9): install, seat-config plan/apply/restore, on copies only.

usage:
  upgrade_copy_test.py fixture
  upgrade_copy_test.py live --project <dir> --live-spec <dir> [--live-spec <dir>]... --rig <name>
                            [--evidence <dir>]

Dev only, not shipped. Both modes run checks 1-9 on throwaway copies and never write an original:
  1 sha256 lists of the originals (the project tree minus .git/objects, and each live spec)
  2 the copies: `rsync -a` for the project, `cp -a` for each spec
  3 `HACF-install.sh --source <this checkout> --ref <HEAD sha> -y` on the project copy: exit 0
  4 the copy's .mcp.json is byte-identical after that install and after a second one (no
    exception in v0.6.5: lead ruling P31 built no env block)
  5 per spec copy: seat-config-upgrade.py plan exits 0 or 1 and prints its plan, which lists
    every roles.yml role and every HACF node of the rig, with none skipped (v0.6.6: a plan of
    zero seats no longer passes); for each HACF seat whose resolved model (HACF's seat-config.yml
    and the copy's overrides.yml) differs from its `rig ps --nodes --full` model, the plan prints
    that seat's `model <role> <seat>:` line with its set-model (or `manual`) and rollback lines,
    the lead's block last (v0.6.6); apply exits 0
  6 `rig spec validate` passes on the applied copy, its `cwd:` lines are unchanged, and caps.yml
    and roles.yml are present
  7 seat-config-upgrade.py restore makes the copy byte-identical to the pre-apply copy (sha256)
  8 the originals' sha256 lists are unchanged
  9 `rig specs ls` prints the same before and after

It prints `PASS|FAIL <n> <check>[: detail]` per check, then `VERDICT: PASS|FAIL`. Exit 0 PASS,
1 FAIL, 2 usage or a missing tool. The work dir is $TMPDIR/upgrade-copy-<pid>; it is removed by
that path at the end, on a failure too. Each seat-config run gets its own HOME inside it, so its
backup never reaches the real $HOME/.hacf.

fixture: deterministic and offline. The originals are made in the work dir: a project made by
installing this checkout's HEAD into a fresh `git init` dir (code graph selected, method recorded
as uv-tool, a bare codebase-memory-mcp entry pre-seeded in .mcp.json), and copies of slice 09's
stand-in specs. A fake `rig` stands in for OpenRig and records `specs ls`; failing download shims
(curl, wget, pip, uv, uvx, npx, npm) come first on PATH.

live: the lead's run (SAFE condition (b)) on the real project and specs. Checks 1-2 and the
OpenRig daemon reads (`specs ls`, `ps --nodes --full`, `seat status`) run outside; checks 3-7 run
inside bwrap with --unshare-net, a private /tmp and the originals, this checkout and the OpenRig
home bound read-only. There a `rig` wrapper serves the recorded daemon reads and passes only
`spec validate` (local) to the real rig. Checks 8-9 run outside after bwrap exits. Evidence
(check lines, sha256 lists, plan text, install output) goes to --evidence, which must be outside
this checkout and the originals; without it, it stays in the work dir and is dropped.

Every child gets a per-run CBM_RUNTIME_DIR and CBM_CACHE_DIR under the work dir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "HACF-install.sh"
SEAT_CONFIG = REPO_ROOT / "openrig" / "hacf" / "bin" / "seat-config-upgrade.py"
FIXTURE_SPECS_DIR = REPO_ROOT / "dev" / "tests" / "fixtures" / "seat_config"
FIXTURE_SPECS = ("eta-like", "cleanroom-like")
FIXTURE_RIG = "demo-hacf"  # a stand-in rig name
RECORDED_PS = FIXTURE_SPECS_DIR / "rig-ps-full-0.7.21.json"  # a real 0.7.21 record, normalised
HACF_DIR = REPO_ROOT / "openrig" / "hacf"
ROLES_YML = HACF_DIR / "roles.yml"
SEAT_RESOLVER = HACF_DIR / "bin" / "seat-config.py"
MODEL_HEAD_RE = re.compile(r"^  model (\S+) (\S+): ", re.M)
PLAN_SEAT_RE = re.compile(r"^  (\S+) \(([^)]+)\):$", re.M)
PLAN_SKIP_RE = re.compile(r"^  (\S+): skipped \((.*)\)$", re.M)
SHIMS = ("curl", "wget", "pip", "pip3", "uv", "uvx", "npx", "npm")
BARE_ENTRY = {"type": "stdio", "command": "codebase-memory-mcp", "args": []}
CHECKS = {
    1: "originals hashed",
    2: "copies made",
    3: "install --source --ref on the project copy",
    4: ".mcp.json byte-identical after install and re-install",
    5: "seat-config plan and apply; the plan covers every HACF role and seat",
    6: "rig spec validate, cwd unchanged, caps.yml and roles.yml present",
    7: "restore byte-identical to the pre-apply copy",
    8: "originals unchanged",
    9: "rig specs ls unchanged",
}
CWD_RE = re.compile(r"^\s*cwd:.*$", re.M)
TIMEOUT = 600

FAKE_RIG = r"""
import hashlib, json, os, sys
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
if argv[:2] == ["seat", "status"] and len(argv) > 2:
    path = os.path.join(d, "status-%s.json" % argv[2])
    if not os.path.exists(path):
        sys.exit(1)
    print(open(path).read())
    sys.exit(0)
if argv[:2] in (["seat", "set-model"], ["seat", "set-effort"]) and "--help" in argv:
    print("Usage: rig seat %s [options] <seat>" % argv[1])  # the host probe: the fake has the verbs
    sys.exit(0)
if argv == ["--version"]:
    print("0.7.21 (fake)")
    sys.exit(0)
if argv[:2] == ["spec", "validate"]:
    code = os.path.join(d, "validate.exit")
    print("validate: stand-in output")
    sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
if argv[:2] == ["specs", "ls"]:
    library = open(os.path.join(d, "library")).read().strip()
    for name in sorted(os.listdir(library)):
        rig_yaml = os.path.join(library, name, "rig.yaml")
        digest = hashlib.sha256(open(rig_yaml, "rb").read()).hexdigest() if os.path.isfile(
            rig_yaml) else "-"
        print("rig %s %s" % (name, digest))
    sys.exit(0)
sys.exit(97)
"""

LIVE_RIG = r"""
import json, os, sys
d = os.environ["HACF_COPYTEST_RIGREC"]
argv = sys.argv[1:]
with open(os.path.join(d, "argv.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv[:2] == ["spec", "validate"]:
    real = os.environ["HACF_COPYTEST_REAL_RIG"]
    os.execv(real, [real, *argv])
recorded = json.load(open(os.path.join(d, "recorded.json")))
answer = recorded.get(json.dumps(argv))
if answer is None:
    sys.stderr.write("copy-test rig: %s is not a recorded read-only verb\n" % " ".join(argv))
    sys.exit(97)
sys.stdout.write(answer["stdout"])
sys.exit(answer["rc"])
"""


def role_is_lead(role: str | None, session: str) -> bool:
    """Whether a planned seat is the lead's (its role, or the orch-lead@<rig> session name)."""
    return role == "lead" or session.startswith("orch-lead@")


def hacf_role(node: dict) -> str | None:
    """A node's role as seat-config-upgrade.py takes it: `profile`, else the logicalId's tail."""
    profile = node.get("profile")
    if isinstance(profile, str) and profile:
        return profile
    logical = node.get("logicalId")
    return logical.rsplit(".", 1)[-1] if isinstance(logical, str) and logical else None


class Usage(Exception):
    """A usage error or a missing tool: exit 2."""


class SetupError(Exception):
    """The fixture originals could not be made: the verdict is FAIL."""


def sha_list(root: Path, skip_git_objects: bool = False) -> dict[str, str]:
    """sha256 of every file below root (a symlink: of its target text), by posix relative path."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        if skip_git_objects and base == root / ".git":
            dirnames[:] = [d for d in dirnames if d != "objects"]
        links = [d for d in dirnames if (base / d).is_symlink()]
        for name in filenames + links:
            path = base / name
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            out[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return dict(sorted(out.items()))


def sha_text(listing: dict[str, str]) -> str:
    """A sha256sum-style text of a sha_list."""
    return "".join(f"{digest}  {rel}\n" for rel, digest in listing.items())


def cwd_lines(spec: Path) -> list[str]:
    """The `cwd:` lines of a spec's rig.yaml, in order."""
    path = spec / "rig.yaml"
    return CWD_RE.findall(path.read_text()) if path.is_file() else []


def run(argv: list[str], env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run argv (never a shell string) with env; stdout and stderr combined in .stdout."""
    return subprocess.run(
        argv,
        cwd=None if cwd is None else str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


def write_exe(path: Path, text: str) -> None:
    """Write an executable file."""
    path.write_text(text)
    path.chmod(0o755)


def head_sha() -> str:
    """This checkout's HEAD, a full sha."""
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise Usage(f"{REPO_ROOT} is not a git checkout: {proc.stderr.strip()}")
    return proc.stdout.strip()


def work_root() -> Path:
    """$TMPDIR (or the system temp dir)."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def base_env(work: Path) -> dict[str, str]:
    """The children's environment: ours without git/uv/venv state, HOME/XDG/CBM in the work dir."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GIT_", "UV_", "HACF_", "XDG_", "CBM_", "OPENRIG_"))
        and k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    gitconfig = work / "gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    env.update(
        PATH=os.pathsep.join([str(work / "bin"), env.get("PATH", "/usr/bin:/bin")]),
        HOME=str(work / "home"),
        TMPDIR=str(work / "tmp"),
        XDG_CACHE_HOME=str(work / "home" / ".cache"),
        XDG_CONFIG_HOME=str(work / "home" / ".config"),
        XDG_DATA_HOME=str(work / "home" / ".local" / "share"),
        XDG_STATE_HOME=str(work / "home" / ".local" / "state"),
        CBM_RUNTIME_DIR=str(work / "cbmrun"),
        CBM_CACHE_DIR=str(work / "cbmcache"),
        HACF_SEAT_CONFIG_SANDBOX="1",
        PYTHONDONTWRITEBYTECODE="1",
        LC_ALL="C",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="HACF Copy Test",
        GIT_AUTHOR_EMAIL="copy-test@example.invalid",
        GIT_COMMITTER_NAME="HACF Copy Test",
        GIT_COMMITTER_EMAIL="copy-test@example.invalid",
    )
    return env


def make_work_tree(work: Path) -> None:
    """The work dir's fixed parts: bin (shims, python3), home, tmp, CBM dirs, evidence."""
    for sub in ("bin", "home", "tmp", "cbmcache", "evidence", "copies/specs"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    (work / "cbmrun").mkdir(mode=0o700, exist_ok=True)
    (work / "cbmrun").chmod(0o700)
    log = work / "shims.log"
    for name in SHIMS:
        write_exe(work / "bin" / name, f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
    # A wrapper, not a symlink: a venv interpreter reached through a symlink loses its venv.
    write_exe(work / "bin" / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')


class CopyTest:
    """One run: the originals, the copies, the check results and the evidence."""

    def __init__(self, work: Path, project: Path, specs: list[Path], rig: str, evidence: Path):
        """Name every path; nothing is read or written yet."""
        self.work, self.project, self.specs, self.rig = work, project, specs, rig
        self.evidence = evidence
        self.head = head_sha()
        self.env = base_env(work)
        self.copy_project = work / "copies" / "project"
        self.copy_specs = [
            work / "copies" / "specs" / f"{i}-{s.name}" for i, s in enumerate(specs, 1)
        ]
        self.results: dict[int, tuple[bool, str]] = {}
        self.before: dict[str, dict[str, str]] = {}
        self.specs_ls_before: str | None = None

    # -- bookkeeping -------------------------------------------------------------------------

    def record(self, n: int, ok: bool, detail: str = "") -> None:
        """Keep check n's outcome (the first one recorded wins)."""
        self.results.setdefault(n, (ok, detail))

    def guarded(self, n: int, fn) -> None:
        """Run one check; an exception is that check's FAIL."""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - every failure is a FAIL line, never a crash
            self.record(n, False, f"{type(exc).__name__}: {exc}")

    def save(self, name: str, text: str) -> None:
        """Write one evidence file."""
        path = self.evidence / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def originals(self) -> dict[str, Path]:
        """Each original by its evidence label."""
        out = {"project": self.project}
        out.update({f"spec-{i}-{s.name}": s for i, s in enumerate(self.specs, 1)})
        return out

    def hash_originals(self) -> dict[str, dict[str, str]]:
        """The sha256 lists of every original."""
        return {
            label: sha_list(path, skip_git_objects=label == "project")
            for label, path in self.originals().items()
        }

    # -- checks ------------------------------------------------------------------------------

    def check_1(self) -> None:
        """Hash every original."""
        for label, path in self.originals().items():
            if not path.is_dir():
                raise FileNotFoundError(f"{label} {path} is not a directory")
        self.before = self.hash_originals()
        for label, listing in self.before.items():
            self.save(f"sha-before/{label}.txt", sha_text(listing))
        counts = ", ".join(f"{label} {len(v)} files" for label, v in self.before.items())
        self.record(1, True, counts)

    def check_2(self) -> None:
        """Copy the project (rsync -a) and each spec (cp -a); each copy hashes like its original."""
        self.copy_project.mkdir(parents=True)
        r = run(["rsync", "-a", f"{self.project}/", f"{self.copy_project}/"], self.env)
        if r.returncode != 0:
            raise RuntimeError(f"rsync -a exit {r.returncode}: {r.stdout.strip()[-300:]}")
        for spec, copy in zip(self.specs, self.copy_specs):
            r = run(["cp", "-a", str(spec), str(copy)], self.env)
            if r.returncode != 0:
                raise RuntimeError(f"cp -a {spec.name} exit {r.returncode}")
        same = sha_list(self.copy_project, True) == self.before["project"] and all(
            sha_list(c) == self.before[f"spec-{i}-{s.name}"]
            for i, (s, c) in enumerate(zip(self.specs, self.copy_specs), 1)
        )
        self.record(2, same, "each copy hashes like its original" if same else "a copy differs")

    def install(self, tag: str) -> subprocess.CompletedProcess:
        """One installer run on the project copy from this checkout's HEAD."""
        argv = ["bash", str(INSTALLER), f"--source={REPO_ROOT}", f"--ref={self.head}", "-y"]
        r = run(argv, self.env, cwd=self.copy_project)
        self.save(f"install-{tag}.out", r.stdout)
        return r

    def mcp_bytes(self) -> bytes | None:
        """The project copy's .mcp.json, or None when it has none."""
        path = self.copy_project / ".mcp.json"
        return path.read_bytes() if path.exists() else None

    def check_3_4(self) -> None:
        """Install on the project copy, then again; .mcp.json byte-identical throughout."""
        if not self.copy_project.is_dir():
            self.record(3, False, "no project copy")
            self.record(4, False, "no project copy")
            return
        mcp_before = self.mcp_bytes()
        first = self.install("1")
        self.record(3, first.returncode == 0, f"exit {first.returncode} at {self.head[:12]}")
        after_first = self.mcp_bytes()
        second = self.install("2")
        after_second = self.mcp_bytes()
        digest = "absent" if mcp_before is None else hashlib.sha256(mcp_before).hexdigest()[:16]
        same = mcp_before == after_first == after_second
        ok = same and first.returncode == 0 and second.returncode == 0
        self.record(4, ok, f"sha256 {digest} before = after 1 = after 2: {same}; "
                    f"re-install exit {second.returncode}")  # fmt: skip

    def seat_config(self, home: Path, *args: str) -> subprocess.CompletedProcess:
        """One seat-config-upgrade.py run with HOME in the work dir."""
        env = dict(self.env, HOME=str(home))
        return run([sys.executable, str(SEAT_CONFIG), *args], env)

    def resolved_models(self, copy: Path | None) -> dict[str, str] | str:
        """{role: model} from HACF defaults and the copy's overrides.yml; a reason if refused."""
        with tempfile.TemporaryDirectory(prefix="resolve-", dir=self.work) as tmp:
            work = Path(tmp)
            for name in ("seat-config.yml", "roles.yml", "caps.yml"):
                shutil.copyfile(HACF_DIR / name, work / name)
            if copy is not None and (copy / "overrides.yml").is_file():
                shutil.copyfile(copy / "overrides.yml", work / "overrides.yml")
            r = run([sys.executable, str(SEAT_RESOLVER), "resolve", "--dir", str(work),
                     "--section", "seats", "--json"], self.env)  # fmt: skip
            if r.returncode != 0:
                tail = r.stdout.strip().splitlines()
                return tail[-1] if tail else "the resolver failed"
            seats = json.loads(r.stdout)["seats"]
        return {role: row["model"]["value"] for role, row in seats.items() if "model" in row}

    def model_failures(self, plan_text: str, nodes: list, copy: Path | None) -> list[str]:
        """Check 5's model half (v0.6.6): each seat whose model must change has its lines.

        For a HACF seat whose resolved model differs from the live one (`model` absent reads as
        different), the plan has its `model <role> <seat>: ... -> ...` line, a set-model line (or
        the `manual` line a host without the verb gets) and a rollback line, and the lead's block
        is the last one. Each failure names the seat.
        """
        resolved = self.resolved_models(copy)
        if not isinstance(resolved, dict):
            return [f"cannot resolve the seat models ({resolved})"]
        section = plan_text.split("\nmodels (", 1)[-1].split("\nhost lines", 1)[0]
        heads = [(m.group(1), m.group(2), m.start()) for m in MODEL_HEAD_RE.finditer(section)]
        order = [session for _role, session, _at in heads]
        failures: list[str] = []
        due: list[tuple[str, str]] = []
        for node in nodes:
            role = hacf_role(node) if isinstance(node, dict) else None
            session = node.get("canonicalSessionName") if isinstance(node, dict) else None
            if not session or node.get("rigName", self.rig) != self.rig or role not in resolved:
                continue
            if "model" in node and node["model"] == resolved[role]:
                continue
            due.append((role, session))
            at = next((i for i, (_r, name, _a) in enumerate(heads) if name == session), None)
            if at is None:
                failures.append(f"no model line for seat {session} ({role})")
                continue
            end = heads[at + 1][2] if at + 1 < len(heads) else len(section)
            block = section[heads[at][2] : end]
            if " -> " not in block.splitlines()[0]:
                failures.append(f"model line of seat {session} shows no change")
            elif f"rig seat set-model {session} " in block:
                if f"rollback: rig seat set-model {session} " not in block:
                    failures.append(f"no rollback line for seat {session}")
            elif "model: manual (" not in block:
                failures.append(f"no set-model line for seat {session}")
        for role, session in due:
            if role_is_lead(role, session) and session in order:
                if order.index(session) != len(order) - 1:
                    failures.append(f"the lead's block ({session}) is not last")
        return failures

    def coverage_failures(self, plan_text: str, copy: Path | None = None) -> list[str]:
        """Check 5's coverage conditions (v0.6.6); each line names the role or the seat.

        Fails when (i) the plan lists zero seats, (ii) a roles.yml role has no planned seat,
        (iii) the planned seats are not the rig's HACF nodes in number, (iv) a seat is skipped.
        """
        roles = sorted(yaml.safe_load(ROLES_YML.read_text(encoding="utf-8"))["roles"])
        planned = PLAN_SEAT_RE.findall(plan_text)
        skipped = PLAN_SKIP_RE.findall(plan_text)
        failures = []
        if not planned:
            failures.append("the plan lists zero seats")
        have = {role for _session, role in planned}
        failures += [f"no planned seat for role {role}" for role in roles if role not in have]
        failures += [f"seat {session} skipped ({why})" for session, why in skipped]
        ps = run(["rig", "ps", "--nodes", "--rig", self.rig, "--full", "--json"], self.env)
        try:
            nodes = json.loads(ps.stdout) if ps.returncode == 0 else None
        except ValueError:
            nodes = None
        if not isinstance(nodes, list):
            failures.append("cannot read the rig's nodes (rig ps --nodes --full --json)")
            return failures
        hacf = [
            n["canonicalSessionName"]
            for n in nodes
            if isinstance(n, dict)
            and n.get("rigName", self.rig) == self.rig
            and n.get("canonicalSessionName")
            and hacf_role(n) in roles
        ]
        if len(planned) != len(hacf):
            seen = {session for session, _role in planned}
            missing = ", ".join(sorted(set(hacf) - seen)) or "none by name"
            failures.append(
                f"planned {len(planned)} seats, the rig has {len(hacf)} HACF nodes "
                f"(unplanned: {missing})"
            )
        return failures + self.model_failures(plan_text, nodes, copy)

    def plan_apply(self, i: int, copy: Path, home: Path) -> Path | None:
        """Check 5 for one spec copy: plan, then apply; the backup dir apply printed."""
        plan = self.seat_config(home, "plan", "--live-spec", str(copy), "--rig", self.rig)
        self.save(f"plan-{copy.name}.txt", plan.stdout)
        planned = plan.returncode in (0, 1) and bool(plan.stdout.strip())
        gaps = self.coverage_failures(plan.stdout, copy)
        if gaps:
            self.spec_notes[5].append(f"{copy.name}: " + "; ".join(gaps))
            self.spec_fail.add(5)
        apply = self.seat_config(home, "apply", "--live-spec", str(copy), "--rig", self.rig)
        self.save(f"apply-{copy.name}.txt", apply.stdout)
        match = re.search(r"^applied; backup: (.+)$", apply.stdout, re.M)
        backup = Path(match.group(1)) if match and apply.returncode == 0 else None
        self.spec_notes[5].append(
            f"{copy.name} plan exit {plan.returncode}, apply exit {apply.returncode}"
        )
        if not (planned and backup is not None):
            self.spec_fail.add(5)
        return backup

    def validate_applied(self, copy: Path, cwd_before: list[str]) -> None:
        """Check 6 for one applied spec copy."""
        r = run(["rig", "spec", "validate", str(copy / "rig.yaml")], self.env)
        self.save(f"validate-{copy.name}.txt", r.stdout)
        cwd_same = cwd_lines(copy) == cwd_before
        present = (copy / "caps.yml").is_file() and (copy / "roles.yml").is_file()
        self.spec_notes[6].append(
            f"{copy.name} validate exit {r.returncode}, cwd unchanged {cwd_same}, "
            f"caps.yml+roles.yml {present}"
        )
        if not (r.returncode == 0 and cwd_same and present):
            self.spec_fail.add(6)

    def restore(self, copy: Path, home: Path, backup: Path, pre: dict[str, str]) -> None:
        """Check 7 for one spec copy."""
        r = self.seat_config(home, "restore", str(backup))
        self.save(f"restore-{copy.name}.txt", r.stdout)
        same = sha_list(copy) == pre
        self.spec_notes[7].append(f"{copy.name} restore exit {r.returncode}, identical {same}")
        if not (r.returncode == 0 and same):
            self.spec_fail.add(7)

    def check_5_6_7(self) -> None:
        """Per spec copy (its own HOME, so backups never collide): plan/apply, validate, restore."""
        self.spec_notes: dict[int, list[str]] = {5: [], 6: [], 7: []}
        self.spec_fail: set[int] = set()
        for i, copy in enumerate(self.copy_specs, 1):
            home = self.work / f"home-spec-{i}"
            home.mkdir(exist_ok=True)
            if not copy.is_dir():
                self.spec_fail.update((5, 6, 7))
                for n in (5, 6, 7):
                    self.spec_notes[n].append(f"{copy.name}: no copy")
                continue
            pre = sha_list(copy)
            cwd_before = cwd_lines(copy)
            backup = self.plan_apply(i, copy, home)
            if backup is None:
                self.spec_fail.update((6, 7))
                self.spec_notes[6].append(f"{copy.name}: not applied")
                self.spec_notes[7].append(f"{copy.name}: no backup to restore")
                continue
            self.validate_applied(copy, cwd_before)
            self.restore(copy, home, backup, pre)
        for n in (5, 6, 7):
            self.record(n, n not in self.spec_fail, "; ".join(self.spec_notes[n]))

    def check_8(self) -> None:
        """Hash every original again; name the changed files."""
        after = self.hash_originals()
        changed = []
        for label, listing in after.items():
            self.save(f"sha-after/{label}.txt", sha_text(listing))
            if listing != self.before.get(label):
                old = self.before.get(label, {})
                diff = sorted(k for k in set(old) | set(listing) if old.get(k) != listing.get(k))
                changed.append(f"{label} ({', '.join(diff[:5])})")
        self.record(8, not changed, "changed: " + "; ".join(changed) if changed else "identical")

    def specs_ls(self) -> str:
        """`rig specs ls` on the outer PATH (the fake rig, or the real one in live mode)."""
        r = run(["rig", "specs", "ls"], self.outer_env())
        if r.returncode != 0:
            raise RuntimeError(f"rig specs ls exit {r.returncode}")
        return r.stdout

    def outer_env(self) -> dict[str, str]:
        """The environment for the outer `rig specs ls` (fixture: the fake rig)."""
        return self.env

    def check_9(self) -> None:
        """`rig specs ls` again; it must print what it printed before the run."""
        if self.specs_ls_before is None:
            raise RuntimeError("no `rig specs ls` before the run")
        after = self.specs_ls()
        self.save("specs-ls-before.txt", self.specs_ls_before)
        self.save("specs-ls-after.txt", after)
        same = after == self.specs_ls_before
        self.record(9, same, f"{len(after.splitlines())} lines, identical {same}")

    # -- the run -----------------------------------------------------------------------------

    def run_inside(self) -> None:
        """Checks 3-7, on the copies."""
        self.guarded(3, self.check_3_4)
        self.guarded(5, self.check_5_6_7)

    def report(self) -> int:
        """Print one line per check and the verdict; the exit code."""
        out = sys.stdout
        lines = []
        for n, name in CHECKS.items():
            ok, detail = self.results.get(n, (False, "not run"))
            word = "PASS" if ok else "FAIL"
            lines.append(f"{word} {n} {name}" + (f": {detail}" if detail else ""))
        verdict = all(self.results.get(n, (False,))[0] for n in CHECKS)
        lines.append(f"VERDICT: {'PASS' if verdict else 'FAIL'}")
        text = "\n".join(lines) + "\n"
        self.save("checks.txt", text)
        out.write(text)
        out.flush()
        return 0 if verdict else 1


class FixtureTest(CopyTest):
    """Fixture mode: originals, a fake rig and the shims, all made in the work dir."""

    def __init__(self, work: Path):
        """Lay out the stand-in originals' paths (made by setup)."""
        originals = work / "originals"
        specs = [originals / "specs" / name for name in FIXTURE_SPECS]
        super().__init__(work, originals / "project", specs, FIXTURE_RIG, work / "evidence")
        self.fake = work / "fake"
        self.env["FAKE_RIG_DIR"] = str(self.fake)

    def setup(self) -> None:
        """Make the originals: an installed project and the stand-in specs; the fake rig."""
        self.fake.mkdir()
        write_exe(self.work / "bin" / "rig", f"#!{sys.executable}\n{FAKE_RIG}")
        nodes = json.loads(RECORDED_PS.read_text(encoding="utf-8"))
        (self.fake / "ps.json").write_text(json.dumps(nodes))
        for session in (n["canonicalSessionName"] for n in nodes):
            record = {"seat_ref": session, "permissions": {"selectionState": "inherit"}}
            (self.fake / f"status-{session}.json").write_text(json.dumps(record))
        (self.fake / "library").write_text(str(self.specs[0].parent))
        for spec in self.specs:
            shutil.copytree(FIXTURE_SPECS_DIR / spec.name, spec, symlinks=True)
        project = self.project
        project.mkdir(parents=True)
        self.git(project, "init", "-q", "-b", "main")
        argv = ["bash", str(INSTALLER), f"--source={REPO_ROOT}", f"--ref={self.head}", "-y",
                "--ides=claude", "--code-graph=codebase-memory-mcp"]  # fmt: skip
        r = run(argv, self.env, cwd=project)
        self.save("install-0-setup.out", r.stdout)
        if r.returncode != 0:
            raise SetupError(f"setup install exit {r.returncode}: {r.stdout.strip()[-300:]}")
        hacf_yml = project / ".agents" / "local" / "hacf.yml"
        data = yaml.safe_load(hacf_yml.read_text()) if hacf_yml.exists() else None
        data = data if isinstance(data, dict) else {}
        data.setdefault("code_graph", {})["install_method"] = "uv-tool"
        hacf_yml.parent.mkdir(parents=True, exist_ok=True)
        hacf_yml.write_text(yaml.safe_dump(data, sort_keys=False))
        servers = {"foreign": {"command": "foreign-mcp"}, "codebase-memory-mcp": BARE_ENTRY}
        doc = {"mcpServers": servers}
        (project / ".mcp.json").write_text(json.dumps(doc, indent=2) + "\n")

    def git(self, cwd: Path, *args: str) -> None:
        """Run git in a fixture original; a failure is a setup failure."""
        r = run(["git", *args], self.env, cwd=cwd)
        if r.returncode != 0:
            raise SetupError(f"git {' '.join(args)}: {r.stdout.strip()[-300:]}")

    def execute(self) -> int:
        """Setup, then checks 1-9 in order."""
        try:
            self.setup()
        except SetupError as exc:
            print(f"FAIL setup: {exc}\nVERDICT: FAIL")
            return 1
        self.specs_ls_before = self.specs_ls()
        self.guarded(1, self.check_1)
        self.guarded(2, self.check_2)
        self.run_inside()
        self.guarded(8, self.check_8)
        self.guarded(9, self.check_9)
        return self.report()


# --- live mode -------------------------------------------------------------------------------


def bwrap_argv(
    work: Path, read_only: list[Path], evidence: Path | None, inner: list[str]
) -> list[str]:
    """The bwrap command for checks 3-7: no network, a private /tmp, read_only bound read-only.

    The private /tmp comes first, so the work dir and evidence binds stay visible even under /tmp.
    """
    argv = ["bwrap", "--dev-bind", "/", "/", "--tmpfs", "/tmp"]
    argv += ["--unshare-net", "--die-with-parent"]
    for path in read_only:
        argv += ["--ro-bind", str(path), str(path)]
    argv += ["--bind", str(work), str(work)]
    if evidence is not None:
        argv += ["--bind", str(evidence), str(evidence)]
    return [*argv, "--", *inner]


class LiveTest(CopyTest):
    """Live mode: copies outside, checks 3-7 inside bwrap, checks 8-9 outside."""

    def __init__(self, work, project, specs, rig, evidence, kept_evidence: Path | None):
        """kept_evidence is --evidence (bound into bwrap), or None."""
        super().__init__(work, project, specs, rig, evidence)
        self.kept_evidence = kept_evidence
        self.real_rig = shutil.which("rig") or ""
        self.outer = dict(os.environ)

    def outer_env(self) -> dict[str, str]:
        """The real rig runs with the caller's own environment, for its read-only verbs only."""
        return self.outer

    def record_daemon_reads(self) -> None:
        """Record `specs ls`, `ps --nodes --full` and each `seat status` for the rig wrapper."""
        rec = self.work / "rigrec"
        rec.mkdir()
        answers = {}

        def read(argv: list[str]) -> subprocess.CompletedProcess:
            r = subprocess.run(["rig", *argv], env=self.outer, capture_output=True, text=True,
                               timeout=120, check=False)  # fmt: skip
            answers[json.dumps(argv)] = {"rc": r.returncode, "stdout": r.stdout}
            return r

        ps = read(["ps", "--nodes", "--rig", self.rig, "--full", "--json"])
        try:
            nodes = json.loads(ps.stdout) if ps.returncode == 0 else []
        except ValueError:
            nodes = []
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, dict) and node.get("rigName", self.rig) == self.rig:
                session = node.get("canonicalSessionName")
                if session:
                    read(["seat", "status", session, "--json"])
        for argv in (
            ["seat", "set-model", "--help"],
            ["seat", "set-effort", "--help"],
            ["--version"],
        ):  # the plan's host probe (R76): read-only help and version
            read(argv)  # fmt: skip
        (rec / "recorded.json").write_text(json.dumps(answers))
        write_exe(self.work / "bin" / "rig", f"#!{sys.executable}\n{LIVE_RIG}")

    def state(self) -> dict:
        """What the inner run needs."""
        return {
            "work": str(self.work),
            "project": str(self.project),
            "specs": [str(s) for s in self.specs],
            "rig": self.rig,
            "evidence": str(self.evidence),
        }

    def inner_env(self) -> dict[str, str]:
        """The bwrap child's environment: the base env plus the rig wrapper's settings."""
        return dict(
            self.env,
            HACF_COPYTEST_RIGREC=str(self.work / "rigrec"),
            HACF_COPYTEST_REAL_RIG=self.real_rig,
            OPENRIG_HOME=str(self.work / "orhome"),
            OPENRIG_URL="http://127.0.0.1:9",
        )

    def execute(self) -> int:
        """Checks 1-2 and the daemon reads outside, 3-7 inside bwrap, 8-9 outside."""
        (self.work / "orhome").mkdir()
        self.guarded(9, lambda: setattr(self, "specs_ls_before", self.specs_ls()))
        self.guarded(1, self.check_1)
        self.guarded(2, self.check_2)
        self.record_daemon_reads()
        (self.work / "state.json").write_text(json.dumps(self.state()))
        read_only = [*self.originals().values(), REPO_ROOT]
        for extra in (Path.home() / ".openrig", Path.home() / ".cache" / "codebase-memory-mcp"):
            if extra.is_dir():
                read_only.append(extra)
        inner = [sys.executable, str(Path(__file__).resolve()), "_inner",
                 "--state", str(self.work / "state.json")]  # fmt: skip
        argv = bwrap_argv(self.work, read_only, self.kept_evidence, inner)
        r = run(argv, self.inner_env())
        self.save("bwrap.out", r.stdout)
        results = self.work / "results.json"
        if results.exists():
            for n, (ok, detail) in json.loads(results.read_text()).items():
                self.record(int(n), ok, detail)
        for n in range(3, 8):
            self.record(n, False, f"no result from inside bwrap (exit {r.returncode})")
        self.guarded(8, self.check_8)
        self.guarded(9, self.check_9)
        return self.report()


def inner_main(state_path: Path) -> int:
    """Inside bwrap: refuse writable originals, then checks 3-7; results to results.json."""
    state = json.loads(state_path.read_text())
    work = Path(state["work"])
    test = CopyTest(work, Path(state["project"]), [Path(s) for s in state["specs"]], state["rig"],
                    Path(state["evidence"]))  # fmt: skip
    test.env = dict(os.environ)
    writable = [str(p) for p in test.originals().values() if os.access(p, os.W_OK)]
    if writable:
        for n in range(3, 8):
            test.record(n, False, "an original is writable inside bwrap: " + ", ".join(writable))
    else:
        test.run_inside()
    (work / "results.json").write_text(json.dumps({str(n): r for n, r in test.results.items()}))
    return 0


# --- CLI -------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """The CLI: fixture, live and the internal _inner."""
    summary = __doc__.split("\n")[0]
    parser = argparse.ArgumentParser(prog="upgrade_copy_test.py", description=summary)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("fixture", help="deterministic and offline (the auditor's run)")
    live = sub.add_parser("live", help="throwaway copies of a real project and live specs")
    live.add_argument("--project", required=True, type=Path)
    live.add_argument("--live-spec", required=True, type=Path, action="append", dest="specs")
    live.add_argument("--rig", required=True)
    live.add_argument("--evidence", type=Path)
    inner = sub.add_parser("_inner", help=argparse.SUPPRESS)
    inner.add_argument("--state", required=True, type=Path)
    return parser


def need_tools(*names: str) -> None:
    """Raise Usage naming every tool missing from PATH."""
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise Usage(f"missing tool(s) on PATH: {', '.join(missing)}")


def check_evidence(evidence: Path, originals: list[Path]) -> Path:
    """--evidence must be outside this checkout and every original."""
    real = evidence.resolve()
    for root in [REPO_ROOT, *originals]:
        root = root.resolve()
        if real == root or root in real.parents:
            raise Usage(f"--evidence {evidence} is inside {root}; put it outside")
    real.mkdir(parents=True, exist_ok=True)
    return real


def _terminate(signum, _frame) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    """Run one mode; the exit code (0 PASS, 1 FAIL, 2 usage or a missing tool)."""
    try:
        ns = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if ns.mode == "_inner":
        return inner_main(ns.state)
    try:
        if ns.mode == "live":
            need_tools("bwrap", "rsync", "git", "rig")
            originals = [ns.project.resolve(), *(s.resolve() for s in ns.specs)]
            evidence = check_evidence(ns.evidence, originals) if ns.evidence else None
        else:
            need_tools("rsync", "git")
        head_sha()
    except Usage as exc:
        print(f"upgrade_copy_test.py: {exc}", file=sys.stderr)
        return 2
    work = work_root() / f"upgrade-copy-{os.getpid()}"
    old = signal.signal(signal.SIGTERM, _terminate)
    try:
        work.mkdir(mode=0o700)
        make_work_tree(work)
        if ns.mode == "fixture":
            return FixtureTest(work).execute()
        test = LiveTest(work, originals[0], originals[1:], ns.rig,
                        evidence or work / "evidence", evidence)  # fmt: skip
        return test.execute()
    finally:
        signal.signal(signal.SIGTERM, old)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
