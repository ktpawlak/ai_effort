# FIT image comparison — `qcom.itb` (existing) vs `dtb.bin` (new in PR #111)

Both are FIT images generated from the **same two source files**:
`debian.qcom/fitimage/qcom-next-fitimage.its` and
`debian.qcom/fitimage/qcom-metadata.dts`. That is the only thing they share.

- Existing: `do_fitimage` block, `debian/rules.d/2-binary-arch.mk:197-210`
  (added by `544acfcd732a UBUNTU: [SAUCE] fit image generation`).
- New: `do_dtb_capsule` block, `debian/rules.d/2-binary-arch.mk:212-304`, which
  calls `debian.qcom/fitimage/build-dtb-image.sh`.

---

## Side-by-side

| | existing `do_fitimage` → `qcom.itb` | new `do_dtb_capsule` → `dtb.bin` |
|---|---|---|
| Built by | inline `mkimage` in the make recipe | `build-dtb-image.sh` (707 lines) |
| `dtc` used | kernel's own `$(build_dir)/scripts/dtc/dtc` | system `dtc` (`device-tree-compiler`) |
| DTB source | kernel build tree; `/incbin/` paths resolve natively against `$(build_dir)/arch/arm64/boot/dts/qcom/` | the **installed** dir `$(pkgdir)/usr/lib/firmware/<kver>/device-tree/qcom`, flattened by `find -L` into a `mktemp` staging tree that re-creates `arch/arm64/boot/dts/qcom/` |
| DTB content | pristine | DTBs first mutated by `fdtput` to carry the `/qcom-dtb-capsule-provenance` node → byte-for-byte different |
| ITS used | full, verbatim: ~48 image entries, ~55 configurations, all SoCs | `--soc hamoa purwa` filter (validated against `/soc` subnodes of `qcom-metadata.dts`), then `--prune` |
| `--prune` effect | n/a | drops every image entry whose `/incbin/` DTB is absent from the staged source, drops any configuration referencing a dropped label, and **renumbers surviving `conf-N` sequentially** |
| `mkimage` flags | none → **inline data** (blobs embedded in the FDT) | `-E -B 8` → **external data** (payloads appended after the FDT, 8-byte aligned) |
| Output filename | `qcom.itb` | `qclinux_fit.img` — hardcoded in UEFI firmware as `#define FIT_BINARY_FILE L"\\qclinux_fit.img"` |
| Container | raw `.itb` file on the rootfs | wrapped in a **4 MB FAT filesystem**: `dd if=/dev/zero count=4` + `mformat -S 5` (4096-byte sectors) + `mcopy` |
| Destination | `/usr/lib/firmware/<kver>/device-tree/qcom/qcom.itb`, inside `linux-modules-<kver>-qcom` | capsule payload → spinor `dtb_a` / `dtb_b` raw partitions |
| Consumer / stage | GRUB / u-boot at OS boot, read from the mounted rootfs | UEFI firmware pre-boot, read from raw flash |
| Per-flavour? | yes — built for every flavour | no — built once, from the first flavour only |
| Authentication | none | outer FMP capsule PKCS#7 only; the FIT itself is still unsigned (see `capsule-signing-analysis.md`) |

---

## Practical consequences

1. **The capsule FIT is a strict subset of `qcom.itb`.** Since commit
   `cea843c74c09` the prune source is the *installed* device-tree dir, so
   `.dtbo` fragments that exist only as overlay build inputs and are never
   `dtbs_install`ed get silently dropped, taking their `conf-N` entries with
   them. A board can therefore be bootable via `qcom.itb` yet absent from the
   capsule. The dropped entries are printed as `[WARN] --prune: dropped ...`,
   but that output is buried in the build log.

2. **`conf-N` renumbering** means capsule configuration indices do not
   correspond to `qcom.itb`'s. Harmless as long as selection is by
   `compatible` (it is), but the two images are not comparable by index.

3. **Fixed 4 MB FAT with no headroom check.** `--size 4` is the default and the
   rules pass it explicitly. If the hamoa+purwa DTB set outgrows it, `mcopy`
   fails — and that failure is swallowed by the `;`-joined make recipe
   (blocking finding #3 in `pr111-review.md`), yielding a package with no
   capsule and no build error.

4. **`-E` vs inline** means the two images are not byte-comparable even for an
   identical DTB set. That is why the provenance sha256 is computed over the
   loose `.dtb`/`.dtbo` files rather than over either image.

5. **Two platforms, one image, two capsules.** `--soc hamoa purwa` produces a
   single `dtb.bin` covering both; it is then signed twice, once per platform,
   with different `FMP_GUID` / `-T TARGET` values (`IQ-X7181` vs `IQ-X5121`).
   postinst picks the right `.cap` by matching the packaged GUID against the
   device's ESRT `fw_class`.

6. **Divergent `dtc` binaries.** The old path uses the kernel's freshly built
   `scripts/dtc/dtc`, the new path the distro `dtc`. Both compile the same
   `qcom-metadata.dts`, so a version skew between them could in principle
   produce differing metadata DTBs between the two images.
