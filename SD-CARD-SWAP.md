# Swapping the Pi's failing SD card — clone-and-repair runbook

Written 2026-08-12, after confirming the card returns different data on every physical read
(see PROJECT.md → "The Pi's SD card is failing").

> **This was executed successfully on 2026-08-12.** Old card SanDisk `SL16G` (06/2016) →
> new SanDisk `SN64G` (02/2026). Result: 110 corrupted files repaired to 0, root expanded
> 14 GB → 59 GB, `/home` verified byte-identical, all services healthy. Total ~90 minutes.
> The "gotchas hit in practice" section at the end records what the plan did not anticipate —
> **read it before repeating this.**

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

- **The boot partition is affected**, so the kernel/firmware must be rewritten too, not just
  userland. In practice the package repair regenerates the initramfs and does this for free.
- **The venv's compiled `.so` files sit in a damaged region.** In the event they survived the
  clone intact, but do not assume it — verify by force-reinstalling at *pinned* versions, not by
  rebuilding from `requirementsPy3.txt` (whose `>=` constraints would upgrade a working display).
  See "Verifying the venv" at the end. Also purge `__pycache__`: corrupt `.pyc` files are
  invisible to `dpkg -V` and survive every package repair.

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

### Never mount a partition to inspect it — read it raw

**Mounting a filesystem on macOS is never read-only.** Mounting the FAT `bootfs` volume to check
its contents causes macOS to write `.Spotlight-V100`/`.fseventsd` and to update the FSInfo
free-cluster counters, so the card immediately stops matching the image. Worse, macOS
**auto-mounts the volume as soon as `dd` finishes** and re-reads the partition table, and
`diskutil unmountDisk` then flushes FSInfo back to the card — so the difference reappears even
if you rewrite the partition. It is not a fight you can win, and does not need winning.

Verify structures by reading the device or image directly instead — boot signature, partition
entries, FAT32 BPB, ext4 superblock magic/UUID/state can all be parsed from raw bytes without
mounting anything (see the Python snippets used on 2026-08-12, and step 7 below).

### Expect the FSInfo sector to differ, and ignore it

A byte-for-byte `cmp` of card against image will report a difference at **absolute offset
8,389,608–8,389,615** — partition offset 1000–1007, which is `FSI_Free_Count` and `FSI_Nxt_Free`
in the FAT32 FSInfo sector. This is benign and expected:

- The FAT32 spec explicitly permits these to be stale; they are hints, not authority.
- Linux's FAT driver treats them as untrusted and recomputes from the FAT on mount.
- The Pi bootloader reads `config.txt`/`kernel8.img` without consulting FSInfo at all.

What matters is that **nothing differs in the data region** (absolute offset ≥ 10,493,952 for
this layout, i.e. past both FAT copies) and nothing differs in the ext4 partition (≥ 545,259,520).
Use `cmp -l` to list *all* differences rather than plain `cmp`, which stops at the first:

```bash
sudo dd if=/dev/rdiskN bs=4m 2>/dev/null | head -c 15523119104 \
  | cmp -l - "$HOME/pi-card-image.img" > /tmp/carddiff.txt
wc -l < /tmp/carddiff.txt          # expect a handful, all around 8389609
```

Note `cmp -l` is CPU-bound and runs at roughly 25 MB/s — budget ~10 minutes for 14.5 GB, versus
~3 for a plain `cmp`.

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

---

## Gotchas hit in practice (2026-08-12)

Four things the plan above did not anticipate. All cost time; all are avoidable next time.

### 1. `sqv` was corrupt, which broke apt entirely — a chicken-and-egg

Trixie's apt verifies repository signatures with `/usr/bin/sqv`. That binary was in the damaged
set, so **every** repository failed with `No good signature` and `apt-get update` was unusable —
apt could not repair the very thing apt needed to work.

Resolved **without** weakening apt, by fetching the package directly over HTTPS and verifying it
against the md5 dpkg recorded at original install (which predates the corruption):

```bash
curl -fsSL -o /tmp/sqv.deb \
  "https://deb.debian.org/debian/pool/main/r/rust-sequoia-sqv/sqv_1.3.0-3+b2_arm64.deb"
dpkg-deb -x /tmp/sqv.deb /tmp/sqvx
md5sum /tmp/sqvx/usr/bin/sqv                                   # compare against:
sudo grep -h "usr/bin/sqv$" /var/lib/dpkg/info/*.md5sums
sudo dpkg -i /tmp/sqv.deb
```

TLS transport plus a hash match against a pre-corruption record is stronger evidence than
`--allow-unauthenticated`, and keeps signature verification on for everything afterwards.
Note the archive carries the binNMU (`+b2`); `dpkg -s` reports the version without it.

**Generalise this:** if any of `sqv`, `gpgv`, `apt`, `dpkg`, `tar` or `bash` is corrupt, apt
cannot bootstrap itself. Repair those by direct download first.

### 2. Repair `sed` before the bulk run

`/usr/bin/sed` was corrupt and dying with `Illegal instruction`. Countless dpkg maintainer
scripts call `sed`, so a bulk repair could fail midway. Fix it first:

```bash
sudo DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y sed
echo x | sed s/x/ok/          # must print "ok"
```

Then check `bash perl awk grep tar gzip` all still run before proceeding.

### 3. Reinstall only the damaged packages, not all 902

The blanket reinstall in step 3 is wrong for a system with pending updates. It hit a
`libcamera` solver conflict, and — more importantly — **204 packages had upgrades pending**, so
it would have upgraded the system rather than repaired it. That is a much bigger change than a
card swap warrants.

Because the new card reads deterministically, `dpkg -V` is now stable and complete (it varied
129/203 on the failing card, which is why the original plan distrusted it). So target precisely:

```bash
sudo dpkg -V | awk '{print $NF}' | sort -u > files.txt
xargs -a files.txt -n1 dpkg -S 2>/dev/null | cut -d: -f1 | tr -d ' ' | sort -u > pkgs.txt
sudo apt-get install --reinstall -y -o Dpkg::Options::=--force-confold $(tr '\n' ' ' < pkgs.txt)
```

110 damaged files → 36 packages → repaired to 0 in about 6 minutes. `--force-confold` keeps your
edited conffiles; check them individually rather than letting them be replaced (see below).
A few packages cannot be reinstalled at their installed version because the archive only carries
a newer build — those necessarily upgrade, which for `bash`/`systemd`/`openssh` is fine.

This also regenerates the initramfs, which rewrites `/boot/firmware/initramfs8` and
`initramfs_2712` — covering the damaged boot-partition chunk without a separate kernel reinstall.

### 4. Corrupt `.pyc` bytecode caches survive a package repair

After the package repair the board still crashed:

```
File "<frozen importlib._bootstrap_external>", line 784, in _compile_bytecode
ValueError: could not convert string to float: ''
```

A failure inside `_compile_bytecode` means Python read a **cached `.pyc` that unmarshalled to
garbage**. Bytecode caches are not package-managed, so `dpkg -V` cannot see them and
`apt --reinstall` does not clear them. Purge them wherever Python runs:

```bash
sudo find /home/gduthie/Bus-Departure-Board /home/gduthie/twyford-dashboard \
  -name __pycache__ -type d -prune -exec rm -rf {} +
sudo find /home/gduthie/Bus-Departure-Board /home/gduthie/twyford-dashboard -name '*.pyc' -delete
```

1,346 `.pyc` files were purged. Python regenerates them on next import.

### Conffiles: check, do not blanket-replace

`dpkg -V` flags conffiles (`c` in column 2) for *any* deviation, including legitimate edits.
Two remain flagged permanently and **should not be repaired**:

| File | Why it differs |
|---|---|
| `/etc/login.defs` | Raspberry Pi OS adds sbin dirs to `ENV_PATH` |
| `/etc/skel/.bashrc` | Raspberry Pi OS enables colour prompt + grep aliases |

Distinguish corruption from customisation by diffing against a pristine copy
(`apt-get download <pkg>`, `dpkg-deb -x`, `diff`) — a real edit is coherent, corruption is not.
Do this **before** trusting `--force-confold`; and check `sudo visudo -c` parses, since
`/etc/sudoers.d/010_pi-nopasswd` is what grants passwordless sudo.

### Two shell traps worth knowing

- **`pkill -f <pattern>` matches your own SSH command line** and will kill your session
  (exit 255). Use `pgrep -x`, or a pattern that cannot match the invoking command.
- **`fs.protected_regular`** stops root overwriting another user's file in a sticky directory
  like `/tmp`, giving a confusing `Permission denied` *as root*. Use a root-owned working
  directory. Related: `sudo cmd > /root/file` redirects as **your** user and fails — use
  `sudo bash -c 'cmd > /root/file'`.

### Verifying the venv

Do not rebuild from `requirementsPy3.txt` — its `>=` constraints turn a repair into a version
upgrade of a working display. Pin to what is installed instead:

```bash
./venv/bin/pip freeze > /tmp/venv-pinned.txt
./venv/bin/pip install --force-reinstall --no-cache-dir -r /tmp/venv-pinned.txt
```

`RPi.GPIO` and `spidev` fail to rebuild (no wheels, missing build deps) — harmless, the existing
installs survive, but confirm with `pip list` afterwards since `--force-reinstall` uninstalls
before installing.
