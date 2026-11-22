# v0.6.0 Decisions

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

Decisions D1–D14 were made by the user on 2026-10-20 after reviewing verified evidence. They are binding for v0.6.0. Each is recorded as a PROPOSED ADR in `lore/ADRs/` until the user promotes it through the CANON gate.

<a id="d1"></a>
## D1 — OpenRig interop is required

**Decision.** The user runs (or will run) OpenRig, so real OpenRig interoperability has concrete value.

**Rejected alternatives**

- Inspiration only (compatibility not a goal)
- Not yet evaluated

<a id="d2"></a>
## D2 — Full OpenRig layout at the user's repo root

**Decision.** SPEC.md, project.yaml, workspace.yaml and missions/<m>/{SPEC.md, mission.yaml, PROGRESS.md, NOTES.md, slices/<s>/{SPEC.md, slice.yaml, PROGRESS.md, PROOF.md, proof/}} at the repo root; manifests always written; pinned to OpenRig 0.6.13 (v0alpha1). Mitigations: root-collision detection, one pinned manifest adapter + contract test, a spike gate before topology work.

**Rejected alternatives**

- OpenRig-shaped tree inside spec/ with an opt-in manifest adapter (was the recommendation)
- HACF-native hierarchy + export-only copy into ~/.openrig/workspace
- Keep the flat spec/compiled layout

<a id="d3"></a>
## D3 — SuperPRD/MiniPRD remain document types

**Decision.** Files are named SPEC.md (OpenRig-compatible); templates, headings and an hacf_type field keep SuperPRD (mission), MiniPRD (slice) and SystemOfRecord (project).

**Rejected alternatives**

- Retire the terms everywhere (~283 references)

<a id="d4"></a>
## D4 — spec/ stays HACF's workbench

**Decision.** spec/active (drafts), spec/archive (agent-blocked history), spec/process, spec/compiled/architecture.yml (component ledger). Compiled SuperPRD/MiniPRD content moves to missions/.

**Rejected alternatives**

- Migrate spec/ away entirely (drafts as stage: wip, archive under missions/<m>/archive/, ledger at repo root)

<a id="d5"></a>
## D5 — codebase-memory-mcp first, behind an executable adapter

**Decision.** The adapter runs `codebase-memory-mcp cli <tool>` with argv lists and JSON output, normalizes results, and returns explicit unsupported/unavailable. architecture.yml stays as the component ledger and is fed by the code graph (diff → files → components).

**Rejected alternatives**

- code-review-graph first
- Both adapters in v0.6.0
- No code graph yet
- The pack's universal facade returning prose instructions for an LLM to interpret
- Retiring architecture.yml
- Understand-Anything as a backend (no MCP/CLI; LLM-built graph)

<a id="d6"></a>
## D6 — The pack's lore/ tree, fixed

**Decision.** lore/{MAP.md, CANON.md, ADRs/, STORIES/} + lore_hypergraph.yml as the source of truth; docs/adr/ migrates into lore/ADRs/. Fixes: REJECTED kept, SUPERSEDED/RETIRED exits, stable validated pointers, locking, anchors, provenance, mission-level NOTES.md, safe merge strategy.

**Rejected alternatives**

- Build on docs/adr/ + CONTEXT.md with a generated index (was the recommendation)
- Defer lore to a later release

<a id="d7"></a>
## D7 — Conversational CANON gate with an audit trail

**Decision.** An agent promotes a node only right after the user's explicit 'Approve' in AskUserQuestion, one node at a time; approved_by, approved_at and a required warrant are recorded; CODEOWNERS is optional for teams. The guarantee is procedural, not a security boundary.

**Rejected alternatives**

- Human-run approve (TTY confirmation + pre-commit check)
- PR review / CODEOWNERS only

<a id="d8"></a>
## D8 — Fix the parallel engine in v0.6.0

**Decision.** One git worktree per slice; pluggable headless-CLI executor (`claude -p` default; codex and gemini argv templates; an OpenRig-seat executor as a seam); autonomous mode where gates stop-and-record; slice depends_on drives a DAG.

**Rejected alternatives**

- Park the engine for a later cycle (was the recommendation)
- Replace with host-native subagents
- Delegate orchestration to OpenRig
- Executor: Anthropic API tool-use loop
- Executor: dispatch to OpenRig seats

<a id="d9"></a>
## D9 — Retire unwired stores

**Decision.** Final Truth path (compaction_engine.py, interrupt_detector.py, final_truth_template.md); dead memory templates (productContext.md → project SPEC.md; systemPatterns.md → lore/CANON.md); the spec/handoffs/ rule.

**Rejected alternatives**

- Keep and wire them

<a id="d10"></a>
## D10 — Make the agent firewall real

**Decision.** .agentignore stays the single source and is rendered into each tool's mechanism (Claude Code permissions.deny, .cursorignore, .geminiignore, others where they exist).

**Rejected alternatives**

- Leave .agentignore as documentation

<a id="d11"></a>
## D11 — One v0.6.0 release in ordered slices

**Decision.** Built slice by slice on swimlane/v0.6.0 with a gate per slice; each pipeline skill edited once.

**Rejected alternatives**

- v0.6.0 additive + v0.7.0 breaking
- v0.6.0 breaking + v0.7.0 additive

<a id="d12"></a>
## D12 — Land through HACF's own pipeline (H5 bootstrap)

**Decision.** Repair the ground, write PROPOSED lore and a self-contained Draft_PRD.md; the user runs /hyper-redteam and /hyper-resolve in fresh contexts; MiniPRDs (v0.5.9 flat format) go through /hyper-execute + /hyper-audit.

**Rejected alternatives**

- Direct implementation without a Red Team pass
- Hybrid (Red Team only the breaking parts)

<a id="d13"></a>
## D13 — The pack lives in PRDs/v0.6.0/

**Decision.** Matches the PRDs/v0.5.0/ design-inbox convention; a README marks it reference-only.

**Rejected alternatives**

- Leave it untracked
- Delete it after capture

<a id="d14"></a>
## D14 — The HACF repo tracks lore/ and missions/

**Decision.** Plus root SPEC.md and OpenRig manifests; reverses 0652499 for missions; the installer and /hyper-update never copy them into user projects.

**Rejected alternatives**

- Track lore/ only (was the recommendation)
- Track neither

<a id="adr-0010"></a>
## ADR-0010 — Upstream update integrity (design choice, PROPOSED)

hyper_update_core.py hard-fails on GPG verification, and every HACF release commit is unsigned (`git log --format=%G?` prints N), so the script-driven update can never run. Proposal: signed-if-configured, plus explicit user consent for an unsigned upstream.

Red Team resolutions (R11, R12, R25):
- v0.6.x releases stay unsigned (Q31 answered). Any future signing must use SSH signatures: a saved v0.5.x updater checks with `git verify-commit` (hyper_update_core.py:166-171), which cannot verify an SSH signature without `gpg.ssh.allowedSignersFile`, so its `rmtree` path (AUTO_UPDATE_DIRS) stays disarmed. GPG signing would arm it for any user who imports the key. Docs never instruct a global key import (R11).
- Upstream URL and ref overrides come only from CLI flags (`--repo-url`, `--branch`, `--upstream-url`, `--ref`); the env overrides `HACF_REPO_URL`, `HACF_BRANCH` and `HACF_UPSTREAM_URL` are honored only with `HACF_TEST=1`. Non-`https://` schemes are refused outside tests. Every consent prompt and log line shows the effective URL and commit (R12).
- Allowed signers are pinned machine-locally in `.agents/state/upstream-trust.json` on first trust. An update payload's `.agents/config/upstream.yml` is only a proposal; changing the pinned set needs interactive consent showing the diff (R12).
- The installed commit is recorded in `.agents/hacf-manifest.json`; a non-descendant upstream needs `--allow-downgrade`. Consent is logged to `.agents/logs/update-consent.jsonl`; `--allow-unsigned` is refused under `HACF_AUTONOMOUS=1` or without a TTY, so unattended updates are impossible while releases are unsigned (R25).

<a id="adr-0011"></a>
## ADR-0011 — Script runtime policy (design choice, PROPOSED)

Skills invoke `python3`, which is 3.10.12 on the development machine (`python` is absent), while pyproject requires >=3.11 and the venv is 3.11. Proposal: .agents/scripts target Python >=3.10 and PyYAML >=5.4, tested under both interpreters; the dev environment stays 3.11.

<a id="deviations"></a>
## Deviations from the user decisions (mitigations, not reversals)

- D5: MCP auto-registration for Claude Code (.mcp.json), Cursor (.cursor/mcp.json) and Roo (.roo/mcp.json), whose formats are verified. Windsurf, Cline and Gemini/Antigravity get printed manual steps because their project formats are UNVERIFIED.
- D2: when a foreign SPEC.md, project.yaml, workspace.yaml or missions/ exists, HACF does not write manifests there; it stops and reports (the collision mitigation D2 asks for). Fallback locations only behind flags and only if the spike confirms. Otherwise manifests are always written.
- D2/D14: the OpenRig id prefix defaults to OPR, because rig hardcodes DEFAULT_PROJECT_PREFIX='OPR' with no config. The dogfood mission is OPR.0.6.0, not HCF.
- D6: canon bodies are quarantined in lore/proposed/ until approved. In legacy mode, lived notes temporarily go to activeContext.md 'Field Notes (legacy)' (never spec/process); the migration moves them into mission NOTES.md.
- D7: accepted docs/adr import as PROPOSED. The gate is described as harness-assisted and procedural (hook plus ask plus nonce plus warrant plus git history), not as harness-enforced.
- D8: codex and gemini are experimental; the OpenRig seat is a seam only. The claude-cli prompt tells the agent to read the real SKILL.md (the same text as the .claude/commands bridge) instead of relying on slash expansion, because --bare is slated to become the -p default. The Claude OS sandbox is required by default.
- D10: no .clineignore (deprecated and not access control); .geminiignore is rendered under HACF's antigravity id as a context filter; nothing for Antigravity CLI proper, Copilot or Zed. All non-Claude files are labeled best-effort.
- D9: historical CHANGELOG entries are kept. README claims are removed in S15a2, not S10.
- D12/AGENTS.md:77: B5 hand-edits the unparseable ledger once.
- D12: the bootstrap also writes process tooling (dev/scripts/v060_gate.py), a normative annex (dev/design/v0.6.0/) and an activeContext.md procedure section. It commits on swimlane/v0.6.0 (C1-C3 plus one commit per slice; never pushed). The parent plan's 'no commits unless asked' requires plan approval to explicitly cover this.
- D14 (R10, Red Team): HACF's repo does not track a root `.gitattributes`. The scaffold writes HACF's merge-driver attributes into `<git-common-dir>/info/attributes` (managed block `merge-drivers`) next to the repo-local driver config, in HACF's repo and in user projects. Reason: a saved v0.5.x `HACF-uninstall.sh` deletes every project file whose path exists in HACF `main`, which would remove users' own `.gitattributes` and their Git LFS rules. D14 otherwise stands (`lore/`, `missions/`, root `SPEC.md`, `project.yaml`, `workspace.yaml` stay tracked).
- D12 (R16, Red Team): one more bootstrap commit, C4, hardens `dev/scripts/v060_gate.py` before post-resolve ([operating-procedure.md#gate-amendment-c4](operating-procedure.md#gate-amendment-c4)).
- D4: compiled specs and the ledger are force-tracked on the swimlane branch during the bootstrap for recoverability. S15b untracks spec/ so main's tree has none. /hyper-resolve writes flat specs only as a foreign-collision fallback.
- D11: S01, S09a, S12b and S15a are pre-split, making 29 MiniPRDs. S11 (firewall and hook) moves before S06 so the lore gate is harness-assisted from its first use.
- Not a user decision (ADR-0010, PROPOSED): the always-failing GPG hard requirement in hyper_update_core.py (releases are unsigned: `git log --format=%G?` shows N) becomes signed-if-configured plus consent for unsigned updates.
- Not a user decision (ADR-0011, PROPOSED): .agents/scripts target Python >=3.10 and PyYAML >=5.4, tested under both interpreters. The dev environment stays at 3.11.

<a id="critique-resolutions"></a>
## Adversarial critique resolutions

Every defect, missing item, decision conflict and unverified claim raised by the adversarial review, with the reviser's verified resolution.

1. **[partially_accepted]** S01 validator rejects the real ledger's label endpoints (blocker)

   Verified: on the B4-repaired copy, 32/52 inputs and 49/61 outputs are non-node labels. The design changes in four ways:
   - Writes are non-regressive: they are refused only when they add new errors, so the mandatory updater keeps working on imperfect ledgers.
   - Label endpoints are a warning in v1.
   - migrate-v2 converts them to `external:`.
   - Fixtures come from the real file.
The proposed allowlist is replaced by a label pattern, because labels are project-specific.

2. **[accepted]** No command edits depends_on/member_of edges; remove-node hyper_fix can never succeed (blocker)

   Verified: hyper_daemon depends_on hyper_fix (architecture.yml:446-448). Added:
   - `set-edges` (cycle-checked)
   - `--depends-on` and `--member-of` on ensure-node
   - an `edges` key in apply-review
S09b runs set-edges and then remove-node, with a test of that sequence.

3. **[partially_accepted]** Appendix C and schemas never reach the fresh execute/audit sessions

   Verified: archive_specs moves the Draft into the agent-blocked archive (:19, :32-35), and execute reads only activeContext and the MiniPRD (:18-19, :34-35). Accepted:
   - a 'Pipeline Operating Notes' section plus a machine-readable hacf-bootstrap block in every MiniPRD
   - the procedure in activeContext.md
   - post-resolve grep checks
Instead of verbatim schema copies in all 29 MiniPRDs, which would push resolver output past budget, MiniPRDs cite a tracked, agent-readable normative annex (dev/design/v0.6.0/). Precedence: MiniPRD > SuperPRD trade-off log > annex.

4. **[accepted]** Compiled specs, ledger and slice work stay untracked; no per-slice commits

   Verified: .gitignore:5-10, and B8 committed only PRDs and lore. Added:
   - C1, which force-tracks spec/ on swimlane before the repair
   - a post-resolve tar backup plus force-add commit
   - per-slice `v060_gate.py finish` commits with HACF-Slice trailers
S15b untracks spec/ before landing.

5. **[accepted]** Legacy audit Haiku rewrite on the v2 ledger (wrong yaml.dump premise)

   Verified: SKILL.md:31-35 has the sub-agent write the file as free text, with no yaml.dump. The premise is withdrawn. From S01a on, the Operating Notes direct audits to `review-template` plus `apply-review`. The gate runs the non-regression check (dropped ids and keys versus the scope base, `validate --against-ref <scope base>`; see subsystems.md#verification), and `restore-ledger` restores a bad rewrite.

6. **[accepted]** S13c keeps a GPG step that always fails

   Verified: `git verify-commit 984ca46` exits 1, `%G?` is N for v0.5.4-v0.5.9, and error() calls sys.exit (:88-91, :177-179). Policy chosen in ADR-0010 (PROPOSED):
   - signatures are required when signers are configured (SSH or GPG)
   - otherwise an unsigned update needs interactive consent or --allow-unsigned, and is logged
   - tests cover an unsigned local upstream and an SSH-signed one (git 2.34)

7. **[accepted]** S15b dogfood migration misses archived MiniPRDs and name-based checklists

   Verified: SuperPRD.md:127-133 lists MiniPRDs by name, and 15 *_AUDITED.md files exist in spec/archive. Added `--include-archived` (status audited, with a legacy C1 proof whose candidate_sha comes from the HACF-Slice trailer commit). Matching accepts an exact filename or an exact normalized 'MiniPRD: <name>'. Golden plan on an HACF snapshot.

8. **[accepted]** Executor allow list ('uv run *', 'python3 .agents/scripts/*') voids the deny list; refs are shared

   Verified (permissions doc):
   - `git -C . push` is not matched by a deny rule
   - deny/ask rules are 'not a security boundary'
   - the sandbox is the OS-level control and allows writes to the shared .git in worktrees
The executors now run with the sandbox enabled via --settings (allowUnsandboxedCommands false, failIfUnavailable), and the Bash allow lists are dropped. Added a ref guard via for-each-ref before and after each phase. The scope check is the gate. The SECURITY section is reworded, including the oracle limit.

9. **[accepted]** D7 'enforced by the harness' is false (Bash(python3 *) allow, bypass modes, warrant in shell)

   Verified settings.local.json:7. Changes:
   - a PreToolUse hook catches any spelling. It returns ask in interactive modes; per the hooks doc an ask forces a prompt even in auto mode and shows the reason. It denies in dontAsk, bypassPermissions and plan.
   - `--warrant-file` written with the Write tool
   - `approval-cancel`
   - `firewall check` warns about broad allows
   - the guarantee is documented as procedural

10. **[accepted]** Pipeline runs scripts on system python3 3.10 while tests use 3.11

   Verified: /usr/bin/python3 is 3.10.12 with PyYAML 5.4.1 and pytest 9.0.3, `python` is absent, and the venv is 3.11.0rc1. Policy (ADR-0011): scripts target 3.10+ and PyYAML 5.4+. check.sh runs the suite under both interpreters, an AST scan bans 3.11-only APIs, and every bare `python ` call becomes `python3` (S06, S07a, S14, S15a1).

11. **[accepted]** Legacy-mode behavior of rewritten skills undefined

   Every call now has a legacy branch:
   - ensure-node --from-spec parses the MiniPRD header (implemented in S01a, so there is no inversion)
   - progress and proof are skipped with a notice
   - notes go to activeContext via `--legacy`
   - lore-check is keyed on the legacy provenance source
   - archival uses --miniprd
test_legacy_walkthrough runs every scripted step of both skills.

12. **[accepted]** prompt_yn returns YES under -y, so consent prompts auto-consent

   Verified HACF-install.sh:131-133. Added prompt_consent(flag), which returns NO under -y or a non-TTY unless that action's flag is given. It covers pip, uv, uvx, install.sh and ccstatusline. The test asserts that -y downloads nothing. The unconditional pip install at :630 is replaced by a preflight.

13. **[accepted]** D14 artifacts leak via README clone-as-root and saved v0.5.x uninstallers

   Verified README:42 and uninstall :119-133. Changes:
   - S15a2 replaces the clone-as-root path with git init plus the installer
   - S12c adds a manifest-based uninstaller with a legacy-hash fallback that refuses NEVER_COPY paths
   - the installer detects a saved v0.5.x uninstaller and offers replacement
   - the CHANGELOG warns

14. **[partially_accepted]** OpenRig interop only at CLI level; daemon catalog never tested

   Verified: project-workspace.md describes workspace.yaml as the catalog and the GET /api/scopes/projects read API. Added a consented `register-project` into the user's catalog, and a daemon-level spike verdict recorded in STORY-0002. That verdict can only be produced in the user's OpenRig environment (no tmux or rig here), so it gates the release (S15b) with an explicit waiver option, rather than gating S03a.

15. **[accepted]** D2 spike is skippable

   S03a depends on S02. S02's proof requires spike_verdict_cli pass (or a user waiver recorded in lore). It runs via `npx -y --package=@openrig/cli@0.6.13` with consent (node v24 present). The source confirms that scope and proof add need no daemon and that index.ts has no tmux preflight.

16. **[accepted]** Per-slice diff gate and C1 candidate_sha assume commits

   The gate diffs against the scope base (the newest commit it recorded: the post-resolve commit or the last finished slice's commit) and commits per slice. For manual v6 audits, `--candidate-sha auto` snapshots through a temp index into refs/hacf/candidates/<id> without touching the user's index. Engine runs use the engine commit.

17. **[accepted]** S01, S09a, S15a likely exceed 50k; resolver ~100k output

   Pre-split at compile time (AGENTS.md:177):
   - S01 into a, b and c
   - S09a into 1 and 2
   - S15a into 1 and 2
   - also S12b into installer and S12c uninstaller
The resolver compiles the SuperPRD first, then at most 5 MiniPRDs per response, and cites annex sections instead of copying them.

18. **[accepted]** B4 deletion leaves depends_on null (TypeError in v0.5.9 updater)

   Verified: in-memory simulation gives depends_on None, and updater :76 uses the `in` operator. B5 writes `depends_on: []`, adds a check that every edges value is a list, and runs the v0.5.9 updater on a scratch copy outside the repo.

19. **[accepted]** .gitkeep removal undone by archive_specs/.gitignore/scaffold

   Verified archive_specs.py:38-42 and .gitignore negations :6-16. S03b drops the re-creation. S15b ignores /spec/ and /tests/ wholesale and untracks them. The test asserts `git ls-files spec tests` is empty.

20. **[accepted]** Golden test flaky due to set ordering

   Verified hypergraph_updater.py:62,95. The old version runs with PYTHONHASHSEED=0 and the blast-radius line is compared as a set. The new version prints a sorted list.

21. **[accepted]** Engine branches hacf/* not push-protected

   Verified pre-push:6. The engine branches are now swimlane/hacf/<run>/integration and swimlane/hacf/<run>/<slice>. Landing is the explicit `land` command.

22. **[accepted]** Firewall stated limits wrong (cat/sed are covered)

   Verified (permissions doc): Read and Edit denies cover cat, head, tail, sed, tee and redirect targets, but not Python or Node subprocesses or unnamed reads. The text is corrected everywhere.

23. **[accepted]** .clineignore/.geminiignore are not enforcement; antigravity mapping unclear

   Verified the Cline deprecations page ('never designed to prevent access') and the geminiignore doc (filters supporting tools). .clineignore is skipped and the plugin is pointed to. .geminiignore maps to HACF's antigravity id (IDE_DEFS :52). All of these files are labeled best-effort.

24. **[accepted]** --bare may become -p default, dropping custom commands

   Verified in the headless doc. The prompt now tells the agent to read AGENTS.md and the SKILL.md, the same text as the .claude/commands bridge. Added max_tested_version and a live test. Auth breakage under bare mode is an open risk that preflight catches.

25. **[accepted]** uvx doesn't put an executable on PATH; install.sh fetched from main

   Verified the README v0.12.1 statement that package managers cache the binary. Exe resolution now supports an argv prefix ['uvx', 'codebase-memory-mcp@0.12.1'] and the `uv tool install` method. install.sh is pinned to the v0.12.1 tag URL, which exists and verifies SHA-256.

26. **[accepted]** Code graph index per project path vs engine worktrees

   Engine runs use git diff plus files: globs by default. An opt-in mode indexes the integration worktree once and calls delete_project at cleanup.

27. **[accepted]** Engine applies untracked ledger to main checkout before landing

   The ledger is staged per run in .agents/state/runs/<id>/ledger/ and applied only by `hyper_engine.py land`. test_main_checkout_byte_identical now holds.

28. **[accepted]** OpenRig closed status, OPR prefix, slice.yaml status, proof argv

   Verified at v0.6.13:
   - `slice close` moves the slice to <mission>/closed/, sets closed-<reason>, and removes the composition entry
   - missing_proof matches 'close'
   - DEFAULT_PROJECT_PREFIX = 'OPR' with no config
   - SLICE_MANIFEST has no status
   - `rig proof --workspace <root> add <slice-path> ... --name`, with default naming <type>-<verdict>-<ISO>.md
The design now mirrors all of these.

29. **[accepted]** Apache-2.0 attribution only in non-shipped fixtures

   Ships .agents/schemas/openrig/NOTICE with the URL, tag and license, following the hyper-grill-docs/ATTRIBUTION.txt precedent.

30. **[accepted]** Completeness gaps (project-templates DEVELOPMENT.md, installer banner, README hyper_fix ref)

   Verified DEVELOPMENT.md:20, install :867-868 and README:239.
   - DEVELOPMENT.md: S15a2, content only
   - banner: S12b
   - S09b's grep test is limited to code
   - README is edited once, in S15a2

31. **[accepted]** Installer upgrade edge cases (OpenRig .agents, saved installer tests copy, PyYAML after copies)

   Verified :202, :89, :409-432 and :630.
   - Upgrade detection uses the manifest or hypergraph_updater.py.
   - A PyYAML preflight aborts before any write.
   - The saved-installer abort is documented, and the state it leaves is dual-read-safe.

32. **[accepted]** Retention fix would delete migration backups

   Migration backups move to .agents/state/migrations/<ts>/backup. Retention parses only exact timestamp names and exempts migration dirs. Tested in S13c.

33. **[accepted]** Migration journal is machine-local

   resolve falls back to scanning hacf_legacy_source in missions/**/SPEC.md, including closed/.

34. **[partially_accepted]** Lore robustness: planned files, stuck pending request, generated-file conflicts

   Added planned files, resolve-anchors, approval-cancel and 30-minute expiry. For MAP.md and CANON.md, a custom `hacf-generated` driver replaces plain merge=ours (which would drop hand-written MAP edits): it 3-way merges the hand-written parts, keeps ours for generated regions, and marks them stale; `index` then regenerates.

35. **[accepted]** Engine env scrub drops auth vars; timeout kill leaves children; hooks on engine commits

   Verified the authMethod values and SIGTERM handling in the docs.
   - Prefix passthrough for ANTHROPIC_, CLAUDE_, AWS_, GOOGLE_ and similar, plus proxy and CA vars; preflight runs with the same env.
   - start_new_session with SIGTERM, then SIGKILL, to the process group.
   - New commit_rejected failure type.

36. **[accepted]** S09a depends_on misses S05a and S06

   S09a2 now depends on S09a1, S01c, S05a and S06. A release-DAG import lint test is added in S15b, and the full order was re-checked with no inversions.

37. **[accepted]** Missing: Claude Code sandbox for headless phases

   Adopted via --settings with sandbox.enabled, allowUnsandboxedCommands false, failIfUnavailable and an empty network allowlist. bwrap and socat are present here. Live-tested in S08.

38. **[accepted]** Missing: per-slice commit, ledger snapshot, post-resolve backup and force-add

   Covered by C1, the post-resolve gate, and per-slice `finish` (validate, restore-ledger, commit).

39. **[accepted]** Missing: landing path swimlane to main

   B11 step 5:
1. `git switch main && git merge --ff-only swimlane/v0.6.0` (or --squash)
2. check that the spec and tests trees are empty
3. run check.sh
4. /hyper-publish from main
Rationale: pre-push blocks swimlane, and publish pushes the current branch (SKILL.md:205-207).

40. **[partially_accepted]** Missing: resolver instructions for self-contained MiniPRDs

   Draft Appendix B mandates the Operating Notes section and the hacf-bootstrap block. Schemas are included by normative annex reference rather than copied verbatim (see the propagation item).

41. **[accepted]** Missing: ledger edge editing

   set-edges, ensure-node --depends-on/--member-of, and the apply-review edges key, all in S01a.

42. **[accepted]** Missing: migration of archived MiniPRDs and name matching

   --include-archived and exact name matching in S13a.

43. **[accepted]** Missing: upstream integrity policy

   ADR-0010 plus .agents/config/upstream.yml, in S13c.

44. **[accepted]** Missing: no-manifest behavior for uninstaller and /hyper-update

   The uninstaller falls back to legacy hashes (S12c). Without a manifest, /hyper-update treats framework files as modified: it backs them up, then overwrites, and keeps unknown files (S13c).

45. **[accepted]** Missing: interpreter policy

   ADR-0011 plus the dual-interpreter check.sh.

46. **[accepted]** Missing: OpenRig daemon registration and spike step

   register-project plus the daemon verdict; it gates the release.

47. **[accepted]** Missing: PreToolUse hook, --warrant-file, approval-cancel

   S11 adds the hook. S05a adds the warrant file and cancel.

48. **[accepted]** Missing: NOTICE for OpenRig-derived templates

   Added in S03b.

49. **[accepted]** Missing: README new-project rewrite

   Done in S15a2.

50. **[accepted]** Missing: MCP auto-registration for Cursor and Roo

   Verified .cursor/mcp.json (type stdio, command, args) and .roo/mcp.json (mcpServers command/args). Both are merged in S12b.

51. **[partially_accepted]** Missing: Claude version ceiling or live check for slash expansion

   The executor no longer depends on slash expansion. Added a max_tested_version warning and a live test instead of a hard ceiling.

52. **[accepted]** Conflict D5: MCP only for Claude

   Cursor and Roo are added. Windsurf, Cline and Gemini/Antigravity stay manual until their formats are verified.

53. **[accepted]** Conflict D2: spike skippable

   The spike is a hard gate before S03a, with a waiver only from the user.

54. **[accepted]** Conflict D7: harness-enforcement claim

   Reworded as harness-assisted and procedural. The hook closes spelling and bypass-mode gaps.

55. **[accepted]** Conflict D11: tests gating needs commits

   The gate commits each slice.

56. **[accepted]** Conflict D12: dual-read and Appendix C propagation

   Handled by the Operating Notes, activeContext, the annex and the S06 legacy branches.

57. **[accepted]** Conflict D14: leakage to users

   Handled by the README rewrite, the new uninstaller, detection of saved uninstallers and CHANGELOG warnings. D14 itself is kept.

58. **[accepted]** Conflict D10: ignore files presented as enforcement

   Ignore files are relabeled, .clineignore is skipped, and .geminiignore is mapped to the antigravity id.

59. **[partially_accepted]** Soft conflict D4: resolve --legacy writes spec/compiled

   The opt-in --legacy flag is removed. Missions are always written. Flat output remains only as a foreign-collision fallback, with a warning, because D2's collision mitigation must leave a working pipeline.

60. **[accepted]** Soft conflict D6: lived notes have no home in legacy mode

   `note --legacy` appends to the activeContext 'Field Notes (legacy)' section (never spec/process). S13b moves the notes into mission NOTES.md.

61. **[partially_accepted]** Unverified: rig runs scope/proof without tmux/daemon

   The source now confirms it: index.ts has no preflight, only `scope approve` uses the daemon, and `proof add` notes 'No daemon involvement'. The runtime check is still recorded by the S02 spike.

62. **[accepted]** Unverified: daemon lists a repo-root workspace

   It remains UNVERIFIED. The daemon-level spike in the user's environment gates the release.

63. **[accepted]** Unverified: slice create appends composition; close moves dirs

   Now verified in scope.ts @v0.6.13: create calls appendMissionMembership, and close moves the slice to <mission>/closed/ and removes its membership. The adapter mirrors this and re-reads after rig calls.

64. **[accepted]** Unverified: install.intent override honored

   It remains UNVERIFIED (spike). missions.root is honored per the scope.ts --workspace help.

65. **[accepted]** Unverified: CBM --base-branch, name override, exit codes, payloads

   These remain UNVERIFIED. The hyphenated flags are verified in README v0.12.1. A consented S04 contract test records `--help` and real outputs.

66. **[accepted]** Unverified: uvx shim works and its cache location

   The README confirms package managers verify and cache the executable; the location is UNVERIFIED. The adapter resolves the exe by argv prefix and the live test exercises uvx.

67. **[partially_accepted]** Unverified: Agent in --allowedTools; worktree trust and allow; ask in auto; --bare timing

   Verified:
   - the Agent tool name (rule `Agent(isolation:worktree)`)
   - trust and local settings resolve to the main checkout in worktrees
   - a hook's ask forces a prompt in auto mode
The engine relies only on CLI-supplied rules with `--setting-sources user,project`. The --bare default timing remains UNVERIFIED and is mitigated.

68. **[accepted]** Unverified: Gemini/Antigravity headless flags and ignore file

   These stay UNVERIFIED and experimental. Nothing is rendered for Antigravity CLI proper.

69. **[accepted]** Unverified: codex -m/-C; --full-auto unsupported

   Neither flag is used. The codex template uses only verified flags (exec, --json, --sandbox, --output-last-message).

70. **[accepted]** Unverified: Windsurf and Cline MCP config locations

   Both stay manual in v0.6.0.

71. **[rejected]** Unverified: 406 ruff errors / 30 unformatted files

   The claim is now verified without writing a cache: `.venv/bin/ruff check --no-cache --statistics .` reports 'Found 406 errors' and `ruff format --no-cache --check .` reports '30 files would be reformatted'. The rationale for staged-only lint stands.

72. **[accepted]** Unverified: folded-scalar emitter round-trip under PyYAML 5.4.1 vs 6.x

   Both versions are present (system 5.4.1, venv 6.0.3). The S01a round-trip tests run under both through check.sh.

<a id="open-risks"></a>
## Open risks

- OpenRig workspace.root equal to a code repo root is undocumented ('never assumed to be its code working directory'). If the S02 CLI spike fails, D2 needs a user decision before S03a.
- Daemon-level OpenRig interop can only be verified in the user's environment (no tmux or rig here). The release needs the recorded verdict or an explicit user waiver.
- OpenRig is 0.x with v0alpha1 schemas and roughly one maintainer. Every upgrade past 0.6.13 needs the contract test and adapter update (single-file change).
- codebase-memory-mcp 0.12.1 details remain UNVERIFIED (--base-branch, payload fields, exit codes, shim cache) until a consented contract run. Early parser fixtures may be synthetic.
- The CANON gate is harness-assisted but procedural. The hook fails closed, tokenizes commands and issues a single-use ticket that `hyper_lore.py` requires for gated verbs (R6); a deliberately forged ticket or a direct Python write to lore_hypergraph.yml still bypasses it. `validate` checks format only; git history, review and optional CODEOWNERS are the provenance (R23.5, R23.7).
- Claude Read/Edit denies do not cover Python or Node subprocesses or unnamed reads. Outside engine sandboxed phases, the firewall is advisory for those.
- Engine: agent phases get no write access to git metadata and a manifest guard checks the shared .git around every phase (R1); oracles and repo hooks run in bwrap by default and fail closed without it (R2, R3); executor-editable control files are protected and the audit reads base-revision copies (R4). Remaining exposure: user-level Claude Code hooks run in engine sessions (documented).
- Claude Code evolution: --bare will become the default for -p (the prompt no longer depends on slash expansion, but bare mode ignores OAuth logins, so auth may break; preflight detects it). dontAsk and sandbox interactions are pinned by min and max tested versions plus live tests.
- Self-hosting: S06 rewrites the audit skill that audits S06. Fallback via `git show 984ca46`.
- Before S06, audits rely on the MiniPRD Operating Notes overriding the v0.5.9 Phase 3 text. The gate's non-regression check and restore-ledger catch a free-text rewrite that drops keys.
- Unsigned upstream releases: ADR-0010 makes unsigned updates consent-gated. Authenticity rests on HTTPS to GitHub; v0.6.x stays unsigned and future signing is SSH-only (R11, R12).
- Saved v0.5.x HACF-uninstall.sh copies delete any project file whose path exists in HACF main. After D14 that includes SPEC.md, project.yaml, workspace.yaml and lore files; HACF main no longer tracks a root .gitattributes (R10). Otherwise mitigated only by detection, replacement offers and docs that lead with 'delete any saved copy of HACF-uninstall.sh'.
- Saved v0.5.x HACF-install.sh copies abort at tests/ after copying system dirs; the result is dual-read-safe.
- PEP 668 systems need --install-deps consent or a venv. The installer aborts before any write.
- The HACF ledger is untracked on main after S15b. Engine ledger changes are staged until `land`, which refuses ledger drift unless `--rebase-ledger` replays the outbox (R5); a fresh clone starts from `hyper_ledger.py init` (R21.6).
- D7 strictness: accepted legacy ADRs need individual re-approval.
- Lint debt (406 ruff errors, 30 unformatted files) persists outside the 12 legacy scripts the slices modify; S00 normalizes those 12 and freezes their residual codes (R13). Only staged and changed files are linted.
- 29 MiniPRDs make a long resolve. Mediation and compile run in separate sessions, one per batch of five (R17).
- Per-slice history with force-tracked spec/ files enters main's history on an ff-only landing. The user may choose --squash.
- Behavior changes: --ides honored under -y; statusLine opt-in; Cline ignore file not written; id prefix OPR.
