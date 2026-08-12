# Swapping the Pi's failing SD card — clone-and-repair runbook

Written 2026-08-12, after confirming the card returns different data on every physical read
(see PROJECT.md → "The Pi's SD card is failing").

**You do not need to reinstall the OS.** Clone the card, then repair the corrupted files from
the package repositories. All config, users, WiFi, SSH keys, systemd units and `/home` survive.

## Why a plain `dd` clone is not enough on its own

`dd` copies whatever the card hands back. In the damaged region that is garbage — and different
garbage each read. So the clone is only half the job; step 4 is what makes it sound.

Two facts make this approach work:

1. **ext4 `metadata_csum` is enabled and the filesystem is clean.** The *structure* (inode
   tables, directory blocks, bitmaps) is intact and verified by checksum — only file data blocks
   are damaged. A block-level clone therefore yields a structurally valid filesystem.
2. **Nothing irreplaceable sits in a damaged region** — see the map below. Everything affected is
   either package-managed (re-downloadable), pip-installable, or regenerable junk.

## Map of the damage (measured 2026-08-12)

The whole card was read twice with the cache dropped between passes, comparing every 4 MB chunk:

**59 of 3,701 chunks are unstable — 1.6%, about 236 MB.** Mapping those blocks through
`debugfs icheck`/`ncheck` gives 190 affected files:

| Area | Files | What they are | Recoverable? |
|---|---|---|---|
| `/usr` | 99 | `/usr/bin/s*` binaries, `libLLVM`, locale data — **all package-owned** | Yes — `apt reinstall` |
| `/home` | 85 | `Bus-Departure-Board/venv/` shared objects, `luma_*.png` debug screenshots, `dashboard.log`, `signals_learned.json` | Yes — see below |
| `/var` | 6 | apt/man caches, `/var/swap` | Yes — regenerated |
| `/boot/firmware` | 1 chunk | ~48 MB in, where `initramfs8` / `kernel8.img` live | Yes — `apt reinstall` |

**No file in the verified backup is affected**, other than `dashboard.log` (a log) and
`signals_learned.json` — and that one is rewritten every ~10 s, parses as valid JSON, and hashes
stably once you account for it changing mid-read.

Two consequences, both folded into the steps below:

- **The boot partition is affected**, so the kernel/firmware packages must be reinstalled too,
  not just userland. Step 4 covers this.
- **The venv's compiled `.so` files are corrupt** (pygame/SDL objects). Do **not** trust the
  cloned venv — rebuild it from `requirementsPy3.txt`. Step 5a.

---

## Step 0 — Before you start

- New card must be **≥ 15,523,119,104 bytes** (the old card's exact size). **A larger card is
  fine and is the easy case** — the target in use is a 64 GB SanDisk Extreme, comfortably
  bigger, so the clone lands in the first 14.5 GB and you expand into the remaining ~48 GB at
  step 5. Only a *smaller* card would be a problem.
- **Keep the old card.** Do not wipe or reuse it until the new one is verified. It is your
  rollback.
- You also have a checksum-verified backup at `~/Programming/pi-backups/2026-08-12/`.

Notes on the 64 GB SanDisk Extreme specifically:

- 64 GB cards ship formatted exFAT, which the Pi cannot boot from — irrelevant here, because
  `dd` overwrites the partition table wholesale with the cloned FAT32 + ext4 layout.
- The Pi 3's SD interface tops out around 20–23 MB/s, so you will not see the card's rated
  speed. No harm; the extra capacity also means far more spare area for wear levelling than
  the 10-year-old 16 GB card had.
- Writing only takes 14.5 GB, not 64 GB — the clone is quick.

```bash
# Clean shutdown, then pull the card
ssh gduthie@172.16.10.136 'sudo shutdown -h now'
```

## Step 1 — Clone, on the Mac

**These `dd` commands must be run from a real terminal window (Terminal.app / iTerm).** They
need `sudo`, and neither a Claude Code tool call nor the `!` prefix provides a TTY for the
password prompt — both fail with *"a terminal is required to read the password"*. On macOS the
raw disk nodes are `root:operator` mode 640 and a normal admin account is not in `operator`, so
there is no way around the password.

Identify the card by **difference**, not by eye — `dd` to the wrong node destroys that disk:

```bash
diskutil list | grep '^/dev/disk'    # BEFORE inserting the card
# ...insert card...
diskutil list | grep '^/dev/disk'    # the new entry is your card
diskutil info /dev/diskN | grep -iE 'Disk Size|Removable|Protocol'
```

Cross-check before trusting it: the old card is **exactly 15,523,119,104 bytes** and shows an
`FDisk_partition_scheme` with `Windows_FAT_32 bootfs` + `Linux`. It should report
`external, physical`, USB, removable. macOS will pop *"disk not readable"* when you insert it —
that is the ext4 partition it cannot read. **Click Ignore, never Initialize.**

```bash
diskutil unmountDisk /dev/diskOLD
sudo dd if=/dev/rdiskOLD of="$HOME/pi-card-image.img" bs=4m status=progress
```

`/dev/rdiskN` (raw, note the `r`) is several times faster than `/dev/diskN`. Expect roughly
10–20 minutes for 14.5 GB. Recent macOS `dd` does accept `status=progress`; on older versions
press **Ctrl-T** for a progress line instead.

Verify the image is complete before going any further — it must be exactly the card's size:

```bash
stat -f%z "$HOME/pi-card-image.img"     # expect 15523119104
```

No read errors are expected: `badblocks` found zero *unreadable* sectors, since this card's
failure is silent corruption rather than I/O failure. If `dd` does report read errors, re-run
with `conv=noerror,sync` so the image keeps its offsets aligned.

Then write it to the new card:

```bash
diskutil list                       # find the NEW card
diskutil unmountDisk /dev/diskNEW
sudo dd if="$HOME/pi-card-image.img" of=/dev/rdiskNEW bs=4m status=progress
sync
```

Going via an image file (rather than card-to-card) means you only read the failing card once,
it works with a single reader, and you keep the image as a second fallback.

## Step 2 — Boot the Pi on the new card

Swap the card in and power up. It should come up identically — same hostname (`trainpi`), same
IP, same services. Confirm:

```bash
ssh gduthie@172.16.10.136 'uptime; systemctl is-active twyford-dashboard train-pi-controller'
```

## Step 3 — Repair the corrupted files (the important step)

**Do this before expanding.** Expansion runs `growpart` and `/sbin/resize2fs`, and those binaries
came off the failing card — repair first so you are resizing with known-good tools. There is
8.6 GB free on the unexpanded root, which is ample for the downloads.

Rewrite every package-managed file from the repositories. This is what removes the corruption
the clone brought across.

```bash
ssh gduthie@172.16.10.136 'sudo apt-get update && \
  sudo DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y \
  $(dpkg --get-selections | grep -w install | cut -f1)'
```

902 packages. Mostly download time; run it and leave it. If the WiFi drops partway, just run it
again — it is idempotent.

**Then rewrite the boot partition**, which the general reinstall above does not fully cover.
A damaged chunk sits ~48 MB into `/boot/firmware`, where `initramfs8` and `kernel8.img` live:

```bash
ssh gduthie@172.16.10.136 'sudo apt-get install --reinstall -y \
  linux-image-rpi-v8 linux-image-rpi-2712 raspi-firmware && \
  sudo update-initramfs -u -k all'
```

Reboot afterwards and confirm it comes back up before continuing.

*Lower-bandwidth alternative:* reinstall only the packages that own damaged files. Because the
flagged set **varies between runs**, take the union of several passes first:

```bash
ssh gduthie@172.16.10.136 'for i in 1 2 3 4 5; do sudo dpkg -V; done \
  | awk "{print \$NF}" | sort -u > /tmp/damaged.txt; wc -l < /tmp/damaged.txt
  xargs -a /tmp/damaged.txt -n1 dpkg -S 2>/dev/null | cut -d: -f1 | sort -u > /tmp/pkgs.txt
  cat /tmp/pkgs.txt'
```

then `apt-get install --reinstall` just those. Faster, but relies on `dpkg -V` having caught
everything — the full reinstall is the safer choice if you can spare the bandwidth.

## Step 4 — Expand the root filesystem into the rest of the 64 GB

The clone leaves ~48 GB unallocated. `raspi-config` does partition + filesystem in one go:

```bash
ssh gduthie@172.16.10.136 'sudo raspi-config --expand-rootfs && sudo reboot'
```

If that misbehaves, the manual equivalent (both tools are present, in `/sbin`):

```bash
ssh gduthie@172.16.10.136 'sudo growpart /dev/mmcblk0 2 && sudo /sbin/resize2fs /dev/mmcblk0p2'
```

Verify — root should now show ~59 GB rather than 14 GB:

```bash
ssh gduthie@172.16.10.136 'df -h /'
```

## Step 5 — Rebuild the board's Python venv

**Do not trust the cloned venv.** Its compiled `.so` files (pygame/SDL objects) sit in a damaged
region of the old card, so the clone carries corrupt copies. It is not in the backup either —
by design, because it is rebuildable:

```bash
ssh gduthie@172.16.10.136 'cd /home/gduthie/Bus-Departure-Board && \
  sudo systemctl stop train-pi-controller && \
  rm -rf venv && python3 -m venv venv && \
  ./venv/bin/pip install -r requirementsPy3.txt && \
  sudo systemctl start train-pi-controller'
```

Re-add `py-spy` afterwards if you want the profiling tooling back:
`./venv/bin/pip install py-spy`.

## Step 6 — Restore `/home` from the verified backup

Optional but recommended: it replaces the cloned copies with the bytes checksum-verified on
2026-08-12.

```bash
rsync -a --rsync-path="sudo rsync" \
  ~/Programming/pi-backups/2026-08-12/joggler/twyford-dashboard/ \
  gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/

rsync -a --rsync-path="sudo rsync" \
  ~/Programming/pi-backups/2026-08-12/trainpi/Bus-Departure-Board/ \
  gduthie@172.16.10.136:/home/gduthie/Bus-Departure-Board/
```

**Do not add `--delete`** — the backup excludes `venv/`, which you have just rebuilt.

While you are here, the `luma_*.png` debug screenshots littering `/home/gduthie` are junk from
the board library and can go: `rm -f /home/gduthie/luma_*.png`.

## Step 7 — Verify the new card

The check that actually detects this failure mode. Ten identical hashes = good:

```bash
ssh gduthie@172.16.10.136 'for i in $(seq 1 10); do
  sudo sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"
  md5sum /usr/bin/sed
done | sort | uniq -c'
```

Then a full package verification — should print nothing:

```bash
ssh gduthie@172.16.10.136 'sudo dpkg -V'
```

And confirm the services and endpoints are live:

```bash
ssh gduthie@172.16.10.136 'systemctl is-active twyford-dashboard train-pi-controller train-pi-restart.timer
  curl -s -o /dev/null -w "trains:%{http_code}\n" http://localhost:5001/api/trains
  curl -s -o /dev/null -w "flights:%{http_code}\n" "http://localhost:5001/api/flights?lat=51.474&lon=-0.861"'
```

## Step 8 — Afterwards

- Keep the old card until the new one has run clean for a week.
- Fit a heatsink while the Pi is open — it idles at 78–83 °C and gets ARM-frequency-capped
  (PI-SETUP.md → "The Pi itself"). Heat plausibly contributed to the card's failure.
- Re-run `dpkg -V` after a week as a regression check.

## If the clone comes up broken instead

Fall back to a fresh Raspberry Pi OS image (Raspberry Pi Imager can pre-set hostname, user, SSH
key and WiFi, so it needs no monitor), then follow the restore outline in
`~/Programming/pi-backups/2026-08-12/README.md`. Slower and more hands-on, but provably clean.
