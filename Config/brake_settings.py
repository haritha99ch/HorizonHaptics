from dataclasses import dataclass
from .trigger_mode import TriggerMode


@dataclass
class BrakeSettings:
    mode: TriggerMode = TriggerMode.VIBRATION
    intensity: float = 0.7
    grip_loss_value: float = 0.05

    # Normal braking - progressive feedback resistance (0-8 per zone)
    min_resistance: int = 0  # strength at brake=0
    max_resistance: int = 7  # strength at brake=255
    resistance_smoothing: float = 0.4

    # Handbrake - firm resistance when HandBrake byte is non-zero
    handbrake_strength: int = 8  # resistance level (0-8)

    # ABS / traction loss - vibration_wall overlay
    # Lower trigger zones pulse at ABS frequency; top abs_wall_zones stay firm.
    abs_wall_zones: int = 3  # top N zones held firm during ABS (1-9)
    min_vibration: int = 10  # pulse freq at light slip
    max_vibration: int = 40  # pulse freq at heavy slip
    vib_smoothing: float = 0.8
