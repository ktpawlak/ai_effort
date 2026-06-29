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

Extract new commits by diffing qcom-tip against upstream base, then cross-referencing commit subjects already applied in the Ubuntu tree. See `2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md` for the full procedure.

**Common conflict patterns:**
- Qualcomm patches touching the same DTS nodes or driver files as Ubuntu SAUCE patches.
- `rej` files indicate failed hunks — resolve by reading context, then `git add` + `git cherry-pick --continue`.
- After a rebase, always verify `qcs8300.dtsi` (or `monaco.dtsi` in 7.0+) has no remaining `QCS8300_MMCX`/`QCS8300_MXC` constants (see `2139069_wrong_power_domain_id/analysis.md`).

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

## Flashing

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
