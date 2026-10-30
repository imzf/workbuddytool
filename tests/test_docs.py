"""帮助文档与免责声明：README、命令行 --help、安装脚本都要有。"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from wb_launcher import cli

ROOT = Path(__file__).resolve().parent.parent


class ReadmeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_has_disclaimer_section(self) -> None:
        self.assertIn("## 免责声明", self.text)
        self.assertIn("AS IS", self.text)

    def test_has_early_warning(self) -> None:
        # 开头就要能看到，不能只藏在文末
        head = self.text[:2000]
        self.assertIn("免责声明", head)

    def test_names_the_risk(self) -> None:
        self.assertIn("封禁", self.text)
        self.assertIn("腾讯", self.text)

    def test_points_at_license(self) -> None:
        self.assertIn("[`LICENSE`](LICENSE)", self.text)


class CliHelpTest(unittest.TestCase):
    def test_help_mentions_disclaimer(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["--help"])
        output = buffer.getvalue()
        self.assertIn("免责声明", output)
        self.assertIn("风险", output)


class InstallScriptTest(unittest.TestCase):
    def test_install_output_mentions_disclaimer(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("免责声明", text)


if __name__ == "__main__":
    unittest.main()
