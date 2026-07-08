# Plan: Railway signal status on /lineside

Status: **PLANNED — not started** (plan agreed 2026-07-08). Update this line as phases complete.

Goal: show live signal aspects (red/green heads) on the /lineside diagram for the four
running lines Reading↔Maidenhead, like Tracksy does.

## Background — where the data comes from

Signal state arrives on the **same NR TD feed we already consume** (`/topic/TD_ALL_SIG_AREA`),
via **S-class messages** interleaved with the C-class (CA/CC) berth steps:

- `SF_MSG` — update: one byte changed. Fields: `area_id`, `address` (2 hex chars), `data`
  (2 hex chars = 1 byte = 8 independent signalling bits).
- `SG_MSG` — refresh: `data` is 8 hex chars = **4 consecutive bytes** starting at `address`.
  A burst of SG messages after (re)connect snapshots the whole area.
- `SH_MSG` — refresh finished (same shape as SG; carries the final bytes).
- (Verify field details against https://wiki.openraildata.com/index.php/S_Class_Messages
  before implementing.)

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
  or inconsistently — filter them out by requiring the "goes red exactly as the train
  passes, every time" signature.
- **Nobody has published a decode for TVSC areas D1/D6** (checked 2026-07-08; only ~7 of 61
  TD areas ever decoded publicly). We must derive the bit→signal mapping ourselves.

## What already exists in this repo

- `transport-proxy.py` `_handle_td()` (~line 2990): already parses CA/CC and **SF** messages
  for `_TD_AREAS`, stores SF into `_sf_state[(area, addr)] = (data_hex, ts)` under
  `_sf_lock`. Does NOT handle SG/SH → no state seeding at startup.
- `transport-proxy.py` `_td_live()` (~line 4000): `/api/td-live` already serves raw
  `signals: {"area:addr": {data, ts}}`. Nothing consumes it client-side yet.
- `td_correlate.py`: standalone STOMP watcher + correlation analyser. **Reuse the analysis
  entry point, rewrite the analysis**: it correlates at byte-address level (must be bit
  level), its `decode_aspect()` invents a multi-bit aspect encoding (wrong — 1 bit/signal),
  and its `HOUSE_ZONE` (D6 540–600 ≈ house) predates the corrected geography (those berths
  are MAIDENHEAD). Its earlier findings were discarded for these reasons — the method is
  sound. Byte-level leads from those old runs (active addresses D6:12, 19, 15, 1C, 1B;
  D6:14 fires constantly — likely not a plain signal) are still useful starting points,
  but every geographic interpretation attached to them was wrong. Re-derive from scratch.
- **Verified corridor berth topology per line** — the answer key for correlation — is in
  memory `reference-smart-bplan.md` (west→east berth sequences for Up/Down Main/Relief,
  Twyford platform berths, Henley branch) and `berth_chain.json` (learned adjacency +
  per-berth `dist_mi`, lives on the Pi). `_berth_info(area, berth)` in transport-proxy.py
  returns line/place/dist_mi for a berth.
- History note: /lineside once had 9 speculative signal dots; removed 2026-07-06 because
  their positions used the discredited berth-574=house calibration. Don't resurrect that
  code (`renderSignals`/`KEY_SIGNALS`/`decodeAspect` are gone from lineside.html).

## Operational constraints (read before Phase 1)

- **Deployment**: transport-proxy.py runs on the Raspberry Pi (172.16.10.136, user
  `gduthie`) at `/home/gduthie/twyford-dashboard/`, as systemd service
  `twyford-dashboard`. Deploy = `scp transport-proxy.py gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/`
  then `ssh gduthie@172.16.10.136 'sudo systemctl restart twyford-dashboard'`. Logs in
  `dashboard.log` there. See `project-joggler-status` memory for full procedure.
- **NR STOMP is ON-DEMAND**: the proxy connects only while /trains or /lineside pages are
  polling, and disconnects after 90 s idle (`_nr_touch()`). **Passive multi-day logging
  will silently not happen** unless Phase 1 adds a way to hold the connection open — see
  the collect-mode flag below.
- **Do NOT run td_correlate.py's own STOMP connection while the proxy is connected.** Both
  use the same NR account with `client-id = NR_USERNAME`; ActiveMQ rejects/kicks duplicate
  client-ids. All collection must happen inside the proxy's single connection;
  td_correlate.py survives only as the offline `--analyse` harness (or a new script).
- One TD area's S-class traffic is a few messages/sec at peak. A bit-transition JSONL for
  D1+D6 should be well under ~50 MB for 3 days, but cap/rotate anyway — the Pi's SD card
  is not infinite.

## Phase 1 — Harden collection (transport-proxy.py)

1. **SG/SH handling** in `_handle_td`: for `msg_key[:2] in ('SG','SH')`, split the 8-hex-char
   `data` into 4 bytes and write each to `_sf_state[(area, addr+i)]` (address arithmetic in
   hex: `f'{int(addr,16)+i:02x}'`). Gives full state shortly after connect instead of
   "unknown until it changes". Tag these entries as refresh-sourced.
2. **Bit-transition log**: keep previous byte per `(area, addr)`; on change, XOR old vs new
   and append one JSONL line per flipped bit, plus every CA step, to
   `/home/gduthie/twyford-dashboard/sclass_log.jsonl`:
   ```
   {"t":"bit","ts":1751970000,"a":"D1","addr":"0a","bit":3,"v":0,"src":"SF"}
   {"t":"ca","ts":1751970001,"a":"D1","from":"1626","to":"1618","descr":"1K22"}
   ```
   `src:"SG"` marks refresh-snapshot writes so the correlator can exclude them (a refresh
   after reconnect "changes" every byte at once — that's state sync, not a real transition).
   Size-cap or daily-rotate the file.
3. **Collect mode**: env var `SCLASS_COLLECT=1` (read at startup, set in the systemd unit
   or `.env`) that (a) keeps the NR STOMP connection up permanently instead of on-demand,
   and (b) enables the JSONL logging. Without the flag, behaviour is unchanged (on-demand
   connect, no log growth). **Remember to remove the flag after the corpus is collected.**
4. Deploy, verify in dashboard.log that SG bursts arrive on connect and the JSONL is
   growing, then leave it for **2–3 days** (must span a weekday morning + evening peak;
   the corridor is busy enough that this captures every regularly-used signal many times).

Acceptance: after restart, `/api/td-live` `signals` dict is populated within ~1 min without
any train movement (SG seeding works); `sclass_log.jsonl` contains interleaved `bit` and
`ca` lines with sensible timestamps; proxy memory/CPU unchanged.

## Phase 2 — Derive the bit→signal mapping

Pull the corpus back (`scp gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/sclass_log.jsonl .`)
and analyse offline (new script or rewritten `td_correlate.py --analyse`):

1. Index all CA steps by `(area, from, to)`. Restrict to steps where `from` is a berth in
   the verified corridor topology (known line + dist_mi).
2. For each step occurrence, collect `bit` events with `v==0`, `src=="SF"`, and
   `0 ≤ bit_ts − ca_ts ≤ 5 s` (also try −2..+5 s; TD timestamps are true UTC ms but the
   two message classes may not be perfectly ordered).
3. Score each candidate `(area, addr, bit)` per step key:
   - co-fire rate ≥ ~80% of that step's occurrences (require ≥ 5 occurrences,
     ≥ 3 distinct headcodes);
   - **uniqueness**: the bit's total 1→0 count over the corpus ≈ its matched-step count
     (a bit that also fires for unrelated steps is a route/TRTS/track-circuit bit, not
     this signal — like old D6:14);
   - **exclusivity per step**: ideally exactly one bit survives per step key. If two do,
     one is probably the signal and one a track-circuit/overlap bit — prefer the one whose
     polarity check (below) passes.
4. **Polarity/occupancy check**: for each surviving bit, verify it sits at 0 for the whole
   interval a train occupies the berth beyond the signal (between the `A→B` step and the
   subsequent `B→C` step), and returns to 1 some time after `B→C`.
5. Emit `signals-candidates.json`: one entry per step key with the winning bit, evidence
   counts, and the derived identity/position — signal id ≈ the `from` berth number
   (confirm the actual TVSC prefix by reading signal numbers off Tracksy), line from
   `_berth_info`, `dist_mi` = boundary between `from` and `to` berths (midpoint of their
   dist_mi values is fine at this scale).
6. **Ground truth vs Tracksy**: add a temporary debug endpoint/page (e.g. `/signals-debug`
   served by the proxy: table of candidate signals with live decoded state, auto-refresh).
   Graham watches Tracksy's Twyford diagram alongside and confirms ~6 signals by number
   and by watching them flick red as trains pass. Only candidates that pass get promoted.

Watch for: TVSC possibly using extra bits per signal head (route/aspect bits) — the
correlation output will reveal rather than break; rarely-used signals (crossovers, Henley
branch) may take weeks of corpus — ship what's confirmed, keep `SCLASS_COLLECT` running
longer if coverage is thin.

## Phase 3 — Bake the mapping

`signals.json` committed to the repo (and deployed next to the proxy):

```json
{
  "T1626": {"area": "D1", "address": "0a", "bit": 3,
             "line": "UM", "dist_mi": -0.4, "dir": "up"}
}
```

Proxy loads it at startup, decodes state server-side in `_td_live()`, and adds a clean
list to the `/api/td-live` response:

```json
"signal_states": [{"id": "T1626", "line": "UM", "dist_mi": -0.4, "dir": "up",
                    "state": "off", "age_s": 12}]
```

`state` ∈ `red` (bit 0) / `off` (bit 1) / `unknown` (byte never seen since connect).
Keep the existing raw `signals` dict for debugging. Client never touches raw bits.

## Phase 4 — Display on /lineside

In lineside.html, render from the same `/api/td-live` fetch the page already does every
5 s (`fetchTd`) — no new polling:

- Coordinate system: `distToX(mi) = 400 − mi*51.1`, house at x=400, WEST=+=LEFT,
  window ±6.75 mi. Skip signals outside it.
- One glyph per signal on its line's row: short stem + small circle, red fill for `red`,
  green for `off`, grey/hollow outline for `unknown` or stale (`age_s` > ~600 — though
  note S-class only sends on *change*, so a healthy connection with a quiet signal has
  large age; prefer "unknown = no state since connect" over pure age for greying, or track
  last-refresh time).
- Place the glyph on the approach side per direction of travel (up trains run left→right,
  down right→left) so it reads like a signalbox diagram.
- Joggler constraints: plain SVG shapes only, no CSS filters (`--disable-gpu`, Atom CPU);
  if any signal becomes tappable later, use `onmousedown` not `onclick`. Test on
  Chromium 148 and ideally the Chromium 53 backup (see `joggler-compatibility` memory).
- Deploy is scp of lineside.html only, no service restart (static file).

Acceptance: with a train visibly approaching on /lineside, the signal behind it flicks
red within ~5 s of the train symbol passing it, and clears back green shortly after —
matching what Tracksy shows for the same signal.

## Limitations (accepted)

- Red vs "off" only — no yellow/double-yellow distinction (Tracksy shares this limitation
  in most areas).
- Coverage grows over time; unconfirmed signals simply don't render.

## Optional follow-up

Publish the validated D1/D6 decode on the Open Rail Data wiki (they ask decoders to share).
