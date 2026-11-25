#!/usr/bin/env bash
# usage: context-guard.sh --rig <rig> --project-root <dir> [--project-root <dir> ...] --caps <file> [--apply] [--arm-checkpoints] [--log <file>]
#
# The HACF context guard. One run is one tick: it restarts at most ONE idle, non-lead seat whose
# context is over its role's cap_tokens in --caps (caps.yml, checked against roles.yml beside it),
# and only at a durable point — the seat is idle, its usage sample is fresh or provably current
# (its transcript has not grown since the sample), it holds no pending, in-progress or blocked
# queue row, and every project root (and each of its worktrees) is clean. An untracked file under
# spec/active/ (other than the top-level FAILED_WORKFLOWS.md) blocks only the spec.* seats.
# A planned_handover role is never restarted. HACF_CTX_MAX is not read.
#
# With --arm-checkpoints, each tick first makes sure every running seat whose role's behaviour is
# checkpoint_message or planned_handover has exactly one active context-usage-threshold watchdog at
# its cap_tokens (it fires once per occupant, so one job serves every fresh launch). It registers
# a missing one (only with --apply), reports duplicates, and never stops, updates or restarts
# anything on that path.
#
# It fails closed: any field it cannot read, any rig call that fails, any root it cannot inspect
# means no restart this tick. orch-lead is never restarted. Without --apply (the default) it only
# reads (`rig ps`, `rig queue list`, `rig seat launch ... --stop --plan`) and logs what it would do.
#
# Every decision is one log line, to stdout and to the log
# (default $HOME/.hacf/<rig>/context-guard.log):
#   <UTC ts> rig=<r> mode=<dry-run|apply> seat=<session|-> tokens=<n|-> max=<cap|-> action=<...> reason=<text>
#
#   <UTC ts> rig=<r> mode=<...> note=HACF_CTX_MAX ignored; caps come from --caps   (when it is set)
#
# Seat config (v0.6.6, R86): when seat-config.yml sits beside the --caps file, each role's
# cap_tokens, behaviour and reset_scope come from `bin/seat-config.py resolve --section context`
# (HACF's caps.yml default < a preset < the user's overrides.yml); the CLI and every decision below
# are unchanged. Without seat-config.yml the --caps file is read exactly as before. When the
# resolver refuses (an invalid overrides.yml, R70) the guard runs on the --caps file, which is
# HACF's defaults, logs `caps_source=defaults (seat config refused: <first line>)` and, with
# --apply, raises one durable alert per distinct refusal (queue rows to orch-lead@<rig> and
# operator-agent@kernel, then a marker under ~/.hacf/<rig>/guard-alerts/).
#
# Exit 0 when it ran (restart or not), 1 when a launch or the note to orch-lead failed, 2 on usage
# or a caps file it refuses.
set -euo pipefail

HACF_GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HACF_GUARD_DIR

command -v python3 >/dev/null 2>&1 || { echo "context-guard.sh: python3 is not on PATH" >&2; exit 2; }

PROG=$(cat <<'PY'
import argparse
import datetime
import errno
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

LEAD_ID = "orch.lead"
SPEC_PREFIX = "spec."
EXEMPT_SPEC_FILE = "spec/active/FAILED_WORKFLOWS.md"
CAPS_SCHEMA = "hacf.caps/v1"
BEHAVIOURS = ("planned_handover", "restart_at_durable_point", "checkpoint_message")
RESET_SCOPES = ("mission", "slice")
CHECKPOINT_BEHAVIOURS = ("checkpoint_message", "planned_handover")
WATCHDOG_POLICY = "context-usage-threshold"
WATCHDOG_INTERVAL = 300
CHECKPOINT_MESSAGES = {
    "checkpoint_message": (
        "context-guard checkpoint: your context passed %d tokens (openrig/hacf/caps.yml). "
        "Finish the step you are on. Do not commit and do not stash. Write a checkpoint note: "
        "python3 .agents/scripts/hyper_lore.py note --mission <your mission dir> with the tasks "
        "done, the next task, the files touched and <base>. Tell orch-lead in one line that you "
        "are ready for a fresh launch, then end your turn. If you already wrote the note for this "
        "slice, ignore this."),
    "planned_handover": (
        "context-guard handover: your context passed %d tokens (openrig/hacf/caps.yml). At your "
        "next durable point, plan your handover (openrig-core retiring-and-inheriting-a-seat). "
        "The guard never restarts you."),
}
SAMPLE_SLACK = 2.0   # seconds: the transcript's mtime and sampledAt come from two writers
SAMPLED_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})")
ACTIVE_STATES = ("pending", "in-progress", "blocked")
RIG_TIMEOUT = 120
GIT_TIMEOUT = 60
# git must answer about the root it is pointed at, never about a repository named in the caller's env
GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")}


class Usage(Exception):
    pass


class CapsError(Exception):
    pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def load_caps(path):
    """{role: entry} from caps.yml, checked against roles.yml beside it; CapsError otherwise."""
    try:
        import yaml
    except ImportError:
        raise CapsError("PyYAML is not installed")

    def read(p):
        try:
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except OSError as e:
            raise CapsError("cannot read %s (%s)" % (p, e.strerror or e))
        except yaml.YAMLError as e:
            raise CapsError("%s is not valid YAML (%s)" % (p, str(e).splitlines()[0]))

    data = read(path)
    roles_path = os.path.join(os.path.dirname(os.path.abspath(path)), "roles.yml")
    roles_doc = read(roles_path)
    if not isinstance(roles_doc, dict) or not isinstance(roles_doc.get("roles"), dict) \
            or not roles_doc["roles"]:
        raise CapsError("%s has no roles table" % roles_path)
    roles = set(roles_doc["roles"])
    if not isinstance(data, dict):
        raise CapsError("%s is not a mapping" % path)
    if data.get("schema") != CAPS_SCHEMA:
        raise CapsError("schema must be %s, got %r" % (CAPS_SCHEMA, data.get("schema")))
    seats = data.get("seats")
    if not isinstance(seats, dict):
        raise CapsError("seats must be a mapping of role to entry")
    missing = sorted(roles - set(seats))
    if missing:
        raise CapsError("no cap for role %s of roles.yml" % ", ".join(missing))
    unknown = sorted(str(r) for r in set(seats) - roles)
    if unknown:
        raise CapsError("unknown role %s (not in roles.yml)" % ", ".join(unknown))
    for role in sorted(seats):
        entry = seats[role]
        if not isinstance(entry, dict):
            raise CapsError("seats.%s must be a mapping" % role)
        cap = entry.get("cap_tokens")
        if not _is_int(cap) or cap <= 0:
            raise CapsError("seats.%s.cap_tokens must be a positive int, got %r" % (role, cap))
        if entry.get("behaviour") not in BEHAVIOURS:
            raise CapsError("seats.%s.behaviour unknown: %r" % (role, entry.get("behaviour")))
        if entry.get("reset_scope") not in RESET_SCOPES:
            raise CapsError("seats.%s.reset_scope unknown: %r" % (role, entry.get("reset_scope")))
    reeval = data.get("reevaluate")
    if not isinstance(reeval, list) or not all(isinstance(x, str) for x in reeval):
        raise CapsError("reevaluate must be a list of strings")
    return seats


def resolve_seat_config(caps_path, caps):
    """(caps, caps_source, refusal): caps taken through seat-config.py when seat-config.yml sits
    beside the --caps file; the file's own caps and a refusal text when the resolver refuses."""
    directory = os.path.dirname(os.path.abspath(caps_path))
    if not os.path.isfile(os.path.join(directory, "seat-config.yml")):
        return caps, "file", None
    script = os.path.join(os.environ.get("HACF_GUARD_DIR", ""), "seat-config.py")
    try:
        r = subprocess.run([sys.executable, script, "resolve", "--section", "context", "--caps",
                            os.path.abspath(caps_path), "--dir", directory, "--json"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return caps, "defaults", "seat-config.py: cannot run (%s)" % e
    if r.returncode != 0:
        text = r.stderr.strip() or "seat-config.py: exited %d" % r.returncode
        return caps, "defaults", text
    try:
        seats = json.loads(r.stdout)["seats"]
        merged, changed = {}, []
        for role, entry in caps.items():
            row = dict(entry)
            for key in ("cap_tokens", "behaviour", "reset_scope"):
                cell = seats[role][key]
                row[key] = cell["value"]
                if cell["source"] != "default":
                    changed.append("%s.%s=%s" % (role, key, cell["source"]))
            merged[role] = row
    except (ValueError, KeyError, TypeError, AttributeError):
        return caps, "defaults", "seat-config.py: unreadable output"
    return merged, "resolved", changed


def parse_sampled_at(text):
    """The epoch seconds of an ISO-8601 sampledAt with a zone, or None."""
    if not isinstance(text, str):
        return None
    m = SAMPLED_AT_RE.fullmatch(text)
    if not m:
        return None
    try:
        base = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    zone = m.group(3)
    if zone == "Z":
        offset = datetime.timedelta(0)
    else:
        sign = 1 if zone[0] == "+" else -1
        offset = sign * datetime.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
    when = base.replace(tzinfo=datetime.timezone(offset))
    return when.timestamp() + (float(m.group(2)) if m.group(2) else 0.0)


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="context-guard.sh",
        usage="context-guard.sh --rig <rig> --project-root <dir> [--project-root <dir> ...] "
              "--caps <file> [--apply] [--arm-checkpoints] [--log <file>]")
    p.add_argument("--rig", required=True)
    p.add_argument("--project-root", action="append", required=True, dest="roots")
    p.add_argument("--caps", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--arm-checkpoints", action="store_true", dest="arm")
    p.add_argument("--log")
    args = p.parse_args(argv)   # argparse exits 2 with the usage line
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.rig):
        p.error("--rig must be a rig name")
    return args


class Guard:
    def __init__(self, args, caps, caps_source="file", refusal=None, changed=None):
        self.caps_source = caps_source
        self.refusal = refusal
        self.changed = changed or []
        self.rig = args.rig
        self.apply = args.apply
        self.mode = "apply" if args.apply else "dry-run"
        self.caps = caps
        self.roots = args.roots
        self.arm = args.arm
        self.notes = {}   # canonicalSessionName -> extra decision text (the stale-sample rule)
        self.state_dir = os.path.join(os.path.expanduser("~"), ".hacf", self.rig)
        self.log_path = args.log or os.path.join(self.state_dir, "context-guard.log")
        self.lock_path = os.path.join(self.state_dir, "context-guard.lock")

    # --- output ---------------------------------------------------------------------------
    def log(self, seat, tokens, action, reason, cap=None):
        self.write("seat=%s tokens=%s max=%s action=%s reason=%s" % (
            seat or "-", "-" if tokens is None else tokens, "-" if cap is None else cap,
            action, reason))

    def write(self, rest):
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = "%s rig=%s mode=%s %s" % (ts, self.rig, self.mode, rest)
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

    def cap_of(self, node):
        """The seat's role cap_tokens, or None when its profile has no cap."""
        entry = self.caps.get(node.get("profile")) if isinstance(node.get("profile"), str) else None
        return entry["cap_tokens"] if entry else None

    @staticmethod
    def transcript_check(usage):
        """None when the stale sample provably still holds, else the reason it does not."""
        path, sid = usage.get("transcriptPath"), usage.get("sessionId")
        if not isinstance(path, str) or not path:
            return "transcript unknown (no transcriptPath)"
        if not isinstance(sid, str) or not sid:
            return "transcript unknown (no sessionId)"
        if os.path.basename(path) != sid + ".jsonl":
            return "transcript unknown (%s is not %s.jsonl)" % (os.path.basename(path), sid)
        sampled = parse_sampled_at(usage.get("sampledAt"))
        if sampled is None:
            return "transcript unknown (sampledAt=%s)" % usage.get("sampledAt")
        try:
            mtime = os.stat(path).st_mtime
        except OSError as e:
            return "transcript unknown (stat failed: %s)" % (e.strerror or e)
        if mtime > sampled + SAMPLE_SLACK:
            return "transcript grew since sample"
        return None

    def why_not(self, node):
        """None when the seat is a candidate, else the reason it is not."""
        if node.get("logicalId") == LEAD_ID:
            return "orch.lead is exempt"
        profile = node.get("profile")
        entry = self.caps.get(profile) if isinstance(profile, str) else None
        if entry is None:
            return "unknown: no cap for profile %s" % profile
        if entry["behaviour"] == "planned_handover":
            return "planned handover seat (never restarted)"
        session = node.get("canonicalSessionName")
        if not isinstance(session, str) or not session:
            return "unknown: no canonicalSessionName"
        self.notes.pop(session, None)
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
        tokens = self.tokens_of(node)
        if tokens is None:
            return "unknown: no totalInputTokens"
        if usage.get("fresh") is not True:
            problem = self.transcript_check(usage)
            if problem is not None:
                return problem
            self.notes[session] = ", stale sample, transcript unchanged since %s" % usage["sampledAt"]
        cap = entry["cap_tokens"]
        if tokens <= cap:
            return "under the cap (%d <= %d)" % (tokens, cap)
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
        """True when spec/active/ holds a file git does not track (the top-level
        FAILED_WORKFLOWS.md excepted), None when unreadable."""
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
                if rel == EXEMPT_SPEC_FILE:
                    continue
                if rel not in tracked:
                    return True
        return None if errors else False

    def roots_problems(self):
        """(every-seat reason or None, spec-seat reason or None) over every root and worktree."""
        spec_only = None
        for root in self.roots:
            code, top = self.git(root, "rev-parse", "--show-toplevel")
            if code != 0 or not top.strip():
                return "unknown: %s" % root, None
            wts = self.worktrees(root)
            if wts is None or not wts:
                return "unknown: %s" % root, None
            for wt in wts:
                if not os.path.isdir(wt):
                    return "unknown: %s" % wt, None
                code, out = self.git(wt, "status", "--porcelain=v1", "--untracked-files=all")
                if code != 0:
                    return "unknown: %s" % wt, None
                if out.strip():
                    return "uncommitted work in %s" % wt, None
                spec = self.untracked_spec_active(wt)
                if spec is None:
                    return "unknown: %s" % wt, None
                if spec and spec_only is None:
                    spec_only = "spec/active work in %s (spec seats only)" % wt
        return None, spec_only

    @staticmethod
    def root_reason(node, problems):
        """The roots' reason this seat may not restart, or None."""
        every, spec_only = problems
        if every is not None:
            return every
        logical = node.get("logicalId")
        if spec_only is not None and isinstance(logical, str) and logical.startswith(SPEC_PREFIX):
            return spec_only
        return None

    # --- one tick ---------------------------------------------------------------------------
    # --- checkpoint watchdogs ---------------------------------------------------------------
    def arm_checkpoints(self, nodes, lead_session):
        """Ensure one active context-usage-threshold job per checkpoint seat; 1 when a register
        failed, else 0. Never stops, updates or restarts anything."""
        seats = []
        for node in nodes:
            profile = node.get("profile")
            entry = self.caps.get(profile) if isinstance(profile, str) else None
            session = node.get("canonicalSessionName")
            if entry is None or entry["behaviour"] not in CHECKPOINT_BEHAVIOURS:
                continue
            if node.get("lifecycleState") != "running" or not isinstance(session, str) or not session:
                continue
            seats.append((node, session, entry))
        if not seats:
            return 0
        jobs = self.rig_json("watchdog", "list", "--json")
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            self.log(None, None, "skip", "unknown: watchdog list unreadable")
            return 0
        rc = 0
        for node, session, entry in seats:
            cap = entry["cap_tokens"]
            tokens = self.tokens_of(node)
            ids = [str(j.get("jobId")) for j in jobs
                   if j.get("policy") == WATCHDOG_POLICY and j.get("targetSession") == session
                   and j.get("state") == "active"]
            if len(ids) == 1:
                self.log(session, tokens, "skip", "checkpoint armed %s" % ids[0], cap)
                continue
            if len(ids) > 1:
                self.log(session, tokens, "skip", "duplicate checkpoint jobs %s" % ",".join(ids), cap)
                continue
            if not self.apply:
                self.log(session, tokens, "would-arm", "no active checkpoint job; would register at %d"
                         % cap, cap)
                continue
            code, _ = self.run(["rig", "watchdog", "register", "--policy", WATCHDOG_POLICY,
                                "--target-session", session, "--threshold-tokens", str(cap),
                                "--interval-seconds", str(WATCHDOG_INTERVAL),
                                "--registered-by", lead_session,
                                "--message", CHECKPOINT_MESSAGES[entry["behaviour"]] % cap,
                                "--json"])
            if code != 0:
                self.log(session, tokens, "failed", "watchdog register exited %s" % code, cap)
                rc = 1
            else:
                self.log(session, tokens, "arm", "registered at %d" % cap, cap)
        return rc

    def alert_refusal(self):
        """Raise the durable alert for a refused seat config, once per distinct refusal text."""
        first = self.refusal.splitlines()[0]
        digest = hashlib.sha256(self.refusal.encode("utf-8")).hexdigest()
        marker_dir = os.path.join(self.state_dir, "guard-alerts")
        marker = os.path.join(marker_dir, digest)
        if os.path.exists(marker):
            return
        parts = first.split(": ", 2)
        where = "%s: %s" % (parts[0], parts[1]) if len(parts) == 3 else first
        summary = "context-guard: overrides.yml invalid (%s); running on HACF defaults" % where
        if not self.apply:
            self.write("note=would alert: %s" % summary)
            return
        body = ("The context guard refused the seat config and is running on HACF's defaults:\n\n"
                "%s\n\nFix: correct or remove the line; the guard keeps running on defaults "
                "until then.\n" % self.refusal)
        os.makedirs(marker_dir, mode=0o700, exist_ok=True)
        fd, body_path = tempfile.mkstemp(prefix="alert-", suffix=".md", dir=self.state_dir)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        try:
            ok = True
            for dest in ("orch-lead@%s" % self.rig, "operator-agent@kernel"):
                code, _ = self.run(["rig", "queue", "create", "--source", "context-guard@%s" % self.rig,
                                    "--destination", dest, "--summary", summary,
                                    "--body-file", body_path])
                if code != 0:
                    self.write("note=alert to %s failed (queue create exited %s); retrying next tick"
                               % (dest, code))
                    ok = False
            if ok:
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.close(fd)
                self.write("note=alert raised for %s" % digest[:12])
        finally:
            os.unlink(body_path)

    def tick(self):
        if "HACF_CTX_MAX" in os.environ:
            self.write("note=HACF_CTX_MAX ignored; caps come from --caps")
        if self.caps_source == "resolved":
            self.write("note=caps_source=resolved%s" % (
                " non-default: " + ",".join(self.changed) if self.changed else ""))
        elif self.caps_source == "defaults":
            self.write("note=caps_source=defaults (seat config refused: %s)"
                       % self.refusal.splitlines()[0])
            self.alert_refusal()
        nodes = self.read_ps()
        if nodes is None:
            self.log(None, None, "skip", "unknown: ps unreadable")
            return 0
        lead = next((n for n in nodes if n.get("logicalId") == LEAD_ID), None)
        lead_session = lead.get("canonicalSessionName") if lead else None
        if not isinstance(lead_session, str) or not lead_session:
            self.log(None, None, "skip", "no orch.lead")
            return 0
        armed = self.arm_checkpoints(nodes, lead_session) if self.arm else 0
        return self.restart_tick(nodes, lead_session) or armed

    def restart_tick(self, nodes, lead_session):
        candidates = []
        for node in nodes:
            reason = self.why_not(node)
            seat = node.get("canonicalSessionName") or node.get("logicalId")
            if reason is None:
                candidates.append(node)
            else:
                self.log(seat if isinstance(seat, str) else None, self.tokens_of(node), "skip", reason,
                         self.cap_of(node))
        if not candidates:
            return 0

        problems = self.roots_problems()
        kept = []
        for node in candidates:
            problem = self.root_reason(node, problems)
            if problem is None:
                kept.append(node)
            else:
                self.log(node["canonicalSessionName"], self.tokens_of(node), "skip", problem,
                         self.cap_of(node))
        candidates = kept
        if not candidates:
            return 0

        candidates.sort(key=lambda n: (-self.tokens_of(n), n["canonicalSessionName"]))
        chosen, rest = candidates[0], candidates[1:]
        for node in rest:
            self.log(node["canonicalSessionName"], self.tokens_of(node), "skip", "one restart per tick",
                     self.cap_of(node))
        seat = chosen["canonicalSessionName"]
        cap = self.cap_of(chosen)

        # re-read everything right before the launch
        again = self.read_ps()
        node = next((n for n in again or [] if n.get("canonicalSessionName") == seat), None)
        if node is None:
            self.log(seat, self.tokens_of(chosen), "skip", "changed on re-read (seat not in ps)", cap)
            return 0
        reason = self.why_not(node) or self.root_reason(node, self.roots_problems())
        if reason is not None:
            self.log(seat, self.tokens_of(node), "skip", "changed on re-read (%s)" % reason,
                     self.cap_of(node))
            return 0
        tokens = self.tokens_of(node)
        cap = self.cap_of(node)
        # the preview mirrors the real launch: a live managed seat plans as launchable only with --stop
        plan = self.rig_json("seat", "launch", seat, "--fresh", "--stop", "--plan", "--json")
        if not isinstance(plan, dict) or plan.get("launchable") is not True:
            self.log(seat, tokens, "skip", "not launchable", cap)
            return 0

        why = "%d input tokens > %d, idle at a durable point%s" % (tokens, cap, self.notes.get(seat, ""))
        if not self.apply:
            self.log(seat, tokens, "would-restart", why, cap)
            return 0
        code, _ = self.run(["rig", "seat", "launch", seat, "--fresh", "--stop",
                            "--reason", "context-guard: " + why, "--json"])
        if code != 0:
            self.log(seat, tokens, "failed", "launch exited %s" % code, cap)
            return 1
        self.log(seat, tokens, "restart", why, cap)
        note = "context-guard: restarted %s (%d > %d) at a durable point; log %s" % (
            seat, tokens, cap, self.log_path)
        code, _ = self.run(["rig", "send", lead_session, note])
        if code != 0:
            self.log(seat, tokens, "failed", "note to %s exited %s" % (lead_session, code), cap)
            return 1
        return 0


def main(argv):
    args = parse_args(argv)
    try:
        caps = load_caps(args.caps)
    except CapsError as e:
        print("context-guard.sh: --caps %s: %s" % (args.caps, e), file=sys.stderr)
        return 2
    caps, caps_source, extra = resolve_seat_config(args.caps, caps)
    if caps_source == "defaults":
        g = Guard(args, caps, caps_source, refusal=extra)
    else:
        g = Guard(args, caps, caps_source, changed=extra or [])
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
