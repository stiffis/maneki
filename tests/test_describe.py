import unittest

from perch import describe


class ActivityTest(unittest.TestCase):
    def test_bash_prefers_description(self):
        data = {"command": "make check", "description": "Run the test gate"}
        self.assertEqual(describe.activity("Bash", data), "Run the test gate")

    def test_bash_falls_back_to_first_command_line(self):
        self.assertEqual(describe.activity("Bash", {"command": "ls -la\npwd"}), "ls -la")

    def test_file_tools_show_the_file_name(self):
        self.assertEqual(describe.activity("Read", {"file_path": "/a/b/cap3.tex"}), "Reading cap3.tex")
        self.assertEqual(describe.activity("Edit", {"file_path": "/a/b.py"}), "Editing b.py")
        self.assertEqual(describe.activity("Write", {"file_path": "/a/c.md"}), "Writing c.md")

    def test_web_fetch_shows_the_host(self):
        self.assertEqual(describe.activity("WebFetch", {"url": "https://example.org/x"}), "Fetching example.org")

    def test_mcp_tools_show_server_and_tool(self):
        self.assertEqual(describe.activity("mcp__github__create_issue", {}), "github: create_issue")

    def test_unknown_tool_uses_its_name(self):
        self.assertEqual(describe.activity("Frobnicate", None), "Frobnicate")


class AskTest(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(describe.ask_title("Bash"), "wants to run")
        self.assertEqual(describe.ask_title("mcp__slack__post"), "wants to use slack: post")
        self.assertEqual(describe.ask_title("Mystery"), "wants to use Mystery")

    def test_edit_detail_counts_lines(self):
        data = {"file_path": "/x/y.py", "old_string": "a\nb", "new_string": "a\nb\nc"}
        self.assertEqual(describe.ask_detail("Edit", data), "/x/y.py\n−2 +3 lines")

    def test_long_commands_are_clipped(self):
        detail = describe.ask_detail("Bash", {"command": "x" * 1000})
        self.assertEqual(len(detail), 600)
        self.assertTrue(detail.endswith("…"))


class SuggestionTest(unittest.TestCase):
    def test_mode_changes_are_never_picked(self):
        suggestions = [
            {"type": "setMode", "mode": "acceptEdits", "destination": "session"},
            {"type": "addDirectories", "directories": ["/tmp/x"], "destination": "session"},
        ]
        self.assertEqual(describe.pick_suggestion(suggestions)["type"], "addDirectories")

    def test_only_allow_rules_are_picked(self):
        deny = {"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "deny"}
        self.assertIsNone(describe.pick_suggestion([deny]))

    def test_always_label(self):
        rule = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "make check"}], "behavior": "allow"}
        self.assertEqual(describe.always_label(rule), "Always allow Bash(make check)")
        self.assertEqual(describe.always_label(None), "")


class DurationTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(describe.duration(None), "")
        self.assertEqual(describe.duration(-3), "0s")
        self.assertEqual(describe.duration(42.9), "42s")
        self.assertEqual(describe.duration(60), "1m")
        self.assertEqual(describe.duration(3599), "59m")
        self.assertEqual(describe.duration(3600 + 5 * 60 + 9), "1h 5m")


if __name__ == "__main__":
    unittest.main()
