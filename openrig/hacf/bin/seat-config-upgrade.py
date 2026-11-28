#!/usr/bin/env python3
"""Carry HACF's seat configuration into a live rig spec: plan, apply, restore (v0.6.5).

usage:
  seat-config-upgrade.py plan    --live-spec <dir> --rig <name> [--take-hacf <rel>]...
                                 [--project-root <dir>]
  seat-config-upgrade.py apply   --live-spec <dir> --rig <name> [--take-hacf <rel>]...
                                 [--project-root <dir>]
  seat-config-upgrade.py restore <backup-dir>

The HACF source is the checkout this script runs from (it must hold openrig/hacf/rig.yaml).
This script changes FILES only. The rules that take effect on a seat live in OpenRig's seat
records; plan and apply print each seat's `rig seat set-permissions` command and its rollback, and
never run them (OPS.md, "Upgrading a live spec (v0.6.5)"). Live seats change at their next fresh
launch at a durable point, one seat at a time, never by a mass restart.

- The always-HACF set (OPS.md, bin/*, host.example.yml) is replaced from the source in full; a
  missing file is added.
- The defaults files (roles.yml, caps.yml, seat-config.yml) are replaced when the live file is HACF
  text (its git blob id is HACF's at HEAD or at a v* tag). A locally edited caps.yml or
  seat-config.yml is kept and plan prints how to move the change into overrides.yml; a locally
  edited roles.yml is refused (below) unless it parses equal to HEAD's: a difference only in
  comments or layout is replaced, and plan prints its diff. `--take-hacf <rel>` replaces any of
  them.
- overrides.yml is the user's: it is in no copy, backup, restore or checksum, and is never written.
- The adapted set (agents/**, startup/**, presets/**, CULTURE.md, README.md, HARDENING.md): a live
  file that is unmodified HACF text (its git blob id equals the source's at HEAD or at a v* tag) is
  replaced; any other file is kept and plan prints its diff against HEAD; --take-hacf <rel>
  replaces it. A file only in the source is added; a file only in the live spec is kept and listed.
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
- The model half (v0.6.6) compares each seat's resolved model and effort (seat-config.yml, then the
  live overrides.yml) with its LIVE `rig ps --nodes --full` state and prints the `rig seat
  set-model`, `set-effort` and fresh-launch commands with an Opus rollback, the lead last. It also
  lists each new default an override hides. It never runs a command that writes a seat; a host
  whose `rig seat set-model --help` fails gets `manual (...)` in place of the command.

Seat config v2 (v0.6.7):
- An edited roles.yml (R158). Keeping it would hide HACF's new defaults and replacing it would
  re-deny a tool at the next fresh launch, so plan prints only its unified diff, the overrides.yml
  `seats.<role>.tools` lines that carry the tools_deny difference and every other difference as
  not expressible, and exits 1; apply refuses before the backup. `--take-hacf roles.yml` takes
  HACF's file.
- One resolution dir. The seat rules and the model, key and hidden-default sections read a temp dir
  of the source defaults, presets and agent.yaml, plus read-only copies of the live overrides.yml
  and agents/hacf/local/. `--project-root` and, when a layer sets skills, each seat's `rig skill
  loadout --seat <session> --json` tell bin/seat-config.py which skills exist. A config it refuses
  gives `manual` lines, never rules the overrides do not shape.
- Key lines. Each key whose effect is owed gets one line naming when it takes effect: tools (at the
  next fresh launch, under each set-permissions command), skills and instructions (rendered at
  apply; next fresh launch), a claude-code, codex or pi harness that differs from the seat's live
  runtime (`rig seat set-runtime`; when the successor confirms), any other harness (deferred, not
  applied) and routing (read live; no apply step). They never change plan's exit code.
- The render. apply writes the resolved skills and instructions into the role's profile in
  agents/hacf/agent.yaml by a line edit of a recognised shape, checked by parsing the result; any
  other shape is `<key> <role>: manual (<reason>)` and that role is not rendered.
  seat-config.rendered.json records the written and unrendered blob ids, so a later plan treats
  the rendered file as HACF text. agents/hacf/local/, like overrides.yml, is in no copy, backup,
  restore or checksum and is never written.
- Hidden defaults. For every role and new key overrides.yml sets, plan prints the default, your
  edit and the resolved value, and names an edit the default already holds.

HACF_SEAT_CONFIG_SANDBOX=1 (tests): refuse any path under the real OpenRig home
($OPENRIG_HOME, and ~/.openrig of the real user) before anything is read.

Exit 0: ok (plan: no change; apply/restore: done). Exit 1: plan has changes, a refusal, or a
failed apply (auto-restored). Exit 2: usage.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import difflib
import fcntl
import hashlib
import importlib.util
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
DEFAULTS = ("caps.yml", "seat-config.yml", "roles.yml")  # kept when locally edited (R87)
OVERRIDES = "overrides.yml"  # the user's file: in no copy, backup, restore or checksum (R85)
AGENT = "agents/hacf/agent.yaml"
LOCAL = "agents/hacf/local"  # the user's guidance files: treated like overrides.yml
RENDERED = "seat-config.rendered.json"  # the blob ids of the agent.yaml apply rendered
ADAPTED_DIRS = ("agents", "startup", "presets")
ADAPTED_FILES = ("CULTURE.md", "README.md", "HARDENING.md")
ENV_KEY = "ENABLE_CLAUDEAI_MCP_SERVERS"
ENV_VALUE = '"false"'
REASON = "hacf seat-config v0.6.5"
MODEL_REASON = "hacf seat-config v0.6.6"
RUNTIME_REASON = "hacf seat-config v0.6.7"
NEXT_LAUNCH = "takes effect at the next fresh launch"
NEW_KEYS = ("tools", "skills", "instructions", "harness")  # hidden-default lines (US-004)
EDITS = {"tools": ("remove_deny", "add_deny"), "skills": ("remove", "add")}
OPUS = "claude-opus-5-5"
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


def users(rel: str) -> bool:
    """Whether rel is the user's: overrides.yml, or agents/hacf/local and everything below it."""
    return rel == OVERRIDES or rel == LOCAL or rel.startswith(f"{LOCAL}/")


def files_under(root: Path) -> list[str]:
    """Every file (or symlink) below root, as sorted posix relative paths."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in filenames + [d for d in dirnames if (base / d).is_symlink()]:
            out.append((base / name).relative_to(root).as_posix())
    return sorted(out)


def adapted(rel: str) -> bool:
    """Whether rel is in the adapted set (ADAPTED_DIRS and ADAPTED_FILES)."""
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
    """One entry per path: action add, replace, same, keep, refuse, take or live-only."""
    blobs, _ = known_blobs(src.parents[1])
    source = files_under(src)
    plan = []
    for rel in ALWAYS:
        if not (src / rel).is_file():
            raise Usage(f"the source has no {rel}")
        e = entry(src, live, rel, "defaults" if rel in DEFAULTS else "always")
        if e["set"] == "defaults" and e["action"] == "replace":  # R87: locally edited is kept
            if rel in take:
                e["action"] = "take"
            elif blob_id((live / rel).read_bytes()) not in blobs.get(rel, set()):
                if rel != "roles.yml":
                    e["action"] = "keep"
                elif roles_difference(src, live) is None:
                    e["text_only"] = True  # R227: no setting differs, so replace and show the diff
                else:
                    e["action"] = "refuse"  # R158
        plan.append(e)
    for rel in [r for r in source if adapted(r) and not users(r)]:
        e = entry(src, live, rel, "adapted")
        if e["action"] == "replace":  # the live file differs from HEAD's bytes
            if rel in take:
                e["action"] = "take"
            else:
                mine = blob_id((live / rel).read_bytes())
                ours = blobs.get(rel, set())
                if rel == AGENT and rendered_from(live, mine) in ours:
                    e["rendered"] = True  # apply's own render of HACF text
                elif mine not in ours:
                    e["action"] = "keep"  # not HACF text at HEAD or a v* tag (outside git: kept)
        plan.append(e)
    for rel in [r for r in files_under(live) if adapted(r) and not users(r) and r not in source]:
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


def rendered_from(live: Path, blob: str) -> str | None:
    """The unrendered blob id the marker records for a rendered agent.yaml with this blob id."""
    try:
        record = json.loads((live / RENDERED).read_text())[AGENT]
        return record["base"] if record["rendered"] == blob else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- an edited roles.yml (R158) ----------------------------------------------------------------


def seat_config_module(src: Path):
    """The source's bin/seat-config.py as a module; no bytecode is written beside it."""
    spec = importlib.util.spec_from_file_location("seat_config", src / "bin" / "seat-config.py")
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def flow(value) -> str:
    """Return value as one line of flow-style YAML."""
    text = yaml.safe_dump([value], default_flow_style=True, sort_keys=False, width=10**9)
    return text.strip()[1:-1]


def roles_difference(src: Path, live: Path) -> tuple[dict, list[str]] | None:
    """({role: tools edit}, [what overrides.yml cannot say]); None when the files parse equal."""
    new = yaml.safe_load((src / "roles.yml").read_text())
    try:
        old = yaml.safe_load((live / "roles.yml").read_text())
    except (UnicodeDecodeError, yaml.YAMLError):
        return {}, ["roles.yml (not readable as YAML)"]
    if old == new:
        return None
    theirs = new["roles"]
    mine = old.get("roles") if isinstance(old, dict) else None
    if not isinstance(mine, dict):
        return {}, ["roles.yml (no roles table)"]
    config = seat_config_module(src)
    edits: dict = {}
    rest = [str(k) for k in {**new, **old} if k != "roles" and new.get(k) != old.get(k)]
    for role in {**theirs, **mine}:
        a, b = theirs.get(role), mine.get(role)
        if not isinstance(a, dict) or not isinstance(b, dict):
            if role not in mine:
                rest.append(f"{role} (only in HEAD/roles.yml)")
            elif role not in theirs:
                rest.append(f"{role} (only in live/roles.yml)")
            else:
                rest.append(str(role))
            continue
        for key in {**a, **b}:
            if key in a and key in b and a[key] == b[key]:
                continue
            deny = b.get(key)
            if key != "tools_deny" or not isinstance(deny, list) or key not in a:
                rest.append(f"{role}.{key}")
                continue
            carried: dict = {}
            for name, tools in (
                ("remove_deny", [t for t in a[key] if t not in deny]),
                ("add_deny", [t for t in deny if t not in a[key]]),
            ):
                for tool in tools:
                    problems = config.Problems()  # the resolver's own floor and rule shapes
                    if isinstance(tool, str) and tool:
                        config.check_tools("-", "-", {name: [tool]}, problems)
                    else:
                        problems.add("-", "-", "not a tool name")
                    if problems.lines:
                        rest.append(f"{role}.{key} ({tool})")
                    else:
                        carried.setdefault(name, []).append(tool)
            if carried:
                edits[role] = carried
            elif set(map(str, deny)) == set(a[key]):
                rest.append(f"{role}.{key} (order or repeats only)")
    return edits, rest


def carried_by_overrides(live: Path, edits: dict) -> bool:
    """Whether the live overrides.yml holds exactly these tools edits, as sets, per role."""
    try:
        seats_ = yaml.safe_load((live / OVERRIDES).read_text())["seats"]
        return bool(edits) and all(
            {k: set(v) for k, v in seats_[role]["tools"].items() if v}
            == {k: set(v) for k, v in edit.items()}
            for role, edit in edits.items()
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError, AttributeError):
        return False


def diff_lines(live: Path, src: Path, rel: str) -> list[str]:
    """The unified diff of the live file against HEAD's, indented 4."""
    old = (live / rel).read_text(errors="replace").splitlines(keepends=True)
    new = (src / rel).read_text(errors="replace").splitlines(keepends=True)
    diff = difflib.unified_diff(old, new, f"live/{rel}", f"HEAD/{rel}")
    return ["    " + d.rstrip("\n") for d in diff]


def refusal_lines(src: Path, live: Path) -> list[str]:
    """The refusal of a locally edited roles.yml: diff, override lines and the next step."""
    out = [
        "refused: roles.yml is locally edited; replacing it would drop your change at the next "
        "fresh launch, and keeping it would hide HACF's new defaults (R158)",
        "  diff -u live/roles.yml HEAD/roles.yml:",
        *diff_lines(live, src, "roles.yml"),
    ]
    edits, rest = roles_difference(src, live)  # a refused file never parses equal
    if edits:
        out += ["  the overrides.yml lines that carry it:", "    seats:"]
        out += [f"      {role}: {flow({'tools': edit})}" for role, edit in edits.items()]
    if rest:
        out.append(
            "  not expressible in overrides.yml (move them by hand, then use --take-hacf "
            f"roles.yml): {', '.join(rest)}"
        )
    if carried_by_overrides(live, edits):
        out.append("  next: overrides.yml already carries these; re-run with --take-hacf roles.yml")
    else:
        out.append(
            f"  next: put those lines in {live}/overrides.yml yourself, then re-run with "
            "--take-hacf roles.yml"
        )
    return out


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


def node_role(node: dict) -> str | None:
    """A node's HACF role: its `profile`, else the member id of its `logicalId`.

    The logicalId is used only when `profile` is absent or empty (`build.execute` -> `execute`,
    split on the last dot).
    """
    profile = node.get("profile")
    if isinstance(profile, str) and profile:
        return profile
    logical = node.get("logicalId")
    if isinstance(logical, str) and logical:
        return logical.rsplit(".", 1)[-1]
    return None


@contextlib.contextmanager
def resolution_dir(src: Path, live: Path | None):
    """A temp dir bin/seat-config.py resolves in.

    It holds the source defaults, presets and agent.yaml, plus read-only copies of the live
    overrides.yml and agents/hacf/local/. A symlink below local/ stays one, so the resolver refuses
    it. A kept, locally edited defaults file does not change the dir (R87).
    """
    with tempfile.TemporaryDirectory(prefix="seat-config-") as tmp:
        work = Path(tmp)
        for rel in (*DEFAULTS, AGENT):
            if (src / rel).is_file():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / rel, work / rel)
        origins = [src / "presets"] + ([live / "presets"] if live else [])  # a live preset wins
        for origin in origins:
            if origin.is_dir():
                shutil.copytree(origin, work / "presets", dirs_exist_ok=True)
        if live and (live / OVERRIDES).is_file():
            shutil.copyfile(live / OVERRIDES, work / OVERRIDES)  # read-only use, in a temp dir
        if live and (live / LOCAL).is_symlink():  # the resolver judges the link, as it would live
            (work / LOCAL).parent.mkdir(parents=True, exist_ok=True)
            (work / LOCAL).symlink_to((live / LOCAL).resolve())
        elif live and (live / LOCAL).is_dir():
            try:
                shutil.copytree(live / LOCAL, work / LOCAL, symlinks=True)
            except (OSError, shutil.Error) as exc:
                raise Refusal(f"cannot read {live / LOCAL} ({exc}); nothing written") from None
        yield work


def seats(rig: str, src: Path, live: Path | None = None) -> list[dict]:
    """Each seat of the rig: session, role, its status record, new rules and rollback rules."""
    # `profile` (the role) is in the record only with --full; without it every seat looked role-less
    nodes = rig_json("ps", "--nodes", "--rig", rig, "--full", "--json")
    if not isinstance(nodes, list):
        raise Refusal(f"rig ps --nodes --rig {rig} --full --json: unreadable; nothing written")
    roles = yaml.safe_load((src / "roles.yml").read_text())["roles"]
    out = []
    with resolution_dir(src, live) as work:
        for node in nodes:
            if not isinstance(node, dict) or node.get("rigName", rig) != rig:
                continue
            session, role = node.get("canonicalSessionName"), node_role(node)
            if not session:
                continue
            seat = {"session": session, "role": role, "live_known": "model" in node}
            for key in ("model", "effort", "effortSource"):  # kept as given (None when absent)
                seat[key] = node.get(key)
            if role not in roles:
                seat["skip"] = f"no role {role!r} in roles.yml"
                out.append(seat)
                continue
            record = rig_json("seat", "status", session, "--json")
            seat["runtime"] = node.get("runtime")
            rules = subprocess.run(
                [sys.executable, str(src / "bin" / "seat-rules.py"), "--roles",
                 str(work / "roles.yml"), "--role", role, "--json"],
                capture_output=True, text=True, timeout=60,
            )  # fmt: skip
            refused = [
                x for x in rules.stderr.splitlines() if x.startswith("seat-rules.py: seat-config")
            ]
            if rules.returncode == 2 and refused:  # never the unedited denies in their place
                why = refused[0].split("seat-config: ", 1)[-1]
                seat.update(record=record, rules=None, rules_why=why)
            elif rules.returncode != 0:
                raise Refusal(f"seat-rules.py --role {role}: exit {rules.returncode}")
            else:
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
        if s["rules"] is None:
            lines.append(f"    rules: manual (seat-config refused: {s['rules_why']})")
        else:
            lines.append(f"    rules: {json.dumps(s['rules'], separators=(',', ':'))}")
            rules = f"{where}/seats/{name}.json"
            lines.append(f'    rig seat set-permissions {name} --rules {rules} --reason "{REASON}"')
            lines.append(f"      effect: {NEXT_LAUNCH}")
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


# --- the model half (v0.6.6) ------------------------------------------------------------------


def probe_verbs() -> dict[str, str | None]:
    """Per `rig seat` verb: None if its `--help` works, else a `manual (...)` note."""
    ok = {}
    for verb in ("set-model", "set-effort", "set-runtime"):
        try:
            r = subprocess.run(
                ["rig", "seat", verb, "--help"], capture_output=True, text=True, timeout=60
            )
            # an unknown subcommand's --help still exits 0 (it prints `rig seat`'s own usage), so
            # the verb's own usage line must be there too
            ok[verb] = r.returncode == 0 and f"rig seat {verb}" in r.stdout
        except (OSError, subprocess.SubprocessError):
            ok[verb] = False
    if all(ok.values()):
        return {verb: None for verb in ok}
    try:
        r = subprocess.run(["rig", "--version"], capture_output=True, text=True, timeout=60)
        version = r.stdout.strip() if r.returncode == 0 else ""
        version = version or "unknown version"
    except (OSError, subprocess.SubprocessError):
        version = "unknown version"
    return {
        verb: None if ok[verb] else f"manual (rig seat {verb} not available on {version})"
        for verb in ok
    }


def skill_roles(work: Path) -> tuple[bool, set[str]]:
    """Return (whether any layer sets skills, the roles a layer adds skills to).

    The layers of a resolution dir are overrides.yml and the named preset.
    """
    paths = [work / OVERRIDES]
    try:
        name = yaml.safe_load((work / "seat-config.yml").read_text()).get("preset")
        if isinstance(name, str) and name and "/" not in name:
            paths.append(work / "presets" / f"{name}.yml")
    except (OSError, UnicodeDecodeError, yaml.YAMLError, AttributeError):
        pass
    found = [
        (role, value)
        for path in paths
        for (role, key), value in layer_values(path).items()
        if key == "skills"
    ]
    return bool(found), {role for role, v in found if isinstance(v, dict) and v.get("add")}


def loadouts(rig_seats: list[dict]) -> dict[str, list[str]]:
    """{role: [skill id]} from each seat's `rig skill loadout` (read-only); a failure adds none."""
    out: dict[str, list[str]] = {}
    for seat in rig_seats:
        if "skip" in seat:
            continue
        try:
            r = subprocess.run(
                ["rig", "skill", "loadout", "--seat", seat["session"], "--json"],
                capture_output=True, text=True, timeout=60,
            )  # fmt: skip
            if r.returncode != 0:
                continue
            ids = [skill["id"] for skill in json.loads(r.stdout)["skills"]]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            continue
        if all(isinstance(i, str) for i in ids):
            have = out.setdefault(seat["role"], [])
            have += [i for i in ids if i not in have]
    return out


def resolve_config(
    src: Path, live: Path, rig_seats: list[dict], project_root: str | None
) -> tuple[dict | None, str]:
    """Run bin/seat-config.py resolve on a resolution dir.

    Returns ({"seats": ..., "routing": ..., "warnings": [...]}, ""), or (None, reason) when it
    refuses.
    """
    with resolution_dir(src, live) as work:
        argv = [sys.executable, str(src / "bin" / "seat-config.py"), "resolve", "--dir",
                str(work), "--json"]  # fmt: skip
        if project_root is not None:
            argv += ["--project-root", str(Path(project_root).resolve())]
        any_skills, adding = skill_roles(work)
        if any_skills:
            known = loadouts(rig_seats)
            # the resolver refuses an unknown skill for a role a given loadout leaves out, so a
            # role it would judge without an answer means no loadout: unverified, warned (R162)
            if adding <= set(known):
                (work / "loadout.json").write_text(json.dumps(known))
                argv += ["--loadout", str(work / "loadout.json")]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            said = [
                x for x in r.stderr.strip().splitlines() if not x.startswith(("warning:", "note:"))
            ]
            return None, (said or [f"exit {r.returncode}"])[0]
        warnings = [x for x in r.stderr.splitlines() if x.startswith("warning:")]
        return {**json.loads(r.stdout), "warnings": warnings}, ""


def shown(value) -> str:
    """A live or resolved value as printed: None reads `unset (harness default)`."""
    return "unset (harness default)" if value is None else str(value)


def seat_model_block(seat: dict, row: dict, verbs: dict) -> list[str]:
    """One seat's model lines (R84).

    A key counts as different when its resolved value differs from the live one, or the live record
    has no `model` key at all (`live unknown`, which differs from anything).
    """
    name, role, unknown = seat["session"], seat["role"], not seat["live_known"]
    differs = [
        key
        for key in ("model", "effort")
        if key in row and (unknown or seat.get(key) != row[key]["value"])
    ]
    live_model = "live unknown" if unknown else shown(seat.get("model"))
    if "model" in differs:
        want = row["model"]
        head = f"  model {role} {name}: {live_model} -> {want['value']} (source: {want['source']})"
    else:
        head = f"  model {role} {name}: {live_model} (unchanged)"
    if not differs:
        return [head]
    out = [head]
    if "effort" in differs:
        want = row["effort"]
        live_effort = "live unknown" if unknown else shown(seat.get("effort"))
        out.append(
            f"  effort {role} {name}: {live_effort} -> {want['value']} (source: {want['source']})"
        )
    pad = "      "
    if "model" in differs:
        if verbs["set-model"]:
            out.append(f"{pad}model: {verbs['set-model']}")
        else:
            out.append(
                f"{pad}rig seat set-model {name} --model {row['model']['value']} "
                f'--reason "{MODEL_REASON}"'
            )
    if "effort" in differs:
        if verbs["set-effort"]:
            out.append(f"{pad}effort: {verbs['set-effort']}")
        else:
            out.append(
                f"{pad}rig seat set-effort {name} --effort {row['effort']['value']} "
                f'--reason "{MODEL_REASON}"'
            )
    launch = f"rig seat launch {name} --fresh --stop"
    out.append(f"{pad}{launch}   ({who_switches(name)})")
    if unknown:
        out.append(f"{pad}verify with: rig seat effort {name}")
    if "model" in differs and not verbs["set-model"]:
        out.append(
            f'{pad}rollback: rig seat set-model {name} --model {OPUS} --reason "hacf seat-config '
            'rollback", then the same launch line'
        )
    elif "effort" in differs and not verbs["set-effort"] and seat.get("effort"):
        out.append(
            f"{pad}rollback: rig seat set-effort {name} --effort {seat['effort']} "
            f'--reason "hacf seat-config rollback", then the same launch line'
        )
    return out


def default_values(root: Path) -> dict[tuple[str, str], object]:
    """(role, key) -> default value from root's seat-config.yml and caps.yml; {} when unreadable."""
    out: dict[tuple[str, str], object] = {}
    for rel in ("seat-config.yml", "caps.yml"):
        try:
            data = yaml.safe_load((root / rel).read_text())
        except (OSError, yaml.YAMLError):
            continue
        seats_ = data.get("seats") if isinstance(data, dict) else None
        for role, entry in (seats_ if isinstance(seats_, dict) else {}).items():
            for key, value in (entry if isinstance(entry, dict) else {}).items():
                out[(role, key)] = value
    return out


def layer_values(path: Path) -> dict[tuple[str, str], object]:
    """(role, key) -> value of an overrides or preset file's `seats:`; {} when absent or invalid."""
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    seats_ = data.get("seats") if isinstance(data, dict) else None
    return {
        (role, key): value
        for role, entry in (seats_ if isinstance(seats_, dict) else {}).items()
        for key, value in (entry if isinstance(entry, dict) else {}).items()
    }


def hidden_lines(src: Path, live: Path) -> list[str]:
    """R67: each new default (changed or new against the live files) an override hides."""
    new, old = default_values(src), default_values(live)
    try:
        preset = (yaml.safe_load((src / "seat-config.yml").read_text()) or {}).get("preset")
    except (OSError, yaml.YAMLError, AttributeError):
        preset = None
    layers = [(layer_values(live / OVERRIDES), OVERRIDES)]
    if isinstance(preset, str) and preset:
        layers.append((layer_values(live / "presets" / f"{preset}.yml"), f"presets/{preset}.yml"))
    out = []
    for (role, key), value in sorted(new.items()):
        if (role, key) in old and old[(role, key)] == value:
            continue  # this default did not change
        for values, name in layers:
            if (role, key) not in values:
                continue
            if values[(role, key)] == value:
                out.append(f"  override now equals the default: {role}.{key}")
            else:
                was = old[(role, key)] if (role, key) in old else "(none)"
                out.append(
                    f"  hidden by your override: {role}.{key} default {was} -> {value}; "
                    f"yours: {values[(role, key)]} ({name}); to adopt it, delete that line"
                )
            break
    return out


# --- the render of skills and instructions (R160) ---------------------------------------------


class Manual(Exception):
    """A shape the line edit does not recognise."""


def guidance(role: str) -> str:
    """The role's shipped guidance file, relative to the agent dir."""
    return f"guidance/{role}.md"


def child(lines: list[str], start: int, end: int, indent: int, key: str) -> tuple[int, int] | None:
    """(first, past-last) of the lines under the one `<indent>key:` line in lines[start:end]."""
    head = f"{' ' * indent}{key}:"
    found = [i for i in range(start, end) if lines[i].rstrip() == head]
    if len(found) != 1:
        return None
    stop = found[0] + 1
    while stop < end and (
        not lines[stop].strip() or len(lines[stop]) - len(lines[stop].lstrip(" ")) > indent
    ):
        stop += 1
    return found[0] + 1, stop


def block(lines: list[str], *path: str) -> tuple[int, int] | None:
    """The line range under a block-mapping path whose keys are indented by two per level."""
    span = (0, len(lines))
    for depth, key in enumerate(path):
        span = child(lines, *span, 2 * depth, key)
        if span is None:
            return None
    return span


def listed(doc, role: str, key: str) -> list | None:
    """The role's uses.skills, or its startup paths after the guidance file; None if absent."""
    try:
        profile = doc["profiles"][role]
        if key == "skills":
            skills = profile["uses"].get("skills") or []
            return list(skills) if isinstance(skills, list) else None
        paths = [entry["path"] for entry in profile["startup"]["files"]]
        return paths[paths.index(guidance(role)) + 1 :]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def unchanged(have: list | None, key: str, value: list) -> bool:
    """Whether nothing is to render: the same list, or no instructions and no place for any."""
    return have == value or (key == "instructions" and have is None and not value)


def edit_skills(lines: list[str], role: str, value: list[str]) -> list[str]:
    """Rewrite the role's one-line `skills: [...]` flow list."""
    where = f"profiles.{role}.uses.skills"
    span = block(lines, "profiles", role, "uses")
    found = (
        [i for i in range(*span) if re.match(r"      skills: \[[^\]]*\]\s*$", lines[i])]
        if span
        else []
    )
    if len(found) != 1:
        raise Manual(f"{where} is not a one-line flow list")
    new = list(lines)
    new[found[0]] = f"      skills: {flow(value)}\n"
    return new


def edit_instructions(lines: list[str], role: str, value: list[str]) -> list[str]:
    """Replace the entries after the role's guidance entry with one per instruction."""
    first = f"        - path: {guidance(role)}"
    where = f"profiles.{role}.startup.files"
    span = block(lines, "profiles", role, "startup", "files")
    found = [i for i in range(*span) if lines[i].rstrip() == first] if span else []
    if len(found) != 1:
        raise Manual(f"no {guidance(role)} entry under {where}")

    def past(at: int) -> int:
        """The line after the entry that starts at `at` (its keys are indented 10)."""
        at += 1
        while at < span[1] and lines[at].startswith("          "):
            at += 1
        return at

    start = end = past(found[0])
    while end < span[1] and lines[end].startswith("        - "):
        path = lines[end].strip().removeprefix("- path: ").strip("'\"")
        if not path.startswith("local/"):
            raise Manual(f"an entry after {guidance(role)} is not under local/: {path}")
        end = past(end)
    added = []
    for path in value:
        added += [
            f"        - path: {flow(path)}\n",
            "          delivery_hint: send_text\n",
            "          required: true\n",
        ]
    head = lines[:start]
    if head and not head[-1].endswith("\n"):
        head[-1] += "\n"
    return head + added + lines[end:]


def render_role(text: str, role: str, wanted: dict) -> str:
    """Set the role's lists in text, parse-checked; Manual (naming the key) if it cannot."""
    for key, edit in (("skills", edit_skills), ("instructions", edit_instructions)):
        if key not in wanted:
            continue
        value = wanted[key]
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            raise Manual(f"{key}|{AGENT} is not valid YAML") from None
        if unchanged(listed(doc, role, key), key, value):
            continue
        try:
            new = "".join(edit(text.splitlines(keepends=True), role, value))
            expected = copy.deepcopy(doc)
            if key == "skills":
                expected["profiles"][role]["uses"]["skills"] = value
            else:
                files = expected["profiles"][role]["startup"]["files"]
                at = [entry["path"] for entry in files].index(guidance(role))
                files[at + 1 :] = [
                    {"path": p, "delivery_hint": "send_text", "required": True} for p in value
                ]
            if yaml.safe_load(new) != expected:
                raise Manual("the line edit changed more than the list")
        except Manual as exc:
            raise Manual(f"{key}|{exc}") from None
        except (KeyError, TypeError, ValueError, yaml.YAMLError):
            raise Manual(f"{key}|profiles.{role} has no recognised {key} list") from None
        text = new
    return text


def render_agent(src: Path, live: Path, plan: list[dict], resolved: dict | None) -> dict:
    """Plan the agent.yaml write and update its plan entry in place.

    Returns {"data": bytes to write or None, "marker": seat-config.rendered.json's bytes or None,
    "manual": {role: (key, reason)}}.
    """
    out: dict = {"data": None, "marker": None, "manual": {}}
    found = [e for e in plan if e["rel"] == AGENT]
    if not found:
        return out
    e = found[0]
    if resolved is None:
        if e.get("rendered"):  # never drop an earlier render on a config nobody can resolve
            e["action"], e["held"] = "keep", True
        return out
    wanted = {
        role: {k: row[k]["value"] for k in ("skills", "instructions") if k in row}
        for role, row in resolved.items()
    }
    if e["action"] == "symlink":
        out["manual"] = {
            role: ("skills", f"{AGENT} is a symlink")
            for role, lists in wanted.items()
            if any(listed(yaml.safe_load((src / AGENT).read_text()), role, k) != v
                   for k, v in lists.items())
        }  # fmt: skip
        return out
    from_source = e["action"] in ("add", "replace", "take")
    base = ((src if from_source else live) / AGENT).read_bytes()
    try:
        text = base.decode()
    except UnicodeDecodeError:
        out["manual"] = {role: ("skills", f"{AGENT} is not UTF-8") for role in wanted}
        return out
    for role, lists in wanted.items():
        try:
            text = render_role(text, role, lists)
        except Manual as exc:
            out["manual"][role] = tuple(str(exc).split("|", 1))
    data = text.encode()
    target = live / AGENT
    if target.is_file() and target.read_bytes() == data:
        if e["action"] in ("replace", "take"):
            e["action"] = "same"
        return out
    if data != base:
        e["render"] = True
    elif not from_source:
        return out
    out["data"] = data
    if data != base or (live / RENDERED).is_file():
        record = {AGENT: {"rendered": blob_id(data), "base": blob_id(base)}}
        out["marker"] = (json.dumps(record, indent=2) + "\n").encode()
    return out


# --- the key lines (R160, R161) ---------------------------------------------------------------


def bracketed(items) -> str:
    """A list as printed in a key line."""
    return f"[{', '.join(str(i) for i in items)}]"


def who_switches(name: str) -> str:
    """Who runs a seat's launch or switch, and when."""
    if is_lead(name):
        return "planned handover by the operator; not by the lead itself"
    return "at an idle point, never mid-MiniPRD"


def live_agent(src: Path, live: Path):
    """The live agent.yaml parsed (the source's when apply would add it); None if unreadable."""
    path = live / AGENT if (live / AGENT).exists() else src / AGENT
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def seat_key_lines(seat: dict, row: dict, doc, manual: dict, verbs: dict) -> list[str]:
    """One seat's skills, instructions and appliable harness lines."""
    name, role, out = seat["session"], seat["role"], []
    for key, field in (("skills", "uses.skills"), ("instructions", "startup.files")):
        if key not in row or role in manual:
            continue
        value, have = row[key]["value"], listed(doc, role, key)
        if unchanged(have, key, value):
            continue
        shown_have = "unknown" if have is None else bracketed(have)
        out.append(
            f"  {key} {role} {name}: {shown_have} -> {bracketed(value)} (source: "
            f"{row[key]['source']}); rendered into {AGENT} profiles.{role}.{field} at apply; "
            f"{NEXT_LAUNCH}"
        )
    cell = row.get("harness")
    if not cell or "deferred" in cell:
        return out
    runtime, value = seat.get("runtime"), cell["value"]
    # a record with no runtime says nothing about the default; a harness someone set is shown
    if runtime == value or (runtime is None and cell["source"] == "default"):
        return out
    out.append(
        f"  harness {role} {name}: {runtime or 'live unknown'} -> {value} "
        f"(source: {cell['source']})"
    )
    if verbs["set-runtime"]:
        out.append(f"      harness: {verbs['set-runtime']}")
        return out
    out.append(
        f'      rig seat set-runtime {name} --runtime {value} --reason "{RUNTIME_REASON}"   '
        f"(successor-first switch; {who_switches(name)})"
    )
    out.append("      effect: takes effect when the successor confirms the handover")
    return out


def key_lines(src, live, rig_seats, config, why, rendered, verbs) -> list[str]:
    """The keys section: per seat in the lead-last order, then deferrals' leftovers and routing."""
    out = ["keys (the operator runs these per seat; this script never does):"]
    if config is None:
        out.append(f"  keys: manual (seat-config resolve refused: {why})")
        return out
    out += [f"  {line}" for line in config["warnings"]]
    doc, manual = live_agent(src, live), rendered["manual"]
    rows = config["seats"]
    order = [s["role"] for s in rig_seats if "skip" not in s]
    order += [role for role in rows if role not in order]  # a role with no live seat
    for role in dict.fromkeys(order):
        row = rows.get(role, {})
        if role in manual:
            out.append(f"  {manual[role][0]} {role}: manual ({manual[role][1]})")
        for seat in rig_seats:
            if "skip" not in seat and seat["role"] == role:
                out += seat_key_lines(seat, row, doc, manual, verbs)
        if "deferred" in row.get("harness", {}):
            out.append(f"  {row['harness']['deferred']}")
    out.append(
        "routing: read live by each seat through bin/seat-config.py resolve --section routing; "
        "no apply step"
    )
    return out


def override_lines(src: Path, live: Path, resolved: dict) -> list[str]:
    """US-004: default vs override for each new key and routing key overrides.yml sets."""
    try:
        mine = yaml.safe_load((live / OVERRIDES).read_text()) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    roles = yaml.safe_load((src / "roles.yml").read_text())["roles"]
    agent = yaml.safe_load((src / AGENT).read_text())
    config = yaml.safe_load((src / "seat-config.yml").read_text()) or {}

    def show(key: str, value) -> str:
        """A value of a key as printed."""
        if key == "tools":
            return f"{len(value)} denies"
        if key in ("skills", "instructions"):
            return bracketed(value)
        return str(value)

    out = []
    seats_ = mine.get("seats") or {}
    for role in sorted(seats_):
        for key in NEW_KEYS:
            if not isinstance(seats_[role], dict) or key not in seats_[role]:
                continue
            edit, cell = seats_[role][key], resolved["seats"][role].get(key)
            if cell is None:
                continue
            value = cell["value"]["deny"] if key == "tools" else cell["value"]
            if key == "tools":
                default = roles[role]["tools_deny"]
            elif key == "skills":
                default = listed(agent, role, "skills") or []
            elif key == "harness":
                default = agent["profiles"][role]["preferences"]["runtime"]
            else:
                default = []
            yours = flow(edit) if isinstance(edit, (dict, list)) else str(edit)
            out.append(
                f"  default vs override: {role}.{key} default {show(key, default)}; yours: "
                f"{yours} ({OVERRIDES}) -> resolved {show(key, value)}"
            )
            if key not in EDITS:
                continue
            remove, add = EDITS[key]
            for name, items in ((remove, edit.get(remove) or []), (add, edit.get(add) or [])):
                for item in items:
                    if (item in default) == (name == add):
                        out.append(f"  override now equals the default: {role}.{key}.{name} {item}")
    for key, cell in resolved["routing"].items():
        node, base = mine.get("routing"), config.get("routing")
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            base = base.get(part) if isinstance(base, dict) else None
        if node is None:
            continue
        if base is None and key.endswith(".destination"):
            base = {"rule": "discover"}
        out.append(
            f"  default vs override: routing.{key} default "
            f"{'(none)' if base is None else flow(base)}; yours: {flow(node)} ({OVERRIDES}) -> "
            f"resolved {flow(cell['value'])}"
        )
    return out


def model_plan(src, live, rig_seats, verbs, project_root=None, plan=()) -> tuple[list[str], dict]:
    """The hidden-default, model and key sections of the plan, and the agent.yaml render.

    Like the seat half's rules, the sections are the operator's step and never turn plan's exit
    code to 1 (it reports file and rig.yaml changes only; a render that changes agent.yaml is one).
    """
    lines = []
    config, why = resolve_config(src, live, rig_seats, project_root)
    resolved = None if config is None else config["seats"]
    rendered = render_agent(src, live, plan, resolved)
    hidden = hidden_lines(src, live)
    if config is not None:
        hidden += override_lines(src, live, config)
    if hidden:
        lines += ["defaults hidden by your overrides (overrides.yml is never changed):", *hidden]
    lines.append(
        "models (live state from rig ps --nodes --full; the operator runs these per seat, the "
        "lead last; this script never does):"
    )
    if resolved is None:
        lines.append(f"  model: manual (seat-config resolve refused: {why})")
    else:
        for seat in rig_seats:
            if "skip" not in seat:
                lines += seat_model_block(seat, resolved.get(seat["role"], {}), verbs)
    lines += key_lines(src, live, rig_seats, config, why, rendered, verbs)
    return lines, rendered


# --- plan text --------------------------------------------------------------------------------


def proposed_lines(live: Path, src: Path, rel: str) -> list[str]:
    """For a kept caps.yml or seat-config.yml: each differing role key as a proposed override."""
    if rel not in ("caps.yml", "seat-config.yml"):
        return []
    mine: dict = {}
    theirs: dict = {}
    try:
        old = yaml.safe_load((live / rel).read_text())
        new = yaml.safe_load((src / rel).read_text())
    except (OSError, yaml.YAMLError):
        return []
    for data, out in ((old, mine), (new, theirs)):
        seats_ = data.get("seats") if isinstance(data, dict) else None
        for role, entry in (seats_ if isinstance(seats_, dict) else {}).items():
            for key, value in (entry if isinstance(entry, dict) else {}).items():
                out[(role, key)] = value
    return [
        f"    proposed override: {role}: {{{key}: {value}}}"
        for (role, key), value in sorted(mine.items())
        if theirs.get((role, key)) != value
    ]


def header(src: Path, live: Path, rig: str) -> str:
    """The plan's first line (restore reads the rig and the live spec back from it)."""
    label = source_label(src.parents[1])
    return f"seat-config-upgrade plan: rig {rig}; live spec {live}; {label}"


def plan_text(src, live, rig, plan, rig_note, rig_seats, where, extra) -> tuple[list[str], bool]:
    """The plan's lines and whether anything changes."""
    lines = [header(src, live, rig)]
    changes = False
    for e in plan:
        a, rel = e["action"], e["rel"]
        if a in ("add", "replace", "take"):
            changes = True
            why = {"add": "add", "replace": "replace", "take": "replace (--take-hacf)"}[a]
            if a == "replace" and e["set"] == "adapted":
                why = "replace (unmodified HACF text)"
            elif a == "replace" and e.get("text_only"):
                why = "replace (text only; no setting differs from HEAD)"
            lines.append(f"  {why}: {rel}")
            if e.get("text_only"):
                lines += diff_lines(live, src, rel)
        elif a == "keep" and e["set"] == "defaults":
            lines.append(
                "  keep (locally edited defaults; move your changes to overrides.yml, then "
                f"--take-hacf {rel}): {rel}"
            )
            lines += proposed_lines(live, src, rel)
        elif a == "keep" and e.get("held"):
            lines.append(
                f"  keep (an earlier apply rendered it; the seat config is refused): {rel}"
            )
        elif a == "keep":
            lines.append(f"  keep (locally edited; --take-hacf {rel} replaces it): {rel}")
            lines += diff_lines(live, src, rel)
        elif a == "live-only":
            lines.append(f"  keep (only in the live spec): {rel}")
        elif a == "symlink":
            lines.append(f"  keep (a symlink; left alone): {rel}")
        if e.get("render"):
            changes = True
            lines.append(f"  render: {rel} (resolved skills and instructions)")
    if rig_note[0] is None:
        changes = True  # a manual edit is still owed
        lines.append(f"rig.yaml: {rig_note[1]}")
    else:
        if rig_note[1] != "unchanged":
            changes = True
        lines.append(f"rig.yaml: {rig_note[1]} env.{ENV_KEY}: {ENV_VALUE}")
    lines += seat_lines(rig_seats, where) + extra + host_lines(rig)
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
        if users(rel):
            continue  # the user's files are never checksummed (R85)
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
        if not users(rel) and (path.is_file() or path.is_symlink()):
            path.unlink()
    for rel in files_under(spec):
        if users(rel):
            continue  # never written, removed or restored (R85)
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
            mine = users(path.relative_to(live).as_posix())
            if not mine and not path.is_symlink() and not kept and not any(path.iterdir()):
                path.rmdir()
    recorded = (backup / "SHA256SUMS").read_text().splitlines(keepends=True)
    expected = "".join(x for x in recorded if not users(x.rstrip("\n").partition("  ")[2]))
    if sha256sums(live) != expected:
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
        if not (adapted(rel) or rel in DEFAULTS) or not (src / rel).is_file():
            raise Usage(
                f"--take-hacf {rel}: not a file of the adapted or defaults sets in the source"
            )
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
    if any(e["action"] == "refuse" for e in plan):  # R158: only the header and the refusal
        print("\n".join([header(src, live, ns.rig), *refusal_lines(src, live)]))
        return 1
    rig_seats = seats(ns.rig, src, live)
    extra, _ = model_plan(src, live, rig_seats, probe_verbs(), ns.project_root, plan)
    lines, changes = plan_text(src, live, ns.rig, plan, rig_note, rig_seats, "<backup>", extra)
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


def write_backup(
    backup, live, src, rig, plan, rig_note, rig_seats, extra, marker=None
) -> list[str]:
    """Write the whole backup (spec, SHA256SUMS, seats, plan.txt, added.json); the plan lines."""
    backup.mkdir(parents=True)
    live_root = live.resolve()

    def skip_users(directory: str, names: list[str]) -> list[str]:
        """The live spec's overrides.yml and agents/hacf/local stay out of the backup (R85)."""
        here = Path(directory).resolve()
        if here != live_root and live_root not in here.parents:
            return []
        return [n for n in names if users((here / n).relative_to(live_root).as_posix())]

    shutil.copytree(live, backup / "spec", symlinks=True, ignore=skip_users)
    (backup / "SHA256SUMS").write_text(sha256sums(backup / "spec"))
    (backup / "seats").mkdir()
    for s in rig_seats:
        if "skip" in s:
            continue
        name = s["session"]
        (backup / "seats" / f"{name}.before.json").write_text(json.dumps(s["record"], indent=2))
        (backup / "seats" / f"{name}.rollback.json").write_text(json.dumps(s["rollback"]))
        if s["rules"] is not None:
            (backup / "seats" / f"{name}.json").write_text(json.dumps(s["rules"]))
    order = [s["session"] for s in rig_seats if "skip" not in s]  # the lead role last
    (backup / "seats" / ORDER).write_text(json.dumps(order))
    lines, _ = plan_text(src, live, rig, plan, rig_note, rig_seats, str(backup), extra)
    (backup / "plan.txt").write_text("\n".join(lines) + "\n")
    added = [e["rel"] for e in plan if e["action"] == "add"]
    if marker is not None and not (live / RENDERED).exists():
        added.append(RENDERED)
    (backup / MANIFEST).write_text(json.dumps(added))
    return lines


def write_live(live, src, plan, rig_text, rig_note, rendered) -> subprocess.CompletedProcess:
    """Write the planned files and the rig.yaml edit atomically, then run rig spec validate.

    agent.yaml is written with its render, and the render's marker beside it.
    """
    for e in plan:
        rel = e["rel"]
        data = rendered["data"] if rel == AGENT else None
        if data is None and e["action"] in ("add", "replace", "take"):
            data = (src / rel).read_bytes()
        if data is not None:
            atomic_write(live / rel, data, mode_for(rel, live / rel, src / rel))
    if rendered["marker"] is not None:
        atomic_write(live / RENDERED, rendered["marker"], 0o644)
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
    if any(e["action"] == "refuse" for e in plan):
        raise Refusal("\n".join(refusal_lines(src, live)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path.home() / ".hacf" / ns.rig / "backups" / f"seat-config-{stamp}"
    check_backup_place(backup, live)
    rig_seats = seats(ns.rig, src, live)
    if any(s.get("rollback") is None and "skip" not in s for s in rig_seats):
        raise Refusal("a seat's status record is unreadable, so its rollback is unknown; refused")
    extra, rendered = model_plan(src, live, rig_seats, probe_verbs(), ns.project_root, plan)
    held = lock(ns.rig)
    saved = {s: signal.signal(s, _raise_interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            lines = write_backup(
                backup, live, src, ns.rig, plan, rig_note, rig_seats, extra, rendered["marker"]
            )
            (backup / MARKER).write_text(f"apply started {stamp}\n")
        except BaseException as exc:
            print(f"apply failed ({reason(exc)}) before any live write; the live spec is unchanged")
            return 1
        try:
            write_live(live, src, plan, rig_text, rig_note, rendered)
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
        p.add_argument("--project-root", metavar="DIR", help="the project whose skills count")
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
