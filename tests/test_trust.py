import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from maneki import proto, trust


def fake_proc(root, chain):
    for pid, comm, ppid, args in chain:
        directory = root / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0\n")
        (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


class CallerTest(unittest.TestCase):
    def check(self, chain, pid=30):
        with tempfile.TemporaryDirectory() as tmp:
            fake_proc(Path(tmp), chain)
            return trust.caller_is_user(pid, tmp)

    def test_keybinds_launched_by_hyprland_are_you(self):
        self.assertTrue(self.check([
            (30, "python3", 20, ["/usr/bin/python3", "-I", "/x/maneki", "allow"]),
            (20, "sh", 10, ["/bin/sh", "-c", "maneki allow"]),
            (10, "Hyprland", 1, ["Hyprland"]),
        ]))

    def test_your_own_terminal_is_you(self):
        self.assertTrue(self.check([
            (30, "python3", 20, ["/usr/bin/python3", "-I", "/x/maneki", "allow"]),
            (20, "zsh", 15, ["zsh"]),
            (15, "kitty", 10, ["kitty"]),
            (10, "Hyprland", 1, ["Hyprland"]),
        ]))

    def test_anything_run_by_an_agent_is_not_you(self):
        for agent in (["claude"], ["opencode"], ["node", "/usr/bin/copilot"], ["codex"]):
            chain = [
                (30, "python3", 20, ["/usr/bin/python3", "-I", "/x/maneki", "allow"]),
                (20, "zsh", 15, ["zsh", "-c", "maneki allow"]),
                (15, "node-MainThread" if agent[0] == "node" else agent[0], 12, agent),
                (12, "kitty", 10, ["kitty"]),
                (10, "Hyprland", 1, ["Hyprland"]),
            ]
            self.assertFalse(self.check(chain), agent)

    def test_orphans_are_not_you(self):
        self.assertFalse(self.check([
            (30, "python3", 1, ["/usr/bin/python3", "-I", "/x/maneki", "allow"]),
        ]))

    def test_a_vanished_process_is_not_you(self):
        self.assertFalse(self.check([], pid=99))


class GenuineDaemonTest(unittest.TestCase):
    def make(self, tmp, args, script_name="maneki"):
        root = Path(tmp) / "proc"
        root.mkdir()
        bin_dir = Path(tmp) / "repo" / "bin"
        bin_dir.mkdir(parents=True)
        script = bin_dir / script_name
        script.write_text("")
        fake_proc(root, [(40, "python3", 1, args(str(script)))])
        os.symlink(tmp, root / "40" / "cwd")
        os.symlink(os.path.realpath(sys.executable), root / "40" / "exe")
        return root, os.path.realpath(script)

    def test_the_real_daemon_is_recognized(self):
        for flags in ("-I", "-IS"):
            with tempfile.TemporaryDirectory() as tmp:
                root, script = self.make(tmp, lambda s, f=flags: [sys.executable, f, s, "daemon"])
                self.assertTrue(trust.genuine_daemon(40, root, script), flags)

    def test_other_programs_are_rejected(self):
        cases = [
            lambda s: [sys.executable, s, "daemon"],
            lambda s: [sys.executable, "-S", s, "daemon"],
            lambda s: [sys.executable, "-I", s, "demo"],
            lambda s: [sys.executable, "-I", "/tmp/evil.py", "daemon"],
            lambda s: ["/usr/bin/nc", "-lU", "maneki.sock"],
        ]
        for args in cases:
            with tempfile.TemporaryDirectory() as tmp:
                root, script = self.make(tmp, args)
                self.assertFalse(trust.genuine_daemon(40, root, script), args("S"))


class FakeIslandTest(unittest.TestCase):
    def serve(self, tmp):
        os.makedirs(os.path.join(tmp, "maneki"))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(os.path.join(tmp, "maneki", "maneki.sock"))
        server.listen(4)

        def answer_everything():
            while True:
                try:
                    client, _ = server.accept()
                except OSError:
                    return
                client.sendall(proto.encode({"answer": "allow"}))
                client.close()

        threading.Thread(target=answer_everything, daemon=True).start()
        return server

    def test_hooks_ignore_an_island_that_is_not_maneki(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            server = self.serve(tmp)
            self.assertIsNone(proto.connect())
            self.assertIsNotNone(proto.connect(genuine=lambda pid: pid == os.getpid()))
            server.close()

    def test_runtime_dir_never_falls_back_to_tmp(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(proto.runtime_dir(), f"/run/user/{os.getuid()}/maneki")


if __name__ == "__main__":
    unittest.main()
