"""Save/load settings to ~/.config/horizonhaptics/settings.json. v1.2.2"""

import dataclasses
import json
import logging
from pathlib import Path

from Config import BrakeSettings, GearSettings, SurfaceSettings, ThrottleSettings, TriggerMode

log = logging.getLogger("hh.prefs")
PREFS_FILE = Path.home() / ".config" / "horizonhaptics" / "settings.json"


def save(
    throttle: ThrottleSettings,
    brake: BrakeSettings,
    gear: GearSettings,
    surface: SurfaceSettings,
) -> None:
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "throttle": {**dataclasses.asdict(throttle), "mode": int(throttle.mode)},
        "brake":    {**dataclasses.asdict(brake),    "mode": int(brake.mode)},
        "gear":     dataclasses.asdict(gear),
        "surface":  dataclasses.asdict(surface),
    }
    PREFS_FILE.write_text(json.dumps(data, indent=2))
    log.debug("Settings saved to %s", PREFS_FILE)


def load(
    throttle: ThrottleSettings,
    brake: BrakeSettings,
    gear: GearSettings,
    surface: SurfaceSettings,
) -> None:
    if not PREFS_FILE.exists():
        return
    try:
        data = json.loads(PREFS_FILE.read_text())
        _apply(throttle, data.get("throttle", {}), ThrottleSettings)
        _apply(brake,    data.get("brake", {}),    BrakeSettings)
        _apply(gear,     data.get("gear", {}),     GearSettings)
        _apply(surface,  data.get("surface", {}),  SurfaceSettings)
        log.debug("Settings loaded from %s", PREFS_FILE)
    except Exception as exc:
        log.warning("Could not load settings: %s", exc)


def reset(
    throttle: ThrottleSettings,
    brake: BrakeSettings,
    gear: GearSettings,
    surface: SurfaceSettings,
) -> None:
    for obj, cls in [
        (throttle, ThrottleSettings),
        (brake, BrakeSettings),
        (gear, GearSettings),
        (surface, SurfaceSettings),
    ]:
        defaults = cls()
        for f in dataclasses.fields(obj):
            setattr(obj, f.name, getattr(defaults, f.name))
    save(throttle, brake, gear, surface)
    log.info("Settings reset to defaults.")


def _apply(obj, d: dict, cls) -> None:
    defaults = cls()
    for key, val in d.items():
        if not hasattr(obj, key):
            continue
        default = getattr(defaults, key)
        try:
            if key == "mode":
                setattr(obj, key, TriggerMode(int(val)))
            elif isinstance(default, bool):
                setattr(obj, key, bool(val))
            elif isinstance(default, float):
                setattr(obj, key, float(val))
            elif isinstance(default, int):
                setattr(obj, key, int(val))
            else:
                setattr(obj, key, val)
        except (TypeError, ValueError):
            pass
