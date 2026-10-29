"""面板 config.json 的读写：监听地址、API 密钥、版本号。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from wb_launcher import payload


class ListenTest(unittest.TestCase):
    def test_ipv4_roundtrip(self) -> None:
        self.assertEqual(payload.format_listen("127.0.0.1", 7863), "127.0.0.1:7863")
        self.assertEqual(payload.parse_listen("127.0.0.1:7863"), ("127.0.0.1", 7863))

    def test_ipv6_roundtrip(self) -> None:
        self.assertEqual(payload.format_listen("::1", 7863), "[::1]:7863")
        self.assertEqual(payload.parse_listen("[::1]:7863"), ("::1", 7863))

    def test_listen_all_interfaces(self) -> None:
        self.assertEqual(payload.parse_listen(":9000"), ("127.0.0.1", 9000))

    def test_invalid_value_falls_back(self) -> None:
        self.assertEqual(payload.parse_listen("nonsense"), ("127.0.0.1", 7863))
        self.assertEqual(payload.parse_listen(""), ("127.0.0.1", 7863))

    def test_bracketed_ipv6_with_zone(self) -> None:
        self.assertEqual(payload.format_listen("fe80::1%eth0", 8080), "[fe80::1%eth0]:8080")

    def test_panel_url_rewrites_wildcard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = payload.PanelPaths(Path(tmp))
            payload.write_config(paths, {"listen": "0.0.0.0:7999"})
            self.assertEqual(payload.panel_url(paths), "http://127.0.0.1:7999/panel/")
            payload.set_listen(paths, "::1", 7000)
            self.assertEqual(payload.panel_url(paths), "http://[::1]:7000/panel/")


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = payload.PanelPaths(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_api_key_shape(self) -> None:
        key = payload.new_api_key()
        self.assertTrue(key.startswith("sk-"))
        self.assertGreater(len(key), 20)
        self.assertNotEqual(key, payload.new_api_key())

    def test_ensure_config_creates_minimal_file(self) -> None:
        config = payload.ensure_config(self.paths, "127.0.0.1", 7863)
        self.assertEqual(config["listen"], "127.0.0.1:7863")
        self.assertTrue(str(config["api_key"]).startswith("sk-"))
        self.assertEqual(config["auth_dir"], "./auths")
        self.assertEqual(config["state_file"], "./data/state.json")
        self.assertFalse(config["update"]["auto_enabled"])
        self.assertTrue(self.paths.config.is_file())
        self.assertTrue(self.paths.auths.is_dir())
        self.assertTrue(self.paths.data.is_dir())
        # 权限应当收紧到 0600，密钥不能给同机其他用户读
        self.assertEqual(os.stat(self.paths.config).st_mode & 0o777, 0o600)

    def test_ensure_config_keeps_existing_values(self) -> None:
        payload.write_config(
            self.paths,
            {"listen": "127.0.0.1:1234", "api_key": "sk-keep", "custom": "yes"},
        )
        config = payload.ensure_config(self.paths, "0.0.0.0", 9999)
        self.assertEqual(config["listen"], "127.0.0.1:1234")
        self.assertEqual(config["api_key"], "sk-keep")
        self.assertEqual(config["custom"], "yes")

    def test_set_listen_keeps_other_keys(self) -> None:
        payload.write_config(self.paths, {"listen": "127.0.0.1:7863", "api_key": "sk-x"})
        payload.set_listen(self.paths, "0.0.0.0", 8888)
        data = json.loads(self.paths.config.read_text(encoding="utf-8"))
        self.assertEqual(data["listen"], "0.0.0.0:8888")
        self.assertEqual(data["api_key"], "sk-x")

    def test_version_file(self) -> None:
        self.assertEqual(payload.get_version(self.paths), "")
        payload.set_version(self.paths, "v2026.10.29.1418")
        self.assertEqual(payload.get_version(self.paths), "v2026.10.29.1418")

    def test_installed_flag(self) -> None:
        self.assertFalse(self.paths.installed)
        self.paths.binary.parent.mkdir(parents=True, exist_ok=True)
        self.paths.binary.write_text("", encoding="utf-8")
        self.assertTrue(self.paths.installed)

    def test_broken_json_is_tolerated(self) -> None:
        self.paths.config.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config.write_text("{not json", encoding="utf-8")
        self.assertEqual(payload.read_config(self.paths), {})
        self.assertEqual(payload.get_api_key(self.paths), "")
        self.assertEqual(payload.get_listen(self.paths), ("127.0.0.1", 7863))


if __name__ == "__main__":
    unittest.main()
