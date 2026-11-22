"""Interpreter guard and the one timestamp helper shared by HACF scripts.

Every new or modified `.agents/scripts` entry point calls `require_runtime()` before it imports
PyYAML, so an interpreter that is too old stops with an actionable message instead of a
traceback. This module uses no syntax newer than Python 3.7, so it still imports (and prints
that message) on an interpreter it rejects.

Timestamps: `utc_now_iso()` writes UTC to the second with a `+00:00` offset, never `Z`;
`parse_ts()` accepts both, because `datetime.fromisoformat` only learned `Z` in Python 3.11
and the skills run scripts on the system Python 3.10.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

MIN_PYTHON = (3, 10)
MIN_PYYAML = (5, 4)

_FIXES = """\
Fix it with one of these, then run the command again:
  - any OS (uv): uv python install 3.12, then run HACF with that interpreter
  - macOS (Homebrew): brew install python@3.12
  - Ubuntu/Debian (deadsnakes): sudo add-apt-repository ppa:deadsnakes/ppa
    && sudo apt install python3.12
  - PyYAML: python3 -m pip install --user 'pyyaml>=5.4'
    (Ubuntu/Debian: sudo apt install python3-yaml)"""


def _version_tuple(text: str) -> tuple[int, ...]:
    """Return the leading numeric parts of a version string ("5.4.1" -> (5, 4, 1))."""
    match = re.match(r"\d+(?:\.\d+)*", text.strip())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def _yaml_version() -> str | None:
    """Return PyYAML's version string, or None when PyYAML cannot be imported."""
    try:
        import yaml
    except ImportError:
        return None
    return str(getattr(yaml, "__version__", ""))


def require_runtime() -> None:
    """Exit with status 2 and per-OS fixes unless Python >= 3.10 and PyYAML >= 5.4.

    A missing PyYAML, or one whose version cannot be read, counts as too old.
    """
    problems = []
    python = ".".join(str(part) for part in sys.version_info[:3])
    if tuple(sys.version_info[:2]) < MIN_PYTHON:
        problems.append(f"Python {python} is older than 3.10")
    yaml_version = _yaml_version()
    if yaml_version is None:
        problems.append("PyYAML is not installed")
    elif _version_tuple(yaml_version) < MIN_PYYAML:
        problems.append(f"PyYAML {yaml_version or '(unknown version)'} is older than 5.4")
    if problems:
        print(
            f"HACF needs Python >= 3.10 and PyYAML >= 5.4, but {sys.executable}: "
            + "; ".join(problems)
            + ".\n"
            + _FIXES,
            file=sys.stderr,
        )
        sys.exit(2)


def utc_now_iso() -> str:
    """Return the current UTC time to the second, as `YYYY-MM-DDTHH:MM:SS+00:00` (never `Z`)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(text: str) -> datetime:
    """Parse an ISO 8601 timestamp ending in `Z` or `+00:00`, on Python 3.10 too.

    A trailing `Z` is replaced with `+00:00` before `datetime.fromisoformat`, which accepts `Z`
    only from Python 3.11. Raises ValueError for text it cannot parse.
    """
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
