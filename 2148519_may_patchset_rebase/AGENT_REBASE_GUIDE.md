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

## 8. History of Rebases

| Date | Old tag | New tag | Upstream base | Commits applied |
|------|---------|---------|---------------|-----------------|
| 2026-04-xx | (initial) | qcom-next-7.0-rc6-20260409 | Linux 7.0 | ~600 (original tree setup) |
| 2026-06-09 | qcom-next-7.0-rc6-20260409 | qcom-next-7.1-rc2-20260515 | Linux 7.1-rc2 | 378 (6 skipped, 40 auto, 14 manual) |

---

## 9. After the Rebase

Once all commits are applied:
1. Run `git log --oneline -20` and verify HEAD looks sane.
2. Do Ubuntu packaging: add `UBUNTU: Start new release` commit with version bump.
3. Build-test: `fakeroot debian/rules clean && fakeroot debian/rules binary-headers` (or equivalent).
4. **Check for graduated-upstream gaps** (see Section 5): look for DTC `Label or path not found` errors and `No rule to make target` errors pointing at DTS files. Fix by copying the relevant files from qcom-linux tip.
5. Watch for compiler errors from conflict resolutions — particularly in subsystems with enum/macro renames (e.g. `INDEX_*` in coresight CTI).
6. Update `~/qualcomm/qualcomm_rebase.txt` with the new rebase summary.
7. Update this file's history table (Section 8) and the "Known graduated-upstream files" table (Section 5) with any new entries.

---

## 10. Quick Reference Commands

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
