"""把面板二进制交给 systemd 用户服务托管，并读取状态 / 日志。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import UNIT_NAME
from .util import http_probe, run


def systemctl(*args: str, timeout: float = 25) -> tuple[int, str, str]:
    proc = run(["systemctl", "--user", *args], timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def systemd_available() -> tuple[bool, str]:
    rc, _, err = systemctl("show-environment", timeout=10)
    return rc == 0, err


def unit_state() -> str:
    """active / inactive / failed / activating / unknown。"""
    rc, out, _ = systemctl("show", "-p", "ActiveState", "--value", UNIT_NAME, timeout=10)
    if rc != 0 or not out:
        return "unknown"
    return out.strip() or "unknown"


def is_enabled() -> bool:
    rc, out, _ = systemctl("is-enabled", UNIT_NAME, timeout=10)
    return rc == 0 and out.strip() in ("enabled", "enabled-runtime", "static", "indirect")


def enable() -> tuple[bool, str]:
    rc, out, err = systemctl("enable", UNIT_NAME)
    return rc == 0, err or out


def disable() -> tuple[bool, str]:
    rc, out, err = systemctl("disable", UNIT_NAME)
    return rc == 0, err or out


def daemon_reload() -> tuple[bool, str]:
    rc, out, err = systemctl("daemon-reload")
    return rc == 0, err or out


def reset_failed() -> None:
    """清掉 systemd 的失败/限流状态，否则连续重启后会卡在 start-limit-hit。"""
    systemctl("reset-failed", UNIT_NAME, timeout=10)


def start() -> tuple[bool, str]:
    reset_failed()
    rc, out, err = systemctl("start", UNIT_NAME, timeout=45)
    return rc == 0, err or out or "已启动"


def stop() -> tuple[bool, str]:
    rc, out, err = systemctl("stop", UNIT_NAME, timeout=45)
    return rc == 0, err or out or "已停止"


def restart() -> tuple[bool, str]:
    reset_failed()
    rc, out, err = systemctl("restart", UNIT_NAME, timeout=60)
    return rc == 0, err or out or "已重启"


def journal(lines: int = 300) -> str:
    proc = run(
        [
            "journalctl",
            "--user",
            "-u",
            UNIT_NAME,
            "-n",
            str(max(1, min(5000, lines))),
            "--no-pager",
            "-o",
            "short-iso",
        ],
        timeout=20,
    )
    if proc.returncode != 0:
        return (proc.stderr or "").strip() or "读取日志失败（journalctl 不可用）"
    return (proc.stdout or "").strip() or "（暂无日志）"


@dataclass
class ServiceStatus:
    state: str
    reachable: bool = False
    accounts: int = -1  # /healthz 的 total；-1 表示未知（没连上或响应看不懂）
    healthy_accounts: int = -1
    detail: str = ""

    @property
    def running(self) -> bool:
        return self.state == "active"

    @property
    def healthy(self) -> bool:
        return self.reachable and self.healthy_accounts != 0

    @property
    def label(self) -> str:
        if self.state in ("activating", "reloading"):
            return "启动中"
        if self.state == "active" and not self.reachable:
            return "运行中（端口未响应）"
        if self.state == "active" and self.accounts == 0:
            return "运行中（还没有账号）"
        if self.state == "active":
            return "运行中"
        if self.state == "failed":
            return "启动失败"
        return "已停止"


def health_url(host: str, port: int) -> str:
    check_host = host
    if check_host in ("0.0.0.0", "::", ""):
        check_host = "127.0.0.1"
    if ":" in check_host:
        check_host = f"[{check_host}]"
    return f"http://{check_host}:{port}/healthz"


def status(host: str, port: int) -> ServiceStatus:
    """服务状态 = systemd 状态 + /healthz。

    注意：上游 /healthz 在一个可用账号都没有时返回 503，但进程是正常的，
    所以「连上了」和「HTTP 200」要分开判断。
    """
    state = unit_state()
    reachable, code, body = http_probe(health_url(host, port), timeout=3)
    accounts = healthy_accounts = -1
    detail = body if not reachable else ""
    if reachable:
        try:
            data = json.loads(body)
            accounts = int(data.get("total", -1))
            healthy_accounts = int(data.get("healthy", -1))
            detail = f"HTTP {code} · 账号 {healthy_accounts}/{accounts}"
        except (ValueError, TypeError, AttributeError):
            detail = f"HTTP {code}"
    return ServiceStatus(
        state=state,
        reachable=reachable,
        accounts=accounts,
        healthy_accounts=healthy_accounts,
        detail=detail,
    )


def open_in_browser(url: str) -> tuple[bool, str]:
    proc = run(["xdg-open", url], timeout=10)
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()
