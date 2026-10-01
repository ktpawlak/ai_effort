# Enabling UEFI signing for the Qualcomm kernel

Prerequisite for attaching `linux-signed-qcom`: the kernel source package has to
produce `linux-image-unsigned-*` binaries, and those binaries have to contain
something Launchpad can actually sign.

Two independent problems. The first stopped the build outright and **is fixed**.
The second passes the build and then fails in the signing service; it is
**documented but deliberately not fixed** — the tree still builds `Image.gz`.

Read the status banner on each section before using it.

---

## 1. `do_uefi_signed` is not a variable anybody reads

### Symptom

```
dh_prep: error: Requested unknown package linux-image-7.0.0-1014-qcom via -p/--package,
  expected one of: ... linux-image-unsigned-7.0.0-1014-qcom ...
make: *** [debian/rules.d/2-binary-arch.mk:148: .../stamp-install-qcom] Error 255
```

Note the direction: `debian/control` had already been generated with the
**unsigned** names, while the install rules were asking for the **signed** name.
Half the build thought signing was on and the other half thought it was off.

### Cause

`debian.qcom/rules.d/arm64.mk` said

```make
do_uefi_signed   = true
```

but the variable the packaging reads is `uefi_signed`, with no `do_` prefix
(`debian/rules:86`):

```make
any_signed=$(sort $(filter-out false,$(uefi_signed) $(opal_signed) $(sipl_signed)))
ifeq ($(any_signed),true)
bin_pkg_name=$(bin_pkg_name_unsigned)
else
bin_pkg_name=$(bin_pkg_name_signed)
endif
```

Most `rules.d` knobs *are* spelled `do_*` (`do_dtbs`, `do_fitimage`,
`do_tools_perf`…), so `do_uefi_signed` looks right. The signing flags are the
exception — compare `debian.master/rules.d/arm64.mk`, which says plain
`uefi_signed = true`.

So to make it fail quietly, `uefi_signed` was empty → `any_signed` empty →
`bin_pkg_name = linux-image-…` (the *signed* name).

### Why `debian/control` disagreed

`debian/scripts/control-create` does not evaluate make. It greps (line 64):

```bash
if grep -q -E '(uefi|opal|sipl)_signed[[:space:]]*=[[:space:]]*true' "${DEBIAN}/rules.d/${a}.mk"; then
```

The regex is **unanchored**, so the string `do_uefi_signed   = true` contains
`uefi_signed   = true` and matches. control-create therefore emitted the
unsigned package names while make computed the signed ones.

Demonstrated:

| file content | control-create | make |
|---|---|---|
| `do_uefi_signed   = true` | SIGNED | `uefi_signed` = *(empty)* → `linux-image-…` |
| `uefi_signed     = true`  | SIGNED | `uefi_signed` = `true` → `linux-image-unsigned-…` |

Two different answers from one typo. A `do_`-prefixed signing flag is
*specifically* the worst way to get this wrong: anchoring the regex would have
turned this into an honest "signing is off" instead of a contradiction.

### Fix

```diff
-do_uefi_signed   = true
+uefi_signed	= true
```

Verified by A/B on the real tree (`DEB_HOST_ARCH=arm64 make -f debian/rules printenv`):

```
before:  uefi_signed =         bin_pkg_name = linux-image-7.0.0-1014
after:   uefi_signed = true    bin_pkg_name = linux-image-unsigned-7.0.0-1014
```

> Note when reproducing locally: `arch := $(DEB_HOST_ARCH)`, so on an x86_64
> workstation make includes `rules.d/amd64.mk`, which does not exist here —
> `flavours` and `uefi_signed` both come back empty and the test looks like it
> failed. Force `DEB_HOST_ARCH=arm64`.

---

## 2. `Image.gz` cannot be signed — open, **not** fixed

> **Status: investigated, measured, and deliberately left alone.** The tree
> still builds `Image.gz`. Everything below is the evidence for the decision
> that still has to be made, not a description of the current tree.

Fixing (1) gets a clean build, but Launchpad will then reject the payload.

`debian/package.config` asks for `sig_type: efi`, which is `sbsign`, which
requires a **PE/COFF** binary. The tree builds:

```make
build_image	= Image.gz
kernel_file	= arch/$(build_arch)/boot/Image.gz
```

`Image.gz` is a bare gzip stream. Measured on a real build of this tree:

| file | magic | `file(1)` | size |
|---|---|---|---|
| `Image` | `4d5a` (`MZ`) | PE32+ EFI application | 51 264 000 |
| `Image.gz` | `1f8b` | gzip compressed data | 15 817 760 |
| `vmlinuz.efi` | `4d5a` (`MZ`) | PE32+ EFI application Aarch64 | 15 876 608 |

`sbsign` against each:

```
Image.gz     -> Invalid DOS header magic        (exit 1)
vmlinuz.efi  -> Signing Unsigned original image (exit 0)
                sbverify: Signature verification OK
```

Uncompressed `Image` *is* signable — with `CONFIG_EFI=y` the arm64 kernel carries
a real PE header, built by `__EFI_PE_HEADER` in `arch/arm64/kernel/efi-header.S`
(the `ccmp x18, #0, #0xd, pl` instruction whose opcode spells `MZ`). But it costs
35 MB.

### Consequence of leaving it as `Image.gz`

The split in (1) works: the kernel builds `linux-image-unsigned-*` and
`linux-signed-qcom` can be attached. But the `efi` signing request for
`/boot/vmlinuz-<abi>-<flavour>` will fail in the signing service, because that
file is `Image.gz` under a different name.

So the packaging is correct and the signing step is not yet viable. That is a
known, bounded gap — not a silent one.

### The option if/when it is taken: `CONFIG_EFI_ZBOOT`

`vmlinuz.efi` is a PE wrapper around the *same* compressed payload — the EFI
decompressor. That is what `debian.master` already does on arm64.

```diff
-build_image	= Image.gz
-kernel_file	= arch/$(build_arch)/boot/Image.gz
+build_image	= vmlinuz.efi
+kernel_file	= arch/$(build_arch)/boot/vmlinuz.efi
```

**The cost is 58 848 bytes — 0.37 %.** It is the same gzip payload plus a PE
header, not a re-compression.

### Config fallout, which is easy to miss

`EFI_ZBOOT` `select`s `HAVE_KERNEL_GZIP` and `HAVE_KERNEL_ZSTD`, and on arm64
*nothing else does*. Turning it on therefore makes three previously-invisible
symbols appear, and the annotations enforcement will flag every one:

| symbol | before | after | why |
|---|---|---|---|
| `CONFIG_EFI_ZBOOT` | `n` | `y` | the change itself |
| `CONFIG_KERNEL_GZIP` | `-` | `y` | compression choice becomes visible, `default KERNEL_GZIP` |
| `CONFIG_KERNEL_ZSTD` | `-` | `n` | the other arm of the same choice |
| `CONFIG_EFI_SBAT_FILE` | `-` | `""` | `depends on EFI_ZBOOT \|\| (EFI_STUB && X86)` |

All four were confirmed by running Kconfig rather than by reading it:
`scripts/config --enable EFI_ZBOOT` + `make olddefconfig` produced exactly
`EFI_ZBOOT=y`, `KERNEL_GZIP=y`, `# CONFIG_KERNEL_ZSTD is not set`,
`EFI_SBAT_FILE=""`.

Keeping gzip (rather than following generic to zstd) makes this a wrapper-only
change: the payload bytes are what the platform was already booting.

The two notes saying `we use arch/arm64/boot/Image.gz` would need updating too.

### Would this break the Qualcomm boot path?

- The capsule/FIT work is unaffected — `qcom-next-fitimage.its` bundles **DTBs
  only**, no kernel image. The two are independent.
- The platform must already run UEFI, or ESRT capsule update could not work at
  all, so grub loading `vmlinuz.efi` is the expected path.
- For non-EFI loaders, the zboot header describes the compression and the
  payload can still be extracted (`drivers/firmware/efi/Kconfig`, `EFI_ZBOOT`
  help text). U-Boot supports this.

Unproven, and the reason this was not taken: nobody has booted `vmlinuz.efi` on
these boards. The `we use arch/arm64/boot/Image.gz` annotation presumably
encodes a Qualcomm boot-chain requirement that is not written down here. That
needs establishing before the switch, not after.

---

## 3. `qcom-rt` had no signed counterpart

Not part of the reported symptom; found while checking the change.

With `uefi_signed = true` the kernel stops producing `linux-image-*` for **both**
flavours and produces `linux-image-unsigned-*` instead. Those are then produced
by `linux-signed-qcom` — but only for flavours listed in `debian/package.config`,
which had just:

```
sign arm64 efi vmlinuz qcom
```

`linux-meta` depends on `linux-image-${kernel-abi-version}-qcom-rt`
(`debian/control.d/qcom-rt:13`). Nothing would have built it, so
`linux-image-qcom-rt` would have become uninstallable the moment signing was
switched on. Added:

```
sign arm64 efi vmlinuz qcom-rt
```

Cross-checked afterwards:

```
KERNEL builds:   linux-image-unsigned-7.0.0-1014-{qcom,qcom-rt}
SIGNED builds:   linux-image-7.0.0-1014-{qcom,qcom-rt}
META requires:   linux-image-7.0.0-1014-{qcom,qcom-rt}      -> all satisfied
```

and the generated `files.json` now lists both `/boot/vmlinuz-7.0.0-1014-qcom`
and `/boot/vmlinuz-7.0.0-1014-qcom-rt` with `sig_type: efi`.

### Capsules deliberately stay `qcom`-only

A capsule describes **hardware**, not a kernel flavour; `hamoa` and `purwa` are
the same boards whichever kernel runs. Adding `capsule arm64 hamoa qcom-rt`
would also collide on disk: `signed-install` installs each machine directory to
the shared path `usr/share/dtb-capsule/`, so `dtb-capsule-…-qcom` and
`dtb-capsule-…-qcom-rt` would both ship `usr/share/dtb-capsule/hamoa/` and
conflict at unpack time.

If `-rt` ever needs its own capsule, that shared path has to be flavour-qualified
first.

---

## Changes actually applied

| repo | file | change |
|---|---|---|
| `linux-main` | `debian.qcom/rules.d/arm64.mk` | `do_uefi_signed` → `uefi_signed` (one line) |
| `linux-signed` | `debian/package.config` | `sign arm64 efi vmlinuz qcom-rt` |

## Deliberately **not** applied

| | |
|---|---|
| `Image.gz` → `vmlinuz.efi` | Investigated and measured (section 2). Reverted; the tree keeps `Image.gz` and `CONFIG_EFI_ZBOOT=n`, and the four config annotations are byte-identical to before. |

**Consequence to keep in view:** signing is now switched on at the packaging
level, so the kernel emits `linux-image-unsigned-*` and `linux-signed-qcom` can
be attached — but the `efi` signing request will fail, because the file at
`/boot/vmlinuz-<abi>-<flavour>` is a gzip stream. Section 2 has the evidence and
the one-line change that would resolve it when the boot-path question is settled.

## Still to confirm on hardware

- Whether the firmware's UEFI db actually trusts the Canonical cert LP signs
  with, or whether these boards need that provisioning (the same open question
  as for the capsule chain — see `launchpad-port-plan.md`).
- `linux-signed-qcom` must not be uploaded before the matching
  `linux-image-unsigned-*` is published, or `download-unsigned` has nothing to
  fetch.
