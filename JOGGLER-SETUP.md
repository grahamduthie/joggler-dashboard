# Twyford Dashboard — Joggler Setup Guide

How to go from a bare O2 Joggler to a fully running thin-client kiosk.

The Joggler is the **display device only**. All API proxying and data fetching happen on
the Raspberry Pi. Set up the Pi backend first (see [PI-SETUP.md](PI-SETUP.md)), then set
up the Joggler to point at it.

---

## Hardware

- **Model**: O2 Joggler / OpenPeak OpenFrame 1
- **CPU**: Intel Atom Z520 (i686, 32-bit, single core, 1.33 GHz)
- **RAM**: 492 MB — Chromium needs `--disable-gpu --single-process` to avoid OOM
- **Display**: 7" 800×480 resistive touchscreen (no BTN_TOUCH, requires touch-bridge.py)
- **USB**: One port — used for the boot drive. Nothing else can be plugged in.
- **Network**: WiFi (Realtek rtl8192su)

The internal eMMC (1 GB) is too small and unreliable — always boot from USB.

---

## Step 1 — Flash openframe-linux to USB

openframe-linux is the only OS that works reliably on this hardware (custom kernel 6.18.31
with EMGD graphics, Realtek WiFi driver, etc.). Standard Raspberry Pi OS or Ubuntu will not work.

**Get the image:**

```
Image: of-ext2-1028-46-trixie-v6.18.31.img.gz
GitHub: https://github.com/birdslikewires/openframe-linux
```

**Flash (on Mac):**

```bash
diskutil unmountDisk /dev/diskN          # N = your USB drive number
gunzip -c of-ext2-1028-46-trixie-v6.18.31.img.gz | sudo dd of=/dev/diskN bs=4m status=progress
```

Use a USB 2.0 drive of 8 GB or larger (PNY 16 GB recommended — ~24 MB/s raw read).

**First boot:**

1. Plug USB into the Joggler, press power.
2. SSH in (default user: `of`, password: see openframe-linux docs).
3. Expand the root partition: `sudo of-expand`
4. Reboot.

---

## Step 2 — WiFi

Edit `/boot/network.yaml` (the EFI partition copy — this is what persists across reboots):

```bash
sudo nano /boot/network.yaml
```

Example:

```yaml
network:
  version: 2
  wifis:
    wlan0:
      dhcp4: true
      access-points:
        "YourSSID":
          password: "YourPassword"
```

The `of-netplan` service copies `/boot/network.yaml` to `/etc/netplan/` on every boot.
Do **not** edit `/etc/netplan/` directly — it will be overwritten on the next boot.

Apply immediately: `sudo netplan apply`

---

## Step 3 — Install packages

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    xserver-xorg xinit openbox xdotool fonts-opensymbol unclutter
sudo apt-get clean
sudo apt-get install -y --no-install-recommends chromium
```

Chromium is installed separately because it pulls in many deps — doing it in one pass can
exhaust memory on the Atom. Clean the apt cache first.

Set Xorg setuid (`xserver-xorg-legacy` is not available in Debian Trixie):

```bash
sudo chmod u+s /usr/lib/xorg/Xorg
sudo usermod -aG video,input,tty of
```

---

## Step 4 — Copy project files to the Joggler

From your Mac:

```bash
JOGGLER=of@172.16.10.168
PROJECT=~/Programming/Joggler

scp -i ~/.ssh/id_ed25519 \
    $PROJECT/shutdown-server.py \
    $PROJECT/touch-bridge.py \
    $JOGGLER:/home/of/
```

Only `shutdown-server.py` and `touch-bridge.py` run on the Joggler. The transport proxy,
cast server, and all API Python files run on the Pi (see PI-SETUP.md).

---

## Step 5 — Run setup-kiosk.sh

```bash
chmod +x /home/of/setup-kiosk.sh
/home/of/setup-kiosk.sh
```

Or run it directly from the Mac:

```bash
scp setup-kiosk.sh of@172.16.10.168:/home/of/ && \
  ssh of@172.16.10.168 'bash /home/of/setup-kiosk.sh'
```

This script configures:
- xorg.conf (fbdev driver + touchscreen ignore rule)
- Autologin on tty1
- `.bash_profile` (starts X on tty1)
- `.xinitrc` (launches openbox-session)
- Openbox autostart (starts touch-bridge.py, shutdown-server.py, and kiosk.sh)
- `kiosk.sh` (Chromium launcher pointing at the Pi — `http://172.16.10.136:5001/`)
- sudoers rule (passwordless poweroff for the power button)
- udev rule (touchscreen as mouse device)
- sshd OOM protection

After running, **reboot** to apply autologin and udev rules.

---

## Step 6 — System timezone

```bash
sudo timedatectl set-timezone Europe/London
```

---

## Step 7 — Verify

After reboot the Joggler should:
1. Auto-login as `of` on tty1
2. Start X → Openbox → launch touch-bridge.py and shutdown-server.py → launch Chromium
3. Load `http://172.16.10.136:5001/` (the Pi backend must be running — see PI-SETUP.md)

**Check servers are running on Joggler:**

```bash
ssh -i ~/.ssh/id_ed25519 of@172.16.10.168 'pgrep -af python3'
```

Should show: `touch-bridge.py` and `shutdown-server.py`

**Check Chromium:**

```bash
ssh -i ~/.ssh/id_ed25519 of@172.16.10.168 'pgrep -c chromium'
```

**Take a screenshot:**

```bash
ssh -i ~/.ssh/id_ed25519 of@172.16.10.168 \
    'DISPLAY=:0 scrot /tmp/screenshot.png && base64 /tmp/screenshot.png' \
    | base64 -d > /tmp/joggler_screen.png
open /tmp/joggler_screen.png
```

---

## Ongoing deployment (from Mac)

**Deploy dashboard.html and force a hard reload:**

```bash
scp dashboard.html gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/ && \
  ssh -i ~/.ssh/id_ed25519 of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'
```

`dashboard.html` goes to the **Pi** (where it's served from). The Joggler is told to
hard-reload with `ctrl+shift+r` (not F5 — F5 may serve stale cached CSS).

---

## Persistent data files on the Joggler

There are none — the Joggler is stateless. All cached data (bus stops, aircraft info,
airline logos, Hive tokens) lives on the Pi at `/home/gduthie/twyford-dashboard/`.

---

## Known gotchas

- **OOM**: Chromium must use `--disable-gpu --single-process --js-flags="--max-old-space-size=80"`.
  Without these flags it will OOM-kill on the 492 MB Atom.
- **Touchscreen**: The device has `ABS_X/Y + BTN_LEFT` but no `BTN_TOUCH`. libinput can't
  handle it. xorg.conf ignores the device entirely; `touch-bridge.py` reads `/dev/input/event1`
  directly and injects events via XTest (libXtst).
- **DefaultDepth 24**: The GMA 500 framebuffer rejects 16 bpp. xorg.conf must have `DefaultDepth 24`.
- **WiFi config**: Always edit `/boot/network.yaml`, not `/etc/netplan/` (overwritten on boot).
- **Thermal**: CPU runs at 65°C at idle. Thermal shutdown trips at 100°C. Ensure adequate
  ventilation — the Joggler has no fan.
- **SVG height**: Always set explicit `px` height on SVG elements — `height: auto` is
  unreliable on Chromium Linux.
- **CSS filters**: `filter: drop-shadow/brightness/blur` falls back to CPU on `--disable-gpu`.
  Do not apply filters to images — kills performance on the Atom Z520.
- **`python3-pip` not in apt**: If you need pip on the Joggler for any reason, install via
  get-pip.py: `curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && python3 /tmp/get-pip.py --user`
