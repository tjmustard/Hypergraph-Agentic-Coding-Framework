# ADR Format

ADRs are decision nodes of the lore graph. Their bodies live in `lore/ADRs/ADR-NNNN-<slug>.md`, and `hyper_lore.py` allocates the id: never number an ADR by hand.

## Proposing

Agents only propose:

1. Write the body (template below) with the Write tool to a temporary file outside `lore/`.
2. Run `python3 .agents/scripts/hyper_lore.py propose --type decision --title '<short title>' --body-file <file>`, adding `--anchors-components <ids>`, `--anchors-files <paths>` or `--anchors-symbols <names>` where known. The node starts PROPOSED.

CANON, REJECTED, RETIRED and SUPERSEDED come only through the gate (/hyper-lore-approve), with the user's warrant. To replace a decision, propose the new one with `--supersedes <ID>`; the gate applies it.

Existing `docs/adr/` records are imported with `python3 .agents/scripts/hyper_lore.py import --adr-dirs <dir>` or by the migration, never written.

## Template

```md
# {Short title of the decision}

{1-3 sentences: what's the context, what did we decide, and why.}
```

That's it. An ADR can be a single paragraph. The value is in recording *that* a decision was made and *why* — not in filling out sections.

## Optional Sections

Only include these when they add genuine value. Most ADRs won't need them. Status lives in the lore graph, not in the body.

- **Considered Options** — only when the rejected alternatives are worth remembering
- **Consequences** — only when non-obvious downstream effects need to be called out

## When to Offer an ADR

All three of these must be true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If a decision is easy to reverse, skip it — you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing."

### What Qualifies

- **Architectural shape.** "We're using a monorepo." "The write model is event-sourced, the read model is projected into Postgres."
- **Integration patterns between contexts.** "Ordering and Billing communicate via domain events, not synchronous HTTP."
- **Technology choices that carry lock-in.** Database, message bus, auth provider, deployment target. Not every library — just the ones that would take a quarter to swap out.
- **Boundary and scope decisions.** "Customer data is owned by the Customer context; other contexts reference it by ID only." The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path.** "We're using manual SQL instead of an ORM because X." Anything where a reasonable reader would assume the opposite. These stop the next engineer from "fixing" something that was deliberate.
- **Constraints not visible in the code.** "We can't use AWS because of compliance requirements." "Response times must be under 200ms because of the partner API contract."
- **Rejected alternatives when the rejection is non-obvious.** If you considered GraphQL and picked REST for subtle reasons, record it — otherwise someone will suggest GraphQL again in six months.
