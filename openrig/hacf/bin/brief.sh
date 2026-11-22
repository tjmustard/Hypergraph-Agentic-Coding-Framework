#!/usr/bin/env bash
# usage: brief.sh execute|audit <slice> <SID> <START_SHA> [extra-notes-file]
#
# Prints the brief the lead hands a just-cleared execute or audit seat. Everything
# run-specific comes from the rig that runs it: the rig name from `rig whoami --json`, and the
# project root, feature, Base, branch, rig spec path, grants and environment from that rig's
# run record, ~/.hacf/<rig>/current-run.md (startup/protocol.md, "The run record"). The script
# names no rig, project, person, interpreter path or grant of its own.
#
# <slice> is a slice directory or its SPEC.md, relative to the project root, or NN_Name for
# the legacy flat layout's spec/compiled/MiniPRD_NN_Name.md. Exit 2 on any refusal, with one
# line on stderr saying why.
set -euo pipefail

die() { echo "brief.sh: $*" >&2; exit 2; }

USAGE="usage: brief.sh execute|audit <slice> <SID> <START_SHA> [extra-notes-file]"
[ $# -ge 4 ] && [ $# -le 5 ] || die "$USAGE"
KIND=$1 SLICE_ARG=$2 SID=$3 START=$4 EXTRA=${5:-}
case "$KIND" in execute|audit) ;; *) die "$USAGE" ;; esac
[ -z "$EXTRA" ] || [ -f "$EXTRA" ] || die "extra-notes-file not found: $EXTRA"

# --- the rig: whoever runs this ---
command -v rig >/dev/null 2>&1 || die "rig is not on PATH; cannot tell which rig this is"
WHO=$(rig whoami --json 2>/dev/null) || die "rig whoami --json failed"
RIG=$(printf '%s' "$WHO" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin)["identity"]["rigName"] or "")
except Exception:
    print("")') || RIG=""
[ -n "$RIG" ] || die "rig whoami --json printed no identity.rigName"

addr() { printf '%s-%s@%s' "$1" "$2" "$RIG"; }   # <pod>-<member>@<rig>
LEAD=$(addr orch lead)

# --- the run record: the only source of run-specific facts ---
REC="$HOME/.hacf/$RIG/current-run.md"
REC_SHOWN="~/.hacf/$RIG/current-run.md"
[ -f "$REC" ] || die "no run record at $REC_SHOWN"

field() {   # the value of the first "- <name>: <value>" line, trailing space trimmed
  sed -n "s/^- $1:[[:space:]]*//p" "$REC" | head -n 1 | sed 's/[[:space:]]*$//'
}
for name in "Project root" "Feature" "Base" "Branch" "Rig spec"; do
  [ -n "$(field "$name")" ] || die "run record $REC_SHOWN has no '- $name:' line"
done
ROOT=$(field "Project root") FEATURE=$(field "Feature") BASE=$(field "Base")
BRANCH=$(field "Branch") SPEC_DIR=$(field "Rig spec")

section() {   # the body of "## <name>" up to the next "## " heading, verbatim
  awk -v h="## $1" '
    $0 == h { on = 1; next }
    on && /^## / { exit }
    on { print }
  ' "$REC"
}
recorded() {   # a section's body, or "none recorded" when it is absent or blank
  local body
  body=$(section "$1")
  if [ -n "$(printf '%s' "$body" | tr -d '[:space:]')" ]; then
    printf '%s\n' "$body" | sed -e '/./,$!d'
  else
    echo "none recorded"
  fi
}

# --- the slice ---
case "$SLICE_ARG" in
  */*|*.md) SPEC=${SLICE_ARG%/} ;;
  [0-9]*_*) SPEC="spec/compiled/MiniPRD_$SLICE_ARG.md" ;;   # legacy flat layout
  *) SPEC=$SLICE_ARG ;;
esac
[ -d "$ROOT/$SPEC" ] && SPEC="$SPEC/SPEC.md"
[ -f "$ROOT/$SPEC" ] || die "no spec at $SPEC under the project root $ROOT"
case "$SPEC" in
  */SPEC.md) SLUG=$(basename "$(dirname "$SPEC")") ;;
  *) SLUG=$(basename "$SPEC" .md); SLUG=${SLUG#MiniPRD_} ;;
esac
LOOP="~/.hacf/$RIG/loops/hacf-loop-$SLUG.md"

TITLE="Execute"; [ "$KIND" = audit ] && TITLE="Audit"
cat <<B
# $TITLE: $SPEC ($SID) - $FEATURE

You were just cleared. Re-orient from files first:
- $SPEC_DIR/startup/context.md, $SPEC_DIR/startup/protocol.md, $SPEC_DIR/CULTURE.md and
  $SPEC_DIR/agents/hacf/guidance/$KIND.md, then the run record $REC_SHOWN.
- Then the project's .agents/skills/hyper-$KIND/SKILL.md.

1. Project root: $ROOT. cd there explicitly before anything else.
   Feature: $FEATURE. Base: $BASE. Branch: $BRANCH. Start for this slice: $START.

2. Input: $SPEC. When it has a "## Pipeline Operating Notes" section, those notes override the
   skill text where they differ. Context: the project's AGENTS.md and the ledger
   spec/compiled/architecture.yml.

B
if [ "$KIND" = execute ]; then cat <<B
3. Owe back: the slice built exactly as written, every task box ticked, and the slice's own
   ledger and progress steps run as written. Run the project's targeted tests while you
   iterate and its full check once at the end. Record Start in your handoff.
   Handoff body: Start, what changed, every command you ran with its exit code, and every
   deviation for the auditor to rule on. A novel test output (no approved fixture) goes to
   tests/candidate_outputs/, and you halt and hand the row to the lead.
B
else cat <<B
3. Judge $START..the slice's result (its commit, or the working tree where the notes say the
   audit commits). The builder's report, the source row of this handoff
   ("rig queue show <id> --full"), is testimony to verify, not evidence.
   Owe back: the loop record $LOOP (protocol.md schema;
   continue its Iteration count when it exists).
   On PASS: write "Latest verdict: PASS", then the slice's close-out as the skill and the
   notes say. On FAIL: increment Iteration, and give each Unmet item with the spec sentence
   that governs it. Approve, reject or review no lore; list what a gate selected.
   Handoff body: the verdict, every command with its exit code, and the commit sha if any.
B
fi
cat <<B

4. Per-run grants, copied verbatim from the run record. Follow a grant only as it is worded
   there; nothing else is granted:
$(recorded "Per-run grants")

   Environment, copied verbatim from the run record:
$(recorded "Environment")

5. Rules: never AskUserQuestion or wait at a terminal prompt; a question goes to $LEAD
   as a queue row. Never push, never --no-verify, never amend or rebase, never git add -A or
   git add .; stage explicit paths.

6. Routing: hand the row to $LEAD, never peer to peer:
   BODY="\$(mktemp "\${TMPDIR:-/tmp}/handoff-XXXXXX.md")"   # write your report into "\$BODY"
   rig queue handoff <your-qitem> --to $LEAD --summary "$KIND: $SID" --body-file "\$BODY"
B
if [ -n "$EXTRA" ]; then echo; echo "7. Specific to this round:"; cat "$EXTRA"; fi
