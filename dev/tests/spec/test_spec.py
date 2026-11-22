"""Deterministic tests for hyper_spec.py, the spec model (S03a), and hyper_openrig's mission status.

Every test works in a temp git repository built from dev/tests/fixtures/specs/ (a v6 mission with
two chained slices and a closed migrated slice, a legacy flat MiniPRD, a migration journal and C1
proofs). Nothing downloads or runs the real rig: PATH holds no `rig` unless a test puts a stub
there, and every file a test writes lives under tmp_path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import hyper_openrig
import hyper_spec
import pytest
from conftest import FIXTURES_DIR, SCRIPTS_DIR, GitRepo

SPECS = FIXTURES_DIR / "specs"
SCRIPT = SCRIPTS_DIR / "hyper_spec.py"
MISSION = "missions/release-9.1.0"
ALPHA = f"{MISSION}/slices/01-alpha"
BETA = f"{MISSION}/slices/02-beta"
GAMMA = f"{MISSION}/closed/03-gamma"
LEGACY = "spec/compiled/MiniPRD_07_Example.md"
GAMMA_LEGACY = "spec/compiled/MiniPRD_03_Gamma.md"
JOURNAL = ".agents/state/migrations/v0.6.0.json"
TS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00"


def _no_rig_path() -> str:
    """Return PATH without any directory that holds a `rig` executable."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(d for d in dirs if d and not (Path(d) / "rig").exists())


@pytest.fixture(autouse=True)
def _no_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an outer git's GIT_* variables and an engine's HACF_OUTBOX away from the tests."""
    for key in list(os.environ):
        if key.startswith("GIT_") or key == "HACF_OUTBOX":
            monkeypatch.delenv(key)


@pytest.fixture
def spec_repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return tmp_git_repo holding the v6 fixture tree and the legacy file; cwd is the repo."""
    shutil.copytree(SPECS / "v6", tmp_git_repo.path, dirs_exist_ok=True)
    shutil.copyfile(SPECS / "legacy" / "MiniPRD_07_Example.md", _mkparent(tmp_git_repo, LEGACY))
    tmp_git_repo.commit("fixture specs")
    monkeypatch.chdir(tmp_git_repo.path)
    monkeypatch.setenv("PATH", _no_rig_path())
    return tmp_git_repo


def _mkparent(repo: GitRepo, rel: str) -> Path:
    target = repo.path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def run(repo: GitRepo, *args: str, path: str | None = None, **env: str):
    """Run hyper_spec.py in the repository under the interpreter running pytest."""
    full = {key: value for key, value in repo.env.items() if key != "HACF_OUTBOX"}
    full["PATH"] = path or _no_rig_path()
    full.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def resolve(repo: GitRepo, target: str) -> dict:
    """Return `resolve <target> --json` as a dict (it must exit 0)."""
    proc = run(repo, "resolve", target, "--json")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def lint(repo: GitRepo, *args: str) -> tuple[int, list[dict]]:
    """Return the exit code and the findings of `lint ... --json`."""
    proc = run(repo, "lint", *args, "--json")
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode, json.loads(proc.stdout)["findings"]


def codes(findings: list[dict], path: str, severity: str | None = None) -> list[str]:
    """Return the finding codes for one file (of one severity when given)."""
    return [
        f["code"]
        for f in findings
        if f["path"] == path and (severity is None or f["severity"] == severity)
    ]


def snapshot(root: Path) -> dict[str, bytes]:
    """Return every work-tree file under root (outside .git) with its bytes."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def body_file(tmp_path: Path) -> Path:
    """Write and return a proof artifact body outside the repository."""
    path = tmp_path / "proof-body.md"
    path.write_text("The oracle passed.\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# resolve and the frontmatter splice
# ---------------------------------------------------------------------------


def test_resolve_legacy_v6_journal_and_frontmatter_fallback(spec_repo: GitRepo) -> None:
    """Legacy, path and dot-ID targets resolve; a migrated legacy path resolves, journal or not."""
    legacy = resolve(spec_repo, LEGACY)
    assert legacy["layout"] == "legacy" and legacy["path"] == LEGACY
    assert (legacy["node_id"], legacy["parent"]) == ("example_module", "orchestration_layer")
    assert legacy["legacy_order"] == 7 and legacy["title"] == "Example legacy slice"
    for key in ("id", "slice", "mission", "hacf_status", "depends_on", "touches", "oracle"):
        assert legacy[key] is None, key

    beta = resolve(spec_repo, BETA)
    assert beta == resolve(spec_repo, f"{BETA}/SPEC.md") == resolve(spec_repo, "OPR.9.1.0.2")
    assert beta["layout"] == "slice" and beta["path"] == f"{BETA}/SPEC.md"
    assert (beta["id"], beta["slice"], beta["mission"]) == (
        "OPR.9.1.0.2",
        "02-beta",
        "release-9.1.0",
    )
    assert (beta["node_id"], beta["parent"]) == ("beta_module", "orchestration_layer")
    assert (beta["hacf_status"], beta["status"]) == ("compiled", "active")
    assert beta["depends_on"] == ["OPR.9.1.0.1"] and beta["touches"] == ["src/beta.py"]
    assert beta["oracle"] == [["python3", "-m", "pytest", "tests/beta", "-q"]]
    assert beta["warnings"] == [] and beta["errors"] == []

    mission = resolve(spec_repo, "OPR.9.1.0")
    assert (mission["layout"], mission["path"], mission["status"]) == (
        "mission",
        f"{MISSION}/SPEC.md",
        "planned",
    )
    assert resolve(spec_repo, "SPEC.md")["layout"] == "project"

    # slice.yaml execution.depends_on wins over the frontmatter (HACF reads it, never writes it).
    manifest = spec_repo.path / BETA / "slice.yaml"
    manifest.write_text(
        manifest.read_text() + "execution:\n  depends_on: [OPR.9.1.0.3]\n", encoding="utf-8"
    )
    assert resolve(spec_repo, BETA)["depends_on"] == ["OPR.9.1.0.3"]

    # A migrated legacy path: through the journal's legacy_map, then by the frontmatter scan.
    shutil.copyfile(SPECS / "journal" / "v0.6.0.json", _mkparent(spec_repo, JOURNAL))
    via_journal = resolve(spec_repo, GAMMA_LEGACY)
    assert via_journal["path"] == f"{GAMMA}/SPEC.md" and via_journal["layout"] == "slice"
    assert via_journal["legacy_source"] == GAMMA_LEGACY and via_journal["legacy_order"] == 3
    assert via_journal["status"] == "closed-superseded"
    assert resolve(spec_repo, "spec/compiled/MiniPRD_02_Beta.md")["path"] == f"{BETA}/SPEC.md"
    (spec_repo.path / JOURNAL).unlink()
    assert resolve(spec_repo, GAMMA_LEGACY) == via_journal  # closed/ is scanned too
    # Only the journal maps beta, and no slug or name fragment ever matches.
    for target in ("spec/compiled/MiniPRD_02_Beta.md", "MiniPRD_03_Gamma.md", "OPR.9.1.0.9"):
        proc = run(spec_repo, "resolve", target, "--json")
        assert proc.returncode == 2 and proc.stdout == "", target


def test_frontmatter_roundtrip_unknown_keys(spec_repo: GitRepo) -> None:
    """Only hacf_status and the mapped status change; rig and unknown keys keep bytes and order."""
    spec = spec_repo.path / ALPHA / "SPEC.md"
    original = spec.read_text(encoding="utf-8")
    seeded = original.replace(
        "status: active\n",
        "status: placeholder  # rig's scaffold status\nrig-claimed-by: seat-7\n"
        "review_note: {by: rig, note: 'keep: me'}\n",
    )
    spec.write_text(seeded, encoding="utf-8")
    keys = hyper_spec.parse_frontmatter(seeded).keys
    assert keys[3:6] == ["status", "rig-claimed-by", "review_note"]

    proc = run(spec_repo, "progress", "--slice", ALPHA, "--event", "executing")
    assert proc.returncode == 0, proc.stderr
    after = spec.read_text(encoding="utf-8")
    expected = seeded.replace(
        "status: placeholder  # rig's scaffold status", "status: active  # rig's scaffold status"
    ).replace("hacf_status: compiled", "hacf_status: executing")
    assert after == expected
    assert hyper_spec.parse_frontmatter(after).keys == keys

    # The owned and shared key sets are data per file type; anything else is refused.
    for kind, updates in (
        ("slice", {"rig-claimed-by": "seat-8"}),
        ("slice", {"status": "shipped"}),
        ("project", {"hacf_status": "compiled"}),
        ("mission", {"parent": "x"}),
    ):
        with pytest.raises(hyper_spec.UsageError):
            hyper_spec.splice_owned(after, kind, updates)
    assert hyper_spec.may_write("slice", "hacf_anything") and hyper_spec.may_write(
        "slice", "parent"
    )


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def test_lint_reports(spec_repo: GitRepo) -> None:
    """Each defect is reported once with its file; legacy files warn; rig's content rules hold."""
    code, findings = lint(spec_repo, "--all")
    assert code == 0, findings
    assert all(f["severity"] == "warning" for f in findings)
    assert sorted(codes(findings, f"{GAMMA}/SPEC.md")) == [
        "mini_requirements_missing_or_malformed",
        "proof_contract_missing_or_malformed",
    ]
    legacy = codes(findings, LEGACY)
    assert legacy.count("legacy_heading") == 2 and "heading_missing" in legacy

    # One slice with a bad dot-ID, a dangling depends_on, an unsafe touch and a missing section.
    bad = spec_repo.path / MISSION / "slices" / "04-bad"
    bad.mkdir()
    shutil.copyfile(spec_repo.path / ALPHA / "slice.yaml", bad / "slice.yaml")
    text = (spec_repo.path / ALPHA / "SPEC.md").read_text(encoding="utf-8")
    text = text.replace("id: OPR.9.1.0.1", "id: OPR.9")
    text = text.replace("depends_on: []", "depends_on: [OPR.9.1.0.7]")
    text = text.replace("'tests/alpha/**'", "'../outside.py'")
    text = text.replace("## Source material\n\n- (none)\n\n", "")
    (bad / "SPEC.md").write_text(text, encoding="utf-8")
    code, findings = lint(spec_repo, "--mission", MISSION)
    assert code == 1
    rel = f"{MISSION}/slices/04-bad/SPEC.md"
    found = codes(findings, rel)
    for name in ("dot_id_invalid", "depends_on_dangling", "touch_unsafe", "heading_missing"):
        assert found.count(name) == 1, (name, found)
    assert len(found) == 4
    assert [f for f in findings if f["severity"] == "error" and f["path"] != rel] == []
    proc = run(spec_repo, "lint", "--mission", MISSION)
    assert proc.returncode == 1 and proc.stdout.splitlines()[-1] == "lint: failed"
    assert f"error: {rel}: touch_unsafe: hacf_touches entry '../outside.py'" in proc.stdout
    assert str(spec_repo.path) not in proc.stdout  # repo-relative paths only, no line numbers
    shutil.rmtree(bad)

    # A legacy heading is an error in a v6 file and a warning in a legacy flat file.
    spec = spec_repo.path / ALPHA / "SPEC.md"
    original = spec.read_text(encoding="utf-8")
    spec.write_text(
        original.replace("## Mini-requirements", "## 2. Atomic User Stories"), encoding="utf-8"
    )
    code, findings = lint(spec_repo, ALPHA)
    assert code == 1 and "legacy_heading" in codes(findings, f"{ALPHA}/SPEC.md", "error")
    code, findings = lint(spec_repo, LEGACY)
    assert code == 0 and codes(findings, LEGACY, "warning").count("legacy_heading") == 2

    # Out-of-order headings are only a warning.
    swapped = original.replace("## Status\n", "## TMP\n").replace(
        "## Dependencies\n", "## Status\n"
    )
    spec.write_text(swapped.replace("## TMP\n", "## Dependencies\n"), encoding="utf-8")
    code, findings = lint(spec_repo, ALPHA)
    assert code == 0 and codes(findings, f"{ALPHA}/SPEC.md") == ["heading_order"]

    # Rig's content rules: errors in a v6 slice, warnings in a migrated one (R29).
    malformed = original.replace("1. US-001: alpha() returns 1.", "- US-001: alpha() returns 1.")
    malformed = malformed.replace("- [ ] test_alpha passes.", "- test_alpha passes.")
    spec.write_text(malformed, encoding="utf-8")
    rules = ["mini_requirements_missing_or_malformed", "proof_contract_missing_or_malformed"]
    code, findings = lint(spec_repo, ALPHA)
    assert code == 1 and sorted(codes(findings, f"{ALPHA}/SPEC.md", "error")) == rules
    placeholder = original.replace("1. US-001: alpha() returns 1.", "1. [requirement]")
    spec.write_text(placeholder, encoding="utf-8")
    code, findings = lint(spec_repo, ALPHA)
    assert codes(findings, f"{ALPHA}/SPEC.md", "error") == [rules[0]]
    migrated = hyper_spec.splice_owned(malformed, "slice", {"hacf_legacy_source": GAMMA_LEGACY})
    spec.write_text(migrated, encoding="utf-8")
    code, findings = lint(spec_repo, ALPHA)
    assert code == 0 and sorted(codes(findings, f"{ALPHA}/SPEC.md", "warning")) == rules

    # A tree with no missions/ and only legacy flat files: lint --all exits 0.
    shutil.rmtree(spec_repo.path / "missions")
    (spec_repo.path / "SPEC.md").unlink()
    code, findings = lint(spec_repo, "--all")
    assert code == 0 and {f["path"] for f in findings} == {LEGACY}
    assert run(spec_repo, "lint").returncode == 2  # exactly one of <path>, --mission, --all


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_progress_transitions_atomic(spec_repo: GitRepo, tmp_path: Path) -> None:
    """Allowed events update SPEC.md, PROGRESS.md and the mission files together; others none."""
    root = spec_repo.path
    spec, progress = root / ALPHA / "SPEC.md", root / ALPHA / "PROGRESS.md"
    mission_progress, manifest = root / MISSION / "PROGRESS.md", root / MISSION / "mission.yaml"
    planned = manifest.read_text(encoding="utf-8")
    table_head = mission_progress.read_text(encoding="utf-8").split("<!-- BEGIN")[0]

    before = snapshot(root)
    for event in ("executed", "audited", "compiled", "blocked"):  # none is allowed from compiled
        proc = run(spec_repo, "progress", "--slice", ALPHA, "--event", event)
        assert proc.returncode == 4, (event, proc.stderr)
    assert run(spec_repo, "progress", "--slice", ALPHA, "--event", "done").returncode == 2
    assert snapshot(root) == before

    detail = tmp_path / "detail.txt"
    detail.write_text("commit abc1234,\nexecutor claude-cli\n", encoding="utf-8")
    walk = ["executing", "executed", "audit_failed", "executing", "blocked", "executing"]
    walk += ["executed", "audited"]
    outcomes = progress.read_text(encoding="utf-8").split("## Outcomes\n")[1].splitlines()
    for index, event in enumerate(walk):
        extra = ("--detail-file", str(detail)) if index == 1 else ()
        proc = run(spec_repo, "progress", "--slice", ALPHA, "--event", event, *extra)
        assert proc.returncode == 0, (event, proc.stderr)
        front = hyper_openrig.read_frontmatter(spec)
        assert front["hacf_status"] == event
        assert front["status"] == ("done" if event == "audited" else "active")
        text = progress.read_text(encoding="utf-8")
        region = text.split("<!-- BEGIN HACF GENERATED: current-state -->\n")[1].split("<!--")[0]
        assert region.startswith(f"- hacf_status: {event}\n")
        lines = text.split("## Outcomes\n")[1].splitlines()
        assert lines[: len(outcomes)] == outcomes, "an earlier Outcomes line changed"
        suffix = " - commit abc1234, executor claude-cli" if index == 1 else ""
        assert re.fullmatch(rf"- {TS} {event}{re.escape(suffix)}", lines[-1]), lines[-1]
        outcomes = lines
        table = mission_progress.read_text(encoding="utf-8")
        assert table.startswith(table_head)
        row = next(line for line in table.splitlines() if "| 01-alpha |" in line)
        assert re.fullmatch(
            rf"\| OPR\.9\.1\.0\.1 \| 01-alpha \| {event} \|  \| {TS} {event} \|", row
        )
        assert "| OPR.9.1.0.2 | 02-beta | compiled | OPR.9.1.0.1 |" in table
        assert manifest.read_text(encoding="utf-8") == planned.replace(
            "  status: planned\n", "  status: active\n", 1
        )

    # status reads what progress wrote.
    proc = run(spec_repo, "status", "--json")
    assert proc.returncode == 0, proc.stderr
    (mission,) = json.loads(proc.stdout)["missions"]
    assert (mission["id"], mission["status"]) == ("OPR.9.1.0", "active")
    alpha = mission["slices"][0]
    assert (alpha["hacf_status"], alpha["status"], alpha["last_event"]["event"]) == (
        "audited",
        "done",
        "audited",
    )
    assert [s["slice"] for s in mission["slices"]] == ["01-alpha", "02-beta", "03-gamma"]

    # audited needs PROOF.md and a C1 artifact; beta has neither artifact.
    for event in ("executing", "executed"):
        assert run(spec_repo, "progress", "--slice", BETA, "--event", event).returncode == 0
    before = snapshot(root)
    assert run(spec_repo, "progress", "--slice", BETA, "--event", "audited").returncode == 4
    assert snapshot(root) == before

    # A legacy flat target is skipped with a notice; HACF_OUTBOX records one entry instead.
    proc = run(spec_repo, "progress", "--slice", LEGACY, "--event", "executing")
    assert proc.returncode == 0 and proc.stdout.strip() == hyper_spec.LEGACY_PROGRESS_NOTICE
    outbox = tmp_path / "outbox"
    proc = run(
        spec_repo, "progress", "--slice", BETA, "--event", "audit_failed", HACF_OUTBOX=str(outbox)
    )
    assert proc.returncode == 0, proc.stderr
    assert snapshot(root) == before
    (entry,) = sorted(outbox.iterdir())
    assert entry.name == "000001-progress.json"
    assert json.loads(entry.read_text(encoding="utf-8")) == {
        "tool": "hyper_spec",
        "op": "progress",
        "args": {"slice": BETA, "event": "audit_failed", "detail": None},
    }


def test_progress_cas_refuses_concurrent_change(
    spec_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file changed between read and replace gives exit 5 and keeps the other writer's bytes."""
    root = spec_repo.path
    spec, mission_progress = root / ALPHA / "SPEC.md", root / MISSION / "PROGRESS.md"
    real = hyper_openrig.cas_write
    before = snapshot(root)

    def racing(victim: Path, other: str):
        def cas_write(path: Path, original: str | None, text: str) -> None:
            if os.path.realpath(path) == os.path.realpath(victim):
                Path(victim).write_text(other, encoding="utf-8")  # rig writes in between
            real(path, original, text)

        return cas_write

    rig_text = "---\nid: OPR.9.1.0.1\nstatus: done\n---\n\n# written by rig\n"
    monkeypatch.setattr(hyper_openrig, "cas_write", racing(spec, rig_text))
    assert hyper_spec.main(["progress", "--slice", ALPHA, "--event", "executing"]) == 5
    assert spec.read_text(encoding="utf-8") == rig_text
    after = snapshot(root)
    assert {k: v for k, v in after.items() if k != f"{ALPHA}/SPEC.md"} == {
        k: v for k, v in before.items() if k != f"{ALPHA}/SPEC.md"
    }

    # A change to a later file of the same event puts back the files already written.
    spec.write_bytes(before[f"{ALPHA}/SPEC.md"])
    monkeypatch.setattr(hyper_openrig, "cas_write", racing(mission_progress, "rig wrote this\n"))
    assert hyper_spec.main(["progress", "--slice", ALPHA, "--event", "executing"]) == 5
    assert mission_progress.read_text(encoding="utf-8") == "rig wrote this\n"
    after = snapshot(root)
    assert {k: v for k, v in after.items() if k != f"{MISSION}/PROGRESS.md"} == {
        k: v for k, v in before.items() if k != f"{MISSION}/PROGRESS.md"
    }


def test_mission_status_splice_and_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The R29 splice edits only the metadata.status scalar; the writer is compare-and-swap."""
    rendered = hyper_openrig.render_mission_yaml()
    active = hyper_openrig.splice_mission_status(rendered, "active")
    assert active == rendered.replace("  status: planned\n", "  status: active\n", 1)
    assert hyper_openrig.splice_mission_status(active, "active") == active
    crlf = rendered.replace("\n", "\r\n")
    assert hyper_openrig.splice_mission_status(crlf, "complete") == crlf.replace(
        "status: planned", "status: complete", 1
    )
    commented = (
        "# team mission\n"
        "schema: openrig.mission/v0alpha1\n"
        "kind: mission\n"
        "metadata:   # lifecycle\n"
        '  owner: "seat-3"\n'
        "  status: 'planned'    # the rollup reads this\n"
        "  notes: [a, b]\n"
        "composition:\n"
        "  mission_markdown: {spec: SPEC.md}\n"
        "  slices: []\n"
        "status: planned  # not metadata.status\n"
    )
    assert hyper_openrig.splice_mission_status(commented, "complete") == commented.replace(
        "status: 'planned'    #", "status: complete    #"
    )
    flow = "kind: mission\nmetadata: {status: planned, owner: me}\ncomposition: {slices: []}\n"
    assert hyper_openrig.splice_mission_status(flow, "active") == flow.replace(
        "status: planned", "status: active"
    )
    for status in ("done", "Active", ""):
        with pytest.raises(hyper_openrig.UsageError) as caught:
            hyper_openrig.splice_mission_status(rendered, status)
        assert caught.value.code == 2
    for broken in (
        "kind: mission\n",
        "metadata: {owner: me}\n",
        "metadata:\n  status:\n",
        "a: [\n",
    ):
        with pytest.raises(hyper_openrig.ParseError) as caught:
            hyper_openrig.splice_mission_status(broken, "active")
        assert caught.value.code == 4

    mission = tmp_path / "mission"
    mission.mkdir()
    path = mission / "mission.yaml"
    path.write_text(rendered, encoding="utf-8")
    assert hyper_openrig.set_mission_status(mission, "active") == "active"
    assert path.read_text(encoding="utf-8") == active
    inode = os.stat(path).st_ino
    assert hyper_openrig.set_mission_status(mission, "active") == "active"
    assert os.stat(path).st_ino == inode, "the current status rewrites nothing"
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.set_mission_status(mission, "shipped")
    assert path.read_text(encoding="utf-8") == active

    real = hyper_openrig.splice_mission_status

    def racing(text: str, status: str) -> str:
        path.write_text("kind: mission\nmetadata: {status: complete}\n", encoding="utf-8")
        return real(text, status)

    monkeypatch.setattr(hyper_openrig, "splice_mission_status", racing)
    with pytest.raises(hyper_openrig.Refused) as caught:
        hyper_openrig.set_mission_status(mission, "complete")
    assert caught.value.code == 5
    assert path.read_text(encoding="utf-8") == "kind: mission\nmetadata: {status: complete}\n"


# ---------------------------------------------------------------------------
# proof
# ---------------------------------------------------------------------------

STUB_RIG = """#!{python}
import json
import sys
from pathlib import Path

args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print("0.6.13 (9eeb2e42)")
    sys.exit(0)
start = args.index("add")
opts = {{args[i]: args[i + 1] for i in range(start + 2, len(args) - 1) if args[i].startswith("--")}}
target = Path(args[start + 1]) / "proof" / opts["--name"]
target.parent.mkdir(exist_ok=True)
header = [("slice", "OPR.9.1.0.2"), ("candidate_sha", opts["--candidate-sha"])]
header += [("artifact_type", opts["--artifact-type"]), ("verdict", opts["--verdict"])]
header += [("money_evidence", opts["--money-evidence"])]
lines = [key + ": " + json.dumps(value) for key, value in header]
body = Path(opts["--file"]).read_text(encoding="utf-8")
target.write_text("---\\n" + "\\n".join(lines) + "\\n---\\n\\n" + body, encoding="utf-8")
"""


def test_proof_c1_closed_sets(spec_repo: GitRepo, tmp_path: Path) -> None:
    """Every allowed artifact_type and verdict writes a valid C1 file; other values exit 2."""
    root = spec_repo.path
    head = spec_repo.git("rev-parse", "HEAD").strip()
    body = body_file(tmp_path)
    proof_md = root / BETA / "PROOF.md"
    original_lines = proof_md.read_text(encoding="utf-8").splitlines()
    base = ["proof", "--slice", BETA, "--candidate-sha", head, "--file", str(body)]
    cases = [(t, "PASS") for t in hyper_openrig.C1_ARTIFACT_TYPES]
    cases += [("qa", v) for v in hyper_openrig.C1_VERDICTS]
    for index, (artifact_type, verdict) in enumerate(cases, 1):
        name = f"{index:02d}-{artifact_type}.md"
        args = ["--artifact-type", artifact_type, "--verdict", verdict, "--name", name]
        proc = run(spec_repo, *base, *args, "--money-evidence", "oracle passed")
        assert proc.returncode == 0, proc.stderr
        text = (root / BETA / "proof" / name).read_text(encoding="utf-8")
        assert hyper_openrig.c1_problems(text) == []
        header = hyper_openrig.split_frontmatter(text)[2]
        assert header == {
            "slice": "OPR.9.1.0.2",
            "candidate_sha": head,
            "artifact_type": artifact_type,
            "verdict": verdict,
            "money_evidence": "oracle passed",
        }
        assert text.endswith("\n\nThe oracle passed.\n")
        entry = f"- proof/{name} - {artifact_type} {verdict}: oracle passed"
        lines = proof_md.read_text(encoding="utf-8").splitlines()
        assert entry in lines and [x for x in lines if x not in original_lines][-1] == entry
        assert [x for x in original_lines if x in lines] == original_lines
    section = proof_md.read_text(encoding="utf-8").split("## Artifacts (media in proof/)\n")[1]
    assert section.split("## Residue")[0].count("- proof/") == len(cases) + 1

    before = snapshot(root)
    ok = ["--artifact-type", "qa", "--verdict", "PASS", "--money-evidence", "fine"]
    for bad in (
        [
            "--artifact-type",
            "review",
            "--verdict",
            "PASS",
            "--money-evidence",
            "x",
            "--name",
            "90-qa.md",
        ],
        [
            "--artifact-type",
            "qa",
            "--verdict",
            "MAYBE",
            "--money-evidence",
            "x",
            "--name",
            "90-qa.md",
        ],
        [*ok, "--name", "qa.md"],
        [*ok, "--name", "90-qa.md", "--swimlane-sha", "not-a-sha"],
        [
            "--artifact-type",
            "qa",
            "--verdict",
            "PASS",
            "--money-evidence",
            "-x",
            "--name",
            "90-qa.md",
        ],
    ):
        proc = run(spec_repo, *base, *bad)
        assert proc.returncode == 2, (bad, proc.stderr)
    bad_sha = ["proof", "--slice", BETA, "--candidate-sha", "HEAD~1", "--file", str(body)]
    assert run(spec_repo, *bad_sha, *ok, "--name", "90-qa.md").returncode == 2
    assert run(spec_repo, *base, *ok, "--name", "01-guard.md").returncode == 4  # exists already
    assert snapshot(root) == before
    proc = run(spec_repo, "proof", "--slice", LEGACY, *base[3:], *ok, "--name", "90-qa.md")
    assert proc.returncode == 0 and proc.stdout.strip() == hyper_spec.LEGACY_PROOF_NOTICE
    assert snapshot(root) == before

    # A slice without PROOF.md gets one; the pinned rig 0.6.13 is delegated to, with --name.
    proof_md.unlink()
    bin_dir, log = tmp_path / "bin", tmp_path / "rig.log"
    bin_dir.mkdir()
    stub = bin_dir / "rig"
    stub.write_text(STUB_RIG.format(python=sys.executable, log=str(log)), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    proc = run(
        spec_repo,
        *base,
        *ok,
        "--name",
        "20-qa.md",
        "--swimlane-sha",
        head,
        path=f"{bin_dir}{os.pathsep}{_no_rig_path()}",
    )
    assert proc.returncode == 0, proc.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls[0] == ["--version"]
    assert calls[1][:4] == ["proof", "--workspace", str(root), "add"]
    assert calls[1][calls[1].index("--name") + 1] == "20-qa.md"
    header = hyper_openrig.split_frontmatter((root / BETA / "proof" / "20-qa.md").read_text())[2]
    assert header["swimlane_sha"] == head and header["verdict"] == "PASS"
    assert proof_md.read_text(encoding="utf-8").startswith("# PROOF - OPR.9.1.0.2 Beta module\n")
    assert "- proof/20-qa.md - qa PASS: fine" in proof_md.read_text(encoding="utf-8")
    code, findings = lint(spec_repo, BETA)
    assert code == 0 and findings == []


def test_proof_swimlane_sha_historical(spec_repo: GitRepo) -> None:
    """An unreachable candidate_sha with swimlane_sha is a warning; without it, an error."""
    target = spec_repo.path / ALPHA / "proof" / "02-qa.md"
    rel = f"{ALPHA}/proof/02-qa.md"
    shutil.copyfile(SPECS / "proofs" / "historical.md", target)
    code, findings = lint(spec_repo, ALPHA)
    assert code == 0 and codes(findings, rel) == ["proof_candidate_historical"]
    record = resolve(spec_repo, ALPHA)
    assert record["errors"] == [] and "proof_candidate_historical" in record["warnings"][0]
    status = json.loads(run(spec_repo, "status", "--json").stdout)
    assert "proof_candidate_historical" in status["missions"][0]["slices"][0]["warnings"][0]

    shutil.copyfile(SPECS / "proofs" / "unreachable.md", target)
    code, findings = lint(spec_repo, ALPHA)
    assert code == 1 and codes(findings, rel, "error") == ["proof_candidate_unreachable"]
    record = resolve(spec_repo, ALPHA)
    assert "proof_candidate_unreachable" in record["errors"][0] and record["warnings"] == []

    head = spec_repo.git("rev-parse", "HEAD").strip()
    target.write_text(
        (SPECS / "proofs" / "unreachable.md").read_text().replace("deadbeef" * 5, head),
        encoding="utf-8",
    )
    assert lint(spec_repo, ALPHA) == (0, [])


# ---------------------------------------------------------------------------
# --candidate-sha auto
# ---------------------------------------------------------------------------


def _proof_auto(repo: GitRepo, tmp_path: Path, name: str, **env: str):
    args = ["--artifact-type", "qa", "--verdict", "PASS", "--money-evidence", "tests pass"]
    return run(
        repo,
        "proof",
        "--slice",
        BETA,
        "--candidate-sha",
        "auto",
        "--file",
        str(body_file(tmp_path)),
        "--name",
        name,
        *args,
        **env,
    )


def _candidate(repo: GitRepo, name: str) -> str:
    text = (repo.path / BETA / "proof" / name).read_text(encoding="utf-8")
    return hyper_openrig.split_frontmatter(text)[2]["candidate_sha"]


def _status_outside_slice(repo: GitRepo) -> list[str]:
    status = repo.git("status", "--porcelain", "--untracked-files=all").splitlines()
    return [line for line in status if f"{BETA}/" not in line]


def test_candidate_auto_does_not_touch_index(spec_repo: GitRepo, tmp_path: Path) -> None:
    """Clean slice files give HEAD; dirty ones a snapshot ref; the user's index never changes."""
    spec_repo.write("src/beta.py", "VALUE = 1\n")
    head = spec_repo.commit("beta v1")
    spec_repo.write("README.md", "# changed outside the slice\n")
    proc = _proof_auto(spec_repo, tmp_path, "01-qa.md")
    assert proc.returncode == 0, proc.stderr
    assert _candidate(spec_repo, "01-qa.md") == head
    assert spec_repo.git("for-each-ref", "refs/hacf") == ""

    spec_repo.write("src/beta.py", "VALUE = 2\n")
    spec_repo.write("staged.txt", "staged by the user\n")
    spec_repo.git("add", "staged.txt")
    status = _status_outside_slice(spec_repo)  # a plain git status may refresh the index first
    index = (spec_repo.path / ".git" / "index").read_bytes()
    proc = _proof_auto(spec_repo, tmp_path, "02-qa.md")
    assert proc.returncode == 0, proc.stderr
    assert (spec_repo.path / ".git" / "index").read_bytes() == index
    assert _status_outside_slice(spec_repo) == status
    ref = spec_repo.git("rev-parse", "refs/hacf/candidates/OPR.9.1.0.2").strip()
    assert _candidate(spec_repo, "02-qa.md") == ref != head
    assert spec_repo.git("rev-parse", f"{ref}^").strip() == head
    assert spec_repo.git("show", f"{ref}:src/beta.py") == "VALUE = 2\n"
    assert spec_repo.git("show", f"{ref}:staged.txt") == "staged by the user\n"
    assert (spec_repo.path / "src" / "beta.py").read_text() == "VALUE = 2\n"


def test_candidate_auto_keeps_force_tracked_ignored(spec_repo: GitRepo, tmp_path: Path) -> None:
    """A force-tracked file under an ignored directory stays in the candidate tree (R22.2)."""
    spec_repo.write(".gitignore", "spec/\n")
    spec_repo.write("spec/ledger.yml", "schema: hacf.ledger/v2\nnodes: []\n")
    spec_repo.git("add", "-f", "spec/ledger.yml")
    spec_repo.commit("force-track the ledger", ".gitignore")
    spec_repo.write("spec/ledger.yml", "schema: hacf.ledger/v2\nnodes: [changed]\n")
    spec_repo.write("spec/untracked.yml", "ignored and untracked\n")
    spec_repo.write("src/beta.py", "VALUE = 3\n")
    proc = _proof_auto(spec_repo, tmp_path, "01-qa.md")
    assert proc.returncode == 0, proc.stderr
    ref = "refs/hacf/candidates/OPR.9.1.0.2"
    tree = spec_repo.git("ls-tree", "-r", "--name-only", ref).splitlines()
    assert "spec/ledger.yml" in tree and LEGACY in tree and "src/beta.py" in tree
    assert "spec/untracked.yml" not in tree
    assert spec_repo.git("show", f"{ref}:spec/ledger.yml").endswith("nodes: [changed]\n")


def test_candidate_auto_refused_under_outbox(spec_repo: GitRepo, tmp_path: Path) -> None:
    """With HACF_OUTBOX set, --candidate-sha auto exits 4: no ref, no file, no outbox entry (R1)."""
    spec_repo.write("src/beta.py", "VALUE = 4\n")
    before = snapshot(spec_repo.path)
    outbox = tmp_path / "outbox"
    proc = _proof_auto(spec_repo, tmp_path, "01-qa.md", HACF_OUTBOX=str(outbox))
    assert proc.returncode == 4
    assert "the engine writes PROOF and candidate refs" in proc.stderr
    assert spec_repo.git("for-each-ref", "refs/hacf") == ""
    assert snapshot(spec_repo.path) == before
    assert not outbox.exists()
