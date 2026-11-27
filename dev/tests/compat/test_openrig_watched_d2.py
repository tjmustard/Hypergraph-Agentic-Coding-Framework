"""The watched-item checker's product / harness split (OPR.99.0.12.2, mission R114-R125).

A renamed or vanished watched item is REVIEW (the product changed, a person looks); a harness
inability (an unreadable package, an unparseable or ambiguous template) stays SETUP-ERROR; a seed
run stays strict. Every package here is written under tmp_path; no npm, rig or network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_openrig_compat import _green_state, _ns, _package

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

SCOPE = "dist/commands/scope.js"
SLICE_LITERAL = b"`schema: s\n`"


def _scope(path: Path, body: bytes) -> Path:
    """A package whose scope.js is `body` and whose other watched items are the usual ones."""
    package = _package(path)
    (package / SCOPE).write_bytes(body)
    return package


def _items(package: Path) -> dict:
    return oc.watched_items(package)


def test_the_renamed_slice_template_is_found_by_its_alias(tmp_path: Path) -> None:
    """0.7.23 renamed SLICE_MANIFEST to sliceManifest (an arrow function): same literal, found."""
    body = (
        b"const missionManifest = (n) => `m`;\n"
        b"const sliceManifest = (executionDependsOn) => " + SLICE_LITERAL + b";\n"
    )
    got = _items(_scope(tmp_path / "new", body))["SLICE_MANIFEST"]
    assert got == ("ok", SLICE_LITERAL)
    old = b"const missionManifest = (n) => `m`;\nconst SLICE_MANIFEST = " + SLICE_LITERAL + b";\n"
    assert _items(_scope(tmp_path / "old", old))["SLICE_MANIFEST"] == ("ok", SLICE_LITERAL)


def test_a_vanished_item_is_missing_not_setup(tmp_path: Path) -> None:
    """No match, an absent file and an absent parent directory are all `missing` (product)."""
    package = _scope(tmp_path / "p", b"const missionManifest = (n) => `m`;\n")
    assert _items(package)["SLICE_MANIFEST"][0] == "missing"
    (package / "dist/lib/scope/scope-audit.js").unlink()
    assert _items(package)["scope-audit.js"][0] == "missing"
    (package / "daemon/dist/domain/workspace/default-workspace-scaffold.js").unlink()
    (package / "daemon/dist/domain/workspace").rmdir()
    got = _items(package)
    assert got["PROJECT_MANIFEST"][0] == "missing" and got["WORKSPACE_CATALOG"][0] == "missing"


@pytest.mark.parametrize(
    ("body", "text"),
    [
        (
            b"const SLICE_MANIFEST = `a`;\nconst sliceManifest = (x) => `b`;\n",
            "ambiguous: 2 matches",
        ),
        (b"const SLICE_MANIFEST = `a`;\nconst SLICE_MANIFEST = `b`;\n", "ambiguous: 2 matches"),
        (b"const SLICE_MANIFEST = `never closed", "template literal could not be parsed"),
    ],
)
def test_ambiguous_or_unparseable_templates_are_setup(
    tmp_path: Path, body: bytes, text: str
) -> None:
    """More than one match, or a literal that does not close, is the harness's problem."""
    kind, detail = _items(_scope(tmp_path / "p", body))["SLICE_MANIFEST"]
    assert kind == "setup" and text in detail


def test_harness_inabilities_are_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable package root, a directory in place of a file and a too-large file."""
    package = _package(tmp_path / "p")
    assert {k: v[0] for k, v in _items(tmp_path / "nowhere").items()} == dict.fromkeys(
        (w[0] for w in oc.WATCHED), "setup"
    )
    assert "package root unreadable" in _items(tmp_path / "nowhere")["scope-audit.js"][1]
    (package / "dist/lib/scope/scope-audit.js").unlink()
    (package / "dist/lib/scope/scope-audit.js").mkdir()
    assert _items(package)["scope-audit.js"][0] == "setup"
    monkeypatch.setattr(oc, "MAX_WATCHED_BYTES", 4)
    assert _items(package)["lifecycle-manifest.js"][0] == "setup"


def test_run_watched_splits_missing_review_from_setup(tmp_path: Path) -> None:
    """A missing item -> watched_diff (REVIEW, exit 0); setup -> result["setup"] (exit 2)."""
    root = tmp_path / "tree"
    good = _package(tmp_path / "good")
    dest = oc.baseline_dir(root, "0.7.21") / "watched"
    dest.mkdir(parents=True)
    for key, data in oc.watched_bytes(good).items():
        (dest / oc.watched_file_name(key)).write_bytes(data or b"")
    out, diffs = tmp_path / "w.json", tmp_path / "diffs"
    gone = _scope(tmp_path / "gone", b"const missionManifest = (n) => `m`;\n")
    assert oc.run_watched(root, gone, ["0.7.21"], out, diffs) == oc.EXIT_OK
    result = json.loads(out.read_text())
    assert result["setup"] == [] and result["watched_diff"]["SLICE_MANIFEST"] == {
        "0.7.21": "missing"
    }
    assert oc.decide([], result["watched_diff"], seed=False)[0] != "PASS"
    bad = _scope(tmp_path / "bad", b"const SLICE_MANIFEST = `never closed")
    assert oc.run_watched(root, bad, ["0.7.21"], out, diffs) == oc.EXIT_SETUP
    result = json.loads(out.read_text())
    assert any(s.startswith("watched: SLICE_MANIFEST:") for s in result["setup"])
    assert "SLICE_MANIFEST" not in result["watched_diff"]


def _candidate(tmp_path: Path, package: Path, *, seed: bool) -> tuple[list[str], dict]:
    state = tmp_path / "state"
    (state / "recorded").mkdir(parents=True, exist_ok=True)
    (state / "help").mkdir(exist_ok=True)
    dest = tmp_path / "cand"
    problems = oc.write_baseline_candidate(state, package, dest, {"version": "x"}, (), seed=seed)
    return problems, json.loads((dest / "meta.json").read_text())


def test_baseline_candidate_missing_is_recorded_unless_seed(tmp_path: Path) -> None:
    """A missing item is listed in meta.watched_missing; only a seed makes it a problem."""
    gone = _scope(tmp_path / "gone", b"const missionManifest = (n) => `m`;\n")
    problems, meta = _candidate(tmp_path / "a", gone, seed=False)
    assert not [p for p in problems if p.startswith("watched:")]
    assert meta["watched_missing"] == ["SLICE_MANIFEST"]
    problems, _ = _candidate(tmp_path / "b", gone, seed=True)
    assert "watched: SLICE_MANIFEST missing" in problems
    problems, meta = _candidate(tmp_path / "c", _package(tmp_path / "ok"), seed=False)
    assert not [p for p in problems if p.startswith("watched:")] and meta["watched_missing"] == []
    bad = _scope(tmp_path / "bad", b"const SLICE_MANIFEST = `never closed")
    problems, _ = _candidate(tmp_path / "d", bad, seed=False)
    assert any(p.startswith("watched: SLICE_MANIFEST:") for p in problems)


def test_report_names_the_missing_items(tmp_path: Path) -> None:
    """REPORT.md says which watched items are missing, before the `differ` line, and is REVIEW."""
    state = tmp_path / "state"
    _green_state(state)
    watched = {"watched_diff": {"SLICE_MANIFEST": {"0.7.16": "missing"}}, "setup": []}
    oc.write_json(state / "watched.json", watched)
    assert oc.report(_ns(tmp_path)) == oc.VERDICT_EXIT["REVIEW"]
    text = (tmp_path / "evidence" / "REPORT.md").read_text()
    missing = "Watched items missing from the candidate (REVIEW): SLICE_MANIFEST."
    assert missing in text and text.index(missing) < text.index("Watched items that differ")
