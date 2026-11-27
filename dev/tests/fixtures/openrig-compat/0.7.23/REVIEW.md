# REVIEW: @openrig/cli 0.7.23 against the 0.7.21 baseline

- Run: `dev/openrig_compat.sh 0.7.23 --harness agy` -> `openrig-compat 0.7.23: REVIEW` (exit 3), all 16
  steps ok, from a clean clone at `fef4a80`.
- Evidence: `<private evidence dir>/openrig-compat-0.7.23-20261126T044840Z/` (`verdict.json`, `REPORT.md`,
  `diffs/watched-SLICE_MANIFEST-vs-0.7.21.diff`).
- Baselines: newest 0.7.21, newest person-accepted 0.7.21 (the same dir).
- Harness agy: `unreferenced` (the 0.7.21 baseline holds no `harness/agy/`; this dir does).

Only `SLICE_MANIFEST` of the watched items differs.

## SLICE_MANIFEST

```diff
--- baseline 0.7.21/SLICE_MANIFEST
+++ candidate/SLICE_MANIFEST
@@ -6,5 +6,5 @@
     spec: SPEC.md
     progress: PROGRESS.md
     proof: PROOF.md
-# Optional assignment, SDLC, and evidence sections are added here.
+${executionDependsOn ? `execution:\n  depends_on: ${JSON.stringify(executionDependsOn)}\n` : ""}# Optional assignment, SDLC, and evidence sections are added here.
 `
```

What changed: the slice.yaml template writes an `execution.depends_on` block when it is given
`executionDependsOn`, and nothing new otherwise. Help adds one flag,
`scope slice create --execution-depends-on <dot-id...>`, described as writing slice.yaml
`execution.depends_on`; it is listed apart from the advisory `--depends-on`.

Why HACF is unaffected (read: the diff, `scope_slice_create.txt`, `argv_slice_create`,
`render_slice_yaml`, the `execution.depends_on` readers in `hyper_openrig.py` and `hyper_spec.py`,
the live suite's `_create`, and the slice create action in the 0.7.23 package's
`dist/commands/scope.js`):
1. `argv_slice_create` passes `--title`, `--intent`, `--depends-on` and `--json`, never
   `--execution-depends-on`; no file under `.agents/` or `openrig/` names the new flag.
2. The live suite's three `*_slice_yaml_pinned` checks pass on 0.7.23: rig's slice.yaml equals
   `render_slice_yaml()` byte for byte. Those creates pass no `--depends-on`. In `scope.js`
   slice.yaml is written as `sliceManifest(executionDependsOn)`, and `executionDependsOn` comes only
   from `--execution-depends-on`, so `--depends-on` does not change it (read, not run).
3. A slice.yaml that a person creates with the new flag is already handled: HACF reads
   `execution.depends_on`, lets it win over the frontmatter, and never writes it.

Additive, reported rather than failed: the help flag above. HACF's flags are all still present.

Accepted by the person: TJ's roadmap decision (row 66e58b7e), "Add OpenRig 0.7.23 to the shipped contract (accepted_by: person)".

reviewed_by: build-execute@hacf (OPR.99.0.14.1 execute; the audit re-reads it)
date: 2026-11-26
