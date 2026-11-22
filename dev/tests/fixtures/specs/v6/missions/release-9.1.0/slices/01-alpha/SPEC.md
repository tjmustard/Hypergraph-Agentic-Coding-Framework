---
id: OPR.9.1.0.1
slice: 01-alpha
mission: release-9.1.0
status: active
stage: wip
verified: '2026-10-21 against /hyper-resolve compile'
created: '2026-10-21'
intent: 'Alpha: the first fixture slice.'
depends_on: []
hacf_schema: 1
hacf_type: MiniPRD
hacf_status: compiled
node_id: alpha_module
parent: orchestration_layer
hacf_touches: ['src/alpha.py', 'tests/alpha/**']
hacf_oracle: [['python3', '-m', 'pytest', 'tests/alpha', '-q']]
lore_refs: [ADR-0001]
hacf_legacy_source: null
hacf_legacy_order: null
---

# MiniPRD: Alpha module

## Intent

Alpha is the first fixture slice.

## 1. The Confidence Mandate

Confidence 10/10.

## Mini-requirements

1. US-001: alpha() returns 1.

## 3. Implementation Plan (Task List)

- [ ] Task 1: write src/alpha.py.

## 4. The Negative Space (Constraints)

- **DO NOT** touch beta.

## Proof contract

- [ ] test_alpha passes.

## Source material

- (none)

## Intent visual

N/A

## Status

- compiled

## Dependencies

- (none)
