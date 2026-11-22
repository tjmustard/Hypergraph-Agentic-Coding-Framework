#!/usr/bin/env python3
"""HACF workflow engine: config, preflight, slice DAG, run state, scheduler, worktrees and land.

Normative design: HACF's v0.6.0 design notes (not shipped): the engine (RUN LIFECYCLE,
BUDGET AND RATE LIMITS, LANDING, GARBAGE COLLECTION, LOCKS) and the orchestration config.
Agents run only through the executors of hyper_executors.py; nothing here calls the Anthropic API.

Usage:
    hyper_engine.py preflight [run flags]
    hyper_engine.py run [--resume <run_id>] [run flags]
    hyper_engine.py status <run_id>
    hyper_engine.py land <run_id> [--merge] [--rebase-ledger]
    hyper_engine.py gc [--older-than 14d] [--apply]

Run flags: --executor <name> (default claude-cli), --unverified-executor, --allow-unsandboxed,
--allow-unsandboxed-oracle, --max-budget-usd-per-run <usd>. They are the only way to loosen a
security key: tracked .agents/config/orchestration.yml and .agents/local/hacf.yml `orchestration`
may only tighten one (R4, R42). The test-only stub executor runs only with HACF_TEST=1 (R41).

`run` drives the scheduler through WorktreeRunner (steps 4a-k): each slice runs in its own
worktree on swimlane/hacf/<run>/<slice dir>, branched from the integration worktree's tip, and
the main checkout stays byte-identical until `land`. Every engine git call names GIT_DIR and
GIT_WORK_TREE and runs no repo hook (R1, R3); the metadata guard restores any other change to
refs or git metadata as ref_violation. Oracles and base-revision hooks run in bwrap (R2, R40).

State: .agents/state/runs/<run_id>/ holds run.json (written atomically), events.jsonl, base/
(read-only base-revision skill text and hooks), ledger/base.yml (the base ledger bytes, R5) and,
for an untracked ledger, the staged ledger/<name>; outbox/<slice>/{execute,audit}/, phases/ and
punch/. Locks: $(git rev-parse --git-common-dir)/hacf/locks/{engine,git,ledger}.lock (R22.1).

Exit codes: 0 ok, 1 run finished with blocked slices (or stopped), 2 usage, 3 lock held,
5 refused (preflight, ledger drift, a land conflict).
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import copy
import fcntl
import fnmatch
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import _runtime
import hyper_executors as hx
import hyper_ledger
import hyper_spec
from hyper_failures import FailureType, RetryPolicy, classify

try:
    import yaml
except ImportError:  # require_runtime() below reports it
    yaml = None  # type: ignore[assignment]

_runtime.require_runtime()

SCRIPTS = Path(__file__).resolve().parent
CONFIG_REL = ".agents/config/orchestration.yml"
LOCAL_REL = ".agents/local/hacf.yml"
STATE_REL = ".agents/state"
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
CHECK_TIMEOUT = 600  # seconds for one git or validator call
EXIT_OK, EXIT_BLOCKED, EXIT_USAGE, EXIT_LOCKED, EXIT_REFUSED = 0, 1, 2, 3, 5
# hacf_status -> the engine's starting status; anything else is skipped (never scheduled).
INITIAL_STATUS = {"audited": "audited", "compiled": "pending", "audit_failed": "pending"}
AGENT_SANDBOX_TOOLS = {"linux": ("bwrap", "socat"), "darwin": ("sandbox-exec",)}
COMMAND_KEYS = ("argv", "preflight", "prompts")  # executor keys that accept only the shipped value
OUTBOX_DIR = ".hacf-outbox"
# R20.3: the outbox entries an agent phase may leave. Approval entries are never among them.
OUTBOX_WHITELIST = {
    "hyper_ledger": ("ensure-node", "set-edges", "apply-review"),
    "hyper_lore": ("propose", "note", "stale"),
    "hyper_spec": ("progress",),
}
OUTBOX_FILES = ("audit.json", "execute.json", "gates.jsonl")
APPROVAL_OPS = ("approve", "reject", "retire", "reopen", "review", "approval-request")
NON_STOP_GATES = ("lore_decision_needed",)  # the audit records it; it does not stop the slice
BASE_FILES = (
    "AGENTS.md",
    ".agents/skills/hyper-execute/SKILL.md",
    ".agents/skills/hyper-audit/SKILL.md",
    hx.GATE_HOOK_REL,  # stdlib only, so it needs no imports copied beside it
)
HOOKS = ("pre-commit", "commit-msg")
# R20.4: shapes the scan looks for on the added lines of a slice diff (never printed).
CREDENTIAL_SHAPES = (
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a PEM private-key header", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("an Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,})")),
)
DURATION_RE = re.compile(r"^(\d+)([dhm])$")
DEFAULT_FLAGS: dict[str, Any] = {
    "executor": "claude-cli",
    "unverified_executor": False,
    "allow_unsandboxed": False,
    "allow_unsandboxed_oracle": False,
    "max_budget_usd_per_run": None,
}

# The shipped defaults: the whole of orchestration.yml (a test checks equality). The executor,
# tools and autonomy keys hyper_executors.py reads come from its own embedded copy.
DEFAULTS: dict[str, Any] = {
    "schema": "hacf.orchestration/v1",
    "security_keys": [
        "executors.*.sandbox",
        "executors.*.verified",
        "executors.*.argv",
        "executors.*.preflight",
        "executors.*.prompts",
        "oracle.sandbox",
        "oracle.allowed_executables",
        "autonomy.env_passthrough",
        "autonomy.env_passthrough_prefixes",
        "autonomy.auth_passthrough",
        "autonomy.max_budget_usd_per_phase",
        "autonomy.max_budget_usd_per_run",
        "autonomy.scope_policy",
        "autonomy.credential_scan",
        "protected_paths",
        "git.ref_guard",
    ],
    "topology": {
        "project_spec": "SPEC.md",
        "missions_root": "missions",
        "ledger": "spec/compiled/architecture.yml",
        "lore_graph": "lore_hypergraph.yml",
        "legacy_compiled_dir": "spec/compiled",
    },
    "openrig": {
        "cli_package": "@openrig/cli",
        "accepted_versions": ["0.6.13", "0.7.16"],
        "id_prefix": "OPR",
        "use_rig_when_present": True,
    },
    "models": {
        "opus": "claude-opus-5-5",
        "sonnet": "claude-sonnet-5-5",
        "haiku": "claude-haiku-5-5",
        "fable": "claude-fable-5-1",
    },
    "phase_models": {"execute": "sonnet", "audit": "sonnet"},
    "executors": copy.deepcopy(hx.DEFAULTS["executors"]),
    "tools": copy.deepcopy(hx.DEFAULTS["tools"]),
    "autonomy": {
        **copy.deepcopy(hx.DEFAULTS["autonomy"]),
        "env": {
            "HACF_AUTONOMOUS": "1",
            "HACF_RUN_ID": "{run_id}",
            "HACF_SLICE": "{slice}",
            "HACF_OUTBOX": "<worktree>/.hacf-outbox/",
            "UV_NO_CACHE": "1",
        },
        "credential_scan": True,
        "confidence_threshold": 9,
        "on_gate": "stop_and_record",
        "max_parallel": 3,
        "retries": {"transient": 2, "oracle_or_audit_fail": 1, "backoff_s": [30, 120]},
        "ai_conflict_resolution": False,
    },
    "protected_paths": [
        "spec/compiled/architecture.yml",
        "lore_hypergraph.yml",
        "lore/CANON.md",
        "lore/canon/**",
        "missions/*/mission.yaml",
        "missions/*/slices/*/slice.yaml",
        ".git*",
        ".claude/**",
        ".mcp.json",
        ".agents/skills/**",
        ".agents/scripts/**",
        ".agents/config/**",
        ".agents/local/hacf.yml",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "pyproject.toml",
        "uv.lock",
        "package*.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
        "go.sum",
        "poetry.lock",
    ],
    "oracle": {
        "default": [["uv", "run", "--frozen", "--no-sync", "pytest", "-q"]],
        "prepare": [["uv", "sync", "--frozen"]],
        "allowed_executables": ["uv", "pytest", "python3", "npm", "pnpm", "make", "cargo", "go"],
        "sandbox": "bwrap",
    },
    "git": {
        "base_ref": "HEAD",
        "integration_branch": "swimlane/hacf/{run_id}/integration",
        "slice_branch": "swimlane/hacf/{run_id}/{slice_dir}",
        "worktree_root": "~/.cache/hacf/worktrees/{repo_hash}",
        "keep_failed_worktrees": True,
        "commit_trailers": ["HACF-Run", "HACF-Slice", "HACF-Executor", "HACF-Session"],
        "ref_guard": True,
        "commit_hooks": "base-in-bwrap",
    },
    "code_graph_in_engine": False,
}


class EngineError(Exception):
    """A refusal named by its failure-taxonomy type."""

    def __init__(self, failure_type: str, message: str) -> None:
        """Keep the taxonomy type next to the message."""
        super().__init__(message)
        self.failure_type = failure_type


class LockHeld(Exception):
    """engine.lock is held by another run; holder is its run_id when the lock file names it."""

    def __init__(self, holder: str | None) -> None:
        """Remember the holder."""
        super().__init__(holder or "unknown")
        self.holder = holder


def _warn(message: str) -> None:
    print(f"hyper_engine: warning: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Config (R4)
# ---------------------------------------------------------------------------


def _security_rule(path: tuple[str, ...]) -> str | None:
    """Return the security_keys entry naming a dotted path; the list comes from DEFAULTS only."""
    for rule in DEFAULTS["security_keys"]:
        parts = rule.split(".")
        if len(parts) == len(path) and all(p in ("*", s) for p, s in zip(parts, path)):
            return rule
    return None


def _subset(value: Any, current: Any) -> bool:
    lists = isinstance(value, list) and isinstance(current, list)
    return lists and all(item in current for item in value)


def _tightens(rule: str, current: Any, value: Any) -> bool:
    """Return True when a tracked value for a security key is at least as strict as current."""
    leaf = rule.rsplit(".", 1)[-1]
    if leaf in COMMAND_KEYS:
        return value == current  # a custom command needs a new executor name (R42)
    if leaf in ("ref_guard", "credential_scan"):
        return value is True
    if leaf == "sandbox":
        return value == ("bwrap" if rule == "oracle.sandbox" else "required")
    if leaf == "verified":
        return value is False
    if leaf == "scope_policy":
        return value == "strict"
    if leaf.startswith("max_budget"):
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return number and isinstance(current, (int, float)) and value <= current  # NaN fails
    if leaf == "auth_passthrough":
        return (
            isinstance(value, dict)
            and isinstance(current, dict)
            and all(_subset(v, current.get(k)) for k, v in value.items())
        )
    return _subset(value, current)  # allowed_executables and the passthrough lists


def _merge(base: dict, override: dict, source: str, ignored: list[str], prefix=()) -> None:
    """Merge a tracked override into base; a security key changes only when it tightens."""
    for key, value in override.items():
        path = (*prefix, str(key))
        name = ".".join(path)
        current = base.get(key)
        rule = _security_rule(path)
        if rule == "protected_paths":
            # R42: the shipped list plus any additions; a dropped entry stays.
            listed = value if isinstance(value, list) else []
            dropped = [entry for entry in current if entry not in listed]
            if dropped:
                ignored.append(f"{source}: protected_paths cannot drop {', '.join(dropped)}; kept")
            base[key] = [*current, *(p for p in listed if isinstance(p, str) and p not in current)]
        elif rule is not None:
            # An executor DEFAULTS does not name is experimental, so it may bring its own commands.
            custom = path[0] == "executors" and path[1] not in DEFAULTS["executors"]
            if (custom and path[-1] in COMMAND_KEYS) or _tightens(rule, current, value):
                base[key] = copy.deepcopy(value)
            elif value != current:
                ignored.append(f"{source}: {name} cannot loosen a security key; ignored")
        elif len(path) == 3 and path[0] == "executors" and path[2] == "status":
            # S08: status decides whether an executor needs --allow-unsandboxed: shipped only
            if value != current:
                ignored.append(f"{source}: {name} cannot change the shipped status; ignored")
        elif isinstance(value, dict):
            if not isinstance(current, dict):
                base[key] = current = {}
            _merge(current, value, source, ignored, path)
        elif isinstance(current, dict):
            ignored.append(f"{source}: {name} must be a mapping; ignored")
        else:
            base[key] = copy.deepcopy(value)


def _yaml_mapping(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise EngineError("preflight_failed", f"{path}: cannot read: {exc}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EngineError("preflight_failed", f"{path}: not a mapping")
    return data


def resolve_config(top: Path, flags: dict | None = None) -> tuple[dict, list[str]]:
    """Return the resolved config and the ignored loosening overrides, one message each (R4).

    Order: the embedded DEFAULTS, then .agents/config/orchestration.yml, then the `orchestration`
    mapping of .agents/local/hacf.yml (both tighten-only for security keys), then the CLI flags,
    the only way to loosen. An executor DEFAULTS does not name is experimental.
    """
    flags = {**DEFAULT_FLAGS, **(flags or {})}
    config = copy.deepcopy(DEFAULTS)
    ignored: list[str] = []
    shipped = Path(top) / CONFIG_REL
    if shipped.is_file():
        _merge(config, _yaml_mapping(shipped), CONFIG_REL, ignored)
    else:
        _warn(f"{CONFIG_REL} is missing; using the embedded defaults ({hx.UPGRADE_NOTICE})")
    local = Path(top) / LOCAL_REL
    if local.is_file():
        section = _yaml_mapping(local).get("orchestration") or {}
        if not isinstance(section, dict):
            raise EngineError("preflight_failed", f"{LOCAL_REL}: orchestration is not a mapping")
        _merge(config, section, LOCAL_REL, ignored)
    for name, entry in config["executors"].items():
        if name not in DEFAULTS["executors"] and isinstance(entry, dict):
            entry["status"] = "experimental"
    for message in ignored:
        _warn(message)
    if flags["allow_unsandboxed"]:
        for entry in config["executors"].values():
            if isinstance(entry, dict) and "sandbox" in entry:
                entry["sandbox"] = "off"
    if flags["allow_unsandboxed_oracle"]:
        config["oracle"]["sandbox"] = "none"
    if flags["max_budget_usd_per_run"] is not None:
        config["autonomy"]["max_budget_usd_per_run"] = float(flags["max_budget_usd_per_run"])
    return config, ignored


def security_values(config: dict) -> dict[str, Any]:
    """Return every security key's resolved value by dotted path (executors.* expanded)."""
    values: dict[str, Any] = {}
    for rule in DEFAULTS["security_keys"]:
        head, *rest = rule.split(".")
        if not rest:
            values[rule] = config.get(head)
        elif rest[0] == "*":
            for name, entry in config[head].items():
                if isinstance(entry, dict) and rest[1] in entry:
                    values[f"{head}.{name}.{rest[1]}"] = entry[rest[1]]
        else:
            values[rule] = config[head].get(rest[0])
    return values


# ---------------------------------------------------------------------------
# Probes and git
# ---------------------------------------------------------------------------


def _run_argv(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run an argv list (never a shell string) and capture its output."""
    if not isinstance(argv, list):
        raise TypeError("argv must be a list")
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",  # a diff of a non-UTF-8 file must not crash the engine
        timeout=CHECK_TIMEOUT,
        check=False,
    )


@dataclass
class Probes:
    """The engine's view of the machine; tests replace them, so no claude, bwrap or network."""

    which: Callable[[str], str | None] = shutil.which
    platform: str = sys.platform
    run: Callable[..., subprocess.CompletedProcess] = _run_argv
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    executor: Callable[..., Any] = hx.get_executor  # (name, config, allow_unsandboxed=)


def _base_env() -> dict[str, str]:
    """Return os.environ without GIT_*, so a caller's GIT_DIR never redirects the engine.

    GIT_OPTIONAL_LOCKS=0 keeps read commands from refreshing an index the guard hashes.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


@dataclass
class Tree:
    """A work tree and its own git dir: the target of an engine git call."""

    top: Path
    git_dir: Path

    def env(self) -> dict[str, str]:
        """Return the env of every engine git call: explicit GIT_DIR and GIT_WORK_TREE (R1)."""
        return {**_base_env(), "GIT_DIR": str(self.git_dir), "GIT_WORK_TREE": str(self.top)}


@dataclass
class Repo(Tree):
    """The main checkout: its top level, git dir and common dir (all absolute)."""

    common_dir: Path


def discover(top: Path, probes: Probes) -> Repo:
    """Locate the repository around top; raise EngineError when it is not a git work tree.

    The nearest `.git` above top (a directory or a worktree's file) is the first GIT_DIR, so
    even this call names its git dir (R1).
    """
    start = Path(top).absolute()
    found = next((p for p in (start, *start.parents) if (p / ".git").exists()), None)
    if found is None:
        raise EngineError("preflight_failed", f"{top}: not a git work tree")
    argv = ["rev-parse", "--path-format=absolute", "--show-toplevel", "--absolute-git-dir"]
    proc = _git(Tree(found, found / ".git"), probes, *argv, "--git-common-dir")
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) != 3:
        raise EngineError("preflight_failed", f"{top}: not a git work tree: {proc.stderr.strip()}")
    return Repo(*(Path(line) for line in lines))


_NO_HOOKS: list[str] = []  # the empty hooks dir, made once per process


def _git(tree: Tree, probes: Probes, *args: str) -> subprocess.CompletedProcess:
    """Run one engine git command on tree: no repo hook runs and no auto gc starts (R1, R3)."""
    if not _NO_HOOKS:
        _NO_HOOKS.append(tempfile.mkdtemp(prefix="hacf-no-hooks-"))
        atexit.register(shutil.rmtree, _NO_HOOKS[0], True)
    flags = ["-c", f"core.hooksPath={_NO_HOOKS[0]}", "-c", "gc.auto=0"]
    return probes.run(
        ["git", *flags, "-c", "maintenance.auto=false", *args], cwd=tree.top, env=tree.env()
    )


def _locks_dir(common_dir: Path) -> Path:
    return Path(common_dir) / "hacf" / "locks"


@contextlib.contextmanager
def _flock(path: Path, timeout: float) -> Iterator[None]:
    """Hold an exclusive fcntl lock on path, waiting at most timeout seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise EngineError("internal_error", f"timed out waiting for {path}") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_GIT_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def git_lock(common_dir: Path) -> Iterator[None]:
    """Hold git.lock (fcntl, every process) with its in-process lock (every thread) (LOCKS)."""
    with _GIT_THREAD_LOCK, _flock(_locks_dir(common_dir) / "git.lock", CHECK_TIMEOUT):
        yield


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------


@dataclass
class Slice:
    """One schedulable unit: a v6 slice or a legacy flat MiniPRD."""

    id: str
    path: str
    depends_on: list[str] = field(default_factory=list)
    touches: list[str] | None = None  # None: unknown, so it overlaps every slice
    node_id: str | None = None
    initial: str = "pending"  # audited (done), pending or skipped


def _v6_plan(repo: Repo, probes: Probes) -> list[Slice]:
    """Return the mission slices in Kahn order (hyper_scope.py dag --json, S03b)."""
    argv = [sys.executable, str(SCRIPTS / "hyper_scope.py"), "dag", "--json"]
    proc = probes.run(argv, cwd=repo.top, env=_base_env())
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        message = f"hyper_scope.py dag: {proc.stderr.strip()}"
        raise EngineError("preflight_failed", message) from None
    if report.get("cycle"):
        cycle = " -> ".join(report["cycle"])
        raise EngineError("preflight_failed", f"dependency cycle: {cycle}")
    if proc.returncode != 0:
        problems = "; ".join(report.get("findings") or []) or proc.stderr.strip()
        raise EngineError("preflight_failed", f"hyper_scope.py dag: {problems}")
    plan = []
    for record in report["slices"]:
        front = hyper_spec._front_of(repo.top / record["path"] / "SPEC.md")
        touches = front.get("hacf_touches")
        valid = isinstance(touches, list) and all(isinstance(t, str) for t in touches)
        status = front.get("hacf_status")
        plan.append(
            Slice(
                record["id"],
                record["path"],
                list(record["depends_on"]),
                touches if valid else None,
                front.get("node_id"),
                INITIAL_STATUS.get(status, "skipped") if isinstance(status, str) else "skipped",
            )
        )
    return plan


def _legacy_key(path: Path) -> tuple:
    match = hyper_spec.LEGACY_ORDER_RE.match(path.name)
    return (match is None, int(match.group(1)) if match else 0, path.name)


def _legacy_plan(repo: Repo, compiled: Path) -> list[Slice]:
    """Return the flat MiniPRDs as a chain, by NN then lexically; touches are unknown."""
    files = sorted((p for p in compiled.glob("MiniPRD_*.md") if p.is_file()), key=_legacy_key)
    plan: list[Slice] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        node = hyper_spec._header_value(hyper_ledger.LEGACY_ID_RE.search(text))
        depends = [plan[-1].id] if plan else []
        plan.append(Slice(path.stem, path.relative_to(repo.top).as_posix(), depends, None, node))
    return plan


def build_plan(repo: Repo, config: dict, probes: Probes) -> list[Slice]:
    """Return the run's slices in composition order: v6 missions when any exist, else legacy."""
    try:
        missions = hyper_spec.mission_dirs(hyper_spec.missions_root(repo.top))
    except hyper_spec.SpecError as exc:
        raise EngineError("preflight_failed", str(exc)) from None
    if missions:
        return _v6_plan(repo, probes)
    return _legacy_plan(repo, repo.top / config["topology"]["legacy_compiled_dir"])


def _literal_prefix(glob: str) -> list[str]:
    parts = [part for part in glob.split("/") if part not in ("", ".")]
    for index, part in enumerate(parts):
        if set(part) & set("*?["):
            return parts[:index]
    return parts


def globs_overlap(a: str, b: str) -> bool:
    """Return True unless the literal prefixes before the first wildcard segment diverge.

    Conservative: two globs that might match one path count as overlapping.
    """
    left, right = _literal_prefix(a), _literal_prefix(b)
    size = min(len(left), len(right))
    return left[:size] == right[:size]


def conflicts(a: Slice, b: Slice) -> bool:
    """Return True when two slices must not run at once: shared node_id or overlapping touches."""
    if a.node_id and a.node_id == b.node_id:
        return True
    if a.touches is None or b.touches is None:
        return True
    return any(globs_overlap(x, y) for x in a.touches for y in b.touches)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@dataclass
class Preflight:
    """The preflight report: every check run so far, warnings and what run.json records."""

    ok: bool = True
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    claude_version: str | None = None
    auth_kind: str = "oauth"
    base_sha: str | None = None
    base_ledger_sha256: str | None = None
    base_ledger_bytes: bytes = b""
    plan: list[Slice] = field(default_factory=list)

    def as_json(self) -> dict:
        """Return the printable report (slice ids instead of Slice records)."""
        keys = ("ok", "checks", "warnings", "ignored", "claude_version", "base_sha")
        report = {key: getattr(self, key) for key in keys}
        report["base_ledger_sha256"] = self.base_ledger_sha256
        report["slices"] = [item.id for item in self.plan]
        return report


def _result(name: str, ok: bool, message: str = "", failure: str = "preflight_failed") -> dict:
    return {"name": name, "ok": ok, "failure_type": None if ok else failure, "message": message}


def _loud(report: Preflight, message: str) -> None:
    report.warnings.append(message)
    _warn(message.upper())


def _check_git(repo, config, flags, probes, report) -> Iterator[dict]:
    proc = _git(repo, probes, "worktree", "list")
    yield _result("git_worktree", proc.returncode == 0, proc.stderr.strip())
    proc = _git(repo, probes, "status", "--porcelain", "--untracked-files=no")
    dirty = proc.returncode != 0 or proc.stdout.strip() != ""
    message = "tracked files in the main checkout have uncommitted changes" if dirty else ""
    yield _result("git_clean", not dirty, f"{message}\n{proc.stdout}{proc.stderr}".strip())
    base = config["git"]["base_ref"]
    proc = _git(repo, probes, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    report.base_sha = proc.stdout.strip() or None
    yield _result("git_base", proc.returncode == 0, f"base {base}: {report.base_sha}")


def _check_executor(repo, config, flags, probes, report) -> Iterator[dict]:
    name = flags["executor"]
    allow = bool(flags["allow_unsandboxed"])
    try:
        executor = probes.executor(name, config, allow_unsandboxed=allow)
    except (ValueError, NotImplementedError) as exc:
        yield _result("executor", False, str(exc))
        return
    entry = config["executors"][name]
    if entry.get("status") == "test-only" and os.environ.get("HACF_TEST") != "1":
        message = (
            f"{name} is a test-only executor; it runs only when the environment has HACF_TEST=1, "
            "whatever --unverified-executor says (R41)"
        )
        yield _result("executor", False, message)
        return
    if entry.get("status") == "experimental":
        if not allow:
            message = (
                f"{name} is experimental and has no HACF-verified sandbox; it runs only with "
                "--allow-unsandboxed (R20.2)"
            )
            yield _result("executor", False, message, "sandbox_unavailable")
            return
        _loud(report, f"{name} runs without an OS sandbox (--allow-unsandboxed)")
    try:
        info = executor.preflight()
    except hx.ExecutorError as exc:
        yield _result("executor", False, str(exc), exc.failure_type)
        return
    report.warnings += info.get("warnings") or []
    report.claude_version = info.get("version") if name == "claude-cli" else None
    report.auth_kind = info.get("auth_kind") or "oauth"
    if entry.get("verified") is not True:
        if not flags["unverified_executor"]:
            message = (
                f"executors.{name}.verified is false (its live test has not passed); pass "
                "--unverified-executor to run it anyway (R20.1)"
            )
            yield _result("executor", False, message)
            return
        _loud(report, f"{name} is unverified; running because of --unverified-executor")
    yield _result("executor", True, f"{name} {info.get('version') or ''}".strip())


def _check_agent_sandbox(repo, config, flags, probes, report) -> Iterator[dict]:
    if flags["allow_unsandboxed"]:
        _loud(report, "agent phases run without the OS sandbox (--allow-unsandboxed)")
        yield _result("agent_sandbox", True, "skipped: --allow-unsandboxed")
        return
    tools = AGENT_SANDBOX_TOOLS.get(probes.platform)
    missing = [t for t in tools or () if probes.which(t) is None]
    if tools is None or missing:
        need = f"{', '.join(missing)} on PATH" if tools else f"a sandbox on {probes.platform}"
        message = (
            f"the agent sandbox needs {need} (on Ubuntu 24.04 AppArmor can also block bwrap); "
            "install it or pass --allow-unsandboxed"
        )
        yield _result("agent_sandbox", False, message, "sandbox_unavailable")
        return
    yield _result("agent_sandbox", True)


def _check_oracle_sandbox(repo, config, flags, probes, report) -> Iterator[dict]:
    if flags["allow_unsandboxed_oracle"]:
        message = "oracles and repo hooks run without bwrap, with your privileges and network"
        _loud(report, f"{message} (--allow-unsandboxed-oracle)")
        yield _result("oracle_sandbox", True, "skipped: --allow-unsandboxed-oracle")
        return
    if probes.platform != "linux":
        where = "macOS" if probes.platform == "darwin" else probes.platform
        message = f"{where} has no bwrap; oracles run only with --allow-unsandboxed-oracle (R2)"
        yield _result("oracle_sandbox", False, message, "sandbox_unavailable")
    elif probes.which("bwrap") is None:
        message = (
            "oracle.sandbox is bwrap but bwrap is not on PATH; install bubblewrap or pass "
            "--allow-unsandboxed-oracle (R2)"
        )
        yield _result("oracle_sandbox", False, message, "sandbox_unavailable")
    else:
        yield _result("oracle_sandbox", True)


def _check_oracle_argv(repo, config, flags, probes, report) -> Iterator[dict]:
    allowed = config["oracle"]["allowed_executables"]
    bad = [
        argv
        for argv in config["oracle"]["default"]
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv))
        or argv[0] not in allowed
    ]
    message = f"an oracle argv must start with one of {allowed}: {bad}" if bad else ""
    yield _result("oracle_argv", not bad, message)  # hygiene, not isolation (R2)


def _check_ledger(repo, config, flags, probes, report) -> Iterator[dict]:
    rel = config["topology"]["ledger"]
    path = repo.top / rel
    if not path.is_file():
        message = (
            f"no ledger at {rel}: create one with `python3 .agents/scripts/hyper_ledger.py init`, "
            "or map the codebase with /hyper-discover (R21.6)"
        )
        yield _result("ledger", False, message)
        return
    report.base_ledger_bytes = path.read_bytes()
    report.base_ledger_sha256 = hashlib.sha256(report.base_ledger_bytes).hexdigest()
    yield _result("ledger", True, f"sha256 {report.base_ledger_sha256}")


def _check_validators(repo, config, flags, probes, report) -> Iterator[dict]:
    validators = [
        ("lint", "hyper_spec.py", "lint", "--all"),
        ("ledger_validate", "hyper_ledger.py", "validate"),
    ]
    if (repo.top / config["topology"]["lore_graph"]).is_file():
        validators.append(("lore_validate", "hyper_lore.py", "validate"))
    for name, script, *args in validators:
        argv = [sys.executable, str(SCRIPTS / script), *args]
        try:
            proc = probes.run(argv, cwd=repo.top, env=_base_env())
        except subprocess.TimeoutExpired:
            yield _result(name, False, f"{script} {' '.join(args)} timed out")
            return
        output = f"{proc.stdout}{proc.stderr}".strip()
        yield _result(name, proc.returncode == 0, output[-4000:] if proc.returncode else "")


def _check_dag(repo, config, flags, probes, report) -> Iterator[dict]:
    try:
        report.plan = build_plan(repo, config, probes)
    except EngineError as exc:
        yield _result("dag", False, str(exc))
        return
    yield _result("dag", True, f"{len(report.plan)} slices")


PREFLIGHT_STEPS = (
    _check_git,
    _check_executor,
    _check_agent_sandbox,
    _check_oracle_sandbox,
    _check_oracle_argv,
    _check_ledger,
    _check_validators,
    _check_dag,
)


def preflight(
    repo: Repo, config: dict, flags: dict, probes: Probes, ignored: list[str] = ()
) -> Preflight:
    """Run the ordered preflight checks; the first failure stops it (every one is fatal)."""
    flags = {**DEFAULT_FLAGS, **flags}
    report = Preflight(ignored=list(ignored), warnings=list(ignored))
    for step in PREFLIGHT_STEPS:
        for check in step(repo, config, flags, probes, report):
            report.checks.append(check)
            if not check["ok"]:
                report.ok = False
                return report
    return report


# ---------------------------------------------------------------------------
# Run state, events and the lock
# ---------------------------------------------------------------------------


def new_run_id() -> str:
    """Return `<UTC yyyymmddTHHMMSSZ>-<6 hex>` from _runtime's clock."""
    stamp = _runtime.parse_ts(_runtime.utc_now_iso()).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def run_dir(top: Path, run_id: str) -> Path:
    """Return .agents/state/runs/<run_id>; a malformed run_id is a usage error."""
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"run id {run_id!r} does not match {RUN_ID_RE.pattern}")
    return Path(top) / STATE_REL / "runs" / run_id


def _make_run_dir(top: Path, run_id: str) -> Path:
    state = Path(top) / STATE_REL
    state.mkdir(parents=True, exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    directory = run_dir(top, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_state(directory: Path, state: dict) -> None:
    """Write run.json atomically (temp file, fsync, os.replace, fsync of the directory)."""
    hyper_ledger._atomic_write(directory / "run.json", json.dumps(state, indent=2) + "\n")


def load_state(top: Path, run_id: str) -> dict:
    """Read a run's run.json; its created_at must parse (Z or +00:00)."""
    state = json.loads((run_dir(top, run_id) / "run.json").read_text(encoding="utf-8"))
    _runtime.parse_ts(state["created_at"])
    return state


class EventLog:
    """Append-only events.jsonl: one {at, slice, event, detail} object per line."""

    def __init__(self, path: Path) -> None:
        """Bind the file; writes from runner threads are serialized."""
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, slice_id: str | None, event: str, detail: Any = None) -> None:
        """Append one event stamped with _runtime.utc_now_iso()."""
        line = {"at": _runtime.utc_now_iso(), "slice": slice_id, "event": event, "detail": detail}
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")


@contextlib.contextmanager
def engine_lock(common_dir: Path, holder: str) -> Iterator[Path]:
    """Hold engine.lock under <git-common-dir>/hacf/locks/ (R22.1); raise LockHeld if taken."""
    directory = Path(common_dir) / "hacf" / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "engine.lock"
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            raise LockHeld(handle.read().strip() or None) from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(holder + "\n")
            handle.flush()
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


@dataclass
class SliceOutcome:
    """A SliceRunner's report; failure_type None means the slice is audited and integrated."""

    failure_type: FailureType | str | None = None
    cost_usd: float = 0.0
    detail: str = ""
    phases: list[dict] = field(default_factory=list)  # appended to the slice record's phases
    info: dict = field(default_factory=dict)  # merged into the slice record (worktree, commit...)


class SliceRunner(Protocol):
    """Runs one slice end to end (worktree, phases, oracle, integrate: WorktreeRunner)."""

    def run(self, slice: Slice, ctx: dict) -> SliceOutcome:
        """Run the slice; call ctx['gate'].wait() before every phase (a paused worker waits)."""


class PauseGate:
    """The global rate-limit pause (R20.5): one Event plus a resume time on the injected clock."""

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        """Start unpaused."""
        self._clock, self._sleep = clock, sleep
        self._running = threading.Event()
        self._running.set()
        self._resume_at = 0.0
        self._lock = threading.Lock()  # guards _resume_at
        self._sleeper = threading.Lock()  # one waiter sleeps; the rest then find the pause over

    def pause(self, seconds: float) -> None:
        """Pause every worker until seconds from now (an earlier pause is only extended)."""
        with self._lock:
            self._resume_at = max(self._resume_at, self._clock() + seconds)
            self._running.clear()

    def paused(self) -> bool:
        """Return True while a pause is in force."""
        return not self._running.is_set()

    def wait(self) -> None:
        """Block until no pause is in force."""
        while not self._running.is_set():
            with self._sleeper:
                with self._lock:
                    remaining = self._resume_at - self._clock()
                    if remaining <= 0:
                        self._running.set()
                        return
                self._sleep(remaining)


def ready(plan: list[Slice], records: dict, running: list[Slice], limit: int) -> list[Slice]:
    """Return the slices to start now, in composition order (Tasks 20, 21).

    A slice is ready when it is pending and every depends_on is audited; it starts unless the
    running ones plus those picked already reach limit or one of them conflicts with it.
    """
    busy, picked = list(running), []
    for item in plan:
        if len(busy) >= limit:
            break
        if records[item.id]["status"] != "pending":
            continue
        if not all(records.get(dep, {}).get("status") == "audited" for dep in item.depends_on):
            continue
        if any(conflicts(item, other) for other in busy):
            continue
        busy.append(item)
        picked.append(item)
    return picked


def _attempt(runner: SliceRunner, item: Slice, ctx: dict) -> SliceOutcome:
    try:
        return runner.run(item, ctx)
    except Exception as exc:  # a runner bug aborts the run with its state saved
        return SliceOutcome(FailureType.internal_error, detail=f"{type(exc).__name__}: {exc}")


def _block(plan: list[Slice], records: dict, item: Slice, failure: FailureType, events) -> None:
    """Block item and mark every transitive dependent still pending upstream_blocked."""
    records[item.id].update(status="blocked", failure_type=failure.value)
    events(item.id, "blocked", {"failure_type": failure.value})
    blocked = {item.id}
    for other in plan:  # plan is in dependency order, so one pass reaches every dependent
        if blocked.intersection(other.depends_on):
            blocked.add(other.id)
            if records[other.id]["status"] == "pending":
                records[other.id].update(status="upstream_blocked", failure_type="upstream_blocked")
                events(other.id, "upstream_blocked", {"blocked_by": item.id})


def _settle(plan, state, item, outcome, policy, gate, events) -> None:
    """Apply one finished slice to the run state: cost, then success or the retry policy."""
    record = state["slices"][item.id]
    cost = float(outcome.cost_usd or 0.0)
    record["cost_usd"] += cost
    state["cost_usd"] += cost
    record.update(outcome.info)
    record.setdefault("phases", []).extend(outcome.phases)
    if outcome.failure_type is None:
        record.update(status="audited", failure_type=None)
        events(item.id, "audited", {"cost_usd": cost})
        return
    try:
        failure = FailureType(outcome.failure_type)
    except ValueError:
        failure = FailureType.internal_error
    record["failure_type"] = failure.value
    if failure is FailureType.rate_limited:
        used = state["rate_limit_pauses"]
    else:
        used = record["retries"].get(failure.value, 0)
    action = policy.next_action(failure, used)
    detail = {"action": action.kind, "detail": outcome.detail, "cost_usd": cost}
    events(item.id, failure.value, detail)
    if action.kind == "retry":
        record["retries"][failure.value] = used + 1
        record["status"] = "pending"
    elif action.kind == "pause_all":
        state["rate_limit_pauses"] += 1
        record["status"] = "pending"  # runs again after the pause, never in a retry loop
        gate.pause(action.delay_s)
        events(None, "paused", {"seconds": action.delay_s})
    elif action.kind in ("fatal", "abort"):
        record["status"] = "pending"  # resumable once the cause is fixed
        state["failure_type"] = failure.value
        events(None, "stopped", {"failure_type": failure.value})
    else:  # block, rollback_then_block (the runner rolled the integration back)
        _block(plan, state["slices"], item, failure, events)


def schedule(
    plan: list[Slice],
    state: dict,
    runner: SliceRunner,
    config: dict,
    *,
    gate: PauseGate,
    events: Callable[..., None],
    save: Callable[[], None],
    ctx: dict,
) -> None:
    """Run ready slices through runner until nothing is ready or running (step 3, R20.5).

    The run budget and fatal or internal failures stop new scheduling (in-flight slices finish);
    a rate-limit pause starts nothing until it ends. run.json is saved after every change.
    """
    autonomy = config["autonomy"]
    policy = RetryPolicy(autonomy.get("retries"))
    limit = max(1, int(autonomy.get("max_parallel", 3)))
    budget = float(autonomy["max_budget_usd_per_run"])
    records = state["slices"]
    order = {item.id: index for index, item in enumerate(plan)}
    running: dict[Future, Slice] = {}
    with ThreadPoolExecutor(max_workers=limit) as pool:
        while True:
            if state["failure_type"] is None and state["cost_usd"] >= budget:
                state["failure_type"] = FailureType.budget_exceeded.value
                events(None, "budget_exceeded", {"cost_usd": state["cost_usd"], "budget": budget})
            if state["failure_type"] is None:
                gate.wait()
                for item in ready(plan, records, list(running.values()), limit):
                    record = records[item.id]
                    record["status"] = "running"
                    record["attempts"] += 1
                    events(item.id, "started", {"attempt": record["attempts"]})
                    attempt_ctx = {**ctx, "attempt": record["attempts"]}
                    running[pool.submit(_attempt, runner, item, attempt_ctx)] = item
            save()
            if not running:
                return
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: order[running[f].id]):
                item = running.pop(future)
                _settle(plan, state, item, future.result(), policy, gate, events)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _new_state(run_id: str, pre: Preflight, flags: dict, config: dict) -> dict:
    return {
        "run_id": run_id,
        "created_at": _runtime.utc_now_iso(),
        "status": "running",
        "failure_type": None,
        "base_ref": config["git"]["base_ref"],
        "base_sha": pre.base_sha,
        "base_ledger_sha256": pre.base_ledger_sha256,
        "claude_version": pre.claude_version,
        "flags": flags,
        "security": security_values(config),
        "cost_usd": 0.0,
        "rate_limit_pauses": 0,
        "slices": {},
    }


def _resume_state(state: dict, pre: Preflight, flags: dict, config: dict) -> dict:
    """Keep the recorded base; every slice not yet audited is scheduled again.

    The resume records its own security values; the top-level `security` stays the first
    run's (R42).
    """
    state.update(status="running", failure_type=None)
    entry = {"at": _runtime.utc_now_iso(), "flags": flags, "claude_version": pre.claude_version}
    entry["security"] = security_values(config)
    state.setdefault("resumed", []).append(entry)
    return state


def _report_table(state: dict) -> str:
    """Return the end-of-run table: one row per slice."""
    rows = [("slice", "status", "failure_type", "attempts", "cost_usd", "worktree kept")]
    for sid, record in state["slices"].items():
        kept = record.get("worktree") if record.get("worktree_kept") else "-"
        failure = record.get("failure_type") or "-"
        cost = f"{record['cost_usd']:.2f}"
        rows.append((sid, record["status"], failure, str(record["attempts"]), cost, kept))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows]
    return "\n".join(lines)


def _refused(report: dict) -> int:
    print(json.dumps(report, indent=2))
    return EXIT_REFUSED


def run_engine(
    top: Path,
    flags: dict,
    runner: SliceRunner | None,
    *,
    probes: Probes | None = None,
    resume: str | None = None,
) -> int:
    """Preflight, then schedule every ready slice through runner; return the exit code.

    runner defaults to a WorktreeRunner. --resume refuses (exit 5) when the main checkout's
    ledger differs from the run's base ledger (R5).
    """
    probes = probes or Probes()
    runner = runner if runner is not None else WorktreeRunner()
    flags = {**DEFAULT_FLAGS, **flags}
    try:
        repo = discover(Path(top), probes)
        config, ignored = resolve_config(repo.top, flags)
    except EngineError as exc:
        return _refused({"ok": False, "checks": [_result("config", False, str(exc))]})
    run_id = resume or new_run_id()
    try:
        state = load_state(repo.top, resume) if resume else None
    except (OSError, ValueError, KeyError) as exc:
        print(f"hyper_engine.py: cannot resume {resume}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        with engine_lock(repo.common_dir, run_id):
            pre = preflight(repo, config, flags, probes, ignored)
            if state is not None and pre.base_ledger_sha256 != state.get("base_ledger_sha256"):
                message = (
                    f"the main checkout's ledger (sha256 {pre.base_ledger_sha256}) differs from "
                    f"the run's base ledger ({state.get('base_ledger_sha256')}); --resume "
                    "refuses (R5)"
                )
                pre.checks.append(_result("ledger_drift", False, message))
                pre.ok = False
            if not pre.ok:
                return _refused(pre.as_json())
            directory = _make_run_dir(repo.top, run_id)
            if state is None:
                state = _new_state(run_id, pre, flags, config)
                base = directory / "ledger" / "base.yml"  # land --rebase-ledger's --base-file
                base.parent.mkdir(exist_ok=True)
                base.write_bytes(pre.base_ledger_bytes)
            else:
                state = _resume_state(state, pre, flags, config)
            for item in pre.plan:
                record = state["slices"].setdefault(item.id, {"attempts": 0, "cost_usd": 0.0})
                record.setdefault("retries", {})
                if record.get("status") != "audited":
                    record.update(status=item.initial, failure_type=None)
            events = EventLog(directory / "events.jsonl")
            events(None, "resumed" if resume else "created", {"base_sha": state["base_sha"]})
            ctx = {
                "run_id": run_id,
                "run_dir": directory,
                "repo": repo,
                "config": config,
                "flags": flags,
                "base_sha": state["base_sha"],
                "event": events,
                "plan": pre.plan,
                "auth_kind": pre.auth_kind,
                "probes": probes,
            }
            gate = PauseGate(probes.clock, probes.sleep)
            ctx["gate"] = gate
            try:
                schedule(
                    pre.plan,
                    state,
                    runner,
                    config,
                    gate=gate,
                    events=events,
                    save=lambda: save_state(directory, state),
                    ctx=ctx,
                )
            except Exception as exc:  # internal_error: abort with the state saved, then re-raise
                state["failure_type"] = FailureType.internal_error.value
                events(None, "internal_error", {"error": f"{type(exc).__name__}: {exc}"})
                raise
            finally:
                stopped = state["failure_type"]
                state["status"] = {None: "finished", "internal_error": "aborted"}.get(
                    stopped, "stopped"
                )
                state["ended_at"] = _runtime.utc_now_iso()
                events(None, state["status"], {"failure_type": stopped})
                save_state(directory, state)
    except LockHeld as exc:
        print(f"hyper_engine.py: engine.lock is held by run {exc.holder}", file=sys.stderr)
        return EXIT_LOCKED
    statuses = {sid: record["status"] for sid, record in state["slices"].items()}
    print(json.dumps({"run_id": run_id, "status": state["status"], "slices": statuses}, indent=2))
    print(_report_table(state), file=sys.stderr)
    blocked = any(s in ("blocked", "upstream_blocked") for s in statuses.values())
    return EXIT_BLOCKED if blocked or state["failure_type"] else EXIT_OK


# ---------------------------------------------------------------------------
# Metadata guard (R1)
# ---------------------------------------------------------------------------


def _changes(before: dict, after: dict) -> list[tuple]:
    """Return (kind, name, old, new) for every ref or manifest file that differs."""
    found = []
    for kind in ("files", "refs"):
        old, new = before[kind], after[kind]
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                found.append((kind, name, old.get(name), new.get(name)))
    return found


def _describe(change: tuple) -> str:
    kind, name, old, new = change
    what = "created" if old is None else "deleted" if new is None else "changed"
    if kind == "refs":
        moved = f" ({old[:12]} -> {new[:12]})" if old and new else ""
        return f"ref {name} {what}{moved}"
    return f"git metadata file {name} {what}"


class Guard:
    """The metadata guard (R1; subsystems.md#engine steps b and d).

    watch() snapshots `git for-each-ref` plus the manifest's bytes before an agent phase, oracle
    or hook run; check() compares afterwards, restores every difference and returns it
    (ref_violation). Every engine git write runs through write(), which re-baselines the watched
    snapshots with its own changes, so parallel slices never see each other's engine writes.
    """

    def __init__(self, repo: Repo, probes: Probes, enabled: bool = True) -> None:
        """Bind the main checkout; with enabled False (git.ref_guard off) nothing is checked."""
        self.repo, self.probes, self.enabled = repo, probes, enabled
        self.watched: dict[str, dict] = {}

    def snapshot(self, pointer: Path | None = None) -> dict:
        """Return {refs, files, pointer}; files maps every manifest path to its bytes.

        The manifest: <common>/{HEAD,ORIG_HEAD,MERGE_HEAD,shallow,packed-refs}, <common>/info/*,
        <common>/objects/info/*, every file under <common>/worktrees/ (each worktree's HEAD,
        ORIG_HEAD, MERGE_HEAD and index) and pointer, a slice worktree's .git file.
        """
        proc = _git(self.repo, self.probes, "for-each-ref", "--format=%(objectname) %(refname)")
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git for-each-ref: {proc.stderr.strip()}")
        lines = (line.split(" ", 1) for line in proc.stdout.splitlines() if line)
        refs = {name: sha for sha, name in lines}
        common = self.repo.common_dir
        paths = [common / name for name in ("HEAD", "ORIG_HEAD", "MERGE_HEAD", "shallow")]
        paths.append(common / "packed-refs")
        paths += sorted((common / "info").glob("*")) + sorted((common / "objects/info").glob("*"))
        paths += sorted((common / "worktrees").rglob("*"))
        if pointer is not None:
            paths.append(pointer)
        files = {}
        for path in paths:
            with contextlib.suppress(OSError):  # gone since the listing
                if path.is_file():
                    files[str(path)] = path.read_bytes()
        return {"refs": refs, "files": files, "pointer": pointer}

    def watch(self, key: str, pointer: Path | None = None) -> None:
        """Snapshot before an untrusted step."""
        if self.enabled:
            with git_lock(self.repo.common_dir):
                self.watched[key] = self.snapshot(pointer)

    def check(self, key: str) -> list[str]:
        """Compare key's snapshot with now; restore and return every difference."""
        if not self.enabled:
            return []
        with git_lock(self.repo.common_dir):
            before = self.watched.pop(key)
            after = self.snapshot(before["pointer"])
            changes = _changes(before, after)
            self._restore(changes)
            found = [_describe(change) for change in changes]
            if str(self.repo.common_dir / "objects" / "info" / "alternates") in after["files"]:
                found.append("objects/info/alternates exists (refused outright)")
        return found

    def write(
        self,
        action: Callable[[], Any],
        *,
        expect: tuple = (),
        own: Path | None = None,
        new_admin: bool = False,
    ) -> tuple[Any, list[str]]:
        """Run one engine git write under git.lock; return (its result, unexpected changes).

        The write may move the refs in expect and change files under own (the target
        worktree's admin dir); with new_admin it may also create a new admin dir under
        <common>/worktrees/ (git worktree add). Only those changes re-baseline the watched
        snapshots. Any other change may be an agent phase writing at the same moment: while
        a phase is watched it stays in place for that phase's check (ref_violation there);
        with nothing watched it is restored and returned.
        """
        with git_lock(self.repo.common_dir):
            if not self.enabled:
                return action(), []
            before = self.snapshot()
            result = action()
            changes = _changes(before, self.snapshot())
            admins = self.repo.common_dir / "worktrees"
            known = {
                Path(name).relative_to(admins).parts[0]
                for name in before["files"]
                if Path(name).is_relative_to(admins)
            }
            bad = []
            for change in changes:
                kind, name, old, _ = change
                path = Path(name)
                if kind == "refs":
                    mine = name in expect
                else:
                    mine = own is not None and path.is_relative_to(own)
                    created = new_admin and old is None and path.is_relative_to(admins)
                    mine = mine or (created and path.relative_to(admins).parts[0] not in known)
                if not mine:
                    bad.append(change)
            for snap in self.watched.values():
                for kind, name, _, new in (change for change in changes if change not in bad):
                    if new is None:
                        snap[kind].pop(name, None)
                    else:
                        snap[kind][name] = new
            if self.watched:
                return result, []  # each watching phase's check restores and reports it
            self._restore(bad)
        return result, [_describe(change) for change in bad]

    def _restore(self, changes: list[tuple]) -> None:
        """Put the snapshot's files back first, then its refs."""
        for kind, name, old, _ in sorted(changes, key=lambda change: change[0] != "files"):
            if kind == "refs":
                args = ("update-ref", name, old) if old else ("update-ref", "-d", name)
                _git(self.repo, self.probes, *args)
                continue
            path = Path(name)
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)  # never write through a planted symlink
            if old is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(old)


# ---------------------------------------------------------------------------
# Oracle sandbox (R2, R3, R40)
# ---------------------------------------------------------------------------


def oracle_env(environ: dict, autonomy: dict, kind: str, *, sandboxed: bool) -> dict[str, str]:
    """Return the env of oracles and base-revision hooks (R20.4, R40).

    PATH, LANG, LC_ALL, TMPDIR (/tmp inside bwrap), HOME (masked by bwrap), the auth variables
    of the method in use, and UV_NO_CACHE=1, so uv keeps a throwaway cache in tmp.
    """
    names = ["PATH", "LANG", "LC_ALL", "TMPDIR"]
    env = hx.scrub_env(
        environ, {**autonomy, "env_passthrough": names, "env_passthrough_prefixes": []}, kind
    )
    env["HOME"] = environ.get("HOME") or os.path.expanduser("~")
    if sandboxed:
        env["TMPDIR"] = "/tmp"
    env["UV_NO_CACHE"] = "1"
    return env


def _venv_home(worktree: Path) -> str | None:
    """Return the `home` of <worktree>/.venv/pyvenv.cfg, or None."""
    cfg = worktree / ".venv" / "pyvenv.cfg"
    if not cfg.is_file():
        return None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip() or None
    return None


def bwrap_argv(
    argv: list[str], *, worktree: Path, common_dir: Path, env: dict, writable=()
) -> list[str]:
    """Return argv wrapped in the oracle bwrap profile (R2, R40).

    Read-only root; read-write worktree, tmp and writable; $HOME on a tmpfs except read-only
    binds for the real path of argv[0] on env's PATH and, with .venv/pyvenv.cfg, the parent of
    its home (the interpreter's install prefix: the binary alone cannot find its stdlib). No
    tool cache is bound: a read-only ~/.cache/uv makes `uv run --frozen --no-sync` exit 2. The
    worktree's .git pointer and the git common dir are read-only; no network.
    """
    worktree = Path(worktree)
    readonly = []
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe:
        readonly.append(os.path.realpath(exe))
    home = _venv_home(worktree)
    if home:
        readonly.append(str(Path(home).parent))
    out = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--tmpfs", "/tmp"]
    out += ["--tmpfs", env["HOME"]]
    for path in readonly:
        out += ["--ro-bind", path, path]
    for path in (str(worktree), *map(str, writable)):
        out += ["--bind", path, path]
    pointer = str(worktree / ".git")
    out += ["--ro-bind", pointer, pointer, "--ro-bind", str(common_dir), str(common_dir)]
    return [*out, "--unshare-net", "--die-with-parent", "--chdir", str(worktree), "--", *argv]


# ---------------------------------------------------------------------------
# Outbox, scope and credential checks (R4, R20)
# ---------------------------------------------------------------------------


def outbox_problems(directory: Path) -> list[str]:
    """Return every entry of a moved outbox that the whitelist refuses (R20.3)."""
    problems = []
    for path in sorted(directory.iterdir()):
        name = path.name
        if path.is_symlink() or not path.is_file():
            problems.append(f"{name}: not a regular file")
            continue
        if name in OUTBOX_FILES:
            continue
        if not hyper_ledger.OUTBOX_ENTRY_RE.match(name):
            problems.append(f"{name}: not an outbox entry")
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError):
            entry = None
        tool, op = (entry.get("tool"), entry.get("op")) if isinstance(entry, dict) else (None, None)
        if op in OUTBOX_WHITELIST.get(str(tool), ()):
            continue
        kind = "an approval entry" if op in APPROVAL_OPS else "outside the whitelist"
        problems.append(f"{name}: {tool} {op} is {kind}; rejected")
    return problems


def move_outbox(source: Path, dest: Path) -> list[str]:
    """Move a phase's outbox to dest (replacing it); return what the whitelist refuses."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink() or source.is_file():
        source.unlink()
        dest.mkdir()
        return [f"{OUTBOX_DIR}: not a directory"]
    if source.is_dir():
        shutil.move(str(source), str(dest))
    else:
        dest.mkdir()
    return outbox_problems(dest)


def outbox_tools(directory: Path) -> set:
    """Return the tools that have entries in a moved outbox."""
    if not directory.is_dir():
        return set()
    return {entry.get("tool") for _, entry in hyper_ledger.read_outbox(directory)}


def gate_stops(directory: Path) -> list[dict]:
    """Return the outbox gates.jsonl records that stop the slice (gate_stopped)."""
    path = directory / "gates.jsonl"
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    stops = []
    for line in filter(str.strip, text.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            record = {"gate": "unreadable", "line": line[:200]}
        if record.get("gate") not in NON_STOP_GATES:
            stops.append(record)
    return stops


def _matches(path: str, globs) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def scope_check(paths: list[str], item: Slice, plan: list[Slice], config: dict) -> tuple:
    """Return (violations, warnings) for a slice's changed paths (step f, R4.1, R2).

    Always protected: hyper_executors.PROTECTED_PATHS and every other slice's path. The rest of
    protected_paths (control paths and lockfiles) is protected unless hacf_touches lists the
    path, under every scope_policy. Other paths outside hacf_touches warn, or fail under strict.
    The outbox path is exempt.
    """
    always = list(hx.PROTECTED_PATHS)
    for other in plan:
        if other.id != item.id:
            always += [other.path, f"{other.path}/**"]
    control = [p for p in config["protected_paths"] if p not in hx.PROTECTED_PATHS]
    touches = item.touches
    strict = config["autonomy"]["scope_policy"] == "strict"
    violations, warnings = [], []
    for path in paths:
        if _matches(path, (OUTBOX_DIR, f"{OUTBOX_DIR}/*")):
            continue
        if _matches(path, always):
            violations.append(f"{path}: always protected")
        elif _matches(path, control) and not _matches(path, touches or ()):
            violations.append(f"{path}: a protected control path outside hacf_touches")
        elif touches is not None and not _matches(path, touches):
            (violations if strict else warnings).append(f"{path}: outside hacf_touches")
    return violations, warnings


def credential_hits(diff: str) -> list[str]:
    """Return '<file>:<line>: <shape>' per credential shape on an added line of a -U0 diff.

    The report never holds the matched value (R20.4).
    Every added line is scanned: a `+++` name git quotes (a non-ASCII byte, a quote, a
    backslash or a control character) is decoded, and a name that cannot be read is
    reported as `(unreadable path)`.
    """
    hits, path, number = [], "(unreadable path)", 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = _diff_path(line[4:]) or "(unreadable path)"
        elif line.startswith("@@"):
            match = re.match(r"@@ -\S+ \+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif line.startswith("+"):
            hits += [
                f"{path}:{number}: {label}" for label, rx in CREDENTIAL_SHAPES if rx.search(line)
            ]
            number += 1
    return hits


_C_ESCAPES = {b"a": 7, b"b": 8, b"t": 9, b"n": 10, b"v": 11, b"f": 12, b"r": 13}


def _diff_path(spec: str) -> str | None:
    """Return the path of a diff `+++` header (b/ stripped, C-quoting decoded), or None."""
    spec = spec.rstrip("\t")  # git ends a name holding a space with a tab
    if len(spec) >= 2 and spec[0] == spec[-1] == '"':

        def unescape(match: re.Match) -> bytes:
            code = match.group(1)
            if len(code) == 3:
                return bytes([int(code, 8) & 0xFF])
            return bytes([_C_ESCAPES[code]]) if code in _C_ESCAPES else code

        raw = re.sub(rb"\\([0-7]{3}|.)", unescape, spec[1:-1].encode("utf-8"))
        spec = raw.decode("utf-8", errors="replace")
    if spec.startswith("b/"):
        return spec[2:]
    return None if spec == "/dev/null" else spec


# ---------------------------------------------------------------------------
# Worktree runner (RUN LIFECYCLE steps 2 and 4a-k)
# ---------------------------------------------------------------------------


def _unexpected(bad: list[str]) -> None:
    """Refuse an engine git write that changed git metadata it does not own (R1)."""
    if bad:
        raise EngineError("ref_violation", "; ".join(bad))


class SliceFailed(Exception):
    """A slice step failed with one failure-taxonomy type."""

    def __init__(self, failure_type: FailureType, detail: str) -> None:
        """Keep the taxonomy type next to the detail."""
        super().__init__(detail)
        self.failure_type = failure_type


@dataclass
class _Attempt:
    """One attempt's report: cost, phase records, slice-record fields and its worktree."""

    cost: float = 0.0
    phases: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    tree: Tree | None = None


def _legacy(item: Slice) -> bool:
    return item.path.endswith(".md")


def _slice_dir(item: Slice) -> str:
    """Return a v6 slice's <NN-slug>, or a legacy MiniPRD's file stem."""
    return Path(item.path).stem if _legacy(item) else Path(item.path).name


def worktree_root(repo: Repo, config: dict) -> Path:
    """Return git.worktree_root with {repo_hash} filled from the git common dir."""
    digest = hashlib.sha256(str(repo.common_dir).encode("utf-8")).hexdigest()[:12]
    return Path(os.path.expanduser(config["git"]["worktree_root"].format(repo_hash=digest)))


def _admin_dir(repo: Repo, path: Path) -> Path:
    """Return the admin dir of the linked worktree at path.

    It comes from the common dir's gitdir records, never from the worktree's own .git file,
    which an agent could rewrite.
    """
    want = os.path.realpath(path)
    for record in sorted((repo.common_dir / "worktrees").glob("*/gitdir")):
        text = record.read_text(encoding="utf-8").strip()
        if os.path.realpath(os.path.dirname(text)) == want:
            return record.parent
    raise EngineError("internal_error", f"{path} is not a worktree of {repo.top}")


def _copy(source: Path, target: Path, mode: int | None = None) -> None:
    """Copy source over target (a read-only target too); mode then applies to the copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    shutil.copyfile(source, target)
    if mode is not None:
        target.chmod(mode)


def _write_mode(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.write_bytes(data)
    target.chmod(mode)


def _tail(proc: Any) -> str:
    return f"{proc.stdout}{proc.stderr}".strip()[-2000:]


class WorktreeRunner:
    """The SliceRunner of a real run: one git worktree per slice (steps 4a-k).

    The first slice of a run sets up the guard, the integration worktree, base/ and the staged
    ledger. A failing step raises SliceFailed, which becomes the outcome's failure_type; the
    worktree is then kept (git.keep_failed_worktrees), and removed after a success.
    """

    def __init__(self) -> None:
        """Start without a run."""
        self._setup_lock = threading.Lock()
        self._integrate_lock = threading.Lock()  # the integrator takes one slice at a time
        self._run_id: str | None = None

    # -- run setup ---------------------------------------------------------

    def _setup(self, ctx: dict) -> None:
        with self._setup_lock:
            if self._run_id == ctx["run_id"]:
                return
            self.repo, self.config, self.probes = ctx["repo"], ctx["config"], ctx["probes"]
            self.guard = Guard(self.repo, self.probes, self.config["git"]["ref_guard"] is True)
            self.root = worktree_root(self.repo, self.config) / ctx["run_id"]
            self.base_dir = ctx["run_dir"] / "base"
            self.branch = self.config["git"]["integration_branch"].format(run_id=ctx["run_id"])
            start = ctx["base_sha"]
            self.integration = self._worktree(self.branch, self.root / "integration", start)
            if not self.base_dir.is_dir():
                self._materialize_base(ctx)
            self.ledger_rel = self.config["topology"]["ledger"]
            self.ledger_tracked = self._tracked(self.ledger_rel)
            self.staged = ctx["run_dir"] / "ledger" / Path(self.ledger_rel).name
            if not self.ledger_tracked:  # staged in the run dir until land (R5)
                if not self.staged.is_file():
                    _copy(ctx["run_dir"] / "ledger" / "base.yml", self.staged)
                _copy(self.staged, self.integration.top / self.ledger_rel)
            flags = ctx["flags"]
            allow = bool(flags["allow_unsandboxed"])
            self.executor = self.probes.executor(
                flags["executor"], self.config, allow_unsandboxed=allow
            )
            self._run_id = ctx["run_id"]

    def _tracked(self, rel: str) -> bool:
        return (
            _git(self.repo, self.probes, "ls-files", "--error-unmatch", "--", rel).returncode == 0
        )

    def _rev(self, ref: str) -> str:
        proc = _git(self.repo, self.probes, "rev-parse", "--verify", ref)
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git rev-parse {ref}: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _materialize_base(self, ctx: dict) -> None:
        """Write base/ from the base revision, read-only (steps 4a and g; R3, R4).

        AGENTS.md, the SKILL.md files the prompts name and the HACF hook script come from the
        base commit; the repo's pre-commit and commit-msg hooks go to base/hooks/.
        """
        missing = []
        for rel in BASE_FILES:
            proc = _git(self.repo, self.probes, "show", f"{ctx['base_sha']}:{rel}")
            if proc.returncode != 0:
                missing.append(rel)
            else:
                _write_mode(self.base_dir / rel, proc.stdout.encode("utf-8"), 0o444)
        for hook, data in self._base_hooks(ctx["base_sha"]).items():
            _write_mode(self.base_dir / "hooks" / hook, data, 0o555)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        ctx["event"](None, "base_materialized", {"missing": missing})

    def _base_hooks(self, base_sha: str) -> dict[str, bytes]:
        """Return the repo's pre-commit and commit-msg hooks as of the base (R3).

        A hook tracked at the base comes from the base commit; an untracked one
        (<common>/hooks, or a core.hooksPath outside the repo) is copied as it is now
        (Clarifying question 1).
        """
        argv = ["git", "config", "--get", "core.hooksPath"]  # not _git: that overrides it
        value = self.probes.run(argv, cwd=self.repo.top, env=self.repo.env()).stdout.strip()
        hooks = Path(os.path.expanduser(value)) if value else self.repo.common_dir / "hooks"
        hooks = hooks if hooks.is_absolute() else self.repo.top / hooks
        found = {}
        for hook in HOOKS:
            path = hooks / hook
            try:
                rel = path.resolve().relative_to(self.repo.top.resolve()).as_posix()
            except ValueError:
                rel = None
            listed = (
                _git(self.repo, self.probes, "ls-tree", base_sha, "--", rel).stdout if rel else ""
            )
            if listed:
                if listed.startswith("100755 "):  # git runs only an executable hook
                    shown = _git(self.repo, self.probes, "show", f"{base_sha}:{rel}")
                    found[hook] = shown.stdout.encode("utf-8")
            elif path.is_file() and os.access(path, os.X_OK):
                found[hook] = path.read_bytes()
        return found

    def _worktree(self, branch: str, path: Path, start: str) -> Tree:
        """Return the worktree at path on branch; add it first (from start, or the branch)."""
        if path.is_dir():
            with contextlib.suppress(EngineError):
                return Tree(path, _admin_dir(self.repo, path))
        known = _git(self.repo, self.probes, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
        args = [str(path), branch] if known.returncode == 0 else ["-b", branch, str(path), start]
        add = lambda: _git(self.repo, self.probes, "worktree", "add", *args)  # noqa: E731
        proc, bad = self.guard.write(add, expect=(f"refs/heads/{branch}",), new_admin=True)
        _unexpected(bad)
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git worktree add {path}: {_tail(proc)}")
        return Tree(path, _admin_dir(self.repo, path))

    def _remove(self, tree: Tree) -> None:
        """Remove a worktree; its branch stays until gc."""
        argv = ("worktree", "remove", "--force", str(tree.top))
        _, bad = self.guard.write(lambda: _git(self.repo, self.probes, *argv), own=tree.git_dir)
        _unexpected(bad)

    # -- one slice -----------------------------------------------------------

    def run(self, item: Slice, ctx: dict) -> SliceOutcome:
        """Run steps 4a-k for one slice and return its outcome."""
        attempt = _Attempt()
        try:
            self._setup(ctx)
            self._steps(item, ctx, attempt)
        except (SliceFailed, EngineError) as exc:
            failure = FailureType(exc.failure_type)
            detail = {"failure_type": failure.value, "detail": str(exc)}
            ctx["event"](item.id, "slice_failed", detail)
            if attempt.tree is not None:
                if self.config["git"]["keep_failed_worktrees"] is not False:
                    attempt.info["worktree_kept"] = True
                else:
                    self._remove(attempt.tree)
            return SliceOutcome(failure, attempt.cost, str(exc), attempt.phases, attempt.info)
        return SliceOutcome(None, attempt.cost, "", attempt.phases, attempt.info)

    def _steps(self, item: Slice, ctx: dict, attempt: _Attempt) -> None:
        name = _slice_dir(item)
        branch = self.config["git"]["slice_branch"].format(run_id=ctx["run_id"], slice_dir=name)
        path = self.root / name
        fresh = not path.is_dir()
        tree = attempt.tree = self._worktree(branch, path, self._rev(f"refs/heads/{self.branch}"))
        attempt.info.update(worktree=str(path), branch=branch, worktree_kept=False)
        if not fresh:  # a kept worktree restarts from its last engine commit
            self._reset(tree, f"refs/heads/{branch}")
        copies = self._copy_inputs(item, tree)
        outbox = ctx["run_dir"] / "outbox" / item.id
        shutil.rmtree(outbox, ignore_errors=True)  # only the last attempt's outboxes integrate
        if fresh:
            self._prepare(item, tree, ctx)
        ctx["gate"].wait()
        execute = self._agent_phase("execute", item, tree, ctx, attempt)  # c, d
        self._collect(item, tree, outbox / "execute", ctx)  # e
        refs = (f"refs/heads/{branch}", f"refs/heads/{self.branch}")
        base = _git(self.repo, self.probes, "merge-base", *refs)
        self._stage_and_scope(item, tree, base.stdout.strip(), copies, ctx)  # f
        message = self._message(item, ctx, "commit", f"hacf: {item.id}", execute.session_id)
        self._hooks(item, tree, ctx, message)  # g
        commit = attempt.info["commit"] = self._commit(item, tree, branch, message, ctx)
        self._oracle(item, tree, ctx)  # h
        before = self._dirty(tree, copies)
        ctx["gate"].wait()
        self._agent_phase("audit", item, tree, ctx, attempt)  # i
        record = self._collect(item, tree, outbox / "audit", ctx)
        after = self._dirty(tree, copies)
        left = [p for p in sorted({*before, *after}) if before.get(p, "") != after.get(p, "")]
        if left:  # R4: the audit changed something besides its outbox
            ctx["event"](item.id, "dirty_after_audit", {"paths": left})
            raise SliceFailed(FailureType.scope_violation, f"the audit left changes: {left}")
        self._verdict(item, record, ctx)
        self._proof(item, tree, branch, commit, record, execute, ctx)
        attempt.info["candidate_ref"] = self._candidate(item, commit, ctx)
        with self._integrate_lock:  # j
            attempt.info.update(self._integrate(item, branch, outbox, execute.session_id, ctx))
        self._remove(tree)  # k

    def _phase_dir(self, item: Slice, ctx: dict, phase: str) -> Path:
        path = ctx["run_dir"] / "phases" / f"{item.id}.{ctx['attempt']}.{phase}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _punch_file(self, item: Slice, ctx: dict) -> Path:
        return ctx["run_dir"] / "punch" / f"{item.id}.md"

    def _write_punch(self, item: Slice, ctx: dict, text: str) -> None:
        path = self._punch_file(item, ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _copy_inputs(self, item: Slice, tree: Tree) -> list[str]:
        """Copy untracked inputs into the worktree: a legacy MiniPRD and a read-only ledger."""
        copies = []
        if _legacy(item) and not self._tracked(item.path):
            _copy(self.repo.top / item.path, tree.top / item.path)
            copies.append(item.path)
        if not self.ledger_tracked:
            _copy(self.staged, tree.top / self.ledger_rel, 0o444)
            copies.append(self.ledger_rel)
        return copies

    def _excludes(self, tree: Tree, paths: list[str]) -> list[str]:
        """Return `git add` exclude pathspecs for the paths git does not ignore already.

        git add refuses an explicit pathspec that names an ignored file.
        """
        check = ("check-ignore", "-q", "--no-index", "--")
        shown = [p for p in paths if _git(tree, self.probes, *check, p).returncode != 0]
        return [f":(exclude){path}" for path in shown]

    def _spawn(self, key: str, tree: Tree, argv: list[str], env: dict) -> tuple:
        """Run argv in tree under the guard; a timeout kills its process group (R20)."""
        autonomy = self.config["autonomy"]
        timeout, grace = float(autonomy["phase_timeout_s"]), float(autonomy["kill_grace_s"])
        self.guard.watch(key, tree.top / ".git")
        try:
            completed = hx.run(argv, env=env, cwd=tree.top, timeout=timeout, kill_grace_s=grace)
        except OSError as exc:  # the executable is missing
            completed = hx.Completed(list(argv), 127, "", str(exc))
        return completed, self.guard.check(key)

    def _violation(self, item: Slice, ctx: dict, found: list[str]) -> None:
        if found:
            ctx["event"](item.id, "guard_differences", {"differences": found})
            raise SliceFailed(FailureType.ref_violation, "; ".join(found))

    def _prepare(self, item: Slice, tree: Tree, ctx: dict) -> None:
        """Run oracle.prepare engine-side in a fresh worktree, whose lockfile is the base's (R2)."""
        env = hx.scrub_env(dict(os.environ), self.config["autonomy"], ctx["auth_kind"])
        for argv in self.config["oracle"]["prepare"]:
            completed, found = self._spawn(f"{item.id}:prepare", tree, list(argv), env)
            self._violation(item, ctx, found)
            if completed.returncode != 0:
                detail = f"oracle.prepare {argv}: {_tail(completed)}"
                raise SliceFailed(FailureType.oracle_failed, detail)

    def _agent_phase(
        self, phase: str, item: Slice, tree: Tree, ctx: dict, attempt: _Attempt
    ) -> hx.ExecResult:
        """Run one agent phase (c or i) through the executor in tree, guarded (d)."""
        config, phase_dir = self.config, self._phase_dir(item, ctx, phase)
        outbox = tree.top / OUTBOX_DIR
        shutil.rmtree(outbox, ignore_errors=True)
        outbox.mkdir()
        punch = self._punch_file(item, ctx)
        punch = (
            punch.read_text(encoding="utf-8") if phase == "execute" and punch.is_file() else None
        )
        prompt = phase_dir / "autonomy.md"
        prompt.write_text(self._autonomy_prompt(phase, item, ctx, punch), encoding="utf-8")
        model = config["phase_models"][phase]
        pctx = {
            "worktree": str(tree.top),
            "git_common_dir": str(self.repo.common_dir),
            "base_dir": str(self.base_dir),
            "slice_spec": item.path if _legacy(item) else f"{item.path}/SPEC.md",
            "model": config["models"].get(model, model),
            "autonomy_prompt_file": str(prompt),
            "run_id": ctx["run_id"],
            "slice": item.id,
            "outbox": f"{outbox}/",
            "protected_paths": self._protected(item),
            "scenario": "success",  # the stub's argv names one; tests choose others
            "last_message_file": str(phase_dir / "last_message.txt"),
        }
        argv = self.executor.prepare(phase, pctx, phase_dir)
        env = hx.scrub_env(dict(os.environ), config["autonomy"], ctx["auth_kind"], pctx)
        if punch is not None:
            env["HACF_ALLOW_REIMPLEMENT"] = "1"
        completed, found = self._spawn(f"{item.id}:{phase}", tree, argv, env)
        raw = phase_dir / "result.json"
        raw.write_text(completed.stdout, encoding="utf-8")
        result = self.executor.parse_result(completed)
        result.raw_ref = str(raw)
        record = {
            "attempt": ctx["attempt"],
            "phase": phase,
            "session_id": result.session_id,
            "num_turns": result.num_turns,
            "cost_usd": result.cost_usd,
            "failure_type": result.failure_type,
        }
        attempt.phases.append(record)
        attempt.cost += float(result.cost_usd or 0.0)
        ctx["event"](item.id, phase, record)
        self._violation(item, ctx, found)
        failure = classify(result)
        if failure is not None:
            raise SliceFailed(failure, (result.result_text or "")[-2000:])
        return result

    def _autonomy_prompt(self, phase: str, item: Slice, ctx: dict, punch: str | None) -> str:
        lines = [
            f"You run the {phase} phase of HACF engine run {ctx['run_id']}, slice {item.id}.",
            "Nobody can answer a question: follow the skill's Autonomous lines. The scripts send "
            "ledger, lore and progress writes to $HACF_OUTBOX.",
            "Never write git metadata, commit, push or switch branches: the engine performs "
            "every git write.",
        ]
        if punch is not None:
            lines += ["", "The previous attempt failed. Fix this punch list:", "", punch]
        return "\n".join(lines) + "\n"

    def _protected(self, item: Slice) -> list[str]:
        """Return the settings' Edit denies for a slice.

        The always-protected paths plus the control paths no hacf_touches glob overlaps; the
        scope check is the exact gate.
        """
        control = [p for p in self.config["protected_paths"] if p not in hx.PROTECTED_PATHS]
        touches = item.touches or ()
        free = [p for p in control if not any(globs_overlap(p, t) for t in touches)]
        return [*hx.PROTECTED_PATHS, *free]

    def _collect(self, item: Slice, tree: Tree, dest: Path, ctx: dict) -> dict | None:
        """Step e: move the outbox out of the worktree; return the phase's audit.json record.

        Entries the whitelist does not allow are scope_violation; a stop gate is gate_stopped.
        """
        problems = move_outbox(tree.top / OUTBOX_DIR, dest)
        if problems:
            ctx["event"](item.id, "outbox_refused", {"entries": problems})
            raise SliceFailed(FailureType.scope_violation, "outbox: " + "; ".join(problems))
        stops = gate_stops(dest)
        if stops:
            ctx["event"](item.id, "gate_stop", {"gates": stops})
            names = ", ".join(str(stop.get("gate")) for stop in stops)
            raise SliceFailed(FailureType.gate_stopped, names)
        audit = dest / "audit.json"
        try:
            record = json.loads(audit.read_text(encoding="utf-8")) if audit.is_file() else None
        except (UnicodeDecodeError, ValueError):
            record = None
        return record if isinstance(record, dict) else None

    def _stage_and_scope(
        self, item: Slice, tree: Tree, base: str, copies: list[str], ctx: dict
    ) -> None:
        """Step f: stage the slice, then check scope and scan for credentials.

        Both run over the staged diff against the slice's base.
        """
        excludes = self._excludes(tree, [OUTBOX_DIR, *copies])
        proc, bad = self.guard.write(
            lambda: _git(tree, self.probes, "add", "-A", "--", ".", *excludes), own=tree.git_dir
        )
        self._violation(item, ctx, bad)
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git add: {_tail(proc)}")
        diff = ("diff", "--cached", "--no-renames", "--no-ext-diff", "--no-textconv")
        names = _git(tree, self.probes, *diff, "--name-only", "-z", base).stdout.split("\0")
        found = scope_check([n for n in names if n], item, ctx["plan"], self.config)
        violations, warnings = found
        if warnings:
            ctx["event"](item.id, "scope_warning", {"paths": warnings})
        if self.config["autonomy"]["credential_scan"] is not False:
            unquoted = ("-c", "core.quotePath=false", *diff)  # credential_hits decodes the rest
            text = _git(tree, self.probes, *unquoted, "-U0", "--no-color", base).stdout
            violations += [f"{hit} (credential shape)" for hit in credential_hits(text)]
        if violations:
            ctx["event"](item.id, "scope_check", {"violations": violations})
            raise SliceFailed(FailureType.scope_violation, "; ".join(violations))

    def _message(self, item: Slice, ctx: dict, kind: str, title: str, session: Any) -> Path:
        """Write a commit message with the git.commit_trailers to a file; return it."""
        values = {
            "HACF-Run": ctx["run_id"],
            "HACF-Slice": item.id,
            "HACF-Executor": ctx["flags"]["executor"],
            "HACF-Session": session or "none",
        }
        names = [name for name in self.config["git"]["commit_trailers"] if name in values]
        path = self._phase_dir(item, ctx, kind) / "message.txt"
        path.write_text(title + "\n\n" + "".join(f"{n}: {values[n]}\n" for n in names))
        return path

    def _sandboxed(
        self, key: str, item: Slice, tree: Tree, argv: list[str], ctx: dict, extra=None, rw=()
    ) -> hx.Completed:
        """Run argv in the oracle bwrap profile.

        It runs unsandboxed only under the CLI flag --allow-unsandboxed-oracle (R2).
        """
        sandboxed = self.config["oracle"]["sandbox"] != "none"
        env = oracle_env(
            dict(os.environ), self.config["autonomy"], ctx["auth_kind"], sandboxed=sandboxed
        )
        env.update(extra or {})
        if sandboxed:
            if self.probes.which("bwrap") is None:
                message = "bwrap is not on PATH; oracles and repo hooks need it (R2)"
                raise SliceFailed(FailureType.sandbox_unavailable, message)
            common = self.repo.common_dir
            argv = bwrap_argv(argv, worktree=tree.top, common_dir=common, env=env, writable=rw)
        ctx["event"](item.id, "sandboxed_run", {"argv": argv})
        completed, found = self._spawn(key, tree, argv, env)
        self._violation(item, ctx, found)
        return completed

    def _hooks(self, item: Slice, tree: Tree, ctx: dict, message: Path) -> None:
        """Step g, first: the base's pre-commit and commit-msg hooks in the oracle profile (R3)."""
        git_env = {"GIT_DIR": str(tree.git_dir), "GIT_WORK_TREE": str(tree.top)}
        for hook in HOOKS:
            path = self.base_dir / "hooks" / hook
            if not path.is_file():
                continue
            argv = [str(path), *([str(message)] if hook == "commit-msg" else [])]
            completed = self._sandboxed(
                f"{item.id}:{hook}", item, tree, argv, ctx, git_env, [message]
            )
            if completed.returncode != 0:
                detail = f"the base {hook} hook exited {completed.returncode}: {_tail(completed)}"
                raise SliceFailed(FailureType.commit_rejected, detail)

    def _commit(self, item: Slice, tree: Tree, branch: str, message: Path, ctx: dict) -> str:
        """Step g: the engine commit, hooks off (R3); the guard allows only the branch move."""
        ref = f"refs/heads/{branch}"
        argv = ("commit", "-q", "--no-verify", "--allow-empty", "-F", str(message))
        proc, bad = self.guard.write(
            lambda: _git(tree, self.probes, *argv), expect=(ref,), own=tree.git_dir
        )
        self._violation(item, ctx, bad)
        if proc.returncode != 0:
            raise SliceFailed(FailureType.commit_rejected, f"git commit: {_tail(proc)}")
        return self._rev(ref)

    def _oracle(self, item: Slice, tree: Tree, ctx: dict) -> None:
        """Step h: each oracle argv in the bwrap profile; a failure leaves a punch list."""
        for argv in self.config["oracle"]["default"]:
            completed = self._sandboxed(f"{item.id}:oracle", item, tree, list(argv), ctx)
            ctx["event"](item.id, "oracle", {"argv": argv, "exit": completed.returncode})
            if completed.returncode != 0:
                text = f"The oracle `{' '.join(argv)}` exited {completed.returncode}:\n\n"
                self._write_punch(item, ctx, text + _tail(completed) + "\n")
                detail = f"the oracle {argv} exited {completed.returncode}"
                raise SliceFailed(FailureType.oracle_failed, detail)

    def _dirty(self, tree: Tree, copies: list[str]) -> dict[str, str]:
        """Return each changed or untracked path (copied inputs aside) with its sha256."""
        argv = ("status", "--porcelain=v1", "-z", "-uall", "--no-renames")
        proc = _git(tree, self.probes, *argv)
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git status: {_tail(proc)}")
        found = {}
        for path in (entry[3:] for entry in proc.stdout.split("\0")):
            if path and path not in copies:
                target = tree.top / path
                regular = target.is_file() and not target.is_symlink()
                found[path] = hashlib.sha256(target.read_bytes()).hexdigest() if regular else "-"
        return found

    def _verdict(self, item: Slice, record: dict | None, ctx: dict) -> None:
        """Take the verdict only from the moved outbox audit.json (R1).

        A FAIL leaves its punch list for the next attempt.
        """
        verdict = str((record or {}).get("verdict") or "MISSING")
        if classify(audit_verdict=verdict) is None:
            return
        entries = (record or {}).get("punch_list") or ["(no punch list)"]
        entries = entries if isinstance(entries, list) else [entries]
        lines = "".join(f"- {entry}\n" for entry in entries)
        self._write_punch(item, ctx, f"The audit verdict was {verdict}. Punch list:\n\n{lines}")
        raise SliceFailed(FailureType.audit_failed, f"audit verdict {verdict}")

    def _script(self, tree: Tree, failure: FailureType, script: str, *args: str) -> None:
        """Run one HACF script in tree, never into an outbox; a failure raises failure."""
        env = {k: v for k, v in tree.env().items() if k not in ("HACF_OUTBOX", "HACF_AUTONOMOUS")}
        proc = self.probes.run(
            [sys.executable, str(SCRIPTS / script), *args], cwd=tree.top, env=env
        )
        if proc.returncode != 0:
            raise SliceFailed(failure, f"{script} {' '.join(args)}: {_tail(proc)}")

    def _proof(self, item, tree, branch, commit, record, execute, ctx) -> None:
        """After an audit PASS, write and commit the slice's PROGRESS, PROOF and proof file.

        The engine stages only those files (R1, R4); a legacy flat MiniPRD has none.
        """
        if _legacy(item):
            notice = hyper_spec.LEGACY_PROGRESS_NOTICE
            print(f"hyper_engine.py: {item.id}: {notice}", file=sys.stderr)
            ctx["event"](item.id, "progress_skipped", {"notice": notice})
            return
        phase_dir = self._phase_dir(item, ctx, "proof")
        status = hyper_spec._front_of(tree.top / item.path / "SPEC.md").get("hacf_status")
        detail = phase_dir / "progress.txt"
        detail.write_text(
            f"commit {commit}, executor {ctx['flags']['executor']} (session "
            f"{execute.session_id}, turns {execute.num_turns}, cost {execute.cost_usd})\n"
        )
        events = [["executing"]] if status in ("compiled", "audit_failed", "blocked") else []
        for args in [*events, ["executed", "--detail-file", str(detail)]]:
            progress = ("progress", "--slice", item.path, "--event", *args)
            self._script(tree, FailureType.internal_error, "hyper_spec.py", *progress)
        name = f"{len(list((tree.top / item.path / 'proof').glob('*.md'))) + 1:02d}-qa.md"
        notes = [n for n in (record or {}).get("notes") or [] if isinstance(n, str)]
        body = phase_dir / "proof.md"
        body.write_text("Engine audit PASS.\n\n" + "".join(f"- {n}\n" for n in notes))
        money = str((record or {}).get("money_evidence") or "").strip().split("\n")[0]
        proof = ("proof", "--slice", item.path, "--artifact-type", "qa", "--verdict", "PASS")
        proof += ("--candidate-sha", commit, "--money-evidence", money or "engine audit PASS")
        self._script(
            tree,
            FailureType.internal_error,
            "hyper_spec.py",
            *proof,
            "--file",
            str(body),
            "--name",
            name,
        )
        files = [
            f"{item.path}/{n}" for n in ("SPEC.md", "PROGRESS.md", "PROOF.md", f"proof/{name}")
        ]
        files = [rel for rel in files if (tree.top / rel).is_file()]
        message = self._message(
            item, ctx, "proof", f"hacf: {item.id} progress and proof", execute.session_id
        )

        def act() -> Any:
            added = _git(tree, self.probes, "add", "--", *files)
            argv = ("commit", "-q", "--no-verify", "-F", str(message))
            return added if added.returncode else _git(tree, self.probes, *argv)

        proc, bad = self.guard.write(act, expect=(f"refs/heads/{branch}",), own=tree.git_dir)
        self._violation(item, ctx, bad)
        if proc.returncode != 0:
            raise SliceFailed(FailureType.commit_rejected, f"progress and proof: {_tail(proc)}")

    def _candidate(self, item: Slice, commit: str, ctx: dict) -> str:
        """Write refs/hacf/candidates/<slice id> at the step g commit (R1)."""
        ref = f"refs/hacf/candidates/{item.id}"
        action = lambda: _git(self.repo, self.probes, "update-ref", ref, commit)  # noqa: E731
        proc, bad = self.guard.write(action, expect=(ref,))
        self._violation(item, ctx, bad)
        if proc.returncode != 0:
            raise EngineError("internal_error", f"git update-ref {ref}: {_tail(proc)}")
        return ref

    def _integrate(self, item: Slice, branch: str, outbox: Path, session: Any, ctx: dict) -> dict:
        """Step j: merge, apply the outboxes, validate and commit in the integration worktree.

        Any failure rolls back inside the integration worktree only.
        """
        tree = self.integration
        tip = self._rev(f"refs/heads/{self.branch}")
        argv = ("merge", "--no-ff", "--no-commit", f"refs/heads/{branch}")
        merge, bad = self.guard.write(lambda: _git(tree, self.probes, *argv), own=tree.git_dir)
        if bad:
            self._rollback(tip)
            self._violation(item, ctx, bad)
        if merge.returncode != 0:
            self._rollback(tip)
            raise SliceFailed(FailureType.merge_conflict, _tail(merge))
        try:
            self._apply_outboxes(item, tree, outbox)
            message = self._message(item, ctx, "integrate", f"hacf: integrate {item.id}", session)
            excludes = self._excludes(tree, [] if self.ledger_tracked else [self.ledger_rel])

            def act() -> Any:
                added = _git(tree, self.probes, "add", "-A", "--", ".", *excludes)
                commit = ("commit", "-q", "--no-verify", "-F", str(message))
                return added if added.returncode else _git(tree, self.probes, *commit)

            ref = f"refs/heads/{self.branch}"
            proc, bad = self.guard.write(act, expect=(ref,), own=tree.git_dir)
            self._violation(item, ctx, bad)
            if proc.returncode != 0:
                raise EngineError("internal_error", f"integration commit: {_tail(proc)}")
        except (SliceFailed, EngineError):
            self._rollback(tip)
            raise
        if not self.ledger_tracked:
            _copy(tree.top / self.ledger_rel, self.staged)
        sha = self._rev(f"refs/heads/{self.branch}")
        ctx["event"](item.id, "integrated", {"commit": sha})
        return {"integrated_seq": time.time_ns(), "integration_commit": sha}

    def _apply_outboxes(self, item: Slice, tree: Tree, outbox: Path) -> None:
        """Replay the slice's outboxes, record the audited event, then validate."""
        for phase in ("execute", "audit"):
            directory = outbox / phase
            tools = outbox_tools(directory)
            if "hyper_ledger" in tools:
                args = ("apply-outbox", str(directory))
                self._script(tree, FailureType.ledger_invalid, "hyper_ledger.py", *args)
            if "hyper_lore" in tools:  # proposals, notes and stale marks only
                args = ("apply-outbox", str(directory))
                self._script(tree, FailureType.lore_invalid, "hyper_lore.py", *args)
        if not _legacy(item):
            progress = ("progress", "--slice", item.path, "--event", "audited")
            self._script(tree, FailureType.ledger_invalid, "hyper_spec.py", *progress)
        if (tree.top / self.ledger_rel).is_file():
            self._script(tree, FailureType.ledger_invalid, "hyper_ledger.py", "validate")
        if (tree.top / self.config["topology"]["lore_graph"]).is_file():
            self._script(tree, FailureType.lore_invalid, "hyper_lore.py", "validate")
        # A spec that stops linting rolls back like the ledger (both rolled back, then blocked).
        self._script(tree, FailureType.ledger_invalid, "hyper_spec.py", "lint", "--all")

    def _reset(self, tree: Tree, ref: str, rev: str = "HEAD") -> None:
        """Reset an engine worktree (on branch ref) to rev and drop its untracked leftovers.

        Never the main checkout; ignored files such as .venv stay.
        """

        def act() -> Any:
            _git(tree, self.probes, "reset", "-q", "--hard", rev)
            return _git(tree, self.probes, "clean", "-fdq")

        _, bad = self.guard.write(act, expect=(ref,), own=tree.git_dir)
        _unexpected(bad)

    def _rollback(self, tip: str) -> None:
        """Reset the integration worktree to tip (reset --hard there only)."""
        self._reset(self.integration, f"refs/heads/{self.branch}", tip)
        if not self.ledger_tracked:
            _copy(self.staged, self.integration.top / self.ledger_rel)


# ---------------------------------------------------------------------------
# Land and gc (LANDING, GARBAGE COLLECTION; R5, R20.6)
# ---------------------------------------------------------------------------


def lock_holder(common_dir: Path) -> str | None:
    """Return the run id holding engine.lock ('unknown' when unnamed), or None when free."""
    try:
        with engine_lock(common_dir, "probe"):
            return None
    except LockHeld as exc:
        return exc.holder or "unknown"


def _rebase_ledger(directory: Path, state: dict, ledger: Path) -> str:
    """Replay the run's recorded ledger outbox entries onto the current ledger (R5).

    This is `hyper_ledger.py apply-outbox <dir> --base-file ledger/base.yml` run in-process on a
    copy outside the work tree, because land already holds ledger.lock. An entry touching a
    node whose content changed since the base raises Refused with one line per node.
    """
    slices = state["slices"].items()
    order = sorted((r["integrated_seq"], sid) for sid, r in slices if r.get("integrated_seq"))
    with tempfile.TemporaryDirectory(prefix="hacf-land-") as tmp:
        combined = Path(tmp) / "outbox"
        combined.mkdir()
        count = 0
        for _, sid in order:
            for phase in ("execute", "audit"):
                source = directory / "outbox" / sid / phase
                for _, entry in hyper_ledger.read_outbox(source) if source.is_dir() else []:
                    if entry.get("tool") == hyper_ledger.OUTBOX_TOOL:
                        count += 1
                        target = combined / f"{count:06d}-{entry.get('op')}.json"
                        target.write_text(json.dumps(entry), encoding="utf-8")
        copy_path = Path(tmp) / ledger.name
        shutil.copyfile(ledger, copy_path)
        hyper_ledger.apply_outbox(copy_path, combined, directory / "ledger" / "base.yml")
        return copy_path.read_text(encoding="utf-8")


def _land_ledger(ledger: Path, text: str) -> None:
    """Write text over the main checkout's ledger unless that adds a validation error."""
    before = hyper_ledger.load_ledger(ledger)[1] if ledger.is_file() else None
    after = hyper_ledger.parse_ledger(text, str(ledger))
    added = hyper_ledger.check_non_regression(before, after)[0]
    if added:
        message = "refused: the run's ledger would add validation errors; nothing written"
        raise hyper_ledger.Refused(message, [finding.line() for finding in added])
    hyper_ledger._atomic_write(ledger, text)


def _merge_problem(repo: Repo, branch: str, probes: Probes) -> str | None:
    """Return why `git merge --ff-only branch` cannot run in the main checkout, or None."""
    if _git(repo, probes, "symbolic-ref", "-q", "HEAD").returncode != 0:
        return "the main checkout's HEAD is detached; check out the base branch first"
    if _git(repo, probes, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return "the main checkout has uncommitted changes to tracked files"
    if _git(repo, probes, "merge-base", "--is-ancestor", "HEAD", branch).returncode != 0:
        return f"{branch} does not fast-forward the checked-out branch"
    return None


def land(
    repo: Repo, run_id: str, probes: Probes, *, merge: bool = False, rebase: bool = False
) -> tuple[int, dict]:
    """Land a run (LANDING, R5): its ledger under ledger.lock, then optionally the merge.

    Returns (exit code, report). Nothing is written when the main checkout's ledger drifted
    from the run's base (unless rebase replays the run's ledger outbox entries cleanly) or
    when --merge cannot fast-forward a clean checkout. Nothing is ever pushed.
    """
    state = load_state(repo.top, run_id)
    directory = run_dir(repo.top, run_id)
    config, _ = resolve_config(repo.top, state.get("flags"))
    rel = config["topology"]["ledger"]
    ledger = repo.top / rel
    branch = config["git"]["integration_branch"].format(run_id=run_id)
    base_sha256 = state.get("base_ledger_sha256")
    report = {"run_id": run_id, "ok": False, "integration_branch": branch}
    if lock_holder(repo.common_dir) == run_id:
        report["message"] = f"run {run_id} is still running (it holds engine.lock)"
        return EXIT_LOCKED, report
    with _flock(_locks_dir(repo.common_dir) / "ledger.lock", hyper_ledger.LOCK_TIMEOUT):
        current = hashlib.sha256(ledger.read_bytes()).hexdigest() if ledger.is_file() else None
        report.update(base_ledger_sha256=base_sha256, ledger_sha256=current)
        drift = current != base_sha256
        tracked = _git(repo, probes, "ls-files", "--error-unmatch", "--", rel).returncode == 0
        problem = None
        if drift and not rebase:
            problem = (
                "the main checkout's ledger changed since the run's base, so land refuses (R5); "
                "--rebase-ledger replays the run's ledger outbox entries onto it instead"
            )
        elif drift and merge and tracked:
            problem = "with a tracked ledger, replay with --rebase-ledger first, then merge"
        elif merge:
            problem = _merge_problem(repo, branch, probes)
        if problem:
            report["message"] = problem
            return EXIT_REFUSED, report
        staged = directory / "ledger" / ledger.name
        try:
            if drift:
                _land_ledger(ledger, _rebase_ledger(directory, state, ledger))
                report["ledger"] = "replayed"
            elif not tracked and staged.is_file():
                _land_ledger(ledger, staged.read_text(encoding="utf-8"))
                report["ledger"] = "applied"
        except hyper_ledger.LedgerError as exc:
            report.update(message=str(exc), conflicts=exc.details)
            return EXIT_REFUSED, report
        if merge:
            proc = _git(repo, probes, "merge", "--ff-only", "-q", branch)
            if proc.returncode != 0:
                report["message"] = f"git merge --ff-only {branch}: {_tail(proc)}"
                return EXIT_REFUSED, report
        state["landed_at"] = _runtime.utc_now_iso()
        save_state(directory, state)
    report.update(ok=True, merged=merge)
    if not merge:
        report["merge_command"] = f"git merge --ff-only {branch}"
    return EXIT_OK, report


def gc(repo: Repo, probes: Probes, *, older_than: float = 14 * 86400, apply: bool = False) -> dict:
    """List, and with apply remove, what landed or abandoned runs left behind (R20.6).

    A run is abandoned when it ended (finished or aborted) without landed_at; it counts once
    created_at is older_than seconds ago. The run holding engine.lock is never listed.
    """
    holder = lock_holder(repo.common_dir)
    config, _ = resolve_config(repo.top)
    checked_out, path = {}, None
    for line in _git(repo, probes, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch "):
            checked_out[line[len("branch ") :]] = path
    now = _runtime.parse_ts(_runtime.utc_now_iso())
    runs = repo.top / STATE_REL / "runs"
    found = []
    for directory in sorted(runs.iterdir()) if runs.is_dir() else []:
        run_id = directory.name
        if not RUN_ID_RE.match(run_id) or run_id == holder:
            continue
        try:
            state = load_state(repo.top, run_id)
        except (OSError, ValueError, KeyError):
            continue
        landed = bool(state.get("landed_at"))
        if not landed and state.get("status") not in ("finished", "aborted"):
            continue
        if (now - _runtime.parse_ts(state["created_at"])).total_seconds() < older_than:
            continue
        prefix = config["git"]["slice_branch"].format(run_id=run_id, slice_dir="").rstrip("/")
        listed = _git(repo, probes, "for-each-ref", "--format=%(refname)", f"refs/heads/{prefix}")
        branches = listed.stdout.split()
        refs = []
        for record in state["slices"].values():
            ref = record.get("candidate_ref")
            at = _git(repo, probes, "rev-parse", "--verify", "-q", ref).stdout if ref else ""
            if ref and at.strip() == record.get("commit"):
                refs.append(ref)
        found.append(
            {
                "run_id": run_id,
                "reason": "landed" if landed else "abandoned",
                "worktrees": [checked_out[b] for b in branches if b in checked_out],
                "branches": branches,
                "refs": refs,
                "run_dir": str(directory),
            }
        )
    if apply and found:
        with git_lock(repo.common_dir):
            for item in found:
                for worktree in item["worktrees"]:
                    _git(repo, probes, "worktree", "remove", "--force", worktree)
                    with contextlib.suppress(OSError):
                        Path(worktree).parent.rmdir()  # the run's worktree dir, once empty
                for ref in item["branches"] + item["refs"]:
                    _git(repo, probes, "update-ref", "-d", ref)
                shutil.rmtree(item["run_dir"])
            _git(repo, probes, "worktree", "prune")
    return {"apply": apply, "runs": found}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _usd(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not a budget in USD")
    return value


def _duration(text: str) -> float:
    match = DURATION_RE.match(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r} is not an age like 14d, 12h or 30m")
    return int(match.group(1)) * {"d": 86400, "h": 3600, "m": 60}[match.group(2)]


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(prog="hyper_engine.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def run_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--executor", default="claude-cli", help="executor name")
        p.add_argument("--unverified-executor", action="store_true", help="allow verified: false")
        p.add_argument("--allow-unsandboxed", action="store_true", help="agent phases unsandboxed")
        p.add_argument(
            "--allow-unsandboxed-oracle", action="store_true", help="oracles without bwrap"
        )
        p.add_argument("--max-budget-usd-per-run", type=_usd, metavar="USD", help="run budget")

    run_flags(sub.add_parser("preflight", help="run the preflight checks only"))
    p = sub.add_parser("run", help="preflight, then schedule the slices")
    run_flags(p)
    p.add_argument("--resume", metavar="RUN_ID", help="continue a recorded run")
    p = sub.add_parser("status", help="print a run's run.json")
    p.add_argument("run_id")
    p = sub.add_parser("land", help="apply a run's ledger; --merge fast-forwards the base branch")
    p.add_argument("run_id")
    p.add_argument("--merge", action="store_true", help="git merge --ff-only the integration")
    p.add_argument(
        "--rebase-ledger", action="store_true", help="replay the run's ledger entries on drift"
    )
    p = sub.add_parser("gc", help="list (or --apply: remove) what finished runs left behind")
    p.add_argument("--older-than", type=_duration, default="14d", metavar="AGE", help="e.g. 14d")
    p.add_argument("--apply", action="store_true", help="remove what is listed")
    return parser


def _flags(ns: argparse.Namespace) -> dict:
    return {key: getattr(ns, key) for key in DEFAULT_FLAGS}


def cmd_preflight(top: Path, flags: dict, probes: Probes) -> int:
    """Run preflight under engine.lock and print its report."""
    try:
        repo = discover(top, probes)
        config, ignored = resolve_config(repo.top, flags)
        with engine_lock(repo.common_dir, "preflight"):
            report = preflight(repo, config, flags, probes, ignored)
    except EngineError as exc:
        return _refused({"ok": False, "checks": [_result("config", False, str(exc))]})
    except LockHeld as exc:
        print(f"hyper_engine.py: engine.lock is held by run {exc.holder}", file=sys.stderr)
        return EXIT_LOCKED
    print(json.dumps(report.as_json(), indent=2))
    return EXIT_OK if report.ok else EXIT_REFUSED


def cmd_status(top: Path, run_id: str, probes: Probes) -> int:
    """Print a run's run.json."""
    try:
        state = load_state(discover(top, probes).top, run_id)
    except (EngineError, OSError, ValueError, KeyError) as exc:
        print(f"hyper_engine.py: status {run_id}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(state, indent=2))
    return EXIT_OK


def cmd_land(top: Path, ns: argparse.Namespace, probes: Probes) -> int:
    """Land a run and print its report."""
    try:
        repo = discover(top, probes)
        code, report = land(repo, ns.run_id, probes, merge=ns.merge, rebase=ns.rebase_ledger)
    except (EngineError, OSError, ValueError, KeyError) as exc:
        print(f"hyper_engine.py: land {ns.run_id}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(report, indent=2))
    return code


def cmd_gc(top: Path, ns: argparse.Namespace, probes: Probes) -> int:
    """List, or with --apply remove, what landed or abandoned runs left behind."""
    try:
        report = gc(discover(top, probes), probes, older_than=ns.older_than, apply=ns.apply)
    except EngineError as exc:
        print(f"hyper_engine.py: gc: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(report, indent=2))
    return EXIT_OK


def main(argv: list[str] | None = None, probes: Probes | None = None) -> int:
    """Run the CLI in the working directory's repository and return its exit code."""
    ns = build_parser().parse_args(argv)
    probes = probes or Probes()
    top = Path.cwd()
    if ns.command == "status":
        return cmd_status(top, ns.run_id, probes)
    if ns.command == "preflight":
        return cmd_preflight(top, _flags(ns), probes)
    if ns.command == "land":
        return cmd_land(top, ns, probes)
    if ns.command == "gc":
        return cmd_gc(top, ns, probes)
    return run_engine(top, _flags(ns), WorktreeRunner(), probes=probes, resume=ns.resume)


if __name__ == "__main__":
    sys.exit(main())
