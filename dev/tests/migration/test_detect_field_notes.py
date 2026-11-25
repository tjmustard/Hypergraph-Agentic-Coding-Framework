"""`hyper_migrate.py detect` raises legacy_field_notes only when Task 13 would move an entry (D2).

Every project is a temp git repository built with the v0.5.9 fixture builder, with this
checkout's scripts installed (see test_migrate_stores.py). Nothing runs `detect` or `migrate` on
this checkout.
"""

from __future__ import annotations

from pathlib import Path

import test_migrate_specs as specs
import test_migrate_stores as stores

ACTIVE = ".agents/memory/activeContext.md"
SIGNAL = "legacy_field_notes:"
MAPPED = "spec/compiled/MiniPRD_01_Invoices.md"
UNMAPPED = "spec/compiled/MiniPRD_99_Gone.md"
NOTE_TEXT = "Invoices need idempotent retries."


def _project(tmp_path: Path) -> Path:
    return stores.build(tmp_path)


def _note(project: Path, source: str, text: str = NOTE_TEXT) -> str:
    """Write one entry the way `hyper_lore.py note --legacy` does; return its line."""
    proc = specs.tool(
        project, "hyper_lore.py", "note", "--legacy", "--source", source,
        "--agent", "hyper-execute", "--text", text, "--kind", "field-note",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = (project / ACTIVE).read_text(encoding="utf-8").splitlines()
    return next(line for line in lines if text in line)


def _detect(project: Path) -> str:
    proc = specs.migrate(project, "detect")
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    return proc.stdout


def test_a_real_entry_with_a_mapped_source_gives_the_signal(tmp_path: Path) -> None:
    """(a) an entry as hyper_lore writes it, with a mapped source, raises the signal."""
    project = _project(tmp_path)
    _note(project, MAPPED)
    assert SIGNAL in _detect(project)


def test_b_after_migrate_moved_the_entry_there_is_no_signal(tmp_path: Path) -> None:
    """(b) after migrate moved the entry (heading kept), detect is quiet."""
    project = _project(tmp_path)
    entry = _note(project, MAPPED)
    specs.commit(project, "fixture: one legacy note")
    stores.apply_yes(project)
    text = (project / ACTIVE).read_text(encoding="utf-8")
    assert entry not in text and "## Field Notes (legacy)" in text  # moved, heading kept
    assert SIGNAL not in _detect(project)


def test_c_a_free_text_bullet_under_the_heading_gives_no_signal(tmp_path: Path) -> None:
    """(c) a user's free-text bullet is not an entry Task 13 moves, so no signal."""
    project = _project(tmp_path)
    _note(project, MAPPED)
    path = project / ACTIVE
    text = path.read_text(encoding="utf-8")
    # The user replaced the entry with their own bullet: not an entry Task 13 can move.
    entry = next(line for line in text.splitlines() if NOTE_TEXT in line)
    path.write_text(text.replace(entry, "- remember to rotate the invoice keys"), encoding="utf-8")
    assert SIGNAL not in _detect(project)


def test_d_an_entry_with_an_unmapped_source_gives_no_signal(tmp_path: Path) -> None:
    """(d) an unmapped source is left by Task 13, so detect does not promise a move."""
    project = _project(tmp_path)
    entry = _note(project, UNMAPPED, "An orphan note.")
    specs.commit(project, "fixture: one unmapped legacy note")
    assert SIGNAL not in _detect(project)
    proc = stores.apply_yes(project)  # Task 13 indeed leaves it
    assert entry in (project / ACTIVE).read_text(encoding="utf-8").splitlines()
    assert f"no migrated slice for {UNMAPPED}" in proc.stdout
