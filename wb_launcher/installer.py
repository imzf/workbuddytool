"""安装 / 更新面板：下载、校验、落盘、systemd 用户服务、桌面项与 GNOME 扩展。"""

from __future__ import annotations

import errno
import os
import platform
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import (
    DEFAULT_HOST,
    DEFAULT_MIRROR,
    DEFAULT_PORT,
    UNIT_NAME,
    UPSTREAM_URL,
)
from . import payload, release, service
from .settings import config_home, data_home
from .util import http_probe, human_size, is_command_available, run

EXTENSION_UUID = "workbuddy2api-panel@local"
DESKTOP_ID = "workbuddy2api-launcher.desktop"

_DATA_DIR = Path(__file__).with_name("data")


class InstallError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class InstallOptions:
    app_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    source: str = "auto"  # auto（官方优先，失败换加速）| official | mirror
    mirror: str = DEFAULT_MIRROR
    local_zip: Path | None = None
    autostart: bool = True
    with_extension: bool = False
    start_now: bool = True
    launcher_cmd: str = ""


class Reporter:
    """把安装进度交给界面或终端；界面侧把回调再转投 GLib 主线程。"""

    def __init__(
        self,
        on_step: Callable[[str], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.on_step = on_step
        self.on_progress = on_progress
        self.on_log = on_log
        self._cancel = cancel

    def step(self, text: str) -> None:
        if self.on_step:
            self.on_step(text)

    def progress(self, fraction: float, text: str = "") -> None:
        if self.on_progress:
            self.on_progress(max(0.0, min(1.0, fraction)), text)

    def log(self, text: str) -> None:
        if self.on_log:
            self.on_log(text)

    def check_cancel(self) -> None:
        if self._cancel and self._cancel():
            raise Cancelled("已取消")


# ── 路径 ──────────────────────────────────────────────────────────────


def unit_path() -> Path:
    return config_home() / "systemd" / "user" / UNIT_NAME


def desktop_path() -> Path:
    return data_home() / "applications" / DESKTOP_ID


def icon_path() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / "workbuddy2api-panel.svg"


def extension_dir() -> Path:
    return data_home() / "gnome-shell" / "extensions" / EXTENSION_UUID


def resource(*parts: str) -> Path:
    return _DATA_DIR.joinpath(*parts)


# ── 安装 ──────────────────────────────────────────────────────────────


def install(options: InstallOptions, reporter: Reporter | None = None) -> str:
    reporter = reporter or Reporter()
    paths = payload.PanelPaths(Path(options.app_dir).expanduser())

    reporter.check_cancel()
    reporter.step("检查运行环境")
    _check_environment(reporter)
    reporter.progress(0.04, "环境就绪")

    reporter.check_cancel()
    reporter.step("查询版本")
    plan = _plan_install(options, reporter)
    tag = plan.tag
    reporter.progress(0.12, f"准备安装 {tag or '本地安装包'}")

    paths.ensure_dirs()
    _cleanup_stale_extracts(paths.root)
    reporter.log(f"安装目录：{paths.root}")
    if tag:
        reporter.log(f"上游版本：{tag}")

    reporter.check_cancel()
    reporter.step("下载安装包")
    archive = _download_archive(options, plan, reporter)
    reporter.progress(0.55, "下载完成")

    reporter.check_cancel()
    reporter.step("校验并解压")
    if plan.sha256:
        actual = release.sha256_of(archive)
        if actual.lower() != plan.sha256.lower():
            _cleanup_temp(options, archive)
            raise InstallError(f"安装包摘要不一致：期望 {plan.sha256}，实际 {actual}")
        reporter.log(f"SHA-256 校验通过：{actual}")
    binary_tmp = _extract_binary(archive, paths.root)
    _cleanup_temp(options, archive)

    reporter.step("写入配置")
    config = payload.ensure_config(paths, options.host, options.port)
    if config.get("listen"):
        host, port = payload.parse_listen(str(config["listen"]))
        reporter.log(f"保留现有配置：{payload.format_listen(host, port)}")
    if tag:
        payload.set_version(paths, tag)

    reporter.step("安装程序")
    _replace_binary(binary_tmp, paths.binary)
    reporter.log(f"已写入 {paths.binary}（{human_size(paths.binary.stat().st_size)}）")

    reporter.step("配置 systemd 用户服务")
    _write_unit(paths)
    ok, message = service.daemon_reload()
    if not ok:
        raise InstallError(f"systemd 重新加载失败：{message}")
    reporter.log(f"服务单元：{unit_path()}")

    if options.autostart:
        ok, message = service.enable()
        reporter.log("已设为登录时自动启动" if ok else f"设置开机自启失败：{message}")
    else:
        service.disable()

    reporter.step("安装桌面入口")
    _install_desktop(options.launcher_cmd, reporter)

    if options.with_extension:
        reporter.step("安装 GNOME 扩展")
        try:
            message = install_extension()
            reporter.log(message)
        except Exception as exc:  # noqa: BLE001 - 扩展失败不该让面板安装回滚
            reporter.log(f"GNOME 扩展安装失败（可稍后重试）：{exc}")

    reporter.step("启动服务")
    if options.start_now:
        service.stop()
        ok, message = service.start()
        if not ok:
            raise InstallError(f"服务启动失败：{message}\n可执行 journalctl --user -u {UNIT_NAME} -n 80 查看原因")
        reporter.progress(0.9, "等待面板就绪")
        host, port = payload.get_listen(paths)
        if not wait_health(host, port, timeout=25):
            tail = service.journal(40)
            raise InstallError(f"服务已启动但健康检查未通过。\n\n{tail}")
        reporter.progress(1.0, "安装完成")
    else:
        reporter.progress(1.0, "安装完成（服务未启动）")

    reporter.log("安装完成。")
    return tag


def update(options: InstallOptions, reporter: Reporter | None = None) -> str:
    """只替换二进制，保留 config.json / auths / data；失败自动回滚。"""
    reporter = reporter or Reporter()
    paths = payload.PanelPaths(Path(options.app_dir).expanduser())
    if not paths.installed:
        return install(options, reporter)

    reporter.step("查询最新版本")
    plan = _plan_install(options, reporter)
    tag = plan.tag
    current = payload.get_version(paths)
    if tag and current and tag == current and options.local_zip is None:
        reporter.log(f"当前已是最新版本：{tag}")
        return current

    reporter.step("下载安装包")
    archive = _download_archive(options, plan, reporter)
    reporter.step("校验并解压")
    _cleanup_stale_extracts(paths.root)
    if plan.sha256:
        actual = release.sha256_of(archive)
        if actual.lower() != plan.sha256.lower():
            _cleanup_temp(options, archive)
            raise InstallError("安装包摘要不一致，已中止更新")
        reporter.log(f"SHA-256 校验通过：{actual}")
    binary_tmp = _extract_binary(archive, paths.root)
    _cleanup_temp(options, archive)

    backup = paths.binary.with_suffix(".old")
    reporter.step("停止服务")
    service.stop()
    try:
        if paths.binary.exists():
            if backup.exists():
                backup.unlink()
            _move_into_place(paths.binary, backup)
        _move_into_place(binary_tmp, paths.binary)
        os.chmod(paths.binary, 0o755)
        payload.set_version(paths, tag or current)
        reporter.step("启动新版本")
        ok, message = service.start()
        if not ok:
            raise InstallError(f"新版本启动失败：{message}")
        host, port = payload.get_listen(paths)
        if not wait_health(host, port, timeout=25):
            raise InstallError("新版本健康检查未通过")
        # 新版本确认可用后就不再保留旧文件（11MB），回滚窗口到此为止
        try:
            if backup.exists():
                backup.unlink()
        except OSError:
            pass
    except Exception:
        reporter.log("更新失败，正在回滚到上一版本…")
        service.stop()
        if backup.exists():
            if paths.binary.exists():
                paths.binary.unlink()
            _move_into_place(backup, paths.binary)
            service.start()
        raise
    installed = tag or payload.get_version(paths) or "本地安装包"
    reporter.log(f"已更新到 {installed}")
    return installed


# ── 内部步骤 ──────────────────────────────────────────────────────────


def _check_environment(reporter: Reporter) -> None:
    if os.geteuid() == 0:
        reporter.log("提示：当前以 root 运行。systemd 用户服务通常无法在 root 的桌面会话外工作。")
    ok, err = service.systemd_available()
    if not ok:
        raise InstallError(f"systemd 用户会话不可用（systemctl --user 失败）：{err}")
    if not is_command_available("journalctl"):
        reporter.log("提示：未找到 journalctl，启动器内将无法查看服务日志。")
    reporter.log(f"系统：{platform.system()} {platform.release()} / {platform.machine()}")


@dataclass
class _Plan:
    tag: str = ""
    archive: Path | None = None
    asset: release.Asset | None = None
    sha256: str = ""
    version: release.Release | None = field(default=None, repr=False)


def _plan_install(options: InstallOptions, reporter: Reporter) -> _Plan:
    """决定这次装哪个包：本地 zip，或 GitHub 最新 Release 里对应架构的资产。"""
    if options.local_zip:
        local = Path(options.local_zip).expanduser()
        if not local.is_file():
            raise InstallError(f"本地安装包不存在：{local}")
        reporter.log(f"使用本地安装包：{local}")
        return _Plan(archive=local)

    try:
        latest = release.fetch_latest()
    except release.ReleaseError as exc:
        raise InstallError(
            f"{exc}\n\n可以改用「本地安装包」，或先设置系统代理（HTTPS_PROXY）后重试。"
        ) from exc
    asset = release.pick_asset(latest)
    reporter.log(f"最新版本：{latest.tag}（{asset.name}，{human_size(asset.size)}）")
    reporter.log(f"发布页：{latest.page_url or UPSTREAM_URL + '/releases'}")
    return _Plan(tag=latest.tag, asset=asset, sha256=asset.sha256, version=latest)


def _download_archive(options: InstallOptions, plan: _Plan, reporter: Reporter) -> Path:
    if plan.archive and plan.archive.is_file():
        reporter.progress(0.5, "使用本地安装包")
        return plan.archive
    if plan.asset is None:
        raise InstallError("没有可下载的安装包")

    asset = plan.asset
    target_dir = Path(options.app_dir).expanduser() / ".downloads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / asset.name

    mirrored = release.mirror_url(asset.url, options.mirror)

    urls = [asset.url]
    if options.source == "mirror":
        urls = [mirrored or asset.url, asset.url]
    elif options.source == "auto":
        urls = [asset.url, mirrored or ""]
    reporter.log("下载地址：" + "\n           ".join(u for u in urls if u))
    if mirrored and options.source == "auto":
        reporter.log("官方地址失败时会自动改用 GitProxy 加速。")

    def progress(done: int, total: int) -> None:
        reporter.check_cancel()
        total = total or asset.size
        if total:
            reporter.progress(0.12 + 0.43 * done / total, f"{human_size(done)} / {human_size(total)}")

    try:
        release.download(urls, target, plan.sha256, progress, reporter.check_cancel)
    except release.DownloadCancelled as exc:
        raise Cancelled("已取消") from exc
    except release.ReleaseError as exc:
        raise InstallError(str(exc)) from exc
    return target


def _cleanup_temp(options: InstallOptions, archive: Path) -> None:
    try:
        if archive and archive.is_file() and archive.parent.name == ".downloads":
            archive.unlink()
        download_dir = Path(options.app_dir).expanduser() / ".downloads"
        if download_dir.is_dir() and not any(download_dir.iterdir()):
            download_dir.rmdir()
    except OSError:
        pass


def _extract_binary(archive: Path, dest_dir: Path | None = None) -> Path:
    """从 zip 里只取出 wb2api，拒绝路径穿越/符号链接。

    解压目标默认放在 zip 旁边；传入 dest_dir 时直接写到最终目录，
    避免 /tmp 与家目录不在同一个文件系统时 os.replace 报 EXDEV。
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            member = None
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.endswith("/"):
                    continue
                if "/" in name.strip("/") or ".." in name.split("/"):
                    # 上游包是平铺的；出现目录或 .. 说明包被改过，直接拒绝
                    raise InstallError(f"安装包结构异常（包含路径）：{info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InstallError(f"安装包结构异常（包含符号链接）：{info.filename}")
                if name == "wb2api":
                    member = info
            if member is None:
                raise InstallError("安装包里没有找到 wb2api 可执行文件")
            target_dir = Path(dest_dir) if dest_dir is not None else Path(archive).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            tmp = target_dir / f".wb2api-extract-{os.getpid()}"
            with zf.open(member) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(tmp, 0o755)
            return tmp
    except zipfile.BadZipFile as exc:
        raise InstallError(f"安装包不是有效的 zip：{exc}") from exc


def _cleanup_stale_extracts(root: Path) -> None:
    """清掉上次安装中断留下的解压临时文件。"""
    try:
        for stale in root.glob(".wb2api-extract-*"):
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _move_into_place(source: Path, destination: Path) -> None:
    """优先原子 rename；跨文件系统时退回复制 + 删除。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copyfile(source, destination)
        try:
            source.unlink()
        except OSError:
            pass


def _replace_binary(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup = destination.with_suffix(".old")
        try:
            if backup.exists():
                backup.unlink()
            _move_into_place(destination, backup)
        except OSError:
            pass
    _move_into_place(source, destination)
    os.chmod(destination, 0o755)


def _systemd_quote(path: Path) -> str:
    text = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _systemd_path(path: Path) -> str:
    """WorkingDirectory= 不做引号解析，引号会被当成路径的一部分；只转义 % 和换行。"""
    return str(path).replace("%", "%%").replace("\n", "")


def _write_unit(paths: payload.PanelPaths) -> None:
    template = resource("workbuddy2api.service.in").read_text(encoding="utf-8")
    content = (
        template.replace("@APP_DIR@", _systemd_quote(paths.root))
        .replace("@EXEC_START@", f"{_systemd_quote(paths.binary)} -config {_systemd_quote(paths.config)}")
        .replace("@WORK_DIR@", _systemd_path(paths.root))
    )
    target = unit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _install_desktop(launcher_cmd: str, reporter: Reporter) -> None:
    """写 .desktop 与图标；失败只记日志，不影响面板可用。"""
    try:
        icon_target = icon_path()
        icon_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resource("workbuddy2api-panel.svg"), icon_target)

        command = launcher_cmd or guess_launcher_command()
        template = resource("workbuddy2api-launcher.desktop.in").read_text(encoding="utf-8")
        content = template.replace("@EXEC@", command).replace("@ICON@", str(icon_target))
        target = desktop_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        reporter.log(f"桌面入口：{target}")
        if is_command_available("update-desktop-database"):
            run(["update-desktop-database", str(target.parent)], timeout=10)
        if is_command_available("gtk-update-icon-cache"):
            run(["gtk-update-icon-cache", "-f", "-t", str(icon_target.parent.parent.parent)], timeout=10)
    except Exception as exc:  # noqa: BLE001 - 桌面项是锦上添花
        reporter.log(f"桌面入口安装失败（不影响使用）：{exc}")


def guess_launcher_command() -> str:
    entry = Path(sys.argv[0]).resolve()
    if entry.is_file() and os.access(entry, os.X_OK) and entry.name.startswith("workbuddy"):
        return _desktop_quote(str(entry))
    root = Path(__file__).resolve().parent.parent
    entry = root / "workbuddy-launcher"
    if entry.is_file():
        return _desktop_quote(str(entry))
    return _desktop_quote(f"{sys.executable} -m wb_launcher")


def _desktop_quote(value: str) -> str:
    if any(ch in value for ch in ' \t"\''):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def install_extension() -> str:
    source = resource("gnome-extension", EXTENSION_UUID)
    if not source.is_dir():
        raise InstallError("扩展文件缺失（安装不完整）")
    target = extension_dir()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    message = f"GNOME 扩展已安装到 {target}"
    if is_command_available("gnome-extensions"):
        proc = run(["gnome-extensions", "enable", EXTENSION_UUID], timeout=15)
        if proc.returncode == 0:
            message += "，已启用。"
        else:
            message += (
                "。启用失败（%s）。首次安装通常需要注销后重新登录，"
                "再执行 gnome-extensions enable %s。" % ((proc.stderr or proc.stdout or "").strip(), EXTENSION_UUID)
            )
    else:
        message += "。未找到 gnome-extensions 命令，请手动启用。"
    return message


def wait_health(host: str, port: int, timeout: float = 25) -> bool:
    """等服务真的开始监听。/healthz 在没账号时返回 503，所以只看「有没有响应」。"""
    url = service.health_url(host, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reachable, _, _ = http_probe(url, timeout=2)
        if reachable:
            return True
        time.sleep(0.6)
    return False


def uninstall_service(app_dir: Path | str, remove_data: bool = False) -> str:
    paths = payload.PanelPaths(Path(app_dir).expanduser())
    service.stop()
    service.disable()
    for path in (unit_path(), desktop_path(), icon_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    service.daemon_reload()
    if remove_data and paths.root.exists():
        shutil.rmtree(paths.root, ignore_errors=True)
    return "服务已停止并移除" + ("，安装目录已删除" if remove_data else "（账号与配置保留）")
