# Your first move (lead)

You start from the person's goal. It usually reaches you as a queue row from the kernel
operator; if a goal arrived with the launch, use it and do not ask for it again.

1. **Name the project root**, by the mode rule in `startup/context.md`:
   - **project mode** (your working directory holds `.agents/skills/hyper-architect/SKILL.md`
     and is the git top level): the project root is your working directory;
   - **parent-folder mode**: your working directory holds projects. The goal usually implies
     one of its folders; if it is not obvious, ask the person one question with your best guess
     attached — never work in a repository you inferred.
2. **Write the run record** at `~/.hacf/<rig>/current-run.md` — `<rig>` is the name
   `rig whoami --json` gives you (template in `protocol.md`, "The run record"). The directory
   does not exist on a fresh host, so create it first: `mkdir -p ~/.hacf/<rig>`. Fill every line
   the template names, including `Rig spec:` (the absolute path of the folder your seats
   launched from); add `## Per-run grants` or `## Environment` only when the person grants
   something or the project needs it (`guidance/lead.md`). This file is how every other seat
   finds the same project after a restart, and how you find it after a compaction. Keep it
   current or the team is lost.
3. **Check the project is onboarded** — `.agents/`, `spec/`, `AGENTS.md`, `CLAUDE.md`.
   Greenfield or brownfield without it: that is Phase 0/Phase -1, which is also yours.
   See your role file for the exact route.
4. **Dispatch the first stage** with a queue handoff. Then keep the sequence.

If no goal was supplied and the person is not there, propose the smallest sensible first
step from the project's own state and say you are proposing rather than starting.
