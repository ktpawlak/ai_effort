# FIT image comparison — `qcom.itb` (existing) vs `dtb.bin` (new in PR #111)

> **Update (2026-10-03): the two no longer share source files.** When the
> capsule build moved out of the kernel package into `linux-signed`, the ITS
> and metadata DTS were **copied**, not shared. There are now two
> byte-identical pairs in two different source packages:
>
> | | path |
> |---|---|
> | kernel (`linux-main`) | `debian.qcom/fitimage/{qcom-next-fitimage.its,qcom-metadata.dts}` |
> | signed (`linux-signed`) | `debian/capsule/fitimage/{qcom-next-fitimage.its,qcom-metadata.dts}` |
>
> A Debian source package cannot read another's build inputs, and
> `linux-modules` ships only the *built* `qcom.itb`, not the `.its` — so the
> copy was the only option available at the time. **Nothing keeps them in
> sync.** See "Drift between the two ITS copies" at the end of this file.

Both are FIT images generated from the **same two source files**:
`debian.qcom/fitimage/qcom-next-fitimage.its` and
`debian.qcom/fitimage/qcom-metadata.dts`. That is the only thing they share.
(As of the split above, "same" means "identical copies", not "one file".)

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

---

## Drift between the two ITS copies

Since the capsule build moved to `linux-signed`, `qcom-next-fitimage.its` and
`qcom-metadata.dts` exist twice, once per source package. They are currently
byte-identical (`diff` clean, 12104 B / 3315 B), and **no build step, test or
checksum enforces that**.

### Why this is not as bad as it looks

The capsule does **not** carry its own DTBs. `build-capsule-payload.sh` takes
them from the *installed* `linux-modules` tree
(`/usr/lib/firmware/<kver>/device-tree/qcom/`). So DTB **content** can never
drift — it always comes from the kernel binary package. Only the ITS
**node list and configuration structure** is duplicated.

### Why it is still a real hazard

The two drift directions fail very differently:

| drift | effect | detected? |
|---|---|---|
| Kernel **adds** a board to its ITS; signed copy not updated | the new DTB is installed, but the signed ITS never references it, so it is simply absent from the capsule | **No. Completely silent.** `--prune` only *drops* entries whose DTB is missing; it never *adds* entries for DTBs it was not told about. |
| Kernel **stops building** a DTB; signed copy still lists it | `--prune` drops the entry and its `conf-N` | Yes — `[WARN] --prune: dropped ...`, though buried in the build log |

The dangerous direction is the silent one, and it is also the likely one: new
boards get added to the kernel. The result is a capsule that updates the `dtb`
partition with a FIT that is missing the newest board, which is exactly the
kind of regression capsule update is supposed to prevent.

Note that **provenance does not catch this.** `expected-dtb-sha256` is computed
over `dtb-provenance-content-sha256sums.txt`, i.e. over the DTB *files*, never
over the ITS. A stale signed-side ITS produces a capsule that passes every
provenance check while containing fewer boards than the kernel supports.

### Recommended fix — single source of truth

Make the kernel package the owner and have `-generate-` read its copy, which is
the same principle already applied to provenance ("let `-generate-` read these
files, do not recompute them"):

1. In `debian/rules.d/2-binary-arch.mk`, in the existing `do_fitimage` block,
   install the two inputs next to the output they produce:

   ```make
   install -m644 $(CURDIR)/$(DEBIAN)/fitimage/qcom-next-fitimage.its \
                 $(CURDIR)/$(DEBIAN)/fitimage/qcom-metadata.dts \
       $(pkgdir)/usr/lib/firmware/$(abi_release)-$*/device-tree/qcom/
   ```

   15 KB in `linux-modules`, and it makes the package self-describing.

2. Point `build-dtb-image.sh` at the installed copy (it already locates the
   DTB directory via the `dpkg -S` → `apt-get download` → `dpkg-deb -x`
   pattern, so the path is already in hand), and delete
   `debian/capsule/fitimage/*.its` / `*.dts` from `linux-signed`.

Until that is done, treat the two files as a matched pair: **any change to
`debian.qcom/fitimage/` must be mirrored into `linux-signed`.**

### Cheap interim guard

Without any kernel change, the silent direction can still be detected, because
`qcom.itb` *is* shipped in `linux-modules` and encodes the kernel's full
configuration list. Comparing the `compatible` strings of its `conf-*` nodes
against those of the pruned capsule FIT, and failing the build on entries
present in `qcom.itb` but absent from the capsule, would turn the silent
failure into a build error. Not implemented.
