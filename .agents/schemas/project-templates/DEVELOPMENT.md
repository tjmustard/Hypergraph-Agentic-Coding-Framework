# Development Notes

{{PROJECT_NAME}} is built to handle specific workflows safely and efficiently. This document is a starting point for developers modifying the project, providing a compact orientation to the key project documents and repository organization.

**Essential project documents:**

| Document | Role |
| --- | --- |
| [README.md](README.md) | Primary user-facing overview of the tool and installation. |
| [DEVELOPMENT.md](DEVELOPMENT.md) | This document. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution process, testing requirements, and AI disclosure rules. |

**Main repository components:**

| Directory | Role |
| --- | --- |
| `src/` | Core logic and application code. |
| `tests/` | Pytest fixtures and integration tests. |
| `scratch/` | Utility scripts and manual verification tools. |
| `spec/` | The HACF workbench: `active/` drafts and `compiled/architecture.yml` (the component ledger). Specs live in the root `SPEC.md` and `missions/`, decisions in `lore/`. |
| `docs/` | User-facing documentation and tutorials. |

**Development Workflow:**

This project utilizes `uv` as its strict package manager. 
- Use `uv add <package>` to modify dependencies.
- Use `uv run --locked pytest` to execute the test suite.
