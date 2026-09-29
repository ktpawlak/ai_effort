# Review: qualcomm-linux/ubuntu-qcom-kernel#111

**Title:** `qcom: implement DTB UEFI capsule update with post-boot verification`
**Branch:** `GuanquanTian:dtb-capsule-resolute-devel-tip` → `resolute-qcom-devel`
**Size:** 7 commits, +8813 lines, 32 files
**Base for diff:** `e2d8ff83c07f` (`git diff e2d8ff83c07f..HEAD`)
**Related:** #112 (injects the capsule signing certs in CI)
**Reviewed:** 2026-09-10 at head `fe76d4244e37`
**Re-checked:** 2026-09-25 at head `9d7b0cc312d4` — see section 4. Findings
below are unchanged except #10, which is now fixed.

---

## 1. What the PR does

Enables UEFI capsule updates of the Qualcomm device tree blob (DTB) for the Ubuntu
Resolute kernel, by adding a new `dtb-capsule-<abi>-qcom` binary package to the
kernel's Debian packaging.

### Commits

| Commit | Summary |
|---|---|
| `733ba646d2f2` | vendor `qcom_capsule_tool` for UEFI DTB capsule generation |
| `ac62d60e15ae` | vendor `build-dtb-image.sh` to build SoC-filtered FIT DTB images |
| `81b188c74a6a` | build and ship a `dtb-capsule` package with post-boot verification |
| `cea843c74c09` | source dtb-capsule provenance manifest from installed device-tree dir |
| `52d9e36bca25` | rename spinor `dtb`/`dtb_BACKUP` partitions to `dtb_a`/`dtb_b` |
| `6c6dc8ec5ad9` | add `dtb-capsule-recovery` to switch GRUB default to a matching kernel |
| `fe76d4244e37` | ci: enable secrets inheritance in premerge PR workflow |

### Mechanism

1. **Vendored tooling**
   - `debian.qcom/scripts/qcom_capsule_tool/` — ~5k lines of Qualcomm Python
     (`cli.py`, `capsule_creator.py`, `fv_builder.py`, `generate_capsule.py`,
     `patch_capsule_cert.py`, `FVCreation*.py`, `UpdateFvXml.py`,
     `UpdateJsonParameters.py`, `XmlFwEntryValidation.py`, `XmlParser.py`, ...).
   - `debian.qcom/fitimage/build-dtb-image.sh` (707 lines) + existing
     `qcom-metadata.dts` / `qcom-next-fitimage.its`.
   - `debian.qcom/qcom-ptool/platforms/iq-x7181-evk/spinor/partitions.conf`.

2. **Build time** (`do_dtb_capsule = true`, arm64 only, first flavour only)
   - Collects the flavour's `.dtb`/`.dtbo` from the installed device-tree dir.
   - Builds a **FAT-wrapped, external-data FIT image** `dtb.bin`
     (`mkimage -E -B 8`, then `mformat` + `mcopy`). This is a *distinct artefact*
     from `do_fitimage`'s `qcom.itb`: it is consumed by UEFI firmware from the
     spinor `dtb_a`/`dtb_b` partitions pre-boot, not by GRUB at OS boot.
   - Signs one capsule per platform via
     `python3 -m qcom_capsule_tool.cli create ... -T <TARGET> -guid <FMP_GUID>`:
     - `hamoa` → `IQ-X7181`, GUID `0F6D58FC-2258-4D27-9E23-D77219B0897C`
     - `purwa` → `IQ-X5121`, GUID `185a798b-13b2-4595-bd08-e2770a4bb190`

3. **New package `dtb-capsule-PKGVER-ABINUM-qcom`** (`Architecture: arm64`,
   `Depends: linux-modules-PKGVER-ABINUM-qcom`, `Recommends: grub2-common`)
   ships:
   - `/usr/share/dtb-capsule/<machine>/<machine>-dtb.cap` + `capsule.env`
   - `/usr/share/dtb-capsule/dtb-provenance-content-sha256sums.txt`,
     `expected-kver`, `expected-dtb-sha256`
   - `/usr/share/dtb-capsule/verify-capsule-result.sh`
   - `/usr/sbin/dtb-capsule-recovery`
   - `/etc/update-motd.d/85-dtb-capsule`
   - `/lib/systemd/system/dtb-capsule-verify.service`

4. **Provenance chain** — sha256 over the manifest of all DTB hashes is:
   - injected as a `/qcom-dtb-capsule-provenance` DT node (via `fdtput`) into the
     **capsule** DTBs only, so a running system exposes it at
     `/sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256`;
   - written as `/usr/lib/modules/<kver>/dtb-provenance-sha256` into
     `linux-modules`.

   This lets the device prove at runtime that the kernel it is running and the
   DTB firmware actually applied came from the same build.

5. **postinst (`configure`)** — clears a stuck
   `IsCapsulePendingInPersistedMedia`, refuses to proceed if
   `linux-modules-<kver>` is absent or its provenance sha differs, selects the
   platform by matching packaged `FMP_GUID` against ESRT `fw_class`, copies the
   `.cap` to `/boot/efi/EFI/UpdateCapsule/qcom-dtb-<kver>.cap`, and sets
   `OsIndications` bit 2 with `efivar -w` (with NVRAM read-back verification).
   No reboot is forced.

6. **Next boot** — `dtb-capsule-verify.service` runs
   `verify-capsule-result.sh`, which writes
   `/var/lib/dtb-capsule/last-verify-state` with
   `kver_match_state` / `dtb_pairing_state` / `rollback_target_*` /
   `guid_conflict` / `summary`. States include `apply_confirmed`,
   `apply_failed`, `apply_failed_with_rollback_available`,
   `suspected_dtb_rollback`, `content_mismatch_localized`, `reboot_pending`,
   `reboot_stalled`. On several failure states it invokes
   `dtb-capsule-recovery --auto` to repoint the GRUB default at a kernel whose
   DTB provenance matches what is actually running. The MOTD hook prints
   nothing when healthy.

7. **prerm (`remove`)** deletes this package's own staged-but-unconsumed
   `.cap` from the ESP (it lives outside dpkg's file list).

---

## 2. Assessment

The **design is sound and unusually well-instrumented** — dedup caching of ESRT
results, `boot_id`-based reboot-stall detection (immune to clock skew),
per-file mismatch localisation, direction-agnostic kver comparison, and careful
POSIX-sh portability (busybox-safe `od` parsing, octal `printf` for dash).

The problems are in packaging/CI plumbing, not in the concept.

### Blocking

1. **`secrets: inherit` added to `premerge-pr.yml`.**
   The file's own header comment explicitly states it uses `pull_request`
   (not `pull_request_target`) *specifically so the run gets no secrets*, and it
   builds fork PR merge refs on a credentialed self-hosted runner. This change
   hands every repo secret — including the capsule signing key added by #112 —
   to untrusted contributor code. Should be reverted or moved behind a trusted
   trigger with a scoped secret.

2. **Signing certs are not in the tree.**
   `dtb_capsule_cert_{leaf,root,sub}` default to
   `debian.qcom/certs/QcFMP{Cert,Root.pub,Sub.pub}.pem`, and no `debian.qcom/certs/`
   exists. `capsule_creator.py:97-116` writes those paths into `config.json` and
   `generate_capsule.py:103-162` signs with `openssl smime -signer ... -certfile ...`.
   Any build outside the CI that injects them (developer, Launchpad) cannot
   produce a capsule, with no graceful degradation.

3. **Silent build failure.**
   The entire ~90-line capsule step in `debian/rules.d/2-binary-arch.mk` is a
   single `;`-joined make recipe line, and no `SHELL`/`.SHELLFLAGS` override sets
   `-e`. The recipe's exit status is that of the final
   `cp .../dtb-capsule.prerm.in debian/<pkg>.prerm`. Missing certs, a
   capsule-tool traceback, or an `mkimage` failure therefore produce a
   `dtb-capsule` package **containing no `.cap` at all**, which installs cleanly
   and logs `no .cap found, skipping`. The `binary-%` block has the same shape.

4. **`efivar` is not a dependency.**
   Without it, postinst logs a warning and `return 0` — the capsule is staged on
   the ESP but `OsIndications` is never set, so firmware never processes it. The
   feature silently no-ops. Should be `Depends: efivar`.

### Important

5. **Unversioned inter-package dependency.**
   `Depends: linux-modules-PKGVER-ABINUM-qcom` has no `(= ${binary:Version})`.
   Since `abi_release` doesn't encode the upload version, a capsule from build N
   can pair with modules from build M. The mismatch is instead caught by
   `exit 1` in postinst, which fails the dpkg run rather than letting apt's
   resolver pick a consistent set.

6. **Static capsule firmware version.**
   `dtb_capsule_fwver ?= 0.0.2.0` and `dtb_capsule_lfwver ?= 0.0.0.0` are the
   same for every kernel build. The verifier's success condition is
   `last_attempt_status == 0 && fw_version == last_attempt_version`; firmware
   that enforces version monotonicity will reject the *next* kernel's
   same-version capsule with `ErrorIncorrectVersion` (status 3). The
   `lfwver=0.0.0.0` also disables anti-rollback protection entirely. The version
   should be derived from the kernel version/ABI.

7. **Nothing pulls the package in.**
   No meta package or `linux-image-*` dependency references
   `dtb-capsule-*-qcom`, so the feature ships in the archive but is never
   installed by a normal kernel upgrade.

8. **No ESP sanity check.**
   postinst does `mkdir -p /boot/efi/EFI/UpdateCapsule` without verifying
   `/boot/efi` is a mounted vfat ESP. If it isn't mounted, the capsule is
   written to the root filesystem and postinst reports success.

9. **Invasive delta to shared Canonical packaging.**
   ~120 lines of qcom-specific logic — including hard-coded machine names
   (`for machine in hamoa purwa`) and `debian.qcom/...` paths — were added to the
   shared `debian/rules.d/2-binary-arch.mk` and `debian/rules.d/0-common-vars.mk`,
   even though `debian/rules:40` already does
   `-include $(DEBIAN)/rules.d/hooks.mk` for exactly this purpose. This will
   conflict on every rebase against Canonical's kernel. (`do_fitimage` is a
   partial precedent, but much smaller.)

10. **Working notes committed to the repo root.** ✅ **FIXED in `9d7b0cc312d4`**
    (see section 4). Was: `FLOWCHART-UPDATES-v3.md`,
    `IMPLEMENTATION-SUMMARY-P1-P4.md` (both Chinese-language AI implementation
    notes, with stale line numbers) and `dtb-capsule-flowcharts-v3-en.html`
    (80 KB, 1308 lines) in the repo root, landing in the source package and the
    `linux-source` tarball. Now consolidated under `debian.qcom/docs/` and not
    referenced by any install rule.

### Minor

11. `verify-capsule-result.sh` is installed `0755` under `/usr/share` and
    executed by systemd — FHS says `/usr/share` is non-executable
    architecture-independent data; `/usr/libexec` or `/usr/lib/dtb-capsule` is
    the right home. Likely lintian warning.
12. `/qcom-dtb-capsule-provenance` is a non-upstream DT root node with no
    documented binding; it will trip `make dtbs_check`.
13. `Provides`/`Conflicts`/`Replaces: dtb-capsule-qcom` means only one kernel's
    capsule package can be installed at a time. Deliberate, but at odds with
    Ubuntu's multi-kernel-installed model and interacts oddly with
    `apt autoremove`.
14. GUID case is inconsistent between `hamoa/capsule.env` (uppercase) and
    `purwa/capsule.env` (lowercase). Harmless — both sides lowercase before
    comparison at runtime — but sloppy.
15. `dtb-capsule-verify.service` has both `After=multi-user.target` and
    `WantedBy=multi-user.target`; works, but unusual ordering.
16. Package name isn't `linux-`-prefixed, unlike every other binary package from
    this source.

### Verified correct (things that looked risky but aren't)

- **`--update-partitions dtb` still resolves after the `dtb`→`dtb_a`/`dtb_b`
  rename.** `partitions.conf:88-89` defines `dtb_a`/`dtb_b`;
  `find_base_names()` (`UpdateFvXml.py:145-149`) strips the `_a`/`_b` suffix to
  base name `dtb`; `create_xml()` (`UpdateFvXml.py:189-195`) marks it `UPDATE`.
  Generated XML targets `dtb_a` with backup `dtb_b`.
- **`config.json` need not pre-exist** — `UpdateJsonParameters.py:231-240`
  creates it when absent.
- **Build-deps are complete for this path**: new `mtools [arm64]`, plus existing
  `u-boot-tools`, `device-tree-compiler`, `openssl`, `python3`.
  `libfdt`/`pyelftools` are only needed by the unused `patch-capsule-cert`
  subcommand.
- **The provenance manifest is computed *before* the `fdtput` injection**, so it
  hashes the pristine DTBs that are shipped in `linux-modules`. The
  `content_mismatch_localized` per-file diff against
  `/usr/lib/firmware/<kver>/device-tree/qcom` is therefore self-consistent.
- **Upgrade ordering is safe.** With `Conflicts`+`Replaces`, dpkg runs the old
  package's `prerm remove` before unpacking the new one; `prerm` only deletes
  its own `qcom-dtb-<oldkver>.cap`, so the new capsule cannot be clobbered.
- **`dh_systemd_enable`/`dh_systemd_start` are valid** at
  `debhelper-compat (= 10)`, matching the existing `hv-*-daemon` convention, and
  the unit is installed into the package dir during `stamp-install` before
  `dh_all_inline` runs in `binary-%`.
- **`pkgdir` is the `linux-modules` package dir**
  (`2-binary-arch.mk:119`), so the `dtb-provenance-sha256` marker and the
  device-tree dir both land in `linux-modules-<kver>-qcom`, consistent with the
  declared `Depends` and with the runtime scripts' paths.
- **`build-dtb-image.sh` fails loudly** — `set -euo pipefail`, `require_cmd` for
  `mformat`/`mcopy`/`mkimage`, and `--soc`/`--board` validated against
  `qcom-metadata.dts` subnodes. Its `.its`/`.dts` inputs are vendored. (Its
  failure is nevertheless swallowed by finding #3.)

---

## 3. Suggested minimum before merge

1. Revert `secrets: inherit` (or scope it to a trusted, non-fork trigger).
2. Wrap the capsule build in a `set -e` sub-shell, or fail explicitly when the
   certs / `.cap` outputs are missing.
3. Add `Depends: efivar` and `(= ${binary:Version})` on `linux-modules`.
4. Derive `dtb_capsule_fwver` from the kernel version instead of hard-coding it.
5. Move the qcom-specific rules into `debian.qcom/rules.d/hooks.mk`.
6. Remove the three root-level design-notes/HTML files from the tree.
7. Decide how the package gets installed (meta-package dependency).

---

## 4. Re-check after rebase (2026-09-25, head `9d7b0cc312d4`)

The PR was rebased and updated on 2026-09-22. It is now 100+ commits,
847 files, +166384/-10049 — but nearly all of that is unrelated kernel churn
pulled in by the rebase onto a newer `resolute-qcom-devel` (shikra/kaanapali
DTS, media/iris reverts, pinctrl). The six original capsule commits are intact
and unmodified.

`git diff fe76d4244e37 9d7b0cc312d4 -- debian.qcom/ debian/rules.d/ debian/rules .github/`
returns exactly **three files**:

| File | Change |
|---|---|
| `debian.qcom/docs/DTB-CAPSULE-IMPLEMENTATION.md` | new, 621 lines |
| `debian.qcom/docs/dtb-capsule-flowcharts-en.html` | new, 1186 lines |
| `debian.qcom/dtb-capsule-runtime/verify-capsule-result.sh` | 11 lines changed |

Everything else in the packaging is byte-identical to the reviewed state.

### Fixed

- **Finding #10 (root-level working notes).** New commit
  `9d7b0cc312d4 "qcom: consolidate dtb-capsule design docs under
  debian.qcom/docs"` removes the three root-level files and replaces them with
  two consolidated documents under `debian.qcom/docs/`. Verified that nothing
  in `debian/` references that directory, so they are not installed into any
  binary package.

- **A bug not caught in the original review.** The suspected-rollback branch in
  `verify-capsule-result.sh` was exiting unconditionally and shadowing the
  `apply_failed_with_rollback_available` case when firmware reported *failure*:

  ```diff
  -if [ -n "$ROLLBACK_TARGET_KVER" ]; then
  +if [ "$ESRT_CONFIRMED" -eq 1 ] && [ -n "$ROLLBACK_TARGET_KVER" ]; then
  ```

  Correct fix, clearly commented. Credit to the author.

### Still open — all blocking findings survived the rebase

| Finding | Status at `9d7b0cc312d4` |
|---|---|
| #1 `secrets: inherit` | still present at `premerge-pr.yml:42` |
| #2 certs absent | `debian.qcom/certs/` still does not exist; build still fails |
| #3 no `set -e` in the recipe | unchanged |
| #4 no `efivar` dependency | unchanged — `Depends: ${misc:Depends}, linux-modules-PKGVER-ABINUM-qcom` |
| #5 unversioned `linux-modules` dep | unchanged |
| #9 qcom logic in shared packaging | unchanged — 61 `capsule` references still in `debian/rules.d/2-binary-arch.mk`; `debian.qcom/rules.d/hooks.mk` still does not exist |
| non-reproducible build (`uuid4` FFS GUID) | unchanged |

The signing blocker is entirely unaffected.

**Note on the security item:** the rebase makes finding #1 *more* exposed, not
less. The branch now tracks current devel, so it is more likely to be built by
CI with `secrets: inherit` in place — and that inherited secret is a genuine
Qualcomm capsule-signing private key.
