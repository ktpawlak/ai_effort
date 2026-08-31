# Testing the rtc02 fix on a device

`rtc02.arm64` in this directory is the **patched** test, cross-compiled for
aarch64 (libltp linked statically; only libc is dynamic, so it runs standalone
on Monza2/Hamoa).

## 0. Power on and reach the board

```bash
sudo ~/qualcomm/carmel-tools/alpaca.py on          # boards were off
# Monza2 = 192.168.1.185, Hamoa = 192.168.1.123, ssh password: changeme12
BOARD=192.168.1.185                                # or .123
```

## 1. Confirm the board actually has a read-only RTC (baseline)

```bash
sshpass -p changeme12 ssh -o StrictHostKeyChecking=no ubuntu@$BOARD \
  'sudo hwclock --systohc; cat /sys/class/rtc/rtc0/since_epoch'
```

Expected on the affected hardware:
```
hwclock: ioctl(RTC_SET_TIME) to /dev/rtc0 to set the time failed: No such device
<small number>      # RTC stuck near the 1970 epoch / uptime, not wall-clock
```
That `No such device` (ENODEV) is exactly what the fix keys off.

## 2. Run the PATCHED test → expect TCONF (skip)

```bash
sshpass -p changeme12 scp -o StrictHostKeyChecking=no rtc02.arm64 ubuntu@$BOARD:/tmp/
sshpass -p changeme12 ssh -o StrictHostKeyChecking=no ubuntu@$BOARD \
  'chmod +x /tmp/rtc02.arm64 && sudo /tmp/rtc02.arm64'
```

Expected (PASS = fix works):
```
rtc02.arm64    1  TCONF: RTC does not support setting the time: ENODEV ...
Summary:
passed   0
failed   0
...
conf     1
```
Key point: **conf 1 / failed 0** — the test is now *skipped*, not failed.

## 3. (Optional) Prove the before/after difference

Build the STOCK (unpatched) rtc02 and run it on the same board — it reports
`TFAIL: ioctl() RTC_SET_TIME`:

```bash
# on the workstation, from a clean LTP checkout at the same base commit,
# WITHOUT this patch:
git -C /tmp/ltp stash            # or check out upstream rtc02.c
# rebuild for arm64 (see below) and rerun step 2 -> you get "failed 1"
```

## 4. (Optional) Confirm the PASS path still works on writable-RTC hardware

On a device whose RTC *can* be set (e.g. a normal x86 laptop, or any board with
`allow-set-time`/nvmem offset), the patched test must still behave as before:
set / read-back / compare / restore and report **TPASS**. This proves the probe
didn't turn a working RTC into a skip. (Needs root; it briefly sets the RTC and
restores it.)

## Rebuilding the arm64 binary yourself

```bash
git clone https://github.com/linux-test-project/ltp.git && cd ltp
git checkout <the fix branch>          # or: git apply 0001-rtc02-...patch
make autotools
./configure --host=aarch64-linux-gnu CC=aarch64-linux-gnu-gcc
make -C lib
make -C testcases/kernel/device-drivers/rtc rtc02
# -> testcases/kernel/device-drivers/rtc/rtc02  (ARM aarch64)
```

Or build natively on the board:
```bash
sudo apt install -y git make gcc autoconf automake pkg-config
# clone, then: make autotools && ./configure && \
#   make -C testcases/kernel/device-drivers/rtc rtc02 && sudo ./rtc02
```

## Notes

- `rtc02` needs root (`needs_root`). Run under `sudo`.
- The patched probe writes back the RTC's *current* value, so on a writable RTC
  it is a no-op; on a read-only RTC it fails with ENODEV and skips before the
  clock-save/restore, so nothing on the device is modified.
- `rtc01` (alarm/RTC_RD_TIME) is unaffected — it does not call RTC_SET_TIME.
