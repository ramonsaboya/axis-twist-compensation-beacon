# Axis Twist Compensation Beacon

Automates **axis twist compensation calibration** for [Klipper](https://www.klipper3d.org/) printers using [Beacon](https://beacon3d.com/) probe's **contact (touch)** and **proximity (scan)** modes.

This eliminates the tedious manual paper-test nozzle probing normally required by Klipper's built-in `AXIS_TWIST_COMPENSATION_CALIBRATE` command, making the entire calibration fully automated and hands-free.

## What It Does

When your X or Y rail has a slight twist, the probe reads different Z values at different positions along the axis — even when the bed is perfectly flat. Klipper's `[axis_twist_compensation]` corrects for this, but calibrating it normally requires you to manually jog the nozzle with a paper test at each calibration point.

This module replaces that manual step with **Beacon Contact (touch) probing**, which probes directly at the nozzle with zero offset. At each calibration point, it:

1. **Proximity (scan) probes** → gets the induction Z reading at that position
2. **Contact (touch) probes** → gets the true nozzle Z reading at that position
3. **Records the difference** → this is the twist-induced error

The resulting compensation values are saved to `[axis_twist_compensation]` just like the stock calibration.

## Prerequisites

- **Beacon probe** with both scan (proximity) and contact (touch) calibrated
- **Klipper** with `[axis_twist_compensation]` section configured
- `position_min: -2` (or lower) in your `[stepper_z]` config — required for Beacon contact probing
- Standard Beacon Contact safety precautions (clean nozzle, metallic bed target)

## Installation

### Automatic

```bash
cd ~
git clone https://github.com/ramonsaboya/axis-twist-compensation-beacon.git
cd axis-twist-compensation-beacon
./install.sh
```

### Manual

Copy `axis_twist_compensation_beacon.py` into your Klipper extras directory:

```bash
cp axis_twist_compensation_beacon.py ~/klipper/klippy/extras/
```

Then restart Klipper.

### Moonraker Update Manager

Add the following to your `moonraker.conf` to get automatic updates via Fluidd/Mainsail:

```ini
[update_manager axis-twist-compensation-beacon]
type: git_repo
path: ~/axis-twist-compensation-beacon
origin: https://github.com/ramonsaboya/axis-twist-compensation-beacon.git
primary_branch: main
managed_services: klipper
```

Then restart Moonraker.

## Configuration

Add these sections to your `printer.cfg`:

```ini
# Required: Define the calibration boundaries
[axis_twist_compensation]
calibrate_start_x: 20
calibrate_end_x: 200
calibrate_y: 112.5
# For Y axis calibration, also add:
# calibrate_start_y: 20
# calibrate_end_y: 200
# calibrate_x: 112.5

# Required: Enable this module (can be empty)
[axis_twist_compensation_beacon]
```

### Optional Configuration

```ini
[axis_twist_compensation_beacon]
speed: 50                  # Travel speed between points (mm/s)
horizontal_move_z: 5       # Safe Z clearance height (mm)
probe_speed: 3             # Contact probing speed (mm/s)
contact_samples: 3         # Number of contact samples per point
retract_dist: 2            # Retract distance between contact samples (mm)
```

## Usage

### Basic Usage

```gcode
; Home all axes first
G28

; Run twist compensation calibration
AXIS_TWIST_COMPENSATION_BEACON

; Save results to printer.cfg
SAVE_CONFIG
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `AXIS` | `X` | Axis to calibrate (`X` or `Y`) |
| `SAMPLE_COUNT` | `3` | Number of points along the axis (min 2) |
| `SPEED` | `50` | Travel speed between points (mm/s) |
| `HORIZONTAL_MOVE_Z` | `5` | Safe Z clearance height (mm) |
| `PROBE_SPEED` | `3` | Contact probing speed (mm/s) |
| `CONTACT_SAMPLES` | `3` | Contact samples per point |
| `RETRACT_DIST` | `2` | Retract distance between samples (mm) |

### Examples

```gcode
; X axis with 5 sample points
AXIS_TWIST_COMPENSATION_BEACON AXIS=X SAMPLE_COUNT=5

; Y axis calibration
AXIS_TWIST_COMPENSATION_BEACON AXIS=Y SAMPLE_COUNT=3

; Custom probe speed
AXIS_TWIST_COMPENSATION_BEACON SAMPLE_COUNT=7 PROBE_SPEED=2
```

### In Print Start Macro

If your axis twist is temperature-dependent, you can run this as part of your print start macro. Run it **after** bed leveling (QGL/Z_TILT) and **before** `BED_MESH_CALIBRATE`:

```gcode
[gcode_macro PRINT_START]
gcode:
    G28
    QUAD_GANTRY_LEVEL         ; or Z_TILT_ADJUST
    AXIS_TWIST_COMPENSATION_BEACON AXIS=X SAMPLE_COUNT=5
    BED_MESH_CALIBRATE
    ; ... rest of start macro
```

## How It Works

```
AXIS_TWIST_COMPENSATION_BEACON AXIS=X SAMPLE_COUNT=5
  │
  ├─ Read calibrate_start_x, calibrate_end_x, calibrate_y
  │  from [axis_twist_compensation] config
  │
  ├─ Calculate 5 evenly-spaced points along X axis
  │
  ├─ For each point:
  │   ├─ Move to point at safe Z height
  │   ├─ Beacon Proximity Probe → scan_z
  │   ├─ Beacon Contact Probe → nozzle_z
  │   └─ twist_error = scan_z - nozzle_z
  │
  ├─ Normalize results (subtract mean)
  │
  └─ Save z_compensations to [axis_twist_compensation]
     └─ Run SAVE_CONFIG to persist
```

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE).
