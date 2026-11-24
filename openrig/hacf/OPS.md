# Applying the role table to a running hacf rig

The operator's recipe for giving each seat of a running hacf rig its role's tool denies from
`roles.yml`, checking that they took, and rolling them back. `rig.yaml` never carries these rules
(it stays key-free, so it launches on every OpenRig this rig is tested against); they are applied
**per seat**, one seat at a time, by a person or the operator acting for them. Nothing in this
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
rig seat set-permissions <seat> --rules <dir>/<seat>.json --reason "hacf roles v1"
rig seat status <seat> --json      # the rules show in the next-launch plan
```

`set-permissions` changes only the next managed launch. The running occupant keeps its tools until
a fresh launch at a **durable point**: the seat's rows are handed off, blocked or done, and its
projects hold no uncommitted work of its own. Then:

```sh
rig seat launch <seat> --fresh --plan --json
rig seat launch <seat> --fresh --stop --reason "hacf roles v1"
```

One seat at a time; probe it (below) before the next. **orch-lead goes last**, by planned handover,
so the lead is never down while another seat is relaunching. Never `rig up` and never `rig down`
for this: both act on the whole rig.

## MCP: not changed

Every role records `mcp: off`: no guidance file or HACF skill calls an MCP tool, and the code graph
is the `hyper_codegraph.py` CLI. On OpenRig 0.7.21 no `rig seat` verb sets MCP loading on an
existing seat, so this recipe changes nothing about MCP, and the MCP saving is reported
**`unknown`**. `seat-rules.py --role` says the same on stderr.

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

## Rollback, per seat

```sh
rig seat set-permissions <seat> --rules <dir>/<seat>.rollback.json --reason "hacf roles v1 rollback"
```

`<seat>.rollback.json` holds the `allow` and `deny` rules the seat's `<seat>.before.json` record
listed under `permissions`, or `{"allow": [], "deny": []}` when it had none (empty lists clear the
seat's rules). Then a fresh launch at a durable point, as above:
`rig seat launch <seat> --fresh --plan --json`, then
`rig seat launch <seat> --fresh --stop --reason "hacf roles v1 rollback"`.
Never `rig up` and never `rig down` to roll back.
