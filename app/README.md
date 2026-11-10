# Neos75 屏幕（桌面端）

AULA Neos75 的桌面程序（个人使用），代替 Aula Hub：键盘本身（按键、旋钮、宏、高级键、灯光、性能）和
我们做的屏幕实时页面（Page 7–14）并列在左侧导航里，外加开机启动和托盘。界面沿用原厂 Néos 风格：黑底、
发丝线胶囊、MiSans 细体，品牌标识和原厂页面缩略图直接取自官方 UI 包（`design/official`，用
`python tools/ui_pack.py` 下载；没有时用文字代替）。

![按键](screenshots/05-keys.png)

## 启动

需要：`sdk/.venv`（Python SDK）、系统 Chrome 或 Edge、Node.js。第一次：

```bash
cd app
npm install
```

```bash
npm start          # 平时用这个：连接真实键盘屏幕（没插键盘时显示“未找到键盘屏幕”，插上后自动连上）
```

```bash
npm run fake       # 仅演示/开发：模拟设备，不需要键盘
```

模拟模式下标题栏和屏幕画面上一直有黄色的“模拟数据 · 未连接键盘”标记，状态栏不显示电量，命令行
`status` 也会标明；模拟设备默认不上报任何电量。如果发现已经在运行的服务模式不对（例如留下的模拟服务），
`npm start` 会先关掉它，再用真实键盘启动。

程序启动时先找已在运行的屏幕服务（`%APPDATA%\neos75screen\service.json`），没有就自己启动
`sdk/.venv/Scripts/python -m neos75screen.service`（安装版用自带的 `resources/python`），退出时关掉自己启动的服务。服务日志：
`%APPDATA%\neos75screen\neos75d.log`。

左侧导航分三组，结构参考 Aula Hub（左侧菜单 + 中间键盘图 + 下方设置面板），把它分散在各处的东西收拢：

**键盘**（代替 Aula Hub；写进键盘存储，换电脑、不开本应用也有效；有线和 2.4G 接收器都能用，蓝牙不行）

- **按键**：按官方布局画出整把键盘，三层（基础层、Fn 层、第 2 层）。点一个键，下面的选择器按分组
  （常用、高级键、宏、媒体、屏幕与键盘功能、鼠标、灯光、层、快捷键、字母……）列出键码，可搜索，也可
  直接按一个键（带 Ctrl/Shift/Alt/Win）。宏和高级键带金色边框。
- **旋钮**：两个旋钮的五个动作（顺时针、逆时针、按下、按住顺时针、按住逆时针）在一张表里。每个动作要么
  **写进键盘**（按层，任何电脑都有效，只能选键码），要么交给**电脑端**（打开程序/文件/网址、任意组合键、
  屏幕翻页、滚动、缩放……，需本应用在运行，存在 `%APPDATA%\neos75screen\knobs.json`）。“按住转动”只能
  由电脑端处理。顶部开关关掉后旋钮完全按键盘里的设置工作。
  - 屏幕旋钮转动交给电脑端时，靠屏幕固件的“旋钮接管”（flags 127），屏幕要在显示；只设置了“按住转动”时，
    平时转动照常翻页。
  - 按下、音量旋钮由电脑端键盘钩子接管（只认 Neos75 键盘：有线和 2.4G 接收器都验证过）。
- **宏**：16 个宏、共 1187 字节，存在键盘里。每步是点按/按下/松开某个键、延时或一段文字；可以“录制”
  （在键盘上按一遍，可选录下停顿）。改完点“写进键盘”，16 个宏一起写入。到「按键」把某个键设成「宏 n」使用。
  宏名只存在本机（`keyboard_names.json`）。
- **高级键**：4 个，即 Vial 的 tap dance：点按、按住、双击、点按后按住各发一个键，判定时间可调。官方驱动
  的“按住/单击（MT）”就是“点按 + 按住”；它的“切换开关（TGL）”这块键盘的固件不支持（读取返回错误）。
- **灯光**：44 种灯效、亮度、速度、颜色、饱和度。
- **性能**：Windows/macOS、按键响应间隔、防抖方式、休眠时间、感应唤醒、电脑灯光同步（官方驱动的
  “性能设置”就是这六项）。

**屏幕**

- **屏幕页面**：屏幕实时镜像、原厂样式的状态栏、开始/停止显示（停止会释放 USB 设备给其他程序）；旋钮循环
  （拖动排序，把页面库的卡片拖进来或点“添加”，悬停后 ⚙ 改选项、× 移除）；页面库（实时预览，点卡片先设置
  选项再添加）。左下角的小屏幕随时显示屏幕当前画面，点它进入这一页。

**本机**

- **设备与备份**：键盘和屏幕的连接、电量、协议、固件构建、宏与高级键的容量。第一次读取键盘时自动备份
  按键、旋钮、灯光、性能、宏和高级键（`%APPDATA%\neos75screen\keyboard_backup.json`），可一键恢复。
- **应用设置**：开机启动、关闭时隐藏到托盘、服务启动后自动显示、刷新间隔、画质、额外页面文件夹。
- **日志**：服务的最近记录。

标题栏右侧一直显示键盘的连接方式和电量。

不做的事：恢复出厂、固件升级、Vial 解锁——它们会擦除或重刷键盘；本应用不会写入会重启或擦除键盘/屏幕的
键码（QMK 的 bootloader/EEPROM 清除、厂商的“屏幕复位/出厂/刷写”），只调用白名单里的操作
（`sdk/src/neos75screen/via.py`）。

实现：VIA 协议用开源库 [qmk-via-api](https://github.com/srwi/qmk-via-api)（GPL-3.0，VIA 官方应用键盘接口的
移植），我们只包了一层守卫和这把键盘特有的部分（系统设置、电量、Vial tap dance）。键盘布局来自 Aula Hub
自带的官方 VIA 定义 `design/official/AULA-Neos75_via_v3.json`（不进 git，打包时带上），用
[pykle-serial](https://pypi.org/project/pykle-serial/)（MIT）解析；没有这个文件时按 7×15 矩阵显示。键盘
主控是厂商改的 QMK，同时支持 VIA 12 和 Vial 6。Aula Hub 每次改键后会发 `15 01 00…`，在这个固件里就是
“写编码器”，会把 Fn+音量旋钮逆时针清空；本应用不发。

## 安装包

```bash
sdk\.venv\Scripts\python tools\build_app.py
```

在仓库根目录运行，生成 `app/dist/Neos75Screen-Setup-<版本>.exe`（约 200 MB，含官方素材：只给自己用，`dist/`
不进 git）。脚本做这些事：

- 下载和 `sdk/.venv` 同版本的官方 Python 嵌入版，把 SDK 和小组件依赖（psutil、媒体信息、音频采集、numpy）
  装进去，放在 `resources/python`；
- 复制页面和素材到 `resources/design`（`neos`、`widgets`、`custom`、`official/jpg`、MiSans）；
- 记下构建它的仓库位置（`resources/build-info.json`）：安装版会读取那里的 `design/custom`，所以你或智能体
  新写的页面不用重新打包就能用；
- 用 electron-builder 打成 NSIS 安装包。

为什么里面有 Python：窗口是 Electron，但和键盘通信（USB HID）、渲染页面、读取系统和媒体信息的屏幕服务
一直是 Python SDK（开发版用 `sdk/.venv`）。安装版自带一份，电脑上不用另装 Python。

安装版的行为：

- 装到 `%LOCALAPPDATA%\Programs\Neos75Screen`（可改目录），开始菜单和桌面快捷方式，「应用和功能」里可以
  卸载。设置、页面选择和日志仍在 `%APPDATA%\neos75screen`，卸载时保留，和开发版共用。
- 页面用系统的 Chrome 渲染，没有 Chrome 就用 Edge（Windows 自带）。服务直接用 DevTools 协议控制这个后台
  浏览器（`sdk/src/neos75screen/live/cdp.py`，连接用 websocket-client），不再经过 Playwright 和它的
  Node.js 驱动进程；Playwright
  只在开发时给 `neos75ctl check` 用（`pip install -e sdk[check]`）。
- 内存：旋钮循环里的页面同在一个渲染进程（`--process-per-site`）；页面库预览用的浏览器空闲 1 分钟就关掉，
  需要时再开；关掉窗口（到托盘）会真的关闭它，托盘里再打开时重新建。
- 覆盖安装或卸载前，安装程序先让正在运行的应用正常退出（`Neos75Screen.exe --quit`），屏幕服务随之停止。
  应用被强行结束时，服务也会自己退出（`NEOS75_PARENT_PID`），不会占着键盘和文件。
- 开发版（`Neos75.cmd`）和安装版不能同时开：两者共用同一个单实例锁和屏幕服务。开发版曾打开过开机启动时，
  安装版第一次启动会接管这一项，避免登录时启动两次。
- 安装包没有代码签名，第一次运行时 SmartScreen 可能提示「未知发布者」，点「更多信息 → 仍要运行」。

## 给智能体

同一个服务也给命令行和智能体用（界面和智能体可以同时操作，互相看到变化）。左栏「给智能体 → 复制提示词」把
技能 `.claude/skills/neos75-screen/SKILL.md` 连同本机路径（工作目录、`neos75ctl` 的位置、`reference.md`）复制到
剪贴板，粘贴给智能体即可（`main.js` 的 `agentPrompt`）。开发版和构建仓库还在的安装版用仓库和 `sdk/.venv`；
仓库不在时用安装包里的技能副本（`resources/skill`）和自带的 Python。

```bash
sdk/.venv/Scripts/neos75ctl status --json
```

HTTP API：`sdk/src/neos75screen/service/API.md`。

## 截图

```bash
npm run shots      # 用模拟设备打开界面，自动截图到 screenshots/ 后退出
```

截图前可设 `NEOS75_HOME` 指向一个临时文件夹，以免改动自己的页面选择。截图用的是模拟设备，所以图上带着“模拟数据”标记。

模拟模式下服务还会带一把模拟键盘（按真实键盘读出的出厂键位），所以键盘的各页也能截图。

| 文件 | 内容 |
|---|---|
| `screenshots/01-screen.png` | 屏幕页面：屏幕镜像（旋钮已转到 Page 8）、旋钮循环、页面库 |
| `screenshots/02-page-options.png` | “图片”页的选项与预览 |
| `screenshots/03-add-page.png` | 从页面库添加 Hi-Fi 播放器 |
| `screenshots/04-library.png` | 页面库 |
| `screenshots/05-keys.png` | 按键：键盘图和键码选择器 |
| `screenshots/06-knobs.png` | 旋钮：两个旋钮的动作和编辑器 |
| `screenshots/07-macros.png` | 宏编辑 |
| `screenshots/08-advanced.png` | 高级键 |
| `screenshots/09-lighting.png` | 灯光 |
| `screenshots/10-performance.png` | 性能 |
| `screenshots/11-device.png` | 设备与备份 |
| `screenshots/12-settings.png` | 应用设置 |

## 文件

- `main.js`：主进程（找/启动服务、窗口、托盘、开机启动、文件选择、截图模式）
- `preload.js`：页面可用的少量接口（无 Node 权限）
- `renderer/`：界面（原生 HTML/CSS/JS），直接调用服务的 localhost API 和事件流；`keyboard.js` 是键盘各页，
  `app.js` 是导航、屏幕页面、设置和日志
- `assets/ring.png`：没有官方 UI 包时的托盘图标
- `installer.nsh`：安装程序的关闭应用步骤（先请应用退出，再结束安装目录里剩下的进程）
- `../tools/build_app.py`：生成安装包
