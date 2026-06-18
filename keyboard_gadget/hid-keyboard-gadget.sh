#!/bin/bash
# hid-keyboard-gadget.sh - Create/remove a USB HID keyboard gadget on a Linux
# board that has a UDC (e.g. Raspberry Pi 4 USB-C / dwc2).
#
# Usage:
#   sudo ./hid-keyboard-gadget.sh up      # create gadget + bind to UDC -> /dev/hidg0
#   sudo ./hid-keyboard-gadget.sh down    # unbind + remove gadget
#   sudo ./hid-keyboard-gadget.sh status  # show state
#
# After 'up', /dev/hidg0 accepts 8-byte boot-protocol keyboard reports:
#   byte0 = modifier bitmask, byte1 = 0 (reserved), byte2..7 = up to 6 keycodes.
#
# The gadget advertises Remote Wakeup so sending a keystroke can wake a
# suspended USB host (used for the suspend test harness).

set -euo pipefail

GADGET_NAME="hidkbd"
G="/sys/kernel/config/usb_gadget/${GADGET_NAME}"

# USB identity (0x1d6b/0x0104 = Linux Foundation / Multifunction Composite;
# generic, fine for a test keyboard).
VID="0x1d6b"
PID="0x0104"
MANUF="QPA Test Harness"
PRODUCT="Virtual Keyboard"
SERIAL="qpa-kbd-0001"

# Standard boot-protocol keyboard HID report descriptor (8-byte input report),
# as a flat hex string (no spaces).
REPORT_DESC_HEX="05010906a1010507\
19e029e715002501\
7501950881029501\
7508810395057501\
0508190129059102\
9501750391039506\
7508150025650507\
1900296581 00c0"
REPORT_DESC_HEX="${REPORT_DESC_HEX// /}"

die() { echo "ERROR: $*" >&2; exit 1; }

find_udc() {
    local udc
    udc=$(ls /sys/class/udc 2>/dev/null | head -1)
    [ -n "$udc" ] || die "No UDC found in /sys/class/udc (is dwc2/peripheral mode enabled?)"
    echo "$udc"
}

gadget_up() {
    [ "$(id -u)" -eq 0 ] || die "must run as root"

    modprobe libcomposite 2>/dev/null || true
    [ -d /sys/kernel/config/usb_gadget ] || \
        die "configfs usb_gadget not available (CONFIG_USB_CONFIGFS / libcomposite)"

    if [ -e "${G}/UDC" ] && [ -n "$(cat ${G}/UDC 2>/dev/null)" ]; then
        echo "Gadget '${GADGET_NAME}' already bound to UDC: $(cat ${G}/UDC)"
        return 0
    fi

    mkdir -p "$G"
    echo "$VID" > "${G}/idVendor"
    echo "$PID" > "${G}/idProduct"
    echo 0x0100 > "${G}/bcdDevice"   # device version 1.0.0
    echo 0x0200 > "${G}/bcdUSB"      # USB 2.0

    mkdir -p "${G}/strings/0x409"
    echo "$SERIAL"  > "${G}/strings/0x409/serialnumber"
    echo "$MANUF"   > "${G}/strings/0x409/manufacturer"
    echo "$PRODUCT" > "${G}/strings/0x409/product"

    # Configuration 1, with Remote Wakeup (bmAttributes bit5=0x20) + bus powered.
    mkdir -p "${G}/configs/c.1/strings/0x409"
    echo "HID Keyboard" > "${G}/configs/c.1/strings/0x409/configuration"
    echo 0xa0 > "${G}/configs/c.1/bmAttributes"   # 0x80 bus-powered | 0x20 remote-wakeup
    echo 250  > "${G}/configs/c.1/MaxPower"

    # HID keyboard function.
    mkdir -p "${G}/functions/hid.usb0"
    echo 1 > "${G}/functions/hid.usb0/protocol"      # 1 = keyboard
    echo 1 > "${G}/functions/hid.usb0/subclass"      # 1 = boot interface
    echo 8 > "${G}/functions/hid.usb0/report_length" # 8-byte reports
    # Write the report descriptor as raw bytes.
    python3 -c "import sys; sys.stdout.buffer.write(bytes.fromhex('${REPORT_DESC_HEX}'))" \
        > "${G}/functions/hid.usb0/report_desc"

    ln -sf "${G}/functions/hid.usb0" "${G}/configs/c.1/"

    local udc; udc=$(find_udc)
    echo "$udc" > "${G}/UDC"
    echo "Gadget '${GADGET_NAME}' bound to UDC '${udc}'."
    sleep 0.3
    ls -l /dev/hidg0 2>/dev/null && echo "Ready: /dev/hidg0" || \
        echo "WARNING: /dev/hidg0 not present yet (check dmesg)."
}

gadget_down() {
    [ "$(id -u)" -eq 0 ] || die "must run as root"
    [ -d "$G" ] || { echo "Gadget '${GADGET_NAME}' not present."; return 0; }

    # Unbind from UDC first.
    if [ -e "${G}/UDC" ]; then echo "" > "${G}/UDC" 2>/dev/null || true; fi

    # Remove function symlinks from configs.
    rm -f "${G}/configs/c.1/hid.usb0" 2>/dev/null || true
    rmdir "${G}/configs/c.1/strings/0x409" 2>/dev/null || true
    rmdir "${G}/configs/c.1" 2>/dev/null || true
    rmdir "${G}/functions/hid.usb0" 2>/dev/null || true
    rmdir "${G}/strings/0x409" 2>/dev/null || true
    rmdir "$G" 2>/dev/null || true
    echo "Gadget '${GADGET_NAME}' removed."
}

gadget_status() {
    echo "UDCs available: $(ls /sys/class/udc 2>/dev/null | tr '\n' ' ')"
    if [ -d "$G" ]; then
        echo "Gadget '${GADGET_NAME}': present"
        echo "  bound UDC: $(cat ${G}/UDC 2>/dev/null || echo '(none)')"
        local udc; udc=$(ls /sys/class/udc 2>/dev/null | head -1)
        [ -n "$udc" ] && echo "  UDC state: $(cat /sys/class/udc/$udc/state 2>/dev/null)"
        ls -l /dev/hidg0 2>/dev/null || echo "  /dev/hidg0: absent"
    else
        echo "Gadget '${GADGET_NAME}': not present"
    fi
}

case "${1:-}" in
    up)     gadget_up ;;
    down)   gadget_down ;;
    status) gadget_status ;;
    *) echo "Usage: $0 {up|down|status}" >&2; exit 1 ;;
esac
