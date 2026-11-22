"""Deterministic tests for hyper_blocks.py, the managed-block engine (S12a; R9, R22, R26).

Every file lives under tmp_path. Each block test asserts that the bytes outside the named block
are identical before and after (assert_outside_identical). The interactive cases inject a TTY
opener; the CLI cases run the script as a subprocess under this interpreter.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import hyper_blocks
import pytest
from conftest import SCRIPTS_DIR, clean_env

SCRIPT = SCRIPTS_DIR / "hyper_blocks.py"
REPO_ROOT = SCRIPTS_DIR.parent.parent
OPENRIG = (
    "<!-- BEGIN OpenRig MANAGED BLOCK: role -->\n"
    "You are a seat.\n"
    "<!-- END OpenRig MANAGED BLOCK: role -->\n"
)
BEGIN = "<!-- BEGIN HACF MANAGED BLOCK: hacf -->"
END = "<!-- END HACF MANAGED BLOCK: hacf -->"
TEMPLATE_COMMITS = ("626a28e", "cd8ae6a", "b3b1634", "329e8f7", "0b19f16", "984ca46")


def cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run hyper_blocks.py with argv under this interpreter, with no TTY on stdin."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        env=clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def outside(data: bytes, block_id: str, style: str = hyper_blocks.MARKDOWN) -> bytes:
    """Return data with block_id's marker and body lines cut out (all of data when absent)."""
    doc = hyper_blocks.parse_doc(data)
    block = hyper_blocks.find_block(hyper_blocks.scan(doc, style), block_id)
    if block is None:
        return data
    return doc.bom + b"".join(doc.lines[: block.begin] + doc.lines[block.end + 1 :])


def assert_outside_identical(
    before: bytes, after: bytes, block_id: str, style: str = hyper_blocks.MARKDOWN
) -> None:
    """Assert the bytes outside block_id are identical; an appended block may add one gap line."""
    old, new = outside(before, block_id, style), outside(after, block_id, style)
    if old == new:
        return
    # A newly appended block: the old bytes are a prefix, followed only by line endings.
    assert new.startswith(old.rstrip(b"\r\n")), (old, new)
    assert new[len(old.rstrip(b"\r\n")) :].strip(b"\r\n") == b"", (old, new)


def write(path: Path, data: bytes | str) -> Path:
    """Write bytes (or UTF-8 text) to path and return it."""
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


class FakeTty(io.StringIO):
    """A TTY stand-in: answers are read from the given text, prompts are collected."""

    def __init__(self, answers: str) -> None:
        """Hold the answers and an output buffer."""
        super().__init__(answers)
        self.prompts: list[str] = []
        self.closed_by_caller = False

    def write(self, text: str) -> int:  # type: ignore[override]
        """Record a prompt instead of writing into the answers."""
        self.prompts.append(text)
        return len(text)

    def close(self) -> None:
        """Record the close and keep the buffer readable for assertions."""
        self.closed_by_caller = True


def tty_answering(answer: str) -> tuple[list[FakeTty], callable]:
    """Return the opened TTYs and an opener that answers `answer`."""
    opened: list[FakeTty] = []

    def opener() -> FakeTty:
        tty = FakeTty(answer + "\n")
        opened.append(tty)
        return tty

    return opened, opener


# ---------------------------------------------------------------------------
# Task 25: byte preservation
# ---------------------------------------------------------------------------


def test_blocks_preserve_openrig(tmp_path: Path) -> None:
    """An HACF upsert next to an OpenRig block keeps that block and every other byte."""
    path = write(tmp_path / "AGENTS.md", "# Project\n\n" + OPENRIG + "\nTrailing user text.\n")
    before = path.read_bytes()
    outcome = hyper_blocks.upsert(path, "hacf", "HACF rules v1\n")
    assert outcome.action == "upserted"
    after = path.read_bytes()
    assert OPENRIG.encode() in after
    assert_outside_identical(before, after, "hacf")
    assert after.endswith(f"{BEGIN}\nHACF rules v1\n{END}\n".encode())

    # Replacing the body: everything outside the block stays byte-identical.
    hyper_blocks.upsert(path, "hacf", "HACF rules v2\nsecond line\n")
    again = path.read_bytes()
    assert outside(again, "hacf") == outside(after, "hacf")
    assert b"HACF rules v2\nsecond line\n" in again and b"v1" not in again
    assert hyper_blocks.upsert(path, "hacf", "HACF rules v2\nsecond line\n").action == "unchanged"


def test_blocks_crlf_preserved(tmp_path: Path) -> None:
    """A CRLF file stays CRLF, including the new block lines."""
    path = write(tmp_path / "AGENTS.md", b"# Title\r\n\r\nUser text\r\n")
    before = path.read_bytes()
    hyper_blocks.upsert(path, "hacf", "line one\nline two\n")
    after = path.read_bytes()
    assert after.count(b"\n") == after.count(b"\r\n")
    assert f"{BEGIN}\r\nline one\r\nline two\r\n{END}\r\n".encode() in after
    assert_outside_identical(before, after, "hacf")
    hyper_blocks.upsert(path, "hacf", "replaced\n")
    final = path.read_bytes()
    assert final.count(b"\n") == final.count(b"\r\n")
    assert outside(final, "hacf") == outside(after, "hacf")


def test_blocks_bom_preserved(tmp_path: Path) -> None:
    """A UTF-8 BOM survives an upsert, and a removal."""
    path = write(tmp_path / "CLAUDE.md", b"\xef\xbb\xbf# Title\n")
    before = path.read_bytes()
    hyper_blocks.upsert(path, "hacf", "body\n")
    after = path.read_bytes()
    assert after.startswith(b"\xef\xbb\xbf# Title\n")
    assert after.count(b"\xef\xbb\xbf") == 1
    assert_outside_identical(before, after, "hacf")
    hyper_blocks.remove(path, "hacf")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_blocks_missing_final_newline_preserved(tmp_path: Path) -> None:
    """A file without a final newline still has none after an upsert (append and replace).

    Remove keeps every byte outside the block (Task 6): the line ending and separator line the
    append added stay, so the file ends with an empty line rather than with no final newline.
    """
    path = write(tmp_path / ".gitignore", b"node_modules/\n*.log")
    before = path.read_bytes()
    hyper_blocks.upsert(path, "gitignore", "/exhaust/\n")
    after = path.read_bytes()
    assert not after.endswith(b"\n")
    assert after.endswith(b"# END HACF MANAGED BLOCK: gitignore")
    assert_outside_identical(before, after, "gitignore", hyper_blocks.HASH)
    hyper_blocks.upsert(path, "gitignore", "/exhaust/\n/.openrig/\n")
    replaced = path.read_bytes()
    assert not replaced.endswith(b"\n")
    assert outside(replaced, "gitignore", hyper_blocks.HASH) == outside(
        after, "gitignore", hyper_blocks.HASH
    )
    hyper_blocks.remove(path, "gitignore")
    assert path.read_bytes() == b"node_modules/\n*.log\n\n"


def test_blocks_remove_keeps_the_rest(tmp_path: Path) -> None:
    """Remove deletes the block and its markers; every other byte stays (S12c's path)."""
    text = "# Title\n\n" + OPENRIG + f"\n{BEGIN}\nbody\n{END}\nafter\n"
    path = write(tmp_path / "AGENTS.md", text)
    assert hyper_blocks.remove(path, "hacf").action == "removed"
    assert path.read_text() == "# Title\n\n" + OPENRIG + "\nafter\n"
    assert hyper_blocks.remove(path, "hacf").action == "absent"


def test_blocks_remove_keeps_empty_line_before_eof_block(tmp_path: Path) -> None:
    """A block that ends the file: remove keeps the empty line before it (Task 6, US-001)."""
    path = write(tmp_path / "CLAUDE.md", b"x\n\n")
    hyper_blocks.upsert(path, "hacf", "z\n")
    assert path.read_bytes() == f"x\n\n{BEGIN}\nz\n{END}\n".encode()  # no separator added
    hyper_blocks.remove(path, "hacf")
    assert path.read_bytes() == b"x\n\n"

    placed = write(tmp_path / "AGENTS.md", f"x\n\n{BEGIN}\nz\n{END}\n")
    assert hyper_blocks.remove(placed, "hacf").action == "removed"
    assert placed.read_bytes() == b"x\n\n"


def test_blocks_remove_eof_block_without_final_newline(tmp_path: Path) -> None:
    """A block ending a file with no final newline: remove keeps the user's line ending (U1b)."""
    cases = {
        f"x\n\n{BEGIN}\nz\n{END}": b"x\n\n",
        f"x\n{BEGIN}\nz\n{END}": b"x\n",
        f"x\r\n{BEGIN}\r\nz\r\n{END}": b"x\r\n",
    }
    for index, (text, expected) in enumerate(cases.items()):
        (tmp_path / f"case{index}").mkdir()
        path = write(tmp_path / f"case{index}" / "AGENTS.md", text.encode())
        assert hyper_blocks.remove(path, "hacf").action == "removed"
        assert path.read_bytes() == expected


# ---------------------------------------------------------------------------
# Task 26: what is not a marker
# ---------------------------------------------------------------------------


def test_blocks_marker_in_fenced_code_ignored(tmp_path: Path) -> None:
    """A marker inside a fenced code block is not a block; an upsert appends a real one."""
    for fence in ("```", "~~~"):
        fenced = f"# Docs\n\n{fence}markdown\n{BEGIN}\nexample\n{END}\n{fence}\n"
        path = write(tmp_path / "AGENTS.md", fenced)
        assert hyper_blocks.check_file(path) == []
        before = path.read_bytes()
        hyper_blocks.upsert(path, "hacf", "real\n")
        after = path.read_bytes()
        assert after.startswith(fenced.encode())  # the example is untouched
        assert after.endswith(f"\n{BEGIN}\nreal\n{END}\n".encode())
        assert_outside_identical(before, after, "hacf")
        blocks = hyper_blocks.check_file(path)
        assert len(blocks) == 1 and blocks[0].begin > fenced.count("\n")


def test_blocks_indented_marker_ignored(tmp_path: Path) -> None:
    """An indented marker is not a block (and so is not unbalanced either)."""
    text = f"# Title\n  {BEGIN}\n  body\n\t{END}\n"
    path = write(tmp_path / "AGENTS.md", text)
    assert hyper_blocks.check_file(path) == []
    before = path.read_bytes()
    hyper_blocks.upsert(path, "hacf", "real\n")
    after = path.read_bytes()
    assert after.startswith(text.encode())
    assert_outside_identical(before, after, "hacf")


def test_blocks_case_and_prefix_not_matched(tmp_path: Path) -> None:
    """Lowercase, prefixed, suffixed and OpenRig markers are never HACF blocks (R26)."""
    lookalikes = (
        "<!-- BEGIN hacf MANAGED BLOCK: hacf -->\nx\n<!-- END hacf MANAGED BLOCK: hacf -->\n"
        "<!-- begin HACF MANAGED BLOCK: hacf -->\n"
        f"{BEGIN} trailing\n"
        f"x{BEGIN}\n"
        "<!-- BEGIN HACF MANAGED BLOCK: HACF -->\n"
        "<!-- BEGIN OpenRig MANAGED BLOCK: role -->\nr\n<!-- END OpenRig MANAGED BLOCK: role -->\n"
    )
    path = write(tmp_path / "AGENTS.md", lookalikes)
    assert hyper_blocks.check_file(path) == []
    before = path.read_bytes()
    assert hyper_blocks.remove(path, "hacf").action == "absent"
    assert path.read_bytes() == before
    hyper_blocks.upsert(path, "hacf", "body\n")
    after = path.read_bytes()
    assert after.startswith(before)
    assert_outside_identical(before, after, "hacf")
    # A hash-comment file does not take Markdown markers, nor lowercase hash markers.
    ignore = write(
        tmp_path / ".gitignore", "# begin hacf managed block: gitignore\n" + BEGIN + "\n"
    )
    assert hyper_blocks.check_file(ignore) == []


# ---------------------------------------------------------------------------
# Task 27: refusals, nothing written
# ---------------------------------------------------------------------------


def assert_refused(path: Path, *args: str) -> None:
    """Assert check and upsert exit 5 through the CLI and leave path's bytes and mtime alone."""
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    body = write(path.parent / "body.txt", "new\n")
    for argv in (
        ("check", str(path)),
        ("upsert", str(path), "--id", "hacf", "--body-file", str(body)),
    ):
        proc = cli(*argv, *args, cwd=path.parent)
        assert proc.returncode == 5, proc.stdout + proc.stderr
    with pytest.raises(hyper_blocks.BlockError):
        hyper_blocks.upsert(path, "hacf", "new\n")
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
    assert [p.name for p in path.parent.iterdir() if "hacf-tmp" in p.name] == []


def test_blocks_duplicate_refused(tmp_path: Path) -> None:
    """Two blocks with one id: exit 5, file unchanged."""
    block = f"{BEGIN}\na\n{END}\n"
    assert_refused(write(tmp_path / "AGENTS.md", "# T\n" + block + "middle\n" + block))


def test_blocks_nested_in_openrig_refused(tmp_path: Path) -> None:
    """An HACF block inside an OpenRig block, or one HACF block in another: exit 5."""
    nested = (
        "<!-- BEGIN OpenRig MANAGED BLOCK: role -->\n"
        f"{BEGIN}\nx\n{END}\n"
        "<!-- END OpenRig MANAGED BLOCK: role -->\n"
    )
    assert_refused(write(tmp_path / "AGENTS.md", nested))
    inner = "<!-- BEGIN HACF MANAGED BLOCK: other -->\ny\n<!-- END HACF MANAGED BLOCK: other -->\n"
    assert_refused(write(tmp_path / "CLAUDE.md", f"{BEGIN}\n{inner}{END}\n"))


def test_blocks_unbalanced_refused(tmp_path: Path) -> None:
    """A BEGIN without END, an END without BEGIN, or a mismatched pair: exit 5."""
    assert_refused(write(tmp_path / "a.md", f"# T\n{BEGIN}\nbody\n"))
    assert_refused(write(tmp_path / "b.md", f"# T\nbody\n{END}\n"))
    mismatched = f"{BEGIN}\nbody\n<!-- END HACF MANAGED BLOCK: other -->\n"
    assert_refused(write(tmp_path / "c.md", mismatched))
    hash_file = write(tmp_path / ".gitignore", "# BEGIN HACF MANAGED BLOCK: gitignore\nx\n")
    with pytest.raises(hyper_blocks.BlockError):
        hyper_blocks.check_file(hash_file)


def test_blocks_cas_refuses_concurrent_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file changes between read and replace (an injected hook): exit 5, theirs kept."""
    path = write(tmp_path / "AGENTS.md", "# Title\n\n" + OPENRIG)
    concurrent = b"# Title\n\n<!-- BEGIN OpenRig MANAGED BLOCK: role -->\nrewritten by rig\n"
    calls: list[Path] = []

    def hook(target: Path) -> None:
        calls.append(target)
        target.write_bytes(concurrent)

    monkeypatch.setattr(hyper_blocks, "BEFORE_REPLACE_HOOK", hook)
    with pytest.raises(hyper_blocks.Refused) as excinfo:
        hyper_blocks.upsert(path, "hacf", "body\n")
    assert excinfo.value.code == 5
    assert calls == [path]
    assert path.read_bytes() == concurrent
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AGENTS.md"]  # no temp file left


def test_instruction_replace_lost_race_leaves_no_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interactive (a) whose replace loses the CAS race: exit 5 and no .v059.bak left."""
    custom = b"# Our AGENTS.md\n\nWe wrote this ourselves.\n"
    path = write(tmp_path / "AGENTS.md", custom)
    concurrent = b"# Our AGENTS.md\n\nEdited meanwhile.\n"

    def hook(target: Path) -> None:
        if target == path:
            target.write_bytes(concurrent)

    monkeypatch.setattr(hyper_blocks, "BEFORE_REPLACE_HOOK", hook)
    _, opener = tty_answering("a")
    with pytest.raises(hyper_blocks.Refused) as excinfo:
        hyper_blocks.decide_instruction_file(path, "rules\n", tty_opener=opener)
    assert excinfo.value.code == 5
    assert path.read_bytes() == concurrent
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AGENTS.md"]


def test_blocks_unsupported_file_is_usage_error(tmp_path: Path) -> None:
    """A file that is neither Markdown nor a hash-comment file exits 2, nothing written."""
    path = write(tmp_path / "settings.json", "{}\n")
    body = write(tmp_path / "body.txt", "x\n")
    proc = cli("upsert", str(path), "--id", "hacf", "--body-file", str(body), cwd=tmp_path)
    assert proc.returncode == 2
    assert path.read_text() == "{}\n"
    proc = cli(
        "upsert", str(tmp_path / "x.md"), "--id", "Bad_Id", "--body-file", str(body), cwd=tmp_path
    )
    assert proc.returncode == 2
    assert not (tmp_path / "x.md").exists()


# ---------------------------------------------------------------------------
# Task 28: user edits, CLAUDE.local.md and the R9 decision
# ---------------------------------------------------------------------------


def test_blocks_user_edited_block_backup_and_report(tmp_path: Path) -> None:
    """A user-edited body: backup and ask when interactive; left and reported under -y."""
    project = tmp_path / "project"
    project.mkdir()
    shipped = "shipped line\n"
    manifest_sha = hyper_blocks.body_sha256(hyper_blocks.body_lines(shipped))
    edited = f"# T\n{BEGIN}\nshipped line\nmy own line\n{END}\n"
    path = write(project / "AGENTS.md", edited)
    before = path.read_bytes()

    # --yes: left in place, a `left:` report line, exit 0, nothing written.
    body = write(tmp_path / "body.txt", "new shipped\n")
    proc = cli(
        "upsert", str(path), "--id", "hacf", "--body-file", str(body),
        "--manifest-sha", manifest_sha, "--yes", cwd=project,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("left: ")
    assert path.read_bytes() == before
    assert not (project / ".agents").exists()

    # Interactive, answer "keep" (the default is keep too): a backup and the question.
    opened, opener = tty_answering("")
    outcome = hyper_blocks.upsert(
        path, "hacf", "new shipped\n", manifest_sha=manifest_sha, tty_opener=opener
    )
    assert outcome.action == "left"
    assert path.read_bytes() == before
    backups = list((project / ".agents" / ".backup").glob("*/AGENTS.md"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert any("Keep your edits" in p for p in opened[0].prompts)
    assert opened[0].closed_by_caller

    # Interactive, answer "replace": the block is rewritten, bytes outside it kept.
    _, opener = tty_answering("replace")
    outcome = hyper_blocks.upsert(
        path, "hacf", "new shipped\n", manifest_sha=manifest_sha, tty_opener=opener
    )
    assert outcome.action == "upserted"
    assert b"new shipped\n" in path.read_bytes() and b"my own line" not in path.read_bytes()
    assert_outside_identical(before, path.read_bytes(), "hacf")

    # An unedited block (sha matches) is replaced without a question.
    current = hyper_blocks.body_sha256(hyper_blocks.body_lines("new shipped\n"))
    outcome = hyper_blocks.upsert(path, "hacf", "v3\n", manifest_sha=current, assume_yes=True)
    assert outcome.action == "upserted"


def test_claude_local_md_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any operation on CLAUDE.local.md exits 5; its bytes and mtime are unchanged."""
    path = write(tmp_path / "CLAUDE.local.md", f"# mine\n{BEGIN}\nx\n{END}\n")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    body = write(tmp_path / "body.txt", "x\n")
    for argv in (
        ("check", str(path)),
        ("upsert", str(path), "--id", "hacf", "--body-file", str(body)),
        ("remove", str(path), "--id", "hacf"),
        ("instruction", str(path), "--block-file", str(body), "--yes"),
    ):
        proc = cli(*argv, cwd=tmp_path)
        assert proc.returncode == 5, (argv, proc.stdout, proc.stderr)
        assert "CLAUDE.local.md" in proc.stderr

    def no_open(*args, **kwargs):
        raise AssertionError("CLAUDE.local.md was opened")

    monkeypatch.setattr(Path, "read_bytes", no_open)
    for call in (
        lambda: hyper_blocks.upsert(path, "hacf", "x\n"),
        lambda: hyper_blocks.remove(path, "hacf"),
        lambda: hyper_blocks.check_file(path),
        lambda: hyper_blocks.decide_instruction_file(path, "x\n", assume_yes=True),
    ):
        with pytest.raises(hyper_blocks.Refused):
            call()
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def _git_show(commit: str, rel: str) -> bytes:
    """Return a file's bytes at a commit of this repository."""
    return subprocess.run(
        ["git", "show", f"{commit}:{rel}"],
        cwd=str(REPO_ROOT),
        env=clean_env(),
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout


def test_v059_template_detection(tmp_path: Path) -> None:
    """Every release's AGENTS.md, CLAUDE.md, GEMINI.md is known; one changed byte is not."""
    for name in hyper_blocks.INSTRUCTION_FILES:
        known, _ = hyper_blocks.known_template_shas(name)
        for commit in TEMPLATE_COMMITS:
            data = _git_show(commit, f".agents/install-templates/{name}")
            assert hashlib.sha256(data).hexdigest() in known, (commit, name)
            project = tmp_path / f"{commit}-{name}"
            project.mkdir()
            path = write(project / name, data)
            outcome = hyper_blocks.decide_instruction_file(
                path, "HACF v0.6.0\n", assume_yes=True, root=project
            )
            assert outcome.action == "replaced"
            assert path.read_text() == f"{BEGIN}\nHACF v0.6.0\n{END}\n"
            changed = project / "changed"
            changed.mkdir()
            one_byte = write(changed / name, data[:-1] + bytes([data[-1] ^ 1]))
            assert hashlib.sha256(one_byte.read_bytes()).hexdigest() not in known
            outcome = hyper_blocks.decide_instruction_file(
                one_byte, "HACF v0.6.0\n", assume_yes=True, root=changed
            )
            assert outcome.action == "sidecar"
    # legacy_file_hashes.json entries are accepted too (S12c writes the file).
    project = tmp_path / "configured"
    (project / ".agents" / "config").mkdir(parents=True)
    custom = b"# a template from a release this list does not know\n"
    sha = hashlib.sha256(custom).hexdigest()
    write(project / ".agents" / "config" / "legacy_file_hashes.json", f'{{"AGENTS.md": ["{sha}"]}}')
    known, configured = hyper_blocks.known_template_shas("AGENTS.md", project)
    assert configured and sha in known


def test_unknown_sha_whole_file_never_appended(tmp_path: Path) -> None:
    """Interactive replace writes AGENTS.md.v059.bak then the block; -y writes the sidecar."""
    custom = b"# Our AGENTS.md\n\nWe wrote this ourselves.\n"
    yes_dir = tmp_path / "yes"
    yes_dir.mkdir()
    path = write(yes_dir / "AGENTS.md", custom)
    block = write(tmp_path / "block.md", "HACF v0.6.0 rules\n")
    proc = cli("instruction", str(path), "--block-file", str(block), "--yes", cwd=yes_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("sidecar: ")
    assert "run HACF-install.sh to complete the upgrade" in proc.stderr  # R24.10 notice
    assert path.read_bytes() == custom
    sidecar = yes_dir / "AGENTS.hacf-v0.6.0.md"
    text = sidecar.read_text()
    assert "copy the block below" in text
    assert text.endswith(f"{BEGIN}\nHACF v0.6.0 rules\n{END}\n")
    assert sorted(p.name for p in yes_dir.iterdir()) == ["AGENTS.hacf-v0.6.0.md", "AGENTS.md"]

    # No TTY at all behaves like --yes.
    nontty = tmp_path / "nontty"
    nontty.mkdir()
    path = write(nontty / "CLAUDE.md", custom)
    outcome = hyper_blocks.decide_instruction_file(
        path, "rules\n", interactive=True, tty_opener=lambda: None
    )
    assert outcome.action == "sidecar" and path.read_bytes() == custom
    assert (nontty / "CLAUDE.hacf-v0.6.0.md").is_file()

    # Interactive (a): the backup first, then the whole file is the block.
    replace_dir = tmp_path / "replace"
    replace_dir.mkdir()
    path = write(replace_dir / "AGENTS.md", custom)
    opened, opener = tty_answering("a")
    outcome = hyper_blocks.decide_instruction_file(path, "rules\n", tty_opener=opener)
    assert outcome.action == "replaced-with-backup"
    assert (replace_dir / "AGENTS.md.v059.bak").read_bytes() == custom
    assert path.read_text() == f"{BEGIN}\nrules\n{END}\n"
    assert not (replace_dir / "AGENTS.hacf-v0.6.0.md").exists()
    assert "AGENTS.md.v059.bak" in "".join(opened[0].prompts)

    # Interactive (b), and the default (an empty answer), leave the file and write the sidecar.
    for answer in ("b", ""):
        side_dir = tmp_path / f"side-{answer or 'default'}"
        side_dir.mkdir()
        path = write(side_dir / "GEMINI.md", custom)
        _, opener = tty_answering(answer)
        assert hyper_blocks.decide_instruction_file(path, "r\n", tty_opener=opener).action == (
            "sidecar"
        )
        assert path.read_bytes() == custom
    # Never an append: no outcome leaves the custom text with an HACF block after it.


def test_instruction_decision_created_and_upserted(tmp_path: Path) -> None:
    """A missing file is created with only the block; a file with markers gets an upsert."""
    path = tmp_path / "AGENTS.md"
    assert hyper_blocks.decide_instruction_file(path, "r1\n", assume_yes=True).action == "created"
    assert path.read_text() == f"{BEGIN}\nr1\n{END}\n"
    write(path, "# Mine\n\n" + OPENRIG + f"\n{BEGIN}\nr1\n{END}\n")
    before = path.read_bytes()
    assert hyper_blocks.decide_instruction_file(path, "r2\n", assume_yes=True).action == "upserted"
    assert_outside_identical(before, path.read_bytes(), "hacf")
    with pytest.raises(hyper_blocks.UsageError):
        hyper_blocks.decide_instruction_file(tmp_path / "README.md", "x\n")


def test_cas_write_keeps_mode(tmp_path: Path) -> None:
    """A rewrite keeps the file mode (R22.3)."""
    path = write(tmp_path / ".gitignore", "a\n")
    os.chmod(path, 0o640)
    hyper_blocks.upsert(path, "gitignore", "b\n")
    assert path.stat().st_mode & 0o777 == 0o640
