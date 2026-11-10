"""QMK keycodes as the Neos75 keyboard firmware uses them (QMK 0.19+ numbering, VIA protocol 12), with short
labels, Chinese names and the groups the app's picker shows.

Verified on the keyboard 2026-11-09: 0x00A8 mute, 0x00A9 / 0x00AA volume up / down, 0x00B2 calculator,
0x7827 / 0x7828 RGB brightness up / down, 0x7E0F / 0x7E10 the vendor's "LCD mode+ / -" (screen next / previous
page; custom keycode 15 / 16 of the official definition).

`forbidden()` keys are never written: they reboot or erase the keyboard or the screen.
"""
from __future__ import annotations

KC_NO, KC_TRNS = 0x0000, 0x0001
QK_MODS, QK_MODS_MAX = 0x0100, 0x1FFF
QK_MOD_TAP, QK_LAYER_TAP = 0x2000, 0x4000
QK_TO, QK_MOMENTARY, QK_DEF_LAYER, QK_TOGGLE_LAYER, QK_ONE_SHOT_LAYER = 0x5200, 0x5220, 0x5240, 0x5260, 0x5280
QK_LAYER_TAP_TOGGLE = 0x52C0
QK_TAP_DANCE, QK_MACRO = 0x5700, 0x7700
QK_KB = 0x7E00
RGB_BASE = 0x7820

# Vendor custom keycodes (QK_KB + index), official definition order. Chinese names are ours.
CUSTOM = [
    ("Mission Control", "调度中心（macOS）"), ("Launch Pad", "启动台（macOS）"), ("Task View", "任务视图"),
    ("File Explorer", "文件资源管理器"), ("Win Mode", "Windows 模式"), ("Mac Mode", "macOS 模式"),
    ("Lamp Test", "灯光测试"), ("Deep Sleep", "深度睡眠"), ("Out Usb", "切到有线"), ("BT 1", "蓝牙 1"),
    ("BT 2", "蓝牙 2"), ("BT 3", "蓝牙 3"), ("TG_GUI", "锁定 Win 键"), ("OUT RF", "切到 2.4G"),
    ("LCD_TG", "屏幕开关"), ("LCD+", "屏幕下一页"), ("LCD-", "屏幕上一页"), ("Siri", "Siri（macOS）"),
    ("SShot", "截图（macOS）"), ("Cortana", "Cortana"), ("BYC", "充电指示灯开关"), ("RBHL", "电量灯开关"),
    ("LCD_RETURN", "屏幕返回"), ("LCD_ENTER", "屏幕确认"), ("LCD_UTOG", "屏幕上方信息开关"),
    ("U_D", "SOCD 上↔下"), ("L_R", "SOCD 左↔右"), ("D_U", "SOCD 下↔上"), ("R_L", "SOCD 右↔左"),
    ("W_S", "SOCD W↔S"), ("A_D", "SOCD A↔D"), ("S_W", "SOCD S↔W"), ("D_A", "SOCD D↔A"),
    ("LCD_CALC", "屏幕计算器"), ("LCD_RST", "屏幕复位/出厂/刷写（禁用）"), ("OLRGB", "电脑灯光同步开关"),
    ("PROFILE+", "下一个配置"), ("PROFILE-", "上一个配置"), ("SENSOR", "感应开关"), ("LCD_START", "屏幕计时开始"),
    ("LCD_PAUSE", "屏幕计时暂停"), ("LCD_STOP", "屏幕计时停止"), ("VIA", "打开 VIA 网站"),
]
CUSTOM_HIDDEN = {6, 34}          # lamp test, LCD reset
FORBIDDEN = {0x7C00, 0x7C01, 0x7C02, 0x7C03, QK_KB + 34}   # QK_BOOT, QK_REBOOT, QK_DEBUG_TOGGLE, EE_CLR, LCD reset


def forbidden(code: int) -> bool:
    return code in FORBIDDEN


BASIC: dict[int, tuple[str, str]] = {0x0000: ("", "无"), 0x0001: ("▽", "透明（用下层）")}
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    BASIC[0x04 + i] = (ch, ch)
for i, ch in enumerate("1234567890"):
    BASIC[0x1E + i] = (ch, ch)
BASIC.update({
    0x28: ("Enter", "回车"), 0x29: ("Esc", "Esc"), 0x2A: ("Bksp", "退格"), 0x2B: ("Tab", "Tab"),
    0x2C: ("Space", "空格"), 0x2D: ("-", "-"), 0x2E: ("=", "="), 0x2F: ("[", "["), 0x30: ("]", "]"),
    0x31: ("\\", "\\"), 0x32: ("#", "非美式 #"), 0x33: (";", ";"), 0x34: ("'", "'"), 0x35: ("`", "`"),
    0x36: (",", ","), 0x37: (".", "."), 0x38: ("/", "/"), 0x39: ("Caps", "大写锁定"),
    0x46: ("PrtSc", "截屏键"), 0x47: ("ScrLk", "滚动锁定"), 0x48: ("Pause", "暂停"), 0x49: ("Ins", "Insert"),
    0x4A: ("Home", "Home"), 0x4B: ("PgUp", "上翻页"), 0x4C: ("Del", "Delete"), 0x4D: ("End", "End"),
    0x4E: ("PgDn", "下翻页"), 0x4F: ("→", "右"), 0x50: ("←", "左"), 0x51: ("↓", "下"), 0x52: ("↑", "上"),
    0x53: ("Num", "数字锁定"), 0x54: ("P/", "小键盘 /"), 0x55: ("P*", "小键盘 *"), 0x56: ("P-", "小键盘 -"),
    0x57: ("P+", "小键盘 +"), 0x58: ("PEnt", "小键盘回车"), 0x63: ("P.", "小键盘 ."), 0x64: ("\\|", "非美式 \\"),
    0x65: ("Menu", "菜单键"),
    0xA5: ("Power", "关机"), 0xA6: ("Sleep", "睡眠"), 0xA7: ("Wake", "唤醒"),
    0xA8: ("Mute", "静音"), 0xA9: ("Vol+", "音量 +"), 0xAA: ("Vol-", "音量 −"), 0xAB: ("Next", "下一首"),
    0xAC: ("Prev", "上一首"), 0xAD: ("Stop", "停止播放"), 0xAE: ("Play", "播放/暂停"), 0xAF: ("Media", "媒体播放器"),
    0xB0: ("Eject", "弹出"), 0xB1: ("Mail", "邮件"), 0xB2: ("Calc", "计算器"), 0xB3: ("MyPC", "此电脑"),
    0xB4: ("Search", "浏览器搜索"), 0xB5: ("WHome", "浏览器主页"), 0xB6: ("Back", "浏览器后退"),
    0xB7: ("Fwd", "浏览器前进"), 0xB8: ("WStop", "浏览器停止"), 0xB9: ("Refresh", "浏览器刷新"),
    0xBA: ("Fav", "浏览器收藏"), 0xBB: ("FFwd", "快进"), 0xBC: ("Rwd", "快退"), 0xBD: ("Bri+", "屏幕亮度 +"),
    0xBE: ("Bri-", "屏幕亮度 −"), 0xBF: ("Ctrl P", "控制面板"), 0xC0: ("Asst", "助手"), 0xC1: ("MCtl", "调度中心"),
    0xC2: ("LPad", "启动台"),
    0xCD: ("M↑", "鼠标上移"), 0xCE: ("M↓", "鼠标下移"), 0xCF: ("M←", "鼠标左移"), 0xD0: ("M→", "鼠标右移"),
    0xD1: ("M1", "鼠标左键"), 0xD2: ("M2", "鼠标右键"), 0xD3: ("M3", "鼠标中键"), 0xD4: ("M4", "鼠标侧键 4"),
    0xD5: ("M5", "鼠标侧键 5"), 0xD9: ("Wh↑", "滚轮向上"), 0xDA: ("Wh↓", "滚轮向下"), 0xDB: ("Wh←", "滚轮向左"),
    0xDC: ("Wh→", "滚轮向右"),
    0xE0: ("LCtrl", "左 Ctrl"), 0xE1: ("LShift", "左 Shift"), 0xE2: ("LAlt", "左 Alt"), 0xE3: ("LWin", "左 Win"),
    0xE4: ("RCtrl", "右 Ctrl"), 0xE5: ("RShift", "右 Shift"), 0xE6: ("RAlt", "右 Alt"), 0xE7: ("RWin", "右 Win"),
})
for n in range(1, 13):
    BASIC[0x39 + n] = (f"F{n}", f"F{n}")
for n in range(13, 25):
    BASIC[0x68 + n - 13] = (f"F{n}", f"F{n}")
for n in range(1, 10):
    BASIC[0x59 + n - 1] = (f"P{n}", f"小键盘 {n}")
BASIC[0x62] = ("P0", "小键盘 0")

RGB: dict[int, tuple[str, str]] = {
    0x7820: ("RGB", "灯光开关"), 0x7821: ("Mode+", "下一个灯效"), 0x7822: ("Mode-", "上一个灯效"),
    0x7823: ("Hue+", "色相 +"), 0x7824: ("Hue-", "色相 −"), 0x7825: ("Sat+", "饱和度 +"), 0x7826: ("Sat-", "饱和度 −"),
    0x7827: ("Lit+", "灯光亮度 +"), 0x7828: ("Lit-", "灯光亮度 −"), 0x7829: ("Spd+", "灯效速度 +"),
    0x782A: ("Spd-", "灯效速度 −"),
}
MOD_NAMES = ((0x01, "Ctrl"), (0x02, "Shift"), (0x04, "Alt"), (0x08, "Win"))


def _mods(m: int) -> str:
    side = "R" if m & 0x10 else ""
    return "+".join(side + name for bit, name in MOD_NAMES if m & bit)


def describe(code: int) -> dict:
    """{code, label, name, group} for any keycode; unknown ones show as hex."""
    code &= 0xFFFF
    if code in BASIC:
        label, name = BASIC[code]
        return {"code": code, "label": label, "name": name}
    if code in RGB:
        label, name = RGB[code]
        return {"code": code, "label": label, "name": name}
    if QK_KB <= code < QK_KB + len(CUSTOM):
        label, name = CUSTOM[code - QK_KB]
        return {"code": code, "label": label, "name": name}
    if QK_MODS <= code <= QK_MODS_MAX:
        inner = describe(code & 0xFF)
        mods = _mods((code >> 8) & 0x1F)
        return {"code": code, "label": f"{mods}+{inner['label']}", "name": f"{mods} + {inner['name']}"}
    for base, fmt, name in ((QK_TO, "TO({})", "切到第 {} 层"), (QK_MOMENTARY, "MO({})", "按住切到第 {} 层"),
                            (QK_DEF_LAYER, "DF({})", "默认层设为 {}"), (QK_TOGGLE_LAYER, "TG({})", "开关第 {} 层"),
                            (QK_ONE_SHOT_LAYER, "OSL({})", "下一键用第 {} 层"),
                            (QK_LAYER_TAP_TOGGLE, "TT({})", "点按开关/按住切到第 {} 层")):
        if base <= code < base + 0x20:
            return {"code": code, "label": fmt.format(code - base), "name": name.format(code - base)}
    if QK_MACRO <= code < QK_MACRO + 0x20:
        return {"code": code, "label": f"M{code - QK_MACRO + 1}", "name": f"宏 {code - QK_MACRO + 1}"}
    if QK_TAP_DANCE <= code < QK_TAP_DANCE + 0x100:
        return {"code": code, "label": f"TD{code - QK_TAP_DANCE + 1}", "name": f"高级键 {code - QK_TAP_DANCE + 1}"}
    if QK_LAYER_TAP <= code < QK_LAYER_TAP + 0x1000:
        layer, inner = (code >> 8) & 0x0F, describe(code & 0xFF)
        return {"code": code, "label": f"LT{layer}({inner['label']})",
                "name": f"点按 {inner['name']}，按住切到第 {layer} 层"}
    if QK_MOD_TAP <= code < QK_LAYER_TAP:
        mods, inner = _mods((code >> 8) & 0x1F), describe(code & 0xFF)
        return {"code": code, "label": f"MT({inner['label']})", "name": f"点按 {inner['name']}，按住 {mods}"}
    return {"code": code, "label": f"0x{code:04X}", "name": f"键码 0x{code:04X}"}


def catalog() -> list[dict]:
    """Picker groups: [{group, keys: [{code, label, name}]}]."""
    def keys(codes) -> list[dict]:  # noqa: ANN001
        return [describe(c) for c in codes]
    letters = list(range(0x04, 0x1E)) + list(range(0x1E, 0x28))
    symbols = [0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
               0x65, 0x32, 0x64]
    function = list(range(0x3A, 0x46)) + list(range(0x68, 0x74))
    navigation = [0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52]
    numpad = [0x53, 0x54, 0x55, 0x56, 0x57, 0x58] + list(range(0x59, 0x63)) + [0x62, 0x63]
    modifiers = list(range(0xE0, 0xE8))
    media = [0xA8, 0xA9, 0xAA, 0xAE, 0xAB, 0xAC, 0xAD, 0xBB, 0xBC, 0xBD, 0xBE, 0xB2, 0xB3, 0xB1, 0xAF,
             0xB4, 0xB5, 0xB6, 0xB7, 0xB9, 0xBA, 0xBF, 0xC1, 0xC2, 0xA6, 0xA7, 0xA5, 0xB0]
    mouse = [0xD9, 0xDA, 0xDB, 0xDC, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xCD, 0xCE, 0xCF, 0xD0]
    layers = [QK_MOMENTARY + n for n in range(1, 3)] + [QK_TOGGLE_LAYER + n for n in range(1, 3)] + \
             [QK_TO + n for n in range(3)]
    custom = [QK_KB + i for i in range(len(CUSTOM)) if i not in CUSTOM_HIDDEN]
    shortcuts = [0x0100 | 0x06, 0x0100 | 0x19, 0x0100 | 0x1B, 0x0100 | 0x1D, 0x0100 | 0x1C, 0x0100 | 0x04,
                 0x0100 | 0x16, 0x0100 | 0x09, 0x0100 | 0x17, 0x0100 | 0x1A, 0x0300 | 0x17, 0x0800 | 0x07,
                 0x0800 | 0x08, 0x0800 | 0x0F, 0x0400 | 0x2B, 0x0400 | 0x3D, 0x0800 | 0x2B, 0x0A00 | 0x16]
    return [
        {"group": "常用", "keys": keys([KC_NO, KC_TRNS])},
        {"group": "媒体与系统", "keys": keys(media)},
        {"group": "屏幕与键盘功能", "keys": keys(custom)},
        {"group": "鼠标", "keys": keys(mouse)},
        {"group": "灯光", "keys": keys(list(RGB))},
        {"group": "层", "keys": keys(layers)},
        {"group": "快捷键", "keys": keys(shortcuts)},
        {"group": "字母与数字", "keys": keys(letters)},
        {"group": "符号与编辑", "keys": keys(symbols)},
        {"group": "功能键", "keys": keys(function)},
        {"group": "方向与导航", "keys": keys(navigation)},
        {"group": "修饰键", "keys": keys(modifiers)},
        {"group": "小键盘", "keys": keys(numpad)},
    ]


def with_mods(code: int, ctrl: bool = False, shift: bool = False, alt: bool = False, win: bool = False) -> int:
    """A basic keycode with modifiers held (QMK LCTL(kc) etc.)."""
    m = (0x01 if ctrl else 0) | (0x02 if shift else 0) | (0x04 if alt else 0) | (0x08 if win else 0)
    return (m << 8) | (code & 0xFF) if m else code & 0xFF
