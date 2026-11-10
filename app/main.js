// Neos75 屏幕 — desktop app (Electron main process).
// Finds or starts the screen service (neos75d, Python SDK in ../sdk), opens the window, keeps a tray icon.
// Installed (tools/build_app.py): the service runs on the bundled Python in resources/python, and the pages
// come from resources/design (NEOS75_ROOT).
// Flags: --fake (service with the fake screen), --hidden (start in the tray; used for autostart),
//        --shots=DIR (take GUI screenshots into DIR and quit).
const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, nativeImage, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { spawn, execFileSync } = require("child_process");

const PACKAGED = app.isPackaged;
const ROOT = PACKAGED ? process.resourcesPath : path.resolve(__dirname, "..");
const ARGS = process.argv.slice(1);
const FAKE = ARGS.includes("--fake") || process.env.NEOS75_FAKE === "1";
const HIDDEN = ARGS.includes("--hidden");
const QUIT = ARGS.includes("--quit");      // ask the running app to quit (the installer, before replacing files)
const SHOTS = (ARGS.find(a => a.startsWith("--shots=")) || "").slice(8) || null;
const ART = path.join(ROOT, "design", "official", "jpg");

const dataDir = () => process.env.NEOS75_HOME || path.join(process.env.APPDATA || app.getPath("appData"), "neos75screen");
const discoveryPath = () => path.join(dataDir(), "service.json");
const appSettingsPath = () => path.join(dataDir(), "app.json");

let win = null, tray = null, child = null, service = null, quitting = false;
let deliberate = false;          // stopService is ending the service: do not restart it
const crashes = [];              // times of unexpected exits, to give up on a crash loop

// --- app settings (window/tray behaviour; the service has its own settings.json) ---------------------
function readAppSettings() {
  const defaults = { closeToTray: true };
  try { return { ...defaults, ...JSON.parse(fs.readFileSync(appSettingsPath(), "utf8")) }; } catch { return defaults; }
}
function writeAppSettings(s) {
  fs.mkdirSync(dataDir(), { recursive: true });
  fs.writeFileSync(appSettingsPath(), JSON.stringify(s, null, 2));
}

// --- service ---------------------------------------------------------------------------------------------
function request(port, method, p, token, body) {
  return new Promise((resolve, reject) => {
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = http.request({ host: "127.0.0.1", port, path: p, method, timeout: 2000,
      headers: { ...(token ? { "X-Neos75-Token": token } : {}), ...(data ? { "Content-Type": "application/json", "Content-Length": data.length } : {}) } },
      res => { let s = ""; res.on("data", c => (s += c)); res.on("end", () => { try { resolve(JSON.parse(s)); } catch (e) { reject(e); } }); });
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("timeout")));
    if (data) req.write(data);
    req.end();
  });
}

async function findService() {
  try {
    const info = JSON.parse(fs.readFileSync(discoveryPath(), "utf8"));
    const health = await request(info.port, "GET", "/api/health");
    if (health.ok && health.pid === info.pid) return info;
  } catch { /* not running */ }
  return null;
}

// written by tools/build_app.py: { version, repo, built }
function buildInfo() {
  try { return JSON.parse(fs.readFileSync(path.join(ROOT, "build-info.json"), "utf8")); } catch { return {}; }
}

function pythonPath() {
  if (PACKAGED) return path.join(ROOT, "python", "python.exe");
  const venv = path.join(ROOT, "sdk", ".venv", "Scripts", "python.exe");
  return fs.existsSync(venv) ? venv : "python";
}

function serviceEnv() {
  // NEOS75_PARENT_PID: the service stops when this app ends, even when it is killed
  const env = { ...process.env, PYTHONIOENCODING: "utf-8", NEOS75_PARENT_PID: String(process.pid) };
  if (PACKAGED) {
    env.NEOS75_ROOT = ROOT;
    delete env.PYTHONHOME;
    delete env.PYTHONPATH;
    // pages you or an agent write in the repository this app was built from show up without a rebuild
    const repo = buildInfo().repo;
    const custom = repo && path.join(repo, "design", "custom");
    if (custom && fs.existsSync(custom)) env.NEOS75_CUSTOM = custom;
  }
  return env;
}

// The development launcher (Neos75.cmd) registered autostart as Electron's default entry; the installed app
// takes it over so the keyboard screen does not start twice at login.
function adoptDevAutostart() {
  const repo = buildInfo().repo;
  if (!PACKAGED || !repo) return;
  const key = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", name = "electron.app.Electron";
  let out;
  try { out = execFileSync("reg", ["query", key, "/v", name], { encoding: "utf8", windowsHide: true }); } catch { return; }
  if (!out.toLowerCase().includes(path.join(repo, "app").toLowerCase())) return;
  try { execFileSync("reg", ["delete", key, "/v", name, "/f"], { windowsHide: true }); } catch { return; }
  app.setLoginItemSettings({ openAtLogin: true, path: process.execPath, args: ["--hidden"] });
}

async function ensureService() {
  let running = await findService();
  if (running && !!running.fake !== FAKE) {
    // e.g. a demo service left running: the app must not show simulated data when the keyboard is wanted
    try { await request(running.port, "POST", "/api/shutdown", running.token); } catch { /* gone */ }
    for (let i = 0; i < 50 && (await findService()); i++) await new Promise(r => setTimeout(r, 200));
    running = await findService();
    if (running) throw new Error("另一个屏幕服务仍在运行（模式不同），请先退出它");
  }
  if (running) return { ...running, spawned: false };
  fs.mkdirSync(dataDir(), { recursive: true });
  const log = fs.openSync(path.join(dataDir(), "neos75d.log"), "a");
  child = spawn(pythonPath(), ["-B", "-m", "neos75screen.service", ...(FAKE ? ["--fake"] : [])],
    { cwd: PACKAGED ? ROOT : path.join(ROOT, "sdk"), windowsHide: true, stdio: ["ignore", log, log],
      env: serviceEnv() });
  child.on("exit", code => {
    child = null;
    if (quitting || deliberate) return;
    // died on its own (2026-11-08: a native crash): start it again, unless it keeps dying
    const now = Date.now();
    crashes.push(now);
    while (crashes.length && now - crashes[0] > 10 * 60000) crashes.shift();
    if (crashes.length > 5) { if (win) win.webContents.send("service-exit", code); return; }
    setTimeout(async () => {
      if (quitting || deliberate || child) return;
      try {
        service = await ensureService();
        if (win) win.webContents.send("service-restarted", { service, code });
      } catch (e) {
        if (win) win.webContents.send("service-exit", code);
      }
    }, 2000);
  });
  for (let i = 0; i < 100; i++) {
    await new Promise(r => setTimeout(r, 200));
    const info = await findService();
    if (info) return { ...info, spawned: true };
    if (!child) break;
  }
  throw new Error(`屏幕服务没有启动，见 ${path.join(dataDir(), "neos75d.log")}`);
}

async function stopService() {
  if (!service || !service.spawned) return;
  deliberate = true;
  try {
    try { await request(service.port, "POST", "/api/shutdown", service.token); } catch { /* already gone */ }
    for (let i = 0; i < 30 && child; i++) await new Promise(r => setTimeout(r, 100));
    if (child) child.kill();
    for (let i = 0; i < 30 && child; i++) await new Promise(r => setTimeout(r, 100));
  } finally {
    deliberate = false;
  }
}

// --- window and tray -------------------------------------------------------------------------------------
function iconImage(size) {
  // the chrome ring of the official artwork when the UI pack is unpacked, else our own ring
  const official = path.join(ART, "sg25.jpg");
  if (fs.existsSync(official)) {
    const img = nativeImage.createFromPath(official);
    if (!img.isEmpty()) return img.crop({ x: 53, y: 3, width: 134, height: 134 }).resize({ width: size, height: size, quality: "best" });
  }
  return nativeImage.createFromPath(path.join(__dirname, "assets", "ring.png")).resize({ width: size, height: size });
}

function createWindow() {
  win = new BrowserWindow({
    width: SHOTS ? 1440 : 1360, height: SHOTS ? 900 : 880, minWidth: 1120, minHeight: 720,
    backgroundColor: "#000000", show: !HIDDEN && !SHOTS, title: FAKE ? "Neos75 屏幕（模拟设备）" : "Neos75 屏幕", icon: iconImage(256),
    titleBarStyle: "hidden", titleBarOverlay: { color: "#000000", symbolColor: "#cfcfcf", height: 44 },
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true,
                      backgroundThrottling: false },
  });
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  win.on("close", () => {
    // Closing the window really closes it, also to the tray: a hidden window kept its renderer and GPU memory
    // (about 100 MB) and kept fetching the live mirror. The tray opens a new one. Without "close to tray",
    // closing the window quits the app.
    if (!quitting && !SHOTS && !readAppSettings().closeToTray) { quitting = true; app.quit(); }
  });
  win.on("closed", () => { win = null; });
  if (SHOTS) win.once("ready-to-show", () => win.show());
}

function showWindow() { if (!win) createWindow(); win.show(); win.focus(); }

function createTray() {
  tray = new Tray(iconImage(32));
  tray.setToolTip(FAKE ? "Neos75 屏幕（模拟设备，未连接键盘）" : "Neos75 屏幕");
  const menu = () => Menu.buildFromTemplate([
    { label: "打开 Neos75 屏幕", click: showWindow },
    { type: "separator" },
    { label: "开始显示", click: () => service && request(service.port, "POST", "/api/runner/start", service.token).catch(() => {}) },
    { label: "停止显示（释放设备）", click: () => service && request(service.port, "POST", "/api/runner/stop", service.token).catch(() => {}) },
    { type: "separator" },
    { label: "退出", click: () => { quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu());
  tray.on("click", showWindow);
}

// --- IPC -------------------------------------------------------------------------------------------------
ipcMain.handle("info", () => ({ service, fakeRequested: FAKE, root: ROOT, art: fs.existsSync(ART) ? ART : null,
  dataDir: dataDir(), shots: !!SHOTS }));
ipcMain.handle("pick-image", async () => {
  const r = await dialog.showOpenDialog(win, { title: "选择图片", properties: ["openFile"],
    filters: [{ name: "图片", extensions: ["jpg", "jpeg", "png", "webp", "gif", "svg", "bmp"] }] });
  return r.canceled ? null : r.filePaths[0].replace(/\\/g, "/");
});
ipcMain.handle("pick-file", async (_e, extensions) => {
  const r = await dialog.showOpenDialog(win, { properties: ["openFile"],
    filters: extensions && extensions.length ? [{ name: "文件", extensions }] : [] });
  return r.canceled ? null : r.filePaths[0].replace(/\\/g, "/");
});
ipcMain.handle("pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("app-settings", () => ({ ...readAppSettings(), autostart: app.getLoginItemSettings({ args: ["--hidden"] }).openAtLogin }));
ipcMain.handle("set-app-settings", (_e, changes) => {
  const s = { ...readAppSettings() };
  if (typeof changes.closeToTray === "boolean") s.closeToTray = changes.closeToTray;
  writeAppSettings(s);
  if (typeof changes.autostart === "boolean" && !SHOTS) {
    // starts this app hidden in the tray at login; the app starts the service
    const exe = process.execPath, appArgs = app.isPackaged ? ["--hidden"] : [path.resolve(__dirname), "--hidden"];
    app.setLoginItemSettings({ openAtLogin: changes.autostart, path: exe, args: appArgs });
  }
  return { ...s, autostart: app.getLoginItemSettings({ args: app.isPackaged ? ["--hidden"] : [path.resolve(__dirname), "--hidden"] }).openAtLogin };
});
ipcMain.handle("show-item", (_e, p) => shell.showItemInFolder(p));
ipcMain.handle("open-path", (_e, p) => shell.openPath(p));
ipcMain.handle("restart-service", async () => {
  await stopService();
  service = await ensureService();
  return service;
});

// --- screenshots (--shots=DIR) ---------------------------------------------------------------------------
async function takeShots() {
  const dir = path.resolve(SHOTS);
  fs.mkdirSync(dir, { recursive: true });
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const run = js => win.webContents.executeJavaScript(js, true);
  const shot = async name => {
    win.webContents.invalidate();                 // repaint now, so the capture is not the previous frame
    await wait(400);
    fs.writeFileSync(path.join(dir, name), (await win.webContents.capturePage()).toPNG());
  };
  await run("window.__shots.ready(120000)");
  await run("window.__shots.mirror(60000)");
  await run("window.__shots.knob(1)");          // the screen knob turns to Page 8 (fake device)
  await wait(3500);
  await shot("01-screen.png");
  await run("window.__shots.openOptions()");
  await run("window.__shots.ready(60000)");
  await wait(1500);
  await shot("02-page-options.png");
  await run("window.__shots.closeAll(); window.__shots.openLibraryItem('neos_hifi_player')");
  await run("window.__shots.ready(60000)");
  await wait(1200);
  await shot("03-add-page.png");
  await run("window.__shots.closeAll(); window.__shots.scrollLibrary()");
  await wait(1500);
  await shot("04-library.png");
  // the keyboard's pages (a simulated keyboard in --fake mode)
  await run("window.__shots.closeAll(); window.__shots.view('keys')");
  await run("window.__shots.keyboardReady()");
  await run("window.__shots.selectKey(3, 1)");
  await wait(600);
  await shot("05-keys.png");
  await run("window.__shots.closeAll(); window.__shots.view('knobs')");
  await run("window.__shots.keyboardReady()");
  await run("window.__shots.selectKnob('screen', 'cw', 'kb')");
  await wait(600);
  await shot("06-knobs.png");
  await run("window.__shots.closeAll(); window.__shots.view('macros')");
  await run("window.__shots.demoMacro()");
  await wait(600);
  await shot("07-macros.png");
  for (const [name, file] of [["advanced", "08-advanced.png"], ["lighting", "09-lighting.png"],
                              ["performance", "10-performance.png"], ["device", "11-device.png"], ["settings", "12-settings.png"]]) {
    await run(`window.__shots.closeAll(); window.__shots.view('${name}')`);
    await wait(900);
    await shot(file);
  }
  quitting = true;
  app.quit();
}

// --- lifecycle -------------------------------------------------------------------------------------------
// screenshots run beside an installed copy: their own profile, so the single-instance lock does not stop them
if (SHOTS) app.setPath("userData", path.join(app.getPath("temp"), "neos75-shots-profile"));
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else if (QUIT) {
  app.quit();                               // nothing running: nothing to do
} else {
  app.on("second-instance", (_e, argv) => {
    if (argv.includes("--quit")) { quitting = true; app.quit(); }   // before-quit stops the service
    else showWindow();
  });
  app.whenReady().then(async () => {
    if (!SHOTS) { try { adoptDevAutostart(); } catch { /* keep the old entry */ } }
    try {
      service = await ensureService();
    } catch (e) {
      dialog.showErrorBox("Neos75 屏幕", String(e.message || e));
    }
    if (!HIDDEN || SHOTS) createWindow();   // started in the tray (login): no window until it is opened
    if (!SHOTS) createTray();
    else takeShots().catch(e => { console.error(e); quitting = true; app.quit(); });
  });
  app.on("window-all-closed", () => { if (quitting || SHOTS) app.quit(); });
  let stopping = false;
  app.on("before-quit", e => {
    quitting = true;
    if (!stopping && service && service.spawned && child) {  // the service we started ends with the app
      e.preventDefault();
      stopping = true;
      stopService().finally(() => app.exit(0));
    }
  });
}
