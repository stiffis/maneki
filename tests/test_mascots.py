import itertools
import unittest

from maneki import mascots
from maneki.mascots import ASKING, DONE, IDLE, INTERRUPTED, SPRITES, STATES, WORKING


def poses(sprite):
    for state, frame, age, rest, blink in itertools.product(STATES, range(mascots.CYCLE), (0.0, 1.5, 9.0), (False, True), (False, True)):
        yield state, sprite.frame(state, frame, age, rest, blink)


class SpriteTest(unittest.TestCase):
    def test_every_base_row_fits_the_sprite(self):
        for sprite in SPRITES.values():
            for line in sprite.base:
                self.assertLessEqual(sprite.left + len(line), sprite.width, sprite.name)

    def test_every_pose_stays_inside_the_sprite_and_uses_its_palette(self):
        for sprite in SPRITES.values():
            for state, pose in poses(sprite):
                self.assertLessEqual(pose.dy, 3)
                for part in pose.parts:
                    for row, col, char in part:
                        where = f"{sprite.name} {state} ({row}, {col})"
                        self.assertTrue(0 <= row < sprite.height, where)
                        self.assertTrue(0 <= col + sprite.left < sprite.width, where)
                        self.assertTrue(char == "." or char in sprite.palette, where)

    def test_every_base_pixel_has_a_color(self):
        for sprite in SPRITES.values():
            used = set("".join(sprite.base)) - {"."}
            self.assertLessEqual(used, set(sprite.palette), sprite.name)

    def test_pixels_group_cells_by_color(self):
        pose = mascots.pose("cat", WORKING, 0, 9.0, True, False)
        groups = mascots.pixels("cat", pose.parts, pose.drop)
        cells = [cell for _, found in groups for cell in found]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertTrue(all(0 <= x < 27 and 0 <= y < 20 for x, y in cells))

    def test_a_nod_drops_the_head_one_row(self):
        pose = mascots.pose("osaka", IDLE, 2, 0.0, False, False)
        self.assertEqual(pose.drop, 15)
        rows = {y for _, found in mascots.pixels("osaka", pose.parts, pose.drop) for _, y in found}
        self.assertNotIn(0, rows)

    def test_every_bubble_is_known_ahead(self):
        for sprite in SPRITES.values():
            known = set(mascots.bubbles(sprite.name))
            self.assertNotIn("", known)
            for _, pose in poses(sprite):
                self.assertTrue(pose.bubble == "" or pose.bubble in known, pose.bubble)
        self.assertIn("sata andagi!", mascots.bubbles("osaka"))

    def test_osaka_notices_a_request_late(self):
        self.assertEqual(mascots.pose("osaka", ASKING, 1, 0.5, False, False).bubble, "…")
        self.assertEqual(mascots.pose("osaka", ASKING, 1, 2.0, False, False).bubble, "huh?!")


class MoodTest(unittest.TestCase):
    def test_the_most_urgent_session_wins(self):
        self.assertEqual(mascots.mood(["working", "done"], True), ASKING)
        self.assertEqual(mascots.mood(["working", "attention"], False), ASKING)
        self.assertEqual(mascots.mood(["done", "working", "idle"], False), WORKING)
        self.assertEqual(mascots.mood(["idle", "done"], False), DONE)
        self.assertEqual(mascots.mood([], False), IDLE)

    def test_finishing_a_turn_is_a_feeling(self):
        before = {"a": ("working", "Editing x"), "b": ("idle", "")}
        self.assertEqual(mascots.emotion(before, {"a": ("done", "Done"), "b": ("idle", "")}), DONE)

    def test_an_interruption_is_a_feeling(self):
        before = {"a": ("working", "Thinking…")}
        self.assertEqual(mascots.emotion(before, {"a": ("idle", "Interrupted")}), INTERRUPTED)

    def test_finishing_beats_an_interruption(self):
        before = {"a": ("working", ""), "b": ("working", "")}
        after = {"a": ("idle", "Interrupted"), "b": ("done", "Done")}
        self.assertEqual(mascots.emotion(before, after), DONE)

    def test_nothing_new_is_no_feeling(self):
        self.assertIsNone(mascots.emotion({}, {"a": ("done", "Done")}))
        self.assertIsNone(mascots.emotion({"a": ("done", "Done")}, {"a": ("done", "Done")}))
        self.assertIsNone(mascots.emotion({"a": ("idle", "Interrupted")}, {"a": ("idle", "Interrupted")}))


if __name__ == "__main__":
    unittest.main()
