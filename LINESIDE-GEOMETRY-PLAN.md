# Lineside Geometry Improvement Plan

## Purpose

Make the `/lineside` diagram between the Reading approach and Maidenhead
represent the physical railway much more faithfully:

- put the four running lines, branches, platforms, turnbacks and crossovers in
  the right topological arrangement;
- space berths at approximately their real-world locations; and
- retain the existing signal-to-berth meaning while placing the signal dots on
  the improved track geometry.

This is a **display-geometry** project.  It must not change TD ingestion,
berth learning, train attribution, signal decoding, or ETA distances in its
first release.  Keeping those concerns separate makes a bad diagram release
safe and easy to reverse.

## Scope and success criteria

Initial scope is the Reading approach, Twyford/Henley Junction, Ruscombe and
Maidenhead (including the Marlow branch, platform/turnback tracks and the
displayed stabling area).  The rest of the schematic may continue to use the
current layout until it is separately surveyed.

The replacement is successful when:

1. Each displayed route has a continuous, plausible physical path and no
   invented crossing or connection.
2. The order and approximate spacing of known berths agrees with track
   geometry, signal anchors, SMART/TD evidence and observed CA transitions.
3. A train moving through a known TD berth chain moves in the correct
   direction along that path; it does not jump between main, relief, branch or
   platform tracks without an explicit junction.
4. The standard `/lineside` view remains unchanged until the new version has
   passed review.  The new view is available only by an explicit URL flag.
5. Reverting requires no database migration, learner reset, TD change or
   service restart.

“Roughly right” should mean geographically credible at diagram scale, not a
claim that every track metre or signal post is surveyed.  Unknown or weakly
supported geometry should be deliberately marked as such rather than guessed.

## Evidence and source hierarchy

Use sources for the purpose they are good at.  Do not use a single source as
proof of everything.

| Question | Primary evidence | Supporting evidence |
| --- | --- | --- |
| What track physically exists and how it curves | A checked-in OpenStreetMap geometry snapshot | aerial/satellite review and local observation |
| Where named signals are located | OSM signal nodes, where their route and reference agree | signal learner confidence and TD timing |
| Which TD berths are consecutive | raw CA event history and the learned berth graph | SMART route data and train observations |
| What line/platform a berth denotes | SMART/berth metadata and confirmed TD transitions | existing verified diagram data |
| Exact crossover, turnback and siding connectivity | OSM where mapped, otherwise verified operational evidence | observations and a deliberately manual topology record |

The initial OpenStreetMap check already provides useful anchors: the Up Main
has mapped `T 1650`, `T 1646`, `T 1640`, `T 1626` and `T 1602` signal nodes,
and the main/relief routes plus the Henley Branch Junction are mapped in the
Twyford corridor.  It is not complete enough to infer every crossover or
interlocking connection automatically, particularly around stations.  OSM
therefore anchors the geometry; it does not silently replace validated
topology.

### Signal-position audit (2026-08-16)

The signal dots still have the correct *semantic* position: each is attached
to the exit boundary of its TD berth. Their geographic position is not yet
reliable across the four rows. The current renderer spaces the Up Relief row
evenly and snaps the other rows to the nearest Up Relief cell. That produces
large distortions even on the independently mapped Up Main: the nearly equal
physical gaps `1650 → 1646 → 1640 → 1626` (about 1.25, 1.36 and 1.46 km) are
currently drawn at about 128, 25 and 46 pixels. The full audited evidence is
kept in `data/lineside-osm-anchors.json`.

Do not move dots independently to hide this. Replace the fixed grid with
projected path coordinates first, then keep every dot on its currently
validated berth exit boundary. OSM does not label enough signals on the other
three lines to position them automatically; those positions need chainage
interpolation between verified track/path anchors.

### Checked-in Tracksy reference captures

The project already contains useful visual topology references.  Treat these
as reviewed diagram evidence, not as a source to copy berth labels from:

| Capture | What it establishes for v2 |
| --- | --- |
| `ReadingToTwyford.png` and `Traksy_KennetBrJn.png` | Reading is a platform fan with a complex east throat; platform berths must not be rendered as running-line cells outside the station. |
| `Traksy_TwyfordJn.png` and `Traksy_RuscombeJn.png` | Twyford's platform roads are staggered around Henley Branch Junction and the later Main/Relief crossovers are real, discrete connections. |
| `TwyfordToMaidenhead.png` | Maidenhead has a multi-road Crossrail stabling fan, branch connection and platform throat; the current single-spur representation is insufficient. |

The first confirmed correction is `D1/1696`: live TD showed a CC interpose of
`9U95`, SMART identifies the berth as Reading platform 14, and the Tracksy
view independently showed the same train at platform 14.  It is therefore not
a Reading-approach Up Relief berth.  This correction enters v2 first, with its
evidence stored in `data/lineside-layout-v2.json`.

Use a local, version-controlled snapshot rather than querying OSM from a
browser or the production server.  Record the Overpass query, retrieval time,
bounding boxes, OSM object IDs and attribution/licence information alongside
the snapshot.  Update it only through a reviewed change.  The page must
continue to work when external map services are unavailable.

The first reviewed snapshot is `data/lineside-osm-anchors.json`. It records
the named Up Main signal anchors `T 1650` / `1646` / `1640` / `1626` / `1602`,
plus OSM geometry for Kennet Loop and the Maidenhead Turnback Siding. The
Kennet review now covers all three continuous OSM ways (about 0.7 km), rather
than treating it as a short local connection. It is input to a reviewable
reconciliation process only; it must not overwrite the learner's signal bit
mappings.

## Preserve a known-good baseline first

Before moving any berth or drawing any new line:

1. Capture screenshots of the current `/lineside` view at its normal desktop
   size and the 800x480 Joggler-scaled size, including quiet, busy and
   route-set conditions.
2. Save representative TD CA replays for both directions, a Reading approach
   movement, a Henley/Marlow movement, Maidenhead platform/turnback activity
   and Crossrail stabling activity.  Include the observed Up Main chain such
   as `1650 → 1646 → 1640 → 1626 → 1618` where available.
3. Extract a baseline list of every berth that the current corridor renderer
   knows about, every active raw berth recently seen, and every signal shown.
   A berth that lacks a cell must be visible in this report rather than being
   silently omitted by the UI.
4. Freeze the current geometry as an explicit `layout-v1` module/data file.
   Do not leave it only as an old revision of a large `lineside.html` script.
   This is the immediate visual fallback.

The cloud server is the only production TD consumer.  Keep the Pi service
stopped while collecting the baseline and all validation evidence: a second
connection to the shared TD subscription can split messages and make a valid
layout look wrong.

## Build a reviewable geometry model

Create a display-only geometry data set, for example
`data/lineside-layout-v2.json`, plus a source metadata file and OSM snapshot.
It should contain the following separately reviewable sections:

- `paths`: ordered polylines for Up/Down Main, Up/Down Relief, Reading
  approach paths, Henley and Marlow branches, platform/turnback tracks and
  stabling tracks;
- `junctions`: named connections between paths, with evidence and confidence;
- `anchors`: stations, platforms, verified signal posts and other fixed
  reference points, retaining OSM IDs where applicable;
- `berths`: a berth-to-path assignment, chainage along that path, label,
  source, confidence and any manual review note; and
- `display`: the local projection, crop and diagram offsets used to turn
  physical coordinates into the SVG/canvas layout.

Convert latitude/longitude to a local projected coordinate system or
along-track chainage before rendering.  Do not space cells by their berth
number and do not retain the current evenly spaced reference grid as the
primary positioning rule.  A berth with no surveyed position may be
interpolated only between two validated anchors on the same path, and must say
so in its metadata.

Keep operational data separate from drawing data.  In particular, do not
alter `_BERTH_MI`, the learner's `berth_chain.json`, SMART matching or train
model logic merely to move a cell on screen.

## Establish topology before detailed positioning

Create a small topology ledger for each problem area.  Every connection must
have a source, confidence and reviewer status.

### Reading approach

1. Trace the Main and Relief pairs independently from the western diagram
   boundary to the Reading station representation.
2. Record every confirmed divergence, convergence and crossover, distinguishing
   a real connection from two lines that merely pass close on a schematic.
3. Replace the current generic west-side berth chips progressively with route
   paths only where the connection is evidenced.  Preserve the present station
   panel if a full Reading platform survey is outside this phase.
4. Verify direction and route membership against replayed CA chains before
   allowing a berth onto a path.

### Twyford and Henley Junction

1. Use the mapped Great Western Main Line, Relief Line, Henley branch and
   Henley Branch Junction as spatial anchors.
2. Reconcile the existing Twyford platform 5 bay and Henley branch cells with
   actual branch entry/exit transitions.  Do not infer a connection just from
   matching berth digits.
3. Use mapped Up Main signal references as an independent check on along-track
   order and scale.

### Maidenhead approach and station area

1. Trace all four running lines into the station area before placing platform,
   turnback, Marlow and stabling cells.
2. Verify which existing loop, platform 5 and turnback/stabling labels describe
   a physical track rather than a historical diagram convenience.
3. Make any not-yet-evidenced sidings or crossovers visually provisional (for
   example muted/dashed in the v2 review view) and log the evidence needed to
   promote them.
4. Reconcile berth ordering from TD replays with the physical route before
   tuning the final cell spacing.

## Rendering approach

Refactor the fixed `X_WEST`/`X_TWY`/`X_EAST` spacing and generic horizontal
line drawing in `lineside.html` behind a layout interface:

```text
layout-v1  -> current coordinates and connectors, preserved unchanged
layout-v2  -> paths, junctions, anchors and berth chainages from the geometry data
```

The renderer should draw the selected path polylines first, then junctions,
platform bands, berth cells and existing signal markers.  A berth cell should
be positioned normal to its own path, not snapped to the closest Up Relief
grid position.  Labels need collision handling, but collision handling must
not move a berth far enough to make its physical order misleading.

For this phase retain the existing signal semantics and associations.  In
particular, do not change which berth boundary a signal is attached to or
reinterpret which signal controls which movement.  When a cell moves, its
current dot moves with that same boundary.  Signal-control conventions can be
reviewed as a later, isolated task.

Expose development information in the v2-only view: path name, berth source /
confidence and unmapped active berth count.  It can be hidden in ordinary
viewing mode, but it makes field observations actionable.

## Validation gates

Implement a repeatable local validation command before exposing v2 publicly.
It should fail on at least the following:

- an active or historically observed corridor berth with no v2 entry, unless it
  is explicitly recorded as unmapped;
- duplicate berth placements or a berth assigned to an invalid path;
- reversed chainage for a confirmed same-line CA transition;
- an unexplained jump between paths;
- self-intersecting paths or crossings that have not been declared as a
  junction; and
- a signal marker that lost its existing berth-boundary association.

Add fixture-based replay tests using saved raw TD events.  Measure:

- percentage of active berths rendered;
- percentage of confirmed CA transitions that progress in the correct screen
  direction and along a connected path;
- count of unexplained cross-path jumps;
- signal-marker association regressions; and
- number of provisional/manual geometry elements still outstanding.

Review v2 visually against OSM at a few fixed checkpoints: Reading approach,
Twyford/Henley Junction, Ruscombe, Maidenhead western approach and the
Maidenhead platform/turnback area.  Require an explicit pass for a fast Up
Main train, a Down Main train, relief traffic, a Henley/Marlow movement and a
Maidenhead turnback/stabling case.

## Safe delivery and rollback

1. Deploy the data, renderer and tests to the cloud, but leave the normal
   `/lineside` URL on `layout-v1`.
2. Make v2 opt-in using a stable query parameter, for example
   `/lineside?layout=v2`.  This is a display-only shadow view using exactly the
   same `/api/td-live` data as v1.
3. Compare v1 and v2 during live traffic and retain screenshots plus any user
   observations as geometry evidence.  Do not make judgement from a period in
   which TD health is degraded.
4. Correct the v2 data in small, separately reviewable commits.  Keep source
   snapshots and topology-ledger changes with each correction.
5. Only after the validation gates and a real-traffic review pass, make v2 the
   default while retaining `layout=v1` as an explicit escape hatch for at
   least one release cycle.

Rollback is intentionally cheap:

- During shadow: use `/lineside` (v1); no production change is required.
- After promotion: switch the default layout configuration back to v1 or use
  `?layout=v1`; v2 data can remain in place for diagnosis.
- If an asset release itself is faulty: deploy the prior static asset bundle
  using the documented cloud deployment process.  No TD service, database,
  learner state or operational evidence must be rolled back.

Use a scoped cloud release/check script for this work and record the deployed
release identifier.  Do not revive the Pi as a fallback production service.

## Proposed sequence

| Phase | Deliverable | Exit condition |
| --- | --- | --- |
| 0 | Baseline screenshots, TD replay fixtures, berth/signal inventory and explicit v1 module | baseline reviewed and Pi remains stopped |
| 1 | Versioned OSM snapshot, metadata and topology ledger | every claimed connection has evidence/confidence |
| 2 | `layout-v2` data model and local projection/compiler | no automatic runtime OSM dependency |
| 3 | V2 renderer plus static validation/replay tests | all validation gates pass locally |
| 4 | Cloud shadow URL `?layout=v2` | live comparison covers all required movement types |
| 5 | Default switch with v1 escape hatch | review accepted; rollback is tested |

### Implementation status

- **Phase 0 / v2 mechanism: in progress.** `data/lineside-layout-v1.json`
  records the legacy Reading classification. `data/lineside-layout-v2.json`
  records the first confirmed correction and `lineside-layout-v2.js` exposes it
  only for `?layout=v2`.
- **First shadow correction: ready for review.** V2 removes `1696` from the
  Up Relief running-line sequence and renders it in the Reading station box.
  V1 is unchanged by default.
- **Cloud shadow published 2026-08-16:** latest release
  `lineside-geometry-20260816T215157Z`, at
  `https://nearby.gdx.org.uk/lineside?layout=v2`. It deployed static layout
  assets only; the `joggler` TD consumer was not restarted. V2 now starts the
  four eastern Reading approaches at `1676`, `1672`, `1687` and `1675`; the
  learner-confirmed Reading platform/throat fan remains in the station box.
  `1669` remains in the normal Down Relief sequence: its 3,150 learner dwell
  samples average 31.5 seconds and overwhelmingly follow
  `1665 → 1669 → 1677`. `1679` is separated as Kennet Loop: it branches from
  `1669`, has just 36 samples averaging 424 seconds, then rejoins toward
  `1687`. This is an interim occupancy distinction only: OSM shows that the
  loop is a long road, so its current simple v2 shape is not approved for
  promotion.
- **Four-line alignment pass:** v2 now uses reviewed shared columns for the
  Reading/Kennet approach, Twyford West and both verified Ruscombe crossovers.
  It keeps Twyford's platform stagger and the house immediately to its east.
  These are explicitly schematic columns, not a claim of surveyed
  berth-to-berth distances.
- **Crossover rendering rule:** v2 crossover leads terminate in clear
  running-track gaps, never on berth boxes, and are diagonal. Maidenhead's
  turnback leads now reach the turnback berth itself.
- **Intermediate-track rule:** where a movement traverses two track rows, v2
  renders paired adjacent-track crossovers and the short intervening running
  track, following the Tracksy Twyford/Ruscombe topology; it never draws a
  direct two-row crossover.
- **Ruscombe ladder correction:** the three adjacent-track crossover leads
  now progress eastwards through UR → DR → UM → DM, matching the reviewed
  Tracksy topology. The former west-sloping UM-to-DM leg was removed.
- **Twyford West ladder correction:** v2 now renders the reviewed eastward
  adjacent-track ladder DM → UM → DR → UR. This replaces the former
  route-level links, including the invalid UM-to-DM lead.
- **Overnight handoff:** `OVERNIGHT-HANDOFF-2026-08-16.md` records the current
  cloud shadow URL, release, review constraints and the separate train-model
  monitoring state. Keep the default `/lineside` on v1 overnight.
- **2026-08-17 — v2 promoted, v1 removed (Phase 5, without the escape hatch below).**
  With a single user of a still-experimental "production" site, keeping two
  live layouts plus a query-param switch was more clutter than safety net.
  `/lineside` now always renders what was `?layout=v2`; the `?layout=` param,
  the `layoutRequest`/`null`-`ACTIVE_LAYOUT` branch, `data/lineside-layout-v1.json`'s
  role as a live fallback, and `deployment/lineside-geometry-shadow-deploy.sh` (the
  scoped, restart-free release script this needed while it was a shadow) are all gone.
  A lineside.html/lineside-layout-v2.js change is now just a normal frontend asset
  change via `deployment/cloud-deploy.sh`. `data/lineside-layout-v1.json` itself is
  kept only as the pre-correction baseline `test_lineside_layout.py` diffs against.
  `data/lineside-layout-v2.json`'s `status` field is now `"live"`. This layout is
  still not a surveyed distance model (see "Alignment" above) — that limitation is
  unchanged by the promotion, only the rollback safety net is.

## Decisions deliberately deferred

- Moving signal markers to a different railway-signalling convention.
- Changes to TD feed collection, signal learning, berth learning, `/trains`,
  `/now` or ETA calculation.
- A full surveyed Reading station diagram.
- Automatically accepting unreviewed OSM edits into the live display.

Those can follow once the new geometry has proved itself, but combining them
with this display change would make evidence and rollback much less clear.
