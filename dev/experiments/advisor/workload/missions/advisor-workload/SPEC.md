---
id: OPR.99.0.1
mission: advisor-workload
stage: wip
verified: "2026-11-27 against scaffold (hyper_scope mission create)"
created: "2026-11-27"
intent: "A small fixed workload: two slices on the stockroom package."
depends_on: []
hacf_schema: 1
hacf_type: SuperPRD
hacf_status: compiled
node_id: null
lore_refs: []
hacf_source: null
---

# SuperPRD: advisor-workload

## Intent

A small fixed workload: two slices on the stockroom package.

## 1. Introduction & Goals

Two small features on `stockroom`, a stock ledger of about 250 lines, built and audited one slice at a time.

## 2. Confidence Mandate

Both slices are settled; each SPEC states its own behaviour in full.

## 3. Scope

The two slices below. Nothing else in the package changes.

## 4. User Stories (Atomic)

- US-001: As a stock keeper, I list the items that need reordering.
- US-002: As a stock keeper, I record many stock movements from one CSV file, all or none.

## 5. Technical Specifications

Python 3.10+, standard library only. The oracles run in well under 30 s.

## 6. Negative Constraints

- **DO NOT** add a runtime dependency.
- **DO NOT** change an existing public function's signature or behaviour.

## 7. Risks & Mitigation

None beyond each slice's own contract.

## 8. Success Metrics

Each slice's oracle exits 0 and its audit passes.

## Slices

<!-- BEGIN HACF GENERATED: slices -->
| id | slice | intent |
|---|---|---|
| OPR.99.0.1.1 | slices/01-small | Add a low-stock report. |
| OPR.99.0.1.2 | slices/02-medium | Import stock movements from CSV. |
<!-- END HACF GENERATED: slices -->

## Status

- compiled
