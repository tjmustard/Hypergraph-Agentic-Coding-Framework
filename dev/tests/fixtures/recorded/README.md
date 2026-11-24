# Recorded live-run captures

`recorded/<SID>/` holds verbatim captures of live runs (OpenRig, codebase-memory-mcp, the
`claude` CLI, the Claude sandbox) made by the slice `<SID>`.

- The test harness writes every capture. The model never writes, edits or summarizes one.
- Each capture records the argv, stdout, stderr, exit code, tool version and the UTC date of
  the run, exactly as the harness observed them.
- Name capture files so git keeps them: `.gitignore` ignores `*.log` and `*.swp`, so
  `git check-ignore` must report nothing for a capture path.
- A verdict is computed by the test from the capture, never typed in by hand. The slice records
  that verdict where its MiniPRD says (STORY-0002 for S02; the audited Operating Notes prose for
  S04, S08 and S11).
- Live tests are opt-in by pytest marker (`live`, `openrig`, `codegraph`, `claude`, `sandbox`)
  and need the user's consent. `dev/scripts/check.sh` runs `-m 'not live'`.

## Raw and cleaned captures

The raw captures live only in `../recorded-raw/<SID>/`, which is never exported: they hold
account and host data (user names, paths, session ids, costs, usage windows). The files under
`recorded/<SID>/` are cleaned copies, written only by `../normalize_recorded.py` (a keep-list:
only the keys a reader asserts on, with paths and ids replaced by placeholders such as `<HOME>`,
`<TMP>` and `<SESSION-n>`). Tests read them through `../recorded_loader.py`, which puts the
running checkout's values back. After a live run, run
`python3 dev/tests/fixtures/normalize_recorded.py --set <SID>` and commit the cleaned copies;
`--check` confirms they match the raw captures.

When the user declines consent, the item uses a synthetic fixture instead and stays UNVERIFIED;
see `../synthetic/README.md`.
