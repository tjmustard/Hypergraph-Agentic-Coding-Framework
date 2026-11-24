#!/usr/bin/env python3
"""Turn a role of roles.yml into the rules `rig seat set-permissions <seat> --rules <file>` takes.

usage: seat-rules.py [--roles <file>] (--role <role> | --all) [--json]
       seat-rules.py [--roles <file>] --check

  --role <r> --json  prints {"allow": [], "deny": [<the role's tools_deny, in table order>]}
  --all --json       prints {<role>: <rules>} for every role
  --check            the allowlist check and the key-free check; prints every problem and exits 1
                     when there is one, else prints `ok` and exits 0

It reads roles.yml beside its own folder (openrig/hacf/roles.yml) unless --roles names another
file; --check reads agents/hacf/agent.yaml and rig.yaml beside that roles.yml. It never emits an
allow rule: under `builtin:yolo` an allow constrains nothing, and under a prompting mode a bare
allow would pre-approve that tool. It writes nothing and calls no `rig` command; OPS.md is the
operator's recipe for applying the output. Exit 2, with one line on stderr, on an unknown role
or a missing or invalid file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

SCHEMA = "hacf.roles/v1"
REQUIRED_DENY = "AskUserQuestion"
# A bare MCP server deny (roles v2): `mcp__<server>`, one server segment with no `__` in it.
MCP_SERVER_DENY = re.compile(r"mcp__[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*")
# TJ: keep codebase-memory-mcp wherever MCP is on, so no role may deny it.
KEPT_MCP = "codebase-memory-mcp"
# rig.yaml stays key-free: OpenRig daemons up to 0.7.18 refuse unknown member keys.
FORBIDDEN_RIG_KEYS = (
    "claude_permissions",
    "claude_mcp",
    "claude_settings_fragment",
    "claudeMdExcludes",
)
MCP_NOTE = (
    "mcp: off is recorded, not applied: no rig seat verb sets MCP loading on an existing seat "
    "(OpenRig 0.7.21); unknown"
)
DEFAULT_ROLES = Path(__file__).resolve().parent.parent / "roles.yml"


class Refusal(Exception):
    """A missing or invalid input: exit 2 with one line."""


def load_yaml(path: Path, what: str):
    """Return the parsed YAML at path, or raise Refusal naming what is wrong with it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refusal(f"cannot read {what} {path}: {exc.strerror or exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Refusal(f"{what} {path} is not valid YAML: {str(exc).splitlines()[0]}") from exc


def mcp_value(value) -> str | None:
    """Return 'on' or 'off' for a role's mcp field. YAML 1.1 reads a bare on/off as a boolean."""
    if value is True or value == "on":
        return "on"
    if value is False or value == "off":
        return "off"
    return None


def load_roles(path: Path) -> dict[str, dict]:
    """Return {role: entry} from roles.yml, in table order, or raise Refusal."""
    data = load_yaml(path, "roles.yml")
    if not isinstance(data, dict):
        raise Refusal(f"roles.yml {path} is not a mapping")
    if data.get("schema") != SCHEMA:
        raise Refusal(f"roles.yml {path}: schema is {data.get('schema')!r}, expected {SCHEMA!r}")
    roles = data.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise Refusal(f"roles.yml {path}: `roles` is not a non-empty mapping")
    for name, entry in roles.items():
        if not isinstance(entry, dict):
            raise Refusal(f"roles.yml {path}: role {name!r} is not a mapping")
        for key in ("tools_allow", "tools_deny", "skills"):
            items = entry.get(key)
            if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                raise Refusal(f"roles.yml {path}: role {name!r}: `{key}` is not a list of strings")
        if mcp_value(entry.get("mcp")) is None:
            raise Refusal(f"roles.yml {path}: role {name!r}: `mcp` is not on or off")
    return {str(name): entry for name, entry in roles.items()}


def rules_for(entry: dict) -> dict[str, list[str]]:
    """Return the --rules JSON for one role: no allows, the table's denies in order."""
    return {"allow": [], "deny": list(entry["tools_deny"])}


def forbidden_keys(node, where: str = "rig.yaml") -> list[str]:
    """Return the path of every forbidden key, at any depth of node."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}"
            if key in FORBIDDEN_RIG_KEYS:
                found.append(here)
            found.extend(forbidden_keys(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(forbidden_keys(value, f"{where}[{index}]"))
    return found


def rig_members(rig: dict) -> list[dict]:
    """Return every member mapping of rig.yaml's pods."""
    members = []
    for pod in rig.get("pods") or []:
        if isinstance(pod, dict):
            members.extend(m for m in pod.get("members") or [] if isinstance(m, dict))
    return members


def check(roles_path: Path) -> list[str]:
    """Return every problem of the allowlist check and the key-free check."""
    roles = load_roles(roles_path)
    base = roles_path.parent
    agent = load_yaml(base / "agents" / "hacf" / "agent.yaml", "agent.yaml")
    rig = load_yaml(base / "rig.yaml", "rig.yaml")
    if not isinstance(agent, dict) or not isinstance(agent.get("profiles"), dict):
        raise Refusal(f"agent.yaml {base / 'agents/hacf/agent.yaml'} has no `profiles` mapping")
    if not isinstance(rig, dict):
        raise Refusal(f"rig.yaml {base / 'rig.yaml'} is not a mapping")
    profiles = agent["profiles"]
    used = {m.get("profile") for m in rig_members(rig)}
    problems = []

    # The allowlist check: the table, agent.yaml and rig.yaml name the same roles and skills.
    for name, profile in profiles.items():
        if name not in roles:
            problems.append(f"agent.yaml profile {name!r} has no role in roles.yml")
            continue
        uses = (profile or {}).get("uses") or {}
        for skill in uses.get("skills") or []:
            if skill not in roles[name]["skills"]:
                problems.append(
                    f"agent.yaml profile {name!r} grants skill {skill!r},"
                    f" not in roles.yml {name}.skills"
                )
    for name, entry in roles.items():
        if name not in profiles:
            problems.append(f"roles.yml role {name!r} is not a profile in agent.yaml")
        if name not in used:
            problems.append(f"roles.yml role {name!r} is used by no rig.yaml member")
        allow, deny = entry["tools_allow"], entry["tools_deny"]
        rules = rules_for(entry)
        for tool in rules["allow"]:
            if tool not in allow:
                problems.append(f"role {name!r}: generated allow {tool!r} is not in tools_allow")
        if rules["deny"] != deny:
            problems.append(f"role {name!r}: generated deny differs from tools_deny")
        for tool in allow:
            if tool in deny:
                problems.append(f"role {name!r}: {tool!r} is in both tools_allow and tools_deny")
        if REQUIRED_DENY not in deny:
            problems.append(f"role {name!r}: tools_deny lacks {REQUIRED_DENY}")
        for tool in [*allow, *deny, *rules["allow"], *rules["deny"]]:
            bare_server = tool in deny and MCP_SERVER_DENY.fullmatch(tool) is not None
            if "(" in tool or (tool.startswith("mcp__") and not bare_server):
                problems.append(f"role {name!r}: rule {tool!r} is an MCP or parenthesized rule")
        for tool in deny:
            if KEPT_MCP in tool.lower().replace("_", "-"):
                problems.append(f"role {name!r}: {tool!r} denies {KEPT_MCP}, which stays allowed")

    # The key-free check: rig.yaml carries no per-member Claude keys.
    for path in forbidden_keys(rig):
        problems.append(f"{path}: forbidden key (rig.yaml stays key-free)")
    return list(dict.fromkeys(problems))


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, run one mode and return its exit code."""
    parser = argparse.ArgumentParser(prog="seat-rules.py", description=__doc__.splitlines()[0])
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES, help="the roles.yml to read")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--role", help="print one role's --rules JSON")
    mode.add_argument("--all", action="store_true", help="print every role's --rules JSON")
    mode.add_argument("--check", action="store_true", help="the allowlist and key-free checks")
    parser.add_argument("--json", action="store_true", help="JSON output (the only format)")
    args = parser.parse_args(argv)

    try:
        if args.check:
            problems = check(args.roles)
            for problem in problems:
                print(problem)
            if problems:
                return 1
            print("ok")
            return 0
        roles = load_roles(args.roles)
        if args.all:
            print(json.dumps({name: rules_for(entry) for name, entry in roles.items()}, indent=2))
            return 0
        if args.role not in roles:
            raise Refusal(f"unknown role {args.role!r}; roles.yml has {', '.join(roles)}")
        entry = roles[args.role]
        if mcp_value(entry["mcp"]) == "off":
            print(MCP_NOTE, file=sys.stderr)
        print(json.dumps(rules_for(entry), indent=2))
        return 0
    except Refusal as exc:
        print(f"seat-rules.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
