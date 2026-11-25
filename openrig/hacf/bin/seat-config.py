#!/usr/bin/env python3
"""seat-config.py: validate and resolve the hacf team's per-seat configuration.

    seat-config.py validate [--dir D] [--caps F]
    seat-config.py resolve  [--dir D] [--caps F] [--role R] [--section context|seats] --json

D defaults to the openrig/hacf/ directory this script sits in. It holds seat-config.yml (HACF's
defaults, schema hacf.seat-config/v1), roles.yml, caps.yml (or the file F), the user's optional
overrides.yml (schema hacf.seat-overrides/v1; the schema line may be absent) and presets/<name>.yml
(schema hacf.seat-preset/v1), read only when seat-config.yml names one.

Precedence, per role and per key: default < preset < override. An override replaces one key's value
and never merges inside it. This tool only reads: it never writes, creates or deletes any file,
and overrides.yml least of all.

validate exits 0 when everything is valid, 1 with one line per problem on stderr
(`<file>: <key path>: <reason>`), 2 on a usage error. resolve validates first (same exit 1) and
prints {"seats": {role: {key: {"value": v, "source": default|preset:<name>|override}}}}.
"""

from __future__ import annotations

import argparse
import json
import os
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
SCHEMAS = {
    SEAT_CONFIG: "hacf.seat-config/v1",
    OVERRIDES: "hacf.seat-overrides/v1",
    CAPS: "hacf.caps/v1",
    "preset": "hacf.seat-preset/v1",
}
SEAT_KEYS = ("model", "effort")
CONTEXT_KEYS = ("cap_tokens", "behaviour", "reset_scope")
RESERVED = ("harness", "instructions", "skills", "tools", "mcp")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
BEHAVIOURS = ("planned_handover", "restart_at_durable_point", "checkpoint_message")
RESET_SCOPES = ("mission", "slice")
SECTION_KEYS = {"seats": SEAT_KEYS, "context": CONTEXT_KEYS}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Problems:
    """The lines of one validation, in the order found."""

    def __init__(self):
        """Start with no problems."""
        self.lines = []

    def add(self, file, path, reason):
        """Record one `<file>: <key path>: <reason>` line."""
        self.lines.append("%s: %s: %s" % (file, path or "-", reason))


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


def check_value(file, path, key, value, problems):
    """Check one key's value."""
    if key == "model":
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


def check_seats(file, seats, roles, allowed, problems):
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
            if key in RESERVED:
                problems.add(file, path, "reserved key (a later version will define it)")
            elif key not in allowed:
                what = "unknown key"
                if key in CONTEXT_KEYS or key in SEAT_KEYS:
                    what = "not a key of this file (it belongs in %s)" % (
                        "caps.yml or overrides.yml" if key in CONTEXT_KEYS else "seat-config.yml"
                    )
                problems.add(file, path, what)
            else:
                check_value(file, path, key, value, problems)


def check_top(file, data, allowed, problems):
    """Refuse a top-level key that is unknown or reserved."""
    for key in data:
        if key not in allowed:
            problems.add(file, str(key), "reserved key" if key in RESERVED else "unknown key")


def load_all(directory, caps_path, problems):
    """Read and check every file; return what resolve needs, or None when anything is refused."""
    roles_doc = read_yaml(os.path.join(directory, ROLES), ROLES, problems)
    roles = None
    if roles_doc is not None:
        table = roles_doc.get("roles")
        if isinstance(table, dict) and table:
            roles = set(table)
        else:
            problems.add(ROLES, "roles", "no roles table")
    result = {"roles": roles}

    config = read_yaml(os.path.join(directory, SEAT_CONFIG), SEAT_CONFIG, problems)
    preset_name = None
    if config is not None:
        if config.get("schema") != SCHEMAS[SEAT_CONFIG]:
            problems.add(
                SEAT_CONFIG,
                "schema",
                "must be %s, got %r" % (SCHEMAS[SEAT_CONFIG], config.get("schema")),
            )
        check_top(
            SEAT_CONFIG, config, ("schema", "sections", "preset", "reserved", "seats"), problems
        )
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
        if roles is not None:
            check_seats(SEAT_CONFIG, config.get("seats"), roles, SEAT_KEYS, problems)
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
                if preset.get("schema") != SCHEMAS["preset"]:
                    problems.add(
                        name,
                        "schema",
                        "must be %s, got %r" % (SCHEMAS["preset"], preset.get("schema")),
                    )
                check_top(name, preset, ("schema", "seats"), problems)
                if roles is not None:
                    check_seats(
                        name, preset.get("seats"), roles, SEAT_KEYS + CONTEXT_KEYS, problems
                    )
    result["preset"], result["preset_name"] = preset, preset_name

    overrides = None
    path = os.path.join(directory, OVERRIDES)
    if os.path.lexists(path):
        overrides = read_yaml(path, OVERRIDES, problems)
        if overrides is not None:
            if "schema" in overrides and overrides["schema"] != SCHEMAS[OVERRIDES]:
                problems.add(
                    OVERRIDES,
                    "schema",
                    "must be %s (or absent), got %r" % (SCHEMAS[OVERRIDES], overrides["schema"]),
                )
            check_top(OVERRIDES, overrides, ("schema", "seats"), problems)
            if roles is not None:
                check_seats(
                    OVERRIDES, overrides.get("seats"), roles, SEAT_KEYS + CONTEXT_KEYS, problems
                )
    result["overrides"] = overrides

    caps = read_yaml(caps_path, os.path.basename(caps_path), problems)
    cname = os.path.basename(caps_path)
    if caps is not None:
        if caps.get("schema") != SCHEMAS[CAPS]:
            problems.add(
                cname, "schema", "must be %s, got %r" % (SCHEMAS[CAPS], caps.get("schema"))
            )
        seats = caps.get("seats")
        if not isinstance(seats, dict):
            problems.add(cname, "seats", "must be a mapping of role to entry")
        elif roles is not None:
            for role in sorted(roles - set(seats)):
                problems.add(cname, "seats.%s" % role, "no cap for this role of roles.yml")
            check_seats(cname, seats, roles, CONTEXT_KEYS, problems)
    result["caps"] = caps
    return result


def resolve(loaded, role=None, section=None):
    """{role: {key: {value, source}}} at default < preset < override."""
    keys = SECTION_KEYS.get(section) or SEAT_KEYS + CONTEXT_KEYS
    out = {}
    caps_seats = (loaded["caps"] or {}).get("seats") or {}
    cfg_seats = (loaded["config"] or {}).get("seats") or {}
    preset_seats = (loaded["preset"] or {}).get("seats") or {}
    over_seats = (loaded["overrides"] or {}).get("seats") or {}
    psource = "preset:%s" % loaded["preset_name"]
    for name in sorted(loaded["roles"]):
        if role is not None and name != role:
            continue
        row = {}
        for key in keys:
            for layer, source in (
                (caps_seats if key in CONTEXT_KEYS else cfg_seats, "default"),
                (preset_seats, psource),
                (over_seats, "override"),
            ):
                entry = layer.get(name)
                if isinstance(entry, dict) and key in entry:
                    row[key] = {"value": entry[key], "source": source}
        out[name] = row
    return out


def build_parser():
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(prog="seat-config.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "resolve"):
        p = sub.add_parser(name)
        p.add_argument("--dir")
        p.add_argument("--caps")
        if name == "resolve":
            p.add_argument("--role")
            p.add_argument("--section", choices=("context", "seats"))
            p.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv):
    """Run the CLI and return its exit code."""
    args = build_parser().parse_args(argv)  # argparse exits 2 with the usage line
    directory = args.dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caps_path = args.caps or os.path.join(directory, CAPS)
    problems = Problems()
    loaded = load_all(directory, caps_path, problems)
    if problems.lines:
        sys.stderr.write("\n".join(problems.lines) + "\n")
        return 1
    if args.command == "validate":
        return 0
    if args.role is not None and args.role not in loaded["roles"]:
        sys.stderr.write("seat-config.py: unknown role %s\n" % args.role)
        return 2
    sys.stdout.write(
        json.dumps({"seats": resolve(loaded, args.role, args.section)}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
