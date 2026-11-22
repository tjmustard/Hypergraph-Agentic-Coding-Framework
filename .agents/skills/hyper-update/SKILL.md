---
name: update
description: Upgrades the installed Hypergraph framework from upstream without clobbering user work. Runs a read-only dry run (fetch, ancestry, signature status, the per-file sync plan, the migration plan), then hands the user one command to run in their own terminal, where consent, the sync and the HACF-block merges happen; finally reads the report and the consent log. Use when the user runs /hyper-update or asks to update HACF.
---

# Update

This skill upgrades a project's HACF framework files from upstream with
`.agents/scripts/hyper_update_core.py`. The script does the work; the agent runs only its
read-only part, explains it, and leaves every decision that needs consent to the user's own
terminal.

## When to use this skill

- The user runs `/hyper-update` or asks to update or upgrade HACF.
- HACF is installed: `.agents/hacf-manifest.json` or `.agents/scripts/hypergraph_updater.py`
  exists. Otherwise point the user at `HACF-install.sh`.

## Rules

- Call every script with `python3`.
- Run only `python3 .agents/scripts/hyper_update_core.py --dry-run` yourself. The real update
  needs a terminal (TTY) for its consent prompt, and your Bash tool has none, so the user runs
  it (Phase 3).
- Never pass `--allow-unsigned` or `--allow-downgrade` on the user's behalf, and never suggest
  them as a way around a refusal. They are the user's own consent, given in their terminal.
- Never run `hyper_migrate.py apply`. The update prints the migration plan; applying it is the
  user's decision, in their terminal.
- Never tell the user to import a GPG or SSH key into their global keyring or git config. HACF
  checks SSH signatures only against the machine-local pin, one call at a time.
- The upstream URL and ref change only through `--upstream-url <https-url>` and `--ref <ref>`.

## What the update does (so you can explain it)

- **Integrity (HACF's update-integrity policy).** The script fetches the upstream with
  `git clone --filter=blob:none --branch <ref> <url>` and prints the effective URL and commit
  sha on every prompt and log line.
  - *Anti-rollback:* an upstream commit that does not descend from the manifest's
    `installed_commit` is refused unless the user passes `--allow-downgrade`.
  - A project installed with `HACF-install.sh --source` cannot be updated by /hyper-update yet;
    see the CHANGELOG known issues.
  - *Signer pin:* allowed signers are pinned machine-locally in
    `.agents/state/upstream-trust.json` on the first trusted update. The upstream's
    `.agents/config/upstream.yml` is only a proposal; a different signer set is shown as a diff
    and adopted only with the user's interactive consent.
  - *Signed:* with pinned signers, a valid SSH signature is required; nothing overrides a
    failure.
  - *Unsigned (v0.6.x releases):* the user consents interactively, or passes `--allow-unsigned`
    in a terminal. Every consent is appended to `.agents/logs/update-consent.jsonl`.
- **Sync.** Per file, driven by the manifest, over `.agents/skills/`, `.agents/scripts/` and
  `.agents/schemas/` only. A file the user changed (or any file, when there is no manifest) is
  backed up to `.agents/.backup/<timestamp>/` before it is overwritten. User and
  OpenRig-projected skill dirs and unknown files are kept; a file is removed only when upstream
  dropped it and the manifest says HACF installed it unchanged. The project's own specs, tests,
  lore, missions and settings are never synced. The manifest is then updated.
- **Blocks.** `.agentignore` gets only its HACF `agentignore` block. `AGENTS.md`, `CLAUDE.md`
  and `GEMINI.md` get only their HACF `hacf` block; a whole file without HACF markers is
  replaced only when it is an unmodified v0.5.x template, and is never appended to otherwise:
  in a terminal the user chooses replace (saving `<file>.v059.bak`) or a sidecar
  `<STEM>.hacf-v0.6.0.md`; under `-y` the sidecar is written. Bytes outside HACF blocks,
  OpenRig and RIGGED blocks included, stay identical. `CLAUDE.local.md` is never touched.
- **Bridges.** Changed files under `.claude/commands/`, `.clinerules/`, `.roo/rules*/`,
  `.cursor/rules/` and `.windsurf/rules/` are merged in the terminal (replace, `###`-section
  merge, or skip; only the HACF block when the file has one), or listed for review under `-y`.
- **Migration hand-off.** It runs `python3 .agents/scripts/hyper_migrate.py plan` and prints
  the plan with the interactive apply command. It never applies it.

## Phases

### Phase 1: Preflight and dry run

1. Run:
   ```bash
   python3 .agents/scripts/hyper_update_core.py --dry-run
   ```
   Add `--upstream-url <https-url>` or `--ref <ref>` only when the user asked for them. The dry
   run writes nothing: no backup, pin, log line, lock or manifest change.
2. Read the JSON report (the last thing on stdout) and the `[hyper-update]` lines on stderr.

### Phase 2: Show the user what would happen

Report, plainly:

- the upstream **URL**, **ref** and **commit sha**;
- **ancestry**: `descendant`, `not-descendant` (the update will be refused unless they choose
  `--allow-downgrade`) or `unchecked` (no manifest);
- **signature status**: `ssh-verified`, `invalid` (refused; nothing overrides it) or `unsigned`
  (needs their consent), and any signer proposal that differs from their pin;
- **notices**, including "run HACF-install.sh to complete the upgrade" when a config file is
  missing, and any uncommitted changes that would stop the real run;
- the **sync plan** (`install`, `update`, `update-with-backup`, `remove-retired`,
  `keep-unshipped`, `refused-never-copy`), the instruction-file and `.agentignore` lines, and
  the bridge files that would need review;
- the **migration plan**. In a dry run it comes from the project's installed
  `hyper_migrate.py` (the dry run never executes the upstream's code), so say that the plan
  after the update may differ; the real run prints the plan from the synced script.

If the dry run itself was refused (exit 1), report the reason and stop.

### Phase 3: Hand off to the user's terminal

Give the user the exact command to run in their own terminal, from the project root:

```bash
python3 .agents/scripts/hyper_update_core.py
```

plus the same `--upstream-url` / `--ref` they asked for. Tell them what it will ask: consent to
an unsigned upstream (it names the URL and commit), adoption of a changed signer set, the
replace-or-sidecar choice for a customized instruction file, and the bridge merges. Say that
`--allow-unsigned` exists for a terminal session where they have already decided, and that it
is refused without a TTY and under `HACF_AUTONOMOUS=1`. Then wait for them to say it finished.

### Phase 4: Read the result

1. Read the last entry of `.agents/logs/hyper-update.log` (its `integrity` field is
   `ssh-verified`, `unsigned-consent-interactive` or `unsigned-consent-flag`) and the last line
   of `.agents/logs/update-consent.jsonl` when the upstream was unsigned.
2. Summarize: files installed, updated (with the backup dir), removed and kept; the instruction
   file outcomes (a sidecar to merge by hand, if any); bridges merged, skipped or left for
   review; the migration plan and its apply command
   (`python3 .agents/scripts/hyper_migrate.py apply`, theirs to run).
3. Suggest reviewing `git diff` and committing the update themselves.

## Autonomous mode

Under `HACF_AUTONOMOUS=1`, run Phase 1 only, report Phase 2, and stop. Record why: an update of
an unsigned upstream needs a person's consent in a terminal, and `--allow-unsigned` is refused
in autonomous mode. Do not hand off a command, and do not retry with any flag.

## Negative Space (Constraints)

- **DO NOT** run the update without `--dry-run` from your own shell.
- **DO NOT** pass `--allow-unsigned` or `--allow-downgrade`, or apply the migration.
- **DO NOT** tell the user to import a key globally, or to edit the signer pin by hand to get
  past a refusal.
- **DO NOT** copy files from the upstream yourself, edit bytes outside HACF blocks, or touch
  `CLAUDE.local.md` or `.claude/settings.local.json`.
- **DO NOT** delete `.agents/.backup/` dirs or anything under `.agents/state/migrations/`.

## Integration & Idempotency

Running the update again is safe: unchanged files stay unchanged, the manifest records the new
`installed_commit`, the dry run never writes, and every backup dir is timestamped. Backups older
than 30 days are removed at the next real update, except `*-migration` dirs and migration
backups under `.agents/state/migrations/`.
