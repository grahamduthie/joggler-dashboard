#!/usr/bin/env python3
"""
TD berth/signal correlation watcher.

Connects to NR STOMP, captures CA (berth step) and SF (signal flag) messages
for areas D1/D4/D6 (Thames Valley SC), then analyses which SF signal addresses
consistently fire alongside each CA berth transition.

After a 30-minute run with a few trains through Twyford you'll have a mapping of
NR signal addresses to physical berth locations — without needing Traksy's
server-side lookup table.

Usage:
    python3 td_correlate.py              # run for 30 min then analyse
    python3 td_correlate.py 60           # run for 60 min
    python3 td_correlate.py --analyse    # just re-analyse last saved log

Reads NR_USERNAME / NR_PASSWORD from .env in the same directory.
"""

import os, sys, json, time, threading, collections, datetime
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

AREAS           = {'D1', 'D4', 'D6'}
LOG_PATH        = Path(__file__).parent / 'td_correlate_log.json'

# D6 berths near the house (crossover ~200 m east of Twyford station).
# Down trains (increasing berths, westbound): approach house ~0560-0577
# Up trains  (decreasing berths, eastbound): depart station ~0548-0565
HOUSE_ZONE      = {'D6': (540, 600)}   # decimal berth range near house/crossover

# Correlation window: SF events within this many ms of a CA are considered linked
CORR_WINDOW_MS  = 2000

# Only report signals that fired alongside a berth transition >= this fraction
MIN_COFIRE_PCT  = 0.50

# ── Credentials ───────────────────────────────────────────────────────────────

def load_env():
    env = {}
    for candidate in [Path(__file__).parent / '.env', Path.home() / '.env']:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip().strip('"\'')
            break
    return env

env = load_env()
NR_USERNAME = env.get('NR_USERNAME', '')
NR_PASSWORD = env.get('NR_PASSWORD', '')
if not NR_USERNAME and '--analyse' not in sys.argv:
    sys.exit('NR_USERNAME not found in .env')

# ── In-memory event stores ────────────────────────────────────────────────────

lock      = threading.Lock()
ca_events = []   # (ts_ms, area, from_berth, to_berth, descr)
sf_events = []   # (ts_ms, area, address_hex, data_hex)

def now_ms():
    return int(time.time() * 1000)

def fmt_ts(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime('%H:%M:%S.') + \
           f'{(ts_ms % 1000):03d}'

# ── STOMP listener ─────────────────────────────────────────────────────────────

try:
    import stomp
except ImportError:
    if '--analyse' not in sys.argv:
        sys.exit('stomp.py not installed. Run: sudo pip3 install stomp.py --break-system-packages')

class Listener(stomp.ConnectionListener):
    def on_message(self, frame):
        ts = now_ms()
        try:
            msgs = json.loads(frame.body)
        except Exception:
            return
        if not isinstance(msgs, list):
            return
        with lock:
            for item in msgs:
                for key, body in item.items():
                    if not isinstance(body, dict):
                        continue
                    area = body.get('area_id', '')
                    if area not in AREAS:
                        continue
                    mtype = key[:2]
                    if mtype == 'CA':
                        descr = body.get('descr', '').strip()
                        if not descr or descr in ('0000', '    '):
                            continue
                        entry = (ts, area,
                                 body.get('from', ''),
                                 body.get('to',   ''),
                                 descr)
                        ca_events.append(entry)
                        _maybe_print_live(ts, 'CA', area, body)
                    elif mtype == 'SF':
                        addr = body.get('address', '')
                        data = body.get('data', '')
                        if addr:
                            sf_events.append((ts, area, addr, data))

    def on_error(self, frame):
        print(f'STOMP error: {frame.body!r}')

    def on_disconnected(self):
        print('STOMP disconnected')

    def on_heartbeat_timeout(self):
        print('STOMP heartbeat timeout')

# ── Live print filter — only print berths in/near the house zone ──────────────

def _in_house_zone(area, berth_str):
    try:
        b = int(berth_str, 16) if berth_str.startswith(('0x','0X')) else int(berth_str)
    except (ValueError, TypeError):
        return False
    lo, hi = HOUSE_ZONE.get(area, (None, None))
    return lo is not None and lo <= b <= hi

def _maybe_print_live(ts, mtype, area, body):
    if area == 'D6' and mtype == 'CA':
        frm, to, descr = body.get('from',''), body.get('to',''), body.get('descr','').strip()
        marker = ' ◀ HOUSE ZONE' if (_in_house_zone(area, frm) or _in_house_zone(area, to)) else ''
        print(f'  {fmt_ts(ts)}  CA  {area}  {frm}→{to:<4}  [{descr}]{marker}')

# ── Connection ─────────────────────────────────────────────────────────────────

def connect():
    conn = stomp.Connection(
        host_and_ports=[('publicdatafeeds.networkrail.co.uk', 61618)],
        heartbeats=(10000, 10000),
    )
    conn.set_listener('', Listener())
    conn.connect(NR_USERNAME, NR_PASSWORD, wait=True,
                 headers={'client-id': NR_USERNAME})
    conn.subscribe(
        destination='/topic/TD_ALL_SIG_AREA',
        id='1',
        ack='auto',
        headers={'activemq.subscriptionName': NR_USERNAME + '-tdcorr'},
    )
    return conn

# ── Analysis ───────────────────────────────────────────────────────────────────

def decode_aspect(data_hex):
    """
    Attempt to decode signal aspect from SF data byte.
    Thames Valley IECC (Westinghouse) bitmask — empirically determined by community.
    Bit 0 = occupied/red, bits 1-3 = aspect route, bit 7 = route set.
    This is approximate; use the raw data to verify.
    """
    try:
        b = int(data_hex, 16)
    except (ValueError, TypeError):
        return '?'
    aspects = []
    if b & 0x01: aspects.append('RED')
    if b & 0x02: aspects.append('YEL')
    if b & 0x04: aspects.append('2YEL')
    if b & 0x08: aspects.append('GRN')
    if b & 0x80: aspects.append('ROUTE')
    if b & 0x40: aspects.append('CALL')
    return ','.join(aspects) if aspects else f'0x{b:02X}'

def analyse(ca, sf):
    print(f'\n{"="*72}')
    print(f'CORRELATION ANALYSIS')
    print(f'  {len(ca)} CA (berth step) events')
    print(f'  {len(sf)} SF (signal flag) events')
    if ca:
        span_s = (ca[-1][0] - ca[0][0]) / 1000
        print(f'  Observation window: {span_s/60:.1f} min')
    print(f'{"="*72}\n')

    # For each CA transition, collect SF events within CORR_WINDOW_MS
    berth_sf   = collections.defaultdict(collections.Counter)  # key→Counter of (area,addr)
    berth_n    = collections.Counter()
    berth_desc = collections.defaultdict(set)  # key→headcodes seen

    for (ca_ts, ca_area, frm, to, descr) in ca:
        key = (ca_area, frm, to)
        berth_n[key] += 1
        berth_desc[key].add(descr)
        for (sf_ts, sf_area, addr, data) in sf:
            if abs(sf_ts - ca_ts) <= CORR_WINDOW_MS:
                berth_sf[key][(sf_area, addr)] += 1

    # Print berth transitions with consistent co-firing signals
    # Highlight house-zone berths
    print('Berth transitions with consistently co-firing signals:')
    print(f'  (showing signals that fire on ≥{int(MIN_COFIRE_PCT*100)}% of transitions)')
    print()
    print(f'  {"Berth":20s} {"N":>4}  {"Headcodes":12s}  {"Signal":14s}  {"N":>4}  {"Pct":>4}  {"Aspects"}')
    print(f'  {"-"*20} {"-"*4}  {"-"*12}  {"-"*14}  {"-"*4}  {"-"*4}  {"-"*20}')

    n_printed = 0
    for key in sorted(berth_sf.keys()):
        area, frm, to = key
        n = berth_n[key]
        # Filter to signals firing >= MIN_COFIRE_PCT of the time
        consistent = [(sig, cnt) for sig, cnt in berth_sf[key].items()
                      if cnt >= max(2, n * MIN_COFIRE_PCT)]
        if not consistent:
            continue
        consistent.sort(key=lambda x: -x[1])
        hcs = ','.join(sorted(berth_desc[key]))[:12]
        in_zone = (_in_house_zone(area, frm) or _in_house_zone(area, to))
        zone_mark = ' ◀◀' if in_zone else ''

        # Collect data values seen for each signal alongside this berth
        sig_data = collections.defaultdict(list)
        for (ca_ts, ca_area2, f2, t2, _) in ca:
            if (ca_area2, f2, t2) != key:
                continue
            for (sf_ts, sf_area, addr, data) in sf:
                if abs(sf_ts - ca_ts) <= CORR_WINDOW_MS:
                    sig_data[(sf_area, addr)].append(data)

        first = True
        for (sig_area, addr), cnt in consistent:
            pct = int(100 * cnt / n)
            # Most common data value seen with this signal/berth pair
            dvals = sig_data.get((sig_area, addr), [])
            common_data = max(set(dvals), key=dvals.count) if dvals else '??'
            aspect = decode_aspect(common_data)
            if first:
                print(f'  {area} {frm:>4}→{to:<4}{zone_mark:3s}         {n:>4}  {hcs:<12s}  '
                      f'{sig_area}:{addr:<10s}  {cnt:>4}  {pct:>3}%  [{common_data}] {aspect}')
                first = False
            else:
                print(f'  {"":20s}      {"":12s}  {sig_area}:{addr:<10s}  {cnt:>4}  {pct:>3}%  '
                      f'[{common_data}] {aspect}')
            n_printed += 1

    if n_printed == 0:
        print('  No consistent correlations found yet — need more trains through the area.')

    # Summary: all unique SF addresses seen in AREAS
    print(f'\n\nAll SF addresses observed in D1/D4/D6 (most active first):')
    addr_counter = collections.Counter(
        f'{area}:{addr}' for (_, area, addr, _) in sf
    )
    for sig_key, cnt in addr_counter.most_common(50):
        print(f'  {sig_key:<22s}  {cnt:>6} fires')

    # Save raw log
    with open(LOG_PATH, 'w') as f:
        json.dump({'ca': ca, 'sf': sf,
                   'saved': datetime.datetime.now().isoformat()}, f)
    print(f'\nRaw log saved → {LOG_PATH}')
    print('Re-run with --analyse to re-process the log without reconnecting.\n')

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global ca_events, sf_events

    if '--analyse' in sys.argv:
        if not LOG_PATH.exists():
            sys.exit(f'No log file found at {LOG_PATH}')
        print(f'Loading {LOG_PATH}...')
        data = json.loads(LOG_PATH.read_text())
        analyse(data['ca'], data['sf'])
        return

    duration = int(sys.argv[1]) * 60 if len(sys.argv) > 1 else 1800

    print(f'Connecting to NR STOMP as {NR_USERNAME}...')
    conn = connect()
    print(f'Connected. Watching D1/D4/D6 for {duration//60} min.')
    print(f'House-zone berths (D6 {HOUSE_ZONE["D6"][0]:04X}–{HOUSE_ZONE["D6"][1]:04X}) marked ◀')
    print(f'Press Ctrl+C to stop early and run analysis.\n')

    start      = time.time()
    last_print = start

    try:
        while time.time() - start < duration:
            time.sleep(10)
            now = time.time()
            if now - last_print >= 60:
                elapsed = int(now - start)
                with lock:
                    n_ca, n_sf = len(ca_events), len(sf_events)
                print(f'  [{elapsed//60:3d} min]  {n_ca} CA  {n_sf} SF captured')
                last_print = now
    except KeyboardInterrupt:
        print('\nStopped early — running analysis...')
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    with lock:
        ca = list(ca_events)
        sf = list(sf_events)

    analyse(ca, sf)

if __name__ == '__main__':
    main()
