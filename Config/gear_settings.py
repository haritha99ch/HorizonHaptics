from dataclasses import dataclass


@dataclass
class GearSettings:
    enable_throttle: bool = True  # gear burst on R2
    enable_brake: bool = True  # gear burst on L2
    freq: int = 20  # vibration frequency during burst
    amp: int = 100  # vibration amplitude during burst
    duration_ms: float = 60.0  # how long the burst lasts (ms)
