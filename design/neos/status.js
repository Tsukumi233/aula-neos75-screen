// Page number pill and keyboard status on the official status bar, as the stock firmware draws it (page code 0x35918, updaters
// 0x3370c / 0x337b0): battery percent white, centred in 56x19 at (238,50); the 8 px dot under "B" (195,55)
// grey, green while charging; the dots under Bluetooth (350,55), wired (385,55) and 2.4G (432,55) grey,
// red on the active connection. Load after the page script. DATA.kb_battery null = no report: dots stay grey.
(() => {
  const mk = (cls, x, y) => {
    const e = document.createElement("div");
    e.className = cls; e.style.left = `${x}px`; e.style.top = `${y}px`;
    document.body.appendChild(e); return e;
  };
  // <body data-status="off">: a canvas page (like stock page 5) without the status bar.
  const bar = document.body.dataset.status !== "off";
  const pct = bar ? mk("kb-pct", 238, 50) : null, charge = bar ? mk("kb-dot kb-charge", 195, 55) : null;
  const links = bar ? [[mk("kb-dot", 350, 55), l => l >= 1 && l <= 3], [mk("kb-dot", 385, 55), l => l === 5],
                       [mk("kb-dot", 432, 55), l => l === 4]] : [];
  // Page number pill over the baked "Page N" (same size, fill and hairline): the runner sets DATA.page_label
  // from the widget's place in the knob cycle (stock pages are 1-6, so the first widget is Page 7).
  // <body data-pill="off">: a page without it (e.g. a picture filling the screen).
  const pillAt = document.body.dataset.pill || "21,20";
  const [px, py] = pillAt.split(",").map(Number);
  const pill = pillAt === "off" ? null : mk("page-pill", px, py);
  const draw = () => {
    if (pill) {
      pill.textContent = DATA.page_label || "";
      pill.style.display = DATA.page_label ? "" : "none";
    }
    if (!bar) return;
    const known = DATA.kb_battery != null;
    pct.textContent = known ? `${DATA.kb_battery}%` : "";
    charge.classList.toggle("on", known && !!DATA.kb_charging);
    for (const [dot, active] of links) dot.classList.toggle("on", known && active(DATA.kb_link));
  };
  const page = window.render;
  window.render = () => { if (page) page(); draw(); };
  draw();
})();
