# Autonomous Run

You are running headless under the HACF engine. `HACF_AUTONOMOUS=1` is set, and `python3 .agents/scripts/hyper_gates.py mode` prints `autonomous`.

- **Nobody can answer.** AskUserQuestion is unavailable. At every gate, run `python3 .agents/scripts/hyper_gates.py record --gate <name> --slice <SID> --reason '<why>'` and stop as the skill's `Autonomous:` line says. Never guess an answer, and never accept low confidence.
- **Writes go to the outbox.** Ledger, lore and progress writes go to `$HACF_OUTBOX` only. The HACF scripts do this on their own while `HACF_OUTBOX` is set, so use them and never write those files yourself.
- **Never run a gated lore verb.** That covers `approval-request`, `approve`, `reject`, `retire`, `reopen` and `review`. Lore decisions wait for a person.
- **The audit verdict goes only to `$HACF_OUTBOX/audit.json`.** Never write PROOF, PROGRESS or candidate refs. The engine writes them after the phase.
- **Never move git state.** Never run `git commit`, `git push`, `git checkout` or another branch-moving command. The engine makes every commit.
