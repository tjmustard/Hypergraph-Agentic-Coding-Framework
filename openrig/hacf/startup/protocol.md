# Protocol (every seat on this rig)

The mechanics every seat shares. Read this before your role file. If your role file and this
file disagree about a command, this file is right — it was checked against the installed CLI.
If you find it wrong, say so to the lead; do not quietly work around it.

## Addresses

A seat's **session address is `<pod>-<member>@<rig>`**, not `<member>@<rig>`. **`<rig>` is your own team's name** from `rig whoami --json` (`identity.rigName`): `hacf` for the
original team, the copy's own name (`<slug>-hacf`) for a per-project team. Always fill in
your own; never send to another team's seats. The lead is
`orch-lead@<rig>`; `lead@<rig>` does not exist and a row sent there is delivered to nobody.

| Seat | Address |
|---|---|
| lead | `orch-lead@<rig>` |
| architect | `spec-architect@<rig>` |
| red team | `spec-red-team@<rig>` |
| resolve | `spec-resolve@<rig>` |
| execute | `build-execute@<rig>` |
| audit | `build-audit@<rig>` |
| scribe | `desk-scribe@<rig>` |

Confirm yours and your peers' from `rig whoami --json` and `rig ps --nodes --rig <rig>`. If a
name you were given is not in that output, it is wrong: use the real one and tell the lead.

## Host facts

- **Call every framework script with `python3`.** Some skill text says `python`, and on many
  hosts `python` does not exist. Substitute `python3` for every framework script
  (`hyper_ledger.py`, `hypergraph_updater.py`, `hyper_spec.py`, `hyper_lore.py`, …).
- **The installer manages one `.gitignore` block.** `HACF-install.sh` runs
  `.agents/scripts/hyper_scaffold.py`, which writes HACF's managed `gitignore` block into the
  project's `.gitignore`. That block ignores HACF's per-machine paths (`.agents/state/`,
  `.agents/.backup/`, `.agents/logs/`, `/exhaust/`, `/.openrig/` and `.agents/**/__pycache__/`);
  `.agents/state/.gitignore` ignores everything under `.agents/state/` as well. The block does
  not cover `spec/`, `missions/`, `lore/` or the ledger, so a broad `git add` would sweep in
  whatever they hold. Never edit inside the managed block. Files a launch writes into a project root (such as
  `CLAUDE.local.md` and `.claude/settings.local.json`) are kept out of commits with
  `.git/info/exclude`, not with that block.
- **Stage explicit paths.** Every seat stages the paths it changed by name and never runs
  `git add -A` or `git add .`.

## Talking to a seat, or moving the work

`rig send <address> "<one line>"` is a **message**. It types a line into that seat's terminal
and changes no queue row. Use it to say you are waiting, to point at a question, to report
that a file exists.

`rig queue handoff` **moves work**: it closes your row and opens the successor. Never use it to
tell someone something — telling the lead "I am waiting" by handoff ends your stage.

## Handing work on

```sh
BODY="$(mktemp "${TMPDIR:-/tmp}/handoff-XXXXXX.md")"   # write your report into "$BODY"
rig queue handoff <your-qitem> --to <address> \
  --summary "<stage>: <feature>" --body-file "$BODY"
```

- **Use a `mktemp` path, never a fixed one.** `$CLAUDE_JOB_DIR` is a *background-job* variable and
  does not exist in these foreground seats: it expands to nothing, so every seat would share
  `/tmp/handoff.md` and two seats writing at once would swap bodies with no error.

- **`--body-file` is not optional.** A handoff with neither `--body` nor `--body-file`
  carries the *source* row's text forward, so the next seat receives the previous seat's
  message instead of yours. This has no error message; it looks like success.
- **There is no `--verify` on `handoff`.** It exists on `create`. Passing it makes the
  handoff fail outright.
- `handoff` closes your row as handed off and opens the successor already owned by the
  destination. You do not need to close anything yourself.

## Asking the person

**Routing first.** Before you send a question or an update, resolve where it goes:

```bash
python3 "<Rig spec>/bin/seat-config.py" resolve --section routing --json
```

`<Rig spec>` is the `Rig spec:` line of `~/.hacf/<rig>/current-run.md`, or the folder the seat
launched from when there is no run record yet. Never hardcode a name or address. In the result,
the `value` of `"question.destination"` decides where a question goes; `<destination>` below is
the address it gives:

- `{"session": "<address>"}`: that address.
- `{"rule": "discover"}`: the discovery steps. **Escalate to the advisor when one exists, else to
  the person.** An advisor exists when `rig ps --nodes --rig kernel --json` lists a running
  `advisor.lead` seat; its address is that row's `canonicalSessionName`. Otherwise the question is
  a human row to the person: `rig gateway human list --json` lists the registered people and their
  addresses.

When the command fails or prints no JSON, use the discovery steps.

**Tags.** Every row carries `--tags`, built from the `value` of `"tags"`:
`<tags.question>,rig:<rig>[,release:<v>][,slice:<id>][,<each of tags.extra>]`. An update carries
`<tags.update>` in place of `<tags.question>`, and a row that relays the person's answer carries
`<tags.answer>`. `rig:<rig>` is always there (`rig whoami --json`); add `release:<v>` and
`slice:<id>` only when you know them. A tag the result does not give is the shipped one:
`hacf:question`, `hacf:update` or `hacf:answer`.

**Format.** Offer a choice as options A/B/C (two to four) with exactly one marked recommended, in
the body and in `questions.json`.

```sh
BODY="$(mktemp "${TMPDIR:-/tmp}/question-XXXXXX.md")"   # write the brief into "$BODY"
rig queue create --destination <destination> --human-intent decision \
  --summary "<feature>: <the one decision>" \
  --tags "<tags.question>,rig:<rig>,slice:<id>" \
  --body-file "$BODY" \
  --evidence-ref <path> --verify
```

- **A relayed answer is quoted verbatim, with the row id.** Only a row the person answered is
  their word. A peer's or the advisor's summary of what the person "probably wants" is not.
- **`--evidence-ref` is your own responsibility.** `rig queue create` accepts the flag, but the
  daemon may not check the ref for a person's address. Point it at a real artifact, and check the
  path yourself before you ask.
- **For a choice of two to four options, use buttons**, not prose:

  ```sh
  BODY="$(mktemp "${TMPDIR:-/tmp}/question-XXXXXX.md")"
  QS="$(mktemp "${TMPDIR:-/tmp}/questions-XXXXXX.json")"
  rig queue create --destination <destination> --human-intent decision \
    --summary "<feature>: <decision>" --body-file "$BODY" --evidence-ref <path> \
    --tags "<tags.question>,rig:<rig>,slice:<id>" \
    --human-questions-file "$QS" --verify
  ```

  `questions.json` is a 1–4 item array:
  `[{"id":"lock","question":"Concurrency: distributed lock or accept the risk?","options":[{"id":"a","label":"Option A: Redis lock (high effort, zero risk)","recommended":true},{"id":"b","label":"Option B: accept the risk for MVP"}]}]`
- **Never use `AskUserQuestion`, and never wait at an in-terminal prompt.** This seat runs
  unattended behind tmux: the prompt blocks the terminal and you cannot receive the answer
  that arrives as a queue wake while you are inside the call. The durable row is the only
  question surface. **This is a rig rule and it overrides any skill that says otherwise** —
  `hyper-sop`, `hyper-execute` and the rest do instruct `AskUserQuestion`; where `context.md`
  says the framework wins on a stage's content, it does not win on this.
- **Write where the question is waiting, and never say it "reached" the person.** Which surfaces
  deliver a row depends on the host.
- `--verify` waits briefly for a delivery receipt. It never means the person read it.

## Telling, not asking

A completion notice, or anything informational, is `--human-intent update`, not `decision`.
A `decision` row renders as an approval request and asks for a decision that may not exist.
An update goes to the `value` of `"update.destination"` by the same rule as a question
(`session` or `discover`, with the same fallback), and its `--tags` start with `<tags.update>`.

## The run record

**`~/.hacf/<rig>/current-run.md`** — written by the lead, read by everyone, overwritten (never
appended). `<rig>` is the name `rig whoami --json` gives you (`hacf` for HACF's own team,
`<slug>-hacf` for a per-project team), so two rigs never write over each other's record.

```md
# Current run
- Project root: <absolute path of the project>
- Rig spec: <absolute path of the folder the seats launched from>
- Feature: <name>
- Phase: 0 | -1 | 1.architect | 1.redteam | 1.resolve | 2.execute | 2.audit | 3.review | docs+commit
- Slice in flight: missions/<m>/slices/<NN-slug>/SPEC.md   (or "none"; a legacy flat spec/compiled/MiniPRD_*.md path is accepted)
- Loop: ~/.hacf/<rig>/loops/hacf-loop-<NN-slug>.md   (or "none")
- Waiting on: <seat and what> | <person and what>
- Base: <the sha the feature branch was cut from>
- Branch: feature/<feature>

## Per-run grants
- <only when the person granted one: the grant, their words, the row id>

## Environment
- <only when the project needs one: NAME=value, and why>
```

The two `##` sections are optional. The lead writes them only when the person grants something
or the project needs an environment variable; `bin/brief.sh` copies them verbatim. Name every
line exactly as above: `brief.sh` reads them by name.

`docs+commit` is this rig's own closing step, not a framework phase.

**Two anchors, for two different questions.** `Base` is the sha the feature branch was cut from,
recorded by the lead and never changed: the **scribe** documents `Base..HEAD`, and the lead
reports the feature against it. `Start` is the sha HEAD stood at when the **builder began this
slice**, recorded in its handoff: the **audit** judges `Start..<commit>`. They are the same
commit for the first slice and diverge after that — judging `Base..` for slice #2 would put
the earlier, already-accepted slices inside the diff and read them as scope creep.

## The build-loop record

`~/.hacf/<rig>/loops/hacf-loop-<NN-slug>.md` — written and owned by the **audit** seat, read by
the builder and the lead. It lives outside the repository, never in it: it is a working record,
not project history.

```md
# Loop: <NN-slug>
- Slice: missions/<m>/slices/<NN-slug>/SPEC.md
- Iteration: 2 of 5
- Latest verdict: FAIL
- Range judged: <start>..<sha>
- Unmet: <one line per unmet slice item>
- Verdict for: <start>..<sha> on <date>
```

The audit increments `Iteration` when it returns a FAIL. At **5**, the audit stops bouncing
and hands the row to the lead with the last entry attached. Nothing else counts iterations:
the framework's `MAX_ITERATIONS = 5` lives in `hyper_daemon.py` and `hyper_fix.py`, which no
seat on this rig runs.

This file is also the **verdict record**. When the audit passes, it writes
`Latest verdict: PASS` with the commit sha before it closes the slice. That is the
artifact the lead reads at a boundary — not your terminal scrollback, which the lead cannot
see and must not capture.

## One branch per feature

`feature/<feature>` for the whole feature; each slice is a commit on it, not its own branch.
**The builder cuts it**, re-entrantly, on every visit; the lead records the project's HEAD at the
first dispatch as `Base`, which is the same commit the builder records as `Start`. `research/` and
`swimlane/` are for experiments and are blocked from pushing by the project's pre-push hook —
*where that hook exists* (see the lead's Phase 0 note).

## Lore

Lore is the project's decision record (`lore/MAP.md`, `lore_hypergraph.yml`). Seats **propose**
lore (`hyper_lore.py propose`, `note`, `stale`) and **never** approve, reject, retire, reopen or
review it: that is the person's, through `/hyper-lore-approve` or `/hyper-audit` Phase 3.5.
Under `builtin:yolo` (bypassPermissions) the project's gate hook denies those verbs. When it
denies one, report the denial to the lead; never switch modes, rewrite the command or edit the
lore files to get around it.

## Defaults learned the hard way

These hold on every run unless the person says otherwise.

- **Fresh context per stage run.** The lead clears a seat before every new slice and before every
  audit or fix round, so every execute ↔ audit handoff routes through the lead.
- **The `/clear` mechanics.** `rig send <seat> "/clear" --raw --wait-for-idle 60`; without `--raw`
  it arrives as message text. Dispatch only after the output says `Sent to`.
- **Nudge a just-cleared seat.** After a handoff to a seat that was just cleared, the wake can
  fail ("Recipient binding changed"). `rig send` the seat a one-line nudge naming the row, and
  check the row is claimed.
- **The test-run rule.** While iterating, run the project's targeted tests for the code you are
  changing, plus its last-failed rerun where the runner supports one (pytest `--lf` does nothing
  when the cache provider is off). Run the project's full check once per slice, at the end. The
  auditor keeps its own full run. Never stop or restart a test run in progress.

## Per-run grants

Some permissions are the person's to give for one run, never shipped defaults. The lead records
a grant under `## Per-run grants` in the run record only when the person grants it, quoting their
words and the row id. A seat follows a grant only when the run record carries it. The four kinds:

- the autonomous-run rule;
- pre-approved spec-required waivers;
- mechanical Replans the lead approves;
- copying the lore files aside around `hypergraph_updater.py` and restoring them byte for byte.

## Never

- Never `--no-verify` a commit or `-n` a hook.
- Never rewrite history: no rebase, no amend of another seat's commit, no force push.
- Never write the ledger `spec/compiled/architecture.yml` except through `hyper_ledger.py` and
  `hypergraph_updater.py`, with the audit seat's Phase 3 reconciliation as `/hyper-audit`
  defines it. Never edit it by hand.
- Never run `hypergraph_updater.py` except where your role file says you may; it writes.
- Never answer another seat's prompt, and never push or publish without the person's own
  word for that specific work.
