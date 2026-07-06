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
import gzip
import base64
from urllib.parse import urlparse, parse_qs, quote
from zoneinfo import ZoneInfo

_TZ_LONDON = ZoneInfo('Europe/London')

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

APP_DIR            = '/home/gduthie/twyford-dashboard'
AIRPORT_NAMES_FILE = os.path.join(APP_DIR, 'airport-names.json')
MIME    = {'.html': 'text/html', '.js': 'application/javascript',
           '.png':  'image/png',  '.svg': 'image/svg+xml',
           '.json': 'application/json', '.css': 'text/css'}

_cache         = {}
_lock          = threading.Lock()
_airport_names = {}

def _clean_airport_name(raw):
    for suffix in [' International Airport', ' National Airport', ' Regional Airport',
                   ' Airport', ' International', ' Regional', ' Airfield', ' Aerodrome']:
        if raw.endswith(suffix):
            return raw[:-len(suffix)].strip()
    return raw.strip()

def _load_airport_names():
    global _airport_names
    if os.path.exists(AIRPORT_NAMES_FILE):
        try:
            with open(AIRPORT_NAMES_FILE) as f:
                _airport_names = json.load(f)
            print(f'Airport names loaded: {len(_airport_names)} entries')
            return
        except Exception as e:
            print(f'Airport names cache read error: {e}')
    try:
        import csv, io
        print('Downloading airport names from OurAirports...')
        req = urllib.request.Request(
            'https://davidmegginson.github.io/ourairports-data/airports.csv',
            headers={'User-Agent': 'Joggler-Dashboard/1.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read().decode('utf-8')
        names = {}
        for row in csv.DictReader(io.StringIO(content)):
            iata = row.get('iata_code', '').strip()
            if iata and len(iata) == 3:
                names[iata] = _clean_airport_name(row.get('name', iata))
        _airport_names = names
        with open(AIRPORT_NAMES_FILE, 'w') as f:
            json.dump(names, f)
        print(f'Airport names cached: {len(names)} entries')
    except Exception as e:
        print(f'Airport names download failed: {e}')

threading.Thread(target=_load_airport_names, daemon=True).start()

# ── Bus: BODS (Bus Open Data Service) ────────────────────────────────────────
BODS_ENV_FILE   = '/home/gduthie/twyford-dashboard/.env'
BODS_API_KEY    = ''   # loaded from BODS_ENV_FILE at startup
LASTFM_API_KEY  = ''   # loaded from BODS_ENV_FILE at startup
SKYLINK_API_KEY = ''   # loaded from BODS_ENV_FILE at startup
RTT_REFRESH_TOKEN = ''  # loaded from BODS_ENV_FILE at startup
NR_USERNAME       = ''  # loaded from BODS_ENV_FILE at startup
NR_PASSWORD       = ''  # loaded from BODS_ENV_FILE at startup
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
           'services': [],
           'nrccMessages': []}

    nrcc_el = res.find('.//{*}nrccMessages')
    if nrcc_el is not None:
        for msg_el in nrcc_el.findall('.//{*}message'):
            txt = ''.join(msg_el.itertext()).strip()
            if txt:
                out['nrccMessages'].append(txt)

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
    global BODS_API_KEY, LASTFM_API_KEY, SKYLINK_API_KEY, RTT_REFRESH_TOKEN
    global NR_USERNAME, NR_PASSWORD
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
                    elif k == 'SKYLINK_API_KEY':
                        SKYLINK_API_KEY = v
                    elif k == 'RTT_REFRESH_TOKEN':
                        RTT_REFRESH_TOKEN = v
                    elif k == 'NR_USERNAME':
                        NR_USERNAME = v
                    elif k == 'NR_PASSWORD':
                        NR_PASSWORD = v
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
    result = {'image': '', 'album': '', 'bio': '', 'listeners': '', 'tags': [], 'similar': []}
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

    # Get similar artists
    try:
        d = _lfm({'method': 'artist.getSimilar', 'artist': artist, 'limit': '5', 'autocorrect': '1'})
        similar = d.get('similarartists', {}).get('artist', [])
        result['similar'] = [a['name'] for a in similar[:5] if a.get('name')]
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


# ── RTT (Real Time Trains) API ───────────────────────────────────────────────

RTT_API_BASE     = 'https://data.rtt.io'
RTT_TTL          = 30   # seconds proxy cache

# Reading↔Twyford is ~4 min.  _rtt_normalise applies a signed offset from the
# Reading schedule time: UP trains reach Twyford AFTER Reading (+), DOWN trains
# pass Twyford BEFORE arriving Reading (−); Main expresses are faster than Relief
# stoppers.  The values live inline in _rtt_normalise (3 min Main / 5 min Relief).

# Destination descriptions that indicate an UP (towards London) service
_RTT_UP_DESTS = {
    'London Paddington', 'Paddington', 'London Paddington (EL)',
    'Abbey Wood', 'Shenfield',
    'Heathrow Terminal 4', 'Heathrow Terminal 5', 'Heathrow Terminals 1-3',
}

# Long-distance / express terminals served by GWR & XC trains running the FAST
# (Main) lines through Twyford.  Used to classify Main vs Relief when no TD
# berth position and no usable line code are available.
_MAIN_DESTS = frozenset({
    'Bristol Temple Meads', 'Bristol Parkway', 'Cardiff Central',
    'Swansea', 'Carmarthen', 'Pembroke Dock', 'Fishguard Harbour',
    'Milford Haven', 'Cheltenham Spa', 'Gloucester', 'Hereford',
    'Worcester Foregate Street', 'Worcester Shrub Hill', 'Great Malvern',
    'Taunton', 'Exeter St Davids', 'Plymouth', 'Penzance', 'Paignton',
    'Newquay', 'Weston-super-Mare', 'Westbury', 'Frome', 'Castle Cary',
})
# Local / stopping terminals served on the SLOW (Relief) lines through Twyford.
# NB: "Reading" is intentionally NOT here — both Relief stoppers (Elizabeth Line,
# GWR locals) and fast GWR expresses (e.g. 1R41 on the Down Main) terminate there,
# so the headcode class (1xxx=Main) decides; a live SMART berth line overrides anyway.
_RELIEF_DESTS = frozenset({
    'Didcot Parkway', 'Newbury', 'Bedwyn', 'Basingstoke',
    'Slough', 'Maidenhead', 'Henley-on-Thames',
    'Bourne End', 'Marlow', 'Greenford', 'West Ealing',
    'Ealing Broadway', 'Hayes & Harlington', 'Gatwick Airport', 'Redhill',
})


# Location-name tokens strictly EAST of Twyford (the London side).  A train
# runs through Twyford only if exactly one of {origin, destination} is east of
# it — i.e. Twyford lies between the two ends.  This rejects both Reading-
# junction traffic that never reaches Twyford (Newbury, Basingstoke,
# Gatwick/Redhill, CrossCountry to the north) AND trains that terminate east of
# Twyford and turn back (e.g. Paddington→Maidenhead Elizabeth Line — Maidenhead
# is east of Twyford, so the train never reaches the house).
# NB: Twyford itself is NOT in this set, so trains starting/ending at Twyford
# still count as passing it.
_TWY_EAST_TOKENS = (
    'Paddington', 'Abbey Wood', 'Heathrow', 'Shenfield', 'Stratford',
    'Ealing', 'Acton', 'West Drayton', 'Hayes', 'Southall', 'Hanwell',
    'Maidenhead', 'Taplow', 'Burnham', 'Slough', 'Langley', 'Iver',
)


def _is_east(name):
    """True if a location name is strictly east (London side) of Twyford."""
    return any(tok in name for tok in _TWY_EAST_TOKENS)


def _passes_twyford(direction, origin, dest):
    """A train runs through Twyford only if Twyford lies between its two ends:
    exactly one endpoint is east of Twyford.  Both-east (e.g. Paddington→
    Maidenhead) or both-west (e.g. Reading→Newbury) never reach the house."""
    return _is_east(origin) != _is_east(dest)


def _classify_direction(line_code, dest):
    """UP (toward London) or DOWN.  Prefers the line-code U/D prefix when the
    code is a directional one (UML/DML/RL/URL…); otherwise uses the destination."""
    lc = line_code or ''
    if lc[:1] == 'U':
        return 'up'
    if lc[:1] == 'D':
        return 'down'
    if (dest in _RTT_UP_DESTS or 'Paddington' in dest
            or 'Abbey Wood' in dest or 'Heathrow' in dest):
        return 'up'
    return 'down'


# Genuine Relief/slow-line code designations at Twyford (as opposed to Reading
# junction-throat codes like WL/FVL which say nothing about the Twyford line).
_RELIEF_CODES = frozenset({
    'RL', 'URL', 'DRL', 'UDL', 'DDL', 'UBL', 'DBL', 'SL', 'EL',
})


def _classify_track(line_code, op_code, headcode, dest):
    """Main (fast) or Relief (slow) line at Twyford.  Layered fallbacks:
    operator → line code (ML-suffix) → destination character → headcode class
    (1xxx express = Main, everything else = Relief)."""
    if op_code in ('XR', 'HX'):      # Elizabeth Line / Heathrow Express — relief/electric lines
        return 'Relief'
    if op_code == 'XC':              # CrossCountry — always Main line
        return 'Main'
    lc = line_code or ''
    if lc.endswith('ML'):            # UML / DML / ML — definitely Main
        return 'Main'
    if lc in _RELIEF_CODES:          # genuine relief/slow-line designations
        return 'Relief'
    # Other codes (WL/FVL/numeric…) reflect the Reading junction throat, not the
    # Twyford line — ignore them and classify by train character.
    if dest in _MAIN_DESTS:
        return 'Main'
    if dest in _RELIEF_DESTS:
        return 'Relief'
    return 'Main' if headcode[:1] == '1' else 'Relief'

_rtt_lock         = threading.Lock()
_rtt_access_token = ''
_rtt_token_expiry = 0.0
_rtt_trains_ts    = 0.0
_rtt_trains_data  = None


def _rtt_get_token():
    global _rtt_access_token, _rtt_token_expiry
    now = time.time()
    with _rtt_lock:
        if _rtt_access_token and now < _rtt_token_expiry - 60:
            return _rtt_access_token
        if not RTT_REFRESH_TOKEN:
            return ''
        try:
            req = urllib.request.Request(
                RTT_API_BASE + '/api/get_access_token',
                headers={'Authorization': 'Bearer ' + RTT_REFRESH_TOKEN,
                         'User-Agent': 'Joggler-Dashboard/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read())
            _rtt_access_token = d['token']
            vu = d['validUntil'].replace('Z', '+00:00')
            _rtt_token_expiry = datetime.datetime.fromisoformat(vu).timestamp()
            return _rtt_access_token
        except Exception as e:
            print(f'RTT token refresh: {e}')
            return ''


def _rtt_location_query(code, time_from_iso, window_min=100):
    token = _rtt_get_token()
    if not token:
        return []
    url = (RTT_API_BASE + '/gb-nr/location'
           + '?code=' + code
           + '&timeFrom=' + quote(time_from_iso)
           + '&timeWindow=' + str(window_min))
    try:
        req = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + token,
            'User-Agent': 'Joggler-Dashboard/1.0',
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read()).get('services', [])
    except Exception as e:
        print(f'RTT location {code}: {e}')
        return []


def _rtt_normalise(svc, confirmed):
    sm  = svc.get('scheduleMetadata', {})
    td  = svc.get('temporalData', {})
    lm  = svc.get('locationMetadata', {})
    arr = td.get('arrival') or {}
    dep = td.get('departure') or {}
    pas = td.get('pass') or {}
    best = dep if dep else (arr if arr else pas)

    sched_iso  = best.get('scheduleAdvertised', '')
    actual_iso = best.get('realtimeActual', '')
    forecast_iso = best.get('realtimeForecast', '')
    late_min   = best.get('realtimeAdvertisedLateness') or 0
    # RTT often omits realtimeAdvertisedLateness even when realtimeForecast differs
    if not late_min and forecast_iso and sched_iso:
        try:
            fd = datetime.datetime.fromisoformat(forecast_iso)
            sd = datetime.datetime.fromisoformat(sched_iso)
            late_min = max(0, round((fd - sd).total_seconds() / 60))
        except Exception:
            pass
    cancelled  = best.get('isCancelled', False)

    dest_name  = (svc.get('destination') or [{}])[0].get('location', {}).get('description', '')
    orig_name  = (svc.get('origin') or [{}])[0].get('location', {}).get('description', '')
    orig_dep   = ((svc.get('origin') or [{}])[0].get('temporalData') or {}).get('scheduleAdvertised', '')
    dest_arr   = ((svc.get('destination') or [{}])[0].get('temporalData') or {}).get('scheduleAdvertised', '')

    line_code  = lm.get('line', {}).get('planned', '')
    platform   = lm.get('platform', {}).get('planned', '')
    num_veh    = lm.get('numberOfVehicles')
    op_code    = sm.get('operator', {}).get('code', '')
    headcode   = sm.get('trainReportingIdentity', '')

    direction = _classify_direction(line_code, dest_name)
    track     = _classify_track(line_code, op_code, headcode, dest_name)

    if confirmed:
        call_type  = 'STOP' if td.get('displayAs') == 'CALL' else 'PASS'
        twy_sched  = sched_iso
        twy_actual = actual_iso or forecast_iso
    else:
        # Reading is ~4 min from Twyford.  UP trains depart Reading and reach
        # Twyford AFTER (add offset); DOWN trains pass Twyford BEFORE arriving
        # Reading (subtract offset).  Main expresses run faster than Relief
        # stoppers, so the offset is smaller.
        call_type  = 'PASS'
        twy_actual = ''
        offset = 3 if track == 'Main' else 5
        delta  = offset if direction == 'up' else -offset
        if sched_iso:
            try:
                dt = datetime.datetime.fromisoformat(sched_iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_TZ_LONDON)
                twy_sched = (dt + datetime.timedelta(minutes=delta)).isoformat()
            except Exception:
                twy_sched = sched_iso
        else:
            twy_sched = ''

    # For stopping trains, pass through arrival and departure separately so
    # house_pass_ts can be computed correctly (house is east of station):
    # - DOWN stop: house passed at arrival - 15s
    # - UP stop:   house passed at departure + 15s
    twy_arr_sched = arr.get('scheduleAdvertised', '') if confirmed and call_type == 'STOP' else ''
    twy_dep_sched = dep.get('scheduleAdvertised', '') if confirmed and call_type == 'STOP' else ''
    twy_arr_actual = (arr.get('realtimeActual', '') or arr.get('realtimeForecast', '')) if confirmed and call_type == 'STOP' else ''
    twy_dep_actual = (dep.get('realtimeActual', '') or dep.get('realtimeForecast', '')) if confirmed and call_type == 'STOP' else ''

    return {
        'uid':       sm.get('uniqueIdentity', ''),
        'headcode':  sm.get('trainReportingIdentity', ''),
        'op_code':   sm.get('operator', {}).get('code', ''),
        'op_name':   sm.get('operator', {}).get('name', ''),
        'passenger': sm.get('inPassengerService', True),
        'call_type': call_type,
        'direction': direction,
        'track':     track,
        'line_code': line_code,
        'origin':    orig_name,
        'dest':      dest_name,
        'orig_dep':  orig_dep,
        'dest_arr':  dest_arr,
        'twy_sched': twy_sched,
        'twy_actual': twy_actual,
        'twy_arr_sched': twy_arr_sched,
        'twy_dep_sched': twy_dep_sched,
        'twy_arr_actual': twy_arr_actual,
        'twy_dep_actual': twy_dep_actual,
        'late_min':  late_min if not cancelled else None,
        'cancelled': cancelled,
        'status':    td.get('status'),
        'platform':  platform,
        'confirmed': confirmed,
        'num_veh':   num_veh,
    }


def _iso_to_ts(iso):
    """Convert an ISO datetime string to a UTC Unix timestamp.
    RTT returns times in Europe/London local time without a timezone suffix.
    TRUST buffer stores times in UTC with +00:00 suffix.
    Naive strings are treated as Europe/London (handles BST/GMT automatically).
    """
    try:
        dt = datetime.datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ_LONDON)
        return dt.timestamp()
    except Exception:
        return 0.0


def _rtt_build_trains():
    global _rtt_trains_ts, _rtt_trains_data
    now = time.time()
    with _lock:
        if _rtt_trains_data is not None and now - _rtt_trains_ts < RTT_TTL:
            return _rtt_trains_data

    # RTT interprets timeFrom as Europe/London LOCAL time.  Sending UTC here
    # shifted the whole window back an hour in BST: stale trains lingered and
    # the forward window shrank from ~90 to ~30 min (missing upcoming trains).
    from_dt   = datetime.datetime.now(_TZ_LONDON) - datetime.timedelta(minutes=10)
    time_from = from_dt.strftime('%Y-%m-%dT%H:%M:%S')

    # Two RTT location queries (parallel):
    #  • TWYFORD — trains that actually CALL or PASS Twyford (Relief/stopping);
    #    gives confirmed pass times.  Fast Main-line trains have no Twyford
    #    timing point and never appear here.
    #  • RDG (Reading) — bi-directional predictor.  Reading is ~4 min from
    #    Twyford: UP trains reach Twyford AFTER departing Reading; DOWN trains
    #    pass Twyford BEFORE arriving Reading.  This is the only feed that
    #    surfaces fast Down-Main expresses (Bristol/Cardiff/Plymouth/etc.).
    fetch_results = [None, None]
    def _fetch_rtt(idx, code, tfrom, window):
        fetch_results[idx] = _rtt_location_query(code, tfrom, window)
    threads = [
        threading.Thread(target=_fetch_rtt, args=(0, 'TWYFORD', time_from, 100)),
        threading.Thread(target=_fetch_rtt, args=(1, 'RDG',     time_from, 100)),
    ]
    for th in threads: th.start()
    for th in threads: th.join(timeout=15)
    twy_svcs, rdg_svcs = fetch_results

    trains   = []
    seen     = set()

    # Identity index (headcode → who/where) built from EVERYTHING RTT returned,
    # BEFORE any filtering — used later to put names on trains that arrive via
    # TRUST/CIF/TD rather than RTT.
    ident = {}
    for svc in (twy_svcs or []) + (rdg_svcs or []):
        sm = svc.get('scheduleMetadata', {})
        hc = sm.get('trainReportingIdentity', '')
        if hc and hc not in ident:
            ident[hc] = {
                'origin':    (svc.get('origin') or [{}])[0].get('location', {}).get('description', ''),
                'dest':      (svc.get('destination') or [{}])[0].get('location', {}).get('description', ''),
                'op_code':   sm.get('operator', {}).get('code', ''),
                'op_name':   sm.get('operator', {}).get('name', ''),
                'passenger': sm.get('inPassengerService', True),
            }

    for svc in (twy_svcs or []):
        uid = svc.get('scheduleMetadata', {}).get('uniqueIdentity', '')
        seen.add(uid)
        t = _rtt_normalise(svc, confirmed=True)
        if t['twy_sched'] and not t['headcode'].startswith('2H'):
            trains.append(t)

    for svc in (rdg_svcs or []):
        uid  = svc.get('scheduleMetadata', {}).get('uniqueIdentity', '')
        if uid in seen:
            continue
        # _rtt_normalise applies a signed Reading→Twyford offset based on the
        # train's own direction (UP +, DOWN −), so both directions are kept.
        t = _rtt_normalise(svc, confirmed=False)
        hc = t['headcode']
        if not t['twy_sched'] or hc.startswith('2H') or hc[:1] == '0':
            continue                      # no time / Henley branch / light-loco-bus moves
        if t['op_code'] == 'HX':          # Heathrow Express — own track, not via Twyford
            continue
        if (t.get('line_code') or '') == 'BUS':
            continue                      # rail-replacement bus
        if not _passes_twyford(t['direction'], t.get('origin', ''), t.get('dest', '')):
            continue                      # Reading-junction traffic that bypasses Twyford
        seen.add(uid)
        trains.append(t)

    # Merge NR STOMP buffer; prune entries older than 30 min.
    # Deduplicate against trains already present by headcode+time (10-min
    # window) and enrich with origin/destination from the CIF schedule.
    cutoff = now - 1800   # 30 min — purge old TRUST entries (they accumulate passenger trains)
    existing_hc = {}
    for t in trains:
        if t.get('headcode'):
            existing_hc.setdefault(t['headcode'], []).append(
                _iso_to_ts(t.get('twy_sched', '')))
    with _nr_lock:
        stale = [uid for uid, e in _nr_buffer.items()
                 if _iso_to_ts(e.get('twy_sched', '')) < cutoff]
        for uid in stale:
            del _nr_buffer[uid]
        nr_entries = [dict(e) for e in _nr_buffer.values()]
    for entry in nr_entries:
        hc = entry.get('headcode', '')
        if hc.startswith('2H'):
            continue   # Henley branch — excluded everywhere
        if not _nr_freight_hc(hc):
            continue   # Passenger trains come from RTT, not TRUST buffer
        ets = _iso_to_ts(entry.get('twy_sched', ''))
        if any(abs(ets - x) < 600 for x in existing_hc.get(hc, [])):
            continue   # same working already tracked via RTT
        if not entry.get('origin'):
            ci = _cif_ident(hc) or ident.get(hc)
            if ci:
                entry['origin']   = ci.get('origin') or ''
                entry['dest']     = ci.get('dest') or ''
                entry['orig_dep'] = ci.get('orig_dep') or ''
                entry['dest_arr'] = ci.get('dest_arr') or ''
        trains.append(entry)
        existing_hc.setdefault(hc, []).append(ets)

    # Add CIF-scheduled freight trains not yet seen via RTT or TRUST.
    # These are trains approaching Twyford whose schedule is known but whose
    # TRUST Movement (type 0003) at STANOX 74023 hasn't fired yet.
    with _cif_lock:
        rtt_hcs = {t.get('headcode') for t in trains}
        for hc, entries in _cif_index.items():
            if hc in rtt_hcs:
                continue      # already tracked via RTT or TRUST
            best = _cif_best(entries)
            if not best:
                continue      # cancelled or no valid schedule
            twy_ts = _hhmm_to_ts(best['twy_hhmm'])
            if twy_ts is None:
                continue
            if twy_ts < now - 600 or twy_ts > now + 5400:
                continue      # more than 10 min past or 90 min future
                              # (freight paths are speculative — many never run)
            twy_iso = datetime.datetime.fromtimestamp(
                twy_ts, tz=_TZ_LONDON).isoformat()

            def _cif_iso(hhmm):
                ts = _hhmm_to_ts(hhmm) if hhmm else None
                return (datetime.datetime.fromtimestamp(ts, tz=_TZ_LONDON)
                        .isoformat()) if ts is not None else ''

            trains.append({
                'uid':         'cif:' + hc,
                'headcode':    hc,
                'passenger':   False,
                'direction':   best.get('direction') or 'up',
                'track':       'Relief',
                'call_type':   'PASS',
                'confirmed':   False,
                'twy_sched':   twy_iso,
                'twy_actual':  None,
                'twy_arr_sched': '', 'twy_dep_sched': '',
                'twy_arr_actual': '', 'twy_dep_actual': '',
                'house_pass_ts': int(twy_ts),
                'op_code':     None,
                'op_name':     None,
                'dest':        _tiploc_name(best.get('dest_tip')) or None,
                'origin':      _tiploc_name(best.get('orig_tip')) or None,
                'orig_dep':    _cif_iso(best.get('orig_hhmm')) or None,
                'dest_arr':    _cif_iso(best.get('dest_hhmm')) or None,
                'late_min':    None,
                'cancelled':   False,
                'status':      None,
                'platform':    None,
                'num_veh':     None,
                'source':      'cif',
            })

    # Compute house_pass_ts for each train (unix seconds).
    # House is ~100m east of Twyford east platform signal = ~15s before/after the stop.
    # DOWN stop: train passes house on approach → arrival_sched - 15s
    # UP stop:   train passes house after departing → departure_sched + 15s
    # PASS/freight/Main: twy_sched/twy_actual is already the pass time
    for t in trains:
        call = t.get('call_type', 'PASS')
        direction = t.get('direction', '')
        late_sec = (t.get('late_min') or 0) * 60
        if call == 'STOP':
            if direction == 'down':
                actual = t.get('twy_arr_actual') or t.get('twy_actual')
                iso = actual or t.get('twy_arr_sched') or t.get('twy_sched')
                ts = _iso_to_ts(iso) - 15 if iso else 0
                if not actual and late_sec:
                    ts += late_sec
            else:
                actual = t.get('twy_dep_actual') or t.get('twy_actual')
                iso = actual or t.get('twy_dep_sched') or t.get('twy_sched')
                ts = _iso_to_ts(iso) + 15 if iso else 0
                if not actual and late_sec:
                    ts += late_sec
        else:
            actual = t.get('twy_actual')
            iso = actual or t.get('twy_sched')
            ts = _iso_to_ts(iso) if iso else 0
            if not actual and late_sec:
                ts += late_sec
        t['house_pass_ts'] = int(ts) if ts else 0

    _td_enrich_trains(trains, now, ident)

    # Drop phantom CIF freight paths.  A freight predicted to pass within the
    # next ~10 min would already be inside the tracked Reading↔Maidenhead
    # corridor and reporting TD berths.  If such an imminent CIF-scheduled train
    # has no TD sighting at all (never confirmed, no live berth, no actual pass),
    # it physically can't be that close — the path simply didn't run — so drop it
    # rather than show a bogus "N min" countdown.  Paths further out are kept as
    # genuine timetable predictions; they reappear here once TD actually sees them.
    def _phantom_cif(t):
        if t.get('source') != 'cif':
            return False
        if t.get('confirmed') or t.get('td_berth') or t.get('twy_actual'):
            return False
        ts = t.get('house_pass_ts') or 0
        return 0 < ts < now + 600
    trains = [t for t in trains if not _phantom_cif(t)]

    # Drop stale entries: anything that passed (or should have passed) long ago.
    # Confirmed trains stay 30 min (feeds the "last train" strip); unconfirmed
    # estimates 12 min — a scheduled train that never showed shouldn't linger.
    def _fresh(t):
        ts = t.get('house_pass_ts') or _iso_to_ts(t.get('twy_sched', ''))
        if not ts:
            return False
        keep = 1800 if (t.get('twy_actual') or t.get('confirmed')) else 720
        return ts > now - keep

    trains = [t for t in trains if _fresh(t)]
    trains.sort(key=lambda t: t.get('house_pass_ts') or _iso_to_ts(t.get('twy_sched', '')))
    result = {'trains': trains, 'ts': int(now)}

    with _lock:
        _rtt_trains_data = result
        _rtt_trains_ts   = now
    return result


# Max seconds of "progress within the current berth" to credit when smoothing the
# ETA countdown.  A moving train steps to the next berth within ~this long; sitting
# longer means dwelling/held, not progressing.
_BERTH_STEP_S = 50


def _berth_eta_to_house_s(area, berth_str, direction, is_passenger, is_main, age_s):
    """
    Estimate seconds until a train at this berth reaches the house, using the
    SMART berth model (real chainage distance).  Returns None if the berth is
    unknown or the train has already passed the house.  Returns (eta_s, held).
    The line (Main/Relief) is taken from SMART, not the caller's `is_main`.
    """
    info = _berth_info(area, berth_str)
    if not info or info['dist_mi'] is None:
        return None
    dist_mi = info['dist_mi']            # signed: + = west of house, − = east of house
    # to_go = miles still to travel to the house: + = approaching, − = already passed.
    # DOWN trains approach from the east (negative dist) heading west; UP trains
    # approach from the west (positive dist) heading east.
    if direction == 'down':
        to_go = -dist_mi
    elif direction == 'up':
        to_go = dist_mi
    else:
        return None
    if to_go > 8.0:
        return None                      # too far out: constant-speed estimate is
                                         # unreliable (intermediate stops) — keep the
                                         # RTT schedule instead
    main = (info['line'] == 'Main') if info['line'] else is_main
    if is_passenger is False:            # explicit freight — runs slower
        speed_mph = 50.0 if main else 35.0
    else:
        speed_mph = 90.0 if main else 60.0
    travel_s = to_go / speed_mph * 3600.0
    if to_go < -0.3:
        # Already past the house (berth says so).  Report it as passed — a negative
        # ETA — so a stale schedule + lateness can't show it as still upcoming
        # (e.g. a delayed DOWN train now sitting west at Reading).
        return (travel_s, False)
    # age_s smooths the countdown between berth steps, but a CA berth is a POINT,
    # not a section the train slides along.  A train DWELLING at a station or HELD
    # at a signal sits in one berth far longer than it takes a moving train to step
    # out of it, so crediting the full age would wrongly advance it (e.g. a train
    # sitting at Reading platform showing "1 min" while 5 mi away).  Credit at most
    # one berth-step of real progress; beyond that, treat it as not moving toward
    # the house and use the full travel time from here, flagged held/dwelling.
    if age_s > _BERTH_STEP_S * 2 and to_go > 0.6:
        return (travel_s, True)
    eta = travel_s - min(age_s, _BERTH_STEP_S)
    return (eta, False)


# ── SMART berth → line & position model ──────────────────────────────────────
# SMART (NR open data) maps every TD berth step to a line (via FROMLINE U/D +
# PLATFORM) and a STANOX/location.  Combined with BPLAN chainage (validated:
# kmvalue ÷ 1000 ≈ miles × 1.609 from Paddington) this gives each berth its
# line (Main/Relief) and signed distance from the house.
_SMART_URL = ('https://publicdatafeeds.networkrail.co.uk/ntrod/'
              'SupportingFileAuthenticate?type=SMART')

# Miles from London Paddington for SMART STANMEs on/near the GWML through
# Twyford (house ≈ Twyford station + 200 m east).  Anchors validated vs BPLAN.
_HOUSE_MI = 30.9
_STANME_MI = {
    'HANWELL': 7.1, 'SOUTHALL': 9.1, 'STHALLOCO': 9.1, 'HAYES&HAR': 10.4,
    'STOCKLYJN': 10.8, 'AIRPORTJN': 11.0, 'LHR TUN J': 11.5, 'LHR T 2&3': 13.0,
    'LHR TML 4': 13.5, 'LHR TML 5': 14.5, 'W DRAYTON': 13.2, 'IVER': 14.5,
    'LANGLEY': 15.6, 'SLOUGH': 18.4, 'BURNHAM': 19.9, 'TAPLOW': 21.8,
    'MAIDENHED': 24.2, 'MDNHDMIDS': 24.4, 'MDNHD CS': 24.4,
    'TWYFORD': 31.0, 'HENLEYONT': 31.0,
    'READWTORJ': 35.5, 'RDGSPURJN': 35.7, 'READNG': 36.0, 'READGWEST': 36.4,
    'TILEHURST': 38.7, 'PANGBORNE': 41.6, 'GORING&ST': 44.8, 'CHOLSEY': 48.3,
}

_smart_lock  = threading.Lock()
_smart_berth = {}   # (area, berth) → {'dir','line','stanme','dist_mi'}
_smart_ts    = 0.0


def _smart_line(plat, fromline):
    """Map a SMART platform / FROMLINE to Main or Relief at the Twyford corridor.
    GWML convention: platform 1=Down Main, 2=Up Main, 3=Down Relief, 4/5=Up Relief."""
    if plat in ('1', '2'):
        return 'Main'
    if plat in ('3', '4', '5'):
        return 'Relief'
    if fromline == 'M':
        return 'Main'
    if fromline == 'R':
        return 'Relief'
    return ''


def _load_smart():
    """Download the NR SMART berth reference, build the berth → line/position map."""
    global _smart_berth, _smart_ts
    if not NR_USERNAME or not NR_PASSWORD:
        print('SMART: NR credentials not available, skipping')
        return
    try:
        creds = base64.b64encode(f'{NR_USERNAME}:{NR_PASSWORD}'.encode()).decode()
        req = urllib.request.Request(_SMART_URL, headers={
            'Authorization': 'Basic ' + creds, 'User-Agent': 'twyford-dashboard/1.0'})

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None

        opener = urllib.request.build_opener(_NoRedirect())
        s3_url = None
        try:
            with opener.open(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                s3_url = e.headers.get('Location')
            else:
                raise
        if s3_url:
            with urllib.request.urlopen(s3_url, timeout=90) as resp:
                raw = resp.read()
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        data = json.loads(raw)
        recs = data['BERTHDATA'] if isinstance(data, dict) and 'BERTHDATA' in data else data

        # Aggregate every (area, berth) seen in a step.  The FROMLINE/PLATFORM/
        # STANME describe the step's line & place; index BOTH the from and to
        # berths so destination-only berths (e.g. the Twyford throat 1650/1668)
        # are also covered.  FROM berths get a higher weight (more authoritative).
        from collections import Counter
        agg = {}
        for r in recs:
            td = r.get('TD')
            if td not in ('D1', 'D4', 'D6'):
                continue
            fl = (r.get('FROMLINE') or '').strip()
            pl = (r.get('PLATFORM') or '').strip()
            sm = (r.get('STANME') or '').strip()
            for b, w in ((r.get('FROMBERTH', ''), 2), (r.get('TOBERTH', ''), 1)):
                if not b:
                    continue
                d = agg.setdefault((td, b), {'dir': Counter(), 'plat': Counter(),
                                             'stanme': Counter(), 'fl': Counter()})
                if fl in ('U', 'D'):
                    d['dir'][fl] += w
                if fl:
                    d['fl'][fl] += w
                if pl:
                    d['plat'][pl] += w
                if sm:
                    d['stanme'][sm] += w

        table = {}
        for key, d in agg.items():
            direction = (d['dir'].most_common(1)[0][0] if d['dir'] else '')
            plat      = (d['plat'].most_common(1)[0][0] if d['plat'] else '')
            fl        = (d['fl'].most_common(1)[0][0] if d['fl'] else '')
            stanme    = (d['stanme'].most_common(1)[0][0] if d['stanme'] else '')
            line      = _smart_line(plat, fl)
            mi        = _STANME_MI.get(stanme)
            table[key] = {
                'dir':     'down' if direction == 'D' else ('up' if direction == 'U' else ''),
                'line':    line,
                'stanme':  stanme,
                'dist_mi': (mi - _HOUSE_MI) if mi is not None else None,
            }
        with _smart_lock:
            _smart_berth = table
            _smart_ts = time.time()
        print(f'SMART: loaded {len(table)} D1/D4/D6 berths')
    except Exception as e:
        print(f'SMART load failed: {e}')


# Coarse fallback: (line, signed dist_mi) for every corridor berth Reading↔
# Maidenhead that SMART's STANOX anchoring and the CA chain-learner leave without
# a position.  dist_mi: + = west (Reading side), − = east (London side); values
# interpolated from the verified west→east berth order between the station
# chainages in _STANME_MI (Reading +5.1, Kennet Br +3.34, Twyford +0.1,
# Maidenhead −6.7).  Deliberately approximate but MONOTONIC per line — its job is
# only to keep a train inside the corridor and give it a rough ETA through the
# unanchored near-house throat berths (e.g. 1623/1626), where SMART has no
# reporting point, so a train seen leaving Maidenhead is tracked all the way in
# instead of vanishing from the list the moment it enters a positionless berth.
_BERTH_MI = {
    # Up Main (west→east; house crossing observed live at 1640→1626)
    '1672': ('Main', 5.1), '1666': ('Main', 4.4), '1662': ('Main', 3.6),
    '1658': ('Main', 3.0), '1650': ('Main', 2.1), '1646': ('Main', 1.2),
    '1640': ('Main', 0.25), '1626': ('Main', -0.15), '1618': ('Main', -0.6),
    '1614': ('Main', -1.1), '1610': ('Main', -1.8), '1606': ('Main', -2.6),
    '1602': ('Main', -3.5), '0596': ('Main', -4.6), '0592': ('Main', -5.6),
    '0570': ('Main', -6.7), '0566': ('Main', -7.5),
    # Up Relief (west→east)
    '1676': ('Relief', 5.1), '1668': ('Relief', 3.34), '1664': ('Relief', 3.0),
    '1660': ('Relief', 2.6), '1652': ('Relief', 2.0), '1648': ('Relief', 1.5),
    '1644': ('Relief', 1.0), '1642': ('Relief', 0.5), '1630': ('Relief', 0.1),
    '1628': ('Relief', -0.3), '1624': ('Relief', -0.8), '1622': ('Relief', -1.3),
    '1620': ('Relief', -1.8), '1616': ('Relief', -2.4), '1612': ('Relief', -3.1),
    '1608': ('Relief', -3.9), '1604': ('Relief', -4.8), '0598': ('Relief', -5.7),
    '0594': ('Relief', -6.3), '0574': ('Relief', -6.7), '0576': ('Relief', -6.7),
    '0568': ('Relief', -7.5),
    # Down Relief (west→east)
    '1687': ('Relief', 5.1), '1677': ('Relief', 4.0), '1669': ('Relief', 3.34),
    '1665': ('Relief', 2.5), '1661': ('Relief', 1.5), '1657': ('Relief', 0.6),
    '1637': ('Relief', 0.1), '1635': ('Relief', -0.3), '1631': ('Relief', -0.8),
    '1627': ('Relief', -1.4), '1623': ('Relief', -2.1), '1611': ('Relief', -3.0),
    '1607': ('Relief', -4.0), '1603': ('Relief', -5.2), '0595': ('Relief', -6.2),
    '0577': ('Relief', -6.7), '0571': ('Relief', -7.5),
    # Down Main (west→east)
    '1675': ('Main', 5.1), '1667': ('Main', 3.5), '1663': ('Main', 2.0),
    '1659': ('Main', 0.6), '1655': ('Main', 0.1), '1633': ('Main', -0.4),
    '1629': ('Main', -1.0), '1625': ('Main', -1.7), '1621': ('Main', -2.5),
    '1609': ('Main', -3.6), '1605': ('Main', -4.6), '1601': ('Main', -5.6),
    '0593': ('Main', -6.3), '0573': ('Main', -6.7), '0569': ('Main', -7.5),
}


def _berth_info(area, berth_str):
    """Return {'dir','line','stanme','dist_mi'} for a TD berth, or None.
    SMART is authoritative for line/place; CA-interpolated positions fill in
    dist_mi for intermediate berths SMART doesn't carry; a coarse static
    _BERTH_MI table is the last-resort fill for the unanchored near-house
    throat berths so corridor trains aren't dropped there."""
    with _smart_lock:
        info = _smart_berth.get((area, berth_str))
    if info and info.get('dist_mi') is not None:
        return info
    with _chain_lock:
        cd = _chain_pos.get((area, berth_str))
    if cd is None:
        fb = _BERTH_MI.get(berth_str)
        if fb:
            line, mi = fb
            if info:
                info = dict(info)
                if not info.get('line'):
                    info['line'] = line
                info['dist_mi'] = mi
                return info
            return {'dir': '', 'line': line, 'stanme': '', 'dist_mi': mi}
        return info                      # unknown position → caller falls back to schedule
    if info:
        info = dict(info); info['dist_mi'] = cd
        return info
    return {'dir': '', 'line': '', 'stanme': '', 'dist_mi': cd}


def _rebuild_chain_positions():
    """Interpolate dist_mi for berths SMART doesn't anchor, by walking the CA
    adjacency to the nearest anchored berths up- and down-stream and dividing the
    distance by cumulative transit time.  Conservative: only assigns a position
    when the berth is bracketed by two anchors within a few hops on both sides."""
    with _smart_lock:
        anchors = {k: v['dist_mi'] for k, v in _smart_berth.items()
                   if v.get('dist_mi') is not None}
    with _chain_lock:
        succ = {k: dict(v) for k, v in _ca_succ.items()}
        pred = {k: dict(v) for k, v in _ca_pred.items()}
        transit = {k: v[0] for k, v in _ca_transit.items()}

    def walk(start, graph):
        """From `start`, follow the most-travelled edge until an anchor; return
        (anchor_key, cumulative_transit_seconds) or None.  Max 12 hops."""
        cum = 0.0
        cur = start
        seen = {cur}
        for _ in range(12):
            nxt = graph.get(cur)
            if not nxt:
                return None
            best = max(nxt, key=nxt.get)
            nkey = (cur[0], best)
            if nkey in seen:
                return None
            cum += transit.get(cur, 30.0)    # time spent in `cur` before stepping
            if nkey in anchors:
                return (nkey, cum)
            seen.add(nkey)
            cur = nkey
        return None

    out = {}
    berths = set(succ) | set(pred)
    for key in berths:
        if key in anchors:
            continue
        fwd = walk(key, succ)              # downstream toward an anchor
        bwd = walk(key, pred)              # upstream toward an anchor
        if not fwd or not bwd:
            continue
        a_fwd, t_fwd = fwd
        a_bwd, t_bwd = bwd
        if a_fwd == a_bwd or (t_fwd + t_bwd) <= 0:
            continue
        d_fwd, d_bwd = anchors[a_fwd], anchors[a_bwd]
        # position = bwd anchor + fraction of the way (by transit time) to fwd anchor
        frac = t_bwd / (t_bwd + t_fwd)
        dist = d_bwd + (d_fwd - d_bwd) * frac
        # sanity: must lie between the two bracketing anchor distances
        lo, hi = sorted((d_fwd, d_bwd))
        if lo - 0.2 <= dist <= hi + 0.2:
            out[key] = round(dist, 2)
    with _chain_lock:
        _chain_pos.clear()
        _chain_pos.update(out)
    return len(out)


def _save_chain():
    try:
        with _chain_lock:
            data = {
                'succ': {f'{a}|{b}': v for (a, b), v in _ca_succ.items()},
                'pred': {f'{a}|{b}': v for (a, b), v in _ca_pred.items()},
                'transit': {f'{a}|{b}': v for (a, b), v in _ca_transit.items()},
            }
        with open(_CHAIN_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f'chain save failed: {e}')


def _load_chain():
    global _ca_succ, _ca_pred, _ca_transit
    try:
        with open(_CHAIN_FILE) as f:
            data = json.load(f)
        def unkey(d):
            return {tuple(k.split('|', 1)): v for k, v in d.items()}
        with _chain_lock:
            _ca_succ = unkey(data.get('succ', {}))
            _ca_pred = unkey(data.get('pred', {}))
            _ca_transit = unkey(data.get('transit', {}))
        print(f'chain: loaded {len(_ca_succ)} berth edges')
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'chain load failed: {e}')


def _chain_refresh_loop():
    """Periodically rebuild interpolated positions and persist the learned model."""
    _load_chain()
    while True:
        time.sleep(120)
        try:
            n = _rebuild_chain_positions()
            _save_chain()
        except Exception as e:
            print(f'chain refresh: {e}')


def _td_enrich_trains(trains, now, ident=None):
    """Enrich train list with TD house-crossing events and live berth positions,
    then synthesise entries for live corridor trains no schedule source knew."""
    ident = ident or {}
    cutoff_pos   = now - 300
    cutoff_house = now - 3600
    td_pos = {}
    with _td_lock:
        for entry in reversed(_td_buffer):
            hc = entry['descr']
            if hc not in td_pos and entry['ts'] > cutoff_pos:
                td_pos[hc] = entry
    with _td_house_lock:
        house_evts = {hc: e for hc, e in _td_house_events.items()
                      if e['ts'] > cutoff_house}
    train_hcs = set()
    for t in trains:
        hc = t.get('headcode', '')
        if not hc:
            continue
        train_hcs.add(hc)
        h = house_evts.get(hc)
        if h:
            expected = t.get('house_pass_ts') or 0
            if not expected or abs(h['ts'] - expected) < 1800:
                evt = h['event']
                t['confirmed'] = True
                t['td_track']  = h['track']
                if evt == 'at_house':
                    if not t.get('twy_actual'):
                        t['twy_actual']    = datetime.datetime.fromtimestamp(
                            h['ts'], tz=datetime.timezone.utc).isoformat()
                        t['house_pass_ts'] = int(h['ts'])
                    t['at_station'] = False
                elif evt == 'approaching':
                    t['at_station'] = False
        pos = td_pos.get(hc)
        if pos:
            t['confirmed']    = True
            t['td_berth']     = pos['to']
            t['td_berth_age'] = int(now - pos['ts'])
            berth_age = int(now - pos['ts'])
            # Live berth line (from SMART) is ground truth — the train is
            # physically on that line — so it overrides the heuristic `track`
            # (e.g. a fast Paddington→Reading express on the Down Main whose
            # destination "Reading" otherwise reads as a Relief stopper).
            binfo = _berth_info(pos['area'], pos['to'])
            if binfo and binfo.get('line'):
                t['track'] = binfo['line']
            # Live position summary so frontends don't have to join /api/td-live
            if binfo:
                if binfo.get('dist_mi') is not None:
                    t['td_dist_mi'] = round(binfo['dist_mi'], 2)
                if binfo.get('stanme'):
                    t['td_place'] = binfo['stanme']
            if (pos['area'] == 'D6'
                    and pos['to'] in ('1612', '1608', '1604')
                    and t.get('direction') == 'up'
                    and t.get('call_type') == 'STOP'
                    and berth_age > 45
                    and not t.get('twy_actual')):
                t['at_station'] = True
            # Refine house_pass_ts from the live berth position (SMART distance).
            elif (not t.get('twy_actual')
                    and not t.get('at_station')
                    and berth_age < 300):
                res = _berth_eta_to_house_s(
                    pos['area'], pos['to'],
                    t.get('direction', ''),
                    t.get('passenger'),
                    t.get('track', '') == 'Main',
                    berth_age,
                )
                if res is not None:
                    eta_s, held = res
                    if held:
                        # Dwelling at a station / held at a signal: floor the ETA
                        # (can't pass before the travel time) and flag it.
                        t['held'] = True
                    # Live berth position is authoritative (beats the schedule),
                    # whether the train is approaching (+) or has passed (−).
                    t['house_pass_ts'] = int(now + eta_s)
                    t['td_eta_s'] = int(eta_s)
    # Synthesise entries for trains PHYSICALLY inside the Reading↔Maidenhead
    # corridor that no schedule source matched.  A live berth fix strictly
    # between Maidenhead and the house (Down) or between Reading and the house
    # (Up), moving toward the house, WILL pass it — there is no turnback in
    # between — so physical presence beats any name-token corridor heuristic.
    # (Trains sitting AT Maidenhead/Reading stations are excluded: they may
    # terminate/reverse there, e.g. Elizabeth Line Maidenhead terminators.)
    # Identity comes from the RTT pre-filter index or today's CIF freight.
    for hc, pos in td_pos.items():
        if hc in train_hcs:
            continue
        if hc.startswith('2H') or hc[:1] == '0':
            continue          # Henley branch shuttle / light-loco-bus moves
        age = now - pos['ts']
        if age > 180:
            continue          # stale fix — may have stopped or left the area
        info = _berth_info(pos['area'], pos['to'])
        if not info or info.get('dist_mi') is None:
            continue
        d = info['dist_mi']
        direction = info.get('dir') or ''
        if not direction and pos.get('from'):
            finfo = _berth_info(pos['area'], pos['from'])
            fd = (finfo['dist_mi']
                  if finfo and finfo.get('dist_mi') is not None else None)
            if fd is not None and fd != d:
                direction = 'down' if d > fd else 'up'
        if not direction:
            continue
        if not ((direction == 'down' and -6.3 < d < -0.05)
                or (direction == 'up' and 0.05 < d < 4.95)):
            continue          # outside the no-turnback corridor
        who = ident.get(hc) or {}
        ci  = _cif_ident(hc)
        if not who.get('origin') and ci:
            who = dict(who, origin=ci['origin'], dest=ci['dest'],
                       orig_dep=ci['orig_dep'], dest_arr=ci['dest_arr'])
        # If we KNOW this train's endpoints and they say it never reaches
        # Twyford, trust that over a lone berth fix in the Reading throat.  A
        # CrossCountry service from the north that terminates/reverses at
        # Reading (Manchester→Reading) sits in a D1 throat berth at dist ~4.8
        # mi and — with an ambiguous from-berth — can read as "up, approaching
        # the house".  It never passes Twyford, so don't synthesise it.
        if (who.get('origin') and who.get('dest')
                and not _passes_twyford(direction, who['origin'], who['dest'])):
            continue
        passenger = who['passenger'] if 'passenger' in who else (hc[:1] in '129')
        line = info.get('line') or ''
        is_main = (line == 'Main') if line else (hc[:1] == '1')
        res = _berth_eta_to_house_s(pos['area'], pos['to'], direction,
                                    passenger, is_main, int(age))
        if res is None:
            continue
        eta_s, held = res
        pass_ts = int(now + eta_s)
        trains.append({
            'uid':        'td:' + hc,
            'headcode':   hc,
            'op_code':    who.get('op_code') or '',
            'op_name':    who.get('op_name') or '',
            'passenger':  passenger,
            'call_type':  'PASS',
            'direction':  direction,
            'track':      line or ('Main' if is_main else 'Relief'),
            'origin':     who.get('origin') or '',
            'dest':       who.get('dest') or '',
            'orig_dep':   who.get('orig_dep') or '',
            'dest_arr':   who.get('dest_arr') or '',
            'twy_sched':  datetime.datetime.fromtimestamp(
                              pass_ts, tz=_TZ_LONDON).isoformat(),
            'twy_actual': '',
            'twy_arr_sched': '', 'twy_dep_sched': '',
            'twy_arr_actual': '', 'twy_dep_actual': '',
            'house_pass_ts': pass_ts,
            'td_eta_s':   int(eta_s),
            'td_berth':   pos['to'],
            'td_berth_age': int(age),
            'td_dist_mi': round(d, 2),
            'td_place':   info.get('stanme') or '',
            'held':       bool(held),
            'late_min':   None,
            'cancelled':  False,
            'status':     None,
            'platform':   None,
            'num_veh':    None,
            'confirmed':  True,
            'source':     'td',
        })


# ── Network Rail STOMP (freight trains) ──────────────────────────────────────

try:
    import stomp as _stomp_module
    _HAS_STOMP = True
except ImportError:
    _HAS_STOMP = False

# STANOX → config for buffering TRUST movements to supplement RTT.
# Watched stanoxes: only those where TRUST fires for trains RTT might miss (freight pass-through).
# 74023 = Twyford (all trains incl. freight PASS). Offset=0: train is already at Twyford.
# freight_only=False: buffer all trains (passenger deduped later against RTT headcode+time).
_NR_STANOX_WATCH = {
    '74023': {                   # Twyford: all WTT timing-point trains incl. freight
        'main_plats':  frozenset(),  # no platform info for pass-through freight
        'up_fast':    0,  'down_fast':  0,
        'up_slow':    0,  'down_slow':  0,
        'freight_only': False,
    },
}

_nr_lock      = threading.Lock()
_nr_buffer    = {}   # uid → train dict
_nr_conn      = None
_nr_running   = False
_nr_start_lock     = threading.Lock()
_nr_page_active_ts = 0.0     # time of last /api/trains request
_NR_IDLE_TIMEOUT   = 90      # seconds without a poll → disconnect STOMP

_td_lock      = threading.Lock()
_td_buffer    = []   # recent TD CA (berth step) messages, newest last, D1/D4/D6 only
_TD_BUF_MAX   = 3000
_TD_AREAS     = {'D1', 'D4', 'D6'}   # Thames Valley SC: Reading, Hayes, Maidenhead

# ── CA berth-chain learning ──────────────────────────────────────────────────
# SMART only positions berths that are TRUST reporting points; intermediate
# signal berths (e.g. the Ruscombe/Twyford throat 1606/1614/1650) have no SMART
# row.  The CA stream steps every train through every berth, so we learn the
# berth adjacency + per-berth transit time from it and interpolate the missing
# positions along the chain between SMART anchors.  Persisted to disk so it
# survives restarts and keeps improving.
_CHAIN_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'berth_chain.json')
_chain_lock   = threading.Lock()
_ca_succ      = {}   # (area, berth) → {next_berth: count}
_ca_pred      = {}   # (area, berth) → {prev_berth: count}
_ca_transit   = {}   # (area, berth) → [ewma_seconds, samples]
_ca_last_pos  = {}   # headcode → (area, berth, ts) — to measure transit time
_chain_pos    = {}   # (area, berth) → dist_mi   (interpolated; merged into _berth_info)
_chain_dirty  = False


def _ca_observe(area, frm, to, hc, ts):
    """Record one CA berth step into the adjacency + transit-time model."""
    global _chain_dirty
    with _chain_lock:
        if frm and to:
            _ca_succ.setdefault((area, frm), {})
            _ca_succ[(area, frm)][to] = _ca_succ[(area, frm)].get(to, 0) + 1
            _ca_pred.setdefault((area, to), {})
            _ca_pred[(area, to)][frm] = _ca_pred[(area, to)].get(frm, 0) + 1
            _chain_dirty = True
        # transit time of `frm` = now − when this train entered `frm`
        prev = _ca_last_pos.get(hc)
        if prev and prev[0] == area and prev[1] == frm and ts > prev[2]:
            dt = ts - prev[2]
            if 2 <= dt <= 600:
                cur = _ca_transit.get((area, frm))
                if cur is None:
                    _ca_transit[(area, frm)] = [float(dt), 1]
                else:
                    cur[0] = cur[0] * 0.8 + dt * 0.2   # EWMA
                    cur[1] += 1
        _ca_last_pos[hc] = (area, to, ts)

_sf_lock      = threading.Lock()
_sf_state     = {}   # (area, address) → {'data': hex_str, 'ts': unix_seconds}

# D6 berth transitions that indicate a train passing or approaching the house.
# House is ~200 m east of Twyford station, at the Up/Down Relief crossover.
# House-crossing detection is DISTANCE-based: a CA berth step whose from/to
# distances (SMART + chain-interpolated) straddle 0 means the train physically
# crossed the house on that step.  (The old static berth-pair table used D6
# berths 0569/0573/0577 etc., which the SMART recalibration showed are at
# MAIDENHEAD, ~6.7 mi east — it fired "at house" ~7 min early for Down trains.)
_td_house_lock   = threading.Lock()
_td_house_events = {}   # headcode → {ts, track, event} — most recent event only


def _detect_house_event(area, frm, to):
    """Classify a CA berth step relative to the house using the SMART/chain
    distance model (dist_mi: + = west of house, − = east).  Returns
    ('at_house'|'approaching', 'Down Main'/'Up Relief'/…) or None.

    at_house fires only on a genuine zero-crossing (both berth distances known
    and straddling 0).  This gives the right semantics for stoppers too: an Up
    train dwelling at Twyford (station is west of the house) only fires when it
    departs east; a Down train fires on arrival — both are the moments the
    train actually passes the house."""
    info_to = _berth_info(area, to)
    if not info_to or info_to.get('dist_mi') is None:
        return None
    d_to = info_to['dist_mi']
    info_frm = _berth_info(area, frm) if frm else None
    d_frm = (info_frm['dist_mi']
             if info_frm and info_frm.get('dist_mi') is not None else None)
    line = info_to.get('line') or (info_frm.get('line') if info_frm else '') or ''
    # Lines through Twyford are directional, so SMART's per-berth dir is the
    # train's direction; fall back to the step's sign (dist increasing = down).
    direction = info_to.get('dir') or (info_frm.get('dir') if info_frm else '')
    if not direction and d_frm is not None and d_frm != d_to:
        direction = 'down' if d_to > d_frm else 'up'
    if not direction:
        return None
    track = ('Down ' if direction == 'down' else 'Up ') + (line or 'line')
    if (d_frm is not None and d_frm != d_to
            and abs(d_to - d_frm) < 3.0 and abs(d_to) < 1.6 and abs(d_frm) < 1.6
            and min(d_frm, d_to) <= 0.0 <= max(d_frm, d_to)):
        return ('at_house', track)
    # approaching: inside a mile, getting closer, and on the approach side
    if (d_frm is not None and abs(d_to) < 1.0 and abs(d_to) < abs(d_frm)
            and ((direction == 'down' and d_to < 0)
                 or (direction == 'up' and d_to > 0))):
        return ('approaching', track)
    return None


def _nr_freight_hc(hc):
    return bool(hc) and hc[0] in '45678'


# ── CIF Freight Schedule (pre-arrival visibility) ──────────────────────────

_CIF_URL = ('https://publicdatafeeds.networkrail.co.uk/ntrod/'
            'CifFileAuthenticate?type=CIF_FREIGHT_FULL_DAILY&day=toc-full')

# TIPLOCs east of Twyford (towards London) — used to infer UP direction
_CIF_EAST = frozenset({
    'MAIDNHD', 'TAPLOW', 'BURNHMB', 'SLOUGH', 'LANGLEY', 'IVER',
    'WDRYTON', 'WSTDRTN', 'ACTNMLJ', 'ACTNCAN', 'ACTNMLN', 'ACTNWLJ',
    'SOUTHLL', 'STLACTHN', 'LNGSF', 'PADTON', 'OLDOAKC',
    'FELTHAM', 'STAINES', 'WLSDJN', 'WLSDNHJ', 'COLNBRK', 'HTRWAPT',
})
# TIPLOCs west of Twyford (towards Bristol) — used as origin fallback
_CIF_WEST = frozenset({
    'WHATLYQ', 'WESTBRY', 'FRMJNTN', 'FROME', 'AVNMTH', 'AVONMTH',
    'BRSTLPW', 'BRSTOAL', 'BRSTLTM', 'SWINDON', 'SWNDON',
    'DIDCOTP', 'RDNGWST', 'RDNGJSW', 'RDNG', 'THEALE', 'NEWBURY',
    'BEDWYN', 'KEMBLE', 'STROUD', 'CHELTNM', 'GLSTRCA', 'HEREFD',
    'WORCSTR', 'BRNGRVE', 'MKNTJN', 'SEVERNB', 'MEREHEAD',
})

_cif_lock  = threading.Lock()
_cif_index = {}    # headcode → list of {uid, twy_hhmm, direction, stp}
_cif_ts    = 0.0   # unix time of last successful load


def _cif_direction(locs, twy_idx):
    """Derive UP/DOWN from the TIPLOC sequence relative to Twyford."""
    for loc in locs[twy_idx + 1:]:
        tip = loc.get('tiploc_code', '').strip()
        if tip in _CIF_EAST:
            return 'up'
        if tip in _CIF_WEST:
            return 'down'
    for loc in locs[:twy_idx]:
        tip = loc.get('tiploc_code', '').strip()
        if tip in _CIF_WEST:
            return 'up'    # origin is west → heading east (UP)
        if tip in _CIF_EAST:
            return 'down'  # origin is east → heading west (DOWN)
    return None


def _cif_best(entries):
    """Pick highest-priority non-cancelled schedule entry (O > N > P; skip C)."""
    priority = {'O': 0, 'N': 1, 'P': 2}
    valid = [e for e in entries if e.get('stp') != 'C']
    if not valid:
        return None
    return min(valid, key=lambda e: priority.get(e.get('stp', 'P'), 99))


def _hhmm_to_ts(hhmm):
    """Convert CIF HHMM string (BST local) to Unix timestamp for today."""
    try:
        h, m = int(hhmm[:2]), int(hhmm[2:4])
        today = datetime.date.today()
        naive = datetime.datetime(today.year, today.month, today.day, h, m)
        return naive.replace(tzinfo=_TZ_LONDON).timestamp()
    except Exception:
        return None


# ── CORPUS (TIPLOC → location name) ─────────────────────────────────────────
# NROD reference file; same 302→S3 auth dance as SMART/CIF.  Used to give
# freight trains readable origins/destinations from their CIF TIPLOCs.
_CORPUS_URL = ('https://publicdatafeeds.networkrail.co.uk/ntrod/'
               'SupportingFileAuthenticate?type=CORPUS')
_corpus_lock = threading.Lock()
_corpus_map  = {}   # TIPLOC → 'LOCATION NAME' (NLCDESC, all caps)


def _load_corpus():
    global _corpus_map
    if not NR_USERNAME or not NR_PASSWORD:
        return
    try:
        creds = base64.b64encode(f'{NR_USERNAME}:{NR_PASSWORD}'.encode()).decode()
        req = urllib.request.Request(_CORPUS_URL, headers={
            'Authorization': 'Basic ' + creds, 'User-Agent': 'twyford-dashboard/1.0'})

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None

        opener = urllib.request.build_opener(_NoRedirect())
        raw = None
        try:
            with opener.open(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                with urllib.request.urlopen(e.headers.get('Location'), timeout=90) as r2:
                    raw = r2.read()
            else:
                raise
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        data = json.loads(raw)
        table = {}
        for r in data.get('TIPLOCDATA', []):
            tip = (r.get('TIPLOC') or '').strip()
            name = (r.get('NLCDESC') or '').strip()
            if tip and name:
                table[tip] = name
        with _corpus_lock:
            _corpus_map = table
        print(f'CORPUS: loaded {len(table)} TIPLOC names')
    except Exception as e:
        print(f'CORPUS load failed: {e}')


# Words to keep upper-case / fix when prettifying CORPUS names
_TIPLOC_FIXES = {
    'T.c.': 'T.C.', 'Tc': 'T.C.', 'C.s.': 'C.S.', 'T&rsmd': 'T&RSMD',
    'Lip': 'LIP', 'Fd': 'FD', 'Arc': 'ARC', 'Fhh': 'FHH', 'Gbrf': 'GBRf',
    'Db': 'DB', 'Ews': 'EWS', 'Emr': 'EMR', 'Drs': 'DRS', 'Lul': 'LUL',
    'Hs': 'HS', 'Ce': 'CE', 'Sdgs': 'Sidings', 'Sdg': 'Siding',
    'Rects': 'Receptions', 'Recp': 'Reception', 'Jn': 'Jn', 'Jcn': 'Jn',
}


def _tiploc_name(tip):
    """Readable location name for a TIPLOC: CORPUS description, title-cased,
    with freight-operator suffix noise stripped ('(Fhh)', 'F Liner H Hau'…)."""
    if not tip:
        return ''
    with _corpus_lock:
        name = _corpus_map.get(tip, '')
    if not name:
        return tip.title()
    words = []
    for w in name.title().split():
        words.append(_TIPLOC_FIXES.get(w, w))
    name = ' '.join(words)
    name = re.sub(r'\s*\((?:fhh|flhh|fl|fh|gbrf|gbf|ews|dbs|dbc|arc|colas)\)$',
                  '', name, flags=re.I)
    name = re.sub(r'\s+(?:f liner h haul?|flhh|fhh|gbrf|gbf|fh|ews|dbs|dbc)$',
                  '', name, flags=re.I)
    name = name.replace('(Ml)', '').replace('  ', ' ').strip()
    if name.endswith(' London'):          # CORPUS: 'Paddington London'
        name = 'London ' + name[:-7]
    return name


def _cif_ident(hc):
    """Origin/destination identity for a freight headcode from today's CIF
    schedule: {'origin','dest','orig_dep','dest_arr'} (ISO times) or None."""
    with _cif_lock:
        entries = _cif_index.get(hc)
    best = _cif_best(entries) if entries else None
    if not best:
        return None

    def _iso(hhmm):
        ts = _hhmm_to_ts(hhmm) if hhmm else None
        if ts is None:
            return ''
        return datetime.datetime.fromtimestamp(ts, tz=_TZ_LONDON).isoformat()

    return {
        'origin':   _tiploc_name(best.get('orig_tip')),
        'dest':     _tiploc_name(best.get('dest_tip')),
        'orig_dep': _iso(best.get('orig_hhmm')),
        'dest_arr': _iso(best.get('dest_hhmm')),
    }


def _load_cif():
    """Download and parse the NR daily CIF freight schedule; populate _cif_index."""
    global _cif_index, _cif_ts
    nr_user = NR_USERNAME
    nr_pass = NR_PASSWORD
    if not nr_user or not nr_pass:
        print('CIF: NR credentials not available, skipping')
        return
    try:
        # Step 1: hit the auth endpoint (returns 302 to S3 pre-signed URL)
        creds = base64.b64encode(f'{nr_user}:{nr_pass}'.encode()).decode()
        req1 = urllib.request.Request(_CIF_URL)
        req1.add_header('Authorization', f'Basic {creds}')
        req1.add_header('User-Agent', 'twyford-dashboard/1.0')

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None

        opener1 = urllib.request.build_opener(_NoRedirect())
        data_url = None
        try:
            with opener1.open(req1, timeout=30) as resp:
                data_url = resp.url   # no redirect — rare
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                data_url = e.headers.get('Location')
            else:
                raise

        # Step 2: download from the S3 pre-signed URL WITHOUT auth header
        print('CIF: downloading freight schedule…')
        if data_url and data_url != _CIF_URL:
            req2 = urllib.request.Request(data_url)
            req2.add_header('User-Agent', 'twyford-dashboard/1.0')
            with urllib.request.urlopen(req2, timeout=180) as resp2:
                raw = resp2.read()

        if raw is None:
            raise RuntimeError('no data received')
        data = gzip.decompress(raw)
        print(f'CIF: {len(raw) // 1024} KB compressed → {len(data) // 1024} KB uncompressed')
    except Exception as exc:
        body = ''
        if hasattr(exc, 'read'):
            try: body = exc.read(200).decode('utf-8', errors='replace')
            except Exception: pass
        print(f'CIF: download failed: {exc} {body}')
        return

    today = datetime.date.today()
    dow   = today.weekday()   # 0=Mon … 6=Sun
    new_index: dict = {}

    for line in data.split(b'\n'):
        if not line or b'TWYFORD' not in line:   # fast pre-filter
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        v1 = rec.get('JsonScheduleV1')
        if not v1:
            continue
        try:
            start = datetime.date.fromisoformat(v1['schedule_start_date'])
            end   = datetime.date.fromisoformat(v1['schedule_end_date'])
        except Exception:
            continue
        if not (start <= today <= end):
            continue
        days = v1.get('schedule_days_runs', '1111111')
        if len(days) > dow and days[dow] != '1':
            continue

        seg = v1.get('schedule_segment', {})
        hc  = (seg.get('signalling_id') or '').strip()
        if not hc or hc.startswith('2H'):
            continue

        uid = v1.get('CIF_train_uid', '')
        stp = v1.get('CIF_stp_indicator', 'P')

        locs    = seg.get('schedule_location', [])
        twy_t   = None
        twy_idx = None
        for i, loc in enumerate(locs):
            if loc.get('tiploc_code', '').startswith('TWYFORD'):
                raw_t = (loc.get('pass') or loc.get('arrival')
                         or loc.get('departure') or '')
                if len(raw_t) >= 4:
                    twy_t   = raw_t[:4]
                    twy_idx = i
                break
        if twy_t is None:
            continue

        direction = _cif_direction(locs, twy_idx)
        o_loc = locs[0] if locs else {}
        d_loc = locs[-1] if locs else {}
        new_index.setdefault(hc, []).append({
            'uid': uid, 'hc': hc, 'twy_hhmm': twy_t,
            'direction': direction, 'stp': stp,
            'orig_tip': (o_loc.get('tiploc_code') or '').strip(),
            'dest_tip': (d_loc.get('tiploc_code') or '').strip(),
            'orig_hhmm': ((o_loc.get('departure') or o_loc.get('pass') or '')[:4]),
            'dest_hhmm': ((d_loc.get('arrival') or d_loc.get('pass') or '')[:4]),
        })

    with _cif_lock:
        _cif_index = new_index
        _cif_ts    = time.time()

    count = sum(1 for v in new_index.values() if _cif_best(v))
    print(f'CIF: {count} freight trains passing Twyford today indexed')


def _cif_refresh_loop():
    """Load SMART + CORPUS + CIF at startup, then refresh daily at 02:30."""
    _load_smart()
    _load_corpus()
    _load_cif()
    while True:
        now = datetime.datetime.now()
        nxt = (datetime.datetime(now.year, now.month, now.day, 2, 30)
               + datetime.timedelta(days=1))
        time.sleep((nxt - now).total_seconds())
        _load_smart()
        _load_corpus()
        _load_cif()


class _NRListener:
    def __init__(self):
        self._conn_ref = None

    def set_conn(self, conn):
        self._conn_ref = conn

    def on_heartbeat_timeout(self):
        print('NR STOMP heartbeat timeout; will reconnect')
        threading.Thread(target=_nr_stomp_reconnect, daemon=True).start()

    def _handle_td(self, msgs):
        """Process Train Describer (TD) messages: CA berth steps and SF signal flags."""
        global _td_buffer, _rtt_trains_ts
        new_ca = []
        sf_updates = {}   # (area, addr) → (data, ts) — deduplicated to last value
        for item in msgs:
            for msg_key, body in item.items():
                if not isinstance(body, dict):
                    continue
                area = body.get('area_id', '')
                if area not in _TD_AREAS:
                    continue
                mtype = msg_key[:2]
                try:
                    ts = int(body.get('time', 0)) // 1000  # TD time is Unix ms → convert to s
                except (TypeError, ValueError):
                    ts = 0
                if mtype == 'CA':
                    descr = body.get('descr', '').strip()
                    if not descr or descr == '    ' or descr == '0000':
                        continue
                    new_ca.append({
                        'area': area,
                        'from': body.get('from', ''),
                        'to':   body.get('to', ''),
                        'descr': descr,
                        'ts':   ts,
                    })
                elif mtype == 'SF':
                    addr = body.get('address', '')
                    data = body.get('data', '')
                    if addr:
                        sf_updates[(area, addr)] = (data, ts)
        if new_ca:
            with _td_lock:
                _td_buffer.extend(new_ca)
                if len(_td_buffer) > _TD_BUF_MAX:
                    _td_buffer = _td_buffer[-_TD_BUF_MAX:]
            for evt in new_ca:
                _ca_observe(evt['area'], evt['from'], evt['to'], evt['descr'], evt['ts'])
        if sf_updates:
            with _sf_lock:
                _sf_state.update(sf_updates)
        for evt in new_ca:
            res = _detect_house_event(evt['area'], evt['from'], evt['to'])
            if not res:
                continue
            event_type, track = res
            hc = evt['descr']
            with _td_house_lock:
                _td_house_events[hc] = {'ts': evt['ts'], 'track': track, 'event': event_type}
            if event_type == 'at_house':
                with _lock:
                    _rtt_trains_ts = 0   # force immediate RTT refresh

    def on_message(self, frame):
        global _rtt_trains_ts
        try:
            msgs = json.loads(frame.body)
            if not isinstance(msgs, list):
                msgs = [msgs]
            # Distinguish TD messages (have CA_MSG/CB_MSG keys) from MVT (have 'header')
            if msgs and any(k.endswith('_MSG') for k in msgs[0]):
                self._handle_td(msgs)
                return
            for msg in msgs:
                body = msg.get('body', {})
                msg_type = msg.get('header', {}).get('msg_type', '')
                stanox = body.get('loc_stanox', '')
                # Twyford station (stopping trains). Invalidate RTT cache immediately
                # so the next /api/trains request fetches fresh data with twy_actual,
                # without waiting out the remaining 30s TTL.
                if stanox == '87014' and msg_type == '0003':
                    with _lock:
                        _rtt_trains_ts = 0
                    continue
                if msg_type != '0003' or stanox not in _NR_STANOX_WATCH:
                    continue
                hc = body.get('train_id', '')
                # TRUST train_id is 10 chars: 2-char schedule prefix + 4-char headcode + 4-char suffix.
                # e.g. "731G21MR25" → prefix "73", headcode "1G21", suffix "MR25".
                reporting_hc = hc[2:6] if len(hc) >= 6 else hc
                info = _NR_STANOX_WATCH[stanox]
                if info.get('freight_only', True) and not _nr_freight_hc(reporting_hc):
                    continue
                direction = body.get('direction_ind', '').upper()
                platform  = body.get('platform', '').strip()
                info = _NR_STANOX_WATCH[stanox]
                is_main = platform in info['main_plats']
                if direction == 'UP':
                    offset_min = info['up_fast'] if is_main else info['up_slow']
                elif direction == 'DOWN':
                    offset_min = info['down_fast'] if is_main else info['down_slow']
                else:
                    continue
                ts_ms = body.get('actual_timestamp')
                if not ts_ms:
                    continue
                try:
                    ts_ms = int(ts_ms)
                except (TypeError, ValueError):
                    continue
                twy_ms = ts_ms + offset_min * 60000
                # TRUST actual_timestamp is London LOCAL wall-clock time encoded
                # as epoch-ms-as-if-UTC (the well-known NROD quirk).  During BST
                # treating it as UTC put every freight pass an hour in the future.
                twy_naive = datetime.datetime.fromtimestamp(
                    twy_ms / 1000.0, tz=datetime.timezone.utc).replace(tzinfo=None)
                twy_dt  = twy_naive.replace(tzinfo=_TZ_LONDON)
                twy_iso = twy_dt.isoformat()
                try:
                    variation = int(body.get('timetable_variation') or 0)
                except (TypeError, ValueError):
                    variation = 0
                entry = {
                    'uid':        'nr:' + hc,
                    'headcode':   reporting_hc,
                    'op_code':    '',
                    'op_name':    '',
                    'passenger':  False,
                    'call_type':  'PASS',
                    'direction':  'up' if direction == 'UP' else 'down',
                    'track':      'Main' if is_main else 'Relief',
                    'origin':     '',
                    'dest':       '',
                    'orig_dep':   '',
                    'dest_arr':   '',
                    'twy_sched':  twy_iso,
                    'twy_actual': twy_iso,
                    'late_min':   variation,
                    'cancelled':  False,
                    'status':     body.get('variation_status'),
                    'platform':   '',
                    'confirmed':  True,
                    'num_veh':    None,
                }
                with _nr_lock:
                    _nr_buffer['nr:' + hc] = entry
        except Exception as e:
            print(f'NR STOMP on_message: {e}')

    def on_disconnected(self):
        print('NR STOMP disconnected; will reconnect')
        threading.Thread(target=_nr_stomp_reconnect, daemon=True).start()

    def on_error(self, frame):
        hdrs = getattr(frame, 'headers', {})
        print(f'NR STOMP error: {frame.body!r}  headers={hdrs}')


def _nr_stomp_connect():
    global _nr_conn
    if not _HAS_STOMP:
        print('NR STOMP: stomp.py not installed, freight disabled')
        return False
    if not NR_USERNAME or not NR_PASSWORD:
        print('NR STOMP: credentials not set, freight disabled')
        return False
    try:
        listener = _NRListener()
        conn = _stomp_module.Connection(
            host_and_ports=[('publicdatafeeds.networkrail.co.uk', 61618)],
            heartbeats=(10000, 10000),
        )
        listener.set_conn(conn)
        conn.set_listener('', listener)
        conn.connect(
            username=NR_USERNAME,
            passcode=NR_PASSWORD,
            wait=True,
            headers={'client-id': NR_USERNAME},
        )
        conn.subscribe(
            destination='/topic/TRAIN_MVT_ALL_TOC',
            id='1',
            ack='auto',
            headers={'activemq.subscriptionName': NR_USERNAME + '-mvt'},
        )
        try:
            conn.subscribe(
                destination='/topic/TD_ALL_SIG_AREA',
                id='2',
                ack='auto',
                headers={'activemq.subscriptionName': NR_USERNAME + '-td'},
            )
            print('NR STOMP also subscribed to TD_ALL_SIG_AREA')
        except Exception as e:
            print(f'NR STOMP TD subscription failed (non-fatal): {e}')
        _nr_conn = conn
        print('NR STOMP connected and subscribed to TRAIN_MVT_ALL_TOC + TD_ALL_SIG_AREA')
        return True
    except Exception as e:
        print(f'NR STOMP connect failed: {e}')
        return False


def _nr_stomp_reconnect():
    delay = 10
    while True:
        time.sleep(delay)
        if not _nr_running:          # stopped intentionally while we slept — do not reconnect
            return
        print(f'NR STOMP reconnecting (delay was {delay}s)…')
        if _nr_stomp_connect():
            return
        delay = min(delay * 2, 300)


def _nr_stomp_stop():
    """Cleanly disconnect STOMP. Sets _nr_running=False BEFORE disconnect so
    on_disconnected → _nr_stomp_reconnect exits immediately without retrying."""
    global _nr_running, _nr_conn
    if not _nr_running:
        return
    _nr_running = False          # must precede disconnect() to suppress reconnect
    conn, _nr_conn = _nr_conn, None
    if conn:
        try:
            conn.disconnect()
        except Exception:
            pass
    print('NR STOMP disconnected (idle)')


def _nr_stomp_start():
    global _nr_running
    _nr_running = True
    threading.Thread(target=_nr_stomp_connect, daemon=True).start()


def _nr_touch():
    """Record that the trains page is active; start STOMP if not already running."""
    global _nr_page_active_ts
    _nr_page_active_ts = time.time()
    if not _nr_running and _HAS_STOMP:
        with _nr_start_lock:
            if not _nr_running:      # double-check inside lock to prevent race
                _nr_stomp_start()


def _nr_idle_watcher():
    """Background thread: disconnect STOMP when no /api/trains poll for _NR_IDLE_TIMEOUT s."""
    while True:
        time.sleep(30)
        if _nr_running and time.time() - _nr_page_active_ts > _NR_IDLE_TIMEOUT:
            print('NR STOMP: no trains page activity — disconnecting')
            _nr_stomp_stop()


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
        elif parsed.path == '/api/aircraft-info-test':
            self._aircraft_info_test(qs)
        elif parsed.path == '/api/airport-name':
            self._airport_name(qs)
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
        elif parsed.path == '/aircraft':
            self._static('/aircraft.html')
        elif parsed.path == '/trains':
            self._static('/trains.html')
        elif parsed.path == '/lineside':
            self._static('/lineside.html')
        elif parsed.path == '/api/trains':
            self._trains(qs)
        elif parsed.path == '/api/nrcc':
            self._nrcc(qs)
        elif parsed.path == '/api/td-log':
            self._td_log(qs)
        elif parsed.path == '/api/td-live':
            self._td_live(qs)
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
        focus = qs.get('focus', ['0'])[0] == '1'
        ttl   = 20 if focus else FLIGHT_TTL
        key = ('flights', lat, lon, dist)
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < ttl:
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
                with open(cache_path) as f:
                    self._json(json.load(f))
                return
            except Exception:
                pass
        result = {}
        # Try UK CAA G-INFO first (free, no key, covers G- registered aircraft)
        try:
            hex_upper = hex_code.upper()
            search_body = json.dumps({'ICAO24BitHex': hex_upper, 'IncludeDeregistered': False}).encode()
            req = urllib.request.Request(
                'https://ginfoapi.caa.co.uk/api/aircraft/search',
                data=search_body,
                headers={
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.caa.co.uk',
                    'Referer': 'https://www.caa.co.uk/',
                    'User-Agent': 'Mozilla/5.0',
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                matches = json.loads(resp.read())
            if matches:
                aircraft_id = matches[0].get('AircraftID')
                if aircraft_id:
                    req2 = urllib.request.Request(
                        f'https://ginfoapi.caa.co.uk/api/aircraft/details/{aircraft_id}',
                        headers={
                            'Origin': 'https://www.caa.co.uk',
                            'Referer': 'https://www.caa.co.uk/',
                            'User-Agent': 'Mozilla/5.0',
                        },
                    )
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        detail = json.loads(resp2.read())
                    year = detail.get('AircraftDetails', {}).get('YearBuild')
                    if year:
                        result['built'] = str(year)
                        result['built_label'] = 'Built'
        except Exception:
            pass
        # Fall back to SkyLink API if CAA had no result and key is configured
        if not result and SKYLINK_API_KEY:
            try:
                url = 'https://skylink-api.p.rapidapi.com/v3/aircraft/icao24/' + hex_code
                req = urllib.request.Request(url, headers={
                    'X-RapidAPI-Key':  SKYLINK_API_KEY,
                    'X-RapidAPI-Host': 'skylink-api.p.rapidapi.com',
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                year = data.get('aircraft', {}).get('year_built', '')
                if year:
                    result['built'] = str(year)
                    result['built_label'] = 'Built'
            except Exception:
                pass
        if result:
            try:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
            except Exception:
                pass
        self._json(result)

    def _aircraft_info_test(self, qs):
        hex_code = qs.get('hex', [''])[0].strip().lower()
        if not re.match(r'^[0-9a-f]{6}$', hex_code):
            self._respond(400, 'text/plain', b'Bad hex')
            return
        try:
            url = 'https://opensky-network.org/api/metadata/aircraft/icao/' + hex_code
            req = urllib.request.Request(url, headers={'User-Agent': 'Joggler-Dashboard/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
            self._respond(200, 'application/json', raw)
        except Exception as e:
            self._json({'error': str(e)})

    def _airport_name(self, qs):
        iata = qs.get('iata', [''])[0].strip().upper()
        if not re.match(r'^[A-Z]{3,4}$', iata):
            self._json({'name': None})
            return
        self._json({'name': _airport_names.get(iata)})

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
            self._json({'image': '', 'album': '', 'bio': '', 'listeners': '', 'similar': []})
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

    def _trains(self, qs):
        _nr_touch()
        try:
            data = _rtt_build_trains()
            self._json(data)
        except Exception as e:
            self._respond(502, 'text/plain', str(e).encode())

    def _nrcc(self, qs):
        """Return NRCC disruption messages for Twyford from Darwin, cached 5 min."""
        key = ('nrcc', 'TWY')
        now = time.time()
        with _lock:
            if key in _cache and now - _cache[key][0] < 300:
                self._json(_cache[key][1])
                return
        try:
            root = _soap('TWY', 1)
            data = _parse(root, None, 0)   # limit=0: no services, just board metadata
            result = {
                'messages': data.get('nrccMessages', []) if data else [],
                'ts': int(now),
            }
        except Exception:
            result = {'messages': [], 'ts': int(now)}
        with _lock:
            _cache[key] = (now, result)
        self._json(result)

    def _td_log(self, qs):
        limit = min(int(qs.get('n', ['100'])[0]), _TD_BUF_MAX)
        with _td_lock:
            entries = list(_td_buffer[-limit:])
        self._json({'count': len(entries), 'entries': entries})

    def _td_live(self, qs):
        """Current train positions (one per headcode, most recent berth) + signal states."""
        now_ts = time.time()
        positions = {}   # headcode → entry (most recent only)
        with _td_lock:
            for entry in reversed(_td_buffer):
                hc = entry['descr']
                if hc not in positions and now_ts - entry['ts'] < 600:
                    info = _berth_info(entry['area'], entry['to'])
                    positions[hc] = {
                        'headcode':   hc,
                        'area':       entry['area'],
                        'berth':      entry['to'],
                        'from_berth': entry['from'],
                        'ts':         entry['ts'],
                        'age_s':      int(now_ts - entry['ts']),
                        # SMART-derived line + position (None when berth unknown)
                        'line':       info['line']    if info else '',
                        'place':      info['stanme']  if info else '',
                        'dist_mi':    info['dist_mi'] if info else None,
                    }
        with _sf_lock:
            signals = {f"{a}:{addr}": {'data': d, 'ts': t}
                       for (a, addr), (d, t) in _sf_state.items()}
        self._json({'positions': list(positions.values()),
                    'signals': signals, 'ts': int(now_ts)})

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
    threading.Thread(target=_nr_idle_watcher, daemon=True).start()
    threading.Thread(target=_cif_refresh_loop, daemon=True).start()
    threading.Thread(target=_chain_refresh_loop, daemon=True).start()
    ThreadedServer(('0.0.0.0', 5001), Handler).serve_forever()
