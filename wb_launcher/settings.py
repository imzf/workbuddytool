"""启动器自己的设置（与面板的 config.json 分开存）。

面板的监听地址、api_key 都在 payload 的 config.json 里，是唯一事实来源；
这里只存「面板装在哪、用什么加速源」这类启动器偏好，避免两份配置打架。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import DEFAULT_MIRROR
from .util import atomic_write_text, read_text


def _xdg(env: str, fallback: str) -> Path:
    value = os.environ.get(env, "").strip()
    return Path(value) if value else Path.home() / fallback


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def default_app_dir() -> Path:
    return data_home() / "workbuddy2api-panel"


def launcher_config_path() -> Path:
    return config_home() / "workbuddy2api-launcher" / "config.json"


@dataclass
class LauncherConfig:
    """启动器偏好。路径都用字符串，方便 JSON 往返。"""

    app_dir: str = field(default_factory=lambda: str(default_app_dir()))
    mirror: str = DEFAULT_MIRROR
    # 上次安装/更新到的版本（上游 tag，含 v 前缀），仅用于界面展示与更新比较
    version: str = ""
    # 首次安装时用户选择的下载源：auto / official / mirror
    source: str = "auto"
    extension_installed: bool = False
    window_width: int = 720
    window_height: int = 820

    @property
    def app_path(self) -> Path:
        return Path(self.app_dir).expanduser()

    def save(self) -> None:
        atomic_write_text(launcher_config_path(), json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n")

    @classmethod
    def load(cls) -> "LauncherConfig":
        raw = read_text(launcher_config_path(), limit=64 * 1024)
        cfg = cls()
        if not raw:
            return cfg
        try:
            data: Any = json.loads(raw)
        except (ValueError, TypeError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        for key, value in data.items():
            if value is None or not hasattr(cfg, key):
                continue
            # 只接受与默认值同类型的值，手改坏了的配置不至于把界面撑崩
            if isinstance(value, type(getattr(cfg, key))):
                setattr(cfg, key, value)
        return cfg
