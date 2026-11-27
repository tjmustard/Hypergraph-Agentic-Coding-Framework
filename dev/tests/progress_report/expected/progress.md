# HACF progress report

Generated 2026-01-07T00:00:00Z by progress-report.py (read-only, zero-token).

## 1. Headline (whole project to date)

| Project | Releases | Span (days) | Source lines (net) | Test lines (net) | Tests (functions / collected cases) | Features shipped | Slices | Human decisions |
|---|---|---|---|---|---|---|---|---|
| demo | 2 | 5.5 | 5 | 11 | 4 / not recorded | 3 | 4 | not recorded |

- Human decisions: questions / answers, the queue rows tagged hacf:question / hacf:answer (or their legacy names question-for-tj / tj-answer). Not the same count as §4's Decisions.
- Collected cases are the cases the project's own pytest configuration selects; "(N deselected)" counts the cases it deselects.

## 2. Per release

### demo

| Version | Date | Days | Commits | Source +/− | Test +/− | Unit | Integration | E2E/acceptance | Features | Slices (replans) | First-pass audit % | ADRs / stories | Tokens / cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v0.1.0 | 2026-01-03 | 2.0 | 2 | 3 / 0 | 9 / 0 | 2 / not recorded | not recorded | 1 / not recorded | 2 | 3 (not recorded) | not recorded | 1 / 0 | not recorded |
| v0.2.0 | 2026-01-06 | 3.5 | 3 | 3 / 1 | 2 / 0 | 2 / not recorded | 1 / not recorded | 1 / not recorded | 1 | 1 (not recorded) | not recorded | 2 / 1 | not recorded |
| **Total** | 2026-01-03 → 2026-01-06 | 5.5 | 5 | 6 / 1 | 11 / 0 | 2 / not recorded | 1 / not recorded | 1 / not recorded | 3 | 4 (not recorded) | not recorded | 2 / 1 | not recorded |

- Collected cases are the cases the project's own pytest configuration selects; "(N deselected)" counts the cases it deselects.
- ADRs / stories: approved (CANON) lore at each tag; lore approved after a tag appears with the next release.

## 3. Quality and safety (per release)

### demo

| Version | Tests passing / collected | Lint / format / types | Licence check | Red-team findings (resolved) | Audit rework rounds | Regressions caught | Known issues carried |
|---|---|---|---|---|---|---|---|
| v0.1.0 | not recorded | not recorded | not recorded | 3 (2) | not recorded | not recorded | 1 |
| v0.2.0 | not recorded | not recorded | not recorded | 1 (1) | not recorded | not recorded | not recorded |
| **Total** | not recorded | not recorded | not recorded | 4 (3) | not recorded | not recorded | not recorded |

## 4. Human in the loop (per release)

### demo

| Version | Questions to the human | Decisions (recommended / not) | Phase 3 gates | Lore approved / proposed |
|---|---|---|---|---|
| v0.1.0 | not recorded | not recorded | not recorded | 1 / 3 |
| v0.2.0 | not recorded | not recorded | not recorded | 2 / 1 |
| **Total** | not recorded | not recorded | not recorded | 3 / 4 |

- Decisions (recommended / not): queue rows raised with the decision intent. Not the same count as §1's Human decisions.

## Not recorded

- collection not run (--collect-cases) (cells: demo Headline Tests (functions / collected cases); demo v0.1.0 Unit; demo v0.1.0 E2E/acceptance; demo v0.2.0 Unit; demo v0.2.0 Integration; demo v0.2.0 E2E/acceptance; demo Total Unit; demo Total Integration; demo Total E2E/acceptance)
- no queue source given (cells: demo Headline Human decisions; demo v0.1.0 Questions to the human; demo v0.1.0 Decisions (recommended / not); demo v0.2.0 Questions to the human; demo v0.2.0 Decisions (recommended / not); demo Total Questions to the human; demo Total Decisions (recommended / not))
- no tests/integration at the tag (cells: demo v0.1.0 Integration)
- v0.1.0's slice records predate HACF's audit and replan records (no escape_tracking: v1 marker; HACF v0.6.4) (cells: demo v0.1.0 Slices (replans); demo v0.1.0 First-pass audit %; demo v0.1.0 Audit rework rounds)
- no telemetry source given (cells: demo v0.1.0 Tokens / cost; demo v0.2.0 Tokens / cost; demo Total Tokens / cost)
- v0.2.0's slice records predate HACF's audit and replan records (no escape_tracking: v1 marker; HACF v0.6.4) (cells: demo v0.2.0 Slices (replans); demo v0.2.0 First-pass audit %; demo v0.2.0 Audit rework rounds)
- not recorded for v0.1.0, v0.2.0 (cells: demo Total Slices (replans); demo Total First-pass audit %; demo Total Audit rework rounds)
- no test-result record (HACF's pre-push hook records none) (cells: demo v0.1.0 Tests passing / collected; demo v0.2.0 Tests passing / collected; demo Total Tests passing / collected)
- no per-release record exists (cells: demo v0.1.0 Lint / format / types; demo v0.1.0 Licence check; demo v0.2.0 Lint / format / types; demo v0.2.0 Licence check; demo Total Lint / format / types; demo Total Licence check)
- the template defines no source (cells: demo v0.1.0 Regressions caught; demo v0.2.0 Regressions caught; demo Total Regressions caught)
- no Known issues heading in CHANGELOG.md [0.2.0] (cells: demo v0.2.0 Known issues carried)
- not recorded for v0.2.0 (cells: demo Total Known issues carried)
- no Phase 3 gate record given (cells: demo v0.1.0 Phase 3 gates; demo v0.2.0 Phase 3 gates; demo Total Phase 3 gates)

## Sources

- git tags vX.Y.Z
- committer times: the earliest root commit to the latest tag
- sum over the releases
- git grep for test functions at the latest tag; cases: collection not run (--collect-cases)
- committer date of the tag's commit (UTC)
- committer times: previous release (or root commit) to the tag
- git rev-list --count between release tags
- git diff --numstat between release tags under src
- git diff --numstat between release tags under tests
- git grep for test functions under tests/unit at the tag; cases: collection not run (--collect-cases); by_marker: collection not run
- git grep for test functions under tests/e2e, tests/acceptance at the tag; cases: collection not run (--collect-cases); by_marker: collection not run
- CHANGELOG.md [0.1.0] '### Added' bullets at the tag
- slice table in missions/release-0.1.0/PROGRESS.md; git: HACF-Replan-Of trailers and 'resolve: replan' subjects
- each file's Status line at the tag (no lore graph)
- git grep for test functions under tests/integration at the tag; cases: collection not run (--collect-cases); by_marker: collection not run
- CHANGELOG.md [0.2.0] '### Added' bullets at the tag
- slice table in missions/release-0.2.0/PROGRESS.md; git: HACF-Replan-Of trailers and 'resolve: replan' subjects
- the first and latest release dates
- unique F-ids in the release's spec/archive RedTeam_Report.md; resolved: those also in the folder's SuperPRD.md
- CHANGELOG.md [0.1.0] '### Known issues' bullets at the tag
- each file's Status line at the tag (no lore graph); against the previous release
