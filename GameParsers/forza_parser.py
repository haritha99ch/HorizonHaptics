"""Forza Horizon 6 UDP packet parsing.

FH6 sends 324-byte packets. All offsets below are absolute byte positions
within the packet as documented in the official FH6 Data Out specification.
"""

import struct
from dataclasses import dataclass

FH6_PACKET_SIZE = 324


@dataclass
class DataPacket:
    """Parsed telemetry frame. Field names match the FH6 Data Out spec."""
    is_race_on: bool = False

    # Acceleration in car-local space (m/s^2)
    acceleration_x: float = 0.0  # lateral (right = positive)
    acceleration_z: float = 0.0  # forward

    # Per-wheel combined slip (0 = full grip, |slip| > 1 = grip loss)
    tire_combined_slip_fl: float = 0.0
    tire_combined_slip_fr: float = 0.0
    tire_combined_slip_rl: float = 0.0
    tire_combined_slip_rr: float = 0.0

    current_engine_rpm: float = 0.0
    engine_max_rpm: float = 0.0

    speed: float = 0.0  # m/s
    speed_kmh: float = 0.0

    accel: int = 0  # 0-255 (Accel byte @315)
    brake: int = 0  # 0-255
    hand_brake: int = 0  # 0 or non-zero
    gear: int = 0
    steer: int = 0  # -127 to 127

    boost: float = 0.0  # turbo boost pressure

    # Road surface (per-wheel), FH6 @148-160
    surface_rumble_fl: float = 0.0
    surface_rumble_fr: float = 0.0
    surface_rumble_rl: float = 0.0
    surface_rumble_rr: float = 0.0

    # Rumble strip detection (S32, non-zero = on strip), FH6 @116-128
    wheel_on_rumble_strip_fl: int = 0
    wheel_on_rumble_strip_fr: int = 0
    wheel_on_rumble_strip_rl: int = 0
    wheel_on_rumble_strip_rr: int = 0

    # Collision velocity delta (m/s), FH6-exclusive @236
    smashable_vel_diff: float = 0.0

    # Pre-computed slip aggregates (absolute values averaged)
    four_wheel_slip: float = 0.0
    front_slip: float = 0.0
    rear_slip: float = 0.0

    # Pre-computed surface aggregates
    surface_rumble: float = 0.0  # avg of 4 wheels
    on_rumble_strip: bool = False  # any wheel on strip


def parse(data: bytes) -> DataPacket:
    """Parse a 324-byte FH6 UDP packet into a DataPacket.

    Raises ValueError if the packet length is wrong.
    """
    if len(data) != FH6_PACKET_SIZE:
        raise ValueError(f"Expected {FH6_PACKET_SIZE} bytes, got {len(data)}")

    def f(o):
        return struct.unpack_from("<f", data, o)[0]

    def i32(o):
        return struct.unpack_from("<i", data, o)[0]

    fl = abs(f(180))
    fr = abs(f(184))
    rl = abs(f(188))
    rr = abs(f(192))

    srf_fl = f(148)
    srf_fr = f(152)
    srf_rl = f(156)
    srf_rr = f(160)

    rs_fl = i32(116)
    rs_fr = i32(120)
    rs_rl = i32(124)
    rs_rr = i32(128)

    return DataPacket(
        is_race_on=f(0) > 0,
        acceleration_x=f(20),
        acceleration_z=f(28),
        tire_combined_slip_fl=f(180),
        tire_combined_slip_fr=f(184),
        tire_combined_slip_rl=f(188),
        tire_combined_slip_rr=f(192),
        current_engine_rpm=f(16),
        engine_max_rpm=f(8),
        speed=f(256),
        speed_kmh=f(256) * 3.6,
        accel=data[315],
        brake=data[316],
        hand_brake=data[318],
        gear=data[319],
        steer=struct.unpack_from("<b", data, 320)[0],
        boost=f(284),
        surface_rumble_fl=srf_fl,
        surface_rumble_fr=srf_fr,
        surface_rumble_rl=srf_rl,
        surface_rumble_rr=srf_rr,
        wheel_on_rumble_strip_fl=rs_fl,
        wheel_on_rumble_strip_fr=rs_fr,
        wheel_on_rumble_strip_rl=rs_rl,
        wheel_on_rumble_strip_rr=rs_rr,
        smashable_vel_diff=f(236),
        four_wheel_slip=(fl + fr + rl + rr) / 4.0,
        front_slip=(fl + fr) / 2.0,
        rear_slip=(rl + rr) / 2.0,
        surface_rumble=(srf_fl + srf_fr + srf_rl + srf_rr) / 4.0,
        on_rumble_strip=(rs_fl != 0 or rs_fr != 0 or rs_rl != 0 or rs_rr != 0),
    )
