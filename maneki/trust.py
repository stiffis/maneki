import os
import socket
import struct
import sys

AGENTS = frozenset({"claude", "codex", "copilot", "opencode"})
DESKTOPS = frozenset({"Hyprland"})
PEER = struct.Struct("3i")
DEPTH = 64
ISOLATED = frozenset({"-I", "-IS"})
DAEMON_SCRIPT = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "maneki"))


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def process(pid, proc="/proc"):
    stat = read_bytes(os.path.join(proc, str(pid), "stat"))
    if stat is None:
        return None
    text = stat.decode(errors="replace")
    try:
        comm = text[text.index("(") + 1 : text.rindex(")")]
        ppid = int(text[text.rindex(")") + 2 :].split()[1])
    except (ValueError, IndexError):
        return None
    raw = read_bytes(os.path.join(proc, str(pid), "cmdline")) or b""
    args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    return comm, ppid, args


def names(comm, args):
    return {comm} | {os.path.basename(arg) for arg in args[:2]}


def caller_is_user(pid, proc="/proc"):
    for _ in range(DEPTH):
        info = process(pid, proc)
        if info is None:
            return False
        comm, ppid, args = info
        if names(comm, args) & AGENTS:
            return False
        if comm in DESKTOPS:
            return True
        if ppid <= 1:
            return False
        pid = ppid
    return False


def resolve(path, cwd):
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def genuine_daemon(pid, proc="/proc", script=DAEMON_SCRIPT, python=None):
    info = process(pid, proc)
    if info is None:
        return False
    _, _, args = info
    if len(args) != 4 or args[1] not in ISOLATED or args[3] != "daemon":
        return False
    try:
        cwd = os.readlink(os.path.join(proc, str(pid), "cwd"))
        exe = os.path.realpath(os.readlink(os.path.join(proc, str(pid), "exe")))
    except OSError:
        return False
    return resolve(args[2], cwd) == script and exe == os.path.realpath(python or sys.executable)


def peer(sock):
    try:
        pid, uid, gid = PEER.unpack(sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, PEER.size))
    except (OSError, struct.error):
        return None, None
    return pid, uid
