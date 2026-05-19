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
    ):
        self.throttle = throttle or ThrottleSettings()
        self.brake = brake or BrakeSettings()
        self.gear = gear or GearSettings()
        self.surface = surface or SurfaceSettings()
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

    def compute(self, pkt: DataPacket) -> tuple:
        """Return (L2_frame, R2_frame) for one telemetry tick."""
        self._arm_shift(pkt)
        self._arm_collision(pkt)
        return self._l2(pkt), self._r2(pkt)

    # -- Collision jolt --

    def _arm_collision(self, pkt: DataPacket):
        s = self.surface
        if (s.enable_collision
                and pkt.smashable_vel_diff > s.collision_threshold):
            self._collision_until = time.monotonic() + s.collision_duration_ms / 1000.0

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
