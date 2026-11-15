# EZVIZ Recordings for Home Assistant

Browse, play and keep the clips your **EZVIZ cameras record on their SD card / internal storage**,
right inside Home Assistant, also for **battery cameras without local RTSP** (eLife and similar):
clips are fetched through the EZVIZ cloud relay, the same way the EZVIZ app does.

- 🎞️ **Recordings card** – daily list of a camera's clips with thumbnails, day selector
  (‹ › / calendar / Today) and inline player; plays the next clip automatically.
- 📅 **Shared day bar** – one day selector for several cards (great on a wall tablet).
- 📁 **Media panel** – *Media → Camera recordings → camera → day → clip*.
- ⬇️ **Automatic download** of today's clips every few hours and when you open the card,
  saved as MP4 + a small JPEG thumbnail, kept until a size limit you choose.
- 🔋 Battery friendly: skips cameras with low battery, opening the card triggers at most
  one download every 10 minutes, record lists are cached (past days are never asked again).

> ⚠️ **Unofficial, community project.** Not affiliated with EZVIZ. It relies on an
> undocumented cloud protocol that EZVIZ may change at any time.

## Requirements

| What | Why |
|---|---|
| [**ezviz_rtsp**](https://github.com/tarasifua/ezviz_rtsp) ("Ezviz Cloud with Video") set up with your EZVIZ account | EZVIZ Recordings reuses its login and camera list – no second login, no MFA prompt |
| `pyezvizapi` **1.0.5.0** (installed by ezviz_rtsp) | the cloud playback code uses parts of it; other versions are refused with a clear log message |
| Home Assistant 2025.1 or newer | |
| Video encryption **off** on the camera (EZVIZ app → camera settings) | encrypted recordings are not supported yet |

Tested with EZVIZ eLife battery cameras. Other models with SD/internal storage should work;
reports are welcome.

## Installation

1. HACS / Marketplace → ⋮ → **Custom repositories** → add
   `https://github.com/FrancescoEsk/ha-ezviz-recordings`, category **Integration**.
2. Install **EZVIZ Recordings**.
3. Add to `configuration.yaml` (all options are optional):

   ```yaml
   ezviz_recordings:
   ```

4. Restart Home Assistant.

<details>
<summary>Manual installation</summary>

Copy `custom_components/ezviz_recordings` into your `config/custom_components/` folder,
add the line above to `configuration.yaml` and restart.
</details>

## Configuration

```yaml
ezviz_recordings:
  storage_dir: /media/ezviz_recordings   # where clips and thumbnails are saved
  storage_limit_gb: 20                   # above this, whole oldest days are deleted (today never)
  require_mount: /media/nas              # optional: save only if this share is mounted
  auto_download: true                    # download today's clips automatically
  auto_hours: [3, 6, 9, 12, 15, 18, 21]  # when (plus a last pass at 23:50)
  min_battery: 20                        # skip cameras below this battery %
  media_days: 30                         # days listed per camera in the Media panel
```

| Option | Default | Notes |
|---|---|---|
| `storage_dir` | `/media/ezviz_recordings` | inside `/media` the files also show up under *Media → My media* |
| `storage_limit_gb` | `20` | a clip is ~0.6 MB, a thumbnail ~3 KB; ~50 clips/day ≈ 30 MB/day |
| `require_mount` | – | use it with a NAS/NFS/SMB share so a missing share never fills the HA disk |
| `auto_download` | `true` | `false` = clips are downloaded only when you play them |
| `auto_hours` | `[3,6,…,21]` | every download wakes the camera for ~10 s per clip |
| `min_battery` | `20` | |
| `media_days` | `30` | |

## Cards

The cards are installed by the integration itself (no separate frontend resource needed).

### Recordings card

```yaml
type: custom:ezviz-recordings-card
serial: ABC123456        # camera serial (required)
title: Front door        # optional, default = camera name
max_height: 480px        # optional, height of the clip list
autoplay_next: true      # optional, play the next (older) clip when one ends
sync: cameras            # optional, follow an ezviz-recordings-datebar with the same sync
hide_bar: true           # optional, hide this card's own day selector (use with sync)
```

### Shared day bar

```yaml
type: custom:ezviz-recordings-datebar
sync: cameras            # required, same value as the cards it drives
reset_after: 300         # optional, back to today after N s without touches (0 = never)
```

Ready-made dashboards in [`examples/`](examples):
[simple](examples/dashboard-simple.yaml) ·
[one day bar for all cameras](examples/dashboard-shared-day.yaml) ·
[gallery look (needs card-mod)](examples/dashboard-gallery.yaml).

The card elements have plain class names (`.head`, `.bar`, `.list`, `.clip`, `.lead`,
`.t`, `.d`, `.player`…) so they are easy to restyle with card-mod.

## Service

`ezviz_recordings.download_today` – download now every clip of today that is not saved yet;
returns a per-camera summary.

## How it works

1. The list of clips of a day comes from the camera's storage (`search_records_v2`), cached in
   `.storage/ezviz_recordings.records`.
2. A clip is requested from the EZVIZ VTM cloud relay as a *playback* stream for its exact
   time range, then remuxed (no re-encoding) to MP4 with ffmpeg; a 192 px thumbnail is taken
   from the middle of the clip.
3. Files are served only to logged-in users, through signed URLs.

## Known limitations

- Video is **HEVC/H.265**: plays in Edge, Chrome on most PCs, Safari/iOS and the HA apps;
  not in Firefox.
- Sleeping battery cameras can take up to ~40 s to answer, especially at night.
- Clips of days before the integration was installed are listed and playable on demand, but
  are not downloaded automatically (each one would wake the camera).
- Encrypted video is not supported yet.

## Credits

- The cloud SD-card playback request is based on the work in
  [pyEzvizApi PR #417](https://github.com/RenierM26/pyEzvizApi/pull/417)
  (and #266) by the pyEzvizApi contributors.
- Live view and camera entities come from
  [ezviz_rtsp](https://github.com/tarasifua/ezviz_rtsp) by @tarasifua.

## License

[MIT](LICENSE)

---

### 🇮🇹 In breve

Integrazione per **vedere, riprodurre e conservare in Home Assistant le registrazioni salvate
sulla memoria delle telecamere EZVIZ**, anche quelle a batteria senza RTSP locale. Serve
l'integrazione **ezviz_rtsp** già configurata con l'account EZVIZ. Installazione da
HACS/Marketplace come *repository personalizzato*, poi `ezviz_recordings:` in
`configuration.yaml` e riavvio. Le card e la sezione Media sono in italiano se HA è in italiano.
