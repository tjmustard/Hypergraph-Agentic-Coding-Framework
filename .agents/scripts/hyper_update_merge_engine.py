#!/usr/bin/env python3
"""hyper_update_merge_engine.py — Sensitive file merge orchestration with per-section approval

Implements:
- File interaction prompting (Replace/Merge/Skip)
- Markdown section parsing by ### headings (preserves code blocks)
- Per-section approval loop with diff display
- File assembly and validation (YAML, markdown)
- Summary report generation

Block-aware (S13c Task 19, R26): when the local file holds an HACF managed block, Replace and
Merge offer only the content inside that block; every byte outside it (OpenRig and RIGGED
blocks, the user's own text) is never offered and stays identical, because the result is
written back with hyper_blocks.render_upsert. A file with a foreign (OpenRig or RIGGED) block
but no HACF block is never merged: it is skipped and reported. A file with no blocks at all
keeps the whole-file merge.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import _runtime

_runtime.require_runtime()  # R19.4: before PyYAML is imported

import hyper_blocks  # noqa: E402
import yaml  # noqa: E402

# Foreign managed blocks (OpenRig, RIGGED): recognized only so they are never offered.
FOREIGN_MARKER_RE = re.compile(r"^<!-- BEGIN (OpenRig|RIGGED) [A-Z ]*BLOCK", re.MULTILINE)


@dataclass
class Section:
    """Represents a markdown section."""

    heading: str  # "### Section Name" or "" for preamble
    content: str  # Content of this section
    line_num: int  # Starting line number


class MergeEngine:
    """Handles section-by-section merging of sensitive files."""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self.changes = {
            "auto_updated": [],
            "replaced": [],
            "merged": [],
            "skipped": [],
        }

    def log_message(self, msg: str):
        """Print a message to stderr for real-time feedback."""
        print(f"[hyper-update] {msg}", file=sys.stderr)

    def error(self, msg: str):
        """Log an error and exit."""
        self.log_message(f"❌ Error: {msg}")
        sys.exit(1)

    # ==== Phase A: File Interaction Prompting ====

    def prompt_file_action(self, filename: str, diff_preview: str) -> str:
        """Prompt user for action on a file with changes.
        Returns: "replace", "merge", or "skip"
        """
        self.log_message(f"\n📝 File: {filename}")
        self.log_message("Diff preview (first 10 lines):")
        for line in diff_preview.split("\n")[:10]:
            self.log_message(f"  {line}")

        while True:
            response = input("\n[R]eplace / [M]erge / [S]kip? (r/m/s): ").lower().strip()

            if response in ("r", "replace"):
                return "replace"
            elif response in ("m", "merge"):
                return "merge"
            elif response in ("s", "skip"):
                return "skip"
            else:
                self.log_message("Invalid input. Enter r, m, or s.")

    def generate_diff_preview(self, upstream: str, local: str) -> str:
        """Generate a unified diff-style preview (simplified)."""
        upstream_lines = upstream.split("\n")
        local_lines = local.split("\n")

        diff_lines = []
        for i, (up, loc) in enumerate(zip(upstream_lines[:5], local_lines[:5])):
            if up != loc:
                diff_lines.append(f"- {up[:60]}")
                diff_lines.append(f"+ {loc[:60]}")
            else:
                diff_lines.append(f"  {up[:60]}")

        return "\n".join(diff_lines)

    # ==== Phase B: Markdown Section Parsing ====

    def parse_sections(self, content: str) -> List[Section]:
        """Parse markdown into sections by ### headings.
        Preserves code blocks (does not split within triple backticks).
        Returns: List of Section objects
        """
        lines = content.split("\n")
        sections = []
        current_section_heading = ""
        current_section_lines = []
        in_code_block = False

        for i, line in enumerate(lines):
            # Track code block state
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            # Check for section heading (only outside code blocks)
            if line.startswith("###") and not in_code_block:
                # Save previous section
                if current_section_lines or current_section_heading:
                    sections.append(
                        Section(
                            heading=current_section_heading,
                            content="\n".join(current_section_lines),
                            line_num=len(sections),
                        )
                    )

                current_section_heading = line
                current_section_lines = []
            else:
                current_section_lines.append(line)

        # Save final section
        if current_section_lines or current_section_heading:
            sections.append(
                Section(
                    heading=current_section_heading,
                    content="\n".join(current_section_lines),
                    line_num=len(sections),
                )
            )

        return sections

    # ==== Phase C: Per-Section Approval Loop ====

    def approve_sections(
        self, upstream_sections: List[Section], local_sections: List[Section]
    ) -> Dict[str, str]:
        """Show each differing section and prompt for approval.
        Returns: Dict of heading -> approved content
        """
        approved = {}
        local_by_heading = {s.heading: s.content for s in local_sections}
        upstream_by_heading = {s.heading: s.content for s in upstream_sections}

        # Process each upstream section
        total_sections = len(upstream_sections)
        approved_count = 0

        for i, upstream_sec in enumerate(upstream_sections):
            heading = upstream_sec.heading
            local_content = local_by_heading.get(heading, None)

            # If section unchanged, approve silently
            if local_content is not None and local_content == upstream_sec.content:
                approved[heading] = local_content
                approved_count += 1
                continue

            # Section differs or is new; prompt user
            self.log_message(f"\n📋 Section {approved_count + 1} of {total_sections}: {heading}")

            if local_content is None:
                self.log_message("ℹ️  New section in upstream (not in local)")
                self.log_message(f"Content:\n{upstream_sec.content[:500]}...")
                response = input("Accept upstream section? (y/n): ").lower().strip()
                if response in ("y", "yes"):
                    approved[heading] = upstream_sec.content
                else:
                    # Skip new section
                    pass
                approved_count += 1
            else:
                # Section exists in both; show diff
                self.log_message("Upstream version:")
                self.log_message(upstream_sec.content[:300])
                self.log_message("\nLocal version:")
                self.log_message(local_content[:300])

                while True:
                    response = (
                        input("\n[K]eep local / [A]ccept upstream / [E]dit manually? (k/a/e): ")
                        .lower()
                        .strip()
                    )

                    if response in ("k", "keep"):
                        approved[heading] = local_content
                        break
                    elif response in ("a", "accept"):
                        approved[heading] = upstream_sec.content
                        break
                    elif response in ("e", "edit"):
                        edited = input("Paste edited content:\n")
                        approved[heading] = edited
                        break
                    else:
                        self.log_message("Invalid input. Enter k, a, or e.")

                approved_count += 1

        return approved

    # ==== Phase D: File Assembly & Validation ====

    def assemble_file(self, approved_sections: Dict[str, str]) -> str:
        """Assemble approved sections into final file content."""
        parts = []
        for heading, content in approved_sections.items():
            if heading:  # Include heading if present
                parts.append(heading)
            parts.append(content)

        return "\n".join(parts)

    def validate_file(self, filename: str, content: str) -> Tuple[bool, str]:
        """Validate merged file for syntax errors.
        Returns: (is_valid, error_message)
        """
        if filename.endswith(".yml") or filename.endswith(".yaml"):
            try:
                yaml.safe_load(content)
                return True, ""
            except yaml.YAMLError as e:
                return False, f"YAML syntax error: {e}"

        if filename.endswith(".md"):
            # Check for unmatched code block delimiters
            backtick_count = content.count("```")
            if backtick_count % 2 != 0:
                return (
                    False,
                    "Markdown has unmatched code block delimiters (```)",
                )

        return True, ""

    def handle_validation_failure(self, filename: str, error: str) -> str:
        """Prompt user after validation failure."""
        self.log_message(f"\n❌ Validation failed: {error}")

        while True:
            response = (
                input(
                    "What would you like to do?\n[K]eep original / [R]e-merge / [A]ccept anyway? (k/r/a): "
                )
                .lower()
                .strip()
            )

            if response in ("k", "keep"):
                return "keep_original"
            elif response in ("r", "remerge"):
                return "remerge"
            elif response in ("a", "accept"):
                return "accept_anyway"
            else:
                self.log_message("Invalid input. Enter k, r, or a.")

    # ==== Phase E: Summary Report ====

    def generate_summary(self, backup_path: Optional[str] = None) -> str:
        """Generate upgrade summary report."""
        lines = ["\n📊 Upgrade Summary"]
        lines.append(f"✅ Auto-updated: {len(self.changes['auto_updated'])} files")
        if self.changes["auto_updated"]:
            for f in self.changes["auto_updated"][:3]:
                lines.append(f"   - {f}")
            if len(self.changes["auto_updated"]) > 3:
                lines.append(f"   ... and {len(self.changes['auto_updated']) - 3} more")

        lines.append(f"✅ Replaced: {len(self.changes['replaced'])} file(s)")
        for f in self.changes["replaced"]:
            lines.append(f"   - {f}")

        lines.append(f"✅ Merged: {len(self.changes['merged'])} file(s)")
        for f in self.changes["merged"]:
            lines.append(f"   - {f}")

        lines.append(f"⏭️  Skipped: {len(self.changes['skipped'])} file(s)")
        for f in self.changes["skipped"]:
            lines.append(f"   - {f}")

        if backup_path:
            lines.append(f"\n🔐 Backup: {backup_path}")

        return "\n".join(lines)

    # ==== Main Orchestration ====

    def _hacf_block(self, filename: str, content: str):
        """Return (doc, style, block) for the file's first HACF managed block, or None."""
        try:
            style = hyper_blocks.style_of(Path(filename))
        except hyper_blocks.BlocksError:
            return None
        doc = hyper_blocks.parse_doc(content.encode("utf-8"))
        try:
            blocks = hyper_blocks.scan(doc, style)
        except hyper_blocks.BlocksError as exc:
            raise ValueError(f"{filename}: {exc}") from None
        managed = [b for b in blocks if b.kind == hyper_blocks.MANAGED]
        return (doc, style, managed[0]) if managed else None

    def _upstream_body(self, filename: str, upstream_content: str, block_id: str) -> str:
        """Return the upstream text for a block: the same block upstream, else the whole file."""
        found = self._hacf_block(filename, upstream_content)
        if found is not None and found[2].block_id == block_id:
            doc, _, block = found
            return "\n".join(line.decode("utf-8") for line in hyper_blocks.body_of(doc, block))
        return upstream_content.rstrip("\n")

    def merge_file(
        self,
        filename: str,
        upstream_content: str,
        local_content: str,
        backup_dir: Optional[Path] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Orchestrate merge for a single file (block-aware, Task 19).

        Returns: (success, final_content or None)
        """
        try:
            found = self._hacf_block(filename, local_content)
        except ValueError as exc:
            self.log_message(f"⏭️  Skipped {filename}: {exc}")
            self.changes["skipped"].append(filename)
            return True, None
        if found is None and FOREIGN_MARKER_RE.search(local_content):
            self.log_message(
                f"⏭️  Skipped {filename}: it holds an OpenRig/RIGGED block and no HACF block, "
                "so HACF never offers any of its content"
            )
            self.changes["skipped"].append(filename)
            return True, None
        if found is None:
            return self._merge_text(filename, upstream_content, local_content, backup_dir)

        doc, style, block = found
        local_body = "\n".join(line.decode("utf-8") for line in hyper_blocks.body_of(doc, block))
        upstream_body = self._upstream_body(filename, upstream_content, block.block_id)
        if local_body == upstream_body:
            return True, None
        self.log_message(
            f"Only HACF block '{block.block_id}' of {filename} is offered; "
            "everything outside it stays byte-identical."
        )
        ok, merged_body = self._merge_text(
            f"{filename} [HACF block {block.block_id}]",
            upstream_body,
            local_body,
            None,
            record=filename,
        )
        if merged_body is None:
            return ok, None
        new_doc = hyper_blocks.render_upsert(
            doc, style, block.block_id, hyper_blocks.body_lines(merged_body)
        )
        final_content = new_doc.to_bytes().decode("utf-8")
        if backup_dir:
            self._backup_file(filename, local_content, backup_dir)
        return True, final_content

    def _merge_text(
        self,
        filename: str,
        upstream_content: str,
        local_content: str,
        backup_dir: Optional[Path] = None,
        record: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Replace / merge by ### section / skip one text (a whole file or a block body)."""
        record = record or filename
        action = self.prompt_file_action(
            filename, self.generate_diff_preview(upstream_content, local_content)
        )

        if action == "skip":
            self.changes["skipped"].append(record)
            return True, None

        if action == "replace":
            if backup_dir:
                self._backup_file(filename, local_content, backup_dir)
            self.changes["replaced"].append(record)
            return True, upstream_content

        # action == "merge"
        upstream_sections = self.parse_sections(upstream_content)
        local_sections = self.parse_sections(local_content)
        approved = self.approve_sections(upstream_sections, local_sections)
        final_content = self.assemble_file(approved)
        is_valid, error_msg = self.validate_file(record, final_content)

        if not is_valid:
            choice = self.handle_validation_failure(filename, error_msg)
            if choice == "keep_original":
                self.changes["skipped"].append(record)
                return True, None
            if choice == "remerge":
                return self._merge_text(
                    filename, upstream_content, local_content, backup_dir, record
                )
            # accept_anyway: proceed with writing

        if backup_dir:
            self._backup_file(filename, local_content, backup_dir)

        self.changes["merged"].append(record)
        self.log_message(f"✅ Merged {filename} (validation: pass)")
        return True, final_content

    def _backup_file(self, filename: str, content: str, backup_dir: Path):
        """Create backup of original file."""
        backup_path = backup_dir / filename
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with open(backup_path, "w") as f:
            f.write(content)
