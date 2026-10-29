"""启动器自身配置（~/.config/workbuddy2api-launcher/config.json）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wb_launcher import settings


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.home / "config"),
                "XDG_DATA_HOME": str(self.home / "data"),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_paths_follow_xdg(self) -> None:
        self.assertEqual(settings.config_home(), self.home / "config")
        self.assertEqual(settings.data_home(), self.home / "data")
        self.assertEqual(settings.launcher_config_path(), self.home / "config" / "workbuddy2api-launcher" / "config.json")
        self.assertEqual(settings.default_app_dir(), self.home / "data" / "workbuddy2api-panel")

    def test_defaults(self) -> None:
        cfg = settings.LauncherConfig.load()
        self.assertEqual(cfg.app_dir, str(self.home / "data" / "workbuddy2api-panel"))
        self.assertEqual(cfg.source, "auto")
        self.assertTrue(cfg.mirror.startswith("https://"))
        self.assertFalse(cfg.extension_installed)

    def test_save_and_load_roundtrip(self) -> None:
        cfg = settings.LauncherConfig(app_dir="/tmp/panel", version="v1", source="mirror")
        cfg.save()
        again = settings.LauncherConfig.load()
        self.assertEqual(again.app_dir, "/tmp/panel")
        self.assertEqual(again.version, "v1")
        self.assertEqual(again.source, "mirror")

    def test_unknown_keys_and_broken_json_are_ignored(self) -> None:
        path = settings.launcher_config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"app_dir": "/tmp/x", "bogus": 1}), encoding="utf-8")
        cfg = settings.LauncherConfig.load()
        self.assertEqual(cfg.app_dir, "/tmp/x")
        self.assertFalse(hasattr(cfg, "bogus"))

        path.write_text("{oops", encoding="utf-8")
        self.assertEqual(settings.LauncherConfig.load().app_dir, str(settings.default_app_dir()))

    def test_wrong_types_are_ignored(self) -> None:
        path = settings.launcher_config_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"app_dir": 123, "window_width": "wide", "source": "mirror"}),
            encoding="utf-8",
        )
        cfg = settings.LauncherConfig.load()
        self.assertEqual(cfg.app_dir, str(settings.default_app_dir()))
        self.assertEqual(cfg.window_width, 720)
        self.assertEqual(cfg.source, "mirror")

    def test_app_path_expands_user(self) -> None:
        cfg = settings.LauncherConfig(app_dir="~/panel")
        self.assertEqual(cfg.app_path, Path.home() / "panel")


if __name__ == "__main__":
    unittest.main()
