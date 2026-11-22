# HACF v0.6.0 — Normative Design Annex

This directory is the durable, agent-readable reference for the v0.6.0 release. `spec/active/Draft_PRD.md` is archived by `/hyper-resolve` into the agent-blocked `spec/archive/`, so every compiled MiniPRD cites this annex instead.

## Precedence

1. The MiniPRD being executed (`spec/compiled/MiniPRD_NN_<Slug>.md`).
2. The SuperPRD trade-off log (`spec/compiled/SuperPRD_v0_6_0.md`).
3. This annex.

Only `/hyper-resolve` may edit this annex, and it logs every change in the SuperPRD trade-off log. After compile the annex is frozen; only the [Replan procedure](operating-procedure.md#replan) reopens it, for unfinished slices (R14).

A MiniPRD's `## Pipeline Operating Notes` override the skill text where they differ (R15). A MiniPRD may diverge from this annex only where an R-row in the SuperPRD trade-off log records it; an unrecorded divergence is a defect: stop and ask (R19.9).

## Contents

| File | Holds |
|---|---|
| [decisions.md](decisions.md) | D1–D14 with rejected alternatives, ADR-0010/0011, deviations, all critique resolutions, open risks |
| [schemas.md](schemas.md) | Target layout and every schema (SPEC frontmatter, PROGRESS/PROOF/NOTES, OpenRig manifests, lore graph, code-graph config, orchestration.yml, ledger v2) |
| [subsystems.md](subsystems.md) | Engine, lore, installer/update, migration, firewall, skill change matrix, verification |
| [slices.md](slices.md) | The 29 slice seeds (NN 00–28) with tasks, tests and constraints |
| [operating-procedure.md](operating-procedure.md) | Global session rules, bootstrap steps, the `hacf-bootstrap` block schema, compile instructions, gate amendment C4, replan, main moved, abort a slice |
| [evidence.md](evidence.md) | Codebase evidence (file:line), primary external sources, UNVERIFIED items |
| guards.json | Content hashes the gate refuses to let change |

Process tooling: `dev/scripts/v060_gate.py`. Reference-only pack (never read during slices): `PRDs/v0.6.0/`.
