# 🌐 NetMonitor

> **Clash Verge (Rev) / Mihomo TUN 模式下真实进程网络流量精准监控与历史归因工具**
>
> 解决 TUN 模式下 Windows 任务管理器流量全部归属于 `verge-mihomo.exe`、无法定位真实偷跑流量程序的问题。零第三方依赖、开箱即用。

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078d7.svg)](https://www.microsoft.com/windows)
[![Kernel](https://img.shields.io/badge/Kernel-Mihomo%20%2F%20Clash%20Meta-orange.svg)](https://github.com/MetaCubeX/mihomo)

---

## 📌 背景痛点

在使用 **Clash Verge / Clash Verge Rev** 并开启 **TUN 模式** 时：
- 系统通过虚拟网卡（L3 隧道）截获全机网络流量并转交给内核 `verge-mihomo.exe` 处理。
- **Windows 任务管理器**的流量归因机制失效：所有外网请求在任务管理器中 100% 显示为 `verge-mihomo.exe`。
- 当出现几百 MB 甚至数十 GB 异常流量消耗时，用户根本无法获知到底是 Steam 下载、网盘同步、系统更新、还是流氓后台程序在偷跑流量。

**NetMonitor** 通过直接对接 Mihomo 内核的外部控制 API，实时捕获内核建立套接字时记录的底层真实进程信息（进程名、程序完整执行路径、目标主机、连接时长等），并以毫秒级聚合持久化到本地 SQLite 数据库，提供直观易用的 Web 仪表盘。

---

## ✨ 核心特性

- 🪶 **零第三方依赖（Zero-Dependency）**：
  基于 Python 3 原生标准库（`http.server`, `sqlite3`, `urllib` 等）编写，无需 `pip install` 任何额外扩展包，双击即跑。
- ⚡ **毫秒级实时网速监控**：
  实时计算全机及各活跃进程的实时上传/下载速率（KB/s、MB/s），秒级刷新，流量动态一目了然。
- 📅 **每日全量历史与归因下钻**：
  - 自动按天统计全天总流量、下行下载、上行上传、总连接数及当日“流量冠军”程序。
  - 支持下钻查看任意历史日期，查看该日所有消耗流量进程的完整排名、占比与累计使用量。
- 🎯 **目标域名与服务器穿透**：
  记录每个程序最常访问的服务器域名和目标 IP，明确流量去向，轻松识别异常流氓连接。
- 🚨 **大流量直观预警**：
  UI 自动标记超大流量应用（> 1 GB 标红警示，> 300 MB 醒目标注）。
- 🔍 **便捷检索与多维排序**：
  支持通过程序名称、文件路径关键词快速模糊过滤，支持按总流量、下载量、上传量自由排序。
- 🖥️ **桌面极简置顶悬浮窗 & 原生 APP 详情面板（Mini Desktop Widget & APP）**：
  - 独立的 Windows 原生桌面应用（纯 Python 原生 GUI，非网页），悬浮窗常驻桌面最前端（Always on Top）。
  - **极简胶囊悬浮窗**：仅显示**当前下载速率 (`↓`)**、**当前上传速率 (`↑`)** 以及 **当日累计总流量 (`今日`)**。
  - **原生 APP 详情面板**：双击悬浮窗即可即刻唤起**原生深色详情窗口**（展示当前各进程实时网速排行、今日各程序流量消耗 TOP 50、各程序流量占比等），支持锁定置顶、关闭面板不影响悬浮窗。
  - 鼠标左键按住任意拖动位置并自动记忆坐标；防手抖微移保护；支持锁定防误触；支持单行横条 / 双行卡片双布局；支持调节透明度与鼠标悬停详细数据 Tooltip。
  - 右键菜单提供一键打开 APP 详情面板、网页分析仪表盘、样式切换、透明度调节及一键完全退出。
- 📊 **现代化暗色 Web 仪表盘**：
  内置单文件响应式前端界面（访问 `http://127.0.0.1:8899`），支持暗黑质感设计、历史柱状图趋势展示。

---

## 🚀 快速上手

### 1. 环境准备
- 操作系统：Windows 10 / 11
- Python 版本：Python 3.8 或更高版本
- 客户端：运行中的 Clash Verge / Clash Verge Rev（已开启 TUN 模式）

### 2. 开启 Clash Verge 外部控制
NetMonitor 通过 Mihomo 内核的外部控制 API 读取连接信息：
- 打开 Clash Verge **「设置」**，打开 **「外部控制器」** 开关（新版 Clash Verge Rev 默认关闭）。
- 建议监听地址保持 `127.0.0.1:端口`，并设置一个**足够长的随机密钥**。外部控制器可以切换节点、修改规则，不要使用 `123456` 这类弱密钥，也不要监听 `0.0.0.0`。
- **无需在 NetMonitor 里重复填写**：程序会自动读取 Clash Verge 配置文件（`%APPDATA%\io.github.clash-verge-rev.clash-verge-rev\config.yaml` 等）中的地址与密钥；修改密钥后约 15 秒内自动生效。
- 使用其他客户端或自动读取失败时，用环境变量 `CLASH_API_URL` / `CLASH_SECRET` 指定（见下方配置）。

连接失败时，看板右上角与悬浮窗详情面板会直接显示原因：「Clash 密钥错误」「外部控制未开启」「Clash 未连接」等。

### 3. 启动与停止运行

#### 方式 A：启动桌面极简置顶悬浮窗 & APP 详情面板（强烈推荐日常使用）
- **直接双击桌面上的快捷方式「`NetMonitor 悬浮窗.lnk`」**，或运行根目录下的 [`start_mini.bat`](start_mini.bat) / [`start_mini_silent.vbs`](start_mini_silent.vbs)。
- 悬浮窗将直接以 **原生桌面 APP 形式** 浮现在 Windows 桌面最前端：
  - 显示：**当前实时下载速率**、**当前实时上传速率**、**当日累计总流量**。
  - **双击交互**：双击悬浮窗（或双击桌面图标）即可立即弹出**原生桌面 APP 监控中心**（展示进程网速排行与今日消耗 TOP 50）。
  - **位置拖拽**：鼠标左键按住悬浮窗任意位置即可移动，松开自动保存记忆坐标。
  - **右键菜单**：随时打开 APP 详情面板、切换单行/双行布局、调节透明度、锁定位置、开启/关闭置顶。
  - 若后台服务尚未启动，迷你悬浮窗将自动在后台静默拉起数据采集，开箱即用。

#### 方式 B：双击批处理同时启动监控与网页（传统方式）
直接双击运行根目录下的 [`start_monitor.bat`](start_monitor.bat)。它会自动拉起后台监控与悬浮窗，并在默认浏览器打开仪表盘。

#### 方式 C：纯静默启动
双击运行 [`start_monitor_silent.vbs`](start_monitor_silent.vbs)，无任何窗口闪烁，后台静默拉起监控与悬浮窗并打开浏览器。

#### 方式 D：终端命令行启动
```powershell
# 仅启动桌面悬浮窗 APP
python mini_widget.py
# 或通过主程序参数启动
python monitor.py --mini

# 后台静默启动主服务（自动隐藏控制台黑框）
python monitor.py

# 调试模式（保持命令行窗口显示与实时日志）
python monitor.py --show

# 停止后台监控服务及悬浮窗
python monitor.py --stop
```

#### 停止服务
双击运行根目录下的 [`stop_monitor.bat`](stop_monitor.bat)（或 `python monitor.py --stop`）即可停止 NetMonitor 后台服务与悬浮窗。服务会先写完缓存数据再退出；只会结束本程序自己的进程（按 PID 文件并核对脚本完整路径），不会影响其他 Python 程序。

#### 方式 E：一键创建桌面快捷方式
- 双击运行根目录下的 [`create_desktop_shortcut.bat`](create_desktop_shortcut.bat)，即可在桌面同时生成：
  - `NetMonitor 悬浮窗.lnk`（桌面极简悬浮小组件 APP）
  - `NetMonitor.lnk`（完整 Web 分析中心）

启动成功后，浏览器随时访问：👉 **http://127.0.0.1:8899**（端口可用 `WEB_PORT` 修改，所有启动脚本与悬浮窗会自动跟随），或运行 `python monitor.py --open`。

#### 开机自启
双击 [`enable_autostart.bat`](enable_autostart.bat)（等同 `python monitor.py --autostart`）开启，登录 Windows 时静默启动后台服务与悬浮窗；[`disable_autostart.bat`](disable_autostart.bat) 取消。

---

## ⚙️ 进阶配置与环境变量

NetMonitor 支持通过系统环境变量覆盖默认配置，无需修改源代码：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `CLASH_API_URL` | Clash / Mihomo 外部控制器地址 | 自动读取 Clash Verge 配置，否则 `http://127.0.0.1:9097` |
| `CLASH_SECRET` | 外部控制密钥 | 自动读取 Clash Verge 配置，否则为空 |
| `WEB_PORT` | 本地 Web 看板端口（启动脚本与悬浮窗共用） | `8899` |
| `NETMONITOR_IPC_PORT` | 悬浮窗单实例与唤醒使用的本地端口 | `8897` |
| `POLL_INTERVAL` | 活跃连接轮询间隔（秒，最小 0.2） | `1.0` |
| `NETMONITOR_DATA_DIR` | 数据目录：数据库、配置、日志、PID 文件 | `%LOCALAPPDATA%\NetMonitor` |
| `DB_FILE` | 数据库文件路径 | 数据目录下的 `traffic_history.db`（见下方说明） |
| `HOURLY_RETENTION_DAYS` | 小时粒度明细保留天数（看板只用天粒度汇总），`0` 为永久 | `30` |
| `RETENTION_DAYS` | 天粒度汇总保留天数，`0` 为永久 | `0` |
| `HIDE_CONSOLE` | Windows 下是否隐藏控制台窗口（1: 隐藏, 0: 显示） | `1` |

示例（PowerShell）：
```powershell
$env:CLASH_API_URL="http://127.0.0.1:9090"
$env:CLASH_SECRET="一段足够长的随机密钥"
$env:WEB_PORT="8900"
python monitor.py
```

### 数据位置与升级说明
- 新安装默认把数据放在 `%LOCALAPPDATA%\NetMonitor\`（数据库 `traffic_history.db`、悬浮窗配置 `mini_config.json`、日志 `monitor.log` / `mini_widget.log`）。
- **从旧版本升级**：如果旧默认位置 `E:\tmp\traffic_history.db` 或程序目录下已有数据库，会继续使用它，历史数据不丢；程序目录下的旧 `mini_config.json` 会自动复制到数据目录。
- 首次启动新版本会自动升级数据库结构（生成天粒度汇总、删除从未使用的 `completed_connections` 表），一年左右的数据约需数秒。升级后可运行 `python monitor.py --cleanup 天数` 删除更早的数据并压缩数据库文件。
- 数据库打不开时会弹窗说明原因，不再“双击没反应”；运行日志见 `monitor.log`。

### 统计口径
Mihomo 只提供“当前活跃连接”列表，轮询间隔内建立又关闭的连接无法对应到具体进程。NetMonitor 用内核的全局流量计数校准总量，这部分流量计入 **「未归属（短连接）」**，因此每日总流量是准确的，按进程的排行是采样统计。缩短 `POLL_INTERVAL` 可以减少未归属部分，但会增加 CPU 占用。

### 隐私与安全
记录的数据相当于上网轨迹（访问过的域名、程序路径、代理节点），只保存在本机。Web 服务只监听 `127.0.0.1`，并且不允许其他网站跨站读取接口、拒绝非本机 Host 头（防 DNS 重绑定）、有副作用的操作只接受本页面发出的请求；看板中所有来自数据的文字（含订阅里的节点名）都会转义后显示。

---

## 💡 附：如何追溯昨天之前已经产生的历史流量？

若在安装 NetMonitor 之前已经产生未知大流量，可使用 Windows 系统自带底层计量功能排查：

1. **Windows 设置 - 流量使用情况**（受底层 NDIS 驱动记录，不受 TUN 虚拟网卡影响）：
   - 按快捷键 `Win + I` 打开系统设置。
   - 依次进入 **「网络和 Internet」** ➔ **「高级网络设置」** ➔ **「流量使用情况 (Data Usage)」**。
   - 在右上角切换至当前的 **「WLAN / 有线以太网」**。
   - 系统将清晰呈现过去 30 天各应用程序（Steam、云盘、浏览器等）的真实累计流量排行榜。

2. **任务管理器 - 应用历史记录**：
   - 按 `Ctrl + Shift + Esc` 打开任务管理器。
   - 切换至 **「应用历史记录 (App history)」** 标签页。
   - 点击 **「网络」** 列头按消耗从大到小排序。

---

## 📁 项目结构

```text
NetMonitor/
├── monitor.py               # 命令行入口：启动/停止服务、打开看板、开机自启、数据清理
├── mini_widget.py           # 桌面悬浮窗与原生详情面板（Tk）
├── nm_config.py             # 共享配置：端口、数据目录、Clash 外部控制器自动识别
├── collector.py             # 流量采集：轮询 /connections、总量校准、写库重试
├── storage.py               # SQLite：建表与迁移、写入、数据保留、看板查询
├── web.py                   # 本地 Web 服务与 JSON 接口（含安全校验）
├── nm_windows.py            # 进程管理、开机自启、桌面快捷方式、错误弹窗
├── static/                  # Web 看板页面与图标
├── tests/                   # 单元测试（标准库 unittest + 假 Mihomo 接口）
├── create_shortcuts.py      # 创建桌面快捷方式
├── *.bat / *.vbs            # Windows 启动、停止、自启脚本
└── .github/workflows/       # CI：Windows / Linux × Python 3.8 / 3.12
```

## 🧪 开发与测试

```powershell
python -m unittest discover -s tests -t .
```
测试只依赖标准库，使用临时数据目录和一个可编程的假 Mihomo 接口，不会读写你的真实数据。

---

## 📄 开源许可

本项目采用 [MIT License](LICENSE) 开源许可。
