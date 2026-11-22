---
name: grill-docs
description: Relentless domain-sharpening session that challenges your plan against the existing domain model, enforces precise terminology, updates CONTEXT.md inline and proposes ADRs into lore as decisions crystallise. Use when stress-testing a plan against the project's language and documented decisions.
trigger: /hyper-grill-docs
---

# Hyper-Grill-Docs

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing.

If a question can be answered by exploring the codebase, explore the codebase instead.

---

## Domain Awareness

During codebase exploration, also look for existing documentation.

### File Structure

Most repos have a single context:

```
/
├── CONTEXT.md
├── lore/
│   ├── MAP.md
│   └── ADRs/
│       ├── ADR-0001-event-sourced-orders.md
│       └── ADR-0002-postgres-for-write-model.md
└── src/
```

If a `CONTEXT-MAP.md` exists at the root, the repo has multiple contexts. The map points to where each one lives. Decisions stay in the one lore store; `--context <name>` tags a context-specific one:

```
/
├── CONTEXT-MAP.md
├── lore/
│   └── ADRs/                         ← every decision, system-wide and per context
├── src/
│   ├── ordering/
│   │   └── CONTEXT.md
│   └── billing/
│       └── CONTEXT.md
```

Create `CONTEXT.md` lazily — only when you have something to write. If none exists, create one when the first term is resolved. Never create files under `lore/` yourself; `hyper_lore.py propose` writes them. Existing `docs/adr/` records are imported with `python3 .agents/scripts/hyper_lore.py import --adr-dirs <dir>` or by the migration, never written.

---

## During the Session

### Challenge Against the Glossary

When the user uses a term that conflicts with the existing language in `CONTEXT.md`, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y — which is it?"

### Sharpen Fuzzy Language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account' — do you mean the Customer or the User? Those are different things."

### Discuss Concrete Scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-Reference with Code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible — which is right?"

### Update CONTEXT.md Inline

When a term is resolved, update `CONTEXT.md` right there. Don't batch these up — capture them as they happen. Use the format in `.agents/skills/hyper-grill-docs/CONTEXT-FORMAT.md`.

`CONTEXT.md` should be totally devoid of implementation details. Do not treat `CONTEXT.md` as a spec, a scratch pad, or a repository for implementation decisions. It is a glossary and nothing else.

### Offer ADRs Sparingly

Only offer to create an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip the ADR. When the user accepts one, propose it into lore:

1. Write the body in the format of `.agents/skills/hyper-grill-docs/ADR-FORMAT.md` with the Write tool to a temporary file outside `lore/`.
2. Run `python3 .agents/scripts/hyper_lore.py propose --type decision --title '<t>' --body-file <f>`, adding `--anchors-components <ids>`, `--anchors-files <paths>` or `--context <name>` where known. It allocates the id and writes the body to `lore/ADRs/`. The node starts PROPOSED; only the gate (/hyper-lore-approve) makes it CANON.