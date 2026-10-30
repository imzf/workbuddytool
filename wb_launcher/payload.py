"""面板安装目录（payload）的读写：config.json、版本、监听地址、API 密钥。"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import DEFAULT_HOST, DEFAULT_PORT
from .util import atomic_write_text, read_text


@dataclass(frozen=True)
class PanelPaths:
    root: Path

    @property
    def binary(self) -> Path:
        return self.root / "wb2api"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def version_file(self) -> Path:
        return self.root / ".version"

    @property
    def auths(self) -> Path:
        return self.root / "auths"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def installed(self) -> bool:
        return self.binary.is_file()

    def ensure_dirs(self) -> None:
        # 账号凭证就在 auths/ 里，目录只给本人可进
        for directory in (self.root, self.auths, self.data):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)


def new_api_key() -> str:
    # 上游面板用 `sk-` 前缀区分密钥；长度与自动生成的推荐值一致（约 32 字符）
    return "sk-" + secrets.token_urlsafe(24)


def default_config(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict[str, Any]:
    """只写必要字段；其余键由面板按内置默认值补齐（与首启自动生成同口径）。"""
    return {
        "listen": format_listen(host, port),
        "api_key": new_api_key(),
        "auth_dir": "./auths",
        "state_file": "./data/state.json",
        "update": {"auto_enabled": False},
    }


def format_listen(host: str, port: int) -> str:
    host = (host or DEFAULT_HOST).strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if ":" in host:  # IPv6 字面量
        return f"[{host}]:{int(port)}"
    return f"{host}:{int(port)}"


def parse_listen(value: str) -> tuple[str, int]:
    """把 `127.0.0.1:7863` / `[::1]:7863` / `:7863` 拆成 (host, port)。"""
    text = (value or "").strip()
    match = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", text)
    if match:
        return match.group(1), int(match.group(2))
    host, _, port_text = text.rpartition(":")
    if not port_text.isdigit():
        return DEFAULT_HOST, DEFAULT_PORT
    return (host or DEFAULT_HOST), int(port_text)


def read_config(paths: PanelPaths) -> dict[str, Any]:
    raw = read_text(paths.config, limit=1024 * 1024)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config(paths: PanelPaths, data: dict[str, Any]) -> None:
    atomic_write_text(paths.config, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=0o600)


def ensure_config(paths: PanelPaths, host: str, port: int) -> dict[str, Any]:
    """已有配置就原样保留（覆盖安装不重置密钥与账号），没有才生成。"""
    paths.ensure_dirs()
    config = read_config(paths)
    if config:
        return config
    config = default_config(host, port)
    write_config(paths, config)
    return config


def get_listen(paths: PanelPaths) -> tuple[str, int]:
    return parse_listen(str(read_config(paths).get("listen", "")))


def set_listen(paths: PanelPaths, host: str, port: int) -> str:
    config = read_config(paths)
    listen = format_listen(host, port)
    config["listen"] = listen
    write_config(paths, config)
    return listen


def get_api_key(paths: PanelPaths) -> str:
    return str(read_config(paths).get("api_key", "") or "")


def get_version(paths: PanelPaths) -> str:
    return read_text(paths.version_file, limit=200).strip()


def set_version(paths: PanelPaths, version: str) -> None:
    atomic_write_text(paths.version_file, (version.strip() + "\n") if version.strip() else "")


def panel_url(paths: PanelPaths, suffix: str = "/panel/") -> str:
    host, port = get_listen(paths)
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if host == "::1":
        return f"http://[::1]:{port}{suffix}"
    return f"http://{host}:{port}{suffix}"
