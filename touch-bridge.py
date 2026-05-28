#!/usr/bin/env python3
# Reads raw touch events from the AmSC touchscreen (BTN_LEFT + ABS_X/Y, no BTN_TOUCH)
# and injects them as X11 pointer events via XTest so Chromium can respond to taps.
#
# Events are buffered per SYN_REPORT frame so ABS position is always updated
# before the button event fires — fixes "move then click" two-tap behaviour.
import struct
import ctypes
import time
import os

DEVICE = '/dev/input/event1'
SCREEN_W = 800
SCREEN_H = 480
ABS_MAX = 32639

EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
BTN_LEFT = 272
ABS_X = 0
ABS_Y = 1

# 32-bit Linux: input_event = timeval(4+4) + type(2) + code(2) + value(4) = 16 bytes
EVENT_FMT = struct.Struct('<LLHHi')
EVENT_SIZE = EVENT_FMT.size


def open_display():
    libX11 = ctypes.CDLL('libX11.so.6')
    libXtst = ctypes.CDLL('libXtst.so.6')
    libX11.XOpenDisplay.restype = ctypes.c_void_p
    libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    libX11.XFlush.argtypes = [ctypes.c_void_p]
    libXtst.XTestFakeMotionEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
    ]
    libXtst.XTestFakeButtonEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
    ]
    dpy = None
    while dpy is None:
        dpy = libX11.XOpenDisplay(b':0')
        if dpy is None:
            time.sleep(0.5)
    return libX11, libXtst, dpy


def scale(val, axis_max, screen_max):
    return max(0, min(screen_max - 1, int(val * screen_max / axis_max)))


def main():
    libX11, libXtst, dpy = open_display()

    while not os.path.exists(DEVICE):
        time.sleep(0.5)

    cur_x = SCREEN_W // 2
    cur_y = SCREEN_H // 2
    btn_held = False

    # Per-frame pending state — committed on SYN_REPORT
    pending_x = None
    pending_y = None
    pending_btn = None

    with open(DEVICE, 'rb') as f:
        while True:
            data = f.read(EVENT_SIZE)
            if len(data) < EVENT_SIZE:
                break
            _, _, ev_type, ev_code, ev_value = EVENT_FMT.unpack(data)

            if ev_type == EV_ABS:
                if ev_code == ABS_X:
                    pending_x = scale(ev_value, ABS_MAX, SCREEN_W)
                elif ev_code == ABS_Y:
                    pending_y = scale(ev_value, ABS_MAX, SCREEN_H)

            elif ev_type == EV_KEY and ev_code == BTN_LEFT and ev_value != 2:
                pending_btn = ev_value  # 1 = down, 0 = up

            elif ev_type == EV_SYN:
                # Apply position first so click always lands at the right spot
                pos_changed = False
                if pending_x is not None:
                    cur_x = pending_x
                    pending_x = None
                    pos_changed = True
                if pending_y is not None:
                    cur_y = pending_y
                    pending_y = None
                    pos_changed = True

                if pending_btn is not None:
                    libXtst.XTestFakeMotionEvent(dpy, -1, cur_x, cur_y, 0)
                    libXtst.XTestFakeButtonEvent(dpy, 1, pending_btn, 0)
                    libX11.XFlush(dpy)
                    btn_held = bool(pending_btn)
                    pending_btn = None
                elif pos_changed and btn_held:
                    # Drag: position changed while button held
                    libXtst.XTestFakeMotionEvent(dpy, -1, cur_x, cur_y, 0)
                    libX11.XFlush(dpy)


if __name__ == '__main__':
    main()
