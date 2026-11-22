# hacf

A seven-seat OpenRig team that runs the [Hypergraph Agentic Coding
Framework](https://github.com/tjmustard/Hypergraph-Coding-Agent-Framework): one seat for
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
`https://github.com/tjmustard/Hypergraph-Coding-Agent-Framework.git`, run from the project's git
top level (it refuses anywhere else, with `Nothing was written.`):

```sh
HACF="$(mktemp -d)/hacf"
git clone --quiet https://github.com/tjmustard/Hypergraph-Coding-Agent-Framework.git "$HACF"
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
bin/brief.sh                    the execute/audit brief, from the running rig's own run record
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

Facts about the one machine this rig was first run on (`tj-intel`, an Ubuntu desktop). They are
examples of what a host check finds, not rules; re-check them on yours.

- The seats were tmux sessions on that desktop, so a reboot dropped them; `rig up hacf --existing`
  brought the same team back.
- A seat held roughly 400 MB, so a seven-seat rig was ~2.8 GB. With ~20 GB free, the person
  capped per-project duplicates at three in flight, stood up on demand.
- `python` did not exist there, only `python3`; the v0.5.x installer needed
  `PIP_BREAK_SYSTEM_PACKAGES=1` to get past its pip step and `git init` first (`HARDENING.md`
  rows 24-26, now superseded).
