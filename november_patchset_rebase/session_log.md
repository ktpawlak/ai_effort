
---

## User request (2026-11-10T12:33:29.799Z)

> this is a resolute ubuntu kernel for qualcomm. The latest kernel is Ubuntu-qcom-7.0.0-1018.22 that was based off generic's Ubuntu-7.0.0-43.43. It has now been rebased on top of the latest resolute base Ubuntu-7.0.0-44.44. Qualcomm delivered new patches in this repo: ~/qualcomm/ubuntu-qcom-kernel, tag: resolute-qcom-20261030. It is b ased off out tag: Ubuntu-qcom-7.0.0-1016.19 (we have Ubuntu-qcom-7.0.0-1016.20 it's almost the same). Take outstanding patches between Ubuntu-qcom-7.0.0-1016.19 and resolute-qcom-20261030 and put them on top of the kernekl in this repository. At first try to just move them and resolve conflicts.

### Agent report (2026-11-10T12:47:46.889Z)

## Ported Qualcomm's `resolute-qcom-20261030` drop onto the Resolute kernel

**What I did**

Took the outstanding Qualcomm patches in the range `Ubuntu-qcom-7.0.0-1016.19..resolute-qcom-20261030` (from `~/qualcomm/ubuntu-qcom-kernel`) and replayed them on top of the current kernel (`master-next`, `Ubuntu-qcom-7.0.0-1018.22` rebased on `Ubuntu-7.0.0-44.44`).

**Method:** added the delivery repo as a temporary remote, fetched the base/target tags, then used `git rebase --onto master-next <1016.19> <20261030>` with `--empty=drop`. Git auto-dropped patches already present (via patch-id) and I resolved the remaining conflicts by hand.

**Result**
- **420 commits applied** on top of `master-next` (199 files changed, +7426/−5912).
- **~14 commits auto-dropped** as already upstream/present in our tree.
- Work is on branch **`qcom-20261030`**, and `master-next` was fast-forwarded to it (both point to `848e9209e9e3`). Clean working tree, **zero conflict markers** across all 199 changed files.

**Conflicts resolved manually** (all were our local SAUCE vs. the incoming upstream refactors of the same code):
- `clk/qcom/dispcc-qcm2290.c` & `gpucc-qcm2290.c` — took the upstream `const`/`qcom_cc_probe()` model while preserving our SAUCE `.use_rpm = true`, extra `RETAIN_FF_ENABLE` GDSC flag, extra `0x1078` critical cbcr, and `clk_rcg2_shared_ops`.
- `soc/qcom/geni-se.h` & `tty/serial/qcom_geni_serial.c` — ICC vote constants already corrected in our tree; kept the canonical upstream form.
- `ath12k/wmi.{c,h}` — the ML-STA auth-timeout fix was already in our tree plus an extra downstream flag, so that commit became empty and was dropped.

**Validation:** compiled the four hand-resolved drivers (arm64 cross-toolchain) — all build successfully. Note: the two clk drivers emit `-Wdiscarded-qualifiers` warnings because the `const` arrays are assigned to non-const `struct qcom_cc_desc` fields; **these warnings are identical in Qualcomm's own delivery tree** (their `common.h` is also non-const), so this is a faithful port, not a regression. The remaining ~416 commits applied via git's 3-way merge without conflicts.

**Cleanup:** removed the temporary `qdelivery` remote and helper tags; the `resolute-qcom-20261030` reference tag remains. (Pre-existing untracked `log.txt` left untouched.)

**Suggested next steps:** a full `make` of the qcom defconfig/modules and boot/smoke test; optionally add a `common.h` const-correctness fix to silence the pre-existing warnings, and create the next `UBUNTU: Ubuntu-qcom-7.0.0-10xx` release commit once reviewed.

---

## User request (2026-11-10T13:03:29.169Z)

> there is a lot of reverts in the new incoming patchset. Check if these reverst can be collapsed in our derivative that stems from generic's Ubuntu-7.0.0-44.44

### Agent report (2026-11-10T13:07:17.590Z)

Yes, the incoming reverts can be collapsed. I built the collapsed history on a new branch, `qcom-collapsed`, and its final tree is byte-identical to `master-next` (same tree hash). I haven't changed `master-next`.

**What the 184 incoming reverts are**
- **Old Shikra series:** about 180 reverts remove the old Shikra bring-up. Later in the same drop, a new upstream-aligned Shikra series adds it back.
- **Advantech SOM-DB5830:** one revert cancels a patch that's also in this drop.
- **DSI panel chain:** two `shikra-iqs-evk` DSI panel patches were each added, reverted, re-applied and reverted again. Each chain cancels out.

**What I dropped** (rebasing `Ubuntu-7.0.0-44.44..master-next`)
- **368 commits:** 180 original+revert pairs plus the 8-commit DSI chain. Almost all the originals were already in our 1018.22 derivative.
- **3 more commits that became empty:** old Shikra-only fixups that were never reverted by name, but the reverts deleted their files anyway:
  - "Move rpm_requests in glink-edge node"
  - "Enable primary usb controller on IQS platform"
  - "Add dedicated BT 3.3V dummy regulator"

  The new Shikra series already includes the first two changes. The BT 3.3V supply is done differently there (`vreg_wcn_3p3`).

**Result**

| | `master-next` | `qcom-collapsed` |
|---|---|---|
| Commits on top of 44.44 | 1814 | 1443 |
| Reverts | 260 | 74 |
| Commits mentioning Shikra | 530 | 166 |
| Release commits | 14 | 14 |

The rebase needed no manual conflict resolution. Collapsing rewrites history below the existing 1018.22 tag, so the commit hashes change.

The 74 reverts that are left are older pairs already inside our previous derivative, plus 3 reverts with no matching original. They weren't part of this drop, but they could be collapsed the same way.

To adopt it, run `git branch -f master-next qcom-collapsed`.

---

## User request (2026-11-10T13:17:23.623Z)

> create another branch that will agressively remove all add-revert pairs down to the beginning of this derivative, even if they are not part of current delivery

### Agent report (2026-11-10T13:25:36.223Z)

I created **`qcom-collapsed-full`**, which removes every add/revert pair back to `Ubuntu-7.0.0-44.44`. Its final tree is byte-identical to `master-next`. I didn't build the intermediate commits, so some may not compile on their own.

| Branch | Commits over 44.44 | `Revert "` commits |
|---|---|---|
| `master-next` | 1814 | 262 |
| `qcom-collapsed` | 1443 | 76 |
| **`qcom-collapsed-full`** | **1298** | **10** |

**What was removed (151 commits):**
- **69 add/revert pairs** found by matching subjects.
- **The rb3gen2 tsens pair:** the revert's subject is a shortened version of the original's.
- **Two lpass groups:** one revert undoes 4 commits (`lpass-lpi PM clock`), the other undoes 3 (`wsa/va-macro PM clock`).
- **The `QCLINUX: defconfig: Crypto/ICE` pair:** missed at first because the revert's subject starts with a prefix.
- **`UBUNTU: SAUCE: purwa-iot-evk: adapt TSENS override`** was dropped as well. It only adapted a FROMLIST commit that was collapsed, so it became empty.

**What remains:**
- **3 reverts of commits in the base** (BACKPORT/WORKAROUND/FROMLIST `Revert "..."`). They aren't pairs within the derivative, so they stay.
- **7 reduced reverts.** 20 of the pairs weren't exact inverses because Qualcomm's reverts carried extra changes. For each one I dropped the original commit and, at the revert's position, put the affected files back to their original post-revert state. In 7 cases a real difference was left over, so that revert stays as a smaller commit with its original author and a "Collapsed history" note added to the message. Without them the tree wouldn't match:
  - agatti FastRPC
  - venus/iris switch
  - iris platform split
  - "Synchronize with Qualcomm config"
  - PCIe PARF PHY
  - monaco SDHCI overlay
  - iris hwmode

**Conflicts:** they only came up in the iris/venus series, the qcom dts `Makefile` and `purwa-iot-evk.dts`. I kept the HEAD side and let the restore step at each revert's position correct the files.

This rewrites history below earlier release tags. To adopt it, run `git branch -f master-next qcom-collapsed-full`. `master-next`, `qcom-20261030` and `qcom-collapsed` are unchanged, and the temporary files and worktree are cleaned up.

---

## User request (2026-11-10T14:58:52.230Z)

> check if these leftover changes are not a result of some previous conflict resolutions that included unnecessary changes into the commit, and should've never been put there.

---

## User request (2026-11-10T15:24:43.606Z)

> 

### Agent report (2026-11-10T15:24:53.855Z)

Yes, mostly. Only one of the 7 leftovers (agatti) is a genuine change. Five come from earlier conflict resolutions or wholesale "sync" SAUCE commits that changed more than they should have. The PCIe one comes from a Qualcomm revert we imported unchanged. Three of the over-broad resolutions removed real features, and that loss is in the final tree (`master-next`), not just in the history. I only analysed this time; no branches were changed.

**Unnecessary changes that cost real features:**
1. **venus (flip venus/iris switch):** our port of this FROMLIST commit (1006.8) replaced the whole `venus_dt_match` table. That dropped the generic Ubuntu SAUCE `sc8280xp-venus` and `sm8350-venus` entries (X13s) and undid the SAUCE trailing-comma cleanup. Our port of the revert (1009.12) also removed the SAUCE `sc8280xp_res` struct. In `master-next`, sc8280xp venus support is gone and `sm8350_res` is defined but never used.
2. **config (Synchronize with Qualcomm config):** Qualcomm's original only adds 76 lines. Our port (1010.13) also deleted 13 of our own annotations. Still lost in `master-next`:
   - `CLK_X1E80100_CAMCC/DISPCC/GPUCC=y`, the TLB-sync deadlock fix. They now fall back to generic `m`.
   - `SCMI_QCOM_MEMLAT_DEVFREQ=m`, now `y`.

   This has been in the released kernels since 1010.13.
3. **iris (split platform data from firmware data):** Qualcomm's original revert deletes `iris_platform_sm8550.h`, `iris_platform_vpu3x.c` and `iris_platform_vpu4x.c`. Our port dropped those deletions, leaving unbuilt orphan files until the FROMGIT re-apply reused them. Nothing is lost in the final tree.

**Harmless leftovers:**
- **monaco SD-card overlay:** our port of the FROMLIST commit also deleted the `monaco-evk-ifp-mezzanine.dtbo` install line. The revert put it back in a different place, so the only leftover is a moved Makefile line.
- **iris hwmode:** the add and revert are exact inverses. The leftover comes from our wholesale SAUCE commits: "add iris driver at qcom-next tip" deleted the hwmode lines and "sync to qcom 20260717" re-added them.
- **PCIe:** "Set max OPP" is already upstream in the base (`9ac0948c4508`, since 40.40). Qualcomm's revert was written against an older base, and we imported it unchanged, so it also strips the upstream helper. The later FROMLIST v2 re-adds it, and the final tree is correct.
- **agatti FastRPC:** a deliberate revert of upstream `851d5ae6003b` because RB1 fails to boot. It is correct and should stay.

**Defects in my `qcom-collapsed-full` branch:**
- The PCIe leftover is filed under the wrong revert ("Power down PHY" instead of "Set max OPP").
- My keep-HEAD conflict auto-resolution stripped the Makefile lines from the monaco eMMC overlay commit, and the residual re-adds them.
- Several iris commits in between are not consistent on their own.

The final tree is still identical to `master-next`.

Decisions for you:
- Should I restore the venus SAUCE entries and the x1e80100/MEMLAT configs?
- Should I rebuild `qcom-collapsed-full` with those leftovers folded into the right commits?

---

## User request (2026-11-10T15:32:47.904Z)

> create a new branch that will contain rebuilt qcom-collapsed-full and leftovers folded in. Also show me what would be the change for the config an annotations

### Agent report (2026-11-10T15:42:57.353Z)

I created branch **`qcom-collapsed-folded`** (tip `78c2f59ea41e`, 1292 commits). It is `qcom-collapsed-full` rebuilt with the 6 leftover residual reverts folded into the commits they belong to. Its tree is byte-identical to `master-next`, the rebase ran without conflicts, and the drivers I touched compile at the tip. `master-next` is checked out again.

**What got folded** (each target commit keeps its author and message, plus a `[ Collapsed history … ]` note):

| Residual | Folded into |
|---|---|
| venus switch (lost `sc8280xp`/`sm8350` entries) | FROMGIT "flip the venus/iris switch". I also took the venus hunk out of "Fix multiple C source build errors", which had broken venus there. |
| iris platform split (orphan files) | FROMGIT "split platform data from firmware data". The iris SAUCE "add iris driver at qcom-next" no longer adds those platform files to the iris Makefile before they exist. |
| config sync | "Synchronize with Qualcomm config". The lost annotations now show up in this one commit. |
| PCIe "set max OPP" | FROMLIST "Set max OPP…". It now only carries the change on top of upstream `9ac0948c4508`. |
| monaco | the eMMC overlay commit, which has its Makefile lines back. |
| iris hwmode | SAUCE "sync to resolute-qcom-20260717". |

**Checks**
- Only 4 commits titled `Revert "…"` remain: agatti FastRPC (its note reworded) and the 3 reverts of commits in the base.
- No conflict markers, and the empty commits are the same 33 that were there before.
- The iris Makefile and source files now match at every commit.
- Three intermediate commits still fail to compile: the iris SAUCE, the iris split and PCIe "Set max OPP" (a field that only a later commit adds). These failures already exist in `master-next`/`qcom-collapsed-full`; folding didn't cause them.

**Config/annotations change (proposed, not applied).** Saved as `files/0001-restore-x1e80100-clk-memlat-annotations.patch` in the session folder; it applies to `master-next` and the new branch alike, and the annotations script parses it fine.

```
+CONFIG_CLK_X1E80100_CAMCC   policy<{'arm64': 'y'}>  + note 'builtin: apps_smmu camera TBU GDSC … TLB-sync deadlock'
+CONFIG_CLK_X1E80100_DISPCC  policy<{'arm64': 'y'}>  + note 'builtin: apps_smmu display TBU GDSC …'
+CONFIG_CLK_X1E80100_GPUCC   policy<{'arm64': 'y'}>  + note 'builtin: GPU TBU GDSC …'
-CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ  policy<{'arm64': 'y'}>
+CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ  policy<{'arm64': 'm'}>
```

The resulting config change is CAMCC/DISPCC/GPUCC going from m to y and MEMLAT going from y to m.

- **Already shipped:** this regression is in our 1016.20 and 1018.22 releases, and also in Qualcomm's tree.
- **`SM_VIDEOCC_8550`:** it survived as `y`, so it isn't in the patch.
- **Left alone:** `CLK_SHIKRA_AUDIOCORECC`=m, `QCOM_CLK_GP_MND`=n and `QCOM_SPEL`=m already match Qualcomm's own config.

---

## User request (2026-11-10T15:49:54.848Z)

> save all info about this session plus all artifacts generated in this directory. /home/kuba.pawlak@canonical.com/qualcomm/ai_effort/november_patchset_rebase. You can get rules about saving data and such in a parent directory of that one.
