# LP #2059391 — RTC clock not synchronized with system clock (RB3/carmel)

**Launchpad:** https://bugs.launchpad.net/carmel/+bug/2059391 (private; fetched via
launchpadlib OAuth using `~/.config/ngd-crank/lp-credentials`)
**Status in LP:** Fix Released (Critical) — closed by *removing* the Checkbox job
from the carmel test plan (x06+). This directory is the **proper upstream fix**.
**External:** https://warthogs.atlassian.net/browse/PECA-170

## Symptom

Checkbox job `com.canonical.certification::rtc/rtc_clock_rtc0` fails:

```
rtc0 Clock not synchronized with System Clock
System clock= Thu Mar 28 06:57:05 UTC 2024
RTC clock=    Thu Jan  1 00:17:13 UTC 1970
```

`hwclock --systohc` → `ioctl(RTC_SET_TIME) to /dev/rtc0 failed: No such device`
(kernel 6.7.0-1011-qcom).

## Root cause

Qualcomm PMIC RTCs are effectively **read-only**. In `drivers/rtc/rtc-pm8xxx.c`,
`pm8xxx_rtc_set_time()` only writes the counter when `allow_set_time` is set;
otherwise it calls `pm8xxx_rtc_update_offset()`, which returns **`-ENODEV`** when
there is no nvmem cell and no UEFI offset configured. The RTC free-runs from the
Linux epoch (1970) and is only meaningful as an uptime source. This is a
hardware/DT limitation, **not** a kernel defect (confirmed by Qualcomm in the bug
thread: "On QC platforms RTC write is not allowed").

Platform DT cannot be modified in this effort, so the fix belongs in the test.

## Where the fix belongs: LTP, not Checkbox

The failing assertion originates upstream in the **Linux Test Project**
(`testcases/kernel/device-drivers/rtc/rtc02.c`), which exercises `RTC_SET_TIME`
and reports `TFAIL` on any error. LTP flows back into Ubuntu test infrastructure
(`ubuntu_ltp`), so fixing it upstream is the durable solution rather than editing
the Checkbox runner.

LTP convention: an operation the hardware genuinely cannot perform is `TCONF`
(skip / not-applicable), not `TFAIL`. Ubuntu autotest and Checkbox both treat
`TCONF` as skipped.

## The fix — 2-patch series (v2)

Submitted upstream as a series (see the `00*.patch` files / cover letter):

### Patch 1 — `lib: tst_rtctime: close RTC fd on the ioctl() error path`

Pre-existing bug surfaced in mailing-list review: `tst_rtc_ioctl()` opens the RTC
with `SAFE_OPEN()` but on an `ioctl()` failure does `return -1` **without closing
the fd**. The RTC chardev is exclusive-open, so a leaked fd makes the *next*
`tst_rtc_ioctl()` open fail with `EBUSY`. This was latent (failure path rarely
taken) until patch 2's probe hits it on every run on read-only RTCs — we actually
observed it in the stock Monza2 run:

```
tst_rtctime.c:116: TWARN: open(/dev/rtc,0,0000) failed: EBUSY (16)
```

Fix: close the fd on the error path and **preserve `errno`** across the close so
callers can still inspect the failure reason (patch 2 checks `errno == ENODEV`).

### Patch 2 — `rtc02: skip (TCONF) on read-only RTCs that reject RTC_SET_TIME`

The whole check lives in `rtc_setup()`, in one place:

- Probe writability by writing back the *current* RTC time (a no-op on writable
  RTCs). When `RTC_SET_TIME` fails with **`ENODEV`** — the errno the Qualcomm
  driver returns and the one verified on Monza2 — skip the whole test with
  `TCONF`. Any other errno still falls through to `TBROK`, so an unknown failure
  on untested hardware is not silently masked. (Wider errnos such as `EINVAL` are
  used broadly across the kernel and were deliberately *not* included, to avoid
  hiding a genuine defect on a device we can't test.)
- The probe runs **before** `tst_rtc_clock_save()`, so the cleanup
  `tst_rtc_clock_restore()` (which also issues `RTC_SET_TIME`) never runs on a
  read-only RTC and cannot raise `TBROK`.

No change is needed in the test body: if the setup probe can set the RTC, the
body's `RTC_SET_TIME` succeeds too; if it cannot, setup skips and the body never
runs. Only `ENODEV` triggers the skip — the errno confirmed against the driver
and on real hardware — keeping the change conservative so untested failure modes
still surface as `TBROK`.

## Verification

- Built cleanly against LTP master: `make autotools && ./configure &&
  make -C testcases/kernel/device-drivers/rtc rtc02` → `rtc02` binary, exit 0.
- **Tested on real hardware** (see `TESTING.md`):

  | Board | RTC | Stock rtc02 | Patched rtc02 |
  |-------|-----|-------------|---------------|
  | Monza2 (QCS8300, 6.8.0-1080, Noble) | read-only (stuck 1970) | `TFAIL: RTC_SET_TIME ENODEV` + cleanup `TWARN` → failed 1, rc=5 | `TCONF ... ENODEV (19)` → skipped 1, failed 0, rc=32 |
  | Hamoa (X1E80100, 7.0.0-1008, Resolute) | writable (offset) | — | `TPASS` → passed 1, rc=0 |

  The stock run on Monza2 confirms both failure modes: the body `TFAIL`s **and**
  the cleanup restore fails (`tst_wallclock.c:117: TWARN: tst_rtc_settime()
  realtime failed`). The patched version skips before `tst_rtc_clock_save()`, so
  there is no cleanup fallout, and it still `TPASS`es on writable hardware.

## Upstream submission

- Branch: `fix/rtc02-readonly-rtc-tconf` (local clone `/tmp/ltp`).
- Send to `ltp@lists.linux.it` or open a PR against `linux-test-project/ltp`.
- LP #2059391 is private — describe the read-only-RTC hardware limitation
  generically in the commit message (already done); do not link the private bug.
