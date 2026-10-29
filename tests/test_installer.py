"""安装器的纯本地部分：解压安全检查、systemd 单元生成、桌面项生成。"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from wb_launcher import installer, payload


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


class ExtractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extracts_only_the_binary(self) -> None:
        archive = make_zip(
            self.dir / "pkg.zip",
            {"wb2api": b"\x7fELF-fake", "README.md": b"docs", "config.example.json": b"{}"},
        )
        binary = installer._extract_binary(archive)
        self.assertEqual(binary.name.startswith(".wb2api-extract-"), True)
        self.assertEqual(binary.read_bytes(), b"\x7fELF-fake")
        self.assertTrue(_is_executable(binary))

    def test_extracts_into_requested_directory(self) -> None:
        archive = make_zip(self.dir / "pkg.zip", {"wb2api": b"x"})
        target = self.dir / "install"
        binary = installer._extract_binary(archive, target)
        self.assertEqual(binary.parent, target)
        self.assertEqual(binary.read_bytes(), b"x")

    def test_stale_extracts_are_removed(self) -> None:
        stale = self.dir / ".wb2api-extract-999"
        stale.write_bytes(b"junk")
        keep = self.dir / "wb2api"
        keep.write_bytes(b"real")
        installer._cleanup_stale_extracts(self.dir)
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists())

    def test_missing_binary_is_rejected(self) -> None:
        archive = make_zip(self.dir / "pkg.zip", {"README.md": b"docs"})
        with self.assertRaises(installer.InstallError) as ctx:
            installer._extract_binary(archive)
        self.assertIn("wb2api", str(ctx.exception))

    def test_path_traversal_is_rejected(self) -> None:
        archive = make_zip(self.dir / "evil.zip", {"wb2api": b"x", "../evil.sh": b"boom"})
        with self.assertRaises(installer.InstallError) as ctx:
            installer._extract_binary(archive)
        self.assertIn("路径", str(ctx.exception))

    def test_nested_directory_is_rejected(self) -> None:
        archive = make_zip(self.dir / "nested.zip", {"sub/wb2api": b"x"})
        with self.assertRaises(installer.InstallError):
            installer._extract_binary(archive)

    def test_broken_zip_is_rejected(self) -> None:
        broken = self.dir / "broken.zip"
        broken.write_bytes(b"not a zip")
        with self.assertRaises(installer.InstallError) as ctx:
            installer._extract_binary(broken)
        self.assertIn("zip", str(ctx.exception))

    def test_replace_binary_keeps_previous_copy(self) -> None:
        target = self.dir / "app" / "wb2api"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        source = self.dir / "new"
        source.write_bytes(b"new")
        installer._replace_binary(source, target)
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(target.with_suffix(".old").read_bytes(), b"old")
        self.assertTrue(_is_executable(target))


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)


class UnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def render(self, app_dir: Path) -> tuple[str, Path]:
        paths = payload.PanelPaths(app_dir)
        unit = self.dir / "workbuddy2api.service"
        with mock.patch.object(installer, "unit_path", return_value=unit):
            installer._write_unit(paths)
        return unit.read_text(encoding="utf-8"), unit

    def test_placeholders_are_replaced(self) -> None:
        app_dir = self.dir / "app dir"
        content, _ = self.render(app_dir)
        self.assertNotIn("@", content)
        # WorkingDirectory 不解析引号，路径原样写入（含空格也能用）
        self.assertIn(f"WorkingDirectory={app_dir}", content)
        self.assertIn('-config "/', content)
        self.assertIn('ReadWritePaths="', content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("WantedBy=default.target", content)

    def test_working_directory_escapes_specifier(self) -> None:
        content, _ = self.render(self.dir / "100%")
        self.assertIn(f"WorkingDirectory={self.dir}/100%%", content)

    def test_exec_and_rw_paths_are_quoted(self) -> None:
        content, _ = self.render(self.dir / "app dir")
        for line in content.splitlines():
            if line.startswith(("ExecStart=", "ReadWritePaths=")):
                self.assertIn('"', line, line)

    def test_unit_quotes_escaped(self) -> None:
        self.assertEqual(installer._systemd_quote(Path("/tmp/a b")), '"/tmp/a b"')
        self.assertEqual(installer._systemd_quote(Path('/tmp/a"b')), '"/tmp/a\\"b"')

    @unittest.skipUnless(shutil.which("systemd-analyze"), "需要 systemd-analyze")
    def test_systemd_analyze_accepts_unit(self) -> None:
        app_dir = self.dir / "app"
        app_dir.mkdir()
        (app_dir / "wb2api").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (app_dir / "wb2api").chmod(0o755)
        _, unit = self.render(app_dir)
        proc = subprocess.run(
            ["systemd-analyze", "--user", "verify", str(unit)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class DesktopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_desktop_entry_points_at_launcher(self) -> None:
        target = self.dir / "workbuddy2api-launcher.desktop"
        reporter = installer.Reporter()
        with (
            mock.patch.object(installer, "desktop_path", return_value=target),
            mock.patch.object(installer, "icon_path", return_value=self.dir / "icons" / "x.svg"),
            mock.patch.object(installer, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
        ):
            installer._install_desktop("/home/u/.local/bin/workbuddy-launcher", reporter)
        text = target.read_text(encoding="utf-8")
        self.assertIn("Exec=/home/u/.local/bin/workbuddy-launcher", text)
        self.assertIn("Name=WorkBuddy2API 面板", text)
        self.assertIn("Icon=", text)
        self.assertTrue(text.startswith("[Desktop Entry]"))
        self.assertFalse(text.endswith("\n\n"))


class GuessLauncherTest(unittest.TestCase):
    def test_desktop_quote_handles_spaces(self) -> None:
        self.assertEqual(installer._desktop_quote("/usr/bin/x"), "/usr/bin/x")
        self.assertEqual(installer._desktop_quote("/home/a b/x"), '"/home/a b/x"')

    def test_extension_uuid_matches_metadata(self) -> None:
        import json

        meta_path = installer.resource("gnome-extension", installer.EXTENSION_UUID, "metadata.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["uuid"], installer.EXTENSION_UUID)
        self.assertIn("shell-version", meta)


if __name__ == "__main__":
    unittest.main()
