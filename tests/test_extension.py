"""GNOME Shell 扩展的静态检查：元数据、JS 语法、样式类都写到了。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from wb_launcher import installer

EXT_DIR = installer.resource("gnome-extension", installer.EXTENSION_UUID)


class ExtensionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = json.loads((EXT_DIR / "metadata.json").read_text(encoding="utf-8"))
        self.source = (EXT_DIR / "extension.js").read_text(encoding="utf-8")
        self.stylesheet = (EXT_DIR / "stylesheet.css").read_text(encoding="utf-8")

    def test_required_files_exist(self) -> None:
        for name in ("extension.js", "metadata.json", "stylesheet.css"):
            self.assertTrue((EXT_DIR / name).is_file(), name)

    def test_metadata_matches_target(self) -> None:
        self.assertEqual(self.metadata["uuid"], installer.EXTENSION_UUID)
        self.assertEqual(self.metadata["name"], "WorkBuddy2API 面板")
        versions = [int(v) for v in self.metadata["shell-version"]]
        self.assertTrue(versions)
        self.assertEqual(versions, sorted(versions))
        self.assertGreaterEqual(min(versions), 45)

    def test_uses_documented_extension_entrypoints(self) -> None:
        self.assertIn("export default class", self.source)
        self.assertIn("enable()", self.source)
        self.assertIn("disable()", self.source)

    def test_unit_name_matches_systemd_template(self) -> None:
        self.assertIn("workbuddy2api.service", self.source)
        self.assertIn("workbuddy2api.service", installer.unit_path().name)

    def test_style_classes_defined(self) -> None:
        for name in ("workbuddy-ok", "workbuddy-off", "workbuddy-err"):
            self.assertIn(f".{name}", self.stylesheet)
            self.assertIn(name, self.source)

    def test_uses_accessible_name_instead_of_tooltips(self) -> None:
        # St.Widget 没有 set_tooltip_text；桌面指示器的悬浮提示只能靠无障碍名称
        self.assertNotIn("set_tooltip_text", self.source)
        self.assertIn("set_accessible_name", self.source)

    def test_no_legacy_imports(self) -> None:
        # GNOME 45 之后扩展必须用 ESM import，旧的 imports.* / ExtensionUtils 已移除
        self.assertNotIn("imports.gi", self.source)
        self.assertNotIn("ExtensionUtils", self.source)
        for module in re.findall(r"from 'gi://([^']+)'", self.source):
            self.assertIn(module.split("?")[0], {"GObject", "GLib", "Gio", "St", "Soup"})

    @unittest.skipUnless(shutil.which("node"), "需要 node 才做语法检查")
    def test_javascript_syntax(self) -> None:
        node = shutil.which("node")
        proc = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=self.source,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(shutil.which("node"), "需要 node")
    def test_json_metadata_parses_in_js(self) -> None:
        node = shutil.which("node")
        proc = subprocess.run(
            [node, "-e", "JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'))", str(EXT_DIR / "metadata.json")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
