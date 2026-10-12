import json
import os
import socket

from gi.repository import GLib

FULLSCREEN = 2
WATCHED = frozenset(
    {
        "fullscreen",
        "focusedmon",
        "focusedmonv2",
        "workspace",
        "workspacev2",
        "activespecial",
        "activespecialv2",
        "monitoradded",
        "monitoraddedv2",
        "monitorremoved",
        "monitorremovedv2",
        "openwindow",
        "closewindow",
        "movewindow",
        "movewindowv2",
    }
)


def directory(env=None):
    env = os.environ if env is None else env
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE") or ""
    runtime = env.get("XDG_RUNTIME_DIR") or ""
    if not signature or not runtime or "/" in signature:
        return None
    return os.path.join(runtime, "hypr", signature)


def query(command, timeout=1.0, env=None):
    base = directory(env)
    if base is None:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    chunks = []
    try:
        sock.connect(os.path.join(base, ".socket.sock"))
        sock.sendall(f"j/{command}".encode())
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    except OSError:
        return None
    finally:
        sock.close()
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        return None


def focused(monitors):
    return next((monitor for monitor in monitors or [] if monitor.get("focused")), None)


def visible_workspace(monitor):
    special = (monitor or {}).get("specialWorkspace") or {}
    if special.get("id"):
        return special["id"]
    return ((monitor or {}).get("activeWorkspace") or {}).get("id")


def fullscreen(monitor, clients):
    workspace = visible_workspace(monitor)
    return workspace is not None and any(
        (client.get("workspace") or {}).get("id") == workspace and client.get("fullscreen") == FULLSCREEN
        for client in clients or []
    )


def parse_event(line):
    name, _, data = line.partition(">>")
    return name, data


class Events:
    def __init__(self, callback, env=None):
        base = directory(env)
        if base is None:
            raise OSError("Hyprland is not running")
        self.callback = callback
        self.buffer = b""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(os.path.join(base, ".socket2.sock"))
        self.sock.setblocking(False)
        self.watch = GLib.io_add_watch(
            self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.readable
        )

    def readable(self, fd, condition):
        try:
            chunk = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            chunk = b""
        if not chunk:
            self.sock.close()
            return False
        self.buffer += chunk
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            self.callback(*parse_event(line.decode(errors="replace")))
        return True
