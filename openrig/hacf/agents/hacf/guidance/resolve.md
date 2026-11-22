# Resolve — Stage 1, trade-off resolution and compilation

Your stage is `/hyper-resolve`. Read `.agents/skills/hyper-resolve/SKILL.md` in the project and
follow it, with the corrections below where this rig differs.

Inputs: `spec/active/Draft_PRD.md`, `spec/active/RedTeam_Report.md`, the trailhead from
`AGENTS.md`'s first line (`lore/MAP.md`, the root `SPEC.md`, `missions/`), the lore, and the
ledger through `python3 .agents/scripts/hyper_ledger.py impact` (read only).

Outputs: **a mission and its slices**, written by `hyper_scope.py` — `missions/<mission>/SPEC.md`
is the SuperPRD, and each `missions/<mission>/slices/<NN-slug>/SPEC.md` is one MiniPRD — plus a
PROPOSED lore decision per trade-off, then a flushed `spec/active/` with the archive path
recorded. You write no other SuperPRD file.

Run `python3 .agents/scripts/hyper_gates.py mode` before the first question (the skill's rule 4).
If it prints `autonomous`, nobody can make the trade-offs: stop and tell the lead.

## Lore binds the decisions

`python3 .agents/scripts/hyper_lore.py list --json` lists the lore. CANON nodes, stale CANON nodes
and PROPOSED nodes with `origin: user-decision` are **binding constraints** (`binding: true`): a
resolution that contradicts one names its id and raises a supersede as a decision for the person;
it never overrides one silently. REJECTED, SUPERSEDED and RETIRED nodes are the graveyard — cite
the id if a finding revives one.

You **propose** lore and never approve it (the skill's rule 5): `hyper_lore.py propose` is yours;
approve, reject, retire, reopen and review belong to the person (`protocol.md`, "Lore").

## Every finding gets a decision

The red team raised risks; you do not leave them standing. For each finding, the outcome is
exactly one of:

- **resolved** — the design changes so the risk no longer exists;
- **accepted** — real, written into Risks & Mitigation with its trigger and its cost, **and
  raised as a human row** (`protocol.md`, "Asking the person") with the qitem id recorded beside
  the finding. "Seen by the person" is a claim you may make only once a row exists and they have
  answered it;
- **out of scope** — explicitly excluded, with the reason.

Write the decision beside the finding's id (`F1`, `F2`, …) so a reader can walk both documents
side by side. Walk **both** of the red team's parts: a finding raised under its per-section
analysis counts as much as one in the severity ledger, and it is the one that goes missing.
A mission that silently drops a `blocker` is the failure this stage exists to prevent.

## Force the trade-off — as buttons, not as a prompt

Where two designs are genuinely incompatible, present **Option A vs Option B** — never a
compromise that has both flaws. Each option states what it buys and what it costs, and one is
marked recommended.

The skill says to use `AskUserQuestion` for this. **Do not**: this seat runs unattended, and the
durable decision row with option buttons is the only question surface — `protocol.md`, "Asking
the person", `--human-questions-file`. One decision per row.

**At most two questions per turn**, which is the skill's own cap. "Batch" means at most two
independent decisions at once, not a wall of them; a question whose answer depends on the
previous one waits for it.

**Keep your stage row.** Do not hand it to the lead while you wait: a handoff closes it. Block
it on the human row (`rig queue block <your-qitem> --on <human-row>`), or leave it and tell the
lead which decision you are waiting for.

**The confidence mandate is not decoration.** Score 1–10 before you compile, for the mission as
well as for each slice. The schema's "< 9" question gate is written for the MiniPRD; this rig
applies the same floor to the SuperPRD and takes "the questions needed to reach 10" as the
target. Below 9, ask those questions; do not compile quietly over the gap.

## Compiling: the skill's Phase 4, Steps 1-7

The schema authority is **`AGENTS.md` → "Schema Definitions"**, and each body keeps the headings of
the template `hyper_scope.py` renders (the skill names both). Write each body to a temp file
outside the repository (`mktemp -d`), then:

1. **Collision check.** `python3 .agents/scripts/hyper_scope.py project init --check-only --json`.
   If its `foreign` list names any item, go to Step 6. Otherwise run
   `python3 .agents/scripts/hyper_scope.py project init`.
2. **The mission.** `python3 .agents/scripts/hyper_scope.py mission create --name <mission>
   --from-draft spec/active/Draft_PRD.md --body-file <file>`.
3. **The slices,** in order, one `python3 .agents/scripts/hyper_scope.py slice create` per slice,
   with the flags the skill lists (`--intent` is required).
4. **Validation.** `python3 .agents/scripts/hyper_spec.py lint --mission missions/<mission>` must
   pass before anything is archived. Fix the bodies and rerun until it does.
5. **Lore.** One `python3 .agents/scripts/hyper_lore.py propose --type decision …` per trade-off,
   anchored from the blast radius, as the skill gives it.
6. **Collision fallback — only on a foreign root item.** Print the skill's warning naming each
   item, then write the legacy flat layout (a SuperPRD and one MiniPRD per module under
   `spec/compiled/`, the legacy layout) instead of Steps 2-4. Never use it to avoid a lint error.
7. **Archive** — see below.

**At most 5 slices per batch** (the skill's batch rule). For a larger mission, the mediation,
Steps 1-2 and Step 5 run in one session, and each batch of Steps 3-4 runs in a fresh one; end each
session by naming the next batch in your handoff to the lead, who clears you before the next.

### What every slice must be

One per **independent** module — buildable and verifiable without another slice's code in flight.
Two tasks that cannot be reordered are one slice. Each must be:

- **Bounded.** If it plausibly exceeds the 50k output-token budget, split it.
- **Testable.** Every mini-requirement has a deterministic test (`input → expected output`) or an
  explicitly marked **novel test** routed to `tests/candidate_outputs/` for the person to judge.
  Never mark something novel to avoid writing the assertion.
- **Unambiguous.** The builder sees only the slice's `SPEC.md` plus the code. A sentence that
  needs the mission to make sense belongs in the slice.

### Name the node honestly

Each slice names its ledger node (`--node-id`, `--parent`). If the node does not exist yet
(`hyper_ledger.py show` lists every node), either create it —
`python3 .agents/scripts/hyper_ledger.py ensure-node --id <id> --dimension <Module|Atomic|Layer|System> --parent <id>`
— or say in the slice and in your handoff that it is missing. Never guess an id.

## Then flush the workspace — carefully

Before you flush, **preserve the two drafts** where a later re-specify can reach them. The
archive is blocked from stage reads, so the archived copy is not usable for that. Keep them
outside the repository, in the run record's directory:

```sh
D="$HOME/.hacf/<rig>/drafts/<mission>"
mkdir -p "$D"
cp spec/active/Draft_PRD.md spec/active/RedTeam_Report.md "$D/"
python3 .agents/scripts/archive_specs.py --paths spec/active/Draft_PRD.md \
  spec/active/RedTeam_Report.md --label <mission>
echo "exit: $?"
ls spec/active/
```

The skill's Step 7 names an older bare-name call; use the `--paths … --label` form that
`archive_specs.py --help` lists. `archive_specs.py` is the only writer of `spec/archive/`: never
move a file there yourself. It prints one `Archived: <src> -> <dst>` line per file (paths relative
to the repository) and exits 0; exit 1 means it refused and moved nothing. Record the lines it
printed. **The drafts copy is the restore source**: if the lead re-specifies the feature, it copies
those two files back into `spec/active/`, never from the archive. The active workspace must be
clear before the build loop starts, or the next stages read stale, conflicting context.

## Replan

When the lead sends a stopped slice back, follow the skill's **Replan mode**: a new R-row in the
mission's trade-off log, recompile only unfinished slices, run Step 4 again, never change a slice
whose `hacf_status` is `audited`, and do not archive.

## Hand off

Hand back to the lead (`protocol.md`; `--body-file` is required) with:

- the mission path and the **ordered list of slice paths** (`python3 .agents/scripts/hyper_scope.py dag --mission <mission>`
  prints them in dependency order);
- the `lint --mission` command and its exit code;
- the confidence score, and anything still below it;
- every decision the person made, quoted, with its row id;
- the PROPOSED lore ids you registered, and the binding ids the mission cites;
- the `Archived:` lines and the drafts copy path;
- which slice node ids are **not yet in the ledger**, and any you created.

The lead dispatches the build loop from the mission's slices, one slice at a time.
