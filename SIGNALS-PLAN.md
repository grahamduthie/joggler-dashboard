# Plan: Railway signal status on /lineside

Status: **Shipped and learning live (2026-07-08).** Phases 2-3 are folded into
Phase 1 (see "Revised design" below) rather than separate offline steps.
Phase 4 (frontend dots) is done and deployed, including bidirectional-berth
handling (below). Update this line as anything further changes.

Goal: show live signal aspects (red/green heads) on the /lineside diagram for the four
running lines Reading↔Maidenhead, like Traksy (traksy.uk) does.

## Revised design (2026-07-08) — fully automatic, no offline batch, no human oracle

The original plan below (kept for the background/data-source detail, which is
all still accurate) called for: collect a raw JSONL corpus for 2-3 days →
pull it back → analyse offline → have Graham watch Traksy's live map
alongside a debug page to confirm ~6 signals by eye → bake a static
`signals.json`. That was reconsidered:

- **Traksy isn't independent ground truth** — it's a third-party map at
  traksy.uk built from the *same* Network Rail open-data feed this proxy
  already consumes directly. Scraping it (it's subscription-gated for the
  live map anyway) would just be re-deriving our own data through someone
  else's UI. Dropped entirely.
- **The statistical test in the old Phase 2 (steps 1-4: co-fire rate,
  uniqueness, headcode diversity, polarity/occupancy check) is already a
  self-contained proof** — it doesn't need an external oracle or a human to
  eyeball anything. A bit that goes 1→0 within seconds of a train passing a
  berth, consistently, for many different trains, and *only* for that berth
  step, and stays 0 until the train reaches the next berth — that's already
  as certain as a human glancing at Traksy would be. So the human-confirm
  step (old Phase 2 step 6) is simply removed; candidates auto-promote once
  they clear the statistical bar.
- **No separate collection window or offline analysis pass.** Correlation now
  runs incrementally, inline, inside `transport-proxy.py` itself — every CA
  step and SF bit transition updates the model as it arrives (same pattern as
  the existing CA berth-chain learner in the same file). There's no
  `SCLASS_COLLECT` mode, no JSONL corpus, no `td_correlate.py` offline
  rewrite. State persists to `signals_learned.json` next to the proxy and
  reloads on restart, exactly like `berth_chain.json` does.
- **Confidence tiers instead of a binary promote/don't**: every candidate the
  proxy has ever seen is exposed via `/api/td-live`'s new `signal_states`
  list, tagged `tier: "tentative"` (< the promotion bar) or `"confirmed"`
  (cleared it). Phase 4 renders both — hollow/dashed for tentative, solid for
  confirmed — so the display fills in almost immediately and firms up over
  hours/days as more trains pass, with no manual step in between. A
  confirmed signal can also be demoted automatically if later polarity
  evidence turns against it (self-correcting, not a one-shot verdict).
- Busy signals (Up/Down Main through Twyford) should clear the promotion bar
  (≥5 occurrences, ≥3 distinct headcodes, ≥80% uniqueness, polarity check)
  within hours of normal peak traffic. Rarely-used ones (crossovers, Henley
  branch) just sit at "tentative" longer — no different in kind, just slower.

This means **old Phases 2 and 3 are gone as separate steps** — their content
(the correlation algorithm and the decode-at-startup behaviour) is now part
of Phase 1's implementation, done and running. What's left is Phase 4
(frontend).

## Background — where the data comes from

Signal state arrives on the **same NR TD feed we already consume** (`/topic/TD_ALL_SIG_AREA`),
via **S-class messages** interleaved with the C-class (CA/CC) berth steps:

- `SF_MSG` — update: one byte changed. Fields: `area_id`, `address` (2 hex chars), `data`
  (2 hex chars = 1 byte = 8 independent signalling bits).
- `SG_MSG` — refresh: `data` is 8 hex chars = **4 consecutive bytes** starting at `address`.
  A burst of SG messages after (re)connect snapshots the whole area.
- `SH_MSG` — refresh finished (same shape as SG; carries the final bytes).

Key facts (from https://wiki.openraildata.com/index.php/Decoding_S-Class_Data):

- Each bit = one signalling element. Typically **one bit per signal: 0 = red ("on"),
  1 = showing a proceed aspect ("off")**. Binary only — cannot distinguish green from
  single/double yellow.
- Bit labelling convention: bit 0 = LSB of the data byte; label elements `addr bit`
  (e.g. `01 2`). Addresses are hex (`09` → `0a`, not `10`).
- **A berth sits on the APPROACH side of its like-numbered signal.** Berth numbers mirror
  signal numbers. So when a train steps `from=A to=B`, it has just PASSED signal ~A, and
  that signal's bit should flip **1→0 within ~1–5 s** of the CA message.
- Only trust the **1→0 (going red)** direction for correlation. 0→1 (clearing) happens for
  many unrelated reasons (signaller sets a route, overlap clears, etc.).
- Other bits are NOT signals: route settings, TRTS, points. These fire ahead of the train
  or inconsistently — filtered out by requiring the "goes red exactly as the train passes,
  every time, and only then" signature (see uniqueness check below).
- **Nobody has published a decode for TVSC areas D1/D6** (checked 2026-07-08; only ~7 of 61
  TD areas ever decoded publicly). We derive the bit→signal mapping ourselves, live.

## What exists now (2026-07-08, shipped)

`transport-proxy.py`:

- `_handle_td()`: parses CA/CC, **SF** (single-byte update), and **SG/SH** (4-byte
  refresh burst) into `_sf_state[(area, addr)] = {'data', 'ts', 'src'}`. SG/SH seed state
  without being treated as real transitions.
- `_sig_note_bit_transitions()`: diffs each SF update against its prior byte, records every
  1→0 bit flip into a short rolling buffer (`_sig_recent_bits`, 30 s) and a running per-bit
  total (`_sig_bit_total`) used for the uniqueness check.
- `_sig_observe_step()` / `_sig_evaluate_pending()`: on each CA step, schedules a scoring
  pass ~6 s later (after the match window closes) against `_sig_recent_bits`; also resolves
  the *previous* step's polarity check for that headcode (was the matched bit still red
  when the train reached the next berth?). Runs inline from `_handle_td`, so scoring keeps
  up in real time — no batch job.
- **Keyed by berth + direction of travel**, not berth alone: `area|from_berth|direction`
  (`direction` ∈ `east`/`west`, derived by comparing the from/to berths' `dist_mi` via
  `_berth_info` — more negative = eastbound/"up", more positive = westbound/"down"; falls
  back to a directionless `area|from_berth` bucket when dist_mi isn't known for one side).
  Most berths only ever get exited one way, so this just adds a redundant direction tag
  most of the time — but a genuinely bidirectional berth (a train can continue through OR
  reverse back out, e.g. Maidenhead Platform 5, Twyford Platform 4) has TWO different
  physical signals guarding it, one per direction, and pooling them by berth alone (the
  first version of this) would corrupt both candidates by mixing two different bits'
  evidence together. Splitting by direction keeps them as two independent candidate pools
  that can each confirm (or not) on their own.
- `_sig_rebuild_confirmed()`: recomputes, per step key, whether the best candidate
  `(addr, bit)` clears the bar — ≥5 occurrences, ≥3 distinct headcodes, ≥80% uniqueness
  (occurrences / that bit's total 1→0 count anywhere), and (once ≥3 polarity samples exist)
  ≥70% polarity-pass rate. Runs every 60 s from `_sig_refresh_loop`, which also persists
  state to `signals_learned.json` (loaded on startup, mirrors `berth_chain.json`).
- `_sig_decode_states()`: feeds `/api/td-live`'s `signal_states` list — one entry per known
  step key (confirmed candidates always included; otherwise the best tentative candidate if
  it has ≥2 occurrences), each with `berth`, `direction` (`east`/`west`/`null`), `tier`
  (`confirmed`/`tentative`), live `state` (`red`/`off`/`unknown`). No position/line data —
  the frontend already has every berth's cell position and each line's direction hardcoded,
  so it just needs the berth code.
- Existing raw `signals` dict in `/api/td-live` unchanged (now also carries `src`).

Deployed to the Pi (2026-07-08); learning happens automatically whenever the NR STOMP
connection is up — i.e. whenever /trains or /lineside is being viewed, same as the existing
on-demand connection behaviour. No permanent-connection flag needed since there's no
fixed corpus-collection window to satisfy.

- **Scope**: the learner watches every berth in all of `_TD_AREAS` (D1/D4/D6 —
  Reading/Hayes/Maidenhead), not just the ones `/lineside` renders — left this way
  deliberately, see [[project-lineside-signals]] memory for why (possible future use:
  detecting a Reading departure signal clearing as an earlier ETA fix for Twyford).
- **Verified corridor berth topology per line** — used for the direction-bucketing dist_mi
  comparison above — is in memory `reference-smart-bplan.md` and `berth_chain.json`
  (learned adjacency + `dist_mi`, lives on the Pi). `_berth_info(area, berth)` in
  transport-proxy.py returns line/place/dist_mi for a berth.
- History note: /lineside once had 9 speculative signal dots; removed 2026-07-06 because
  their positions used the discredited berth-574=house calibration. Don't resurrect that
  old code (`renderSignals`/`KEY_SIGNALS`/`decodeAspect` from before 2026-07-06 are gone) —
  the current `renderSignals`/`sigByBerth` in lineside.html is a fresh implementation driven
  by `signal_states`, not a revival of that code.
- `td_correlate.py` is superseded/unused for this feature — its correlation logic lives in
  `transport-proxy.py` instead, at bit level, running continuously. Can be deleted (kept as
  historical reference for now).
- **Not yet attempted**: distinguishing a signal's main aspect bit from a "feather"/route
  indicator bit at junctions (Ruscombe Jn, Kennet Br Jn, Henley Br Jn). Likely hard with the
  current timing-signature method alone — a feather probably lights/clears in sync with the
  main aspect, not on a distinguishable schedule — would need keeping multiple confirmed
  bits per berth/direction (currently only the single best one survives) plus using known
  junction locations as a hint. Discussed 2026-07-08, not started.

## Operational constraints

- **Deployment**: transport-proxy.py runs on the Raspberry Pi (172.16.10.136, user
  `gduthie`) at `/home/gduthie/twyford-dashboard/`, as systemd service
  `twyford-dashboard`. Deploy = `scp transport-proxy.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/`
  then `ssh gduthie@172.16.10.136 'sudo systemctl restart twyford-dashboard'`. Logs in
  `dashboard.log` there. See `project-joggler-status` memory for full procedure.
- **NR STOMP is ON-DEMAND**: the proxy connects only while /trains or /lineside pages are
  polling, and disconnects after 90 s idle (`_nr_touch()`). This is fine under the revised
  design — learning just happens whenever the feed is up; there's no fixed window it must
  span, unlike the old plan's 2-3 day requirement.
- `signals_learned.json` persists on the Pi next to `berth_chain.json`; small (one entry per
  step key/candidate bit), no rotation needed.

## Phase 4 — Display on /lineside (shipped 2026-07-08)

`lineside.html` renders from the same `/api/td-live` fetch the page already does every 5 s
(`fetchTd`) — no new polling, no new coordinate system. It reuses the berth panel's
existing cell layout (`CELLS`/`BERTH_CELL`/`LINE_OF`) instead of computing signal positions:

- New `signals-layer` SVG group, drawn on top of everything (trains, occupancy) so a dot is
  never hidden by an occupying train.
- `fetchTd()` builds `sigByBerth[berth] = {east: entry?, west: entry?, any: entry?}` from
  `signal_states`.
- `renderSignals()`: every cell with a resolvable direction (`cell.dir` or its line's `dir`
  via `LINE_OF`) gets one **primary** dot on the downstream edge (right for 'up', left for
  'down'), coloured from whichever of `east`/`west`/`any` matches that cell's own direction.
  Grey/hollow with no backend entry, solid red/green once `state` is known, dashed while
  `tier=='tentative'`.
- **Bidirectional berths**: if the backend also has an entry for the *opposite* direction
  for that berth (real evidence of a reverse-direction departure — e.g. a reversal at
  Maidenhead P5 or Twyford P4), a **secondary** dot renders on the opposite edge too, nudged
  off the track centreline (`cy - 6`) since it sits at the same physical boundary as the
  neighbouring cell's own primary dot (two real signals, facing opposite ways, at ~the same
  point) and would otherwise visually merge with it. No hardcoded list of "special" berths —
  the second dot just appears organically once the backend has real evidence, same
  grey→tentative→confirmed progression as the primary one.
- Joggler constraints: plain SVG shapes only, no CSS filters (`--disable-gpu`, Atom CPU).
  Verified with a headless-Chrome screenshot against the live Pi.
- Deploy is scp of lineside.html only, no service restart (static file).

Acceptance: with a train visibly approaching on /lineside, the signal behind it flicks
red within ~5 s of the train symbol passing it, and clears back green shortly after.
Signals with little evidence so far show as dashed/hollow rather than not rendering at all.
A bidirectional berth shows two dots, one per direction, once both have evidence.

## Limitations (accepted)

- Red vs "off" only — no yellow/double-yellow distinction.
- Coverage grows over time; low-evidence signals render as tentative until enough trains
  have passed them, rather than not rendering at all.

## Optional follow-up

- Once a step key has been `confirmed` for a long stable period, optionally snapshot it into
  a committed `signals.json` as a seed/fallback (not required — `signals_learned.json` on the
  Pi already survives restarts — but useful if the Pi's disk is ever wiped, or to publish the
  decode).
- Publish the validated D1/D6 decode on the Open Rail Data wiki (they ask decoders to share).
- Delete `td_correlate.py` once Phase 4 is confirmed working end-to-end.
