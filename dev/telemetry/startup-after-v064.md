# Start-up after the trim, v0.6.4

The "after" numbers of slice OPR.99.0.8.6, measured on 2026-11-24 with the same instrument as the
before table (`dev/telemetry/startup_probe.py`, a `claude -p` reproduction; see
`startup-baseline-v064.md` for the method and its caveats). Model `claude-opus-5-5`, Claude Code
2.1.331, the workspace's managed `CLAUDE.local.md` (read only).

What changed against the before run:
- roles v2 denies through `seat-rules.py` (the built-in tools each role never names, and the 23 MCP
  servers the probe listed; never codebase-memory-mcp);
- the deduplicated start-up texts: the prompt files from the working tree, and
  `--culture openrig/hacf/CULTURE.md` for the managed CULTURE.md block;
- no rig-key lever (that is slice OPR.99.0.8.7).

One more caveat: the before run used the managed file's own CULTURE.md block (the live rig's copy),
and this run replaces it with the repository's CULTURE.md, so a small part of each delta is the
difference between those two texts.

## Turn-1 per role, before and after

Execute is the median of 3 repeats (39816, 39782, 39779); the others ran once.

| role | before | after | delta | change | input | cache write | cache read | tools | MCP servers | skills/commands |
|---|---|---|---|---|---|---|---|---|---|---|
| lead | 49659 | 41860 | -7799 | -15.7% | 2 | 35461 | 6397 | 8 | 23 | 149 |
| architect | 44696 | 36890 | -7806 | -17.5% | 2 | 30491 | 6397 | 8 | 23 | 149 |
| red-team | 44799 | 36965 | -7834 | -17.5% | 2 | 30566 | 6397 | 8 | 23 | 149 |
| resolve | 45816 | 38012 | -7804 | -17.0% | 2 | 31613 | 6397 | 8 | 23 | 149 |
| execute | 47639 | 39782 | -7857 | -16.5% | 2 | 33417 | 6397 | 8 | 23 | 149 |
| audit | 46513 | 38707 | -7806 | -16.8% | 2 | 32308 | 6397 | 8 | 23 | 149 |
| scribe | 45157 | 39415 | -5742 | -12.7% | 2 | 30952 | 8461 | 9 | 23 | 149 |

The tool count falls from 87 to 8 (9 for the scribe, whose stage skill names `Workflow`). The
remaining tools include `Task` (in this Claude Code build the init inventory names the sub-agent
tool `Task`, not `Agent`) and `ToolSearch` (allowed deferred tools need it). The MCP servers are
still listed (23): a deny removes their tools, not the server entries.

## What remains, and which lever cuts it next

- The cache read every role shares fell from 11726 to 6397 (8461 for the scribe, which keeps
  `Workflow`): the cached prefix of Claude Code's own system prompt and the remaining tool
  definitions. No per-seat surface reaches the rest of it.
- The managed `CLAUDE.local.md` blocks: the onboarding pack (`openrig-onboarding-01/02.md`, about
  2.6k on execute in the before table) is host-wide, so it is the operator's lever, not a seat's.
- The skills and slash-commands listing (149 entries) has no per-seat surface.
- The MCP server entries and connector settings: the rig keys of slice OPR.99.0.8.7
  (`env:` with `ENABLE_CLAUDEAI_MCP_SERVERS=false`, `claude_mcp` strict config).
- The start-up text itself (`protocol.md` alone was about 5.0k in the before table) cannot lose a
  required rule; the dedupe removed only duplicated passages.

## Verdict

**measured; aim 15-20k: reached by none / not reached by lead, architect, red-team, resolve, execute,
audit, scribe** (R5: a measured aim, not a gate). Roles v2 and the dedupe cut turn-1 by 5.7k-7.9k
per role (12.7%-17.5%); the after range is 36.9k-41.9k.

## After + rig key (slice OPR.99.0.8.7)

The shipped `rig.yaml` now sets `ENABLE_CLAUDEAI_MCP_SERVERS: "false"` for every seat (proven by
`rig spec validate` on OpenRig 0.7.16 and 0.7.21 inside the compat check). Measured with the same
instrument, roles v2, `--culture openrig/hacf/CULTURE.md` (which now also carries the restored lore
line, ruling P25) and `--lever env-no-claudeai-mcp`, which sets that variable for the probe; one run
per role.

| role | before | after | after + rig key | key's delta | change vs before | tools | MCP servers |
|---|---|---|---|---|---|---|---|
| lead | 49659 | 41860 | 41150 | -710 | -17.1% | 8 | 19 |
| architect | 44696 | 36890 | 36177 | -713 | -19.1% | 8 | 19 |
| red-team | 44799 | 36965 | 36252 | -713 | -19.1% | 8 | 19 |
| resolve | 45816 | 38012 | 37300 | -712 | -18.6% | 8 | 19 |
| execute | 47639 | 39782 | 39069 | -713 | -18.0% | 8 | 19 |
| audit | 46513 | 38707 | 38000 | -707 | -18.3% | 8 | 19 |
| scribe | 45157 | 39415 | 38702 | -713 | -14.3% | 9 | 19 |

The key removes the 4 claude.ai connector servers (23 to 19 listed) and their instructions, about
0.7k per role (the restored lore line adds a few tokens back). Roles v2 had already denied their
tools, so the key's own saving is small. The 19 plugin MCP servers remain listed, without tools.

**measured; aim 15-20k: reached by none / not reached by lead, architect, red-team, resolve, execute,
audit, scribe** (R5: a measured aim, not a gate). After the rig key: 36.2k-41.2k per role, 14.3%-19.1%
below the before table.
