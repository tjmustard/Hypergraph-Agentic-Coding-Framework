#!/usr/bin/env python3
"""hyper_update_verification.py — upstream integrity (HACF's update-integrity policy), backup retention, audit logs.

Implements:
- SSH signature verification against the machine-local signer pin (R11): the allowed-signers
  file is built from the pin in a temp dir and passed per call with
  `git -c gpg.ssh.allowedSignersFile=<file> verify-commit <sha>`; nothing is imported globally.
- The signer pin `.agents/state/upstream-trust.json` (R12) and the consent log
  `.agents/logs/update-consent.jsonl` (R25.6).
- Backup retention (30 days) over exact backup names only; `*-migration` dirs and
  `.agents/state/migrations/` are exempt.
- The audit trail `.agents/logs/hyper-update.log` (JSON lines; the `integrity` field holds the
  policy's result: ssh-verified, unsigned-consent-interactive or unsigned-consent-flag).

Every timestamp is written with `+00:00` and parsed through `_runtime` (R19.4).
"""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import _runtime

_runtime.require_runtime()  # R19.4: an entry point of /hyper-update

# Integrity results (Task 12).
SSH_VERIFIED = "ssh-verified"
CONSENT_INTERACTIVE = "unsigned-consent-interactive"
CONSENT_FLAG = "unsigned-consent-flag"

# Exact backup names (Task 22): YYYY-MM-DDTHH-MM-SSZ, and the old YYYY-MM-DD.
BACKUP_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")
BACKUP_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_PRINCIPAL = "hacf-upstream"
KEY_TYPE_PREFIXES = ("ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-")


def backup_stamp(now: Optional[datetime] = None) -> str:
    """Return a backup directory name, the UTC time as YYYY-MM-DDTHH-MM-SSZ."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def parse_backup_name(name: str) -> Optional[datetime]:
    """Return the UTC time of an exact backup name, or None for any other name (Task 22)."""
    if name.endswith("-migration"):
        return None
    try:
        if BACKUP_TS_RE.match(name):
            day, clock = name[:-1].split("T")
            return _runtime.parse_ts(f"{day}T{clock.replace('-', ':')}+00:00")
        if BACKUP_DAY_RE.match(name):
            return _runtime.parse_ts(f"{name}T00:00:00+00:00")
    except ValueError:
        return None
    return None


def allowed_signers_lines(signers: List[str]) -> List[str]:
    """Return allowed-signers file lines; a bare `<key-type> <key>` gets a default principal."""
    lines = []
    for entry in signers:
        text = " ".join(str(entry).split())
        if not text:
            continue
        if text.startswith(KEY_TYPE_PREFIXES):
            text = f"{DEFAULT_PRINCIPAL} {text}"
        lines.append(text)
    return lines


SSH_SIGNATURE_BEGIN = "-----BEGIN SSH SIGNATURE-----"


def signature_kind(commit_sha: str, repo_dir: str) -> str:
    """Return "ssh", "none", "mixed" or "other" for the commit object's signature headers.

    Reads the raw object (`git cat-file commit <sha>`); a header is `gpgsig` or `gpgsig-sha256`
    and its first line names the armor, so the format is the signature's, not git config's.
    """
    try:
        result = subprocess.run(
            ["git", "cat-file", "commit", commit_sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return "other"
    if result.returncode != 0:
        return "other"
    header = result.stdout.split("\n\n", 1)[0]
    kinds = set()
    for line in header.split("\n"):
        name, _, value = line.partition(" ")
        if name in ("gpgsig", "gpgsig-sha256"):
            kinds.add("ssh" if value.strip() == SSH_SIGNATURE_BEGIN else "other")
    if not kinds:
        return "none"
    if len(kinds) > 1:
        return "mixed"
    return kinds.pop()


class UpstreamVerification:
    """Handles signature verification, the signer pin, consent and audit logs, retention."""

    BACKUP_DIR = ".agents/.backup"
    MIGRATIONS_DIR = ".agents/state/migrations"
    LOG_DIR = ".agents/logs"
    LOG_FILE = "hyper-update.log"
    CONSENT_LOG = "update-consent.jsonl"
    TRUST_FILE = ".agents/state/upstream-trust.json"
    LOG_MAX_SIZE = 1024 * 1024  # 1MB
    LOG_MAX_FILES = 10
    BACKUP_RETENTION_DAYS = 30

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self.backup_dir = self.project_root / self.BACKUP_DIR
        self.migrations_dir = self.project_root / self.MIGRATIONS_DIR
        self.log_dir = self.project_root / self.LOG_DIR
        self.trust_path = self.project_root / self.TRUST_FILE

    def log_message(self, msg: str):
        """Print a message to stderr for real-time feedback."""
        print(f"[hyper-update] {msg}", file=sys.stderr)

    # ==== Integrity: SSH signatures against the pin (R11) ====

    def verify_upstream_commit(
        self, commit_sha: str, upstream_dir: str, allowed_signers: List[str]
    ) -> Tuple[bool, str, List[str]]:
        """Verify an SSH signature on commit_sha against the pinned signers.

        Returns: (is_valid, message, the verification argv).
        """
        lines = allowed_signers_lines(allowed_signers)
        if not lines:
            return False, "no pinned signers to verify against", []
        # `git verify-commit` dispatches on the signature's own format, so an OpenPGP or
        # X.509 signature would be checked against the user's keyring, not the SSH pin (R11).
        # Accept only a commit whose every signature header is an SSH signature.
        kind = signature_kind(commit_sha, upstream_dir)
        if kind != "ssh":
            return (
                False,
                f"commit {commit_sha[:12]} has no valid signature from a pinned signer "
                f"(signature: {kind}; only an SSH signature by a pinned key is accepted)",
                [],
            )
        tmpdir = tempfile.mkdtemp(prefix="hacf-signers-")
        try:
            signers_file = Path(tmpdir) / "allowed_signers"
            signers_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            argv = [
                "git",
                "-c",
                f"gpg.ssh.allowedSignersFile={signers_file}",
                "verify-commit",
                commit_sha,
            ]
            try:
                result = subprocess.run(
                    argv, cwd=upstream_dir, capture_output=True, text=True, check=False
                )
            except FileNotFoundError:
                return False, "git command not found", argv
            detail = (result.stderr or result.stdout).strip()
            if result.returncode == 0:
                return True, f"SSH signature verified on {commit_sha[:12]} ({detail})", argv
            return (
                False,
                f"commit {commit_sha[:12]} has no valid signature from a pinned signer"
                + (f" ({detail})" if detail else ""),
                argv,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ==== Signer pin (R12) ====

    def read_pin(self) -> Optional[List[str]]:
        """Return the pinned signer set, or None when nothing has been pinned yet."""
        try:
            data = json.loads(self.trust_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self.TRUST_FILE} is unreadable ({exc}); fix or remove it") from None
        signers = data.get("allowed_signers") if isinstance(data, dict) else None
        if not isinstance(signers, list):
            raise ValueError(f"{self.TRUST_FILE} has no allowed_signers list; fix or remove it")
        return [str(s) for s in signers]

    def write_pin(self, signers: List[str], url: str, commit: str, how: str) -> None:
        """Write the signer pin atomically (creating `.agents/state/` on first use)."""
        entry = {
            "allowed_signers": list(signers),
            "pinned_at": _runtime.utc_now_iso(),
            "url": url,
            "commit": commit,
            "how": how,
        }
        atomic_write(self.trust_path, (json.dumps(entry, indent=2) + "\n").encode("utf-8"))

    # ==== Consent log (R25.6) ====

    def log_consent(self, url: str, commit: str, mode: str) -> Path:
        """Append one consent line {timestamp, url, commit, mode, os_user}; return the log path."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / self.CONSENT_LOG
        entry = {
            "timestamp": _runtime.utc_now_iso(),
            "url": url,
            "commit": commit,
            "mode": mode,
            "os_user": getpass.getuser(),
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return path

    # ==== Backup retention (Tasks 22-23) ====

    def _exempt(self, path: Path) -> bool:
        """Return True for a path retention never touches (migration backups, symlinks)."""
        if path.name.endswith("-migration") or path.is_symlink():
            return True
        try:
            path.resolve().relative_to(self.migrations_dir.resolve())
            return True
        except ValueError:
            return False

    def _dated_backups(self):
        """Yield (dir, UTC time) for every backup dir with an exact name that is not exempt."""
        if not self.backup_dir.is_dir():
            return
        for sub in sorted(self.backup_dir.iterdir()):
            if not sub.is_dir() or self._exempt(sub):
                continue
            when = parse_backup_name(sub.name)
            if when is not None:
                yield sub, when

    def cleanup_old_backups(self, now: Optional[datetime] = None) -> Tuple[int, str]:
        """Remove exact-named backup dirs older than BACKUP_RETENTION_DAYS.

        Returns: (count_deleted, log_message)
        """
        now = now or datetime.now(timezone.utc)
        deleted_count = 0
        for backup_subdir, backup_time in list(self._dated_backups()):
            if now - backup_time <= timedelta(days=self.BACKUP_RETENTION_DAYS):
                continue
            try:
                shutil.rmtree(backup_subdir)
                deleted_count += 1
                self.log_message(f"🗑️  Deleted old backup: {backup_subdir.name}")
            except OSError as e:
                self.log_message(f"⚠️  Warning: Could not clean up {backup_subdir.name} ({e})")
        if deleted_count > 0:
            return deleted_count, (
                f"🗑️  Cleaned up {deleted_count} backup(s) older than "
                f"{self.BACKUP_RETENTION_DAYS} days"
            )
        return 0, "No backups to clean up"

    def get_backup_age_warnings(self, now: Optional[datetime] = None) -> list:
        """Return warnings for backups within 2 days of the retention limit."""
        now = now or datetime.now(timezone.utc)
        warnings = []
        for backup_subdir, backup_time in self._dated_backups():
            age = now - backup_time
            days_remaining = self.BACKUP_RETENTION_DAYS - age.days
            if 0 <= days_remaining <= 2:
                warnings.append(
                    f"⚠️  Backup from {backup_subdir.name} is {age.days} days old "
                    f"(expires in {days_remaining} day{'s' if days_remaining != 1 else ''})"
                )
        return warnings

    # ==== Audit trail ====

    def log_audit_trail(
        self,
        status: str,
        url: str,
        commit_sha: Optional[str],
        integrity: Optional[str],
        backups_cleaned: int,
        files_processed: Optional[int] = None,
        error_msg: Optional[str] = None,
    ) -> str:
        """Append an audit entry to the JSON-lines log; return its path."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / self.LOG_FILE
        entry = {
            "timestamp": _runtime.utc_now_iso(),
            "command": "/hyper-update",
            "upstream_repo": url,
            "upstream_commit_sha": commit_sha,
            "integrity": integrity,
            "backups_cleaned": backups_cleaned,
            "status": status,
        }
        if files_processed is not None:
            entry["files_processed"] = files_processed
        if error_msg:
            entry["error"] = error_msg
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            self._rotate_logs(log_path)
        except OSError as e:
            self.log_message(f"⚠️  Warning: Could not write to log file ({e})")
        return str(log_path)

    def _rotate_logs(self, log_path: Path):
        """Rotate log file if it exceeds max size; keep last LOG_MAX_FILES files."""
        try:
            if not log_path.exists() or log_path.stat().st_size <= self.LOG_MAX_SIZE:
                return
            for i in range(self.LOG_MAX_FILES - 1, 0, -1):
                old = log_path.parent / f"{log_path.name}.{i}"
                if old.exists():
                    old.replace(log_path.parent / f"{log_path.name}.{i + 1}")
            log_path.replace(log_path.parent / f"{log_path.name}.1")
            for i in range(self.LOG_MAX_FILES + 1, 100):
                old = log_path.parent / f"{log_path.name}.{i}"
                if not old.exists():
                    break
                old.unlink()
            self.log_message(f"📋 Log rotated (kept last {self.LOG_MAX_FILES} files)")
        except OSError as e:
            self.log_message(f"⚠️  Warning: Could not rotate logs ({e})")

    def format_backup_summary(self) -> str:
        """Format a summary of the dated backups with their ages."""
        now = datetime.now(timezone.utc)
        rows = sorted(self._dated_backups(), key=lambda item: item[0].name, reverse=True)[:10]
        if not rows:
            return ""
        summary_lines = ["Available backups:"]
        for backup_subdir, backup_time in rows:
            age = now - backup_time
            days_remaining = self.BACKUP_RETENTION_DAYS - age.days
            age_str = f"{age.days} days old"
            if days_remaining <= 0:
                age_str += " (EXPIRED)"
            elif days_remaining <= 2:
                age_str += f" (expires in {days_remaining} day{'s' if days_remaining != 1 else ''})"
            summary_lines.append(f"- {backup_subdir.name} ({age_str})")
        return "\n".join(summary_lines)


def atomic_write(path: Path, data: bytes) -> None:
    """Write data to path through a fsynced temp file in the same directory and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
