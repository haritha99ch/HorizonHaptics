import logging
import sys
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

log = logging.getLogger("hh.audio")

class AudioHaptics:
    def __init__(self):
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
        
        # Oscillators phase
        self._phase_low = 0.0
        self._phase_high = 0.0
        self._phase_engine = 0.0
        
        self._low_freq = 65.0
        self._high_freq = 180.0

        self._blocksize = 2048
        self._noise_buf = np.random.uniform(-1.0, 1.0, self._blocksize).astype(np.float32)
        self._noise_pos = 0

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

            log.warning("No 4-channel DualSense audio endpoint found. Make sure it is connected via USB.")
        except Exception as e:
            log.exception("Error scanning for audio devices: %s", e)

    def start(self):
        if not sd or self._device_idx is None:
            return
            
        try:
            self._running = True
            # We use blocksize=0 (auto) and latency='low'
            self._stream = sd.OutputStream(
                device=self._device_idx,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32,
                blocksize=self._blocksize,
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
            
        # Generate time array for this block
        t_low = (np.arange(frames) + self._phase_low) / self.sample_rate
        t_high = (np.arange(frames) + self._phase_high) / self.sample_rate
        t_engine = (np.arange(frames) + self._phase_engine) / self.sample_rate
        
        # Advance phases
        self._phase_low = (self._phase_low + frames) % self.sample_rate
        self._phase_high = (self._phase_high + frames) % self.sample_rate
        self._phase_engine = (self._phase_engine + frames) % self.sample_rate
        
        # Base waveforms (range -1.0 to 1.0)
        wave_low = np.sin(2 * np.pi * self._low_freq * t_low)
        # Read noise from pre-allocated buffer to avoid per-callback allocation
        end = self._noise_pos + frames
        if end <= self._blocksize:
            noise = self._noise_buf[self._noise_pos:end]
        else:
            noise = np.concatenate([self._noise_buf[self._noise_pos:], self._noise_buf[:end - self._blocksize]])
            np.copyto(self._noise_buf, np.random.uniform(-1.0, 1.0, self._blocksize).astype(np.float32))
        self._noise_pos = end % self._blocksize
        tone = np.sin(2 * np.pi * 200.0 * t_high)
        wave_high = (noise * 0.7) + (tone * 0.3)
        
        # Engine: Sawtooth wave for a raspy/growly feel
        wave_engine = 2.0 * (t_engine * self.engine_freq - np.floor(0.5 + t_engine * self.engine_freq))
        
        # Mix L and R
        # We apply an easing so sudden changes don't cause speaker pop, 
        # though doing it per-block is rough, it's fast enough.
        mix_l = (wave_low * self.l_low) + (wave_high * self.l_high) + (wave_engine * self.engine_vol)
        mix_r = (wave_low * self.r_low) + (wave_high * self.r_high) + (wave_engine * self.engine_vol)
        
        # Hard clip to prevent overflow distortion
        mix_l = np.clip(mix_l, -1.0, 1.0)
        mix_r = np.clip(mix_r, -1.0, 1.0)
        
        # Clear all channels
        outdata.fill(0.0)
        
        # DualSense typically uses channels 2 and 3 for haptics (0-indexed: 2=Left Haptic, 3=Right Haptic)
        # Channels 0 and 1 are the headphone jack.
        if self.channels >= 4:
            outdata[:, 2] = mix_l
            outdata[:, 3] = mix_r
        elif self.channels == 2:
            # Fallback if it's presenting as a 2-channel device
            outdata[:, 0] = mix_l
            outdata[:, 1] = mix_r
