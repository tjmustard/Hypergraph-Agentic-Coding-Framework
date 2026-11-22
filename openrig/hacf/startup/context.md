# Start (every seat on this rig)

1. Run `rig whoami --json`. It names your seat and your peers' sessions. Use those
   names; do not guess addresses. During launch a peer may not be listed yet: if one of
   the seats is missing, check `rig ps --nodes` again before telling anyone it is gone.

2. Find out which project you are working in, and which mode you are in. Run `pwd` and
   `git rev-parse --show-toplevel`:
   - **Project mode**: your working directory holds `.agents/skills/hyper-architect/SKILL.md`
     and equals `git rev-parse --show-toplevel`. The team works in this one project. The run
     record's project root must equal your working directory; if it does not, stop and tell
     the lead.
   - **Parent-folder mode**: anything else. Your working directory is the folder that *holds*
     projects, not a project itself, and the run record names the project.

   The run record at `~/.hacf/<rig>/current-run.md` names the project root, the feature and the
   stage that is current. The handoff row that woke you names the same things. **Never
   guess a project root**: if the record and the handoff disagree, or neither names one,
   say so and stop — that is the lead's call, and working in the wrong repository is the
   one mistake this team cannot undo.

3. Check that the project has the Hypergraph framework installed, because every stage
   below is defined by files it installs:

   ```sh
   cd <project root>
   ls .agents/skills/hyper-architect/SKILL.md spec/ AGENTS.md CLAUDE.md
   ```

   If `.agents/` is missing, the project has not been onboarded. Only the lead installs
   the framework; report it and wait. Do not improvise a pipeline out of this rig's
   guidance alone — the stage definitions live in the project.

4. Read the project before you read your instructions:
   - its `CLAUDE.md` and `AGENTS.md` — `AGENTS.md` carries the spec and ledger schemas,
     and those schemas are binding on your artifact;
   - the trailhead `AGENTS.md` names: `lore/MAP.md`, then `SPEC.md`, then the active mission
     under `missions/`;
   - the lore as data: `python3 .agents/scripts/hyper_lore.py list --json`. Binding nodes
     bind (`AGENTS.md`, "Binding Constraints (Lore)");
   - `.agents/rules/` — the always-on coding rules for this project's language;
   - the current state: the ledger `spec/compiled/architecture.yml`,
     `python3 .agents/scripts/hyper_spec.py status --json`, and whatever `spec/active/`
     holds right now.

   Then read **your own stage's** `.agents/skills/hyper-<stage>/SKILL.md` and follow it
   precisely. This rig's role file tells you where you sit in the sequence and what you
   owe the next seat; the framework's skill file tells you how to do the stage. Where
   they seem to disagree, the framework wins on the stage's content and the rig wins on
   who hands what to whom.

5. The **protocol** is already in your startup text above (rig spec `startup/protocol.md`): seat
   addresses, the handoff recipe, how to ask the person, and the two record files
   (`~/.hacf/<rig>/current-run.md` and `~/.hacf/<rig>/loops/hacf-loop-<NN-slug>.md`). It is checked against
   the installed CLI. Where it disagrees with your role file about a command, the protocol wins.

6. Read `CULTURE.md` if it is not already in your context. It is short and it is the team's floor.

7. Submit your startup proof: run the `rig startup-proof submit` command from the
   "OpenRig startup orientation challenge" in your startup text, exactly as given.

Then, unless you are the lead, **wait**. An idle seat is correct here. The lead brings
work through a queue handoff; nothing in this rig should be started because you found it
interesting. If a handoff is thin — no project root, no artifact path, no definition of
done — ask the lead for it in one line rather than inventing it.
