# November Qualcomm patchset rebase (resolute-qcom-20261030)

Status: **ported to `master-next`; three collapsed history alternatives built
(none adopted yet); config regression found and a fix patch proposed.**
There is no Launchpad bug for this work yet.

## Context

| Item | Value |
|------|-------|
| Kernel tree | `~/qualcomm/resolute/linux-qcom/linux-main` (Resolute, `debian.qcom`) |
| Generic base | `Ubuntu-7.0.0-44.44` (`master-next` was already rebased onto it) |
| Previous qcom release | `Ubuntu-qcom-7.0.0-1018.22` (on generic `Ubuntu-7.0.0-43.43`) |
| `master-next` before port | `f54e8d58bc85`: 1018.22 rebased on 44.44, 1394 commits over base |
| Qualcomm delivery repo | `~/qualcomm/ubuntu-qcom-kernel`, tag `resolute-qcom-20261030` |
| Delivery base | our tag `Ubuntu-qcom-7.0.0-1016.19` (we ship 1016.20, which is nearly identical) |
| Delivery range | `Ubuntu-qcom-7.0.0-1016.19..resolute-qcom-20261030`, 434 commits |

## Branches (in the kernel tree, local only, nothing pushed)

All branches have the **same final tree**
(`54e555182043e24408f2d08d9808527a2074ed16`). They differ only in history.

| Branch | Tip | Commits over 44.44 | Subjects containing `Revert "` | Description |
|--------|-----|--------------------|--------------------|-------------|
| `master-next` = `qcom-20261030` | `848e9209e9e3` | 1814 | 262 | Delivery ported on top of the old `master-next` |
| `qcom-collapsed` | `fc9f9c8081d9` | 1443 | 76 | Pairs whose **revert** came in with this delivery removed (some of the adds were already shipped) |
| `qcom-collapsed-full` | `bc14ee337407` | 1298 | 10 | **All** add/revert pairs back to 44.44 removed; 7 reduced residual reverts left |
| `qcom-collapsed-folded` | `78c2f59ea41e` | 1292 | 4 | Residuals folded into the commits they correct (**recommended candidate**) |

A git worktree `linux-rebuild` (branch `master-next-rebuild`) and an
untracked `log.txt` in the kernel tree are unrelated to this effort.

## Phase 1: port the delivery

```bash
git remote add qc ~/qualcomm/ubuntu-qcom-kernel && git fetch qc --tags
git checkout -b qcom-20261030 master-next
git rebase --onto qcom-20261030 Ubuntu-qcom-7.0.0-1016.19 resolute-qcom-20261030 --empty=drop
```

- 420 of the 434 commits applied. About 14 were dropped as already present,
  because our 1016.20 and 1018.22 already contained them. See
  `lists/01_*` and `lists/02_*`.
- Hand-resolved conflicts:
  - `drivers/clk/qcom/dispcc-qcm2290.c`, `gpucc-qcm2290.c`: kept our SAUCE
    `use_rpm`, `RETAIN_FF_ENABLE`, the 0x1078 critical cbcr and
    `clk_rcg2_shared_ops`, and took Qualcomm's other changes.
  - `include/linux/soc/qcom/geni-se.h`, `drivers/tty/serial/qcom_geni_serial.c`.
  - ath12k `wmi.{c,h}`: the ML-STA auth-timeout commit is already upstream in
    our base, so it became empty and was dropped.
- The qcm2290 clk drivers build with `-Wdiscarded-qualifiers` warnings. Qualcomm's
  tree has the same warnings, so they weren't introduced by the resolution.
- `master-next` was then fast-forwarded to `qcom-20261030`.

## Phase 2: collapse the incoming reverts → `qcom-collapsed`

The delivery contains many reverts, mostly the old Shikra bring-up that was
reverted and re-sent. Each revert was paired with the most recent earlier
commit that has the same subject (`scripts/phase2_*`). An interactive rebase
then dropped both commits of each pair.

- 368 commits were dropped: 180 old Shikra add/revert pairs, the Advantech
  SOM-DB5830 add/revert and the 8-commit DSI panel chain. See `lists/03_*`.
- 3 orphan Shikra fixups became empty and were dropped: rpm_requests in
  glink-edge, IQS primary USB, and BT 3.3V dummy regulator.
- Commits with `Revert "` in the subject went from 262 to 76. The tree is identical to `master-next`.

## Phase 3: collapse all add/revert pairs back to 44.44 → `qcom-collapsed-full`

This phase was aggressive: it also removed pairs from earlier deliveries that
we had already shipped (`scripts/phase3_*`).

- Removed:
  - 69 subject-matched pairs;
  - the rb3gen2 tsens pair, whose revert subject is truncated;
  - 2 squashed lpass groups (4+1 and 3+1 commits), found by
    exact-inverse patch matching (`inv.py`);
  - the `QCLINUX: defconfig: Crypto/ICE` pair, removed in a second pass
    (`seq3.py`).
- The purwa TSENS SAUCE commit became empty.
- Some pairs didn't net to zero because later commits had been
  conflict-resolved against them. For those, `residual.py` committed a
  **reduced revert** at the revert's position, so the tree is unchanged at the
  end of the series. 7 such residuals remain. See `lists/04_*` and
  `lists/07_*`.
- The 3 reverts of base commits stay, because they are real Ubuntu or
  Qualcomm decisions:
  - `BACKPORT: Revert "drm/msm/dpu: try reserving the DSPP-less LM first"`;
  - `WORKAROUND: Revert "PCI: dwc: Remove MSI/MSIX capability ..."`;
  - `FROMLIST: Revert "pinctrl: qcom: x1e80100: Bypass PDC wakeup parent for now"`.

## Phase 4: where the 7 residual reverts come from

(`scripts/phase4_*`, blame and pickaxe attribution)

| Residual (on `qcom-collapsed-full`) | Root cause | Verdict |
|---|---|---|
| `d19c87958d2c` Revert agatti FastRPC | Genuine revert of base commit `851d5ae6003b` (RB1 fails to boot) | Keep (reworded) |
| `e7477d371747` venus flip switch | Our 1006.8 port dropped the Ubuntu SAUCE `sc8280xp`/`sm8350` venus entries and a trailing-comma cleanup; the 1009.12 revert port then removed `sc8280xp_res` | **Bad conflict resolution; the loss is in the final tree** |
| `1180d071b9a1` Synchronize config | The 1010.13 port of "Synchronize with Qualcomm config" deleted our annotations x1e80100 CAMCC/DISPCC/GPUCC=y and SCMI_QCOM_MEMLAT_DEVFREQ=m | **Bad conflict resolution; the loss is in the final tree and shipped in 1016.20 and 1018.22** |
| `ebbbee012e93` iris split | The revert port left orphan files | Resolution artefact |
| `5da6242d2711` monaco SD overlay | A Makefile line was moved; the keep-HEAD resolution stripped the eMMC commit's Makefile lines | Resolution artefact |
| `ea568187f450` iris hwmode | Caused by the wholesale "sync to qcom tag" SAUCE commits | Artefact of the SAUCE sync |
| `42f6ed8cfc09` PCIe PHY power down | Qualcomm's "Set max OPP" revert was written against an older base and also strips upstream `9ac0948c4508` | Qualcomm-side artefact |

## Phase 5: fold the residuals → `qcom-collapsed-folded`

`scripts/phase5_fold`: a scripted `rebase -i` with no conflicts. Each fold
adds a `[ Collapsed history: ... ]` note to the message of the commit it
changes (`notes.json`). In the table, the left column is the residual's SHA on
`qcom-collapsed-full` and the right column is the new SHA on
`qcom-collapsed-folded`.

| Residual | Folded into |
|----------|-------------|
| venus `e7477d371747` | FROMGIT "flip venus/iris switch" `8fe7f374ea8e`; venus hunk removed from "Fix multiple C source build errors from rebase" `bb6a070b4326` |
| iris split `ebbbee012e93` | FROMGIT "split platform data from firmware data" `cb642cc4434a`; Makefile hunk removed from the iris SAUCE "add iris driver at qcom-next-7.1-rc6 tip state" `2c7690f60af1` |
| config `1180d071b9a1` | "Synchronize with Qualcomm config" (Yushan Li) `afeaf8d14b2c` |
| hwmode `ea568187f450` | SAUCE "sync to qcom tag resolute-qcom-20260717" `89742b364fd0` |
| PCIe `42f6ed8cfc09` | FROMLIST "Set max OPP before DBI access" `96d9b388623b` |
| monaco `5da6242d2711` | eMMC overlay commit `6edf593e0f91` |
| agatti `d19c87958d2c` | Kept; reworded to `f550d398f971` |

Verification of `qcom-collapsed-folded`:

- the tree equals `master-next^{tree}`;
- 4 commits with `Revert "` in the subject remain (the agatti revert and the 3 reverts of base commits);
- it has the same 33 empty commits as `qcom-collapsed-full`;
- there are no conflict markers;
- at every commit, the iris Makefile matches the iris source files
  (`irischk.sh`);
- at the tip, an arm64 build of venus, iris and pcie-qcom succeeds.

Some intermediate commits fail to build: the iris SAUCE, the iris split and the
PCIe "Set max OPP" commit. **They fail in exactly the same way on the
pre-fold branches**; for example, PCIe needs `skip_pwrctrl_off`, which a later
D3cold commit adds. The folding didn't introduce these failures.

## Config regression and proposed annotations patch

`patches/0001-restore-x1e80100-clk-memlat-annotations.patch` is **not
applied to any branch**. It applies on top of any of the 4 branches:

```text
CONFIG_CLK_X1E80100_CAMCC         -> 'y' (with note)   # was dropped in the 1010.13 port
CONFIG_CLK_X1E80100_DISPCC        -> 'y' (with note)
CONFIG_CLK_X1E80100_GPUCC         -> 'y' (with note)
CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ   'y' -> 'm'
```

- I validated it with `debian/scripts/misc/annotations --query`.
- `SM_VIDEOCC_8550=y` survived the bad port, so the patch doesn't touch it.
- Qualcomm's own tags also lack these CAMCC/DISPCC/GPUCC=y entries and have
  MEMLAT=y. Please tell Qualcomm, or they will re-introduce the problem.
- `SHIKRA_AUDIOCORECC=m`, `QCOM_CLK_GP_MND=n` and `QCOM_SPEL=m` in the new
  delivery match Qualcomm's config, so they are fine.

## Open items and recommendations

1. **Pick a history.** `qcom-collapsed-folded` is the cleanest. Adopting it
   with `git branch -f master-next qcom-collapsed-folded` rewrites history
   **below already-released tags** (1016.x and 1018.22). That needs a force
   push, so the team should agree to it first. All three collapsed branches
   rewrite history: even `qcom-collapsed` drops adds that are already in the
   old `master-next` (`f54e8d58bc85` isn't an ancestor). The lowest-risk
   option is to keep `master-next` as ported.
2. **Restore the venus SAUCE `sc8280xp`/`sm8350` entries.** They were lost in
   our 1006.8 port and are missing in every branch's final tree. Not fixed
   yet.
3. **Apply the annotations patch**, after deciding between MEMLAT `m` and `y`.
4. Intermediate build failures in the iris and PCIe series would break
   bisection. They come from the delivery itself.
5. The `-Wdiscarded-qualifiers` warnings in the qcm2290 dispcc and gpucc
   drivers come from Qualcomm's code.

## How to resume

- All branches are still in the kernel tree. To check that a branch's tree
  is intact:
  `git rev-parse <branch>^{tree}` should print `54e555182043e24408f2d08d9808527a2074ed16`.
- To redo a collapse after a new delivery, regenerate the drop lists with the
  phase 2 and 3 scripts. Their `/tmp/*.txt` and `/tmp/groups.json` inputs were
  not saved; regenerate them by running `pair2.py`, `pair3.py`, `inv.py` and
  `mkgroups.py` in order. Then drive the rebase as shown in
  `scripts/README.md`.
- `lists/` has the commit lists for every step. `session_log.md` has every
  user request and the full report for each step. `session_checkpoints/`
  holds the detailed working notes from the session.

## Directory contents

| Path | Content |
|------|---------|
| `README.md` | This file |
| `session_log.md` | User requests and per-step reports, in chronological order |
| `session_checkpoints/` | Copilot session checkpoints (detailed history, technical decisions) |
| `patches/` | Proposed annotations fix |
| `lists/01_delivery_commits_qcom_repo.txt` | 434 delivery commits (Qualcomm repo SHAs) |
| `lists/02_applied_on_master-next.txt` | 420 commits as applied on `master-next` |
| `lists/03_*`, `04_*`, `05_*` | Commits removed or folded at each collapse step (by subject multiset difference) |
| `lists/06_qcom-collapsed-folded_full_log.txt` | Full log of the recommended branch |
| `lists/07_reverts_left_in_qcom-collapsed-full.txt` | The 10 remaining reverts before folding |
| `scripts/` | Recovered scripts for each phase; see `scripts/README.md` |
