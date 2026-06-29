# Bug #2139069 — [RB4] wrong power-domain ID is used for rpmhpd

Launchpad: https://bugs.launchpad.net/carmel/+bug/2139069
Reported by: Masahiro Yamada, 2026-01-26
Status: Fix Committed (2026-06-25)
Importance: High
Assigned to: Kuba Pawlak (for visibility / future rebase awareness)
Milestone: ubuntu-24.04-x13
Tag: technical-debt, rb4

---

## What the problem was

In `arch/arm64/boot/dts/qcom/qcs8300.dtsi`, two *incompatible* sets of
power-domain constants were mixed together:

- Most `power-domains` entries use **`RPMHPD_*`** (generic IDs):
  - `RPMHPD_CX = 0`, `RPMHPD_MX = 8`, `RPMHPD_MMCX = 6`, etc.
- A handful (videocc, camcc, mdss_mdp, mdss_dp0, dispcc0) use **`QCS8300_*`**
  (chip-specific IDs with *different numeric values*):
  - `QCS8300_MMCX = 7`, `QCS8300_MXC = 12`, etc.

The rpmhpd driver (`drivers/pmdomain/qcom/rpmhpd.c`) registers the QCS8300
power domains using `QCS8300_*` indices as array positions. So when the DTS
passes `RPMHPD_CX = 0` or `RPMHPD_MMCX = 6`, the driver's `xlate()` callback
looks up the wrong entry in the array.

Example of the mismatch:

| Domain | RPMHPD_* value | QCS8300_* value |
|--------|---------------|-----------------|
| CX     | 0             | 0  (same)       |
| EBI    | 2             | 3  (different!) |
| MMCX   | 6             | 7  (different!) |
| MXC    | 10            | 12 (different!) |

**Root cause:** QCLinux had an early draft of an upstream patch that used
`QCS8300_*` names in the driver. Upstream reviewers later changed the approach
back to generic `RPMHPD_*` names (see:
https://lore.kernel.org/all/d5e338fe-bd38-49f7-b69f-fc27f9f87495@kernel.org/).
QCLinux retained the old `QCS8300_*` names in the driver, but the in-tree DTS
was partially updated to `RPMHPD_*` — leaving a broken mixture.

---

## Patches proposed (by Masahiro Yamada, 2026-01-26)

Two patches were attached to the ticket:

### 0001 — pmdomain: qcom: rpmhpd: use RPMHPD_* macros for xlate ID
- File: `drivers/pmdomain/qcom/rpmhpd.c`
- Change: switch the QCS8300 rpmhpds array from `QCS8300_*` index constants
  back to `RPMHPD_*` constants so the xlate callback works correctly with DTS
  that uses `RPMHPD_*`.

### 0002 — arm64: boot: dts: fix xlate ID to rpmhpd
- File: `arch/arm64/boot/dts/qcom/qcs8300.dtsi`
- Change: replace the 5 remaining `QCS8300_MMCX` / `QCS8300_MXC` references
  with `RPMHPD_MMCX` / `RPMHPD_MXC` so the DTS is consistent throughout.

Affected DTS nodes (the 5 that still used QCS8300_*):
- `videocc: clock-controller@abf0000`   → QCS8300_MMCX
- `camcc: clock-controller@ade0000`     → QCS8300_MMCX, QCS8300_MXC
- `mdss_mdp: display-controller@ae01000`→ QCS8300_MMCX
- `mdss_dp0: displayport-controller@af54000` → QCS8300_MMCX
- `dispcc0: clock-controller@af00000`   → QCS8300_MMCX

---

## Discussion summary

- **2026-02-12** Kamal Wadhwa (QC): confirmed the fix is correct; said upstream
  (linux-next / 7.0) and QLI are already fixed. Suggested Canonical could keep
  the RPMHPD_ fix as they are on an older branch.
- **2026-02-19** Masahiro Yamada: clarified the issue is in QCLinux (6.6 branch,
  https://git.codelinaro.org/clo/la/kernel/qcom), not upstream 7.0.
- **2026-04-29** Kamal Wadhwa: switching all clients from QCS8300_* to RPMHPD_*
  in the 6.6 branch would be a large undertaking for QC.
- **2026-05-07** Krzysztof Adamski (Canonical): proposed compromise — add
  `QCS8300_*` as aliases for `RPMHPD_*` in the header, change in-tree driver
  and DTS to RPMHPD_*, OOT drivers can convert at their own pace.
- **2026-06-08** Krzysztof: confirmed the alias approach:
  ```c
  // in include/dt-bindings/power/qcom,rpmhpd.h
  #define RPMHPD_CX 0
  #define QCS8300_CX RPMHPD_CX   // alias
  ```
- **2026-06-17** Kamal Wadhwa: proposed DTS-only fix (0002) without touching
  driver — minimal risk. Headers keep both sets of names.
- **2026-06-19** Krzysztof: accepted the minimal DTS fix as "better than nothing".
- **2026-06-25** Kamal Wadhwa: **confirmed both patches (0001 + 0002) merged
  into QCLinux**. Will be available in the next QLI release.
  Note: file was renamed `qcs8300.dtsi` → `monaco.dtsi` in upstream 7.0+.

---

## What was fixed

Qualcomm merged **both** patches (driver + DTS) into QCLinux. The fix will be
available in the next QLI release (expected in QLI 1.9).

- `QCS8300_*` aliases are left in headers for OOT driver compatibility.
- In-tree driver consistently uses `RPMHPD_*`.
- In-tree DTS consistently uses `RPMHPD_*`.

Canonical already had the equivalent fix in their tree.

---

## What you need to do — ACTION ITEM

**When rebasing from QLI 1.9 (the next QCLinux release):**

1. **Do not double-apply the fix.** Canonical's tree already has the correct
   `RPMHPD_*` usage. When the QLI 1.9 patches arrive, verify there are no
   conflicts that re-introduce `QCS8300_*` in the driver or DTS.

2. **Verify consistency after rebase:** confirm `qcs8300.dtsi` (or `monaco.dtsi`
   if the file was renamed) has no remaining `QCS8300_MMCX` / `QCS8300_MXC`
   references.
   ```bash
   grep "QCS8300_" arch/arm64/boot/dts/qcom/qcs8300.dtsi
   # should return empty (or only comments)
   ```

3. **Headers will have both names** — that's intentional. Don't remove the
   `QCS8300_*` aliases; they exist for OOT driver compatibility.

4. **File rename note:** upstream 7.0 renamed `qcs8300.dtsi` → `monaco.dtsi`.
   In the 6.6 / QLI branch the old name may still be used.

---

## Related tickets

- Bug #2132005 — earlier related rpmhpd issue (referenced in discussion)
- PECA-1334 — internal Qualcomm Jira (linked in description)
