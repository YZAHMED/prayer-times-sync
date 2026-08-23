# prayer-times-sync

Plays a mosque's live stream through a speaker at prayer time, every day, without supervision.

The cloud does the thinking (fetch the timetable, publish it to this repo). The edge device just plays. If the cloud, the network, or the mosque's API disappears, the device keeps working on its own.

```
     Mosque timetable API                     Mixlr API
              │                                   │
              ▼                                   │
     GitHub Actions (daily)                       │
              │  prayers.json                     │
              ▼                                   │
     This repository ─────────────────┐           │
              │                       │           │
              ▼                       ▼           ▼
     ┌──────────────────────────────────────────────────┐
     │  Edge device: prayer-sync                        │
     │    • pulls the timetable once a day              │
     │    • resolves the stream URL AT PLAY TIME        │
     │    • computes prayer times locally if offline    │
     │    • plays to Bluetooth / 3.5 mm / USB / HDMI    │
     └──────────────────────────────────────────────────┘
```

---

## Install

On a fresh Raspberry Pi OS, Debian, Ubuntu, Proxmox LXC, Fedora, Arch or Alpine box:

```bash
curl -fsSL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/install.sh | sudo sh
```

With options:

```bash
curl -fsSL .../install.sh | sudo sh -s -- \
  --mosque masjid-el-noor \
  --output aux \
  --volume 85 \
  --bluetooth 41:42:8C:C6:B2:80
```

| Flag | Meaning |
| --- | --- |
| `--mosque NAME` | which preset in [mosques/](mosques/) to use |
| `--output MODE` | `auto`, `bluetooth`, `aux`, `hdmi`, `usb`, or an exact device id |
| `--volume N` | 0–100 |
| `--bluetooth MAC` | pair, trust and connect a speaker |
| `--user NAME` | account that owns the sound server (auto-detected) |
| `--no-service` | install files only, don't start anything |
| `--uninstall` | remove everything |

The installer detects the package manager, the init system, and which user owns PulseAudio/PipeWire; installs a systemd unit (or OpenRC, or a cron fallback); removes any v1 install; and finishes by printing a health report.

Verify:

```bash
prayer-sync doctor        # full health check
prayer-sync today         # what will play today
prayer-sync test-audio    # play the live stream for 15 seconds, now
```

---

## Configuring it

Three layers, each overriding the one above:

| File | Scope | Refreshed from the repo? |
| --- | --- | --- |
| `mosques/<name>.json` | the mosque | yes |
| `config.json` | the whole fleet | yes |
| `/etc/prayer-sync/config.local.json` | this one device | **never** |

Edit `config.json` here, commit, and every device picks it up at its next refresh. Put anything device-specific (which speaker, which volume) in `config.local.json` so a refresh can't overwrite it.

### When the stream opens and closes

`offsets` decides, per prayer, how long before the adhan the stream opens and how long after the iqamah it closes:

```json
"offsets": {
  "default": { "pre": 5,  "post": 15 },
  "Maghrib": { "pre": 2,  "post": 10 },
  "Jumah":   { "pre": 10, "post": 20 }
}
```

`pre` is minutes before `schedule.start_anchor`, `post` is minutes after `schedule.stop_anchor`. Change which fields those anchor to if you'd rather bracket the *begins* time than the *adhan* time:

```json
"schedule": {
  "start_anchor": "prayerAdhan",
  "stop_anchor":  "prayerIqamah",
  "prayers": ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"],
  "jumah_replaces_dhuhr": true,
  "jumah_weekday": 5,
  "refresh_at": "02:30",
  "max_window_minutes": 90
}
```

`max_window_minutes` is a safety cap: if the feed ever returns nonsense, the stream still can't run all day. A window that would end after midnight is carried into the next day rather than being cut off at 23:59.

To silence everything overnight regardless of schedule:

```json
"quiet_hours": { "enabled": true, "from": "23:30", "to": "04:30" }
```

---

## Swapping or adding a mosque

A mosque is one file in [mosques/](mosques/):

```json
{
  "id": "masjid-el-noor",
  "name": "Masjid-El-Noor",
  "timezone": "America/Toronto",
  "location": { "latitude": 43.6893245, "longitude": -79.4718826, "elevation": 0 },
  "stream":    { "provider": "mixlr", "mixlr_slug": "masjid-el-noor" },
  "timetable": { "provider": "masjidal", "masjid_id": 11 },
  "calculation": { "method": "ISNA", "asr": "hanafi", "high_latitude": "angle_based" }
}
```

Then point at it with `"mosque": "<id>"` in `config.json`, or per device with `--mosque`.

**Stream providers.** `mixlr` (resolved live from Mixlr's API — just give the slug from `https://<slug>.mixlr.com`), or `static` with a direct `url` for any Icecast/SHOUTcast/MP3 stream.

**Timetable providers.** `masjidal` uses the mosque's own iqamah times and needs the two repo secrets. `aladhan` needs no account at all — it computes from the coordinates you supply, so a new mosque can be added with zero credentials. Iqamah times aren't available there, so the device assumes `offline.assumed_iqamah_gap_minutes` after the adhan.

**Calculation methods:** `ISNA`, `MWL`, `EGYPT`, `KARACHI`, `MAKKAH`, `TEHRAN`, `JAFARI`, or set `fajr_angle` / `isha_angle` explicitly. `asr` is `standard` or `hanafi`.

> Masjid-El-Noor uses **Hanafi** Asr — confirmed by comparing against their published times, where standard Asr was 53 minutes off and Hanafi was 33 seconds off.

---

## Audio: Bluetooth *and* 3.5 mm

Outputs are discovered at play time and tried in order until one genuinely produces sound:

```json
"audio": {
  "output": "auto",
  "priority": ["bluetooth", "aux", "usb", "hdmi", "default"],
  "bluetooth_mac": "41:42:8C:C6:B2:80",
  "volume": 100,
  "normalize": true
}
```

- `auto` walks `priority`. Bluetooth is only offered if the speaker actually connects (with `rfkill unblock` and a reconnect attempt first), so a speaker that's off or out of range falls through to the 3.5 mm jack instead of playing into a dead sink.
- `"output": "aux"` forces the headphone jack, and on older Raspberry Pi kernels also flips the analog/HDMI routing switch. It still falls back to other outputs unless you set `"strict_output": true`.
- ALSA controls are **unmuted before every prayer** — a muted mixer is the most common cause of "everything looks right but there's no sound".
- `"extra_args"` passes flags straight to mpv for unusual hardware, e.g. `"--ao=alsa --audio-channels=stereo"`.

```bash
prayer-sync devices     # what's detected, and the order it will be tried
```

---

## Moving between machines

The same code runs on a Raspberry Pi today and a Proxmox container tomorrow. Two files, two jobs:

| File | Language | Responsibility |
| --- | --- | --- |
| `edge/prayer-sync` | POSIX `sh` | supervise the player, pick and verify an audio output, Bluetooth, signals, service lifecycle |
| `edge/prayer_sync_core.py` | Python 3.9+ | config merge, JSON, timetable parsing, prayer-time maths, window computation |

That split is deliberate. **Every portability bug this project hit came from awk** — mawk (the default on Raspberry Pi OS and Debian) has no `{n,m}` regex intervals and no `0x` constants, and `%c` differs across awks and locales. Each one was invisible except on the exact target build, and one of them meant no prayer would ever have played on a real Pi. There is no awk left in this project.

Requirements: `python3` (3.9+, preinstalled on Raspberry Pi OS, Debian, Ubuntu and Proxmox templates), a media player (`mpv` preferred), and `curl` for installation. `timeout(1)` is used when present, with a shell watchdog as fallback. systemd is used when present, else OpenRC, else a cron fallback.

CI runs the suite on Debian 12, Ubuntu 24.04, Alpine 3.20 and Fedora 40, under `dash` and shellcheck, and against Python 3.9, 3.11 and 3.13.

To move a device: run the installer on the new box and `--uninstall` on the old one. There's no state to migrate.

---

## How it avoids failing

**The stream URL is resolved at play time, not cached daily.** Mixlr mints a new broadcast id every time the mosque goes live, so a URL captured once a day is stale the moment they restart their broadcast. That was the original bug. The resolution ladder:

```
Mixlr channel API → Mixlr legacy API → configured fallback_urls
  → last URL that actually played here → the copy in this repo → a local audio file
```

Each candidate is probed for a real audio content-type before use, and re-resolved every couple of minutes during a window — because the mosque often starts broadcasting *after* the window opens.

**If nothing can be fetched, prayer times are computed locally** from latitude and longitude, using standard solar-position equations. Accuracy against Masjid-El-Noor's own published times:

| | Fajr | Sunrise | Dhuhr | Asr | Maghrib | Isha |
| --- | --- | --- | --- | --- | --- | --- |
| error | 44 s | 28 s | 7 s | 33 s | **2 s** | 35 s |

So a device with a dead network still calls the adhan at the right minute, indefinitely. Above ~48.5° latitude, where the sun may never reach the twilight angles, it falls back to the nearest-latitude (Aqrab al-Bilad) convention rather than producing nothing.

**Other things it survives:** a Pi with no RTC booting at the wrong date (waits for NTP before scheduling); missing tzdata (fails loudly instead of silently shifting every prayer); a corrupt or truncated download (validated before it replaces a working file); a captive portal returning HTML (rejected, not parsed as config); two daemons racing (lock file); a stream that drops mid-adhan (reconnects for the rest of the window); an audio device that's busy (moves to the next one).

It **never rewrites the crontab**. v1 wiped and rebuilt it nightly, so a single network blip at 02:30 meant silence for the whole day.

---

## Commands

```
prayer-sync today               today's schedule
prayer-sync times               locally computed prayer times
prayer-sync doctor              full health report
prayer-sync selftest            77-check regression suite
prayer-sync devices             audio outputs and their priority
prayer-sync test-audio [secs]   play the live stream right now
prayer-sync resolve             stream URLs that would be tried, in order
prayer-sync refresh             pull the latest timetable and config
prayer-sync play <name|url>     play a window or an arbitrary URL
prayer-sync config              the fully merged configuration
prayer-sync stop                stop playback

journalctl -u prayer-sync -f    live logs (systemd)
tail -f /var/log/prayer-sync.log
```

---

## Repository layout

```
config.json                 fleet-wide settings
mosques/*.json              one file per mosque
edge/prayer-sync            orchestrator: player, audio, signals (POSIX sh)
edge/prayer_sync_core.py    data layer: config, timetable, prayer maths
install.sh / uninstall.sh   provisioning
tools/update_prayers.mjs    CI: fetch and validate the timetable
tools/resolve_stream.mjs    CI: refresh the fallback stream URL
prayers.json                published timetable (written by CI)
stream_url.txt              published fallback URL (written by CI)
status.json                 last-update heartbeat
```

### Secrets

Only needed for the `masjidal` timetable provider — **Settings → Secrets and variables → Actions**:

- `PRAYER_API_KEY`
- `PRAYER_API_BASE_URL`

The `aladhan` provider needs neither.

### CI

`update-data.yml` runs twice daily, validates the payload (right date, all five prayers present, times parseable) before committing, retries on push races, and opens a GitHub issue if it fails. It has **no npm dependencies** — Puppeteer and its Chromium download are gone.

`validate.yml` runs on every push: POSIX syntax checks under `dash`, shellcheck with warnings treated as errors, the full self-test inside Debian, Ubuntu, Alpine and Fedora containers, the core suite against Python 3.9/3.11/3.13, and a check that a correct five-prayer schedule is still produced with no network and no cached timetable at all.

---

## Upgrading from v1

Just run the installer. It removes the old `mpv --volume` cron jobs, `/usr/local/bin/prayer_stream.sh`, and the `connect-speaker` service before installing v2, so nothing plays twice.

`offsets.json` and `volume.txt` are now `offsets` and `audio.volume` inside `config.json`. Bluetooth setup moved into `install.sh --bluetooth MAC`; the constant 30-second reconnect loop is gone — prayer-sync reconnects on demand before each prayer instead.

`jq` is no longer used at all, and Puppeteer (with its Chromium download) is gone from CI. The one new dependency is `python3`, which the installer adds if it is missing.
