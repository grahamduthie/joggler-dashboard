# Radio View — Controls Bar Layout Bug

## Problem

In the radio playing state (when now-playing data is available), the controls bar
(play/pause button + volume slider) was not flush with the bottom of the screen.
A blank strip of dark space appeared below it.

The controls bar appeared to be in the correct position in screenshots taken remotely,
but the user reported it was still wrong on the physical device. This mismatch was caused
by a deployment error — see below.

---

## Root Causes

### 1. CSS specificity conflict

`.view { position: absolute; inset: 0; }` (specificity 0,1,0) is the base rule that makes
all views fill the `#content` area. But `#view-radio { position: relative; }` (specificity
1,0,0) overrides the position property, making `#view-radio` content-sized rather than
filling its container.

With `position: relative`, `inset: 0` has no stretching effect — it just offsets the
element by 0 from its normal flow position. So `#view-radio` was only as tall as its
content, not the full 418 px of `#content`.

As a result, `position: absolute; bottom: 0` on `#radio-controls` was anchoring to the
bottom of the content box, not the bottom of the screen. Hence the gap.

### 2. Deployment to wrong machine

The dashboard is served by `transport-proxy.py` on the **Raspberry Pi** at
`172.16.10.136:5001`. Chromium on the Joggler fetches the page from the Pi.

Throughout this debugging session, `dashboard.html` was repeatedly deployed to the
**Joggler** (`of@172.16.10.168:/home/of/dashboard.html`) instead of the Pi
(`gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/dashboard.html`).

The file at `/home/of/dashboard.html` on the Joggler is not served by anything. Every
CSS fix was landing in the wrong place, so the browser kept serving the old file from
the Pi. This caused multiple rounds of "deployed but no change visible."

---

## Fixes Tried (in order)

### Attempt 1 — Absolute position on controls bar
Added `position: absolute; bottom: 0; left: 0; right: 0` to `#radio-controls` in the
`.has-track` state, and `margin-bottom: 64px` to `#radio-track` to make room.

**Why it failed:** `#view-radio { position: relative; }` makes `#view-radio` content-sized.
`position: absolute; bottom: 0` anchors to the bottom of the content box, which ends
wherever the content ends — not at the screen bottom.

### Attempt 2 — Add `height: 100%` to `#view-radio.active`
The idea: if the parent `#content` has a definite flex-determined height, `height: 100%`
on the child should resolve to that full height, giving `position: absolute; bottom: 0`
a correct reference point.

**Why it appeared to fail:** Deployed to the Joggler, not the Pi. The Pi served the
unchanged file, so no visible effect on the device.

### Attempt 3 — `position: absolute; inset: 0` on `#view-radio.active`
`#view-radio.active` has specificity (1,1,0) vs `#view-radio` at (1,0,0), so adding
`position: absolute; inset: 0` to the `.active` rule should override the base rule.

Simultaneously switched from the absolute-positioned controls bar back to pure flex
layout (removed `#radio-controls { position: absolute; bottom: 0 }` and
`#radio-track { margin-bottom: 64px }`), relying on `#radio-track { flex: 1 }` to push
the controls to the natural bottom.

The lime-background diagnostic (`background: lime` on `#content`) was used to verify
whether `#view-radio` was filling `#content`. After removing `#view-radio { position: relative; }`,
the lime disappeared from the radio view, confirming the fix.

**Why it appeared to still fail:** All of these changes were deployed to the Joggler,
not the Pi. The diagnostic screenshots were misleading — the Pi was still serving the
old file.

### Attempt 4 — Remove `#view-radio { position: relative; }` entirely
Removing the rule at line 1066 lets `.view { position: absolute; inset: 0; }` apply
unmodified, exactly as it does for every other view. This is the correct and simplest fix.

`#station-btn` and `#cast-btn` (absolutely positioned children of `#view-radio`) still
work correctly because `#view-radio.active` with `position: absolute` is itself a
positioned ancestor.

**This is the fix that was applied.**

### Attempt 5 — Deploy to the Pi ← ACTUALLY FIXED IT
After discovering the Pi's `dashboard.html` still had the original CSS (confirmed by
`ssh gduthie@172.16.10.136 'grep -A6 "view-radio\.active {"'`), the Mac file was
deployed to the correct location:

```bash
scp dashboard.html gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/dashboard.html
ssh of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'
```

After this, the controls bar was flush with the bottom of the screen.

---

## Final CSS State

```css
/* Removed entirely: */
/* #view-radio { position: relative; } */

/* Added to #view-radio.active: */
#view-radio.active {
  position: absolute;
  inset: 0;
  background: linear-gradient(160deg, #0c1f38 0%, #102c4e 50%, #091c30 100%);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  padding: 0;
}

/* Controls bar: plain flex item, no absolute positioning */
#radio-controls {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  padding: 0 16px;
  border-top: 1px solid rgba(0,158,221,0.15);
}

/* Playing state: #radio-track grows to fill space, controls sit at bottom naturally */
#radio-track {
  display: none;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
#view-radio.has-track #radio-track { display: flex; }
```

---

## Deployment Rule

**Always deploy `dashboard.html` to the Pi:**

```bash
scp dashboard.html gduthie@172.16.10.136:/home/gduthie/twyford-dashboard/dashboard.html && \
  ssh of@172.16.10.168 'DISPLAY=:0 xdotool key ctrl+shift+r'
```

The Joggler's `/home/of/dashboard.html` is not served by any process.
The file at `/home/gduthie/twyford-dashboard/dashboard.html` on the Pi is what
`transport-proxy.py` serves at `http://172.16.10.136:5001/`.
