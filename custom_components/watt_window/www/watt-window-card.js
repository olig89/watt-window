// Watt Window dashboard card. Plain web component: no build step, no dependencies.
// Reads the same watt_window/data websocket command as the sidebar page, so it needs
// no extra entities, and draws the price timeline with a lane per Watt Window.
//
// type: custom:watt-window-card
// title: Best times to run            (optional; "" hides the header)
// windows:                            (optional; default: every Watt Window length)
//   - 120                             a length in minutes, cheapest at any time
//   - minutes: 120                    or a lane of its own:
//     period: overnight               any (default) / daytime / overnight
//     name: Dishwasher                lane label instead of "2 h"
// hours_back: 1                       past hours left on the chart
// hours_ahead: 36                     cap on future hours (default: every published price)
// chart_height: 110                   price plot height in px
// show_solar: true                    solar forecast behind the prices, when set up
// show_list: true                     a line per lane with its times and price
// colors: ["#2e7d32", "#1565c0"]      lane colours, in order
// bar_color: "#555"                   price bars (default: theme primary colour)

const CARD_VERSION = "0.13.1";
const COLOURS = ["#2e7d32", "#1565c0", "#ef6c00", "#6a1b9a", "#00838f", "#ad1457", "#5d4037"];
const PERIODS = { any: null, daytime: "day", overnight: "night" };
const PERIOD_WORDS = { any: "", daytime: "daytime", overnight: "overnight" };
const QUARTER = 900000;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class WattWindowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
  }

  static getStubConfig() {
    return { title: "Best times to run" };
  }

  setConfig(config) {
    const windows = config.windows ?? null;
    if (windows != null && !Array.isArray(windows)) throw new Error("windows must be a list");
    for (const w of windows || []) {
      const period = typeof w === "object" ? w.period ?? "any" : "any";
      if (!(period in PERIODS)) throw new Error(`period must be any, daytime or overnight (got ${period})`);
      if (!Number(typeof w === "object" ? w.minutes : w)) throw new Error("each window needs its length in minutes");
    }
    this._config = config;
    if (this._data) this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._start();
  }

  connectedCallback() {
    if (this._hass && !this._timer) this._start();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  _start() {
    this._load();
    this._timer = setInterval(() => this._load(), 60000);
  }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "watt_window/data" });
      this._error = null;
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._render();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6, rows: "auto" };
  }

  // ---------- formatting ----------
  get _tz() {
    return this._hass?.config?.time_zone;
  }
  _time(iso) {
    return new Date(iso).toLocaleTimeString(this._hass?.locale?.language || undefined, {
      hour: "2-digit", minute: "2-digit", timeZone: this._tz,
    });
  }
  _dayWord(iso) {
    const fmt = (d) => d.toLocaleDateString("en-CA", { timeZone: this._tz });
    const d = fmt(new Date(iso));
    if (d === fmt(new Date())) return "Today";
    if (d === fmt(new Date(Date.now() + 86400000))) return "Tomorrow";
    return new Date(iso).toLocaleDateString(undefined, { weekday: "long", timeZone: this._tz });
  }
  _price(p) {
    if (p == null) return "–";
    return this._data?.currency === "EUR" ? `${(p * 100).toFixed(1)} c` : `${p.toFixed(3)} ${esc(this._data?.currency)}`;
  }

  // The lanes asked for, each resolved to its window (or null while prices are short).
  _lanes(d) {
    const colours = this._config.colors?.length ? this._config.colors : COLOURS;
    const wanted = this._config.windows ?? d.windows.map((w) => w.minutes);
    return wanted.map((item, i) => {
      const spec = typeof item === "object" ? item : { minutes: item };
      const minutes = Number(spec.minutes);
      const period = spec.period ?? "any";
      const length = d.windows.find((w) => w.minutes === minutes);
      const win = !length ? null : PERIODS[period] ? length[PERIODS[period]] : length.start ? length : null;
      const label = spec.name ?? (length ? length.label : `${minutes / 60} h`);
      return {
        key: i,
        label,
        detail: spec.name ? `${length ? length.label : ""} ${PERIOD_WORDS[period]}`.trim() : PERIOD_WORDS[period],
        colour: colours[i % colours.length],
        missing: !length,
        win,
      };
    });
  }

  // ---------- render ----------
  _render() {
    if (!this._config) return;
    const d = this._data;
    const title = this._config.title ?? "Best times to run";
    let body;
    if (this._error) body = `<div class="msg bad">Couldn't load Watt Window: ${esc(this._error)}</div>`;
    else if (!d) body = `<div class="msg">Loading…</div>`;
    else if (!d.quarters.length) body = `<div class="msg">No prices yet.</div>`;
    else {
      const lanes = this._lanes(d);
      body = this._chart(d, lanes) + (this._config.show_list === false ? "" : this._list(d, lanes));
    }
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        ${title ? `<div class="header">${esc(title)}</div>` : ""}
        <div class="body ${title ? "" : "notitle"}">${body}</div>
      </ha-card>`;
  }

  _list(d, lanes) {
    return `<div class="list">${lanes.map((l) => {
      let when;
      if (l.missing) when = `<span class="s">No ${esc(l.label)} Watt Window set up</span>`;
      else if (!l.win) when = `<span class="s">Not enough prices yet</span>`;
      else {
        const w = l.win;
        const day = w.active ? "Now" : this._dayWord(w.start);
        const span = w.active ? `until ${this._time(w.end)}` : `${this._time(w.start)}–${this._time(w.end)}`;
        const flex = !w.active && w.latest_start && w.latest_start !== w.start ? ` <span class="s">(or start by ${this._time(w.latest_start)})</span>` : "";
        const unsettled = w.settled === false && !w.active ? `<span class="s" title="May change when the next prices arrive">*</span>` : "";
        when = `<span class="day">${day}</span> ${span}${unsettled}${flex}`;
      }
      return `<div class="row">
        <span class="sw" style="background:${esc(l.colour)}"></span>
        <span class="name">${esc(l.label)}${l.detail ? ` <span class="s">${esc(l.detail)}</span>` : ""}</span>
        <span class="when">${when}</span>
        <span class="price">${l.win ? this._price(l.win.average_price) : ""}</span>
      </div>`;
    }).join("")}</div>`;
  }

  _chart(d, lanes) {
    const now = new Date(d.now).getTime();
    const back = Number(this._config.hours_back ?? 1) * 3600000;
    const ahead = this._config.hours_ahead != null ? Number(this._config.hours_ahead) * 3600000 : Infinity;
    const qs = d.quarters.filter((q) => {
      const t = new Date(q.start).getTime();
      return t + QUARTER > now - back && t < now + ahead;
    });
    if (!qs.length) return `<div class="msg">No prices ahead yet.</div>`;
    const showSolar = d.solar.configured && this._config.show_solar !== false;
    const price = (q) => (d.solar.configured ? q.effective : q.import);
    const shown = lanes.filter((l) => l.win);
    const LANE_H = 13, LANE_GAP = 4;
    const W = 460, top = 6, axisH = 30, left = 34, right = 8;
    const plotH = Number(this._config.chart_height ?? 110);
    const lanesTop = top + plotH + axisH;
    const H = lanesTop + shown.length * (LANE_H + LANE_GAP) + 2;
    const plotW = W - left - right;
    const t0 = new Date(qs[0].start).getTime();
    const t1 = new Date(qs[qs.length - 1].start).getTime() + QUARTER;
    const x = (t) => left + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * plotW;
    const vals = qs.map(price);
    const maxP = Math.max(0.01, ...vals, ...qs.map((q) => q.import));
    const minP = Math.min(0, ...vals);
    const y = (p) => top + plotH - ((p - minP) / (maxP - minP)) * plotH;
    const maxS = Math.max(1000, ...qs.map((q) => q.solar_w));
    const ys = (w) => top + plotH - (w / maxS) * plotH;
    const bw = Math.max(1, plotW / qs.length - 0.4);
    const bar = this._config.bar_color ? esc(this._config.bar_color) : "var(--primary-color)";
    let svg = "";

    for (let k = 0; k <= 2; k++) {
      const p = minP + ((maxP - minP) * k) / 2;
      svg += `<line x1="${left}" x2="${W - right}" y1="${y(p)}" y2="${y(p)}" stroke="var(--divider-color)" stroke-width="1" stroke-dasharray="1 3" shape-rendering="crispEdges"/>`;
      svg += `<text x="${left - 4}" y="${y(p) + 4}" text-anchor="end" class="ax">${d.currency === "EUR" ? (p * 100).toFixed(0) + "c" : p.toFixed(2)}</text>`;
    }
    if (showSolar && qs.some((q) => q.solar_w > 0)) {
      let path = `M ${x(t0)} ${ys(0)}`;
      qs.forEach((q) => {
        const t = new Date(q.start).getTime();
        path += ` L ${x(t)} ${ys(q.solar_w)} L ${x(t + QUARTER)} ${ys(q.solar_w)}`;
      });
      path += ` L ${x(t1)} ${ys(0)} Z`;
      svg += `<path d="${path}" fill="#fbc02d" opacity="0.35"/>`;
    }
    qs.forEach((q) => {
      const t = new Date(q.start).getTime();
      const past = t + QUARTER <= now;
      const y0 = y(Math.max(0, minP)), y1 = y(price(q));
      svg += `<rect x="${x(t)}" y="${Math.min(y0, y1)}" width="${bw}" height="${Math.max(0.5, Math.abs(y1 - y0))}" fill="${bar}" opacity="${past ? 0.3 : 1}" shape-rendering="crispEdges"/>`;
    });
    const hourStep = (t1 - t0) / 3600000 > 24 ? 6 : 3;
    for (let t = Math.ceil(t0 / QUARTER) * QUARTER; t <= t1; t += QUARTER) {
      const [hh, mm] = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: this._tz })
        .format(new Date(t)).split(":").map(Number);
      if (mm !== 0) continue;
      if (hh === 0 && t < t1) {
        svg += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - 2}" stroke="var(--secondary-text-color)" stroke-width="1" stroke-dasharray="3 3" shape-rendering="crispEdges"/>`;
        // near the right edge the day name goes before the line instead of after it
        const end = x(t) > W - right - 70;
        svg += `<text x="${x(t) + (end ? -3 : 3)}" y="${top + plotH + 26}" text-anchor="${end ? "end" : "start"}" class="ax day">${this._dayWord(new Date(t).toISOString())}</text>`;
      }
      if (hh % hourStep === 0) svg += `<text x="${x(t)}" y="${top + plotH + 13}" text-anchor="middle" class="ax">${String(hh).padStart(2, "0")}</text>`;
    }
    // one lane per window: the window, plus a paler tail for later starts at the same price
    shown.forEach((l, i) => {
      const ly = lanesTop + i * (LANE_H + LANE_GAP);
      const w = l.win;
      const a = new Date(w.start).getTime(), b = new Date(w.end).getTime();
      const late = w.latest_start ? new Date(w.latest_start).getTime() + (b - a) : b;
      svg += `<rect x="${left}" y="${ly}" width="${plotW}" height="${LANE_H}" fill="var(--secondary-background-color, #eee)" opacity="0.5" rx="3"/>`;
      if (late > b) svg += `<rect x="${x(b)}" y="${ly}" width="${x(late) - x(b)}" height="${LANE_H}" fill="${esc(l.colour)}" opacity="0.35" rx="3"/>`;
      svg += `<rect x="${x(a)}" y="${ly}" width="${Math.max(3, x(b) - x(a))}" height="${LANE_H}" fill="${esc(l.colour)}" rx="3" shape-rendering="crispEdges"/>`;
      svg += `<text x="${left - 4}" y="${ly + LANE_H - 2}" text-anchor="end" class="ax lane">${esc(l.label.length > 6 ? l.label.slice(0, 5) + "…" : l.label)}</text>`;
    });
    if (now >= t0 && now <= t1) {
      svg += `<line x1="${x(now)}" x2="${x(now)}" y1="${top}" y2="${H - 2}" stroke="var(--error-color, #e53935)" stroke-width="2" shape-rendering="crispEdges"/>`;
    }
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Electricity price for every published quarter-hour, with a lane per Watt Window">${svg}</svg>`;
  }
}

const STYLES = `
  :host { display:block; }
  ha-card { overflow:hidden; }
  .header { font-size: var(--ha-card-header-font-size, 18px); font-weight: 500; padding: 12px 16px 0; color: var(--ha-card-header-color, var(--primary-text-color)); }
  .body { padding: 6px 12px 12px; }
  .body.notitle { padding-top: 12px; }
  svg { display:block; width:100%; height:auto; }
  .ax { font-size:11px; fill: var(--secondary-text-color); }
  .ax.day { font-weight:600; fill: var(--primary-text-color); }
  .ax.lane { font-weight:600; fill: var(--primary-text-color); }
  .msg { font-size:14px; color: var(--secondary-text-color); padding: 8px 4px; }
  .bad { color: var(--error-color); }
  .list { margin-top:6px; display:grid; grid-template-columns: auto auto 1fr auto; gap:3px 8px; align-items:baseline; font-size:14px; }
  .row { display:contents; }
  .sw { width:10px; height:10px; border-radius:2px; align-self:center; }
  .name { font-weight:500; white-space:nowrap; }
  .day { font-weight:500; }
  .s { font-size:12px; color: var(--secondary-text-color); }
  .price { text-align:right; white-space:nowrap; color: var(--secondary-text-color); }
`;

if (!customElements.get("watt-window-card")) {
  customElements.define("watt-window-card", WattWindowCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "watt-window-card",
    name: "Watt Window",
    description: "Price timeline with the cheapest times to run your appliances",
    documentationURL: "https://github.com/olig89/watt-window",
  });
}
console.info(`%c WATT-WINDOW-CARD %c ${CARD_VERSION} `, "background:#2e7d32;color:#fff", "");
