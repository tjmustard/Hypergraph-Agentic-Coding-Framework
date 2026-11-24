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

- **plan** writes nothing and shows every change: the always-HACF files (roles.yml, caps.yml,
  OPS.md, bin/*), the adapted files (agents/**, startup/**, CULTURE.md, README.md, HARDENING.md;
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

## Rig env keys take effect at `rig down`/`rig up` (never a seat's step)

A rig-level or member `env:` key in the live spec reaches a seat only when the whole rig is
brought down and up again (KI-opsa-1). `rig seat launch --fresh` does not re-read it (OPS-A,
KI-opsa-1), so a fresh launch of one seat does not apply a changed key.
That restart is a whole-rig action at an idle point, and it is the operator's or the
person's call: a seat never runs `rig down` or `rig up` itself.
Until then, roles v2's bare denies cover the claude.ai connectors.
Never take the rig down and up to apply a seat rule; seat rules take a fresh launch per seat.

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
