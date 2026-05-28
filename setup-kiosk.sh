#!/bin/bash
# Run on Joggler (172.16.10.168) after X/Openbox/Chromium are installed.
# Pre-requisites already installed:
#   sudo apt-get install -y --no-install-recommends xserver-xorg xinit openbox xdotool fonts-opensymbol unclutter
#   sudo apt-get clean
#   sudo apt-get install -y --no-install-recommends chromium
#   sudo chmod u+s /usr/lib/xorg/Xorg          # no xserver-xorg-legacy in Trixie
#   sudo usermod -aG video,input,tty of
#
# python3-pip is NOT in apt on Debian Trixie — install pip via get-pip.py:
#   curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
#   python3 /tmp/get-pip.py --user

set -e
echo "=== Setting up kiosk stack ==="

# ── Xorg setuid (required — xserver-xorg-legacy absent in Debian Trixie) ──
sudo chmod u+s /usr/lib/xorg/Xorg
sudo usermod -aG video,input,tty of

# ── Protect sshd from OOM killer ──
sudo mkdir -p /etc/systemd/system/ssh.service.d
sudo tee /etc/systemd/system/ssh.service.d/oom.conf > /dev/null << 'EOF'
[Service]
OOMScoreAdjust=-1000
EOF
sudo systemctl daemon-reload
sudo systemctl restart ssh

# ── xorg.conf: fbdev + evdev touchscreen ──
# NOTE: DefaultDepth MUST be 24 — the GMA 500 framebuffer (gma500drmfb) rejects 16bpp
sudo tee /etc/X11/xorg.conf > /dev/null << 'EOF'
Section "Device"
  Identifier "Card0"
  Driver "fbdev"
  Option "fbdev" "/dev/fb0"
EndSection

Section "Screen"
  Identifier "Screen0"
  Device "Card0"
  DefaultDepth 24
EndSection

Section "InputClass"
  Identifier "Ignore Joggler Touchscreen"
  MatchProduct "AmSC OpenPeak Touchscreen Hyup02"
  Option "Ignore" "true"
EndSection

Section "ServerLayout"
  Identifier "Layout0"
  Screen "Screen0"
EndSection
EOF

# ── Autologin as 'of' on tty1 ──
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf > /dev/null << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin of --noclear %I $TERM
EOF
sudo systemctl daemon-reload

# ── .bash_profile: start X on tty1 ──
# NOTE: do NOT check [ -z "$DISPLAY" ] — libpam-systemd sets DISPLAY=:0.0 before X starts
cat > /home/of/.bash_profile << 'EOF'
if [ "$(tty)" = "/dev/tty1" ]; then
  exec startx >> /home/of/.xsession-errors 2>&1
fi
EOF

# ── .xinitrc ──
cat > /home/of/.xinitrc << 'EOF'
exec openbox-session
EOF
chmod +x /home/of/.xinitrc

# ── Openbox autostart ──
# The Joggler is a thin client — transport-proxy.py and cast-server.py run on the Pi.
# Only touch-bridge.py (touchscreen) and shutdown-server.py (power button) run here.
mkdir -p /home/of/.config/openbox
# Start order: touch-bridge first (connects to X), then shutdown server, then kiosk
cat > /home/of/.config/openbox/autostart << 'EOF'
python3 /home/of/touch-bridge.py &
python3 /home/of/shutdown-server.py &
unclutter -idle 0.1 -root &
/home/of/kiosk.sh &
EOF
chmod +x /home/of/.config/openbox/autostart

# ── kiosk.sh ──
# NOTE: xset/x11-xserver-utils not available in Debian Trixie — omitted
# NOTE: exec DISPLAY=:0 chromium is wrong syntax; use export DISPLAY=:0 then exec chromium
cat > /home/of/kiosk.sh << 'EOF'
#!/bin/bash
export DISPLAY=:0
pkill -f chromium 2>/dev/null

# Wait for network (up to 60s)
for i in $(seq 1 30); do
  ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && break
  sleep 2
done

# Let memory settle before Chromium loads
sleep 5

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
  --js-flags="--max-old-space-size=80" \
  --window-position=0,0 \
  --window-size=800,480 \
  http://172.16.10.136:5001/
EOF
chmod +x /home/of/kiosk.sh

# ── sudoers: allow 'of' to power off without a password ──
sudo tee /etc/sudoers.d/of-poweroff > /dev/null << 'EOF'
of ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff
EOF

# ── Fix ownership ──
chown of:of /home/of/.bash_profile /home/of/.xinitrc /home/of/kiosk.sh /home/of/dashboard.html 2>/dev/null || true
chown -R of:of /home/of/.config/openbox

# ── Touchscreen: keep as MOUSE in udev, but X ignores it entirely ──
# The device has ABS_X/Y + BTN_LEFT but NO BTN_TOUCH — libinput TOUCHSCREEN mode
# can't handle it (no touch begin/end events). Instead X ignores the device (see
# xorg.conf above) and touch-bridge.py reads /dev/input/event1 directly, scales
# coordinates to 800x480, and injects mouse events via XTest (libXtst).
sudo tee /etc/udev/rules.d/99-joggler-touch.rules > /dev/null << 'EOF'
SUBSYSTEM=="input", ATTRS{idVendor}=="04b4", ATTRS{idProduct}=="1974", ENV{ID_INPUT_MOUSE}="1", ENV{ID_INPUT_TOUCHSCREEN}="0"
EOF

# ── touch-bridge.py ──
# Reads raw input events (ABS_X/Y 0-32639, BTN_LEFT) and injects X11 pointer
# events via libXtst so Chromium responds to taps as direct clicks.
cp /path/to/touch-bridge.py /home/of/touch-bridge.py
chmod +x /home/of/touch-bridge.py

# ── WiFi persistence: update boot partition config ──
# of-netplan service overwrites /etc/netplan/ from /boot/network.yaml each boot.
# After setting WiFi in /etc/netplan/network.yaml, copy it to /boot to make it permanent.
sudo cp /etc/netplan/network.yaml /boot/network.yaml
echo "WiFi config persisted to /boot/network.yaml"

echo "=== Kiosk setup complete. Reboot to test. ==="
