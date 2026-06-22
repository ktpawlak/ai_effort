#!/usr/bin/env python3
"""
ec_probe.py - Interactive Qualcomm Hamoa EC (I2C 0x76) register probe tool.

Temporarily unbinds the qcom-hamoa-ec kernel driver, reads a range of I2C
register addresses with configurable lengths, prints raw bytes, and tries to
decode known fields. Re-binds the driver on exit.

Usage:
    sudo python3 ec_probe.py [options]

Options:
    --bus     I2C bus number     (default: 1)
    --addr    EC I2C address     (default: 0x76)
    --start   First register     (default: 0x00)
    --end     Last register      (default: 0x5f)
    --len     Bytes to read per register (default: 8)
    --only    Comma-separated list of hex registers to probe (e.g. 0x30,0x42)
    --no-unbind  Skip driver unbind/rebind (if driver is already unbound)
    --raw     Print only raw hex, no decoding
    --repeat  N  Re-read --only registers N times with 1s delay (live monitor)

Examples:
    sudo python3 ec_probe.py                          # full scan 0x00-0x5f
    sudo python3 ec_probe.py --only 0x0e,0x30,0x42   # known commands only
    sudo python3 ec_probe.py --only 0x3e,0x3f --repeat 10  # live monitor

Known commands (from driver source + probing):
    0x05  EC_SCI_EVT_READ_CMD      r 1   - last SCI event code
    0x0e  EC_FW_VERSION_CMD        r 4   - [len, test, sub, main]
    0x30  EC_FAN_DBG_CONTROL_CMD   w 6   - fan PWM/RPM set (write only)
    0x35  EC_SCI_EVT_CONTROL_CMD   w 1   - enable/disable SCI events
    0x42  EC_THERMAL_CAP_CMD       r 3   - [len, fan_info, thermistor_mask]
"""

import argparse
import os
import struct
import sys
import time
import smbus

# ---------------------------------------------------------------------------
# Known register definitions
# ---------------------------------------------------------------------------

KNOWN = {
    0x05: ("EC_SCI_EVT_READ",    1,  "last SCI event byte"),
    0x0e: ("EC_FW_VERSION",      4,  "[bytecount, test_ver, sub_ver, main_ver]"),
    0x30: ("EC_FAN_DBG_CTRL",    8,  "write-only; read may echo last cmd"),
    0x35: ("EC_SCI_EVT_CTRL",    1,  "SCI event control byte"),
    0x42: ("EC_THERMAL_CAP",     3,  "[bytecount, fan_info, thermistor_mask]"),
}

SCI_EVENTS = {
    0x30: "FAN1_STATUS_CHANGE",
    0x31: "FAN2_STATUS_CHANGE",
    0x32: "FAN1_SPEED_CHANGE",
    0x33: "FAN2_SPEED_CHANGE",
    0x34: "NEW_LUT_SET",
    0x35: "FAN_PROFILE_SWITCH",
    0x36: "THERMISTOR_1_THRESHOLD",
    0x37: "THERMISTOR_2_THRESHOLD",
    0x38: "THERMISTOR_3_THRESHOLD",
    0x3d: "EC_RECOVERED_FROM_RESET",
}

DRIVER_NAME = "qcom-hamoa-ec"
DRIVER_PATH = f"/sys/bus/i2c/drivers/{DRIVER_NAME}"

# ---------------------------------------------------------------------------
# Driver bind/unbind helpers
# ---------------------------------------------------------------------------

def find_bound_device(driver_path):
    """Return the device name bound to this driver (e.g. '1-0076'), or None."""
    try:
        for entry in os.listdir(driver_path):
            if "-" in entry and not entry.startswith("."):
                return entry
    except FileNotFoundError:
        pass
    return None


def unbind_driver(driver_path, device):
    unbind = os.path.join(driver_path, "unbind")
    with open(unbind, "w") as f:
        f.write(device)
    time.sleep(0.3)
    print(f"[+] Unbound {device} from {DRIVER_NAME}")


def bind_driver(driver_path, device):
    bind = os.path.join(driver_path, "bind")
    with open(bind, "w") as f:
        f.write(device)
    time.sleep(0.3)
    print(f"[+] Re-bound {device} to {DRIVER_NAME}")


# ---------------------------------------------------------------------------
# I2C read helper
# ---------------------------------------------------------------------------

def read_reg(bus, addr, reg, length):
    """Read `length` bytes from `reg` on `addr`. Returns list or None on error."""
    try:
        return bus.read_i2c_block_data(addr, reg, length)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Decoders for known registers
# ---------------------------------------------------------------------------

def decode(reg, data):
    """Return a human-readable interpretation string, or empty string."""
    if data is None:
        return "(read error)"

    lines = []

    if reg == 0x0e:  # FW_VERSION
        if len(data) >= 4:
            lines.append(f"FW version: {data[3]}.{data[2]}.{data[1]} "
                         f"(main.sub.test, bytecount={data[0]})")

    elif reg == 0x42:  # THERMAL_CAP
        if len(data) >= 3:
            fan_cnt  = data[1] & 0x03
            fan_type = (data[1] >> 2) & 0x07
            valid    = (data[1] >> 7) & 0x01
            tmask    = data[2]
            therm_present = [i for i in range(8) if tmask & (1 << i)]
            lines.append(f"fans={fan_cnt}, type={fan_type}, valid={valid}, "
                         f"bytecount={data[0]}")
            lines.append(f"thermistors present: {therm_present} (mask=0x{tmask:02x})")

    elif reg == 0x05:  # SCI_EVT_READ
        if data:
            ev = data[0]
            name = SCI_EVENTS.get(ev, "unknown")
            lines.append(f"last event: 0x{ev:02x} ({name})")

    else:
        # Generic heuristics — try to spot plausible temperatures (0-100) and
        # 16-bit values that could be RPM (100-8000).
        hints = []
        for i, v in enumerate(data):
            if 0 < v <= 100:
                hints.append(f"byte[{i}]=0x{v:02x}/{v} (plausible °C or %)")
        if len(data) >= 2:
            for i in range(len(data) - 1):
                le = struct.unpack_from("<H", bytes(data[i:i+2]))[0]
                be = struct.unpack_from(">H", bytes(data[i:i+2]))[0]
                if 100 <= le <= 8000:
                    hints.append(f"bytes[{i}:{i+2}] LE={le} (plausible RPM?)")
                if le != be and 100 <= be <= 8000:
                    hints.append(f"bytes[{i}:{i+2}] BE={be} (plausible RPM?)")
        if hints:
            lines.extend(hints)

    return "\n      ".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def hex_row(data):
    if data is None:
        return "(error)"
    return " ".join(f"0x{b:02x}" for b in data) + \
           f"  |  {' '.join(chr(b) if 32 <= b < 127 else '.' for b in data)}"


def print_result(reg, data, label, desc, interpretation):
    reg_str  = f"0x{reg:02x}"
    name_str = f"{label:<22}" if label else f"{'?':<22}"
    raw_str  = hex_row(data)
    print(f"  {reg_str}  {name_str}  {raw_str}")
    if desc:
        print(f"      ↳ {desc}")
    if interpretation:
        print(f"      ↳ {interpretation}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Qualcomm Hamoa EC I2C register probe tool")
    p.add_argument("--bus",       type=lambda x: int(x, 0), default=1)
    p.add_argument("--addr",      type=lambda x: int(x, 0), default=0x76)
    p.add_argument("--start",     type=lambda x: int(x, 0), default=0x00)
    p.add_argument("--end",       type=lambda x: int(x, 0), default=0x5f)
    p.add_argument("--len",       type=int, default=8,
                   help="bytes to read per register")
    p.add_argument("--only",      type=str, default=None,
                   help="comma-separated list of hex registers, e.g. 0x0e,0x42")
    p.add_argument("--no-unbind", action="store_true",
                   help="skip driver unbind/rebind")
    p.add_argument("--raw",       action="store_true",
                   help="print only raw hex, no decoding")
    p.add_argument("--repeat",    type=int, default=1,
                   help="repeat --only probes N times (live monitor mode)")
    return p.parse_args()


def main():
    args = parse_args()

    if os.geteuid() != 0:
        sys.exit("error: must run as root (sudo)")

    # Build register list
    if args.only:
        regs = [int(r, 0) for r in args.only.split(",")]
    else:
        regs = list(range(args.start, args.end + 1))

    # Unbind driver
    bound_dev = None
    if not args.no_unbind:
        bound_dev = find_bound_device(DRIVER_PATH)
        if bound_dev:
            unbind_driver(DRIVER_PATH, bound_dev)
        else:
            print(f"[i] {DRIVER_NAME} not currently bound (or driver not loaded)")

    try:
        bus = smbus.SMBus(args.bus)

        for iteration in range(args.repeat):
            if args.repeat > 1:
                print(f"\n{'='*60}")
                print(f"  Iteration {iteration + 1}/{args.repeat}  "
                      f"({time.strftime('%H:%M:%S')})")
                print(f"{'='*60}")

            print(f"\n  {'reg':<6}  {'name':<22}  {'raw bytes (hex)':<42}  "
                  f"{'ascii'}")
            print("  " + "-" * 90)

            for reg in regs:
                length = KNOWN.get(reg, (None, args.len))[1]
                # respect --len override for unknown regs
                if reg not in KNOWN:
                    length = args.len

                data = read_reg(bus, args.addr, reg, length)

                # Skip all-zero / all-ff / None for full scans (reduce noise)
                if args.only is None and data is not None:
                    if all(b == 0x00 for b in data):
                        continue
                    if all(b == 0xff for b in data):
                        continue

                label, _, desc = KNOWN.get(reg, ("", args.len, ""))
                interp = "" if args.raw else decode(reg, data)
                print_result(reg, data, label, desc if not args.raw else "",
                             interp)

            if iteration < args.repeat - 1:
                time.sleep(1)

        bus.close()

    finally:
        if bound_dev and not args.no_unbind:
            bind_driver(DRIVER_PATH, bound_dev)


if __name__ == "__main__":
    main()
