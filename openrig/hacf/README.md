# hacf

A seven-seat OpenRig team that runs the [Hypergraph Agentic Coding
Framework](https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework): one seat for
each of its stages, one to hold the sequence, one to keep the record.

The framework's SOP asks every phase boundary to happen in a **new context window**, because
a red team that saw the architect's reasoning inherits the architect's assumptions. In this
rig that separation is not a discipline anybody has to remember — each stage is a separate
terminal with its own context, and the lead clears a seat before each new stage run.

| Seat | Stage | Produces |
|---|---|---|
| `orch-lead@<rig>` | The sequence | `~/.hacf/<rig>/current-run.md`; dispatches every stage; decides when a feature is done |
| `spec-architect@<rig>` | `/hyper-architect` | `spec/active/Draft_PRD.md` |
| `spec-red-team@<rig>` | `/hyper-redteam` | `spec/active/RedTeam_Report.md` |
| `spec-resolve@<rig>` | `/hyper-resolve` | `missions/<mission>/SPEC.md` + `slices/<NN-slug>/SPEC.md`, active flushed |
| `build-execute@<rig>` | `/hyper-execute` | code + commit + the ledger's blast radius |
| `build-audit@<rig>` | `/hyper-audit` | verdict, reconciled ledger, the slice `audited` |
| `desk-scribe@<rig>` | `/hyper-document` + commit | updated docs/changelog/memory, a docs commit, the push command |

`<rig>` is the team's name: `hacf` for HACF's own team, `<slug>-hacf` for a per-project copy
(below). Talk to `orch-lead@<rig>` for the sequence. Two stages also reach you directly: the
architect posts its interview questions, and resolve raises each trade-off it needs decided.

A seat's address is `<pod>-<member>@<rig>` — the pod id is part of it, so `lead@<rig>` is not a
seat and a queue row sent there is delivered to nobody.

## What this rig does not carry

The stage skills (`hyper-architect`, `hyper-redteam`, …) are **not** bundled here. They are
installed by the framework into each project at `.agents/skills/`, so a stage's definition lives
with the project, is versioned with the project, and moves when the project's framework is
upgraded. A seat reads `.agents/skills/hyper-<stage>/SKILL.md` from the project it is working in.

What the rig adds is the part the framework leaves to the person: who does which stage, how
work moves between terminals without a stage being skipped or done twice, and what is
allowed to be published.

## Where it works

`startup/context.md` gives every seat one rule for its mode:

- **Project mode** — the seat's working directory holds `.agents/skills/hyper-architect/SKILL.md`
  and is the git top level. The team works in that one project, and the run record's project
  root must equal the working directory.
- **Parent-folder mode** — anything else. The working directory holds projects, and the lead
  names the project root for a run in `~/.hacf/<rig>/current-run.md`; every other seat reads it
  from there and from its handoff. No seat infers a repository.

The `<rig>` segment of the run record's path is why two rigs on one host never overwrite each
other's record of which project they are on. Do not run two features at once in one project —
the ledger cannot take parallel writers, and the framework says so.

## Launching it

`rig.yaml` declares `cwd: ../..` for all seven seats, which from `<project>/openrig/hacf/` is the
project root, and `name: hacf`. There are three places a copy of this spec can live, and each
has its own command. Run `--plan` first every time: it previews the launch and changes nothing.

1. **A project copy**, at `<project root>/openrig/hacf/` (the installer's `--rig`). The copy's
   `name:` is `<slug>-hacf`, where `<slug>` is the project directory's name lowercased with every
   other run of characters replaced by `-`:

   ```sh
   rig up <project root>/openrig/hacf/rig.yaml --name <slug>-hacf --cwd <project root> --plan
   rig up <project root>/openrig/hacf/rig.yaml --name <slug>-hacf --cwd <project root>
   ```

2. **A copy outside the project** (`--rig=<dir>`). A relative `cwd` would resolve inside the
   OpenRig home, so it always needs `--cwd`; the install report prints the exact command:

   ```sh
   rig up <dir>/rig.yaml --name <slug>-hacf --cwd <project root> --plan
   rig up <dir>/rig.yaml --name <slug>-hacf --cwd <project root>
   ```

3. **HACF's own team**, from the framework repository's copy, in parent-folder mode:

   ```sh
   rig up <HACF checkout>/openrig/hacf/rig.yaml --name hacf --cwd <the folder that holds your projects> --plan
   rig up <HACF checkout>/openrig/hacf/rig.yaml --name hacf --cwd <the folder that holds your projects>
   ```

Write the folder you launched from into the run record as `Rig spec:` — `bin/brief.sh` points
every briefed seat at its role file there.

**Recovery** after a reboot or a stopped team brings the same seats back, with their
conversations:

```sh
rig up <name> --existing
```

**Warning:** `rig up ./rig.yaml` (or any spec path) under a name that already exists **replaces
that team** — it archives the old roster and starts a new one, which is the opposite of what you
want after a reboot. Always pass `--name`, run `--plan` first, and use `--existing` to recover.

The per-project walkthrough, with what a launch leaves in the project root and how to keep it
out of commits, is `docs/OpenRig-Team.md` in the HACF repo.

## Before a project works

A project must be onboarded with the framework before any stage means anything:

```sh
cd <project root>
ls .agents/skills/hyper-architect/SKILL.md spec/ AGENTS.md CLAUDE.md
```

If those are missing, the lead installs the framework first — **in both cases**, because
`/hyper-discover` and `/hyper-baseline` are framework skills, so an existing repository needs the
installer too. Then `/hyper-discover` + `/hyper-baseline` (existing code), or straight to the
architect (greenfield). The installer is the framework's own, `HACF-install.sh` from
`https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git`, run from the project's git
top level (it refuses anywhere else, with `Nothing was written.`):

```sh
HACF="$(mktemp -d)/hacf"
git clone --quiet https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git "$HACF"
bash "$HACF/HACF-install.sh" --dry-run -y --ides=claude   # prints every write, writes nothing
bash "$HACF/HACF-install.sh" -y --ides=claude             # installs upstream main
```

- **Pin it** with `--source=<checkout> [--ref=<commit, branch or tag>]`: it installs from a commit
  of a local checkout (never its working tree) and prints the full sha before any write.
  Otherwise it installs upstream `main`; say so in the report.
- **`--dry-run` is the preview**: it prints every write, merge and download and writes nothing.
- **Consent is per flag.** `-y` answers with defaults and consents to nothing; `--install-deps`,
  `--code-graph=…`, `--install-code-graph`, `--statusline` and `--migrate` are each the consent for
  their action. `HACF-install.sh --help` lists them all.
- **Prerequisites:** `git`, and `python3` >= 3.10 with PyYAML >= 5.4; the installer checks both
  before it writes.
- **The installer never copies its own `docs/`.** `docs/MasterSOP.md` exists only in the framework
  repository, so no seat may be sent to read it in a project.

`guidance/lead.md` Phase 0 has the first commit and what to check afterwards.

### What git actually holds

The installer writes HACF's managed block into the project's `.gitignore`. It ignores
per-machine paths (`.agents/state/`, logs, backups) and **not** `spec/`, `missions/`, `lore/` or
the ledger, so those are tracked or untracked as the project decides:

- every seat **stages explicit paths** and never runs `git add -A` or `git add .`;
- the **builder** checks `git ls-files --error-unmatch spec/compiled/architecture.yml` before
  assuming either way, and stages the ledger only if it is tracked;
- the **audit** reads the ledger from disk when it is untracked — an empty `git show` is not
  evidence that the builder skipped the blast radius.

## Defaults and per-run grants

What running the pipeline taught is in `startup/protocol.md`, split two ways:

- **Defaults** ("Defaults learned the hard way") hold on every run: fresh context per stage run,
  the `/clear` mechanics, nudging a just-cleared seat, loop files in `~/.hacf/<rig>/loops/`, the
  test-run rule, and escalation to the advisor when one exists.
- **Per-run grants** ("Per-run grants") hold only when the person gives one for a run: the
  autonomous-run rule, pre-approved spec-required waivers, lead-approved mechanical Replans, and
  the lore copy-aside around `hypergraph_updater.py`. The lead records each one in the run record
  with the person's words and the row id; nothing in this spec grants one.

## The brief

`bin/brief.sh execute|audit <slice> <SID> <START_SHA> [extra-notes-file]` writes the brief the
lead hands a just-cleared execute or audit seat. It takes the rig name from `rig whoami --json`
and everything else — project root, feature, `Base`, branch, `Rig spec:`, and the
`## Per-run grants` / `## Environment` sections, verbatim — from that rig's own run record, so it
never names another rig's seats. The lead copies it to `~/.hacf/<rig>/bin/` and runs it there.

## Context guard

`bin/context-guard.sh --rig <rig> --project-root <dir> [--project-root <dir> …] --caps <file> [--apply] [--arm-checkpoints] [--log <file>]`
keeps seats from dragging a huge context from one slice into the next. One run is one tick: it
restarts at most **one** idle, non-lead seat whose context is over its role's cap in `--caps`
with `rig seat launch <seat> --fresh --stop --reason …`, then tells `orch-lead` with `rig send`.
It is **dry-run by default**: without `--apply` it only reads (`rig ps`, `rig queue list`,
`rig seat launch … --fresh --stop --plan`) and logs `action=would-restart`.

### The caps: `caps.yml`

`--caps` is required; without it the guard exits 2 with its usage line. `caps.yml` holds one entry
per role of `roles.yml` (TJ's starting values of 2026-11-23, to be re-evaluated when a seat's
baseline changes, and per release or month):

| role | `cap_tokens` | `behaviour` | `reset_scope` |
|---|---|---|---|
| lead | 350000 | `planned_handover` | mission |
| architect | 300000 | `restart_at_durable_point` | mission |
| red-team | 250000 | `restart_at_durable_point` | mission |
| resolve | 300000 | `restart_at_durable_point` | mission |
| execute | 300000 | `checkpoint_message` | slice |
| audit | 250000 | `restart_at_durable_point` | slice |
| scribe | 150000 | `restart_at_durable_point` | mission |

A seat's role is its `profile` in `rig ps`. The guard checks the file with PyYAML against the
`roles.yml` beside it and refuses to run (exit 2, one line on stderr) on a wrong `schema`, a
missing or unknown role, a `cap_tokens` that is not a positive integer, an unknown `behaviour` or
`reset_scope`, or a `reevaluate` that is not a list of strings. There is no cap in the code.
`HACF_CTX_MAX` is no longer read: when it is set, each tick logs
`note=HACF_CTX_MAX ignored; caps come from --caps` and carries on.

### Re-evaluating the caps

The caps are starting values. Re-evaluate them on any trigger listed in `caps.yml`
`reevaluate`: a Claude Code or OpenRig minor version change, a start-up trim change (roles, rig
keys, CULTURE/protocol/role files), a seat's model change, each B-metric verdict and each HACF
release. Two read-only commands do it, from this repository (dev tooling, not installed):

```bash
python3 dev/telemetry/collect_metrics.py --rig hacf --since <ISO> --until <ISO> --out-dir "$TMPDIR/ctx"
python3 dev/telemetry/recompute_caps.py --scorecard "$TMPDIR/ctx/<scorecard>.json" \
  --caps openrig/hacf/caps.yml --rig-spec openrig/hacf/rig.yaml
```

Every scorecard carries `context`: per seat, per occupant, the turns and the peak resident context
of its main transcript (`--context-series` also writes each turn). `recompute_caps.py` prints, per
role, n and the low, median, mean, p90 and high of the occupants' peaks, the current cap and a
proposal (p90 rounded up to 10000, at least 100000; none under 5 occupants). It never writes
`caps.yml`. The result goes to TJ as a decision row, and a cap changes only by hand after TJ's OK.

### When it restarts

It restarts a seat only at a durable point, and fails closed: any doubt means no restart.

- The seat's profile has a cap, its `behaviour` is not `planned_handover`, and it is running,
  `idle`, not stale, with a known context count over that cap (`totalInputTokens > cap_tokens`).
- **The stale-sample rule.** Idle seats stop refreshing their usage sample. A sample that is not
  `fresh` still counts only when the seat's transcript provably has not grown since it:
  `contextUsage.transcriptPath` is named `<sessionId>.jsonl`, and its mtime is no later than
  `sampledAt` plus 2 seconds. A later mtime skips with `transcript grew since sample`; a missing
  field, a bad timestamp or a failed stat skips with `transcript unknown`. A restart on such a
  sample says `stale sample, transcript unchanged since <sampledAt>` in its reason.
- It holds no pending, in-progress or blocked queue row (its counts in `rig ps` and
  `rig queue list` both agree).
- **The roots.** For every `--project-root`, and every worktree git lists for it:
  - git-visible dirt (`git status`, untracked files included) blocks **every** seat, because the
    rig's seats work in all of them; so does a root git cannot read;
  - an untracked or ignored file under `spec/active/` blocks only the spec seats (`logicalId`
    `spec.*`): it is Phase-1 work, and a build seat between slices is still at a durable point.
    The top-level `spec/active/FAILED_WORKFLOWS.md` is permanent and blocks nobody; a file of that
    name anywhere deeper counts like any other.
- It never restarts `orch-lead` or a `planned_handover` seat (their handover is planned), a busy,
  stale or `unknown` seat, or more than one seat per run. A failed or unreadable `rig` answer
  means no restart. Right before the launch it reads the seat and the roots again, with the same
  per-seat rules, and asks `rig seat launch … --fresh --stop --plan`, a preview of the real launch;
  anything changed, or a plan without `launchable: true`, means no restart.

Every decision is one line, on stdout and in the log (`--log`, default
`~/.hacf/<rig>/context-guard.log`, its directory `0700`):
`<UTC ts> rig=<r> mode=<dry-run|apply> seat=<session|-> tokens=<n|-> max=<cap|-> action=<restart|would-restart|skip|failed> reason=<text>`,
where `max` is the seat's role cap.
A lock beside the log keeps two runs from overlapping. Exit 0 when it ran, restart or not; 1 when
the launch or the note to the lead failed; 2 on a usage error or a caps file it refuses.

### Checkpoint watchdogs: `--arm-checkpoints`

The `checkpoint_message` (execute) and `planned_handover` (lead) roles are never restarted
mid-work; they get a message instead. With `--arm-checkpoints`, each tick first reads
`rig watchdog list --json` and makes sure every **running** seat of those roles has exactly one
active `context-usage-threshold` job targeting it, at its role's `cap_tokens`:

- none → with `--apply`, `rig watchdog register --policy context-usage-threshold
  --target-session <seat> --threshold-tokens <cap> --interval-seconds 300 --registered-by <orch-lead>
  --message <text> --json` (`action=arm`; a failed register is `action=failed` and exit 1);
  without `--apply`, `action=would-arm`;
- one → `checkpoint armed <jobId>`; more than one → `duplicate checkpoint jobs <ids>`, and nothing
  is added: the operator stops the extras;
- an unreadable list arms nothing this tick; the restart logic runs either way.

The job fires **once per occupant**, so one long-lived job re-arms itself for every fresh launch.
The builder's message tells it to write a checkpoint note, never to commit, and to ask the lead
for a fresh launch; the lead's tells it to plan its handover (`guidance/execute.md`,
`guidance/lead.md`). The guard never calls `rig watchdog stop` or `update`. **To change a cap**,
edit `caps.yml`, then find the old job with `rig watchdog list --json` and stop it with
`rig watchdog stop <jobId>`; the next tick registers the new one.

### The host line

The script changes nothing on the host by itself. The operator runs it from the host's existing
digest timer — no new unit — for rig `hacf` only, with one `--project-root` per git checkout the
rig works in (the guard finds their worktrees itself). From v0.6.4 the line needs `--caps` and
adds `--arm-checkpoints`; until the operator applies this line (OPS-A), the v0.6.3 line exits 2 at
usage every tick:

```bash
# HACF context guard: rig hacf only, one tick per digest run (OPS-A, v0.6.4)
"<hacf checkout>/openrig/hacf/bin/context-guard.sh" --rig hacf --project-root "<project root>" --caps "<hacf checkout>/openrig/hacf/caps.yml" --arm-checkpoints --apply || true
```

## Permissions

Every seat launches with Claude Code's permission prompts off
(`permission_policy: builtin:yolo`). That is deliberate: the pipeline runs across seven
background seats and a prompt in a seat nobody is watching blocks the whole feature. The
consequence is real — a seat can write anywhere the OS user can, run any command, and push if
it decides to. The seats are instructed never to push, publish or rebase, and never to write the
ledger except through `hyper_ledger.py` and `hypergraph_updater.py`. Those are instructions, not
sandbox walls.

To go back to prompting, change `permission_policy` in `rig.yaml` and relaunch from the spec
(`rig up <spec> --name <name>`, after `--plan`); `rig up <name> --existing` keeps the stored
posture.

**Per-role tool denies.** `roles.yml` (roles v2 of v0.6.4, schema `hacf.roles/v1`) names, for each
of the seven roles, the native Claude Code tools it uses, the tools it denies (every role denies
`AskUserQuestion`, `NotebookEdit`, the plan-mode and worktree tools; roles v2 adds every built-in
tool and MCP server the start-up probe listed that the role file and its stage skill never name,
never `codebase-memory-mcp`), whether it needs MCP servers, and its skills.
`bin/seat-rules.py --role <role> --json` turns one role into the JSON that
`rig seat set-permissions <seat> --rules <file>` takes, and `--check` keeps the table, `agent.yaml`
and `rig.yaml` consistent. `rig.yaml` itself carries one rig-level `env:` key (v0.6.4); per-seat
tool rules are still applied per seat, so it launches on every OpenRig this rig is tested against.
The key, `ENABLE_CLAUDEAI_MCP_SERVERS: "false"`, turns off the account's claude.ai MCP connectors for
every seat; project MCP servers such as `codebase-memory-mcp` stay. Every OpenRig version the compat
check runs proves it with `rig spec validate`. The rules are native tool rules, not a
sandbox: the denies hold under `builtin:yolo`, where allows would not constrain anything, so the
generator emits none. `OPS.md` is the operator's recipe for applying them per seat, the turn-1
probe and the rollback; nothing applies them on its own.

## Keeping rig files out of commits

OpenRig writes `CLAUDE.local.md` into each seat's **working directory**, and may write settings
beside it (`.claude/settings.local.json`). In parent-folder mode that is one level above any
project, so no project sees it. In project mode it is the project root: keep those files out of
commits with `.git/info/exclude`, never by editing HACF's managed `.gitignore` block. The scribe
still checks the project for stray rig files before it commits.

## Files

```
rig.yaml                        the topology: 4 pods, 7 seats, the pipeline's edges
CULTURE.md                      the team's values, including the commit rules
HARDENING.md                    the audit register: what was wrong, the rule that fixed it,
                                which rules a later release superseded, and the findings that
                                were rejected with evidence
startup/context.md              start steps shared by every seat, and the mode rule
startup/protocol.md             seat addresses, the handoff recipe, how to ask the person, the
                                two record files, the defaults and the per-run-grant pattern
startup/lead-first-move.md      what the lead does before it dispatches anything
roles.yml                       the role table (roles v2): each role's tools, denies, MCP need and skills
caps.yml                        the per-role context caps bin/context-guard.sh reads (--caps)
OPS.md                          applying roles.yml to a running rig per seat: probe, rollback
bin/brief.sh                    the execute/audit brief, from the running rig's own run record
bin/context-guard.sh            restarts one idle seat over the context cap at a durable point
bin/view-layout.sh              arranges the team's Herdr view: the lead on top, build row, spec row
bin/seat-rules.py               one role of roles.yml as `rig seat set-permissions --rules` JSON
agents/hacf/agent.yaml          seven profiles, one per stage
agents/hacf/guidance/*.md       one role file per seat
agents/hacf/runtime/            the Claude Code activity-hooks selection marker
agents/hacf/bundle-skills/      skills that travel with the rig:
  adversarial-agent-hardening/    vendored from the eta-skills marketplace at 6d9c933
```

## Hardening the agents

Every seat carries `adversarial-agent-hardening`: the generate–critique–patch loop with
independent audits, fixed pass/time/run limits, and a failure ledger. It is how these seven role
files were checked. To harden an agent, copy its definition, run the skill in **patch mode**
against the copy, and keep the ledger — a finding leaves it only as Fixed or as a rejection with
its evidence recorded. Do not let a seat audit its own definition: the whole value is that the
auditor does not share the author's assumptions.

**`HARDENING.md` is this rig's ledger** — three passes, 68 + 26 + 25 findings, every finding
that changed a rule or was rejected with its evidence, and the rows a later framework release
superseded, kept and marked rather than deleted. Read it before editing a role file. It is
explicit about being partial: passes 1 and 2 were patched in place and their raw reports were
never curated, so the register holds the findings that produced a rule, not all of them.

## Example host notes (verified on one machine, 2026-11-16)

Facts about the one machine this rig was first run on (the first host, an Ubuntu desktop). They are
examples of what a host check finds, not rules; re-check them on yours.

- The seats were tmux sessions on that desktop, so a reboot dropped them; `rig up hacf --existing`
  brought the same team back.
- A seat held roughly 400 MB, so a seven-seat rig was ~2.8 GB. With ~20 GB free, the person
  capped per-project duplicates at three in flight, stood up on demand.
- `python` did not exist there, only `python3`; the v0.5.x installer needed
  `PIP_BREAK_SYSTEM_PACKAGES=1` to get past its pip step and `git init` first (`HARDENING.md`
  rows 24-26, now superseded).
