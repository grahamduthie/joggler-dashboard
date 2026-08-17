# Plan: Make `/trains` and `/now` Accurate and Self-Correcting

## Handoff — end of session 2026-08-17

Read this section first. Everything below it is the detailed, dated change log this was built
from -- go there for the "why" behind any specific fix, this section is only the "what's true
right now and what to check next."

**Deployed and stable.** All fixes this session are committed, pushed to `main`, and live on
`cloud.gdx.org.uk` (`joggler` under Supervisor, `127.0.0.1:8002`, published via
`dashboard.gdx.org.uk`/`nearby.gdx.org.uk`). No pending code changes. `python3 -m unittest
tests/test_train_accuracy.py tests/test_lineside_layout.py` is green (53 + 6 tests).

**Current accuracy** (`curl -s https://nearby.gdx.org.uk/api/train-evidence`, 291 crossings scored
as of this handoff): legacy 88.3% correct headline (14s median error), v2 79.0% (14s). **V2 must
not be promoted.** `/api/trains` (the public API every page uses) always serves the legacy
projection regardless -- this has been true since the "one visible model" consolidation earlier
in the session and nothing since has changed it.

**What actually shipped this session, roughly in order:** consolidated to one visible train model
and one `/lineside` layout (removed the `/train-shadow` page and all public model/layout toggles;
legacy-vs-v2 comparison keeps running invisibly in the background regardless); a `ranked` tiered-
source candidate-selection prototype (`_select_headline_candidate`/`_SOURCE_TIER`) validated
against the evidence log but **not wired into anything live**; full candidate-set + `td_unmatched`
reason logging added to the evidence pipeline; found and fixed the `cutoff_pos` bug that silently
dropped held-train TD positions before their own 600s tolerance could apply; made the ETA speed
model train-class-aware (CIF booked speed + a new per-class-per-line learned-speed EWMA,
`_ca_class_speed` in `berth_chain.json`); found and partially fixed Up Relief's dominant v2 failure
mode (a near-house dwelling train falling through to generic `'scheduled'`/ineligible instead of
`'passing'`); a run of live-reported misclassification bugs, each fixed at its actual data root
rather than papered over in the frontend: ECS-as-freight (twice -- once for the `'5'` digit, once
generalised to any digit CIF can positively identify as passenger stock), a held train's ETA being
able to beat its own booked departure, light locomotives showing Elizabeth Line purple, the
class-`'0'` exclusion being too broad in one direction (hid genuine light engines) then too narrow
in the other (let rail-replacement buses onto the approach list), a non-headcode TD descriptor
(`'CAMS'`) that could have been synthesised into a fake predicted train, and a train still at its
own Twyford platform berth showing as passed -- **Up direction only** (`_TWY_PLATFORM_BERTH`/
`_at_twy_platform_berth`); this one was first shipped applying to all four lines "by the same
logic" and had to be corrected the same evening after being asked directly whether DR/UM/DM got
the same treatment -- Down's house-crossing happens on arrival, *before* the platform dwell
(opposite of Up), so the Up-direction assumption doesn't hold there and applying it would have
suppressed real passed statuses for Down Relief/Down Main. Also: synced `/lineside`'s approach
list to redraw on the same 5s cycle as the berth panel instead of lagging on `/api/trains`'s 15s
cycle; renamed generic `HELD`/`AT STN` to name the actual platform (`AT RDG`/`AT MAI`/`AT TWY`)
when that's what's really happening. Full detail on every one of these is in the dated entries
below, each written at the time with the live report that prompted it.

**Open items for next time, roughly in priority order:**

1. **Up Relief (v2) is still the worst-performing row by far** -- 49.3% correct (35/71), 62s
   median error, versus 82-91% everywhere else. Multiple fixes landed against it this session
   (the `'passing'`-state fix, the platform-berth fix) and it is *still* clearly broken. Don't
   assume it's understood: pull fresh `by_row` data (`curl -s
   https://nearby.gdx.org.uk/api/train-evidence`) and, for the wrong crossings, the same
   candidate-tracing method used to find every other bug this session (see "Verification commands"
   below) -- there is likely a fourth distinct bug still to find on this specific row.
2. **Unverified anomaly, needs checking, do not assume it's real or fixed:** `freight_class387` in
   the class-speed learner (`berth_chain.json`'s `class_speed`) had 26/8 samples (Relief/Main)
   before the ECS-generalisation fix and 28/9 after it -- small further growth that a reading of
   the current `_speed_class_bucket` code says shouldn't be possible (a resolved
   `_TIMING_LOAD_CLASS` match returns `'passenger_class...'` unconditionally, before `is_passenger`
   is even consulted). Either this is stale pre-fix EWMA data being misread, or there's a remaining
   path into that bucket that hasn't been found. Worth five minutes with the same live-tracing
   method before trusting either explanation.
3. **Known, bounded, NOT yet fixed:** a train's first 1-2 TD sightings at certain Reading
   platform/throat berths (e.g. 1694, 1702) are invisible to corridor synthesis (`no_direction` in
   `td_unmatched`) until it takes a step where distance-based direction becomes measurable --
   confirmed via 3T60's own trace. Self-resolves within about one more berth-step, so impact is
   bounded, not a permanent miss like the bugs that got fixed. A real fix (inferring direction from
   the learned CA chain's dominant successor for these specific well-established feeder berths) was
   scoped but deliberately not implemented -- it's a judgement call with real edge-case risk (not
   every ambiguous berth is a guaranteed one-way feeder), left for the user to decide is worth it
   rather than assumed.
4. **The `ranked` model prototype is real and validated but sitting unused.** It directly
   implements TRAIN-ACCURACY-PLAN section 7's own target design (tiered source ranking) and a
   backtest showed it would have fixed v2's worst-magnitude wrong picks. It was deliberately not
   promoted or even wired into the evidence pipeline as a third scored model, because Up Relief's
   dominant failure turned out to be a different bug (state/eligibility, not ranking) -- worth
   revisiting once item 1 above is actually understood, not before.
5. **Process note for whoever picks this up:** the Up/Down platform-berth mistake in item-1's fix
   happened because a rule confirmed correct for one line (Up Relief, from a direct live report)
   got generalised to the other three "by the same logic" without checking whether the underlying
   physical asymmetry (`_detect_house_event`'s Up-departs/Down-arrives docstring) actually applies
   to all of them. It was only caught because the user asked directly. Don't repeat this: when a
   fix depends on a directional or line-specific physical fact, check it against each line
   separately before generalising, not after.

**Verification commands:**
```bash
curl -s https://dashboard.gdx.org.uk/health
curl -s https://nearby.gdx.org.uk/api/train-evidence | python3 -m json.tool   # overall + by_row + by_source
ssh gduthie@cloud.gdx.org.uk 'sudo supervisorctl status joggler'
ssh gduthie@cloud.gdx.org.uk 'cat /home/gduthie/joggler/train-evidence.jsonl' > /tmp/evidence.jsonl
ssh gduthie@cloud.gdx.org.uk 'python3 -c "import json; d=json.load(open(\"/home/gduthie/joggler/berth_chain.json\")); print(d.get(\"class_speed\",{}))"'
```
For tracing one headcode's whole story (candidates it beat/lost to, why it was or wasn't
synthesised): pull the evidence log as above, then `grep` for the headcode and parse the
`decision`/`score`/`td_unmatched` JSON lines with Python -- this is the method that found every
bug fixed this session; see the dated entries below for worked examples of the actual filter/print
logic used each time. Deploy with `./deployment/cloud-deploy.sh` from the repo root after any
change; it runs `python3 -m unittest tests/test_train_accuracy.py tests/test_lineside_layout.py`
implicitly only in the sense that you should run it yourself first -- the deploy script itself
does not.

**Post-handoff addendum, same evening — a train still dwelling at its own Twyford platform berth
could show as passed. UP DIRECTION ONLY, see correction below.** Reported live: 9U97 (Up Relief)
dropped off "next past the house" and into the passing log while still at Twyford -- "it is only
passing now". Root cause traced in the evidence log: RTT's own `at_station` computation, and the
`observed_pass_ts` it derives from a minute-precision actual-departure timestamp (+15s), have no
live berth confirmation behind them and can flip a STOP call away from `at_station` before the
train has genuinely left -- confirmed in 9U97's own trace, `at_station` flipped to `'passing'`
roughly 35s before the real TD step out of the platform berth arrived. Two-part fix, because the
state label alone isn't what the legacy-projection frontend (all three pages, since the "one
visible model" consolidation) actually reads:
- `_td_enrich_trains` (early in the per-train loop, before any 'passed' signal is trusted): if live
  TD confirms the train is still in its own platform berth (fresh, `call_type=='STOP'`), force
  `at_station=True`, clear `observed_pass_ts`, and fall back `display_pass_ts`/`house_pass_ts` to
  the RTT forecast/schedule instead -- this is the fix that actually matters, since
  `legacy_house_pass_ts` (what `/api/trains` serves) is captured right after this function returns.
  A genuine TD crossing, once it actually happens, still overrides this precisely and correctly --
  this only suppresses the premature claim, not real evidence.
- `_finalise_train_state`: the same platform-berth check, checked ahead of every other signal
  including the `at_station` flag itself (which the bug had already cleared upstream), for the
  `movement_state` label v2 diagnostics/scoring reads.
Both keyed off a new `_TWY_PLATFORM_BERTH`/`_at_twy_platform_berth`, using confirmed dwell-EWMA
platform berth codes from `reference-smart-bplan.md`.

**Correction, same evening, asked directly: "have you changed the way DR/UM/DM decide passed?"**
Yes, wrongly. The fix above was first written to apply identically to all four lines ("by the same
logic" for 1637/Down Relief and 1655/Down Main) without checking `_detect_house_event`'s own
docstring first, which states the exact asymmetry that breaks that assumption: an Up train crosses
the house on **departure**, after leaving the platform, so "still in the platform berth" genuinely
means "hasn't crossed yet" -- correct for Up Relief and Up Main. A Down train crosses on
**arrival**, *before* the platform dwell -- a Down train confirmed at its own platform berth has
normally *already* crossed, so the same check would have incorrectly forced `at_station=True` and
cleared a real observed pass for every Down Relief/Down Main stopper the moment it reached its
platform. Fixed by adding `direction == 'up'` to both guards; `_TWY_PLATFORM_BERTH` keeps all four
berth codes as a reference table, but only the two Up entries are ever consulted. Down Relief/Down
Main are untouched by either guard -- they still work exactly as before this whole addendum, which
was already correct for them (that's *why* nothing needed changing there, not an oversight left
for later). Two new tests lock in the Down exclusion specifically. Still not re-verified against a
fresh live crossing post-deploy on any line.

**2026-08-17 — a non-headcode TD descriptor could be synthesised into a fake predicted train.**
Reported live: `CAMS` appeared at Reading P13 (D1/1694). Not a real UK headcode -- those are
always digit+letter+2digits (`9U87`, `0Z47`, `3T60`); `CAMS` has no digit at all. Confirmed by its
own data shape too: no `from` berth (an interpose, not a real step) and no resolvable running
line. TD occasionally reports a non-train administrative/test descriptor in the same `descr` field
a real headcode would occupy, and nothing validated the shape before treating it as one. Added
`_is_real_headcode` (`^[0-9][A-Z][0-9]{2}$`) and gated it **only** in corridor synthesis's
`trains.append()` in `_td_enrich_trains` (`not_a_headcode` skip reason) -- the one place an
unidentified TD sighting turns into a prediction with a `house_pass_ts` that could show up on
"next past the house". Deliberately **not** gated at TD ingestion (`_handle_td`) or `/api/td-live`:
the user wants to keep seeing odd descriptors like this on the berth panel/Reading box, which
render whatever TD reports occupying a berth regardless of what it is -- only the leap from "TD
reported something at a berth" to "this is a train that will pass the house" needed stopping.

**2026-08-17 — light locomotive moves (headcode class '0') were excluded from the corridor
entirely; the real digit-'0' split is light engine ('0Z') vs rail-replacement bus ('0B').**
Reported live in three stages the same day:
1. 0Z47 physically passed the house and never appeared on `/trains` or `/lineside`. Both
   `_td_enrich_trains`'s corridor synthesis and the Reading RTT-predictor loop skipped any
   headcode starting `'0'`, bundled into the same check as the Henley branch (`'2H'`) exclusion
   under a single "out of scope" assumption. Fixed by dropping the whole-class-'0' check from
   both loops, keeping only Henley (still correct -- that branch really is out of scope).
   `_td_unmatched`'s `henley_or_light_loco` skip reason renamed `henley_branch` to match.
2. Then 0Z47 showed up on `/lineside`'s berth panel in Elizabeth Line purple. The berth panel
   renders straight from `/api/td-live` position objects, which carry no `passenger` field at
   all (only `headcode`/`area`/`berth`/`line`/`place`/`dist_mi`), so `opInfo()`'s freight test
   (`t.passenger === false || isFrtHc(hc)`) can't see it there even though the fuller
   `/api/trains` entry does report `passenger: false` correctly -- it fell through to the
   Maidenhead-place fallback (`MAID_PLACES`, "an unmatched sighting there is overwhelmingly
   Elizabeth Line") and got purple. Fixed by giving light locos their own headcode-only check
   (`isLightLocoHc`, `hc[0]==='0'`) and colour in `opInfo()` on all three pages (`lineside.html`,
   `trains.html`, `now.html`), checked ahead of both the freight test and the Maidenhead
   fallback so it doesn't depend on which data shape called it.
3. That fix's undo of step 1 (removing the whole-class-'0' exclusion) turned out too broad: 0B00
   (a Reading-Heathrow rail-replacement bus) then appeared on the approach list with an ETA that
   could never resolve -- a bus is RTT-listed as a bookable public service but is a road vehicle
   with no TD presence to ever confirm it. This was the plan's own original, correct reason for
   excluding class '0' in the first place; the mistake was scoping it to the whole digit instead
   of the specific letter. `'0Z'` (light engine) and `'0B'` (bus) are both real, standard
   headcode conventions for what digit '0' covers. Fixed by excluding `hc.startswith('0B')`
   specifically -- in the RTT-predictor loop (where the actual bug was: buses are RTT-listed,
   trains aren't restricted from being there) and, belt-and-braces, in corridor synthesis too
   (`rail_replacement_bus` skip reason), even though a bus can never produce a real TD sighting
   there anyway.

**2026-08-17 — ECS-as-freight fix generalised: any headcode digit, not just '5'.** Reported live:
3T60 (Reading Traincare Depot -> Paddington, CIF-identified as a genuine Class 387 EMU) showed
`'passenger': false` and was styled/labelled as freight -- the same bug class as the ECS fix
earlier the same day, but on headcode digit `'3'`, which nobody had checked (the crude
`hc[:1] in '129'` fallback only recognises the ordinary-passenger digits, and `_nr_ecs_hc` only
recognises `'5'`). Added `_cif_is_recognised_passenger_stock(hc)`, factored out of
`_speed_class_bucket`'s existing `_TIMING_LOAD_CLASS` check: if CIF resolves a known passenger
EMU/IET fleet number (345/387/800/802) for a working, that's now trusted over any headcode-digit
guess, in all three places `passenger`/`is_passenger` gets set from a heuristic rather than a real
identity. Deliberately **not** added to the two TRUST-buffer *visibility* gates
(`_nr_freight_hc(hc) or _nr_ecs_hc(hc)`, merge filter and `freight_only` STANOX check) -- those
exist to keep ordinary RTT-covered passenger trains out of the TRUST supplementary path
entirely, and nearly every ordinary passenger working resolves a stock class too, so admitting on
that basis there would have flooded TRUST with RTT duplicates instead of fixing anything.

**2026-08-17 — held-berth ETA could beat a train's own booked departure.** Reported live: 9U87
showed "passing in ~4 min" while confirmed sitting at Reading -- its origin -- with its booked
Reading departure still ~13 min away. Root cause in `_td_enrich_trains`'s berth-eta refinement:
a held/dwelling berth position alone can't distinguish "waiting for its own booked departure at
its origin platform" from "held mid-journey at a signal", and the constant-speed-from-here math in
`_berth_eta_to_house_s` treats both as "about to depart right now". Fixed by never letting a
`held=True` result imply a house-pass earlier than the train's own `forecast_pass_ts`/
`scheduled_pass_ts` (RTT already encodes the real booked departure and any known running delay) --
take whichever is later, and set `pass_time_source` to whichever supplied the winning number so
the evidence log stays honest about where a clamped ETA actually came from. Applies uniformly to
every held case, not just origin-platform dwelling, since a mid-journey held train's naive
full-speed-from-here ETA can be similarly optimistic once a signal delay outlasts it.

**2026-08-17 — ECS-as-freight fixed at its actual root, closing the gap the earlier speed-learner
fix (below) only partly covered.** That fix made `_speed_class_bucket` trust a *recognised* CIF
timing-load class over the crude `hc[:1]` heuristic, but only helps when CIF resolves one; a
5xxx ECS working CIF can't identify still fell back to `is_passenger`, which was itself wrong at
the source: `_nr_freight_hc` (`transport-proxy.py`) included `'5'` in its freight range, disagreeing
with the frontend's own `isFreightHc`/`isFrtHc` (which have always excluded it), and every ECS
working seen via the TRUST buffer got `'passenger': False` hardcoded regardless. Fixed by
correcting `_nr_freight_hc` to `hc[0] in '4678'` and adding a proper `_nr_ecs_hc`; the two gates
that used to rely on `'5'` being (wrongly) included for ECS *visibility* -- the TRUST buffer's RTT
dedup filter and the `freight_only` STANOX watch gate -- now check `_nr_freight_hc(hc) or
_nr_ecs_hc(hc)` explicitly, so nothing regresses. `passenger` is now `None` (not `False`) for ECS
everywhere it's set from a headcode heuristic rather than a real RTT/CIF identity, which routes it
to the passenger-side ETA speed/bucket defaults without misreporting it as an ordinary booked
service either. The frontend needed no changes: `opInfo()` on all three pages already checked
`isEcsHc()` before any freight logic, so the *label* was already correct -- this was purely a
backend data-correctness bug in a field (`passenger`) that fed the ETA speed model and the
learner, not the display.

**2026-08-17 — Up Relief root cause found and fixed, via the candidate-logging instrumentation
added earlier the same day.** Once real crossings had accumulated with `candidates` data, every
v2-wrong Up Relief pick showed the identical shape: the correct train sitting at TD berth `1630`
(Twyford P4, `td_dist_mi≈0.1` -- essentially at the house), correctly track-confirmed, but with
`movement_state: 'scheduled'` and `eligible: False`, so v2 fell back to a candidate 5+ miles away
sourced from a stale `rtt_forecast`/`schedule` entry (300-950s errors).

Root cause in `_finalise_train_state`: `_berth_eta_to_house_s` credits at most one berth-step of
dwell as progress; at a near-house platform berth the remaining travel time is tiny (a few seconds
at line speed), so a train that's dwelt there only slightly longer than that produces a small
*negative* ETA well before it's dwelt long enough to be flagged `held`. That negative value is too
large-magnitude for the `±15/+20s` `'passing'` window, but not old enough for the `<-60s` `'stale'`
threshold -- it fell through every specific branch to the generic `'scheduled'` state, which
`_v2_headline_eligible` then discarded via its `ts >= now - 15` fallback. Legacy never hit this:
its eligibility rule doesn't gate on `movement_state` at all, which is exactly why only v2 (and
disproportionately Up Relief, whose Twyford platform berth sits precisely in this dead zone with
real stopping-service dwell time) showed the failure.

Fixed with two changes, not one -- reclassifying the state alone wasn't sufficient, since
`_v2_headline_eligible`'s generic timestamp check would still have discarded it via the same
overshot `display_pass_ts`:
1. `_finalise_train_state` gained a branch: a fresh (`td_berth_age<150`) `td_eta`-sourced position
   within 0.5 mi of the house becomes `'passing'` regardless of the ETA's exact sign.
2. `_v2_headline_eligible` now treats `'passing'` as always-eligible, alongside
   `at_station`/`held`/`held_at_red` -- the categorical state is more trustworthy here than a raw
   timestamp the formula above is known to occasionally overshoot.

Also fixed the same day: the per-class speed learner's `_speed_class_bucket` was classifying
empty-stock moves of recognised EMU/IET classes (e.g. a Class 387 running under a 5xxx ECS
headcode) as freight, because its passenger/freight split originally used a crude `hc[:1]`
heuristic that a recognised CIF timing-load class (345/387/800/802) now overrides -- no freight
service runs that stock, so trusting the class number can't misfire the other way. Confirmed live
in `berth_chain.json` before the fix: `freight_class387` had accumulated 26 and 8 samples that
should have been in `passenger_class387`.

Status: **Core correctness implementation is deployed to cloud production. V2 is currently less
accurate than legacy and must not be promoted.**

**2026-08-17 — ETA speed model now train-class-aware, for both legacy and v2 (this is timing, not
selection, so it applies to whichever train either model has already picked).** The berth-to-house
ETA used a flat constant speed per passenger/freight × Main/Relief (90/60/50/35 mph) with no
distinction between e.g. an IET and a stopping EMU, or a GWR 387 and an Elizabeth Line 345 sharing
a line -- raised by the user directly. Fixed in two layers, both in `_berth_eta_to_house_s` via the
new `_lookup_speed_mph`: (1) CIF's own booked Schedule Speed per working (`_cif_pax_index[...]
['speed']`) was already downloaded for the stock-type display feature but never used for ETA --
now used when available. (2) The CA berth-chain learner (`_ca_observe`) now also derives a real
observed mph per CA step (distance ÷ elapsed time, when both berths have a known position) and
folds it into a new per-class-bucket EWMA (`_ca_class_speed`, persisted alongside the existing
chain data in `berth_chain.json`), which is preferred over CIF's speed once it has ≥5 samples for
that bucket -- it's an empirical average of real running on this specific corridor, which already
reflects curves/junctions/TSRs that a booked figure can't. Class buckets always split
passenger/freight first (so two fleets can never average together just because they share a power
type), refined by CIF stock class where known (matches the existing display-only
`stock_type`/`power_bucket` classification's own class list: 345/387/800/802) or a coarser
diesel/electric/bimode bucket otherwise. Falls back to the original flat constants only when none
of the above is available yet. See PROJECT.md's "Per-class speed learner" and "Live-berth ETA
refinement" for the implementation detail. Not yet done: no way to inspect `_ca_class_speed`'s
current buckets/sample counts short of reading `berth_chain.json` directly on the VM -- worth an
`/api/...` read endpoint (mirroring `/api/calibration`) if this needs regular checking.

**2026-08-17 — public shadow surface removed.** The `train_model`/`train_shadow` query params,
the `/train-shadow` page and `deployment/train-accuracy-shadow.sh` are gone: with a single user
of a "production" site that's still experimental, an extra exposed toggle and monitoring page per
model was more clutter than value. Model comparison is internal-only now — `/api/trains` always
serves `legacy`, and `_evidence_record_snapshot`/`_evidence_score_house_crossing` inside
`_rtt_build_trains()` keep scoring `legacy` vs `v2` in the background regardless of what any
client requests (see `train-evidence.jsonl` / `/api/train-evidence`). Any further candidate model
should be added the same way — computed and scored internally via `_headline_run_keys`, never
wired to a public query param or its own page — until it's actually ready to replace `legacy`
outright. A first candidate along these lines (`_select_headline_candidate` / `_SOURCE_TIER`, a
`ranked` variant implementing section 7's tiered-source rule below) exists in `transport-proxy.py`
but is not yet wired into the evidence pipeline.

**2026-08-17 — Up Relief identified as the concentrated failure, evidence log instrumented
to diagnose it.** A row-by-row breakdown of scored crossings (223 scored) found v2 wrong on
Up Relief **59.0%** of the time (36/61) against 8.9-26.0% everywhere else, and legacy also
worst there (16.4-20.0% on the two Relief rows vs 8.9-14.3% on Main). 19 of v2's 36 Up Relief
misses were sourced from `td_eta` -- its *most* trusted tier -- ruling out the ranking bug as
the cause here: v2 was confident and still wrong about which physical train it was looking at.
Leading hypothesis: Up Relief passes the messiest geography in the corridor (nine parallel
Reading platform/throat berths converging on one anchor, `1676`, plus the ambiguous Kennet Loop
siding `1679` beside it -- see LINESIDE-GEOMETRY-PLAN.md), and v2's conservative track-lock
(only reclassifies within the final 1.6 mi approach, vs legacy's immediate reclassification on
any live sighting) may commit to the wrong candidate early there and never correct it.

The evidence log previously only recorded the winning candidate per row, which can prove a pick
was wrong but not explain *why* -- whether the correct train was never a candidate at all, was a
candidate but on the wrong row/track, or lost to a worse candidate. Instrumented to answer this:
- `_row_candidates()` -- every same-row train per row per model, not just the winner, each with
  `eligible`, `source`, `track_source`/`track_confidence`, `td_area`/`td_berth`/`td_dist_mi`/
  `td_berth_age`. Attached to every decision record as `candidates.legacy`/`candidates.v2`.
- `_td_enrich_trains(..., skip_log=...)` -- every TD-seen headcode that never became a trains
  entry now gets a reason (`stale_fix`, `no_berth_distance`, `no_direction`, `outside_corridor`,
  `endpoints_dont_pass_twyford`, `no_eta`, `henley_or_light_loco`). Attached as `td_unmatched`.
- Legacy's recorded `source` is now the real shared `pass_time_source` (`td_eta`/`rtt_forecast`/
  `schedule`/...) instead of a coarse two-bucket label -- it was too coarse to tell a td_eta pick
  from a schedule-only one when diagnosing a wrong legacy headline after the fact.
- `/api/train-evidence` gained `by_row` and `by_source` (both models; `v2_by_source` kept for
  compatibility). This is what should be checked first after the next data-collection window --
  it would have shown the Up Relief concentration directly instead of needing an offline pull.

**Bug found and fixed while adding the above:** `cutoff_pos` (how old a TD position can be
before `_td_enrich_trains` won't consider it at all) was `now - 300`, but the synthesis loop's
own staleness check a few lines later is `age > 600`, with a comment specifically explaining why
600s (a train held at a red for several minutes is still genuinely there). The 300s cutoff meant
that check was unreachable dead code -- a held train's last position aged out and vanished from
candidacy entirely at 300s, before the 600s tolerance it was written for ever got a chance to
apply. Fixed by raising `cutoff_pos` to `now - 600`; safe because both downstream consumers that
specifically need freshness (`_berth_eta_to_house_s` ETA refinement, `_td_berth_rejects_station`)
already self-guard at 300s. This is itself a plausible contributor to "correct train never
visible" cases and is worth checking against the row/candidate data once it accumulates.

Created: 2026-08-16

Primary files:

- `transport-proxy.py` — source aggregation, train identity, position, timing, state and API
- `trains.html` — four-line train display
- `now.html` — combined aircraft/train display; currently duplicates the train-selection logic
- `lineside.html` — useful reference implementation for physical passed-house detection
- `PROJECT.md` — architecture and operational documentation
- `SIGNALS-PLAN.md` — confirmed berth-exit-to-signal learner design

This document is intended to be executable by a future Codex instance. It records the observed
failure modes, the target model, the order of work, validation requirements and deployment
constraints. Do not treat it as evidence that any of the changes below have already shipped.

## Implementation status — 2026-08-16

Implemented locally in this worktree:

- RTT actual and forecast times are now separate; a forecast no longer populates `twy_actual`.
- The train model carries scheduled, forecast, TD-ETA and observed pass timestamps plus source and
  confidence fields.
- TD house crossings override forecasts and become immutable observed passage evidence.
- Calibration cannot alter an observed pass time.
- `/api/trains` now returns an explicit movement state, post-house evidence, run key, track source
  and track confidence.
- A distant TD berth records `current_track` but only changes the displayed track within the
  final 1.6-mile approach; this is a conservative interim topology guard.
- Confirmed learned signal mappings can qualify `held_at_red`.
- `/trains` and `/now` use the same new `train-display.js` helpers and no longer keep a cached
  prediction in NOW for the old 45–100 second grace.
- Focused regression tests cover forecast/actual separation, calibration protection, physical
  passage state and confirmed-red holds.

Still pending from this plan: full run-aware source merging, crossover-by-crossover topology,
replay fixtures, source-specific recalibration and the cloud shadow/release window. These remain
required before calling the programme fully complete.

### Pre-release controls now implemented

- `/api/trains` defaults to `model=legacy`, a projection matching the deployed browser contract.
- `model=v2` exposes the provenance/state model; `shadow=1` adds a compact, per-track comparison
  of legacy and v2 headline run keys without changing the visible display.
- `/trains` and `/now` remain legacy by default. Append
  `?train_model=v2&train_shadow=1` to validate the v2 view on either page.
- `deployment/train-accuracy-shadow.sh` tests, backs up and deploys only the four train files;
  it is intentionally separate from the whole-worktree cloud deployment script.

### Shadow release record

- **2026-08-16 19:24 UTC:** `train-accuracy-shadow-20260816T192428Z` deployed to
  `cloud.gdx.org.uk`. Supervisor `joggler` restarted cleanly; public legacy and v2 endpoints
  each returned 29 trains, with **0** current headline-row disagreements.
- Cloud rollback backup:
  `/home/gduthie/joggler/release-backups/train-accuracy-shadow-20260816T192428Z`.
- The ordinary kiosk pages still request `model=legacy`; no visible behaviour has changed.
- **2026-08-16 19:27 UTC:** temporary monitor deployed at
  `https://nearby.gdx.org.uk/train-shadow` in
  `train-accuracy-shadow-20260816T192735Z`. Public check: 31 legacy rows, 31 v2 rows and **0**
  current headline-row disagreements. Latest rollback backup:
  `/home/gduthie/joggler/release-backups/train-accuracy-shadow-20260816T192735Z`.
- **2026-08-16 19:39 UTC:** evidence and metrics collector deployed in
  `train-accuracy-shadow-20260816T193917Z`. The cloud append-only evidence log was created and
  recorded its first decision snapshot; no independent TD house crossing had yet occurred to
  score. Latest rollback backup:
  `/home/gduthie/joggler/release-backups/train-accuracy-shadow-20260816T193917Z`.
- **2026-08-16 20:04 UTC:** `/lineside` heard-pass calibration presses added to the evidence
  collector in `train-accuracy-shadow-20260816T200451Z`. They remain separate manual truth
  records while continuing to update the robust timing-offset learner. Latest rollback backup:
  `/home/gduthie/joggler/release-backups/train-accuracy-shadow-20260816T200451Z`.
- **2026-08-16 21:55 UTC — first meaningful evidence gate:** 29 independent TD house crossings
  score legacy at 26/29 (89.7%) and v2 at 22/29 (75.9%), both with a 13-second median absolute
  ETA error. V2's `td_eta` choices are 21/23 correct (91.3%), but its RTT forecast fallback is
  only 1/5 correct (1,614-second median error) and its one schedule fallback is wrong. Keep v2
  shadow-only. Diagnose fallback eligibility/ranking and the separate 9U95 TD-ETA mis-rank before
  attempting a cutover. See `OVERNIGHT-HANDOFF-2026-08-16.md` for the exact scored cases and
  morning procedure.

## 1. Goal

Make the four train rows answer these questions reliably:

1. What is the next real train to pass the house on each physical line?
2. Where is it now, and how strong is that evidence?
3. Is it approaching, held, at Twyford station, passing now, or already past?
4. When is it likely to pass, without allowing an estimate to override a physical observation?

The specific reported symptoms are:

- the wrong train is sometimes selected as the headline train;
- `NOW` sometimes continues flashing long after the train has physically passed;
- `/trains` and `/now` contain duplicated selection logic and can drift independently;
- live TD and learned signal evidence is not fully used to qualify the displayed state.

## 2. Confirmed findings from the 2026-08-16 review

### 2.1 RTT forecasts are stored as actuals

`_rtt_normalise()` currently does this for a Twyford RTT result:

```python
twy_actual = actual_iso or forecast_iso
```

It similarly fills `twy_arr_actual` and `twy_dep_actual` with either `realtimeActual` or
`realtimeForecast`. A forecast is therefore truthy in every place that asks whether an actual
time exists.

Consequences:

- `_td_enrich_trains()` does not replace the forecast-derived time with a live berth ETA because
  its refinement path is guarded by `not t.get('twy_actual')`;
- a genuine TD `at_house` event does not write the observed passage time for the same reason;
- frontends use the short “actual” grace window for a forecast;
- “confirmed” is easy to misread as physically observed, when it often means only that RTT
  returned the service in its Twyford location query.

`PROJECT.md` already documents that `twy_actual` carries a forecast until the time is in the
past. That convention is the root problem and should be retired rather than worked around again.

### 2.2 Calibration is applied to observed passage times

The pipeline computes or observes `house_pass_ts`, then applies `_CALIB_OFFSETS` to every train
with a timestamp. There is no provenance check.

At review time, before the cloud migration, the Pi had these applied offsets:

| Line | Applied offset |
|---|---:|
| Up Relief | +15.5 s |
| Down Relief | -6.85 s |
| Up Main | +30.95 s |
| Down Main | +18.9 s |

If a TD house-crossing event records the true passage at `t0`, Up Main becomes `t0 + 30.95s`.
The frontend’s current `NOW` tail is another 45 seconds, so flashing can continue for roughly
76 seconds after the physical passage.

Calibration is valid only for a prediction. It must never modify an observed RTT actual, TRUST
movement actual, TD zero-crossing, or post-house physical observation.

The existing calibration sample set is also noisy and may mix pre-correction errors with
post-correction residuals. Once timestamp semantics are fixed, old offsets must not be trusted
without re-evaluation.

### 2.3 `/trains` and `/now` ignore physical passed-house evidence

`lineside.html` already has `hasPassedHouse()` and `msSincePassed()` logic. It uses signed TD
distance and direction to remove a train shortly after it is physically beyond the house,
independently of its predicted timestamp.

`trains.html` and `now.html` do not use that evidence. They derive the row state from:

```javascript
diff = trainMs(t) - Date.now()
```

and display `NOW` for `-45s < diff <= +25s`. They therefore continue to trust an inaccurate
timestamp after the physical position has disproved it.

The durable fix is to calculate passage state in the backend and expose it to every frontend.
Porting the `lineside.html` test to both pages is an acceptable short-term safety fix, but must
not become a third duplicated state machine.

### 2.4 Headline selection is timestamp-only

For each line, the frontend:

1. filters by direction/track and a broad time window;
2. sorts by `house_pass_ts`;
3. selects the first non-cancelled train still inside `graceMs()`.

It does not rank physical evidence. A stale schedule-only train can therefore block a train that
has a fresh TD position. A forecast is treated more like an actual than its provenance warrants.

### 2.5 A distant live line can incorrectly decide the line at the house

`_td_enrich_trains()` currently lets any SMART berth line override the inferred `track`. The
current line is physical ground truth for where the train is, but not necessarily for which line
it will use past the house if one or more crossovers remain ahead.

During the review, live train `2N78` was assigned Down Main from a berth approximately 15.3 miles
east of the house. Its original service classification was Relief. Its distant current line does
not prove its eventual line at Twyford.

Line-at-house inference must be topology-aware and must state its confidence.

### 2.6 Record identity is too headcode-centric

Several joins and suppression checks are keyed primarily by headcode. Headcodes are reused, can
change during a journey, and can identify different workings at different times of day. CIF
selection has already been improved to choose the closest scheduled time, but the general merge
pipeline still needs a stable run identity.

No duplicate headcodes or UIDs were present in the live API snapshot taken during the review, so
duplication was not the immediate cause of that snapshot’s selection. It remains a structural
risk and a likely explanation for intermittent wrong identity/route details.

### 2.7 “Confirmed” has multiple incompatible meanings

At present `confirmed=True` can mean one or more of:

- the service appeared in the RTT Twyford query;
- there is a recent TD berth for its headcode;
- TRUST reported it at Twyford;
- the backend detected a physical house crossing.

These must be separate evidence facts. A single Boolean is not adequate for selection or UI.

## 3. Current data flow

The existing pipeline in `_rtt_build_trains()` is approximately:

```text
RTT Twyford query ─┐
RTT Reading query ├─> normalise ─> corridor filters ─┐
TRUST movements ──┤                                  │
CIF schedules ────┤                                  ├─> headcode/time dedup
TD berth buffer ──┘                                  │
                                                     ├─> house_pass_ts
TD berth/house enrichment ───────────────────────────┤
manual calibration offset ───────────────────────────┤
cancellation/identity-change filters ────────────────┤
freshness filter/sort ────────────────────────────────┘
                                                     │
                                                     v
                                               /api/trains
                                                 /       \
                                           trains.html  now.html
```

The core design mistake is collapsing all source timestamps into `house_pass_ts` too early,
then asking the browser to reconstruct operational state from that one value.

## 4. Design principles and invariants

Future work must preserve these rules:

1. **Observation beats prediction.** A post-house TD position or zero-crossing can never be
   moved back into the future by a timetable, forecast, speed model or calibration.
2. **Keep provenance.** Schedule, forecast, estimate and observation must remain distinct.
3. **State is backend-owned.** `/trains`, `/now` and future clients must receive the same selected
   state rather than independently inferring it.
4. **A live position proves present location, not necessarily future route.** Track-at-house
   requires topology-aware inference.
5. **Prefer omission to a confident false statement.** Ambiguous identity or line can be shown
   as unknown/estimated; it must not silently become authoritative.
6. **Use confirmed signal mappings only for operational decisions.** Tentative signal mappings
   remain display/debug information.
7. **Retain useful schedule coverage.** Live TD should improve timetable predictions, not make
   the display empty whenever the TD feed is briefly unavailable.
8. **Do not regress stopping trains.** A Down stopper that has passed the house on arrival may
   legitimately remain `at_station` until departure; “passed house” and “current row relevance”
   are related but not identical for a station call.

## 5. Target backend model

### 5.1 Preserve separate time facts

Each merged run should carry these fields internally. The public API may omit raw fields that
are not useful to clients, but keeping them during the merge is important.

| Field | Meaning | May calibration alter it? |
|---|---|---|
| `scheduled_pass_ts` | Published or derived working time at the house | No; retain raw |
| `forecast_pass_ts` | RTT/TRUST forecast adjusted to the house | Yes, only through an explicit prediction model |
| `berth_eta_pass_ts` | ETA produced from current TD berth and speed/transit model | Yes |
| `observed_pass_ts` | Physical house crossing or authoritative actual | Never |
| `display_pass_ts` | Selected timestamp for presentation | Derived from the fields above |
| `pass_time_source` | `td_crossing`, `td_post_house`, `trust_actual`, `rtt_actual`, `td_eta`, `rtt_forecast`, `schedule`, `cif` | N/A |
| `pass_confidence` | `observed`, `live_estimate`, `realtime_forecast`, `schedule` | N/A |

Do not reuse `twy_actual` for forecasts. During migration, either:

- replace it with `twy_forecast` and a genuine `twy_actual`; or
- retain it only as a compatibility field generated from the new model, with no internal logic
  reading it.

### 5.2 Introduce an explicit lifecycle

The backend should return `movement_state` from this controlled vocabulary:

- `scheduled` — timetable only, not yet inside useful live range;
- `forecast` — realtime forecast exists but no corridor TD evidence;
- `approaching` — fresh TD movement on the approach side of the house;
- `held` — fresh location but no movement, with no confirmed red-signal evidence;
- `held_at_red` — current berth’s confirmed exit signal is red;
- `at_station` — calling at Twyford and currently dwelling;
- `passing` — inside the small presentation window around an imminent/observed crossing;
- `passed` — physical or authoritative evidence says it is beyond the house;
- `stale` — evidence is too old to select as the headline train;
- `cancelled` — cancelled and retained only if the UI explicitly wants it.

Recommended state precedence:

```text
cancelled
  > at_station
  > observed passed/passing
  > held_at_red
  > held
  > approaching
  > forecast
  > scheduled
  > stale
```

For a Down stopping service, an observed house crossing on arrival must not remove it while it
is still `at_station`. Once departure is observed/forecast to have occurred and the station
occupancy no longer supports the dwell, it can leave the row.

### 5.3 Record passage as a durable event

When `_detect_house_event()` returns `at_house`:

- persist or retain `observed_pass_ts` for that run/headcode;
- invalidate the train cache;
- never overwrite it with a forecast;
- never calibrate it;
- expose `passed_evidence='td_crossing'`;
- after the configured visual grace, ensure it cannot remain the headline unless it is a
  stopping train still at Twyford.

If the exact zero-crossing was missed but a fresh TD position is physically post-house, record
`passed_evidence='td_post_house'`. Its timestamp may be bounded rather than exact: the crossing
occurred between the preceding and current CA events. Use the most recent defensible bound for
UI state, but do not label the estimated instant as an exact observed timestamp.

### 5.4 Create a stable run key

Build a `run_key` from the strongest available identity:

1. RTT/CIF schedule UID + operating date, when present;
2. otherwise headcode + operating date + expected Twyford time bucket + direction;
3. include origin/destination when available to disambiguate reused headcodes.

Do not use headcode alone when joining a TD sighting to a scheduled run. Candidate matching must
check:

- expected time proximity;
- direction compatibility;
- corridor position compatibility;
- origin/destination or UID where available;
- known TRUST identity changes.

If no scheduled candidate is safe, retain a TD-only run with unknown identity rather than
attaching the wrong route.

### 5.5 Merge evidence before deciding the display record

Refactor `_rtt_build_trains()` conceptually into:

1. collect source observations;
2. normalise each source without destroying provenance;
3. form/locate run entities;
4. merge source evidence into each run;
5. calculate corridor eligibility;
6. calculate current physical state and line-at-house confidence;
7. calculate ETA/display time;
8. rank/select candidates;
9. serialize the API.

Avoid progressively mutating a single `house_pass_ts` as each source is encountered. That makes
the final value depend on pipeline order and hides why it changed.

## 6. Track-at-house inference

Return both:

- `track`: `Main` or `Relief`;
- `track_confidence`: `physical_final`, `physical_pre_junction`, `booked`, `heuristic`, `unknown`;
- optionally `track_source` and `current_track` separately.

Rules:

1. A berth after the final possible crossover before the house gives `physical_final`.
2. A berth farther away reports `current_track`, but does not automatically overwrite the
   predicted `track` at the house.
3. A measured transition through a known crossover updates the predicted house track.
4. Inside the final approach, physical topology wins over booked RTT/CIF data.
5. Outside the reliable topology corridor, use booked/inferred track and lower confidence.

Use the existing `BERTH_MI`/`_BERTH_MI`, learned berth chain and junction layout documented in
`JUNCTIONS-PLAN.md`. Do not introduce a single arbitrary mileage cutoff if the topology can say
whether a crossover remains available.

## 7. Candidate ranking and row selection

Prefer ranking in the backend, returning either a `display_rank` or already grouped candidates
per line. A suggested evidence ranking is:

1. fresh live TD train approaching on a physically final line;
2. fresh live TD train on a plausible approach line;
3. stopping train genuinely at Twyford;
4. realtime RTT forecast consistent with live upstream evidence;
5. schedule-only passenger service;
6. unconfirmed CIF freight path.

Within an evidence tier, use `display_pass_ts`. Never allow a schedule-only overdue train to
block a fresh live approaching train. A past train is eligible only for a short presentation
grace or a genuine station dwell.

Return a short `selection_reason` in debug/shadow mode, for example:

```json
{
  "selection_reason": "fresh_td_final_approach",
  "pass_time_source": "td_eta",
  "track_source": "berth_after_ruscombe_crossover"
}
```

## 8. ETA calculation and calibration

### 8.1 Source priority

Recommended display-time priority:

1. `observed_pass_ts` for recent/past state;
2. fresh post-house TD evidence for `passed` state;
3. fresh TD berth ETA while approaching;
4. authoritative realtime forecast;
5. schedule plus known lateness;
6. raw schedule/CIF path.

The source priority for state is not identical to the source priority for a future ETA. A train
can be physically observed at a berth while the realtime feed still supplies the better model
for a station dwell; preserve both facts.

### 8.2 Rebuild calibration semantics

Before applying existing calibration offsets to the new model:

1. mark old calibration entries with the model version that generated `predicted_ts`, or treat
   all existing entries as legacy;
2. prevent calibration from affecting `observed_pass_ts`;
3. log both raw prediction and already-applied correction so new samples are not residuals mixed
   with old raw errors;
4. use robust statistics per line and prediction source, not one offset for every source;
5. require a larger clean sample than four if automatic correction is retained;
6. report median absolute error and sample spread, not just median bias.

A single line offset may not be appropriate for RTT forecasts, schedule offsets, passenger TD
speed models and freight TD speed models. Prefer source-specific calibration if the evidence
shows materially different errors.

### 8.3 Use learned transit times where possible

The CA berth-chain learner already records per-berth transit EWMA and sample counts. A future
enhancement should estimate ETA by summing learned downstream transit times along the most likely
path, with separate buckets where data supports them:

- direction;
- Main/Relief;
- passenger/freight;
- optionally stopping/non-stopping.

Keep the constant-speed model as fallback. Do not begin this refinement until lifecycle and
timestamp provenance are correct; otherwise improved ETA arithmetic will still feed the wrong
state machine.

## 9. Use of the signal learner

The signal learner had 318 confirmed mappings at review time, including 258 in D1/D6. It can
improve status accuracy, but should not directly predict a release time.

For a train occupying berth `B` and moving in direction `D`:

1. find only a **confirmed** learned exit-signal mapping for `(area, B, D)`;
2. read the live signal bit;
3. if red and the train has remained in the berth beyond a modest threshold, set
   `movement_state='held_at_red'`;
4. if off/proceed, expose `signal_ahead_state='off'`, but do not promise immediate movement;
5. never use a tentative mapping for row selection, ETA, or held-state decisions.

Useful API fields:

```json
{
  "signal_ahead_state": "red",
  "signal_mapping_tier": "confirmed",
  "held_at_red": true
}
```

The learner distinguishes only red/on from off/proceed. It does not distinguish green, single
yellow, double yellow, or junction feathers.

## 10. Frontend changes

### 10.1 Stop deriving operational state from one timestamp

`trains.html` and `now.html` should consume:

- `movement_state`;
- `display_pass_ts`;
- `pass_time_source`;
- `pass_confidence`;
- `seconds_to_house` if approaching;
- `observed_pass_ts` or `seconds_since_passed` if passed;
- `track_confidence`.

`NOW` should be possible only when:

- state is `passing`; or
- state is `approaching` and a high-quality prediction is inside the lead window.

Once state becomes physically `passed`, stop `NOW` immediately and use at most a 15–20 second
non-flashing “PASSED” grace. Do not preserve the current 45-second flashing tail after an
observed crossing.

### 10.2 Share train presentation logic

The current train-side functions in `trains.html` and `now.html` are nearly copied verbatim.
Extract a small static `train-display.js` module containing pure helpers for:

- track key;
- state/countdown label;
- candidate ordering fallback;
- time formatting;
- confidence/status text.

Keep page-specific DOM construction in each HTML file. Serve the shared file through the existing
static handler and ensure it works in the Joggler’s Chromium version without a build step.

If introducing a shared file is operationally undesirable, add parity tests that load both
implementations and assert identical selection/state outputs. Sharing is preferable.

### 10.3 Make confidence visible but quiet

Suggested status language:

- observed: `passed 19:42:13` or a small solid live indicator;
- live ETA: `~19:42 · live position`;
- realtime forecast: `exp 19:42`;
- schedule only: `sched 19:42`;
- held at confirmed red: `HELD AT RED`;
- ambiguous track: avoid a confident line claim or use a subtle estimate marker.

The four-row display must remain glanceable. Do not turn it into a diagnostics screen.

## 11. Implementation phases

### Phase 0 — Capture fixtures and add diagnostics

Before changing behaviour:

1. Save anonymisation-unnecessary JSON fixtures from `/api/trains`, `/api/td-live` and relevant
   TD events for representative cases.
2. Add an optional debug/shadow output containing source timestamps, chosen source, state,
   track source and selection reason.
3. Put debug output behind an environment flag or query parameter; do not bloat the normal UI.
4. Add deterministic clock injection to pure calculation functions so tests do not depend on
   wall time.

Required fixtures:

- Up Main express passing normally;
- Down Main express with Reading-derived prediction;
- Up and Down Twyford stopping services;
- delayed service whose scheduled Twyford slot is already past;
- train held for several minutes at a red signal;
- train changing Main/Relief near a junction;
- CIF freight path that does not run;
- live TD-only freight/ECS working;
- reused headcode with two same-day schedules;
- TRUST identity change;
- missed exact house zero-crossing but fresh post-house berth;
- TD feed disconnect/reconnect.

### Phase 1 — Separate timestamp provenance

1. Change `_rtt_normalise()` to keep actual and forecast separate.
2. Add the target time fields and `pass_time_source`.
3. Update all internal consumers; do not leave truthiness checks on legacy `twy_actual`.
4. Make TD/authoritative observations immutable winners.
5. Apply calibration only to eligible prediction sources.
6. Keep compatibility serialization temporarily if another page still expects old fields.

This phase should directly address the longest `NOW` errors.

### Phase 2 — Backend lifecycle and physical passage

1. Move passed-house logic into a pure backend function.
2. Incorporate both exact `at_house` events and signed post-house position.
3. Define the stopping-train dwell exception explicitly.
4. Return `movement_state`, `seconds_to_house`, `seconds_since_passed` and evidence source.
5. Ensure cache invalidation occurs on state-changing TD events.

### Phase 3 — Run identity, deduplication and evidence ranking

1. Create `run_key` and source-record matching.
2. Replace broad `train_hcs` suppression with run-aware matching.
3. Merge identity changes into the active run where safe.
4. Rank candidates by evidence before time.
5. Ensure a schedule-only overdue record cannot block a fresh TD candidate.
6. Preserve an unmatched TD-only train rather than assigning uncertain identity.

### Phase 4 — Track-at-house topology

1. Separate `current_track` from predicted `track` at the house.
2. Model whether a crossover remains between the current berth and house.
3. Update track confidence after observed crossover transitions.
4. Verify all four lines against `lineside.html` berth cells and real traffic.

### Phase 5 — Frontend migration

1. Make `/trains` consume backend state.
2. Add the short observed-passage grace and remove timestamp-only `NOW` persistence.
3. Apply identical behaviour to `/now` through the shared helper.
4. Keep the last-fetch/no-data indicator independent from train prediction confidence.
5. Add restrained source/confidence labels.

### Phase 6 — Signal-qualified holds and ETA refinement

1. Add confirmed-signal `held_at_red` qualification.
2. Collect clean prediction errors under the new model.
3. Reassess or reset legacy calibration offsets.
4. Consider chain-transit ETA sums after the state model is stable.

## 12. Test plan

### 12.1 Backend unit tests

Extract pure functions and test at least:

1. A future RTT forecast is never classified as actual.
2. A TD `at_house` event replaces every estimate.
3. Calibration does not alter an observed timestamp.
4. A post-house berth makes state `passed` even if the forecast is in the future.
5. An Up/Down stopper may remain `at_station` after house passage until departure.
6. A stale schedule-only train loses to a fresh live approaching train.
7. A distant current Main berth does not automatically make track-at-house Main.
8. A final-approach crossover updates track-at-house correctly.
9. Two same-headcode workings do not merge when their times/routes differ.
10. A TRUST identity change does not leave the old run as the headline.
11. Confirmed red signal qualifies a hold; tentative signal does not.
12. Feed staleness yields `stale`, not a false observed state.

### 12.2 Frontend tests

Using fixed API fixtures and a controllable clock:

1. `/trains` and `/now` select the same run for each line.
2. `NOW` never renders for `passed`, `held`, `held_at_red`, `at_station` or `stale`.
3. An observed pass changes to `PASSED` immediately and leaves after the configured grace.
4. Forecast and schedule labels are visually distinct from observations.
5. The next live train replaces the prior train without an empty or stale interval.
6. No row continues flashing due solely to calibration.

### 12.3 Replay/integration tests

Build a small replay harness that feeds timestamped RTT/TD/TRUST/CIF fixtures through the merge
and state functions. Assert the selected run and state at each point in the event timeline.

Do not require live Network Rail access for correctness tests. Live feeds are for final shadow
validation, not the only reproducibility mechanism.

## 13. Shadow-mode validation

Run the new model alongside the old one without changing the visible display for at least one
busy weekday and, ideally, 48 hours.

Log only changes and disagreements, not every one-second tick:

```json
{
  "ts": 0,
  "line": "um",
  "old_headcode": "1A00",
  "new_headcode": "1B00",
  "old_state": "now",
  "new_state": "passed",
  "reason": "post_house_td"
}
```

Track these metrics:

- selection disagreements per line;
- number and duration of NOW periods after physical passage;
- ETA error against TD house crossings/manual heard-pass events;
- wrong-line corrections after final-approach TD evidence;
- duplicate/ambiguous run joins;
- schedule-only headline duration after its predicted time;
- percentage of headline trains with fresh live evidence;
- false disappearance of genuine stopping/held trains.

Review disagreements manually against `/lineside`, TD berths and, where available, heard-pass
calibrations. Do not automatically treat the old display as ground truth.

### Current cloud procedure

The first cloud release is a **non-visible shadow release**, not a cutover:

```bash
./deployment/train-accuracy-shadow.sh
```

It runs the focused tests, copies only `transport-proxy.py`, `trains.html`, `now.html` and
`train-display.js`, backs up the prior cloud versions under `release-backups/`, restarts
Supervisor and checks both API models. Normal kiosk URLs continue to request `model=legacy`.

During the observation window, compare these opt-in pages against `/lineside` while recording
the returned `shadow.disagreement_rows`:

```text
https://nearby.gdx.org.uk/trains?train_model=v2&train_shadow=1
https://nearby.gdx.org.uk/now?train_model=v2&train_shadow=1
```

The compact monitor URL is `https://nearby.gdx.org.uk/train-shadow`. It is temporary,
`noindex`, and polls both API models directly; remove its route and `train-shadow.html` after
the shadow window concludes.

Observe at least one busy weekday (preferably 48 hours), including a stopping service, a fast
Main-line pass, a held train and a physical post-house TD crossing. Only then switch the default
frontend request to `model=v2`; retain `model=legacy` as the rollback switch for the first week.

**Current gate (2026-08-16): do not use elapsed time alone as a promotion criterion.** V2 must
first beat legacy on independent TD crossings, including its fallback cases. The present 75.9%
v2 result is a regression against legacy's 89.7%, despite the strong TD-only subset.

## 13.1 Evidence and metrics feedback loop

`train-evidence.jsonl` is an append-only cloud runtime file, deliberately excluded from normal
deployments and git. A changed model decision, plus a one-minute unchanged heartbeat, records the
four legacy and v2 headline choices. When TD detects an independent `at_house` crossing, the
backend scores the most recent pre-crossing decision for both models on the physical row:

- whether its headline headcode matched the crossing train;
- its ETA error in seconds; and
- for v2, the time-source class (`td_eta`, RTT forecast, schedule, etc.).

Each `/lineside` **heard it pass** calibration press is also saved as a separate,
high-confidence `manual_observation` in this evidence log and scores both preceding headline
models. It continues to feed the existing robust per-line calibration-offset learner. Manual
observations never overwrite or pretend to be raw TD events.

`/api/train-evidence` returns aggregate crossing accuracy and median absolute ETA error by model
and v2 time source. `/train-shadow` displays the v2 TD-match rate and crossing count.

This is **evidence collection, not autonomous tuning**. A future learning change may only promote
a source/reliability or route/timing adjustment after enough independent crossings demonstrate an
improvement. TD crossing evidence, fresh post-house berths, RTT actuals and explicit heard-pass
presses are valid truth sources; a displayed prediction is never used to validate itself.

## 14. Acceptance criteria

The improvement is ready to replace the current model when:

1. A physically observed post-house train never displays `NOW`.
2. `PASSED` remains for no more than 20 seconds, except an explicitly modelled station dwell.
3. Calibration never changes an observed timestamp.
4. `/trains` and `/now` select the same run and state from the same API response.
5. A fresh live approaching train always outranks a stale schedule-only candidate on its line.
6. No run is merged solely by headcode when multiple plausible same-day workings exist.
7. Track-at-house claims identify their source/confidence and agree with final-approach TD
   evidence in at least 99% of observed passages during shadow validation.
8. The system survives loss of RTT or TD by degrading to lower-confidence schedule/forecast
   output without inventing an observation.
9. Stopping trains remain visible for their genuine dwell and leave promptly after departure.
10. There are no duplicate headline records for one physical train after a headcode change.

## 15. Deployment and rollback

The normal live service runs on the cloud VM at:

```text
host: cloud.gdx.org.uk
user: gduthie
directory: /home/gduthie/joggler
service: joggler (Supervisor)
backend: 127.0.0.1:8002
public URL: https://dashboard.gdx.org.uk/
release command: ./deployment/cloud-deploy.sh
```

The Raspberry Pi dashboard is a temporary rollback path only. Do not make ordinary releases to
it. Its runtime learner state may be useful for recovery, but it is not the production source of
truth.

Important: before release, compare the local train-related files with the deployed cloud copy,
and preserve any unrelated local worktree changes. A future Codex must:

1. inspect `git status` and preserve all existing changes;
2. compare the specific train-related code in local and deployed cloud copies before deployment;
3. use `./deployment/cloud-deploy.sh` for the normal release path rather than ad-hoc copying;
4. never overwrite cloud runtime state files (`.env`, `berth_chain.json`,
   `signals_learned.json`, calibration logs, tokens or downloaded reference data);
5. back up the deployed application files being replaced;
6. syntax-check and run fixture tests locally;
7. deploy backend and both frontends as one compatible version;
8. verify Supervisor health and the cloud `/api/trains` JSON after restart;
9. retain a feature flag for old/new selection during shadow mode and initial rollout;
10. roll back application files, not runtime learner state, if the new model fails.

Do not copy the 18 MB `signals_learned.json` through an unsafe live write path. The backend now
uses atomic replacement locally; preserve that behaviour.

## 16. Suggested file-level changes

### `transport-proxy.py`

- refactor `_rtt_normalise()` to preserve actual/forecast provenance;
- introduce run/evidence structures or clearly separated dictionaries;
- refactor `_rtt_build_trains()` into collection, merge, state, ranking and serialization stages;
- make `_td_enrich_trains()` attach evidence rather than overwrite a universal timestamp;
- create pure passage-state and track-at-house functions;
- restrict calibration by `pass_time_source`;
- add confirmed-signal lookup for `held_at_red`;
- add optional debug/shadow serialization;
- invalidate the cache on every physical state transition that affects selection.

### `trains.html`

- remove local operational-state inference once backend fields exist;
- render backend lifecycle and confidence;
- shorten observed-passage grace;
- consume shared train-display helpers.

### `now.html`

- use exactly the same train selection/state helper as `/trains`;
- retain page-specific compact wording/layout only.

### `lineside.html`

- keep as the physical topology/position reference;
- eventually consume the same backend passage state where appropriate;
- do not remove its richer berth-panel behaviour merely to force UI uniformity.

### New test/support files

Suggested layout without adding a build system:

```text
tests/
  test_train_model.py
  fixtures/trains/
    normal_up_main.json
    down_stopper.json
    delayed_forecast.json
    held_red.json
    crossover.json
    reused_headcode.json
    identity_change.json
    missed_crossing.json
train-display.js
```

Use Python’s standard `unittest` unless the repository adopts `pytest` deliberately. Avoid adding
a large dependency solely for this work.

## 17. Recommended execution order for a future Codex

1. Read this file, the train sections of `PROJECT.md`, `SIGNALS-PLAN.md`, and relevant current
   code in all four primary files.
2. Inspect local dirty changes and compare the deployed cloud versions read-only.
3. Capture fixtures before altering semantics.
4. Add failing tests for forecast-vs-actual, calibration-on-observation, and post-house NOW.
5. Implement Phase 1 and Phase 2 behind a feature flag.
6. Run local tests and a short live shadow comparison.
7. Implement run identity/ranking and track topology with new fixtures for every discovered case.
8. Migrate `/trains` and `/now` together.
9. Run the full shadow window and calculate acceptance metrics.
10. Enable the new model, monitor disagreements/errors, and update `PROJECT.md` with the final
    shipped semantics and date.

The highest-value, lowest-risk first release is: separate forecast from actual, prevent
calibration of observations, and make physical post-house evidence terminate `NOW`. Do those
before tuning speed constants or adding more timetable heuristics.
