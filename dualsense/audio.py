import logging
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
        
        self._low_freq = 65.0   # Bass impacts (65 Hz feels heavy on DualSense)
        self._high_freq = 180.0 # Gravel texture
        
        self._find_device()

    def _find_device(self):
        if not sd:
            return
            
        try:
            # We look for WASAPI device with "DualSense" or "Wireless Controller" in the name, having 4 output channels
            hostapis = sd.query_hostapis()
            wasapi_idx = next((i for i, api in enumerate(hostapis) if api['name'] == 'Windows WASAPI'), None)
            
            if wasapi_idx is None:
                log.error("WASAPI not found, cannot initialize audio haptics.")
                return

            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['hostapi'] == wasapi_idx and dev['max_output_channels'] >= 4:
                    name = dev['name'].lower()
                    if "dualsense" in name or "wireless controller" in name:
                        self._device_idx = i
                        self.channels = dev['max_output_channels']
                        log.info(f"Found DualSense Audio Endpoint: {dev['name']} (Device {i})")
                        return
                        
            log.warning("No 4-channel DualSense audio endpoint found. Make sure it's connected via USB.")
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
                callback=self._audio_callback,
                latency='low'
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
        # For gravel, a mix of white noise and a 200Hz tone creates a very realistic crunch
        noise = np.random.uniform(-1.0, 1.0, size=frames)
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
