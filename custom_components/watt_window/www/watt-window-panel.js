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
// Must match manifest.json (a test checks). Compared with the running integration
// so a tab still holding old page code after an update says so.
const PANEL_VERSION = "0.9.0";

const DIRECTIONS = [
  [0, "North"], [45, "North-east"], [90, "East"], [135, "South-east"],
  [180, "South"], [225, "South-west"], [270, "West"], [315, "North-west"],
];
function loadPref(key, fallback) {
  try {
    const v = localStorage.getItem("watt-window:" + key);
    return v === null ? fallback : JSON.parse(v);
  } catch (e) {
    return fallback;
  }
}
function savePref(key, value) {
  try {
    localStorage.setItem("watt-window:" + key, JSON.stringify(value));
  } catch (e) {
    /* private window or storage blocked: the view just won't be remembered */
  }
}

function compassName(deg) {
  const names = ["North", "North-east", "East", "South-east", "South", "South-west", "West", "North-west"];
  return "About " + names[Math.round((((Number(deg) % 360) + 360) % 360) / 45) % 8].toLowerCase();
}
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
    this._focus = null; // a Watt Window length picked on the chart's lanes
    this._showSolar = loadPref("showSolar", true);
    this._expanded = new Set(loadPref("expanded", []));
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
        ${d && d.version && d.version !== PANEL_VERSION ? `<div class="card warn">Watt Window was updated to ${esc(d.version)}, but this page is still the old version (${PANEL_VERSION}). <button class="btn small" id="reload">Reload the page</button></div>` : ""}
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
    const reload = this.shadowRoot.getElementById("reload");
    if (reload) reload.addEventListener("click", () => location.reload());
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
        <div class="meta">${this._horizon(d)} · ${solarLine}</div>
        ${d.warnings.map((w) => `<div class="meta bad">${esc(w)}</div>`).join("")}
      </div>
      <h2>Cheapest Watt Windows</h2>
      <div class="windows">${windows || `<div class="card">No Watt Windows set up. Add some in Settings.</div>`}</div>
      <h2>Every price we know</h2>
      <div class="card">
        ${this._chart(d)}
        <div class="legend">
          <span><i class="sw bar"></i>What a ${esc(d.settings.load_w)} W load costs each quarter-hour${d.solar.configured ? (this._showSolar ? " (after solar)" : " (grid only, as if you had no solar)") : ""}</span>
          ${d.solar.configured && this._showSolar ? `<span><i class="sw sun"></i>Solar forecast</span>` : ""}
          ${d.solar.configured ? `<label class="check toggle"><input type="checkbox" id="showsolar" ${this._showSolar ? "checked" : ""}> Show solar</label>` : ""}
        </div>
        <p class="explain lanes-note">The coloured lanes under the chart are your Watt Windows, one lane per length. A paler tail means you could start later for the same price. Tap a lane to shade that Watt Window on the chart.</p>
        <p class="explain"><b>Why the chart stops where it does:</b> ${esc(d.price_source)} sets tomorrow's prices once a day, at an auction that closes at noon Central European time; they're published about 45 minutes later${d.next_prices_at ? ` (${this._when(d.next_prices_at)} your time)` : ""}. Before that, nobody knows prices beyond midnight CET, so the furthest anyone can see is roughly a day and a half, and some mornings less than a day.</p>
        ${d.solar.credit ? `<p class="explain credit">Solar estimate: ${esc(d.solar.credit)}.</p>` : ""}
        <p class="explain">Each bar is the price of one quarter-hour: ${esc(d.price_source)} spot plus your network rate, fees and VAT.
        ${d.solar.configured ? (d.settings.can_export
        ? "Where your panels are forecast to produce more than your typical house load, the spare output covers the load first, so that part only costs the export price you'd otherwise have earned."
        : "Where your panels are forecast to produce more than your typical house load, the spare output covers the load first. Your system doesn't export, so that spare power would otherwise be lost: using it is free.") : ""}
        A Watt Window is the run of quarter-hours with the lowest average. Once a Watt Window has started it stays put, even if prices change.</p>
      </div>`;
  }

  _horizon(d) {
    const until = d.prices_until ? this._when(d.prices_until) : "–";
    let next = "";
    if (d.next_prices_at) {
      next = new Date(d.next_prices_at) <= new Date(d.now)
        ? " · the next day's prices are due any minute"
        : ` · the next day's arrive ${this._when(d.next_prices_at)}`;
    }
    return `Prices known until ${until}${next}`;
  }

  _windowCard(w, i, nowQ) {
    const colour = WINDOW_COLOURS[i % WINDOW_COLOURS.length];
    if (!w.start) {
      return `<div class="card win" style="--c:${colour}"><div class="wl">${esc(w.label)}</div>
        <div class="s">Not enough prices published yet for a Watt Window this long.${this._data.next_prices_at ? ` More arrive ${this._when(this._data.next_prices_at)}.` : ""}</div></div>`;
    }
    const when = w.active
      ? `<span class="chip">Now</span> until ${this._time(w.end)}`
      : `${this._dayWord(w.start)} ${this._time(w.start)}–${this._time(w.end)}`;
    const saving = nowQ && nowQ.import > 0 ? Math.round((1 - w.average_price / nowQ.import) * 100) : null;
    const open = this._expanded.has(w.minutes);
    return `<div class="card win" style="--c:${colour}">
      <div class="head">
        <div class="wl">${esc(w.label)}</div>
        <button class="more" data-more="${w.minutes}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} details for ${esc(w.label)}" title="${open ? "Hide" : "Show"} details">
          <ha-icon icon="mdi:chevron-down" class="${open ? "flip" : ""}"></ha-icon>
        </button>
      </div>
      <div class="when">${when}</div>
      ${!w.active && w.latest_start && w.latest_start !== w.start
        ? `<div class="s flex">Same price if you start any time up to ${this._time(w.latest_start)}</div>` : ""}
      ${open ? `<div class="details">
        <div class="s muted">Cheapest in the prices known so far</div>
        <div class="s">${this._price(w.average_price)} on average${w.solar_share > 0 ? ` · ${Math.round(w.solar_share * 100)}% solar` : ""}</div>
        <div class="s">≈ ${this._money(w.cost)} to run ${esc(this._data.settings.load_w)} W${saving != null && saving > 0 && !w.active ? ` · ${saving}% below the price now` : ""}</div>
      </div>` : ""}
      <div class="split">
        ${this._periodLine("day", w.day)}
        ${this._periodLine("night", w.night)}
      </div>
    </div>`;
  }

  _periodLine(kind, p) {
    const icon = kind === "day" ? "mdi:weather-sunny" : "mdi:weather-night";
    const name = kind === "day" ? "Daytime" : "Overnight";
    const head = `<ha-icon icon="${icon}" class="pi" title="${name}"></ha-icon><span class="sr">${name}:</span>`;
    if (!p) return `<div class="period" title="${name}">${head}<span class="s">no prices yet</span></div>`;
    const when = p.active ? `now–${this._time(p.end)}` : `${this._shortDay(p.start)} ${this._time(p.start)}–${this._time(p.end)}`;
    const unsettled = p.settled || p.active ? "" : `<span class="warn-dot" title="May change when the next prices arrive">*</span>`;
    return `<div class="period" title="${name}: ${this._when(p.start)}, ${this._price(p.average_price)}${p.settled || p.active ? "" : " (may change when the next prices arrive)"}">
      ${head}<span class="pt">${when}${unsettled}</span><span class="s">${this._price(p.average_price)}</span></div>`;
  }

  _shortDay(iso) {
    const w = this._dayWord(iso);
    return w === "Today" ? "" : w === "Tomorrow" ? "Tmrw" : w.slice(0, 3);
  }

  _chart(d) {
    const qs = d.quarters;
    if (!qs.length) return `<div class="s">No prices yet.</div>`;
    const showSolar = d.solar.configured && this._showSolar;
    const price = (q) => (showSolar ? q.effective : q.import);
    const lanes = d.windows.filter((w) => w.start);
    const LANE_H = 14, LANE_GAP = 6;
    const W = 1000, top = 12, axisH = 40, left = 44, right = showSolar ? 44 : 12;
    const plotH = 208;
    const lanesTop = top + plotH + axisH;
    const H = lanesTop + lanes.length * (LANE_H + LANE_GAP) + 6;
    const plotW = W - left - right;
    const t0 = new Date(qs[0].start).getTime();
    const t1 = new Date(qs[qs.length - 1].start).getTime() + 900000;
    const x = (t) => left + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * plotW;
    const vals = qs.map(price);
    const maxP = Math.max(0.01, ...vals, ...qs.map((q) => q.import));
    const minP = Math.min(0, ...vals);
    const y = (p) => top + plotH - ((p - minP) / (maxP - minP)) * plotH;
    const maxS = Math.max(1000, ...qs.map((q) => q.solar_w));
    const ys = (w) => top + plotH - (w / maxS) * plotH;
    const bw = Math.max(1, plotW / qs.length - 0.5);
    const now = new Date(d.now).getTime();
    const colourOf = (w) => WINDOW_COLOURS[d.windows.indexOf(w) % WINDOW_COLOURS.length];
    let svg = "";

    // the Watt Window picked on a lane, shaded across the plot
    const fw = lanes.find((w) => w.minutes === this._focus);
    if (fw) {
      const a = new Date(fw.start).getTime(), b = new Date(fw.end).getTime();
      svg += `<rect x="${x(a)}" y="${top}" width="${x(b) - x(a)}" height="${plotH}" fill="${colourOf(fw)}" opacity="0.16"/>`;
    }
    if (showSolar) {
      let path = `M ${x(t0)} ${ys(0)}`;
      qs.forEach((q) => {
        const t = new Date(q.start).getTime();
        path += ` L ${x(t)} ${ys(q.solar_w)} L ${x(t + 900000)} ${ys(q.solar_w)}`;
      });
      path += ` L ${x(t1)} ${ys(0)} Z`;
      svg += `<path d="${path}" fill="#fbc02d" opacity="0.28"/>`;
    }
    qs.forEach((q) => {
      const t = new Date(q.start).getTime();
      const past = t + 900000 <= now;
      const y0 = y(Math.max(0, minP)), y1 = y(price(q));
      const covered = showSolar && q.effective < q.import - 1e-9;
      svg += `<rect x="${x(t)}" y="${Math.min(y0, y1)}" width="${bw}" height="${Math.max(0.5, Math.abs(y1 - y0))}" fill="${covered ? "#43a047" : "var(--primary-color)"}" opacity="${past ? 0.3 : 0.85}"/>`;
    });
    for (let k = 0; k <= 4; k++) {
      const p = minP + ((maxP - minP) * k) / 4;
      svg += `<line x1="${left}" x2="${W - right}" y1="${y(p)}" y2="${y(p)}" stroke="var(--divider-color)" stroke-width="0.6"/>`;
      svg += `<text x="${left - 5}" y="${y(p) + 4}" text-anchor="end" class="ax">${d.currency === "EUR" ? (p * 100).toFixed(0) + "c" : p.toFixed(2)}</text>`;
    }
    if (showSolar) svg += `<text x="${W - right + 5}" y="${top + 8}" class="ax">${(maxS / 1000).toFixed(1)} kW</text>`;
    for (let t = t0; t <= t1; t += 900000) {
      const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: this._tz }).format(new Date(t));
      const [hh, mm] = parts.split(":").map(Number);
      if (mm !== 0) continue;
      if (hh === 0) {
        svg += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - 4}" stroke="var(--secondary-text-color)" stroke-width="0.8" stroke-dasharray="3 3"/>`;
        svg += `<text x="${x(t) + 4}" y="${top + plotH + 32}" class="ax day">${this._dayWord(new Date(t).toISOString())}</text>`;
      }
      if (hh % 3 === 0) svg += `<text x="${x(t)}" y="${top + plotH + 16}" text-anchor="middle" class="ax">${String(hh).padStart(2, "0")}</text>`;
    }
    // one lane per Watt Window length: the window, plus a paler tail for later starts at the same price
    lanes.forEach((w, i) => {
      const ly = lanesTop + i * (LANE_H + LANE_GAP);
      const c = colourOf(w);
      const a = new Date(w.start).getTime(), b = new Date(w.end).getTime();
      const dur = b - a;
      const late = w.latest_start ? new Date(w.latest_start).getTime() + dur : b;
      const on = this._focus === w.minutes;
      svg += `<g class="lane" data-lane="${w.minutes}" tabindex="0" role="button" aria-pressed="${on}" aria-label="${esc(w.label)} Watt Window, ${esc(this._when(w.start))} to ${esc(this._time(w.end))}">`;
      svg += `<rect x="${left}" y="${ly}" width="${plotW}" height="${LANE_H}" fill="var(--secondary-background-color, #eee)" opacity="0.5" rx="3"/>`;
      if (late > b) svg += `<rect x="${x(b)}" y="${ly}" width="${x(late) - x(b)}" height="${LANE_H}" fill="${c}" opacity="0.3" rx="3"/>`;
      svg += `<rect x="${x(a)}" y="${ly}" width="${Math.max(3, x(b) - x(a))}" height="${LANE_H}" fill="${c}" rx="3" ${on ? `stroke="var(--primary-text-color)" stroke-width="1.5"` : ""}/>`;
      svg += `<text x="${left - 5}" y="${ly + LANE_H - 3}" text-anchor="end" class="ax lane-label" fill="${c}">${esc(w.label)}</text>`;
      svg += `</g>`;
    });
    if (now >= t0 && now <= t1) {
      svg += `<line x1="${x(now)}" x2="${x(now)}" y1="${top}" y2="${H - 4}" stroke="#e53935" stroke-width="1.5"/>`;
      svg += `<text x="${x(now) + 4}" y="${top + 10}" class="ax now">now</text>`;
    }
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Electricity price${showSolar ? " and solar forecast" : ""} for every quarter-hour with a published price, with a lane per Watt Window">${svg}</svg>`;
  }

  _bindOverview() {
    const r = this.shadowRoot;
    r.querySelectorAll("[data-more]").forEach((b) => b.addEventListener("click", () => {
      const m = Number(b.dataset.more);
      if (this._expanded.has(m)) this._expanded.delete(m); else this._expanded.add(m);
      savePref("expanded", [...this._expanded]);
      this._render();
    }));
    const solar = r.getElementById("showsolar");
    if (solar) solar.addEventListener("change", () => {
      this._showSolar = solar.checked;
      savePref("showSolar", this._showSolar);
      this._render();
    });
    const pick = (el) => {
      const m = Number(el.dataset.lane);
      this._focus = this._focus === m ? null : m;
      this._render();
    };
    r.querySelectorAll("[data-lane]").forEach((g) => {
      g.addEventListener("click", () => pick(g));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(g); } });
    });
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
        day_start: s.day_start,
        day_end: s.day_end,
        solar_entry_ids: [...(s.solar_entry_ids || [])],
        solar_source: s.solar_source || "none",
        can_export: s.can_export !== false,
        solar_planes: JSON.parse(JSON.stringify(s.solar_planes || [])),
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
        <h3>Watt Window lengths</h3>
        <p class="s">Each length gets its own Watt Window: a "Cheapest … window" sensor (its start time) and an "In cheapest … window" sensor (on while it's running).</p>
        <div class="chips">${f.windows.map((m) => `<span class="chip big">${esc(label(m))}<button data-rm="${m}" aria-label="Remove ${esc(label(m))}">×</button></span>`).join("")}</div>
        <div class="row">
          <label>Add a length (hours)<input type="number" step="0.25" min="0.25" max="24" id="addw" placeholder="e.g. 3 or 1.5"></label>
          <button class="btn" id="addbtn">Add</button>
        </div>
      </div>
      <div class="card">
        <h3>Day and night</h3>
        <p class="s">Each length also gets a cheapest <b>daytime</b> and cheapest <b>overnight</b> Watt Window, for things that must happen in one or the other. This is your day, not your tariff's.</p>
        <div class="grid">
          <label>Day starts at (hour)<input type="number" min="0" max="23" step="1" data-f="day_start" value="${f.day_start}"></label>
          <label>Day ends at (hour)<input type="number" min="1" max="24" step="1" data-f="day_end" value="${f.day_end}"></label>
        </div>
      </div>
      <div class="card">
        <h3>Solar</h3>
        <div class="radios">
          ${[["none", "No solar"], ["open_meteo", "Estimate it for me (free, Open-Meteo)"], ["forecast_solar", "Forecast.Solar"]]
            .map(([v, l]) => `<label class="check"><input type="radio" name="solar_source" value="${v}" ${f.solar_source === v ? "checked" : ""}> ${l}</label>`).join("")}
        </div>
        ${f.solar_source === "open_meteo" ? this._planesEditor(f) : ""}
        ${f.solar_source === "forecast_solar" ? (d.settings.solar_options.length
          ? `<p class="s">Tick the Forecast.Solar setup that covers your panels. If you tick more than one, their forecasts are added up.</p>
             ${d.settings.solar_options.map((o) => `<label class="check"><input type="checkbox" data-solar="${esc(o.entry_id)}" ${f.solar_entry_ids.includes(o.entry_id) ? "checked" : ""}> ${esc(o.title)}</label>`).join("")}`
          : `<p class="s">No Forecast.Solar setup yet. <a href="/config/integrations/dashboard/add?domain=forecast_solar">Add Forecast.Solar</a>, then come back here and tick it.</p>`) : ""}
        ${f.solar_source !== "none" ? `<label class="check" style="margin-top:12px"><input type="checkbox" id="canexport" ${f.can_export ? "checked" : ""}> My system sends spare power to the grid</label>
        <p class="s">Leave on if you're paid for (or can send) power you don't use. Turn off if your inverter holds the panels back instead (&quot;zero export&quot;): then spare solar is simply lost, so using it is free.</p>` : ""}
        <div class="grid" style="margin-top:12px">
          <label>What your house uses on its own (W)<input type="number" min="0" step="50" data-f="base_load_w" value="${f.base_load_w}"></label>
        </div>
        <p class="s">Your panels power the house first; only what's left over makes a Watt Window cheaper. If unsure, leave 500 W.</p>
      </div>
      <div class="card">
        <h3>Home battery</h3>
        <label class="check"><input type="checkbox" id="battery" ${f.has_battery ? "checked" : ""}> I have a home battery</label>
        <p class="s">Saved, but it doesn't change anything yet. Battery-aware Watt Windows (storing spare solar for later instead of using it straight away) come in a later version.</p>
      </div>
      <div class="card">
        <h3>Cost estimates</h3>
        <div class="grid">
          <label>Appliance power (W)<input type="number" min="0" step="50" data-f="load_w" value="${f.load_w}"></label>
        </div>
        <p class="s">Only used for the "≈ € to run" figures and for how much of a Watt Window solar can cover.</p>
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

  _planesEditor(f) {
    if (!f.solar_planes.length) f.solar_planes = [{ name: "Panels", kwp: 5, tilt: 35, direction: 180 }];
    const rows = f.solar_planes.map((p, i) => {
      const known = DIRECTIONS.some(([deg]) => deg === Number(p.direction));
      const dirOptions = DIRECTIONS.map(([deg, name]) => `<option value="${deg}" ${Number(p.direction) === deg ? "selected" : ""}>${name}</option>`).join("")
        + (known ? "" : `<option value="${p.direction}" selected>${compassName(p.direction)} (${Math.round(p.direction)}°)</option>`);
      return `<div class="plane">
        <label>Name<input type="text" data-plane="${i}" data-key="name" value="${esc(p.name)}"></label>
        <label>Size (kWp)<input type="number" min="0.1" step="0.1" data-plane="${i}" data-key="kwp" value="${p.kwp}"></label>
        <label>Faces<select data-plane="${i}" data-key="direction">${dirOptions}</select></label>
        <label>Exact bearing (°)<input type="number" min="0" max="359" step="1" data-plane="${i}" data-key="direction" value="${Math.round(p.direction)}"></label>
        <label>Tilt (°)<input type="number" min="0" max="90" step="1" data-plane="${i}" data-key="tilt" value="${p.tilt}"></label>
        ${f.solar_planes.length > 1 ? `<button class="btn small" data-rmplane="${i}" aria-label="Remove ${esc(p.name)}">Remove</button>` : ""}
      </div>`;
    }).join("");
    return `<p class="s">Only the size matters to start: south-facing at 35° is assumed until you say otherwise.
      If your panels are on more than one roof slope, add each one: its own direction and tilt make the forecast more accurate. Flat roof: tilt 0.
      <b>Faces</b> is fine as a rough pick; for steep roofs an <b>exact bearing</b> is worth it (0 north, 90 east, 180 south, 270 west; a satellite map is enough to measure it). <b>Tilt</b> is the roof pitch from horizontal, as on building drawings.</p>
      ${rows}
      <button class="btn small" id="addplane">Add a roof plane</button>
      <p class="s credit">Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo.com</a> (CC BY 4.0).</p>`;
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
        this._notice = { ok: false, text: "Watt Window lengths are 0.25–24 hours, in quarter-hour steps." };
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
    $('input[name="solar_source"]').forEach((r) => r.addEventListener("change", () => {
      f.solar_source = r.value;
      this._render();
    }));
    $("[data-plane]").forEach((i) => i.addEventListener("change", () => {
      const p = f.solar_planes[Number(i.dataset.plane)];
      p[i.dataset.key] = i.dataset.key === "name" ? i.value : Number(i.value);
      if (i.dataset.key === "direction") {
        p.direction = ((Math.round(p.direction) % 360) + 360) % 360;
        this._render(); // keep the dropdown and the exact bearing in step
      }
    }));
    $("[data-rmplane]").forEach((b) => b.addEventListener("click", () => {
      f.solar_planes.splice(Number(b.dataset.rmplane), 1);
      this._render();
    }));
    const canExport = this.shadowRoot.getElementById("canexport");
    if (canExport) canExport.addEventListener("change", (e) => (f.can_export = e.target.checked));
    const addPlane = this.shadowRoot.getElementById("addplane");
    if (addPlane) addPlane.addEventListener("click", () => {
      f.solar_planes.push({ name: `Plane ${f.solar_planes.length + 1}`, kwp: 2, tilt: 35, direction: 180 });
      this._render();
    });
    $("[data-solar]").forEach((i) => i.addEventListener("change", () => {
      const id = i.dataset.solar;
      f.solar_entry_ids = i.checked ? [...new Set([...f.solar_entry_ids, id])] : f.solar_entry_ids.filter((x) => x !== id);
    }));
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
        day_start: this._draft.day_start,
        day_end: this._draft.day_end,
        solar_entry_ids: this._draft.solar_entry_ids,
        solar_source: this._draft.solar_source,
        can_export: this._draft.can_export,
        ...(this._draft.solar_source === "open_meteo" ? { solar_planes: this._draft.solar_planes } : {}),
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
  .windows { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:12px; }
  @media (max-width: 720px) { .windows { grid-template-columns: 1fr; } }
  .windows .card { margin:0; border-left:4px solid var(--c); }
  .wl { font-size:13px; font-weight:500; color: var(--c); text-transform:uppercase; letter-spacing:.04em; }
  .when { font-size:18px; font-weight:500; margin:4px 0; }
  .flex { color: var(--primary-text-color); margin-bottom:4px; }
  .muted { color: var(--secondary-text-color); font-style: italic; }
  .plane { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:8px 12px; align-items:end; padding:10px 0; border-top:1px solid var(--divider-color); }
  .plane input[type=text] { font:inherit; font-size:14px; padding:8px; border-radius:6px; border:1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .btn.small { padding:6px 12px; font-size:13px; }
  .radios { display:flex; flex-wrap:wrap; gap:6px 18px; margin:4px 0 8px; }
  .credit { font-size:12px; }
  .head { display:flex; align-items:center; justify-content:space-between; }
  .more { background:none; border:none; padding:2px; margin:-4px -4px -4px 0; cursor:pointer; color: var(--secondary-text-color); border-radius:50%; width:32px; height:32px; display:inline-flex; align-items:center; justify-content:center; }
  .more:hover { background: var(--secondary-background-color); }
  .more ha-icon { transition: transform .2s; --mdc-icon-size: 22px; }
  .more ha-icon.flip { transform: rotate(180deg); }
  .details { margin: 4px 0; }
  .split { display:grid; grid-template-columns: 1fr 1fr; gap:6px 10px; }
  .period { display:flex; flex-wrap:wrap; align-items:center; gap:2px 6px; font-size:13px; min-width:0; }
  .period .pi { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .period .pt { font-weight:500; }
  .warn-dot { color: var(--warning-color, #f57c00); margin-left:1px; }
  .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
  .lane { cursor:pointer; }
  .lane:focus { outline: none; }
  .lane:focus rect:nth-of-type(1) { stroke: var(--primary-color); stroke-width: 1; }
  .lane-label { font-weight:600; }
  .toggle { flex-direction:row; }
  .lanes-note { margin-top:6px; }
  .split { border-top:1px solid var(--divider-color); margin-top:8px; padding-top:6px; }
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
