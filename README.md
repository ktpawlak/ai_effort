# ai_effort

Welcome! 👋

This repository is a working knowledge base for **AI-assisted Qualcomm ARM
kernel development at Canonical**. It collects the investigation notes, kernel
patches, automation scripts, and hard-won board knowledge produced while
debugging and maintaining Ubuntu on Qualcomm development boards.

## The idea

Most of the work here starts from a **Launchpad bug**. For each one we capture:

- the **root-cause analysis** (`analysis.md` / `README.md`),
- the **proposed patches** (`*.patch`), and
- any **rebase or reproduction guides** needed to act on it.

The goal is to make every effort **self-contained and resumable** — a future
session (human or AI) can open a directory and pick up exactly where the last
one left off, without re-discovering the same board quirks and kernel pitfalls.

See [`SUMMARY.md`](SUMMARY.md) for a one-page index of every ticket analysis.

## Layout

| Directory | Purpose |
|-----------|---------|
| `2XXXXXXX_*/` | Per-Launchpad-bug analysis: patches, notes, rebase guides |
| `qpa/` | Board flashing and test automation |
| `keyboard_gadget/` | USB HID keyboard gadget to drive a DUT remotely (via Raspberry Pi 4) |
| `fan_control/` | Hamoa fan control investigation |
| `hamoa_slim_initramfs/`, `monza2_slim_initramfs/` | Slim/minimal initramfs experiments |
| `config_trim/` | Kernel config trimming notes |
| `overlay/` | DTB overlay tutorial |

## Boards

| Board  | IP address     | Ubuntu version   | SoC      | Storage |
|--------|----------------|------------------|----------|---------|
| Monza2 | 192.168.1.185  | Noble (24.04)    | QCS8300  | eMMC    |
| Hamoa  | 192.168.1.123  | Resolute (26.04) | X1E80100 | UFS     |

## Getting started

- Browse [`SUMMARY.md`](SUMMARY.md) for the ticket index.
- Open the relevant `2XXXXXXX_*/` directory and read its `analysis.md` before
  touching related kernel code.
- Rebase methodology and repo conventions live in
  [`.github/copilot-instructions.md`](.github/copilot-instructions.md) and
  [`2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md`](2153998_june_patchset_rebase/AGENT_REBASE_GUIDE.md).

> Note: kernel trees, images, and board-control tools live **outside** this repo
> (under `~/qualcomm/`). This repository holds the analysis and tooling, not the
> kernel sources themselves.
