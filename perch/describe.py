import json
import posixpath
from urllib.parse import urlparse

THINKING = "Thinking…"

VERBS = {
    "Bash": "run",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Write": "write",
    "Read": "read",
    "WebFetch": "fetch",
    "WebSearch": "search the web",
}


def clip(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_line(text, limit=80):
    lines = (text or "").strip().splitlines()
    return clip(lines[0].strip(), limit) if lines else ""


def file_name(path):
    return posixpath.basename((path or "").rstrip("/")) or (path or "")


def line_count(text):
    return len((text or "").splitlines())


def mcp_parts(tool):
    parts = tool.split("__")
    return parts[1], "__".join(parts[2:])


def activity(tool, data):
    tool = tool or ""
    data = data if isinstance(data, dict) else {}
    if tool == "Bash":
        return first_line(data.get("description") or data.get("command")) or "Running a command"
    if tool == "Read":
        return f"Reading {file_name(data.get('file_path'))}"
    if tool in ("Edit", "MultiEdit"):
        return f"Editing {file_name(data.get('file_path'))}"
    if tool == "Write":
        return f"Writing {file_name(data.get('file_path'))}"
    if tool == "NotebookEdit":
        return f"Editing {file_name(data.get('notebook_path'))}"
    if tool == "Grep":
        return f"Searching for {first_line(data.get('pattern'), 40)}"
    if tool == "Glob":
        return f"Finding {first_line(data.get('pattern'), 40)}"
    if tool == "WebFetch":
        return f"Fetching {urlparse(data.get('url') or '').netloc or 'a page'}"
    if tool == "WebSearch":
        return f"Searching the web for {first_line(data.get('query'), 40)}"
    if tool in ("Agent", "Task"):
        return f"Agent: {first_line(data.get('description'), 50)}"
    if tool == "TodoWrite":
        return "Updating the plan"
    if tool == "Skill":
        return f"Using skill {data.get('skill', '')}".strip()
    if tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        return f"{server}: {name}"
    return tool or "Working"


def ask_title(tool):
    tool = tool or ""
    if tool in VERBS:
        return f"wants to {VERBS[tool]}"
    if tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        return f"wants to use {server}: {name}"
    return f"wants to use {tool}"


def compact(data, limit):
    return clip(json.dumps(data, ensure_ascii=False, separators=(", ", ": ")), limit)


def ask_detail(tool, data):
    data = data if isinstance(data, dict) else {}
    if tool == "Bash":
        return clip((data.get("command") or "").strip(), 600)
    if tool == "Edit":
        removed, added = line_count(data.get("old_string")), line_count(data.get("new_string"))
        return f"{data.get('file_path', '')}\n−{removed} +{added} lines"
    if tool == "MultiEdit":
        edits = data.get("edits") or []
        removed = sum(line_count(e.get("old_string")) for e in edits)
        added = sum(line_count(e.get("new_string")) for e in edits)
        return f"{data.get('file_path', '')}\n{len(edits)} edits, −{removed} +{added} lines"
    if tool == "Write":
        return f"{data.get('file_path', '')}\n{line_count(data.get('content'))} lines"
    if tool == "NotebookEdit":
        return data.get("notebook_path", "")
    if tool == "Read":
        return data.get("file_path", "")
    if tool == "WebFetch":
        return data.get("url", "")
    if tool == "WebSearch":
        return data.get("query", "")
    return compact(data, 300)


def pick_suggestion(suggestions):
    for suggestion in suggestions or []:
        if isinstance(suggestion, dict) and suggestion.get("type") in ("addRules", "addDirectories"):
            if suggestion.get("type") == "addRules" and suggestion.get("behavior") != "allow":
                continue
            return suggestion
    return None


def rule_text(rule):
    content = rule.get("ruleContent")
    return f"{rule.get('toolName', '')}({content})" if content else rule.get("toolName", "")


def always_label(suggestion):
    if not suggestion:
        return ""
    if suggestion.get("type") == "addRules":
        rules = ", ".join(rule_text(r) for r in suggestion.get("rules") or [])
        return f"Always allow {rules}"
    if suggestion.get("type") == "addDirectories":
        return "Always allow access to " + ", ".join(suggestion.get("directories") or [])
    return "Always allow"
