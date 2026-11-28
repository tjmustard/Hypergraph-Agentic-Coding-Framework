---
id: OPR.99.0.1.1
slice: 01-small
mission: advisor-workload
status: active
stage: wip
verified: "2026-11-27 against scaffold (hyper_scope slice create)"
created: "2026-11-27"
intent: "Add a low-stock report."
depends_on: []
hacf_schema: 1
hacf_type: MiniPRD
hacf_status: compiled
node_id: stockroom
parent: null
hacf_touches: ["stockroom/**", "tests/**", README.md]
hacf_oracle: [[uv, run, "--locked", pytest, "-q"], [uv, run, "--locked", python, "-c", "from stockroom.report import low_stock"]]
lore_refs: []
hacf_legacy_source: null
hacf_legacy_order: null
---

# MiniPRD: A low-stock report

**Hypergraph Node ID:** stockroom

## Intent

A stock keeper sees which items need reordering, the most urgent first.

## 1. The Confidence Mandate

**Score: 10/10.** The behaviour below is complete.

## Mini-requirements

1. `stockroom.report.low_stock(items)` takes a sequence of `Item` and returns a list of those with `reorder_level > 0` and `quantity <= reorder_level`.
2. The result is ordered by shortfall (`reorder_level - quantity`), largest first; equal shortfalls are ordered by SKU.
3. The verb `low` (`python3 -m stockroom --file <f> low`) prints those items with the existing table format, in that order.
4. With nothing to reorder, `low` prints exactly `nothing is low` and exits 0.
5. `README.md` shows the new verb.

## 3. Implementation Plan (Task List)

- [ ] Task 1: tests for Mini-requirements 1-4 in `tests/`.
- [ ] Task 2: `low_stock` in `stockroom/report.py`.
- [ ] Task 3: the `low` verb in `stockroom/cli.py`, and the README line.

## 4. The Negative Space (Constraints)

- **DO NOT** write the ledger file in the `low` verb.
- **DO NOT** print outside `stockroom/cli.py`.
- **DO NOT** add a runtime dependency or change an existing public signature.

## Proof contract

- [ ] The oracle exits 0.
- [ ] The `low` verb's output on a ledger with two low items and on one with none.

## Source material

- `stockroom/report.py`, `stockroom/cli.py`, `stockroom/model.py`.

## Intent visual

N/A (non-visual slice).

## Status

- compiled

## Dependencies

None.
