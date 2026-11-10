# Neos75 Screen：产品规划

状态：草案 v1（2026-11-07）。**范围更新：个人学习使用，暂不公开、不商用**；第 7 节的开源与分发策略暂缓，仅在将来决定公开时适用。本文是后续工作的依据；实现细节以 `PROTOCOL.md`、`FIRMWARE.md`、`DESIGN.md` 为准。

## 1. 产品是什么

AULA Neos75 右侧控制台有一块 480×360 屏幕、一个屏幕旋钮和一个音量旋钮。原厂只能放几页预设动画和一张壁纸。
本项目让电脑实时控制这块屏幕，并把两个旋钮变成可编程的输入：

- **人用**：一个常驻托盘的桌面 app（Electron），装好就能用：时钟、系统监控、正在播放、日历、相册等组件，
  旋钮切换，主题可换。
- **Agent 用**：同一套能力以本地 API、MCP 工具和命令行开放，Agent 能读状态、改页面、渲染预览、接收旋钮事件，
  而且看得到自己画出来的结果。
- **开发者用**：TypeScript 和 Python 两套 SDK、可模拟的固件、声明式的组件/主题格式。

与原厂 Aula Hub **并行**：键位、灯光、性能仍归 Hub（键盘主控）；本项目只管屏幕主控和旋钮输入。

非目标：替代 Aula Hub；支持其他键盘型号（以后按需）；分发厂商固件。

## 2. 设计原则

1. **一个守护进程拥有设备，其他都是客户端。** GUI、CLI、MCP、脚本都通过本地 API 说话，不各自打开 HID。
   这样不会互相抢设备、打断上传，也让 Agent 和人看到的是同一个状态。
2. **GUI 能做的，API 都能做；反过来不一定。** 刷固件这类高风险操作只在 GUI 里、由人确认后执行。
3. **一切可声明、可预览。** 页面和主题是文本文件（YAML/JSON + JSON Schema），任何改动都能先渲染成 PNG 看效果，
   再推到屏幕上。
4. **不自己发明视觉语言。** 视觉建立在已被验证的设计体系、字体和配色上（第 6 节），我们只做适配和策展。
5. **测试先于上机。** 固件在模拟器里执行真实机器码；随机压力测试和变异检查守住协议层；上机测试尽量无人值守。

## 3. 架构

```
                ┌───────────── Electron app（托盘 / 设置 / 主题库 / 刷写向导）
                │                     │ 本地 API
  Agent ── MCP server ──┐             ▼
  脚本 ── CLI (--json) ─┼──► neos75d（守护进程，TypeScript / Node）
  Python SDK ──────────┘       ├─ device：node-hid，协议、上传、保活、事件（移植自 Python SDK）
                               ├─ input：屏幕旋钮事件（固件）+ 电脑端旋钮捕获（Windows 钩子 + Raw Input）
                               ├─ renderer：离屏 480×360 Chromium 页面 → JPEG（组件用 HTML/CSS/Canvas）
                               ├─ pages：页面编排、旋钮切换、定时刷新、按需重发
                               └─ store：配置、主题、组件包（文本文件，可版本管理）
```

- **渲染**：组件是网页。守护进程在 Electron 的离屏窗口里按 480×360 渲染，截图编码为 JPEG（4:2:0，目标 20–45 KiB，
  实测 2.3–1.1 fps；见 `HWTEST.md`），只在内容变化时发送。静态页面低帧率，动画页面上限约 2–4 fps。
- **页面与旋钮**：屏幕旋钮在原厂页面和实时页之间切换（原厂行为不变）。实时页内部可以有多个“视图”，由
  屏幕旋钮的按下（电脑端捕获计算器键）或组件自己切换。音量旋钮默认不接管；用户在设置里打开后，组件可以拿它做
  调节（例如滚动列表），Fn 调音量和鼠标不受影响。旋钮转动不能离开原厂循环，除非将来固件加“接管模式”（第 8 节）。
- **Python SDK** 保留为脚本 SDK 和协议参考实现；TypeScript 核心与它跑同一套一致性测试（第 9 节）。

## 4. 对 Agent 友好

| 能力 | 形式 | 说明 |
|---|---|---|
| 读状态 | `GET /v1/status`、MCP `screen_status`、`neos75 status --json` | 设备、固件版本、可见/武装、当前页、帧率、错误 |
| 显示内容 | `screen_show_image`（路径/base64）、`screen_show_html`（HTML+CSS，可指定时长） | 一次性推送，不改配置 |
| 管理页面 | `pages_list` / `pages_apply`（YAML）/ `pages_set_active` | 声明式，写入 store，可回滚 |
| 预览 | `render_preview` → PNG（返回给 Agent 看） | 与上屏同一渲染器，像素一致 |
| 输入 | `knob_wait_event`（长轮询）、WebSocket `/v1/events` | 两个旋钮的转动/按下、可见性变化 |
| 主题与组件 | `themes_list` / `widgets_list` / `widget_schema` | 每个组件附 JSON Schema 和示例 |
| 诊断 | `diagnostics_bundle` | 日志、配置、设备信息打包，便于报告问题 |

规则：
- 每个接口都有机器可读的错误码（`DEVICE_BUSY`、`HIDDEN`、`NOT_PATCHED`…）和 `--json` 输出；文本只是给人看的附加层。
- 文档同时提供 `docs/llms.txt`（给模型的精简索引）和每个组件的 Schema，Agent 不必读源码。
- 本地 API 只监听 127.0.0.1，带随机令牌（写在用户目录的配置里）；MCP 走 stdio。
- **不对 Agent 开放**：刷写固件、恢复原厂、发送原厂危险命令（`34`/`13`/`FE`、壁纸上传）。这些只在 GUI 向导里由人确认。
- 守护进程提供“模拟设备”模式（固件模拟器），Agent 和 CI 可以在没有键盘的机器上完整演练。

## 5. 组件与内容

首批（按实用性）：时钟/日期、系统监控（CPU/GPU/内存/温度/网络）、正在播放（Windows SMTC：封面、曲目、进度）、
日历下一项、相册/幻灯片、GIF、文本/通知、天气。组件是一个目录：`widget.json`（元数据、参数 Schema、刷新间隔）+
`index.html`/`index.tsx` + 资源。数据通过注入的 `neos75` 对象（`useSensor`、`useNowPlaying`、`onKnob`）获取。

兼容导入（不打包、按需加载，尊重各自许可证）：turing-smart-screen 的 `theme.yaml` 主题（480×320，可居中显示在
480×360 上）；TRMNL 插件的 Liquid 模板（为 800×480 设计，需要缩放/重排，作为实验特性）。

## 6. 视觉方向：Hi-Fi 控制台，而不是“AI 风格的仪表盘”

**依据**：Neos75 出自 AULA 内部新成立的 New Renaissance 团队，屏幕、两个开槽金属旋钮和圆形媒体键组成的控制台，
被评论比作 Dieter Rams 时代的 Braun 收音机和唱机（见来源）。所以屏幕应该像这台“迷你 Hi-Fi”的面板：
刻度、表头、计数器、标牌字，而不是渐变卡片和发光图标。

**主题方向**（每套都是一组设计令牌：字体、配色、刻度/线条规则、动效节奏）：

| 主题 | 气质 | 视觉元素 | 字体（均为开源许可，需逐一核对） |
|---|---|---|---|
| **Console**（默认） | Braun / 功能主义 Hi-Fi | 暖灰与米白底、黑色刻度、单一橙色强调；VU 表头、收音机刻度盘、栅格对齐 | Inter / Space Grotesk / IBM Plex Sans |
| **VFD** | 70–80 年代真空荧光显示屏 | 深色底、青绿色辉光、点阵与七段字、轻微余辉 | DSEG（七段）、Doto（点阵） |
| **Nixie / Amber** | 复古未来、磁带未来主义 | 琥珀色辉光数字、扫描线、等宽终端字 | VT323、Departure Mono |
| **Millennium** | 千禧年 / Y2K 系统界面 | 金属与水蓝、半透明面板、系统窗口边框 | 参考 98.css / XP.css / system.css 的边框与配色 |
| **Renaissance** | 呼应“新文艺复兴” | 羊皮纸底、衬线标题、铜版画式线条与装饰框 | EB Garamond、Cormorant |

**怎样避免“AI 味”**：
- 直接采用成熟体系的规则，而不是凭感觉配：TRMNL Framework（MIT）的版式与栅格、为小屏/低色深设计的排版规则；
  98.css / XP.css / system.css（MIT）的系统界面细节；Lospec 社区调色板作为限色方案。
- 每套主题限定 2–3 种字号、1 个强调色、固定栅格（480×360 用 8 px 基线）。
- 先做“参考板”：每个主题收集 10–20 张真实产品照片（Braun、Teenage Engineering、老式音响、VFD 收音机、Y2K 系统截图）
  作为风格依据；**只借鉴风格，不复制商标、标识或具体产品界面**。
- 视觉回归：每个组件在每个主题下有截图基线，CI 比对；改动要看图审核。
- 欢迎设计师贡献主题：主题就是令牌文件 + 可选背景资源，不需要写代码。

## 7. 固件与分发（开源但不分发厂商固件）

- 仓库公开：补丁源码（`runtime.c`）、补丁生成器、地址表、模拟器测试、刷写工具、SDK、app；**不含**任何厂商二进制、
  导出镜像或反编译结果（`.gitignore` 已覆盖）。
- 刷写向导：从用户自己的屏幕读出原厂固件 → 校验 SHA 是已支持的版本 → 本地打补丁 → 刷入 → 回读核对；原厂备份留在本机，
  一键恢复。默认刷 **release**（启动 60 s 后才确认试运行标志）；dev 版（含测试旋钮注入）只给开发者。
- 支持矩阵：目前只支持 `V1.0.3 Sep 6 2026 23:18:16`。其他版本需要逐版本适配地址；向导遇到未知版本只读、不刷。
- 驱动：升级模式需要 WinUSB。向导内引导安装，并解释原因；屏幕正常使用（HID）不需要额外驱动。
- README 写清：与厂商无关；刷写有风险；按 MIT（代码）发布，字体与第三方资源按各自许可证。

## 8. 待定的固件能力

- **旋钮接管模式**（可选）：主机请求后，实时页可见期间屏幕旋钮只发事件、不翻页；主机超时（10 s）自动解除；需要定义
  退出手势。要改固件并重新验证，排在 app 可用之后。
- **诊断记录**（dev）：把键盘状态变化和配置保存调用记进内存环，供测试读取，用来彻底确认启动期 key 0x11 写入的来源。

## 9. 质量与测试

- 固件：模拟器执行真实机器码（114+ 用例），随机旋钮乱转保持原厂动画定时器数量，配置保存链调用计数。
- 协议：`tests/integration` 用真实 SDK 驱动固件机器码，96 组随机压力（乱拧、上传中途隐藏、输入队列溢出），
  变异检查确认能抓到已知固件缺陷。
- TS 核心：与 Python SDK 共用一致性用例（同一个模拟设备服务）。
- 上机：dev 固件的测试旋钮注入 + `hwtest/knob_auto.py` 无人值守；每次重启后等 20 s 再操作。
  只有实体旋钮到电脑的部分（音量旋钮、按下）每个版本由人按清单测一次。
- 渲染：组件 × 主题截图基线。

## 10. 路线图

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| 0 仓库公开 | README、许可证、`docs/llms.txt`、贡献指南、CI（模拟器测试） | CI 全绿，无厂商文件 |
| 1 TS 核心 + 守护进程 | `packages/core`（node-hid、上传、事件、保活）、`neos75d`、CLI `--json`、本地 API、模拟设备 | 与 Python SDK 一致性用例全过；真机推帧 |
| 2 MCP + 预览 | MCP server、`render_preview`、Schema、llms.txt | Agent 在模拟设备上完成“改页面→看预览→上屏” |
| 3 组件与主题 | 渲染器、6 个首批组件、Console 与 VFD 两套主题、截图基线 | 真机常驻 24 h 无错误、无配置写入 |
| 4 Electron app | 托盘、页面编排、主题库、旋钮设置、开机自启 | 普通用户无命令行完成全部设置 |
| 5 刷写向导 | 读出/校验/打补丁/刷入/恢复、驱动引导、release 固件 | 在干净 Windows 上端到端走通 |
| 6 发布 | 安装包签名、自动更新、首个 release | GitHub Release 可下载安装 |

## 11. 风险与未决问题

- **固件版本**：别的用户的屏幕若不是同一构建，需要逐版本适配；需要收集常见版本。
- **Aula Hub 并存**：Hub 上传壁纸时与我们抢设备；守护进程需检测并让出。
- **电脑端旋钮捕获**只在 Windows 下验证：有线（键盘 1EA7:4884）和 2.4G（接收器 6D66:8572）；蓝牙未验证。
- **代码签名**：未签名安装包会被 SmartScreen 警告。
- **法律**：补丁方式可降低风险，但不是法律意见。
- **TRMNL Framework 的字体与图表/地图适配器**另有许可，使用前逐项核对；turing-smart-screen 代码为 GPL-3.0，
  只做格式兼容导入，不打包其代码。

## 12. 参考来源

- Neos75 设计语言：[Yanko Design — AULA’s Neos 75 keyboard has a tiny hi-fi deck bolted to the side](https://www.yankodesign.com/2026/10/30/aulas-neos-75-keyboard-has-a-tiny-hi-fi-deck-bolted-to-the-side/amp/)
- 设计体系：[TRMNL Framework 开源说明](https://trmnl.com/blog/framework-design-system)、[usetrmnl/trmnl-framework](https://github.com/usetrmnl/trmnl-framework)（代码 MIT，字体 OFL/CC BY）、
  [TRMNL 模板文档](https://docs.usetrmnl.com/go/private-plugins/templates)
- 小屏主题生态：[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)（GPL-3.0，社区主题许可各异，如 [RedLineGraphs](https://github.com/guisfus/RedLineGraphs) 为 MIT）、
  [SensorPanel](https://github.com/oae/sensorpanel)（MIT，React 主题 + 原生渲染 + 可视化编辑器，架构参考）
- 复古界面：[XP.css](https://github.com/botoxparty/xp.css)（MIT，含 98 主题）、[system.css](https://best-of-web.builder.io/library/sakofchit/system.css)、[复古 CSS 框架合集](https://DEV.to/khangnd/10-retro-css-frameworks-to-relive-your-childhood-nph)；
  七段字参考 [Retro 时钟（DSEG 字体）](https://github.com/sonnyp/Retro)
- Agent 控制硬件：[UnitApi/mcp](https://github.com/UnitApi/mcp)、[ESP32 CYD MCP](https://lobehub.com/nl/mcp/ofryl-esp-cyd-mcp)
