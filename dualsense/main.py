"""DualSense HID writer - adaptive triggers only.

Writes L2/R2 trigger effect reports directly to the DualSense controller
over USB or Bluetooth. Motor rumble bytes are left untouched when
allow_steam_rumble is True, so Steam and the game keep control of body
vibration independently alongside the trigger effects.

HID output report format: https://github.com/nondebug/dualsense
"""

import logging
import struct
import sys
import threading
import time
import zlib

if sys.platform.startswith("linux"):
    from . import _hidraw as hid
else:
    import hid

from .triggers import off, rigid

log = logging.getLogger("hh.dualsense")

_SONY_VID = 0x054C
_PIDS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge
_NAMES = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}

# HID output report layout (byte offsets) per transport
_USB = {"id": 0x02, "flags": 1, "vf1": 2, "psav": 10, "r": 11, "l": 22, "size": 64, "bt": False}
_BT  = {"id": 0x31, "flags": 2, "vf1": 3, "psav": 11, "r": 12, "l": 23, "size": 78, "bt": True}

# valid_flag0 bit masks
_FL_MOTORS   = 0x03  # right motor (bit 0) + left motor (bit 1)
_FL_TRIGGERS = 0x0C  # right trigger (bit 2) + left trigger (bit 3)


def _find_controller():
    """Return the gamepad HID interface dict or None."""
    devs = hid.enumerate(_SONY_VID, 0)
    pids = set(_PIDS)
    for d in devs:
        if d.get("product_id") in pids and d.get("usage_page") == 1 and d.get("usage") == 5:
            return d
    for d in devs:
        if d.get("product_id") in pids:
            return d
    return None


def _detect_bluetooth(info):
    """Infer transport from bus_type field or device path."""
    bus = info.get("bus_type")
    if bus in (2, 5):
        return True
    if bus in (1, 3):
        return False
    path = info.get("path", b"")
    if isinstance(path, str):
        path = path.encode()
    p = path.upper()
    return b"BTHENUM" in p or b"BLUETOOTH" in p


class DualSense:
    """Writes adaptive trigger effects to a DualSense over USB or Bluetooth.

    Starts without a controller and retries every `retry_interval` seconds.
    Drops writes silently while disconnected.

    Set allow_steam_rumble = True (default) so the motor bytes in each HID
    report are left unclaimed, letting Steam deliver body rumble alongside
    the trigger effects written here.
    """

    def __init__(self, retry_interval: float = 10.0):
        self._interval = retry_interval
        self._dev = None
        self._layout = _USB
        self._lock = threading.Lock()
        self._pending = (off(), off())
        self._dirty = False
        self._running = False
        self._thread = None
        self._connected = False
        self._open_warned = False
        self.allow_steam_rumble = True

    @property
    def connected(self):
        return self._connected

    def open(self):
        """Start the background I/O thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hh-ds")
        self._thread.start()

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._release()

    def set(self, left, right):
        """Queue a new (L2, R2) effect pair."""
        with self._lock:
            self._pending = (left, right)
            self._dirty = True

    def _connect(self):
        info = _find_controller()
        if not info:
            return False
        name = _NAMES.get(info.get("product_id"), "DualSense")
        try:
            dev = hid.device()
            dev.open_path(info["path"])
            dev.set_nonblocking(True)
        except (OSError, IOError) as exc:
            if not self._open_warned:
                self._open_warned = True
                if sys.platform.startswith("linux"):
                    log.error(
                        "%s: cannot open device (%s)\n"
                        "  Install udev rules then unplug and replug:\n"
                        "    sudo cp packaging/linux/70-dualsense.rules /etc/udev/rules.d/\n"
                        "    sudo udevadm control --reload-rules && sudo udevadm trigger",
                        name, exc,
                    )
                else:
                    log.warning(
                        "%s: cannot open device (%s)\n"
                        "  Close DS4Windows, DualSenseX, or Steam Big Picture and replug.\n"
                        "  If that does not help, try running as Administrator.",
                        name, exc,
                    )
            return False

        self._dev = dev
        self._layout = _BT if _detect_bluetooth(info) else _USB
        self._connected = True
        self._open_warned = False
        log.info("%s connected (%s)", name, "Bluetooth" if self._layout["bt"] else "USB")
        self._startup_pulse()
        return True

    def _release(self):
        if self._dev:
            try:
                self._dev.write(self._build(off(), off()))
            except Exception:
                pass
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        if self._connected:
            log.warning("DualSense disconnected - retrying every %.0fs", self._interval)
        self._connected = False

    def _startup_pulse(self):
        """Brief resistance bump on connect so the user knows the controller is live."""
        try:
            self._dev.write(self._build(rigid(180), rigid(180)))
            time.sleep(0.18)
            self._dev.write(self._build(off(), off()))
        except Exception:
            pass

    def _loop(self):
        last_try = -1e9
        waiting_logged = False
        while self._running:
            if not self._connected:
                now = time.monotonic()
                if now - last_try < self._interval:
                    time.sleep(0.1)
                    continue
                last_try = now
                if self._connect():
                    waiting_logged = False
                    continue
                if not waiting_logged:
                    log.info("Waiting for DualSense - retrying every %.0fs", self._interval)
                    waiting_logged = True
                continue

            try:
                try:
                    self._dev.read(self._layout["size"])
                except OSError:
                    pass
                with self._lock:
                    if not self._dirty:
                        time.sleep(0.001)
                        continue
                    left, right = self._pending
                    self._dirty = False
                self._dev.write(self._build(left, right))
            except Exception as exc:
                log.warning("HID write failed (%s) - reconnecting", exc)
                self._release()

    def _build(self, left, right):
        """Assemble a HID output report for the given L2/R2 effect pair."""
        L = self._layout
        buf = bytearray(L["size"])
        buf[0] = L["id"]
        if L["bt"]:
            buf[1] = 0x02
        # Claim trigger bytes; only claim motor bytes when zeroing them out
        buf[L["flags"]] = _FL_TRIGGERS | (0 if self.allow_steam_rumble else _FL_MOTORS)
        for offset, (mode, params) in ((L["r"], right), (L["l"], left)):
            buf[offset] = mode
            for i, b in enumerate(params[:10]):
                buf[offset + 1 + i] = b & 0xFF
        if L["bt"]:
            struct.pack_into("<I", buf, 74, zlib.crc32(b"\xA2" + bytes(buf[:74])))
        return bytes(buf)
