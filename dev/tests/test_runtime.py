"""Tests for .agents/scripts/_runtime.py: the interpreter guard and the timestamp helper."""

from __future__ import annotations

import json
import re
from pathlib import Path

import _runtime
from conftest import PY310, SCRIPTS_DIR

GUARD_PROBE = f"""\
import sys
sys.path.insert(0, {str(SCRIPTS_DIR)!r})
import _runtime
_runtime.require_runtime()
print("guard passed")
"""

PARSE_PROBE = f"""\
import json, sys
sys.path.insert(0, {str(SCRIPTS_DIR)!r})
from _runtime import parse_ts
z = parse_ts("2026-10-21T00:00:00Z")
offset = parse_ts("2026-10-21T00:00:00+00:00")
print(json.dumps({{
    "python": list(sys.version_info[:2]),
    "equal": z == offset,
    "aware": z.utcoffset() is not None and offset.utcoffset() is not None,
    "z": z.isoformat(),
}}))
"""


def _probe(tmp_path: Path, name: str, source: str) -> Path:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return script


def _yaml_shim(tmp_path: Path, body: str) -> dict[str, str]:
    """Write a stand-in yaml module and return the env that puts it first on sys.path."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "yaml.py").write_text(body, encoding="utf-8")
    return {"PYTHONPATH": str(shim)}


def test_runtime_parse_ts_z_on_310(tmp_path, run_script):
    """Under /usr/bin/python3, `Z` and `+00:00` parse to equal aware datetimes."""
    proc = run_script(_probe(tmp_path, "parse_probe.py", PARSE_PROBE), interpreter=PY310)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["equal"] and result["aware"], result
    assert result["z"] == "2026-10-21T00:00:00+00:00"


def test_runtime_guard_message(tmp_path, run_script):
    """An old-PyYAML shim makes require_runtime() exit 2 with the per-OS guidance."""
    env = _yaml_shim(tmp_path, '__version__ = "5.3"\n')
    proc = run_script(_probe(tmp_path, "guard_probe.py", GUARD_PROBE), env=env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "guard passed" not in proc.stdout
    assert "HACF needs Python >= 3.10 and PyYAML >= 5.4" in proc.stderr
    assert "PyYAML 5.3 is older than 5.4" in proc.stderr
    for fix in ("uv python install 3.12", "Homebrew", "deadsnakes"):
        assert fix in proc.stderr


def test_runtime_guard_missing_yaml(tmp_path, run_script):
    """A missing PyYAML counts as too old."""
    env = _yaml_shim(tmp_path, 'raise ImportError("PyYAML hidden by the test shim")\n')
    proc = run_script(_probe(tmp_path, "guard_probe.py", GUARD_PROBE), env=env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "PyYAML is not installed" in proc.stderr


def test_runtime_guard_passes_here():
    """Both check.sh legs (venv PyYAML 6, system PyYAML 5.4.1) meet the floor."""
    _runtime.require_runtime()


def test_runtime_utc_now_iso_offset(frozen_clock):
    """utc_now_iso() writes `+00:00`, never `Z`, at seconds precision."""
    assert _runtime.utc_now_iso() == "2026-10-21T12:00:00+00:00"


def test_runtime_utc_now_iso_live_format():
    """The unfrozen clock has the same shape."""
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", _runtime.utc_now_iso())
