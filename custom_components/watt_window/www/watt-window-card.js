// Watt Window dashboard card. Plain web component: no build step, no dependencies.
// Reads the same watt_window/data websocket command as the sidebar page, so it needs
// no extra entities. Draws an hour bar per published price, coloured by how that price
// compares with the last weeks, and a lane per appliance with its cheapest start.
//
// type: custom:watt-window-card
// title: Cheapest time to start        (optional; "" hides the header)
// windows:                             (optional; default: every Watt Window length)
//   - 120                              a length in minutes, cheapest at any time
//   - minutes: 210                     or a lane of its own:
//     icon: mdi:dishwasher             shown instead of the length
//     name: Dishwasher                 label when there is no icon
//     period: overnight                any (default) / daytime / overnight
//     color: "#5b8fd1"
// Lengths Watt Window isn't set up for are worked out here, from the same prices.
// hours_back: 1                        past hours left on the chart
// hours_ahead: 36                      cap on future hours (default: every published price)
// chart_height: 110                    price plot height in px
// bar_minutes: 60                      minutes per bar (15 for every quarter-hour)
// history_days: 30                     how far back "cheap" and "pricey" are judged
// band_colors: {solar, cheap, usual, pricey, dearest}
// lane_labels: times                   times (after each lane) or none
// show_legend: true

const CARD_VERSION = "0.15.0";
const LANE_COLOUR = "#5b8fd1";
const BANDS = {
  solar: "#f7cf45",
  cheap: "#7fc79a",
  usual: "#a9c6e8",
  pricey: "#f2a07b",
  dearest: "#e2725b",
};
const BAND_WORDS = { solar: "your solar", cheap: "cheap", usual: "usual", pricey: "pricey", dearest: "dearest" };
const PERIODS = { any: null, daytime: "day", overnight: "night" };
const QUARTER = 900000;
const HOUR = 3600000;
// Same as Watt Window's own windows: starts this close to the best count as the same price.
const SAME_PRICE_TOLERANCE = 0.0005;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const quantile = (sorted, p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];

class WattWindowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
    this._history = null; // sorted hourly import prices from the recorder
  }

  static getStubConfig() {
    return { title: "Cheapest time to start" };
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
    if (!this._historyAt || Date.now() - this._historyAt > 6 * HOUR) await this._loadHistory();
    this._render();
  }

  // Hourly means of Watt Window's own import price sensor, from long-term statistics.
  async _loadHistory() {
    this._historyAt = Date.now();
    const id =
      this._config.history_entity ||
      Object.values(this._hass.entities || {}).find((e) => e.platform === "watt_window" && e.translation_key === "import_price")
        ?.entity_id;
    if (!id) return;
    const days = Number(this._config.history_days ?? 30);
    try {
      const res = await this._hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(Date.now() - days * 86400000).toISOString(),
        statistic_ids: [id],
        period: "hour",
        types: ["mean"],
      });
      const vals = (res[id] || []).map((r) => r.mean).filter((v) => typeof v === "number");
      // a few days of history is enough to tell a cheap hour from a dear one
      this._history = vals.length >= 72 ? vals.sort((a, b) => a - b) : null;
    } catch (e) {
      this._history = null;
    }
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
    if (this._data?.currency === "EUR") return p < 0.0005 ? "free" : `${(p * 100).toFixed(0)} c`;
    return `${p.toFixed(3)} ${esc(this._data?.currency)}`;
  }

  // Thresholds for cheap / pricey / dearest: the last weeks' hours, or what's published now.
  _thresholds(d) {
    const sorted = this._history || d.quarters.map((q) => q.import).sort((a, b) => a - b);
    return { cheap: quantile(sorted, 0.25), pricey: quantile(sorted, 0.75), dearest: quantile(sorted, 0.9) };
  }

  _band(imp, eff, th) {
    if (eff <= imp * 0.5) return "solar";
    if (imp <= th.cheap) return "cheap";
    if (imp >= th.dearest) return "dearest";
    if (imp >= th.pricey) return "pricey";
    return "usual";
  }

  // The cheapest run of quarters of this length from the current quarter on, like Watt Window's own.
  _cheapest(d, minutes) {
    const n = Math.max(1, Math.round(minutes / 15));
    const from = Math.floor(new Date(d.now).getTime() / QUARTER) * QUARTER;
    const qs = d.quarters.filter((q) => new Date(q.start).getTime() >= from);
    const avgs = [];
    let best = null;
    for (let i = 0; i + n <= qs.length; i++) {
      const t0 = new Date(qs[i].start).getTime();
      if (new Date(qs[i + n - 1].start).getTime() - t0 !== (n - 1) * QUARTER) { avgs.push(null); continue; }
      let sum = 0;
      for (let k = i; k < i + n; k++) sum += qs[k].effective;
      avgs.push(sum / n);
      if (best == null || sum / n < avgs[best] - 1e-12) best = i;
    }
    if (best == null) return null;
    let last = best;
    while (avgs[last + 1] != null && avgs[last + 1] <= avgs[best] + SAME_PRICE_TOLERANCE) last++;
    const start = new Date(qs[best].start).getTime();
    return {
      start: qs[best].start,
      end: new Date(start + n * QUARTER).toISOString(),
      latest_start: qs[last].start,
      average_price: avgs[best],
      active: start <= new Date(d.now).getTime(),
    };
  }

  // The lanes asked for, each resolved to its window (or null while prices are short).
  _lanes(d) {
    const wanted = this._config.windows ?? d.windows.map((w) => w.minutes);
    return wanted.map((item, i) => {
      const spec = typeof item === "object" ? item : { minutes: item };
      const minutes = Number(spec.minutes);
      const period = spec.period ?? "any";
      const length = d.windows.find((w) => w.minutes === minutes);
      let win;
      if (PERIODS[period]) win = length ? length[PERIODS[period]] : null;
      else win = length ? (length.start ? length : null) : this._cheapest(d, minutes);
      const hours = minutes / 60;
      return {
        key: i,
        icon: spec.icon,
        label: spec.name ?? (length ? length.label : `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`),
        colour: spec.color ?? LANE_COLOUR,
        win,
      };
    });
  }

  // ---------- render ----------
  _render() {
    if (!this._config) return;
    const d = this._data;
    const title = this._config.title ?? "Cheapest time to start";
    let body;
    if (this._error) body = `<div class="msg bad">Couldn't load Watt Window: ${esc(this._error)}</div>`;
    else if (!d) body = `<div class="msg">Loading…</div>`;
    else if (!d.quarters.length) body = `<div class="msg">No prices yet.</div>`;
    else body = this._chart(d, this._lanes(d));
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        ${title ? `<div class="header">${esc(title)}</div>` : ""}
        <div class="body ${title ? "" : "notitle"}">${body}</div>
      </ha-card>`;
  }

  _chart(d, lanes) {
    const now = new Date(d.now).getTime();
    const back = Number(this._config.hours_back ?? 1) * HOUR;
    const ahead = this._config.hours_ahead != null ? Number(this._config.hours_ahead) * HOUR : Infinity;
    const step = Math.max(15, Number(this._config.bar_minutes ?? 60)) * 60000;
    const qs = d.quarters.filter((q) => {
      const t = new Date(q.start).getTime();
      return t + QUARTER > now - back && t < now + ahead;
    });
    if (!qs.length) return `<div class="msg">No prices ahead yet.</div>`;
    const colours = { ...BANDS, ...(this._config.band_colors || {}) };
    const th = this._thresholds(d);

    // one bar per step (an hour by default): average grid price, coloured by band
    const groups = new Map();
    for (const q of qs) {
      const t = new Date(q.start).getTime();
      const k = Math.floor(t / step) * step;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(q);
    }
    const bars = [...groups.entries()].map(([t, g]) => {
      const imp = g.reduce((s, q) => s + q.import, 0) / g.length;
      const eff = g.reduce((s, q) => s + q.effective, 0) / g.length;
      return { t, end: t + g.length * QUARTER, imp, band: this._band(imp, eff, th) };
    });

    const W = 460, top = 8, axisH = 30, left = 34, right = 8;
    const plotH = Number(this._config.chart_height ?? 110);
    const H = top + plotH + axisH;
    const plotW = W - left - right;
    const t0 = bars[0].t;
    const t1 = bars[bars.length - 1].end;
    const x = (t) => left + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * plotW;
    const maxP = Math.max(0.01, ...bars.map((b) => b.imp));
    const y = (p) => top + plotH - (Math.max(0, p) / maxP) * plotH;
    const gap = bars.length > 40 ? 1 : 3;
    let svg = "";

    for (const p of [maxP / 2, maxP]) {
      svg += `<line x1="${left}" x2="${W - right}" y1="${y(p)}" y2="${y(p)}" class="grid"/>`;
      svg += `<text x="${left - 4}" y="${y(p) + 4}" text-anchor="end" class="ax">${d.currency === "EUR" ? (p * 100).toFixed(0) + "c" : p.toFixed(2)}</text>`;
    }
    for (const b of bars) {
      const bx = x(b.t) + gap / 2, bw = Math.max(1, x(b.end) - x(b.t) - gap);
      const by = y(b.imp), bh = Math.max(1, top + plotH - by);
      const r = Math.min(5, bw / 2, bh);
      const fill = esc(colours[b.band]);
      const past = b.end <= now ? ` opacity="0.45"` : "";
      // rounded top, square base
      svg += `<path d="M ${bx} ${by + bh} V ${by + r} Q ${bx} ${by} ${bx + r} ${by} H ${bx + bw - r} Q ${bx + bw} ${by} ${bx + bw} ${by + r} V ${by + bh} Z" fill="${fill}"${past}/>`;
    }
    const hourStep = (t1 - t0) / HOUR > 24 ? 6 : 3;
    for (let t = Math.ceil(t0 / HOUR) * HOUR; t <= t1; t += HOUR) {
      const [hh] = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hour12: false, timeZone: this._tz })
        .format(new Date(t)).split(":").map(Number);
      if (hh === 0 && t < t1) {
        svg += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - 2}" class="midnight"/>`;
        // near the right edge the day name goes before the line instead of after it
        const end = x(t) > W - right - 70;
        svg += `<text x="${x(t) + (end ? -3 : 3)}" y="${top + plotH + 26}" text-anchor="${end ? "end" : "start"}" class="ax day">${this._dayWord(new Date(t).toISOString())}</text>`;
      }
      if (hh % hourStep === 0) svg += `<text x="${x(t)}" y="${top + plotH + 13}" text-anchor="middle" class="ax">${String(hh).padStart(2, "0")}</text>`;
    }
    if (now >= t0 && now <= t1) {
      svg += `<line x1="${x(now)}" x2="${x(now)}" y1="${top}" y2="${top + plotH}" class="now"/>`;
    }
    const chart = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Electricity price per hour, coloured from cheap to dear compared with recent weeks">${svg}</svg>`;

    // lanes in HTML, so they can carry Home Assistant icons; same x scale as the chart
    const pct = (t) => ((x(t) - left) / plotW) * 100;
    const times = (this._config.lane_labels ?? "times") !== "none";
    const rows = lanes.map((l) => {
      const head = l.icon
        ? `<ha-icon icon="${esc(l.icon)}" title="${esc(l.label)}"></ha-icon>`
        : `<span class="ln">${esc(l.label)}</span>`;
      if (!l.win) return `<div class="lane"><div class="lh">${head}</div><div class="trk"><span class="lt" style="left:2%">not enough prices yet</span></div></div>`;
      const w = l.win;
      const a = new Date(w.start).getTime(), b = new Date(w.end).getTime();
      const late = w.latest_start ? new Date(w.latest_start).getTime() + (b - a) : b;
      const pa = pct(a), pb = pct(b), pl = pct(late);
      const text = `${w.active ? "now" : this._time(w.start)}–${this._time(w.end)} · ${this._price(w.average_price)}`;
      const after = Math.max(pb, pl) < 62;
      const label = !times ? "" : after
        ? `<span class="lt" style="left:calc(${Math.max(pb, pl)}% + 6px)">${text}</span>`
        : `<span class="lt end" style="right:calc(${100 - pa}% + 6px)">${text}</span>`;
      return `<div class="lane"><div class="lh">${head}</div><div class="trk">
        ${pl > pb ? `<div class="pill tail" style="left:${pb}%;width:${pl - pb}%;background:${esc(l.colour)}"></div>` : ""}
        <div class="pill" style="left:${pa}%;width:${Math.max(1.5, pb - pa)}%;background:${esc(l.colour)}"></div>
        ${label}</div></div>`;
    });
    const inLegend = Object.keys(BANDS).filter((k) => k !== "solar" || bars.some((b) => b.band === "solar"));
    const legend = this._config.show_legend === false ? "" :
      `<div class="lg">${inLegend.map((k) => `<span><i style="background:${esc(colours[k])}"></i>${BAND_WORDS[k]}</span>`).join("")}</div>`;
    return chart + `<div class="lanes" style="--gutter:${(left / W) * 100}%;--right:${(right / W) * 100}%">${rows.join("")}</div>` + legend;
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
  .grid { stroke: var(--divider-color); stroke-width:1; stroke-dasharray:1 3; shape-rendering:crispEdges; }
  .midnight { stroke: var(--secondary-text-color); stroke-width:1; stroke-dasharray:3 3; shape-rendering:crispEdges; }
  .now { stroke: var(--error-color, #e53935); stroke-width:2; shape-rendering:crispEdges; }
  .msg { font-size:14px; color: var(--secondary-text-color); padding: 8px 4px; }
  .bad { color: var(--error-color); }
  .lanes { margin-top:2px; }
  .lane { display:flex; align-items:center; height:30px; }
  .lh { width:var(--gutter); flex:none; display:flex; justify-content:flex-end; padding-right:4px; box-sizing:border-box; color: var(--primary-text-color); }
  .lh ha-icon { --mdc-icon-size:24px; }
  .ln { font-size:11px; font-weight:600; white-space:nowrap; }
  .trk { position:relative; flex:1; height:20px; margin-right:var(--right); border-radius:10px; background: var(--secondary-background-color, #efece4); }
  .pill { position:absolute; top:0; height:20px; border-radius:10px; }
  .pill.tail { opacity:0.4; }
  .lt { position:absolute; top:0; line-height:20px; font-size:13px; white-space:nowrap; color: var(--primary-text-color); }
  .lg { display:flex; flex-wrap:wrap; gap:4px 12px; margin:8px 0 0 var(--gutter, 7%); font-size:11px; color: var(--secondary-text-color); }
  .lg i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:4px; vertical-align:-1px; }
`;

if (!customElements.get("watt-window-card")) {
  customElements.define("watt-window-card", WattWindowCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "watt-window-card",
    name: "Watt Window",
    description: "Price per hour, coloured from cheap to dear, with the cheapest times to start your appliances",
    documentationURL: "https://github.com/olig89/watt-window",
  });
}
console.info(`%c WATT-WINDOW-CARD %c ${CARD_VERSION} `, "background:#2e7d32;color:#fff", "");
