<overview>
The user maintains the Ubuntu Resolute qcom derivative kernel in `~/qualcomm/resolute/linux-qcom/linux-main`. It is based on generic `Ubuntu-7.0.0-44.44`, and `master-next` holds Qualcomm's new delivery (`resolute-qcom-20261030`) ported on top. After porting the delivery and creating branches with the add/revert pairs collapsed, the user now wants:
- a new branch: a rebuilt `qcom-collapsed-full` with the 7 leftover "residual revert" commits folded into the commits they belong to;
- to be shown what the config/annotations change would be to restore our lost x1e80100 and MEMLAT configs.

Every rewritten branch must keep its final tree byte-identical to `master-next^{tree}` (`54e555182043…`).
</overview>

<history>
1. **Port the delivery onto `master-next`** (done earlier).
   - 420 commits applied with conflicts resolved, giving `master-next` = `qcom-20261030` = `848e9209e9e3` (1814 commits over 44.44).

2. **Collapse the incoming revert pairs** (done earlier).
   - Produced branch `qcom-collapsed` (1443 commits), tree identical to `master-next`.

3. **Aggressively remove all add/revert pairs back to 44.44.**
   - Built 72 groups (149 commits): 69 subject pairs, the tsens pair, and the 2 squashed lpass groups.
   - Rebased `qcom-collapsed` with a seq-editor that drops the group members and adds an `exec residual.py` after each revert. That script restores the pair's files to the revert's state (skipping files covered by other open pairs) and commits any leftover as a reduced revert with a "Collapsed history" note.
   - A driver auto-resolved conflicts only inside open-pair ranges: `git rm` when the file was deleted in HEAD, otherwise `git merge-file --ours` (keep HEAD). Auto-resolved commits:
     - `aed98cac8412` (venus core.c)
     - `b72ae16597cd` (monaco eMMC, qcom dts Makefile)
     - `76a946268e8e` (purwa SAUCE; became empty and was dropped)
     - `e6200feed4bd` (iris SAUCE wholesale commit: several files merged-ours, 4 files rm'd)
   - A second rebase then dropped the QCLINUX defconfig Crypto/ICE pair (`cfd4232d7a2c` + `ec51be060ba1`).
   - Result `qcom-collapsed-full` (tip `bc14ee337407`): 1298 commits, tree identical to `master-next`.
   - 10 `Revert "` commits remain: 3 legitimate reverts of base commits (BACKPORT dpu DSPP-less LM, WORKAROUND dwc MSI, FROMLIST pinctrl x1e80100 PDC) plus 7 residual reverts.

4. **Check whether the leftovers come from earlier bad conflict resolutions.** Analysis only, no branch changes.
   - **agatti FastRPC** (`d19c87958d2c`): legitimate. Qualcomm deliberately reverts upstream `851d5ae6003b` (in base) because RB1 fails to boot. Our add copy was empty. Keep it.
   - **venus** (`e7477d371747`): our port of FROMLIST "flip venus/iris switch" (1006.8) replaced the whole `venus_dt_match`. This dropped the generic Ubuntu SAUCE `sc8280xp-venus`/`sm8350-venus` entries and undid the trailing-comma SAUCE cleanup. Our port of the revert (1009.12) also removed the SAUCE `sc8280xp_res` struct. In `master-next`, sc8280xp venus is gone and `sm8350_res` is unused.
   - **config** (`1180d071b9a1`): Qualcomm's original "Synchronize with Qualcomm config" (`13e586d16c32`) only adds 76 lines. Our port (1010.13, `bef4ab230037` in `qcom-collapsed`) also deleted 13 of our own annotations. Still lost in `master-next`:
     - `CLK_X1E80100_CAMCC/DISPCC/GPUCC` `'y'` plus notes (TLB-sync deadlock fix, from Kuba's commit "UBUNTU: [Config] qcom: build x1e80100 camcc/dispcc/gpucc/videocc in"); they now fall back to generic `'m'`.
     - `SCMI_QCOM_MEMLAT_DEVFREQ` `'m'` (from "UBUNTU: [Config] change CONFIG_MEMLAT_DEVFREQ to m"); now `'y'`.
     - Also changed: `CLK_SHIKRA_AUDIOCORECC` y→m, `QCOM_CLK_GP_MND` y→n, `QCOM_SPEL` y→m. These may be later deliberate Qualcomm changes and are unverified.
   - **iris split** (`ebbbee012e93`): Qualcomm's original revert `bbeff782f164` deletes `iris_platform_sm8550.h`, `iris_platform_vpu3x.c` and `iris_platform_vpu4x.c`. Our port dropped those deletions, leaving orphan files that the FROMGIT re-apply later reuses.
   - **monaco** (`5da6242d2711`): our port of the SD-card FROMLIST commit also deleted the `monaco-evk-ifp-mezzanine.dtbo` line, which the revert re-added elsewhere. Separately, my keep-HEAD resolution stripped the eMMC commit's Makefile lines, and the residual re-adds them.
   - **hwmode** (`ea568187f450`): the add and revert are exact inverses. Our wholesale SAUCE commits ("add iris driver at qcom-next-7.1-rc6 tip state" removed the hwmode lines; "sync to qcom tag resolute-qcom-20260717 final state", `9c8f196e2c30` in full, re-added them) caused the leftover.
   - **PCIe** (`42f6ed8cfc09`, wrongly labelled as the "Power down PHY" revert): upstream `9ac0948c4508` "Set max OPP…" is in the base since 40.40. Qualcomm's revert of the FROMLIST "Set max OPP" (`cac48fed7a54`, imported verbatim, written against an older base) also strips the upstream helper. The FROMLIST v2 re-apply re-adds it.
   - Reported all of this and called `task_complete`.

5. **Current request:** create a new branch containing a rebuilt `qcom-collapsed-full` with the leftovers folded in, and show what the config/annotations change would be.
   - Started mapping fold targets in `qcom-collapsed-full`:
     - venus `e7477d371747` → FROMGIT re-apply `932cf444c7cd` (adjacent, 0 commits between)
     - iris split `ebbbee012e93` → FROMGIT `f5b461b66c45` (15 commits between, none touching the files)
     - config `1180d071b9a1` → re-sync `704e6d49d300` (adjacent)
     - PCIe `42f6ed8cfc09` → FROMLIST "Set max OPP" `e8bed273512d` (3 commits between touching `pcie-qcom.c`: get_ltssm, power down, D3cold)
     - monaco `5da6242d2711` → fold up into eMMC commit `d1d48e12c4c1` (its Makefile diff is currently EMPTY because of my artifact; 5 commits between touch the Makefile)
     - hwmode `ea568187f450` → fold up into sync SAUCE `9c8f196e2c30` (no commits between touch `iris_vpu_common.c`/`iris_vpu3x.c`)
     - agatti `d19c87958d2c`: keep as is.
</history>

<work_done>
Branches (all unchanged during the current request so far):
- `master-next` = `qcom-20261030` = `848e9209e9e3` (checked out; 1814 commits)
- `qcom-collapsed` = `fc9f9c8081d9` (1443)
- `qcom-collapsed-full` = `bc14ee337407` (1298, 10 `Revert "` commits)

Work completed:
- [x] Aggressive collapse branch `qcom-collapsed-full`
- [x] Analysis of the 7 leftovers (reported)
- [x] Mapped fold targets (above)
- [ ] Create new branch (suggested `qcom-collapsed-folded`) from `qcom-collapsed-full` with the leftovers folded in
- [ ] Show the proposed annotations change restoring the x1e80100 builtin clocks and MEMLAT `m` (show only, unless the user asks to apply)

Temp scripts in `/tmp` were all deleted. The untracked `log.txt` in the repo is pre-existing; don't touch it. A separate worktree `linux-rebuild` (branch `master-next-rebuild`) exists and is unrelated; don't touch it.
</work_done>

<technical_details>
- Base tag: `Ubuntu-7.0.0-44.44` (`3a2a0241367f`). History is linear. Validation: `git rev-parse <branch>^{tree}` must equal `master-next^{tree}` = `54e555182043e24408f2d08d9808527a2074ed16`.
- Rebase scripting:
  - Run: `GIT_SEQUENCE_EDITOR='python3 script.py' GIT_EDITOR=true git -c rebase.abbreviateCommands=false rebase -i --empty=ask <base>`. `--empty=stop` is unsupported.
  - Todo lines are `pick <abbrev> subject`; match them against full SHAs with `startswith`.
- Planned folding mechanics:
  - **Fold down** (residual R into later commit T): move `pick R` to just before T, change T to `fixup T`, then add `exec` of a helper. The helper amends with T's message, author and date (`git commit --amend -F msgfile --author="..." --date="..."`) and appends a note such as `[ Collapsed history: folded in leftover of the dropped <pair> … ]`.
  - **Fold up** (residual R into earlier X): insert `fixup R` right after X's pick, remove R's original line, and add `exec` to append a note to X's message.
  - Expect possible conflicts for PCIe (3 intermediate commits) and monaco (5 intermediate Makefile commits). Resolve manually and scan for conflict markers.
- monaco target state in the final Makefile: `ifp-mezzanine.dtb`, the eMMC block (`monaco-evk-emmc-dtbs := monaco-evk.dtb monaco-evk-emmc.dtbo` / `dtb-$(CONFIG_ARCH_QCOM) += monaco-evk-emmc.dtb`), `ifp-mezzanine.dtbo`, then the sd-card block. If the eMMC commit inserts its block between the `.dtb` and `.dtbo` lines, the residual disappears.
- Known remaining weakness: iris commits between `a3e151147322` (the SAUCE "add iris driver at qcom-next tip", originally `e6200feed4bd`) and the last iris revert are not consistent on their own, because of the merge-ours/rm resolutions. Folding doesn't fix this; mention it to the user.
- Config annotations file: `debian.qcom/config/annotations`. Original Ubuntu entries to restore:
  - `CONFIG_CLK_X1E80100_CAMCC policy<{'arm64': 'y'}>` + note `'builtin: apps_smmu camera TBU GDSC must register before built-in clients probe to avoid TLB-sync deadlock'`
  - `CONFIG_CLK_X1E80100_DISPCC` `'y'` + note `'builtin: apps_smmu display TBU GDSC …'`
  - `CONFIG_CLK_X1E80100_GPUCC` `'y'` + note `'builtin: GPU TBU GDSC …'`
  - These sit in the noted section after `CONFIG_ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT`.
  - `CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ policy<{'arm64': 'm'}>` (currently `'y'`).
  - The original Kuba commit (`faa28e669b46` in `qcom-collapsed`, `5487d2e21521` in `master-next`) adds 13 lines and mentions videocc too. Check its diff to include `VIDEOCC` if present.
  - The generic `debian.master` annotations has CAMCC/DISPCC/GPUCC at `'m'`.
  - To show the proposed change, generate the diff, e.g. by applying the relevant hunks of `5487d2e21521` and `3a9f75aea5af` (MEMLAT m) onto `master-next`'s annotations, and also show the resulting `debian.qcom/config` effect. Don't commit unless asked; optionally provide it as a patch or a separate commit.
- Qualcomm repo: `~/qualcomm/ubuntu-qcom-kernel` (tags `resolute-qcom-*`, `Ubuntu-qcom-7.0.0-10xx`). `git tag --contains` there is very slow; avoid it.
- `grep -c` returning 0 exits with 1, which breaks `&&` chains.
</technical_details>

<important_files>
- `debian.qcom/config/annotations`: the config regression area; the proposed restoration (x1e80100 CAMCC/DISPCC/GPUCC `y` + notes, MEMLAT `m`) must be shown.
- `drivers/media/platform/qcom/venus/core.c`: SAUCE `sc8280xp`/`sm8350` entries lost; venus residual folds into FROMGIT flip `932cf444c7cd`.
- `drivers/media/platform/qcom/iris/{Makefile,iris_platform_sm8550.h,iris_platform_vpu3x.c,iris_platform_vpu4x.c,iris_vpu_common.c,iris_vpu3x.c}`: iris split and hwmode residuals.
- `drivers/pci/controller/dwc/pcie-qcom.c`: PCIe residual (upstream `qcom_pcie_set_max_opp` helper open-coded) folds into `e8bed273512d`.
- `arch/arm64/boot/dts/qcom/Makefile`: monaco residual; the eMMC commit `d1d48e12c4c1` is currently missing its Makefile lines.
- `arch/arm64/boot/dts/qcom/agatti.dtsi`: the genuine agatti revert stays.
</important_files>

<next_steps>
1. Create branch `qcom-collapsed-folded` from `qcom-collapsed-full`, check it out, and run a scripted `rebase -i` with the fold plan:
   - venus `e7477d371747` → `932cf444c7cd` (fold down)
   - iris split `ebbbee012e93` → `f5b461b66c45` (fold down)
   - config `1180d071b9a1` → `704e6d49d300` (fold down)
   - PCIe `42f6ed8cfc09` → `e8bed273512d` (fold down; may conflict)
   - monaco `5da6242d2711` → `d1d48e12c4c1` (fold up; may conflict)
   - hwmode `ea568187f450` → `9c8f196e2c30` (fold up)

   Keep the target commits' author and message and append a `[ Collapsed history … ]` note describing the folded change. Resolve any conflicts manually.
2. Verify:
   - tree equals `master-next^{tree}`;
   - commit count (expected 1292) and that only 4 `Revert "` commits remain (agatti + 3 base reverts);
   - no conflict markers;
   - the eMMC commit now has its Makefile lines;
   - optionally compile-check the touched files with the arm64 cross toolchain.
3. Produce and show the proposed `debian.qcom/config/annotations` diff restoring x1e80100 CAMCC/DISPCC/GPUCC (and VIDEOCC if it was in the original commit) as `'y'` with notes, plus MEMLAT `'m'`. Show it only (e.g., save as a patch in the session files dir); don't apply it to branches unless asked.
4. Check out `master-next`, clean temp files, report (branch name, counts, fold notes, the remaining iris intermediate-inconsistency caveat, the config diff), then call `task_complete`.
</next_steps>