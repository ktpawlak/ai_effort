#!/bin/bash
# provision-pi.sh - Provision a freshly-flashed Raspberry Pi as the USB HID
# keyboard gadget host. Idempotent: safe to re-run any time.
#
# Run from the workstation, from the directory that holds the harness scripts.
#
#   ./provision-pi.sh [--reboot]
#
# What it does on the Pi:
#   1. enables the dwc2 (OTG/peripheral) overlay in config.txt  (needs a reboot)
#   2. ensures libcomposite is loaded at boot
#   3. copies hid-keyboard-gadget.sh + sendkeys.py into place
#   4. installs + enables the hid-keyboard-gadget systemd service
#   5. if the overlay was just added, a reboot is required for the UDC to appear
#      (pass --reboot to do it automatically); otherwise it starts the gadget now.
#
# Config via env:
#   PI_HOST  (default ubuntu@192.168.1.198)
#   PI_DIR   (default /home/ubuntu/hid-keyboard)

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
PI_HOST="${PI_HOST:-ubuntu@192.168.1.198}"
PI_DIR="${PI_DIR:-/home/ubuntu/hid-keyboard}"
SSH="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 ${PI_HOST}"
SCP="scp -o StrictHostKeyChecking=no"

REBOOT=0
[ "${1:-}" = "--reboot" ] && REBOOT=1

echo "== Provisioning ${PI_HOST} (gadget dir: ${PI_DIR}) =="

# Sanity: required source files present locally.
for f in hid-keyboard-gadget.sh sendkeys.py hid-keyboard-gadget.service; do
    [ -f "${SRC_DIR}/$f" ] || { echo "ERROR: missing source file: ${SRC_DIR}/$f" >&2; exit 1; }
done

# Sanity: Pi reachable.
$SSH true 2>/dev/null || { echo "ERROR: cannot SSH to ${PI_HOST}" >&2; exit 1; }

# 1. copy scripts
echo "-- copying scripts to ${PI_DIR}"
$SSH "mkdir -p ${PI_DIR}"
$SCP "${SRC_DIR}/hid-keyboard-gadget.sh" "${SRC_DIR}/sendkeys.py" "${PI_HOST}:${PI_DIR}/" >/dev/null
$SSH "chmod +x ${PI_DIR}/hid-keyboard-gadget.sh ${PI_DIR}/sendkeys.py"

# Stage the service unit, then do config.txt + modules + service on the Pi.
$SCP "${SRC_DIR}/hid-keyboard-gadget.service" "${PI_HOST}:/tmp/hid-keyboard-gadget.service" >/dev/null

NEED_REBOOT=$($SSH 'sudo bash -s' <<'REMOTE'
set -e
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt
changed=0

# 1. enable dwc2 OTG overlay if not already active (skip commented lines).
if ! grep -qE '^[[:space:]]*dtoverlay=dwc2([,[:space:]]|$)' "$CFG"; then
    printf '\n[all]\n# USB HID keyboard gadget (QPA test harness)\ndtoverlay=dwc2\n' >> "$CFG"
    changed=1
fi

# 2. ensure libcomposite is loaded at boot.
echo libcomposite > /etc/modules-load.d/hid-gadget.conf

# 4. install + enable the systemd service.
cp /tmp/hid-keyboard-gadget.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable hid-keyboard-gadget.service >/dev/null 2>&1

# stdout: whether a reboot is needed (overlay just added).
echo "$changed"
REMOTE
)

echo "-- libcomposite load configured; service installed + enabled"

if [ "$NEED_REBOOT" = "1" ]; then
    echo "!! dwc2 overlay was added to config.txt - a REBOOT is required for the UDC to appear."
    if [ "$REBOOT" = "1" ]; then
        echo "-- rebooting the Pi now..."
        $SSH 'sudo reboot' || true
        echo "   Wait ~60s, then verify with:  ./khid status"
    else
        echo "   Re-run with --reboot, or reboot manually:  ssh ${PI_HOST} sudo reboot"
        echo "   After reboot the gadget auto-starts; verify with:  ./khid status"
    fi
else
    echo "-- dwc2 overlay already active; (re)starting the gadget via systemd"
    $SSH 'sudo systemctl restart hid-keyboard-gadget.service' || true
    sleep 1
    $SSH "sudo ${PI_DIR}/hid-keyboard-gadget.sh status" || true
fi

echo "== Provisioning done =="
