#!/usr/bin/env python3
"""Measure one HACF role's turn-1 context with a one-word `claude -p` probe (v0.6.4, OPR.99.0.8.5).

Dev tooling only; never installed into projects. Stdlib + PyYAML, Python 3.10+.

Usage:
  startup_probe.py --role <role> --rig-dir <openrig/hacf> --managed-file <CLAUDE.local.md source>
                   --model <id> --out <json> [--lever <name>]... [--culture <file>] [--repeat N]

The instrument is a `claude -p` reproduction of one seat's launch inputs, not the seat itself:
- a fresh probe directory under $TMPDIR (refused when TMPDIR is unset or under /tmp), removed
  afterwards, holding CLAUDE.local.md, a copy of --managed-file (with --culture, the body of its
  CULTURE.md managed block is replaced by that file's text; the markers stay);
- the prompt: the rig spec's start-up files in rig.yaml order (startup/context.md,
  startup/protocol.md, the member's own files such as startup/lead-first-move.md), then the
  profile's role file from agents/<agent>/agent.yaml, then one line asking for the word OK;
- `claude -p <prompt> --model <id> --output-format stream-json --verbose --max-turns 1
  --permission-mode bypassPermissions --settings '{"permissions": {"deny": [...]}}'`, the deny
  list from bin/seat-rules.py --role <role>, run with every OPENRIG_* and RIGGED_* variable (and
  CLAUDECODE, so it is not a nested session) removed from the environment.

Turn-1 is input_tokens + cache_creation_input_tokens + cache_read_input_tokens of the turn's
assistant record. The system/init event gives the tools, MCP servers and slash commands or
skills. The output JSON holds numbers and names only: never the reply text, never the managed
file's text. OpenRig's own --settings and hooks are not reproduced, and the probe directory's
auto-memory differs from a seat's; compare like with like only.

Levers (--lever, repeatable, applied together):
  deny-extra:<file>    add the bare tool names in <file> (one per line) to the deny list
  env-no-claudeai-mcp  run with ENABLE_CLAUDEAI_MCP_SERVERS=false
  strict-mcp           add --strict-mcp-config --mcp-config '{"mcpServers":{}}'
  no-onboarding        drop the openrig-onboarding-01.md and -02.md managed blocks from the copy
  no-culture           an empty CULTURE.md block (an upper bound only)
  no-protocol          drop startup/protocol.md from the prompt (an upper bound only)

Exit 0; 1 when a probe run fails or its usage or init event is missing (never a guessed
number); 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SCHEMA = "hacf.startup_probe/v1"
MAX_REPEAT = 3
PROBE_LINE = "Probe: reply with the single word OK and call no tool."
SIMPLE_LEVERS = ("env-no-claudeai-mcp", "strict-mcp", "no-onboarding", "no-culture", "no-protocol")
ONBOARDING_BLOCKS = ("openrig-onboarding-01.md", "openrig-onboarding-02.md")
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
ENV_DROP_PREFIXES = ("OPENRIG_", "RIGGED_")
ENV_DROP = ("CLAUDECODE",)
TIMEOUT = 600


class UsageError(Exception):
    """A bad argument or input: exit 2 with one line."""


class ProbeError(Exception):
    """A probe run failed or gave no usage or init event: exit 1 with one line."""


class Parser(argparse.ArgumentParser):
    """argparse that raises UsageError instead of exiting."""

    def error(self, message: str):
        """Raise UsageError with argparse's message."""
        raise UsageError(message)


# --------------------------------------------------------------------------- inputs


def read_yaml(path: Path) -> dict:
    """Load a YAML mapping.

    Raises:
        UsageError: when the file is unreadable or not a mapping.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UsageError(f"cannot read {path.name}: {exc}") from None
    if not isinstance(data, dict):
        raise UsageError(f"{path.name} is not a mapping")
    return data


def start_files(rig_dir: Path, role: str) -> list[Path]:
    """Return the role's delivered start-up files, in rig.yaml order, then its role file.

    Raises:
        UsageError: when the role is not a profile or a file is missing.
    """
    rig = read_yaml(rig_dir / "rig.yaml")
    paths = [rig_dir / f["path"] for f in (rig.get("startup") or {}).get("files") or []]
    member = None
    for pod in rig.get("pods") or []:
        for m in pod.get("members") or []:
            if m.get("profile") == role:
                member = m
                break
        if member:
            break
    if member is None:
        raise UsageError(f"no rig.yaml member has profile {role!r}")
    paths += [rig_dir / f["path"] for f in (member.get("startup") or {}).get("files") or []]
    ref = str(member.get("agent_ref", ""))
    agent_dir = rig_dir / ref.split(":", 1)[1] if ref.startswith("local:") else None
    if agent_dir is None:
        raise UsageError(f"member {member.get('id')} has no local agent_ref")
    profiles = read_yaml(agent_dir / "agent.yaml").get("profiles") or {}
    if role not in profiles:
        raise UsageError(f"agent.yaml has no profile {role!r}")
    paths += [
        agent_dir / f["path"] for f in (profiles[role].get("startup") or {}).get("files") or []
    ]
    for path in paths:
        if not path.is_file():
            raise UsageError(f"start-up file missing: {path.name}")
    return paths


def block_re(name: str) -> re.Pattern:
    """The BEGIN..END OpenRig managed block of one file name, markers in groups 1 and 3."""
    n = re.escape(name)
    return re.compile(
        rf"(<!-- BEGIN OpenRig MANAGED BLOCK: {n} -->\n)(.*?)"
        rf"(<!-- END OpenRig MANAGED BLOCK: {n} -->)",
        re.DOTALL,
    )


def managed_text(source: Path, culture: Path | None, levers: list[str]) -> str:
    """Return the probe's CLAUDE.local.md text.

    Raises:
        UsageError: when the source is unreadable or a needed marker is missing.
    """
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read --managed-file: {exc.strerror or exc}") from None
    if culture is not None or "no-culture" in levers:
        pattern = block_re("CULTURE.md")
        if not pattern.search(text):
            raise UsageError("--managed-file has no CULTURE.md managed block markers")
        if "no-culture" in levers:
            body = ""
        else:
            try:
                body = culture.read_text(encoding="utf-8")
            except OSError as exc:
                raise UsageError(f"cannot read --culture: {exc.strerror or exc}") from None
            if body and not body.endswith("\n"):
                body += "\n"
        text = pattern.sub(lambda m: m.group(1) + body + m.group(3), text, count=1)
    if "no-onboarding" in levers:
        for name in ONBOARDING_BLOCKS:
            pattern = block_re(name)
            if not pattern.search(text):
                raise UsageError(f"--managed-file has no {name} managed block markers")
            text = pattern.sub("", text, count=1)
    return text


def deny_list(rig_dir: Path, role: str, levers: list[str]) -> list[str]:
    """Return the role's deny list from seat-rules.py plus any deny-extra lever's names.

    Raises:
        UsageError: when seat-rules.py fails or a deny-extra file is unreadable.
    """
    result = subprocess.run(
        [sys.executable, str(rig_dir / "bin" / "seat-rules.py"), "--role", role],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise UsageError(f"seat-rules.py --role {role} exited {result.returncode}")
    try:
        deny = list(json.loads(result.stdout)["deny"])
    except (ValueError, KeyError, TypeError):
        raise UsageError("seat-rules.py printed no deny list") from None
    for lever in levers:
        if lever.startswith("deny-extra:"):
            path = Path(lever.split(":", 1)[1])
            try:
                names = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
            except OSError as exc:
                raise UsageError(f"cannot read {lever}: {exc.strerror or exc}") from None
            deny += [n for n in names if n and n not in deny]
    return deny


def check_levers(levers: list[str]) -> None:
    """Raise UsageError on an unknown lever."""
    for lever in levers:
        if lever in SIMPLE_LEVERS or (lever.startswith("deny-extra:") and len(lever) > 11):
            continue
        raise UsageError(f"unknown --lever {lever!r}")


def probe_root() -> Path:
    """Return $TMPDIR, refusing an unset one or one under /tmp.

    Raises:
        UsageError: when TMPDIR is unset, missing or under /tmp.
    """
    raw = os.environ.get("TMPDIR")
    if not raw:
        raise UsageError("TMPDIR is unset; set it to an on-disk dir outside /tmp")
    root = Path(raw).resolve()
    if root == Path("/tmp") or Path("/tmp") in root.parents:
        raise UsageError("TMPDIR is under /tmp; use an on-disk dir")
    if not root.is_dir():
        raise UsageError("TMPDIR is not a directory")
    return root


# --------------------------------------------------------------------------- one run


def probe_env(levers: list[str]) -> dict[str, str]:
    """The environment without rig or seat identity, plus the env lever."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(ENV_DROP_PREFIXES) and k not in ENV_DROP
    }
    if "env-no-claudeai-mcp" in levers:
        env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    return env


def command(prompt: str, model: str, deny: list[str], levers: list[str]) -> list[str]:
    """The claude argv of one probe run."""
    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        json.dumps({"permissions": {"deny": deny}}),
    ]
    if "strict-mcp" in levers:
        argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    return argv


def names(items) -> list[str]:
    """Names from a list of strings or of {name: ...} objects."""
    out = []
    for item in items or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(item["name"])
    return out


def parse_stream(text: str) -> dict:
    """Return one run's numbers and inventories from stream-json output.

    Raises:
        ProbeError: when the init event or the turn's usage is missing.
    """
    init = None
    usage = None
    result_usage = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init" and init is None:
            init = event
        elif event.get("type") == "assistant" and usage is None:
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage = message["usage"]
        elif event.get("type") == "result" and isinstance(event.get("usage"), dict):
            result_usage = event["usage"]
    usage = usage or result_usage
    if init is None:
        raise ProbeError("no system/init event in the stream")
    if usage is None or not all(isinstance(usage.get(k), int) for k in USAGE_KEYS):
        raise ProbeError("no usage with the three input counts in the stream")
    counts = {k: usage[k] for k in USAGE_KEYS}
    skills = names(init.get("slash_commands")) + [
        s for s in names(init.get("skills")) if s not in names(init.get("slash_commands"))
    ]
    return {
        "turn1": sum(counts.values()),
        **counts,
        "tools": names(init.get("tools")),
        "mcp_servers": names(init.get("mcp_servers")),
        "skills_or_commands": skills,
    }


def run_once(root: Path, managed: str, argv: list[str], env: dict[str, str]) -> dict:
    """Run one probe in a fresh dir under root, removed afterwards.

    Raises:
        ProbeError: when claude fails, times out or gives no usage.
    """
    probe_dir = Path(tempfile.mkdtemp(prefix="startup-probe-", dir=root))
    try:
        (probe_dir / "CLAUDE.local.md").write_text(managed, encoding="utf-8")
        try:
            result = subprocess.run(
                argv,
                cwd=probe_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeError(f"claude did not run: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise ProbeError(f"claude exited {result.returncode}")
        return parse_stream(result.stdout)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def claude_version(env: dict[str, str]) -> str | None:
    """`claude --version`, or None."""
    try:
        r = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=60, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


# --------------------------------------------------------------------------- CLI


def run(argv: list[str]) -> int:
    """Run the CLI; return the exit code.

    Raises:
        UsageError: on a usage error.
        ProbeError: when a probe run fails.
    """
    p = Parser(prog="startup_probe.py", description="One role's turn-1 context, by claude -p.")
    p.add_argument("--role", required=True)
    p.add_argument("--rig-dir", required=True)
    p.add_argument("--managed-file", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--lever", action="append", default=[])
    p.add_argument("--culture")
    p.add_argument("--repeat", type=int, default=1)
    args = p.parse_args(argv)
    if not 1 <= args.repeat <= MAX_REPEAT:
        raise UsageError(f"--repeat must be 1..{MAX_REPEAT}")
    check_levers(args.lever)
    root = probe_root()
    rig_dir = Path(args.rig_dir)
    files = start_files(rig_dir, args.role)
    if "no-protocol" in args.lever:
        files = [f for f in files if f.name != "protocol.md"]
    prompt = "\n\n".join(f.read_text(encoding="utf-8").rstrip("\n") for f in files)
    prompt += "\n\n" + PROBE_LINE
    managed = managed_text(
        Path(args.managed_file), Path(args.culture) if args.culture else None, args.lever
    )
    deny = deny_list(rig_dir, args.role, args.lever)
    env = probe_env(args.lever)
    argv_ = command(prompt, args.model, deny, args.lever)
    runs = [run_once(root, managed, argv_, env) for _ in range(args.repeat)]
    out = {
        "schema": SCHEMA,
        "role": args.role,
        "model": args.model,
        "levers": args.lever,
        "claude_version": claude_version(env),
        "runs": runs,
        "turn1_median": statistics.median(r["turn1"] for r in runs),
    }
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{args.role}: turn1_median {out['turn1_median']} over {len(runs)} run(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: exit 2 on a usage error, 1 on a failed probe."""
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        print(f"startup_probe: {exc}", file=sys.stderr)
        return 2
    except ProbeError as exc:
        print(f"startup_probe: probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
