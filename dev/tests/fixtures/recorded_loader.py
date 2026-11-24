"""Load a cleaned recorded capture with the running checkout's values put back.

`normalize_recorded.py` writes `recorded/<S>/` with placeholders in place of host data. `load`
substitutes them in every string value (embedded JSON text included): `<REPO>` -> this checkout's
root, `<HOME>` -> the home directory, `<RUNTIME>` -> `/run/user/<uid>`, `<TMP>` ->
`<tempdir>/pytest-of-<user>`, `<TMP-SLUG>` -> that path's slug (no leading `/`, `/` -> `-`), and
`<USER>` -> the account name. The id placeholders (`<SESSION-n>`, `<UUID-n>`, `<REQUEST-n>`) stay
opaque: readers compare ids only for equality. A host placeholder left anywhere after that (a key
the loader does not walk, for one) makes `load` raise.

Readers import it as `from fixtures.recorded_loader import load, paths`.
"""

from __future__ import annotations

import getpass
import json
import os
import tempfile
from pathlib import Path
from typing import Any

RECORDED = Path(__file__).resolve().parent / "recorded"
REPO_ROOT = Path(__file__).resolve().parents[3]
HOST_PLACEHOLDERS = ("<REPO>", "<HOME>", "<RUNTIME>", "<TMP>", "<TMP-SLUG>", "<USER>")


class LeftoverPlaceholder(ValueError):
    """A host placeholder survived substitution."""


def substitutions() -> list[tuple[str, str]]:
    """Return (placeholder, this host's value) pairs."""
    user = getpass.getuser()
    tmp = f"{tempfile.gettempdir()}/pytest-of-{user}"
    return [
        ("<REPO>", str(REPO_ROOT)),
        ("<HOME>", str(Path.home())),
        ("<RUNTIME>", f"/run/user/{os.getuid()}"),
        ("<TMP-SLUG>", tmp.lstrip("/").replace("/", "-")),
        ("<TMP>", tmp),
        ("<USER>", user),
    ]


def _substitute(value: Any, pairs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for placeholder, real in pairs:
            value = value.replace(placeholder, real)
        return value
    if isinstance(value, list):
        return [_substitute(item, pairs) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, pairs) for key, item in value.items()}
    return value


def load(set_name: str, file_name: str) -> Any:
    """Return recorded/<set_name>/<file_name> parsed, its placeholders substituted."""
    data = json.loads((RECORDED / set_name / file_name).read_text(encoding="utf-8"))
    loaded = _substitute(data, substitutions())
    text = json.dumps(loaded)
    left = [p for p in HOST_PLACEHOLDERS if p in text]
    if left:
        raise LeftoverPlaceholder(f"{set_name}/{file_name}: {', '.join(left)} left after loading")
    return loaded


def paths(set_name: str, pattern: str) -> list[Path]:
    """Return the cleaned files of recorded/<set_name>/ matching pattern, sorted."""
    return sorted((RECORDED / set_name).glob(pattern))
