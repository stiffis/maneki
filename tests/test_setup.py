import json
import os
import tempfile
import unittest
from pathlib import Path

from maneki import setup

FOREIGN = {"type": "command", "command": "/usr/bin/other-tool"}
SETTINGS = {"model": "opus", "hooks": {"Stop": [{"hooks": [FOREIGN]}]}}
SWITCHER = "\n".join(
    [
        "#!/bin/bash",
        'run_step "Aplicando tema rofi"              rofi/set-theme.sh        "$MODE"',
        setup.SWITCH_ANCHOR,
        'run_step "Aplicando tema zathura"           zathura/set-theme.sh     "$MODE"',
        "",
    ]
)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.t = setup.targets(self.home)
        self.write(self.t["settings"], json.dumps(SETTINGS, indent=2) + "\n")
        self.write(self.t["keybinds"], "bind = $mainMod, T, exec, foo\n")
        self.write(self.t["startup"], "exec-once = waybar\n")
        self.write(self.t["switcher"], SWITCHER)
        self.write(self.home / "theme-switcher" / ".current-family", "gruvbox\n")
        self.write(self.home / "theme-switcher" / ".current-theme", "light\n")
        os.chmod(self.t["keybinds"], 0o600)
        self.originals = {key: self.t[key].read_text() for key in ("keybinds", "startup", "switcher")}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def install(self):
        return setup.apply(setup.plan(self.home), self.home, stamp="T")

    def test_dry_plan_touches_nothing(self):
        changes = setup.plan(self.home)
        self.assertIn(self.t["settings"], {change.path for change in changes})
        self.assertFalse(self.t["link"].exists())
        self.assertFalse(self.t["config"].exists())

    def test_install_then_plan_again_is_empty(self):
        self.install()
        self.assertEqual(setup.plan(self.home), [])

    def test_install_wires_everything(self):
        self.install()
        settings = json.loads(self.t["settings"].read_text())
        command = setup.hook_command()
        self.assertEqual(settings["model"], "opus")
        self.assertIn(FOREIGN, settings["hooks"]["Stop"][0]["hooks"])
        self.assertEqual(settings["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 3600)
        self.assertTrue(settings["hooks"]["PreToolUse"][0]["hooks"][0]["async"])
        self.assertEqual(settings["hooks"]["Stop"][1]["hooks"][0]["command"], command)
        self.assertEqual(os.readlink(self.t["link"]), str(setup.BIN))
        self.assertIn(f"{self.t['link']} allow", self.t["keybinds"].read_text())
        self.assertIn(f"bind = $mainMod ALT, D, exec, {self.t['link']} dnd", self.t["keybinds"].read_text())
        self.assertEqual(os.stat(self.t["keybinds"]).st_mode & 0o777, 0o600)
        self.assertIn(setup.SWITCH_LINE, self.t["switcher"].read_text().splitlines())
        colors = (self.t["config"] / "colors.css").read_text()
        self.assertEqual(colors, (setup.THEMES / "colors-gruvbox-light.css").read_text())
        self.assertTrue(os.access(self.t["switcher_script"], os.X_OK))

    def test_backups_keep_the_original_files(self):
        self.install()
        backup = self.t["keybinds"].with_name("UserKeybinds.conf.maneki-T.bak")
        self.assertEqual(backup.read_text(), self.originals["keybinds"])

    def test_remove_restores_the_edited_files(self):
        self.install()
        setup.apply(setup.plan(self.home, remove=True), self.home, stamp="U")
        for key, text in self.originals.items():
            self.assertEqual(self.t[key].read_text(), text, key)
        settings = json.loads(self.t["settings"].read_text())
        self.assertEqual(settings, SETTINGS)
        self.assertFalse(self.t["link"].is_symlink())
        self.assertEqual(setup.plan(self.home, remove=True), [])

    def test_refuses_a_foreign_link(self):
        self.t["link"].parent.mkdir(parents=True)
        os.symlink("/usr/bin/true", self.t["link"])
        with self.assertRaises(setup.SetupError):
            setup.plan(self.home)

    def test_refuses_an_ambiguous_anchor(self):
        self.write(self.t["switcher"], SWITCHER + setup.SWITCH_ANCHOR + "\n")
        with self.assertRaises(setup.SetupError):
            setup.plan(self.home)

    def test_refuses_files_changed_after_planning(self):
        changes = setup.plan(self.home)
        self.write(self.t["startup"], "exec-once = something-else\n")
        with self.assertRaises(setup.SetupError):
            setup.apply(changes, self.home)
        self.assertFalse(self.t["link"].exists())

    def test_existing_theme_files_are_kept(self):
        self.write(self.t["config"] / "style.css", "my own style")
        self.install()
        self.assertEqual((self.t["config"] / "style.css").read_text(), "my own style")

    def test_missing_settings_file_is_created(self):
        self.t["settings"].unlink()
        self.install()
        self.assertIn("hooks", json.loads(self.t["settings"].read_text()))


    def test_style_overrides_start_empty_and_subagent_events_are_hooked(self):
        self.install()
        self.assertEqual((self.t["config"] / "style.css").read_text(), "")
        hooks = json.loads(self.t["settings"].read_text())["hooks"]
        self.assertIn("SubagentStart", hooks)
        self.assertTrue(hooks["SubagentStop"][0]["hooks"][0]["async"])


class MergeHooksTest(unittest.TestCase):
    def test_unchanged_semantics_keep_the_original_text(self):
        text = '{"model":"opus"}'
        self.assertEqual(setup.merge_hooks(text, "x hook", remove=True), text)

    def test_merging_twice_does_not_duplicate(self):
        once = setup.merge_hooks("{}", "x hook")
        self.assertEqual(setup.merge_hooks(once, "x hook"), once)


class AgentSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for directory in (".codex", ".copilot", ".config/opencode"):
            (self.home / directory).mkdir(parents=True)
        self.t = setup.targets(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_and_remove_every_agent(self):
        setup.apply(setup.plan(self.home), self.home, stamp="T")
        codex = json.loads(self.t["codex"].read_text())["hooks"]
        self.assertEqual(codex["PermissionRequest"][0]["hooks"][0]["command"], setup.hook_command(agent="codex"))
        self.assertTrue(codex["PreToolUse"][0]["hooks"][0]["async"])
        self.assertEqual(codex["Interrupt"][0]["hooks"][0]["timeout"], 3)
        copilot = json.loads(self.t["copilot"].read_text())
        command = copilot["hooks"]["notification"][0]["bash"]
        self.assertTrue(command.endswith("--agent copilot --event Notification || true"))
        permission = copilot["hooks"]["PermissionRequest"][0]
        self.assertEqual(permission["timeoutSec"], 90)
        self.assertTrue(permission["bash"].endswith("--event PermissionRequest || true"))
        plugin = self.t["opencode"].read_text()
        self.assertIn(f'const MANEKI = "{setup.BIN}"', plugin)
        self.assertNotIn("__MANEKI_BIN__", plugin)
        self.assertNotIn("node:net", plugin)
        self.assertIn('id: "maneki"', plugin)
        self.assertIn("export default", plugin)
        self.assertEqual(setup.plan(self.home), [])
        setup.apply(setup.plan(self.home, remove=True), self.home, stamp="U")
        for key in ("codex", "copilot", "opencode"):
            self.assertFalse(self.t[key].exists(), key)

    def test_edited_managed_files_are_not_removed(self):
        setup.apply(setup.plan(self.home), self.home, stamp="T")
        self.t["opencode"].write_text("my own plugin")
        setup.apply(setup.plan(self.home, remove=True), self.home, stamp="U")
        self.assertEqual(self.t["opencode"].read_text(), "my own plugin")

    def test_existing_codex_hooks_are_kept(self):
        mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-me"}]}]}}
        self.t["codex"].write_text(json.dumps(mine))
        setup.apply(setup.plan(self.home), self.home, stamp="T")
        setup.apply(setup.plan(self.home, remove=True), self.home, stamp="U")
        self.assertEqual(json.loads(self.t["codex"].read_text()), mine)


if __name__ == "__main__":
    unittest.main()
