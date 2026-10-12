import json
import os
import socket
import tempfile
import threading
import unittest

from maneki import hypr

LAPTOP = {"name": "eDP-1", "focused": True, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0, "name": ""}}
SAMSUNG = {"name": "HDMI-A-1", "focused": False, "activeWorkspace": {"id": 4}, "specialWorkspace": {"id": 0, "name": ""}}


def client(workspace, mode):
    return {"workspace": {"id": workspace}, "fullscreen": mode}


class DesktopTest(unittest.TestCase):
    def test_the_focused_monitor_wins(self):
        self.assertEqual(hypr.focused([SAMSUNG, LAPTOP])["name"], "eDP-1")
        self.assertIsNone(hypr.focused([]))
        self.assertIsNone(hypr.focused(None))

    def test_only_true_fullscreen_counts(self):
        self.assertTrue(hypr.fullscreen(LAPTOP, [client(1, 2)]))
        self.assertFalse(hypr.fullscreen(LAPTOP, [client(1, 1)]))
        self.assertFalse(hypr.fullscreen(LAPTOP, [client(4, 2)]))

    def test_an_open_special_workspace_is_what_you_see(self):
        dropdown = dict(LAPTOP, specialWorkspace={"id": -98, "name": "special:term_1"})
        self.assertEqual(hypr.visible_workspace(dropdown), -98)
        self.assertFalse(hypr.fullscreen(dropdown, [client(1, 2)]))
        self.assertTrue(hypr.fullscreen(dropdown, [client(-98, 2)]))

    def test_events(self):
        self.assertEqual(hypr.parse_event("focusedmon>>HDMI-A-1,4"), ("focusedmon", "HDMI-A-1,4"))
        self.assertEqual(hypr.parse_event("fullscreen>>1"), ("fullscreen", "1"))

    def test_directory_needs_a_clean_signature(self):
        self.assertIsNone(hypr.directory({"XDG_RUNTIME_DIR": "/run/user/1"}))
        self.assertIsNone(hypr.directory({"XDG_RUNTIME_DIR": "/run/user/1", "HYPRLAND_INSTANCE_SIGNATURE": "../x"}))
        env = {"XDG_RUNTIME_DIR": "/run/user/1", "HYPRLAND_INSTANCE_SIGNATURE": "abc"}
        self.assertEqual(hypr.directory(env), "/run/user/1/hypr/abc")


class QueryTest(unittest.TestCase):
    def test_query_reads_the_whole_json_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "hypr", "sig"))
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(os.path.join(tmp, "hypr", "sig", ".socket.sock"))
            server.listen(1)
            asked = []

            def reply():
                conn, _ = server.accept()
                asked.append(conn.recv(100))
                conn.sendall(json.dumps([LAPTOP]).encode())
                conn.close()

            threading.Thread(target=reply, daemon=True).start()
            env = {"XDG_RUNTIME_DIR": tmp, "HYPRLAND_INSTANCE_SIGNATURE": "sig"}
            self.assertEqual(hypr.query("monitors", env=env), [LAPTOP])
            self.assertEqual(asked, [b"j/monitors"])
            server.close()

    def test_query_without_hyprland_is_none(self):
        self.assertIsNone(hypr.query("monitors", env={}))


if __name__ == "__main__":
    unittest.main()
