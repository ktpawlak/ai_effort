#!/bin/bash
# trigger-suspend-test.sh — Trigger a suspend test after arm-suspend-test.sh.
#
# Usage (on the Hamoa board):
#   sudo ./arm-suspend-test.sh [--pm-test]  # arm first
#   sudo ./trigger-suspend-test.sh [--pm-test]
#
# Without --pm-test: triggers real systemctl suspend (detached).
# With    --pm-test: triggers pm_test=devices dry-run (auto-resumes ~5s).
#
# In both cases: WATCH THE SERIAL CONSOLE.
# The pm_print_times output only goes to the kernel ring buffer and serial
# port — it does NOT survive a hard reset and is NOT captured by journald.

PM_TEST_MODE=0
[ "${1:-}" = "--pm-test" ] && PM_TEST_MODE=1

if [ "$PM_TEST_MODE" -eq 1 ]; then
    echo "Triggering pm_test=devices dry-run (auto-resumes in ~5s)..."
    echo freeze | tee /sys/power/state
    echo "--- returned from pm_test ---"
    # Reset pm_test so normal suspend works afterwards
    echo none > /sys/power/pm_test
    echo "pm_test reset to: $(cat /sys/power/pm_test)"
    echo ""
    echo "=== last 60 lines of dmesg (suspend/resume sequence) ==="
    dmesg | grep -iE "PM:|calling|returned|failed|error|suspend|resume" | tail -60
else
    echo "Triggering real suspend (detached — SSH session will disconnect)..."
    echo "Watch the serial console for output."
    setsid bash -c "sleep 2; systemctl suspend" &
    echo "Suspend triggered. Board will disconnect shortly."
fi
