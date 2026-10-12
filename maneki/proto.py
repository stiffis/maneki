import json
import os
import socket

from . import trust


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "maneki")


def socket_file():
    return os.path.join(runtime_dir(), "maneki.sock")


def encode(message) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def trusted(sock, genuine=None):
    pid, uid = trust.peer(sock)
    return pid is not None and uid == os.getuid() and (genuine or trust.genuine_daemon)(pid)


def connect(timeout=0.5, genuine=None):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_file())
    except OSError:
        sock.close()
        return None
    if not trusted(sock, genuine):
        sock.close()
        return None
    return sock


def read_message(sock):
    buffer = bytearray()
    while not buffer.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        buffer += chunk
    if not buffer.strip():
        return None
    try:
        return json.loads(buffer)
    except ValueError:
        return None


def send(message, timeout=0.5):
    sock = connect(timeout)
    if sock is None:
        return False
    with sock:
        try:
            sock.sendall(encode(message))
        except OSError:
            return False
    return True


def request(message, timeout=2.0):
    sock = connect(timeout)
    if sock is None:
        return None
    with sock:
        try:
            sock.sendall(encode(message))
            return read_message(sock)
        except OSError:
            return None
