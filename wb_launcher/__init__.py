"""WorkBuddy2API 面板 · Linux 启动器。

一个用 GTK4 / libadwaita 写的桌面控制器：首次运行自动下载
`misakano7545/workbuddy2api-panel` 的 Linux 单文件二进制，生成本机配置，
以 systemd 用户服务托管，并提供启动 / 停止 / 重启、面板地址、API 密钥、
日志与更新入口。配套的 GNOME Shell 扩展负责托盘区常驻。
"""

__version__ = "1.0.0"

APP_ID = "workbuddy2api-launcher"
APP_NAME = "WorkBuddy2API 面板"
UNIT_NAME = "workbuddy2api.service"
DEFAULT_PORT = 7863
DEFAULT_HOST = "127.0.0.1"

# 上游项目（面板与网关是同一个 Go 二进制）
UPSTREAM_REPO = "misakano7545/workbuddy2api-panel"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
UPSTREAM_RELEASES_URL = f"{UPSTREAM_URL}/releases"

# 下载加速：把原始链接前缀换成该地址（gitproxy.dev 的用法）
DEFAULT_MIRROR = "https://api.gitproxy.dev/"
