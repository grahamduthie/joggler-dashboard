#!/usr/bin/env python3
# Local HTTP server for Chromecast discovery and control.
# Called by dashboard.html on port 9998.
# Chromecasts stream the radio directly — Joggler is not in the audio path.
import http.server
import json
import time
import threading
import urllib.parse

import pychromecast

PORT = 9998

# Cache discovered chromecasts so discover only runs once per session
_chromecasts = {}   # friendly_name -> Chromecast object
_active_cc  = None  # the specific object currently playing
_browser = None
_lock = threading.Lock()


def discover(timeout=5):
    global _browser
    with _lock:
        if _browser:
            _browser.stop_discovery()
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        _browser = browser
        _chromecasts.clear()
        for cc in chromecasts:
            _chromecasts[cc.cast_info.friendly_name] = cc
    return list(_chromecasts.keys())


def _mime_for_url(url):
    u = url.split('?')[0].lower()
    if u.endswith('.m3u8'):
        return 'application/vnd.apple.mpegurl'
    if u.endswith('.aac'):
        return 'audio/aac'
    return 'audio/mpeg'


def cast_play(name, url, title='Radio'):
    global _active_cc
    cc = _chromecasts.get(name)
    if not cc:
        raise KeyError('Device not found: ' + name)
    cc.wait()
    mc = cc.media_controller
    stream_type = 'LIVE' if url.split('?')[0].lower().endswith('.m3u8') else 'UNKNOWN'
    mc.play_media(url, _mime_for_url(url), title=title, stream_type=stream_type)
    mc.block_until_active()
    _active_cc = cc


def cast_stop(name):
    global _active_cc
    cc = _active_cc or _chromecasts.get(name)
    _active_cc = None
    if not cc:
        return
    cc.wait()
    cc.media_controller.stop()
    cc.quit_app()


def cast_volume(name, delta):
    cc = _active_cc or _chromecasts.get(name)
    if not cc:
        raise KeyError('Device not found: ' + name)
    cc.wait()
    current = cc.status.volume_level if cc.status else 0.5
    new_vol = min(1.0, max(0.0, current + delta))
    if delta != 0:
        cc.set_volume(new_vol)
    return new_vol


class Handler(http.server.BaseHTTPRequestHandler):

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/cast/discover':
            try:
                names = discover()
                self.send_json(200, {'devices': names})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
        else:
            self.send_json(404, {'error': 'not found'})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == '/cast/play':
            name  = body.get('device', '')
            url   = body.get('url', '')
            title = body.get('title', 'Radio')
            if not url:
                self.send_json(400, {'error': 'url required'})
                return
            try:
                cast_play(name, url, title)
                self.send_json(200, {'ok': True})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
        elif path == '/cast/stop':
            name = body.get('device', '')
            try:
                cast_stop(name)
                self.send_json(200, {'ok': True})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
        elif path == '/cast/volume':
            name  = body.get('device', '')
            delta = float(body.get('delta', 0))
            try:
                vol = cast_volume(name, delta)
                self.send_json(200, {'volume': vol})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
        else:
            self.send_json(404, {'error': 'not found'})

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
