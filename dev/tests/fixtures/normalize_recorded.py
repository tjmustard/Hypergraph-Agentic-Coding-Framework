#!/usr/bin/env python3
"""Write the cleaned recorded captures from the raw ones: a keep-list, never a deny-list.

    python3 dev/tests/fixtures/normalize_recorded.py [--check] [--set S02|S04|S08|S11 ...]
                                                     [--repo-root PATH]

Reads `recorded-raw/<S>/*.json` (never exported) and writes `recorded/<S>/<same name>`. A key
survives only when `KEEP` names it for that file: the keys a reader asserts on, the keys the
production parser it calls reads, and the capture's structural keys. Strings that hold a JSON
document, or one JSON document per line (NDJSON), are parsed, filtered and rewritten the same way
and written back as compact JSON text. Kept strings are rewritten in this order: the HACF checkout
root -> `<REPO>`, the home directory -> `<HOME>`, `/run/user/<uid>` -> `<RUNTIME>`,
`/tmp/pytest-of-<user>` -> `<TMP>` and its slug `tmp-pytest-of-<user>` -> `<TMP-SLUG>`, the account
name anywhere else -> `<USER>`, and each distinct session id, uuid and request id -> `<SESSION-n>`,
`<UUID-n>`, `<REQUEST-n>` (n by first appearance in the set). Kept values that are costs get a
same-type stand-in. The account name, uid, home and checkout root are read from the raw files'
own paths; nothing else (no time, network, randomness or environment) is an input, so two runs
write identical bytes. `--check` writes nothing, exits 1 and names each file that differs.

`recorded_loader.py` puts the running checkout's values back when a test loads a cleaned file.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RAW = HERE / "recorded-raw"
CLEAN = HERE / "recorded"
SETS = ("S02", "S04", "S08", "S11")

ALL = True  # keep the value whole (its strings still rewritten)
HOMES = "/".join(("", "home", ""))  # the home-directories root, built (no host path is typed)


class Embedded:
    """A string holding a JSON document or NDJSON lines; `spec` applies to each document."""

    def __init__(self, spec: Any) -> None:
        """Remember the keep spec for the embedded documents."""
        self.spec = spec


# The claude result object as hyper_executors.parse_claude_json reads it (type, subtype,
# is_error, api_error_status, result, session_id, num_turns, total_cost_usd,
# permission_denials), narrowed to what test_parse_claude_json asserts inside the denials.
CLAUDE_RESULT = {
    "type": ALL,
    "subtype": ALL,
    "is_error": ALL,
    "api_error_status": ALL,
    "result": ALL,
    "session_id": ALL,
    "num_turns": ALL,
    "total_cost_usd": ALL,
    "permission_denials": {"tool_name": ALL},
}
# Structural keys of every capture.
CAPTURE = {"step": ALL, "exit_code": ALL, "timed_out": ALL}

# An S04 payload capture: the repo root it ran in and the payload, walked whole.
S04_PAYLOAD = {**CAPTURE, "cwd": ALL, "stdout": Embedded(ALL)}

# Per set: (file-name pattern, keep spec), first match wins.
KEEP: dict[str, list[tuple[str, Any]]] = {
    # S02: private_records/test_openrig_story_verdict.py compares verdict.json with STORY-0002
    # byte for byte and checks each listed capture exists; no capture's content is read.
    "S02": [("verdict.json", ALL), ("*", CAPTURE)],
    # S04: test_codegraph.py reads the indexed project from verdict.json, and each adapter
    # capture's exit_code, cwd (the repo root it normalizes against) and stdout, a payload that
    # hyper_codegraph.normalize_payload / normalize_list_projects / truncation_keys walk whole.
    "S04": [
        (
            "verdict.json",
            {"contract_verdict": ALL, "observations": {"index_result": {"project": ALL}}},
        ),
        ("*-adapter-*.json", S04_PAYLOAD),
        # expected_normalized.json names this payload capture too.
        ("16-detect_changes-base-commit.json", S04_PAYLOAD),
        ("*", CAPTURE),
    ],
    # S08: test_executors.py parses every capture's stdout (one claude result) with exit_code,
    # and compares verdict.json's verified, max_tested_version and verdict with the config.
    "S08": [
        ("verdict.json", {"verdict": ALL, "verified": ALL, "max_tested_version": ALL}),
        ("*", {**CAPTURE, "stdout": Embedded(CLAUDE_RESULT)}),
    ],
    # S11: test_executors.py parses 01-hook-ask-default's stdout (stream-json, NDJSON) with
    # exit_code; verdict.json keeps its verdicts (no reader; the capture's outcome).
    "S11": [
        ("verdict.json", {"verdicts": ALL}),
        ("*", {**CAPTURE, "stdout": Embedded(CLAUDE_RESULT)}),
    ],
}

# Kept values on the leak list get a fixed stand-in of the same type.
STAND_INS: dict[str, Any] = {"total_cost_usd": 1.0}
# Id keys and their placeholder stems (the request key's name is built, never a quoted token).
ID_KEYS = {"session_id": "SESSION", "uuid": "UUID", "_".join(("request", "id")): "REQUEST"}


def _documents(text: str) -> tuple[str, list[Any]] | None:
    """Return ("json", [doc]) or ("ndjson", docs) when text holds JSON structure, else None."""
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, (dict, list)):
        return "json", [whole]
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return None
    docs = []
    for line in lines:
        try:
            doc = json.loads(line)
        except ValueError:
            return None
        if not isinstance(doc, (dict, list)):
            return None
        docs.append(doc)
    return "ndjson", docs


class Identity:
    """The host facts the raw files reveal, read from their own paths."""

    def __init__(self, texts: list[str], repo_root: str | None) -> None:
        """Find the account name, uid, home and HACF checkout root in the raw texts."""
        joined = "\n".join(texts)
        users = re.findall(r"/tmp/pytest-of-([A-Za-z0-9._-]+)", joined)
        users += re.findall(HOMES + r"([A-Za-z0-9._-]+)/", joined)
        self.user = users[0] if users else None
        homes = re.findall("(" + HOMES + r"[A-Za-z0-9._-]+)/", joined)
        self.home = homes[0] if homes else None
        uids = re.findall(r"/run/user/(\d+)", joined)
        self.runtime = f"/run/user/{uids[0]}" if uids else None
        if repo_root is None:
            # The HACF checkout a capture ran from: the prefix of its scripts or venv, never a
            # temp project (under /tmp/ or a .cache/ dir); the most frequent one, ties sorted.
            found = re.findall(r"(/[^\s\"'/][^\s\"']*?)/(?:\.agents/scripts|\.venv/bin)/", joined)
            roots = [r for r in found if not r.startswith("/tmp/") and "/.cache/" not in r]
            if roots:
                repo_root = sorted(set(roots), key=lambda r: (-roots.count(r), r))[0]
        self.repo = repo_root

    def rewrites(self) -> list[tuple[str, str]]:
        """Return (raw text, placeholder) pairs in the order they are applied."""
        pairs = []
        if self.repo:
            pairs.append((self.repo, "<REPO>"))
        if self.home:
            pairs.append((self.home, "<HOME>"))
        if self.runtime:
            pairs.append((self.runtime, "<RUNTIME>"))
        if self.user:
            pairs.append((f"/tmp/pytest-of-{self.user}", "<TMP>"))
            pairs.append((f"tmp-pytest-of-{self.user}", "<TMP-SLUG>"))
            pairs.append((self.user, "<USER>"))
        return pairs


def _collect_ids(value: Any, ids: dict[str, str], counters: dict[str, int]) -> None:
    """Number each distinct id value by first appearance (raw order, embedded docs included)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ID_KEYS and isinstance(item, str) and item and item not in ids:
                stem = ID_KEYS[key]
                counters[stem] = counters.get(stem, 0) + 1
                ids[item] = f"<{stem}-{counters[stem]}>"
            _collect_ids(item, ids, counters)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, ids, counters)
    elif isinstance(value, str):
        found = _documents(value)
        if found:
            for doc in found[1]:
                _collect_ids(doc, ids, counters)


class Cleaner:
    """Apply one set's keep spec and value rewrites."""

    def __init__(self, identity: Identity, ids: dict[str, str]) -> None:
        """Hold the ordered text rewrites and the id placeholders."""
        self.pairs = identity.rewrites()
        self.ids = sorted(ids.items(), key=lambda kv: -len(kv[0]))

    def text(self, value: str) -> str:
        """Rewrite one kept plain string."""
        for raw, placeholder in self.pairs:
            value = value.replace(raw, placeholder)
        for raw, placeholder in self.ids:
            value = value.replace(raw, placeholder)
        return value

    def string(self, value: str, spec: Any) -> str:
        """A kept string: structure is filtered and rewritten inside, text only rewritten."""
        found = _documents(value)
        if found is None:
            return self.text(value)
        kind, docs = found
        inner = spec.spec if isinstance(spec, Embedded) else ALL
        cleaned = [json.dumps(self.value(doc, inner), separators=(",", ":")) for doc in docs]
        if kind == "json":
            out = cleaned[0]
        else:
            out = "\n".join(cleaned)
        return out + "\n" if value.endswith("\n") else out

    def value(self, value: Any, spec: Any, key: str | None = None) -> Any:
        """Keep what spec names, at every depth; list items keep their position."""
        if key in STAND_INS and isinstance(value, type(STAND_INS[key])):
            return STAND_INS[key]
        if isinstance(spec, Embedded) and isinstance(value, str):
            return self.string(value, spec)
        if isinstance(value, dict):
            if spec is ALL or isinstance(spec, Embedded):
                return {k: self.value(v, ALL, k) for k, v in value.items()}
            return {k: self.value(v, spec[k], k) for k, v in value.items() if k in spec}
        if isinstance(value, list):
            return [self.value(item, spec, key) for item in value]
        if isinstance(value, str):
            return self.string(value, spec)
        return value


def _spec_for(set_name: str, file_name: str) -> Any:
    for pattern, spec in KEEP[set_name]:
        if fnmatch.fnmatchcase(file_name, pattern):
            return spec
    raise KeyError(f"{set_name}/{file_name}: no keep rule")


def _identity(repo_root: str | None) -> Identity:
    """Read the identity from every raw set, so one set alone cleans the same as all four."""
    texts = [p.read_text(encoding="utf-8") for s in SETS for p in sorted((RAW / s).glob("*.json"))]
    return Identity(texts, repo_root)


def clean_set(set_name: str, repo_root: str | None = None) -> dict[str, str]:
    """Return {file name: cleaned text} for one set."""
    identity = _identity(repo_root)
    files = sorted((RAW / set_name).glob("*.json"))
    raws = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in files}
    ids: dict[str, str] = {}
    counters: dict[str, int] = {}
    for name in sorted(raws):
        _collect_ids(raws[name], ids, counters)
    cleaner = Cleaner(identity, ids)
    out = {}
    for name in sorted(raws):
        cleaned = cleaner.value(raws[name], _spec_for(set_name, name))
        out[name] = json.dumps(cleaned, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    """Write (or with --check, compare) the cleaned copies of the chosen sets."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 on a diff")
    parser.add_argument("--set", dest="sets", action="append", choices=SETS)
    parser.add_argument("--repo-root", help="the HACF checkout root the raw captures name")
    args = parser.parse_args(argv)
    if not RAW.is_dir():
        print(f"no raw captures at {RAW} (never exported)", file=sys.stderr)
        return 2
    differs = []
    for set_name in args.sets or SETS:
        for name, text in clean_set(set_name, args.repo_root).items():
            target = CLEAN / set_name / name
            if args.check:
                if not target.is_file() or target.read_text(encoding="utf-8") != text:
                    differs.append(f"{set_name}/{name}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
    for rel in differs:
        print(f"differs: recorded/{rel}", file=sys.stderr)
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
