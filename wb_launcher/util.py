"""零依赖小工具：子进程、HTTP、原子写文件、异步回调。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "workbuddy2api-launcher/1.0 (+https://github.com/misakano7545/workbuddy2api-panel)"


def run(
    cmd: list[str],
    timeout: float = 30,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """执行命令并收集输出；超时/找不到命令也返回 CompletedProcess。"""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = (exc.stderr if isinstance(exc.stderr, str) else "") or f"命令超时（{timeout}s）"
        return subprocess.CompletedProcess(cmd, 124, out, err)


def http_json(url: str, timeout: float = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_probe(url: str, timeout: float = 3) -> tuple[bool, int, str]:
    """GET 一个地址，返回 (是否拿到 HTTP 响应, 状态码, 响应体前 2KB)。

    面板的 /healthz 在「一个账号都没有」时也会返回 503，但进程其实活着，
    所以这里把「连上了」和「200」分开，不能只看 2xx。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", "replace")
            return True, int(resp.status), body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2048).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 读错误页失败不影响判断
            body = ""
        finally:
            exc.close()
        return True, int(exc.code), body
    except Exception as exc:  # noqa: BLE001 - 网络层异常类型很多
        return False, 0, str(exc)


def http_ok(url: str, timeout: float = 3) -> tuple[bool, str]:
    """GET 一个地址，返回 (是否 2xx, 说明)。"""
    reachable, status, body = http_probe(url, timeout=timeout)
    if not reachable:
        return False, body
    return 200 <= status < 300, body or f"HTTP {status}"


def atomic_write_text(path: str | os.PathLike[str], text: str, mode: int = 0o644) -> None:
    """同目录临时文件 + os.replace，避免写一半留下坏文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_text(path: str | os.PathLike[str], limit: int = 0) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if limit > 0:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
            return handle.read()
    except OSError:
        return ""


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def is_command_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_async(
    work: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> threading.Thread:
    """把阻塞工作丢到线程里，回调交给调用方（GTK 里应再套 GLib.idle_add）。"""

    def _target() -> None:
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001 - 后台任务统一交给 UI 处理
            if on_error is not None:
                on_error(exc)
            return
        if on_done is not None:
            on_done(result)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread
