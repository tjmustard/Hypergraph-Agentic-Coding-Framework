---
name: session-update
description: Updates activeContext.md, proposes lore decisions and field notes, and syncs project docs with work done in the current session. Use at the end of a development session to keep memory in sync with the codebase.
---

# Session Update

This skill closes out a development session by syncing `activeContext.md`, the lore and documentation with the work completed. It ensures the next agent session starts with accurate context.

## When to use this skill

- At the end of every development session, before closing the chat.
- When the user explicitly runs `/hyper-session-update`.
- After completing a significant feature, fix, or architectural change.

## How to use it

1. **Review Session Work**
   - Check the task checkboxes of the specs worked on (`python3 .agents/scripts/hyper_spec.py status` lists missions and slices) to identify what was accomplished.
   - Review git status or recall the file changes made during this session.
   - Identify key decisions, architectural changes, or new patterns that emerged.

2. **Update Product & Design Specs**
   - If requirements changed: run `/hyper-architect` so the change goes through a mission `SPEC.md`; do not edit compiled specs by hand.
   - If design changed: Update `docs/DESIGN.md`.
   - *Goal:* Keep high-level docs in sync with code reality.
   - **CRITICAL:** Do NOT manually modify `spec/compiled/architecture.yml`. Flag the nodes whose files changed with `python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <node_ids>`, and use `/hyper-discover`/`/hyper-audit` to reconcile it.

3. **Update Agentic Memory and Lore**
   - **`activeContext.md`**: Update with current focus, recent changes, and immediate next steps.
   - **Decisions:** write the body to a temporary file and run `python3 .agents/scripts/hyper_lore.py propose --type decision --title '<title>' --body-file <file>`. Recurring patterns go to `propose --type canon`; they stay PROPOSED until `/hyper-lore-approve`.
   - **Field notes:** `python3 .agents/scripts/hyper_lore.py note --mission <mission dir> --text '<note>'`, or `note --legacy --source <MiniPRD path>` for a legacy flat spec.
   - *Note:* `activeContext.md` and the lore act as heuristic context alongside the deterministic ledger.

4. **Update Rules (if needed)**
   - If a correction was repeated or a new rule consistently applied, add it to `.agents/rules/` (or create a new rule file for major topics).

5. **Commit**
   - Commit memory and doc updates alongside code changes, or as a separate `chore: update memory` commit.
