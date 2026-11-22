# Development Notes

Hypergraph Coding Agent Framework is built to handle specific workflows safely and efficiently. This document is a starting point for developers modifying the project, providing a compact orientation to the key project documents and repository organization.

**Essential project documents:**

| Document | Role |
| --- | --- |
| [README.md](README.md) | Primary user-facing overview of the tool and installation. |
| [DEVELOPMENT.md](DEVELOPMENT.md) | This document. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution process, testing requirements, and AI disclosure rules. |

**Main repository components:**

| Directory | Role |
| --- | --- |
| `.agents/` | Core framework logic including skills, schemas, rules, and scripts. |
| `dev/tests/` | The framework's pytest suite (`testpaths` in `pyproject.toml`). Never shipped to installed projects. |
| `dev/scripts/` | `check.sh` (the full check) and the release tooling. |
| `dev/design/` | Design notes and decisions. |
| `tests/` | `fixtures/` (verified baselines) and `candidate_outputs/` (unverified outputs a person reviews). |
| `docs/` | User-facing documentation and tutorials. |
| `spec/` | The HACF workbench: `active/` drafts, `compiled/architecture.yml` (the component ledger), `archive/`. |

**Development Workflow:**

This project uses `uv` as its strict package manager, and `python3` in every command.
- Use `uv add <package>` to modify dependencies.
- Use `uv run --locked pytest` to run the suite in `dev/tests/`.
- Before you commit, run the full check:
  ```bash
  bash dev/scripts/check.sh
  ```
  It runs `dev/tests/` under both interpreters: the uv-managed venv and a Python 3.10 (`$HACF_PY310`, default `/usr/bin/python3`). The 3.10 leg fails, never skips, when that interpreter cannot import pytest and PyYAML. It then runs ruff on the Python files changed since the merge-base with `main`. Live tests (marker `live`) never run here.

**Git hooks:** the `pre-commit` hook blocks staged files under `spec/process/`, then runs `ruff check` and `ruff format --check` on the staged Python files only (added, copied or modified), and skips lint when `uv` is not installed. Never bypass a hook with `--no-verify`.
