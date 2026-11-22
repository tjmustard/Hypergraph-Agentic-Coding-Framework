---
name: hyper-lore-approve
description: Walks one PROPOSED or stale lore node at a time through the nonce, warrant and ticket gate. Use when the user wants to approve, reject or retire lore, or review a stale CANON node.
---

# Lore Approve

This skill puts one lore node at a time through the CANON gate. The gate is harness-assisted and procedural, not a security boundary. Git history and review are the provenance.

## Rules

- One node per approval, one id per gated command. There is no approve-all and no batching.
- Never run a gated verb (`approve`, `reject`, `retire`, `reopen`, `review`) through a script, test, make target or check. Run it as one direct Bash call, so that the PreToolUse hook can issue its ticket.
- Call every script with `python3`.

## How to use it

1. **Check the mode.** Run `python3 .agents/scripts/hyper_gates.py mode`. If it prints `autonomous`, refuse to run: say a person must make this decision, and stop. In the permission modes bypassPermissions, dontAsk and plan the gated command is denied, so tell the user to switch to default or acceptEdits (Shift+Tab) before continuing.

2. **Pick one node.** Use the id the user named. Otherwise run `python3 .agents/scripts/hyper_lore.py list --json` and pick one PROPOSED or stale node. Stale nodes come first.

3. **Request approval.** Run `python3 .agents/scripts/hyper_lore.py approval-request <ID>`. It prints the exact hashed body, its sha, the nonce and the affected ids. Only one request may be pending, and it expires after 30 minutes.

4. **Ask.** Show the body, anchors, provenance and related nodes (`python3 .agents/scripts/hyper_lore.py show <ID>`). In the question body, list every id the approval supersedes, retires or re-anchors, writing "none" for an empty group. Use **AskUserQuestion** with exactly these options:
   - PROPOSED node: 'Approve (becomes CANON)', 'Reject (kept in the graveyard)', 'Keep PROPOSED', 'Skip'.
   - Stale CANON node: 'Still valid', 'Retire', 'Keep stale'.

5. **Keep or Skip.** On 'Keep PROPOSED', 'Skip' or 'Keep stale', run `python3 .agents/scripts/hyper_lore.py approval-cancel <ID>` and go to step 9.

6. **Get the warrant.** On any other answer, ask for the warrant in the user's own words. Write it with the Write tool to `.agents/state/lore/pending/<ID>.warrant.md`. It must be non-empty and differ from the node's title.

7. **Run the gated command.** Run the one command for the answer as one direct Bash call:
   - Approve: `python3 .agents/scripts/hyper_lore.py approve <ID> --nonce <nonce> --warrant-file .agents/state/lore/pending/<ID>.warrant.md`
   - Reject: `python3 .agents/scripts/hyper_lore.py reject <ID> --nonce <nonce> --warrant-file .agents/state/lore/pending/<ID>.warrant.md`
   - Retire: `python3 .agents/scripts/hyper_lore.py retire <ID> --nonce <nonce> --warrant-file .agents/state/lore/pending/<ID>.warrant.md`
   - Still valid: `python3 .agents/scripts/hyper_lore.py review <ID> --still-valid --nonce <nonce> --warrant-file .agents/state/lore/pending/<ID>.warrant.md`

   Claude Code shows its native prompt with the id, title, warrant and affected ids. If the hook denies because of the permission mode, tell the user to switch to default or acceptEdits (Shift+Tab) and retry.

8. **Check the result.** The script consumes the ticket, checks the nonce, sha and warrant, records the approval and rebuilds the index. If it exits non-zero, report its message and do not retry with changed inputs.

9. **Continue only after a new answer.** Ask whether to take another node, and start again at step 2 for one more.

10. **Recommend the commit.** End by recommending a commit that touches only lore paths (`lore_hypergraph.yml`, `lore/`). List the changed paths and let the user make the commit; this skill does not commit.
