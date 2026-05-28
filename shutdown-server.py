#!/usr/bin/env python3
# Tiny local HTTP server that triggers a graceful system shutdown.
# Bound to 127.0.0.1 only so it's not reachable from the network.
# Called by dashboard.html's power button via JOGGLER_SHUTDOWN = 'http://localhost:9999/shutdown'.
# Uses localhost so it only works on the Joggler itself; silently fails on other devices.
import http.server
import subprocess


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b'ok')
        subprocess.Popen(['sudo', 'systemctl', 'poweroff'])

    def log_message(self, *args):
        pass


http.server.HTTPServer(('127.0.0.1', 9999), Handler).serve_forever()
