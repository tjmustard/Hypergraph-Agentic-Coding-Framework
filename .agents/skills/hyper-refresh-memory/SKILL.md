---
name: refresh-memory
description: Reads the ledger, SPEC.md, missions, lore, memory, rules and the codebase to rebuild a fresh mental model of the project, then updates activeContext.md and proposes lore for what changed. Use when starting a new session or when memory feels stale.
---

# Refresh Memory

This skill rebuilds the agent's mental model of the project by reading deterministic state, exploring the actual codebase, updating `activeContext.md` to reflect current reality, and proposing lore for decisions and patterns.

## When to use this skill

- When starting a new session after a significant gap in development.
- When the agent's responses seem to reference outdated architecture or patterns.
- When the user explicitly runs `/hyper-refresh-memory`.
- After major refactors that may have drifted from documented patterns.

## How to use it

1. **Read Baseline State**
   - Read the deterministic state: `spec/compiled/architecture.yml` (the component ledger).
   - Read the root `SPEC.md` (the system of record, including its product context).
   - List missions and slices with `python3 .agents/scripts/hyper_spec.py status --json`, and read the active mission's `SPEC.md`.
   - Read any legacy flat `spec/compiled/MiniPRD_*.md` files (still supported until v0.7.0).
   - Read `lore/MAP.md` and list lore nodes with `python3 .agents/scripts/hyper_lore.py list --json`.
   - Read `.agents/memory/activeContext.md` and all files in `.agents/rules/`.

2. **Explore the Current Codebase**
   - Use directory listing to get a high-level view of the project structure (`src/`, `tests/`, etc.).
   - Use search and file reading to inspect key files and understand implementation details.
   - *Goal:* Build a fresh mental model of the *actual* codebase state vs. what the ledger currently documents.

3. **Synthesize Findings**
   - Compare your exploration findings with the ledger, `SPEC.md`, the missions, the lore and `activeContext.md`.
   - Identify discrepancies (e.g., deprecated dependencies still mentioned, new patterns not documented, renamed files).

4. **Refresh Memory and Propose Lore**
   - **`activeContext.md`** is the only memory file this skill writes: update it with the current development focus, recent changes, and immediate next steps.
   - **Decisions** that are not yet in lore: write the body to a temporary file and run `python3 .agents/scripts/hyper_lore.py propose --type decision --title '<title>' --body-file <file>`.
   - **Recurring patterns** worth binding future work: run `python3 .agents/scripts/hyper_lore.py propose --type canon --title '<title>' --body-file <file>`. The node stays PROPOSED; it becomes canon only through `/hyper-lore-approve`.
   - If product goals have evolved, report it and recommend `/hyper-baseline` (root `SPEC.md`) or `/hyper-architect` (a mission `SPEC.md`); do not rewrite them here.

5. **Sync Rules**
   - If a rule is outdated, update the relevant `.agents/rules/` file.
   - **CRITICAL:** Do NOT manually edit `architecture.yml`. If the ledger is out of sync with reality, instruct the user to run `/hyper-discover` or `/hyper-audit` to deterministically reconcile it.

6. **Verify Consistency**
   - Ensure `activeContext.md` is consistent with the ledger, `SPEC.md`, the missions and the lore, and accurately reflects the codebase.
   - Report any discrepancies found that could not be resolved without running a deterministic script.
