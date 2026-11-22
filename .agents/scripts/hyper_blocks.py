#!/usr/bin/env python3
"""hyper_blocks.py — HACF's managed-block engine and the whole-file instruction decision (R9, R26).

Normative design: HACF's v0.6.0 design notes (not shipped): target layout ("Managed-block grammar"),
key ownership (compare-and-swap, R22.3) and the installer (MANAGED BLOCKS).

CLI (frozen from S12a; S12b's installer, S12c, S13a-c and S15a1 call it, so a change needs a
Replan R-row):
    check <file>
        Lint the HACF markers only; writes nothing.
    upsert <file> --id <id> --body-file <f> [--manifest-sha <sha>] [--yes]
        Replace the body of block <id>, or append the block at the end of the file (creating
        the file when it is missing).
    remove <file> --id <id> [--manifest-sha <sha>] [--yes]
        Delete block <id> and its two marker lines.
    instruction <file> --block-file <f> [--yes]
        The R9 decision for AGENTS.md, CLAUDE.md and GEMINI.md (decide_instruction_file()).

Block ids: the instruction-file block is `hacf` (INSTRUCTION_BLOCK_ID), the scaffold's
`.gitignore` block is `gitignore`, the info/attributes block is `merge-drivers` (written by
`hyper_lore.py install-drivers`) and `.agentignore`'s is `agentignore` (S11).

Comment style follows the file: hash comments for `.gitignore`, `.agentignore`,
`info/attributes`, `.cursorignore`, `.rooignore`, `.codeiumignore` and `.geminiignore`;
Markdown for `*.md`; any other file exits 2.

Exit codes: 0 ok (including a user-edited block "left in place and reported"), 2 usage,
5 refused (a marker error, a compare-and-swap mismatch, a symlink, or a `CLAUDE.local.md` path),
always with nothing written. The same operations are the importable functions check_file(),
upsert(), remove() and decide_instruction_file().

Grammar (R26):
- Markers are exact, case-sensitive whole lines at column 0:
  `<!-- BEGIN HACF MANAGED BLOCK: <id> -->` / `<!-- END HACF MANAGED BLOCK: <id> -->`
  (Markdown) and `# BEGIN HACF MANAGED BLOCK: <id>` / `# END HACF MANAGED BLOCK: <id>`
  (hash comments), and the same pairs for `HACF GENERATED: <id>`
  regions. `<id>` matches `^[a-z][a-z0-9-]{0,63}$`. A prefix, a case-folded or lowercase `hacf`
  marker, an indented marker and a marker inside a fenced code block (Markdown only; ``` or ~~~)
  are ordinary text.
- OpenRig's `<!-- BEGIN OpenRig MANAGED BLOCK: <id> -->` / END pair is recognized only to detect
  nesting. Foreign blocks (OpenRig, RIGGED) are never parsed further or rewritten.
- A duplicate HACF id, an HACF marker inside another HACF block or inside an OpenRig block, an
  OpenRig block inside an HACF block, and an unbalanced or mismatched BEGIN/END raise BlockError.
- Writes keep the UTF-8 BOM, the EOL style (CRLF when the first line ending is CRLF, else LF),
  the final-newline state and every byte outside the block. New lines use the file's EOL.
- An append adds one separating empty line unless the file already ends with one; removing a
  block that ends the file also drops one empty line before it, so upsert then remove gives
  back the original bytes.
- A block body's sha256 (body_sha256()) is taken over its lines with their line endings
  normalized to LF, each line terminated by LF; it is what a manifest records.
- With --manifest-sha, a body whose sha differs was edited by the user: when interactive
  (`/dev/tty` readable, no --yes) the file is backed up to `.agents/.backup/<UTC ts>/<repo-relative
  path>` and the user is asked keep or replace (default keep); otherwise the block is left and a
  `left:` line is printed (exit 0).
- Every write is compare-and-swap (R22.3): the bytes read are hashed, a temp file in the same
  directory is written and fsynced, the file is re-read just before os.replace and a change
  exits 5 with nothing written; the directory is fsynced and the file mode kept.
- Any operation on a path named `CLAUDE.local.md` exits 5 before the file is opened.

Instruction decision (R9), decide_instruction_file(): a missing file is created holding only
the `hacf` block; a file with HACF markers gets the `hacf` block upserted; a file with no HACF
markers whose sha256 is a known v0.5.x template (V05X_TEMPLATE_SHAS plus the entries for that
name in the project's `.agents/config/legacy_file_hashes.json` when it exists) is replaced by the
block; any other file is never appended to: interactive, the user picks (a) replace it, saving
`<file>.v059.bak` first, or (b) leave it and write the block to `<STEM>.hacf-v0.6.0.md` with a
merge-instructions header (the default); `--yes` or no TTY takes (b) and prints a report line.
Without legacy_file_hashes.json the CLI prints "run HACF-install.sh to complete the upgrade"
(R24.10) and uses the embedded list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TextIO

import _runtime

_runtime.require_runtime()  # R19: at import, so every caller gets the actionable message

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 5

INSTRUCTION_BLOCK_ID = "hacf"
GITIGNORE_BLOCK_ID = "gitignore"
ATTRIBUTES_BLOCK_ID = "merge-drivers"
AGENTIGNORE_BLOCK_ID = "agentignore"
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
PROTECTED_NAME = "CLAUDE.local.md"
LEGACY_HASHES_REL = ".agents/config/legacy_file_hashes.json"
BACKUP_REL = ".agents/.backup"
SIDECAR_SUFFIX = ".hacf-v0.6.0.md"
BAK_SUFFIX = ".v059.bak"
INSTALL_NOTICE = "run HACF-install.sh to complete the upgrade"

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ID = rb"([a-z][a-z0-9-]{0,63})"
MD_MARKER_RE = re.compile(rb"^<!-- (BEGIN|END) HACF (MANAGED BLOCK|GENERATED): " + _ID + rb" -->$")
HASH_MARKER_RE = re.compile(rb"^# (BEGIN|END) HACF (MANAGED BLOCK|GENERATED): " + _ID + rb"$")
OPENRIG_MARKER_RE = re.compile(rb"^<!-- (BEGIN|END) OpenRig MANAGED BLOCK: (\S+) -->$")
FENCE_RE = re.compile(rb"^ {0,3}(`{3,}|~{3,})")
BOM = b"\xef\xbb\xbf"

HASH_FILES = frozenset(
    {".gitignore", ".agentignore", ".cursorignore", ".rooignore", ".codeiumignore", ".geminiignore"}
)
MARKDOWN, HASH = "markdown", "hash"
MANAGED = "MANAGED BLOCK"

# sha256 of .agents/install-templates/{AGENTS,CLAUDE,GEMINI}.md at the v0.5.x release commits
# 626a28e, cd8ae6a, b3b1634, 329e8f7, 0b19f16 and 984ca46 (computed with hashlib from
# `git show <commit>:<path>` at S12a execute time).
V05X_TEMPLATE_SHAS: dict[str, tuple[str, ...]] = {
    "AGENTS.md": (
        "16d9d0ff816587b2a1fd6562bcd524f52ccc2b2e9eaad6a2ebd9426a73781ef2",
        "418eba1d56b3e3d5d4f2975a21929a0717d84dcd2d6374615c1332542976a146",
        "c08804f0d1193d8a9bccc5e322778b1dfb85d5c0e1463cf583e15b0489ab3b6b",
    ),
    "CLAUDE.md": (
        "03a36e0baa5a9d786a9519816fb9496ec0480fdd1e5b82de30d659e9756a3e36",
        "725c649ca51dc723c88db60109084544a9dec295dc12d405a74df640fede34ee",
    ),
    "GEMINI.md": (
        "0d289c99aabe913b44ce2b3a0b855313c5d22dbdad81411ab8c6c91df67d34e4",
        "ce930880933b53eec9a4c310e086665bbee5ff4af55b8f1c6e71c5248c6b60cc",
    ),
}

# Test seam for the compare-and-swap check (R22): called after the temp file is written and
# before the file is re-read, so a test can change the file in between.
BEFORE_REPLACE_HOOK: Optional[Callable[[Path], None]] = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BlocksError(Exception):
    """A failure that ends an operation with nothing written; `code` is its exit code."""

    code = EXIT_USAGE


class UsageError(BlocksError):
    """Bad arguments or an unsupported file (exit 2)."""

    code = EXIT_USAGE


class BlockError(BlocksError):
    """Malformed HACF markers: duplicate, nested or unbalanced (exit 5)."""

    code = EXIT_REFUSED


class Refused(BlocksError):
    """A compare-and-swap mismatch, a symlink or a CLAUDE.local.md path (exit 5)."""

    code = EXIT_REFUSED


# ---------------------------------------------------------------------------
# Byte-faithful documents
# ---------------------------------------------------------------------------


@dataclass
class Doc:
    """A file split into lines that keep their own endings, so untouched lines re-emit as read."""

    bom: bytes
    lines: list[bytes]
    eol: bytes

    @property
    def final_newline(self) -> bool:
        """Return True when the file is empty or its last line ends with a newline."""
        return not self.lines or self.lines[-1].endswith(b"\n")

    def to_bytes(self) -> bytes:
        """Return the file's bytes."""
        return self.bom + b"".join(self.lines)


def parse_doc(data: bytes) -> Doc:
    """Split bytes into a Doc, recording the BOM and the EOL style of the first line ending."""
    bom = BOM if data.startswith(BOM) else b""
    rest = data[len(bom) :]
    lines = rest.splitlines(keepends=True)
    # splitlines() also breaks on \r, \v, \f and others; re-join so only \n ends a line.
    joined: list[bytes] = []
    for part in lines:
        if joined and not joined[-1].endswith(b"\n"):
            joined[-1] += part
        else:
            joined.append(part)
    first = rest.find(b"\n")
    eol = b"\r\n" if first > 0 and rest[first - 1 : first] == b"\r" else b"\n"
    return Doc(bom, joined, eol)


def _content(line: bytes) -> bytes:
    """Return a line without its LF or CRLF ending."""
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def body_sha256(lines: list[bytes]) -> str:
    """Return the sha256 of block body lines, each normalized to end with a single LF."""
    digest = hashlib.sha256()
    for line in lines:
        digest.update(_content(line) + b"\n")
    return digest.hexdigest()


def body_lines(body: bytes | str) -> list[bytes]:
    """Split a block body (text or bytes, any EOL) into lines without endings."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    if data.startswith(BOM):
        data = data[len(BOM) :]
    return [_content(line) for line in data.splitlines(keepends=True)]


# ---------------------------------------------------------------------------
# Comment style and markers
# ---------------------------------------------------------------------------


def style_of(path: Path) -> str:
    """Return the comment style of a file, or raise UsageError for an unsupported one."""
    name = path.name
    if name in HASH_FILES or (name == "attributes" and path.parent.name == "info"):
        return HASH
    if name.endswith(".md"):
        return MARKDOWN
    raise UsageError(f"{path}: unsupported file type (Markdown *.md or a hash-comment file only)")


def marker(style: str, edge: str, block_id: str, kind: str = MANAGED) -> bytes:
    """Return one marker line without its line ending."""
    if style == MARKDOWN:
        return f"<!-- {edge} HACF {kind}: {block_id} -->".encode()
    return f"# {edge} HACF {kind}: {block_id}".encode()


def check_id(block_id: str) -> str:
    """Return block_id when it matches the id grammar, else raise UsageError."""
    if not ID_RE.match(block_id or ""):
        raise UsageError(f"block id {block_id!r} does not match ^[a-z][a-z0-9-]{{0,63}}$")
    return block_id


@dataclass(frozen=True)
class Block:
    """One balanced HACF block or generated region: its kind, id and marker line indexes."""

    kind: str
    block_id: str
    begin: int
    end: int


def _fence_flags(lines: list[bytes], style: str) -> list[bool]:
    """Flag each line inside a fenced code block, fence lines included (Markdown only)."""
    if style != MARKDOWN:
        return [False] * len(lines)
    flags, fence = [], None
    for line in lines:
        text = _content(line)
        if fence is None:
            match = FENCE_RE.match(text)
            fence = match.group(1) if match else None
            flags.append(fence is not None)
            continue
        flags.append(True)
        stripped = text.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
    return flags


def scan(doc: Doc, style: str) -> list[Block]:
    """Return every HACF block in doc, or raise BlockError on a malformed or nested marker."""
    regex = MD_MARKER_RE if style == MARKDOWN else HASH_MARKER_RE
    blocks: list[Block] = []
    seen: set[tuple[str, str]] = set()
    open_hacf: Optional[tuple[str, str, int]] = None
    open_openrig: Optional[str] = None
    for index, (line, fenced) in enumerate(zip(doc.lines, _fence_flags(doc.lines, style))):
        if fenced:
            continue
        text = _content(line)
        foreign = OPENRIG_MARKER_RE.match(text) if style == MARKDOWN else None
        if foreign is not None:
            edge, name = foreign.group(1).decode(), foreign.group(2).decode()
            if open_hacf is not None:
                raise BlockError(
                    f"line {index + 1}: an OpenRig block marker inside HACF "
                    f"{open_hacf[0]}: {open_hacf[1]}"
                )
            if edge == "BEGIN":
                open_openrig = name
            elif open_openrig == name:
                open_openrig = None
            continue
        match = regex.match(text)
        if match is None:
            continue
        edge = match.group(1).decode()
        key = (match.group(2).decode(), match.group(3).decode())
        name = f"HACF {key[0]}: {key[1]}"
        if open_openrig is not None:
            raise BlockError(
                f"line {index + 1}: {name} marker inside the OpenRig block {open_openrig}"
            )
        if edge == "BEGIN":
            if open_hacf is not None:
                raise BlockError(
                    f"line {index + 1}: {name} is nested in HACF {open_hacf[0]}: {open_hacf[1]}"
                )
            if key in seen:
                raise BlockError(f"line {index + 1}: duplicate {name} markers")
            seen.add(key)
            open_hacf = (key[0], key[1], index)
        elif open_hacf is None or (open_hacf[0], open_hacf[1]) != key:
            raise BlockError(f"line {index + 1}: unbalanced {name} marker (END without its BEGIN)")
        else:
            blocks.append(Block(key[0], key[1], open_hacf[2], index))
            open_hacf = None
    if open_hacf is not None:
        raise BlockError(
            f"line {open_hacf[2] + 1}: unbalanced HACF {open_hacf[0]}: {open_hacf[1]} (no END)"
        )
    return blocks


def find_block(blocks: list[Block], block_id: str, kind: str = MANAGED) -> Optional[Block]:
    """Return the block of that kind and id, or None."""
    for block in blocks:
        if block.kind == kind and block.block_id == block_id:
            return block
    return None


# ---------------------------------------------------------------------------
# File access: guard, read, compare-and-swap write, backup
# ---------------------------------------------------------------------------


def guard(path: Path) -> Path:
    """Refuse CLAUDE.local.md (before any open) and a symlink; return the path."""
    path = Path(path)
    if path.name == PROTECTED_NAME:
        raise Refused(f"{path}: HACF never touches {PROTECTED_NAME} (R26); nothing was written")
    if path.is_symlink():
        raise Refused(f"{path} is a symlink; HACF writes only regular files")
    return path


def read_optional(path: Path) -> Optional[bytes]:
    """Return a file's bytes, or None when it does not exist."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise UsageError(f"{path} is a directory") from None


def _sha(data: Optional[bytes]) -> Optional[str]:
    """Return the sha256 hex of data, or None for a missing file."""
    return None if data is None else hashlib.sha256(data).hexdigest()


def _fsync_dir(directory: Path) -> None:
    """Fsync a directory so a rename in it is durable; ignore platforms that refuse."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def cas_write(path: Path, original: Optional[bytes], new: bytes) -> None:
    """Replace path with new unless it changed since original was read (R22.3).

    original is None for a file that did not exist. A temp file in the same directory is
    written and fsynced; just before os.replace the file is re-read and compared, and a change
    raises Refused with nothing written. The file mode is kept and the directory fsynced.
    """
    directory = path.parent
    if not directory.is_dir():
        raise UsageError(f"{directory} does not exist")
    if original is not None:
        mode = os.stat(path).st_mode & 0o7777
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".hacf-tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if BEFORE_REPLACE_HOOK is not None:
            BEFORE_REPLACE_HOOK(path)
        if path.is_symlink() or _sha(read_optional(path)) != _sha(original):
            raise Refused(f"{path} changed while HACF was writing it; nothing was written")
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(directory)


def repo_root(path: Path) -> Path:
    """Return the git work-tree top of path's directory, else that directory."""
    directory = path.parent.resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return directory
    top = proc.stdout.strip()
    return Path(top).resolve() if proc.returncode == 0 and top else directory


def utc_stamp() -> str:
    """Return the backup directory name: the UTC time as YYYYMMDDTHHMMSSZ."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup(path: Path, data: bytes, root: Optional[Path] = None) -> Path:
    """Copy data to `.agents/.backup/<UTC ts>/<repo-relative path>` and return the copy's path."""
    root = repo_root(path) if root is None else root
    real = path.resolve()
    rel = os.path.relpath(real, root) if real.is_relative_to(root) else real.name
    target = root / BACKUP_REL / utc_stamp() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def open_tty() -> Optional[TextIO]:
    """Return /dev/tty opened for reading and writing, or None when there is none."""
    try:
        return open("/dev/tty", "r+", encoding="utf-8")  # the caller closes it
    except OSError:
        return None


TtyOpener = Callable[[], Optional[TextIO]]


def ask(tty: TextIO, question: str, choices: tuple[str, ...], default: str) -> str:
    """Ask on the TTY until the answer is one of choices; an empty answer is the default."""
    prompt = f"{question} [{'/'.join(choices)}] (default {default}): "
    for _ in range(5):
        tty.write(prompt)
        tty.flush()
        answer = tty.readline()
        if not answer:
            return default
        answer = answer.strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
    return default


def _interactive_tty(assume_yes: bool, tty_opener: Optional[TtyOpener]) -> Optional[TextIO]:
    """Return the TTY to ask on, or None under --yes or without one."""
    if assume_yes:
        return None
    return (tty_opener or open_tty)()


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """The result of one operation: its action, the path and a report line."""

    action: str  # created | upserted | unchanged | removed | absent | left | replaced
    # | replaced-with-backup | sidecar
    path: Path
    detail: str = ""

    @property
    def changed(self) -> bool:
        """Return True when the operation wrote the target file or a sidecar."""
        return self.action not in ("unchanged", "absent", "left")

    def line(self) -> str:
        """Return the report line, `<action>: <path>[ (<detail>)]`."""
        return f"{self.action}: {self.path}" + (f" ({self.detail})" if self.detail else "")


def check_file(path: Path) -> list[Block]:
    """Lint a file's HACF markers; return its blocks (empty for a missing file)."""
    path = guard(path)
    style = style_of(path)
    data = read_optional(path)
    return [] if data is None else scan(parse_doc(data), style)


def _block_body(doc: Doc, block: Block) -> list[bytes]:
    """Return the body lines of a block, with their original endings."""
    return doc.lines[block.begin + 1 : block.end]


def body_of(doc: Doc, block: Block) -> list[bytes]:
    """Return the body lines of a block without their line endings."""
    return [_content(line) for line in _block_body(doc, block)]


def _user_edited(
    path: Path,
    data: bytes,
    doc: Doc,
    block: Block,
    manifest_sha: Optional[str],
    assume_yes: bool,
    tty_opener: Optional[TtyOpener],
    verb: str,
) -> Optional[Outcome]:
    """Return a `left` outcome when a user-edited block is kept, None to go on writing (R26)."""
    if manifest_sha is None or body_sha256(_block_body(doc, block)) == manifest_sha:
        return None
    tty = _interactive_tty(assume_yes, tty_opener)
    if tty is None:
        return Outcome(
            "left",
            path,
            f"block {block.block_id} was edited by the user (its sha differs from the "
            f"manifest); not {verb}d under --yes or without a TTY",
        )
    try:
        copy = backup(path, data)
        tty.write(f"HACF block {block.block_id} in {path} was edited; backup: {copy}\n")
        choice = ask(tty, f"Keep your edits or {verb} the block?", ("keep", verb), "keep")
    finally:
        tty.close()
    if choice == "keep":
        return Outcome("left", path, f"block {block.block_id} kept at the user's choice; {copy}")
    return None


def _new_block(doc_eol: bytes, style: str, block_id: str, lines: list[bytes]) -> list[bytes]:
    """Return the marker and body lines of a new block, each ending with doc_eol."""
    rows = [marker(style, "BEGIN", block_id), *lines, marker(style, "END", block_id)]
    return [row + doc_eol for row in rows]


def render_upsert(doc: Doc, style: str, block_id: str, lines: list[bytes]) -> Doc:
    """Return doc with the block's body replaced, or the block appended (Task 5)."""
    block = find_block(scan(doc, style), block_id)
    eol = doc.eol
    if block is not None:
        new_lines = [line + eol for line in lines]
        return Doc(doc.bom, doc.lines[: block.begin + 1] + new_lines + doc.lines[block.end :], eol)
    out = list(doc.lines)
    had_final = doc.final_newline
    if out:
        if not out[-1].endswith(b"\n"):
            out[-1] += eol
        if _content(out[-1]).strip():
            out.append(eol)  # one separating empty line
    out += _new_block(eol, style, block_id, lines)
    if not had_final:
        out[-1] = _content(out[-1])
    return Doc(doc.bom, out, eol)


def render_remove(doc: Doc, style: str, block_id: str) -> Optional[Doc]:
    """Return doc without the block and its markers, or None when it has no such block."""
    block = find_block(scan(doc, style), block_id)
    if block is None:
        return None
    out = doc.lines[: block.begin] + doc.lines[block.end + 1 :]  # every other byte stays (Task 6)
    return Doc(doc.bom, out, doc.eol)


def upsert(
    path: Path,
    block_id: str,
    body: bytes | str,
    manifest_sha: Optional[str] = None,
    assume_yes: bool = False,
    tty_opener: Optional[TtyOpener] = None,
) -> Outcome:
    """Write block_id's body into path (creating the file when missing), compare-and-swap."""
    path = guard(path)
    block_id = check_id(block_id)
    style = style_of(path)
    lines = body_lines(body)
    data = read_optional(path)
    if data is None:
        new = b"".join(_new_block(b"\n", style, block_id, lines))
        cas_write(path, None, new)
        return Outcome("created", path, f"block {block_id}")
    doc = parse_doc(data)
    block = find_block(scan(doc, style), block_id)
    if block is not None:
        left = _user_edited(path, data, doc, block, manifest_sha, assume_yes, tty_opener, "replace")
        if left is not None:
            return left
    new = render_upsert(doc, style, block_id, lines).to_bytes()
    if new == data:
        return Outcome("unchanged", path, f"block {block_id}")
    cas_write(path, data, new)
    return Outcome("upserted", path, f"block {block_id}")


def remove(
    path: Path,
    block_id: str,
    manifest_sha: Optional[str] = None,
    assume_yes: bool = False,
    tty_opener: Optional[TtyOpener] = None,
) -> Outcome:
    """Delete block_id and its marker lines from path, compare-and-swap (S12c's uninstaller)."""
    path = guard(path)
    block_id = check_id(block_id)
    style = style_of(path)
    data = read_optional(path)
    if data is None:
        return Outcome("absent", path, "no such file")
    doc = parse_doc(data)
    block = find_block(scan(doc, style), block_id)
    if block is None:
        return Outcome("absent", path, f"no block {block_id}")
    left = _user_edited(path, data, doc, block, manifest_sha, assume_yes, tty_opener, "remove")
    if left is not None:
        return left
    result = render_remove(doc, style, block_id)
    assert result is not None
    cas_write(path, data, result.to_bytes())
    return Outcome("removed", path, f"block {block_id}")


# ---------------------------------------------------------------------------
# Whole-file instruction decision (R9)
# ---------------------------------------------------------------------------


def _legacy_entries(root: Path) -> Optional[dict]:
    """Return the parsed project legacy_file_hashes.json, or None when it is missing or bad."""
    path = root / LEGACY_HASHES_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def known_template_shas(name: str, root: Optional[Path] = None) -> tuple[set[str], bool]:
    """Return the known v0.5.x shas of an instruction file name, and whether the config exists.

    The embedded list is always used; legacy_file_hashes.json (S12c) adds the entries keyed by
    `<name>` or `.agents/install-templates/<name>`, whose value is a sha or a list of shas.
    """
    shas = set(V05X_TEMPLATE_SHAS.get(name, ()))
    entries = _legacy_entries(root) if root is not None else None
    if entries is not None:
        for key in (name, f".agents/install-templates/{name}"):
            value = entries.get(key)
            values = value if isinstance(value, list) else [value]
            shas.update(v for v in values if isinstance(v, str) and len(v) == 64)
    return shas, entries is not None


def sidecar_path(path: Path) -> Path:
    """Return `<STEM>.hacf-v0.6.0.md` next to path (AGENTS.md -> AGENTS.hacf-v0.6.0.md)."""
    return path.with_name(path.name[: -len(".md")] + SIDECAR_SUFFIX)


def sidecar_text(path: Path, block: bytes) -> bytes:
    """Return the sidecar file: a merge-instructions header, then the HACF block."""
    header = (
        f"<!-- HACF v0.6.0: {path.name} was customized, so HACF did not change it. -->\n"
        f"<!-- To finish the upgrade, copy the block below (both marker lines included) into "
        f"{path.name}, then delete this file. -->\n\n"
    )
    return header.encode() + block


def _instruction_block(lines: list[bytes]) -> bytes:
    """Return a whole instruction file holding only the `hacf` block (LF)."""
    return b"".join(_new_block(b"\n", MARKDOWN, INSTRUCTION_BLOCK_ID, lines))


def _write_sidecar(path: Path, block: bytes) -> Outcome:
    """Write (or refresh) the sidecar file and return the `sidecar` outcome."""
    target = guard(sidecar_path(path))
    content = sidecar_text(path, block)
    old = read_optional(target)
    if old != content:
        cas_write(target, old, content)
    return Outcome(
        "sidecar", path, f"customized; left unchanged and the HACF block written to {target.name}"
    )


def decide_instruction_file(
    path: Path,
    block_body: bytes | str,
    interactive: bool = True,
    assume_yes: bool = False,
    tty_opener: Optional[TtyOpener] = None,
    manifest_sha: Optional[str] = None,
    root: Optional[Path] = None,
) -> Outcome:
    """Apply the R9 decision to AGENTS.md, CLAUDE.md or GEMINI.md and return the outcome.

    interactive False, or assume_yes, never asks. root is the project root whose
    legacy_file_hashes.json is consulted (default: path's git work tree).
    """
    path = guard(path)
    if path.name not in INSTRUCTION_FILES:
        raise UsageError(f"{path}: the instruction decision is for {', '.join(INSTRUCTION_FILES)}")
    lines = body_lines(block_body)
    data = read_optional(path)
    if data is None:
        cas_write(path, None, _instruction_block(lines))
        return Outcome("created", path, f"block {INSTRUCTION_BLOCK_ID}")
    doc = parse_doc(data)
    if scan(doc, MARKDOWN):
        return upsert(
            path,
            INSTRUCTION_BLOCK_ID,
            block_body,
            manifest_sha=manifest_sha,
            assume_yes=assume_yes or not interactive,
            tty_opener=tty_opener,
        )
    shas, _ = known_template_shas(path.name, repo_root(path) if root is None else root)
    block = _instruction_block(lines)
    if hashlib.sha256(data).hexdigest() in shas:
        cas_write(path, data, block)
        return Outcome("replaced", path, "an unmodified v0.5.x template")
    tty = None if not interactive else _interactive_tty(assume_yes, tty_opener)
    if tty is None:
        return _write_sidecar(path, block)
    try:
        tty.write(
            f"{path} was customized, so HACF will not append to it. Choose:\n"
            f"  a) replace it with the v0.6.0 HACF block, saving {path.name}{BAK_SUFFIX} first\n"
            f"  b) leave it and write the block to {sidecar_path(path).name}\n"
        )
        choice = ask(tty, "Replace or sidecar?", ("a", "b"), "b")
    finally:
        tty.close()
    if choice != "a":
        return _write_sidecar(path, block)
    bak = guard(path.with_name(path.name + BAK_SUFFIX))
    old_bak = read_optional(bak)
    if old_bak not in (None, data):
        raise Refused(f"{bak} exists with other content; nothing was written")
    if old_bak is None:
        cas_write(bak, None, data)
    try:
        cas_write(path, data, block)
    except Refused:
        if old_bak is None and read_optional(bak) == data:
            bak.unlink()  # exit 5 means nothing written: undo the backup this call made
        raise
    return Outcome("replaced-with-backup", path, f"the original is in {bak.name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_arg_file(value: str, flag: str) -> bytes:
    """Return the bytes of a --body-file/--block-file argument, or raise UsageError."""
    try:
        return Path(value).read_bytes()
    except OSError as exc:
        raise UsageError(f"{flag} {value}: {exc.strerror or exc}") from None


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the frozen CLI."""
    parser = argparse.ArgumentParser(prog="hyper_blocks.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="lint the HACF markers of a file")
    p.add_argument("file")
    p = sub.add_parser("upsert", help="write one HACF block")
    p.add_argument("file")
    p.add_argument("--id", required=True, dest="block_id")
    p.add_argument("--body-file", required=True)
    p.add_argument("--manifest-sha")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("remove", help="delete one HACF block")
    p.add_argument("file")
    p.add_argument("--id", required=True, dest="block_id")
    p.add_argument("--manifest-sha")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("instruction", help="the R9 decision for AGENTS.md, CLAUDE.md, GEMINI.md")
    p.add_argument("file")
    p.add_argument("--block-file", required=True)
    p.add_argument("--yes", "-y", action="store_true")
    return parser


def run(ns: argparse.Namespace) -> int:
    """Run one parsed command and return its exit code."""
    path = Path(ns.file)
    if ns.command == "check":
        blocks = check_file(path)
        if read_optional(guard(path)) is None:
            print(f"check: {path}: no such file")
        else:
            names = ", ".join(f"{b.kind} {b.block_id}" for b in blocks) or "no HACF blocks"
            print(f"check: {path}: ok ({names})")
        return EXIT_OK
    if ns.command == "upsert":
        guard(path)
        outcome = upsert(
            path,
            ns.block_id,
            _read_arg_file(ns.body_file, "--body-file"),
            manifest_sha=ns.manifest_sha,
            assume_yes=ns.yes,
        )
    elif ns.command == "remove":
        outcome = remove(path, ns.block_id, manifest_sha=ns.manifest_sha, assume_yes=ns.yes)
    else:
        guard(path)
        body = _read_arg_file(ns.block_file, "--block-file")
        _, configured = known_template_shas(path.name, repo_root(path))
        if not configured:
            print(f"notice: {LEGACY_HASHES_REL} is missing; {INSTALL_NOTICE}", file=sys.stderr)
        outcome = decide_instruction_file(path, body, interactive=True, assume_yes=ns.yes)
    print(outcome.line())
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    """Parse argv, run the command and map errors to exit codes (nothing written on error)."""
    ns = build_parser().parse_args(argv)
    try:
        return run(ns)
    except BlocksError as exc:
        print(f"hyper_blocks.py: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
