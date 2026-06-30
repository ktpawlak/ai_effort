# LP#2115485 — msm / msm_default state in ~/qualcomm/noble

Tree: `~/qualcomm/noble`, branch `cranky/qcom`, HEAD `0c9f54bf86a`.
(Worked from the working tree; `git log` is broken here — bad object alternates
pointing at `/home/kuba.pawlak@canonical.com/canonical/linux/.git/objects`.)

## Is the upstream `gpu_on` patch added?  → NO
- `grep -rn gpu_on drivers/gpu/drm/` = nothing.
- No `module_param(... gpu_on ...)`; upstream commit 3f179914 is NOT applied.
- BOTH folders still exist and BOTH are built:
  - `drivers/gpu/drm/Makefile`:
    `obj-$(CONFIG_DRM_MSM) += msm/`
    `obj-$(CONFIG_DRM_MSM) += msm_default/`
- No `qcom_graphics.conf` / `modprobe.d` `install msm ... gpu_on=0` rule exists
  (the comment-#7 runtime selector was a proposal, not landed).

So the tree still uses the **old dual-folder** scheme, not the single-driver
+module-param long-term fix.

## Identity of each folder (module name comes from each Makefile, line 164)
| Folder | Module | Role | GPU registration |
|---|---|---|---|
| `drivers/gpu/drm/msm/` | **`msm_display.ko`** | Adreno usecase (downstream-modified fork) | **removed** — `adreno_register()` / `add_gpu_components()` stripped; external proprietary KGSL (`ubuntu/qcom/graphics`, `msm_kgsl.ko`) drives the GPU |
| `drivers/gpu/drm/msm_default/` | **`msm.ko`** | Freedreno usecase (clean upstream) | **present** — registers GPU/Adreno for Mesa Freedreno |

Confirmed via `diff msm_default/msm_drv.c msm/msm_drv.c`: the `msm/` (Adreno)
variant deletes `adreno_register()`, `adreno_unregister()`, `add_gpu_components()`
and the `mdss_with_gpu_name` EPROBE_DEFER coordination — i.e. `msm/` is the one
"modified for Adreno", matching the ticket description.

## Drift still present (the bug's core complaint)
`diff -rq` = **23 differing files** (+ `.gitignore` only in `msm/`). Besides the
intended GPU-registration differences, a real out-of-sync fix exists only in the
Adreno fork and is missing from the clean upstream copy, e.g. in `msm_drv.c` the
metadata-realloc NULL-check (`new_metadata` temp + `-ENOMEM` path) is in `msm/`
but NOT in `msm_default/`. This is exactly the "fixes land in msm/ but not
msm_default/" drift the ticket is about.

## Which folder to remove
Goal (ticket): collapse to ONE driver and use the upstream `gpu_on` module_param.
The keeper must be the clean-upstream copy; the removal target is the
downstream-modified Adreno fork.

→ **Remove `drivers/gpu/drm/msm/` (the `msm_display.ko` Adreno fork).**
→ **Keep `drivers/gpu/drm/msm_default/` (the clean upstream `msm.ko`).**

Then, to finish the long-term fix:
1. Apply upstream commit 3f179914 (`gpu_on` module_param) to `msm_default/`.
2. Rename folder `msm_default/` → `msm/` (its module is already `msm.ko`).
3. Drop the second `obj-$(CONFIG_DRM_MSM) += msm_default/` line in
   `drivers/gpu/drm/Makefile`.
4. Replace the OOT KGSL/Adreno selection with the modprobe rule from comment #7
   (`modprobe msm gpu_on=0 && modprobe msm_kgsl` when an `*adreno1` pkg is
   installed; otherwise `modprobe msm`).

NOTE: until step 1-4 are done you cannot simply delete `msm/` — the Adreno
product currently *ships* `msm_display.ko` from it. Removal is only safe once the
`gpu_on` patch lands so `msm.ko` can serve both Freedreno and Adreno.

---

# PART 2 — Porting the missing patches into msm_default (~/qualcomm/noble)

## Method & repo caveat
- Repo git object store is **partially broken** (pre-existing, not introduced by
  me): `.git/objects/info/alternates` points to a non-existent path
  `/home/kuba.pawlak@canonical.com/canonical/linux/.git/objects`, so a few base
  blobs are unreadable (`fatal: unable to read <sha>`). Most objects ARE local
  and readable, so `git log/show -- <path>` since the fork works.
- Fork commit: `b0f5309581e QCLINUX: duplicate msm/ to msm_default/`.
- Module identity: `msm/` builds `msm_display.ko` (Adreno), `msm_default/` builds
  `msm.ko` (Freedreno / clean upstream).

## Intentional divergence (MUST NOT be ported)
Defined by the "support adreno" + dereg commits; lives ONLY in two files:
- `Makefile`: `obj-$(CONFIG_DRM_MSM) += msm.o` (vs `msm_display.o`).
- `msm_drv.c`: msm_default KEEPS GPU registration for Freedreno —
  `add_gpu_components()`, `adreno_register()/adreno_unregister()`, plus the
  `mdss_with_gpu_name`/`gpu_added`/`card_created` EPROBE_DEFER "bind gpu to single
  dpu" logic (commit `6d3a434dc7d`).
- Excluded msm-side commit: `54d04568700 QCLINUX: Add support to compile
  msm_display.ko` (removes adreno_register from msm/ + renames module) — porting
  it would wrongly strip Freedreno GPU support from msm_default.

## Drift that was missing from msm_default and is now applied
Classification (dry-run, path-rewritten `git apply` reverse/forward check) showed
the historical drift had largely been folded in by the periodic
`UBUNTU: SAUCE: sync/resync msm_default from msm` commits, leaving a residual
drift across these files, now resolved so each is byte-identical to `msm/`:

  adreno/a2xx_gpu.c  adreno/a6xx_gmu.c  adreno/a6xx_gmu.h  adreno/a6xx_gpu.c
  adreno/a6xx_gpu_state.c  disp/dpu1/catalog/dpu_5_0_sm8150.h
  disp/dpu1/catalog/dpu_5_1_sc8180x.h  disp/dpu1/catalog/dpu_7_2_sc7280.h
  disp/dpu1/dpu_encoder_phys_cmd.c  disp/dpu1/dpu_encoder_phys_vid.c
  disp/dpu1/dpu_encoder_phys_wb.c  disp/dpu1/dpu_hw_dsc.h  dp/dp_display.c
  dp/dp_drm.c  dsi/phy/dsi_phy_10nm.c  dsi/phy/dsi_phy_7nm.c  msm_drv.c (drift
  hunk only — GPU-reg preserved)  msm_gem.c  msm_gem.h  msm_gem_submit.c
  msm_gpu_devfreq.c  msm_kms.c
(full list: msm_default-resynced-files.txt)

The last genuinely-missing patch I applied explicitly:
- `fda3bc3ca11 drm/msm/a2xx: stop over-complaining about the legacy firmware`
  → added the guard `&& !a2xx_gpu->protection_disabled` in
  `msm_default/adreno/a2xx_gpu.c` (the only hunk that had not been synced).

## Final verified state
- `diff -rq drivers/gpu/drm/msm drivers/gpu/drm/msm_default` → differences ONLY in
  `Makefile` and `msm_drv.c`, and BOTH are 100% intentional (msm_drv.c: 22 lines,
  all msm_default-only GPU registration; **0 lines present in msm are missing from
  msm_default**).
- `msm/` source tree is unmodified (clean vs HEAD); only `msm_default/` changed
  (22 files, working-tree, uncommitted).
- `.gitignore` exists only under `msm/` (build artifact ignore) — harmless.

## Suggested commit (not done — left for review)
Commit the 22 working-tree changes as:
  `UBUNTU: SAUCE: resync msm_default from msm for <release>`  (BugLink LP#2115485)
The intentional Makefile/msm_drv.c divergence is already in place, so no
"support adreno" follow-up commit is needed this cycle.

---

# PART 3 — Commit

- Repaired the broken object store: repointed
  `~/qualcomm/noble/.git/objects/info/alternates` from the missing
  `/home/kuba.pawlak@canonical.com/canonical/linux/.git/objects` to the local
  sibling clone `/home/ubuntu/qualcomm/kernel2/.git/objects` (which holds the
  same base objects). This was required because committing builds a full tree
  object that references repo-wide blobs (e.g. LICENSES/dual/Apache-2.0).
- Committed the 22-file resync:
  - HEAD `ef82af4e8918` "UBUNTU: SAUCE: resync msm_default from msm for -1078"
  - Author: Kuba Pawlak <kuba.pawlak@canonical.com> (BugLink LP#2115485)
  - 22 files changed, 812 insertions(+), 114 deletions(-)
- Post-commit invariant verified: `diff -rq msm msm_default` shows only the
  intentional `Makefile` + `msm_drv.c` differences; working tree clean.
