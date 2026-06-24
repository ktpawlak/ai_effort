#!/bin/bash
# arm-suspend-test.sh — Arm the kernel for verbose suspend/resume logging.
#
# Run as root on the Hamoa board before triggering any suspend test.
# The settings are volatile (lost on reboot).
#
# What each setting does:
#   console_suspend=N    Keep the serial console alive during suspend so
#                        per-device output is visible even if the board resets.
#                        Without this the console is suspended before devices
#                        and all output after that point is lost.
#
#   pm_print_times=1     Print "calling <device>" / "returned N after Kus" for
#                        every device in the suspend/resume sequence.
#
#   pm_debug_messages=1  Extra PM debug messages (very verbose, optional).
#
#   mem_sleep=s2idle     Use CPU-only idle rather than deep/S3 which the
#                        firmware does not support on this platform.
#
#   printk level 8       Ensure all kernel messages (including DEBUG) are
#                        printed to the console.
#
# Usage:
#   sudo ./arm-suspend-test.sh              # arm for real suspend
#   sudo ./arm-suspend-test.sh --pm-test    # arm for dry-run (devices only, auto-resume)

set -e

PM_TEST_MODE=0
[ "${1:-}" = "--pm-test" ] && PM_TEST_MODE=1

echo N > /sys/module/printk/parameters/console_suspend
echo 1 > /sys/power/pm_print_times
echo 1 > /sys/power/pm_debug_messages 2>/dev/null || true
echo s2idle > /sys/power/mem_sleep
echo 8 > /proc/sys/kernel/printk
dmesg --clear

if [ "$PM_TEST_MODE" -eq 1 ]; then
    echo devices > /sys/power/pm_test
    echo "pm_test set to: $(cat /sys/power/pm_test)"
else
    echo none > /sys/power/pm_test
fi

echo "--- armed ---"
echo "console_suspend : $(cat /sys/module/printk/parameters/console_suspend)"
echo "pm_print_times  : $(cat /sys/power/pm_print_times)"
echo "mem_sleep       : $(cat /sys/power/mem_sleep)"
echo "pm_test         : $(cat /sys/power/pm_test)"
echo ""
if [ "$PM_TEST_MODE" -eq 1 ]; then
    echo "Trigger dry-run (devices only, auto-resumes ~5s, no real power-down):"
    echo "  echo freeze | sudo tee /sys/power/state"
else
    echo "Trigger real suspend (watch serial console):"
    echo "  sudo setsid bash -c 'sleep 2; systemctl suspend' &"
fi
