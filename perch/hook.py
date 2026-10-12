import json
import os
import sys
import time

from . import proto

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
CAMEL = {
    "sessionId": "session_id",
    "transcriptPath": "transcript_path",
    "toolName": "tool_name",
    "toolArgs": "tool_input",
    "toolInput": "tool_input",
    "agentId": "agent_id",
    "agentType": "agent_type",
    "agentName": "agent_type",
    "response": "last_assistant_message",
    "hookEventName": "hook_event_name",
}
EVENTS = {
    "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd",
    "userPromptSubmitted": "UserPromptSubmit",
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "postToolUseFailure": "PostToolUseFailure",
    "permissionRequest": "PermissionRequest",
    "agentStop": "Stop",
    "subagentStart": "SubagentStart",
    "subagentStop": "SubagentStop",
    "notification": "Notification",
    "errorOccurred": "StopFailure",
}
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
PROCESSES = {"claude": ("claude",), "codex": (), "copilot": ("copilot",), "opencode": ("opencode",)}
ANSWERING_AGENTS = ("claude", "codex", "copilot", "opencode")
WAIT_LIMITS = {"codex": 60, "copilot": 60, "opencode": 60}
EMPTY_JSON_EVENTS = {"codex": ("Stop", "SubagentStop", "Interrupt")}
LIMITS = {"prompt": 300, "message": 300, "title": 120, "last_assistant_message": 400, "error": 300}
STRING_LIMIT = 4000
LIST_LIMIT = 50
PASS_THROUGH_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
DENY_MESSAGE = "The user denied this from perch."


def clip(value, limit=STRING_LIMIT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {key: clip(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [clip(item, limit) for item in value[:LIST_LIMIT]]
    return value


def normalize(payload, event_name=None):
    payload = dict(payload)
    for camel, snake in CAMEL.items():
        if camel in payload and snake not in payload:
            payload[snake] = payload[camel]
    if isinstance(payload.get("tool_input"), str):
        try:
            payload["tool_input"] = json.loads(payload["tool_input"])
        except ValueError:
            payload["tool_input"] = {"command": payload["tool_input"]}
    name = event_name or payload.get("hook_event_name") or ""
    payload["hook_event_name"] = EVENTS.get(name, name)
    if payload["hook_event_name"] in TOOL_EVENTS and not payload.get("tool_use_id"):
        import hashlib

        signature = json.dumps([payload.get("tool_name"), payload.get("tool_input")], sort_keys=True, default=str)
        payload["tool_use_id"] = "h" + hashlib.sha1(signature.encode()).hexdigest()[:16]
    return payload


def reduce(payload, now, pid, agent="claude"):
    event = {key: clip(payload[key], LIMITS.get(key, STRING_LIMIT)) for key in KEEP if key in payload}
    event["ts"] = now
    event["agent"] = agent
    if pid:
        event["pid"] = pid
    return event


def parse_stat(text):
    comm = text[text.index("(") + 1 : text.rindex(")")]
    ppid = int(text[text.rindex(")") + 2 :].split()[1])
    return comm, ppid


def launcher_names(proc, pid):
    try:
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as cmdline:
            args = cmdline.read().split(b"\0")[:2]
    except OSError:
        return set()
    return {os.path.basename(arg.decode(errors="replace")) for arg in args if arg}


def agent_pid(names=("claude",), start=None, proc="/proc", depth=8):
    if not names:
        return None
    pid = start or os.getppid()
    for _ in range(depth):
        try:
            with open(os.path.join(proc, str(pid), "stat")) as stat:
                comm, ppid = parse_stat(stat.read())
        except (OSError, ValueError, IndexError):
            return None
        if comm in names or launcher_names(proc, pid) & set(names):
            return pid
        if ppid <= 1:
            return None
        pid = ppid
    return None


def decision_output(answer, suggestion, agent="claude"):
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
    output = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    if agent == "copilot":
        output.update({key: value for key, value in decision.items() if key in ("behavior", "message")})
    return output


def wants_answer(event):
    return (
        event.get("agent") in ANSWERING_AGENTS
        and event.get("hook_event_name") == "PermissionRequest"
        and event.get("tool_name") not in PASS_THROUGH_TOOLS
    )


def ask(event, limit=None):
    sock = proto.connect()
    if sock is None:
        return None
    with sock:
        try:
            sock.sendall(proto.encode({"type": "ask", "event": event}))
            sock.settimeout(limit)
            reply = proto.read_message(sock)
        except OSError:
            return None
    return reply.get("answer") if isinstance(reply, dict) else None


def options(argv):
    found = {"agent": "claude", "event": None}
    for flag, value in zip(argv, argv[1:]):
        if flag in ("--agent", "--event"):
            found[flag[2:]] = value
    return found["agent"], found["event"]


def run(stdin, stdout, agent="claude", event_name=None):
    try:
        payload = json.load(stdin)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    payload = normalize(payload, event_name)
    event = reduce(payload, time.time(), agent_pid(PROCESSES.get(agent, (agent,))), agent)
    output = None
    if wants_answer(event):
        answer = ask(event, WAIT_LIMITS.get(agent))
        if agent == "opencode":
            output = {"answer": answer} if answer else None
        else:
            from .describe import pick_suggestion

            output = decision_output(answer, pick_suggestion(event.get("permission_suggestions")), agent)
    else:
        proto.send({"type": "event", "event": event})
    if output is None and event["hook_event_name"] in EMPTY_JSON_EVENTS.get(agent, ()):
        output = {}
    if output is not None:
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


def main(argv=()):
    agent, event_name = options(list(argv))
    try:
        run(sys.stdin, sys.stdout, agent, event_name)
    except Exception:
        log_error()
    return 0
