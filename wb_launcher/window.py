"""GTK4 / libadwaita 界面：主控制页、首次安装向导与更新对话框。"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import DEFAULT_MIRROR, DEFAULT_PORT, UNIT_NAME  # noqa: E402
from . import installer, payload, service  # noqa: E402
from .settings import LauncherConfig  # noqa: E402
from .util import human_size, run_async  # noqa: E402

APP_CSS = """
.status-pill {
    padding: 3px 12px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 0.85em;
}
.status-ok { background: alpha(@success_color, 0.16); color: @success_color; }
.status-warn { background: alpha(@warning_color, 0.18); color: @warning_color; }
.status-off { background: alpha(@window_fg_color, 0.08); color: @window_fg_color; }
.status-err { background: alpha(@error_color, 0.16); color: @error_color; }
.big-title { font-size: 1.6em; font-weight: 800; }
.dim-label { opacity: 0.75; }
.mono { font-family: monospace; font-size: 0.9em; }
.section-title { font-weight: 700; font-size: 1.05em; }
"""


def _install_css() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(APP_CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _copy(text: str) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    display.get_clipboard().set(text)


def _toast(overlay: Adw.ToastOverlay, text: str) -> None:
    overlay.add_toast(Adw.Toast.new(text))


def _error_dialog(parent: Gtk.Widget, heading: str, body: str) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("close", "知道了")
    dialog.set_default_response("close")
    dialog.present(parent)


def _confirm_dialog(parent: Gtk.Widget, heading: str, body: str, action: str, on_ok) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "取消")
    dialog.add_response("ok", action)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _respond(_dialog, response: str) -> None:
        if response == "ok":
            on_ok()

    dialog.connect("response", _respond)
    dialog.present(parent)


class EndpointDialog(Adw.Window):
    """修改面板监听地址；写回 config.json 后按需重启服务。"""

    def __init__(self, parent: Gtk.Window, paths: payload.PanelPaths, on_saved) -> None:
        super().__init__(transient_for=parent, modal=True, title="修改监听地址")
        self.paths = paths
        self.on_saved = on_saved
        host, port = payload.get_listen(paths)

        self.set_default_size(460, -1)
        self.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        group = Adw.PreferencesGroup(title="监听地址")
        self.host_row = Adw.EntryRow(title="主机")
        self.host_row.set_text(host)
        self.port_row = Adw.EntryRow(title="端口")
        self.port_row.set_text(str(port))
        group.add(self.host_row)
        group.add(self.port_row)
        content.append(group)

        hint = Gtk.Label(
            label="127.0.0.1 只允许本机访问（推荐）。改成 0.0.0.0 后同一局域网的设备都能打开面板，"
            "而面板只靠 API 密钥保护，请自行评估风险。",
            wrap=True,
            xalign=0,
        )
        hint.add_css_class("dim-label")
        content.append(hint)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="保存并重启")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        actions.append(cancel)
        actions.append(save)
        content.append(actions)

        box.append(content)
        self.set_content(box)

    def _save(self, _button: Gtk.Button) -> None:
        host = self.host_row.get_text().strip()
        port_text = self.port_row.get_text().strip()
        if not host:
            _error_dialog(self, "地址无效", "主机不能为空")
            return
        if not port_text.isdigit() or not (1 <= int(port_text) <= 65535):
            _error_dialog(self, "端口无效", "端口必须是 1–65535 之间的数字")
            return
        self.close()
        self.on_saved(host, int(port_text))


class UpdateDialog(Adw.Window):
    """检查 / 执行更新；下载与安装都在后台线程，完成后回调主界面刷新。"""

    def __init__(self, parent: Gtk.Window, config: LauncherConfig, on_done) -> None:
        super().__init__(transient_for=parent, modal=True, title="检查更新")
        self.config = config
        self.on_done = on_done
        self.paths = payload.PanelPaths(config.app_path)
        self._busy = False
        self._close_when_done = False

        self.set_default_size(560, 440)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        self.status = Gtk.Label(label="正在检查最新版本…", xalign=0, wrap=True)
        self.status.add_css_class("big-title")
        content.append(self.status)

        self.detail = Gtk.Label(label="", xalign=0, wrap=True)
        self.detail.add_css_class("dim-label")
        content.append(self.detail)

        self.notes = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.notes.get_buffer().set_text("")
        self.notes.add_css_class("mono")
        notes_scroll = Gtk.ScrolledWindow(vexpand=True)
        notes_scroll.set_child(self.notes)
        notes_scroll.set_min_content_height(160)
        content.append(notes_scroll)

        self.progress = Gtk.ProgressBar(show_text=False)
        self.progress.set_visible(False)
        content.append(self.progress)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.END)
        self.close_button = Gtk.Button(label="关闭")
        self.close_button.connect("clicked", lambda *_: self.close())
        self.install_button = Gtk.Button(label="安装更新")
        self.install_button.add_css_class("suggested-action")
        self.install_button.set_sensitive(False)
        self.install_button.connect("clicked", self._install)
        actions.append(self.close_button)
        actions.append(self.install_button)
        content.append(actions)

        box.append(content)
        self.set_content(box)
        self._check()

    def _check(self) -> None:
        def work():
            latest = installer.release.fetch_latest()
            asset = installer.release.pick_asset(latest)
            return latest, asset

        def done(result):
            latest, asset = result
            current = payload.get_version(self.paths)
            self.detail.set_text(
                f"当前版本：{current or '未记录'}\n最新版本：{latest.tag}\n"
                f"安装包：{asset.name}（{human_size(asset.size)}）"
            )
            self.notes.get_buffer().set_text((latest.notes or "（该版本没有发布说明）")[:20000])
            if current and current == latest.tag:
                self.status.set_text("已是最新版本")
                self.install_button.set_visible(False)
            else:
                self.status.set_text("发现新版本")
                self.install_button.set_sensitive(True)
                self.install_button.grab_focus()

        def failed(exc: BaseException):
            self.status.set_text("检查更新失败")
            self.detail.set_text(str(exc))
            self.install_button.set_visible(False)

        run_async(work, lambda r: GLib.idle_add(done, r), lambda e: GLib.idle_add(failed, e))

    def _install(self, _button: Gtk.Button) -> None:
        if self._busy:
            return
        self._busy = True
        self.install_button.set_sensitive(False)
        self.close_button.set_sensitive(False)
        self.progress.set_visible(True)
        self.status.set_text("正在更新…")

        options = installer.InstallOptions(
            app_dir=self.paths.root,
            source=self.config.source,
            mirror=self.config.mirror,
            start_now=True,
        )

        def on_progress(fraction: float, text: str) -> None:
            def apply():
                self.progress.set_fraction(fraction)
                self.detail.set_text(text)
                return False

            GLib.idle_add(apply)

        reporter = installer.Reporter(
            on_step=lambda text: GLib.idle_add(self._set_status, text),
            on_progress=on_progress,
            on_log=lambda text: GLib.idle_add(self._append_note, text),
        )

        def work():
            return installer.update(options, reporter)

        def done(version: str):
            self._busy = False
            self.status.set_text("更新完成")
            self.detail.set_text(f"已更新到 {version or '新版本'}，服务已重启。")
            self.progress.set_fraction(1.0)
            self.close_button.set_sensitive(True)
            self._close_when_done = True
            self.on_done()

        def failed(exc: BaseException):
            self._busy = False
            self.status.set_text("更新失败")
            self.detail.set_text(str(exc))
            self.progress.set_visible(False)
            self.close_button.set_sensitive(True)

        run_async(work, lambda r: GLib.idle_add(done, r), lambda e: GLib.idle_add(failed, e))

    def _set_status(self, text: str) -> bool:
        self.status.set_text(text)
        return False

    def _append_note(self, text: str) -> bool:
        buffer = self.notes.get_buffer()
        buffer.set_text((self.notes.get_buffer().get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) + "\n" + text).strip())
        return False


class LogDialog(Adw.Window):
    """查看 systemd 用户服务的日志（journalctl --user）。"""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(transient_for=parent, modal=True, title="服务日志")
        self.set_default_size(760, 520)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.add_css_class("mono")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.view)
        scroll.add_css_class("card")
        content.append(scroll)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.END)
        self.copy_button = Gtk.Button(label="复制全部")
        self.copy_button.connect("clicked", lambda *_: self._copy())
        refresh = Gtk.Button(label="刷新")
        refresh.add_css_class("suggested-action")
        refresh.connect("clicked", lambda *_: self._load())
        close = Gtk.Button(label="关闭")
        close.connect("clicked", lambda *_: self.close())
        actions.append(self.copy_button)
        actions.append(refresh)
        actions.append(close)
        content.append(actions)

        box.append(content)
        self.set_content(box)
        self._load()

    def _load(self) -> None:
        self.copy_button.set_sensitive(False)
        self._set_text("正在读取日志…")

        def done(text: str) -> bool:
            self._set_text(text)
            self.copy_button.set_sensitive(True)
            return False

        run_async(lambda: service.journal(400), lambda text: GLib.idle_add(done, text))

    def _set_text(self, text: str) -> None:
        buffer = self.view.get_buffer()
        buffer.set_text(text)
        self.view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 1.0)

    def _copy(self) -> None:
        buffer = self.view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        _copy(text)
        self.copy_button.set_label("已复制")
        GLib.timeout_add_seconds(2, self._reset_copy_label)

    def _reset_copy_label(self) -> bool:
        self.copy_button.set_label("复制全部")
        return GLib.SOURCE_REMOVE


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, config: LauncherConfig) -> None:
        super().__init__(application=application, title="WorkBuddy2API 面板")
        self.config = config
        self.paths = payload.PanelPaths(config.app_path)
        self._status_inflight = False
        self._ticker = 0
        self._suppress_autostart = False

        self.set_default_size(config.window_width, config.window_height)

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)

        self.main_page = self._build_main_page()
        self.setup_page = SetupPage(self, config, self._on_installed)
        self.stack.add_named(self.main_page, "main")
        self.stack.add_named(self.setup_page, "setup")

        if self.paths.installed:
            self.stack.set_visible_child_name("main")
            self._ticker = GLib.timeout_add_seconds(3, self._tick)
            self._refresh_status()
        else:
            self.stack.set_visible_child_name("setup")

        self.connect("close-request", self._on_close)

    # ── 主页面 ────────────────────────────────────────────────────

    def _build_main_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="WorkBuddy2API 面板", subtitle="本机控制台"))
        page.append(header)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("检查更新", "win.check_update")
        menu.append("重新安装 / 修复", "win.reinstall")
        menu.append("打开安装目录", "win.open_dir")
        menu.append("移除服务", "win.uninstall")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        self.banner = Adw.Banner(title="")
        self.banner.set_revealed(False)
        page.append(self.banner)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=520)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(24)
        content.set_margin_start(18)
        content.set_margin_end(18)
        scroll.set_child(clamp)
        clamp.set_child(content)
        page.append(scroll)

        # 状态卡片
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("card")
        card.set_margin_top(2)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        icon.set_pixel_size(40)
        top.append(icon)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        self.status_title = Gtk.Label(label="检查中…", xalign=0)
        self.status_title.add_css_class("big-title")
        self.status_detail = Gtk.Label(label="", xalign=0)
        self.status_detail.add_css_class("dim-label")
        text_box.append(self.status_title)
        text_box.append(self.status_detail)
        top.append(text_box)
        self.status_pill = Gtk.Label(label="检查中")
        self.status_pill.add_css_class("status-pill")
        self.status_pill.add_css_class("status-off")
        top.append(self.status_pill)
        card.append(top)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_homogeneous(True)
        self.start_button = Gtk.Button(label="启动")
        self.start_button.connect("clicked", lambda *_: self._service_action("start"))
        self.stop_button = Gtk.Button(label="停止")
        self.stop_button.connect("clicked", lambda *_: self._service_action("stop"))
        self.restart_button = Gtk.Button(label="重启")
        self.restart_button.connect("clicked", lambda *_: self._service_action("restart"))
        self.open_button = Gtk.Button(label="打开面板")
        self.open_button.add_css_class("suggested-action")
        self.open_button.set_sensitive(False)
        self.open_button.connect("clicked", lambda *_: self._open_panel())
        for button in (self.start_button, self.stop_button, self.restart_button, self.open_button):
            buttons.append(button)
        card.append(buttons)
        content.append(card)

        # 连接信息
        group = Adw.PreferencesGroup(title="连接")
        self.url_row = Adw.ActionRow(title="面板地址")
        self.url_row.set_activatable(True)
        self.url_row.connect("activated", lambda *_: self._open_panel())
        open_suffix = Gtk.Button(icon_name="web-browser-symbolic", valign=Gtk.Align.CENTER)
        open_suffix.add_css_class("flat")
        open_suffix.set_tooltip_text("在浏览器中打开")
        open_suffix.connect("clicked", lambda *_: self._open_panel())
        self.url_row.add_suffix(open_suffix)
        group.add(self.url_row)

        self.key_row = Adw.ActionRow(title="API 密钥", subtitle="打开面板后粘贴到密钥框；浏览器会记住")
        self.key_row.set_subtitle_lines(2)
        self._key_visible = False
        reveal = Gtk.Button(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER)
        reveal.add_css_class("flat")
        reveal.set_tooltip_text("显示 / 隐藏")
        reveal.connect("clicked", lambda button: self._toggle_key(button))
        copy_key = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy_key.add_css_class("flat")
        copy_key.set_tooltip_text("复制密钥")
        copy_key.connect("clicked", lambda *_: self._copy_key())
        self.key_row.add_suffix(reveal)
        self.key_row.add_suffix(copy_key)
        group.add(self.key_row)

        self.listen_row = Adw.ActionRow(title="监听地址")
        change = Gtk.Button(label="修改", valign=Gtk.Align.CENTER)
        change.add_css_class("flat")
        change.connect("clicked", lambda *_: self._edit_endpoint())
        self.listen_row.add_suffix(change)
        group.add(self.listen_row)
        content.append(group)

        # 偏好
        prefs = Adw.PreferencesGroup(title="启动与更新")
        self.autostart_row = Adw.SwitchRow(title="登录时自动启动", subtitle="启用 systemd 用户服务，不需要打开启动器")
        self.autostart_row.connect("notify::active", self._on_autostart_toggle)
        prefs.add(self.autostart_row)

        self.version_row = Adw.ActionRow(title="面板版本")
        check = Gtk.Button(label="检查更新", valign=Gtk.Align.CENTER)
        check.add_css_class("flat")
        check.connect("clicked", lambda *_: self._check_update())
        self.version_row.add_suffix(check)
        prefs.add(self.version_row)
        content.append(prefs)

        # 日志
        logs_group = Adw.PreferencesGroup(title="日志")
        self.logs_row = Adw.ActionRow(title="服务日志", subtitle=f"journalctl --user -u {UNIT_NAME}")
        self.logs_row.set_activatable(True)
        self.logs_row.connect("activated", lambda *_: self._show_logs())
        open_logs = Gtk.Button(label="查看", valign=Gtk.Align.CENTER)
        open_logs.add_css_class("flat")
        open_logs.connect("clicked", lambda *_: self._show_logs())
        self.logs_row.add_suffix(open_logs)
        logs_group.add(self.logs_row)
        content.append(logs_group)

        footer = Gtk.Label(
            label="面板与网关是同一个进程，由 systemd 用户服务 workbuddy2api.service 托管。"
            "账号、状态与请求归档都在安装目录的 data/ 与 auths/ 下。",
            wrap=True,
            xalign=0,
        )
        footer.add_css_class("dim-label")
        footer.set_margin_top(4)
        content.append(footer)

        self._install_actions()
        self._apply_local_state()
        return page

    def _install_actions(self) -> None:
        # Gtk.Widget 没有 add_action；窗口级动作要放进 Gio.SimpleActionGroup，
        # 再用前缀 `win` 插进窗口，菜单里的 win.check_update 才找得到。
        group = Gio.SimpleActionGroup()
        actions = {
            "check_update": lambda *_: self._check_update(),
            "reinstall": lambda *_: self._reinstall(),
            "open_dir": lambda *_: self._open_app_dir(),
            "uninstall": lambda *_: self._uninstall(),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            group.add_action(action)
        self.insert_action_group("win", group)

    def _apply_local_state(self) -> None:
        host, port = payload.get_listen(self.paths)
        url = payload.panel_url(self.paths)
        self.url_row.set_subtitle(url)
        self.listen_row.set_subtitle(payload.format_listen(host, port) + ("（仅本机）" if host == "127.0.0.1" else ""))
        self.version_row.set_subtitle(payload.get_version(self.paths) or "未知")
        self._update_key_label()
        enabled = service.is_enabled()
        self._suppress_autostart = True
        self.autostart_row.set_active(enabled)
        self._suppress_autostart = False

    def _update_key_label(self) -> None:
        key = payload.get_api_key(self.paths)
        if not key:
            self.key_row.set_subtitle("未设置（面板不鉴权，仅建议本机使用）")
            return
        if self._key_visible:
            self.key_row.set_subtitle(key)
        else:
            self.key_row.set_subtitle(key[:5] + "•" * 12 + key[-4:])

    def _toggle_key(self, button: Gtk.Button) -> None:
        self._key_visible = not self._key_visible
        button.set_icon_name("view-conceal-symbolic" if self._key_visible else "view-reveal-symbolic")
        self._update_key_label()

    def _copy_key(self) -> None:
        key = payload.get_api_key(self.paths)
        if not key:
            _toast(self.toasts, "这个面板没有设置 API 密钥")
            return
        _copy(key)
        _toast(self.toasts, "API 密钥已复制，粘贴到面板的密钥框即可")

    def _open_panel(self) -> None:
        url = payload.panel_url(self.paths)
        ok, message = service.open_in_browser(url)
        if not ok:
            _error_dialog(self, "无法打开浏览器", message or url)

    def _open_app_dir(self) -> None:
        ok, message = service.open_in_browser(str(self.paths.root))
        if not ok:
            _error_dialog(self, "无法打开目录", message or str(self.paths.root))

    # ── 状态与控制 ────────────────────────────────────────────────

    def _tick(self) -> bool:
        if self.stack.get_visible_child_name() == "main":
            self._refresh_status()
        return GLib.SOURCE_CONTINUE

    def _refresh_status(self) -> None:
        if self._status_inflight:
            return
        self._status_inflight = True
        host, port = payload.get_listen(self.paths)

        def work():
            return service.status(host, port)

        def done(status: service.ServiceStatus) -> None:
            self._status_inflight = False
            self._apply_status(status)
            return False

        def failed(exc: BaseException) -> None:
            self._status_inflight = False
            self.status_title.set_text("状态未知")
            self.status_detail.set_text(str(exc))
            return False

        run_async(work, lambda result: GLib.idle_add(done, result), lambda exc: GLib.idle_add(failed, exc))

    def _apply_status(self, status: service.ServiceStatus) -> None:
        self.status_title.set_text(status.label)
        parts = [
            payload.panel_url(self.paths),
            f"版本 {payload.get_version(self.paths) or '未知'}",
        ]
        if status.accounts >= 0:
            parts.append(f"账号 {status.accounts}")
        self.status_detail.set_text(" · ".join(parts))

        for css in ("status-ok", "status-warn", "status-off", "status-err"):
            self.status_pill.remove_css_class(css)
        if status.state == "active" and status.reachable:
            self.status_pill.set_text("运行中")
            self.status_pill.add_css_class("status-ok")
        elif status.state == "active":
            self.status_pill.set_text("未就绪")
            self.status_pill.add_css_class("status-warn")
        elif status.state in ("activating", "reloading"):
            self.status_pill.set_text("启动中")
            self.status_pill.add_css_class("status-warn")
        elif status.state == "failed":
            self.status_pill.set_text("失败")
            self.status_pill.add_css_class("status-err")
        else:
            self.status_pill.set_text("已停止")
            self.status_pill.add_css_class("status-off")

        self.start_button.set_sensitive(not status.running and status.state != "activating")
        self.stop_button.set_sensitive(status.running or status.state == "activating")
        # 面板只要在监听就能打开（没账号时面板本来就是用来加账号的）
        self.open_button.set_sensitive(status.reachable)
        self.banner.set_revealed(status.state == "failed")
        if status.state == "failed":
            self.banner.set_title("服务启动失败，打开日志查看原因")

    def _service_action(self, action: str) -> None:
        fn = {"start": service.start, "stop": service.stop, "restart": service.restart}[action]
        self.start_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.restart_button.set_sensitive(False)

        def done(result):
            ok, message = result
            if not ok:
                _error_dialog(self, "操作失败", message or "systemctl 返回了错误")
            else:
                _toast(self.toasts, message)
            self.restart_button.set_sensitive(True)
            self._refresh_status()
            return False

        run_async(fn, lambda result: GLib.idle_add(done, result))

    def _on_autostart_toggle(self, row: Adw.SwitchRow, _pspec) -> None:
        if self._suppress_autostart:
            return
        desired = row.get_active()

        def done(result):
            ok, message = result
            if not ok:
                _error_dialog(self, "设置开机自启失败", message)
                self._suppress_autostart = True
                row.set_active(not desired)
                self._suppress_autostart = False
            else:
                _toast(self.toasts, "已开启登录时自动启动" if desired else "已关闭登录时自动启动")
            return False

        run_async(service.enable if desired else service.disable, lambda result: GLib.idle_add(done, result))

    def _edit_endpoint(self) -> None:
        EndpointDialog(self, self.paths, self._on_endpoint_saved).present()

    def _on_endpoint_saved(self, host: str, port: int) -> None:
        # 写配置放在这里，保证「对话框」和「直接调用」行为一致
        try:
            listen = payload.set_listen(self.paths, host, port)
        except OSError as exc:
            _error_dialog(self, "保存失败", f"无法写入 {self.paths.config}：{exc}")
            return
        if service.unit_state() == "active":
            def done(result):
                ok, message = result
                if not ok:
                    _error_dialog(self, "重启失败", f"地址已改为 {listen}，但服务没有重启成功：{message}")
                else:
                    _toast(self.toasts, f"监听地址已改为 {listen}")
                self._apply_local_state()
                self._refresh_status()
                return False

            run_async(service.restart, lambda result: GLib.idle_add(done, result))
        else:
            _toast(self.toasts, f"监听地址已改为 {listen}，下次启动生效")
            self._apply_local_state()

    def _check_update(self) -> None:
        UpdateDialog(self, self.config, self._after_update).present()

    def _after_update(self) -> None:
        self.config.version = payload.get_version(self.paths)
        self.config.save()
        self._apply_local_state()
        self._refresh_status()

    def _reinstall(self) -> None:
        def go() -> None:
            options = installer.InstallOptions(
                app_dir=self.paths.root,
                source=self.config.source,
                mirror=self.config.mirror,
                autostart=service.is_enabled(),
                start_now=True,
            )
            dialog = InstallProgressDialog(self, "重新安装 / 修复", options, self._after_reinstall)
            dialog.present()

        _confirm_dialog(
            self,
            "重新安装面板？",
            "会重新下载当前最新版本并替换程序文件；config.json、auths/ 与 data/ 都会保留。",
            "重新安装",
            go,
        )

    def _after_reinstall(self) -> None:
        self._apply_local_state()
        self._refresh_status()

    def _uninstall(self) -> None:
        def go() -> None:
            def work():
                return installer.uninstall_service(self.paths.root, remove_data=False)

            def done(message: str) -> bool:
                _toast(self.toasts, message)
                self.config.version = ""
                self.config.save()
                self.stack.set_visible_child_name("setup")
                return False

            run_async(work, lambda message: GLib.idle_add(done, message))

        _confirm_dialog(
            self,
            "移除服务？",
            f"会停止并删除 systemd 单元与桌面入口。账号与配置仍保留在 {self.paths.root}。",
            "移除",
            go,
        )

    def _show_logs(self) -> None:
        if not self.paths.installed:
            _toast(self.toasts, "面板还没有安装")
            return
        LogDialog(self).present()

    def _on_close(self, _window: Gtk.Window) -> bool:
        self.config.window_width = self.get_width()
        self.config.window_height = self.get_height()
        try:
            self.config.save()
        except OSError:
            pass
        if self._ticker:
            GLib.source_remove(self._ticker)
            self._ticker = 0
        return False

    # ── 首次安装 ──────────────────────────────────────────────────

    def _on_installed(self, app_dir: str, version: str) -> None:
        self.config.app_dir = app_dir
        self.config.version = version
        self.config.save()
        self.paths = payload.PanelPaths(Path(app_dir))
        self.stack.set_visible_child_name("main")
        self._apply_local_state()
        self._refresh_status()
        if not self._ticker:
            self._ticker = GLib.timeout_add_seconds(3, self._tick)


class SetupPage(Gtk.Box):
    """首次运行：选择安装位置与端口，然后一键下载安装并启动。"""

    def __init__(self, window: MainWindow, config: LauncherConfig, on_installed) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.config = config
        self.on_installed = on_installed
        self.local_zip: Path | None = None
        self._cancel = threading.Event()
        self._busy = False

        header = Adw.HeaderBar()
        self.append(header)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=520)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(24)
        content.set_margin_bottom(30)
        content.set_margin_start(18)
        content.set_margin_end(18)
        scroll.set_child(clamp)
        clamp.set_child(content)
        self.append(scroll)

        icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        icon.set_pixel_size(72)
        icon.set_halign(Gtk.Align.CENTER)
        content.append(icon)

        title = Gtk.Label(label="安装 WorkBuddy2API 面板", xalign=0.5)
        title.add_css_class("big-title")
        content.append(title)

        subtitle = Gtk.Label(
            label="自动下载官方 Linux 单文件程序，生成本机配置，并用 systemd 用户服务托管。"
            "所有数据都留在你的家目录，不需要 root。",
            wrap=True,
            justify=Gtk.Justification.CENTER,
            xalign=0.5,
        )
        subtitle.add_css_class("dim-label")
        content.append(subtitle)

        group = Adw.PreferencesGroup(title="安装设置")
        self.dir_row = Adw.EntryRow(title="安装目录")
        self.dir_row.set_text(config.app_dir)
        group.add(self.dir_row)

        self.host_row = Adw.ComboRow(
            title="监听地址",
            model=Gtk.StringList.new(["127.0.0.1（仅本机，推荐）", "0.0.0.0（局域网可访问）", "::1（IPv6 本机）"]),
        )
        group.add(self.host_row)

        self.port_row = Adw.SpinRow(
            title="端口",
            adjustment=Gtk.Adjustment(value=DEFAULT_PORT, lower=1024, upper=65535, step_increment=1, page_increment=10),
        )
        group.add(self.port_row)

        self.source_row = Adw.ComboRow(
            title="下载源",
            model=Gtk.StringList.new(["自动：官方优先，失败改用 GitProxy", "仅官方 GitHub", "GitProxy 加速"]),
        )
        group.add(self.source_row)

        self.zip_row = Adw.ActionRow(title="本地安装包（可选）", subtitle="网络不通时，先手动下载 linux-amd64.zip")
        choose = Gtk.Button(label="选择…", valign=Gtk.Align.CENTER)
        choose.add_css_class("flat")
        choose.connect("clicked", lambda *_: self._choose_zip())
        self.zip_row.add_suffix(choose)
        group.add(self.zip_row)

        self.autostart_row = Adw.SwitchRow(title="登录时自动启动", active=True)
        group.add(self.autostart_row)

        self.extension_row = Adw.SwitchRow(
            title="安装 GNOME 托盘扩展",
            subtitle="在顶部面板显示状态并提供启停菜单；安装后可能需要注销重新登录",
            active=True,
        )
        group.add(self.extension_row)
        content.append(group)

        self.install_button = Gtk.Button(label="安装并启动")
        self.install_button.add_css_class("suggested-action")
        self.install_button.add_css_class("pill")
        self.install_button.set_halign(Gtk.Align.CENTER)
        self.install_button.set_size_request(200, -1)
        self.install_button.connect("clicked", lambda *_: self._start_install())
        content.append(self.install_button)

        # 进度区
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.progress_box.set_visible(False)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_box.append(self.progress_bar)
        self.progress_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.progress_label.add_css_class("dim-label")
        self.progress_box.append(self.progress_label)

        self.log_expander = Gtk.Expander(label="安装日志")
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_view.add_css_class("mono")
        self.log_scroll = Gtk.ScrolledWindow()
        self.log_scroll.set_child(self.log_view)
        self.log_scroll.set_min_content_height(180)
        self.log_scroll.add_css_class("card")
        self.log_expander.set_child(self.log_scroll)
        self.progress_box.append(self.log_expander)

        self.cancel_button = Gtk.Button(label="取消")
        self.cancel_button.set_halign(Gtk.Align.CENTER)
        self.cancel_button.connect("clicked", lambda *_: self._cancel.set())
        self.progress_box.append(self.cancel_button)
        content.append(self.progress_box)

        foot = Gtk.Label(
            label="安装包来自 misakano7545/workbuddy2api-panel 的官方 Release，下载后会核对 GitHub 给出的 SHA-256 摘要。",
            wrap=True,
            xalign=0.5,
            justify=Gtk.Justification.CENTER,
        )
        foot.add_css_class("dim-label")
        content.append(foot)

    # ── 交互 ──────────────────────────────────────────────────────

    def _choose_zip(self) -> None:
        dialog = Gtk.FileDialog(title="选择安装包")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        zip_filter = Gtk.FileFilter()
        zip_filter.set_name("zip 安装包")
        zip_filter.add_pattern("*.zip")
        filters.append(zip_filter)
        dialog.set_filters(filters)

        def done(source, result):
            try:
                file = source.open_finish(result)
            except GLib.Error:
                return
            self.local_zip = Path(file.get_path())
            self.zip_row.set_subtitle(str(self.local_zip))
            self.source_row.set_sensitive(False)

        dialog.open(self.window, None, done)

    def _start_install(self) -> None:
        if self._busy:
            return
        app_dir_text = self.dir_row.get_text().strip()
        if not app_dir_text:
            _error_dialog(self.window, "安装目录不能为空", "请选择一个目录")
            return
        app_dir = Path(app_dir_text).expanduser()
        if not app_dir.is_absolute():
            _error_dialog(self.window, "安装目录必须是绝对路径", str(app_dir))
            return
        host = ["127.0.0.1", "0.0.0.0", "::1"][self._host_index()]
        port = int(self.port_row.get_value())
        source = ["auto", "official", "mirror"][self.source_row.get_selected()]

        options = installer.InstallOptions(
            app_dir=app_dir,
            host=host,
            port=port,
            source=source,
            mirror=self.config.mirror or DEFAULT_MIRROR,
            local_zip=self.local_zip,
            autostart=self.autostart_row.get_active(),
            with_extension=self.extension_row.get_active(),
            start_now=True,
            launcher_cmd=installer.guess_launcher_command(),
        )

        self._busy = True
        self._cancel.clear()
        self.install_button.set_sensitive(False)
        self.progress_box.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        self.progress_label.set_text("准备中…")
        self.log_view.get_buffer().set_text("")
        self.log_expander.set_expanded(True)
        self.dir_row.set_sensitive(False)

        def on_step(text: str) -> None:
            GLib.idle_add(self._set_step, text)

        def on_progress(fraction: float, text: str) -> None:
            GLib.idle_add(self._set_progress, fraction, text)

        def on_log(text: str) -> None:
            GLib.idle_add(self._append_log, text)

        reporter = installer.Reporter(
            on_step=on_step,
            on_progress=on_progress,
            on_log=on_log,
            cancel=self._cancel.is_set,
        )

        def work():
            return installer.install(options, reporter)

        def done(version: str) -> None:
            self._busy = False
            self.progress_bar.set_fraction(1.0)
            self.progress_label.set_text("安装完成")
            self.install_button.set_sensitive(True)
            self.dir_row.set_sensitive(True)
            self.config.app_dir = str(app_dir)
            self.config.source = source
            self.on_installed(str(app_dir), version)

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.install_button.set_sensitive(True)
            self.dir_row.set_sensitive(True)
            self.progress_label.set_text(f"安装失败：{exc}")
            _error_dialog(self.window, "安装失败", str(exc))

        run_async(work, lambda result: GLib.idle_add(done, result), lambda exc: GLib.idle_add(failed, exc))

    def _host_index(self) -> int:
        return max(0, min(2, self.host_row.get_selected()))

    def _set_step(self, text: str) -> bool:
        self.progress_label.set_text(text)
        self.progress_bar.pulse()
        return False

    def _set_progress(self, fraction: float, text: str) -> bool:
        self.progress_bar.set_fraction(fraction)
        if text:
            self.progress_label.set_text(text)
        return False

    def _append_log(self, text: str) -> bool:
        buffer = self.log_view.get_buffer()
        start = buffer.get_start_iter()
        existing = buffer.get_text(start, buffer.get_end_iter(), True)
        buffer.set_text((existing + "\n" + text).strip())
        return False


class InstallProgressDialog(Adw.Window):
    """菜单里的「重新安装 / 修复」用；安装完成回调主窗口。"""

    def __init__(self, parent: Gtk.Window, title: str, options: installer.InstallOptions, on_done) -> None:
        super().__init__(transient_for=parent, modal=True, title=title)
        self.on_done = on_done
        self.set_default_size(560, 460)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        self.label = Gtk.Label(label="准备中…", xalign=0, wrap=True)
        self.label.add_css_class("section-title")
        content.append(self.label)
        self.progress = Gtk.ProgressBar()
        content.append(self.progress)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log.add_css_class("mono")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.log)
        scroll.add_css_class("card")
        content.append(scroll)
        self.close_button = Gtk.Button(label="关闭")
        self.close_button.set_halign(Gtk.Align.END)
        self.close_button.connect("clicked", lambda *_: self.close())
        self.close_button.set_sensitive(False)
        content.append(self.close_button)
        box.append(content)
        self.set_content(box)

        reporter = installer.Reporter(
            on_step=lambda text: GLib.idle_add(self._step, text),
            on_progress=lambda fraction, text: GLib.idle_add(self._progress, fraction, text),
            on_log=lambda text: GLib.idle_add(self._append, text),
        )

        def done(version: str) -> None:
            self.label.set_text(f"完成：{version or '已安装'}")
            self.progress.set_fraction(1.0)
            self.close_button.set_sensitive(True)
            self.on_done()

        def failed(exc: BaseException) -> None:
            self.label.set_text(f"失败：{exc}")
            self.close_button.set_sensitive(True)

        run_async(lambda: installer.install(options, reporter), lambda result: GLib.idle_add(done, result), lambda exc: GLib.idle_add(failed, exc))

    def _step(self, text: str) -> bool:
        self.label.set_text(text)
        return False

    def _progress(self, fraction: float, text: str) -> bool:
        self.progress.set_fraction(fraction)
        if text:
            self.label.set_text(text)
        return False

    def _append(self, text: str) -> bool:
        buffer = self.log.get_buffer()
        start = buffer.get_start_iter()
        existing = buffer.get_text(start, buffer.get_end_iter(), True)
        buffer.set_text((existing + "\n" + text).strip())
        return False
