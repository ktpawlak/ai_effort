# Why ADSP/CDSP Crash on s2idle Resume — CXPC Explained

Date: 2026-06-18
Board: Qualcomm Hamoa IoT EVK (x1e80100 / Snapdragon X Elite)
Context: companion explainer to suspend_resume_investigation.md, section 9 (Issue A).

This document explains, in detail, the sentence:

  "On CXPC entry the DSP power-domain votes drop, TZ power-gates them, and on
   resume TZ sends a fatal error because Linux never gracefully shut them down."

---

## 1. The power architecture: CX, MX, rails, and RPMh

On x1e80100 most of the SoC's non-CPU logic — DSPs, PCIe, display, USB,
interconnect fabrics — is powered from a shared top-level rail called **CX**
("Compute/Core eXtension"), with a companion memory rail **MX**. Finer-grained
child domains are layered on top of CX/MX for individual subsystems, including
the ones the DSPs use:

  - ADSP (audio DSP)   votes on `lcx` / `lmx`  = LPASS-CX / LPASS-MX
                                                 (the LPASS island's slice of CX/MX)
  - CDSP (compute DSP) votes on `cx` / `mxc` / `nsp`  (NSP = compute/NPU rail)

Nobody switches these rails directly. A dedicated always-on microcontroller,
**RPMh**, is the arbiter. Every "master" on the chip — the application CPUs (AP),
the ADSP, the CDSP, the modem, etc. — sends **votes** to RPMh ("I need CX at
performance level X"). RPMh **aggregates** all votes and only collapses a rail
when EVERY master has dropped its vote.

Key invariant:
  >> CX stays powered as long as ANY master still votes for it. <<

## 2. CXPC and the idle hierarchy

**CXPC = CX Power Collapse**: the state where RPMh has determined that no master
needs CX, so it powers the entire CX rail off. It is the deepest, lowest-power
system idle state.

In our DTS (arch/arm64/boot/dts/qcom/hamoa.dtsi) the nested PSCI "domain idle
states" are:

    cluster_cl4   arm,psci-suspend-param = 0x01000044   ← CPU cluster retention (shallow)
    cluster_cl5   arm,psci-suspend-param = 0x01000054   ← CPU cluster power-down
    domain_ss3    arm,psci-suspend-param = 0x0200c354   ← CXPC: whole-SoC / CX collapse (deepest)

When Linux goes idle (or into s2idle) it walks DOWN this hierarchy:
CPU -> cluster -> and if everything lines up, the system power domain enters
`domain_ss3`, which is the request to RPMh to collapse CX.

Our tree HAS `domain_ss3` wired into the system power domain — which is exactly
why suspend on this board reaches CXPC depth. Many boards leave it out as a
`/* TODO: system-wide idle states */` placeholder, so they never get this far and
never hit this bug. We DO get there, so we DO hit it.

## 3. How the DSPs normally interact with CX (proxy votes + handover)

From the remoteproc PAS driver (drivers/remoteproc/qcom_q6v5_pas.c):

At DSP boot:
    static int qcom_pas_pds_enable(...) {
        dev_pm_genpd_set_performance_state(pds[i], INT_MAX);
        pm_runtime_get_sync(pds[i]);     // AP votes lcx/lmx (or cx/mxc/nsp) ON
    }

These are PROXY votes. While the DSP firmware is still loading it cannot yet talk
to RPMh itself, so the AP votes ON THE DSP'S BEHALF to keep its rails up. Once the
firmware finishes booting it signals **handover** — it now has its own live RPMh
connection and votes for its own rails directly. The AP then DROPS the proxy
votes:

    static void qcom_pas_pds_disable(...) {     // after handover
        dev_pm_genpd_set_performance_state(pds[i], 0);
        pm_runtime_put(pds[i]);                 // AP releases its proxy vote
    }

So in normal steady state, a RUNNING ADSP/CDSP holds its OWN votes on
lcx/lmx / cx/mxc/nsp via its OWN RPMh master port. Because of RPMh aggregation,
those DSP votes keep CX from collapsing while the DSPs are alive. When a DSP is
genuinely idle, ITS OWN FIRMWARE lowers/drops its votes and saves whatever
context it needs in a way it knows is safe — and only then can CX collapse.

Crucial point:
  >> For a DSP, the entity that decides it is safe to let CX go away is the DSP's
     OWN FIRMWARE — not Linux. <<

## 4. Why it crashed for us

During `systemctl suspend` on our board:

  1. `auto_boot = true` for both DSPs, and Linux NEVER stops them — there is no
     suspend hook to do so. From Linux's view they are permanently "running".
  2. Linux freezes userspace/devices and drives the system power domain into
     `domain_ss3` (CXPC).
  3. The ADSP/CDSP firmware was NEVER told "we are about to collapse CX — save
     your state and release your rails gracefully." There is no Linux->DSP
     suspend handshake. So from the DSP firmware's perspective, CX (its
     lcx/lmx, cx/mxc/nsp) is yanked out from under it while it still considered
     itself live.
  4. TZ (TrustZone / secure monitor) is the watchdog that tracks each
     subsystem's health. When CX returns on resume, TZ sees the DSP subsystem in
     an inconsistent/torn-down state that did not follow the proper power-down
     protocol, and raises a fatal error:

         qcom_q6v5_pas 6800000.remoteproc:  fatal error from TZ (sys_m_smsm.c:322)
         remoteproc remoteproc0: crash detected in adsp: type fatal error
         qcom_q6v5_pas 32300000.remoteproc: fatal error from TZ (sys_m_smsm.c:475)
         remoteproc remoteproc1: crash detected in cdsp: type fatal error

  5. The remoteproc core treats that as a subsystem crash and starts SSR
     (subsystem restart) recovery — but mid-resume, with PCIe also failing to
     re-init, the whole thing cascades into a reboot.

### Why "just stop the DSPs first" also failed (our experiment)
Stopping the ADSP removed the LPASS clock/power provider that the audio macros
depend on, so on resume THOSE could not re-enable their clocks:

    va_macro/tx_macro/wsa_macro/rx_macro: __pm_clk_enable: failed ... -5/-6
                                          "unable to prepare mclk"
    wcd9380-codec: IRQ sync failed to resume: -16
    Failed to enable clk 'bus_slave': -16 -> qcom-pcie 1bd0000.pcie: Host init failed

So the DSPs cannot simply be removed from the picture — half the audio subsystem
(and clock tree) hangs off the ADSP. We merely traded the TZ crash for LPASS/PCIe
clock failures, and the board still rebooted.

## 5. How it SHOULD look

For CXPC during s2idle to be safe, the DSPs must be brought to a coordinated
state BEFORE CX collapses, and restored after. Two valid shapes:

### (a) Graceful shutdown/restart around suspend  (the missing mainline piece)
Add system-sleep PM ops to qcom_q6v5_pas.c:

    static int adsp_suspend(struct device *dev) {
        // qcom_q6v5_request_stop() / qcom_scm_pas_shutdown()
        // -> unload firmware, reset the subsystem cleanly, drop its rail votes
    }
    static int adsp_resume(struct device *dev) {
        // qcom_scm_pas_auth_and_reset() + reload firmware
        // -> bring the DSP back up after CX is restored
    }
    static const struct dev_pm_ops adsp_pm_ops = {
        SET_SYSTEM_SLEEP_PM_OPS(adsp_suspend, adsp_resume)
    };

With this, Linux tears the DSP down properly (so TZ is satisfied that protocol
was followed), CX can collapse, and on resume the driver re-auths and reloads
firmware. THIS CODE EXISTS NOWHERE UPSTREAM — not in our tree, not in qcom-next —
which is the core blocker.

### (b) Firmware-coordinated retention  (closer to how Windows does it)
The DSP firmware itself implements a low-power "sleep" handshake (via
GLINK/QMP/pd-mapper messaging): on a system-suspend signal it saves context,
lowers its RPMh votes to a retention level, and acks that it is ready for CX
collapse; on resume it restores. No full firmware reload, so it's true
"suspend", but it requires BOTH firmware support AND the Linux-side messaging
plumbed in.

### The invariant (both approaches)
  >> CX must not be collapsed until every subsystem voting on it (including the
     DSPs) has been brought to a state where IT has agreed it is safe. <<

Our crash is exactly the violation of that invariant: Linux collapsed CX while
the DSPs still thought they owned their rails, and TZ flagged the resulting torn
state as a fatal subsystem error.

## 6. Relation to the PCIe -ENODEV failure

The PCIe resume failure is a parallel, smaller instance of the SAME theme: the
PCIe GDSCs are collapsed at CXPC (PWRSTS_OFF_ON) without the resume path handling
re-initialization, so `genpd_resume_noirq` returns -ENODEV. Difference: PCIe DOES
have partial upstream fixes (GDSC PWRSTS_RET_ON retention; the D3cold series
merged to pcie/next ~7.1), whereas the DSP system-sleep support does not exist
at all.

## 7. Bottom line

This is platform ENABLEMENT, not a one-line bug fix. Making s2idle resume work
requires either writing remoteproc system-sleep PM ops (option a) or a firmware
sleep protocol (option b), neither of which exists upstream today. That is why
the original "instant reboot at suspend entry" was fixable (PCIe L2 timeout made
non-fatal), but a clean RESUME is currently blocked.
