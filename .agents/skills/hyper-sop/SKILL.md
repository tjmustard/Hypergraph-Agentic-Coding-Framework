---
name: sop
description: Explains the Master SOP and guides the user to the correct framework phase. Use when a new user needs orientation or when you need to re-anchor to the correct workflow phase.
trigger: /hyper-sop
---

# /hyper-sop — Master Standard Operating Procedure Guide

This skill guides the user through the **Master Standard Operating Procedure (SOP)** of the Hypergraph Coding Agent Framework.

The framework relies on a strict sequential model — Spec-First, Deterministic Memory, and Automated Auditing — to prevent context collapse and hallucinated requirements.

## Step 1: Introduction

Briefly introduce the Hypergraph framework's core paradigm:
- The workflow is strictly sequential.
- Specialized agents handle distinct phases (Architect, Red Team, Resolution, Auditor).
- Deterministic Python scripts manage state so LLMs never traverse the graph probabilistically.
- New context windows (new conversations) prevent cross-contamination between adversarial agents.

## Step 2: Determine User State

Use **AskUserQuestion** to determine the user's current state:

```
Which phase of the framework are you in?

- Option A: Phase -1: Legacy Onboarding — Integrating the framework into an existing project
- Option B: Phase 0: Initialization — Starting a brand new greenfield project
- Option C: Phase 1: Specification — Planning and designing a new feature
- Option D: Phase 2: Execution — Writing code against a mission slice or a legacy flat MiniPRD
```

## Step 3: Provide Phase-Specific Guidance

Wait for the user's response, then provide the exact steps:

**Phase -1 (Legacy Onboarding):**
1. Run `/hyper-discover` — scans the codebase into the component ledger `spec/compiled/architecture.yml`. When no ledger exists it first runs `python3 .agents/scripts/hyper_ledger.py init`.
2. Review the ledger for accuracy: `python3 .agents/scripts/hyper_ledger.py show` and `python3 .agents/scripts/hyper_ledger.py validate`.
3. Run `/hyper-baseline` — writes the root `SPEC.md` (the system of record) describing the system as it exists.
4. Once verified, proceed to Phase 1.

**Phase 0 (System Initialization):**
1. Install HACF with `HACF-install.sh`, which writes `.agents/`, `spec/`, the root `SPEC.md`, `missions/` and the `lore/` directories.
2. Make sure `python3` can import PyYAML: `python3 -m pip install --user 'pyyaml>=5.4'`
3. The workspace is ready. Proceed to Phase 1.

**Phase 1 (Specification):**
1. Run `/hyper-architect` — begins the requirements extraction interview (creates `Draft_PRD.md` in `spec/active/`).
2. **Start a new conversation** and run `/hyper-redteam` — adversarial analysis (creates `RedTeam_Report.md` in `spec/active/`).
3. **Start a new conversation** and run `/hyper-resolve` — resolves trade-offs into PROPOSED lore decisions and compiles a mission under `missions/<mission>/` (mission `SPEC.md` plus one slice `SPEC.md` per module). It writes flat `spec/compiled/` specs only as the fallback when a foreign root item collides.
4. The resolve agent archives the active drafts with `python3 .agents/scripts/archive_specs.py --paths spec/active/Draft_PRD.md spec/active/RedTeam_Report.md --label <mission>`.

**Phase 2 (Execution):**
1. **Start a new conversation** and run `/hyper-execute <slice path or dot-id>` on a mission slice, or on a legacy flat `spec/compiled/MiniPRD_*.md`.
2. The Builder flags the blast radius after writing code: `python3 .agents/scripts/hypergraph_updater.py --from-diff <base> --apply --slice <SID> --json` for a slice, or `python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <node_ids>` for a legacy MiniPRD.
3. **Start a new conversation** and run `/hyper-audit <the same spec>` to verify code against the contract.
4. When an audit or resolve proposes lore canon, run `/hyper-lore-approve` to accept or reject it. Canon is reached only through this gate.

## Step 4: Remind About Testing and Iteration

- **Novel Tests:** Subjective/AI-generated outputs go to `tests/candidate_outputs/<SID>/` for human review. Only the user promotes an approved output into the project's fixtures and updates the spec's test definition to `deterministic`.
- **Iterative Updates:** Future features follow the identical Phase 1 → Phase 2 loop. Agents read the existing ledger, root `SPEC.md` and `lore/MAP.md` and perform Delta Extractions automatically.

## Step 5: Next Actions

Use **AskUserQuestion**:

```
Ready to begin, or do you have questions?

- Option A: Let's begin — start the selected phase now
- Option B: I have questions — clarify something before starting
```
