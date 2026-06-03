#!/bin/bash
export DISPLAY=:0

# Kill any stale Chromium from a previous session
pkill -f chromium 2>/dev/null
sleep 1

# Wait for network (up to 60 s)
for i in $(seq 1 30); do
  ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && break
  sleep 2
done

sleep 5

mkdir -p /tmp/chromium-cache

while true; do
  # Clear stale singleton locks left by a crash
  rm -f ~/.config/chromium/SingletonLock \
        ~/.config/chromium/SingletonCookie \
        ~/.config/chromium/SingletonSocket

  # Mark previous session as clean so Chromium doesn't show "Restore pages?" dialog
  python3 -c "
import json, os
p = os.path.expanduser('~/.config/chromium/Default/Preferences')
try:
  d = json.load(open(p))
  d.setdefault('profile', {}).update({'exit_type': 'Normal', 'exited_cleanly': True})
  json.dump(d, open(p, 'w'))
except: pass
" 2>/dev/null

  chromium \
    --start-fullscreen \
    --test-type \
    --no-first-run \
    --disable-infobars \
    --no-sandbox \
    --disable-gpu \
    --disable-extensions \
    --disable-sync \
    --disable-background-networking \
    --disable-default-apps \
    --single-process \
    --disable-dev-shm-usage \
    --disk-cache-dir=/tmp/chromium-cache \
    --disk-cache-size=52428800 \
    --js-flags="--max-old-space-size=80" \
    --window-position=0,0 \
    --window-size=800,480 \
    http://172.16.10.136:5001/

  echo "$(date): Chromium exited (code $?), restarting in 5s" >> /tmp/kiosk-watchdog.log
  sleep 5
done
