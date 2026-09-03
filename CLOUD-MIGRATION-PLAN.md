# Cloud migration plan — Dashboard and Nearby

**Status: live in production; the Pi backend is retained stopped as a rollback path.**

This document is the working record for the completed move of the Joggler dashboard backend from
the home Raspberry Pi to the shared GDX cloud VM. Update it when a decision is made, a deployment
stage starts or finishes, or a material assumption is disproved. It distinguishes live cloud
production from the temporary Pi rollback and the independent TrainPi hardware services.

## Goal

Run the dashboard's internet-facing backend on the GDX VM alongside Cruise Tracker and
RailRouter. The O2 Joggler remains a thin Chromium client. The unrelated TrainPi LED-display
software remains on the Pi because it depends on local hardware.

The move reduced pressure on the Pi (the dashboard previously used about 276 MiB RSS there) and
makes the dashboard and nearby-transport views accessible at stable HTTPS URLs.

## Agreed public URLs

| Host | Canonical content | Intended role |
|---|---|---|
| `https://dashboard.gdx.org.uk/` | Main `dashboard.html` application | Household dashboard now; responsive desktop/tablet/phone dashboard later |
| `https://nearby.gdx.org.uk/` | `nearby.html` | Public Nearby hub; navigation for Aircraft Nearby and the Twyford views |
| `https://nearby.gdx.org.uk/now` | `now.html` | Public combined aircraft/Twyford view |
| `https://nearby.gdx.org.uk/lineside` | `lineside.html` | Public Twyford lineside view |
| `https://nearby.gdx.org.uk/aircraft` | `aircraft.html` | Public, location-capable aircraft view |
| `https://nearby.gdx.org.uk/trains` | `trains.html` | Public Twyford trains view |

Requests for the four standalone pages on `dashboard.gdx.org.uk` should redirect to the canonical
`nearby.gdx.org.uk` URL. The public GDX landing page links directly to Aircraft Nearby
(`https://nearby.gdx.org.uk/aircraft`). The SSO-protected Internal apps page includes the existing
Nearby hub as a convenience link, but the hub and every Nearby route remain public and must not
inherit Internal Apps/Dashboard authentication.

## Confirmed infrastructure facts (2026-08-16)

### Shared cloud VM

- Host: `cloud.gdx.org.uk` / `gdxcloud`, Ubuntu 24.04.
- Public addresses: `63.250.53.61` and `2602:ff16:7:125c::1`.
- Capacity at review: 2 vCPU, 7.8 GiB RAM (7.2 GiB available), 135 GiB free disk, no recent OOM
  events. Existing Cruise Tracker and RailRouter processes used about 197 MiB RSS combined.
- Nginx is already the public TLS reverse proxy; UFW allows only SSH, HTTP and HTTPS. Existing
  applications bind to loopback and are managed by Supervisor.
- Nginx listens on IPv4 and IPv6. Use separate Nginx site files and separate Let's Encrypt
  certificates, following the existing `cruisetracker.gdx.org.uk` and `railrouter.gdx.org.uk`
  pattern. Do not change shared Nginx configuration unnecessarily.
- The VM has no swap. A 1 GiB swapfile is optional operational insurance, not a capacity
  prerequisite.

### DNS

All of these names should publish both addresses above (A and AAAA records): `gdx.org.uk`,
`www.gdx.org.uk`, `cruisetracker.gdx.org.uk`, `railrouter.gdx.org.uk`,
`dashboard.gdx.org.uk`, and `nearby.gdx.org.uk`. DNS is managed at IONOS.

At the time this document was created, the authoritative IONOS nameservers returned the correct
records for `dashboard` and `nearby`; public recursive resolvers may retain prior IONOS parking
answers until their former TTL expires. Recheck public DNS before certificate issuance or cutover.

### Home network and devices

- The Joggler reaches the cloud reliably over IPv4; measured cloud ping was about 10 ms on the
  home LAN path. Home IPv6 is intentionally out of scope for this migration.
- The Joggler's local `shutdown-server.py` (`localhost:9999`) and `cast-server.py`
  (`localhost:9998`) must remain on the Joggler. They are not cloud services.
- The Pi backend remains a rollback source during the migration, but
  `twyford-dashboard` was stopped on 2026-08-16. It shares the National Rail durable TD STOMP
  subscription identity with cloud; running both services splits berth messages between them.
  For rollback, stop cloud first, then start the Pi service — never run both dashboard backends
  concurrently.

## Security decisions

Dashboard requires Google SSO for Internet clients. Home-LAN devices are intentionally exempt:
the cloud VM sees them after pfSense NAT as the home's IPv4 WAN address (`82.71.18.37`), so its
Dashboard Nginx vhost returns a successful internal authentication check only for that address.
This keeps the Joggler kiosk login-free while retaining Google authentication everywhere else.
If the WAN address changes, update that one allow-list entry and reload Nginx. Do not broaden this
to an address range or bypass authentication for arbitrary Internet sources.

Nearby is intentionally different: `nearby.gdx.org.uk` is public for all Internet clients. Its hub
shares the visual language of `www.gdx.org.uk`, but that is a presentation choice only; it does not
share the GDX landing site's restrictive CSP or OAuth `auth_request` configuration.

The following protections remain required:

1. Keep the backend on `127.0.0.1` only; Nginx is its sole public entry point.
2. Preserve the existing credentials; **do not rotate credentials merely for this migration**.
   Move existing values into a mode-600 cloud `.env`/state files where code portability requires
   it. Never commit, serve, or log them.
3. Remove the backend's wildcard CORS response header. Browser calls to the backend are
   same-origin and do not need it.
4. Protect upstream-backed API requests with application caching, endpoint allow-lists and, where
   needed, carefully tuned per-route limits. Do **not** apply a small generic per-IP Nginx limit:
   one Dashboard screen legitimately makes several concurrent flight-route and transport requests.
   Public access must not exhaust upstream quotas.
5. Remove or restrict public diagnostic/test endpoints.
6. Make Lineside calibration a validated, rate-limited `POST`; it must accept only current,
   known train data so arbitrary public requests cannot corrupt learned timings.
7. Change the Joggler-local Chromecast and shutdown services from wildcard CORS to an allow-list
   containing `https://dashboard.gdx.org.uk` (and the Pi origin temporarily while rollback exists).

**Outstanding local hardening:** the restricted-CORS source changes exist in this repository, but
the Joggler currently has older `cast-server.py` and `shutdown-server.py` copies. Deploy those two
files to `/home/of/`, restart Chromium/Openbox or the relevant helpers, and test casting and the
power button before marking this control complete.

If Dashboard later controls heating, exposes cameras/presence/location history, adds accounts, or
uses costly APIs, reassess authentication before shipping that feature.

## Required application changes

1. Replace hard-coded `/home/gduthie/twyford-dashboard` paths with a single configurable
   application directory. Apply the same treatment to `hive-setup.py`.
2. Make the listener address and port configurable; cloud production will use
   `127.0.0.1:8002`.
3. Create a reproducible Python environment and explicit dependency list. `stomp.py` is needed
   for the live Network Rail STOMP feeds; `requests` is needed by interactive Hive setup.
4. Preserve existing runtime state during migration: `.env`, `hive-tokens.json`,
   `hive-credentials.json`, `signals_learned.json`, `berth_chain.json`,
   `calibration_log.jsonl`, `bus-stops.json`, `bus-route-stops.json`, `logos/`,
   `aircraft-info/`, and `airport-names.json` where present. Do not copy logs as state.
5. Solve HTTPS radio before cutover. HTTP streams are mixed content when the page itself is HTTPS.
   Prefer HTTPS stream replacements. For streams that cannot be replaced, implement a fixed
   allow-listed HTTPS streaming proxy; never implement an arbitrary-URL proxy.
6. Check all external resources against the cloud egress IP: National Rail HTTP/STOMP feeds,
   RTT, BODS/Transport API, bus operator pages, ADS-B sources, CAA/SkyLink, Hive, radio metadata,
   map tiles and WagtailCam. A cloud/VPS IP may receive different rate limits or WAF treatment
   than the home connection.

## Deployment design

### Backend service

- Install under `/home/gduthie/joggler` on the VM.
- Use a dedicated virtual environment and Supervisor program (for example `joggler`).
- Bind to loopback port 8002; confirm with `ss` that it is never publicly listening.
- Configure automatic restart, orderly logs/log rotation, and a local `/health` probe.
- Take a protected nightly backup of cloud-only state. Test restoration into a clean staging
  directory.

### Day-to-day production releases

Run `./deployment/cloud-deploy.sh` from this repository on the Mac. It synchronises code and
static assets to `/home/gduthie/joggler`, deliberately excluding credentials and cloud-generated
state, refreshes the Python dependencies, restarts Supervisor program `joggler`, and verifies
`http://127.0.0.1:8002/health` on the VM. It intentionally does **not** use `--delete`.

After a front-end change, hard-reload the Joggler with
`ssh of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'`. Check
`https://dashboard.gdx.org.uk/health` and the changed public view afterwards. For problems, inspect
`sudo supervisorctl status joggler`, `/var/log/supervisor/joggler.err.log`, and Nginx's logs on the
VM. Do not deploy ordinary Dashboard changes to the Pi; it is rollback-only during observation.

### Monitoring-process caveat — 2026-08-16

During the train-accuracy shadow observation, the cloud collector was found running as a direct
`transport-proxy.py` process while `joggler.service` was absent and no matching managed unit was
visible through `systemctl`. It was actively updating the TD learner and evidence log, so do not
interrupt it during the overnight observation. Before relying on unattended monitoring, reconcile
this runtime state with the intended Supervisor configuration: establish one managed process,
automatic restart, persistent logs, and a verified local health probe. See
`OVERNIGHT-HANDOFF-2026-08-16.md` for the observed process and safe next checks.

### Pi dashboard retirement (after the observation period)

Do this only after at least seven uneventful days, a cold Joggler restart, and a current protected
backup of the cloud runtime state. The Pi's TrainPi software is independent of Dashboard, but its
`train-pi-controller.service` currently has `After=network-online.target twyford-dashboard.service`.
That is only a boot-order dependency; remove the `twyford-dashboard.service` term before retiring
the Dashboard unit.

1. On the Pi, edit `/etc/systemd/system/train-pi-controller.service` to leave
   `After=network-online.target`; then run `sudo systemctl daemon-reload`. Confirm
   `systemctl is-active train-pi-controller train-pi-restart.timer` still reports `active`.
2. Make an off-Pi, checksum-verified archive of `/home/gduthie/twyford-dashboard`, including its
   protected `.env`, Hive files and learned signal state. Do not put that archive in Git.
3. Stop and disable only the Dashboard services:
   `sudo systemctl disable --now twyford-dashboard twyford-cast`. Confirm ports 5001/5002 are no
   longer listening and that `train-pi-controller` remains active. The Pi's `twyford-cast` unit is
   not currently enabled, but disable it explicitly to prevent future accidental activation.
4. Remove `/etc/systemd/system/twyford-dashboard.service` and
   `/etc/systemd/system/twyford-cast.service`, then run `sudo systemctl daemon-reload` and
   `sudo systemctl reset-failed`.
5. Only after the archive and service checks pass, remove the exact directory
   `/home/gduthie/twyford-dashboard`. Reboot the Pi at a convenient time and confirm the LED
   display and `train-pi-controller` operate normally.

Do not remove `/home/gduthie/Bus-Departure-Board`, `train-pi-controller.service`,
`train-pi-restart.service`, or `train-pi-restart.timer`; those are TrainPi. The Joggler's own
`/home/of/cast-server.py` and `/home/of/shutdown-server.py` also remain: they provide local
Chromecast and power-control functions to the cloud-hosted Dashboard.

### Cloud runtime backups

The first manual backup was made on 2026-08-16 at
`/Users/gduthie/Programming/cloud-backups/joggler/joggler-runtime-2026-08-16-182409.tar.gz`,
with adjacent SHA-256 file. Both are mode 600 on the FileVault-protected Mac. It contains only
runtime state: `.env`, Hive credentials/tokens, bus data, berth/signal learning state,
calibration, airport names, logos and aircraft-information cache. Archive structure and JSON
members were verified without exposing their contents. No automated job is installed yet: backup
automation will move to the user's forthcoming always-on machine.

### Nginx

- Create independent site files for `dashboard.gdx.org.uk` and `nearby.gdx.org.uk`.
- HTTP redirects to HTTPS; each HTTPS server proxies only to `127.0.0.1:8002`.
- `dashboard` redirects `/now`, `/lineside`, `/aircraft` and `/trains` to `nearby`.
- `nearby` serves the public Nearby hub at `/` and the four canonical standalone paths. Do not add
  an `auth_request` to this vhost: the link's placement in `www.gdx.org.uk/internal.html` does not
  make its target private.
- Do not copy the GDX landing page's restrictive CSP wholesale: these pages require external map,
  image, audio and API origins. Add security headers incrementally after browser testing.
- Validate with `nginx -t` before every reload. Do not alter global Nginx, firewall, or existing
  application site configuration as part of this work.

## Rollout gates and rollback

Proceed in order; each gate must pass before the next starts.

1. **Portable-code gate** — implement and test configurable paths, listener, state handling,
   dependencies, CORS/local-service hardening, calibration integrity, and radio solution locally.
2. **Private-cloud gate** — deploy to loopback only; verify file permissions, startup/restart,
   service logs, memory/CPU, cache creation and all external API feeds. Observe a scheduled rail
   data refresh because it is the heaviest recurring workload.
3. **Public-host gate** — confirm public DNS A/AAAA answers; install Nginx sites and certificates;
   test IPv4 and IPv6, HTTPS redirects, routes, rate limiting, and that port 8002 is inaccessible
   externally. Test that Nearby cannot expose Dashboard-only functionality as the applications
   diverge.
4. **Physical-Joggler gate** — test cold boot, touchscreen, all dashboard views, radio, casting,
   power button, Hive temperatures, Lineside, trains, aircraft and buses on the real hardware.
   In particular, confirm the secure cloud origin can still make permitted requests to the
   Joggler-local services.
5. **Rollback-ready gate** — retain the Pi application and runtime state for at least one week,
   but keep its dashboard service stopped. Both backends share the National Rail durable TD STOMP
   subscription identity, so concurrent operation splits berth messages. For a rollback, stop
   cloud first and only then start the Pi service. Monitor cloud logs, service health and
   resources over that period.
6. **Cutover gate** — update `kiosk.sh`, `setup-kiosk.sh`, and relevant documentation to point to
   `https://dashboard.gdx.org.uk/`. Keep the prior Pi URL and service as a documented quick
   rollback until the observation period ends.
7. **Completion gate** — only after stable operation, stop `twyford-dashboard` on the Pi.
   Leave TrainPi and its hardware-specific services untouched.

## Known failure modes and responses

| Failure | Response |
|---|---|
| New hostname/certificate does not validate | Do not change the kiosk. Confirm authoritative and public DNS, IPv4/IPv6 reachability and Nginx port-80 virtual host first. |
| A live API works from home but not the VM | Keep the Pi active, capture the cloud-side error without credentials, and adjust the upstream integration/rate limit before retrying. |
| HTTPS page has no radio | Treat it as a cutover blocker; fix the stream origin/proxy and test sustained playback. |
| Joggler cannot load or operate the cloud page | Restore its Pi URL immediately; retain diagnostics and test the exact browser/CORS/TLS issue before another cutover. |
| Cloud outage or home internet outage | Use the retained Pi backend as the short-term rollback. A permanent kiosk health-check/fallback to the Pi is an optional resilience enhancement to decide after migration. |
| Nginx change affects another cloud app | Revert the new site file/symlink and reload only after `nginx -t` passes; avoid global configuration edits. |
| Public traffic consumes upstream quota | Prefer caches, endpoint allow-lists and per-route limits sized to real Dashboard bursts; do not expose a generic upstream proxy. |

## Landing page follow-up

After both new hosts are live, update the source-controlled GDX landing assets in
`/Users/gduthie/Programming/Lovable/cruise-fare-voyager/deployment/gdx-landing/` with links to
Dashboard and Nearby. Follow that repository's `deployment/GDX_LANDING.md` deployment procedure.
Those files currently have unrelated uncommitted user work; do not overwrite or casually deploy
them during this migration.

## Change log

- **2026-08-16 — VM swap safety net:** Added a 1 GiB `/swapfile` on the ext4 root filesystem,
  enabled immediately and persistently through `/etc/fstab`, and set persistent
  `vm.swappiness=10` in `/etc/sysctl.d/99-joggler-swap.conf`. It is unused after activation. The
  prior `/etc/fstab` was retained as `/etc/fstab.pre-joggler-swap-2026-08-16`. This is emergency
  headroom, not capacity to rely on; investigate any sustained swap use.
- **2026-08-16 — post-cutover capacity check:** VM load average was 0.07 on 2 vCPU; 7.0 GiB of
  7.8 GiB RAM and 135 GiB disk were available. `joggler` used about 206 MiB RSS; all three
  Supervisor applications together used about 389 MiB RSS. All services were running, Joggler had
  no recorded Supervisor errors, and it remained bound only to `127.0.0.1:8002`. The VM has ample
  capacity for further modest web applications, subject to monitoring because it has no swap.
- **2026-08-16 — dashboard rate-limit correction:** Initial public testing showed HTTP 429
  responses when the Joggler opened the transport view. The generic `joggler_api` Nginx limit
  (30 requests/minute per public IP) was too low: a normal screen load concurrently requests
  flight routes, departures and bus data. It was removed from the Dashboard and Nearby sites and
  Nginx was validated/reloaded. Subsequent Joggler requests to trains, departures, buses, flights
  and flight routes returned HTTP 200. Retain caching and endpoint restrictions; introduce only
  measured, route-specific limits if abuse becomes a concern.
- **2026-08-16 — public publishing and kiosk cutover:** `dashboard.gdx.org.uk` and
  `nearby.gdx.org.uk` received separate Let's Encrypt certificates and source-controlled Nginx
  reverse-proxy sites. Browser GET checks for Dashboard, Nearby and all four Nearby views returned
  HTTP 200; IPv6 Nearby access was also verified. The public GDX landing page now links to both
  hosts. The Joggler kiosk launcher now uses `https://dashboard.gdx.org.uk/`; the previous Pi
  backend remains running as the rollback path for the observation period. The Joggler's
  remote-debugging endpoint was unavailable after relaunch, but Chromium is running with the
  correct URL and HTTPS reachability from the device was verified before cutover.
- **2026-08-16 — private-cloud staging:** Deployed `/home/gduthie/joggler` under the new
  Supervisor `joggler` program, bound only to `127.0.0.1:8002`. Health, departures, aircraft,
  Hive and BODS smoke tests all returned HTTP 200. No public Nginx host or kiosk URL was changed.
  Observations: `signals_learned.json` copied from the Pi is malformed and was not loaded; retain
  the Pi as rollback and investigate/rebuild that non-critical learned cache before cutover.
  `airplanes.live` returns HTTP 403 from the cloud egress IP; the aircraft endpoint succeeded via
  its configured ADS-B failover, but monitor the remaining providers and do not assume the first
  source is usable on the VM.
- **2026-08-16:** Plan created. DNS records for Dashboard/Nearby were configured at IONOS; verify
  public propagation before TLS work. User selected public no-login pages, no credential rotation,
  and IPv4-only home networking for now.
