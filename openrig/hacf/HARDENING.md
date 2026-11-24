# Hardening HACF

The register of what was wrong with these seven seat definitions, what the failure would have
looked like in normal use, and the rule that now stands in the files. Read it before changing a
role file, and copy the method if you harden a duplicate rig.

## What this file is, and what it is not

It is **not** a per-finding ledger of all 119 audit findings. Passes 1 and 2 were patched in
place; their raw reports were never curated into a list and survive only as the session that
produced them. What is below is every finding that **changed the deployed text**, plus every
finding that was **rejected**, with the evidence for the rejection. Wording and convention
findings that produced no rule change are not listed.

Treat a row here as: *this class of mistake was made once, and is now guarded.* If you touch a
row's rule, re-check the row rather than the prose.

The register is historical: a row is never deleted. When a later framework release makes a
row's rule obsolete, the row stays and its rule cell starts with `Superseded (v0.6.0): <reason>`,
so the next person can see both what went wrong and why the old guard no longer applies. Facts
that were true of one machine, rather than of the framework, are under "Example host notes" at
the end.

## The method

Generate, critique, patch — three independent passes over the same seven definitions:

| Pass | What the auditors saw | Findings |
|---|---|---|
| 1 | the first draft | 68 |
| 2 | the draft after pass 1's patch | 26 |
| 3 | the **exact text that was then deployed** | 25 |

Each pass used three auditors that did not share the author's assumptions and could not see
each other's work. Pass 3's value was that it read the deployed text, not a description of it —
it is the pass that caught the `.gitignore` gap and the single-anchor collision, both of which
had survived two rounds.

The skill is vendored at `agents/hacf/bundle-skills/adversarial-agent-hardening` and selected in
all seven profiles. A seat must never audit its own definition: the whole value is that the
auditor does not share the author's assumptions.

## The catch register

Each row is a mistake that would have surfaced as a normal-looking failure, not as a crash.

### Ranges and history

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 1 | The audit judged **one commit**. A fix necessarily adds a commit (history is never rewritten), so every retry read already-accepted work as missing → false FAIL, burning iterations to the 5-cap. | The audit judges `Start..<sha>`: `Start` is the sha HEAD stood at when the builder began *this* MiniPRD. | `guidance/audit.md` "Judge this MiniPRD, not one commit" |
| 2 | The scribe's range started at the audit's verdict sha — which **is HEAD** on a clean pass. Empty diff; nothing would ever be documented. | Range from **`Base`**, the branch point. Never the verdict sha. Fall back to `git merge-base <default branch> HEAD` and say which was used. | `guidance/scribe.md` §range |
| 3 | `Base` and `Start` were one sha. For MiniPRD #2 the audit's range then included already-accepted code → false scope-creep FAIL. | Two anchors, two questions: `Base` = what the feature changed (scribe); `Start` = what this build attempt changed (audit). | `startup/protocol.md` "Two anchors" |
| 4 | "One commit per MiniPRD" was written as a rule. The fix loop contradicts it by construction. | One commit per **build attempt**; fixes add commits. | `guidance/execute.md` |
| 5 | The builder was told to create the feature branch. If the lead also cut it, the two disagreed about `Base`. | The builder cuts the branch (re-entrantly) and records `Start`; the lead records `Base`. | `guidance/execute.md`, `guidance/lead.md` |

### Git state nobody checked

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 6 | The audit ran `git show <sha>:spec/compiled/architecture.yml` on a file the installer leaves **untracked**. Empty output read as "the builder skipped the blast radius" — the opposite of the truth. | Read the graph from disk when untracked; `git show` only when tracked. | `guidance/audit.md` |
| 7 | The framework **repo** ignores `spec/active/*`, `spec/archive/*`, `spec/compiled/*` — but the installer **never copies that `.gitignore`**. In a project those paths are untracked *and not ignored*, so one `git add -A` commits the graph, the compiled specs and the drafts. | Superseded (v0.6.0): the installer now writes HACF's managed `.gitignore` block, which ignores per-machine paths but not `spec/` or the ledger; what stands is "stage explicit paths, never `git add -A`" plus the builder's and audit's tracked-graph check. Was: lead adds the three ignore rules after install; builder checks `git ls-files --error-unmatch` and never uses `git add -A`; audit commits the graph itself **if** it is tracked. | `startup/protocol.md` "Host facts", `guidance/execute.md`, `guidance/audit.md` |
| 8 | `.gitignore` and the first commit were assumed to exist. | On an unborn HEAD the **lead** makes the first commit, then reports `git rev-parse HEAD` as `Base`. The `git init`-first half is superseded (v0.6.0): the installer refuses outside a git root with `Nothing was written.` | `guidance/lead.md` Phase 0 |

### The installer and the host

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 9 | `--ides=claude` was assumed to bind. The installer's `if $AUTO_YES \|\| ! is_tty` branch is evaluated **first** and every seat's stdin is not a TTY, so it installs *every* IDE and **skips its own `.gitignore` prompt**. | Superseded (v0.6.0): `--ides` now binds without a TTY; only an install with no `--ides` and no earlier selection defaults to every IDE, and it says so. Was: either the person runs the installer, or the lead accepts the artifacts and ignores the IDE paths. | `guidance/lead.md` Phase 0 |
| 10 | The installer executes `main` **unpinned**, with no prompt under `builtin:yolo`. | Say that it was unpinned, or pin it: `--source=<checkout> --ref=<commit>` installs from a commit of a local checkout. `head -50` is a glance, not a review. | `guidance/lead.md` Phase 0, `README.md` |
| 11 | The installer never copies its own `docs/`. | No seat may be sent to read `docs/MasterSOP.md` in a project — it exists only in the framework repo. | `README.md` installer traps |
| 12 | Every framework script was invoked as `python`. On the host where this was found **`python` did not exist** (see "Example host notes"); only `python3`. Every script would have exited 127. | `python3` throughout, even though some framework text says `python`. | `startup/protocol.md` "Host facts", all seven role files |
| 13 | `.agents/` contents were assumed. The installer copies `{scripts,schemas,config,rules,…}` — **not** `.agents/memory/`, and not the `.agent` symlink. | Role files say "if it exists" for `.agents/memory/`. The template half is superseded (v0.6.0): no MiniPRD template ships, slices are created by `hyper_scope.py slice create`, and the binding schema is `AGENTS.md`. | `guidance/execute.md`, `startup/context.md` |

### Moving work between seats

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 14 | Handoff bodies were written to `$CLAUDE_JOB_DIR/tmp/…`. That is a **background-job** variable and is absent on all seven foreground tmux seats — all seven would have shared one `/tmp/handoff.md` and silently swapped bodies. | `mktemp` per handoff, written into the report file itself. | `startup/protocol.md` recipe, every handoff site |
| 15 | `rig send` was used for work that must survive a missed message. | `rig send` informs; `rig queue handoff` moves work. The recipe is in one place so the seven role files stop restating it. | `startup/protocol.md` "Talking to a seat, or moving the work" |
| 16 | The daemon's human-route enforcer was assumed to be a floor. It fires on `tier === "human-gate"` or `^human(?:-…)?@(kernel\|host)$` — **a person's gateway address matches neither**, so nothing is enforced, not even a blank `--evidence-ref`. | The evidence-ref is the seat's **own** responsibility, stated as such, not as a rule something else enforces. | `guidance/architect.md`, `guidance/resolve.md`, `startup/protocol.md` |
| 17 | `AskUserQuestion` was a natural way to ask. It is not available to these seats. | Seats ask through the queue; the ban is explicit so a skill cannot re-introduce it. | `startup/protocol.md` "Asking the person" |
| 18 | A single run record per host would have made two rigs overwrite each other's project. | `~/.hacf/<rig>/current-run.md` — the rig name is in the path. | `startup/context.md`, `guidance/lead.md` |

### Graph and spec mechanics

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 19 | `hypergraph_updater.py` was described as marking dependents only. It also marks **`implements` parents**. | Marked: dependents *and* implements parents. Check the exit code — **1 means it wrote nothing**. `<id> does not exist` is not a code finding: stop, hand back to the lead. | `guidance/execute.md` |
| 20 | The updater was treated as inspectable. It has no dry-run; `--help` prints usage and exits 1; the provenance flags write too. | Superseded (v0.6.0): `hypergraph_updater.py --help` exits 0, and the updater writes only with `--apply`. Was: say what it does, not what to try. | `guidance/red-team.md`, `guidance/execute.md` |
| 21 | The audit's reconciliation seemed to violate the team's "never edit the compiled graph by hand". | Superseded (v0.6.0): reconciliation goes through `hyper_ledger.py review-template` and `apply-review`, with no `mv` into `spec/archive/` and no hand edit. Was: a carve-out for `/hyper-audit`'s manual fallback, plus moving the compiled MiniPRD into the archive by hand. | `guidance/audit.md`, `CULTURE.md` |
| 22 | `archive_specs.py` scope and output were guessed. It touches only `spec/active/` and prints `INFO: No active specifications found to archive.` with exit 0. | Superseded (v0.6.0): `archive_specs.py` takes the forms its `--help` lists (`--paths`/`--label`, `--miniprd` for the legacy flat layout) and is the only writer of `spec/archive/`; the role files name those forms. Was: exact scope and exact text, so "it printed INFO" is not read as a failure. | `guidance/audit.md`, `guidance/resolve.md` |
| 23 | Re-specify had no defined source. | Superseded (v0.6.0): the source is now `~/.hacf/<rig>/drafts/<mission>/`, where resolve copies the drafts before it flushes, and the loop record lives in `~/.hacf/<rig>/loops/`. Was: restore from `spec/process/<feature>-drafts/`, and **move** the spent loop record to `…-attempt1.md` so attempt 2 does not inherit attempt 1's iteration count. | `guidance/lead.md` "The build loop", `guidance/resolve.md` |

### Found by contact with a real host (after deploy)

The three passes above read files. These three came from actually running the installer on one
machine (the evidence is under "Example host notes"), and only one of them was visible in any of
the earlier passes' reasoning. All three guarded the v0.5.x installer; v0.6.0 rewrote the steps
they describe.

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 24 | `pip` was installed and the preflight passed, so the install was declared unblocked. The installer's next Python step was `pip install pyyaml --quiet`, and a system Python marked externally managed (PEP 668) exits 1 with `externally-managed-environment` **even when pyyaml is already there**, because pip refuses before checking whether the requirement is satisfied. Under `set -euo pipefail` the whole install aborts. Preflight passing is not the install working. | Superseded (v0.6.0): the installer checks for `python3` >= 3.10 with PyYAML >= 5.4 and installs PyYAML only with the `--install-deps` consent (`pip install --user`), so no environment variable is needed. The lesson stands: a passing preflight is not a working install. Was: run it as `PIP_BREAK_SYSTEM_PACKAGES=1 bash HACF-install.sh …`. | `guidance/lead.md` Phase 0 |
| 25 | No `.git` → `install_hook` runs `[ -d ".git" ] \|\| return`, which returns the failed test's status, and `set -e` kills the installer **after** it copied `.agents/`, the IDE directories, `CLAUDE.md` and `AGENTS.md`. Half-installed project, exit 1. | Superseded (v0.6.0): outside a git root the installer refuses before any write, with `Nothing was written.` (interactively it offers `git init`). Was: `git init` first is load-bearing. | `guidance/lead.md` Phase 0 |
| 26 | `--mode=dry-run` was offered as a way to check first. It guarded only the pip call — it still wrote every file into the current directory. | Superseded (v0.6.0): `--dry-run` (and `--mode=dry-run`, its alias) prints every write and writes nothing. Was: it is not a preview; a real preview is a `mktemp -d` throwaway with `git init`, deleted after. | `guidance/lead.md` Phase 0, `README.md` |

Row 24 is the one to remember: the earlier passes all reasoned about whether `pip` **existed**,
and none of them asked whether `pip install` would **run**. The check that settled it took one
command on the real host.

### Learned running the pipeline (v0.6.0)

The framework's own v0.6.0 release was built slice by slice through these seats. What that run
taught is split two ways, and the split is the rule: a **default** holds on every run; a
**per-run grant** holds only when the person gives it for one run.

| # | What would have happened | Rule now in force | Where |
|---|---|---|---|
| 27 | A seat that carried one slice's context into the next, or into the audit of its own build, judged with the builder's assumptions. | Default: fresh context per stage run. The lead clears a seat before every new slice and every audit or fix round, so execute ↔ audit routes through the lead. | `startup/protocol.md` "Defaults learned the hard way", `guidance/lead.md` |
| 28 | `/clear` sent without `--raw` arrived as message text, and a handoff to a just-cleared seat could fail to wake it ("Recipient binding changed"). | Default: `rig send <seat> "/clear" --raw --wait-for-idle 60`, dispatch only after `Sent to`, then nudge the seat with the row id and check the row is claimed. | `startup/protocol.md` "Defaults learned the hard way", `guidance/lead.md` |
| 29 | A loop record inside the repository was an untracked file a scope check counted against the slice. | Default: loop files live in `~/.hacf/<rig>/loops/`, never in the repository. | `startup/protocol.md` "The build-loop record" |
| 30 | Full test runs on every iteration, or a run stopped half way, cost hours and shared disk. | Default: targeted tests while iterating, the full check once per slice, the auditor's own full run; never stop a run in progress. | `startup/protocol.md` "Defaults learned the hard way" |
| 31 | A permission that one run's person granted (autonomous mode, a waiver, a lore copy-aside) was written into the shipped text and became every run's rule. | Per-run grants are a pattern, not defaults: the lead records one under `## Per-run grants` in the run record only with the person's words and the row id, and `bin/brief.sh` copies it verbatim. | `startup/protocol.md` "Per-run grants", `guidance/lead.md`, `bin/brief.sh` |

## Rejected findings

Each was raised, checked, and is not a defect. The evidence is the reason it stays rejected.

| Claim | Why it is wrong |
|---|---|
| "The MiniPRD template's confidence rule (below 9) contradicts the rig's floor of 10." | Invented. The template says *below 9 → reach 10*. The two agree; the rig simply applies the floor to the SuperPRD too. |
| "`--evidence-ref` must point at an existing path, or the route fails." | False. Only a non-blank string is checked, and for a person's gateway address nothing is checked at all. Replaced by row 16, which says the seat owns it. |
| "`--ides` is ignored because auto-yes wins." | Wrong mechanism. The non-TTY branch pre-empts `--ides` regardless of `-y`. Replaced by row 9. |
| "The graph is not versioned, because `.gitignore` ignores `spec/compiled`." | False for projects: the installer never copies that `.gitignore`. The real state is untracked-and-not-ignored. Replaced by row 7. |
| "One commit per MiniPRD." | Contradicted by the fix loop. Replaced by row 4. |

## What this register does not cover

The rows above are guards on **instructions**. A row's rule is checked against the files it
names; whether a seat follows it under load is shown only by running the pipeline. Rows 27-31
came from one such run. A rig on a different host or permission posture has not had that test.

## Hardening a duplicate

The duplicate launches from the same bundle, so it inherits all of this. Re-run the method when
its role files diverge — a duplicate with a different project, host, or permission posture has
its own host facts to check. Check the cheap ones first, because they are what the passes kept
finding: does `python3` exist (and `python` not), does the foreground seat have the job-dir
variable, does the project's graph file end up tracked, what is the *actual* enforcement for this
rig's human address, and — the one that only a real run finds — does the installer actually
**run** to completion on this host, not merely pass its preflight.

## Example host notes (verified on one machine, 2026-11-16)

These are facts about the one machine the rig was first hardened on (the first host, an Ubuntu
desktop), kept as examples of what a host check finds. They describe the v0.5.x installer, which
rows 24-26 now mark superseded. Re-check them on your own host; do not assume them.

- `python` did not exist; only `python3` (3.14), with `pyyaml` 6.0.3, `git` 2.53.0, `curl`,
  `tmux` and `rsync` present.
- After `apt install python3-pip`, `pip` 25.1.1 was at `/usr/bin/pip` and `/usr/bin/pip3` and the
  v0.5.x preflight passed, but `pip install pyyaml --quiet` exited 1 with
  `externally-managed-environment`. `PIP_BREAK_SYSTEM_PACKAGES=1` made that step exit 0 with no
  writes: `dist-packages` untouched, pyyaml still 6.0.3.
- The v0.5.x installer ran end to end in a `mktemp -d` throwaway with `git init`: exit 0,
  "Installation complete!", and the `pre-commit`, `commit-msg` and `pre-push` hooks installed.
  Without `git init` it exited 1 at `install_hook`, after copying files.
- The human address there was `<user>@external`, which the daemon's human-route enforcer
  (row 16) does not match.
