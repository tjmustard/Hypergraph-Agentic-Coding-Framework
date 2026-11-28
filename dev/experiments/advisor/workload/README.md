# Advisor-experiment workload

The fixed work every arm of the experiment does (slice 13, `OPR.99.0.16.13`).

- `seed/`: `stockroom`, a small standard-library Python package with its own pytest tests.
- `missions/advisor-workload/`: two compiled slices, `01-small` and `02-medium`.

`run_arm.py` copies both into a scratch project, installs the kit there and never edits them here.
The slices' oracles are light (a few seconds); they are not heavy jobs.

The planted defects used by the audit-only replay are **not** here, and neither is a reference
build. They live outside the repository; only their hashes are committed (`../sealed.sha256`).
