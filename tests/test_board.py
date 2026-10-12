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
        self.board.apply(event("UserPromptSubmit", 1, pid=4242))
        changed, _ = self.board.expire(2, lambda pid: pid != 4242)
        self.assertTrue(changed)
        self.assertEqual(self.board.sessions, {})

    def test_idle_prompt_settles_a_stuck_session(self):
        self.board.apply(event("UserPromptSubmit", 1))
        self.board.apply(event("Notification", 70, notification_type="idle_prompt"))
        self.assertEqual(self.board.sessions["s1"].status, IDLE)

    def test_duplicate_project_names_get_a_suffix(self):
        self.board.apply(event("UserPromptSubmit", 1, session="ses_01hAAAA"))
        self.board.apply(event("UserPromptSubmit", 1, session="ses_01hBBBB"))
        self.assertEqual(sorted(self.board.names().values()), ["proj #AAAA", "proj #BBBB"])

    def test_questions_need_you_until_answered(self):
        self.board.apply(event("UserPromptSubmit", 1, agent="opencode"))
        self.board.apply(event("PreToolUse", 2, agent="opencode", tool_name="question", tool_input={}, tool_use_id="q1"))
        session = self.board.sessions["s1"]
        self.assertEqual((session.status, session.activity), ("attention", "Has a question for you"))
        self.board.apply(event("PostToolUse", 3, agent="opencode", tool_name="question", tool_input={}, tool_use_id="q1"))
        self.assertEqual((session.status, session.activity), (WORKING, "Thinking…"))


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


class ElapsedTest(unittest.TestCase):
    def test_counts_while_busy_and_freezes_when_done(self):
        board = Board()
        board.apply(event("UserPromptSubmit", 100))
        session = board.sessions["s1"]
        self.assertEqual(session.elapsed(160), 60)
        board.apply(event("Stop", 280))
        self.assertEqual(session.elapsed(999), 180)
        board.apply(event("UserPromptSubmit", 1000))
        self.assertEqual(session.elapsed(1005), 5)

    def test_interrupt_freezes_the_clock(self):
        board = Board()
        board.apply(event("UserPromptSubmit", 100))
        board.interrupted("s1", 130, 131)
        self.assertEqual(board.sessions["s1"].elapsed(500), 31)


EXPLORE = {"description": "Map the theme files", "prompt": "...", "subagent_type": "Explore"}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.board = Board(done_seconds=10)
        self.board.apply(event("UserPromptSubmit", 1))
        self.board.apply(event("PreToolUse", 2, tool_name="Agent", tool_input=EXPLORE, tool_use_id="a1"))
        self.board.apply(event("SubagentStart", 3, agent_id="ag1", agent_type="Explore"))
        self.session = self.board.sessions["s1"]

    def test_agent_takes_the_launch_description(self):
        agent = self.session.agents["ag1"]
        self.assertEqual((agent.kind, agent.label, agent.state), ("Explore", "Map the theme files", "running"))

    def test_agent_tools_count_as_agent_steps_not_session_steps(self):
        read = {"file_path": "/x/style.css"}
        self.board.apply(event("PreToolUse", 4, tool_name="Read", tool_input=read, tool_use_id="r1", agent_id="ag1", agent_type="Explore"))
        self.board.apply(event("PostToolUse", 5, tool_name="Read", tool_input=read, tool_use_id="r1", agent_id="ag1", agent_type="Explore"))
        agent = self.session.agents["ag1"]
        self.assertEqual((agent.steps, agent.activity), (1, "Reading style.css"))
        self.assertEqual([step.key for step in self.session.steps], ["a1"])
        self.assertEqual(self.session.activity, "Agent: Map the theme files")

    def test_stop_finishes_the_agent_and_expire_clears_it(self):
        self.board.apply(event("SubagentStop", 9, agent_id="ag1", agent_type="Explore", last_assistant_message="Found 5 files."))
        agent = self.session.agents["ag1"]
        self.assertEqual((agent.state, agent.activity, agent.elapsed(100)), ("ok", "Found 5 files.", 6))
        self.board.expire(18.9, lambda pid: True)
        self.assertIn("ag1", self.session.agents)
        self.board.expire(19, lambda pid: True)
        self.assertNotIn("ag1", self.session.agents)

    def test_internal_agent_stops_are_ignored(self):
        self.board.apply(event("SubagentStop", 9, agent_id="internal", agent_type=""))
        self.assertNotIn("internal", self.session.agents)

    def test_running_agents_keep_a_finished_session_visible(self):
        self.board.apply(event("Stop", 10))
        self.board.expire(30, lambda pid: True)
        self.assertEqual(self.session.status, IDLE)
        self.assertEqual(self.board.visible(), [self.session])

    def test_permission_from_an_agent_names_it(self):
        ask = self.board.add_ask(event("PermissionRequest", 6, tool_name="Bash", tool_input=BASH, agent_id="ag1", agent_type="Explore"))
        self.assertEqual(ask.agent, "Map the theme files")
        self.assertEqual(self.session.agents["ag1"].activity, "Waiting for permission")

    def test_interrupt_fails_running_agents(self):
        self.board.interrupted("s1", 7, 8)
        self.assertEqual(self.session.agents["ag1"].state, "error")


class MultiAgentTest(unittest.TestCase):
    def test_codex_interrupt_settles_the_session(self):
        board = Board()
        board.apply(event("UserPromptSubmit", 1, agent="codex"))
        board.apply(event("Interrupt", 5, agent="codex"))
        session = board.sessions["s1"]
        self.assertEqual((session.agent, session.status, session.activity), ("codex", IDLE, "Interrupted"))

    def test_only_claude_transcripts_are_followed(self):
        board = Board()
        board.apply(event("UserPromptSubmit", 1, agent="codex", transcript_path="/t/codex.jsonl"))
        board.apply(event("UserPromptSubmit", 1, session="s2", transcript_path="/t/claude.jsonl"))
        self.assertEqual([s.id for s in board.watched()], ["s2"])

    def test_opencode_permission_resolved_in_its_own_ui(self):
        board = Board()
        ask = board.add_ask(event("PermissionRequest", 2, agent="opencode", tool_name="bash", tool_input={"command": "ls"}, tool_use_id="per_1"))
        self.assertEqual(ask.tool_use_id, "per_1")
        self.assertEqual(board.apply(event("PermissionResolved", 3, agent="opencode", tool_use_id="per_1")), [ask.id])

    def test_opencode_task_tool_launches_an_agent(self):
        board = Board()
        board.apply(event("PreToolUse", 1, agent="opencode", tool_name="task", tool_input={"description": "Scan", "subagent_type": "general"}, tool_use_id="c1"))
        board.apply(event("SubagentStart", 2, agent="opencode", agent_id="child", agent_type="general"))
        self.assertEqual(board.sessions["s1"].agents["child"].label, "Scan")


if __name__ == "__main__":
    unittest.main()
