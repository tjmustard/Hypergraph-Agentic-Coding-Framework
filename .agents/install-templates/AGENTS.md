Trailhead: start at lore/MAP.md, then SPEC.md, then the active mission under missions/

# Hypergraph Agentic Coding Framework — Agent Instructions

> **HACF as a Toolchain:** This project uses the Hypergraph Agentic Coding Framework
> (HACF) as its development toolchain. The skills in `.agents/skills/`, the scripts
> in `.agents/scripts/`, and the schemas in `.agents/schemas/` are development tools —
> they are **not** subjects of this project's plans, PRDs, or architecture docs.
> When you create SuperPRDs, MiniPRDs, or architecture nodes, you are documenting
> **this project**, not the HACF framework itself.
>
> Cross-IDE manifest: always-on context for Windsurf, Cursor, Roo Code, GitHub Copilot, Zed and
> Codex. Bridge files in `.claude/`, `.cursor/`, `.windsurf/`, `.clinerules/` and `.roo/` add
> tool-specific overrides. Human guide: HACF's README, <https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework#readme>.

---

## Layout (v0.6.0)

```
SPEC.md               # project system of record (hacf_type: SystemOfRecord)
project.yaml          # OpenRig project manifest
workspace.yaml        # OpenRig workspace manifest
lore_hypergraph.yml   # lore graph (hacf.lore/v1); written only by hyper_lore.py
lore/                 # MAP.md (start here), CANON.md, ADRs/, STORIES/, proposed/, canon/
missions/<mission>/   # SPEC.md (SuperPRD), mission.yaml, slices/<NN-slug>/SPEC.md (MiniPRD)
spec/                 # HACF workbench
├── active/           # Draft_PRD.md, RedTeam_Report.md — temporary
├── compiled/         # architecture.yml (ledger v2); legacy flat MiniPRD_*.md
├── archive/          # written only by archive_specs.py — BLOCKED from agent reads
└── process/          # commit-blocked
tests/candidate_outputs/   # unverified outputs — BLOCKED from agent reads
tests/fixtures/            # verified baselines, promoted by a person
.agents/              # skills/, scripts/, schemas/, rules/, config/, install-templates/,
                      # memory/activeContext.md, local/hacf.yml, state/ (machine-local)
```

`.agents/` is the single source of truth for skills, rules, schemas and scripts; the IDE
directories hold thin bridges to it. Do not copy the `lore/MAP.md` index anywhere: read it.

---

## System Mandates

### 1. Skill Invocation
When the user invokes a skill (e.g. `/hyper-architect`), read `.agents/skills/<name>/SKILL.md`
and follow it precisely. `ls .agents/skills/` lists them.

### 2. Deterministic Scripts
The interpreter is `python3` in every command. Run a script when a skill mandates it and check
its exit code and output.

```bash
python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <node_id> [<node_id> ...]
python3 .agents/scripts/archive_specs.py --miniprd spec/compiled/MiniPRD_<Module>.md
```

Entry points by role:

| Role | Entry point | Purpose |
|---|---|---|
| Ledger | `hyper_ledger.py` | single writer of `architecture.yml`: init, validate, impact, ensure-node, review-template, apply-review, mark, remove-node |
| Ledger | `hypergraph_updater.py` | blast radius: positional node ids, or `--from-diff <rev-range>` (prints `unmapped`) |
| Specs | `hyper_spec.py` | resolve, lint, progress, proof and status for SPEC.md and legacy MiniPRDs |
| Specs | `archive_specs.py` | the only writer of `spec/archive/` (`--miniprd` or `--paths`) |
| Scopes | `hyper_scope.py`, `hyper_openrig.py` | project, mission and slice scopes; OpenRig manifests (rig versions listed in `.agents/config/openrig_contract.yml`, or a probed 0.7.x) |
| Lore | `hyper_lore.py`, `hyper_gates.py` | lore graph writer and CANON gate; gate mode and lore-check |
| Firewall | `hyper_firewall.py`, `hyper_gate_hook.py` | render `.agentignore` into tool rules; the PreToolUse gate hook |
| Engine | `hyper_engine.py` | parallel workflow engine: preflight, run, status, land, gc |
| Migration | `hyper_migrate.py` | v0.5.x to v0.6.0: detect, plan, apply, verify, rollback |
| Install | `hyper_scaffold.py`, `hyper_blocks.py` | project scaffold; HACF managed blocks in shared files |

### 3. State Management
- `spec/compiled/architecture.yml` is the ground truth for component state. Never edit it by hand:
  `hyper_ledger.py` and `hypergraph_updater.py` write it, and `/hyper-audit` reconciles it.
- Working drafts and reports go to `spec/active/`. Read a file before overwriting it.
- Specs follow the schemas below; `hyper_spec.py lint` checks them.

### 4. Interactive Interviews
As the Architect or Resolution Agent, enforce the **Pacing Loop**: at most **2 questions per
turn**, then wait for the answer.

### 5. No Probabilistic Traversal
Never guess architectural dependencies. Use the updater's or `hyper_ledger.py impact` output.

### 6. File Lifecycle Rules
- Never write to `spec/archive/` by hand; use `archive_specs.py`.
- Never read `spec/archive/` or `tests/candidate_outputs/` during agentic tasks (`.agentignore`).
- Only a person promotes a candidate output into `tests/fixtures/`.

### 7. Always-On Coding Rules
Apply `.agents/rules/`: `python.md`, `security.md`, `testing.md`, `package-management.md` (`uv`)
and `git-workflow.md` (protected branches `research/*`, `swimlane/*`; `spec/process/` is
commit-blocked by the git hooks).

### Safe Sandboxing & Automation
When instructed to write experimental code, test tools, or execute destructive scripts, you must **always checkout a branch prefixed with `research/` or `swimlane/`**.

These prefixes act as an automatic local sandbox. They are intercepted by a repository `pre-push` hook, physically preventing you from accidentally pushing unstable code or broken logic to the remote server. Never execute large automated changes directly on `main` or a feature branch.

---

## Binding Constraints (Lore)

`lore/MAP.md` lists the lore nodes; `python3 .agents/scripts/hyper_lore.py list --json` lists them
as data. They bind every agent:
- **CANON** nodes are constraints.
- A **stale CANON** node stays binding until it is retired or superseded.
- A **PROPOSED** node with `origin: user-decision` binds as a constraint until it is rejected.
- REJECTED, SUPERSEDED and RETIRED nodes are the graveyard: cite their ids, do not follow them.

Agents propose lore (`propose`, `note`, `stale`); only a person approves it, through
`/hyper-lore-approve` or `/hyper-audit` Phase 3.5. Lore approvals need **default or acceptEdits**
permission mode: the gate hook denies the gated verbs (approve, reject, retire, reopen, review) in
bypassPermissions, dontAsk and plan.

---

## Agent Firewall and Its Limits

`.agentignore` is the source; `hyper_firewall.py render` turns it into Claude Code permission
rules (`.claude/settings.json`) and best-effort ignore files for the other selected tools.

The rendered Claude Code rules deny Read and Edit of `spec/archive/**` and
`tests/candidate_outputs/**` (also for recognized Bash file commands such as `cat`, `sed`, `tee`
and redirect targets), deny Read of `.env`, `node_modules/`, `__pycache__/` and `.git/`, and deny
Edit of `architecture.yml`, `lore_hypergraph.yml`, `lore/CANON.md`, `lore/canon/**` and the
mission and slice manifests. Gated `hyper_lore.py` verbs need a confirmation.

Read and Edit denies in bypassPermissions: Enforced (recorded live test, Claude Code 2.1.316).

What the rules do NOT cover:
- Python or Node subprocesses that open files themselves.
- Commands that read files without naming them, e.g. `grep -r` from a parent directory.

The firewall is not a security boundary. The CANON gate is procedural and is not a security
boundary either: its remaining bypasses are a deliberately forged ticket and direct Python writes
to `lore_hypergraph.yml`. In engine sessions the executor runs with `--setting-sources user`, so
project settings are not loaded but user-level hooks still run. The bwrap oracle sandbox of the
engine is Linux-only. OS-level enforcement exists only in the engine's sandboxed phases.

---

## Available Skills

| Skill | Phase | Description |
|---|---|---|
| `hyper-architect` | 1 | Requirements interview → Draft_PRD.md |
| `hyper-redteam` | 1 | Adversarial analysis → RedTeam_Report.md |
| `hyper-resolve` | 1 | Trade-off mediation → SuperPRD + MiniPRDs; trade-offs become PROPOSED lore |
| `hyper-execute` | 2 | Implements one MiniPRD, updates the ledger |
| `hyper-audit` | 2 | Verifies the build, reconciles the ledger, runs the lore gate |
| `hyper-lore-approve` | any | Gated review of PROPOSED and stale CANON lore nodes |
| `hyper-discover` | -1 | Scans the codebase → initializes the ledger |
| `hyper-baseline` | -1 | Reverse-engineers the system → baseline SuperPRD |
| `hyper-init` | 0 | Scaffolds standard repository docs |
| `hyper-clear` | post-audit | Flushes conversation context, keeps specs and metrics |
| `hyper-contextualize` | any | Frames HACF as a toolchain in installed instruction files |
| `hyper-sop` | any | Master SOP and phase orientation |
| `hyper-status` | any | Master Plan snapshot |
| `hyper-consult-cto` | pre-spec | CTO advisor for architectural decisions |
| `hyper-co-research` | any | Peer research partner |
| `hyper-deepdive` | any | First-principles topic research |
| `hyper-create-skill` | any | Turn a prompt into a skill |
| `hyper-new-workflow` | any | Scaffold a skill and its IDE bridges |
| `hyper-document` | any | Update README, CHANGELOG, docs/ and AGENTS.md after a change |
| `hyper-grill-docs` | any | Domain-sharpening interview; hard decisions become lore ADRs |
| `hyper-handoff` | any | Compact the conversation into a handoff document in the OS temp dir |
| `hyper-process-document` | any | Record the session's process to spec/process/ |
| `hyper-session-update` | any | Sync activeContext.md and propose lore from session work |
| `hyper-refresh-memory` | any | Rebuild the mental model from the codebase |
| `hyper-update` | any | Upgrade HACF, keeping customizations |
| `hyper-troubleshooting` | any | Diagnose framework failures |
| `hyper-tutorial-generator` / `-run` / `-audit` | any | Generate, walk through and audit tutorials |
| `hyper-stitch-design` | any | UI/UX → design system spec |
| `hyper-prompt-engineer` | any | Collaborative prompt design |
| `hyper-template-architect` | any | Reverse-engineer a document into a template |
| `hyper-peer-review` | any | Triage peer review findings |
| `hyper-create-issue` | any | File a GitHub issue |
| `hyper-publish` | any | Proposed commits and PRs with approval gates |
| `hyper-learning-opportunity` | any | Structured teaching |

---

## Models

Model ids come from the `models` map of `.agents/config/orchestration.yml`: opus `claude-opus-5-5`,
sonnet `claude-sonnet-5-5`, haiku `claude-haiku-5-5`, fable `claude-fable-5-1`. `phase_models`
picks the engine's execute and audit models. A skill's optional `META.yml` names its
`assigned_model` tier and `model_version`.

---

## Framework Updates

`/hyper-update` upgrades HACF and keeps your edits to instruction files and settings; replaced
files are backed up under `.agents/.backup/`. Update integrity follows HACF's update-integrity policy:
- HACF releases are unsigned in v0.6.x. An unsigned upstream needs explicit consent in a terminal
  (the prompt, or `--allow-unsigned`), which shows the effective URL and commit and is logged to
  `.agents/logs/update-consent.jsonl`; it is refused under `-y`, without a TTY and under
  `HACF_AUTONOMOUS=1`. A non-descendant upstream needs `--allow-downgrade`.
- Any future release signing will use SSH signatures only.
- Never import a signing key into a global keyring.

---

## Framework Workflow (Quick Reference)

```
Phase -1 (Brownfield): /hyper-discover → /hyper-baseline
Phase  1 (Spec):       /hyper-architect → /hyper-redteam → /hyper-resolve → archive_specs.py
Phase  2 (Build):      /hyper-execute → hypergraph_updater.py → /hyper-audit
Phase  3 (Novel):      human review → tests/fixtures/ → update the spec
```

Each phase boundary needs a **fresh context window**: the Red Team must not see the Architect's
conversation.

---

## Schema Definitions

### v0.6.0 documents (SPEC.md)
SuperPRDs and MiniPRDs are document types inside `SPEC.md` files with YAML frontmatter
(`hacf_schema: 1`; `hacf_type: SystemOfRecord | SuperPRD | MiniPRD`). Writers: `hyper_scope.py`;
lint: `hyper_spec.py lint`.
- Mission `missions/<m>/SPEC.md`, `hacf_type: SuperPRD`: `# SuperPRD: <Feature>`, `## Intent`,
  sections 1-8 below, `## Slices` (generated), `## Status`.
- Slice `missions/<m>/slices/<NN-slug>/SPEC.md`, `hacf_type: MiniPRD` (keys include `node_id`,
  `parent`, `hacf_status`, `hacf_touches`, `hacf_oracle`, `lore_refs`): `# MiniPRD: <Module>`,
  `## Intent`, `## 1. The Confidence Mandate`, `## Mini-requirements` (numbered US-NNN),
  `## 3. Implementation Plan (Task List)`, `## 4. The Negative Space (Constraints)`,
  `## Proof contract` (`- [ ]` tests), `## Source material`, `## Intent visual`, `## Status`,
  `## Dependencies`.

### SuperPRD sections
```
# SuperPRD: [Feature Name]
## 1. Introduction & Goals      (problem, solution overview, audience)
## 2. Confidence Mandate        (score 1-10, clarifying questions)
## 3. Scope                     (in scope, out of scope)
## 4. User Stories (Atomic)     | ID | User Story | Acceptance Criteria | Priority |
## 5. Technical Specifications  (architecture and trade-offs, blast radius, execution checklist, contracts, dependencies)
## 6. Negative Constraints      (- **DO NOT** ...)
## 7. Risks & Mitigation
## 8. Success Metrics
```

### Legacy flat MiniPRD (dual-read)
`spec/compiled/MiniPRD_<Module>.md` files are still read:
```
# MiniPRD: [Module Name]
**Hypergraph Node ID:** [node_id]
**Parent Node:** [parent_node_id]
## 1. The Confidence Mandate          (score 1-10; questions if < 9)
## 2. Atomic User Stories             (- US-001: As [User], I want [Action] so that [Value])
## 3. Implementation Plan (Task List) (- [ ] Task 1: <10 min each)
## 4. The Negative Space (Constraints)
## 5. Integration Tests & Verification (Deterministic → expected output; Novel → candidate routing)
```

### Component ledger v2 (`spec/compiled/architecture.yml`)
`schema: hacf.ledger/v2`; single writer `hyper_ledger.py` (`validate` lints it).
```yaml
nodes:
  - id: component_id              # ^[a-z][a-z0-9_]{1,63}$
    dimension: Module             # System | Layer | Module | Atomic
    status: clean                 # clean | dirty | needs_review
    associated_file: path
    files: ['glob']               # what the node owns
    description: >-
      ...
    inputs:  [{data_type: x, source_id: node} | {data_type: x, external: label}]
    outputs: [{data_type: x, target_id: node} | {data_type: x, external: label}]
    edges: {depends_on: [], implements: [], member_of: []}
    spec_refs: [OPR.0.6.0.2]
    provenance: {...}
    review: {...}
removed_nodes: [{id, reason, slice, commit}]   # append-only tombstones
```
`clean`: verified; `dirty`: modified, awaiting audit; `needs_review`: in a changed node's blast
radius (implements parents and reverse depends_on; `member_of` is not traversed).

---

## Migration Notice

v0.6.0 moves specs to `SPEC.md` scopes under `missions/` and lore to `lore/`
(`hyper_migrate.py`). Legacy flat MiniPRDs are dual-read until v0.7.0, and no earlier than
2027-01-31; after that, migrate them with `python3 .agents/scripts/hyper_migrate.py apply`.
