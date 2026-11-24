#!/usr/bin/env python3
"""hyper_gate_hook.py — Claude Code PreToolUse hook (matcher Bash) for the gated lore verbs.

It raises Claude Code's native prompt for `hyper_lore.py` approve, reject, retire, reopen and
review, writes the single-use ticket hyper_lore.py consumes, and refuses the modes that cannot
prompt. Normative design: the firewall section of HACF's v0.6.0 design notes (not shipped) (R6,
R7, R23). This is a procedural gate, not a security boundary.

Stdlib only and fail closed: every import and all logic run inside a catch-all, and the only exit
codes are 0 and 2, because Claude Code treats any other code as a non-blocking error.

Stdin is Claude Code's hook JSON: `permission_mode`, `cwd` and `tool_input.command`.
- default, acceptEdits, auto: exit 0 with hookSpecificOutput permissionDecision ask.
- dontAsk, bypassPermissions, plan: exit 2 with the switch-mode reason.
- a missing or unknown mode, bad JSON or any exception: exit 2, for every Bash command.
- a command with no gated invocation: exit 0 with no output.

Formats this hook shares with hyper_lore.py (S05a), read from its code:
- Ticket: `<project>/.agents/state/lore/tickets/<any name>`, JSON
  {"schema": "hacf.lore-ticket/v1", "verb", "id", "nonce", "created_at", "expires_at"}. hyper_lore
  scans every file in the directory and needs `expires_at - created_at` in (0, 120 s]. Timestamps
  are ISO 8601 with `+00:00`. `<project>` is CLAUDE_PROJECT_DIR, else the input `cwd`.
- Pending record: `<project>/.agents/state/lore/pending/<ID>.json`, written by approval-request,
  with `title` and `affects: {supersedes: [..], retires: [..], reanchors: [..]}`. The hook never
  validates it (hyper_lore does); it only shows what the record says.

A command that mentions hyper_lore but cannot be resolved gets ask (deny where ask is unavailable)
with no ticket, because nothing can be bound. An invocation the matcher misses gets no ticket
either, so hyper_lore.py refuses it unless a person confirms at a TTY.
"""

from __future__ import annotations


def _die(reason: object) -> None:
    """Exit 2 with reason, whatever else is broken."""
    import os
    import sys

    try:
        sys.stderr.write(f"hyper_gate_hook: {reason}\n")
        sys.stderr.flush()
    finally:
        os._exit(2)


try:
    import json
    import os
    import re
    import secrets
    import shlex
    import sys
    import tempfile
    import unicodedata
    from datetime import datetime, timedelta, timezone
except BaseException as exc:  # a hook that cannot import must still block
    _die(f"cannot import its modules ({exc!r})")

ASK_MODES = ("default", "acceptEdits", "auto")
DENY_MODES = ("dontAsk", "bypassPermissions", "plan")
SWITCH_MODE = "switch to default or acceptEdits (Shift+Tab) and retry"
# hyper_lore.py's subcommands, exactly (14 MR 1; the tripwire test reads its parser). A hyper_lore
# argv resolves only when argv[0] is one of these; `-h`/`--help` alone gets no decision.
LORE_VERBS = (
    "validate",
    "show",
    "list",
    "graph",
    "propose",
    "note",
    "stale",
    "resolve-anchors",
    "sync",
    "approval-request",
    "approval-cancel",
    "approve",
    "reject",
    "retire",
    "reopen",
    "review",
    "apply-outbox",
    "index",
    "init",
    "import",
    "merge-driver",
    "merge-generated",
    "renumber",
    "install-drivers",
)
GATED = ("approve", "reject", "retire", "reopen", "review")
LEADS = {
    "approve": "Approve {}",
    "reject": "Reject {}",
    "retire": "Retire {}",
    "reopen": "Reopen {}",
    "review": "Review {} as still valid",
}
NO_TITLE = "(title not in pending record)"
TICKET_SCHEMA = "hacf.lore-ticket/v1"
TICKET_TTL = timedelta(seconds=120)
TICKETS_REL = ".agents/state/lore/tickets"
PENDING_REL = ".agents/state/lore/pending"
SCRIPT = "hyper_lore.py"
MODULE = "hyper_lore"
MAX_REASON = 500
MAX_TITLE = 120

ID_RE = re.compile(r"^(ADR|STORY|CANON)-\d{4,}$")
PUNCTUATION = ";&|()<>\n"
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)(\w+)\1")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")
PYTHON_RE = re.compile(r"^(python|pypy)[\d.]*$")
ANSI_RE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.?")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "eval", "source", "."}
WRAPPERS = {
    "builtin",
    "command",
    "env",
    "exec",
    "ionice",
    "nice",
    "nohup",
    "parallel",
    "setsid",
    "stdbuf",
    "sudo",
    "time",
    "timeout",
    "uv",
    "uvx",
    "xargs",
}
HEREDOC_SHELLS = ("bash", "sh", "zsh", "dash", "ksh")
HEREDOC_ALLOWED = ("list", "show", "validate", "graph", "propose", "note")
HEREDOC_DEPTH = 2
PY_BODY_HINT = (
    "run hyper_lore.py directly with --body-file <file>"
    " (a Python or heredoc body cannot be checked)"
)
SED_PRINT_RE = re.compile(r"\d+(,\d+)?p", re.ASCII)
GIT_READ = ("diff", "log", "show", "status", "commit", "add", "blame")
GIT_EXEC = ("-c", "--config-env", "--exec-path", "--ext-diff")
PYTHON_BODY = object()  # _script_argv's unresolved result for a Python body (MR 3)
ARGV_FEEDERS = ("xargs", "parallel")  # wrappers that can take the argv from stdin (13 MR 1)
UV_RUN_FLAGS = ("--locked", "--frozen", "--offline", "--no-sync", "-q", "--quiet")
HARMLESS_PROGRAMS = ("true", "false", ":", "test", "[", "mkdir")
# HACF's own dev commands (OPR.99.0.10.13 MR 2, R51): they read their arguments as data and never
# run them. Closed like DATA_ONLY; adding an entry needs a replan, and a test pins both lists.
DEV_COMMANDS = {
    "pytest": (("pytest",), ("python", "-m", "pytest"), ("python3", "-m", "pytest")),
    "scripts": (
        "hyper_ledger.py",
        "hyper_spec.py",
        "hyper_scope.py",
        "hypergraph_updater.py",
        "archive_specs.py",
    ),
}


def _any_args(tokens: list[str]) -> bool:
    return True


def _rg_rule(tokens: list[str]) -> bool:
    """Allow rg without --pre or --pre-glob, which run a command."""
    return not any(token.startswith("--pre") for token in tokens[1:])


def _sed_rule(tokens: list[str]) -> bool:
    """Allow sed -n whose every script is Np or N,Mp and whose other tokens are files."""
    if len(tokens) < 2 or tokens[1] != "-n":
        return False
    rest = tokens[2:]
    scripts: list[str] = []
    files: list[str] = []
    if "-e" in rest:
        args = iter(rest)
        for token in args:
            if token == "-e":
                script = next(args, None)
                if script is None:
                    return False
                scripts.append(script)
            else:
                files.append(token)
    elif rest:
        scripts, files = [rest[0]], rest[1:]
    return (
        bool(scripts)
        and all(SED_PRINT_RE.fullmatch(script) for script in scripts)
        and not any(name.startswith("-") for name in files)
    )


def _git_rule(tokens: list[str]) -> bool:
    """Allow git with a GIT_READ subcommand first and no option that runs a command."""
    if len(tokens) < 2 or tokens[1] not in GIT_READ:
        return False
    return not any(
        token in GIT_EXEC or token.startswith(("--config-env=", "--exec-path="))
        for token in tokens[1:]
    )


def _ruff_rule(tokens: list[str]) -> bool:
    """Allow ruff whose first non-option argument is check or format."""
    return next((token for token in tokens[1:] if not token.startswith("-")), None) in (
        "check",
        "format",
    )


# The closed data-only list (OPR.99.0.10.5 MR 1, replan R48): a segment that mentions hyper_lore
# and is not a hyper_lore call resolves with no decision only through this dict. Adding a program
# needs a replan and its own tests; dev/tests/firewall/test_gate_hook.py pins the keys.
DATA_ONLY = {
    "echo": _any_args,
    "printf": _any_args,
    "cat": _any_args,
    "head": _any_args,
    "tail": _any_args,
    "wc": _any_args,
    "ls": _any_args,
    "diff": _any_args,
    "sha256sum": _any_args,
    "grep": _any_args,
    "egrep": _any_args,
    "fgrep": _any_args,
    "rg": _rg_rule,
    "sed": _sed_rule,
    "git": _git_rule,
    "ruff": _ruff_rule,
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _clean(text: object) -> str:
    """Return text on one line with ANSI sequences and control and format characters removed."""
    flat = " ".join(ANSI_RE.sub("", str(text)).split())
    return "".join(ch for ch in flat if unicodedata.category(ch)[0] != "C")


def _mentions(text: str) -> bool:
    """Say whether text names hyper_lore, also when spelled 'hyper_'+'lore'."""
    return MODULE in re.sub(r"['\"+\s]", "", text)


def _expands(token: str) -> bool:
    """Say whether the shell would rewrite token, so its value is unknown here."""
    return "$" in token or "`" in token


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: they are data, and their quotes would break the tokenizer."""
    kept: list[str] = []
    until = None
    for line in command.split("\n"):
        if until is not None:
            if line.strip() == until:
                until = None
            continue
        kept.append(line)
        match = HEREDOC_RE.search(line)
        if match:
            until = match.group(2)
    return "\n".join(kept)


def _heredocs(command: str) -> list[tuple[str, str]]:
    """Return (delimiter, body) for each heredoc _strip_heredocs drops, in order."""
    found: list[tuple[str, str]] = []
    until = None
    body: list[str] = []
    for line in command.split("\n"):
        if until is not None:
            if line.strip() == until:
                found.append((until, "\n".join(body)))
                until = None
            else:
                body.append(line)
            continue
        match = HEREDOC_RE.search(line)
        if match:
            until, body = match.group(2), []
    return found


def _segments(command: str) -> list[list[str]]:
    """Split a command on shell control operators; drop redirections. ValueError if unparseable."""
    return [tokens for tokens, _ in _segments_with_heredocs(command)]


def _segments_with_heredocs(command: str) -> list[tuple[list[str], list[str]]]:
    """Return (tokens, heredoc delimiters) per segment, as _segments splits them."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[tuple[list[str], list[str]]] = [([], [])]
    in_redirect = False
    heredoc_next = False
    for token in lexer:
        if token and set(token) <= set(PUNCTUATION):
            heredoc_next = token == "<<"
            if set(token) & set("<>"):
                if segments[-1][0] and segments[-1][0][-1].isdigit() and not in_redirect:
                    segments[-1][0].pop()  # the 2 of 2>&1
                in_redirect = True
            else:
                segments.append(([], []))
                in_redirect = False
        elif heredoc_next:
            segments[-1][1].append(token[1:] if token.startswith("-") else token)  # <<-EOF
            heredoc_next = False
        elif not in_redirect:
            segments[-1][0].append(token)
    return segments


def _python_target(args: list[str]) -> tuple[str, str, list[str]] | None:
    """Return (kind, value, argv after it) for an interpreter's args: code, module or script."""
    index = 0
    while index < len(args):
        token = args[index]
        if token == "-" or not token.startswith("-"):
            return "script", token, args[index + 1 :]
        if not token.startswith("--"):
            for position, flag in enumerate(token[1:], start=2):
                if flag in "cmXW":
                    attached = token[position:]
                    value = attached or (args[index + 1] if index + 1 < len(args) else "")
                    rest = args[index + 1 :] if attached else args[index + 2 :]
                    if flag == "c":
                        return "code", value, rest
                    if flag == "m":
                        return "module", value, rest
                    index += 0 if attached else 1  # -X and -W take a value
                    break
        index += 1
    return None


def _script_argv(
    tokens: list[str], mention: bool, assigned: bool = False
) -> list[str] | bool | object | None:
    """Return the hyper_lore argv of one segment; True or PYTHON_BODY when unresolved.

    None means the segment is not a hyper_lore call (_scan then applies DATA_ONLY to a mention).
    """
    program = os.path.basename(tokens[0])
    if program in SHELLS:
        # A shell fed from stdin has no code in this segment, so a mention anywhere counts.
        from_stdin = all(arg.startswith("-") for arg in tokens[1:])
        return True if _mentions(" ".join(tokens)) or (mention and from_stdin) else None
    if not (
        program in WRAPPERS or program == SCRIPT or PYTHON_RE.match(program) or _expands(tokens[0])
    ):
        return None
    for index, token in enumerate(tokens):
        name = os.path.basename(token).strip("`")
        if name == SCRIPT:
            return _fed(tokens[:index], tokens[index + 1 :], assigned)
        if not PYTHON_RE.match(name):
            continue
        return _fed(tokens[:index], _python_argv(tokens[index + 1 :], mention), assigned)
    return None


def _python_argv(args: list[str], mention: bool) -> list[str] | bool | object | None:
    """Return the hyper_lore argv an interpreter's args run, as _script_argv does."""
    target = _python_target(args)
    if target is None:
        return None
    kind, value, rest = target
    if kind == "code":
        return PYTHON_BODY if _mentions(value) else None
    if kind == "module":
        if value == "runpy" and rest:
            return rest[1:] if os.path.basename(rest[0]) in (MODULE, SCRIPT) else None
        return rest if value.split(".")[-1] == MODULE else None
    if value == "-":  # a body on stdin
        return PYTHON_BODY if mention else None
    if value == "$":  # what an unquoted $(...) leaves in the script position
        return True if mention else None
    if os.path.basename(value) == SCRIPT:
        return rest
    if _expands(value) or set(value) & set("*?["):
        return True if rest and rest[0] in GATED else None
    return None


def _fed(before: list[str], argv: list[str] | bool | object | None, assigned: bool):
    """Return argv, or True when an xargs/parallel before the script could supply the verb.

    The closed form (13 MR 1, lead ruling P28, R54): no leading assignment (PARALLEL=… carries
    options), the wrapper followed directly by the interpreter or script (no option of any kind),
    a non-empty argv whose argv[0] is a literal (none of `{`, `}`, `:`, `$`, backtick) ungated verb,
    and no later token with a `{`/`}` replacement string or a `:::` source. Anything else is
    unresolved; input can then only append arguments after a literal ungated verb.
    """
    feeder = next(
        (i for i, token in enumerate(before) if os.path.basename(token) in ARGV_FEEDERS), None
    )
    if feeder is None or not isinstance(argv, list):
        return argv
    literal = bool(argv) and argv[0] not in GATED and not set(argv[0]) & set("{}:$`")
    later = before[feeder + 1 :] + argv
    if (
        assigned
        or feeder != len(before) - 1
        or not literal
        or any("{" in token or "}" in token or token.startswith(":::") for token in later)
    ):
        return True
    return argv


def _parse_gated(args: list[str]) -> tuple[list[str], str | None, str | None]:
    """Return (ids, nonce, warrant file) from the arguments after a gated verb."""
    ids: list[str] = []
    nonce = warrant = None
    tokens = iter(args)
    for token in tokens:
        if token == "--nonce":
            nonce = next(tokens, None)
        elif token.startswith("--nonce="):
            nonce = token.split("=", 1)[1]
        elif token == "--warrant-file":
            warrant = next(tokens, None)
        elif token.startswith("--warrant-file="):
            warrant = token.split("=", 1)[1]
        elif not token.startswith("-"):
            ids.append(token)
    return ids, nonce, warrant


def _data_only(assignments: list[str], tokens: list[str]) -> bool:
    """Say whether a segment only reads data (05 MR 1, R48; 13 MR 3-4, R52).

    No leading assignment at all, nothing expands, and the program, named bare (no `/`, so PATH
    resolves it), is in DATA_ONLY and passes its rule.
    """
    if assignments or any(_expands(token) for token in tokens) or "/" in tokens[0]:
        return False
    rule = DATA_ONLY.get(tokens[0])
    return rule is not None and rule(tokens)


def _dev_command(assignments: list[str], tokens: list[str]) -> bool:
    """Say whether a segment is one of HACF's DEV_COMMANDS (13 MR 2-4).

    A leading `uv run` may carry only UV_RUN_FLAGS. Then pytest, `python[3] -m pytest`, or
    `python[3] [./].agents/scripts/<name>` for a DEV_COMMANDS script, named bare, no assignment,
    nothing expanding, and no interpreter option.
    """
    if assignments or any(_expands(token) for token in tokens):
        return False
    command = tokens
    if command[0] == "uv":
        if command[1:2] != ["run"]:
            return False
        command = command[2:]
        while command and command[0].startswith("-"):
            if command[0] not in UV_RUN_FLAGS:
                return False
            command = command[1:]
    if not command or "/" in command[0]:
        return False
    if any(tuple(command[: len(form)]) == form for form in DEV_COMMANDS["pytest"]):
        return True
    scripts = {
        f"{prefix}{name}"
        for prefix in (".agents/scripts/", "./.agents/scripts/")
        for name in DEV_COMMANDS["scripts"]
    }
    return command[0] in ("python", "python3") and len(command) > 1 and command[1] in scripts


def _scan(
    command: str, start: str | None, depth: int = 0, harmless: bool = True
) -> tuple[list[tuple], bool, bool]:
    """Return (gated invocations as (verb, ids, nonce, warrant path), unresolved, Python body).

    Depth 0 is the command itself; a shell heredoc body is re-scanned one level deeper, up to
    HEREDOC_DEPTH (F6). In a body every hyper_lore call must be a HEREDOC_ALLOWED verb or a
    complete gated call; anything else is unresolved (lead ruling P26 for incomplete gated calls).

    `harmless` says every earlier segment had a closed harmless shape (13 MR 4, R52): cd, a
    HARMLESS_PROGRAMS command, a DATA_ONLY or DEV_COMMANDS segment, or a resolved hyper_lore call,
    each with no leading assignment. A data-only or dev read of hyper_lore resolves only then; a
    heredoc body inherits it, because what the outer shell ran reaches the inner one.
    """
    mention = _mentions(command)
    bodies = _heredocs(command)
    try:
        segments = _segments_with_heredocs(_strip_heredocs(command))
    except ValueError:
        return [], mention, False
    gated: list[tuple] = []
    unresolved = False
    python_body = False
    cwd = start
    for segment, delimiters in segments:
        tokens = list(segment)
        assignments: list[str] = []
        while tokens and ASSIGNMENT_RE.match(tokens[0]):
            assignments.append(tokens.pop(0))
        if not tokens:
            harmless = harmless and not assignments  # a bare assignment primes what follows
            continue
        seg_mention = _mentions(" ".join(assignments + tokens))
        if tokens[0] == "cd":
            unresolved = unresolved or seg_mention
            harmless = harmless and not assignments
            target = next((t for t in tokens[1:] if not t.startswith("-")), "~")
            if _expands(target) or "-" in tokens[1:] or (cwd is None and target[:1] not in "/~"):
                cwd = None
            else:
                cwd = os.path.join(cwd or "", os.path.expanduser(target))
            continue
        if (
            os.path.basename(tokens[0]) in HEREDOC_SHELLS
            and delimiters
            and all(arg.startswith("-") for arg in tokens[1:])
        ):
            matching = [body for name, body in bodies if name in delimiters]
            if len(delimiters) != 1 or len(matching) != 1 or depth + 1 > HEREDOC_DEPTH:
                unresolved = True
                continue
            inner = _scan(matching[0], cwd, depth + 1, harmless)
            inner_gated, inner_unresolved, inner_python = inner
            gated += inner_gated
            unresolved = unresolved or inner_unresolved
            python_body = python_body or inner_python
            harmless = False  # not a closed harmless shape (13 MR 4)
            continue
        argv = _script_argv(tokens, mention, bool(assignments))
        plain = argv is None and (
            _data_only(assignments, tokens) or _dev_command(assignments, tokens)
        )
        if argv is None and seg_mention and not (plain and harmless):
            argv = True  # fail closed (F5, R52): not a call, or a read something could redirect
        harmless = harmless and (
            plain
            or isinstance(argv, list)
            or (argv is None and not assignments and tokens[0] in HARMLESS_PROGRAMS)
        )
        if argv is PYTHON_BODY:
            unresolved = python_body = True
        elif argv is True:
            unresolved = True
        elif isinstance(argv, list) and (argv or depth):
            verb, rest = (argv[0], argv[1:]) if argv else ("", [])
            if _expands(verb):
                unresolved = True
            elif verb in GATED:
                ids, nonce, warrant = _parse_gated(rest)
                if any(_expands(value or "") for value in (*ids, nonce, warrant)):
                    unresolved = True
                elif warrant and cwd is None and not warrant.startswith(("/", "~")):
                    unresolved = True
                elif depth and (len(ids) != 1 or not nonce or not warrant):
                    unresolved = True  # lead ruling P26: an incomplete gated call in a body
                else:
                    path = os.path.join(cwd or "", os.path.expanduser(warrant)) if warrant else None
                    gated.append((verb, ids, nonce, path))
            elif depth and verb not in HEREDOC_ALLOWED:
                unresolved = True
            elif verb not in LORE_VERBS and argv not in (["-h"], ["--help"]):
                unresolved = True  # 14 MR 2: `--`, an option or an unknown name is no verb
    return gated, unresolved, python_body


def _pending(project: str, node_id: str) -> dict:
    """Return the pending record of node_id, or {} when it is absent or unreadable."""
    path = os.path.join(project, PENDING_REL, f"{node_id}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _reason(verb: str, node_id: str, pending: dict, warrant: str) -> str:
    """Build the prompt text; the warrant gives way first so the affected ids always show (R23)."""
    title = pending.get("title")
    title = _clean(title)[:MAX_TITLE] if isinstance(title, str) and title.strip() else NO_TITLE
    affects = pending.get("affects")
    ids: list[str] = []
    if isinstance(affects, dict):
        for key in ("supersedes", "retires", "reanchors"):
            values = affects.get(key)
            ids += [_clean(value) for value in values] if isinstance(values, list) else []
    tail = ""
    if verb == "approve":
        tail = f"; supersedes/retires/re-anchors: {', '.join(ids) or 'none'}"
    elif verb == "retire":
        tail = f"; retires: {', '.join(ids) or 'none'}"
    head = f"{LEADS[verb].format(node_id)} {title}; warrant: "
    room = max(MAX_REASON - len(head) - len(tail), 0)
    return (head + _clean(warrant)[:room] + tail)[:MAX_REASON]


def _write_ticket(project: str, verb: str, node_id: str, nonce: str) -> None:
    """Write the single-use ticket atomically, refusing a directory reached through a symlink."""
    directory = os.path.join(project, TICKETS_REL)
    os.makedirs(directory, exist_ok=True)
    if os.path.realpath(directory) != os.path.join(os.path.realpath(project), TICKETS_REL):
        raise ValueError(f"{TICKETS_REL} passes through a symlink")
    now = _now()
    ticket = {
        "schema": TICKET_SCHEMA,
        "verb": verb,
        "id": node_id,
        "nonce": nonce,
        "created_at": now.isoformat(),
        "expires_at": (now + TICKET_TTL).isoformat(),
    }
    fd, tmp = tempfile.mkstemp(prefix=".ticket-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ticket, handle)
        os.replace(tmp, os.path.join(directory, f"{verb}-{node_id}-{secrets.token_hex(8)}.json"))
    except BaseException:
        os.unlink(tmp)
        raise


def _ask(reason: str) -> int:
    decision = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": _clean(reason)[:MAX_REASON],
    }
    sys.stdout.write(json.dumps({"hookSpecificOutput": decision}) + "\n")
    return 0


def _deny(reason: str) -> int:
    sys.stderr.write(f"hyper_gate_hook: {_clean(reason)[:MAX_REASON]}\n")
    return 2


def decide(raw: bytes, environ: dict) -> int:
    """Return the exit code for one hook input; prints the decision or the reason."""
    data = json.loads(raw)
    mode = data.get("permission_mode")
    if mode not in ASK_MODES + DENY_MODES:
        return _deny(
            f"permission_mode {mode!r} is missing or unknown; cannot classify this command"
        )
    command = data["tool_input"]["command"]
    if not isinstance(command, str):
        raise TypeError("tool_input.command is not a string")
    project = environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd")
    gated, unresolved, python_body = _scan(command, data.get("cwd") or project)
    if not gated and not unresolved:
        return 0
    if mode in DENY_MODES:
        hint = f"; {PY_BODY_HINT}" if python_body and mode == "bypassPermissions" else ""
        return _deny(
            f"a gated hyper_lore command needs a prompt, which {mode} cannot show: {SWITCH_MODE}"
            + hint
        )
    if not gated:
        return _ask(
            f"This command mentions hyper_lore but cannot be resolved, so no ticket: {command}"
        )
    if len(gated) > 1:
        return _deny("run one gated hyper_lore verb per command (a request covers one node)")
    verb, ids, nonce, warrant_path = gated[0]
    if len(ids) != 1 or not ID_RE.match(ids[0]) or not nonce or not warrant_path:
        return _deny(f"hyper_lore.py {verb} needs exactly one lore id, --nonce and --warrant-file")
    if not os.path.isfile(warrant_path):
        return _deny(f"--warrant-file {warrant_path} is not a regular file")
    if not project:
        return _deny("neither CLAUDE_PROJECT_DIR nor the input cwd names the project")
    with open(warrant_path, "rb") as handle:
        warrant = handle.read(MAX_REASON * 4).decode("utf-8", errors="replace")
    reason = _reason(verb, ids[0], _pending(project, ids[0]), warrant)
    _write_ticket(project, verb, ids[0], nonce)
    return _ask(reason)


def main() -> None:
    """Run the hook; any failure, including a failed flush at exit, exits 2."""
    try:
        code = decide(sys.stdin.buffer.read(), dict(os.environ))
        sys.stdout.flush()
        sys.stderr.flush()
    except BaseException as exc:  # fail closed (R6)
        _die(f"blocked this Bash call after an internal error ({exc!r})")
    os._exit(code)


if __name__ == "__main__":
    main()
