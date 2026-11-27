# Applying the role table to a running hacf rig

The operator's recipe for giving each seat of a running hacf rig its role's tool denies from
`roles.yml`, checking that they took, and rolling them back. `rig.yaml` never carries these rules
(it carries one rig-level `env:` key (v0.6.4); per-seat tool rules are still applied per seat, so it
launches on every OpenRig this rig is tested against); they are applied **per seat**, one seat at a
time, by a person or the operator acting for them. Nothing in this
spec applies them on its own.

The rules are native Claude Code tool rules, not a sandbox. The generator emits only denies:
under the shipped `builtin:yolo` an allow constrains nothing, and a deny still removes the tool.
Read OpenRig's `rig-spec.md`, "Per-member Claude tool rules", for what `--rules` accepts.

Placeholders: `<rig>` is the team's name (`rig whoami --json`, `identity.rigName`); `<seat>` is a
session address from the table below; `<dir>` is a folder outside any project that keeps the
records until the rollback window is over.

## Seats and their roles

From `rig.yaml`: each member's session address and the `roles.yml` role (its profile).

| Seat | Role |
|---|---|
| `orch-lead@<rig>` | `lead` |
| `spec-architect@<rig>` | `architect` |
| `spec-red-team@<rig>` | `red-team` |
| `spec-resolve@<rig>` | `resolve` |
| `build-execute@<rig>` | `execute` |
| `build-audit@<rig>` | `audit` |
| `desk-scribe@<rig>` | `scribe` |

## When

After the mission's last audit PASS, before the next mission's architect stage. The lead records
the B0 scorecard first (slice OPR.99.0.7.1's command, `dev/telemetry/collect_metrics.py` in HACF's
own repository), so the saving has a baseline to compare with.

## Before: the records

For every seat, from the project root:

```sh
python3 openrig/hacf/bin/seat-rules.py --check          # ok, exit 0, before anything else
rig seat status <seat> --json > <dir>/<seat>.before.json # the rollback record
python3 openrig/hacf/bin/seat-rules.py --role <role> --json > <dir>/<seat>.json
```

## Apply, one seat at a time

```sh
rig seat set-permissions <seat> --rules <dir>/<seat>.json --reason "hacf roles v2"
rig seat status <seat> --json      # the rules show in the next-launch plan
```

`set-permissions` changes only the next managed launch. The running occupant keeps its tools until
a fresh launch at a **durable point**: the seat's rows are handed off, blocked or done, and its
projects hold no uncommitted work of its own. Then:

```sh
rig seat launch <seat> --fresh --plan --json
rig seat launch <seat> --fresh --stop --reason "hacf roles v2"
```

One seat at a time; probe it (below) before the next. **orch-lead goes last**, by planned handover,
so the lead is never down while another seat is relaunching. Never `rig up` and never `rig down`
for this: both act on the whole rig.

## MCP: denied per server, never codebase-memory-mcp

Every role records `mcp: off`: no guidance file or HACF skill calls an MCP tool, and the code graph
is the `hyper_codegraph.py` CLI. On OpenRig 0.7.21 no `rig seat` verb sets MCP loading on an
existing seat, so MCP loading itself is not changed (`seat-rules.py --role` says so on stderr).
Roles v2 (v0.6.4) instead denies, by bare `mcp__<server>` rules, the MCP servers the start-up probe
listed on HACF's host (`dev/telemetry/startup-baseline-v064.md`). `codebase-memory-mcp` is never
denied, and `seat-rules.py --check` refuses a deny of it. A server on another host that is not in
the list keeps its tools, so the probe below still records any `mcp__` tool it sees.

## Turn-1 probe, per role

Right after the relaunched seat's first turn, send it one probe:

```sh
rig send <seat> "Probe, answer without calling any tool: which of these tools are in your tool list, loaded or deferred (named only): <the role's tools_deny>? Is any mcp__ tool in it?"
```

Expected: none of the denied tools. Record whether any `mcp__` tool is listed; MCP is not
changed, so expect some (account connectors and plugins). A denied tool that is listed means the
rules did not take: roll that seat back (below) and stop. Then record the seat's turn-1 context,
`contextUsage.totalInputTokens` from `rig ps --nodes --rig <rig> --full --json`, in the
scorecard notes. Without the probe, the saving is reported **`unknown`**, not assumed.

**The live turn-1 reading** is the first `rig ps` `contextUsage.totalInputTokens` sample after the
start-up delivery and before the seat's first tool call. Record it beside the start-up probe's
number (`dev/telemetry/startup_probe.py`, a `claude -p` reproduction); never compare the two, since
they measure different things.

## Compaction probe, per role

Right after the turn-1 probe, check that the rules survive a compaction:

```sh
rig send <seat> "/compact"
rig ps --nodes --rig <rig> --json     # wait until the seat is idle again
rig send <seat> "Probe, answer without calling any tool: restate each of these rules of yours in one line: footer-line-1, footer-line-2, no-co-author, push-only-with-the-person, no-secrets, no-no-verify, no-ask-user-question, no-git-add-all, lore-propose-only."
```

The names are the `REQUIRED` rows of `dev/tests/rig_spec/test_startup_rules.py`. Expected: each
rule restated, except `footer-line-2` while that row is still marked xfail (the two-line footer
lands in v064-process .1). A rule it cannot restate means the trim lost it: roll that seat back (below) and
stop.

## Rollback, per seat

```sh
rig seat set-permissions <seat> --rules <dir>/<seat>.rollback.json --reason "hacf roles v2 rollback"
```

`<seat>.rollback.json` holds the `allow` and `deny` rules the seat's `<seat>.before.json` record
listed under `permissions`, or `{"allow": [], "deny": []}` when it had none (empty lists clear the
seat's rules). Then a fresh launch at a durable point, as above:
`rig seat launch <seat> --fresh --plan --json`, then
`rig seat launch <seat> --fresh --stop --reason "hacf roles v2 rollback"`.
Never `rig up` and never `rig down` to roll back.

## Upgrading a live spec (v0.6.5)

`bin/seat-config-upgrade.py` carries HACF's seat configuration from a HACF checkout into one rig's
**live** spec. It changes files only; the seat half stays this recipe's per-seat steps. Run it from
the checkout, never from the live spec:

```sh
python3 openrig/hacf/bin/seat-config-upgrade.py plan  --live-spec <live spec dir> --rig <rig>
python3 openrig/hacf/bin/seat-config-upgrade.py apply --live-spec <live spec dir> --rig <rig>
python3 openrig/hacf/bin/seat-config-upgrade.py restore <backup dir>
```

- **plan** writes nothing and shows every change: the always-HACF files (OPS.md, bin/*), the
  defaults files (roles.yml, caps.yml, seat-config.yml; see the next subsection), the adapted files (agents/**, startup/**, CULTURE.md, README.md, HARDENING.md;
  one that is unmodified HACF text is replaced, a locally edited one is kept and its diff shown,
  `--take-hacf <rel>` replaces it), the one rig.yaml key (`env.ENABLE_CLAUDEAI_MCP_SERVERS`, or
  `rig.yaml: manual (<reason>)` for a shape it does not recognise), each seat's
  `set-permissions` command and rollback, and the guard and checkpoint host lines for the rig.
- **apply** first writes a backup to `$HOME/.hacf/<rig>/backups/seat-config-<UTC>/` (the whole
  spec, SHA256SUMS, each seat's `before.json` and `rollback.json`, the plan), outside the spec
  library, then writes each file atomically under a lock and runs `rig spec validate` (60 s). Any
  failure after the backup exists (validate non-zero or timed out, a write error, SIGTERM/SIGINT)
  restores the backup by itself and prints `apply failed (<reason>); auto-restored from <backup>`.
  If that restore fails too, it says `AUTO-RESTORE FAILED`: run `restore <backup>` by hand.
- **plan and apply refuse** a HACF checkout with uncommitted or untracked changes under the files
  they copy (commit or clean them first), and refuse while a backup still holds
  `APPLY_IN_PROGRESS` (an apply that was killed): run the `restore` it names.
- **restore** puts the files back byte for byte and prints each seat's rollback.
- **The seat half:** apply never runs `rig seat set-permissions`. Run the printed commands one
  seat at a time as in "Apply, one seat at a time" above. Live seats change at their next fresh
  launch at a durable point, orch-lead last, never by a mass restart (never `rig up`/`rig down`).

### The model lines, hidden defaults and overrides.yml (v0.6.6)

`plan` and `apply` also print, after the seat half, one block per seat saying whether its **model**
(and effort, when the defaults set one) must change. The "before" is the seat's **live** state from
`rig ps --nodes --full`, never a file version; the "after" is `bin/seat-config.py` over HACF's
`seat-config.yml` and your `overrides.yml`.

- **Model lines.** `model <role> <seat>: <live> -> <resolved> (source: default|preset:<n>|override)`,
  then the `rig seat set-model` (and `set-effort`) command, `rig seat launch <seat> --fresh --stop`,
  and `rollback: ...` (`set-model <seat> --model claude-opus-5-5`, then the same launch). The lead's
  block is last and its launch is a planned handover by the operator. A seat already on the resolved
  value reads `(unchanged)`. An unset model reads `unset (harness default)`; a record with no
  `model` key reads `live unknown`, with `verify with: rig seat effort <seat>`. Plan and apply print
  the same lines and run none of them: you run each seat's commands, one seat at a time.
- **`manual (...)`.** If `rig seat set-model --help` (or `set-effort`) fails on this host, or only
  prints `rig seat`'s own usage, that command line becomes `model: manual (rig seat set-model not
  available on <rig --version>)` and the rest still prints.
- **Hidden defaults.** Where HACF's new default for a role key differs from your live
  `seat-config.yml` (or is new) and your `overrides.yml` sets that key, plan prints `hidden by your
  override: <role>.<key> default <old> -> <new>; yours: <value> (overrides.yml); to adopt it, delete
  that line`, or `override now equals the default: <role>.<key>`. Every default no override hides
  applies with the upgrade.
- **overrides.yml is yours.** It is in no copy, backup, restore or checksum, and these tools never
  write, create or delete it; it stays as you left it through plan, apply, restore and an
  auto-restore. A locally edited `caps.yml` or `seat-config.yml` is kept, with
  `proposed override: <role>: {<key>: <value>}` lines; move the change into overrides.yml, then
  `--take-hacf <rel>` replaces the file. A locally edited `roles.yml` is refused (v0.6.7, below).
  `bin/*` and OPS.md are always replaced.

### Applying and reverting a model (v0.6.6)

The seats that ship a tiered default, and why, are in `dev/telemetry/h5-v066.md`; every other seat
ships `claude-opus-5-5`. Plan and apply only print the commands; these recipes say what to do with
them.

- **Apply** (an upgraded or a new team): run `seat-config-upgrade.py plan`, then run the printed
  `rig seat set-model` (and `set-effort`) commands seat by seat, each at an idle point, then its
  `rig seat launch <seat> --fresh --stop` line; orch-lead last, by planned handover. Verify each
  seat with `rig seat effort <seat>` and the whole rig with `rig ps --nodes --full --json`.
  `rig up` never sets a model: each seat runs on the harness default until the printed commands run.
- **Revert one seat to Opus:** add `<role>: {model: claude-opus-5-5}` under `seats:` in
  `overrides.yml`, run `plan`, and run the printed commands for that seat. There is no unset: the
  revert pins Opus, and a later HACF default for that role stays hidden (plan lists it) until you
  delete the line.
- **Revert the whole upgrade:** `seat-config-upgrade.py restore <backup dir>`. It prints each seat's
  rollback commands and never touches `overrides.yml`.
- The shipped model ids age while HACF is parked; when an id is retired, override it with the
  current one.

### Moving a local roles.yml edit into overrides.yml (v0.6.7)

Since v0.6.7, `plan` and `apply` **refuse** a locally edited `roles.yml`: keeping it would hide
HACF's new defaults, and replacing it would deny your tools again at the next fresh launch. The
edit moves into `overrides.yml`, which is yours and which no HACF tool writes. A `roles.yml` that
differs from HACF's only in comments or layout is not refused: it is replaced, with its diff shown.
The example is a rig that allows `Monitor` and `TaskStop` on execute, audit and lead.

1. Write `overrides.yml` in the live spec **first**:

   ```yaml
   schema: hacf.seat-overrides/v2
   seats:
     execute: {tools: {remove_deny: [Monitor, TaskStop]}}
     audit: {tools: {remove_deny: [Monitor, TaskStop]}}
     lead: {tools: {remove_deny: [Monitor, TaskStop]}}
   ```

2. Run `plan`. It refuses (exit 1) and prints the diff, the `overrides.yml` lines that carry your
   `tools_deny` edit and `next: overrides.yml already carries these`. If it names anything
   `not expressible in overrides.yml`, decide about that by hand before the next step: HACF's file
   replaces it.
3. Run `plan --take-hacf roles.yml`, read it, then `apply --take-hacf roles.yml`.
4. Run each seat's printed `rig seat set-permissions` line, then its fresh launch at an idle point
   (`rig seat launch <seat> --fresh --stop`), one seat at a time, orch-lead last.
5. Verify each seat with `rig seat status <seat> --json`: its rules lack `Monitor` and `TaskStop`.

### Seat config v2: when each key takes effect (v0.6.7)

Pass `--project-root <project>` to `plan` and `apply` so the project's own skills count as known.
After the model lines, the `keys (...)` section prints what is still owed. This script runs none of
it.

| Key | Apply path | Takes effect |
|---|---|---|
| `tools` | the seat's `rig seat set-permissions` line | at the next fresh launch |
| `skills` | `apply` renders the role's `uses.skills` in the live `agents/hacf/agent.yaml` | at the next fresh launch |
| `instructions` | `apply` renders the role's `startup.files` entries after its guidance file | at the next fresh launch |
| `harness` (`claude-code`, `codex`, `pi`) | the printed `rig seat set-runtime` line | when the successor confirms the handover |
| `routing` | none: each seat reads it through `seat-config.py resolve --section routing` | read live |

- Any other `harness` value, such as a community harness, is **deferred in v0.6.7** (OpenRig 0.7.23
  has no per-seat path for it): plan names it and nothing applies it.
- If `agent.yaml` has a shape the render does not recognise, plan prints
  `<key> <role>: manual (<reason>)`; that role is not rendered and the rest applies.
- `seat-config.rendered.json` in the live spec records what apply rendered, so a later upgrade
  replaces and re-renders that file instead of keeping it as your edit.
- A config `seat-config.py` refuses gives `manual` lines in place of the rules and keys; an
  `agent.yaml` an earlier apply rendered is then left as it is.
- `agents/hacf/local/` holds your instruction files. Like `overrides.yml`, it is in no copy,
  backup, restore or checksum, and no HACF tool writes it.

## Rig env keys are stored when a rig is created (never a seat's step)

A rig-level or member `env:` key reaches a seat from the copy OpenRig stores when it creates the rig
from its spec. Editing the live spec of an existing rig changes nothing for it (OPR.99.0.14.6,
OpenRig 0.7.23). `rig seat launch --fresh` does not re-read it, so a fresh launch of one seat does
not apply a changed key. Restoring the same rig does not re-read it either.
Only a new team created from the spec stores the new value: it starts without the old conversations,
and the old rig is archived. That is the operator's or the person's call: never a seat's step, and
a seat never runs `rig down` or `rig up` itself.
Until then, roles v2's bare denies cover the claude.ai connectors.
Never take the rig down and up to apply a seat rule; seat rules take a fresh launch per seat.

## The Herdr team view (herdr-view.py)

`bin/herdr-view.py` puts one team's Herdr view in order. Run it after each of these, and
after a resume:

- a launch with `rig up`, which the tool never runs;
- a return with `rig up <name> --existing`, which the tool never runs.

```sh
python3 <hacf checkout>/openrig/hacf/bin/herdr-view.py --rig <rig> --dry-run   # the plan; writes nothing
python3 <hacf checkout>/openrig/hacf/bin/herdr-view.py --rig <rig>
```

In order, it:

1. sets each seat's tmux window to `window-size latest` where it is anything else;
2. opens the team's Herdr space with `rig terminal open` when none is labelled `<rig>`, or closes
   and reopens one whose panes show `detached (from session` (only when every pane in it is a seat
   pane or an idle shell);
3. lays the seats out from the rig's spec: the lead full width on top, then rows of three (pods
   `orch`, `build`, `desk`, `spec`, then any other pod). A view already in the rows is not moved;
4. closes idle shells, and gives the focus back to the space that had it.

It writes to no other space, never swaps panes, and types nothing into a pane.

**Dotted filler on the right of every pane** means the seat's tmux window was left at
`window-size manual`, 80x24. To fix only that, with no Herdr call:

```sh
python3 <hacf checkout>/openrig/hacf/bin/herdr-view.py --rig <rig> --fix-size-only
```

**The idle-shell rule.** A pane is closed only when all of these hold: its label is not a seat's;
Herdr reports no agent for it; its one foreground process is its own shell, named `bash`, `zsh`,
`sh`, `fish` or `dash`; and that shell has no child process. Any other pane in the lead's tab moves
to a tab of its own.

`--dry-run` lists every close and move, in order. For a space that would be reopened it stops at the
reopen, since the new panes do not exist yet.

Exit 0: arranged, already arranged, or nothing to do (one line says which). Exit 1: a `rig`, `tmux`
or `herdr` call failed, the final layout is not the planned one, focus was not restored, or the
space was left alone (two spaces with the label, or a detached one holding other panes). Exit 2:
usage.

`bin/view-layout.sh` is deprecated. It behaves as before and now says so on stderr.

## OPS-A: rig hacf's live spec only

The shipped `rig.yaml` sets `ENABLE_CLAUDEAI_MCP_SERVERS: "false"` for every seat (v0.6.4), which
turns off the account's claude.ai connectors and keeps project MCP servers. It never ships
`claude_mcp: none`, because that also removes project servers such as `codebase-memory-mcp`.

For a project that uses no MCP server at all, the operator may add `claude_mcp: none` to each
member of that rig's **live** spec. The only such rig is `hacf` working in HACF's own repository,
whose roles record `mcp: off`. **Never on eta or cleanroom**, or any rig whose project keeps
`codebase-memory-mcp`.

```sh
cp -p <live spec dir>/rig.yaml <dir>/rig.yaml.before      # a byte backup
# edit <live spec dir>/rig.yaml: add `claude_mcp: none` to each member, nothing else
rig spec validate --json <live spec dir>/rig.yaml          # valid: true, or restore and stop
```

Then a fresh launch per seat at a durable point, one seat at a time and orch-lead last, as in
"Apply, one seat at a time" above; after each launch, the turn-1 probe and the compaction probe.

Rollback: restore the backup byte for byte, then a fresh launch per seat at a durable point.

```sh
cp -p <dir>/rig.yaml.before <live spec dir>/rig.yaml
cmp <dir>/rig.yaml.before <live spec dir>/rig.yaml          # no output: identical
rig seat launch <seat> --fresh --plan --json
rig seat launch <seat> --fresh --stop --reason "hacf claude_mcp rollback"
```
