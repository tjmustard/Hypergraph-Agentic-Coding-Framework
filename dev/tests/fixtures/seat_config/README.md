# Seat-config fixtures (OPR.99.0.10.9)

Stand-in live specs for `seat-config-upgrade.py`; stand-in names and paths only.

- `eta-like/`: absolute `cwd:`, no roles.yml/OPS.md/bin, a locally edited `startup/protocol.md`,
  `agents/hacf/guidance/scribe.md` as old HACF text (the tests tag it `v0.0.1` in a temp source),
  and a live-only `startup/local-notes.md`.
- `cleanroom-like/`: relative `cwd:`, old HACF files, a top-level `env:` with one other key.
- `odd/`: a flow-style `env:`, which must give `rig.yaml: manual`.
