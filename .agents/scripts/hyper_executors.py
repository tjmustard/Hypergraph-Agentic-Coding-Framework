#!/usr/bin/env python3
"""Pluggable headless executors for the HACF engine: argv, sandbox settings, env, run, parsers.

Normative design: HACF's v0.6.0 design notes (not shipped): the engine (EXECUTOR CONTRACT) and the
orchestration config. The engine (S09a1) drives these; nothing here calls the
Anthropic API.

Usage:
    hyper_executors.py preflight [--executor claude-cli]

Contract (every executor): preflight(), build_argv(phase, ctx), build_settings(ctx) and
parse_result(completed) -> ExecResult. get_executor(name) returns one; prepare() writes the
generated settings and the empty strict MCP config, then builds the argv; run() starts it in its
own process group.

- claude-cli: `claude -p` with dontAsk, `--permission-prompts none` and `--setting-sources user`
  (never project settings, R4). The generated --settings enables the OS sandbox: writes limited
  to the worktree and the sandbox's own temp dir, the worktree's .git pointer and the git common
  dir denied (R1), no network, no unsandboxed retry, fail if unavailable. Its PreToolUse hook
  calls the base-revision hyper_gate_hook.py under ctx base_dir by absolute path (R4).
- codex-cli, gemini-cli: experimental with no HACF-verified sandbox; building their argv refuses
  unless the caller passes allow_unsandboxed=True (the engine CLI flag, never config; R20.2).
- openrig-seat: a seam only (NotImplementedError). stub: test-only.

ctx keys: worktree, git_common_dir, base_dir (absolute), slice_spec, model, settings_file,
mcp_config_file, autonomy_prompt_file, run_id, slice, outbox; optional max_turns,
max_budget_usd, protected_paths (worktree-relative globs, default PROTECTED_PATHS), prompt,
scenario (stub) and last_message_file (codex).

Config: .agents/config/orchestration.yml over the embedded DEFAULTS; a missing file falls back
to DEFAULTS with a notice. Security keys come only from DEFAULTS or caller flags: a tracked
`sandbox` is always `required`, a tracked `verified` can only turn it false, `status` comes from
DEFAULTS (an executor DEFAULTS does not name is experimental), and the autonomy security keys
are taken from DEFAULTS (R4, R20.2).

Exit codes (CLI): 0 ok, 1 preflight failed, 2 usage.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import _runtime

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "orchestration.yml"
UPGRADE_NOTICE = "run HACF-install.sh to complete the upgrade"
GATE_HOOK_REL = ".agents/scripts/hyper_gate_hook.py"
EMPTY_MCP_CONFIG = {"mcpServers": {}}
PHASES = ("execute", "audit")
CHECK_TIMEOUT = 60  # seconds for `claude --version` and `claude auth status`
# Always protected (subsystems.md#engine step f); the engine adds the hacf_touches-dependent ones.
PROTECTED_PATHS = (
    "spec/compiled/architecture.yml",
    "lore_hypergraph.yml",
    "lore/CANON.md",
    "lore/canon/**",
    "missions/*/mission.yaml",
    "missions/*/slices/*/slice.yaml",
    ".git*",
    ".mcp.json",
)
AUTONOMY_SECURITY_KEYS = (
    "env_passthrough",
    "env_passthrough_prefixes",
    "auth_passthrough",
    "max_budget_usd_per_phase",
    "max_budget_usd_per_run",
    "scope_policy",
)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_PROMPT = (
    "Read {base_dir}/AGENTS.md, then read {base_dir}/.agents/skills/hyper-%s/SKILL.md and follow "
    "its instructions precisely for the slice spec {slice_spec}."
)

# The shipped defaults for the keys this module reads; must equal orchestration.yml (a test checks).
DEFAULTS: dict[str, Any] = {
    "executors": {
        "claude-cli": {
            "preflight": [["claude", "--version"], ["claude", "auth", "status"]],
            "min_version": "2.1.259",
            "max_tested_version": "2.1.305",
            "verified": False,
            "sandbox": "required",
            "argv": [
                "claude",
                "-p",
                "{prompt}",
                "--output-format",
                "json",
                "--model",
                "{model}",
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--setting-sources",
                "user",
                "--settings",
                "{settings_file}",
                "--allowedTools",
                "{allowed_tools}",
                "--disallowedTools",
                "{disallowed_tools}",
                "--max-turns",
                "{max_turns}",
                "--max-budget-usd",
                "{max_budget_usd}",
                "--strict-mcp-config",
                "--mcp-config",
                "{mcp_config_file}",
                "--append-system-prompt-file",
                "{autonomy_prompt_file}",
            ],
            "prompts": {"execute": _PROMPT % "execute", "audit": _PROMPT % "audit"},
            "result_parser": "claude_json",
        },
        "codex-cli": {
            "status": "experimental",
            "sandbox": "required",
            "argv": [
                "codex",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--output-last-message",
                "{last_message_file}",
                "{prompt}",
            ],
            "result_parser": "codex_jsonl",
        },
        "gemini-cli": {
            "status": "experimental",
            "sandbox": "required",
            "argv": [
                "gemini",
                "-p",
                "{prompt}",
                "--output-format",
                "json",
                "--approval-mode",
                "auto_edit",
            ],
            "result_parser": "gemini_json",
        },
        "openrig-seat": {"status": "seam-only"},
        "stub": {
            "status": "test-only",
            "argv": [
                "python3",
                "dev/tests/fixtures/stub_executor.py",
                "--scenario",
                "{scenario}",
                "--prompt",
                "{prompt}",
            ],
            "result_parser": "claude_json",
        },
    },
    "tools": {
        "execute_allowed": ["Read", "Glob", "Grep", "Agent", "Edit(./**)"],
        "audit_allowed": ["Read", "Glob", "Grep", "Agent"],
        "disallowed": [
            "AskUserQuestion",
            "WebFetch",
            "WebSearch",
            "Bash(git push *)",
            "Bash(git checkout *)",
            "Bash(git switch *)",
            "Bash(git reset *)",
            "Bash(git rebase *)",
            "Bash(git merge *)",
            "Bash(git commit *)",
            "Bash(git update-ref *)",
            "Bash(curl *)",
            "Bash(wget *)",
        ],
    },
    "autonomy": {
        "env_passthrough": [
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "TERM",
            "TMPDIR",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "NODE_EXTRA_CA_CERTS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ],
        "env_passthrough_prefixes": ["CLAUDE_CODE_"],
        "auth_passthrough": {
            "api_key": ["ANTHROPIC_API_KEY"],
            "bedrock": ["AWS_*"],
            "vertex": ["GOOGLE_*", "CLOUD_ML_*", "VERTEX_*"],
            "oauth": [],
        },
        "max_turns": 80,
        "max_budget_usd_per_phase": 5.0,
        "max_budget_usd_per_run": 50.0,
        "phase_timeout_s": 3600,
        "kill_grace_s": 30,
        "scope_policy": "warn",
    },
}


class ExecutorError(Exception):
    """A refusal or failure named by its failure-taxonomy type (subsystems.md#engine)."""

    def __init__(self, failure_type: str, message: str) -> None:
        """Keep the taxonomy type next to the message."""
        super().__init__(message)
        self.failure_type = failure_type


@dataclass
class ExecResult:
    """One phase's outcome; failure_type is None exactly when ok is True."""

    ok: bool
    failure_type: str | None = None
    session_id: str | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    result_text: str | None = None
    permission_denials: list = field(default_factory=list)
    raw_ref: str | None = None  # the engine sets it to where it stored the raw stdout


class Completed(NamedTuple):
    """A finished executor process; timed_out is True when run() had to kill its group."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Executor(Protocol):
    """The contract the engine programs against."""

    def preflight(self) -> dict:
        """Check the executor can run; return its version and verified flag."""

    def build_argv(self, phase: str, ctx: dict) -> list[str]:
        """Return the argv list for one phase."""

    def build_settings(self, ctx: dict) -> dict:
        """Return the generated settings for one phase."""

    def parse_result(self, completed: Any) -> ExecResult:
        """Map a finished process to an ExecResult."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _warn(message: str) -> None:
    print(f"hyper_executors: warning: {message}", file=sys.stderr)


def load_config(path: Path | str | None = None) -> dict:
    """Return the config: the file over DEFAULTS, with every security key from DEFAULTS (R4)."""
    path = Path(path) if path is not None else CONFIG_PATH
    config = copy.deepcopy(DEFAULTS)
    if not path.is_file():
        _warn(f"{path} is missing; using the embedded defaults ({UPGRADE_NOTICE})")
        return config
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for name, entry in (data.get("executors") or {}).items():
        # An executor DEFAULTS does not name is experimental, so it needs --allow-unsandboxed.
        shipped = config["executors"].get(name, {"status": "experimental"})
        merged = {**shipped, **entry}
        # R20.2: every executor is sandbox: required; 'off' comes only from --allow-unsandboxed.
        merged["sandbox"] = "required"
        merged["verified"] = shipped.get("verified") is True and merged.get("verified") is True
        # status decides whether build_argv needs allow_unsandboxed, so it is shipped-only.
        merged.pop("status", None)
        if "status" in shipped:
            merged["status"] = shipped["status"]
        for key in ("sandbox", "verified", "status"):
            if key in entry and entry[key] != merged.get(key):
                _warn(f"{path}: executors.{name}.{key} cannot loosen the shipped default; ignored")
        config["executors"][name] = merged
    config["tools"].update(data.get("tools") or {})
    for key, value in (data.get("autonomy") or {}).items():
        if key not in AUTONOMY_SECURITY_KEYS:
            config["autonomy"][key] = value
        elif value != config["autonomy"].get(key):
            _warn(
                f"{path}: autonomy.{key} is a security key (shipped default or CLI only); ignored"
            )
    return config


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


def auth_kind(environ: dict[str, str], auth_method: str | None) -> str:
    """Map the CLAUDE_CODE_USE_* flags and `claude auth status` authMethod to a passthrough key."""
    if environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
        return "bedrock"
    if environ.get("CLAUDE_CODE_USE_VERTEX") == "1":
        return "vertex"
    return "api_key" if auth_method in ("api_key", "api_key_helper") else "oauth"


def scrub_env(
    environ: dict[str, str], autonomy: dict, kind: str, ctx: dict | None = None
) -> dict[str, str]:
    """Return the executor env: passthrough names and prefixes plus kind's auth variables (R20.4).

    With ctx, the engine's HACF_AUTONOMOUS, HACF_RUN_ID, HACF_SLICE and HACF_OUTBOX are added, and
    UV_NO_CACHE=1: the agent sandbox makes ~/.cache/uv read-only, so `uv run` could not open it
    (R40).
    """
    names = set(autonomy["env_passthrough"])
    prefixes = tuple(autonomy["env_passthrough_prefixes"])
    auth = autonomy["auth_passthrough"].get(kind, [])
    env = {
        key: value
        for key, value in environ.items()
        if key in names
        or key.startswith(prefixes)
        or any(fnmatch.fnmatchcase(key, pattern) for pattern in auth)
    }
    if ctx is not None:
        env.update(
            HACF_AUTONOMOUS="1",
            HACF_RUN_ID=str(ctx["run_id"]),
            HACF_SLICE=str(ctx["slice"]),
            HACF_OUTBOX=str(ctx["outbox"]),
            UV_NO_CACHE="1",
        )
    return env


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run(
    argv: list[str], *, env: dict[str, str], cwd: Path | str, timeout: float, kill_grace_s: float
) -> Completed:
    """Run argv in a new session; on timeout SIGTERM the group, then SIGKILL after the grace."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        shell=False,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return Completed(argv, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + kill_grace_s
    while _group_alive(proc.pid) and time.monotonic() < deadline:
        proc.poll()  # reap the leader, so only live members keep the group alive
        time.sleep(0.05)
    _signal_group(proc.pid, signal.SIGKILL)
    stdout, stderr = proc.communicate()
    return Completed(argv, proc.returncode, stdout, stderr, True)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _json_objects(stdout: str) -> list[dict]:
    """Return the JSON object stdout holds, or one per JSON line (stream-json, JSONL)."""
    try:
        whole = json.loads(stdout)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return [whole]
    objects = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            objects.append(item)
    return objects


def _claude_failure(data: dict, returncode: int) -> str | None:
    """Map a claude result object to None (ok) or a failure_type."""
    subtype = data.get("subtype")
    if subtype == "success" and not data.get("is_error") and returncode == 0:
        return None
    if subtype == "error_max_turns":
        return "turns_exceeded"
    if subtype == "error_max_budget_usd":
        return "budget_exceeded"
    status = data.get("api_error_status")
    text = str(data.get("result") or "").lower()
    if status == 429 or "rate limit" in text or "rate_limit" in text or "usage limit" in text:
        return "rate_limited"
    if status in (401, 403) or "invalid api key" in text or "/login" in text:
        return "executor_auth"
    return "executor_error"


def parse_claude_json(completed: Any) -> ExecResult:
    """Parse `claude -p --output-format json` (or stream-json) output into an ExecResult."""
    if getattr(completed, "timed_out", False):
        return ExecResult(False, "timeout", result_text=completed.stderr[-2000:] or None)
    results = [o for o in _json_objects(completed.stdout) if o.get("type") == "result"]
    if not results:
        return ExecResult(False, "executor_error", result_text=completed.stderr[-2000:] or None)
    data = results[-1]
    failure = _claude_failure(data, completed.returncode)
    return ExecResult(
        ok=failure is None,
        failure_type=failure,
        session_id=data.get("session_id"),
        num_turns=data.get("num_turns"),
        cost_usd=data.get("total_cost_usd"),
        result_text=data.get("result"),
        permission_denials=data.get("permission_denials") or [],
    )


def parse_codex_jsonl(completed: Any) -> ExecResult:
    """Parse `codex exec --json` events (experimental; event names UNVERIFIED)."""
    if getattr(completed, "timed_out", False):
        return ExecResult(False, "timeout")
    events = _json_objects(completed.stdout)
    types = [event.get("type") for event in events]
    session = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    messages = [
        e["item"].get("text")
        for e in events
        if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
    ]
    failed = "turn.failed" in types or "error" in types or "turn.completed" not in types
    failure = "executor_error" if failed or completed.returncode != 0 else None
    return ExecResult(
        failure is None, failure, session, result_text=messages[-1] if messages else None
    )


def parse_gemini_json(completed: Any) -> ExecResult:
    """Parse `gemini -p --output-format json` output (experimental; keys UNVERIFIED)."""
    if getattr(completed, "timed_out", False):
        return ExecResult(False, "timeout")
    objects = _json_objects(completed.stdout)
    data = objects[-1] if objects else {}
    failed = completed.returncode != 0 or data.get("error") or "response" not in data
    failure = "executor_error" if failed else None
    return ExecResult(
        failure is None, failure, data.get("session_id"), result_text=data.get("response")
    )


PARSERS = {
    "claude_json": parse_claude_json,
    "codex_jsonl": parse_codex_jsonl,
    "gemini_json": parse_gemini_json,
}


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def substitute(template: list[str], values: dict[str, Any]) -> list[str]:
    """Fill an argv template element by element; each element holds at most one placeholder."""
    argv = []
    for element in template:
        names = _PLACEHOLDER.findall(element)
        if len(names) > 1:
            raise ValueError(f"argv element {element!r} holds more than one placeholder")
        if names:
            if values.get(names[0]) is None:
                raise ValueError(f"argv placeholder {{{names[0]}}} has no value in ctx")
            value = values[names[0]]
            value = ",".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
            element = element.replace(f"{{{names[0]}}}", value)
        argv.append(element)
    return argv


class CliExecutor:
    """An executor described by its config entry: an argv template and a result parser."""

    def __init__(self, name: str, config: dict, *, allow_unsandboxed: bool = False) -> None:
        """Bind the executor's entry; sandbox is 'off' only through allow_unsandboxed (R20)."""
        self.name = name
        self.config = config
        self.entry = config["executors"][name]
        self.allow_unsandboxed = allow_unsandboxed
        self.sandbox = "off" if allow_unsandboxed else "required"

    def preflight(self) -> dict:
        """Check the executable is on PATH; return the verified flag."""
        if shutil.which(self.entry["argv"][0]) is None:
            raise ExecutorError("executor_unavailable", f"{self.entry['argv'][0]} is not on PATH")
        return {"version": None, "verified": self.entry.get("verified") is True, "warnings": []}

    def _values(self, phase: str, ctx: dict) -> dict[str, Any]:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}; expected one of {PHASES}")
        tools = self.config["tools"]
        autonomy = self.config["autonomy"]
        values = {
            "allowed_tools": tools[f"{phase}_allowed"],
            "disallowed_tools": tools["disallowed"],
            "max_turns": autonomy["max_turns"],
            "max_budget_usd": autonomy["max_budget_usd_per_phase"],
            **ctx,
        }
        prompts = self.entry.get("prompts") or self.config["executors"]["claude-cli"]["prompts"]
        if "prompt" not in ctx:
            values["prompt"] = prompts[phase].format(
                base_dir=ctx["base_dir"], slice_spec=ctx["slice_spec"]
            )
        return values

    def build_argv(self, phase: str, ctx: dict) -> list[str]:
        """Return the argv list for phase; experimental executors need allow_unsandboxed (R20.2)."""
        if self.entry.get("status") == "experimental":
            if not self.allow_unsandboxed:
                raise ExecutorError(
                    "sandbox_unavailable",
                    f"{self.name} is experimental and has no HACF-verified sandbox; it runs only "
                    "with the engine CLI flag --allow-unsandboxed",
                )
            _warn(f"{self.name} runs WITHOUT an OS sandbox (--allow-unsandboxed)")
        return substitute(self.entry["argv"], self._values(phase, ctx))

    def build_settings(self, ctx: dict) -> dict:
        """Return no settings: only claude-cli takes a generated --settings file."""
        return {}

    def parse_result(self, completed: Any) -> ExecResult:
        """Parse a finished process with the entry's result parser."""
        return PARSERS[self.entry["result_parser"]](completed)

    def prepare(self, phase: str, ctx: dict, directory: Path | str) -> list[str]:
        """Write settings.json and the empty strict MCP config into directory; return the argv."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        files = {"settings_file": directory / "settings.json"}
        files["mcp_config_file"] = directory / "mcp.json"
        files["settings_file"].write_text(json.dumps(self.build_settings(ctx), indent=2) + "\n")
        files["mcp_config_file"].write_text(json.dumps(EMPTY_MCP_CONFIG) + "\n")
        return self.build_argv(phase, {**ctx, **{k: str(v) for k, v in files.items()}})


class ClaudeCli(CliExecutor):
    """claude-cli: version and auth preflight plus the sandboxed, hooked --settings (R1, R4)."""

    def preflight(self, environ: dict[str, str] | None = None) -> dict:
        """Check `claude --version` and `claude auth status` under the scrubbed env (R20).

        Raises ExecutorError (executor_unavailable, preflight_failed or executor_auth). Returns
        the version string, the verified flag, the auth kind and any warnings; refusing an
        unverified executor is the engine's job.
        """
        environ = dict(os.environ if environ is None else environ)
        autonomy = self.config["autonomy"]
        version_argv, status_argv = self.entry["preflight"]
        proc = _check(version_argv, scrub_env(environ, autonomy, "oauth"))
        if proc.returncode != 0:
            raise ExecutorError("executor_unavailable", f"claude --version: {proc.stderr.strip()}")
        version_text = proc.stdout.strip()
        version = _runtime._version_tuple(version_text)
        if version < _runtime._version_tuple(self.entry["min_version"]):
            raise ExecutorError(
                "preflight_failed",
                f"claude {version_text} is older than {self.entry['min_version']}",
            )
        warnings = []
        if version > _runtime._version_tuple(self.entry["max_tested_version"]):
            warnings.append(
                f"claude {version_text} is newer than the tested "
                f"{self.entry['max_tested_version']}; re-run the S08 claude live test"
            )
            _warn(warnings[-1])
        kind = auth_kind(environ, None)
        if kind == "oauth":  # ask claude which first-party method is in use
            probe = _check(status_argv, scrub_env(environ, autonomy, "api_key"))
            kind = auth_kind(environ, _json_field(probe.stdout, "authMethod"))
        proc = _check(status_argv, scrub_env(environ, autonomy, kind))
        if proc.returncode != 0:
            raise ExecutorError("executor_auth", f"claude auth status exited {proc.returncode}")
        return {
            "version": version_text,
            "verified": self.entry.get("verified") is True,
            "auth_kind": kind,
            "warnings": warnings,
        }

    def build_settings(self, ctx: dict) -> dict:
        """Return the --settings JSON: sandbox, protected-path Edit denies and the base hook."""
        worktree = _absolute(ctx["worktree"], "worktree")
        common = _absolute(ctx["git_common_dir"], "git_common_dir")
        hook = _absolute(ctx["base_dir"], "base_dir") / GATE_HOOK_REL
        protected = ctx.get("protected_paths", PROTECTED_PATHS)
        # Sandbox paths take no globs here (their syntax is UNVERIFIED): a trailing /** becomes
        # its directory and a pattern still holding a wildcard is left to the Edit denies.
        concrete = [p[: -len("/**")] if p.endswith("/**") else p for p in protected]
        deny_write = [str(worktree / p) for p in concrete if not set("*?[") & set(p)]
        deny_write += [str(worktree / ".git"), str(common)]
        return {
            "sandbox": {
                "enabled": True,
                "allowUnsandboxedCommands": False,
                "failIfUnavailable": self.sandbox == "required",
                "network": {"allowedDomains": []},
                # tmp: the sandbox's own per-user temp dir, writable by default
                "filesystem": {"allowWrite": [str(worktree)], "denyWrite": deny_write},
            },
            # `//` marks an absolute path in Read and Edit rules
            "permissions": {"deny": [f"Edit(/{worktree / p})" for p in protected]},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": f"python3 {shlex.quote(str(hook))}"}
                        ],
                    }
                ]
            },
        }


def get_executor(
    name: str, config: dict | None = None, *, allow_unsandboxed: bool = False
) -> CliExecutor:
    """Return the named executor; openrig-seat is a seam only."""
    config = load_config() if config is None else config
    if name not in config["executors"]:
        raise ValueError(f"unknown executor {name!r}")
    if config["executors"][name].get("status") == "seam-only":
        raise NotImplementedError(f"{name} is a seam only")
    cls = ClaudeCli if name == "claude-cli" else CliExecutor
    return cls(name, config, allow_unsandboxed=allow_unsandboxed)


def _absolute(value: Any, key: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise ValueError(f"ctx {key} must be an absolute path, got {value!r}")
    return path


def _check(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=CHECK_TIMEOUT, check=False
        )
    except FileNotFoundError:
        raise ExecutorError("executor_unavailable", f"{argv[0]} is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise ExecutorError("preflight_failed", f"{' '.join(argv)} timed out") from None


def _json_field(text: str, key: str) -> Any:
    objects = _json_objects(text)
    return objects[0].get(key) if objects else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(prog="hyper_executors.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="check an executor's version and auth")
    pre.add_argument("--executor", default="claude-cli")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _runtime.require_runtime()
    ns = build_parser().parse_args(argv)
    try:
        report = get_executor(ns.executor).preflight()
    except (ExecutorError, ValueError, NotImplementedError) as exc:
        failure = getattr(exc, "failure_type", "preflight_failed")
        print(json.dumps({"ok": False, "failure_type": failure, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **report}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
