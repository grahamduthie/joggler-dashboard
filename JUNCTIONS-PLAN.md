# Plan: fix junction geometry + show route-through on /lineside

Status: **Phases A-C done and deployed (2026-07-09), plus post-review refinements same
day** — connectors now run cell-boundary to cell-boundary instead of through a berth's
middle, Twyford West's slope direction corrected against Traksy, an UM<->DM connector
added there, Twyford's Main-line platforms offset from Relief per Traksy, and all berth
cells resized to a small uniform width (`CELL_W`, was variable 24-58px) so the connecting
track between cells is always visible — a prerequisite for Phase D/E's route highlighting.
Phase D (cheap route-taken highlight) and Phase E (advance route-set decode) not started.

Goal: make Ruscombe Jn and Twyford West Jn read correctly when a train crosses between
lines (never appears to move backwards), draw them as real junctions instead of unexplained
jumps, extend the Reading end enough to show Up Relief vs Up Main commitment without
modelling Reading's platforms, and (where cheaply possible) show the route actually being
taken through a junction — inspired by Traksy's screenshots (`Traksy_RuscombeJn.png`,
`Traksy_TwyfordJn.png`, `Traksy_KennetBrJn.png` in repo root, supplied 2026-07-09).

## Method: use the learned berth chain as ground truth, not the screenshots

`berth_chain.json` (on the Pi, learned from real CA steps — see `project-trains-page`
memory) already records every berth-to-berth step ever observed, with counts. Filtering
that for edges where the `from` and `to` berths belong to *different* lines in `lineside.html`'s
`LINES` array reveals every real crossover in the corridor, with real frequencies — far more
reliable than reading a static screenshot. Cross-referencing those berths' `BERTH_MI`
distances against the direction of travel finds the actual backward-jump bugs directly.

Found (D1 area, count ≥ 3 occurrences, `BERTH_MI` values as of 2026-07-09):

| from → to | line change | dist_mi (from→to) | screen x (from→to) | direction check |
|---|---|---|---|---|
| `um:1646→ur:1642` (×9) | UM→UR | 1.2 → 0.5 | 338.7 → 374.5 | up, x increases — OK, just a big jump |
| `ur:1622→um:1618` (×4) | UR→UM | -1.3 → -0.6 | 466.4 → 430.7 | **up, x DECREASES — backward jump bug** |
| `dm:1625→dr:1631` (×23) | DM→DR | -1.7 → -0.8 | 486.9 → 440.9 | down, x decreases — OK (down = right→left) |
| `dr:1657→dm:1659` (×13) | DR→DM | 0.6 → 0.6 | equal | already aligned |
| `dm:1675→dr:1687` (×44) | DM→DR | 5.1 → 5.1 | equal | already aligned |
| `ur:1676→um:1666` (×76) | UR→UM | 5.1 → 4.4 | 139.4 → 175.2 | up, x increases — OK, big jump |
| `um:1672→ur:1668` (×12) | UM→UR | 5.1 → 3.34 | 139.4 → 229.3 | up, x increases — OK, big jump |

By position, the west-of-Twyford pair (`um:1646↔ur:1642`, dist ~0.5-1.2) is almost
certainly **Twyford West Jn**; the east-of-Twyford pair (`ur:1622↔um:1618` and
`dm:1625↔dr:1631`, dist ~-0.6 to -1.7) is almost certainly **Ruscombe Jn** (both an
Up-pair and a Down-pair crossover at the same location, which matches a normal 4-track
junction layout and the diagonal crossovers visible in `Traksy_RuscombeJn.png`). The
Reading-end pairs (`ur:1676↔um:1666`, `um:1672↔ur:1668`, `dm:1675↔dr:1687`) are the
East Jn / Kennet Bridge Jn throat visible in `Traksy_KennetBrJn.png`. **Confirm this
naming against the screenshots/live Traksy during Phase A** rather than trusting the
guess here.

Only one edge is an actual direction-reversal bug (`ur:1622→um:1618`) — the others are
correct-direction but oversized jumps (0.7-1.8 mi in one step, i.e. the two lines'
`BERTH_MI` were calibrated independently of each other and don't agree at the point they
actually meet). Both problems have the same fix: recalibrate.

Also found while auditing:
- The existing `'RUSCOMBE JN'` caption is hardcoded at pixel `x:790` → **dist_mi ≈ -7.6**,
  nowhere near the real crossover cluster (~-1 mi). Leftover from an earlier layout pass;
  wrong.
- There is no `'TWYFORD WEST JN'` caption at all currently.
- `'KENNET BR JN'` at `x:150` → dist_mi ≈ 4.9, roughly in the right neighbourhood (5.1) but
  worth re-deriving from real data rather than eyeballing once Phase C extends that area.

## What's currently NOT modelled: the Reading throat

`berth_chain.json` also shows real, well-used predecessor chains feeding the current
westmost berth of every line (e.g. UR's `1676` is fed by `1696`/`1698`/`1694`, hundreds of
occurrences each; UM's `1672` by `1688`/`1690`/`1686`), all currently outside the diagram —
trains there are only shown as anonymous headcode chips in the Reading box. Critically,
**these next-ring-out berths are already distinct per line** (UR's exit berths are never
UM's), meaning the Up Relief vs Up Main decision has *already happened* by the time a train
reaches them — we don't need to model Reading's internal points/platforms (7-15, per the
Traksy screenshot) at all to answer "is this train going onto Up Relief or Up Main", just
extend each line's `west` array by one ring using berths we already have real adjacency
data for.

## Phase A — Fix the geometry (data only, no new visuals)

1. For each crossover pair above, decide a single reconciled `dist_mi` for the meeting
   point (weight by occurrence count — e.g. for `ur:1622`/`um:1618`, 4 occurrences is thin;
   cross-check against a few more weeks of accumulated `berth_chain.json` data or a live
   Traksy comparison before committing, per [[project-lineside-signals]]'s established
   pattern of verifying against Traksy).
2. Update `BERTH_MI` in lineside.html so both sides of each crossover agree (or are within
   a few hundred metres — some real separation between the two physical points is fine,
   backward motion isn't).
3. Confirm the fix: no cross-line edge in `berth_chain.json` should map to a decreasing x
   for an 'up' line or increasing x for a 'down' line.

Acceptance: replay recent CA steps (or watch live) for a train taking `ur:1622→um:1618` —
its occupant marker moves strictly rightward across the jump, never backward.

## Phase B — Draw the junctions

1. Add diagonal connector track artwork in `drawStatic()` at Ruscombe Jn and Twyford West
   Jn, same pattern already used for the Henley branch / P5 loop / turnback siding
   connectors (thick dark backing line + thin coloured overlay, `C_RELIEF`/`C_MAIN`).
   Position from the *reconciled* dist_mi from Phase A, not a guessed pixel value.
2. Fix `'RUSCOMBE JN'`'s caption position; add `'TWYFORD WEST JN'`.
3. Verify visually against `Traksy_RuscombeJn.png` / `Traksy_TwyfordJn.png` for which
   lines the crossovers actually connect (confirming or correcting the naming guess above).

## Phase C — Extend the Reading end (Kennet Bridge Jn, simplified)

1. Get `dist_mi`/line for the next ring of Reading-throat berths (`1694`/`1696`/`1698` for
   UR, `1688`/`1690`/`1686` for UM, and the DR/DM equivalents — check `berth_chain.json`
   predecessors of `dr:1687`/`dm:1675` for those) via SMART/BPLAN lookup first, falling back
   to chain-interpolation (`_rebuild_chain_positions`, same mechanism already used for
   unanchored berths) if SMART doesn't cover them individually.
2. Prepend them to each line's `west` array; widen `X_WEST`'s left edge slightly to fit.
3. Remove them from `READ_BERTHS` if present, so a train there renders on the diagram
   instead of as an anonymous Reading-box chip.
4. Reading station's own internals (platforms 7-15) stay exactly as they are now — a chip
   list, not modelled — per the explicit ask.

Acceptance: a train leaving Reading is visible on the diagram, on the correct line (Relief
or Main), before it reaches the current `1676`/`1672`/etc. edge — not just as a chip.

## Phase D — Show the route actually taken (cheap version, no new backend work)

Once Phase B's connector artwork exists, an occupant crossing via a junction already
renders correctly (`drawOccupant`/`BERTH_CELL` are generic — they don't care which row a
berth's "home" line is). Add: when a train's most recent CA step used a known crossover
edge, briefly highlight that specific connector segment (a few seconds, matching Traksy's
green dashed styling loosely — solid is fine, animation isn't a priority) so the jump reads
as "just crossed via points" rather than an unexplained teleport.

This only shows the route **after** the train has taken it, from data we already have
(CA steps) — not in advance like Traksy's dashed preview line.

## Phase E — Advance route-setting indication (researched 2026-07-09, more feasible than expected)

Traksy shows the route locked *ahead* of the train, before it arrives. Researched whether
this is a real distinct S-class bit signature or just inferred from surrounding signal
aspects — **it's real, and it's specifically available for our corridor**:

- The official [S-Class Messages](https://wiki.openraildata.com/index.php/S_Class_Messages)
  spec (Network Rail-sourced, via Open Rail Data wiki) lists **six** distinct signalling
  element types carried in S-class data: signal aspects, **points** (normal/reverse),
  **route set** (signal → signal or bay platform), TRTS buttons, level crossings, track
  sections. Route-set is a first-class, separate bit group from signal aspect — not
  something inferred.
- Coverage varies by TD area (confirmed via the
  [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers)
  table, columns SIG/RTE/LAT/TRK/PTS/LXG): **D1 (Reading) and D4 (Hayes) both have RTE
  (route-set) ✅. D6 (Maidenhead) does not.** Checked live which area our junction berths
  are actually in (`/api/td-log`, not guessed): **1622, 1618, 1642, 1646, 1625, 1631, 1630,
  1626, 1637, 1655 are all area D1** — meaning Ruscombe Jn, Twyford West Jn, *and* the
  Reading throat are all within RTE-enabled territory. (Raw point normal/reverse state
  isn't available in any of our three areas — but route-set is the more useful of the two
  for this anyway, since it's the full route, not one point at a time.)
- **A community decode for D1 already exists and covers exactly these signals** — the
  [D1 wiki page](https://wiki.openraildata.com/index.php/D1) lists named route-option bits
  (e.g. `R1622AM`/`R1622BM` — two routes for our Ruscombe candidate signal 1622;
  `R1646A`/`R1646B` for the Twyford West candidate; `R1630AM`/`R1630BS` for Twyford
  Platform 4 — nicely cross-validating our own bidirectional-berth signal-aspect work;
  `R1676A`/`B`/`C`, `R1687AM`/`AW`/`BM`/`BW`/`CM`/`CW`, `R1672A`/`B`/`C`, `R1675A`/`B`/`C`
  for the Reading-throat multi-route signals). The naming (`D12D:4` etc.) decodes directly
  to our own `(area, address, bit)` tuple format — `D1` + hex address (`2d`) + bit (`4`).
- **Caveat**: this is an unofficial, community-maintained wiki page — "not subject to
  change management and may not be current" per its own disclaimer. Treat it as a strong
  starting hypothesis, not ground truth: verify each candidate against our own live data
  (does the bit's state actually predict which berth a train ends up in next?) before
  trusting it, the same way Maidenhead P4/P5 was corrected via live cross-checks rather
  than trusted from a single source.
- **Decoding approach differs from the signal-aspect correlator**: per the wiki's own
  decoding guide, route bits "may be set up well in advance" and "may not clear every time
  a train passes" — so the reliable signal isn't transition-timing (a route bit doesn't
  reliably flip right as the train arrives, unlike a signal aspect bit), it's an
  association between **bit value** and **eventual outcome**: when `R1622A`-equivalent is
  1, do trains from berth 1622 consistently end up at one specific next berth, and when
  `R1622B`-equivalent is 1, a different one? This is arguably a *simpler* correlation
  problem than signal aspects (no tight timing window needed, just value-vs-outcome
  bucketing over many occurrences) — a new, separate scorer, not a reuse of
  `_sig_observe_step`/`_sig_evaluate_pending`.

Promoted from "stretch, deferred" — this is now a viable near-term phase, not a maybe.
Sequencing still TBD with Graham: could go after Phase A/B (geometry first, so there's
something correct to overlay the route highlight onto), or could be pulled forward given
the wiki page removes most of the derivation risk. Not started.

## Related

[[project-trains-page]] (berth chain / SMART data this plan is built on),
[[reference-smart-bplan]] (verified corridor topology),
[[project-lineside-signals]] (the signal-aspect work this reuses patterns from — continuous
backend correlation, Traksy-verification, confidence tiers).
