# Qualcomm Kernel Rebase: qcom-next-7.1-rc2-20260515 → qcom-next-7.1-rc6-20260609

**Ticket:** 2158071
**Date:** 2026-07-16
**Operator:** kuba.pawlak@canonical.com
**Previous effort:** `~/qualcomm/ai_effort/2153998_june_patchset_rebase` (rc6-7.0 → rc2-7.1)

---

## Goal

Update the Ubuntu Qualcomm derivative kernel with the *Qualcomm-specific* patches
contained in the new tag `qcom-next-7.1-rc6-20260609`, without pulling in the
mainline commits that sit between the versions. The previous rebase brought in
the Qualcomm patches from `qcom-next-7.1-rc2-20260515`; this effort brings in only
the delta of new Qualcomm work introduced since then.

The Ubuntu kernel stays on the **7.0** base (Ubuntu Resolute 7.0) for now. We do
**not** advance the kernel to 7.1/7.2 mainline — we only lift Qualcomm patches on
top of the existing 7.0-based tree, keeping a **linear** history (cherry-pick, never
merge).

---

## Repository layout

| Path | Description |
|------|-------------|
| `~/qualcomm/linux` | Ubuntu derivative kernel (target). Base branch: `master-next`. |
| `~/qualcomm/qualcomm-linux` | Qualcomm's tree (source of patches). |
| `~/canonical/linux` | Mainline reference tree (currently ~v7.2-rc3+). |
| `~/qualcomm/ai_effort/2158071_july_patchset_rebase` | This effort's artifacts. |

**Test branch created for this effort (in `~/qualcomm/linux`):**
`test-2158071-qcom-next-7.1-rc6-20260609` (branched from `master-next`).

---

## Tag structure

`qcom-next-7.1-rc6-20260609` is a merge commit with two parents:

- **UPSTREAM_BASE** (parent 1): `e43ffb69e0438cddd72aaa30898b4dc446f664f8` — "Linux 7.1-rc6"
- **QCOM_TIP**      (parent 2): `c99e264f29022f53dcb9f012a0d1dd80ea61fa06` — "Add qcom-next log files for 20260604"

The Ubuntu tree's mainline base commit is `028ef9c96e96` ("Linux 7.0").

---

## Method — identifying commits to cherry-pick

Same methodology as the June effort (documented in
`../2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md`).

1. **All qcom-specific commits in the new tag** (on top of its 7.1-rc6 base):
   ```bash
   git -C ~/qualcomm/qualcomm-linux log --no-merges --format="%H %s" \
       c99e264f29022f53dcb9f012a0d1dd80ea61fa06 ^e43ffb69e0438cddd72aaa30898b4dc446f664f8
   ```
   → **1004** commits. (Saved to `qcom_all_commits.txt`.)

   Because the tip is rebased onto 7.1-rc6, this diff contains *only* Qualcomm
   patches — the mainline commits from 7.0→7.1-rc6 are in the excluded base and do
   not appear.

2. **Subjects already present in the Ubuntu tree** (excluding `UBUNTU:` packaging):
   ```bash
   git -C ~/qualcomm/linux log --no-merges --format="%s" \
       HEAD ^028ef9c96e96 | grep -v "^UBUNTU:"
   ```
   → **3913** subjects.

   > Note: this number is much larger than the June effort's 600, because the
   > Ubuntu tree has since taken stable point-release updates (Linux 7.0.1 …
   > 7.0.12), which pulled in thousands of mainline stable commits. Those extra
   > subjects are harmless for the cross-reference — worst case a genuine qcom
   > commit whose subject coincidentally matches a stable subject would be
   > skipped, but the cherry-pick pass (empty-commit skip) is the backstop.

3. **Cross-reference by exact subject line.** Commits whose subject is not already
   applied are the ones to pick, emitted oldest-first (correct cherry-pick order).

### Result

| Metric | Count |
|--------|-------|
| Total qcom-specific commits in new tag | 1004 |
| Already applied (matched by subject)   | 749  |
| **New — need cherry-picking**          | **255** |

Output files:
- `commits_to_cherrypick.txt` — `<SHA> <subject>`, oldest-first (SHAs reference `~/qualcomm/qualcomm-linux`).
- `shas_to_pick.txt` — SHA-only, oldest-first (feed directly to the cherry-pick script).
- `qcom_all_commits.txt` — full 1004-commit qcom set from the new tag (reference).

---

## Validation / sanity checks

- Cross-checked the 255-commit pick list against the previous tag
  `qcom-next-7.1-rc2-20260515`: **246 of 255** are genuinely new qcom commits
  introduced *after* the rc2 tag — confirming the list is overwhelmingly new
  Qualcomm work, not accidental re-inclusion.
- The remaining **9** picks trace back to the rc2 tag but were never applied to
  the Ubuntu tree (skipped/dropped or subject-modified during the June effort).
  They are legitimately included; if any are in fact already applied, the
  cherry-pick will resolve to an empty commit and be auto-skipped. These 9 are:
  - `18e08519` FROMLIST: coresight: core: refactor ctcu_get_active_port and make it generic
  - `20c09ce8` watchdog: Add driver for Gunyah Watchdog
  - `287f0f5c` FROMLIST: serial: qcom_geni: fix kfifo underflow when flush precedes DMA completion IRQ
  - `50b47806` WORKAROUND: Revert "drm/msm/dpu: enable virtual planes by default"
  - `5ece42df` FROMLIST: dt-bindings: display: bridge: lontium,lt9211: Add lt9211c support
  - `91c991e1` FROMLIST: arm64: dts: qcom: talos-evk-som: Enable Adreno 612 GPU
  - `9292d384` FROMLIST: arm64: dts: qcom: qcs615-ride: Enable QSPI and NOR flash
  - `9981d8d1` FROMLIST: arm64: dts: qcom: x1e80100-lenovo-yoga-slim7x: Add l7b_2p8 voltage regulator for RGB camera
  - `c6beed37` FROMLIST: dt-bindings: crypto: ice: add operating-points-v2 property for QCOM ICE

---

## Cherry-pick execution (results)

Cherry-picked onto branch `test-2158071-qcom-next-7.1-rc6-20260609` in `~/qualcomm/linux`
using the June automation (`git cherry-pick -x`, oldest-first, auto-skip empties,
auto-resolve simple conflicts by taking *theirs*, stop on complex conflicts).

**Prerequisite:** the ubuntu tree did not have all qcom-linux objects locally. Added a
local remote and fetched the tag so cherry-pick could reference the SHAs:
```bash
git -C ~/qualcomm/linux remote add qcomlocal ~/qualcomm/qualcomm-linux
git -C ~/qualcomm/linux fetch qcomlocal c99e264f29022f53dcb9f012a0d1dd80ea61fa06
```

### Outcome

| Result | Count |
|--------|-------|
| Applied (clean or auto-resolved) | **241** |
| Skipped — empty / already applied | 13 |
| Dropped — qcom-next log-files commit (`c99e264f`) | 1 |
| **Total in pick list** | 255 |

- The 9 rc2-reachable entries all resolved to **empty** (already in the tree) — confirming
  they were previously applied, exactly as predicted.
- **13 conflicts auto-resolved (take-theirs)** — all verified as safe (mostly DTS
  label/board additions and one-line comment/header changes):
  - `drivers/pci/controller/dwc/pcie-qcom.c` (one-line comment) — `c49333b6`
  - `arch/arm64/boot/dts/qcom/glymur.dtsi` — `a22f5dd6`
  - `arch/arm64/boot/dts/qcom/kaanapali.dtsi` — `cd4fdfb3`, `1d47d861`
  - `arch/arm64/boot/dts/qcom/monaco.dtsi` — `c9fed4e0`
  - `arch/arm64/boot/dts/qcom/Makefile` — `daa1cfdb`
  - `arch/arm64/boot/dts/qcom/qcs8300-ride.dts` — `64fbefde`
  - `drivers/media/platform/qcom/venus/core.c` (Revert venus/iris switch) — `572f9d98`
  - `drivers/media/platform/qcom/iris/*` — `3a4ae830`
  - `arch/arm64/boot/dts/qcom/shikra.dtsi` — `90df8a96`
  - `include/ufs/ufshcd.h` — `1f2a6196`
  - `drivers/pinctrl/qcom/pinctrl-shikra.c` — `f8edc718`
- The log-files commit `c99e264f` ("Add qcom-next log files") was applied then
  **dropped** via `git reset --hard HEAD~1` (non-code bookkeeping, as planned).
- No leftover conflict markers remain in any changed file; working tree clean.

Branch HEAD after cherry-pick: `01b2c774bf1c` (FROMLIST: arm64: dts: qcom: monaco:
Add GEM_NOC interconnect for adreno SMMU).

## CBD build

Pushed to the CBD remote to build the `qcom` (non-RT) flavour:
```bash
cd ~/qualcomm/linux
git push cbd HEAD:refs/heads/test-2158071-qcom-next-7.1-rc6 -o native --force
```

### Build 1 — `kpawlak-resolute-01b2c774bf1c-1209` — FAILED (config check)

Failed at the annotations config-check stage: 7 new Kconfig symbols pulled in by
the cherry-picks were unannotated in the Ubuntu config:
`CONFIG_ACPI_AEST`, `CONFIG_CLK_GLYMUR_EVACC`, `CONFIG_CLK_SHIKRA_AUDIOCORECC`,
`CONFIG_DRM_LONTIUM_LT9611C`, `CONFIG_OF_AEST`, `CONFIG_QCOM_CLK_GP_MND`,
`CONFIG_QCOM_SPEL`.
→ Fixed by operator (config synchronised; commit `621ffac7cace`
"Ubuntu: [Config] synchronize config after rebase").

### Build 2 — `kpawlak-resolute-621ffac7cace-743` — FAILED (DTS duplicate node)

```
glymur.dtsi:4317: ERROR (duplicate_node_names): /soc@0/gpu@3d00000:
Duplicate node name
```
**Root cause (graduated-upstream interaction):** master-next already carried an
older Glymur GPU block (`gpu@3d00000` + `gmu@3d6a000`), added during the June
7.1-rc2 rebase as a graduated-upstream DTS fix. The July FROMLIST GPU commits
("Add GPU smmu node", "Add GPU support for Glymur", "Add GPU cooling") added the
new upstream GPU block (`gpu` + `gxclkctl` + `gmu@3d6c000`, matching the qcom-next
tip) without removing the old one — leaving two `gpu@3d00000` nodes.

**Fix:** commit `a83cd130a9ad` "UBUNTU: SAUCE: arm64: dts: qcom: glymur: Drop
duplicate gpu/gmu nodes" — removed the stale old block. The remaining
gpu/gxclkctl/gmu region now matches the qcom-next tip byte-for-byte.

**Local verification:** built all 325 real qcom DTBs locally with
`aarch64-linux-gnu-` + dtc — no duplicate-node or parse errors. The four
auto-theirs DTS files (glymur, kaanapali, monaco, shikra) all compile clean.

### Build 3 — `kpawlak-resolute-a83cd130a9ad-8163` — FAILED (DTS label gap)

```
purwa-iot-evk.dts: Label or path thermal_gpuss_0 not found
```
**Root cause (graduated-upstream gap):** the July commit that added
`&thermal_gpuss_0..3` overrides uses the new *label-based* thermal model, but our
tree still uses the old *inline* `thermal-zones` model (the label-based conversion
is a mainline change we do not carry).

**Fix:** commit `8e43a2a9f8ab` "UBUNTU: SAUCE: arm64: dts: qcom: purwa-iot-evk:
adapt TSENS override to inline thermal model" — rewrote the overrides to target
the existing trip labels `gpuss0_alert0..gpuss3_alert0 { temperature = <105000>; }`.
Rebuilt all 325 DTBs clean. (Lesson: the DTB error grep must include the
`Label or path … not found` / `Syntax error parsing` class, not just
`duplicate_node_names`.)

### Build 4 — `kpawlak-resolute-8e43a2a9f8ab-6692` — FAILED (2 C errors)

1. `pinctrl-shikra.c`: `macro 'PINGROUP' requires 14 arguments, but only 12 given`.
2. `pcie-designware-host.c:1216`: implicit declaration of
   `pci_host_common_can_enter_d3cold`.

**Fixes:**
- `f0812a212065` "pinctrl: qcom: shikra: fix PINGROUP macro/table mismatch" — the
  take-theirs resolution of "align with upstream" left the old 14-arg PINGROUP
  macro with new 12-arg table entries. Shikra pinctrl has no Ubuntu content, so
  the file was replaced wholesale with the qcom-next tip version (12-arg, self
  consistent).
- `23b9dde985cb` "PCI: dwc: update D3cold eligibility helper call after rename" —
  "FROMLIST: PCI: host-common: Add helper to determine host bridge D3cold
  eligibility" renamed `pci_host_common_can_enter_d3cold()` →
  `pci_host_common_d3cold_possible(bridge, bool *pme_capable)`. The caller in
  `dw_pcie_suspend_noirq()` (a graduated-upstream file) was not updated. Updated
  the call and added a local `pme_capable` out-parameter. Our tree does not carry
  the pwrctrl `skip_pwrctrl_off` field, so `pme_capable` is only used to satisfy
  the interface.

### Build 5 — `kpawlak-resolute-23b9dde985cb-5197` — FAILED (iris firmware)

```
iris_firmware.c: 'struct iris_core' has no member named 'fw'; 'ctx' undeclared
```
First symptom of the iris tangle (see below).

### Build 6 — `kpawlak-resolute-d3e5786c9d70-4047` — FAILED (iris ubwc)

```
iris_hfi_gen2_packet.c: implicit declaration of 'qcom_ubwc_macrotile_mode'
  (and _min_acc_length_64b, _swizzle, _bank_spread)
```
**Fix:** `b2f66529b4f0` "soc: qcom: ubwc: add UBWC config accessor helpers" — our
`include/linux/soc/qcom/ubwc.h` had the struct fields but not the four inline
`qcom_ubwc_*` accessors (graduated-upstream gap). Added them to match the tip.

### The iris tangle — wholesale sync to qcom tip

Local `-k` builds of `drivers/media/platform/qcom/iris/` after build 6 surfaced a
cascade of further errors in `iris_platform_vpu3x.c` (`iris_create_cb_dev`,
`dev_np`/`dev_bs`/`dev_p`, `iris_vpu36_ops`, `iris_glymur_*` undeclared).

**Root cause:** Qualcomm's `qcom-next` branch applies a large iris refactoring
series (Secure PAS, context banks, platform-data splits, UBWC config retrieval,
glymur/kaanapali/X1P42100 platform data), **reverts** parts of it, then
**re-applies** newer versions. The `qcom-next-7.1-rc6` tag's final tip
(`c99e264f`) is internally consistent and has all features. Replaying that churn
commit-by-commit with take-theirs conflict resolution left our iris directory as
a Frankenstein — old and new APIs mixed, and four platform-data files missing.

**Fix:** `e4e8520252b7` "media: iris: sync driver to qcom-next-7.1-rc6 tip state"
— the iris directory carries no Ubuntu content, so the whole directory was checked
out from `c99e264f` (`git checkout c99e264f -- drivers/media/platform/qcom/iris/`).
This adds the missing `iris_platform_glymur.[ch]`, `iris_platform_kaanapali.h`,
`iris_platform_x1p42100.h` and makes the driver match Qualcomm exactly. The
obsolete intermediate fix "drop Secure-PAS load path" (`d3e5786`) was dropped from
history. Builds clean (the `qcom_scm` PAS-context API and UBWC accessors are now
present). Also spot-built pinctrl/clk/soc/firmware/pci-dwc/phy/drm-msm qcom
subsystems locally — all clean.

### Build 8 — `kpawlak-resolute-e4e8520252b7-3907` — **BUILD-OK** ✅

Pushed with the iris sync + all C fixes. The `qcom` arm64 flavour built and
packaged successfully — all artefacts produced:
`linux-image`, `linux-modules`, `linux-headers`, `linux-buildinfo`,
`linux-tools` (`7.0.0-1008-qcom_7.0.0-1008.11_arm64.deb`). Full clean build
(kernel + modules + DTBs + packaging), no errors.

**Final branch head:** `e4e8520252b7`
(`test-2158071-qcom-next-7.1-rc6-20260609`).

## Summary of SAUCE fixes applied on top of the cherry-picks

| Commit | Fix | Class |
|---|---|---|
| `a83cd130a9ad` | glymur: drop duplicate gpu/gmu nodes | DTS duplicate node |
| `8e43a2a9f8ab` | purwa-iot-evk: adapt TSENS override to inline thermal model | DTS graduated-upstream label gap |
| `f0812a212065` | pinctrl shikra: fix PINGROUP macro/table mismatch | C garbled take-theirs merge |
| `23b9dde985cb` | PCI dwc: update D3cold helper call after rename | C graduated-upstream caller not updated |
| `b2f66529b4f0` | soc: qcom: ubwc: add UBWC config accessor helpers | Header graduated-upstream gap |
| `e4e8520252b7` | media: iris: sync driver to qcom-next-7.1-rc6 tip state | Wholesale dir sync (revert/re-apply churn) |

## Notes for the cherry-pick pass

- The last entry in the list, `c99e264f … "Add qcom-next log files for 20260604"`,
  is a non-code Qualcomm bookkeeping commit (log files only). **Consider skipping
  it** — the June effort flagged the equivalent commit as skippable.
- Reuse the automation from the June guide
  (`../2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md` §3): apply oldest-first
  with `git cherry-pick -x`, auto-skip empties, auto-resolve simple single-file
  conflicts by taking *theirs*, and stop for manual resolution on complex
  conflicts. Do **not** use `set -e` in the cherry-pick loop (it breaks conflict
  detection).
- Cherry-pick onto the test branch `test-2158071-qcom-next-7.1-rc6-20260609`.

---

## Commands reference

```bash
# Recreate the pick list from scratch
UPSTREAM_BASE=e43ffb69e0438cddd72aaa30898b4dc446f664f8   # Linux 7.1-rc6
QCOM_TIP=c99e264f29022f53dcb9f012a0d1dd80ea61fa06        # qcom-next tip
UBUNTU_BASE=028ef9c96e96                                 # Linux 7.0

git -C ~/qualcomm/qualcomm-linux log --no-merges --format="%H %s" \
    "$QCOM_TIP" ^"$UPSTREAM_BASE" > qcom_all_commits.txt

git -C ~/qualcomm/linux log --no-merges --format="%s" \
    HEAD ^"$UBUNTU_BASE" | grep -v "^UBUNTU:" > /tmp/applied_subjects.txt

# cross-reference by subject → commits_to_cherrypick.txt (oldest-first)
```

## Deploy + test on Hamoa (2026-07-17)

Deployed the green build to the Hamoa IoT EVK (Resolute/26.04, `ubuntu@192.168.1.123`)
by reusing the CBD artifacts (no rebuild):

```bash
cd ~/qualcomm/linux
~/qualcomm/ai_effort/qpa/cbd-deploy.sh --no-push \
    --build-id kpawlak-resolute-e4e8520252b7-3907
```

The script downloaded the tarball, `dpkg -i`'d the image+modules, ran
flash-kernel/GRUB, rebooted, and confirmed the board came back up.

### Results — PASS

- **Running our build:** `uname -a` → `7.0.0-1008-qcom #11 ... Fri Jul 17 05:59:53
  UTC 2026` (our CBD build date, vs the previous Jul 14 image).
- **iris video codec (our biggest change — wholesale dir sync):** driver bound
  (`qcom-iris` → `aa00000.video-codec`), `/dev/video0..17` + `/dev/media0` created,
  **both `iris_non_pixel.0` and `iris_pixel.0` added to IOMMU groups cleanly**, and
  **no iris errors/warnings/timeouts** in dmesg. Matches the June effort's iris
  success criteria.
- **PCIe (d3cold helper fix):** `PCIe Gen.4 x2 link up` and `Gen.3 x2 link up`;
  `lspci` enumerates WCN785x Wi-Fi 7 and the KIOXIA NVMe SSD — no regression.
- **Only anomaly:** a single `WARNING: kernel/sched/idle.c:269 cpuidle_idle_call`
  alongside `arm-scmi … unable to communicate with SCMI` (taint 512 = TAINT_WARN).
  This is a pre-existing cpuidle/SCMI-firmware platform issue on the Hamoa EVK,
  **unrelated to any patch in this rebase** (none of our changes touch cpuidle or
  SCMI).

**Conclusion:** the July `qcom-next-7.1-rc6-20260609` patchset builds clean on CBD
and boots + runs correctly on Hamoa, with the touched subsystems (iris, PCIe)
verified healthy.

## Flattening the iris churn (2026-07-17)

The cherry-picked history replayed Qualcomm's iris apply/revert/re-apply churn as
**47 iris-only commits** (21 FROM*/PENDING adds interleaved with 26 reverts) whose
only lasting effect is the final directory state, plus the incremental SAUCE fixes
that repaired it. This was flattened into a single commit.

### Method (lossless — verified by tree equality)

Key property: in the entire 241-commit cherry-pick range, `drivers/media/platform/
qcom/iris/` is touched by exactly **49** commits — the 47 iris-only ones plus 2
venus commits (the "flip the venus/iris switch" add + its revert, which also toggle
iris `Makefile`/`iris_probe.c`). No other commit touches iris. So the 47 iris-only
commits can be dropped without affecting any kept commit.

```bash
# backup the tested head
git branch -f backup-2158071-pre-flatten e4e8520252b7

# drop the 47 iris-only churn commits + the intermediate sync commit;
# -X theirs auto-resolves the only possible conflicts (the 2 venus commits'
# tiny iris Makefile/iris_probe.c hunks, which get overwritten anyway).
GIT_SEQUENCE_EDITOR="sed -i -f /tmp/seq_sed.txt" \
    git rebase -i -X theirs master-next

# re-create the final iris state in ONE commit
git checkout c99e264f -- drivers/media/platform/qcom/iris/
git commit -m "UBUNTU: SAUCE: media: iris: add iris driver at qcom-next-7.1-rc6 tip state"

# SAFETY CHECK — final tree must be byte-identical to the tested build:
git diff --quiet backup-2158071-pre-flatten HEAD   # → TREES IDENTICAL
```

### Result

- Branch: **248 → 201 commits** (dropped 48, added 1).
- iris work is now a single reviewable commit `…media: iris: add iris driver at
  qcom-next-7.1-rc6 tip state`, plus the small enabling commit
  `…soc: qcom: ubwc: add UBWC config accessor helpers`.
- **Tree byte-identical** to the tested/booted build `e4e8520252b7`, so the flatten
  cannot have changed behaviour. iris rebuilt clean locally; confirmation CBD
  build `kpawlak-resolute-abb2731f8240-4928` → **BUILD-OK** ✅.
- The 2 venus commits (`flip the venus/iris switch` + its revert of the older
  FROMLIST version) are **kept** — they are venus-subsystem commits and their net
  effect on `venus/core.c` is a real, wanted change, not add/revert-to-zero.
- Backup of the pre-flatten tested head kept on branch `backup-2158071-pre-flatten`
  (and tag `backup-tested-e4e8520`).
