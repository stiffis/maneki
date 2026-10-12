# perch

A small island at the top of the screen that keeps an eye on your coding agent
sessions (Claude Code, Codex, GitHub Copilot CLI and OpenCode): what each one
is doing, when it finishes, and the permission requests you can answer without
going back to the terminal.

Built for Hyprland, but any compositor with `wlr-layer-shell` works.

## How it works

```
Claude Code ──hook (JSON on stdin)──▶ perch hook ──unix socket──▶ perch daemon (GTK4 layer-shell)
            ◀──── allow / deny ◀──────────────── answer ◀──── click or Super+Alt+Y / N
```

- Every hook runs `async` except `PermissionRequest` and `SessionEnd`, so Claude
  never waits on the island.
- On a permission request the terminal dialog and the island race: the first
  answer wins. If you answer in the terminal, the island sees the result in the
  session transcript and drops the card.
- If the island is not running, the hook exits at once and Claude behaves as if
  perch did not exist.
- `AskUserQuestion` and plan approvals stay in the terminal; the island only
  shows that the session needs you.
- A session leaves the island on `SessionEnd`, when its `claude` process exits,
  or after an interrupt settles it.
- Each session shows how long the current turn has run (or how long the last
  one took), and each subagent gets its own row with its steps, what it is
  doing and its time. Claude Code does not report real progress, so steps are
  the measure.

## Agents

Each session shows its agent's icon.

| Agent       | How it connects                                        | Permissions from the island |
| ----------- | ------------------------------------------------------ | --------------------------- |
| Claude Code | hooks in `~/.claude/settings.json`                     | yes, races the terminal     |
| Codex       | hooks in `~/.codex/hooks.json`                         | yes, for 60 seconds         |
| Copilot CLI | hooks in `~/.copilot/hooks/perch.json`                 | yes, for 60 seconds         |
| OpenCode    | plugin in `~/.config/opencode/plugins/perch.js`        | yes, races its dialog       |

- Codex asks you to review and trust new hooks once: open `/hooks` in Codex.
- Codex and Copilot wait for the hook before showing their own prompt, so the
  island gives up after 60 seconds (or at once with **Terminal** or
  `perch terminal`) and hands the request back to them.
- Copilot asks the hook before checking its own saved approvals, so the island
  also shows requests Copilot would have approved by itself.
- Copilot's hooks end in `|| true` because a failing `preToolUse` hook makes
  Copilot deny the tool.
- OpenCode loads plugins when it starts, so restart running sessions after an
  update.

## Requirements

Python 3.10+, `python-gobject`, `gtk4` (4.16+ for CSS variables) and
`gtk4-layer-shell`.

## Install

```sh
perch setup            # dry run: prints every change as a diff
perch setup --apply    # makes them, with timestamped backups
```

Setup links `~/.local/bin/perch`, copies the themes to `~/.config/perch`, adds
the hooks to `~/.claude/settings.json`, the keybinds and autostart to Hyprland's
`UserConfigs`, and a step to `~/theme-switcher`. Each edit is atomic, checked
against the file's hash, and skipped when it is already there.

## Use

| Keys            | Command        | What it does                                   |
| --------------- | -------------- | ---------------------------------------------- |
| `Super+Alt+Y`   | `perch allow`  | allow the oldest pending request               |
| `Super+Alt+N`   | `perch deny`   | deny it                                        |
| `Super+Alt+A`   | `perch always` | allow it and keep the rule Claude suggested    |
| `Super+Alt+I`   | `perch toggle` | show idle sessions and recent steps            |
|                 | `perch terminal` | leave the request to the agent's own prompt  |

Click a session to see its recent steps. `perch status` prints the state as
JSON and `perch quit` stops the island.

## Theming

The island loads `~/.config/perch/colors.css`, then the base `themes/style.css`
from this repo, then `~/.config/perch/style.css`, which starts empty and holds
your own overrides. It reloads them when a file in `~/.config/perch` changes.
`colors.css` is a copy of one of the four variants (everforest, gruvbox, dark
and light); `~/theme-switcher/perch/set-theme.sh` swaps it with the rest of the
desktop.

## Uninstall

```sh
perch setup --remove           # dry run
perch setup --remove --apply
```

`~/.config/perch` and `~/theme-switcher/perch` are left in place.

## Icons

The agent icons in `icons/` are rebuilt from the path data of
[Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT). The logos are
trademarks of their owners.

## Development

```sh
make check     # unit tests
perch demo     # two fake sessions and a permission request
```
