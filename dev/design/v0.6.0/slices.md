# v0.6.0 Slice Seeds (29 MiniPRDs)

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

Each seed below is the normative input for one `MiniPRD_NN_<Slug>.md` compiled by `/hyper-resolve`. `/hyper-resolve` may refine wording, but every task, test, negative constraint and dependency here must be carried into the MiniPRD or explicitly changed in the SuperPRD trade-off log.

| NN | SID | Title | Depends on | Size |
|---|---|---|---|---|
| 00 | [S00](#s00) | Test harness, dual-interpreter checks, staged-only pre-commit, state ignores | — | ~12k |
| 01 | [S01a](#s01a) | Ledger core: hyper_ledger.py (validate, non-regressive locked writes, edge editing, review patches) | S00 | ~25k |
| 02 | [S01b](#s01b) | Ledger migrate-v2 and repair-text; migrate the HACF ledger | S01a | ~15k |
| 03 | [S01c](#s01c) | hypergraph_updater compatibility, --from-diff and outbox; semantic_graph_merger guard | S01a | ~18k |
| 04 | [S02](#s02) | OpenRig spike (gate) and pinned manifest adapter | S01a | ~22k |
| 05 | [S03a](#s03a) | Spec model (hyper_spec.py): resolve, lint, progress, proof, status | S02 | ~24k |
| 06 | [S03b](#s03b) | Scope manager (hyper_scope.py), templates, NOTICE, archive_specs changes | S03a | ~24k |
| 07 | [S04](#s04) | Code-graph adapter (codebase-memory-mcp) feeding blast radius | S01c | ~22k |
| 08 | [S05a](#s05a) | Lore engine core (hyper_lore.py): state machine, nonce-bound gate, anchors, notes, outbox | S01a, S03a | ~28k |
| 09 | [S05b](#s05b) | Lore index, importers, merge drivers, HACF lore graph | S05a, S03b | ~24k |
| 10 | [S11](#s11) | Agent firewall renderer and lore gate hook | S00 | ~22k |
| 11 | [S06](#s06) | Gates; hyper-execute and hyper-audit rewritten once; /hyper-lore-approve | S03b, S04, S05b, S11 | ~20k |
| 12 | [S07a](#s07a) | hyper-architect, hyper-redteam, hyper-resolve rewritten once | S03b, S04, S05b, S06 | ~12k |
| 13 | [S07b](#s07b) | hyper-discover, hyper-baseline, hyper-grill-docs, hyper-status rewritten once | S03b, S04, S05b, S06 | ~14k |
| 14 | [S08](#s08) | Pluggable headless executors with Claude sandbox; current model ids | S00 | ~24k |
| 15 | [S09a1](#s09a1) | Engine core: failure taxonomy, run state, DAG, scheduler | S03b, S08 | ~22k |
| 16 | [S09a2](#s09a2) | Engine worktrees, ref guard, oracle, integrator, staged ledger, land | S09a1, S01c, S05a, S06 | ~30k |
| 17 | [S09b](#s09b) | Orchestrator and daemon as thin wrappers; retire hyper_fix; conflict tooling | S09a2, S05b, S01a | ~25k |
| 18 | [S10](#s10) | Retire Final Truth, dead memory templates, handoffs rule doc | S06, S07b | ~8k |
| 19 | [S12a](#s12a) | Scaffold and managed-block engine | S02, S03b, S05b, S11 | ~22k |
| 20 | [S12b](#s12b) | Installer: consent-first, merge-not-clobber, manifest-driven | S12a, S04, S11 | ~30k |
| 21 | [S12c](#s12c) | Uninstaller: manifest-driven with legacy fallback | S12b | ~12k |
| 22 | [S13a](#s13a) | Migration I: detect, plan, backup, journal, rollback, specs incl. archived | S01b, S03b, S12a | ~34k |
| 23 | [S13b](#s13b) | Migration II: ledger, lore, memory, notes, firewall, verify | S13a, S05b, S11 | ~25k |
| 24 | [S13c](#s13c) | /hyper-update rewritten once: manifest sync, integrity policy, migration hand-off | S13b, S12b | ~28k |
| 25 | [S14](#s14) | Supporting skills sweep (each edited once) | S06, S07b, S10 | ~16k |
| 26 | [S15a1](#s15a1) | Agent instruction files: trailhead, layout, schemas | S13c, S14 | ~20k |
| 27 | [S15a2](#s15a2) | Documentation | S15a1, S09b | ~25k |
| 28 | [S15b](#s15b) | Release v0.6.0 and dogfood migration | S15a2, S09b, S13a, S12c | ~18k |

<a id="s00"></a>
## S00 — NN 00: Test harness, dual-interpreter checks, staged-only pre-commit, state ignores

- **Ledger node:** none (Operating Notes: skip the updater)
- **Depends on:** —
- **Size estimate:** ~12k
- **Red Team resolutions:** R13, R19, R27

**Goal.** A deterministic gate for every later slice, covering both Python 3.11 (venv) and the system Python 3.10 + PyYAML 5.4.1 that the skills actually run.

**Files to create**

- dev/tests/conftest.py
- dev/tests/test_static_safety.py
- dev/tests/test_pre_commit.py
- dev/tests/test_pre_push.py
- dev/tests/test_runtime.py
- dev/tests/test_v060_gate.py
- dev/tests/fixtures/ledger_v1_real_broken.yml
- dev/tests/fixtures/ledger_v1_real_repaired.yml
- dev/tests/fixtures/recorded/README.md
- dev/tests/fixtures/synthetic/README.md
- dev/scripts/check.sh
- .agents/scripts/_runtime.py

**Files to modify**

- pyproject.toml
- uv.lock
- .agents/scripts/pre-commit
- .agents/scripts/pre-push
- .gitignore
- .agents/scripts/hyper_update_verification.py
- .agents/scripts/hyper_orchestrator.py
- .agents/scripts/hyper_daemon.py
- .agents/scripts/hyper_update_merge_engine.py
- .agents/scripts/hyper_resolve_conflict.py
- .agents/scripts/token_budget_checker.py
- .agents/scripts/token_budget_reconciler.py
- .agents/scripts/semantic_graph_merger.py
- .agents/scripts/archive_specs.py
- .agents/scripts/hypergraph_updater.py
- .agents/scripts/hyper_update_core.py
- .agents/scripts/model_router.py

**Files to delete**

- (none)

**Tasks**

- pyproject.toml:
- add pytest>=8 and pyyaml>=6 to the dev group
- [tool.pytest.ini_options] testpaths=['dev/tests'], markers live/openrig/codegraph/claude/sandbox, addopts '-p no:cacheprovider'
- [tool.ruff] target-version='py310'
- run `uv lock`
- dev/tests/conftest.py: fixtures tmp_git_repo, copy_fixture, run_script (selectable interpreter), frozen clock.
- Fixtures built from the real ledger:
- ledger_v1_real_broken.yml from `git show <C1>:spec/compiled/architecture.yml`
- ledger_v1_real_repaired.yml from C2
- dev/tests/test_static_safety.py: AST scan of .agents/scripts forbidding shell=True, os.system, eval and exec (currently clean), and argv lists of the form `['bash'|'sh', '-c', <non-literal>]` (R19); a 3.11-API scan for tomllib, datetime.UTC, StrEnum, typing.Self, ExceptionGroup, except*, TaskGroup, hashlib.file_digest, contextlib.chdir, asyncio.timeout, operator.call, logging.getLevelNamesMapping, typing.Never, typing.LiteralString, typing.assert_never, typing.reveal_type, typing.Required, typing.NotRequired, typing.dataclass_transform, enum.verify, enum.member and enum.nonmember (R19). No new dev dependency (no vermin, no mypy).
- dev/scripts/check.sh (R19):
- `uv run --locked pytest -m 'not live'`
- `/usr/bin/python3 -m pytest -m 'not live'`. Before it, check.sh runs `/usr/bin/python3 -c 'import pytest, yaml'` (the same binary it runs) and FAILS, never skips, when that import fails.
- ruff check and format --check (via `uv run --locked`) on files changed since the merge-base
- dev/tests/test_v060_gate.py: finish commits with the trailer, scope report, ledger id/key non-regression, post-resolve checks.
- .agents/scripts/pre-commit: lint only staged *.py (`git diff --cached --name-only --diff-filter=ACM`); remove the spec/handoffs rule (D9); keep spec/process.
- .agents/scripts/pre-push (R19): read each `<local ref> <local sha> <remote ref> <remote sha>` line; refuse a remote ref outside `refs/heads/` and `refs/tags/` (for example `refs/hacf/*`); refuse a local ref under `refs/heads/swimlane/` or `refs/heads/research/`; refuse a pushed sha that `git branch --contains <sha>` shows only on `swimlane/*` or `research/*` branches (catches `git push origin <sha>:refs/heads/main`). A header comment states that `--no-verify` bypasses the hook and that it does not stop code running with the user's credentials.
- .agents/scripts/_runtime.py (R19): `require_runtime()` checks Python >= 3.10 and `yaml.__version__` >= 5.4 and exits 2 with an actionable message (per-OS hints: `uv python install 3.12`, Homebrew, deadsnakes); `utc_now_iso()` writes `+00:00` (never `Z`); `parse_ts(text)` accepts `Z` and `+00:00` on 3.10 (replace a trailing `Z` before `datetime.fromisoformat`). From S00 on, every new or modified `.agents/scripts` entry point imports it (S00's mechanical normalization of the 12 legacy files below is exempt).
- Lint baseline for the 12 legacy scripts the slices modify (R13): run `uv run --locked ruff format` and `uv run --locked ruff check --fix` (safe fixes only, never `--unsafe-fixes`) on exactly .agents/scripts/hyper_update_verification.py, hyper_orchestrator.py, hyper_daemon.py, hyper_update_merge_engine.py, hyper_resolve_conflict.py, token_budget_checker.py, token_budget_reconciler.py, semantic_graph_merger.py, archive_specs.py, hypergraph_updater.py, hyper_update_core.py and model_router.py, as one mechanical change reviewable with `git diff -w`. Then add `[tool.ruff.lint.per-file-ignores]` entries in pyproject.toml for those 12 paths listing only the rule codes each still violates (expected among E501, D205, D415, E402, F841, D101, D103, D105, D107, D200). check.sh must pass under both interpreters afterwards.
- dev/tests/fixtures/recorded/README.md and dev/tests/fixtures/synthetic/README.md (R27): the capture layout. `recorded/<SID>/` holds verbatim live-run captures written by the test harness, never by the model (argv, stdout, stderr, exit code, tool version, UTC date); `synthetic/<SID>/` holds hand-made fixtures whose files start with a `synthetic: true` header and keep the item UNVERIFIED.
- .gitignore: .agents/state/, .agents/.backup/, .agents/logs/, .agents/hacf-manifest.json, /exhaust/, /.openrig/.

**Tests (deterministic)**

- check.sh exits 0 under both interpreters.
- test_check_sh_fails_without_310_leg: with `/usr/bin/python3` lacking pytest (shim), check.sh exits non-zero instead of skipping (R19).
- test_pre_commit_staged_only: an unstaged bad file is ignored; a staged bad file fails.
- test_pre_commit_blocks_spec_process; test_pre_commit_allows_spec_handoffs.
- test_pre_push_refuses_swimlane_sha_to_main: `<sha>:refs/heads/main` where the sha is only on a swimlane branch is refused; a sha also on main passes (R19).
- test_pre_push_refuses_non_heads_refs: a push to `refs/hacf/x` is refused (R19).
- test_static_safety_bash_c: a `['bash', '-c', f'...']` argv is reported; a literal `['bash', '-c', 'true']` is not (R19).
- test_runtime_parse_ts_z_on_310: `parse_ts('2026-10-21T00:00:00Z')` and `+00:00` both parse under `/usr/bin/python3` (R19).
- test_runtime_guard_message: an old-PyYAML shim makes `require_runtime()` exit 2 with the guidance text (R19).
- test_legacy_lint_baseline: ruff check and format --check pass on the 12 normalized files with the per-file-ignores in place (R13).
- test_state_ignored (git check-ignore).
- test_v060_gate_*.

**Negative constraints**

- DO NOT fix ruff errors outside the 12 files named in the lint-baseline task; DO NOT hand-edit their residual errors (they are frozen by per-file-ignores); DO NOT use `--unsafe-fixes` (R13).
- DO NOT reformat or autofix any other existing file (R13).
- DO NOT put tests under tests/ (HACF-install.sh:89 copies it).
- DO NOT install hooks automatically (the user re-copies pre-push; see Pipeline safety).

**Risks**

- `uv lock` needs network access.
- A safe autofix (F401 unused import, I001 sort) could drop an import with side effects; check.sh under both interpreters must stay green after the normalization (R13).

**Pipeline safety.** No skill or pipeline script behavior changes; the lint-baseline change on the 12 legacy files is formatting and safe autofixes only (R13). pre-commit is not installed here (B1). After S00 finishes, the operating procedure has the user re-copy the hardened hook: `cp .agents/scripts/pre-push .git/hooks/pre-push` (R19). The Operating Notes tell /hyper-execute to skip the updater, which avoids marking orchestration_layer and its 17 dependents. The tracking document is this MiniPRD's own task checkboxes; create no progress files (R19, applies to NN 00-11).

<a id="s01a"></a>
## S01a — NN 01: Ledger core: hyper_ledger.py (validate, non-regressive locked writes, edge editing, review patches)

- **Ledger node:** registers component_ledger (parent orchestration_layer, written as `member_of`; R21)
- **Depends on:** S00
- **Size estimate:** ~25k
- **Red Team resolutions:** R18, R19, R21, R22
- **Replan resolutions:** R28

**Goal.** A single validated writer that tolerates the real ledger's pre-existing defects, can edit edges, and replaces free-text audit rewrites with JSON review patches.

**Files to create**

- .agents/scripts/hyper_ledger.py
- dev/tests/ledger/test_ledger_core.py
- dev/tests/fixtures/ledger_v2_sample.yml

**Files to modify**

- (none)

**Files to delete**

- (none)

**Tasks**

- Model: v1/v2 load, endpoint model (node vs external), dimension ranks.
- validate: all rules, non-regression engine, --strict, --against-ref/--against-file (dropped ids and keys). A v1 ledger that carries v2-only fields (`files`, `member_of`, `provenance`) written by ensure-node before `migrate-v2` is reported as the warning "mixed v1/v2 ledger", never an error (R21). `--strict` fails on leftover `needs_review` nodes and lists them in a separate section (R21). The non-regression check treats ids listed in top-level `removed_nodes` as declared removals (R21).
- Locked atomic save with a folded-scalar representer for description. Lock files live under `$(git rev-parse --git-common-dir)/hacf/locks/` (ledger.lock), shared by all worktrees; protocol: lock, open, validate, temp file, fsync, `os.replace`, fsync the directory; keep the file mode; create the lock dir and `.agents/state/` on first use. With `HACF_OUTBOX` set, write only the outbox and take no shared lock (R22).
- Commands: show, impact, ensure-node (incl. --from-spec legacy and v6, --depends-on, --member-of, --files a,b, --spec-refs a,b), set-edges, review-template, apply-review (incl. edges), mark, remove-node, apply-outbox, init. Exit codes 0-5.
- ensure-node `--parent X` (R21): writes `member_of: [X]` when X's dimension is Layer or System; writes `implements: [X]` only when X is a Module and the new node is Atomic; any other combination is a usage error (exit 2).
- remove-node `<id> --reason R` (R21): still refuses while inbound edges exist; on success appends a tombstone `{id, reason, slice, commit}` to the top-level `removed_nodes` list (slice from `--slice`, commit from `git rev-parse HEAD`).
- init (R21): writes an empty `hacf.ledger/v2` ledger at the given path (default spec/compiled/architecture.yml); refuses (exit 5) when the file exists.
- ensure-node list flags (R28): `--files` takes one comma-separated list and replaces the node's `files`; `--spec-refs a,b` appends the dot-IDs missing from `spec_refs` (a repeat is a no-op; an item that is not a dot-ID exits 2) and is the only writer of `spec_refs`.
- Frozen CLI contract (R18): the subcommand names and flag spellings above, as written in dev/design/v0.6.0/schemas.md#architecture-yml-changes, are a contract later slices' pinned `ensure_node` commands rely on; changing them needs a replan R-row. Put a module docstring section "CLI contract (frozen for v0.6.x)" listing them.
- Import `_runtime.require_runtime()` at the entry point (R19).
- At the end, run ensure-node component_ledger.

**Tests (deterministic)**

- test_validate_detects: dangling, inverted implements, duplicate, cycle, bad dimension, unsafe glob, null edges.
- test_non_regression: on ledger_v1_real_repaired a status write succeeds despite 81 label endpoints; a new dangling edge is refused (exit 5) and the file is untouched.
- test_lock_before_open: 2 processes x 50 writes.
- test_lock_shared_across_worktrees: two linked worktrees resolve the same lock file under the git common dir (R22).
- test_set_edges_cycle_refused.
- test_remove_node_refused_then_ok_after_set_edges.
- test_remove_node_writes_tombstone: `removed_nodes` gains `{id, reason, slice, commit}` and `validate --against-ref` does not report the id as dropped (R21).
- test_parent_member_of_for_layer: `ensure-node --parent orchestration_layer` writes `member_of`, not `implements` (R21).
- test_parent_implements_for_module: an Atomic node with a Module parent gets `implements` (R21).
- test_mixed_v1_v2_warning: ensure-node into ledger_v1_real_repaired adds v2 fields; validate exits 0 with the "mixed v1/v2 ledger" warning (R21).
- test_strict_fails_on_needs_review (R21).
- test_init_writes_empty_v2_and_refuses_existing (R21).
- test_apply_review_roundtrip_with_edges.
- test_ensure_node_from_legacy_header.
- test_ensure_node_list_flags_and_spec_refs: `--files a,b` writes both globs; `--spec-refs OPR.0.6.0.1` then `--spec-refs OPR.0.6.0.1,OPR.0.6.0.2` lists each id once; an item that is not a dot-ID exits 2 (R28).
- test_against_ref_reports_dropped_keys.
- Folded-scalar round trip under both interpreters (check.sh).

**Negative constraints**

- DO NOT allow any write path that skips validation.
- DO NOT infer edges from prose.
- DO NOT change a subcommand name or flag spelling of the frozen CLI contract after this slice without a replan R-row (R18).
- DO NOT write `implements` for a Layer or System parent (R21).

**Risks**

- PyYAML 5.4 vs 6 dump differences; covered by dual-interpreter tests.

**Pipeline safety.** New module; hypergraph_updater is unchanged. From this slice's own audit on (Q28: `audit_phase3: review-patch`), the Operating Notes have audits run `review-template` and then `apply-review` instead of the Haiku free-text rewrite (hyper-audit SKILL.md:31-35); the gate's built-in ledger checks (parse, list-valued edges, no dropped ids or keys) stay independent of hyper_ledger.py. The gate checks non-regression. Writing `member_of` (R21) means S01a's own mandatory v0.5.9 updater call does not walk the parent: the v0.5.9 updater (hypergraph_updater.py:68-88) traverses only `implements` and reverse `depends_on`, ignores `member_of` and keeps unknown keys.

<a id="s01b"></a>
## S01b — NN 02: Ledger migrate-v2 and repair-text; migrate the HACF ledger

- **Ledger node:** component_ledger
- **Depends on:** S01a
- **Size estimate:** ~15k
- **Red Team resolutions:** R16, R21

**Goal.** Deterministic migration of v1 ledgers (membership, externals, provenance, files seeding) and text repair of LLM-broken scalars, applied to HACF's own ledger.

**Files to create**

- dev/tests/ledger/test_ledger_migrate.py

**Files to modify**

- .agents/scripts/hyper_ledger.py
- spec/compiled/architecture.yml (via the script only)

**Files to delete**

- (none)

**Tasks**

- migrate-v2 [--dry-run]:
- adds the schema key
- depends_on edges targeting Layer nodes become member_of
- non-node endpoints become external
- _provenance becomes provenance
- seeds files: from associated_file when it is a source file
- normalizes a mixed v1/v2 ledger (v2 fields written by ensure-node before migration) without duplicating edges (R21)
- keeps the top-level `removed_nodes` list (R21)
- shows a diff; idempotent
- repair-text [--dry-run]: plain `description:` scalars containing ' #' or ': ' become folded; prints a diff.
- Apply to the HACF ledger: dry-run, review the diff, then apply. 17 `depends_on` membership edges convert; with the `member_of` edge S01a wrote for component_ledger, the HACF ledger ends with 18 `member_of` edges (R21).

**Tests (deterministic)**

- test_repair_text_real: ledger_v1_real_broken parses afterwards, and the diff touches only description lines.
- test_migrate_real: 17 member_of edges, 81 externals, second run is a no-op.
- test_v059_updater_still_runs_on_v2: `git show 984ca46:.agents/scripts/hypergraph_updater.py` run on the migrated fixture exits 0.
- test_migrate_mixed_ledger: a v1 ledger with a `component_ledger` node carrying `member_of` migrates to 18 `member_of` edges with no duplicates; a second run is a no-op (R21).

**Negative constraints**

- DO NOT hand-edit the ledger.
- DO NOT seed files: for Layer/System nodes.

**Risks**

- Exotic LLM YAML that repair-text cannot handle: it stops and reports.

**Pipeline safety.** The v0.5.9 updater stays in place until S01c. It still works on v2, because its BFS reads only depends_on and implements (:58-80) and yaml.dump keeps keys. Removing the membership edges stops the 17-node fan-out. The gate's base-revision validator check (R16) runs S01a's `hyper_ledger.py validate` against the migrated v2 file; if the old version reports findings it did not report at the base (for example v2-only keys), finish refuses until the user approves `--accept-validator-change REASON` in AskUserQuestion. Expect this waiver here.

<a id="s01c"></a>
## S01c — NN 03: hypergraph_updater compatibility, --from-diff and outbox; semantic_graph_merger guard

- **Ledger node:** hypergraph_updater (existing)
- **Depends on:** S01a
- **Size estimate:** ~18k
- **Red Team resolutions:** R19, R21

**Goal.** Keep the mandatory updater call stable while adding diff-driven blast radius and outbox routing.

**Files to create**

- dev/tests/ledger/test_updater_compat.py
- dev/tests/fixtures/diffs/

**Files to modify**

- .agents/scripts/hypergraph_updater.py
- .agents/scripts/semantic_graph_merger.py

**Files to delete**

- (none)

**Tasks**

- Positional form delegates to hyper_ledger with the same banner and exit codes; the blast-radius list is sorted.
- --from-diff, --slice, --apply, --outbox, --json; HACF_OUTBOX; post-impact hook (lazy import of hyper_lore).
- --from-diff (and `hyper_ledger.py impact --from-diff`) prints and returns `unmapped: [paths]`, the changed files that no node's `files:` glob (or v1 `associated_file`) covers; JSON output has an `unmapped` key; an empty result is printed explicitly, never silently (R21).
- When the ledger file is missing, the updater's "Hypergraph not found" message names the first-run path: `python3 .agents/scripts/hyper_ledger.py init` or `/hyper-discover` (R21).
- Golden fixture diffs (R21): dev/tests/fixtures/diffs/ holds rev-range scenarios replayed in a temp repo that carries the real ledger's `files:` globs.
- Delete the uncalled provenance subcommands; grep shows no callers.
- semantic_graph_merger: basename guard; validation via hyper_ledger (non-regressive).
- Import `_runtime.require_runtime()` in both modified entry points (R19).

**Tests (deterministic)**

- test_legacy_cli_golden: run the 984ca46 version with PYTHONHASHSEED=0; compare the blast-radius line as a set and the rest byte-for-byte (updater :62,:95 prints a set).
- test_from_diff.
- test_from_diff_unmapped: a diff touching `HACF-install.sh` and `.agentignore` lists both under `unmapped` (R21).
- test_from_diff_golden_fixtures: each scenario in dev/tests/fixtures/diffs/ maps to its expected node set and unmapped list (R21).
- test_missing_ledger_message_names_init (R21).
- test_outbox_routing.
- test_merger_guard: rejects lore_hypergraph.yml and docker-compose.yml.

**Negative constraints**

- DO NOT change the positional contract.

**Risks**

- Golden test brittleness; mitigated by set comparison.

**Pipeline safety.** The call in hyper-execute SKILL.md:64 and AGENTS.md:51 is unchanged; the Operating Notes use python3.

<a id="s02"></a>
## S02 — NN 04: OpenRig spike (gate) and pinned manifest adapter

- **Ledger node:** openrig_adapter
- **Depends on:** S01a
- **Size estimate:** ~22k
- **Red Team resolutions:** R19, R22, R24, R27

**Goal.** Verify the repo-root workspace with real rig 0.6.13 before any topology work, then freeze the contract in one adapter.

**Files to create**

- .agents/scripts/hyper_openrig.py
- dev/tests/openrig/test_openrig_adapter.py
- dev/tests/contract/test_openrig_live.py
- dev/tests/fixtures/openrig-0.6.13/
- dev/tests/fixtures/recorded/S02/
- lore/STORIES/STORY-0002-openrig-spike.md

**Files to modify**

- (none)

**Files to delete**

- (none)

**Tasks**

- hyper_openrig.py:
- pinned schemas and templates; renderers
- add_slice_to_mission (orders 10, 20, ...) with lifecycle-manifest validation
- dot-IDs (prefix OPR)
- status map, including closed-superseded and moving the slice to closed/
- argv wrappers for scope audit, scope slice ls/create/close and `rig proof --workspace <root> add <slice-path> ... --name`; every rig call that names an artifact passes `--name` (R22)
- register-project (consented; backs up the catalog). It refuses an existing id (exit 5), stores the repo's realpath plus a fingerprint (the root commit sha, `git rev-list --max-parents=0 HEAD`), and gains `unregister` and a `doctor` check that reports moved roots and fingerprint mismatches (R22)
- rig version rule (R22, Q8): `use_rig_when_present` applies only when `rig --version` equals 0.6.13; another 0.6.x: HACF writes `v0alpha1` artifacts itself and warns; another major.minor: HACF stops calling rig until the contract test passes on that version.
- Import `_runtime.require_runtime()` at the entry point (R19).
- Fixture dev/tests/fixtures/openrig-0.6.13/ with source URLs and commit 9eeb2e42.
- dev/tests/contract/test_openrig_live.py (marker openrig). Runs via `npx -y --package=@openrig/cli@0.6.13 rig` or rig on PATH, against a temp git repo used as the workspace root. The harness (never the model) writes a verbatim capture of each rig call (argv, stdout, stderr, exit code, `rig --version`, UTC date) to dev/tests/fixtures/recorded/S02/ (R27). After each rig call it asserts `git status --porcelain` lists only expected paths (R22). It adds a case with the temp repo nested inside a rig workspace root (R22).
- With the user's consent, run the CLI spike. Write lore/STORIES/STORY-0002-openrig-spike.md (PROPOSED) with spike_verdict_cli and spike_verdict_daemon, computed by the test from the captures (the model never writes a verdict; R27), and the npm integrity of the package run (`npm view @openrig/cli@0.6.13 dist.integrity`; R24). The daemon verdict is 'pending-user-env' unless the user runs register-project in their OpenRig; the user-run daemon check also registers two projects with the same OPR ids and records whether they collide in the daemon's views (R22). If the CLI verdict fails, stop and escalate (D2 is user-owned; the replan procedure applies, R14).
- Run ensure-node openrig_adapter.

**Tests (deterministic)**

- test_render_matches_fixture; test_composition_rules; test_dot_ids_opr; test_close_superseded_moves_and_unregisters; test_no_shell.
- test_rig_calls_pass_name: every artifact-naming argv wrapper includes `--name` (R22).
- test_register_project_refuses_existing_id; test_register_project_fingerprint_and_doctor; test_unregister (R22).
- test_rig_version_rule: 0.6.13 delegates, 0.6.14 writes natively with a warning, 0.7.0 stops calling rig (R22).
- live: zero high-severity scope-audit findings; slice create appends a composition entry; HACF frontmatter survives `rig scope slice progress`; proof add is accepted; `git status --porcelain` after each call lists only expected paths; the nested-workspace case resolves the intended workspace.yaml (R22).

**Negative constraints**

- DO NOT download rig without consent.
- DO NOT run `rig config set` or `init-workspace`.
- DO NOT write execution.depends_on or slice.yaml status.

**Risks**

- A failed spike forces a D2 decision before S03a.

**Pipeline safety.** New files; the spike writes only to temp dirs, npx's cache and the capture dir dev/tests/fixtures/recorded/S02/ (R27). A declined spike is recorded as skipped; captures and verdicts are never invented.

<a id="s03a"></a>
## S03a — NN 05: Spec model (hyper_spec.py): resolve, lint, progress, proof, status

- **Ledger node:** spec_model
- **Depends on:** S02
- **Size estimate:** ~24k
- **Red Team resolutions:** R1, R19, R22
- **Replan resolutions:** R29

**Goal.** One deterministic reader and writer for the project, mission, slice and legacy flat specs.

**Files to create**

- .agents/scripts/hyper_spec.py
- dev/tests/spec/test_spec.py
- dev/tests/fixtures/specs/

**Files to modify**

- .agents/scripts/hyper_openrig.py (R29: add `MISSION_STATUSES`, `splice_mission_status()` and `set_mission_status()` only)

**Files to delete**

- (none)

**Tasks**

- resolve <path|dot-id|legacy> --json:
- returns layout, ids, node_id, parent, status, depends_on, touches and oracle
- the legacy header parser reads the NN prefix
- uses the migration journal, else scans missions/**/SPEC.md and closed/ for hacf_legacy_source
- lint: OpenRig-required sections (the per-type section rules of dev/design/v0.6.0/schemas.md#spec-frontmatter, R29); frontmatter; dot-IDs; depends_on targets; safe touches; exact heading alias table.
- progress: transitions; rewrites only HACF-owned keys (dev/design/v0.6.0/schemas.md#key-ownership); updates slice PROGRESS, mission PROGRESS and mission.yaml metadata.status; outbox-aware; legacy mode skips with a notice.
- Mission status in the adapter (R29): add `MISSION_STATUSES = ("planned", "active", "complete")`, `splice_mission_status(text, status) -> str` and `set_mission_status(mission_dir, status) -> str` to hyper_openrig.py, as dev/design/v0.6.0/schemas.md#openrig-manifests specifies. progress sets planned -> active on the first executing event with the splice's bytes under its own compare-and-swap write. Change nothing else in hyper_openrig.py.
- Compare-and-swap writes (R22) for every file HACF shares with rig or OpenRig (slice and mission SPEC.md frontmatter, PROGRESS.md, mission.yaml): record the sha256 at read, re-read before `os.replace`, exit 5 with nothing written on mismatch. Same write protocol and lock location as hyper_ledger (git common dir; directory fsync).
- proof: C1 writer; --candidate-sha auto (temp-index snapshot to refs/hacf/candidates/<id>); delegates to rig when present. The temp index is seeded with `git read-tree HEAD` before `git add -A` and `git write-tree`, so force-tracked files under ignored directories stay in the tree (R22). C1 frontmatter gains an optional `swimlane_sha`; proof checks treat an unreachable `candidate_sha` that has `swimlane_sha` as historical (warning), not an error (R22). With `HACF_OUTBOX` set (engine phases), `--candidate-sha auto` refuses (exit 4): the engine writes PROOF and candidate refs itself (R1).
- Timestamps via `_runtime.utc_now_iso()` and `_runtime.parse_ts()`; import `_runtime.require_runtime()` at the entry point (R19).
- status --json; ensure-node spec_model.

**Tests (deterministic)**

- test_resolve_legacy_v6_journal_and_frontmatter_fallback.
- test_lint_reports.
- test_progress_transitions_atomic.
- test_progress_cas_refuses_concurrent_change: a file changed between read and replace gives exit 5 and is left as the other writer wrote it (R22).
- test_frontmatter_roundtrip_unknown_keys.
- test_proof_c1_closed_sets.
- test_proof_swimlane_sha_historical: an unreachable candidate_sha with swimlane_sha is a warning; without it, an error (R22).
- test_mission_status_splice_and_writer: the splice changes only the `metadata.status` scalar (comments, key order and every other byte kept), a repeat is byte-identical, a value outside MISSION_STATUSES exits 2, a missing `metadata.status` exits 4; the writer refuses (exit 5) when the file changed between read and replace (R29).
- test_candidate_auto_does_not_touch_index.
- test_candidate_auto_keeps_force_tracked_ignored: in a repo with `spec/` ignored and `spec/ledger.yml` force-tracked, the candidate tree contains `spec/ledger.yml` (R22).
- test_candidate_auto_refused_under_outbox (R1).

**Negative constraints**

- DO NOT use slug-to-heading matching.
- DO NOT write outside the target mission or slice.

**Risks**

- Hand-written legacy MiniPRDs deviate from the schema; lint warns only.

**Pipeline safety.** New module; not called by any skill until S06.

<a id="s03b"></a>
## S03b — NN 06: Scope manager (hyper_scope.py), templates, NOTICE, archive_specs changes

- **Ledger node:** scope_manager
- **Depends on:** S03a
- **Size estimate:** ~24k
- **Red Team resolutions:** R16, R18, R19, R22

**Goal.** Deterministic project, mission and slice creation with root-collision detection, the DAG, and script-only archival.

**Files to create**

- .agents/scripts/hyper_scope.py
- .agents/schemas/openrig/project_spec.md
- .agents/schemas/openrig/mission_spec.md
- .agents/schemas/openrig/slice_spec.md
- .agents/schemas/openrig/mission_progress.md
- .agents/schemas/openrig/slice_progress.md
- .agents/schemas/openrig/proof.md
- .agents/schemas/openrig/notes.md
- .agents/schemas/openrig/NOTICE
- dev/tests/scope/test_scope.py

**Files to modify**

- .agents/scripts/archive_specs.py

**Files to delete**

- (none)

**Tasks**

- hyper_scope.py:
- project init [--check-only --json]: classifies each root item as missing, hacf, openrig or foreign; foreign gives exit 5 with nothing written
- mission create [--from-draft]
- slice create/close/move
- dag --json (Kahn; cycles reported with their path; execution.depends_on wins)
- all checks run before any write; writes to files rig also writes (SPEC.md frontmatter, PROGRESS.md, mission.yaml) use hyper_spec's compare-and-swap protocol (R22)
- import `_runtime.require_runtime()` at the entry point (R19)
- Templates .agents/schemas/openrig/*.md plus NOTICE (Apache-2.0, upstream URL, tag v0.6.13).
- archive_specs.py:
- create spec dirs lazily
- stop recreating spec/active/.gitkeep (:38-42)
- positional behavior otherwise unchanged
- add --miniprd (gives the same end state as audit SKILL.md:41-44)
- add --paths --label
- ensure-node scope_manager.

**Tests (deterministic)**

- test_project_init_collision_matrix.
- test_mission_create_tree_idempotent.
- test_slice_close_superseded.
- test_dag_cycle_precedence.
- test_scope_cas_refuses_concurrent_change (R22).
- test_archive_positional_golden.
- test_archive_miniprd.
- test_no_gitkeep_recreation.

**Negative constraints**

- DO NOT overwrite existing files.
- DO NOT change positional archival.

**Risks**

- Uncertain collision cases are reported as foreign.

**Pipeline safety.** The positional call used by hyper-resolve is unchanged; the B.6 archive step moves spec/active/FAILED_WORKFLOWS.md aside before `archive_specs.py v0_6_0` and restores it afterwards (R18), so no change to positional behavior is needed for it. From S03b on, the Operating Notes switch audit Phase 4 to `archive_specs.py --miniprd`. The gate's base-revision validator check (R16) runs the S03a `hyper_spec.py lint --all` against this slice's tree; a new lint finding that comes only from the older validator needs the user-approved `--accept-validator-change` waiver.

<a id="s04"></a>
## S04 — NN 07: Code-graph adapter (codebase-memory-mcp) feeding blast radius

- **Ledger node:** code_graph_adapter
- **Depends on:** S01c
- **Size estimate:** ~22k
- **Red Team resolutions:** R19, R20, R24, R27
- **Replan resolutions:** R31, R32, R33, R34

**Goal.** A pinned, contract-tested executable adapter with explicit unsupported and unavailable results.

**Files to create**

- .agents/scripts/hyper_codegraph.py
- .agents/config/code_graph.defaults.yml
- dev/tests/codegraph/test_codegraph.py
- dev/tests/contract/test_codegraph_live.py
- dev/tests/fixtures/codegraph-0.12.1/
- dev/tests/fixtures/recorded/S04/
- dev/tests/fixtures/synthetic/S04/

**Files to modify**

- .agents/scripts/hypergraph_updater.py

**Files to delete**

- (none)

**Tasks**

- hyper_codegraph.py:
- 3-layer config; exe prefix resolution (path, uv tool, uvx with consent)
- capabilities and normalization; components via files: globs
- doctor; index stores the project name in state (.agents/state/codegraph/project.json)
- delete: removes the backend's index for the project named in .agents/state/codegraph/project.json (the uninstaller offers it, R24)
- Placeholder rules (R24): `project` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`; any placeholder value starting with `-` is refused (exit 2) before argv is built.
- Missing `.agents/config/code_graph.defaults.yml` falls back to embedded defaults with the notice "run HACF-install.sh to complete the upgrade" (R24).
- Import `_runtime.require_runtime()` at the entry point (R19).
- Add .agents/config/code_graph.defaults.yml.
- hypergraph_updater --from-diff enrichment when the result is ok.
- Capability templates as dev/design/v0.6.0/schemas.md#code-graph-config pins them after the first contract run: no `--format` for index and delete (R31); paging literals for list_projects and diff_impact (R33).
- symbol_impact: trace_path names no file, so `file`, `line`, `kind` and `risk` are null, `impacted_files` and `components` are empty, and a warning says components come from diff_impact (R32).
- Truncation (`has_more`, `*_has_more`, `truncated`): status error for diff_impact and list_projects, a warning for search and symbol_impact (R33).
- --from-diff base (R34): the full commit id of the single revision; a rev-range skips the code graph with the fallback line; `hacf.yml` `code_graph.base_branch` only when the contract test records that 0.12.1 refuses a commit id.
- Contract test (marker codegraph, consented): `cli <tool> --help` and recorded outputs, including `detect_changes --base-branch <commit id>` on an uncommitted edit (R34). The harness (never the model) writes verbatim captures (argv, stdout, stderr, exit code, tool version, UTC date) to dev/tests/fixtures/recorded/S04/ (R27). It records how the codebase-memory-mcp shim verifies its native runtime (pinned hash or version only) as a finding in the audited Operating Notes (R24).
- Declined contract test (R20, R27): parser fixtures are synthetic, live under dev/tests/fixtures/synthetic/S04/ with a `synthetic: true` header, the backend stays `none` by default, and the install prompt labels it "unverified".

**Tests (deterministic)**

- test_backend_none_unsupported.
- test_missing_binary_unavailable.
- test_exe_prefix_uvx.
- test_placeholder_validation.
- test_placeholder_leading_dash_refused: `project` values `--format` and `-x`, and a symbol or pattern starting with `-`, are refused before any subprocess runs (R24).
- test_missing_defaults_uses_embedded (R24).
- test_delete_uses_recorded_project (R24).
- test_synthetic_fixtures_labeled: every file under dev/tests/fixtures/synthetic/S04/ starts with the `synthetic: true` header (R27).
- test_normalize_fixture.
- test_from_diff_enrichment.
- test_symbol_impact_without_files: a trace_path payload gives impacted entries with `file` null and `distance` from `hop`, empty `impacted_files` and `components`, the R32 warning and status ok (R32).
- test_truncated_results: `has_more`, a `*_has_more` key or `truncated` set to true gives status error 'truncated: <keys>' for diff_impact and list_projects (the updater prints the fallback line) and status ok with the warning for search and symbol_impact (R33).
- test_from_diff_base_commit: `--from-diff HEAD` passes HEAD's full commit id as `--base-branch` (the stub's argv log); a rev-range runs no backend call and prints the fallback line (R34).

**Negative constraints**

- DO NOT use shell=True.
- DO NOT install without consent; always pass --skip-config to the vendor script.
- DO NOT write or edit a capture under dev/tests/fixtures/recorded/S04/ by hand; DO NOT invent recorded outputs (R27).

**Risks**

- The backend ships fast-moving 0.x releases; the version is pinned.

**Pipeline safety.** The default backend is none, so behavior is unchanged. A declined contract test is recorded as skipped and the items stay UNVERIFIED (R20, R27).

<a id="s05a"></a>
## S05a — NN 08: Lore engine core (hyper_lore.py): state machine, nonce-bound gate, anchors, notes, outbox

- **Ledger node:** lore_engine
- **Depends on:** S01a, S03a
- **Size estimate:** ~28k
- **Red Team resolutions:** R6, R7, R8, R19, R21, R22, R23

**Goal.** lore_hypergraph.yml as a locked, validated source of truth with the full state machine.

**Files to create**

- .agents/scripts/hyper_lore.py
- dev/tests/lore/test_lore_core.py
- dev/tests/fixtures/lore/

**Files to modify**

- (none)

**Files to delete**

- (none)

**Tasks**

- Schema validation and id allocation.
- propose: canon bodies go to lore/proposed/.
- note: --mission, or --legacy into activeContext.
- approval-request (prints the hashed body and every id the approval would supersede, retire or re-anchor, also stored in the pending record for the hook reason and the AskUserQuestion body; R23), approval-cancel, approve, reject, retire, reopen, review, with --nonce and --warrant-file.
- approve moves the body from lore/proposed/<ID>.md to lore/canon/<ID>.md, which is never edited afterwards; supersede, retire and reopen change status in lore_hypergraph.yml only (R8).
- Gated-verb ticket check (R6): approve, reject, retire, reopen and `review --still-valid` refuse (exit 4) unless an unexpired single-use ticket exists under .agents/state/lore/tickets/ bound to the same verb, id and nonce (TTL 120 s, written by the S11 hook); the ticket is consumed on use. Ticketless fallback for tools without a PreToolUse hook: proceed only when stdin and stdout are a TTY and the user types the node id at a confirmation prompt; otherwise exit 4. Nothing approves between NN 08 and NN 10; tests use a test ticket dir.
- Refusal under HACF_AUTONOMOUS=1, or without a hook ticket or TTY confirmation (R6, R7). hyper_lore.py does not try to learn Claude Code's permission mode (R7).
- Automatic supersession; stale (impact or sha); resolve-anchors (planned moves to anchors).
- validate (R21): anchors to ids tombstoned in the ledger's `removed_nodes` or to deleted files are stale warnings (exit 0 when only warnings; CANON nodes with such anchors are marked stale and routed to the gated stale review); anchors to unknown ids are errors. A CANON node without its lore/canon/<ID>.md file is an error (R8). Stale CANON stays binding until retired or superseded; show and list report it as binding (R23).
- show, list (incl. `--status CANON --with-commit`, which prints the commit that introduced each CANON status; R23), graph, apply-outbox (refuses approval-type entries), sync.
- Lock and write protocol as hyper_ledger (lock under the git common dir, directory fsync; with HACF_OUTBOX set, outbox only; R22). Timestamps (`approved_at`, `expires_at`) via `_runtime.utc_now_iso()`/`parse_ts()`; import `_runtime.require_runtime()` (R19).
- stale_from_impact() for the S01c hook.
- ensure-node lore_engine.

**Tests (deterministic)**

- test_transitions_matrix.
- test_gate_nonce_sha_single_pending_cancel_expiry.
- test_warrant_file_required_and_not_title.
- test_autonomous_refusal.
- test_gated_verb_requires_ticket: approve without a ticket and without a TTY exits 4; with a matching ticket it succeeds and the ticket is gone; a ticket for another id, verb or nonce, or an expired one, is refused (R6).
- test_ticketless_tty_confirmation: with a pty, typing the node id proceeds and anything else refuses (R6).
- test_approve_moves_body_to_canon_file: lore/canon/<ID>.md exists with the approved body, lore/proposed/<ID>.md is gone; retire leaves the canon file untouched (R8).
- test_approval_request_lists_superseded_ids (R23).
- test_validate_tombstoned_anchor_warns: an anchor to a tombstoned id or a deleted file warns and exits 0; an unknown id exits 1 (R21).
- test_validate_canon_without_file_errors (R8).
- test_list_canon_with_commit (R23).
- test_timestamps_z_suffix_on_310 (R19).
- test_reject_keeps_node.
- test_outbox_rejects_approvals.
- test_resolve_planned_anchors.
- test_concurrent_writers.
- test_pointer_validation.

**Negative constraints**

- DO NOT delete on reject.
- DO NOT accept approved_by from args (it is attribution from git config, not identity; R23).
- DO NOT approve more than one id per invocation.
- DO NOT edit a lore/canon/<ID>.md file after approval (R8).
- DO NOT run a gated verb without a ticket or TTY confirmation (R6).

**Risks**

- The gate is procedural (see open_risks). Remaining bypasses after R6: a deliberately forged ticket and direct Python writes to lore_hypergraph.yml; documented in docs/Lore.md (S15a2).

**Pipeline safety.** New module; the S01c hook stays a no-op until the graph exists. The ticket writer (S11) lands at NN 10 and /hyper-lore-approve at NN 11, so no approval runs before then.

<a id="s05b"></a>
## S05b — NN 09: Lore index, importers, merge drivers, HACF lore graph

- **Ledger node:** lore_index
- **Depends on:** S05a, S03b
- **Size estimate:** ~24k
- **Red Team resolutions:** R8, R10, R16, R20, R22, R26

**Goal.** Generated MAP.md and CANON.md, draft and docs/adr importers, merge drivers, and HACF's own graph.

**Files to create**

- dev/tests/lore/test_lore_index.py
- lore/MAP.md
- lore/CANON.md
- lore_hypergraph.yml

**Files to modify**

- .agents/scripts/hyper_lore.py

**Files to delete**

- (none)

**Tasks**

- index: the MAP.md generated region (canon, proposed, stale, graveyard, stories, where things live); CANON.md as a pure concatenation of the lore/canon/<ID>.md files of CANON nodes, rebuildable at any time from the graph and those files (R8). The MAP.md graveyard lists the newest 20 REJECTED entries followed by the line "full list: `hyper_lore.py list --status REJECTED`" (R20). Generated regions use the exact markers `<!-- BEGIN HACF GENERATED: <id> -->` / `<!-- END HACF GENERATED: <id> -->`, recognized only at column 0 and outside fenced code (R26).
- import --drafts: forces PROPOSED; resolves planned anchors.
- import --adr-dirs [--context]: status mapping; legacy.source kept; leaves a stub README.
- merge-driver (hacf-lore) and merge-generated (hacf-generated). hacf-generated regenerates the region (runs index on the merged graph) instead of keeping "ours" (R8).
- renumber <old> <new> (R22): rewrites every in-repo reference to the old id (`lore_refs` in missions/**/SPEC.md and slice frontmatter, other nodes' `relates_to` and `supersedes`) and reports out-of-repo references (commit messages) without rewriting them.
- install-drivers (R10): writes the repo-local `merge.hacf-lore.*` and `merge.hacf-generated.*` git config and a `# BEGIN HACF MANAGED BLOCK: merge-drivers` / `# END HACF MANAGED BLOCK: merge-drivers` block into `$(git rev-parse --git-common-dir)/info/attributes` (`lore_hypergraph.yml merge=hacf-lore`, `lore/MAP.md merge=hacf-generated`, `lore/CANON.md merge=hacf-generated`); idempotent; never writes a tracked `.gitattributes`.
- HACF repo: import ADR-0001..0011 and STORY-0001/0002; create MAP.md, CANON.md and lore_hypergraph.yml; run install-drivers. No root `.gitattributes` is created or tracked (R10).
- ensure-node lore_index.

**Tests (deterministic)**

- test_index_idempotent_preserves_manual.
- test_canon_md_rebuilt_from_canon_files: deleting lore/CANON.md and running index restores it byte-for-byte (R8).
- test_graveyard_capped_at_20 (R20).
- test_generated_markers_column0_outside_fences: a marker inside a fenced code block or indented is not treated as a region (R26).
- test_import_adr_mapping_multicontext.
- test_merge_driver_union_and_conflict.
- test_merge_generated_regenerates: a merge of two branches that each approved a different node yields a CANON.md containing both bodies (R8).
- test_renumber_rewrites_references: lore_refs, slice frontmatter, relates_to and supersedes follow the new id; commit-message references are reported (R22).
- test_install_drivers_writes_info_attributes: the attributes block lands in the git common dir's info/attributes, a second run changes nothing, and no `.gitattributes` exists in the work tree (R10).
- test_bootstrap_drafts_import (13 nodes PROPOSED).

**Negative constraints**

- DO NOT import accepted ADRs as CANON.
- DO NOT create or track a root `.gitattributes` (R10).

**Risks**

- The merge drivers need per-clone git config and the info/attributes block (both repo-local, set by install-drivers or the scaffold); validate catches the fallback.

**Pipeline safety.** Additive. The stale hook now marks lore nodes only. The gate's base-revision validator check (R16) runs S05a's `hyper_lore.py validate` against this slice's tree; a finding reported only by the older validator needs the user-approved `--accept-validator-change` waiver.

<a id="s11"></a>
## S11 — NN 10: Agent firewall renderer and lore gate hook

- **Ledger node:** agent_firewall
- **Depends on:** S00, S02, S05a
- **Size estimate:** ~22k
- **Red Team resolutions:** R6, R7, R19, R23, R26, R27
- **Replan resolutions:** R54 (Depends on corrected to S00, S02, S05a after S11 finished: hyper_firewall.py has imported hyper_openrig (S02) and hyper_lore (S05a) since S11's own commit 2e4f6ed; the finished MiniPRD_10 block keeps `depends_on: [S00]`, accepted by two Seed deviations rows)

**Goal.** Render .agentignore into enforcement each tool honors, plus protected-write denies, ask rules and a PreToolUse hook for the gated lore verbs, before the skills rely on them.

**Files to create**

- .agents/scripts/hyper_firewall.py
- .agents/scripts/hyper_gate_hook.py
- dev/tests/firewall/test_firewall.py
- dev/tests/firewall/test_gate_hook.py
- dev/tests/fixtures/recorded/S11/

**Files to modify**

- .agentignore
- .claude/settings.json

**Files to delete**

- (none)

**Tasks**

- hyper_firewall.py render, check and merge-settings:
- Claude Read/Edit deny rules (root-anchored)
- protected-write Edit denies
- permissions.ask for the gated hyper_lore verbs in python3 and python forms
- a hooks.PreToolUse entry (matcher Bash) running `python3 "${CLAUDE_PROJECT_DIR}/.agents/scripts/hyper_gate_hook.py"` (invoked through python3, so a missing exec bit cannot turn into a non-blocking error; R6)
- the JSON merge never removes user keys; it removes only manifest-owned rules; invalid JSON aborts
- `check` warns about broad allows (Bash(python3 *), Bash(git *)) and exits 1 on drift
- ignore-file blocks for selected IDEs, labeled best-effort, using the exact `# BEGIN HACF MANAGED BLOCK: <id>` / `# END HACF MANAGED BLOCK: <id>` markers at column 0 (R26)
- skip .clineignore (deprecated); .geminiignore goes under the antigravity id
- hyper_gate_hook.py (stdlib) (R6):
- the whole hook runs inside a catch-all: any exception, unparseable input JSON, or a missing or unknown `permission_mode` exits 2 with a reason (fail closed; never exit 1)
- matcher: tokenize `tool_input.command` with `shlex`, split on `&&`, `||`, `;` and `|`, resolve `cd <dir>` for relative paths, and match invocations of the hyper_lore script or module (path basename `hyper_lore.py`, `python3 -m ...hyper_lore`, `runpy` with a hyper_lore path) whose subcommand (argv[1] of the script) is a gated verb: approve, reject, retire, reopen, or `review --still-valid`. Non-gated verbs (propose, note, stale, validate, show, list) and words inside quoted arguments (titles, commit messages) do not match
- a command that mentions `hyper_lore` but cannot be tokenized gives ask (deny where ask is unavailable)
- by permission_mode: default, acceptEdits, auto: ask, with reason 'Approve <ID> <title>; warrant: <text from --warrant-file>; supersedes/retires/re-anchors: <ids from the pending record>' (R23); dontAsk, bypassPermissions, plan: deny via exit 2 with the reason 'switch to default or acceptEdits (Shift+Tab) and retry' (R7)
- the reason text has control characters (incl. ANSI escapes) stripped and is capped at 500 characters
- on a gated match in an ask mode, write a single-use ticket under .agents/state/lore/tickets/ bound to verb + id + nonce with a 120 s TTL (consumed by hyper_lore.py, S05a)
- Apply it to HACF's .claude/settings.json, adding the HACF-only deny Read(/PRDs/v0.6.0/**).
- Update the .agentignore header; ensure-node agent_firewall.
- Import `_runtime.require_runtime()` in hyper_firewall.py (R19); the hook stays stdlib-only and fails closed if its own imports fail.
- Live test (claude marker, consented; R7, R27): the harness writes verbatim captures (argv, stdout, stderr, exit code, `claude --version`, UTC date) to dev/tests/fixtures/recorded/S11/. Cases: the hook's ask prompts in default mode; `cat spec/archive/x` is denied; Read and Edit deny-rule enforcement in bypassPermissions; and Bash commands whose arguments name a spec/archive/ path, in the shapes `python3 dev/scripts/v060_gate.py finish <SID> --miniprd spec/archive/...` and `python3 .agents/scripts/archive_specs.py --miniprd <path>`, run in default and bypassPermissions against a temp repo with stub scripts (RT-92). Verdicts are computed from the captures and recorded in the audited Operating Notes; the bypassPermissions result feeds AGENTS.md's stated limits in S15a1.

**Tests (deterministic)**

- test_render_claude_rules_golden.
- test_merge_preserves_user_settings.
- test_removes_only_stale_hacf_rules.
- test_ignore_blocks.
- test_invalid_json_aborts.
- test_hook_decisions_by_mode (all spellings, e.g. `cd .agents/scripts && python3 hyper_lore.py approve`).
- test_hook_fails_closed_on_crash: an injected exception, invalid input JSON, a missing permission_mode and an unknown mode each exit 2 (R6).
- test_hook_missing_warrant_file_denies (R6).
- test_hook_evasions_matched: `python3 -c "import runpy,sys; ... runpy.run_path('.agents/scripts/hyper_'+'lore.py' ...)"` (unparseable mention gives ask), `python3 -m hyper_lore approve ...`, and `cd` plus a relative path are caught (R6).
- test_hook_no_overmatch: `hyper_lore.py validate && hyper_ledger.py review-template --json`, `propose --title "Retire the compaction store"`, `note --text "reopen after review"`, `stale --reason "needs review"` and `git commit -m "hyper_lore: review fixes"` are allowed (R6).
- test_hook_strips_control_chars_and_caps_reason (R6).
- test_hook_writes_ticket_on_gated_ask: the ticket binds verb, id and nonce and expires after 120 s (R6).
- test_hook_bypass_reason_says_switch_mode (R7).
- live (claude marker): the cases listed in the live-test task.

**Negative constraints**

- DO NOT write user-level settings.
- DO NOT claim Python/Node subprocess reads are blocked.
- DO NOT let the hook exit with any code other than 0 or 2 (R6).
- DO NOT write or edit a capture under dev/tests/fixtures/recorded/S11/ by hand (R27).

**Risks**

- After R6 the remaining bypasses are a deliberately forged ticket, running the gated verb from a written script without a ticket in a TTY the agent controls, and direct Python writes to lore_hypergraph.yml; the gate is documented as procedural (docs/Lore.md and the render report).

**Pipeline safety.** Denies cover reads that AGENTS.md:75-76 already forbids. archive_specs and the gate run through python3 subprocesses; the RT-92 live cases confirm Bash commands naming spec/archive/ paths are not blocked (R7). Audit Phase 4 already uses archive_specs --miniprd (from S03b on). Bootstrap execute and audit sessions may run in any permission mode; lore approvals need default or acceptEdits (R7).

<a id="s06"></a>
## S06 — NN 11: Gates; hyper-execute and hyper-audit rewritten once; /hyper-lore-approve

- **Ledger node:** pipeline_gates
- **Depends on:** S03b, S04, S05b, S11
- **Size estimate:** ~20k
- **Red Team resolutions:** R1, R6, R7, R19, R21, R23, R27

**Goal.** Edit both skills exactly once (topology, ledger, code graph, lore), with an explicit legacy branch for every step and autonomous stop-and-record.

**Files to create**

- .agents/scripts/hyper_gates.py
- .agents/schemas/autonomy_prompt.md
- .agents/skills/hyper-lore-approve/SKILL.md
- .agents/skills/hyper-lore-approve/META.yml
- .claude/commands/hyper-lore-approve.md
- .windsurf/workflows/hyper-lore-approve.md
- dev/tests/skills/test_execute_audit_contract.py

**Files to modify**

- .agents/skills/hyper-execute/SKILL.md
- .agents/skills/hyper-audit/SKILL.md
- .claude/commands/hyper-execute.md
- .claude/commands/hyper-audit.md
- .windsurf/workflows/hyper-execute.md
- .windsurf/workflows/hyper-audit.md

**Files to delete**

- (none)

**Tasks**

- hyper_gates.py: mode prints autonomous only when HACF_AUTONOMOUS=1 (the engine sets it), else interactive; it does not try to learn Claude Code's permission mode, because the R6 ticket does the enforcement (R7); record writes outbox gates.jsonl; lore-check selects nodes whose provenance.source is this slice or its legacy path, plus stale nodes in the blast radius. Import `_runtime.require_runtime()` (R19).
- Add .agents/schemas/autonomy_prompt.md.
- hyper-execute steps:
1. resolve
2. Memory Check on activeContext plus hacf_status
3. confidence gate (autonomous: record and stop)
4. Haiku orientation
5. implement
6. `hyper_ledger.py ensure-node --from-spec`
7. `hypergraph_updater.py --from-diff <base>` (legacy: the positional ids); a non-empty `unmapped` list is reported in the execute summary (R21)
8. v6: progress executed; legacy: notice
9. notes: v6 to `note --mission`, legacy to `note --legacy`
10. halt and report
Remove the emoji tracking (:59): the tracking document is the MiniPRD's or slice's own task checkboxes, and no progress file is created (R19); use python3 throughout.
- hyper-audit phases:
- Phase 2: novel tests without fixtures record novel_fixture_needed; their candidate outputs go to tests/candidate_outputs/<SID>/ (gitignored, agent-blocked) until the user promotes them into the project's fixtures (R27)
- Phase 3: review-template, sub-agent JSON written to a temp file, apply-review; the patch is applied only through `hyper_ledger.py apply-review` (schema validation and non-regression), never hand-merged (R27). A non-empty `unmapped` list from `--from-diff` is a finding: register a node, or accept the gap in writing in the audit report (R21)
- Phase 3.5: lore gate (request, ask, warrant-file, gated command, or cancel). The AskUserQuestion body lists every id the approval supersedes, retires or re-anchors (R23). The gated command is run as one direct Bash call so the PreToolUse hook can issue its ticket, never through a script, test or make target (R6). When the hook denies because of the permission mode, the skill tells the user to switch to default or acceptEdits (Shift+Tab) and retry (R7)
- Phase 4 v6 interactive: proof with candidate auto, then progress audited
- Phase 4 autonomous (engine): write the verdict only to the outbox audit.json; never write PROOF, PROGRESS or candidate refs, which the engine writes after the phase (R1)
- Phase 4 legacy: `archive_specs.py --miniprd`
- Add hyper-lore-approve SKILL.md and META.yml plus both bridges; reword the execute and audit bridges. /hyper-lore-approve ends by recommending, not making, a lore-only commit (R23).
- If this slice's own audit falls back to the 984ca46 text, it appends a line `Fallback used: hyper-audit@984ca46` to its Operating Notes prose, above the block (R19, Q26).

**Tests (deterministic)**

- test_skill_text_contract: resolve present; no 'mv spec/compiled'; no direct ledger write; no bare `python `; every AskUserQuestion has an autonomous branch.
- test_skill_text_autonomous_audit_verdict_only: the autonomous Phase 4 text writes only audit.json and names no proof or ref command (R1).
- test_skill_text_unmapped_is_finding (R21).
- test_skill_text_novel_outputs_quarantined: Phase 2 names tests/candidate_outputs/<SID>/ (R27).
- test_lore_approve_lists_superseded_and_recommends_commit (R23).
- test_legacy_walkthrough: runs EVERY scripted step of both skills against a flat fixture (resolve, ensure-node from the legacy header, positional updater, note --legacy, lore-check by legacy path, progress/proof skip, archive --miniprd).
- test_gates_mode_and_record.
- test_gates_mode_only_hacf_autonomous: with HACF_AUTONOMOUS unset, mode is interactive regardless of other env; with HACF_AUTONOMOUS=1, autonomous (R7).

**Negative constraints**

- DO NOT drop legacy support.
- DO NOT let autonomous mode approve lore or accept low confidence.
- DO NOT batch approvals.
- DO NOT run a gated lore verb through a script, test, make target or check.sh (R6).

**Risks**

- Skill text is grep-tested only; behavior is proven via the stub executor in S09a2.

**Pipeline safety.** S06 is audited by the new text in legacy mode, which test_legacy_walkthrough covers. Fallback: `git show 984ca46:.agents/skills/hyper-audit/SKILL.md`, recorded with the `Fallback used:` line (R19, Q26). Every later MiniPRD runs through the legacy branch. Bootstrap execute and audit run in any permission mode; a Phase 3.5 approval needs default or acceptEdits (R7). The tracking document for NN 00-11 is the MiniPRD's own checkboxes (R19).

<a id="s07a"></a>
## S07a — NN 12: hyper-architect, hyper-redteam, hyper-resolve rewritten once

- **Ledger node:** spec_pipeline_skills
- **Depends on:** S03b, S04, S05b, S06
- **Size estimate:** ~12k
- **Red Team resolutions:** R14, R17, R23

**Goal.** Resolve compiles missions and slices and registers trade-offs as PROPOSED ADRs. Architect and redteam consult the ledger, code graph and lore.

**Files to create**

- dev/tests/skills/test_spec_pipeline_contract.py

**Files to modify**

- .agents/skills/hyper-architect/SKILL.md
- .agents/skills/hyper-redteam/SKILL.md
- .agents/skills/hyper-resolve/SKILL.md
- .claude/commands/hyper-resolve.md
- .windsurf/workflows/hyper-resolve.md

**Files to delete**

- (none)

**Tasks**

- architect: draft frontmatter; impact via hyper_ledger plus the code-graph search; constraints are CANON nodes, stale CANON nodes (binding until retired or superseded) and PROPOSED nodes with `origin: user-decision` (binding until rejected) (R23); Pacing Loop kept.
- redteam: cite CANON, REJECTED and ledger ids; treat stale CANON and PROPOSED user-decision nodes as binding (R23).
- resolve Phase 4:
- `hyper_scope mission create --from-draft`, then one `slice create` per MiniPRD
- `lint --mission` must pass
- `hyper_lore.py propose` for trade-offs
- batch compilation (at most 5 MiniPRDs per response); for large missions, mediation and the SuperPRD in one session and each batch in a fresh session that reads only the SuperPRD, the annex and the ledger (R17)
- `python3 archive_specs.py`
- write flat legacy output only as a collision fallback (foreign root items), with a warning
- AGENTS.md schema instead of the .agent template path (:36)
- resolve replan mode (R14): inputs are the SuperPRD trade-off log, the annex and the failure record of the stopped slice; it may change the annex only for unfinished slices, logs each change as an R-row, and recompiles only unfinished MiniPRDs or slices.
- Update the resolve bridges.

**Tests (deterministic)**

- test_spec_pipeline_contract: no bare SuperPRD.md target; no .agent/schemas path; scope and propose present; Pacing Loop kept; no bare `python `.
- test_resolve_replan_mode_text: the replan section names the three inputs and forbids changes to finished slices (R14).
- test_architect_redteam_binding_text: both skills name stale CANON and PROPOSED user-decision nodes as binding (R23).

**Negative constraints**

- DO NOT let resolve approve lore.
- DO NOT let a replan change a finished slice (R14).

**Risks**

- Schema drift is caught by lint.

**Pipeline safety.** The v0.6.0 MiniPRDs were compiled with the old text. The bootstrap re-runs resolve only for a replan (R14): before NN 12 with the v0.5.9 text via `git show 984ca46:.agents/skills/hyper-resolve/SKILL.md`, after this slice with the rewritten skill's replan mode.

<a id="s07b"></a>
## S07b — NN 13: hyper-discover, hyper-baseline, hyper-grill-docs, hyper-status rewritten once

- **Ledger node:** discovery_skills
- **Depends on:** S03b, S04, S05b, S06
- **Size estimate:** ~14k
- **Red Team resolutions:** R21, R23

**Goal.** Discovery driven by the code graph and written through scripts; the baseline becomes the root SPEC.md; ADRs go into lore; status reads the new stores.

**Files to create**

- dev/tests/skills/test_discovery_contract.py

**Files to modify**

- .agents/skills/hyper-discover/SKILL.md
- .agents/skills/hyper-baseline/SKILL.md
- .agents/skills/hyper-grill-docs/SKILL.md
- .agents/skills/hyper-grill-docs/ADR-FORMAT.md
- .agents/skills/hyper-grill-docs/META.yml
- .agents/skills/hyper-status/SKILL.md
- .claude/commands/hyper-discover.md
- .claude/commands/hyper-baseline.md
- .windsurf/workflows/hyper-discover.md
- .windsurf/workflows/hyper-baseline.md

**Files to delete**

- (none)

**Tasks**

- discover: when no ledger exists, first run `python3 .agents/scripts/hyper_ledger.py init` (R21); index; ensure-node with files: and `--parent` (member_of for Layer or System parents; R21); structural edges via set-edges / --depends-on from the code graph; backend none means llm-read provenance and needs_review.
- baseline: root SPEC.md via `hyper_scope project init|update`; stop on a foreign file.
- grill-docs SKILL.md, ADR-FORMAT.md, META.yml: `hyper_lore.py propose`; model id.
- status: SPEC.md, MAP.md, hyper_spec status, hyper_lore list; stale CANON shown as binding (R23).
- Update the discover and baseline bridges.

**Tests (deterministic)**

- test_discovery_contract: no docs/adr writes; no memory-template reads; no hand-traced edges with a backend.
- test_discover_calls_ledger_init_when_missing (R21).

**Negative constraints**

- DO NOT overwrite a foreign SPEC.md.

**Risks**

- Edge granularity; aggregate edges through files: globs.

**Pipeline safety.** Not used in the build loop.

<a id="s08"></a>
## S08 — NN 14: Pluggable headless executors with Claude sandbox; current model ids

- **Ledger node:** headless_executors
- **Depends on:** S00
- **Size estimate:** ~24k
- **Red Team resolutions:** R1, R2, R4, R19, R20, R27

**Goal.** The executor layer: verified Claude argv with the OS sandbox, experimental codex and gemini, the seat seam, a stub, and model ids.

**Files to create**

- .agents/scripts/hyper_executors.py
- .agents/config/orchestration.yml
- dev/tests/fixtures/stub_executor.py
- dev/tests/engine/test_executors.py
- dev/tests/fixtures/recorded/S08/

**Files to modify**

- .agents/config/heuristic_rules.yaml
- .agents/scripts/model_router.py
- .agents/schemas/META_template.md
- .agents/scripts/token_budget_checker.py
- .agents/scripts/token_budget_reconciler.py

**Files to delete**

- (none)

**Tasks**

- hyper_executors.py:
- Executor protocol
- generated --settings JSON (sandbox required, failIfUnavailable) and the empty strict MCP config. Sandbox write allowlist: the worktree (cwd) and tmp only, never the shared `.git`; `denyWrite` on the worktree's `.git` pointer file (R1). Hooks in the generated settings use absolute paths to base-revision copies of HACF scripts supplied in ctx by the engine (R4).
- `--setting-sources user` (no `project`), so the executor never loads the worktree's `.claude/settings*.json`; docs state that user-level hooks still run (R4)
- element-wise argv; env scrub with passthrough lists and prefixes. Passthrough only for the auth method in use (R20): `AWS_*` only with `CLAUDE_CODE_USE_BEDROCK=1`, `GOOGLE_*`/`CLOUD_ML_*` only with `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_API_KEY` only for API-key auth
- Popen(start_new_session=True); on timeout SIGTERM to the group, then SIGKILL after kill_grace_s
- parsers
- codex and gemini stay experimental and run only with CLI `--allow-unsandboxed` (never from tracked config), printing a warning; `sandbox: required` applies to every executor (R20)
- Import `_runtime.require_runtime()` (R19).
- Add .agents/config/orchestration.yml (R2, R4, R20):
- `oracle.sandbox: bwrap` by default (Linux); profile: read-only root, read-write worktree and tmp, `$HOME` masked except read-only binds the oracle needs (interpreter realpath, tool caches), `.git` read-only, `--unshare-net`, `--die-with-parent`; `none` is accepted only from the CLI flag `--allow-unsandboxed-oracle`, never from config
- `oracle.prepare: [['uv', 'sync', '--frozen']]` run engine-side from the base lockfile; oracle commands run as `uv run --frozen --no-sync ...`; `oracle.allowed_executables` is documented as hygiene, not isolation
- `autonomy.max_budget_usd_per_run: 50.0` next to `max_budget_usd_per_phase`
- `executors.claude-cli.verified: false` until the live test passes; `max_tested_version` is set to the `claude --version` the live test passed on (Q18)
- security keys (`executors.*.sandbox`, `oracle.sandbox`, `oracle.allowed_executables`, `autonomy.env_passthrough*`, the budget keys, `scope_policy`) are marked in the schema as shipped-default-or-CLI only; tracked `.agents/local/hacf.yml` and `.agents/config/**` overrides may only tighten them (enforced by the S09a1 loader)
- Model ids in heuristic_rules.yaml, model_router.py and META_template.md.
- token_budget_checker and token_budget_reconciler accept slice ids and paths.
- stub_executor.py scenarios: success, low_confidence, oracle_fail, audit_fail_then_pass, timeout, budget_exceeded, scope_violation, rate_limited, ref_move, hook_reject, git_meta_write (writes the worktree `.git` file and `<common>/worktrees/<other>/HEAD`), credential_leak, dirty_after_audit, control_path_edit.
- Live test captures (R27): the harness writes argv, stdout, stderr, exit code, `claude --version` and UTC date for each case to dev/tests/fixtures/recorded/S08/; verdicts come from the captures and set `verified` and `max_tested_version`.
- ensure-node headless_executors.

**Tests (deterministic)**

- test_claude_argv_golden (prompt reads SKILL.md, --permission-prompts none, --setting-sources user) (R4).
- test_settings_sandbox_json: the write allowlist excludes the shared `.git` and `denyWrite` names the worktree's `.git` file (R1).
- test_settings_hooks_absolute_base_paths (R4).
- test_parse_claude_json subtypes.
- test_env_passthrough_auth_vars: `AWS_*` passes only with Bedrock, `GOOGLE_*` only with Vertex, `ANTHROPIC_API_KEY` only for API-key auth (R20).
- test_experimental_executors_need_flag (R20).
- test_orchestration_security_keys_marked: the schema lists every security key as shipped-default-or-CLI only (R4).
- test_timeout_kills_process_group.
- test_stale_model_ids_absent.
- live (claude, HACF_LIVE_CLAUDE=1, consent, $0.25):
- JSON parses
- a Bash write outside the worktree fails in the sandbox
- a sandboxed Bash write to the worktree's `.git` file fails, and a write under the git common dir fails (R1)
- whether a PreToolUse hook runs inside the sandbox or with the user's privileges, recorded as a finding (R1, RT-67a)
- `curl` is blocked
- an in-worktree Edit succeeds
- AskUserQuestion is unavailable
- the Agent tool works
- `uv run pytest` works offline

**Negative constraints**

- DO NOT call the Anthropic API directly.
- DO NOT use bypassPermissions, --bare or slash-command prompts.
- DO NOT build shell strings.
- DO NOT give agent phases write access to any git metadata (R1).
- DO NOT read a security key from tracked config except to tighten it (R4).
- DO NOT write or edit a capture under dev/tests/fixtures/recorded/S08/ by hand (R27).

**Risks**

- CLI churn: min and max tested versions plus the live test. A declined live test leaves `verified: false`, and the engine then refuses to run without `--unverified-executor` (R20).

**Pipeline safety.** Config values only; no skill changes. model_router.py and the token-budget scripts were normalized in S00 (R13), so this slice's diff shows logic only.

<a id="s09a1"></a>
## S09a1 — NN 15: Engine core: failure taxonomy, run state, DAG, scheduler

- **Ledger node:** workflow_engine
- **Depends on:** S03b, S08
- **Size estimate:** ~22k
- **Red Team resolutions:** R2, R4, R5, R19, R20, R21, R22

**Goal.** A deterministic scheduler and persisted run state, testable without git worktrees.

**Files to create**

- .agents/scripts/hyper_engine.py
- .agents/scripts/hyper_failures.py
- dev/tests/engine/test_engine_dag.py

**Files to modify**

- (none)

**Files to delete**

- (none)

**Tasks**

- hyper_failures.py: enum including commit_rejected, sandbox_unavailable and ref_violation; classification; retry policy. `rate_limited` triggers a global pause of every worker (backoff 30 s, then 120 s), not a per-slice retry (R20).
- hyper_engine.py core:
- preflight (executor, lint, validate, DAG). A missing ledger fails with a message naming `python3 .agents/scripts/hyper_ledger.py init` and `/hyper-discover` (R21). With `oracle.sandbox: bwrap`, bwrap must be available, else `sandbox_unavailable` (fatal) unless `--allow-unsandboxed-oracle` is passed; on macOS oracles are refused without that flag (R2). `executors.claude-cli.verified: false` stops the run unless `--unverified-executor` is passed, recorded in run.json (R20). codex or gemini requires `--allow-unsandboxed` (R20).
- config loader (R4): security keys come only from shipped defaults or CLI flags; a tracked `.agents/local/hacf.yml` or `.agents/config/**` override may only tighten them, and a loosening override is ignored with a warning.
- run_id and state files; run.json records the sha256 of the base ledger (R5); --resume
- run budget (R20): `autonomy.max_budget_usd_per_run` (default 50.0, CLI-overridable) stops scheduling and records `budget_exceeded` for the run
- ready set; max_parallel; serialize on touches or node_id overlap
- legacy NN chain
- engine.lock under `$(git rev-parse --git-common-dir)/hacf/locks/` (R22)
- Timestamps via `_runtime`; import `_runtime.require_runtime()` (R19).

**Tests (deterministic)**

- test_dag_diamond_order.
- test_overlap_serialized.
- test_gate_stop_upstream_blocked.
- test_resume_skips_audited.
- test_failure_classification.
- test_rate_limited_pauses_all_workers (R20).
- test_run_budget_stops_scheduling (R20).
- test_preflight_missing_ledger_names_init (R21).
- test_preflight_unverified_executor_refused (R20).
- test_preflight_bwrap_missing_is_fatal (R2).
- test_config_tracked_override_cannot_loosen: `oracle.sandbox: none` in hacf.yml is ignored with a warning; a stricter budget is honored (R4).
- test_run_json_records_base_ledger_sha (R5).

**Negative constraints**

- DO NOT import anthropic.

**Risks**

- (none)

**Pipeline safety.** New files; the engine is never run in the bootstrap.

<a id="s09a2"></a>
## S09a2 — NN 16: Engine worktrees, ref guard, oracle, integrator, staged ledger, land

- **Ledger node:** workflow_engine
- **Depends on:** S09a1, S01c, S05a, S06
- **Size estimate:** ~30k
- **Red Team resolutions:** R1, R2, R3, R4, R5, R17, R20
- **Replan resolutions:** R40 (uv without a cache in the oracle and agent envs; oracle binds), R41 (stub refused without `HACF_TEST=1`), R42 (S09a1 audit notes F2 and F4), R43 (this slice commits the STORY-0003 stop record)

**Goal.** One worktree per slice, the engine commits, a serialized outbox integrator, a ref guard, and an explicit land step.

**Files to create**

- dev/tests/engine/test_engine_worktrees.py
- dev/tests/engine/test_engine_integration.py
- dev/tests/engine/test_engine_guard.py
- dev/tests/engine/test_engine_land.py
- lore/STORIES/STORY-0003-*.md (written by the stop for the Replan; committed by this slice's finish, R43)

**Files to modify**

- .agents/scripts/hyper_engine.py
- .agents/scripts/hyper_executors.py (R40: `scrub_env` adds `UV_NO_CACHE=1` with ctx; DEFAULTS follow orchestration.yml)
- .agents/config/orchestration.yml (R40: `autonomy.env` gains `UV_NO_CACHE`; R42: `security_keys` gains the new keys; the oracle comments follow)
- lore_hypergraph.yml (R43: STORY-0003 node only)
- lore/MAP.md (R43: STORY-0003 entry only)

**Files to delete**

- (none)

**Tasks**

- WorktreeManager (`git worktree add -b swimlane/hacf/<run>/<slice>` from the integration tip; never a checkout in the main tree).
- Engine-only git writes (R1): agent phases have no write access to git metadata (S08 settings). The engine performs every git write itself (the step g commit, the post-audit proof commit, candidate refs) with explicit `GIT_DIR` and `GIT_WORK_TREE` for the target worktree.
- Metadata guard (R1): before and after every agent phase, and after steps g (commit), h (oracle) and i (audit), take a snapshot of `git for-each-ref` plus sha256 of the worktree's `.git` pointer file, every file under `<common>/worktrees/`, `<common>/info/*`, `<common>/objects/info/*`, `<common>/shallow`, `<common>/packed-refs` and every HEAD, ORIG_HEAD and MERGE_HEAD file. Any difference outside the engine's own expected ref update is `ref_violation`: restore, block the slice, keep the worktree. An `objects/info/alternates` file is refused outright.
- Control paths (R4): `.agents/skills/**`, `.agents/scripts/**`, `.agents/config/**`, `.agents/local/hacf.yml`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/**`, `.mcp.json`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json` and other lockfiles are always protected: a change not listed in the slice's `hacf_touches` is `scope_violation`, whatever `scope_policy` says. Before each agent phase the engine copies the skill text, AGENTS.md and the HACF hook scripts from the integration base into a read-only run dir; the prompt points there and the generated settings' hooks use those absolute paths.
- Outbox (R20): `HACF_OUTBOX=<worktree>/.hacf-outbox/`; after each phase the engine moves it into `.agents/state/runs/<id>/outbox/<slice>/` before the scope check, exempts that path from the check, and refuses entry types outside the whitelist (ledger ops, lore propose/note/stale, spec progress; approval entries rejected).
- Credential scan (R20): before integration, the slice diff is scanned for credential shapes (AWS access keys, PEM private-key headers, `sk-ant-`, GitHub tokens); a hit is `scope_violation`.
- Engine commit with trailers (R3): `git -c core.hooksPath=<empty temp dir> commit --no-verify`. When the repo has pre-commit or commit-msg hooks (`core.hooksPath` or `.git/hooks`), the engine first runs the base revision's copies of those hooks inside the bwrap oracle profile; a failing hook becomes commit_rejected.
- Oracle (R2, R40): prepare engine-side from the base lockfile (`uv sync --frozen`), then argv with allowed executables inside bwrap (read-only root, read-write worktree and tmp, `$HOME` masked except read-only binds for the real path of argv[0] and, when the worktree has `.venv/pyvenv.cfg`, the parent of its `home` directory; no uv cache bind; `.git` read-only, `--unshare-net`, `--die-with-parent`); Python oracles run as `uv run --frozen --no-sync ...`; the oracle env is scrubbed like the executor's (PATH, LANG, TMPDIR, masked HOME plus the auth passthrough list; R20) plus `UV_NO_CACHE=1` (R40). `none` only with `--allow-unsandboxed-oracle`.
- Agent env (R40): `hyper_executors.scrub_env` adds `UV_NO_CACHE=1` with ctx, next to `HACF_AUTONOMOUS`; orchestration.yml `autonomy.env` and both embedded defaults name it.
- Stub refusal (R41): preflight refuses a `status: test-only` executor unless the environment has `HACF_TEST=1`, whatever `--unverified-executor` says. The slice's stub-based tests set `HACF_TEST=1`.
- Tighten-only config (R42, S09a1 audit note F2): `security_keys` (orchestration.yml and the engine DEFAULTS) gains `executors.*.argv`, `executors.*.preflight`, `executors.*.prompts` (only the shipped value), `protected_paths` (additions only), `git.ref_guard` and `autonomy.credential_scan` (only true); a loosening tracked value is ignored with a warning. `oracle.prepare` stays configurable.
- Resume record (R42, S09a1 audit note F4): each `--resume` records its resolved security values in its run.json `resumed[]` entry.
- STORY-0003 (R43): the finish commits the stop record's three lore paths. Before the audit's `--from-diff`, copy them aside and restore those copies afterwards, never HEAD's, which lacks STORY-0003.
- After the audit phase (R4): a dirty worktree (outbox excepted) is refused; the engine stages only the named PROGRESS, PROOF and proof files and writes the candidate ref itself.
- Integrator: merge --no-ff in the integration worktree; apply ledger, lore and spec outboxes; validate; roll back inside that worktree only.
- Untracked ledger staged in .agents/state/runs/<id>/ledger/ and applied only by `hyper_engine.py land <run> [--merge]`.
- land and --resume (R5): refuse (exit 5, report) when the main checkout's ledger sha256 differs from the base recorded in run.json. `land --rebase-ledger` replays the run's recorded ledger outbox entries (ensure-node, set-edges, apply-review patches) onto the current ledger through `hyper_ledger.py apply-outbox` with validation; an entry touching a node whose content changed since base is a conflict, and the land is refused with a per-node report. `ledger.lock` is held during land.
- gc (R20): `hyper_engine.py gc [--older-than 14d] [--apply]` lists (default) or removes kept worktrees (then `git worktree prune`), `swimlane/hacf/<run>/*` branches, run dirs and `refs/hacf/candidates/*` of landed or abandoned runs; never a run holding engine.lock.

**Tests (deterministic)**

- test_worktree_lifecycle.
- test_ref_guard_restores.
- test_guard_detects_pointer_file_rewrite: the stub rewrites the worktree's `.git` file to point at the main repo; the slice is `ref_violation` and the main index and branch are unchanged (R1).
- test_guard_detects_sibling_head_write: a write to `<common>/worktrees/<other>/HEAD` is `ref_violation` (R1).
- test_guard_detects_info_exclude_and_attributes (R1).
- test_guard_refuses_alternates (R1).
- test_engine_git_uses_explicit_git_dir (R1).
- test_scope_violation_protected_paths.
- test_control_path_edit_is_scope_violation_under_warn: an edit to `.agents/skills/hyper-audit/SKILL.md` outside touches is refused with `scope_policy: warn` (R4).
- test_audit_reads_base_revision_copies (R4).
- test_dirty_worktree_after_audit_refused (R4).
- test_outbox_moved_and_whitelisted (R20).
- test_credential_shape_is_scope_violation (R20).
- test_engine_commit_hooks_disabled_and_base_hooks_sandboxed: an executor edit to `.githooks/pre-commit` has no effect; the base hook runs in bwrap; a failing base hook is commit_rejected (R3).
- test_oracle_runs_in_bwrap_without_network_or_home (R2).
- test_oracle_none_requires_cli_flag (R2).
- test_oracle_fail_retry.
- test_merge_conflict_keeps_worktree.
- test_ledger_invalid_rollback.
- test_commit_rejected_by_hook.
- test_untracked_ledger_staged_until_land.
- test_land_refuses_ledger_drift (R5).
- test_land_rebase_ledger_replays_outbox: a manual change to an untouched node survives; a change to a node the run also changed is a conflict (R5).
- test_resume_refuses_ledger_drift (R5).
- test_gc_lists_then_applies_and_skips_running (R20).
- test_main_checkout_byte_identical.
- test_oracle_uv_run_inside_bwrap: the oracle argv and env have `UV_NO_CACHE=1`, no uv cache bind, and read-only binds for argv[0]'s real path and the venv interpreter's prefix; where bwrap and uv work, a `uv run --frozen --no-sync` probe in a temp project exits 0 inside the profile (R40).
- test_agent_env_sets_uv_no_cache: `scrub_env` with ctx sets `UV_NO_CACHE=1`; without ctx it does not (R40).
- test_stub_executor_refused_outside_tests: without `HACF_TEST=1`, preflight refuses the stub even with `--unverified-executor`; with it, the stub runs (R41).
- test_tracked_config_cannot_loosen_r42_keys: tracked `executors.claude-cli.argv`, `preflight` and `prompts` changes, a shorter `protected_paths`, `git.ref_guard: false` and `autonomy.credential_scan: false` are ignored with a warning; an added protected path is kept (R42).
- test_resume_records_security_values: a resumed run appends its resolved security values to `resumed[]` and keeps the first run's `security` (R42).

**Negative constraints**

- DO NOT push, or touch user branches, before land.
- DO NOT let an agent phase write git metadata; DO NOT run any git command against the main checkout without explicit `GIT_DIR`/`GIT_WORK_TREE` (R1).
- DO NOT run repo hooks or oracles with full privileges unless the user passed `--allow-unsandboxed-oracle` (R2, R3).
- DO NOT apply a staged ledger over a drifted main-checkout ledger (R5).

**Risks**

- bwrap is Linux-only; on macOS oracles and repo hooks need `--allow-unsandboxed-oracle` (stated limit; R2).
- User-level hooks still run in executor sessions through `--setting-sources user` (stated limit; R4).

**Pipeline safety.** Never run in the bootstrap. Large slice (R17): the task checkboxes are the resume state; after any compaction, re-read the MiniPRD before continuing.

<a id="s09b"></a>
## S09b — NN 17: Orchestrator and daemon as thin wrappers; retire hyper_fix; conflict tooling

- **Ledger node:** hyper_orchestrator (existing; also hyper_daemon, hyper_resolve_conflict; removes hyper_fix)
- **Depends on:** S09a2, S05b, S01a
- **Size estimate:** ~25k
- **Red Team resolutions:** R13, R19, R21

**Goal.** Replace the broken internals with hyper_engine and remove the unsafe paths.

**Files to create**

- dev/tests/engine/test_orchestrator_cli.py

**Files to modify**

- .agents/scripts/hyper_orchestrator.py
- .agents/scripts/hyper_daemon.py
- .agents/scripts/hyper_resolve_conflict.py
- .agents/scripts/semantic_graph_merger.py

**Files to delete**

- .agents/scripts/hyper_fix.py
- .provenance_staging/.gitkeep

**Tasks**

- hyper_orchestrator.py: CLI over the engine. Delete MODEL_ALLOWLIST (:40-46), messages.create (:584), the checkouts (:570-571, :678, :829-860), name-fragment pairing (:874-911), the OR routing (:691-695) and provenance staging (:53, :481, :772).
- hyper_daemon.py: single-slice runner; remove the calls to the missing runners (:344, :357).
- Delete hyper_fix.py (unbounded `repo_root / rel_path` write, :334-366). Then `set-edges --id hyper_daemon --remove-depends-on hyper_fix`, then `remove-node hyper_fix --reason D8 --slice S09b`, which records the tombstone in `removed_nodes` (R21).
- hyper_resolve_conflict: model from config; rotation via archive_specs --paths. semantic_graph_merger: rotation via archive_specs (:184).
- git rm .provenance_staging/.gitkeep.
- Import `_runtime.require_runtime()` in the three modified entry points (R19).

**Tests (deterministic)**

- test_orchestrator_dry_run.
- test_daemon_stub.
- test_no_refs_to_hyper_fix_in_code (.agents/, .claude/, .windsurf/, installers; docs are handled in S15a2).
- test_remove_node_sequence: ends with a `removed_nodes` tombstone for hyper_fix (R21).
- test_lore_validate_warns_on_tombstoned_hyper_fix_anchor: ADR-0006's anchor to hyper_fix is a stale warning and validate exits 0 (R21).

**Negative constraints**

- DO NOT keep any Anthropic SDK path in the engine.
- DO NOT edit lore anchors in this slice; the tombstoned anchor stays a warning (R21).

**Risks**

- Users who scripted hyper_fix lose it; the CHANGELOG says so.

**Pipeline safety.** The manual loop never invokes these scripts. The four modified scripts were normalized in S00 (R13), so this slice's diff shows logic only. `hyper_lore.py validate` treats the hyper_fix anchor as a stale warning (R21), so finish is not blocked.

<a id="s10"></a>
## S10 — NN 18: Retire Final Truth, dead memory templates, handoffs rule doc

- **Ledger node:** context_compaction (removed)
- **Depends on:** S06, S07b
- **Size estimate:** ~8k
- **Red Team resolutions:** R21

**Goal.** Remove the unwired stores (D9). Code only; the docs come in S15.

**Files to create**

- .agents/schemas/activeContext_template.md

**Files to modify**

- .agents/rules/git-workflow.md

**Files to delete**

- .agents/scripts/compaction_engine.py
- .agents/scripts/interrupt_detector.py
- .agents/schemas/final_truth_template.md
- .agents/memory/productContext.md
- .agents/memory/systemPatterns.md

**Tasks**

- git rm compaction_engine.py, interrupt_detector.py and final_truth_template.md; `remove-node context_compaction --reason D9 --slice S10` (no inbound edges), which records the tombstone in `removed_nodes` (R21).
- git rm productContext.md and systemPatterns.md; add activeContext_template.md.
- Remove spec/handoffs from .agents/rules/git-workflow.md (pre-commit was done in S00).

**Tests (deterministic)**

- grep in code paths finds no references.
- ledger validate passes.
- test_context_compaction_tombstoned: `removed_nodes` lists context_compaction with reason D9, and `hyper_lore.py validate` reports ADR-0007's anchor as a stale warning and exits 0 (R21).

**Negative constraints**

- DO NOT delete activeContext.md.

**Risks**

- (none)

**Pipeline safety.** Nothing invokes the deleted code; hyper-status stopped reading the templates in S07b. Lore anchors are not edited here; the tombstoned anchor is a warning (R21).

<a id="s12a"></a>
## S12a — NN 19: Scaffold and managed-block engine

- **Ledger node:** root_scaffold
- **Depends on:** S02, S03b, S05b, S11
- **Size estimate:** ~22k
- **Red Team resolutions:** R8, R9, R10, R19, R22, R26
- **Replan resolutions:** R39

**Goal.** Idempotent scaffolding with collision detection, and a strict block engine that preserves OpenRig blocks.

**Files to create**

- .agents/scripts/hyper_blocks.py
- .agents/scripts/hyper_scaffold.py
- dev/tests/fixtures/build_user_project_v059.py
- dev/tests/scaffold/test_scaffold.py
- dev/tests/scaffold/test_blocks.py

**Files to modify**

- .agents/scripts/hyper_scope.py (R39: S03b audit notes F1 and F2 only)
- dev/tests/scope/test_scope.py (R39: the two tests below)

**Files to delete**

- (none)

**Tasks**

- hyper_blocks.py (R26): exact, case-sensitive markers `<!-- BEGIN HACF MANAGED BLOCK: <id> -->` / `<!-- END HACF MANAGED BLOCK: <id> -->` in Markdown, `# BEGIN HACF MANAGED BLOCK: <id>` / `# END HACF MANAGED BLOCK: <id>` in hash-comment files (`.gitignore`, `.agentignore`, `info/attributes`), and `HACF GENERATED: <id>` regions; `<id>` matches `^[a-z][a-z0-9-]{0,63}$`; never a prefix or case-folded match, so `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` and RIGGED blocks are byte-preserved. Markers count only at column 0 and outside fenced code blocks. The file's EOL style (LF/CRLF), UTF-8 BOM and final-newline state are preserved. Duplicate, nested or unbalanced HACF markers give exit 5 with nothing written. A user-edited HACF block (sha differs from the manifest) is backed up and asked about when interactive, and left in place and reported under `-y`. `CLAUDE.local.md` is never touched. Writes use compare-and-swap (sha at read, re-read before `os.replace`, exit 5 on mismatch), because OpenRig rewrites its own blocks in the same files (R22). v0.5.x template sha list.
- Whole-file instruction decision (R9): `hyper_blocks.py` exposes one function used by the installer, migration and update for AGENTS.md, CLAUDE.md and GEMINI.md with no HACF markers. A sha in `legacy_file_hashes.json` (unmodified v0.5.x template): replace the file with the HACF block. An unknown sha: never append. Interactive (via `/dev/tty`): offer (a) replace with the v0.6.0 HACF block, saving `<file>.v059.bak`, or (b) leave the file and write the block to `<STEM>.hacf-v0.6.0.md` with merge instructions. `-y` or non-TTY: (b) plus a report line.
- hyper_scaffold.py check|apply: project init, lore (including an empty lore/canon/ dir; R8), local hacf.yml, state .gitignore, activeContext if missing, spec/ and tests/ dirs, the .gitignore block, repo-local merge drivers. The merge-driver attributes go into a `# BEGIN HACF MANAGED BLOCK: merge-drivers` block in `$(git rev-parse --git-common-dir)/info/attributes`, never a tracked `.gitattributes` (R10).
- Import `_runtime.require_runtime()` in both new entry points (R19).
- hyper_scope.py, S03b's audit notes (R39): F1, `project init --check-only` classifies a project.yaml whose `missions.root` lies outside the workspace as foreign (exit 5, nothing written), the same refusal `hyper_openrig.missions_root()` makes, so check and init agree and the scaffold and installer take the collision path; F2, `project init` renders every file it writes before its first mkdir, so a render failure leaves nothing to roll back. Change nothing else in hyper_scope.py.
- Fixture builder build_user_project_v059.py: flat specs, archived MiniPRDs, broken ledger, docs/adr including multi-context, filled memory, an AGENTS.md with an OpenRig block, a customized (unknown-sha) whole-file AGENTS.md and CLAUDE.md variant (R9), custom settings, collision variants, OpenRig-projected .agents/skills/<id> without HACF, a saved v0.5.x HACF-uninstall.sh, and a user `.gitattributes` with Git LFS rules (R10).

**Tests (deterministic)**

- test_collision_matrix (exit 5, nothing written).
- test_openrig_adopted.
- test_idempotent.
- test_blocks_preserve_openrig.
- test_v059_template_detection.
- test_blocks_crlf_preserved (R26).
- test_blocks_bom_preserved (R26).
- test_blocks_missing_final_newline_preserved (R26).
- test_blocks_marker_in_fenced_code_ignored (R26).
- test_blocks_indented_marker_ignored (R26).
- test_blocks_duplicate_refused (R26).
- test_blocks_nested_in_openrig_refused (R26).
- test_blocks_unbalanced_refused (R26).
- test_blocks_case_and_prefix_not_matched: a lowercase `hacf` marker and `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` are never treated as HACF blocks (R26).
- test_blocks_user_edited_block_backup_and_report (R26).
- test_claude_local_md_untouched (R26).
- test_blocks_cas_refuses_concurrent_change (R22).
- Each block test asserts the bytes outside the block are identical (R26).
- test_unknown_sha_whole_file_never_appended: interactive replace writes `.v059.bak`; `-y` writes `AGENTS.hacf-v0.6.0.md` and leaves AGENTS.md byte-identical (R9).
- test_scaffold_writes_info_attributes_not_gitattributes: a user `.gitattributes` with LFS rules is byte-identical afterwards (R10).
- test_out_of_workspace_missions_root_foreign: a project.yaml with `missions.root: ../elsewhere` makes `project init --check-only --json` report it foreign and exit 5, and `project init` exit 5 with the tree unchanged (R39, F1).
- test_project_init_renders_before_mkdir: a render failure of SPEC.md leaves no `missions/` directory and no other new path (R39, F2).

**Negative constraints**

- DO NOT overwrite or edit outside HACF blocks.
- DO NOT append an HACF block to an unknown-sha whole-file AGENTS.md, CLAUDE.md or GEMINI.md (R9).
- DO NOT write or modify a tracked `.gitattributes` (R10).
- DO NOT touch `CLAUDE.local.md` (R26).

**Risks**

- (none)

**Pipeline safety.** Called only by the installer, migration and tests.

<a id="s12b"></a>
## S12b — NN 20: Installer: consent-first, merge-not-clobber, manifest-driven

- **Ledger node:** installer
- **Depends on:** S12a, S04, S11
- **Size estimate:** ~30k
- **Red Team resolutions:** R9, R10, R12, R17, R24, R25, R26

**Goal.** A TTY-safe installer that downloads nothing third-party under -y, aborts before writing if PyYAML is missing, merges settings and MCP entries, and writes the manifest.

**Files to create**

- .agents/install-templates/claude-settings.fragment.json
- dev/tests/installer/test_install.py
- dev/tests/test_never_copy.py

**Files to modify**

- HACF-install.sh

**Files to delete**

- (none)

**Tasks**

- Flags: --install-deps, --code-graph=, --install-code-graph, --firewall=, --migrate, --statusline, --dry-run, --repo-url, --branch (R12).
- Upstream overrides (R12): the repository URL and branch come only from `--repo-url` and `--branch`; env `HACF_REPO_URL` and `HACF_BRANCH` are honored only when `HACF_TEST=1` (E2E tests with a local bare clone). A non-`https://` URL is refused unless `HACF_TEST=1`. Every consent prompt and log line prints the effective URL and the commit sha being installed.
- Platform guard (R24): refuse bash < 4 (`BASH_VERSINFO`) with a Homebrew hint; supported: Linux (Ubuntu 22.04+, Debian 12+) and WSL2; macOS best-effort. Hashing in shipped code uses Python `hashlib`, never `sha256sum`.
- prompt_consent(): reads the answer from `/dev/tty` when it exists and is readable, else NO (R24); NO under -y or without a TTY unless the specific flag is given; all network and third-party actions use it.
- Preflight, before any write: git, python3>=3.10, `yaml.__version__`>=5.4 (the same rule as `_runtime.py`; R24), with per-OS guidance (`uv python install`, Homebrew, deadsnakes). Missing deps give consented `python3 -m pip install --user pyyaml` (PEP 668 aware: abort before any write with uv and venv instructions; Q34) or abort.
- Upgrade detection by .agents/hacf-manifest.json or .agents/scripts/hypergraph_updater.py, not by `-d .agents` (:202).
- Honor --ides under -y (fix :295-301).
- Replace CORE_DIRS tests (:89, :409-432) with the scaffold.
- claude IDE entry: .claude/commands only; settings via the fragment merge.
- Blocks for AGENTS.md, CLAUDE.md and GEMINI.md through hyper_blocks (R26 grammar). Upgrade of a whole-file v0.5.x instruction file uses the S12a decision function: a known sha is replaced by the block; an unknown sha is never appended to (interactive: replace with `.v059.bak` or sidecar; `-y` or non-TTY: sidecar `<STEM>.hacf-v0.6.0.md` plus a report line) (R9).
- The installer never writes a `.gitattributes`; merge-driver attributes go to the git common dir's info/attributes through the scaffold (R10).
- MCP merge for claude (.mcp.json), cursor (.cursor/mcp.json with type stdio) and roo (.roo/mcp.json); print steps for the others. The install report states that these tracked files make teammates who accept the server run `uvx codebase-memory-mcp@0.12.1` (R24).
- Manifest; NEVER_COPY. `.agents/hacf-manifest.json` records the installed upstream commit (`installed_commit`) for the update's anti-rollback check (R25).
- After an upgrade from a partial or saved-installer state, missing `.agents/config/*` files are installed and the report says the upgrade is complete; scripts run on embedded defaults until then (R24).
- Detect a saved v0.5.x HACF-uninstall.sh by sha and offer replacement.
- Banner (:867-868): run /hyper-baseline to write SPEC.md.

**Tests (deterministic)**

- test_fresh_install_noninteractive (-y downloads nothing; only the selected IDEs). PATH starts with failing shims for curl, wget, pip, pip3, uv, uvx, npx and npm, and the run uses `unshare -rn` when available (R24).
- test_missing_pyyaml_aborts_before_writes.
- test_old_pyyaml_aborts_before_writes: a shim reporting PyYAML 5.3.1 aborts with guidance and zero files written (R24).
- test_bash3_refused (R24).
- test_consent_reads_dev_tty: under `bash -s < HACF-install.sh` with no `/dev/tty`, every consent is NO (R24).
- test_repo_url_env_ignored_without_hacf_test: `HACF_REPO_URL` alone is ignored; `--repo-url` is used; `file://` is refused unless `HACF_TEST=1` (R12).
- test_consent_prompt_shows_url_and_commit (R12).
- test_openrig_only_repo_is_fresh_install.
- test_upgrade_preserves.
- test_upgrade_customized_agents_md_sidecar: an unknown-sha AGENTS.md is byte-identical after `-y` and `AGENTS.hacf-v0.6.0.md` exists (R9).
- test_install_leaves_user_gitattributes: a user `.gitattributes` with LFS rules is byte-identical (R10).
- test_manifest_records_installed_commit (R25).
- test_mcp_merge_three_ides.
- test_never_copy.
- bash -n.

**Negative constraints**

- DO NOT use bare read -p.
- DO NOT auto-consent.
- DO NOT write user-level config.
- DO NOT honor URL or branch overrides from the environment unless `HACF_TEST=1` (R12).
- DO NOT write a `.gitattributes` (R10).
- DO NOT use `sha256sum` or other GNU-only tools in shipped code paths (R24).

**Risks**

- Bash coverage is partial.

**Pipeline safety.** Runs only in user projects and temp dirs. Large slice (R17): the task checkboxes are the resume state; after any compaction, re-read the MiniPRD before continuing.

<a id="s12c"></a>
## S12c — NN 21: Uninstaller: manifest-driven with legacy fallback

- **Ledger node:** installer
- **Depends on:** S12b
- **Size estimate:** ~12k
- **Red Team resolutions:** R10, R12, R19, R24
- **Replan resolutions:** R44 (the S09b and S10 retired-name scans exclude exactly `.agents/config/legacy_file_hashes.json`)

**Goal.** Never delete user files, including on v0.5.9 installs that have no manifest.

**Files to create**

- dev/scripts/build_legacy_hashes.py
- .agents/config/legacy_file_hashes.json
- dev/tests/installer/test_uninstall.py

**Files to modify**

- HACF-uninstall.sh
- dev/tests/engine/test_orchestrator_cli.py (R44: the exclusion only)
- dev/tests/retire/test_retire_unwired_stores.py (R44: the exclusion only)

**Files to delete**

- (none)

**Tasks**

- dev/scripts/build_legacy_hashes.py generates .agents/config/legacy_file_hashes.json (path to sha256 for shipped files at release commits 626a28e..984ca46). It imports `_runtime.require_runtime()` (R19).
- HACF-uninstall.sh:
- with a manifest: delete only files whose sha still matches
- without one: delete only files matching a legacy hash, read from `.agents/config/legacy_file_hashes.json` in the uninstaller's own fresh clone, never from the project (R24)
- hashes are computed with Python `hashlib`, never `sha256sum` (R24)
- the clone URL and branch come only from `--repo-url` and `--branch`; env `HACF_REPO_URL`/`HACF_BRANCH` only when `HACF_TEST=1`; non-`https://` refused unless `HACF_TEST=1`; the prompt prints the effective URL and commit (R12)
- remove HACF blocks, settings rules and MCP entries, and the HACF merge-drivers block in the git common dir's info/attributes (R10)
- when `.agents/state/codegraph/project.json` names a project, offer (consented) `python3 .agents/scripts/hyper_codegraph.py delete` (R24)
- refuse NEVER_COPY paths outright
- today's version deletes every path that matches the clone (:119-133)
- R44: `.agents/config/legacy_file_hashes.json` keeps the four retired v0.5.x paths it lists (`hyper_fix.py`, `compaction_engine.py`, `interrupt_detector.py`, `final_truth_template.md`), because it is v0.5.x deletion data, not a code reference. `_scanned_files()` in dev/tests/engine/test_orchestrator_cli.py (S09b's test_no_refs_to_hyper_fix_in_code) and `_code_files()` in dev/tests/retire/test_retire_unwired_stores.py (S10's test_no_code_refs_to_retired_stores) each skip exactly that one repo-relative path (an equality test on the path, no glob, prefix, directory or name pattern); nothing else in either file changes

**Tests (deterministic)**

- test_uninstall_preserves (pyproject, uv.lock, LICENSE, .gitignore, .gitattributes, SPEC.md, project.yaml, workspace.yaml, lore/, missions/) (R10).
- test_no_manifest_legacy_hash_fallback.
- test_legacy_hashes_read_from_clone: a tampered project copy of legacy_file_hashes.json is ignored (R24).
- test_uninstall_offers_codegraph_delete (R24).
- test_uninstall_repo_url_env_ignored_without_hacf_test (R12).

**Negative constraints**

- DO NOT delete files with an unknown sha.
- DO NOT widen the R44 scan exclusion beyond the exact path `.agents/config/legacy_file_hashes.json`, or change anything else in the two S09b and S10 test files.

**Risks**

- Saved v0.5.x uninstallers remain dangerous; CHANGELOG and installer detection mitigate.

**Pipeline safety.** Not in the loop.

<a id="s13a"></a>
## S13a — NN 22: Migration I: detect, plan, backup, journal, rollback, specs incl. archived

- **Ledger node:** migration_engine
- **Depends on:** S01b, S03b, S12a
- **Size estimate:** ~34k
- **Red Team resolutions:** R17, R19, R22, R25
- **Replan resolutions:** R29, R37, R38, R45 (slice dirs take 1-based positions when any NN in a mission is missing, 0 or duplicated)

**Goal.** hyper_migrate.py converts flat and archived specs into missions, safely and repeatably.

**Files to create**

- .agents/scripts/hyper_migrate.py
- dev/tests/migration/test_migrate_specs.py

**Files to modify**

- (none)

**Files to delete**

- (none)

**Tasks**

- detect, plan --json, backup to .agents/state/migrations/<ts>/backup (exempt from retention), journal, rollback. The backup is verified with Python `hashlib` against its SHA256SUMS, never `sha256sum` (R25).
- apply (R25): requires a TTY confirmation; `--yes` skips it but is refused under `HACF_AUTONOMOUS=1`; documented as procedural for agent shells.
- rollback (R25): hashes every file it would restore or delete against the post-apply journal and refuses on drift unless `--force`, listing the drifted paths.
- SuperPRD classification by heading text (any number): baseline vs feature.
- Membership from Execution Checklist entries matched by exact filename, OR by the exact normalized 'MiniPRD: <name>' to MiniPRD_<name>.md / _AUDITED.md. No fragments.
- Twins (R38): when spec/compiled/MiniPRD_<name>.md and spec/archive/MiniPRD_<name>_AUDITED.md both exist, the archived copy is the slice; the compiled twin makes no slice, is listed in the plan as a twin, and is moved with the converted compiled files (`archive_specs.py --paths --label v060_migrated`, which never overwrites).
- Slices via `hyper_scope.py slice create` (R37): `--intent` is the legacy H1 text after `# MiniPRD: ` (else after `# `, else the `<name>` of `MiniPRD_<name>.md`); `--legacy-source` and `--legacy-order` fill `hacf_legacy_source` and `hacf_legacy_order`.
- --include-archived: spec/archive/MiniPRD_*_AUDITED.md becomes hacf_status audited, with PROOF.md and a proof/00-legacy-audit.md C1 artifact. candidate_sha comes from the `HACF-Slice:` trailer commit, else 'legacy-unrecorded'. When the trailer commit is found, the same sha is also written as `swimlane_sha`, so proof checks treat it as historical if it later becomes unreachable (R22).
- Mission status (R29): after a mission's slices exist, set its mission.yaml `metadata.status` with `hyper_openrig.set_mission_status()`: every slice audited -> complete (a historical mission); any slice past compiled -> active; otherwise it stays planned and nothing is written. The mission SPEC.md `hacf_status` is left as hyper_scope.py wrote it.
- Unreferenced MiniPRDs go to legacy-unassigned.
- Slice dir names (R45): `<NN>-<slug>` with the file's NN when every slice of the mission has a valid, unique rig ordinal (1 or more); when any NN in the mission is missing, 0 or duplicated, every slice of that mission takes its 1-based position in the mission order (HACF's release-0.6.0: slices 01..29, slice number = MiniPRD file NN + 1). `--legacy-order` is the 1-based position.
- Order and --no-chain; heading conversion. The conversion adds every slice heading of dev/design/v0.6.0/schemas.md#spec-frontmatter that the legacy file lacks, `## Intent visual` included, so lint finds no missing heading; the legacy section text stays verbatim, and lint reports the two rig content rules on a migrated slice as warnings (R29).
- Move legacy files via `archive_specs.py --paths`.
- Timestamps via `_runtime`; import `_runtime.require_runtime()` (R19).

**Tests (deterministic)**

- golden plan for the fixture and for an HACF-repo snapshot.
- apply then lint clean.
- idempotent.
- rollback byte-identical.
- test_rollback_refuses_drift: a migrated file edited after apply makes rollback refuse and list it; `--force` proceeds (R25).
- test_apply_yes_refused_autonomous (R25).
- test_backup_verified_with_hashlib (R25).
- test_archived_proof_sets_swimlane_sha (R22).
- test_historical_mission_status_complete: a mission whose slices are all archived and audited gets `metadata.status: complete`; one with audited and compiled slices gets `active`; one with only compiled slices keeps `planned` with mission.yaml byte-identical (R29).
- test_compiled_twin_of_archived: with `--include-archived`, `MiniPRD_alpha.md` in spec/compiled and `MiniPRD_alpha_AUDITED.md` in spec/archive give one audited slice from the archived copy, the plan lists the compiled file as a twin, and apply moves it under `spec/archive/<ts>_v060_migrated/` with the `_AUDITED` file byte-identical (R38).
- archived import with name-matched checklists.

**Negative constraints**

- DO NOT pair by fragments.
- DO NOT write spec/archive except via archive_specs.py.
- DO NOT apply without a TTY confirmation or `--yes`, and never with `--yes` under `HACF_AUTONOMOUS=1` (R25).

**Risks**

- Structure loss is mitigated by keeping the original bytes in the backup.

**Pipeline safety.** Explicit invocation only. Resolve keeps working through the journal and frontmatter fallback. Large slice (R17): the task checkboxes are the resume state; after any compaction, re-read the MiniPRD before continuing.

<a id="s13b"></a>
## S13b — NN 23: Migration II: ledger, lore, memory, notes, firewall, verify

- **Ledger node:** migration_engine
- **Depends on:** S13a, S05b, S11
- **Size estimate:** ~25k
- **Red Team resolutions:** R8, R9, R10, R25, R26
- **Replan resolutions:** R28, R46 (S10's retired-name scan excludes exactly `.agents/scripts/hyper_migrate.py`)

**Goal.** Finish user-install migration.

**Files to create**

- dev/tests/migration/test_migrate_stores.py

**Files to modify**

- .agents/scripts/hyper_migrate.py
- dev/tests/retire/test_retire_unwired_stores.py (R46: the exclusion only)

**Files to delete**

- (none)

**Tasks**

- ledger: repair-text (diff, confirm), migrate-v2, associated_file via the journal, spec_refs via `ensure-node --spec-refs` (R28).
- lore (R25): import **/docs/adr with --context, skipping gitlinks (`git ls-files -s` mode 160000), untracked trees and nested repositories; interactive: ask per directory; non-interactive: import only the project root's docs/adr; each moved ADR file leaves a per-file redirect stub at its old path. Canon bodies are never written into lore/CANON.md directly: a body that becomes canon lives in lore/canon/<ID>.md and CANON.md is regenerated by `hyper_lore.py index` (R8); on import every node is PROPOSED (D7), so lore/canon/ stays empty.
- memory: productContext into SPEC.md; systemPatterns into a PROPOSED canon (body in lore/proposed/); legacy field notes into mission NOTES.md via the journal.
- Instruction files (R9): AGENTS.md, CLAUDE.md and GEMINI.md go through the S12a decision function: a known v0.5.x sha is replaced by the HACF block; an unknown sha is never appended to (interactive: replace with `<file>.v059.bak` or sidecar; non-interactive: sidecar `<STEM>.hacf-v0.6.0.md` plus a report line).
- Firewall, blocks (R26 grammar) and drivers. Merge-driver attributes go into the git common dir's info/attributes block, never a tracked `.gitattributes` (R10).
- verify: lint, validate, firewall check, rig audit when present.
- R46: `.agents/scripts/hyper_migrate.py` names `productContext` and `systemPatterns`, because it is the v0.5.x migration reader by design (the memory task above). `_code_files()` in dev/tests/retire/test_retire_unwired_stores.py (S10's test_no_code_refs_to_retired_stores) skips exactly that one repo-relative path (an equality test on the path, no glob, prefix, directory or name pattern), next to R44's skip; nothing else in that file changes. The full fixture test runs on a `clean_ledger` variant (the v0.5.9 fixture ledger has three broken edges no ledger command removes; the migration reports them).

**Tests (deterministic)**

- memory fold.
- multi-context ADR import.
- test_adr_scan_skips_gitlinks_and_nested_repos (R25).
- test_adr_noninteractive_root_only (R25).
- test_adr_redirect_stub_per_file (R25).
- test_migrate_customized_agents_md_sidecar (R9).
- test_migrate_never_writes_gitattributes (R10).
- broken ledger repaired.
- legacy notes moved.
- full fixture migration then engine --dry-run.

**Negative constraints**

- DO NOT approve lore.
- DO NOT delete unimported ADRs.
- DO NOT import ADRs from submodules, nested repositories or untracked trees (R25).
- DO NOT widen the R46 scan exclusion beyond the exact path `.agents/scripts/hyper_migrate.py`, or change anything else in the S10 test file.

**Risks**

- (none)

**Pipeline safety.** Journaled and reversible.

<a id="s13c"></a>
## S13c — NN 24: /hyper-update rewritten once: manifest sync, integrity policy, migration hand-off

- **Ledger node:** hyper_update_core_logic (existing; also hyper_update, hyper_update_merge_engine, hyper_update_upstream_verification)
- **Depends on:** S13b, S12b
- **Size estimate:** ~28k
- **Red Team resolutions:** R9, R11, R12, R13, R17, R19, R24, R25

**Goal.** Make the script-driven update actually runnable and non-destructive.

**Files to create**

- .agents/config/upstream.yml
- dev/tests/update/test_update_core.py

**Files to modify**

- .agents/scripts/hyper_update_core.py
- .agents/scripts/hyper_update_merge_engine.py
- .agents/scripts/hyper_update_verification.py
- .agents/skills/hyper-update/SKILL.md
- .agents/skills/hyper-update/META.yml

**Files to delete**

- (none)

**Tasks**

- Integrity (ADR-0010): add .agents/config/upstream.yml {repo_url, ref, verification: {allowed_signers or gpg fingerprints, require_consent_if_unsigned: true}}. The shipped upstream.yml is only a proposal (R12). Replace the hard verify_gpg_signature exit (:142-179, :346):
- signer pinning (R12): on first trusted update, the signer set is pinned machine-locally in `.agents/state/upstream-trust.json` (TOFU); later updates read signers only from that pin; a payload upstream.yml that differs from the pin is shown as a diff and adopted only with interactive consent (refused under `-y`, without a TTY, or under `HACF_AUTONOMOUS=1`)
- signers pinned: a signature is required
- SSH signatures (R11): verify with `git -c gpg.ssh.allowedSignersFile=<file built from the pin> verify-commit <sha>`; future HACF signing is SSH-only; no doc or prompt tells the user to import a key globally
- unsigned (v0.6.x releases are unsigned, R11): interactive consent, or `--allow-unsigned`; `--allow-unsigned` is refused under `HACF_AUTONOMOUS=1` or without a TTY (R25; accepted consequence: no unattended CI updates while releases are unsigned)
- consent log (R25): append `{timestamp, url, commit, mode: interactive|flag, os_user}` to `.agents/logs/update-consent.jsonl`
- upstream overrides (R12): `/hyper-update --upstream-url <url> --ref <ref>` flags; env `HACF_UPSTREAM_URL` is honored only when `HACF_TEST=1`; a non-`https://` URL is refused unless `HACF_TEST=1`; every consent prompt and log line prints the effective URL and commit sha
- anti-rollback (R25): `.agents/hacf-manifest.json` records `installed_commit`; the fetch uses `git clone --filter=blob:none` (not `--depth=1`) so ancestry can be tested; an upstream commit that is not a descendant of `installed_commit` is refused without `--allow-downgrade`
- AUTO_UPDATE_DIRS (:35-41): drop spec/ and tests/. Manifest per-file sync replaces rmtree+copytree (:284-293). Merge the .agentignore block. Merge SENSITIVE_FILES via blocks. Whole-file AGENTS.md, CLAUDE.md and GEMINI.md go through the S12a decision function (unknown sha: never appended; R9). Enforce NEVER_COPY. Hand off to `hyper_migrate.py plan`.
- Missing `.agents/config/*` files fall back to embedded defaults with the notice "run HACF-install.sh to complete the upgrade" (R24).
- merge_engine is block-aware.
- verification: retention parses only exact timestamp names and never touches *-migration or state/migrations dirs (:112-125).
- Timestamps via `_runtime`; import `_runtime.require_runtime()` in the three modified entry points (R19).
- Rewrite hyper-update SKILL.md and META.yml once.

**Tests (deterministic)**

- test_unsigned_upstream_requires_consent.
- test_allow_unsigned_refused_without_tty_or_autonomous (R25).
- test_consent_log_fields (R25).
- test_ssh_signed_upstream_verified (git 2.34 ssh signing, temp key, `-c gpg.ssh.allowedSignersFile`) (R11).
- test_signers_pinned_tofu: a payload upstream.yml with an empty signer list does not change the pin without interactive consent (R12).
- test_upstream_url_env_ignored_without_hacf_test: `HACF_UPSTREAM_URL` alone is ignored; `--upstream-url` is used; `file://` is refused unless `HACF_TEST=1` (R12).
- test_downgrade_refused_without_flag (R25).
- test_unknown_sha_agents_md_not_appended (R9).
- test_missing_config_uses_embedded_defaults (R24).
- test_preserves_user_spec_tests (rmtree regression).
- test_user_skill_dirs_preserved.
- test_retention_exempts_migration.

**Negative constraints**

- DO NOT auto-apply the migration non-interactively.
- DO NOT update unsigned without consent.
- DO NOT read allowed signers from the update payload except as a proposal (R12).
- DO NOT honor `HACF_UPSTREAM_URL` without `HACF_TEST=1` (R12).
- DO NOT document a global GPG or SSH key import (R11).

**Risks**

- Unsigned updates weaken authenticity; signing is opt-in and SSH-only (ADR-0010, R11).

**Pipeline safety.** Not in the build loop. Old-text v0.5.9 updates copy v0.6.0 skills, which dual-read the legacy layout and tolerate missing `.agents/config/*` (R24). The three modified scripts were normalized in S00 (R13). Large slice (R17): the task checkboxes are the resume state; after any compaction, re-read the MiniPRD before continuing.

<a id="s14"></a>
## S14 — NN 25: Supporting skills sweep (each edited once)

- **Ledger node:** supporting_skills
- **Depends on:** S06, S07b, S10
- **Size estimate:** ~16k
- **Replan resolutions:** R47 (exactly the four bare `python` commands in hyper-publish/SKILL.md become `python3`)

**Goal.** New topology, lore and memory vocabulary everywhere else.

**Files to create**

- dev/tests/skills/test_supporting_contract.py

**Files to modify**

- .agents/skills/hyper-clear/SKILL.md
- .agents/skills/hyper-sop/SKILL.md
- .agents/skills/hyper-handoff/SKILL.md
- .agents/skills/hyper-handoff/META.yml
- .agents/skills/hyper-troubleshooting/SKILL.md
- .agents/skills/hyper-process-document/SKILL.md
- .agents/skills/hyper-refresh-memory/SKILL.md
- .agents/skills/hyper-session-update/SKILL.md
- .agents/skills/hyper-document/SKILL.md
- .agents/skills/hyper-consult-cto/SKILL.md
- .agents/skills/hyper-contextualize/SKILL.md
- .agents/skills/hyper-contextualize/META.yml
- .agents/skills/hyper-create-issue/SKILL.md
- .agents/skills/hyper-deepdive/SKILL.md
- .agents/skills/hyper-peer-review/SKILL.md
- .agents/skills/hyper-stitch-design/SKILL.md
- .agents/skills/hyper-publish/META.yml
- .agents/skills/hyper-publish/SKILL.md (R47: the four bare `python` commands only)
- .claude/commands/hyper-process-document.md

**Files to delete**

- (none)

**Tasks**

- clear, sop, handoff (+META), troubleshooting, process-document, refresh-memory, session-update, document, consult-cto, contextualize (+META), create-issue, deepdive, peer-review, stitch-design; publish META.yml only. Use python3 everywhere.
- Update the process-document bridge.
- R47: in .agents/skills/hyper-publish/SKILL.md, change exactly the four bare `python` commands (v0.5.9 lines 33, 80, 249 and 367: setup_permissions.py twice, generate_commit_message.py, generate_pr_description.py) to `python3` (US-002), and nothing else in that file. Then the oracle.

**Tests (deterministic)**

- grep contract: no SuperPRD.md target, productContext, systemPatterns, spec/handoffs, stale model id or bare `python `.
- Every SKILL.md has frontmatter.

**Negative constraints**

- DO NOT touch skills edited in S06, S07a, S07b or S13c.
- DO NOT edit anything in .agents/skills/hyper-publish/SKILL.md besides the four R47 `python` -> `python3` changes, or its scripts, examples or resources.

**Risks**

- (none)

**Pipeline safety.** Not used by the loop.

<a id="s15a1"></a>
## S15a1 — NN 26: Agent instruction files: trailhead, layout, schemas

- **Ledger node:** agent_instructions
- **Depends on:** S13c, S14
- **Size estimate:** ~20k
- **Red Team resolutions:** R2, R4, R6, R7, R11, R19, R23, R24, R26
- **Replan resolutions:** R48 (install templates body-only, wrapped by the installer and migration; root instruction files stay without the HACF block)

**Goal.** AGENTS.md (dev and template), CLAUDE.md, GEMINI.md and the IDE rule bridges at v0.6.0, pointing to lore/MAP.md.

**Files to create**

- (none)

**Files to modify**

- AGENTS.md
- .agents/install-templates/AGENTS.md
- .agents/install-templates/CLAUDE.md
- .agents/install-templates/GEMINI.md
- CLAUDE.md
- GEMINI.md
- .cursor/rules/hypergraph-agent.mdc
- .clinerules/hypergraph-agent.md
- .roo/rules/hypergraph-agent.md
- .windsurf/rules/hypergraph-agent.md

**Files to delete**

- (none)

**Tasks**

- AGENTS.md and the template: the template is the HACF block's body only, with no markers; the installer and the migration wrap it in the HACF block (R26 marker grammar, R48); Trailhead; tree; scripts (python3; fixes :51, :54); rendered firewall with correct limits; SuperPRD, MiniPRD and ledger v2 schemas; model ids (:155); GPG text (:190) changed to the ADR-0010 policy (releases unsigned in v0.6.x; any future signing is SSH-only; never import a key globally; R11); Migration Notice (dual-read until v0.7.0, no earlier than 2027-01-31); remove handoffs and memory-template references.
- Binding constraints (R23): CANON nodes; stale CANON nodes stay binding until retired or superseded; PROPOSED nodes with `origin: user-decision` bind agents as constraints until rejected.
- Stated limits (R2, R4, R6, R7): Read and Edit deny-rule enforcement in bypassPermissions as recorded by the S11 live test; lore approvals need default or acceptEdits; the CANON gate is procedural, with the remaining bypasses (a forged ticket, direct Python writes to lore_hypergraph.yml); user-level hooks still run in engine sessions (`--setting-sources user`); the bwrap oracle sandbox is Linux-only.
- CLAUDE.md and GEMINI.md, root and templates. Root CLAUDE.md keeps its "Safe Sandboxing & Automation" and "Context Window Management" sections verbatim (R19).
- hypergraph-agent rules in .cursor/rules, .clinerules, .roo/rules and .windsurf/rules.
- R48: the CLAUDE.md and GEMINI.md templates are body-only too; root AGENTS.md, CLAUDE.md and GEMINI.md stay whole files without the HACF block (the user's answer to the root-versus-template question).

**Tests (deterministic)**

- grep contract.
- test_claude_md_keeps_loop_sections: root CLAUDE.md contains the "Safe Sandboxing & Automation" and "Context Window Management" sections byte-identical to their pre-slice text (R19).
- test_agents_md_binding_and_limits_text: AGENTS.md names stale CANON and PROPOSED user-decision nodes as binding and states the limits listed above (R7, R23).
- Blocks balanced (R26): no duplicate, nested or unbalanced HACF marker; each install template, as hyper_blocks wraps it, holds exactly one HACF block (R48).
- test_agents_md_size_bytes: AGENTS.md size in bytes is not above the v0.5.9 baseline, and the install template plus an OpenRig block stays under 32 KiB (Codex cap) (R24).

**Negative constraints**

- DO NOT duplicate the MAP.md index.
- DO NOT put HACF markers in the install templates, or add the HACF block to root AGENTS.md, CLAUDE.md or GEMINI.md (R48).

**Risks**

- (none)

**Pipeline safety.** Always-on context changes. The legacy MiniPRD schema stays documented for dual-read. Every later session auto-loads root CLAUDE.md, so the loop's "Safe Sandboxing & Automation" and "Context Window Management" mandates must survive (R19).

<a id="s15a2"></a>
## S15a2 — NN 27: Documentation

- **Ledger node:** agent_instructions
- **Depends on:** S15a1, S09b
- **Size estimate:** ~25k
- **Red Team resolutions:** R2, R5, R6, R7, R10, R11, R12, R17, R19, R20, R23, R24, R25
- **Replan resolutions:** R49 (`hyper_lore.py init` creates an empty hacf.lore/v1 graph, MAP.md and CANON.md through the locked writer and refuses when a graph exists; hyper_scaffold.py calls it for a missing graph instead of reporting `skipped`; supersedes the MiniPRD_19 (S12a) clarifying question 3 deferral), R50 (dev/tests/test_never_copy.py: `MAY_EXIST` gains lore_hypergraph.yml, which R49's `init` creates on a fresh install; the sentinel checks are unchanged)

**Goal.** README and docs at v0.6.0, including the safe new-project path.

**Files to create**

- docs/Lore.md
- dev/tests/lore/test_lore_init.py (R49)

**Files to modify**

- README.md
- docs/MasterSOP.md
- docs/MIGRATION_RULES.md
- docs/Troubleshooting.md
- docs/Tutorial.md
- docs/Whitepaper.md
- skills-info.md
- Agentic-Memory.md
- DEVELOPMENT.md
- .agents/schemas/project-templates/DEVELOPMENT.md
- .agents/scripts/hyper_lore.py (R49: the `init` command only)
- .agents/scripts/hyper_scaffold.py (R49: the missing-graph step only)
- dev/tests/scaffold/test_scaffold.py (R49: the missing-graph assertions)
- dev/tests/test_never_copy.py (R50: `MAY_EXIST` gains lore_hypergraph.yml only)

**Files to delete**

- (none)

**Tasks**

- README:
- the New Project path becomes `git init` plus the installer (fixes README:42 clone-as-root)
- uninstall and upgrade notes lead with "delete any saved copy of HACF-uninstall.sh" (R10); always re-download the installer; saved v0.5.x installers abort at tests/; saved uninstallers delete same-named user files, including `.gitattributes` (Git LFS rules), `lore_hypergraph.yml`, `SPEC.md`, `project.yaml`, `workspace.yaml` and `docs/*` (R10)
- Supported Platforms (R24): Linux (Ubuntu 22.04+, Debian 12+) and WSL2 supported and tested; macOS best-effort (bash >= 4 from Homebrew; the bwrap oracle is Linux-only)
- install flags (R12): `--repo-url` and `--branch`; environment overrides only with `HACF_TEST=1`
- remove Final Truth and hyper_fix (:238-239)
- model id :377
- docs/MasterSOP, MIGRATION_RULES, Troubleshooting, Tutorial, Whitepaper; skills-info.md; Agentic-Memory.md; DEVELOPMENT.md; .agents/schemas/project-templates/DEVELOPMENT.md:20 (content only, no version bump); new docs/Lore.md (optional CODEOWNERS).
- docs/Lore.md (R6, R7, R10, R23, R24): the approval flow with the hook ticket and the ticketless TTY fallback; approvals need default or acceptEdits; the remaining bypasses (a deliberately forged ticket, direct Python writes to lore_hypergraph.yml); `approved_by` is attribution, not identity; branch merges are not gated, with recommended CODEOWNERS on `lore_hypergraph.yml`, `lore/canon/**` and `lore/CANON.md`; merge drivers are installed repo-locally into the git common dir's info/attributes (no tracked `.gitattributes`); tracked `.mcp.json`, `.cursor/mcp.json` and `.roo/mcp.json` entries make teammates who accept the server run `uvx codebase-memory-mcp@0.12.1`.
- Update docs (R11, R12, R25): releases are unsigned in v0.6.x; any future signing is SSH-only and docs never tell users to import a key globally; signers are pinned locally on first trust; `--allow-unsigned` needs a TTY, so unattended CI updates are not possible while releases are unsigned; `--allow-downgrade`.
- Engine docs (R2, R5, R20): the bwrap oracle default and `--allow-unsandboxed-oracle`; `--unverified-executor`; `land --rebase-ledger`; `hyper_engine.py gc`; the per-run budget.
- Every Python command in docs uses python3 (R19).
- R49: add `hyper_lore.py init`: under lore.lock it refuses (exit 4, nothing written) when lore_hypergraph.yml exists in any form, else writes the empty hacf.lore/v1 graph compare-and-swap through hyper_lore's single writer and then generates lore/MAP.md and lore/CANON.md as `index` does; refused under HACF_OUTBOX. hyper_scaffold.py apply runs `init` when the graph is missing and reports the graph, MAP.md and CANON.md `created`; an existing graph is `kept`; a second apply writes nothing. docs/Lore.md and the README install section describe `init`. This supersedes the MiniPRD_19 (S12a) clarifying question 3 deferral (scaffold reported a missing graph `skipped`).
- R50: add lore_hypergraph.yml to `MAY_EXIST` in dev/tests/test_never_copy.py (a fresh install now creates the empty graph through `init`, so it may exist, without the sentinel); nothing else in the test changes.

**Tests (deterministic)**

- Grep contract on all docs.
- test_docs_no_global_key_import: no doc tells users to import a signing key globally (R11).
- test_docs_uninstall_warning_leads: README's uninstall section starts with the saved-uninstaller warning (R10).
- Internal link check.
- test_lore_init_creates_empty_graph, test_lore_init_refuses_existing, test_lore_init_uses_locked_writer, test_scaffold_keeps_existing_lore_graph; test_idempotent reports the graph, MAP.md and CANON.md `created`, then `kept` (R49).
- test_never_copy passes on a fresh install, with lore_hypergraph.yml allowed to exist without the sentinel (R50).

**Negative constraints**

- DO NOT bump versions in project-templates.
- DO NOT describe the firewall or the CANON gate as a security boundary.
- DO NOT write lore_hypergraph.yml, lore/MAP.md or lore/CANON.md except through hyper_lore.py's locked writer, overwrite an existing graph, or change any other hyper_lore.py command or hyper_scaffold.py step (R49).
- DO NOT change dev/tests/test_never_copy.py beyond adding lore_hypergraph.yml to `MAY_EXIST`: keep every NEVER_COPY path, the sentinel-absent assertion, the `MAY_EXIST` sentinel check and the repo-wide sentinel sweep (R50).

**Risks**

- Large prose. Not split (Q29): if it runs over budget, the execute spans sessions and resumes from the task checkboxes (R17, R19).

**Pipeline safety.** Docs, plus R49's `init` command and scaffold step. Large slice (R17): the task checkboxes are the resume state; after any compaction, re-read the MiniPRD before continuing.

<a id="s15b"></a>
## S15b — NN 28: Release v0.6.0 and dogfood migration

- **Ledger node:** orchestration_layer
- **Depends on:** S15a2, S09b, S13a, S12c
- **Size estimate:** ~18k
- **Red Team resolutions:** R10, R11, R12, R19, R21, R22, R23, R25
- **Replan resolutions:** R43 (the Red Team resolutions story takes the next free STORY id; STORY-0003 is the S09a2 stop record), R45 (the mission is dynamic-workflow-parallel-execution-engine; release-0.6.0 slice dirs are 01..29), R51 (before the apply, the machine-local `/home` interpreter in MiniPRD_16's audited copy becomes `/usr/bin/python3`; no tracked spec/ or missions/ file names a `/home` interpreter; the recorded S04, S08 and S11 fixtures stay unedited and the CHANGELOG lists them), R52 (the apply's firewall and lore/proposed writes join the files; the machine-local .agents/local/hacf.yml and the root sidecars are deleted after the apply; resolve takes the full legacy path; the user consented to apply --yes; the daemon verdict is held until after the apply), R53 (the template-name grep excludes exactly .agents/config/legacy_file_hashes.json and the Historical section of docs/MIGRATION_RULES.md, which gains one line saying the three templates were removed in v0.6.0; the promotion offer was answered "Not now"; hacf stays registered in OpenRig and .agents/state/openrig/ is never touched; the apply re-runs from the clean repinned tree), R54 (the uv.lock root version line follows the pyproject bump; the converted slice is marked executing after the resolve; the Red Team story body is written outside the repo and the node file takes propose's dashed slug; a guarded `hyper_lore.py sync` refreshes STORY-0002's sha after the verdict edit; render_claude deduplicates rules so S11's golden passes with the apply's agentignore block; the apply re-runs from the clean repinned tree)

**Goal.** Migrate HACF's own specs (including archived ones) into missions/, untrack spec/ and tests/, bump the version.

**Files to create**

- SPEC.md
- project.yaml
- workspace.yaml
- missions/
- dev/tests/test_release_dag.py
- dev/tests/e2e/test_from_diff_real_history.py
- lore/STORIES/STORY-*-v0-6-0-red-team-resolutions.md
- dev/tests/firewall/test_firewall_dedupe.py
- lore/canon/
- lore/proposed/
- .codeiumignore
- .cursorignore
- .rooignore

**Files to modify**

- CHANGELOG.md
- README.md
- CITATION.cff
- pyproject.toml
- .gitignore
- .agentignore
- docs/MIGRATION_RULES.md
- uv.lock
- .agents/scripts/hyper_firewall.py
- lore_hypergraph.yml
- lore/MAP.md
- lore/CANON.md
- lore/ADRs/

**Files to delete**

- spec/active/.gitkeep
- spec/archive/.gitkeep
- spec/compiled/.gitkeep
- spec/process/.gitkeep
- tests/candidate_outputs/.gitkeep
- tests/fixtures/.gitkeep
- tests/integration/.gitkeep
- .agents/schemas/MiniPRD_template.md
- .agents/schemas/SuperPRD_Template.md
- .agents/schemas/hypergraph_schema.md

**Tasks**

- Run `hyper_migrate.py plan --include-archived`, then apply:
- release-0.6.0 = OPR.0.6.0 with 29 slices (28 audited, 1 executing) in slice dirs 01..29, slice number = MiniPRD file NN + 1 (R45); S15b's own audit then writes its slice as audited through `python3 .agents/scripts/hyper_spec.py progress`, so the mission ends with 29 audited (R19)
- every migrated slice's C1 proof carries `candidate_sha` = its `HACF-Slice:` commit and `swimlane_sha` = the same sha, so proof checks treat it as historical if a `--squash` landing makes it unreachable (R22; Q38 stays the user's landing-time choice)
- hyper-update and dynamic-workflow-parallel-execution-engine (the H1 rule, R45) become historical (complete)
- TokenEfficiency MiniPRDs go to legacy-unassigned
- git add the D14 artifacts; `git rm -r --cached spec`; git rm the tests/*/.gitkeep files; .gitignore ignores /spec/ and /tests/ wholesale. No root `.gitattributes` is added or tracked (R10).
- Delete the deprecated MiniPRD_template.md, SuperPRD_Template.md and hypergraph_schema.md. ADR-0002 and ADR-0003 anchor these templates; `hyper_lore.py validate` reports those anchors as stale warnings and exits 0 (R21); this slice does not edit the anchors.
- Ledger leftovers (R21): run `python3 .agents/scripts/hyper_ledger.py review-template --status needs_review,dirty --json`, have a sub-agent return the JSON patch, apply it with `apply-review --patch <file> --slice S15b`, then run `validate --strict`, which fails on any leftover needs_review node.
- Lore (R23, R43): propose "v0.6.0 Red Team resolutions" (PROPOSED) under the next free STORY id (STORY-0004 as of R43): count the nodes, write the body outside the repo (R54) summarizing the R-rows and linking missions/release-0.6.0/SPEC.md, then `python3 .agents/scripts/hyper_lore.py propose`, which writes lore/STORIES/STORY-<id>-v0-6-0-red-team-resolutions.md (its dashed slug, R54), and `index`; the release holds one lore node more than before this step (15 as of R43: 13 from S05b, the S09a2 stop record STORY-0003 and this story). Offer the user optional one-at-a-time promotion of ADR-0001..0009 through `/hyper-lore-approve` (consent; a decline is recorded, not a failure); an approval writes lore/canon/<ID>.md and may update the ADR's status frontmatter under lore/ADRs/, which is why both paths are listed; with no approval they stay unchanged.
- Bump the version in CHANGELOG (Added, Changed, Removed; old-installer warnings), the README:12 badge, CITATION.cff and pyproject. The CHANGELOG warnings (R10, R11, R12, R25): lead with "delete any saved copy of HACF-uninstall.sh"; saved uninstallers delete same-named user files including `.gitattributes` (Git LFS rules), `lore_hypergraph.yml`, `SPEC.md`, `project.yaml`, `workspace.yaml` and `docs/*`; releases are unsigned and any future signing is SSH-only, so the v0.5.x GPG update path stays disarmed; URL and branch overrides now need flags (or `HACF_TEST=1`); unattended unsigned updates are refused.
- R51: before the apply, replace the machine-local interpreter path under `/home` (the host's `HACF_PY310` venv, written by the S09a2 build) in spec/archive/MiniPRD_16_EngineWorktreesLand_AUDITED.md with `/usr/bin/python3` (3 occurrences: Task 45, the §5 oracle, the block's `oracle`), by one `sed` and without reading the file, so the migration copies portable text into missions/; `HACF_PY310` stays a host setting in dev/scripts/check.sh. The CHANGELOG lists the recorded fixtures under dev/tests/fixtures/recorded/S04, S08 and S11 that keep absolute paths, unedited, as evidence.
- R52: the apply writes the HACF managed block into `.agentignore`, renders `.codeiumignore`, `.cursorignore` and `.rooignore`, and creates `lore/proposed/`; all are in the block's files and are staged. After the apply, delete the machine-local `.agents/local/hacf.yml` (never committed) and the root `AGENTS/CLAUDE/GEMINI.hacf-v0.6.0.md` sidecars; root AGENTS.md, CLAUDE.md and GEMINI.md stay byte-identical; keep the migration journal. Resolve the moved MiniPRD with its full legacy path, `hyper_spec.py resolve spec/compiled/MiniPRD_28_ReleaseDogfood.md` (the bare name is refused). The builder runs `hyper_migrate.py apply --include-archived --allow-dirty --yes` on the user's consent of 2026-11-19 00:52Z UTC. The daemon verdict is held: the user runs the STORY-0002 check after the apply, and the builder records the user's words verbatim when they arrive.
- R53: after deleting the templates, the grep for their names excludes exactly two places: the file .agents/config/legacy_file_hashes.json (S12c's v0.5.x removal hashes) and docs/MIGRATION_RULES.md from its `## Historical: Persistent Rules (spec/schemas → CLAUDE.md, v0.5.x)` heading to the end (S15a2's kept v0.5.x section); any other hit is a stop. Add one line to that Historical section, after the `## What Was Migrated` table: "These three templates were removed in v0.6.0."; nothing else in docs/ changes. The promotion offer is answered: the user said "Not now" (via the lead, 2026-11-19); record "Not now (recorded)" and do not ask. The first apply was set aside by the lead (stash and journal outside the repo); the builder re-runs the plan check, the R51 `sed`, the apply (same consent), the resolve, the sidecar cleanup, the mission check and the template deletion from the clean repinned tree.
- R54: after the pyproject bump, change only the root package's `version` line in uv.lock (`uv lock --check` exits 0; the uv.lock diff is that one line). Right after the resolve, `python3 .agents/scripts/hyper_spec.py progress --slice missions/release-0.6.0/slices/29-release-dogfood --event executing` (the apply creates the slice as compiled). After the verbatim verdict edit of STORY-0002, run `python3 .agents/scripts/hyper_lore.py sync`, guarded: it may change only STORY-0002's content_sha256 (no CANON node turns stale, no other node changes), else restore the lore files and ask; never approve or reject lore. In `.agents/scripts/hyper_firewall.py`, `render_claude` keeps the first occurrence of each deny and ask rule, in order, so the apply's `agentignore` managed block (spec/archive/, tests/candidate_outputs/, already listed above it) renders no duplicates; dev/tests/firewall/golden/claude_rules.json stays unchanged and passes. The apply re-runs from the clean repinned tree (the R53 re-run is set aside by the lead).
- Full verification; `rig scope audit` if present. The daemon verdict comes from STORY-0002, or a user waiver.
- Gate finish with --no-track-spec (the pinned block has `track_spec: false`).

**Tests (deterministic)**

- E2E suite.
- test_from_diff_real_history (R21): `hypergraph_updater.py --from-diff` over the S01c slice commit maps to {hypergraph_updater, semantic_graph_merger}, and over the S13c slice commit to the four hyper_update* nodes; each run's `unmapped` list is asserted.
- lint, validate --strict, lore validate, firewall check.
- `git ls-files spec tests` is empty; `git ls-files .gitattributes` is empty (R10).
- test_release_dag_imports: each slice's imported hyper_* modules are created by its ancestors; each slice's created scripts are read at its own ref refs/hacf/v0.6.0/slices/<SID>, not from today's tree (R54).
- test_migrated_proofs_have_swimlane_sha (R22).
- test_never_copy on a fresh install.
- test_no_home_interpreter_in_spec_or_missions (R51): no file `git ls-files -- spec missions` lists names an interpreter under `/home` (`dev/tests/fixtures/recorded/` is not checked).
- test_render_claude_dedupes_rules (R54): the shipped .agentignore plus the `agentignore` managed block renders the golden deny list with no duplicate, first occurrences in order; test_render_claude_rules_golden passes unchanged.

**Negative constraints**

- DO NOT push.
- DO NOT bump project-templates.
- DO NOT approve lore without the user's per-node consent (R23).
- DO NOT track a root `.gitattributes` (R10).
- DO NOT edit dev/tests/fixtures/recorded/ or dev/scripts/check.sh, or write spec/archive/ beyond R51's one `sed` (R51).
- DO NOT commit `.agents/local/hacf.yml`, touch the OpenRig catalog, run `register-project`, or write a daemon verdict the user did not give (R52).
- DO NOT unregister or re-register hacf, or edit, delete or commit .agents/state/openrig/ (the user's real registration state); DO NOT widen the template-name grep exclusions beyond R53's two (R53).
- DO NOT change uv.lock beyond the root version line, edit dev/tests/firewall/golden/ or test_firewall.py, change hyper_firewall.py beyond render_claude's deduplication, or keep a sync that changes more than STORY-0002's sha (R54).

**Risks**

- Moving the executing MiniPRD; covered by the journal and frontmatter fallback.

**Pipeline safety.** Runs last. Its own audit resolves the moved MiniPRD through the journal and records its own audited status (R19). The block's `files` include `"missions/release-0.6.0/**"` and the lore paths this slice writes (R19, R23).

