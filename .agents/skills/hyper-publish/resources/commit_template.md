# Commit Message Template

Use this as a reference when editing a commit message manually in `/hyper-publish`.

---

## Format

```
<imperative verb> <what>: <brief context or reason>

<Optional body — 1-3 sentences explaining WHY, not what>
<Reference any related issues, PRs, or decisions>

<Optional trailers: Fixes #123, Related: #456>
Agentic-Assistant: OpenRig (Claude Code harness) · model <model-id>
Agentic-Framework: HACF v<version> (<mode>)
```

An agent-made commit ends with exactly these two footer lines, and never a Claude co-author trailer or
session-link line; any other trailer (`Fixes #123`, `HACF-Slice:`) comes before the two lines.
`<version>` is the `version` field of `.agents/hacf-manifest.json`, and `<mode>` is `OpenRig` for a seat
of an OpenRig HACF team or `manual` for HACF skills used by hand; leave a line off when its value is unknown.

---

## Guidelines

### Subject Line (required)
- **Imperative mood**: "Add", "Fix", "Update", "Release" — as if giving a command
- **No period** at the end
- **≤72 characters** (aim for ≤50 for best GitHub display)
- **Specific**: Say what changed, not "fix bug" but "fix authentication timeout in login flow"

### Body (optional but recommended for significant changes)
- Explain **WHY** the change was made, not what was changed (the diff shows that)
- Keep to 1-3 sentences
- Wrap at 72 chars
- Blank line between subject and body

### Footer
- Issue reference (optional): `Fixes #123` or `Related: #456`, before the two lines
- Agent-made commit (required): the two footer lines above, last; never a Claude co-author trailer

---

## Examples

### Release
```
Release v0.4.1: Framework upgrades with /hyper-update and /hyper-recover
```

### Feature
```
Add hyper-publish skill for timestamp-safe git publishing

Implements post-session IP protection by updating file mtimes
before commit, ensuring the repository history reflects when
you published, not when the model wrote.

Agentic-Assistant: OpenRig (Claude Code harness) · model <model-id>
Agentic-Framework: HACF v<version> (<mode>)
```

### Simple fix
```
Fix typo in README.md
```

### Update with context
```
Update CHANGELOG to reflect recent bug fixes

These fixes were discovered during integration testing and
must be documented before the next release.

Agentic-Assistant: OpenRig (Claude Code harness) · model <model-id>
Agentic-Framework: HACF v<version> (<mode>)
```

---

## Anti-patterns

❌ "fix stuff"  
❌ "WIP"  
❌ "asdf" (placeholder)  
❌ All caps or run-on sentences  
❌ Commits without verbs ("The new feature")  

✅ "Fix race condition in token counter"  
✅ "Add streaming support to API responses"  
✅ "Release v0.5.0: Performance optimizations"
