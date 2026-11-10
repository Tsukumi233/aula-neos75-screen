"""Theme mockups: 5 themes x 3 widgets at 480x360, rendered with headless Chrome."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

OUT = Path(__file__).parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Shared sample data (fictional track)
TIME = "10:42"
DATE = ("SAT", "07", "NOV", "2026")
CPU, GPU, RAM, TEMP = 37, 52, 61, 58
NET_DN, NET_UP = "12.4", "0.8"
TRACK, ARTIST, ALBUM = "Aerial Cartography", "Low Tide Orchestra", "Coastlines"
POS, DUR, PROG = "2:14", "5:38", 134 / 338


# ---------------------------------------------------------------------------------------------- helpers
def seven(digits: str, x: float, y: float, h: float, on: str, off: str, glow: str = "", gap: float = 0.0,
          slant: float = 0.0) -> str:
    """SVG seven-segment digits (with unlit segments drawn in `off`). ':' gives a colon."""
    w = h * 0.52
    t = h * 0.11          # segment thickness
    g = t * 0.18 + gap    # gap between segments
    segs = {
        "a": [(t / 2 + g, 0), (w - t / 2 - g, 0), (w - t - g, t), (t + g, t)],
        "d": [(t + g, h - t), (w - t - g, h - t), (w - t / 2 - g, h), (t / 2 + g, h)],
        "g": [(t / 2 + g, h / 2), (t + g, h / 2 - t / 2), (w - t - g, h / 2 - t / 2), (w - t / 2 - g, h / 2),
              (w - t - g, h / 2 + t / 2), (t + g, h / 2 + t / 2)],
        "f": [(0, t / 2 + g), (t, t + g), (t, h / 2 - t / 2 - g), (0, h / 2 - g)],
        "e": [(0, h / 2 + g), (t, h / 2 + t / 2 + g), (t, h - t - g), (0, h - t / 2 - g)],
        "b": [(w, t / 2 + g), (w, h / 2 - g), (w - t, h / 2 - t / 2 - g), (w - t, t + g)],
        "c": [(w, h / 2 + g), (w, h - t / 2 - g), (w - t, h - t - g), (w - t, h / 2 + t / 2 + g)],
    }
    lit = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
           "7": "abc", "8": "abcdefg", "9": "abcdfg", " ": "", "-": "g"}
    out = []
    cx = x
    flt = f' filter="url(#{glow})"' if glow else ""
    for ch in digits:
        if ch == ":":
            r = t * 0.55
            for cy in (y + h * 0.32, y + h * 0.68):
                out.append(f'<circle cx="{cx + t:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{on}"{flt}/>')
            cx += t * 2.6
            continue
        for name, pts in segs.items():
            col = on if name in lit[ch] else off
            if col == "none":
                continue
            p = " ".join(f"{cx + px - (py - h / 2) * slant:.1f},{y + py:.1f}" for px, py in pts)
            f = flt if name in lit[ch] else ""
            out.append(f'<polygon points="{p}" fill="{col}"{f}/>')
        cx += w + h * 0.16
    return "\n".join(out)


def page(name: str, fonts: str, css: str, body: str) -> None:
    link = f'<link href="https://fonts.googleapis.com/css2?{fonts}&display=block" rel="stylesheet">' if fonts else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8">{link}<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:480px;height:360px;overflow:hidden}}
body{{position:relative}}
{css}</style></head><body>{body}</body></html>"""
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")


def arc_ticks(cx, cy, r1, r2, a0, a1, n, color, width, major_every=0, r_major=None):
    out = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        rr = r_major if (major_every and i % major_every == 0 and r_major) else r2
        x1, y1 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
        x2, y2 = cx + rr * math.cos(a), cy + rr * math.sin(a)
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"/>')
    return "".join(out)


# ============================================================================================ CONSOLE
# Braun / functionalist hi-fi, strict 8 px grid, one orange accent. Two finishes: gunmetal (matches the
# user's keyboard, default) and light.
CONSOLE_PALETTES = {
    "console": dict(bg="#2B2E31", face="#222427", ink="#ECEAE5", mute="#8F9499", acc="#F26B2B", line="#43474B",
                    btn="#35393D", btn_ink="#ECEAE5"),
    "console_light": dict(bg="#E8E4DA", face="#F4F1EA", ink="#1D1C1A", mute="#8B867C", acc="#E0531D", line="#CFC9BC",
                          btn="#F4F1EA", btn_ink="#1D1C1A"),
}


def console_css(P):
    return f"""body{{background:{P['bg']};color:{P['ink']};font-family:Inter,sans-serif}}
.lbl{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:{P['mute']};font-weight:500}}
.val{{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}}"""


FONT_INTER = "family=Inter:wght@300;500;600"


def console_clock(key="console"):
    P = CONSOLE_PALETTES[key]
    cx, cy, r = 168, 180, 136
    hh, mm, ss = 10, 42, 18
    ticks = arc_ticks(cx, cy, r - 14, r - 2, -90, 270, 60, P['ink'], 1.4, 5, r - 26)
    nums = "".join(
        f'<text x="{cx + (r - 46) * math.cos(math.radians(-90 + 30 * i)):.1f}" y="{cy + (r - 46) * math.sin(math.radians(-90 + 30 * i)) + 6:.1f}" '
        f'text-anchor="middle" font-size="16" font-weight="500" fill="{P["ink"]}">{12 if i == 0 else i}</text>' for i in range(12))

    def hand(angle, length, width, color, tail=0):
        a = math.radians(angle - 90)
        return (f'<line x1="{cx - tail * math.cos(a):.1f}" y1="{cy - tail * math.sin(a):.1f}" x2="{cx + length * math.cos(a):.1f}" '
                f'y2="{cy + length * math.sin(a):.1f}" stroke="{color}" stroke-width="{width}"/>')
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0;font-family:Inter">
<circle cx="{cx}" cy="{cy}" r="{r}" fill="{P['face']}" stroke="{P['line']}" stroke-width="1"/>
{ticks}{nums}
{hand((hh % 12 + mm / 60) * 30, 70, 7, P['ink'], 14)}{hand(mm * 6, 106, 5, P['ink'], 18)}{hand(ss * 6, 116, 1.6, P['acc'], 28)}
<circle cx="{cx}" cy="{cy}" r="6" fill="{P['acc']}"/></svg>"""
    body = f"""{svg}
<div style="position:absolute;left:328px;top:48px;width:136px">
 <div class="lbl">Saturday</div>
 <div style="font-size:56px;font-weight:300;line-height:1;margin-top:8px">07</div>
 <div class="lbl" style="margin-top:8px">November 2026</div>
 <div style="height:1px;background:{P['line']};margin:24px 0"></div>
 <div class="lbl">Next</div>
 <div class="val" style="margin-top:6px">Design review</div>
 <div class="lbl" style="margin-top:4px;color:{P['acc']}">11:30 · in 48 min</div>
</div>"""
    page(f"{key}_clock", FONT_INTER, console_css(P), body)


def console_meter(P, x, y, value):
    w = 288
    ticks = []
    for i in range(0, 51):
        tx = x + w * i / 50
        major = i % 10 == 0
        ticks.append(f'<line x1="{tx:.1f}" y1="{y + (0 if major else 6)}" x2="{tx:.1f}" y2="{y + 14}" stroke="{P["ink"]}" stroke-width="{1.4 if major else 0.8}"/>')
        if major:
            ticks.append(f'<text x="{tx:.1f}" y="{y + 30}" text-anchor="middle" font-size="10" fill="{P["mute"]}">{i * 2}</text>')
    red = f'<rect x="{x + w * 0.8:.1f}" y="{y + 16}" width="{w * 0.2:.1f}" height="2" fill="{P["acc"]}"/>'
    px = x + w * value / 100
    pointer = (f'<polygon points="{px - 6:.1f},{y - 10} {px + 6:.1f},{y - 10} {px:.1f},{y + 2}" fill="{P["acc"]}"/>'
               f'<line x1="{px:.1f}" y1="{y}" x2="{px:.1f}" y2="{y + 18}" stroke="{P["acc"]}" stroke-width="2"/>')
    return "".join(ticks) + red + pointer


def console_system(key="console"):
    P = CONSOLE_PALETTES[key]
    parts, labels = [], []
    for i, (lab, v) in enumerate((("CPU", CPU), ("GPU", GPU), ("Memory", RAM))):
        parts.append(console_meter(P, 160, 56 + i * 80, v))
        labels.append(f'<div style="position:absolute;left:24px;top:{44 + i * 80}px;width:120px">'
                      f'<div class="lbl">{lab}</div><div style="font-size:30px;font-weight:300;line-height:1.1;font-variant-numeric:tabular-nums">{v}'
                      f'<span style="font-size:14px;color:{P["mute"]}"> %</span></div></div>')
    body = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0;font-family:Inter">{''.join(parts)}
<line x1="24" y1="288" x2="456" y2="288" stroke="{P['line']}"/></svg>
{''.join(labels)}
<div style="position:absolute;left:24px;right:24px;top:304px;display:flex;justify-content:space-between">
 <div><div class="lbl">CPU temp</div><div class="val">{TEMP} °C</div></div>
 <div><div class="lbl">Down</div><div class="val">{NET_DN} MB/s</div></div>
 <div><div class="lbl">Up</div><div class="val">{NET_UP} MB/s</div></div>
 <div><div class="lbl">Uptime</div><div class="val">3 d 04 h</div></div>
</div>"""
    page(f"{key}_system", FONT_INTER, console_css(P), body)


def console_now(key="console"):
    P = CONSOLE_PALETTES[key]
    x0, x1, y = 24, 456, 216
    t = []
    for i in range(0, 87):
        tx = x0 + (x1 - x0) * i / 86
        major = i % 10 == 0
        t.append(f'<line x1="{tx:.1f}" y1="{y - (14 if major else 7)}" x2="{tx:.1f}" y2="{y}" stroke="{P["ink"]}" stroke-width="{1.4 if major else .8}"/>')
    for i, f in enumerate(("88", "90", "92", "94", "96", "98", "100", "102", "104")):
        tx = x0 + (x1 - x0) * (i * 10 + 3) / 86
        t.append(f'<text x="{tx:.1f}" y="{y + 18}" text-anchor="middle" font-size="10" fill="{P["mute"]}">{f}</text>')
    px = x0 + (x1 - x0) * PROG
    t.append(f'<rect x="{px - 1.5:.1f}" y="{y - 36}" width="3" height="46" fill="{P["acc"]}"/>')

    def btn(cx, inner, fill=None, stroke=None):
        return f'<circle cx="{cx}" cy="308" r="20" fill="{fill or P["btn"]}" stroke="{stroke or P["line"]}"/>{inner}'
    tri = lambda cx: f'<polygon points="{cx - 5},300 {cx - 5},316 {cx + 8},308" fill="{P["bg"]}"/>'
    prev = lambda cx: f'<polygon points="{cx + 6},301 {cx + 6},315 {cx - 3},308" fill="{P["btn_ink"]}"/><rect x="{cx - 6}" y="301" width="2" height="14" fill="{P["btn_ink"]}"/>'
    nxt = lambda cx: f'<polygon points="{cx - 6},301 {cx - 6},315 {cx + 3},308" fill="{P["btn_ink"]}"/><rect x="{cx + 4}" y="301" width="2" height="14" fill="{P["btn_ink"]}"/>'
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0;font-family:Inter">{''.join(t)}
{btn(192, prev(192))}{btn(240, tri(240), P['acc'], P['acc'])}{btn(288, nxt(288))}</svg>"""
    body = f"""<div style="position:absolute;left:24px;top:32px;right:24px">
 <div class="lbl">Now playing · {ALBUM}</div>
 <div style="font-size:34px;font-weight:300;line-height:1.1;margin-top:16px">{TRACK}</div>
 <div style="font-size:15px;font-weight:500;margin-top:8px">{ARTIST}</div>
</div>
{svg}
<div class="val" style="position:absolute;left:24px;top:248px">{POS}</div>
<div class="val" style="position:absolute;right:24px;top:248px;color:{P['mute']}">{DUR}</div>"""
    page(f"{key}_now", FONT_INTER, console_css(P), body)


# ================================================================================================ VFD
V_BG, V_ON, V_OFF, V_DIM = "#06090A", "#7CF5DA", "#10221F", "#3E8C7C"
vfd_css = f"""body{{background:radial-gradient(ellipse at 50% 40%,#0B1413 0%,{V_BG} 70%);color:{V_ON};font-family:Doto,monospace}}
.lbl{{font-size:13px;font-weight:700;letter-spacing:.06em;color:{V_DIM}}}
.on{{color:{V_ON};text-shadow:0 0 6px rgba(124,245,218,.55)}}
.grid{{position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.25) 0 1px,transparent 1px 3px)}}"""
GLOW = """<defs><filter id="g" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2.2" result="b"/>
<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>"""


def vfd_clock():
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">{GLOW}
{seven('10:42', 36, 104, 112, V_ON, V_OFF, 'g', slant=0.08)}
{seven('18', 388, 168, 48, V_ON, V_OFF, 'g', slant=0.08)}</svg>"""
    annun = lambda x, txt, lit: f'<div class="lbl {"on" if lit else ""}" style="position:absolute;left:{x}px;top:56px">{txt}</div>'
    body = f"""{svg}
{annun(44,'AM',True)}{annun(84,'PM',False)}{annun(124,'ALARM',False)}{annun(196,'SNOOZE',False)}{annun(388,'SEC',True)}
<div style="position:absolute;left:44px;top:268px;font-size:30px;font-weight:700;letter-spacing:.08em" class="on">SAT 07 NOV</div>
<div style="position:absolute;right:44px;top:276px;font-size:20px;font-weight:700" class="lbl">2026</div>
<div class="grid"></div>"""
    page("vfd_clock", "family=Doto:wght@700", vfd_css, body)


def vfd_bar(y, label, value, blocks=20):
    lit = round(value / 100 * blocks)
    glow = 'filter="url(#g)"'
    cells = "".join(
        f'<rect x="{120 + i * 13}" y="{y}" width="10" height="22" fill="{V_ON if i < lit else V_OFF}" {glow if i < lit else ""}/>'
        for i in range(blocks))
    return cells


def vfd_system():
    rows = []
    svg = [GLOW]
    for i, (lab, v) in enumerate((("CPU", CPU), ("GPU", GPU), ("MEM", RAM), ("TMP", TEMP))):
        y = 48 + i * 52
        svg.append(vfd_bar(y, lab, v))
        rows.append(f'<div class="on" style="position:absolute;left:32px;top:{y - 2}px;font-size:24px;font-weight:700">{lab}</div>')
        rows.append(f'<div class="on" style="position:absolute;left:396px;width:56px;text-align:right;top:{y - 2}px;font-size:24px;font-weight:700">{v}</div>')
    for k, x in enumerate((120, 120 + 13 * 5, 120 + 13 * 10, 120 + 13 * 15, 120 + 13 * 20)):
        svg.append(f'<text x="{x - 1.5}" y="258" text-anchor="middle" font-family="Doto" font-weight="700" font-size="12" fill="{V_DIM}">{k * 25}</text>')
    body = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">{''.join(svg)}</svg>{''.join(rows)}
<div class="lbl" style="position:absolute;left:32px;top:300px">RX</div><div class="on" style="position:absolute;left:72px;top:292px;font-size:30px;font-weight:700">{NET_DN}</div>
<div class="lbl" style="position:absolute;left:236px;top:300px">TX</div><div class="on" style="position:absolute;left:276px;top:292px;font-size:30px;font-weight:700">{NET_UP}</div>
<div class="lbl" style="position:absolute;right:28px;top:300px">MB/S</div>
<div class="grid"></div>"""
    page("vfd_system", "family=Doto:wght@700", vfd_css, body)


def vfd_now():
    import random
    rng = random.Random(4)
    bands = [0.35, 0.55, 0.8, 0.95, 0.7, 0.62, 0.78, 0.5, 0.44, 0.58, 0.4, 0.3, 0.36, 0.22, 0.18, 0.12]
    svg = [GLOW]
    for i, b in enumerate(bands):
        x = 32 + i * 26
        for j in range(10):
            on = j < round(b * 10)
            peak = j == round(b * 10)
            col = V_ON if (on or peak) else V_OFF
            f = 'filter="url(#g)"' if (on or peak) else ""
            svg.append(f'<rect x="{x}" y="{220 - j * 11}" width="20" height="7" fill="{col}" {f}/>')
    svg.append(seven(" 214", 300, 262, 52, V_ON, V_OFF, "g", slant=0.08))
    body = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">{''.join(svg)}</svg>
<div class="on" style="position:absolute;left:32px;top:24px;font-size:26px;font-weight:700;white-space:nowrap">{TRACK.upper()}</div>
<div class="lbl" style="position:absolute;left:32px;top:62px;font-size:16px">{ARTIST.upper()}</div>
<div class="lbl" style="position:absolute;left:32px;top:248px">31  63  125  250  500  1K  2K  4K  8K  16K</div>
<div class="lbl on" style="position:absolute;left:32px;top:280px;font-size:20px">▶ PLAY</div>
<div class="lbl" style="position:absolute;left:150px;top:280px;font-size:20px">REPEAT</div>
<div class="lbl" style="position:absolute;left:300px;top:322px">TRACK 03</div>
<div class="grid"></div>"""
    page("vfd_now", "family=Doto:wght@700", vfd_css, body)


# ======================================================================================== AMBER / NIXIE
A_BG, A_ON, A_DIM, A_FAINT = "#0E0703", "#FFB23E", "#8A5A1A", "#2A1606"
amber_css = f"""body{{background:radial-gradient(ellipse at 50% 45%,#1A0D04 0%,{A_BG} 75%);color:{A_ON};font-family:'Share Tech Mono',monospace}}
.t{{font-family:VT323,monospace;color:{A_ON};text-shadow:0 0 5px rgba(255,170,50,.6)}}
.d{{color:{A_DIM}}}
.scan{{position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.32) 0 1px,transparent 1px 3px);pointer-events:none}}
.vig{{position:absolute;inset:0;box-shadow:inset 0 0 80px rgba(0,0,0,.85);pointer-events:none}}"""


def nixie_tube(x, digit):
    # glass tube with honeycomb mesh and stacked faint numerals behind the lit one
    ghost = "".join(f'<text x="{x + 34}" y="214" text-anchor="middle" font-size="104" fill="none" stroke="{A_FAINT}" stroke-width="1.2" font-family="IBM Plex Sans Condensed" font-weight="300">{d}</text>' for d in "0123456789")
    return f"""<rect x="{x}" y="76" width="68" height="168" rx="30" fill="rgba(255,190,120,.035)" stroke="#5A3A1C" stroke-width="1.5"/>
<rect x="{x + 6}" y="86" width="56" height="148" rx="24" fill="url(#mesh)"/>
{ghost}
<text x="{x + 34}" y="214" text-anchor="middle" font-size="104" fill="#FFC56A" font-family="IBM Plex Sans Condensed" font-weight="300" filter="url(#ng)">{digit}</text>
<rect x="{x + 14}" y="244" width="40" height="10" fill="#2A1A0C"/>"""


def amber_clock():
    tubes = "".join(nixie_tube(x, d) for x, d in ((36, "1"), (112, "0"), (268, "4"), (344, "2")))
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">
<defs><pattern id="mesh" width="6" height="5.2" patternUnits="userSpaceOnUse"><path d="M3 0 L6 1.7 L6 3.5 L3 5.2 L0 3.5 L0 1.7 Z" fill="none" stroke="#3A2210" stroke-width=".6"/></pattern>
<filter id="ng" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
{tubes}
<circle cx="230" cy="136" r="6" fill="#FFB23E" filter="url(#ng)"/><circle cx="230" cy="186" r="6" fill="#FFB23E" filter="url(#ng)"/></svg>"""
    body = f"""{svg}
<div class="t" style="position:absolute;left:36px;top:24px;font-size:24px">CHRONOMETER // NEOS-75</div>
<div class="t d" style="position:absolute;right:36px;top:24px;font-size:24px">UTC+8</div>
<div class="t" style="position:absolute;left:36px;top:290px;font-size:30px;letter-spacing:.06em">SAT 07.11.2026</div>
<div class="t d" style="position:absolute;right:36px;top:296px;font-size:24px">SYNC OK</div>
<div class="scan"></div><div class="vig"></div>"""
    page("amber_clock", "family=VT323&family=IBM+Plex+Sans+Condensed:wght@300", amber_css, body)


def amber_system():
    def bar(v, n=24):
        k = round(v / 100 * n)
        cells = "".join(f'<i class="{"c on" if i < k else "c"}"></i>' for i in range(n))
        return f'<span class="bar">{cells}</span>'
    spark = [3, 4, 5, 7, 6, 5, 4, 5, 7, 9, 8, 7, 5, 4, 4, 5, 6, 5, 4, 3, 4, 5, 5, 4]
    sp = "".join(f'<i style="height:{h * 2}px"></i>' for h in spark)
    css = amber_css + """
.row{display:grid;grid-template-columns:56px 1fr 56px;align-items:center;height:32px}
.bar{display:flex;gap:3px}.c{display:block;width:9px;height:16px;background:#2A1606}
.c.on{background:#FFB23E;box-shadow:0 0 5px rgba(255,170,50,.6)}
.rule{border-top:1px solid #5A3A12;margin:6px 0}
.kv{display:flex;justify-content:space-between;height:26px}
.sp{display:flex;align-items:flex-end;gap:3px;height:20px}.sp i{display:block;width:6px;background:#FFB23E;box-shadow:0 0 4px rgba(255,170,50,.5)}"""
    body = f"""<div class="t" style="position:absolute;left:28px;right:28px;top:18px;font-size:23px">
<div class="kv"><span>SYS.MON // NODE 75</span><span class="d">T+03:04:12</span></div>
<div class="rule"></div>
<div class="row"><span>CPU</span>{bar(CPU)}<span style="text-align:right">{CPU}%</span></div>
<div class="row"><span>GPU</span>{bar(GPU)}<span style="text-align:right">{GPU}%</span></div>
<div class="row"><span>MEM</span>{bar(RAM)}<span style="text-align:right">{RAM}%</span></div>
<div class="rule"></div>
<div class="kv"><span>CORE TEMP</span><span>{TEMP} C <span class="d">NOMINAL</span></span></div>
<div class="kv"><span>LINK RX / TX</span><span>{NET_DN} / {NET_UP} MB/S</span></div>
<div class="kv" style="align-items:flex-end"><span class="d">LOAD 60S</span><span class="sp">{sp}</span></div>
<div class="rule"></div>
<div>&gt; ALL SYSTEMS NOMINAL<span style="background:#FFB23E;color:#0E0703">_</span></div>
</div><div class="scan"></div><div class="vig"></div>"""
    page("amber_system", "family=VT323", css, body)


def amber_now():
    def reel(cx, tape_r):
        spokes = "".join(
            f'<rect x="{cx - 3}" y="{150 - 20}" width="6" height="14" fill="{A_BG}" transform="rotate({a} {cx} 150)"/>' for a in (0, 60, 120, 180, 240, 300))
        return (f'<circle cx="{cx}" cy="150" r="{tape_r}" fill="#3B220D"/>'
                f'<circle cx="{cx}" cy="150" r="26" fill="none" stroke="{A_ON}" stroke-width="3" filter="url(#ag)"/>'
                f'<circle cx="{cx}" cy="150" r="20" fill="{A_ON}" filter="url(#ag)"/>{spokes}<circle cx="{cx}" cy="150" r="6" fill="{A_BG}"/>')
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">
<defs><filter id="ag" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
<rect x="64" y="62" width="352" height="196" rx="14" fill="none" stroke="{A_DIM}" stroke-width="2"/>
<rect x="88" y="78" width="304" height="40" rx="4" fill="none" stroke="{A_DIM}" stroke-width="1.5"/>
<rect x="148" y="122" width="184" height="56" rx="6" fill="#1A0D05" stroke="{A_DIM}" stroke-width="1.5"/>
{reel(180, 52)}{reel(300, 34)}
<path d="M140 232 L172 210 L308 210 L340 232" fill="none" stroke="{A_DIM}" stroke-width="1.5"/>
<rect x="64" y="292" width="352" height="6" fill="{A_FAINT}"/><rect x="64" y="292" width="{352 * PROG:.0f}" height="6" fill="{A_ON}" filter="url(#ag)"/></svg>"""
    body = f"""{svg}
<div class="t" style="position:absolute;left:100px;top:82px;font-size:26px;white-space:nowrap">{TRACK.upper()}</div>
<div class="t d" style="position:absolute;left:96px;top:214px;font-size:22px">SIDE A</div>
<div class="t d" style="position:absolute;right:96px;top:214px;font-size:22px">C-90</div>
<div class="t" style="position:absolute;left:64px;top:20px;font-size:24px">{ARTIST.upper()}</div>
<div class="t d" style="position:absolute;right:64px;top:20px;font-size:24px">PLAY ▶</div>
<div class="t" style="position:absolute;left:64px;top:306px;font-size:26px">{POS}</div>
<div class="t d" style="position:absolute;right:64px;top:306px;font-size:26px">-3:24</div>
<div class="scan"></div><div class="vig"></div>"""
    page("amber_now", "family=VT323", amber_css, body)


# ========================================================================================== MILLENNIUM
# Y2K portable player: brushed silver body, backlit blue LCD with dark segments, glossy aqua buttons.
M_LCD1, M_LCD2, M_SEG, M_GHOST = "#B9E3F2", "#8CCBE4", "#0F2C3C", "rgba(15,44,60,.10)"
mill_css = f"""body{{background:
 repeating-linear-gradient(90deg,rgba(255,255,255,.07) 0 1px,rgba(0,0,0,.03) 1px 2px),
 linear-gradient(180deg,#E9ECEF 0%,#C9CED3 48%,#B3B9BF 52%,#D4D8DC 100%);font-family:Tahoma,Verdana,sans-serif;color:#1E2A33}}
.lcd{{position:absolute;border-radius:10px;background:linear-gradient(180deg,{M_LCD1},{M_LCD2});
 box-shadow:inset 0 2px 6px rgba(0,30,50,.45),0 1px 0 rgba(255,255,255,.9);border:1px solid #6C7A84}}
.lcdt{{color:{M_SEG};font-family:Tahoma,Verdana,sans-serif}}
.bezel{{position:absolute;border-radius:14px;background:linear-gradient(180deg,#9AA3AA,#E6E9EC);padding:3px}}
.brand{{font-size:11px;font-weight:bold;letter-spacing:.2em;color:#5D6B75;text-shadow:0 1px 0 #fff}}
.btn{{position:absolute;width:52px;height:52px;border-radius:50%;
 background:radial-gradient(circle at 50% 30%,#E9FBFF 0%,#7FD3F2 35%,#1B86C4 75%,#0F5E97 100%);
 box-shadow:0 2px 4px rgba(0,0,0,.35),inset 0 -3px 6px rgba(0,40,80,.4);border:1px solid #0D4F7E;color:#fff;
 display:flex;align-items:center;justify-content:center;font-size:18px;text-shadow:0 1px 1px rgba(0,0,0,.5)}}"""


def mill_clock():
    svg = f"""<svg width="420" height="200" style="position:absolute;left:30px;top:70px">
{seven('88:88', 16, 40, 104, M_GHOST, 'none')}{seven('10:42', 16, 40, 104, M_SEG, 'none')}
{seven('18', 342, 98, 46, M_SEG, M_GHOST)}</svg>"""
    body = f"""<div class="brand" style="position:absolute;left:30px;top:24px">NEOS · DIGITAL AUDIO</div>
<div class="brand" style="position:absolute;right:30px;top:24px">MILLENNIUM EDITION</div>
<div class="lcd" style="left:24px;top:52px;width:432px;height:236px"></div>
{svg}
<div class="lcdt" style="position:absolute;left:52px;top:72px;font-size:15px;font-weight:bold">SAT</div>
<div class="lcdt" style="position:absolute;left:96px;top:72px;font-size:15px">07 · 11 · 2026</div>
<div class="lcdt" style="position:absolute;right:52px;top:72px;font-size:13px;font-weight:bold;border:1.5px solid {M_SEG};padding:0 4px;border-radius:3px">AM</div>
<div class="lcdt" style="position:absolute;left:52px;top:252px;font-size:13px">◉ ALARM 07:00</div>
<div class="lcdt" style="position:absolute;right:52px;top:252px;font-size:13px">▮▮▮▯ BATT</div>
<div style="position:absolute;left:0;right:0;top:304px;display:flex;justify-content:center;gap:10px">
 <div style="width:120px;height:28px;border-radius:14px;background:linear-gradient(180deg,#F7F8F9,#B9C0C6);border:1px solid #8A949B;box-shadow:inset 0 1px 0 #fff"></div>
</div>"""
    page("mill_clock", "", mill_css, body)


def mill_system():
    def graph(x, y, w, h, pts, label, val):
        poly = " ".join(f"{x + w * i / (len(pts) - 1):.1f},{y + h - h * p:.1f}" for i, p in enumerate(pts))
        grid = "".join(f'<line x1="{x}" y1="{y + h * k / 4:.1f}" x2="{x + w}" y2="{y + h * k / 4:.1f}" stroke="rgba(15,44,60,.18)"/>' for k in range(1, 4))
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{M_SEG}" stroke-width="1"/>{grid}'
                f'<polyline points="{poly}" fill="none" stroke="{M_SEG}" stroke-width="2"/>'
                f'<text x="{x}" y="{y - 6}" font-size="12" font-weight="bold" fill="{M_SEG}">{label}</text>'
                f'<text x="{x + w}" y="{y - 6}" font-size="12" text-anchor="end" fill="{M_SEG}">{val}</text>')
    import random
    rng = random.Random(7)
    cpu = [min(.95, max(.08, .37 + rng.uniform(-.18, .2))) for _ in range(24)]
    gpu = [min(.95, max(.08, .52 + rng.uniform(-.2, .2))) for _ in range(24)]
    ram = [.58 + i * .001 for i in range(24)]
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0;font-family:Tahoma">
{graph(48, 84, 180, 70, cpu, 'CPU', f'{CPU}%')}{graph(252, 84, 180, 70, gpu, 'GPU', f'{GPU}%')}
{graph(48, 190, 180, 70, ram, 'MEMORY', f'{RAM}%')}</svg>"""
    body = f"""<div class="brand" style="position:absolute;left:30px;top:24px">SYSTEM MONITOR</div>
<div class="brand" style="position:absolute;right:30px;top:24px">NEOS · 2000</div>
<div class="lcd" style="left:24px;top:52px;width:432px;height:236px"></div>{svg}
<div class="lcdt" style="position:absolute;left:252px;top:178px;font-size:12px;line-height:20px">
 <b>TEMP</b>&nbsp;&nbsp;{TEMP} °C<br><b>DOWN</b>&nbsp;&nbsp;{NET_DN} MB/s<br><b>UP</b>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{NET_UP} MB/s<br><b>UPTIME</b> 3d 04h</div>
<div style="position:absolute;left:30px;top:306px" class="brand">CPU · GPU · MEM</div>
<div style="position:absolute;right:30px;top:306px;width:140px;height:12px;border-radius:6px;background:linear-gradient(180deg,#7FD3F2,#1B86C4);box-shadow:inset 0 1px 0 rgba(255,255,255,.7);border:1px solid #0D4F7E"></div>"""
    page("mill_system", "", mill_css, body)


def mill_now():
    eq = "".join(f'<div style="width:10px;height:{h}px;background:{M_SEG};opacity:.85"></div>' for h in (10, 18, 26, 22, 14, 30, 24, 16, 20, 12, 8, 14))
    body = f"""<div class="brand" style="position:absolute;left:30px;top:20px">NEOS · DIGITAL AUDIO</div>
<div class="brand" style="position:absolute;right:30px;top:20px">MP3 · WMA</div>
<div class="lcd" style="left:24px;top:44px;width:432px;height:192px"></div>
<div class="lcdt" style="position:absolute;left:44px;top:60px;font-size:12px;font-weight:bold">TRACK 03/12&nbsp;&nbsp;&nbsp;▶ PLAY&nbsp;&nbsp;&nbsp;128kbps&nbsp;&nbsp;&nbsp;RPT</div>
<div class="lcdt" style="position:absolute;left:44px;top:88px;font-size:26px;font-weight:bold;white-space:nowrap">{TRACK}</div>
<div class="lcdt" style="position:absolute;left:44px;top:124px;font-size:15px">{ARTIST} — {ALBUM}</div>
<div style="position:absolute;left:44px;top:156px;width:392px;height:8px;border:1px solid {M_SEG};border-radius:4px">
 <div style="width:{PROG * 100:.0f}%;height:100%;background:{M_SEG};border-radius:3px"></div></div>
<div class="lcdt" style="position:absolute;left:44px;top:172px;font-size:13px">{POS}</div>
<div class="lcdt" style="position:absolute;right:44px;top:172px;font-size:13px">{DUR}</div>
<div style="position:absolute;right:44px;top:194px;display:flex;align-items:flex-end;gap:3px;height:32px">{eq}</div>
<div class="btn" style="left:130px;top:262px">⏮</div><div class="btn" style="left:200px;top:254px;width:68px;height:68px;font-size:24px">▶</div>
<div class="btn" style="left:286px;top:262px">⏭</div>"""
    page("mill_now", "", mill_css, body)


# ========================================================================================== RENAISSANCE
R_PAPER, R_INK, R_RED, R_FADE = "#EFE5CF", "#2A2017", "#9A2E1A", "#8C7B62"
ren_css = f"""body{{background:radial-gradient(ellipse at 50% 45%,#F4EBD7 0%,{R_PAPER} 55%,#E2D3B3 100%);color:{R_INK};font-family:'EB Garamond',serif}}
.sc{{font-family:'Cormorant SC',serif;letter-spacing:.08em}}
.frame{{position:absolute;left:12px;top:12px;right:12px;bottom:12px;border:1.5px solid {R_INK}}}
.frame2{{position:absolute;left:17px;top:17px;right:17px;bottom:17px;border:.6px solid {R_INK}}}"""



def orn(color, w=40):
    """Small typographic ornament: a lozenge between two hairlines."""
    return (f'<svg width="{w}" height="12" style="vertical-align:middle"><line x1="0" y1="6" x2="{w / 2 - 7}" y2="6" stroke="{color}" stroke-width=".8"/>'
            f'<path d="M{w / 2} 1 L{w / 2 + 5} 6 L{w / 2} 11 L{w / 2 - 5} 6 Z" fill="{color}"/>'
            f'<line x1="{w / 2 + 7}" y1="6" x2="{w}" y2="6" stroke="{color}" stroke-width=".8"/></svg>')


def ren_clock():
    cx, cy, r = 176, 180, 132
    romans = ["XII", "I", "II", "III", "IIII", "V", "VI", "VII", "VIII", "IX", "X", "XI"]
    nums = "".join(
        f'<text x="0" y="0" font-family="EB Garamond" font-size="17" text-anchor="middle" fill="{R_INK}" '
        f'transform="translate({cx + (r - 30) * math.sin(math.radians(30 * i)):.1f} {cy - (r - 30) * math.cos(math.radians(30 * i)):.1f}) rotate({30 * i}) translate(0 6)">{n}</text>'
        for i, n in enumerate(romans))
    ticks = arc_ticks(cx, cy, r - 10, r - 4, -90, 270, 60, R_INK, .8)
    hh, mm = 10, 42
    def fleur_hand(angle, length, width):
        return (f'<g transform="rotate({angle} {cx} {cy})"><path d="M{cx} {cy + 14} L{cx - width} {cy} L{cx} {cy - length} L{cx + width} {cy} Z" fill="{R_INK}"/>'
                f'<circle cx="{cx}" cy="{cy - length * .62}" r="{width * 1.1}" fill="none" stroke="{R_INK}" stroke-width="1.2"/></g>')
    sun = "".join(f'<line x1="{cx}" y1="{cy}" x2="{cx + 22 * math.cos(math.radians(a)):.1f}" y2="{cy + 22 * math.sin(math.radians(a)):.1f}" stroke="{R_RED}" stroke-width="1"/>' for a in range(0, 360, 15))
    svg = f"""<svg width="480" height="360" style="position:absolute;left:0;top:0">
<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{R_INK}" stroke-width="1.6"/>
<circle cx="{cx}" cy="{cy}" r="{r - 4}" fill="none" stroke="{R_INK}" stroke-width=".6"/>
<circle cx="{cx}" cy="{cy}" r="{r - 50}" fill="none" stroke="{R_INK}" stroke-width=".6"/>
{ticks}{nums}{sun}
{fleur_hand((hh % 12 + mm / 60) * 30, 66, 5)}{fleur_hand(mm * 6, 100, 3.5)}
<circle cx="{cx}" cy="{cy}" r="9" fill="{R_RED}"/><circle cx="{cx}" cy="{cy}" r="3" fill="{R_PAPER}"/></svg>"""
    body = f"""<div class="frame"></div><div class="frame2"></div>{svg}
<div style="position:absolute;left:330px;top:58px;width:120px;text-align:center">
 <div class="sc" style="font-size:16px;color:{R_RED}">Sabbato</div>
 <div style="font-size:64px;line-height:1;margin-top:6px">7</div>
 <div class="sc" style="font-size:16px;margin-top:4px">Novembris</div>
 <div style="font-size:15px;font-style:italic;color:{R_FADE};margin-top:2px">Anno MMXXVI</div>
 <div style="margin:18px 0 10px">{orn(R_RED, 64)}</div>
 <div style="font-size:15px;font-style:italic;line-height:1.35">Hora decima,<br>minuta xlii</div>
</div>"""
    page("ren_clock", "family=EB+Garamond:ital,wght@0,400;1,400&family=Cormorant+SC:wght@500", ren_css, body)


def ren_system():
    def hatch(v):
        return (f'<div style="position:relative;height:12px;border:.8px solid {R_INK};width:200px">'
                f'<div style="position:absolute;left:0;top:0;bottom:0;width:{v * 2}px;'
                f'background:repeating-linear-gradient(-45deg,{R_INK} 0 1px,transparent 1px 4px)"></div></div>')
    rows = [("Cerebrum", "processor", CPU), ("Oculus", "graphics", GPU), ("Memoria", "memory", RAM)]
    trs = "".join(
        f'<tr><td style="padding:7px 0"><span class="sc" style="font-size:18px">{a}</span> '
        f'<i style="color:{R_FADE};font-size:14px">{b}</i></td><td style="padding:7px 12px">{hatch(v)}</td>'
        f'<td style="text-align:right;font-size:20px;font-variant-numeric:oldstyle-nums">{v}<span style="font-size:13px">%</span></td></tr>'
        for a, b, v in rows)
    body = f"""<div class="frame"></div><div class="frame2"></div>
<div style="position:absolute;left:40px;top:34px;right:40px">
 <div style="display:flex;align-items:baseline;gap:10px"><span style="font-size:46px;line-height:.8;color:{R_RED};font-family:'Cormorant SC'">M</span>
 <span class="sc" style="font-size:20px">achinae Status</span><span style="margin-left:auto;font-style:italic;color:{R_FADE}">folio vii</span></div>
 <div style="border-top:.8px solid {R_INK};margin-top:10px"></div>
 <table style="width:100%;border-collapse:collapse;margin-top:8px">{trs}</table>
 <div style="border-top:.8px solid {R_INK};margin-top:8px"></div>
 <div style="display:flex;justify-content:space-between;margin-top:12px;font-size:16px">
  <div><span class="sc" style="color:{R_RED}">Calor</span> {TEMP}°</div>
  <div><span class="sc" style="color:{R_RED}">Influxus</span> {NET_DN} <i style="font-size:13px">MB/s</i></div>
  <div><span class="sc" style="color:{R_RED}">Effluxus</span> {NET_UP} <i style="font-size:13px">MB/s</i></div>
 </div>
 <div style="text-align:center;margin-top:40px">{orn(R_RED, 56)} &nbsp; <i style="color:{R_FADE}">omnia recte procedunt</i> &nbsp; {orn(R_RED, 56)}</div>
</div>"""
    page("ren_system", "family=EB+Garamond:ital,wght@0,400;1,400&family=Cormorant+SC:wght@500", ren_css, body)


def ren_now():
    body = f"""<div class="frame"></div><div class="frame2"></div>
<div style="position:absolute;left:40px;top:34px;right:40px">
 <div class="sc" style="font-size:15px;color:{R_FADE}">Nunc canitur · ex libro <i>{ALBUM}</i></div>
 <div style="display:flex;margin-top:14px">
  <div style="font-family:'Cormorant SC';font-size:104px;line-height:.78;color:{R_RED};margin-right:10px;padding-top:6px">A</div>
  <div style="font-size:32px;line-height:1.1;font-style:italic;padding-top:6px">erial<br>Cartography</div>
 </div>
 <div class="sc" style="font-size:19px;margin-top:16px">{ARTIST}</div>
</div>
<svg width="480" height="360" style="position:absolute;left:0;top:0">
 <line x1="40" y1="262" x2="440" y2="262" stroke="{R_INK}" stroke-width=".8"/>
 <line x1="40" y1="258" x2="40" y2="266" stroke="{R_INK}"/><line x1="440" y1="258" x2="440" y2="266" stroke="{R_INK}"/>
 <line x1="40" y1="262" x2="{40 + 400 * PROG:.0f}" y2="262" stroke="{R_RED}" stroke-width="2.2"/>
 <path d="M{40 + 400 * PROG:.0f} 252 L{45 + 400 * PROG:.0f} 262 L{40 + 400 * PROG:.0f} 272 L{35 + 400 * PROG:.0f} 262 Z" fill="{R_RED}"/>
</svg>
<div style="position:absolute;left:40px;top:274px;font-style:italic;color:{R_FADE}">ii : xiv</div>
<div style="position:absolute;right:40px;top:274px;font-style:italic;color:{R_FADE}">v : xxxviii</div>
<svg width="480" height="40" style="position:absolute;left:0;top:296px">
 <path d="M206 20 L218 12 L218 28 Z" fill="{R_INK}"/><rect x="202" y="12" width="2" height="16" fill="{R_INK}"/>
 <rect x="234" y="11" width="4" height="18" fill="{R_RED}"/><rect x="243" y="11" width="4" height="18" fill="{R_RED}"/>
 <path d="M274 20 L262 12 L262 28 Z" fill="{R_INK}"/><rect x="276" y="12" width="2" height="16" fill="{R_INK}"/>
 <line x1="120" y1="20" x2="186" y2="20" stroke="{R_INK}" stroke-width=".6"/><line x1="294" y1="20" x2="360" y2="20" stroke="{R_INK}" stroke-width=".6"/></svg>"""
    page("ren_now", "family=EB+Garamond:ital,wght@0,400;1,400&family=Cormorant+SC:wght@500", ren_css, body)


ALL = [lambda: console_clock("console"), lambda: console_system("console"), lambda: console_now("console"),
       lambda: console_clock("console_light"), lambda: console_system("console_light"), lambda: console_now("console_light"),
       vfd_clock, vfd_system, vfd_now, amber_clock, amber_system,
       amber_now, mill_clock, mill_system, mill_now, ren_clock, ren_system, ren_now]


def render(name: str) -> None:
    html = (OUT / f"{name}.html").resolve().as_uri()
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={OUT / 'prof'}",
                    "--window-size=480,360", "--virtual-time-budget=8000", f"--screenshot={OUT / (name + '.png')}", html],
                   check=True, capture_output=True)


if __name__ == "__main__":
    import sys
    only = sys.argv[1:]
    for fn in ALL:
        fn()
    for html in sorted(OUT.glob("*.html")):
        name = html.stem
        if name == "t" or (only and name not in only):
            continue
        render(name)
        print("rendered", name)
