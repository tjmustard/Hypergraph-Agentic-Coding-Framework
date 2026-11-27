# herdr-view fixtures

Recorded on 2026-11-26 with Herdr 0.9.5 and OpenRig 0.7.23 (484cbffb) (`rig --version`), from one
running seven-seat team view, and **scrubbed** before they were written here. Used by
`dev/tests/rig_spec/test_herdr_view.py`, which derives every scrambled, detached or eight-seat
variant in test code. Do not edit these files to make a variant.

| File | Read-only command | What is kept |
|---|---|---|
| `rig-ps.json` | `rig ps --nodes --rig <rig> --json` | `rigId`, `rigName`, `logicalId`, `canonicalSessionName` |
| `rig-spec-show.json` | `rig spec show <rigId> --json` | `name`, `pods[].id`, `pods[].members[].id` |
| `workspace-list.json` | `herdr workspace list` | `workspace_id`, `label`, `focused`; the team's space and one other |
| `pane-list.json` | `herdr pane list` | `pane_id`, `tab_id`, `workspace_id`, `label`, `agent`; the team's panes and one shell pane of the other space |
| `pane-layout.json` | `herdr pane layout --pane <lead pane>` | `tab_id`, `workspace_id`, `panes[].pane_id`, `panes[].rect` |
| `process-info.json` | `herdr pane process-info --pane <id>`, one entry per pane above | `pane_id`, `shell_pid`, `foreground_processes[].pid` and `.name` |

`herdr pane read` was never recorded: it holds terminal text.

## Scrub rules

- Only the keys above are kept. So no path (`cwd`, `foreground_cwd`, `argv`, `cmdline`),
  `terminal_id` or host id is here at all.
- The rig name is `demo-hacf`, the rig id `01DEMORIGID0000000000000000`, and each session
  `<seat>@demo-hacf`.
- The other space's label is `demo-other`.
- Herdr's own pane, tab and workspace ids and the process ids are as recorded.
