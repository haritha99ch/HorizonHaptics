# HorizonHaptics

DualSense adaptive trigger haptics for **Forza Horizon 6** on PC.  
Reads the game's UDP telemetry and translates live physics data into trigger resistance and vibration on a PlayStation 5 DualSense controller.

---

## Acknowledgements

Inspired by [Hamza Yesilmen's Forza Horizon DualSense project](https://github.com/HamzaYslmn/Forza-Horizon-DualSense-Python), which pioneered adaptive trigger support for Forza on PC. HorizonHaptics is a separate implementation with a different architecture and an expanded feature set targeted at Forza Horizon 6.

---

## Features

> Note: This project was developed independently over the same period. Some of these features may already exist or have since been added to the project that inspired it.

**FH6-exclusive telemetry (unique to this project):**

| Feature | Description |
|---|---|
| Collision jolt | Hard burst on both triggers when SmashableVelDiff spikes - FH6-only field |
| Road surface rumble | Per-wheel SurfaceRumble telemetry drives idle trigger feedback |
| Rumble strip detection | WheelOnRumbleStrip telemetry fires independently of in-game vibration setting |
| Boost | Extra R2 resistance when turbo boost telemetry is active |

**Original implementations:**

| Feature | Description |
|---|---|
| G-force resistance | R2 strength from acceleration vector (lateral + forward G), tunable independently |
| Wheelspin vibration | Frequency scales with combined slip, amplitude with G-force, EWMA smoothed |
| Trigger modes | Off / Resistance / Vibration selectable per trigger |
| EWMA smoothing | Separate smoothing state per effect path prevents mode transition bleed |
| Qt desktop GUI | Full settings UI with live tuning, no terminal required |
| First-run setup | Auto-installs udev rules on Linux, firewall guidance on Windows |
| Settings | Saved to disk automatically, take effect immediately without restart |

**Shared concepts (independent implementations):**

| Feature | Description |
|---|---|
| ABS simulation | Top N zones stay firm while lower zones pulse at lock-up frequency |
| Handbrake | L2 goes rigid while handbrake is active |
| Gear shift burst | Vibration burst on gear change, configurable per trigger |
| Steam rumble coexistence | Motor bytes left untouched so FH6 body rumble works alongside trigger effects |

---

## Requirements

- Forza Horizon 6 (PC)
- DualSense or DualSense Edge controller connected via **USB** (recommended) or Bluetooth  
  *(DualSense Edge support is untested  -  same HID protocol, should work)*
- Python 3.13+ - managed automatically by the launcher scripts

---

## Quick Start

### Linux
```bash
./run.sh
```
Double-click `run.sh` in your file manager and choose **Run**, or execute it in a terminal.

### Windows
Double-click `run.bat`.

Both scripts install `uv` (the package manager) if it is not already present, then install all dependencies and launch the app.

> An internet connection is required on the first run to download `uv` and the dependencies. Subsequent runs work offline.

---

## Forza Horizon 6 Setup

1. In-game: **Settings -> HUD and Gameplay -> Data Out -> ON**
2. Set **Data Out IP** to your PC's local IP address  
   *(shown on the Info tab inside HorizonHaptics)*
3. Set **Data Out Port** to `5300`
4. Start a race or free roam  -  the status bar will show **FH6: Receiving** when packets arrive

---

## How It Works

HorizonHaptics listens for 324-byte UDP telemetry packets from FH6 and maps physics values to DualSense trigger effects every frame. Effects are layered by priority:

```
Collision jolt  >  Gear shift burst  >  Handbrake / ABS / Wheelspin  >  Normal resistance  >  Surface rumble
```

---

## Effects

### R2  -  Throttle

| Situation | Effect |
|---|---|
| Normal driving | Feedback resistance, strength proportional to G-force |
| Boost active | Extra resistance added on top |
| Wheelspin / grip loss | Vibration  -  frequency scales with slip, amplitude with G-force |
| Gear change | Short vibration burst on both triggers |
| Collision | Hard vibration jolt on both triggers |
| Idle (no throttle) | Light surface rumble (road texture / rumble strips) |

**Modes**

- **Off**  -  trigger is always soft, no effects
- **Resistance** *(default)*  -  feedback resistance only; grip loss events add stronger resistance instead of vibration
- **Vibration**  -  full effect set including wheelspin vibration

---

### L2  -  Brake

| Situation | Effect |
|---|---|
| Normal braking | Progressive feedback resistance, builds with brake pressure |
| Handbrake | Firm rigid resistance for the full press travel |
| ABS / wheel lock-up | Top N zones stay firm while lower zones pulse at lock-up frequency |
| Gear change | Short vibration burst on both triggers |
| Collision | Hard vibration jolt on both triggers |
| Idle (not braking) | Light surface rumble (road texture / rumble strips) |

**Modes**  -  same as throttle (Off / Resistance / Vibration). Default: **Vibration**.

---

## Settings Reference

All settings take effect immediately and are saved automatically to  
`~/.config/horizonhaptics/settings.json`.

---

### R2  -  Throttle

#### General
| Setting | Default | Description |
|---|---|---|
| **Mode** | Resistance | Off / Resistance / Vibration  -  controls which effect set is active |
| **Intensity** | `0.7` | Global output multiplier for all R2 effects. Lower to soften everything. |
| **Grip loss threshold** | `0.6` | Combined slip value at which wheelspin mode activates. Lower = triggers earlier. |

#### Normal Driving  -  Resistance
| Setting | Default | Description |
|---|---|---|
| **Min strength** | `0` | Resistance at zero G-force (lightest feel). Range 0-8. |
| **Max strength** | `3` | Resistance at G-force ceiling (heaviest feel). Range 0-8. |
| **Smoothing** | `0.9` | EWMA smoothing for resistance transitions. `0` = instant, `1` = no change. |
| **Lateral G scale** | `0.25` | Weight of sideways acceleration in the G-force calculation. |
| **Forward G scale** | `1.0` | Weight of forward acceleration in the G-force calculation. |
| **G-force ceiling** | `10.0` | G-force value that maps to Max strength. Above this, resistance is capped. |

#### Grip Loss  -  Vibration
| Setting | Default | Description |
|---|---|---|
| **Min freq (fallback)** | `5` | If computed frequency falls below this, effect falls back to rigid resistance. |
| **Max freq** | `55` | Vibration frequency at maximum wheel slip. |
| **Freq smoothing** | `1.0` | EWMA smoothing for vibration frequency changes. |
| **Amp at 0 G (inverted)** | `255` | Vibration amplitude at zero G during wheelspin. Inverted scale  -  higher = softer. |
| **Amp at max G (inverted)** | `175` | Vibration amplitude at G-force ceiling during wheelspin. |
| **Min throttle to enter vib** | `5` | Throttle input (0-255) that must be exceeded before vibration mode activates. Prevents triggering on coasting oversteer. |

#### Boost
| Setting | Default | Description |
|---|---|---|
| **Extra resistance while boosting** | `0.25` | Additional resistance added to R2 whenever turbo boost is active. |

---

### L2  -  Brake

#### General
| Setting | Default | Description |
|---|---|---|
| **Mode** | Vibration | Off / Resistance / Vibration |
| **Intensity** | `0.7` | Global output multiplier for all L2 effects. |
| **Grip loss threshold** | `0.05` | Slip value at which ABS/lock-up vibration activates. Very sensitive by default. |

#### Normal Braking  -  Resistance
| Setting | Default | Description |
|---|---|---|
| **Min strength** | `0` | Resistance at zero brake input. |
| **Max strength** | `7` | Resistance at full brake (255). Range 0-8. |
| **Smoothing** | `0.4` | EWMA smoothing for brake resistance transitions. |

#### Handbrake
| Setting | Default | Description |
|---|---|---|
| **Resistance level** | `8` | Firm resistance strength when handbrake is active. Range 0-8. |

#### ABS / Grip Loss  -  Vibration
| Setting | Default | Description |
|---|---|---|
| **Wall zones during ABS** | `3` | Top N trigger zones held firm as a resistance wall while lower zones pulse. Mimics GT7-style ABS feel. Range 1-9. |
| **Min pulse freq** | `10` | Vibration frequency at light wheel lock-up. |
| **Max pulse freq** | `40` | Vibration frequency at heavy wheel lock-up. |
| **Freq smoothing** | `0.8` | EWMA smoothing for ABS pulse frequency. |

---

### Gear Shift

#### Enable
| Setting | Default | Description |
|---|---|---|
| **R2  -  Throttle burst** | `On` | Enable/disable gear shift burst on the throttle trigger. |
| **L2  -  Brake burst** | `On` | Enable/disable gear shift burst on the brake trigger. |

#### Gear Shift  -  Trigger Burst
| Setting | Default | Description |
|---|---|---|
| **Frequency** | `20` | Vibration frequency of the shift burst. |
| **Amplitude** | `100` | Vibration strength of the shift burst. |
| **Duration (ms)** | `60` | How long the burst lasts in milliseconds. |

---

### Surface & Effects

#### Enable
| Setting | Default | Description |
|---|---|---|
| **R2 - Throttle surface rumble** | `On` | Enable road texture / rumble strip feedback on the idle throttle trigger. |
| **L2 - Brake surface rumble** | `On` | Enable road texture / rumble strip feedback on the idle brake trigger. |
| **Collision jolt** | `On` | Enable the hard jolt effect on sudden impacts. |
| **Allow Steam rumble** | `On` | When on, HorizonHaptics does not touch the controller's vibration motors, letting Steam and FH6 deliver body rumble independently alongside trigger effects. When off, motor bytes are zeroed on every write, silencing any Steam rumble. Recommended: leave on. |

#### Road Surface Rumble
Light continuous vibration on the idle trigger driven by the game's per-wheel surface data.

| Setting | Default | Description |
|---|---|---|
| **Frequency** | `10` | Vibration frequency for normal road texture. |
| **Amplitude** | `80` | Vibration strength for normal road texture. |

#### Rumble Strip
Stronger vibration when any wheel crosses a rumble strip.

| Setting | Default | Description |
|---|---|---|
| **Frequency** | `25` | Vibration frequency on rumble strips. |
| **Amplitude** | `150` | Vibration strength on rumble strips. |

#### Collision Jolt
A sharp burst on both triggers when the car takes a hard impact.

| Setting | Default | Description |
|---|---|---|
| **Vel diff threshold (m/s)** | `5.0` | Minimum velocity change in a single frame to arm the jolt. Increase to ignore light taps. |
| **Frequency** | `40` | Vibration frequency of the jolt. |
| **Amplitude** | `255` | Vibration strength of the jolt. |
| **Duration (ms)** | `200` | How long the jolt lasts. |

---

## Info Tab

Shows your machine's local IP addresses and the listening port, formatted ready to paste into Forza's Data Out settings. Also displays live connection status for the DualSense and the game.

---

## Troubleshooting

### First-run setup

#### Linux - DualSense not detected

Linux blocks non-root access to HID devices by default. `run.sh` will prompt you to install the udev rules automatically on first launch. If you skipped it or ran the app another way:

```bash
sudo cp packaging/linux/70-dualsense.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug the controller (or re-pair over Bluetooth). Covers DualSense (`054c:0ce6`) and DualSense Edge (`054c:0df2`).

#### Windows - FH6 packets not arriving

Windows Firewall will show a security alert the first time the app opens UDP port 5300. Click **Allow access**. If you previously clicked Cancel or Block:

1. Run `packaging\windows\add-firewall-rule.ps1` (right-click -> Run with PowerShell, a UAC prompt will appear)
2. Or open Windows Defender Firewall -> Advanced Settings -> Inbound Rules -> New Rule -> UDP port 5300 -> Allow

#### Windows - DualSense not detected

If the controller shows as connected but the app still shows **DualSense: Waiting**, another app is claiming the HID device:

- Close **DS4Windows**, **DualSenseX**, or **Steam** (Big Picture mode) then replug the controller
- If it still fails, right-click `run.bat` -> **Run as administrator**

---

### In-app issues

| Problem | Fix |
|---|---|
| **DualSense: Waiting** | Connect via USB. On Linux, install the udev rules (see above). On Windows, close DS4Windows / DualSenseX if running. |
| **FH6: Waiting for packets** | Confirm Data Out is enabled in-game, the IP matches one shown on the Info tab, and the port is `5300`. Check your firewall allows inbound UDP on port 5300. |
| **No effects despite receiving** | Check that the trigger mode is not set to **Off** and that intensity is above `0`. |

---

## Resources

- **[Forza Horizon 6 Data Out Documentation](https://support.forza.net/hc/en-us/articles/51744149102611-Forza-Horizon-6-Data-Out-Documentation)**  -  Official Playground Games / Microsoft specification for the FH6 UDP telemetry packet format. Field offsets, types, and units used directly in this project. Note: FH6 includes three fields not in Forza Motorsport  -  `CarGroup`, `SmashableVelDiff`, and `SmashableMass`.

- **[DualSense HID Adaptive Trigger Protocol](https://github.com/nondebug/dualsense)**  -  Community reverse-engineered documentation of the DualSense USB/Bluetooth HID output report format, including the adaptive trigger effect encoding used to drive resistance and vibration.

- **[hidapi](https://github.com/libusb/hidapi)**  -  Cross-platform C library for communicating with HID devices. Used here via the Python `hidapi` binding to write trigger effect reports directly to the DualSense.

- **[uv  -  Python Package Manager](https://docs.astral.sh/uv/)**  -  The fast Python package and project manager used to manage dependencies and run the app.
