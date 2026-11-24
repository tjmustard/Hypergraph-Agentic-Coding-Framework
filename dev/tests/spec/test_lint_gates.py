"""hyper_spec.py lint --min-confidence N and --mission-close (v064-process OPR.99.0.9.4).

Fixture missions are built in a temp git repository with hyper_scope.py (`project init`,
`mission create`, `slice create`), and slices are moved to `audited` with `hyper_spec.py progress`.
The default-lint test runs on the real repository and only reads it.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import hyper_spec
import pytest
from conftest import REPO_ROOT, SCRIPTS_DIR, GitRepo

SCOPE = SCRIPTS_DIR / "hyper_scope.py"
SPEC = SCRIPTS_DIR / "hyper_spec.py"
MISSION = "missions/release-9.2.0"

SLICE_BODY = """\
# MiniPRD: {title}

## Intent

{title} does one thing.

## 1. The Confidence Mandate

{score}

## Mini-requirements

1. US-001: {title} works.

## 3. Implementation Plan (Task List)

- [ ] Task 1: write it.

## 4. The Negative Space (Constraints)

- **DO NOT** touch other modules.

## Proof contract

- [ ] test_{title} passes.

## Source material

- (none)

## Intent visual

N/A

## Status

- compiled

## Dependencies

- (none)
"""


@pytest.fixture(autouse=True)
def _no_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an outer git's GIT_* variables and an engine's HACF_OUTBOX away from the tests."""
    for key in list(os.environ):
        if key.startswith("GIT_") or key == "HACF_OUTBOX":
            monkeypatch.delenv(key)


def run(repo: GitRepo, script: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a framework script in the repository under this interpreter."""
    env = {key: value for key, value in repo.env.items() if key != "HACF_OUTBOX"}
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=repo.path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def ok(repo: GitRepo, script: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a script and assert it exits 0."""
    proc = run(repo, script, *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def lint(repo: GitRepo, *args: str) -> subprocess.CompletedProcess:
    """Run `hyper_spec.py lint --mission <fixture mission>` plus args."""
    return run(repo, SPEC, "lint", "--mission", MISSION, *args)


@pytest.fixture
def mission(tmp_git_repo: GitRepo) -> GitRepo:
    """A repository holding the empty mission release-9.2.0."""
    ok(tmp_git_repo, SCOPE, "project", "init")
    ok(tmp_git_repo, SCOPE, "mission", "create", "--name", "release-9.2.0", "--intent", "Ship.")
    return tmp_git_repo


def add_slice(repo: GitRepo, name: str, score: str) -> Path:
    """Create a slice whose Confidence Mandate reads `score`; return its dir."""
    title = name.split("-", 1)[1]
    body = repo.scratch / f"{name}.md"
    body.write_text(SLICE_BODY.format(title=title, score=score), encoding="utf-8")
    ok(
        repo,
        SCOPE,
        "slice",
        "create",
        "--mission",
        MISSION,
        "--name",
        name,
        "--intent",
        f"The {title} slice.",
        "--body-file",
        str(body),
    )
    return repo.path / MISSION / "slices" / name


def audit(repo: GitRepo, slice_dir: Path) -> None:
    """Move a compiled slice to audited through the progress events, with a C1 proof."""
    for event in ("executing", "executed"):
        ok(repo, SPEC, "progress", "--slice", str(slice_dir), "--event", event)
    body = repo.scratch / f"{slice_dir.name}-proof.md"
    body.write_text("# QA\n\nFixture proof.\n", encoding="utf-8")
    ok(
        repo,
        SPEC,
        "proof",
        "--slice",
        str(slice_dir),
        "--artifact-type",
        "qa",
        "--verdict",
        "PASS",
        "--candidate-sha",
        "legacy-unrecorded",
        "--money-evidence",
        "fixture",
        "--file",
        str(body),
        "--name",
        "01-qa.md",
    )
    ok(repo, SPEC, "progress", "--slice", str(slice_dir), "--event", "audited")


def set_lore_capture(repo: GitRepo, section: str | None) -> None:
    """Replace the mission NOTES.md's `## Lore capture` section (None: remove it)."""
    notes = repo.path / MISSION / "NOTES.md"
    text = notes.read_text(encoding="utf-8")
    head = text.split("\n## Lore capture\n", 1)[0].rstrip("\n") + "\n"
    notes.write_text(head + (f"\n## Lore capture\n\n{section}" if section else ""), "utf-8")


# --- --min-confidence -------------------------------------------------------------------------


def test_score_8_with_period(mission: GitRepo) -> None:
    """`**Score: 8/10.**` on a compiled slice: an error at 9, none at 8."""
    add_slice(mission, "01-low", "**Score: 8/10.** Read by resolve.")
    at9 = lint(mission, "--min-confidence", "9")
    assert at9.returncode == 1
    assert "confidence: 8/10 < 9 (OPR.9.2.0.1)" in at9.stdout
    at8 = lint(mission, "--min-confidence", "8")
    assert at8.returncode == 0, at8.stdout
    assert "confidence" not in at8.stdout


def test_score_9_without_period(mission: GitRepo) -> None:
    """`**Score: 9/10**` passes at 9."""
    add_slice(mission, "01-ok", "**Score: 9/10**")
    proc = lint(mission, "--min-confidence", "9")
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize("score", ["Confidence 9/10.", "Score: 9/10", "TBD"])
def test_no_score_is_an_error(mission: GitRepo, score: str) -> None:
    """No `**Score: N/10**` in the Confidence Mandate: an error."""
    add_slice(mission, "01-none", score)
    proc = lint(mission, "--min-confidence", "1")
    assert proc.returncode == 1
    assert "confidence: no score recorded (OPR.9.2.0.1)" in proc.stdout


def test_score_outside_the_mandate_does_not_count(mission: GitRepo) -> None:
    """Only the Confidence Mandate section is read."""
    slice_dir = add_slice(mission, "01-elsewhere", "No score here.")
    spec = slice_dir / "SPEC.md"
    spec.write_text(spec.read_text() + "\n**Score: 10/10.**\n", encoding="utf-8")
    assert "no score recorded" in lint(mission, "--min-confidence", "9").stdout


def test_audited_slice_is_not_gated(mission: GitRepo) -> None:
    """An audited slice with 7/10: no error."""
    audit(mission, add_slice(mission, "01-old", "**Score: 7/10.**"))
    proc = lint(mission, "--min-confidence", "9")
    assert proc.returncode == 0, proc.stdout


def test_all_reads_every_mission(mission: GitRepo) -> None:
    """--all applies the gate to every mission's compiled slices."""
    add_slice(mission, "01-low", "**Score: 6/10.**")
    proc = run(mission, SPEC, "lint", "--all", "--min-confidence", "9")
    assert proc.returncode == 1 and "confidence: 6/10 < 9" in proc.stdout


@pytest.mark.parametrize("level", ["11", "0", "nine", "9.5"])
def test_bad_level_exits_2(mission: GitRepo, level: str) -> None:
    """A level that is not an int from 1 to 10: exit 2."""
    proc = lint(mission, "--min-confidence", level)
    assert proc.returncode == 2
    assert "--min-confidence" in proc.stderr


def test_gates_need_their_scope(mission: GitRepo) -> None:
    """--min-confidence needs --mission or --all; --mission-close needs --mission."""
    slice_dir = add_slice(mission, "01-ok", "**Score: 9/10**")
    assert run(mission, SPEC, "lint", str(slice_dir), "--min-confidence", "9").returncode == 2
    assert run(mission, SPEC, "lint", "--all", "--mission-close").returncode == 2


# --- --mission-close --------------------------------------------------------------------------


@pytest.fixture
def closed(mission: GitRepo) -> GitRepo:
    """The mission with its one slice audited."""
    audit(mission, add_slice(mission, "01-done", "**Score: 9/10.**"))
    return mission


def test_template_carries_the_section(mission: GitRepo) -> None:
    """A new mission's NOTES.md ends with the Lore capture section of the template."""
    text = (mission.path / MISSION / "NOTES.md").read_text(encoding="utf-8")
    assert text.endswith(
        "## Lore capture\n\n"
        "- replans: <filled by the lead at mission close>\n"
        "- stories: <STORY ids proposed by the scribe, or none>\n"
    )


def test_unfilled_template_fails_at_close(closed: GitRepo) -> None:
    """The template's placeholders are not a filled capture."""
    proc = lint(closed, "--mission-close")
    assert proc.returncode == 1
    assert "lore capture: no '- replans: <int>' line" in proc.stdout


def test_missing_section_is_an_error(closed: GitRepo) -> None:
    """A finished mission without the section: error."""
    set_lore_capture(closed, None)
    proc = lint(closed, "--mission-close")
    assert proc.returncode == 1
    assert "lore capture: no '## Lore capture' section" in proc.stdout


def test_fewer_stories_than_replans(closed: GitRepo) -> None:
    """Two replans, one story: error."""
    set_lore_capture(closed, "- replans: 2\n- stories: STORY-0001\n")
    proc = lint(closed, "--mission-close")
    assert proc.returncode == 1
    assert "lore capture: stories 1 < replans 2" in proc.stdout


@pytest.mark.parametrize(
    "section",
    [
        "- replans: 0\n- stories: none\n",
        "- replans: 2\n- stories: STORY-0001, STORY-0002\n",
        "- replans: 1\n- stories: STORY-0003,STORY-0004\n",
    ],
    ids=["none", "equal", "more"],
)
def test_enough_stories_pass(closed: GitRepo, section: str) -> None:
    """At least as many stories as replans: no finding."""
    set_lore_capture(closed, section)
    proc = lint(closed, "--mission-close")
    assert proc.returncode == 0, proc.stdout
    assert "lore_capture" not in proc.stdout


def test_bad_stories_line_is_an_error(closed: GitRepo) -> None:
    """A malformed STORY id is not a stories line."""
    set_lore_capture(closed, "- replans: 0\n- stories: STORY-1\n")
    proc = lint(closed, "--mission-close")
    assert proc.returncode == 1
    assert "no '- stories: <STORY ids or none>' line" in proc.stdout


def test_open_mission_is_only_a_note(mission: GitRepo) -> None:
    """One slice still compiled: the note `mission not closed`, no error."""
    audit(mission, add_slice(mission, "01-done", "**Score: 9/10.**"))
    add_slice(mission, "02-open", "**Score: 9/10.**")
    set_lore_capture(mission, None)
    proc = lint(mission, "--mission-close")
    assert proc.returncode == 0, proc.stdout
    assert f"note: {MISSION}/NOTES.md: lore_capture: mission not closed" in proc.stdout
    assert "error" not in proc.stdout


# --- MR 3: default lint is unchanged ----------------------------------------------------------


def test_default_lint_never_runs_the_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default `lint --all` on the real repository never calls the gates.

    Its output and exit match a separate run of the script. The base-vs-new byte comparison is in
    the slice's proof.
    """

    def boom(*args: object) -> list:
        raise AssertionError("a lint gate ran without its flag")

    monkeypatch.setattr(hyper_spec, "confidence_findings", boom)
    monkeypatch.setattr(hyper_spec, "mission_close_findings", boom)
    monkeypatch.chdir(REPO_ROOT)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = hyper_spec.main(["lint", "--all"])
    proc = subprocess.run(
        [sys.executable, str(SPEC), "lint", "--all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (code, out.getvalue()) == (proc.returncode, proc.stdout)
    assert "confidence:" not in proc.stdout and "lore_capture" not in proc.stdout
