# **Hypergraph Agentic Coding Framework: Orchestrating Autonomous Software Engineering via Multi-Dimensional Specification and Deterministic State Management**

## **Abstract**

The transition to the "Hypergraph Agentic Coding Framework" shifts the developer's role from a writer of syntax to an architect of intent. This transition is bottlenecked by the **Specification Alignment Problem**: Large Language Models (LLMs) suffer from context collapse and hallucination when forced to ingest monolithic requirements or traverse complex system architectures probabilistically.

This whitepaper describes a "Spec-First" framework, as shipped in v0.6.0, designed around those constraints. It decouples probabilistic reasoning (LLM agents) from deterministic state management (Python scripts), serializes the system's architecture into a component ledger, keeps the project's decisions in a gated lore graph, and runs agents in sandboxed, isolated sessions whose every git write is made by the engine, not the agent.

## **1\. The Core Architecture (The Living Master Plan)**

The framework abandons the "Prompt Zero" approach in favor of a multi-resolution specification pipeline. Its pillars:

1. **Multi-Agent State Machines:** Specification is broken into discrete conversational phases run by specialized agents (Architect, Red Team, Resolution, Builder, Auditor), each in a fresh context window, so no stage inherits the previous stage's assumptions.
2. **The Component Ledger:** System memory is decoupled from context windows and serialized into `spec/compiled/architecture.yml` (`hacf.ledger/v2`), which maps components, the files they own and their edges. `hyper_ledger.py` is its single validated, locked writer.
3. **Deterministic Graph Traversal:** LLMs never traverse the graph. Scripts (`hypergraph_updater.py`, `hyper_ledger.py impact`) compute the blast radius of a change.
4. **Lore:** Decisions, stories and binding canon live in `lore_hypergraph.yml` and `lore/`. Agents propose; only a person approves.
5. **Missions and Slices:** Specifications are `SPEC.md` scopes: the project's `SPEC.md` (the system of record), one mission per feature (the SuperPRD) and one slice per module (the MiniPRD).
6. **Aggressive Lifecycle Management:** Finished drafts are archived by `archive_specs.py` into `spec/archive/`, which agents never read.
7. **The Candidate Artifact Protocol:** Non-deterministic outputs are routed to `tests/candidate_outputs/` for a person to review and promote.

## **2\. The Deterministic Tooling**

Every state change goes through a script; the interpreter is `python3`.

### **2.1 The Ledger Writer (`hyper_ledger.py`)**

Every write takes the ledger lock (`<git common dir>/hacf/locks/ledger.lock`, a bounded wait, then exit 3), loads the file, applies the change in memory, validates the result and refuses a write that would add an error (exit 5). Its commands: `init`, `show`, `validate`, `impact`, `ensure-node`, `set-edges`, `review-template`, `apply-review`, `mark`, `remove-node` (which leaves an append-only tombstone), `apply-outbox`, `migrate-v2` and `repair-text`. An Auditor never rewrites the YAML: it fills a review template, and `apply-review` applies the patch.

### **2.2 The Blast-Radius Updater (`hypergraph_updater.py`)**

Given node ids, it marks them `dirty` and every node an edge reaches `needs_review`: the `implements` parents and the reverse `depends_on` dependents (`member_of` is not traversed). With `--from-diff <rev-range>` it maps a git diff through the nodes' `files` globs and lists the changed files no node claims as `unmapped`, which the audit treats as a finding.

### **2.3 Specs and Archival (`hyper_scope.py`, `hyper_spec.py`, `archive_specs.py`)**

`hyper_scope.py` creates the project, mission and slice scopes and prints the slice DAG. `hyper_spec.py` resolves a spec by path or dot-id, lints it against the schema, and records progress and proof. `archive_specs.py --miniprd <path>` or `--paths <files>` is the only writer of `spec/archive/`.

## **3\. Data Structures & Constraints (Schemas)**

### **3.1 The Component Ledger (`hacf.ledger/v2`)**

```yaml
schema: hacf.ledger/v2
nodes:
  - id: component_id              # ^[a-z][a-z0-9_]{1,63}$
    dimension: Module             # System | Layer | Module | Atomic
    status: clean                 # clean | dirty | needs_review
    associated_file: path
    files: ['glob']               # what the node owns
    description: >-
      Semantic purpose.
    inputs:  [{data_type: x, source_id: node} | {data_type: x, external: label}]
    outputs: [{data_type: x, target_id: node} | {data_type: x, external: label}]
    edges: {depends_on: [], implements: [], member_of: []}
    spec_refs: []
    provenance: {}
    review: {}
removed_nodes: [{id, reason, slice, commit}]   # append-only tombstones
```

`clean` means verified, `dirty` means modified and awaiting audit, and `needs_review` means inside a changed node's blast radius.

### **3.2 Missions and Slices (`SPEC.md`)**

SuperPRDs and MiniPRDs are document types inside `SPEC.md` files with YAML frontmatter (`hacf_schema: 1`; `hacf_type: SystemOfRecord | SuperPRD | MiniPRD`). A slice's frontmatter carries its ledger `node_id` and `parent`, its status (`hacf_status`), the files it may touch (`hacf_touches`), its oracle (`hacf_oracle`) and the lore it is bound by (`lore_refs`). Its body keeps the MiniPRD discipline:

```
# MiniPRD: <Module>
## Intent
## 1. The Confidence Mandate              (score 1-10; questions if < 9)
## Mini-requirements                       (US-001: As [User], I want [Action] so that [Value])
## 3. Implementation Plan (Task List)      (- [ ] tasks of under 10 minutes each)
## 4. The Negative Space (Constraints)     (- **DO NOT** ...)
## Proof contract                          (deterministic tests; novel tests route to candidates)
```

Legacy flat `spec/compiled/MiniPRD_*.md` files from v0.5.x are still read until v0.7.0.

### **3.3 Lore (`hacf.lore/v1`)**

`lore_hypergraph.yml` holds the decisions (ADRs), stories and canon nodes; `lore/` holds their text, the immutable `lore/canon/<ID>.md` copies and the generated `MAP.md` and `CANON.md`. `hyper_lore.py` is its single writer, under its own lock and with compare-and-swap writes; `init` creates an empty store for a fresh project. A node is PROPOSED, CANON, REJECTED, SUPERSEDED or RETIRED. CANON nodes bind every agent, as do PROPOSED nodes whose origin is `user-decision`, and a CANON node whose anchors changed is marked stale but stays binding until a person retires or supersedes it. Agents may only propose, note and mark stale; the approve, reject, retire, reopen and review verbs pass through the CANON gate, which needs a person. The gate is procedural, not a security boundary. See [Lore](Lore.md).

### **3.4 The Agent Firewall (`.agentignore`)**

`.agentignore` is the source; `hyper_firewall.py render` turns it into Claude Code permission rules and best-effort ignore files for the other tools. The rules deny reads of `spec/archive/` and `tests/candidate_outputs/`, and edits of the ledger, the lore graph and the canon. They do not cover Python or Node subprocesses that open files themselves, or commands that read files without naming them. The firewall keeps honest agents out of stale context; it is not a security boundary.

## **4\. The Agentic Workflows (Skills)**

Each skill lives once in `.agents/skills/<name>/SKILL.md`; every IDE reads it through a thin bridge.

| Skill | Phase | Contract |
|---|---|---|
| `/hyper-architect` | 1 | A paced interview (at most two questions per turn) that writes `spec/active/Draft_PRD.md`. With an existing ledger it switches to delta extraction. |
| `/hyper-redteam` | 1 | A hostile but constructive review of the draft against the ledger's blast radius; writes `spec/active/RedTeam_Report.md`. No new product features. |
| `/hyper-resolve` | 1 | Forced trade-offs (Option A vs. Option B). Each decision becomes PROPOSED lore; the mission and slices are compiled through `hyper_scope.py` and linted. Replan mode recompiles only unfinished slices. |
| `/hyper-execute` | 2 | Implements one slice exactly, then marks the touched nodes with the updater and halts. |
| `/hyper-audit` | 2 | Verifies the code against the slice's constraints and proof contract, reconciles the `needs_review` nodes through `review-template` and `apply-review`, records the proof and runs the lore gate. |
| `/hyper-lore-approve` | any | The person's review of PROPOSED and stale CANON nodes, one node per approval. |

## **5\. The Workflow Engine**

`hyper_engine.py run` runs every mission's slices in parallel (in `hyper_scope.py dag` order; with no missions, every legacy MiniPRD in sequence), without the Anthropic SDK. It has no mission selector. Each slice runs in its own git worktree on a `swimlane/hacf/<run>/…` branch; each agent phase is a headless `claude -p` session inside the OS sandbox, with no write access to git metadata. The engine itself makes every commit, checks each slice's diff against its declared files, scans it for credential shapes, runs the slice's oracle in a bwrap sandbox on Linux, and integrates passing slices one at a time. Tracked configuration can only tighten a security setting; loosening one takes a command-line flag, which the run records. A per-run budget (`autonomy.max_budget_usd_per_run`) stops scheduling when reached. The main checkout stays byte-identical until a person runs `hyper_engine.py land`. See [MasterSOP](MasterSOP.md#the-workflow-engine).

## **6\. Update Integrity (ADR-0010)**

An updater that cannot verify its upstream is an attack path. HACF's policy is signed-if-configured plus explicit consent:

- Releases are unsigned in v0.6.x. An unsigned upstream needs a person's consent in a terminal, which is logged with the URL and commit, so unattended updates are impossible while releases stay unsigned.
- Any future signing uses SSH signatures only, verified against an allowed-signers file passed to git for that one command. Users never import a key globally.
- Allowed signers are pinned machine-locally on first trust; an upstream's proposal to change them needs interactive consent.
- An upstream commit that does not descend from the installed one is refused without `--allow-downgrade`.

See [Migration Rules](MIGRATION_RULES.md#updating-hacf-adr-0010).

## **7\. Standard Operating Procedure**

The workflow is strictly sequential where it shares state, so the ledger is never written by two builders at once.

1. **Initialization:** `git init` your own project and run the installer; it scaffolds `SPEC.md`, the manifests and an empty lore store.
2. **Specification:** `/hyper-architect`, then `/hyper-redteam`, then `/hyper-resolve`, each in a fresh context; then archive the workbench.
3. **Execution:** per slice, `/hyper-execute <spec>`, then `/hyper-audit <spec>` in a fresh context; or `hyper_engine.py run` and `land`, which run every mission's slices (every legacy MiniPRD when there are no missions).
4. **Novel outputs:** a person reviews `tests/candidate_outputs/` and promotes what is correct to `tests/fixtures/`.

Details: [MasterSOP](MasterSOP.md).

## **Conclusion**

The Hypergraph Agentic Coding Framework is not defined by the intelligence of the model but by the rigor of the system around it. By wrapping LLMs in deterministic writers, state-machine pacing, a component ledger, gated lore and isolated sessions, it turns probabilistic text generators into dependable software engineering engines.
