# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.7] - 2026-11-27

### Added
- `progress-report.py` fills its cost, human, test and gate cells. `--telemetry <dir>` gives tokens
  and cost, each a list-price equivalent, from the release's own `collect_metrics.py` scorecard
  (each release's `telemetry_hint` holds the arguments that make it). `--queue rig` (read-only) or
  `--queue-export <file>` gives questions, answers and decisions by the `hacf:question` /
  `hacf:answer` tags and their legacy names `question-for-tj` / `tj-answer`.
  `--junit <X.Y.Z>=<file>` gives tests passing and collected, and `--gates-file <file>` the Phase 3
  gates; those two take exactly one `--project`.
- `openrig/hacf/bin/progress-report.py --project <root> [--project <root>]... [--host] --out-dir
  <dir>`: a read-only, zero-token progress report. It writes `progress.json`
  (`hacf.progress-report/v1`), `progress.md` and `progress.html` with four sections (headline, per
  release, quality and safety, human in the loop) and a Total row on each per-release table, per
  project and, with `--host`, as a roll-up. Every cell without a source reads "not recorded";
  nothing is estimated, and no model or network is used. `--collect-cases` is opt-in and counts
  pytest cases per release inside `bwrap`. `--validate` checks a `progress.json`.
  `seat-config-upgrade.py` carries the tool into a live spec.
- `openrig/hacf/bin/herdr-view.py --rig <rig> [--fix-size-only] [--dry-run]`: one command for a
  team's Herdr view. It sets the seats' tmux windows to `window-size latest`, reopens a missing or
  detached view, lays the seats out from the rig spec (the lead on top, then rows of three), closes
  only idle shells, restores the focus and writes to no other space. `seat-config-upgrade.py`
  carries it into a live spec.
- Question and update routing is config: `seat-config.py resolve --section routing --json`, which
  every seat now runs before asking (`startup/protocol.md`). The shipped default is the same
  discovery rule as before; `overrides.yml` may name a fixed session. Question, update and
  relayed-answer rows carry `hacf:question`, `hacf:update` or `hacf:answer`.
- Six seat presets in `openrig/hacf/presets/` (`claude-opus-5-5`, `claude-sonnet-5-5`,
  `claude-haiku-5-5`, `tiered`, `codex`, `agy`), none selected by default. Each states what was
  tested in a `tested:` line, which `seat-config.py validate` now requires of every preset.
  `seat-config-upgrade.py` carries `presets/` into a live spec like the other adapted files.
- `seat-config-upgrade.py` prints an apply line with its effect time for each seat config v2 key
  (`tools`, `skills`, `instructions`, `harness`, `routing`), renders the resolved `skills` and
  `instructions` into the live `agents/hacf/agent.yaml` at apply (recorded in
  `seat-config.rendered.json`), lists default vs override for the new keys, takes
  `--project-root`, and never copies or writes `agents/hacf/local/`. OPS.md has the recipe that
  moves a local `roles.yml` edit into `overrides.yml`.
- Seat config v2 (`hacf.seat-config/v2`, `-overrides/v2`, `-preset/v2`): `tools`, `skills`,
  `instructions` and `harness` per seat and a top-level `routing` are real keys. Unset routing is
  the shipped discovery rule. The floor is checked: `AskUserQuestion` stays denied and
  `codebase-memory-mcp` is never denied. v1 files are still read. A harness other than
  `claude-code`, `codex` or `pi` is accepted but deferred, and not applied.
- `seat-rules.py` prints each role's denies with the `tools` edits of `overrides.yml` applied.
- OpenRig 0.7.23 is in the shipped contract, accepted by the person on a compat REVIEW against 0.7.21
  (only the slice.yaml template differs: it gained an optional `execution.depends_on` block). Its
  committed baseline holds the `agy` harness files.
- The release export gate has an eleventh step, `openrig-contract`: each release row from 0.6.7
  declares `openrig_accepted`, and the gate fails when the exported contract's versions differ from
  it or hold a watcher acceptance.
- The daily OpenRig watcher self-tests HACF's checker on the installed OpenRig against an expectation
  keyed by that version, and for an accepted patch above the installed version raises one verifiable
  upgrade request to a configured operator, which it withdraws, supersedes or expires when the facts
  change. FYI rows tell the row destination before and after. The watcher never upgrades anything.
- The daily OpenRig watcher keeps an outbox: a queue row is written to disk before it is sent and
  re-sent with its request id until it lands. `openrig_watch.sh --row-check` sends one verified row,
  and the installer runs it under the unit's environment when `--row-destination` is given. New
  install options: `--upgrade-destination`, `--harness` (repeatable) and `--failure-notify-cmd`, a
  notifier the unit's `OnFailure=` runs when a watcher run fails (a desktop notification is visible
  only while you are logged in). Each run has a time budget the unit's timeout covers.
- The OpenRig compat run has a `harness` step (default `agy`, `--harness <name>` repeatable): the
  candidate's own preset parser loads each community harness's example presets in isolation, the
  launcher and runner are syntax-checked, the launcher's four cases run against a stub (no real
  `agy`, model or key), and the example files are compared byte for byte with the baselines. A
  changed, unreferenced or absent harness is REVIEW and can never be accepted, a broken one is
  FAIL, and a checker inability is SETUP-ERROR.

### Fixed
- The progress report shows replans, first-pass audit % and rework rounds as 'not recorded' for a
  release built before HACF recorded them (v0.6.4), and a CHANGELOG section without an Added heading
  uses the audited-slice fallback.
- The progress report shows the collected cases a project's pytest configuration deselects,
  lists every partly-unknown cell's reason, explains its human-decision and lore columns, and
  reports a missing Known issues heading as 'not recorded'.
- A seat's cost no longer reads unknown because of Claude Code's zero-token `<synthetic>` records; a
  record with zero tokens costs $0 whatever its model.
- Escape tracking also reads the JSON form of the `escape_tracking: v1` marker.
- The progress report showed `$0.00` for a release whose seats OpenRig had not sampled;
  `collect_metrics.py` now also reads seat sessions from OpenRig's `sessions` table, and a release
  with no telemetry record is 'not recorded'.
- `dev/telemetry/collect_metrics.py` no longer reports OpenRig's rate-limit-window samples as
  "samples without a session id", and counts the real ones in a new `samples` key.
- `seat-config-upgrade.py` replaces a live `roles.yml` that differs from HACF's only in comments
  or layout (no setting differs), and plan prints its diff; it used to refuse it.
- A project skill whose `SKILL.md` lacks a `description` or a body is no longer rendered into a
  seat's skills (OpenRig 0.7.23 would not take it); `seat-config.py` names what is missing.
- A wildcard `add_deny` that could deny `codebase-memory-mcp` is refused, and so is an
  `instructions` entry when `agents/hacf/local` itself is a symlink.
- A skill found only in `<project>/.agents/skills` is no longer rendered for a `claude-code` seat,
  which OpenRig 0.7.23 would not find.
- The daily OpenRig watcher stayed silent for a day on a setup error, and an alert whose queue row
  failed ended in `history.log` only. The first setup error of a stream is now a "checker broke" row
  (the old latch is cleared on migration), a failed row is retried by later runs, and the steps
  after the new-version step run after every outcome.
- Since v0.6.2, an OpenRig compat run on a clean tree (every daily watcher run) ended SETUP-ERROR at
  the tree step, so OpenRig 0.7.22 and 0.7.23 were never judged. The tree step now commits a clean
  tree (`--allow-empty`), and the harness provisions the clone's ledger from its own lore anchors,
  so such a run reaches `delegate-verify` instead of a false FAIL. A watcher picks the fix up only
  when its worktree is advanced.
- A compat run on an unlisted in-range OpenRig failed HACF's own oracle tests, which assumed the
  contract lists exactly the shipped versions (those tests now derive an unlisted version, and the
  exact contract check outside harness runs tolerates only the watcher entries a harness marker
  names). A watched template renamed in OpenRig (0.7.23's `sliceManifest`) or missing from the
  package was a SETUP-ERROR and is now REVIEW; a harness inability is still a SETUP-ERROR, and a
  seed run stays strict.

### Changed
- `progress-report.py` reads lore status from `lore_hypergraph.yml` at each tag; each lore file's
  Status line is used only for a tag without the graph.
- Every role denies `mcp__treg`, an external paid tool service (TJ).
- `seat-config-upgrade.py` now **refuses** a locally edited `roles.yml` (it used to keep it): the
  plan prints the diff and the `overrides.yml` lines that carry the edit, and apply writes nothing.
  `--take-hacf roles.yml` still replaces it.

### Deprecated
- `openrig/hacf/bin/view-layout.sh`: use `herdr-view.py`. Its flags, moves and exit codes are
  unchanged; it now also prints `view-layout.sh is deprecated; use herdr-view.py` on stderr.

### Known issues
- `harness` values other than `claude-code`, `codex` and `pi` are validated and reported but not
  applied in v0.6.7.
- OpenRig 0.7.23 stores a rig's `env:` only when the rig is created (`rigspec-instantiator.js:522`,
  `:1377`); restore, fresh launch and handover read that stored value, so
  `ENABLE_CLAUDEAI_MCP_SERVERS` reaches only the seats of rigs created with it. A draft for an
  upstream report is with TJ, not filed.
- `harness: agy` (or any community harness) in a seat config is validated and reported but not
  applied: OpenRig 0.7.23 has no per-seat path for a community harness, so `seat-config-upgrade.py`
  prints it as deferred.
- The progress report leaves some cells 'not recorded' by design: Lint / format / types and Licence
  check (no per-release record exists), Regressions caught (the template defines no source) and the
  recommended / not split of Decisions (the queue records no chosen option, OpenRig 0.7.23).
- The progress report's "tracked" rule skips an audited slice that has no `PROGRESS.md` at the tag,
  so the other slices alone can make its release tracked; for a release with no such file at all,
  the 'not recorded' reason can name the wrong cause. Both are left for a later release.

## [0.6.6] - 2026-11-25

### Added
- The per-seat config format: `openrig/hacf/seat-config.yml` (HACF's defaults, one entry per role;
  the tiered ones are in the H5 line under Changed), your own `overrides.yml` beside it (never written by HACF, git-ignored here,
  refused by the release export), `bin/seat-config.py` to validate and resolve default < preset <
  override, and the context guard taking its caps through it (CLI unchanged; an invalid overrides file
  means HACF's defaults plus one durable alert).
- seat-config-upgrade's plan and apply print each seat's model change against its live
  `rig ps --full` state, with the `set-model`, `set-effort` and fresh-launch commands and an Opus
  rollback (lead last), list each new default an override hides, keep a locally edited defaults
  file, print `manual (...)` where the host lacks the verbs, and never copy, back up, restore or
  checksum your `overrides.yml`.
- `collect_metrics.py` reports dollars (a dated price table, `dev/telemetry/prices.yml`) and tokens
  per slice by effective model, flags mixed-model slices, and gives per-slice size.

### Changed
- Tiered models per seat, from the v0.6.6 H5 experiment (`dev/telemetry/h5-v066.md`): build-execute
  ships `claude-sonnet-5-5` at medium effort and desk-scribe ships `claude-haiku-5-5`, because each
  held against rules fixed before the switch; orch-lead did not hold (one finding) and every other
  seat stays `claude-opus-5-5`. A seat gets a new model only through the printed commands
  (`seat-config-upgrade.py plan`); OPS.md has the apply and revert-to-Opus recipes.

### Fixed
- seat-config-upgrade's seat half skipped every seat on a live rig (it read `rig ps` without
  `--full`); it now reads `profile` from `--full`, and copy-test check 5 fails unless every HACF
  role is planned.
- Uninstall left `.agents/state/install/source.json` behind (KI-3): `HACF-uninstall.sh` now removes
  it as its last cleanup step, only when no step failed, so a failed uninstall stays re-runnable.
  The rest of `.agents/state/` is kept as before.
- `HACF-install.sh --rig` ended with GNU-only `mv -T` and failed on macOS (K10); the final move is
  now one `rename(2)` through python3, which still refuses to move into a directory that appeared
  during the install.
- `hyper_migrate.py detect` raised `legacy_field_notes` for any bullet under `## Field Notes (legacy)`,
  so an edited `activeContext.md` kept reporting a migration that never happened (D2); it now
  raises it only when Task 13 would move an entry, using the same predicate.
- An install with Claude Code selected backed up `.claude/settings.json` after its own earlier write
  (KI-6); the installer now passes `--written-sha` for bytes it wrote itself, so that needless backup
  is gone, while a user's own file is still backed up exactly once, byte-equal, and no backup is
  ever deleted.
- Uninstall left HACF's legacy `hyper-tutorial` pointers and a finished `*.hacf-v0.6.0.md` sidecar behind
  (K9): it now removes a pointer only when its sha256 is listed for that exact path in its own clone,
  and a sidecar only when it equals the HACF block still in its instruction file. Anything that may
  hold an unfinished merge is always kept and reported.

### Corrections to earlier Known issues

Released entries are not edited; these lines settle what the 0.6.5 entry promised for this release.
- Shipped: KI-3 (`source.json` surviving uninstall; Fixed, above), K10 (`--rig` and GNU `mv -T`),
  D2 (the `hyper_migrate.py detect` false positive), KI-6 (the double write of
  `.claude/settings.json`) and K9 (the legacy `hyper-tutorial` leftovers after uninstall).
- Shipped: the upgrade copy test now checks every HACF seat of a rig and works on an uncommitted tree,
  and 0.6.5's seat half, which skipped every seat on a live rig, is fixed (Fixed, above).
- Deferred, unplanned (no version is promised): KI-1 (lock files left in `.git/hacf/locks`), KI-2
  (`/hyper-update` refusing after a local-source install), KI-4 (narrowing `--ides` leaving files),
  KI-5 (`/hyper-update` without `--source`), K11 (exact ruff parity for a nested config in a
  root-excluded tree), `symbol_impact --include-tests`, the `.mcp.json` `env` block for the code-graph
  daemon, and the shared uv caches.
- Measured (F20): on the 7 eta seats, the `mcp__claude-in-chrome` and `mcp__easel` tools are gone,
  `codebase-memory-mcp` stays (17 tools), and 9 of 9 rules hold. This settles 0.6.5's "effect on live
  seats is unmeasured". No token figure is claimed.

### Known issues
- Record correction: the v0.6.0 export commit message says the S04, S08 and S11 recorded live-test
  fixtures ship unedited. They were left out of v0.6.0-v0.6.2; from v0.6.3 they ship as
  keep-list-cleaned copies (`dev/tests/fixtures/recorded/`). A pushed commit message cannot change.
  (still open in 0.6.6)
- The optional code graph (codebase-memory-mcp) shares one daemon per user unless
  `CBM_RUNTIME_DIR` differs between runs; HACF's own runs are isolated, the daemon Claude Code launches
  from `.mcp.json` is not. (still open in 0.6.6)
- codebase-memory-mcp caps a project name at 128 characters, and the name is derived from the
  checkout path; the workaround is a shorter checkout path. (still open in 0.6.6)
- The repository moved to `https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework`. An
  existing install keeps its pinned old URL and works through GitHub's redirect; to move it, run
  `/hyper-update --upstream-url https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git`.
  (still open in 0.6.6)
- Of the 127 test functions left out of the public v0.6.2 tree, 110 are back in the public suite.
  The 17 that read HACF's private development records stay in `dev/tests/private_records/`, the one
  test directory still left out of every export. (still open in 0.6.6)
- A rig `env:` key reaches a running rig only at a whole-rig `rig down`/`rig up`
  (`openrig/hacf/OPS.md`); OpenRig has no verb that updates a running rig's env. (still open in 0.6.6)
- Live seats' first-turn context (about 48.7k-50.4k tokens) is above the headless probe's
  36.9k-41.9k; not re-measured in 0.6.6. (still open in 0.6.6)
- The hacf OpenRig team's context guard and its checkpoint watchdogs are armed on the rig named
  `hacf` only; arming another team is the operator's step at that team's own upgrade. (still open in
  0.6.6)
- H5 (directional, not a release gate; `dev/telemetry/h5-v066.md`): build-execute (`claude-sonnet-5-5`,
  medium) and desk-scribe (`claude-haiku-5-5`) held and ship as tiered defaults; orch-lead did not hold
  (one finding) and stays on `claude-opus-5-5`, like every other seat. At n = 8 non-mixed slices (9
  measured), median list-price dollars per slice were 3.06 against B2's 6.08 and median tokens
  14,083,273 against 18,891,038; the -35% to -55% target is not met on this window (median tokens
  -16.6%, cost-weighted -22.9%, over the 9 slices). Dollars are API list-price equivalents, not what
  the account is billed; v0.6.6 has no post-release escape window and B2 had one.
- The shipped model ids (`claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5`) age while HACF is
  parked; override a retired id in `overrides.yml`.
- A fresh `rig up` runs each seat on the harness default until the commands seat-config-upgrade
  prints are run.
- Restoring to 0.6.5 leaves `overrides.yml` in place, which 0.6.5 does not read.
- seat-config-upgrade's `set-model`, `set-effort` and `ps --full` verbs were measured on OpenRig 0.7.16
  and 0.6.13 by the builder; the audit did not re-run that measurement, and no other OpenRig version
  was tested (the plan probes each verb at run time and prints `manual (...)` where one is missing).
- Privacy scan of the working tree: 0 of the 10 v0.6.6 audits recorded an `unknown` result (all 10
  ran it, exit 0).

### Known limits
- KI-6: after HACF writes `.claude/settings.json`, the installer hashes the file on disk, not the
  bytes the firewall wrote. If the user saves the file in the milliseconds between that write and the
  hash, during an install, HACF's next write in the same install can skip the backup of that one
  save. The user's original file is always backed up first.
- K9: the uninstaller decides in its plan and removes without hashing again. If a legacy
  `*.hacf-v0.6.0.md` sidecar is edited between the confirmation and the removal, inside one
  uninstall run, that edit is lost.

## [0.6.5] - 2026-11-24

### Added
- `openrig/hacf/bin/seat-config-upgrade.py` (plan/apply/restore): carries HACF's seat configuration
  into a live rig spec. It changes files only (backup outside the spec library, atomic writes under
  a lock, one rig.yaml key, auto-restore on any failure after the backup, a refusal on a dirty
  source checkout) and prints the seat half, orch-lead last.
- A per-project daemon runtime for HACF's own code-graph runs: every codebase-memory-mcp process
  `hyper_codegraph.py` starts gets `CBM_RUNTIME_DIR=$XDG_RUNTIME_DIR/hacf-cbm/<12-hex>` (or
  `${XDG_CACHE_HOME:-~/.cache}/hacf-cbm/run/<12-hex>`), created 0700; a caller's own value is kept,
  and `CBM_CACHE_DIR` is untouched. A dir codebase-memory-mcp cannot use (socket path over 107
  bytes, a parent that is not yours or is group/world-writable) refuses the run before any process
  starts, never falling back to the shared `/tmp/cbm-daemon-<uid>/`; `doctor` reports the dir.
- `dev/scripts/upgrade_copy_test.py` (dev only, not shipped): the upgrade copy test. `fixture`
  mode (offline, the auditor's run) and `live` mode (the lead's run on throwaway copies, inside
  bwrap) both run nine checks: originals hashed and copied, a pinned `--source --ref` install,
  `.mcp.json` byte-identical across two installs, seat-config plan/apply, `rig spec validate`,
  a byte-identical restore, untouched originals and an unchanged `rig specs ls`.
- `hyper_codegraph.py index --if-stale` re-indexes only when the recorded commit is not HEAD;
  hyper-execute runs it before the blast-radius updater when a code-graph backend is selected.
- `--no-mcp-entry` (recorded as `mcp_entry: skipped`, kept by a plain re-run) and `--mcp-entry`:
  the installer writes no codebase-memory-mcp MCP entry and leaves an existing one alone.
- The saved installer copy (C2): each install writes `.agents/state/hacf/installer/HACF-install.sh`
  and records its `installer_sha256`; run from there, the copy refuses on a sha256 mismatch. The
  uninstaller removes it.
- `release_export.py scan --tree worktree|head` and check.sh's privacy-scan leg (exit 3 = unknown
  without the per-user digest file).

### Changed
- The per-role caps in `openrig/hacf/caps.yml` follow TJ's 2026-11-24 approval: architect 190000,
  red-team 190000, resolve 270000, execute 330000, audit 220000; lead and scribe unchanged.
- Every role in `openrig/hacf/roles.yml` also denies `mcp__claude-in-chrome` and `mcp__easel`
  (bare server denies), and OPS.md says a rig `env:` key reaches a running rig only at a whole-rig
  `rig down`/`rig up`, the operator's or the person's call (`rig seat launch --fresh` does not re-read it).
- The context guard's host line is documented per rig: one `--rig <rig>` line per HACF rig on the
  host, with `hacf` as the worked example; `--arm-checkpoints` arms only that rig's seats.

### Fixed
- A plain installer re-run keeps the recorded pin (C3): `source.json` records the resolved
  full sha, never `HEAD`, and a recorded commit missing from the checkout refuses and names `--ref`.
- A remote install records the fetched installer's version, not the running script's (C4).
- An upgrade no longer flips the code-graph MCP entry: the recorded install method decides its form, an
  older HACF uvx entry is upgraded, a user's entry is kept, and the report names what was written.
- The lore gate allows `propose`, `note` and the read verbs (`list`, `show`, `validate`, `graph`)
  in a shell heredoc, and denies hyper_lore behind an unknown wrapper (`flock`, `script`,
  `unbuffer`, …): a command that mentions hyper_lore and is neither a resolved call nor a read of
  data by one of a closed list of programs is unresolved. A Python body's bypass reason names
  `--body-file`.
- The lore gate denies hyper_lore fed its verb through `xargs`/`parallel` (stdin, a replace
  string or `:::`), and a read of hyper_lore that a leading assignment, an earlier command in the
  same call or a path could redirect to another program. It no longer denies HACF's own pytest and
  ledger commands that name hyper_lore files: a closed list (`pytest`, `python3 -m pytest`, and
  `python3 .agents/scripts/` + `hyper_ledger.py`, `hyper_spec.py`, `hyper_scope.py`,
  `hypergraph_updater.py`, `archive_specs.py`, optionally after `uv run --locked`).
- `view-layout.sh` arranges only the Herdr workspace labelled with its `--rig`, so a second HACF
  team on the same Herdr is left alone; no such workspace moves nothing.
- A read-only updater run (`--from-diff` without `--apply`) no longer re-marks lore stale: it
  writes no lore and reports the ids it would mark as `lore_stale_candidates`.
- The lore gate no longer takes `--` or an option as hyper_lore's verb: a call resolves only when its
  first argument is exactly one of hyper_lore's subcommands (`-h`/`--help` alone gets no decision).

### Known issues
- Record correction: the v0.6.0 export commit message says the S04, S08 and S11 recorded live-test
  fixtures ship unedited. They were left out of v0.6.0-v0.6.2; from v0.6.3 they ship as
  keep-list-cleaned copies (`dev/tests/fixtures/recorded/`). A pushed commit message cannot change.
  (still open in 0.6.5)
- The optional code graph (codebase-memory-mcp) shares one daemon per user unless
  `CBM_RUNTIME_DIR` differs between runs. (still open in 0.6.5 for the daemon Claude Code launches
  from `.mcp.json`; HACF's own runs are now isolated, below)
- codebase-memory-mcp caps a project name at 128 characters, and the name is derived from the
  checkout path; the workaround is a shorter checkout path. (still open in 0.6.5)
- The repository moved to `https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework` (the
  old URL is in the 0.6.3 Known issues). An existing install keeps its pinned old URL and works
  through GitHub's redirect; to move it, run
  `/hyper-update --upstream-url https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git`.
  (still open in 0.6.5)
- Of the 127 test functions left out of the public v0.6.2 tree, 110 are back in the public suite.
  The 17 that read HACF's private development records stay in `dev/tests/private_records/`, the one
  test directory still left out of every export; on a public clone that directory skips with one
  stated reason. (still open in 0.6.5)
- The code-graph daemon that Claude Code launches from `.mcp.json` is still shared across
  projects. HACF's own code-graph runs are isolated per project (Added, above), and an upgrade
  leaves `.mcp.json` unchanged. A host-neutral `env` block for that entry is planned for v0.6.6:
  codebase-memory-mcp does not create a missing runtime directory, so the directory must exist
  before Claude Code starts the server.
- Shared uv caches, the other half of 0.6.4's per-project item: not addressed in v0.6.5 and not
  yet scheduled. (still open in 0.6.5)
- The six v0.6.0 known issues (lock files left in `.git/hacf/locks`, `/hyper-update` refusing after
  a local-source install, `source.json` surviving uninstall, narrowing `--ides` leaving files,
  `/hyper-update` without `--source`, the double write of `.claude/settings.json`): scheduled for
  v0.6.6.
- K9 (the `hyper-tutorial` pointers, the empty `.claude/commands/hyper-tutorial.md` and the
  `AGENTS.hacf-v0.6.0.md` sidecar left after uninstall), K10 (`--rig` uses the GNU-only `mv -T`),
  K11 (exact ruff parity for a nested config in a root-excluded tree) and D2 (a
  `hyper_migrate.py detect` false positive on an edited `.agents/memory/activeContext.md`):
  scheduled for v0.6.6.
- `symbol_impact --include-tests` has no defined semantics yet: scheduled for v0.6.6.
- The role table's bare denies of `mcp__claude-in-chrome` and `mcp__easel` ship, but their effect
  on live seats is unmeasured: the headless `claude -p` probe attaches neither server (0 names,
  and a deny leaves server entries listed anyway). It is checked only after the operator applies
  the roles, by a live tool-list probe per seat. No token saving is claimed.
- A rig `env:` key reaches a running rig only at a whole-rig `rig down`/`rig up`
  (`openrig/hacf/OPS.md`); OpenRig has no verb that updates a running rig's env.
- Live seats' first-turn context (about 48.7k-50.4k tokens) is above the headless probe's
  36.9k-41.9k after 0.6.4's start-up trim; the gap is probably host MCP servers, onboarding and a
  live `CULTURE.md`. Not re-measured in 0.6.5.
- The hacf OpenRig team's context guard and its checkpoint watchdogs are armed on the rig named
  `hacf` only. The host line is now documented per rig; arming it on another team is the
  operator's step at that team's own upgrade.
- B2 (directional, not a release gate): not met by the collector's rule, n = 20 slices since the
  roles-v2 rollout against B0's 29. The median tokens per slice fell 41.4% (32.2M to 18.9M;
  cost-weighted -41.2%), past the 30% target, but escapes per slice read 0 for B0 and 0.2 for B2,
  and B0's 0 predates the escape counter that sees audit FAILs and builder halts, so that half is
  not like for like.
- Privacy scan of the working tree: no v0.6.5 audit recorded an `unknown` result (13 of 14 audits
  ran it; the first predates the scan leg).

## [0.6.4] - 2026-11-24

### Changed
- `hyper_spec.py lint` gains `--min-confidence N` (a compiled slice whose Confidence Mandate
  records no score or one below N fails) and `--mission-close` (a finished mission's `NOTES.md`
  needs a `## Lore capture` section with at least as many stories as replans); the NOTES template
  carries the section, resolve, the lead and the scribe run the checks, and default `lint` is
  unchanged.
- The hacf OpenRig team: new `bin/view-layout.sh --rig <rig> [--dry-run]` arranges a team's Herdr
  view into the recommended rows (the lead on top, the build row, the spec row), idempotently and
  from the rig's own seats; `docs/OpenRig-Team.md` gains "Recommended team view" (Herdr-only).
- The `commit-msg` hook adds the `Agentic-Framework: HACF v<version> (<mode>)` line when it is
  missing on a commit that has the `Agentic-Assistant` line (mode `OpenRig` under
  `OPENRIG_SESSION_NAME`, else `manual`; version from the manifest, else `HACF_VERSION`; nothing
  when the version is unknown); a commit without that line is never touched.
- The hacf OpenRig team: `bin/context-guard.sh` now requires `--caps openrig/hacf/caps.yml`, a
  per-role cap table checked against `roles.yml` (`HACF_CTX_MAX` is no longer read), never restarts
  a `planned_handover` role, accepts a stale usage sample only when the seat's transcript has not
  grown since it, and lets untracked `spec/active/` work block only the spec seats (the top-level
  `FAILED_WORKFLOWS.md` blocks nobody); the host line gains `--caps`.
- The hacf OpenRig team: `context-guard.sh --arm-checkpoints` keeps exactly one
  `context-usage-threshold` watchdog per running build-execute (at its cap) and orch-lead (at its
  handover cap), registering a missing one and never stopping one; `guidance/execute.md` and
  `guidance/lead.md` say what to do when it fires (a checkpoint note, never a commit; the lead
  relaunches the builder).
- Dev tooling only: every `dev/telemetry/collect_metrics.py` scorecard gains `context`, per seat and
  occupant the turns and peak resident context of its main transcript (`--context-series` writes
  each turn), and the new read-only `dev/telemetry/recompute_caps.py` proposes per-role caps from
  them for TJ's OK; `openrig/hacf/caps.yml` lists its re-evaluation triggers.
- Dev tooling only: the telemetry escape counter adds builder halts (`blocked` progress events) and
  `HACF-Replan-Of` replan commits to the audit FAILs, reports `escape_parts` per slice, backfills
  FAILs from loop records given with `--loops-dir`, and compares as `met (escapes unknown)` when a
  side's escapes are unknown; the audit, execute and resolve skills and guidance now record
  `audit_failed`, `blocked` (with the `escape_tracking: v1` marker) and the replan trailer.
- The hacf OpenRig team: `CULTURE.md` now carries the secrets ban itself (it reached seats only through
  OpenRig's default culture). Dev tooling only: `dev/telemetry/startup_probe.py`, a pinned `claude -p`
  probe of one role's turn-1 context and tool, MCP and skill inventory under each trim lever, its
  before table `dev/telemetry/startup-baseline-v064.md`, and a test that every role's delivered
  start-up set carries the required rules.
- The hacf OpenRig team: roles v2 (`roles.yml`) denies, per role, the built-in tools and MCP servers
  the start-up probe listed that the role file and its stage skill never name (never
  `codebase-memory-mcp`; `seat-rules.py --check` now accepts a bare `mcp__<server>` deny and refuses
  one of codebase-memory-mcp); duplicated start-up passages are removed; `OPS.md` applies roles v2
  and adds a compaction probe and the live turn-1 reading; `dev/telemetry/startup-after-v064.md`
  measures the result.
- The hacf OpenRig team: `rig.yaml` gains one rig-level key, `env: {ENABLE_CLAUDEAI_MCP_SERVERS:
  "false"}`, which turns off the account's claude.ai MCP connectors for every seat (project MCP
  servers such as `codebase-memory-mcp` stay), proven by `rig spec validate` on OpenRig 0.7.16 and
  0.7.21; `CULTURE.md` again carries the lore rule. Dev tooling only: the OpenRig compat check
  validates the shipped `rig.yaml` (a FAIL `spec-invalid` when it is refused) and runs its oracle
  from the moved `test_contract_pin_upgrade.py`.
- The two-line agent commit footer (`Agentic-Assistant: ...` then `Agentic-Framework: HACF v<version>
  (<mode>)`) is in the hacf team's `CULTURE.md`, the execute, audit and scribe guidance and the
  hyper-publish examples; the `commit-msg` hook now deletes `Claude-Session:` from an agent commit
  instead of adding a third footer line, and no longer uses GNU-only `sed -i`.

### Fixed
- Dev tooling only: the tests that need HACF's private records, the raw recorded captures or the
  private release history moved to `dev/tests/private_records/`, so a public clone's test run has no
  failure and no unexpected skip.

### Known issues
- Record correction: the v0.6.0 export commit message says the S04, S08 and S11 recorded live-test
  fixtures ship unedited. They were left out of v0.6.0-v0.6.2; from v0.6.3 they ship as
  keep-list-cleaned copies (`dev/tests/fixtures/recorded/`). A pushed commit message cannot change.
  (still open in 0.6.4)
- The optional code graph (codebase-memory-mcp) shares one daemon per user unless
  `CBM_RUNTIME_DIR` differs between runs. (still open in 0.6.4)
- codebase-memory-mcp caps a project name at 128 characters, and the name is derived from the
  checkout path; the workaround is a shorter checkout path. (still open in 0.6.4)
- The pinned re-run of a local-checkout install (D1) protects only re-runs of a v0.6.3 or later
  installer. A checkout moved to an older commit runs that commit's older installer; the saved
  installer copy planned for v0.6.4 is the cure. (still open in 0.6.4; the saved copy is now
  scheduled for v0.6.5, below)
- The repository moved to `https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework` (the
  old URL is in the 0.6.3 Known issues). An existing install keeps its pinned old URL and works
  through GitHub's redirect; to move it, run
  `/hyper-update --upstream-url https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git`.
  (still open in 0.6.4)
- Of the 127 test functions left out of the public v0.6.2 tree, 110 are back in the public suite.
  The 17 that read HACF's private development records stay in `dev/tests/private_records/`, the one
  test directory still left out of every export; on a public clone that directory skips with one
  stated reason. (still open in 0.6.4)
- A seat-config upgrade path for an existing team: scheduled for v0.6.5 (mission
  `v064-upgrade-path`). Until then the operator applies seat changes per seat
  (`openrig/hacf/OPS.md`).
- The saved installer copy, the cure for "D1 protects only re-runs of a v0.6.3 or later
  installer": scheduled for v0.6.5 (mission `v064-upgrade-path`).
- F2, a plain installer re-run rewrites `source.json`'s pinned ref: scheduled for v0.6.5 (mission
  `v064-upgrade-path`).
- An install without `--source` records the script's own version, not the version it fetched:
  scheduled for v0.6.5 (mission `v064-upgrade-path`).
- A per-project code-graph runtime and shared uv caches: scheduled for v0.6.5 (mission
  `v064-upgrade-path`).
- The six v0.6.0 known issues (lock files left in `.git/hacf/locks`, `/hyper-update` refusing after
  a local-source install, `source.json` surviving uninstall, narrowing `--ides` leaving files,
  `/hyper-update` without `--source`, the double write of `.claude/settings.json`): scheduled for
  v0.6.5 (mission `v064-upgrade-path`).
- K9 (the `hyper-tutorial` pointers, the empty `.claude/commands/hyper-tutorial.md` and the
  `AGENTS.hacf-v0.6.0.md` sidecar left after uninstall), K10 (`--rig` uses the GNU-only `mv -T`),
  K11 (exact ruff parity for a nested config in a root-excluded tree) and D2 (a
  `hyper_migrate.py detect` false positive on an edited `.agents/memory/activeContext.md`):
  scheduled for v0.6.5 (mission `v064-upgrade-path`).
- The four code-graph items (the install report names uvx while the uv-tool method writes a bare
  command; no stage re-indexes the code graph; no flag skips the `.mcp.json` entry;
  `symbol_impact --include-tests`): scheduled for v0.6.5 (mission `v064-upgrade-path`).
- The hacf OpenRig team's context guard (`bin/context-guard.sh`) and its checkpoint watchdogs are
  enforced on the rig named `hacf` only; per-rig guard and checkpoint lines for other teams are
  scheduled for v0.6.5 (mission `v064-upgrade-path`).
- B2, interim and not a verdict: n = 4 slices since the roles-v2 rollout (OPR.99.0.9.1-.4; .9.1
  only partly in the window), median 7.96M tokens per slice (cost-weighted 1.24M). The B2 verdict
  waits for n >= 8.

## [0.6.3] - 2026-11-24

### Added
- The hacf OpenRig team: `openrig/hacf/roles.yml`, a per-role table of the native tools each seat
  denies (every role denies `AskUserQuestion`), `bin/seat-rules.py`, which turns a role into
  `rig seat set-permissions --rules` JSON and checks the table against `agent.yaml` and a key-free
  `rig.yaml`, and `OPS.md`, the per-seat apply, probe and rollback recipe.
- Dev tooling only, not installed into projects: `dev/release_export.py`, which builds HACF's public,
  product-only release commits, leaving out HACF's private development records and the tests and
  recorded captures that cannot be published, and gates each one before a push: a privacy scan,
  `check.sh` and ruff on a public-only clone, install checks and the code-graph contract. It never
  pushes.
- Dev tooling only: `dev/scripts/host_gate.py`, one per-user heavy-job lock and memory floor taken by
  `check.sh`, every repository pytest, the release gate and the OpenRig compat and probe runs; a
  wait that runs out exits 75 (retry later).
- Dev tooling only: `dev/telemetry/collect_metrics.py`, a read-only collector that writes per-seat,
  per-slice token scorecards from a snapshot copy of the OpenRig database and the seats' JSONL usage
  numbers, and computes the B0/B1 token-reduction metric.
- The hacf OpenRig team: `openrig/hacf/bin/context-guard.sh`, a fail-closed, dry-run-by-default
  guard that restarts at most one idle, non-lead seat over 150k input tokens per run, only at a
  durable point, logging every decision and telling the lead.

### Changed
- The framework is renamed to the Hypergraph Agentic Coding Framework (still HACF) in the docs, agent instructions, install templates, skills and installer banners.
- README.md describes Coding 4.1: the five stages run by hand (Coding 4.0) or as an OpenRig team, with the same human gates, and its diagram now shows the deterministic state scripts. `dev/tests/docs/test_readme_mermaid.py` allows one optional `HACF state` subgraph.
- Dev tooling only: `dev/scripts/check.sh` prints a short summary per leg (status, counts, failing ids
  with the tail of their tracebacks, the log path) instead of the full pytest and ruff output, runs
  every leg after a failure and exits 1 at the end, and keeps the full logs and junit XML in
  `${XDG_STATE_HOME:-~/.local/state}/hacf/check-logs` (newest 10 runs; `HACF_LOG_DIR`,
  `HACF_CHECK_VERBOSE=1`); the release gate's console prints the same per-leg summary.
- `HACF-install.sh` re-runs of a local-checkout install are pinned: a plain re-run (or `--source=` the
  recorded checkout without `--ref`) reinstalls `installed_commit` and refuses, writing nothing, when
  the checkout's HEAD has moved; `--ref` alone now moves within the recorded checkout.
- Dev tooling only, not installed into projects: the OpenRig watcher's notifier
  (`HACF_WATCH_NOTIFY_CMD`), its queue-row destination (`HACF_WATCH_ROW_DESTINATION`) and the
  compat evidence root (`HACF_COMPAT_EVIDENCE_ROOT`) are now settings with neutral defaults: no
  notifier, no row, and `${XDG_STATE_HOME:-~/.local/state}/hacf/evidence`. DEVELOPMENT.md
  describes them.
- The shipped `.agents/` scripts pass ruff (no new ignores), `PRDs/` is excluded from ruff, and
  `dev/scripts/check.sh` and the release gate lint every tracked Python file on a fresh clone.
- Dev tooling only: the tests that read HACF's private development records moved to
  `dev/tests/private_records/`, which skips with one stated reason on a checkout without them; the
  rest of those test files are public again.
- Dev tooling only: the recorded live captures are cleaned by a keep-list normaliser
  (`dev/tests/fixtures/normalize_recorded.py`) and read through a placeholder-substituting loader;
  the raw captures live only in the never-exported `dev/tests/fixtures/recorded-raw/`.
- Dev tooling only: the release-export profile is `hacf.release-export/v2`. It names no private
  value or digest of one: private literals live only in the untracked digest file, which gains
  `window` and `regex` lines (`release_export.py digest-line` prints one from stdin) and must hold
  every `required_digest_labels` label (the lead appends them before a gate).
- Dev tooling only: the export scan fails on a raw-only capture key (`scan.strict_keys`) anywhere,
  the plan's verify-push line carries `--report-root`, and host names and private paths are gone
  from the current OpenRig docs.

### Fixed
- Dev tooling only: the OpenRig watcher installer (`dev/openrig_watch_install.sh`) fast-forwards an
  existing watcher worktree to `--base` on a re-install, and refuses one with uncommitted changes or
  with commits not in `--base`; it used to leave the worktree where it was.
- Dev tests only: the pseudo-terminal helper in `dev/tests/installer/test_uninstall.py` types an
  answer only at a known prompt, so a load stall mid-run no longer types a stray newline that a
  later prompt reads as "No".
- `hyper_ledger.py ensure-node` no longer gives a path a second owner by accident: `--from-spec`
  skips (and names the owner of) a touch another node's `files` already cover, and `--files`
  refuses such a new path (exit 2, nothing written) unless the new `--allow-shared` is given.
- `hyper_codegraph.py`: the adapter no longer runs an unpinned backend found on PATH or recorded in the
  state file; each candidate must report codebase-memory-mcp 0.12.1, and a project name over 128
  characters is refused with the cause and the workaround (a shorter checkout path).

### Known issues
- Record correction: the v0.6.0 export commit message says the S04, S08 and S11 recorded live-test
  fixtures ship unedited. They were left out of v0.6.0-v0.6.2; from v0.6.3 they ship as
  keep-list-cleaned copies (`dev/tests/fixtures/recorded/`). A pushed commit message cannot change.
- The optional code graph (codebase-memory-mcp) shares one daemon per user unless
  `CBM_RUNTIME_DIR` differs between runs.
- codebase-memory-mcp caps a project name at 128 characters, and the name is derived from the
  checkout path; the workaround is a shorter checkout path.
- The pinned re-run of a local-checkout install (D1) protects only re-runs of a v0.6.3 or later
  installer. A checkout moved to an older commit runs that commit's older installer; the saved
  installer copy planned for v0.6.4 is the cure.
- The repository moved from `https://github.com/tjmustard/Hypergraph-Coding-Agent-Framework` to `https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework`. An existing install keeps its pinned old URL and
  works through GitHub's redirect; to move it, run
  `/hyper-update --upstream-url https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git`.
- Of the 127 test functions left out of the public v0.6.2 tree, 110 are back in the public suite.
  The 17 that read HACF's private development records stay in `dev/tests/private_records/`, the one
  test directory still left out of every export; on a public clone that directory skips with one
  stated reason.

## [0.6.2] - 2026-11-23

### Added
- `.agents/config/openrig_contract.yml`, the list of OpenRig versions HACF calls (0.6.13, 0.7.16
  and 0.7.21) and one probed range, 0.7.x with x at least 16. A project's copy changes only with a
  HACF upgrade. A missing, malformed or unknown-keyed file is dropped whole for the compiled-in list,
  with a warning; it never widens what HACF calls.
- The range probe: an unlisted rig inside the range is called only after an isolated probe of
  exactly what HACF delegates (`scope audit`, `slice ls`, `proof add` over HACF-rendered files)
  passed against `.agents/config/openrig_probe_expect.json`. Results are cached machine-wide under
  `${XDG_CACHE_HOME:-~/.cache}/hacf/rig-probe/` (a pass for 7 days, anything else for 24 hours).
  Every doubt means HACF writes the files itself and warns; verify reports a probe still running as
  `skipped: rig probe pending (<version>)`. See docs/OpenRig-Team.md for what the probe cannot cover.
- `hyper_openrig.py contract show [--json]` and `contract probe [--reprobe] [--json]`.
- The opt-out: `openrig.range_probe: false` in `.agents/local/hacf.yml` turns the range off.
- Dev only, not installed into projects: `dev/openrig_compat.sh <version>`, which decides in
  minutes, in isolation, whether an OpenRig version works with HACF against committed baselines
  (PASS, FAIL, REVIEW, SETUP-ERROR, SEED); and a daily watcher, `dev/openrig_watch.sh` with its
  systemd user timer and `dev/openrig_watch_install.sh`, which runs it on each new `latest`
  release, auto-accepts a passing patch of an accepted minor as a local commit on
  `swimlane/openrig-accepted` (never pushed), and files a decision row for every other verdict
  (REVIEW, a final FAIL, a PASS it did not accept). A run without a verdict (SETUP-ERROR, SEED,
  or deferred by the memory gate) files no decision row; the second such run in a row sends one
  update.
  DEVELOPMENT.md describes both, and the watcher's limit: it sees only npm's `latest` dist-tag.

### Changed
- HACF accepts OpenRig 0.7.21: with it on PATH, HACF calls `rig`. It passed HACF's live contract
  test; its compat run against 0.7.16 was REVIEW (two watched files changed), and those changes were
  read and signed off in `dev/tests/fixtures/openrig-compat/0.7.21/REVIEW.md`.
- `RIG_VERSION`, the version an explicit `npx` download fetches, is now 0.7.21: the highest
  person-accepted version in the contract file.
- The mirror key `openrig.accepted_versions` is gone from `orchestration.yml` and the engine's
  defaults; the contract file replaces it. An older `orchestration.yml` that still carries it is
  accepted, the key is ignored, and one message says so.
- Dev tooling only; nothing here is installed into projects. `dev/scripts/check.sh` runs both
  legs under pytest-xdist with a memory-headroom worker budget (`HACF_TEST_WORKERS` overrides it),
  and test temp moved off `/tmp`, to `HACF_TEST_TMPDIR` or an inherited `TMPDIR` that is neither
  `/tmp` nor a tmpfs. `pytest --lf` works, and `run_in_pty` and the timeout test were fixed.
  Measured: check.sh wall about 946 -> 122 s (B/A 0.13), CPU user+sys +19-20%, peak memory about
  3.4 -> 2 GB, with `--dist loadgroup` (about 14% faster per leg than `load`).

## [0.6.1] - 2026-11-22

### Added
- HACF's OpenRig team spec, under `openrig/hacf/`: a seven-seat team (lead, architect, red team,
  resolve, builder, auditor, scribe) that runs the HACF pipeline one stage per seat.
- `HACF-install.sh --rig[=DEST]` copies that spec to `openrig/hacf/` in your project (or to DEST)
  and renames its rig `<slug>-hacf`. It runs only when you ask: an interactive install asks once
  (default No), and `-y` never copies without the flag. It never writes over an existing path.
  The copy is yours: the manifest records it (`rig_spec`), an upgrade never refreshes it and
  uninstall leaves it in place.
- `openrig/hacf/bin/brief.sh`, which writes a stage brief from the team's run record.
- `docs/OpenRig-Team.md`: starting a per-project team, what a recorded launch did, the files a
  launch leaves in the project root, and recovery with `rig up <name> --existing`.

### Changed
- HACF accepts OpenRig 0.6.13 and 0.7.16: with either one on PATH, HACF calls `rig` (for example
  `rig proof add`). Another 0.6.x or 0.7.x is not called; HACF writes its files itself and prints a
  warning. Both versions passed HACF's live contract test on a real rig (0.7.16 installed, 0.6.13
  run through `npx`), after a recorded comparison of the two (mission R18d).
- New missions' `mission.yaml` now records `metadata.name`, as OpenRig 0.7.16 writes. Upgrading
  never rewrites an existing mission.yaml: missions created before 0.6.1 keep their file as it is,
  without `metadata.name`, and HACF never adds it.
- With OpenRig 0.7.16, `rig scope slice ls --state shipped` also lists HACF's audited slices
  (`status: done`); 0.6.13 lists them only under `all`. HACF does not read this listing.

### Fixed
- An install that selects any IDE now also installs `AGENTS.md` (HACF block only; your own text is
  never changed), because every IDE's instructions point at it. A plain upgrade adds it to an
  existing install.
- The installed `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` now point at HACF's published README
  instead of a local `README.md` that was never installed. A plain upgrade refreshes the HACF
  block and leaves your own text alone.
- The resolve and SOP skills now name `archive_specs.py --paths … --label <mission>`, the
  documented form. The troubleshooting skill's archive step now passes `--label` too.
- The pre-commit hook now honours your ruff config's `exclude` and `extend-exclude` for staged files
  (`--force-exclude`), and `git config hacf.lint false` skips its lint step.

### Known issues
- A saved v0.5.x `HACF-uninstall.sh` kept outside the project root deletes
  `<project>/openrig/hacf/` when run. The installer refuses `--rig` while one is at
  `./HACF-uninstall.sh`, but it cannot see a copy saved elsewhere. Delete such copies; never run
  them.
- A launched team was checked only with the shipped `permission_policy: builtin:yolo` and
  `non_interruptive: true` on OpenRig 0.7.16. The default permission mode is untested.
- `rig down --delete` leaves `.claude/plugins/openrig-core/` and `.claude/settings.local.json` in
  the project root (see `docs/OpenRig-Team.md`).
- A nested ruff config inside a root-excluded tree is still linted by the pre-commit hook; give it
  its own exclude, or set hacf.lint false (see `docs/Troubleshooting.md`).

## [0.6.0] - 2026-11-21

### Upgrade warnings (read first)
- **Delete any saved copy of `HACF-uninstall.sh`** from your project and your downloads before you upgrade. A saved v0.5.x uninstaller deletes every project file whose path also exists in the HACF repository, including same-named user files such as `.gitattributes` (your Git LFS rules), `lore_hypergraph.yml`, `SPEC.md`, `project.yaml`, `workspace.yaml` and `docs/*`. Use the uninstaller the v0.6.0 installer writes, or download it fresh.
- **Re-download the installer.** A saved v0.5.x `HACF-install.sh` aborts at `tests/`, because the v0.6.0 repository no longer tracks `spec/` or `tests/`.
- **Releases are unsigned.** v0.6.x ships no release signatures, and any future signing will be SSH-only. A v0.5.x `git verify-commit` cannot verify SSH signatures without a pinned allowed-signers file, so the v0.5.x GPG update path (and its directory-replacing `rmtree`) stays disarmed. Never import a release key globally.
- **URL and branch overrides need flags.** `HACF-install.sh --repo-url/--branch`, `HACF-uninstall.sh --repo-url/--branch` and `/hyper-update --upstream-url/--ref` are the only override paths; the `HACF_REPO_URL`, `HACF_BRANCH` and `HACF_UPSTREAM_URL` environment variables are honored only with `HACF_TEST=1`, and non-`https://` URLs are refused without it. Upstream signers are pinned locally on first trust; a change to the pinned set needs interactive consent.
- **Unattended unsigned updates are refused.** `--allow-unsigned` is refused without a TTY or under `HACF_AUTONOMOUS=1`, so CI cannot update HACF while releases are unsigned. `hyper_migrate.py apply` likewise needs a TTY confirmation, or `--yes` outside `HACF_AUTONOMOUS=1`.

### Added
- **OpenRig 0.6.13 topology**: missions and slices at the repo root (`SPEC.md`, `project.yaml`, `workspace.yaml`, `missions/<mission>/slices/<slice>/`), read, linted and tracked by `hyper_spec.py`; scopes with root-collision detection and a DAG in `hyper_scope.py`; `hyper_openrig.py` as the single pinned manifest writer (dot-IDs, prefix `OPR`). Legacy flat MiniPRDs are still read through v0.6.x.
- **Component ledger** `hyper_ledger.py`: the single, locked, validated, non-regressive writer of `architecture.yml` (`hacf.ledger/v2`), with edge editing and JSON review patches that replace the free-text audit rewrite. `hypergraph_updater.py` keeps its positional CLI and gains `--from-diff`.
- **Optional code graph** `hyper_codegraph.py` (codebase-memory-mcp 0.12.1, off by default).
- **Lore hypergraph**: `lore_hypergraph.yml`, immutable `lore/canon/` bodies, generated `lore/MAP.md` and `lore/CANON.md`, `hyper_lore.py`, and `/hyper-lore-approve` for one-node-at-a-time approval by the user.
- **Agent firewall** `hyper_firewall.py`: renders `.agentignore` into Claude Code deny and ask rules plus a PreToolUse hook for gated lore verbs, and best-effort ignore files for Windsurf, Cursor and Roo. It is not a security boundary outside the sandboxed engine.
- **Headless engine** `hyper_engine.py` and `hyper_executors.py`: one git worktree and a sandboxed `claude -p` per slice, an integrator, and an explicit `land` that refuses ledger drift; `hyper_gates.py` for pipeline gates.
- **Migration** `hyper_migrate.py` (`detect`, `plan`, `apply`, `rollback`): journaled, backed up and byte-identically reversible conversion of v0.5.x specs (archived ones included) into missions.
- **Uninstaller** driven by `.agents/hacf-manifest.json`, with a legacy-hash fallback for v0.5.x installs.
- **Install from a local checkout**: `HACF-install.sh --source=PATH [--ref=REF]` installs one commit of a local HACF git checkout (never its working tree; uncommitted changes warn and are not installed), and `HACF-uninstall.sh --source=PATH [--ref=REF]` takes HACF's code from one. `--ref` defaults to `HEAD` for the installer and to the manifest's `installed_commit` for the uninstaller; `--source` cannot be combined with `--repo-url` or `--branch`.
- **Manifest `source` and `ides` keys**: `.agents/hacf-manifest.json` records `source` as `{kind: local, commit}` or `{kind: remote, url, branch, commit}` (never a local path) and the IDE selection as `ides`.
- **Per-machine source file** `.agents/state/install/source.json` (gitignored): the absolute path, ref and commit of a `--source` install. A later remote install removes it.
- **A project uninstaller**: the installer writes `./HACF-uninstall.sh` into the project and records it in the manifest; upgrades refresh it; uninstall deletes it last, after the manifest, and only when it is unchanged.
- The recorded live-test fixtures `dev/tests/fixtures/recorded/S04/18-21-*.json`, `dev/tests/fixtures/recorded/S08/01-09-*.json` and `dev/tests/fixtures/recorded/S11/01-09-*.json` keep the absolute paths of the machine they were recorded on, unedited, because they are evidence.

### Changed
- **`--ides` is honored under `-y`**: a non-interactive install configures only the tools you name.
- **The statusLine (`npx -y ccstatusline@latest`) is installed only with `--statusline`**; `-y` never downloads third-party code.
- **No Cline ignore file**: `.clineignore` is deprecated and no longer rendered.
- **Slice and mission ids use the prefix `OPR`** (for example `OPR.0.6.0.29`).
- **Installer, `/hyper-update` and uninstaller are consent-first and manifest-driven**: per-file sync that never deletes unknown files, managed blocks with an exact grammar that leave OpenRig's blocks untouched, no silent append to customized instruction files (a `*.hacf-v0.6.0.md` sidecar instead), and merge-driver attributes in `.git/info/attributes` instead of a tracked root `.gitattributes`.
- **The HACF repository no longer tracks `spec/` or `tests/`**; its own specs now live in `missions/`.
- `/hyper-orchestrator` and the daemon are thin wrappers over the engine.
- **The installed pre-commit hook never runs `uv` or downloads**: it takes ruff from `.venv/bin/ruff`, else from `PATH`, else skips the lint with a warning.
- **Upgrades keep the recorded IDE selection**: under `-y` an upgrade without `--ides` configures the IDEs in the manifest's `ides`; interactively, the prompt is prefilled with them (Enter keeps them).
- **Plain uninstall and upgrade use the recorded local source**: in a project installed with `--source`, `HACF-uninstall.sh` and `HACF-install.sh` with no `--source`, `--repo-url` or `--branch` use the checkout in `.agents/state/install/source.json`, and refuse (changing nothing) when it is missing or no longer matches the install.
- **Shipped files no longer cite HACF's internal design ids.**
- **An empty `--ides=` on an upgrade keeps the recorded selection** under `-y`, or prefills it interactively, like no `--ides` at all (before 0.6.0 it meant all IDEs).
- The managed `.gitignore` block also ignores HACF's bytecode (`.agents/**/__pycache__/`); an upgrade adds the line. The installer and uninstaller write no bytecode, and uninstall deletes HACF's `__pycache__` directories (only those holding nothing but `.pyc` files).
- Uninstall removes the `merge.hacf-lore` and `merge.hacf-generated` git config sections (unless you changed them), and deletes `<git-common-dir>/info/attributes` when it held only HACF's block; `--dry-run` lists both.

### Removed
- `hyper_fix.py` (replaced by the engine's executors), Final Truth, the unfilled memory templates and the `spec/handoffs/` rule.
- The deprecated templates `.agents/schemas/MiniPRD_template.md`, `.agents/schemas/SuperPRD_Template.md` and `.agents/schemas/hypergraph_schema.md` (their schemas live in `AGENTS.md`).

### Known issues
1. HACF's lock files stay in `<git-common-dir>/hacf/locks/`: `lore.lock`, `spec.lock` and `migrate.lock`, empty files inside `.git` that git never tracks. A normal install plus uninstall leaves `lore.lock` and `spec.lock`, and a refused or failed `hyper_migrate.py apply` leaves `migrate.lock`. Deleting a lock file would race a process holding or waiting on it, so neither the commands nor the uninstaller remove them. Delete `<git-common-dir>/hacf/` by hand when no HACF command is running.
2. `/hyper-update` after a local-source install refuses, because the installed commit is not in the upstream's history: `upstream <url> @ <commit> does not descend from the installed commit <installed_commit> (a downgrade or a rewritten history); rerun with --allow-downgrade to accept it`. **Do not use `--allow-downgrade` for this**: it turns off rollback protection. Re-run `HACF-install.sh --source=<checkout>` instead. The refusal clears once the installed commit is in the history of the upstream `main`.
3. `.agents/state/install/source.json` stays after uninstall, because the uninstaller never deletes `.agents/state/`.
4. Narrowing `--ides` on an upgrade does not remove the files of the IDEs left out.
5. The installer and `/hyper-update` do not cover local sources the same way: `/hyper-update` has no `--source`.
6. A fresh install that selects Claude Code (with the default `--firewall=auto`) writes `.claude/settings.json` twice, a merge and then the firewall render, and the render backs up the just-merged copy to `.agents/.backup/<timestamp>/.claude/settings.json`. That backup is safe to delete.

## [0.5.9] - 2026-07-23

### Changed
- **`hyper-process-document` and `hyper-handoff` skills**: Output filenames now use a date-only stamp (`process_<YYYYMMDD>_<slug>.md`, `handoff_<YYYYMMDD>.md`) instead of a full `<YYYYMMDD_HHMMSS>` timestamp, with a `_v2`/`_v3` suffix appended to disambiguate multiple documents written on the same date. Agents now run workflows autonomously, so a wall-clock time-of-day in the filename no longer reflects when the underlying work was actually done.

## [0.5.8] - 2026-07-21

### Added
- **AI-agent repository safeguards**: Two new git hooks distributed by `HACF-install.sh` — `commit-msg` (`.agents/scripts/commit-msg`) sanitizes agent tracking trailers (`Claude-Session:` → `Agentic Coding Assisted: Claude Code`), and `pre-push` (`.agents/scripts/pre-push`) blocks pushing experimental `research/*` and `swimlane/*` branches.
- **`.agents/rules/git-workflow.md`**: New always-on rule documenting protected branches, protected paths, and how to handle hook rejections. Referenced from the "Always-On Coding Rules" table in `AGENTS.md` and its `.agents/install-templates/` counterpart.
- **Safe Sandboxing & Automation guidance**: Added to `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` (and their `.agents/install-templates/` counterparts) instructing agents to use `research/`/`swimlane/` branch prefixes for experimental work.
- **`HACF-install.sh` — new `--files=` targets**: `commit-msg`, `pre-push` (git hooks, chmod+x), and `git-workflow-rules` (→ `.agents/rules/git-workflow.md`).

### Changed
- **`.agents/scripts/pre-commit`**: Now blocks committing forbidden internal paths (`spec/process/`, `spec/handoffs/`) before running its existing ruff lint/format checks.
- **`HACF-install.sh` — `install_hook()`**: Generalized to install all three git hooks (`pre-commit`, `commit-msg`, `pre-push`) instead of only `pre-commit`.

## [0.5.7] - 2026-07-03

### Added
- **`HACF-install.sh` — `--files=` flag**: Surgical file install targeting individual named files without overwriting existing files. Skips the mode menu and IDE selection; clones the repo, copies only the specified targets, and exits. Always uses repair semantics — skips any destination that already exists.
- **`HACF-install.sh` — `FILE_TARGETS` map**: Six named targets for `--files=`: `pre-commit` (git hook → `.git/hooks/pre-commit`, chmod+x), `pyproject-template` (ruff config template → `.agents/schemas/project-templates/pyproject.toml`), `python-rules`, `testing-rules`, `security-rules`, `package-rules` (each to its `.agents/rules/` path).

## [0.5.6] - 2026-07-03

### Added
- **`HACF-install.sh` — mode selection menu**: Interactive upgrade mode now presents a numbered menu (full / skills-only / system-only / IDE-only / repair / dry-run) before proceeding. Fresh installs skip the menu and run the full install automatically.
- **`HACF-install.sh` — `--mode=` flag**: All six modes are accessible non-interactively via `--mode=<full|skills|system|ide|repair|dry-run>` for CI and scripted use.
- **`HACF-install.sh` — dry-run mode**: Prints every action that would be taken (`[DRY-RUN] would copy …`) without modifying any files.
- **`HACF-install.sh` — skills-only mode**: Updates `.agents/skills/` and the IDE skill bridge directories (`.claude/commands/`, `.windsurf/workflows/`) without touching system dirs, static IDE rules, or config files.
- **`HACF-install.sh` — system-only mode**: Updates `.agents/scripts/`, `.agents/schemas/`, `.agents/config/`, `.agents/rules/`, `.agents/install-templates/`, `tests/`, `.agentignore`, and the git pre-commit hook — without touching `.agents/skills/` or skill bridges.
- **`HACF-install.sh` — IDE-only mode**: Updates IDE directories and config files (`.claude/`, `.windsurf/`, `.cursor/`, `.clinerules/`, `.roo/`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) without touching `.agents/`.
- **`HACF-install.sh` — repair mode**: Installs only missing pieces; skips any file or directory already present. Safe to run on a partial install without overwriting customizations.
- **`HACF-install.sh` — `SKILL_BRIDGE_DIRS`**: New array (`".claude/commands"`, `".windsurf/workflows"`) listing IDE directories that contain per-skill bridge files. Skills mode auto-detects which of these are installed and syncs only those.
- **`HACF-install.sh` — `AGENT_SYSTEM_SUBDIRS`**: New array enumerating system subdirectories within `.agents/` (`scripts`, `schemas`, `config`, `rules`, `install-templates`). Excludes `memory/` (user-generated content) and `skills/` (managed separately).

### Changed
- **`HACF-install.sh`**: Refactored linear install body into named functions (`install_system_dirs`, `install_skills_dir`, `install_skill_bridges`, `install_core_dirs`, `install_core_files`, `install_ide_dirs`, `install_ide_files`, `set_permissions`, `install_python_deps`, `install_hook`) routed by `case "$MODE"`.
- **`HACF-install.sh`**: IDE selection menu is skipped for `skills` and `system` modes, which do not require IDE context.
- **`HACF-install.sh`**: `.gitignore` update prompt is shown only for modes that install IDE files (`install`, `full`, `ide`, `repair`, `dry-run`).
- **`HACF-install.sh`**: Done banner adapts to the active mode (e.g. "Skills update complete!", "System update complete!").

## [0.5.5] - 2026-06-24

### Added
- **`hyper-tutorial-audit` skill**: Run after `/hyper-tutorial-run` to turn tutorial failures into a structured, constraint-aware goal prompt. Interviews the user (max 2 questions per round) about their goal and what the fix agent may/may not change, reviews failures already visible in the conversation context, and generates a paste-ready prompt for a new Claude Code fix session. Never re-runs tutorial commands; read-only during the audit phase. Optionally saves the goal prompt to `tutorials/<name>/audit_<YYYY-MM-DD>.md`.

## [0.5.4] - 2026-06-23

### Added
- **`hyper-process-document` skill**: Documents the process, methodology, and decisions of a session as a reproducible narrative saved to `spec/process/`. Retrospective and human-readable — distinct from `/hyper-handoff` (which is forward-looking for the next agent). Accepts an optional title argument.
- **`spec/process/` directory**: New workspace directory for process documents generated by `/hyper-process-document`.
- **`.agents/schemas/project-templates/pyproject.toml`**: New `pyproject.toml` template with ruff linting and formatting configuration for projects scaffolded by `/hyper-init`. Includes Google docstring convention, isort, and `{{REPO_NAME}}`/`{{PROJECT_DESCRIPTION}}`/`{{PACKAGE_NAME}}` placeholder substitution.
- **`pyproject.toml`** (HACF root): Added ruff configuration for the HACF framework's own Python scripts.
- **`.agents/scripts/pre-commit`**: New git pre-commit hook script. Runs `uv run ruff check .` and `uv run ruff format --check .` before every commit. Gracefully skips if `uv` is not installed.

### Changed
- **`hyper-tutorial` skill**: Renamed to `hyper-tutorial-run` for clarity (trigger: `/hyper-tutorial-run`). No behavioral changes.
- **`.gitignore`**: Added `tests/fixtures/*` / `!tests/fixtures/.gitkeep` to prevent test fixture files from being accidentally committed, matching the existing `tests/candidate_outputs/` pattern.
- **`HACF-install.sh`**: During upgrade, IDE config files (CLAUDE.md, AGENTS.md, GEMINI.md) now display a unified diff before prompting yes/no. Files that are already identical are reported as "already up to date" without prompting.
- **`HACF-install.sh`**: When no `.git` directory is detected during a fresh install, the installer now offers to run `git init` interactively instead of just asking "install anyway?".
- **`HACF-install.sh`**: Automatically installs `.agents/scripts/pre-commit` into `.git/hooks/pre-commit` during both fresh installs and upgrades (with diff+prompt on upgrade). Pre-commit hook runs ruff lint checks before every commit.
- **`HACF-install.sh`**: Added `spec/process/` to the spec directory scaffold (alongside `active/`, `compiled/`, `archive/`).
- **`.agents/rules/python.md`**: Added `## Linting Configuration` section with concrete ruff `[tool.ruff]` TOML block — Google docstring convention, isort config, and line-length 88.
- **`.agents/rules/testing.md`**: Added `### 4. Lint (Mandatory)` section — `uv run ruff check .` and `uv run ruff format --check .` must exit 0 before every PR.
- **`.agents/rules/package-management.md`**: Added required dev dependencies section — every project must include `uv add --dev ruff` and `uv add --dev pytest`.
- **`.agents/skills/hyper-init/SKILL.md`**: Added Step 2b to scaffold `pyproject.toml` from `.agents/schemas/project-templates/pyproject.toml` with placeholder substitution, and runs `uv add --dev ruff` and `uv add --dev pytest`.

## [0.5.3] - 2026-06-06

### Changed
- **`install.sh`**: Renamed to `HACF-install.sh` for clearer project identity and to avoid collisions with other tools' install scripts.
- **`uninstall.sh`**: Renamed to `HACF-uninstall.sh` for consistency with the above rename.
- **`HACF-install.sh`**: Added `--preserve-custom` flag. When set, skips overwriting `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` during upgrades — preserving per-project agent instruction customizations while still updating `.agents/`, `.claude/commands/`, and other bridge directories. Compatible with `-y` for non-interactive upgrades.
- **`README.md`**: Updated all installer references from `install.sh`/`uninstall.sh` to `HACF-install.sh`/`HACF-uninstall.sh`; added `--preserve-custom` usage to the Upgrading section.

## [0.5.2] - 2026-06-06

### Changed
- **`.claude/settings.json`**: Added `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var (set to `60`) to trigger auto-compaction at 60% of context capacity rather than the default ~95%, improving token efficiency across long agent sessions.
- **`.claude/settings.json`**: Added `attribution` block (`commit` and `pr` fields) for commit and PR attribution configuration.
- **`hyper-publish` skill**: Removed IP protection framing from mtime-touching behavior description.

## [0.5.1] - 2026-06-06

### Added
- **`hyper-contextualize` skill**: Audits and fixes HACF agent instruction files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) in installed projects to ensure HACF is framed as a development toolchain, not the project subject. Three fix modes: Replace with install templates, Inject framing banner only, or Per-file diff. Reports HACF bleed found in `README.md` without modifying it.
- **`hyper-handoff` skill**: Compacts the current conversation into a structured handoff document saved to the OS temp directory. References existing artifacts (PRDs, commits, specs) by path rather than reproducing them. Includes a "Suggested Skills" section for the next session. Redacts sensitive information. Accepts an optional argument describing what the next session will focus on. Adapted from Matt Pocock's [`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT License).
- **`hyper-grill-docs` skill**: Relentless domain-sharpening interview that challenges terminology against `CONTEXT.md`, cross-references stated behavior against the codebase, stress-tests domain boundaries with concrete scenarios, and writes ADRs inline when decisions are hard-to-reverse, surprising, and trade-off-driven. Ships with `CONTEXT-FORMAT.md` and `ADR-FORMAT.md` format references. Adapted from Matt Pocock's [`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT License).
- **`ATTRIBUTION.txt`** files in `.agents/skills/hyper-handoff/` and `.agents/skills/hyper-grill-docs/` acknowledging source adaptation from Matt Pocock's skills repo.

### Changed
- **`hyper-architect` skill**: Redesigned interview mode — one question per turn (down from two), each question leads with a `Recommended: [X]` answer so the user can accept or redirect without composing answers from scratch. Codebase-first rule added: reads `architecture.yml` and existing code before asking anything derivable from the project state. Decision-tree traversal added: resolves dependencies depth-first before opening sibling branches.
- **`install.sh`** success banners: Fixed stale command names (`/discover` → `/hyper-discover`, `/baseline` → `/hyper-baseline`); added `/hyper-contextualize` suggestion to both fresh-install and upgrade banners.
- **`README.md`**: Added `## 🙏 Acknowledgements` section crediting Zevi Arnovitz (core workflow concept) and Matt Pocock (hyper-grill-docs, hyper-handoff, grill-me inspiration).

## [0.5.0] - 2026-06-04

### Added
- **Dynamic Workflow Engine** — five new scripts enabling fully automated parallel MiniPRD execution:
  - **`hyper_orchestrator.py`**: Fans each SuperPRD/MiniPRD pair out to an isolated Git branch via `ThreadPoolExecutor` (max 8 workers), spawns one Anthropic API subagent per pair, then runs a sequential rebase pipeline. Enforces global `fcntl` mutex, 6-point startup validation gate, API rate budget, idempotency via `_provenance` checks in `architecture.yml`, `SIGTERM`/`SIGINT` handler, structured JSON-Lines logging to `.agents/logs/`, and 50-entry `FAILED_WORKFLOWS.md` rotation.
  - **`hyper_daemon.py`**: Execute → Audit → Oracle → Fix loop (max 5 iterations). Supports orchestrated mode (`--orchestrated` flag, called by `hyper_orchestrator`) and standalone mode. Enforces `stop_reason` guard (`TOKEN_OVERRUN` on `max_tokens`), `pytest --collect-only` pre-flight (`NO_TESTS_COLLECTED`), AST-based spec drift detection at 40% threshold (`SPEC_DRIFT`), and hard 5-iteration cap (`ORACLE_FAILURE`).
  - **`hyper_fix.py`**: Bug-fix agent triangulating SuperPRD + MiniPRD + pytest error trace. Enforces prompt injection hardening via XML delimiters, pre-call token estimation (95% context ceiling guard), spec drift detection, and ORACLE_FAILURE delegation. Parses LLM patches via `# FILE: path` + fenced code blocks.
  - **`hyper_resolve_conflict.py`**: AI conflict resolver for git rebase conflicts. Enforces file-type whitelist (`{.py, .md, .json, .toml}`), unconditional blocking of `architecture.yml/yaml`, confidence gate ≥ 85, post-resolution syntax validation (`ast.parse` / `yaml.safe_load` / `json.loads`), and write-only-on-pass semantics.
  - **`semantic_graph_merger.py`**: Deterministic YAML merger for `architecture.yml` conflicts. Never uses AI. Enforces exclusive `fcntl` locks, duplicate-node conflict policy (identical = deduplicate, differing = `REBASE_CONFLICT` abort), extension normalization, 4-point post-merge schema validation, and pre-merge backup with restore on failure.
- **`.agents/install-templates/`**: New directory containing project-facing versions of `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`. Install templates add a "HACF as a Toolchain" framing banner (preventing agents in installed projects from conflating the user's project with the HACF framework), remove HACF-internal migration notices and upgrade-maintenance documentation, and preserve all schema definitions, skill references, and system mandates.
- **`.python-version`**: Pinned Python 3.11+ required for framework scripts.
- **Integration test suites** (30 tests): `test_dynamic_orchestrator.py` (10 tests covering mutex, startup validation, branch collision, TOKEN_OVERRUN, NO_TESTS_COLLECTED, hung worker timeout, SIGTERM, idempotency), `test_autonomous_resolution.py` (10 tests covering conflict resolver whitelist, binary detection, JSON decode guard, confidence assertion, confidence gate, syntax override, error field), `test_provenance_integration.py` (tests for `semantic_graph_merger` routing guard, duplicate node policies, schema validation, concurrent write protection, and pre-merge backup restore).

### Changed
- **`hypergraph_updater.py`**: Added provenance staging lifecycle subcommands (`write-provenance`, `merge-provenance`, `cleanup-provenance`) with exclusive `fcntl` locking.
- **`install.sh`**: Added `FILE_SOURCE_OVERRIDE` associative array. IDE config files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) are now installed from `.agents/install-templates/` rather than the repo root, ensuring installed projects receive project-framed agent instructions.
- **`hyper_update_core.py`**: Added `SENSITIVE_FILE_UPSTREAM_SOURCES` dict. Upgrade comparison for `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md` now reads from `.agents/install-templates/` in the upstream repo rather than the repo root, so upgrade diffs are computed against the correct install-template versions.
- **`hyper-architect` skill**: Schema reference updated from `CLAUDE.md` to `AGENTS.md` (aligns with actual schema location in installed projects).
- **`spec/compiled/architecture.yml`**: Six dirty nodes (`hypergraph_updater`, `hyper_orchestrator`, `hyper_daemon`, `hyper_fix`, `hyper_resolve_conflict`, `semantic_graph_merger`) reconciled to `clean` after Dynamic Workflow Engine audit.

### Fixed
- **Git Tracking**: Removed `spec/compiled/` files (`architecture.yml`, `SuperPRD.md`) from Git tracking to respect `.gitignore` and framework standards.
- **Context bleed in installed projects**: `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md` installed into user projects were previously framed for HACF development, causing agents to conflate user projects with the HACF framework. Install templates now provide correct project-facing context.

### Removed
- **Legacy integration tests**: Pre-v0.5.0 integration test stubs in `tests/integration/` removed; replaced by the three new deterministic test suites above.

## [0.4.3] - 2026-05-17

### Added
- **`hyper-init` skill**: Scaffolds a new project with standard repository documentation from generic templates.
- **Project Templates**: Added generalized templates for `CHANGELOG.md`, `CITATION.cff`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `DEVELOPMENT.md`, `README.md`, and `SECURITY.md` in `.agents/schemas/project-templates/`.
- **Standard Repo Files**: Bootstrapped the Hypergraph framework repository itself with the new `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `DEVELOPMENT.md`, and `CITATION.cff` files based on the new templates.

### Changed
- **`hyper-document` skill**: Updated to include guidelines for maintaining the new standard repository documents in sync with the templates.
- **`README.md`**: Added `/hyper-init` instruction to the New Project setup guide.
- **`AGENTS.md`**: Registered `/hyper-init` in the master available skills index.
- **`GEMINI.md`**: Updated the list of available skills to include all 30 prefixed skills in alphabetical order, resolving stale legacy names.

## [0.4.2] - 2026-05-14

### Added
- **Enhanced `/hyper-publish` skill**: AI-proposes commit messages from CHANGELOG `[Unreleased]` + git diff; uses human-in-the-loop approval gates at each step (7 decision points); supports optional PR creation with auto-drafted descriptions from changelog.
- **Helper scripts for `/hyper-publish`**: 
  - `generate_commit_message.py` — reads CHANGELOG and recent commits to suggest subject/body
  - `generate_pr_description.py` — formats PR body from changelog with Summary and Test plan sections
  - `setup_permissions.py` — patches `.claude/settings.json` to pre-approve git/gh commands (eliminates per-invocation prompts)
- **Command Normalization**: Renamed `/clear` command to `/hyper-clear` to resolve naming conflict with Claude Code's built-in `/clear` command. All references updated across framework.
- **Examples and Templates for `/hyper-publish`**: 
  - `examples/example_commit_messages.md` — 5 real commit patterns from framework history
  - `examples/example_pr_description.md` — example PR description format
  - `resources/commit_template.md` — blank commit message template with guidelines
  - `resources/pr_description_template.md` — blank PR body template with section structure
- **`hyper-tutorial-generator` skill**: New `/hyper-tutorial-generator` command that converts existing integration tests or user-provided files into polished markdown tutorials via iterative LLM collaboration. Two input modes (Integration Test or User Files); section-by-section drafting with HITL approval gates; auto-scaffolds tutorial directory structure with supporting subdirectories (input_files/, output_files/, code_samples/, screenshots/).

### Changed
- **AskUserQuestion enforcement across 18 skills**: Standardized all structured user-facing choice prompts to explicitly invoke the `AskUserQuestion` tool instead of relying on freeform conversation turns. Affected skills: `hyper-sop` (phase selection), `hyper-troubleshooting` (symptom triage + follow-up), `hyper-update` (Replace/Merge/Skip gate + per-section merge approval), `hyper-execute` (re-implement confirmation + confidence gate), `hyper-resolve` (forced trade-off choices + NFR approval), `hyper-architect` (phase advance confirmation), `hyper-tutorial` (all 6 readiness checks), `hyper-deepdive` (integration question), `hyper-document` (version bump gate), `hyper-peer-review` (input method), `hyper-create-issue` (issue type + priority), `hyper-stitch-design` (style reference), `hyper-template-architect` (source document gate), `hyper-new-workflow` (name confirmation), `hyper-prompt-engineer` (output format + export), `hyper-co-research` (scoping gate), `hyper-consult-cto` (clarifying questions), `hyper-learning-opportunity` (depth selection + between-level readiness). Open-ended free-text interview questions (e.g., Architect phase questions) remain as freeform input.
- **`hyper-execute` and `hyper-audit` skills**: Enhanced with Haiku sub-agents for cost optimization. `/hyper-execute` delegates file pre-loading to a Haiku sub-agent (Step 2.5) before implementation. `/hyper-audit` delegates YAML reconciliation to a Haiku sub-agent (Phase 3) for mechanical node updates. Both include fallback logic to operate manually if sub-agent fails.
- **`install.sh`**: `spec/` directory is now scaffolded (empty subdirs created) instead of copied from framework. Ensures users start with clean slate for their own specification files, not framework's internal development history.
- **`.agents/memory/activeContext.md`**: Updated all references from `/clear` to `/hyper-clear`; noted command rename rationale.
- **`spec/compiled/architecture.yml`**: Updated context_clearing node to reference hyper-clear skill path and command name.
- **`.claude/settings.json`**: Added `"permissions": {"allow": ["Bash(git *)", "Bash(gh *)", "Bash(touch *)"]}` to pre-approve framework commands without per-invocation prompts.

### Removed
- **Deprecated `/clear` command**: Removed `.claude/commands/clear.md` command bridge and `.agents/skills/clear/` directory. Users must use `/hyper-clear` instead (same functionality, no naming conflict).

## [0.4.1] - 2026-05-04

### Added
- **`hyper-publish` skill**: New `/hyper-publish` command that updates `mtime` on all git-changed files to the current system time before committing and pushing. Supports `--dry-run` flag, optional CHANGELOG version promotion, and `$ARGUMENTS`-based commit message passthrough.
- **`hyper-update` skill**: Smart framework upgrade command with GPG signature verification, customization preservation, and backup management. Auto-updates framework files while guiding users through section-by-section merging of sensitive files (CLAUDE.md, AGENTS.md, IDE rules).
- **`hyper-recover` command**: File restoration from timestamped backups with meta-backup protection. List available backups and restore specific files from specific dates.
- **GPG Signature Verification**: Cryptographic verification of upstream commits before framework fetch. Fails-fast on invalid/missing signatures with installation guidance.
- **Timestamped Backup System**: ISO 8601 UTC backup directories (`.agents/.backup/YYYY-MM-DDTHH-MM-SSZ/`) with lazy creation, 30-day retention policy, and automatic cleanup.
- **Merge State Tracking**: JSON lock file (`.merge-in-progress.lock`) persists merge state across interrupts. Supports resume from last approved section and stale lock detection (>24 hours).
- **Backup Lifecycle Management**: Audit trail logging (JSON format) to `.agents/logs/hyper-update.log` with log rotation (1MB rotation, 10-file retention). Includes backup age warnings and meta-backup protection during recovery.

## [0.4.0] - 2026-05-03

### Added
- **Hybrid Model Orchestration**: Three-tier model routing system (Opus/Sonnet/Haiku) for cost-optimized skill execution. Routes simple deterministic tasks to Haiku (~70% cost reduction), complex reasoning to Opus, with fallback logic and comprehensive logging.
- **`model_router.py`**: Core module implementing model assignment logic, heuristic-based routing decisions, Opus fallback with alerting, and version compatibility checking.
- **`heuristic_classifier.py`**: YAML-based rule engine for automatic model tier suggestions during skill creation. Analyzes tool count, reasoning intensity, I/O complexity to recommend appropriate model.
- **`.agents/config/heuristic_rules.yaml`**: Declarative rule definitions for model tier classification with per-rule confidence scoring and model mapping (thinking tokens, costs, latency).
- **Skill Metadata System**: Per-skill `META.yml` configuration files in `.agents/skills/hyper-*/` with model assignment, version pinning, and override management.
- **`meta_loader.py`**: MetaLoader class for reading/validating skill metadata with schema validation and fallback defaults.
- **`override_manager.py`**: OverrideManager class for managing model assignment overrides with scope lifecycle (single_run/permanent), persistence, and terminal warning on activation.
- **Thinking Token Ceiling Enforcement**: Per-model defaults (Opus 20k, Sonnet 10k, Haiku 2k) with optional per-skill overrides via META.yml.
- **`thinking_token_monitor.py`**: ThinkingTokenMonitor class for resolving and logging thinking token ceilings with META.yml override support.
- **`ceiling_enforcer.py`**: CeilingEnforcer class for detecting ceiling breaches, emitting structured warnings with recovery suggestions (task splitting, temporary overrides), and tracking utilization metrics.
- **Output Token Budget Enforcement**: 50k output token ceiling with heuristic estimation formula (code_lines/40 + doc_pages*500 + test_cases*100).
- **`token_budget_checker.py`**: BudgetChecker class for validating estimated output tokens against 50k ceiling and suggesting task split points.
- **`token_budget_reconciler.py`**: TokenBudgetReconciler class for post-execution variance tracking (estimated vs. actual tokens) with investigation flag for >20% deviation.
- **Context Compaction Engine**: Automatic debate transcript compression preserving trade-off reasoning (both positions + resolution + risks), with interrupt recovery.
- **`compaction_engine.py`**: CompactionEngine class for extracting and compacting debate transcripts into Final Truth documents with archived full history links.
- **`interrupt_detector.py`**: InterruptDetector class for detecting incomplete compactions and auto-triggering compaction before /hyper-execute.
- **Context Clearing Mechanism**: Idempotent session context flushing via `/hyper-clear` command with state preservation (specs, metrics, performance data retained).
- **`clear` skill**: New `.agents/skills/hyper-clear/SKILL.md` implementing idempotent context clearing with post-audit hook integration.
- **Persistent Rules Migration**: Schema consolidation from `.agents/schemas/` into CLAUDE.md to reduce per-message token overhead (~10-15% reduction).
- **`final_truth_template.md`**: Schema for compacted debate output format with trade-off context preservation and archive links.
- **`META_template.md`**: Schema for per-skill metadata files with model assignment, version pinning, and override management guidance.
- **7 Integration Test Suites**: Comprehensive test documentation covering model routing, skill metadata, context compaction, thinking token ceilings, and token budget enforcement (46 tests total, all passing).

### Changed
- **`hyper-architect` skill**: Now routes to Opus by default; documents model assignment in execution logs.
- **`hyper-redteam` skill**: Now routes to Sonnet by default; logs thinking token usage separately from input/output.
- **`hyper-execute` skill**: Routes to Haiku by default; checks for missing compaction files and auto-compacts before proceeding; updated to support cost metrics collection.
- **`hyper-resolve` skill**: Automatically triggers context compaction after completion; outputs both full debate history and compacted Final Truth document.
- **`hyper-audit` skill**: Automatically triggers `/hyper-clear` after completion; updated to reconcile token-budget-enforcer node.
- **`CLAUDE.md`**: Added comprehensive "Schema Definitions" section with embedded SuperPRD, MiniPRD, and Hypergraph schemas (~800 lines consolidated); deprecated `.agents/schemas/` files with 6-week migration window (deadline: 2026-06-17).
- **`AGENTS.md`**: Updated skill descriptions for model routing, thinking token monitoring, and token budget enforcement; added `/hyper-clear` command documentation.
- **`spec/compiled/architecture.yml`**: Added 7 new nodes (context_clearing, context_compaction, model_router, skill_metadata, thinking_token_monitor, token_budget_enforcer, rules_migration); all marked clean and verified.
- **`.agents/memory/activeContext.md`**: Updated with Token Efficiency implementation status; all 7 MiniPRDs audited and archived.

### Removed
- Deprecated `.agents/schemas/` files (SuperPRD_Template.md, MiniPRD_template.md, hypergraph_schema.md) marked with deprecation headers; old files retained until 2026-06-17 for backward compatibility.

## [0.3.5] - 2026-03-15

### Changed
- **`hyper-execute` skill**: Added Step 1 — Memory Check. The skill now reads `.agents/memory/activeContext.md` before touching the MiniPRD. If the target MiniPRD is listed as complete or already audited, execution halts with a warning that explains the collision, provides the manual archive command as a fallback, and requires explicit user confirmation before proceeding.
- **`AGENTS.md`**: Updated `hyper-execute` description to reflect the memory check step.

## [0.3.4] - 2026-03-15

### Changed
- **`hyper-document` skill**: Expanded from a minimal 5-step outline to a full per-target documentation guide. Now covers `README.md`, `CHANGELOG.md`, `docs/MasterSOP.md`, `docs/Tutorial.md`, `docs/Troubleshooting.md`, `docs/Whitepaper.md`, `AGENTS.md`, `.agents/memory/` files, and `skills-info.md`. Each target includes explicit update triggers and style rules (tone, formatting, heading levels, prohibited language).
- **`AGENTS.md`**: Updated `hyper-document` skill description to reflect expanded scope.

## [0.3.3] - 2026-03-15

### Changed
- **`hyper-audit` skill**: Added Phase 4 — MiniPRD Archival. After a successful audit, the MiniPRD is automatically moved from `spec/compiled/` to `spec/archive/` (renamed with `_AUDITED` suffix). Since `spec/archive/` is blocked by `.agentignore`, the completed MiniPRD no longer surfaces in subsequent `/hyper-execute` runs.
- **`hyper-execute` skill**: Added a note in the halt/report step informing developers that a passing audit will archive the MiniPRD automatically.

## [0.3.2] - 2026-03-15

### Changed
- **`README.md`**: Documented the `hyper-` naming convention — added explanations in the directory structure section (`.agents/skills/`, `.claude/commands/`) and a callout block in the Setup section clarifying that the prefix prevents collisions with project-specific skills and commands.

## [0.3.1] - 2026-03-15

### Changed
- **Skill namespace prefix**: All 25 skills renamed from `<name>` to `hyper-<name>` to prevent collisions with project-specific skills in consumer repos.
  - `.agents/skills/<name>/` → `.agents/skills/hyper-<name>/`
  - `.claude/commands/<name>.md` → `.claude/commands/hyper-<name>.md`
  - `.windsurf/workflows/<name>.md` → `.windsurf/workflows/hyper-<name>.md`
  - All slash command invocations updated (`/architect` → `/hyper-architect`, etc.)
  - All references in `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, rule bridges, and `docs/` updated accordingly.

## [0.3.0] - 2026-03-15

### Added
- **Multi-IDE Support**: Full support for Windsurf, Cursor, Cline, Roo Code, GitHub Copilot, and Zed in addition to existing Claude Code and Gemini CLI/Antigravity support.
- **`AGENTS.md`**: New universal cross-IDE always-on manifest at the repository root. Consumed natively by Windsurf, Cursor, Roo Code, GitHub Copilot, and Zed.
- **`.windsurf/`**: New directory with rule bridges (`rules/`) and workflow bridges (`workflows/`) — one thin file per skill and coding standard.
- **`.cursor/rules/`**: New directory with `.mdc` rule bridges using Cursor's YAML frontmatter (`globs`, `alwaysApply`) for contextual activation.
- **`.clinerules/`**: New directory with plain markdown rule bridges for Cline.
- **`.roo/`**: New directory with mode-aware rule bridges (`rules/` for all modes, `rules-code/` for code mode only).
- **`sop` skill**: Created missing `.agents/skills/hyper-sop/SKILL.md` — the skill existed as a `.claude/commands/` entry but had no backing SKILL.md.
- **`install.sh` IDE selection menu**: Interactive numbered menu to select which IDE(s) to install support for; supports `--ides=` flag and `-y` for non-interactive installs.
- **`install.sh` `.gitignore` prompt**: After installation, offers to append installed IDE-specific paths to the project's `.gitignore`.

### Changed
- **Zero-duplication architecture**: `.agents/skills/*/SKILL.md` is now the single source of truth. All IDE directories (`.claude/commands/`, `.windsurf/workflows/`, etc.) contain only 4-line thin bridge files that reference the central skill.
- **`.claude/commands/`**: All 25 command files converted to thin bridges. Previously, 7 files (architect, redteam, resolve, audit, discover, baseline, sop) contained full duplicate skill content.
- **`CLAUDE.md`**: Slimmed from ~103 lines to ~40 lines. Removed duplicate content now covered by `AGENTS.md`; retained only Claude Code-specific tool name overrides and task tracking mandate.
- **`GEMINI.md`**: Same treatment as `CLAUDE.md` — slimmed to Gemini CLI-specific tool overrides only, references `AGENTS.md` for shared content.
- **`.agents/skills/hyper-new-workflow/SKILL.md`**: Fixed scaffold target from `.agents/workflows/` to `.agents/skills/*/SKILL.md` to prevent regenerating the removed duplication layer.
- **`.agents/skills/hyper-architect/SKILL.md`**: Fixed truncated Phase 5 — content was cut off mid-sentence; full action steps restored.
- **`install.sh`**: Rewrote with modular `IDE_DEFS` array, upgrade-mode detection, migration block, and per-component update prompts.
- **`README.md`**: Full rewrite to document the no-duplication architecture, all new IDE bridge directories, per-IDE integration sections, and updated install.sh features.

### Removed
- **`.agents/workflows/`**: Entire directory deleted. Was a third redundant copy of skill content (older and less complete than `.agents/skills/`). All 17 workflow files removed.

## [0.2.3] - 2026-02-22

### Changed
- **Workflows**: Replaced the automated git-history approach in the `/hyper-document` workflow with an explicit, interactive user prompt for semantic versioning.
- **Workflows**: Upgraded the `/hyper-document` workflow to gracefully structure and label stamped release sections derived from the `[Unreleased]` block.

## [0.2.2] - 2026-02-22

### Added
- **Agent Resources**: Added new directories `.agents/memory/`, `.agents/rules/`, and `.agents/schemas/` housing agent rule templates and schema templates (`DESIGN_Template.md`, `PRD_Template.md`, `plan_Template.md`, `todo_Template.md`).
- **Workflows**: Duplicated and formatted agent skills into the `.agents/workflows/` directory for Antigravity integration (e.g., `code-auditor.md`, `consult-cto.md`, `create-issue.md`, `create-plan.md`, `execute.md`, `explore.md`, `review.md`, etc.).
- **Gemini CLI Integration**: Added `GEMINI.md` to automate Hypergraph framework orchestration using Gemini CLI's Skills and autonomous tool execution.
- **Maintainability Tools**: Added `.gitignore` to prevent project-specific draft specifications from being accidentally committed.
- **Standard Dependency Management**: Added `requirements.txt` to replace manual `pip install` instructions.
- **Project Tracking**: Added `CHANGELOG.md` to record development history and architectural shifts.
- **Tutorial Visibility**: Updated `README.md` to point more clearly to the `Tutorial.md` and `GEMINI.md` guides.

### Changed
- **Documentation Structure**: Moved `MasterSOP.md`, `Troubleshooting.md`, `Tutorial.md`, and `Whitepaper.md` to a new `docs/` directory, updating `README.md` links accordingly.
- **Documentation**: Enhanced `README.md` to include references to Gemini CLI's native multi-agent capabilities.
- **Workflow**: Updated the repository's core mandates to prioritize automated state management when operated by Gemini CLI.
- **Workflows**: Refined formatting of agent workflows in `.agents/workflows/` by adding standard titles and updating legacy PRD references to the new `SuperPRD` format.
- **Documentation**: Updated the `/hyper-document` workflow to explicitly mandate `README.md` updates alongside the `CHANGELOG.md`.
- **Documentation**: Updated `README.md` to document the `.agents/workflows/` directory.

### Removed
- **Archived Workflows**: Deleted the obsolete `.agents/workflows/archive/` directory.
- **Workflows**: Deleted obsolete `.agents/workflows/review.md` in favor of newer workflow structures.


## [0.2.0] - 2026-02-21

### Added
- Created `skills-info.md` to document the purpose and usage of each agent skill.
- Created `baseline` skill directory (`.agents/skills/hyper-baseline/SKILL.md`).
- Created `discover` skill directory (`.agents/skills/hyper-discover/SKILL.md`) with its own `hypergraph_schema.md` and `hypergraph_updater.py`.

### Changed
- **Major Skill Directory Refactor**: Transitioned from flat markdown files in `.agents/skills/` to dedicated skill folders (e.g., `.agents/skills/hyper-architect/SKILL.md`).
- Copied necessary resources (`hypergraph_schema.md`, `MiniPRD_template.md`) and scripts (`archive_specs.py`, `hypergraph_updater.py`) directly into their respective skill directories to make them self-contained.
- Overhauled `README.md` to reflect the new directory structure and refined workflow.
- Updated `MasterSOP.md`, `Tutorial.md`, `Troubleshooting.md`, and `Whitepaper.md` to align with the new structure.

### Removed
- Redundant `Coding_4_Whitepaper.md` file.
- Archived the old flat workflow files into `.agents/workflows/archive/`.

## [0.1.0] - 2026-02-21

### Added
- Fleshed out `.agents/workflows/` by adding detailed markdown files for `architect.md`, `audit.md`, `baseline.md`, `discover.md`, `redteam.md`, and `resolve.md`.
- Added new workflow instructions to `MasterSOP.md`.

### Changed
- Renamed the `.agent` directory to `.agents`.

## [0.0.1] - 2026-02-21

### Added
- Initial commit establishing the core Hypergraph Coding Agent Framework.
- `.agentignore` to prevent AI context collapse.
- Core documentation: `README.md`, `Whitepaper.md`, `Coding_4_Whitepaper.md`, `MasterSOP.md`, `Tutorial.md`, and `Troubleshooting.md`.
- `MiniPRD_template.md` and `hypergraph_schema.md` schemas.
- Deterministic Python scripts: `archive_specs.py` and `hypergraph_updater.py`.
- Initial agent skill prompts: `architect.md`, `audit.md`, `redteam.md`, and `resolve.md`.
- Directory structure for specifications (`spec/active/`, `spec/archive/`, `spec/compiled/`) and tests (`tests/candidate_outputs/`, `tests/fixtures/`) with `.gitkeep` files.
