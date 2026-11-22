# v0.6.0 Schemas

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

<a id="target-layout"></a>
## Target layout (user project and HACF repo)

USER PROJECT after v0.6.0 (fresh install or migrated):
<repo>/
- SPEC.md: hacf_type SystemOfRecord; also OpenRig project intent (project.yaml install.intent)
- project.yaml: openrig.project/v0alpha1, the exact v0.6.13 scaffold
- workspace.yaml: openrig.workspace/v0alpha1, projects [{id: default, root: .}]
- lore_hypergraph.yml: hacf.lore/v1
- No tracked .gitattributes is written (R10). The merge-driver attributes go into the repo-local, untracked
  `$(git rev-parse --git-common-dir)/info/attributes`, next to the repo-local driver config the scaffold
  writes, as a managed block:
  # BEGIN HACF MANAGED BLOCK: merge-drivers
  lore_hypergraph.yml merge=hacf-lore
  lore/MAP.md merge=hacf-generated
  lore/CANON.md merge=hacf-generated
  # END HACF MANAGED BLOCK: merge-drivers
  A user's own tracked .gitattributes is never written or deleted.
- lore/
  - MAP.md: hand-written intro plus a generated region
  - CANON.md: generated; a pure concatenation of lore/canon/*.md for CANON nodes (R8)
  - ADRs/ADR-NNNN-<slug>.md
  - STORIES/STORY-NNNN-<slug>.md
  - proposed/CANON-NNNN.md: canon bodies awaiting approval
  - canon/CANON-NNNN.md: approved canon bodies, immutable after approval (R8)
- missions/<release-X.Y.Z | feature-slug>/
  - SPEC.md (SuperPRD), mission.yaml, PROGRESS.md, NOTES.md
  - slices/<NN-slug>/: SPEC.md (MiniPRD), slice.yaml, PROGRESS.md, PROOF.md, proof/<NN>-<type>.md
  - closed/<NN-slug>/: rig's close convention; superseded slices live here
- spec/ (D4 workbench)
  - active/: Draft_PRD.md, RedTeam_Report.md, FAILED_WORKFLOWS.md
  - archive/: written only by archive_specs.py
  - process/: commit-blocked
  - compiled/architecture.yml: hacf.ledger/v2. Legacy flat MiniPRD_*.md files are still read until v0.7.0.
- tests/{candidate_outputs,fixtures,integration}/: created by the scaffold with .gitkeep; never copied.
  tests/candidate_outputs/<SID>/ holds audit `novel_fixture_needed` outputs (gitignored, agent-blocked)
  until the user promotes them into a fixtures dir (R27).
- .agents/
  - skills, scripts, schemas (incl. openrig/*.md and openrig/NOTICE), rules, install-templates, config (code_graph.defaults.yml, orchestration.yml, upstream.yml, legacy_file_hashes.json): framework-owned, per-file manifest sync. Every script tolerates missing config files: embedded defaults plus the notice "run HACF-install.sh to complete the upgrade" (R24.10).
  - scripts/_runtime.py: shared guard imported by every new or modified entry point; checks Python >= 3.10 and PyYAML >= 5.4 with an actionable message and provides the one timestamp helper (writes `+00:00`, parses `Z` and `+00:00`) (R19.4)
  - local/hacf.yml: tracked, never shipped or overwritten. Holds team selections only: code_graph.backend and install_method, ides, openrig.id_prefix (default OPR), executor, orchestration overrides. No machine paths. It may only tighten the security keys listed in #orchestration-config, never loosen them (R4).
  - memory/activeContext.md
  - hacf-manifest.json: tracked; also records the installed upstream commit as `installed_commit` (R25.5)
  - state/ (own .gitignore '*'; any script that needs it creates it on first use, R22.1):
    - runs/<id>/{run.json, events.jsonl, claude-settings.json, ledger/, outbox/, base/}: run.json records the sha256 of the base ledger (R5) and any --unverified-executor use (R20.1); base/ is the read-only copy of AGENTS.md, skill text and hook scripts from the integration base (R4.2)
    - lore/pending/ and lore/tickets/ (R6)
    - codegraph/project.json: resolved exe and project name, machine-local
    - migrations/v0.6.0.json and migrations/<ts>/backup/
    - upstream-trust.json: machine-local pinned signer set, written on first trust; the payload's .agents/config/upstream.yml is only a proposal, and a change to the pinned set needs interactive consent showing the diff (R12)
  - .backup/ and logs/: gitignored by the managed block. logs/update-consent.jsonl records every unsigned-update consent (R25.6).
- Lock files are not under .agents/state/. They live in `$(git rev-parse --git-common-dir)/hacf/locks/`
  (ledger.lock, lore.lock, engine.lock, git.lock), shared by every worktree of the repo (R22.1).
- Engine outboxes live in the slice worktree at `.hacf-outbox/` and are moved to
  `.agents/state/runs/<id>/outbox/` after each phase (R20.3).
- .agentignore: firewall source
- .claude/commands/*.md
- .claude/settings.json: merged deny/ask rules plus the PreToolUse gate hook; never replaced. settings.local.json is never touched.
- MCP entries, merged and only when selected: .mcp.json (claude), .cursor/mcp.json (cursor), .roo/mcp.json (roo)
- Ignore-file blocks, written only for selected IDEs and labeled best-effort filters: .cursorignore, .rooignore, .codeiumignore (windsurf), .geminiignore (antigravity id)
- AGENTS.md, CLAUDE.md, GEMINI.md: HACF managed block; foreign (OpenRig) blocks preserved byte-for-byte. CLAUDE.local.md is never touched. A whole-file v0.5.9 instruction file with no HACF markers and an unknown sha is never appended to: interactive -> replace with the block and save `<file>.v059.bak`, or write `<STEM>.hacf-v0.6.0.md`; `-y` or non-TTY -> the sidecar file and a report line (R9).
- .gitignore block: .agents/state/, .agents/.backup/, .agents/logs/, /exhaust/, /.openrig/

Managed-block grammar (R26), used for every HACF block and generated region:
- Markers are exact and case-sensitive: `<!-- BEGIN HACF MANAGED BLOCK: <id> -->` and
  `<!-- END HACF MANAGED BLOCK: <id> -->` in Markdown; `# BEGIN HACF MANAGED BLOCK: <id>` and
  `# END HACF MANAGED BLOCK: <id>` in hash-comment files (.gitignore, .agentignore, info/attributes); the
  same rules for `HACF GENERATED: <id>` regions. `<id>` matches `^[a-z][a-z0-9-]{0,63}$`.
- No prefix or case-folded match, so `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` is never treated as HACF's.
- Markers count only at column 0 and outside fenced code blocks.
- The file's EOL style (LF/CRLF), UTF-8 BOM and final-newline state are preserved; bytes outside the block
  stay identical.
- Duplicate, nested or unbalanced HACF markers: exit 5, nothing written.
- A user-edited HACF block (sha differs from the manifest): backup plus ask when interactive; leave it and
  report under `-y`.
- Files HACF shares with rig or OpenRig are written compare-and-swap (#key-ownership).

HACF REPOSITORY differences:
- D14 (narrowed by R10): tracks lore/, missions/ (release-0.6.0 is OPR.0.6.0; also hyper-update, dynamic-workflow-parallel-execution-engine (R45: the H1 rule) and legacy-unassigned), root SPEC.md, project.yaml, workspace.yaml and lore_hypergraph.yml. It tracks no root .gitattributes; its merge-driver attributes live in .git/info/attributes like a user project's.
- .gitignore ignores /spec/ and /tests/ wholesale after S15b, so `git ls-files spec tests` is empty. The ledger is untracked again on main, but its per-slice history lives in swimlane commits.
- dev/: tests (testpaths), scripts (check.sh, v060_gate.py), design/v0.6.0. Never shipped. Live-run
  captures (argv, stdout, stderr, exit code, tool version, UTC date), written by the test harness and never
  by the model, go to dev/tests/fixtures/recorded/<SID>/; synthetic fixtures used when consent is declined go
  to dev/tests/fixtures/synthetic/<SID>/ with a `synthetic: true` header (R27).
- PRDs/v0.6.0/: the reference pack.
- Root AGENTS.md, CLAUDE.md and GEMINI.md are dev versions; the user versions are in .agents/install-templates/.
- .agent symlink kept and not shipped. .provenance_staging/ removed. .agents/hacf-manifest.json gitignored.

NEVER_COPY. Enforced by HACF-install.sh, hyper_update_core.py and HACF-uninstall.sh (which refuses to delete them), and tested by dev/tests/test_never_copy.py:
- SPEC.md, project.yaml, workspace.yaml, lore_hypergraph.yml, .gitattributes
- lore/, missions/, exhaust/, .openrig/
- the contents of spec/ and tests/
- dev/, PRDs/, docs/
- .agents/local/, .agents/state/, .agents/.backup/, .agents/logs/
- .agents/memory/, except creating activeContext.md when it is missing
- merge-only files: .claude/settings*.json, .mcp.json, .cursor/mcp.json, .roo/mcp.json, .gitignore
- CHANGELOG.md, README.md, CITATION.cff, pyproject.toml, uv.lock, requirements.txt, .agent
- any HACF root file not on an explicit copy list

<a id="spec-frontmatter"></a>
## SPEC.md frontmatter (project, mission, slice)

Common rules:
- Frontmatter is parsed with yaml.safe_load.
- Rewrites splice only the owned keys and keep unknown keys and key order, the same way OpenRig's updateFrontmatter does (packages/cli/src/lib/scope/scope-fs.ts @v0.6.13).
- HACF keys use the hacf_ prefix, except node_id, parent and lore_refs.
- Which keys rig writes and which HACF writes, and the compare-and-swap rule for shared files, are in
  #key-ownership (R22.3).
- Dot-IDs follow dot-id.ts: DOT_ID_RE ^([A-Z]{2,3})\.(\d+(?:\.\d+){1,4})$. release-X.Y[.Z] maps to <PFX>.X.Y[.Z]. Any other name maps to <PFX>.99.0.<n>.
- The default prefix is OPR, because rig hardcodes DEFAULT_PROJECT_PREFIX='OPR' with no config.
- stage values: wip|provisional|established|canonical|superseded|retired.

1) Project SPEC.md (SystemOfRecord), written by `hyper_scope.py project init|update` or by the migration:
---
intent: '<one sentence>'
hacf_schema: 1
hacf_type: SystemOfRecord
hacf_updated: <date>
---
Body:
# Project - <name>
## Intent
## 1-8: SuperPRD sections, plus the baseline headings
## Product Context
A generated missions index between '<!-- BEGIN HACF GENERATED: missions-index -->' and its END marker.

2) Mission SPEC.md (SuperPRD):
---
id: OPR.0.6.0
mission: release-0.6.0
stage: wip
verified: '<date> against /hyper-resolve compile'
created: <date>
intent: '<one sentence>'
depends_on: []
hacf_schema: 1
hacf_type: SuperPRD
hacf_status: compiled        # draft|compiled|in_progress|complete|superseded
node_id: <optional>
lore_refs: [ADR-0001]
hacf_source: {draft: <archive path>, redteam: <archive path>}
---
Body:
# SuperPRD: <Feature>
## Intent
## 1-8
## Slices: a generated table
## Status

3) Slice SPEC.md (MiniPRD):
---
id: OPR.0.6.0.4
slice: 04-openrig-adapter
mission: release-0.6.0
status: active
stage: wip
verified: '...'
created: <date>
intent: '...'
depends_on: [OPR.0.6.0.2]   # slice.yaml execution.depends_on wins if present
hacf_schema: 1
hacf_type: MiniPRD
hacf_status: compiled        # compiled|executing|executed|audit_failed|audited|blocked|superseded
node_id: openrig_adapter
parent: orchestration_layer
hacf_touches: ['.agents/scripts/hyper_openrig.py', 'dev/tests/openrig/**']
hacf_oracle: [['uv', 'run', 'pytest', 'dev/tests/openrig', '-q']]
lore_refs: [ADR-0001]
hacf_legacy_source: null
hacf_legacy_order: null
---
Body, in order:
# MiniPRD: <Module>
## Intent
## 1. The Confidence Mandate
## Mini-requirements: numbered US-NNN
## 3. Implementation Plan (Task List): each task under 10 minutes
## 4. The Negative Space (Constraints)
## Proof contract: checkbox tests
## Source material
## Intent visual
## Status
## Dependencies
Legacy headings (## 2. Atomic User Stories, ## 5. Integration Tests & Verification) are accepted only in legacy files.
Writer (R37, as S03b shipped it): `hyper_scope.py slice create --mission <dir> --name <NN-slug> --intent '<one sentence>' [--node-id ID] [--parent ID] [--depends-on a,b] [--touches g1,g2] [--oracle-json F] [--body-file F] [--legacy-source PATH] [--legacy-order N]`. `--intent` is required (one non-empty line without control characters) and fills `intent`; `--legacy-source` (the repo-relative legacy path) and `--legacy-order` (an integer) fill `hacf_legacy_source` and `hacf_legacy_order`, which stay null without them. Callers: /hyper-resolve's compile (S07a) passes each MiniPRD's one-sentence intent; the migration (S13a) passes the legacy H1 rule of #migration step 5 and both legacy flags.

Lint section rules (R29; `hyper_spec.py lint`, exact string comparison, outside fenced code):
- Slice SPEC.md: the H1 starts with `# MiniPRD: `, and each of the ten `##` headings above is present as its exact text before the first `:` of its line here (`## Mini-requirements`, `## Proof contract`, and `## 3. Implementation Plan (Task List)` included). A missing heading is an error; headings out of the listed order are a warning.
- Rig's two content rules, from S02's recorded audits (dev/tests/fixtures/recorded/S02/08-scope-audit-after-proof.json, 15-scope-audit-final.json, 18-nested-scope-audit.json): `## Mini-requirements` must hold at least one numbered item (`1. `), else `mini_requirements_missing_or_malformed`; `## Proof contract` must hold at least one `- [ ] ` item, else `proof_contract_missing_or_malformed`. Both are errors in v6 slice files; rig itself rates them low.
- Mission SPEC.md: `## Intent`, `## Slices` and `## Status` must be present. Project SPEC.md: `## Intent` and `## Product Context`. The `## 1-8` lines are placeholders for the SuperPRD sections and are not checked.
- Migrated slices (a v6 slice SPEC.md whose `hacf_legacy_source` is not null): a missing heading is still an error, but the two content rules and the heading order are warnings, because the migration keeps the legacy section text verbatim. The migration adds every missing heading (S13a heading conversion).
- Legacy flat files: the alias table applies, every deviation is a warning, and the two content rules are skipped.

hacf_status to OpenRig mapping (hyper_openrig.py; rig's active filter is in scope-fs.ts):
- compiled, executing, executed, audit_failed, blocked -> status 'active'
- audited -> 'done', plus PROOF.md and a C1 artifact. scope-audit raises missing_proof when the status contains done/ship/close/proven/promoted and the proof is absent.
- superseded -> 'closed-superseded', plus `closed-on`. The slice dir moves to <mission>/closed/<NN-slug> and its composition entry is removed, the same effect as `rig scope slice close --reason superseded`. The command refuses while live slices depend on it unless --rewrite-dependents-to <id> is given.
slice.yaml has no status field, so HACF never writes status there.
mission.yaml metadata.status: planned at compile; active at the first execution; complete for historical missions. 'complete' is one of scope-audit's terminal tokens. The bytes come from hyper_openrig.py's mission status functions (#openrig-manifests, R29).

<a id="progress-proof-notes"></a>
## PROGRESS.md, PROOF.md, NOTES.md

Templates are derived from the OpenRig v0.6.13 scope templates (slice-progress, mission-progress, proof, notes). They live in .agents/schemas/openrig/*.md and are attributed in .agents/schemas/openrig/NOTICE (Apache-2.0, source URL, tag v0.6.13). Only scripts write these files after creation.

Mission PROGRESS.md:
# Progress - <mission>
## Acceptance
- [ ] Scope complete
- [ ] Implementation in progress
- [ ] QA / review pass
- [ ] Merge / ship
A generated slice table (id | slice | hacf_status | depends_on | last event).
Writer: `hyper_spec.py progress`. In engine runs only the integrator writes it.

Slice PROGRESS.md:
# Progress - <NN-slug>
## Current state: generated
## Outcomes: append-only lines, e.g. '<ts> executed - commit <sha>, executor claude-cli (session, turns, cost)'
Writer: `hyper_spec.py progress --slice <dir> --event compiled|executing|executed|audit_failed|audited|blocked [--detail-file f]`.
Legacy flat MiniPRDs: progress and proof are skipped with a printed notice, because there is no slice dir.

PROOF.md:
# PROOF - <id> <title>
## What this proves: each Proof contract item mapped to an artifact
## Artifacts
## Residue / caveats
C1 artifacts are proof/<NN>-<type>.md with frontmatter {slice, candidate_sha, artifact_type: guard|qa|rev1-r1|rev1-r2|adjudication, verdict: CLEAR|BLOCKING|CONCERNING|PASS|NOT-CLEAR, money_evidence}, plus the optional HACF key `swimlane_sha` (R22.8). The closed sets come from proof.ts and scope-audit.ts @v0.6.13.
- `swimlane_sha` (optional): the commit the proof was made against on a build branch that may never reach the published branch. S15b sets it, equal to `candidate_sha`, for every slice it migrates (R22.8).
- Proof checks (`hyper_spec.py resolve`, `status`, `lint`) treat an unreachable `candidate_sha` as historical, a warning, when `swimlane_sha` is present; without it, an unreachable `candidate_sha` is an error. `legacy-unrecorded` stays valid.
- Timestamps in PROGRESS lines, proof frontmatter and run files are written with `+00:00` and parsed through `_runtime.py`, which also accepts `Z` (R19.4).

Writer: `hyper_spec.py proof --slice <dir> --artifact-type qa --verdict PASS --candidate-sha <sha|auto> --money-evidence '<line>' --file <f> --name <NN>-qa.md`.
- With `auto`: HEAD if the slice files are clean. Otherwise a temp-index snapshot (`GIT_INDEX_FILE=<tmp> git read-tree HEAD; GIT_INDEX_FILE=<tmp> git add -A; git write-tree; git commit-tree -p HEAD`) stored at refs/hacf/candidates/<slice-id>. Seeding the temp index from HEAD keeps force-tracked files under ignored directories in the tree (R22.2; tested with a force-tracked file under an ignored dir). The user's index and working tree are untouched.
- Engine runs pass the engine's slice commit; the engine, never the sandboxed audit phase, writes PROOF and candidate refs (R1).
- When rig 0.6.13 is present the writer delegates to `rig proof --workspace <root> add <slice-path> --artifact-type --verdict --candidate-sha --money-evidence --file --name --json`. --workspace is an option of the parent command, and `add` uses no daemon (proof.ts). Otherwise HACF writes the identical C1 header itself.

Mission NOTES.md:
---
mission, name, created
---
# Notes - <name>
## Notes: append-only lines '<date> [field-note] (agent:<skill>, slice <id>) <text> anchors: ...'
Writer: `hyper_lore.py note --mission <dir>` (fcntl lock under `<git-common-dir>/hacf/locks/`; outbox only, no lock, when HACF_OUTBOX is set; compare-and-swap write per #key-ownership).
Legacy mode: `hyper_lore.py note --legacy --source <MiniPRD path>` appends to '## Field Notes (legacy)' in .agents/memory/activeContext.md. S13b moves these into the mapped mission NOTES.md. Notes never go to spec/process.

spec/active/FAILED_WORKFLOWS.md stays the human-readable failure log. It is rotated via `archive_specs.py --paths`. A full `archive_specs.py <feature>` run still moves it with the other active files; for v0.6.0 the compile self-check restores it right after `archive_specs.py v0_6_0` with `git restore --source=HEAD --worktree -- spec/active/FAILED_WORKFLOWS.md` (operating-procedure.md#compile-instructions, R18.11).

<a id="openrig-manifests"></a>
## OpenRig manifests (pinned 0.6.13)

hyper_openrig.py is the only writer. Source: tag v0.6.13 (commit 9eeb2e42). The keys are transcribed into dev/tests/fixtures/openrig-0.6.13/ and contract-tested.

project.yaml (default-workspace-scaffold.ts):
schema: openrig.project/v0alpha1
kind: project
install: {intent: SPEC.md, context: [], skills: []}
missions: {root: missions}

workspace.yaml:
schema: openrig.workspace/v0alpha1
projects: [{id: default, root: .}]

mission.yaml (MISSION_MANIFEST in scope.ts):
schema: openrig.mission/v0alpha1
kind: mission
metadata: {status: planned}
composition:
  mission_markdown: {spec: SPEC.md}
  slices: []   # entries {ref: slices/<dir>/slice.yaml, order: 10|20|..., active: true}
Rules mirror validateMissionComposition: safe relative ref, exists, no symlink, inside the mission, unique; order is an integer, unique and strictly increasing. `rig scope slice create` itself appends membership (appendMissionMembership), so HACF re-reads after any rig call.

Mission status (R29; added by S03a). `MISSION_STATUSES = ("planned", "active", "complete")`.
- `splice_mission_status(text, status) -> str` returns mission.yaml text with only the bytes of the `metadata.status` scalar replaced (through the `_mapping_item`/`_value_span` text helpers); comments, key order and every other byte are kept. A status outside MISSION_STATUSES is a usage error (exit 2). A file without a `metadata` mapping holding `status` is refused as a parse error (exit 4); the key is never inserted. Setting the current status returns the text unchanged.
- `set_mission_status(mission_dir, status) -> str` reads `<mission_dir>/mission.yaml`, applies the splice and writes it with the adapter's `cas_write` (sha256 at read, re-read before `os.replace`, exit 5 on a change), like `set_slice_status()`; it takes no lock and returns the status.
- Callers: `hyper_spec.py progress` (S03a) uses the splice under its own `spec.lock` compare-and-swap write (planned -> active on the first executing event). `hyper_migrate.py` (S13a) uses the writer: complete when every slice of the mission is audited, active when any slice is past compiled, else no write.

slice.yaml (SLICE_MANIFEST):
schema: openrig.slice/v0alpha1
kind: slice
composition:
  mission: ../../mission.yaml
  slice_markdown: {spec: SPEC.md, progress: PROGRESS.md, proof: PROOF.md}

HACF never writes:
- execution.depends_on (it reads it and lets it win)
- slice.yaml status
- `rig config set` or `rig config init-workspace`
Every rig call is an argv list such as `rig scope --workspace <repo> audit --mission <m> --json`. The --workspace help says it uses project.yaml missions.root, else missions/. Only `scope approve` contacts the daemon, and index.ts has no tmux or daemon startup preflight.
- Every rig call that creates or names an artifact passes `--name` explicitly, so rig never writes a default-named file such as `<type>-<verdict>-<ISO>.md` into a tracked mission dir (R22.4).

Rig version rule (R22.7, answers Q8). `use_rig_when_present` applies only when `rig --version` equals the pinned 0.6.13:
- another 0.6.x: HACF does not call rig; it writes the v0alpha1 manifests and C1 artifacts itself and warns;
- another major.minor: HACF stops calling rig entirely until the contract test passes on that version.

Registration with the user's running OpenRig (D1):
`hyper_openrig.py register-project --catalog <path to the user's OpenRig workspace.yaml> --id <slug> [--dry-run]`
- Appends {id, root: <absolute repo path>} to the catalog. workspace.yaml is 'the project-location catalog'; the daemon read API is GET /api/scopes/projects (project-workspace.md).
- Requires explicit consent, backs up the catalog first, and never runs automatically.
- Refuses an id that already exists in the catalog (exit 5). Stores the repo's realpath plus a fingerprint, the repo's root-commit sha (`git rev-list --max-parents=0 HEAD`), in HACF state so a moved or re-created repo is detected (R22.5).
- `hyper_openrig.py unregister --catalog <path> --id <slug>` removes the entry (consent, backup first).
- `hyper_openrig.py doctor --catalog <path>` reports a registered root that no longer exists or whose root-commit sha differs from the fingerprint.
- Whether a relative or absolute root is accepted is UNVERIFIED; the spike settles it.

Renumbering (R22.6). `hyper_lore.py renumber <old> <new>` (#lore-hypergraph, Merging) rewrites every in-repo reference to the old id (lore_refs in missions/**/SPEC.md and slice frontmatter, other nodes' relates_to and supersedes) and reports out-of-repo references (commit messages) without rewriting them. Whether two projects with the same OPR ids collide in the daemon's views is checked by the daemon-level spike.

S02 spike (gate), run via `npx -y --package=@openrig/cli@0.6.13 rig ...` with consent:
(a) CLI level, required before S03a:
    - scope audit, slice ls and slice create against a temp repo whose workspace root is the repo root
    - frontmatter round-trip
    - `proof add`
    - status classification
    - after each rig call, `git status --porcelain` lists only the expected paths (R22.4)
    - a case with the repo nested inside a rig workspace root such as ~/.openrig/workspace (R22.4)
(b) Daemon level, run in the user's OpenRig environment and required before S15b (or an explicit user waiver):
    - register-project
    - the project and its missions appear in GET /api/scopes/projects or the TUI PROJECTS view
    - two registered projects with the same OPR ids (R22.6)
The verdicts are recorded as spike_verdict_cli and spike_verdict_daemon in lore/STORIES/STORY-0002 (PROPOSED). The rig runs are verbatim captures written by the test harness to dev/tests/fixtures/recorded/S02/; verdicts are computed from the captures, never written by the model (R27). STORY-0002 also records `npm view @openrig/cli@0.6.13 dist.integrity` (R24.8).

Still UNVERIFIED: repo-root acceptance at runtime; install.intent override; whether HACF's <mission-id>.<k> matches rig's inferred slice numbering; the root path semantics of the catalog; the exact mission status field that scope-audit reads.

<a id="key-ownership"></a>
## Key ownership (HACF vs OpenRig)

HACF and rig (or an OpenRig seat) can write the same files in one checkout, and HACF's locks are invisible to rig. Two rules apply (R22.3):

1. Each key has an owner. A writer rewrites only the keys it owns, plus the shared keys below, and keeps every other key and the key order (the splice rule in #spec-frontmatter).
2. Every HACF write to a shared file is compare-and-swap: record the sha256 of the bytes read, re-read the file just before `os.replace`, and exit 5 with nothing written when the sha differs. The caller re-reads and retries or reports.

| File | HACF-owned keys | rig-owned keys | Shared keys (both write; compare-and-swap) |
|---|---|---|---|
| Project SPEC.md frontmatter | `hacf_schema`, `hacf_type`, `hacf_updated`; the HACF GENERATED missions-index region | none known | `intent` |
| Mission SPEC.md frontmatter | every `hacf_*` key, `node_id`, `lore_refs` | keys rig adds on its own scope commands | `id`, `mission`, `stage`, `verified`, `created`, `intent`, `depends_on` (HACF writes them only when it creates the mission) |
| Slice SPEC.md frontmatter | every `hacf_*` key, `node_id`, `parent`, `lore_refs` | keys rig adds on `rig scope slice progress` and other scope commands | `status` (HACF writes only the #spec-frontmatter mapping), `stage`, `verified`, `id`, `slice`, `mission`, `created`, `intent`, `depends_on` |
| Slice and mission PROGRESS.md | the generated "Current state" region and the slice table | none known | the append-only Outcomes list (append only, never rewrite a line) |
| mission.yaml | `metadata.status` | `composition.slices` membership appended by `rig scope slice create` | none; HACF re-reads after any rig call |
| slice.yaml | none: HACF never writes `status` or `execution.depends_on` | everything | none |
| AGENTS.md, CLAUDE.md | the HACF managed block only (#target-layout grammar) | the OpenRig managed block only | none; bytes outside each block are untouched |

The rig-owned column is what OpenRig 0.6.13 is known to write (scope-fs.ts, scope.ts); the S02 frontmatter round-trip (Q7) records rig's actual write set in dev/tests/fixtures/recorded/S02/, and a key it writes that this table lists as HACF-owned is a spike finding.

<a id="lore-hypergraph"></a>
## lore_hypergraph.yml

Location: <repo>/lore_hypergraph.yml, tracked. Single writer: hyper_lore.py.
Write protocol:
1. fcntl lock on `$(git rev-parse --git-common-dir)/hacf/locks/lore.lock`, taken before the data file is opened (R22.1). With HACF_OUTBOX set, no lock is taken and the change goes to the outbox only.
2. read and mutate
3. validate
4. temp file, fsync, os.replace, fsync the directory; the file mode is kept

schema: hacf.lore/v1
id_counters: {ADR: 11, STORY: 1, CANON: 0}
nodes:
  - id: ADR-0003                 # ADR-NNNN | STORY-NNNN | CANON-NNNN, immutable
    type: decision               # decision | story | canon
    title: '...'
    status: PROPOSED             # PROPOSED | CANON | REJECTED | SUPERSEDED | RETIRED
    origin: user-decision        # user-decision | design-choice | agent | import
    context: null
    pointer: {path: lore/ADRs/ADR-0003-...md}   # canon: {path: lore/proposed/CANON-0002.md} until approved, then {path: lore/canon/CANON-0002.md} (R8)
    content_sha256: '<sha>'
    anchors: {components: [..], files: [globs], symbols: []}
    planned: {components: [], files: []}   # moved into anchors by resolve-anchors once they exist
    relates_to: []
    supersedes: []
    superseded_by: null
    stale: false
    stale_reason: null
    legacy: null                 # {source: docs/adr/0004-x.md, status: accepted}
    provenance: {created_by, created_at, source, session_id, run_id}
    approval: null               # {approved_by (from git config; attribution, not identity: R23.6), approved_at, warrant, warrant_sha256, method: 'AskUserQuestion+PreToolUse-ask+ticket' | 'tty-confirm', content_sha256, nonce}
    history: [{at, from, to, by, warrant, ref}]   # append-only

Edges: anchors (the decision-to-{components, files, symbols} hyperedge), relates_to, supersedes/superseded_by. `graph --json` derives the edge list.

Transitions. Gated means: through approve, reject, retire, reopen or review only; right after a per-node AskUserQuestion; warrant required; refused (exit 4) under HACF_AUTONOMOUS=1, or when the invocation has neither a matching hook ticket nor a TTY confirmation (R6, R7). Claude Code's permission mode is not passed to hyper_lore.py; the PreToolUse hook enforces it and issues the ticket only in default, acceptEdits and auto mode.
- PROPOSED -> CANON: gated; the body moves from lore/proposed/<ID>.md to lore/canon/<ID>.md (R8)
- PROPOSED -> REJECTED: gated; node and file kept
- CANON -> SUPERSEDED: automatic when a node naming it in supersedes is approved; status only, the canon file is not touched
- CANON -> RETIRED: gated; status only, the canon file is not touched
- REJECTED -> PROPOSED: gated reopen
- stale is set by impact intersection, a sha mismatch or an anchor to a removed component or deleted file; it is cleared only by a gated `review --still-valid` or by supersession
Agents may propose, add notes, edit anchors of PROPOSED nodes, and mark stale.

Binding force (R23.1, R23.4):
- CANON nodes are constraints for every agent. A stale CANON node stays binding until it is retired or superseded; stale marks a node for review, it does not suspend it.
- PROPOSED nodes with `origin: user-decision` bind agents as constraints until they are rejected. Other PROPOSED nodes are context, not constraints.

Pointers:
- One file per ADR or STORY; the path is the pointer, with no slug-to-heading conversion.
- Approved canon bodies live one per file in lore/canon/CANON-NNNN.md and are immutable after approval (R8). lore/CANON.md is a pure concatenation, in id order, of the canon files of nodes whose status is CANON, each between '<!-- lore:CANON-0002 -->' and '<!-- /lore:CANON-0002 -->'; `index` can always rebuild it from lore_hypergraph.yml and lore/canon/.
- Validation: the path exists under lore/ and is not a symlink; markers are unique and balanced; the sha matches. A CANON sha mismatch sets stale with reason content_changed_since_approval. A CANON node without its lore/canon/<ID>.md file is an error (R8).

Anchors (R21.4, answers Q41):
- An anchor to a component id that is tombstoned in the ledger's `removed_nodes`, or a files glob that matches only deleted paths, is a stale warning: validate exits 0 when only warnings remain, and a CANON node is marked stale, which routes it to the gated stale review.
- An anchor to a component id that is neither in the ledger nor tombstoned is an error.
- Agents may also update the anchors of PROPOSED nodes when a slice removes what they point at.

Approval binding:
1. `approval-request <ID>` runs BEFORE the question. It prints the exact body that was hashed and writes {id, content_sha256, nonce, expires_at = now + 30m} to .agents/state/lore/pending/<ID>.json. Only one request may be pending. Its output, and the AskUserQuestion body built from it, list every id the approval will supersede, retire or re-anchor (R23.3).
2. `approval-cancel <ID>` handles Keep, Skip and abandoned requests; expired requests are auto-cleared.
3. The gated command takes --nonce and --warrant-file. The file is written with the Write tool, so the user's text is never placed in a shell string. The warrant must be non-empty and differ from the title.
4. Ticket (R6). When the PreToolUse hook matches a gated verb in an allowed mode, it writes a single-use ticket to .agents/state/lore/tickets/ bound to the verb, the node id and the nonce, with a TTL of 120 s. `hyper_lore.py` refuses a gated verb without a matching unexpired ticket and deletes the ticket when it uses it.
5. Ticketless fallback (tools without a PreToolUse hook): a gated verb without a ticket proceeds only when stdin and stdout are a TTY and the user types the node id at a confirmation prompt (`method: tty-confirm`); otherwise exit 4.
6. approved_by comes from git config. It is attribution, not identity: an agent can change git config (R23.6).
7. `/hyper-lore-approve` ends by recommending a lore-only commit; it never commits (R23.5).

What validate proves (R23.5): `validate` checks format and internal consistency (nonce present, sha consistent), not provenance; an approval record forged by a Python write looks valid. Git history and review are the provenance. `hyper_lore.py list --status CANON --with-commit` shows, for each CANON node, the commit that introduced its CANON status, so reviewers can check that the transition landed in a commit touching only lore paths.

Generated MAP.md: its graveyard region lists the newest 20 REJECTED nodes, followed by the line "full list: `hyper_lore.py list --status REJECTED`" (R20.6).

Merging:
- The driver attributes are written to `$(git rev-parse --git-common-dir)/info/attributes` (managed block `merge-drivers`), never to a tracked .gitattributes (R10).
- lore_hypergraph.yml uses `merge=hacf-lore`: a by-id merge with union of history. A status conflict or id collision exits 1 with a report; `renumber <old> <new>` resolves collisions and rewrites in-repo references (#openrig-manifests, R22.6).
- lore/MAP.md and lore/CANON.md use `merge=hacf-generated`: a 3-way `git merge-file` of the hand-written parts of MAP.md; the generated regions, and all of CANON.md, are regenerated by running `index` on the merged lore_hypergraph.yml and lore/canon/ rather than keeping either side (R8). If the merged graph is not yet available to the driver, it touches .agents/state/lore/needs-index; `validate` fails on a stale region and `index` regenerates it. Canon bodies cannot be lost in a merge because lore/canon/<ID>.md files are ordinary tracked files.
- Without the repo-local driver config, git produces a text conflict, which validate catches.
- Branch merges are not gated: a CANON node added on a branch by a direct write merges cleanly. docs/Lore.md recommends CODEOWNERS on lore_hypergraph.yml, lore/canon/** and lore/CANON.md (R23.7).

<a id="code-graph-config"></a>
## Code-graph configuration and result schema

Three layers:
(1) .agents/config/code_graph.defaults.yml: shipped, replaced on upgrade, also embedded in hyper_codegraph.py.
(2) .agents/local/hacf.yml code_graph {backend: none|codebase-memory-mcp, install_method: path|uv-tool|uvx, base_branch: main}: tracked, never shipped.
(3) .agents/state/codegraph/project.json {exe_argv_prefix, project_name, version, last_index}: machine-local, because project names are path-derived and must come from list_projects.

defaults:
schema: hacf.codegraph-defaults/v1
backends:
  codebase-memory-mcp:
    pinned_version: '0.12.1'
    exe_resolution:
      - local override
      - shutil.which('codebase-memory-mcp')
      - ['uvx', 'codebase-memory-mcp@0.12.1'], only with recorded consent
    install_options:   # consent required
      uv_tool: ['uv', 'tool', 'install', 'codebase-memory-mcp==0.12.1']   # PyPI shim on PATH; downloads and verifies the native runtime on first run
      uvx: ['uvx', 'codebase-memory-mcp@0.12.1']
      vendor_script: {url: 'https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/v0.12.1/install.sh', args: ['--skip-config'], env: {CBM_DOWNLOAD_URL: 'https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.12.1'}}   # tag-pinned; SHA-256 always verified
    mcp_server: {type: stdio, command: uvx, args: ['codebase-memory-mcp@0.12.1']}   # or the bare command name when installed via uv tool
    capabilities:   # argv after the exe prefix; each element is a literal or exactly one {placeholder}
      index:         ['cli', 'index_repository', '--repo-path', '{repo_root}']   # no --format (R31); prints one JSON object
      status:        ['cli', 'index_status', '--project', '{project}', '--format', 'json']
      list_projects: ['cli', 'list_projects', '--limit', '1000', '--format', 'json']   # paging literal (R33)
      diff_impact:   ['cli', 'detect_changes', '--project', '{project}', '--base-branch', '{base}', '--changed-limit', '1000', '--limit', '1000', '--module-limit', '1000', '--max-output-tokens', '100000', '--format', 'json']   # paging literals (R33)
      symbol_impact: ['cli', 'trace_path', '--project', '{project}', '--function-name', '{symbol}', '--direction', 'inbound', '--format', 'json']
      search:        ['cli', 'search_graph', '--project', '{project}', '--name-pattern', '{pattern}', '--format', 'json']
      delete:        ['cli', 'delete_project', '--project', '{project}']   # no --format (R31); prints one JSON object
    timeouts_s: {index: 900, query: 60}
  none: {capabilities: {}}

Verified in README v0.12.1: the hyphenated flags --repo-path, --name-pattern, --function-name and --direction; `--format json` for the payload versus outer `--json` for the envelope; the project value is the 'name' returned by list_projects; indexes live in ~/.cache/codebase-memory-mcp (CBM_CACHE_DIR); install.sh @v0.12.1 accepts --skip-config, --dir and --clients and honors an HTTPS CBM_DOWNLOAD_URL with mandatory SHA-256.
Seen in S04's first contract run (2026-10-27 UTC; its captures are set aside with the partial work and the re-run records them again, R27; stop report in MiniPRD_07's Operating Notes): `cli index_repository --help` and `cli delete_project --help` list no `--format`, and `index_repository ... --format json` exits 1 with "unknown flag --format for this tool", so those two templates drop it (R31); the help of list_projects, detect_changes, search_graph and trace_path lists the paging flags used above, with defaults list_projects `--limit 50`, detect_changes `--changed-limit 20`, `--limit 200`, `--module-limit 20`, search_graph `--limit 50`, trace_path `--limit 100`, and `--max-output-tokens 3200` (R33); trace_path JSON names no file (R32).
UNVERIFIED: --base-branch; whether --base-branch accepts a full commit id (R34); an index name override; exit codes; detect_changes, list_projects and trace_path payload fields; the shim's cache location; whether the shim pins the native runtime's hash. The S04 contract test (consented) runs `cli <tool> --help` and records real outputs as verbatim captures in dev/tests/fixtures/recorded/S04/, and records how the shim verifies its native runtime as a finding (R24.8, R27). If the user declines it, parser fixtures are synthetic (dev/tests/fixtures/synthetic/S04/, `synthetic: true`), the backend default stays `none`, and the install prompt labels the backend "unverified" (R20.1).

Normalized result from `hyper_codegraph.py <cap> --json`:
{schema: hacf.codegraph-result/v1, backend, backend_version, capability, status: ok|unsupported|unavailable|stale_index|error, project, inputs, changed_files, impacted: [{symbol, file, line, kind, risk, distance}], impacted_files, components, warnings, error, raw_ref, elapsed_ms}
On unsupported or unavailable, callers fall back to git diff plus files: globs and say so.
Result rules:
- symbol_impact (R32): trace_path rows name no file, so each `impacted` entry has `file`, `line`, `kind` and `risk` null, `symbol` the qualified name built from its group's `qn_prefix` and the row's `name` (joined as the recorded capture shows, never guessed) and `distance` the row's `hop`. `impacted_files` and `components` are empty, `warnings` holds 'symbol_impact: trace_path reports no file paths; components come from diff_impact', and the status stays ok. There is no second lookup.
- Truncation (R33): when a payload has `has_more`, any `*_has_more` key or `truncated` set to true, diff_impact and list_projects return status error with error 'truncated: <the keys>' (callers fall back to git diff plus files: globs and say so; `index` records no project from a truncated list), and search and symbol_impact return status ok with the warning 'truncated: <the keys>'.
- Base (R34): `hypergraph_updater.py --from-diff <rev>` with a single revision passes the full commit id from `git rev-parse --verify <rev>^{commit}` as {base}; a full commit id passes the base rule below (`HEAD` and `HEAD~1` do not). For an ancestor of HEAD the merge-base is that commit, so detect_changes compares the working tree with the revision --from-diff compares. A rev-range (`A..B`, `A...B`) skips the code graph and prints 'code graph unsupported for a revision range: blast radius from git diff plus files: globs', because detect_changes always compares the working tree. If S04's contract test records that 0.12.1 refuses a commit id for --base-branch, the updater passes `.agents/local/hacf.yml` `code_graph.base_branch` (default `main`) instead, the result carries the warning 'base: <branch> merge-base; results cover the whole branch diff', and S04's Live-test record says so; the user approved this fallback in advance, so it needs no Replan. A manual `hyper_codegraph.py diff_impact --base <name>` takes a branch name.
Engine runs do not use the code graph by default (orchestration.code_graph_in_engine: false). Each worktree would need its own index. When it is enabled, the engine indexes the integration worktree once per run and calls delete at cleanup.

Security: shell=False, timeouts, placeholder validation (R24.6):
- every placeholder value that starts with '-' is refused, so no value can be parsed as an option
- repo_root: an absolute existing dir
- project: ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
- symbol: an identifier plus .:$<>-, at most 256, not starting with '-'
- pattern: at most 256 chars, must compile, not starting with '-'
- base: must pass `git check-ref-format --branch` and not start with '-'

Uninstall: when .agents/state/codegraph/project.json names a project, the uninstaller offers `python3 .agents/scripts/hyper_codegraph.py delete` so the index under ~/.cache/codebase-memory-mcp is removed (R24.9). Teammate effect: tracked .mcp.json, .cursor/mcp.json and .roo/mcp.json entries make every teammate who accepts the server run `uvx codebase-memory-mcp@0.12.1`; the install report and docs say so (R24.7).

<a id="orchestration-config"></a>
## orchestration.yml

.agents/config/orchestration.yml. Shipped and embedded in hyper_engine.py; overrides go in .agents/local/hacf.yml orchestration, except for the security keys below.

Security keys (R4.3). These come only from the shipped defaults (the copy embedded in hyper_engine.py, not the on-disk .agents/config file) or from CLI flags. Tracked .agents/local/hacf.yml and .agents/config/** may only tighten them (a stricter sandbox, fewer executables, fewer passthrough variables, a lower budget, scope_policy strict); a loosening value is ignored with a warning:
- executors.*.sandbox and executors.*.verified
- executors.*.argv, executors.*.preflight, executors.*.prompts (R42): only the shipped value counts as tightening, so any tracked change is ignored; a custom command goes under a new executor name, which is experimental and needs --allow-unsandboxed
- oracle.sandbox, oracle.allowed_executables
- autonomy.env_passthrough, autonomy.env_passthrough_prefixes, autonomy.auth_passthrough
- autonomy.max_budget_usd_per_phase, autonomy.max_budget_usd_per_run
- autonomy.scope_policy
- autonomy.credential_scan, git.ref_guard (R42): only true
- protected_paths (R42): a tracked list may add entries, never remove one (the resolved list is the shipped list plus the additions)
executors.*.status stays shipped-only (S08). oracle.prepare is not a security key: it runs engine-side from the base revision's lockfile (R2), and non-Python repos need their own (R42). run.json records the resolved values in `security`; each --resume records its own in its `resumed[]` entry (R42).

schema: hacf.orchestration/v1
topology: {project_spec: SPEC.md, missions_root: missions, ledger: spec/compiled/architecture.yml, lore_graph: lore_hypergraph.yml, legacy_compiled_dir: spec/compiled}
openrig: {cli_package: '@openrig/cli', pinned_version: '0.6.13', id_prefix: OPR, use_rig_when_present: true}
models: {opus: claude-opus-5-5, sonnet: claude-sonnet-5-5, haiku: claude-haiku-5-5, fable: claude-fable-5-1}
phase_models: {execute: sonnet, audit: sonnet}
executors:
  claude-cli:
    preflight: [['claude', '--version'], ['claude', 'auth', 'status']]   # run with the scrubbed env
    min_version: '2.1.259'          # --permission-prompts
    max_tested_version: '2.1.305'   # newer warns: re-run the claude live test. S08 sets it to the `claude --version` its live test passed on (R20.7, answers Q18)
    verified: false                 # true only after the S08 live test passes; while false the engine refuses to run without the CLI flag --unverified-executor, recorded in run.json (R20.1)
    sandbox: required               # 'off' only with the engine CLI flag --allow-unsandboxed, never from config (R4.3)
    argv: ['claude', '-p', '{prompt}', '--output-format', 'json', '--model', '{model}', '--permission-mode', 'dontAsk', '--permission-prompts', 'none', '--setting-sources', 'user', '--settings', '{settings_file}', '--allowedTools', '{allowed_tools}', '--disallowedTools', '{disallowed_tools}', '--max-turns', '{max_turns}', '--max-budget-usd', '{max_budget_usd}', '--strict-mcp-config', '--mcp-config', '{mcp_config_file}', '--append-system-prompt-file', '{autonomy_prompt_file}']
    # --setting-sources is 'user' only: project .claude/settings*.json in the worktree are executor-editable and never loaded (R4.2). User-level hooks still run; docs say so.
    prompts:
      execute: 'Read {base_dir}/AGENTS.md, then read {base_dir}/.agents/skills/hyper-execute/SKILL.md and follow its instructions precisely for the slice spec {slice_spec}.'
      audit: same form for hyper-audit
      # {base_dir} = .agents/state/runs/<id>/base/: AGENTS.md and the skill text materialized from the integration base, read-only, so an executor's edits to them in the worktree never instruct the auditor (R4.2)
      # does not depend on slash expansion, which --bare skips
    settings_file_content: {sandbox: {enabled: true, allowUnsandboxedCommands: false, failIfUnavailable: true, network: {allowedDomains: []}, filesystem: {denyWrite: [protected paths, '<worktree>/.git', '<git-common-dir>']}}, permissions: {deny: [the protected-path Edit rules]}, hooks: {PreToolUse: [hyper_gate_hook.py by absolute path to the base revision's verified copy under {base_dir}]}}
    # R1: agent phases get no write access to git metadata. The shared .git (default-writable in linked worktrees) and the worktree's .git pointer file are denied; the engine performs every git write itself.
    result_parser: claude_json
  codex-cli: {status: experimental, argv: ['codex', 'exec', '--json', '--sandbox', 'workspace-write', '--output-last-message', '{last_message_file}', '{prompt}'], result_parser: codex_jsonl}
  gemini-cli: {status: experimental, argv: ['gemini', '-p', '{prompt}', '--output-format', 'json', '--approval-mode', 'auto_edit'], result_parser: gemini_json}   # flags UNVERIFIED
  # R20.2: sandbox: required applies to every executor. codex-cli and gemini-cli have no HACF-verified OS sandbox, so they run only with the CLI flag --allow-unsandboxed (never from tracked config) and print a warning.
  openrig-seat: {status: seam-only}
  stub: {status: test-only, argv: ['python3', 'dev/tests/fixtures/stub_executor.py', '--scenario', '{scenario}', '--prompt', '{prompt}']}
  # R41: the engine's preflight refuses a test-only executor unless the environment has HACF_TEST=1 (the R12 test switch), whatever --unverified-executor says.
tools:
  execute_allowed: [Read, Glob, Grep, Agent, 'Edit(./**)']
  audit_allowed: [Read, Glob, Grep, Agent]
  # sandboxed Bash is auto-allowed: autoAllowBashIfSandboxed defaults to true
  disallowed: [AskUserQuestion, WebFetch, WebSearch, 'Bash(git push *)', 'Bash(git checkout *)', 'Bash(git switch *)', 'Bash(git reset *)', 'Bash(git rebase *)', 'Bash(git merge *)', 'Bash(git commit *)', 'Bash(git update-ref *)', 'Bash(curl *)', 'Bash(wget *)']
  # defense in depth only: Bash rules are not a security boundary; the sandbox's git-metadata denies (R1) are the control
autonomy:
  env: {HACF_AUTONOMOUS: '1', HACF_RUN_ID, HACF_SLICE, HACF_OUTBOX: '<worktree>/.hacf-outbox/', UV_NO_CACHE: '1'}
  # R40: UV_NO_CACHE=1 because the agent sandbox makes ~/.cache/uv read-only and `uv run` opens its cache for writing (S08 live test: "failed to open file ~/.cache/uv/sdists-v9/.git: Read-only file system"); uv then uses a throwaway cache in the sandbox tmp. hyper_executors.scrub_env sets it with the other autonomy variables.
  # R20.3: after each phase the engine moves .hacf-outbox/ to .agents/state/runs/<id>/outbox/ before the scope check, exempts that path from scope, and refuses entry types outside the whitelist: ledger ops (ensure-node, set-edges, apply-review), lore propose, note and stale, spec progress. Approval entries are rejected.
  env_passthrough: [PATH, HOME, LANG, LC_ALL, TERM, TMPDIR, XDG_CONFIG_HOME, XDG_CACHE_HOME, SSL_CERT_FILE, SSL_CERT_DIR, NODE_EXTRA_CA_CERTS, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, http_proxy, https_proxy, no_proxy]
  env_passthrough_prefixes: [CLAUDE_CODE_]
  auth_passthrough:   # R20.4: only the variables of the auth method in use pass; the method comes from `claude auth status` (authMethod) and the CLAUDE_CODE_USE_* flags
    api_key: [ANTHROPIC_API_KEY]              # api_key and api_key_helper auth only
    bedrock: ['AWS_*']                        # only with CLAUDE_CODE_USE_BEDROCK=1
    vertex: ['GOOGLE_*', 'CLOUD_ML_*', 'VERTEX_*']   # only with CLAUDE_CODE_USE_VERTEX=1
    oauth: []                                 # claude.ai and oauth_token logins pass no extra variables
  credential_scan: true   # R20.4: before integration the slice diff is scanned for credential shapes (AWS access key ids, PEM private-key headers, 'sk-ant-', GitHub tokens); a hit is scope_violation
  confidence_threshold: 9
  on_gate: stop_and_record
  max_parallel: 3
  max_turns: 80
  max_budget_usd_per_phase: 5.0
  max_budget_usd_per_run: 50.0   # R20.5: when reached, scheduling stops and the run records budget_exceeded
  phase_timeout_s: 3600
  kill_grace_s: 30
  retries: {transient: 2, oracle_or_audit_fail: 1, backoff_s: [30, 120]}
  # R20.5: rate_limited pauses every worker together (global backoff 30 s, then 120 s), not one slice at a time
  scope_policy: warn   # applies to ordinary files outside hacf_touches; the protected paths below are scope_violation under every policy (R4.1)
  ai_conflict_resolution: false
protected_paths:   # R4.1, R2: a change here not listed in the slice's hacf_touches is scope_violation, independent of scope_policy
  - spec/compiled/architecture.yml, lore_hypergraph.yml, lore/CANON.md, lore/canon/**, mission.yaml, slice.yaml, other slices' dirs
  - '.git*', '.claude/**', .mcp.json
  - .agents/skills/**, .agents/scripts/**, .agents/config/**, .agents/local/hacf.yml
  - AGENTS.md, CLAUDE.md, GEMINI.md
  - pyproject.toml, uv.lock, 'package*.json', and other lockfiles (pnpm-lock.yaml, yarn.lock, Cargo.lock, go.sum, poetry.lock)
oracle:
  default: [['uv', 'run', '--frozen', '--no-sync', 'pytest', '-q']]
  prepare: [['uv', 'sync', '--frozen']]   # engine-side, from the base revision's lockfile, before the phase
  allowed_executables: [uv, pytest, python3, npm, pnpm, make, cargo, go]   # hygiene, not isolation: every one of them runs repo-defined code
  sandbox: bwrap   # R2: the default on Linux; fail closed
# bwrap profile: read-only root; read-write worktree and tmp only; $HOME masked (tmpfs) except read-only binds the oracle needs; the worktree .git pointer and the git common dir read-only; --unshare-net; --die-with-parent.
# R40: the read-only binds are the real path of argv[0] as found on the scrubbed PATH (uv usually lives in ~/.local/bin) and, when the worktree has .venv/pyvenv.cfg, the parent of its `home` directory (the interpreter's whole install prefix, e.g. ~/.local/share/uv/python/cpython-3.12.12-linux-x86_64-gnu; binding the binary alone fails, because it cannot find its stdlib). No uv cache is bound: a read-only ~/.cache/uv makes `uv run --frozen --no-sync` exit 2 ("failed to open file ~/.cache/uv/sdists-v9/.git: Read-only file system"), checked with bwrap and uv 0.9.15 on 2026-11-10, where UV_NO_CACHE=1, UV_CACHE_DIR in tmp and .venv/bin/python -m pytest all passed.
# bwrap unavailable -> sandbox_unavailable (fatal). 'none' only with the engine CLI flag --allow-unsandboxed-oracle, never from config. macOS has no bwrap, so oracles there need that flag.
# The oracle env is scrubbed the same way as the executor env: PATH, LANG, LC_ALL, TMPDIR, the masked HOME, plus the auth_passthrough entry in use (R20.4), plus UV_NO_CACHE=1 (R40).
git:
  base_ref: HEAD
  integration_branch: 'swimlane/hacf/{run_id}/integration'
  slice_branch: 'swimlane/hacf/{run_id}/{slice_dir}'
  worktree_root: '~/.cache/hacf/worktrees/{repo_hash}'
  keep_failed_worktrees: true
  commit_trailers: [HACF-Run, HACF-Slice, HACF-Executor, HACF-Session]
  ref_guard: true
  # R1 metadata guard: before and after every agent phase, and after the engine's commit, oracle and audit steps, the engine records `git for-each-ref` plus a sha256 manifest of the worktree's .git pointer file, <common>/worktrees/**, <common>/info/*, <common>/objects/info/*, shallow, packed-refs and every HEAD, ORIG_HEAD and MERGE_HEAD file. Any difference is ref_violation: restore, block, keep the worktree. An objects/info/alternates file is refused outright. Every engine-side git command runs with explicit GIT_DIR and GIT_WORK_TREE.
  commit_hooks: base-in-bwrap
  # R3: engine commits run with `-c core.hooksPath=<empty dir>` and `--no-verify`. When the repo has pre-commit or commit-msg hooks (core.hooksPath or .git/hooks), the engine runs the base revision's copies inside the bwrap oracle profile before committing; a failing hook is commit_rejected.
code_graph_in_engine: false

Verified at https://code.claude.com/docs/en/cli-reference, /headless, /permissions and /sandboxing:
- -p, --output-format json, --permission-mode dontAsk, --permission-prompts none (v2.1.259+), --setting-sources, --settings (inline JSON or file), --allowedTools/--disallowedTools, --max-turns, --max-budget-usd, --strict-mcp-config, --mcp-config, --append-system-prompt-file
- `claude auth status` exits 0/1; authMethod is one of none|claude.ai|oauth_token|api_key|api_key_helper|third_party
- dontAsk denies AskUserQuestion
- the Agent tool name (permission rule `Agent(isolation:worktree)`)
- `--bare ... will become the default for -p in a future release`
- SIGTERM exits 143 and terminates the Bash process trees
- sandbox: sandbox.enabled, allowUnsandboxedCommands, failIfUnavailable; bubblewrap and socat on Linux; default writes are cwd plus tmp plus, in linked worktrees, the shared .git except hooks/ and config. HACF denies the shared .git and the worktree's .git pointer file (R1).
- --settings disabling the retry makes repository loosening ignored
UNVERIFIED, covered by the S08 live test:
- whether Edit(./**) also covers Write
- sandbox auto-allow under dontAsk
- uv inside the sandbox (cache writes). The S08 run (2026-11-06, claude 2.1.318) failed this check because ~/.cache/uv is read-only in the sandbox; R40 sets UV_NO_CACHE=1 for agent phases, which passes under plain bwrap but stays UNVERIFIED in Claude's sandbox until the live test is re-run, so claude-cli stays `verified: false` until then
- the denyWrite path syntax
- that a sandboxed Bash write to the worktree's .git pointer file and to the git common dir both fail (R1)
- whether Claude Code hooks run inside the OS sandbox or as ordinary processes with the user's privileges (RT-67a, R1)
The live-test runs are verbatim captures in dev/tests/fixtures/recorded/S08/ (R27). A declined live test leaves `verified: false`.

<a id="architecture-yml-changes"></a>
## Component ledger v2 (architecture.yml)

Ledger v2 stays at spec/compiled/architecture.yml. Its single writer is hyper_ledger.py. hypergraph_updater.py keeps its positional CLI and delegates to it.

schema: hacf.ledger/v2
nodes:
  - id: component_ledger            # ^[a-z][a-z0-9_]{1,63}$, unique
    dimension: Module               # System | Layer | Module | Atomic
    status: clean                   # clean | dirty | needs_review
    associated_file: .agents/scripts/hyper_ledger.py
    files: ['.agents/scripts/hyper_ledger.py']   # safe repo-relative globs; not seeded for Layer/System or directories
    description: >-                 # writer always emits folded block scalars
      ...
    inputs:  [{data_type: x, source_id: <node>} | {data_type: x, external: <label>}]
    outputs: [{data_type: x, target_id: <node>} | {data_type: x, external: <label>}]
    edges: {depends_on: [], implements: [], member_of: []}
    spec_refs: [OPR.0.6.0.2]
    provenance: {last_slice, last_commit, last_run_id, updated_at, updated_by}
    review: {reviewed_at, reviewed_by: 'audit:<slice>', summary}
removed_nodes:                      # R21.4: append-only tombstones written by remove-node
  - {id: hyper_fix, reason: '...', slice: S09b, commit: '<sha or null>'}

Tombstones. `removed_nodes` is a top-level list. An id in it may not be reused by ensure-node. The gate's and validate's non-regression checks treat a tombstoned id as a declared removal, not a dropped id. hyper_lore.py reads the list to tell an anchor to a removed component (stale warning) from a typo (error).

Mixed v1/v2 files (R21.3). Before `migrate-v2`, ensure-node may write v2-only fields (`files`, `member_of`, `provenance`, `removed_nodes`) into a v1 ledger. validate reports "mixed v1/v2 ledger" as a warning, not an error. The v0.5.9 updater (hypergraph_updater.py:68-88) ignores `member_of` and keeps unknown keys when it rewrites the file. migrate-v2 normalizes the file.

Endpoint model. The real ledger has 32/52 inputs and 49/61 outputs pointing at labels such as filesystem and user_interface (verified on the B5-repaired copy).
- v1 files: a non-node source_id/target_id is a warning.
- migrate-v2 converts each non-node endpoint into {external: <label>} with the label pattern ^[a-z][a-z0-9_]{0,63}$. An allowlist is not used because labels are project-specific.
- Label normalization (R30, as S01b shipped it in `external_label`): a value that fails the pattern gets `_` inserted before each capital that follows a lowercase letter or digit, is lowercased, and is kept when the result matches (`activeContext` -> `active_context`, `FILESYSTEM` -> `filesystem`, `HTTPServer` -> `httpserver`, `my_Label` -> `my_label`). Each rename is listed in the migrate-v2 summary; any other value still refuses (exit 5). A rename can equal a node id (`hyperUpdate` -> `hyper_update`), which the v2 rule below reports as a warning.
- v2 files: node-form endpoints must exist (error). An external label equal to a node id is a warning.

Validation: `hyper_ledger.py validate [--strict] [--json] [--against-ref REF | --against-file F]`. Checks:
- parse errors with line and column
- id pattern and uniqueness; enums
- edges values must be lists (null is an error, the B5 class)
- edge targets exist; no cycles in depends_on, implements or member_of
- implements points to a higher dimension
- safe globs; description is a string of at most 2000 chars
- endpoint rules above
--against reports dropped node ids and dropped per-node keys (used by the gate and by audits); ids in `removed_nodes` are not reported as dropped.
Writes are NON-REGRESSIVE. A write is refused (exit 5, file untouched) only if it adds errors that were not present before. Pre-existing defects are printed as warnings, so the mandatory updater step keeps working on imperfect user ledgers. --strict makes read-only validate fail on any finding, including every node left with status needs_review; those nodes are listed in a separate "leftover needs_review" section of the report (R21.5).

Commands (lock file `$(git rev-parse --git-common-dir)/hacf/locks/ledger.lock` taken before open (R22.1); validate; temp file, fsync, os.replace, fsync the directory; the file mode is kept). With HACF_OUTBOX set, writes go to the outbox and no lock is taken, except that `migrate-v2`, `repair-text` and `apply-outbox` exit 2 ("unset HACF_OUTBOX") and write no outbox entry; the `--dry-run` forms still run (R30). These three rewrite the whole ledger (repair-text's input may not parse), so they cannot be replayed from an outbox, and the engine's outbox whitelist (S09a2) does not list them. Exit codes: 0 ok, 1 findings, 2 usage, 3 lock timeout, 4 parse error, 5 refused.
- init: writes an empty `hacf.ledger/v2` ledger at the topology path; refuses (exit 5) when a ledger exists. /hyper-discover calls it on a fresh clone; the updater's "Hypergraph not found" message and engine preflight name it (R21.6, Q35).
- show, impact (--nodes | --files | --from-diff)
- ensure-node --id --dimension [--parent] [--member-of] [--depends-on a,b] [--files a,b] [--associated-file] [--description-file] [--spec-refs a,b] [--from-spec <legacy MiniPRD | v6 SPEC.md>]: parses **Hypergraph Node ID:**/**Parent Node:** or frontmatter node_id/parent/hacf_touches.
  List flags (R28): `--files` takes one comma-separated list of globs, like `--depends-on a,b` and `impact --files p1,p2`, and replaces the node's `files`. `--spec-refs` takes one comma-separated list of dot-IDs (DOT_ID_RE in #spec-frontmatter; any other item exits 2) and appends each id the node's `spec_refs` lacks, keeping order, so a repeat is a no-op. It is the only writer of `spec_refs`; migration step 6 uses it (subsystems.md#migration).
  `--parent X` semantics (R21.2, answers Q30): when X is a Layer or System node it writes `member_of: [X]` (not traversed by blast radius); when X is a Module and the new node is Atomic it writes `implements: [X]`; any other combination exits 2. Every node the v0.6.0 slices register is a Module with parent `orchestration_layer`, so each gets `member_of: [orchestration_layer]` and no blast-radius fan-out.
- set-edges --id [--add-depends-on] [--remove-depends-on] [--set-member-of] [--set-implements]: cycle-checked
- review-template [--status needs_review,dirty] --json
- apply-review --patch <json> --slice <id>: {node: {description?, inputs?, outputs?, files?, edges?}}; sets review and clean. Review patches written by sub-agents are applied only through this command (schema validation plus non-regression), never merged by hand (R27).
- mark --dirty|--needs-review|--clean --ids
- remove-node <id> --reason [--slice <SID>]: refuses while inbound edges exist and lists them; on success it appends {id, reason, slice, commit} to `removed_nodes` (commit: HEAD at removal time, or null) (R21.4)
- apply-outbox <dir> [--base-file <ledger>]: applies outbox entries in order with validation. With --base-file, an entry that touches a node whose content differs between the base file and the current ledger is a conflict: nothing is written, exit 5, and a per-node report is printed. `hyper_engine.py land --rebase-ledger` uses this form (R5).
- migrate-v2 [--dry-run] and repair-text [--dry-run] (S01b)

Frozen CLI (R18.12). From S01a on, the subcommands and flag spellings above are a contract: the ensure_node commands in every compiled MiniPRD's hacf-bootstrap block are copied verbatim from this section, and changing a subcommand or flag needs a replan R-row (operating-procedure.md#replan).

BFS semantics are unchanged from v0.5.9 (:58-80): implements parents and reverse depends_on. member_of is not traversed, so the 17 orchestration_layer membership edges stop fanning out.

hypergraph_updater.py:
- `<yaml> <ids...>`: same banner and exit codes; the blast-radius list is printed sorted.
- `--from-diff <rev-range> [--slice] [--apply] [--outbox] [--json]`: v1 falls back to associated_file matching. A post-impact hook calls hyper_lore.stale_from_impact when importable. HACF_OUTBOX routes writes.
- `--from-diff` (and `hyper_ledger.py impact --from-diff`) prints and returns `unmapped: [paths]`, the changed files that no node's `files` globs or `associated_file` cover; the JSON output has an `unmapped` key. /hyper-audit treats a non-empty list as a finding: register a node, or accept the gap in writing in the audit report (R21.1).
- When the ledger file is missing, the error names `python3 .agents/scripts/hyper_ledger.py init` and /hyper-discover (R21.6).
- The uncalled --write/--merge/--cleanup-provenance subcommands are deleted; grep finds no callers outside the file.

Enforcement:
- AGENTS.md:77
- Claude denies Edit(/spec/compiled/architecture.yml), which also covers sed/tee/redirect targets
- pre-commit runs validate when the ledger is staged
- semantic_graph_merger accepts only architecture.yml/.yaml basenames (fixes :42-56 and orchestrator :691-695)
- bootstrap audits use review-template plus apply-review instead of free-text rewrites (hyper-audit SKILL.md:31-35)

