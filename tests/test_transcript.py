import json
import os
import tempfile
import unittest
from pathlib import Path

from perch import transcript


def line(entry):
    return json.dumps(entry, separators=(",", ":")) + "\n"


TOOL_USE = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1"}]}}
RESULT = {
    "type": "user",
    "timestamp": "2026-10-12T01:00:48.099Z",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True}]},
}
INTERRUPT = {
    "type": "user",
    "timestamp": "2026-10-12T01:00:48.107Z",
    "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]},
}


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "session.jsonl"
        self.path.write_text(line(TOOL_USE))

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, text):
        with open(self.path, "a") as handle:
            handle.write(text)

    def test_finds_results_and_interrupts_written_after_the_offset(self):
        offset = transcript.end_offset(self.path)
        self.append(line(RESULT) + line(INTERRUPT))
        found, offset = transcript.scan(self.path, offset, self.tmp.name)
        self.assertEqual([kind for kind, _, _ in found], ["result", "interrupted"])
        self.assertEqual(found[0][1], "toolu_1")
        self.assertAlmostEqual(found[0][2], 1791766848.099, places=3)
        self.assertEqual(offset, self.path.stat().st_size)

    def test_tool_use_alone_is_not_a_result(self):
        found, _ = transcript.scan(self.path, 0, self.tmp.name)
        self.assertEqual(found, [])

    def test_partial_lines_wait_for_the_newline(self):
        offset = transcript.end_offset(self.path)
        text = line(RESULT)
        self.append(text[:20])
        found, offset = transcript.scan(self.path, offset, self.tmp.name)
        self.assertEqual(found, [])
        self.append(text[20:])
        found, _ = transcript.scan(self.path, offset, self.tmp.name)
        self.assertEqual(len(found), 1)

    def test_assistant_text_mentioning_the_marker_is_not_an_interrupt(self):
        entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}}
        self.append(line(entry))
        found, _ = transcript.scan(self.path, 0, self.tmp.name)
        self.assertEqual(found, [])

    def test_missing_file_is_harmless(self):
        self.assertEqual(transcript.scan(Path(self.tmp.name) / "nope.jsonl", 5, self.tmp.name), ([], 5))

    def test_files_outside_the_projects_folder_are_ignored(self):
        self.append(line(RESULT))
        found, offset = transcript.scan(self.path, 0, "/somewhere/else")
        self.assertEqual((found, offset), ([], 0))

    def test_a_fifo_does_not_hang_the_island(self):
        fifo = Path(self.tmp.name) / "trap.jsonl"
        os.mkfifo(fifo)
        self.assertEqual(transcript.scan(fifo, 0, self.tmp.name), ([], 0))


if __name__ == "__main__":
    unittest.main()
