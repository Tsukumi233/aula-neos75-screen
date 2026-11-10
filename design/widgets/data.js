// Widget data. Defaults are sample values (overridable with URL parameters for previews); the live runner
// calls window.neos75Update({...}) with real values, which re-renders through window.render().
const Q = new URLSearchParams(location.search);
const DATA = {
  time: Q.get("time") || "10:42:18",
  weekday: Q.get("weekday") || "Saturday", day: Q.get("day") || "07", month: Q.get("month") || "November",
  year: Q.get("year") || "2026",
  next: Q.get("next") || "Design review", nextAt: Q.get("nextAt") || "11:30 · in 48 min",
  cpu: +(Q.get("cpu") || 37), gpu: +(Q.get("gpu") || 52), mem: +(Q.get("mem") || 61),
  temp: +(Q.get("temp") || 58), disk: +(Q.get("disk") || 34), net: +(Q.get("net") || 12.4),
  title: Q.get("title") || "Aerial Cartography", artist: Q.get("artist") || "Low Tide Orchestra",
  album: Q.get("album") || "Coastlines", pos: +(Q.get("pos") || 134), dur: +(Q.get("dur") || 338),
  playing: true, has_media: true,
  // keyboard status bar (neos/status.js); live: Screen.keyboard(), null battery = not reported
  kb_battery: Q.get("kb_battery") === "none" ? null : +(Q.get("kb_battery") || 86),
  kb_charging: Q.get("kb_charging") === "1", kb_link: +(Q.get("kb_link") || 4),
  page_label: Q.get("page") ? `Page ${Q.get("page")}` : "",  // live: from the runner (cycle position)
  lvl_l: +(Q.get("lvl_l") || 0.42), lvl_r: +(Q.get("lvl_r") || 0.31),  // live: real output peak 0..1
  spec: [.78,.82,.8,.74,.7,.62,.58,.6,.55,.5,.56,.6,.52,.5,.54,.57,.52,.48,.42,.4,.33,.3,.25,.18],  // live: 24 bands 0..1
};
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function deriveTime() {
  if (DATA.now) {  // live: local wall clock from the runner's timestamp
    const d = new Date(DATA.now);
    DATA.time = [d.getHours(), d.getMinutes(), d.getSeconds()].map(v => String(v).padStart(2, "0")).join(":");
    DATA.weekday = WEEKDAYS[d.getDay()]; DATA.day = String(d.getDate()).padStart(2, "0");
    DATA.month = MONTHS[d.getMonth()]; DATA.year = String(d.getFullYear());
    const start = new Date(d.getFullYear(), 0, 1);
    const th = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 3 - (d.getDay() + 6) % 7);  // ISO 8601 week
    const week = 1 + Math.round(((th - new Date(th.getFullYear(), 0, 4)) / 864e5 - 3 + (new Date(th.getFullYear(), 0, 4).getDay() + 6) % 7) / 7);
    if (!DATA.nextLive) { DATA.next = `Week ${week}`; DATA.nextAt = `Day ${Math.floor((d - start) / 864e5) + 1} of ${d.getFullYear()}`; }
  }
  DATA.hms = DATA.time.split(":").map(Number);
}
deriveTime();
window.neos75Update = d => { Object.assign(DATA, d); deriveTime(); if (window.render) window.render(); };
