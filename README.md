# WorkBuddy2API 面板 · Linux 启动器

给 [`misakano7545/workbuddy2api-panel`](https://github.com/misakano7545/workbuddy2api-panel) 做的 Linux 桌面启动器：
一个 GTK4 / libadwaita 控制窗口，负责首次全自动安装、把面板交给 systemd 用户服务托管，
再配一个 GNOME 顶部栏扩展负责日常启停。

面板本体和 OpenAI 兼容网关是上游发布的同一个 Go 单文件程序（`wb2api`），
本项目**不含任何上游代码**，只负责下载、校验、配置和托管。

> ⚠️ **免责声明**：本项目与腾讯、CodeBuddy、WorkBuddy 官方无任何关联，也未获其
> 授权或认可。软件按「现状」提供，不附带任何担保；使用非官方网关可能导致账号被
> 限流、风控或封禁，风险由使用者自行承担。完整条款见
> [`DISCLAIMER.md`](DISCLAIMER.md)。

```
┌────────────────────────────┐        ┌──────────────────────────────┐
│  GTK4 启动器               │        │  systemd 用户服务             │
│  启动 / 停止 / 重启        │ ─────► │  workbuddy2api.service        │
│  端口 / 密钥 / 日志 / 更新 │        │  ~/.local/share/.../wb2api    │
└────────────────────────────┘        └──────────────┬───────────────┘
                                                     │ 127.0.0.1:7863
┌────────────────────────────┐                       ▼
│  GNOME 顶部栏扩展          │        ┌──────────────────────────────┐
│  状态 / 启停 / 打开面板    │ ─────► │  浏览器 http://…/panel/       │
└────────────────────────────┘        └──────────────────────────────┘
```

## 功能

| 功能 | 说明 |
| --- | --- |
| 首次自动安装 | 从上游 Release 下载对应架构的 Linux 包，核对 GitHub 给出的 SHA-256 摘要，解压出 `wb2api` 并生成最小配置 |
| 服务托管 | 写入 `~/.config/systemd/user/workbuddy2api.service`，默认随登录启动，崩溃自动重启 |
| 启动 / 停止 / 重启 | 图形界面按钮，或扩展菜单里的对应项 |
| 状态显示 | systemd 状态 + `/healthz`，显示监听端口、版本、账号数量 |
| 打开面板 | 一键用默认浏览器打开 `http://127.0.0.1:7863/panel/`，自动带上面板地址 |
| API 密钥 | 显示 / 隐藏 / 复制 `api_key`，首次打开面板时粘贴到密钥框 |
| 修改端口 | 图形界面改监听地址，保存后自动重启服务 |
| 开机自启 | 开关即 `systemctl --user enable/disable` |
| 日志 | 在启动器里直接看 `journalctl --user -u workbuddy2api.service` |
| 更新 | 检查最新 Release，替换二进制并重启；失败自动回滚到上一版本 |
| 下载加速 | 官方地址失败时自动改用 [GitProxy](https://gitproxy.dev/guide)（`https://api.gitproxy.dev/`） |
| 离线安装 | 网络完全不通时，可选本地 zip 安装包 |

启动器自己的界面语言是简体中文。安装过程不需要 root：面板装在
`~/.local/share/workbuddy2api-panel`，服务是当前用户的 systemd 用户单元。

## 依赖

- Linux + systemd 用户会话（`systemctl --user` 可用）
- Python ≥ 3.10
- GTK 4 + libadwaita 的 Python 绑定（PyGObject）

各发行版：

```bash
# Arch / EndeavourOS / Manjaro
sudo pacman -S python python-gobject gtk4 libadwaita

# Debian / Ubuntu
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1

# Fedora
sudo dnf install python3 python3-gobject gtk4 libadwaita
```

GNOME 扩展需要 GNOME Shell 45 及以上（本项目已在 GNOME Shell 51 上验证）。

## 安装启动器

```bash
git clone https://github.com/misakano7545/workbuddy2api-panel   # 或直接用本目录
cd workbuddytool
./install.sh                 # 装到 ~/.local
./install.sh --extension     # 同时安装 GNOME 顶部栏扩展
```

`install.sh` 会把程序放到 `~/.local/share/workbuddy2api-launcher`，命令链接到
`~/.local/bin/workbuddy-launcher`，并写入桌面项与图标。如果 `~/.local/bin`
不在 `PATH` 里，按脚本提示加一行即可。

也可以不安装、直接从源码目录运行：

```bash
./workbuddy-launcher
```

## 首次使用

1. 打开「WorkBuddy2API 面板」（应用菜单，或终端运行 `workbuddy-launcher`）。
2. 首次启动会显示安装页：确认安装目录、监听地址、端口、下载源，按需勾选
   「登录时自动启动」和「安装 GNOME 托盘扩展」。
3. 点「安装并启动」。下载、校验、解压、写配置、装服务、启动、等待端口就绪都是全自动的。
4. 主界面出现后点「打开面板」。面板需要 `api_key`：
   点「API 密钥」右边的复制按钮，粘贴到面板的密钥输入框即可（浏览器会记住）。

面板默认只监听 `127.0.0.1:7863`。改成 `0.0.0.0` 可以让局域网访问，
但面板只靠 `api_key` 保护，请自行评估风险。

### 从命令行安装

```bash
workbuddy-launcher install                                   # 最新版 + 默认端口
workbuddy-launcher install --port 9000                       # 换端口
workbuddy-launcher install --source mirror                   # 强制走 GitProxy
workbuddy-launcher install --local-zip ~/下载/wb2api-*.zip   # 离线安装
workbuddy-launcher install --no-autostart --no-start         # 只装，不设自启/不启动
```

命令行的 `install` / `update` 会读取和保存同一份启动器配置，
所以图形界面和命令行可以混用。

## 日常使用

图形界面：启动 / 停止 / 重启、打开面板、复制密钥、改端口、开关自启、
查看日志、检查更新、重新安装 / 修复、移除服务。

命令行：

```bash
workbuddy-launcher status        # 安装目录、版本、监听、服务状态、账号数
workbuddy-launcher open          # 用浏览器打开面板
workbuddy-launcher update        # 更新到最新版本
workbuddy-launcher extension     # 安装 / 更新 GNOME 扩展
workbuddy-launcher opencode-models  # 把网关的模型同步进 OpenCode 配置
workbuddy-launcher version       # 显示版本
workbuddy-launcher uninstall     # 停止并移除服务（保留账号数据）
workbuddy-launcher uninstall --purge   # 连安装目录一起删
```

systemd 侧：

```bash
systemctl --user status workbuddy2api.service
systemctl --user restart workbuddy2api.service
journalctl --user -u workbuddy2api.service -f     # 实时日志
```

## 接到 OpenCode

面板和网关是 OpenAI 兼容的，可以直接给 [OpenCode](https://opencode.ai) 之类的客户端用。
`opencode-models` 会从网关的 `/v1/models` 读真实清单，把 `providers.wb` 写进
`~/.config/opencode/opencode.jsonc`：

```bash
workbuddy-launcher opencode-models            # 写入并覆盖 wb 这一段
workbuddy-launcher opencode-models --dry-run  # 只打印，不落盘
workbuddy-launcher opencode-models --config ~/work/opencode.jsonc
```

- 每个模型都带上 `limit.context` / `limit.output`，数值来自网关，不是猜的；
  不写这两个字段客户端会按默认窗口算，1M 上下文的模型也会被提前截断。
- `capabilities` 按网关的 `supports_tool_call` / `supports_images` / `supports_reasoning` 生成。
- 只替换 `providers.wb`，其它提供商、`mcp`、注释都原样保留；重复执行结果不变。
- 想用别的 provider 名字加 `--provider-id mywb`，客户端里就是 `mywb/<模型>`。

写完重启 OpenCode，模型选择器里会出现 `WB` / `WB 国际` 开头的条目，
命令行也可以直接指定：

```bash
opencode run -m wb/cn:glm-5.3 "..."
```

网关必须开着（`workbuddy-launcher status` 确认）。上游加减模型后重跑一次即可。

## GNOME 顶部栏扩展

扩展 uuid 是 `workbuddy2api-panel@local`，它做三件事：

- 顶部栏图标显示服务状态（绿=运行、灰=停止、红=启动失败）；
- 菜单里显示端口 / 账号数，并提供启动、停止、重启；
- 「打开面板」用默认浏览器打开，图标「打开启动器」启动 GTK 界面。

扩展通过 `systemctl --user` 控制同一个服务，通过 `/healthz` 判断进程是否在监听，
并从面板的 `config.json` 读取端口，所以不需要额外的守护进程或控制端口。

安装（已经随启动器装过的话不用重复）：

```bash
workbuddy-launcher extension
# 或者
./install.sh --extension
```

Wayland 会话下新装的扩展需要**注销并重新登录**（或重启 GNOME Shell）才会加载；
之后运行 `gnome-extensions enable workbuddy2api-panel@local`（或到「扩展」应用里打开开关）。

## 文件位置

| 路径 | 内容 |
| --- | --- |
| `~/.local/share/workbuddy2api-launcher/` | 启动器程序（`install.sh` 安装后） |
| `~/.local/bin/workbuddy-launcher` | 启动器命令 |
| `~/.local/share/workbuddy2api-panel/` | 面板：`wb2api`、`config.json`、`auths/`、`data/` |
| `~/.config/workbuddy2api-launcher/config.json` | 启动器偏好（安装目录、下载源、窗口大小） |
| `~/.config/systemd/user/workbuddy2api.service` | systemd 用户单元 |
| `~/.local/share/applications/io.github.workbuddy2api.launcher.desktop` | 桌面项 |
| `~/.local/share/gnome-shell/extensions/workbuddy2api-panel@local/` | GNOME 扩展 |

面板的 `config.json` 只写必要字段（`listen`、`api_key`、`auth_dir`、
`state_file`、`update.auto_enabled`），其余配置由 `wb2api` 按内置默认值补齐。
账号保存在 `auths/`，运行数据在 `data/`，重装和更新都不会动它们。

## 下载与校验

- Release 信息来自官方 API：`https://api.github.com/repos/misakano7545/workbuddy2api-panel/releases/latest`
- 只接受 `*-linux-amd64.zip` / `*-linux-arm64.zip`（按 `uname -m` 自动选）
- 每个资产自带 `digest`（`sha256:...`），下载完必须一致才会安装
- 下载源三种：`auto`（官方优先，失败改用 GitProxy）、`official`、`mirror`
- 加速写法遵循 GitProxy 文档：把 `https://github.com/...` 换成
  `https://api.gitproxy.dev/github.com/...`，同一个文件、同样的摘要

安装包只从 zip 里取平铺的 `wb2api`；带目录、`..` 或符号链接的包会被直接拒绝。

## 故障排查

**状态显示「运行中（还没有账号）」、面板能开但 `/healthz` 返回 503**

正常现象。上游 `/healthz` 在没有任何可用账号时返回 503，启动器因此只判断
「端口有没有响应」。打开面板加账号后就会变成 200。

**服务起不来**

```bash
systemctl --user status workbuddy2api.service
journalctl --user -u workbuddy2api.service -n 100 --no-pager
```

常见原因：端口被占用（换一个端口）、安装目录被删、`systemctl --user` 不可用
（比如在 ssh 里没有 `XDG_RUNTIME_DIR`）。

**端口被占用**

```bash
ss -ltnp | grep 7863
```

然后在启动器里点「监听地址 → 修改」，或 `workbuddy-launcher install --port 新端口`。

**下载很慢或失败**

- 安装页把「下载源」改成「GitProxy 加速」，或命令行加 `--source mirror`；
- 仍然失败就手动下载 `wb2api-panel-<版本>-linux-amd64.zip`，用「本地安装包」安装；
- 公司网络需要代理时，先 `export HTTPS_PROXY=http://...` 再启动。

**GitHub API 返回 403**

未登录的 GitHub API 有速率限制，等一会儿再试；或者直接用本地安装包。

**扩展不显示 / 提示不存在**

Wayland 下必须注销重新登录；登录后：

```bash
gnome-extensions list | grep workbuddy
gnome-extensions enable workbuddy2api-panel@local
```

**想确认启动器本身没问题**

```bash
workbuddy-launcher status
systemd-analyze --user verify ~/.config/systemd/user/workbuddy2api.service
```

## 卸载

```bash
./uninstall.sh                 # 只卸启动器，保留面板与账号
./uninstall.sh --purge         # 连启动器配置一起删
./uninstall.sh --purge-panel   # 连面板服务、安装目录（账号数据）一起删
```

注意 `--purge-panel` 会删除 `auths/` 里的账号凭证，无法恢复。

## 开发

```bash
python3 -m unittest discover -s tests -v     # 全部本地测试，不联网
python3 -m compileall -q wb_launcher
```

测试覆盖：Release 资产选择与摘要校验、GitProxy 地址改写、跨文件系统安装、
zip 安全检查、systemd 单元生成与 `systemd-analyze` 校验、监听地址解析、
启动器配置往返、HTTP 探测（含 503 语义）、扩展元数据与 JS 语法。

代码结构：

| 文件 | 职责 |
| --- | --- |
| `wb_launcher/window.py` | 主界面、安装向导、更新 / 端口 / 日志对话框 |
| `wb_launcher/installer.py` | 安装、更新、回滚、systemd 单元、桌面项、扩展 |
| `wb_launcher/release.py` | Release 查询、架构选择、加速、下载与摘要 |
| `wb_launcher/payload.py` | 面板 `config.json` 的读写 |
| `wb_launcher/service.py` | `systemctl --user`、状态、日志 |
| `wb_launcher/settings.py` | 启动器自己的配置 |
| `wb_launcher/cli.py` | 命令行入口 |
| `wb_launcher/opencode.py` | 把网关模型清单写进 OpenCode 配置 |
| `wb_launcher/data/` | systemd 单元模板、桌面项模板、图标、GNOME 扩展 |

## 合规与安全

**上游与许可**

- 本仓库只包含启动器代码，不含上游任何源码，也不重新分发上游二进制；
  安装时从官方 Release 现下载、现校验。
- 上游 `misakano7545/workbuddy2api-panel` 使用 MIT 许可，版权链为
  Sliverkiss → linguo2625469 → misakano7545；本项目是独立实现，同样按 MIT 发布（见 `LICENSE`）。
- 上游的许可证与免责声明在其仓库根目录，分发本项目的二进制或安装包时请一并保留。
- 面板、网关、账号规则全部由上游决定，本项目只负责安装与托管。

**使用边界**

上游对自己的定位写得很清楚，这里原样转述：

> ⚠️ 合规须知：本项目是**非官方**网关，使用 CodeBuddy 账号作为上游，
> **仅限本人授权账号、本机 / 私有环境测试**。
>
> 风险自评：上游随时可能彻底收紧导致通道不可用；因使用非官方客户端导致的
> 账号风控 / 封禁，由使用者自行承担。

也就是说：只用自己的账号、只在本机或自己的私有环境里跑；不要拿它做对外服务、
不要收集或转卖他人的账号凭证。是否符合腾讯的服务条款，需要你自己判断并承担后果。

**数据落在哪**

| 数据 | 位置 | 权限 |
| --- | --- | --- |
| 账号凭证 | `~/.local/share/workbuddy2api-panel/auths/` | 目录 `0700`，文件 `0600` |
| 网关配置（含 `api_key`） | `~/.local/share/workbuddy2api-panel/config.json` | `0600` |
| 运行数据 | `~/.local/share/workbuddy2api-panel/data/` | 目录 `0700` |
| OpenCode provider 配置 | `~/.config/opencode/opencode.jsonc` | `0600` |

启动器不收集遥测、不上报任何数据；除了下载 Release 和本机网关，没有别的网络请求。

**服务加固**

systemd 用户单元默认带上：`NoNewPrivileges`、`ProtectSystem=full`、
`ProtectHome=read-only`、`ReadWritePaths=<安装目录>`、`UMask=0077`、
`ProtectKernelTunables/Modules/ControlGroups/Clock/Hostname/KernelLogs`、
`ProtectProc=invisible`、`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`、
`RestrictRealtime`、`RestrictSUIDSGID`、`LockPersonality`、`RestrictNamespaces`。
`systemd-analyze security --user` 评分 5.9（MEDIUM）；剩下的大头是系统调用过滤，
加上去会牺牲兼容性，这里刻意没做。

**已知边界**

- 面板默认只监听 `127.0.0.1`。改成 `0.0.0.0` 后只有 `api_key` 一层保护，
  而且走明文 HTTP，局域网内可被嗅探，别在不可信网络里这么用。
- 下载只校验 Release 资产自带的 SHA-256 摘要，没有 GPG 签名；威胁模型里
  GitHub API 与下载源是同一个信任根。
- 启动器自身只做本机操作，不联网控制远端主机。

## 免责声明

完整条款见 [`DISCLAIMER.md`](DISCLAIMER.md)，与 [`LICENSE`](LICENSE) 共同适用。要点：

- 第三方独立工具，与腾讯、CodeBuddy、WorkBuddy 官方无隶属、合作或背书关系，
  未获其授权或认可；相关商标仅作指示性使用，归权利人所有。
- 按「现状」（AS IS）提供，不作任何明示或暗示的担保，也无技术支持或维护义务。
- 使用非官方接入可能导致账号被限流、风控、冻结、封禁，或积分、权益、数据丢失，
  全部后果由你自行承担。
- 仅限你本人拥有或已获明确授权的账号；须遵守被管理服务的条款与当地法律法规；
  不得转售、代充、共享账号池或对外提供接口。
- 在适用法律允许的最大范围内，作者不承担任何直接或间接责任；就本软件承担的
  责任总额以你实际支付的金额为限（免费获取时为零）。依法不可排除的责任不受此限。
- 因你违反上述限制导致第三方索赔的，你应负责抗辩并使作者免责。

下载、安装或使用本软件，即视为你已阅读并同意上述条款；不同意请停止使用并删除。
