"""GTK 应用入口。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_DBUS_ID  # noqa: E402
from .settings import LauncherConfig  # noqa: E402
from .window import MainWindow, _install_css  # noqa: E402


class LauncherApp(Adw.Application):
    def __init__(self, config: LauncherConfig | None = None) -> None:
        super().__init__(
            application_id=APP_DBUS_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.config = config or LauncherConfig.load()
        GLib.set_application_name("WorkBuddy2API 面板")
        # 桌面项没被匹配上时（比如从终端直接跑），至少让窗口有个图标
        Gtk.Window.set_default_icon_name("workbuddy2api-panel")
        self._css_ready = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        if not self._css_ready:
            _install_css()
            self._css_ready = True

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = MainWindow(self, self.config)
        window.present()


def run_gui(config: LauncherConfig | None = None) -> int:
    app = LauncherApp(config or LauncherConfig.load())
    return app.run([])
