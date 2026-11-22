#!/usr/bin/env python3
"""Build a realistic v0.5.9 HACF user project, and its edge-case variants, for S12b-S13 and the E2E.

    python3 dev/tests/fixtures/build_user_project_v059.py <dest> [--variant V]

or, from a test, `build(dest, variant="default") -> Path`. It writes only under `dest` (which
must be missing or empty), uses no network, and takes the v0.5.9 framework files from this
repository's history with `git archive 984ca46` (argv only, never a shell).

Variants:
- default: `git init`, a v0.5.9 install (`.agents/`, `.claude/commands/`, the saved
  `HACF-uninstall.sh`, root AGENTS.md, CLAUDE.md and GEMINI.md from
  `.agents/install-templates/`), flat specs (`spec/compiled/SuperPRD_demo.md` with an Execution
  Checklist plus two `MiniPRD_*.md`), an archived `spec/archive/MiniPRD_00_Setup_AUDITED.md`, the
  broken ledger (S00's `ledger_v1_real_broken.yml` as `spec/compiled/architecture.yml`),
  `docs/adr/` plus a second context's `services/billing/docs/adr/`, filled productContext.md,
  systemPatterns.md and activeContext.md, an AGENTS.md carrying an
  `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` block, a custom `.claude/settings.json`, user
  `pyproject.toml`, `uv.lock` and `LICENSE`, and a user `.gitattributes` with Git LFS rules. Every
  file is committed once.
- custom-instructions: the default, with whole-file AGENTS.md and CLAUDE.md the user edited
  (unknown sha, no markers; R9).
- collision-spec, collision-project-yaml, collision-workspace-yaml, collision-missions: the
  default plus one foreign root item each.
- openrig-only-skills: no HACF at all; an OpenRig-projected `.agents/skills/<id>/` only.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent
V059_COMMIT = "984ca46"
ARCHIVE_PATHS = (".agents", ".claude/commands", "HACF-uninstall.sh")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
VARIANTS = (
    "default",
    "custom-instructions",
    "collision-spec",
    "collision-project-yaml",
    "collision-workspace-yaml",
    "collision-missions",
    "openrig-only-skills",
)
OPENRIG_SKILL_ID = "openrig-review"

# The item each variant is recognized by (test_fixture_builder_variants).
DISTINGUISHING = {
    "default": "AGENTS.md",
    "custom-instructions": "CLAUDE.md",
    "collision-spec": "SPEC.md",
    "collision-project-yaml": "project.yaml",
    "collision-workspace-yaml": "workspace.yaml",
    "collision-missions": "missions/notes.txt",
    "openrig-only-skills": f".agents/skills/{OPENRIG_SKILL_ID}/SKILL.md",
}

OPENRIG_BLOCK = (
    "\n<!-- BEGIN OpenRig MANAGED BLOCK: role -->\n"
    "You are seat build-execute in rig demo. Run `rig whoami --json` first.\n"
    "<!-- END OpenRig MANAGED BLOCK: role -->\n"
)
CUSTOM_LINE = "\n## Team rules\n\n- Our own rule: run `make lint` before every commit.\n"

SUPERPRD = """\
# SuperPRD: Demo billing service

## 1. Intent

Bill customers monthly.

## Execution Checklist

- [x] MiniPRD_00_Setup
- [ ] MiniPRD_01_Invoices
- [ ] MiniPRD_02_Payments
"""
MINIPRD = """\
# MiniPRD: {title}

**Hypergraph Node ID:** {node}
**Parent Node:** billing

## 1. The Confidence Mandate

Confidence 9/10.

## 3. Implementation Plan (Task List)

- [ ] Task 1: build {title}.

## 4. The Negative Space (Constraints)

- **DO NOT** touch other modules.
"""
ADR = """\
# {number}. {title}

Date: 2026-03-0{day}

## Status

{status}

## Context

{title} needed a decision.

## Decision

We chose the simple option.
"""
PRODUCT_CONTEXT = "# Product Context\n\nA billing service for small shops. Users: shop owners.\n"
SYSTEM_PATTERNS = "# System Patterns\n\n- Hexagonal layout; adapters under `billing/adapters/`.\n"
ACTIVE_CONTEXT = (
    "# Active Context\n\n## Current focus\n\nMiniPRD_01_Invoices is in flight.\n\n"
    "## Recent changes\n\n- MiniPRD_00_Setup audited.\n"
)
SETTINGS = (
    '{\n  "permissions": {\n    "allow": ["Bash(make lint)"],\n'
    '    "deny": ["Read(./secrets/**)"]\n  },\n  "model": "sonnet"\n}\n'
)
PYPROJECT = '[project]\nname = "demo-billing"\nversion = "1.2.0"\nrequires-python = ">=3.10"\n'
UV_LOCK = 'version = 1\nrequires-python = ">=3.10"\n\n[[package]]\nname = "demo-billing"\n'
LICENSE = "MIT License\n\nCopyright (c) 2026 Demo Shop\n"
GITATTRIBUTES = (
    "*.psd filter=lfs diff=lfs merge=lfs -text\n*.zip filter=lfs diff=lfs merge=lfs -text\n"
)
OPENRIG_SKILL = (
    "---\nname: openrig-review\ndescription: Projected by OpenRig; not an HACF skill.\n---\n\n"
    "# Review\n\nReview the diff.\n"
)
COLLISIONS = {
    "collision-spec": ("SPEC.md", "# Our product spec\n\nNo frontmatter; written by the team.\n"),
    "collision-project-yaml": ("project.yaml", "name: demo-billing\nowner: platform-team\n"),
    "collision-workspace-yaml": ("workspace.yaml", "workspaces:\n  - billing\n"),
    "collision-missions": ("missions/notes.txt", "Our own missions list, not OpenRig's.\n"),
}


class BuildError(Exception):
    """The fixture could not be built."""


def _git_env() -> dict[str, str]:
    """Return an environment with no outer git state, no user config and a fixed identity."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Demo User",
        GIT_AUTHOR_EMAIL="user@example.invalid",
        GIT_AUTHOR_DATE="2026-03-01T12:00:00+00:00",
        GIT_COMMITTER_NAME="Demo User",
        GIT_COMMITTER_EMAIL="user@example.invalid",
        GIT_COMMITTER_DATE="2026-03-01T12:00:00+00:00",
    )
    return env


def _git(args: list[str], cwd: Path, text: bool = True) -> subprocess.CompletedProcess:
    """Run git with an argv list and raise BuildError on failure."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=_git_env(),
        capture_output=True,
        text=text,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise BuildError(f"git {' '.join(args[:2])} failed: {err.strip()}")
    return proc


def _write(dest: Path, rel: str, text: str) -> None:
    """Write text to dest/rel, creating parent directories."""
    path = dest / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_member(member: tarfile.TarInfo) -> bool:
    """Return True for a regular file or directory whose path stays inside the destination."""
    parts = PurePosixPath(member.name).parts
    return (
        (member.isfile() or member.isdir())
        and not PurePosixPath(member.name).is_absolute()
        and ".." not in parts
    )


def extract_v059(dest: Path) -> None:
    """Extract the v0.5.9 framework files from `git archive 984ca46` into dest."""
    proc = _git(
        ["archive", "--format=tar", V059_COMMIT, "--", *ARCHIVE_PATHS], REPO_ROOT, text=False
    )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if not _safe_member(member):
                raise BuildError(f"unexpected archive member {member.name!r}")
            target = dest / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            assert source is not None
            target.write_bytes(source.read())
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    templates = dest / ".agents" / "install-templates"
    for name in INSTRUCTION_FILES:
        shutil.copyfile(templates / name, dest / name)


def _base_content(dest: Path) -> None:
    """Add the base variant's user content (Tasks 22 and 23)."""
    _write(dest, "spec/compiled/SuperPRD_demo.md", SUPERPRD)
    _write(
        dest,
        "spec/compiled/MiniPRD_01_Invoices.md",
        MINIPRD.format(title="Invoices", node="invoices"),
    )
    _write(
        dest,
        "spec/compiled/MiniPRD_02_Payments.md",
        MINIPRD.format(title="Payments", node="payments"),
    )
    _write(
        dest,
        "spec/archive/MiniPRD_00_Setup_AUDITED.md",
        MINIPRD.format(title="Setup", node="setup"),
    )
    ledger = dest / "spec" / "compiled" / "architecture.yml"
    shutil.copyfile(FIXTURES / "ledger_v1_real_broken.yml", ledger)
    _write(
        dest,
        "docs/adr/0001-use-postgres.md",
        ADR.format(number=1, title="Use Postgres", day=1, status="Accepted"),
    )
    _write(
        dest,
        "docs/adr/0002-monthly-billing.md",
        ADR.format(number=2, title="Monthly billing", day=2, status="Proposed"),
    )
    _write(
        dest,
        "services/billing/docs/adr/0001-stripe-adapter.md",
        ADR.format(number=1, title="Stripe adapter", day=3, status="Accepted"),
    )
    _write(dest, ".agents/memory/productContext.md", PRODUCT_CONTEXT)
    _write(dest, ".agents/memory/systemPatterns.md", SYSTEM_PATTERNS)
    _write(dest, ".agents/memory/activeContext.md", ACTIVE_CONTEXT)
    agents = dest / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + OPENRIG_BLOCK.encode())
    _write(dest, ".claude/settings.json", SETTINGS)
    _write(dest, "pyproject.toml", PYPROJECT)
    _write(dest, "uv.lock", UV_LOCK)
    _write(dest, "LICENSE", LICENSE)
    _write(dest, ".gitattributes", GITATTRIBUTES)


def _customize(dest: Path) -> None:
    """Make AGENTS.md and CLAUDE.md whole files the user edited: no markers, unknown sha."""
    templates = dest / ".agents" / "install-templates"
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (templates / name).read_text(encoding="utf-8")
        (dest / name).write_text(text + CUSTOM_LINE, encoding="utf-8")


def build(dest: Path | str, variant: str = "default") -> Path:
    """Build the fixture project `variant` at dest (missing or empty) and return its path."""
    if variant not in VARIANTS:
        raise BuildError(f"unknown variant {variant!r}; choose one of {', '.join(VARIANTS)}")
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise BuildError(f"{dest} exists and is not an empty directory")
    dest.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], dest)
    if variant == "openrig-only-skills":
        _write(dest, f".agents/skills/{OPENRIG_SKILL_ID}/SKILL.md", OPENRIG_SKILL)
        _write(dest, "README.md", "# Demo billing\n")
    else:
        extract_v059(dest)
        _base_content(dest)
        if variant == "custom-instructions":
            _customize(dest)
        if variant in COLLISIONS:
            rel, text = COLLISIONS[variant]
            _write(dest, rel, text)
    _git(["add", "--", "."], dest)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", f"fixture: {variant}"], dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    """Build one fixture from the command line; exit 2 on a usage or build error."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest")
    parser.add_argument("--variant", default="default", choices=VARIANTS)
    ns = parser.parse_args(argv)
    try:
        print(build(ns.dest, ns.variant))
    except BuildError as exc:
        print(f"build_user_project_v059.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
