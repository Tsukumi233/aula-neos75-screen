// The keyboard's pages (in place of Aula Hub): keys, knobs, macros, advanced keys, lighting, performance, device.
// Everything except the PC-side knob actions is written into the keyboard over VIA / Vial by the service
// (/api/keyboard/via*, sdk/src/neos75screen/via.py). Uses app.js globals ($, $$, el, api, act, toast, S).
"use strict";

const KB = {
  state: null, catalog: null, knobs: null, view: null, layer: 0, target: null, busy: false, loading: null, error: null,
  pickerGroup: null, pickerQuery: "",
  macroSel: 0, macroDraft: null, macroPick: null, recording: null,
  tdDraft: null, tdTarget: null,
};
const KB_VIEWS = { keys: renderKeys, knobs: renderKnobs, macros: renderMacros, advanced: renderAdvanced,
                   lighting: renderLighting, performance: renderPerformance, device: renderDevice };
const LAYER_NAMES = ["基础层", "Fn 层", "第 2 层"];
const LINK_NAMES = { wired: "有线", "2.4g": "2.4G 接收器" };
const KNOB_ENCODER = { screen: 1, volume: 0 };
const TD_FIELDS = [["tap", "点按"], ["hold", "按住"], ["double_tap", "双击"], ["tap_hold", "点按后按住"]];
const MACRO_TYPES = [["tap", "点按"], ["down", "按下"], ["up", "松开"], ["delay", "延时"], ["text", "文字"]];
const QK_MACRO = 0x7700, QK_TD = 0x5700;
// KeyboardEvent.code -> HID usage (the basic keycode), for "press the key you want" and macro recording
const HID = (() => {
  const m = {};
  "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").forEach((c, i) => (m[`Key${c}`] = 0x04 + i));
  "1234567890".split("").forEach((c, i) => (m[`Digit${c}`] = 0x1e + i));
  for (let n = 1; n <= 12; n++) m[`F${n}`] = 0x39 + n;
  for (let n = 13; n <= 24; n++) m[`F${n}`] = 0x68 + n - 13;
  for (let n = 1; n <= 9; n++) m[`Numpad${n}`] = 0x59 + n - 1;
  Object.assign(m, { Numpad0: 0x62, Enter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c, Minus: 0x2d,
    Equal: 0x2e, BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31, Semicolon: 0x33, Quote: 0x34, Backquote: 0x35,
    Comma: 0x36, Period: 0x37, Slash: 0x38, CapsLock: 0x39, PrintScreen: 0x46, ScrollLock: 0x47, Pause: 0x48,
    Insert: 0x49, Home: 0x4a, PageUp: 0x4b, Delete: 0x4c, End: 0x4d, PageDown: 0x4e, ArrowRight: 0x4f,
    ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52, NumLock: 0x53, NumpadDivide: 0x54, NumpadMultiply: 0x55,
    NumpadSubtract: 0x56, NumpadAdd: 0x57, NumpadEnter: 0x58, NumpadDecimal: 0x63, ContextMenu: 0x65,
    ControlLeft: 0xe0, ShiftLeft: 0xe1, AltLeft: 0xe2, MetaLeft: 0xe3, ControlRight: 0xe4, ShiftRight: 0xe5,
    AltRight: 0xe6, MetaRight: 0xe7 });
  return m;
})();

// --- data ------------------------------------------------------------------------------------------------
function labelOfSlot(kind, n) { return KB.state?.labels?.[kind]?.[String(n)] || ""; }

function describe(code) {
  if (code >= QK_MACRO && code < QK_MACRO + 0x20) {
    const n = code - QK_MACRO, name = labelOfSlot("macros", n);
    return { code, label: name || `M${n + 1}`, name: `宏 ${n + 1}${name ? ` · ${name}` : ""}` };
  }
  if (code >= QK_TD && code < QK_TD + 0x100) {
    const n = code - QK_TD, name = labelOfSlot("tap_dances", n);
    return { code, label: name || `TD${n + 1}`, name: `高级键 ${n + 1}${name ? ` · ${name}` : ""}` };
  }
  if (KB.catalog) for (const g of KB.catalog.keycodes) for (const k of g.keys) if (k.code === code) return k;
  return KB.state?.names?.[code] || { code, label: `0x${code.toString(16).padStart(4, "0").toUpperCase()}`, name: "" };
}
const nameOf = code => { const d = describe(code); return d.name || d.label; };

async function loadKeyboard(force = false) {
  if (KB.loading) return KB.loading;
  if (KB.state && !force) return KB.state;
  KB.loading = (async () => {
    try {
      KB.catalog = KB.catalog || await api("GET", "/api/keyboard/via/catalog");
      KB.state = await api("GET", "/api/keyboard/via");
      KB.error = null;
      KB.macroDraft = null; KB.tdDraft = null;
      KB.layer = Math.min(KB.layer, KB.state.layers - 1);
    } catch (e) {
      KB.error = e.message;
    } finally {
      KB.loading = null;
    }
    if (typeof renderTitleStatus === "function") renderTitleStatus();
    return KB.state;
  })();
  return KB.loading;
}

async function loadKnobs() {
  const d = await api("GET", "/api/knobs").catch(() => null);
  if (d) KB.knobs = d;
  return KB.knobs;
}

async function showKbView(name, force = false) {
  stopRecording();
  if (KB.view !== name) { KB.target = null; KB.tdTarget = null; KB.macroPick = null; KB.pickerQuery = ""; }
  KB.view = name;
  const body = $(`.view[data-view="${name}"] [data-kb-body]`);
  if (!KB.state || force) {
    body.replaceChildren(el("div", "dim kb-loading", "正在读取键盘…"));
    $(`.view[data-view="${name}"] [data-kb-tools]`).replaceChildren();
  }
  await Promise.all([loadKeyboard(force), name === "knobs" ? loadKnobs() : null]);
  if (KB.view === name) renderKb();
}

function renderKb() {
  const name = KB.view;
  if (!name || !KB_VIEWS[name]) return;
  const view = $(`.view[data-view="${name}"]`);
  if (!view || !view.classList.contains("active")) return;
  view.querySelector("[data-kb-tools]").replaceChildren(...kbTools(name));
  const body = view.querySelector("[data-kb-body]");
  if (!KB.state) {
    const retry = el("button", "pill", "重试");
    retry.onclick = () => showKbView(name, true);
    const box = el("div", "panel empty-state");
    box.append(el("div", "note warn", KB.error || "没有读到键盘"),
               el("div", "dim", "键盘要用 USB 线或 2.4G 接收器连着电脑（蓝牙下不能设置）。"), retry);
    body.replaceChildren(box);
    return;
  }
  const scroll = $("#views").scrollTop;
  body.replaceChildren(...[KB_VIEWS[name]()].flat().filter(Boolean));
  $("#views").scrollTop = scroll;
}

function kbTools(name) {
  const out = [];
  if (KB.state && (name === "keys" || name === "knobs")) out.push(layerBar());
  if (KB.state) {
    const s = KB.state;
    const link = s.simulated ? "模拟键盘" : LINK_NAMES[s.connection] || "已连接";
    const bat = s.battery ? ` · 电量 ${s.battery.percent}%${s.battery.charging ? "（充电中）" : ""}` : "";
    out.push(el("span", `dim kb-status${s.simulated ? " sim-text" : ""}`, `${link}${bat}`));
  }
  const refresh = el("button", "pill ghost", "刷新");
  refresh.title = "重新读取键盘";
  refresh.onclick = () => showKbView(name, true);
  out.push(refresh);
  return out;
}

function layerBar() {
  const bar = el("div", "seg kb-layers");
  for (let l = 0; l < KB.state.layers; l++) {
    const b = el("button", `pill${l === KB.layer ? " active" : ""}`, LAYER_NAMES[l] || `第 ${l} 层`);
    b.onclick = () => { KB.layer = l; KB.target = null; renderKb(); };
    bar.append(b);
  }
  return bar;
}

function where(code) {
  // "Fn 层 · 键 2,3" for every place a keycode is used (keys and knob turns)
  const s = KB.state, out = [];
  s.keymap.forEach((layer, l) => layer.forEach((row, r) => row.forEach((c, k) => { if (c === code) out.push(`${LAYER_NAMES[l]} ${r},${k}`); })));
  s.encoders.forEach((layer, l) => layer.forEach((e, i) => ["cw", "ccw"].forEach(g => {
    if (e[g] === code) out.push(`${LAYER_NAMES[l]} ${i === 1 ? "屏幕旋钮" : "音量旋钮"}${g === "cw" ? "顺转" : "逆转"}`);
  })));
  return out;
}

// --- key picker (shared) ------------------------------------------------------------------------------
function pickerGroups(opts) {
  const groups = KB.catalog.keycodes.map(g => ({ group: g.group, keys: opts.basicOnly ? g.keys.filter(k => k.code > 0 && k.code <= 0xff) : g.keys }))
    .filter(g => g.keys.length);
  if (!opts.basicOnly) {
    const s = KB.state;
    if (s.macro_space && !opts.noMacros)
      groups.splice(1, 0, { group: "宏", keys: [...Array(s.macro_space.count)].map((_, n) => describe(QK_MACRO + n)) });
    if (s.tap_dances && s.tap_dances.length && !opts.noTapDance)
      groups.splice(1, 0, { group: "高级键", keys: s.tap_dances.map((_, n) => describe(QK_TD + n)) });
  }
  return groups;
}

function keyPicker(opts) {
  // opts: {title, current, onPick(code), onClose, basicOnly, noMacros, noTapDance}
  const box = el("div", "kb-picker");
  const head = el("div", "kb-picker-head");
  head.append(el("span", "kb-picker-title", opts.title));
  if (opts.current != null) head.append(el("span", "dim", `现在：${nameOf(opts.current)}`));
  if (opts.onClose) {
    const close = el("button", "pill ghost round", "×");
    close.title = "关闭";
    close.onclick = opts.onClose;
    head.append(close);
  }
  box.append(head);

  const bar = el("div", "kb-picker-bar");
  const rec = el("input", "field kb-record");
  rec.readOnly = true;
  rec.placeholder = opts.basicOnly ? "点这里后按一个键" : "点这里后按一个键（可带 Ctrl/Shift/Alt/Win）";
  rec.onkeydown = e => {
    e.preventDefault(); e.stopPropagation();
    const code = HID[e.code];
    if (code == null) return;
    if (opts.basicOnly) { opts.onPick(code); return; }
    if (code >= 0xe0) return;   // a modifier alone: wait for the key
    const m = (e.ctrlKey ? 1 : 0) | (e.shiftKey ? 2 : 0) | (e.altKey ? 4 : 0) | (e.metaKey ? 8 : 0);
    opts.onPick(m ? (m << 8) | code : code);
  };
  const search = el("input", "field kb-search");
  search.placeholder = "搜索：音量、F5、宏…";
  search.value = KB.pickerQuery;
  bar.append(rec, search);
  box.append(bar);

  const groups = pickerGroups(opts);
  if (!groups.find(g => g.group === KB.pickerGroup)) KB.pickerGroup = groups[0].group;
  const tabs = el("div", "kb-group-tabs");
  const list = el("div", "kb-group-keys");
  const draw = () => {
    const q = KB.pickerQuery.trim().toLowerCase();
    tabs.replaceChildren(...groups.map(g => {
      const b = el("button", `kb-tab${!q && g.group === KB.pickerGroup ? " active" : ""}`, g.group);
      b.onclick = () => { KB.pickerGroup = g.group; KB.pickerQuery = ""; search.value = ""; draw(); };
      return b;
    }));
    const keys = q ? groups.flatMap(g => g.keys).filter(k => `${k.label} ${k.name}`.toLowerCase().includes(q))
                   : groups.find(g => g.group === KB.pickerGroup).keys;
    const seen = new Set();
    list.replaceChildren(...keys.filter(k => !seen.has(k.code) && seen.add(k.code)).map(k => {
      const b = el("button", `pill kb-code${k.code === opts.current ? " active" : ""}`, k.label || "无");
      b.title = k.name;
      b.onclick = () => opts.onPick(k.code);
      return b;
    }));
    if (!list.children.length) list.append(el("span", "dim", "没有找到"));
  };
  search.oninput = () => { KB.pickerQuery = search.value; draw(); };
  draw();
  box.append(tabs, list);
  setTimeout(() => box.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0);
  return box;
}

// --- keys ------------------------------------------------------------------------------------------------
function board(opts) {
  // opts: {layer, selected(k) -> bool, onKey(k, code)}
  const s = KB.state, keymap = s.keymap[opts.layer];
  const layout = s.layout || [...Array(s.rows)].flatMap((_, r) => [...Array(s.cols)].map((__, c) => ({ row: r, col: c, x: c, y: r, w: 1, h: 1 })));
  const U = 46, width = Math.max(...layout.map(k => k.x + k.w)), height = Math.max(...layout.map(k => k.y + k.h));
  const wrap = el("div", "kb-board");
  wrap.style.width = `${width * U}px`; wrap.style.height = `${height * U}px`;
  for (const k of layout) {
    const knob = k.encoder != null;
    const code = keymap[k.row][k.col];
    const d = describe(code);
    const special = (code >= QK_MACRO && code < QK_MACRO + 0x20) || (code >= QK_TD && code < QK_TD + 0x100);
    const key = el("button", `kb-key${knob ? " knob" : ""}${opts.selected(k) ? " sel" : ""}${code === 1 ? " trns" : ""}${special ? " special" : ""}`);
    Object.assign(key.style, { left: `${k.x * U}px`, top: `${k.y * U}px`, width: `${k.w * U - 5}px`, height: `${k.h * U - 5}px` });
    key.textContent = d.label;
    key.title = `${d.name || d.label}${knob ? `（${k.encoder === 1 ? "屏幕" : "音量"}旋钮按下；转动在「旋钮」）` : ""}`;
    key.onclick = () => opts.onKey(k, code);
    wrap.append(key);
  }
  const outer = el("div", "kb-board-wrap");
  outer.append(wrap);
  return outer;
}

function renderKeys() {
  const t = KB.target && KB.target.kind === "key" ? KB.target : null;
  const parts = [board({
    layer: KB.layer,
    selected: k => t && t.row === k.row && t.col === k.col,
    onKey: (k, code) => { KB.target = { kind: "key", row: k.row, col: k.col, code, encoder: k.encoder }; renderKb(); },
  })];
  if (t) {
    const knob = t.encoder != null ? ` · ${t.encoder === 1 ? "屏幕" : "音量"}旋钮按下` : "";
    const goKnob = t.encoder != null ? el("button", "pill ghost", "旋钮的转动 →") : null;
    if (goKnob) goKnob.onclick = () => showView("knobs");
    const picker = keyPicker({
      title: `${LAYER_NAMES[KB.layer]} · 键 ${t.row},${t.col}${knob}`, current: t.code,
      onClose: () => { KB.target = null; renderKb(); },
      onPick: code => writeKey(t, code),
    });
    if (goKnob) picker.querySelector(".kb-picker-head").insertBefore(goKnob, picker.querySelector(".kb-picker-head .round"));
    parts.push(picker);
  } else {
    parts.push(el("div", "dim kb-hint", "▽ = 透明（用下层的键）。金色边框的是宏或高级键。旋钮的转动在「旋钮」里设置。"));
  }
  return parts;
}

async function writeKey(t, code) {
  if (KB.busy) return;
  KB.busy = true;
  try {
    const d = await api("PUT", "/api/keyboard/via/key", { layer: KB.layer, row: t.row, col: t.col, keycode: code });
    KB.state.names[d.code] = d;
    KB.state.keymap[KB.layer][t.row][t.col] = d.code;
    if (t.encoder != null) KB.state.encoders[KB.layer][t.encoder].press = d.code;
    toast(`已写进键盘：${nameOf(d.code)}`);
    KB.target = null;
  } catch (e) { toast(e.message, true); }
  KB.busy = false;
  renderKb();
}

// --- knobs: one place for both knobs, written into the keyboard or handled by the PC ------------------------
function knobNotes() {
  const { config, status } = KB.knobs;
  const notes = [];
  const custom = (k, gs) => gs.some(g => config[k][g].action !== "default");
  if (!config.enabled) return notes;
  if (custom("screen", ["cw", "ccw", "hold_cw", "hold_ccw"])) {
    if (status.firmware_takeover === false)
      notes.push(["warn", "屏幕固件不支持接管旋钮转动（需要新版固件），屏幕旋钮转动的电脑端动作暂不生效。"]);
    else if (!status.connected)
      notes.push(["warn", "屏幕旋钮转动的电脑端动作由屏幕芯片转交：屏幕在显示时（「屏幕页面」→ 开始显示）才生效。"]);
  }
  if (custom("screen", ["cw", "ccw"]))
    notes.push(["", "屏幕旋钮转动交给电脑端后，屏幕不再随它翻页；想翻页可把某个动作设成“屏幕下一页/上一页”。"]);
  if (!status.pc_input) notes.push(["warn", "电脑端动作只支持 Windows。"]);
  else if (status.hook_error) notes.push(["warn", `电脑端接管没有启动：${status.hook_error}`]);
  return notes;
}

function pcActionLabel(entry) {
  const a = KB.knobs.catalog.actions.find(x => x.id === entry.action);
  const label = a ? a.label : entry.action;
  if (entry.action === "keys") return `${label} ${entry.keys || ""}`;
  if (entry.action === "run") return `${label} ${(entry.target || "").split(/[\\/]/).pop()}`;
  return label;
}

function renderKnobs() {
  if (!KB.knobs) return el("div", "note warn", "没有读到电脑端旋钮设置（屏幕服务）");
  const { config, catalog } = KB.knobs;
  const s = KB.state;
  const parts = [];

  const master = el("div", "panel knob-master");
  const text = el("div");
  text.append(el("div", null, "电脑端动作"),
              el("div", "dim", "开启后，下面标着“电脑端”的动作由本应用执行，代替键盘里的设置；关闭则旋钮完全按键盘里的设置工作。"));
  const sw = el("label", "switch");
  const input = el("input"); input.type = "checkbox"; input.checked = !!config.enabled;
  input.onchange = () => saveKnobConfig(c => { c.enabled = input.checked; });
  sw.append(input, el("i"));
  master.append(text, sw);
  parts.push(master);
  const notes = knobNotes();
  if (notes.length) {
    const box = el("div", "knob-notes");
    box.append(...notes.map(([cls, t]) => el("div", `note ${cls}`, t)));
    parts.push(box);
  }

  const cards = el("div", "knob-cards");
  for (const knob of catalog.knobs) {
    const enc = KNOB_ENCODER[knob.id];
    const card = el("div", "knob-card");
    card.append(el("h4", null, knob.label));
    for (const g of catalog.gestures) {
      const pc = config[knob.id][g.id];
      const pcOn = config.enabled && pc.action !== "default";
      const inKb = ["cw", "ccw", "press"].includes(g.id);
      const code = inKb ? s.encoders[KB.layer][enc][g.id] : null;
      const sel = KB.target && KB.target.kind === "knob" && KB.target.knob === knob.id && KB.target.gesture === g.id;
      const row = el("button", `gesture-row${sel ? " sel" : ""}`);
      row.dataset.gesture = `${knob.id}.${g.id}`;
      let value, badge;
      if (pcOn) { value = pcActionLabel(pc); badge = ["pc", "电脑端"]; }
      else if (inKb) { value = nameOf(code); badge = ["kb", `键盘 · ${LAYER_NAMES[KB.layer]}`]; }
      else { value = "同普通转动"; badge = ["off", "未设置"]; }
      row.append(el("span", "g-name", g.label), el("span", "g-value", value), el("span", `badge ${badge[0]}`, badge[1]));
      row.onclick = () => {
        KB.target = sel ? null : { kind: "knob", knob: knob.id, gesture: g.id, tab: pcOn || !inKb ? "pc" : "kb" };
        renderKb();
      };
      card.append(row);
    }
    cards.append(card);
  }
  parts.push(cards);
  if (KB.target && KB.target.kind === "knob") parts.push(knobEditor());
  else parts.push(el("div", "dim kb-hint", "点一个动作来修改。“键盘”里的设置按层保存（上面切换层）；“按住转动”只能由电脑端处理。"));
  return parts;
}

function knobEditor() {
  const t = KB.target, { config, catalog } = KB.knobs;
  const knob = catalog.knobs.find(k => k.id === t.knob), gesture = catalog.gestures.find(g => g.id === t.gesture);
  const enc = KNOB_ENCODER[t.knob];
  const inKb = ["cw", "ccw", "press"].includes(t.gesture);
  const box = el("div", "knob-editor");
  const head = el("div", "kb-picker-head");
  head.append(el("span", "kb-picker-title", `${knob.label} · ${gesture.label}`));
  const seg = el("div", "seg");
  for (const [id, label] of [["kb", "写进键盘"], ["pc", "电脑端动作"]]) {
    const b = el("button", `pill${t.tab === id ? " active" : ""}`, label);
    b.disabled = id === "kb" && !inKb;
    b.title = id === "kb" && !inKb ? "按住转动只能由电脑端处理" : "";
    b.onclick = () => { t.tab = id; renderKb(); };
    seg.append(b);
  }
  const close = el("button", "pill ghost round", "×");
  close.onclick = () => { KB.target = null; renderKb(); };
  head.append(seg, close);
  box.append(head);

  if (t.tab === "kb") {
    const pcOn = config.enabled && config[t.knob][t.gesture].action !== "default";
    if (pcOn) box.append(el("div", "note warn", "这个动作现在由电脑端处理；选一个键写进键盘后，电脑端的设置会清除。"));
    box.append(keyPicker({
      title: `写进键盘（${LAYER_NAMES[KB.layer]}）`, current: KB.state.encoders[KB.layer][enc][t.gesture],
      onPick: code => writeKnobKey(t, enc, code),
    }));
    return box;
  }
  // PC-side action
  const entry = { ...config[t.knob][t.gesture] };
  const stock = catalog.default[`${t.knob}.${t.gesture}`] || "同普通转动";
  const form = el("div", "pc-form");
  const select = el("select", "field");
  const groups = [];
  for (const a of catalog.actions) {
    let g = groups.find(x => x.name === a.group);
    if (!g) groups.push(g = { name: a.group, items: [] });
    g.items.push(a);
  }
  for (const g of groups) {
    const og = document.createElement("optgroup");
    og.label = g.name;
    for (const a of g.items) {
      const o = el("option", null, a.id === "default" ? (inKb ? "不用电脑端（按键盘里的设置）" : `不设置（${stock}）`) : a.label);
      o.value = a.id;
      og.append(o);
    }
    select.append(og);
  }
  select.value = entry.action;
  const arg = el("div", "g-arg");
  const drawArg = () => {
    const needs = catalog.actions.find(a => a.id === select.value).needs;
    arg.replaceChildren();
    if (!needs) return;
    const input = el("input", "field");
    if (needs === "keys") {
      input.readOnly = true;
      input.placeholder = "点这里，然后按下组合键（Esc 取消）";
      input.value = entry.action === "keys" ? entry.keys || "" : "";
      input.onfocus = () => input.classList.add("recording");
      input.onblur = () => input.classList.remove("recording");
      input.onkeydown = e => {
        e.preventDefault(); e.stopPropagation();
        if (e.key === "Escape" && !e.ctrlKey && !e.altKey && !e.shiftKey) { input.blur(); return; }
        const keys = shortcutFrom(e);
        if (!keys) return;
        input.value = keys;
        input.blur();
        saveKnobEntry(t, { action: "keys", keys });
      };
      arg.append(input);
    } else {
      input.placeholder = "程序、文件或网址，例如 https://… 或 C:/…/app.exe";
      input.value = entry.action === "run" ? entry.target || "" : "";
      input.onchange = () => input.value.trim() && saveKnobEntry(t, { action: "run", target: input.value.trim() });
      const pick = el("button", "pill", "选择…");
      pick.onclick = async () => {
        const file = await window.neos.pickFile([]);
        if (file) { input.value = file; saveKnobEntry(t, { action: "run", target: file }); }
      };
      arg.append(input, pick);
    }
    setTimeout(() => input.focus(), 0);
  };
  select.onchange = () => {
    const needs = catalog.actions.find(a => a.id === select.value).needs;
    if (needs) drawArg(); else saveKnobEntry(t, { action: select.value });
  };
  drawArg();
  form.append(select, arg);
  box.append(form, el("div", "dim kb-hint",
    "电脑端动作需要本应用在运行（可在「应用设置」里设为开机启动、关闭时隐藏到托盘）。快捷键：点输入框后直接按组合键。"));
  return box;
}

async function writeKnobKey(t, enc, code) {
  if (KB.busy) return;
  KB.busy = true;
  try {
    const d = await api("PUT", "/api/keyboard/via/knob", { layer: KB.layer, encoder: enc, gesture: t.gesture, keycode: code });
    KB.state.names[d.code] = d;
    KB.state.encoders[KB.layer][enc][t.gesture] = d.code;
    if (t.gesture === "press") {
      const [r, c] = enc === 0 ? [6, 8] : [5, 8];
      KB.state.keymap[KB.layer][r][c] = d.code;
    }
    if (KB.knobs.config[t.knob][t.gesture].action !== "default")
      await saveKnobConfig(c => { c[t.knob][t.gesture] = { action: "default" }; }, false);
    toast(`已写进键盘：${nameOf(d.code)}`);
    KB.target = null;
  } catch (e) { toast(e.message, true); }
  KB.busy = false;
  renderKb();
}

async function saveKnobEntry(t, entry) {
  await saveKnobConfig(c => {
    c[t.knob][t.gesture] = entry;
    if (entry.action !== "default") c.enabled = true;
  });
}

async function saveKnobConfig(change, announce = true) {
  const config = structuredClone(KB.knobs.config);
  change(config);
  const data = await act(() => api("PUT", "/api/knobs", { config }), announce ? "已保存" : null);
  if (data) KB.knobs = data;
  renderKb();
}

function kbFlashGesture(e) {
  if (KB.view !== "knobs") return;
  const row = $(`.gesture-row[data-gesture="${e.knob}.${e.gesture}"]`);
  if (!row) return;
  row.classList.add("flash");
  clearTimeout(row.timer);
  row.timer = setTimeout(() => row.classList.remove("flash"), 500);
}

function kbKnobsChanged() {
  if (KB.view === "knobs") loadKnobs().then(renderKb);
}

// --- macros --------------------------------------------------------------------------------------------
function macroBytes(actions) {
  let n = 1;
  for (const a of actions) n += a.type === "delay" ? 3 + String(a.ms).length : a.type === "text" ? a.text.length : 3;
  return n;
}
function macroSummary(actions) {
  if (!actions.length) return "空";
  const parts = actions.slice(0, 6).map(a => a.type === "delay" ? `${a.ms}ms` : a.type === "text" ? `“${a.text}”`
    : `${a.type === "down" ? "↓" : a.type === "up" ? "↑" : ""}${describe(a.code).label}`);
  return parts.join(" ") + (actions.length > 6 ? " …" : "");
}

function renderMacros() {
  const s = KB.state;
  if (!s.macros) return el("div", "note warn", "键盘没有回应宏的读取。");
  KB.macroDraft = KB.macroDraft || structuredClone(s.macros);
  const draft = KB.macroDraft, sel = KB.macroSel;
  const dirty = JSON.stringify(draft) !== JSON.stringify(s.macros);
  const used = draft.reduce((n, m) => n + macroBytes(m), 0);

  const wrap = el("div", "macro-layout");
  const list = el("div", "macro-list");
  draft.forEach((m, n) => {
    const item = el("button", `macro-item${n === sel ? " active" : ""}`);
    const name = labelOfSlot("macros", n);
    const changed = JSON.stringify(m) !== JSON.stringify(s.macros[n]);
    item.append(el("div", "macro-title", `宏 ${n + 1}${name ? ` · ${name}` : ""}${changed ? " •" : ""}`),
                el("div", "dim macro-sum", macroSummary(m)));
    const bound = where(QK_MACRO + n);
    if (bound.length) item.append(el("div", "macro-bound", `在 ${bound.join("、")}`));
    item.onclick = () => { stopRecording(); KB.macroSel = n; KB.macroPick = null; renderKb(); };
    list.append(item);
  });

  const ed = el("div", "panel macro-editor");
  const actions = draft[sel];
  const head = el("div", "macro-head");
  const nameInput = el("input", "field macro-name");
  nameInput.placeholder = `宏 ${sel + 1} 的名字（只存在本机）`;
  nameInput.value = labelOfSlot("macros", sel);
  nameInput.onchange = () => saveLabel("macros", sel, nameInput.value);
  head.append(el("h3", null, `宏 ${sel + 1}`), nameInput);
  ed.append(head);

  const rows = el("div", "macro-rows");
  actions.forEach((a, i) => rows.append(macroRow(actions, a, i)));
  if (!actions.length) rows.append(el("div", "dim macro-empty", "还没有动作。点“录制”在键盘上按一遍，或用下面的按钮添加。"));
  ed.append(rows);
  if (KB.macroPick != null && actions[KB.macroPick]) {
    ed.append(keyPicker({
      title: `第 ${KB.macroPick + 1} 步的按键`, current: actions[KB.macroPick].code, basicOnly: true,
      onClose: () => { KB.macroPick = null; renderKb(); },
      onPick: code => { actions[KB.macroPick].code = code; KB.macroPick = null; renderKb(); },
    }));
  }

  const add = el("div", "macro-add");
  const rec = el("button", `pill${KB.recording ? " recording" : ""}`, KB.recording ? "■ 停止录制" : "● 录制");
  rec.onclick = () => (KB.recording ? stopRecording(true) : startRecording());
  const delays = el("label", "check");
  const dIn = el("input"); dIn.type = "checkbox"; dIn.checked = KB.recordDelays !== false;
  dIn.onchange = () => { KB.recordDelays = dIn.checked; };
  delays.append(dIn, el("span", null, "录下停顿"));
  const addKey = el("button", "pill", "+ 按键");
  addKey.onclick = () => { actions.push({ type: "tap", code: 0x04 }); KB.macroPick = actions.length - 1; renderKb(); };
  const addDelay = el("button", "pill", "+ 延时");
  addDelay.onclick = () => { actions.push({ type: "delay", ms: 100 }); renderKb(); };
  const addText = el("button", "pill", "+ 文字");
  addText.onclick = () => { actions.push({ type: "text", text: "" }); renderKb(); };
  const clear = el("button", "pill ghost", "清空");
  clear.onclick = () => { draft[sel] = []; KB.macroPick = null; renderKb(); };
  add.append(rec, delays, addKey, addDelay, addText, clear);
  ed.append(add);

  const foot = el("div", "macro-foot");
  const meter = el("div", "meter");
  const bar = el("i");
  bar.style.width = `${Math.min(100, used / s.macro_space.size * 100)}%`;
  meter.append(bar);
  meter.classList.toggle("over", used > s.macro_space.size);
  const save = el("button", "pill primary", "写进键盘");
  save.disabled = !dirty || used > s.macro_space.size;
  save.onclick = saveMacros;
  const revert = el("button", "pill ghost", "撤销修改");
  revert.disabled = !dirty;
  revert.onclick = () => { KB.macroDraft = null; KB.macroPick = null; renderKb(); };
  foot.append(el("span", "dim", `空间 ${used} / ${s.macro_space.size} 字节`), meter, revert, save);
  ed.append(foot);
  ed.append(el("div", "dim kb-hint",
    "宏只能用基本按键（字母、数字、符号、功能键、修饰键、媒体键）；文字按美式键盘输入，只支持英文字母、数字和符号。改动点“写进键盘”后才生效，16 个宏一起保存。"));
  wrap.append(list, ed);
  return wrap;
}

function macroRow(actions, a, i) {
  const row = el("div", `macro-row${KB.macroPick === i ? " sel" : ""}`);
  row.append(el("span", "macro-step dim", String(i + 1)));
  const type = el("select", "field macro-type");
  for (const [id, label] of MACRO_TYPES) { const o = el("option", null, label); o.value = id; type.append(o); }
  type.value = a.type;
  type.onchange = () => {
    const t = type.value;
    actions[i] = t === "delay" ? { type: t, ms: 100 } : t === "text" ? { type: t, text: "" } : { type: t, code: a.code || 0x04 };
    renderKb();
  };
  row.append(type);
  if (a.type === "delay") {
    const ms = el("input", "field macro-val");
    ms.type = "number"; ms.min = 0; ms.max = 60000; ms.step = 10; ms.value = a.ms;
    ms.onchange = () => { a.ms = Math.max(0, Math.min(60000, Math.round(Number(ms.value) || 0))); renderKb(); };
    row.append(ms, el("span", "dim", "毫秒"));
  } else if (a.type === "text") {
    const tx = el("input", "field macro-val wide");
    tx.value = a.text; tx.placeholder = "要输入的文字（英文、数字、符号）";
    tx.onchange = () => {
      const v = tx.value.replace(/[^\x20-\x7e]/g, "");
      if (v !== tx.value) toast("只保留了英文字母、数字和符号", true);
      a.text = v; renderKb();
    };
    row.append(tx);
  } else {
    const k = el("button", "pill macro-key", describe(a.code).label);
    k.title = nameOf(a.code);
    k.onclick = () => { KB.macroPick = KB.macroPick === i ? null : i; renderKb(); };
    row.append(k);
  }
  const tools = el("span", "macro-tools");
  const up = el("button", "pill ghost round", "↑"); up.disabled = i === 0;
  up.onclick = () => { [actions[i - 1], actions[i]] = [actions[i], actions[i - 1]]; KB.macroPick = null; renderKb(); };
  const down = el("button", "pill ghost round", "↓"); down.disabled = i === actions.length - 1;
  down.onclick = () => { [actions[i + 1], actions[i]] = [actions[i], actions[i + 1]]; KB.macroPick = null; renderKb(); };
  const del = el("button", "pill ghost round", "×");
  del.onclick = () => { actions.splice(i, 1); KB.macroPick = null; renderKb(); };
  tools.append(up, down, del);
  row.append(tools);
  return row;
}

function startRecording() {
  const actions = KB.macroDraft[KB.macroSel];
  const rec = { actions: [], last: null, down: new Set() };
  const push = (type, code) => {
    const now = performance.now();
    if (KB.recordDelays !== false && rec.last != null) {
      const ms = Math.round((now - rec.last) / 10) * 10;
      if (ms >= 20) rec.actions.push({ type: "delay", ms });
    }
    rec.last = now;
    rec.actions.push({ type, code });
  };
  rec.keydown = e => {
    const code = HID[e.code];
    e.preventDefault(); e.stopPropagation();
    if (code == null || e.repeat || rec.down.has(code)) return;
    rec.down.add(code); push("down", code);
  };
  rec.keyup = e => {
    const code = HID[e.code];
    e.preventDefault(); e.stopPropagation();
    if (code == null || !rec.down.has(code)) return;
    rec.down.delete(code); push("up", code);
  };
  rec.target = actions;
  window.addEventListener("keydown", rec.keydown, true);
  window.addEventListener("keyup", rec.keyup, true);
  KB.recording = rec;
  KB.macroPick = null;
  document.activeElement?.blur();
  toast("正在录制：在键盘上按一遍，完成后点“停止录制”");
  renderKb();
}

function stopRecording(apply = false) {
  const rec = KB.recording;
  if (!rec) return;
  window.removeEventListener("keydown", rec.keydown, true);
  window.removeEventListener("keyup", rec.keyup, true);
  KB.recording = null;
  if (apply) {
    for (const code of rec.down) rec.actions.push({ type: "up", code });   // keys still held: release them
    // a key pressed and released with nothing in between is one tap
    const out = [];
    for (const a of rec.actions) {
      const prev = out[out.length - 1];
      if (a.type === "up" && prev && prev.type === "down" && prev.code === a.code) prev.type = "tap";
      else out.push(a);
    }
    rec.target.push(...out);
    renderKb();
  }
}

async function saveMacros() {
  stopRecording(true);
  const r = await act(() => api("PUT", "/api/keyboard/via/macros", { macros: KB.macroDraft }), "宏已写进键盘");
  if (r) {
    KB.state.macros = r.macros;
    KB.state.macro_space = r.macro_space;
    KB.macroDraft = null;
  }
  renderKb();
}

async function saveLabel(kind, index, name) {
  const r = await act(() => api("PUT", "/api/keyboard/via/label", { kind, index, name }));
  if (r) { KB.state.labels = r; renderKb(); }
}

// --- advanced keys (Vial tap dance) ------------------------------------------------------------------
function renderAdvanced() {
  const s = KB.state;
  if (!s.tap_dances) return el("div", "note warn", "键盘没有回应高级键的读取。");
  KB.tdDraft = KB.tdDraft || structuredClone(s.tap_dances);
  const cards = el("div", "td-cards");
  KB.tdDraft.forEach((td, n) => {
    const orig = s.tap_dances[n];
    const dirty = JSON.stringify(td) !== JSON.stringify(orig);
    const card = el("div", `panel td-card${KB.tdTarget && KB.tdTarget.index === n ? " active" : ""}`);
    const head = el("div", "td-head");
    const name = el("input", "field td-name");
    name.placeholder = "名字（只存在本机）";
    name.value = labelOfSlot("tap_dances", n);
    name.onchange = () => saveLabel("tap_dances", n, name.value);
    head.append(el("h3", null, `高级键 ${n + 1}`), name);
    card.append(head);
    for (const [f, label] of TD_FIELDS) {
      const sel = KB.tdTarget && KB.tdTarget.index === n && KB.tdTarget.field === f;
      const row = el("div", `td-row${sel ? " sel" : ""}`);
      const b = el("button", "pill td-key", td[f] ? describe(td[f]).label : "—");
      b.title = td[f] ? nameOf(td[f]) : "未设置";
      b.onclick = () => { KB.tdTarget = sel ? null : { index: n, field: f }; renderKb(); };
      const x = el("button", "pill ghost round", "×");
      x.title = "清除";
      x.disabled = !td[f];
      x.onclick = () => { td[f] = 0; renderKb(); };
      row.append(el("span", "td-label", label), b, x);
      card.append(row);
    }
    const term = el("div", "td-row");
    const slider = el("input", "kb-slider"); slider.type = "range"; slider.min = 100; slider.max = 1000; slider.step = 10;
    slider.value = td.term || 200;
    const val = el("span", "td-term", `${td.term || 200} ms`);
    slider.oninput = () => { val.textContent = `${slider.value} ms`; };
    slider.onchange = () => { td.term = Number(slider.value); renderKb(); };
    term.append(el("span", "td-label", "判定时间"), slider, val);
    card.append(term);
    const bound = where(QK_TD + n);
    card.append(el("div", "dim td-bound", bound.length ? `在 ${bound.join("、")}` : "还没有键用它：到「按键」把一个键设成「高级键」里的它"));
    const foot = el("div", "td-foot");
    const revert = el("button", "pill ghost", "撤销");
    revert.disabled = !dirty;
    revert.onclick = () => { KB.tdDraft[n] = structuredClone(orig); renderKb(); };
    const save = el("button", "pill primary", "写进键盘");
    save.disabled = !dirty;
    save.onclick = () => saveTapDance(n);
    foot.append(revert, save);
    card.append(foot);
    cards.append(card);
  });
  const parts = [cards];
  if (KB.tdTarget) {
    const t = KB.tdTarget, td = KB.tdDraft[t.index];
    parts.push(keyPicker({
      title: `高级键 ${t.index + 1} · ${TD_FIELDS.find(f => f[0] === t.field)[1]}`, current: td[t.field], noTapDance: true,
      onClose: () => { KB.tdTarget = null; renderKb(); },
      onPick: code => { td[t.field] = code; if (!td.term) td.term = 200; KB.tdTarget = null; renderKb(); },
    }));
  } else {
    parts.push(el("div", "dim kb-hint",
      "例：点按 = Esc、按住 = 左 Ctrl，就是官方驱动里的“按住/单击（MT）”。判定时间：按住超过它算“按住”，两次点按间隔小于它算“双击”。这块键盘有 4 个高级键位置；官方驱动的“切换开关（TGL）”这块键盘的固件不支持。"));
  }
  return parts;
}

async function saveTapDance(n) {
  const td = KB.tdDraft[n];
  const r = await act(() => api("PUT", "/api/keyboard/via/tapdance", { index: n, ...td, term: td.term || 200 }), `高级键 ${n + 1} 已写进键盘`);
  if (r) {
    Object.assign(KB.state.names, r.names || {});
    const { names, ...entry } = r;
    KB.state.tap_dances[n] = entry;
    KB.tdDraft[n] = structuredClone(entry);
  }
  renderKb();
}

// --- lighting --------------------------------------------------------------------------------------------
function renderLighting() {
  const L = KB.state.lighting;
  const effect = KB.catalog.effects.find(e => e.id === L.effect) || { speed: true, color: true };
  const panel = el("div", "panel");
  const grid = el("div", "effect-grid");
  for (const e of KB.catalog.effects) {
    const b = el("button", `effect${e.id === L.effect ? " active" : ""}`, e.name);
    b.onclick = () => setLight({ effect: e.id });
    grid.append(b);
  }
  panel.append(el("div", "section-label", "灯效"), grid);
  const form = el("div", "panel kb-form");
  form.append(row("亮度", slider(L.brightness, 0, 255, v => setLight({ brightness: v }))));
  if (effect.speed) form.append(row("速度", slider(L.speed, 0, 255, v => setLight({ speed: v }))));
  if (effect.color) {
    const hue = slider(L.hue, 0, 255, v => setLight({ hue: v }));
    hue.classList.add("hue");
    form.append(row("颜色", hue));
    form.append(row("饱和度", slider(L.saturation, 0, 255, v => setLight({ saturation: v }))));
    const sw = el("div", "kb-swatch");
    sw.style.background = `hsl(${L.hue / 255 * 360}, ${L.saturation / 255 * 100}%, 50%)`;
    form.append(row("", sw));
  } else {
    form.append(el("div", "dim kb-hint", "这个灯效自带颜色，不能调色。"));
  }
  return [panel, form];
}
function row(label, control, hint) {
  const r = el("div", "setting");
  const text = el("div");
  text.append(el("div", null, label));
  if (hint) text.append(el("div", "dim", hint));
  r.append(text, control);
  return r;
}
function slider(value, min, max, onchange) {
  const s = el("input", "kb-slider"); s.type = "range"; s.min = min; s.max = max; s.value = value;
  s.onchange = () => onchange(Number(s.value));
  return s;
}
async function setLight(changes) {
  const L = await act(() => api("PUT", "/api/keyboard/via/lighting", changes));
  if (L) { KB.state.lighting = L; renderKb(); }
}

// --- performance (the vendor's system settings) ------------------------------------------------------
const SYSTEM_HINTS = {
  5: "切到 macOS 后，Win/Alt 等按 Mac 习惯工作",
  1: "两次按键之间的最短间隔",
  2: "先发送：响应更快；先防抖：更不容易误触",
  7: "无线时多久不用进入深度睡眠",
  9: "手靠近时唤醒键盘",
  4: "让电脑上的灯光软件控制键盘背光",
};
function renderPerformance() {
  const s = KB.state, panel = el("div", "panel narrow-panel");
  for (const item of KB.catalog.system) {
    let control;
    const save = async value => {
      const r = await act(() => api("PUT", "/api/keyboard/via/system", { item: item.item, value }), "已保存");
      if (r) { s.system[item.item] = r.value; renderKb(); }
    };
    if (item.options.length <= 4) {
      control = el("div", "seg");
      for (const o of item.options) {
        const b = el("button", `pill${s.system[item.item] === o.value ? " active" : ""}`, o.label);
        b.onclick = () => save(o.value);
        control.append(b);
      }
    } else {
      control = el("select", "field narrow-select");
      for (const o of item.options) { const op = el("option", null, o.label); op.value = o.value; control.append(op); }
      control.value = s.system[item.item];
      control.onchange = () => save(Number(control.value));
    }
    panel.append(row(item.label, control, SYSTEM_HINTS[item.item]));
  }
  return panel;
}

// --- device and backup -----------------------------------------------------------------------------------
function renderDevice() {
  const s = KB.state, st = S.status || {};
  const kbCard = el("div", "panel info-card");
  kbCard.append(el("h3", null, "键盘"));
  const facts = el("dl", "facts wide");
  const add = (k, v) => facts.append(el("dt", null, k), el("dd", null, v));
  add("连接", s.simulated ? "模拟键盘（不是你的键盘）" : LINK_NAMES[s.connection] || s.connection);
  add("电量", s.battery ? `${s.battery.percent}%${s.battery.charging ? " · 充电中" : ""}` : "—");
  add("协议", `VIA ${s.protocol} · Vial`);
  add("按键", `${s.layers} 层 · 矩阵 ${s.rows}×${s.cols}${s.layout ? " · 官方布局" : " · 没找到官方布局，按矩阵显示"}`);
  add("宏", s.macro_space ? `${s.macro_space.count} 个 · 已用 ${s.macro_space.used} / ${s.macro_space.size} 字节` : "—");
  add("高级键", s.tap_dances ? `${s.tap_dances.length} 个` : "—");
  add("固件版本", "键盘不提供读取（官方驱动也读不到）");
  kbCard.append(facts);

  const scCard = el("div", "panel info-card");
  scCard.append(el("h3", null, "屏幕"));
  const sf = el("dl", "facts wide");
  const d = st.device || {};
  const add2 = (k, v) => sf.append(el("dt", null, k), el("dd", null, v));
  add2("状态", { connected: "已连接", connecting: "连接中", absent: "没找到（2.4G/蓝牙下电脑看不到屏幕）", idle: "已释放", error: "出错" }[d.state] || d.state || "—");
  if (d.model) add2("型号", d.model);
  if (d.build) add2("固件构建", d.build);
  if (d.capabilities) {
    const c = d.capabilities;
    add2("我们的补丁", c.patch_version ? `第 ${c.patch_version} 版 · 协议 ${c.proto_version}` : d.realtime === false ? "原厂固件（没有实时显示）" : "—");
    const names = { knob: "旋钮事件", host_pages: "多页", telemetry: "键盘状态", region: "局部刷新", knob_takeover: "旋钮接管", test_knob: "电脑转旋钮" };
    add2("实时功能", Object.entries(names).filter(([k]) => c[k]).map(([, v]) => v).join("、") || "—");
  }
  scCard.append(sf);

  const bk = el("div", "panel info-card");
  bk.append(el("h3", null, "备份"));
  bk.append(el("p", "dim", s.backup
    ? `第一次读取键盘时自动备份了按键、旋钮、灯光、性能设置、宏和高级键${s.backup_time ? `（${s.backup_time}）` : ""}。恢复会把它们全部写回。`
    : "还没有备份：第一次成功读取键盘时会自动备份。"));
  const restore = el("button", "pill", "恢复到备份");
  restore.disabled = !s.backup;
  restore.onclick = async () => {
    if (!confirm("把按键、旋钮、灯光、性能设置、宏和高级键恢复到备份？")) return;
    const r = await act(() => api("POST", "/api/keyboard/via/restore"));
    if (r) { toast(`已恢复（${r.changed} 处改动）`); showKbView("device", true); }
  };
  bk.append(restore, el("p", "dim", "不提供“恢复出厂”和固件升级：那会擦除或重刷键盘，本应用从不发送这类命令。"));
  const grid = el("div", "info-grid");
  grid.append(kbCard, scCard, bk);
  return grid;
}
