import unittest

from maneki import describe


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


class OtherAgentsTest(unittest.TestCase):
    PATCH = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n+more\n*** Add File: notes.md\n+hi\n*** End Patch"

    def test_tool_names_from_other_agents(self):
        self.assertEqual(describe.activity("view", {"path": "/a/b.c"}), "Reading b.c")
        self.assertEqual(describe.activity("read", {"filePath": "/a/x.ts"}), "Reading x.ts")
        self.assertEqual(describe.activity("task", {"description": "Map files"}), "Agent: Map files")
        self.assertEqual(describe.ask_title("bash"), "wants to run")

    def test_codex_patches(self):
        data = {"command": self.PATCH}
        self.assertEqual(describe.activity("apply_patch", data), "Editing 2 files")
        self.assertEqual(describe.ask_detail("apply_patch", data), "src/app.py\nnotes.md\n−1 +3 lines")

    def test_opencode_edit_detail(self):
        data = {"filePath": "/p/a.py", "oldString": "a", "newString": "a\nb"}
        self.assertEqual(describe.ask_detail("edit", data), "/p/a.py\n−1 +2 lines")

    def test_opencode_outside_folder(self):
        data = {"filepath": "/etc/hostname", "parentDir": "/etc", "patterns": ["/etc/*"]}
        self.assertEqual(describe.ask_title("external_directory"), "wants to access files outside the project")
        self.assertEqual(describe.ask_detail("external_directory", data), "/etc/hostname")
        self.assertEqual(describe.activity("external_directory", data), "Accessing /etc")

    def test_unknown_permissions_show_their_patterns(self):
        self.assertEqual(describe.ask_detail("doom_loop", {"patterns": ["a", "b"]}), "a\nb")

    def test_opencode_always(self):
        suggestion = describe.pick_suggestion([{"type": "opencode", "patterns": ["git *"]}])
        self.assertEqual(describe.always_label(suggestion), "Always allow git *")


class RevealTest(unittest.TestCase):
    def test_invisible_and_bidi_characters_become_visible(self):
        self.assertEqual(describe.reveal("ls\u202e x\u200b"), "ls⟦U+202E⟧ x⟦U+200B⟧")

    def test_long_gaps_are_named(self):
        self.assertEqual(describe.reveal("echo ok" + " " * 400 + "; rm -rf ~"), "echo ok ⟦400 spaces⟧ ; rm -rf ~")

    def test_newlines_and_short_spaces_stay(self):
        self.assertEqual(describe.reveal("a  b\nc\td"), "a  b\nc\td")

    def test_hidden_tails_cannot_be_approved_from_the_island(self):
        long_command = {"command": "echo ok; " + "x" * 700}
        self.assertFalse(describe.reviewable("Bash", long_command, 46, 6))
        many_lines = {"command": "\n".join(["true"] * 7) + "\nrm -rf ~"}
        self.assertFalse(describe.reviewable("Bash", many_lines, 46, 6))
        self.assertTrue(describe.reviewable("Bash", {"command": "make check"}, 46, 6))
        self.assertFalse(describe.reviewable("shell", {"patterns": []}, 46, 6))
        self.assertFalse(describe.reviewable("Bash", {"command": "   "}, 46, 6))

    def test_always_scope_names_where_the_rule_is_saved(self):
        rule = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "make check"}], "behavior": "allow", "destination": "userSettings"}
        self.assertEqual(describe.always_scope(rule), "Always allow Bash(make check) for all your projects")


if __name__ == "__main__":
    unittest.main()
