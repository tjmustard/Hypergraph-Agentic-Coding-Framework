# v0.6.0 Operating Procedure

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

<a id="global-rules"></a>
## Global rules for every v0.6.0 session (read first)

- Precedence: the MiniPRD you are executing > the SuperPRD trade-off log (`spec/compiled/SuperPRD_v0_6_0.md`) > this annex. Only `/hyper-resolve` may edit the annex; it is frozen after compile, except through the [Replan procedure](#replan) (R14).
- A MiniPRD's `## Pipeline Operating Notes` override the skill text (SKILL.md and the `.claude/commands` bridge) where they differ; each MiniPRD opens with an Overrides banner naming the replaced steps (R15).
- A MiniPRD may diverge from this annex only where an R-row in the SuperPRD trade-off log records it. An unrecorded divergence is a defect: stop and ask the user (R19.9).
- The tracking document for NN 00-11 is the MiniPRD's own task checkboxes; never create PROGRESS.md, TODO.md or any file under `spec/active/` (R19.7). After an auto-compaction, re-read the MiniPRD and resume from the checkboxes (R17).
- Invoke scripts with `python3` (`python` does not exist on the dev machine). Every `.agents/scripts` change must run on `/usr/bin/python3` (3.10, PyYAML 5.4.1) and `.venv/bin/python` (3.11).
- Never hand-edit `spec/compiled/architecture.yml`. Bootstrap commit C2 was the single documented exception. Before S01a lands, use `hypergraph_updater.py` only as the MiniPRD's Pipeline Operating Notes instruct (S00: skip the updater). From S01a on, use `hyper_ledger.py` commands named in the Operating Notes.
- Never run `hyper_orchestrator.py` or `hyper_daemon.py` before S09b lands.
- Never read `PRDs/v0.6.0/` (reference-only pack with fabricated tool names).
- If a rewritten skill misbehaves on its own slice, fall back to the v0.5.9 text: `git show 984ca46:.agents/skills/<skill>/SKILL.md`.
- Every audit ends with `python3 dev/scripts/v060_gate.py finish <SID> --miniprd <spec/archive/MiniPRD_NN_<Slug>_AUDITED.md>`; it validates, checks scope, runs checks and commits with an `HACF-Slice: <SID>` trailer. `restore-ledger` rolls back a bad audit.
- The gate writes only on `swimlane/v0.6.0`: `post-resolve`, `finish` and `restore-ledger` refuse another branch or a detached HEAD before writing anything, and `status` warns. `finish` also refuses a MiniPRD whose `hacf-bootstrap` block differs from its copy in the post-resolve commit, so a block change after post-resolve needs the `--accept-block-change` waiver (slice and nn can never change). `post-resolve` runs once; a second run needs `--repin`.
- Never commit by hand on `swimlane/v0.6.0`. The gate records its own commits in `refs/hacf/v0.6.0/` (`post-resolve`, `slices/<SID>`, and `superseded/<SID>/<sha>` for a commit a re-finish replaced); a commit it did not record is not a completed slice, and its changes fall into the next slice's scope check.
- Never use `git add -N`, `git update-index --assume-unchanged` or `--skip-worktree`, `git replace` or a grafts file on `swimlane/v0.6.0`. `post-resolve` and `finish` refuse to run while the index has such an entry or a grafts file exists, and warn while `refs/replace/` has refs.
- When `finish` or `post-resolve` stops after its commit, follow the advice it prints. If HEAD is a finish whose slice ref was not recorded, `finish` refuses until you run `git reset --soft HEAD^` (the changes stay staged) and finish again. If `refs/hacf/v0.6.0/post-resolve` is missing, the gate prints the `git update-ref` command that records it again; the user runs it only after confirming the ref was lost, not removed on purpose.
- Use a waiver flag (`--accept-scope`, `--accept-block-change`, `--allow-empty`, `--refinish`, from C4 also `--out-of-order` and `--accept-validator-change`, and `--repin` for post-resolve) only after the user approves it in AskUserQuestion, and pass the user's words as REASON. Each waiver becomes a commit trailer that `status` lists; the gate cannot verify the approval, so the rule is procedural.
- Nothing is pushed. `swimlane/*` is push-blocked by `.git/hooks/pre-push`. After S00 finishes, the user re-copies the hardened `.agents/scripts/pre-push` into `.git/hooks/pre-push` (R19.2); the hook is advisory against `--no-verify`.
- To abandon a half-done slice, follow [Abort a slice](#abort-a-slice). If `main` moves during the build, follow [Main moved](#main-moved).

<a id="bootstrap"></a>
## Bootstrap steps (as approved)

### B0

B0 Preflight (read-only). Confirm `git status --porcelain` shows only `?? v0.6.0_migration_pack/` and the branch is main at 984ca46. Record tool versions in the B2 backup README:
- claude 2.1.305, uv 0.9.15, git 2.34.1, node v24.15.0
- /usr/bin/python3 3.10.12 with PyYAML 5.4.1 and pytest 9.0.3 (user site)
- .venv python 3.11.0rc1 with PyYAML 6.0.3 and pytest 9.0.3
- bwrap and socat are present (Claude Code sandbox dependencies on Linux)
- `python`, tmux, rig, codex, gemini and codebase-memory-mcp are absent

**Check.** Outputs match. Abort if any tracked file is modified.

### B1

B1 Branch and push guard. Run `git switch -c swimlane/v0.6.0`, then `cp .agents/scripts/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`.
Do not install the pre-commit hook: `.venv/bin/ruff check --no-cache --statistics .` reports 406 errors and `ruff format --no-cache --check` reports 30 files, so every commit would fail (pre-commit:25-26).
Do not install the commit-msg hook.

**Check.** `git branch --show-current` prints swimlane/v0.6.0. The hook is executable and contains the refs/heads/swimlane/* block (pre-push:6).

### B2

B2 Backups outside git: `~/.local/state/hacf/backups/v0.6.0/00-bootstrap-<UTC>/`. Contents:
- spec.tar.gz (all of spec/)
- agents-memory.tar.gz
- claude-settings.tar.gz
- a copy of .agentignore
- SHA256SUMS, including per-file sums of spec/compiled/*
Fallback if the sandbox denies $HOME writes: `.git/hacf-backups/<UTC>/`.
Not used: .agents/.backup/. It is not gitignored, and /hyper-publish runs `git add -A` (hyper-publish SKILL.md:205).

**Check.** `sha256sum -c SHA256SUMS` passes. `tar -tzf spec.tar.gz` lists all 3 spec/compiled files.

### B3

B3 Protect the /hyper-update SuperPRD: `mv -n spec/compiled/SuperPRD.md spec/compiled/SuperPRD_hyper_update.md`.
Reason: /hyper-resolve writes spec/compiled/SuperPRD.md (resolve SKILL.md:36), and so does /hyper-baseline (SKILL.md:18,25).

**Check.** `test ! -e spec/compiled/SuperPRD.md`, and the renamed file's sha256 equals the B2 per-file sum.

### B4

B4 Commit C1: track the unrepaired spec state on the swimlane branch.
`git add -f spec/compiled spec/archive spec/active && git commit -m 'bootstrap(v0.6.0): track pre-migration spec/ state on swimlane (unrepaired)'`
Every later ledger or spec change then becomes a reviewable, restorable git diff. S15b removes spec/ from the tree before landing. Old updaters only clone the tip of main (hyper_update_core.py:127-137, HACF-install.sh:378).

**Check.** `git show --stat HEAD` lists only spec/**. The architecture.yml blob is byte-identical to the backup.

### B5

B5 Commit C2: minimal ledger repair. This is a one-time, documented exception to AGENTS.md:77. The file does not parse, so no script can load it.
(a) Three descriptions get folded scalars. Rewrite `  description: <text>` as `  description: >-` with the text on the next line, indented 4 spaces; leave continuation lines unchanged:
- :311 hyper_update_merge_engine (` ###` at :313 starts a comment)
- :454 hyper_fix (`'# FILE: path'` at :459 contains ': ')
- :517 semantic_graph_merger (`checks: YAML` at :522)
(b) Delete `- hyper_resolve` from context_compaction (:80). Rewrite rules_migration's edges (:134-135) as `depends_on: []`. Deleting the item alone leaves null, which makes the v0.5.9 updater raise TypeError at hypergraph_updater.py:76.
(c) Set hyper_update `implements: []` and hyper_update_core_logic `implements: [hyper_update]`. This fixes the edge inverted at :234-235.

**Check.** (1) yaml.safe_load gives 19 nodes.
(2) Every edges.depends_on and edges.implements value is a list.
(3) semantic_graph_merger._validate_architecture_yaml returns (True, '').
(4) Copy the file to <backup>/scratch/ and run `python3 .agents/scripts/hypergraph_updater.py <scratch>/architecture.yml hyper_update_core_logic` on the copy. It exits 0, and its blast radius is {hyper_update_core_logic, hyper_update, hyper_update_merge_engine, hyper_update_recovery}. Never run it on the real file: it rewrites the file (:83-88).
(5) `git diff HEAD` shows only these 7 edits, then commit.

### B6

B6 Move the pack (D13): `mkdir -p PRDs && mv v0.6.0_migration_pack PRDs/v0.6.0`, then write PRDs/v0.6.0/README.md.
The README says:
- The pack is REFERENCE ONLY.
- Never read it during slices and never copy from it.
- Fabricated or unverified items: cbm_get_symbol, cbm_blast_radius, crg_find_symbol and crg_detect_changes_brief; npm @deusdata/codebase-memory-mcp and a code-review-graph npm package; .cbm/graph.db; requires_build_pass:false; daemonised stdio servers.
- Injection bugs: shell=True, eval, raw {target}.
- `## name:` skill stubs; delete-on-reject.
- The normative design is dev/design/v0.6.0/; the story is lore/STORIES/STORY-0001.

**Check.** `ls PRDs/v0.6.0` lists README.md, migration_pack_builder.md and claude_code_migration_prompt.md. `test ! -e v0.6.0_migration_pack`.

### B7

B7 PROPOSED lore drafts in lore/ADRs/:
- ADR-0001 OpenRig topology at repo root (D1, D2, D4, D14)
- ADR-0002 SuperPRD/MiniPRD as SPEC doc types (D3)
- ADR-0003 ledger plus code-graph adapter (D5)
- ADR-0004 lore engine (D6)
- ADR-0005 conversational CANON gate (D7)
- ADR-0006 headless executor engine with sandbox (D8)
- ADR-0007 retire unwired stores (D9)
- ADR-0008 agent firewall (D10)
- ADR-0009 single release through own pipeline, with per-slice commits and gate (D11, D12, D13)
- ADR-0010 upstream update integrity (design choice, origin: design-choice)
- ADR-0011 script runtime policy: Python >=3.10, PyYAML >=5.4 (design choice)
Also lore/STORIES/STORY-0001-the-v0.6.0-migration-pack.md.
Draft frontmatter:
- hacf_lore_draft: 1; id; type; title; status: PROPOSED; decisions: [Dn]
- origin: user-decision | design-choice
- anchors: {components: [the 19 existing ids], files: [existing globs], symbols: []}
- planned: {components: [], files: []}
- relates_to; supersedes: []
- provenance: {created_by: 'agent:claude-code bootstrap', created_at, source}
Body sections: Context, Decision, Considered Options (including the pack's version), Consequences, Evidence.

**Check.** Frontmatter parses. Ids are unique and well-formed. Status is PROPOSED. anchors.components is a subset of the 19 ids. Each anchors.files glob matches at least 1 path; planned entries are exempt.

### B8

B8 Write the normative annex, the gate and the operating procedure.
(1) dev/design/v0.6.0/:
- README.md: precedence is MiniPRD > SuperPRD trade-off log > annex; only /hyper-resolve may edit the annex; frozen after compile.
- decisions.md: D1-D14, rejected alternatives, critique resolutions.
- schemas.md: every schema in this design, verbatim.
- slices.md: 29 slice seeds, each with SID, NN, depends_on, files, tasks, tests, node_id, ensure-node command and audit_phase3 mode.
- operating-procedure.md
- evidence.md: file:line references and URLs, plus the UNVERIFIED list.
(2) dev/scripts/v060_gate.py: stdlib + PyYAML, runs on Python >=3.10. Subcommands:
- `post-resolve [--expect N] [--repin REASON]`
- `finish <SID> --miniprd <path> [--accept-scope REASON] [--accept-block-change REASON] [--allow-empty REASON] [--refinish REASON] [--no-track-spec]`
- `restore-ledger`
- `status`
- `selftest`: builds a throwaway repo under $TMPDIR.
(3) Prepend '## v0.6.0 Bootstrap Operating Procedure (read first)' to .agents/memory/activeContext.md, which is tracked and which the Memory Check reads first (hyper-execute SKILL.md:18-19). It holds the global rules and an appended 'Completed slices' list. From the post-resolve commit on, `finish` reports any change to this section (its heading through the `<!-- END v060-completed -->` line) as out of scope unless a `files` entry names `.agents/memory/activeContext.md` exactly (a wildcard that matches the file does not); the gate's own appended line is committed with each slice.

**Check.** `python3 dev/scripts/v060_gate.py selftest` passes under /usr/bin/python3 and under .venv/bin/python. Every annex anchor cited by slices.md exists.

### B9

B9 Write spec/active/Draft_PRD.md: '# SuperPRD: HACF v0.6.0 - OpenRig topology, lore hypergraph, code-graph blast radius, headless engine'. It follows the AGENTS.md:250-286 schema, with sections §1-§8.
- §1: about 15 key evidence bullets.
- §2: the UNVERIFIED items as clarifying questions.
- §5: trade-off log D1-D14 and ADR-0010/0011, the 19-node blast radius, and an Execution Checklist of 29 slices. §5 cites dev/design/v0.6.0/schemas.md and slices.md as normative annexes the Red Team must read.
Appendix A, Red Team focus:
- repo-root rig workspace and daemon discovery
- sandbox and ref-guard sufficiency
- gate bypass paths
- worktrees with an untracked ledger
- managed-block merges
- v0.5.x uninstaller and installer leakage
- unsigned-upstream policy
- interpreter policy
Appendix B, compile instructions for /hyper-resolve:
- Emit spec/compiled/SuperPRD_v0_6_0.md first, then MiniPRD_NN_<Slug>.md for NN 00-28, at most 5 per response.
- Use the legacy flat format with **Hypergraph Node ID:** and **Parent Node:** lines.
- Every MiniPRD ends with a '## Pipeline Operating Notes' section and a fenced ```hacf-bootstrap``` YAML block: {slice, nn, title, depends_on, node_id, parent, ensure_node, files, oracle, audit_phase3, removes_nodes, track_spec, lore_refs, annex_refs}.
- Use the AGENTS.md:290-321 schema. The template path in SKILL.md:36 does not exist under that case.
- Use python3; `python` is absent.
- Archive with `python3 .agents/scripts/archive_specs.py v0_6_0`.
- If a resolution changes a normative schema, edit the annex in place and log it.

**Check.** grep finds the 8 numbered H2 sections and Appendices A-B. Every cited annex path exists. No mention of 'this conversation'. Every SID S00..S15b appears with its NN.

### B10

B10 Commit C3: `git add PRDs/v0.6.0 lore dev .agents/memory/activeContext.md && git add -f spec/active/Draft_PRD.md && git commit -m 'bootstrap(v0.6.0): reference pack, PROPOSED lore drafts, normative annex, gate, Draft_PRD'`.
Commits C1-C3 are part of the approved procedure. The parent plan's rule 'no commits unless asked' is satisfied only if plan approval explicitly covers commits on swimlane/v0.6.0.

**Check.** `git show --stat HEAD` lists only these paths, and `git status --porcelain` is clean.

### B11

B11 Hand-off. The main agent never runs the Red Team or the resolver.
1. Fresh session: /hyper-redteam.
2. Fresh session: /hyper-resolve, mediation only (R17). It resolves every Red Team flag, applies the annex edits and writes `spec/compiled/SuperPRD_v0_6_0.md` with every R-row.
   - 2a. Six fresh /hyper-resolve compile sessions, one per batch (00-04, 05-09, 10-14, 15-19, 20-24, 25-28), each following [Compile instructions](#compile-instructions). Before them the user puts `{"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "85"}}` in the gitignored `.claude/settings.local.json`. The last batch session runs the self-check and archive_specs.py.
   - 2b. A fresh separate session implements [Gate amendment C4](#gate-amendment-c4); the user reviews and commits it (R16). C4 must be committed before step 3.
3. `python3 dev/scripts/v060_gate.py post-resolve` on `swimlane/v0.6.0` (it refuses another branch or a detached HEAD). It:
   - refuses if a post-resolve commit already exists, unless `--repin REASON` is given (trailer HACF-Repin); every post-resolve commit must have one parent and change only the directories below
   - refuses an assume-unchanged, skip-worktree or intent-to-add index entry and a grafts file; every refusal, including 'nothing to commit', comes before any write
   - verifies 29 MiniPRDs (NN 00-28), each with an Operating Notes block (duplicate YAML keys refused; a single-line title with at least one letter or digit, no control, format or separator characters, and no 'HACF-' in any letter case), depends_on pointing only to lower NN, safe files globs (no leading '/', no '..' segment, no backslash, a literal first path segment, no catch-all such as '**') and annex refs that resolve
   - refuses, before any write, every changed or untracked path outside spec/compiled/**, spec/archive/**, spec/active/** and dev/design/v0.6.0/** (never dev/design/v0.6.0/guards.json; spec/process/** is never committed); the user commits, stashes or removes stray files first. It also refuses a symlink or non-regular file among the paths it would commit, the compiled MiniPRDs and the SuperPRD
   - renames a bare SuperPRD.md to SuperPRD_v0_6_0.md, refusing if the target exists
   - checks the SuperPRD_hyper_update.md hash is unchanged and guards.json is unchanged versus HEAD (content and mode)
   - tars spec/compiled plus the new archive folder to the backup dir (a backup root inside the work tree falls back to <git-common-dir>/hacf-backups)
   - stages only those directories (force-adding the gitignored spec/ ones), refuses if anything else is staged or if a file it parsed is staged with other content, commits (checking the commit's tree), and records the commit as refs/hacf/v0.6.0/post-resolve
4. For each NN in order:
   - fresh `/hyper-execute spec/compiled/MiniPRD_NN_<Slug>.md`
   - fresh `/hyper-audit` on the same file
   - `python3 dev/scripts/v060_gate.py finish <SID> --miniprd spec/archive/MiniPRD_NN_<Slug>_AUDITED.md`. The audit agent runs this as its final step. It validates, checks scope, runs dev/scripts/check.sh once it exists (refusing if the checks changed anything it validated or wrote any other path), commits 'v0.6.0 <SID>: <title>' with the trailer HACF-Slice: <SID>, verifies the commit (parent, tree, paths and message, so its content is exactly what it checked) and records it as refs/hacf/v0.6.0/slices/<SID>. It refuses a symlinked --miniprd file, and it refuses while spec/compiled/MiniPRD_NN_*.md still exists: the audit moves the MiniPRD, it does not copy it. The branch, block-pinning and waiver rules in the global rules apply.
5. Landing, user-initiated, after S15b: `git switch main && git merge --ff-only swimlane/v0.6.0` (or --squash). Then check `git ls-tree -r --name-only HEAD -- spec tests` is empty and run dev/scripts/check.sh. Then run /hyper-publish from main. Pre-push blocks only swimlane/* and research/* (from S00 also any sha reachable only from them, R19.2). With `--squash` the slice commits stay reachable only in the maintainer's clone and the per-finish bundles (R19.5); proofs keep `swimlane_sha` for them (R22.8). If `main` moved during the build, land with a merge commit instead of `--ff-only` ([Main moved](#main-moved)).

**Check.** After post-resolve the commit contains only spec/** and, when /hyper-resolve edited the annex, dev/design/v0.6.0/** (never guards.json). After each finish, HEAD has the trailer, refs/hacf/v0.6.0/slices/<SID> names HEAD, and the tree is clean.

<a id="hacf-bootstrap-block"></a>
## The `hacf-bootstrap` block (required in every MiniPRD)

Every compiled `spec/compiled/MiniPRD_NN_<Slug>.md` ends with a `## Pipeline Operating Notes` section containing prose instructions for the execute and audit sessions, followed by one fenced code block whose info string is `hacf-bootstrap` and whose body is YAML with exactly these keys:

```yaml
slice: S01a                 # SID from slices.md
nn: 1                       # integer 0-28; matches the filename's NN
title: Ledger core          # short title
depends_on: [S00]           # SIDs; each must have a lower nn
node_id: component_ledger   # ledger node this slice registers or modifies, or 'none'
parent: orchestration_layer # MiniPRD 'Parent Node', or 'none'
ensure_node: 'python3 .agents/scripts/hyper_ledger.py ensure-node ...'  # or 'none'
files: ['.agents/scripts/hyper_ledger.py', 'dev/tests/ledger/**']       # allowed change globs
oracle: [['uv', 'run', 'pytest', 'dev/tests/ledger', '-q']]            # argv lists, never a shell string
audit_phase3: review-patch  # legacy-haiku | review-patch | skip (S00 skip; S01a on review-patch, Q28/R19.11)
removes_nodes: []           # ledger node ids this slice deliberately deletes
track_spec: true            # force-add spec/compiled and spec/archive into the slice commit (false only for S15b)
lore_refs: [ADR-0003]
annex_refs: ['dev/design/v0.6.0/slices.md#s01a', 'dev/design/v0.6.0/schemas.md#architecture-yml-changes']
```

Annex references resolve when the target file contains `<a id="ANCHOR"></a>` or a heading whose GitHub-style slug equals ANCHOR.

<a id="compile-instructions"></a>
## Compile instructions (MiniPRD batch sessions)

Added by /hyper-resolve (R17). Ported from the Draft PRD's Appendix B, which is archived, with the Red Team resolutions applied. These rules override `.agents/skills/hyper-resolve/SKILL.md` where they differ: its v0.5.9 text targets a bare `SuperPRD.md`, a template path that does not exist (`.agent/schemas/MiniPRD_Template.md`) and `python`.

**Inputs.** `spec/compiled/SuperPRD_v0_6_0.md`, this annex (README first), `spec/compiled/architecture.yml` (read only; never run the updater on it) and the MiniPRDs already compiled in earlier batches (for consistency only). Not the Draft PRD, the Red Team report or `spec/active/Resolution_Log.md`: everything binding is in the SuperPRD and this annex. Not `PRDs/v0.6.0/`.

**Batches.** One fresh session per batch: 00-04, 05-09, 10-14, 15-19, 20-24, 25-28. Write the batch's files in NN order, at most 5 per response. A compile session never edits the SuperPRD or this annex; a needed change is a defect to report to the user (it needs a [Replan](#replan) R-row). Never write under `lore/`, never write a bare `spec/compiled/SuperPRD.md`, never touch `spec/compiled/SuperPRD_hyper_update.md` or any other existing file in `spec/compiled/`.

**File names.** Exactly the 29 filenames in the SuperPRD's Execution Checklist (`spec/compiled/MiniPRD_NN_<Slug>.md`; NN two digits equal to the seed's NN). Do not split, merge or renumber slices.

**MiniPRD format.** Legacy flat v0.5.9 format and the AGENTS.md:290-321 MiniPRD schema.
- H1 `# MiniPRD: <SID> - <title>`, then `**Hypergraph Node ID:** <node_id or none>` and `**Parent Node:** <parent or none>` (SuperPRD §5.2).
- Then the Overrides banner (R15): one blockquote paragraph whose first line begins `> **Overrides (read first):**`. It names each SKILL.md step the Operating Notes replace (the Phase 3 mode, the Phase 4 archive command, the updater command and node ids, the tracking document = this MiniPRD's task checkboxes, `python3` instead of `python`) and ends by pointing to `## Pipeline Operating Notes`. The C4 gate looks for that first line and for the text `## Pipeline Operating Notes` inside the banner.
- Then exactly these sections: `## 1. The Confidence Mandate`, `## 2. Atomic User Stories`, `## 3. Implementation Plan (Task List)`, `## 4. The Negative Space (Constraints)`, `## 5. Integration Tests & Verification`, and last `## Pipeline Operating Notes`, ending with the `hacf-bootstrap` block (nothing after it).
- Carry every seed task, test (by its exact `test_` name), negative constraint, file and dependency from `slices.md` into the matching MiniPRD, including the seed's `**Red Team resolutions:**` items. The C4 seed cross-check refuses an omission at post-resolve unless the SuperPRD's Seed deviations table names it. Split tasks so each takes under 10 minutes (`- [ ] Task N: ...`). Open seed risks become clarifying questions in §1. Live tests are opt-in by marker and need consent; recordings follow R27 (verbatim captures in `dev/tests/fixtures/recorded/<SID>/`, labeled synthetic fixtures in `dev/tests/fixtures/synthetic/<SID>/`, novel outputs in `tests/candidate_outputs/<SID>/`).
- `python3` in every command, never `python`.
- Self-contained for fresh execute and audit runs, which may read the MiniPRD, `.agents/memory/activeContext.md`, the SuperPRD and this annex. Never point them at the Draft PRD, the Red Team report or the resolution log.

**`## Pipeline Operating Notes`** (prose for the execute and audit runs, then the block):
- **Ledger node:** the node id, or `none: skip the updater` (S00). Name every existing node whose file the slice edits (SuperPRD §5.2). Removal commands, in order: S10 runs `python3 .agents/scripts/hyper_ledger.py remove-node context_compaction --reason D9`; S09b runs `python3 .agents/scripts/hyper_ledger.py set-edges --id hyper_daemon --remove-depends-on hyper_fix`, then `python3 .agents/scripts/hyper_ledger.py remove-node hyper_fix --reason D8`. Skip the updater for removed ids.
- **Execute:** the exact `ensure-node` command, flag spellings copied verbatim from [schemas.md#architecture-yml-changes](schemas.md#architecture-yml-changes) (R18.12; `--parent orchestration_layer` writes `member_of`, R21.2), run before the updater. NN 00-11: `python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <ids>`; from NN 12 on, the rewritten skill's legacy branch. NN 00-11: "the tracking document is this MiniPRD's task checkboxes; create no progress files" (R19.7). S09a2, S12b, S13a, S13c and S15a2: "after any auto-compaction, re-read this MiniPRD and resume from the checkboxes" (R17).
- **Audit Phase 3:** per `audit_phase3`. `skip` for S00; `review-patch` from S01a on (Q28): `python3 .agents/scripts/hyper_ledger.py review-template --json`, a sub-agent returns a JSON patch written to a temp file, then `python3 .agents/scripts/hyper_ledger.py apply-review --patch <file> --slice <SID>` (R27.4). This replaces the v0.5.9 free-text rewrite (`.agents/skills/hyper-audit/SKILL.md:31-35`). From NN 03 on, a non-empty `unmapped` list from `--from-diff` is an audit finding (R21.1).
- **Audit Phase 4:** NN 00-05 use the v0.5.9 move to `spec/archive/MiniPRD_NN_<Slug>_AUDITED.md`; from S03b (NN 06) on, `python3 .agents/scripts/archive_specs.py --miniprd <path>`.
- **Post-audit checks** before `finish`: the slice oracle; `python3 .agents/scripts/hyper_ledger.py validate --against-ref HEAD` (NN 01 on); `python3 .agents/scripts/hyper_spec.py lint --all` (NN 05 on); `python3 .agents/scripts/hyper_lore.py validate` (NN 08 on). The gate repeats the validators against the scope base and, after C4, also runs their scope-base copies (R16); a slice that changes a validator notes that `finish` may need `--accept-validator-change` with the user's approval.
- **Fallbacks:** the v0.5.9 skill text via `git show 984ca46:.agents/skills/<skill>/SKILL.md` (an S06 audit that uses it appends `Fallback used: <skill>@984ca46` to its Operating Notes prose, Q26); `python3 dev/scripts/v060_gate.py restore-ledger` after a bad Phase 3; slice-specific stops (S02 stops and escalates when the CLI spike fails, then [Replan](#replan)); [Abort a slice](#abort-a-slice). Declined consent for a live test is recorded as skipped; verdicts and recorded outputs are never invented (R27).
- **Finish:** `python3 dev/scripts/v060_gate.py finish <SID> --miniprd spec/archive/MiniPRD_NN_<Slug>_AUDITED.md` on `swimlane/v0.6.0` (S15b adds `--no-track-spec`, allowed because its block says `track_spec: false`, and passes the archived path that `archive_specs.py --paths` printed for MiniPRD_28). Move the MiniPRD into `spec/archive/` first; never commit by hand; keep tests from writing into the repo; on a scope failure, fix within the globs; follow the advice finish prints; never use `git add -N` or the assume-unchanged and skip-worktree flags. Use a waiver flag (`--accept-scope`, `--accept-block-change`, `--allow-empty`, `--refinish`, `--out-of-order`, `--accept-validator-change`) only after the user approves it in AskUserQuestion, with the user's words as REASON.
- **Global rules**, restated briefly: never run `hyper_orchestrator.py` or `hyper_daemon.py` before S09b lands; never read `PRDs/v0.6.0/`; never hand-edit the ledger; nothing is pushed.

**The `hacf-bootstrap` block.** Exactly the 14 keys of [the block schema](#hacf-bootstrap-block). Value rules:
- The YAML must load with `yaml.safe_load` under PyYAML 5.4.1: quote every glob and every string containing `: `, `#` or a leading `*`; write `nn` unpadded (`nn: 8`, not `nn: 08`).
- `files` covers every path the slice's execute and audit runs change except the gate's exemptions (the ledger, the slice's own MiniPRD paths, and `activeContext.md` outside its procedure section): the seed's files, its test dirs, its `dev/tests/fixtures/recorded/<SID>/**` and `synthetic/<SID>/**` dirs for live-test slices (R27), and the lore paths the notes call for. Never list `.agents/memory/activeContext.md` itself.
- Globs are as narrow as the seed's paths (never `.agents/**`), repo-relative, with no leading `/`, no `..` segment, no backslash, a literal first segment, no catch-all (`*`, `**`, `**/*`, `*/**`, `**/**`), no partial-segment `**` such as `dev/tests**`, and no surrounding whitespace (R18.8).
- S15b: `files` includes `"spec/**"`, `"tests/**"`, `"missions/release-0.6.0/**"` (R19.10) and the lore paths for its Red Team resolutions story, whose id is the next free STORY id (R23.2, R43); `track_spec: false`.
- `removes_nodes` is `[hyper_fix]` for S09b and `[context_compaction]` for S10, `[]` otherwise.
- `lore_refs`: ADR ids from the SuperPRD trade-off log that the slice implements.
- `annex_refs`: at least the slice's own anchor in `slices.md` (the SID in lower case, e.g. `dev/design/v0.6.0/slices.md#s09a1`) plus every schema or subsystem anchor the slice implements; every reference must resolve.

**Self-check and archive** (the batch 25-28 session, after writing MiniPRD_28):
1. For each of the 29 files: the two header lines, the Overrides banner, the five schema sections, `## Pipeline Operating Notes` and exactly one `hacf-bootstrap` block with the 14 keys; every `depends_on` SID has a lower `nn`; every `annex_refs` entry resolves; every seed file is covered by `files` and every seed `test_` name appears (the C4 cross-check rule below), or the SuperPRD's Seed deviations table names the gap.
2. `python3 .agents/scripts/archive_specs.py v0_6_0`; log the absolute path it prints.
3. `git restore --source=HEAD --worktree -- spec/active/FAILED_WORKFLOWS.md` (tracked since C1; schemas.md keeps the failure log in `spec/active/`; the archived copy stays as the record, R18.11).
4. Do not commit, run the gate, run the updater or start any slice. Tell the user: run the [C4 session](#gate-amendment-c4) if it is not committed yet, then `python3 dev/scripts/v060_gate.py post-resolve` on `swimlane/v0.6.0`.

<a id="gate-amendment-c4"></a>
## Gate amendment C4 (before post-resolve)

Added by /hyper-resolve (R16). `dev/scripts/v060_gate.py` is in no slice's `files`, and `post-resolve` refuses changes outside `spec/` and this annex, so the gate is hardened once, before `post-resolve`, by a fresh separate session (not /hyper-resolve, not /hyper-redteam). That session reads this section and the SuperPRD rows it cites (R14, R15, R16, R18, R19.5), changes only `dev/scripts/v060_gate.py` and the protected procedure section of `.agents/memory/activeContext.md`, and adds a selftest case for every item. `python3 dev/scripts/v060_gate.py selftest` must pass under `/usr/bin/python3` and `.venv/bin/python`, and `.venv/bin/ruff check --no-cache` and `ruff format --no-cache --check` must pass on the gate. The user reviews the diff and commits it on `swimlane/v0.6.0` with `git add dev/scripts/v060_gate.py .agents/memory/activeContext.md && git commit -m 'bootstrap(v0.6.0): C4 gate amendment'`, a documented exception like C1-C3, with nothing else staged (the uncommitted annex and `spec/` changes go into the post-resolve commit).

C4.1 **Validator independence (R16, RT-94).** For each validator in the gate's list (`hyper_ledger.py validate`, `hyper_spec.py lint --all`, `hyper_lore.py validate`) that exists at the scope base, also run the scope-base copy (`git show <base>:<path>` into a temp dir, same arguments, run against the working tree). The working-tree version must pass. The base version must report no finding it did not already report when run against the scope base's own tree. Otherwise print both outputs and refuse unless `--accept-validator-change REASON` (new waiver; trailer `HACF-Validator-Change`).

C4.2 **Repin rule (R14).** `post-resolve --repin REASON` refuses any change to the `hacf-bootstrap` block of a SID that has a slice ref, and any removal of such a MiniPRD.

C4.3 **Overrides banner (R15).** `post-resolve` refuses a MiniPRD without a line beginning `> **Overrides (read first):**` between the `**Parent Node:**` line and `## 1. The Confidence Mandate`, whose blockquote paragraph contains `## Pipeline Operating Notes`.

C4.4 **Dependencies (RT-28).** `finish` refuses when a `depends_on` SID has no slice ref, unless `--out-of-order REASON` (new waiver; trailer `HACF-Out-Of-Order`).

C4.5 **`--no-track-spec` (RT-95).** Refused unless the pinned block has `track_spec: false`.

C4.6 **Hooks (RT-96).** Gate commits use `-c core.hooksPath=<empty temp dir>` and `--no-verify`.

C4.7 **Tested bytes equal committed bytes (RT-97).** For every committed path, `git hash-object --no-filters <path>` equals the staged blob. Refuse when `git ls-files -o -i --exclude-standard -- dev/tests .agents/scripts` lists a `*.py` or `conftest.py` outside `__pycache__/`.

C4.8 **Entry modes (RT-98).** `finish` refuses 120000 (symlink) and 160000 (gitlink) entries among committed paths.

C4.9 **Slice refs (RT-99).** A slice ref counts only for the 29 SIDs pinned in the post-resolve commit. At each `finish`, every recorded slice commit is re-diffed against its own pinned `files` plus the paths its waiver trailers accepted. `--accept-block-change` writes the old and new value of each changed key into the commit body.

C4.10 **Lookalike headings (RT-100).** The activeContext.md exemption stays as it is; refuse whitespace- or case-folded duplicates of the protected heading and of the BEGIN/END completed-list markers anywhere in the file.

C4.11 **Globs and process groups (RT-101).** `post-resolve` and `finish` refuse a `**` that is not a whole path segment and any leading or trailing whitespace in a glob. check.sh runs with `start_new_session=True`; the gate kills the process group afterwards.

C4.12 **Seed cross-check (RT-104).** `post-resolve` parses every seed in `slices.md` (from its `<a id="sNN">` anchor to the next anchor) and refuses unless, for the MiniPRD with that SID:
- every entry under `**Files to create**`, `**Files to modify**` and `**Files to delete**` (first token after `- `, backticks and trailing punctuation stripped, `(none)` skipped; a trailing `/` means `/**`) is matched by a block `files` glob, or equals one when the entry is itself a glob; entries that are scope exemptions (`spec/compiled/architecture.yml`, the slice's own MiniPRD paths, `.agents/memory/activeContext.md`) are skipped, because `files` never lists them;
- every token matching `\btest_[A-Za-z0-9_]+\*?` under `**Tests (deterministic)**` appears in the MiniPRD (a token ending in `*` needs at least one name with that prefix);
- the block's `depends_on` equals, as a set, the SIDs on the seed's `- **Depends on:**` line.
A mismatch passes only when the SuperPRD's `Seed deviations` table has a row naming the SID and the exact item.

C4.13 **Tool version (RT-09).** Each slice commit body records the first line of `claude --version` when the command exists.

C4.14 **Bundles (R19.5, RT-58).** After recording a slice ref, `finish` runs `git bundle create <backup root>/hacf-v060-<SID>.bundle --branches --glob=refs/hacf` (the backup root of B2) and prints its path; a bundle failure is a warning, never a rollback.

C4.15 **activeContext.md.** In the protected procedure section, add to the precedence bullet: "A MiniPRD's Operating Notes override the skill text where they differ; an annex divergence without an R-row is a defect: stop and ask." Add `--out-of-order` and `--accept-validator-change` to the waiver bullet. Keep the heading and the BEGIN/END markers byte-identical.

The new waiver flags join `WAIVER_FLAGS`, become commit trailers and show in `status`.

<a id="replan"></a>
## Replan (R14)

Use when a slice cannot finish because the plan is wrong: the S02 CLI spike fails (Q1; D2 returns to the user), the S04 contract test or the S08 or S11 live test contradicts an assumption, or the user rejects a design point mid-build.
1. Stop the slice; do not finish it. Record the failure: from NN 08 on as a PROPOSED lore STORY through `python3 .agents/scripts/hyper_lore.py propose`; before NN 08 as a stop report appended to the MiniPRD's Operating Notes prose. Abort partial edits ([Abort a slice](#abort-a-slice)).
2. The user starts a fresh /hyper-resolve in replan mode: before NN 12 with `git show 984ca46:.agents/skills/hyper-resolve/SKILL.md`, from NN 12 on with the rewritten skill. Its inputs are the SuperPRD trade-off log, this annex, the failure record, and finished slices' MiniPRDs read through `git show refs/hacf/v0.6.0/post-resolve:spec/compiled/<file>` (not from `spec/archive/`).
3. It edits this annex only for unfinished slices, logs each change as a new R-row (continuing the numbering) and, where a seed changes, a Seed deviations row; it recompiles only the unfinished MiniPRDs in `spec/compiled/`.
4. The user runs `python3 dev/scripts/v060_gate.py post-resolve --repin "<the user's words>"`. After C4 the gate refuses any change to the block of a slice that has a slice ref (C4.2).
5. The per-slice loop resumes at the stopped NN.

<a id="main-moved"></a>
## Main moved (R19.8)

Default: change nothing on `main` until v0.6.0 has landed. If `main` moved anyway, land with `git merge --no-ff swimlane/v0.6.0` instead of `--ff-only`. If the build needs a `main` change (for example a hotfix the slices depend on), merge `main` into `swimlane/v0.6.0` directly before starting the next slice; the gate reports it as a commit it did not record, and that slice's `finish` needs `--accept-scope "<the user's words>"` for main's paths. Never rebase `swimlane/v0.6.0`: it strands `refs/hacf/v0.6.0/*`.

<a id="abort-a-slice"></a>
## Abort a slice (R19.6)

When an execute or audit session dies mid-slice or has to start over:
1. Inspect `git status --porcelain`.
2. Set the partial work aside (R35). List the changed and untracked paths inside the block's globs: `git status --porcelain=v1 -z -uall --no-renames -- <block files globs as ':(glob)<glob>' pathspecs>` (status never fails on a glob that matches nothing); each NUL-terminated entry is two status letters, a space and the path. An empty list means there is nothing to set aside. Otherwise stash exactly those paths: `git stash push -u -m "abort <SID>" -- <each listed path as a ':(literal)<path>' pathspec>`. Never pass a glob to `git stash push`: one that matches no file makes it exit 1 after it saved the entry, with the tree left dirty (S04 stop report, git 2.34.1). Then check that the same `git status` command prints nothing and that `git stash show --include-untracked --name-only stash@{0}` lists the paths. To discard instead, the user confirms first, then `git restore --staged --worktree` (tracked paths) and `git clean -f` (the `??` paths), limited to the same listed paths as `:(literal)` pathspecs.
3. If the ledger changed, run `python3 dev/scripts/v060_gate.py restore-ledger`.
4. If the audit had already moved the MiniPRD into `spec/archive/`, move it back to its `spec/compiled/` name.
5. Start a fresh /hyper-execute on the compiled MiniPRD.

