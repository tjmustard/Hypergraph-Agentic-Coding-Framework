"""openrig/hacf/caps.yml and the guard's schema check of it (v064-context OPR.99.0.8.1).

The shipped file holds TJ's seven caps exactly, and context-guard.sh refuses (exit 2, one stderr
line, no rig call) every malformed caps file. The guard runs with a fake `rig` that records any
call, so a refusal is proven to come before the first rig call.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HACF = REPO_ROOT / "openrig" / "hacf"
CAPS = HACF / "caps.yml"
ROLES = HACF / "roles.yml"
GUARD = HACF / "bin" / "context-guard.sh"

# TJ 2026-11-23 23:57Z, as the slice states it
TJ_CAPS = {
    "lead": {"cap_tokens": 350000, "behaviour": "planned_handover", "reset_scope": "mission"},
    "architect": {
        "cap_tokens": 300000,
        "behaviour": "restart_at_durable_point",
        "reset_scope": "mission",
    },
    "red-team": {
        "cap_tokens": 250000,
        "behaviour": "restart_at_durable_point",
        "reset_scope": "mission",
    },
    "resolve": {
        "cap_tokens": 300000,
        "behaviour": "restart_at_durable_point",
        "reset_scope": "mission",
    },
    "execute": {"cap_tokens": 300000, "behaviour": "checkpoint_message", "reset_scope": "slice"},
    "audit": {
        "cap_tokens": 250000,
        "behaviour": "restart_at_durable_point",
        "reset_scope": "slice",
    },
    "scribe": {
        "cap_tokens": 150000,
        "behaviour": "restart_at_durable_point",
        "reset_scope": "mission",
    },
}

FAKE_RIG = """#!/usr/bin/env python3
import json, os, sys
with open(os.path.join(os.environ["FAKE_RIG_DIR"], "calls.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write("[]")
"""


def test_shipped_caps_are_tj_values() -> None:
    """caps.yml holds the schema, TJ's seven entries exactly and an empty reevaluate list."""
    doc = yaml.safe_load(CAPS.read_text())
    assert doc["schema"] == "hacf.caps/v1"
    assert doc["seats"] == TJ_CAPS
    assert doc["reevaluate"] == [
        "claude-code minor version change",
        "openrig minor version change",
        "start-up trim change (roles, rig keys, CULTURE/protocol/role files)",
        "a seat's model change",
        "each B-metric verdict",
        "each HACF release",
    ]
    assert set(doc) == {"schema", "seats", "reevaluate"}


def test_caps_cover_roles_yml() -> None:
    """Every role of roles.yml has a cap and no cap names an unknown role."""
    roles = yaml.safe_load(ROLES.read_text())["roles"]
    assert set(yaml.safe_load(CAPS.read_text())["seats"]) == set(roles)


def test_caps_header_names_the_decision() -> None:
    """The comment header names TJ's 23:57Z decision and the re-evaluation note."""
    text = CAPS.read_text()
    header = text.split("schema:", 1)[0]
    assert "23:57Z" in header and "TJ" in header
    assert "re-evaluate" in header.lower()


def good() -> dict:
    """A deep copy of the shipped document."""
    return copy.deepcopy(yaml.safe_load(CAPS.read_text()))


def _drop_scribe(d: dict) -> None:
    del d["seats"]["scribe"]


def _add_janitor(d: dict) -> None:
    d["seats"]["janitor"] = dict(d["seats"]["audit"])


def _set(path: tuple, value):
    def f(d: dict) -> None:
        cur = d
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value

    return f


def _drop(path: tuple):
    def f(d: dict) -> None:
        cur = d
        for k in path[:-1]:
            cur = cur[k]
        del cur[path[-1]]

    return f


BAD = [
    ("schema-wrong", _set(("schema",), "hacf.caps/v2"), "schema"),
    ("schema-missing", _drop(("schema",)), "schema"),
    ("role-missing", _drop_scribe, "scribe"),
    ("role-unknown", _add_janitor, "janitor"),
    ("cap-zero", _set(("seats", "audit", "cap_tokens"), 0), "cap_tokens"),
    ("cap-negative", _set(("seats", "audit", "cap_tokens"), -5), "cap_tokens"),
    ("cap-bool", _set(("seats", "audit", "cap_tokens"), True), "cap_tokens"),
    ("cap-string", _set(("seats", "audit", "cap_tokens"), "300000"), "cap_tokens"),
    ("cap-float", _set(("seats", "audit", "cap_tokens"), 300000.0), "cap_tokens"),
    ("cap-missing", _drop(("seats", "audit", "cap_tokens")), "cap_tokens"),
    ("behaviour-unknown", _set(("seats", "audit", "behaviour"), "restart_now"), "behaviour"),
    ("behaviour-missing", _drop(("seats", "audit", "behaviour")), "behaviour"),
    ("scope-unknown", _set(("seats", "audit", "reset_scope"), "turn"), "reset_scope"),
    ("scope-missing", _drop(("seats", "audit", "reset_scope")), "reset_scope"),
    ("entry-not-mapping", _set(("seats", "audit"), 250000), "seats.audit"),
    ("seats-not-mapping", _set(("seats",), []), "seats"),
    ("reevaluate-missing", _drop(("reevaluate",)), "reevaluate"),
    ("reevaluate-not-list", _set(("reevaluate",), "monthly"), "reevaluate"),
    ("reevaluate-non-string", _set(("reevaluate",), ["monthly", 3]), "reevaluate"),
]


def run_guard(tmp_path: Path, caps: Path, roles_text: str | None = None):
    """Run the guard dry with caps beside a roles.yml; return (result, rig calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    rig = bin_dir / "rig"
    rig.write_text(FAKE_RIG)
    rig.chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (caps.parent / "roles.yml").write_text(
        roles_text if roles_text is not None else ROLES.read_text()
    )
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "FAKE_RIG_DIR": str(fake),
        "LC_ALL": "C",
    }
    result = subprocess.run(
        ["bash", str(GUARD), "--rig", "hacf", "--project-root", str(tmp_path), "--caps", str(caps)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log = fake / "calls.log"
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def write(tmp_path: Path, doc) -> Path:
    """Write doc as tmp_path/c/caps.yml."""
    d = tmp_path / "c"
    d.mkdir(exist_ok=True)
    p = d / "caps.yml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


def assert_refused(result, calls, word: str) -> None:
    """Exit 2, exactly one stderr line naming the problem, no rig call."""
    assert result.returncode == 2, (result.stdout, result.stderr)
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert word in lines[0]
    assert calls == []
    assert result.stdout == ""


def test_shipped_caps_accepted(tmp_path: Path) -> None:
    """The shipped file passes the check and the tick goes on to read rig ps."""
    result, calls = run_guard(tmp_path, write(tmp_path, good()))
    assert result.returncode == 0, result.stderr
    assert calls and calls[0][0] == "ps"


@pytest.mark.parametrize(("case", "mutate", "word"), BAD, ids=[b[0] for b in BAD])
def test_bad_caps_refused(tmp_path: Path, case: str, mutate, word: str) -> None:
    """Each malformed caps file exits 2 with one line naming the problem."""
    doc = good()
    mutate(doc)
    result, calls = run_guard(tmp_path, write(tmp_path, doc))
    assert_refused(result, calls, word)


@pytest.mark.parametrize(
    ("text", "word"),
    [("- just\n- a list\n", "mapping"), ("schema: [unclosed\n", "YAML"), ("", "mapping")],
    ids=["list", "bad-yaml", "empty"],
)
def test_unparseable_caps_refused(tmp_path: Path, text: str, word: str) -> None:
    """A caps file that is not a YAML mapping is refused."""
    d = tmp_path / "c"
    d.mkdir()
    (d / "caps.yml").write_text(text)
    result, calls = run_guard(tmp_path, d / "caps.yml")
    assert_refused(result, calls, word)


def test_missing_caps_file_refused(tmp_path: Path) -> None:
    """A --caps path that does not exist is refused."""
    d = tmp_path / "c"
    d.mkdir()
    result, calls = run_guard(tmp_path, d / "nope.yml")
    assert_refused(result, calls, "cannot read")


def test_roles_come_from_beside_the_caps(tmp_path: Path) -> None:
    """A roles.yml beside caps.yml with one more role makes the shipped caps incomplete."""
    roles = yaml.safe_load(ROLES.read_text())
    roles["roles"]["janitor"] = dict(roles["roles"]["audit"])
    result, calls = run_guard(tmp_path, write(tmp_path, good()), yaml.safe_dump(roles))
    assert_refused(result, calls, "janitor")


def test_missing_roles_refused(tmp_path: Path) -> None:
    """Without a roles table beside caps.yml the guard refuses."""
    result, calls = run_guard(tmp_path, write(tmp_path, good()), "schema: hacf.roles/v1\n")
    assert_refused(result, calls, "roles")
