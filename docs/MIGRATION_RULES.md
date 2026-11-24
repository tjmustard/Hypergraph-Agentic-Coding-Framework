# Migration Rules

This guide covers updating HACF in a project (ADR-0010), moving v0.5.x specs to the v0.6.0
layout, and, at the end, the historical v0.5.x schema migration.

<a id="updating-hacf-adr-0010"></a>
## Updating HACF (ADR-0010)

**First, delete any saved copy of HACF-uninstall.sh.** A saved v0.5.x uninstaller deletes every
project file whose path also exists in the HACF repository, including `.gitattributes`,
`lore_hypergraph.yml`, `SPEC.md`, `project.yaml`, `workspace.yaml` and `docs/*`.

`/hyper-update` runs only the read-only preview; you run the update itself in your own terminal:

```bash
python3 .agents/scripts/hyper_update_core.py --dry-run   # fetch, check ancestry and signature, print the plans; write nothing
python3 .agents/scripts/hyper_update_core.py             # the update
```

### Signatures and trust

- **Releases are unsigned in v0.6.x.** An unsigned upstream needs your consent in a terminal:
  answer the prompt, or pass `--allow-unsigned`. `--allow-unsigned` needs a TTY and is refused
  under `HACF_AUTONOMOUS=1`, so **unattended CI updates are not possible** while
  releases are unsigned.
- **Any future signing is SSH-only.** The updater checks a signature with
  `git -c gpg.ssh.allowedSignersFile=<temporary file> verify-commit`. An upstream config that lists
  `gpg_fingerprints` is refused.
- **Never import a signing key globally**: no key in a global keyring and no signing setting in
  your global git config. HACF does not need one, and a globally trusted key would arm a saved
  v0.5.x updater's delete path.
- **Signers are pinned locally on first trust**, in `.agents/state/upstream-trust.json`
  (machine-local, never synced). The upstream's `.agents/config/upstream.yml` is only a proposal; a
  change to the pinned set needs your interactive consent.
- Every consent is logged to `.agents/logs/update-consent.jsonl` with the effective URL and commit.

### Upstream and ancestry

- The upstream changes only through `--upstream-url <https-url>` and `--ref <ref>`; non-`https://`
  URLs are refused. The environment overrides `HACF_REPO_URL`, `HACF_BRANCH` and
  `HACF_UPSTREAM_URL` apply only with `HACF_TEST=1`.
- There is no local counterpart: `/hyper-update` has no `--source`. A project installed with
  `HACF-install.sh --source` is updated by re-running `HACF-install.sh` from that checkout with
  `--ref=<commit>`; see the CHANGELOG known issues.
- **A re-run without `--ref` is pinned (v0.6.3+).** A plain re-run, or `--source=<the recorded
  checkout>` without `--ref`, reinstalls the manifest's `installed_commit`. When the checkout's `HEAD`
  has moved, it refuses and writes nothing:

  ```text
  refusing a plain re-run: the recorded HACF checkout <path> is at <head sha>, but this project is installed at <installed sha>.
    to reinstall the installed commit:  bash <path>/HACF-install.sh --ref=<installed sha>
    to move to the checkout's HEAD:     bash <path>/HACF-install.sh --ref=<head sha>
  Nothing was written.
  ```

  Run one of the two commands as printed. This protects re-runs of a v0.6.3+ installer; a checkout
  moved to an older commit runs that commit's older installer.
- **Anti-rollback:** the installed commit is recorded in `.agents/hacf-manifest.json`. An upstream
  commit that does not descend from it is refused unless you pass `--allow-downgrade`.
- Only `.agents/skills/`, `.agents/scripts/` and `.agents/schemas/` are synced; `AGENTS.md`,
  `CLAUDE.md`, `GEMINI.md` and `.agentignore` get only their HACF block. Your specs, tests, lore,
  missions and settings are never synced.

<a id="migrating-specs-to-v060"></a>
## Migrating specs to v0.6.0

```bash
python3 .agents/scripts/hyper_migrate.py detect    # report the legacy signals; write nothing
python3 .agents/scripts/hyper_migrate.py plan      # print the deterministic action list; write nothing
python3 .agents/scripts/hyper_migrate.py apply     # backup and journal first, then run the plan
python3 .agents/scripts/hyper_migrate.py verify    # lint, ledger and lore validate, firewall check
```

- `apply` needs a typed `yes` on a TTY. `--yes` skips the prompt and is refused under
  `HACF_AUTONOMOUS=1`; an agent shell can still pass `--yes`, so the rule is procedural: only a
  person decides to migrate. `--allow-dirty`, `--no-chain` and `--include-archived` are the other
  `apply` options.
- `rollback --journal .agents/state/migrations/v0.6.0.json` restores the pre-apply tree from the
  backup. It **refuses on drift**: when a path changed since the apply it lists the paths, restores
  nothing and exits 5; `--force` restores over them.
- Legacy flat `spec/compiled/MiniPRD_*.md` files are still read until v0.7.0.

---

## Historical: Persistent Rules (spec/schemas → CLAUDE.md, v0.5.x)


**Effective Date:** 2026-05-03  
**Deadline:** 2026-06-17 (6-week deprecation period)  
**Status:** In progress

---

## Overview

Static schema definitions have been migrated from individual files in `spec/schemas/` to CLAUDE.md
to reduce per-message token overhead. This guide helps you update references in your skills or workflows.

---

## What Was Migrated

The following static schemas have been moved to CLAUDE.md:

| Old Location | New Location | Purpose |
|---|---|---|
| `.agents/schemas/SuperPRD_Template.md` | CLAUDE.md: Schema Definitions › SuperPRD Schema | High-level feature spec template |
| `.agents/schemas/MiniPRD_template.md` | CLAUDE.md: Schema Definitions › MiniPRD Schema | Modular spec template |
| `.agents/schemas/hypergraph_schema.md` | CLAUDE.md: Schema Definitions › Hypergraph Schema | Dependency graph structure |

These three templates were removed in v0.6.0.

**Custom templates** (DESIGN_Template.md, plan_Template.md, etc.) remain in `.agents/schemas/` for project-specific use.

---

## Migration Checklist

### For Skill Developers

If your skill references old schema files:

- [ ] **Step 1: Find References**
  ```bash
  grep -r "spec/schemas/" .agents/skills/[your-skill]/SKILL.md
  ```

- [ ] **Step 2: Update Instructions**
  Replace:
  ```
  Read `.agents/schemas/SuperPRD_Template.md` to ensure correct structure.
  ```
  
  With:
  ```
  Review CLAUDE.md: "Schema Definitions › SuperPRD Schema" to ensure correct structure.
  ```

- [ ] **Step 3: Test**
  - Run your skill
  - Verify no "file not found" errors
  - Confirm behavior unchanged

- [ ] **Step 4: Commit**
  ```bash
  git add .agents/skills/[your-skill]/SKILL.md
  git commit -m "Migrate schema reference to CLAUDE.md"
  ```

### For Custom Skills

If you created a custom skill that references schema files:

1. Check if your references are in `.agents/schemas/`:
   ```bash
   ls .agents/schemas/
   ```

2. For each file you reference:
   - If it's `SuperPRD_Template.md`, `MiniPRD_template.md`, or `hypergraph_schema.md`: **Update to CLAUDE.md**
   - If it's a custom template: **Keep referencing `.agents/schemas/`**

3. Update your skill instructions accordingly

---

## Examples

### Example 1: SuperPRD Reference (Migrated)

**Before:**
```markdown
## Phase 5: Draft Generation
1. Read `.agents/schemas/SuperPRD_Template.md` to ensure correct output structure.
2. Generate the complete `Draft_PRD.md` ...
```

**After:**
```markdown
## Phase 5: Draft Generation
1. Review CLAUDE.md: "Schema Definitions › SuperPRD Schema" to ensure correct output structure.
2. Generate the complete `Draft_PRD.md` ...
```

---

### Example 2: MiniPRD Reference (Migrated)

**Before:**
```markdown
When creating a MiniPRD, follow the structure in `.agents/schemas/MiniPRD_template.md`.
```

**After:**
```markdown
When creating a MiniPRD, follow the structure in CLAUDE.md: "Schema Definitions › MiniPRD Schema".
```

---

### Example 3: Custom Template (Not Migrated)

**Before & After (No Change):**
```markdown
Custom templates are stored in `.agents/schemas/` (e.g., `DESIGN_Template.md`).
```

Custom templates like `DESIGN_Template.md`, `plan_Template.md`, etc., are NOT being migrated.
Continue referencing `.agents/schemas/` for these files.

---

## Backward Compatibility

### During Deprecation Period (Until 2026-06-17)

Old files remain in place with deprecation headers. Both old and new references work:

```
✅ CLAUDE.md: "Schema Definitions › SuperPRD Schema"  (NEW)
✅ .agents/schemas/SuperPRD_Template.md  (OLD, deprecated)
```

**Warning message** added to old files; no functional impact.

### After Deadline (2026-06-17)

Old files will be deleted. CI lint will enforce CLAUDE.md-only references:

```
❌ .agents/schemas/SuperPRD_Template.md  (REMOVED)
✅ CLAUDE.md: "Schema Definitions › SuperPRD Schema"  (REQUIRED)
```

---

## Deprecation Headers

When the migration started, deprecation headers were added to affected files:

```markdown
# ⚠️ DEPRECATED: SuperPRD Template

**Status:** Migrated to CLAUDE.md (See CLAUDE.md: Schema Definitions › SuperPRD Schema)
**Deadline for Removal:** 2026-06-17 (6-week deprecation period)

This file is maintained for backward compatibility only. New references should use CLAUDE.md.
```

You'll see this at the top of:
- `.agents/schemas/SuperPRD_Template.md`
- `.agents/schemas/MiniPRD_template.md`
- `.agents/schemas/hypergraph_schema.md`

---

## FAQ

### Q: Can I still use the old files during the deprecation period?
**A:** Yes. Both old and new references work until 2026-06-17. We recommend updating at your convenience.

### Q: What happens if I don't update by the deadline?
**A:** After 2026-06-17, CI lint will fail if your skill references old schema files. You'll need to update before merging.

### Q: Do I need to update custom templates like DESIGN_Template.md?
**A:** No. Custom templates are NOT being migrated. Continue referencing `.agents/schemas/` for these.

### Q: How much token overhead does this save?
**A:** Approximately 10-15% per-message reduction by embedding schemas in CLAUDE.md instead of reading separate files.

### Q: What if I'm creating a new skill?
**A:** Reference CLAUDE.md for static schemas (SuperPRD, MiniPRD, hypergraph). Example:
```markdown
Review CLAUDE.md: "Schema Definitions › [Schema Name]" to understand the structure.
```

---

## Support

- **Questions?** See CLAUDE.md: Schema Definitions
- **Migration Issues?** Check that your skill is reading from CLAUDE.md (not old files)
- **Custom Template Questions?** Continue using `.agents/schemas/` for project-specific templates

---

## Timeline

| Date | Event |
|---|---|
| 2026-05-03 | Migration starts; schemas moved to CLAUDE.md |
| 2026-05-03 to 2026-06-17 | 6-week deprecation period; both old and new work |
| 2026-06-17 | Old files deleted; CI lint enforces CLAUDE.md-only |
| 2026-06-18+ | Old schema files no longer available |
