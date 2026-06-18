# USB HID Keyboard Test Harness

A virtual USB keyboard for remotely interacting with a device under test (DUT) —
e.g. waking the Qualcomm Hamoa board from suspend, or driving a bootloader/console
that only takes USB-keyboard input.

## Why a separate board?

A USB **gadget** (acting as a USB *device*) requires *peripheral-capable*
controller hardware (a UDC). The x86/AMD workstation has **host-only** USB — its
`/sys/class/udc/` is empty — so it physically cannot present itself as a keyboard.

A **Raspberry Pi 4** is used as the gadget: its USB-C port has a real OTG/UDC
controller (`dwc2`). The workstation drives it over the network (SSH).

```
  [ workstation ]  --SSH(net)-->  [ Raspberry Pi 4 ]  --USB-C cable-->  [ Hamoa DUT ]
   ./khid wake                     /dev/hidg0 keyboard                   sees a keyboard
```

## Components

| File | Runs on | Purpose |
|------|---------|---------|
| `hid-keyboard-gadget.sh` | Pi | create/remove the configfs HID keyboard gadget (`up`/`down`/`status`) |
| `sendkeys.py`            | Pi | write HID reports to `/dev/hidg0` (type/key/combo/wake) |
| `khid`                   | workstation | SSH wrapper that drives the Pi remotely |

## Physical connection (IMPORTANT)

- **On the Pi 4:** use the **USB-C port** (the one normally used for power). That
  is the *only* port with a UDC. The 4 USB-A ports are host-only and will NOT work.
- **On the Hamoa DUT:** use any **USB-A host port** (or a USB-C host port).
- Use a **USB-C (Pi) ↔ USB-A (Hamoa)** data cable.

### Powering the Pi
The Pi's USB-C is now the data-to-DUT port, so the Pi needs power. Two options:

1. **Bus-powered (simplest):** the Hamoa host port supplies 5V over the same
   cable. Just plug Pi-USB-C → Hamoa-USB-A. Works if the port supplies enough
   current; a Pi 4 running headless as a keyboard draws ~0.6–1.2 A. Verify the Pi
   stays online (`ping 192.168.1.198`) after connecting; if it browns out, use #2.
2. **GPIO-powered (robust):** power the Pi from a separate 5V supply on the GPIO
   header (pin 2/4 = 5V, pin 6 = GND) or a PoE HAT, leaving USB-C purely for data.

The Pi stays reachable for SSH over its **network** interface (Ethernet/WiFi),
independent of how it is powered.

## Usage

From the workstation (in this directory):

```bash
./khid up                    # bring the keyboard gadget up on the Pi
./khid status                # check it; after cabling to the DUT, UDC state -> 'configured'
./khid wake                  # tap Left-Shift (wakes a suspended DUT, types nothing)
./khid type "root\n"         # type a string (\n = Enter, \t = Tab)
./khid key enter             # single named key (enter, esc, space, f2, up, down ...)
./khid combo ctrl-alt-delete # key combo
./khid raw --key esc --repeat 3 --delay 0.2   # arbitrary sendkeys.py args
./khid down                  # remove the gadget
```

Run directly on the Pi instead (equivalent):

```bash
sudo ./hid-keyboard-gadget.sh up
sudo python3 sendkeys.py --type "hello\n"
sudo python3 sendkeys.py --wake
```

## Verifying enumeration on the DUT

After `./khid up` and cabling Pi-USB-C → DUT:

- Pi side: `./khid status` should show `UDC state: configured` (was `not attached`).
- DUT side: `dmesg | tail` shows a new `input: QPA Test Harness Virtual Keyboard`
  and `lsusb` lists `1d6b:0104`.

If `sendkeys` reports *"Cannot send after transport endpoint shutdown" (errno 108)*
the gadget is up but no host has enumerated it — check the cable/port and that the
DUT is powered.

## Suspend-test use

The gadget advertises **Remote Wakeup** (`bmAttributes=0xa0`). To wake a suspended
DUT, `./khid wake` taps Left-Shift, generating USB activity / resume signalling.
Left-Shift is used because it injects no character into a focused field.

Note: USB remote-wakeup only works if the DUT armed the device for wakeup before
suspend and the DUT's USB controller is a configured wake source.

## Persistence (optional)

The gadget is torn down on Pi reboot. To recreate at boot, add a systemd unit on
the Pi running `hid-keyboard-gadget.sh up` after `sys-kernel-config.mount`.

## Identity

- VID:PID `1d6b:0104` (Linux Foundation / Multifunction Composite — generic)
- Product string `QPA Test Harness Virtual Keyboard`
- 8-byte boot-protocol keyboard reports: `[modifiers, 0, k1..k6]`
