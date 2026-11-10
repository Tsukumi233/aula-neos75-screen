# AULA Neos75 屏幕

让 AULA Neos75（Selene 枪灰）键盘上那块 480×360 小屏，显示电脑实时渲染的页面：时钟、系统负载、正在播放、
真实音频频谱、磁带、辉光管、你自己的图片或任何网页。转动屏幕旋钮，我们的页面和原厂 6 页排在同一个循环里
（原厂第 6 页之后是 Page 7、8、9…），体验和原厂页面一样。个人使用，不公开、不商用。

![桌面应用](app/screenshots/01-main.png)

## 快速开始

1. **安装（一次）**：右键 `setup.ps1` →「使用 PowerShell 运行」。它只在本文件夹里安装：Python 环境
   `sdk\.venv`、小组件依赖、官方屏幕素材 `design\official`（从 Aula Hub 下载）、桌面应用依赖和本地 MiSans
   字体。可以反复运行，用来修复。不改系统设置，不刷固件。
2. **启动**：双击 `Neos75.cmd`。桌面应用会自己启动屏幕服务、连上键盘屏幕，开始显示你选的页面。
   在应用的「设置」里可以打开开机启动和托盘。

   **或者装成普通 Windows 应用**：`sdk\.venv\Scripts\python tools\build_app.py` 生成
   `app\dist\Neos75Screen-Setup-<版本>.exe`。双击安装：装到当前用户（不需要管理员），有开始菜单和桌面
   快捷方式、卸载项；自带 Python 和页面素材，不再依赖本文件夹（只有你在 `design\custom` 写的新页面仍从这里
   读取）。安装包含官方素材，只给自己用，不要提交或发布。详见 [app/README.md](app/README.md#安装包)。
3. **用**：转屏幕旋钮，原厂第 6 页之后就是我们的页面；在应用里拖动排序、添加、删除页面，约 1 秒上屏，
   不用重启，不用刷固件。
4. **键盘本身**（代替 Aula Hub）：左侧「键盘」一组里改按键、旋钮、宏、高级键、灯光、性能，直接写进键盘。
   旋钮的每个动作也可以交给电脑端（打开程序、快捷键、按住转动……）；默认全是原厂功能，不拦截任何东西。

前提：键盘屏幕已经是补丁固件（见下文「固件」）。原厂固件上应用也能打开，但不能显示实时页面。

## 页面

页面就是 480×360 的网页，放进页面库、在应用或命令行里选上（最多 8 页，Page 7–14）就会出现在旋钮循环里。

| 页面 | 内容 |
|---|---|
| `neos_clock` / `neos_system` / `neos_player` | 原厂第 3 / 1 / 4 页底图上的时钟、CPU/GPU/内存、正在播放 |
| `neos_hifi_clock` | 铬圈表盘，红色秒针，日期窗 |
| `neos_hifi_meters` | CPU / GPU 指针 VU 表，内存、温度、磁盘、网速推子 |
| `neos_hifi_player` | 正在播放 + 真实 24 段音频频谱（系统声音的环回 FFT，约 13–17 帧/秒） |
| `neos_nixie` | 千禧年风格的段码时钟，在原厂第 2 页的玻璃胶囊里发银蓝色辉光 |
| `neos_cassette` | 枪灰色磁带，原厂铬环做卷轴，播放时转动，带量跟随进度 |
| `neos_photo?src=…` | 你的图片（像原厂第 5 页的自定义图片；`full=1` 全屏） |

所有页面都保留原厂状态栏的电量、连接方式（有状态栏的底图上）和页码。写自己的页面：
[`design/neos/README.md`](design/neos/README.md)；样式沿用官方 Néos 素材和 MiSans。

命令行：

```bash
sdk\.venv\Scripts\neos75 pages --list
```

```bash
sdk\.venv\Scripts\neos75 pages neos_clock neos_hifi_player "neos_photo?src=D:/pictures/a.jpg&full=1"
```

## 给智能体

- 屏幕服务的命令行（`--json` 输出）：`sdk\.venv\Scripts\neos75ctl status --json`
- Claude Code 技能：[`.claude/skills/neos75-screen/`](.claude/skills/neos75-screen/SKILL.md)（列出/选择页面、写新页面、预览、上屏）
- 本地 HTTP API：[`sdk/src/neos75screen/service/API.md`](sdk/src/neos75screen/service/API.md)
- Python SDK：[`sdk/README.md`](sdk/README.md)

界面和智能体可以同时操作，互相看到变化。

## 流畅度

屏幕和电脑之间是 USB HID，每毫秒最多 64 字节（实测约 58 KB/s），这是硬上限。所以电脑只发画面里变化的
16×16 小块（区域帧）：

| 内容 | 每帧 | 帧率（真机） |
|---|---|---|
| 小区域（电平条） | 0.7 KB | 47 帧/秒 |
| 频谱（24 条柱） | 约 3.5 KB | 约 13 帧/秒（运行器端到端） |
| 整屏 | 约 23 KB | 约 2 帧/秒 |

当前固件还有：实时页期间 5 ms 节拍（原厂 10 ms）、流水线收帧、**页缓存**：每页的画面预先存在
屏幕内存里，转旋钮时立即显示（真机解码 5 ms；以前约 0.45 s），随后电脑发来实时画面。

## 固件

实时显示需要补丁固件（只在 RAM 里工作，不写闪存配置；原厂固件完整备份在
`~/Tools/aula_neos75_backup`，可随时恢复；放在别处时用环境变量 `NEOS75_BACKUP` 指定，
见 [`firmware/README.md`](firmware/README.md)）。

| 版本 | 内容 | 状态 |
|---|---|---|
| `1d4c029f` | 实时页面、旋钮、键盘状态、主机分页、区域帧 | 上一版 |
| `04f09a6d` | 再加 5 ms 节拍、流水线、页缓存 | 上一版 |
| `1cb11444` | 再加帧页号标签（快速转旋钮时不再闪回上一页） | 上一版（dev） |
| `2710bdd4` | 发布版：同上，电脑也能转旋钮，开机 60 s 后确认试运行 | 上一版 |
| `209be02c` | 发布版：再加旋钮接管（屏幕旋钮的转动可以自定义） | **当前屏幕上**（2026-11-09 刷入，真机验收通过） |

每个新固件都要你按 SHA 批准后才刷。刷写和检查脚本：`hwtest/run_v3l.py`（沿用 `run_v3k.py`/`run_v3i.py`）（预检、只读备份、写入、20 秒静默、
功能检查、配置页比对；任何意外自动恢复原厂）。细节：[`docs/FIRMWARE.md`](docs/FIRMWARE.md)、
[`docs/HWTEST.md`](docs/HWTEST.md)；协议：[`docs/PROTOCOL.md`](docs/PROTOCOL.md)。

## 寿命与安全

- 页面、缓存、帧全部在屏幕 RAM 里，不写闪存；旋钮切到我们的页面也不会触发原厂的页号保存。
- 刷固件本身会擦写一次闪存：所以改进会攒成一个版本、一次刷入。
- 程序只发白名单里的命令（`sdk/src/neos75screen/guard.py`），不会发出厂重置、DFU 等原厂危险命令。

## 目录

| 目录 | 内容 |
|---|---|
| `app/` | 桌面应用（Electron）：键盘（按键、旋钮、宏、高级键、灯光、性能）、屏幕页面、设置 |
| `sdk/` | Python SDK、屏幕服务 `neos75d`、命令行 `neos75` / `neos75ctl`、小组件运行器 |
| `design/neos/` | 页面（官方 Néos 风格）；`design/widgets/` 早期的 Hi-Fi/复古页面 |
| `firmware/` | 补丁生成器、运行时（C）、模拟器测试 |
| `hwtest/` | 真机检查和刷写脚本 |
| `docs/` | 协议、固件、硬件测试记录、产品规划 |
