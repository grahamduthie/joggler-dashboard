# Handoff — 2026-11-08

## Current state

Branch `main`, tracking `origin/main` at `git@github.com:grahamduthie/joggler-dashboard.git`.
This checkpoint includes the cloud recovery, Weather moon panel, vendor license, and documentation.
All requested work is implemented and deployed. No unrelated local work is deliberately excluded.

The Joggler is a thin client at `172.16.10.168` (`of`); production runs under Supervisor program
`joggler` on `cloud.gdx.org.uk` (`gduthie`), in `/home/gduthie/joggler`. The Pi is rollback-only.
See [PROJECT.md](PROJECT.md) and [CLOUD-MIGRATION-PLAN.md](CLOUD-MIGRATION-PLAN.md).
[August overnight notes](OVERNIGHT-HANDOFF-2026-08-16.md) are historical, not current restrictions.

## Completed this session

- **Cloud 502 recovery:** backend was nominally RUNNING with 9,386 threads and repeated
  `RuntimeError: can't start new thread`. Each NR STOMP heartbeat/disconnect callback spawned
  another reconnect loop. `transport-proxy.py` now routes both through `_nr_schedule_reconnect()`:
  a lock and a tracked worker coalesce concurrent callbacks; intentionally idle feeds do not restart.
  Deployed with `./deployment/cloud-deploy.sh`, which restarted the exhausted backend.
- **Weather Now:** `dashboard.html` divides the top-right sun area into sun and moon panels.
  Sunrise/sunset and current daylight progress remain; phase name, shaded moon and illuminated
  percentage are added. SunCalc 1.9.0 is vendored under `vendor/` with its BSD license, from
  `https://github.com/mourner/suncalc/tree/v1.9.0`. Computation runs locally every ten minutes.
  Waxing is lit on the right and waning on the left, following the UK display convention.
- **Readability requirement:** sunrise/sunset times now render about 14px at 800×480;
  moon phase is 12px, illumination 11px; long names wrap. Do not shrink these to fit future changes.
- **Live kiosk:** reloaded and switched to Weather via its SSH-tunnelled Chromium debugger.
  Confirmed phase text (`New moon`, 1% illuminated at the time).
- **Viewport repair:** our Puppeteer connection initially applied its default 800×600 viewport,
  creating a 20px top margin and clipping the bottom. Repaired using an explicit 800×480 viewport;
  live screenshot and DOM confirmed body `(0,0)` through `(800,480)` and full forecast visibility.

## Critical browser inspection rule

Always connect to the live kiosk with `puppeteer.connect({browserURL: ..., defaultViewport: null,
protocolTimeout: 90000})`. Without `defaultViewport: null`, inspection changes the page geometry.
CDP is loopback-only; tunnel with `ssh -N -L 19322:127.0.0.1:9222 of@172.16.10.168`.
Do not apply local preview viewport settings to the live device.

If the viewport becomes wrong, `await page.setViewport({width:800,height:480,deviceScaleFactor:1})`
worked; `Emulation.clearDeviceMetricsOverride` alone did not. Wait for `innerHeight === 480` and
body rectangle `y === 0`, then inspect the screenshot. Reloading alone may retain emulation.

## Checks actually completed

- `python3 -m unittest discover -s tests -p 'test_*.py'`: **56 tests passed**.
- Parsed `transport-proxy.py` with Python AST; compiled inline dashboard JS and SunCalc with
  Node `vm.Script`: passed.
- Isolated scheduler exercise: 100 concurrent callers created exactly one reconnect worker;
  setting `_nr_running=False` suppressed another worker. No full upstream-failure soak test.
- Local headless browser at 800×480: no page JavaScript errors, rendered and inspected sun/moon;
  simulated Waning gibbous label fits. 390×844 phone portrait layout fits after waiting for the
  responsive profile handler. An initial phone assertion ran before that handler and failed;
  corrected the check to wait for the profile and it passed.
- Production deployment health check passed; public dashboard and SunCalc asset returned HTTP 200.
- Final cloud read-only check: `joggler` RUNNING, PID 1295408, seven threads, uptime 7m30s;
  local `/health` returned `ok`. PID is diagnostic history, not a fixed operational identifier.
- Actual kiosk screenshot after repair verified 800×480 with no top margin or bottom clipping.

## Next actions and limitations

1. No immediate user request remains. If investigating stability, observe the backend thread count
   over a longer period and especially through NR feed outages. The guard fixes reconnect
   amplification; it does not redesign cleanup of failed connections or all start/stop races.
2. For backend health, use `sudo supervisorctl status joggler`, `ps -o pid,nlwp,rss,etime -p PID`,
   and `/var/log/supervisor/joggler.err.log` over SSH. Do not infer health from RUNNING alone.
3. Future display work should preview locally at 800×480, preserve the larger labels, and attach
   to the live browser with the rule above. Do not deploy ordinary changes to the Pi.

Temporary preview/reload scripts and screenshots are in `/tmp/joggler-*` on the Mac and are not
part of the checkpoint; they are disposable. A Puppeteer installation already available at
`/Users/gduthie/Programming/Hyperframes/marlow-map-demo/node_modules/puppeteer-core` was used.
No credentials, runtime caches, screenshots or temporary scripts are included in Git.
