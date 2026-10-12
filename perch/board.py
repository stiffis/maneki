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
BUSY = (ASKING, ATTENTION, WORKING)
PRIORITY = {ASKING: 0, ATTENTION: 1, WORKING: 2, DONE: 3, IDLE: 4}
ATTENTION_NOTIFICATIONS = {"elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
PASS_THROUGH_LABELS = {"AskUserQuestion": "Has a question for you", "ExitPlanMode": "Plan ready for review"}
DEFAULT_AGENT = "general-purpose"
STEP_LIMIT = 12
RECENT_LIMIT = 24
STALE_SECONDS = 12 * 3600
IDLE_WITHOUT_PID_SECONDS = 30 * 60


@dataclass
class Step:
    key: str
    label: str
    state: str = "running"


@dataclass
class Agent:
    id: str
    kind: str
    label: str
    started: float
    ended: float | None = None
    steps: int = 0
    activity: str = ""
    state: str = "running"

    def elapsed(self, now):
        return (self.ended or now) - self.started


@dataclass
class Session:
    id: str
    agent: str = "claude"
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
    turn_end: float | None = None
    steps: deque = field(default_factory=lambda: deque(maxlen=STEP_LIMIT))
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_LIMIT))
    launches: deque = field(default_factory=lambda: deque(maxlen=RECENT_LIMIT))
    agents: dict = field(default_factory=dict)

    @property
    def name(self):
        return self.title or PurePosixPath(self.cwd).name or self.id[:8]

    def step(self, key):
        return next((step for step in self.steps if step.key == key), None)

    def running_agents(self):
        return [agent for agent in self.agents.values() if agent.state == "running"]

    def elapsed(self, now):
        if self.status in BUSY:
            return now - self.turn_ts
        if self.turn_end is not None:
            return self.turn_end - self.turn_ts
        return None


@dataclass
class Ask:
    id: int
    session_id: str
    tool: str
    data: dict
    suggestion: dict | None
    ts: float
    tool_use_id: str | None = None
    agent: str = ""


class Board:
    def __init__(self, done_seconds=10.0, stale_seconds=STALE_SECONDS):
        self.sessions = {}
        self.asks = {}
        self.next_id = 1
        self.done_seconds = done_seconds
        self.stale_seconds = stale_seconds

    def touch(self, event):
        session_id = event.get("session_id") or "unknown"
        ts = event.get("ts") or time.time()
        session = self.sessions.get(session_id)
        if session is None:
            session = self.sessions[session_id] = Session(session_id, turn_ts=ts)
        session.cwd = event.get("cwd") or session.cwd
        session.title = event.get("session_title") or session.title
        session.agent = event.get("agent") or session.agent
        session.pid = event.get("pid") or session.pid
        session.transcript = event.get("transcript_path") or session.transcript
        session.seen = max(session.seen, ts)
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
            session.turn_end = None
            return [ask.id for ask in self.asks_of(session.id)]
        elif name == "PreToolUse":
            self.tool_started(session, event, key, tool, data, ts)
        elif name in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
            return self.tool_finished(session, event, key, tool, data, ts, name == "PostToolUse")
        elif name == "SubagentStart":
            self.agent_started(session, event, ts)
        elif name == "SubagentStop":
            self.agent_stopped(session, event, ts)
        elif name == "Interrupt":
            return self.interrupted(session.id, ts, ts)
        elif name == "PermissionResolved":
            return self.result_seen(session.id, key)
        elif name == "PermissionRequest":
            self.set_status(session, ATTENTION, ts, PASS_THROUGH_LABELS.get(tool, "Waiting for permission"))
        elif name == "Notification":
            self.notified(session, event, ts)
        elif name == "Stop":
            if self.set_status(session, DONE, ts, "Done"):
                session.turn_end = ts
            session.message = describe.clip((event.get("last_assistant_message") or "").strip(), 400)
            return [ask.id for ask in self.asks_of(session.id)]
        elif name == "StopFailure":
            if self.set_status(session, ATTENTION, ts, "Stopped with an error"):
                session.turn_end = ts
            session.message = describe.clip((event.get("error") or "").strip(), 300)
        return []

    def link_asks(self, session, key, tool, data):
        session.recent.append((key, tool, data))
        for ask in self.asks_of(session.id):
            if ask.tool_use_id is None and ask.tool == tool and ask.data == data:
                ask.tool_use_id = key
                break

    def tool_started(self, session, event, key, tool, data, ts):
        label = describe.activity(tool, data)
        if key:
            self.link_asks(session, key, tool, data)
        if event.get("agent_id"):
            agent = self.agent(session, event, ts)
            agent.steps += 1
            agent.activity = label
            return
        if key and session.step(key) is None:
            session.steps.append(Step(key, label))
        if describe.canonical(tool) == "Agent":
            kind = data.get("subagent_type") or DEFAULT_AGENT
            session.launches.append((kind, describe.first_line(data.get("description"), 50)))
        if self.asks_of(session.id):
            return
        if describe.canonical(tool) == "AskUserQuestion":
            self.set_status(session, ATTENTION, ts, label)
        else:
            self.set_status(session, WORKING, ts, label)

    def tool_finished(self, session, event, key, tool, data, ts, ok):
        resolved = [
            ask.id
            for ask in self.asks_of(session.id)
            if (key and ask.tool_use_id == key) or (ask.tool == tool and ask.data == data)
        ]
        if event.get("agent_id"):
            return resolved
        step = session.step(key) if key else None
        if step is None and key:
            step = Step(key, describe.activity(tool, data))
            session.steps.append(step)
        if step:
            step.state = "ok" if ok else "error"
        if session.status in (WORKING, ATTENTION) and not self.asks_of(session.id):
            self.set_status(session, WORKING, ts, describe.THINKING)
        return resolved

    def agent(self, session, event, ts):
        agent_id = event.get("agent_id")
        agent = session.agents.get(agent_id)
        if agent is None:
            kind = event.get("agent_type") or DEFAULT_AGENT
            label = next((text for k, text in session.launches if k == kind and text), "")
            if label:
                session.launches.remove((kind, label))
            agent = session.agents[agent_id] = Agent(agent_id, kind, label or kind, ts)
        return agent

    def agent_started(self, session, event, ts):
        agent = self.agent(session, event, ts)
        agent.state = "running"
        agent.ended = None
        agent.activity = agent.activity or "Starting"

    def agent_stopped(self, session, event, ts):
        agent = session.agents.get(event.get("agent_id"))
        if agent is None:
            return
        agent.state = "ok"
        agent.ended = ts
        agent.activity = describe.first_line(event.get("last_assistant_message"), 80) or "Done"

    def notified(self, session, event, ts):
        kind = event.get("notification_type")
        unanswered = kind == "permission_prompt" and not self.asks_of(session.id)
        if kind in ATTENTION_NOTIFICATIONS or unanswered:
            text = describe.first_line(event.get("message"), 80) or "Needs your input"
            self.set_status(session, ATTENTION, ts, text)
        elif kind == "idle_prompt" and session.status in (WORKING, ATTENTION) and not self.asks_of(session.id):
            if self.set_status(session, IDLE, ts, "Waiting for you"):
                session.turn_end = ts

    def watched(self):
        return [
            session
            for session in self.sessions.values()
            if session.agent == "claude"
            and session.transcript
            and (session.status in BUSY or self.asks_of(session.id))
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
        for agent in session.running_agents():
            agent.state = "error"
            agent.ended = now
        if self.set_status(session, IDLE, max(now, session.status_ts), "Interrupted"):
            session.turn_end = now
        return [ask.id for ask in self.asks_of(session_id)]

    def add_ask(self, event):
        session = self.touch(event)
        tool = event.get("tool_name") or ""
        data = event.get("tool_input") or {}
        ts = event.get("ts") or time.time()
        taken = {ask.tool_use_id for ask in self.asks.values()}
        key = event.get("tool_use_id") or next(
            (k for k, t, d in reversed(session.recent) if t == tool and d == data and k not in taken),
            None,
        )
        agent = self.agent(session, event, ts) if event.get("agent_id") else None
        ask = Ask(
            id=self.next_id,
            session_id=session.id,
            tool=tool,
            data=data,
            suggestion=describe.pick_suggestion(event.get("permission_suggestions")),
            ts=ts,
            tool_use_id=key,
            agent=agent.label if agent else "",
        )
        self.next_id += 1
        self.asks[ask.id] = ask
        if agent:
            agent.activity = "Waiting for permission"
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
            forgotten = session.pid is None and session.status == IDLE and now - session.seen > IDLE_WITHOUT_PID_SECONDS
            if dead or forgotten or now - session.seen > self.stale_seconds:
                resolved += self.drop(session.id)
                changed = True
                continue
            if session.status == DONE and now - session.status_ts >= self.done_seconds:
                session.status = IDLE
                changed = True
            for agent in list(session.agents.values()):
                if agent.ended is not None and now - agent.ended >= self.done_seconds:
                    del session.agents[agent.id]
                    changed = True
        return changed, resolved

    def visible(self, everything=False):
        shown = [
            s for s in self.sessions.values() if everything or s.status in SHOWN or s.running_agents()
        ]
        return sorted(shown, key=lambda s: (PRIORITY[s.status], -s.seen))

    def names(self):
        counts = Counter(session.name for session in self.sessions.values())
        return {
            session.id: session.name if counts[session.name] == 1 else f"{session.name} #{session.id[-4:]}"
            for session in self.sessions.values()
        }

    def snapshot(self, now=None):
        now = now or time.time()
        names = self.names()
        return {
            "sessions": [
                {
                    "id": s.id,
                    "agent": s.agent,
                    "name": names[s.id],
                    "cwd": s.cwd,
                    "status": s.status,
                    "activity": s.activity,
                    "message": s.message,
                    "elapsed": s.elapsed(now),
                    "pid": s.pid,
                    "steps": [{"label": step.label, "state": step.state} for step in s.steps],
                    "agents": [
                        {
                            "kind": a.kind,
                            "label": a.label,
                            "state": a.state,
                            "steps": a.steps,
                            "activity": a.activity,
                            "elapsed": a.elapsed(now),
                        }
                        for a in s.agents.values()
                    ],
                }
                for s in self.visible(everything=True)
            ],
            "asks": [
                {
                    "id": ask.id,
                    "session": names.get(ask.session_id, ask.session_id),
                    "agent": ask.agent,
                    "tool": ask.tool,
                    "detail": describe.ask_detail(ask.tool, ask.data),
                    "always": describe.always_label(ask.suggestion),
                }
                for ask in self.ordered_asks()
            ],
        }
