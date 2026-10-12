import json
import os
import stat
from datetime import datetime

INTERRUPTED = "[Request interrupted by user"
MARKERS = (b'"tool_result"', INTERRUPTED.encode())
ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def allowed(path, root=ROOT):
    real = os.path.realpath(path or "")
    return real.startswith(os.path.realpath(root) + os.sep) and real.endswith(".jsonl")


def end_offset(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def entry_time(entry):
    try:
        return datetime.fromisoformat(entry["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def signals(entry):
    if not isinstance(entry, dict):
        return []
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return []
    found = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result" and block.get("tool_use_id"):
            found.append(("result", block["tool_use_id"], entry_time(entry)))
        elif entry.get("type") == "user" and block.get("type") == "text":
            if str(block.get("text", "")).startswith(INTERRUPTED):
                found.append(("interrupted", None, entry_time(entry)))
    return found


def scan(path, offset, root=ROOT):
    if not allowed(path, root):
        return [], offset
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return [], offset
        with os.fdopen(descriptor, "rb") as transcript:
            size = transcript.seek(0, os.SEEK_END)
            if size < offset:
                offset = 0
            transcript.seek(offset)
            data = transcript.read(size - offset)
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    found = []
    for line in data[:end].split(b"\n"):
        if not any(marker in line for marker in MARKERS):
            continue
        try:
            found += signals(json.loads(line))
        except ValueError:
            continue
    return found, offset + end + 1
