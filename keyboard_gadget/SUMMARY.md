# USB HID Keyboard Gadget — Work Summary

Date: 2026-06-18
Goal: build a test harness so the workstation can remotely inject keystrokes into
a device under test (DUT) — primarily to wake the Qualcomm Hamoa board from
suspend, but also usable for any USB-keyboard interaction (bootloader/console).

---

## Key finding: the workstation cannot be the keyboard

A USB **gadget** (acting as a USB *device*) needs *peripheral-capable* controller
hardware — a UDC (USB Device Controller).

Investigation of the workstation (Lenovo ThinkPad P16s, AMD, Ubuntu 6.17 OEM):
- `/sys/class/udc/` is EMPTY → no UDC.
- Has the full gadget *software* stack (libcomposite loads, configfs mounted,
  CONFIG_USB_CONFIGFS_F_HID=y) but no peripheral *hardware* exposed to Linux.
- The AMD xHCI is host-only; the Type-C `device` data-role is just USB-PD role
  negotiation; the xHCI DbC is a debug-console-only function. None provide a
  general-purpose UDC.

Conclusion: this x86/AMD laptop physically cannot present itself as a USB keyboard
through its own ports. A separate board with a real UDC is required.

## Solution architecture

```
  [ workstation ]  --SSH(net)-->  [ Raspberry Pi 4 ]  --USB-C cable-->  [ Hamoa DUT ]
   ./khid wake                     /dev/hidg0 keyboard                   sees a keyboard
```

A **Raspberry Pi 4** (ubuntu@192.168.1.198, no password) is the gadget: its USB-C
port has a real OTG/UDC controller (`dwc2`, UDC name `fe980000.usb`). The
workstation drives it over the network via SSH.

### Pi readiness (verified)
- Pi 4 Model B, Ubuntu 24.04, kernel 6.8.0-1057-raspi.
- `config.txt` has `dtoverlay=dwc2` (default OTG) under `[all]`. (A
  `dtoverlay=dwc2,dr_mode=host` line exists but is under a `[cm4]` section that
  does NOT apply to a Pi 4 Model B — harmless.)
- UDC `fe980000.usb` present; no reboot/config change needed.

---

## Deliverables (this directory + qpa/hid-keyboard/ + deployed on the Pi)

| File | Runs on | Purpose |
|------|---------|---------|
| `hid-keyboard-gadget.sh` | Pi | create/remove the configfs HID keyboard gadget (`up`/`down`/`status`) |
| `sendkeys.py`            | Pi | write 8-byte boot-keyboard HID reports to `/dev/hidg0` (type/key/combo/wake) |
| `khid`                   | workstation | SSH wrapper that drives the Pi remotely |
| `README.md`              | — | full user documentation |

Deployed copy on the Pi: `/home/ubuntu/hid-keyboard/`.
Working copy in repo:     `~/qualcomm/qpa/hid-keyboard/`.
Archive copy:             `~/qualcomm/ai_effort/keyboard_gadget/` (this dir).

### Technical details
- Gadget identity: VID:PID `1d6b:0104` (Linux Foundation / Multifunction
  Composite), product string "QPA Test Harness Virtual Keyboard".
- Standard boot-protocol keyboard: 63-byte HID report descriptor, 8-byte reports
  `[modifiers, 0, k1..k6]`.
- Config `bmAttributes=0xa0` → bus-powered + **Remote Wakeup** enabled (so a
  keystroke can wake a suspended host).
- `sendkeys.py` maps full ASCII (incl. shifted chars), named keys (enter/esc/
  space/f1-f12/arrows/etc.), and combos (e.g. `ctrl-alt-delete`). `--wake` taps
  Left-Shift (wakes the host, injects no character).

---

## Verification done (without the physical cable)
- Gadget creates, binds to `fe980000.usb`, `/dev/hidg0` appears. ✓
- Report descriptor read back = 63 bytes, correct boot-keyboard bytes. ✓
- dwc2 bound driver `configfs-gadget.hidkbd`. ✓
- `sendkeys.py --help` OK; writing with no host attached returns a clean,
  friendly message (errno 108 ESHUTDOWN handled, no traceback). ✓
- `khid` wrapper reaches the Pi over SSH and runs sendkeys remotely. ✓

## FULL HARDWARE VERIFICATION (2026-06-18) — WORKS END TO END ✓
Setup: operator inserted a **powered USB hub** between the Pi and the Hamoa, so
the Pi's USB-C is both powered (from the hub) and the data channel.
- Pi UDC state went `not attached` → **`configured`** (Hamoa enumerated it).
- Hamoa side: `lsusb` shows `1d6b:0104 Linux Foundation Multifunction Composite
  Gadget`; input device `QPA Test Harness Virtual Keyboard` with `kbd`/`event4`
  handlers, located at `2-2.1.2` (behind the hub `2-2`).
- Sent `abc` + Enter + `--wake` from the WORKSTATION via `khid` (SSH→Pi). The
  Hamoa input layer received exactly: a, b, c, ENTER, LSHIFT. ✓
Chain proven: workstation → SSH → Pi USB-C gadget → powered hub → Hamoa.

GOTCHA observed: the Pi had rebooted (when re-cabled through the hub), which tore
down the configfs gadget — had to re-run `khid up`. This is why an auto-start
systemd unit on the Pi is recommended (see follow-ups).

---

## Physical connection (for the operator)
- **Pi 4:** use the **USB-C port** (normally power) — the ONLY UDC-capable port.
  The 4 USB-A ports are host-only and will not work.
- **Hamoa DUT:** any **USB-A host port**.
- Cable: **USB-C (Pi) ↔ USB-A (Hamoa)**.
- **Power:** USB-C is now the data port, so either (1) let the Hamoa bus-power the
  Pi over the same cable (verify the Pi stays pingable; a Pi 4 keyboard-only load
  is ~0.6-1.2 A), or (2) power the Pi from a 5V GPIO supply / PoE HAT. The Pi
  stays SSH-reachable over its network either way.

## Recommended bring-up
1. Self-test: Pi-USB-C → a workstation USB-A; `./khid type "hello\n"` into a text
   editor to confirm typing works.
2. Then Pi-USB-C → Hamoa. `./khid status` should show `UDC state: configured`;
   `lsusb` on the DUT lists `1d6b:0104`.
3. Use `./khid wake` / `./khid type ...` in the suspend harness.

## Usage cheatsheet (from the workstation)
```bash
./khid up                     # bring gadget up on the Pi
./khid status                 # check gadget/UDC state
./khid wake                   # tap Left-Shift (wake a suspended DUT)
./khid type "root\n"          # type a string (\n=Enter, \t=Tab)
./khid key enter              # single named key
./khid combo ctrl-alt-delete  # key combo
./khid raw --key esc --repeat 3 --delay 0.2
./khid down                   # remove the gadget
```

## Open / optional follow-ups
- ~~The gadget is torn down on Pi reboot. A systemd unit...~~ **DONE** (see below).
- `khid wake` can be wired into the earlier suspend-test scripts as the wake step.
- NOTE: USB remote-wakeup only works if the DUT armed its USB controller as a wake
  source before suspend. (Separately, full s2idle *resume* on Hamoa is still
  blocked by the DSP/PCIe platform issues documented under
  ../2153998_june_patchset_rebase/suspend_resume_investigation.md.)

## Auto-start persistence (DONE, 2026-06-18)
`hid-keyboard-gadget.service` installed on the Pi at
`/etc/systemd/system/hid-keyboard-gadget.service` and `enabled`. It:
- waits up to ~15s for the dwc2 UDC to appear (ExecStartPre loop),
- runs `hid-keyboard-gadget.sh up` on boot (after `sys-kernel-config.mount`),
- runs `hid-keyboard-gadget.sh down` on stop.

Verified: `systemctl restart hid-keyboard-gadget` recreates the gadget (UDC ->
`configured`), and a full end-to-end keystroke test through the service-managed
gadget succeeded — typed `ok\n` from the workstation, Hamoa received o, k, ENTER.

So after a Pi reboot the keyboard now comes up automatically; no manual
`khid up` needed. Manage with:
  sudo systemctl {status|restart|stop|start} hid-keyboard-gadget
