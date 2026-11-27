#!/usr/bin/env python3
"""seat-config.py: validate and resolve the hacf team's per-seat configuration.

    seat-config.py validate [--dir D] [--caps F] [--project-root P] [--loadout F]
    seat-config.py resolve  [--dir D] [--caps F] [--role R] [--section seats|context|routing]
                            [--project-root P] [--loadout F] --json

D defaults to the openrig/hacf/ directory this script sits in. It holds seat-config.yml (HACF's
defaults), roles.yml, caps.yml (or the file given to --caps), agents/hacf/agent.yaml (the skills
and harness defaults), the user's optional overrides.yml, the user's own guidance files under
agents/hacf/local/, and presets/<name>.yml, read only when seat-config.yml names one. The three
config files are schema v2 (hacf.seat-config/v2, hacf.seat-overrides/v2, hacf.seat-preset/v2);
a file declaring v1 is read with v1's keys and noted on stderr. An overrides.yml with no schema
line is v2. A preset must carry a non-empty `tested:` line, so a v1 preset is refused.

Precedence, per role and per key: default < preset < override. A later layer replaces a key's
value and never merges inside it, except tools and skills: each layer's removes, then its adds,
edit the default list. routing is top level, per key: seat-config.yml < overrides.yml, and an
unset destination is the discovery rule. The floor: AskUserQuestion is always denied and
codebase-memory-mcp is never denied, neither by name nor by a glob that could match it.

--project-root P looks for P/.agents/skills/<name>/SKILL.md; --loadout F is a JSON
{role: [skill id, ...]}. A skill found only in P/.agents/skills is known only for a harness OpenRig
scans that directory for (OPENRIG_SKILL_ROOTS: codex) and only with a SKILL.md OpenRig takes
(name, description, body; name equal to the directory). Otherwise it is refused with a loadout,
and without one warned and listed under the skills cell's `held` instead of its value.
This tool never runs `rig`. It only reads: it never writes, creates or deletes any file, and
overrides.yml and agents/hacf/local/ least of all.

validate exits 0 when everything is valid, 1 with one line per problem on stderr
(`<file>: <key path>: <reason>`), 2 on a usage error. Lines starting `warning:` or `note:` never
change the exit code. resolve validates first (same exit 1) and prints
{"seats": {role: {key: {"value": v, "source": s}}}, "routing": {key: {...}}}; --section seats or
context prints only "seats", --section routing only "routing".
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import stat
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("seat-config.py: PyYAML is not installed\n")
    sys.exit(2)

SEAT_CONFIG = "seat-config.yml"
OVERRIDES = "overrides.yml"
CAPS = "caps.yml"
ROLES = "roles.yml"
AGENT = "agents/hacf/agent.yaml"
LOCAL = "local"
AGENT_DIR = "agents/hacf"
V1 = "v1"
V2 = "v2"
SCHEMAS = {
    SEAT_CONFIG: ("hacf.seat-config/v1", "hacf.seat-config/v2"),
    OVERRIDES: ("hacf.seat-overrides/v1", "hacf.seat-overrides/v2"),
    "preset": ("hacf.seat-preset/v1", "hacf.seat-preset/v2"),
}
CAPS_SCHEMA = "hacf.caps/v1"
# The keys seat-config.yml may set per seat; overrides and presets may set every SEAT_KEYS key.
BASE_KEYS = ("model", "effort")
SEAT_KEYS = BASE_KEYS + ("tools", "skills", "instructions", "harness")
CONTEXT_KEYS = ("cap_tokens", "behaviour", "reset_scope")
RESERVED_V1 = ("harness", "instructions", "skills", "tools", "mcp")
RESERVED = ("mcp",)
TOP_KEYS = {
    (SEAT_CONFIG, V1): ("schema", "sections", "preset", "reserved", "seats"),
    (SEAT_CONFIG, V2): ("schema", "sections", "preset", "reserved", "seats", "routing"),
    (OVERRIDES, V1): ("schema", "seats"),
    (OVERRIDES, V2): ("schema", "seats", "routing"),
    ("preset", V1): ("schema", "seats"),
    ("preset", V2): ("schema", "tested", "seats"),
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
BEHAVIOURS = ("planned_handover", "restart_at_durable_point", "checkpoint_message")
RESET_SCOPES = ("mission", "slice")
SECTION_KEYS = {"seats": SEAT_KEYS, "context": CONTEXT_KEYS}
# The floor (seat-rules.py binds these same names).
REQUIRED_DENY = "AskUserQuestion"
# A bare MCP server deny (roles v2): `mcp__<server>`, one server segment with no `__` in it.
MCP_SERVER_DENY = re.compile(r"mcp__[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*")
# TJ: keep codebase-memory-mcp wherever MCP is on, so no role may deny it.
KEPT_MCP = "codebase-memory-mcp"
# A glob deny is refused when it matches the server or one of its tools (normalised names).
KEPT_PROBES = ("mcp--%s" % KEPT_MCP, "mcp--%s--search-graph" % KEPT_MCP)
GLOB_CHARS = "*?["
INSTRUCTIONS_MAX_BYTES = 65536
HARNESS_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# The runtimes `rig seat set-runtime` accepts; any other harness is deferred out of v0.6.7 (R161).
APPLIABLE_HARNESSES = ("claude-code", "codex", "pi")
# Where OpenRig finds a skill, per runtime, in its precedence order: @openrig/cli 0.7.23
# daemon/dist/domain/skill-discovery.js:191-211 (listScanRoots). <cwd> is the project root: every
# HACF seat declares `cwd: ../..` (rig.yaml).
PROJECT_ROOT_SCAN = "<cwd>/.agents/skills"
OPENRIG_SKILL_ROOTS = {
    "claude-code": (
        "<cwd>/.claude/skills",
        "<spec-install>/skills",
        "~/.claude/skills",
        "~/.openrig/skills",
    ),
    "codex": (
        PROJECT_ROOT_SCAN,
        "<spec-install>/skills",
        "~/.agents/skills",
        "~/.openrig/skills",
    ),
    "pi": ("<spec-install>/skills",),
}
# SKILL.md front matter and body: skill-discovery.js:16 (FRONTMATTER_RE).
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
NAME_DIFFERS = "frontmatter name differs"
SESSION_RE = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+")
TAG_RE = re.compile(r"[a-z0-9][a-z0-9:._-]{0,63}")
DESTINATIONS = ("question", "update")
TAG_KEYS = ("question", "update", "answer", "extra")
FORMAT = {"options": "A/B/C", "recommended": "required"}
DISCOVER = {"rule": "discover"}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Problems:
    """The lines of one validation, in the order found."""

    def __init__(self):
        """Start with no problems, warnings or notes."""
        self.lines = []
        self.warnings = []
        self.notes = []

    def add(self, file, path, reason):
        """Record one `<file>: <key path>: <reason>` line."""
        self.lines.append("%s: %s: %s" % (file, path or "-", reason))

    def warn(self, file, path, reason):
        """Record a warning: it never changes the exit code."""
        self.warnings.append("warning: %s: %s: %s" % (file, path, reason))

    def note_v1(self, file):
        """Record that a file was read with v1's rules."""
        self.notes.append("note: %s: schema v1: read as v1; the new keys need v2" % file)


def read_yaml(path, name, problems):
    """The parsed mapping at path, or None after recording why not."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        problems.add(name, "-", "cannot read (%s)" % (exc.strerror or exc))
        return None
    except yaml.YAMLError as exc:
        problems.add(name, "-", "not valid YAML (%s)" % str(exc).splitlines()[0])
        return None
    if not isinstance(data, dict):
        problems.add(name, "-", "not a mapping")
        return None
    return data


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def floor_breach(tool):
    """Why denying `tool` breaks the floor, or None."""
    name = tool.lower().replace("_", "-")
    # How Claude Code reads a bare or cross-server `*` is undocumented: refuse a superset.
    glob = any(char in name for char in GLOB_CHARS)
    if KEPT_MCP in name or glob and any(fnmatch.fnmatchcase(p, name) for p in KEPT_PROBES):
        return "%r denies %s, the floor: never denied" % (tool, KEPT_MCP)
    return None


def check_edits(file, path, key, value, lists, problems):
    """True when value is a mapping holding only `lists`, each a list of non-empty strings."""
    if not isinstance(value, dict):
        problems.add(file, path, "must be a mapping with %s" % " and/or ".join(lists))
        return False
    good = True
    for name, items in value.items():
        if name not in lists:
            problems.add(file, "%s.%s" % (path, name), "unknown key")
            good = False
        elif not _strings(items):
            problems.add(file, "%s.%s" % (path, name), "must be a list of non-empty strings")
            good = False
    return good


def check_tools(file, path, value, problems):
    """Check a `tools: {add_deny, remove_deny}` value and the floor."""
    if not check_edits(file, path, "tools", value, ("add_deny", "remove_deny"), problems):
        return
    for name, items in value.items():
        where = "%s.%s" % (path, name)
        for tool in items:
            if "(" in tool:
                problems.add(file, where, "%r is a parenthesized rule" % tool)
            elif name == "remove_deny" and tool == REQUIRED_DENY:
                problems.add(file, where, "%s is the floor: always denied" % REQUIRED_DENY)
            elif name == "add_deny" and floor_breach(tool):
                problems.add(file, where, floor_breach(tool))
            elif tool.startswith("mcp__") and not MCP_SERVER_DENY.fullmatch(tool):
                problems.add(file, where, "%r is not a bare MCP server deny" % tool)
    for tool in value.get("add_deny", []):
        if tool in value.get("remove_deny", []):
            problems.add(file, path, "%r is in both add_deny and remove_deny" % tool)


def check_routing(file, value, problems):
    """Check a top-level `routing:` mapping."""
    if not isinstance(value, dict):
        problems.add(file, "routing", "must be a mapping")
        return
    for key, entry in value.items():
        path = "routing.%s" % key
        if key not in DESTINATIONS + ("tags", "format"):
            problems.add(file, path, "unknown key")
        elif key == "format":
            if entry != FORMAT:
                problems.add(
                    file,
                    path,
                    "must be exactly {options: %s, recommended: %s}"
                    % (FORMAT["options"], FORMAT["recommended"]),
                )
        elif not isinstance(entry, dict):
            problems.add(file, path, "must be a mapping")
        elif key == "tags":
            check_tags(file, path, entry, problems)
        else:
            for name, destination in entry.items():
                where = "%s.%s" % (path, name)
                if name != "destination":
                    problems.add(file, where, "unknown key")
                elif destination != DISCOVER and not (
                    isinstance(destination, dict)
                    and list(destination) == ["session"]
                    and isinstance(destination["session"], str)
                    and SESSION_RE.fullmatch(destination["session"])
                ):
                    problems.add(
                        file,
                        where,
                        "must be {rule: discover} or {session: <name>@<rig>}, got %r"
                        % (destination,),
                    )


def check_tags(file, path, tags, problems):
    """Check `routing.tags`: question, update and answer are tags, extra a list of tags."""
    for name, value in tags.items():
        where = "%s.%s" % (path, name)
        if name not in TAG_KEYS:
            problems.add(file, where, "unknown key")
            continue
        if name == "extra" and not isinstance(value, list):
            problems.add(file, where, "must be a list of tags")
            continue
        for tag in value if name == "extra" else [value]:
            if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
                problems.add(file, where, "not a tag (%s): %r" % (TAG_RE.pattern, tag))


def check_value(file, path, key, value, problems):
    """Check one key's value. skills and instructions need the directory: see check_files."""
    if key == "tools":
        check_tools(file, path, value, problems)
    elif key == "skills":
        check_edits(file, path, key, value, ("add", "remove"), problems)
    elif key == "instructions":
        if not _strings(value):
            problems.add(file, path, "must be a list of local/<file> strings, got %r" % (value,))
    elif key == "harness":
        if not isinstance(value, str) or not HARNESS_RE.fullmatch(value):
            problems.add(file, path, "harness must match %s, got %r" % (HARNESS_RE.pattern, value))
    elif key == "model":
        if not isinstance(value, str) or not value.strip():
            problems.add(file, path, "model must be a non-empty string, got %r" % (value,))
    elif key == "effort":
        if value not in EFFORTS:
            problems.add(
                file, path, "effort must be one of %s, got %r" % ("|".join(EFFORTS), value)
            )
    elif key == "cap_tokens":
        if not _is_int(value) or value <= 0:
            problems.add(file, path, "cap_tokens must be a positive int, got %r" % (value,))
    elif key == "behaviour":
        if value not in BEHAVIOURS:
            problems.add(
                file, path, "behaviour must be one of %s, got %r" % ("|".join(BEHAVIOURS), value)
            )
    elif key == "reset_scope":
        if value not in RESET_SCOPES:
            problems.add(
                file,
                path,
                "reset_scope must be one of %s, got %r" % ("|".join(RESET_SCOPES), value),
            )


def check_seats(file, seats, roles, allowed, reserved, problems):
    """Check a `seats:` mapping of role -> {key: value}."""
    if not isinstance(seats, dict):
        problems.add(file, "seats", "must be a mapping of role to entry")
        return
    for role in seats:
        if role not in roles:
            problems.add(file, "seats.%s" % role, "unknown role (not in roles.yml)")
            continue
        entry = seats[role]
        if entry is None:
            continue
        if not isinstance(entry, dict):
            problems.add(file, "seats.%s" % role, "must be a mapping")
            continue
        for key, value in entry.items():
            path = "seats.%s.%s" % (role, key)
            if key in reserved:
                problems.add(file, path, "reserved key (a later version will define it)")
            elif key not in allowed:
                what = "unknown key"
                if key in CONTEXT_KEYS:
                    what = "not a key of this file (it belongs in caps.yml or overrides.yml)"
                elif key in BASE_KEYS:
                    what = "not a key of this file (it belongs in seat-config.yml)"
                elif key in SEAT_KEYS:
                    what = "not a key of this file (it belongs in overrides.yml or a preset)"
                problems.add(file, path, what)
            else:
                check_value(file, path, key, value, problems)


def check_top(file, data, allowed, reserved, problems):
    """Refuse a top-level key that is unknown or reserved."""
    for key in data:
        if key in allowed:
            continue
        if file.startswith("presets/") and key == "routing" and "tested" in allowed:
            problems.add(file, "routing", "presets may not set routing")
        else:
            problems.add(file, str(key), "reserved key" if key in reserved else "unknown key")


def check_layer(file, kind, data, roles, problems):
    """Check one of seat-config.yml, a preset or overrides.yml by the version it declares."""
    declared = data.get("schema")
    old, new = SCHEMAS[kind]
    absent_ok = kind == OVERRIDES and "schema" not in data
    if declared not in (old, new) and not absent_ok:
        wanted = "%s or %s%s" % (old, new, " (or absent)" if kind == OVERRIDES else "")
        problems.add(file, "schema", "must be %s, got %r" % (wanted, declared))
    version = V1 if declared == old else V2
    reserved = RESERVED_V1 if version == V1 else RESERVED
    if version == V1:
        problems.note_v1(file)
    check_top(file, data, TOP_KEYS[(kind, version)], reserved, problems)
    if "routing" in data and "routing" in TOP_KEYS[(kind, version)]:
        check_routing(file, data["routing"], problems)
    if kind == "preset":  # v1 has no tested key, so a v1 preset cannot state one
        tested = data.get("tested") if version == V2 else None
        if not isinstance(tested, str) or not tested.strip():
            problems.add(file, "tested", "a preset must state what was tested")
    if roles is None:
        return
    if kind == SEAT_CONFIG:
        allowed = BASE_KEYS
    else:
        allowed = (BASE_KEYS if version == V1 else SEAT_KEYS) + CONTEXT_KEYS
    # A v2 overrides.yml may set routing alone.
    if kind == OVERRIDES and version == V2 and "routing" in data and "seats" not in data:
        return
    check_seats(file, data.get("seats"), roles, allowed, reserved, problems)


def check_roles_floor(table, name, problems):
    """The base deny lists are under the floor too, so no resolved list can break it."""
    for role, entry in table.items():
        deny = entry.get("tools_deny") if isinstance(entry, dict) else None
        if not isinstance(deny, list):
            continue
        path = "roles.%s.tools_deny" % role
        if REQUIRED_DENY not in deny:
            problems.add(name, path, "lacks %s, the floor: always denied" % REQUIRED_DENY)
        for tool in deny:
            if isinstance(tool, str) and floor_breach(tool):
                problems.add(name, path, floor_breach(tool))


def read_agent(directory, problems):
    """agent.yaml's mapping, {} after recording why not, or None when there is no such file."""
    path = os.path.join(directory, *AGENT.split("/"))
    if not os.path.lexists(path):
        return None
    return read_yaml(path, AGENT, problems) or {}


def _get(node, *keys):
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def resource_skills(agent):
    """The ids of agent.yaml's resources.skills."""
    items = _get(agent, "resources", "skills")
    return {_get(i, "id") for i in items} - {None} if isinstance(items, list) else set()


def project_skill(project_root, name):
    """Whether the project holds .agents/skills/<name>/SKILL.md as a regular file."""
    if project_root is None or name in (".", "..") or "/" in name:
        return False
    try:
        path = os.path.join(project_root, ".agents", "skills", name, "SKILL.md")
        return stat.S_ISREG(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def skill_md_problem(content, name):
    """Why OpenRig 0.7.23 would not take the SKILL.md text of skill `name`, or None.

    A port of @openrig/cli 0.7.23 daemon/dist/domain/skill-discovery.js: parseSkillFrontmatter
    (:21-56, its reasons in its order), then the name equals the directory name (:176).
    """
    match = FRONTMATTER_RE.match(content)
    if not match:
        return "no YAML frontmatter delimited by --- lines"
    text, body = match.groups()
    try:
        front = yaml.safe_load(text[:-1] if text.endswith("\r") else text)
    except (ValueError, yaml.YAMLError, RecursionError):
        return "frontmatter YAML parse error"
    if not isinstance(front, dict):
        return "frontmatter is not a YAML mapping"
    for key in ("name", "description"):
        value = front.get(key)
        if not isinstance(value, str) or not value.strip():
            return "frontmatter missing required %s field" % key
    if not body.strip():
        return "SKILL.md body is empty"
    return None if front["name"] == name else NAME_DIFFERS


def project_skill_problem(project_root, name):
    """skill_md_problem of the project's SKILL.md for `name`, read as OpenRig reads it."""
    path = os.path.join(project_root, ".agents", "skills", name, "SKILL.md")
    try:  # no newline translation, and bad bytes replaced, as Node's utf-8 read does
        with open(path, encoding="utf-8", errors="replace", newline="") as handle:
            content = handle.read()
    except OSError as exc:
        return "cannot read it (%s)" % (exc.strerror or exc)
    return skill_md_problem(content, name)


def role_harness(loaded, role):
    """The role's resolved harness: agent.yaml's runtime < preset < override."""
    value = _get(loaded["agent"], "profiles", role, "preferences", "runtime")
    for _, entry in _upper(loaded, role):
        value = entry.get("harness", value)
    return value


def project_only_refusal(project_root, name, harness):
    """Why OpenRig would not take a skill only the project holds, or None when it would."""
    where = "%s is only in <project>/.agents/skills" % name
    roots = OPENRIG_SKILL_ROOTS.get(harness) if isinstance(harness, str) else None
    if roots is None or PROJECT_ROOT_SCAN not in roots:
        none = "none; harness %s is deferred, not applied" % harness
        return "%s, which OpenRig 0.7.23 does not scan for %s (scan roots: %s)" % (
            where,
            harness,
            ", ".join(roots) if roots else none,
        )
    why = project_skill_problem(project_root, name)
    if why == NAME_DIFFERS:
        return (
            "%s, and its SKILL.md frontmatter name is not %s, so OpenRig 0.7.23 does not take it"
            % (where, name)
        )
    if why:
        return "%s, and OpenRig 0.7.23 would not take its SKILL.md: %s" % (where, why)
    return None


def instruction_problem(directory, entry):
    """Why an instructions entry is refused, or None."""
    parts = entry.split("/")
    if parts[0] != LOCAL or len(parts) < 2 or ".." in parts:
        return "must be %s/<file> under %s/, with no '..'" % (LOCAL, AGENT_DIR)
    base = os.path.join(directory, *AGENT_DIR.split("/"))
    path = base
    try:
        for part in parts:  # local itself included
            path = os.path.join(path, part)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                return "is or passes through a symlink"
    except FileNotFoundError:
        return "no such file under %s/" % AGENT_DIR
    except (OSError, ValueError) as exc:
        return "cannot read (%s)" % (getattr(exc, "strerror", None) or exc)
    if not stat.S_ISREG(info.st_mode):
        return "not a regular file"
    if info.st_size > INSTRUCTIONS_MAX_BYTES:
        return "larger than %d bytes" % INSTRUCTIONS_MAX_BYTES
    return None


def check_files(file, data, loaded, directory, project_root, loadout, problems):
    """Check a layer's skills against the known names and its instructions against the files."""
    seats = _get(data, "seats")
    if not isinstance(seats, dict):
        return
    for role, entry in seats.items():
        if role not in (loaded["roles"] or ()) or not isinstance(entry, dict):
            continue
        entries = entry.get("instructions")
        if _strings(entries):
            for item in entries:
                why = instruction_problem(directory, item)
                if why:
                    problems.add(file, "seats.%s.instructions" % role, "%s: %s" % (item, why))
        if "skills" not in entry:
            continue
        path = "seats.%s.skills" % role
        if loaded["agent"] is None:
            problems.add(file, path, "skills: needs %s in %s" % (AGENT, directory))
            continue
        added = _get(entry["skills"], "add")
        if not _strings(added):
            continue
        known = resource_skills(loaded["agent"])
        for name in added:
            if name in known or (loadout is not None and name in loadout.get(role, ())):
                continue
            why = None
            if project_skill(project_root, name):
                why = project_only_refusal(project_root, name, role_harness(loaded, role))
                if why is None:
                    continue
            if loadout is None:
                reason = "unverified: rig skill loadout unavailable"
                if why:  # OpenRig would not find it, so it is not rendered
                    loaded["held"].setdefault(role, set()).add(name)
                    reason = "%s; %s" % (reason, why)
                problems.warn(file, "%s.add.%s" % (path, name), reason)
            else:
                problems.add(file, path + ".add", why or "unknown skill %s" % name)


def load_all(directory, caps_path, problems, roles_path=None, project_root=None, loadout=None):
    """Read and check every file; return what resolve needs. Refused when problems.lines."""
    roles_path = roles_path or os.path.join(directory, ROLES)
    rname = os.path.basename(roles_path)
    roles_doc = read_yaml(roles_path, rname, problems)
    roles, table = None, {}
    if roles_doc is not None:
        found = roles_doc.get("roles")
        if isinstance(found, dict) and found:
            roles, table = set(found), found
            check_roles_floor(found, rname, problems)
        else:
            problems.add(rname, "roles", "no roles table")
    result = {"roles": roles, "table": table, "agent": read_agent(directory, problems)}
    result["held"] = {}  # {role: skills check_files keeps out of the render}
    layers = []

    config = read_yaml(os.path.join(directory, SEAT_CONFIG), SEAT_CONFIG, problems)
    preset_name = None
    if config is not None:
        check_layer(SEAT_CONFIG, SEAT_CONFIG, config, roles, problems)
        preset_name = config.get("preset")
        if preset_name is not None:
            if (
                not isinstance(preset_name, str)
                or not preset_name
                or "/" in preset_name
                or preset_name.startswith(".")
            ):
                problems.add(
                    SEAT_CONFIG, "preset", "must be null or a preset name, got %r" % (preset_name,)
                )
                preset_name = None
    result["config"] = config

    preset = None
    if preset_name is not None:
        path = os.path.join(directory, "presets", preset_name + ".yml")
        if not os.path.isfile(path):
            problems.add(
                SEAT_CONFIG, "preset", "names presets/%s.yml, which does not exist" % preset_name
            )
        else:
            name = "presets/%s.yml" % preset_name
            preset = read_yaml(path, name, problems)
            if preset is not None:
                check_layer(name, "preset", preset, roles, problems)
                layers.append((name, preset))
    result["preset"], result["preset_name"] = preset, preset_name

    overrides = None
    path = os.path.join(directory, OVERRIDES)
    if os.path.lexists(path):
        overrides = read_yaml(path, OVERRIDES, problems)
        if overrides is not None:
            check_layer(OVERRIDES, OVERRIDES, overrides, roles, problems)
            layers.append((OVERRIDES, overrides))
    result["overrides"] = overrides

    for name, data in layers:
        # A v1 file's new keys are already refused as reserved; check nothing more of them.
        if data.get("schema") not in (SCHEMAS[OVERRIDES][0], SCHEMAS["preset"][0]):
            check_files(name, data, result, directory, project_root, loadout, problems)

    caps = read_yaml(caps_path, os.path.basename(caps_path), problems)
    cname = os.path.basename(caps_path)
    if caps is not None:
        if caps.get("schema") != CAPS_SCHEMA:
            problems.add(cname, "schema", "must be %s, got %r" % (CAPS_SCHEMA, caps.get("schema")))
        seats = caps.get("seats")
        if not isinstance(seats, dict):
            problems.add(cname, "seats", "must be a mapping of role to entry")
        elif roles is not None:
            for role in sorted(roles - set(seats)):
                problems.add(cname, "seats.%s" % role, "no cap for this role of roles.yml")
            check_seats(cname, seats, roles, CONTEXT_KEYS, RESERVED_V1, problems)
    result["caps"] = caps
    return result


def edited(base, layers, remove_key, add_key):
    """(list, source): each (source, edits) layer's removes, then adds, on the base list."""
    out, sources = list(base), ["default"]
    for source, edits in layers:
        removed = edits.get(remove_key, [])
        after = [item for item in out if item not in removed]
        for item in edits.get(add_key, []):
            if item not in after:
                after.append(item)
        if after != out:
            sources.append(source)
        out = after
    return out, "+".join(sources)


def _upper(loaded, role):
    """[(source, entry)] of the role in the preset and then overrides.yml."""
    psource = "preset:%s" % loaded["preset_name"]
    found = []
    for data, source in ((loaded["preset"], psource), (loaded["overrides"], "override")):
        entry = _get(data, "seats", role)
        if isinstance(entry, dict):
            found.append((source, entry))
    return found


def resolve(loaded, role=None, section=None):
    """{role: {key: {value, source}}} at default < preset < override."""
    keys = SECTION_KEYS.get(section) or SEAT_KEYS + CONTEXT_KEYS
    out = {}
    caps_seats = _get(loaded["caps"], "seats") or {}
    cfg_seats = _get(loaded["config"], "seats") or {}
    for name in sorted(loaded["roles"]):
        if role is not None and name != role:
            continue
        upper = _upper(loaded, name)
        row = {}
        for key in keys:
            if key in ("tools", "skills"):
                cell = resolve_edits(loaded, name, key, upper)
            else:
                cell = None
                if key == "instructions":
                    cell = {"value": [], "source": "default"}
                if key == "harness":
                    runtime = _get(loaded["agent"], "profiles", name, "preferences", "runtime")
                    if runtime is not None:
                        cell = {"value": runtime, "source": "default"}
                base = (caps_seats if key in CONTEXT_KEYS else cfg_seats).get(name)
                found = [("default", base)] if isinstance(base, dict) else []
                for source, entry in found + upper:
                    if key in entry:
                        cell = {"value": entry[key], "source": source}
            if cell is None:
                continue
            if key == "harness" and cell["value"] not in APPLIABLE_HARNESSES:
                cell["deferred"] = (
                    "harness %s for %s: deferred in v0.6.7 (OpenRig 0.7.23 has no per-seat path "
                    "for a community harness); not applied" % (cell["value"], name)
                )
            row[key] = cell
        out[name] = row
    return out


def resolve_edits(loaded, role, key, upper):
    """The composed tools or skills cell of a role, or None when it has no default list."""
    if key == "tools":
        base = _get(loaded["table"].get(role), "tools_deny")
        names = ("remove_deny", "add_deny")
    elif loaded["agent"] is None:
        base = None
    else:
        base = _get(loaded["agent"], "profiles", role, "uses", "skills") or []
        names = ("remove", "add")
    if not isinstance(base, list):
        return None
    layers = [(source, entry[key]) for source, entry in upper if key in entry]
    items, source = edited(base, layers, *names)
    if key == "tools":
        return {"value": {"deny": items}, "source": source}
    held = loaded["held"].get(role, set())
    cell = {"value": [item for item in items if item not in held], "source": source}
    if held.intersection(items):
        cell["held"] = [item for item in items if item in held]
    return cell


def added_skills(loaded, role):
    """The role's resolved skills that a preset or overrides.yml adds to agent.yaml's list."""
    cell = resolve_edits(loaded, role, "skills", _upper(loaded, role))
    added = {
        name for _, entry in _upper(loaded, role) for name in _get(entry, "skills", "add") or []
    }
    return added & set(cell["value"]) if cell else set()


def resolve_routing(loaded):
    """{key: {value, source}}: seat-config.yml < overrides.yml; destinations default to discover."""
    layers = [
        (_get(loaded["config"], "routing"), "default"),
        (_get(loaded["overrides"], "routing"), "override"),
    ]
    out = {}
    for key in ("question.destination", "update.destination", "tags", "format"):
        cell = {"value": dict(DISCOVER), "source": "default"} if "." in key else None
        for routing, source in layers:
            node = routing
            for part in key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node is not None:
                cell = {"value": node, "source": source}
        if cell is not None:
            out[key] = cell
    return out


def read_loadout(path):
    """The {role: [skill id]} of a --loadout file, or None when it cannot be used."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in data.values()):
        return None
    return data


def build_parser():
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(prog="seat-config.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "resolve"):
        p = sub.add_parser(name)
        p.add_argument("--dir")
        p.add_argument("--caps")
        p.add_argument("--project-root")
        p.add_argument("--loadout")
        if name == "resolve":
            p.add_argument("--role")
            p.add_argument("--section", choices=("seats", "context", "routing"))
            p.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv):
    """Run the CLI and return its exit code."""
    args = build_parser().parse_args(argv)  # argparse exits 2 with the usage line
    directory = args.dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caps_path = args.caps or os.path.join(directory, CAPS)
    loadout = None
    if args.loadout is not None:
        loadout = read_loadout(args.loadout)
        if loadout is None:
            sys.stderr.write(
                "seat-config.py: --loadout %s: not a readable JSON {role: [skill id]}\n"
                % args.loadout
            )
            return 2
    problems = Problems()
    loaded = load_all(
        directory, caps_path, problems, project_root=args.project_root, loadout=loadout
    )
    for line in problems.notes + problems.warnings + problems.lines:
        sys.stderr.write(line + "\n")
    if problems.lines:
        return 1
    if args.command == "validate":
        return 0
    if args.role is not None and args.role not in loaded["roles"]:
        sys.stderr.write("seat-config.py: unknown role %s\n" % args.role)
        return 2
    doc = {}
    if args.section != "routing":
        doc["seats"] = resolve(loaded, args.role, args.section)
    if args.section in (None, "routing"):
        doc["routing"] = resolve_routing(loaded)
    sys.stdout.write(json.dumps(doc, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
