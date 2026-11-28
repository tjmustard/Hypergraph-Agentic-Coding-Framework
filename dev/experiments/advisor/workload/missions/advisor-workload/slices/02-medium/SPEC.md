---
id: OPR.99.0.1.2
slice: 02-medium
mission: advisor-workload
status: active
stage: wip
verified: "2026-11-27 against scaffold (hyper_scope slice create)"
created: "2026-11-27"
intent: "Import stock movements from CSV."
depends_on: [OPR.99.0.1.1]
hacf_schema: 1
hacf_type: MiniPRD
hacf_status: compiled
node_id: stockroom
parent: null
hacf_touches: ["stockroom/**", "tests/**", README.md]
hacf_oracle: [[uv, run, "--locked", pytest, "-q"], [uv, run, "--locked", python, "-c", "from stockroom.importer import import_movements"]]
lore_refs: []
hacf_legacy_source: null
hacf_legacy_order: null
---

# MiniPRD: Stock movements from a CSV file, all or none

**Hypergraph Node ID:** stockroom

## Intent

A stock keeper records a delivery or a stock take from one CSV file. A file with any mistake changes nothing.

## 1. The Confidence Mandate

**Score: 10/10.** The behaviour below is complete.

## Mini-requirements

1. `stockroom.importer.import_movements(ledger, path)` reads a UTF-8 CSV file and returns `(new_ledger, summary)`. `new_ledger` is a copy of `ledger` with every row applied in file order; `summary` is `{"applied": <rows>, "net": {sku: <sum of its deltas>}}`.
2. The first line is the header and is exactly `sku,delta,note`. Anything else is refused, and the message quotes the expected header.
3. Each data row has two or three fields. `sku` and `delta` are stripped of surrounding spaces. `delta` is a non-zero whole number, written with an optional sign and digits only. A missing `note` is empty.
4. A blank line (no fields, or only empty fields) is skipped.
5. A row is invalid when its fields do not match the rules above, its SKU is unknown, or applying it in file order would make that item's quantity negative at that point.
6. When any row is invalid, nothing is applied: `StockError` is raised, and its message has one line per invalid row, in file order, each starting `line <n>: ` where the header is line 1. Rows after an invalid row are still checked.
7. A file that cannot be read or decoded raises `StockError`. A file with only the header applies nothing and returns `applied: 0`.
8. The verb `import <csv>` saves the ledger only when the import succeeds, prints `imported <n> movements`, and exits 0; otherwise it prints the message on stderr, leaves the ledger file untouched and exits 1.
9. `README.md` shows the new verb and the file format.

## 3. Implementation Plan (Task List)

- [ ] Task 1: tests for Mini-requirements 1-8 in `tests/`.
- [ ] Task 2: `stockroom/importer.py`.
- [ ] Task 3: the `import` verb in `stockroom/cli.py`, and the README section.

## 4. The Negative Space (Constraints)

- **DO NOT** change the `ledger` passed to `import_movements`, whatever happens.
- **DO NOT** use a private attribute (a name starting with `_`) of `Ledger` outside `stockroom/store.py`.
- **DO NOT** write the ledger file anywhere but through `Ledger.save`.
- **DO NOT** print outside `stockroom/cli.py`.
- **DO NOT** add a runtime dependency or change an existing public signature.

## Proof contract

- [ ] The oracle exits 0.
- [ ] The `import` verb's output and exit code on a good file and on a file with two bad rows, with the ledger file unchanged after the bad one.

## Source material

- `stockroom/store.py`, `stockroom/cli.py`, `stockroom/errors.py`.

## Intent visual

N/A (non-visual slice).

## Status

- compiled

## Dependencies

- Slice 01 (`01-small`).
