# Copilot Instructions

This repository (`ai_effort`) is a workspace for AI-assisted Qualcomm ARM kernel development at Canonical. It contains investigation documents, kernel patches, automation scripts, and tooling for Qualcomm development boards.

## Subprojects

| Directory | Purpose |
|-----------|---------|
| `qpa/` | Board flashing and test automation — has its own `.github/copilot-instructions.md` |
| `keyboard_gadget/` | USB HID keyboard gadget for driving a DUT remotely (via Raspberry Pi 4) |
| `fan_control/` | Hamoa fan control investigation and notes |
| `2XXXXXXX_*/` | Per-Launchpad-bug analysis: patches, investigation notes, rebase guides |

## Boards

| Board  | IP address     | Ubuntu version   | SoC      | Storage |
|--------|----------------|------------------|----------|---------|
| Monza2 | 192.168.1.185  | Noble (24.04)    | QCS8300  | eMMC    |
| Hamoa  | 192.168.1.123  | Resolute (26.04) | X1E80100 | UFS     |

Default SSH password after flashing: `changeme12` (changed from `ubuntu` by flash scripts).

## Kernel trees (outside repo)

| Path | Description |
|------|-------------|
| `~/qualcomm/linux` | Ubuntu kernel tree — target for cherry-picks. Branch: `master-next`. |
| `~/qualcomm/qualcomm-linux` | Qualcomm upstream tree — source of Qualcomm patches (qcom-next tags). |
| `~/qualcomm/images/` | Ubuntu images, organised by `<os-version>/<release-tag>/` (e.g. `24.04/x11/`). |
| `~/qualcomm/carmel-tools/alpaca.py` | Board power/mode control (requires `sudo`). |

## Kernel rebase methodology

The Ubuntu tree history is **always linear** — never merge, always cherry-pick.

```
upstream kernel X.Y
  └── Ubuntu SAUCE patches
        └── Qualcomm cherry-picks (linear, newest on top)
              └── "UBUNTU: Start new release" / packaging commits
```

A qcom-next tag in `~/qualcomm/qualcomm-linux` is a merge commit with two parents:
- `parent 1` = upstream kernel base (e.g. "Linux 7.1-rc2")
- `parent 2` = qcom-next tip (all Qualcomm-specific patches)

See `2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md` for the complete procedure including automation scripts.

### Identifying commits to cherry-pick

```bash
# Extract all qcom-specific commits from the new tag (in qualcomm-linux)
git -C ~/qualcomm/qualcomm-linux log --no-merges --format="%H %s" \
    "$QCOM_TIP" ^"$UPSTREAM_BASE" > /tmp/qcom_all_commits.txt

# Get subjects already applied in the ubuntu tree
UBUNTU_UPSTREAM_BASE=$(git -C ~/qualcomm/linux log --oneline --no-merges | \
    grep "^.\{8\} Linux [0-9]" | head -1 | awk '{print $1}')
git -C ~/qualcomm/linux log --no-merges --format="%s" \
    HEAD ^"$UBUNTU_UPSTREAM_BASE" | grep -v "^UBUNTU:" > /tmp/ubuntu_applied_subjects.txt
```

Cross-reference with the Python script in `AGENT_REBASE_GUIDE.md §2.4` to produce `commits_to_cherrypick.txt` in oldest-first order.

### Cherry-pick execution

Use the automation script from `AGENT_REBASE_GUIDE.md §3` — it iterates a SHA file, auto-resolves simple conflicts by taking THEIRS, and halts for manual review otherwise. **Do not use `set -e`** in the loop — it breaks conflict detection.

After manually resolving a conflict:
```bash
git add <resolved-files>
git cherry-pick --continue --no-edit
# Remove the completed SHA from /tmp/shas_to_pick.txt, then re-run the script
```

### Conflict resolution strategy

For every conflict, check the **new tag's final state** of the file:
```bash
git -C ~/qualcomm/qualcomm-linux show "$QCOM_TIP":<path/to/file> | head -80
```

| Pattern | Resolution |
|---------|------------|
| Incoming removes code still present in qcom-tip's final file | KEEP OURS |
| Incoming removes code absent from final file | TAKE THEIRS |
| Empty insertion-point mismatch (new code at a spot HEAD doesn't have) | TAKE THEIRS |
| Clock/binding renames across DTS files | TAKE THEIRS |
| Commit deletes function still present in final file | KEEP OURS |
| Auto-resolver produces duplicate blocks | TAKE THEIRS + manual dedup |

After auto-resolving `Makefile`, verify no duplicate entries:
```bash
git diff HEAD -- arch/arm64/boot/dts/qcom/Makefile | grep "^+" | sort | uniq -d
```

### Graduated upstream gap (critical post-rebase check)

Commits that graduated from qcom-next into upstream *between* the two kernel versions are correctly excluded from the cherry-pick list (they're in the new upstream base), but **missing from our tree** (which is still on the old upstream). This causes DTC or build errors after all cherry-picks complete.

**Symptoms:**
```
Error: arch/arm64/boot/dts/qcom/foo.dtsi:N Label or path bar not found
No rule to make target 'arch/arm64/boot/dts/qcom/foo.dtb'
```

**Always diff first** before choosing a fix:
```bash
diff ~/qualcomm/linux/arch/arm64/boot/dts/qcom/foo.dtsi \
     ~/qualcomm/qualcomm-linux/arch/arm64/boot/dts/qcom/foo.dtsi
# Lines with '<' = ubuntu-only content — must be preserved if ubuntu-specific
```

| Fix approach | When to use |
|-------------|-------------|
| Copy wholesale from qcom-linux | All `<` lines are just old qcom content (no ubuntu additions) |
| Surgical insert of missing node/label | Ubuntu tree has additions absent from qcom-linux entirely |
| Cherry-pick graduated commit from qcom-linux | Changes are non-trivial or span multiple files |

### Known recurring conflict areas

| Subsystem | Files | Typical cause |
|-----------|-------|---------------|
| Coresight CTI | `drivers/hwtracing/coresight/coresight-cti-*.c`, `qcom-cti.h` | Register encoding refactors |
| Coresight TMC | `coresight-tmc-core.c`, `coresight-tmc.h` | sysfs ops refactoring |
| ICE / crypto clocks | `drivers/soc/qcom/ice.c`, many DTS files | Clock name renames |
| Display (DP) | `drivers/gpu/drm/msm/dp/dp_ctrl.c` | HPD handling refactors |
| RPMH regulator | `drivers/regulator/qcom-rpmh-regulator.c` | PMIC model additions |
| DTS Makefile | `arch/arm64/boot/dts/qcom/Makefile` | New board additions |

### C source build errors after rebase

| Pattern | Diagnosis | Fix |
|---------|-----------|-----|
| File ~50% shorter than qcom-linux version | Double cherry-pick: FROMLIST + FROMGIT pair both applied | Copy wholesale from qcom-linux (if no ubuntu-specific content) |
| `struct X has no member Y` in ubuntu-specific code | SAUCE commit missed adding the struct field | Surgical insert of missing field + initialiser |
| `redefinition of 'function_name'` | Two cherry-picks left duplicate function bodies | Remove older body; verify correct form against qcom-linux |
| Symbols undeclared that ARE in same file | `#ifdef` guard scope mismatch — ubuntu code fell outside conditional block | Check qcom-linux for correct guard boundaries |

After a rebase, always verify `qcs8300.dtsi` (or `monaco.dtsi` in kernel 7.0+) has no remaining `QCS8300_MMCX`/`QCS8300_MXC` constants — see `2139069_wrong_power_domain_id/analysis.md`.

## CBD remote kernel build (Hamoa / Resolute)

CBD is the Canonical remote kernel build system. Run from `~/qualcomm/linux`:

```bash
# Build qcom flavour (~35 min warm cache)
git push cbd -o native

# Build RT flavour
git push cbd -o native -o binary-qcom-rt

# Full automated push → build → deploy to Hamoa
~/qualcomm/qpa/cbd-deploy.sh
~/qualcomm/qpa/cbd-deploy.sh --flavour qcom-rt
~/qualcomm/qpa/cbd-deploy.sh --no-push --build-id kpawlak-resolute-<SHA>-<N>
```

Build ID format: `kpawlak-resolute-<SHORT_SHA>-<4DIGITS>/arm64`

```bash
ssh cbd.kernel ls kpawlak-resolute-<ID>/arm64 | grep -oE "BUILD-OK|BUILD-FAILED|BUILDING|QUEUED"
ssh cbd tarball kpawlak-resolute-<ID>/arm64 > tarball.tgz
```

## Board control

```bash
sudo ~/qualcomm/carmel-tools/alpaca.py on   # power on
sudo ~/qualcomm/carmel-tools/alpaca.py off  # power off
sudo ~/qualcomm/carmel-tools/alpaca.py edl  # signal EDL mode
```

**Critical EDL sequence:** `alpaca.py off` must precede `alpaca.py edl` — without the power-off, USB flashing port `05c6:9008` won't enumerate. Confirm with `lsusb | grep 05c6:9008`.

## Hamoa fan control

Driver: `qcom-hamoa-ec` (I2C device `1-0076`). Two fans exposed as Linux thermal cooling devices and (from kernel `7.0.0-1006.10ubuntu2`) as hwmon `pwm` nodes. Both interfaces write to the same EC hardware.

| Fan | Thermal sysfs path | hwmon path |
|-----|-------------------|------------|
| 0 | `/sys/class/thermal/cooling_device3/cur_state` | `/sys/class/hwmon/hwmon0/pwm1` |
| 1 | `/sys/class/thermal/cooling_device4/cur_state` | `/sys/class/hwmon/hwmon0/pwm2` |

Range 0–255. To convert a percentage: `speed = pct * 255 / 100`. `cooling_device5..7` are PCIe link speed and GPU devfreq — not fans.

```bash
# Find the hwmon path (name = "qcom_ec")
grep -rl "^qcom_ec$" /sys/class/hwmon/hwmon*/name | sed "s|/name||"

# Read current speed
cat /sys/class/thermal/cooling_device3/cur_state

# Set both fans to 50% (128/255) via hwmon
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm1
echo 128 | sudo tee /sys/class/hwmon/hwmon0/pwm2

# Or via thermal cooling device (always available, any kernel)
echo 128 | sudo tee /sys/class/thermal/cooling_device3/cur_state
echo 128 | sudo tee /sys/class/thermal/cooling_device4/cur_state
```

**No RPM readback:** the EC has no I2C read command for current fan speed (confirmed by exhaustive register probing). `pwmN` reads return the **last-written value**, not a live hardware measurement. `fan*_input` entries are absent. The thermal governor overrides `cur_state` on the next thermal event — manual writes are transient.

See `fan_control/fan_control.md` for full discovery notes.



All flash scripts run from the **repo root** (`qpa/`), not `ai_effort/`. See `qpa/.github/copilot-instructions.md` for full flashing details.

```bash
cd ~/qualcomm/qpa
./flash-monza2.sh ~/qualcomm/images/24.04/x11   # two-phase: CDT then OS
./flash-hamoa.sh  ~/qualcomm/images/26.04/x02   # single-phase: OS only
```

`qdl: firehose operation timed out` at the end of each phase is **expected** (board reset).

## Bug/patch directories (`2XXXXXXX_*/`)

Each directory corresponds to a Launchpad bug. Typical contents:
- `analysis.md` — bug description, root cause, patch discussion, rebase action items
- `*.patch` — proposed kernel patches
- `AGENT_REBASE_GUIDE.md` — step-by-step instructions for a specific rebase task

These directories accumulate context for future sessions. Always check the analysis doc before modifying related kernel code.

## Required tools

`qdl`, `sshpass`, `expect` must be in `PATH`. `alpaca.py` is at `~/qualcomm/carmel-tools/alpaca.py`.
