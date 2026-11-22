---
name: execute
description: Implements a v6 slice SPEC.md or a legacy flat MiniPRD precisely and in full, following existing code patterns and flagging the ledger after every modification. Use when ready to write code against a compiled spec.
---

# Execute

This skill implements a spec precisely, minimally, and in full, adhering to existing conventions and keeping the component ledger in sync.

## When to use this skill

- When the user has a compiled spec and is ready to build: a v6 slice `SPEC.md` (`missions/<mission>/slices/<NN-slug>/SPEC.md`, by path or dot-id) or a legacy flat `spec/compiled/MiniPRD_*.md`.
- When the user explicitly runs `/hyper-execute` or asks to "implement the plan."

## Rules

- A `## Pipeline Operating Notes` section in the spec overrides this skill where they differ.
- Call every script with `python3`.
- Ledger writes go through `hyper_ledger.py` and `hypergraph_updater.py` only; never edit the ledger by hand.
- The tracking document is the spec's own task checkboxes; create no progress file.
- `python3 .agents/scripts/hyper_gates.py mode` prints `autonomous` only under `HACF_AUTONOMOUS=1`. In that mode nobody can answer a question, so follow each step's `Autonomous:` line, and the scripts send ledger, lore and progress writes to `$HACF_OUTBOX` on their own.
- Placeholders: `<spec>` is the path `resolve` reports (`resolve` itself takes the user's path or dot-id). `<SID>` is the slice id; for a legacy MiniPRD it is the `slice:` of its `hacf-bootstrap` block, else its file name without `.md`.
- Fenced blocks tagged `legacy` hold the commands of the legacy branch, including those both layouts share. Blocks tagged `v6` hold the commands only a slice runs.

## How to use it

1. **Resolve the spec**
   - Resolve the input and record `<base>`:

     ```bash legacy
     python3 .agents/scripts/hyper_gates.py mode
     python3 .agents/scripts/hyper_spec.py resolve <spec> --json
     git rev-parse HEAD
     ```

   - `layout` in the output is `slice` for v6 or `legacy`. Follow that branch below. `<base>` is the commit `git rev-parse HEAD` printed.
   - If `resolve` fails, report the error and stop.
   - Autonomous: run `python3 .agents/scripts/hyper_gates.py record --gate missing_input --slice <SID> --reason '<why>'` and stop.

2. **Memory Check**
   - Read `.agents/memory/activeContext.md` and the `hacf_status` that `resolve` reported (`null` for a legacy MiniPRD).
   - If `hacf_status` is `executed` or `audited`, or `activeContext.md` lists the spec as complete or audited, use **AskUserQuestion**: "[spec] appears already complete. Re-implement it?" with the options "Yes, re-implement" and "No, stop (run /hyper-audit or review it instead)".
   - Autonomous: when `HACF_ALLOW_REIMPLEMENT=1` is set, continue, because the engine is retrying with a punch list. Otherwise run `python3 .agents/scripts/hyper_gates.py record --gate already_complete --slice <SID> --reason '<why>'` and stop.

3. **Confidence Gate**
   - Read the spec and output a Confidence Score (1-10) against its Confidence Mandate. At 9 or higher, continue.
   - Below 9, list the clarifying questions, then use **AskUserQuestion** with the options "Answer my questions", "Proceed anyway (accept the risk)" and "Stop until the spec is revised".
   - Autonomous: low confidence is never accepted. Run `python3 .agents/scripts/hyper_gates.py record --gate low_confidence --slice <SID> --reason '<score and questions>'` and stop.

4. **Codebase Orientation (Haiku Sub-Agent)**
   - Launch a read-only sub-agent with the Agent tool (`subagent_type: "general-purpose"`, `model: "haiku"`). Prompt it: "Read `[spec path]` in full. From its task list, identify every file path that must be created or modified. For each file that already exists, read its full content and return it verbatim. For new files, list the path and expected purpose only. Return a structured report mapping file paths to their current content."
   - Use the returned contents as your working context, and re-read a file only to verify a specific line.
   - **Fallback:** if the sub-agent returns an error or an empty result, read the files directly.

5. **Implement Precisely**
   - Write elegant, minimal, modular code that follows the patterns of the surrounding code, with comments where logic is non-obvious.
   - Check each task's box in the spec as the task finishes.

6. **Register the Node**
   - Create or update the spec's ledger node. A v6 spec gives it in its frontmatter, and a legacy MiniPRD in its `**Hypergraph Node ID:**` and `**Parent Node:**` lines:

     ```bash legacy
     python3 .agents/scripts/hyper_ledger.py ensure-node --from-spec <spec>
     ```

   - When the node is new, `ensure-node` also needs `--dimension Module` (or another dimension the spec names).

7. **Flag the Blast Radius (MANDATORY)**
   - v6: mark the nodes the diff touches. This form also reports `unmapped`, the changed paths no node covers:

     ```bash v6
     python3 .agents/scripts/hypergraph_updater.py --from-diff <base> --apply --slice <SID> --json
     ```

   - Legacy: `<node_ids>` is the space-separated node ids of the files you changed:

     ```bash legacy
     python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <node_ids>
     ```

   - A non-empty `unmapped` list goes in the summary of step 10.
   - The audit depends on these flags, so skipping this step corrupts the state machine.

8. **Record Progress**
   - v6: `<slice dir>` is the directory holding the slice's `SPEC.md`, not a dot-id:

     ```bash v6
     python3 .agents/scripts/hyper_spec.py progress --slice <slice dir> --event executed
     ```

   - Legacy: there is no slice directory, so the same command only prints a skip notice:

     ```bash legacy
     python3 .agents/scripts/hyper_spec.py progress --slice <spec> --event executed
     ```

9. **Write Field Notes**
   - Write each non-obvious discovery as one line, and never to `spec/process/`.
   - v6: `<mission dir>` is the directory that holds `slices/`:

     ```bash v6
     python3 .agents/scripts/hyper_lore.py note --mission <mission dir> --agent hyper-execute --slice <SID> --text '<note>'
     ```

   - Legacy:

     ```bash legacy
     python3 .agents/scripts/hyper_lore.py note --legacy --source <spec> --agent hyper-execute --text '<note>'
     ```

10. **Halt and Report**
    - Output the files changed, the nodes flagged and any `unmapped` paths.
    - Tell the user: "Implementation complete. Start a new conversation and run `/hyper-audit <spec>` to verify the code against the contract."
    - Autonomous: write the same summary with the Write tool to `$HACF_OUTBOX/execute.json` as `{"slice": "<SID>", "files_changed": [...], "nodes_flagged": [...], "unmapped": [...]}`, then stop.
