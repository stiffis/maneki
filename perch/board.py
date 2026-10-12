import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import describe

WORKING = "working"
ASKING = "asking"
ATTENTION = "attention"
DONE = "done"
IDLE = "idle"

SHOWN = (ASKING, ATTENTION, WORKING, DONE)
PRIORITY = {ASKING: 0, ATTENTION: 1, WORKING: 2, DONE: 3, IDLE: 4}
ATTENTION_NOTIFICATIONS = {"elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
PASS_THROUGH_LABELS = {"AskUserQuestion": "Has a question for you", "ExitPlanMode": "Plan ready for review"}
STEP_LIMIT = 12
RECENT_LIMIT = 24
STALE_SECONDS = 12 * 3600


@dataclass
class Step:
    key: str
    label: str
    state: str = "running"


@dataclass
class Session:
    id: str
    cwd: str = ""
    title: str = ""
    pid: int | None = None
    status: str = IDLE
    status_ts: float = 0.0
    activity: str = ""
    message: str = ""
    seen: float = 0.0
    transcript: str = ""
    turn_ts: float = 0.0
    steps: deque = field(default_factory=lambda: deque(maxlen=STEP_LIMIT))
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_LIMIT))

    @property
    def name(self):
        return self.title or PurePosixPath(self.cwd).name or self.id[:8]

    def step(self, key):
        return next((step for step in self.steps if step.key == key), None)


@dataclass
class Ask:
    id: int
    session_id: str
    tool: str
    data: dict
    suggestion: dict | None
    ts: float
    tool_use_id: str | None = None


class Board:
    def __init__(self, done_seconds=10.0, stale_seconds=STALE_SECONDS):
        self.sessions = {}
        self.asks = {}
        self.next_id = 1
        self.done_seconds = done_seconds
        self.stale_seconds = stale_seconds

    def touch(self, event):
        session_id = event.get("session_id") or "unknown"
        session = self.sessions.get(session_id)
        if session is None:
            session = self.sessions[session_id] = Session(session_id)
        session.cwd = event.get("cwd") or session.cwd
        session.title = event.get("session_title") or session.title
        session.pid = event.get("claude_pid") or session.pid
        session.transcript = event.get("transcript_path") or session.transcript
        session.seen = max(session.seen, event.get("ts") or time.time())
        return session

    @staticmethod
    def set_status(session, status, ts, activity=None):
        if ts < session.status_ts:
            return False
        session.status = status
        session.status_ts = ts
        if activity is not None:
            session.activity = activity
        return True

    def asks_of(self, session_id):
        return [ask for ask in self.asks.values() if ask.session_id == session_id]

    def apply(self, event):
        name = event.get("hook_event_name")
        ts = event.get("ts") or time.time()
        if name == "SessionEnd":
            return self.drop(event.get("session_id"))
        session = self.touch(event)
        tool = event.get("tool_name") or ""
        data = event.get("tool_input") or {}
        key = event.get("tool_use_id")
        if name == "SessionStart":
            self.set_status(session, IDLE, ts, "")
        elif name == "UserPromptSubmit":
            self.set_status(session, WORKING, ts, describe.THINKING)
            session.message = ""
            session.turn_ts = ts
            return [ask.id for ask in self.asks_of(session.id)]
        elif name == "PreToolUse":
            self.tool_started(session, key, tool, data, ts)
        elif name in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
            return self.tool_finished(session, key, tool, data, ts, name == "PostToolUse")
        elif name == "PermissionRequest":
            self.set_status(session, ATTENTION, ts, PASS_THROUGH_LABELS.get(tool, "Waiting for permission"))
        elif name == "Notification":
            self.notified(session, event, ts)
        elif name == "Stop":
            self.set_status(session, DONE, ts, "Done")
            session.message = describe.clip((event.get("last_assistant_message") or "").strip(), 400)
            return [ask.id for ask in self.asks_of(session.id)]
        elif name == "StopFailure":
            self.set_status(session, ATTENTION, ts, "Stopped with an error")
            session.message = describe.clip((event.get("error") or "").strip(), 300)
        return []

    def tool_started(self, session, key, tool, data, ts):
        label = describe.activity(tool, data)
        if key:
            if session.step(key) is None:
                session.steps.append(Step(key, label))
            session.recent.append((key, tool, data))
            for ask in self.asks_of(session.id):
                if ask.tool_use_id is None and ask.tool == tool and ask.data == data:
                    ask.tool_use_id = key
                    break
        if not self.asks_of(session.id):
            self.set_status(session, WORKING, ts, label)

    def tool_finished(self, session, key, tool, data, ts, ok):
        step = session.step(key) if key else None
        if step is None and key:
            step = Step(key, describe.activity(tool, data))
            session.steps.append(step)
        if step:
            step.state = "ok" if ok else "error"
        if session.status == WORKING:
            self.set_status(session, WORKING, ts, describe.THINKING)
        return [
            ask.id
            for ask in self.asks_of(session.id)
            if (key and ask.tool_use_id == key) or (ask.tool == tool and ask.data == data)
        ]

    def notified(self, session, event, ts):
        kind = event.get("notification_type")
        unanswered = kind == "permission_prompt" and not self.asks_of(session.id)
        if kind in ATTENTION_NOTIFICATIONS or unanswered:
            text = describe.first_line(event.get("message"), 80) or "Needs your input"
            self.set_status(session, ATTENTION, ts, text)
        elif kind == "idle_prompt" and session.status in (WORKING, ATTENTION) and not self.asks_of(session.id):
            self.set_status(session, IDLE, ts, "Waiting for you")

    def watched(self):
        return [
            session
            for session in self.sessions.values()
            if session.transcript and (session.status in (WORKING, ASKING, ATTENTION) or self.asks_of(session.id))
        ]

    def result_seen(self, session_id, tool_use_id):
        return [ask.id for ask in self.asks_of(session_id) if ask.tool_use_id == tool_use_id]

    def interrupted(self, session_id, when, now):
        session = self.sessions.get(session_id)
        if session is None or (when and when < session.turn_ts):
            return []
        for step in session.steps:
            if step.state == "running":
                step.state = "error"
        self.set_status(session, IDLE, max(now, session.status_ts), "Interrupted")
        return [ask.id for ask in self.asks_of(session_id)]

    def add_ask(self, event):
        session = self.touch(event)
        tool = event.get("tool_name") or ""
        data = event.get("tool_input") or {}
        taken = {ask.tool_use_id for ask in self.asks.values()}
        key = next(
            (k for k, t, d in reversed(session.recent) if t == tool and d == data and k not in taken),
            None,
        )
        ask = Ask(
            id=self.next_id,
            session_id=session.id,
            tool=tool,
            data=data,
            suggestion=describe.pick_suggestion(event.get("permission_suggestions")),
            ts=event.get("ts") or time.time(),
            tool_use_id=key,
        )
        self.next_id += 1
        self.asks[ask.id] = ask
        self.set_status(session, ASKING, ask.ts, describe.activity(tool, data))
        return ask

    def remove_ask(self, ask_id):
        ask = self.asks.pop(ask_id, None)
        if ask is None:
            return None
        session = self.sessions.get(ask.session_id)
        if session and session.status == ASKING and not self.asks_of(session.id):
            session.status = WORKING
        return ask

    def oldest_ask(self):
        return min(self.asks.values(), key=lambda ask: ask.id, default=None)

    def ordered_asks(self):
        return sorted(self.asks.values(), key=lambda ask: ask.id)

    def drop(self, session_id):
        self.sessions.pop(session_id, None)
        return [ask.id for ask in self.asks_of(session_id)]

    def expire(self, now, alive):
        changed = False
        resolved = []
        for session in list(self.sessions.values()):
            dead = session.pid is not None and not alive(session.pid)
            if dead or now - session.seen > self.stale_seconds:
                resolved += self.drop(session.id)
                changed = True
            elif session.status == DONE and now - session.status_ts >= self.done_seconds:
                session.status = IDLE
                changed = True
        return changed, resolved

    def visible(self, everything=False):
        shown = [s for s in self.sessions.values() if everything or s.status in SHOWN]
        return sorted(shown, key=lambda s: (PRIORITY[s.status], -s.seen))

    def names(self):
        counts = Counter(session.name for session in self.sessions.values())
        return {
            session.id: session.name if counts[session.name] == 1 else f"{session.name} #{session.id[:4]}"
            for session in self.sessions.values()
        }

    def snapshot(self):
        names = self.names()
        return {
            "sessions": [
                {
                    "id": s.id,
                    "name": names[s.id],
                    "cwd": s.cwd,
                    "status": s.status,
                    "activity": s.activity,
                    "message": s.message,
                    "pid": s.pid,
                    "steps": [{"label": step.label, "state": step.state} for step in s.steps],
                }
                for s in self.visible(everything=True)
            ],
            "asks": [
                {
                    "id": ask.id,
                    "session": names.get(ask.session_id, ask.session_id),
                    "tool": ask.tool,
                    "detail": describe.ask_detail(ask.tool, ask.data),
                    "always": describe.always_label(ask.suggestion),
                }
                for ask in self.ordered_asks()
            ],
        }
