---
name: contextualize
description: Audits and fixes HACF agent instruction files (CLAUDE.md, AGENTS.md, GEMINI.md) in an installed project to ensure HACF is framed as a development toolchain, not the project subject. Use when HACF content bleeds into project plans, PRDs, or architecture docs.
---

# Hyper-Contextualize

This skill audits the installed HACF agent instruction files (`CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`) to verify they frame HACF as a **development toolchain** rather than the
project subject. It also checks `README.md` for HACF context bleed.

**What HACF writes, and where.** HACF writes `lore/`, `missions/`, the root `SPEC.md` and the
OpenRig manifests (`project.yaml`, `workspace.yaml`) only through its own scripts, never by hand from
this skill. In `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` it edits only inside its own managed blocks,
whose markers are exact and case-sensitive:

```
<!-- BEGIN HACF MANAGED BLOCK: <id> -->
<!-- END HACF MANAGED BLOCK: <id> -->
```

OpenRig and RIGGED blocks, and every byte outside the HACF block, stay byte-for-byte as they are.
`CLAUDE.local.md` is never touched.

Run this skill:
- After installing HACF into an existing project for the first time
- When you notice the AI treating HACF skills/schemas as the project's own features
- After `/hyper-update` if you merged agent files and the framing may have been lost

---

## Step 1 — Pre-flight Check

Verify `.agents/` exists in the current directory. If not, halt with:
`ERROR: No .agents/ directory found. This is not an HACF project.`

---

## Step 2 — Scan Agent Files

Read each of the following from the project root if they exist:
`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `README.md`

**For each agent file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`), check:**

1. **Framing banner present?** Look for the string `HACF as a Toolchain` near the top of the file.
2. **Internal migration notice present?** Look for any of: `deadline 2026-06-17`,
   `CI lint will enforce`, `docs/MIGRATION_RULES.md`.
3. **Framework Upgrade & Maintenance section present?** Look for a heading with exactly that text — it describes `/hyper-update` internals and should not appear in user project docs.

---

## Step 3 — Report Findings

Print a structured audit report:

```
HACF Contextualization Audit
─────────────────────────────────────────────
✅ CLAUDE.md   — Framing: PRESENT  |  Internal notices: NONE
⚠️  AGENTS.md   — Framing: MISSING  |  Internal notices: FOUND (migration notice)
❌ GEMINI.md   — File not found (Gemini not installed — skip)
📄 README.md   — Checked (see findings below)
```

If all installed agent files pass all checks: output `[CONTEXTUALIZED: OK]` and stop.

---

## Step 4 — Ask How to Fix

If any issues were found, use **AskUserQuestion** with these options:

- **Replace the HACF block from the install templates (Recommended)** — rewrites the body of
  each affected file's `hacf` managed block from `.agents/install-templates/<file>`. Canonical
  framing, no stale notices inside the block. Everything outside the block, including OpenRig
  and RIGGED blocks, is kept byte-for-byte.
- **Inject framing banner only** — adds the "HACF as a Toolchain" block at the start of the
  `hacf` managed block body without touching the rest. Preserves existing content but does NOT
  remove stale migration notices.
- **Show diff first, decide per file** — display a summary of differences between install
  template and current file, then ask Replace / Inject / Skip for each file individually.

---

## Step 5 — Apply Fix

### Option A: Replace with Install Templates

Check that `.agents/install-templates/` exists. If not, halt with:
`ERROR: .agents/install-templates/ not found. Run /hyper-update first to fetch v0.5.0+ templates.`

For each affected file that has a corresponding install template, write the template into the
file's `hacf` managed block through the block engine, which leaves every byte outside the block
unchanged:

```bash
python3 .agents/scripts/hyper_blocks.py upsert --id hacf --body-file .agents/install-templates/AGENTS.md AGENTS.md
```

(and the same for `CLAUDE.md` and `GEMINI.md`). A file with no HACF markers at all (a whole-file
v0.5.9 instruction file) goes through the instruction decision instead, which never appends to an
unknown file:

```bash
python3 .agents/scripts/hyper_blocks.py instruction --block-file .agents/install-templates/AGENTS.md AGENTS.md
```

Run `python3 .agents/scripts/hyper_blocks.py check <file>` afterwards; it lints the HACF markers.
Skip any file whose template does not exist in `.agents/install-templates/`.

### Option B: Inject Framing Banner

Insert the following block at the start of the body of each affected file's `hacf` managed
block (write the banner plus the current block body to a temporary file and run
`python3 .agents/scripts/hyper_blocks.py upsert --id hacf --body-file <tmp> <file>`). A file with no
HACF block gets one through Option A's instruction decision instead:

```
> **HACF as a Toolchain:** This project uses the Hypergraph Coding Agent Framework
> (HACF) as its development toolchain. The skills in `.agents/skills/`, the scripts
> in `.agents/scripts/`, and the schemas in `.agents/schemas/` are development tools —
> they are **not** subjects of this project's plans, PRDs, or architecture docs.
> When you write missions, slice specs, lore or ledger nodes, you are documenting
> **this project**, not the HACF framework itself.

```

Before injecting, verify the banner is not already present (idempotency check). If `HACF as a Toolchain` is already in the file, skip it and report `already present`.

### Option C: Per-File Decision

For each affected file:
1. Show a brief summary: current first 10 lines vs. install template first 10 lines.
2. Use **AskUserQuestion** (Replace / Inject / Skip) for that specific file.
3. Apply the chosen action before moving to the next file.

---

## Step 6 — README.md Check (Report Only)

If `README.md` exists and contains any of: `Hypergraph`, `HACF`, `hyper-`:

Scan each section (by H2/H3 heading). For each section containing a match:

**Flag as BLEED if the section:**
- Describes HACF skills as project features or deliverables (e.g., lists `/hyper-architect` as a project command rather than a dev tool)
- Lists HACF internal node IDs (`hyper_architect`, `hyper_execute`, `hyper_orchestrator`, etc.) as project architecture components
- Embeds HACF schema documentation (mission or slice `SPEC.md` templates, the ledger or lore schemas) as if they were the project's own schemas
- Uses HACF framework versioning language (e.g., "as of HACF v0.4.0", "this project implements HACF")

**Flag as CORRECT FRAMING if:**
- HACF is mentioned only as a development tool or in a "Built with" context
- The section describes the project's own features separately from HACF tooling
- There is a "HACF as a Toolchain" banner already present

Report each flagged section with its heading and a one-line explanation.
**DO NOT modify `README.md`** — surface findings only and let the user decide.

If `README.md` contains no HACF references at all, report: `README.md: No HACF references found — no action needed.`

---

## Step 7 — Final Report

After all fixes are applied:

```
[CONTEXTUALIZED: COMPLETE]
Updated:   CLAUDE.md, AGENTS.md
Unchanged: GEMINI.md (already correct)
Skipped:   (none)
README.md: 2 potential bleed sections found — see findings above
```

---

## Negative Space

- **DO NOT** modify `spec/`, `tests/`, `.agents/`, `.claude/commands/`, or any IDE bridge directories (`.cursor/`, `.windsurf/`, `.clinerules/`, `.roo/`).
- **DO NOT** modify `README.md` — report findings only.
- **DO NOT** edit `CLAUDE.md`, `AGENTS.md` or `GEMINI.md` outside the HACF managed block; OpenRig and RIGGED blocks stay byte-for-byte.
- **DO NOT** touch `CLAUDE.local.md`.
- **DO NOT** apply any changes silently — always confirm the fix mode with AskUserQuestion before writing.
- **DO NOT** treat absence of `GEMINI.md` or `CLAUDE.md` as an error — those IDEs may not be installed.
- **DO NOT** inject the framing banner twice — check for existing `HACF as a Toolchain` string before injecting.
