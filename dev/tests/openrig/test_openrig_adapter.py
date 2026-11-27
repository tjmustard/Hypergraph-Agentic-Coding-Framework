"""Deterministic tests for hyper_openrig.py, the OpenRig adapter (S02; the set is in the contract).

Nothing here downloads or runs the real rig: version-rule tests put a stub `rig` on PATH, and
registration tests use temp catalogs and temp repositories with the confirmation function
replaced. Every file a test writes lives under tmp_path.
"""

from __future__ import annotations

import ast
import os
import shutil
import stat
from pathlib import Path

import hyper_openrig
import pytest
import yaml
from conftest import FIXTURES_DIR, SCRIPTS_DIR, GitRepo

OPENRIG_FIXTURE = FIXTURES_DIR / "openrig-0.6.13"
MISSION = "release-0.6.0"
MISSION_ID = "OPR.0.6.0"
# The two mission.yaml shapes HACF must read and edit (R21a): rig 0.6.13's unnamed template (every
# mission created before 0.6.1, an early install's included) and the named shape rig 0.7.16
# and HACF now write.
MISSION_SHAPES = {
    "0.6.13": (OPENRIG_FIXTURE / "mission.yaml").read_text(encoding="utf-8"),
    "0.7.16": hyper_openrig.render_mission_yaml("mission"),
}


@pytest.fixture(autouse=True)
def _no_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an outer git's GIT_* variables (e.g. from a hook) away from in-process git calls."""
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _spec(slice_id: str, **keys: object) -> str:
    """Return a slice SPEC.md with HACF and rig keys in a fixed order (flow-style values)."""
    fields = {
        "id": slice_id,
        "mission": MISSION,
        "status": "active",
        "intent": "a test slice",
        "hacf_schema": 1,
        "hacf_type": "MiniPRD",
        "hacf_status": "compiled",
        "node_id": "openrig_adapter",
        **keys,
    }
    lines = [f"{key}: {hyper_openrig.flow_value(value)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n# MiniPRD: test\n"


def _mission(tmp_path: Path, slices: dict[str, str]) -> Path:
    """Build missions/release-0.6.0 with the given slices (dir name -> SPEC.md) registered."""
    mission = tmp_path / "missions" / MISSION
    _write(mission / "mission.yaml", hyper_openrig.render_mission_yaml(MISSION))
    _write(mission / "SPEC.md", f"---\nid: {MISSION_ID}\nintent: test mission\n---\n")
    for name, spec in slices.items():
        _write(mission / "slices" / name / "slice.yaml", hyper_openrig.render_slice_yaml())
        _write(mission / "slices" / name / "SPEC.md", spec)
        hyper_openrig.add_slice_to_mission(mission, name)
    return mission


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------
# Manifests, composition, dot-IDs, status
# ---------------------------------------------------------------------------


def test_render_matches_fixture() -> None:
    """Each renderer returns the bytes transcribed from rig's templates.

    project.yaml, workspace.yaml and slice.yaml are 0.6.13's bytes, unchanged at 0.7.16.
    mission.yaml is rig 0.7.16's missionManifest(name) (dist/commands/scope.js:30): the 0.6.13
    fixture, kept as the 0.6.13 record, with `  name: "<name>"` inserted after `metadata:` (R21a).
    """
    renderers = {
        "project.yaml": hyper_openrig.render_project_yaml,
        "workspace.yaml": hyper_openrig.render_workspace_yaml,
        "mission.yaml": lambda: hyper_openrig.render_mission_yaml("release-1.0"),
        "slice.yaml": hyper_openrig.render_slice_yaml,
    }
    for name in ("project.yaml", "workspace.yaml", "slice.yaml"):
        assert renderers[name]().encode("utf-8") == (OPENRIG_FIXTURE / name).read_bytes(), name
    fixture = (OPENRIG_FIXTURE / "mission.yaml").read_text(encoding="utf-8")
    assert fixture.count("metadata:\n") == 1 and "name:" not in fixture
    named = fixture.replace("metadata:\n", 'metadata:\n  name: "release-1.0"\n')
    assert renderers["mission.yaml"]() == named
    assert yaml.safe_load(named)["metadata"] == {"name": "release-1.0", "status": "planned"}
    for bad in ("", "-x", "a b", "a/b", 'a"b'):
        with pytest.raises(hyper_openrig.UsageError):
            hyper_openrig.render_mission_yaml(bad)
    schemas = {
        "project.yaml": hyper_openrig.SCHEMA_PROJECT,
        "workspace.yaml": hyper_openrig.SCHEMA_WORKSPACE,
        "mission.yaml": hyper_openrig.SCHEMA_MISSION,
        "slice.yaml": hyper_openrig.SCHEMA_SLICE,
    }
    for name, schema in schemas.items():
        assert yaml.safe_load(renderers[name]())["schema"] == schema
    sources = (OPENRIG_FIXTURE / "SOURCES.md").read_text(encoding="utf-8")
    assert "v0.6.13" in sources and hyper_openrig.RIG_COMMIT in sources


@pytest.mark.parametrize("shape", sorted(MISSION_SHAPES))
def test_composition_rules(tmp_path: Path, shape: str) -> None:
    """Rig's composition rules refuse each bad entry; add/remove keep every other byte.

    Run on both mission.yaml shapes: 0.6.13's unnamed file and the named 0.7.16 one (R21a).
    """
    template = MISSION_SHAPES[shape]
    mission = tmp_path / "mission"
    path = mission / "mission.yaml"
    _write(path, template)
    for name in ("01-a", "02-b"):
        _write(mission / "slices" / name / "slice.yaml", hyper_openrig.render_slice_yaml())
    outside = _write(tmp_path / "outside" / "slice.yaml", "kind: slice\n")
    (mission / "slices" / "03-link").mkdir()
    os.symlink(outside, mission / "slices" / "03-link" / "slice.yaml")
    os.symlink(outside.parent, mission / "slices" / "04-escape")

    def check(entries: list[dict]) -> list:
        data = yaml.safe_load(template)
        data["composition"]["slices"] = entries
        return hyper_openrig.validate_mission_composition(data, path)

    a = {"ref": "slices/01-a/slice.yaml", "order": 10, "active": True}
    b = {"ref": "slices/02-b/slice.yaml", "order": 20}
    assert check([a, b]) == [
        hyper_openrig.Member("slices/01-a/slice.yaml", 10, True),
        hyper_openrig.Member("slices/02-b/slice.yaml", 20, True),
    ]
    refused = {
        "lifecycle_path_escape": [
            [{"ref": "../outside/slice.yaml", "order": 10}],
            [{"ref": str(outside), "order": 10}],
            [{"ref": "slices/04-escape/slice.yaml", "order": 10}],
        ],
        "lifecycle_member_missing": [[{"ref": "slices/09-none/slice.yaml", "order": 10}]],
        "lifecycle_member_symlink": [[{"ref": "slices/03-link/slice.yaml", "order": 10}]],
        "lifecycle_membership_duplicate": [[a, {**a, "ref": "slices/01-a/./slice.yaml"}]],
        "lifecycle_order_invalid": [
            [a, {**b, "order": 10}],
            [a, {**b, "order": 5}],
            [{**a, "order": "10"}],
            [{**a, "order": True}],
        ],
        "lifecycle_field_missing": [[{"order": 10}]],
        "lifecycle_membership_invalid": [["slices/01-a/slice.yaml"]],
    }
    for kind, cases in refused.items():
        for entries in cases:
            with pytest.raises(hyper_openrig.CompositionError) as caught:
                check(entries)
            assert caught.value.kind == kind, entries

    # add_slice_to_mission: orders 10, 20; the template's comments and keys stay byte-identical.
    assert hyper_openrig.add_slice_to_mission(mission, "01-a").order == 10
    assert hyper_openrig.add_slice_to_mission(mission, "02-b").order == 20
    text = path.read_text(encoding="utf-8")
    assert text.startswith(template.split("  slices: []")[0])
    assert text.endswith("# Optional team and SDLC sections are added here.\n")
    assert hyper_openrig.read_mission_members(mission)[1].ref == "slices/02-b/slice.yaml"
    before = path.read_bytes()
    with pytest.raises(hyper_openrig.Refused):
        hyper_openrig.add_slice_to_mission(mission, "01-a")
    with pytest.raises(hyper_openrig.CompositionError):
        hyper_openrig.add_slice_to_mission(mission, "09-missing")
    assert path.read_bytes() == before

    # A file changed after it was read is never overwritten (compare-and-swap).
    with pytest.raises(hyper_openrig.Refused):
        hyper_openrig.cas_write(path, "stale text\n", "new text\n")
    assert path.read_bytes() == before

    # Removing every member gives back the template bytes.
    hyper_openrig.remove_slice_from_mission(mission, "slices/01-a/slice.yaml")
    hyper_openrig.remove_slice_from_mission(mission, "slices/02-b/slice.yaml")
    assert path.read_text(encoding="utf-8") == template


def test_mission_editors_keep_unknown_keys(tmp_path: Path) -> None:
    """metadata.name, an unknown metadata.owner and a top-level team: survive every edit (R21a).

    add, remove and a status change each change only their own value; nothing is normalised.
    """
    mission = tmp_path / "mission"
    path = mission / "mission.yaml"
    team = "team:\n  lead: someone  # a comment HACF keeps\n"
    extra = MISSION_SHAPES["0.7.16"].replace(
        "  status: planned\n", "  owner: me\n  status: planned\n"
    )
    extra = extra + team
    _write(path, extra)
    _write(mission / "slices" / "01-a" / "slice.yaml", hyper_openrig.render_slice_yaml())
    kept = ('  name: "mission"\n', "  owner: me\n", team)

    hyper_openrig.add_slice_to_mission(mission, "01-a")
    added = path.read_text(encoding="utf-8")
    assert all(part in added for part in kept)
    expected = yaml.safe_load(extra)
    expected["composition"]["slices"] = [
        {"ref": "slices/01-a/slice.yaml", "order": 10, "active": True}
    ]
    assert yaml.safe_load(added) == expected

    assert hyper_openrig.set_mission_status(mission, "active") == "active"
    status = path.read_text(encoding="utf-8")
    assert status == added.replace("  status: planned\n", "  status: active\n")

    hyper_openrig.remove_slice_from_mission(mission, "slices/01-a/slice.yaml")
    assert path.read_text(encoding="utf-8") == extra.replace(
        "  status: planned\n", "  status: active\n"
    )


def test_dot_ids_opr() -> None:
    """Release missions keep their semver, others take the escape band; slices add one ordinal."""
    assert hyper_openrig.mission_dot_id("release-0.6.0") == "OPR.0.6.0"
    assert hyper_openrig.mission_dot_id("release-1.2") == "OPR.1.2"
    ordinal = hyper_openrig.next_escape_band_ordinal(["OPR.99.0.1", "OPR.99.0.3", None, "OPR.0.6"])
    assert ordinal == 4
    assert hyper_openrig.next_escape_band_ordinal([]) == 1
    assert hyper_openrig.mission_dot_id("hyper-update", ordinal) == "OPR.99.0.4"
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.mission_dot_id("hyper-update")
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.mission_dot_id("release-0.6.0", prefix="opr")
    for mission_id in ("OPR.0.6.0", "OPR.1.2", "OPR.99.0.4"):
        assert hyper_openrig.is_mission_dot_id(mission_id)
        for k in (1, 4, 12):
            slice_id = hyper_openrig.slice_dot_id(mission_id, k)
            assert slice_id == f"{mission_id}.{k}"
            assert hyper_openrig.DOT_ID_RE.match(slice_id)
            assert hyper_openrig.is_slice_dot_id(slice_id)
    assert not hyper_openrig.is_mission_dot_id("OPR.99.7.8")
    assert not hyper_openrig.is_slice_dot_id("OPR.0.6")
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.slice_dot_id("OPR.0.6.0", 0)


def test_status_map(tmp_path: Path) -> None:
    """hacf_status maps to rig's status; done needs proof; slice.yaml is never written."""
    for status in ("compiled", "executing", "executed", "audit_failed", "blocked"):
        assert hyper_openrig.openrig_status(status) == "active"
    assert hyper_openrig.openrig_status("audited") == "done"
    assert hyper_openrig.openrig_status("superseded") == "closed-superseded"
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.openrig_status("shipped")

    mission = _mission(tmp_path, {"01-a": _spec("OPR.0.6.0.1", status="draft")})
    slice_dir = mission / "slices" / "01-a"
    manifest = (slice_dir / "slice.yaml").read_bytes()
    assert hyper_openrig.set_slice_status(slice_dir, "executing") == "active"
    assert hyper_openrig.read_frontmatter(slice_dir / "SPEC.md")["status"] == "active"
    with pytest.raises(hyper_openrig.Refused):
        hyper_openrig.set_slice_status(slice_dir, "audited")  # no PROOF.md, no C1 artifact
    _write(slice_dir / "PROOF.md", "# PROOF\n")
    _write(slice_dir / "proof" / "01-qa.md", "---\nslice: OPR.0.6.0.1\nverdict: MAYBE\n---\n")
    with pytest.raises(hyper_openrig.Refused):
        hyper_openrig.set_slice_status(slice_dir, "audited")  # the artifact's header is invalid
    header = {
        "slice": "OPR.0.6.0.1",
        "candidate_sha": "a" * 40,
        "artifact_type": "qa",
        "verdict": "PASS",
        "money_evidence": "all tests pass",
    }
    _write(slice_dir / "proof" / "02-qa.md", hyper_openrig.render_c1_artifact(header, "ok\n"))
    assert hyper_openrig.set_slice_status(slice_dir, "audited") == "done"
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.set_slice_status(slice_dir, "superseded")
    keys = list(hyper_openrig.read_frontmatter(slice_dir / "SPEC.md"))
    assert keys == list(yaml.safe_load(_spec("x").split("---")[1]))
    assert (slice_dir / "slice.yaml").read_bytes() == manifest


def test_close_superseded_moves_and_unregisters(tmp_path: Path) -> None:
    """A superseded slice moves to closed/, leaves the composition and gets closed-superseded."""
    mission = _mission(
        tmp_path,
        {
            "01-a": _spec("OPR.0.6.0.1"),
            "02-b": _spec("OPR.0.6.0.2", depends_on=["OPR.0.6.0.3", "OPR.0.6.0.6"]),
            "03-c": _spec("OPR.0.6.0.3"),
            "04-d": _spec("OPR.0.6.0.4", status="done", depends_on=["OPR.0.6.0.1"]),
        },
    )
    # 04-d depends on 01-a but is done, so it is not a live dependent.
    closed = hyper_openrig.close_superseded(mission, "01-a", today="2026-10-26")
    assert closed == mission / "closed" / "01-a" and closed.is_dir()
    assert not (mission / "slices" / "01-a").exists()
    refs = [m.ref for m in hyper_openrig.read_mission_members(mission)]
    assert refs == [f"slices/{n}/slice.yaml" for n in ("02-b", "03-c", "04-d")]
    front = hyper_openrig.read_frontmatter(closed / "SPEC.md")
    assert front["status"] == "closed-superseded" and front["closed-on"] == "2026-10-26"
    assert list(front)[: len(front) - 1] == list(yaml.safe_load(_spec("x").split("---")[1])), (
        "keys keep their order; closed-on is appended"
    )
    deps = hyper_openrig.read_frontmatter(mission / "slices/04-d/SPEC.md")["depends_on"]
    assert deps == ["OPR.0.6.0.1"], "a done slice's dependencies are left alone"

    # 03-c has a live dependent, 02-b.
    before = _snapshot(mission)
    with pytest.raises(hyper_openrig.Refused, match="02-b"):
        hyper_openrig.close_superseded(mission, "03-c")
    assert _snapshot(mission) == before
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.close_superseded(mission, "03-c", rewrite_dependents_to="OPR.0.6.0.3")
    assert _snapshot(mission) == before

    # A dependency in slice.yaml execution.depends_on is never rewritten by HACF.
    manifest = mission / "slices" / "02-b" / "slice.yaml"
    manifest.write_text(
        hyper_openrig.render_slice_yaml() + "execution:\n  depends_on: [OPR.0.6.0.3]\n",
        encoding="utf-8",
    )
    before = _snapshot(mission)
    with pytest.raises(hyper_openrig.Refused, match="slice.yaml"):
        hyper_openrig.close_superseded(mission, "03-c", rewrite_dependents_to="OPR.0.6.0.5")
    assert _snapshot(mission) == before
    manifest.write_text(hyper_openrig.render_slice_yaml(), encoding="utf-8")

    hyper_openrig.close_superseded(mission, "03-c", rewrite_dependents_to="OPR.0.6.0.5")
    assert (mission / "closed" / "03-c").is_dir()
    deps = hyper_openrig.read_frontmatter(mission / "slices/02-b/SPEC.md")["depends_on"]
    assert deps == ["OPR.0.6.0.5", "OPR.0.6.0.6"]
    assert [m.ref for m in hyper_openrig.read_mission_members(mission)] == [
        "slices/02-b/slice.yaml",
        "slices/04-d/slice.yaml",
    ]


def test_no_shell() -> None:
    """Every subprocess in hyper_openrig.py goes through _run, which takes an argv list only."""
    source = (SCRIPTS_DIR / "hyper_openrig.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run"]
    assert len(run_defs) == 1
    inside = {id(node) for node in ast.walk(run_defs[0])}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "subprocess" and node.attr not in ("TimeoutExpired",):
                assert id(node) in inside, f"subprocess.{node.attr} outside _run"
            assert (node.value.id, node.attr) not in {("os", "system"), ("os", "popen")}
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec")
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert isinstance(keyword.value, ast.Constant) and keyword.value.value is False
    with pytest.raises(TypeError):
        hyper_openrig._run("rig --version", timeout=5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        hyper_openrig._run(["rig", 1], timeout=5)  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# rig argv wrappers and the version rule
# ---------------------------------------------------------------------------

PROOF_ARGS = {
    "artifact_type": "qa",
    "verdict": "PASS",
    "candidate_sha": "0123456789abcdef0123456789abcdef01234567",
    "money_evidence": "the oracle passed",
}


def test_rig_calls_pass_name(tmp_path: Path) -> None:
    """Every artifact-naming wrapper passes --name; argv values are checked before any call."""
    workspace = tmp_path / "repo"
    body = tmp_path / "body.md"
    assert hyper_openrig.ARTIFACT_NAMING_WRAPPERS
    for wrapper in hyper_openrig.ARTIFACT_NAMING_WRAPPERS:
        argv = wrapper(["rig"], workspace, "04-adapter", file=body, name="01-qa.md", **PROOF_ARGS)
        assert argv[argv.index("--name") + 1] == "01-qa.md"
        assert all(isinstance(arg, str) for arg in argv)
        assert argv[:5] == ["rig", "proof", "--workspace", str(workspace), "add"]
        with pytest.raises(TypeError):
            wrapper(["rig"], workspace, "04-adapter", file=body, **PROOF_ARGS)
        for name in ("../SPEC.md", "proof/x.md", "x.txt", "-x.md", ""):
            with pytest.raises(hyper_openrig.UsageError):
                wrapper(["rig"], workspace, "04-adapter", file=body, name=name, **PROOF_ARGS)
        with pytest.raises(hyper_openrig.UsageError):
            wrapper(
                ["rig"],
                workspace,
                "04-adapter",
                file=body,
                name="a.md",
                mission="--json",
                **PROOF_ARGS,
            )
    bad = {"money_evidence": "--verdict BLOCKING"}
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.argv_proof_add(
            ["rig"], workspace, "04-adapter", file=body, name="a.md", **{**PROOF_ARGS, **bad}
        )
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.argv_scope_audit(["rig"], Path("relative"), MISSION)
    audit = hyper_openrig.argv_scope_audit(["rig"], workspace, MISSION)
    expected = ["rig", "scope", "--workspace", str(workspace), "audit", "--mission", MISSION]
    assert audit == [*expected, "--json"]
    create = hyper_openrig.argv_slice_create(
        ["rig"], workspace, MISSION, "adapter", depends_on=["OPR.0.6.0.2"]
    )
    assert create[create.index("--depends-on") + 1] == "OPR.0.6.0.2" and create[-1] == "--json"
    close = hyper_openrig.argv_slice_close(["rig"], workspace, "04-adapter", reason="superseded")
    assert close[close.index("--reason") + 1] == "superseded"
    with pytest.raises(hyper_openrig.UsageError):
        hyper_openrig.argv_slice_close(["rig"], workspace, "04-adapter", reason="done")


def _stub_rig(bin_dir: Path, version: str, log: Path) -> None:
    """Put an executable `rig` in bin_dir that logs its argv and prints version for --version."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "rig"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f'if [ "$1" = "--version" ]; then echo "{version}"; fi\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_rig_version_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """0.6.13, 0.7.16 and 0.7.21 delegate; in-range 0.7.x, 0.6.14 and 0.7.0 are native; 0.8 stops.

    The set is read from .agents/config/openrig_contract.yml; 0.5.9 stops too.
    """
    mission = _mission(tmp_path / "ws", {"04-adapter": _spec("OPR.0.6.0.4")})
    slice_dir = mission / "slices" / "04-adapter"
    body = _write(tmp_path / "body.md", "The oracle passed.\n")
    contract = hyper_openrig.load_contract()
    assert contract.source == "file"
    # An unlisted in-range version derived from the contract, never a literal (slice 3's compat
    # clone lists its candidate as a watcher entry).
    top_07 = max(int(v.split(".")[2]) for v in contract.versions if v.startswith("0.7."))
    in_range = f"0.7.{top_07 + 1}"
    expectations = {
        "0.6.13": "delegate",
        "0.7.16": "delegate",
        "0.7.21": "delegate",
        in_range: "native",
        "0.6.14": "native",
        "0.7.0": "native",
        "0.5.9": "stop",
        "0.8.0": "stop",
    }
    named = " or ".join(contract.versions)
    for index, (version, mode) in enumerate(expectations.items()):
        log = tmp_path / f"rig-{version}.log"
        bin_dir = tmp_path / f"bin-{version}"
        _stub_rig(bin_dir, version, log)
        monkeypatch.setenv("PATH", str(bin_dir))
        plan = hyper_openrig.plan_rig()
        assert plan.mode == mode and plan.version == version
        name = f"0{index}-qa.md"
        target = hyper_openrig.write_proof_artifact(
            slice_dir,
            workspace=tmp_path / "ws",
            plan=plan,
            body_file=body,
            name=name,
            **PROOF_ARGS,
        )
        calls = log.read_text(encoding="utf-8").splitlines()
        warning = capsys.readouterr().err
        if mode == "delegate":
            assert calls[0] == "--version" and calls[1].startswith("proof --workspace ")
            assert f"--name {name}" in calls[1]
            assert not target.exists(), "the stub rig writes nothing; HACF did not write either"
            assert warning == ""
        else:
            assert calls == ["--version"], "no rig call besides --version"
            assert f"rig {named}" in warning
            assert "rig 0.6.13 or 0.7.16 or 0.7.21" in warning
            if version == in_range:
                assert "inside the probed range" in warning and "does not probe" in warning
            assert not hyper_openrig.c1_problems(target.read_text(encoding="utf-8"))
            header = hyper_openrig.split_frontmatter(target.read_text(encoding="utf-8"))[2]
            assert header["slice"] == "OPR.0.6.0.4" and header["verdict"] == "PASS"
    stop_text = hyper_openrig.version_decision("0.8.0")[1]
    assert ".agents/config/openrig_contract.yml" in stop_text
    assert "test_openrig_live.py passes" in stop_text
    assert hyper_openrig.ACCEPTED_RIG_VERSIONS == contract.versions
    assert {"0.6.13", "0.7.16", "0.7.21"} <= set(hyper_openrig.ACCEPTED_RIG_VERSIONS)
    top = max(
        (e.version for e in contract.accepted if e.accepted_by == "person"),
        key=lambda v: tuple(int(part) for part in v.split(".")),
    )
    assert hyper_openrig.RIG_VERSION == top
    assert hyper_openrig.NPX_PREFIX[2] == f"--package=@openrig/cli@{top}"

    # No rig on PATH: npx is never used without explicit download consent.
    npx_log = tmp_path / "npx.log"
    _stub_rig(tmp_path / "bin-npx", "0.6.13", npx_log)
    (tmp_path / "bin-npx" / "rig").rename(tmp_path / "bin-npx" / "npx")
    monkeypatch.setenv("PATH", str(tmp_path / "bin-npx"))
    assert hyper_openrig.resolve_rig() is None
    assert hyper_openrig.plan_rig().mode == "absent"
    assert not npx_log.exists()
    assert hyper_openrig.resolve_rig(allow_download=True) == list(hyper_openrig.NPX_PREFIX)


# ---------------------------------------------------------------------------
# register-project, unregister, doctor
# ---------------------------------------------------------------------------

CATALOG = """\
schema: openrig.workspace/v0alpha1
# the user's catalog; HACF must keep every byte
projects:
  - id: default
    root: .
  - id: other-project
    root: /srv/other  # a comment HACF keeps
"""


confirm_without_tty = hyper_openrig.confirm  # the real function, kept before any monkeypatch


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """Return a temp OpenRig catalog that stands in for the user's workspace.yaml."""
    return _write(tmp_path / "openrig-workspace" / "workspace.yaml", CATALOG)


@pytest.fixture
def confirmations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the terminal confirmation with a yes; return the prompts it was asked."""
    asked: list[str] = []

    def yes(prompt: str) -> bool:
        asked.append(prompt)
        return True

    monkeypatch.setattr(hyper_openrig, "confirm", yes)
    return asked


def _backups(repo: Path) -> list[Path]:
    folder = repo / ".agents" / "state" / "openrig" / "catalog-backups"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def _registrations(repo: Path) -> list[dict]:
    return hyper_openrig.load_registrations(repo)


def test_register_project_refuses_existing_id(
    tmp_git_repo: GitRepo, catalog: Path, monkeypatch: pytest.MonkeyPatch, confirmations: list
) -> None:
    """An id already in the catalog exits 5 before any prompt, backup or write."""
    monkeypatch.chdir(tmp_git_repo.path)
    for project_id in ("default", "other-project"):
        code = hyper_openrig.main(
            ["register-project", "--catalog", str(catalog), "--id", project_id]
        )
        assert code == hyper_openrig.EXIT_REFUSED
    assert catalog.read_text(encoding="utf-8") == CATALOG
    assert confirmations == [] and _backups(tmp_git_repo.path) == []
    assert not (tmp_git_repo.path / ".agents").exists()
    code = hyper_openrig.main(["register-project", "--catalog", str(catalog), "--id", "Bad Id"])
    assert code == hyper_openrig.EXIT_USAGE


def test_register_project_fingerprint_and_doctor(
    tmp_git_repo: GitRepo,
    catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmations: list,
    capsys: pytest.CaptureFixture,
) -> None:
    """Register appends as text and fingerprints the repo; doctor reports moves and re-creations."""
    repo = tmp_git_repo.path
    monkeypatch.chdir(repo)
    args = ["register-project", "--catalog", str(catalog), "--id", "hacf-test"]

    # --dry-run prints the change and writes nothing; it needs no confirmation.
    assert hyper_openrig.main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"root: {os.path.realpath(repo)}" in out and "dry run" in out
    assert catalog.read_text(encoding="utf-8") == CATALOG and confirmations == []

    assert hyper_openrig.main(args) == 0
    assert len(confirmations) == 1
    text = catalog.read_text(encoding="utf-8")
    assert text.startswith(CATALOG), "every existing byte stays"
    assert text[len(CATALOG) :] == f"  - id: hacf-test\n    root: {os.path.realpath(repo)}\n"
    backups = _backups(repo)
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == CATALOG
    assert (repo / ".agents" / "state" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    [reg] = _registrations(repo)
    roots = tmp_git_repo.git("rev-list", "--max-parents=0", "HEAD").split()
    assert reg["id"] == "hacf-test" and reg["catalog"] == str(catalog.resolve())
    assert reg["root"] == os.path.realpath(repo) and reg["root_commits"] == roots
    assert reg["registered_at"].endswith("+00:00")
    assert hyper_openrig.main(["doctor", "--catalog", str(catalog)]) == 0
    assert "doctor: ok" in capsys.readouterr().out

    # A second registration of the same root under another id is refused.
    again = ["register-project", "--catalog", str(catalog), "--id", "hacf-two"]
    assert hyper_openrig.main(again) == hyper_openrig.EXIT_REFUSED

    # Without a terminal the real confirmation refuses and nothing changes.
    monkeypatch.setattr(hyper_openrig, "confirm", confirm_without_tty)
    unregister = ["unregister", "--catalog", str(catalog), "--id", "hacf-test"]
    assert hyper_openrig.main(unregister) == hyper_openrig.EXIT_REFUSED
    assert catalog.read_text(encoding="utf-8") == text

    # Re-created repository at the same path: a different root commit.
    shutil.rmtree(repo / ".git")
    tmp_git_repo.git("init", "-q", "-b", "main")
    tmp_git_repo.git("config", "commit.gpgsign", "false")
    tmp_git_repo.write("README.md", "# A different repository\n")
    tmp_git_repo.commit("a different initial commit", "README.md")
    assert hyper_openrig.main(["doctor", "--catalog", str(catalog)]) == hyper_openrig.EXIT_FINDINGS
    assert "fingerprint" in capsys.readouterr().out

    # Moved root: the registered path no longer exists.
    moved = tmp_path / "moved-repo"
    repo.rename(moved)
    monkeypatch.chdir(moved)
    assert hyper_openrig.main(["doctor", "--catalog", str(catalog)]) == hyper_openrig.EXIT_FINDINGS
    assert "no longer exists" in capsys.readouterr().out


def test_unregister(
    tmp_git_repo: GitRepo,
    catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmations: list,
) -> None:
    """Unregister backs up first, removes only this repo's entry and drops its fingerprint."""
    repo = tmp_git_repo.path
    monkeypatch.chdir(repo)
    assert (
        hyper_openrig.main(["register-project", "--catalog", str(catalog), "--id", "hacf-x"]) == 0
    )
    registered = catalog.read_text(encoding="utf-8")

    # Another project's entry is never removed from here.
    other = ["unregister", "--catalog", str(catalog), "--id", "other-project"]
    assert hyper_openrig.main(other) == hyper_openrig.EXIT_REFUSED
    missing = ["unregister", "--catalog", str(catalog), "--id", "not-there"]
    assert hyper_openrig.main(missing) == hyper_openrig.EXIT_REFUSED
    assert catalog.read_text(encoding="utf-8") == registered and len(_backups(repo)) == 1

    assert hyper_openrig.main(["unregister", "--catalog", str(catalog), "--id", "hacf-x"]) == 0
    assert catalog.read_text(encoding="utf-8") == CATALOG, "back to the original bytes"
    backups = _backups(repo)
    assert len(backups) == 2 and backups[1].read_text(encoding="utf-8") == registered
    assert _registrations(repo) == [] and len(confirmations) == 2

    # Removing the only entry leaves an empty list, not a null.
    lone = _write(tmp_path / "lone" / "workspace.yaml", hyper_openrig.render_workspace_yaml())
    assert hyper_openrig.main(["register-project", "--catalog", str(lone), "--id", "solo"]) == 0
    text = lone.read_text(encoding="utf-8")
    result = hyper_openrig.remove_catalog_entry(text, "default")
    result = hyper_openrig.remove_catalog_entry(result, "solo")
    assert yaml.safe_load(result)["projects"] == []
    assert result.startswith(f"schema: {hyper_openrig.SCHEMA_WORKSPACE}\nprojects: []")
