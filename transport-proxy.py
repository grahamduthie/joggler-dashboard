#!/usr/bin/env python3
"""Twyford Dashboard backend: National Rail + ADS-B + Bus + Hive + Radio APIs → JSON.
Uses only Python stdlib (no pip/flask/requests required).
Runs on 0.0.0.0:5001 — serves dashboard.html and icons/ as static files.
"""

import http.server
import socketserver
import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import time
import datetime
import threading
import os
import re
import subprocess
import html as _html
from urllib.parse import urlparse, parse_qs, quote

NR_TOKEN     = '32cf81aa-5b5f-4195-8a02-6dc47bc20ce5'
SOAP_URL     = 'https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb12.asmx'
SOAP_ACT     = 'http://thalesgroup.com/RTTI/2015-05-14/ldb/GetDepBoardWithDetails'
ADSB_URL     = 'https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}'
CACHE_TTL    = 90   # seconds — tile polls every 120s; TTL < interval = every call misses
FLIGHT_TTL   = 60   # seconds — tile polls every 60s; same principle
RADIO_TTL    = 30   # seconds — Bauer session keys expire quickly; resolve fresh each play
NOWPLAYING_TTL = 25  # seconds — ICY metadata cache; slightly under 30s poll interval
ROUTE_TTL    = 14400  # 4 hours — FlightAware route per callsign; doesn't change mid-flight

# ── Bus: Transport API (departures + on-time) ────────────────────────────────
BUS_APP_ID   = '8355685c'
BUS_APP_KEY  = '4c99459ebd761de52c51b0b98766deb7'
BUS_DEP_URL  = ('https://transportapi.com/v3/uk/bus/stop/{stop}/live.json'
                '?app_id={app_id}&app_key={app_key}&group=no&nextbuses=yes&limit=20')
BUS_DEP_TTL  = 300  # seconds — ~288 req/day, leaves headroom for timetable calls

# ── Bus: Passenger-platform vehicle tracking (all operators) ─────────────────
# Each entry: (base_url, [(operator_code, route), ...])
BUS_VEHICLE_SOURCES = [
    ('https://www.carouselbuses.co.uk/_ajax/lines/vehicles',
     [('CSLB', '850'), ('CSLB', '127')]),
    ('https://www.reading-buses.co.uk/_ajax/lines/vehicles',
     [('RBUS', '12')]),
    ('https://www.thamesvalleybuses.com/_ajax/lines/vehicles',
     [('CTNY', '127'), ('CTNY', '128'), ('CTNY', '129')]),
]
BUS_VEH_TTL  = 30   # seconds

# ── Bus: stop locations (OpenStreetMap / Overpass API) ───────────────────────
# Combined query: route_ref tags (works for all routes incl. route 12 which has
# no OSM relation) + node members of route relations (850 has these; 127/128/129
# relations exist but have only road geometry, no node members).
# False-positive filtering (e.g. route 126 matching "12") is done in Python.
# ── Bus: route timetables (one-time fetch, cached permanently) ────────────────
# Each entry: (operator, route_number, direction)
BUS_TIMETABLE_ROUTES = [
    ('CSLB', '850', 'outbound'), ('CSLB', '850', 'inbound'),
    ('CSLB', '127', 'outbound'), ('CSLB', '127', 'inbound'),
    ('CTNY', '127', 'outbound'), ('CTNY', '127', 'inbound'),
    ('CTNY', '128', 'outbound'), ('CTNY', '128', 'inbound'),
    ('CTNY', '129', 'outbound'), ('CTNY', '129', 'inbound'),
    ('RBUS', '12',  'outbound'), ('RBUS', '12',  'inbound'),
]
BUS_TIMETABLE_URL  = ('https://transportapi.com/v3/uk/bus/route'
                      '/{op}/{route}/{direction}/timetable.json'
                      '?app_id={app_id}&app_key={app_key}')
BUS_TIMETABLE_FILE = '/home/gduthie/twyford-dashboard/bus-route-stops.json'

# ── Hive central heating temperatures ────────────────────────────────────────
HIVE_TOKEN_FILE = '/home/gduthie/twyford-dashboard/hive-tokens.json'
HIVE_CREDS_FILE = '/home/gduthie/twyford-dashboard/hive-credentials.json'
HIVE_SETUP_PY   = '/home/gduthie/twyford-dashboard/hive-setup.py'
HIVE_API_BASE   = 'https://beekeeper-uk.hivehome.com/1.0'
HIVE_TEMP_TTL   = 300   # 5 minutes

OVERPASS_URL    = 'https://overpass-api.de/api/interpreter'
OVERPASS_QUERY  = (
    '[out:json][timeout:60];'
    'relation["route"="bus"]["ref"~"850|127|128|129"](51.38,-1.00,51.65,-0.70)->.r;'
    '('
    'node["highway"="bus_stop"]["route_ref"~"850|127|128|129|12"](51.38,-1.00,51.65,-0.70);'
    'node(r.r)["highway"="bus_stop"];'
    'node(r.r)["public_transport"~"stop_position|platform"];'
    ');'
    'out body;'
)
TRACKED_ROUTES  = frozenset({'850', '127', '128', '129', '12'})
BUS_STOPS_FILE  = '/home/gduthie/twyford-dashboard/bus-stops.json'  # persisted across reboots
BUS_STOPS_TTL   = 14400  # 4-hour in-memory cache; file reused indefinitely

APP_DIR = '/home/gduthie/twyford-dashboard'
MIME    = {'.html': 'text/html', '.js': 'application/javascript',
           '.png':  'image/png',  '.svg': 'image/svg+xml',
           '.json': 'application/json', '.css': 'text/css'}

_cache = {}
_lock  = threading.Lock()

# ── Bus: BODS (Bus Open Data Service) ────────────────────────────────────────
BODS_ENV_FILE   = '/home/gduthie/twyford-dashboard/.env'
BODS_API_KEY    = ''   # loaded from BODS_ENV_FILE at startup
LASTFM_API_KEY  = ''   # loaded from BODS_ENV_FILE at startup
BODS_URL       = ('https://data.bus-data.dft.gov.uk/api/v1/datafeed/'
                  '?api_key={key}&operatorRef={op}')
BODS_OPERATORS = ['RBUS', 'CSLB', 'CTNY']
BODS_ROUTES    = frozenset(['850', '12', '127', '127S', '128', '129', '227'])
BODS_TTL       = 30   # seconds — generous; BODS updates ~every 10s
BODS_ROUTE_BEARINGS = {
    '127':  {'outbound': 70,  'inbound': 250},
    '850':  {'outbound': 340, 'inbound': 160},
}
BODS_OPERATOR_NAMES = {
    'RBUS': 'Reading Buses',
    'CSLB': 'Carousel Buses',
    'CTNY': 'Thames Valley Buses',
}
BODS_ROUTE_COLOURS = {
    '850':  {'bg': '#d97706', 'fg': '#ffffff'},
    '127':  {'bg': '#10b981', 'fg': '#ffffff'},
    '127S': {'bg': '#0891b2', 'fg': '#ffffff'},
    '128':  {'bg': '#2563eb', 'fg': '#ffffff'},
    '129':  {'bg': '#3b82f6', 'fg': '#ffffff'},
    '12':   {'bg': '#8b5cf6', 'fg': '#ffffff'},
    '227':  {'bg': '#0d9488', 'fg': '#ffffff'},
}
# Twyford-area stops used for BODS-based ETA departures.
# id = NaPTAN ATCO code; routes = route numbers that call here.
BODS_STOPS = {
    '035091060001': {'name': 'Waggon and Horses',         'lat': 51.478222, 'lon': -0.872320, 'routes': ['127', '128', '850']},
    '035091060002': {'name': 'Waggon and Horses',         'lat': 51.478177, 'lon': -0.873784, 'routes': ['12', '850', '127']},
    '035091120001': {'name': 'Twyford Station',           'lat': 51.475519, 'lon': -0.861709, 'routes': ['128', '129']},
    '035091100001': {'name': 'Waitrose',                  'lat': 51.478046, 'lon': -0.865476, 'routes': ['127', '850']},
    '035091100003': {'name': 'Waitrose',                  'lat': 51.477893, 'lon': -0.865451, 'routes': ['12', '129']},
    '035091100004': {'name': 'Church Street',             'lat': 51.477000, 'lon': -0.865157, 'routes': ['128', '129', '227']},
    '035099850002': {'name': 'Twyford Station Forecourt', 'lat': 51.475777, 'lon': -0.863517, 'routes': ['227']},
}

# ── Bus: Passenger-platform stop departure pages ──────────────────────────────
# Each ATCO maps to a list of (domain, fetch_atco, operator_name).
# fetch_atco may differ from the query ATCO so one logical stop can merge both
# directions (e.g. Waggon and Horses has 035091060001 and 035091060002).
PASSENGER_STOP_SOURCES = {
    '035091060001': [('www.carouselbuses.co.uk',   '035091060001', 'Carousel'),
                     ('www.thamesvalleybuses.com', '035091060001', 'Thames Valley'),
                     ('www.carouselbuses.co.uk',   '035091060002', 'Carousel'),
                     ('www.reading-buses.co.uk',   '035091060002', 'Reading Buses')],
    '035091060002': [('www.carouselbuses.co.uk',   '035091060002', 'Carousel'),
                     ('www.reading-buses.co.uk',   '035091060002', 'Reading Buses')],
    '035091120001': [('www.thamesvalleybuses.com', '035091120001', 'Thames Valley')],
    '035091100001': [('www.carouselbuses.co.uk',   '035091100001', 'Carousel')],
    '035091100003': [('www.reading-buses.co.uk',   '035091100003', 'Reading Buses'),
                     ('www.thamesvalleybuses.com', '035091100003', 'Thames Valley')],
    '035091100004': [('www.thamesvalleybuses.com', '035091100004', 'Thames Valley')],
    '035099850002': [('www.thamesvalleybuses.com', '035099850002', 'Thames Valley')],
}
PASSENGER_TTL = 30   # seconds

_bods_cache = {'data': None, 'ts': 0}


# ── SOAP call ────────────────────────────────────────────────────────────────

def _soap(crs, rows):
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:tok="http://thalesgroup.com/RTTI/2013-11-28/Token/types"'
        ' xmlns:ldb="http://thalesgroup.com/RTTI/2021-11-01/ldb/">'
        '<soap:Header>'
        '<tok:AccessToken><tok:TokenValue>' + NR_TOKEN + '</tok:TokenValue></tok:AccessToken>'
        '</soap:Header>'
        '<soap:Body>'
        '<ldb:GetDepBoardWithDetailsRequest>'
        '<ldb:numRows>' + str(rows) + '</ldb:numRows>'
        '<ldb:crs>' + crs + '</ldb:crs>'
        '</ldb:GetDepBoardWithDetailsRequest>'
        '</soap:Body>'
        '</soap:Envelope>'
    ).encode('utf-8')

    req = urllib.request.Request(
        SOAP_URL, data=body,
        headers={'Content-Type': 'text/xml; charset=utf-8',
                 'SOAPAction': '"' + SOAP_ACT + '"'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return ET.fromstring(resp.read())


# ── XML parsing ──────────────────────────────────────────────────────────────

def _t(el, tag):
    """Find first child by local name (namespace-agnostic), return text or None."""
    if el is None:
        return None
    found = el.find('.//{*}' + tag)
    return found.text.strip() if found is not None and found.text else None


def _parse(root, platform_filter, limit):
    res = root.find('.//{*}GetStationBoardResult')
    if res is None:
        return None

    out = {'station': _t(res, 'locationName') or '',
           'crs':     _t(res, 'crs') or '',
           'services': []}

    svcs = res.find('.//{*}trainServices')
    if svcs is None:
        return out

    for child in svcs:
        local = child.tag.split('}')[1] if '}' in child.tag else child.tag
        if local != 'service':
            continue

        plat = (_t(child, 'platform') or '').strip()
        if platform_filter and plat and plat != platform_filter:
            continue

        dest_el = child.find('.//{*}destination/{*}location')

        calling = []
        for cp in child.findall('.//{*}subsequentCallingPoints'
                                '//{*}callingPoint'):
            nm = _t(cp, 'locationName')
            if nm:
                calling.append({'name': nm,
                                'st':   _t(cp, 'st') or '',
                                'et':   _t(cp, 'et') or 'On time'})

        out['services'].append({
            'std':          _t(child, 'std') or '',
            'etd':          _t(child, 'etd') or 'On time',
            'platform':     plat,
            'operator':     _t(child, 'operator') or '',
            'destination':  _t(dest_el, 'locationName') if dest_el is not None else '',
            'destinationCrs': _t(dest_el, 'crs') if dest_el is not None else '',
            'callingPoints': calling,
            'cancelled':    _t(child, 'isCancelled') == 'true',
            'cancelReason': _t(child, 'cancelReason'),
            'delayReason':  _t(child, 'delayReason'),
        })
        if len(out['services']) >= limit:
            break

    return out


# ── Bus: vehicle positions ───────────────────────────────────────────────────

def _build_vehicle_url(base_url, routes):
    params = '&'.join(
        'lines%5B' + str(i) + '%5D=' + op + '%3A' + route
        for i, (op, route) in enumerate(routes)
    )
    return base_url + '?' + params


def _fetch_bus_vehicles():
    features = []
    lock = threading.Lock()

    def fetch_one(base_url, routes):
        url = _build_vehicle_url(base_url, routes)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                with lock:
                    features.extend(data.get('features', []))
        except Exception:
            pass  # partial data is better than a hard failure

    threads = [threading.Thread(target=fetch_one, args=(base_url, routes))
               for base_url, routes in BUS_VEHICLE_SOURCES]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)

    return {'type': 'FeatureCollection', 'features': features}


# ── Bus: live departures (Transport API) ─────────────────────────────────────

def _mins_diff(aimed, expected):
    """Return integer minutes between two HH:MM strings (expected − aimed)."""
    try:
        ah, am = map(int, aimed.split(':'))
        eh, em = map(int, expected.split(':'))
        diff = (eh * 60 + em) - (ah * 60 + am)
        if diff < -120:   # handle midnight wrap (e.g. 23:58 aimed → 00:02 expected)
            diff += 1440
        return diff
    except Exception:
        return 0


def _fetch_bus_departures(stop):
    url = BUS_DEP_URL.format(stop=stop, app_id=BUS_APP_ID, app_key=BUS_APP_KEY)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return None, str(e)

    raw = data.get('departures', {}).get('all', [])
    out = []
    for d in raw:
        aimed     = d.get('aimed_departure_time') or ''
        expected  = d.get('expected_departure_time') or ''
        cancelled = (d.get('status') or {}).get('cancellation', {}).get('value', False)

        if cancelled:
            status, mins_late = 'cancelled', 0
        elif expected and expected != aimed and aimed:
            diff = _mins_diff(aimed, expected)
            status    = 'late' if diff > 1 else 'ontime'
            mins_late = max(0, diff)
        elif expected or aimed:
            status, mins_late = 'ontime', 0
        else:
            status, mins_late = 'nodata', 0

        out.append({
            'scheduled': aimed,
            'expected':  expected or aimed,
            'line':      d.get('line_name') or d.get('line') or '',
            'destination': d.get('direction') or '',
            'operator':  d.get('operator_name') or '',
            'status':    status,
            'minsLate':  mins_late,
        })

    return out, None


# ── Bus: stop locations ──────────────────────────────────────────────────────

def _fetch_one_route_timetable(op, route, direction):
    """Fetch stop ATCO codes for one route/direction from Transport API.
    Returns list of {atco, name, lat, lon} or raises on error/rate-limit."""
    url = BUS_TIMETABLE_URL.format(
        op=op, route=route, direction=direction,
        app_id=BUS_APP_ID, app_key=BUS_APP_KEY)
    req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get('error') or data.get('errors'):
        raise RuntimeError(str(data.get('error') or data.get('errors')))
    return [{'atco': s['atcocode'], 'name': s.get('stop_name') or s.get('name', ''),
             'lat': s['latitude'], 'lon': s['longitude']}
            for s in data.get('stops', []) if s.get('atcocode')]


def _load_timetable_file():
    try:
        with open(BUS_TIMETABLE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_timetable_file(data):
    try:
        with open(BUS_TIMETABLE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def _build_atco_routes(timetable_data):
    """Build atco → sorted list of route numbers from timetable data."""
    atco_routes = {}
    for entry in timetable_data.get('routes', []):
        route = entry['route']
        for atco in entry.get('atcos', []):
            atco_routes.setdefault(atco, set()).add(route)
    return {a: sorted(r) for a, r in atco_routes.items()}


def _stop_has_tracked_route(route_ref_str):
    """Return True if route_ref contains at least one tracked route as an exact token.
    Stops with no route_ref tag came from relation membership and are always kept."""
    if not route_ref_str:
        return True
    parts = frozenset(r.strip() for r in re.split(r'[;,\s]+', route_ref_str) if r.strip())
    return bool(parts & TRACKED_ROUTES)


def _fetch_bus_stops():
    """Fetch route-filtered stops from Overpass API via HTTP POST."""
    body = ('data=' + quote(OVERPASS_QUERY)).encode('utf-8')
    req  = urllib.request.Request(
        OVERPASS_URL, data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'User-Agent':   'Joggler/1.0'})
    with urllib.request.urlopen(req, timeout=65) as resp:
        raw = json.loads(resp.read())
    seen  = set()
    stops = []
    for el in raw.get('elements', []):
        if el.get('type') != 'node' or el['id'] in seen:
            continue
        tags = el.get('tags', {})
        if not _stop_has_tracked_route(tags.get('route_ref', '')):
            continue
        seen.add(el['id'])
        route_ref = tags.get('route_ref', '')
        osm_routes = sorted({r.strip() for r in re.split(r'[;,\s]+', route_ref)
                             if r.strip() in TRACKED_ROUTES})
        stops.append({
            'lat':    el['lat'],
            'lon':    el['lon'],
            'name':   tags.get('name') or tags.get('naptan:CommonName') or '',
            'atco':   tags.get('naptan:AtcoCode') or '',
            'routes': osm_routes,
        })
    return stops


def _load_stops_file():
    try:
        with open(BUS_STOPS_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_stops_file(data):
    try:
        with open(BUS_STOPS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


# ── Hive helpers ─────────────────────────────────────────────────────────────

def _hive_load_tokens():
    try:
        with open(HIVE_TOKEN_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _hive_save_tokens(tokens):
    try:
        with open(HIVE_TOKEN_FILE, 'w') as f:
            json.dump(tokens, f)
        os.chmod(HIVE_TOKEN_FILE, 0o600)
    except Exception:
        pass


def _hive_cognito_post(region, target, body):
    url = f'https://cognito-idp.{region}.amazonaws.com/'
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        'X-Amz-Target': f'AWSCognitoIdentityProviderService.{target}',
        'Content-Type': 'application/x-amz-json-1.1',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Cognito {target} HTTP {e.code}: {body[:200]}')


def _hive_refresh(tokens):
    """Refresh Cognito tokens using the saved refresh token."""
    try:
        result = _hive_cognito_post(tokens['region'], 'InitiateAuth', {
            'ClientId': tokens['client_id'],
            'AuthFlow': 'REFRESH_TOKEN_AUTH',
            'AuthParameters': {'REFRESH_TOKEN': tokens['RefreshToken']},
        })
    except Exception as e:
        return None, str(e)
    auth = result.get('AuthenticationResult', {})
    if not auth.get('IdToken'):
        return None, 'Refresh returned no IdToken: ' + repr(result)
    tokens['IdToken']      = auth['IdToken']
    tokens['AccessToken']  = auth['AccessToken']
    tokens['token_expiry'] = time.time() + auth.get('ExpiresIn', 3600) - 60
    _hive_save_tokens(tokens)
    return tokens, None


def _hive_full_reauth():
    """Re-authenticate from scratch using saved credentials file."""
    if not os.path.exists(HIVE_CREDS_FILE):
        return None, 'Refresh token expired and no credentials file found — run hive-setup.py'
    try:
        result = subprocess.run(
            ['python3', HIVE_SETUP_PY,
             '--credentials-file', HIVE_CREDS_FILE,
             '--token-file', HIVE_TOKEN_FILE],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return None, f'Re-auth subprocess failed: {e}'
    if result.returncode != 0:
        out = (result.stderr or result.stdout)[:300]
        return None, f'Re-auth failed (exit {result.returncode}): {out}'
    tokens = _hive_load_tokens()
    if not tokens:
        return None, 'Re-auth ran but token file is missing or unreadable'
    return tokens, None


def _hive_fetch_temps():
    """Load tokens, refresh if expired, query /nodes/all for the home with devices."""
    tokens = _hive_load_tokens()
    if not tokens:
        return None, 'No Hive tokens — run hive-setup.py first'
    if time.time() > tokens.get('token_expiry', 0):
        tokens, err = _hive_refresh(tokens)
        if err:
            tokens, err2 = _hive_full_reauth()
            if err2:
                return None, f'Token refresh failed ({err}); re-auth also failed: {err2}'
    home_id = tokens.get('home_id', '')
    url = HIVE_API_BASE + '/nodes/all?products=true'
    if home_id:
        url += '&homeId=' + home_id
    req = urllib.request.Request(
        url,
        headers={'authorization': tokens['IdToken'], 'content-type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return None, str(e)
    sensors = []
    for p in data.get('products', []):
        if p.get('type') != 'heating':
            continue
        temp = (p.get('props') or {}).get('temperature')
        if temp is None:
            continue
        name = (p.get('state') or {}).get('name') or p.get('id', 'Unknown')
        sensors.append({'name': name, 'temp': round(float(temp), 1)})
    return sensors, None


# ── BODS helpers ─────────────────────────────────────────────────────────────

def _load_env():
    global BODS_API_KEY, LASTFM_API_KEY
    try:
        with open(BODS_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    k, v = k.strip(), v.strip()
                    if k == 'BODS_API_KEY':
                        BODS_API_KEY = v
                    elif k == 'LASTFM_API_KEY':
                        LASTFM_API_KEY = v
    except Exception:
        pass


def _bods_xel(el, tag):
    """Find a direct child element ignoring XML namespace."""
    child = el.find('{*}' + tag)
    return child if child is not None else el.find(tag)


def _bods_xt(el, tag):
    """Get text of a direct child element ignoring XML namespace."""
    child = _bods_xel(el, tag)
    return (child.text or '').strip() if child is not None else ''


def _clean_bods_dest(name):
    if not name:
        return ''
    name = name.replace('_', ' ')
    name = re.sub(r', .*$', '', name)                            # strip ", Suffix"
    name = re.sub(r'^(\w[\w ]+?) \1\b', r'\1', name)            # "Foo Foo Bar" → "Foo Bar"
    name = re.sub(r'\bBusStn\b', 'Bus Station', name)
    name = re.sub(r'\bStn\b', 'Station', name)
    return name.strip()


def _parse_bods_delay(delay_str):
    """Parse ISO 8601 duration e.g. PT5M or -PT2M → (text, mins, status)."""
    if not delay_str:
        return 'On Time', 0, 'ontime'
    s = str(delay_str).strip()
    negative = s.startswith('-')
    m = re.search(r'PT(?:(\d+)H)?(?:(\d+)M)?', s)
    if not m:
        return 'On Time', 0, 'ontime'
    total = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if total <= 1:
        return 'On Time', 0, 'ontime'
    if negative:
        return f'{total}m Early', -total, 'early'
    return f'{total}m Delay', total, 'late'


def _parse_bods_xml(xml_text):
    """Parse SIRI-VM XML from BODS, return list of bus dicts."""
    buses = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return buses
    for activity in root.findall('.//{*}VehicleActivity'):
        journey = _bods_xel(activity, 'MonitoredVehicleJourney')
        if journey is None:
            continue
        loc = _bods_xel(journey, 'VehicleLocation')
        if loc is None:
            continue
        try:
            lat = float(_bods_xt(loc, 'Latitude'))
            lon = float(_bods_xt(loc, 'Longitude'))
        except (ValueError, TypeError):
            continue
        line = _bods_xt(journey, 'LineRef')
        op   = _bods_xt(journey, 'OperatorRef')
        if op == 'CTNY' and line == '127':
            line = '127S'
        if line not in BODS_ROUTES:
            continue
        direction = _bods_xt(journey, 'DirectionRef').lower()
        raw_b = _bods_xt(journey, 'Bearing')
        try:
            bearing = float(raw_b)
        except (ValueError, TypeError):
            bearing = None
        if bearing is None and direction and line in BODS_ROUTE_BEARINGS:
            bearing = BODS_ROUTE_BEARINGS[line].get(direction)
        dest = _clean_bods_dest(_bods_xt(journey, 'DestinationName'))
        delay_txt, delay_mins, status = _parse_bods_delay(_bods_xt(journey, 'Delay'))
        bus_id = _bods_xt(journey, 'VehicleRef') or (op + '-' + line + '-' + str(lat))
        buses.append({
            'id':          bus_id,
            'line':        line,
            'operator':    op,
            'destination': dest,
            'direction':   direction,
            'lat':         lat,
            'lon':         lon,
            'bearing':     bearing,
            'delay':       delay_txt,
            'delayMins':   delay_mins,
            'status':      status,
            'recordedAt':  _bods_xt(activity, 'RecordedAtTime'),
        })
    return buses


def _fetch_bods_all():
    """Fetch BODS SIRI-VM for all operators in parallel; return (buses, errors)."""
    results = [None] * len(BODS_OPERATORS)
    errors  = []

    def _fetch_one(i, op):
        url = BODS_URL.format(key=BODS_API_KEY, op=op)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                results[i] = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            errors.append(f'{op}: {e}')

    threads = [threading.Thread(target=_fetch_one, args=(i, op), daemon=True)
               for i, op in enumerate(BODS_OPERATORS)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=25)

    buses = []
    seen  = set()
    for xml_text in results:
        if xml_text:
            for bus in _parse_bods_xml(xml_text):
                if bus['id'] not in seen:
                    seen.add(bus['id'])
                    buses.append(bus)
    return buses, errors


def _get_bods_cached():
    now = time.time()
    with _lock:
        if _bods_cache['data'] is not None and now - _bods_cache['ts'] < BODS_TTL:
            return _bods_cache['data'], []
    buses, errors = _fetch_bods_all()
    with _lock:
        _bods_cache['data'] = buses
        _bods_cache['ts']   = now
    return buses, errors


def _bods_is_fresh(recorded_at, max_age_secs=900):
    """True if recordedAt ISO timestamp is within max_age_secs of now (UTC)."""
    if not recorded_at:
        return True
    try:
        dt = datetime.datetime.fromisoformat(recorded_at)
        now = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        age = (now - dt).total_seconds()
        return 0 <= age < max_age_secs
    except Exception:
        return True


def _bods_heading_toward(bus_lat, bus_lon, bearing, stop_lat, stop_lon):
    """True if bus bearing is within 90° of the direction to stop."""
    if bearing is None:
        return True
    mid = math.radians((bus_lat + stop_lat) / 2)
    dlat = stop_lat - bus_lat
    dlon = (stop_lon - bus_lon) * math.cos(mid)
    angle = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360
    return abs((bearing - angle + 180) % 360 - 180) < 90


def _bods_eta_mins(bus_lat, bus_lon, stop_lat, stop_lon):
    """Straight-line ETA in minutes at 48 km/h (≈30 mph)."""
    dlat = (stop_lat - bus_lat) * 111.0
    dlon = (stop_lon - bus_lon) * 111.0 * math.cos(math.radians(bus_lat))
    return math.sqrt(dlat**2 + dlon**2) / 48.0 * 60.0


def _fetch_passenger_stop(atco, domain, operator_name):
    """Scrape the Passenger-platform stop departure board, return list of departure dicts."""
    url = f'https://{domain}/stops/{atco}'
    req = urllib.request.Request(url, headers={
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'text/html',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode('utf-8', errors='replace')
    except Exception:
        return []

    items = re.findall(r'<li class="departure-board__item".*?</li>', body, re.DOTALL)
    now = datetime.datetime.now()
    now_mins = now.hour * 60 + now.minute
    results = []

    for item in items:
        route_m = re.search(r'single-visit__name[^>]*>([^<]+)', item)
        dest_m  = re.search(r'single-visit__description[^>]*>([^<]+)', item)
        time_m  = re.search(r'single-visit__arrival-time__cell[^>]*>([^<]+)', item)
        state_m = re.search(r'single-visit--(\w+)', item)
        aimed_m = re.search(r'time=(\d{1,2}:\d{2})', item)

        if not (route_m and time_m):
            continue

        route  = _html.unescape(route_m.group(1).strip())
        dest   = _html.unescape(dest_m.group(1).strip()) if dest_m else ''
        disp   = time_m.group(1).strip()
        state  = state_m.group(1) if state_m else ''
        aimed_str = aimed_m.group(1) if aimed_m else None

        # Parse display time ("20 mins" or "15:04") → eta_mins + expected HH:MM
        mins_m = re.match(r'(\d+)\s+min', disp)
        if mins_m:
            eta_mins  = int(mins_m.group(1))
            exp_total = (now_mins + eta_mins) % 1440
            exp_str   = f'{exp_total // 60:02d}:{exp_total % 60:02d}'
        elif re.match(r'\d{1,2}:\d{2}$', disp):
            exp_str   = disp
            h, m      = map(int, disp.split(':'))
            exp_total = h * 60 + m
            eta_mins  = (exp_total - now_mins) % 1440
        else:
            continue

        # Aimed/scheduled time
        if aimed_str:
            ah, am       = map(int, aimed_str.split(':'))
            aimed_total  = ah * 60 + am
        else:
            aimed_total  = None

        # On-time status by comparing expected vs aimed
        if state == 'cancelled':
            status, mins_late = 'cancelled', 0
        elif aimed_total is not None:
            diff = exp_total - aimed_total
            if diff >  720: diff -= 1440
            if diff < -720: diff += 1440
            mins_late = diff
            status = 'ontime' if abs(mins_late) <= 1 else ('late' if mins_late > 0 else 'early')
        else:
            status, mins_late = None, 0

        results.append({
            'scheduled':   aimed_str,
            'expected':    exp_str,
            'line':        route,
            'destination': dest,
            'operator':    operator_name,
            'status':      status,
            'minsLate':    mins_late,
            'etaMins':     eta_mins,
        })

    return results


# ── Radio stream resolver ─────────────────────────────────────────────────────

def _resolve_stream_url(playlist_url):
    """Fetch a .m3u or .pls playlist and return the first stream URL."""
    req = urllib.request.Request(playlist_url,
                                 headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)'})
    with urllib.request.urlopen(req, timeout=10) as r:
        content = r.read().decode('utf-8', errors='ignore')
    for line in content.splitlines():
        line = line.strip()
        if line.lower().startswith('file1='):
            return line.split('=', 1)[1].strip()
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    raise ValueError('No stream URL found in playlist')


RP_API_URL = 'https://api.radioparadise.com/api/now_playing?chan={chan}'
RP_TTL = 20  # seconds — short so we pick up new tracks quickly after the timer fires

LASTFM_URL     = 'https://ws.audioscrobbler.com/2.0/'
LASTFM_TTL     = 3600  # 1 hour — track info doesn't change


def _fetch_rp_nowplaying(chan):
    """Fetch Radio Paradise now-playing JSON for the given channel."""
    url = RP_API_URL.format(chan=int(chan))
    req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_lastfm_track_info(artist, title):
    """Fetch track + artist info from Last.fm. Returns dict with image, album, bio, listeners, tags."""
    import urllib.parse
    result = {'image': '', 'album': '', 'bio': '', 'listeners': '', 'tags': []}
    if not LASTFM_API_KEY:
        return result

    def _lfm(params):
        params['api_key'] = LASTFM_API_KEY
        params['format']  = 'json'
        url = LASTFM_URL + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _strip_html(s):
        s = re.sub(r'<[^>]+>', '', s)
        s = re.sub(r'\s+', ' ', s).strip()
        # Last.fm summaries end with "Read more on Last.fm" link; remove it
        s = re.sub(r'\s*Read more on Last\.fm\s*\.?\s*$', '', s, flags=re.IGNORECASE)
        return s

    def _best_image(images):
        for size in ('extralarge', 'large', 'medium', 'small'):
            for img in images:
                if img.get('size') == size and img.get('#text'):
                    return img['#text']
        return ''

    # Try track.getInfo first
    try:
        d = _lfm({'method': 'track.getInfo', 'artist': artist, 'track': title, 'autocorrect': '1'})
        t = d.get('track', {})
        al = t.get('album', {})
        result['album']     = al.get('title', '')
        result['listeners'] = t.get('listeners', '')
        result['image']     = _best_image(al.get('image', []))
        wiki = t.get('wiki', {})
        if wiki.get('summary'):
            result['bio'] = _strip_html(wiki['summary'])
        tags = [tag['name'] for tag in t.get('toptags', {}).get('tag', []) if tag.get('name')]
        if tags:
            result['tags'] = tags[:4]
    except Exception:
        pass

    # Fill missing bio/image/listeners from artist.getInfo
    if not result['bio'] or not result['listeners']:
        try:
            d = _lfm({'method': 'artist.getInfo', 'artist': artist, 'autocorrect': '1'})
            a = d.get('artist', {})
            if not result['listeners']:
                result['listeners'] = a.get('stats', {}).get('listeners', '')
            if not result['bio']:
                bio = a.get('bio', {}).get('summary', '')
                if bio:
                    result['bio'] = _strip_html(bio)
            if not result['image']:
                result['image'] = _best_image(a.get('image', []))
        except Exception:
            pass

    return result


def _fetch_icy_nowplaying(stream_url):
    """Connect to an ICY/Icecast stream, read to the first metadata block, return StreamTitle or None."""
    req = urllib.request.Request(stream_url, headers={
        'Icy-MetaData': '1',
        'User-Agent': 'WinampMPEG/5.09',
        'Accept': '*/*',
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        try:
            metaint = int(resp.headers.get('icy-metaint', '0') or '0')
        except (ValueError, TypeError):
            return None
        if metaint <= 0:
            return None
        audio = b''
        while len(audio) < metaint:
            chunk = resp.read(metaint - len(audio))
            if not chunk:
                return None
            audio += chunk
        lb = resp.read(1)
        if not lb:
            return None
        meta_len = lb[0] * 16
        if meta_len == 0:
            return None
        meta_str = resp.read(meta_len).decode('utf-8', errors='replace').rstrip('\x00')
    m = re.search(r"StreamTitle='([^']*)'", meta_str)
    return m.group(1).strip() if m else None


# ── HTTP server ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        if parsed.path == '/api/departures':
            self._departures(qs)
        elif parsed.path == '/api/flights':
            self._flights(qs)
        elif parsed.path == '/api/buses/departures':
            self._bus_departures(qs)
        elif parsed.path == '/api/buses/vehicles':
            self._bus_vehicles(qs)
        elif parsed.path == '/api/buses/stops':
            self._bus_stops(qs)
        elif parsed.path == '/api/buses/route-stops':
            self._bus_route_stops(qs)
        elif parsed.path == '/api/hive':
            self._hive(qs)
        elif parsed.path == '/api/bods/buses':
            self._bods_buses(qs)
        elif parsed.path == '/api/bods/departures':
            self._bods_departures(qs)
        elif parsed.path == '/api/flight-route':
            self._flight_route(qs)
        elif parsed.path == '/api/airline-logo':
            self._airline_logo(qs)
        elif parsed.path == '/api/aircraft-info':
            self._aircraft_info(qs)
        elif parsed.path == '/api/radio/resolve':
            self._radio_resolve(qs)
        elif parsed.path == '/api/radio/nowplaying':
            self._radio_nowplaying(qs)
        elif parsed.path == '/api/radio/nowplaying-rp':
            self._radio_nowplaying_rp(qs)
        elif parsed.path == '/api/radio/track-info':
            self._radio_track_info(qs)
        elif parsed.path == '/health':
            self._respond(200, 'text/plain', b'ok')
        else:
            self._static(parsed.path)

    def _departures(self, qs):
        station  = (qs.get('station', ['TWY'])[0].strip().upper())[:3]
        platform = qs.get('platform', [''])[0].strip()
        limit    = min(int(qs.get('rows', ['6'])[0]), 15)
        fetch    = limit + (8 if platform else 0)
        key      = (station, platform, limit)
        now      = time.time()

        with _lock:
            if key in _cache and now - _cache[key][0] < CACHE_TTL:
                self._json(_cache[key][1])
                return

        try:
            root = _soap(station, fetch)
            data = _parse(root, platform or None, limit)
            if data is None:
                self._respond(502, 'text/plain', b'Parse failed')
                return
        except Exception as e:
            self._respond(502, 'text/plain', str(e).encode())
            return

        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _flights(self, qs):
        try:
            lat  = float(qs.get('lat',  ['51.4741'])[0])
            lon  = float(qs.get('lon',  ['-0.9752'])[0])
            dist = int(qs.get('dist',   ['100'])[0])
        except (ValueError, KeyError):
            self._respond(400, 'text/plain', b'Bad params')
            return
        # Snap lat/lon to 2 dp and dist to nearest 10nm so nearby viewports share a cache entry
        lat  = round(lat, 2)
        lon  = round(lon, 2)
        dist = max(10, round(dist / 10) * 10)
        key = ('flights', lat, lon, dist)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < FLIGHT_TTL:
                self._respond(200, 'application/json', _cache[key][1])
                return
        url = ADSB_URL.format(lat=lat, lon=lon, dist=dist)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Joggler/1.0'})
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read()
        except Exception as e:
            self._respond(502, 'text/plain', str(e).encode())
            return
        with _lock:
            _cache[key] = (now, body)
        self._respond(200, 'application/json', body)

    def _bus_departures(self, qs):
        stop = qs.get('stop', ['035091060001'])[0].strip()
        key  = ('bus_dep', stop)
        now  = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < BUS_DEP_TTL:
                self._json(_cache[key][1])
                return
        deps, err = _fetch_bus_departures(stop)
        if deps is None:
            self._respond(502, 'text/plain', err.encode())
            return
        data = {'stop': stop, 'departures': deps}
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _bus_stops(self, qs):
        key = ('bus_stops',)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < BUS_STOPS_TTL:
                self._json(_cache[key][1]); return
        # Load or fetch OSM base stop data
        file_data = _load_stops_file()
        if file_data is None:
            try:
                base_stops = _fetch_bus_stops()
            except Exception as e:
                self._respond(502, 'text/plain', str(e).encode()); return
            file_data = {'stops': base_stops}
            _save_stops_file(file_data)
        # Always merge current timetable data at serve time (improves as more
        # routes are fetched by /api/buses/route-stops over subsequent days)
        stops = [dict(s) for s in file_data['stops']]  # shallow copy
        timetable = _load_timetable_file()
        if timetable:
            atco_routes = _build_atco_routes(timetable)
            for s in stops:
                if s.get('atco') and s['atco'] in atco_routes:
                    s['routes'] = atco_routes[s['atco']]
        data = {'stops': stops}
        with _lock: _cache[key] = (now, data)
        self._json(data)

    def _bus_route_stops(self, qs):
        """Fetch timetable stop lists for all tracked routes, one at a time.
        Results are cached permanently to BUS_TIMETABLE_FILE.
        Returns {routes: [{op, route, direction, atcos: [...], error: str|null}],
                 complete: bool}
        Incomplete means some routes still lack data (rate-limited or failed)."""
        existing = _load_timetable_file() or {'routes': []}
        done = {(e['op'], e['route'], e['direction'])
                for e in existing['routes'] if not e.get('error')}
        todo = [(op, r, d) for op, r, d in BUS_TIMETABLE_ROUTES
                if (op, r, d) not in done]
        results = list(existing['routes'])
        changed = False
        for op, route, direction in todo:
            try:
                stops = _fetch_one_route_timetable(op, route, direction)
                results.append({'op': op, 'route': route, 'direction': direction,
                                'atcos': [s['atco'] for s in stops], 'error': None})
                changed = True
            except Exception as e:
                msg = str(e)
                results.append({'op': op, 'route': route, 'direction': direction,
                                'atcos': [], 'error': msg})
                # Stop on rate-limit to preserve remaining quota
                if 'Usage limits' in msg or 'Authorisation' in msg:
                    break
            time.sleep(0.5)
        if changed:
            data = {'routes': [r for r in results if not r.get('error')]}
            _save_timetable_file(data)
            # Invalidate stops cache so next fetch merges new route data
            with _lock:
                _cache.pop(('bus_stops',), None)
        complete = len([r for r in results if not r.get('error')]) == len(BUS_TIMETABLE_ROUTES)
        self._json({'routes': results, 'complete': complete})

    def _bus_vehicles(self, qs):
        key = ('bus_veh',)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < BODS_TTL:
                self._json(_cache[key][1])
                return
        buses, _ = _get_bods_cached()
        features = []
        for bus in buses:
            if not _bods_is_fresh(bus['recordedAt']):
                continue
            colours = BODS_ROUTE_COLOURS.get(bus['line'], {'bg': '#2a6080', 'fg': '#ffffff'})
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [bus['lon'], bus['lat']]},
                'properties': {
                    'vehicle':     bus['id'],
                    'operator':    BODS_OPERATOR_NAMES.get(bus['operator'], bus['operator']),
                    'line':        bus['line'],
                    'bearing':     bus['bearing'],
                    'destination': bus['destination'],
                    'background':  colours['bg'],
                    'foreground':  colours['fg'],
                },
            })
        data = {'type': 'FeatureCollection', 'features': features}
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _hive(self, qs):
        key = ('hive',)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < HIVE_TEMP_TTL:
                self._json(_cache[key][1])
                return
        sensors, err = _hive_fetch_temps()
        if sensors is None:
            self._respond(502, 'text/plain', err.encode())
            return
        result = {'sensors': sensors, 'updated': int(now)}
        with _lock:
            _cache[key] = (now, result)
        self._json(result)

    def _bods_buses(self, qs):
        buses, errors = _get_bods_cached()
        now = datetime.datetime.now(datetime.timezone.utc)
        for b in buses:
            try:
                dt = datetime.datetime.fromisoformat(b['recordedAt'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                b['ageSeconds'] = int((now - dt).total_seconds())
            except Exception:
                b['ageSeconds'] = None
        self._json({'buses': buses, 'count': len(buses), 'errors': errors})

    def _bods_departures(self, qs):
        stop = qs.get('stop', ['035091060001'])[0].strip()
        stop_info = BODS_STOPS.get(stop)
        sources   = PASSENGER_STOP_SOURCES.get(stop)
        if not stop_info or not sources:
            self._respond(400, 'text/plain', b'Unknown stop')
            return

        cache_key = ('passenger_dep', stop)
        now = time.time()
        with _lock:
            if cache_key in _cache and now - _cache[cache_key][0] < PASSENGER_TTL:
                self._json(_cache[cache_key][1])
                return

        # Fetch all operator sites in parallel
        results_list = [[] for _ in sources]
        def _fetch(idx, domain, fetch_atco, op_name):
            results_list[idx] = _fetch_passenger_stop(fetch_atco, domain, op_name)
        threads = [threading.Thread(target=_fetch, args=(i, d, a, n))
                   for i, (d, a, n) in enumerate(sources)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=9)

        all_deps = [dep for deps in results_list for dep in deps]
        all_deps.sort(key=lambda x: x['etaMins'])

        result = {
            'stop':       stop,
            'stopName':   stop_info['name'],
            'source':     'passenger',
            'departures': all_deps[:10],
        }
        with _lock:
            _cache[cache_key] = (now, result)
        self._json(result)

    def _flight_route(self, qs):
        cs = qs.get('cs', [''])[0].strip().upper()
        if not cs or not re.match(r'^[A-Z0-9]{3,10}$', cs):
            self._respond(400, 'text/plain', b'Bad callsign')
            return
        key = ('flight_route', cs)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < ROUTE_TTL:
                self._json(_cache[key][1])
                return
        try:
            url = 'https://www.flightaware.com/live/flight/' + quote(cs)
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-GB,en;q=0.9',
                'DNT': '1',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                enc = resp.headers.get('Content-Encoding', '')
            if enc == 'gzip':
                import gzip
                raw = gzip.decompress(raw)
            page = raw.decode('utf-8', errors='replace')
        except Exception:
            self._json({})
            return
        result = {}
        m = re.search(r'<meta name="origin" content="([A-Z]{4})"', page)
        if m:
            result['orig_icao'] = m.group(1)
        m = re.search(r'<meta name="destination" content="([A-Z]{4})"', page)
        if m:
            result['dest_icao'] = m.group(1)
        m = re.search(r"'origin_IATA',\s*'([A-Z]{3})'", page)
        if m:
            result['orig_iata'] = m.group(1)
        m = re.search(r"'destination_IATA',\s*'([A-Z]{3})'", page)
        if m:
            result['dest_iata'] = m.group(1)
        m = re.search(r'flight from (.+?) to (.+?)(?:&quot;|")', page)
        if m:
            result['orig'] = _html.unescape(m.group(1))
            result['dest'] = _html.unescape(m.group(2))
        m = re.search(r'"operated"\s*:\s*"/live/flight/([A-Z0-9]+)/', page)
        if m:
            result['operated_ident'] = m.group(1)
        m = re.search(r'<title>([A-Z]{2}[A-Z0-9]+) \(', page)
        if m:
            result['iata_flight'] = m.group(1)
        # Parse trackpollBootstrap JSON for richer details
        m = re.search(r'trackpollBootstrap\s*=\s*(\{)', page)
        if m:
            start = m.start(1)
            depth = 0
            end = start
            for i in range(start, min(start + 300000, len(page))):
                c = page[i]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            try:
                tb = json.loads(page[start:end])
                flights_dict = tb.get('flights', {})
                if flights_dict:
                    first_key = next(iter(flights_dict))
                    activity = flights_dict[first_key].get('activityLog', {}).get('flights', [])
                    if activity:
                        f = activity[0]
                        if f.get('aircraftTypeFriendly'):
                            result['aircraft_friendly'] = f['aircraftTypeFriendly']
                        orig_d = f.get('origin') or {}
                        dest_d = f.get('destination') or {}
                        if orig_d.get('terminal'):
                            result['orig_terminal'] = orig_d['terminal']
                        if dest_d.get('terminal'):
                            result['dest_terminal'] = dest_d['terminal']
                        if dest_d.get('gate'):
                            result['dest_gate'] = dest_d['gate']
                        if f.get('flightStatus'):
                            result['flight_status'] = f['flightStatus']
                        tt = f.get('takeoffTimes') or {}
                        lt = f.get('landingTimes') or {}
                        if tt.get('scheduled'):
                            result['depart_sched'] = tt['scheduled']
                        if tt.get('actual') or tt.get('estimated'):
                            result['depart_est'] = tt.get('actual') or tt.get('estimated')
                        if lt.get('scheduled'):
                            result['arrive_sched'] = lt['scheduled']
                        if lt.get('actual') or lt.get('estimated'):
                            result['arrive_est'] = lt.get('actual') or lt.get('estimated')
                        fp = f.get('flightPlan') or {}
                        if fp.get('directDistance'):
                            result['distance_nm'] = fp['directDistance']
                        if fp.get('ete'):
                            result['ete_s'] = fp['ete']
            except Exception:
                pass
        if result.get('orig') and result.get('dest'):
            with _lock:
                _cache[key] = (now, result)
        self._json(result)

    def _airline_logo(self, qs):
        iata = qs.get('iata', [''])[0].strip().upper()
        if not re.match(r'^[A-Z0-9]{2,3}$', iata):
            self._respond(400, 'text/plain', b'Bad iata')
            return
        logo_dir = '/home/gduthie/twyford-dashboard/logos'
        os.makedirs(logo_dir, exist_ok=True)
        path = os.path.join(logo_dir, iata + '.png')
        if not os.path.exists(path):
            try:
                url = 'http://pics.avs.io/200/60/' + iata + '.png'
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                with open(path, 'wb') as f:
                    f.write(data)
            except Exception:
                self._respond(404, 'text/plain', b'Logo not found')
                return
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._respond(500, 'text/plain', b'Read error')

    def _aircraft_info(self, qs):
        hex_code = qs.get('hex', [''])[0].strip().lower()
        if not re.match(r'^[0-9a-f]{6}$', hex_code):
            self._respond(400, 'text/plain', b'Bad hex')
            return
        cache_dir = '/home/gduthie/twyford-dashboard/aircraft-info'
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, hex_code + '.json')
        if os.path.exists(cache_path):
            try:
                age = time.time() - os.path.getmtime(cache_path)
                if age < 30 * 86400:
                    with open(cache_path) as f:
                        self._json(json.load(f))
                    return
            except Exception:
                pass
        result = {}
        fetched = False
        try:
            url = 'https://opensky-network.org/api/metadata/aircraft/icao/' + hex_code
            req = urllib.request.Request(url, headers={'User-Agent': 'Joggler-Dashboard/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if data.get('built'):
                result['built'] = data['built'][:4]
                result['built_label'] = 'Built'
            elif data.get('firstFlightDate'):
                result['built'] = data['firstFlightDate'][:4]
                result['built_label'] = 'Built'
            elif data.get('registered'):
                result['built'] = data['registered'][:4]
                result['built_label'] = "Reg'd"
            fetched = True
        except Exception:
            pass
        if fetched:
            try:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
            except Exception:
                pass
        self._json(result)

    def _radio_resolve(self, qs):
        url = qs.get('url', [''])[0]
        if not url:
            self._respond(400, 'text/plain', b'url required')
            return
        key = ('radio_resolve', url)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < RADIO_TTL:
                self._json(_cache[key][1])
                return
        try:
            stream_url = _resolve_stream_url(url)
        except Exception as e:
            self._respond(502, 'text/plain', str(e).encode())
            return
        data = {'streamUrl': stream_url}
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _radio_nowplaying_rp(self, qs):
        chan = qs.get('chan', ['0'])[0]
        key = ('radio_np_rp', chan)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < RP_TTL:
                self._json(_cache[key][1])
                return
        try:
            data = _fetch_rp_nowplaying(chan)
        except Exception:
            data = {}
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _radio_nowplaying(self, qs):
        url = qs.get('url', [''])[0]
        if not url:
            self._respond(400, 'text/plain', b'url required')
            return
        key = ('radio_np', url)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < NOWPLAYING_TTL:
                self._json(_cache[key][1])
                return
        try:
            title = _fetch_icy_nowplaying(url)
            data = {'streamTitle': title or ''}
        except Exception:
            data = {'streamTitle': ''}
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _radio_track_info(self, qs):
        artist = qs.get('artist', [''])[0].strip()
        title  = qs.get('title',  [''])[0].strip()
        if not artist and not title:
            self._json({'image': '', 'album': '', 'bio': '', 'listeners': ''})
            return
        key = ('lastfm_track', artist.lower(), title.lower())
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < LASTFM_TTL:
                self._json(_cache[key][1])
                return
        data = _fetch_lastfm_track_info(artist, title)
        with _lock:
            _cache[key] = (now, data)
        self._json(data)

    def _static(self, path):
        if '..' in path:
            self._respond(403, 'text/plain', b'Forbidden'); return
        filepath = os.path.join(APP_DIR, 'dashboard.html') if path == '/' \
                   else os.path.join(APP_DIR, path.lstrip('/'))
        if not os.path.isfile(filepath):
            self._respond(404, 'text/plain', b'Not found'); return
        ext   = os.path.splitext(filepath)[1].lower()
        ctype = MIME.get(ext, 'application/octet-stream')
        with open(filepath, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self._respond(200, 'application/json', body)

    def _respond(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress request logs


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    pass


if __name__ == '__main__':
    _load_env()
    ThreadedServer(('0.0.0.0', 5001), Handler).serve_forever()
