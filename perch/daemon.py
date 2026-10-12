import os
import signal
import sys
import time
from ctypes import CDLL

CDLL("libgtk4-layer-shell.so")

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import describe, hypr, transcript, trust  # noqa: E402
from .hook import WAIT_LIMITS  # noqa: E402
from .board import DONE, Board  # noqa: E402
from .paths import ICONS, THEMES, config_dir, socket_path  # noqa: E402
from .server import AlreadyRunning, Server  # noqa: E402

ANSWERS = ("allow", "deny", "always", "terminal")
GRANTS = ("allow", "always")
GUARDED = ANSWERS + ("quit", "dnd")
NOTICE_SECONDS = 1.5
DESKTOP_DELAY_MS = 120
ARM_SECONDS = 0.8
DETAIL_LINES = 6
STATUS_SYMBOL = {"working": "●", "asking": "●", "attention": "●", "done": "✓", "idle": "○"}
STEP_SYMBOL = {"running": "…", "ok": "✓", "error": "✗"}
AGENT_SYMBOL = {"running": "↳", "ok": "✓", "error": "✗"}
MAX_ASKS = 2
MAX_AGENTS = 4
MAX_STEPS = 6
DETAIL_CHARS = 46
TOP_MARGIN = 6
ICON_SIZE = 14
HINT = "Super+Alt+Y allow · Super+Alt+N deny"


def pid_alive(pid):
    return os.path.exists(f"/proc/{pid}")


def label(text, *classes, wrap=False, width=None, lines=None):
    widget = Gtk.Label(label=describe.reveal(text), xalign=0)
    for name in classes:
        widget.add_css_class(name)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if width:
        widget.set_max_width_chars(width)
    if lines:
        widget.set_lines(lines)
    if width or lines:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    return widget


class Island:
    def __init__(self, app):
        self.app = app
        self.board = Board()
        self.connections = {}
        self.offsets = {}
        self.expanded = False
        self.opened = set()
        self.clocks = []
        self.icons = {}
        self.shown_at = {}
        self.arming = set()
        self.render_queued = False
        self.reload_queued = False
        self.dnd = False
        self.fullscreen = False
        self.monitor_name = None
        self.layer = None
        self.notice = None
        self.desktop_queued = False
        self.events = None
        self.window = None
        self.monitor = None
        self.server = None
        self.provider = Gtk.CssProvider()

    def start(self):
        self.server = Server(socket_path(), self.on_message, self.on_close)

    def activate(self, app):
        if self.window is not None:
            return
        app.hold()
        self.window = Gtk.Window(application=app, title="perch")
        self.window.add_css_class("perch")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "perch")
        LayerShell.set_layer(self.window, LayerShell.Layer.TOP)
        LayerShell.set_anchor(self.window, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(self.window, LayerShell.Edge.TOP, TOP_MARGIN)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self.load_css()
        self.watch_css()
        GLib.timeout_add(1000, self.tick)
        self.refresh_desktop()
        try:
            self.events = hypr.Events(self.desktop_event)
        except OSError as error:
            print(f"perch: not following Hyprland: {error}", file=sys.stderr)
        self.render()

    def shutdown(self, app):
        for ask_id in list(self.connections):
            self.answer(ask_id, None)
        if self.server:
            self.server.stop()

    def css_text(self):
        colors = config_dir() / "colors.css"
        overrides = config_dir() / "style.css"
        parts = [
            (colors if colors.is_file() else THEMES / "colors-everforest.css").read_text(),
            (THEMES / "style.css").read_text(),
        ]
        if overrides.is_file():
            parts.append(overrides.read_text())
        return "\n".join(parts)

    def load_css(self):
        self.reload_queued = False
        try:
            self.provider.load_from_string(self.css_text())
        except OSError as error:
            print(f"perch: could not read the theme: {error}", file=sys.stderr)
        return GLib.SOURCE_REMOVE

    def watch_css(self):
        try:
            self.monitor = Gio.File.new_for_path(str(config_dir())).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error:
            return
        self.monitor.connect("changed", self.css_changed)

    def css_changed(self, *args):
        if not self.reload_queued:
            self.reload_queued = True
            GLib.timeout_add(150, self.load_css)

    def on_message(self, connection, message):
        kind = message.get("type")
        event = message.get("event") or {}
        if kind == "event":
            connection.close()
            self.resolve(self.board.apply(event))
            self.track(event)
            self.queue_render()
        elif kind == "ask":
            ask = self.board.add_ask(event)
            connection.ask_id = ask.id
            self.connections[ask.id] = connection
            self.track(event)
            self.queue_render()
        elif kind == "cmd":
            connection.reply(self.command(message.get("cmd"), connection.pid))
        else:
            connection.close()

    def on_close(self, connection):
        ask_id = connection.ask_id
        if ask_id is not None and self.connections.get(ask_id) is connection:
            del self.connections[ask_id]
            self.board.remove_ask(ask_id)
            self.queue_render()

    def command(self, name, pid=None):
        if name in GUARDED and not (pid and trust.caller_is_user(pid)):
            return {"ok": False, "error": "only you can do that: use the island or its keybinds, not an agent or a script"}
        if name in ANSWERS:
            ask = self.board.oldest_ask()
            if ask is None:
                return {"ok": False, "error": "nothing is waiting for an answer"}
            if name in GRANTS and not self.armed(ask.id):
                return {"ok": False, "error": "the request just appeared; look at it first"}
            if name in GRANTS and not self.reviewable(ask):
                return {"ok": False, "error": "this request is too long to review on the island; answer it in the terminal"}
            self.answer(ask.id, None if name == "terminal" else name)
            return {"ok": True, "tool": ask.tool}
        if name == "toggle":
            self.expanded = not self.expanded
            self.queue_render()
            return {"ok": True, "expanded": self.expanded}
        if name == "dnd":
            self.dnd = not self.dnd
            self.show_notice("Do not disturb: on" if self.dnd else "Do not disturb: off")
            return {"ok": True, "dnd": self.dnd}
        if name == "status":
            desktop = {"dnd": self.dnd, "fullscreen": self.fullscreen, "monitor": self.monitor_name}
            return {"ok": True, **desktop, **self.board.snapshot()}
        if name == "quit":
            GLib.idle_add(self.app.quit)
            return {"ok": True}
        return {"ok": False, "error": f"unknown command: {name}"}

    def show_notice(self, text):
        self.notice = (text, time.monotonic() + NOTICE_SECONDS)
        self.queue_render()
        GLib.timeout_add(int(NOTICE_SECONDS * 1000) + 50, lambda: self.queue_render() or GLib.SOURCE_REMOVE)

    def desktop_event(self, name, data):
        if name in hypr.WATCHED and not self.desktop_queued:
            self.desktop_queued = True
            GLib.timeout_add(DESKTOP_DELAY_MS, self.refresh_desktop)

    def refresh_desktop(self):
        self.desktop_queued = False
        monitor = hypr.focused(hypr.query("monitors"))
        if monitor is None:
            return GLib.SOURCE_REMOVE
        if monitor.get("name") != self.monitor_name:
            self.place_on(monitor.get("name"))
        quiet = hypr.fullscreen(monitor, hypr.query("clients"))
        if quiet != self.fullscreen:
            self.fullscreen = quiet
            self.queue_render()
        return GLib.SOURCE_REMOVE

    def place_on(self, name):
        monitors = Gdk.Display.get_default().get_monitors()
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            if monitor.get_connector() == name:
                LayerShell.set_monitor(self.window, monitor)
                self.monitor_name = name
                return

    def set_layer(self, overlay):
        layer = LayerShell.Layer.OVERLAY if overlay else LayerShell.Layer.TOP
        if layer != self.layer:
            LayerShell.set_layer(self.window, layer)
            self.layer = layer

    def armed(self, ask_id):
        shown = self.shown_at.get(ask_id)
        return shown is not None and time.monotonic() - shown >= ARM_SECONDS

    def reviewable(self, ask):
        return describe.reviewable(ask.tool, ask.data, DETAIL_CHARS, DETAIL_LINES)

    def arm_later(self, ask_id):
        if ask_id in self.arming or self.armed(ask_id):
            return
        self.arming.add(ask_id)

        def rearm():
            self.arming.discard(ask_id)
            self.queue_render()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(int(ARM_SECONDS * 1000) + 50, rearm)

    def answer(self, ask_id, answer):
        self.shown_at.pop(ask_id, None)
        self.board.remove_ask(ask_id)
        connection = self.connections.pop(ask_id, None)
        if connection:
            connection.reply({"answer": answer})
        self.queue_render()

    def resolve(self, ask_ids):
        for ask_id in ask_ids:
            self.answer(ask_id, None)

    def track(self, event):
        session_id = event.get("session_id")
        path = event.get("transcript_path")
        if path and session_id in self.board.sessions and session_id not in self.offsets:
            self.offsets[session_id] = transcript.end_offset(path)

    def follow_transcripts(self, now):
        changed = False
        for session in self.board.watched():
            offset = self.offsets.setdefault(session.id, transcript.end_offset(session.transcript))
            found, self.offsets[session.id] = transcript.scan(session.transcript, offset)
            for kind, tool_use_id, when in found:
                if kind == "result":
                    self.resolve(self.board.result_seen(session.id, tool_use_id))
                else:
                    self.resolve(self.board.interrupted(session.id, when, now))
                    changed = True
        for session_id in set(self.offsets) - set(self.board.sessions):
            del self.offsets[session_id]
        return changed

    def tick(self):
        now = time.time()
        changed, resolved = self.board.expire(now, pid_alive)
        self.resolve(resolved)
        if self.follow_transcripts(now) or changed:
            self.queue_render()
        self.update_clocks(now)
        return GLib.SOURCE_CONTINUE

    def icon(self, agent):
        agent = agent if agent in describe.AGENT_NAMES else "unknown"
        if agent not in self.icons:
            path = ICONS / f"{agent}-symbolic.svg"
            self.icons[agent] = (
                Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(path)), ICON_SIZE, 1)
                if path.is_file()
                else None
            )
        paintable = self.icons[agent]
        image = Gtk.Image.new_from_paintable(paintable) if paintable else Gtk.Image()
        image.set_pixel_size(ICON_SIZE)
        image.add_css_class("agent-icon")
        image.add_css_class(agent)
        image.set_tooltip_text(describe.AGENT_NAMES.get(agent, agent))
        return image

    def update_clocks(self, now):
        for widget, text_at in self.clocks:
            text = text_at(now)
            if widget.get_label() != text:
                widget.set_label(text)

    def clock(self, text_at, now, *classes):
        widget = label(text_at(now), "elapsed", *classes)
        self.clocks.append((widget, text_at))
        return widget

    def queue_render(self):
        if not self.render_queued and self.window is not None:
            self.render_queued = True
            GLib.idle_add(self.render)

    def toggle_session(self, session_id):
        self.opened.symmetric_difference_update({session_id})
        self.queue_render()

    def render(self):
        self.render_queued = False
        self.clocks = []
        now = time.time()
        quiet = self.dnd or self.fullscreen
        sessions = [] if quiet else self.board.visible(self.expanded)
        asks = self.board.ordered_asks()
        notice = self.notice[0] if self.notice and time.monotonic() < self.notice[1] else None
        if not sessions and not asks and not notice:
            self.window.set_visible(False)
            self.window.set_child(None)
            return GLib.SOURCE_REMOVE
        self.set_layer(bool(asks))
        names = self.board.names()
        island = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        island.add_css_class("island")
        if asks:
            island.add_css_class("asking")
        for index, ask in enumerate(asks[:MAX_ASKS]):
            island.append(self.ask_card(ask, names, index == 0))
        if len(asks) > MAX_ASKS:
            island.append(label(f"+{len(asks) - MAX_ASKS} more waiting", "hint"))
        if notice:
            island.append(label(notice, "notice"))
        for session in sessions:
            island.append(self.session_row(session, names, now))
            agents = list(session.agents.values())
            for agent in agents[-MAX_AGENTS:]:
                island.append(self.agent_row(agent, now))
            if len(agents) > MAX_AGENTS:
                island.append(label(f"+{len(agents) - MAX_AGENTS} more agents", "hint", "agent-more"))
            if self.expanded or session.id in self.opened:
                island.append(self.session_details(session))
        self.window.set_child(island)
        self.window.set_default_size(1, 1)
        self.window.set_visible(True)
        return GLib.SOURCE_REMOVE

    def ask_card(self, ask, names, first):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("ask")
        session = self.board.sessions.get(ask.session_id)
        agent = session.agent if session else "claude"
        who = names.get(ask.session_id, describe.AGENT_NAMES.get(agent, agent))
        if ask.agent:
            who = f"{who} › {ask.agent}"
        heading = Gtk.Box(spacing=8)
        heading.append(self.icon(agent))
        heading.append(label(f"{who} {describe.ask_title(ask.tool)}", "ask-title", width=60))
        card.append(heading)
        detail = label(describe.ask_detail(ask.tool, ask.data), "ask-detail", wrap=True, width=60, lines=DETAIL_LINES)
        detail.set_width_chars(DETAIL_CHARS)
        card.append(detail)
        self.shown_at.setdefault(ask.id, time.monotonic())
        armed = self.armed(ask.id)
        if not armed:
            self.arm_later(ask.id)
        reviewable = self.reviewable(ask)
        if not reviewable:
            card.append(label("Too long to review here. Answer it in the terminal.", "warning", wrap=True, width=60))
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        choices = [("allow", "Allow"), ("deny", "Deny")]
        if ask.suggestion:
            choices.append(("always", "Always"))
        if agent in WAIT_LIMITS:
            choices.append(("terminal", "Terminal"))
        for answer, text in choices:
            button = Gtk.Button(label=text)
            button.add_css_class(answer)
            if answer in GRANTS:
                button.set_sensitive(armed and reviewable)
            reply = None if answer == "terminal" else answer
            button.connect("clicked", lambda _button, a=reply, i=ask.id: self.answer(i, a))
            if answer == "always":
                button.set_tooltip_text(describe.always_label(ask.suggestion))
            buttons.append(button)
        card.append(buttons)
        if ask.suggestion and reviewable:
            card.append(label(describe.always_scope(ask.suggestion), "hint", wrap=True, width=60))
        if first:
            card.add_css_class("target")
            card.append(label(HINT, "hint", "keys"))
        if agent in WAIT_LIMITS:
            name = describe.AGENT_NAMES.get(agent, agent)
            deadline = ask.ts + WAIT_LIMITS[agent]
            card.append(
                self.clock(
                    lambda at, d=deadline, n=name: f"{n} asks in the terminal in {describe.duration(max(0, d - at))}",
                    time.time(),
                    "hint",
                )
            )
        return card

    def session_row(self, session, names, now):
        row = Gtk.Box(spacing=8)
        row.add_css_class("session")
        row.append(label(STATUS_SYMBOL.get(session.status, "●"), "dot", session.status))
        row.append(self.icon(session.agent))
        row.append(label(names[session.id], "name"))
        text = session.activity
        if session.status == DONE and session.message:
            text = describe.first_line(session.message, 120)
        activity = label(text, "activity", width=42)
        activity.set_hexpand(True)
        row.append(activity)
        row.append(self.clock(lambda at, s=session: describe.duration(s.elapsed(at)), now))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_args: self.toggle_session(session.id))
        row.add_controller(click)
        return row

    def agent_row(self, agent, now):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("agent")
        box.add_css_class(agent.state)
        top = Gtk.Box(spacing=8)
        top.append(label(AGENT_SYMBOL.get(agent.state, "↳"), "branch"))
        name = label(agent.label, "agent-name", width=40)
        name.set_hexpand(True)
        top.append(name)
        top.append(label(f"{agent.steps} {'step' if agent.steps == 1 else 'steps'}", "meta"))
        top.append(self.clock(lambda at, a=agent: describe.duration(a.elapsed(at)), now))
        box.append(top)
        box.append(label(agent.activity, "agent-activity", width=52))
        return box

    def session_details(self, session):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("details")
        for step in list(session.steps)[-MAX_STEPS:]:
            box.append(label(f"{STEP_SYMBOL[step.state]} {step.label}", "step", step.state, width=56))
        if session.message:
            box.append(label(session.message, "message", wrap=True, width=56, lines=4))
        if not session.steps and not session.message:
            box.append(label("No steps yet", "step"))
        return box


def main():
    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    island = Island(app)
    try:
        island.start()
    except AlreadyRunning:
        print("perch: the island is already running", file=sys.stderr)
        return 1
    app.connect("activate", island.activate)
    app.connect("shutdown", island.shutdown)
    for number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: app.quit() or GLib.SOURCE_REMOVE)
    return app.run([sys.argv[0]])
