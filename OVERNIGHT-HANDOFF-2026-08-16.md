# Overnight handoff — 2026-08-16

## Leave this running overnight

Keep the browser open at [the train shadow monitor](https://nearby.gdx.org.uk/train-shadow). It polls both models every 10 seconds, keeps the on-demand TD connection active, and appends decisions to the cloud evidence log. Do **not** promote v2, alter model weights, reset the learner, or deploy train-model code overnight.

The default `/trains` and `/now` displays still use `model=legacy`. V2 is opt-in only:

- `https://nearby.gdx.org.uk/trains?train_model=v2&train_shadow=1`
- `https://nearby.gdx.org.uk/now?train_model=v2&train_shadow=1`
- `https://nearby.gdx.org.uk/train-shadow`

## Current train-accuracy evidence

At **2026-08-16 21:55 UTC**, the cloud evidence log contained 302 decision snapshots, 29 TD-verified house crossings and 6 manual heard-pass observations. The independent crossing score is currently a regression:

| Model | Correct / scored | Rate | Median absolute ETA error |
| --- | ---: | ---: | ---: |
| Legacy | 26 / 29 | 89.7% | 13s |
| V2 | 22 / 29 | 75.9% | 13s |

V2 is promising only when its source is `td_eta`: 21/23 correct (91.3%, 13-second median absolute error). Its fallback choices are unsafe: RTT forecast was 1/5 correct with a 1,614-second median absolute error; its one schedule fallback was also wrong.

The four known legacy-correct/v2-wrong crossings are:

| Truth | Row | V2 selection | V2 source | ETA error |
| --- | --- | --- | --- | ---: |
| 9U87 | Up Relief | 9U95 | RTT forecast | +1,751s |
| 9U95 | Up Relief | 2P89 | TD ETA | +246s |
| 9R96 | Down Relief | 9R08 | RTT forecast | +1,439s |
| 2Y09 | Up Relief | 1P88 | Schedule | +1,491s |

Tomorrow’s order of work:

1. Re-read `/api/train-evidence`; compare the overnight sample with this table.
2. Diagnose fallback eligibility/ranking first: a stale RTT forecast or schedule candidate must not displace a credible fresh TD candidate.
3. Investigate the TD-ETA mis-rank at the 9U95 crossing separately; do not hide it by merely increasing global TD weighting.
4. Add replay/regression fixtures for each confirmed disagreement before changing selection logic.
5. Keep v2 shadow-only until it beats legacy on a sufficiently larger independent sample.

Evidence lives only on the cloud VM at `/home/gduthie/joggler/train-evidence.jsonl`. Use the public aggregate endpoint unless individual scoring records are needed:

```bash
curl --fail --silent https://nearby.gdx.org.uk/api/train-evidence
```

## Collector health and deployment warning

The TD collector is running on `cloud.gdx.org.uk` as:

```text
/home/gduthie/joggler/venv/bin/python /home/gduthie/joggler/transport-proxy.py
```

It had PID 364805, started at 20:04 UTC, and was still updating `berth_chain.json` and `signals_learned.json` at 21:55 UTC. However, `joggler.service` does not exist and no matching managed service was visible through `systemctl`; this process is not restart-safe. Do not interrupt it tonight. Tomorrow, establish the intended Supervisor/systemd management and health/restart path before relying on unattended monitoring long-term.

The cloud production directory is `/home/gduthie/joggler`; use the normal cloud deployment documentation, not the Pi. Avoid a broad deployment while the worktree has unrelated changes. For train-model work use `deployment/train-accuracy-shadow.sh`; for display-only Lineside v2 assets use `bash deployment/lineside-geometry-shadow-deploy.sh`.

## Lineside v2 state

The default `/lineside` remains v1. The current display-only shadow is:

```text
https://nearby.gdx.org.uk/lineside?layout=v2
release: lineside-geometry-20260816T215157Z
```

It includes the confirmed Reading-platform correction, Kennet Loop berth `1679`, shared four-line schematic columns, and the reviewed Twyford West/Ruscombe adjacent-track crossover ladders. It is still a review layout: do not promote it without visual comparison against the Tracksy references. The loop geometry and all intermediate berth spacing remain schematic rather than surveyed.

Relevant local sources:

- `TRAIN-ACCURACY-PLAN.md` — implementation and shadow/evidence design.
- `LINESIDE-GEOMETRY-PLAN.md` — v2 geometry, OSM/learner evidence and rollback rules.
- `data/lineside-osm-anchors.json` — reviewed OSM signal/track anchors and signal-spacing audit.
- `data/lineside-layout-v2.json` and `lineside-layout-v2.js` — v2-only layout configuration.

## Verification commands for tomorrow

```bash
python3 -m unittest tests/test_lineside_layout.py tests/test_train_accuracy.py
curl --fail --silent https://nearby.gdx.org.uk/api/train-evidence
ssh gduthie@cloud.gdx.org.uk 'ps -eo pid,ppid,lstart,cmd | grep "[t]ransport-proxy.py"'
ssh gduthie@cloud.gdx.org.uk 'cd /home/gduthie/joggler && stat -c "%n %y %s bytes" berth_chain.json signals_learned.json train-evidence.jsonl'
```

Do not expose credentials or copy `.env`/runtime state into git.
