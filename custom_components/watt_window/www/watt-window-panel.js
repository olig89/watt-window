// Watt Window sidebar panel. Plain web component: no build step, no dependencies.
// Talks to the integration over two websocket commands: watt_window/data and watt_window/save.

const RATE_LABELS = {
  flat: "Network rate",
  day: "Day network rate",
  night: "Night network rate",
  weekday_peak: "Weekday peak network rate",
  weekend_peak: "Weekend peak network rate",
};
const PERIOD_NAMES = {
  flat: "Flat rate",
  day: "Day rate",
  night: "Night rate",
  weekday_peak: "Weekday peak",
  weekend_peak: "Weekend peak",
};
const RATE_KEYS = {
  flat: ["flat"],
  day_night: ["day", "night"],
  vork5: ["day", "night", "weekday_peak", "weekend_peak"],
};
const PLAN_NAMES = { flat: "One rate", day_night: "Day / night", vork5: "Day / night / winter peaks (Võrk 5)" };
const WINDOW_COLOURS = ["#2e7d32", "#1565c0", "#ef6c00", "#6a1b9a", "#00838f", "#ad1457", "#5d4037"];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class WattWindowPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "overview";
    this._data = null;
    this._error = null;
    this._draft = null;
    this._highlight = null;
    this._saving = false;
    this._notice = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._load();
      this._timer = setInterval(() => this._tab === "overview" && this._load(), 60000);
    }
    const mb = this.shadowRoot.querySelector("ha-menu-button");
    if (mb) mb.hass = hass;
  }

  set narrow(v) {
    this._narrow = v;
    const mb = this.shadowRoot.querySelector("ha-menu-button");
    if (mb) mb.narrow = v;
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "watt_window/data" });
      this._error = null;
      if (this._highlight == null && this._data.windows.length) this._highlight = this._data.windows[0].minutes;
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._render();
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
    const today = fmt(new Date());
    const tomorrow = fmt(new Date(Date.now() + 86400000));
    if (d === today) return "Today";
    if (d === tomorrow) return "Tomorrow";
    return new Date(iso).toLocaleDateString(undefined, { weekday: "long", timeZone: this._tz });
  }
  _when(iso) {
    const w = this._dayWord(iso);
    return `${w === "Today" || w === "Tomorrow" ? w.toLowerCase() : w} ${this._time(iso)}`;
  }
  _price(p) {
    if (p == null) return "–";
    return this._data?.currency === "EUR" ? `${(p * 100).toFixed(1)} c/kWh` : `${p.toFixed(3)} ${esc(this._data?.currency)}/kWh`;
  }
  _money(v) {
    if (v == null) return "–";
    return this._data?.currency === "EUR" ? `€${v.toFixed(2)}` : `${v.toFixed(2)} ${esc(this._data?.currency)}`;
  }

  // ---------- render ----------
  _render() {
    const d = this._data;
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="title">Watt Window</div>
      </div>
      <div class="tabs">
        <button class="tab ${this._tab === "overview" ? "on" : ""}" data-tab="overview">Overview</button>
        <button class="tab ${this._tab === "settings" ? "on" : ""}" data-tab="settings">Settings</button>
      </div>
      <div class="content">
        ${this._error ? `<div class="card warn">Couldn't load Watt Window: ${esc(this._error)}</div>` : ""}
        ${!d && !this._error ? `<div class="card">Loading…</div>` : ""}
        ${d ? (this._tab === "overview" ? this._overview(d) : this._settings(d)) : ""}
      </div>`;
    const mb = this.shadowRoot.querySelector("ha-menu-button");
    if (mb) { mb.hass = this._hass; mb.narrow = this._narrow; }
    this.shadowRoot.querySelectorAll("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        if (this._tab === "settings") this._draft = null;
        this._notice = null;
        this._render();
      })
    );
    if (d && this._tab === "overview") this._bindOverview();
    if (d && this._tab === "settings") this._bindSettings();
  }

  _overview(d) {
    const nowQ = d.quarters.find((q) => new Date(q.start) <= new Date(d.now) && new Date(d.now) - new Date(q.start) < 900000);
    const solarLine = d.solar.configured
      ? d.solar.ok ? `Solar: <b>${esc(d.solar.title || "Forecast.Solar")}</b>` : `<span class="bad">Solar forecast unavailable</span>`
      : `No solar forecast set`;
    const windows = d.windows.map((w, i) => this._windowCard(w, i, nowQ)).join("");
    return `
      <div class="card">
        <div class="now">
          <div><div class="k">Price now</div><div class="v">${this._price(nowQ?.import)}</div><div class="s">${esc(PERIOD_NAMES[nowQ?.period] || "")}</div></div>
          ${d.solar.configured ? `<div><div class="k">After solar</div><div class="v">${this._price(nowQ?.effective)}</div><div class="s">for a ${esc(d.settings.load_w)} W load</div></div>
          <div><div class="k">Solar forecast</div><div class="v">${nowQ ? (nowQ.solar_w / 1000).toFixed(1) : "–"} kW</div><div class="s">&nbsp;</div></div>` : ""}
        </div>
        <div class="meta">Prices known until ${d.prices_until ? `${this._when(d.prices_until)}` : "–"} · ${solarLine}</div>
        ${d.warnings.map((w) => `<div class="meta bad">${esc(w)}</div>`).join("")}
      </div>
      <h2>Cheapest windows</h2>
      <div class="windows">${windows || `<div class="card">No windows set up. Add some in Settings.</div>`}</div>
      <h2>The next two days</h2>
      <div class="card">
        ${this._chart(d)}
        <div class="legend">
          <span><i class="sw bar"></i>What a ${esc(d.settings.load_w)} W load costs each quarter-hour${d.solar.configured ? " (after solar)" : ""}</span>
          ${d.solar.configured ? `<span><i class="sw sun"></i>Solar forecast</span>` : ""}
          <span><i class="sw band"></i>Highlighted window:
            <select id="hl">${d.windows.map((w) => `<option value="${w.minutes}" ${w.minutes === this._highlight ? "selected" : ""}>${esc(w.label)}</option>`).join("")}</select></span>
        </div>
        <p class="explain">Each bar is the price of one quarter-hour: Nord Pool spot plus your network rate, fees and VAT.
        ${d.solar.configured ? "Where your panels are forecast to produce more than your typical house load, the spare output covers the load first, so that part only costs the export price you'd otherwise have earned." : ""}
        A window is the run of quarter-hours with the lowest average. Once a window has started it stays put, even if prices change.</p>
      </div>`;
  }

  _windowCard(w, i, nowQ) {
    const colour = WINDOW_COLOURS[i % WINDOW_COLOURS.length];
    if (!w.start) {
      return `<div class="card win" style="--c:${colour}"><div class="wl">${esc(w.label)}</div>
        <div class="s">Not enough price data yet. Tomorrow's prices usually arrive around 14:00.</div></div>`;
    }
    const when = w.active
      ? `<span class="chip">Now</span> until ${this._time(w.end)}`
      : `${this._dayWord(w.start)} ${this._time(w.start)}–${this._time(w.end)}`;
    const saving = nowQ && nowQ.import > 0 ? Math.round((1 - w.average_price / nowQ.import) * 100) : null;
    return `<div class="card win" style="--c:${colour}">
      <div class="wl">${esc(w.label)}</div>
      <div class="when">${when}</div>
      <div class="s">${this._price(w.average_price)} on average${w.solar_share > 0 ? ` · ${Math.round(w.solar_share * 100)}% solar` : ""}</div>
      <div class="s">≈ ${this._money(w.cost)} to run ${esc(this._data.settings.load_w)} W${saving != null && saving > 0 && !w.active ? ` · ${saving}% below the price now` : ""}</div>
    </div>`;
  }

  _chart(d) {
    const qs = d.quarters;
    if (!qs.length) return `<div class="s">No prices yet.</div>`;
    const W = 1000, H = 260, top = 12, bottom = 40, left = 44, right = d.solar.configured ? 44 : 12;
    const plotW = W - left - right, plotH = H - top - bottom;
    const t0 = new Date(qs[0].start).getTime();
    const t1 = new Date(qs[qs.length - 1].start).getTime() + 900000;
    const x = (t) => left + ((t - t0) / (t1 - t0)) * plotW;
    const vals = qs.map((q) => q.effective);
    const maxP = Math.max(0.01, ...vals, ...qs.map((q) => q.import));
    const minP = Math.min(0, ...vals);
    const y = (p) => top + plotH - ((p - minP) / (maxP - minP)) * plotH;
    const maxS = Math.max(1000, ...qs.map((q) => q.solar_w));
    const ys = (w) => top + plotH - (w / maxS) * plotH;
    const bw = Math.max(1, plotW / qs.length - 0.5);
    const now = new Date(d.now).getTime();
    let svg = "";

    // highlighted window band
    const hw = d.windows.find((w) => w.minutes === this._highlight);
    if (hw && hw.start) {
      const i = d.windows.indexOf(hw);
      svg += `<rect x="${x(new Date(hw.start).getTime())}" y="${top}" width="${x(new Date(hw.end).getTime()) - x(new Date(hw.start).getTime())}" height="${plotH}" fill="${WINDOW_COLOURS[i % WINDOW_COLOURS.length]}" opacity="0.14"/>`;
    }
    // solar area
    if (d.solar.configured) {
      let path = `M ${x(t0)} ${ys(0)}`;
      qs.forEach((q) => {
        const t = new Date(q.start).getTime();
        path += ` L ${x(t)} ${ys(q.solar_w)} L ${x(t + 900000)} ${ys(q.solar_w)}`;
      });
      path += ` L ${x(t1)} ${ys(0)} Z`;
      svg += `<path d="${path}" fill="#fbc02d" opacity="0.28"/>`;
    }
    // price bars
    qs.forEach((q) => {
      const t = new Date(q.start).getTime();
      const past = t + 900000 <= now;
      const y0 = y(Math.max(0, minP)), y1 = y(q.effective);
      const covered = q.effective < q.import - 1e-9;
      svg += `<rect x="${x(t)}" y="${Math.min(y0, y1)}" width="${bw}" height="${Math.max(0.5, Math.abs(y1 - y0))}" fill="${covered ? "#43a047" : "var(--primary-color)"}" opacity="${past ? 0.3 : 0.85}"/>`;
    });
    // gridlines + price axis
    for (let k = 0; k <= 4; k++) {
      const p = minP + ((maxP - minP) * k) / 4;
      svg += `<line x1="${left}" x2="${W - right}" y1="${y(p)}" y2="${y(p)}" stroke="var(--divider-color)" stroke-width="0.6"/>`;
      svg += `<text x="${left - 5}" y="${y(p) + 4}" text-anchor="end" class="ax">${d.currency === "EUR" ? (p * 100).toFixed(0) + "c" : p.toFixed(2)}</text>`;
    }
    if (d.solar.configured) {
      svg += `<text x="${W - right + 5}" y="${top + 8}" class="ax">${(maxS / 1000).toFixed(1)} kW</text>`;
    }
    // hour labels every 3 h, midnight lines
    for (let t = t0; t <= t1; t += 900000) {
      const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: this._tz }).format(new Date(t));
      const [hh, mm] = parts.split(":").map(Number);
      if (mm !== 0) continue;
      if (hh === 0) {
        svg += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${top + plotH}" stroke="var(--secondary-text-color)" stroke-width="0.8" stroke-dasharray="3 3"/>`;
        svg += `<text x="${x(t) + 4}" y="${H - 6}" class="ax day">${this._dayWord(new Date(t).toISOString())}</text>`;
      }
      if (hh % 3 === 0) svg += `<text x="${x(t)}" y="${top + plotH + 16}" text-anchor="middle" class="ax">${String(hh).padStart(2, "0")}</text>`;
    }
    // now line
    if (now >= t0 && now <= t1) {
      svg += `<line x1="${x(now)}" x2="${x(now)}" y1="${top}" y2="${top + plotH}" stroke="#e53935" stroke-width="1.5"/>`;
      svg += `<text x="${x(now) + 4}" y="${top + 10}" class="ax now">now</text>`;
    }
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Electricity price and solar forecast for the next two days">${svg}</svg>`;
  }

  _bindOverview() {
    const hl = this.shadowRoot.getElementById("hl");
    if (hl) hl.addEventListener("change", () => { this._highlight = Number(hl.value); this._render(); });
  }

  // ---------- settings ----------
  _settings(d) {
    if (!this._draft) {
      const s = d.settings;
      this._draft = {
        windows: [...(s.windows || [])],
        load_w: s.load_w,
        base_load_w: s.base_load_w,
        has_battery: !!s.has_battery,
        tariff: JSON.parse(JSON.stringify(s.tariff)),
      };
    }
    const f = this._draft, t = f.tariff;
    const rates = (RATE_KEYS[t.network_plan] || []).map((k) => `
      <label>${esc(RATE_LABELS[k])}<input type="number" step="any" data-rate="${k}" value="${t.network_rates[k] ?? 0}"></label>`).join("");
    const dayNight = t.network_plan === "day_night" ? `
      <label>Day rate starts (hour)<input type="number" min="0" max="24" data-t="day_start" value="${t.day_start}"></label>
      <label>Day rate ends (hour)<input type="number" min="0" max="24" data-t="day_end" value="${t.day_end}"></label>
      <label class="check"><input type="checkbox" data-tb="weekends_night" ${t.weekends_night ? "checked" : ""}> Weekends are night rate</label>
      <label class="check"><input type="checkbox" data-tb="holidays_night" ${t.holidays_night ? "checked" : ""}> Public holidays are night rate</label>` : "";
    return `
      ${this._notice ? `<div class="card ${this._notice.ok ? "ok" : "warn"}">${esc(this._notice.text)}</div>` : ""}
      <div class="card">
        <h3>Window lengths</h3>
        <p class="s">Each length gets a "Cheapest … window" sensor (its start time) and an "In cheapest … window" sensor (on while it's running).</p>
        <div class="chips">${f.windows.map((m) => `<span class="chip big">${esc(label(m))}<button data-rm="${m}" aria-label="Remove ${esc(label(m))}">×</button></span>`).join("")}</div>
        <div class="row">
          <label>Add a length (hours)<input type="number" step="0.25" min="0.25" max="24" id="addw" placeholder="e.g. 3 or 1.5"></label>
          <button class="btn" id="addbtn">Add</button>
        </div>
      </div>
      <div class="card">
        <h3>Your load</h3>
        <div class="grid">
          <label>Power of the things you run in a window (W)<input type="number" min="0" step="50" data-f="load_w" value="${f.load_w}"></label>
          <label>Typical house load (W)<input type="number" min="0" step="50" data-f="base_load_w" value="${f.base_load_w}"></label>
        </div>
        <label class="check"><input type="checkbox" id="battery" ${f.has_battery ? "checked" : ""}> I have a home battery</label>
        <p class="s">Leave this off for now. Battery-aware windows (store spare solar for later instead of using it straight away) come in a later version; the setting is saved but changes nothing yet.</p>
        <p class="s">Solar covers the house load first; only the rest counts towards running your load cheaply.
        Solar source: <b>${d.solar.configured ? esc(d.solar.title || "Forecast.Solar") : "none"}</b> —
        <a href="/config/integrations/integration/watt_window">change it in the integration settings</a>.</p>
      </div>
      <div class="card">
        <h3>Tariff: ${esc(PLAN_NAMES[t.network_plan] || t.network_plan)}</h3>
        <p class="s">Rates per kWh before VAT, in ${esc(d.currency)}. To switch tariff type, use the integration settings.</p>
        <div class="grid">
          ${rates}
          ${dayNight}
          <label>VAT (%)<input type="number" step="any" data-t="vat_pct" value="${+(t.vat * 100).toFixed(2)}"></label>
          <label>Supplier margin<input type="number" step="any" data-t="margin" value="${t.margin}"></label>
          <label>Other per-kWh charges<input type="number" step="any" data-t="other_per_kwh" value="${t.other_per_kwh}"></label>
          <label>Export fee<input type="number" step="any" data-t="export_fee" value="${t.export_fee}"></label>
        </div>
      </div>
      <div class="actions"><button class="btn primary" id="save" ${this._saving ? "disabled" : ""}>${this._saving ? "Saving…" : "Save"}</button></div>`;
  }

  _bindSettings() {
    const $ = (s) => this.shadowRoot.querySelectorAll(s);
    const f = this._draft;
    $("[data-rm]").forEach((b) => b.addEventListener("click", () => {
      f.windows = f.windows.filter((m) => m !== Number(b.dataset.rm));
      this._render();
    }));
    this.shadowRoot.getElementById("addbtn").addEventListener("click", () => {
      const v = Number(this.shadowRoot.getElementById("addw").value);
      const m = Math.round(v * 60);
      if (!v || m < 15 || m > 1440 || m % 15) {
        this._notice = { ok: false, text: "Window lengths are 0.25–24 hours, in quarter-hour steps." };
      } else if (!f.windows.includes(m)) {
        f.windows = [...f.windows, m].sort((a, b) => a - b);
        this._notice = null;
      }
      this._render();
    });
    $("[data-f]").forEach((i) => i.addEventListener("change", () => (f[i.dataset.f] = Number(i.value))));
    $("[data-rate]").forEach((i) => i.addEventListener("change", () => (f.tariff.network_rates[i.dataset.rate] = Number(i.value))));
    $("[data-t]").forEach((i) => i.addEventListener("change", () => {
      const k = i.dataset.t;
      if (k === "vat_pct") f.tariff.vat = Number(i.value) / 100;
      else f.tariff[k] = Number(i.value);
    }));
    this.shadowRoot.getElementById("battery").addEventListener("change", (e) => (f.has_battery = e.target.checked));
    $("[data-tb]").forEach((i) => i.addEventListener("change", () => (f.tariff[i.dataset.tb] = i.checked)));
    this.shadowRoot.getElementById("save").addEventListener("click", () => this._save());
  }

  async _save() {
    this._saving = true;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: "watt_window/save",
        windows: this._draft.windows,
        load_w: this._draft.load_w,
        base_load_w: this._draft.base_load_w,
        has_battery: this._draft.has_battery,
        tariff: this._draft.tariff,
      });
      this._notice = { ok: true, text: "Saved. Watt Window has recalculated with your new settings." };
      this._draft = null;
      await new Promise((r) => setTimeout(r, 1500)); // the integration reloads
      await this._load();
    } catch (e) {
      this._notice = { ok: false, text: `Not saved: ${e.message || e.code || e}` };
    }
    this._saving = false;
    this._render();
  }
}

function label(m) {
  if (m % 60 === 0) return `${m / 60} h`;
  if (m > 60 && m % 30 === 0) return `${m / 60} h`;
  return `${m} min`;
}

const STYLES = `
  :host { display:block; background: var(--primary-background-color); min-height:100vh; color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display:flex; align-items:center; height:56px; padding:0 8px; background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
  .title { font-size:20px; margin-left:8px; }
  .tabs { display:flex; gap:4px; padding:8px 16px 0; border-bottom:1px solid var(--divider-color); background: var(--card-background-color); }
  .tab { background:none; border:none; padding:10px 14px; font-size:14px; color: var(--secondary-text-color); cursor:pointer; border-bottom:2px solid transparent; }
  .tab.on { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  .content { max-width: 1000px; margin: 0 auto; padding: 16px; }
  h2 { font-size:16px; font-weight:500; margin:20px 4px 8px; }
  h3 { font-size:15px; font-weight:500; margin:0 0 8px; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); box-shadow: var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); padding:16px; margin-bottom:12px; }
  .card.warn { border-color: var(--error-color); }
  .card.ok { border-color: var(--success-color, #43a047); }
  .now { display:flex; flex-wrap:wrap; gap:28px; }
  .k { font-size:12px; color: var(--secondary-text-color); }
  .v { font-size:24px; font-weight:500; }
  .s { font-size:13px; color: var(--secondary-text-color); margin-top:2px; }
  .meta { font-size:12px; color: var(--secondary-text-color); margin-top:12px; }
  .bad { color: var(--error-color); }
  .windows { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:12px; }
  .windows .card { margin:0; border-left:4px solid var(--c); }
  .wl { font-size:13px; font-weight:500; color: var(--c); text-transform:uppercase; letter-spacing:.04em; }
  .when { font-size:18px; font-weight:500; margin:4px 0; }
  .chip { display:inline-flex; align-items:center; gap:4px; background: var(--c, var(--primary-color)); color:#fff; border-radius:10px; padding:1px 8px; font-size:12px; }
  .chip.big { background: var(--secondary-background-color); color: var(--primary-text-color); font-size:14px; padding:4px 4px 4px 10px; }
  .chip button { border:none; background:none; color: var(--secondary-text-color); font-size:16px; cursor:pointer; padding:0 4px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 12px; }
  svg { width:100%; height:auto; display:block; }
  .ax { font-size:11px; fill: var(--secondary-text-color); }
  .ax.day { font-weight:600; }
  .ax.now { fill:#e53935; }
  .legend { display:flex; flex-wrap:wrap; gap:16px; font-size:12px; color: var(--secondary-text-color); margin-top:8px; align-items:center; }
  .sw { display:inline-block; width:12px; height:10px; margin-right:6px; vertical-align:middle; border-radius:2px; }
  .sw.bar { background: var(--primary-color); } .sw.sun { background:#fbc02d; opacity:.5; } .sw.band { background:#2e7d32; opacity:.25; }
  .explain { font-size:13px; color: var(--secondary-text-color); line-height:1.5; margin:12px 0 0; }
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap:12px 16px; }
  label { display:flex; flex-direction:column; font-size:13px; color: var(--secondary-text-color); gap:4px; }
  label.check { flex-direction:row; align-items:center; gap:8px; }
  input[type=number], select { font:inherit; font-size:14px; padding:8px; border-radius:6px; border:1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  select { padding:2px 4px; }
  .row { display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; }
  .btn { font:inherit; font-size:14px; padding:8px 16px; border-radius:8px; border:1px solid var(--primary-color); background:none; color: var(--primary-color); cursor:pointer; }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .btn:disabled { opacity:.6; }
  .actions { display:flex; justify-content:flex-end; }
  a { color: var(--primary-color); }
`;

customElements.define("watt-window-panel", WattWindowPanel);
