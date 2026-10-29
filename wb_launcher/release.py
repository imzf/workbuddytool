"""上游发布包：查询最新 Release、挑选本机架构的 zip、校验摘要并下载。

供应方（misakano7545/workbuddy2api-panel）的每个 Release 里：
  - `wb2api-panel-<版本>-linux-amd64.zip`
  - `wb2api-panel-<版本>-linux-arm64.zip`
GitHub API 会给每个资产一个 `digest`（sha256），下载后必须核对。
"""

from __future__ import annotations

import hashlib
import os
import platform
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import DEFAULT_MIRROR, UPSTREAM_REPO
from .util import USER_AGENT, http_json, human_size

API_LATEST = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"


class ReleaseError(RuntimeError):
    pass


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    digest: str = ""

    @property
    def sha256(self) -> str:
        value = self.digest.strip().lower()
        if value.startswith("sha256:"):
            return value.split(":", 1)[1]
        return value


@dataclass
class Release:
    tag: str
    version: str
    notes: str
    page_url: str
    assets: list[Asset] = field(default_factory=list)


def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise ReleaseError(f"暂不支持的 CPU 架构：{machine}（上游只发 amd64 / arm64）")


def fetch_latest(timeout: float = 25) -> Release:
    try:
        data = http_json(API_LATEST, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise ReleaseError("GitHub API 返回 403（多半是请求频率超限），请稍后再试或手动选择安装包") from exc
        raise ReleaseError(f"查询最新版本失败：HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - 网络错误统一转成可读信息
        raise ReleaseError(f"查询最新版本失败：{exc}") from exc

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise ReleaseError("Release 数据里没有 tag_name")
    assets = [
        Asset(
            name=str(item.get("name") or ""),
            url=str(item.get("browser_download_url") or ""),
            size=int(item.get("size") or 0),
            digest=str(item.get("digest") or ""),
        )
        for item in data.get("assets") or []
        if isinstance(item, dict)
    ]
    return Release(
        tag=tag,
        version=tag.lstrip("vV"),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or ""),
        assets=assets,
    )


def pick_asset(release: Release, arch: str | None = None) -> Asset:
    arch = arch or host_arch()
    suffix = f"-linux-{arch}.zip"
    for asset in release.assets:
        if asset.name.endswith(suffix):
            return asset
    raise ReleaseError(f"该版本没有 {arch} 的 Linux 安装包（{release.tag}）")


def mirror_url(url: str, prefix: str = DEFAULT_MIRROR) -> str | None:
    """把 `https://github.com/...` 换成 `https://api.gitproxy.dev/github.com/...`。"""
    if not prefix or not url.startswith("https://"):
        return None
    return prefix.rstrip("/") + "/" + url[len("https://"):]


def download(
    urls: Iterable[str],
    dest: Path,
    expected_sha256: str = "",
    on_progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 30,
) -> None:
    """按顺序尝试每个地址；成功写完并校验后才落到 dest。"""
    errors: list[str] = []
    for url in [u for u in urls if u]:
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            _download_one(url, part, on_progress, cancel, timeout)
            if expected_sha256:
                actual = sha256_of(part)
                if actual.lower() != expected_sha256.lower():
                    raise ReleaseError(
                        f"安装包摘要不一致：期望 {expected_sha256[:16]}…，实际 {actual[:16]}…"
                    )
            os.replace(part, dest)
            return
        except DownloadCancelled:
            _quiet_unlink(part)
            raise
        except Exception as exc:  # noqa: BLE001 - 换下一个地址
            _quiet_unlink(part)
            errors.append(f"{url} → {exc}")
    raise ReleaseError("下载失败：\n" + "\n".join(errors or ["没有可用地址"]))


def _download_one(
    url: str,
    dest: Path,
    on_progress: Callable[[int, int], None] | None,
    cancel: Callable[[], bool] | None,
    timeout: float,
) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as handle:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel is not None and cancel():
                raise DownloadCancelled("已取消")
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)
        if total and done != total:
            raise ReleaseError(f"下载不完整：{human_size(done)} / {human_size(total)}")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
