# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# The report layout and CRC seed implemented here follow SAxense by
# egormanga (https://github.com/egormanga/SAxense), released under MPL-2.0,
# so this file keeps that license. The rest of HorizonHaptics stays MIT.

"""DualSense audio haptics over Bluetooth.

Windows only exposes the DualSense's haptic actuators as a sound card over
USB. Over Bluetooth, the controller accepts the haptic audio inside HID
output report 0x32 instead: a 142-byte report carrying 32 stereo frames of
signed 8-bit PCM at 3 kHz, sent every ~10.67 ms.

The wire format (report layout and CRC seed) was reverse-engineered by
egormanga in SAxense (https://github.com/egormanga/SAxense, MPL-2.0).
Windows feasibility was shown by awalol/dualsense-bt-haptics.
"""

import logging
import threading
import time

import numpy as np

log = logging.getLogger("hh.bt_haptics")

SAMPLE_RATE = 3000          # Hz, per channel
FRAMES = 32                 # stereo frames per report -> 64 PCM bytes
TICK_S = FRAMES / SAMPLE_RATE   # ~10.667 ms
REPORT_SIZE = 142

_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0xEDB88320 if _c & 1 else 0)
    _CRC_TABLE.append(_c)
_CRC_INIT = (~0xEADA2D49) & 0xFFFFFFFF  # standard CRC32 pre-fed with 0xA2


def _crc32(buf) -> int:
    crc = _CRC_INIT
    for b in buf:
        crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return (~crc) & 0xFFFFFFFF


def build_report(counter: int, pcm64: bytes) -> bytes:
    """One haptics report: id 0x32, control packet 0x11, PCM packet 0x12, CRC."""
    if len(pcm64) != FRAMES * 2:
        raise ValueError("pcm64 must be 64 bytes")
    body = bytearray(REPORT_SIZE - 4)
    body[0] = 0x32
    body[1] = 0x00
    # packet 0x11 (sized): 7 bytes, last one is a free-running counter
    body[2:11] = bytes((0x91, 0x07, 0xFE, 0x00, 0x00, 0x00, 0x00, 0xFF, counter & 0xFF))
    # packet 0x12 (sized): 64 bytes of interleaved stereo s8 PCM
    body[11] = 0x92
    body[12] = 0x40
    body[13:77] = pcm64
    return bytes(body) + _crc32(body).to_bytes(4, "little")


class BtHapticsStreamer:
    """Streams synthesized haptic audio to a Bluetooth DualSense.

    `render(frames, rate)` must return two float arrays (left, right) in
    [-1, 1]. The streamer idles while the controller is absent or on USB, and
    stops sending once the signal has been silent for a few reports, so it
    uses no Bluetooth bandwidth when body haptics are off or the car is idle.
    """

    QUIET_REPORTS = 4  # trailing silent reports sent before going idle

    def __init__(self, ds, render, gain: float = 1.0):
        self._ds = ds
        self._render = render
        self.gain = gain
        self._running = False
        self._thread = None
        self._counter = 0
        self._quiet = self.QUIET_REPORTS
        self._announced = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hh-bt-haptics")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _loop(self):
        next_tick = time.perf_counter()
        while self._running:
            if not self._ds.is_bluetooth:
                self._announced = False
                time.sleep(0.25)
                next_tick = time.perf_counter()
                continue
            if not self._announced:
                log.info("Bluetooth haptics streamer active (report 0x32, %d Hz)", SAMPLE_RATE)
                self._announced = True

            next_tick += TICK_S
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.1:
                next_tick = time.perf_counter()  # fell far behind: resync, don't burst

            left, right = self._render(FRAMES, SAMPLE_RATE)
            peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))))
            if peak < 1e-3:
                if self._quiet >= self.QUIET_REPORTS:
                    continue
                self._quiet += 1
            else:
                self._quiet = 0

            pcm = np.empty(FRAMES * 2, dtype=np.int8)
            pcm[0::2] = np.clip(left * 127.0 * self.gain, -128, 127).astype(np.int8)
            pcm[1::2] = np.clip(right * 127.0 * self.gain, -128, 127).astype(np.int8)
            report = build_report(self._counter, pcm.tobytes())
            self._counter = (self._counter + 1) & 0xFF
            self._ds.write_raw(report)
