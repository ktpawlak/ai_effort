# Hamoa (x1e80100) Suspend/Resume Investigation

Date: 2026-06-18
Board: Qualcomm Hamoa IoT EVK (x1e80100 / Snapdragon X Elite)
Kernel: 7.0.0-1006-qcom, branch master-next, version 7.0.0-1006.10ubuntu2
Tree: ~/qualcomm/linux

---

## 1. Original symptom

`systemctl suspend` instantly reboots the board. Reported by user.

Initial hypothesis (wrong): deep (S3) vs s2idle. The board exposes:
- `/sys/power/state`     = `freeze mem`
- `/sys/power/mem_sleep` = `s2idle [deep]`  (default was deep/S3)

Testing showed BOTH `deep` and `s2idle` reset the board, so the mode was not
the root cause.

---

## 2. Root cause #1 — suspend ENTRY aborted by PCIe L2 timeout (FIXED)

Serial/dmesg captured during an s2idle attempt showed the suspend was being
ABORTED at the noirq phase, then the resume rollback re-init'd the link and the
board reset:

```
ath12k_wifi7_pci 0004:01:00.0: pci_pm_suspend_late returned 0 after 86933 usecs
qcom-pcie 1c08000.pci: Timeout waiting for L2 entry! LTSSM: 0x11
qcom-pcie 1c08000.pci: PM: genpd_suspend_noirq returned -110 after 26019 usecs
qcom-pcie 1c08000.pci: PM: failed to suspend noirq: error -110
PM: noirq suspend of devices aborted after 172.967 msecs
...
mhi mhi0: Requested to power ON
[ RESET ]
```

The ath12k WCN785x WiFi 7 endpoint on PCIe controller `1c08000.pci` (domain
0004) does not enter PCIe L2 on suspend (LTSSM 0x11). Our kernel's
`dw_pcie_suspend_noirq()` treated that timeout as FATAL (`dev_err` + `return
ret`), so the whole suspend aborted with -110 (-ETIMEDOUT) and the recovery
path reset the board.

### Fix (applied, confirmed)
Cherry-picked upstream commit:

  eed390775470 "PCI: dwc: Proceed with system suspend even if the endpoint
                doesn't respond with PME_TO_Ack message"
  Author: Manivannan Sadhasivam (DWC PCIe maintainer)
  Tested upstream on SM8650-HDK.

Per PCIe spec r7.0 sec 5.3.3.2.1 the PME_TO_Ack/L2 timeout is non-fatal: warn
and continue the L2/L3 stop-link sequence instead of aborting.

  drivers/pci/controller/dwc/pcie-designware-host.c
  -   dev_err(... "Timeout waiting for L2 entry! ...");  return ret;
  +   dev_warn(... "Timeout waiting for L2 entry! ...");  ret = 0;

Applied in our tree as commit c503a7e80bec (HEAD), version 1006.10ubuntu2.

### Result
Suspend now ENTERS successfully — the board actually sleeps (fans stop, console
quiesces). The original "instantly reboots at suspend" symptom is resolved.

---

## 3. Root cause #2 — RESUME path failure (STILL OPEN)

After the suspend-entry fix, the board sleeps but FAILS TO RESUME. On wake
(power button / keyboard) the serial log shows:

```
[drm:dpu_encoder_phys_vid_disable [msm]] *ERROR* wait disable failed: id:41 intf:5 ret:-110
dwc3-qcom-legacy a0f8800.usb: port-1 HS-PHY not in L2
dwc3-qcom-legacy a6f8800.usb: port-1 HS-PHY not in L2
qcom-pcie 1c00000.pci:  PM: dpm_run_callback(): genpd_resume_noirq returns -19
qcom-pcie 1c00000.pci:  PM: failed to resume noirq: error -19
qcom-pcie 1bd0000.pcie: PM: dpm_run_callback(): genpd_resume_noirq returns -19
qcom-pcie 1bd0000.pcie: PM: failed to resume noirq: error -19
qcom_q6v5_pas 32300000.remoteproc: fatal error received: sys_m_smsm.c:475: err fatal notification received from TZ
remoteproc remoteproc1: crash detected in cdsp: type fatal error
qcom_q6v5_pas 6800000.remoteproc: fatal error received: sys_m_smsm.c:322: err fatal notification received from TZ
remoteproc remoteproc0: crash detected in adsp: type fatal error
[ PBL boot banner -> REBOOT ]
```

Two distinct failure classes on resume:

### 3a. PCIe controllers fail resume with -ENODEV (-19)
- `1c00000.pci`  = pcie5 (hamoa.dtsi:3851, compatible qcom,pcie-x1e80100)
- `1bd0000.pcie` = pcie3 (hamoa.dtsi:3434, compatible qcom,pcie-x1e80100)
Both are enabled on hamoa-iot (PHYs + supplies configured in
hamoa-iot-som.dtsi / hamoa-iot-evk.dts). NOTE: these are DIFFERENT controllers
from the ath12k one (1c08000) that caused the suspend-entry issue.

The -19 originates in `qcom_pcie_resume_noirq()` ->
`dw_pcie_resume_noirq()` (drivers/pci/controller/dwc/pcie-designware-host.c
:1270). That function returns the error from one of:
  - pci->pp.ops->init (qcom_pcie_host_init)  -> could be clk/reset/regulator
  - dw_pcie_start_link
  - dw_pcie_wait_for_link  (-ETIMEDOUT, explicitly tolerated by qcom wrapper)
-ENODEV (19) is NOT -ETIMEDOUT, so it propagates and PM logs "failed to resume
noirq". Resume-noirq failures are logged but do NOT by themselves reboot the
board (PM continues resuming other devices).

Possible relation to the user's earlier WORKAROUND commit
"arm64: dts: qcom: Add qref supply for PCIe PHYs" (8c3450985b6b) — the PCIe
PHYs need vdda-qref; investigate whether the qref/L3J rail is not restored in
time on resume.

### 3b. ADSP/CDSP remoteproc fatal TZ errors (the actual board-killer)
- adsp = 6800000.remoteproc
- cdsp = 32300000.remoteproc
Both report "fatal error notification from TZ" on resume and crash, which is
what actually reboots the board.

Key finding: NEITHER our tree NOR qcom-next (qcom-next-7.1-rc2-20260515) has
any system-sleep PM ops in drivers/remoteproc/qcom_q6v5_pas.c:
  grep 'SET_SYSTEM_SLEEP_PM_OPS|.suspend|.resume|dev_pm_ops' -> (none)
So the DSP remoteprocs are not suspended/quiesced across s2idle. Their power
rails drop while they are still "running", and TZ flags them as crashed on
resume. This is a platform/firmware-level limitation, not a simple driver bug.

---

## 4. Assessment

- Suspend ENTRY: FIXED (upstream PCIe L2 patch). Original symptom resolved.
- RESUME: blocked by (a) PCIe pcie3/pcie5 resume -ENODEV and (b) ADSP/CDSP TZ
  fatal crashes. (b) is the fatal one and appears firmware-limited.
- Full s2idle suspend/resume on x1e80100 / Snapdragon X Elite is known to be
  incompletely supported in mainline at this time.

---

## 5. State of the tree & board

- HEAD: c503a7e80bec "PCI: dwc: Proceed with system suspend even if the endpoint
  doesn't respond with PME_TO_Ack message" (cherry-pick of eed390775470)
- spi18 disabled (revert of the re-enable dropped via rebase) -> no boot crash
- Backups: branch backup-before-revert-drop, branch backup-before-squash
- Board reflashed to stock x03 (26.04) image then patched kernel installed:
  ~/qualcomm/images/26.04/x03, via qpa/flash-hamoa.sh
- Board IP 192.168.1.123, user ubuntu / changeme12

## 6. flash-hamoa.sh fixes (qpa repo, this session)
- Removed unused rawprogram0_emmc.xml requirement+copy (hamoa is UFS single-phase;
  the emmc XML was vestigial from the Monza2/eMMC flow and x03 doesn't ship it).
- Hardened the post-flash password change:
  * known_hosts: after reflash the board's SSH host key changes; StrictHostKeyChecking=no
    only auto-accepts NEW keys, not CHANGED ones -> ssh refused -> expect saw no
    password prompt -> silently timed out -> "exits without password changed".
    Fixed with UserKnownHostsFile=/dev/null + ssh-keygen -R before connecting.
  * expect: added `set timeout`, case-insensitive prompt regexes, explicit
    success ("updated successfully") / failure detection, and a post-change
    verification loop (sshpass with NEW_PASS) before declaring success.
  Verified end-to-end: password changed to changeme12 successfully.

---

## 7. Next investigation steps (resume path)
1. PCIe resume -ENODEV: instrument/trace qcom_pcie_host_init on resume for
   pcie3/pcie5; check qref (vdda-qref / L3J) rail and PHY re-init timing.
   Compare qcom-next pcie-qcom.c suspend/resume against ours for x1e80100.
2. ADSP/CDSP: search upstream/lore + qcom-next for q6v5_pas system-sleep PM ops
   or any "remoteproc suspend" / pd-mapper / TZ handover patches for x1e80100.
   Determine whether suspend support for these DSPs exists anywhere yet.
3. Check upstream linux-pm / linaro for x1e80100 s2idle enablement status.

---

## 8. EXPERIMENT: stop DSPs before suspend (2026-06-18)

Hypothesis: the ADSP/CDSP TZ crash is the board-killer; stopping them before
suspend should let the board resume.

Procedure:
  echo stop > /sys/class/remoteproc/remoteproc1/state   # cdsp -> offline
  echo stop > /sys/class/remoteproc/remoteproc0/state   # adsp -> offline
  echo s2idle > /sys/power/mem_sleep ; systemctl suspend

Result: the ADSP/CDSP "fatal error from TZ" / "crash detected" messages were
GONE (replaced by benign "Handover signaled, but it already happened").
=> Stopping the DSPs DID eliminate the TZ crash.

BUT a NEW layer of resume failures appeared and the board still rebooted:
  va_macro/tx_macro/wsa_macro/rx_macro: __pm_clk_enable: failed to enable clk -5/-6,
                                        "unable to prepare mclk"
  wcd9380-codec: IRQ sync failed to resume: -16  (x many)
  ucsi_glink: failed to send UCSI write request: -5
  Failed to enable clk 'bus_slave': -16  ->  qcom-pcie 1bd0000.pcie: Host init failed: -16
  -> reboot

Key insight: the LPASS audio macros (va/tx/wsa/rx) are CLIENTS of the ADSP.
Stopping the ADSP removed their clock provider, so their clocks cannot be
re-enabled on resume. We merely traded the TZ crash for LPASS/PCIe clock
failures. Confirms a multi-subsystem platform suspend/resume gap.

---

## 9. UPSTREAM RESEARCH FINDINGS (2026-06-18)

Thorough lore.kernel.org / linux-arm-msm research. Bottom line:

**Full s2idle with clean resume is NOT achievable on x1e80100 with current
mainline (June 2026).** The fundamental blocker is missing remoteproc
system-sleep support; PCIe has partial fixes.

### Issue A — ADSP/CDSP TZ "fatal error" on resume  (THE blocker)
- `drivers/remoteproc/qcom_q6v5_pas.c` has NO system-sleep PM ops
  (no SET_SYSTEM_SLEEP_PM_OPS / .suspend / .resume) — confirmed absent in BOTH
  our tree AND qcom-next-7.1-rc2-20260515. Only runtime-PM + genpd perf states.
- On CXPC (domain_ss3) entry, the DSP proxy power-domain votes (adsp: lcx/lmx;
  cdsp: cx/mxc/nsp) are dropped, TZ power-gates the DSPs, and on CXPC exit TZ
  sends sys_m_smsm FATAL_ERROR because Linux never gracefully shut them down.
- **No upstream patch exists and none is pending.** A proper fix would add
  system-sleep PM ops that call qcom_q6v5_request_stop()/pas_shutdown() on
  suspend and pas_auth_and_reset()+reload on resume. This is non-trivial new
  development, not a cherry-pick.
- Matches our empirical experiment (section 8): the DSPs are indeed the
  TZ-crash source, but they can't simply be stopped (LPASS depends on ADSP).

### Issue B — PCIe resume -ENODEV  (partial fixes exist)
- Root cause: x1e80100 PCIe GDSCs in gcc-x1e80100.c are PWRSTS_OFF_ON, so genpd
  fully powers them off at CXPC; resume then returns -ENODEV.
- Two upstream approaches:
  1. GDSC retention: "clk: qcom: gcc-x1e80100: Do not turn off PCIe GDSCs ..."
     (Krishna Chaitanya Chundru, 20260102-pci_gdsc_fix-v1-6) — sets PWRSTS_RET_ON
     on all 8 PCIe GDSCs. v1 only, pushback from Konrad/Stephan, NOT merged to
     mainline. NOT in our tree, NOT in qcom-next (both still PWRSTS_OFF_ON).
  2. D3cold series (Krishna Chaitanya Chundru, 20260429-d3cold-v5) — MERGED to
     Mani's pcie/next ~2026-05-13 (commits fdf2dc2b..2ce984da), targeting ~7.1.
- OUR TREE STATE (verified):
  * PCIe GDSCs: PWRSTS_OFF_ON (GDSC retention fix NOT applied)
  * Has dev_pm_genpd_rpm_always_on() in qcom_pcie_suspend_noirq (the
    "Prevent GDSC power down on suspend" approach, FROMLIST 6e0e7de370f3 in
    qcom-next) — but pcie3/pcie5 STILL fail resume with -ENODEV/-EBUSY.
  * Has partial D3cold bits (qcom_pcie_get_ltssm, pci_host_common_can_enter_d3cold).
  * domain_ss3 deepest idle state IS present (hamoa.dtsi:310) -> CXPC is reached.
- D3cold v5 cover letter explicitly notes: CXPC achievable only on systems with
  NO attached NVMe; NVMe-attached systems need further NVMe driver changes.

### Issue C — domain_ss3 / PDC wake GPIOs
- "x1e80100: Enable PDC wake GPIOs and deepest idle state" (hamoa_pdc v3,
  20260616, Maulik Shah) — still under review June 2026. Our tree already has
  domain_ss3 enabled, which is why suspend reaches CXPC depth.

### Qualcomm engineer statements (context)
- Bjorn Andersson: on compute targets s2ram == s2idle + CXPC; "normal retention
  assumptions hold" for x1e80100 -> CXPC is INTENDED to work with proper driver
  support; not a TZ design limitation.
- No official Qualcomm/Linaro doc states "s2idle unsupported"; the gaps are
  Linux driver deficiencies (remoteproc system-sleep + PCIe GDSC/D3cold).

### Key citations
- q6v5 PAS x1e80100 add: lore 20240212-x1e80100-remoteproc-v2-2-604614367f38@linaro.org
- GDSC retention v1: lore 20260102-pci_gdsc_fix-v1-6-b17ed3d175bc@oss.qualcomm.com
- PCIe genpd_rpm_always_on (rejected upstream, but in qcom-next/our tree):
  lore 20260128-genpd_fix-v1-1-cd45a249d12f@oss.qualcomm.com
- D3cold v5 (merged pcie/next): lore 20260429-d3cold-v5-0-89e9735b9df6@oss.qualcomm.com
- hamoa_pdc v3 (deepest idle): lore 20260616-hamoa_pdc_v3-v3-0-4d8e1504ea75@oss.qualcomm.com

---

## 10. CONCLUSION & RECOMMENDATION

- The original symptom ("systemctl suspend instantly reboots") is RESOLVED:
  with the PCIe L2-timeout fix (eed390775470) the board now genuinely suspends.
- A clean RESUME is currently BLOCKED and is NOT fixable with a small patch:
  * ADSP/CDSP have no system-sleep PM ops anywhere upstream (the hard blocker);
    they crash from TZ on every CXPC resume, and they can't simply be stopped
    because LPASS audio clocks depend on the ADSP.
  * PCIe resume needs the GDSC-retention and/or full D3cold work (partly merged
    upstream ~7.1), and even then NVMe-attached systems need more work.
- RECOMMENDATION: treat full s2idle suspend/resume as a platform-enablement
  effort tracked upstream (remoteproc system-sleep PM ops + PCIe D3cold/GDSC +
  hamoa_pdc), not a one-off fix. Keep the PCIe L2-timeout fix. Optionally, to
  reduce resume error noise, the GDSC-retention patch (PWRSTS_RET_ON) could be
  applied, but it will NOT yield working resume on its own due to the DSP blocker.
