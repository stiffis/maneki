import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from perch import hook


class ReduceTest(unittest.TestCase):
    def test_keeps_known_fields_and_drops_tool_response(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "tool_name": "Read",
            "tool_input": {"file_path": "/a"},
            "tool_response": {"content": "x" * 100000},
        }
        event = hook.reduce(payload, 12.5, 99)
        self.assertNotIn("tool_response", event)
        self.assertEqual(event["ts"], 12.5)
        self.assertEqual(event["pid"], 99)
        self.assertEqual(event["agent"], "claude")
        self.assertEqual(event["tool_input"], {"file_path": "/a"})

    def test_clips_long_strings_deep_inside(self):
        event = hook.reduce({"tool_input": {"edits": [{"new_string": "y" * 9000}]}}, 0, None)
        self.assertEqual(len(event["tool_input"]["edits"][0]["new_string"]), hook.STRING_LIMIT)
        self.assertNotIn("pid", event)

    def test_prompt_has_a_tighter_limit(self):
        event = hook.reduce({"prompt": "p" * 1000}, 0, None)
        self.assertEqual(len(event["prompt"]), hook.LIMITS["prompt"])


class ClaudePidTest(unittest.TestCase):
    def fake_proc(self, root, chain):
        for pid, comm, ppid in chain:
            directory = root / str(pid)
            directory.mkdir()
            (directory / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0\n")

    def test_walks_up_to_the_claude_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_proc(root, [(30, "python3", 20), (20, "sh", 10), (10, "claude", 1)])
            self.assertEqual(hook.agent_pid(("claude",), 30, root), 10)

    def test_handles_names_with_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_proc(root, [(30, "we (ird) name", 10), (10, "claude", 1)])
            self.assertEqual(hook.agent_pid(("claude",), 30, root), 10)

    def test_returns_none_without_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_proc(root, [(30, "python3", 20), (20, "zsh", 1)])
            self.assertIsNone(hook.agent_pid(("claude",), 30, root))


class DecisionTest(unittest.TestCase):
    def test_allow(self):
        output = hook.decision_output("allow", None)
        self.assertEqual(output["hookSpecificOutput"]["decision"], {"behavior": "allow"})

    def test_always_echoes_the_suggestion(self):
        rule = {"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow"}
        decision = hook.decision_output("always", rule)["hookSpecificOutput"]["decision"]
        self.assertEqual(decision["updatedPermissions"], [rule])

    def test_deny_tells_claude_why(self):
        decision = hook.decision_output("deny", None)["hookSpecificOutput"]["decision"]
        self.assertEqual(decision["behavior"], "deny")
        self.assertIn("perch", decision["message"])

    def test_no_answer_means_no_output(self):
        self.assertIsNone(hook.decision_output(None, None))


class RunWithoutIslandTest(unittest.TestCase):
    def run_hook(self, payload):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            stdout = io.StringIO()
            hook.run(io.StringIO(json.dumps(payload)), stdout)
            return stdout.getvalue()

    def test_permission_request_falls_back_to_the_terminal(self):
        payload = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.assertEqual(self.run_hook(payload), "")

    def test_other_events_print_nothing(self):
        self.assertEqual(self.run_hook({"hook_event_name": "Stop"}), "")

    def test_garbage_input_is_ignored(self):
        stdout = io.StringIO()
        hook.run(io.StringIO("not json"), stdout)
        self.assertEqual(stdout.getvalue(), "")


class AgentPayloadTest(unittest.TestCase):
    def test_copilot_camel_case_payload_is_normalized(self):
        payload = {"sessionId": "c1", "toolName": "bash", "toolArgs": '{"command": "ls"}', "cwd": "/x"}
        event = hook.normalize(payload, "preToolUse")
        self.assertEqual(event["hook_event_name"], "PreToolUse")
        self.assertEqual(event["session_id"], "c1")
        self.assertEqual(event["tool_input"], {"command": "ls"})

    def test_missing_tool_ids_match_between_pre_and_post(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        pre = hook.normalize(data, "PreToolUse")
        post = hook.normalize(data, "PostToolUse")
        self.assertEqual(pre["tool_use_id"], post["tool_use_id"])

    def test_permission_requests_keep_their_missing_id(self):
        event = hook.normalize({"tool_name": "Bash", "tool_input": {}}, "PermissionRequest")
        self.assertNotIn("tool_use_id", event)

    def test_copilot_never_waits_for_the_island(self):
        event = hook.reduce({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, 0, None, "copilot")
        self.assertFalse(hook.wants_answer(event))
        codex = hook.reduce({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, 0, None, "codex")
        self.assertTrue(hook.wants_answer(codex))

    def test_options(self):
        self.assertEqual(hook.options(["--agent", "copilot", "--event", "Stop"]), ("copilot", "Stop"))
        self.assertEqual(hook.options([]), ("claude", None))


class CodexOutputTest(unittest.TestCase):
    def test_codex_stop_gets_an_empty_json_object(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            stdout = io.StringIO()
            hook.run(io.StringIO('{"hook_event_name": "Stop"}'), stdout, "codex")
            self.assertEqual(stdout.getvalue(), "{}")
            stdout = io.StringIO()
            hook.run(io.StringIO('{"hook_event_name": "Stop"}'), stdout, "claude")
            self.assertEqual(stdout.getvalue(), "")


class LauncherTest(unittest.TestCase):
    def test_node_scripts_are_found_by_their_script_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, comm, ppid, args in [
                (30, "python3", 20, [b"/usr/bin/python3", b"-I", b"/x/perch"]),
                (20, "bash", 10, [b"bash", b"-c", b"perch hook --agent copilot"]),
                (10, "node", 1, [b"node", b"/usr/bin/copilot"]),
            ]:
                directory = root / str(pid)
                directory.mkdir()
                (directory / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0\n")
                (directory / "cmdline").write_bytes(b"\0".join(args) + b"\0")
            self.assertEqual(hook.agent_pid(("copilot",), 30, root), 10)


if __name__ == "__main__":
    unittest.main()
