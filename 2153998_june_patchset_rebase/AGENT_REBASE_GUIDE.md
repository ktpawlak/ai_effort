# Qualcomm Kernel Rebase — Agent Handoff Guide

**Purpose:** This document is a reference for future AI agent sessions performing Qualcomm kernel rebases. It explains the repository layout, the rebase methodology, known pitfalls, and step-by-step instructions for applying patches from a new `qcom-next` tag.

---

## 1. Repository Layout

| Path | Description |
|------|-------------|
| `~/qualcomm/linux` | Ubuntu kernel tree. Branch: `master-next`. This is the target. |
| `~/qualcomm/qualcomm-linux` | Qualcomm's upstream tree. Source of patches. |
| `~/qualcomm/commits_to_cherrypick.txt` | List of commits applied in the last rebase (for reference). |
| `~/qualcomm/qualcomm_rebase.txt` | Prose summary of the last rebase (qcom-next-7.0-rc6 → 7.1-rc2). |

### Ubuntu tree structure (oldest → newest)
```
upstream kernel X.Y (e.g. 7.0, 7.1)
  └── Ubuntu SAUCE patches
        └── Qualcomm cherry-picks (linear history, NOT merges)
              └── "UBUNTU: Start new release" / packaging commits
```

**Critical constraint:** History must remain **linear**. Never merge; always cherry-pick.

---

## 2. Identifying the Relevant Tag Commits

### 2.1 Understand the new tag

In `~/qualcomm/qualcomm-linux`, a qcom-next tag is a merge commit with two parents:

```bash
cd ~/qualcomm/qualcomm-linux
git cat-file -p <NEW_TAG>
# parent 1 = upstream kernel base (e.g. "Linux 7.1-rc2")
# parent 2 = qcom-next tip (all Qualcomm-specific patches)
```

Name the two parents:
- `UPSTREAM_BASE` = parent 1 (commit with subject "Linux X.Y-rcN")
- `QCOM_TIP`      = parent 2

### 2.2 Extract all qcom-specific commits from the new tag

```bash
git -C ~/qualcomm/qualcomm-linux log --no-merges --format="%H %s" \
    "$QCOM_TIP" ^"$UPSTREAM_BASE" > /tmp/qcom_all_commits.txt
wc -l /tmp/qcom_all_commits.txt   # total qcom commits in new tag
```

### 2.3 Extract commits already in the Ubuntu tree

Find the boundary commit: the "Linux X.Y" commit in the ubuntu tree (the common ancestor with upstream). Use `git log` to find it:

```bash
UBUNTU_UPSTREAM_BASE=$(git -C ~/qualcomm/linux log --oneline --no-merges | \
    grep "^.\{8\} Linux [0-9]" | head -1 | awk '{print $1}')
```

Then get all non-Ubuntu subjects already applied:

```bash
git -C ~/qualcomm/linux log --no-merges --format="%s" \
    HEAD ^"$UBUNTU_UPSTREAM_BASE" | grep -v "^UBUNTU:" > /tmp/ubuntu_applied_subjects.txt
```

### 2.4 Cross-reference to find new commits

```python
#!/usr/bin/env python3
# Run from any directory
applied = set(open('/tmp/ubuntu_applied_subjects.txt').read().splitlines())
new_commits = []
for line in open('/tmp/qcom_all_commits.txt').read().splitlines():
    sha, subject = line.split(' ', 1)
    if subject not in applied:
        new_commits.append((sha, subject))
# new_commits is in newest-first order from git log; reverse for cherry-pick
new_commits.reverse()
with open('/tmp/shas_to_pick.txt', 'w') as f:
    for sha, subject in new_commits:
        f.write(sha + '\n')
with open('~/qualcomm/commits_to_cherrypick.txt', 'w') as f:
    for sha, subject in new_commits:
        f.write(sha + ' ' + subject + '\n')
print(f"{len(new_commits)} commits to cherry-pick")
```

The output file `commits_to_cherrypick.txt` is **oldest-first** (the correct cherry-pick order).

---

## 3. Cherry-Pick Execution

### 3.1 Automation script

Save as `/tmp/do_cherrypick.sh`:

```bash
#!/bin/bash
# do NOT use set -e — it breaks conflict detection
SHA_FILE="/tmp/shas_to_pick.txt"
cd ~/qualcomm/linux

while read sha; do
    echo "==> Picking $sha"
    output=$(git cherry-pick -x "$sha" 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then
        echo "  OK"
        continue
    fi
    # Check if it resolved to an empty commit
    if echo "$output" | grep -q "nothing to commit"; then
        git cherry-pick --skip
        echo "  SKIPPED (empty)"
        continue
    fi
    if ! echo "$output" | grep -q "CONFLICT"; then
        echo "  FAILED (non-conflict): $output"
        break
    fi
    # Attempt auto-resolution: take theirs on all conflicted files
    conflicted=$(git diff --name-only --diff-filter=U)
    auto_ok=true
    for f in $conflicted; do
        python3 /tmp/resolve_conflict.py "$f" theirs || { auto_ok=false; break; }
    done
    if $auto_ok && ! git diff --name-only --diff-filter=U | grep -q .; then
        git add -A && git cherry-pick --continue --no-edit
        echo "  AUTO-RESOLVED"
    else
        echo "  CONFLICT — manual resolution required. Files:"
        git diff --name-only --diff-filter=U
        break
    fi
done < "$SHA_FILE"
```

### 3.2 Conflict resolver script

Save as `/tmp/resolve_conflict.py`:

```python
#!/usr/bin/env python3
import re, sys

def resolve(path, side):
    text = open(path, 'r', errors='replace').read()
    if '<<<<<<< HEAD' not in text:
        return  # no markers
    if side == 'ours':
        pattern = r'<<<<<<< HEAD\n(.*?)=======\n.*?>>>>>>> [^\n]+\n'
        repl = r'\1'
    else:  # theirs
        pattern = r'<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> [^\n]+\n'
        repl = r'\2'
    result = re.sub(pattern, repl, text, flags=re.DOTALL)
    if '<<<<<<< HEAD' in result:
        # Fallback: empty HEAD section (no newline before =======)
        if side == 'theirs':
            pattern2 = r'<<<<<<< HEAD\n(.*?)=======(.*?)>>>>>>> [^\n]+\n'
            result = re.sub(pattern2, r'\2', result, flags=re.DOTALL)
        else:
            pattern2 = r'<<<<<<< HEAD\n(.*?)=======(.*?)>>>>>>> [^\n]+\n'
            result = re.sub(pattern2, r'\1', result, flags=re.DOTALL)
    open(path, 'w').write(result)

if __name__ == '__main__':
    resolve(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'theirs')
```

### 3.3 Resuming after a manual conflict

After manually resolving files:
```bash
git add <resolved-files>
git cherry-pick --continue --no-edit
# Then re-run do_cherrypick.sh — it reads the remaining SHAs
```

**Tracking progress:** Remove already-applied SHAs from `/tmp/shas_to_pick.txt` before re-running the script. The script reads sequentially from the top of the file.

---

## 4. Conflict Resolution Strategy

### Golden rule
For every conflict, check the **new tag's final state** of the file to determine correct resolution:

```bash
git -C ~/qualcomm/qualcomm-linux show "$QCOM_TIP":<path/to/file> | head -80
```

If the final file matches the incoming (THEIRS) side → take THEIRS.  
If the final file preserves content from our HEAD (added by old tag) → keep OURS.

### Common conflict types

| Pattern | Usual resolution | Rationale |
|---------|-----------------|-----------|
| Incoming refactor removes code that HEAD added, but code still in final file | KEEP OURS | A later commit (already in HEAD) re-adds it |
| Incoming refactor removes code that is absent from final file | TAKE THEIRS | The removal is intentional |
| Empty insertion-point mismatch (new code at a spot HEAD doesn't have) | TAKE THEIRS | No real conflict |
| Clock/binding renames across DTS files | TAKE THEIRS | Systematic rename |
| Auto-resolver produces duplicate blocks | TAKE THEIRS + manual dedup | Resolver applied to wrong region |
| Commit deletes function that still exists in final file | KEEP OURS | Later commit re-adds it |

### Checking for duplicate stale blocks (Makefile pitfall)
After auto-resolving DTS `Makefile` entries, always verify no block is duplicated:
```bash
git diff HEAD -- arch/arm64/boot/dts/qcom/Makefile | grep "^+" | sort | uniq -d
```

---

## 5. The "Graduated Upstream" Gap — Critical Post-Rebase Check

### What it is

The cherry-pick methodology filters out commits that are already in `UPSTREAM_BASE` (e.g. Linux 7.1-rc2):

```
git log --no-merges QCOM_TIP ^UPSTREAM_BASE
```

This is correct — those commits are already in upstream. **But our ubuntu tree is based on the *previous* upstream (e.g. 7.0), not the new one (7.1-rc2).** So commits that graduated from qcom-next into upstream *between the two kernel versions* are:
- ✅ correctly excluded from the cherry-pick list (they're in upstream 7.1-rc2)
- ❌ **missing from our tree** (which is based on 7.0)

These cause build failures *after* the cherry-pick is complete, because later qcom commits (already applied) may reference labels or code defined only by the graduated commits.

### How to detect graduated-upstream gaps

After building, look for DTC errors of the form:
```
Error: arch/arm64/boot/dts/qcom/foo.dtsi:N Label or path bar not found
```
or Makefile errors:
```
No rule to make target 'arch/arm64/boot/dts/qcom/foo.dtb'
```

These indicate a file or label is referenced but never defined — classic sign of a missing graduated commit.

To proactively find which files are affected, diff the ubuntu tree against the qcom-linux tip for any file that a failing commit touches:

```bash
# Find commits between old and new qcom-next tips that touch a suspect file
OLD_QCOM_TIP=<parent2 of old tag>
git -C ~/qualcomm/qualcomm-linux log --oneline \
    "$QCOM_TIP" ^"$OLD_QCOM_TIP" -- path/to/suspect/file

# For each MISS commit, check if it's in upstream (graduated):
git -C ~/qualcomm/qualcomm-linux log --format="%s" "$UPSTREAM_BASE" | \
    grep -xF "<commit subject>"
# count > 0 → graduated upstream → need to apply manually
```

### How to fix graduated-upstream gaps

**Always diff first** to understand the scope and check for ubuntu-specific content:
```bash
diff ~/qualcomm/linux/arch/arm64/boot/dts/qcom/foo.dtsi \
     ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dtsi
```
- Lines with `<` = ubuntu-only content. If these are just old versions of qcom content (no-label node names, old strings) → safe to copy wholesale.
- Lines with `<` that are **ubuntu-specific additions** (content absent from qcom-linux entirely) → surgical insert required.

**Option A — copy the file** (when all `<` lines are just old qcom content, no ubuntu additions):
```bash
cp ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dtsi \
   ~/qualcomm/linux/arch/arm64/boot/dts/qcom/foo.dtsi
```

**Option B — surgical insert** (when ubuntu tree has content absent from qcom-linux):

Add only the missing node/label at the correct position in the ubuntu file:
```bash
# Identify the missing block in qcom-linux
grep -n "missing_label" ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dtsi
# Find a unique anchor nearby in ubuntu tree
grep -n "nearby_node" ~/qualcomm/linux/arch/arm64/boot/dts/qcom/foo.dtsi
# Edit ubuntu file to insert the missing block before/after the anchor
```
**Example:** `monaco.dtsi` has ubuntu-specific camera pin states (`cam1_avdd_2v8_en_default`, `cam2_avdd_2v8_en_default`) absent from qcom-linux. Copying wholesale would lose them. Instead, only the missing `lpass_tlmm` pinctrl node and its `#include` were inserted surgically.

**Option C — cherry-pick the graduated commits** (when changes are non-trivial or span multiple files):
```bash
# Pick in oldest-first order from qcom-linux
git -C ~/qualcomm/linux cherry-pick -x <sha-from-qcom-linux>
```
Cherry-pick from the qcom-linux SHA, not the upstream SHA (the upstream SHA won't exist in our local clone).

### Known graduated-upstream files (7.0→7.1 rebase)

These files were copied wholesale from qcom-linux to fix build failures after the 7.1-rc2 rebase. The changes were label/string additions only — no functional difference:

| File | What was missing | Commit(s) that graduated | Fix approach |
|------|-----------------|--------------------------|--------------|
|------|-----------------|--------------------------|
| `arch/arm64/boot/dts/qcom/glymur.dtsi` | Thermal zone labels (`thermal_cpu_2_*`, `thermal_aoss_*`, `thermal_gpu_*`, `thermal_nsp*`, `thermal_camera_*`, `thermal_ddr_*`, `thermal_video_*`, `thermal_gpuss_*`), `cpu_map_cluster2:` label, CPU compatible strings (`qcom,oryon` → `qcom,oryon-2-1/2-2`), `mdss_dp3_phy` repositioned | `fee828abbd9d`, `5044a0b0307a`, others | Copy wholesale (no ubuntu-specific content) |
| `arch/arm64/boot/dts/qcom/pmcx0102.dtsi` | `pmcx0102_d0_thermal:` label | `c1014a629d01` | Copy wholesale |
| `arch/arm64/boot/dts/qcom/pmh0104-glymur.dtsi` | `pmh0104_i0_thermal:`, `pmh0104_j0_thermal:` labels | `c1014a629d01` | Copy wholesale |
| `arch/arm64/boot/dts/qcom/monaco.dtsi` | `lpass_tlmm: pinctrl@3440000` node (defines `quad_mi2s_active`, `quad_mclk_active`, `lpi_i2s4_active`); `#include <dt-bindings/sound/qcom,q6dsp-lpass-ports.h>` | LPASS audio commit | **Surgical insert only** — ubuntu tree has `cam1_avdd_2v8_en_default` / `cam2_avdd_2v8_en_default` pin states absent from qcom-linux; copy wholesale would lose them |

### The "missing DTS source file" pattern

A related but distinct failure: the DTS `Makefile` gains an entry for `foo.dtb` (pulled in as context during a conflict resolution), but `foo.dts` was never added because its commit was filtered as "already applied" (e.g., a WORKAROUND commit added only the `.dtsi`, while the commit that adds the `.dts` was considered already-applied by subject match).

**Symptom:**
```
No rule to make target 'arch/arm64/boot/dts/qcom/foo.dtb'
```

**Fix:** Copy the missing `.dts` (and any new `.dtsi` it includes) from qcom-linux:
```bash
# Find it
find ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/ -name "foo.dts"
# Check its includes
grep "^#include" ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dts
# Copy missing files
cp ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dts \
   ~/qualcomm/linux/arch/arm64/boot/dts/qcom/foo.dts
```

**Known occurrences (7.1-rc2 rebase):**
- `mahua-crd.dts` — added by `c1014a629d01`, missed because `mahua.dtsi` was already present via WORKAROUND commit, making the parent commit appear "already applied" by subject. Fixed in commit `f904f3822ff3`.
- `monaco-arduino-monza.dts` + `monaco-monza-som.dtsi` — Makefile entry pulled in during monaco-ac-evk conflict resolution; source files never added. Fixed in commit `8870436d2f36`.

---

## 6. Sanity Checks

### Before starting
```bash
cd ~/qualcomm/linux
git status           # must be clean
git log --oneline -5 # note current HEAD
```

### After each round
```bash
git log --oneline -5
git diff HEAD~1 --stat | tail -5
```

### After all cherry-picks
```bash
# Count new non-Ubuntu commits since last packaging commit
git log --oneline --no-merges HEAD ^<UBUNTU_UPSTREAM_BASE> | grep -vc "^.\{8\} UBUNTU:"
```

---

## 7. Known Recurring Conflict Areas

These subsystems had conflicts during the qcom-next-7.0→7.1 rebase and may conflict again:

| Subsystem | Files | Typical cause |
|-----------|-------|---------------|
| Coresight CTI | `drivers/hwtracing/coresight/coresight-cti-*.c`, `coresight-cti.h`, `qcom-cti.h` | Register encoding refactors |
| Coresight TMC | `coresight-tmc-core.c`, `coresight-tmc.h` | sysfs ops refactoring |
| ICE / crypto clocks | `drivers/soc/qcom/ice.c`, many DTS files | Clock name renames |
| Display (DP) | `drivers/gpu/drm/msm/dp/dp_ctrl.c` | HPD handling refactors |
| Venus/Iris | `drivers/media/platform/qcom/venus/core.c` | CONFIG guard additions |
| RPMH regulator | `drivers/regulator/qcom-rpmh-regulator.c` | PMIC model additions |
| DTS Makefile | `arch/arm64/boot/dts/qcom/Makefile` | New board additions |

---

## 8. C Source Build Errors After Rebase

After all DTS errors are fixed, compiler errors from C source files appear. These follow distinct patterns:

### 8.1 Double Cherry-Pick (file truncation)

**Symptom:** File significantly shorter than qcom-linux version. Compiler reports symbols as undeclared that appear to be defined in the file.

**Cause:** Both a FROMLIST commit and its matching FROMGIT commit (same change, different upstream status) were both cherry-picked. The FROMGIT commit tries to remove code that the FROMLIST already removed, resulting in net deletion of large sections.

**Detection:**
```bash
wc -l ~/qualcomm/linux/path/to/file.c
wc -l ~/qualcomm/qualcomm-linux/path/to/file.c
# If ubuntu has ~50% or fewer lines → double cherry-pick likely

# Check git log for FROMLIST + FROMGIT pair on same file
git log --oneline -- path/to/file.c | grep -E "FROMLIST|FROMGIT"
```

**Fix:** If ubuntu has no ubuntu-specific content in the file, copy wholesale from qcom-linux:
```bash
cp ~/qualcomm/qualcomm-linux/path/to/file.c ~/qualcomm/linux/path/to/file.c
```

**Known occurrences (7.1-rc2 rebase):**
- `drivers/regulator/qcom-rpmh-regulator.c` — lost ~947 lines; fixed by copying from qcom-linux
- `drivers/soc/qcom/smem.c` — missing `debugfs_dir` field + `smem_dram_parse()`; fixed by copying from qcom-linux

### 8.2 Ubuntu-specific code referencing missing struct members/includes

**Symptom:** Compiler error like `struct X has no member Y` or `implicit declaration of function Z` in a file that has ubuntu-specific additions.

**Cause:** Ubuntu SAUCE commits added new functionality (e.g., mutex locking, pm_runtime) but missed adding the required struct field or include.

**Fix:** Surgical insertion only — do NOT copy from qcom-linux as ubuntu-specific code would be lost.

```bash
# Identify ubuntu-specific lines (present in ubuntu but not qcom-linux)
diff ~/qualcomm/qualcomm-linux/path/to/file.c ~/qualcomm/linux/path/to/file.c | grep "^>"
```

**Known occurrences (7.1-rc2 rebase):**
- `drivers/misc/fastrpc.c` — missing `struct mutex mutex` in `fastrpc_session_ctx`; added field + `mutex_init`
- `drivers/soc/qcom/ice.c` — missing `#include <linux/pm_runtime.h>` despite pm_runtime calls

### 8.3 Duplicate function definition (double cherry-pick creating two function bodies)

**Symptom:** `redefinition of 'function_name'` compile error. Two identical or similar function bodies in the same file.

**Cause:** A function was added by one cherry-pick, then updated/refactored by a second cherry-pick that didn't properly handle the first version already being present.

**Detection:**
```bash
grep -n "function_name" ~/qualcomm/linux/path/to/file.c
# Shows two line numbers for the same definition
```

**Fix:** Check qcom-linux to determine which version is correct (usually the second/newer), then remove the first/older one.

**Known occurrences (7.1-rc2 rebase):**
- `drivers/gpu/drm/msm/dp/dp_ctrl.c` — `msm_dp_ctrl_off_link` appeared twice (old version without MST support, then new version with MST). Removed the old one.

### 8.4 `#ifdef` guard scope mismatch (ubuntu code outside a conditional block)

**Symptom:** Compiler reports symbols as "undeclared" that ARE defined in the same file.

**Cause:** A qcom-next cherry-pick wraps a block in `#if (!IS_ENABLED(CONFIG_FOO))`, but ubuntu-specific code that references those symbols is outside the block.

**Detection:**
```bash
grep -n "#if\|#endif\|suspicious_symbol" ~/qualcomm/linux/path/to/file.c
# Check if definition is inside a #if block that the usage is outside
```

**Fix:** Move the ubuntu-specific code inside the same `#if` block, before its `#endif`.

**Known occurrences (7.1-rc2 rebase):**
- `drivers/media/platform/qcom/venus/core.c` — `sc8280xp_freq_table` and `sc8280xp_res` (ubuntu SAUCE) were outside `#if (!IS_ENABLED(CONFIG_VIDEO_QCOM_IRIS))` but reference symbols (`sm8350_reg_preset`, `VPU_VERSION_IRIS2`) inside it. Moved them inside the `#if` block.

### 8.5 Garbled hybrid file (two conflicting implementations merged)

**Symptom:** File uses APIs from two different approaches simultaneously. References undefined types/functions. Wildly different from both ubuntu original and qcom-linux version.

**Cause:** A qcom-next cherry-pick reimplemented a subsystem in a completely different way, but the cherry-pick landed on top of ubuntu-specific additions that used the old approach. The result is a nonsensical mix.

**Fix:** Determine if there are ubuntu-specific callers that depend on the old API. If not, copy qcom-linux wholesale. If yes, manually rebase the ubuntu-specific additions onto the new API.

**Known occurrences (7.1-rc2 rebase):**
- `drivers/power/reset/reboot-mode.c` — ubuntu used `rb_class`/`reboot_dev`/`driver_name` approach; qcom-linux used `reboot_mode_sysfs_data`/`reboot_mode_class` approach. No external callers used ubuntu-specific fields. Replaced with qcom-linux version (which already includes `name` field support from the ubuntu WORKAROUND commit).

### 8.6 Cherry-picked code using newer upstream API

**Symptom:** Errors like `has no member named 'new_field'`, `implicit declaration of function 'new_helper'`, or `too few/many arguments` on functions that exist in upstream.

**Cause:** A commit from qcom-next (based on a newer upstream kernel) uses APIs that were added/changed between the ubuntu kernel version and the new upstream. The ubuntu kernel has an older version of the API.

**Diagnosis:**
```bash
# Check ubuntu's version of the API
grep -n "function_name\|struct_name" ~/qualcomm/linux/include/relevant/header.h

# Check qcom-linux version to understand the new API contract
grep -n "function_name\|struct_name" ~/qualcomm/qualcomm-linux/include/relevant/header.h
```

**Fix:** Adapt the cherry-picked code to use the ubuntu (older) API pattern. Common adaptations:

| New API (qcom-linux) | Old API (ubuntu 7.0) | Notes |
|---------------------|----------------------|-------|
| `drm_atomic_private_obj_init(dev, obj, funcs)` — 3 args | `drm_atomic_private_obj_init(dev, obj, state, funcs)` — 4 args; initial state required | Must allocate initial state separately |
| `drm_private_state_funcs.atomic_create_state` | No such member; only `atomic_duplicate_state` | Remove `atomic_create_state` hook and `__drm_atomic_helper_private_obj_create_state` usage |
| `__drm_atomic_helper_private_obj_create_state(obj, &state->base)` | Not available | Replace with manual init; `drm_atomic_private_obj_init` sets `state->obj = obj` itself |

**Known occurrences (7.1-rc2 rebase):**
- `drivers/gpu/drm/msm/dp/dp_mst_drm.c` — used new `atomic_create_state` hook and 3-arg `drm_atomic_private_obj_init`. Adapted to old 4-arg API with inline initial state allocation.

### 8.7 Missing source files for new Kbuild targets

**Symptom:** `No rule to make target 'drivers/foo/bar.o'`

**Cause:** A Makefile gained `obj-$(CONFIG_FOO) += bar.o` but the corresponding `bar.c` source file was never copied because its commit was filtered out (graduated upstream or subject-matched against an older commit).

**Fix:** Copy all required `.c` and `.h` files from qcom-linux:
```bash
# Find the source file
ls ~/qualcomm/qualcomm-linux/drivers/foo/bar.c

# Also check for required dt-bindings headers
grep "^#include" ~/qualcomm/qualcomm-linux/drivers/foo/bar.c | grep "dt-bindings"

# Copy source + headers
cp ~/qualcomm/qualcomm-linux/drivers/foo/bar.c ~/qualcomm/linux/drivers/foo/bar.c
cp ~/qualcomm/qualcomm-linux/include/dt-bindings/clock/qcom,bar.h \
   ~/qualcomm/linux/include/dt-bindings/clock/qcom,bar.h
```

**Known occurrences (7.1-rc2 rebase):**
- `drivers/clk/qcom/gcc-nord.c`, `negcc-nord.c`, `nwgcc-nord.c`, `segcc-nord.c`, `tcsrcc-nord.c` + 5 dt-binding headers — all missing for new Nord platform clock drivers.

---

## 9. History of Rebases

| Date | Old tag | New tag | Upstream base | Commits applied |
|------|---------|---------|---------------|-----------------|
| 2026-04-xx | (initial) | qcom-next-7.0-rc6-20260409 | Linux 7.0 | ~600 (original tree setup) |
| 2026-06-09 | qcom-next-7.0-rc6-20260409 | qcom-next-7.1-rc2-20260515 | Linux 7.1-rc2 | 378 (6 skipped, 40 auto, 14 manual) |

---

## 10. After the Rebase

Once all commits are applied:
1. Run `git log --oneline -20` and verify HEAD looks sane.
2. Do Ubuntu packaging: add `UBUNTU: Start new release` commit with version bump.
3. Build-test: `fakeroot debian/rules clean && fakeroot debian/rules binary-headers` (or equivalent).
4. **Check for graduated-upstream gaps** (see Section 5): look for DTC `Label or path not found` errors and `No rule to make target` errors pointing at DTS files. Fix by copying the relevant files from qcom-linux tip.
5. **Check for C source build errors** (see Section 8): after DTS errors are resolved, watch for compiler errors from double cherry-picks, missing struct members/includes, duplicate functions, `#ifdef` scope mismatches, garbled hybrid files, and API version mismatches.
6. Watch for compiler errors from conflict resolutions — particularly in subsystems with enum/macro renames (e.g. `INDEX_*` in coresight CTI).
6. Update `~/qualcomm/qualcomm_rebase.txt` with the new rebase summary.
7. Update this file's history table (Section 8) and the "Known graduated-upstream files" table (Section 5) with any new entries.

---

## 11. CBD Remote Build System

The kernel is built on a remote CBD (Canonical Build Device) machine. All build operations are driven by `git push` — there is no manual SSH invocation needed to start a build.

### 11.1 Triggering a build

From the ubuntu tree directory, push with the `native` option:

```bash
cd ~/qualcomm/linux
git push cbd -o native
```

The push **blocks** (hangs) while the build is in progress, streaming status lines to stderr. Do not interrupt it — let it run to completion.

### 11.2 Reading status lines

While the push is running, the remote prints periodic status lines:

```
remote: 2026-06-09 18:21:09 3/7 worker busy, 0 builds queued, 0 workers starting
remote:  kpawlak-resolute-<HEAD_SHA>-<4DIGITS>/arm64/BUILDING
```

**Build ID format:** `kpawlak-resolute-<SHORT_SHA>-<4DIGITS>/arm64`
- `<SHORT_SHA>` is the short SHA of the HEAD commit that was pushed
- `<4DIGITS>` is a random 4-digit job number assigned by CBD

**Status values:**
| Status | Meaning |
|--------|---------|
| `QUEUED` | Waiting for a free worker (can take 5–15 min if all 7 workers are busy) |
| `BUILDING` | Actively compiling (~15–25 min for a full arm64 kernel build) |
| `BUILD-OK` | Success — kernel built and packaged |
| `BUILD-FAILED` | Failure — download the log (see below) |

### 11.3 Downloading the build log on failure

When the push completes with `BUILD-FAILED`, download the log using the Build ID:

```bash
# Format: ssh cbd log <BUILD_ID>
# The BUILD_ID has no trailing slash
ssh cbd log kpawlak-resolute-<SHORT_SHA>-<4DIGITS>/arm64 > log.txt
```

**Example:**
```bash
ssh cbd log kpawlak-resolute-18da63c8db55-3774/arm64 > log.txt
```

The log is saved to `log.txt` in the current directory. It is typically 20,000–25,000 lines long.

### 11.4 Finding errors in the log

The build log contains verbose make output. Extract compiler errors efficiently:

```bash
# Show all actual errors (exclude warnings and notes)
grep -n "error:" log.txt | grep -iv "warning\|note:" | head -40

# Show surrounding context for first error
grep -n "error:" log.txt | grep -iv "warning\|note:" | head -1
# Then: sed -n '<LINE-10>,<LINE+10>p' log.txt

# Find the make rule that failed
grep "make\[.*\]: \*\*\*" log.txt | head -10
```

Common error patterns to search for:
```bash
grep -n "error:\|undefined\|implicit\|undeclared\|redefinition\|no rule\|cannot find" log.txt \
    | grep -iv "warning\|note:" | head -40
```

### 11.5 Iterating on failures

Typical cycle:
1. Fix the error(s) in the source tree
2. Commit the fix: `git add <files> && git commit -m "Fix: <description>"`
3. Re-push: `git push cbd -o native`
4. Wait for result (~20–30 min total including queue time)
5. If `BUILD-FAILED`: `ssh cbd log kpawlak-resolute-<NEW_SHA>-<4DIGITS>/arm64 > log.txt`
6. Repeat

**Tip:** Fix all visible errors before re-pushing. Each build takes 15–25 minutes, so batch fixes when possible. After resolving one category of error (e.g., all DTS errors), scan the full log carefully before pushing to catch additional errors in the same build.

### 11.6 Checking build status without waiting for the push

If you need to check status without holding an open push connection, you can query the CBD status page or use:

```bash
ssh cbd.kernel ls kpawlak-resolute-<SHORT_SHA>-<4DIGITS>
```

(The build ID is printed by the push output; note `cbd.kernel` vs `cbd` for the `ls` sub-command.)

---

## 12. Quick Reference Commands

```bash
# Show tag parents
git cat-file -p <TAG_SHA>

# All qcom commits in a tag (newest-first)
git log --no-merges --format="%H %s" "$QCOM_TIP" ^"$UPSTREAM_BASE"

# Check file in qcom tip
git show "$QCOM_TIP":<path>

# Continue after manual conflict fix
git add <files> && git cherry-pick --continue --no-edit

# Skip empty commit
git cherry-pick --skip

# Abort and start over on current commit
git cherry-pick --abort
```
