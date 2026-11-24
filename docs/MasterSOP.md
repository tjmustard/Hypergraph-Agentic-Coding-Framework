# **Master SOP: Hypergraph Agentic Coding Framework Workflow**

**Version:** 1.0 (Strict Sequential Execution Model)

**Core Paradigm:** Spec-First, Deterministic Memory, Automated Auditing

This standard operating procedure dictates the exact sequence of operations for generating, executing, and auditing software with the Hypergraph Agentic Coding Framework (HACF) v0.6.0. Do not deviate from the sequence. Bypassing the specification or auditing phases leads to context collapse, hallucinated requirements and a corrupted component ledger.

Every script command below uses `python3`. Each phase boundary needs a **fresh context window**.

## Where things live (v0.6.0)

| Path | What it is |
|---|---|
| `SPEC.md` | The project's system of record |
| `missions/<mission>/SPEC.md` | One mission per feature: its SuperPRD |
| `missions/<mission>/slices/<NN-slug>/SPEC.md` | One slice per module: its MiniPRD |
| `spec/compiled/architecture.yml` | The component ledger (`hacf.ledger/v2`), written only by `hyper_ledger.py` and `hypergraph_updater.py` |
| `lore_hypergraph.yml`, `lore/` | Decisions, stories and canon; see [Lore](Lore.md) |
| `spec/active/` | The workbench: `Draft_PRD.md`, `RedTeam_Report.md` |
| `spec/archive/` | Written only by `archive_specs.py`; agents never read it |

**Legacy flat specs (dual-read):** flat `spec/compiled/MiniPRD_*.md` files from v0.5.x are still read by every pipeline skill and script, with a warning, until v0.7.0 (no earlier than 2027-01-31). Move them with `hyper_migrate.py` ([Migration Rules](MIGRATION_RULES.md#migrating-specs-to-v060)).

## **Phase \-1: Legacy Onboarding (Brownfield Projects Only)**

If you are adopting the framework in an existing repository, map the territory before starting Phase 1.

1. **Initialize the ledger:** Execute `/hyper-discover`. The agent scans your code and populates `spec/compiled/architecture.yml` through `hyper_ledger.py`.
2. **Review the map:** `python3 .agents/scripts/hyper_ledger.py show` prints the summary. Verify that the major modules and dependencies were captured.
3. **Baseline the system:** Execute `/hyper-baseline`. The agent writes the root `SPEC.md` (the System of Record) through `hyper_scope.py project init` or `project update`.
4. **Verification:** Read `SPEC.md`. If it accurately reflects what your software does today, proceed to **Phase 1, Step 1 (`/hyper-architect`)**.

## **Phase 0: System Initialization**

1. **Install into your own repository:** run `git init` in your project directory (if it is not a git repository yet), then run the installer from the project root, as the [README](../README.md#new-project) shows. Never clone HACF as your project root.
2. **What the installer leaves:** `.agents/` (skills, scripts, schemas, rules, config), the IDE bridges, the HACF blocks in `AGENTS.md`, `CLAUDE.md` and `GEMINI.md`, the git hooks, and the project scaffold: `SPEC.md`, `project.yaml`, `workspace.yaml`, `missions/`, and an empty lore graph with `lore/MAP.md` and `lore/CANON.md` (created by `python3 .agents/scripts/hyper_lore.py init` when no graph exists; an existing graph is kept).
3. **Runtime:** Python 3.10 or newer with PyYAML. The installer's `--install-deps` consents to `pip install --user pyyaml` when it is missing.

## **Phase 1: The Specification Engine**

This phase is conversational. It extracts constraints, maps the blast radius of changes and forces architectural trade-offs *before* a line of code is written.

### **Step 1: Requirements Extraction (/hyper-architect)**

1. Open your agentic IDE (Claude Code, Antigravity, Cursor, Windsurf, Cline, Roo).
2. Execute `/hyper-architect`.
3. **The Pacing Loop:** the agent asks at most 2 questions per turn. Answer them thoroughly.
4. **Artifact:** `spec/active/Draft_PRD.md`.

### **Step 2: Adversarial Analysis (/hyper-redteam)**

1. **CRITICAL:** open a **new context window**. The Red Team must not read the Architect's conversation.
2. Execute `/hyper-redteam`.
3. The agent reads `Draft_PRD.md` (and the ledger, for an iterative update) to hunt for vulnerabilities, data contract collisions and missing non-functional requirements.
4. **Artifact:** `spec/active/RedTeam_Report.md`.

### **Step 3: Trade-off Resolution (/hyper-resolve)**

1. Open a **new context window**.
2. Execute `/hyper-resolve`.
3. The agent presents a forced trade-off (Option A vs. Option B) for every finding. Each decided trade-off becomes a PROPOSED lore node, which only a person approves (`/hyper-lore-approve`).
4. **Compilation:** the agent creates the mission and its slices through the scope manager: `hyper_scope.py mission create` writes `missions/<mission>/SPEC.md` (the SuperPRD) and `hyper_scope.py slice create` writes one `missions/<mission>/slices/<NN-slug>/SPEC.md` per module (the MiniPRDs). `python3 .agents/scripts/hyper_spec.py lint --mission missions/<mission> --min-confidence 9` must pass, so no slice is compiled below 9/10. If a foreign root item blocks the scaffold, it writes flat legacy specs to `spec/compiled/` instead and says so.
5. **Memory flush:** archive the workbench, so the next feature starts clean:
   ```bash
   python3 .agents/scripts/archive_specs.py --paths spec/active/Draft_PRD.md spec/active/RedTeam_Report.md --label <Feature_Name>
   ```

`python3 .agents/scripts/hyper_scope.py dag` prints the live slices in dependency order; `python3 .agents/scripts/hyper_spec.py status` lists missions and slices with their statuses.

## **Phase 2: The Execution Engine (The Build Loop)**

Build each slice by hand with the two skills below, one slice at a time, or let the [workflow engine](#the-workflow-engine) run every mission's slices in parallel.

### **Step 1: The Builder (/hyper-execute)**

1. Open a **new context window**.
2. Execute `/hyper-execute <spec>`, where `<spec>` is a slice `SPEC.md` (by path or dot-id) or a legacy `spec/compiled/MiniPRD_*.md`. The skill resolves it with `hyper_spec.py resolve`, implements every task, and marks the touched ledger nodes with `hypergraph_updater.py` before halting.
3. **Wait for the skill to finish** before Step 2. Never run `/hyper-audit` in the same context window.

### **Step 2: Contract Verification (/hyper-audit)**

1. **CRITICAL:** `/hyper-execute` must have finished. *Two builders at once corrupt the ledger.*
2. Open a **new context window**.
3. Execute `/hyper-audit <spec>`.
4. The Auditor evaluates the code against the spec's constraints and runs its tests.
5. **Reconciliation:** on a pass, the Auditor reconciles the `needs_review` nodes through `hyper_ledger.py review-template` and `apply-review` (never a hand edit), records the proof with `hyper_spec.py proof`, runs the lore gate, and archives a legacy MiniPRD with `archive_specs.py --miniprd <spec>`.

### **Mission close: lore capture**

When every slice of a mission is `audited`, the mission's `NOTES.md` records its lore capture under `## Lore capture`: `- replans:` (the replan commits counted) and `- stories:` (one proposed STORY per replan, or `none`). `python3 .agents/scripts/hyper_spec.py lint --mission missions/<mission> --mission-close` must then pass; it fails while the section is missing or the stories number fewer than the replans. Seats only propose the stories; a person approves lore.

## The Workflow Engine

`python3 .agents/scripts/hyper_engine.py run` runs every mission's slices in parallel, in `hyper_scope.py dag` order; when there are no missions it runs every legacy `spec/compiled/MiniPRD_*.md` in sequence. It has no mission selector. It never imports the Anthropic SDK: each agent phase is a headless `claude -p` session (the `claude-cli` executor) inside the OS sandbox, and the engine itself makes every git write.

```bash
python3 .agents/scripts/hyper_engine.py preflight            # the checks only
python3 .agents/scripts/hyper_engine.py run                  # preflight, then schedule the slices
python3 .agents/scripts/hyper_engine.py run --resume <run_id>
python3 .agents/scripts/hyper_engine.py status <run_id>      # print the run's run.json
python3 .agents/scripts/hyper_engine.py land <run_id>        # apply the run's ledger; prints the merge command
python3 .agents/scripts/hyper_engine.py land <run_id> --merge
python3 .agents/scripts/hyper_engine.py land <run_id> --rebase-ledger
python3 .agents/scripts/hyper_engine.py gc                   # list what finished runs left behind
python3 .agents/scripts/hyper_engine.py gc --older-than 14d --apply
```

**How a run works.** Preflight checks git worktree support, a clean main checkout, the `claude` CLI version and login, both sandboxes, a valid ledger (if there is none, it names `python3 .agents/scripts/hyper_ledger.py init` and `/hyper-discover`), lint and an acyclic slice DAG. The run then creates an integration branch `swimlane/hacf/<run>/integration`. A slice is ready when the slices it depends on are audited and integrated; slices whose declared files overlap, or that share a ledger node, run one after the other. Each slice gets its own worktree on `swimlane/hacf/<run>/<NN-slug>`, an execute phase, the engine's commit, its oracle, an audit phase and a scope check against its declared files; passing slices are integrated one at a time. Your main checkout stays byte-identical until `land`. Run state lives in `.agents/state/runs/<run_id>/`. Exit codes: 0 ok, 1 finished with blocked slices (or stopped), 2 usage, 3 lock held, 5 refused.

**Run flags.** Tracked config (`.agents/config/orchestration.yml`, `.agents/local/hacf.yml`) can only tighten a security setting; these flags are the only way to loosen one, and the run records them:

| Flag | Effect |
|---|---|
| `--allow-unsandboxed-oracle` | Run oracles and repo hooks without bwrap. By default every oracle runs in a bwrap sandbox on Linux (read-only root, writable worktree and tmp, masked `$HOME`, read-only `.git`, no network); without bwrap the run refuses. macOS has no bwrap, so oracles there need this flag, and then run agent-written code with your privileges and network. |
| `--unverified-executor` | Allow an executor whose `verified` is false. The engine refuses one without this flag. |
| `--allow-unsandboxed` | Run agent phases without the OS sandbox (loud warning). The experimental `codex-cli` and `gemini-cli` executors have no HACF-verified sandbox and run only with it, and, being unverified, also with `--unverified-executor`. |
| `--executor <name>` | The executor (default `claude-cli`). |
| `--max-budget-usd-per-run <usd>` | The run budget for this run. |

**Budget.** `autonomy.max_budget_usd_per_run` (shipped default 50.0 USD) caps a run: when it is reached, scheduling stops and the run records `budget_exceeded`. `autonomy.max_budget_usd_per_phase` (5.0) caps one agent phase. Both are security keys: tracked config may only lower them. A rate limit pauses every worker together (30 s, then 120 s).

**Landing.** `land <run_id>` applies the run to your main checkout. A tracked ledger rides along in the integration commits; an untracked one is staged in the run directory and applied by `land` under the ledger lock, with validation. Without `--merge`, `land` prints the `git merge --ff-only` command; with `--merge` it fast-forwards the checked-out base branch, only if it is clean. If the main checkout's ledger changed since the run started, `land` refuses (exit 5, with a report). `land --rebase-ledger` instead replays the run's recorded ledger entries onto the current ledger, validating; an entry that touches a node changed since the base is a conflict, and the land is refused with a per-node report.

**Garbage collection.** `gc` lists, and `gc --apply` removes, what landed or abandoned runs left behind: kept worktrees (then `git worktree prune`), `swimlane/hacf/<run>/*` branches, run directories and `refs/hacf/candidates/*`. `--older-than` takes an age such as `14d`. It never touches a run that holds the engine lock.

**Defense in depth.** The Bash allow and deny rules given to agent phases are defense in depth only, not a security boundary; the OS sandbox's denies on git metadata are the control. Review what a run integrates before you land it.

## **Phase 3: Novel Test Protocol (Human-in-the-Loop)**

Standard CI cannot test subjective or AI-generated outputs. If a spec contains a novel test:

1. The Builder or Auditor writes the result to `tests/candidate_outputs/` and halts.
2. Review the file yourself.
3. If the output is correct, move it to `tests/fixtures/`. Only a person promotes a candidate.
4. Change the spec's test definition from novel to deterministic. Later runs regress against the approved baseline.

## **Phase 4: Iterative Updates (The Delta Loop)**

When you add a feature later, the process is the same.

1. Run `/hyper-architect`. The agent detects the existing ledger and switches to **Delta Extraction**.
2. Run `/hyper-redteam`. It takes the **blast radius** from the ledger (`python3 .agents/scripts/hyper_ledger.py impact --nodes <ids>` or `--files <paths>`) to find how the feature collides with existing code.
3. Run `/hyper-resolve` to compile the new mission and its slices and archive the workbench.
4. Build each slice with `/hyper-execute` and `/hyper-audit`, or run the engine, which runs every mission's slices.

When a plan proves wrong mid-mission, `/hyper-resolve` in Replan mode recompiles only the unfinished slices.

## Ledger recovery

| Symptom | Command |
|---|---|
| No ledger yet | `python3 .agents/scripts/hyper_ledger.py init`, then `/hyper-discover` |
| Is the ledger valid? | `python3 .agents/scripts/hyper_ledger.py validate` (`--strict` fails on any finding or `needs_review`) |
| The YAML no longer parses because of a plain description | `python3 .agents/scripts/hyper_ledger.py repair-text --dry-run`, then without `--dry-run` |

More in [Troubleshooting](Troubleshooting.md#ledger).
