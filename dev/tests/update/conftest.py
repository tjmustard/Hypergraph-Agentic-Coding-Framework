"""Harness for the /hyper-update tests (S13c Task 27).

Every update runs in a temp project against a local bare upstream built from this working tree
(uncommitted edits included) with HACF_TEST=1 and a file:// URL. A run without a terminal has
stdin on /dev/null and a new session (no controlling terminal); a run "in a terminal" gets a
pseudo-terminal (`pty.fork`) whose prompts are answered by the test. Nothing is written inside
the HACF checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence

import pytest
from installer.conftest import build_upstream, git, git_env

# The framework files a v0.6.0 install puts in a project (the manifest records them).
INSTALLED_DIRS = (
    ".agents/scripts",
    ".agents/skills",
    ".agents/schemas",
    ".agents/config",
    ".agents/install-templates",
)
CORE = ".agents/scripts/hyper_update_core.py"
TRUST_REL = ".agents/state/upstream-trust.json"
CONSENT_REL = ".agents/logs/update-consent.jsonl"
GPG_UID = "gpg-signer@example.invalid"


def sha_file(path: Path) -> str:
    """Return the sha256 hex of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, bytes]:
    """Return {relative path: bytes} for every file under root, .git excluded."""
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/") or not path.is_file() or path.is_symlink():
            continue
        found[rel] = path.read_bytes()
    return found


@dataclass
class Upstream:
    """A local bare upstream and the commit its main branch points at."""

    bare: Path
    sha: str

    @property
    def url(self) -> str:
        """Return the file:// URL of the bare repository."""
        return f"file://{self.bare}"


@dataclass
class Result:
    """One update run: exit code, combined output and the JSON report (the last JSON object)."""

    returncode: int
    output: str
    report: dict = field(default_factory=dict)


def parse_report(output: str) -> dict:
    """Return the JSON report printed last on stdout (`{` at the start of a line)."""
    text = output.replace("\r\n", "\n")
    start = text.rfind("\n{\n")
    start = 0 if start < 0 and text.startswith("{\n") else start
    if start < 0:
        return {}
    try:
        return json.loads(text[start:].strip())
    except ValueError:
        return {}


class Harness:
    """Builds upstreams and projects in tmp_path and runs the project's hyper_update_core.py."""

    def __init__(self, tmp_path: Path, base: Upstream, base_src: Path):
        """Keep the test's tmp dir, the session upstream and the upstream's source tree."""
        self.tmp = tmp_path
        self.scratch = tmp_path / "scratch"
        self.base = base
        self.base_src = base_src
        self.env = git_env(self.scratch)
        self._n = 0
        self.gpg_homes: list[Path] = []

    def _name(self, stem: str) -> Path:
        self._n += 1
        return self.tmp / f"{stem}-{self._n}"

    # ---- upstreams ----

    def derive(
        self,
        changes: Optional[dict[str, Optional[str]]] = None,
        sign_key: Optional[Path] = None,
        parent: Optional[Upstream] = None,
        branch: str = "main",
        gpg_home: Optional[Path] = None,
    ) -> Upstream:
        """Return a new bare upstream: parent's history plus one commit with `changes`.

        A change value of None deletes the path. With sign_key, the commit is SSH-signed; with
        gpg_home (from gpg_key), it is OpenPGP-signed by that home's key.
        """
        parent = parent or self.base
        bare = self._name("upstream") / "upstream.git"
        bare.parent.mkdir(parents=True)
        git(["clone", "-q", "--bare", str(parent.bare), str(bare)], self.tmp, self.env)
        work = bare.parent / "work"
        git(["clone", "-q", "--branch", "main", str(bare), str(work)], self.tmp, self.env)
        if branch != "main":
            git(["checkout", "-q", "-b", branch], work, self.env)
        for rel, text in (changes or {}).items():
            target = work / rel
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(["add", "-A", "-f", "--", "."], work, self.env)
        args = ["-c", "commit.gpgsign=false"]
        env = self.env
        if sign_key is not None:
            args = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={sign_key}"]
        elif gpg_home is not None:
            args = ["-c", "gpg.format=openpgp", "-c", f"user.signingkey={GPG_UID}"]
            env = dict(self.env, GNUPGHOME=str(gpg_home))
        signed = sign_key is not None or gpg_home is not None
        commit = ["commit", "-q", "--allow-empty", "-m", "upstream change"]
        git([*args, *commit, *(["-S"] if signed else [])], work, env)
        git(["push", "-q", "origin", branch], work, self.env)
        sha = git(["rev-parse", "HEAD"], work, self.env).strip()
        return Upstream(bare, sha)

    def ssh_key(self, name: str = "key") -> tuple[Path, str]:
        """Return (private key path, public key `<type> <key>`) of a new temp ed25519 key."""
        key = self._name(name) / "id_ed25519"
        key.parent.mkdir(parents=True)
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)],
            check=True,
            capture_output=True,
        )
        public = " ".join((key.with_suffix(".pub")).read_text().split()[:2])
        return key, public

    def gpg_key(self) -> Path:
        """Return a temp GNUPGHOME holding a new passphrase-less OpenPGP signing key."""
        home = self._name("gnupg")
        home.mkdir(mode=0o700)
        self.gpg_homes.append(home)
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-gen-key",
                GPG_UID,
                "ed25519",
                "sign",
                "never",
            ],
            env=dict(self.env, GNUPGHOME=str(home)),
            check=True,
            capture_output=True,
        )
        return home

    def close(self) -> None:
        """Stop the gpg-agents the test's temp GNUPGHOMEs started."""
        for home in self.gpg_homes:
            subprocess.run(
                ["gpgconf", "--kill", "gpg-agent"],
                env=dict(self.env, GNUPGHOME=str(home)),
                capture_output=True,
                check=False,
            )

    # ---- projects ----

    def project(self, extra: Optional[dict[str, str]] = None, manifest: bool = True) -> Path:
        """Return a committed temp project holding the base upstream's framework files."""
        root = self._name("project")
        root.mkdir()
        git(["init", "-q", "-b", "main"], root, self.env)
        files = {}
        for top in INSTALLED_DIRS:
            for source in sorted((self.base_src / top).rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                rel = source.relative_to(self.base_src).as_posix()
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files[rel] = {"sha256": sha_file(target), "owner": "hacf"}
        if manifest:
            data = {
                "version": "0.6.0",
                "installed_commit": self.base.sha,
                "installed_at": "2026-11-01T00:00:00+00:00",
                "files": files,
                "blocks": {},
            }
            (root / ".agents/hacf-manifest.json").write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        for rel, text in (extra or {}).items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.commit(root)
        return root

    def commit(self, root: Path) -> None:
        """Commit everything in the project."""
        git(["add", "-A", "-f", "--", "."], root, self.env)
        git(
            ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "p"],
            root,
            self.env,
        )

    # ---- runs ----

    def run_env(self, env: Optional[dict[str, Optional[str]]]) -> dict[str, str]:
        """Return the run environment: HACF_TEST=1 unless overridden; None entries removed."""
        full = dict(self.env, HACF_TEST="1")
        for key, value in (env or {}).items():
            if value is None:
                full.pop(key, None)
            else:
                full[key] = value
        return full

    def run(
        self,
        project: Path,
        *args: str,
        env: Optional[dict[str, Optional[str]]] = None,
        timeout: int = 300,
    ) -> Result:
        """Run the update with no terminal (stdin /dev/null, a new session)."""
        proc = subprocess.run(
            [sys.executable, str(project / CORE), *args],
            cwd=project,
            env=self.run_env(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
            check=False,
        )
        output = proc.stdout + proc.stderr
        return Result(proc.returncode, output, parse_report(proc.stdout))

    def run_tty(
        self,
        project: Path,
        *args: str,
        answers: Sequence[tuple[str, str]] = (),
        env: Optional[dict[str, Optional[str]]] = None,
        timeout: int = 120,
    ) -> Result:
        """Run the update on a pseudo-terminal; answer each (prompt substring, reply) in order."""
        argv = [sys.executable, str(project / CORE), *args]
        full_env = self.run_env(env)
        pid, fd = pty.fork()
        if pid == 0:  # the child: exec at once
            try:
                os.chdir(project)
                os.execve(argv[0], argv, full_env)
            finally:
                os._exit(127)
        out = b""
        consumed = 0
        pending = list(answers)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if time.monotonic() > deadline:
                    os.kill(pid, signal.SIGKILL)
                    raise AssertionError(f"pty run timed out; output so far:\n{out.decode()}")
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break  # EIO: the child closed the terminal
                if not chunk:
                    break
                out += chunk
                while pending:
                    index = out.find(pending[0][0].encode(), consumed)
                    if index < 0:
                        break
                    consumed = index + len(pending[0][0])
                    os.write(fd, (pending[0][1] + "\n").encode())
                    pending.pop(0)
        finally:
            os.close(fd)
        _, status = os.waitpid(pid, 0)
        text = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
        return Result(os.waitstatus_to_exitcode(status), text, parse_report(text))


@pytest.fixture(scope="session")
def base_upstream(tmp_path_factory: pytest.TempPathFactory) -> tuple[Upstream, Path]:
    """Return the session's bare upstream built from the working tree, and its source dir."""
    root = tmp_path_factory.mktemp("update-upstream")
    built = build_upstream(root, root / "scratch")
    return Upstream(built.bare, built.sha), root / "src"


@pytest.fixture
def hx(tmp_path: Path, base_upstream: tuple[Upstream, Path]) -> Iterator[Harness]:
    """Return a Harness for this test; stop any gpg-agent it started afterwards."""
    harness = Harness(tmp_path, *base_upstream)
    yield harness
    harness.close()
