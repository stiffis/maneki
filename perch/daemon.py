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

from . import describe, transcript  # noqa: E402
from .board import DONE, Board  # noqa: E402
from .paths import THEMES, config_dir, socket_path  # noqa: E402
from .server import AlreadyRunning, Server  # noqa: E402

ANSWERS = ("allow", "deny", "always")
STATUS_SYMBOL = {"working": "●", "asking": "●", "attention": "●", "done": "✓", "idle": "○"}
STEP_SYMBOL = {"running": "…", "ok": "✓", "error": "✗"}
MAX_ASKS = 2
MAX_STEPS = 6
DETAIL_CHARS = 46
TOP_MARGIN = 6
HINT = "Super+Alt+Y allow · Super+Alt+N deny"


def pid_alive(pid):
    return os.path.exists(f"/proc/{pid}")


def label(text, *classes, wrap=False, width=None, lines=None):
    widget = Gtk.Label(label=text, xalign=0)
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
        self.render_queued = False
        self.reload_queued = False
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
        self.render()

    def shutdown(self, app):
        for ask_id in list(self.connections):
            self.answer(ask_id, None)
        if self.server:
            self.server.stop()

    def css_text(self):
        parts = []
        for name, fallback in (("colors.css", "colors-everforest.css"), ("style.css", "style.css")):
            path = config_dir() / name
            parts.append((path if path.is_file() else THEMES / fallback).read_text())
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
            connection.reply(self.command(message.get("cmd")))
        else:
            connection.close()

    def on_close(self, connection):
        ask_id = connection.ask_id
        if ask_id is not None and self.connections.get(ask_id) is connection:
            del self.connections[ask_id]
            self.board.remove_ask(ask_id)
            self.queue_render()

    def command(self, name):
        if name in ANSWERS:
            ask = self.board.oldest_ask()
            if ask is None:
                return {"ok": False, "error": "nothing is waiting for an answer"}
            self.answer(ask.id, name)
            return {"ok": True, "tool": ask.tool}
        if name == "toggle":
            self.expanded = not self.expanded
            self.queue_render()
            return {"ok": True, "expanded": self.expanded}
        if name == "status":
            return {"ok": True, **self.board.snapshot()}
        if name == "quit":
            GLib.idle_add(self.app.quit)
            return {"ok": True}
        return {"ok": False, "error": f"unknown command: {name}"}

    def answer(self, ask_id, answer):
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
        return GLib.SOURCE_CONTINUE

    def queue_render(self):
        if not self.render_queued and self.window is not None:
            self.render_queued = True
            GLib.idle_add(self.render)

    def toggle_session(self, session_id):
        self.opened.symmetric_difference_update({session_id})
        self.queue_render()

    def render(self):
        self.render_queued = False
        sessions = self.board.visible(self.expanded)
        asks = self.board.ordered_asks()
        if not sessions and not asks:
            self.window.set_visible(False)
            self.window.set_child(None)
            return GLib.SOURCE_REMOVE
        names = self.board.names()
        island = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        island.add_css_class("island")
        if asks:
            island.add_css_class("asking")
        for index, ask in enumerate(asks[:MAX_ASKS]):
            island.append(self.ask_card(ask, names, index == 0))
        if len(asks) > MAX_ASKS:
            island.append(label(f"+{len(asks) - MAX_ASKS} more waiting", "hint"))
        for session in sessions:
            island.append(self.session_row(session, names))
            if self.expanded or session.id in self.opened:
                island.append(self.session_details(session))
        self.window.set_child(island)
        self.window.set_default_size(1, 1)
        self.window.set_visible(True)
        return GLib.SOURCE_REMOVE

    def ask_card(self, ask, names, first):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("ask")
        title = f"{names.get(ask.session_id, 'Claude')} {describe.ask_title(ask.tool)}"
        card.append(label(title, "ask-title", width=60))
        detail = label(describe.ask_detail(ask.tool, ask.data), "ask-detail", wrap=True, width=60, lines=6)
        detail.set_width_chars(DETAIL_CHARS)
        card.append(detail)
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        choices = [("allow", "Allow"), ("deny", "Deny")]
        if ask.suggestion:
            choices.append(("always", "Always"))
        for answer, text in choices:
            button = Gtk.Button(label=text)
            button.add_css_class(answer)
            button.connect("clicked", lambda _button, a=answer, i=ask.id: self.answer(i, a))
            if answer == "always":
                button.set_tooltip_text(describe.always_label(ask.suggestion))
            buttons.append(button)
        card.append(buttons)
        if first:
            card.append(label(HINT, "hint"))
        return card

    def session_row(self, session, names):
        row = Gtk.Box(spacing=8)
        row.add_css_class("session")
        row.append(label(STATUS_SYMBOL.get(session.status, "●"), "dot", session.status))
        row.append(label(names[session.id], "name"))
        text = session.activity
        if session.status == DONE and session.message:
            text = describe.first_line(session.message, 120)
        activity = label(text, "activity", width=42)
        activity.set_hexpand(True)
        row.append(activity)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_args: self.toggle_session(session.id))
        row.add_controller(click)
        return row

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
