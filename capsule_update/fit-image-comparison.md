# FIT image comparison — `qcom.itb` (existing) vs `dtb.bin` (new in PR #111)

> **RESOLVED (2026-10-03): `qcom.itb` no longer exists.** The kernel package's
> FIT generation was removed in linux-qcom `fbbe71beb3f2` "UBUNTU: [Packaging]
> Drop FIT image generation from the kernel package", which effectively reverts
> `544acfcd732a`. `linux-signed` is now the sole owner of
> `qcom-next-fitimage.its` / `qcom-metadata.dts`, and the capsule FIT
> (`qclinux_fit.img` → `dtb.bin`) is the only FIT produced.
>
> Nothing consumed `qcom.itb` — it was referenced only at its creation site —
> so the removal was self-contained. `device-tree-compiler` stayed in
> Build-Depends (its `fdtput` stamps the DTB build version under
> `do_dtbs_version`); only `u-boot-tools` became unnecessary.
>
> **The comparison below is retained as history.** The left-hand column
> describes a build path that no longer exists; the right-hand column is still
> accurate and is now the whole story. The drift hazard documented at the end
> is resolved by construction.

> **Update (2026-10-03, superseded by the above): the two no longer share
> source files.** When the capsule build moved out of the kernel package into
> `linux-signed`, the ITS and metadata DTS were **copied**, not shared. There
> were then two byte-identical pairs in two different source packages:
>
> | | path |
> |---|---|
> | kernel (`linux-main`) | `debian.qcom/fitimage/{qcom-next-fitimage.its,qcom-metadata.dts}` |
> | signed (`linux-signed`) | `debian/capsule/fitimage/{qcom-next-fitimage.its,qcom-metadata.dts}` |
>
> A Debian source package cannot read another's build inputs, and
> `linux-modules` ships only the *built* `qcom.itb`, not the `.its` — so the
> copy was the only option available at the time. **Nothing kept them in
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

---

## Resolution (2026-10-03)

The duplication was resolved in the opposite direction to the recommendation
above: rather than making the kernel package the owner and having `-signed`
read the installed copy, the kernel's FIT generation was **deleted** and
`-signed` became the sole owner. That is simpler, and it is the right call
because nothing consumed `qcom.itb` — the capsule is the only consumer of a
FIT in this stack.

Removed from `linux-qcom` (commit `fbbe71beb3f2`, effectively reverting
`544acfcd732a`):

- the `do_fitimage` block in `debian/rules.d/2-binary-arch.mk`
- `do_fitimage = true` in `debian.qcom/rules.d/arm64.mk`
- the `do_fitimage=false` default in `debian/rules.d/0-common-vars.mk` and the
  `printenv` echo in `debian/rules.d/1-maintainer.mk`
- `debian.qcom/fitimage/` (both files)
- `u-boot-tools` from `Build-Depends` in `debian.qcom/control.stub.in`

**`device-tree-compiler` was deliberately kept.** It was added by the same
commit and so looks like it should go too, but its `fdtput` is what stamps the
DTB build version under `do_dtbs_version`, which is still enabled. Removing it
would have broken the build.

Consequences for the table above: every "existing `qcom.itb`" column entry is
now historical. The "Practical consequences" items 1, 2, 4 and 6 lose their
comparative framing — there is no second image to compare against — though
item 2's renumbering and item 3's fixed 4 MB FAT remain live properties of the
capsule FIT. The drift hazard is resolved by construction.

### Related: contiguous `conf-N` numbering is a hard requirement

Applied to `-signed` at the same time (from Krzysztof Adamski's
`0002-UBUNTU-SAUCE-fix-FIT-image-definition.patch`, originally written against
the kernel package's now-deleted copy): the ITS had gaps in its `conf-N`
sequence (it skipped 20, 21, 23, 25, 26, 27, 33, 35, 38, 42, 48 …) because
entries had been removed over time. Undocumented upstream, but a
non-contiguous sequence makes the bootloader reject the image:

```
qclinux_fit.img Loading Failed status=0x2
```

The patch is pure renumbering — 49 changed lines, all of the form `conf-N {`,
with the 51 configurations and 48 `fdt` images otherwise untouched and no
`default` property to repoint.

Worth noting that **the capsule was already immune to this by accident**:
`build-capsule-payload.sh` always passes `--prune`, and the prune pass
renumbers surviving configurations sequentially from `conf-1`. The error above
names `qclinux_fit.img`, so it was presumably hit before that renumbering
existed. The fix was still applied to the source, because the renumbering only
covers entries `--prune` keeps and relying on a side effect of an optional
pass is not a guarantee.

### The companion patch 0001 needs no action

`0001-UBUNTU-SAUCE-Fit-image-format-refinement.patch` adds `-E -B 8` (external
data, 8-byte alignment) to the kernel package's `mkimage` call, without which
the bootloader cannot load the image. It requires no action on either side:

- **Kernel package** — moot, that `mkimage` call no longer exists.
- **`linux-signed`** — already satisfied. `build-dtb-image.sh:680` has always
  been `mkimage -f "${DEFAULT_ITS_FILE}" out/qclinux_fit.img -E -B 8`, copied
  from the reference script at
  `https://github.com/qualcomm-linux/qcom-dtb-metadata.git`, which is where the
  patch's rationale comes from in the first place.

So both patches in that series are accounted for: 0001 by construction, 0002
by the renumbering commit above. Neither should be re-applied.

---

## Why there are two capsules (2026-10-05)

Measured, not inferred: both capsules were built from an identical payload and
compared byte by byte.

**The two capsules differ only in the FMP GUID.** Everything else is identical:

| | hamoa | purwa | |
|---|---|---|---|
| `dtb.bin` | — built **once** from `--soc hamoa purwa`, copied into both — | identical |
| `TARGET` | `IQ-X7181` | `IQ-X5121` | **no effect** |
| `FvUpdate.xml` | | | byte-identical |
| `SYSFW_VERSION.bin` | | | byte-identical |
| `firmware.fv` | | | identical modulo a random GUID (below) |
| `config.json` `Guid` | `0F6D58FC-…` | `185a798b-…` | **the only real difference** |

`TARGET` is a no-op because `UpdateFvXml.SUPPORTED_PLATFORMS` maps **both**
`IQ-X7181` and `IQ-X5121` to the same qcom-ptool dir `iq-x7181-evk`, so both
resolve the same `partitions.conf`.

### Two capsule *files* are genuinely required

The FMP GUID is the `UpdateImageTypeId` in
`EFI_FIRMWARE_MANAGEMENT_CAPSULE_IMAGE_HEADER`. Firmware matches it against the
`ImageTypeId` of its FMP instance — the value a device publishes as
`/sys/firmware/efi/esrt/entries/*/fw_class`. A capsule carrying only Hamoa's
GUID is rejected on Purwa and vice versa, so one file per platform is needed.
`signed-install` already picks the right one by matching ESRT `fw_class`.

### But two *signatures* are not

The FMP GUID lives in the image header, which is **outside**
`EFI_FIRMWARE_IMAGE_AUTHENTICATION`. The design already relies on this — the
`.capsule.vars` comment says "only the FMP image-header fields, which are
outside it, have to travel".

Verified: the FMP GUID appears **nowhere** inside `firmware.fv` (searched
`bytes_le`, `bytes_be` and ASCII; all absent), and the two `--emit-signable`
blobs differ in only 34 bytes.

Those 34 bytes are **not platform data — they are build nondeterminism**. Two
builds of *the same* platform, from identical inputs, differ in exactly the
same 34 bytes at exactly the same offsets. Masking them makes both pairs
byte-identical:

```
hamoa vs purwa    :  34 differing bytes
hamoa vs hamoa #2 :  34 differing bytes   <-- same platform, same inputs
same offsets?     : True
masked: hamoa vs purwa IDENTICAL, hamoa vs hamoa#2 IDENTICAL
```

Source: `XmlFwEntryValidation.py:395` calls `uuid.uuid4()` for the FFS
`FileGuid` whenever the FwEntry XML does not pin one, which ours does not. It
is embedded twice, plus one checksum byte.

**So the signed content is entirely platform-independent**, and we were sending
Launchpad two signing requests for what would otherwise be identical bytes.

**Fixed** in `linux-signed` `44ff45d`: `XmlFwEntryValidation.py` now honours
`QCOM_CAPSULE_FILE_GUID`, which `build-capsule-payload.sh` derives from the DTB
provenance hash (`uuid5` over `urn:ubuntu:qcom-dtb-capsule:<provenance>`) so it
stays distinct per payload, as an FFS file identifier should be. Measured
after the change:

```
hamoa run1 vs run2 : IDENTICAL   (reproducible)
hamoa      vs purwa: IDENTICAL   (one signature serves both)
config.json GUIDs  : 0F6D58FC-… / 185a798b-…  (still distinct, as required)
```

With the variable unset, upstream's random behaviour is preserved exactly (two
builds still differ by the same 34 bytes); an unparseable value fails the build
rather than silently falling back. Nothing can be matching on the value, since
upstream leaves it random.

### Decision: two capsules, two signatures — deliberately

The byte-identical blobs make it *possible* to sign once and wrap the result
into both capsules. **This was considered and rejected.** Two signing requests
are not a problem; a pipeline in which a capsule's signature did not come from
that capsule's own blob is. The saving is one request per upload, and the cost
is a non-obvious coupling that every future reader of `signed-build` would have
to understand before touching it.

So the flow stays as it is: one blob, one `.sig` and one `.capsule.vars` per
machine, assembled independently. `signed-build` requires a signature beside
each blob and fails if one is missing (unless `--allow-unsigned` is declared
explicitly). The pinning is kept purely for reproducibility, which is worth
having on its own.

Verified that the two-capsule path is handled properly end to end:

- **`signed-build`** iterates each `*.capsule` blob, derives the machine from
  its directory, loads that machine's `.capsule.vars`, assembles with that
  machine's `CAPSULE_GUID`, and re-reads the result with `--dump-info` so a
  truncated signature is caught at build time rather than on the device.
- **`signed-install`** ships every machine's capsule in one per-flavour binary
  package and hard-fails if any machine's capsule was not assembled.
- **postinst** reads the device's ESRT `fw_class` entries and matches them
  against each packaged `capsule.env`'s `FMP_GUID`, lowercasing both sides —
  which matters, since hamoa's GUID is written uppercase and purwa's lowercase.
  It requires exactly one match: zero matches skip staging with a warning, and
  two or more are treated as an ambiguity, recorded in `last-guid-conflict` and
  surfaced by `verify-capsule-result.sh`.

### A single merged capsule is possible but not worth it

`config.json` has a `Payloads` **array**, and `generate_capsule.py` builds
`ItemOffsetList` with `PayloadItemCount = len(items) - embedded_driver_count`,
so one capsule could carry both GUIDs as two payload items. Rejected: each item
embeds its own copy of the 4 MB FV (≈8 MB shipped to every device, half of it
never used), and `EFI_FIRMWARE_IMAGE_AUTHENTICATION` is per-payload, so it
would still need two signatures. Two files sharing one signature is the better
shape.
