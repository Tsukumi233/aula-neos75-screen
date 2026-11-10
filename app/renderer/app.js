// Neos75 — window logic. Talks to the service (neos75d) over its localhost API; see
// sdk/src/neos75screen/service/API.md. No framework: the pages are small and the data comes as events.
// The sidebar switches views: the keyboard's pages (keyboard.js), the screen (this file), settings, log.
"use strict";

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

const MAX_PAGES = 8, STOCK = 6;
const S = { info: null, base: "", token: "", status: null, widgets: [], pages: [], filter: "all", drawer: null,
            frameVersion: -1, logs: [], appSettings: null, view: null };

const RUNNER = { stopped: ["已停止", ""], starting: ["启动中…", "warn"], running: ["运行中", "ok"], waiting: ["等待设备", "warn"],
                 stopping: ["停止中…", "warn"], error: ["出错", "err"] };
const DEVICE = { idle: ["未占用（已释放给其他程序）", ""], connecting: ["连接中…", "warn"], connected: ["已连接", "ok"],
                 absent: ["未找到键盘屏幕", "err"], error: ["出错", "err"] };
const LINK = { bluetooth: "蓝牙", "2.4g": "2.4G 无线", wired: "有线", unknown: "未知连接" };
const GROUP = { neos: "Néos", custom: "我的页面", classic: "经典", extra: "其他文件夹" };

// --- API -------------------------------------------------------------------------------------------------
async function api(method, path, body) {
  const res = await fetch(S.base + path, { method, headers: { "X-Neos75-Token": S.token, ...(body ? { "Content-Type": "application/json" } : {}) },
                                           body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const url = (path, params = {}) => {
  const q = new URLSearchParams({ ...params, token: S.token });
  return `${S.base}${path}?${q}`;
};
const previewUrl = (entry, label, width = 480) => url("/api/preview", { entry, label, width });

function toast(message, error = false) {
  const t = $("#toast");
  t.textContent = message;
  t.classList.toggle("err", error);
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), error ? 6000 : 2600);
}
async function act(fn, done) {
  try { const r = await fn(); if (done) toast(done); return r; } catch (e) { toast(e.message, true); return null; }
}

// --- selection entries (same encoding as the Python side: readable paths) ---------------------------------
function splitEntry(entry) {
  const i = entry.indexOf("?");
  const name = i < 0 ? entry : entry.slice(0, i);
  const options = {};
  if (i >= 0) for (const [k, v] of new URLSearchParams(entry.slice(i + 1))) options[k] = v;
  return { name, options };
}
function joinEntry(name, options) {
  const enc = s => encodeURIComponent(s).replace(/%3A/gi, ":").replace(/%2F/gi, "/").replace(/%5C/gi, "\\").replace(/%2C/gi, ",");
  const parts = Object.entries(options).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${enc(k)}=${enc(v)}`);
  return parts.length ? `${name}?${parts.join("&")}` : name;
}
const widget = name => S.widgets.find(w => w.name === name);
const labelOf = i => `Page ${STOCK + 1 + i}`;

// --- official artwork ------------------------------------------------------------------------------------
const artUrl = file => S.info && S.info.art ? `file:///${encodeURI(S.info.art.replace(/\\/g, "/"))}/${file}` : null;
const ART_SIZES = { "page2_bg.jpg": [480, 360], "sg25.jpg": [238, 140], "page5_bg.jpg": [82, 321] };
function applyArt(root = document) {
  for (const e of root.querySelectorAll(".art[data-art]")) {
    const src = artUrl(e.dataset.art);
    if (!src) continue;
    const [x, y, w, h] = e.dataset.crop.split(",").map(Number);
    const scale = e.dataset.size ? Number(e.dataset.size) / w : 1;
    const [iw, ih] = ART_SIZES[e.dataset.art] || [480, 360];
    Object.assign(e.style, { backgroundImage: `url("${src}")`, width: `${w * scale}px`, height: `${h * scale}px`,
      backgroundSize: `${iw * scale}px ${ih * scale}px`, backgroundPosition: `${-x * scale}px ${-y * scale}px` });
    e.classList.add("loaded");
  }
}

// --- thumbnails ------------------------------------------------------------------------------------------
function thumb(src, alt = "") {
  const box = el("div", "thumb");
  const img = el("img");
  img.alt = alt;
  img.dataset.preview = "1";
  img.draggable = false;
  const loading = el("div", "loading");
  img.onload = () => img.classList.add("ready");
  img.onerror = () => { img.classList.add("ready", "failed"); img.style.display = "none"; loading.classList.add("done");
                        box.appendChild(el("div", "err", "预览失败（详见日志）")); };
  img.src = src;
  box.append(img, loading);
  return box;
}

// --- status (left column) --------------------------------------------------------------------------------
function setState(node, [text, cls]) {
  node.textContent = text;
  node.className = `state ${cls}`;
}
function renderStatus() {
  const s = S.status;
  if (!s) return;
  const r = s.runner, d = s.device, k = s.keyboard || {};
  const span = el("span"); setState(span, RUNNER[r.state] || [r.state, ""]);
  $("#f-runner").replaceChildren(span, ...(r.error ? [el("span", "dim", `  ${r.error}`)] : []));
  const dev = el("span"); setState(dev, DEVICE[d.state] || [d.state, ""]);
  $("#f-device").replaceChildren(dev, d.fake ? el("span", "sim-text", "  模拟设备（不是真实键盘）")
                                            : el("span", "dim", d.model ? `  ${d.model}` : ""));
  $("#f-fps").textContent = r.state === "running" ? `${r.fps.toFixed(1)} fps · 已发送 ${r.frames} 帧` : "—";
  const sim = !!s.service.fake;
  $("#sim-badge").classList.toggle("hidden", !sim);
  $("#mirror-sim").classList.toggle("hidden", !sim);
  document.title = sim ? "Neos75（模拟设备）" : "Neos75";
  const kb = $("#f-keyboard");
  kb.classList.toggle("sim-text", sim);
  if (sim) kb.textContent = k.reported ? `模拟值 ${k.battery}%（不是你的键盘）` : "无（模拟设备，未连接键盘）";
  else kb.textContent = k.reported ? `电量 ${k.battery}%${k.charging ? " · 充电中" : ""} · ${LINK[k.connection] || ""}`
                                   : (d.state === "connected" ? "键盘还没有上报状态" : "—");

  const active = ["starting", "running", "waiting"].includes(r.state);
  const run = $("#btn-run");
  run.textContent = active ? "停止显示" : "开始显示";
  run.classList.toggle("primary", !active);
  $("#btn-restart").disabled = !active;
  // the knob from the PC: always on the fake device, on the keyboard when its firmware can (test_knob)
  const pcKnob = !sim && r.state === "running" && !!(d.capabilities && d.capabilities.test_knob);
  $("#knob-sim").classList.toggle("hidden", !(sim || pcKnob));
  $("#knob-sim-note").textContent = sim ? "（模拟设备）" : "（从电脑转动键盘旋钮）";
  $("#knob-press").classList.toggle("hidden", !sim);

  // the stock status bar
  const shown = r.state === "running" && r.visible !== false && r.label;
  $("#sb-page").textContent = shown ? r.label : r.state === "running" ? "原厂页" : "—";
  const real = k.reported && !sim;      // a simulated keyboard never fills the status bar
  $("#sb-battery").textContent = real ? `${k.battery}%` : sim ? "模拟" : "Battery";
  $("#sb-battery").classList.toggle("sim", sim);
  $("#sb-charge").className = `dot charge${real && k.charging ? " on" : ""}`;
  $("#sb-bt").classList.toggle("on", !!real && k.connection === "bluetooth");
  $("#sb-wired").classList.toggle("on", !!real && k.connection === "wired");
  $("#sb-24g").classList.toggle("on", !!real && k.connection === "2.4g");

  // the sidebar's little screen
  const shownLabel = r.state === "running" ? (r.visible === false ? "原厂页" : r.label || "运行中") : null;
  $("#nav-screen-text").textContent = shownLabel ? `屏幕 · ${shownLabel}` : `屏幕 · ${(RUNNER[r.state] || [r.state])[0]}`;
  $("#nav-screen-dot").className = `nav-dot${r.state === "running" ? " on" : r.state === "error" ? " err" : ""}`;
  $("#nav-mirror").style.opacity = r.state === "running" && r.frames ? (r.visible === false ? ".35" : "1") : "0";
  renderTitleStatus();

  // the mirror
  const idle = $("#mirror-idle");
  const img = $("#mirror-img");
  if (r.state !== "running" || !r.frames) {
    idle.classList.remove("hidden");
    $("#mirror-idle-text").textContent = { running: "正在载入页面…", starting: "启动中…", waiting: r.error || "等待设备…",
                                           stopping: "停止中…" }[r.state] || "未在显示 · 点“开始显示”";
    img.style.opacity = r.frames ? ".25" : "0";
  } else {
    idle.classList.add("hidden");
    img.style.opacity = r.visible === false ? ".35" : "1";
  }
  highlightCurrent();
}

function renderTitleStatus() {
  const s = S.status, k = (s && s.keyboard) || {};
  const sim = !!(s && s.service.fake);
  const items = [];
  const kb = typeof KB !== "undefined" ? KB.state : null;
  const link = kb ? (kb.simulated ? "模拟键盘" : { wired: "有线", "2.4g": "2.4G" }[kb.connection]) : k.reported && !sim ? LINK[k.connection] : null;
  const bat = kb && kb.battery ? kb.battery : k.reported && !sim ? { percent: k.battery, charging: k.charging } : null;
  if (link) items.push(el("span", "tb-pill", link));
  if (bat) {
    const p = el("span", `tb-pill battery${bat.charging ? " charging" : ""}`);
    const gauge = el("i"); const fill = el("b"); fill.style.width = `${Math.max(4, bat.percent)}%`; gauge.append(fill);
    p.append(gauge, document.createTextNode(`${bat.percent}%${bat.charging ? " 充电中" : ""}`));
    items.push(p);
  }
  if (!items.length) items.push(el("span", "tb-pill dim", "键盘未连接"));
  $("#tb-status").replaceChildren(...items);
}

function refreshMirror(version) {
  // one image in flight; a newer frame announced meanwhile is fetched when it arrives
  if (version == null || version === S.frameVersion) return;
  S.frameWanted = version;
  if (S.mirrorLoading) return;
  // only the sidebar's little screen is visible outside the screen view: 2 updates a second are enough there
  const wait = (S.mirrorAt || 0) + (S.view === "screen" ? 0 : 500) - Date.now();
  if (wait > 0) {
    if (!S.mirrorTimer) S.mirrorTimer = setTimeout(() => { S.mirrorTimer = null; refreshMirror(S.frameWanted); }, wait);
    return;
  }
  S.mirrorAt = Date.now();
  S.frameVersion = version;
  S.mirrorLoading = true;
  const next = new Image();
  const done = () => {
    S.mirrorLoading = false;
    if (S.frameWanted !== S.frameVersion) refreshMirror(S.frameWanted);
  };
  next.onload = () => { $("#mirror-img").src = next.src; $("#nav-mirror").src = next.src; done(); };
  next.onerror = done;
  next.src = url("/api/screen.jpg", { v: version });
}

async function reloadStatus() {
  const s = await api("GET", "/api/status").catch(() => null);
  if (!s) return;
  S.status = s;
  if (JSON.stringify(s.pages) !== JSON.stringify(S.pages)) { S.pages = s.pages; renderCycle(); renderLibrary(); }
  renderStatus();
}

// --- knob cycle ------------------------------------------------------------------------------------------
function renderCycle() {
  // the six stock pages: a compact strip (the knob passes them before yours)
  const strip = el("div", "stock-strip");
  const thumbs = el("div", "stock-thumbs");
  for (let n = 1; n <= STOCK; n++) {
    const mini = el("div", "stock-mini");
    const src = artUrl(`page${n}_bg.jpg`);
    if (src) { const img = el("img"); img.src = src; img.draggable = false; mini.append(img); }
    mini.append(el("span", null, String(n)));
    thumbs.append(mini);
  }
  strip.append(el("div", "stock-caption", "原厂 Page 1–6"), thumbs, el("div", "stock-arrow", "→ 继续转动：你的页面"));

  const grid = el("div", "user-grid");
  S.pages.forEach((p, i) => {
    const slot = el("div", "slot user");
    slot.dataset.index = i;
    slot.draggable = true;
    const box = thumb(previewUrl(p.entry, p.label, 480), p.title);
    const tools = el("div", "tools");
    const opts = el("button", "pill", "⚙"); opts.title = "选项";
    opts.onclick = e => { e.stopPropagation(); openDrawer({ mode: "edit", index: i }); };
    const del = el("button", "pill", "×"); del.title = "从旋钮移除";
    del.onclick = e => { e.stopPropagation(); removePage(i); };
    tools.append(opts, del);
    box.append(tools);
    const meta = el("div", "meta");
    meta.append(el("div", "pill page-pill", p.label), el("span", "now", "屏幕上"));
    const name = el("div", "name", p.known ? p.title : `${p.name}（找不到）`);
    if (Object.keys(p.options).length) name.append(el("span", "dim", "  · 已设置"));
    slot.append(box, meta, name);
    slot.onclick = () => openDrawer({ mode: "edit", index: i });
    dragSource(slot, "index", i);
    dropTarget(slot, i);
    grid.append(slot);
  });
  if (S.pages.length < MAX_PAGES) {
    const slot = el("div", "slot empty");
    const box = el("div", "thumb");
    box.append(el("div", null, "+ 拖入页面"), el("div", "dim", `还可添加 ${MAX_PAGES - S.pages.length} 个`));
    const meta = el("div", "meta");
    meta.append(el("div", "pill page-pill ghost-pill", labelOf(S.pages.length)));
    slot.append(box, meta);
    slot.onclick = () => $("#library-section").scrollIntoView();
    dropTarget(slot, S.pages.length);
    grid.append(slot);
  }
  $("#cycle").replaceChildren(strip, grid);
  $("#cycle-count").textContent = `${S.pages.length} / ${MAX_PAGES}`;
  highlightCurrent();
}

function highlightCurrent() {
  const r = S.status && S.status.runner;
  const current = r && r.state === "running" && r.visible !== false ? r.page_index : null;
  for (const slot of $$(".slot.user")) slot.classList.toggle("current", Number(slot.dataset.index) === current);
}

// drag and drop: cycle slots move, library cards add
let drag = null;
function dragSource(node, kind, value) {
  node.addEventListener("dragstart", e => {
    drag = { kind, value };
    e.dataTransfer.effectAllowed = kind === "index" ? "move" : "copy";
    e.dataTransfer.setData("text/plain", String(value));
    node.classList.add("dragging");
  });
  node.addEventListener("dragend", () => { drag = null; node.classList.remove("dragging"); clearDrop(); });
}
function clearDrop() { $$(".drop-before, .drop-into").forEach(n => n.classList.remove("drop-before", "drop-into")); }
function dropTarget(node, position) {
  node.addEventListener("dragover", e => {
    if (!drag) return;
    if (drag.kind === "entry" && S.pages.length >= MAX_PAGES) return;
    e.preventDefault();
    clearDrop();
    node.classList.add(node.classList.contains("empty") ? "drop-into" : "drop-before");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop-before", "drop-into"));
  node.addEventListener("drop", e => {
    e.preventDefault();
    clearDrop();
    if (!drag) return;
    const d = drag; drag = null;
    if (d.kind === "index") {
      const from = d.value;
      let to = position > from ? position - 1 : position;
      to = Math.min(to, S.pages.length - 1);
      if (to !== from) setPagesFrom(api("POST", "/api/pages/move", { from, to }));
    } else {
      setPagesFrom(api("POST", "/api/pages/add", { entry: d.value, position: Math.min(position, S.pages.length) }), `已添加到 ${labelOf(Math.min(position, S.pages.length))}`);
    }
  });
}

async function setPagesFrom(promise, done) {
  const pages = await act(() => promise, done);
  if (pages) { S.pages = pages; renderCycle(); renderLibrary(); }
  return pages;
}
function removePage(i) {
  if (S.pages.length <= 1) { toast("至少保留一个页面", true); return; }
  return setPagesFrom(api("POST", "/api/pages/remove", { index: i }), `已移除 ${S.pages[i].title}`);
}

// --- library ---------------------------------------------------------------------------------------------
function renderLibrary() {
  const used = new Map();
  S.pages.forEach(p => { if (!used.has(p.name)) used.set(p.name, []); used.get(p.name).push(p.label); });
  const items = S.widgets.filter(w => S.filter === "all" || w.group === S.filter);
  const full = S.pages.length >= MAX_PAGES;
  const cards = items.map(w => {
    const card = el("div", "card");
    card.draggable = true;
    card.append(thumb(previewUrl(w.name, "Page 7", 480), w.title));
    const title = el("div", "title");
    title.append(el("span", null, w.title));
    const add = el("button", "pill add", "添加");
    add.disabled = full;
    add.title = full ? "旋钮循环已满（8 个）" : "添加到旋钮循环末尾";
    add.onclick = e => { e.stopPropagation(); setPagesFrom(api("POST", "/api/pages/add", { entry: w.name }), `已添加到 ${labelOf(S.pages.length)}`); };
    title.append(add);
    const chips = el("div", "chips");
    chips.append(el("span", "chip", GROUP[w.group] || w.group));
    if (w.fps > 1) chips.append(el("span", "chip", `${w.fps} fps`));
    if (w.options.length) chips.append(el("span", "chip", "可设置"));
    for (const label of used.get(w.name) || []) chips.append(el("span", "chip on", `在 ${label}`));
    card.append(title, el("div", "desc", w.description || w.path), chips);
    card.onclick = () => openDrawer({ mode: "add", name: w.name });
    dragSource(card, "entry", w.name);
    return card;
  });
  if (!cards.length) {
    const empty = el("div", "dim", S.filter === "custom" ? "还没有自己的页面。让智能体用 neos75ctl new 创建一个，或把 .html 放进 design/custom。" : "没有页面");
    cards.push(empty);
  }
  $("#library").replaceChildren(...cards);
}

// --- drawer: one page, its options and preview ------------------------------------------------------------
function openDrawer(spec) {
  let name, options, label;
  if (spec.mode === "edit") {
    const p = S.pages[spec.index];
    if (!p) return;
    ({ name, options } = splitEntry(p.entry));
    label = p.label;
  } else {
    name = spec.name; options = {}; label = labelOf(S.pages.length);
  }
  const w = widget(name) || { name, title: name, description: "", options: [], path: "", fps: 1, group: "extra" };
  S.drawer = { ...spec, name, options: { ...options }, label, widget: w };
  $("#d-label").textContent = spec.mode === "edit" ? label : `将成为 ${label}`;
  $("#d-title").textContent = w.title;
  $("#d-desc").textContent = w.description;
  const meta = $("#d-meta");
  meta.replaceChildren(el("span", "chip", GROUP[w.group] || w.group), el("span", "chip", `${w.fps} fps`),
                       el("span", "chip", w.status_bar ? "原厂状态栏" : "无状态栏"), el("span", "chip", `${name}.html`));
  renderOptions();
  const primary = $("#d-primary");
  primary.textContent = spec.mode === "edit" ? "保存" : "添加到旋钮";
  primary.disabled = spec.mode === "add" && S.pages.length >= MAX_PAGES;
  $("#d-remove").classList.toggle("hidden", spec.mode !== "edit");
  $("#d-file").classList.toggle("hidden", !w.path);
  updateDrawerPreview(true);
  $("#scrim").classList.remove("hidden");
  $("#drawer").classList.remove("hidden");
}
function closeDrawer() { S.drawer = null; $("#drawer").classList.add("hidden"); $("#scrim").classList.add("hidden"); }

function boolValue(opt, options) {
  if (opt.key in options) return options[opt.key] === (opt.on ?? "1");
  if ((opt.on ?? "1") === "") return true;
  if ((opt.off ?? "") === "") return false;
  return !!opt.default;
}

function renderOptions() {
  const d = S.drawer, form = $("#d-options");
  const rows = d.widget.options.map(opt => {
    const row = el("div", "opt");
    const lbl = el("label", "lbl", opt.label || opt.key);
    row.append(lbl);
    const set = value => { d.options[opt.key] = value; updateDrawerPreview(); };
    const value = d.options[opt.key];
    if (opt.type === "bool") {
      lbl.className = "";
      row.style.cssText = "display:flex;align-items:center;justify-content:space-between";
      const sw = el("label", "switch");
      const input = el("input"); input.type = "checkbox"; input.checked = boolValue(opt, d.options);
      input.onchange = () => set(input.checked ? (opt.on ?? "1") : (opt.off ?? ""));
      sw.append(input, el("i"));
      row.append(sw);
    } else if (opt.type === "select") {
      const seg = el("div", "seg");
      const current = value ?? opt.default ?? (opt.choices[0] || [""])[0];
      for (const [v, text] of opt.choices || []) {
        const b = el("button", `pill${v === current ? " active" : ""}`, text);
        b.type = "button";
        b.onclick = () => { set(v === opt.default ? "" : v); renderOptions(); };
        seg.append(b);
      }
      row.append(seg);
    } else if (opt.type === "file") {
      const r = el("div", "file-row");
      const input = el("input", "field"); input.value = value || ""; input.placeholder = "未选择";
      input.onchange = () => set(input.value.trim());
      const pick = el("button", "pill", "选择…"); pick.type = "button";
      const images = !opt.accept || opt.accept.some(x => ["jpg", "png", "gif", "webp"].includes(x));
      pick.onclick = async () => {
        const p = images ? await window.neos.pickImage() : await window.neos.pickFile(opt.accept || []);
        if (p) { input.value = p; set(p); }
      };
      r.append(input, pick);
      row.append(r);
    } else {
      const input = el("input", "field");
      input.type = opt.type === "number" ? "number" : "text";
      input.value = value || "";
      input.placeholder = opt.default != null ? String(opt.default) : "";
      input.onchange = () => set(input.value.trim());
      row.append(input);
    }
    return row;
  });
  form.replaceChildren(...rows);
}

function currentEntry() { return joinEntry(S.drawer.name, S.drawer.options); }
function updateDrawerPreview(now = false) {
  const d = S.drawer;
  if (!d) return;
  const entry = currentEntry();
  $("#d-entry").textContent = entry;
  clearTimeout(updateDrawerPreview.timer);
  updateDrawerPreview.timer = setTimeout(() => {
    const img = $("#d-preview");
    img.classList.remove("ready");
    $("#d-loading").classList.remove("done");
    img.onload = () => img.classList.add("ready");
    img.onerror = () => { img.classList.add("ready"); $("#d-loading").classList.add("done"); };
    img.src = previewUrl(entry, d.label, 480);
  }, now ? 0 : 350);
}

async function drawerPrimary() {
  const d = S.drawer;
  const entry = currentEntry();
  if (d.mode === "edit") {
    if (await setPagesFrom(api("POST", "/api/pages/replace", { index: d.index, entry }), `${d.label} 已保存`)) closeDrawer();
  } else if (await setPagesFrom(api("POST", "/api/pages/add", { entry }), `已添加到 ${labelOf(S.pages.length)}`)) closeDrawer();
}

// --- settings and log ------------------------------------------------------------------------------------
async function openSettings() {
  const [settings, appSettings] = await Promise.all([api("GET", "/api/settings"), window.neos.appSettings()]);
  S.appSettings = appSettings;
  $("#s-autostart").checked = !!appSettings.autostart;
  $("#s-tray").checked = !!appSettings.closeToTray;
  $("#s-run").checked = !!settings.run_on_start;
  $("#s-interval").value = settings.interval;
  $("#s-quality").value = settings.quality;
  renderDirs(settings.extra_dirs);
  const sv = S.status ? S.status.service : {};
  $("#s-about").innerHTML = "";
  $("#s-about").append(
    el("div", null, `数据文件夹  ${S.info.dataDir}`),
    el("div", null, `服务  127.0.0.1:${sv.port ?? "?"} · pid ${sv.pid ?? "?"}${sv.fake ? " · 模拟设备" : ""}`),
    el("div", null, "智能体与脚本：neos75ctl（--json）或 HTTP API，见 sdk/src/neos75screen/service/API.md"));
}
function renderDirs(dirs) {
  $("#s-dirs").replaceChildren(...dirs.map((dir, i) => {
    const row = el("div", "dir");
    row.append(el("span", null, dir));
    const del = el("button", "pill ghost round", "×");
    del.onclick = async () => { const s = await act(() => api("PUT", "/api/settings", { extra_dirs: dirs.filter((_, j) => j !== i) })); if (s) { renderDirs(s.extra_dirs); loadWidgets(); } };
    row.append(del);
    return row;
  }));
}
async function saveSetting(changes) {
  const s = await act(() => api("PUT", "/api/settings", changes), "已保存");
  if (s && S.status) S.status.settings = s;
  return s;
}
function renderLog() { $("#log-lines").textContent = S.logs.map(l => `${new Date(l.time * 1000).toLocaleTimeString()}  ${l.message}`).join("\n"); }

// --- shortcut recorder (knob actions, keyboard.js) --------------------------------------------------------
const KEY_NAMES = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", PageUp: "pageup",
  PageDown: "pagedown", Home: "home", End: "end", Insert: "insert", Delete: "delete", Backspace: "backspace",
  Enter: "enter", NumpadEnter: "enter", Tab: "tab", Space: "space", Escape: "esc", PrintScreen: "printscreen",
  CapsLock: "capslock", ContextMenu: "apps", Minus: "-", Equal: "=", Comma: ",", Period: ".", Slash: "/",
  Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]", Backslash: "\\", Backquote: "`",
  MediaPlayPause: "play_pause", MediaTrackNext: "media_next", MediaTrackPrevious: "media_prev",
  AudioVolumeUp: "volume_up", AudioVolumeDown: "volume_down", AudioVolumeMute: "mute" };

function shortcutFrom(e) {
  const code = e.code;
  let key = null;
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3).toLowerCase();
  else if (/^Digit\d$/.test(code)) key = code.slice(5);
  else if (/^Numpad\d$/.test(code)) key = `num${code.slice(6)}`;
  else if (/^F\d{1,2}$/.test(code)) key = code.toLowerCase();
  else key = KEY_NAMES[code] || null;
  const mods = [e.ctrlKey && "ctrl", e.shiftKey && "shift", e.altKey && "alt", e.metaKey && "win"].filter(Boolean);
  if (!key) return null;   // only modifiers so far
  return [...mods, key].join("+");
}

// --- views ---------------------------------------------------------------------------------------------------
const VIEWS = ["keys", "knobs", "macros", "advanced", "lighting", "performance", "screen", "device", "settings", "log"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "keys";
  S.view = name;
  localStorage.setItem("view", name);
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.dataset.view === name));
  $("#views").scrollTop = 0;
  if (name !== "screen") closeDrawer();
  if (KB_VIEWS[name]) showKbView(name);
  else stopRecording();
  if (name === "settings") openSettings().catch(e => toast(e.message, true));
  if (name === "log") api("GET", "/api/log").then(l => { S.logs = l; renderLog(); }).catch(() => renderLog());
}

// --- events ----------------------------------------------------------------------------------------------
function connectEvents() {
  const es = new EventSource(url("/api/events"));
  S.events = es;
  es.onmessage = msg => {
    const e = JSON.parse(msg.data);
    const s = S.status;
    switch (e.type) {
      case "status":
        S.status = e;
        S.pages = e.pages;
        renderCycle(); renderLibrary(); renderStatus();
        break;
      case "runner": case "device": case "settings":
        reloadStatus();
        break;
      case "keyboard":
        if (s) { s.keyboard = e; renderStatus(); }
        break;
      case "tick":
        if (s) {
          Object.assign(s.runner, { fps: e.fps, frames: e.frames, page_index: e.page_index, visible: e.visible });
          if (e.page_index != null && S.pages[e.page_index]) { s.runner.label = S.pages[e.page_index].label; s.runner.page = S.pages[e.page_index].entry; }
          renderStatus();
          if (s.runner.state === "running" && e.frames) refreshMirror(e.frame_version);
        }
        break;
      case "frame":
        if (s && s.runner.state === "running") refreshMirror(e.frame_version);
        break;
      case "page":
        if (s) { s.runner.page_index = e.index; s.runner.label = e.label; renderStatus(); }
        break;
      case "visibility":
        if (s) { s.runner.visible = e.visible; renderStatus(); }
        break;
      case "knob": {
        const f = $("#knob-flash");
        f.textContent = { 1: "顺时针 ›", "-1": "‹ 逆时针", 0: "按下" }[e.direction] || "";
        f.style.opacity = 1;
        clearTimeout(f.timer);
        f.timer = setTimeout(() => (f.style.opacity = 0), 900);
        break;
      }
      case "knob_action":
        kbFlashGesture(e);
        break;
      case "knobs":
        kbKnobsChanged();
        break;
      case "selection":
        S.pages = e.pages; renderCycle(); renderLibrary();
        if (s) s.pages = e.pages;
        break;
      case "log":
        S.logs.push({ time: e.time, message: e.message });
        if (S.logs.length > 400) S.logs.shift();
        if (S.view === "log") renderLog();
        break;
    }
  };
  es.onerror = () => { if (S.status) { S.status.runner.state = S.status.runner.state; } };
}

async function loadWidgets() {
  S.widgets = await api("GET", "/api/widgets");
  renderLibrary();
}

// --- wiring ----------------------------------------------------------------------------------------------
function wire() {
  $("#btn-run").onclick = () => {
    const active = S.status && ["starting", "running", "waiting"].includes(S.status.runner.state);
    act(() => api("POST", `/api/runner/${active ? "stop" : "start"}`), active ? "已停止，设备已释放" : null).then(reloadStatus);
  };
  $("#btn-restart").onclick = () => act(() => api("POST", "/api/runner/restart")).then(reloadStatus);
  $("#btn-agent-prompt").onclick = () => act(() => window.neos.copyAgentPrompt(), "已复制提示词，粘贴给智能体即可");
  for (const b of $$("[data-knob]")) b.onclick = () => act(() => api("POST", "/api/knob", { direction: Number(b.dataset.knob) }));
  $("#filters").onclick = e => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    S.filter = b.dataset.filter;
    $$("#filters .pill").forEach(x => x.classList.toggle("active", x === b));
    renderLibrary();
  };
  $("#d-close").onclick = closeDrawer;
  $("#scrim").onclick = closeDrawer;
  $("#d-primary").onclick = drawerPrimary;
  $("#d-remove").onclick = async () => { if (S.drawer && (await removePage(S.drawer.index))) closeDrawer(); };
  $("#d-file").onclick = () => S.drawer && window.neos.showItem(S.drawer.widget.path);
  $("#nav").onclick = e => { const b = e.target.closest(".nav-item"); if (b) showView(b.dataset.view); };
  $(".nav-foot").onclick = () => showView("screen");
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || KB.recording) return;
    if (!$("#drawer").classList.contains("hidden")) { closeDrawer(); return; }
    if (KB.target || KB.tdTarget || KB.macroPick != null) { KB.target = KB.tdTarget = KB.macroPick = null; renderKb(); }
  });
  $("#s-autostart").onchange = async e => { S.appSettings = await window.neos.setAppSettings({ autostart: e.target.checked }); toast(S.appSettings.autostart ? "已设为开机启动" : "已取消开机启动"); };
  $("#s-tray").onchange = async e => { S.appSettings = await window.neos.setAppSettings({ closeToTray: e.target.checked }); };
  $("#s-run").onchange = e => saveSetting({ run_on_start: e.target.checked });
  $("#s-interval").onchange = e => saveSetting({ interval: Number(e.target.value) });
  $("#s-quality").onchange = e => saveSetting({ quality: Number(e.target.value) });
  $("#s-add-dir").onclick = async () => {
    const dir = await window.neos.pickFolder();
    if (!dir) return;
    const current = (await api("GET", "/api/settings")).extra_dirs;
    const s = await act(() => api("PUT", "/api/settings", { extra_dirs: [...current, dir] }), "已添加文件夹");
    if (s) { renderDirs(s.extra_dirs); loadWidgets(); }
  };
  window.neos.onServiceExit(code => toast(`屏幕服务已退出（${code}），请重新打开应用`, true));
  window.neos.onServiceRestarted(({ service, code }) => {
    // the app started the service again after it died: new token, new event stream
    S.info.service = service;
    S.base = `http://127.0.0.1:${service.port}`;
    S.token = service.token;
    if (S.events) S.events.close();
    connectEvents();
    toast(`屏幕服务意外退出（${code}），已自动重启`);
  });
}

// --- screenshot hooks (app started with --shots) ---------------------------------------------------------
window.__shots = {
  ready: async (timeout = 60000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const imgs = $$("img[data-preview], #d-preview").filter(i => i.offsetParent !== null && i.getAttribute("src"));
      if (S.status && imgs.every(i => i.classList.contains("ready"))) return true;
      await new Promise(r => setTimeout(r, 250));
    }
    return false;
  },
  mirror: async (timeout = 60000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (S.status && S.status.runner.frames > 0 && $("#mirror-img").getAttribute("src")) return true;
      await new Promise(r => setTimeout(r, 250));
    }
    return false;
  },
  knob: direction => api("POST", "/api/knob", { direction }),
  openOptions: () => { showView("screen"); const i = S.pages.findIndex(p => p.name === "neos_photo"); openDrawer({ mode: "edit", index: i < 0 ? 0 : i }); },
  openLibraryItem: name => { showView("screen"); openDrawer({ mode: "add", name }); },
  view: name => showView(name),
  keyboardReady: async (timeout = 30000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end && !KB.state && !KB.error) await new Promise(r => setTimeout(r, 200));
    await new Promise(r => setTimeout(r, 300));
    return !!KB.state;
  },
  selectKey: (row, col) => { KB.target = { kind: "key", row, col, code: KB.state.keymap[KB.layer][row][col] }; renderKb(); },
  selectKnob: (knob, gesture, tab) => { KB.target = { kind: "knob", knob, gesture, tab }; renderKb(); },
  demoMacro: () => {
    KB.macroDraft = structuredClone(KB.state.macros);
    KB.macroDraft[0] = [{ type: "down", code: 0xe0 }, { type: "tap", code: 0x06 }, { type: "up", code: 0xe0 },
                        { type: "delay", ms: 80 }, { type: "text", text: "hello" }, { type: "tap", code: 0x28 }];
    renderKb();
  },
  openSettings: () => showView("settings"),
  closeAll: () => { closeDrawer(); KB.target = KB.tdTarget = KB.macroPick = null; },
  scrollLibrary: () => { showView("screen"); $("#content").style.scrollBehavior = "auto"; $("#library-section").scrollIntoView(); },
};

(async function main() {
  S.info = await window.neos.info();
  applyArt();
  wire();
  if (!S.info.service) { toast("服务没有启动（见数据文件夹里的 neos75d.log）", true); return; }
  S.base = `http://127.0.0.1:${S.info.service.port}`;
  S.token = S.info.service.token;
  showView(S.info.shots ? "screen" : localStorage.getItem("view") || "keys");
  await loadWidgets().catch(e => toast(e.message, true));
  connectEvents();
})();
