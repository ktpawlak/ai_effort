# Runtime CSIPHY/Camera Fixes — Squash Analysis & Action Log

Date: 2026-06-16
Tree: `~/qualcomm/linux`, branch `master-next`
Context: Post-rebase runtime debugging fixes for the x1e80100 Hamoa IoT EVK
camera (CSIPHY) bring-up. Three hand-written fixes were sitting just below the
`Ubuntu-qcom-7.0.0-1006.8` release commit. Goal: see whether they can be
squashed / whether upstream or qcom-next already fix the same issues.

---

## 1. The three runtime fixes (as they were)

```
ec004b2463ae UBUNTU: Ubuntu-qcom-7.0.0-1006.8           (release tag, GPG-signed)
3a41e7a222a0 phy: qcom-mipi-csi2-phy: Fix x1e80100 clock names to match DTS   (#1)
2a297533b90e media: qcom: camss: csiphy: Remove obsolete PHY_TYPE_DPHY check  (#2)
e4592a1d5d4a arm64: dts: qcom: hamoa: Disable spi18 and fix qupv3_2 fw-name   (#3)
d889fdcb6ca9 UBUNTU: [Config] Synchronize with Qualcomm config
```

| Fix | File(s) touched | Root cause |
|-----|-----------------|-----------|
| #1 `3a41e7a222a0` | `drivers/phy/qualcomm/phy-qcom-mipi-csi2-3ph-dphy.c` | Driver `x1e_clks[]` declared 4 clocks (`camnoc_axi`, `cpas_ahb`, `csiphy`, `csiphy_timer`); DTS only provides 2 (`core`, `timer`). `devm_clk_bulk_get()` → `-ENOENT` → PHY never probes → `acb7000.isp` stuck in deferred probe. |
| #2 `2a297533b90e` | `drivers/media/platform/qcom/camss/camss-csiphy.c` | Driver compared the `#phy-cells` arg `PHY_QCOM_CSI2_MODE_DPHY` (= 0) against `PHY_TYPE_DPHY` (= 10) from generic phy.h → always false → probe failed with `csiphy1 mode 0 not supported`. |
| #3 `e4592a1d5d4a` | `arch/arm64/boot/dts/qcom/hamoa-iot-evk.dts`, `hamoa-iot-som.dtsi` | spi18 (MCP2518FD CAN, SE2 of QUPv3_2) triggers a TZ-firewall sync external abort; qupv3_2 firmware-name removed. **Unrelated to camera.** |

---

## 2. Squash analysis

- **#1 and #2** both fix the *same functionality* (x1e80100 CSIPHY/camera
  bring-up) but live in **different subsystems** (`drivers/phy/` vs
  `drivers/media/`) and fix **different root causes**. They are logically one
  "camera bring-up" change → good candidate to combine into a single commit.
- **#3** is unrelated (SPI/CAN TZ firewall) → must stay separate.

### Ideal-but-impractical option (rejected)
"Fixup" each runtime fix into the commit that *introduced* the buggy code:

| Fix | Introducing commit | Depth below HEAD |
|-----|--------------------|------------------|
| #1  | `8139ede946a1` FROMLIST + `9ec0d33065ed` SAUCE "Add CSI2 MIPI DPHY driver" | 748 / **1607** |
| #2  | `e4c683e07c7c` SAUCE "camss: Add support for PHY API devices" | **1601** |

The driver was in fact added **twice** (a `UBUNTU: SAUCE` copy *and* a
`FROMLIST` copy, 859 commits apart) — a real redundancy. But fixing up / 
collapsing at that depth means an interactive rebase replaying **~1600
commits**, which would:
- rewrite the SHA of the entire carefully-resolved rebase history,
- rewrite the signed `Ubuntu-qcom-7.0.0-1006.8` release tag + 3 commits below it,
- require a massive force-push to `lp/master-next` and `cbd/master-next`,
- risk re-triggering merge-conflict resolution across that whole span.

→ **Rejected** as not worth it for a cosmetic cleanup.

### Chosen option (done)
Combine **only** #1 + #2 into one commit at the top of the tree; leave #3 and
everything below untouched. Rewrites just the top ~2 commits (+ the release
commit's SHA).

---

## 3. Upstream / qcom-next equivalents

- **Fix #2 — yes, qcom-next already does this.** qcom-next's
  `camss-csiphy.c` (FROMLIST `6081f28dfe98` "Add support for PHY API devices")
  has **no** `PHY_TYPE_DPHY` / "mode not supported" check — it sets
  `csiphy->cfg.combo_mode = 0` directly. Our SAUCE copy
  (`e4c683e07c7c`) carried an obsolete check; fix #2 just realigns to qcom-next.

- **Fix #1 — qcom-next has correct clock names, but a DIFFERENT driver.**
  qcom-next's `phy-qcom-mipi-csi2-3ph-dphy.c` already uses
  `x1e_clks[] = {"core","timer"}` with `opp_clk = x1e_clks[0]`,
  `timer_clk = x1e_clks[1]`. **However** it is a *substantially different
  implementation*:
    - qcom-next: **genpd-based** (`genpd_names = {"mx","mmcx"}`), supplies
      `{"vdda-0p9","vdda-1p2"}`, no `clk_freq[]`, no `.generation = GEN2`,
      lowercase hex in lane reg tables.
    - ours: **clk_freq-based** (sets clock rates directly), supplies
      `{"vdda-0p8","vdda-1p2"}` (to match our DTS overlay),
      `.generation = GEN2`, extra `CSIPHY_*` defines, uppercase hex.
  Because the two drivers diverge, we **cannot** simply swap in the qcom-next
  version. Fix #1 is the correct minimal correction for *our* SAUCE variant.
  (Diff between the SAUCE and FROMLIST copies of this file = 114 ins / 94 del.)

- **Fix #3** — DTS workaround specific to our TZ firmware; no upstream fix
  (TZ-managed SE2/spi18 until TZ firmware grants non-secure access).

---

## 4. Action taken (2026-06-16)

Combined #1 + #2 into a single commit via scripted interactive rebase
(`reword` the camss fix, `fixup` the phy fix into it), base = `e4592a1d5d4a`
so #3 and below were untouched.

### Result
```
98ca9468179b UBUNTU: Ubuntu-qcom-7.0.0-1006.8                  (release, NEW sha)
75c8b050a516 media/phy: qcom: Fix x1e80100 CSIPHY camera bring-up   ← combined #1+#2
e4592a1d5d4a arm64: dts: qcom: hamoa: Disable spi18 ...             ← #3 (unchanged)
d889fdcb6ca9 UBUNTU: [Config] Synchronize with Qualcomm config
```

- Combined commit `75c8b050a516` touches **both** files
  (`camss-csiphy.c` −10, `phy-qcom-mipi-csi2-3ph-dphy.c` +4/−10).
- **Tree integrity verified**: `git diff backup-before-squash HEAD` is empty —
  the resulting source tree is byte-identical to before. Only history changed.

### Combined commit message
`media/phy: qcom: Fix x1e80100 CSIPHY camera bring-up` — documents both
sub-fixes (camss PHY_TYPE_DPHY check removal + phy clock-name correction),
each with its own root-cause paragraph. Retains
`Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.

### Safety backups (in `~/qualcomm/linux`)
- branch `backup-before-squash`
- tag    `backup-before-squash-20260616`

Rollback: `git reset --hard backup-before-squash`

---

## 5. Outstanding items (owner: Kuba — to do manually)

1. **Signed release tag**: `Ubuntu-qcom-7.0.0-1006.8` is GPG-signed and still
   points to the OLD orphaned release commit `ec004b2463ae`. It was **not**
   moved/recreated (cannot reproduce the signature). Regenerate the release /
   re-sign the tag with the normal kernel release tooling, or move it manually
   if the signature is not required.
2. **Force-push**: branch is now 2 ahead / 3 behind `lp/master-next` and
   `cbd/master-next`. Push with `git push --force-with-lease` (and the tag
   separately once regenerated).
3. (Optional, future) The deep duplicate SAUCE + FROMLIST phy-driver-add
   commits (`9ec0d33065ed` + `8139ede946a1`, 859 apart) remain. Collapsing
   them is only worthwhile if/when a larger history rewrite is acceptable.

---

## 6. Reference: key commits

| SHA | Role |
|-----|------|
| `75c8b050a516` | combined camera fix (new) |
| `98ca9468179b` | release commit after rebase (new sha) |
| `ec004b2463ae` | release commit before rebase (old; tag still points here) |
| `3a41e7a222a0` | original fix #1 (phy clock names) — now folded in |
| `2a297533b90e` | original fix #2 (camss PHY_TYPE_DPHY) — now folded in |
| `e4592a1d5d4a` | fix #3 (spi18 DTS) — left separate |
| `9ec0d33065ed` | UBUNTU: SAUCE phy driver add (1607 deep) |
| `8139ede946a1` | FROMLIST phy driver add (748 deep) |
| `e4c683e07c7c` | UBUNTU: SAUCE camss PHY API add (1601 deep) |
| qcom-next `6081f28dfe98` | qcom-next camss PHY API (no PHY_TYPE_DPHY check) |
| qcom-next `34a40528b0ff` | qcom-next phy driver (genpd-based, core/timer clocks) |
