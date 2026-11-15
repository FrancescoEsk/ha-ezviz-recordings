/* EZVIZ Recordings card: daily list of SD-card clips for one camera, with a day selector.
 *
 *   type: custom:ezviz-recordings-card
 *   serial: ABC123456          # camera serial (required)
 *   title: Front door          # optional, defaults to the camera name
 *   max_height: 320px          # optional, height of the scrolling clip list
 *   autoplay_next: true        # optional, after a clip ends play the next older one
 *   sync: living_room          # optional, follow the day chosen in an ezviz-recordings-datebar with the same sync
 *   hide_bar: true             # optional, hide this card's own day selector (use with sync)
 *
 * Shared day bar (one selector for several recordings cards):
 *
 *   type: custom:ezviz-recordings-datebar
 *   sync: living_room          # required, same value as the cards it drives
 *   reset_after: 300           # optional, seconds without touches before going back to today (0 = never)
 */
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const TEXT = {
  en: {
    today: "Today", yesterday: "Yesterday", refresh: "Refresh", prev: "Previous day",
    next: "Next day", pick: "Choose the day", clips: "clips", updating: "updating…",
    loading: "Loading recordings… (the camera is waking up)",
    empty: "No recordings on this day", load_error: "Could not load the recordings",
    download_failed: "Download failed", downloading: (t) => `Downloading the ${t} clip…`,
    card_name: "EZVIZ Recordings",
    card_desc: "Daily list of a camera's recordings, with a day selector",
    bar_name: "EZVIZ Recordings - day bar",
    bar_desc: "One day selector for several recordings cards (same sync)",
  },
  it: {
    today: "Oggi", yesterday: "Ieri", refresh: "Aggiorna", prev: "Giorno precedente",
    next: "Giorno successivo", pick: "Scegli il giorno", clips: "clip", updating: "aggiorno…",
    loading: "Carico le registrazioni… (la telecamera si sta svegliando)",
    empty: "Nessuna registrazione in questo giorno", load_error: "Errore nel caricare le registrazioni",
    download_failed: "Download non riuscito", downloading: (t) => `Scarico la clip delle ${t}…`,
    card_name: "EZVIZ Registrazioni",
    card_desc: "Elenco giornaliero delle registrazioni di una telecamera, con selettore del giorno",
    bar_name: "EZVIZ Registrazioni - barra data",
    bar_desc: "Un unico selettore del giorno per più card registrazioni (stesso sync)",
  },
};
// Language of the HA user (HA sets <html lang>), English when not translated.
const lang = () => (document.documentElement.lang || navigator.language || "en").split("-")[0].toLowerCase();
const T = (key) => (TEXT[lang()] || TEXT.en)[key];
const SYNC_EVENT = "ezviz-recordings-day";
const syncDays = (window.__ezvizRecordingsDays = window.__ezvizRecordingsDays || {});
const sharedDay = (group) => (group && syncDays[group]) || isoDay(new Date());
const publishDay = (group, day) => {
  syncDays[group] = day;
  window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { sync: group, day } }));
};
const dayLabel = (day) => {
  const today = isoDay(new Date());
  const y = new Date();
  y.setDate(y.getDate() - 1);
  const d = new Date(day + "T12:00:00");
  const base = d.toLocaleDateString(lang(), { day: "numeric", month: "short", year: "numeric" });
  if (day === today) return `${T("today")} · ${base}`;
  if (day === isoDay(y)) return `${T("yesterday")} · ${base}`;
  const wd = d.toLocaleDateString(lang(), { weekday: "long" });
  return `${wd.charAt(0).toUpperCase()}${wd.slice(1)} · ${base}`;
};
const shiftDay = (day, days) => {
  const d = new Date(day + "T12:00:00");
  d.setDate(d.getDate() + days);
  const next = isoDay(d);
  return next > isoDay(new Date()) ? null : next;
};

class EzvizRecordingsCard extends HTMLElement {
  setConfig(config) {
    if (!config.serial) throw new Error("serial is required");
    this._config = { max_height: "320px", autoplay_next: true, ...config };
    this._day = sharedDay(this._config.sync);
    this._clips = null;
    this._current = null;
    this._error = null;
    this._loading = false;
    this._downloading = null;
    this._name = config.title || null;
    this._build();
    if (this.isConnected) this.connectedCallback();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._loadName();
      this._load();
    }
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getStubConfig() {
    return { serial: "" };
  }

  // ---------------------------------------------------------------- data
  async _loadName() {
    if (this._name) return this._render();
    try {
      const res = await this._hass.callWS({ type: "ezviz_recordings/cameras" });
      const cam = res.cameras.find((c) => c.serial === this._config.serial);
      this._name = cam ? cam.name : this._config.serial;
    } catch (e) {
      this._name = this._config.serial;
    }
    this._render();
  }

  async _load(refresh = false) {
    const day = this._day;
    this._loading = !refresh || !this._clips;
    this._updating = !this._loading;
    this._error = null;
    if (this._loading) this._clips = null;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "ezviz_recordings/clips",
        serial: this._config.serial,
        day,
        refresh,
      });
      if (day !== this._day) return;
      this._clips = res.clips;
      if (res.stale) this._update(day);
      if (day === isoDay(new Date())) this._syncToday(res.running);
    } catch (e) {
      if (day !== this._day) return;
      this._error = (e && e.message) || T("load_error");
    }
    this._loading = false;
    if (refresh) this._updating = false;
    this._render();
  }

  // Ask HA to download today's new clips (HA allows it at most every 10 min,
  // and only once even if several cards ask); then follow the new thumbnails.
  async _syncToday(alreadyRunning) {
    let running = alreadyRunning;
    try {
      const res = await this._hass.callWS({ type: "ezviz_recordings/sync" });
      running = res.started || res.running;
    } catch (e) {
      /* older backend or offline: nothing to follow */
    }
    if (running) this._followSync();
  }

  _followSync() {
    if (this._syncTimer) return;
    this._syncTimer = setInterval(async () => {
      const day = this._day;
      if (day !== isoDay(new Date()) || !this.isConnected) return this._stopFollow();
      try {
        const res = await this._hass.callWS({
          type: "ezviz_recordings/clips",
          serial: this._config.serial,
          day,
        });
        if (day !== this._day) return;
        const before = this._clips ? this._clips.filter((c) => c.thumb).length : 0;
        const after = res.clips.filter((c) => c.thumb).length;
        if (!this._clips || res.clips.length !== this._clips.length || after !== before) {
          this._clips = res.clips;
          this._render();
        }
        if (!res.running) this._stopFollow();
      } catch (e) {
        this._stopFollow();
      }
    }, 15000);
  }

  _stopFollow() {
    clearInterval(this._syncTimer);
    this._syncTimer = null;
  }

  connectedCallback() {
    if (!this._config || !this._config.sync || this._onSync) return;
    this._onSync = (e) => {
      if (e.detail.sync === this._config.sync) this._setDay(e.detail.day);
    };
    window.addEventListener(SYNC_EVENT, this._onSync);
    // The day may have changed while this card was not on the page.
    if (this._hass && this._day !== sharedDay(this._config.sync)) this._setDay(sharedDay(this._config.sync));
  }

  disconnectedCallback() {
    this._stopFollow();
    if (this._onSync) window.removeEventListener(SYNC_EVENT, this._onSync);
    this._onSync = null;
  }

  // Cached list was shown at once; fetch only the new clips in the background.
  async _update(day) {
    this._updating = true;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "ezviz_recordings/clips",
        serial: this._config.serial,
        day,
        update: true,
      });
      if (day === this._day) this._clips = res.clips;
    } catch (e) {
      /* keep the cached list; the refresh button retries */
    }
    this._updating = false;
    this._render();
  }

  async _play(clip) {
    const day = this._day;
    this._current = { day, key: clip.key, label: clip.begin };
    this._downloading = clip.key;
    this._error = null;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "ezviz_recordings/clip_url",
        serial: this._config.serial,
        day,
        key: clip.key,
      });
      if (!this._current || this._current.key !== clip.key) return;
      clip.cached = true;
      clip.thumb = res.thumb;
      this._downloading = null;
      this._render();
      const video = this.shadowRoot.querySelector("video");
      video.src = res.url;
      video.play().catch(() => {});
    } catch (e) {
      if (this._current && this._current.key === clip.key) {
        this._downloading = null;
        this._error = (e && e.message) || T("download_failed");
        this._render();
      }
    }
  }

  _ended() {
    if (!this._config.autoplay_next || !this._clips || !this._current) return;
    const i = this._clips.findIndex((c) => c.key === this._current.key);
    if (i >= 0 && i + 1 < this._clips.length) this._play(this._clips[i + 1]);
  }

  _shift(days) {
    const next = shiftDay(this._day, days);
    if (next) this._setDay(next);
  }

  _setDay(day) {
    if (!day || day === this._day) return;
    this._day = day;
    this._current = null;
    this._downloading = null;
    const video = this.shadowRoot.querySelector("video");
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    this._load();
  }

  _dayLabel() {
    return dayLabel(this._day);
  }

  // ---------------------------------------------------------------- view
  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .head { display: flex; align-items: center; gap: 8px; padding: 12px 16px 4px; }
        .title { font-size: 1.1em; font-weight: 500; flex: 1; min-width: 0;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .bar { display: flex; align-items: center; gap: 6px; padding: 4px 12px 8px; flex-wrap: wrap; }
        .daylabel { flex: 1; min-width: 120px; text-align: center; font-weight: 500; }
        button { font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.15));
                 border: none; border-radius: 8px; padding: 6px 10px; cursor: pointer; }
        button:disabled { opacity: .35; cursor: default; }
        button.icon { width: 36px; padding: 6px 0; font-size: 1.1em; }
        input[type=date] { font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.15));
                 border: none; border-radius: 8px; padding: 5px 8px; color-scheme: light dark; }
        .player { position: relative; background: #000; aspect-ratio: 16 / 9; display: none; }
        .player.on { display: block; }
        video { width: 100%; height: 100%; display: block; }
        .overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
                   justify-content: center; color: #fff; gap: 10px; background: rgba(0,0,0,.55); font-size: .95em; }
        .overlay[hidden] { display: none; }
        .spin { width: 28px; height: 28px; border: 3px solid rgba(255,255,255,.3); border-top-color: #fff;
                border-radius: 50%; animation: s 1s linear infinite; }
        @keyframes s { to { transform: rotate(360deg); } }
        .refresh.busy { animation: s 1s linear infinite; }
        .list { max-height: var(--ezr-max-height); overflow-y: auto; padding: 4px 8px 10px; }
        .clip { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
        .clip:hover { background: var(--secondary-background-color, rgba(127,127,127,.12)); }
        .clip.cur { background: rgba(var(--rgb-primary-color, 3,169,244), .18); }
        .clip .t { font-variant-numeric: tabular-nums; font-weight: 500; }
        .clip .d { color: var(--secondary-text-color); font-size: .9em; flex: 1; }
        .clip .c { color: var(--secondary-text-color); font-size: .8em; }
        .play { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
                background: var(--primary-color); color: var(--text-primary-color, #fff); font-size: 11px; flex: none; }
        .lead { width: 64px; height: 36px; flex: none; display: flex; align-items: center; justify-content: center;
                position: relative; border-radius: 6px; overflow: hidden; }
        .lead.has { background: #000; }
        .lead img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .lead .mini { position: absolute; right: 3px; bottom: 3px; width: 14px; height: 14px; border-radius: 50%;
                      background: rgba(0,0,0,.6); color: #fff; font-size: 7px; display: flex; align-items: center;
                      justify-content: center; }
        .msg { padding: 14px 16px 18px; color: var(--secondary-text-color); text-align: center; }
        .err { color: var(--error-color, #db4437); }
        .count { color: var(--secondary-text-color); font-size: .85em; }
        .bar[hidden] { display: none; }
      </style>
      <ha-card>
        <div class="head"><div class="title"></div><span class="count"></span>
          <button class="icon refresh" title="${T("refresh")}">⟳</button></div>
        <div class="bar">
          <button class="icon prev" title="${T("prev")}">‹</button>
          <div class="daylabel"></div>
          <button class="icon next" title="${T("next")}">›</button>
          <input type="date" class="pick" title="${T("pick")}">
          <button class="today">${T("today")}</button>
        </div>
        <div class="player"><video controls playsinline preload="none"></video>
          <div class="overlay" hidden><div class="spin"></div><div class="otext"></div></div></div>
        <div class="list"></div>
      </ha-card>`;
    const $ = (s) => this.shadowRoot.querySelector(s);
    $(".prev").addEventListener("click", () => this._shift(-1));
    $(".next").addEventListener("click", () => this._shift(1));
    $(".today").addEventListener("click", () => this._setDay(isoDay(new Date())));
    $(".refresh").addEventListener("click", () => this._load(true));
    $(".pick").addEventListener("change", (e) => this._setDay(e.target.value));
    $("video").addEventListener("ended", () => this._ended());
    $(".bar").hidden = !!this._config.hide_bar;
    this.shadowRoot.host.style.setProperty("--ezr-max-height", this._config.max_height);
  }

  _render() {
    if (!this.shadowRoot) return;
    const $ = (s) => this.shadowRoot.querySelector(s);
    const today = isoDay(new Date());
    $(".title").textContent = this._name || "";
    $(".daylabel").textContent = this._dayLabel();
    $(".next").disabled = this._day >= today;
    $(".today").disabled = this._day === today;
    const pick = $(".pick");
    pick.max = today;
    pick.value = this._day;
    $(".count").textContent = this._clips
      ? `${this._clips.length} ${T("clips")}${this._updating ? ` · ${T("updating")}` : ""}`
      : "";
    $(".refresh").classList.toggle("busy", !!this._updating);

    const player = $(".player");
    player.classList.toggle("on", !!this._current);
    const overlay = $(".overlay");
    overlay.hidden = !(this._downloading && this._current);
    if (!overlay.hidden) {
      $(".otext").textContent = T("downloading")(this._current.label);
    }

    const list = $(".list");
    if (this._loading) {
      list.innerHTML = `<div class="msg">${T("loading")}</div>`;
      return;
    }
    if (this._error && !this._clips) {
      list.innerHTML = `<div class="msg err"></div>`;
      list.firstChild.textContent = this._error;
      return;
    }
    if (!this._clips) {
      list.innerHTML = "";
      return;
    }
    list.innerHTML = "";
    if (this._error) {
      const e = document.createElement("div");
      e.className = "msg err";
      e.textContent = this._error;
      list.appendChild(e);
    }
    if (!this._clips.length) {
      const m = document.createElement("div");
      m.className = "msg";
      m.textContent = T("empty");
      list.appendChild(m);
      return;
    }
    for (const clip of this._clips) {
      const row = document.createElement("div");
      row.className = "clip" + (this._current && this._current.key === clip.key ? " cur" : "");
      row.innerHTML = `<span class="lead"></span><span class="t"></span><span class="d"></span><span class="c"></span>`;
      const lead = row.querySelector(".lead");
      if (clip.thumb) {
        lead.classList.add("has");
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "";
        img.src = clip.thumb;
        img.addEventListener("error", () => {
          lead.classList.remove("has");
          lead.innerHTML = `<span class="play">▶</span>`;
        });
        lead.appendChild(img);
        const mini = document.createElement("span");
        mini.className = "mini";
        mini.textContent = "▶";
        lead.appendChild(mini);
      } else {
        lead.innerHTML = `<span class="play">▶</span>`;
      }
      row.querySelector(".t").textContent = clip.begin;
      row.querySelector(".d").textContent = `${clip.seconds} s`;
      row.querySelector(".c").textContent = "";
      row.addEventListener("click", () => this._play(clip));
      list.appendChild(row);
    }
  }
}

// One day selector for every recordings card with the same `sync`.
class EzvizRecordingsDatebar extends HTMLElement {
  setConfig(config) {
    if (!config.sync) throw new Error("sync is required");
    this._config = { reset_after: 300, ...config };
    this._build();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 1 };
  }

  static getStubConfig() {
    return { sync: "cameras" };
  }

  connectedCallback() {
    if (!this._config || this._onSync) return;
    this._onSync = (e) => {
      if (e.detail.sync === this._config.sync) this._render();
    };
    window.addEventListener(SYNC_EVENT, this._onSync);
    this._render();
  }

  disconnectedCallback() {
    if (this._onSync) window.removeEventListener(SYNC_EVENT, this._onSync);
    this._onSync = null;
    clearTimeout(this._resetTimer);
  }

  _day() {
    return sharedDay(this._config.sync);
  }

  _go(day) {
    if (!day || day === this._day()) return;
    publishDay(this._config.sync, day);
    this._armReset();
  }

  // Back to today after a while, so the tablet never stays on an old day.
  _armReset() {
    clearTimeout(this._resetTimer);
    const secs = Number(this._config.reset_after) || 0;
    if (!secs) return;
    this._resetTimer = setTimeout(() => {
      const today = isoDay(new Date());
      if (this._day() !== today) publishDay(this._config.sync, today);
    }, secs * 1000);
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { display: flex; align-items: center; gap: 10px; padding: 8px 10px; }
        button { font: inherit; color: var(--primary-text-color); border: none; cursor: pointer;
                 background: var(--secondary-background-color, rgba(127,127,127,.15)); border-radius: 999px; }
        button:disabled { opacity: .3; cursor: default; }
        .arrow { width: 56px; height: 48px; font-size: 1.6em; line-height: 1; flex: none; }
        .mid { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; gap: 10px; position: relative; }
        .label { font-weight: 600; font-size: 1.1em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; }
        .today { padding: 8px 16px; font-weight: 600; flex: none; }
        .today[hidden] { display: none; }
        input[type=date] { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; color-scheme: light dark; }
      </style>
      <ha-card>
        <button class="arrow prev" title="${T("prev")}">‹</button>
        <div class="mid">
          <span class="label"></span>
          <input type="date" class="pick" title="${T("pick")}">
        </div>
        <button class="today">${T("today")}</button>
        <button class="arrow next" title="${T("next")}">›</button>
      </ha-card>`;
    const $ = (s) => this.shadowRoot.querySelector(s);
    $(".prev").addEventListener("click", () => this._go(shiftDay(this._day(), -1)));
    $(".next").addEventListener("click", () => this._go(shiftDay(this._day(), 1)));
    $(".today").addEventListener("click", () => this._go(isoDay(new Date())));
    const pick = $(".pick");
    pick.addEventListener("change", (e) => this._go(e.target.value));
    pick.addEventListener("click", () => {
      try {
        pick.showPicker();
      } catch (e) {
        /* older browsers open it on their own */
      }
    });
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const $ = (s) => this.shadowRoot.querySelector(s);
    const day = this._day();
    const today = isoDay(new Date());
    $(".label").textContent = dayLabel(day);
    $(".next").disabled = day >= today;
    $(".today").hidden = day === today;
    const pick = $(".pick");
    pick.max = today;
    pick.value = day;
  }
}

if (!customElements.get("ezviz-recordings-datebar")) {
  customElements.define("ezviz-recordings-datebar", EzvizRecordingsDatebar);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "ezviz-recordings-datebar",
    name: T("bar_name"),
    description: T("bar_desc"),
  });
}

if (!customElements.get("ezviz-recordings-card")) {
  customElements.define("ezviz-recordings-card", EzvizRecordingsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "ezviz-recordings-card",
    name: T("card_name"),
    description: T("card_desc"),
  });
}
