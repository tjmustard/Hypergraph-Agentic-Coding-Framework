"""Live contract test: HACF's repo-root workspace against the real rig 0.6.13 (S02 CLI spike).

Opt-in only: markers `live` and `openrig`, and it runs only when HACF_LIVE_OPENRIG=1, which the
user sets after consenting to download and run `@openrig/cli@0.6.13` (or with `rig` already on
PATH):

    HACF_LIVE_OPENRIG=1 uv run --locked pytest -m 'live and openrig' \
        dev/tests/contract/test_openrig_live.py

The harness, never the model, writes one verbatim capture per external call to
dev/tests/fixtures/recorded/S02/<NN>-<step>.json (argv, stdout, stderr, exit code, `rig --version`,
UTC date, and `git status --porcelain` after the call), computes spike_verdict_cli from the
checks (`pass` only when every check passed) and writes verdict.json there (R27). rig runs
against throwaway git repositories under tmp_path with OPENRIG_HOME in tmp_path; it writes
nowhere else except npx's cache.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import _runtime
import hyper_openrig
import pytest
from conftest import FIXTURES_DIR, GitRepo

RECORDED = FIXTURES_DIR / "recorded" / "S02"
MISSION = "release-0.6.0"
FIRST_CALL_TIMEOUT = 900  # the first npx call downloads and installs the package

pytestmark = [
    pytest.mark.live,
    pytest.mark.openrig,
    pytest.mark.skipif(
        os.environ.get("HACF_LIVE_OPENRIG") != "1",
        reason="live OpenRig spike: set HACF_LIVE_OPENRIG=1 after the user consents (Q2)",
    ),
]


class Spike:
    """Runs external calls, writes their captures and collects the check results."""

    def __init__(self, prefix: list[str], env: dict[str, str]) -> None:
        """Start an empty capture set; the caller cleared RECORDED first."""
        self.prefix = prefix
        self.env = env
        self.seq = 0
        self.rig_version: str | None = None
        self.captures: list[str] = []
        self.checks: dict[str, bool] = {}
        self.write_sets: dict[str, object] = {}

    def call(
        self,
        step: str,
        argv: list[str],
        *,
        cwd: Path,
        repo: GitRepo | None = None,
        env: dict[str, str] | None = None,
        timeout: float = hyper_openrig.RIG_TIMEOUT,
    ) -> hyper_openrig.Completed:
        """Run argv, write its capture (with the repo's git status after it) and return it."""
        proc = hyper_openrig.run_rig(argv, cwd=cwd, env=env or self.env, timeout=timeout)
        if step == "rig-version" and proc.returncode == 0:
            self.rig_version = proc.stdout.strip()
        self.seq += 1
        name = f"{self.seq:02d}-{step}.json"
        record: dict[str, object] = {
            "step": step,
            "argv": argv,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "rig_version": self.rig_version,
            "captured_at": _runtime.utc_now_iso(),
        }
        if repo is not None:
            status = _porcelain(repo)
            record["git_status_after"] = status
            self.write_sets[step] = sorted(_paths(status))
        (RECORDED / name).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.captures.append(name)
        return proc

    def check(self, name: str, ok: object) -> bool:
        """Record one named check; the verdict is pass only when every check is True."""
        self.checks[name] = bool(ok)
        return bool(ok)


def _porcelain(repo: GitRepo) -> list[str]:
    return repo.git("status", "--porcelain=v1", "--untracked-files=all").splitlines()


def _paths(status: list[str]) -> set[str]:
    """Return every path a porcelain v1 listing names (both sides of a rename)."""
    paths: set[str] = set()
    for line in status:
        rest = line[3:]
        paths.update(part.strip('"') for part in rest.split(" -> "))
    return paths


def _json(proc: hyper_openrig.Completed) -> dict:
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _high_findings(audit: dict) -> int:
    findings = list(audit.get("mission", {}).get("findings", []))
    for item in audit.get("slices", []):
        findings += item.get("findings", [])
    return sum(1 for f in findings if f.get("severity") == "high")


def _finding_kinds(audit: dict) -> set[str]:
    kinds = {f.get("kind") for f in audit.get("mission", {}).get("findings", [])}
    for item in audit.get("slices", []):
        kinds |= {f.get("kind") for f in item.get("findings", [])}
    return kinds


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _scaffold(root: Path, mission: str, mission_id: str, today: str) -> Path:
    """Write the HACF-rendered workspace and one mission at root; return the mission dir."""
    _write(root / "project.yaml", hyper_openrig.render_project_yaml())
    _write(root / "workspace.yaml", hyper_openrig.render_workspace_yaml())
    _write(
        root / "SPEC.md",
        "---\nintent: 'Throwaway project for the HACF OpenRig spike'\nhacf_schema: 1\n"
        f"hacf_type: SystemOfRecord\nhacf_updated: '{today}'\n---\n\n# Project - spike\n",
    )
    mission_dir = root / "missions" / mission
    _write(
        mission_dir / "SPEC.md",
        f"---\nid: {mission_id}\nmission: {mission}\nstage: wip\ncreated: '{today}'\n"
        "intent: 'Verify the repo-root workspace against rig 0.6.13'\ndepends_on: []\n"
        "hacf_schema: 1\nhacf_type: SuperPRD\nhacf_status: compiled\nlore_refs: [ADR-0001]\n"
        "---\n\n# SuperPRD: OpenRig spike\n\n## Intent\n\nVerify the repo-root workspace.\n",
    )
    _write(mission_dir / "mission.yaml", hyper_openrig.render_mission_yaml())
    _write(
        mission_dir / "PROGRESS.md",
        f"# Progress - {mission}\n\n## Acceptance\n\n- [ ] Scope complete\n"
        "- [ ] Implementation in progress\n- [ ] QA / review pass\n- [ ] Merge / ship\n",
    )
    _write(
        mission_dir / "NOTES.md",
        f"---\nmission: {mission}\nname: {mission}\ncreated: '{today}'\n---\n\n"
        f"# Notes - {mission}\n\n## Notes\n",
    )
    return mission_dir


def _new_repo(path: Path, scratch: Path) -> GitRepo:
    """Return a git repository at path with the conftest isolation (no hooks, no user config)."""
    (scratch / "no-hooks").mkdir(parents=True, exist_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    repo = GitRepo(path, scratch)
    repo.git("init", "-q", "-b", "main")
    for key, value in (
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(scratch / "no-hooks")),
        ("core.excludesFile", os.devnull),
        ("core.attributesFile", os.devnull),
    ):
        repo.git("config", key, value)
    return repo


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _slice_files(rel: str) -> set[str]:
    return {f"{rel}/{name}" for name in ("SPEC.md", "slice.yaml", "PROGRESS.md", "PROOF.md")}


def _rig_env(base: dict[str, str], tmp_path: Path, **extra: str) -> dict[str, str]:
    """Return base without the user's OpenRig settings, with a throwaway OPENRIG_HOME."""
    env = {k: v for k, v in base.items() if not k.startswith(("OPENRIG_", "RIGGED_"))}
    env.update(
        OPENRIG_HOME=str(tmp_path / "openrig-qa-home"),
        NODE_COMPILE_CACHE=str(tmp_path / "node-compile-cache"),
        NO_UPDATE_NOTIFIER="1",
        npm_config_update_notifier="false",
        npm_config_fund="false",
        npm_config_audit="false",
    )
    env.update(extra)
    return env


def test_openrig_cli_spike(tmp_path: Path) -> None:
    """Run the CLI-level spike, write captures and verdict.json, and require a pass."""
    prefix = hyper_openrig.resolve_rig(allow_download=True)  # HACF_LIVE_OPENRIG=1 is the consent
    if prefix is None:
        pytest.fail("neither rig nor npx is on PATH; the spike cannot run (nothing was captured)")
    RECORDED.mkdir(parents=True, exist_ok=True)
    for stale in RECORDED.glob("*.json"):
        stale.unlink()
    home = tmp_path / "openrig-qa-home"
    home.mkdir()
    (home / ".openrig-fixture").write_text("", encoding="utf-8")  # rig's fixture-home marker

    repo = _new_repo(tmp_path / "repo", tmp_path / "git-scratch")
    spike = Spike(prefix, _rig_env(repo.env, tmp_path))
    integrity: str | None = None
    try:
        integrity = _run_spike(spike, repo, tmp_path, _runtime.utc_now_iso()[:10])
    except Exception as exc:  # a crash is a failed check, recorded, never hidden
        spike.check("harness_completed", False)
        spike.write_sets["harness_error"] = f"{type(exc).__name__}: {exc}"
    else:
        spike.check("harness_completed", True)
    verdict = {
        "spike_verdict_cli": "pass" if all(spike.checks.values()) else "fail",
        "spike_verdict_daemon": "pending-user-env",
        "npm_dist_integrity": integrity,
        "rig_version": spike.rig_version,
        "captured_at": _runtime.utc_now_iso(),
        "checks": spike.checks,
        "rig_write_sets": spike.write_sets,
        "captures": spike.captures,
    }
    (RECORDED / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    failed = [name for name, ok in spike.checks.items() if not ok]
    assert verdict["spike_verdict_cli"] == "pass", f"failed checks: {failed}"


def _run_spike(spike: Spike, repo: GitRepo, tmp_path: Path, today: str) -> str | None:
    """Run every CLI-level step in order; return the npm dist.integrity of the pinned package."""
    root = repo.path
    mission_id = hyper_openrig.mission_dot_id(MISSION)
    mission = _scaffold(root, MISSION, mission_id, today)
    repo.write("README.md", "# Throwaway repository for the OpenRig spike\n")
    repo.commit("HACF-rendered workspace")
    spike.check("setup_clean", _porcelain(repo) == [])
    integrity = _version_and_integrity(spike, repo, tmp_path)
    _first_slice(spike, repo, mission, mission_id)
    _close_superseded(spike, repo, mission, today)
    _nested_workspace(spike, tmp_path, mission_id, today)
    return integrity


def _version_and_integrity(spike: Spike, repo: GitRepo, tmp_path: Path) -> str | None:
    """`rig --version` must be the pinned 0.6.13; record the package's npm integrity (R24.8).

    `npm view` is not a rig call, so it runs outside the repository with no git check.
    """
    argv = [*spike.prefix, "--version"]
    proc = spike.call("rig-version", argv, cwd=repo.path, repo=repo, timeout=FIRST_CALL_TIMEOUT)
    spike.check("rig_version_pinned", hyper_openrig.parse_version(proc.stdout) == (0, 6, 13))
    spike.check("rig_version_git_clean", _porcelain(repo) == [])
    package = f"{hyper_openrig.RIG_PACKAGE}@{hyper_openrig.RIG_VERSION}"
    proc = spike.call(
        "npm-view-integrity", ["npm", "view", package, "dist.integrity"], cwd=tmp_path
    )
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


def _audit(
    spike: Spike, step: str, repo: GitRepo, workspace: Path, env: dict[str, str] | None = None
) -> dict:
    """Run scope audit; check it ran with no high-severity finding and changed nothing."""
    argv = hyper_openrig.argv_scope_audit(spike.prefix, workspace, MISSION)
    proc = spike.call(step, argv, cwd=repo.path, repo=repo, env=env)
    audit = _json(proc)
    key = step.replace("-", "_")
    spike.check(f"{key}_ok", proc.returncode == 0 and audit.get("ok") is True)
    spike.check(f"{key}_no_high", bool(audit) and _high_findings(audit) == 0)
    spike.check(f"{key}_git_clean", _porcelain(repo) == [])
    return audit


def _ls(spike: Spike, step: str, repo: GitRepo, state: str) -> dict[str, object]:
    """Run slice ls for the mission; check it ran and changed nothing; return {name: status}."""
    argv = hyper_openrig.argv_slice_ls(spike.prefix, repo.path, mission=MISSION, state=state)
    proc = spike.call(step, argv, cwd=repo.path, repo=repo)
    key = step.replace("-", "_")
    spike.check(f"{key}_ran", proc.returncode == 0)
    spike.check(f"{key}_git_clean", _porcelain(repo) == [])
    return {row.get("name"): row.get("status") for row in _json(proc).get("slices", [])}


def _create(spike: Spike, repo: GitRepo, n: int, slug: str, **options: str) -> tuple[Path, dict]:
    """Run slice create; check its scaffold, slice.yaml and that it touched only expected paths.

    Returns the slice dir and the `slice` object of rig's JSON output.
    """
    argv = hyper_openrig.argv_slice_create(spike.prefix, repo.path, MISSION, slug, **options)
    step = f"slice-create-{slug}"
    proc = spike.call(step, argv, cwd=repo.path, repo=repo)
    name = f"{n:02d}-{slug}"
    rel = f"missions/{MISSION}"
    slice_rel = f"{rel}/slices/{name}"
    key = step.replace("-", "_")
    created = _json(proc).get("slice", {})
    spike.check(f"{key}_ran", proc.returncode == 0 and created.get("name") == name)
    spike.check(f"{key}_scaffold", all((repo.path / f).is_file() for f in _slice_files(slice_rel)))
    manifest = repo.path / slice_rel / "slice.yaml"
    pinned = manifest.is_file() and manifest.read_text(encoding="utf-8")
    spike.check(f"{key}_slice_yaml_pinned", pinned == hyper_openrig.render_slice_yaml())
    allowed = {f"{rel}/mission.yaml", f"{rel}/slices/.nn-{n:02d}", *_slice_files(slice_rel)}
    changed = _paths(_porcelain(repo))
    spike.check(f"{key}_git_paths", changed <= allowed and _slice_files(slice_rel) <= changed)
    repo.commit(f"rig slice create {name}")
    return repo.path / slice_rel, created


def _first_slice(spike: Spike, repo: GitRepo, mission: Path, mission_id: str) -> None:
    """Audit, create, frontmatter round-trip (Q7), status classification and proof add."""
    p, root = spike.prefix, repo.path
    _audit(spike, "scope-audit-initial", repo, root)
    slice_dir, created = _create(spike, repo, 1, "adapter-spike", intent="Exercise slice create")
    expected_id = hyper_openrig.slice_dot_id(mission_id, 1)
    spike.check("slice_id_matches_hacf", created.get("id") == expected_id)
    members = hyper_openrig.read_mission_members(mission)  # re-read after the rig call
    first = hyper_openrig.Member("slices/01-adapter-spike/slice.yaml", 10, True)
    spike.check("composition_appended_by_rig", members == [first])

    # Frontmatter round-trip (Q7): HACF keys survive `rig scope slice progress`.
    spec = slice_dir / "SPEC.md"
    hacf_keys = {
        "hacf_schema": 1,
        "hacf_type": "MiniPRD",
        "hacf_status": "compiled",
        "node_id": "openrig_adapter",
        "parent": "orchestration_layer",
        "hacf_touches": [".agents/scripts/hyper_openrig.py"],
        "hacf_oracle": [["uv", "run", "pytest", "dev/tests/openrig", "-q"]],
        "lore_refs": ["ADR-0001"],
        "hacf_legacy_source": None,
    }
    text = spec.read_bytes().decode("utf-8")
    hyper_openrig.cas_write(spec, text, hyper_openrig.splice_frontmatter(text, hacf_keys))
    repo.commit("HACF frontmatter keys")
    before = hyper_openrig.read_frontmatter(spec)
    argv = hyper_openrig.argv_slice_progress(
        p, root, slice_dir.name, mission=MISSION, add="Spike row added by rig"
    )
    proc = spike.call("slice-progress", argv, cwd=root, repo=repo)
    after = hyper_openrig.read_frontmatter(spec)
    spike.check("slice_progress_ran", proc.returncode == 0)
    survived = all(after.get(k) == v for k, v in hacf_keys.items())
    spike.check("hacf_frontmatter_survives_progress", survived)
    owned = [k for k in {*before, *after} if k.startswith("hacf_") or k in hacf_keys]
    spike.write_sets["slice-progress-frontmatter"] = {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
        "hacf_owned_keys_touched": sorted(k for k in owned if before.get(k) != after.get(k)),
    }
    slice_rel = str(slice_dir.relative_to(root))
    progress_paths = {f"{slice_rel}/PROGRESS.md", f"{slice_rel}/SPEC.md"}
    spike.check("slice_progress_git_paths", _paths(_porcelain(repo)) <= progress_paths)
    repo.commit("rig slice progress")

    # Status classification: active, then done once rig has written a C1 proof.
    status = hyper_openrig.set_slice_status(slice_dir, "executing")
    spike.check("hacf_executing_is_active", status == "active")
    repo.commit("status active")
    listed = _ls(spike, "slice-ls-active", repo, "active")
    spike.check("ls_active_lists_active_slice", listed.get(slice_dir.name) == "active")
    body = root.parent / "proof-body.md"  # outside the repository
    body.write_text("The S02 spike harness ran rig proof add.\n", encoding="utf-8")
    argv = hyper_openrig.argv_proof_add(
        p,
        root,
        slice_dir.name,
        mission=MISSION,
        artifact_type="qa",
        verdict="PASS",
        candidate_sha=repo.git("rev-parse", "HEAD").strip(),
        money_evidence="the S02 spike harness ran",
        file=body,
        name="01-qa.md",
    )
    proc = spike.call("proof-add", argv, cwd=root, repo=repo)
    artifact = slice_dir / "proof" / "01-qa.md"
    spike.check("proof_add_ran", proc.returncode == 0 and artifact.is_file())
    header_text = artifact.read_text(encoding="utf-8") if artifact.is_file() else ""
    spike.check(
        "proof_c1_header_valid", bool(header_text) and not hyper_openrig.c1_problems(header_text)
    )
    spike.check("proof_add_git_paths", _paths(_porcelain(repo)) == {f"{slice_rel}/proof/01-qa.md"})
    repo.commit("rig proof add")
    try:
        done = hyper_openrig.set_slice_status(slice_dir, "audited")
    except hyper_openrig.OpenRigError:
        done = None
    spike.check("hacf_audited_is_done", done == "done")
    repo.commit("status done")
    kinds = _finding_kinds(_audit(spike, "scope-audit-after-proof", repo, root))
    spike.check("no_proof_artifact_c1_invalid", "proof_artifact_c1_invalid" not in kinds)
    spike.check("no_missing_proof", "missing_proof" not in kinds)
    listed = _ls(spike, "slice-ls-all", repo, "all")
    spike.check("ls_all_classifies_done", listed.get(slice_dir.name) == "done")
    listed = _ls(spike, "slice-ls-active-after-done", repo, "active")
    spike.check("done_excluded_from_active", slice_dir.name not in listed)


def _close_superseded(spike: Spike, repo: GitRepo, mission: Path, today: str) -> None:
    """Superseded slices close into closed/: one through rig, one through close_superseded()."""
    root = repo.path
    rel = f"missions/{MISSION}"
    _create(spike, repo, 2, "rig-close")
    _create(spike, repo, 3, "hacf-close")
    argv = hyper_openrig.argv_slice_close(
        spike.prefix, root, "02-rig-close", reason="superseded", mission=MISSION
    )
    proc = spike.call("slice-close-superseded", argv, cwd=root, repo=repo)
    closed = mission / "closed" / "02-rig-close"
    moved = closed.is_dir() and not (mission / "slices" / "02-rig-close").exists()
    spike.check("rig_close_moved", proc.returncode == 0 and moved)
    front = hyper_openrig.read_frontmatter(closed / "SPEC.md")
    spike.check("rig_close_status", front.get("status") == "closed-superseded")
    spike.check("rig_close_closed_on", "closed-on" in front)
    old_and_new = set()
    for where in ("slices", "closed"):
        old_and_new |= _slice_files(f"{rel}/{where}/02-rig-close")
    allowed = {f"{rel}/mission.yaml", *old_and_new}
    spike.check("rig_close_git_paths", _paths(_porcelain(repo)) <= allowed)
    repo.commit("rig slice close")
    hyper_openrig.close_superseded(mission, "03-hacf-close", today=today)
    repo.commit("HACF close_superseded")
    refs = [m.ref for m in hyper_openrig.read_mission_members(mission)]
    spike.check("composition_after_closes", refs == ["slices/01-adapter-spike/slice.yaml"])
    listed = _ls(spike, "slice-ls-closed", repo, "closed")
    expected = {"02-rig-close": "closed-superseded", "03-hacf-close": "closed-superseded"}
    spike.check("closed_slices_classified", listed == expected)
    _audit(spike, "scope-audit-final", repo, root)


def _nested_workspace(spike: Spike, tmp_path: Path, mission_id: str, today: str) -> None:
    """The repo nested in a rig workspace root, with OPENRIG_WORK_ROOT at the outer root (R22.4).

    `--workspace <repo>` must select the repo's own project.yaml and missions/, and nothing
    outside the repo may change.
    """
    outer = tmp_path / "rig-workspace"
    _scaffold(outer, "outer-mission", hyper_openrig.mission_dot_id("outer-mission", 1), today)
    inner = _new_repo(outer / "projects" / "nested-repo", tmp_path / "git-scratch-nested")
    _scaffold(inner.path, MISSION, mission_id, today)
    inner.commit("HACF-rendered workspace (nested)")

    def outer_files() -> dict[str, bytes]:
        return {k: v for k, v in _snapshot(outer).items() if not k.startswith("projects/")}

    before = outer_files()
    env = _rig_env(inner.env, tmp_path, OPENRIG_WORK_ROOT=str(outer))
    argv = hyper_openrig.argv_slice_create(spike.prefix, inner.path, MISSION, "nested-spike")
    proc = spike.call("nested-slice-create", argv, cwd=inner.path, repo=inner, env=env)
    rel = f"missions/{MISSION}"
    slice_rel = f"{rel}/slices/01-nested-spike"
    created = (inner.path / slice_rel / "slice.yaml").is_file()
    spike.check("nested_create_in_inner", proc.returncode == 0 and created)
    allowed = {f"{rel}/mission.yaml", f"{rel}/slices/.nn-01", *_slice_files(slice_rel)}
    spike.check("nested_create_git_paths", _paths(_porcelain(inner)) <= allowed)
    inner.commit("rig slice create (nested)")
    argv = hyper_openrig.argv_slice_ls(spike.prefix, inner.path, state="all")
    proc = spike.call("nested-slice-ls", argv, cwd=inner.path, repo=inner, env=env)
    rows = _json(proc).get("slices", [])
    prefix = os.path.realpath(inner.path / "missions") + os.sep
    inside = all(os.path.realpath(str(r.get("path", ""))).startswith(prefix) for r in rows)
    outer_listed = any(r.get("mission") == "outer-mission" for r in rows)
    spike.check("nested_ls_uses_inner", proc.returncode == 0 and bool(rows) and inside)
    spike.check("nested_ls_skips_outer", not outer_listed)
    spike.check("nested_slice_ls_git_clean", _porcelain(inner) == [])
    _audit(spike, "nested-scope-audit", inner, inner.path, env)
    spike.check("nested_outer_untouched", outer_files() == before)
