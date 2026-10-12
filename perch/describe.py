import json
import math
import posixpath
import re
import unicodedata
from urllib.parse import urlparse

THINKING = "Thinking…"

AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "copilot": "Copilot", "opencode": "OpenCode"}

ALIASES = {
    "bash": "Bash",
    "shell": "Bash",
    "exec_command": "Bash",
    "powershell": "Bash",
    "read": "Read",
    "view": "Read",
    "edit": "Edit",
    "str_replace_editor": "Edit",
    "multiedit": "MultiEdit",
    "write": "Write",
    "create": "Write",
    "glob": "Glob",
    "list": "Glob",
    "grep": "Grep",
    "rg": "Grep",
    "webfetch": "WebFetch",
    "web_fetch": "WebFetch",
    "websearch": "WebSearch",
    "web_search": "WebSearch",
    "task": "Agent",
    "spawn_agent": "Agent",
    "todowrite": "TodoWrite",
    "update_todo": "TodoWrite",
    "update_plan": "TodoWrite",
    "patch": "apply_patch",
    "skill": "Skill",
    "question": "AskUserQuestion",
    "ask_user": "AskUserQuestion",
    "askuserquestion": "AskUserQuestion",
}

VERBS = {
    "Bash": "run",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "apply_patch": "edit",
    "Write": "write",
    "Read": "read",
    "WebFetch": "fetch",
    "WebSearch": "search the web",
    "external_directory": "access files outside the project",
}

PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
DESTINATIONS = {
    "session": "this session",
    "localSettings": "this project, only for you",
    "projectSettings": "this project, for everyone",
    "userSettings": "all your projects",
}
REVIEW_LIMIT = 600


def canonical(tool):
    tool = tool or ""
    return ALIASES.get(tool.lower(), tool) if not tool.startswith("mcp__") else tool


def arg(data, *keys):
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return ""


def clip(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_line(text, limit=80):
    lines = (text or "").strip().splitlines()
    return clip(lines[0].strip(), limit) if lines else ""


def duration(seconds):
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h {rest // 60}m"


def file_name(path):
    return posixpath.basename((path or "").rstrip("/")) or (path or "")


def path_of(data):
    return arg(data, "file_path", "filePath", "filepath", "path", "notebook_path")


def line_count(text):
    return len((text or "").splitlines())


def mcp_parts(tool):
    parts = tool.split("__")
    return parts[1], "__".join(parts[2:])


def patch_text(data):
    return data.get("command") if isinstance(data.get("command"), str) else arg(data, "patch", "input")


def patch_files(data):
    return PATCH_FILE.findall(patch_text(data) or "")


def patch_counts(text):
    lines = (text or "").splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return removed, added


def activity(tool, data):
    tool = canonical(tool)
    data = data if isinstance(data, dict) else {}
    if tool == "Bash":
        return first_line(arg(data, "description", "command", "cmd")) or "Running a command"
    if tool == "Read":
        return f"Reading {file_name(path_of(data))}"
    if tool in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Editing {file_name(path_of(data))}"
    if tool == "Write":
        return f"Writing {file_name(path_of(data))}"
    if tool == "apply_patch":
        files = patch_files(data)
        if len(files) == 1:
            return f"Editing {file_name(files[0])}"
        return f"Editing {len(files)} files" if files else "Applying a patch"
    if tool == "Grep":
        return f"Searching for {first_line(arg(data, 'pattern', 'query'), 40)}"
    if tool == "Glob":
        return f"Finding {first_line(arg(data, 'pattern', 'path'), 40)}"
    if tool == "WebFetch":
        return f"Fetching {urlparse(data.get('url') or '').netloc or 'a page'}"
    if tool == "WebSearch":
        return f"Searching the web for {first_line(data.get('query'), 40)}"
    if tool in ("Agent", "Task"):
        return f"Agent: {first_line(arg(data, 'description', 'prompt', 'message'), 50)}"
    if tool == "TodoWrite":
        return "Updating the plan"
    if tool == "AskUserQuestion":
        return "Has a question for you"
    if tool == "external_directory":
        return f"Accessing {arg(data, 'parentDir') or posixpath.dirname(path_of(data))}"
    if tool == "Skill":
        return f"Using skill {data.get('skill', '')}".strip()
    if tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        return f"{server}: {name}"
    return tool or "Working"


def ask_title(tool):
    tool = canonical(tool)
    if tool in VERBS:
        return f"wants to {VERBS[tool]}"
    if tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        return f"wants to use {server}: {name}"
    return f"wants to use {tool}"


GAP = re.compile(r"[ \t]{8,}")


def reveal(text):
    shown = "".join(
        char if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp") else f"⟦U+{ord(char):04X}⟧"
        for char in text or ""
    )
    return GAP.sub(lambda gap: f" ⟦{len(gap.group())} spaces⟧ ", shown)


def full_detail(tool, data):
    tool = canonical(tool)
    data = data if isinstance(data, dict) else {}
    if tool == "Bash":
        command = arg(data, "command", "cmd")
        return (" ".join(command) if isinstance(command, list) else command).strip()
    return ask_detail(tool, data)


def reviewable(tool, data, width, lines):
    text = reveal(full_detail(tool, data))
    if len(text) > REVIEW_LIMIT or text.endswith("…"):
        return False
    rows = sum(max(1, math.ceil(len(line) / width)) for line in text.split("\n"))
    return rows <= lines


def compact(data, limit):
    return clip(json.dumps(data, ensure_ascii=False, separators=(", ", ": ")), limit)


def ask_detail(tool, data):
    tool = canonical(tool)
    data = data if isinstance(data, dict) else {}
    if tool == "Bash":
        command = arg(data, "command", "cmd")
        command = " ".join(command) if isinstance(command, list) else command
        return clip(command.strip(), 600)
    if tool == "Edit":
        old, new = arg(data, "old_string", "oldString"), arg(data, "new_string", "newString")
        return f"{path_of(data)}\n−{line_count(old)} +{line_count(new)} lines"
    if tool == "MultiEdit":
        edits = data.get("edits") or []
        removed = sum(line_count(e.get("old_string")) for e in edits)
        added = sum(line_count(e.get("new_string")) for e in edits)
        return f"{path_of(data)}\n{len(edits)} edits, −{removed} +{added} lines"
    if tool == "Write":
        return f"{path_of(data)}\n{line_count(data.get('content'))} lines"
    if tool == "apply_patch":
        removed, added = patch_counts(patch_text(data))
        return "\n".join(patch_files(data) + [f"−{removed} +{added} lines"])
    if tool in ("NotebookEdit", "Read"):
        return path_of(data)
    if tool == "WebFetch":
        return data.get("url", "")
    if tool == "WebSearch":
        return data.get("query", "")
    if tool == "external_directory":
        return path_of(data) or "\n".join(data.get("patterns") or [])
    if data.get("patterns") and isinstance(data["patterns"], list):
        return clip("\n".join(map(str, data["patterns"])), 600)
    return compact(data, 300)


def pick_suggestion(suggestions):
    for suggestion in suggestions or []:
        if isinstance(suggestion, dict) and suggestion.get("type") in ("addRules", "addDirectories", "opencode"):
            if suggestion.get("type") == "addRules" and suggestion.get("behavior") != "allow":
                continue
            return suggestion
    return None


def rule_text(rule):
    content = rule.get("ruleContent")
    return f"{rule.get('toolName', '')}({content})" if content else rule.get("toolName", "")


def always_scope(suggestion):
    if not suggestion:
        return ""
    where = DESTINATIONS.get(suggestion.get("destination"), "")
    if suggestion.get("type") == "opencode":
        where = "this session"
    return f"{always_label(suggestion)} for {where}" if where else always_label(suggestion)


def always_label(suggestion):
    if not suggestion:
        return ""
    if suggestion.get("type") == "addRules":
        rules = ", ".join(rule_text(r) for r in suggestion.get("rules") or [])
        return f"Always allow {rules}"
    if suggestion.get("type") == "addDirectories":
        return "Always allow access to " + ", ".join(suggestion.get("directories") or [])
    if suggestion.get("type") == "opencode":
        return "Always allow " + ", ".join(suggestion.get("patterns") or [])
    return "Always allow"
