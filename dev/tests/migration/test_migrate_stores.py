"""Deterministic tests for hyper_migrate.py APPLY steps 6-9 and `verify` (S13b).

Every project is a temp git repository built with S12a's v0.5.9 fixture builder
(`dev/tests/fixtures/build_user_project_v059.py`); a variant the builder lacks is built here on top
of its output, never by editing the builder. Each project gets this checkout's `.agents/scripts`,
`.agents/schemas` and `.agents/install-templates` (the state after the v0.6.0 installer ran) and
runs its own copy of hyper_migrate.py as a subprocess. Nothing reads or writes this checkout's
`spec/`, `lore/` or ledger; PATH holds no `rig`.

The fixture ledger (`ledger_v1_real_broken.yml`) also carries three graph errors of its own (two
dangling depends_on edges and an inverted implements) that no migration step may fix, since only
hyper_ledger.py commands write the ledger and none removes an edge. Tests 8 and 10, which need
`hyper_ledger.py validate` to exit 0, use the `clean_ledger` variant: the same file with those
three edges removed and its ` ###` text break kept.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_migrate_specs as specs
import yaml
from conftest import REPO_ROOT

JOURNAL = specs.JOURNAL
V059_COMMIT = "984ca46"
LEDGER = "spec/compiled/architecture.yml"
SLICE_01 = "missions/legacy-unassigned/slices/01-invoices"
REDIRECT_MARK = "<!-- hacf-lore-redirect: "
PRODUCT_BEGIN = "<!-- BEGIN HACF MANAGED BLOCK: migrated-product-context -->"
PRODUCT_END = "<!-- END HACF MANAGED BLOCK: migrated-product-context -->"
# The three pre-existing graph errors of the fixture ledger, and the text without each.
LEDGER_FIXES = (
    (
        "    - orchestration_layer\n    - hyper_resolve\n    implements: []\n",
        "    - orchestration_layer\n    implements: []\n",
    ),
    (
        "    depends_on:\n    - framework_optimization\n    implements: []\n",
        "    depends_on: []\n    implements: []\n",
    ),
    (
        "    implements:\n    - hyper_update_core_logic\n- id: hyper_update_core_logic\n",
        "    implements: []\n- id: hyper_update_core_logic\n",
    ),
)
INVOICES_NODE = """\
- id: invoices
  dimension: Module
  status: clean
  associated_file: spec/compiled/MiniPRD_01_Invoices.md
  description: Invoices module of the demo billing service.
  inputs: []
  outputs: []
  edges:
    depends_on: []
    implements: []
"""


def _install(project: Path) -> Path:
    """Add the v0.6.0 scripts, schemas and install templates to a built project and commit."""
    specs.install_scripts(project)
    shutil.copytree(
        REPO_ROOT / ".agents" / "install-templates",
        project / ".agents" / "install-templates",
        dirs_exist_ok=True,
    )
    specs.commit(project, "fixture: v0.6.0 scripts and install templates")
    return project


def build(tmp_path: Path, variant: str = "default", clean_ledger: bool = False) -> Path:
    """Return a fixture project (optionally the clean_ledger variant) with the v0.6.0 files."""
    project = specs.builder.build(tmp_path / "project", variant)
    if clean_ledger:
        ledger = project / LEDGER
        text = ledger.read_text(encoding="utf-8")
        for old, new in LEDGER_FIXES:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        ledger.write_text(text + INVOICES_NODE, encoding="utf-8")
    return _install(project)


def apply_yes(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `apply --yes` (no TTY) and fail the test unless it exits 0."""
    proc = specs.migrate(project, "apply", "--yes", *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def run_tty(
    project: Path, args: list[str], answers: dict[str, str], timeout: float = 600
) -> tuple[int, str]:
    """Run the project's hyper_migrate.py on a pseudo-terminal; answer each prompt it shows.

    `answers` maps a prompt substring to the line typed after it. stdin, stdout and the
    controlling terminal (/dev/tty, which hyper_blocks opens) are all the pty.
    """
    argv = [sys.executable, str(project / ".agents/scripts/hyper_migrate.py"), *args]
    env = specs._env()
    pid, fd = pty.fork()
    if pid == 0:  # the child: run the migration in the project
        try:
            os.chdir(project)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)
    output, seen = "", 0
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break  # the child closed the pty
                if not chunk:
                    break
                output += chunk.decode("utf-8", errors="replace")
            hits = [(output.find(key, seen), key) for key in answers if output.find(key, seen) >= 0]
            if hits:
                at, key = min(hits)
                os.write(fd, (answers[key] + "\n").encode())
                seen = at + len(key)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)  # a pty.fork child leads its session: pgid == pid
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
            raise AssertionError(f"timed out; output so far:\n{output}")
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), output


def graph(project: Path) -> dict:
    """Return the parsed lore graph of the project."""
    return yaml.safe_load((project / "lore_hypergraph.yml").read_text(encoding="utf-8"))


def adr_nodes(project: Path) -> dict[str, dict]:
    """Return the imported ADR nodes keyed by their legacy source path."""
    return {
        node["legacy"]["source"]: node
        for node in graph(project)["nodes"]
        if node.get("type") == "decision" and node.get("legacy")
    }


def files_under(project: Path, rel: str) -> dict[str, bytes]:
    """Return {path: bytes} of every file under project/rel."""
    root = project / rel
    return {
        p.relative_to(project).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def journal_step(project: Path, step_id: str) -> dict:
    """Return the journal's done step with this id."""
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    return next(s for s in journal["steps"] if s["id"] == step_id and s["status"] == "done")


# ---------------------------------------------------------------------------
# Test 1: memory fold
# ---------------------------------------------------------------------------


def test_memory_fold(tmp_path: Path) -> None:
    """Test 1: productContext -> SPEC.md block, systemPatterns -> PROPOSED canon; templates go.

    The fixture fills all three v0.5.9 memory templates, so the template-identical third file is
    `techContext.md` holding the v0.5.9 productContext template's bytes (the rule is by sha).
    """
    project = specs.builder.build(tmp_path / "project", "default")
    template = subprocess.run(
        ["git", "show", f"{V059_COMMIT}:.agents/memory/productContext.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    (project / ".agents/memory/techContext.md").write_bytes(template)
    specs.commit(project, "fixture: a template-identical memory file")
    project = _install(project)
    before = specs.content(specs.tree(project))
    product = (project / ".agents/memory/productContext.md").read_text(encoding="utf-8")
    patterns = (project / ".agents/memory/systemPatterns.md").read_text(encoding="utf-8")
    apply_yes(project)

    spec = (project / "SPEC.md").read_text(encoding="utf-8")
    section = spec.split("\n## Product Context\n", 1)[1]
    assert PRODUCT_BEGIN in section and PRODUCT_END in section
    body = section.split(PRODUCT_BEGIN + "\n", 1)[1].split(PRODUCT_END, 1)[0]
    assert body == product  # the original text, verbatim
    canon = [n for n in graph(project)["nodes"] if n["type"] == "canon"]
    assert len(canon) == 1 and canon[0]["status"] == "PROPOSED"
    pointer = canon[0]["pointer"]["path"]
    assert pointer.startswith("lore/proposed/")
    assert (project / pointer).read_text(encoding="utf-8") == patterns
    assert not list((project / "lore/canon").glob("*.md"))  # nothing reached the canon
    assert not (project / ".agents/memory/techContext.md").exists()
    assert (project / ".agents/memory/activeContext.md").is_file()
    lint = specs.tool(project, "hyper_spec.py", "lint", "--all")
    assert lint.returncode == 0, lint.stdout + lint.stderr

    proc = specs.migrate(project, "rollback", "--journal", JOURNAL)  # Task 19
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert specs.content(specs.tree(project)) == before


# ---------------------------------------------------------------------------
# Tests 2-5: ADR scan, selection, import, stubs
# ---------------------------------------------------------------------------


def test_multi_context_adr_import(tmp_path: Path) -> None:
    """Test 2: interactive yes for both dirs; the second imports with --context services/billing."""
    project = build(tmp_path)
    before = specs.content(specs.tree(project))
    answers = {
        "Type yes to migrate this project": "yes",
        f"Write this repair to {LEDGER}?": "y",
        "Import the ADRs of docs/adr into lore?": "y",
        "Import the ADRs of services/billing/docs/adr (context services/billing) into lore?": "y",
        "Replace or sidecar?": "b",
    }
    code, output = run_tty(project, ["apply"], answers)
    assert code == 0, output
    nodes = adr_nodes(project)
    assert sorted(nodes) == [
        "docs/adr/0001-use-postgres.md",
        "docs/adr/0002-monthly-billing.md",
        "services/billing/docs/adr/0001-stripe-adapter.md",
    ]
    assert {n["status"] for n in nodes.values()} == {"PROPOSED"}
    assert nodes["docs/adr/0001-use-postgres.md"]["legacy"]["status"] == "accepted"
    assert nodes["docs/adr/0002-monthly-billing.md"]["legacy"]["status"] == "proposed"
    billing = nodes["services/billing/docs/adr/0001-stripe-adapter.md"]
    assert billing["legacy"]["status"] == "accepted" and billing["context"] == "services/billing"
    assert nodes["docs/adr/0001-use-postgres.md"]["context"] is None
    step = journal_step(project, "adr-import:services/billing/docs/adr")
    assert step["inputs"]["argv"] == [
        "hyper_lore.py",
        "import",
        "--adr-dirs",
        "services/billing/docs/adr",
        "--context",
        "services/billing",
    ]
    assert journal_step(project, "adr-import:docs/adr")["inputs"]["argv"][-1] == "docs/adr"
    assert not list((project / "lore/canon").glob("*.md"))  # imports stay PROPOSED (D7)

    proc = specs.migrate(project, "rollback", "--journal", JOURNAL)  # Task 19: stubs restored
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert specs.content(specs.tree(project)) == before


def _gitlink_and_nested(tmp_path: Path, project: Path) -> None:
    """Add a submodule gitlink, a nested repository and an untracked tree, each with docs/adr."""
    adr = specs.builder.ADR.format(number=1, title="Vendored choice", day=4, status="Accepted")
    sub = project / "libs/sub"
    specs.write(sub, "docs/adr/0001-vendored-choice.md", adr)
    specs.git(sub, "init", "-q", "-b", "main")
    sha = specs.commit(sub, "the submodule's own history")
    specs.git(project, "update-index", "--add", "--cacheinfo", f"160000,{sha},libs/sub")
    specs.git(project, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "a submodule")
    nested = project / "tools/nested"
    specs.write(nested, "docs/adr/0001-nested-choice.md", adr)
    specs.git(nested, "init", "-q", "-b", "main")
    specs.write(project, "scratch/docs/adr/0001-untracked-choice.md", adr)  # never committed


def test_adr_scan_skips_gitlinks_and_nested_repos(tmp_path: Path) -> None:
    """Test 3: a gitlink, a nested repository and an untracked tree are reported, not imported."""
    project = build(tmp_path)
    _gitlink_and_nested(tmp_path, project)
    skipped_dirs = ("libs/sub/docs/adr", "tools/nested/docs/adr", "scratch/docs/adr")
    before = {d: files_under(project, d) for d in skipped_dirs}
    plan = specs.plan_json(project)
    scan = next(a for a in plan["actions"] if a["op"] == "adr-scan")
    reasons = {s["dir"]: s["reason"] for s in scan["skipped"]}
    assert "submodule libs/sub" in reasons["libs/sub/docs/adr"]
    assert "nested repository tools/nested" in reasons["tools/nested/docs/adr"]
    assert "untracked" in reasons["scratch/docs/adr"]
    offered = [a["target"] for a in plan["actions"] if a["op"] == "adr-import"]
    assert offered == ["docs/adr", "services/billing/docs/adr"]  # none of the three is offered
    proc = apply_yes(project)
    for folder in skipped_dirs:
        assert f"report: {folder}: not imported ({reasons[folder]})" in proc.stdout
        assert files_under(project, folder) == before[folder]
    assert not [s for s in adr_nodes(project) if s.startswith(skipped_dirs)]


def test_adr_noninteractive_root_only(tmp_path: Path) -> None:
    """Test 4: `apply --yes` without a TTY imports only the root docs/adr."""
    project = build(tmp_path)
    billing = files_under(project, "services/billing/docs/adr")
    proc = apply_yes(project)
    assert sorted(adr_nodes(project)) == [
        "docs/adr/0001-use-postgres.md",
        "docs/adr/0002-monthly-billing.md",
    ]
    assert files_under(project, "services/billing/docs/adr") == billing  # byte-identical
    assert "services/billing/docs/adr: not imported (a non-interactive run" in proc.stdout


def test_adr_redirect_stub_per_file(tmp_path: Path) -> None:
    """Test 5: each moved file leaves a stub naming its new path; a failed file keeps its bytes."""
    project = specs.builder.build(tmp_path / "project", "default")
    bad = b"# 3. Latin-1 notes\n\nStatus: Accepted\n\nCaf\xe9 au lait.\n"  # not UTF-8
    (project / "docs/adr/0003-latin1-notes.md").write_bytes(bad)
    specs.commit(project, "fixture: an ADR that cannot be imported")
    project = _install(project)
    originals = files_under(project, "docs/adr")
    proc = apply_yes(project)
    nodes = adr_nodes(project)
    assert sorted(nodes) == ["docs/adr/0001-use-postgres.md", "docs/adr/0002-monthly-billing.md"]
    for old, node in nodes.items():
        new = node["pointer"]["path"]
        assert new.startswith("lore/ADRs/ADR-") and (project / new).read_bytes() == originals[old]
        stub = (project / old).read_text(encoding="utf-8")
        assert stub.startswith(f"{REDIRECT_MARK}{new} -->\n")
        assert f"[{new}](../../{new})" in stub and "hyper_migrate.py" in stub
        assert "adr.tar.gz" in stub
    assert (project / "docs/adr/0003-latin1-notes.md").read_bytes() == bad  # left, no stub
    assert "docs/adr/0003-latin1-notes.md" in proc.stdout  # reported
    step = journal_step(project, "adr-import:docs/adr")
    assert [s["path"] for s in step["inputs"]["stubs"]] == sorted(nodes)
    assert (
        step["inputs"]["left_in_place"]
        and "0003-latin1-notes.md" in step["inputs"]["left_in_place"][0]
    )


# ---------------------------------------------------------------------------
# Tests 6 and 7: instruction files, .gitattributes
# ---------------------------------------------------------------------------


def test_migrate_customized_agents_md_sidecar(tmp_path: Path) -> None:
    """Test 6: an unknown-sha AGENTS.md is byte-identical after `apply --yes`; a sidecar exists."""
    project = specs.builder.build(tmp_path / "project", "custom-instructions")
    specs.write(project, "CLAUDE.local.md", "# My local notes\n\nNever shared.\n")
    specs.commit(project, "fixture: CLAUDE.local.md")
    project = _install(project)
    agents = (project / "AGENTS.md").read_bytes()
    local = (project / "CLAUDE.local.md").read_bytes()
    proc = apply_yes(project)
    assert (project / "AGENTS.md").read_bytes() == agents
    sidecar = (project / "AGENTS.hacf-v0.6.0.md").read_text(encoding="utf-8")
    assert "<!-- BEGIN HACF MANAGED BLOCK: hacf -->" in sidecar
    assert "report: AGENTS.md: customized, so it was left unchanged" in proc.stdout
    assert (project / "CLAUDE.local.md").read_bytes() == local
    assert not (project / "AGENTS.md.v059.bak").exists()


def test_migrate_never_writes_gitattributes(tmp_path: Path) -> None:
    """Test 7: a user .gitattributes (Git LFS) is kept byte-identical; drivers: info/attributes."""
    project = build(tmp_path / "with")
    attributes = (project / ".gitattributes").read_bytes()
    assert b"filter=lfs" in attributes
    apply_yes(project)
    assert (project / ".gitattributes").read_bytes() == attributes
    common = Path(specs.git(project, "rev-parse", "--git-common-dir").strip())
    info = (common if common.is_absolute() else project / common) / "info" / "attributes"
    assert "# BEGIN HACF MANAGED BLOCK: merge-drivers" in info.read_text(encoding="utf-8")
    assert journal_step(project, "drivers-check")["inputs"]["present"] is True

    bare = build(tmp_path / "without")
    specs.git(bare, "rm", "-q", ".gitattributes")
    specs.commit(bare, "fixture: no .gitattributes")
    apply_yes(bare)
    assert not (bare / ".gitattributes").exists()


# ---------------------------------------------------------------------------
# Tests 8 and 9: ledger, legacy notes
# ---------------------------------------------------------------------------


def test_broken_ledger_repaired(tmp_path: Path) -> None:
    """Test 8: the ` ###` ledger parses, is v2, validates; the repair diff is description-only."""
    project = build(tmp_path, clean_ledger=True)
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load((project / LEDGER).read_text(encoding="utf-8"))
    apply_yes(project)
    data = yaml.safe_load((project / LEDGER).read_text(encoding="utf-8"))
    assert data["schema"] == "hacf.ledger/v2"
    validate = specs.tool(project, "hyper_ledger.py", "validate")
    assert validate.returncode == 0, validate.stdout + validate.stderr

    diff = journal_step(project, f"ledger-repair:{LEDGER}")["inputs"]["diff"]
    changed = [
        line
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert changed
    for line in changed:  # a removed description line, its folded header, or its text
        assert line.startswith(("-  description: ", "+  description: >-", "+    ")), line

    spec_id = specs.front(project / SLICE_01 / "SPEC.md")["id"]
    node = next(n for n in data["nodes"] if n["id"] == "invoices")
    assert node["associated_file"] == f"{SLICE_01}/SPEC.md"
    assert spec_id in node["spec_refs"]
    step = journal_step(project, f"ledger-spec-refs:{LEDGER}")
    assert step["inputs"]["commands"] == [
        [
            "hyper_ledger.py", "ensure-node", "--id", "invoices", "--dimension", "Module",
            "--associated-file", f"{SLICE_01}/SPEC.md", "--spec-refs", spec_id,
        ]
    ]  # fmt: skip


def test_legacy_notes_moved(tmp_path: Path) -> None:
    """Test 9: a mapped legacy note moves to its mission NOTES.md; an unmapped one stays."""
    project = specs.builder.build(tmp_path / "project", "default")
    moved = (
        "- 2026-03-04 [field-note] (agent:hyper-execute, "
        "slice spec/compiled/MiniPRD_01_Invoices.md) "
        "Invoices need idempotent retries. anchors: invoices"
    )
    orphan = (
        "- 2026-03-05 [gotcha] (agent:hyper-audit, slice spec/compiled/MiniPRD_99_Gone.md)"
        " An orphan note. anchors: none"
    )
    active = project / ".agents/memory/activeContext.md"
    with open(active, "a", encoding="utf-8") as handle:
        handle.write(f"\n## Field Notes (legacy)\n\n{moved}\n{orphan}\n")
    specs.commit(project, "fixture: legacy field notes")
    project = _install(project)
    proc = apply_yes(project)
    spec_id = specs.front(project / SLICE_01 / "SPEC.md")["id"]
    notes = (project / "missions/legacy-unassigned/NOTES.md").read_text(encoding="utf-8")
    expected = (
        f"- 2026-03-04 [field-note] (agent:hyper-execute, slice {spec_id}) "
        "Invoices need idempotent retries. anchors: invoices"
    )
    assert expected in notes.splitlines()
    text = active.read_text(encoding="utf-8")
    assert moved not in text and orphan in text.splitlines()
    assert "no migrated slice for spec/compiled/MiniPRD_99_Gone.md" in proc.stdout


# ---------------------------------------------------------------------------
# Test 10: the full fixture, verify, then the engine's preflight
# ---------------------------------------------------------------------------


def test_full_fixture_migration_then_engine_dry_run(tmp_path: Path) -> None:
    """Test 10: apply --yes --include-archived, verify exits 0, the engine preflight passes.

    Question 3: `hyper_engine.py preflight` with the stub executor (HACF_TEST=1, R41) is the
    engine's dry run; it runs the lint, ledger-validate and DAG checks and starts no executor.
    The sandbox checks are skipped with --allow-unsandboxed(-oracle): the stub runs no agent and
    no oracle, and the host need not have socat or bwrap.
    """
    project = build(tmp_path, clean_ledger=True)
    apply_yes(project, "--include-archived")
    proc = specs.migrate(project, "verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for check in ("hyper_spec.py lint --all", "hyper_ledger.py validate", "hyper_lore.py validate"):
        assert f"ok: {check}" in proc.stdout
    assert "ok: hyper_firewall.py check" in proc.stdout
    assert "skipped: rig scope audit" in proc.stdout  # no rig on PATH
    journal = json.loads((project / JOURNAL).read_text(encoding="utf-8"))
    assert journal["verify"]["ok"] is True

    specs.commit(project, "migrated to v0.6.0")  # the engine needs a clean main checkout
    engine = subprocess.run(
        [
            sys.executable, str(project / ".agents/scripts/hyper_engine.py"), "preflight",
            "--executor", "stub", "--unverified-executor",
            "--allow-unsandboxed", "--allow-unsandboxed-oracle",
        ],
        cwd=project, env=specs._env(HACF_TEST="1"), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert engine.returncode == 0, engine.stdout + engine.stderr
    report = json.loads(engine.stdout)
    assert report["ok"] is True
    names = {check["name"] for check in report["checks"]}
    assert {"executor", "lint", "ledger_validate"} <= names
    assert all(check["ok"] for check in report["checks"])
