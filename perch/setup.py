import difflib
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import BIN, INTEGRATIONS, THEMES

ASYNC_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
    "Notification",
    "Stop",
    "StopFailure",
)
SYNC_EVENTS = {"SessionEnd": {}, "PermissionRequest": {"timeout": 3600}}
THEME_FILES = (
    "style.css",
    "colors-everforest.css",
    "colors-everforest-light.css",
    "colors-gruvbox.css",
    "colors-gruvbox-light.css",
)
FAMILIES = ("everforest", "gruvbox")
SWITCH_ANCHOR = 'run_step "Aplicando tema swaync"            swaync/set-theme.sh      "$MODE"'
SWITCH_LINE = 'run_step "Aplicando tema perch"             perch/set-theme.sh       "$MODE"'


class SetupError(Exception):
    pass


@dataclass
class Change:
    path: Path
    before: str | None
    after: str | None
    note: str
    mode: int | None = None
    link: bool = False


def targets(home):
    hypr = home / ".config" / "hypr" / "UserConfigs"
    switcher = home / "theme-switcher"
    return {
        "settings": home / ".claude" / "settings.json",
        "link": home / ".local" / "bin" / "perch",
        "config": home / ".config" / "perch",
        "keybinds": hypr / "UserKeybinds.conf",
        "startup": hypr / "Startup_Apps.conf",
        "switcher": switcher / "switch-theme.sh",
        "switcher_script": switcher / "perch" / "set-theme.sh",
    }


def allowed_paths(home):
    t = targets(home)
    paths = {t[key] for key in ("settings", "link", "keybinds", "startup", "switcher", "switcher_script")}
    paths |= {t["config"] / name for name in THEME_FILES + ("colors.css",)}
    return paths


def hook_command(bin_path=BIN):
    return f"{bin_path} hook"


def keybind_lines(link):
    return [
        f"bind = $mainMod ALT, Y, exec, {link} allow",
        f"bind = $mainMod ALT, N, exec, {link} deny",
        f"bind = $mainMod ALT, A, exec, {link} always",
        f"bind = $mainMod ALT, I, exec, {link} toggle",
    ]


def startup_lines(link):
    return [f"exec-once = {link} daemon"]


def read(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def digest(text):
    return None if text is None else hashlib.sha256(text.encode()).hexdigest()


def merge_hooks(text, command, remove=False):
    settings = json.loads(text) if text and text.strip() else {}
    if not isinstance(settings, dict):
        raise SetupError("settings.json does not hold a JSON object")
    hooks = settings.get("hooks") or {}
    cleaned = {}
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") or []
            kept = [entry for entry in entries if entry.get("command") != command]
            if kept or not entries:
                kept_groups.append({**group, "hooks": kept} if entries else group)
        if kept_groups:
            cleaned[event] = kept_groups
    if not remove:
        for event in ASYNC_EVENTS:
            cleaned.setdefault(event, []).append({"hooks": [{"type": "command", "command": command, "async": True}]})
        for event, extra in SYNC_EVENTS.items():
            cleaned.setdefault(event, []).append({"hooks": [{"type": "command", "command": command, **extra}]})
    if cleaned:
        settings["hooks"] = cleaned
    else:
        settings.pop("hooks", None)
    if text and text.strip() and json.loads(text) == settings:
        return text
    ending = "\n" if (text or "\n").endswith("\n") else ""
    return json.dumps(settings, indent=2, ensure_ascii=False) + ending


def append_lines(text, lines):
    present = set(text.splitlines())
    missing = [line for line in lines if line not in present]
    if not missing:
        return text
    base = text if text.endswith("\n") or not text else text + "\n"
    return base + "\n" + "\n".join(missing) + "\n"


def remove_lines(text, lines):
    kept = [line for line in text.splitlines(keepends=True) if line.rstrip("\n") not in lines]
    result = "".join(kept)
    return result if result == text else result.rstrip("\n") + "\n"


def insert_after(text, anchor, line, path):
    lines = text.splitlines(keepends=True)
    if any(existing.rstrip("\n") == line for existing in lines):
        return text
    hits = [index for index, existing in enumerate(lines) if existing.rstrip("\n") == anchor]
    if len(hits) != 1:
        raise SetupError(f"expected the swaync step exactly once in {path}, found {len(hits)}")
    lines.insert(hits[0] + 1, line + "\n")
    return "".join(lines)


def current_colors(home):
    switcher = home / "theme-switcher"
    family = (read(switcher / ".current-family") or "").strip()
    theme = (read(switcher / ".current-theme") or "").strip()
    family = family if family in FAMILIES else "everforest"
    return f"colors-{family}-light.css" if theme == "light" else f"colors-{family}.css"


def new_file(path, content, note, mode=None):
    before = read(path)
    if before is not None:
        return None
    return Change(path, None, content, note, mode)


def edit_file(path, transform, note):
    before = read(path)
    if before is None:
        return None
    return Change(path, before, transform(before), note)


def link_change(link, target, remove):
    if link.is_symlink():
        current = os.readlink(link)
        if Path(current) != target:
            raise SetupError(f"{link} already points to {current}; move it away first")
        return Change(link, str(target), None, "remove command link", link=True) if remove else None
    if link.exists():
        raise SetupError(f"{link} exists and is not a link; move it away first")
    return None if remove else Change(link, None, str(target), "command link", link=True)


def plan(home, remove=False, bin_path=BIN):
    t = targets(home)
    link = t["link"]
    command = hook_command(bin_path)
    changes = [link_change(link, bin_path, remove)]
    if not remove:
        for name in THEME_FILES:
            changes.append(new_file(t["config"] / name, (THEMES / name).read_text(), "theme file"))
        colors = current_colors(home)
        changes.append(new_file(t["config"] / "colors.css", (THEMES / colors).read_text(), f"active colors ({colors})"))
        if t["switcher"].exists():
            script = (INTEGRATIONS / "theme-switcher" / "set-theme.sh").read_text()
            changes.append(new_file(t["switcher_script"], script, "theme-switcher step", 0o755))
    settings_before = read(t["settings"])
    changes.append(
        Change(t["settings"], settings_before, merge_hooks(settings_before, command, remove), "Claude Code hooks")
    )
    keys, starts = keybind_lines(link), startup_lines(link)
    if remove:
        changes.append(edit_file(t["keybinds"], lambda text: remove_lines(text, keys), "keybinds"))
        changes.append(edit_file(t["startup"], lambda text: remove_lines(text, starts), "autostart"))
        changes.append(edit_file(t["switcher"], lambda text: remove_lines(text, [SWITCH_LINE]), "theme-switcher step"))
    else:
        changes.append(edit_file(t["keybinds"], lambda text: append_lines(text, keys), "keybinds"))
        changes.append(edit_file(t["startup"], lambda text: append_lines(text, starts), "autostart"))
        changes.append(
            edit_file(
                t["switcher"],
                lambda text: insert_after(text, SWITCH_ANCHOR, SWITCH_LINE, t["switcher"]),
                "theme-switcher step",
            )
        )
    return [change for change in changes if change is not None and change.before != change.after]


def describe_change(change):
    if change.link:
        if change.after is None:
            return f"- {change.path} -> {change.before}  ({change.note})"
        return f"+ {change.path} -> {change.after}  ({change.note})"
    if change.before is None:
        lines = len(change.after.splitlines())
        return f"+ {change.path}  ({change.note}, new file, {lines} lines)"
    diff = difflib.unified_diff(
        change.before.splitlines(keepends=True),
        change.after.splitlines(keepends=True),
        fromfile=str(change.path),
        tofile=str(change.path),
    )
    return f"~ {change.path}  ({change.note})\n" + "".join(diff).rstrip("\n")


def write_atomic(path, text, mode, stamp):
    real = path.resolve() if path.is_symlink() else path
    real.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if real.exists():
        mode = stat.S_IMODE(real.stat().st_mode)
        backup = real.with_name(f"{real.name}.perch-{stamp}.bak")
        shutil.copy2(real, backup)
    descriptor, temporary = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode if mode is not None else 0o644)
        os.replace(temporary, real)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return backup


def apply_link(change):
    change.path.parent.mkdir(parents=True, exist_ok=True)
    if change.after is None:
        if change.path.is_symlink() and os.readlink(change.path) == change.before:
            change.path.unlink()
        return
    temporary = change.path.with_name(f".{change.path.name}.perch-tmp")
    temporary.unlink(missing_ok=True)
    os.symlink(change.after, temporary)
    os.replace(temporary, change.path)


def apply(changes, home, stamp=None):
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    allowed = allowed_paths(home)
    for change in changes:
        if change.path not in allowed:
            raise SetupError(f"refusing to touch {change.path}")
        if not change.link and digest(read(change.path)) != digest(change.before):
            raise SetupError(f"{change.path} changed since the plan was made; run setup again")
    results = []
    for change in changes:
        if change.link:
            apply_link(change)
            results.append(f"✓ {change.note}: {change.path}")
            continue
        backup = write_atomic(change.path, change.after, change.mode, stamp)
        suffix = f" (backup: {backup})" if backup else ""
        results.append(f"✓ {change.note}: {change.path}{suffix}")
    return results


def main(apply_changes=False, remove=False, home=None):
    home = home or Path.home()
    try:
        changes = plan(home, remove)
    except (SetupError, ValueError) as error:
        print(f"perch setup: {error}", file=sys.stderr)
        return 1
    if not changes:
        print("Nothing to do: everything is already " + ("removed." if remove else "in place."))
        return 0
    for change in changes:
        print(describe_change(change))
        print()
    if not apply_changes:
        print("Dry run: nothing was changed. Run again with --apply to make these changes.")
        return 0
    try:
        for line in apply(changes, home):
            print(line)
    except (SetupError, OSError) as error:
        print(f"perch setup: {error}", file=sys.stderr)
        return 1
    if remove:
        print("\nLeft in place: ~/.config/perch and ~/theme-switcher/perch (delete them by hand if you want).")
    else:
        print("\nStart the island now with: hyprctl dispatch exec perch daemon")
    return 0
