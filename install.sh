#!/usr/bin/env bash
# WorkBuddy2API 面板启动器 · 安装脚本
#
# 只安装「启动器」本身（GTK4 界面 + 命令行）；面板程序由启动器首次运行时
# 从上游 Release 下载，装在 ~/.local/share/workbuddy2api-panel。
#
# 用法：
#   ./install.sh                    # 装到 ~/.local
#   PREFIX=/usr/local ./install.sh  # 换安装前缀（仍不需要 root）
#   ./install.sh --extension        # 顺便装 GNOME 顶部栏扩展
#   ./uninstall.sh                  # 卸载启动器

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="workbuddy2api-launcher"
# 桌面项文件名必须等于 GApplication 的 application_id（见 wb_launcher/__init__.py），
# 否则 GNOME 认不出运行中的窗口，Dock 里会是空白图标。
DESKTOP_ID="io.github.workbuddy2api.launcher.desktop"
EXT_UUID="workbuddy2api-panel@local"
PREFIX="${PREFIX:-$HOME/.local}"
WITH_EXTENSION=0

for arg in "$@"; do
    case "$arg" in
        --extension) WITH_EXTENSION=1 ;;
        -h|--help)
            sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数：$arg（支持 --extension）" >&2
            exit 2
            ;;
    esac
done

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
LIB_DIR="$PREFIX/share/$APP_ID"
BIN_DIR="$PREFIX/bin"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"

info() { printf '  · %s\n' "$*"; }
step() { printf '\n▶ %s\n' "$*"; }
die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

step "检查 Python 与 GTK4"
command -v python3 >/dev/null 2>&1 || die "没有找到 python3，请先安装 Python 3.10 以上版本"

python3 - <<'PY' || die "缺少 GTK4 / libadwaita 的 Python 绑定，请先安装（见 README 的「依赖」一节）"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 版本过低：{sys.version.split()[0]}")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: F401
print(f"  · Python {sys.version.split()[0]}，GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}，libadwaita {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}")
PY

step "安装启动器文件到 $LIB_DIR"
mkdir -p "$LIB_DIR"
rm -rf "$LIB_DIR/wb_launcher"
cp -r "$SCRIPT_DIR/wb_launcher" "$LIB_DIR/wb_launcher"
cp "$SCRIPT_DIR/workbuddy-launcher" "$LIB_DIR/workbuddy-launcher"
chmod +x "$LIB_DIR/workbuddy-launcher"
find "$LIB_DIR/wb_launcher" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
info "程序目录：$LIB_DIR"

step "创建命令 $BIN_DIR/workbuddy-launcher"
mkdir -p "$BIN_DIR"
ln -sf "$LIB_DIR/workbuddy-launcher" "$BIN_DIR/workbuddy-launcher"
info "命令行：$BIN_DIR/workbuddy-launcher"

step "安装桌面入口与图标"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
cp "$SCRIPT_DIR/wb_launcher/data/workbuddy2api-panel.svg" "$ICON_DIR/workbuddy2api-panel.svg"
# 旧版本用的是 workbuddy2api-launcher.desktop，先清掉避免出现两个入口
rm -f "$DESKTOP_DIR/workbuddy2api-launcher.desktop"
# .desktop 的 Exec 按 shell 规则拆词，路径里有空格必须加引号
EXEC_PATH="$BIN_DIR/workbuddy-launcher"
case "$EXEC_PATH" in
    *[[:space:]\"]*) EXEC_VALUE="\"$EXEC_PATH\"" ;;
    *) EXEC_VALUE="$EXEC_PATH" ;;
esac
cat > "$DESKTOP_DIR/$DESKTOP_ID" <<EOF
[Desktop Entry]
Type=Application
Name=WorkBuddy2API 面板
GenericName=CodeBuddy 账号池控制台
Comment=管理 CodeBuddy 账号池与 OpenAI 兼容网关
Exec=$EXEC_VALUE
Icon=workbuddy2api-panel
Terminal=false
Categories=Development;
Keywords=workbuddy;codebuddy;openai;api;gateway;panel;
StartupNotify=true
X-GNOME-UsesNotifications=true
StartupWMClass=io.github.workbuddy2api.launcher
EOF
chmod 644 "$DESKTOP_DIR/$DESKTOP_ID"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
info "桌面项：$DESKTOP_DIR/$DESKTOP_ID"

if [ "$WITH_EXTENSION" -eq 1 ]; then
    step "安装 GNOME 顶部栏扩展"
    EXT_DIR="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
    mkdir -p "$(dirname "$EXT_DIR")"
    rm -rf "$EXT_DIR"
    cp -r "$SCRIPT_DIR/wb_launcher/data/gnome-extension/$EXT_UUID" "$EXT_DIR"
    info "扩展目录：$EXT_DIR"
    if command -v gnome-extensions >/dev/null 2>&1; then
        if gnome-extensions enable "$EXT_UUID" >/dev/null 2>&1; then
            info "扩展已启用"
        else
            info "首次安装需要注销并重新登录，然后执行：gnome-extensions enable $EXT_UUID"
        fi
    else
        info "没找到 gnome-extensions 命令，请在「扩展」应用中手动启用"
    fi
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        printf '\n提示：%s 不在 PATH 里，可以把下面一行加到 ~/.bashrc 或 ~/.zshrc：\n  export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR"
        ;;
esac

cat <<EOF

✓ 启动器安装完成

  打开方式：应用菜单里的「WorkBuddy2API 面板」，或运行：$BIN_DIR/workbuddy-launcher
  首次打开会引导你下载并安装面板本体（不需要 root）。

  免责声明：本工具与腾讯 / CodeBuddy 官方无关，按「现状」提供；
  使用非官方网关可能导致账号被风控或封禁，风险自负。

EOF
