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

    def test_the_file_is_read(self):
        self.file.parent.mkdir()
        self.file.write_text('mascot = "osaka"\n')
        self.assertEqual(config.load()["mascot"], "osaka")


if __name__ == "__main__":
    unittest.main()
