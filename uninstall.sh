#!/usr/bin/env bash
# WorkBuddy2API 面板启动器 · 卸载脚本
#
# 默认只移除启动器（界面、命令、桌面项），保留面板本体与账号数据。
#
# 用法：
#   ./uninstall.sh                 # 只卸载启动器
#   ./uninstall.sh --purge         # 连启动器配置一起删
#   ./uninstall.sh --purge-panel   # 连面板服务与安装目录（账号数据）一起删

set -euo pipefail

APP_ID="workbuddy2api-launcher"
EXT_UUID="workbuddy2api-panel@local"
PREFIX="${PREFIX:-$HOME/.local}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
LIB_DIR="$PREFIX/share/$APP_ID"
BIN_DIR="$PREFIX/bin"
PURGE=0
PURGE_PANEL=0

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --purge-panel) PURGE_PANEL=1; PURGE=1 ;;
        -h|--help)
            sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数：$arg（支持 --purge / --purge-panel）" >&2
            exit 2
            ;;
    esac
done

info() { printf '  · %s\n' "$*"; }

if [ "$PURGE_PANEL" -eq 1 ]; then
    if [ -x "$LIB_DIR/workbuddy-launcher" ]; then
        printf '\n▶ 移除面板服务与安装目录\n'
        "$LIB_DIR/workbuddy-launcher" uninstall --purge || true
    fi
fi

printf '\n▶ 移除启动器命令与文件\n'
rm -f "$BIN_DIR/workbuddy-launcher"
rm -rf "$LIB_DIR"
info "已删除 $BIN_DIR/workbuddy-launcher 与 $LIB_DIR"

printf '\n▶ 移除桌面入口与图标\n'
rm -f "$DATA_HOME/applications/workbuddy2api-launcher.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/workbuddy2api-panel.svg"
info "已删除桌面项与图标"

EXT_DIR="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
if [ -d "$EXT_DIR" ]; then
    printf '\n▶ 移除 GNOME 扩展\n'
    command -v gnome-extensions >/dev/null 2>&1 && gnome-extensions disable "$EXT_UUID" >/dev/null 2>&1 || true
    rm -rf "$EXT_DIR"
    info "已删除 $EXT_DIR（注销后彻底消失）"
fi

if [ "$PURGE" -eq 1 ]; then
    printf '\n▶ 移除启动器配置\n'
    rm -rf "$CONFIG_HOME/workbuddy2api-launcher"
    info "已删除 $CONFIG_HOME/workbuddy2api-launcher"
fi

cat <<EOF

✓ 卸载完成

  面板安装目录 ~/.local/share/workbuddy2api-panel 与账号数据$([ "$PURGE_PANEL" -eq 1 ] && echo "已一并删除" || echo "仍保留，如需清除请加 --purge-panel")

EOF
