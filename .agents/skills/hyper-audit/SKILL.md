---
name: audit
description: Strictly verifies the codebase against a v6 slice SPEC.md or a legacy flat MiniPRD and reconciles the component ledger.
trigger: /hyper-audit [spec path or dot-id]
---

# ROLE: The Auditor Agent
Your objective is to verify newly written code against its strict requirements and reconcile the component ledger through its validated writer.

## INPUTS
1. The target spec: a v6 slice `SPEC.md` (path or dot-id) or a legacy flat `spec/compiled/MiniPRD_*.md`. Resolve it first:

   ```bash legacy
   python3 .agents/scripts/hyper_gates.py mode
   python3 .agents/scripts/hyper_spec.py resolve <spec> --json
   ```

   `layout` in the output is `slice` for v6 or `legacy`. Follow that branch below. `mode` prints `autonomous` only under `HACF_AUTONOMOUS=1`.
2. The component ledger, read through `hyper_ledger.py`.
3. The specific source code files recently modified by the Builder Agent.

## CRITICAL RULES
1. **No Scope Creep:** Evaluate code STRICTLY against the Acceptance Criteria and Negative Space in the spec. Do not suggest stylistic refactors outside of this scope.
2. **Fresh Context:** Read the source code directly from the disk. Do not rely on conversational memory.
3. **Operating Notes:** A `## Pipeline Operating Notes` section in the spec overrides this skill where they differ.
4. **Interpreter:** Call every script with `python3`.
5. **Placeholders:** `<spec>` is the path `resolve` reports (`resolve` itself takes the user's path or dot-id). `<SID>` is the slice id; for a legacy MiniPRD it is the `slice:` of its `hacf-bootstrap` block, else its file name without `.md`.
6. **Command blocks:** Fenced blocks tagged `legacy` hold the commands of the legacy branch, including those both layouts share. Blocks tagged `v6` hold the commands only a slice runs.
7. **Record a FAIL:** outside autonomous mode, on every FAIL verdict and before you return to the Builder, run `python3 .agents/scripts/hyper_spec.py progress --slice <spec> --event audit_failed`, so the slice's progress counts the failure.

## STATE MACHINE PHASES

### [PHASE 1: Contract Verification]
* **Action:** Analyze the modified code against the spec.
* **Output:** If it fails, generate an actionable `Punch List`, return it to the Builder, and HALT execution. If it passes, output `[VERIFICATION: PASSED]`.
* **Autonomous (engine):** on failure, write the verdict `FAIL` with the punch list to `$HACF_OUTBOX/audit.json` (format in Phase 4) and stop.

### [PHASE 2: Test Validation]
* **Action:** Verify Deterministic Tests pass. A Novel Test passes only against a human-approved fixture in the project's fixtures.
* **Novel test without a fixture:** record `novel_fixture_needed` with `python3 .agents/scripts/hyper_gates.py record --gate novel_fixture_needed --slice <SID> --reason '<test name>'`. Send the test's candidate outputs to `tests/candidate_outputs/<SID>/` from the command that produces them (the directory is gitignored and agents cannot read it). They stay there until the user promotes them into the project's fixtures.
* **Output:** Pass/Fail. If fail, HALT and return to Builder.
* **Autonomous (engine):** on failure, or when a fixture is missing, write the verdict `FAIL` to `$HACF_OUTBOX/audit.json` (format in Phase 4) and stop.

### [PHASE 3: Ledger Reconciliation (CRITICAL)]
* **Trigger:** Phases 1 and 2 passed.
* **Action:**
  1. List the nodes awaiting review and the changed paths no node covers. `<base>` is `HEAD` while the slice work is uncommitted. In an engine run the slice commit is `HEAD`, so use the base the prompt names, else `HEAD^`.

     ```bash legacy
     python3 .agents/scripts/hyper_ledger.py review-template --json
     python3 .agents/scripts/hypergraph_updater.py --from-diff <base> --json
     ```

     The second command also runs the lore stale hook, so it can change `lore_hypergraph.yml` and `lore/MAP.md`, even without `--apply`.
  2. **`unmapped` is a finding.** A non-empty `unmapped` list must be resolved: register a node for those paths, or accept the gap in writing in the audit report.
  3. Launch a sub-agent with the Agent tool (`subagent_type: "general-purpose"`). Give it the template's `instructions` and `patch_schema` and have it read each listed node's files from disk. It returns only the JSON patch, and it writes nothing.
  4. Check the patch against the code, write it to a temp file outside the repository (`mktemp`), then apply it. The patch is applied only this way and is never hand-merged:

     ```bash legacy
     python3 .agents/scripts/hyper_ledger.py apply-review --patch <patch_file> --slice <SID>
     ```

  * **Fallback:** if the sub-agent fails, draft the patch in the main context and apply it the same way.
* **Output:** Report the nodes reconciled and their changes. Output `[AUDIT COMPLETE & LEDGER RECONCILED]`.

### [PHASE 3.5: Lore Gate]
* **Trigger:** Phase 3 passed.
* **Action:** Select the nodes this slice proposed plus the stale nodes in its blast radius. `<radius_ids>` is the comma-joined `blast_radius` from Phase 3 (omit `--source` for a v6 slice):

  ```bash legacy
  python3 .agents/scripts/hyper_gates.py lore-check --slice <SID> --source <spec> --nodes <radius_ids> --json
  ```

  For each selected node, one at a time and with no batching:
  1. Run `python3 .agents/scripts/hyper_lore.py approval-request <ID>`. It prints the hashed body, the sha, the nonce and the ids the approval affects.
  2. Show the body, anchors, provenance and related nodes, and list in the question body every id the approval supersedes, retires or re-anchors. Use **AskUserQuestion** with exactly these options: 'Approve (becomes CANON)', 'Reject (kept in the graveyard)', 'Keep PROPOSED', 'Skip'. For a stale CANON node: 'Still valid', 'Retire', 'Keep stale'.
  3. On 'Keep PROPOSED', 'Skip' or 'Keep stale', run `python3 .agents/scripts/hyper_lore.py approval-cancel <ID>` and go to the next node. These answers never block the audit.
  4. Otherwise ask for the warrant in the user's own words and write it with the Write tool to `.agents/state/lore/pending/<ID>.warrant.md`.
  5. Run the gated command as one direct Bash call, so that the PreToolUse hook can issue its ticket. Never run it through a script, test or make target. `<verb>` is `approve`, `reject` or `retire`; 'Still valid' uses `review <ID> --still-valid` with the same two flags:

     `python3 .agents/scripts/hyper_lore.py <verb> <ID> --nonce <nonce> --warrant-file .agents/state/lore/pending/<ID>.warrant.md`
  6. If the hook denies because of the permission mode, tell the user to switch to default or acceptEdits (Shift+Tab) and retry.
* **Autonomous:** never approve and never ask. Run `python3 .agents/scripts/hyper_gates.py record --gate lore_decision_needed --slice <SID> --reason '<ID>'` for each selected node and stop this gate. It does not fail the audit, so continue to Phase 4.
* **Output:** Report each node's decision. Output `[LORE GATE COMPLETE]`.

### [PHASE 4: Close the Audit]
* **Trigger:** Phase 3.5 finished.
* **v6 interactive:** write the proof body to a temp file outside the repository (`mktemp`), then:

  ```bash v6
  python3 .agents/scripts/hyper_spec.py proof --slice <slice dir> --artifact-type qa --verdict PASS --candidate-sha auto --money-evidence '<one line>' --file <body> --name <NN>-qa.md
  python3 .agents/scripts/hyper_spec.py progress --slice <slice dir> --event audited
  ```

  `<slice dir>` is the directory holding the slice's `SPEC.md`, not a dot-id.
* **Autonomous (engine):** write the verdict with the Write tool to `$HACF_OUTBOX/audit.json`, and write nothing else in this phase: `{"slice": "<SID>", "verdict": "PASS", "punch_list": [], "notes": [...], "money_evidence": "<one line>"}`. `verdict` is `PASS` or `FAIL`, and a FAIL carries its `punch_list`. Never write PROOF, PROGRESS or candidate refs; the engine writes them after this phase.
* **Legacy:** there is no slice directory, so the first two commands only print skip notices. The third moves the MiniPRD to `spec/archive/MiniPRD_<Name>_AUDITED.md`, which removes it from future `/hyper-execute` runs:

  ```bash legacy
  python3 .agents/scripts/hyper_spec.py proof --slice <spec> --artifact-type qa --verdict PASS --candidate-sha legacy-unrecorded --money-evidence '<one line>' --file <body> --name <NN>-qa.md
  python3 .agents/scripts/hyper_spec.py progress --slice <spec> --event audited
  python3 .agents/scripts/archive_specs.py --miniprd <spec>
  ```

* **Output:** Output `[AUDIT CLOSED]` and inform the user the feature loop is complete.
