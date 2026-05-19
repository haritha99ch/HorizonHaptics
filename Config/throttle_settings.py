from dataclasses import dataclass
from .trigger_mode import TriggerMode


@dataclass
class ThrottleSettings:
    mode: TriggerMode = TriggerMode.RESISTANCE
    intensity: float = 0.7
    grip_loss_value: float = 0.6
    turn_accel_scale: float = 0.25
    fwd_accel_scale: float = 1.0
    accel_limit: float = 10.0
    vib_mode_start: int = 5  # accelerator must exceed this to enter vib mode
    min_vibration: int = 5  # freq below this -> fall back to resistance
    max_vibration: int = 55
    vib_smoothing: float = 1.0
    min_stiffness: int = 255  # amp at avg_accel=0 during wheelspin (inverted)
    max_stiffness: int = 175  # amp at avg_accel=AccelLimit during wheelspin
    min_resistance: int = 0  # feedback strength at avg_accel=0
    max_resistance: int = 3  # feedback strength at avg_accel=AccelLimit
    resistance_smoothing: float = 0.9
    boost_resistance: float = 0.25  # extra resistance added while boost > 0
