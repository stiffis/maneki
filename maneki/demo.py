import os
import threading
import time
import uuid

from . import proto
from .cli import NOT_RUNNING

BOOK = "/home/demo/libro-arqui"
CHIP = "/home/demo/xqdot4z"


class Fake:
    def __init__(self, cwd, agent="claude"):
        self.session = f"demo-{uuid.uuid4().hex[:8]}"
        self.cwd = cwd
        self.agent = agent

    def event(self, name, **fields):
        return {
            "hook_event_name": name,
            "session_id": self.session,
            "cwd": self.cwd,
            "ts": time.time(),
            "pid": os.getpid(),
            "agent": self.agent,
            **fields,
        }

    def emit(self, name, **fields):
        proto.send({"type": "event", "event": self.event(name, **fields)})

    def tool(self, name, data, seconds=1.2, ok=True):
        key = f"toolu_demo_{uuid.uuid4().hex[:12]}"
        self.emit("PreToolUse", tool_name=name, tool_input=data, tool_use_id=key)
        time.sleep(seconds)
        self.emit("PostToolUse" if ok else "PostToolUseFailure", tool_name=name, tool_input=data, tool_use_id=key)
        return key

    def ask(self, name, data, suggestions):
        sock = proto.connect()
        if sock is None:
            return None
        event = self.event("PermissionRequest", tool_name=name, tool_input=data, permission_suggestions=suggestions)
        with sock:
            sock.sendall(proto.encode({"type": "ask", "event": event}))
            sock.settimeout(120)
            reply = proto.read_message(sock)
        return reply.get("answer") if isinstance(reply, dict) else None


def writer():
    book = Fake(BOOK, "codex")
    book.emit("SessionStart", source="startup")
    book.emit("UserPromptSubmit", prompt="Redraw the pipeline figure")
    time.sleep(1.5)
    book.tool("Read", {"file_path": f"{BOOK}/cap3.tex"})
    book.tool("Grep", {"pattern": "fig:pipeline", "path": BOOK})
    book.tool("Edit", {"file_path": f"{BOOK}/figuras/pipeline.tex", "old_string": "a\nb", "new_string": "a\nb\nc"}, 2.5)
    book.tool("Bash", {"command": "latexmk -xelatex main.tex", "description": "Build the book"}, 4)
    book.emit("Stop", last_assistant_message="Redrew the pipeline figure and rebuilt the book without errors.")


def main():
    if proto.connect() is None:
        print(NOT_RUNNING)
        return 1
    print("Two fake sessions are on the island. Answer the request with the buttons or Super+Alt+Y / N.")
    thread = threading.Thread(target=writer)
    thread.start()
    chip = Fake(CHIP)
    chip.emit("SessionStart", source="startup")
    chip.emit("UserPromptSubmit", prompt="Run the checks")
    time.sleep(2)
    command = {"command": "make check", "description": "Run the full test gate"}
    key = f"toolu_demo_{uuid.uuid4().hex[:12]}"
    chip.emit("PreToolUse", tool_name="Bash", tool_input=command, tool_use_id=key)
    suggestions = [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "make check"}], "behavior": "allow", "destination": "localSettings"},
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"},
    ]
    answer = chip.ask("Bash", command, suggestions)
    print(f"Answer received: {answer}")
    if answer in ("allow", "always"):
        time.sleep(3)
        chip.emit("PostToolUse", tool_name="Bash", tool_input=command, tool_use_id=key)
        chip.emit("Stop", last_assistant_message="All 214 checks passed.")
    else:
        chip.emit("PostToolUseFailure", tool_name="Bash", tool_input=command, tool_use_id=key, error="denied")
        chip.emit("Stop", last_assistant_message="Stopped: the checks were not allowed to run.")
    thread.join()
    time.sleep(6)
    return 0
