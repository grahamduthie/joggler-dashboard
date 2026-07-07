# Railway Data Sources — Reference for Future Claude

This documents every railway data source the Twyford dashboard uses: the credential it
needs, where that credential lives, what data it provides, how to call it, and the
non-obvious gotchas. The house is ~200 m east of **Twyford** station (Great Western Main
Line), on the Up/Down Relief crossover. Four running lines pass it: **Up Main, Down Main,
Up Relief, Down Relief**. All of this is consumed by `transport-proxy.py` on the Pi.

> **Credential policy:** secret values are NOT written in this file (it is committed to a
> GitHub repo). They live in `.env` / `nr-credentials.env`, which are **gitignored**. To read
> a live value: `cut -d= -f2- .env | …` or just read the file — a Claude working in this repo
> (Mac or Pi) can open them. The one exception is the Darwin token, which is already hard-coded
> in `transport-proxy.py` (see §3), so it is repeated here.

Credential files:
- **Pi:** `/home/gduthie/twyford-dashboard/.env` (mode 600) — keys: `BODS_API_KEY`,
  `LASTFM_API_KEY`, `RTT_REFRESH_TOKEN`, `NR_USERNAME`, `NR_PASSWORD`.
- **Mac (this repo):** `nr-credentials.env` — `NR_USERNAME`, `NR_PASSWORD`, `RTT_REFRESH_TOKEN`.
- Loaded at startup by `_load_env()` in `transport-proxy.py`.

---

## 1. Real Time Trains (RTT) — schedules + realtime, the primary passenger source

| | |
|---|---|
| **Base URL** | `https://data.rtt.io` |
| **Credential** | `RTT_REFRESH_TOKEN` — a long-lived refresh token (`.env`) |
| **Account / portal** | https://api-portal.rtt.io (RTT unified login). The "Pull/Push data API" product. |
| **Rate limit** | 30 req/min, 750/hr, 9000/day. We use **2 calls / 30 s = 5,760/day**. |
| **Code** | `_rtt_get_token`, `_rtt_location_query`, `_rtt_normalise`, `_rtt_build_trains` |

**Auth flow (two-token):**
1. `GET /api/get_access_token` with header `Authorization: Bearer <RTT_REFRESH_TOKEN>` →
   `{"token": "<access_token>", "validUntil": "<ISO>"}`.
2. All data calls use `Authorization: Bearer <access_token>`. The proxy caches the access
   token and refreshes it 60 s before `validUntil`.

**Endpoint used:** `GET /gb-nr/location?code=<CODE>&timeFrom=<ISO8601>&timeWindow=<minutes>`
- `code=TWYFORD` — trains that CALL or PASS Twyford **with a WTT timing point** (Relief/
  stopping only, ~13 services). Fast Main-line trains have no Twyford timing point and never
  appear here.
- `code=RDG` (Reading) — **bi-directional predictor**, the only feed that surfaces fast
  Down-Main expresses. Reading is ~4 min from Twyford: UP trains pass Twyford *after* Reading
  (offset +); DOWN trains pass *before* arriving Reading (offset −).

**Each service returns:** `scheduleMetadata` (`uniqueIdentity`, `trainReportingIdentity`=headcode,
`operator.code`, `inPassengerService`), `locationMetadata` (`line.planned`, `platform.planned`,
`numberOfVehicles`), `temporalData.{arrival,departure,pass}` (`scheduleAdvertised`,
`realtimeActual`, `realtimeForecast`, `isCancelled`, `realtimeAdvertisedLateness`), and
`origin`/`destination` arrays (each with `location.description` and `temporalData`).

**Gotchas (hard-won):**
- `locationMetadata.line.planned` is **station-specific**. At **Reading** it's the real line
  code: `UML`/`DML`/`ML`=Main, `RL`/`URL`/`UDL`/`DDL`=Relief, but `WL`/`FVL`/`BUS` are
  Reading-throat codes that say nothing about the Twyford line (a Penzance Down-Main express
  carries `WL`). At **Paddington** it's a platform-group digit (`1`/`2`/`3`/`4`), NOT a
  direction — this caused a long-standing "Down Main always empty" bug when a `startswith('D')`
  filter was applied to it.
- `realtimeAdvertisedLateness` is often absent even when `realtimeForecast` differs from
  `scheduleAdvertised` — derive lateness from the forecast as a fallback.
- A train more delayed than its scheduled Twyford slot can drop out of the TWYFORD window;
  the Reading query (confirmed=false) is what keeps it visible.

---

## 2. Network Rail Open Data — STOMP push feeds + reference files (the big one)

| | |
|---|---|
| **Account / portal** | https://publicdatafeeds.networkrail.co.uk (the "Open Data Feeds" / DataFeeds account). Register, then enable feeds in the portal. |
| **Credential** | `NR_USERNAME` (the account email) + `NR_PASSWORD` (`.env` / `nr-credentials.env`) |
| **Account state note** | feeds only flow when the account is **Active**; it can lapse — see PROJECT.md "Network Rail Open Data Feeds". |

Two completely different access methods share the same credentials:

### 2a. STOMP push feeds (real-time, the live position source)

| | |
|---|---|
| **Host** | `publicdatafeeds.networkrail.co.uk:61618` (STOMP) |
| **Library** | `stomp.py` (`import stomp`) — installed via pip on the Pi |
| **Connect** | `login=NR_USERNAME`, `passcode=NR_PASSWORD`, `headers={'client-id': NR_USERNAME}`, `heartbeats=(10000,10000)` |
| **Code** | `_nr_stomp_connect`, `_NRListener.on_message`, `_nr_idle_watcher` (disconnects after 90 s with no `/api/trains` poll, reconnects on demand) |

**Topics subscribed:**
- **`/topic/TRAIN_MVT_ALL_TOC`** — TRUST train movements. Message type `0003` = movement; carries
  `loc_stanox`, planned/actual timestamps, train identity. Watched STANOXes: **`74023`** (Twyford,
  all WTT timing-point trains incl. freight) and **`87014`** (Twyford stops → forces immediate RTT
  cache refresh so confirmed pass times appear within ~1 s). Provides freight that's absent from RTT.
- **`/topic/TD_ALL_SIG_AREA`** — Train Describer. Two message classes:
  - **C-class** `CA` (berth step): `{area_id, from, to, descr=headcode, time(ms)}`. This is the
    live train-position stream — every train steps berth→berth through it. Filtered to areas
    **D1, D4, D6** (Thames Valley Signalling Centre — Reading, Hayes, Maidenhead). Also `CB`
    (cancel), `CC` (interpose), `CT` (heartbeat).
  - **S-class** `SF` (signal flag): `{area_id, address, data}` — signal aspect bitmask, used by
    `/lineside`.

**What CA gives that nothing else does:** the full berth adjacency chain + per-step timestamps,
including intermediate signal berths that SMART omits (see §2b and the CA-chain learner in
`_ca_observe`/`_rebuild_chain_positions`).

### 2b. HTTP "supporting file" reference downloads (daily, the berth/schedule maps)

Base: `https://publicdatafeeds.networkrail.co.uk/ntrod/…`. **Auth quirk shared by all of these:**
the authenticated request **302-redirects to a pre-signed S3 URL**; you must do it in two steps —
first request WITH `Authorization: Basic base64(user:pass)` to get the `Location` header, then
fetch the S3 URL **WITHOUT** the auth header (S3 rejects both auth mechanisms). Handled by the
`_NoRedirect` urllib handler. Files are gzipped JSON.

| Type param | Endpoint | What it is | Code |
|---|---|---|---|
| `SMART` | `SupportingFileAuthenticate?type=SMART` → `SMARTExtract.json.gz` | **Berth → line + reporting point** map. Each step has `TD`, `FROMBERTH`/`TOBERTH`, `FROMLINE` (U/D/M/R…), `PLATFORM`, `STANOX`/`STANME`, `BERTHOFFSET`. Only covers berths that are TRUST reporting points (platforms/junctions), NOT every signal berth. | `_load_smart` |
| `CIF_FREIGHT_FULL_DAILY` | `CifFileAuthenticate?type=CIF_FREIGHT_FULL_DAILY&day=toc-full` | Daily freight working timetable (~15 MB gz / ~370 MB uncompressed, ~36k schedules). Indexed by headcode for trains with a TWYFORD `pass`. | `_load_cif` |
| `CIF_ALL_FULL_DAILY` | `CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full` | Full WTT incl. passenger (~70 MB gz / ~2 GB uncompressed, ~200k+ schedules — confirmed real size 2026-07-07). Downloaded daily for stock-type display (Power Type/Timing Load/Speed/Category, keyed by headcode) and as a broader origin/dest fallback than the freight-only feed. Both this and `_load_cif` stream-decompress via `gzip.GzipFile(fileobj=resp)` rather than reading the whole file into memory first — the Pi only has 906 MB RAM, nowhere near enough to hold ~2 GB decompressed at once. | `_load_pax_cif` |

**SMART line decoding (GWML platform convention, verified):** platform `1`=Down Main, `2`=Up Main,
`3`=Down Relief, `4`/`5`=Up Relief; `FROMLINE` `U`/`D`=direction. See `_smart_line`.

---

## 3. National Rail Darwin (OpenLDBWS) — departure boards + disruption messages

| | |
|---|---|
| **Endpoint** | `https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb12.asmx` (SOAP) |
| **Credential** | `NR_TOKEN` — a Darwin LDBWS "Lite" token, **hard-coded** in `transport-proxy.py` (line ~27). Current value: `32cf81aa-5b5f-4195-8a02-6dc47bc20ce5`. |
| **Account / portal** | National Rail Data Portal — register for an OpenLDBWS token (separate from the NR Open Data Feeds account in §2). |
| **Code** | `_soap`, `_parse`, `_departures`, `_nrcc` |

**How to call:** POST a SOAP envelope with `SOAPAction: http://thalesgroup.com/RTTI/2015-05-14/ldb/GetDepBoardWithDetails`
and the token inside `<tok:AccessToken><tok:TokenValue>NR_TOKEN</tok:TokenValue></tok:AccessToken>`;
request `GetDepBoardWithDetailsRequest` with `numRows` + `crs` (CRS code, e.g. `TWY`).

**Provides:** public departure boards (used for `/api/departures`) and **NRCC disruption messages**
(`{*}nrccMessages/{*}message`, used for `/api/nrcc` — the Twyford-area alerts strip). This is the
"customer-facing" view; it does NOT give berth positions or pass times for non-stopping trains.

---

## 4. Vail Data — ad-hoc BPLAN + SMART lookups (no credential)

| | |
|---|---|
| **URL** | `https://vaildata.uk/api/bplan/loc/<TIPLOC>` (e.g. `…/TWYFORD`) |
| **Auth** | none, but send a browser `User-Agent` (default/script UAs get **403**). The HTML page (`/bplan/tiploc?loc=…`) is JS-rendered — use the `/api/…` JSON endpoint. |

**Provides per location:** `tps_nodes` (network model nodes with `kmvalue` = **metres from line
origin** on the `elr` — `MLN1`=Main Line, `HEN`=Henley branch — plus lat/long), `tps_data`
(TIPLOC/STANOX/CRS/NLC), and a `smart` array (SMART rows for that STANOX). Use it to look up real
positions / validate chainage without parsing the whole SMART feed. Validated anchors:
Twyford ≈ 49.8 km (MP31.0), Maidenhead ≈ 39.0 km (MP24.2), Slough ≈ 29.6 km (MP18.4).

---

## How the sources combine (mental model)

- **RTT** = *what* trains exist, their identity, route, schedule, realtime lateness (both directions).
- **SMART** = *which line* a berth is on + *where* it is (for berths that are reporting points).
- **CA stream (TD)** = *live position* of every train + the full berth chain (fills SMART's gaps via
  the learner; positions persisted to `berth_chain.json`).
- **TRUST (STOMP)** = confirmed movements + freight; triggers instant RTT refresh at Twyford.
- **CIF** = freight schedules not in RTT.
- **Darwin** = customer departure boards + disruption text.
- **BPLAN (Vail)** = ground-truth positions/mileages for calibration.

Distance model: each berth → `dist_mi` = signed miles from the house (− = east toward London,
+ = west toward Reading), from SMART chainage or CA interpolation. **D6 berths 0475–0594 are
Iver→Maidenhead, EAST of Twyford** (berth 0577 = Maidenhead, ~6.7 mi east — NOT "the house"; an
earlier single-formula calibration got this badly wrong). See PROJECT.md "trains.html" section.

## Non-railway keys in the same `.env` (out of scope here)
- `BODS_API_KEY` — Bus Open Data Service (buses view).
- `LASTFM_API_KEY` — Last.fm (radio now-playing/track info).
