---
name: status
description: Immediately outputs the current, fully updated Living Master Plan including project objectives, architecture hypotheses, actionable steps, constraints, and a graveyard of failed approaches. Use at any point to get a snapshot of the project state.
---

# Status

This skill immediately outputs the current Living Master Plan — the complete, up-to-date snapshot of project state maintained by the Expert Co-Researcher persona.

## When to use this skill

- When the user wants a quick summary of where the project stands.
- When the user explicitly runs `/hyper-status`.
- At the start of a new session to re-orient before diving in.
- When context has been lost and the user needs to re-establish shared understanding.

## How to use it

Read the following sources (all read only) to compile the current state:
- `.agents/memory/activeContext.md` — current focus and next steps
- root `SPEC.md` — the system of record: intent, baseline and product context
- `lore/MAP.md` — the lore map: canon, proposed, stale, graveyard and where things live
- `python3 .agents/scripts/hyper_spec.py status --json` — missions and slices with their statuses
- `python3 .agents/scripts/hyper_lore.py list --json` — every lore node with its status, `stale` flag and `binding`
- `spec/compiled/architecture.yml` — hypergraph state (read only)

Legacy project: when there is no root `SPEC.md`, `hyper_spec.py status --json` lists only v6 missions, so also list the legacy flat specs in `spec/compiled/` (`SuperPRD_*.md`, `MiniPRD_*.md`) as the plan. A missing source is reported as missing, not guessed.

Then output the Living Master Plan immediately, covering all sections without compressing or summarizing older milestones:

### Project Objectives
The fundamental goals of the current project — what success looks like.

### Current Hypotheses & Architecture
The theoretical foundation and structural plans currently in play. What architectural bets have been made and why.

### Actionable Steps
A high-level roadmap where any immediate tasks are broken down into clear atomic steps. Flag what is blocked, in-progress, or ready to start.

### Known Constraints & Open Questions
Limitations, barriers, and uncertainties that affect execution. Include both technical constraints and product unknowns.
Binding lore, cited by id (R23): CANON nodes, stale CANON nodes (binding until retired or superseded) and PROPOSED nodes with `origin: user-decision` (binding until rejected); `list --json` marks them `binding: true`.

### The Graveyard
A strict, exhaustively detailed log of:
- REJECTED, SUPERSEDED and RETIRED lore nodes, each with its id and warrant
- Failed code iterations and why they failed
- Discarded architectural approaches and what ruled them out
- Disproven hypotheses and the evidence against them

Do not compress this section. The Graveyard prevents repeating known failures.
