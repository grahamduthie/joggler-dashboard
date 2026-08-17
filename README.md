# Twyford Dashboard

A smart home kiosk built on an O2 Joggler — a repurposed 2010-era internet appliance with a
7" touchscreen. It runs as a full-screen dashboard for a house in Twyford, Berkshire, showing
live weather, radio, a wildlife camera, trains, flights radar, and a bus departure board.

![Dashboard](IMG_6994.jpg)

---

## What is a Joggler?

The O2 Joggler (OpenPeak OpenFrame 1) was a UK consumer internet appliance from around 2010.
It has a 7" 800×480 resistive touchscreen, an Intel Atom Z520 CPU, 492 MB RAM, and originally
ran a bespoke Linux OS that is now defunct. This project replaces that OS with
[openframe-linux](https://github.com/birdslikewires/openframe-linux) (Debian Trixie) on a
USB stick, and repurposes the device as a persistent household display.

---

## Features

| Tile / View | What it shows |
|-------------|---------------|
| **Weather** | Current conditions, hourly chart, 7-day forecast. NOW / TODAY / WEEK tabs. Sun arc, wind compass, AQI, indoor temperatures from Hive heating. |
| **Radio** | 30+ stations — Marlow FM (with live SSE now-playing), Bauer/Global/indie streams. Station picker grid. Chromecast casting. |
| **WagtailCam** | Live MJPEG stream and dated timelapse from a garden wildlife camera. |
| **Trains** | Next 5 departures from Twyford station (National Rail live feed). Tap for calling points. |
| **Trains detail** (`/trains`) | Standalone full-screen trains SPA (Real Time Trains API). Shows all trains passing the house — stopping, non-stopping, and Main Line fast trains estimated from Reading. RECENT / NEXT / LIST modes with operator branding. |
| **Flights** | Leaflet.js radar map with ADS-B live positions, airline logos, and FlightAware route details. |
| **Aircraft detail** (`/aircraft`) | Standalone full-screen aircraft SPA. Tap any aircraft for detail view; Focus mode shows a single large display of the closest commercial aircraft with live stats — designed to identify overhead aircraft at a glance. |
| **Buses** | Live departure board for Twyford stops (routes 850, 127, 128, 129, 12). Leaflet.js map with live vehicle positions. |

The dashboard is a single-file SPA (`dashboard.html`) — no framework, no build step.

### Responsive profiles

The dashboard automatically adapts to the display it runs on:

| Profile | Condition | Layout |
|---------|-----------|--------|
| `profile-joggler` | 780–820 px wide, ≤500 px tall | Original kiosk layout; power button visible |
| `profile-phone-portrait` | ≤540 px wide, portrait | 2-column tile grid; views scroll |
| `profile-phone-landscape` | ≤900 px wide, ≤500 px tall | 3-column compact tiles |
| `profile-card` | Everything else | 800 px centred card; power button hidden |

---

## Architecture

The system has two parts:

```
GDX cloud VM                             O2 Joggler (172.16.10.168)
────────────────────────────           ─────────────────────────
transport-proxy.py :8002 (loopback)     Chromium kiosk
  • serves dashboard.html      ────▶     https://dashboard.gdx.org.uk/
  • National Rail API proxy
  • ADS-B / flight route proxy  shutdown-server.py :9999
  • Bus departures + vehicles     (power button — Joggler only)
  • Hive heating temperatures
  • Radio stream resolver        touch-bridge.py
  • Airline logos / aircraft info  (raw touchscreen → XTest)
  • Static file serving

Joggler-local helpers
  • cast-server.py :9998 (Chromecast discovery + control)
  • shutdown-server.py :9999 (power button)
```

The Joggler is a **thin client**: it runs Chromium in kiosk mode and nothing else. All API
proxying and data fetching happen on the cloud VM. Nginx publishes the cloud backend over HTTPS,
so relative `/api/...` URLs work from the Joggler and any other browser.

---

## Repository structure

```
dashboard.html          Single-file SPA — all views, CSS, JS
aircraft.html           Standalone full-screen aircraft SPA (served at /aircraft)
trains.html             Standalone full-screen trains SPA (served at /trains)
transport-proxy.py      Cloud backend: all API proxying + static file serving
cast-server.py          Joggler-local Chromecast discovery/control (port 9998)
shutdown-server.py      Joggler: graceful power-off via power button (port 9999)
touch-bridge.py         Joggler: raw touchscreen events → X11 mouse events
hive-setup.py           One-time interactive Hive auth setup
setup-kiosk.sh          One-time Joggler setup script (X, Openbox, autostart)
bench-drive.sh          USB drive benchmark (dd + hdparm)
fix-oom.sh              Apply OOM protection to sshd (run once if not in setup-kiosk.sh)
hls.min.js              HLS.js library for AAC/HLS radio streams
icons/                  Weather icons (MAm TV set, 92 PNGs) + station/camera logos
```

**Not in this repository** (created at runtime or contain credentials):
- `hive-tokens.json`, `hive-credentials.json` — Hive/Cognito auth tokens (mode 600)
- `.env` — BODS API key, Last.fm API key, RTT refresh token
- `bus-stops.json`, `bus-route-stops.json` — cached from Overpass/Transport API
- `logos/`, `aircraft-info/`, `airport-names.json` — downloaded and cached airline logos, aircraft metadata, and airport names

---

## Setup

Two separate setup guides:

- **[JOGGLER-SETUP.md](JOGGLER-SETUP.md)** — Flash openframe-linux, install packages, configure
  the Joggler as a thin kiosk client pointing at `https://dashboard.gdx.org.uk/`.

- **[PI-SETUP.md](PI-SETUP.md)** — Legacy Pi setup and the temporary rollback backend. It is no
  longer the normal production deployment target.

See **[PROJECT.md](PROJECT.md)** for the full technical reference — API details, rate limits,
data formats, deployment commands, and key gotchas.

The live production backend is on the GDX cloud VM at `dashboard.gdx.org.uk` and
`nearby.gdx.org.uk`. The Pi backend remains available temporarily as the documented rollback path.
Read **[CLOUD-MIGRATION-PLAN.md](CLOUD-MIGRATION-PLAN.md)** before changing cloud deployment,
DNS, Nginx, kiosk URLs, or the public Dashboard/Nearby routes.

---

## Quick deployment (day-to-day)

```bash
# Deploy code and static assets to cloud production, restart the Supervisor service,
# and verify its loopback health endpoint. Protected runtime state is not overwritten.
./deployment/cloud-deploy.sh

# For a front-end-only change, then hard-reload the Joggler to pick it up immediately.
ssh of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'
```

Use `ctrl+shift+r` (hard reload), not F5 — F5 may serve cached CSS.

Do not run `rsync --delete` against `/home/gduthie/joggler`: that directory contains cloud-only
credentials, tokens, learned railway state, caches and calibration data.

---

## Photos

| | | |
|--|--|--|
| ![Home screen](IMG_6993.jpg) | ![Weather](IMG_6995.jpg) | ![Buses](IMG_6996.jpg) |
| Home screen | Weather — NOW tab | Bus departure board |
