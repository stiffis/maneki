import math
import os
import signal
import sys
import time
from ctypes import CDLL

os.environ.setdefault("GSK_RENDERER", "cairo")
CDLL("libgtk4-layer-shell.so")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import config, describe, hypr, mascots, transcript, trust  # noqa: E402
from .hook import WAIT_LIMITS  # noqa: E402
from .board import DONE, Board  # noqa: E402
from .paths import ICONS, THEMES, config_dir, socket_path  # noqa: E402
from .server import AlreadyRunning, Server  # noqa: E402

ANSWERS = ("allow", "deny", "always", "terminal")
GRANTS = ("allow", "always")
GUARDED = ANSWERS + ("quit", "dnd")
NOTICE_SECONDS = 1.5
RENDER_INTERVAL = 0.1
DESKTOP_DELAY_MS = 120
ARM_SECONDS = 0.8
DETAIL_LINES = 6
STATUS_SYMBOL = {"working": "●", "asking": "●", "attention": "●", "done": "✓", "idle": "○"}
STEP_SYMBOL = {"running": "…", "ok": "✓", "error": "✗"}
AGENT_SYMBOL = {"running": "↳", "ok": "✓", "error": "✗"}
MAX_ASKS = 2
MAX_AGENTS = 4
MAX_STEPS = 6
DETAIL_CHARS = 40
TOP_MARGIN = 6
ICON_SIZE = 14
HINT = "Super+Alt+Y allow · Super+Alt+N deny"
FRAME_MS = 280
FEELING_SECONDS = 3.2
BLINK_EVERY_MS = 4200
BLINK_MS = 160
MASCOT_GAP = 6


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


def detach(widget):
    parent = widget.get_parent()
    if isinstance(parent, Gtk.Overlay):
        parent.remove_overlay(widget)
    elif parent is not None:
        parent.remove(widget)


class Mascot:
    def __init__(self, name, scale, ended):
        sprite = mascots.SPRITES[name]
        self.name = name
        self.scale = scale
        self.ended = ended
        self.area = Gtk.DrawingArea()
        self.area.add_css_class("mascot")
        self.area.set_content_width(sprite.width * scale)
        self.area.set_content_height((sprite.height + mascots.LIFT) * scale)
        self.area.set_valign(Gtk.Align.START)
        self.area.set_draw_func(self.draw)
        self.bubble = Gtk.Label()
        self.bubble.add_css_class("bubble")
        self.bubble.set_halign(Gtk.Align.END)
        self.bubble.set_valign(Gtk.Align.START)
        self.bubble.set_can_target(False)
        self.state = mascots.IDLE
        self.feeling = None
        self.since = time.monotonic()
        self.frame = 0
        self.blinking = False
        self.animation = None
        self.blinker = None
        self.pose = None
        self.refresh()

    def set_state(self, state):
        if state != self.state:
            self.state = state
            if self.feeling is None:
                self.restart()

    def feel(self, feeling):
        self.feeling = feeling
        self.restart()

    def restart(self):
        self.since = time.monotonic()
        self.frame = 0
        if self.animation is None:
            self.animation = GLib.timeout_add(FRAME_MS, self.step)
        self.refresh()

    def step(self):
        self.frame += 1
        if time.monotonic() - self.since < FEELING_SECONDS:
            self.refresh()
            return GLib.SOURCE_CONTINUE
        self.animation = None
        if self.feeling is not None:
            self.feeling = None
            self.ended()
        self.refresh()
        return GLib.SOURCE_REMOVE

    def refresh(self):
        moving = self.animation is not None
        age = time.monotonic() - self.since if moving else FEELING_SECONDS
        self.pose = mascots.pose(self.name, self.feeling or self.state, self.frame, age, not moving, self.blinking)
        text = self.pose.bubble if moving else ""
        if self.bubble.get_label() != text:
            self.bubble.set_label(text)
        self.bubble.set_visible(bool(text))
        self.area.queue_draw()

    def draw(self, area, cr, width, height):
        size = self.scale
        lift = mascots.LIFT - min(self.pose.dy, mascots.LIFT)
        for color, cells in mascots.pixels(self.name, self.pose.parts, self.pose.drop):
            cr.set_source_rgb(*color)
            for x, y in cells:
                cr.rectangle(x * size, (y + lift) * size, size, size)
            cr.fill()

    def wake(self):
        if self.blinker is None:
            self.blinker = GLib.timeout_add(BLINK_EVERY_MS, self.blink)

    def sleep(self):
        if self.animation is None and self.blinker is None and self.feeling is None and not self.blinking:
            return
        for source in (self.animation, self.blinker):
            if source is not None:
                GLib.source_remove(source)
        self.animation = self.blinker = None
        self.feeling = None
        self.blinking = False
        self.refresh()

    def blink(self):
        if self.animation is None and self.state != mascots.IDLE:
            self.blinking = True
            self.refresh()
            GLib.timeout_add(BLINK_MS, self.unblink)
        return GLib.SOURCE_CONTINUE

    def unblink(self):
        self.blinking = False
        self.refresh()
        return GLib.SOURCE_REMOVE


class Island:
    def __init__(self, app):
        self.app = app
        self.board = Board()
        self.connections = {}
        self.offsets = {}
        self.expanded = False
        self.opened = set()
        self.clocks = []
        self.beating = []
        self.beat = False
        self.icons = {}
        self.shown_at = {}
        self.arming = set()
        self.render_queued = False
        self.last_render = 0.0
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
        self.settings = config.load()
        self.mascot = None
        self.moods = {}
        self.clickable = []
        self.input_rects = None

    def start(self):
        self.server = Server(socket_path(), self.on_message, self.on_close)

    def activate(self, app):
        if self.window is not None:
            return
        app.hold()
        self.window = Gtk.Window(application=app, title="perch")
        self.window.add_css_class("perch")
        self.window.connect("realize", self.watch_paint)
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
        self.mascot = self.make_mascot()
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

    def watch_paint(self, window):
        window.get_frame_clock().connect("after-paint", self.fit_input)

    def fit_input(self, *args):
        rects = []
        for widget in self.clickable:
            found, bounds = widget.compute_bounds(self.window)
            if found:
                x, y = math.floor(bounds.get_x()), math.floor(bounds.get_y())
                rects.append((x, y, math.ceil(bounds.get_width()), math.ceil(bounds.get_height())))
        surface = self.window.get_surface()
        if surface is None or rects == self.input_rects:
            return
        self.input_rects = rects
        surface.set_input_region(cairo.Region([cairo.RectangleInt(*rect) for rect in rects]))

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
            GLib.timeout_add(150, self.reload_files)

    def reload_files(self):
        self.load_css()
        settings = config.load()
        if settings != self.settings:
            self.settings = settings
            if self.mascot:
                self.mascot.sleep()
            self.mascot = self.make_mascot()
            self.queue_render()
        return GLib.SOURCE_REMOVE

    def make_mascot(self):
        name = self.settings["mascot"]
        if name not in mascots.SPRITES:
            return None
        return Mascot(name, self.settings["mascot_scale"], self.queue_render)

    def update_mascot(self, asks):
        current = {session.id: (session.status, session.activity) for session in self.board.sessions.values()}
        feeling = mascots.emotion(self.moods, current)
        self.moods = current
        if self.mascot is None:
            return False
        if feeling:
            self.mascot.feel(feeling)
        self.mascot.set_state(mascots.mood([status for status, _ in current.values()], bool(asks)))
        return self.mascot.feeling is not None

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
            desktop = {"dnd": self.dnd, "fullscreen": self.fullscreen, "monitor": self.monitor_name, "mascot": self.settings["mascot"]}
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
        self.update_beat()
        return GLib.SOURCE_CONTINUE

    def update_beat(self):
        self.beat = not self.beat
        for widget in self.beating:
            if self.beat:
                widget.add_css_class("beat")
            else:
                widget.remove_css_class("beat")

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
        if self.render_queued or self.window is None:
            return
        self.render_queued = True
        wait = self.last_render + RENDER_INTERVAL - time.monotonic()
        if wait > 0:
            GLib.timeout_add(int(wait * 1000) + 1, self.render)
        else:
            GLib.idle_add(self.render)

    def toggle_session(self, session_id):
        self.opened.symmetric_difference_update({session_id})
        self.queue_render()

    def render(self):
        self.render_queued = False
        self.last_render = time.monotonic()
        self.clocks = []
        self.beating = []
        now = time.time()
        quiet = self.dnd or self.fullscreen
        sessions = [] if quiet else self.board.visible(self.expanded)
        asks = self.board.ordered_asks()
        notice = self.notice[0] if self.notice and time.monotonic() < self.notice[1] else None
        feeling = self.update_mascot(asks)
        mascot = self.mascot if self.mascot and (sessions or asks or feeling) else None
        if self.mascot and not mascot:
            self.mascot.sleep()
        if not sessions and not asks and not notice and not mascot:
            self.window.set_visible(False)
            self.window.set_child(None)
            return GLib.SOURCE_REMOVE
        alone = not sessions and not asks and not notice
        self.set_layer(bool(asks) or alone)
        names = self.board.names()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for index, ask in enumerate(asks[:MAX_ASKS]):
            content.append(self.ask_card(ask, names, index == 0))
        if len(asks) > MAX_ASKS:
            content.append(label(f"+{len(asks) - MAX_ASKS} more waiting", "hint"))
        if notice:
            content.append(label(notice, "notice"))
        for session in sessions:
            content.append(self.session_row(session, names, now))
            agents = list(session.agents.values())
            for agent in agents[-MAX_AGENTS:]:
                content.append(self.agent_row(agent, now))
            if len(agents) > MAX_AGENTS:
                content.append(label(f"+{len(agents) - MAX_AGENTS} more agents", "hint", "agent-more"))
            if self.expanded or session.id in self.opened:
                content.append(self.session_details(session))
        self.window.set_child(self.wrap(content, mascot, bool(asks), alone))
        self.window.set_default_size(1, 1)
        self.window.set_visible(True)
        return GLib.SOURCE_REMOVE

    def wrap(self, content, mascot, asking, alone):
        island = Gtk.Box()
        island.add_css_class("island")
        if asking:
            island.add_css_class("asking")
        content.set_hexpand(True)
        island.append(content)
        if mascot is None:
            self.clickable = [island]
            return island
        mascot.wake()
        detach(mascot.area)
        detach(mascot.bubble)
        row = Gtk.Box(spacing=MASCOT_GAP)
        row.set_valign(Gtk.Align.START)
        row.append(mascot.area)
        if alone:
            self.clickable = []
            mascot.bubble.set_valign(Gtk.Align.CENTER)
            row.append(mascot.bubble)
            return row
        overlay = Gtk.Overlay()
        overlay.set_valign(Gtk.Align.START)
        overlay.set_child(island)
        mascot.bubble.set_valign(Gtk.Align.START)
        overlay.add_overlay(mascot.bubble)
        row.append(overlay)
        balance = Gtk.Box()
        balance.set_size_request(mascot.area.get_content_width(), 1)
        row.append(balance)
        self.clickable = [island]
        return row

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
        heading.append(label(f"{who} {describe.ask_title(ask.tool)}", "ask-title", width=52))
        card.append(heading)
        detail = label(describe.ask_detail(ask.tool, ask.data), "ask-detail", wrap=True, width=52, lines=DETAIL_LINES)
        detail.set_width_chars(DETAIL_CHARS)
        card.append(detail)
        self.shown_at.setdefault(ask.id, time.monotonic())
        armed = self.armed(ask.id)
        if not armed:
            self.arm_later(ask.id)
        reviewable = self.reviewable(ask)
        if not reviewable:
            card.append(label("Too long to review here. Answer it in the terminal.", "warning", wrap=True, width=52))
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
            card.append(label(describe.always_scope(ask.suggestion), "hint", wrap=True, width=52))
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
        dot = label(STATUS_SYMBOL.get(session.status, "●"), "dot", session.status)
        if session.status in ("working", "attention"):
            self.beating.append(dot)
        row.append(dot)
        row.append(self.icon(session.agent))
        row.append(label(names[session.id], "name"))
        text = session.activity
        if session.status == DONE and session.message:
            text = describe.first_line(session.message, 120)
        activity = label(text, "activity", width=34)
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
        name = label(agent.label, "agent-name", width=32)
        name.set_hexpand(True)
        top.append(name)
        top.append(label(f"{agent.steps} {'step' if agent.steps == 1 else 'steps'}", "meta"))
        top.append(self.clock(lambda at, a=agent: describe.duration(a.elapsed(at)), now))
        box.append(top)
        box.append(label(agent.activity, "agent-activity", width=44))
        return box

    def session_details(self, session):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("details")
        for step in list(session.steps)[-MAX_STEPS:]:
            box.append(label(f"{STEP_SYMBOL[step.state]} {step.label}", "step", step.state, width=48))
        if session.message:
            box.append(label(session.message, "message", wrap=True, width=48, lines=4))
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
