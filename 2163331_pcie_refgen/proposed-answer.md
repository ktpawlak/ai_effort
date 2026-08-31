# Proposed reply — LP #2163331 ([Monza][RB4] PCIe5 issue needs Regulator L7A (refgen) vote)

> DRAFT ONLY — not posted to the bug. Reply to kadamski's comment #3.

---

Hi Sushrut, Adam,

I looked into this against the Noble qcom tree (6.8.0-1082) and upstream
(v7.2-rc4). A few findings:

**1. On Adam's question — the probe will not actually fail, but the `refgen`
entry is wrong.**

`devm_regulator_bulk_get()` uses `NORMAL_GET`. When a `*-supply` is absent from
the DT, and the platform has full regulator constraints (Monza does), the core
returns a *dummy* regulator with a `"supply <id> not found, using dummy
regulator"` warning rather than failing — it is not strictly all-or-nothing for
*missing* supplies (it is for `-EPROBE_DEFER`). We can see this already happening
on Monza today, e.g. `msm_dsi ...: supply refgen not found, using dummy
regulator`. So adding a bare `"refgen"` regulator would not break probe, but it
would silently bind a dummy and do nothing.

More importantly, **`refgen` is a PHY clock, not a regulator.** In both upstream
and our tree it only appears in the clock list
(`qmp_pciephy_clk_l = { "aux", "cfg_ahb", "ref", "refgen", "rchng", "phy_aux" }`,
fetched optionally via `devm_clk_bulk_get_optional()`). There is no `refgen`
regulator / `refgen-supply` anywhere in the QMP PCIe PHY driver upstream. So the
bare `"refgen"` string in the *regulator* list looks like a mix-up with the
clock of the same name. Only `"vdda-refgen"` (matching the new
`vdda-refgen-supply = <&vreg_l7a>` DT property) is correct. Good catch, Adam.

**2. The bigger issue: the patch does not update the config that pcie0 uses.**

Monza has two PCIe PHYs, bound to different configs:

| DT node      | compatible                        | driver cfg                        |
|--------------|-----------------------------------|-----------------------------------|
| `pcie0_phy`  | `qcom,qcs8300-qmp-gen4x2-pcie-phy`| `qcs8300_qmp_gen4x2_pciephy_cfg`  |
| `pcie1_phy`  | `qcom,sa8775p-qmp-gen4x4-pcie-phy`| `sa8775p_qmp_gen4x4_pciephy_cfg`  |

`qcs8300_qmp_gen4x2_pciephy_cfg` still uses `qmp_phy_vreg_l` = `{vdda-phy,
vdda-pll}` only — it was **not** updated by the earlier qref commit
(`c17bbf79 phy: qcom: qmp-pcie: Add qref regulator vote for QCS8300`, which
actually only touched `sm8450_gen3x1` and `sa8775p_gen4x4`) and it is not touched
by this patch either. Since the DT adds `vdda-qref-supply` / `vdda-refgen-supply`
to *both* `pcie0_phy` and `pcie1_phy`, but the driver only requests those
supplies for the `sa8775p_gen4x4` cfg, **the votes on `pcie0_phy` are inert** —
the driver never calls `regulator_get()` for them. Only `pcie1_phy` currently
gets qref, and (with this patch) refgen.

**3. Note on upstream / backport**

For the record, this is not a broken upstream backport — upstream (v7.2-rc4) has
no `vdda-qref` / `vdda-refgen` regulator scheme for these PHYs at all; the whole
qref/refgen regulator voting is downstream-only. So the fix has to be authored
directly downstream; there is no upstream commit to align to.

**Suggested fix** (patch attached / below):

- Add a dedicated `sa8775p_qmp_phy_vreg_l = {vdda-phy, vdda-pll, vdda-qref,
  vdda-refgen}` (drop the bare `refgen`).
- Point **both** `sa8775p_qmp_gen4x4_pciephy_cfg` **and**
  `qcs8300_qmp_gen4x2_pciephy_cfg` at it, so the DT votes are honoured for both
  Monza PHYs. This also closes the pre-existing gap where `pcie0`'s
  `vdda-qref-supply` was never being requested.
- Keep the DT additions of `vdda-refgen-supply = <&vreg_l7a>` on both phys.

One thing to confirm on your side: if "PCIe5" is specifically the
`sa8775p-gen4x4` (pcie1) instance and pcie0 does *not* need the vote, then an
alternative is to drop the `vdda-refgen-supply`/`vdda-qref-supply` lines from
`pcie0_phy` in the DT instead of extending the qcs8300 cfg — but given the DT
already declares them on both, honouring them in the driver (as above) seems the
intended behaviour.

Verified: the reworked driver change cross-compiles cleanly
(`drivers/phy/qualcomm/phy-qcom-qmp-pcie.o`, arm64).

Thanks!

---

## Corrected patch

See `0001-monaco-monza-Add-refgen-L7A-vote-for-PCIe-PHY.patch` in this folder.
Summary of the difference vs the originally attached patch:

- Regulator list contains only `"vdda-refgen"` (the bogus bare `"refgen"` is
  removed).
- `qcs8300_qmp_gen4x2_pciephy_cfg` (pcie0_phy) is *also* switched to the new
  list, so its DT `vdda-qref`/`vdda-refgen` supplies are actually requested.
- Does not touch `sm8350_qmp_gen3x2` / `sm8450_qmp_gen3x1` (unrelated SoCs whose
  boards have no `vdda-refgen-supply`; extending their lists would only add
  dummy-regulator warnings). Scope kept to the two configs Monza actually binds.
