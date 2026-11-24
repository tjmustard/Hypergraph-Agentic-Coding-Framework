#!/usr/bin/env python3
"""Carry HACF's seat configuration into a live rig spec: plan, apply, restore (v0.6.5).

usage:
  seat-config-upgrade.py plan    --live-spec <dir> --rig <name> [--take-hacf <rel>]...
  seat-config-upgrade.py apply   --live-spec <dir> --rig <name> [--take-hacf <rel>]...
  seat-config-upgrade.py restore <backup-dir>

The HACF source is the checkout this script runs from (it must hold openrig/hacf/rig.yaml).
This script changes FILES only. The rules that take effect on a seat live in OpenRig's seat
records; plan and apply print each seat's `rig seat set-permissions` command and its rollback, and
never run them (OPS.md, "Upgrading a live spec (v0.6.5)"). Live seats change at their next fresh
launch at a durable point, one seat at a time, never by a mass restart.

- The always-HACF set (roles.yml, caps.yml, OPS.md, bin/*) is replaced from the source in full;
  a missing file is added.
- The adapted set (agents/**, startup/**, CULTURE.md, README.md, HARDENING.md): a live file that is
  unmodified HACF text (its git blob id equals the source's at HEAD or at a v* tag) is replaced;
  any other file is kept and plan prints its diff against HEAD; --take-hacf <rel> replaces it.
  A file only in the source is added; a file only in the live spec is kept and listed.
- rig.yaml: only the top-level env.ENABLE_CLAUDEAI_MCP_SERVERS: "false" changes, by a line edit
  of a recognised shape; any other shape is `rig.yaml: manual (<reason>)` and the rest applies.
- plan and apply refuse a source checkout with any uncommitted or untracked change under the
  paths they copy (git status), so the printed `source HEAD <sha>` is true; a source that is not
  a git checkout is labelled `source: not a git checkout (files as found)`.
- apply backs up the whole live spec to $HOME/.hacf/<rig>/backups/seat-config-<UTC>/ first,
  writes each file atomically under $HOME/.hacf/<rig>/seat-config.lock, then runs
  `rig spec validate` (60 s timeout). Once the backup exists, any failure (a non-zero or timed-out
  validate, an exception, SIGTERM or SIGINT) restores the backup, verifies it and exits 1 with
  `apply failed (<reason>); auto-restored from <backup>`; if that restore fails it says
  AUTO-RESTORE FAILED and keeps everything. <backup>/APPLY_IN_PROGRESS stands from the first live
  write until success or a verified restore; while one exists, plan and apply refuse.
- restore puts the backup's files back byte for byte, removes files apply added, checks
  SHA256SUMS and prints each seat's rollback.
- The seat half lists the lead role's seat last (OPS.md: orch-lead goes last); apply records the
  order in seats/order.json for restore, and a backup without it puts orch-lead@<rig> last.

HACF_SEAT_CONFIG_SANDBOX=1 (tests): refuse any path under the real OpenRig home
($OPENRIG_HOME, and ~/.openrig of the real user) before anything is read.

Exit 0: ok (plan: no change; apply/restore: done). Exit 1: plan has changes, a refusal, or a
failed apply (auto-restored). Exit 2: usage.
"""

from __future__ import annotations

import argparse
import difflib
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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

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
ADAPTED_DIRS = ("agents", "startup")
ADAPTED_FILES = ("CULTURE.md", "README.md", "HARDENING.md")
ENV_KEY = "ENABLE_CLAUDEAI_MCP_SERVERS"
ENV_VALUE = '"false"'
REASON = "hacf seat-config v0.6.5"
SANDBOX = "HACF_SEAT_CONFIG_SANDBOX"
MANIFEST = "added.json"
MARKER = "APPLY_IN_PROGRESS"
VALIDATE_TIMEOUT = 60  # seconds (MR 7)
ORDER = "order.json"  # seats/order.json: the seat order apply printed, the lead role last
KEY_RE = re.compile(r"^(?P<indent>[ ]+)(?P<key>[A-Za-z_][A-Za-z0-9_]*):[ ]+(?P<value>\S.*)$")


class Usage(Exception):
    """A usage error: exit 2."""


class Refusal(Exception):
    """A refusal: exit 1, nothing written."""


class Failed(Exception):
    """A failed step after the backup exists: auto-restore, exit 1 (check: validate's output)."""

    def __init__(self, message: str, check: subprocess.CompletedProcess | None = None) -> None:
        """Keep the message and, for a failed validate, its completed process."""
        super().__init__(message)
        self.check = check


class Interrupted(Exception):
    """SIGTERM or SIGINT during apply: auto-restore, exit 1."""


# --- paths ------------------------------------------------------------------------------------


def checkout() -> Path:
    """The HACF checkout this script runs from; Usage when it is not one."""
    root = Path(__file__).resolve().parents[3]
    if not (root / "openrig" / "hacf" / "rig.yaml").is_file():
        raise Usage(f"run this from a HACF checkout: {root}/openrig/hacf/rig.yaml is missing")
    return root


def sandbox_guard(*paths: Path) -> None:
    """Under HACF_SEAT_CONFIG_SANDBOX=1, refuse a path under the real OpenRig home."""
    if os.environ.get(SANDBOX) != "1":
        return
    homes = [Path(pwd.getpwuid(os.getuid()).pw_dir) / ".openrig"]
    if os.environ.get("OPENRIG_HOME"):
        homes.append(Path(os.environ["OPENRIG_HOME"]))
    for path in paths:
        real = path.resolve()
        for home in homes:
            if real == home.resolve() or home.resolve() in real.parents:
                raise Refusal(f"sandbox: {path} is under the real OpenRig home {home}; refused")


def under(path: Path, root: Path) -> bool:
    """Whether path is root or below it, after resolving both."""
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def check_backup_place(backup: Path, live: Path) -> None:
    """Refuse a backup inside the spec library or inside another rig's spec."""
    if under(backup, live.parent):
        raise Refusal(f"backup {backup} is under the spec library {live.parent}; refused")
    for parent in [backup, *backup.parents]:
        if (parent / "rig.yaml").is_file():
            raise Refusal(f"backup {backup} is under {parent}, which holds a rig.yaml; refused")


def files_under(root: Path) -> list[str]:
    """Every file (or symlink) below root, as sorted posix relative paths."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in filenames + [d for d in dirnames if (base / d).is_symlink()]:
            out.append((base / name).relative_to(root).as_posix())
    return sorted(out)


def adapted(rel: str) -> bool:
    """Whether rel is in the adapted set (agents/**, startup/**, CULTURE/README/HARDENING)."""
    return rel in ADAPTED_FILES or rel.split("/", 1)[0] in ADAPTED_DIRS and "/" in rel


# --- git --------------------------------------------------------------------------------------


def git(root: Path, *args: str) -> str | None:
    """Run git in root; its stdout, or None on any failure."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode() if r.returncode == 0 else None


def blob_id(data: bytes) -> str:
    """The git hash-object id of these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def known_blobs(root: Path) -> tuple[dict[str, set[str]], str]:
    """Map rel to its blob ids at HEAD and every v* tag; also HEAD's short id ('' outside git)."""
    head = git(root, "rev-parse", "--short", "HEAD")
    if head is None:
        return {}, ""
    refs = ["HEAD"] + (git(root, "tag", "-l", "v*") or "").split()
    blobs: dict[str, set[str]] = {}
    for ref in refs:
        for line in (git(root, "ls-tree", "-r", ref, "--", "openrig/hacf") or "").splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                blobs.setdefault(path[len("openrig/hacf/") :], set()).add(parts[2])
    return blobs, head.strip()


def source_paths() -> list[str]:
    """Every source path plan and apply copy or read (the always-set and the adapted set)."""
    base = "openrig/hacf/"
    return [base + r for r in (*ALWAYS, *ADAPTED_DIRS, *ADAPTED_FILES)]


def is_git(root: Path) -> bool:
    """Whether root is inside a git work tree."""
    return (git(root, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def source_label(root: Path) -> str:
    """'source HEAD <sha>' for a git checkout, else the honest not-git label."""
    _, head = known_blobs(root)
    if is_git(root) and head:
        return f"source HEAD {head}"
    return "source: not a git checkout (files as found)"


def clean_source(root: Path) -> None:
    """P29: refuse a git source with any change (staged, modified, deleted, untracked) it copies."""
    if not is_git(root):
        return
    out = git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", *source_paths())
    if out is None:
        raise Refusal(f"git status failed in the source checkout {root}; refused")
    dirty = [line[3:] for line in out.splitlines() if line.strip()]
    if dirty:
        raise Refusal(
            f"source checkout has uncommitted changes under: {', '.join(dirty)}; "
            "commit or clean them"
        )


def unfinished(rig: str) -> None:
    """Refuse while any backup of this rig still holds APPLY_IN_PROGRESS."""
    root = Path.home() / ".hacf" / rig / "backups"
    for marker in sorted(root.glob(f"*/{MARKER}")) if root.is_dir() else []:
        raise Refusal(f"an earlier apply did not finish: run restore {marker.parent}")


# --- the file plan ----------------------------------------------------------------------------


def file_plan(src: Path, live: Path, take: list[str]) -> list[dict]:
    """One entry per path: action add, replace, same, keep, take or live-only."""
    blobs, _ = known_blobs(src.parents[1])
    source = files_under(src)
    plan = []
    for rel in ALWAYS:
        if not (src / rel).is_file():
            raise Usage(f"the source has no {rel}")
        plan.append(entry(src, live, rel, "always"))
    for rel in [r for r in source if adapted(r)]:
        e = entry(src, live, rel, "adapted")
        if e["action"] == "replace":  # the live file differs from HEAD's bytes
            if rel in take:
                e["action"] = "take"
            elif blob_id((live / rel).read_bytes()) not in blobs.get(rel, set()):
                e["action"] = "keep"  # not HACF text at HEAD or a v* tag (outside git: kept)
        plan.append(e)
    for rel in [r for r in files_under(live) if adapted(r) and r not in source]:
        plan.append({"rel": rel, "set": "adapted", "action": "live-only"})
    return plan


def entry(src: Path, live: Path, rel: str, kind: str) -> dict:
    """The plan entry for one path: symlink, add, same or replace."""
    target = live / rel
    if target.is_symlink():
        return {"rel": rel, "set": kind, "action": "symlink"}
    if not target.exists():
        return {"rel": rel, "set": kind, "action": "add"}
    if target.read_bytes() == (src / rel).read_bytes():
        return {"rel": rel, "set": kind, "action": "same"}
    return {"rel": rel, "set": kind, "action": "replace"}


# --- rig.yaml ---------------------------------------------------------------------------------


def patch_rig_yaml(text: str) -> tuple[str | None, str]:
    """Return (new text or None, note): set the one env key on a recognised shape only."""
    lines = text.splitlines(keepends=True)
    env_at = [i for i, line in enumerate(lines) if re.match(r"env\s*:", line)]
    if len(env_at) > 1:
        return None, "manual (more than one top-level env:)"
    if not env_at:
        names = [i for i, line in enumerate(lines) if re.match(r"name\s*:", line)]
        if len(names) != 1:
            return None, "manual (no single top-level name:)"
        block = f"env:\n  {ENV_KEY}: {ENV_VALUE}\n"
        new = lines[: names[0] + 1] + [block] + lines[names[0] + 1 :]
        return verify(text, "".join(new)), "add env block"
    start = env_at[0]
    if lines[start].rstrip("\n").rstrip() != "env:":
        return None, "manual (env: is not a plain block mapping)"
    end = start + 1
    while end < len(lines) and (lines[end].strip() == "" or lines[end][:1] in " \t"):
        end += 1
    while end > start + 1 and lines[end - 1].strip() == "":
        end -= 1
    body = lines[start + 1 : end]
    indents = set()
    found = None
    for offset, line in enumerate(body):
        raw = line.rstrip("\n")
        if "\t" in raw or "#" in raw:
            return None, "manual (a tab or comment inside env:)"
        m = KEY_RE.match(raw)
        if not m or m["value"][:1] in "{[&*|>!":
            return None, "manual (env: holds something other than key: scalar lines)"
        indents.add(m["indent"])
        if m["key"] == ENV_KEY:
            found = start + 1 + offset
    if len(indents) > 1:
        return None, "manual (env: keys are not evenly indented)"
    indent = indents.pop() if indents else "  "
    line = f"{indent}{ENV_KEY}: {ENV_VALUE}\n"
    if found is not None:
        if lines[found] == line:
            return text, "unchanged"
        new = lines[:found] + [line] + lines[found + 1 :]
        return verify(text, "".join(new)), "set env key"
    new = lines[:end] + [line] + lines[end:]
    return verify(text, "".join(new)), "add env key"


def verify(old: str, new: str) -> str | None:
    """new, if it parses and differs from old only at the one env key; else None."""
    try:
        before, after = yaml.safe_load(old), yaml.safe_load(new)
    except yaml.YAMLError:
        return None
    if not isinstance(after, dict) or not isinstance(after.get("env"), dict):
        return None
    if after["env"].get(ENV_KEY) != "false" or "claude_mcp" in new and "claude_mcp" not in old:
        return None
    after["env"].pop(ENV_KEY)
    if isinstance(before, dict) and isinstance(before.get("env"), dict):
        before["env"].pop(ENV_KEY, None)
    if not after["env"]:
        after.pop("env")
    if isinstance(before, dict) and before.get("env") in ({}, None):
        before.pop("env", None)
    return new if before == after else None


# --- the seat half ----------------------------------------------------------------------------


def rig_json(*args: str):
    """Run one rig verb and return its JSON, or None on any failure."""
    try:
        r = subprocess.run(["rig", *args], capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def rollback_rules(record: dict) -> dict:
    """OPS.md's rollback rule: the allow/deny the record listed under permissions, or empty."""
    perms = record.get("permissions") if isinstance(record, dict) else None
    for holder in (
        perms,
        (perms or {}).get("desired") if isinstance(perms, dict) else None,
        ((perms or {}).get("desired") or {}).get("rules") if isinstance(perms, dict) else None,
    ):
        if isinstance(holder, dict) and ("allow" in holder or "deny" in holder):
            allow, deny = holder.get("allow") or [], holder.get("deny") or []
            return {"allow": list(allow), "deny": list(deny)}
    return {"allow": [], "deny": []}


def seats(rig: str, src: Path) -> list[dict]:
    """Each seat of the rig: session, role, its status record, new rules and rollback rules."""
    nodes = rig_json("ps", "--nodes", "--rig", rig, "--json")
    if not isinstance(nodes, list):
        raise Refusal(f"rig ps --nodes --rig {rig} --json: unreadable; nothing written")
    roles = yaml.safe_load((src / "roles.yml").read_text())["roles"]
    out = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("rigName", rig) != rig:
            continue
        session, role = node.get("canonicalSessionName"), node.get("profile")
        if not session:
            continue
        seat = {"session": session, "role": role}
        if role not in roles:
            seat["skip"] = f"no role {role!r} in roles.yml"
            out.append(seat)
            continue
        record = rig_json("seat", "status", session, "--json")
        rules = subprocess.run(
            [sys.executable, str(src / "bin" / "seat-rules.py"), "--roles",
             str(src / "roles.yml"), "--role", role, "--json"],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        if rules.returncode != 0:
            raise Refusal(f"seat-rules.py --role {role}: exit {rules.returncode}")
        seat.update(record=record, rules=json.loads(rules.stdout))
        seat["rollback"] = rollback_rules(record) if isinstance(record, dict) else None
        out.append(seat)
    return lead_last(out, lambda s: s.get("role") == "lead" or is_lead(s["session"]))


def is_lead(session: str) -> bool:
    """Whether a session name is the lead's (orch-lead@<rig>)."""
    return session.startswith("orch-lead@")


def lead_last(items: list, lead) -> list:
    """Return items in order, every lead item moved to the end (OPS.md: orch-lead goes last)."""
    return [i for i in items if not lead(i)] + [i for i in items if lead(i)]


def seat_lines(rig_seats: list[dict], where: str) -> list[str]:
    """The seat half: each seat's rules, set-permissions command and rollback."""
    lines = ["seats (the seat half: the operator runs these per seat; this script never does):"]
    for s in rig_seats:
        name = s["session"]
        if "skip" in s:
            lines.append(f"  {name}: skipped ({s['skip']})")
            continue
        lines.append(f"  {name} ({s['role']}):")
        lines.append(f"    rules: {json.dumps(s['rules'], separators=(',', ':'))}")
        rules = f"{where}/seats/{name}.json"
        lines.append(f'    rig seat set-permissions {name} --rules {rules} --reason "{REASON}"')
        if s["rollback"] is None:
            lines.append(f"    rollback: unknown (rig seat status {name} --json unreadable)")
        else:
            lines.append(
                f"    rollback: rig seat set-permissions {name} --rules "
                f'{where}/seats/{name}.rollback.json --reason "{REASON} rollback"'
            )
    return lines


def host_lines(rig: str) -> list[str]:
    """The guard and checkpoint host lines with --rig filled in (slice 08's template)."""
    guard = '"<hacf checkout>/openrig/hacf/bin/context-guard.sh"'
    caps = '"<hacf checkout>/openrig/hacf/caps.yml"'
    args = f'--rig {rig} --project-root "<project root>" --caps {caps}'
    return [
        "host lines (the operator's step, under the standing policy; nothing here arms them):",
        f"  guard:      {guard} {args} --arm-checkpoints --apply || true",
        f"  checkpoint: {guard} {args} --arm-checkpoints   # dry run: shows would-arm for {rig}",
    ]


# --- plan text --------------------------------------------------------------------------------


def plan_text(src, live, rig, plan, rig_note, rig_seats, where) -> tuple[list[str], bool]:
    """The plan's lines and whether anything changes."""
    label = source_label(src.parents[1])
    lines = [f"seat-config-upgrade plan: rig {rig}; live spec {live}; {label}"]
    changes = False
    for e in plan:
        a, rel = e["action"], e["rel"]
        if a in ("add", "replace", "take"):
            changes = True
            why = {"add": "add", "replace": "replace", "take": "replace (--take-hacf)"}[a]
            if a == "replace" and e["set"] == "adapted":
                why = "replace (unmodified HACF text)"
            lines.append(f"  {why}: {rel}")
        elif a == "keep":
            lines.append(f"  keep (locally edited; --take-hacf {rel} replaces it): {rel}")
            old = (live / rel).read_text(errors="replace").splitlines(keepends=True)
            new = (src / rel).read_text(errors="replace").splitlines(keepends=True)
            diff = difflib.unified_diff(old, new, f"live/{rel}", f"HEAD/{rel}")
            lines.extend("    " + d.rstrip("\n") for d in diff)
        elif a == "live-only":
            lines.append(f"  keep (only in the live spec): {rel}")
        elif a == "symlink":
            lines.append(f"  keep (a symlink; left alone): {rel}")
    if rig_note[0] is None:
        changes = True  # a manual edit is still owed
        lines.append(f"rig.yaml: {rig_note[1]}")
    else:
        if rig_note[1] != "unchanged":
            changes = True
        lines.append(f"rig.yaml: {rig_note[1]} env.{ENV_KEY}: {ENV_VALUE}")
    lines += seat_lines(rig_seats, where) + host_lines(rig)
    return lines, changes


# --- writing ----------------------------------------------------------------------------------


def atomic_write(target: Path, data: bytes, mode: int) -> None:
    """Write data to target by a temp file in its directory and os.replace, with mode."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def mode_for(rel: str, target: Path, source: Path) -> int:
    """The mode for a written file: 0755 for bin/*, else the live file's or the source's."""
    if rel.startswith("bin/"):
        return 0o755
    if target.exists():
        return target.stat().st_mode & 0o7777
    return source.stat().st_mode & 0o7777


def sha256sums(root: Path) -> str:
    """SHA256SUMS text for every file below root (a symlink hashes its target text)."""
    out = []
    for rel in files_under(root):
        path = root / rel
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        out.append(f"{hashlib.sha256(data).hexdigest()}  {rel}\n")
    return "".join(out)


def lock(rig: str):
    """Hold $HOME/.hacf/<rig>/seat-config.lock (non-blocking); Refusal when it is held."""
    path = Path.home() / ".hacf" / rig / "seat-config.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")  # noqa: SIM115 - held for the run
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Refusal("another apply or restore is running") from None
    return handle


def restore_files(backup: Path, live: Path) -> None:
    """Put the backup's spec/ back over live byte for byte; remove files apply added."""
    spec = backup / "spec"
    added = json.loads((backup / MANIFEST).read_text()) if (backup / MANIFEST).exists() else []
    for rel in added:
        path = live / rel
        if path.is_file() or path.is_symlink():
            path.unlink()
    for rel in files_under(spec):
        src, dst = spec / rel, live / rel
        if src.is_symlink():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(src), dst)
        else:
            data, mode = src.read_bytes(), src.stat().st_mode & 0o7777
            same = dst.is_file() and not dst.is_symlink() and dst.read_bytes() == data
            if not (same and dst.stat().st_mode & 0o7777 == mode):
                atomic_write(dst, data, mode)
    for dirpath, dirnames, _ in os.walk(live, topdown=False):
        for d in dirnames:
            path = Path(dirpath) / d
            kept = (spec / path.relative_to(live)).exists()
            if not path.is_symlink() and not kept and not any(path.iterdir()):
                path.rmdir()
    if sha256sums(live) != (backup / "SHA256SUMS").read_text():
        raise Refusal(f"restore: {live} does not match {backup}/SHA256SUMS")


def rollback_lines(backup: Path) -> list[str]:
    """Restore's per-seat rollback and fresh-launch lines (OPS.md)."""
    lines = ["restored files only; seat records are unchanged: run these per seat:"]
    names = sorted(
        p.name[: -len(".rollback.json")] for p in (backup / "seats").glob("*.rollback.json")
    )
    order_file = backup / "seats" / ORDER
    order = json.loads(order_file.read_text()) if order_file.is_file() else []
    order = [s for s in order if s in names] + lead_last(
        [s for s in names if s not in order], is_lead
    )
    for seat in order:
        record = backup / "seats" / f"{seat}.rollback.json"
        lines += [
            f'  rig seat set-permissions {seat} --rules {record} --reason "{REASON} rollback"',
            f"  rig seat launch {seat} --fresh --plan --json",
            f'  rig seat launch {seat} --fresh --stop --reason "{REASON} rollback"',
        ]
    lines.append("one seat at a time at a durable point, orch-lead last; never rig up or rig down")
    return lines


# --- commands ---------------------------------------------------------------------------------


def prepare(ns):
    """Check the inputs and build the file plan and the rig.yaml edit (nothing written)."""
    live = Path(ns.live_spec)
    sandbox_guard(live, Path.home() / ".hacf" / ns.rig)
    src = checkout() / "openrig" / "hacf"
    if not (live / "rig.yaml").is_file():
        raise Usage(f"--live-spec {live} holds no rig.yaml")
    clean_source(src.parents[1])
    unfinished(ns.rig)
    for rel in ns.take_hacf:
        if not adapted(rel) or not (src / rel).is_file():
            raise Usage(f"--take-hacf {rel}: not a file of the adapted set in the source")
    plan = file_plan(src, live, ns.take_hacf)
    for rel in ns.take_hacf:
        if not any(e["rel"] == rel and e["action"] in ("take", "same", "add") for e in plan):
            raise Usage(f"--take-hacf {rel}: the live spec has no such kept file")
    rig_text = (live / "rig.yaml").read_text()
    rig_note = patch_rig_yaml(rig_text)
    if rig_note[0] is None and not rig_note[1].startswith("manual"):
        rig_note = (None, "manual (the edit did not verify)")
    return live, src, plan, rig_text, rig_note


def cmd_plan(ns) -> int:
    """Print the plan; exit 1 when anything would change."""
    live, src, plan, _, rig_note = prepare(ns)
    rig_seats = seats(ns.rig, src)
    lines, changes = plan_text(src, live, ns.rig, plan, rig_note, rig_seats, "<backup>")
    print("\n".join(lines))
    return 1 if changes else 0


def reason(exc: BaseException) -> str:
    """One short line naming why apply failed."""
    if isinstance(exc, (Failed, Interrupted)):
        return str(exc)
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted (SIGINT)"
    return f"{type(exc).__name__}: {exc}"


def _raise_interrupted(signum, _frame) -> None:
    """Signal handler: turn SIGTERM/SIGINT into an exception that takes the auto-restore path."""
    raise Interrupted(f"interrupted ({signal.Signals(signum).name})")


def write_backup(backup, live, src, rig, plan, rig_note, rig_seats) -> list[str]:
    """Write the whole backup (spec, SHA256SUMS, seats, plan.txt, added.json); the plan lines."""
    backup.mkdir(parents=True)
    shutil.copytree(live, backup / "spec", symlinks=True)
    (backup / "SHA256SUMS").write_text(sha256sums(backup / "spec"))
    (backup / "seats").mkdir()
    for s in rig_seats:
        if "skip" in s:
            continue
        name = s["session"]
        (backup / "seats" / f"{name}.before.json").write_text(json.dumps(s["record"], indent=2))
        (backup / "seats" / f"{name}.rollback.json").write_text(json.dumps(s["rollback"]))
        (backup / "seats" / f"{name}.json").write_text(json.dumps(s["rules"]))
    order = [s["session"] for s in rig_seats if "skip" not in s]  # the lead role last
    (backup / "seats" / ORDER).write_text(json.dumps(order))
    lines, _ = plan_text(src, live, rig, plan, rig_note, rig_seats, str(backup))
    (backup / "plan.txt").write_text("\n".join(lines) + "\n")
    added = [e["rel"] for e in plan if e["action"] == "add"]
    (backup / MANIFEST).write_text(json.dumps(added))
    return lines


def write_live(live, src, plan, rig_text, rig_note) -> subprocess.CompletedProcess:
    """Write each planned file and the rig.yaml edit atomically, then run rig spec validate."""
    for e in plan:
        if e["action"] in ("add", "replace", "take"):
            rel = e["rel"]
            mode = mode_for(rel, live / rel, src / rel)
            atomic_write(live / rel, (src / rel).read_bytes(), mode)
    if rig_note[0] is not None and rig_note[0] != rig_text:
        target = live / "rig.yaml"
        atomic_write(target, rig_note[0].encode(), target.stat().st_mode & 0o7777)
    try:
        check = subprocess.run(
            ["rig", "spec", "validate", str(live / "rig.yaml")],
            capture_output=True, text=True, timeout=VALIDATE_TIMEOUT,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise Failed(f"rig spec validate timed out after {VALIDATE_TIMEOUT} s") from None
    if check.returncode != 0:
        raise Failed(f"rig spec validate exited {check.returncode}", check)
    return check


def cmd_apply(ns) -> int:
    """Back up, write the files atomically under the lock, validate; auto-restore on any failure."""
    live, src, plan, rig_text, rig_note = prepare(ns)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path.home() / ".hacf" / ns.rig / "backups" / f"seat-config-{stamp}"
    check_backup_place(backup, live)
    rig_seats = seats(ns.rig, src)
    if any(s.get("rollback") is None and "skip" not in s for s in rig_seats):
        raise Refusal("a seat's status record is unreadable, so its rollback is unknown; refused")
    held = lock(ns.rig)
    saved = {s: signal.signal(s, _raise_interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            lines = write_backup(backup, live, src, ns.rig, plan, rig_note, rig_seats)
            (backup / MARKER).write_text(f"apply started {stamp}\n")
        except BaseException as exc:
            print(f"apply failed ({reason(exc)}) before any live write; the live spec is unchanged")
            return 1
        try:
            write_live(live, src, plan, rig_text, rig_note)
        except BaseException as exc:
            why = reason(exc)
            for s in saved:
                signal.signal(s, signal.SIG_IGN)  # the restore itself is not interrupted
            try:
                restore_files(backup, live)
            except BaseException as exc2:
                print(f"apply failed ({why}); AUTO-RESTORE FAILED ({reason(exc2)}): "
                      f"run restore {backup} by hand")  # fmt: skip
                return 1
            (backup / MARKER).unlink()
            check = exc.check if isinstance(exc, Failed) else None
            if check is not None:
                sys.stdout.write(check.stdout)
                sys.stderr.write(check.stderr)
            print(f"apply failed ({why}); auto-restored from {backup}")
            return 1
        (backup / MARKER).unlink()
        print("\n".join(lines))
        print(f"applied; backup: {backup}")
        return 0
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)
        held.close()


def cmd_restore(ns) -> int:
    """Restore a backup's files byte for byte and print each seat's rollback."""
    backup = Path(ns.backup)
    sandbox_guard(backup)
    plan = backup / "plan.txt"
    if not (backup / "spec" / "rig.yaml").is_file() or not plan.is_file():
        raise Usage(f"{backup} is not a seat-config backup")
    header = plan.read_text().splitlines()[0]
    m = re.match(
        r"seat-config-upgrade plan: rig (\S+); live spec (.+); source(?: HEAD |: not a git)", header
    )
    if not m:
        raise Usage(f"{backup}/plan.txt has no recognisable header")
    rig, live = m[1], Path(m[2])
    sandbox_guard(live)
    held = lock(rig)
    try:
        restore_files(backup, live)
        (backup / MARKER).unlink(missing_ok=True)
    finally:
        held.close()
    print(f"restored {live} from {backup} (SHA256SUMS verified)")
    print("\n".join(rollback_lines(backup)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run one command; return the exit code."""
    ap = argparse.ArgumentParser(prog="seat-config-upgrade.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--live-spec", required=True)
        p.add_argument("--rig", required=True)
        p.add_argument("--take-hacf", action="append", default=[], metavar="REL")
    p = sub.add_parser("restore")
    p.add_argument("backup")
    try:
        ns = ap.parse_args(argv)
        if getattr(ns, "rig", "x").strip() == "" or "/" in getattr(ns, "rig", ""):
            raise Usage("--rig must be a rig name")
        return {"plan": cmd_plan, "apply": cmd_apply, "restore": cmd_restore}[ns.command](ns)
    except Usage as exc:
        print(f"seat-config-upgrade: {exc}", file=sys.stderr)
        return 2
    except Refusal as exc:
        print(f"seat-config-upgrade: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
