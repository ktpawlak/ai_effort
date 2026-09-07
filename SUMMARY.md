# Launchpad Ticket Analyses — Summary

This repository collects per-ticket investigation notes, patches, and rebase
guides for Qualcomm ARM kernel work at Canonical. Each `2XXXXXXX_*` directory
corresponds to a Launchpad bug. This file summarises them; open the linked
directory (and its `analysis.md` / `README.md`) for full detail.

| Ticket | Topic | Board / Kernel | Status |
|--------|-------|----------------|--------|
| [2023546](2023546_so_incoming_cpu/) | `so_incoming_cpu` selftest fails on 1-CPU systems | cloud VMs | Patch sent upstream |
| [2059391](2059391_rtc_ltp/) | RTC not synced (read-only PMIC RTC) | RB3/carmel | Fix Released; LTP v2/v3 series sent |
| [2139069](2139069_wrong_power_domain_id/) | Wrong power-domain ID for rpmhpd | RB4 / QCS8300 | Fix Committed |
| [2146501](2146501_dmesg_inspect_cpu_opp_fixes/) | CPU OPP / frequency fixes from dmesg inspection | QCS8300 | Patches drafted |
| [2153998](2153998_june_patchset_rebase/) | June Qualcomm patchset rebase (7.0-rc6 → 7.1-rc2) | Ubuntu qcom tree | Completed + guide |
| [2158071](2158071_july_patchset_rebase/) | July Qualcomm patchset rebase (7.1-rc2 → 7.1-rc6) | Ubuntu qcom tree | Completed |
| [2163331](2163331_pcie_refgen/) | PCIe5 needs L7A (refgen) vote | Monza / RB4 | Draft reply + patch |
| [2165440](2165440/) | Rebase conflict-resolution audit (6.8.0-1084.89) | Noble / cranky-qcom | 4 defects fixed |

---

## Bug-fix / analysis tickets

### 2023546 — `so_incoming_cpu` on single-CPU systems
All 12 `net:so_incoming_cpu` selftests failed on 1-vCPU cloud instances (AWS
`a1.medium`, Azure `Standard_B1ms`) with `Expected 2 <= self->nproc (1)`. Not a
real failure — the test needs ≥2 CPUs to verify per-CPU listener distribution.
**Fix:** report `SKIP` when `get_nprocs() < 2`. Patch sent upstream to net-next
(2026-09-04).

### 2059391 — RTC not synchronized with system clock
Qualcomm PMIC RTCs are effectively **read-only** (`RTC_SET_TIME` → `-ENODEV`),
so `hwclock --systohc` fails and the RTC free-runs from the 1970 epoch. This is
a hardware/DT limitation, not a kernel defect. **Fix belongs in the test:** an
operation the hardware cannot perform should be LTP `TCONF` (skip), not `TFAIL`.
Submitted a 2-patch LTP series (later v3): (1) close the RTC fd on the
`ioctl()` error path to avoid a leaked exclusive fd causing `EBUSY`; (2) make
`rtc02` skip with `TCONF` on read-only RTCs.

### 2139069 — Wrong power-domain ID for rpmhpd (QCS8300)
`qcs8300.dtsi` mixed incompatible `RPMHPD_*` (generic) and `QCS8300_*`
(chip-specific, different numeric values) power-domain constants, so the driver
`xlate()` looked up the wrong array entry. Root cause: QCLinux kept an early
draft's `QCS8300_*` driver names after upstream reverted to generic `RPMHPD_*`.
**Fix (2 patches):** switch the driver's rpmhpd array to `RPMHPD_*`, and replace
the 5 remaining `QCS8300_MMCX`/`QCS8300_MXC` DTS references. Fix Committed.

### 2146501 — CPU OPP / frequency fixes
Task was to SSH to the device, inspect `dmesg`, and fix issues in the kernel
code. Produced two `qcs8300.dtsi` patches: a frequency-issue fix and adding a
missing low-frequency OPP.

### 2163331 — PCIe5 needs Regulator L7A (refgen) vote (Monza)
Review of a proposed downstream patch. Findings: (1) a bare `"refgen"` in the
*regulator* list is a mix-up — `refgen` is a PHY **clock**; only
`vdda-refgen-supply = <&vreg_l7a>` is correct. (2) The patch updates only the
`sa8775p_gen4x4` cfg, so votes on `pcie0_phy` (which uses
`qcs8300_qmp_gen4x2_pciephy_cfg`) are inert. (3) This is downstream-only — no
upstream commit to align to. Draft reply + corrected patch attached (not posted).

---

## Rebase efforts

### 2153998 — June Qualcomm patchset rebase (qcom-next-7.0-rc6 → 7.1-rc2)
Lifts Qualcomm-specific patches from the new tag onto the Ubuntu derivative
without pulling mainline commits, keeping history **linear** (cherry-pick, never
merge). Includes `AGENT_REBASE_GUIDE.md` — the canonical step-by-step procedure
and pitfalls reused by later rebases — plus suspend/resume and DSP-crash
investigations.

### 2158071 — July Qualcomm patchset rebase (qcom-next-7.1-rc2 → 7.1-rc6)
Delta rebase of only the new Qualcomm work since the June effort; Ubuntu tree
stays on the 7.0 base. Followed the June guide's methodology on a dedicated test
branch.

### 2165440 — Rebase conflict-resolution audit (Ubuntu-qcom-6.8.0-1084.89)
Audit of a Noble `cranky/qcom` rebase onto `Ubuntu-6.8.0-145.145`. Found and
fixed **four** conflict-resolution defects — two build failures and two *silent*
ones that would have shipped a broken driver (e.g. `drivers/soc/qcom/ice.c`
losing its `ice_mutex`/`ice_handles` definitions to a theirs-only resolution).

---

## Related tooling (non-ticket directories)

`qpa/` (board flash/test automation), `keyboard_gadget/` (USB HID DUT control),
`fan_control/`, `hamoa_slim_initramfs/`, `monza2_slim_initramfs/`, `config_trim/`,
and `overlay/` (DTB overlay tutorial). See each directory's README and the
repository `.github/copilot-instructions.md`.
