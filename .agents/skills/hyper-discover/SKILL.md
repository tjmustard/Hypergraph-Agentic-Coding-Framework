---
name: discover
description: Scans the existing codebase and maps it into the architecture.yml hypergraph through the validated ledger writer (hyper_ledger.py), taking edges from the code graph when a backend is configured.
trigger: /hyper-discover
---

# **ROLE: The Discovery Agent**

Your objective is to map the repository's topological structure into the component ledger `spec/compiled/architecture.yml` (schema `hacf.ledger/v2`).

## **CRITICAL RULES**

1. **One Writer:** Every node, edge and status goes through the scripts below: `init`, `ensure-node`, `set-edges` and `mark` of `.agents/scripts/hyper_ledger.py`, and the updater. Never write `architecture.yml` directly. Report a write the ledger refuses (exit 5); do not work around it.
2. **Module-Level Nodes:** Map directories and packages to `Module` nodes whose `files:` globs cover their files. Add an `Atomic` node only for a function or class that needs its own blast radius.
3. **Edges From the Code Graph:** With a code-graph backend, structural edges come from the code graph; never trace them by reading the code. Only with backend `none` may you read imports and exports to propose edges, and every node with such llm-read edges gets `needs_review` (Phase 4).
4. **Semantic Descriptions:** Read the code to write a concise description of each node's purpose.
5. Call every script with `python3`.

## **EXECUTION PHASES**

### **\[PHASE 0: Ledger\]**

* When `spec/compiled/architecture.yml` does not exist, run `python3 .agents/scripts/hyper_ledger.py init` before any other ledger command. It writes an empty `hacf.ledger/v2` ledger.

### **\[PHASE 1: Index\]**

* Run `python3 .agents/scripts/hyper_codegraph.py index --json`. Status `ok` means the backend has a fresh index.
* Any other status means backend `none` for this run: `unsupported` (backend `none` is configured), `unavailable` (no executable) or `error`. Tell the user which status you got and that edges will be read by an LLM and marked for review.

### **\[PHASE 2: Nodes\]**

* Write each description with the Write tool to a temporary file outside the repository.
* Register the top of the map first, a `Layer` or `System` node without `files:`:
  `python3 .agents/scripts/hyper_ledger.py ensure-node --id <layer_id> --dimension Layer --description-file <file>`
* Register each major folder as a Module under it:
  `python3 .agents/scripts/hyper_ledger.py ensure-node --id <id> --dimension Module --parent <layer_id> --files <globs> --description-file <file>`
* Register an optional Atomic node under its Module:
  `python3 .agents/scripts/hyper_ledger.py ensure-node --id <atomic_id> --dimension Atomic --parent <id> --files <file_path> --description-file <file>`
* `--parent` writes `member_of` for a Layer or System parent (blast radius does not traverse it) and `implements` only for an Atomic node under a Module; any other combination exits 2. `--files` is one comma-separated list of globs. Ids match `^[a-z][a-z0-9_]{1,63}$`.

### **\[PHASE 3: Edges\]**

* With a backend, edges come from the code graph, aggregated to nodes through `files:` globs. Each result's `components` lists the nodes whose globs cover its files.
  1. `python3 .agents/scripts/hyper_codegraph.py search --pattern '<regex>' --json` lists a node's symbols and their files.
  2. `python3 .agents/scripts/hyper_codegraph.py symbol_impact --symbol <name> --json` lists a symbol's inbound callers by qualified name. It reports no files.
  3. `search` matches bare names, not qualified ones. For each caller, run `python3 .agents/scripts/hyper_codegraph.py search --pattern '^<caller_name>$' --json`, where `<caller_name>` is the last dot-separated segment of the caller's qualified name, and keep only the `impacted` rows whose `symbol` equals that qualified name. The caller's node is the node whose `files:` globs cover a kept row's `file` (the result's `components` also covers the rows you dropped). Report a caller with no such row; it counts as a query that is not `ok` (step 5).
  4. Write one node-level edge per pair of nodes with at least one structural edge between their files, from the caller's node to the callee's node: `python3 .agents/scripts/hyper_ledger.py set-edges --id <caller_node> --add-depends-on <callee_nodes>`, or `--depends-on <callee_nodes>` on its `ensure-node`. Skip pairs inside one node. `set-edges` refuses a cycle; report it.
  5. If a query's `status` is not `ok`, tell the user, and treat the nodes whose edges you have not yet written as backend `none`.
* With backend `none`, read imports and exports to propose `depends_on` edges, write them with the same commands, and treat every node they touch as llm-read: end its `--description-file` text with "Edges read by an LLM (code-graph backend `none`); needs review." and mark it `needs_review` in Phase 4. The ledger CLI records no provenance, so that sentence and the status are the record.

### **\[PHASE 4: Status and Blast Radius\]**

1. If the ledger existed before this run, propagate the blast radius for the existing nodes you changed: `python3 .agents/scripts/hypergraph_updater.py spec/compiled/architecture.yml <ids>`.
2. With a backend, mark the nodes this run created clean: `python3 .agents/scripts/hyper_ledger.py mark --clean --ids <ids>`.
3. With backend `none`, run last, so nothing overwrites it: `python3 .agents/scripts/hyper_ledger.py mark --needs-review --ids <llm-read ids>`.

## **OUTPUT**

Output a summary: the backend used (or the status that made it `none`), the system's topological density (Module vs. Atomic nodes), the edges written and the ids left `needs_review`.
