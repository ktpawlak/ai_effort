#!/usr/bin/env python3
"""
sendkeys.py - Send keystrokes through the USB HID keyboard gadget (/dev/hidg0).

Runs ON the gadget board (e.g. the Raspberry Pi 4). The host drives it over SSH.

Examples:
  sudo ./sendkeys.py --type "hello world\n"      # type a string (\n = Enter)
  sudo ./sendkeys.py --key enter                 # press a single named key
  sudo ./sendkeys.py --combo ctrl-alt-delete     # press a key combo
  sudo ./sendkeys.py --wake                       # tap Left-Shift (wakes host, no char)
  sudo ./sendkeys.py --key esc --repeat 3 --delay 0.2

Report format (8 bytes): [modifiers, 0, k1, k2, k3, k4, k5, k6]
"""
import argparse
import sys
import time

DEV = "/dev/hidg0"

MOD = {
    "ctrl": 0x01, "lctrl": 0x01, "shift": 0x02, "lshift": 0x02,
    "alt": 0x04, "lalt": 0x04, "gui": 0x08, "win": 0x08, "lgui": 0x08, "meta": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "altgr": 0x40, "rgui": 0x80,
}

# Named keys -> HID usage id (Keyboard/Keypad page 0x07).
KEYS = {
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2a, "bksp": 0x2a, "tab": 0x2b, "space": 0x2c,
    "caps": 0x39, "capslock": 0x39,
    "right": 0x4f, "left": 0x50, "down": 0x51, "up": 0x52,
    "ins": 0x49, "insert": 0x49, "home": 0x4a, "pageup": 0x4b, "pgup": 0x4b,
    "del": 0x4c, "delete": 0x4c, "end": 0x4d, "pagedown": 0x4e, "pgdn": 0x4e,
    "printscreen": 0x46, "sysrq": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "menu": 0x65,
}
for i in range(1, 13):
    KEYS["f%d" % i] = 0x3a + (i - 1)

# Unshifted printable ASCII -> usage id.
_CHAR = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _CHAR[c] = 0x04 + i
for i, c in enumerate("1234567890"):
    _CHAR[c] = 0x1e + i
_CHAR.update({
    "\n": 0x28, "\t": 0x2b, " ": 0x2c,
    "-": 0x2d, "=": 0x2e, "[": 0x2f, "]": 0x30, "\\": 0x31,
    ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38,
})
# Shifted characters -> (usage id, needs shift).
_SHIFT = {
    "!": 0x1e, "@": 0x1f, "#": 0x20, "$": 0x21, "%": 0x22, "^": 0x23,
    "&": 0x24, "*": 0x25, "(": 0x26, ")": 0x27,
    "_": 0x2d, "+": 0x2e, "{": 0x2f, "}": 0x30, "|": 0x31,
    ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37, "?": 0x38,
}
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _SHIFT[c] = _CHAR[c.lower()]


def char_to_report(ch):
    """Return (modifier, usage_id) for a single character, or None."""
    if ch in _SHIFT:
        return (MOD["lshift"], _SHIFT[ch])
    if ch in _CHAR:
        return (0, _CHAR[ch])
    return None


def write_report(fh, mod=0, keys=()):
    report = bytearray(8)
    report[0] = mod & 0xFF
    for i, k in enumerate(keys[:6]):
        report[2 + i] = k & 0xFF
    try:
        fh.write(bytes(report))
        fh.flush()
    except (BrokenPipeError, OSError) as e:
        # ESHUTDOWN (108): the gadget is not enumerated by any host yet.
        raise SystemExit(
            "write failed (%s).\n"
            "The gadget is up but no USB host is attached to the gadget port.\n"
            "Connect the board's USB-C (gadget) port to the target and ensure the\n"
            "target has enumerated the keyboard, then try again." % e)


def release(fh):
    write_report(fh, 0, ())


def tap(fh, mod, usage, hold=0.01, gap=0.01):
    write_report(fh, mod, (usage,) if usage else ())
    time.sleep(hold)
    release(fh)
    time.sleep(gap)


def type_string(fh, s, delay):
    # Allow literal escape sequences in --type argument.
    s = s.replace("\\n", "\n").replace("\\t", "\t")
    for ch in s:
        r = char_to_report(ch)
        if r is None:
            sys.stderr.write("skip (unmapped): %r\n" % ch)
            continue
        mod, usage = r
        tap(fh, mod, usage, hold=0.012, gap=delay)


def do_combo(fh, spec, hold):
    """spec like 'ctrl-alt-delete' or 'ctrl-c'."""
    parts = spec.lower().split("-")
    mod = 0
    usage = 0
    for p in parts:
        if p in MOD:
            mod |= MOD[p]
        elif p in KEYS:
            usage = KEYS[p]
        elif len(p) == 1 and char_to_report(p):
            m, u = char_to_report(p)
            mod |= m
            usage = u
        else:
            raise SystemExit("unknown combo token: %r" % p)
    write_report(fh, mod, (usage,) if usage else ())
    time.sleep(hold)
    release(fh)


def main():
    ap = argparse.ArgumentParser(description="Send keystrokes via USB HID gadget")
    ap.add_argument("--dev", default=DEV, help="HID gadget device (default %s)" % DEV)
    ap.add_argument("--type", help="type a string (\\n=Enter, \\t=Tab)")
    ap.add_argument("--key", help="press a single named key (enter, esc, space, f2, up...)")
    ap.add_argument("--combo", help="press a combo, e.g. ctrl-alt-delete or ctrl-c")
    ap.add_argument("--wake", action="store_true",
                    help="tap Left-Shift to wake a suspended host (injects no character)")
    ap.add_argument("--repeat", type=int, default=1, help="repeat the action N times")
    ap.add_argument("--delay", type=float, default=0.02,
                    help="delay between keystrokes/repeats (s)")
    ap.add_argument("--hold", type=float, default=0.05, help="combo/key hold time (s)")
    args = ap.parse_args()

    if not any([args.type, args.key, args.combo, args.wake]):
        ap.error("nothing to do: use --type / --key / --combo / --wake")

    try:
        fh = open(args.dev, "wb", buffering=0)
    except PermissionError:
        raise SystemExit("permission denied opening %s (run with sudo)" % args.dev)
    except FileNotFoundError:
        raise SystemExit("%s not found (is the gadget 'up' and a host connected?)" % args.dev)

    with fh:
        for n in range(args.repeat):
            if args.wake:
                # Left-Shift tap: wakes host, does not type a character.
                tap(fh, MOD["lshift"], 0, hold=args.hold, gap=args.delay)
            if args.combo:
                do_combo(fh, args.combo, args.hold)
                time.sleep(args.delay)
            if args.key:
                name = args.key.lower()
                if name in KEYS:
                    tap(fh, 0, KEYS[name], hold=args.hold, gap=args.delay)
                elif len(name) == 1 and char_to_report(args.key):
                    m, u = char_to_report(args.key)
                    tap(fh, m, u, hold=args.hold, gap=args.delay)
                else:
                    raise SystemExit("unknown key: %r" % args.key)
            if args.type:
                type_string(fh, args.type, args.delay)
        # Always end released.
        release(fh)


if __name__ == "__main__":
    main()
