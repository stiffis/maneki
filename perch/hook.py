import json
import os
import sys
import time

from . import describe, proto

KEEP = (
    "hook_event_name",
    "session_id",
    "session_title",
    "cwd",
    "transcript_path",
    "permission_mode",
    "tool_name",
    "tool_input",
    "tool_use_id",
    "permission_suggestions",
    "prompt",
    "message",
    "title",
    "notification_type",
    "last_assistant_message",
    "error",
    "is_interrupt",
    "reason",
    "source",
    "agent_id",
    "agent_type",
)
LIMITS = {"prompt": 300, "message": 300, "title": 120, "last_assistant_message": 400, "error": 300}
STRING_LIMIT = 4000
LIST_LIMIT = 50
PASS_THROUGH_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
DENY_MESSAGE = "The user denied this from perch."


def clip(value, limit=STRING_LIMIT):
    if isinstance(value, str):
        return describe.clip(value, limit)
    if isinstance(value, dict):
        return {key: clip(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [clip(item, limit) for item in value[:LIST_LIMIT]]
    return value


def reduce(payload, now, pid):
    event = {key: clip(payload[key], LIMITS.get(key, STRING_LIMIT)) for key in KEEP if key in payload}
    event["ts"] = now
    if pid:
        event["claude_pid"] = pid
    return event


def parse_stat(text):
    comm = text[text.index("(") + 1 : text.rindex(")")]
    ppid = int(text[text.rindex(")") + 2 :].split()[1])
    return comm, ppid


def claude_pid(start=None, proc="/proc", depth=8):
    pid = start or os.getppid()
    for _ in range(depth):
        try:
            with open(os.path.join(proc, str(pid), "stat")) as stat:
                comm, ppid = parse_stat(stat.read())
        except (OSError, ValueError, IndexError):
            return None
        if comm == "claude":
            return pid
        if ppid <= 1:
            return None
        pid = ppid
    return None


def decision_output(answer, suggestion):
    if answer == "allow":
        decision = {"behavior": "allow"}
    elif answer == "always":
        decision = {"behavior": "allow"}
        if suggestion:
            decision["updatedPermissions"] = [suggestion]
    elif answer == "deny":
        decision = {"behavior": "deny", "message": DENY_MESSAGE}
    else:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def wants_answer(event):
    return event.get("hook_event_name") == "PermissionRequest" and event.get("tool_name") not in PASS_THROUGH_TOOLS


def ask(event):
    sock = proto.connect()
    if sock is None:
        return None
    with sock:
        try:
            sock.sendall(proto.encode({"type": "ask", "event": event}))
            sock.settimeout(None)
            reply = proto.read_message(sock)
        except OSError:
            return None
    return reply.get("answer") if isinstance(reply, dict) else None


def run(stdin, stdout):
    try:
        payload = json.load(stdin)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    event = reduce(payload, time.time(), claude_pid())
    if not wants_answer(event):
        proto.send({"type": "event", "event": event})
        return
    answer = ask(event)
    output = decision_output(answer, describe.pick_suggestion(event.get("permission_suggestions")))
    if output:
        stdout.write(json.dumps(output))
        stdout.flush()


def log_error():
    import traceback

    try:
        directory = proto.runtime_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(os.path.join(directory, "hook-errors.log"), "a") as log:
            log.write(f"{time.strftime('%F %T')} {traceback.format_exc()}\n")
    except OSError:
        pass


def main():
    try:
        run(sys.stdin, sys.stdout)
    except Exception:
        log_error()
    return 0
