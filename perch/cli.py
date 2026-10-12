import json
import sys

from . import proto

NOT_RUNNING = "perch: the island is not running (start it with `perch daemon`)"
COMMANDS = {
    "allow": "allow the oldest pending permission request",
    "deny": "deny the oldest pending permission request",
    "always": "allow it and remember the rule Claude Code suggested",
    "toggle": "show or hide idle sessions and recent steps",
    "status": "print the island state as JSON",
    "quit": "stop the island",
}


def parser():
    import argparse

    root = argparse.ArgumentParser(
        prog="perch",
        description="A small island at the top of the screen that keeps an eye on your Claude Code sessions.",
    )
    sub = root.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("daemon", help="run the island")
    sub.add_parser("hook", help="entry point for Claude Code hooks (JSON on stdin)")
    for name, text in COMMANDS.items():
        sub.add_parser(name, help=text)
    sub.add_parser("demo", help="send fake sessions to the island")
    setup = sub.add_parser("setup", help="install hooks, keybinds, autostart and themes (dry run by default)")
    setup.add_argument("--apply", action="store_true", help="make the changes instead of only showing them")
    setup.add_argument("--remove", action="store_true", help="undo what setup added")
    return root


def send_command(name):
    reply = proto.request({"type": "cmd", "cmd": name})
    if reply is None:
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    if name == "status":
        print(json.dumps(reply, indent=2, ensure_ascii=False))
    elif not reply.get("ok"):
        print(f"perch: {reply.get('error', 'failed')}", file=sys.stderr)
    return 0 if reply.get("ok") else 1


def main(argv):
    if argv[:1] == ["hook"]:
        from .hook import main as hook

        return hook()
    args = parser().parse_args(argv)
    if args.command == "daemon":
        from .daemon import main as daemon

        return daemon()
    if args.command == "demo":
        from .demo import main as demo

        return demo()
    if args.command == "setup":
        from .setup import main as setup

        return setup(apply_changes=args.apply, remove=args.remove)
    return send_command(args.command)
