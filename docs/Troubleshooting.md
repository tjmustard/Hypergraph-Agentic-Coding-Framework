# **Troubleshooting Guide: Agent Failure States**

Large Language Models are probabilistic engines. Even with rigid constraints, they occasionally fail. This guide shows how to diagnose and recover from the most common failure states in the Hypergraph Coding Agent Framework (v0.6.0). Every command uses `python3`.

`<spec>` below is a slice `SPEC.md` (`missions/<mission>/slices/<NN-slug>/SPEC.md`, by path or dot-id) or a legacy flat `spec/compiled/MiniPRD_*.md`, which is still read until v0.7.0.

## **1\. Context Bloat & Hallucination**

**The Symptom:** The Builder writes code for features that were discussed during the interview but rejected or changed during resolution.

**The Cause:** The context window is polluted with old drafts. The workbench was probably not archived.

**The Fix:**

1. Halt the agent.
2. Ensure `.agentignore` contains `spec/archive/` (the installer renders it into each IDE's rules).
3. Archive the stale drafts:
   ```bash
   python3 .agents/scripts/archive_specs.py --paths spec/active/Draft_PRD.md spec/active/RedTeam_Report.md --label <Feature_Name>
   ```
4. Open a **completely new context window** and run `/hyper-execute <spec>`.

<a id="ledger"></a>
## **2\. Ledger Desynchronization**

**The Symptom:** `/hyper-audit` cannot reconcile the ledger, or `/hyper-redteam` reports the wrong blast radius.

**The Cause:** The Builder did not run the blast-radius update, so `spec/compiled/architecture.yml` does not know the code changed.

**The Fix:**

1. Identify the files `/hyper-execute` changed and their node ids in the ledger (`python3 .agents/scripts/hyper_ledger.py show`), then mark them:
   ```bash
   python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <node_id_1> <node_id_2>
   ```
   Check the exit code: 1 means it wrote nothing (for example, a node id the ledger does not have).
2. Open a **new context window** and run `/hyper-audit <spec>` to reconcile the `needs_review` nodes.

## **3\. Broken or Missing Ledger**

**The Symptom:** A script exits 4 ("does not load as YAML"), or the engine's preflight says there is no ledger.

**The Fix:**

| Situation | Command |
|---|---|
| No ledger | `python3 .agents/scripts/hyper_ledger.py init`, then `/hyper-discover` |
| What is wrong? | `python3 .agents/scripts/hyper_ledger.py validate` (`--json` for a report, `--strict` to fail on any finding or `needs_review`) |
| What did a change drop? | `python3 .agents/scripts/hyper_ledger.py validate --against-ref HEAD` |
| A plain description scalar breaks the YAML | `python3 .agents/scripts/hyper_ledger.py repair-text --dry-run`, then without `--dry-run` |
| A v1 or mixed ledger | `python3 .agents/scripts/hyper_ledger.py migrate-v2` |
| A write left it worse | `git checkout -- spec/compiled/architecture.yml` (when it is tracked), then re-run `/hyper-audit` |

Never edit `architecture.yml` by hand: `hyper_ledger.py` is its single validated writer, and a write that would add an error is refused (exit 5).

**Exit 3 ("timed out waiting for the ledger lock"):** another writer holds `$(git rev-parse --git-common-dir)/hacf/locks/ledger.lock`. You ran two builders at once, or the engine is running. Wait for it; never delete the lock while a writer is alive.

## **4\. Red Team "Scope Creep"**

**The Symptom:** The Red Team report suggests entirely new product features instead of technical vulnerabilities.

**The Fix:**

1. Do not pass this report to the Resolution Agent.
2. Delete `spec/active/RedTeam_Report.md`.
3. Open a new context window and re-run with a strict override: `/hyper-redteam Analyze the Draft PRD. CRITICAL: Identify technical vulnerabilities ONLY. Reject any product feature suggestions.`

## **5\. The "Infinite Loop" Interview**

**The Symptom:** The Architect keeps asking the same clarifying questions and never writes the Draft PRD.

**The Fix:**

1. Give a specific, quantified answer (instead of "make it secure", say "AES-256 encryption and JWTs with a 15-minute expiry").
2. If it is still stuck: "Stop questioning. Force transition to Phase 5 and generate the Draft PRD immediately."

## **6\. A Finished Spec Shows Up Again**

**The Symptom:** `/hyper-execute` picks up a slice or MiniPRD that was already built and audited.

**The Cause:** The audit did not complete. A slice's status lives in its `SPEC.md` frontmatter; a legacy MiniPRD stays pending while it is in `spec/compiled/`.

**The Fix:** Open a **new context window** and run `/hyper-audit <spec>`. On a pass it records the slice as audited, or archives the legacy MiniPRD to `spec/archive/MiniPRD_<Name>_AUDITED.md` with `archive_specs.py --miniprd <spec>`. `python3 .agents/scripts/hyper_spec.py status` lists each slice's status. Never move files into `spec/archive/` by hand.

<a id="lore"></a>
## **7\. Lore**

| Symptom | Fix |
|---|---|
| A fresh project has no `lore_hypergraph.yml` | `python3 .agents/scripts/hyper_lore.py init` writes an empty graph, `lore/MAP.md` and `lore/CANON.md`. It refuses (exit 4, nothing written) when the graph already exists. |
| `lore/MAP.md` or `lore/CANON.md` is out of date | `python3 .agents/scripts/hyper_lore.py index` |
| Is the graph valid? | `python3 .agents/scripts/hyper_lore.py validate` (read-only) |
| An approval is refused with "switch to default or acceptEdits" | Switch Claude Code to default or acceptEdits mode (Shift+Tab) and run `/hyper-lore-approve` again. Other modes are refused by design. |
| Exit 3 ("lore.lock") | Another lore write holds `$(git rev-parse --git-common-dir)/hacf/locks/lore.lock`; wait and retry. |
| A merge conflict in `lore_hypergraph.yml` | Install the repo-local merge drivers: `python3 .agents/scripts/hyper_lore.py install-drivers` |

How the gate works: [Lore](Lore.md).

## **8\. Workflow Engine**

| Symptom | Fix |
|---|---|
| Preflight: `sandbox_unavailable` | Install `bwrap` and `socat` (Linux; on Ubuntu 24.04 AppArmor can also block bwrap) or make `sandbox-exec` available (macOS, agent phases only). Only if you accept the risk: `--allow-unsandboxed-oracle` for oracles, `--allow-unsandboxed` for agent phases. |
| Preflight refuses an unverified executor | Pass `--unverified-executor` if you accept it; the run records it. `codex-cli` and `gemini-cli` are unverified and need it as well as `--allow-unsandboxed`. |
| The run stopped with `budget_exceeded` | Raise the cap for this run with `--max-budget-usd-per-run <usd>`, then `run --resume <run_id>`. |
| `land` refuses with a ledger drift report (exit 5) | `python3 .agents/scripts/hyper_engine.py land <run_id> --rebase-ledger` replays the run's ledger entries; a per-node conflict report means a person must reconcile those nodes. |
| Old worktrees and `swimlane/hacf/*` branches pile up | `python3 .agents/scripts/hyper_engine.py gc`, then `gc --apply` |

Details: [MasterSOP](MasterSOP.md#the-workflow-engine).

## **9\. Updates and Migration**

| Symptom | Fix |
|---|---|
| The update refuses an unsigned upstream in CI | Expected: releases are unsigned in v0.6.x and `--allow-unsigned` needs a TTY. Update from a terminal. |
| The update refuses a non-descendant upstream | Pass `--allow-downgrade` only if you meant to move back. |
| `hyper_migrate.py rollback` lists drifted paths and restores nothing | Files changed since the apply. Save your edits, then `python3 .agents/scripts/hyper_migrate.py rollback --journal .agents/state/migrations/v0.6.0.json --force`. |

Details: [Migration Rules](MIGRATION_RULES.md#updating-hacf-adr-0010).

## **10\. Git Hooks**

### The pre-commit hook lints a file my ruff config excludes

- **v0.6.0:** HACF's pre-commit hook passed the staged `*.py` paths to ruff explicitly. For explicit
  paths, ruff ignores `exclude` and `extend-exclude` unless it gets `--force-exclude`, so the hook
  linted and blocked files the project had excluded.
- **v0.6.1:** the hook calls `ruff check --force-exclude` and `ruff format --check --force-exclude`, so
  it lints the staged files that `ruff check .` from the project root would lint. Upgrade to get the
  new hook. A v0.6.0 workaround (a root `force-exclude = true`, with a nested `.ruff.toml`) keeps
  working.
- **Known limit:** a nested ruff config (a `pyproject.toml` with `[tool.ruff]`, a `ruff.toml` or a
  `.ruff.toml`) inside a root-excluded directory is still linted. For an explicit path, ruff takes
  the settings from the nearest config, so the root's exclusions never apply to that file. Give the
  nested directory its own exclusion: a `.ruff.toml` beside the nested config, holding
  `exclude = ["*"]`.
- **Skip the lint:** `git config hacf.lint false` turns the hook's lint step off for this repository,
  and `git config --unset hacf.lint` turns it back on. For a single commit, use
  `git -c hacf.lint=false commit`. The hook prints `lint skipped (git config hacf.lint=false)`. The
  forbidden-path check (`spec/process/`) still runs.
