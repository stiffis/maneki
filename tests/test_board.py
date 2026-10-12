import unittest

from perch.board import ASKING, DONE, IDLE, WORKING, Board


def event(name, ts, session="s1", **fields):
    return {"hook_event_name": name, "session_id": session, "cwd": "/home/x/proj", "ts": ts, **fields}


BASH = {"command": "make check"}


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.board = Board(done_seconds=10)

    def test_a_turn_goes_working_then_done_then_idle(self):
        self.board.apply(event("UserPromptSubmit", 1))
        self.assertEqual(self.board.sessions["s1"].status, WORKING)
        self.board.apply(event("PreToolUse", 2, tool_name="Read", tool_input={"file_path": "/a/b.txt"}, tool_use_id="t1"))
        self.assertEqual(self.board.sessions["s1"].activity, "Reading b.txt")
        self.board.apply(event("PostToolUse", 3, tool_name="Read", tool_input={"file_path": "/a/b.txt"}, tool_use_id="t1"))
        self.board.apply(event("Stop", 4, last_assistant_message="All good."))
        session = self.board.sessions["s1"]
        self.assertEqual((session.status, session.message), (DONE, "All good."))
        self.assertEqual([step.state for step in session.steps], ["ok"])
        self.board.expire(13.9, lambda pid: True)
        self.assertEqual(session.status, DONE)
        self.board.expire(14.0, lambda pid: True)
        self.assertEqual(session.status, IDLE)

    def test_late_events_do_not_rewind_the_status(self):
        self.board.apply(event("Stop", 5))
        self.board.apply(event("PreToolUse", 4, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))
        self.assertEqual(self.board.sessions["s1"].status, DONE)

    def test_post_before_pre_keeps_one_finished_step(self):
        self.board.apply(event("PostToolUse", 3, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))
        self.board.apply(event("PreToolUse", 2, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))
        steps = list(self.board.sessions["s1"].steps)
        self.assertEqual([(s.key, s.state) for s in steps], [("t1", "ok")])

    def test_session_end_drops_the_session(self):
        self.board.apply(event("UserPromptSubmit", 1))
        self.board.apply(event("SessionEnd", 2, reason="prompt_input_exit"))
        self.assertEqual(self.board.sessions, {})

    def test_dead_claude_process_drops_the_session(self):
        self.board.apply(event("UserPromptSubmit", 1, claude_pid=4242))
        changed, _ = self.board.expire(2, lambda pid: pid != 4242)
        self.assertTrue(changed)
        self.assertEqual(self.board.sessions, {})

    def test_idle_prompt_settles_a_stuck_session(self):
        self.board.apply(event("UserPromptSubmit", 1))
        self.board.apply(event("Notification", 70, notification_type="idle_prompt"))
        self.assertEqual(self.board.sessions["s1"].status, IDLE)

    def test_duplicate_project_names_get_a_suffix(self):
        self.board.apply(event("UserPromptSubmit", 1, session="aaaa1111"))
        self.board.apply(event("UserPromptSubmit", 1, session="bbbb2222"))
        self.assertEqual(sorted(self.board.names().values()), ["proj #aaaa", "proj #bbbb"])


class AskTest(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.board.apply(event("UserPromptSubmit", 1))
        self.board.apply(event("PreToolUse", 2, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))

    def test_ask_links_to_the_matching_tool_call(self):
        ask = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input=BASH))
        self.assertEqual(ask.tool_use_id, "t1")
        self.assertEqual(self.board.sessions["s1"].status, ASKING)

    def test_late_pre_tool_use_links_the_ask(self):
        ask = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input={"command": "ls"}))
        self.assertIsNone(ask.tool_use_id)
        self.board.apply(event("PreToolUse", 2.5, tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="t2"))
        self.assertEqual(ask.tool_use_id, "t2")

    def test_post_tool_use_resolves_the_ask(self):
        ask = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input=BASH))
        resolved = self.board.apply(event("PostToolUse", 4, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))
        self.assertEqual(resolved, [ask.id])

    def test_transcript_result_resolves_the_ask(self):
        ask = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input=BASH))
        self.assertEqual(self.board.result_seen("s1", "t1"), [ask.id])
        self.assertEqual(self.board.result_seen("s1", "other"), [])

    def test_removing_the_last_ask_goes_back_to_working(self):
        ask = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input=BASH))
        self.board.remove_ask(ask.id)
        self.assertEqual(self.board.sessions["s1"].status, WORKING)

    def test_oldest_ask_comes_first(self):
        first = self.board.add_ask(event("PermissionRequest", 3, tool_name="Bash", tool_input=BASH))
        self.board.add_ask(event("PermissionRequest", 4, session="s2", tool_name="Bash", tool_input=BASH))
        self.assertIs(self.board.oldest_ask(), first)


class InterruptTest(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.board.apply(event("UserPromptSubmit", 100))
        self.board.apply(event("PreToolUse", 101, tool_name="Bash", tool_input=BASH, tool_use_id="t1"))

    def test_interrupt_in_this_turn_settles_the_session(self):
        ask = self.board.add_ask(event("PermissionRequest", 102, tool_name="Bash", tool_input=BASH))
        resolved = self.board.interrupted("s1", 103, 104)
        session = self.board.sessions["s1"]
        self.assertEqual((session.status, session.activity), (IDLE, "Interrupted"))
        self.assertEqual(resolved, [ask.id])
        self.assertEqual([step.state for step in session.steps], ["error"])

    def test_interrupt_from_an_earlier_turn_is_ignored(self):
        self.assertEqual(self.board.interrupted("s1", 99, 104), [])
        self.assertEqual(self.board.sessions["s1"].status, WORKING)


if __name__ == "__main__":
    unittest.main()
