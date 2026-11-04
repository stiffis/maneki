import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maneki import config


class ParseTest(unittest.TestCase):
    def test_an_empty_file_keeps_the_defaults(self):
        self.assertEqual(config.parse(""), (config.DEFAULTS, []))

    def test_a_known_mascot_and_scale_are_used(self):
        settings, problems = config.parse('mascot = "osaka"\nmascot_scale = 3\n')
        self.assertEqual(settings, {"mascot": "osaka", "mascot_scale": 3})
        self.assertEqual(problems, [])

    def test_the_mascot_can_be_turned_off(self):
        self.assertEqual(config.parse('mascot = "none"')[0]["mascot"], "none")

    def test_bad_values_fall_back_to_the_defaults(self):
        for text in ('mascot = "dog"', "mascot_scale = 9", "mascot_scale = true", 'mascot_scale = "2"'):
            settings, problems = config.parse(text)
            self.assertEqual(settings, config.DEFAULTS, text)
            self.assertEqual(len(problems), 1, text)


class RotationTest(unittest.TestCase):
    def test_the_last_mascot_wraps_to_the_first(self):
        names = tuple(config.SPRITES)
        self.assertEqual(config.next_mascot(names[0]), names[1])
        self.assertEqual(config.next_mascot(names[-1]), names[0])

    def test_no_mascot_starts_from_the_first(self):
        self.assertEqual(config.next_mascot("none"), tuple(config.SPRITES)[0])

    def test_a_missing_line_is_added_on_top(self):
        self.assertEqual(config.with_mascot("mascot_scale = 3\n", "cat"), 'mascot = "cat"\nmascot_scale = 3\n')

    def test_the_scale_line_is_not_mistaken_for_the_mascot(self):
        text = 'mascot_scale = 3\nmascot = "cat"\n'
        self.assertEqual(config.with_mascot(text, "osaka"), 'mascot_scale = 3\nmascot = "osaka"\n')


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.file = Path(self.home.name) / "maneki" / "config.toml"

    def test_a_missing_file_gives_the_defaults(self):
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_a_broken_file_gives_the_defaults(self):
        self.file.parent.mkdir()
        self.file.write_text("mascot = ")
        with mock.patch("sys.stderr"):
            self.assertEqual(config.load(), config.DEFAULTS)

    def test_rotating_goes_through_every_mascot_and_back(self):
        seen = [config.rotate_mascot() for _ in config.SPRITES]
        self.assertEqual(sorted(seen), sorted(config.SPRITES))
        self.assertEqual(seen[-1], config.DEFAULTS["mascot"])
        self.assertEqual(config.load()["mascot"], seen[-1])

    def test_rotating_keeps_the_rest_of_the_file(self):
        self.file.parent.mkdir()
        self.file.write_text('# mine\nmascot_scale = 3\nmascot = "osaka"\n')
        name = config.rotate_mascot()
        self.assertEqual(self.file.read_text(), f'# mine\nmascot_scale = 3\nmascot = "{name}"\n')
        self.assertEqual(config.load()["mascot_scale"], 3)

    def test_rotating_leaves_no_temporary_files(self):
        config.rotate_mascot()
        self.assertEqual([path.name for path in self.file.parent.iterdir()], ["config.toml"])

    def test_the_file_is_read(self):
        self.file.parent.mkdir()
        self.file.write_text('mascot = "osaka"\n')
        self.assertEqual(config.load()["mascot"], "osaka")


if __name__ == "__main__":
    unittest.main()
