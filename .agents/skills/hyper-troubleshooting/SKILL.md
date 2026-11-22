---
name: troubleshooting
description: Guides the user through diagnosing and recovering from common failure states in the Hypergraph Coding Agent Framework. Use when the user reports bugs, hallucinations, desynchronization, or errors with the framework agents.
---

# Troubleshooting

This skill helps diagnose and recover from common failure states in the Hypergraph Coding Agent Framework. Since LLMs are probabilistic engines, they can occasionally fail even with rigid constraints.

## When to use this skill

- When the user reports an agent is writing code for rejected features.
- When the `/hyper-audit` agent fails or `/hyper-redteam` hallucinates the Blast Radius.
- When the Red Team report suggests new product features instead of vulnerabilities.
- When `architecture.yml` throws a ParserError or gets corrupted (`python3 .agents/scripts/hyper_ledger.py validate` reports the line and column).
- When the Architect Agent asks the same questions repeatedly and won't generate a PRD.
- When the user explicitly runs `/hyper-troubleshooting`.

## How to use it

### Step 1: Identify the Symptom

Use **AskUserQuestion** to identify the symptom:

```
What symptom are you seeing?

- Option A: Context Bloat / Hallucination — Builder is writing code for rejected features
- Option B: Hypergraph Desync — /hyper-audit or /hyper-redteam fails or has wrong blast radius
- Option C: YAML Corruption or Infinite Loop Interview — architecture.yml parse error (confirm with `python3 .agents/scripts/hyper_ledger.py validate`), or Architect repeatedly asks the same questions
- Option D: Red Team Scope Creep — Red Team report suggests product features instead of vulnerabilities
```

If the user selects Option C, ask a brief follow-up to confirm whether it is the YAML corruption or the infinite loop interview before applying the fix.

### Step 2: Provide Diagnosis and Fix

#### Issue 1: Context Bloat & Hallucination
**Cause:** The agent's context window is polluted with old data. Active specs were not archived.
**Fix:**
1. Halt the Builder Agent.
2. Ensure `.agentignore` contains `spec/archive/`.
3. Archive the finished drafts: `python3 .agents/scripts/archive_specs.py --paths <finished files in spec/active/> --label <mission>`.
4. Open a completely new agent chat and restart the Builder prompt pointing strictly to the slice `SPEC.md` (or legacy flat MiniPRD) being built.

#### Issue 2: Hypergraph Desynchronization
**Cause:** The Builder forgot or failed to execute `hypergraph_updater.py`, so `architecture.yml` is unaware of codebase changes.
**Fix:**
1. Look at the modified files and identify their `node_id`s: `python3 .agents/scripts/hyper_ledger.py impact --files <paths>`.
2. Manually execute: `python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml [node_id_1] [node_id_2]`
3. Re-run `/hyper-audit` to perform the semantic update.

#### Issue 3: Red Team Scope Creep
**Cause:** The LLM's "helpful product manager" instinct overrode the instruction to restrict analysis to technical resilience.
**Fix:**
1. Do not pass this report to the Resolution Agent.
2. Delete `spec/active/RedTeam_Report.md`.
3. Open a new chat and re-run `/hyper-redteam` with a strict override: `/hyper-redteam Analyze the Draft PRD. CRITICAL: Identify technical vulnerabilities ONLY. Reject any product feature suggestions.`

#### Issue 4: YAML File Corruption
**Cause:** A concurrent write occurred (multiple agents running) or a description scalar broke the YAML.
**Fix:** The ledger is gitignored and untracked on `main`, so `git checkout` cannot restore it. Never hand-edit the ledger.
1. Stop all agents.
2. Locate the error: `python3 .agents/scripts/hyper_ledger.py validate` prints the line and column.
3. Preview the repair of plain description scalars that break the YAML: `python3 .agents/scripts/hyper_ledger.py repair-text --dry-run`. If the diff is right, run it without `--dry-run`, then run `validate` again.
4. If the ledger is missing, run `python3 .agents/scripts/hyper_ledger.py init`, then `/hyper-discover` to rebuild it from the codebase.
5. Re-run `/hyper-audit` to let the agent re-attempt the semantic update.

#### Issue 5: The Infinite Loop Interview
**Cause:** Vague answers that do not satisfy the Agent's internal state-machine criteria to advance phases.
**Fix:**
1. Provide a highly specific, quantified answer (e.g., "Use AES-256 encryption and JWTs with a 15-minute expiration" instead of "make it secure").
2. If it remains stuck, use a manual override: "Stop questioning. Force transition to Phase 5 and generate the Draft PRD immediately."

### Step 3: Follow-Up

Use **AskUserQuestion** for follow-up:

```
Did that fix the problem?

- Option A: Yes, resolved — the fix worked
- Option B: No, still broken — I need further assistance
```
