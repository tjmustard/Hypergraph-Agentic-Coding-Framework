# v0.6.0 Subsystem Designs

> Normative annex for HACF v0.6.0 — see [README.md](README.md) for precedence.
> Rendered verbatim from the reviewed design (design → adversarial critique → revision).

<a id="engine"></a>
## Workflow engine and headless executors

Modules:
- hyper_engine.py (core, land)
- hyper_executors.py (plugins, sandbox settings)
- hyper_failures.py
- hyper_gates.py
- hyper_orchestrator.py (CLI wrapper)
- hyper_daemon.py (single-slice runner)
No Anthropic SDK import anywhere: the messages.create at :584 and hyper_fix.py are removed.

RUN LIFECYCLE
1. Preflight (fatal on failure):
   - git worktree support
   - clean tracked files in the main checkout
   - `claude --version` between min 2.1.259 and the tested maximum (warning above it); the exact version is recorded in run.json
   - `executors.claude-cli.verified` is true (set by a passing S08 live test); otherwise refuse unless the CLI flag `--unverified-executor` is given, recorded in run.json (R20)
   - a `status: test-only` executor (the stub) is refused unless the environment has `HACF_TEST=1`, whatever `--unverified-executor` says; `status` is shipped-only (R41)
   - `claude auth status` exits 0, run with the same scrubbed env
   - agent sandbox available (failIfUnavailable; bwrap and socat on Linux; Ubuntu 24.04 AppArmor note); otherwise sandbox_unavailable, unless the CLI flag --allow-unsandboxed is given, with a loud warning. `sandbox: required` applies to every executor; codex and gemini have no HACF-verified sandbox, so they run only with CLI --allow-unsandboxed, never from tracked config (R20)
   - oracle sandbox available: bwrap on Linux; otherwise sandbox_unavailable unless the CLI flag --allow-unsandboxed-oracle is given; on macOS the engine refuses oracles without that flag (R2)
   - security keys (`executors.*.sandbox`, `executors.*.verified`, `executors.*.argv`, `executors.*.preflight`, `executors.*.prompts`, `oracle.sandbox`, `oracle.allowed_executables`, `autonomy.env_passthrough*`, `autonomy.auth_passthrough`, `autonomy.credential_scan`, budgets, `scope_policy`, `protected_paths`, `git.ref_guard`) are resolved from shipped defaults and CLI flags only; tracked `.agents/local/hacf.yml` and `.agents/config/**` may only tighten them (R4). Tightening for the R42 keys: `argv`, `preflight` and `prompts` accept only the shipped value (a custom command needs a new executor name, which is experimental and needs `--allow-unsandboxed`); `protected_paths` accepts added entries, never removed ones; `git.ref_guard` and `autonomy.credential_scan` accept only `true`. `oracle.prepare` stays configurable: it runs from the base revision (R2), and non-Python repos need their own (R42)
   - a ledger exists; if not, preflight fails and names `python3 .agents/scripts/hyper_ledger.py init` and /hyper-discover (R21)
   - lint, ledger and lore validate; acyclic DAG
   - engine.lock (see LOCKS)
   - the sha256 of the main checkout's ledger is recorded in run.json as the base ledger (R5)
2. `git branch swimlane/hacf/<run>/integration <base>` plus its own worktree. The swimlane prefix keeps pre-push blocking it.
3. Scheduler:
   - ready = compiled slices (or audit_failed with retries left) whose depends_on are audited and integrated
   - serialize on hacf_touches overlap or a shared node_id
   - ties break by composition order
   - the run budget and the global rate-limit pause (BUDGET AND RATE LIMITS) stop or delay scheduling
4. Per slice:
   a. Worktree `git worktree add -b swimlane/hacf/<run>/<NN-slug>` from the integration tip. Copy untracked inputs (a legacy MiniPRD; a read-only copy of an untracked ledger). Create the outbox at `<worktree>/.hacf-outbox/` and set `HACF_OUTBOX` to it (R20). Materialize, from the integration base, a read-only run dir (`.agents/state/runs/<id>/base/`) holding AGENTS.md, the SKILL.md files the prompts name and base-revision copies of the HACF hook scripts (R4). Run oracle.prepare (e.g. `uv sync --frozen`) engine-side from the base revision's lockfile (R2).
   b. Guard snapshot (R1): `git for-each-ref` plus a sha256 manifest of the worktree's `.git` pointer file, `<common>/worktrees/**`, `<common>/info/*`, `<common>/objects/info/*`, `<common>/shallow`, `<common>/packed-refs` and every HEAD, ORIG_HEAD and MERGE_HEAD file (common and per-worktree), where `<common>` is `git rev-parse --git-common-dir`. An `objects/info/alternates` file is refused outright (ref_violation).
   c. EXECUTE: claude-cli argv. The prompt tells the agent to read AGENTS.md and the SKILL.md from the read-only run dir of step a, not from the worktree (R4). --settings is engine-generated JSON: it enables the sandbox (writes limited to the worktree and tmp; the shared .git is not writable and the worktree's `.git` pointer file is in denyWrite, R1; network allowlist empty; allowUnsandboxedCommands false), and its hooks call the base-revision copies of HACF scripts by absolute path (R4). --setting-sources user (no project, R4); docs state that user-level hooks still run. The env is scrubbed; passthrough covers only the auth method in use: `AWS_*` only with `CLAUDE_CODE_USE_BEDROCK=1`, `GOOGLE_*`/`CLOUD_ML_*` only with `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_API_KEY` only for API-key auth (R20). The engine adds `UV_NO_CACHE=1`, because the sandbox makes `~/.cache/uv` read-only and `uv run` fails to open it (S08 live test; R40). dontAsk, --permission-prompts none. Popen(start_new_session) with a timeout: SIGTERM to the group (Claude exits 143 and kills its Bash trees), then SIGKILL after 30 s. Agent phases never write git metadata; the engine performs every git write (commits, the proof commit, candidate refs) itself, with explicit `GIT_DIR` and `GIT_WORK_TREE` (R1).
   d. Guard check (R1): the step b snapshot is taken again after every agent phase (c, i) and after steps g, h and i. Any difference (a moved ref, a changed manifest entry, a new alternates file) is restored where possible and the slice gets ref_violation (blocked, worktree kept).
   e. Gates: the engine moves `<worktree>/.hacf-outbox/` to the run dir before the scope check and refuses entry types outside the whitelist (ledger ops, lore propose/note/stale, spec progress; approval entries are rejected) (R20). Outbox gates.jsonl: a stop gate gives gate_stopped, and dependents become upstream_blocked.
   f. Scope check: a protected path changed gives scope_violation. Always protected (agent writes go through the outbox): architecture.yml, lore_hypergraph.yml, lore/CANON.md, lore/canon/** (R8), mission.yaml, slice.yaml, other slices' dirs, .git*, .mcp.json. Always protected unless the slice's hacf_touches list them, independent of scope_policy (R4, R2): `.agents/skills/**`, `.agents/scripts/**`, `.agents/config/**`, `.agents/local/hacf.yml`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/**`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json` and other lockfiles. The slice diff is also scanned for credential shapes (AWS access keys, PEM private-key headers, `sk-ant-`, GitHub tokens); a hit gives scope_violation (R20). The moved outbox path is exempt. Other files outside hacf_touches warn, or fail under the strict policy.
   g. COMMIT by the engine, with trailers, explicit GIT_DIR/GIT_WORK_TREE, `-c core.hooksPath=<empty dir>` and `--no-verify` (R3). Before committing, if the repo has pre-commit or commit-msg hooks (`core.hooksPath` or `.git/hooks`), the engine runs the base revision's copies of those hooks inside the oracle bwrap profile (step h); a failing hook gives commit_rejected (R3). The guard (d) runs after the commit.
   h. ORACLE: argv, first element in allowed_executables, shell=False. allowed_executables is hygiene, not isolation: every listed tool runs repo-defined code (R2). Default `oracle.sandbox: bwrap` on Linux (R2): read-only root, rw worktree and tmp, `$HOME` masked except read-only binds the oracle needs, `.git` read-only (the pointer file and the common dir), --unshare-net, --die-with-parent. The read-only binds are the real path of argv[0] as found on the scrubbed PATH and, when the worktree has `.venv/pyvenv.cfg`, the parent of its `home` directory (the interpreter's whole install prefix; the binary alone cannot find its stdlib). No uv cache is bound: uv opens its cache for writing, and a read-only `~/.cache/uv` fails (R40). If bwrap is unavailable: sandbox_unavailable (fatal). `none` only with the CLI flag --allow-unsandboxed-oracle, never from config. Python oracles run `uv run --frozen --no-sync` after the engine-side prepare. The oracle env is scrubbed like step c (PATH, LANG, TMPDIR, the masked HOME plus the auth passthrough) (R20), plus `UV_NO_CACHE=1`, so uv uses a throwaway cache in the sandbox tmp (R40). Failure: one re-execute with the punch list (HACF_ALLOW_REIMPLEMENT=1), then blocked. The guard (d) runs after the oracle.
   i. AUDIT: fresh session, same sandbox, the same engine-generated settings and the same read-only base-revision skill text (R4). In autonomous mode the audit skill writes its verdict only to the outbox audit.json; it never writes PROOF, proof files or refs (R1). After the phase the guard (d) runs, and a dirty worktree (anything besides the outbox) is refused (R4). On PASS, the engine writes and commits the slice PROGRESS, PROOF and proof files, staging only those named files, with candidate_sha = the g commit, and writes any candidate ref itself (R1, R4).
   j. INTEGRATE on one serialized thread:
      - merge --no-ff in the integration worktree
      - apply outboxes: hyper_ledger apply-outbox, hyper_lore apply-outbox (proposals, notes and stale marks only; approval entries are rejected), hyper_spec progress audited
      - validate
      - on failure, reset --hard inside the integration worktree only (ledger_invalid or lore_invalid)
      - commit
   k. Cleanup on success; keep the worktree on failure.
5. End of run: report table, run.json (base ledger sha256, `claude --version`, CLI flags used), events.jsonl, --resume. `--resume` refuses (exit 5, report) when the main checkout's ledger differs from the recorded base ledger (R5). Each resume records its own resolved security values in its run.json `resumed[]` entry; the top-level `security` stays the first run's record (R42).

BUDGET AND RATE LIMITS (R20):
- `max_budget_usd_per_phase` per phase, as before.
- `autonomy.max_budget_usd_per_run` (default 50.0; a security key per R4, so only defaults or a CLI flag set it) stops scheduling when reached and records budget_exceeded for the run.
- rate_limited pauses every worker together (global backoff 30 s, then 120 s), instead of per-slice retries that keep calling during an account-level limit.

LANDING: `hyper_engine.py land <run> [--merge] [--rebase-ledger]`.
- Tracked ledgers ride along in the integration commits.
- An untracked ledger (HACF repo after S15b, or users with a gitignored spec/) is staged in .agents/state/runs/<id>/ledger/. It is applied to the main checkout only by `land`, under ledger.lock (held for the whole land) with validation.
- Ledger drift (R5): `land` refuses (exit 5, with a report) when the main checkout's ledger sha256 differs from the base ledger recorded in run.json. `land --rebase-ledger` instead replays the run's recorded ledger outbox entries (ensure-node, set-edges, apply-review patches) onto the current ledger through `hyper_ledger.py apply-outbox`, validating; an entry that touches a node whose content changed since the base is a conflict, and the land is refused with a per-node report.
- --merge runs `git merge --ff-only` into the checked-out base branch, only if it is clean. Without --merge, land prints the command.
- Until land, the main checkout is byte-identical.

GARBAGE COLLECTION (R20): `hyper_engine.py gc [--older-than 14d] [--apply]` lists by default, and with --apply removes, kept worktrees (then `git worktree prune`), `swimlane/hacf/<run>/*` branches, run dirs and `refs/hacf/candidates/*` of runs that are landed or abandoned. It never touches a run that holds engine.lock.

CODE GRAPH: off in engine runs. Blast radius comes from git diff plus files: globs. When code_graph_in_engine is true, the integration worktree is indexed once per run and deleted at cleanup.

EXECUTOR CONTRACT:
- preflight()
- build_argv(phase, ctx)
- build_settings(ctx)
- parse_result(completed) -> ExecResult {ok, failure_type, session_id, num_turns, cost_usd, result_text, permission_denials, raw_ref}
Templates are literal or single-placeholder elements; lists are comma-joined.
- `verified`: true only after the executor's live test passed (S08 for claude-cli); an unverified executor needs the CLI flag --unverified-executor (R20).
- codex and gemini: experimental, argv and parse tests only (-m, -C and the gemini flags UNVERIFIED; no --full-auto). They run only with the CLI flag --allow-unsandboxed, never from tracked config, and print a warning (R20).
- openrig-seat: seam only, raises NotImplementedError.
- stub: test-only; the engine refuses it unless `HACF_TEST=1` (R41).

AUTONOMOUS MODE: HACF_AUTONOMOUS=1, which the engine sets. `hyper_gates.py mode` reports autonomous only for that variable; no permission-mode channel exists, and the hook ticket enforces the lore gate (R7, R6).
- execute: already-complete, low confidence and missing input are recorded and stop the slice; ledger and lore writes go to the outbox; the summary goes to execute.json.
- audit: FAIL with a punch list; novel_fixture_needed (outputs routed to tests/candidate_outputs/<SID>/, R27); review JSON patch (applied only through `hyper_ledger.py apply-review`, R27); lore_decision_needed (never approves; does not fail the audit). The verdict goes only to the outbox audit.json (R1).
- lore-approve, resolve and architect refuse to run.
AskUserQuestion is unavailable (dontAsk and --permission-prompts none). The gate hook denies gated verbs in dontAsk.

FAILURE TAXONOMY:
- Fatal: preflight_failed, executor_unavailable, executor_auth, sandbox_unavailable (agent or oracle sandbox, R2).
- Retried: rate_limited (global pause, 30 s, then 120 s; R20); timeout and executor_error (one retry each).
- Retried once, then blocked: oracle_failed, audit_failed.
- Blocked: turns_exceeded, budget_exceeded (phase or run, R20), gate_stopped, scope_violation (including control paths, lockfiles and credential hits), ref_violation (any guard difference, R1), merge_conflict, commit_rejected (including a failing base-revision hook, R3).
- Rolled back, then blocked: ledger_invalid, lore_invalid.
- upstream_blocked: dependents of a blocked slice.
- internal_error: the run aborts with state saved.

LOCKS (R22): lock files live under `$(git rev-parse --git-common-dir)/hacf/locks/` (engine.lock, ledger.lock, lore.lock, git.lock), so every worktree shares them; ledger.lock and lore.lock are taken before open. With HACF_OUTBOX set, scripts write only the outbox and take no shared lock (agent phases cannot write .git, R1). git.lock is paired with an in-process lock for worktree, branch and merge operations.

SECURITY (honest statement):
- The OS sandbox bounds Bash writes (worktree and tmp only; no git metadata, R1) and network for agent phases. bwrap bounds oracles and base-revision hook runs (R2, R3).
- Permission rules are defense in depth only, because Bash rules are 'not a security boundary' (permissions doc).
- The real gates are the sandboxes, the git-metadata guard, the scope check with its always-protected control paths, the outbox whitelist, the credential scan and the never-push policy.
- Remaining exposures:
  - built-in Edit/Write follow permission rules (Edit(./**) only)
  - user-level Claude Code hooks run in engine sessions, because the `user` setting source stays (R4)
  - with the CLI flag --allow-unsandboxed-oracle (required on macOS) oracles run agent-written code with the user's privileges and network (R2); with --allow-unsandboxed agent phases run without the OS sandbox (R20)
  - the guard detects and restores; a git-metadata write outside its manifest would go unnoticed (none is reachable from a sandboxed phase, R1)

PROVENANCE: commit trailers; ledger provenance; lore provenance (session_id, run_id); slice PROGRESS outcome lines; run.json (base ledger sha256, `claude --version`, CLI flags).

TESTS (stub executor, temp repos): diamond DAG; parallel vs overlap; gate stop; oracle and audit retry; budget (phase and run), turns and timeout with process-group kill; global rate-limit pause; scope and ref violations; guard cases (worktree `.git` pointer rewrite, a sibling worktree's HEAD, info/exclude, info/attributes, an alternates file refused; R1); a control-path or lockfile edit outside touches gives scope_violation (R4, R2); a credential-shaped string in the diff gives scope_violation (R20); hacf.yml cannot loosen a security key, including the R42 keys (R4, R42); the stub is refused without `HACF_TEST=1` (R41); a `uv run --frozen --no-sync` oracle passes inside the bwrap profile and agent phases get `UV_NO_CACHE=1` (R40); each resume records its security values (R42); a dirty worktree after audit is refused (R4); hooks disabled on engine commits and base-revision hooks run in bwrap (R3); outbox whitelist (R20); env passthrough by auth method (R20); unverified executor refused without the flag (R20); merge conflict; ledger rollback; commit_rejected; resume and land refuse ledger drift, `--rebase-ledger` replays and reports conflicts (R5); `gc` lists by default and never touches a locked run (R20); untracked ledger plus land; main checkout byte-identical; no anthropic import; argv and settings goldens.

<a id="lore"></a>
## Lore engine and CANON gate

COMMANDS (`python3 .agents/scripts/hyper_lore.py <cmd>`)
Read-only: validate [--strict], show, list [--status] [--stale] [--json] [--with-commit], graph --json. `list --status CANON --with-commit` shows, for each CANON node, the commit that introduced its CANON status (R23).
Agent writes:
- propose --type decision|story|canon --title --body-file [--anchors-*] [--planned-*] [--relates-to] [--supersedes] [--source] [--context]
- note --mission <dir> | --legacy --source <path> --text [--kind]
- stale --nodes --files --reason
- resolve-anchors
Gate (each gated verb needs a hook ticket or a TTY confirmation, R6):
- approval-request <ID>
- approval-cancel <ID>
- approve | reject | retire | reopen | review --still-valid, each with <ID> --nonce N --warrant-file F
Maintenance: index (rebuilds MAP.md's generated region and CANON.md from lore/canon/, R8), import --drafts | --adr-dirs [--context], merge-driver, merge-generated, renumber, apply-outbox (rejects approval entries), sync.
Exit codes: 0 ok, 1 validation, 2 usage, 3 lock, 4 refused, 5 conflict.

GATE FLOW (D7), shared by /hyper-lore-approve and /hyper-audit Phase 3.5:
1. `hyper_gates.py mode` must print interactive. It prints autonomous only under HACF_AUTONOMOUS=1; there is no permission-mode channel (R7).
2. Pick ONE node.
3. `approval-request <ID>`. It prints the exact hashed body, sha and nonce; only one request may be pending; it expires after 30 min.
4. Show the body, anchors, provenance and related nodes, and list every id the approval will supersede, retire or re-anchor (R23). AskUserQuestion with exactly: 'Approve (becomes CANON)', 'Reject (kept in the graveyard)', 'Keep PROPOSED', 'Skip'. For stale CANON nodes: 'Still valid', 'Retire', 'Keep stale'.
5. Keep or Skip: `approval-cancel`.
6. Approve, Reject, Retire or Still valid: ask for the warrant in the user's own words. Write it with the Write tool to .agents/state/lore/pending/<ID>.warrant.md.
7. Run `hyper_lore.py approve <ID> --nonce <n> --warrant-file <f>`. The settings ask rules and the PreToolUse hook force Claude Code's native prompt for the invocations the hook's matcher recognizes (#firewall); the prompt's reason shows the ID, the title, the warrant and every id the approval supersedes, retires or re-anchors (R23), with control characters stripped and its length capped (R6). On a gated match the hook writes a single-use ticket under .agents/state/lore/tickets/, bound to verb + id + nonce, with a 120 s TTL (R6). The hook denies gated verbs in dontAsk, bypassPermissions and plan modes with the reason "switch to default or acceptEdits (Shift+Tab) and retry" (R7).
8. The script refuses under HACF_AUTONOMOUS=1. It then requires a matching, unexpired ticket and consumes it; without a ticket (tools without a PreToolUse hook) it proceeds only when stdin and stdout are a TTY and the user types the node id at a confirmation prompt, otherwise it exits 4 (R6). It checks the nonce, the unchanged sha and the warrant (non-empty, not the title), takes approved_by from git config (attribution, not identity, R23), records approval and history, moves the approved body from lore/proposed/<ID>.md to the immutable lore/canon/<ID>.md (R8), applies supersession (a status change only; canon files are never edited) and runs index.
9. Continue only after a new answer; there is no approve-all.
10. /hyper-lore-approve ends by recommending a commit that touches only lore paths; it does not commit (R23).
Guarantee: harness-assisted and procedural, not a security boundary. Remaining bypasses, documented in docs/Lore.md and the render report (R6): a deliberately forged ticket, and direct Python writes to lore_hypergraph.yml, which Edit denies do not cover. `validate` checks format only and cannot tell a forged approval record from a real one; git history and review are the provenance (R23). Branch merges are not gated: a branch whose lore_hypergraph.yml gained a CANON node merges cleanly through hacf-lore, so docs/Lore.md recommends CODEOWNERS on lore_hypergraph.yml, lore/canon/** and lore/CANON.md, plus branch protection (R23).

PER-SKILL INTEGRATION
- /hyper-resolve: trade-offs become `propose --type decision` (PROPOSED, anchors from the blast radius). This replaces Final Truth.
- /hyper-grill-docs: ADRs via propose into lore/ADRs; CONTEXT.md unchanged.
- /hyper-execute: v6 notes go to `note --mission`; legacy notes to `note --legacy` (activeContext 'Field Notes (legacy)'), which the migration moves into mission NOTES. Never spec/process.
- Updater and ledger impact: the post-impact hook runs stale. ensure-node and index run resolve-anchors.
- /hyper-audit 3.5: lore-check selects nodes proposed by this slice (provenance.source is the slice id or legacy path) plus stale nodes in the blast radius. 'Keep PROPOSED' never blocks.
- /hyper-architect and /hyper-redteam: CANON is a constraint, and stale CANON stays binding until it is retired or superseded (R23). PROPOSED nodes with `origin: user-decision` also bind as constraints until rejected (R23). REJECTED, SUPERSEDED and RETIRED form the graveyard; ids are cited.
- /hyper-status: `list --json`.
- Engine: outbox only.

STALE AND REMOVED ANCHORS (R21): `validate` reports an anchor to a tombstoned component id (the ledger's top-level `removed_nodes`) or to a deleted file as a stale warning (exit 0 when there are only warnings). A CANON node with such an anchor is marked stale and goes to the gated stale review. An anchor to an unknown id (neither a node nor a tombstone) is an error. Agents may also update the anchors of PROPOSED nodes.

docs/adr TO lore/ADRs: the scan covers **/docs/adr including multi-context layouts, but skips gitlinks (`git ls-files -s` mode 160000), untracked trees and nested repos; interactive runs ask per directory, and non-interactive runs import only the project root's docs/adr (R25). NNNN-slug becomes ADR-NNNN-slug, renumbered on collision, with legacy.source kept. Status mapping as in the migration (accepted stays PROPOSED with legacy.status accepted). Bodies are verbatim; each moved file leaves a per-file redirect stub (R25); ADR-FORMAT.md is rewritten.

FINAL TRUTH RETIREMENT (S10): delete compaction_engine.py, interrupt_detector.py and final_truth_template.md; remove-node context_compaction. README and CHANGELOG are handled in S15a2 and S15b.

MEMORY FOLDING: productContext goes into SPEC.md; systemPatterns becomes a PROPOSED canon (its body reaches lore/canon/ and CANON.md only through the gate); activeContext is kept. refresh-memory and session-update write activeContext and propose lore.

MAP.md: a hand-written intro plus a generated region (Canon, Proposed, Stale, Graveyard with warrants, Stories, Where things live including activeContext). The Graveyard shows the newest 20 REJECTED entries plus the line "full list: `python3 .agents/scripts/hyper_lore.py list --status REJECTED`" (R20). Regenerated after every write and integration, with deterministic sorting. validate fails on a stale region; pre-commit runs it when lore is staged.

CANON BODIES (R8): an approved body lives in the immutable lore/canon/<ID>.md, moved from lore/proposed/<ID>.md on approval and never edited afterwards; supersede, retire and reopen change only the status in lore_hypergraph.yml. lore/CANON.md is a pure concatenation of the CANON nodes' canon files that `index` can always rebuild, and `validate` errors on a CANON node without its canon file.

MERGES: hacf-lore merges lore_hypergraph.yml by id with a union of history. hacf-generated regenerates (runs index) for MAP.md's generated region and for CANON.md instead of keeping "ours", so a branch's approved canon is never dropped (R8). The scaffold writes the merge-driver attributes (`lore_hypergraph.yml merge=hacf-lore`, the generated files `merge=hacf-generated`) into `$(git rev-parse --git-common-dir)/info/attributes` next to the repo-local driver config, never into a tracked .gitattributes, in HACF's own repo and in user projects (R10).

AGENTS.md TRAILHEAD: the HACF block opens with 'Trailhead: start at lore/MAP.md, then SPEC.md, then the active mission under missions/'. It also states that PROPOSED nodes with `origin: user-decision` bind agents until rejected and that stale CANON stays binding until retired or superseded (R23). MAP content is never duplicated into AGENTS.md.

<a id="installer"></a>
## Installer, uninstaller and update

HACF-install.sh stays bash with `set -euo pipefail` and stays compatible with `curl | bash`.

SUPPORTED PLATFORMS (R24): Linux (Ubuntu 22.04+, Debian 12+) and WSL2 are supported and tested. macOS is best-effort: the installer refuses bash < 4 (`BASH_VERSINFO`) with a Homebrew hint, and shipped code uses Python `hashlib`, never `sha256sum` or other GNU-only tools. The engine's bwrap oracle is Linux-only (R2).

FLAGS
Kept: -y, --ides=, --preserve-custom, --mode=, --files=.
New:
- --install-deps (consent for pyyaml)
- --code-graph=none|codebase-memory-mcp
- --install-code-graph
- --firewall=auto|off
- --migrate
- --statusline
- --dry-run
- --repo-url=, --branch= (R12)
URL overrides (R12): today `REPO_URL` and `BRANCH` are hardcoded (:39-40, :378). The upstream URL and branch can be overridden only by the CLI flags --repo-url and --branch. The env variables HACF_REPO_URL and HACF_BRANCH are honored only when HACF_TEST=1 (E2E tests). A non-`https://` URL is refused unless HACF_TEST=1 (the E2E uses a local bare clone).

CONSENT
- prompt_yn returns YES under AUTO_YES (:128-141), so it is never used for consent.
- New prompt_consent(flag): returns NO under -y unless that action's flag was given. It reads the answer from /dev/tty when /dev/tty exists and is readable, so `curl | bash` still asks an interactive user; otherwise it returns NO unless the flag was given (R24). In a TTY it asks, with default No.
- Every network or third-party action uses it: pip, uv tool or uvx, the vendor install.sh, ccstatusline.
- Every consent prompt and log line prints the effective upstream URL and commit sha (R12).
- No bare `read -p`.
- --ides is honored under -y and non-TTY (fixes :295-301).

PREFLIGHT, before any write:
- git; bash >= 4 (R24)
- python3 >= 3.10 and `yaml.__version__` >= 5.4, the same rule as `.agents/scripts/_runtime.py` (R24), with per-OS guidance (`uv python install`, Homebrew, deadsnakes) when either is too old.
- If PyYAML is missing: with consent, `python3 -m pip install --user pyyaml`. On PEP 668 refusal, print uv and venv instructions and abort before any write (Q34, R24).
- This replaces the unconditional `pip install` at :625-634, which today runs after files are already copied.

MODE DETECTION: upgrade only if .agents/hacf-manifest.json or .agents/scripts/hypergraph_updater.py exists. Today it is `-d .agents` (:202), which misfires on OpenRig-projected .agents/skills.

FLOW
1. Preflight.
2. Mode and IDE selection.
3. Clone.
4. System dirs, manifest-aware: locally modified files are backed up to .agents/.backup/<ts>/ first.
5. Skills and bridges. Unknown skill dirs, including OpenRig-projected ones, are never deleted.
6. `hyper_scaffold.py apply`. Replaces install_spec_scaffold (:396-407) and CORE_DIRS tests (:89, :409-432). It skips with a report and stays in legacy mode on a foreign collision. It writes the lore merge-driver attributes into `$(git rev-parse --git-common-dir)/info/attributes` (a hash-comment managed block) next to the repo-local driver config, never a tracked .gitattributes (R10).
7. IDE dirs: claude means .claude/commands only (today :51 copies settings.json).
8. AGENTS.md, CLAUDE.md and GEMINI.md through hyper_blocks (MANAGED BLOCKS below). Replaces the whole-file copy at :571-607. OpenRig and RIGGED blocks are preserved byte-for-byte; --preserve-custom keeps its meaning. A v0.5.9 whole file whose sha is in legacy_file_hashes.json is replaced by the HACF block. A whole file with no HACF markers and an unknown sha is never appended to (R9): an interactive run asks (through /dev/tty) whether to (a) replace it with the v0.6.0 HACF block and save `<file>.v059.bak`, or (b) leave it and write the block to `<STEM>.hacf-v0.6.0.md` (e.g. `AGENTS.hacf-v0.6.0.md`) with merge instructions; -y or a non-TTY run takes (b) and adds a report line. CLAUDE.local.md is never touched (R26).
9. Settings: merge-settings with the fragment (allows, SessionStart hook, gate hook; statusLine only with --statusline) plus the rendered deny and ask rules. settings.local.json is never touched.
10. Code graph, if selected:
    - backend and install_method go to .agents/local/hacf.yml
    - consented install: `uv tool install codebase-memory-mcp==0.12.1`, or uvx, or the tag-pinned vendor install.sh with --skip-config and CBM_DOWNLOAD_URL
    - when the S04 contract test was declined, the prompt labels the backend "unverified" (R20)
    - `hyper_codegraph.py index` records the project name in .agents/state/codegraph/project.json
11. MCP registration: merge {type: stdio, command: uvx|codebase-memory-mcp, args} into .mcp.json (claude, https://code.claude.com/docs/en/mcp), .cursor/mcp.json (cursor; type stdio required, https://cursor.com/docs/context/mcp) and .roo/mcp.json (roo; mcpServers command/args, Roo docs). Other servers are preserved. Windsurf, Cline and Gemini/Antigravity get printed steps (UNVERIFIED formats). The report and docs state that these tracked files make every teammate who accepts the project server run `uvx codebase-memory-mcp@0.12.1` (exact pin kept) (R24).
12. Firewall render.
13. Hooks: pre-commit (staged-only), commit-msg, pre-push.
14. Write the manifest.
15. If ./HACF-uninstall.sh matches a known v0.5.x sha, warn and offer replacement with consent. Detection covers only ./HACF-uninstall.sh, so README and CHANGELOG lead with "delete any saved copy of HACF-uninstall.sh" and name what a saved copy deletes: any project file whose path exists in HACF main, including `.gitattributes` (Git LFS rules), `lore_hypergraph.yml`, `docs/*`, `SPEC.md`, `project.yaml` and `workspace.yaml` (R10).
16. Report. Banner: '/hyper-discover, then /hyper-baseline to write SPEC.md' (fixes :867-868).

NON-INTERACTIVE DEFAULTS
- Code graph none; no downloads; no user-level config.
- Firewall auto, project files only.
- The scaffold runs only when there is no collision.
- The migration is never applied; the plan command is printed.
- The .gitignore block is always written.

PER-PROJECT SELECTIONS: .agents/local/hacf.yml, never shipped or overwritten. The installer writes only keys that are absent or given by flag. Machine data lives in .agents/state/.

ROOT COLLISIONS: `hyper_scaffold check` classifies each item as missing, hacf, openrig (adopted) or foreign (abort before writes). Fallback flags --openrig-intent-file and --missions-root are offered only if the spike shows rig honors them; missions.root is honored per the scope.ts help text.

MANIFEST: .agents/hacf-manifest.json {version, installed_commit, installed_at, files: {path: {sha256, owner}}, settings_rules, hooks, blocks, mcp_entries}. `installed_commit` is the installed upstream commit, used for the anti-rollback check (R25).

MISSING CONFIG (R24): every v0.6.0 script tolerates a missing `.agents/config/*` file (orchestration.yml, code_graph.defaults.yml, upstream.yml, legacy_file_hashes.json) by using embedded defaults and printing "run HACF-install.sh to complete the upgrade". This covers v0.5.x users whose old /hyper-update text copied only skills, scripts and schemas, and the partial state a saved v0.5.x installer leaves (Q33).

MANAGED BLOCKS (hyper_blocks, R26):
- Markers are exact and case-sensitive. Markdown: `<!-- BEGIN HACF MANAGED BLOCK: <id> -->` and `<!-- END HACF MANAGED BLOCK: <id> -->`. Hash-comment files (.gitignore, .agentignore, info/attributes): `# BEGIN HACF MANAGED BLOCK: <id>` and `# END HACF MANAGED BLOCK: <id>`. Generated regions keep `HACF GENERATED: <id>`. `<id>` matches `^[a-z][a-z0-9-]{0,63}$`. A prefix or case-folded match never counts, so `<!-- BEGIN OpenRig MANAGED BLOCK: role -->` is never treated as an HACF block.
- Markers count only at column 0 and outside fenced code blocks.
- The file's EOL style (LF or CRLF), UTF-8 BOM and final-newline state are preserved.
- Duplicate, nested or unbalanced HACF markers: exit 5, nothing written.
- A user-edited HACF block (its sha differs from the manifest): back it up and ask when interactive; leave it and report under -y.
- CLAUDE.local.md is never touched.
- Writes are compare-and-swap: the sha read is re-checked before os.replace, and a concurrent change (for example OpenRig rewriting its block) gives exit 5 (R22).
- S12a has one test per case, each asserting the bytes outside the block are identical.

UNINSTALL (S12c):
- Delete only manifest files whose sha still matches. Without a manifest, only files whose sha is in legacy_file_hashes.json, read from the uninstaller's own fresh clone, not from the project (R24).
- The clone URL and branch follow the installer's override rules: CLI --repo-url and --branch, env only under HACF_TEST=1, https only (R12).
- Remove HACF blocks, rules, hooks and MCP entries.
- When .agents/state/codegraph/project.json names a project, offer `hyper_codegraph.py delete` with consent (R24).
- Refuse NEVER_COPY paths.
- Never touch SPEC.md, project.yaml, workspace.yaml, lore/, missions/, spec/, tests/ or user root files.
- Today's script deletes every clone-matching path (:119-133).

/hyper-update (S13c): manifest per-file sync of the framework dirs; spec/ and tests/ removed from AUTO_UPDATE_DIRS (:35-41); block merges for .agentignore and the sensitive files, with the unknown-sha whole-file rule of FLOW step 8 (R9); NEVER_COPY; integrity policy per ADR-0010; migration hand-off. Integrity and trust:
- Releases stay unsigned in v0.6.x; an unsigned update needs interactive consent or `--allow-unsigned`. Any future signing is SSH-only: the verifier passes `-c gpg.ssh.allowedSignersFile=<pinned file>` per call, and docs never instruct a global key import, because a GPG signature would let a saved v0.5.x updater's `git verify-commit` pass and arm its rmtree path (R11).
- The upstream URL and ref come only from `--upstream-url` and `--ref`; HACF_UPSTREAM_URL is honored only under HACF_TEST=1; non-https URLs are refused outside tests; prompts and logs print the effective URL and commit (R12).
- Allowed signers are pinned machine-locally in `.agents/state/upstream-trust.json` on first trust; the payload's `.agents/config/upstream.yml` is only a proposal, and a change to the pinned set needs interactive consent that shows the diff (refused under -y, a non-TTY or HACF_AUTONOMOUS=1) (R12).
- Anti-rollback: the fetch uses `--filter=blob:none` (not `--depth=1`) so ancestry can be tested; an upstream commit that does not descend from the manifest's `installed_commit` is refused without `--allow-downgrade` (R25).
- Consent log: `.agents/logs/update-consent.jsonl` records timestamp, URL, commit, interactive-or-flag and OS user. `--allow-unsigned` is refused under HACF_AUTONOMOUS=1 or without a TTY, so no unattended CI update is possible while releases are unsigned (accepted, R25).

<a id="migration"></a>
## Migration of existing installs

Tool: .agents/scripts/hyper_migrate.py (S13a, S13b). Entry points: /hyper-update (S13c), `HACF-install.sh --migrate`, or a manual run. Subcommands: detect, plan [--json] [--include-archived], apply [--yes] [--allow-dirty] [--no-chain] [--include-archived], verify, rollback --journal [--force], move-slice.

G5 EXCEPTION (R25): the migration may move, fold and remove files without a manifest or hash match because it is consented, journaled and backed up; G5 states this exception.

CONFIRMATION (R25): `apply` requires a TTY confirmation. `--yes` skips it but is refused under HACF_AUTONOMOUS=1. An agent shell can still pass --yes, so the rule is procedural there and is documented as such.

DETECT (read-only). Signals:
- flat compiled or archived MiniPRDs
- docs/adr dirs
- non-template memory files
- a ledger with no schema key or that does not parse
- a missing scaffold
- missing HACF blocks
- missing firewall rules
- legacy field notes in activeContext

PLAN: a deterministic action list (golden-tested) plus a human summary. No writes.

PREFLIGHT:
- inside a git work tree; not mid-merge or mid-rebase
- touched tracked files are clean (or --allow-dirty)
- migrate.lock acquired
- `hyper_scaffold check` finds no foreign root items; a foreign item stops apply with a report

BACKUP:
1. Ensure the .gitignore block exists.
2. Write .agents/state/migrations/<UTC>/backup/ with spec.tar.gz, adr.tar.gz, memory.tar.gz, instructions.tar.gz and SHA256SUMS.
3. Verify SHA256SUMS with Python hashlib, not `sha256sum -c`, which macOS lacks (R25, R24).
This location is ignored, never committed, and exempt from the 30-day .agents/.backup retention (fixes the hyper_update_verification.py:112-125 interaction).

APPLY (idempotent; each step journaled in .agents/state/migrations/v0.6.0.json with inputs, outputs, shas and the legacy-to-new map):
1. Scaffold.
2. Classify SuperPRD*.md by heading text, regardless of numbering:
   - two or more baseline headings: BASELINE
   - an 'Execution Checklist' or 'Technical Specifications' heading: FEATURE (e.g. HACF's hyper-update SuperPRD uses '### 4.3 Execution Checklist')
   - otherwise unclassified: ask, or skip and report when non-interactive
   - BASELINE goes to root SPEC.md when it is absent or still the scaffold default; otherwise report it
3. FEATURE becomes a mission:
   - slug from the H1; id from the escape band or release-X.Y.Z; prefix OPR
   - membership only from explicit checklist evidence: an exact filename, or an exact normalized '**MiniPRD: <name>**' matching MiniPRD_<name>.md or MiniPRD_<name>_AUDITED.md
   - twins (R38): when spec/compiled/MiniPRD_<name>.md and spec/archive/MiniPRD_<name>_AUDITED.md both exist, the archived copy is the slice (audited with --include-archived). The compiled twin makes no slice; the plan lists it as a twin of the archived file, and step 10 moves it with the converted compiled files, so nothing is overwritten. Without --include-archived the twin still makes no slice, and the plan says that --include-archived imports the archived copy. (HACF's own spec/compiled keeps byte-identical pinned copies of finished slices, which a Replan restores for `post-resolve --repin`.)
4. Unreferenced MiniPRDs go to legacy-unassigned.
5. MiniPRD to slice:
   - order: checklist, then NN, then lexical; the slice dir is `<NN>-<slug>` with the file's NN when every slice of the mission has a valid, unique rig ordinal (1 or more), else every slice of that mission takes its 1-based position, because rig refuses ordinal 0 (R45)
   - `hyper_scope.py slice create` with `--intent` = the legacy H1 text after `# MiniPRD: ` (else the H1 text after `# `, else the `<name>` of `MiniPRD_<name>.md`), `--legacy-source <repo-relative path>` and `--legacy-order <n>` (R37)
   - node_id and parent from the header
   - hacf_status compiled, or audited for archived files with --include-archived
   - archived slices get PROOF.md and proof/00-legacy-audit.md (C1, artifact_type qa, verdict PASS, candidate_sha from the commit carrying 'HACF-Slice: <id>' else 'legacy-unrecorded', money_evidence citing the archive path). When such a commit exists, the proof also records `swimlane_sha` with the same value; proof checks treat an unreachable candidate_sha that has swimlane_sha as historical (a warning), not an error, so a squash landing does not break them (R22)
   - depends_on chains to the predecessor unless --no-chain
   - heading conversion (every missing slice heading added, `## Intent visual` included, R29); manifests via hyper_scope and hyper_openrig
   - mission status via `hyper_openrig.set_mission_status()` (R29): complete when every slice of the mission is audited (a historical mission), active when any slice is past compiled, else planned (no write)
6. Ledger: repair-text if needed (diff, confirm), migrate-v2, associated_file through the journal, spec_refs (`hyper_ledger.py ensure-node --spec-refs <slice dot-ID>` on each node whose associated_file moved, R28).
7. Lore: `import --adr-dirs` with --context for the docs/adr dirs the scan selects (R25): it walks **/docs/adr excluding .git, node_modules, .venv and vendor, and skips gitlinks (`git ls-files -s` mode 160000), untracked trees and nested repos; an interactive run asks per directory, and a non-interactive run imports only the project root's docs/adr. Status mapping:
   - proposed -> PROPOSED
   - accepted -> PROPOSED with legacy.status accepted (approved later one at a time, D7; Q36)
   - deprecated -> RETIRED
   - 'superseded by ADR-NNNN' -> SUPERSEDED
   - none -> PROPOSED
   Bodies of PROPOSED imports go to lore/proposed/<ID>.md; bodies of RETIRED and SUPERSEDED imports go to the immutable lore/canon/<ID>.md, where every node that has held canon status keeps its body (R8). Each moved file leaves a per-file redirect stub (R25); failed files are left in place.
8. Memory:
   - productContext goes into a migrated block in SPEC.md
   - systemPatterns becomes a PROPOSED canon in lore/proposed/
   - template-identical files are removed; activeContext is kept
   - 'Field Notes (legacy)' entries are appended to the mapped mission NOTES.md and removed from activeContext
9. Blocks and firewall. AGENTS.md, CLAUDE.md and GEMINI.md follow the unknown-sha whole-file rule (#installer FLOW step 8, R9): never appended to; ask (replace with `.v059.bak`, or a `<STEM>.hacf-v0.6.0.md` sidecar), and the sidecar under -y or a non-TTY.
10. Converted legacy compiled files, and the compiled twins of step 3 (R38), are moved via `archive_specs.py --paths --label v060_migrated`. Archived inputs stay where they are.

VERIFY: hyper_spec lint --all, hyper_ledger validate, hyper_lore validate, hyper_firewall check, and `rig scope --workspace . audit` per mission when rig 0.6.13 is present. The report goes to the journal.

IDEMPOTENCY: hash checks on every step. A re-run is a no-op; a partial run resumes. Hash drift caused by a human stops the step.

ROLLBACK: verify SHA256SUMS (Python hashlib); hash every file it would restore or delete against the post-apply journal and refuse on drift unless --force, listing the drifted paths (R25); restore the tarballs, delete only unchanged created files, remove added blocks, report what could not be restored.

RESOLUTION WITHOUT THE JOURNAL (it is machine-local): `hyper_spec.py resolve` scans hacf_legacy_source in missions/**/SPEC.md, including closed/, so teammates and CI can resolve old paths.

DEPRECATION:
- Flat dual-read with a warning in every pipeline skill and script through v0.6.x; removal in v0.7.0, no earlier than 2027-01-31.
- /hyper-resolve writes flat output only as a collision fallback.
- First upgrade from v0.5.9: the old skill text copies upstream skills and scripts. Upstream ships no spec/ or tests/, so the rmtree path (hyper_update_core.py:284-293) and the copy text (SKILL.md:41-49) become no-ops. The new skills run in legacy mode and print the migrate hint. The copied v0.6.0 scripts run without .agents/config/* (#installer MISSING CONFIG, R24).
- A saved v0.5.x HACF-install.sh aborts at `cp $TMP_DIR/tests` under set -e, after copying the system dirs. The CHANGELOG and README say to re-download the installer; the resulting state is dual-read-safe.

<a id="firewall"></a>
## Agent firewall

SOURCE: .agentignore (gitignore syntax). No tool reads it natively today.
Renderer: `hyper_firewall.py render [--ides] [--dry-run] | check | merge-settings <fragment>`. Run by the installer, /hyper-update and the migration. pre-commit runs `check` when .agentignore or a rendered file is staged.

CLAUDE CODE (.claude/settings.json, project scope; https://code.claude.com/docs/en/permissions):
- Read/Edit rules are gitignore-style; '/path' is relative to the settings root.
- Precedence: deny > ask > allow.
- Read/Edit denies also apply to recognized Bash file commands (cat, head, tail, sed, tee) and to redirect targets.

Rendered permissions.deny from the shipped .agentignore:
- Read and Edit (/spec/archive/**)
- Read and Edit (/tests/candidate_outputs/**)
- Read(/**/node_modules/**), Read(/**/.env), Read(/**/__pycache__/**), Read(/.git/**)

Fixed protected-write denies:
- Edit(/spec/compiled/architecture.yml)
- Edit(/lore_hypergraph.yml), Edit(/lore/CANON.md), Edit(/lore/canon/**) (immutable canon files, R8)
- Edit(/missions/*/mission.yaml), Edit(/missions/*/slices/*/slice.yaml)

permissions.ask: Bash rules for hyper_lore approve, reject, retire, reopen and review, in python3 and python forms.

hooks.PreToolUse (matcher Bash): `${CLAUDE_PROJECT_DIR}/.agents/scripts/hyper_gate_hook.py` (in engine sessions, the engine-generated settings point at a base-revision copy by absolute path, R4). Behavior (R6, R7):
- Fail closed: the whole hook runs inside a catch-all. An exception, unparseable input JSON, or a missing or unknown permission_mode exits 2 with a reason. Exit codes other than 0 and 2 are non-blocking in Claude Code, so the hook never exits 1.
- Matcher: the command is tokenized with `shlex`, `cd` is resolved, and the hook matches invocations of the hyper_lore script or module (path basename, `python3 -m`, `runpy`) whose subcommand is a gated verb (approve, reject, retire, reopen, review). Non-gated verbs (propose, note, stale, validate) and text inside quoted arguments (titles, commit messages) do not match. A command that mentions hyper_lore but cannot be parsed gets ask, or deny where ask is unavailable.
- Modes: ask in default, acceptEdits and auto (a hook's ask also forces a prompt in auto mode, hooks doc); deny via exit 2 in dontAsk, bypassPermissions and plan, with the reason "switch to default or acceptEdits (Shift+Tab) and retry" (R7). The hook input carries permission_mode; no other script learns the mode (R7).
- Ticket: on a gated match the hook writes a single-use ticket under .agents/state/lore/tickets/, bound to verb + id + nonce, with a 120 s TTL; hyper_lore.py refuses a gated verb without a matching unexpired ticket and consumes it, so script files, tests, make targets or check.sh lines that call approve fail unless the ticket is forged on purpose (R6). Tools without a PreToolUse hook use the TTY confirmation fallback (#lore GATE FLOW step 8).
- Reason text: the ID, title, warrant and every id the approval supersedes, retires or re-anchors (R23), with control characters (including ANSI sequences) stripped and the length capped.
- The hook needs its exec bit; S11's tests cover a crash, a missing warrant file, an unknown mode, ANSI in a title, the RT-75 over-match cases and each RT-74 evasion.

SUPPORTED MODES (R7): bootstrap execute and audit sessions run in any permission mode; lore approvals need default or acceptEdits.

Pattern translation:
- 'dir/' with an inner slash -> /dir/**
- bare 'dir/' -> /**/dir/**
- 'name' -> /**/name
- a leading '/' is kept
- '!' negations are warned about and skipped (conservative)

Merge behavior:
- Invalid JSON aborts with no write.
- Missing HACF rules and hooks are added.
- Only manifest-owned stale entries are removed.
- All user keys and rules are preserved (statusLine, env, attribution, hooks, allow/deny).
- Atomic write with a backup. settings.local.json is never touched.
- `check` warns about broad allows that shadow gates or firewall intent, e.g. Bash(python3 *) in .claude/settings.local.json:7 and Bash(git *) in .claude/settings.json:8.

CORRECTED LIMITS (stated in AGENTS.md and the render report):
Rules do NOT cover:
- Python or Node subprocesses that open files themselves
- commands that read files without naming them, e.g. `grep -r` from a parent dir
- Read and Edit denies in bypassPermissions, if the S11 live test shows Claude Code does not enforce them there (the recorded result goes into AGENTS.md's stated limits, R7)
The lore gate's remaining bypasses are a deliberately forged ticket and direct Python writes to lore_hypergraph.yml (R6).
They are not a security boundary. OS-level enforcement exists only in the engine's sandboxed phases, plus the engine scope check.

LIVE TEST (S11; consent; captures per #verification CANDIDATE ARTIFACTS, R27): the hook asks or denies by mode; `cat spec/archive/x` is denied; Read and Edit deny-rule enforcement in bypassPermissions (R7); and, in default and bypassPermissions, whether Bash commands that name archived paths still run: `python3 dev/scripts/v060_gate.py finish ... --miniprd spec/archive/...` and `python3 .agents/scripts/archive_specs.py --miniprd ...` (RT-92, R7).

OTHER TOOLS (selected IDEs only). A managed block '# BEGIN HACF MANAGED BLOCK: agentignore' (grammar in #installer MANAGED BLOCKS, R26) with patterns verbatim; user lines preserved. These tools have no PreToolUse hook, so gated lore verbs need the TTY confirmation (R6). Each file is labeled in the report:
- Roo: .rooignore. Roo's tools block reads and writes per the Roo docs; not shell.
- Cursor: .cursorignore, best-effort.
- Windsurf: .codeiumignore, indexing filter (UNVERIFIED).
- Antigravity / Gemini CLI (HACF id antigravity): .geminiignore, a context filter for tools that support it (geminicli.com/docs/cli/gemini-ignore). Gemini CLI was replaced by Antigravity CLI for unpaid users on 2026-06-18; Antigravity's own mechanism is UNVERIFIED.
- Cline: nothing rendered. .clineignore is 'deprecating soon' and 'was never designed to prevent access' (docs.cline.bot/resources/deprecations). The report points to Cline's Block Ignored File Access plugin, which uses .gitignore and is not a boundary.
- Copilot, Zed: AGENTS.md mandate only.

HACF repo only: deny Read(/PRDs/v0.6.0/**) so slices never consult the fabricated pack. Not shipped.

<a id="skill-change-matrix"></a>
## Skill and document change matrix

PIPELINE SKILLS (each edited exactly once)
- hyper-execute SKILL.md, S06:
  - resolve both layouts
  - activeContext plus hacf_status memory check
  - autonomous gates
  - ensure-node --from-spec
  - updater --from-diff (positional in legacy)
  - progress (v6) or notice (legacy)
  - notes: mission or --legacy
  - python3 instead of python (:64)
  - remove emoji tracking (:59)
- hyper-audit SKILL.md, S06:
  - review-template plus apply-review (replaces :31-35); a sub-agent's JSON patch is written to a temp file and applied only through `hyper_ledger.py apply-review`, never hand-merged (R27)
  - an `unmapped` list from `--from-diff` is a finding: register a node, or accept the gap in writing in the audit report (R21)
  - Phase 2 novel tests without fixtures record novel_fixture_needed, and their outputs go to tests/candidate_outputs/<SID>/ until the user promotes them into dev/tests/fixtures/ (R27)
  - Phase 3.5 lore gate
  - proof plus progress (v6); in autonomous mode the verdict goes only to the outbox audit.json, and the engine writes PROOF, proof files and candidate refs (R1)
  - archive_specs --miniprd (replaces :41-44)
  - bootstrap note (Q26, R19): an S06 audit that falls back to `git show 984ca46:.agents/skills/hyper-audit/SKILL.md` appends `Fallback used: hyper-audit@984ca46` to its MiniPRD's Operating Notes prose, above the block
- hyper-lore-approve SKILL.md and META.yml (new), S06: lists every id an approval supersedes, retires or re-anchors (R23); in bypassPermissions, dontAsk or plan it tells the user to switch to default or acceptEdits (R7); ends by recommending a lore-only commit without committing (R23).
- hyper-architect, hyper-redteam, hyper-resolve, S07a: resolve fixes :36 (bare SuperPRD.md, template path) and :38 (python). Architect and redteam treat CANON, stale CANON (until retired or superseded) and PROPOSED nodes with `origin: user-decision` as constraints (R23).
- hyper-discover, hyper-baseline (:18, :25), hyper-grill-docs (SKILL.md, ADR-FORMAT.md, META.yml), hyper-status, S07b. Discover runs `python3 .agents/scripts/hyper_ledger.py init` when no ledger exists (R21).
- hyper-update SKILL.md and META.yml, S13c.

SUPPORTING SKILLS (S14, once each): hyper-clear, hyper-sop, hyper-handoff (+META), hyper-troubleshooting (also fixes the python call at :50 and the git-checkout advice for a gitignored ledger), hyper-process-document, hyper-refresh-memory, hyper-session-update, hyper-document, hyper-consult-cto, hyper-contextualize (+META), hyper-create-issue, hyper-deepdive, hyper-peer-review, hyper-stitch-design; hyper-publish META, plus exactly the four bare `python` commands in its SKILL.md changed to `python3` (R47).
Unchanged: hyper-init, hyper-co-research, hyper-learning-opportunity, hyper-new-workflow, hyper-prompt-engineer, hyper-template-architect, hyper-tutorial*, hyper-create-skill.

BRIDGES:
- S06: execute and audit (.claude, .windsurf) plus the new lore-approve bridges.
- S07a: resolve.
- S07b: discover, baseline.
- S14: process-document.
- S15a1: hypergraph-agent rules in .cursor/rules, .clinerules, .roo/rules and .windsurf/rules.

SCRIPTS:
- S00: pre-commit (staged-only lint and the handoffs rule; its only edit); pre-push (refuses remote refs outside refs/heads/ and refs/tags/ and any sha reachable only from swimlane/* or research/*, R19); `_runtime.py` (new: Python and PyYAML floor, timestamp helper, R19); `ruff format` plus safe `ruff check --fix` on exactly the 12 legacy scripts of R13 (hyper_update_verification, hyper_orchestrator, hyper_daemon, hyper_update_merge_engine, hyper_resolve_conflict, token_budget_checker, token_budget_reconciler, semantic_graph_merger, archive_specs, hypergraph_updater, hyper_update_core, model_router), with no behavior change.
- S01a: hyper_ledger (new; includes `init`, R21). S01b: hyper_ledger. S01c: hypergraph_updater (`unmapped` list and the "Hypergraph not found" message naming `hyper_ledger.py init`, R21), semantic_graph_merger (guard and validation).
- S02: hyper_openrig. S03a: hyper_spec. S03b: hyper_scope, archive_specs.
- S04: hyper_codegraph, hypergraph_updater (enrichment).
- S05a and S05b: hyper_lore.
- S11: hyper_firewall, hyper_gate_hook (fail closed, tokenized matcher, ticket writer, R6).
- S06: hyper_gates (`mode` reports autonomous only for HACF_AUTONOMOUS=1, R7).
- S08: hyper_executors, model_router, token_budget_checker, token_budget_reconciler.
- S09a1 and S09a2: hyper_engine, hyper_failures.
- S09b: hyper_orchestrator, hyper_daemon, hyper_resolve_conflict, semantic_graph_merger (rotation); hyper_fix deleted.
- S10: compaction_engine and interrupt_detector deleted.
- S12a: hyper_blocks, hyper_scaffold. S12b: HACF-install.sh. S12c: HACF-uninstall.sh.
- S13a and S13b: hyper_migrate. S13c: hyper_update_core, merge_engine, verification.

CONFIG AND SCHEMAS:
- S04: code_graph.defaults.yml. S08: orchestration.yml, heuristic_rules.yaml, META_template.md.
- S12c: legacy_file_hashes.json. S13c: upstream.yml.
- S03b: schemas/openrig/*.md and NOTICE. S06: autonomy_prompt.md. S10: activeContext_template.md.
- S15b: deprecated templates deleted.
- project-templates/DEVELOPMENT.md: content only, in S15a2.

INSTRUCTIONS AND DOCS:
- S15a1: AGENTS.md (×2), CLAUDE.md (×2), GEMINI.md (×2), IDE rules.
- S15a2: README (only here), docs/*, skills-info, Agentic-Memory, DEVELOPMENT, docs/Lore.md.
- S10: .agents/rules/git-workflow.md.
- S15b: CHANGELOG.

DATA AND CONFIG:
- S11: .agentignore header, HACF .claude/settings.json.
- .claude/settings.local.json untouched; `firewall check` flags its Bash(python3 *).
- .agents/memory/activeContext.md: bootstrap section (B8), the C4 precedence sentence (R15, R16), gate appends, S13b migration.
- productContext and systemPatterns deleted in S10.
- .provenance_staging deleted in S09b.
- .gitignore: S00, S15b.
- .gitattributes: never tracked; merge-driver attributes live in `<git-common-dir>/info/attributes` (R10).
- pyproject.toml: S00 adds per-file-ignores freezing the residual ruff codes of the 12 legacy scripts (R13).
- dev/design/v0.6.0 and dev/scripts/v060_gate.py: bootstrap, plus the C4 gate amendment before post-resolve (R16); tests in S00.

<a id="verification"></a>
## Verification

PER-SLICE GATE, bootstrap (`python3 dev/scripts/v060_gate.py finish <SID> --miniprd <path>`). The gate records its own commits in git refs: post-resolve sets refs/hacf/v0.6.0/post-resolve, and each finish sets refs/hacf/v0.6.0/slices/<SID> (a re-finish keeps the commit it replaces as refs/hacf/v0.6.0/superseded/<SID>/<sha>). A slice is complete when its ref exists, is an ancestor of HEAD and names a commit carrying `HACF-Slice: <SID>`. The scope base is the newest of the post-resolve commit and the completed slice commits. A commit carrying an HACF-Slice trailer that no ref names is warned about and is neither complete nor a scope base, so its changes fall into the next slice's scope check. Refs make a forged commit visible; they cannot prevent one. Every post-resolve commit must have exactly one parent and change only paths post-resolve may commit. When refs/hacf/v0.6.0/post-resolve is missing but its commit is in HEAD's history, the gate prints the `git update-ref` command that records it again; the user runs it only if the ref was lost, not removed on purpose. Every git command the gate runs ignores git replace objects (GIT_NO_REPLACE_OBJECTS=1), and status, post-resolve and finish warn while refs/replace/ has refs.
(1) Preconditions, checked before any write. HEAD is the branch swimlane/v0.6.0 (another branch or a detached HEAD is refused). The index has no assume-unchanged, skip-worktree or intent-to-add (git add -N) entries and no grafts file exists; no flag waives these, and the refusal names the command that clears each one. When HEAD is a finish of this SID that did not record its slice ref (interrupted after its commit), finish refuses and says to run `git reset --soft HEAD^` and finish again. refs/hacf/v0.6.0/post-resolve exists, is an ancestor of HEAD and is the newest commit whose subject starts with the post-resolve prefix; every later post-resolve commit needs an HACF-Repin trailer (an amended or hand-made post-resolve commit is refused). --miniprd names a regular file (not a symlink, not reached through a symlinked directory) inside the repo under spec/archive/ named MiniPRD_<NN>_<Slug>_AUDITED.md, NN equal to the block's nn; only the final slice may also pass its archived path without _AUDITED (S15b's migration moves its MiniPRD with archive_specs.py --paths), and a spec/compiled/ path is always refused. While a spec/compiled/MiniPRD_<NN>_*.md for that NN still exists, finish refuses (the audit moves the file; it does not copy it). The block is parsed with duplicate YAML keys refused and must equal the block that declares the same slice in the post-resolve commit, unless --accept-block-change REASON is given; even then slice and nn cannot change. A SID whose slice ref already exists (finished, or reset away) is refused unless --refinish REASON is given.
(2) Ledger: it is a regular file (not a symlink); it parses; edges are lists; node ids and per-node keys are not dropped versus the scope base's ledger, except removes_nodes declared in the block. A base ledger that exists but does not load is a problem; HEAD's ledger is used only when the base has none. From S01a on, also `hyper_ledger.py validate --against-ref <scope base sha>` (non-regression). Commits between the scope base and HEAD without a recorded gate ref produce a warning; their changes are still scope-checked because the diff runs from the base.
(3) Guards, at every finish: each guards.json entry must match; a mismatch, an invalid entry or a guarded path that is a symlink or not a regular file is a problem that no flag can waive, and a missing guarded file passes only if a regular file under spec/archive/** has the guarded sha256 (the gate prints where it moved). guards.json itself must be a regular file, byte-identical to its copy in the post-resolve commit.
(4) Validators: from S05a on `hyper_lore.py validate`; from S03a on `hyper_spec.py lint --all`. After the C4 amendment the gate also runs the scope-base copy of each validator against the working tree; see operating-procedure.md#gate-amendment-c4 (R16).
(5) Scope: paths changed since the scope base (tracked diff plus untracked, plus ignored files under spec/compiled and spec/archive when track_spec is true) must match the block's files globs. Always in scope: the ledger (its content is checked by (2)); this slice's own MiniPRD paths (spec/compiled/MiniPRD_<NN>_*.md and spec/archive/**/MiniPRD_<NN>_*.md); .agents/memory/activeContext.md (a regular file, not a symlink) outside its procedure section. That section, from '## v0.6.0 Bootstrap Operating Procedure (read first)' through the '<!-- END v060-completed -->' line, must be byte-identical to the scope base (a retry after an interrupted finish may already hold this SID's own completed line), or the gate reports '.agents/memory/activeContext.md (procedure section)' as out of scope. Only a files entry that names .agents/memory/activeContext.md exactly lets the section change; a wildcard that matches the file does not. Everything else, including SuperPRD_v0_6_0.md, other slices' MiniPRDs, the rest of spec/archive/** and spec/active/**, must match a glob. Out-of-scope paths exit 1 unless --accept-scope REASON is given; the commit body then lists every accepted path. When every changed path is bookkeeping (the ledger, activeContext.md, this slice's MiniPRD paths), the gate refuses unless --allow-empty REASON is given.
(6) dev/scripts/check.sh, once it exists: `uv run --locked pytest` under the uv 3.11 venv AND `/usr/bin/python3 -m pytest` under 3.10 (PyYAML 5.4.1), plus ruff check and format --check on changed files. The 3.10 leg fails, never skips, when `/usr/bin/python3 -c 'import pytest, yaml'` fails, and it tests the same binary it runs (R19). Every `uv run` uses --locked, so check.sh never rewrites uv.lock (R19). Ruff honors the per-file-ignores baseline S00 writes for the 12 legacy scripts (R13). Deleting check.sh after it existed at the scope base is refused. The gate fingerprints HEAD, the branch, the index (entries and their flags) and every validated path, the ledger, activeContext.md, guards.json, each guarded path, the --miniprd file and check.sh before the checks run, and refuses, with nothing written, if the checks changed any of them or changed any other path that git add would pick up, in scope or not. check.sh must be a regular file, not a symlink. Just before the checks, the gate also builds the tree its own staging would make of the checked work tree, in a temporary copy of the index, so index-only state such as S15b's `git rm -r --cached spec` is part of it.
(7) Commit: snapshot the index (git write-tree), append to the activeContext completed list, stage, check that every staged path was validated and that the staged tree differs from the tree built before the checks only by the line the gate added to activeContext.md, and commit (git commit --cleanup=verbatim) with the HACF-Slice trailer plus one trailer per waiver (HACF-Scope-Accepted, HACF-Block-Changed, HACF-Empty, HACF-Refinish). On any failure or interruption (SIGTERM and SIGHUP included) before the commit lands, the gate writes activeContext.md back and restores the index snapshot (git read-tree), so index-only state such as S15b's `git rm -r --cached spec` survives. A failure exits 1; after SIGTERM or SIGHUP the gate exits 128+N (143 or 129) once the rollback is done, and Ctrl-C ends with Python's KeyboardInterrupt. After the commit, it compares the commit's parent, tree, paths and message with what it staged and wrote (hooks can change them); on a mismatch it records no ref and exits 1. It advises `git reset --soft HEAD^` only while HEAD is its commit on swimlane/v0.6.0 on top of where it started; otherwise it points to git reflog. Otherwise it sets refs/hacf/v0.6.0/slices/<SID>. SIGTERM and SIGHUP stay trapped until the ref is set; an interrupt after the commit says whether the slice is finished or how to recover, and exits 128+N.
Waivers (--accept-scope, --accept-block-change, --allow-empty, --refinish, and --repin for post-resolve) need a REASON that, after whitespace collapsing, is non-empty, contains a letter or digit and no control or format characters, and the user's approval in AskUserQuestion, quoted as REASON. The gate cannot verify that approval (procedural), so it prints every waiver it applies and `status` lists every waiver trailer in history (with the accepted paths) together with the current branch and any unrecorded gate-looking commits.
`restore-ledger` (swimlane/v0.6.0 only) restores HEAD's ledger after saving a copy to the backup dir.
C4 AMENDMENT (R16, R18): before post-resolve, the bootstrap amendment C4 adds to the gate the --repin rule for finished slices (R14), the Overrides-banner check (R15), the scope-base validator run and the --accept-validator-change waiver (R16), the --out-of-order waiver, hook-free gate commits, tested-bytes-equal-committed-bytes checks, symlink and gitlink refusal, pinned-SID slice refs, lookalike-heading refusal, glob hygiene, process-group cleanup, the post-resolve seed cross-check, `claude --version` in commit bodies (R18) and a git bundle after each finish (R19). The normative spec is operating-procedure.md#gate-amendment-c4; where it differs from (1)-(7) above, it wins.
Live tests are opt-in by marker and env var and need consent: openrig (rig or npx 0.6.13), codegraph (0.12.1), claude (HACF_LIVE_CLAUDE=1, spends money), sandbox.

CANDIDATE ARTIFACTS (R27):
- Live runs in S02, S04, S08 and S11 are verbatim captures written by the test harness, never by the model: argv, stdout, stderr, exit code, tool version and UTC date, under dev/tests/fixtures/recorded/<SID>/. Verdicts are computed by the test from the capture and recorded in STORY-0002 (S02) or in the slice's audited Operating Notes prose (S04, S08, S11).
- Declined consent: synthetic fixtures under dev/tests/fixtures/synthetic/<SID>/ with a `synthetic: true` header; the item stays UNVERIFIED and the R20 flags apply (`--unverified-executor`; an "unverified" code-graph backend).
- Audit novel_fixture_needed outputs go to tests/candidate_outputs/<SID>/ (gitignored, agent-blocked) until the user promotes them into dev/tests/fixtures/.
- Sub-agent review patches are applied only through `hyper_ledger.py apply-review`.
- The LLM-compiled MiniPRDs are checked by the C4 seed cross-check at post-resolve.

STATIC CHECKS (S00+):
- AST scan for shell=True, os.system, eval and exec (currently clean), plus argv lists of the form ['bash'|'sh', '-c', <non-literal>] (R19)
- 3.11-only API scan: tomllib, datetime.UTC, StrEnum, typing.Self, ExceptionGroup, except*, TaskGroup, plus hashlib.file_digest, contextlib.chdir, asyncio.timeout, operator.call, logging.getLevelNamesMapping, typing.Never, LiteralString, assert_never, reveal_type, Required, NotRequired, dataclass_transform, enum.verify, enum.member and enum.nonmember (R19); no new dev dependency
- runtime guard: every new or modified `.agents/scripts` entry point imports `_runtime.py` (Python >= 3.10, PyYAML >= 5.4); every timestamp parser accepts `Z` and `+00:00` under /usr/bin/python3 3.10, and writers emit `+00:00` (R19)
- lint baseline: ruff check and format --check pass on changed files; the 12 legacy scripts are formatted and their residual codes frozen in pyproject per-file-ignores (R13)
- no anthropic import in the engine; no stale model ids; no bare `python ` in skills or docs
- NEVER_COPY test; config schema checks; SKILL.md frontmatter; balanced blocks
- AGENTS.md size in bytes: not above the v0.5.9 baseline, and the install template with an OpenRig block stays under 32 KiB (Codex cap) (R24)
- release DAG import lint (S15b)

END-TO-END (dev/tests/e2e/test_e2e_v059_upgrade.py) on the fixture builder: flat and archived specs, broken ledger, multi-context ADRs, filled memory, an OpenRig block, custom settings, user pyproject/uv.lock/LICENSE, collision variants, an OpenRig-only .agents/skills repo, a saved v0.5.x uninstaller.
1. Non-interactive upgrade install from a local bare clone (HACF_TEST=1 with HACF_REPO_URL, R12). Assert no downloads: failing shims for curl, wget, pip, pip3, uv, uvx, npx and npm come first on PATH, plus `unshare -rn` when available (R24). The OpenRig block is byte-identical; settings and selections are kept; MCP entries are merged only for the selected IDEs; a customized (unknown-sha) AGENTS.md is left untouched and a `AGENTS.hacf-v0.6.0.md` sidecar is written (R9); with PyYAML hidden by a shim python3, the installer aborts before any write. Consent under `bash -s < HACF-install.sh` reads /dev/tty (R24).
2. The plan equals the golden plan.
3. Apply. Assert: missions with chained slices and audited archived slices with C1 proofs; legacy-unassigned; SPEC.md from the baseline; ledger v2 valid; ADRs PROPOSED; canon proposal; firewall rules and hook; lint clean; no tracked .gitattributes, and the merge-driver attributes are in info/attributes (R10).
4. A re-run is a no-op.
5. Rollback is byte-identical. A rollback after a user edit to a migrated file refuses and lists the path unless --force (R25).
6. Re-apply.
7. Engine with the stub executor: DAG order; worktrees; integration commits with trailers; PROGRESS and PROOF; NOTES; staged ledger applied only after `land`; main checkout byte-identical before land; the git-metadata guard (R1); land refuses ledger drift (R5).
8. Gate scenario: the stub reports low_confidence on slice 01, so 02 is upstream_blocked and nothing is integrated.
9. Uninstall preserves user files. The no-manifest path uses legacy hashes.
10. Collision variants: exit 5, nothing written, legacy mode works.

OPENRIG (S02 gate; the release requires the daemon verdict or a waiver; captures per CANDIDATE ARTIFACTS):
- `npx -y --package=@openrig/cli@0.6.13 rig scope --workspace <fixture> audit --mission <m> --json`: zero high findings
- slice ls classification
- slice create keys and appended composition
- frontmatter survives rig rewrites
- `rig proof --workspace <fixture> add <slice-path> ... --name` is accepted with no proof_artifact_c1_invalid
- after each rig call, `git status --porcelain` lists only expected paths (the adapter always passes --name) (R22)
- a case with the repo nested inside a rig workspace root (R22)
- superseded slices closed into closed/
- STORY-0002 records `npm view @openrig/cli@0.6.13 dist.integrity` (R24)
- in the user's OpenRig: register-project, then the project appears in GET /api/scopes/projects or the TUI; the user-run spike also checks two projects with the same OPR ids (R22)

CODE GRAPH (consented; captures per CANDIDATE ARTIFACTS): index the fixture; detect_changes after an edit; --from-diff marks the caller's component; `cli <tool> --help` spellings match the defaults; uvx prefix execution works; S04 records how the shim verifies its native runtime (a pinned hash or not) as a finding (R24); a placeholder value starting with '-' is refused (R24).

CLAUDE (S08 and S11; consent; $0.25 budget; captures per CANDIDATE ARTIFACTS):
- JSON parses
- sandbox blocks writes outside the worktree and curl; in-worktree Edit succeeds; AskUserQuestion unavailable; Agent works
- a sandboxed Bash write to the worktree's `.git` pointer file and to `<git-common-dir>/` both fail; whether hooks run inside the sandbox is recorded (R1)
- S08 sets `max_tested_version` to the `claude --version` the test passed on, and a pass sets `executors.claude-cli.verified: true` (R20)
- the gate hook asks or denies by mode
- cat spec/archive/x is denied by the firewall
- S11: Read and Edit deny rules in bypassPermissions, and `v060_gate.py finish --miniprd spec/archive/...` plus `archive_specs.py --miniprd`, in default and bypassPermissions (R7)

GOLDEN BLAST RADIUS (R21): S01c tests `--from-diff` on fixture diffs in a temp repo, including the `unmapped` list. S15b's E2E runs it on the real S01c slice commit (expects hypergraph_updater and semantic_graph_merger) and the S13c slice commit (expects the four hyper_update* nodes).

DOGFOOD (S15b):
- a final `review-template` plus `apply-review` pass over every leftover needs_review node, then missions/release-0.6.0 (OPR.0.6.0) passes lint, ledger validate --strict (which fails on leftover needs_review, R21), lore validate and firewall check
- migrated slice proofs carry candidate_sha and swimlane_sha (R22)
- the "v0.6.0 Red Team resolutions" story is proposed (PROPOSED) under the next free STORY id (STORY-0004 since the S09a2 stop record took STORY-0003, R43), so the release holds one lore node more than before that step (15 as of R43); the user is offered optional one-at-a-time promotion of ADR-0001..0009 (R23)
- `git ls-files spec tests` is empty; no root .gitattributes is tracked (R10)
- rig audit has zero high findings if rig is present
- a fresh install from this tree matches target_layout
- landing: `--ff-only` or `--squash` (Q38, the user's choice at landing; a merge, not ff, if main moved during the build, R19), then check.sh on main

