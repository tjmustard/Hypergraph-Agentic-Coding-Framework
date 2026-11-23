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

## Which OpenRig versions HACF uses

HACF calls `rig` for two things: `rig proof add` when it writes a proof artifact, and
`rig scope audit` when `hyper_migrate.py verify` checks a mission. Whether it calls the `rig` on your `PATH`
depends on that rig's version (`rig --version`) and on the project's contract file.

**The contract file.** `.agents/config/openrig_contract.yml` lists the accepted versions (today
0.6.13, 0.7.16 and 0.7.21) and one probed range (0.7.x with x at least 16). The installer copies it
with the rest of `.agents/config/`, so a project's copy changes only when you upgrade HACF; nothing
else rewrites it. If the file is missing, unreadable or malformed, or has a key HACF does not know,
HACF drops the whole file, uses its compiled-in list (0.6.13, 0.7.16, 0.7.21) with no range, and
prints one warning. A doubt about the file never widens what HACF calls.

**The rule.**

| `rig --version` | What HACF does |
|---|---|
| a listed version | calls `rig` (delegates) |
| unlisted, inside the range (e.g. 0.7.22) | calls `rig` only after the range probe passed for it; otherwise writes the files itself and warns |
| unlisted, outside the range, same `major.minor` as a listed one (e.g. 0.7.15) | writes the files itself and warns |
| a new minor or major (e.g. 0.8.0) | never calls it; writes the files itself and warns that a new version arrives only through a HACF release |
| no `rig` found | writes the files itself, silently |

"Writes the files itself" means HACF produces the same OpenRig manifests and proof artifacts
without calling `rig`. Nothing fails; you see a warning.

**The range probe.** For an unlisted in-range version, HACF first checks that rig against exactly
what it delegates. In a throwaway git repo under `$TMPDIR`, it renders one mission with two slices
using HACF's own renderers and templates, then runs `rig --version`, `scope audit --json`,
`slice ls --state all --json`, `proof add` with the arguments HACF passes, a second `scope audit --json`, and
`--help` for those three commands. It compares the results with
`.agents/config/openrig_probe_expect.json`: every exit code, the exact set of files written, that
rig left HACF's files byte-identical, the proof artifact's header field by field, the JSON keys
HACF reads, and every flag HACF passes. The probe runs isolated: every `OPENRIG_*` and `RIGGED_*`
variable dropped, `OPENRIG_URL` pointed at a port where nothing listens, and `OPENRIG_HOME` and the
node cache in the probe's temp dir. It has a 20-second timeout. Anything but a pass (a failed check,
a timeout, a crash, a cache error) means HACF writes the files itself, plus a warning.

**What the probe cannot cover.** A pass says the delegated calls behave as expected on a small
example. It cannot see:
- anything that goes through the OpenRig daemon (the probe never contacts one);
- rules that only real content triggers, such as a large or unusual mission;
- a change that keeps the same output shape but changes its meaning;
- parts of rig HACF does not exercise (the probe does not run rig's own slice writers);
- performance at real scale;
- an in-place edit of the installed package that keeps the `rig` binary's modification time and
  size (the cache key would not change).

Listed versions are different: each was added by a HACF release after HACF's live contract test
passed on a real rig of that version.

**The cache.** Results live in `${XDG_CACHE_HOME:-~/.cache}/hacf/rig-probe/`, one file per key,
shared by every project on the machine. The key covers the `rig --version` output, the resolved
binary's path, modification time and size, and the bytes of HACF's probe code, contract file and
expectation file, so a new rig, a moved binary or a HACF upgrade probes again. A pass is kept for 7
days, any other result for 24 hours. The probe's own evidence (its commands, exit codes and the
differences it found) sits beside the cache entry, never in your project. If the cache dir is not
writable, HACF does not probe and writes the files itself. If a version delegated through the probe
later misbehaves in real use (`scope audit` in `hyper_migrate.py verify` exiting outside 0 and 1 or
printing JSON without the keys HACF reads, or `proof add` exiting non-zero), HACF deletes that cache
entry and warns, so the next call writes the files itself until a new probe passes. A failed
`proof add` is also an error for that call: `hyper_spec.py proof` exits 4 with `rig proof add failed`
and does not write the artifact itself.

**When it runs.** `hyper_spec.py proof` probes on a cache miss before it takes its lock, and does
not wait for a probe another process holds (that one call writes the files itself).
`hyper_migrate.py verify` waits for a running probe and then reads the result; if there is still
none, the mission's audit is reported as `skipped: rig probe pending (<version>)`, distinct from
`skipped: rig is native`, so an auditor never mistakes it for an accepted version's result.

**Commands.**

```bash
python3 .agents/scripts/hyper_openrig.py contract show [--json]
python3 .agents/scripts/hyper_openrig.py contract probe [--reprobe] [--json]
```

`contract show` prints the contract as HACF reads it, including when it fell back to the
compiled-in list. `contract probe` probes the resolved rig when it is an unlisted in-range version
(exit 0 pass, 1 otherwise, 2 when there is nothing to probe: rig is not on PATH, it is a listed
or out-of-range version, or the opt-out below removed the range); `--reprobe` drops the cached
result first. When the probe is off but the range stands (an unreadable or malformed
`.agents/local/hacf.yml`, a non-boolean `range_probe`, or rig run through `npx`), it prints the
outcome `off` and exits 1.

**Opting out.** To call only listed versions, never a probed one, set this in
`.agents/local/hacf.yml`:

```yaml
openrig:
  range_probe: false
```

Only the boolean `false` turns the range off. A missing file or key leaves the probe on. An
unreadable or malformed `hacf.yml`, or a value that is not a boolean, turns the probe off and
prints a warning.

**Getting a new version.** A new OpenRig version reaches a project only through a HACF release that
lists it, and that project's upgrade. Until then, an in-range version goes through the probe and
anything else is written natively.
