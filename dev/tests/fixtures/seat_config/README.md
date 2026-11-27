# Seat-config fixtures (OPR.99.0.10.9)

Stand-in live specs for `seat-config-upgrade.py`; stand-in names and paths only.

- `eta-like/`: absolute `cwd:`, no roles.yml/OPS.md/bin, a locally edited `startup/protocol.md`,
  `agents/hacf/guidance/scribe.md` as old HACF text (the tests tag it `v0.0.1` in a temp source),
  and a live-only `startup/local-notes.md`.
- `cleanroom-like/`: relative `cwd:`, old HACF files, a top-level `env:` with one other key.
  Its `roles.yml` is the byte copy of tag `v0.6.3`'s table: a roles.yml no tag knows is refused
  (R158, OPR.99.0.14.3), so the copy test needs real HACF text here.
- `odd/`: a flow-style `env:`, which must give `rig.yaml: manual`.
- `eta-v067/` (OPR.99.0.14.3): `roles.yml` is the shipped table with `Monitor` and `TaskStop`
  removed from the execute, audit and lead `tools_deny`; `overrides.yml` carries the same edit.
  Not a whole live spec: the tests copy these two files over an `eta-like` one.
  OPR.99.0.14.8: each `tools_deny` also ends with the hand-appended `mcp__treg`, the shape of a
  live install on 2026-11-26 that had its own edit as well.
- `hand-deny/` (OPR.99.0.14.8): `roles.yml` is the shipped table with `, mcp__treg` appended to
  each `tools_deny` and without the header's v0.6.7 treg entry: the shape of the live installs on
  2026-11-26 whose only edit was that append. One file, copied over an `eta-like` live spec.
