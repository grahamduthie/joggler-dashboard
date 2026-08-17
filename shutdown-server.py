#!/usr/bin/env python3
# Tiny local HTTP server that triggers a graceful system shutdown.
# Bound to 127.0.0.1 only so it's not reachable from the network.
# Called by dashboard.html's power button via JOGGLER_SHUTDOWN = 'http://localhost:9999/shutdown'.
# Uses localhost so it only works on the Joggler itself; silently fails on other devices.
import http.server
import os
import subprocess

ALLOWED_ORIGINS = frozenset(filter(None, os.environ.get(
    'JOGGLER_ALLOWED_ORIGINS',
    'http://172.16.10.136:5001,https://dashboard.gdx.org.uk'
).split(',')))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        origin = self.headers.get('Origin')
        if origin not in ALLOWED_ORIGINS:
            self.send_error(403, 'Forbidden origin')
            return
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(b'ok')
        subprocess.Popen(['sudo', 'systemctl', 'poweroff'])

    def log_message(self, *args):
        pass


http.server.HTTPServer(('127.0.0.1', 9999), Handler).serve_forever()
