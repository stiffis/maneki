import json
import os
import socket

from gi.repository import GLib

from . import proto, trust

MAX_LINE = 1 << 20
MAX_CONNECTIONS = 256


class AlreadyRunning(Exception):
    pass


class Connection:
    def __init__(self, server, sock, pid):
        self.server = server
        self.sock = sock
        self.pid = pid
        self.buffer = bytearray()
        self.closed = False
        self.ask_id = None
        self.watch = GLib.io_add_watch(
            sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.readable
        )

    def readable(self, fd, condition):
        try:
            chunk = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            chunk = b""
        if not chunk:
            self.close()
            return False
        self.buffer += chunk
        if b"\n" not in self.buffer and len(self.buffer) > MAX_LINE:
            self.close()
            return False
        while b"\n" in self.buffer and not self.closed:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.server.on_message(self, message)
        return not self.closed

    def reply(self, message):
        if self.closed:
            return
        try:
            self.sock.settimeout(1.0)
            self.sock.sendall(proto.encode(message))
        except OSError:
            pass
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.watch is not None:
            GLib.source_remove(self.watch)
            self.watch = None
        try:
            self.sock.close()
        except OSError:
            pass
        self.server.closed(self)


def probe(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect(str(path))
    except OSError:
        return False
    finally:
        sock.close()
    return True


class Server:
    def __init__(self, path, on_message, on_close):
        self.path = path
        self.on_message = on_message
        self.on_close = on_close
        self.connections = set()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists() or path.is_socket():
            if probe(path):
                raise AlreadyRunning(str(path))
            path.unlink()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        os.chmod(path, 0o600)
        self.sock.listen(32)
        self.sock.setblocking(False)
        self.watch = GLib.io_add_watch(self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self.accept)

    def accept(self, fd, condition):
        try:
            sock, _ = self.sock.accept()
        except OSError:
            return True
        pid, uid = trust.peer(sock)
        if uid != os.getuid() or len(self.connections) >= MAX_CONNECTIONS:
            sock.close()
            return True
        sock.setblocking(False)
        self.connections.add(Connection(self, sock, pid))
        return True

    def closed(self, connection):
        self.connections.discard(connection)
        self.on_close(connection)

    def stop(self):
        for connection in list(self.connections):
            connection.close()
        GLib.source_remove(self.watch)
        self.sock.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
