#!/usr/bin/env python3
"""hyper_update_core.py — Core logic for /hyper-update (v0.6.0, S13c).

    python3 .agents/scripts/hyper_update_core.py [--dry-run] [--upstream-url URL] [--ref REF]
        [--allow-unsigned] [--allow-downgrade] [-y]

Phases:
- Config: `.agents/config/upstream.yml` (repo_url, ref); missing, the embedded defaults are used
  and "run HACF-install.sh to complete the upgrade" is printed (R24.10).
- Upstream: the URL and ref change only through --upstream-url and --ref; HACF_UPSTREAM_URL is
  read only when HACF_TEST=1, and a URL that is not https:// is refused unless HACF_TEST=1 (R12).
  The fetch is `git clone --filter=blob:none --branch <ref> <url>`, so ancestry can be tested.
- Integrity (HACF's update-integrity policy): anti-rollback against the manifest's
  installed_commit (R25.5); signers pinned machine-locally in
  `.agents/state/upstream-trust.json` (TOFU; the payload's upstream.yml is only a proposal,
  R12); pinned signers require a valid SSH signature (R11);
  no pinned signers needs interactive consent or --allow-unsigned in a TTY, never under
  HACF_AUTONOMOUS=1, logged to `.agents/logs/update-consent.jsonl` (R25.6).
- Sync: per-file, manifest-driven, over .agents/skills/, .agents/scripts/ and .agents/schemas/
  only (spec/ and tests/ are never touched); locally modified files are backed up to
  `.agents/.backup/<ts>/` first; files upstream does not ship are kept unless the manifest owns
  them unmodified; NEVER_COPY paths are refused. The manifest is then updated atomically.
- Blocks: `.agentignore` gets only its `agentignore` block; AGENTS.md, CLAUDE.md and GEMINI.md
  get their `hacf` block, or S12a's R9 decision for a whole file (never appended to). Both go
  through `hyper_blocks.py`. CLAUDE.local.md is never touched.
- Hand-off: `python3 .agents/scripts/hyper_migrate.py plan` is run and printed; never `apply`.

--dry-run runs fetch, ancestry, signature status, the sync plan and the migration plan and
writes nothing (no backup, pin, log line, lock or manifest). It never executes upstream code:
the migration plan comes from the project's installed hyper_migrate.py, not the clone's.

Exit codes: 0 done (or dry run), 1 refused or failed, 2 usage. The last thing on stdout is the
JSON report.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import _runtime

_runtime.require_runtime()  # R19.4: before PyYAML or any sibling module is imported

import hyper_blocks  # noqa: E402
import yaml  # noqa: E402
from hyper_update_merge_engine import MergeEngine  # noqa: E402
from hyper_update_verification import (  # noqa: E402
    CONSENT_FLAG,
    CONSENT_INTERACTIVE,
    SSH_VERIFIED,
    UpstreamVerification,
    atomic_write,
    backup_stamp,
)

CONFIG_REL = ".agents/config/upstream.yml"
MANIFEST_REL = ".agents/hacf-manifest.json"
MIGRATE_REL = ".agents/scripts/hyper_migrate.py"
BLOCKS_REL = ".agents/scripts/hyper_blocks.py"
INSTALL_NOTICE = "run HACF-install.sh to complete the upgrade"

# Embedded defaults: the shipped upstream.yml (Task 2, R24.10).
DEFAULT_CONFIG = {
    "repo_url": "https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git",
    "ref": "main",
    "verification": {
        "allowed_signers": [],
        "gpg_fingerprints": [],
        "require_consent_if_unsigned": True,
    },
}

# NEVER_COPY (target layout in HACF's design notes), as HACF-install.sh lists it. A
# trailing / means the directory and everything under it.
NEVER_COPY = (
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    ".gitattributes",
    "lore/",
    "missions/",
    "exhaust/",
    ".openrig/",
    "spec/",
    "tests/",
    "dev/",
    "PRDs/",
    "docs/",
    ".agents/local/",
    ".agents/state/",
    ".agents/.backup/",
    ".agents/logs/",
    ".agents/memory/",
    ".claude/settings*.json",
    ".mcp.json",
    ".cursor/mcp.json",
    ".roo/mcp.json",
    ".gitignore",
    "CHANGELOG.md",
    "README.md",
    "CITATION.cff",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    ".agent",
    "CLAUDE.local.md",
)

# The `.agentignore` block header, as hyper_migrate.py writes it (S13b), so both agree.
AGENTIGNORE_HEADER = (
    "# HACF: historical drafts and unverified outputs agents must not read (hyper_firewall.py)."
)
AGENTIGNORE_SPLIT = "# Standard dev ignores"


def never_copy(rel: str) -> bool:
    """Return True for a path HACF never copies into a project (Task 15)."""
    rel = rel.replace(os.sep, "/").lstrip("/")
    if Path(rel).name == "CLAUDE.local.md":
        return True
    for pattern in NEVER_COPY:
        if pattern.endswith("/"):
            if rel == pattern[:-1] or rel.startswith(pattern):
                return True
        elif fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def sha_file(path: Path) -> str:
    """Return the sha256 hex of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_files(base: Path, top: str) -> List[str]:
    """Return the regular files under base/top as base-relative POSIX paths (no bytecode)."""
    root = base / top
    found: List[str] = []
    if not root.is_dir() or root.is_symlink():
        return found
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs if d != "__pycache__" and not os.path.islink(os.path.join(current, d))
        )
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            found.append(os.path.relpath(path, base).replace(os.sep, "/"))
    return found


class UpdateRefused(Exception):
    """The update stops here; nothing past this point was written."""

    def __init__(self, message: str, status: str = "refused"):
        super().__init__(message)
        self.status = status


class HyperUpdateCore:
    """Core logic orchestrator for framework upgrades."""

    LOCK_FILE = ".agents/.hyper-update.lock"

    # Framework dirs the per-file sync covers (Task 13: spec/ and tests/ are gone; question 4:
    # .agents/config/, rules and install-templates stay with HACF-install.sh).
    AUTO_UPDATE_DIRS = [
        ".agents/skills/",
        ".agents/scripts/",
        ".agents/schemas/",
    ]

    # Sensitive files: only their HACF block changes (Task 18).
    SENSITIVE_FILES = [
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
    ]

    # Maps local destination filename -> upstream source path (the block body template).
    SENSITIVE_FILE_UPSTREAM_SOURCES = {
        "CLAUDE.md": ".agents/install-templates/CLAUDE.md",
        "AGENTS.md": ".agents/install-templates/AGENTS.md",
        "GEMINI.md": ".agents/install-templates/GEMINI.md",
    }
    SENSITIVE_DIRS = [
        ".claude/commands/",
        ".clinerules/",
        ".roo/rules/",
        ".roo/rules-code/",
        ".cursor/rules/",
        ".windsurf/rules/",
    ]

    def __init__(
        self,
        project_root: str = ".",
        upstream_url: Optional[str] = None,
        ref: Optional[str] = None,
        allow_unsigned: bool = False,
        allow_downgrade: bool = False,
        dry_run: bool = False,
        assume_yes: bool = False,
    ):
        self.project_root = Path(project_root).resolve()
        self.flag_url = upstream_url
        self.flag_ref = ref
        self.allow_unsigned = allow_unsigned
        self.allow_downgrade = allow_downgrade
        self.dry_run = dry_run
        self.assume_yes = assume_yes
        self.test_mode = os.environ.get("HACF_TEST") == "1"
        self.autonomous = os.environ.get("HACF_AUTONOMOUS") == "1"
        self.tty = sys.stdin.isatty() and sys.stdout.isatty()
        self.can_ask = self.tty and not assume_yes and not self.autonomous and not dry_run
        self.upstream_dir: Optional[Path] = None
        self.url = ""
        self.ref = ""
        self.commit_sha: Optional[str] = None
        self.integrity: Optional[str] = None
        self.backup_root: Optional[Path] = None
        self.lock_held = False
        self.verifier = UpstreamVerification(str(self.project_root))
        self.manifest: Optional[dict] = None
        self.report: Dict = {
            "status": "running",
            "dry_run": dry_run,
            "url": None,
            "ref": None,
            "commit_sha": None,
            "notices": [],
        }

    # ==== Output ====

    def log(self, msg: str):
        """Log a message to stderr."""
        print(f"[hyper-update] {msg}", file=sys.stderr, flush=True)

    def notice(self, msg: str):
        """Log a message and keep it in the report."""
        self.log(msg)
        self.report["notices"].append(msg)

    def ask(self, question: str) -> bool:
        """Ask a yes/no question on the terminal (default No); only called when can_ask."""
        sys.stdout.write(f"{question} [y/N]: ")
        sys.stdout.flush()
        answer = sys.stdin.readline()
        return answer.strip().lower() in ("y", "yes")

    def upstream_label(self) -> str:
        """Return `<url> @ <sha>` for prompts and log lines (R12)."""
        return f"{self.url} @ {self.commit_sha or '(not fetched)'}"

    # ==== Config and upstream resolution (Tasks 1-5) ====

    def load_config(self) -> dict:
        """Return the project's upstream config, or the embedded defaults with the notice."""
        path = self.project_root / CONFIG_REL
        if not path.is_file():
            self.notice(f"MISSING CONFIG: {CONFIG_REL}; using embedded defaults; {INSTALL_NOTICE}")
            self.report["config_source"] = "embedded-defaults"
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise UpdateRefused(f"{CONFIG_REL} is unreadable ({exc}); fix it or remove it")
        if not isinstance(data, dict):
            raise UpdateRefused(f"{CONFIG_REL} is not a mapping")
        self.report["config_source"] = CONFIG_REL
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for key in ("repo_url", "ref"):
            if data.get(key):
                merged[key] = str(data[key])
        if isinstance(data.get("verification"), dict):
            merged["verification"].update(data["verification"])
        return merged

    @staticmethod
    def check_gpg_unsupported(config: dict, where: str):
        """Stop on a non-empty gpg_fingerprints list (question 3: SSH-only in v0.6.0)."""
        verification = config.get("verification") or {}
        if verification.get("gpg_fingerprints"):
            raise UpdateRefused(
                f"{where} lists gpg_fingerprints, which v0.6.0 does not support (HACF signing "
                "is SSH-only, R11); remove them to continue"
            )

    def resolve_upstream(self, config: dict):
        """Set the effective URL and ref (R12): flags, then HACF_UPSTREAM_URL under HACF_TEST=1."""
        url = config["repo_url"]
        env_url = os.environ.get("HACF_UPSTREAM_URL")
        if env_url and self.test_mode:
            url = env_url
        elif env_url:
            self.notice("HACF_UPSTREAM_URL is ignored without HACF_TEST=1")
        if self.flag_url:
            url = self.flag_url
        self.url = url
        self.ref = self.flag_ref or config["ref"]
        self.report["url"] = self.url
        self.report["ref"] = self.ref
        self.log(f"Upstream URL: {self.url} (ref {self.ref})")
        if not self.url.startswith("https://") and not self.test_mode:
            raise UpdateRefused(f"upstream URL {self.url} is not https://; refused")
        if self.ref.startswith("-") or self.url.startswith("-"):
            raise UpdateRefused("the upstream URL and ref must not start with '-'")

    def verify_git_available(self):
        """Verify git is installed."""
        try:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise UpdateRefused("git not found. Install git and retry.")

    def fetch_upstream(self):
        """Clone the upstream (blob-filtered, full history) and record its commit (Task 5)."""
        self.upstream_dir = Path(tempfile.mkdtemp(prefix="hacf-upstream-"))
        clone = self.upstream_dir / "repo"
        argv = [
            "git",
            "clone",
            "-q",
            "--filter=blob:none",
            "--branch",
            self.ref,
            self.url,
            str(clone),
        ]
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise UpdateRefused(
                f"could not fetch {self.url} (ref {self.ref}): {result.stderr.strip()}",
                status="failed",
            )
        self.upstream_dir = clone
        self.commit_sha = self._git_upstream(["rev-parse", "HEAD"]).strip()
        self.report["commit_sha"] = self.commit_sha
        self.log(f"Fetched {self.upstream_label()}")

    def _git_upstream(self, args: List[str]) -> str:
        """Run git in the clone and return stdout (raises UpdateRefused on failure)."""
        result = subprocess.run(
            ["git", *args], cwd=str(self.upstream_dir), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise UpdateRefused(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    # ==== Integrity (Tasks 6-11) ====

    def load_manifest(self) -> Optional[dict]:
        """Return the parsed manifest, or None when there is none."""
        path = self.project_root / MANIFEST_REL
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise UpdateRefused(f"{MANIFEST_REL} is unreadable ({exc}); run HACF-install.sh")
        return data if isinstance(data, dict) else None

    def check_ancestry(self):
        """Refuse an upstream that does not descend from installed_commit (R25.5, Task 6)."""
        installed = (self.manifest or {}).get("installed_commit")
        if not installed:
            self.notice(
                "no installed_commit in the manifest; no ancestry check was possible"
                if self.manifest is not None
                else f"no {MANIFEST_REL}; no ancestry check was possible"
            )
            self.report["ancestry"] = "unchecked"
            return
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", str(installed), self.commit_sha],
            cwd=str(self.upstream_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            self.report["ancestry"] = "descendant"
            self.log(f"Ancestry: {self.commit_sha[:12]} descends from installed {installed[:12]}")
            return
        self.report["ancestry"] = "not-descendant"
        message = (
            f"upstream {self.upstream_label()} does not descend from the installed commit "
            f"{installed} (a downgrade or a rewritten history)"
        )
        if self.allow_downgrade:
            self.notice(f"{message}; proceeding because --allow-downgrade was given")
            return
        raise UpdateRefused(f"{message}; rerun with --allow-downgrade to accept it")

    def payload_config(self) -> dict:
        """Return the payload's upstream.yml (a proposal only, R12)."""
        path = self.upstream_dir / CONFIG_REL
        if not path.is_file():
            return {"verification": {"allowed_signers": []}}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise UpdateRefused(f"the upstream's {CONFIG_REL} is unreadable ({exc})")
        return data if isinstance(data, dict) else {}

    def resolve_signers(self, proposal: List[str]):
        """Return (effective signers, signers to pin or None) from the pin and the proposal."""
        try:
            pin = self.verifier.read_pin()
        except ValueError as exc:
            raise UpdateRefused(str(exc))
        if pin is None:
            self.log(f"No signer pin yet; trusting the upstream proposal on first use: {proposal}")
            self.report["signer_pin"] = "first-use"
            return proposal, proposal
        self.report["signer_pin"] = "pinned"
        if sorted(pin) == sorted(proposal):
            return pin, None
        diff = [f"- {s}" for s in pin if s not in proposal]
        diff += [f"+ {s}" for s in proposal if s not in pin]
        self.log(
            f"The upstream {self.upstream_label()} proposes a different signer set "
            f"(pin {self.verifier.TRUST_FILE}):\n" + "\n".join(diff or ["(order only)"])
        )
        self.report["signer_proposal"] = {"pinned": pin, "proposed": proposal}
        if self.can_ask and self.ask(
            f"Adopt the proposed signer set from {self.upstream_label()}?"
        ):
            self.report["signer_pin"] = "proposal-adopted"
            return proposal, proposal
        self.notice(
            "the upstream's signer proposal was not adopted (it needs interactive consent; "
            "refused under -y, without a TTY or under HACF_AUTONOMOUS=1); the pin stays"
        )
        return pin, None

    def check_integrity(self, signers: List[str]):
        """Signed if pinned, else logged consent (Tasks 9-10); sets self.integrity."""
        if signers:
            ok, message, argv = self.verifier.verify_upstream_commit(
                self.commit_sha, str(self.upstream_dir), signers
            )
            self.report["verification_argv"] = argv
            if not ok:
                self.report["signature"] = "invalid"
                raise UpdateRefused(f"{message}; {self.upstream_label()} is refused")
            self.report["signature"] = SSH_VERIFIED
            self.integrity = SSH_VERIFIED
            self.log(f"✅ {message}")
            return
        self.report["signature"] = "unsigned"
        label = self.upstream_label()
        if self.dry_run:
            self.notice(
                f"{label} is unsigned and no signers are pinned: the update needs your consent "
                "(an interactive prompt, or --allow-unsigned in a terminal)"
            )
            return
        if self.allow_unsigned:
            if self.autonomous:
                raise UpdateRefused("--allow-unsigned is refused under HACF_AUTONOMOUS=1")
            if not self.tty:
                raise UpdateRefused("--allow-unsigned is refused without a TTY")
            self.integrity = CONSENT_FLAG
            self.log(f"Unsigned upstream {label} accepted by --allow-unsigned")
            return
        if not self.can_ask:
            raise UpdateRefused(
                f"{label} is unsigned and no signers are pinned; an update needs interactive "
                "consent in a terminal (not under -y, without a TTY or under HACF_AUTONOMOUS=1)"
            )
        if not self.ask(f"Update from the UNSIGNED upstream {label}?"):
            raise UpdateRefused(f"consent to the unsigned upstream {label} was declined")
        self.integrity = CONSENT_INTERACTIVE

    # ==== Preflight, lock ====

    def check_uncommitted_changes(self) -> List[str]:
        """Return uncommitted paths among the ones this update may change."""
        paths = [
            *self.AUTO_UPDATE_DIRS,
            ".agentignore",
            *self.SENSITIVE_FILES,
            *self.SENSITIVE_DIRS,
        ]
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *paths],
            cwd=str(self.project_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.notice("could not run git status (no git repository?); preflight skipped")
            return []
        return [line[3:] for line in result.stdout.splitlines() if line.strip()]

    def acquire_lock(self):
        """Create the mutex lock file."""
        lock_path = self.project_root / self.LOCK_FILE
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise UpdateRefused(
                f"another update is in progress ({self.LOCK_FILE} exists); wait for it, or "
                "delete the lock if no update is running"
            )
        with os.fdopen(fd, "w") as handle:
            handle.write(f"locked at {_runtime.utc_now_iso()}\n")
        self.lock_held = True

    def release_lock(self):
        """Delete the mutex lock file this run created."""
        if self.lock_held:
            (self.project_root / self.LOCK_FILE).unlink(missing_ok=True)
            self.lock_held = False

    # ==== Sync (Tasks 13-16) ====

    def _backup(self, rel: str) -> Path:
        """Copy a project file into this run's `.agents/.backup/<ts>/` and return the copy."""
        if self.backup_root is None:
            self.backup_root = self.project_root / ".agents/.backup" / backup_stamp()
        target = self.backup_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.project_root / rel, target)
        return target

    def plan_sync(self) -> List[dict]:
        """Return the per-file sync plan over AUTO_UPDATE_DIRS (no writes)."""
        owned = (self.manifest or {}).get("files") or {}
        plan: List[dict] = []
        for top in self.AUTO_UPDATE_DIRS:
            shipped = walk_files(self.upstream_dir, top)
            shipped_set = set(shipped)
            for rel in shipped:
                if never_copy(rel):
                    plan.append({"path": rel, "action": "refused-never-copy"})
                    continue
                local = self.project_root / rel
                new_sha = sha_file(self.upstream_dir / rel)
                if os.path.islink(local) or (os.path.lexists(local) and not local.is_file()):
                    plan.append({"path": rel, "action": "left-not-a-file"})
                    continue
                if not local.exists():
                    plan.append({"path": rel, "action": "install", "sha256": new_sha})
                    continue
                current = sha_file(local)
                if current == new_sha:
                    plan.append({"path": rel, "action": "unchanged", "sha256": new_sha})
                    continue
                entry = owned.get(rel) if isinstance(owned, dict) else None
                clean = isinstance(entry, dict) and entry.get("sha256") == current
                action = "update" if clean else "update-with-backup"
                plan.append({"path": rel, "action": action, "sha256": new_sha})
            for rel in walk_files(self.project_root, top):
                if rel in shipped_set:
                    continue
                entry = owned.get(rel) if isinstance(owned, dict) else None
                if isinstance(entry, dict) and entry.get("sha256") == sha_file(
                    self.project_root / rel
                ):
                    plan.append({"path": rel, "action": "remove-retired"})
                else:
                    plan.append({"path": rel, "action": "keep-unshipped"})
        return plan

    def apply_sync(self, plan: List[dict]) -> List[str]:
        """Apply the sync plan; return the updated manifest file entries' changed paths."""
        touched = []
        for item in plan:
            rel, action = item["path"], item["action"]
            local = self.project_root / rel
            if action in ("install", "update", "update-with-backup"):
                source = self.upstream_dir / rel
                if action == "update-with-backup":
                    copy = self._backup(rel)
                    self.log(f"💾 backed up (locally modified or not in the manifest): {rel}")
                    item["backup"] = str(copy.relative_to(self.project_root))
                mode = os.stat(source).st_mode & 0o777
                atomic_write(local, source.read_bytes())
                os.chmod(local, mode)
                touched.append(rel)
            elif action == "remove-retired":
                local.unlink()
                parent = local.parent
                stop = {self.project_root / top.rstrip("/") for top in self.AUTO_UPDATE_DIRS}
                while parent not in stop and parent != self.project_root:
                    try:
                        parent.rmdir()  # only an empty directory
                    except OSError:
                        break
                    parent = parent.parent
                touched.append(rel)
        return touched

    def write_manifest(self, plan: List[dict], block_shas: Dict[str, Dict[str, str]]):
        """Write files shas, installed_commit and installed_at atomically (Task 16)."""
        manifest = dict(self.manifest or {})
        files = dict(manifest.get("files") or {})
        for item in plan:
            rel, action = item["path"], item["action"]
            if action in ("install", "update", "update-with-backup", "unchanged"):
                previous = files.get(rel) if isinstance(files.get(rel), dict) else {}
                files[rel] = {"sha256": item["sha256"], "owner": previous.get("owner", "hacf")}
            elif action == "remove-retired":
                files.pop(rel, None)
        manifest["files"] = dict(sorted(files.items()))
        blocks = dict(manifest.get("blocks") or {})
        for path, entries in block_shas.items():
            blocks[path] = {**(blocks.get(path) or {}), **entries}
        manifest["blocks"] = blocks
        version = self.upstream_version()
        if version:
            manifest["version"] = version
        manifest["installed_commit"] = self.commit_sha
        manifest["installed_at"] = _runtime.utc_now_iso()
        data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        atomic_write(self.project_root / MANIFEST_REL, data)

    def upstream_version(self) -> Optional[str]:
        """Return HACF_VERSION from the upstream installer, when it has one."""
        path = self.upstream_dir / "HACF-install.sh"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(r'^HACF_VERSION="([^"]+)"', text, re.MULTILINE)
        return match.group(1) if match else None

    # ==== Blocks (Tasks 17-18) ====

    def agentignore_body(self) -> Optional[str]:
        """Return the `agentignore` block body from the upstream's .agentignore, or None."""
        path = self.upstream_dir / ".agentignore"
        if not path.is_file():
            return None
        data = path.read_bytes()
        doc = hyper_blocks.parse_doc(data)
        try:
            block = hyper_blocks.find_block(
                hyper_blocks.scan(doc, hyper_blocks.HASH), hyper_blocks.AGENTIGNORE_BLOCK_ID
            )
        except hyper_blocks.BlocksError:
            block = None
        if block is not None:
            return "\n".join(line.decode() for line in hyper_blocks.body_of(doc, block)) + "\n"
        patterns = []
        for line in data.decode("utf-8").splitlines():
            if line.strip() == AGENTIGNORE_SPLIT:
                break
            if line.strip() and not line.lstrip().startswith("#"):
                patterns.append(line.strip())
        return "\n".join([AGENTIGNORE_HEADER, *patterns]) + "\n" if patterns else None

    def _manifest_block_sha(self, rel: str, block_id: str) -> Optional[str]:
        """Return the manifest's body sha for a block, or None."""
        entry = ((self.manifest or {}).get("blocks") or {}).get(rel) or {}
        value = entry.get(block_id)
        return value if isinstance(value, str) else None

    def run_blocks(self, args: List[str]) -> dict:
        """Run the project's hyper_blocks.py with an argv list and return its outcome."""
        script = self.project_root / BLOCKS_REL
        if not script.is_file():
            return {"exit": None, "line": f"skipped: {BLOCKS_REL} is missing; {INSTALL_NOTICE}"}
        if not self.can_ask and "--yes" not in args:
            args = [*args, "--yes"]
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(self.project_root),
            stdin=None if self.can_ask else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        line = (result.stdout.strip().splitlines() or [""])[-1]
        if result.returncode != 0:
            line = f"error (exit {result.returncode}): {result.stderr.strip()}"
        return {"exit": result.returncode, "line": line}

    def update_agentignore(self, block_shas: Dict[str, Dict[str, str]]) -> dict:
        """Upsert only the `agentignore` block of .agentignore (Task 17)."""
        body = self.agentignore_body()
        if body is None:
            return {"path": ".agentignore", "line": "skipped: upstream ships no .agentignore"}
        block_id = hyper_blocks.AGENTIGNORE_BLOCK_ID
        if self.dry_run:
            return {"path": ".agentignore", "line": f"would upsert block {block_id}"}
        tmp = Path(tempfile.mkdtemp(prefix="hacf-block-")) / "agentignore.body"
        try:
            tmp.write_text(body, encoding="utf-8")
            args = ["upsert", ".agentignore", "--id", block_id, "--body-file", str(tmp)]
            sha = self._manifest_block_sha(".agentignore", block_id)
            if sha:
                args += ["--manifest-sha", sha]
            outcome = self.run_blocks(args)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        if outcome["exit"] == 0 and outcome["line"].startswith(("created", "upserted", "unch")):
            body_sha = hyper_blocks.body_sha256(hyper_blocks.body_lines(body))
            block_shas.setdefault(".agentignore", {})[block_id] = body_sha
        return {"path": ".agentignore", **outcome}

    def update_instruction_files(self, block_shas: Dict[str, Dict[str, str]]) -> List[dict]:
        """Update the `hacf` block of AGENTS.md, CLAUDE.md, GEMINI.md, or apply R9 (Task 18)."""
        results = []
        block_id = hyper_blocks.INSTRUCTION_BLOCK_ID
        for name in self.SENSITIVE_FILES:
            local = self.project_root / name
            template = self.upstream_dir / self.SENSITIVE_FILE_UPSTREAM_SOURCES[name]
            if never_copy(name) or not template.is_file():
                continue
            if not local.is_file() or local.is_symlink():
                results.append({"path": name, "line": "skipped: no such file in the project"})
                continue
            try:
                has_markers = bool(hyper_blocks.check_file(local))
            except hyper_blocks.BlocksError as exc:
                results.append({"path": name, "line": f"error: {exc}"})
                continue
            if self.dry_run:
                if has_markers:
                    line = f"would upsert block {block_id}"
                else:
                    shas, _ = hyper_blocks.known_template_shas(name, self.project_root)
                    known = sha_file(local) in shas
                    line = (
                        "would replace an unmodified v0.5.x template with the block"
                        if known
                        else "customized whole file: never appended to; you choose replace "
                        f"(with {name}.v059.bak) or the sidecar {local.stem}.hacf-v0.6.0.md"
                    )
                results.append({"path": name, "line": line})
                continue
            if has_markers:
                args = ["upsert", name, "--id", block_id, "--body-file", str(template)]
                sha = self._manifest_block_sha(name, block_id)
                if sha:
                    args += ["--manifest-sha", sha]
            else:
                args = ["instruction", name, "--block-file", str(template)]
            outcome = self.run_blocks(args)
            written = ("created", "upserted", "unchanged", "replaced")
            if outcome["exit"] == 0 and outcome["line"].startswith(written):
                body_sha = hyper_blocks.body_sha256(hyper_blocks.body_lines(template.read_bytes()))
                block_shas.setdefault(name, {})[block_id] = body_sha
            results.append({"path": name, **outcome})
        return results

    # ==== Sensitive dirs (bridges): block-aware merges ====

    def detect_sensitive_changes(self) -> List[str]:
        """Return bridge files that exist locally and differ from upstream (NEVER_COPY skipped)."""
        changed = []
        for sens_dir in self.SENSITIVE_DIRS:
            for rel in walk_files(self.upstream_dir, sens_dir):
                local = self.project_root / rel
                if never_copy(rel) or not local.is_file() or local.is_symlink():
                    continue
                if sha_file(local) != sha_file(self.upstream_dir / rel):
                    changed.append(rel)
        return changed

    def merge_sensitive(self, changed: List[str]) -> dict:
        """Merge changed bridge files interactively, else list them for review."""
        if not changed:
            return {"merged": [], "replaced": [], "skipped": [], "needs_review": []}
        if not self.can_ask:
            return {"merged": [], "replaced": [], "skipped": [], "needs_review": changed}
        engine = MergeEngine(str(self.project_root))
        for rel in changed:
            local = self.project_root / rel
            upstream = (self.upstream_dir / rel).read_text(encoding="utf-8")
            current = local.read_text(encoding="utf-8")
            if self.backup_root is None:
                self.backup_root = self.project_root / ".agents/.backup" / backup_stamp()
            ok, final = engine.merge_file(rel, upstream, current, self.backup_root)
            if ok and final is not None and final != current:
                atomic_write(local, final.encode("utf-8"))
        return {
            "merged": engine.changes["merged"],
            "replaced": engine.changes["replaced"],
            "skipped": engine.changes["skipped"],
            "needs_review": [],
        }

    # ==== Migration hand-off (Task 20) ====

    def migration_plan(self) -> dict:
        """Run the project's installed `hyper_migrate.py plan` (never apply); return its output.

        Only the installed script runs, never the clone's: a dry run has neither verified nor
        been consented to the upstream, so its code must not execute (§4, Task 21). After a
        real update the installed script is the newly synced one.
        """
        script = self.project_root / MIGRATE_REL
        if not script.is_file():
            return {"ran": False, "output": f"{MIGRATE_REL} is not installed; no migration plan"}
        result = subprocess.run(
            [sys.executable, str(script), "plan"],
            cwd=str(self.project_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        output = (result.stdout + result.stderr).strip()
        self.log(f"Migration plan (python3 {MIGRATE_REL} plan):\n{output}")
        self.log(
            f"To migrate, run it yourself in a terminal: python3 {MIGRATE_REL} apply "
            "(this update never applies it)"
        )
        return {
            "ran": True,
            "exit": result.returncode,
            "output": output,
            "apply_command": f"python3 {MIGRATE_REL} apply",
            "script": "installed",
        }

    # ==== Main Execution ====

    def run(self) -> int:
        """Execute the update; print the JSON report; return the exit code."""
        code = 1
        try:
            self._run()
            code = 0
        except UpdateRefused as exc:
            self.log(f"❌ {exc}")
            self.report["status"] = exc.status
            self.report["reason"] = str(exc)
        finally:
            self.release_lock()
            if self.upstream_dir is not None:
                top = self.upstream_dir
                if top.name == "repo":
                    top = top.parent
                shutil.rmtree(top, ignore_errors=True)
        print(json.dumps(self.report, indent=2))
        return code

    def _run(self):
        config = self.load_config()
        self.check_gpg_unsupported(config, CONFIG_REL)
        self.resolve_upstream(config)
        self.verify_git_available()
        self.manifest = self.load_manifest()

        uncommitted = self.check_uncommitted_changes()
        if uncommitted:
            self.report["uncommitted_changes"] = uncommitted
            if not self.dry_run:
                raise UpdateRefused(
                    "uncommitted changes in files this update may change: "
                    + ", ".join(uncommitted)
                    + "; commit or stash them first",
                    status="preflight_warning",
                )
            self.notice("uncommitted changes would stop a real update: " + ", ".join(uncommitted))

        self.fetch_upstream()
        self.check_ancestry()
        payload = self.payload_config()
        self.check_gpg_unsupported(payload, f"the upstream's {CONFIG_REL}")
        proposal = [
            str(s) for s in ((payload.get("verification") or {}).get("allowed_signers") or [])
        ]
        signers, to_pin = self.resolve_signers(proposal)
        self.check_integrity(signers)
        self.report["integrity"] = self.integrity

        if not self.dry_run:
            if self.integrity in (CONSENT_FLAG, CONSENT_INTERACTIVE):
                mode = "flag" if self.integrity == CONSENT_FLAG else "interactive"
                path = self.verifier.log_consent(self.url, self.commit_sha, mode)
                self.log(f"Consent logged ({mode}) for {self.upstream_label()} in {path}")
            if to_pin is not None:
                how = "first-use" if self.report.get("signer_pin") == "first-use" else "consent"
                self.verifier.write_pin(to_pin, self.url, self.commit_sha, how)
                self.log(f"Signer pin written ({how}): {self.verifier.TRUST_FILE}")
            cleaned, message = self.verifier.cleanup_old_backups()
            self.report["backups_cleaned"] = cleaned
            self.log(message)
            self.acquire_lock()
        self.report["backup_warnings"] = self.verifier.get_backup_age_warnings()

        plan = self.plan_sync()
        self.report["sync"] = {
            action: [item["path"] for item in plan if item["action"] == action]
            for action in sorted({item["action"] for item in plan})
            if action != "unchanged"
        }
        self.report["sync_unchanged"] = sum(1 for item in plan if item["action"] == "unchanged")
        block_shas: Dict[str, Dict[str, str]] = {}
        changed_bridges = self.detect_sensitive_changes()
        if self.dry_run:
            self.report["agentignore"] = self.update_agentignore(block_shas)
            self.report["instruction_files"] = self.update_instruction_files(block_shas)
            self.report["sensitive_dirs"] = {"would_review": changed_bridges}
            self.report["migration"] = self.migration_plan()
            self.report["status"] = "dry-run"
            self.log("Dry run: nothing was written.")
            return

        self.apply_sync(plan)
        self.report["agentignore"] = self.update_agentignore(block_shas)
        self.report["instruction_files"] = self.update_instruction_files(block_shas)
        self.report["sensitive_dirs"] = self.merge_sensitive(changed_bridges)
        self.write_manifest(plan, block_shas)
        if self.backup_root is not None:
            self.report["backup"] = str(self.backup_root.relative_to(self.project_root))
        self.report["migration"] = self.migration_plan()
        self.report["status"] = "success"
        files_processed = sum(1 for item in plan if item["action"] != "unchanged")
        self.report["log_file"] = self.verifier.log_audit_trail(
            status="success",
            url=self.url,
            commit_sha=self.commit_sha,
            integrity=self.integrity,
            backups_cleaned=self.report.get("backups_cleaned", 0),
            files_processed=files_processed,
        )
        self.log(f"✅ Updated to {self.upstream_label()}")


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="hyper_update_core.py",
        description="Update HACF's framework files from upstream (HACF's update-integrity policy).",
        allow_abbrev=False,
    )
    parser.add_argument("--upstream-url", help="the upstream git URL (https:// only)")
    parser.add_argument("--ref", help="the upstream branch or tag (default: upstream.yml ref)")
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="consent to an unsigned upstream (needs a TTY; refused under HACF_AUTONOMOUS=1)",
    )
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="accept an upstream commit that does not descend from the installed one",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, check ancestry and signature, print the sync and migration plans; "
        "write nothing",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="never ask; take the non-interactive defaults"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point."""
    ns = build_parser().parse_args(argv)
    updater = HyperUpdateCore(
        os.environ.get("PROJECT_ROOT", "."),
        upstream_url=ns.upstream_url,
        ref=ns.ref,
        allow_unsigned=ns.allow_unsigned,
        allow_downgrade=ns.allow_downgrade,
        dry_run=ns.dry_run,
        assume_yes=ns.yes,
    )
    return updater.run()


if __name__ == "__main__":
    sys.exit(main())
