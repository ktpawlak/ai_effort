# Hamoa Fan Control

Board: Qualcomm Hamoa IoT EVK (x1e80100 / Snapdragon X Elite)
Date: 2026-06-19

---

## Hardware

The board has **two fans** driven by a **Qualcomm Embedded Controller**:

- Driver: `qcom-hamoa-ec`
- Bus: I2C device `1-0076`
- Kernel interface: Linux thermal **cooling devices**

| Device | Sysfs path | Range |
|--------|-----------|-------|
| fan 0 | `/sys/class/thermal/cooling_device3/cur_state` | 0–255 |
| fan 1 | `/sys/class/thermal/cooling_device4/cur_state` | 0–255 |

There is no tachometer (RPM) — only the set-point is readable back.
The thermal governor may override `cur_state` automatically based on trip points.

---

## Control

### Via thermal cooling device (always available)

```bash
# Read current speed
cat /sys/class/thermal/cooling_device3/cur_state   # fan 0
cat /sys/class/thermal/cooling_device4/cur_state   # fan 1

# Set by percentage (multiply % by 2.55, round to int)
#   0%  =   0
#  25%  =  64
#  30%  =  76
#  50%  = 128
#  75%  = 191
# 100%  = 255
echo 64  | sudo tee /sys/class/thermal/cooling_device3/cur_state   # fan 0 @ 25%
echo 128 | sudo tee /sys/class/thermal/cooling_device3/cur_state   # fan 0 @ 50%
echo 255 | sudo tee /sys/class/thermal/cooling_device3/cur_state   # fan 0 @ 100%
echo 0   | sudo tee /sys/class/thermal/cooling_device3/cur_state   # fan 0 off

# Control both fans at once
SPEED=128
echo $SPEED | sudo tee /sys/class/thermal/cooling_device3/cur_state
echo $SPEED | sudo tee /sys/class/thermal/cooling_device4/cur_state
```

### Via hwmon (standard interface, kernel >= 7.0.0-1006.10ubuntu2)

The driver now also registers a `qcom_ec` hwmon device, exposing fans as
`pwm1` and `pwm2` under `/sys/class/hwmon/hwmon0/`. Both interfaces write
to the same EC hardware and stay in sync.

```bash
# Find the hwmon path
grep -rl "^qcom_ec$" /sys/class/hwmon/hwmon*/name | sed "s|/name||"
# → /sys/class/hwmon/hwmon0

# Read / write pwm (0-255)
cat /sys/class/hwmon/hwmon0/pwm1
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm1   # fan 0 @ 50%
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm2   # fan 1 @ 50%

# Works with standard tools:
sensors                                             # read all hwmon sensors
# (fan RPM is not available — EC does not expose a read command for it)
```

**Note on RPM readback:** The hwmon `pwmN` read returns the **cached
last-written value**, not a hardware measurement. The EC firmware has no
I2C read command for current fan RPM (confirmed by exhaustive register
probing and review of upstream driver series V3-V9). RPM tracking is done
internally by the EC firmware using its own LUT.

### Quick helper (bash function)

```bash
set_fan() {
    local pct=$1      # 0–100
    local speed=$(( pct * 255 / 100 ))
    echo $speed | sudo tee /sys/class/thermal/cooling_device3/cur_state > /dev/null
    echo $speed | sudo tee /sys/class/thermal/cooling_device4/cur_state > /dev/null
    echo "fans set to ${pct}% (${speed}/255)"
}

# Usage:
set_fan 0    # off
set_fan 25   # 25%
set_fan 100  # full
```

---

## Discovery notes

- No `fan*_input` entries in hwmon → no RPM readback.
- `pwmchip0` / `pwmchip1` present (PMIC PWM) but these are for the display
  backlight, not the fans.
- `cooling_device5..7` are PCIe link speed and GPU devfreq, not fans.
- The thermal framework maps the fans to trip points on the SoC thermal zones.
  Writing `cur_state` manually overrides the current value but the governor
  will resume control on the next thermal event.
