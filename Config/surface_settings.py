from dataclasses import dataclass


@dataclass
class SurfaceSettings:
    # Per-trigger enable for surface / rumble-strip feedback
    enable_throttle: bool = True
    enable_brake: bool = True

    # Road surface rumble (SurfaceRumble* fields, active on idle trigger)
    freq: int = 10
    amp: int = 80

    # Rumble strip (WheelOnRumbleStrip* fields)
    strip_freq: int = 25
    strip_amp: int = 150

    # Steam Input sends rumble to the left/right vibration motors independently
    # of trigger effects. When True, motor bytes are not written so Steam keeps
    # control. When False, motors are zeroed every frame, suppressing Steam rumble.
    allow_steam_rumble: bool = True

    # Collision jolt (SmashableVelDiff spike)
    enable_collision: bool = True
    collision_threshold: float = 5.0  # m/s velocity change to arm
    collision_freq: int = 40
    collision_amp: int = 255
    collision_duration_ms: float = 200.0
