# v0.6.0 Evidence

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

<a id="codebase-evidence"></a>
## Codebase evidence (verified 2026-10-20)

- `spec/compiled/architecture.yml` did not parse: PyYAML error at :314 (` ###` inside a plain scalar); also :459 and :522. Repaired once in bootstrap commit C2 (7 edits).
- Ledger quality: ~6 of 31 depends_on edges match real imports/subprocess calls; 17 express orchestration_layer membership; 2 dangled; the one implements edge was inverted; 32/52 inputs and 49/61 outputs are labels, not node ids.
- `/hyper-discover` builds the ledger by LLM reading (`.agents/skills/hyper-discover/SKILL.md:13-30`); no HACF code emits nodes.
- Engine: `hyper_daemon.py:343-359` calls `hyper_execute_runner.py` / `hyper_audit_runner.py`, which do not exist; `hyper_orchestrator.py:579-611` discards the API response text; `:570`/`:677` run `git checkout` from 8 threads in one working tree; `:40-46` model allowlist is stale; `:4-5` requires Python 3.11 while `/usr/bin/python3` is 3.10.12.
- `.agentignore` is read by no tool; `.claude/settings.json` has no permissions.deny rules.
- `hyper_update_core.py:280-302` rmtree+copytree of spec/, tests/, .agents/skills/, .agents/scripts/, .agents/schemas/ with no backup; unreachable today only because `verify_gpg_signature` (:142-179) always fails on unsigned release commits.
- `/hyper-resolve` and `/hyper-baseline` both write `spec/compiled/SuperPRD.md` (`hyper-resolve/SKILL.md:36`, `hyper-baseline/SKILL.md:18,25`).
- Installer: bare `read -p` would hit EOF under `curl | bash -s -- -y` with `set -euo pipefail`; `prompt_yn` returns YES under AUTO_YES (`HACF-install.sh:128-141`); `.agents/config/` is wholesale-overwritten on upgrade (`install_system_dirs`).
- `/hyper-grill-docs` already writes `docs/adr/` with proposed/accepted/deprecated/superseded (`.agents/skills/hyper-grill-docs/ADR-FORMAT.md`).
- Final Truth: `compaction_engine.py` computes but never writes; nothing invokes it or `interrupt_detector.py`.
- `spec/process/` is commit-blocked by `.agents/scripts/pre-commit`; `spec/handoffs/` is never written.
- Repo lint debt: `ruff check --no-cache` reports 406 errors; 30 files would be reformatted.

<a id="external-sources"></a>
## External sources (primary, verified 2026-10-20)

### Code-graph tools

**codebase-memory-mcp** — https://github.com/DeusData/codebase-memory-mcp

- Pack-claims check: start_command "npx -y @deusdata/codebase-memory-mcp": FALSE. registry.npmjs.org/@deusdata/codebase-memory-mcp returns 404. The real package is unscoped, so the valid form is `npx -y codebase-memory-mcp` (or uvx, or the native binary). It is also a stdio server the MCP client must spawn, not a shell-launched daemon. | capability "ast": TRUE (tree-sitter). | capability "hybrid-lsp": TRUE only as the project's own label, and MISLEADING: no LSP servers are used, just built-in C type resolution for about 11 languages. | capability "cypher-query": TRUE (partial). `query_graph` accepts a read-only openCypher subset. | mcp_tools.get_symbol -> "cbm_get_symbol": FALSE. It is not in the TOOLS[] array in src/mcp/mcp.c; the cbm_ prefix appears only on internal C functions such as cbm_mcp_server_new. The real equivalent is search_graph(project, name_pattern|qn_pattern|query) followed by get_code_snippet(qualified_name, project). | mcp_tools.blast_radius -> "cbm_blast_radius": FALSE. The real equivalents are detect_changes (diff-based) and trace_path direction=inbound (symbol-based). | cli_fallback `sqlite3 .cbm/graph.db "SELECT * FROM nodes WHERE name LIKE '%{target}%';"`: PARTLY FALSE. The path is wrong; the real DB is ~/.cache/codebase-memory-mcp/<project>.db. A `nodes` table with a `name` column does exist, so the SQL itself would run against the right file. The instruction text calls it a "cypher query" but it is SQL. It also couples to an undocumented internal schema, needs a sqlite3 binary, and substitutes {target} unescaped into shell and SQL. The supported fallback is `codebase-memory-mcp cli search_graph --project <p> --name-pattern '<re>' --format json`. | type "hybrid-mcp-cli": TRUE (MCP plus the `cli` subcommand). | requires_build_pass: false: MISLEADING. No compile is needed, but an index pass is (auto_index is off by default). | Installer text "C/SQLite, Hybrid LSP, 3D UI": C TRUE. SQLite TRUE. Hybrid LSP TRUE only as a label (see above). 3D UI TRUE: CBM Lens is in open beta v0.0.1 with Galaxy and Architecture 3D views served on loopback. Release assets still ship separate `-ui-` tarballs, although the README says the standard release includes Lens.
- Sources:
  - https://github.com/DeusData/codebase-memory-mcp
  - https://api.github.com/repos/DeusData/codebase-memory-mcp
  - https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c
  - https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.h
  - https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/store/store.c
  - https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/server.json
  - https://registry.npmjs.org/codebase-memory-mcp
  - https://registry.npmjs.org/@deusdata/codebase-memory-mcp
  - https://pypi.org/pypi/codebase-memory-mcp/json
  - https://api.github.com/repos/DeusData/codebase-memory-mcp/releases

**code-review-graph** — https://github.com/tirth8205/code-review-graph

- Pack-claims check: start_command "npx -y @tirth8205/code-review-graph serve": FALSE. No npm package exists (404); it is a Python/PyPI tool, so the real command is `uvx code-review-graph serve` or `code-review-graph serve`. The `serve` subcommand itself is TRUE. | mcp_tools.get_symbol -> "crg_find_symbol": FALSE. No tool has a crg_ prefix (verified in code_review_graph/main.py). The closest are semantic_search_nodes_tool(query, kind), which searches "by name, keyword, or semantic similarity" and falls back to FTS, and query_graph_tool. | mcp_tools.blast_radius -> "crg_detect_changes_brief": FALSE. The real tools are detect_changes_tool (detail_level='minimal' is the nearest thing to brief) or get_impact_radius_tool. | cli_fallback `npx @tirth8205/code-review-graph detect-changes --brief`: PARTIAL. `detect-changes --brief` is a real subcommand and flag, but the npx launcher is FALSE. The command also takes no {target}; it diffs HEAD~1 or --base, so it cannot answer a symbol-targeted query. | Installer text "Incremental SQLite": TRUE. | "Web UI": MOSTLY FALSE. There is no served web UI. `visualize` writes a static interactive D3 HTML file plus exports, a VS Code extension folder exists, and `serve --http` is an MCP endpoint, not a UI. | "good for PRs": TRUE. It is built for code review: detect-changes --base mirrors a PR's file set, and it ships a GitHub Action and review/pre_merge_check MCP prompts. | requires_build_pass: false: FALSE in practice. An initial `code-review-graph build` (or build_or_update_graph_tool) is needed, and `install` doesn't run it. | type "hybrid-mcp-cli": TRUE.
- Sources:
  - https://github.com/tirth8205/code-review-graph
  - https://api.github.com/repos/tirth8205/code-review-graph
  - https://raw.githubusercontent.com/tirth8205/code-review-graph/staging/code_review_graph/main.py
  - https://raw.githubusercontent.com/tirth8205/code-review-graph/staging/code_review_graph/skills.py
  - https://raw.githubusercontent.com/tirth8205/code-review-graph/staging/code_review_graph/incremental.py
  - https://raw.githubusercontent.com/tirth8205/code-review-graph/staging/docs/USAGE.md
  - https://pypi.org/pypi/code-review-graph/json
  - https://registry.npmjs.org/@tirth8205/code-review-graph
  - https://registry.npmjs.org/code-review-graph
  - https://api.github.com/repos/tirth8205/code-review-graph/releases

**codegraph (assumed colbymchenry/codegraph; the name is ambiguous)** — https://github.com/colbymchenry/codegraph

- Pack-claims check: The pack names "codegraph" with no details, so which project it means is UNVERIFIABLE. MiniPRD Task 1 requires mcp_tools and cli_fallback entries for it, but none are given. If it means colbymchenry/codegraph, the implied claim that it supports MCP plus a CLI fallback for get_symbol and blast_radius is TRUE: get_symbol maps to codegraph_search (MCP, hidden unless CODEGRAPH_MCP_TOOLS includes it) or `codegraph query <name> --json`, and blast_radius maps to codegraph_impact or `codegraph impact <symbol> --json`. The line-104 claim that it is a "pluggable FOSS backend via MCP or CLI" is TRUE (MIT, both interfaces). The pack's general rule that requires_build_pass is false would be wrong here too: `codegraph init` plus indexing is required per project.
- Sources:
  - https://github.com/colbymchenry/codegraph
  - https://api.github.com/repos/colbymchenry/codegraph
  - https://raw.githubusercontent.com/colbymchenry/codegraph/main/src/mcp/tools.ts
  - https://api.github.com/repos/colbymchenry/codegraph/contents/src/mcp
  - https://registry.npmjs.org/@colbymchenry/codegraph
  - https://api.github.com/repos/colbymchenry/codegraph/releases
  - https://github.com/Jakedismo/codegraph-rust
  - https://api.github.com/repos/Jakedismo/codegraph-rust
  - https://github.com/sunerpy/codegraph-rust
  - https://api.github.com/repos/optave/codegraph
  - https://registry.npmjs.org/@optave/codegraph
  - https://registry.npmjs.org/codegraph
  - https://pypi.org/pypi/codegraph/json

**understand-anything** — https://github.com/Egonex-AI/Understand-Anything (the original Lum1104/Understand-Anything now resolves here after a repo transfer)

- Pack-claims check: Named in MiniPRD Task 1 only. The implied claim that the YAML can define mcp_tools and cli_fallback for it is FALSE: it has no MCP server (no mcpServers or .mcp.json in the plugin) and no shell CLI. Its graph is built partly by LLM agents and its impact analysis is LLM grep, which conflicts with the pack's own rule that the code graph must "deterministically map symbols and blast radiuses". It is better treated as an optional onboarding and visualization plugin than as a facade backend.
- Sources:
  - https://github.com/Egonex-AI/Understand-Anything
  - https://api.github.com/repos/Egonex-AI/Understand-Anything
  - https://api.github.com/repos/Lum1104/Understand-Anything
  - https://raw.githubusercontent.com/Egonex-AI/Understand-Anything/main/understand-anything-plugin/.claude-plugin/plugin.json
  - https://api.github.com/repos/Egonex-AI/Understand-Anything/contents/understand-anything-plugin
  - https://raw.githubusercontent.com/Egonex-AI/Understand-Anything/main/understand-anything-plugin/skills/understand-diff/SKILL.md
  - https://api.github.com/repos/Egonex-AI/Understand-Anything/contents/understand-anything-plugin/packages/core/src/languages/configs
  - https://api.github.com/repos/Egonex-AI/Understand-Anything/releases

**graphify** — https://github.com/Graphify-Labs/graphify (the original safishamsi/graphify now resolves here after a repo transfer; default branch `v8`)

- Pack-claims check: Named on line 31 as an example FOSS backend ("Graphify, Codebase-Memory-MCP, etc."). That it exists and is FOSS is TRUE (Apache-2.0/MIT, though a commercial cloud exists). No mcp_tools or cli_fallback are given, so the details are UNVERIFIABLE as written. The implied claim that it fits the get_symbol and blast_radius facade is PARTIAL: get_symbol maps to get_node (MCP) or `graphify explain` (CLI), while symbol-level blast_radius exists only on the CLI (`graphify affected`); the MCP side offers only get_neighbors (1-hop) or PR-level get_pr_impact. It needs a build first (/graphify . or graphify extract/update), so requires_build_pass:false would also be wrong. Its strength in mixed corpora (docs, ADRs, PDFs) makes it more relevant to the pack's Lore Hypergraph than to code topology.
- Sources:
  - https://github.com/Graphify-Labs/graphify
  - https://api.github.com/repos/Graphify-Labs/graphify
  - https://api.github.com/repos/safishamsi/graphify
  - https://raw.githubusercontent.com/Graphify-Labs/graphify/v8/graphify/serve.py
  - https://raw.githubusercontent.com/Graphify-Labs/graphify/v8/graphify/cli.py
  - https://raw.githubusercontent.com/Graphify-Labs/graphify/v8/graphify/affected.py
  - https://pypi.org/pypi/graphifyy/json
  - https://docs.graphify.com/reference/mcp-tools
  - https://docs.graphify.com/mcp/overview
  - https://api.github.com/repos/Graphify-Labs/graphify/releases

### OpenRig

A real, actively developed open-source multi-agent harness: github.com/mvschwarz/openrig (site openrig.dev, npm @openrig/cli, CLI command `rig`), by Mike Schwarz (GitHub mvschwarz, company Esoteric Labs). README: 'A harness wraps a model. A rig wraps your harnesses.' and 'OpenRig is open-source software for building and running your own network of agents.' Repo description: 'Build your own network of agents from Claude Code, Codex and Pi: persistent teams with roles, shared context and owned work.' It is a local daemon, CLI, terminal UI and MCP server built on tmux and SQLite. It does not call models itself. It runs existing harness sessions (Claude Code, Codex, Pi) as a persistent team. Teams are declared in YAML (RigSpec: 'Pods, members, edges, continuity policies, culture file'). Each agent occupies a seat, 'A stable role and address in a rig, such as dev-build@starter', and seats are grouped into pods. Work moves via `rig send`, `rig queue`, broadcast and chatrooms, and `rig down --snapshot` / `rig up` save and restore state. On top of the runtime it ships (a) a project/work layer of missions and slices with SPEC.md, PROGRESS.md and PROOF.md, scaffolded by `rig scope`, and (b) a knowledge layer: chain files (LEARNED.md, CRAFT.md, CULTURE.md), per-seat lore, and an 'epistemic ladder' doc. So the pack is right that a 'rig' of agents in 'seats' exists, but OpenRig is mainly a runtime/orchestrator, not a file-format standard. Disambiguation: I found no other software project named OpenRig. Results were only coverage of this one plus a community Docker image (netinventors/openrig) that bundles it, and there is no PyPI package.

**Actual file topology.** PROJECT TREE (docs/reference/project-workspace.md, docs/reference/sdlc-conventions.md). Default root is `~/.openrig/workspace`, created by `rig config init-workspace` unless `--root`/`workspace.root` points elsewhere:
workspace/
  SPEC.md          project intent (project.yaml `install.intent: SPEC.md`)
  project.yaml     `schema: openrig.project/v0alpha1`, `kind: project`
  workspace.yaml   `schema: openrig.workspace/v0alpha1`, 'the project-location catalog'
  .gitignore       ignores exhaust/ and .openrig/
  missions/<mission-id>/
    SPEC.md  mission.yaml  PROGRESS.md  NOTES.md  [CAPABILITY-DELTA-v<version>.md, release missions only]
    slices/<slice-id>/
      SPEC.md  slice.yaml  PROGRESS.md ('the durable acceptance checklist')  PROOF.md  proof/
  exhaust/
Frontmatter: SPEC.md has `intent:` (required) and `depends_on:`. Slices have `stage:` (wip|provisional|established|canonical|superseded|retired) and `verified:`. mission.yaml has `metadata.status` (scaffolds write `planned`). Key line: 'the project-world metadata root is never assumed to be its code working directory.'

TOPOLOGY TREE (docs/reference/chain-file-convention.md, 'CE-v2'). The root comes from config (`rig config get topology.root`); $OPENRIG_HOME (normally ~/.openrig) contains topology/:
<topology.root>/<NAME>.md                          # instance
<topology.root>/rigs/<rig>/<NAME>.md               # rig
<topology.root>/rigs/<rig>/pods/<pod>/<NAME>.md    # pod (optional)
<topology.root>/rigs/<rig>/seats/<seat>/<NAME>.md  # seat
Chain-file names: LEARNED.md ('never shared or shipped as-is'), CRAFT.md (instance, rig, seat), ORCHESTRATION-CRAFT.md (rig only), CULTURE.md (delivered from the rig spec's culture_file).

LORE (docs/reference/lore-routing.md): `<topology-root>/rigs/<rig>/seats/<seat>/lore/<stable-slug>.md`. One file per item, addressed as `seat:lore/<stable-slug>.md#situation`, carrying `stage` and `taxonomy: lore`.

CANON (docs/reference/knowledge-maturity.md): 'conventions/ and doctrine/ are canon by location', plus SOP.md and CULTURE.md.

HARNESS FILES: OpenRig merges managed blocks into AGENTS.md (Codex, Pi) or CLAUDE.md / CLAUDE.local.md (Claude), between `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` and the matching END marker. It projects skills into .claude/skills and .agents/skills.

NOT FOUND in any OpenRig source checked: CANON.md, MAP.md, STORIES/, a repo-root seats/ directory, a slice-level NOTES.md template, a PROPOSED status, or the term 'path ascent'.

**Pack-claims check.** 1. OpenRig is a real multi-agent orchestration project with a 'rig' of agents in 'seats': TRUE.
2. Project-level SPEC.md: TRUE (workspace/SPEC.md is the project intent). The pack leaves out the required project.yaml and workspace.yaml (schema v0alpha1).
3. missions/{feature}/SPEC.md: TRUE (missions/<mission-id>/SPEC.md). The pack leaves out mission.yaml and PROGRESS.md, and puts NOTES.md at the wrong level.
4. missions/{feature}/slices/{sub_feature}/SPEC.md: TRUE. The pack leaves out slice.yaml, PROGRESS.md and the frontmatter (`intent:` required, `depends_on`, `stage`, `verified`).
5. Slice-level NOTES.md as 'lived notes': MOSTLY FALSE. OpenRig scaffolds NOTES.md at mission level (template frontmatter `mission:`) and classes it as field notes. No slice NOTES.md template exists. OpenRig's 'Living Notes' is a review-composition layer, not a notes file.
6. Slice-level PROOF.md: TRUE. It comes with a proof/ directory and `rig proof add` evidence drops, and is required before a slice can close.
7. topology_tree `global_rules: AGENTS.md`: FALSE for OpenRig. Its topology-tree chain files are LEARNED.md, CRAFT.md, ORCHESTRATION-CRAFT.md and CULTURE.md; OpenRig only merges managed blocks into AGENTS.md/CLAUDE.md. AGENTS.md as global rules is TRUE of the AGENTS.md standard, not of OpenRig.
8. `seats/{seat_id}/LEARNED.md` as seat memory: PARTIALLY TRUE. The real path is <topology.root>/rigs/<rig>/seats/<seat>/LEARNED.md (the pack drops the rig segment). LEARNED.md also exists at instance, rig and pod level, lives under $OPENRIG_HOME rather than in the code repo, is 'never shared or shipped as-is', and assumes OpenRig's persistent seat runtime.
9. epistemic_ladder raw(NOTES.md) → proposed → canon(lore/CANON.md): FALSE as an OpenRig attribution. OpenRig's ladder is 'DATA → FIELD NOTES / OBSERVATIONS → INSIGHTS → CANON' with stages 'wip | provisional | established | canonical | superseded | retired'. There is no PROPOSED status and no CANON.md. Canon is defined by location, the doc is 'direction only', and it names no human gate.
10. 'OpenRig-compatible path-ascent context': concept PARTIALLY TRUE, compatibility UNVERIFIED. The term isn't OpenRig's. Walk-to-root exists in CE-v2 but runs inside OpenRig tooling over its configured roots, and without the YAML manifests and frontmatter nothing shows HACF output would be consumable.
11. 'Maximizes token caching': UNVERIFIED and unsupported. No OpenRig source makes this claim. Anthropic prompt caching needs '100% identical prompt segments' up to the cache breakpoint, which depends on how the harness assembles the prompt, not on directory names.
12. Enables 'orchestrator agnosticism': FALSE as reasoning. Mirroring one orchestrator's internal layout ties HACF to that orchestrator. OpenRig itself keeps these trees outside the code repo, and no other harness reads missions/**/SPEC.md.
13. 'Symlinks cause split-brain hallucination in LLMs': UNVERIFIED; I found no evidence. Claude Code documents symlink support for .claude/rules and skips an AGENTS.md it already loaded through an import or symlink, so it isn't read twice.
14. Adopt the structure 'as the v1.0.0 standard': not a factual claim, but it conflicts with OpenRig being v0.6.13 with v0alpha1 schemas and core conventions under two months old.
15. 'filepath#heading' addressable pointers: TRUE in spirit. OpenRig retrieves single headings (`rig context get`) and uses stable addresses such as `seat:lore/<stable-slug>.md#situation`.
16. A lore/ folder holding STORIES: PARTIALLY TRUE. The author keeps 'stories and lessons like this in a lore folder', but OpenRig lore is per-seat at rigs/<rig>/seats/<seat>/lore/<slug>.md and must never be shared or exported. The pack's shared project-level lore/ is the opposite.
17. lore/MAP.md as 'Trailhead': FALSE as an attribution. 'Trailhead' is the OpenRig author's word, but his trailhead is AGENTS.md, and OpenRig has no MAP.md.
18. lore/CANON.md: FALSE; no such file exists in OpenRig.
19. lore/ADRs/: not from OpenRig. ADRs come from Nygard (2011) and MADR, and HACF already keeps them in docs/adr/.
20. Node dimensions System|Decision|Story|Concept: UNVERIFIED; I found no source, so this looks synthesized.
21. 'Teleological lore (intent, trade-offs, ADRs)': no source found; this looks like synthesized framing.
22. Human-gated PROPOSED → CANON (H4): not from OpenRig, which doesn't say who promotes. It is HACF's own idea and closely matches the ADR 'proposed → accepted' lifecycle.

Sources:

- https://github.com/mvschwarz/openrig
- https://openrig.dev
- https://registry.npmjs.org/@openrig/cli
- https://api.github.com/repos/mvschwarz/openrig
- https://raw.githubusercontent.com/mvschwarz/openrig/main/README.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/ROADMAP.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/project-workspace.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/sdlc-conventions.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/chain-file-convention.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/knowledge-maturity.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/lore-routing.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/agent-startup-guide.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/instance-layout.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/legacy-topology-migration.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/reference/topology-naming.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/docs/as-built/architecture/living-notes-review.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/packages/cli/src/lib/scope-templates/notes.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/packages/cli/src/lib/scope-templates/proof.md
- https://raw.githubusercontent.com/mvschwarz/openrig/main/packages/cli/src/context-resolve.ts
- https://api.github.com/repos/mvschwarz/openrig/commits/622323d
- https://openrig.dev/blog/how-my-agent-teams-find-their-way.md
- https://openrig.dev/blog/what-i-put-in-an-agents-starting-map.md

### Lore vocabulary origin

Most of the vocabulary traces to OpenRig's author, Mike Schwarz, and was then remixed by the LLM that wrote the pack. SOURCES: (1) Blog post 'Context engineering as world building' (openrig.dev/blog/how-my-agent-teams-find-their-way.md, 2026-10-16). It contains: 'In my system, AGENTS.md is a trailhead into the context library.'; 'I keep stories and lessons like this in a lore folder.'; 'A useful observation might become a field note, then a shared insight.'; 'At the top is canon, the knowledge the project treats as established.'; 'That's epistemic maturity.'; 'That's progressive disclosure.' It also describes heading-addressed retrieval (`rig context get`, e.g. demo/slack.md#messaging), which is the origin of the pack's 'filepath#heading' pointers. Notably, the author writes: 'I'd tried graph databases and AI memory tools, and I was still running into this same problem.' (2) Blog post 'What I put in an agent's starting map' (2026-10-20, the same day the pack files are timestamped). It calls AGENTS.md 'a trailhead', has a 'When the map runs out' section, and argues for 'minimum viable competence'. (3) OpenRig docs: knowledge-maturity.md, titled 'Knowledge maturity — the epistemic ladder' and first committed 2026-08-28 as 'recover the epistemic maturity ladder', and lore-routing.md ('Lore is knowledge earned by one durable position while doing its work'). WHAT THE PACK CHANGED OR ADDED: it moved the trailhead from AGENTS.md to a new lore/MAP.md. It collapsed four rungs and six stage values into LIVED → PROPOSED → CANON. 'LIVED' echoes OpenRig's phrase 'the lived layer (LEARNED.md)', and 'PROPOSED' is ADR status vocabulary (Nygard 2011, MADR), which HACF already uses in docs/adr. It invented a single CANON.md file, although OpenRig defines canon by location. It turned private per-seat lore into a shared project-level lore/ with ADRs/ and STORIES/. It added the System|Decision|Story|Concept dimension taxonomy, the 'teleological' framing, a YAML graph index (lore_hypergraph.yml, which continues HACF's own hypergraph idea rather than anything from OpenRig), and mechanical human gating. None of these appear in OpenRig, whose ladder doc says 'Nothing in this document is mechanized' and 'It is direction only'. I found no independent project that uses the exact LIVED/PROPOSED/CANON triple, a MAP.md trailhead, or lore/CANON.md. TIMING: the main sources are 0 to 4 days old relative to the pack and the underlying docs about 7 weeks old, so the vocabulary went into the pack before anyone outside OpenRig had vetted it.

### Comparable standards

- **AGENTS.md open format** (https://agents.md/): The neutral cross-harness home for global rules and a natural trailhead, and HACF already ships one. Nested files are supported: 'The closest AGENTS.md to the edited file wins; explicit user chat prompts override everything.' No structure is mandated. OpenRig itself uses AGENTS.md as its trailhead.
- **OpenAI Codex AGENTS.md discovery** (https://learn.chatgpt.com/docs/agent-configuration/agents-md): A concrete, documented path-ascent behavior: global ~/.codex first, then project root down to cwd, with AGENTS.override.md taking priority over AGENTS.md and fallback names. Codex 'concatenates files from the root down' so closer files come later and win, and caps the total at project_doc_max_bytes (32 KiB).
- **Claude Code CLAUDE.md hierarchy, .claude/rules and auto memory** (https://code.claude.com/docs/en/memory): Native progressive disclosure that HACF can rely on instead of writing its own walkers. Ancestor CLAUDE.md files load at launch, ordered 'from the filesystem root down to your working directory', and subdirectory files 'load on demand'. @imports go up to four hops. Rules can be path-scoped with `paths:` frontmatter. AGENTS.md is read when no CLAUDE.md exists (v2.1.277+). Auto memory covers per-repo learnings.
- **Agent Skills open standard (SKILL.md)** (https://agentskills.io/): Three-stage progressive disclosure: Discovery loads name and description, Activation loads the SKILL.md body, Execution loads resources. HACF already uses .agents/skills/*/SKILL.md, and OpenRig's 'router skill' plays the trailhead role, so the routing layer can be skills rather than a new MAP.md.
- **Gemini CLI / Antigravity GEMINI.md hierarchy** (https://geminicli.com/docs/cli/gemini-md/): Loads a global file plus workspace directories and their ancestors, then does a just-in-time scan 'up to a trusted root', and concatenates the results. `context.fileName` can include AGENTS.md. Relevant because HACF ships GEMINI.md and .antigravitycli. The docs say Gemini CLI was replaced by Antigravity CLI on 2026-06-18.
- **Architecture Decision Records (Michael Nygard, 2011)** (https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions): The established version of PROPOSED → CANON with an audit trail. Sections: Title, Context, Decision, Status, Consequences. Statuses: proposed, accepted, deprecated, superseded. 'If a decision is reversed, we will keep the old one around, but mark it as superseded.' 'Numbers will not be reused.' HACF's docs/adr already follows this pattern.
- **MADR 4.0.0 (Markdown Any Decision Records)** (https://adr.github.io/madr/): Puts machine-readable status in each file's frontmatter (status, date, decision-makers, consulted, informed), with statuses proposed|rejected|accepted|deprecated|'superseded by ADR-NNNN' and files at docs/decisions/NNNN-title-with-dashes.md. A lore index could be generated from these files rather than maintained by hand.
- **adr-tools (Nat Pryce)** (https://github.com/npryce/adr-tools): Precedent for a small deterministic CLI like hyper_lore.py. It keeps numbered ADRs in doc/adr and records supersession with `adr new -s <n>`.
- **GitHub spec-kit (Spec-Driven Development)** (https://github.com/github/spec-kit): The most widely adopted spec layout: flat per-feature folders specs/<NNN-feature>/ with spec.md, plan.md, tasks.md, research.md, data-model.md, contracts/ and quickstart.md, plus memory/constitution.md. Has multi-agent integrations and is past 1.0. A more stable reference point than OpenRig's mission/slice tree if HACF changes its spec layout.
- **Kiro specs and steering** (https://kiro.dev/docs/steering/): Three-file spec workflow: requirements.md (or bugfix.md), design.md, tasks.md. Steering files in .kiro/steering/ (product.md, tech.md, structure.md) use frontmatter inclusion modes always|fileMatch|manual|auto, which is progressive disclosure done in frontmatter. Kiro reads AGENTS.md and always includes it.
- **BMAD Method** (https://github.com/bmad-code-org/BMAD-METHOD): Persona-based agentic SDLC, the closest peer to HACF's phase/persona model. Its loop is Clarify → Plan → Build and verify → Learn and adjust, with a Product Owner agent (bmad-agent-po) and a bmad-project-context skill. Current artifact file paths were not stated in the docs I fetched (UNVERIFIED).
- **llms.txt** (https://llmstxt.org/): A standardized 'map' format: an H1 (the only required section), a blockquote summary, H2 lists of links, and an 'Optional' section, meant for inference-time use. Could define the body of any HACF trailhead or map file instead of a bespoke MAP.md format.
- **Graphiti (Zep)** (https://github.com/getzep/graphiti): Temporal context graphs for agents. 'old facts are invalidated — not deleted', episodes are 'the ground truth stream' with provenance, ontologies can be custom Pydantic types, and backends include Neo4j, FalkorDB and Neptune (Kuzu deprecated). Its reference MCP server is not production-ready and has no auth. A good model for lore provenance and invalidation, but heavy infrastructure for a file-based framework.
- **Cognee** (https://github.com/topoteretes/cognee): Open-source agent memory with remember/recall/improve/forget operations, graph plus vector retrieval, custom ontologies and an MCP server. Could be a pluggable backend behind hyper_lore.py instead of a native YAML graph, matching the pack's own pluggable code-graph pattern.
- **Basic Memory** (https://github.com/basicmachines-co/basic-memory): Each Markdown file is an entity with frontmatter, [category] observations and [[WikiLinks]] relations, backed by a local SQLite index and an MCP server with memory:// URLs. The closest existing design to 'Markdown is the source of truth, the graph is a derived index'. AGPL-3.0, so do not vendor or bundle it without a license review.
- **Letta / Letta Code MemFS (Context Repositories)** (https://www.letta.com/blog/context-repositories): Git-backed Markdown memory (announced 2026-02-12): every change is versioned with a commit, files under system/ always load, and other files appear as a tree of names and descriptions. Precedent for version-controlled agent memory with progressive disclosure.

<a id="unverified"></a>
## UNVERIFIED items (must be settled by spikes or live tests)

- codebase-memory-mcp 0.12.1 details remain UNVERIFIED (--base-branch, payload fields, exit codes, shim cache) until a consented contract run. Early parser fixtures may be synthetic.
