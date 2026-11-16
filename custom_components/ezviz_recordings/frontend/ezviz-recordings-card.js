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
 *   hide_refresh: true         # optional, hide this card's own ⟳ (use the day bar's one)
 *   short_time: true           # optional, show the clip time as HH:MM instead of HH:MM:SS
 *   layout: timeline           # optional, clips as piles on a time axis; a pile opens its clips in a grid
 *                              # (back with ‹ or a swipe to the right). Default: list
 *
 * Shared day bar (one selector for several recordings cards):
 *
 *   type: custom:ezviz-recordings-datebar
 *   sync: living_room          # required, same value as the cards it drives
 *   reset_after: 300           # optional, seconds without touches before going back to today (0 = never)
 *   show_refresh: true         # optional, ⟳ that refreshes every card of the group, with the number
 *                              # of clips still downloading next to it
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
    back: "Back", refresh_all: "Refresh every camera", left: (n) => `${n} clip${n === 1 ? "" : "s"} still downloading`,
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
    back: "Indietro", refresh_all: "Aggiorna tutte le telecamere", left: (n) => `${n} clip ancora da scaricare`,
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
/** Call `fn(detail)` for every `event` of `group`; returns the unsubscribe function. */
const listen = (event, group, fn) => {
  const listener = (e) => {
    if (e.detail.sync === group) fn(e.detail);
  };
  window.addEventListener(event, listener);
  return () => window.removeEventListener(event, listener);
};
/** Call `onDay(day)` when the day of `group` changes; returns the unsubscribe function. */
const followDay = (group, onDay) => listen(SYNC_EVENT, group, (d) => onDay(d.day));

// The day bar's ⟳ asks every card of the group to re-read its list; each card reports back
// whether it is busy and how many clips HA still has to download, shown next to the ⟳.
const REFRESH_EVENT = "ezviz-recordings-refresh";
const STATUS_EVENT = "ezviz-recordings-status";
const syncStatus = (window.__ezvizRecordingsStatus = window.__ezvizRecordingsStatus || {});
const reportStatus = (group, serial, status) => {
  const prev = (syncStatus[group] = syncStatus[group] || {})[serial];
  if (prev && prev.busy === status.busy && prev.loading === status.loading && prev.missing === status.missing) return;
  syncStatus[group][serial] = status;
  window.dispatchEvent(new CustomEvent(STATUS_EVENT, { detail: { sync: group } }));
};

// ---------------------------------------------------------------- shared look and helpers
// Material Design Icons (the set Home Assistant uses), drawn inline so they look the same everywhere.
const MDI = {
  left: "M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z",
  right: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
  refresh: "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z",
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  down: "M11,4H13V16L18.5,10.5L19.92,11.92L12,19.84L4.08,11.92L5.5,10.5L11,16V4Z",
};
const icon = (name) =>
  `<svg class="mdi" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="${MDI[name]}"/></svg>`;

const BASE_CSS = `
  button { font: inherit; color: var(--primary-text-color); border: none; cursor: pointer;
           background: var(--secondary-background-color, rgba(127,127,127,.15));
           display: inline-flex; align-items: center; justify-content: center; }
  svg.mdi { width: 22px; height: 22px; flex: none; display: block; }
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
  button.icon { width: 36px; height: 36px; padding: 0; border-radius: 999px; }
  .refresh.busy svg { animation: spin 1s linear infinite; }
  input[type=date] { background: var(--secondary-background-color, rgba(127,127,127,.15));
           border: none; border-radius: 8px; padding: 5px 8px; }
  .player { position: relative; background: #000; aspect-ratio: 16 / 9; display: none; }
  .player.on { display: block; }
  video { width: 100%; height: 100%; display: block; }
  .overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
             justify-content: center; color: #fff; gap: 10px; background: rgba(0,0,0,.55); font-size: .95em; }
  .spin { width: 28px; height: 28px; border: 3px solid rgba(255,255,255,.3); border-top-color: #fff;
          border-radius: 50%; animation: spin 1s linear infinite; }
  .list { max-height: var(--ezr-max-height); overflow-y: auto; padding: 4px 8px 10px; touch-action: pan-y; }
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
          background: var(--primary-color); color: var(--text-primary-color, #fff); flex: none; }
  .play svg.mdi { width: 16px; height: 16px; margin-left: 2px; }
  .lead .mini { position: absolute; right: 3px; bottom: 3px; width: 16px; height: 16px; border-radius: 50%;
                background: rgba(0,0,0,.6); color: #fff; display: flex; align-items: center;
                justify-content: center; }
  .lead .mini svg.mdi { width: 11px; height: 11px; margin-left: 1px; }
  .msg { padding: 14px 16px 18px; color: var(--secondary-text-color); text-align: center; }
  .err { color: var(--error-color, #db4437); }
  ha-card { position: relative; }

  /* timeline layout */
  .list.tl { padding: 14px 12px 16px 4px; }
  .tlin { position: relative; }
  .tlin::before { content: ""; position: absolute; top: -14px; bottom: -16px; left: 60px; width: 8px;
                  background: rgba(127,127,127,.22); border-radius: 4px; }
  .tg { display: grid; grid-template-columns: 50px 8px minmax(0, 1fr); column-gap: 14px; align-items: start;
        position: relative; isolation: isolate; }
  .tt { text-align: right; font-size: .92em; color: var(--secondary-text-color); font-variant-numeric: tabular-nums;
        padding-top: 12px; position: relative; }
  .tt::after { content: ""; position: absolute; right: -10px; top: 21px; width: 8px; height: 1px;
               background: var(--secondary-text-color); opacity: .6; }
  .ax { position: relative; align-self: stretch; margin-top: 12px; }
  .ax i { position: absolute; left: 0; right: 0; height: 2px; margin-top: -1px;
          background: var(--primary-color); opacity: .85; }
  .pile { position: relative; margin-top: 12px; aspect-ratio: 16 / 9; max-width: 260px; border-radius: 14px;
          cursor: pointer; background: linear-gradient(135deg, rgba(127,127,127,.35), rgba(127,127,127,.15)); }
  .pile.stack::before, .pile.stack::after { content: ""; position: absolute; border-radius: 12px 12px 0 0;
          background: rgba(127,127,127,.28); z-index: -1; }
  .pile.stack::before { left: 5%; right: 5%; top: -6px; height: 20px; }
  .pile.stack::after { left: 11%; right: 11%; top: -12px; height: 20px; background: rgba(127,127,127,.18); }
  .pile .ph { position: absolute; inset: 0; border-radius: inherit; overflow: hidden; }
  .pile .ph img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .pile .ph::after { content: ""; position: absolute; inset: 0;
          background: linear-gradient(180deg, rgba(0,0,0,.45), rgba(0,0,0,0) 45%, rgba(0,0,0,0) 60%, rgba(0,0,0,.4)); }
  .pile .pn { position: absolute; left: 12px; top: 9px; color: #fff; font-weight: 600; font-size: .95em;
              text-shadow: 0 1px 3px rgba(0,0,0,.6); }
  .pile .pp { position: absolute; right: 10px; bottom: 9px; width: 34px; height: 34px; border-radius: 50%;
              display: flex; align-items: center; justify-content: center; color: #fff;
              background: rgba(0,0,0,.5); backdrop-filter: blur(4px); }
  .pile .pp svg.mdi { width: 20px; height: 20px; margin-left: 2px; }
  .pile.cur { outline: 3px solid var(--primary-color); outline-offset: 2px; }
  .tl .msg { padding-left: 16px; }

  /* sheet: the clips of one pile, over the card */
  .sheet { position: absolute; inset: 0; z-index: 3; display: flex; flex-direction: column; overflow: hidden;
           touch-action: pan-y; border-radius: inherit; transition: transform .2s ease;
           background: var(--ha-card-background, var(--card-background-color, #1c1c1c)); }
  .sh-head { display: flex; align-items: center; gap: 8px; padding: 12px 12px 8px; flex: none; }
  .sh-title { font-weight: 700; font-size: 1.05em; }
  button.back { width: 40px; height: 40px; }
  button.back svg.mdi { width: 26px; height: 26px; }
  .sh-player .player { margin: 0 12px 10px; border-radius: 14px; overflow: hidden; }
  .sh-grid { flex: 1; overflow-y: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
             padding: 2px 12px 14px; align-content: start; }
  .sh-grid .clip { display: flex; flex-direction: column; align-items: stretch; gap: 0; padding: 0 0 8px;
                   border-radius: 14px; overflow: hidden; background: rgba(127,127,127,.14); }
  .sh-grid .clip.cur { background: var(--primary-text-color); color: var(--primary-background-color); }
  .sh-grid .lead { width: 100%; height: auto; aspect-ratio: 16 / 9; border-radius: 0; margin-bottom: 7px; }
  .sh-grid .lead .mini, .sh-grid .d, .sh-grid .c { display: none; }
  .sh-grid .t { padding: 0 10px; font-weight: 700; font-size: .95em; }
`;

const GROUP_GAP_S = 300; // timeline: clips less than this apart go in the same pile
const TL_MAX_GAP_PX = 90; // timeline: most empty space drawn between two piles
const SHEET_CLOSE_PX = 70; // swipe this far to the right to close a pile's sheet
const secondsOf = (hms) => {
  const [h, m, s] = hms.split(":").map(Number);
  return h * 3600 + m * 60 + (s || 0);
};
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
    this._running = false; // HA is downloading clips of the shown day
    this._missing = 0; // finished clips of the shown day not saved yet
    this._error = null;
    this._build();
    if (this.isConnected) this.connectedCallback();
  }

  /** Keep the download state of the last answer of the clips command. */
  _took(res) {
    this._clips = res.clips;
    this._running = !!res.running;
    this._missing = res.missing || 0;
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
    const group = this._config.sync;
    const offDay = followDay(group, (day) => this._setDay(day));
    const offRefresh = listen(REFRESH_EVENT, group, () => {
      // a copy hidden on this screen size (e.g. desktop layout on a phone) must not wake the camera again
      const shown = this.getBoundingClientRect().width > 0;
      if (shown && this._hass && !this._loading && !this._updating) this._load(true);
    });
    this._unfollowDay = () => {
      offDay();
      offRefresh();
    };
    // The day may have changed while this card was not on the page.
    if (this._hass) this._setDay(sharedDay(group));
    this._report();
  }

  disconnectedCallback() {
    this._stopFollowSync();
    if (this._unfollowDay) this._unfollowDay();
    this._unfollowDay = null;
    // a card that left the page (other swipe page, other view) no longer counts
    if (this._config && this._config.sync && syncStatus[this._config.sync]) {
      delete syncStatus[this._config.sync][this._statusKey()];
      window.dispatchEvent(new CustomEvent(STATUS_EVENT, { detail: { sync: this._config.sync } }));
    }
  }

  _statusKey() {
    if (!this._uid) this._uid = `${this._config.serial}#${Math.random().toString(36).slice(2, 8)}`;
    return this._uid;
  }

  /** Tell the day bar of the group whether this card is busy and what is left to download. */
  _report() {
    if (!this._config || !this._config.sync || !this.isConnected) return;
    reportStatus(this._config.sync, this._statusKey(), {
      serial: this._config.serial,
      loading: this._loading || this._updating,
      busy: this._loading || this._updating || this._running,
      missing: this._running ? this._missing : 0,
    });
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
      this._took(res);
      if (res.stale) this._update(day);
      if (day === today()) this._syncToday(res.running);
      else if (res.running) this._followSync();
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
      if (day === this._day) {
        this._took(res);
        if (res.running) this._followSync(); // the camera woke up: its missing clips are downloading
      }
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
    if (running) {
      this._running = true;
      this._render();
      this._followSync();
    }
  }

  _followSync() {
    if (this._syncTimer && this._followDay === this._day) return;
    this._stopFollowSync();
    const thumbs = (clips) => (clips || []).filter((c) => c.thumb).length;
    const followed = (this._followDay = this._day);
    this._syncTimer = setInterval(async () => {
      const day = this._day;
      if (day !== followed || !this.isConnected) return this._stopFollowSync();
      try {
        const res = await this._clipsOf(day);
        if (day !== this._day) return;
        const changed = !this._clips || res.clips.length !== this._clips.length
          || thumbs(res.clips) !== thumbs(this._clips) || res.missing !== this._missing || !!res.running !== this._running;
        this._took(res);
        if (changed) this._render();
        if (!res.running) this._stopFollowSync();
      } catch (e) {
        this._stopFollowSync();
      }
    }, FOLLOW_SYNC_MS);
  }

  _stopFollowSync() {
    clearInterval(this._syncTimer);
    this._syncTimer = null;
    if (this._running) {
      this._running = false;
      this._render();
    }
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
    this._sheet = null;
    this._stopFollowSync();
    this._running = false;
    this._missing = 0;
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
          <button class="icon refresh" title="${T("refresh")}">${icon("refresh")}</button></div>
        <div class="bar">
          <button class="icon prev" title="${T("prev")}">${icon("left")}</button>
          <div class="daylabel"></div>
          <button class="icon next" title="${T("next")}">${icon("right")}</button>
          <input type="date" class="pick" title="${T("pick")}">
          <button class="today">${T("today")}</button>
        </div>
        <div class="player"><video controls playsinline preload="none"></video>
          <div class="overlay" hidden><div class="spin"></div><div class="otext"></div></div></div>
        <div class="list"></div>
        <div class="sheet" hidden>
          <div class="sh-head"><button class="icon back" title="${T("back")}">${icon("left")}</button><div class="sh-title"></div></div>
          <div class="sh-player"></div>
          <div class="sh-grid"></div>
        </div>
      </ha-card>`);
    this.$(".back").addEventListener("click", () => this._closeSheet());
    this._sheetGestures(this.$(".sheet"));
    this._listSwipe(this.$(".list"));
    this.$(".prev").addEventListener("click", () => this._setDay(shiftDay(this._day, -1)));
    this.$(".next").addEventListener("click", () => this._setDay(shiftDay(this._day, 1)));
    this.$(".today").addEventListener("click", () => this._setDay(today()));
    this.$(".refresh").addEventListener("click", () => this._load(true));
    this.$(".pick").addEventListener("change", (e) => this._setDay(e.target.value));
    this.$("video").addEventListener("ended", () => this._ended());
    this.$(".bar").hidden = !!this._config.hide_bar;
    this.$(".refresh").hidden = !!this._config.hide_refresh;
    this.style.setProperty("--ezr-max-height", this._config.max_height);
  }

  _render() {
    if (!this.shadowRoot) return;
    this._renderHead();
    this._renderPlayer();
    this._renderList();
    this._report();
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
    list.classList.toggle("tl", this._config.layout === "timeline");
    this._renderSheet();
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
    if (this._config.layout === "timeline") return this._renderTimeline(list);
    for (const clip of this._clips) list.appendChild(this._row(clip));
  }

  // ---------------------------------------------------------------- timeline layout
  /** Clips close in time (newest first) grouped into piles. */
  _groups() {
    const groups = [];
    let group = null;
    let lastStart = null;
    for (const clip of this._clips) {
      const start = secondsOf(clip.begin);
      if (group && lastStart - (start + clip.seconds) <= GROUP_GAP_S) group.clips.push(clip);
      else groups.push((group = { clips: [clip] }));
      lastStart = start;
    }
    return groups;
  }

  _renderTimeline(list) {
    const inner = document.createElement("div");
    inner.className = "tlin";
    let prevOldest = null;
    for (const g of this._groups()) {
      const newest = g.clips[0];
      const oldest = g.clips[g.clips.length - 1];
      const top = secondsOf(newest.begin) + newest.seconds;
      const bottom = secondsOf(oldest.begin);
      const span = Math.max(1, top - bottom);
      const row = document.createElement("div");
      row.className = "tg";
      if (prevOldest !== null) {
        // more empty time between two piles = more space, within limits
        const gapMin = Math.max(0, (prevOldest - top) / 60);
        row.style.marginTop = `${Math.round(Math.min(TL_MAX_GAP_PX, 14 + gapMin * 0.5))}px`;
      }
      prevOldest = bottom;
      row.innerHTML = `<span class="tt"></span><span class="ax"></span>
        <div class="pile"><div class="ph"></div><span class="pn"></span><span class="pp">${icon("play")}</span></div>`;
      row.querySelector(".tt").textContent = newest.begin.slice(0, 5);
      const ax = row.querySelector(".ax");
      for (const c of g.clips) {
        const tick = document.createElement("i");
        tick.style.top = `${((top - secondsOf(c.begin) - c.seconds / 2) / span) * 100}%`;
        ax.appendChild(tick);
      }
      const pile = row.querySelector(".pile");
      const many = g.clips.length > 1;
      pile.classList.toggle("stack", many);
      pile.classList.toggle("cur", !!this._current && g.clips.some((c) => c.key === this._current.key));
      row.querySelector(".pn").textContent = many ? `${g.clips.length} ${T("clips")}` : "";
      const withThumb = g.clips.find((c) => c.thumb);
      if (withThumb) {
        const img = Object.assign(document.createElement("img"), { loading: "lazy", alt: "", src: withThumb.thumb });
        img.addEventListener("error", () => img.remove());
        row.querySelector(".ph").appendChild(img);
      }
      pile.addEventListener("click", () => (many ? this._openSheet(newest.key) : this._play(newest)));
      row.querySelector(".pp").addEventListener("click", (e) => {
        e.stopPropagation();
        this._play(newest);
      });
      inner.appendChild(row);
    }
    list.appendChild(inner);
  }

  // ---------------------------------------------------------------- pile sheet (grid of one pile)
  _openSheet(key) {
    this._sheet = key;
    this._render();
    this.$(".sh-grid").scrollTop = 0;
  }

  _closeSheet() {
    if (!this._sheet) return;
    this._sheet = null;
    this._render();
  }

  _renderSheet() {
    const sheet = this.$(".sheet");
    const group = this._sheet && this._clips ? this._groups().find((g) => g.clips.some((c) => c.key === this._sheet)) : null;
    if (!group) this._sheet = null;
    sheet.hidden = !group;
    sheet.style.transform = "";
    this._placePlayer();
    if (!group) return;
    const n = group.clips.length;
    const first = group.clips[n - 1].begin.slice(0, 5);
    const last = group.clips[0].begin.slice(0, 5);
    this.$(".sh-title").textContent = `${first === last ? last : `${first} – ${last}`} · ${n} ${T("clips")}`;
    const grid = this.$(".sh-grid");
    const scroll = grid.scrollTop;
    grid.replaceChildren(...group.clips.map((c) => this._row(c, true)));
    grid.scrollTop = scroll;
  }

  /** The player sits at the top of the card, or at the top of the sheet while it is open. */
  _placePlayer() {
    const player = this.$(".player");
    const host = this._sheet ? this.$(".sh-player") : this.$("ha-card");
    if (player.parentNode === host) return;
    const video = this.$("video");
    const wasPlaying = !video.paused && !video.ended;
    if (this._sheet) host.appendChild(player);
    else host.insertBefore(player, this.$(".list"));
    if (wasPlaying) video.play().catch(() => {}); // moving a video in the page pauses it
  }

  /** Swipe the sheet to the right to go back to the timeline (the swipe card behind must not move). */
  _sheetGestures(sheet) {
    let x0 = null;
    let y0 = 0;
    let dx = 0;
    let horizontal = null;
    const stop = (e) => e.stopPropagation();
    for (const type of ["touchstart", "touchmove", "touchend", "mousedown", "mousemove", "mouseup"]) {
      sheet.addEventListener(type, stop);
    }
    sheet.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      if (e.target.closest("video, button")) return;
      x0 = e.clientX;
      y0 = e.clientY;
      dx = 0;
      horizontal = null;
    });
    sheet.addEventListener("pointermove", (e) => {
      e.stopPropagation();
      if (x0 === null) return;
      dx = e.clientX - x0;
      const dy = e.clientY - y0;
      if (horizontal === null && Math.abs(dx) + Math.abs(dy) > 10) horizontal = Math.abs(dx) > Math.abs(dy);
      if (horizontal) {
        sheet.style.transition = "none";
        sheet.style.transform = `translateX(${Math.max(0, dx)}px)`;
      }
    });
    const end = (e) => {
      e.stopPropagation();
      if (x0 === null) return;
      x0 = null;
      sheet.style.transition = "";
      if (horizontal && dx > SHEET_CLOSE_PX) this._closeSheet();
      else sheet.style.transform = "";
    };
    sheet.addEventListener("pointerup", end);
    sheet.addEventListener("pointercancel", end);
  }

  /** The swipe card around this card (simple-swipe-card), if any. */
  _swiper() {
    let node = this;
    for (let i = 0; node && i < 40; i++) {
      if (node.localName === "simple-swipe-card") return node;
      node = node.assignedSlot || node.parentNode || node.host;
    }
    return null;
  }

  /** simple-swipe-card ignores swipes that start inside a scrolling list (ours): pass
   *  horizontal swipes on the list to it, so the next/previous camera still opens. */
  _listSwipe(list) {
    let x0 = null;
    let y0 = 0;
    let t0 = 0;
    list.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      x0 = e.clientX;
      y0 = e.clientY;
      t0 = Date.now();
      this._lastMove = null;
    });
    const end = (e) => {
      if (x0 === null) return;
      const dx = e.clientX - x0;
      const dy = e.clientY - y0;
      x0 = null;
      if (e.type === "pointercancel" && !dx) return;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5 || Date.now() - t0 > 1000) return;
      const swiper = this._swiper();
      if (!swiper || typeof swiper.goToSlide !== "function") return;
      const count = (swiper.visibleCardIndices || []).length || Infinity;
      const next = (swiper.currentIndex || 0) + (dx < 0 ? 1 : -1);
      if (next < 0 || next >= count) return;
      this._swallowClick = true; // the swipe must not also open a pile or play a clip
      setTimeout(() => (this._swallowClick = false), 400);
      swiper.goToSlide(next);
    };
    list.addEventListener("pointerup", end);
    // a vertical-scroll gesture turns into pointercancel on touch screens: keep the last point
    list.addEventListener("pointermove", (e) => {
      if (x0 !== null) this._lastMove = e;
    });
    list.addEventListener("pointercancel", () => {
      if (this._lastMove) end(this._lastMove);
      x0 = null;
    });
    list.addEventListener("click", (e) => {
      if (this._swallowClick) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);
  }

  _row(clip, short = false) {
    const row = document.createElement("div");
    row.className = "clip" + (this._current && this._current.key === clip.key ? " cur" : "");
    row.innerHTML = `<span class="lead"></span><span class="t"></span><span class="d"></span><span class="c"></span>`;
    row.querySelector(".t").textContent = short || this._config.short_time ? clip.begin.slice(0, 5) : clip.begin;
    row.querySelector(".d").textContent = `${clip.seconds} s`;
    const lead = row.querySelector(".lead");
    const playDot = () => {
      lead.classList.remove("has");
      lead.innerHTML = `<span class="play">${icon("play")}</span>`;
    };
    if (clip.thumb) {
      lead.classList.add("has");
      const img = Object.assign(document.createElement("img"), { loading: "lazy", alt: "", src: clip.thumb });
      img.addEventListener("error", playDot);
      const mini = Object.assign(document.createElement("span"), { className: "mini" });
      mini.innerHTML = icon("play");
      lead.append(img, mini);
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
  .arrow { width: 52px; height: 48px; flex: none; }
  .arrow svg.mdi { width: 28px; height: 28px; }
  .mid { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; gap: 10px; position: relative; }
  .label { font-weight: 600; font-size: 1.1em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; }
  .today { padding: 8px 16px; font-weight: 600; flex: none; }
  input[type=date] { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; }
  .refresh { height: 48px; min-width: 48px; padding: 0 12px; flex: none; gap: 6px; }
  .refresh .ic { display: inline-flex; }
  .refresh .ic svg.mdi { width: 24px; height: 24px; }
  .refresh.busy .ic { animation: spin 1s linear infinite; }
  .refresh .n { display: inline-flex; align-items: center; gap: 1px; font-size: .9em; font-weight: 700;
                font-variant-numeric: tabular-nums; }
  .refresh .n svg.mdi { width: 15px; height: 15px; }
`;

class EzvizRecordingsDatebar extends EzvizBase {
  setConfig(config) {
    if (!config.sync) throw new Error("sync is required");
    this._config = { reset_after: 300, show_refresh: true, ...config };
    this._build();
    this._render();
  }

  /** Busy and clips-left of the group's cards on the page (one entry per camera). */
  _status() {
    const perCamera = {};
    for (const s of Object.values(syncStatus[this._config.sync] || {})) {
      const cam = (perCamera[s.serial] = perCamera[s.serial] || { busy: false, loading: false, missing: 0 });
      cam.busy = cam.busy || s.busy;
      cam.loading = cam.loading || s.loading;
      cam.missing = Math.max(cam.missing, s.missing); // a hidden copy of the same camera does not count twice
    }
    const cams = Object.values(perCamera);
    return {
      busy: cams.some((c) => c.busy),
      loading: cams.some((c) => c.loading),
      missing: cams.reduce((sum, c) => sum + c.missing, 0),
    };
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
    const offDay = followDay(this._config.sync, () => this._render());
    const offStatus = listen(STATUS_EVENT, this._config.sync, () => this._render());
    this._unfollowDay = () => {
      offDay();
      offStatus();
    };
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
        <button class="arrow prev" title="${T("prev")}">${icon("left")}</button>
        <div class="mid">
          <span class="label"></span>
          <input type="date" class="pick" title="${T("pick")}">
        </div>
        <button class="refresh" title="${T("refresh_all")}"><span class="ic">${icon("refresh")}</span><span class="n" hidden></span></button>
        <button class="today">${T("today")}</button>
        <button class="arrow next" title="${T("next")}">${icon("right")}</button>
      </ha-card>`);
    this.$(".refresh").hidden = !this._config.show_refresh;
    this.$(".refresh").addEventListener("click", () => {
      if (this._status().loading) return; // lists are being read right now
      window.dispatchEvent(new CustomEvent(REFRESH_EVENT, { detail: { sync: this._config.sync } }));
    });
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
    const status = this._status();
    const refresh = this.$(".refresh");
    refresh.classList.toggle("busy", status.busy);
    const n = this.$(".n");
    n.hidden = !status.missing;
    n.innerHTML = `${icon("down")}${status.missing}`;
    refresh.title = status.missing ? T("left")(status.missing) : T("refresh_all");
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
