"""Trigger effect logic

R2 (throttle):
  Normal    -> feedback resistance, strength 0-3 proportional to G-force.
              + boost_resistance added when turbo boost is active.
  Wheelspin -> vibration; freq scales with combined slip, amp with G-force.
  Surface   -> light rumble on idle trigger (road texture / rumble strip).

L2 (brake):
  Normal    -> feedback resistance, strength 0-7 proportional to brake pressure.
  Handbrake -> firm resistance when HandBrake byte is non-zero.
  ABS/grip  -> vibration; freq scales with combined slip, amp inverted with brake.
  Surface   -> light rumble on idle trigger (road texture / rumble strip).

Gear shift: brief vibration burst on both triggers when gear changes.
Collision:  short hard jolt on both triggers when SmashableVelDiff spikes.

Priority (highest to lowest): collision > gear shift > handbrake/ABS/wheelspin
  > normal resistance > surface rumble
"""

import math
import time

from dualsense.triggers import feedback, off, rigid, vibration, vibration_wall
from Config.brake_settings import BrakeSettings
from Config.throttle_settings import ThrottleSettings
from Config.gear_settings import GearSettings
from Config.surface_settings import SurfaceSettings
from Config.tachometer_settings import TachometerSettings
from Config.trigger_mode import TriggerMode
from .forza_parser import DataPacket


def _map(x, in_min, in_max, out_min, out_max):
    if in_max <= in_min:
        return float(out_min)
    t = max(0.0, min(1.0, (x - in_min) / (in_max - in_min)))
    return out_min + t * (out_max - out_min)


def _ewma(value, last, alpha):
    return alpha * value + (1.0 - alpha) * last


def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(round(v))))


class Parser:
    """Computes L2/R2 HID frames from a DataPacket. Holds EWMA filter state.

    Separate EWMA state per mode (normal vs vibration) prevents scale bleed on mode transitions.
    """

    def __init__(
        self,
        throttle: ThrottleSettings | None = None,
        brake: BrakeSettings | None = None,
        gear: GearSettings | None = None,
        surface: SurfaceSettings | None = None,
        tachometer: TachometerSettings | None = None,
    ):
        self.throttle = throttle or ThrottleSettings()
        self.brake = brake or BrakeSettings()
        self.gear = gear or GearSettings()
        self.surface = surface or SurfaceSettings()
        self.tachometer = tachometer or TachometerSettings()
        # Throttle EWMA - separate per path to avoid 175-255 <-> 0-3 bleed
        self._r2_res_n = 0.0  # normal path (0 - max_resistance)
        self._r2_res_v = 0.0  # vibration path (min_stiffness - max_stiffness)
        self._r2_freq = 0.0
        # Brake EWMA
        self._l2_res_n = 0.0  # normal braking resistance (0 - max_resistance)
        self._l2_freq = 0.0  # ABS pulse frequency
        # Gear shift state
        self._prev_gear = 0
        self._shift_until = 0.0
        # Collision state
        self._collision_until = 0.0
        self._collision_l_factor = 1.0
        self._collision_r_factor = 1.0
        self._collision_intensity = 0.0
        # Suspension state
        self._prev_suspension = (0.0, 0.0, 0.0, 0.0)
        # Acceleration state for jerk (collision) detection
        self._prev_accel_x = 0.0
        self._prev_accel_z = 0.0

    def compute(self, pkt: DataPacket) -> tuple:
        """Return (L2_frame, R2_frame, r, g, b, motor_l, motor_r, player_leds, l_low, l_high, r_low, r_high)."""
        self._arm_shift(pkt)
        self._arm_collision(pkt)
        
        l2 = self._l2(pkt) if pkt.is_race_on else off()
        r2 = self._r2(pkt) if pkt.is_race_on else off()
        if not pkt.is_race_on:
            motor_l, motor_r = 0, 0
            
        # motor_l and motor_r are now handled by AudioHaptics, but we still return 0 for the HID report
        return l2, r2, *self._tachometer_color(pkt), 0, 0, self._gear_leds(pkt), *self._audio_haptics(pkt)

    def _gear_leds(self, pkt: DataPacket) -> int:
        if not pkt.is_race_on or pkt.gear == 0:
            return 0
        
        gear = pkt.gear
        if gear == 1: return 0x04 # Center
        if gear == 2: return 0x0A # Inner Left, Inner Right
        if gear == 3: return 0x15 # Outer L, Center, Outer R
        if gear == 4: return 0x1B # Outer L, Inner L, Inner R, Outer R
        if gear >= 5: return 0x1F # All 5 LEDs
        return 0

    def _audio_haptics(self, pkt: DataPacket) -> tuple[float, float, float, float, float, float]:
        if not self.surface.enable_body_haptics or not pkt.is_race_on:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            
        l_low, l_high, r_low, r_high = 0.0, 0.0, 0.0, 0.0
        engine_freq, engine_vol = 0.0, 0.0
        
        # Dynamic Omni-Collisions based on Jerk (Acceleration spikes)
        jerk_x = abs(pkt.acceleration_x - self._prev_accel_x)
        jerk_z = abs(pkt.acceleration_z - self._prev_accel_z)
        self._prev_accel_x = pkt.acceleration_x
        self._prev_accel_z = pkt.acceleration_z
        
        jerk_mag = math.sqrt(jerk_x**2 + jerk_z**2)
        if jerk_mag > 3.0:
            # Impact! Intensity scales with how hard we hit. 3.0 is a scrape, 30.0 is a wall.
            intensity = min(2.0, (jerk_mag - 3.0) / 27.0) * self.surface.collision_haptics_volume
            
            # Directional impact mapping based on current acceleration direction
            if pkt.acceleration_x > 5.0: # Pushed right -> Hit came from Left
                l_low += intensity * 1.0
                r_low += intensity * 0.2
            elif pkt.acceleration_x < -5.0: # Pushed left -> Hit came from Right
                l_low += intensity * 0.2
                r_low += intensity * 1.0
            else: # Head on or rear end
                l_low += intensity
                r_low += intensity
                
        # Legacy Collisions (Smashables like fences)
        if time.monotonic() < self._collision_until:
            bump = (self.surface.collision_amp / 255.0) * self._collision_intensity
            l_low += bump * self._collision_l_factor
            r_low += bump * self._collision_r_factor
            
        # Suspension Thud (Jumps and large potholes)
        susp_curr = (pkt.suspension_travel_fl, pkt.suspension_travel_fr, pkt.suspension_travel_rl, pkt.suspension_travel_rr)
        susp_delta = [c - p for c, p in zip(susp_curr, self._prev_suspension)]
        self._prev_suspension = susp_curr
        
        thud_threshold = -0.015  # meters per tick compression
        if susp_delta[0] < thud_threshold or susp_delta[2] < thud_threshold:
            l_low += 1.0
        if susp_delta[1] < thud_threshold or susp_delta[3] < thud_threshold:
            r_low += 1.0
            
        # Gear Shift "Kick" (Sharp bass impact on the chassis)
        if time.monotonic() < self._shift_until:
            l_low += 0.8
            r_low += 0.8
            
        # Surface Rumble (Gravel/Texture): High frequency noise
        surface_intensity = self.surface.haptic_intensity * 1.5 
        l_high += (pkt.surface_rumble_fl + pkt.surface_rumble_rl) * 0.5 * surface_intensity
        r_high += (pkt.surface_rumble_fr + pkt.surface_rumble_rr) * 0.5 * surface_intensity
        
        # Baseline Asphalt Hum (scales with speed, gentle tire roll)
        # pkt.speed is in m/s. 100 km/h is ~27.7 m/s.
        if pkt.speed > 3.0:
            speed_factor = min(1.0, (pkt.speed - 3.0) / 80.0) # Peaks around 300 km/h
            asphalt_hum = speed_factor * 0.08 * surface_intensity
            l_high += asphalt_hum
            r_high += asphalt_hum
            
        # Puddles / Water splashes (Heavy drag + splash texture)
        puddle_l = max(pkt.wheel_in_puddle_depth_fl, pkt.wheel_in_puddle_depth_rl)
        puddle_r = max(pkt.wheel_in_puddle_depth_fr, pkt.wheel_in_puddle_depth_rr)
        if puddle_l > 0:
            l_low += puddle_l * 0.6  # Heavy water drag
            l_high += puddle_l * 0.3 # Splash texture
        if puddle_r > 0:
            r_low += puddle_r * 0.6
            r_high += puddle_r * 0.3
        
        # Tire slip (Loss of grip): Low frequency heavy rumble
        if self.surface.enable_slip_haptics:
            t = self.surface.slip_threshold
            i = self.surface.slip_intensity
            
            slip_l = max(pkt.tire_combined_slip_fl, pkt.tire_combined_slip_rl)
            if slip_l > t:
                l_low += (slip_l - t) * 0.5 * i
                
            slip_r = max(pkt.tire_combined_slip_fr, pkt.tire_combined_slip_rr)
            if slip_r > t:
                r_low += (slip_r - t) * 0.5 * i
                
        # ABS Body Vibration (pulse low freq when braking with high slip)
        if pkt.brake > 100 and pkt.four_wheel_slip > self.brake.grip_loss_value:
            if int(time.monotonic() * 15) % 2 == 0:
                l_low += 0.5
                r_low += 0.5
                
        # Engine RPM Vibration
        # Map RPM to Frequency: 40 Hz at idle, up to 120 Hz at redline.
        rpm_ratio = 0.0
        if pkt.engine_max_rpm > pkt.engine_idle_rpm:
            rpm_ratio = (pkt.current_engine_rpm - pkt.engine_idle_rpm) / (pkt.engine_max_rpm - pkt.engine_idle_rpm)
            rpm_ratio = max(0.0, min(1.0, rpm_ratio))
        
        engine_freq = 40.0 + (rpm_ratio * 80.0)
        
        # Volume: Base idle rumble is faint (0.1), scales up with throttle (accel is 0-255)
        throttle_ratio = pkt.accel / 255.0
        engine_vol = (0.08 + (throttle_ratio * 0.25) + (rpm_ratio * 0.1)) * self.surface.engine_haptics_volume
        
        def _c(val): return max(0.0, min(1.0, val))
        return _c(l_low), _c(l_high), _c(r_low), _c(r_high), engine_freq, _c(engine_vol)

    def _tachometer_color(self, pkt: DataPacket) -> tuple[int, int, int]:
        if not self.tachometer.enable:
            return 0, 0, 0

        max_rpm = pkt.engine_max_rpm
        current_rpm = pkt.current_engine_rpm

        if max_rpm <= 0:
            return 0, 0, 0

        ratio = current_rpm / max_rpm

        # Forza sends native Right Trigger (throttle) rumble for rev limiter and wheelspin in surface_rumble_fr.
        # We intercept this to accurately flash the shift light when the engine is bouncing on the limiter.
        is_rev_limiter = pkt.surface_rumble_fr > 0.05 and ratio > 0.85

        start = self.tachometer.start_percent
        flash = self.tachometer.flash_percent

        if start >= flash:
            start = flash - 0.01

        # Turn on lightbar starting at start_percent
        if ratio < start and not is_rev_limiter:
            return 0, 0, 0
            
        if is_rev_limiter or ratio >= flash:
            # Flashing red
            rate = self.tachometer.flash_rate_hz
            if rate <= 0:
                return 255, 0, 0
            
            if int(time.monotonic() * rate * 2) % 2 == 0:
                return 255, 0, 0
            else:
                return 0, 0, 0
        
        t = (ratio - start) / (flash - start)  # Normalize to 0.0 - 1.0

        if t < 0.5:
            # Green to Yellow (R goes 0->255, G stays 255)
            r = _clamp(255 * (t / 0.5))
            g = 255
            b = 0
        else:
            # Yellow to Red (R stays 255, G goes 255->0)
            r = 255
            g = _clamp(255 * (1.0 - ((t - 0.5) / 0.5)))
            b = 0

        return r, g, b

    # -- Collision jolt --

    def _arm_collision(self, pkt: DataPacket):
        s = self.surface
        if (s.enable_collision
                and pkt.smashable_vel_diff > s.collision_threshold):
            self._collision_until = time.monotonic() + s.collision_duration_ms / 1000.0
            self._collision_intensity = min(1.0, pkt.smashable_vel_diff / 15.0)
            
            # Directional impact mapping
            x = pkt.acceleration_x
            if abs(x) < 5.0:
                # Head-on or rear-end: full impact on both sides
                self._collision_l_factor = 1.0
                self._collision_r_factor = 1.0
            elif x > 0:
                # Pushed left (Hit on right side): right side gets full impact
                self._collision_l_factor = 0.2
                self._collision_r_factor = 1.0
            else:
                # Pushed right (Hit on left side): left side gets full impact
                self._collision_l_factor = 1.0
                self._collision_r_factor = 0.2

    def _collision_burst(self):
        if time.monotonic() < self._collision_until:
            s = self.surface
            return vibration(_clamp(s.collision_freq), _clamp(s.collision_amp))
        return None

    # -- Gear shift --

    def _arm_shift(self, pkt: DataPacket):
        gear = pkt.gear
        prev = self._prev_gear
        if (prev > 0 and gear > 0
                and gear != prev and pkt.speed_kmh > 3.0):
            self._shift_until = time.monotonic() + self.gear.duration_ms / 1000.0
        self._prev_gear = gear

    def _shift_burst(self):
        if time.monotonic() < self._shift_until:
            g = self.gear
            return vibration(_clamp(g.freq), _clamp(g.amp))
        return None

    # -- Surface / rumble strip --

    def _surface_effect(self, pkt: DataPacket):
        """Idle trigger feedback: road texture or rumble strip.

        WheelOnRumbleStrip is position-based geometry data and is non-zero
        regardless of the in-game vibration setting, so rumble strip detection
        is checked first with fixed amplitude.

        SurfaceRumble fields mirror what FH6 would send to the controller rumble
        motors -- they are zeroed by the game when in-game vibration is disabled,
        so road texture only works when in-game vibration is on.
        """
        s = self.surface
        if pkt.on_rumble_strip:
            return vibration(_clamp(s.strip_freq), _clamp(s.strip_amp))
        rumble = pkt.surface_rumble
        if rumble <= 0.0:
            return None
        return vibration(_clamp(s.freq), _clamp(s.amp * rumble))

    # -- R2 / Throttle --

    def _r2(self, pkt: DataPacket):
        s = self.throttle
        if s.mode == TriggerMode.OFF:
            return off()

        burst = self._collision_burst()
        if burst is not None:
            return burst

        if self.gear.enable_throttle:
            burst = self._shift_burst()
            if burst is not None:
                return burst

        avg_accel = math.sqrt(
            s.turn_accel_scale * pkt.acceleration_x ** 2
            + s.fwd_accel_scale * pkt.acceleration_z ** 2
        )
        accel = pkt.accel

        losing = (
            pkt.front_slip > s.grip_loss_value
            or (pkt.rear_slip > s.grip_loss_value and accel > 200)
        )

        if losing and s.mode == TriggerMode.VIBRATION:
            freq = _map(pkt.four_wheel_slip, s.grip_loss_value, 5.0, 0, s.max_vibration)
            stiff = _map(avg_accel, 0.0, s.accel_limit, s.min_stiffness, s.max_stiffness)
            self._r2_freq = _ewma(freq, self._r2_freq, s.vib_smoothing)
            self._r2_res_v = _ewma(stiff, self._r2_res_v, s.resistance_smoothing)
            f = _clamp(self._r2_freq * s.intensity)
            r = _clamp(self._r2_res_v * s.intensity)
            if f <= s.min_vibration or accel <= s.vib_mode_start:
                return rigid(r)
            return vibration(f, r)

        res = _map(avg_accel, 0.0, s.accel_limit, s.min_resistance, s.max_resistance)
        self._r2_res_n = _ewma(res, self._r2_res_n, s.resistance_smoothing)
        boost_bonus = s.boost_resistance if pkt.boost > 0.5 else 0
        strength = _clamp(self._r2_res_n * s.intensity + boost_bonus, 0, 8)
        if strength > 0:
            return feedback([strength] * 10)

        if self.surface.enable_throttle:
            effect = self._surface_effect(pkt)
            if effect is not None:
                return effect

        return off()

    # -- L2 / Brake --

    def _l2(self, pkt: DataPacket):
        """GT7-style brake trigger.

        Normal braking: progressive feedback resistance builds with brake pressure.
        Handbrake: firm resistance when HandBrake byte is non-zero.
        ABS / lock-up: top abs_wall_zones stay firm (resistance remains) while
        lower zones pulse at ABS frequency - near exactly how GT7 signals lock-up
        without dropping the resistance wall entirely.
        """
        s = self.brake
        if s.mode == TriggerMode.OFF:
            return off()

        burst = self._collision_burst()
        if burst is not None:
            return burst

        if self.gear.enable_brake:
            burst = self._shift_burst()
            if burst is not None:
                return burst

        # Handbrake takes priority over normal braking
        if pkt.hand_brake > 0:
            return rigid(_clamp(s.handbrake_strength, 0, 8))

        brake = pkt.brake
        slip = pkt.four_wheel_slip
        losing = slip > s.grip_loss_value and brake > 100

        if losing and s.mode == TriggerMode.VIBRATION:
            freq = _map(slip, s.grip_loss_value, 5.0, s.min_vibration, s.max_vibration)
            self._l2_freq = _ewma(freq, self._l2_freq, s.vib_smoothing)
            f = _clamp(self._l2_freq * s.intensity)

            if f < s.min_vibration:
                res = _map(brake, 0, 255, s.min_resistance, s.max_resistance)
                self._l2_res_n = _ewma(res, self._l2_res_n, s.resistance_smoothing)
                strength = _clamp(self._l2_res_n * s.intensity, 0, 8)
                return feedback([strength] * 10) if strength > 0 else off()

            amp = max(1, min(8, round(_map(slip, s.grip_loss_value, 5.0, 1, 6))))
            wall = max(1, min(9, s.abs_wall_zones))
            return vibration_wall(int(amp * s.intensity), f, wall)

        # Normal braking: smooth feedback resistance
        res = _map(brake, 0, 255, s.min_resistance, s.max_resistance)
        self._l2_res_n = _ewma(res, self._l2_res_n, s.resistance_smoothing)
        strength = _clamp(self._l2_res_n * s.intensity, 0, 8)
        if strength > 0:
            return feedback([strength] * 10)

        if self.surface.enable_brake:
            effect = self._surface_effect(pkt)
            if effect is not None:
                return effect

        return off()
