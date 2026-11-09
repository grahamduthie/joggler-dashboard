# Handoff — 2026-11-09

## Current state

Branch `main`, tracking `origin/main` at `git@github.com:grahamduthie/joggler-dashboard.git`.
The cloud recovery and Weather moon panel from the previous checkpoint remain deployed. This session
adds a departure-tile display change and deploys it to cloud production; the change is not yet
committed or pushed. No unrelated local work is deliberately excluded.

The Joggler is a thin client at `172.16.10.168` (`of`); production runs under Supervisor program
`joggler` on `cloud.gdx.org.uk` (`gduthie`), in `/home/gduthie/joggler`. The Pi is rollback-only.
See [PROJECT.md](PROJECT.md) and [CLOUD-MIGRATION-PLAN.md](CLOUD-MIGRATION-PLAN.md).
[August overnight notes](OVERNIGHT-HANDOFF-2026-08-16.md) are historical, not current restrictions.

## Current task — Darwin cancellation/ETD discrepancy

The user observed Twyford → Abbey Wood trains marked cancelled on the Joggler despite Darwin ETDs
and later stops appearing on time. They asked the Train tile to keep Darwin's `etd` visible while
also indicating that Darwin's `isCancelled` flag is set. `dashboard.html` now displays the ETD as
before and adds a red `isCancelled: true` marker beneath it whenever `svc.cancelled` is true. The
status column is colored cancellation red in that case. The cancellation reason remains in the
expanded row.

Deployed via `./deployment/cloud-deploy.sh`; Supervisor restarted and the loopback health endpoint
returned `ok`. The live `https://nearby.gdx.org.uk/dashboard.html` contains the new marker. The
attempt to hard reload Chromium on `of@172.16.10.168` via SSH did not complete (timed out without
output), so the Joggler may need a manual hard reload with Ctrl+Shift+R before the display updates.
No tests were run for this small frontend change. Do not assume the physical screen has refreshed.

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

1. Commit the scoped `dashboard.html` and `HANDOFF.md` changes on `main`, then push to `origin`
   if the branch is still fast-forwardable. Check the staged diff before committing.
2. If useful, confirm the Joggler has refreshed `https://nearby.gdx.org.uk/dashboard.html` (the
   earlier SSH Ctrl+Shift+R command timed out); verify an on-time ETD can appear beside the red
   `isCancelled: true` marker.
3. If investigating stability, observe the backend thread count
   over a longer period and especially through NR feed outages. The guard fixes reconnect
   amplification; it does not redesign cleanup of failed connections or all start/stop races.
4. For backend health, use `sudo supervisorctl status joggler`, `ps -o pid,nlwp,rss,etime -p PID`,
   and `/var/log/supervisor/joggler.err.log` over SSH. Do not infer health from RUNNING alone.
5. Future display work should preview locally at 800×480, preserve the larger labels, and attach
   to the live browser with the rule above. Do not deploy ordinary changes to the Pi.

Temporary preview/reload scripts and screenshots are in `/tmp/joggler-*` on the Mac and are not
part of the checkpoint; they are disposable. A Puppeteer installation already available at
`/Users/gduthie/Programming/Hyperframes/marlow-map-demo/node_modules/puppeteer-core` was used.
No credentials, runtime caches, screenshots or temporary scripts are included in Git.
