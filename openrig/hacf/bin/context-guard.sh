#!/usr/bin/env bash
# usage: context-guard.sh --rig <rig> --project-root <dir> [--project-root <dir> ...] [--apply] [--log <file>]
#
# The HACF context guard. One run is one tick: it restarts at most ONE idle, non-lead seat whose
# context is over HACF_CTX_MAX input tokens (default 150000), and only at a durable point — the
# seat is idle, fresh, holds no pending, in-progress or blocked queue row, and every project root
# (and each of its worktrees) is clean with nothing uncommitted under spec/active/.
#
# It fails closed: any field it cannot read, any rig call that fails, any root it cannot inspect
# means no restart this tick. orch-lead is never restarted. Without --apply (the default) it only
# reads (`rig ps`, `rig queue list`, `rig seat launch ... --stop --plan`) and logs what it would do.
#
# Every decision is one log line, to stdout and to the log
# (default $HOME/.hacf/<rig>/context-guard.log):
#   <UTC ts> rig=<r> mode=<dry-run|apply> seat=<session|-> tokens=<n|-> action=<...> reason=<text>
#
# Exit 0 when it ran (restart or not), 1 when a launch or the note to orch-lead failed, 2 on usage.
set -euo pipefail

command -v python3 >/dev/null 2>&1 || { echo "context-guard.sh: python3 is not on PATH" >&2; exit 2; }

PROG=$(cat <<'PY'
import argparse
import datetime
import errno
import fcntl
import json
import os
import re
import subprocess
import sys

LEAD_ID = "orch.lead"
ACTIVE_STATES = ("pending", "in-progress", "blocked")
RIG_TIMEOUT = 120
GIT_TIMEOUT = 60
# git must answer about the root it is pointed at, never about a repository named in the caller's env
GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")}


class Usage(Exception):
    pass


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="context-guard.sh",
        usage="context-guard.sh --rig <rig> --project-root <dir> [--project-root <dir> ...] "
              "[--apply] [--log <file>]")
    p.add_argument("--rig", required=True)
    p.add_argument("--project-root", action="append", required=True, dest="roots")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--log")
    args = p.parse_args(argv)   # argparse exits 2 with the usage line
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.rig):
        p.error("--rig must be a rig name")
    raw = os.environ.get("HACF_CTX_MAX", "150000")
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) <= 0:
        p.error("HACF_CTX_MAX must be a positive integer, got %r" % raw)
    return args, int(raw)


class Guard:
    def __init__(self, args, ctx_max):
        self.rig = args.rig
        self.apply = args.apply
        self.mode = "apply" if args.apply else "dry-run"
        self.max = ctx_max
        self.roots = args.roots
        self.state_dir = os.path.join(os.path.expanduser("~"), ".hacf", self.rig)
        self.log_path = args.log or os.path.join(self.state_dir, "context-guard.log")
        self.lock_path = os.path.join(self.state_dir, "context-guard.lock")

    # --- output ---------------------------------------------------------------------------
    def log(self, seat, tokens, action, reason):
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = "%s rig=%s mode=%s seat=%s tokens=%s action=%s reason=%s" % (
            ts, self.rig, self.mode, seat or "-", "-" if tokens is None else tokens, action, reason)
        print(line, flush=True)
        fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")

    # --- rig --------------------------------------------------------------------------------
    @staticmethod
    def run(argv, timeout=RIG_TIMEOUT, env=None):
        """(returncode, stdout); returncode None when the command could not run or timed out."""
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None, ""
        return r.returncode, r.stdout

    def rig_json(self, *argv):
        code, out = self.run(["rig", *argv])
        if code != 0:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    def read_ps(self):
        nodes = self.rig_json("ps", "--nodes", "--rig", self.rig, "--full", "--json")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            return None
        return nodes

    # --- one seat ---------------------------------------------------------------------------
    @staticmethod
    def tokens_of(node):
        usage = node.get("contextUsage")
        t = usage.get("totalInputTokens") if isinstance(usage, dict) else None
        return t if isinstance(t, int) and not isinstance(t, bool) else None

    def why_not(self, node):
        """None when the seat is a candidate, else the reason it is not."""
        if node.get("logicalId") == LEAD_ID:
            return "orch.lead is exempt"
        session = node.get("canonicalSessionName")
        if not isinstance(session, str) or not session:
            return "unknown: no canonicalSessionName"
        if node.get("lifecycleState") != "running":
            return "not running (lifecycleState=%s)" % node.get("lifecycleState")
        activity = node.get("agentActivity")
        if not isinstance(activity, dict):
            return "unknown: no agentActivity"
        state = activity.get("state")
        if state != "idle":
            if state in (None, "unknown") or not isinstance(state, str):
                return "unknown: agentActivity.state=%s" % state
            return "busy (agentActivity.state=%s)" % state
        if activity.get("stale") is True:
            return "unknown: agentActivity is stale"
        usage = node.get("contextUsage")
        if not isinstance(usage, dict) or usage.get("availability") != "known":
            return "unknown: contextUsage not known"
        if usage.get("fresh") is not True:
            return "unknown: contextUsage not fresh"
        tokens = self.tokens_of(node)
        if tokens is None:
            return "unknown: no totalInputTokens"
        if tokens <= self.max:
            return "under the cap (%d <= %d)" % (tokens, self.max)
        for key in ("pendingWorkCount", "inProgressWorkCount", "blockedWorkCount"):
            v = node.get(key)
            if not isinstance(v, int) or isinstance(v, bool):
                return "unknown: %s=%s" % (key, v)
            if v != 0:
                return "holds queue rows (%s=%d)" % (key, v)
        rows = self.rig_json("queue", "list", "-A", "--destination", session,
                             "--state", ",".join(ACTIVE_STATES), "--json")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return "unknown: queue list unreadable"
        held = [r.get("qitemId") for r in rows
                if r.get("state") in ACTIVE_STATES and r.get("destinationSession") == session]
        if held:
            return "holds queue rows (%s)" % ",".join(str(q) for q in held)
        return None

    # --- the project roots ----------------------------------------------------------------
    def git(self, cwd, *argv):
        return self.run(["git", "-C", cwd, *argv], timeout=GIT_TIMEOUT, env=GIT_ENV)

    def worktrees(self, root):
        """The root's worktree paths, or None when git cannot list them."""
        code, out = self.git(root, "worktree", "list", "--porcelain")
        if code != 0:
            return None
        paths, cur = [], None
        for line in out.splitlines() + [""]:
            if line.startswith("worktree "):
                cur = {"path": line[len("worktree "):], "skip": False}
            elif cur is not None and (line == "bare" or line.startswith("prunable")):
                cur["skip"] = True
            elif line == "" and cur is not None:
                if not cur["skip"]:
                    paths.append(cur["path"])
                cur = None
        return paths

    def untracked_spec_active(self, wt):
        """True when spec/active/ holds a file git does not track, None when unreadable."""
        active = os.path.join(wt, "spec", "active")
        if not os.path.lexists(active):
            return False
        if not os.path.isdir(active) or os.path.islink(active):
            return True
        code, out = self.git(wt, "ls-files", "-z", "--", "spec/active")
        if code != 0:
            return None
        tracked = set(p for p in out.split("\0") if p)
        errors = []
        for dirpath, dirnames, filenames in os.walk(active, onerror=errors.append):
            # a symlinked directory is listed, never followed: it is an entry of its own
            entries = filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
            for name in entries:
                rel = os.path.relpath(os.path.join(dirpath, name), wt).replace(os.sep, "/")
                if rel not in tracked:
                    return True
        return None if errors else False

    def roots_problem(self):
        """None when every root and worktree is clean, else the reason for no restart."""
        for root in self.roots:
            code, top = self.git(root, "rev-parse", "--show-toplevel")
            if code != 0 or not top.strip():
                return "unknown: %s" % root
            wts = self.worktrees(root)
            if wts is None or not wts:
                return "unknown: %s" % root
            for wt in wts:
                if not os.path.isdir(wt):
                    return "unknown: %s" % wt
                code, out = self.git(wt, "status", "--porcelain=v1", "--untracked-files=all")
                if code != 0:
                    return "unknown: %s" % wt
                if out.strip():
                    return "uncommitted work in %s" % wt
                spec = self.untracked_spec_active(wt)
                if spec is None:
                    return "unknown: %s" % wt
                if spec:
                    return "uncommitted work in %s" % wt
        return None

    # --- one tick ---------------------------------------------------------------------------
    def tick(self):
        nodes = self.read_ps()
        if nodes is None:
            self.log(None, None, "skip", "unknown: ps unreadable")
            return 0
        lead = next((n for n in nodes if n.get("logicalId") == LEAD_ID), None)
        lead_session = lead.get("canonicalSessionName") if lead else None
        if not isinstance(lead_session, str) or not lead_session:
            self.log(None, None, "skip", "no orch.lead")
            return 0

        candidates = []
        for node in nodes:
            reason = self.why_not(node)
            seat = node.get("canonicalSessionName") or node.get("logicalId")
            if reason is None:
                candidates.append(node)
            else:
                self.log(seat if isinstance(seat, str) else None, self.tokens_of(node), "skip", reason)
        if not candidates:
            return 0

        problem = self.roots_problem()
        if problem is not None:
            for node in candidates:
                self.log(node["canonicalSessionName"], self.tokens_of(node), "skip", problem)
            return 0

        candidates.sort(key=lambda n: (-self.tokens_of(n), n["canonicalSessionName"]))
        chosen, rest = candidates[0], candidates[1:]
        for node in rest:
            self.log(node["canonicalSessionName"], self.tokens_of(node), "skip", "one restart per tick")
        seat = chosen["canonicalSessionName"]

        # re-read everything right before the launch
        again = self.read_ps()
        node = next((n for n in again or [] if n.get("canonicalSessionName") == seat), None)
        if node is None:
            self.log(seat, self.tokens_of(chosen), "skip", "changed on re-read (seat not in ps)")
            return 0
        reason = self.why_not(node) or self.roots_problem()
        if reason is not None:
            self.log(seat, self.tokens_of(node), "skip", "changed on re-read (%s)" % reason)
            return 0
        tokens = self.tokens_of(node)
        # the preview mirrors the real launch: a live managed seat plans as launchable only with --stop
        plan = self.rig_json("seat", "launch", seat, "--fresh", "--stop", "--plan", "--json")
        if not isinstance(plan, dict) or plan.get("launchable") is not True:
            self.log(seat, tokens, "skip", "not launchable")
            return 0

        why = "%d input tokens > %d, idle at a durable point" % (tokens, self.max)
        if not self.apply:
            self.log(seat, tokens, "would-restart", why)
            return 0
        code, _ = self.run(["rig", "seat", "launch", seat, "--fresh", "--stop",
                            "--reason", "context-guard: " + why, "--json"])
        if code != 0:
            self.log(seat, tokens, "failed", "launch exited %s" % code)
            return 1
        self.log(seat, tokens, "restart", why)
        note = "context-guard: restarted %s (%d > %d) at a durable point; log %s" % (
            seat, tokens, self.max, self.log_path)
        code, _ = self.run(["rig", "send", lead_session, note])
        if code != 0:
            self.log(seat, tokens, "failed", "note to %s exited %s" % (lead_session, code))
            return 1
        return 0


def main(argv):
    args, ctx_max = parse_args(argv)
    g = Guard(args, ctx_max)
    os.makedirs(g.state_dir, mode=0o700, exist_ok=True)
    log_dir = os.path.dirname(os.path.abspath(g.log_path))
    os.makedirs(log_dir, mode=0o700, exist_ok=True)

    lock_fd = os.open(g.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            g.log(None, None, "skip", "another guard run")
            return 0
        raise
    try:
        return g.tick()
    except Exception as e:  # fail closed: a bug in the guard never restarts anything
        g.log(None, None, "skip", "unknown: internal error (%s: %s)" % (type(e).__name__, e))
        return 0
    finally:
        os.close(lock_fd)


sys.exit(main(sys.argv[1:]))
PY
)

exec python3 -c "$PROG" "$@"
