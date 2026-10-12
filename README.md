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

### 2. 配置 Clash Verge 外部控制
确保 Clash Verge 开启了外部控制（通常默认开启）：
- 打开 Clash Verge **「设置 (Settings)」**
- 确认外部控制端口为 `9097`（即 `127.0.0.1:9097`）
- 确认密钥（Secret）为 `123456`（若为其他值，见下方自定义配置说明）

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
双击运行根目录下的 [`stop_monitor.bat`](stop_monitor.bat) 即可一键安全停止所有后台运行的 NetMonitor 进程与悬浮窗。

#### 方式 E：一键创建桌面快捷方式
- 双击运行根目录下的 [`create_desktop_shortcut.bat`](create_desktop_shortcut.bat)，即可在桌面同时生成：
  - `NetMonitor 悬浮窗.lnk`（桌面极简悬浮小组件 APP）
  - `NetMonitor.lnk`（完整 Web 分析中心）

启动成功后，浏览器随时访问：👉 **http://127.0.0.1:8899**

---

## ⚙️ 进阶配置与环境变量

NetMonitor 支持通过系统环境变量无缝覆盖默认配置，无需修改源代码：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `CLASH_API_URL` | Clash Verge 内核外部控制器 API 地址 | `http://127.0.0.1:9097` |
| `CLASH_SECRET` | Clash Verge 外部控制密钥 (Secret) | `123456` |
| `WEB_PORT` | NetMonitor 本地 Web 监控页面端口 | `8899` |
| `POLL_INTERVAL` | 活跃连接轮询间隔（秒） | `1.0` |
| `DB_FILE` | 流量历史 SQLite 数据库存储文件路径 | `E:\tmp\traffic_history.db` |
| `HIDE_CONSOLE` | 是否在 Windows 下自动隐藏控制台黑框（1: 隐藏, 0: 显示） | `1` |

示例（PowerShell）：
```powershell
$env:CLASH_API_URL="http://127.0.0.1:9090"
$env:CLASH_SECRET="my_custom_secret"
$env:WEB_PORT="8900"
$env:DB_FILE="E:\tmp\traffic_history.db"
python monitor.py
```

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
├── .gitignore               # Git 忽略配置（自动排除大体积 SQLite 数据库及缓存）
├── LICENSE                  # 开源协议 (MIT License)
├── README.md                # 项目使用说明文档
├── monitor.py               # 核心后端监控服务、API 轮询与内嵌 Web 仪表盘
├── start_monitor.bat        # Windows 启动脚本（自动隐藏命令行黑框）
├── start_monitor_silent.vbs # Windows 纯静默无黑框启动器
└── stop_monitor.bat         # Windows 一键停止后台监控脚本
```

---

## 📄 开源许可

本项目采用 [MIT License](LICENSE) 开源许可。
