# Twyford Dashboard — Raspberry Pi Setup Guide

How to set up the Raspberry Pi as the dashboard backend from scratch.

The Pi serves `dashboard.html` and all API endpoints. The Joggler (and any other browser
on the LAN) connects to `http://172.16.10.136:5001/`.

---

## Prerequisites

- Raspberry Pi already installed, networked, and reachable at `172.16.10.136`
- Python 3 available: `python3 --version`
- pip available: `pip3 --version` or `python3 -m pip --version`

---

## Step 1 — Create the app directory

```bash
ssh gduthie@172.16.10.136
mkdir -p /home/gduthie/twyford-dashboard/logos
mkdir -p /home/gduthie/twyford-dashboard/aircraft-info
mkdir -p /home/gduthie/twyford-dashboard/icons
```

---

## Step 2 — Install Python dependencies

```bash
pip3 install --user pychromecast requests
```

- `pychromecast` — required by `cast-server.py`
- `requests` — required by `hive-setup.py`
- `transport-proxy.py` uses Python stdlib only — no pip dependencies

---

## Step 3 — Copy project files

From your Mac:

```bash
PI=gduthie@172.16.10.136
PROJECT=~/Programming/Joggler
APP=/home/gduthie/twyford-dashboard

scp -i ~/.ssh/id_ed25519 \
    $PROJECT/dashboard.html \
    $PROJECT/transport-proxy.py \
    $PROJECT/cast-server.py \
    $PROJECT/hive-setup.py \
    $PROJECT/hls.min.js \
    $PI:$APP/

scp -i ~/.ssh/id_ed25519 -r $PROJECT/icons/ $PI:$APP/
```

---

## Step 4 — API credentials

### BODS API key (bus vehicle positions)

Create `/home/gduthie/twyford-dashboard/.env`:

```bash
echo 'BODS_API_KEY=your_key_here' > /home/gduthie/twyford-dashboard/.env
chmod 600 /home/gduthie/twyford-dashboard/.env
```

Get a free API key from the Bus Open Data Service portal (data.bus-data.dft.gov.uk).

### Hive indoor temperatures

Run the interactive auth script **once** on the Pi to save tokens and credentials:

```bash
ssh -t gduthie@172.16.10.136 \
  'python3 /home/gduthie/twyford-dashboard/hive-setup.py --save-credentials'
```

This creates:
- `hive-credentials.json` (mode 600) — username/password for automatic re-auth
- `hive-tokens.json` (mode 600) — Cognito tokens (auto-refreshed by proxy)

If `hive-setup.py` asks for an SMS verification code, enter it. After first login,
subsequent token refreshes skip MFA.

**Account note:** The Hive account must have access to the home that contains the heating
devices. Graham's heating devices are under the "Luke" home — use the account that is a
SUPERUSER there (or use Luke's account directly).

---

## Step 5 — Run transport-proxy.py (test)

```bash
ssh gduthie@172.16.10.136 \
  'python3 /home/gduthie/twyford-dashboard/transport-proxy.py &'
curl http://172.16.10.136:5001/health
```

Should return `ok`. Then open `http://172.16.10.136:5001/` in a browser on the same
network to verify the dashboard loads.

---

## Step 6 — Systemd service (persistent)

Create `/etc/systemd/system/twyford-dashboard.service` on the Pi:

```bash
sudo tee /etc/systemd/system/twyford-dashboard.service > /dev/null << 'EOF'
[Unit]
Description=Twyford Dashboard Backend
After=network.target

[Service]
Type=simple
User=gduthie
WorkingDirectory=/home/gduthie/twyford-dashboard
ExecStart=/usr/bin/python3 /home/gduthie/twyford-dashboard/transport-proxy.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/gduthie/twyford-dashboard/dashboard.log
StandardError=append:/home/gduthie/twyford-dashboard/dashboard.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable twyford-dashboard
sudo systemctl start twyford-dashboard
sudo systemctl status twyford-dashboard
```

**cast-server.py** is typically started manually or via a second service unit. To start it:

```bash
ssh gduthie@172.16.10.136 \
  'nohup python3 /home/gduthie/twyford-dashboard/cast-server.py \
     >> /home/gduthie/twyford-dashboard/dashboard.log 2>&1 & disown; echo started'
```

To make it persistent, create a second systemd unit (`twyford-cast.service`) following
the same pattern.

---

## Step 7 — Verify all endpoints

```bash
# Health check
curl http://172.16.10.136:5001/health

# Train departures
curl "http://172.16.10.136:5001/api/departures?station=TWY&rows=5"

# Bus departures
curl "http://172.16.10.136:5001/api/bods/departures?stop=035091060001"

# Hive temperatures
curl http://172.16.10.136:5001/api/hive

# Bus stops (first call triggers Overpass fetch — may take ~10s)
curl http://172.16.10.136:5001/api/buses/stops | python3 -m json.tool
```

---

## Step 8 — Migrate existing file caches (optional)

If you have an old Joggler with accumulated logos and aircraft info, copy them across to
avoid re-fetching:

```bash
scp -r of@172.16.10.168:/home/of/logos/ \
    gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/
scp -r of@172.16.10.168:/home/of/aircraft-info/ \
    gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/
scp of@172.16.10.168:/home/of/bus-stops.json \
    gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/
scp of@172.16.10.168:/home/of/bus-route-stops.json \
    gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/
```

---

## Optional: nginx + HTTPS for external access

To access the dashboard from outside the LAN over HTTPS:

### Install nginx and Certbot

```bash
sudo apt update
sudo apt install nginx certbot python3-certbot-nginx
```

### nginx site config

Create `/etc/nginx/sites-available/twyford-dashboard`:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Optional: basic auth to prevent public access
    # auth_basic "Twyford Dashboard";
    # auth_basic_user_file /etc/nginx/.htpasswd;

    # SSE needs unbuffered streaming
    location /api/radio/ {
        proxy_pass         http://127.0.0.1:5001;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 86400;
        proxy_set_header   Host $host;
    }

    location / {
        proxy_pass       http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/twyford-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.com
```

### pfSense port forwarding

In pfSense: **Firewall → NAT → Port Forward → Add**

| Field | Value |
|-------|-------|
| Interface | WAN |
| Protocol | TCP |
| Destination port | 443 |
| Redirect target IP | 172.16.10.136 |
| Redirect target port | 443 |

Add a second rule for port 80 → 80 if you want HTTP→HTTPS redirect from outside.
Do **not** forward port 5001 directly.

### Radio over HTTPS

When the dashboard is served over HTTPS, browsers block HTTP audio sources as mixed
content. Bauer/Global streams use HTTP. To fix this, `transport-proxy.py` would need a
streaming proxy endpoint (`/api/radio/stream-proxy`) that fetches the upstream HTTP stream
and pipes it through HTTPS. This is not yet implemented — radio works fine over plain HTTP
on the LAN.

---

## Persistent data files (created at runtime)

| File | How it's created | Can delete? |
|------|-----------------|-------------|
| `bus-stops.json` | Fetched from Overpass on first bus map load | Yes — regenerates |
| `bus-route-stops.json` | Built progressively from Transport API | Yes — rebuilds slowly |
| `logos/*.png` | Cached airline logos from pics.avs.io | Yes — re-fetched on demand |
| `aircraft-info/*.json` | Aircraft year/reg from OpenSky | Yes — re-fetched on demand |
| `hive-tokens.json` | Created by hive-setup.py, auto-refreshed | No — re-run hive-setup.py |
