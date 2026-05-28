# Joggler Kiosk — Technical Reference

## What Is This?

An O2 Joggler (OpenPeak OpenFrame 1) repurposed as a smart home kiosk for Twyford, Berkshire. It
displays a full-screen touch-driven dashboard with live weather (including indoor temperatures from
Hive heating), internet radio (30+ stations, with Chromecast casting), WagtailCam live
stream/timelapse, trains from Twyford station, flights radar, and a live bus departure board
with map.

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
  ├── .env  — BODS_API_KEY + LASTFM_API_KEY (mode 600)
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
            → chromium --kiosk http://172.16.10.136:5001/
```

`touch-bridge.py`, `shutdown-server.py`, and `cast-server.py` run on the Joggler. The transport
proxy runs on the Pi.

**Why cast-server.py runs on the Joggler:** `dashboard.html` calls `http://localhost:9998` from
JavaScript. Since JS runs in the Joggler's browser, `localhost` resolves to the Joggler — not
the Pi. cast-server.py must therefore run on the Joggler to be reachable.

### kiosk.sh (key flags)

```bash
exec chromium \
  --kiosk --no-first-run --disable-infobars \
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
transport-proxy.py          # API proxy + static file server (port 5001)
cast-server.py              # Chromecast discovery/control (port 9998)
hive-setup.py               # Interactive Hive auth setup (run once to obtain tokens)
hls.min.js                  # HLS.js library (served statically to browser)

hive-tokens.json            # Hive/Cognito auth tokens + home_id (mode 600)
hive-credentials.json       # Hive login credentials for auto-reauth (mode 600)
.env                        # BODS_API_KEY + LASTFM_API_KEY (mode 600)

icons/
  wsymbol_*.png             # 92 PNG weather icons (MAm TV set, 128×128)
  *.png / *.svg             # Radio station logos, WagtailCam logo

logos/                      # Airline logos cached from pics.avs.io (created at runtime)
aircraft-info/              # Aircraft year/reg from OpenSky (created at runtime)
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
  `flightRouteCache` (localStorage). **TTL: 6 hours** — low-cost carriers reuse flight numbers
  daily on different routes, so long TTLs show stale destinations.
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
| `GET /api/flights?lat=…&lon=…&dist=…` | adsb.lol `/v2/lat/{}/lon/{}/dist/{}` | 60 s | lat/lon rounded to 2 dp, dist snapped to 10 nm — nearby viewports share cache |
| `GET /api/bods/departures?stop=ATCO` | Passenger platform scrape (parallel per operator) | 30 s | |
| `GET /api/bods/buses` | BODS SIRI-VM (all operators in parallel) | 30 s | Full bus list |
| `GET /api/buses/vehicles` | BODS (filtered to tracked routes, GeoJSON) | 30 s | |
| `GET /api/buses/stops` | Overpass API (first run only) | 4 h in-memory; file indefinite | Merges timetable route data at serve time |
| `GET /api/buses/route-stops` | Transport API timetable (progressive) | file permanent | Fetches missing routes; stops on rate-limit |
| `GET /api/hive` | Hive Beekeeper API (via Cognito tokens) | 300 s | Auto-refreshes tokens |
| `GET /api/flight-route?cs=CALLSIGN` | FlightAware HTML scrape | 4 h | Route, times, aircraft type |
| `GET /api/airline-logo?iata=XX` | pics.avs.io (file-cached) | File permanent | |
| `GET /api/aircraft-info?hex=XXXXXX` | OpenSky metadata (file-cached) | 30 days (mtime check) | |
| `GET /api/radio/resolve?url=…` | PLS/M3U playlist fetch | 30 s | Returns direct stream URL |
| `GET /api/radio/nowplaying?url=…` | ICY stream metadata | 25 s | StreamTitle from ICY |
| `GET /api/radio/nowplaying-rp?chan=N` | Radio Paradise API | 20 s | |
| `GET /api/radio/track-info?artist=…&title=…` | Last.fm API | 3600 s | Bio, album, listeners, artwork, tags, similar artists |
| `GET /health` | — | — | Returns `ok` |
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

# Deploy and restart transport-proxy on Pi
scp transport-proxy.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh gduthie@172.16.10.136 \
    'kill $(pgrep -f transport-proxy) 2>/dev/null; \
     nohup python3 /home/gduthie/twyford-dashboard/transport-proxy.py \
       >> /home/gduthie/twyford-dashboard/dashboard.log 2>&1 & disown; echo started'

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

### Python / Server
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

# Test train departures
ssh gduthie@172.16.10.136 'curl -s "http://localhost:5001/api/departures?station=TWY&rows=5"'

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

# Watch proxy log
ssh gduthie@172.16.10.136 'tail -f /home/gduthie/twyford-dashboard/dashboard.log'

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

Everything working as of 2026-05-28.

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
      aircraft detail panel, 6-hour route cache, rAF-throttled drag, debounced fetch
- [x] Buses: Passenger platform departure board, BODS live vehicle map, stop markers
- [x] Bus stop data file-cached (Overpass, run once)
- [x] Bus timetable route data building progressively (Transport API, file-cached)
- [x] Aircraft route cache persisted to localStorage (6-hour TTL)
- [x] Aircraft info disk-cached on Pi (30-day mtime expiry)
- [x] Hive indoor temperatures in Weather view
- [x] Graceful shutdown via power button (Joggler only)
- [x] Chromecast from Joggler (cast-server.py on Joggler, port 9998)
