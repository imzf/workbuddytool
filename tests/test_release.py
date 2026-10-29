"""上游 Release 解析、架构选择、加速地址与下载校验。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from wb_launcher import release


def make_release(*names: str) -> release.Release:
    assets = [
        release.Asset(
            name=name,
            url=f"https://github.com/misakano7545/workbuddy2api-panel/releases/download/v1/{name}",
            size=1024,
            digest="sha256:" + "a" * 64,
        )
        for name in names
    ]
    return release.Release(tag="v2026.10.29.1418", version="2026.10.29.1418", notes="", page_url="", assets=assets)


class PickAssetTest(unittest.TestCase):
    def test_picks_matching_arch(self) -> None:
        r = make_release(
            "wb2api-panel-1-linux-amd64.zip",
            "wb2api-panel-1-linux-arm64.zip",
            "wb2api-panel-1-windows-amd64.zip",
        )
        self.assertEqual(release.pick_asset(r, "arm64").name, "wb2api-panel-1-linux-arm64.zip")
        self.assertEqual(release.pick_asset(r, "amd64").name, "wb2api-panel-1-linux-amd64.zip")

    def test_rejects_windows_and_darwin_assets(self) -> None:
        r = make_release("wb2api-panel-1-darwin-amd64.zip", "wb2api-panel-1-windows-amd64.zip")
        with self.assertRaises(release.ReleaseError):
            release.pick_asset(r, "amd64")

    def test_missing_arch(self) -> None:
        r = make_release("wb2api-panel-1-linux-amd64.zip")
        with self.assertRaises(release.ReleaseError) as ctx:
            release.pick_asset(r, "arm64")
        self.assertIn("arm64", str(ctx.exception))

    def test_host_arch(self) -> None:
        self.assertIn(release.host_arch(), ("amd64", "arm64"))


class DigestTest(unittest.TestCase):
    def test_sha256_prefix_stripped(self) -> None:
        asset = release.Asset(name="a.zip", url="", digest="sha256:ABCDEF")
        self.assertEqual(asset.sha256, "abcdef")

    def test_empty_digest(self) -> None:
        self.assertEqual(release.Asset(name="a.zip", url="").sha256, "")


class MirrorTest(unittest.TestCase):
    def test_prefix_replacement(self) -> None:
        url = "https://github.com/misakano7545/workbuddy2api-panel/releases/download/v1/a.zip"
        self.assertEqual(
            release.mirror_url(url, "https://api.gitproxy.dev/"),
            "https://api.gitproxy.dev/github.com/misakano7545/workbuddy2api-panel/releases/download/v1/a.zip",
        )

    def test_custom_prefix_without_trailing_slash(self) -> None:
        self.assertEqual(release.mirror_url("https://github.com/a/b", "https://m.example"), "https://m.example/github.com/a/b")

    def test_non_https_is_ignored(self) -> None:
        self.assertIsNone(release.mirror_url("http://github.com/a/b"))
        self.assertIsNone(release.mirror_url("https://github.com/a/b", ""))


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.source = self.dir / "payload.zip"
        with zipfile.ZipFile(self.source, "w") as zf:
            zf.writestr("wb2api", "binary")
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_download_matches_digest(self) -> None:
        dest = self.dir / "out.zip"
        release.download([self.source.as_uri()], dest, self.sha)
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), self.sha)

    def test_digest_mismatch_removes_partial_file(self) -> None:
        dest = self.dir / "out.zip"
        with self.assertRaises(release.ReleaseError):
            release.download([self.source.as_uri()], dest, "0" * 64)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix(".zip.part").exists())

    def test_falls_back_to_second_url(self) -> None:
        dest = self.dir / "out.zip"
        bad = (self.dir / "missing.zip").as_uri()
        release.download([bad, self.source.as_uri()], dest, self.sha)
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), self.sha)

    def test_all_urls_fail(self) -> None:
        dest = self.dir / "out.zip"
        with self.assertRaises(release.ReleaseError) as ctx:
            release.download([(self.dir / "nope.zip").as_uri()], dest, "")
        self.assertIn("下载失败", str(ctx.exception))

    def test_progress_reports_total(self) -> None:
        seen: list[tuple[int, int]] = []
        dest = self.dir / "out.zip"
        release.download([self.source.as_uri()], dest, self.sha, lambda done, total: seen.append((done, total)))
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1])
        self.assertGreater(seen[-1][1], 0)


if __name__ == "__main__":
    unittest.main()
