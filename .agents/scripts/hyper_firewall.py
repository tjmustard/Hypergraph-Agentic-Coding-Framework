#!/usr/bin/env python3
"""hyper_firewall.py — render .agentignore into the enforcement each tool honors.

Normative design: the firewall section of HACF's v0.6.0 design notes (not shipped) (R6, R7, R8,
R26). The repository is the git work tree of the working directory. This is not a security
boundary: see the limits the report prints.

CLI:
    render [--ides a,b ...] [--dry-run] [--written-sha SHA ...]   default ids: claude
    check
    merge-settings <fragment.json> [--written-sha SHA ...]

Exit codes: 0 ok, 1 drift or findings, 2 usage, 5 refused with nothing written.

Semantics:
- Patterns above the `# Standard dev ignores` comment in .agentignore get Read and Edit denies;
  patterns below it get Read denies. `.git/` stays root-anchored, as subsystems.md lists it.
- render --ides claude merges the rendered denies, the gated-verb ask rules and the gate hook into
  .claude/settings.json. roo, cursor, windsurf and antigravity get a best-effort managed block in
  their ignore file. cline, universal, copilot and zed get a report line only. Every target is
  computed before the first write, so a refusal writes nothing.
- A merge never replaces a user key or rule. Invalid JSON (duplicate keys included) refuses. It
  adds what is missing, and removes only entries that .agents/hacf-manifest.json owns and the
  fragment no longer lists, in the permission buckets and hook events the fragment names, so that
  render and the installer's fragment never remove each other's rules. Without a manifest nothing
  is removed. .claude/settings.local.json is never written.
- The manifest's ownership keys, and the `owned:` JSON line that render and merge-settings print
  for the installer to union and record, have the shape
  {"settings_rules": {"allow|ask|deny": [rule, ..]}, "hooks": {"<event>": [command, ..]}}.
  An entry the user already had is not claimed.
- check exits 1 when a merge or a block render would change anything, and warns about broad allows
  in both settings files. A project without .claude/ has no Claude target to drift.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _runtime
import hyper_lore
import hyper_openrig

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5

AGENTIGNORE = ".agentignore"
SETTINGS_REL = ".claude/settings.json"
LOCAL_SETTINGS_REL = ".claude/settings.local.json"
MANIFEST_REL = ".agents/hacf-manifest.json"
BACKUP_REL = ".agents/.backup"
READ_ONLY_SECTION = "# Standard dev ignores"
ROOT_ANCHORED = {".git/": "/.git/**"}
FIXED_DENIES = (
    "Edit(/spec/compiled/architecture.yml)",
    "Edit(/lore_hypergraph.yml)",
    "Edit(/lore/CANON.md)",
    "Edit(/lore/canon/**)",
    "Edit(/missions/*/mission.yaml)",
    "Edit(/missions/*/slices/*/slice.yaml)",
)
GATED_VERBS = ("approve", "reject", "retire", "reopen", "review")
HOOK_COMMAND = 'python3 "${CLAUDE_PROJECT_DIR}/.agents/scripts/hyper_gate_hook.py"'
BLOCK_ID = "agentignore"
BEST_EFFORT_LABEL = "# Best-effort, rendered from .agentignore by HACF; not a security boundary."
IGNORE_FILES = {
    "roo": (".rooignore", "Roo's tools honor it; shell commands do not"),
    "cursor": (".cursorignore", "best-effort"),
    "windsurf": (".codeiumignore", "an indexing filter, UNVERIFIED"),
    "antigravity": (".geminiignore", "a context filter; Antigravity's own mechanism is UNVERIFIED"),
}
REPORT_ONLY = {
    "cline": "Cline: nothing rendered (.clineignore is deprecated); see Cline's Block Ignored "
    "File Access plugin, which uses .gitignore and is not a boundary.",
    "universal": "Copilot and Zed: the AGENTS.md mandate only.",
    "copilot": "Copilot: the AGENTS.md mandate only.",
    "zed": "Zed: the AGENTS.md mandate only.",
}
KNOWN_IDES = ("claude", *IGNORE_FILES, *REPORT_ONLY)
BROAD_ALLOWS = ("Bash", "Bash(*)", "Bash(git *)", "Bash(python3 *)", "Bash(python *)")
LIMITS = (
    "Limits: these rules are not a security boundary. They do not cover Python or Node "
    "subprocesses that open files themselves, or commands that read files without naming them "
    "(grep -r from a parent directory). Read and Edit tool denies held under bypassPermissions in "
    "the S11 live test (Claude Code 2.1.316); a later Claude Code may differ.",
    "Lore gate bypasses that remain: a deliberately forged ticket, a gated verb run from a "
    "written script in a TTY the agent controls, a direct Python write to lore_hypergraph.yml, "
    "and a ticket left live for up to 120 s after a declined prompt (the hook runs before the "
    "prompt).",
)


class Refused(Exception):
    """A target cannot be changed safely; nothing was written (exit 5)."""


def _read(path: Path) -> str | None:
    """Return a file's exact text, or None when absent; refuse a symlink or undecodable file."""
    if path.is_symlink():
        raise Refused(f"{path} is a symlink")
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"{path} cannot be read as UTF-8 text ({exc})") from exc


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


def _load_object(text: str, where: Path | str) -> dict:
    """Parse a JSON object; a duplicate key refuses too, because rewriting would drop one."""
    try:
        data = json.loads(text, object_pairs_hook=_unique_keys)
    except ValueError as exc:
        raise Refused(f"{where} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise Refused(f"{where} is not a JSON object")
    return data


def parse_agentignore(text: str) -> tuple[list[tuple[str, bool]], list[str], list[str]]:
    """Return ((pattern, also Edit) pairs, every pattern line verbatim, warnings)."""
    pairs: list[tuple[str, bool]] = []
    rows: list[str] = []
    warnings: list[str] = []
    also_edit = True
    for line in text.lstrip("﻿").splitlines():
        pattern = line.strip()
        if pattern.startswith(READ_ONLY_SECTION):
            also_edit = False
        if not pattern or pattern.startswith("#"):
            continue
        rows.append(pattern)
        if pattern.startswith("!"):
            warnings.append(f"{AGENTIGNORE}: negation {pattern} is skipped in the Claude rules")
        else:
            pairs.append((pattern, also_edit))
    return pairs, rows, warnings


def translate(pattern: str) -> str:
    """Translate one gitignore pattern to a settings-root-relative rule path."""
    if pattern in ROOT_ANCHORED:
        return ROOT_ANCHORED[pattern]
    if pattern.startswith("/"):
        path = pattern
    elif "/" in pattern.rstrip("/"):
        path = f"/{pattern}"
    else:
        path = f"/**/{pattern}"
    return f"{path}**" if pattern.endswith("/") else path


def render_claude(text: str) -> dict:
    """Return the settings fragment for a .agentignore text."""
    pairs, _, _ = parse_agentignore(text)
    deny: list[str] = []
    for pattern, also_edit in pairs:
        path = translate(pattern)
        deny.append(f"Read({path})")
        if also_edit:
            deny.append(f"Edit({path})")
    ask = [
        f"Bash({python} .agents/scripts/hyper_lore.py {verb} *)"
        for verb in GATED_VERBS
        for python in ("python3", "python")
    ]
    hook = {"type": "command", "command": HOOK_COMMAND}
    # A pattern listed twice (e.g. above and inside the HACF managed block) renders once;
    # dict.fromkeys keeps the first occurrence and the order of first occurrences.
    return {
        "permissions": {
            "deny": list(dict.fromkeys([*deny, *FIXED_DENIES])),
            "ask": list(dict.fromkeys(ask)),
        },
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]},
    }


def _dict_at(mapping: dict, key: str, where: str) -> dict:
    value = mapping.setdefault(key, {})
    if not isinstance(value, dict):
        raise Refused(f"{where} is not an object")
    return value


def _list_at(mapping: dict, key: str, where: str) -> list:
    value = mapping.setdefault(key, [])
    if not isinstance(value, list):
        raise Refused(f"{where} is not a list")
    return value


def _add_missing(target: dict, source: dict) -> None:
    """Add source's keys to target recursively; an existing value always wins."""
    for key, value in source.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _add_missing(target[key], value)


def _commands(entry: Any) -> list[str]:
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return []
    return [hook["command"] for hook in hooks if isinstance(hook, dict) and "command" in hook]


def _merge_hooks(current: list, entries: list, owned: list) -> list[str]:
    """Merge one event's fragment entries into current; return the commands to own afterwards."""
    wanted = [command for entry in entries for command in _commands(entry)]
    stale = set(owned) - set(wanted)
    for entry in list(current):
        commands = _commands(entry)
        if stale & set(commands):
            entry["hooks"] = [
                hook
                for hook in entry["hooks"]
                if not (isinstance(hook, dict) and hook.get("command") in stale)
            ]
            if not entry["hooks"]:
                current.remove(entry)
    added: list[str] = []
    for entry in entries:
        have = {
            command
            for other in current
            if isinstance(other, dict) and other.get("matcher") == entry.get("matcher")
            for command in _commands(other)
        }
        missing = [hook for hook in entry.get("hooks", []) if hook.get("command") not in have]
        if missing:
            current.append({**copy.deepcopy(entry), "hooks": copy.deepcopy(missing)})
            added += [hook.get("command") for hook in missing]
    return [command for command in wanted if command in owned or command in added]


def merge(settings: dict, fragment: dict, manifest: dict) -> tuple[dict, dict]:
    """Return (merged settings, owned set); see the module docstring for the rules."""
    merged = copy.deepcopy(settings)
    owned_rules = manifest.get("settings_rules") or {}
    owned_hooks = manifest.get("hooks") or {}
    if not isinstance(owned_rules, dict) or not isinstance(owned_hooks, dict):
        raise Refused(f"{MANIFEST_REL}: settings_rules and hooks must be objects")
    new_rules = copy.deepcopy(owned_rules)
    new_hooks = copy.deepcopy(owned_hooks)
    _add_missing(merged, {k: v for k, v in fragment.items() if k not in ("permissions", "hooks")})
    for bucket, rules in fragment.get("permissions", {}).items():
        permissions = _dict_at(merged, "permissions", "permissions")
        if not isinstance(rules, list):
            _add_missing(permissions, {bucket: rules})
            continue
        current = _list_at(permissions, bucket, f"permissions.{bucket}")
        owned = owned_rules.get(bucket) or []
        current[:] = [rule for rule in current if rule in rules or rule not in owned]
        added = [rule for rule in rules if rule not in current]
        current.extend(added)
        new_rules[bucket] = [rule for rule in rules if rule in owned or rule in added]
    for event, entries in fragment.get("hooks", {}).items():
        hooks = _dict_at(merged, "hooks", "hooks")
        current = _list_at(hooks, event, f"hooks.{event}")
        new_hooks[event] = _merge_hooks(current, entries, owned_hooks.get(event) or [])
    return merged, {"settings_rules": new_rules, "hooks": new_hooks}


def _dump(settings: dict) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def _plan_settings(top: Path, fragment: dict) -> tuple[Path, str | None, str, dict]:
    """Return (settings path, old text, new text, owned set) for one fragment."""
    path = top / SETTINGS_REL
    old = _read(path)
    settings = _load_object(old, SETTINGS_REL) if old is not None else {}
    manifest_text = _read(top / MANIFEST_REL)
    manifest = _load_object(manifest_text, MANIFEST_REL) if manifest_text is not None else {}
    merged, owned = merge(settings, fragment, manifest)
    new = old if merged == settings and old is not None else _dump(merged)
    return path, old, new, owned


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup(top: Path, path: Path) -> None:
    """Copy path under .agents/.backup/<timestamp>/ without overwriting an earlier backup."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rel = path.relative_to(top)
    for suffix in itertools.chain([""], (f"-{n}" for n in itertools.count(1))):
        target = top / BACKUP_REL / f"{stamp}{suffix}" / rel
        if not target.exists():
            break
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _apply(
    top: Path,
    plan: list[tuple[Path, str | None, str]],
    dry_run: bool,
    written_shas: tuple[str, ...] = (),
) -> None:
    """Write every changed target atomically, after a backup of each existing one.

    KI-6: the only backup skipped is one of .claude/settings.json whose current bytes hash to
    exactly a --written-sha value, i.e. bytes the installer's own earlier write put there.
    """
    for path, old, new in plan:
        rel = path.relative_to(top)
        if new == old:
            print(f"no change: {rel}")
        elif dry_run:
            print(f"would write: {rel}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if old is not None:
                if rel.as_posix() == SETTINGS_REL and _sha256(path) in written_shas:
                    print(f"no backup: {rel} (this run wrote these bytes)")
                else:
                    _backup(top, path)
            try:
                hyper_openrig.cas_write(path, old, new)
            except hyper_openrig.Refused as exc:
                raise Refused(str(exc)) from exc
            print(f"wrote: {rel}")


def _agentignore(top: Path) -> str:
    text = _read(top / AGENTIGNORE)
    if text is None:
        raise Refused(f"{AGENTIGNORE} does not exist")
    return text


def _block_rows(rows: list[str]) -> list[str]:
    return [BEST_EFFORT_LABEL, *rows]


def _print_owned(owned: dict) -> None:
    print(f"owned: {json.dumps(owned, sort_keys=True)}")


def cmd_render(
    top: Path, ides: list[str], dry_run: bool, written_shas: tuple[str, ...] = ()
) -> int:
    """Render the selected targets; refuse with nothing written if any one cannot be written."""
    text = _agentignore(top)
    _, rows, warnings = parse_agentignore(text)
    plan: list[tuple[Path, str | None, str]] = []
    owned = None
    if "claude" in ides:
        path, old, new, owned = _plan_settings(top, render_claude(text))
        plan.append((path, old, new))
    for ide in ides:
        if ide in IGNORE_FILES:
            path = top / IGNORE_FILES[ide][0]
            old = _read(path)
            try:
                new = hyper_lore.set_hash_block(old or "", BLOCK_ID, _block_rows(rows))
            except hyper_lore.Conflict as exc:
                raise Refused(f"{path.name}: {exc}") from exc
            plan.append((path, old, new))
    _apply(top, plan, dry_run, written_shas)
    for warning in warnings:
        print(f"warning: {warning}")
    if "claude" in ides:
        print(f"Claude Code: {SETTINGS_REL} (rules enforced by Claude Code)")
    for ide in ides:
        if ide in IGNORE_FILES:
            name, label = IGNORE_FILES[ide]
            print(f"{ide}: {name} (best-effort; {label})")
        elif ide in REPORT_ONLY:
            print(REPORT_ONLY[ide])
    for line in LIMITS:
        print(line)
    if owned is not None:
        _print_owned(owned)
    return EXIT_OK


def cmd_merge_settings(top: Path, fragment_path: str, written_shas: tuple[str, ...] = ()) -> int:
    """Merge a fragment file into .claude/settings.json."""
    text = _read(Path(fragment_path))
    if text is None:
        raise Refused(f"{fragment_path} does not exist")
    fragment = _load_object(text, fragment_path)
    for key in ("permissions", "hooks"):
        if not isinstance(fragment.get(key, {}), dict):
            raise Refused(f"{fragment_path}: {key} is not an object")
    for entries in fragment.get("hooks", {}).values():
        if not isinstance(entries, list) or not all(_commands(entry) for entry in entries):
            raise Refused(f"{fragment_path}: a hook entry has no command")
    path, old, new, owned = _plan_settings(top, fragment)
    _apply(top, [(path, old, new)], dry_run=False, written_shas=written_shas)
    _print_owned(owned)
    return EXIT_OK


def _broad_allow_warnings(top: Path) -> list[str]:
    """Return a warning per broad allow in either settings file (the local one is read-only)."""
    warnings: list[str] = []
    for rel in (SETTINGS_REL, LOCAL_SETTINGS_REL):
        try:
            text = _read(top / rel)
            data = _load_object(text, rel) if text is not None else {}
        except Refused as exc:
            warnings.append(str(exc))
            continue
        permissions = data.get("permissions")
        allow = permissions.get("allow") if isinstance(permissions, dict) else None
        for rule in allow if isinstance(allow, list) else []:
            if rule in BROAD_ALLOWS:
                lines = text.splitlines()
                number = next((n for n, line in enumerate(lines, 1) if f'"{rule}"' in line), 0)
                warnings.append(f"{rule} in {rel}:{number} shadows firewall or gate intent")
    return warnings


def cmd_check(top: Path) -> int:
    """Report drift between .agentignore and what is rendered; warn about broad allows."""
    findings: list[str] = []
    try:
        text = _agentignore(top)
    except Refused as exc:
        print(f"drift: {exc}")
        return EXIT_FINDINGS
    _, rows, warnings = parse_agentignore(text)
    if (top / ".claude").is_dir():
        try:
            _, old, new, _ = _plan_settings(top, render_claude(text))
            if new != old:
                findings.append(f"{SETTINGS_REL} differs from the rendered rules")
        except Refused as exc:
            findings.append(str(exc))
    for name, _ in IGNORE_FILES.values():
        try:
            old = _read(top / name)
            if old is not None and f"HACF MANAGED BLOCK: {BLOCK_ID}" in old:
                if hyper_lore.set_hash_block(old, BLOCK_ID, _block_rows(rows)) != old:
                    findings.append(f"{name} differs from the rendered block")
        except (Refused, hyper_lore.Conflict) as exc:
            findings.append(f"{name}: {exc}")
    for warning in [*warnings, *_broad_allow_warnings(top)]:
        print(f"warning: {warning}")
    for finding in findings:
        print(f"drift: {finding}")
    if findings:
        print("Run `python3 .agents/scripts/hyper_firewall.py render` for the IDEs you selected.")
        return EXIT_FINDINGS
    print("check: ok")
    return EXIT_OK


def _parse_ides(values: list[str]) -> list[str]:
    ides = [ide for value in values for ide in value.split(",") if ide]
    unknown = [ide for ide in ides if ide not in KNOWN_IDES]
    if unknown:
        raise ValueError(f"unknown IDE id(s) {', '.join(unknown)}; known: {', '.join(KNOWN_IDES)}")
    return list(dict.fromkeys(ides))


def _sha_arg(value: str) -> str:
    """Accept only a lowercase sha256 hex digest (nothing is written on a malformed value)."""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a lowercase sha256 hex digest")
    return value


WRITTEN_SHA = {
    "action": "append",
    "type": _sha_arg,
    "default": [],
    "metavar": "SHA256",
    "help": (
        "sha256 of .claude/settings.json bytes this installer run wrote; repeatable. "
        "A backup of that file is skipped only when its bytes hash to one of these."
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(prog="hyper_firewall.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render .agentignore for the selected IDEs")
    render.add_argument(
        "--ides", nargs="+", default=["claude"], metavar="ID", help="comma or space"
    )
    render.add_argument("--dry-run", action="store_true", help="report, write nothing")
    render.add_argument("--written-sha", **WRITTEN_SHA)
    commands.add_parser("check", help="exit 1 on drift; warn about broad allows")
    merge_settings = commands.add_parser("merge-settings", help="merge a settings fragment")
    merge_settings.add_argument("fragment", help="JSON file holding the settings fragment")
    merge_settings.add_argument("--written-sha", **WRITTEN_SHA)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _runtime.require_runtime()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        top = hyper_openrig.repo_toplevel()
        if ns.command == "render":
            return cmd_render(top, _parse_ides(ns.ides), ns.dry_run, tuple(ns.written_sha))
        if ns.command == "merge-settings":
            return cmd_merge_settings(top, ns.fragment, tuple(ns.written_sha))
        return cmd_check(top)
    except (ValueError, hyper_openrig.UsageError) as exc:
        print(f"hyper_firewall.py: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Refused as exc:
        print(f"hyper_firewall.py: refused, nothing written: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
