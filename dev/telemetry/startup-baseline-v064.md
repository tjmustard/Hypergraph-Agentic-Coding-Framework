# Start-up baseline, v0.6.4 (before the trim)

The "before" numbers for slices OPR.99.0.8.6 (the trim) and OPR.99.0.8.7, measured by slice
OPR.99.0.8.5 on 2026-11-24 with `dev/telemetry/startup_probe.py`.

**The instrument** is a `claude -p` reproduction of one seat's launch inputs, not a live seat. Each
run sends the role's delivered start-up text (startup/context.md, startup/protocol.md, the lead's
startup/lead-first-move.md, the role file) as a one-word prompt, from a throwaway directory holding
a copy of the workspace's managed `CLAUDE.local.md`, with the role's `seat-rules.py` deny list. **Turn-1** is
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens` of that turn.

- Sources: openrig/hacf at `<base>` 04d40a2 (before the CULTURE.md secrets line), the workspace's
  managed `CLAUDE.local.md` as it stood (read only); model `claude-opus-5-5`; Claude Code
  2.1.331.
- Caveats:
  - OpenRig's own `--settings` and hooks are not reproduced;
  - the cwd's auto-memory differs from a seat's (the probe directory has none of its own);
  - the user-level settings, plugins and MCP connectors of this host are included, as a seat has them;
  - the comparison is like with like only: compare a later run of this same instrument, never a
    live seat's sample.

## Noise (execute, no lever, 3 repeats)

47639, 47636, 47639: median **47639**, spread 3 tokens.

## Turn-1 per role (no lever, one run each; execute is the median above)

| role | turn-1 | input | cache write | cache read | tools | MCP servers | skills/commands |
|---|---|---|---|---|---|---|---|
| lead | 49659 | 2 | 37931 | 11726 | 87 | 23 | 149 |
| architect | 44696 | 2 | 32968 | 11726 | 87 | 23 | 149 |
| red-team | 44799 | 2 | 33071 | 11726 | 87 | 23 | 149 |
| resolve | 45816 | 2 | 34088 | 11726 | 87 | 23 | 149 |
| execute | 47639 | 2 | 35911 | 11726 | 87 | 23 | 149 |
| audit | 46513 | 2 | 34785 | 11726 | 87 | 23 | 149 |
| scribe | 45157 | 2 | 33429 | 11726 | 87 | 23 | 149 |

## Each lever alone, on execute (one run each; delta against the median 47639)

| lever | turn-1 | delta | tools | MCP servers | skills/commands |
|---|---|---|---|---|---|
| deny-extra | 37150 | -10489 | 7 | 23 | 149 |
| env-no-claudeai-mcp | 44652 | -2987 | 21 | 19 | 149 |
| strict-mcp | 44296 | -3343 | 21 | 0 | 149 |
| no-onboarding | 45005 | -2634 | 87 | 23 | 149 |
| no-culture | 45836 | -1803 | 87 | 23 | 149 |
| no-protocol | 42657 | -4982 | 87 | 23 | 149 |

- `deny-extra` used a candidate list of 81 tool names: the init `tools` minus `roles.yml`'s execute
  `tools_allow`, minus every tool that `guidance/execute.md` or `.agents/skills/hyper-execute/SKILL.md`
  names (none matched). The list is 63 MCP tools plus 18 built-ins (Task, the Cron tools, DesignSync,
  ListAgents, the MCP resource tools, Monitor, PushNotification, RemoteTrigger, ReportFindings,
  ScheduleWakeup, SendMessage, TaskStop, ToolSearch, Workflow). It is a measurement, not a
  recommendation: whether a seat can lose a tool is slice OPR.99.0.8.6's call.
- `no-culture` and `no-protocol` are upper bounds only. No required rule may be removed (see
  `dev/tests/rig_spec/test_startup_rules.py`).
- The levers were measured one at a time; their savings overlap (for example, `strict-mcp` and
  `env-no-claudeai-mcp` both remove MCP tools), so they do not add.
