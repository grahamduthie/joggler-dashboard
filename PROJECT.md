# Joggler Kiosk — Technical Reference

## What Is This?

An O2 Joggler (OpenPeak OpenFrame 1) repurposed as a smart home kiosk for Twyford, Berkshire. It
displays a full-screen touch-driven dashboard with live weather (including indoor temperatures from
Hive heating), internet radio (30+ stations, with Chromecast casting), WagtailCam live
stream/timelapse, trains from Twyford station, flights radar, and a live bus departure board
with map. Standalone SPAs at `/aircraft` and `/trains` provide detailed full-screen views of
nearby aircraft and passing trains respectively.

The original Joggler OS is non-functional. The device runs openframe-linux (Debian Trixie) on a
USB stick and acts as a **thin client**: it runs Chromium in kiosk mode pointed at a Raspberry Pi
that handles all API proxying and serves the dashboard.

---

## Architecture

```
Raspberry Pi (172.16.10.136, user gduthie)
  /home/gduthie/twyford-dashboard/
  ├── transport-proxy.py   port 5001  (0.0.0.0) — API proxy + static file server
  ├── dashboard.html, icons/, hls.min.js — served as static files by transport-proxy
  ├── hive-tokens.json, hive-credentials.json (mode 600)
  ├── .env  — BODS_API_KEY + LASTFM_API_KEY + RTT_REFRESH_TOKEN + NR_USERNAME + NR_PASSWORD (mode 600)
  └── logos/, aircraft-info/, bus-stops.json, bus-route-stops.json (runtime caches)

O2 Joggler (172.16.10.168, user of)
  /home/of/
  ├── Chromium kiosk → http://172.16.10.136:5001/
  ├── cast-server.py       port 9998  (0.0.0.0) — Chromecast discovery/control
  ├── shutdown-server.py   port 9999  (127.0.0.1) — graceful poweroff via power button
  └── touch-bridge.py — raw touchscreen events → X11 mouse via XTest
```

The dashboard is served from the Pi. All `/api/…` calls in the HTML are relative URLs and
resolve to the Pi automatically from any browser on the LAN.

**Chromecast note:** `CAST_BASE = 'http://localhost:9998'` in `dashboard.html`. The Joggler's
kiosk browser resolves `localhost` to the Joggler itself, so cast-server.py runs **on the Joggler**
(not the Pi) at port 9998. Casting therefore works from the Joggler's kiosk. It will not work from
a Mac/phone browser because `localhost:9998` resolves to the client machine, not the Joggler.

**Power button note:** `JOGGLER_SHUTDOWN = 'http://localhost:9999/shutdown'` — resolves to the
Joggler's own shutdown-server.py (127.0.0.1:9999), so the power button only works from the
Joggler's kiosk. On other browsers `localhost:9999` is unreachable, which is intentional.

---

## Hardware

| Component | Detail |
|-----------|--------|
| Model | O2 Joggler / OpenPeak OpenFrame 1 |
| CPU | Intel Atom Z520 (i686, 32-bit, single core, ~1.33 GHz) |
| RAM | 492 MB — critical constraint |
| Display | 7" 800×480 resistive touchscreen |
| Internal storage | 1 GB eMMC (mmcblk0) — NOT used; too small and fragile |
| USB | One port — used for boot drive |
| Network | WiFi, Realtek rtl8192su driver |
| Touchscreen | AmSC OpenPeak Hyup02, USB HID, /dev/input/event1 |
| Framebuffer | /dev/fb0 via EMGD — use `fbdev` Xorg driver, NOT modesetting |

**Critical RAM constraint:** 492 MB. Chromium must run with `--disable-gpu --single-process` or it
OOMs. No GPU acceleration; all rendering is CPU-only.

**Touchscreen constraint:** resistive single-touch only. No pinch-to-zoom. Navigation relies on
tap and the +/- zoom buttons on maps.

---

## OS

**birdslikewires/openframe-linux** on a 16 GB USB stick (PNY USB 2.0, ~24 MB/s raw read).
- Debian Trixie base, custom kernel 6.18.31 with Joggler hardware patches
- Chromium 148 (modern: HTTPS, CSS gap, ES6, Leaflet.js all work)
- Image: `of-ext2-1028-46-trixie-v6.18.31.img.gz`
- GitHub: https://github.com/birdslikewires/openframe-linux

**Flashing a new USB:**
```bash
gunzip -c of-ext2-1028-46-trixie-v6.18.31.img.gz | sudo dd if=/dev/stdin of=/dev/diskN bs=4m
# Then on first boot:
sudo of-expand   # expands root partition to fill the drive
```

**Cloning to a larger USB (Mac):**
```bash
diskutil unmountDisk /dev/diskN && diskutil unmountDisk /dev/diskM
sudo dd if=/dev/diskN of=/dev/diskM bs=4m status=progress
# Then on Joggler first boot: sudo of-expand
```

The original 4 GB generic USB is kept as a fallback (boots the same system, slightly slower at ~17 MB/s).

---

## Boot / Autostart Chain

### Joggler

```
tty1 autologin as 'of'
  → .bash_profile: startx
    → .xinitrc: openbox-session
      → .config/openbox/autostart:
          python3 touch-bridge.py &
          python3 shutdown-server.py &
          python3 cast-server.py &
          unclutter -idle 0.1 -root &
          kiosk.sh &
            → chromium --start-fullscreen http://172.16.10.136:5001/
            (watchdog loop — auto-restarts on crash)
```

`touch-bridge.py`, `shutdown-server.py`, and `cast-server.py` run on the Joggler. The transport
proxy runs on the Pi.

**Why cast-server.py runs on the Joggler:** `dashboard.html` calls `http://localhost:9998` from
JavaScript. Since JS runs in the Joggler's browser, `localhost` resolves to the Joggler — not
the Pi. cast-server.py must therefore run on the Joggler to be reachable.

### kiosk.sh

kiosk.sh runs a `while true` watchdog loop. On each iteration it clears stale Chromium singleton
locks, resets the profile's `exit_type` to `Normal` (prevents the "Restore pages?" dialog after a
crash), then launches Chromium. When Chromium exits for any reason it restarts automatically after
5 seconds. Crash history is logged to `/tmp/kiosk-watchdog.log`.

Key Chromium flags:

```bash
chromium \
  --start-fullscreen \   # replaces --kiosk (which creates a stuck 10×10 window on this Xorg setup)
  --test-type \          # suppresses the --no-sandbox warning banner
  --no-first-run --disable-infobars \
  --no-sandbox --disable-gpu --disable-extensions \
  --disable-sync --disable-background-networking \
  --disable-default-apps --single-process \
  --disable-dev-shm-usage \
  --disk-cache-dir=/tmp/chromium-cache \
  --disk-cache-size=52428800 \
  --js-flags="--max-old-space-size=80" \
  --window-position=0,0 --window-size=800,480 \
  http://172.16.10.136:5001/
```

**`--kiosk` is broken on this system** (Chromium 148 + Openbox + EMGD framebuffer): it creates a
10×10 window that never expands. `--start-fullscreen` uses the standard `_NET_WM_STATE_FULLSCREEN`
mechanism that Openbox handles correctly.

**`--single-process` requires `--no-sandbox`**, which triggers a Chromium warning banner. `--test-type`
suppresses it without affecting page behaviour.

`/tmp` on openframe-linux is a tmpfs (RAM-backed, 246 MB). Redirecting Chromium's HTTP disk
cache there eliminates USB I/O for cached resources. Without this, every page fetch causes USB
writes at ~24 MB/s and drive wear. `fix-oom.sh` sets this up on a fresh or repaired system.

**`vm.swappiness = 10`** (set in `/etc/sysctl.d/99-joggler.conf`) — reduces kernel eagerness to
swap under memory pressure. Default is 100 on this distro. Lower values keep hot Chromium pages
in RAM longer, reducing I/O wait spikes. Applied by `fix-oom.sh`.

### Pi (systemd)

```
twyford-dashboard.service
  → python3 /home/gduthie/twyford-dashboard/transport-proxy.py
```

cast-server.py is started separately (manually or via a second systemd unit). See PI-SETUP.md.

**The Pi is shared with an unrelated project** (2026-07-07): it also runs
`train-pi-controller.service`, the backend for a Raspberry-Pi-driven OLED train/bus/tube/
flight departure board — a separate repo at `~/Programming/TrainPi` on the Mac, nothing to
do with the Joggler dashboard. Same physical Pi (906 MB RAM), independent codebase, independent
systemd unit.

**Boot-time memory race (found and fixed 2026-07-07):** `transport-proxy.py` downloads and
`gzip.decompress()`s the full daily CIF freight schedule synchronously at startup — 15 MB
compressed → ~360 MB in memory (see `CIF: NN KB compressed → NN KB uncompressed` in
`dashboard.log`). On a 906 MB Pi this is the single largest transient memory spike on the box.
`train-pi-controller.service` used to have no ordering relative to `twyford-dashboard.service`
and a very aggressive restart policy, so if its own boot-time Python import landed inside this
spike it would crash-loop and give up permanently (`Start request repeated too quickly`),
requiring manual SSH intervention. Fixed on the TrainPi side (not in this repo) by adding
`After=twyford-dashboard.service` + a short `ExecStartPre=/bin/sleep 8` to
`train-pi-controller.service`, plus a more forgiving restart policy (`RestartSec=3`,
`StartLimitBurst=30`/`StartLimitIntervalSec=120`) so a collision self-heals instead of
failing permanently. See `~/Programming/TrainPi/CLAUDE.md` for the full writeup.

**Why this matters here:** if you ever change `transport-proxy.py`'s startup behaviour —
especially anything that makes the CIF load bigger, slower, or moved earlier/later in
startup — it changes the size/timing of this memory spike and could reopen the collision
window for the co-hosted service. Not a reason to avoid changes, just worth knowing the Pi
isn't dedicated to this project alone.

**Pi-only files are backed up outside git — keep the backup current:** the Pi's SD card
started showing signs of failure on 2026-07-08 (intermittent binary corruption). Everything
under `/home/gduthie/twyford-dashboard` that isn't tracked in this repo (`.env`,
`hive-credentials.json`, `hive-tokens.json`, `bus-stops.json`, `bus-route-stops.json`,
`berth_chain.json`, `logos/`, `aircraft-info/`, `airport-names.json`,
`calibration_log.jsonl`) plus the `twyford-dashboard.service`/`twyford-cast.service` unit
files were mirrored to `~/Programming/pi-backups/2026-07-08/joggler/` on the Mac (see the
`README.md` there for full contents and restore steps). If you add new gitignored files on
the Pi, change credentials/tokens, or edit either unit file, refresh that backup (or make a
new dated one) so a card failure doesn't lose them.

---

## SSH Access

**Joggler (openframe-linux, PNY 16 GB USB):**
- IP: `172.16.10.168` · User: `of` · Auth: SSH key only (password auth broken)

```
Host 172.16.10.168
  User of
  IdentityFile ~/.ssh/id_ed25519
```

**Pi:**
- IP: `172.16.10.136` · User: `gduthie`

**Backup Joggler (old Ubuntu USB):**
- IP: `172.16.10.179` · User: `joggler`

```
Host 172.16.10.179
  User joggler
  HostKeyAlgorithms +ssh-rsa
  PubkeyAcceptedAlgorithms +ssh-rsa
  IdentityFile ~/.ssh/id_ed25519
```

---

## Files

### On the Pi (/home/gduthie/twyford-dashboard/)

```
dashboard.html              # The kiosk SPA (single file, all views)
aircraft.html               # Standalone aircraft detail SPA (served at /aircraft)
trains.html                 # Standalone trains SPA (served at /trains)
lineside.html               # Standalone visual track display SPA (served at /lineside)
transport-proxy.py          # API proxy + static file server (port 5001)
cast-server.py              # Chromecast discovery/control (port 9998)
hive-setup.py               # Interactive Hive auth setup (run once to obtain tokens)
hls.min.js                  # HLS.js library (served statically to browser)

hive-tokens.json            # Hive/Cognito auth tokens + home_id (mode 600)
hive-credentials.json       # Hive login credentials for auto-reauth (mode 600)
.env                        # BODS_API_KEY + LASTFM_API_KEY + RTT_REFRESH_TOKEN + NR_USERNAME + NR_PASSWORD (mode 600)

icons/
  wsymbol_*.png             # 92 PNG weather icons (MAm TV set, 128×128)
  *.png / *.svg             # Radio station logos, WagtailCam logo

logos/                      # Airline logos cached from pics.avs.io (created at runtime)
aircraft-info/              # Aircraft year/reg from OpenSky (created at runtime)
airport-names.json          # IATA→name map from OurAirports CSV (downloaded on first run)
bus-stops.json              # OSM bus stop locations (created on first bus map load)
bus-route-stops.json        # Bus route timetable stop lists (built progressively)
```

### On the Joggler (/home/of/)

```
kiosk.sh                    # Launches Chromium in kiosk mode
touch-bridge.py             # Raw touchscreen → X11 pointer events via XTest
shutdown-server.py          # HTTP :9999 → graceful poweroff (Joggler-local only)
cast-server.py              # Chromecast discovery/control (port 9998, 0.0.0.0)

.config/openbox/autostart   # Starts touch-bridge, shutdown-server, cast-server, kiosk.sh
.local/bin/pip              # pip installed via get-pip.py (not in apt on Trixie)
```

### In this repository (/Users/gduthie/Programming/Joggler/)

```
dashboard.html              # Source (deploy to Pi with scp)
aircraft.html               # Standalone aircraft detail SPA (served at /aircraft)
trains.html                 # Standalone trains SPA (served at /trains)
lineside.html               # Standalone lineside visual track display (served at /lineside)
transport-proxy.py          # Source
cast-server.py              # Source
shutdown-server.py          # Source
touch-bridge.py             # Source
hive-setup.py               # Source — interactive Hive first-time auth
PROJECT.md                  # This document
README.md                   # GitHub landing page
JOGGLER-SETUP.md            # Setup guide — Joggler thin client
PI-SETUP.md                 # Setup guide — Raspberry Pi backend
setup-kiosk.sh              # One-time setup script for a fresh Joggler USB
bench-drive.sh              # USB drive benchmark (dd + hdparm)
fix-oom.sh                  # Apply sshd OOM protection + update kiosk.sh
icons/                      # Icons directory (matches Pi icons/ directory)
```

---

## The Dashboard (dashboard.html)

Single-file SPA (~5,000 lines). All views are `<div class="view">` elements shown/hidden by JS
— no page reloads, no network dependencies for the page itself. Audio plays continuously across
view changes.

### Responsive Design

The dashboard detects the display at startup and sets a class on `<html>`:

| Class | Condition | Layout |
|-------|-----------|--------|
| `profile-joggler` | w==800 && h≤490 | Original Joggler layout; power button shown |
| `profile-phone-portrait` | w≤540 && h>w | 2-col tile grid, views scroll vertically |
| `profile-phone-landscape` | w≤900 && h≤500 | 3-col compact tiles, views fill screen |
| `profile-card` | Everything else | 800 px centred card, rounded corners, power button hidden |

An early-running `<script>` in `<head>` sets the class before the first CSS paint (prevents
layout flash). A `resize` listener handles orientation changes.

### Top Bar (always visible)

Left: date + HH:MM:SS clock. Right: ⌂ home button (hidden on home screen) or ⏻ power button
(home screen only — Joggler profile only; hidden in card/phone profiles).

### Home Screen — 3×2 Tile Grid

Six tiles. Key layout notes:
- `.app-tile { min-height: 0 }` — essential to prevent CSS grid 1fr rows growing beyond
  their calculated size (bottom row would overflow off-screen without this)
- Title label: `position: absolute; bottom: 12px` — same position on every tile

| Tile | Background | Dynamic content | Update rate |
|------|-----------|-----------------|-------------|
| Weather | `#1a5276` | Condition icon, temp, hi/lo | Every 10 min |
| Radio | `#6030a0` | Station logo ± track artwork, show/track info | Live (SSE or proxy poll) |
| WagtailCam | `#3d7025` | Latest timelapse thumbnail | Every 5 min |
| Trains | `#963020` | Next 5 departures: `22:03 PAD OT` etc | Every 120 s |
| Aircraft | `#1d8a90` | Nearest aircraft callsign + route | Every 60 s |
| Buses | `#7a3060` | Next 5 bus departures | Every 90 s (proxy TTL 30 s) |

**All interactive elements use `onmousedown` (not `onclick`)** — the Joggler's resistive
touchscreen injects `BTN_LEFT` via XTest; `onclick` requires a clean press+release at the same
coordinates, which finger drift prevents. `onmousedown` fires on first contact regardless of
where the finger lifts. This applies to all HTML attributes AND JS-assigned handlers (e.g.
dynamically created cast device buttons, wcam date buttons, weather hour columns). Canvas
handlers (`wxCanvas.onclick`, flight radar `canvas.onclick`) deliberately keep `onclick`.

**Touch support for mobile browsers:** All buttons also carry `ontouchstart` handlers matching
their `onmousedown`. The flight map canvas has explicit `touchstart`/`touchmove`/`touchend`
listeners with `{passive: false}` on `touchmove` so `preventDefault()` can suppress page
scrolling. The canvas also has `touch-action: none` CSS. Without these iOS Safari intercepts all
touch events as page scroll gestures.

**Tile timer behaviour:** All tile `setInterval` timers are stopped when any full view is opened
and restarted (with an immediate fetch) when returning to the home screen. This prevents
unnecessary API calls while the user is looking at a different view.

**Departure status suffixes (trains and buses):**
`OT` = on time · `NL` = N minutes late (amber) · `C` = cancelled (red) · `D` = delayed unknown

---

## Views

### Weather View

Source: Open-Meteo (HTTPS, no API key). Location: lat=51.474, lon=-0.861 (Twyford, Berkshire).
Fetched on load and every 10 minutes.

**Always use the default model (ECMWF-based best match). Do NOT add `&models=ukmo_seamless` —
the Met Office UKV model does not return `precipitation_probability`, which breaks all rain %
displays.**

Three tabs — NOW / TODAY / WEEK:

**NOW tab** (`#wx-now-main`, 3-column layout):
- Left (`#wx-now-left`, flex: 0 0 268px): weather icon + temperature · condition + feels-like ·
  2×3 stat chip grid (humidity, dew point, hi/lo, rain today, AQI, visibility)
- Middle (`#wx-now-mid`, flex: 1): "Next hours" — 6 rows: time label · icon · temp positional
  marker bar (position on today's day range) · rain% · rain amount (mm or `trace`).
  Bar formula: `left = (temp - dayMin) / dayRange * (100 - barWidth)%` where `barWidth = 12%`
- Right (`#wx-now-right`, width: 234px): sun arc SVG (viewBox 300×110, explicit `height: 70px`)
  · wind compass wrap · indoor temperature strip

**TODAY tab**: temperature curve canvas (`#wx-chart`, 800×262) + precipitation % bars.
Zone layout: `TEMP_TOP=24, TEMP_H=158, SEP=10, RAIN_TOP=192, RAIN_H=44`. 8 icon columns
below (tap to select hour). Horizontal scroll on phone portrait.

**WEEK tab**: 7-day rows with temp bar, hi/lo, rain, wind, pressure. Tap a row to expand
(sunrise/sunset, UV, precipitation sum). Feels + pressure columns hidden on phone portrait.

**Pressure trend arrow:** Computed from 3-hour delta in `hourly.pressure_msl` using yesterday's
data (enabled by `past_days=1`): `pArr[24 + nowH] - pArr[24 + nowH - 3]`. Threshold ±1.5 hPa.

**Indoor temperatures (`#wx-indoor`):** Fetched from `/api/hive` when weather view opens and
every 5 min. Shows "INDOOR UPSTAIRS" / "INDOOR DOWNSTAIRS". Hidden when Hive returns no data.

**Rain amount display convention:**
- ≥ 0.1 mm: show actual figure e.g. `1.4mm`
- < 0.1 mm: show `trace`
- Daily views: use `precipitation_sum` (mm/day)
- Hourly views: use `precipitation` (mm/hr)

**Open-Meteo API call:**
```
https://api.open-meteo.com/v1/forecast
  ?latitude=51.474&longitude=-0.861
  &current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,
           wind_gusts_10m,relative_humidity_2m,apparent_temperature,
           precipitation,cloud_cover,surface_pressure,uv_index,is_day,
           dew_point_2m,visibility
  &hourly=temperature_2m,precipitation_probability,precipitation,
          weather_code,pressure_msl
  &daily=temperature_2m_max,temperature_2m_min,weather_code,
         sunrise,sunset,precipitation_probability_max,precipitation_sum,
         wind_speed_10m_max,wind_direction_10m_dominant,
         apparent_temperature_max,apparent_temperature_min,uv_index_max
  &timezone=Europe%2FLondon&past_days=1&forecast_days=7
```

**`past_days=1` array indexing:** Both `hourly` and `daily` arrays start from yesterday.
- `hourly[0]` = yesterday 00:00; `hourly[24]` = today 00:00; `hourly[24 + H]` = today hour H.
- `daily[0]` = yesterday; `daily[1]` = today; `daily[1+i]` = forecast day i.
- All code that accesses "today" data must use these offsets. The forecast strip and WEEK tab
  loop from `i=1`, not `i=0`.

**Air Quality API (separate call, every 30 min):**
```
https://air-quality-api.open-meteo.com/v1/air-quality
  ?latitude=51.474&longitude=-0.861&current=european_aqi&timezone=Europe%2FLondon
```

### Radio View

Multi-station internet radio player. Station picker is a 3-column image grid. ~30 stations
including Marlow FM (local community station with live now-playing SSE), Bauer stations (via
PLS playlist → stream URL), Global stations (direct ICY streams), BBC World Service, Radio
Paradise, FIP, and local/independent stations.

**Stream types:**
- `streamUrl`: Direct MP3/AAC stream. Played directly by `<audio>`.
- `streamPlaylist`: Bauer `.pls` playlist — must call `/api/radio/resolve` each time to get a
  fresh `skey`-authenticated URL. Never cache these URLs.
- `icyMeta: true`: Proxy fetches ICY metadata (StreamTitle) via `/api/radio/nowplaying`.
- `rpChan: N`: Radio Paradise — fetch now-playing via `/api/radio/nowplaying-rp?chan=N`.

**Marlow FM SSE (starts at page load, before radio view is opened):**
- Now-playing: `https://now-playing.marlowfm.co.uk:3002/sse-json`
- Show info: `https://episodes.marlowfm.co.uk:3009/sse-json`
- Marlow FM stream is HTTP only (no HTTPS on server) — must use `http://`.

**Last.fm track info:** When now-playing updates with an artist+title, the proxy fetches
`/api/radio/track-info?artist=…&title=…` → Last.fm `track.getInfo` (falls back to
`artist.getInfo` for bio/image if track has no bio) + `artist.getSimilar` (up to 5 names).
Returns `{image, album, bio, listeners, tags, similar}`. Displayed in the playing screen:
album name, genre tags (green chips), artist bio with listener count, then similar artists
(purple chips with "Similar Artists:" label). API key stored as `LASTFM_API_KEY` in `.env`.
TTL: 1 hour.

**Tile display:**
- No track playing: logo · show name · presenter
- Track playing: logo + album artwork · **track title** (bold) · artist · show name · presenter

**Chromecast:** Cast icon (top-right of radio view) → discover devices → tap device name to cast.
The Chromecast streams the radio URL directly; the Joggler is not in the audio path.
`CAST_BASE = 'http://localhost:9998'` — see Architecture section for limitations.

### WagtailCam View

API base: `https://wagtailcam.gdx.org.uk`. Token: `1196aa2a51b6c86f914a800742434dd0de4f9606`
(appended as `?token=…` via `wcamUrl(path)`).

- **Live:** `/api/live?token=…` — MJPEG stream, reconnects every 5 min to prevent Chromium
  accumulating an ever-growing MJPEG buffer in RAM
- **Timelapse:** MJPEG stream with pause/resume, single-frame step, calendar date picker
- **Fullscreen:** hides topbar and controls, fills 800×480
- **Tile thumbnail:** `/api/timelapse/dates` → `/api/timelapse/list?date=…` → `/api/timelapse/image?path=…`
  (3 HTTP calls, every 5 min)

### Transport View

Two tabs: **Trains** and **Flights**. The Trains tile opens Transport on the Trains tab; the
Aircraft tile opens on the Flights tab.

**Trains tab:**

Calls `/api/departures` → National Rail SOAP `ldb12.asmx` → JSON.

Shows next 5 departures. Each row: time · destination CRS · platform · status.
Tap a row to expand calling points. Settings (⚙) persists station/platform to localStorage.
Default: TWY, platform 4.

Departure status: `On time` → `OT` · `Delayed` → `D` · `Cancelled` → `C` · e.g. `Late 3 mins` → `3L`.

**Flights tab:**

Leaflet.js OSM map + canvas overlay. Aircraft triangles rotated by heading.

- ADS-B data via `/api/flights` → adsb.lol API (proxied — adsb.lol dropped direct CORS support)
- Route data via `/api/flight-route?cs=CALLSIGN` → Pi scrapes FlightAware. Results cached in
  `flightRouteCache` (localStorage). **TTL: 30 minutes** — timing data (actual/estimated
  departure and arrival) changes while a flight is in progress, so short TTL keeps delay
  information current. Proxy-side `ROUTE_TTL` is also 30 minutes for the same reason.
- Map is **draggable** — canvas intercepts mouse/touch drag gestures and calls `flightMap.panBy()`.
  Aircraft list shows up to 7 aircraft closest to the map centre that are within the visible bounds.
- **Filter buttons:** Airlines (commercial callsigns in AIRLINES table) / Other / LHR only.
  `flightPassesFilter()` is used for both the canvas draw loop and the list — single source of truth.
- **Map controls:** ⌂ home (re-centres on Twyford), + / − zoom buttons (zoom 6–12, default 9 ≈ 21 nm)
- **Performance (Atom Z520 critical):** Tile layer uses `updateWhenIdle:true, updateWhenZooming:false`
  so tiles only load after drag/zoom ends, not on every `panBy`. Canvas `mousemove`/`touchmove`
  accumulate pan deltas and flush once per rAF frame. `flightOnMoveEnd` debounces data fetches
  by 800 ms to prevent rapid zoom taps each firing a fetch.
- **Cache key normalisation:** `lat`/`lon` rounded to 2 dp, `dist` snapped to nearest 10 nm so
  different screen sizes share the same Pi-side cache entry.
- Twyford coordinates: lat=51.4741, lon=**-0.8647** (not -0.9752 which is Reading/Caversham)

**Config defaults** (localStorage `transConfig`):
```json
{ "station": "TWY", "platform": "4", "flightLat": 51.4741, "flightLon": -0.8647, "flightRadius": 100 }
```

### aircraft.html — Standalone Full-Screen Aircraft SPA

Served at `GET /aircraft` — a separate page from `dashboard.html`, optimised for large-screen
display at a distance (all text sized in `vw` units). Designed to be used in a browser on a PC
or TV alongside the main Joggler kiosk.

**List mode (default):**
- Leaflet.js map (top half) + Leaflet canvas radar overlay with aircraft triangles
- Scrollable list of up to 7 nearest commercial aircraft (ICAO airline prefix in AIRLINES dict)
- Each row: airline logo badge, IATA flight number, origin→destination, aircraft type/reg, stats
- Tap/click a row → **Detail view**

**Detail view (tap any aircraft):**
- Airline logo header (brand background colour)
- Vertical route layout: origin city → arrow → destination city, with dep/arr times
- Aircraft type, registration, and manufacture year
- Live stat cards: altitude (m), speed (km/h), heading (with rotating arrow), distance
- "Back" button returns to list; selected aircraft highlighted orange on radar with trail

**Focus mode ("Focus" button in topbar):**
- Full-screen view of the single closest commercial aircraft, auto-updating
- Airline header: logo (left) + airline name (right) in brand colours
- Large flight number + IATA code centred
- Aircraft type/registration in top-right
- Vertical origin→destination route with dep/arr times (left column) + flight duration/distance (right column)
- Four stat cards below: altitude, speed, heading, distance
- Sends `&focus=1` on ADS-B fetch so proxy uses 20 s TTL instead of 60 s

**Airport name resolution:**
- Static AIRPORTS dict in JS covers ~230 common codes (all major LHR destinations: European,
  North American, Caribbean, Middle Eastern, South Asian, African, Asia-Pacific)
- On cache miss: calls `/api/airport-name?iata=XXX` → proxy reads `airport-names.json`
  (populated from OurAirports CSV, ~10 k IATA codes, downloaded once on proxy startup)
- Result stored in `extraAirports` dict and triggers re-render

**Route data:**
- `fetchRoute(cs)` calls `/api/flight-route?cs=CALLSIGN` (same FlightAware scrape as dashboard)
- Results cached in `routeCache` (also persisted to localStorage)
- Negative results (no orig/dest) cached as `_notFound: true` → shows "No route information
  available" instead of looping on "Route information loading…"

**Location settings:**
- "⚙ Location" button opens modal; location persists to localStorage and can be overridden with
  `?lat=…&lon=…&name=…` URL params
- Uses Nominatim forward geocoding (place-name search) — geolocation API is blocked on HTTP
  origins; Nominatim HTTPS calls work fine from HTTP pages

**Responsive design:**
- Portrait orientation or viewport ≤640 px wide: map stacks above list (38% height for map,
  remainder for list). The existing `window.resize` handler already calls
  `flightMap.invalidateSize()` + redraws the canvas, so orientation changes are handled.
- All `vw`-based font sizes in the focus view and detail view use `clamp(min, Xvw, max)` so
  they floor at readable minimums on phones (e.g. stat values ≥26 px, airport names ≥22 px).
- Viewport ≤600 px: topbar compacted to 52 px, count text hidden, clock shrunk, buttons smaller.
  Altitude-colour legend hidden. Settings modal padding reduced.
- Viewport ≤420 px: title text hidden to avoid overflow (clock + two buttons still fit).
- Landscape phones (height ≤480 px): focus view logo, header, and route padding reduced so stat
  cards still have adequate space.
- All interactive elements carry `touch-action: manipulation` for instant tap response.

### trains.html — Standalone Full-Screen Trains SPA

Served at `GET /trains` — a separate page from `dashboard.html`, showing real-time train
information for all trains passing the house (~200 m east of Twyford station). The house is
adjacent to four tracks: two Main Line tracks (fast GWR inter-city and freight) and two Relief
Line tracks (GWR local and Elizabeth Line stopping services).

**Data source:** Real Time Trains (RTT) API v2 (`data.rtt.io`). RTT is queried with **two calls**
per 30-second polling cycle (run in parallel threads):

1. **Twyford query** (`/gb-nr/location?code=TWYFORD`): trains that call or pass Twyford with a
   WTT timing point — Elizabeth Line and GWR local on the Relief Line (~13 services). These are
   `confirmed=true`. Both `displayAs=CALL` and `PASS` included. Fast Main-line trains have **no
   Twyford timing point** and never appear here.

2. **Reading query** (`/gb-nr/location?code=RDG`): **bi-directional predictor** — the only feed
   that surfaces fast Down-Main expresses (Bristol/Cardiff/Plymouth/Penzance). Reading is ~4 min
   from Twyford: UP trains reach Twyford *after* departing Reading (offset +); DOWN trains pass
   Twyford *before* arriving Reading (offset −). `_rtt_normalise` applies the signed offset
   (3 min Main / 5 min Relief) from the train's own direction. `confirmed=false`.

**`timeFrom` is London LOCAL time** (fixed 2026-07-06): RTT interprets the `timeFrom` query
parameter as Europe/London local. The proxy previously sent UTC, which during BST shifted the
whole window back an hour — hour-old trains lingered in the list and the forward window shrank
from ~90 to ~30 min (upcoming trains were missed). `_rtt_build_trains` now formats
`datetime.now(_TZ_LONDON) − 10 min`.

**Identity index (`ident`)**: before any filtering, every service RTT returned (both queries) is
indexed by headcode → {origin, dest, op_code, op_name, passenger}. Trains that arrive via
TRUST/CIF/TD later in the pipeline get their names from this index (or from CIF).

**Paddington (PAD) query REMOVED** (was the cause of "Down Main always empty"): at Paddington,
`locationMetadata.line.planned` is NOT `DML`/`UML` — it's the platform-group code (`1`/`2`/`3`/`4`
or empty), so the old filter `if line and not line.startswith('D')` discarded every down express.

**Corridor filter (`_passes_twyford`):** Reading is a major junction; a train runs through
Twyford only if Twyford lies *between* its endpoints — exactly one of {origin, destination} is
east of Twyford (`_is_east(origin) != _is_east(dest)`). This rejects both Reading-junction traffic
that bypasses Twyford (Newbury, Basingstoke, Gatwick/Redhill, CrossCountry to the north) AND
trains terminating east of Twyford and turning back (Paddington→Maidenhead Elizabeth Line — both
ends east). `_TWY_EAST_TOKENS` lists strictly-east places (Paddington…Maidenhead).

**Direction & track classification** (`_classify_direction`, `_classify_track`): track (Main/Relief)
is layered — operator (XR/HX→Relief, XC→Main) → line code ending `ML`→Main → genuine relief codes
(`_RELIEF_CODES`: RL/URL/UDL/DDL/…)→Relief → destination character (`_MAIN_DESTS`/`_RELIEF_DESTS`)
→ headcode class (1xxx express=Main, else Relief). Reading throat codes like `WL`/`FVL` are NOT
trusted (a Penzance Down-Main express carries `lc=WL`). "Reading" is deliberately absent from
`_RELIEF_DESTS` (both fast GWR expresses and local stoppers terminate there, so the headcode class
decides). **A live SMART berth line overrides this heuristic** — once a train has a berth fix the
physical line is authoritative (e.g. fast Paddington→Reading express `1R41` reads as Main from its
berth, not Relief from its "Reading" destination). Frontend `isMainTrack(t)` just returns
`t.track==='Main'` (backend is authoritative).

**SMART berth → line + position model** (`_load_smart`, `_berth_info`): the NR **SMART** open-data
file (`SupportingFileAuthenticate?type=SMART`, S3 redirect like CIF; downloaded at startup + daily)
maps each TD berth step to a line (FROMLINE U/D + platform: 1=Down Main, 2=Up Main, 3=Down Relief,
4/5=Up Relief) and a STANOX/location. Combined with hard-coded GWML chainage (`_STANME_MI`,
validated vs BPLAN `kmvalue`), each berth gets `dist_mi` = signed miles from the house (− = east).
This corrected the old (wrong) single-formula calibration: **D6 berths 0475–0594 are Iver→Maidenhead,
EAST of Twyford** (berth 0577 = Maidenhead, ~6.7 mi east — not "the house" as previously assumed).
`/api/td-live` positions are tagged with `line`, `place`, `dist_mi`.

**CA berth-chain learner** (`_ca_observe`, `_rebuild_chain_positions`, persisted to `berth_chain.json`):
SMART only positions berths that are TRUST reporting points, so intermediate signal berths near the
house (1606/1614/1650…) have no SMART row. The TD **CA** message stream steps every train through
every berth, so the proxy accumulates the `from→to` berth adjacency + per-berth transit times and
**interpolates the missing berths' positions** along the chain between SMART anchors (Maidenhead /
Twyford / Reading), weighted by transit time. Conservative: a berth gets a position only when
bracketed by anchors within a few hops; otherwise it falls back to the RTT schedule. Refreshed
every 120 s; the model persists across restarts and sharpens over time.

**Static near-house berth fallback** (`_BERTH_MI`, 2026-07-06 eve): SMART only anchors berths to
whole stations (coarse — every Twyford-area berth reads 0.1 mi, every Slough berth −12.5) and the
chain-learner leaves the immediate throat berths (1623/1626/1614/1633…) with `dist_mi = None`.
A corridor train was therefore **dropped from the list the instant it entered a positionless
berth** — the worst moment (e.g. 4O38 seen leaving Maidenhead then vanishing at 1623). `_BERTH_MI`
gives every corridor berth a coarse signed distance (interpolated west→east between the station
anchors), used by `_berth_info` **only as a last resort** (SMART/chain still win when they have a
real position). It also feeds the frontend's distance-based cell placement (mirrored as
`BERTH_MI`). Values are approximate and tuned per observation — notably Up Main, where trains are
heard passing the house at berths **1640→1626** (2 berths west of the earlier assumption), so those
straddle 0 and the distance zero-crossing house-event fires there.

**Live-berth ETA refinement** (`_berth_eta_to_house_s`, `_td_enrich_trains`): for a matched train
with a live berth, `house_pass_ts` is refined from the real distance (speed by line/passenger),
capped at 8 mi out (constant-speed estimate unreliable further, with intermediate stops). The live
berth is **authoritative over the schedule** — `house_pass_ts = now + eta` whether the train is
approaching (+) or has already passed (−). Three subtleties it handles:
- **Dwelling / held:** a CA berth is a *point*, not a section the train slides along, so the time
  a train has sat in a berth (`age_s`) is credited as progress only up to one berth-step
  (`_BERTH_STEP_S`=50 s). Beyond that it's dwelling at a station or held at a signal — it gets the
  full travel-from-here time and is flagged `held` (fixed a train at Reading platform showing
  "1 min" while 5 mi away).
- **Passed:** a train past the house (down now west / up now east, `to_go < −0.3 mi`) returns a
  negative ETA so a stale schedule + lateness can't keep showing it as upcoming (fixed a 25-min-late
  down train at Reading reading as +15 s).
- **8-mile cap / unknown berth:** falls back to the RTT schedule estimate.

**House-crossing detection is distance-based** (`_detect_house_event`, 2026-07-06): a CA berth
step whose from/to distances straddle 0 (both known, both within 1.6 mi, step < 3 mi) means the
train physically crossed the house — it fires `at_house` (sets `twy_actual`, invalidates the RTT
cache) with the track derived from the SMART line + direction. A step landing within 1 mi on the
approach side fires `approaching`. This replaced the static `_TD_HOUSE_TRANSITIONS` berth-pair
table, whose D6 berths (0569/0573/0577…) the SMART recalibration showed are at **Maidenhead**,
~6.7 mi east — it had been marking Down trains "passed" ~7 min early. The distance rule also gets
stopper semantics right: an Up train dwelling at Twyford (station is west of the house) only
fires when it departs east; a Down train fires on arrival.

**TD corridor synthesis** (`_td_enrich_trains`, 2026-07-06): a live TD fix strictly *inside* the
corridor — Down between Maidenhead and the house (−6.3 < d < −0.05) or Up between Reading and
the house (0.05 < d < 4.95), moving toward the house per SMART's per-berth direction — WILL pass
(no turnback exists in between), so an entry is synthesised (`source='td'`) even when no schedule
source matched. Physical presence beats the name-token corridor heuristic; identity (origin/dest/
operator) is filled from the RTT pre-filter index or today's CIF. Trains sitting AT Maidenhead/
Reading stations stay excluded (they may reverse, e.g. Elizabeth Line Maidenhead terminators).
Trains also carry `td_dist_mi`/`td_place`/`td_berth`/`td_berth_age` so the frontends don't need
to join `/api/td-live` themselves. **Identity guard** (2026-07-06 eve): if a synthesis candidate's
known endpoints say it never passes Twyford (`_passes_twyford` false — e.g. a CrossCountry service
from the north that reverses at Reading and sits in a D1 throat berth ~4.8 mi), it is skipped;
known endpoints beat a lone ambiguous berth fix.

**Phantom CIF drop** (`_phantom_cif`, 2026-07-06 eve): a CIF freight predicted to pass within
`now + 600` s would already be inside the corridor reporting TD berths; if it has no sighting at
all (`not confirmed and not td_berth and not twy_actual`) it isn't running — it's dropped so it
can't show a bogus "N min". Freight further out is kept as a timetable prediction and reappears
once TD sees it.

**Stale-train filter**: after sorting, trains whose pass moment is long gone are dropped —
confirmed keep 30 min (feeds the lineside passing log), unconfirmed estimates 12 min.

**Freight trains via Network Rail STOMP + CIF schedule:** Freight is absent from RTT queries.
The proxy uses two mechanisms:

1. **STOMP TRUST buffer**: On-demand STOMP connection watches STANOX 74023 (Twyford) for
   live movement events. `freight_only: False` — all train types are captured; passenger
   trains are deduplicated against RTT by headcode+time matching. Headcodes beginning `9`
   (departmental/engineering) are excluded from the TRUST freight buffer (`_nr_freight_hc`
   checks `hc[0] in '45678'` only) to avoid duplicating engineering trains that also appear
   in RTT. STANOX 87014 (Twyford stops) triggers immediate RTT cache invalidation so
   confirmed pass times appear within 1–2 s of TRUST.
   **TRUST `actual_timestamp` is London LOCAL wall-clock encoded as epoch-ms-as-if-UTC** (the
   well-known NROD quirk; fixed 2026-07-06 — treating it as UTC had put every freight pass an
   hour in the future during BST). Buffer entries are deduplicated against trains already in the
   list by headcode + 10-min schedule window, and enriched with origin/destination from CIF.

2. **CIF_FREIGHT_FULL_DAILY**: The proxy downloads the NR daily freight schedule (~15 MB gzip
   → ~370 MB) at startup and refreshes at 02:30 daily (`_cif_refresh_loop()`). Download URL:
   `publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_FREIGHT_FULL_DAILY&day=toc-full`.
   Auth note: the endpoint 302-redirects to an S3 pre-signed URL — the download must be done
   in two steps: first request with `Authorization: Basic` to get the redirect Location, then
   second request to S3 WITHOUT the auth header (S3 rejects requests with both auth mechanisms).
   This is handled by `_NoRedirect` urllib handler in `_load_cif()`.
   Trains with TWYFORD TIPLOC `pass` entries are indexed as `_cif_index[headcode]`.
   CIF STP indicator priority: O (overlay) > P (permanent) > N (new); C (cancel) skipped.
   Direction inferred from TIPLOC lists (`_CIF_EAST`, `_CIF_WEST`) relative to Twyford's index.
   CIF trains show with `source='cif'`, `confirmed=False`, `track='Relief'` (approximate —
   most freight uses Relief lines but not all; a live berth fix overrides). Window: −10 min to
   +90 min at serve time (freight paths are speculative — many never run). Trains already in
   RTT or TRUST buffer (matched by headcode) are not duplicated.
   **Origin/destination** (2026-07-06): the first/last `schedule_location` TIPLOCs + times are
   captured per schedule and resolved to readable names via **CORPUS** (`_load_corpus`,
   `SupportingFileAuthenticate?type=CORPUS`, same 302→S3 auth dance; ~12k TIPLOC→NLCDESC names,
   title-cased with freight-operator suffix noise stripped by `_tiploc_name` — e.g. `MERHFHH` →
   "Merehead Quarry", `NTHOLTS` → "West London Waste"). `_cif_ident(hc)` exposes this for
   enriching TRUST/TD entries too.

See the "Network Rail Open Data Feeds" section for STOMP details.

**Henley branch trains excluded:** Trains with headcode prefix `2H` are filtered out. These
run on the Henley-on-Thames branch, diverging from the **west** end of Twyford station, and
are not audible from the house (~200 m east of the station).

**Display — "trackboard" (rebuilt 2026-07-06):** a pure at-a-distance kitchen board. Four
full-width rows, one per line, in **physical order from the house**: Up Relief (nearest),
Down Relief, Up Main, Down Main. No list/detail views — /lineside is the detail page.
Each row: track identity column (arrow + name + "to/from London"), then the next train —
destination in huge Barlow Condensed caps, an operator-colour pill + "from <origin>" +
coaches + live position (`● <place> · X.X mi`, green, from the train's own `td_dist_mi`) +
headcode, then a dim "then HH:MM <dest> | HH:MM <dest>" follow-on line — and on the right a
giant countdown (`N min` / `NN sec` / amber pulsing `NOW` / `HELD` / `AT STATION` / `PASSED`)
with `HH:MM · on time/+N min/estimated` beneath. A thin operator-colour spine marks each row's
left edge; a slow amber sweep animates across a row while a train is passing. Empty row →
"NO TRAIN DUE". Header: TWYFORD title, `NO DATA` indicator if the API goes quiet >60 s, clock
with seconds. NRCC messages appear as an amber DISRUPTION strip pinned to the bottom.
Fonts: Barlow Condensed / Barlow / IBM Plex Mono (Google Fonts, graceful fallback).
Operator colours are brightened-for-dark variants (`OPS` dict) with per-op pill text colours;
freight without a known operator gets an olive "Freight" pill and its CIF origin/destination.

**Grace / headline selection:** headline = first non-cancelled train still within its grace
window: confirmed-passed Down 20 s, Up 45 s (house is ~200 m east of the station), unconfirmed
100 s. Candidates limited to −2.5 min … +65 min. Polling: /api/trains 15 s, /api/nrcc 5 min,
re-render every 1 s.

**Delay handling:** RTT provides delay information via two mechanisms that must both be
handled:

- `realtimeActual` / `realtimeForecast`: ISO datetime for the actual/expected time at the
  queried station. Present when RTT has real-time data.
- `realtimeAdvertisedLateness`: integer minutes late. **Frequently absent** even when a
  forecast is available — do not rely on this field alone.

The proxy computes `late_min` in `_rtt_normalise()` as:
```
late_min = realtimeAdvertisedLateness or 0
if not late_min and realtimeForecast and scheduleAdvertised:
    late_min = max(0, round((forecast - scheduled).total_seconds() / 60))
```

`house_pass_ts` (Unix seconds, used for sorting and display) incorporates the delay when no
confirmed actual time is available:
```
if not actual_time and late_min:
    ts += late_min * 60
```

Frontends derive the pass moment from `house_pass_ts` (falling back to `twy_sched + late_min`).
**`twy_actual` carries RTT's realtime FORECAST until the pass actually happens** — lineside only
shows the `✓ HH:MM` confirmation tick once that time is in the past; before that it renders
`exp HH:MM (+N)`.

**Note on trains missing from Twyford RTT:** When a train is significantly delayed, its
scheduled slot at Twyford may have already passed when the query runs — RTT's location API
uses scheduled times for the query window. Such trains drop out of the Twyford feed and
are only visible via the Reading or Paddington queries (as `confirmed=false`). The
forecast-based `late_min` calculation is especially important in these cases.

**Freight display:** freight now has real CIF/CORPUS origins and destinations ("Merehead
Quarry → Hanwell Bridge Loop"), shown exactly like passenger routes. When no route is known the
headcode-class label is used instead (`4`=intermodal, `6`=heavy haul, `7`=freight, `8`=light
engine, `5`=empty stock). Known freight operators (GB/DW/FL/ZZ/ZN) get their own pill colours;
unknown ones an olive "Freight" pill.

**Token management:** RTT uses a long-lived refresh token (stored as `RTT_REFRESH_TOKEN` in
Pi's `.env`) exchanged for a short-lived access token (~20 min) at `/api/get_access_token`.
The proxy caches the access token and refreshes it 60 s before expiry without blocking requests.

**Rate limits:** 30 req/min, 750/hr, 9000/day. Two queries per 30 s = 5,760/day (within limit).

### lineside.html — Standalone TD berth panel SPA (Tracksy-style)

Served at `GET /lineside`. Rebuilt again 2026-07-06 (second pass, same day) as a
**Tracksy-style signalbox berth panel**: every TD berth section Reading → Maidenhead is drawn
as a cell, and a train's describer lights the cell it currently occupies — physical berth
occupancy is ground truth for what's coming, how far away it is and how it's moving. Fixed
**1280×720 canvas** scaled to fill the window (`scaleToWindow`). Same font/colour system as
the trackboard (Barlow Condensed / Barlow / IBM Plex Mono; relief = teal, main = steel blue,
house = amber).

- **Berth panel (top, 398 px):** **WEST/Reading = LEFT**, north at top, rows top→bottom = Up
  Relief · Down Relief · Up Main · Down Main (the real geographic order — matches Tracksy). Cell
  sequences were derived from the learned CA berth chain (`berth_chain.json`) + SMART and are
  hardcoded in the `LINES` array, west→east, e.g. Up Relief `1676…1642 → [1630 = TWY P4] →
  1628…0594 → [0574 = MAID P4] → 0568` plus the Platform 5 loop (see below).
  **Distance-based cell placement** (2026-07-06 evening): cells are positioned by a per-berth
  signed distance (`BERTH_MI`, mirrors backend `_BERTH_MI`) on **one shared scale per segment**,
  not evenly spaced — so Main and Relief line up by real position and near-house berths cluster
  at the house (the sparse fast lines no longer read as "off"). `segCenter()` maps distance→x
  within the west/east segments; each cell tiles Voronoi-style (midpoint-to-midpoint, edge cells
  to the segment bound) so they never overlap, floored so a 4-char headcode stays legible.
  Distances are approximate interpolations between the station anchors (Reading +5.1 / Kennet Br
  +3.34 / Twyford +0.1 / Maidenhead −6.7) — good enough for which-side-of-house + rough timing,
  tuned per observation (e.g. **Up Main house crossing is at 1640→1626**, so those straddle 0 and
  1626 is the UM `twy` cell; 1618 moved east). Platform berths from CA dwell EWMAs (TWY P4 = 1630,
  P3 = 1637, P1 = 1655, MAID 0570/0573).
  Extras: **Henley branch** rising top-left (P5 bay = A641/B641/R641, jn cell 1643/1632,
  mid-branch BYDN/BYUP "Wargrave · Shiplake", 1636 = Henley), **Reading box** (orange, trains
  inside Reading station berths shown as headcode chips with a 3-letter destination + platform
  label below each — see "Reading box labels" below), Twyford + Maidenhead station bands with
  Tracksy-orange platform bars, junction captions (Kennet Br Jn, Ruscombe Jn, Henley Br Jn),
  amber dashed house line + ★ east of the Twyford band (glows when a house-straddling berth
  1628/1635/1640/1626/1633 is occupied <90 s).
  Empty cells show their berth number faintly; occupied cells fill with the operator colour
  (from the /api/trains headcode join), bold headcode, leading-edge direction chevron (dropped
  on cells <42 px so it can't collide with the headcode; row direction is still shown by the
  line label + line-end arrowhead), destination abbreviation underneath, amber dashed outline
  when held, dimmed when the fix is >180 s old. Extra occupants of one cell stack below (branch
  cells stack upward). Only areas D1/D6 are mapped (D4 could collide).
- **Next past the house (bottom left):** all upcoming trains merged (both directions, next
  45 min, sorted by `house_pass_ts`): big ETA countdown, line chip (→M/←R in line colour),
  operator pill (now shows the **headcode**, kept in the operator colour — the short operator
  code moved to the subline, next to origin), destination + origin subline, sched HH:MM +
  punctuality + **live expected time** `+N → HH:MM` when it differs from schedule (`✓ actual`
  once past), and live berth fix `● berth · place · X.X mi` (falls back to `td_dist_mi`, then
  "~ schedule"). List/NOW rules (tuned 2026-07-06/07 — see "NOW window" and "Passing log" below
  for the exact mechanics): a train whose Twyford **call is cancelled but which has a live berth
  fix** is kept (it still passes the house) and flagged `✗ not stopping`; `HELD` / `AT STN` take
  priority over `NOW`/`DUE`.
- **Info pane (bottom right):** passing log (client-side, confirmed passes, last 30 min) +
  NRCC message + stats (trains next hour, freight count, live TD fixes).
- Polling: /api/trains 15 s, /api/td-live 5 s (feed dot blinks green each tick, turns red
  when TD silent >30 s), /api/nrcc 5 min; countdown re-render every 1 s.
- Signal-aspect dots remain out (positions unconfirmed); `/api/td-live` still returns `signals`.
- Reference screenshots of the real Tracksy layout: `ReadingToTwyford.png`,
  `TwyfordToMaidenhead.png` (repo root).

#### Calibration ("heard it pass" button, added 2026-07-06)

"⏱ CAL" toggle in the topbar shows an overlay panel (hidden by default, absolutely positioned
top-right of the schematic) with 4 buttons — UR/DR/UM/DM. Press whichever line you just heard
pass the house: `pickCalibCandidate()` finds the train on that **physical** line (via live TD
berth → `LINE_OF`, NOT the booked schedule track — a train can switch Main⇄Relief near
Ruscombe Jn after RTT/CIF already tagged it with its original track, which caused an early
mis-attribution to 9R56 when the real train was 5R43) whose `house_pass_ts` is nearest to now,
and logs `{ts (server clock, not client — avoids clock skew), line, headcode, dest,
predicted_ts, offset_s, sched_ts, sighted, td_berth, dist_mi, confirmed}` to
`calibration_log.jsonl` via `GET /api/calibrate`. `GET /api/calibration` returns recent presses
+ per-line stats + `applied` offsets. Once a line has ≥4 samples, `_load_calib_offsets()`
(median, refreshed ≤ every 5 min) feeds a correction straight into `house_pass_ts` in
`_rtt_build_trains()` — all-positive corrections so far (model was predicting the house-pass
15–35 s early on UR/DM/UM; DR noisier, smaller n). Panel shows a green ✓ + the live applied
value per line once active, else "(n=X, need 4)".

#### NOW window (tuned 2026-07-06/07)

`renderAppr()`'s NOW/DUE flash is **asymmetric**: `NOW_LEAD_MS`=20000 before the predicted pass,
`NOW_TAIL_MS`=30000 after — originally a bug had NO lower bound at all (`diff <= 25000` matched
any amount in the past), so a train kept pulsing NOW for the full 90 s list-retention window
long after it had actually passed. `hasPassedHouse(t)` gives an **immediate** physical override
independent of any timer: a live TD sighting whose signed `dist_mi` has crossed the house for
that direction (UP: negative, DOWN: positive) drops the train from the list right away — but
with a `PASSED_GRACE_MS`=20000 grace period (`msSincePassed()`) so it doesn't vanish the instant
it crosses, only 20 s later.

#### Passing log (fixed 2026-07-06 eve)

`harvestLog()` used to run only every 15 s (tied to `/api/trains`) on a **predicted-time**
check, which could lag well behind physical reality — a train the map already showed as
"gone" could take a long time to show up in the log. It's now driven by the same
`msSincePassed`/`PASSED_GRACE_MS` signal as the approaching-list removal (checked every second
via `tick()`), so a train is logged in the exact same tick it disappears from "Next Past The
House" — no gap between the two. Falls back to the old predicted-time check only for
schedule-only trains that never got a live TD fix at all.

#### Maidenhead station rebuilt to match the real track layout (2026-07-07)

Corrected against SMART/BPLAN data for TIPLOC `MDNHEAD` (STANOX 74005) plus live user
cross-checks against Tracksy, after the original model wrongly merged Platforms 4 and 5 into
one cell and mis-drew the Marlow branch:
- **Platforms 4 (0574) and 5 (0576) are NOT in line** — Platform 5 is a **loop off the Up
  Relief**, drawn on its own row (`maidLoopY`, above Platform 4 — real platform order top-to-
  bottom is 5,4,3,2,1) with an island platform bar between the two rows.
- Loop fed **west to east**: Crossrail stabling joins first (furthest west), then a **crossover**
  back to the Up Relief through line (just after berth 0594, not before) — this crossover is
  what lets a stabling train reach *either* platform — then the **Marlow branch** joins closest
  to the platform, i.e. *after* the crossover, so branch trains can only ever reach Platform 5.
  Berth `3570` (STANME confirmed via SMART: only ever connects to 0576/0581, nothing else) is
  the branch's own token berth; Crossrail stabling proper is `0580,0582,0584,0586,0588,0590`
  (confirmed via the *dedicated* Carriage Sidings BPLAN lookup, TIPLOC `MDNHDCS`/STANOX 74003,
  STANME "MDNHD CS", each reaching both Platform 4 and 5) **plus `6296`, inherited from an
  earlier session and not yet independently verified** — flag if it turns out wrong. `0578`
  looked like a 7th stabling road at first (same fan, reaches P3/P4 like the others don't reach
  P5) but is **not** — live STANME resolves it to "MDNHDMIDS", i.e. it's actually part of the
  turnback siding (see below), not Crossrail stabling.
- **Turnback siding is berths `R578` AND `0578` together** (both confirmed live with STANME
  "MDNHDMIDS" — distinct from the carriage sidings' own R-berths R580-R590), positioned
  **equidistant between the Up Relief and Down Relief rows** west of the station, fed by
  connectors from BOTH Platform 3 (0577) and Platform 4 (0574/0579). One train fits in the
  siding at a time; R578 is the physical *arrival* step (from 0577, a real CA move) while 0578
  is where a CC headcode-relabel lands on the same physical track — confirmed live: 5N50
  arrived at R578 from 0577, and ~5 s later 5N51 appeared at 0578 via an interpose (empty
  `from_berth`). That's also the mechanism behind 5N14→5N21 and 5N41→9U41 elsewhere in the
  siding/platform: a reversing train gets a new headcode for its return working without
  physically moving — see "Headcode supersession" below. No text caption on this cell (removed
  — user found it unhelpful; the two feed lines already show what it's for).

#### TD staleness window widened to 30 min (2026-07-07)

A CA berth step only fires on **movement** — a train dwelling in a siding/turnback (Maidenhead
turnback siding, Crossrail stabling, Henley branch P5 bay) can sit for 15–30+ minutes with no
new message, and was wrongly vanishing from the map well before it actually left. `/api/td-live`'s
window widened from 600 s to 1800 s; the **client** (`fetchTd()` in lineside.html) applies that
full 1800 s only to siding/branch cells (`BERTH_CELL[berth].key === 'br' || 'st'`) and keeps the
tighter 420 s for ordinary running-line berths, where a reading that old really is more likely
stale than dwelling.

#### Headcode supersession (added 2026-07-07)

NR's TD protocol has a **CC** message (berth interpose) separate from the CA step we already
handled — it relabels a berth's descriptor directly, with no `from` berth, used when a
reversing train gets a new headcode for its next working while still parked (confirmed live:
5N41 → 9U41 in place at Maidenhead Platform 4). `_handle_td` now processes CC the same way as
CA (added to `_td_buffer` with `from:''`) and additionally tracks `_td_berth_occupant`; when a
CC's new descriptor differs from the previous occupant, the old headcode is recorded in
`_td_superseded[hc] = ts`. `/api/td-live` skips a headcode's sighting if it's no newer than its
own supersession timestamp — so the old code disappears **immediately** instead of fading out
over the next few minutes, while a genuine later reuse of the same headcode (`ts` after the
supersession point) still shows normally.

`_td_berth_occupant` is keyed by **physical location** `(area, stanme, platform)`, NOT the raw
berth code — first version used `(area, berth)` and failed on exactly the case it was built
for: the Maidenhead turnback siding's arrival step (R578, a real CA move) and its CC relabel
(0578) are different berth codes for the *same* physical track, so the relabel's lookup never
found the arrival's entry (confirmed live: 5N50→5N51 across R578→0578 was NOT superseded,
while 5N51→9U51 at the literal same berth 0574 DID work — proving the berth-code key was the
gap). Platform is included alongside stanme (not stanme alone) because Maidenhead's platforms
1-5 all share STANME "MAIDENHED" — stanme-only would cross-supersede unrelated platforms.

TD-only sightings at Maidenhead (no RTT/CIF identity — corridor synthesis deliberately skips
trains sitting at Maidenhead/Reading, they might terminate/reverse) used to render grey/unknown
in lineside.html even though they're virtually always Elizabeth Line. `opInfo()` now has a
`MAID_PLACES` fallback (STANME ∈ {MAIDENHED, MDNHDMIDS, MDNHD CS} → force EL purple) — scoped
to location rather than a headcode-prefix guess that could misfire elsewhere on the corridor.

#### Reading box labels (added 2026-07-07)

Each headcode chip in the Reading box now has a label below it: 3-letter destination
(`destAbbr(t.dest)`, needs an /api/trains identity match) + current platform. Platform data
was being parsed from SMART (`PLATFORM` field) but discarded before reaching the API — now
threaded through `_load_smart()` → `_berth_info()` → `/api/td-live`'s `platform` field. Rows
grew from 18px to 24px tall to fit the label, so the box shows 8 chips (was 10) before
"+N more".

### Buses View

Two tabs: **Departures** and **Map**. The Buses tile opens on Departures.

**Routes tracked:** 850 (High Wycombe–Reading via Marlow/Twyford), 127, 127S, 128, 129
(Wokingham–High Wycombe), 12 (Reading–Twyford), 227 (Twyford Station Forecourt).
Operators: Carousel (CSLB), Thames Valley Buses (CTNY), Reading Buses (RBUS).

**Departures tab:**

Calls `/api/bods/departures?stop=ATCO` → Passenger platform departure board scrape.
Default stop: `035091060001` (Waggon and Horses). Shows next 10 departures with route,
destination, scheduled time, expected time, and minutes late. Settings (⚙) to change stop.

**Map tab:**

Leaflet.js map centred on Twyford (lat=51.4741, lon=-0.8647), default zoom 12.

*Vehicle markers* — from `/api/buses/vehicles` every 30 s (BODS SIRI-VM). Direction arrow
(from `bearing` field) and route-coloured border.

*Stop markers* — from `/api/buses/stops` (OSM/Overpass, file-cached on Pi). All stops visible
at all zoom levels (no zoom gate — resistive screen means no pinch-to-zoom). Tapping a stop
shows a popup with stop name and green route-number badges.

*Route timetable data* — fetched progressively via `/api/buses/route-stops`. Each call
fetches whichever routes haven't been cached yet. When complete, stop markers show
timetable-authoritative route badges.

---

## Local Python Servers

### On the Pi

#### transport-proxy.py — port 5001 (0.0.0.0)

ThreadingMixIn (concurrent requests). Serves `dashboard.html` and `icons/` as static files in
addition to API endpoints. All responses include `Access-Control-Allow-Origin: *`.

Uses only Python stdlib (no flask). The Hive endpoints use the stdlib `urllib.request` for
HTTPS; `hive-setup.py` is the only file that uses the `requests` package.

**Endpoints:**

| Endpoint | Upstream | Proxy TTL | Notes |
|----------|----------|-----------|-------|
| `GET /api/departures?station=CRS&rows=N[&platform=P]` | National Rail SOAP ldb12.asmx | 90 s | |
| `GET /api/flights?lat=…&lon=…&dist=…[&focus=1]` | adsb.lol `/v2/lat/{}/lon/{}/dist/{}` | 60 s (20 s with `focus=1`) | lat/lon rounded to 2 dp, dist snapped to 10 nm; `focus=1` sent by aircraft.html focus mode for faster refresh |
| `GET /api/bods/departures?stop=ATCO` | Passenger platform scrape (parallel per operator) | 30 s | |
| `GET /api/bods/buses` | BODS SIRI-VM (all operators in parallel) | 30 s | Full bus list |
| `GET /api/buses/vehicles` | BODS (filtered to tracked routes, GeoJSON) | 30 s | |
| `GET /api/buses/stops` | Overpass API (first run only) | 4 h in-memory; file indefinite | Merges timetable route data at serve time |
| `GET /api/buses/route-stops` | Transport API timetable (progressive) | file permanent | Fetches missing routes; stops on rate-limit |
| `GET /api/hive` | Hive Beekeeper API (via Cognito tokens) | 300 s | Auto-refreshes tokens |
| `GET /api/flight-route?cs=CALLSIGN` | FlightAware HTML scrape | 4 h | Route, times, aircraft type |
| `GET /api/airline-logo?iata=XX` | pics.avs.io (file-cached) | File permanent | |
| `GET /api/aircraft-info?hex=XXXXXX` | OpenSky metadata (file-cached) | 30 days (mtime check) | |
| `GET /api/airport-name?iata=XXX` | `airport-names.json` (OurAirports CSV, downloaded once) | In-memory for life of process | Returns `{"name": "…"}` or `{"name": null}` |
| `GET /api/trains` | RTT API (Twyford + Reading, 2 calls) + NR STOMP/CIF + SMART/CA berth model | 30 s | Confirmed stops + bi-directional Reading prediction + corridor filter + NR freight; live-berth ETA refinement |
| `GET /api/radio/resolve?url=…` | PLS/M3U playlist fetch | 30 s | Returns direct stream URL |
| `GET /api/radio/nowplaying?url=…` | ICY stream metadata | 25 s | StreamTitle from ICY |
| `GET /api/radio/nowplaying-rp?chan=N` | Radio Paradise API | 20 s | |
| `GET /api/radio/track-info?artist=…&title=…` | Last.fm API | 3600 s | Bio, album, listeners, artwork, tags, similar artists |
| `GET /health` | — | — | Returns `ok` |
| `GET /aircraft` | Static — aircraft.html | — | Standalone full-screen aircraft SPA |
| `GET /trains` | Static — trains.html | — | Standalone full-screen trains SPA |
| `GET /lineside` | Static — lineside.html | — | Standalone visual track display SPA |
| `GET /api/td-live` | In-memory TD/SF state | — | Live berth positions (tagged with SMART/CA `line`, `place`, `dist_mi`, `platform`) + signal aspects from NR TD feed. No TTL. Positions expire after 30 min of inactivity (widened 2026-07-07 from 10 min — a siding/turnback dwell can legitimately outlast that with no new CA step). A headcode superseded by a CC berth interpose (see message-type table above) disappears immediately rather than waiting to expire. |
| `GET /api/nrcc` | Darwin SOAP nrccMessages | 300 s | NRCC disruption messages for Twyford area. Extracted from existing Darwin SOAP response (`{*}nrccMessages/{*}message`). Returns `{messages:["…"], ts}`. |
| `GET /api/calibrate?line=&headcode=&dest=&predicted_ts=&sched_ts=&sighted=&td_berth=&dist_mi=&confirmed=` | Appends to `calibration_log.jsonl` | — | Logs one lineside.html "heard it pass" button press (line/direction, server-timestamped). See lineside.html § Calibration. |
| `GET /api/calibration` | `calibration_log.jsonl` | — | Recent calibration presses + per-line `{n, mean_s, stdev_s}` + `applied` (median offsets currently being added to `house_pass_ts`, once a line has ≥4 samples) + `min_n`. |
| `GET /` or `GET /icons/…` etc. | Static file from APP_DIR | — | dashboard.html, icons, hls.min.js |

**National Rail SOAP details:**
- Token: `32cf81aa-5b5f-4195-8a02-6dc47bc20ce5`
- Namespace: `xmlns:ldb="http://thalesgroup.com/RTTI/2021-11-01/ldb/"`
- SOAPAction: `"http://thalesgroup.com/RTTI/2015-05-14/ldb/GetDepBoardWithDetails"` (quotes required)
- Request extra rows when platform-filtering: `fetch = limit + 8` to compensate for filtered rows

**Bus vehicle fetch:** 3 parallel threads, one per operator. URL-encodes `lines[N]=OP:ROUTE`
params manually (no urllib.parse.urlencode — preserves exact format expected by Passenger platform).

**Bus stop filtering (`_stop_has_tracked_route`):** Splits OSM `route_ref` tag on `[;,\s]+` and
checks for exact token membership in `TRACKED_ROUTES`. Prevents false positives from
`route_ref~"12"` matching routes 112, 126, etc.

**Bus stops serve-time merge:** `_bus_stops` always merges timetable data from `bus-route-stops.json`
at serve time (not just on first Overpass fetch). Route badges improve automatically as timetable
data fills in, without regenerating the stops file.

**Overpass query:** fetches stops via (1) nodes with `route_ref` tags in bbox `(51.38,-1.00,51.65,-0.70)`,
and (2) node members of route relations for routes 850/127/128/129. Route 12 has no OSM relation;
the `route_ref` tag strategy covers it.

#### cast-server.py — port 9998 (127.0.0.1)

**Must use `ThreadingHTTPServer`** — `/cast/discover` blocks for ~5 s (mDNS scan).

- `GET /cast/discover` → pychromecast/zeroconf mDNS scan → `{"devices": ["name", …]}`
- `POST /cast/play {"device": "name", "url": "…", "title": "…"}` → streams to Chromecast
- `POST /cast/stop {"device": "name"}` → stops cast
- `POST /cast/volume {"device": "name", "delta": ±0.1}` → adjust volume

pychromecast installed via pip on the Pi. See PI-SETUP.md.

**`_active_cc` pattern:** `cast_play()` saves the specific Chromecast object used as `_active_cc`.
`cast_stop()` uses `_active_cc` (not re-discovering by name) so stop() works even if a
subsequent discover has created new objects.

### On the Joggler

#### shutdown-server.py — port 9999 (127.0.0.1)

`GET /shutdown` → `sudo systemctl poweroff`. Bound to 127.0.0.1 only — reachable only from
the Joggler itself. `dashboard.html` calls `http://localhost:9999/shutdown` from the power button;
`localhost` resolves to the Joggler from the Joggler's browser, so it silently fails from any
other device.

---

## External APIs and Rate Limiting

> **Railway data sources** (RTT, Network Rail STOMP + SMART/CIF, Darwin, Vail BPLAN) — their
> credentials, what each provides, how to call them, and the gotchas — are documented in
> **`RAILWAY-APIS.md`**. Read that first when working on train data.

### Complete API inventory

| API | Called from | When | Interval | Daily calls | Hard limit |
|-----|-------------|------|----------|-------------|-----------|
| Open-Meteo weather | Browser | Always | 10 min | ~144 | None |
| Open-Meteo AQI | Browser | Always | 30 min | ~48 | None |
| Marlow FM now-playing SSE | Browser | Always | Persistent SSE | 0 polls | None |
| Marlow FM show SSE | Browser | Radio view | Persistent SSE | 0 polls | None |
| WagtailCam thumbnail | Browser | Home screen | 5 min (3 calls) | ~864 | None |
| WagtailCam MJPEG | Browser | WagtailCam view | Reconnect/5 min | Low | None |
| National Rail SOAP | Proxy | Tile (120 s) + trains view | 90 s TTL | ~720 + view | Fair use |
| RTT API (Twyford + Reading) | Proxy | trains.html (30 s) | 30 s TTL | ~5,760 (2×/30 s) | 9,000/day |
| NR TRUST STOMP (TRAIN_MVT_ALL_TOC) | Proxy | Persistent TCP stream | — (push) | — (push) | None (up to 600 msg/min) |
| ADS-B LOL | Proxy | Tile (60 s) + flights view | 60 s TTL | ~720 + view | Generous |
| BODS SIRI-VM | Proxy | Bus map tab | 30 s | Low | None |
| Passenger platform scrape | Proxy | Bus departures tab | 30 s TTL | ~2,880 | None |
| Transport API timetable | Proxy | Map tab (progressive) | Once per route | 12 total | **1,000/day** |
| Overpass API | Proxy | Once ever | File-cached | ~0 | Fair use |
| adsbdb.com routes | Browser | Flights view, per callsign | Once per callsign | Low | None |
| FlightAware scrape | Proxy | Flights view, per callsign | 4 h TTL | Low | Fair use |
| Last.fm API | Proxy | Radio, on track change | 3600 s TTL | Low | Fair use |
| OpenSky aircraft metadata | Proxy | Once per hex code | File-cached | ~0 | Fair use |
| Hive Beekeeper API | Proxy | Weather view (5 min) | 300 s TTL | ~288 | None |

**Transport API quota management:** 12 timetable route calls are cached permanently after first
fetch. The `BUS_DEP_TTL` constant (currently unused for BODS; kept for the legacy Transport API
departures endpoint) must not drop below 300 s without recalculating daily budget.

**Tile timer pause:** All home screen tile `setInterval` timers are cleared when any view opens,
and restarted on return to home. This avoids wasting quota on invisible tile data.

---

## Real Time Trains (RTT) API

### Account

- **Portal / account management:** https://api-portal.rtt.io (RTT unified login account)
- **Account:** graham.duthie@gmail.com
- **Credentials file (Mac, gitignored):** `nr-credentials.env` in this repository root
  (key: `RTT_REFRESH_TOKEN`)
- **Pi `.env`:** `RTT_REFRESH_TOKEN=…` — read by `transport-proxy.py` at startup

### Authentication flow

RTT uses a two-token scheme:

1. **Refresh token** — long-lived JWT issued by `api-portal.rtt.io` when you sign up. Stored
   in `nr-credentials.env` (Mac) and in the Pi's `.env`. Does not expire on its own but will
   be revoked if placed in a public/client-side application.

2. **Access token** — short-lived (~20 min). Obtained by calling:
   ```
   GET https://data.rtt.io/api/get_access_token
   Authorization: Bearer <refresh_token>
   ```
   Response: `{"token": "…", "validUntil": "2026-06-25T14:32:00Z", "entitlements": […]}`

   The proxy (`transport-proxy.py`) does this exchange automatically, caches the access token
   in memory, and refreshes it 60 s before `validUntil` without blocking in-flight requests.

All subsequent API calls use the access token:
```
Authorization: Bearer <access_token>
```

### API endpoints used

Base URL: `https://data.rtt.io`

| Endpoint | Used for |
|----------|----------|
| `GET /api/get_access_token` | Exchange refresh token for access token |
| `GET /gb-nr/location?code=TWYFORD&from=HHmm&to=HHmm` | Twyford location lineup (stopping/passing trains) |
| `GET /gb-nr/location?code=RDG&from=HHmm&to=HHmm` | Reading location lineup (Main Line estimation) |

The `from`/`to` parameters are local times in `HHmm` format defining a window. The proxy
uses a 100-minute window centred on now (−30 min, +70 min) to capture recent past and upcoming.

### Rate limits

| Dimension | Limit |
|-----------|-------|
| Per minute | 30 requests |
| Per hour | 750 requests |
| Per day | 9,000 requests |

Current usage: 2 requests per 30-second poll = 5,760/day (comfortably within limits).

### What RTT returns (and what it misses)

RTT Twyford query returns trains that have **Twyford as a WTT timing point** — i.e. trains
that stop or are scheduled to pass through at a recorded time. This covers:

- ✅ Elizabeth Line (XR) — stop at Twyford (Relief Line)
- ✅ GWR local services (GW) — stop at Twyford (Relief Line)
- ❌ Fast GWR inter-city IETs (Bristol, Cardiff, Swansea, Penzance, etc.) — Twyford is not
  a WTT timing point for these; they never appear in the Twyford lineup
- ❌ Freight trains — also absent from the Twyford lineup

**Workaround for fast trains:** Query Reading (`RDG`) for Main Line trains and apply a time
offset to estimate Twyford pass time (Down Main −4 min, Up Main +3 min). Adds ~26 extra
trains per 2-hour window. See trains.html section for full detail.

**No workaround for freight:** Freight does not reliably stop at Reading either. Full freight
coverage requires the Network Rail TRUST/Train Movements feed — see NR section below.

### Key field reference

`locationMetadata.line.planned` — line code at that location:
- `RL` — Relief Line
- `ML` — Main Line (Down)
- `UML` — Up Main Line
- `DML` — Down Main Line (sometimes used instead of `ML`)

`displayAs` — `CALL` (stops) or `PASS` (passes without stopping)

---

## Network Rail Open Data Feeds

### Account

- **Portal:** https://publicdatafeeds.networkrail.co.uk
- **Account:** graham.duthie@gmail.com
- **Credentials file (Mac, gitignored):** `nr-credentials.env` in this repository root
- **Account state:** Active as of 2026-06-25 (verified by successful STOMP connection)
- **No separate API key** — the website login email and password are used directly as the
  STOMP username and password. There is nothing else to apply for.
- **No website "subscribe" step needed** — STOMP topic subscriptions are made in code at
  connection time. The website UI has a subscription management page but it does not gate
  access; messages flow as soon as you subscribe via STOMP.

### STOMP connection details

| Parameter | Value |
|-----------|-------|
| Hostname | `publicdatafeeds.networkrail.co.uk` |
| Port | `61618` (SSL/TLS) |
| Username | NR account email |
| Password | NR account password |
| Protocol | STOMP 1.1 |
| Heartbeat | Recommended: `(10000, 10000)` ms |
| Client ID | Set `client-id` header to your email (required for durable subscriptions) |

Subscribe to topics as `/topic/<topic-name>`. For durable subscriptions (messages queued for
up to 5 minutes while disconnected) also set the `activemq.subscriptionName` header to a
stable unique string.

**Python example (stomp.py library):**
```python
import stomp

conn = stomp.Connection(
    host_and_ports=[('publicdatafeeds.networkrail.co.uk', 61618)],
    heartbeats=(10000, 10000)
)
conn.connect(
    username='graham.duthie@gmail.com',
    passcode='<password from nr-credentials.env>',
    wait=True,
    headers={'client-id': 'graham.duthie@gmail.com'}
)
conn.subscribe(
    destination='/topic/TRAIN_MVT_ALL_TOC',
    id='1',
    ack='auto',
    headers={'activemq.subscriptionName': 'graham.duthie@gmail.com-mvt'}
)
```

Messages arrive as JSON batches (an array of objects). Each batch typically contains 1–20
movement records. Messages are NOT gzip-compressed on this platform (unlike the National Rail
Enquiries Darwin feed, which is gzip-compressed).

### Available feeds

| Topic | Feed | Rate | Description |
|-------|------|------|-------------|
| `TRAIN_MVT_ALL_TOC` | Train Movements | Up to 600/min | TRUST system — every train passing or calling at a timing point. Includes freight and non-stopping trains. Messages are batched. **The most useful feed for trains.html.** |
| `TD_ALL_SIG_AREA` | Train Describer (TD) | ~6000/min | All signal areas combined — the **only available TD topic**. Area-specific topics (e.g. `TD_WTV_SIG_AREA` for Western Thames Valley) were deprecated years ago and no longer exist on the broker. Filter client-side. We filter to areas D1/D4/D6 in `_handle_td()` before buffering, reducing effective volume ~50×. Message types: **CA** (berth step — train moved from→to), **CB** (berth cancel, still ignored), **CC** (berth interpose — descriptor set/changed WITHOUT movement, e.g. a reversing train relabelled to its next working's headcode while still parked; processed since 2026-07-07, see "Headcode supersession" under lineside.html), **CT** (heartbeat, ignore), **SF** (signal flag — aspect change), **SG/SH** (signal flag variants, ignored). |
| `VSTP_ALL` | VSTP | Low volume | Very Short Term Planning — late-notice schedule additions not in the daily SCHEDULE feed. |
| `RTPPM_ALL` | RTPPM | 1/min | Aggregate performance metrics. Not useful for per-train display. |
| `TSR_ALL_ROUTE` | TSR | ~11/week | Temporary speed restrictions from the Weekly Operating Notice. |

Static feeds (authenticated HTTP GET, not STOMP):

| Feed | URL pattern | Description |
|------|-------------|-------------|
| SCHEDULE (CIF) | `https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY` | Full working timetable, updated daily ~01:00 UTC. Large file (~200 MB compressed). |
| SCHEDULE (JSON) | `https://publicdatafeeds.networkrail.co.uk/ntrod/inspire/feeds/scheduled_feeds/...` | JSON equivalent. |
| Reference Data | Via the portal download pages | TOC codes, STANOX→TIPLOC mapping, etc. |

### Train Movements message format

Each STOMP message body is a JSON array of objects, each with `header` and `body`:

```json
[
  {
    "header": {
      "msg_type": "0003",
      "msg_queue_timestamp": "1782379703000",
      "source_system_id": "TRUST",
      "original_data_source": "SMART"
    },
    "body": {
      "train_id":             "871C14MD25",   // TRUST train ID (headcode + date encoded)
      "actual_timestamp":     "1782383280000", // Unix ms — actual time at this location
      "timetable_variation":  "0",            // minutes late (negative = early)
      "direction_ind":        "UP",           // UP or DOWN
      "event_type":           "DEPARTURE",    // ARRIVAL, DEPARTURE, or DESTINATION
      "loc_stanox":           "87014",        // STANOX of the location
      "planned_timestamp":    "1782383280000",
      "planned_event_type":   "DEPARTURE",
      "platform":             " 4",
      "variation_status":     "ON TIME",      // ON TIME, EARLY, LATE, OFF ROUTE
      "train_terminated":     "false",
      "offroute_ind":         "false",
      "auto_expected":        "true"
    }
  }
]
```

**`train_id` format (10 characters):** `PPHHHHSSSS` where PP = 2-char schedule prefix
(numeric), HHHH = 4-char headcode (e.g. `1G21`), SSSS = 4-char date suffix. Extract the
headcode with `train_id[2:6]` — **not** `[:4]` which would return the prefix + first two
headcode chars, producing bogus headcodes starting with `7`/`8`.

**msg_type values:**
- `0001` — Train Activation (train ID assigned to a schedule)
- `0002` — Train Cancellation
- `0003` — Train Movement (passing/calling a timing point) ← the useful one
- `0004` — Unidentified train
- `0005` — Train Reinstatement
- `0006` — Change of Origin
- `0007` — Change of Identity
- `0008` — Change of Location

### Twyford location codes

| Code type | Code | Notes |
|-----------|------|-------|
| STANOX | `87014` | Twyford station — fires in TRUST for **stopping** trains only |
| STANOX | `74023` | Twyford (alternate STANOX) — fires for both stopping AND passing trains including freight (2101 freight schedules in CIF_FREIGHT_FULL_DAILY pass Twyford at STANOX 74023) |
| TIPLOC | `TWYFORD` | Used in RTT API and CIF schedules; maps to STANOX 74023 |
| CRS | `TWY` | 3-letter public station code |
| STANOX (signal berths) | `TWYF112`, `TWYF632`, `TWYFDW` | TD berth points — appear in TD feed but NOT in WTT schedules; RTT returns zero services for these |
| STANOX (Maidenhead) | `74005` | ~4.5 miles east of Twyford on Main Line. **Has ZERO freight WTT timing points in CIF** — freight does not fire here. Elizabeth Line stopping trains fire here and are already in RTT data. |

**TD signal area geography (confirmed by observation 2026-06-25):**

All three areas are part of Thames Valley Signalling Centre (TVSC). The former area-specific STOMP topic was `TD_WTV_SIG_AREA` (Western Thames Valley) — now deprecated; all data comes via `TD_ALL_SIG_AREA`.

| TD area_id | TVSC panel name | Geography | Confirmed trains |
|------------|-----------------|-----------|-----------------|
| `D4` | Hayes Area Scalable IECC | East of Twyford → Maidenhead (DOWN trains approach from here) | 1P36 (up Main) entered D4 at berth 0470 heading east; D4 berths 400-699 used for DOWN ETA |
| `D6` | Maidenhead Area Scalable IECC | Twyford corridor — **Relief Line both directions + Up Main** | 9R78, 9R26, 9U35 (Relief); 1A31, 1P36 (up Main) |
| `D1` | Reading IECC A | Reading area west of Twyford (UP trains approach from here); D1 1600+ berths remap to x=330-476 | 1G29 (down Main) was in D1 throughout its Twyford arrival; D1 1600+ used for UP ETA |

**D6 berth geography** (berths increase going west/toward Reading on Relief Line):
- Down Relief at Twyford station: ~0577→0595
- House (crossover between Up and Down Relief lines, ~200 m east of station): ~0x0565–0x0577 (down), ~0x0548–0x0565 (up)
- House-zone watch range: D6 berths **0x0540–0x0600**
- D6→D4 panel boundary (heading east/toward Maidenhead): berth ~0476/0470

**TRUST at Twyford:** STANOX 87014 fires for stopping trains only. STANOX 74023 fires for
ALL trains that have Twyford as a WTT timing point — including freight (confirmed: 157 freight
services active on a typical day in CIF_FREIGHT_FULL_DAILY, all with `pass` at TWYFORD TIPLOC).

**Maidenhead STANOX 74005 is NOT useful for freight.** Despite earlier assumption, CIF
analysis shows freight has zero WTT timing points at Maidenhead. TRUST fires at 74005 for
Elizabeth Line passenger trains (which are already in RTT data), not freight. The proxy
currently watches 74005 but any freight captured there is a false positive.

**Twyford STANOX 74023 is the correct STANOX for freight** — not yet switched in proxy (as
of 2026-06-25); 87014 (stops only) is still the primary code in use.

To filter the full TRAIN_MVT_ALL_TOC stream for Twyford stopping trains:
`body.loc_stanox == "87014"`
For all Twyford timing points (including freight pass-through):
`body.loc_stanox == "74023"`

### Account states

An NROD account can be in one of three states:

- **Pending** — registered but waiting for capacity allocation. Can use the portal UI but
  cannot connect via STOMP. The system sends an email when activated.
- **Active** — fully functional; can connect via STOMP and receive all subscribed feeds.
- **Inactive** — account dormant for ≥30 days; resources deallocated. Log into the portal
  and click "Add to Pending state" to re-queue for activation (~1 hour if capacity available).

Graham's account was confirmed **Active** on 2026-06-25.

### Status page

https://nrodcaci.grafana.net/public-dashboards/960fa54d94884dc7abd1f5ab9c70df7e

### Support

Email: dsg_nrod.support@caci.co.uk (the feeds are operated by CACI on behalf of Network Rail)

---

## Persistent Data Files

These files live on the Pi and survive reboots. Deleting them forces a fresh fetch.

### `/home/gduthie/twyford-dashboard/bus-stops.json`

Bus stop locations for tracked routes (850, 127, 128, 129, 12) in the bbox covering High
Wycombe–Reading. Created by the proxy on first call to `/api/buses/stops` using Overpass API.
Never re-fetched unless deleted. Contains ~62 stops.

Structure: `{"stops": [{"lat": …, "lon": …, "name": "…", "atco": "…", "routes": ["850","12"]}, …]}`

**To regenerate:** `ssh gduthie@172.16.10.136 'rm /home/gduthie/twyford-dashboard/bus-stops.json'`
then trigger `/api/buses/stops` by opening the bus map.

### `/home/gduthie/twyford-dashboard/bus-route-stops.json`

Transport API timetable data mapping each route/direction to its list of ATCO stop codes. Built
progressively — each call to `/api/buses/route-stops` fetches whichever of the 12 combinations
haven't been fetched yet, stopping on rate-limit. When complete, the proxy merges this data into
the stops response so each stop's `routes` array is authoritative.

Structure: `{"routes": [{"op": "CSLB", "route": "850", "direction": "outbound", "atcos": […]}, …]}`

### `/home/gduthie/twyford-dashboard/hive-tokens.json`

Hive/Cognito auth tokens written by `hive-setup.py`. Mode 600. Auto-refreshed in-memory when
`token_expiry` is reached; auto-reauth when refresh token expires (requires `hive-credentials.json`).

Structure:
```json
{
  "pool_id":      "eu-west-1_SamNfoWtf",
  "client_id":    "3rl4i0ajrmtdm8sbre54p9dvd9",
  "region":       "eu-west-1",
  "IdToken":      "…",
  "AccessToken":  "…",
  "RefreshToken": "…",
  "token_expiry": 1234567890.0,
  "home_id":      "39f22388-29d3-4e81-a940-3812fc7272bc"
}
```

`home_id` is the Hive home that contains the heating devices. On Graham's account the devices
live under the "Luke" home; his own "Home" has no devices. `hive-setup.py` auto-discovers and
saves `home_id`.

**To regenerate:** Run `hive-setup.py` on the Pi (see Deployment below).

### `/home/gduthie/twyford-dashboard/hive-credentials.json`

Hive account email and password for automatic re-authentication when the refresh token expires.
Written by `hive-setup.py --save-credentials`. Mode 600.

Structure: `{"username": "…@….com", "password": "…"}`

---

## Deployment

```bash
# From /Users/gduthie/Programming/Joggler/ on Mac

# Deploy dashboard.html to Pi and hard-reload Joggler (most common)
scp dashboard.html gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh -i ~/.ssh/id_ed25519 of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'
# Use ctrl+shift+r (hard reload), NOT F5 — F5 may serve cached CSS

# Deploy and restart transport-proxy on Pi (systemd service)
scp transport-proxy.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh gduthie@172.16.10.136 'sudo systemctl restart twyford-dashboard'

# Deploy lineside.html (no service restart needed — served as static file)
scp lineside.html gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/

# Deploy and restart cast-server on Pi
scp cast-server.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh gduthie@172.16.10.136 \
    'kill $(pgrep -f cast-server) 2>/dev/null; \
     nohup python3 /home/gduthie/twyford-dashboard/cast-server.py \
       >> /home/gduthie/twyford-dashboard/dashboard.log 2>&1 & disown; echo started'

# Verify proxy health
ssh gduthie@172.16.10.136 'curl -s http://127.0.0.1:5001/health'

# First-time Hive auth setup on Pi (run once; saves tokens + credentials)
scp hive-setup.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh -t gduthie@172.16.10.136 \
    'python3 /home/gduthie/twyford-dashboard/hive-setup.py --save-credentials'

# Trigger bus route-stops progressive fetch
ssh gduthie@172.16.10.136 \
  'curl -s http://127.0.0.1:5001/api/buses/route-stops | python3 -m json.tool'

# Deploy icons to Pi
scp -r icons/ gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/
```

---

## Key Technical Gotchas

### CSS / Layout
- **`min-height: 0` on `.app-tile`** — without it, CSS grid 1fr rows grow to fit minimum content
  size and the bottom row overflows off-screen
- **SVG `height: auto` unreliable on Chromium Linux** — always set explicit px height on SVGs
- **`#wx-sun-arc` must have `height: 70px` explicit** or it renders at 150px
- **CSS `filter:` is CPU-rendered** — Chromium runs with `--disable-gpu`. `filter: drop-shadow`,
  `filter: brightness`, etc. all fall back to software rendering on the Atom Z520. Avoid on
  images or frequently-repainted elements
- **`text-align: center` unreliable on flex items in Chromium 148** — use
  `display: flex; justify-content: center` instead
- **`#view-radio { position: relative }` must NOT exist** — this rule was removed because it
  overrides `.view { position: absolute; inset: 0 }` (ID specificity 1,0,0 beats class 0,1,0),
  making the radio view content-sized rather than filling the screen. `#station-btn` and
  `#cast-btn` are positioned correctly as long as `#view-radio.active { position: absolute }`
  is in effect (which it is via `.view` and the explicit `#view-radio.active` rule)
- **Responsive CSS specificity:** `html.profile-X .class` = (0,2,1), `#id` = (1,0,0),
  `html.profile-X #id` = (1,1,0). General profile rules (e.g. `html.profile-phone-portrait
  img.tile-icon { height: 48px }`) can override element-specific rules if they have higher
  specificity — always check both when debugging unexpected overrides
- **Weather contrast** — the clear daytime background is bright blue (`#1976d2`). All SVG strokes
  and stat text must use ≥35% white opacity. Rain % text uses `#c2e4f7` (pale ice-blue)

### API / Data
- **UKMO model breaks rain %** — `&models=ukmo_seamless` causes Open-Meteo to return no
  `precipitation_probability`. Always use default model
- **`past_days=1` shifts both arrays** — `hourly[0]` = yesterday 00:00, `daily[0]` = yesterday.
  Every access to "today" must use `hourly[24+H]` and `daily[1]`. WEEK tab loops from `i=1`
- **Marlow FM stream is HTTP only** — server has no HTTPS; dashboard must use `http://`
- **Bauer PLS streams** — must call `/api/radio/resolve` each time to get a fresh `skey` token.
  Never cache or hardcode the resolved stream URL
- **Magic Classical Bauer station key is `scala-mp3`** (not `magicclassical-mp3`)
- **Route cache TTL is 6 hours** — low-cost carriers (Ryanair, easyJet, Wizz Air etc.) reuse
  flight numbers daily on completely different routes. A longer TTL shows stale/wrong destinations.
  To clear immediately: `localStorage.removeItem('flightRouteCache')` in browser console
- **Twyford coordinates** — lat=51.4741, lon=**-0.8647** (not -0.9752 which is Reading/Caversham)
- **OSM bus route data quality** — routes 127/128/129 relations have geometry only (no node
  members); route 12 has no OSM relation. The `route_ref` tag on individual stop nodes is
  more reliable for stop discovery
- **IATA vs ICAO:** ICAO codes are 3-letter (BAW, EZY), IATA are 2-letter (BA, U2). The
  AIRLINES dict in dashboard.html is keyed by ICAO

### Timezone handling in transport-proxy.py
- **RTT times are local BST, not UTC.** RTT returns schedule times as naive ISO strings without
  timezone suffix (e.g. `2026-06-25T14:04:00`). These are local Europe/London time (BST in
  summer, GMT in winter). Treating them as UTC introduces a 1-hour error in BST.
- **`_iso_to_ts(iso)`** converts any ISO string to a UTC Unix timestamp. Naive strings are
  explicitly tagged as `ZoneInfo('Europe/London')` — handles BST/GMT boundary automatically
  via the Python stdlib. Timezone-aware strings (TRUST buffer uses `+00:00` UTC, BODS uses UTC)
  are passed through unchanged.
- **TRUST `actual_timestamp` is NOT UTC** (corrected 2026-07-06): it is London LOCAL wall-clock
  time encoded as epoch milliseconds *as if* it were UTC (the well-known NROD quirk). During BST,
  treating it as UTC puts every movement an hour in the future. The proxy decodes the wall-clock
  and re-tags it `Europe/London` before storing an ISO string in the buffer.
- **TD `time` (CA/SF messages) IS true UTC milliseconds** — the two feeds differ; don't mix them up.
- **CIF times** (e.g. `1638` = 16:38) are local London time; `_hhmm_to_ts` tags them
  `Europe/London` when building freight schedule timestamps.
- **`ZoneInfo` import**: `from zoneinfo import ZoneInfo` and `_TZ_LONDON = ZoneInfo('Europe/London')`
  at module top; requires Python 3.9+ (Pi has 3.13 — fine).

### Python / Server
- **`stomp.py` must be installed system-wide for the proxy** — the proxy runs as root (`sudo bash -c 'nohup python3 …'`). A user-level install (`pip3 install --user`) is not visible to root and the import silently falls back to `_HAS_STOMP = False`, disabling freight. Install with `sudo pip3 install stomp.py --break-system-packages`
- **`python3 -u` required** — without `-u`, Python block-buffers stdout when output is redirected to a file. Startup messages (NR STOMP connected, airport names loaded) never appear in the log until the buffer is flushed. Always start with `python3 -u transport-proxy.py`
- **NR STOMP auto-reconnects** — on disconnect, `_NRListener.on_disconnected()` starts a background thread (`_nr_stomp_reconnect`) with exponential backoff starting at 10 s, capped at 5 min
- **`python3-pip` not in apt on Debian Trixie** — use get-pip.py on the Joggler if needed
- **`python3-venv` not in apt on Debian Trixie** — use `pip install --user --break-system-packages`
- **`pkill` returns exit code 255 on Trixie even on success** — use `pgrep` + `kill` instead
- **`cast-server.py` MUST use `ThreadingHTTPServer`** — `/cast/discover` blocks ~5 s for mDNS
- **`Access-Control-Allow-Private-Network: true` in cast-server CORS headers** — required when
  a browser on the LAN (page served from a LAN IP) fetches cast-server on localhost. Without
  this header the preflight OPTIONS request fails
- **sshd must have `OOMScoreAdjust=-1000`** to survive Chromium memory pressure on the Joggler

### Hive / Cognito
- **Cognito `X-Amz-Target` prefix is `AWSCognitoIdentityProviderService`** — not
  `AmazonCognitoIdentityProviderService` (using the latter returns `UnknownOperationException`)
- **Graham's devices are under the "Luke" home** (`home_id` in hive-tokens.json) — his own
  "Home" has no devices. The proxy always passes `&homeId=` from the token file
- **`home_id` must be in the token file** — without it the API returns 0 products; `hive-setup.py`
  discovers and saves it automatically
- **Filter products by `type == "heating"`** — the hot water product has no temperature field
- **Auth header is bare IdToken** — no "Bearer" prefix; format: `authorization: {IdToken}`
- **Token auto-refresh** — proxy uses `REFRESH_TOKEN_AUTH` flow; no 2FA needed for refresh
- **Auto-reauth on refresh failure** — proxy calls `hive-setup.py --credentials-file …` as a
  subprocess to perform full SRP re-authentication. Requires `hive-credentials.json` to exist

### WiFi
Config at `/boot/network.yaml` (EFI partition). `of-netplan` service copies to `/etc/netplan/`
on every boot. Always edit `/boot/network.yaml`, never `/etc/netplan/`.

---

## Compatibility Notes

### Current (Chromium 148, Debian Trixie) — no issues
HTTPS, ES6, CSS gap, flexbox, Leaflet.js, SSE — all fine.

### Old Ubuntu 14.04 USB (Chromium 53) — backup only

| Issue | Fix |
|-------|-----|
| No `String.padStart()` | `function pad(n) { return n < 10 ? '0'+n : ''+n; }` |
| Old CA bundle | HTTP URLs only |
| No CSS `gap` | Use `margin-right` |
| Missing Unicode glyphs | Use PNG icons |
| `<img>` inside `<button>` blocks tap | `pointer-events: none` on img |
| OOM | `--disable-gpu --single-process --disable-extensions` |

---

## Useful Commands

```bash
# Hard-reload dashboard (picks up CSS changes)
ssh of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'

# Check what Python servers are running on the Pi
ssh gduthie@172.16.10.136 'pgrep -af python3'

# Check what servers are running on the Joggler
ssh of@172.16.10.168 'pgrep -af python3'

# Test train departures (National Rail — dashboard tile)
ssh gduthie@172.16.10.136 'curl -s "http://localhost:5001/api/departures?station=TWY&rows=5"'

# Test RTT trains endpoint (trains.html)
ssh gduthie@172.16.10.136 'curl -s "http://localhost:5001/api/trains" | python3 -m json.tool | head -40'

# Test bus departures (Twyford Waggon and Horses stop)
ssh gduthie@172.16.10.136 'curl -s "http://localhost:5001/api/bods/departures?stop=035091060001"'

# Check bus stops count and route coverage
ssh gduthie@172.16.10.136 'curl -s http://localhost:5001/api/buses/stops | python3 -c "
import json,sys; d=json.load(sys.stdin); s=d[\"stops\"]
print(len(s),\"stops;\",sum(1 for x in s if x[\"routes\"]),\"with routes\")"'

# Trigger progressive route timetable fetch
ssh gduthie@172.16.10.136 \
  'curl -s http://localhost:5001/api/buses/route-stops | python3 -m json.tool'

# Test Hive temperature endpoint
ssh gduthie@172.16.10.136 'curl -s http://localhost:5001/api/hive'

# Watch proxy log (proxy started with -u flag so output flushes immediately)
ssh gduthie@172.16.10.136 'tail -f /home/gduthie/twyford-dashboard/proxy.log'

# Check NR STOMP connection status
ssh gduthie@172.16.10.136 'grep -i "NR STOMP" /home/gduthie/twyford-dashboard/proxy.log'

# Check freight trains in buffer (quick API test)
ssh gduthie@172.16.10.136 'curl -s http://localhost:5001/api/trains | python3 -c "
import json,sys; d=json.load(sys.stdin)
freight = [t for t in d[\"trains\"] if not t.get(\"passenger\", True)]
print(f\"{len(freight)} freight trains in buffer\")
for t in freight: print(f\"  {t[\\\"headcode\\\"]} {t[\\\"direction\\\"]} {t[\\\"twy_sched\\\"]}\")"'

# Check disk space on Joggler
ssh of@172.16.10.168 'df -h'

# Benchmark USB drive speed (Joggler)
ssh of@172.16.10.168 'bash ~/bench-drive.sh'

# Take a screenshot of the Joggler
ssh of@172.16.10.168 \
    'DISPLAY=:0 scrot /tmp/screenshot.png && base64 /tmp/screenshot.png' \
    | base64 -d > /tmp/joggler_screen.png
open /tmp/joggler_screen.png

# Expand root to full USB (after cloning to a larger drive)
ssh of@172.16.10.168 'sudo of-expand'
```

---

## Current Status

Everything working as of 2026-07-07.

- [x] Joggler: Boot, WiFi, SSH
- [x] Autologin → X → Openbox → kiosk chain (Chromium → Pi)
- [x] Touchscreen tap (touch-bridge.py)
- [x] sshd OOM-protected (OOMScoreAdjust=-1000)
- [x] Chromium HTTP disk cache on tmpfs (/tmp/chromium-cache) to eliminate USB I/O
- [x] vm.swappiness=10 (/etc/sysctl.d/99-joggler.conf)
- [x] Pi: transport-proxy.py serving dashboard + all API endpoints
- [x] Home screen: 6-tile grid (Weather, Radio, WagtailCam, Trains, Aircraft, Buses)
- [x] Responsive design: Joggler / phone portrait / phone landscape / card profiles
- [x] Touch support: ontouchstart on all buttons; canvas touch drag with passive:false
- [x] Tile timers pause when any view is open
- [x] Weather: NOW/TODAY/WEEK tabs, sun arc, wind compass, AQI, pressure trend, indoor temps
- [x] Radio: 30+ stations, station picker, now-playing (SSE + ICY + RP API), Chromecast
- [x] Radio: Last.fm artist bio, album name, listener count, genre tags, artwork, similar artists via /api/radio/track-info
- [x] WagtailCam: live MJPEG, timelapse, fullscreen
- [x] Transport: trains (5 departures, calling points)
- [x] Flights: draggable Leaflet map, canvas overlay, Airlines/Other/LHR filter buttons,
      aircraft detail panel, 30-min route cache, rAF-throttled drag, debounced fetch
- [x] Buses: Passenger platform departure board, BODS live vehicle map, stop markers
- [x] Bus stop data file-cached (Overpass, run once)
- [x] Bus timetable route data building progressively (Transport API, file-cached)
- [x] Aircraft route cache persisted to localStorage (30-min TTL)
- [x] Aircraft info disk-cached on Pi (30-day mtime expiry)
- [x] Hive indoor temperatures in Weather view
- [x] Graceful shutdown via power button (Joggler only)
- [x] Chromecast from Joggler (cast-server.py on Joggler, port 9998)
- [x] aircraft.html: standalone full-screen aircraft SPA at /aircraft
- [x] aircraft.html: click-to-detail view with airline header, route, live stats
- [x] aircraft.html: Focus mode — full-screen closest commercial aircraft, 20s refresh
- [x] aircraft.html: dynamic airport name lookup via /api/airport-name (OurAirports CSV)
- [x] aircraft.html: location settings modal with Nominatim forward geocoding
- [x] aircraft.html: route not-found state cached to avoid stuck "loading" display
- [x] aircraft.html: responsive layout — portrait/narrow stacks map above list; clamp() font sizes ensure readability on phones (390 px); compact topbar ≤600 px; landscape phone reduces focus view spacing
- [x] aircraft.html: focus view heading card — space between direction letters and rotating arrow
- [x] trains.html: standalone full-screen trains SPA at /trains
- [x] trains.html: RECENT / NEXT / LIST view modes with mode toggle buttons
- [x] trains.html: focus view — operator branding header, headcode, track/call-type badges, route strip, stat cards
- [x] trains.html: LIST mode — scrollable departure board; tap row → focus view for that train
- [x] trains.html: two-source RTT data (Twyford confirmed stops + Reading Main Line estimation)
- [x] trains.html: operator colour dict (GWR, Elizabeth Line, CrossCountry, Heathrow Express, freight operators)
- [x] trains.html: RTT token exchange + caching in transport-proxy (60 s pre-expiry refresh)
- [x] trains.html: NR STOMP freight integration — proxy subscribes to TRAIN_MVT_ALL_TOC, buffers movements at watched STANOXes, merges into /api/trains
- [x] trains.html: freight display — FRET badge, freightClass label, directional origin/dest in focus view
- [x] trains.html: Henley branch trains (2H headcodes) filtered out
- [x] trains.html: NEXT mode multi-train view — if 2+ trains within 90s of each other, compact stacked cards shown simultaneously
- [x] trains.html: direction-aware NEXT grace period (DOWN=0s, UP=30s, unconfirmed=120s after twy_actual)
- [x] trains.html: actual pass time always shown in focus view when TRUST confirms (✓ HH:MM); stat label → "Passed At"
- [x] transport-proxy.py: BST/UTC timezone fix — naive ISO strings now treated as Europe/London (ZoneInfo), not system-default naive
- [x] transport-proxy.py: TRUST headcode extraction fixed — train_id[2:6] not [:4] ([:4] returned schedule prefix + partial headcode)
- [x] transport-proxy.py: TRUST deduplication fixed — headcode+time matching against RTT data (was UID matching, which failed because TRUST UIDs are 'nr:' prefixed)
- [x] trains.html: list view 7-column departure board (Sched, Actual, HC, Operator, From→To, Track, Status)
- [x] trains.html: topbar countdown to next API refresh (seconds, ticks every 1 s)
- [x] trains.html: estimated train countdown at half-minute resolution (in about 2½ mins, etc.)
- [x] trains.html: poll interval 15 s; STOMP STANOX 87014 watch invalidates RTT cache on stopping train pass
- [x] trains.html: multi-train NEXT view redesigned — each card mirrors single focus view (header + horizontal route band + 4 stat cards)
- [x] trains.html: RECENT/NEXT mutual exclusion — grace period inverted so same train cannot appear in both modes
- [x] transport-proxy.py: TD buffer filter — only D1/D4/D6 (Thames Valley SC) stored; _TD_BUF_MAX raised 300→3000; discards all other UK areas immediately on receipt
- [x] td_correlate.py: diagnostic script — connects to TD_ALL_SIG_AREA, logs CA+SF events for D1/D4/D6 with ms timestamps, post-run analysis correlates which SF signal addresses consistently fire alongside each CA berth transition; house-zone berths (D6 0540–0600) highlighted live; run with `python3 td_correlate.py [minutes]`, re-analyse saved log with `--analyse`; HOUSE_ZONE bug fixed (was hex literals 0x540=1344, now decimal 540)
- [x] transport-proxy.py: SF signal state tracking — `_sf_state` dict stores latest aspect per (area, address); updated by `_handle_td()` alongside CA berth steps; persists for life of process
- [x] transport-proxy.py: `/api/td-live` endpoint — returns current berth positions (headcode→{area,berth,age_s}) + signal aspects (D6:12 etc → {data, aspect}) as JSON; polled by lineside.html every 5 s
- [x] transport-proxy.py: on-demand STOMP — TD/SF feed only connects when trains pages are active (`_nr_touch()` on every `/api/trains` poll); auto-disconnects after 90 s idle (`_nr_idle_watcher`); `_nr_running=False` set before `disconnect()` to suppress reconnect loop
- [x] lineside.html: standalone visual track display SPA at `/lineside` — 800×480 dark layout showing Reading→Maidenhead track strip with live train dots (operator colours) from `/api/td-live`; estimated positions from schedule when no TD data; signal aspect dots; house marker (★) at berth 571; next-train panel with ETA countdown; recent and also-coming strips
- [x] lineside.html: signal positions updated from td_correlate.py empirical data (2 runs) — D6:12 x=416 (DOWN approach), D6:19 x=263 (DOWN at house), D6:15 x=158 (UP station departure), D6:1C x=263 (UP at house), D6:1B x=437 (UP east toward Maidenhead)
- [x] lineside.html: complete redesign (session 13) — CSS transform scaleToWindow(), 4 tracks (Down Main/Down Relief/Up Relief/Up Main), physical proportions from milepost data (Reading MP35.7 ↔ Maidenhead MP22.5), berth→x calibration, destination abbreviation labels, NRCC alert strip, house-flash when TD berth in 560-590 range, two-column next panel, 9 signal dots at accurate track/x positions, confBadge confidence indicators
- [x] transport-proxy.py: switch TRUST watch 74005 (Maidenhead, no freight) → 74023 (Twyford, all trains); 87014 still used for RTT cache invalidation on stops
- [x] transport-proxy.py: Maidenhead RTT (MAD, ML/DML, +5 min offset) added for DOWN main line trains; Reading RTT now UP only (UML/UDL); all three fetched in parallel threads
- [x] transport-proxy.py: house_pass_ts field added to every train — DOWN STOP: arrival-15s, UP STOP: departure+15s, PASS: twy_actual or twy_sched; trains sorted by this before response
- [x] transport-proxy.py: /api/nrcc endpoint — NRCC disruption messages from Darwin SOAP, 5-min cache; returns {messages:[…], ts}
- [x] trains.html: labelLine() — destination as primary label for passengers, freightClass for freight
- [x] trains.html: confBadge() — ● live (TD <120s), ✓ TRUST, · RTT, ~ est. confidence indicators
- [x] trains.html: focus and multi-card views show destination as primary, headcode+operator+confBadge secondary
- [x] trains.html: NRCC banner (amber/red dismissable bar above topbar); fetches /api/nrcc every 5 min
- [x] trains.html: approach indicator bar (32px fixed footer, RDG→MAD strip, coloured dots per train from house_pass_ts with 62.9% Twyford position, 35-min window)
- [x] trains.html: trainMs() uses house_pass_ts as first priority; tdPositions dict from /api/td-live every 5s
- [x] transport-proxy.py: switch MAD (Maidenhead) RTT → PAD (Paddington) for DOWN trains; +33 min offset; exclude HX (Heathrow), Windsor, Greenford, Hayes, Bourne End destinations via `_PAD_EXCL_DESTS`; catches fast expresses that don't stop at Maidenhead
- [x] transport-proxy.py: CIF freight daily download + Twyford PASS index — startup fetch of CIF_FREIGHT_FULL_DAILY, indexed by headcode, refreshed 02:30 daily; two-step auth (NR → S3 redirect without forwarding Authorization header); source='cif', track='Relief', confirmed=False
- [x] transport-proxy.py: `_td_enrich_trains()` — live TD berth positions enriching the train list; `at_station` detection for UP STOP trains at D6 berths 1612/1608/1604 (>20s dwell); `_TD_HOUSE_TRANSITIONS` for immediate approaching/at_house events on STOMP thread; stub train creation for headcodes in D6 buffer not matched to any RTT/CIF/TRUST train
- [x] transport-proxy.py: `_d6_berth_to_x()` + `_berth_eta_to_house_s()` helpers — berth→lineside x-coordinate (`x = 601-(b-476)*1.041`); ETA to house from D6 (Twyford area), D1 1600+ (Reading approach, UP trains), D4 400-699 (Maidenhead approach, DOWN trains); speed model: 80 mph main/65 mph relief for passenger, 55/38 for freight
- [x] transport-proxy.py: TD-derived `house_pass_ts` override — when live berth position available, `now + eta_s` replaces RTT schedule+lateness estimate; `td_eta_s` field exposed in `/api/trains`; condition: not twy_actual, not at_station, berth age <300 s, eta_s > -60 s
- [x] trains.html: `fmtExpected()` uses `td_eta_s` path — when `td_eta_s` set and no `twy_actual`, shows `~HH:MM` from `house_pass_ts` (more accurate than RTT lateness, critical for CIF freight with bad CIF times); list view `actualDisp` shows TD ETA even when `late_min=0`
- [x] trains.html: at_station display — "⏸ at stn" badge in confBadge(); fmtExpected() shows "dep HH:MM" from twy_dep_actual or twy_dep_sched; approach bar pins dot at house position with amber border
- [x] transport-proxy.py: `held` train detection — D6 berth frozen >120s and train not yet past house sets `held=True`; house_pass_ts kept from RTT (berth ETA suppressed as meaningless for stationary train)
- [x] transport-proxy.py: Up Relief pass-through detection — at_station dwell threshold raised 20s→45s; trains passing through platform berths (1612/1608/1604) without stopping are never wrongly marked at_station
- [x] trains.html: `held` display — "⏸ held" amber badge in confBadge(); fmtExpected() shows "held (~HH:MM)" using RTT forecast time
- [x] lineside.html: `held` display — `.nc-conf.held` CSS class (orange); etaObj shows "held" in ETA slot
- [x] trains.html: NEXT multi-view capped at 3 trains — findTarget() was returning unbounded trains within 90s window; during delay pile-ups (5+ trains bunched) renderMulti overflowed and cards overlapped; hard cap at 3 prevents this
- [x] lineside.html: ECS category — headcode class `5` gets its own teal `ECS` label/colour instead of being lumped into freight's olive-green `FRT`, across berth panel, approaching list, and passing log
- [x] transport-proxy.py: `_load_cif()` and new `_load_pax_cif()` (CIF_ALL_FULL_DAILY) both stream-decompress via `gzip.GzipFile(fileobj=resp)` instead of reading the whole file into memory — the Pi has 906 MB RAM, nowhere near enough for the full feed's ~2 GB uncompressed; shared streaming/scan helpers `_cif_open_stream()`/`_cif_scan()`; verified memory stayed flat (~270 MB) through the full download+parse
- [x] transport-proxy.py: stock-type display — `stock_type`/`power_bucket` fields on every train from CIF_ALL_FULL_DAILY's Power Type/Timing Load, keyed by headcode; `_cif_stock_label()`/`_cif_power_bucket()`. Timing Load's class-number form (`_TIMING_LOAD_CLASS`: 345/387/800/802) takes priority over Power Type — confirmed via live feed inspection that NR uses the literal class number as Timing Load for modern fleets, and that IET (Class 800/802) can report Power Type as either `DMU` or `EMU` depending on the day's working, so Power Type alone can't distinguish an IET from a Class 165/166 Turbo (both otherwise generic "DMU")
- [x] transport-proxy.py: `_cif_pax_ident()` — origin/dest fallback from the full (all-TOC) CIF feed when the dedicated freight-only feed (`_cif_index`) has no match for a working; fixes freight/ECS trains showing no destination (falling back to a crude headcode-class guess) despite real schedule data existing in the broader feed
- [x] transport-proxy.py: TRUST msg_type 0002 (Cancellation) / 0005 (Reinstatement) now processed for any headcode (not gated by watched STANOX, since cancellation can be declared before a train ever reaches the tracked corridor) — `_nr_cancellations` dict merged onto matching trains in `_rtt_build_trains()`, overriding RTT's own (possibly stale/lagging) `cancelled` flag; previously only msg_type 0003 was handled at all, so live cancellations were silently discarded. 0006 (Change of Origin) / 0007 (Change of Identity) now at least logged (previously also discarded); not yet merged into train records — would need re-keying tracked state across an identity swap
- [x] lineside.html: NEXT PAST THE HOUSE redesigned from a single cramped 40px row (6 trains, heavy truncation) to a two-line-per-train layout: line 1 = countdown, expected time, direction chip, headcode, destination, stock type + coach count; line 2 = operator, origin/stops, live position (berth · place · distance, unified format — a separate fallback code path previously used a different, inconsistent distance-first/no-place-name format with the berth code in the wrong colour), scheduled time/status. Colour-coded power-type dot (grey/gold/teal for diesel/electric/bimode) mirrors the berth-diagram cap stripe so both views read as one system
- [x] lineside.html: berth-diagram power-type indicator — thin coloured cap stripe on each occupied berth box (same diesel/electric/bimode colours as the approaching-list dot), topbar legend added
- [x] lineside.html: Maidenhead Platform 5 loop berth `0581` added alongside `0576` — both resolve to the same physical location (SMART: MAIDENHED platform 5), and a reversing train's CC relabel can land on either; backend supersession already keyed correctly (stanme+platform, not raw berth code) but `0581` had nowhere to render on the frontend, leaving the platform 5 box looking empty even though the train (2B94) was being tracked correctly (Tracksy showed it, we didn't). Also: Marlow branch (2B headcodes) coloured GWR green and 2Y-series Maidenhead reversal moves coloured Elizabeth Line purple, same reasoning/pattern as the earlier Henley-branch (2H) fix — these are deliberately absent from every /api/trains path server-side, so op_code is never populated
- [x] now.html: new standalone SPA at `/now` — kitchen ambient display combining aircraft.html's closest-aircraft focus mode (left, cool blue) with trains.html's 4-line Twyford trackboard (right, warm amber), split by a glowing vertical seam; stacks vertically instead on portrait/narrow screens (`max-aspect-ratio: 95/100`). Reuses both pages' data tables/helpers (AIRLINES/AC_TYPES/AIRPORTS, OPS, format helpers) verbatim rather than sharing code, matching this project's established one-file-per-page pattern. Polls `/api/flights?focus=1` every 20s, `/api/trains` every 15s, `/api/nrcc` every 5 min; NRCC disruption banner spans the full width at the bottom. Route registered in transport-proxy.py alongside `/aircraft`/`/trains`/`/lineside`
- [x] now.html: reworked after first real-screen feedback — clamp() max-sizes were copied down from the original full-width single-purpose pages and left huge amounts of blank space on a big monitor; raised throughout (destination text, countdowns, stat values, airport names) so content actually fills available space rather than capping out early. Added plane/train SVG icons (currentColor, tinted per pane) to the pane labels, and made the centre seam far more visually distinct (3px core gradient blue→white→amber, blurred glow bar behind it, glowing meeting-point dot) per explicit request for clearer labelling and a stronger divider
- [x] now.html: fixed a real iPhone-portrait bug — aircraft route text was bleeding visibly into the header and stat cards (not a pane-boundary overflow issue: `.ac-route`'s flex box was being shrunk by its siblings' fixed sizes but had no `overflow:hidden`, so content rendered outside its own box instead of being clipped). Also found the same "grid/flex item defaults to content's min-content width" trap on `.ac-stat-card` — a 6-digit altitude reading (e.g. "11,278") could force a grid column wider than the viewport and spill off the left edge even in landscape; fixed with `min-width:0` on the card plus `overflow:hidden`/reduced max font-size on `.ac-stat-value`. Portrait now has its own explicit (non-vh-scaled) size budget for the aircraft side so header+route+stats reliably fit in half a chrome-shrunk mobile viewport instead of relying on clipping to paper over a too-tall layout
- [x] now.html: airline logos had no background card (unlike aircraft.html's `.cv-logo`/`.det-logo`, both white-boxed), so light-coloured logos blended into the dark background — added the same white rounded-card treatment to `.ac-logo`
- [x] now.html + trains.html: both were still using the pre-ECS-fix categorization (`isFreightHc` included headcode class 5, no distinct ECS branch in `opInfo`) — only lineside.html had gotten that fix. Ported it to both: ECS gets its own teal colour/label instead of being shown as generic olive "Freight" (confirmed bug: 5N82, a real Class 345 ECS move, showed as Freight on both pages while correctly showing as ECS on lineside.html)
- [x] now.html: destination/place text was hard-truncating at this page's much larger base font sizes ("CHELTENHAM S…") with no way to read the rest, since the size was tuned for short names like "Reading". Added a JS shrink-to-fit (`fitText()`) that starts from the CSS clamp()-resolved size and steps down until the text actually fits, applied to `.t-dest`, `.ac-airport`, `.ac-flight-num`, and `.ac-stat-value` (the last also fixed a real bug where a 6-digit altitude could silently clip inside its own card). Also reordered the sub-line so live position sits right after the operator badge instead of last — it's the one dynamic "where is it right now" fact worth protecting when the line doesn't have room for everything — and changed `.t-sub` from single-line-with-fade to wrap onto a second line when needed (there was vertical slack in the row; no reason to lose information that fits)
- [x] transport-proxy.py: fixed a real "vanishes too early" bug for stopping services — a train calling at Twyford (e.g. 2N20) only had its *arrival* recorded via the TD house-crossing timestamp, and the frontend's grace window for a confirmed down-direction train is a mere 20s past that instant, so a train genuinely still dwelling at the platform got dropped from the display and replaced by the next service within seconds of pulling in. `at_station` (the flag that already existed for keeping a train visible while at a stand) was only ever computed for one narrow case — up-direction Maidenhead-reversal berths — so down-direction Twyford calls never got it. Added a general dwell computation using RTT's own arrival/departure times (any confirmed STOP service, either direction) in `_rtt_normalise()`, guarded the TD house-crossing handler so it doesn't immediately clear `at_station` back to False for a STOP (crossing the house on arrival is the *start* of the dwell, not a sign of departure), and taught `graceMs()` (trains.html + now.html) to hold a dwelling train as current for up to 10 minutes instead of 20s
- [x] now.html: the 4 aircraft stat cards (altitude/speed/heading/distance) could end up visibly different sizes — `fitText()` was shrinking each independently, and "Heading" (e.g. "WSW ↖" — letters plus a rotated arrow glyph) is simply wider than a plain number like "693", so it got shrunk more than its neighbours even though all four are meant to read as one uniform row. Added `fitTextGroup()`, which measures all four together and picks the one size that fits the widest, instead of optimizing each card for its own content
- [ ] trains.html / lineside.html: run td_correlate.py again during busy morning service to confirm signal address mapping with more trains; update KEY_SIGNALS positions if needed
- [ ] lineside.html: use SF signal aspects to indicate "clear road" / "signals at caution" for approaching trains once mapping is fully confirmed
- [ ] transport-proxy.py: per-train cancelReason/delayReason from Darwin SOAP (extracted in _parse but not yet exposed in /api/trains response)
- [ ] transport-proxy.py: TRUST msg_type 0006/0007 (Change of Origin / Change of Identity) — merge into train records rather than just logging; needs re-keying tracked state across an identity swap
- [x] transport-proxy.py: `_cif_best()` now disambiguates by time, not just STP priority — freight/ECS headcodes are routinely reused for a different job later the same day, and picking by STP priority (O>N>P) alone with no regard for time could match the wrong job entirely (confirmed: 6M93, live at Twyford 14:08, matched an unrelated CIF entry timed 21:09). Among valid entries with a Twyford-area time, picks whichever is closest to now (or a given ref_ts); STP priority remains the fallback/tie-breaker when no entry has a usable time. `_load_pax_cif()` now also captures `twy_hhmm` per entry (mirroring the freight loader) so this applies to the all-TOC feed too
- [x] transport-proxy.py: `_cif_ident()`/`_cif_pax_ident()` now reject a match whose own Twyford-area time is more than 30 min from now (`_CIF_IDENT_TOLERANCE_S`) — even after the disambiguation fix above, an unreliable/no-good-candidate match is treated as no identity at all rather than risking another false `_passes_twyford()` exclusion; the train still shows via live TD position + headcode-based type guess instead of being dropped entirely
- [x] transport-proxy.py: `_COLOCATED_BERTHS` — manual override table linking bay/siding berth codes known to be the same physical track across arrival/dwell/reversal phases (Twyford P5 bay: A641/B641/R641; Maidenhead turnback: 0578/R578) to a shared canonical key for CC-relabel supersession matching, regardless of SMART coverage gaps. Fixes a reversing train's old headcode getting stuck showing forever after being relabelled (2H37→2H38 case) — confirmed B641 (the P5 bay's middle/dwell berth) has no SMART entry in any area, so the previous STANME+platform key fell back to the raw berth code there and could silently fail to link the relabel to the correct prior occupant
- [x] transport-proxy.py: `_NR_STANOX_WATCH['74233']` (Kennet Bridge Jn, ~5.1 mi west of Twyford, confirmed via live TD sightings + CORPUS STANOX lookup) added for advance warning on freight/ECS workings not in RTT's public schedule query (5E10, 6M93 etc. previously had zero lead time — 74023 fires essentially at the moment of passing). Offsets from the existing freight/passenger speed model at 5.1 mi; UP only gives real advance warning (DOWN there means already past Twyford, same reasoning as the existing Reading RTT query being UP-only). Confirmed receiving live traffic via temporary diagnostic logging (6 hits/17 min, both directions, mixed passenger/freight) before being removed; RTT-covered passenger trains correctly dedup away in favour of the richer RTT record. Still waiting to observe an actual UP-direction freight/ECS example with positive lead time — freight/ECS traffic is inherently much less frequent than passenger, this is expected, not a bug
- [x] transport-proxy.py: fixed a regression from the above — `twy_actual`/`confirmed` were being set unconditionally for every watched STANOX hit, which was harmless when only 74023 (Twyford itself, offset always 0) existed, but wrongly marked a train 5+ miles away at Kennet Bridge Jn as having already been CONFIRMED passing Twyford (5E56 case) once 74233's non-zero offsets came into play. Now only set when `offset_min == 0`; `_td_enrich_trains()` still correctly promotes to confirmed once a real Twyford-area TD sighting happens
- [x] transport-proxy.py: TRUST msg_type 0007 (Change of Identity) now tracked (`_nr_identity_map`) and used to drop an unconfirmed old-headcode entry once we know it changed identity mid-journey — it will genuinely never be seen passing Twyford under that number again. Confirmed on live traffic (6 real identity changes captured same day: 6G45→0G45, 6B44→0B44, 6M09→4M09, 6L95→4L95, 4F55→6F55, 5B52→5B00). 0006 (Change of Origin) logged only — acting on it needs a STANOX→place reverse lookup CORPUS doesn't give directly
- [x] transport-proxy.py: `_passes_twyford()`'s origin/dest exclusion (a crude small hardcoded "east of Twyford" place-name token list) now only applied when direction had to fall back to SMART's static data — not when direction is measured from a real from→to berth comparison. Fixes 4O38 (Birmingham → "Freightliners (Maritime Terml)") being excluded for its entire ~32-minute, unambiguous, multi-berth approach because neither endpoint name matched a known token; real measured movement is stronger evidence than a name-matching guess. The RTT-side use of the same token list (Reading-query filtering) has the same latent fragility but wasn't implicated in a confirmed bug, left alone
- [x] transport-proxy.py: tightened the above — "measured" direction only needs ONE valid from→to step, and a train about to reverse can produce exactly one plausible-looking step first. Confirmed: a CrossCountry Manchester→Reading service (reversing in the D1 Reading throat, ~4.8 mi out — the exact case _passes_twyford's guard was originally built for) briefly showed as approaching then vanished next poll. Now the origin/dest check still applies within the outer ~1 mi of either end of the tracked corridor (the Reading/Maidenhead throats specifically) even when direction reads as measured; measured direction is trusted everywhere else, where a train has real running room and reversal isn't in play. Preserves the 4O38 fix (its approach was through D4→D6→D1, never sitting in the throat) while restoring protection against the reversal case
- [x] lineside.html: NRCC disruption message was hard-truncated at 130 characters mid-word (and boxed to 44px, ~2.6 lines) — raised to a 280-char cap with a clean word-boundary ellipsis if genuinely longer, and the box to 72px so realistic messages fit in full
- [x] lineside.html: Henley branch (2H headcodes) coloured grey instead of GWR green in the berth diagram — these trains are deliberately excluded from every /api/trains path server-side ("Henley branch — excluded everywhere", out of scope for the Reading↔Maidenhead corridor ETA logic), so op_code is never populated. `opInfo()` now special-cases 2H headcodes to GWR's colour directly (100% GWR-operated in reality)
- [x] lineside.html: split the two merged Henley-branch direction-pair berth cells (BYUP/BYDN near Henley, 1632/1643 near Henley Br Jn) into separate boxes — real SMART/live-TD data confirms these are genuine direction-specific TD berths on the single bidirectional line, not duplicates. Fixed a wrong "WARGRAVE · SHIPLAKE" caption on the BYUP/BYDN cell in the process (both actually resolve to STANME HENLEYONT — Henley itself; no berth coverage exists for the real Wargrave/Shiplake stations). Widened cells to ≥42px and added direction chevrons (reusing the existing width-gated mechanism) so the pair is distinguishable at a glance; BYUP/BYDN stacked vertically per user preference, 1632/1643 kept side by side
