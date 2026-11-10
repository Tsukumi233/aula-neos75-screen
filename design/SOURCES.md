# 设计素材来源与许可证

范围：**个人学习使用，不公开、不商用**（2026-11-07 用户确认）。仍然登记出处，便于找回原作。

原则：屏幕组件的视觉尽量**改编别人精心手作的作品**，而不是从零设计。只用许可证明确允许修改和再分发的素材；
每个被采用的素材都在本文件“已采用”表里登记（出处、作者、许可证、改了什么），并在发布包里保留许可声明。
`design/mockups/` 里的自制样图只作为版式草图，不作为最终视觉。

## 1. 可用来源（许可证已核对）

| 来源 | 内容 | 许可证 | 使用条件 |
|---|---|---|---|
| [CodePen](https://blog.codepen.io/docs/licensing/) 公开作品 | 大量手作的 CSS/SVG/JS 小部件：辉光管、VU 表、磁带、时钟、仪表 | 公开 Pen 默认 **MIT** | 保留 MIT 许可声明；改编后的代码仍按 MIT。私有 Pen 无默认许可，不用 |
| [Uiverse.io](https://www.github.com/uiverse-io/galaxy) | 3000+ 社区 UI 元素（按钮、开关、卡片、加载动画） | **MIT** | 建议署名作者和 Uiverse；以各元素页面为准 |
| [Figma Community](https://help.figma.com/hc/en-us/articles/360042296374-Figma-Community-copyright-and-licensing) 免费文件 | UI kit、图标、插画 | 默认 **CC BY 4.0**（作者可另加条款） | 署名；Figma 附加要求成品与原文件“有明显不同”。下载需要你本人登录 Figma |
| [Meteocons](https://github.com/basmilius/meteocons)（Bas Milius） | 475+ 手作天气图标，4 种风格，SVG 和 Lottie 动画 | **MIT** | 保留许可声明 |
| [LottieFiles](https://help.lottiefiles.com/hc/en-us/articles/900002438343-Can-I-use-a-free-animation-on-Lottiefiles-for-commercial-business-use) 免费动画 | 矢量动画（图标、过场、装饰） | **Lottie Simple License** | 可修改、商用；不得单独再分发原文件；以各动画页面为准 |
| [AeroMedia: Frutiger Aero UI](https://onnoff.itch.io/aero-media)（itch.io） | 千禧年/Frutiger Aero 风格界面素材 | **CC BY 4.0** | 署名 |
| [Make Aero](https://github.com/Visnalize/makeaero) | 亮面按钮、玻璃球、Win7 玻璃窗的 CSS 生成器 | **MIT** | 保留许可声明 |
| [Vintage Ornaments](https://screwylightbulb.itch.io/vintage-ornaments)（itch.io） | 19 世纪末 20 世纪初公有领域杂志里的花饰、边框、花体首字母，已描成 SVG | 原图为公有领域；**矢量版的许可需在下载页确认** | 确认后再用 |
| [TRMNL Framework](https://github.com/usetrmnl/trmnl-framework) | 小屏/墨水屏的版式、栅格、字体规则 | 代码 **MIT**，字体 OFL / CC BY | 按各自许可 |
| Kenney.nl UI 素材 | 游戏风 UI 素材 | 一般为 **CC0**（未核对，使用前确认） | — |
| Google Fonts | 字体 | 多为 **SIL OFL** | 可嵌入、修改；不可单独售卖字体 |

**只作灵感、不可直接用的**：Dribbble、Behance、Pinterest 上的作品；Braun、Teenage Engineering 等真实产品的外观和商标；
Rainmeter、KWGT、Facer、Wallpaper Engine 上的皮肤（许可多为禁止商用或禁止改编）；AIDA64 面板模板（许可不明）。

## 2. 各主题候选（待看效果后筛选）

| 主题 | 组件 | 候选 | 备注 |
|---|---|---|---|
| Nixie / Amber | 时钟 | [Steins;Gate 辉光管](https://gist.github.com/CodeMyUI/b2f2e3690d778baf7b2316ae154c4ec4)（Kent Nguyen，CodePen）、[RAFA3L 的 Nixie Tube Clock](https://codepen.io/spark/363)、[nixie-tube-clock](https://awesome.ecosyste.ms/projects/github.com%2Frajtilak-2020%2Fnixie-tube-clock)、[qwerji/nixie-clock](https://www.github.com/qwerji/nixie-clock) | GitHub 项目需逐个看许可证 |
| Nixie / Amber | 正在播放 | 磁带：[Tom Hazledine](https://codepen.io/tomhazledine/pen/IwEDu)、[manz](https://codepen.io/manz/pen/rXzydL)、[artlung](https://codepen.io/artlung/pen/LYaqgKG)、[Alvaro Montoro 单元素版](https://dev.to/alvaromontoro/divtober-day-19-nostalgia-4np5)、[mrtoxas/cassette-tape-css](https://www.github.com/mrtoxas/cassette-tape-css) | 选能按播放进度驱动的 |
| Console | 系统监控 | VU 表：[offeringofpie](https://codepen.io/offeringofpie/pen/ogveLjq)、[ladyjellington](https://codepen.io/ladyjellington/pen/VNrrmV)、[nishantsabharwal13](https://codepen.io/nishantsabharwal13/pen/KBQgab)、[Chmood](https://codepen.io/Chmood/pen/MaBZdM)；CSS 仪表做法：[Mads Stoumann](https://dev.to/madsstoumann/how-to-create-gauges-in-css-3581) | — |
| Console | 时钟 | [纯 CSS 手表（erenesto）](https://codepen.io/erenesto/pen/dybxGXw)、[纯 CSS 时钟（john555）](https://codepen.io/john555/pen/odxzVv) 作为底子，表盘按 Braun AB1 的比例改 | 不用 Braun 商标 |
| Millennium | 全部 | [AeroMedia](https://onnoff.itch.io/aero-media)、[Make Aero](https://github.com/Visnalize/makeaero)、XP.css / 98.css | — |
| Renaissance | 装饰 | [Vintage Ornaments](https://screwylightbulb.itch.io/vintage-ornaments) 的花饰、边框、花体首字母 | 先确认矢量版许可 |
| 全部 | 天气 | [Meteocons](https://github.com/basmilius/meteocons) | 每个主题选一种风格 |

## 2b. 已下载并渲染的候选（2026-11-07）

对比图：`design/candidates_clock_tape.png`、`design/candidates_winamp.png`；源码在 `design/vendor/`（不进 git）。

- **Winamp 皮肤**（[Winamp Skin Museum](https://skins.webamp.org)，用 [Webamp](https://github.com/captbaritone/webamp) 1.5 在浏览器里渲染；
  皮肤 `https://r2.webampskins.org/skins/<md5>.wsz`，检索接口 `https://api.webamp.org/graphql`）：Hitachi ST-9610
  `53d192b798c9294602bf113e7405e403`、HiFi3 `b035f63c…`、HiFi55 `42424ba1…`、Kenwood 风 `ab4afd5d…`、Modern Stereo Amp 2
  `a5d6f0b4…`、Silver Stereo `1e3f2bf9…`、Soney Stereo `c074f89b…`、I-deck `f49ea78b…` / `85ce8687…`、Sony Tape Deck `f54c4e41…`、
  silvert `c7807a69…`、Siemens `e844b963…`、SilverLine `9c892739…`、XP Silver `4446bac8…`、Aluminium2 `8a41d047…`、
  Punched Aluminium `a4d75ae7…`。生成脚本 `design/vendor/winamp_skins.py`、`winamp_render.py`。
- **CodePen**（内置浏览器查看与截图；直接抓取被 Cloudflare 验证拦截，不绕过）：
  [Paul Noble · Braun BC17 时钟](https://codepen.io/paulnoble/pen/gbPGMZZ)、[Eren Guldas · 瑞士风手表](https://codepen.io/erenesto/pen/dybxGXw)、
  [Kent Nguyen · 辉光管](https://codepen.io/rukouen/pen/jVWNre)、[J Lopes · 拟物音箱](https://codepen.io/offeringofpie/pen/ogveLjq)、
  [Tom Hazledine · 磁带](https://codepen.io/tomhazledine/pen/RwxeVw)、[Manz · 磁带](https://codepen.io/manz/pen/rXzydL)、
  [Joe Crawford · 磁带](https://codepen.io/artlung/pen/LYaqgKG)、VFD 做法参考 [Roger · Faux VFD](https://codepen.io/roger-random/pen/XWxYqgY)。
- **GitHub**：[rajtilak-2020/nixie-tube-clock](https://github.com/rajtilak-2020/nixie-tube-clock)、[qwerji/nixie-clock](https://github.com/qwerji/nixie-clock)、
  [mrtoxas/cassette-tape-css](https://github.com/mrtoxas/cassette-tape-css)、[basmilius/meteocons](https://github.com/basmilius/meteocons)、
  [Visnalize/makeaero](https://github.com/Visnalize/makeaero)。
- 试过 Uiverse（3800 个元素按关键词筛 35 个）：几乎都是普通网页控件，不适合本项目的复古质感，放弃。

## 3. 已采用

| 素材 | 出处 | 作者 | 许可证 | 用在哪 | 改动 |
|---|---|---|---|---|---|
| Braun BC17 时钟 | [CodePen](https://codepen.io/paulnoble/pen/gbPGMZZ) | Paul Noble | MIT（CodePen 默认） | `design/widgets/hifi_clock.html` | SCSS 展平、TS 改 JS、去品牌标识、固定 336 px、时间来自数据、加日期栏 |
| 拟物音箱：VU 表、推子、指示灯 | [CodePen](https://codepen.io/offeringofpie/pen/ogveLjq) | J Lopes (offeringofpie) | MIT（CodePen 默认） | `design/widgets/hifi_system.html` | 表头显示 CPU/GPU、推子显示内存/温度/磁盘/网络、只读、480×360 |
| Webamp | [GitHub](https://github.com/captbaritone/webamp) | Jordan Eldredge 等 | MIT | `design/widgets/hifi_player.html` | 只用主窗口和均衡器，缩放到 480×360 |
| Refurbished Hitachi Stereo 皮肤 | [Winamp Skin Museum](https://skins.webamp.org) `53d192b7…` | 原皮肤作者 | 个人使用 | 同上 | 未改动 |
| 辉光管座钟 | [GitHub](https://github.com/rajtilak-2020/nixie-tube-clock) | K Rajtilak | MIT | `design/widgets/retro_clock.html`，`third_party/nixie-tube-clock/` | 去电源按钮、尺寸、时间来自数据、加琥珀色角注 |
| CSS 磁带 | [CodePen](https://codepen.io/tomhazledine/pen/RwxeVw) | Tom Hazledine | MIT（CodePen 默认） | `design/widgets/retro_player.html`，`src/cassette.scss` | 去内页、琥珀配色、磁带卷按进度变化、加琥珀读数 |
