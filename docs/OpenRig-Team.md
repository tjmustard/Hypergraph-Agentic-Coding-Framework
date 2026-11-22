# Running HACF with an OpenRig team

HACF ships the spec of a seven-seat [OpenRig](https://www.openrig.dev) team under `openrig/hacf/`:
a lead, an architect, a red team, a resolve seat, a builder, an auditor and a scribe. Each stage of
the HACF pipeline runs in its own seat. `openrig/hacf/README.md` describes the seats and how work
moves between them. This page covers starting a team for one of your own projects.

## Start a per-project HACF team

### 1. Copy the team spec into your project

The installer copies the team spec only when you ask for it. Run it from your project's root:

```bash
bash HACF-install.sh --rig            # copy to openrig/hacf/ in this project
bash HACF-install.sh --rig=<dir>      # or copy it to another folder, e.g. ~/.openrig/specs/<name>
```

- **Install from a local checkout for now.** Until HACF v0.6.x is published, the default upstream
  (`main`) is still v0.5.9, which has no `--rig`, and its `--dry-run -y` was seen writing files.
  Install from a checkout of a v0.6.x commit instead:

  ```bash
  bash <checkout>/HACF-install.sh --source=<checkout> --ref=<commit> --rig
  ```

- Without `--rig`, an interactive install asks once, `Copy HACF's OpenRig team spec to openrig/hacf/
  for a per-project team? [y/N]`. The default is No. `-y` and runs without a terminal never copy
  without the flag. A copy outside the project is made only by `--rig=<dir>`.
- `--dry-run --rig` prints `[DRY-RUN] would copy openrig/hacf/ (<n> files) to <DEST> as rig
  <slug>-hacf` and writes nothing.
- The installer never writes over an existing path. If the destination exists in any form, or lies
  inside `.git`, inside the `--source` checkout or under a folder that holds a `rig.yaml`, it stops
  with `Nothing was written.` A second `--rig` after a copy is refused too: re-run without `--rig`.
- The copy's rig is named `<slug>-hacf`. `<slug>` is your project folder's name, lowercased, with
  every run of other characters turned into `-` (`My Project` becomes `my-project-hacf`). The copy
  differs from HACF's spec only in that one `name:` line.
- The install report prints the exact next command.

**The copy is yours.** The manifest records where it went and from which commit
(`rig_spec` in `.agents/hacf-manifest.json`; a copy outside the project is recorded without its
path). An upgrade never refreshes it, so your edits stay. Uninstall never removes it and prints a
line saying it is still there. To take a newer HACF team spec, move your copy aside and run the
installer with `--rig` again.

### 2. Check that the team name is free

```bash
rig ps
```

The `<slug>-hacf` name must not be in the list. If it is, see [Recover a team](#recover-a-team)
instead: **`rig up <spec>` under a name that already exists replaces that team.**

### 3. Plan, then launch

From the project root:

```bash
rig up --plan openrig/hacf/rig.yaml --name <slug>-hacf --cwd <project root>
rig up openrig/hacf/rig.yaml --name <slug>-hacf --cwd <project root>
```

- Run `--plan` first. It must report `planned` with no errors, and it starts nothing. When you
  record a plan check, record the HACF commit the spec was copied from (the manifest's
  `rig_spec` entry) with it.
- Always pass `--name`. The spec's own `name:` line already says `<slug>-hacf`, but `--name` makes
  the name explicit on every launch.
- Always pass `--cwd <project root>`. The spec's seats declare `cwd: ../..`, which from
  `<project root>/openrig/hacf/` is the project root. `--cwd` keeps that true wherever the copy is.
- **A copy outside the project always needs `--cwd`.** OpenRig would resolve its relative `cwd`
  somewhere else:

  ```bash
  rig up --plan <dir>/rig.yaml --name <slug>-hacf --cwd <project root>
  rig up <dir>/rig.yaml --name <slug>-hacf --cwd <project root>
  ```

## What a launch was recorded doing

HACF recorded one throwaway launch on OpenRig 0.7.16. It used a project copy, with the shipped
`permission_policy: builtin:yolo` and `non_interruptive: true`. The results below hold for that
setup only. **The default permission mode was not tested.** If you change those two lines, check
the points below yourself.

- **Working directory.** The launch passed no `--cwd`, and all seven seats still ran in the project
  root (`cwd: ../..` resolved as intended). The commands above pass `--cwd` anyway.
- **Deny rules and the gate hook loaded.** HACF's `.claude/settings.json` deny rules applied under
  bypassPermissions: a Read of `.git/config` was refused. The lore gate hook also refused a gated
  `hyper_lore.py approve` before the script ran.
- **No seat stopped at a prompt.** No seat showed a permission or folder-trust prompt. OpenRig
  accepted Claude's bypass-permissions warning for each seat through its own launch flags
  (`non_interruptive`), not through an answer typed into a seat. No recovery was needed.
- **The lead contacts you.** A launched lead with no goal filed a decision row to the person within
  about 40 seconds, asking for the project root and a feature goal. Expect that question. Give it
  your goal. If you launch only to check a launch, tell whoever reads your queue to ignore it.

### Files a launch writes in the project root

The launch wrote these files in the project root:

| Path | After the launch | After `rig down <name> --delete` |
|---|---|---|
| `CLAUDE.local.md` | untracked | removed |
| `.claude/settings.local.json` | ignored | **left** (content `{}`) |
| `.openrig/context-collector.cjs` | ignored | removed |
| `.claude/plugins/openrig-core/` (13 files) | untracked | **left** |

No existing file (`.claude/settings.json`, `.gitignore`, `CLAUDE.md`) was modified.

Keep these files out of your commits with your repository's local exclude file. Never edit HACF's
managed `.gitignore` block:

```bash
printf '%s\n' CLAUDE.local.md .claude/settings.local.json .openrig/context-collector.cjs \
  .claude/plugins/openrig-core/ >> .git/info/exclude
```

In a linked worktree, `.git` is a file. Use `"$(git rev-parse --git-common-dir)/info/exclude"`
there instead.

After `rig down --delete`, delete `.claude/plugins/openrig-core/` and `.claude/settings.local.json`
yourself if you no longer want them.

## Recover a team

After a reboot or a stopped session, bring the same team back by its name:

```bash
rig up <slug>-hacf --existing
```

**The trap:** `rig up openrig/hacf/rig.yaml` (or any spec path) under a name that already exists
does not resume that team. It archives the old roster and starts a new one. To get your team back,
use `--existing` with the name. Use a spec path only for a name that `rig ps` does not list, and
run `--plan` first.
