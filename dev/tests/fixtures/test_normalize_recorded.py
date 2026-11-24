"""The recorded-capture normaliser, its loader and the writers' raw targets (OPR.0.6.3.3).

These run on any checkout. The determinism and raw-value leak tests read `recorded-raw/`, which the
public export leaves out, so they live in dev/tests/private_records/test_normalize_recorded_raw.py.
No private value is written here, and the raw-only key names below are bare words, never quoted
JSON tokens.
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from fixtures import normalize_recorded as nr
from fixtures import recorded_loader
from fixtures.recorded_loader import load, paths

FIXTURES = REPO_ROOT / "dev" / "tests" / "fixtures"
RAW = FIXTURES / "recorded-raw"
CLEAN = FIXTURES / "recorded"

# F6 needle keys and F7 raw-only keys, as bare words (a trailing `*` is a prefix).
RAW_ONLY = "rate_limit* isUsingOverage overage* request_id credential_expires_at five_hour".split()
RAW_ONLY += "seven_day unifiedWindows apiKeySource messaging_socket_path".split()


def _docs(value: Any) -> list[Any]:
    """The value, plus the JSON documents a string holds (whole or one per line)."""
    found = nr._documents(value) if isinstance(value, str) else None
    return found[1] if found else []


def _walk(value: Any, key: str | None = None):
    """Yield (key, value) for every value, descending into embedded JSON strings."""
    yield key, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        for doc in _docs(value):
            yield from _walk(doc, key)


# Host identity: always matched as a substring, never as a whole token only.


# ---------------------------------------------------------------------------
# The writers' targets (no live run: each module is imported, never run)
# ---------------------------------------------------------------------------


def _import(rel: str):
    path = REPO_ROOT / "dev" / "tests" / rel
    name = "writer_" + re.sub(r"\W", "_", rel)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("rel", "set_name"),
    [
        ("contract/test_codegraph_live.py", "S04"),
        ("engine/test_executors.py", "S08"),
        ("firewall/test_gate_hook.py", "S11"),
    ],
)
def test_writer_targets_recorded_raw(rel: str, set_name: str) -> None:
    """Each live writer records under dev/tests/fixtures/recorded-raw/<S>/."""
    assert _import(rel).RECORDED.resolve() == RAW / set_name


def test_openrig_writer_targets_recorded_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """With HACF_LIVE_RECORD_DIR unset, the S02 writer records under recorded-raw/S02/."""
    monkeypatch.delenv("HACF_LIVE_RECORD_DIR", raising=False)
    module = _import("contract/test_openrig_live.py")
    assert module._record_dir().resolve() == RAW / "S02"


def test_live_verdict_is_raw() -> None:
    """The release gate's step 10 reads the live verdict where the live test writes it."""
    sys.path.insert(0, str(REPO_ROOT / "dev"))
    try:
        import release_export as rx
    finally:
        sys.path.remove(str(REPO_ROOT / "dev"))
    assert rx.LIVE_VERDICT.startswith("dev/tests/fixtures/recorded-raw/S04/")


# ---------------------------------------------------------------------------
# The normaliser
# ---------------------------------------------------------------------------


def test_keep_list_drops_unknown_keys_at_every_depth() -> None:
    """Only kept keys survive, at any depth; list items keep their position; order is kept."""
    raw = {
        "z_first": 1,
        "keep": {"deep": {"unknown": 3, "kept": 4}, "other": 5},
        "rows": [{"k": 1, "x": 2}, {"x": 3}, {"k": 4}],
        "dropped": "gone",
    }
    spec = {"z_first": nr.ALL, "keep": {"deep": {"kept": nr.ALL}}, "rows": {"k": nr.ALL}}
    ident = nr.Identity([], None)
    out = nr.Cleaner(ident, {}).value(raw, spec)
    assert out == {"z_first": 1, "keep": {"deep": {"kept": 4}}, "rows": [{"k": 1}, {}, {"k": 4}]}
    assert list(out) == ["z_first", "keep", "rows"]


def test_embedded_json_and_ndjson_are_filtered_inside() -> None:
    """A string holding JSON or NDJSON is filtered and rewritten inside, then written compact."""
    ident = nr.Identity([], None)
    cleaner = nr.Cleaner(ident, {"id-a": "<SESSION-1>"})
    spec = nr.Embedded({"type": nr.ALL, "session_id": nr.ALL})
    whole = json.dumps({"type": "result", "session_id": "id-a", "noise": 1}, indent=2)
    assert cleaner.value(whole, spec) == '{"type":"result","session_id":"<SESSION-1>"}'
    lines = '{"type": "a", "x": 1}\n{"type": "b", "session_id": "id-a"}\n'
    assert cleaner.value(lines, spec) == '{"type":"a"}\n{"type":"b","session_id":"<SESSION-1>"}\n'
    assert cleaner.value("plain text", spec) == "plain text"


# ---------------------------------------------------------------------------
# The leak guard (F6), driven by the raw values
# ---------------------------------------------------------------------------


def test_raw_only_keys_are_absent() -> None:
    """No F7 raw-only key is a key in any cleaned file, nor appears as a quoted JSON token."""
    bad = []
    for path in sorted(CLEAN.glob("*/*.json")):
        text = path.read_text(encoding="utf-8")
        keys = {key for key, _ in _walk(json.loads(text)) if key is not None}
        for name in RAW_ONLY:
            stem = name.rstrip("*")
            if name.endswith("*"):
                found = any(key.startswith(stem) for key in keys)
            else:
                found = stem in keys or ('"' + stem + '"') in text
            if found:
                bad.append(f"{path.relative_to(CLEAN)}: {stem}")
    assert bad == []


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


def test_every_cleaned_file_loads_without_placeholders() -> None:
    """Loading every cleaned file leaves no host placeholder (load raises otherwise)."""
    files = sorted(CLEAN.glob("*/*.json"))
    assert files
    for path in files:
        text = json.dumps(load(path.parent.name, path.name))
        assert not any(p in text for p in recorded_loader.HOST_PLACEHOLDERS), path


def test_loader_substitutes_each_placeholder(tmp_path: Path, monkeypatch) -> None:
    """Each placeholder loads to this host's value; the slug is the substituted path's slug."""
    monkeypatch.setattr(recorded_loader, "RECORDED", tmp_path)
    (tmp_path / "SX").mkdir()
    raw = {
        "repo": "<REPO>/x",
        "home": "<HOME>/y",
        "run": "<RUNTIME>/bus",
        "tmp": "<TMP>/pytest-1",
        "slug": "-<TMP-SLUG>-pytest-1",
        "user": "by <USER>",
        "id": "<SESSION-3>",
        "stdout": json.dumps({"cwd": "<TMP>/pytest-1/repo"}),
    }
    (tmp_path / "SX" / "f.json").write_text(json.dumps(raw))
    tmp = f"{tempfile.gettempdir()}/pytest-of-{getpass.getuser()}"
    assert load("SX", "f.json") == {
        "repo": f"{REPO_ROOT}/x",
        "home": f"{Path.home()}/y",
        "run": f"/run/user/{os.getuid()}/bus",
        "tmp": f"{tmp}/pytest-1",
        "slug": "-" + tmp.lstrip("/").replace("/", "-") + "-pytest-1",
        "user": f"by {getpass.getuser()}",
        "id": "<SESSION-3>",
        "stdout": json.dumps({"cwd": f"{tmp}/pytest-1/repo"}),
    }
    assert paths("SX", "*.json") == [tmp_path / "SX" / "f.json"]


def test_loader_raises_on_a_leftover(tmp_path: Path, monkeypatch) -> None:
    """A placeholder where the loader does not substitute (a key) makes load raise."""
    monkeypatch.setattr(recorded_loader, "RECORDED", tmp_path)
    (tmp_path / "SX").mkdir()
    (tmp_path / "SX" / "f.json").write_text(json.dumps({"<HOME>": 1}))
    with pytest.raises(recorded_loader.LeftoverPlaceholder):
        load("SX", "f.json")
