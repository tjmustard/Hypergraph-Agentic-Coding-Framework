# Lore and the CANON Gate

Lore is the project's memory of *why*: the decisions, stories and binding rules that outlive any one
feature. Agents read it before they plan and propose new entries as they work; **only a person turns a
proposal into CANON**. This guide covers the store, the approval flow, what the gate does and does not
guarantee, and how to protect lore on a team.

Every command is `python3 .agents/scripts/hyper_lore.py <command>`; `--help` lists them. Exit codes:
0 ok, 1 validation, 2 usage, 3 lock, 4 refused, 5 conflict.

## The store

| Path | What it holds |
|---|---|
| `lore_hypergraph.yml` | The lore graph (`schema: hacf.lore/v1`): every node's id, type, status, anchors, approval and history. Written only by `hyper_lore.py`. |
| `lore/ADRs/` | Decision bodies (`ADR-NNNN-…`) |
| `lore/STORIES/` | Story bodies (`STORY-NNNN-…`) |
| `lore/proposed/` | Canon bodies waiting for approval (`lore/proposed/<ID>.md`) |
| `lore/canon/<ID>.md` | Immutable approved bodies. Every node that has ever been CANON has one; nothing edits it afterwards. |
| `lore/MAP.md` | Start here. A hand-written intro plus a generated region: Canon, Proposed, Stale, Graveyard, Stories and "Where things live". |
| `lore/CANON.md` | Generated: the bodies of every CANON node, concatenated in id order. |

`MAP.md`'s generated region and `CANON.md` are rebuilt by `hyper_lore.py index` and, when `lore/MAP.md`
exists, after every graph write; `validate` fails when they are out of date. Do not edit them by hand.

### How the store is created

A fresh project gets its store from the installer: the scaffold (`hyper_scaffold.py apply`) runs

```bash
python3 .agents/scripts/hyper_lore.py init
```

when `lore_hypergraph.yml` is missing. `init` writes an empty `hacf.lore/v1` graph (no nodes, every id
counter at 0) through the same locked compare-and-swap writer as every other command, then generates
`lore/MAP.md` and `lore/CANON.md` exactly as `index` does. It **refuses** (exit 4, nothing written) when
`lore_hypergraph.yml` already exists in any form — a graph, an empty or unparsable file, or a symlink —
so it never overwrites your lore. It is also refused inside an engine phase (`HACF_OUTBOX` set). Without
`init`, `propose` and `note` still create the graph on first use.

### Statuses and the graveyard

| Status | Meaning |
|---|---|
| PROPOSED | Written by an agent or imported; waiting for a person. A PROPOSED node with `origin: user-decision` already binds agents until it is rejected. |
| CANON | Approved by a person. Binding on every agent. A **stale** CANON node (its anchors changed or vanished) stays binding until it is retired or superseded. |
| REJECTED | Turned down; node and body are kept. |
| SUPERSEDED | Replaced by a newer CANON node. |
| RETIRED | No longer applies. |

REJECTED, SUPERSEDED and RETIRED nodes form the **graveyard**: agents cite their ids so a buried idea is
not proposed again, but never follow them. `MAP.md` lists the newest 20 rejections with their warrants;
`hyper_lore.py list --status REJECTED` lists all of them.

### What agents write

Agents propose (`propose --type decision|story|canon`), record field notes (`note`) and mark nodes stale
(`stale`). `/hyper-resolve` turns each trade-off into a PROPOSED decision; `/hyper-execute` writes notes;
the ledger's blast-radius update marks lore anchored to a changed component stale. None of this can make
anything CANON.

## The approval flow

Approval happens in `/hyper-lore-approve`, or in `/hyper-audit` Phase 3.5 for the nodes a slice
proposed. Both follow the same steps, one node per approval — there is no approve-all:

1. **One request.** `approval-request <ID>` prints the exact body that will be hashed, its sha256, a
   random nonce and every id the approval would supersede or retire. It writes a pending record under
   `.agents/state/lore/pending/` that expires after 30 minutes; only one request may be pending.
2. **Your answer.** The agent shows you the body, anchors and related nodes and asks: Approve, Reject,
   Keep PROPOSED or Skip (for stale CANON: Still valid, Retire or Keep stale).
3. **Your warrant.** For Approve, Reject, Retire or Still valid, the agent asks for your reason in your
   own words and saves it as the warrant file.
4. **The gated command.** The agent runs, for example,
   `hyper_lore.py approve <ID> --nonce <n> --warrant-file <f>`. In Claude Code, the HACF PreToolUse
   hook turns that command into Claude Code's own permission prompt, showing the id, title, warrant and
   affected ids. Before it asks, the hook writes a **single-use ticket** under
   `.agents/state/lore/tickets/`, bound to the verb, id and nonce and valid for 120 seconds. If you
   deny the prompt, the command does not run and the unused ticket simply expires.
5. **The script's checks.** `hyper_lore.py` consumes the matching ticket and checks the nonce, that the
   body's sha is unchanged, that the warrant is non-empty and differs from the title, and that git
   `user.name` and `user.email` are set. It records the approval and history, moves an approved body to
   `lore/canon/<ID>.md`, applies any supersession and regenerates the index.

**Tools without a PreToolUse hook** (Cursor, Cline, Roo, Windsurf, a plain terminal): with no ticket,
the gated verb proceeds only when stdin and stdout are a TTY and you type the node id at its
confirmation prompt (the ticketless TTY fallback). Otherwise it exits 4.

**Permission modes.** Lore approvals need Claude Code's **default or acceptEdits** mode. The gate hook
denies the gated verbs (approve, reject, retire, reopen, review) in bypassPermissions, dontAsk and plan
mode with "switch to default or acceptEdits (Shift+Tab) and retry". Every gated verb is also refused
under `HACF_AUTONOMOUS=1`, which the workflow engine sets: an engine run can propose lore, never approve
it.

After an approval session, `/hyper-lore-approve` recommends a commit that touches only lore paths; it
does not commit for you.

## What the gate guarantees, and what it does not

The CANON gate is **procedural**: it makes sure a cooperating agent cannot make CANON without a person
answering a prompt. It is not a security boundary.

**Remaining bypasses:**

- **A deliberately forged ticket.** Anyone (or any process) that writes a well-formed ticket file with the
  right verb, id and nonce passes the ticket check.
- **Direct Python writes to `lore_hypergraph.yml`.** The firewall's Edit denies stop the agent's file
  tools, not a Python or Node process that opens the file itself.

**`approved_by` is attribution, not identity.** It is copied from git `user.name` and `user.email`,
which anyone can set. `validate` checks the graph's format; it cannot tell a forged approval record from a
real one. Git history and code review are the provenance: `hyper_lore.py list --status CANON
--with-commit` shows the commit that made each node CANON.

## Teams: merges and CODEOWNERS

**Branch merges are not gated.** A branch whose `lore_hypergraph.yml` gained a CANON node merges cleanly
through the `hacf-lore` merge driver, and its canon file comes with it. If CANON matters on your team,
protect the lore paths in review. A `CODEOWNERS` file is recommended (optional):

```text
# .github/CODEOWNERS
/lore_hypergraph.yml   @your-org/lore-owners
/lore/canon/**         @your-org/lore-owners
/lore/CANON.md         @your-org/lore-owners
```

Pair it with branch protection that requires a code-owner review.

**Merge drivers.** `hyper_lore.py install-drivers` (run by the scaffold) installs two drivers
repo-locally: `hacf-lore` merges `lore_hypergraph.yml` by node id (a union of nodes and history; a real
conflict exits with a report naming `renumber`), and `hacf-generated` rebuilds `lore/MAP.md`'s region and
`lore/CANON.md` from the merged graph. Their config goes into `git config --local`, and their attributes
into the managed block `merge-drivers` of `$(git rev-parse --git-common-dir)/info/attributes` — never into
a tracked `.gitattributes`, so your own attributes (Git LFS rules, for example) are never touched. Each
clone runs the installer or `install-drivers` once to get them.

<a id="mcp-server-entries"></a>
## Code-graph MCP entries

If the project selects the codebase-memory-mcp code-graph backend, the installer adds an entry to the
tracked `.mcp.json` (Claude Code), `.cursor/mcp.json` (Cursor) and `.roo/mcp.json` (Roo). These files are
committed, so **every teammate who accepts that project server runs it on their machine.** Which entry
is written depends on how the installer installed the server:

| Install method | Entry |
|---|---|
| uvx (no `uv` on PATH, `uvx` found), or nothing installed | `uvx codebase-memory-mcp@0.12.1`: the version is pinned exactly |
| uv-tool (`uv tool install codebase-memory-mcp==0.12.1`) or the vendor script (curl) | `codebase-memory-mcp`: unpinned; each teammate runs whatever binary of that name is on their PATH |

Review the entry before you accept it, and remove it by hand if your team does not want it; rerunning
the installer with `--code-graph=none` does not remove it.

## Limits in one place

- The CANON gate is procedural, not a security boundary (see the bypasses above).
- The agent firewall (`.agentignore` rendered into permission rules) is not a security boundary either:
  it does not cover Python or Node subprocesses that open files themselves, or commands that read files
  without naming them.
- OS-level enforcement exists only in the workflow engine's sandboxed phases: agent phases use bwrap on
  Linux and `sandbox-exec` on macOS; the oracle sandbox (bwrap) is Linux-only.

See also: [MasterSOP](MasterSOP.md), [Troubleshooting](Troubleshooting.md#lore).
