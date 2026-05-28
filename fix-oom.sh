#!/bin/bash
# Run on Joggler immediately after reboot, before Chromium loads.
# Protects sshd from OOM killer and adds startup delay for Chromium.

set -e

# Protect sshd from OOM killer (-1000 = never kill)
sudo mkdir -p /etc/systemd/system/ssh.service.d
sudo tee /etc/systemd/system/ssh.service.d/oom.conf > /dev/null << 'EOF'
[Service]
OOMScoreAdjust=-1000
EOF
sudo systemctl daemon-reload
sudo systemctl restart ssh
echo "sshd OOM protection set"

# Update kiosk.sh with better memory management
cat > /home/of/kiosk.sh << 'EOF'
#!/bin/bash
export DISPLAY=:0
pkill -f chromium 2>/dev/null

# Wait for network (up to 60s)
for i in $(seq 1 30); do
  ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && break
  sleep 2
done

# Let system memory settle after network comes up
sleep 5

xset s off
xset -dpms
xset s noblank

# Redirect Chromium disk cache to tmpfs (RAM) to avoid USB I/O
mkdir -p /tmp/chromium-cache

exec chromium \
  --kiosk \
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
EOF
chmod +x /home/of/kiosk.sh
echo "kiosk.sh updated with tmpfs disk cache"

echo "Done. Reboot to apply."
