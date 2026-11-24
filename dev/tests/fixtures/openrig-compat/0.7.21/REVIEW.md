# REVIEW: @openrig/cli 0.7.21 against the 0.7.16 baseline

- Run: `dev/openrig_compat.sh 0.7.21` -> `openrig-compat 0.7.21: REVIEW` (exit 3), every step green
  (install, tree, live 63 checks, captures, help, delegate probe/verify/proof, oracle, 0 daemon
  connections, ~/.openrig unchanged).
- Evidence: `<private evidence dir>/openrig-compat-0.7.21-20261123T075801Z/` (`verdict.json`, `REPORT.md`,
  `diffs/watched-mission-composition.js-vs-0.7.16.diff`, `diffs/watched-scope-audit.js-vs-0.7.16.diff`).
- Baselines: newest 0.7.16, newest person-accepted 0.7.16 (the same dir).
- Earlier read: Part A, `<private evidence dir>/openrig-0721-20261123T032756Z/REPORT.md` (rows (e) and (f))
  and its `pkg-diff/`. This run's two diffs carry the same added and removed lines as Part A's.

Only two of the 8 watched items differ. lifecycle-manifest.js, scaffold-placeholder.js and the 4
manifest templates (missionManifest, SLICE_MANIFEST, PROJECT_MANIFEST, WORKSPACE_CATALOG) are
byte-identical to 0.7.16.

## dist/lib/scope/mission-composition.js

What changed: one new export, `planMissionMembershipSetActive(missionPath, ref, active)`, which flips
a composition member's `active` flag. Nothing existing was edited.

Why HACF is unaffected: its only caller is `rig scope slice close --in-place` (Part A (e)). No HACF
argv builder passes `--in-place`; `argv_slice_close` passes only `--reason`, `--mission`, `--note` and `--json`
(the help step lists HACF's flags per command). The membership functions HACF's paths use
(`appendMissionMembership`, `planMissionMembershipAdd`/`Remove`) are unchanged, and the live
suite's composition checks (`composition_appended_by_rig`, `composition_after_closes`) pass.

## dist/lib/scope/scope-audit.js

What changed:
1. `statusRequiresProof` returns false for `closed-wontfix`, `closed-deferred`,
   `closed-superseded` and `closed-stale`, so non-delivery closures no longer need proof.
2. A slice's `## Requirements` R-list (`- R<n>.` items with an `Observable:` proof) can stand in
   for both `## Mini-requirements` and `## Proof contract`. An R item without a proof gets a low
   `proof_contract_missing_or_malformed` finding, and the mockup check reads the R list as well.

Why HACF is unaffected:
1. This change only removes findings. HACF closes superseded slices as `closed-superseded`.
   `hyper_migrate.py verify` fails on a non-zero `scope audit` exit and never on a finding that is
   absent. The live suite's `no_missing_proof` check passes on both versions.
2. HACF's slices use `## Mini-requirements` and `## Proof contract`. No file under `.agents/`
   (the templates HACF renders) or under `missions/` has a `## Requirements` heading, checked with
   `grep -rln '^## Requirements' .agents/ missions/` on 2026-11-23. The new branch therefore never
   runs on an HACF-authored slice. The numbered-item and checkbox rules HACF mirrors are unchanged.

Additive, reported rather than failed: help `scope slice close --in-place` and
`scope slice progress --current-state`; JSON `/parentFindings` (audit) and
`/closed/{inPlace,status,proofApplicability/*}` (close). HACF production code reads none of them.

Verdict: the two changes are off HACF's delegated path. 0.7.21 stays accepted, and this dir is its
baseline.

reviewed_by: build-execute@hacf (OPR.0.6.2.3 execute; Part A by spec-red-team@hacf; the audit re-reads it)
date: 2026-11-23
