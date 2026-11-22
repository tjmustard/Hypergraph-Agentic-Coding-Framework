#!/usr/bin/env python3
"""hyper_codegraph.py — HACF's code-graph adapter, pinned to codebase-memory-mcp 0.12.1.

The adapter runs `codebase-memory-mcp cli <tool> ...` (with `--format json` where the tool takes
it; index_repository and delete_project do not, R31) as argv lists, never through a shell, with
a timeout per call, and normalizes every answer to `hacf.codegraph-result/v1`.
Normative design: the code-graph config section of HACF's v0.6.0 design notes (not shipped)
(decision D5). The transcribed CLI contract and the expected normalized results live in
`dev/tests/fixtures/codegraph-0.12.1/`; the consented live contract test is
`dev/tests/contract/test_codegraph_live.py`.

Configuration, three layers under the repository root (later layers win):
1. `.agents/config/code_graph.defaults.yml`: shipped defaults (pinned version, `verified`, argv
   templates, timeouts). The same content is embedded below as EMBEDDED_DEFAULTS; when the file
   is missing the embedded copy is used and stderr says "run HACF-install.sh to complete the
   upgrade" (R24.10).
2. `.agents/local/hacf.yml` `code_graph: {backend, install_method, base_branch}`: the project's
   choice. A missing file or key means backend `none`, which changes nothing (US-008 AC3).
3. `.agents/state/codegraph/project.json` `{exe_argv_prefix, project_name, version,
   last_index}`: machine-local, written by `index`, because the backend derives project names
   from paths and only `list_projects` knows them.

Executable resolution (never downloads or installs anything): the state file's recorded
`exe_argv_prefix`; then `codebase-memory-mcp` on PATH; then `['uvx',
'codebase-memory-mcp@0.12.1']`, only when hacf.yml records `install_method: uvx` (the
installer writes it after the user's consent, S12b). Nothing resolved: status `unavailable`.

Placeholders (R24.6) are validated before any argv is built: a value starting with `-` is
refused; `repo_root` is an absolute existing directory; `project` matches PROJECT_RE; `symbol`
is identifier characters plus `.:$<>-`, at most 256; `pattern` is at most 256 characters and
compiles; `base` passes `git check-ref-format --branch`. Each `{placeholder}` replaces one whole
argv element.

CLI:
    hyper_codegraph.py <capability> [--repo-root DIR] [--project NAME] [--base REF]
        [--symbol NAME] [--pattern REGEX] [--json]
    hyper_codegraph.py doctor [--repo-root DIR] [--json]

Capabilities: index, status, list_projects, diff_impact, symbol_impact, search, delete. `index`
also runs list_projects and records the project in the state file; `delete` removes the backend
index of the project the state file names (the uninstaller offers it, R24.9). `doctor` prints
the resolution chain, the backend version against the pin, the config layers found, the index
status and the `verified` flag. `diff_impact --base` takes a branch name (default: hacf.yml
`base_branch`) or a full commit id, which hypergraph_updater.py --from-diff passes (R34).

Results: diff_impact and search name files, so their impacted files map to ledger components
through `files:` globs. symbol_impact cannot: trace_path names no file, so its entries have a
null file, it maps no component and it says so in a warning (R32). A payload with `has_more`,
a `*_has_more` key or `truncated` set to true is an error for diff_impact and list_projects
and a warning for search and symbol_impact (R33).

Statuses: ok | unsupported (backend `none`, or the backend lacks the capability) | unavailable
(no executable) | stale_index (no index recorded, or HEAD moved since `index`) | error (a
failed, timed-out or unparseable call). On anything but ok, callers fall back to git diff plus
`files:` globs and say so.

Exit codes: 0 for ok, unsupported, unavailable and stale_index; 1 error; 2 usage or a refused
placeholder.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NamedTuple

import _runtime
import hyper_ledger

try:
    import yaml
except ImportError:  # main() calls _runtime.require_runtime() first, which reports it
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

SCHEMA_DEFAULTS = "hacf.codegraph-defaults/v1"
SCHEMA_RESULT = "hacf.codegraph-result/v1"
BACKEND_NONE = "none"
BACKEND_CBM = "codebase-memory-mcp"
EXE_NAME = "codebase-memory-mcp"
PINNED_VERSION = "0.12.1"
DEFAULTS_REL = ".agents/config/code_graph.defaults.yml"
LOCAL_REL = ".agents/local/hacf.yml"
STATE_REL = ".agents/state/codegraph/project.json"
RAW_DIR_REL = ".agents/state/codegraph/raw"
UPGRADE_NOTICE = "run HACF-install.sh to complete the upgrade"
DEFAULT_BASE_BRANCH = "main"

STATUSES = ("ok", "unsupported", "unavailable", "stale_index", "error")
CAPABILITIES = (
    "index",
    "status",
    "list_projects",
    "diff_impact",
    "symbol_impact",
    "search",
    "delete",
)
INSTALL_METHODS = ("path", "uv-tool", "uvx")
PLACEHOLDERS = ("repo_root", "project", "base", "symbol", "pattern")
# The placeholders each capability's template uses (repo_root is always known).
NEEDS = {
    "index": ("repo_root",),
    "status": ("project",),
    "list_projects": (),
    "diff_impact": ("project", "base"),
    "symbol_impact": ("project", "symbol"),
    "search": ("project", "pattern"),
    "delete": ("project",),
}
# Queries that read the graph: no recorded index, or HEAD moved since `index`, is stale_index.
QUERY_CAPABILITIES = ("diff_impact", "symbol_impact", "search")
# Truncation (R33): these keys set to true mean an incomplete payload. It is an error where a
# partial answer would mislead (a blast radius, the project lookup), else a warning. Only the
# capabilities named here are checked: index_status, for one, reports a truncated
# `not_indexed` file list inside a complete answer.
TRUNCATION_KEYS = ("has_more", "truncated")
TRUNCATION_IS_ERROR = ("diff_impact", "list_projects")
TRUNCATION_IS_WARNING = ("search", "symbol_impact")
SYMBOL_IMPACT_WARNING = (
    "symbol_impact: trace_path reports no file paths; components come from diff_impact"
)
# search_graph and trace_path name each row's qualified-name rule (`qn_rule`); only the rule
# the recorded 0.12.1 captures show (dev/tests/fixtures/recorded/S04/) is accepted, and
# _qualified_name() implements it. Search rows give `lines` as "<first>-<last>".
QN_RULE = 'qn = qn_prefix == "" ? name : qn_prefix + "." + name'
LINES_RE = re.compile(r"(\d+)(?:-\d+)?")

PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SYMBOL_RE = re.compile(r"[\w.:$<>-]{1,256}")
PATTERN_MAX = 256
TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
GIT_TIMEOUT = 30.0
KILL_GRACE = 5.0  # seconds to collect output after killing a timed-out process group

FALLBACK = "blast radius from git diff plus files: globs"

# The parsed content of .agents/config/code_graph.defaults.yml, which test_embedded_defaults_match
# keeps equal to the file.
CBM_PREFIX = ["uvx", "codebase-memory-mcp@0.12.1"]
EMBEDDED_DEFAULTS: dict = {
    "schema": SCHEMA_DEFAULTS,
    "backends": {
        BACKEND_CBM: {
            "pinned_version": PINNED_VERSION,
            "verified": True,  # S04 contract test: contract_verdict pass (R20.1)
            "exe_resolution": [
                "local override",
                "shutil.which('codebase-memory-mcp')",
                "['uvx', 'codebase-memory-mcp@0.12.1'], only with recorded consent",
            ],
            "install_options": {
                "uv_tool": ["uv", "tool", "install", "codebase-memory-mcp==0.12.1"],
                "uvx": CBM_PREFIX,
                "vendor_script": {
                    "url": "https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/"
                    "v0.12.1/install.sh",
                    "args": ["--skip-config"],
                    "env": {
                        "CBM_DOWNLOAD_URL": "https://github.com/DeusData/codebase-memory-mcp/"
                        "releases/download/v0.12.1"
                    },
                },
            },
            "mcp_server": {"type": "stdio", "command": "uvx", "args": CBM_PREFIX[1:]},
            "capabilities": {
                "index": ["cli", "index_repository", "--repo-path", "{repo_root}"],  # R31
                "status": ["cli", "index_status", "--project", "{project}", "--format", "json"],
                "list_projects": ["cli", "list_projects", "--limit", "1000", "--format", "json"],
                "diff_impact": [
                    "cli",
                    "detect_changes",
                    "--project",
                    "{project}",
                    "--base-branch",
                    "{base}",
                    # Paging literals (R33): the backend defaults would page the diff.
                    "--changed-limit",
                    "1000",
                    "--limit",
                    "1000",
                    "--module-limit",
                    "1000",
                    "--max-output-tokens",
                    "100000",
                    "--format",
                    "json",
                ],
                "symbol_impact": [
                    "cli",
                    "trace_path",
                    "--project",
                    "{project}",
                    "--function-name",
                    "{symbol}",
                    "--direction",
                    "inbound",
                    "--format",
                    "json",
                ],
                "search": [
                    "cli",
                    "search_graph",
                    "--project",
                    "{project}",
                    "--name-pattern",
                    "{pattern}",
                    "--format",
                    "json",
                ],
                "delete": ["cli", "delete_project", "--project", "{project}"],  # R31
            },
            "timeouts_s": {"index": 900, "query": 60},
        },
        BACKEND_NONE: {"capabilities": {}},
    },
}


# ---------------------------------------------------------------------------
# Errors and small types
# ---------------------------------------------------------------------------


class CodeGraphError(Exception):
    """A failure that makes the result status `error` (exit 1)."""

    code = EXIT_ERROR


class UsageError(CodeGraphError):
    """Bad arguments (exit 2); nothing ran."""

    code = EXIT_USAGE


class PlaceholderRefused(UsageError):
    """A placeholder value broke the R24.6 rules (exit 2); nothing ran."""


class ShapeError(CodeGraphError):
    """A backend payload whose shape the normalizer does not know (status error, never a guess)."""


class Completed(NamedTuple):
    """The outcome of one subprocess; returncode is None when it timed out."""

    argv: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class Config(NamedTuple):
    """The merged three-layer configuration for one repository."""

    repo_root: Path
    backend: str
    install_method: str | None
    base_branch: str
    defaults: dict
    entry: dict  # defaults["backends"][backend]
    state: dict
    layers: dict  # {"defaults": "file" | "embedded", "local": path | None, "state": path | None}


# ---------------------------------------------------------------------------
# Subprocess and git
# ---------------------------------------------------------------------------


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the timed-out child and everything in its process group."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _run(argv: list[str], *, cwd: Path | None, timeout: float) -> Completed:
    """Run argv, a list of strings and never a shell string, with a timeout.

    Every subprocess this module starts goes through here. The child gets its own process group
    and no stdin, so a timeout kills it together with anything it started. A missing executable
    comes back as 127; a timeout as returncode None with timed_out set.
    """
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=None if cwd is None else str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        return Completed(argv, 127, "", f"{argv[0]}: {exc.strerror}")
    except PermissionError as exc:
        return Completed(argv, 126, "", f"{argv[0]}: {exc.strerror}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:  # a detached grandchild still holds the pipes
            stdout, stderr = "", ""
        return Completed(argv, None, stdout or "", stderr or "", timed_out=True)
    return Completed(argv, proc.returncode, stdout, stderr)


def _git(args: list[str], cwd: Path) -> Completed:
    return _run(["git", *args], cwd=cwd, timeout=GIT_TIMEOUT)


def default_repo_root(start: Path | None = None) -> Path:
    """Return the git top level containing start (default: the working directory), else start."""
    here = (start or Path.cwd()).resolve()
    top = hyper_ledger.repo_toplevel(here)
    return top if top is not None else here


def head_commit(repo_root: Path) -> str | None:
    """Return the sha HEAD names in repo_root, or None (no git, no commit)."""
    return commit_id(repo_root, "HEAD", required=False)


def commit_id(repo_root: Path, rev: str, *, required: bool = True) -> str | None:
    """Return the full commit id rev names in repo_root (`git rev-parse --verify rev^{commit}`).

    A full commit id passes the `base` placeholder rule where `HEAD` or `HEAD~1` would not
    (R34). Returns None, or raises UsageError when required, for a rev that names no commit.
    """
    if rev.startswith("-"):
        raise _refuse("rev", rev, "starts with '-', so it could be parsed as an option")
    proc = _git(["rev-parse", "--verify", "-q", f"{rev}^{{commit}}"], repo_root)
    sha = proc.stdout.strip()
    if proc.returncode == 0 and sha:
        return sha
    if required:
        raise UsageError(f"{rev}: not a commit in {repo_root}")
    return None


# ---------------------------------------------------------------------------
# Configuration layers
# ---------------------------------------------------------------------------


def _load_yaml(text: str, label: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise CodeGraphError(f"{label}: YAML parse error: {first}") from None


def _check_defaults(data: Any, label: str) -> dict:
    """Return the defaults mapping after checking the parts the adapter relies on."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_DEFAULTS:
        raise CodeGraphError(f"{label}: not a {SCHEMA_DEFAULTS} document")
    backends = data.get("backends")
    if not isinstance(backends, dict) or BACKEND_NONE not in backends:
        raise CodeGraphError(f"{label}: `backends` must be a mapping that includes `none`")
    for name, entry in backends.items():
        caps = entry.get("capabilities") if isinstance(entry, dict) else None
        if not isinstance(caps, dict):
            raise CodeGraphError(f"{label}: backend {name} has no `capabilities` mapping")
        for cap, template in caps.items():
            if cap not in CAPABILITIES:
                raise CodeGraphError(f"{label}: backend {name}: unknown capability {cap}")
            check_template(template, f"{label}: {name}.{cap}")
    return data


def load_defaults(repo_root: Path) -> tuple[dict, str]:
    """Return (defaults, "file" | "embedded") for repo_root.

    A missing defaults file falls back to EMBEDDED_DEFAULTS and says so on stderr (R24.10).
    """
    path = repo_root / DEFAULTS_REL
    if path.is_file():
        data = _load_yaml(path.read_text(encoding="utf-8"), DEFAULTS_REL)
        return _check_defaults(data, DEFAULTS_REL), "file"
    print(
        f"hyper_codegraph.py: {DEFAULTS_REL} is missing; using the embedded defaults "
        f"({UPGRADE_NOTICE})",
        file=sys.stderr,
    )
    return _check_defaults(copy.deepcopy(EMBEDDED_DEFAULTS), "embedded defaults"), "embedded"


def load_local(repo_root: Path) -> tuple[dict, Path | None]:
    """Return (hacf.yml `code_graph` mapping, the file path or None when it is missing)."""
    path = repo_root / LOCAL_REL
    if not path.is_file():
        return {}, None
    data = _load_yaml(path.read_text(encoding="utf-8"), LOCAL_REL)
    if data is None:
        return {}, path
    if not isinstance(data, dict):
        raise CodeGraphError(f"{LOCAL_REL}: the root must be a mapping")
    section = data.get("code_graph")
    if section is None:
        return {}, path
    if not isinstance(section, dict):
        raise CodeGraphError(f"{LOCAL_REL}: `code_graph` must be a mapping")
    return section, path


def configured_backend(repo_root: Path) -> str:
    """Return the backend hacf.yml selects (`none` when the file or key is missing).

    Reads only layer 2, so a caller can skip the adapter entirely for `none`.
    """
    section, _ = load_local(repo_root)
    backend = section.get("backend", BACKEND_NONE)
    if backend is None:
        return BACKEND_NONE
    if not isinstance(backend, str):
        raise CodeGraphError(f"{LOCAL_REL}: code_graph.backend must be a string")
    return backend


def load_state(repo_root: Path) -> tuple[dict, Path | None]:
    """Return (the state file's mapping, its path or None when it is missing)."""
    path = repo_root / STATE_REL
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CodeGraphError(f"{STATE_REL}: not valid JSON ({exc}); re-run `index`") from None
    if not isinstance(data, dict):
        raise CodeGraphError(f"{STATE_REL}: the root must be a JSON object")
    return data, path


def write_state(repo_root: Path, state: dict) -> Path:
    """Write the state file atomically (creating .agents/state/ on first use); return its path."""
    path = repo_root / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".project.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def load_config(repo_root: Path) -> Config:
    """Merge the three layers for repo_root; raise CodeGraphError on a broken layer."""
    defaults, source = load_defaults(repo_root)
    local, local_path = load_local(repo_root)
    state, state_path = load_state(repo_root)
    backend = local.get("backend", BACKEND_NONE) or BACKEND_NONE
    if not isinstance(backend, str) or backend not in defaults["backends"]:
        known = ", ".join(sorted(defaults["backends"]))
        raise CodeGraphError(f"{LOCAL_REL}: code_graph.backend {backend!r} is not one of {known}")
    method = local.get("install_method")
    if method is not None and method not in INSTALL_METHODS:
        raise CodeGraphError(
            f"{LOCAL_REL}: code_graph.install_method {method!r} is not one of "
            + ", ".join(INSTALL_METHODS)
        )
    base = local.get("base_branch", DEFAULT_BASE_BRANCH) or DEFAULT_BASE_BRANCH
    if not isinstance(base, str):
        raise CodeGraphError(f"{LOCAL_REL}: code_graph.base_branch must be a string")
    return Config(
        repo_root=repo_root,
        backend=backend,
        install_method=method,
        base_branch=base,
        defaults=defaults,
        entry=defaults["backends"][backend],
        state=state,
        layers={"defaults": source, "local": local_path, "state": state_path},
    )


# ---------------------------------------------------------------------------
# Placeholders and argv (R24.6)
# ---------------------------------------------------------------------------


def _refuse(name: str, value: str, why: str) -> PlaceholderRefused:
    return PlaceholderRefused(f"refused: {name} value {value!r} {why}; nothing was run")


def validate_placeholders(values: dict[str, str | None]) -> dict[str, str]:
    """Return the given (non-None) placeholder values after the R24.6 checks.

    Raises PlaceholderRefused (exit 2) for the first value that breaks a rule.
    """
    checked: dict[str, str] = {}
    for name, value in values.items():
        if value is None:
            continue
        if name not in PLACEHOLDERS:
            raise UsageError(f"unknown placeholder {name}")
        if not isinstance(value, str) or not value:
            raise _refuse(name, str(value), "is empty")
        if value.startswith("-"):
            raise _refuse(name, value, "starts with '-', so it could be parsed as an option")
        if "\x00" in value:
            raise _refuse(name, value, "contains a NUL byte")
        if name == "repo_root":
            if not os.path.isabs(value) or not os.path.isdir(value):
                raise _refuse(name, value, "is not an absolute existing directory")
        elif name == "project":
            if not PROJECT_RE.fullmatch(value):
                raise _refuse(name, value, f"does not match ^{PROJECT_RE.pattern}$")
        elif name == "symbol":
            if not SYMBOL_RE.fullmatch(value):
                raise _refuse(name, value, "is not identifier characters plus .:$<>- (at most 256)")
        elif name == "pattern":
            if len(value) > PATTERN_MAX:
                raise _refuse(name, value, f"is longer than {PATTERN_MAX} characters")
            try:
                re.compile(value)
            except re.error as exc:
                raise _refuse(name, value, f"does not compile ({exc})") from None
        elif name == "base":
            argv = ["git", "check-ref-format", "--branch", value]
            if _run(argv, cwd=None, timeout=GIT_TIMEOUT).returncode != 0:
                raise _refuse(name, value, "does not pass `git check-ref-format --branch`")
        checked[name] = value
    return checked


def check_template(template: Any, label: str) -> None:
    """Raise CodeGraphError unless template is a list of literals and whole `{placeholder}`s."""
    if not isinstance(template, list) or not all(isinstance(e, str) for e in template):
        raise CodeGraphError(f"{label}: an argv template must be a list of strings")
    for element in template:
        match = TOKEN_RE.fullmatch(element)
        if match:
            if match.group(1) not in PLACEHOLDERS:
                raise CodeGraphError(f"{label}: unknown placeholder {element}")
        elif "{" in element or "}" in element:
            raise CodeGraphError(
                f"{label}: element {element!r} mixes a placeholder with literal text"
            )


def build_argv(prefix: list[str], template: list[str], values: dict[str, str]) -> list[str]:
    """Return prefix plus template with each `{placeholder}` element replaced by its value.

    values must come from validate_placeholders(); a placeholder only ever fills a whole element.
    """
    check_template(template, "template")
    argv = list(prefix)
    for element in template:
        match = TOKEN_RE.fullmatch(element)
        if match is None:
            argv.append(element)
            continue
        name = match.group(1)
        if name not in values:
            raise UsageError(f"this capability needs --{name.replace('_', '-')}")
        argv.append(values[name])
    return argv


# ---------------------------------------------------------------------------
# Executable resolution
# ---------------------------------------------------------------------------


def _recorded_prefix(state: dict) -> list[str] | None:
    prefix = state.get("exe_argv_prefix")
    if prefix is None:
        return None
    if (
        not isinstance(prefix, list)
        or not prefix
        or not all(isinstance(part, str) and part for part in prefix)
        or prefix[0].startswith("-")
    ):
        raise CodeGraphError(f"{STATE_REL}: exe_argv_prefix must be a non-empty list of strings")
    return list(prefix)


def resolution_chain(cfg: Config) -> tuple[list[str] | None, list[str]]:
    """Return (the argv prefix that runs the backend or None, one line per step tried).

    Order: the state file's exe_argv_prefix; `codebase-memory-mcp` on PATH; the uvx prefix only
    with recorded consent (hacf.yml install_method uvx). Nothing here downloads or installs.
    """
    lines: list[str] = []
    recorded = _recorded_prefix(cfg.state)
    if recorded is not None:
        lines.append(f"state exe_argv_prefix: {json.dumps(recorded)} (chosen)")
        return recorded, lines
    lines.append("state exe_argv_prefix: not recorded")
    found = shutil.which(EXE_NAME)
    if found:
        lines.append(f"PATH {EXE_NAME}: {found} (chosen)")
        return [found], lines
    lines.append(f"PATH {EXE_NAME}: not found")
    uvx = (cfg.entry.get("install_options") or {}).get("uvx")
    if cfg.install_method == "uvx" and isinstance(uvx, list) and uvx:
        lines.append(f"uvx with recorded consent (install_method uvx): {json.dumps(uvx)} (chosen)")
        return [str(part) for part in uvx], lines
    lines.append("uvx: no recorded consent (hacf.yml code_graph.install_method is not uvx)")
    return None, lines


def resolve_prefix(cfg: Config) -> list[str] | None:
    """Return the argv prefix that runs the backend, or None (status unavailable)."""
    return resolution_chain(cfg)[0]


def backend_version(prefix: list[str], cwd: Path, timeout: float) -> str | None:
    """Return the semver `<prefix> --version` prints, or None."""
    proc = _run([*prefix, "--version"], cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        return None
    match = VERSION_RE.search(proc.stdout) or VERSION_RE.search(proc.stderr)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------


def _relpath(path: Any, repo_root: Path) -> str:
    """Return a payload path relative to repo_root (absolute paths under it are shortened)."""
    if not isinstance(path, str) or not path:
        raise ShapeError(f"a file path is not a non-empty string: {path!r}")
    if os.path.isabs(path):
        real_root = os.path.realpath(str(repo_root))
        real = os.path.realpath(path)
        if real == real_root or real.startswith(real_root + os.sep):
            return os.path.relpath(real, real_root).replace(os.sep, "/")
        return path
    return path[2:] if path.startswith("./") else path


def _require(payload: Any, key: str, kind: type, where: str) -> Any:
    if not isinstance(payload, dict) or key not in payload:
        raise ShapeError(f"{where}: expected key {key!r}")
    value = payload[key]
    if not isinstance(value, kind):
        raise ShapeError(f"{where}: {key!r} is {type(value).__name__}, not {kind.__name__}")
    return value


def truncation_keys(payload: Any) -> list[str]:
    """Return the dotted paths of every `has_more`, `*_has_more` or `truncated` key set to true.

    The backend pages and sizes its answers; any of these keys set to true means the payload is
    incomplete (R33). Nested objects are searched too (list positions are not part of a path).
    """
    found: set[str] = set()

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key)
                where = f"{path}.{name}" if path else name
                if value is True and (name in TRUNCATION_KEYS or name.endswith("_has_more")):
                    found.add(where)
                walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(payload, "")
    return sorted(found)


def normalize_list_projects(payload: Any, repo_root: Path) -> list[dict]:
    """Return [{name, root}] from a list_projects payload.

    0.12.1 shape: {projects: [{name, root_path, branch}], total, offset, limit, returned,
    has_more}. Callers check truncation_keys() first: an incomplete listing is never searched.
    """
    projects = _require(payload, "projects", list, "list_projects")
    rows = []
    for item in projects:
        name = _require(item, "name", str, "list_projects.projects[]")
        root = _require(item, "root_path", str, "list_projects.projects[]")
        rows.append({"name": name, "root": root})
    return rows


def _impacted(
    symbol: str, file: str | None, line: Any, kind: Any, risk: Any, distance: Any
) -> dict:
    return {
        "symbol": symbol,
        "file": file,
        "line": line,
        "kind": kind,
        "risk": risk,
        "distance": distance,
    }


def _table_rows(section: Any, where: str, needed: tuple[str, ...]) -> list[tuple[dict, dict]]:
    """Return [(group, row as {col: value})] for a grouped `{qn_rule, cols, groups}` table.

    search_graph and trace_path answer with column names once (`cols`) and rows as arrays,
    grouped under a shared qualified-name prefix (`groups[].qn_prefix`).
    """
    cols = _require(section, "cols", list, where)
    missing = [name for name in needed if name not in cols]
    if missing:
        raise ShapeError(f"{where}: cols {cols} lack {missing}")
    pairs = []
    for group in _require(section, "groups", list, where):
        _require(group, "qn_prefix", str, f"{where}.groups[]")
        for row in _require(group, "rows", list, f"{where}.groups[]"):
            if not isinstance(row, list) or len(row) != len(cols):
                raise ShapeError(f"{where}: a row does not have one value per column: {row!r}")
            pairs.append((group, dict(zip(cols, row))))
    return pairs


def _qualified_name(section: dict, prefix: str, name: Any, where: str) -> str:
    """Join a group's qn_prefix and a row's name as the payload's own qn_rule states it."""
    rule = section.get("qn_rule")
    if rule != QN_RULE:
        raise ShapeError(f"{where}: qn_rule {rule!r} is not the pinned {QN_RULE!r}")
    if not isinstance(name, str) or not name:
        raise ShapeError(f"{where}: a row name is not a non-empty string: {name!r}")
    return name if prefix == "" else f"{prefix}.{name}"


def _first_line(value: Any, where: str) -> int:
    """Return the first line of a search row's `lines` cell."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = LINES_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ShapeError(f"{where}: a row's lines {value!r} is not a line or a line range")
    return int(match.group(1))


def _normalize_diff_impact(payload: Any, repo_root: Path) -> dict:
    where = "detect_changes"
    changed = [_relpath(p, repo_root) for p in _require(payload, "changed_files", list, where)]
    impacted = []
    for row in _require(payload, "impacted", list, where):
        item = f"{where}.impacted[]"
        impacted.append(
            _impacted(
                _require(row, "qn", str, item),
                _relpath(_require(row, "file", str, item), repo_root),
                None,
                _require(row, "label", str, item),
                None,
                _require(row, "hop", int, item),
            )
        )
    return {
        "changed_files": sorted(set(changed)),
        "impacted": impacted,
        "impacted_files": sorted({entry["file"] for entry in impacted}),
    }


def _normalize_symbol_impact(payload: Any, repo_root: Path) -> dict:
    """trace_path names no file (R32): file, line, kind and risk stay null; no components."""
    where = "trace_path.callers"
    section = _require(payload, "callers", dict, "trace_path")
    impacted = [
        _impacted(
            _qualified_name(section, group["qn_prefix"], row["name"], where),
            None,
            None,
            None,
            None,
            _require(row, "hop", int, f"{where} row"),
        )
        for group, row in _table_rows(section, where, ("name", "hop"))
    ]
    return {"changed_files": [], "impacted": impacted, "impacted_files": []}


def _normalize_search(payload: Any, repo_root: Path) -> dict:
    where = "search_graph"
    impacted = []
    for group, row in _table_rows(payload, where, ("name", "label", "lines")):
        file = _relpath(_require(group, "file", str, f"{where}.groups[]"), repo_root)
        impacted.append(
            _impacted(
                _qualified_name(payload, group["qn_prefix"], row["name"], where),
                file,
                _first_line(row["lines"], where),
                _require(row, "label", str, f"{where} row"),
                None,
                None,
            )
        )
    return {
        "changed_files": [],
        "impacted": impacted,
        "impacted_files": sorted({entry["file"] for entry in impacted}),
    }


def normalize_payload(capability: str, payload: Any, repo_root: Path) -> dict:
    """Return {changed_files, impacted, impacted_files} for a capability's payload.

    Raises ShapeError for any shape the 0.12.1 contract does not pin: never a guess.
    """
    if not isinstance(payload, dict):
        raise ShapeError(f"{capability}: the payload is not a JSON object")
    if capability == "diff_impact":
        return _normalize_diff_impact(payload, repo_root)
    if capability == "symbol_impact":
        return _normalize_symbol_impact(payload, repo_root)
    if capability == "search":
        return _normalize_search(payload, repo_root)
    return {"changed_files": [], "impacted": [], "impacted_files": []}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def new_result(capability: str, backend: str | None = None) -> dict:
    """Return an empty hacf.codegraph-result/v1 object for capability."""
    return {
        "schema": SCHEMA_RESULT,
        "backend": backend,
        "backend_version": None,
        "capability": capability,
        "status": "error",
        "project": None,
        "inputs": {},
        "changed_files": [],
        "impacted": [],
        "impacted_files": [],
        "components": [],
        "warnings": [],
        "error": None,
        "raw_ref": None,
        "elapsed_ms": 0,
    }


def fallback_line(status: str, why: str = "") -> str:
    """Return the line a caller prints when the code graph cannot answer."""
    return f"code graph {status}{' ' + why if why else ''}: {FALLBACK}"


def map_components(
    repo_root: Path, paths: list[str], ledger: Path | None = None
) -> tuple[list[str], list[str]]:
    """Return (sorted ledger node ids covering paths, warnings) via the ledger's `files:` globs.

    The ledger defaults to spec/compiled/architecture.yml under repo_root. Reuses
    hyper_ledger.map_paths, so a v1 node without `files` falls back to its associated_file.
    """
    ledger = ledger if ledger is not None else repo_root / hyper_ledger.LEDGER_REL
    if not paths:
        return [], []
    if not ledger.is_file():
        return [], [f"no ledger at {ledger}; components not mapped"]
    try:
        _, data = hyper_ledger.load_ledger(ledger)
    except hyper_ledger.LedgerError as exc:
        return [], [f"ledger not read ({exc}); components not mapped"]
    ids, _ = hyper_ledger.map_paths(data, paths)
    return ids, []


def _save_raw(repo_root: Path, capability: str, stdout: str) -> str:
    """Save a payload verbatim under .agents/state/codegraph/raw/; return its repo-relative path."""
    rel = f"{RAW_DIR_REL}/{capability}.json"
    target = repo_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stdout, encoding="utf-8")
    return rel


def _timeout(cfg: Config, capability: str) -> float:
    timeouts = cfg.entry.get("timeouts_s") or {}
    key = "index" if capability == "index" else "query"
    value = timeouts.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise CodeGraphError(f"timeouts_s.{key} must be a positive number")
    return float(value)


def call_backend(
    cfg: Config, prefix: list[str], capability: str, values: dict[str, str], result: dict
) -> Any:
    """Run one capability; return its parsed JSON payload, or None after setting result's status.

    Sets status `unavailable` when the executable cannot start, `error` on a timeout, a non-zero
    exit or a payload that is not JSON; raw_ref names the saved payload.
    """
    argv = build_argv(prefix, cfg.entry["capabilities"][capability], values)
    timeout = _timeout(cfg, capability)
    proc = _run(argv, cwd=cfg.repo_root, timeout=timeout)
    if proc.timed_out:
        result.update(status="error", error=f"timeout after {timeout:g} s: {argv[0]} was killed")
        return None
    if proc.returncode in (126, 127) and not proc.stdout:
        result.update(status="unavailable", error=proc.stderr.strip() or "cannot run")
        result["warnings"].append(fallback_line("unavailable"))
        return None
    if proc.stdout:
        result["raw_ref"] = _save_raw(cfg.repo_root, capability, proc.stdout)
    if proc.returncode != 0:
        tail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()[-3:]
        result.update(
            status="error", error=f"exit {proc.returncode}: " + (" | ".join(tail) or "no output")
        )
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        result.update(status="error", error=f"payload is not JSON ({exc})")
        return None


def _staleness(cfg: Config, project: str, explicit: bool) -> str | None:
    """Return why a query on project would read a stale index, or None when it is fresh."""
    if explicit and project != cfg.state.get("project_name"):
        return None  # an index this repository did not record: nothing to compare
    last = cfg.state.get("last_index")
    head = head_commit(cfg.repo_root)
    recorded = last.get("head") if isinstance(last, dict) else None
    if recorded is None:
        return f"{STATE_REL} records no indexed commit; run `hyper_codegraph.py index`"
    if head is not None and recorded != head:
        return (
            f"the index was built at {recorded[:12]} but HEAD is {head[:12]}; "
            "run `hyper_codegraph.py index`"
        )
    return None


def run_capability(
    capability: str,
    *,
    repo_root: Path | str | None = None,
    project: str | None = None,
    base: str | None = None,
    symbol: str | None = None,
    pattern: str | None = None,
    ledger: Path | None = None,
) -> dict:
    """Run one capability and return its hacf.codegraph-result/v1 object.

    `ledger` is the ledger whose `files:` globs map files to components (default:
    spec/compiled/architecture.yml under repo_root). Raises UsageError (PlaceholderRefused
    included) before anything runs; every other failure is reported in the result (status
    error).
    """
    if capability not in CAPABILITIES:
        raise UsageError(f"unknown capability {capability}")
    started = time.monotonic()
    given = {"project": project, "base": base, "symbol": symbol, "pattern": pattern}
    root_text = str(repo_root) if repo_root is not None else str(default_repo_root())
    values = validate_placeholders({"repo_root": root_text, **given})
    result = new_result(capability)
    result["inputs"] = {k: v for k, v in values.items() if k in NEEDS[capability]}
    try:
        _dispatch(capability, Path(root_text), values, result, ledger)
    except UsageError:
        raise
    except CodeGraphError as exc:
        result.update(status="error", error=str(exc))
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def _dispatch(
    capability: str, root: Path, values: dict[str, str], result: dict, ledger: Path | None
) -> None:
    cfg = load_config(root)
    result["backend"] = cfg.backend
    result["backend_version"] = cfg.state.get("version")
    if capability not in cfg.entry.get("capabilities", {}):
        result["status"] = "unsupported"
        why = "is none" if cfg.backend == BACKEND_NONE else f"{cfg.backend} lacks {capability}"
        result["warnings"].append(f"code-graph backend {why}; " + fallback_line("unsupported"))
        return
    explicit = "project" in values
    if "project" in NEEDS[capability] and not explicit:
        recorded = cfg.state.get("project_name")
        if recorded is not None:
            values.update(validate_placeholders({"project": recorded}))
    if "base" in NEEDS[capability] and "base" not in values:
        values.update(validate_placeholders({"base": cfg.base_branch}))
    result["inputs"] = {k: v for k, v in values.items() if k in NEEDS[capability]}
    result["project"] = values.get("project")
    if capability == "delete" and "project" not in values:
        result["status"] = "ok"
        result["warnings"].append(f"no project recorded in {STATE_REL}; nothing to delete")
        return
    # A missing executable is the root cause, so it is reported before any index state.
    prefix = resolve_prefix(cfg)
    if prefix is None:
        result.update(status="unavailable", error=f"{EXE_NAME} is not installed or not on PATH")
        result["warnings"].append(fallback_line("unavailable"))
        return
    if "project" in NEEDS[capability] and "project" not in values:
        result["status"] = "stale_index"
        result["warnings"].append(
            f"no index recorded in {STATE_REL}; run `hyper_codegraph.py index`; "
            + fallback_line("stale_index")
        )
        return
    if capability in QUERY_CAPABILITIES:
        stale = _staleness(cfg, values["project"], explicit)
        if stale:
            result["status"] = "stale_index"
            result["warnings"] += [stale, fallback_line("stale_index")]
            return
    if capability == "index":
        _index(cfg, prefix, values, result)
        return
    payload = call_backend(cfg, prefix, capability, values, result)
    if payload is None:
        return
    checked = capability in TRUNCATION_IS_ERROR + TRUNCATION_IS_WARNING
    truncated = truncation_keys(payload) if checked else []
    if truncated and capability in TRUNCATION_IS_ERROR:
        result.update(status="error", error="truncated: " + ", ".join(truncated))
        return
    normalized = normalize_payload(capability, payload, cfg.repo_root)
    result.update(normalized)
    if truncated:
        result["warnings"].append("truncated: " + ", ".join(truncated))
    if capability == "symbol_impact":
        result["warnings"].append(SYMBOL_IMPACT_WARNING)
    paths = sorted(set(normalized["impacted_files"]) | set(normalized["changed_files"]))
    components, warnings = map_components(cfg.repo_root, paths, ledger)
    result["components"] = components
    result["warnings"] += warnings
    result["status"] = "ok"
    if capability == "delete":
        _forget_project(cfg, values["project"])


def _index(cfg: Config, prefix: list[str], values: dict[str, str], result: dict) -> None:
    """Index the repository, find its project name with list_projects and record the state."""
    if call_backend(cfg, prefix, "index", values, result) is None:
        return
    head = head_commit(cfg.repo_root)
    listing = new_result("list_projects", cfg.backend)
    payload = call_backend(cfg, prefix, "list_projects", {}, listing)
    if payload is None:
        result.update(status=listing["status"], error=f"list_projects: {listing['error']}")
        return
    truncated = truncation_keys(payload)
    if truncated:  # the repository's project may be on a page not read: record nothing (R33)
        result.update(status="error", error="list_projects: truncated: " + ", ".join(truncated))
        return
    real_root = os.path.realpath(str(cfg.repo_root))
    matches = [
        p["name"]
        for p in normalize_list_projects(payload, cfg.repo_root)
        if os.path.realpath(p["root"]) == real_root
    ]
    if len(matches) != 1:
        raise CodeGraphError(
            f"list_projects names {len(matches)} project(s) rooted at {real_root}; expected one"
        )
    name = validate_placeholders({"project": matches[0]})["project"]
    version = backend_version(prefix, cfg.repo_root, _timeout(cfg, "status"))
    state = dict(cfg.state)
    state.update(
        project_name=name,
        version=version,
        last_index={"head": head, "at": _runtime.utc_now_iso()},
    )
    write_state(cfg.repo_root, state)
    result.update(status="ok", project=name, backend_version=version)
    if version != PINNED_VERSION:
        result["warnings"].append(
            f"{EXE_NAME} version {version or 'unknown'} is not the pinned {PINNED_VERSION}"
        )


def _forget_project(cfg: Config, project: str) -> None:
    """Drop a deleted project from the state file (the file goes when nothing else is left)."""
    if cfg.state.get("project_name") != project:
        return
    state = {k: v for k, v in cfg.state.items() if k not in ("project_name", "last_index")}
    if state:
        write_state(cfg.repo_root, state)
    else:
        (cfg.repo_root / STATE_REL).unlink()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def doctor(repo_root: Path) -> dict:
    """Return the doctor report: config layers, resolution chain, version, index, verified."""
    cfg = load_config(repo_root)
    report: dict[str, Any] = {
        "backend": cfg.backend,
        "layers": {
            "defaults": DEFAULTS_REL if cfg.layers["defaults"] == "file" else "embedded",
            "local": LOCAL_REL if cfg.layers["local"] else None,
            "state": STATE_REL if cfg.layers["state"] else None,
        },
        "install_method": cfg.install_method,
        "base_branch": cfg.base_branch,
        "verified": None,
        "pinned_version": None,
        "resolution": [],
        "prefix": None,
        "backend_version": None,
        "index": None,
        "warnings": [],
    }
    if cfg.backend == BACKEND_NONE:
        report["warnings"].append("code-graph backend is none; " + FALLBACK)
        return report
    report["verified"] = bool(cfg.entry.get("verified", False))
    report["pinned_version"] = str(cfg.entry.get("pinned_version", PINNED_VERSION))
    if not report["verified"]:
        report["warnings"].append(
            f"{cfg.backend} is unverified: the S04 contract test has not passed for it"
        )
    prefix, lines = resolution_chain(cfg)
    report["resolution"], report["prefix"] = lines, prefix
    if prefix is None:
        report["warnings"].append(fallback_line("unavailable"))
    else:
        version = backend_version(prefix, cfg.repo_root, _timeout(cfg, "status"))
        report["backend_version"] = version
        if version != report["pinned_version"]:
            report["warnings"].append(
                f"{cfg.backend} version {version or 'unknown'} is not the pinned "
                f"{report['pinned_version']}"
            )
    project = cfg.state.get("project_name")
    if project is None:
        report["index"] = {"project": None, "status": "no index recorded"}
    else:
        stale = _staleness(cfg, project, explicit=False)
        report["index"] = {
            "project": project,
            "last_index": cfg.state.get("last_index"),
            "status": "stale" if stale else "fresh",
        }
        if stale:
            report["warnings"].append(stale)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="hyper_codegraph.py",
        description="HACF code-graph adapter (codebase-memory-mcp 0.12.1).",
        allow_abbrev=False,
    )
    parser.add_argument("command", choices=[*CAPABILITIES, "doctor"])
    parser.add_argument("--repo-root", metavar="DIR", help="repository root (default: git top)")
    parser.add_argument("--project", metavar="NAME", help="backend project (default: recorded)")
    parser.add_argument(
        "--base", metavar="REF", help="diff_impact base: a branch or a full commit id"
    )
    parser.add_argument("--symbol", metavar="NAME", help="symbol_impact function name")
    parser.add_argument("--pattern", metavar="REGEX", help="search name pattern")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


def _print_result(result: dict) -> None:
    print(f"{result['capability']}: {result['status']}")
    for key in ("backend", "backend_version", "project", "components", "impacted_files"):
        print(f"{key}: {json.dumps(result[key])}")
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    if result["error"]:
        print(f"error: {result['error']}")


def _print_doctor(report: dict) -> None:
    layers = report["layers"]
    print(f"backend: {report['backend']}")
    print(f"defaults: {layers['defaults']}")
    print(f"local: {layers['local'] or f'{LOCAL_REL} (missing)'}")
    print(f"state: {layers['state'] or f'{STATE_REL} (missing)'}")
    if report["backend"] != BACKEND_NONE:
        print(f"verified: {str(report['verified']).lower()}")
        for line in report["resolution"]:
            print(f"resolution: {line}")
        print(
            f"version: {report['backend_version'] or 'unknown'} (pinned {report['pinned_version']})"
        )
        print(f"index: {json.dumps(report['index'])}")
    for warning in report["warnings"]:
        print(f"warning: {warning}")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code (0 ok and fallbacks, 1 error, 2 usage or refused)."""
    _runtime.require_runtime()
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        if ns.command == "doctor":
            root = validate_placeholders({"repo_root": ns.repo_root or str(default_repo_root())})[
                "repo_root"
            ]
            report = doctor(Path(root))
            if ns.json:
                print(json.dumps(report, indent=2))
            else:
                _print_doctor(report)
            return EXIT_OK
        result = run_capability(
            ns.command,
            repo_root=ns.repo_root,
            project=ns.project,
            base=ns.base,
            symbol=ns.symbol,
            pattern=ns.pattern,
        )
    except CodeGraphError as exc:
        print(f"hyper_codegraph.py: {exc}", file=sys.stderr)
        return exc.code
    if ns.json:
        print(json.dumps(result, indent=2))
    else:
        _print_result(result)
    return EXIT_ERROR if result["status"] == "error" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
