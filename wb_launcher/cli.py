"""命令行入口：安装、更新、状态与卸载；不带参数时打开图形界面。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import DEFAULT_MIRROR, __version__, payload, service
from .settings import LauncherConfig


def _reporter(quiet: bool = False) -> "installer.Reporter":
    from . import installer

    def step(text: str) -> None:
        print(f"\n▶ {text}", flush=True)

    def progress(fraction: float, text: str) -> None:
        if quiet:
            return
        bar = "█" * int(fraction * 30)
        print(f"\r  [{bar:<30}] {fraction * 100:3.0f}%  {text}", end="", flush=True)

    def log(text: str) -> None:
        print(f"  · {text}", flush=True)

    return installer.Reporter(on_step=step, on_progress=progress, on_log=log)


def _options_from_args(args: argparse.Namespace, config: LauncherConfig):
    from . import installer

    return installer.InstallOptions(
        app_dir=Path(args.dir).expanduser() if getattr(args, "dir", None) else config.app_path,
        host=getattr(args, "host", "127.0.0.1"),
        port=int(getattr(args, "port", 7863)),
        source=getattr(args, "source", "auto"),
        mirror=getattr(args, "mirror", None) or config.mirror or DEFAULT_MIRROR,
        local_zip=Path(args.local_zip).expanduser() if getattr(args, "local_zip", None) else None,
        autostart=not getattr(args, "no_autostart", False),
        with_extension=bool(getattr(args, "extension", False)),
        start_now=not getattr(args, "no_start", False),
        launcher_cmd=getattr(args, "launcher_cmd", ""),
    )


def cmd_install(args: argparse.Namespace) -> int:
    from . import installer

    config = LauncherConfig.load()
    options = _options_from_args(args, config)
    try:
        version = installer.install(options, _reporter())
    except installer.Cancelled:
        print("\n已取消。")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI 需要把原因完整打出来
        print(f"\n安装失败：{exc}", file=sys.stderr)
        return 1
    config.app_dir = str(options.app_dir)
    config.version = version
    config.source = options.source
    config.extension_installed = bool(args.extension)
    config.save()
    print(f"\n✓ 安装完成：{version or '本地安装包'}")
    print(f"  面板地址：{payload.panel_url(payload.PanelPaths(options.app_dir))}")
    print("  首次打开面板时，把启动器里显示的 API 密钥粘贴进去即可。")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from . import installer

    config = LauncherConfig.load()
    options = _options_from_args(args, config)
    try:
        version = installer.update(options, _reporter())
    except installer.Cancelled:
        print("\n已取消。")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\n更新失败：{exc}", file=sys.stderr)
        return 1
    config.version = version
    config.save()
    print(f"\n✓ 已更新：{version}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    config = LauncherConfig.load()
    paths = payload.PanelPaths(config.app_path)
    if not paths.installed:
        print("尚未安装（运行 `python3 -m wb_launcher install` 或直接打开图形界面）")
        return 1
    host, port = payload.get_listen(paths)
    status = service.status(host, port)
    print(f"安装目录：{paths.root}")
    print(f"版本：{payload.get_version(paths) or '未知'}")
    print(f"监听：{payload.format_listen(host, port)}")
    print(f"面板：{payload.panel_url(paths)}")
    print(f"服务：{status.state} / {status.label}")
    print(f"开机自启：{'是' if service.is_enabled() else '否'}")
    if status.reachable:
        print(f"账号：{status.healthy_accounts} 可用 / {status.accounts} 总计")
        if status.accounts == 0:
            print("提示：还没有账号时 /healthz 会返回 503，属正常现象，先在面板里加账号。")
    else:
        print(f"端口：无响应（{status.detail or '连接失败'}）")
    return 0 if status.reachable else 2


def cmd_open(_args: argparse.Namespace) -> int:
    config = LauncherConfig.load()
    paths = payload.PanelPaths(config.app_path)
    url = payload.panel_url(paths)
    ok, message = service.open_in_browser(url)
    print(url)
    if not ok:
        print(message, file=sys.stderr)
        return 1
    return 0


def cmd_extension(_args: argparse.Namespace) -> int:
    from . import installer

    try:
        print(installer.install_extension())
    except Exception as exc:  # noqa: BLE001
        print(f"安装扩展失败：{exc}", file=sys.stderr)
        return 1
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from . import installer

    config = LauncherConfig.load()
    try:
        message = installer.uninstall_service(config.app_path, remove_data=args.purge)
    except Exception as exc:  # noqa: BLE001
        print(f"卸载失败：{exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


def cmd_opencode_models(args: argparse.Namespace) -> int:
    from . import opencode

    config = LauncherConfig.load()
    paths = payload.PanelPaths(config.app_path)
    if not paths.installed:
        print("面板还没安装，先用 `workbuddy-launcher install` 装好。", file=sys.stderr)
        return 1
    host, port = payload.get_listen(paths)
    api_key = payload.get_api_key(paths)
    if not api_key:
        print(f"{paths.config} 里没有 api_key。", file=sys.stderr)
        return 1
    base_url = service.api_base_url(host, port)
    target = Path(args.config).expanduser() if args.config else None
    try:
        report = opencode.sync(
            base_url,
            api_key,
            provider_id=args.provider_id,
            path=target,
            dry_run=args.dry_run,
        )
    except opencode.OpenCodeError as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        if "连不上网关" in str(exc):
            print("服务是不是没开？用 `workbuddy-launcher status` 看一眼。", file=sys.stderr)
        return 1

    if args.dry_run:
        print(report.preview)
        print(f"\n（--dry-run，没有写入 {report.path}）")
    else:
        print(f"✓ 已同步 {report.models} 个模型到 {report.path}")
    print(f"  网关：{base_url}")
    print(f"  其中 {report.big} 个支持 1M 上下文")
    if report.promos:
        detail = "，".join(f"{k} {v} 个" for k, v in report.promos.items())
        print(f"  促销：{detail}")
        for name, note in report.notes:
            print(f"    · {name}：{note}")
    if not args.dry_run:
        print("  重启 OpenCode 后生效。")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    config = LauncherConfig.load()
    paths = payload.PanelPaths(config.app_path)
    print(f"启动器 {__version__}")
    print(f"面板   {payload.get_version(paths) or '未安装'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbuddy-launcher",
        description="WorkBuddy2API 面板的 Linux 启动器（GTK4 + systemd 用户服务）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "免责声明：本项目与腾讯 / CodeBuddy 官方无关，未获其授权或认可。\n"
            "软件按「现状」提供，不附带任何担保；使用非官方网关可能导致账号被\n"
            "限流、风控或封禁，风险由使用者自行承担。完整条款见 DISCLAIMER.md。"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    install = sub.add_parser("install", help="下载并安装面板，然后启动服务")
    install.add_argument("--dir", help="安装目录（默认 ~/.local/share/workbuddy2api-panel）")
    install.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    install.add_argument("--port", type=int, default=7863, help="监听端口（默认 7863）")
    install.add_argument("--source", choices=["auto", "official", "mirror"], default="auto")
    install.add_argument("--mirror", help="加速前缀，默认 https://api.gitproxy.dev/")
    install.add_argument("--local-zip", help="使用本地 zip 安装包（网络不通时）")
    install.add_argument("--no-autostart", action="store_true", help="不设置登录时自动启动")
    install.add_argument("--extension", action="store_true", help="同时安装 GNOME 顶部栏扩展")
    install.add_argument("--no-start", action="store_true", help="安装完成后先不启动服务")
    install.set_defaults(func=cmd_install)

    update = sub.add_parser("update", help="检查并安装最新版本")
    update.add_argument("--source", choices=["auto", "official", "mirror"], default="auto")
    update.add_argument("--mirror")
    update.add_argument("--local-zip")
    update.set_defaults(func=cmd_update)

    status = sub.add_parser("status", help="显示服务状态")
    status.set_defaults(func=cmd_status)

    open_cmd = sub.add_parser("open", help="在浏览器中打开面板")
    open_cmd.set_defaults(func=cmd_open)

    extension = sub.add_parser("extension", help="安装 / 更新 GNOME 顶部栏扩展")
    extension.set_defaults(func=cmd_extension)

    uninstall = sub.add_parser("uninstall", help="停止并移除 systemd 服务")
    uninstall.add_argument("--purge", action="store_true", help="连安装目录（账号与数据）一起删除")
    uninstall.set_defaults(func=cmd_uninstall)

    oc = sub.add_parser("opencode-models", help="把网关的模型清单同步进 OpenCode 配置")
    oc.add_argument("--config", help="OpenCode 配置文件（默认 ~/.config/opencode/opencode.jsonc）")
    oc.add_argument("--provider-id", default="wb", help="provider 名字（默认 wb）")
    oc.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    oc.set_defaults(func=cmd_opencode_models)

    version = sub.add_parser("version", help="显示版本")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        from .app import run_gui

        return run_gui(LauncherConfig.load())
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return int(args.func(args) or 0)
