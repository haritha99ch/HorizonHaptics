import logging
import sys
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from .bt_haptics import BtHapticsStreamer

log = logging.getLogger("hh.audio")


class _Synth:
    """Haptic waveform generator. One instance per output path, since each
    keeps its own oscillator phases and runs at its own sample rate
    (48 kHz for the USB sound card, 3 kHz for the Bluetooth stream)."""

    LOW_FREQ = 65.0
    TONE_FREQ = 200.0

    def __init__(self, params):
        self._p = params
        self._t = 0  # running sample index

    def render(self, frames, rate):
        p = self._p
        t = (np.arange(frames) + self._t) / rate
        self._t = (self._t + frames) % (rate * 60)  # wrap every minute

        wave_low = np.sin(2 * np.pi * self.LOW_FREQ * t)
        noise = np.random.uniform(-1.0, 1.0, frames)
        tone = np.sin(2 * np.pi * self.TONE_FREQ * t)
        wave_high = (noise * 0.7) + (tone * 0.3)
        # Engine: sawtooth for a raspy/growly feel
        ef = p.engine_freq
        wave_engine = 2.0 * (t * ef - np.floor(0.5 + t * ef))

        mix_l = (wave_low * p.l_low) + (wave_high * p.l_high) + (wave_engine * p.engine_vol)
        mix_r = (wave_low * p.r_low) + (wave_high * p.r_high) + (wave_engine * p.engine_vol)
        return np.clip(mix_l, -1.0, 1.0), np.clip(mix_r, -1.0, 1.0)


class AudioHaptics:
    def __init__(self, ds=None):
        self._stream = None
        self._running = False
        self._device_idx = None

        # Audio generation state
        self.sample_rate = 48000
        self.channels = 4  # DualSense usually exposes 4 channels

        self.l_low = 0.0
        self.l_high = 0.0
        self.r_low = 0.0
        self.r_high = 0.0
        self.engine_freq = 0.0
        self.engine_vol = 0.0

        self._usb_synth = _Synth(self)
        # Bluetooth path: streams the same effects through HID report 0x32
        self._bt = BtHapticsStreamer(ds, _Synth(self).render) if ds is not None else None

        self._find_device()

    def _find_device(self):
        if not sd:
            return

        try:
            hostapis = sd.query_hostapis()
            if sys.platform.startswith("win"):
                target_api = next((i for i, a in enumerate(hostapis) if a['name'] == 'Windows WASAPI'), None)
                if target_api is None:
                    log.error("WASAPI not found, cannot initialize audio haptics.")
                    return
            else:
                target_api = next((i for i, a in enumerate(hostapis) if 'alsa' in a['name'].lower()), None)
                if target_api is None:
                    log.error("ALSA not found, cannot initialize audio haptics.")
                    return

            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['hostapi'] == target_api and dev['max_output_channels'] >= 4:
                    name = dev['name'].lower()
                    if "dualsense" in name or "wireless controller" in name:
                        self._device_idx = i
                        self.channels = dev['max_output_channels']
                        log.info("Found DualSense audio endpoint: %s (device %d)", dev['name'], i)
                        return

            log.info("No USB DualSense audio endpoint - body haptics will use the Bluetooth stream.")
        except Exception as e:
            log.exception("Error scanning for audio devices: %s", e)

    def start(self):
        if self._bt is not None:
            self._bt.start()

        if not sd or self._device_idx is None:
            return

        try:
            self._running = True
            self._stream = sd.OutputStream(
                device=self._device_idx,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32,
                blocksize=2048,
                callback=self._audio_callback,
                latency='high'
            )
            self._stream.start()
            log.info("Audio Haptics stream started.")
        except Exception as e:
            log.exception("Failed to start audio stream: %s", e)
            self._running = False

    def stop(self):
        self._running = False
        if self._bt is not None:
            self._bt.stop()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def set_haptics(self, l_low: float, l_high: float, r_low: float, r_high: float, engine_freq: float, engine_vol: float):
        """Update the target amplitudes for low, high, and engine frequencies."""
        self.l_low = l_low
        self.l_high = l_high
        self.r_low = r_low
        self.r_high = r_high
        self.engine_freq = engine_freq
        self.engine_vol = engine_vol

    def _audio_callback(self, outdata, frames, time, status):
        if status:
            log.debug(f"Audio status: {status}")

        if not self._running:
            outdata[:] = 0
            return

        mix_l, mix_r = self._usb_synth.render(frames, self.sample_rate)
        outdata.fill(0.0)
        # DualSense uses channels 2 and 3 for haptics (0/1 are the headphone jack)
        if self.channels >= 4:
            outdata[:, 2] = mix_l
            outdata[:, 3] = mix_r
        elif self.channels == 2:
            outdata[:, 0] = mix_l
            outdata[:, 1] = mix_r
