# OpenRig 0.6.13 manifest fixture: sources

The four YAML files in this directory are the manifest templates OpenRig writes, copied byte for
byte from the source at tag `v0.6.13`, commit `9eeb2e42ca6dcfb8da60a8517dc39c7d83b84082`
(github.com/mvschwarz/openrig, Apache-2.0). They were transcribed on 2026-10-26 (UTC) by
extracting each template literal from a shallow clone of that tag (no interpolation or escapes
occur in them). `.agents/scripts/hyper_openrig.py` renders the same bytes, and
`dev/tests/openrig/test_openrig_adapter.py::test_render_matches_fixture` compares the two.

| File | Constant | Source at the tag |
|---|---|---|
| `project.yaml` | `PROJECT_MANIFEST` | https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/daemon/src/domain/workspace/default-workspace-scaffold.ts#L20 |
| `workspace.yaml` | `WORKSPACE_CATALOG` | https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/daemon/src/domain/workspace/default-workspace-scaffold.ts#L32 |
| `mission.yaml` | `MISSION_MANIFEST` | https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/commands/scope.ts#L102 |
| `slice.yaml` | `SLICE_MANIFEST` | https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/commands/scope.ts#L116 |

## Composition rules

Copied from `validateMissionComposition`
(https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/daemon/src/domain/lifecycle-manifest.ts#L30),
the one strict reader of `mission.yaml` membership that rig's read paths and scope writes share:

- The manifest is a mapping with `kind: mission`, and `composition.slices` is a list.
- Each entry is a mapping whose `ref` is a non-empty string.
- `ref` is a safe relative path: not absolute, and its normalized form has no `..` segment.
- `ref` is unique (compared after normalization).
- `order` is an integer.
- `order` is unique and strictly increasing down the list.
- The referenced file exists.
- The referenced file is not a symlink.
- The referenced file's real path lies inside the mission directory's real path.
- `active` is true unless it is literally `false`.

Appending a member (`planMissionMembershipAdd`,
https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/lib/scope/mission-composition.ts#L31)
uses `order` = the last member's order + 10, or 10 for the first member, and writes
`{ref: slices/<dir>/slice.yaml, order, active: true}`. `rig scope slice create` appends its own
membership after scaffolding the slice (`appendMissionMembership`), so a writer re-reads
`mission.yaml` after any rig call.

## Other pinned facts the adapter copies

- Dot-IDs (https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/lib/scope/dot-id.ts):
  `DOT_ID_RE = ^([A-Z]{2,3})\.(\d+(?:\.\d+){1,4})$` (L30), `RELEASE_NAME_RE =
  ^release-(\d+\.\d+(?:\.\d+)?)$` (L25), `DEFAULT_PROJECT_PREFIX = "OPR"` (L23); any other mission
  name takes the escape band `<PFX>.99.0.<n>`.
- C1 closed sets (https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/commands/proof.ts#L34):
  `artifact_type` guard | qa | rev1-r1 | rev1-r2 | adjudication; `verdict` CLEAR | BLOCKING |
  CONCERNING | PASS | NOT-CLEAR. Without `--name`, `rig proof add` names the artifact
  `<artifact-type>-<verdict>-<UTC>.md` (L198), which is why HACF always passes `--name`.
- `rig scope slice close --reason` takes wontfix | deferred | superseded | stale, moves the slice to
  `<mission>/closed/<NN-slug>`, removes its composition entry and sets `status: closed-<reason>`
  and `closed-on: <UTC date>` in its SPEC.md frontmatter
  (https://github.com/mvschwarz/openrig/blob/v0.6.13/packages/cli/src/commands/scope.ts#L545).
