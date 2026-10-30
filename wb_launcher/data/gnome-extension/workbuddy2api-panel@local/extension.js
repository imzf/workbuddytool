// WorkBuddy2API 面板 · GNOME Shell 顶部栏指示器
//
// 职责只有三件：显示服务是否可用、给出一键启停/重启、打开面板或启动器。
// 服务由 systemd 用户单元 workbuddy2api.service 托管（与 GTK 启动器一致），
// 因此这里直接调用 systemctl --user，不引入额外的守护进程或控制端口。

import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Soup from 'gi://Soup?version=3.0';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const UNIT = 'workbuddy2api.service';
const DESKTOP_ID = 'io.github.workbuddy2api.launcher.desktop';
const UUID = 'workbuddy2api-panel@local';
const DEFAULT_PORT = 7863;
const POLL_SECONDS = 5;

function readJson(path) {
    try {
        const [ok, contents] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        return JSON.parse(new TextDecoder().decode(contents));
    } catch (e) {
        return null;
    }
}

function panelPaths() {
    const configHome = GLib.get_user_config_dir();
    const dataHome = GLib.get_user_data_dir();
    const launcher = readJson(GLib.build_filenamev([configHome, 'workbuddy2api-launcher', 'config.json']));
    const appDir = launcher?.app_dir || GLib.build_filenamev([dataHome, 'workbuddy2api-panel']);
    return {
        config: GLib.build_filenamev([appDir, 'config.json']),
    };
}

function parseListen(value) {
    const text = String(value || '').trim();
    const bracket = /^\[(.+)\]:(\d+)$/.exec(text);
    if (bracket)
        return {host: bracket[1], port: Number(bracket[2])};
    const plain = /^(.*):(\d+)$/.exec(text);
    if (plain)
        return {host: plain[1] || '127.0.0.1', port: Number(plain[2])};
    return {host: '127.0.0.1', port: DEFAULT_PORT};
}

function localHealthHost(host) {
    if (!host || host === '0.0.0.0' || host === '::')
        return '127.0.0.1';
    return host.includes(':') ? `[${host}]` : host;
}

function spawn(cmd) {
    try {
        return Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
    } catch (e) {
        return null;
    }
}

const Indicator = GObject.registerClass(
class Indicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'WorkBuddy2API 面板', false);

        this._session = new Soup.Session({timeout: 3});
        this._busy = false;
        this._healthy = false;

        this._icon = new St.Icon({
            icon_name: 'network-server-symbolic',
            style_class: 'system-status-icon workbuddy-off',
        });
        this.add_child(this._icon);
        // GNOME Shell 的 St.Widget 没有 tooltip API，悬浮提示只能用无障碍名称，
        // 具体状态放在下拉菜单的第一行。
        this.set_accessible_name('WorkBuddy2API 面板：正在检查');

        this._statusItem = new PopupMenu.PopupMenuItem('状态：检查中…', {reactive: false});
        this._detailItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._startItem = new PopupMenu.PopupMenuItem('启动服务');
        this._stopItem = new PopupMenu.PopupMenuItem('停止服务');
        this._restartItem = new PopupMenu.PopupMenuItem('重启服务');
        this._openItem = new PopupMenu.PopupMenuItem('打开面板');
        this._launcherItem = new PopupMenu.PopupMenuItem('打开启动器');

        this.menu.addMenuItem(this._statusItem);
        this.menu.addMenuItem(this._detailItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addMenuItem(this._startItem);
        this.menu.addMenuItem(this._stopItem);
        this.menu.addMenuItem(this._restartItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addMenuItem(this._openItem);
        this.menu.addMenuItem(this._launcherItem);

        this._startItem.connect('activate', () => this._action(['systemctl', '--user', 'start', UNIT], '正在启动…'));
        this._stopItem.connect('activate', () => this._action(['systemctl', '--user', 'stop', UNIT], '正在停止…'));
        this._restartItem.connect('activate', () => this._action(['systemctl', '--user', 'restart', UNIT], '正在重启…'));
        this._openItem.connect('activate', () => this._openPanel());
        this._launcherItem.connect('activate', () => this._openLauncher());

        this._timeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, POLL_SECONDS, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._refresh();
    }

    _endpoint() {
        const config = readJson(panelPaths().config);
        const listen = parseListen(config?.listen);
        return {
            host: localHealthHost(listen.host),
            port: listen.port,
        };
    }

    _panelUrl() {
        const {host, port} = this._endpoint();
        return `http://${host}:${port}/panel/`;
    }

    _refresh() {
        if (this._busy)
            return;
        const {host, port} = this._endpoint();
        const message = Soup.Message.new('GET', `http://${host}:${port}/healthz`);
        if (!message) {
            this._apply(false, '地址无效');
            return;
        }
        this._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (session, result) => {
            // 上游 /healthz 在「一个账号都没有」时返回 503，但进程是活的；
            // 所以这里只判断「服务器有没有回话」，不看具体状态码。
            let text = '';
            let reachable = false;
            try {
                const bytes = session.send_and_read_finish(result);
                reachable = message.get_status() > 0;
                if (reachable)
                    text = new TextDecoder().decode(bytes.get_data());
            } catch (e) {
                reachable = false;
            }
            if (!reachable) {
                this._queryUnit(port);
                return;
            }
            let accounts = null;
            try {
                const data = JSON.parse(text);
                if (typeof data.total === 'number')
                    accounts = data.total;
            } catch (e) {
                accounts = null;
            }
            const suffix = accounts === null ? '' : ` · 账号 ${accounts}`;
            this._apply(true, `运行中 · 端口 ${port}${suffix}`);
        });
    }

    _queryUnit(port) {
        let proc;
        try {
            proc = Gio.Subprocess.new(
                ['systemctl', '--user', 'show', '-p', 'ActiveState', '--value', UNIT],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch (e) {
            this._apply(false, '无法调用 systemctl');
            return;
        }
        proc.communicate_utf8_async(null, null, (process, result) => {
            let state = '';
            try {
                const [, out] = process.communicate_utf8_finish(result);
                state = (out || '').trim();
            } catch (e) {
                state = '';
            }
            if (state === 'activating' || state === 'reloading')
                this._apply(false, `启动中 · 端口 ${port}`, 'starting');
            else if (state === 'failed')
                this._apply(false, '服务启动失败', 'failed');
            else
                this._apply(false, '服务已停止', 'stopped');
        });
    }

    _apply(healthy, detail, forcedState) {
        this._healthy = healthy;
        const state = forcedState || (healthy ? 'running' : 'stopped');

        this._statusItem.label.text = healthy ? '状态：运行中' : `状态：${detail}`;
        this._detailItem.label.text = healthy ? detail : `端口 ${this._endpoint().port}`;
        this._detailItem.visible = Boolean(healthy);

        this._icon.remove_style_class_name('workbuddy-ok');
        this._icon.remove_style_class_name('workbuddy-off');
        this._icon.remove_style_class_name('workbuddy-err');
        if (healthy)
            this._icon.add_style_class_name('workbuddy-ok');
        else if (state === 'failed')
            this._icon.add_style_class_name('workbuddy-err');
        else
            this._icon.add_style_class_name('workbuddy-off');

        this.set_accessible_name(healthy ? `WorkBuddy2API 面板，运行中，${detail}` : `WorkBuddy2API 面板，${detail}`);
        this._startItem.setSensitive(!healthy && state !== 'starting');
        this._stopItem.setSensitive(healthy || state === 'starting');
    }

    _action(cmd, pendingText) {
        this._busy = true;
        this._statusItem.label.text = `状态：${pendingText}`;
        const proc = spawn(cmd);
        if (!proc) {
            this._busy = false;
            Main.notify('WorkBuddy2API 面板', '无法执行 systemctl');
            return;
        }
        proc.wait_check_async(null, (process, result) => {
            let ok = false;
            try {
                ok = process.wait_check_finish(result);
            } catch (e) {
                ok = false;
            }
            this._busy = false;
            if (!ok)
                Main.notify('WorkBuddy2API 面板', '操作失败，请打开启动器查看日志');
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                this._refresh();
                return GLib.SOURCE_REMOVE;
            });
        });
    }

    _openPanel() {
        try {
            Gio.AppInfo.launch_default_for_uri(this._panelUrl(), null);
        } catch (e) {
            Main.notify('WorkBuddy2API 面板', `无法打开浏览器：${e.message}`);
        }
    }

    _openLauncher() {
        // 桌面项名字必须和 GApplication 的 application_id 一致，旧的也认一下
        for (const id of [DESKTOP_ID, 'workbuddy2api-launcher.desktop']) {
            try {
                const info = Gio.DesktopAppInfo.new(id);
                if (info) {
                    info.launch([], null);
                    return;
                }
            } catch (e) {
                // 继续试下一个
            }
        }
        // 扩展的 PATH 是登录时继承的，~/.local/bin 不一定在里面，直接给绝对路径
        const home = GLib.get_home_dir();
        const fallback = GLib.build_filenamev([home, '.local', 'bin', 'workbuddy-launcher']);
        const proc = spawn([fallback]);
        if (!proc)
            Main.notify('WorkBuddy2API 面板', '未找到启动器，请先运行 install.sh');
    }

    destroy() {
        if (this._timeout) {
            GLib.source_remove(this._timeout);
            this._timeout = 0;
        }
        try {
            this._session.abort();
        } catch (e) {
            // 忽略：会话可能已经结束
        }
        super.destroy();
    }
});

export default class WorkBuddyPanelExtension extends Extension {
    enable() {
        this._indicator = new Indicator();
        Main.panel.addToStatusArea(UUID, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
