# Hamoa Fan Control — Thermal Test Observation

Date: 2026-06-19
Board: Qualcomm Hamoa IoT EVK (x1e80100 / Snapdragon X Elite)
Kernel: 7.0.0-1006-qcom-rt #10ubuntu2 PREEMPT_RT (with hwmon EC patch 65d3334c805a)

---

## Setup

### hwmon driver (kernel patch 65d3334c805a)

`qcom-hamoa-ec.c` extended with a `hwmon` interface exposing:
- `/sys/class/hwmon/hwmon0/` → `qcom_ec` (I2C 1-0076)
- `pwm1` — fan 0 PWM set-point (0–255)
- `pwm2` — fan 1 PWM set-point (0–255)

Note: the EC does not expose an RPM read command; `pwmN` read returns the
cached last-written value.

### fancontrol config

`/etc/fancontrol` — see fancontrol.conf in this directory.

Sensor used: `cpuss0_top_thermal` (hwmon18, /sys/class/hwmon/hwmon18/temp1_input)
Curve:
  ≤45°C  → MINPWM = 64/255  (25%)
   75°C  → MAXPWM = 255/255 (100%)
  Linear interpolation between those points.

Enable / manage:
```bash
sudo systemctl {start|stop|status|enable|disable} fancontrol
```

---

## Stress test

### Command

```bash
# Run on the Hamoa board (all 12 CPUs, 120 seconds)
sudo stress-ng --cpu 12 --cpu-method matrixprod --timeout 120s
```

### Monitor while stressing

```bash
watch -n5 '
  TEMP=$(( $(cat /sys/class/hwmon/hwmon18/temp1_input) / 1000 ))
  PWM=$(cat /sys/class/hwmon/hwmon0/pwm1)
  PCT=$(( PWM * 100 / 255 ))
  echo "Temp: ${TEMP}°C   Fan0: ${PWM}/255 (${PCT}%)"
'
```

Or with `sensors` (read-only, shows current set-point percentage):
```bash
sensors | grep -A3 qcom_ec
```

---

## Observed results

fancontrol interval: 10s
Temperature sensor: cpuss0_top_thermal

| time  | temp  | fan0 PWM    | fan0 % |
|-------|-------|-------------|--------|
|   5s  | 31°C  |  64/255     |  25%   | ← idle baseline, below MINTEMP
|  10s  | 48°C  |  64/255     |  25%   | ← just above MINTEMP=45°C
|  20s  | 50°C  |  95/255     |  37%   | ← fancontrol starts ramping
|  30s  | 54°C  | 106/255     |  41%   |
|  40s  | 53°C  | 116/255     |  45%   |
|  50s  | 58°C  | 139/255     |  54%   |
|  60s  | 58°C  | 146/255     |  57%   |
|  75s  | 60°C  | 154/255     |  60%   |
|  90s  | 60°C  | 162/255     |  63%   |
| 110s  | 61°C  | 164/255     |  64%   | ← thermal equilibrium ~60°C

### Conclusion

The closed-loop fan control works correctly:
- Below 45°C fans hold at MINPWM (25%)
- Above 45°C fancontrol linearly scales PWM toward 255 at 75°C
- Under full 12-CPU matrix load the board stabilises at ~60–61°C
  with fans at ~64% — well within the 75°C trip point
- fancontrol respects the 10s INTERVAL; fan steps are visible every 10s

---

## Manual fan control (bypass fancontrol)

```bash
# Stop automatic control
sudo systemctl stop fancontrol

# Set by percentage: value = pct * 255 / 100
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm1   # 50%
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm2   # 50%

# Also works via thermal cooling device (same hardware)
echo 128 | sudo tee /sys/class/thermal/cooling_device3/cur_state

# Resume automatic control
sudo systemctl start fancontrol
```

---

## Notes

- `sensors` is **read-only** — it shows sensor values but cannot set them.
- The EC firmware manages fan RPM autonomously via an internal LUT when
  fancontrol is not active (debug mode off). The kernel driver puts the EC
  into "debug mode" (direct PWM override) when writing a non-zero value.
- `MINSTOP` must equal `MINPWM` because there is no RPM tachometer — fancontrol
  cannot detect when the fan actually stops, so the standard "stop-and-restart"
  hysteresis logic is effectively disabled.
- hwmon numbering (`hwmon0`, `hwmon18`) can change between kernels if new hwmon
  drivers are added. If fancontrol fails with "Device path changed", regenerate
  the DEVPATH lines:
    EC:   readlink -f /sys/class/hwmon/hwmon*/name → grep qcom_ec → note hwmonN
          readlink -f /sys/class/hwmon/hwmonN/device | sed 's|/sys/devices/||'
    Temp: same for cpuss0_top_thermal
  Update /etc/fancontrol DEVPATH accordingly.
