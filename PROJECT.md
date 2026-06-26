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

**Data source:** Real Time Trains (RTT) API v2 (`data.rtt.io`). RTT is queried with three calls
per 30-second polling cycle (run in parallel threads):

1. **Twyford query** (`/gb-nr/location?code=TWYFORD`): all trains that call or pass Twyford —
   primarily Elizabeth Line and GWR local on the Relief Line. These are `confirmed=true`.

2. **Reading query** (`/gb-nr/location?code=RDG`): UP Main Line trains only (line codes `UML`,
   `UDL`) from Reading, heading towards London. Offset: +3 min to estimate Twyford pass time.
   These are `confirmed=false` (estimated pass). DOWN trains are NOT taken from Reading
   (they have already passed Twyford by the time they appear in the Reading lineup).

3. **Maidenhead query** (`/gb-nr/location?code=MAD`): DOWN Main Line trains (`ML`, `DML`)
   approaching Twyford from the east. Offset: +5 min after Maidenhead. These are
   `confirmed=false` (estimated pass).

**Why two sources:** Fast-line GWR IETs (Bristol, Cardiff, Swansea, etc.) do not have Twyford
as a WTT timing point — Twyford is only a pass-through with no scheduled entry in their
working timetable. Signal berth points `TWYF112`, `TWYF632`, `TWYFDW` exist in RTT but return
zero services (TRUST berth points are not WTT timing points). The Reading estimation approach
adds ~26 extra trains per 2-hour window (44 total vs 18 from Twyford alone).

**Freight trains via Network Rail STOMP:** Freight is absent from RTT queries. The proxy opens
a persistent on-demand STOMP connection to the Network Rail TRUST feed and buffers ALL train
movements passing Twyford (STANOX 74023) — `freight_only: False` so passenger trains are also
captured (they are deduplicated against RTT by headcode+time matching). Freight entries not
matched by RTT are merged into the `/api/trains` response. STANOX 87014 (Twyford station stops)
additionally triggers an immediate RTT cache invalidation (`_rtt_trains_ts=0`) so confirmed
pass times appear within 1–2 s of the TRUST event. See the "Network Rail Open Data Feeds"
section for STOMP details.

**Henley branch trains excluded:** Trains with headcode prefix `2H` are filtered out. These
run on the Henley-on-Thames branch, diverging from the **west** end of Twyford station, and
are not audible from the house (~200 m east of the station).

**Three display modes** (toggle buttons in top bar):

- **RECENT** — focus view for the most recent train that passed (within last 10 min)
- **NEXT** — focus view for the next train due. If 2 or more trains are expected within
  90 seconds of each other, a stacked multi-card view shows all of them simultaneously.
  Each card is a compressed version of the single focus view: operator header, route band
  (Origin → TWYFORD → Destination, horizontal), and 4 stat cards (At Twyford, Delay, Track,
  Vehicles). All the same information as the single-train view, at ~60% the font size.
- **LIST** — scrollable departure board with 7 columns: Sched, Actual, HC, Operator,
  From → To, Track, Status. Tapping a row opens a focus view for that train.

**Focus view elements:**
- Header bar: operator name + gradient background in operator brand colour
- Large headcode (e.g. `1L35`) + track badge (`Main`/`Relief`) + call type badge (`STOP`/`PASS`/`PASS (est)`)
- Route strip: origin → **TWYFORD** (amber) → destination; when TRUST has confirmed the actual
  pass time, shows `✓ HH:MM` (or `✓ HH:MM (+N min)` if late ≥5 min)
- Four stat cards: time at Twyford (label changes to "Passed At" once TRUST confirms actual
  time), delay (minutes), track direction, vehicle count

**Operator colour dict (`OPS` in trains.html):**

| Code | Operator | Background |
|------|----------|------------|
| GW | GWR | `#007a4d` green |
| XR | Elizabeth Line | `#7156a5` purple |
| XC | CrossCountry | `#a61530` red |
| HX | Heathrow Express | `#532885` indigo |
| GB | GBRf | `#1e3050` navy / `#ffdd00` amber |
| DW / DB | DB Cargo | `#db0a17` red |
| FL | Freightliner | `#1a6b2a` green |
| ZZ | Colas Rail | `#f05a24` orange |
| ZN | Network Rail | `#d4ac0d` yellow |

**Direction logic:** `direction=up` if destination is in the set of London termini (London
Paddington, Abbey Wood, Shenfield, Heathrow termini). Otherwise `down`.

**NEXT mode grace period (direction-aware):** Once TRUST fires for Twyford (`twy_actual` set),
the grace period before removing a train from NEXT depends on direction relative to the house
(~200 m east of Twyford station):
- DOWN trains (Reading direction): house is east of Twyford so the train passed the house
  *before* arriving at Twyford → grace = 0 (remove immediately once `twy_actual` is set)
- UP trains (London direction): train departs Twyford heading east, passes the house ~30 s
  later → grace = 30 s after `twy_actual`
- Unconfirmed/estimated trains: grace = 120 s (schedule is approximate)

**RECENT/NEXT mutual exclusion:** A train cannot appear in both RECENT and NEXT simultaneously.
RECENT uses the same grace thresholds but inverted — a train only moves from NEXT to RECENT
once it has cleared its grace window (`ms < now - grace`). This avoids a confirmed UP train
(30 s grace) appearing in NEXT (not yet expired) and RECENT (just passed) at the same time.

**Countdown features:**
- Topbar shows countdown in seconds to the next API refresh (ticks every 1 s via `tickTopbar()`)
- Estimated trains show a half-minute resolution countdown below the departure time:
  "in about 2½ mins", "in about 30 secs", "passing now" (< 15 s)
- Polling: trains.html polls proxy every 15 s; proxy only hits RTT every 30 s (TTL absorbs extra).
  When TRUST fires at STANOX 87014 (stopping train), proxy invalidates RTT cache immediately
  (`_rtt_trains_ts = 0`) so the confirmed pass time appears within 1–2 s of the TRUST event.

**Freight display:** Freight trains (`passenger=false`) show a green `FRET` badge (background
`#2d3d1a`, text `#a0c060`) instead of the operator colour. The focus view shows the freight
class (`Heavy Freight` / `Intermodal` / `Freight` / `Light Loco` / `Special` based on the
first digit of the headcode) in place of the operator name, and shows direction labels
(`← From west` / `→ To London` or `← From London` / `→ To west`) in place of origin/dest.
If the freight operator code is known (e.g. `GB` for GBRf, `FL` for Freightliner), the
operator's own branding is used instead of the generic `FRET` badge.

**Token management:** RTT uses a long-lived refresh token (stored as `RTT_REFRESH_TOKEN` in
Pi's `.env`) exchanged for a short-lived access token (~20 min) at `/api/get_access_token`.
The proxy caches the access token and refreshes it 60 s before expiry without blocking requests.

**Rate limits:** 30 req/min, 750/hr, 9000/day. Two queries per 30 s = 5,760/day (within limit).

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
| `GET /api/trains` | RTT API (Twyford + Reading) + NR STOMP buffer | 30 s | Three-source: confirmed stops + estimated Main Line passes + NR freight from STOMP |
| `GET /api/radio/resolve?url=…` | PLS/M3U playlist fetch | 30 s | Returns direct stream URL |
| `GET /api/radio/nowplaying?url=…` | ICY stream metadata | 25 s | StreamTitle from ICY |
| `GET /api/radio/nowplaying-rp?chan=N` | Radio Paradise API | 20 s | |
| `GET /api/radio/track-info?artist=…&title=…` | Last.fm API | 3600 s | Bio, album, listeners, artwork, tags, similar artists |
| `GET /health` | — | — | Returns `ok` |
| `GET /aircraft` | Static — aircraft.html | — | Standalone full-screen aircraft SPA |
| `GET /trains` | Static — trains.html | — | Standalone full-screen trains SPA |
| `GET /lineside` | Static — lineside.html | — | Standalone visual track display SPA |
| `GET /api/td-live` | In-memory TD/SF state | — | Live berth positions + signal aspects from NR TD feed. No TTL — returns current state. Positions expire after 10 min of inactivity. |
| `GET /api/nrcc` | Darwin SOAP nrccMessages | 300 s | NRCC disruption messages for Twyford area. Extracted from existing Darwin SOAP response (`{*}nrccMessages/{*}message`). Returns `{messages:["…"], ts}`. |
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
| `TD_ALL_SIG_AREA` | Train Describer (TD) | ~6000/min | All signal areas combined — the **only available TD topic**. Area-specific topics (e.g. `TD_WTV_SIG_AREA` for Western Thames Valley) were deprecated years ago and no longer exist on the broker. Filter client-side. We filter to areas D1/D4/D6 in `_handle_td()` before buffering, reducing effective volume ~50×. Message types: **CA** (berth step — train moved from→to), **CB** (berth cancel), **CC** (berth interpose), **CT** (heartbeat, ignore), **SF** (signal flag — aspect change), **SG/SH** (signal flag variants). |
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
| `D4` | Hayes Area Scalable IECC | East of Twyford → Maidenhead | 1P36 (up Main) entered D4 at berth 0470 heading east |
| `D6` | Maidenhead Area Scalable IECC | Twyford corridor — **Relief Line both directions + Up Main** | 9R78, 9R26, 9U35 (Relief); 1A31, 1P36 (up Main) |
| `D1` | Reading IECC A | Reading area + **Down Main through Twyford** | 1G29 (down Main) was in D1 throughout its Twyford arrival |

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
- **TRUST `actual_timestamp`** is Unix milliseconds, always UTC. Stored in the buffer as
  a UTC-aware ISO string with `+00:00` suffix.
- **CIF times** (e.g. `1638` = 16:38) are local BST. Not used in the proxy currently (freight
  CIF integration is pending), but must be treated as local time when parsed.
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

Everything working as of 2026-06-26.

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
- [ ] trains.html / lineside.html: run td_correlate.py again during busy morning service to confirm signal address mapping with more trains; update KEY_SIGNALS positions if needed
- [ ] lineside.html: use SF signal aspects to indicate "clear road" / "signals at caution" for approaching trains once mapping is fully confirmed
- [ ] transport-proxy.py: per-train cancelReason/delayReason from Darwin SOAP (extracted in _parse but not yet exposed in /api/trains response)
- [ ] transport-proxy.py: TRUST msg_type 0001 (Activation) watch for freight headcode confirmation before arrival
