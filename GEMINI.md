# Gemini CLI Integration Guide

> **For Human Developers:** See `README.md` for the complete usage guide and setup
> instructions.

---

## For Gemini CLI: System Mandates

Read `AGENTS.md` for the full framework system mandates that apply to all IDEs. Its first line is
the trailhead: start at `lore/MAP.md`, then `SPEC.md`, then the active mission under `missions/`.
AGENTS.md also lists the lore nodes that bind you and the stated limits of the agent firewall.

**Gemini CLI-specific overrides and additions:**

### v0.6.0 Notes
- Run every framework script with `python3`, never a bare `python`.
- Gemini CLI has no PreToolUse gate hook, so a gated lore verb (approve, reject, retire, reopen,
  review) asks for a confirmation in the terminal: the user types the node id. Without a terminal
  it is refused.
- `.geminiignore` is rendered from `.agentignore` as a context filter, not a security boundary.

### Tool Names
When skills say "read/write/run/edit a file," use these Gemini CLI tools:

| Action | Tool |
|---|---|
| Read a file | `read_file` tool |
| Write a file | `write_file` tool |
| Run a shell command | `run_shell_command` tool |
| Ask the user a question | `ask_user` tool |
| List directory contents | `list_directory` tool |

### Skill Invocation
Activate skills via the `activate_skill` tool, using the skill names defined in
`.agents/skills/`. Each skill directory contains a `SKILL.md` with full instructions.

Available skills: `hyper-architect`, `hyper-audit`, `hyper-baseline`, `hyper-clear`,
`hyper-co-research`, `hyper-consult-cto`, `hyper-contextualize`, `hyper-create-issue`,
`hyper-create-skill`, `hyper-deepdive`, `hyper-discover`, `hyper-document`, `hyper-execute`,
`hyper-grill-docs`, `hyper-handoff`, `hyper-init`, `hyper-learning-opportunity`,
`hyper-lore-approve`, `hyper-new-workflow`, `hyper-peer-review`, `hyper-process-document`,
`hyper-prompt-engineer`, `hyper-publish`, `hyper-redteam`, `hyper-refresh-memory`, `hyper-resolve`,
`hyper-session-update`, `hyper-sop`, `hyper-status`, `hyper-stitch-design`,
`hyper-template-architect`, `hyper-troubleshooting`, `hyper-tutorial-audit`,
`hyper-tutorial-generator`, `hyper-tutorial-run`, `hyper-update`

### Safe Sandboxing & Automation
When instructed to write experimental code, test tools, or execute destructive scripts, you must **always checkout a branch prefixed with `research/` or `swimlane/`**.

These prefixes act as an automatic local sandbox. They are intercepted by a repository `pre-push` hook, physically preventing you from accidentally pushing unstable code or broken logic to the remote server. Never execute large automated changes directly on `main` or a feature branch.

---

## Schema Definitions & Migration Notice

**Note:** All schema definitions (SuperPRD, MiniPRD, architecture.yml) and migration rules have been centralized in `AGENTS.md` to ensure a single source of truth across all agent IDE integrations. Please refer to `AGENTS.md` for these persistent rules.
