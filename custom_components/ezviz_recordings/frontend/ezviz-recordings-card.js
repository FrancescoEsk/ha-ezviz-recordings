/* EZVIZ Recordings cards (served by the ezviz_recordings integration).
 *
 * Recordings card: daily list of a camera's clips, with a day selector and a player.
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
 *
 * Class names (.head, .bar, .list, .clip, .lead, .t, .d, .player…) are kept stable for card-mod.
 */

// ---------------------------------------------------------------- texts
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

// ---------------------------------------------------------------- days
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const today = () => isoDay(new Date());
const noon = (day) => new Date(day + "T12:00:00");

const dayLabel = (day) => {
  const d = noon(day);
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const date = d.toLocaleDateString(lang(), { day: "numeric", month: "short", year: "numeric" });
  if (day === today()) return `${T("today")} · ${date}`;
  if (day === isoDay(yesterday)) return `${T("yesterday")} · ${date}`;
  const weekday = d.toLocaleDateString(lang(), { weekday: "long" });
  return `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)} · ${date}`;
};

/** The day `days` away, or null if it would be in the future. */
const shiftDay = (day, days) => {
  const d = noon(day);
  d.setDate(d.getDate() + days);
  const next = isoDay(d);
  return next > today() ? null : next;
};

// ---------------------------------------------------------------- shared day (sync groups)
const SYNC_EVENT = "ezviz-recordings-day";
const syncDays = (window.__ezvizRecordingsDays = window.__ezvizRecordingsDays || {});
const sharedDay = (group) => (group && syncDays[group]) || today();
const publishDay = (group, day) => {
  syncDays[group] = day;
  window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { sync: group, day } }));
};
/** Call `onDay(day)` when the day of `group` changes; returns the unsubscribe function. */
const followDay = (group, onDay) => {
  const listener = (e) => {
    if (e.detail.sync === group) onDay(e.detail.day);
  };
  window.addEventListener(SYNC_EVENT, listener);
  return () => window.removeEventListener(SYNC_EVENT, listener);
};

// ---------------------------------------------------------------- shared look and helpers
const BASE_CSS = `
  button { font: inherit; color: var(--primary-text-color); border: none; cursor: pointer;
           background: var(--secondary-background-color, rgba(127,127,127,.15)); }
  button:disabled { opacity: .35; cursor: default; }
  input[type=date] { font: inherit; color: var(--primary-text-color); color-scheme: light dark; }
  [hidden] { display: none !important; }
  @keyframes spin { to { transform: rotate(360deg); } }
`;

/** Base of both cards: shadow DOM, a query helper, the day picker. */
class EzvizBase extends HTMLElement {
  _mount(css, html) {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${css}</style>${html}`;
  }

  $(selector) {
    return this.shadowRoot.querySelector(selector);
  }

  _syncPicker(day) {
    const pick = this.$(".pick");
    pick.max = today();
    pick.value = day;
  }
}

// ================================================================ recordings card
const CARD_CSS = `
  ha-card { overflow: hidden; }
  .head { display: flex; align-items: center; gap: 8px; padding: 12px 16px 4px; }
  .title { font-size: 1.1em; font-weight: 500; flex: 1; min-width: 0;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .count { color: var(--secondary-text-color); font-size: .85em; }
  .bar { display: flex; align-items: center; gap: 6px; padding: 4px 12px 8px; flex-wrap: wrap; }
  .daylabel { flex: 1; min-width: 120px; text-align: center; font-weight: 500; }
  button { border-radius: 8px; padding: 6px 10px; }
  button.icon { width: 36px; padding: 6px 0; font-size: 1.1em; }
  .refresh.busy { animation: spin 1s linear infinite; }
  input[type=date] { background: var(--secondary-background-color, rgba(127,127,127,.15));
           border: none; border-radius: 8px; padding: 5px 8px; }
  .player { position: relative; background: #000; aspect-ratio: 16 / 9; display: none; }
  .player.on { display: block; }
  video { width: 100%; height: 100%; display: block; }
  .overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
             justify-content: center; color: #fff; gap: 10px; background: rgba(0,0,0,.55); font-size: .95em; }
  .spin { width: 28px; height: 28px; border: 3px solid rgba(255,255,255,.3); border-top-color: #fff;
          border-radius: 50%; animation: spin 1s linear infinite; }
  .list { max-height: var(--ezr-max-height); overflow-y: auto; padding: 4px 8px 10px; }
  .clip { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
  .clip:hover { background: var(--secondary-background-color, rgba(127,127,127,.12)); }
  .clip.cur { background: rgba(var(--rgb-primary-color, 3,169,244), .18); }
  .clip .t { font-variant-numeric: tabular-nums; font-weight: 500; }
  .clip .d { color: var(--secondary-text-color); font-size: .9em; flex: 1; }
  .clip .c { color: var(--secondary-text-color); font-size: .8em; }
  .lead { width: 64px; height: 36px; flex: none; display: flex; align-items: center; justify-content: center;
          position: relative; border-radius: 6px; overflow: hidden; }
  .lead.has { background: #000; }
  .lead img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .play { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
          background: var(--primary-color); color: var(--text-primary-color, #fff); font-size: 11px; flex: none; }
  .lead .mini { position: absolute; right: 3px; bottom: 3px; width: 14px; height: 14px; border-radius: 50%;
                background: rgba(0,0,0,.6); color: #fff; font-size: 7px; display: flex; align-items: center;
                justify-content: center; }
  .msg { padding: 14px 16px 18px; color: var(--secondary-text-color); text-align: center; }
  .err { color: var(--error-color, #db4437); }
`;

const FOLLOW_SYNC_MS = 15000; // while HA downloads today's clips, refresh the list this often

class EzvizRecordingsCard extends EzvizBase {
  setConfig(config) {
    if (!config.serial) throw new Error("serial is required");
    this._config = { max_height: "320px", autoplay_next: true, ...config };
    this._day = sharedDay(this._config.sync);
    this._name = config.title || null;
    this._clips = null; // clips of the shown day, newest first
    this._current = null; // { key, label } of the clip in the player
    this._downloading = false;
    this._loading = false;
    this._updating = false;
    this._error = null;
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

  connectedCallback() {
    if (!this._config || !this._config.sync || this._unfollowDay) return;
    this._unfollowDay = followDay(this._config.sync, (day) => this._setDay(day));
    // The day may have changed while this card was not on the page.
    if (this._hass) this._setDay(sharedDay(this._config.sync));
  }

  disconnectedCallback() {
    this._stopFollowSync();
    if (this._unfollowDay) this._unfollowDay();
    this._unfollowDay = null;
  }

  // ---------------------------------------------------------------- data
  _clipsOf(day, flags = {}) {
    return this._hass.callWS({ type: "ezviz_recordings/clips", serial: this._config.serial, day, ...flags });
  }

  async _loadName() {
    if (!this._name) {
      try {
        const res = await this._hass.callWS({ type: "ezviz_recordings/cameras" });
        const cam = res.cameras.find((c) => c.serial === this._config.serial);
        this._name = cam ? cam.name : this._config.serial;
      } catch (e) {
        this._name = this._config.serial;
      }
    }
    this._render();
  }

  /** Load the shown day; `refresh` (⟳ button) re-reads it while keeping the list on screen. */
  async _load(refresh = false) {
    const day = this._day;
    this._loading = !refresh || !this._clips;
    this._updating = !this._loading;
    this._error = null;
    if (this._loading) this._clips = null;
    this._render();
    try {
      const res = await this._clipsOf(day, { refresh });
      if (day !== this._day) return;
      this._clips = res.clips;
      if (res.stale) this._update(day);
      if (day === today()) this._syncToday(res.running);
    } catch (e) {
      if (day !== this._day) return;
      this._error = (e && e.message) || T("load_error");
    }
    this._loading = false;
    if (refresh) this._updating = false;
    this._render();
  }

  /** The cached list was shown at once; fetch only the new clips in the background. */
  async _update(day) {
    this._updating = true;
    this._render();
    try {
      const res = await this._clipsOf(day, { update: true });
      if (day === this._day) this._clips = res.clips;
    } catch (e) {
      /* keep the cached list; the refresh button retries */
    }
    this._updating = false;
    this._render();
  }

  /** Ask HA to download today's new clips (it allows that at most every 10 min,
   *  once for all cards), then follow the thumbnails as they arrive. */
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
    const thumbs = (clips) => (clips || []).filter((c) => c.thumb).length;
    this._syncTimer = setInterval(async () => {
      const day = this._day;
      if (day !== today() || !this.isConnected) return this._stopFollowSync();
      try {
        const res = await this._clipsOf(day);
        if (day !== this._day) return;
        if (!this._clips || res.clips.length !== this._clips.length || thumbs(res.clips) !== thumbs(this._clips)) {
          this._clips = res.clips;
          this._render();
        }
        if (!res.running) this._stopFollowSync();
      } catch (e) {
        this._stopFollowSync();
      }
    }, FOLLOW_SYNC_MS);
  }

  _stopFollowSync() {
    clearInterval(this._syncTimer);
    this._syncTimer = null;
  }

  async _play(clip) {
    this._current = { key: clip.key, label: clip.begin };
    this._downloading = true;
    this._error = null;
    this._render();
    const isCurrent = () => this._current && this._current.key === clip.key;
    try {
      const res = await this._hass.callWS({
        type: "ezviz_recordings/clip_url", serial: this._config.serial, day: this._day, key: clip.key,
      });
      if (!isCurrent()) return;
      Object.assign(clip, { cached: true, thumb: res.thumb });
      this._downloading = false;
      this._render();
      const video = this.$("video");
      video.src = res.url;
      video.play().catch(() => {});
    } catch (e) {
      if (!isCurrent()) return;
      this._downloading = false;
      this._error = (e && e.message) || T("download_failed");
      this._render();
    }
  }

  /** After a clip ends, play the next (older) one. */
  _ended() {
    if (!this._config.autoplay_next || !this._clips || !this._current) return;
    const i = this._clips.findIndex((c) => c.key === this._current.key);
    if (i >= 0 && i + 1 < this._clips.length) this._play(this._clips[i + 1]);
  }

  _setDay(day) {
    if (!day || day === this._day) return;
    this._day = day;
    this._current = null;
    this._downloading = false;
    const video = this.$("video");
    video.pause();
    video.removeAttribute("src");
    video.load();
    this._load();
  }

  // ---------------------------------------------------------------- view
  _build() {
    this._mount(CARD_CSS, `
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
      </ha-card>`);
    this.$(".prev").addEventListener("click", () => this._setDay(shiftDay(this._day, -1)));
    this.$(".next").addEventListener("click", () => this._setDay(shiftDay(this._day, 1)));
    this.$(".today").addEventListener("click", () => this._setDay(today()));
    this.$(".refresh").addEventListener("click", () => this._load(true));
    this.$(".pick").addEventListener("change", (e) => this._setDay(e.target.value));
    this.$("video").addEventListener("ended", () => this._ended());
    this.$(".bar").hidden = !!this._config.hide_bar;
    this.style.setProperty("--ezr-max-height", this._config.max_height);
  }

  _render() {
    if (!this.shadowRoot) return;
    this._renderHead();
    this._renderPlayer();
    this._renderList();
  }

  _renderHead() {
    const isToday = this._day === today();
    this.$(".title").textContent = this._name || "";
    this.$(".daylabel").textContent = dayLabel(this._day);
    this.$(".next").disabled = isToday;
    this.$(".today").disabled = isToday;
    this._syncPicker(this._day);
    this.$(".count").textContent = this._clips
      ? `${this._clips.length} ${T("clips")}${this._updating ? ` · ${T("updating")}` : ""}`
      : "";
    this.$(".refresh").classList.toggle("busy", this._updating);
  }

  _renderPlayer() {
    this.$(".player").classList.toggle("on", !!this._current);
    const overlay = this.$(".overlay");
    overlay.hidden = !(this._downloading && this._current);
    if (!overlay.hidden) this.$(".otext").textContent = T("downloading")(this._current.label);
  }

  _renderList() {
    const list = this.$(".list");
    list.replaceChildren();
    const message = (text, error = false) => {
      const div = document.createElement("div");
      div.className = error ? "msg err" : "msg";
      div.textContent = text;
      list.appendChild(div);
    };
    if (this._loading) return message(T("loading"));
    if (this._error) message(this._error, true);
    if (!this._clips) return;
    if (!this._clips.length) return message(T("empty"));
    for (const clip of this._clips) list.appendChild(this._row(clip));
  }

  _row(clip) {
    const row = document.createElement("div");
    row.className = "clip" + (this._current && this._current.key === clip.key ? " cur" : "");
    row.innerHTML = `<span class="lead"></span><span class="t"></span><span class="d"></span><span class="c"></span>`;
    row.querySelector(".t").textContent = clip.begin;
    row.querySelector(".d").textContent = `${clip.seconds} s`;
    const lead = row.querySelector(".lead");
    const playDot = () => {
      lead.classList.remove("has");
      lead.innerHTML = `<span class="play">▶</span>`;
    };
    if (clip.thumb) {
      lead.classList.add("has");
      const img = Object.assign(document.createElement("img"), { loading: "lazy", alt: "", src: clip.thumb });
      img.addEventListener("error", playDot);
      lead.append(img, Object.assign(document.createElement("span"), { className: "mini", textContent: "▶" }));
    } else {
      playDot();
    }
    row.addEventListener("click", () => this._play(clip));
    return row;
  }
}

// ================================================================ shared day bar
const BAR_CSS = `
  ha-card { display: flex; align-items: center; gap: 10px; padding: 8px 10px; }
  button { border-radius: 999px; }
  button:disabled { opacity: .3; }
  .arrow { width: 56px; height: 48px; font-size: 1.6em; line-height: 1; flex: none; }
  .mid { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; gap: 10px; position: relative; }
  .label { font-weight: 600; font-size: 1.1em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; }
  .today { padding: 8px 16px; font-weight: 600; flex: none; }
  input[type=date] { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; }
`;

class EzvizRecordingsDatebar extends EzvizBase {
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
    if (!this._config || this._unfollowDay) return;
    this._unfollowDay = followDay(this._config.sync, () => this._render());
    this._render();
  }

  disconnectedCallback() {
    if (this._unfollowDay) this._unfollowDay();
    this._unfollowDay = null;
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

  /** Back to today after a while, so a wall tablet never stays on an old day. */
  _armReset() {
    clearTimeout(this._resetTimer);
    const seconds = Number(this._config.reset_after) || 0;
    if (!seconds) return;
    this._resetTimer = setTimeout(() => {
      if (this._day() !== today()) publishDay(this._config.sync, today());
    }, seconds * 1000);
  }

  _build() {
    this._mount(BAR_CSS, `
      <ha-card>
        <button class="arrow prev" title="${T("prev")}">‹</button>
        <div class="mid">
          <span class="label"></span>
          <input type="date" class="pick" title="${T("pick")}">
        </div>
        <button class="today">${T("today")}</button>
        <button class="arrow next" title="${T("next")}">›</button>
      </ha-card>`);
    this.$(".prev").addEventListener("click", () => this._go(shiftDay(this._day(), -1)));
    this.$(".next").addEventListener("click", () => this._go(shiftDay(this._day(), 1)));
    this.$(".today").addEventListener("click", () => this._go(today()));
    const pick = this.$(".pick");
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
    const day = this._day();
    this.$(".label").textContent = dayLabel(day);
    this.$(".next").disabled = day >= today();
    this.$(".today").hidden = day === today();
    this._syncPicker(day);
  }
}

// ---------------------------------------------------------------- registration
const register = (type, cls, name, description) => {
  if (customElements.get(type)) return;
  customElements.define(type, cls);
  window.customCards = window.customCards || [];
  window.customCards.push({ type, name, description });
};
register("ezviz-recordings-card", EzvizRecordingsCard, T("card_name"), T("card_desc"));
register("ezviz-recordings-datebar", EzvizRecordingsDatebar, T("bar_name"), T("bar_desc"));
