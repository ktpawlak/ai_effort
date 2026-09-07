# LP#2165440 — Rebase conflict-resolution audit & fixes

**Tree:** `~/canonical/kernel/ubuntu/noble/linux`
**Branch:** `cranky/qcom`
**Task:** Rebase the Qualcomm derivative `Ubuntu-qcom-6.8.0-1084.89` from base
`Ubuntu-6.8.0-139.139` onto `Ubuntu-6.8.0-145.145`.
**Date:** 2026-09-03

Four conflict-resolution defects were found and fixed. Two caused build
failures; **two were silent** and would have shipped a broken driver.

---

## 1. Reference points

| Ref | Commit | Meaning |
|---|---|---|
| `78d7d3da9419` | `UBUNTU: Ubuntu-6.8.0-139.139` | Old base |
| `895d878151d4` | `UBUNTU: Ubuntu-6.8.0-145.145` | New base |
| `a30603e9c916` | tag `Ubuntu-qcom-6.8.0-1084.89` | Pre-rebase derivative tip |
| `6198199a53e8` | branch `backup/cranky-qcom-pre-icefix` | State before any fix |
| `925a0c46bf86` | `UBUNTU: Ubuntu-qcom-6.8.0-1084.89ubuntu2` | Final HEAD |

Derivative carries 3618 commits on top of the new base. History is linear
(no merge commits), 1 281 828 commits total.

> Caution for future audits: a commit whose *subject* is
> `UBUNTU: Ubuntu-qcom-6.8.0-1084.89` also exists inside the rebased branch.
> It is **not** the pre-rebase state. Always resolve the annotated **tag**
> (`a30603e9c916`), otherwise every diff comes back empty and the audit
> silently produces false negatives.

---

## 2. Defects found and fixed

### 2.1 `drivers/soc/qcom/ice.c` — build failure

**Culprit:** `FROMLIST: soc: qcom: ice: Add OPP-based clock scaling support for ICE`
(now `b289971d30c9`)

New base commit `609d5daeca34` — *"soc: qcom: ice: Fix race between
`qcom_ice_probe()` and `of_qcom_ice_get()`"* (upstream `d922113ef91e`) —
replaced `platform_set_drvdata()` with a global xarray plus mutex. The OPP
commit inserts `legacy_ice_clk_names[]` at exactly that spot, and the
resolution took **theirs-only** instead of keeping both sides:

```diff
-static DEFINE_XARRAY(ice_handles);
-static DEFINE_MUTEX(ice_mutex);
+static const char * const legacy_ice_clk_names[] = { "ice_core_clk", "ice", };
```

All *users* survived (`guard(mutex)`, `xa_load`, `xa_store`,
`qcom_ice_remove`, `.remove_new`, `#include <linux/xarray.h>`), so only the
definitions were missing.

```
ice.c:699: error: 'ice_mutex' undeclared
ice.c:721: error: 'ice_handles' undeclared
```

**Fix:** restore both definitions alongside the new array.

### 2.2 `drivers/bluetooth/hci_qca.c` — build failure

**Culprit:** `PENDING: driver: bluetooth: hci_qca: fix SSR unable to wake up bug`
(now `89f09ef7d2f1`)

This derivative commit and new base commit `f4a527a2fd5f` — *"Bluetooth:
hci_qca: Convert timeout from jiffies to ms"* (upstream `375ba7484132`) —
fix the **same** jiffies-vs-milliseconds bug by different means. The base
embedded `msecs_to_jiffies()` inside the macro and dropped the `_MS` suffix:

```c
#define MEMDUMP_TIMEOUT  msecs_to_jiffies(8000)   /* was MEMDUMP_TIMEOUT_MS 8000 */
```

The resolution kept the derivative's now-redundant `msecs_to_jiffies(...)`
wrapper referencing the deleted macro name.

```
hci_qca.c:1620: error: 'MEMDUMP_TIMEOUT_MS' undeclared
```

**Fix:** drop the derivative hunk; use `MEMDUMP_TIMEOUT` directly. The two
`wake_up_bit()` additions (the commit's actual purpose) were unaffected and
retained.

### 2.3 `drivers/misc/fastrpc.c` — SILENT, severe

**Culprit:** `PENDING: misc: fastrpc: Add rpdev check in device_open`
(now `76e4ebdee049`)

New base commit `e8a8932da9e1` — *"misc: fastrpc: Fix NULL pointer
dereference in rpmsg callback"* — **moved** `dev_set_drvdata()` (delete in
one hunk, re-add in another). The derivative commit independently moves
`data->rpdev = rpdev;` below `of_platform_populate()` as a readiness flag.
The resolution applied the base's **delete** but never the **re-add**, so the
call vanished entirely:

```c
	idr_init(&data->ctx_idr);
	data->domain_id = domain_id;
	                                  /* <-- dev_set_drvdata() lost here */
	err = of_platform_populate(rdev->of_node, NULL, NULL, rdev);
```

**Impact — compiles cleanly, breaks at runtime.** Nothing ever stores the
channel context, so every `dev_get_drvdata()` consumer receives NULL:

* `fastrpc_rpmsg_callback()` — NULL deref on `&cctx->lock`
* `fastrpc_rpmsg_remove()` — same
* `fastrpc_cb_probe()` — reads `dev_get_drvdata(dev->parent)`, so all child
  context-bank probes fail

**Fix:** restore `dev_set_drvdata(&rpdev->dev, data);` *after* full struct
initialisation but *before* `of_platform_populate()`. Placement matters:

* it must precede `of_platform_populate()`, otherwise child probes see NULL;
* it must follow the field initialisation, preserving the base fix's intent
  of never publishing a partially-initialised `cctx`;
* `data->rpdev = rpdev;` stays where the derivative put it, preserving the
  `cctx->rpdev == NULL` readiness-marker design used by
  `fastrpc_device_open()`.

### 2.4 `drivers/misc/fastrpc.c` — SILENT, NULL guard

**Culprit:** `PENDING: misc: fastrpc: Add support for invoke v2`
(now `9eb613e56663`)

The second hunk of base commit `e8a8932da9e1` added a defensive check in
`fastrpc_rpmsg_callback()`. This commit rewrote the surrounding `ctxid`
computation and the guard was dropped in the process.

**Fix:** restore `if (!cctx) return -ENODEV;` after the length check.

---

## 3. Audit method (reusable)

Compile errors only reveal *some* bad resolutions. The dangerous ones — a
dropped `dev_set_drvdata()` — still compile. Procedure used:

**Step 1 — narrow the search space.** Only files touched by *both* sides can
have conflicted:

```sh
git diff --name-only 78d7d3da9419 895d878151d4 | sort -u > base_changed.txt   # 535
git diff --name-only 895d878151d4 HEAD        | sort -u > deriv_changed.txt   # 3288
comm -12 base_changed.txt deriv_changed.txt > risk.txt                        # 24
```

**Step 2 — compare deltas.** For each risk file compare the base delta
(`139→145`) with the actual `pre-rebase-tag → HEAD` delta. Equal line counts
mean the base change landed intact; divergence flags a file for review.

**Step 3 — prove each base commit survived.** Reverse-apply every base commit
touching a risk file; success proves the change is present:

```sh
git show $c -- "$f" | patch -p1 --dry-run -R -f --fuzz=3
```

**Step 4 — compile-verify** every risk object (arm64 allmodconfig).

**Step 5 — locate the culprit** by walking history and testing for the lost
token at each commit:

```sh
for c in $(git rev-list --reverse <base_commit>..HEAD -- <file>); do
    echo "$(git show $c:<file> | grep -c '<token>')  $(git log -1 --oneline $c)"
done
```

The transition `1 → 0` (or `0 → 1` for a stale reference) pinpoints the
commit whose resolution was wrong.

### Reading `audit.txt`

`audit.txt` was regenerated **after** the fixes, so section [2] shows the
reconciled state. Two useful signals there:

* `hci_qca.c` (`18+/20-`) and `fastrpc.c` (`68+/39-`) now match the base
  delta **exactly** — direct proof the lost hunks were fully restored.
  Before the fix they read `17+/19-` and `64+/39-`.
* `ice.c` (`24+/6-` vs `30+/8-`) and `qcom_geni_serial.c` (`10+/10-` vs
  `15+/2-`) legitimately diverge: many derivative commits rewrite those
  regions. Divergence is a *flag to investigate*, not a defect — both were
  cleared by step 3 and by compilation.
* `ucsi_ccg.c` shows `NONE`; explained in §4.

### Step-3 results

| Base commit | File | Status |
|---|---|---|
| `e8a8932da9e1` NULL deref in rpmsg callback | fastrpc.c | was **MISSING** → fixed |
| `9e7eb4e51665` DMA corruption / find_vma misuse | fastrpc.c | present |
| `64623c0cad99` UAF in `fastrpc_map_create` | fastrpc.c | present |
| `8db4ac292173` UAF of `fastrpc_user` in workqueue | fastrpc.c | present |
| `e92f544a16f6` kfifo underflow on flush | qcom_geni_serial.c | present |
| `a705e0c19895` `UART_RX_PAR_EN` bit position | qcom_geni_serial.c | present |
| `0134391aac66` reject FW without `':'` header | ucsi_ccg.c | obsolete — see §4 |

---

## 4. Investigated, no action required

**`drivers/usb/typec/ucsi/ucsi_ccg.c` — base commit `0134391aac66`.**
The base added a NULL guard after `strnchr(fw->data, fw->size, ':')` inside
`do_flash()`. The derivative (`PENDING: usb: ucsi: ccg: Add usb pd firmware
upgrade support for cyacd2 file`) **replaced `do_flash()` wholesale**; the
`strnchr` parsing loop no longer exists. Its replacement,
`ccg_parse_and_build_rows()` → `ccg_parse_cyacd_text()`, validates records
(`if (line[s] != ':')`) and returns `-EINVAL`, which the caller checks. The
protection is preserved by the rewrite, so dropping the hunk is correct.

**`arch/arm/mm/fault.c`, `arch/arm64/Kconfig`, `drivers/hid/hid-ids.h`** and
the remaining 17 risk files — base deltas match exactly; all compile.

---

## 5. Pre-existing issues (NOT rebase-related — informational)

Byte-identical to the pre-rebase tag `a30603e9c916`, therefore untouched by
this rebase. They surface only under `CONFIG_WERROR=y` (arm64 allmodconfig)
and are tolerated by the shipping qcom config.

**`drivers/usb/typec/ucsi/ucsi_ccg.c`** — looks like unfinished debug code:

* `kstrtoul()` called with `simple_strtoul()`'s signature at lines ~1462 and
  ~1471 (`kstrtoul(p, &endp, 16)`), producing `-Werror=int-conversion`. The
  parsed `start`/`size` values are never actually assigned — a real bug.
* `target_bank` used uninitialised in `do_flash()`.
* Stray debug print `pr_err("Ak: hybrid:%d\n", hybrid)`.
* ~12 functions defined but never used (`ccg_wait_success`,
  `ccg_crc32_rows`, `remap_rows_to_bank`, `ccg_dump_first_rows`, …).

**`drivers/misc/fastrpc.c`** — `%llx` format vs `unsigned long` argument
(`-Werror=format=`).

**`drivers/tty/serial/qcom_geni_serial.c`** — `serial_trace_log` missing a
prototype (`-Werror=missing-prototypes`).

Recommend a separate review of `ucsi_ccg.c` before release.

---

## 6. Changes applied

All four fixes were **amended into their originating commits** (not appended
as follow-ups), keeping the derivative patch series clean. Two interactive
rebases were performed; one expected conflict arose while replaying
`misc: fastrpc: prevent context reuse races using extended ctxid with jobid`
and was resolved by keeping **both** the restored `!cctx` guard and the
incoming `ctxid`/`idr` computation.

See `fixes.diff` for the exact code delta and `audit.txt` for raw audit
output.

### Verification

* History integrity: **1 281 828 commits**, identical to backup, no merges,
  commit subjects and order unchanged.
* Tree delta vs `backup/cranky-qcom-pre-icefix`:
  `ice.c +3`, `fastrpc.c +4`, `hci_qca.c ±1` (plus your unrelated
  `debian.qcom/changelog` `ubuntu1`→`ubuntu2` bump).
* Build: all **20** at-risk objects compile with **zero errors**
  (arm64 allmodconfig, cross `aarch64-linux-gnu-`), including
  `ice.o`, `hci_qca.o`, `fastrpc.o`.

### Rollback

```sh
git reset --hard backup/cranky-qcom-pre-icefix
```

---

## 7. Lessons for the next rebase

1. **Moved code is the top hazard.** A base commit that relocates a line
   appears as delete + add in separate hunks. Taking the delete and losing
   the add compiles fine and breaks at runtime — defects 2.3 and 2.4.
2. **Watch for both sides fixing the same bug.** Defect 2.2 arose because
   upstream later fixed, differently, what the derivative had already
   patched. When the base renames or reworks a macro the derivative also
   touches, prefer the base's form and drop the now-redundant hunk.
3. **"Theirs-only" on adjacent insertions loses code.** Defect 2.1: both
   sides inserted at the same line; only one survived.
4. **Compiling is necessary but not sufficient.** Always run the
   reverse-apply audit (§3, step 3) over the base∩derivative file set.
