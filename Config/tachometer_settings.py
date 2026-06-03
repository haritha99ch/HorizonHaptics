from dataclasses import dataclass


@dataclass
class TachometerSettings:
    enable: bool = False
    start_percent: float = 0.7
    flash_percent: float = 0.93
    flash_rate_hz: float = 10.0
