# Porting the DTB capsule build to Launchpad

Plan for re-implementing PR #111 under Launchpad build constraints.
Written 2026-09-23. Supersedes the packaging approach in `pr111-review.md`;
the findings in `capsule-signing-analysis.md` and `fit-image-comparison.md`
still apply unchanged.

## 1. Constraints

| Constraint | Consequence |
|---|---|
| No network on builders except apt / archive host | Nothing may be cloned or fetched at build time |
| No secret material available to a Launchpad build | The PR's CI cert-injection cannot be reproduced; signing must go through Launchpad's signing service or happen out of band |
| Kernel source package only compiles the kernel | It does not produce the signing artifact |

### 1.1 Where PR #111 gets its signing keys

Worth stating explicitly, because it is easy to assume otherwise: **the PR does
not generate a throwaway or self-signed CA.** There is no cert generation
anywhere in the tree — no `openssl req -x509`, `genrsa`, `genpkey` or
equivalent in `debian/`, `debian.qcom/`, `.github/` or the vendored
`qcom_capsule_tool/`. The only `.x509` match in the tree is the unrelated
kernel module `signing_key.x509`.

The certificates are real and arrive from GitHub Secrets, injected by
**PR #112** into `debian.qcom/certs/`:

```yaml
- name: Inject DTB capsule certificates
  env:
    FMPCERT: ${{ secrets.FMPCERT }}
    FMPROOT: ${{ secrets.FMPROOT }}
    FMPSUB:  ${{ secrets.FMPSUB }}
  run: |
    set +x
    echo "$FMPCERT" | base64 -d > kernel-src/debian.qcom/certs/QcFMPCert.pem
    echo "$FMPROOT" | base64 -d > kernel-src/debian.qcom/certs/QcFMPRoot.pub.pem
    echo "$FMPSUB"  | base64 -d > kernel-src/debian.qcom/certs/QcFMPSub.pub.pem
    chmod 600 kernel-src/debian.qcom/certs/*.pem
```

consumed via `debian/rules.d/0-common-vars.mk:113-115`
(`dtb_capsule_cert_leaf` / `_root` / `_sub`).

**`QcFMPCert.pem` contains a private key.** The openssl invocation in
`sign_payload_openssl()` passes `-signer` with **no `-inkey`**, so openssl reads
the private key from that same file; the config field is literally named
`OpenSslSignerPrivateCertFile`.

Two consequences:

1. **The firmware is enforcing, and the signing cannot be faked.** The chain
   terminates at `QcCapsuleRootCert` fused into the EVK. Without the genuine
   `FMPCERT` private key, no producible signature will validate. There is no
   "it is not checked yet, so reproduce the fake signing" shortcut — see
   [`capsule-signing-format.md`](capsule-signing-format.md) section 4.
2. **This escalates blocking finding #1 in `pr111-review.md`.**
   `premerge-pr.yml` sets `secrets: inherit` on a `pull_request` trigger that
   builds the PR *merge ref* on a *self-hosted* runner. Any fork PR can
   therefore run arbitrary code in a job holding a genuine Qualcomm firmware
   capsule-signing private key. `set +x` prevents accidental log echo but is no
   defence against deliberate exfiltration. This should be treated as the most
   urgent item in the PR, ahead of every functional issue, and raised with the
   owner of those secrets independently of whether the PR proceeds.

Target topology, matching the existing `linux` → `linux-generate` → `linux-signed` model:

```
linux-qcom                  compiles the kernel; ships DTBs + provenance markers
        │
        ▼  (apt-get download / installed build-dep)
linux-generate-qcom         builds the capsule payload, emits the raw-signing tarball
        │
        ▼  (Launchpad signs, publishes under dists/.../signed/)
linux-signed-qcom           downloads the signature, assembles the final .cap + runtime pkg
```

Only the first two stages are in scope here.

---

## 2. How the existing `-signed-`/`-generate-` machinery works

Reference implementation: `~/qualcomm/resolute-signed` (`linux-signed 7.0.0-38.38`).

The `-generate-` package is **not** a binary subpackage — it is a whole source
package synthesised from a skeleton inside the `-signed-` source tree.

```
debian/package.config              sign <arch> <sig_type> <binary> <flavour...> [--opts]
        │
        ├─ debian/scripts/config.py            Signing.load() parses it
        │
        ▼
debian/scripts/parameterise-ancillaries $(abi) $(generate_src)
        │   copies debian/ancillary/linux-generate/ into a full source tree
        │   renames the source stanza -> debian/control.common
        │   truncates debian/changelog to 5 stanzas, renamed to the -generate- name
        │   emits debian/files.json:
        │     {"file": "/boot/<binary>-<abi>-<flavour>",
        │      "sig_type": "efi", "arch": "arm64"}
        ▼
linux-generate  (separate source upload)
        │   debian/rules -> debian/scripts/gen-rules -> debian/rules.gen
        │
        │   copy_or_download macro:
        │       if the path is readable on the builder, cp it;
        │       else  dpkg -S <path> -> apt-get download <pkg>
        │             -> dpkg-deb -x -> cp from the extracted tree
        │
        │   custom-upload target:
        │       debian/custom/$(version)/<absolute-path>.<sig_type>
        │       debian/custom/$(version)/control/options     <- literal "tarball"
        │       tar czf ../../../$(source)_$(version)_$(arch).tar.gz .
        │       dpkg-distaddfile $(custom_tar) raw-signing -
        ▼
Launchpad SigningUpload
        │   signs by file extension, republishes to
        │   dists/<archive>/main/signed/<src>-<arch>/<version>/
        │   as SHA256SUMS + signed.tar.gz
        ▼
linux-signed
            ./download-signed "$(generate_src)" "$(ver)" "$(generate_src)"
            ./debian/scripts/signed-build  / signed-install
```

Notes worth remembering:

- `download-signed` locates the archive by `apt.Cache()` and rewrites the
  package's pool URI into a `dists/` URI, so it reuses whatever host apt is
  already configured for — that is why it works on builders, and why it also
  works against a private PPA (it carries over `user:password@` from the URI).
- `gen-rules` also gunzips arm64 `efi` payloads and records `GZIP=1` in a
  `.vars` file so `-signed-` can recompress. Precedent for passing build-time
  metadata alongside a signable file.
- `arm64` already appears in `package.config` with `sign arm64 efi ...`, so the
  arm64 path through this machinery is exercised.

---

## 3. Blocker to resolve before writing code

Launchpad cannot currently produce a signature this firmware will accept.

> **Full detail in [`capsule-signing-format.md`](capsule-signing-format.md)** —
> the exact signed blob, why the certificate chain must be embedded, the
> `sign-file.c` flags, and the trust-anchor problem. Summary follows.

From `lib/lp/archivepublisher/signing.py` (`signing.py:291-304` for the
extension map, `:587-792` for the implementations):

| Ext | Mode | Command | Output |
|---|---|---|---|
| `.efi` | UEFI | `sbsign --key --cert <img>` | `.signed`, attached PE signature |
| `.ko` | KMOD | `kmodsign -D sha512 <pem> <x509> <img> <img>.sig` | `.sig` |
| `.opal` | OPAL | `kmodsign -D sha512 ...` | `.sig` |
| `.sipl` | SIPL | `kmodsign -D sha512 ...` | `.sig` |
| `.fit` | FIT | `mkimage -F -k <keydir> -r <img>` | `.signed`, in place |
| `.cv2-kernel` | CV2_KERNEL | signing service | `.sig` |
| `.android-kernel` | ANDROID_KERNEL | signing service | `.sig` |

(The signing-service path in `signUsingLocalKey` uses suffix `.signed` for
UEFI/FIT and `.sig` for everything else.)

`kmodsign -D` is the closest fit but is **not** a drop-in:

1. `-D` emits a *full detached signature block* — PKCS#7 plus the
   `struct module_signature` trailer and the `~Module signature appended~`
   magic (`scripts/sign-file.c:55`, `:358-360`). A capsule needs the bare
   PKCS#7. (`-d` emits a bare `.p7s`, but Launchpad never invokes it.)
2. `sign-file`'s CMS uses `CMS_NOCERTS | CMS_NOATTR`
   (`scripts/sign-file.c:275-283`): **no embedded certificate chain**, and the
   signature covers raw content rather than signedAttrs. The firmware has only
   `QcCapsuleRootCert` and no certificate store, so it can only build the
   leaf -> sub -> root chain from certificates carried *inside* the PKCS#7 —
   which is exactly why the Qualcomm flow passes `-certfile QcFMPSub.pub.pem`.
   This one is fatal on its own.
3. Hash is hardcoded `sha512`; the Qualcomm capsule uses `sha256`.

Independently of format: the device's trust anchor is `QcCapsuleRootCert`,
provisioned into the UEFI firmware images (this is what the currently-unused
`patch_capsule_cert.py` exists to patch). A Launchpad-held key will not chain
to it unless the shipped firmware is re-provisioned with a Canonical root.
Note that `OpenSslTrustedPublicCertFile` is *required* by
`generate_capsule.py:116-123` but never passed to openssl — it denotes the root
that must already be in firmware. **A new signing mode alone is therefore not
sufficient.**

**Decide this first.** The outcome determines the `sig_type` string, the
tarball member extension, and whether a new Launchpad signing mode must be
requested. Options, roughly in increasing order of effort:

- (a) Add a Launchpad signing mode that runs
  `openssl smime -sign -binary -outform DER -md sha256 -signer <leaf> -certfile <sub>`
  over an opaque blob, plus provision a Canonical-rooted FMP cert into the
  firmware. `cv2-kernel` and `android-kernel` are precedent for vendor-specific
  modes being added.
- (b) Have Qualcomm sign out of band and treat the capsule as a prebuilt
  binary blob shipped in a package (no Launchpad signing at all).
- (c) Ship unsigned capsules for development only — `generate_capsule.py`
  already supports this and prints
  `WARNING: no OpenSSL certificates given, unsigned capsule payload`.

---

## 4. Where to build the capsule: in `-generate-`

**Recommendation: build the capsule payload in `-generate-`, not in the kernel
source package.**

> **This is the end-state design.** `ubuntu-qcom-kernel` has no `-generate-` or
> `-signed-` package yet, so this cannot be implemented today. For the interim
> in-kernel approach — which is viable and has been empirically verified — see
> **section 8**. Sections 4-6 remain the target to migrate to.

### Why it is feasible

- The pipeline has a clean key-free / key-dependent boundary.
  `capsule_creator.py` runs five steps and only the last touches a key:

  | Step | Module | Output | Needs key? |
  |---|---|---|---|
  | 1 | `SYSFW_VERSION_program -Gen` | `SYSFW_VERSION.bin` | no |
  | 2 | `UpdateFvXml` | `FvUpdate.xml` | no |
  | 3 | `FVCreation` | **`firmware.fv`** | no |
  | 4 | `UpdateJsonParameters` | `config.json` (cert *paths* only) | no |
  | 5 | `generate_capsule` | `*.cap` | **yes** |

  Step 3 runs before step 4 writes any cert path, so `firmware.fv` is provably
  cert-independent. It is the natural handoff artifact.

- Nothing in steps 1-4 needs the network or an exotic toolchain:
  - `FVCreation` is pure Python (`ctypes`/`struct`) — **no EDK2 BaseTools**.
  - The only `git clone` (`UpdateFvXml.py:41`, qcom-ptool) is unreachable:
    `UpdateFvXml.py:292` is `if not args.ptool_path: safe_clone(repo_dir)` and
    `--ptool-path` is always passed at the vendored dir. `git` is not even a
    build-dep.
  - `libfdt` / `pyelftools` are imported lazily inside `patch_capsule_images()`
    and only reached via `--patch-image`, which is unused.
  - Remaining tools are all in the archive: `mtools`, `u-boot-tools`,
    `device-tree-compiler`, `python3`.

- `-generate-` can reach the DTBs. `linux-modules-<abi>-qcom` ships
  `/usr/lib/firmware/<kver>/device-tree/qcom/*.dtb`, and `copy_or_download`
  already demonstrates the `dpkg -S` -> `apt-get download` -> `dpkg-deb -x`
  pattern for pulling them in.

### Why not the kernel package

- Keeps ~6k lines of vendored Qualcomm Python and the
  `mtools`/`u-boot-tools` build-deps out of the kernel source package.
- Avoids publishing a multi-MB "carrier" binary package whose only purpose is
  to hand `firmware.fv` to the next stage, and which no end user should install.
- `-generate-` is precisely the stage designed to derive signing artifacts from
  an installed kernel package; its own binary output is a throwaway
  "build interlock package".
- Avoids further growth of the qcom delta against shared Canonical packaging,
  which was already finding #9 in `pr111-review.md`.

---

## 5. Work items

### 5.1 Kernel package (`ubuntu-qcom-kernel`) — minimal delta

Keep `do_fitimage` / `qcom.itb` exactly as-is. Add only the provenance markers,
so a single source of truth exists for the value:

- Compute `dtb-provenance-content-sha256sums.txt` over the installed
  `/usr/lib/firmware/<kver>/device-tree/qcom/*.dtb{,o}`
  (`sha256sum ... | sort -k2,2`).
- Roll it up to one sha256 and ship both in `linux-modules-<abi>-qcom`:
  - `/usr/lib/modules/<kver>/dtb-provenance-sha256`
  - the manifest alongside it.

Roughly 15 lines. No vendored code, no new build-deps, no capsule package.
Put it in `debian.qcom/rules.d/hooks.mk`, **not** in the shared
`debian/rules.d/2-binary-arch.mk`.

Do **not** recompute the value independently in `-generate-` — let it read
these files. If the two source packages each implement the hashing, they will
drift and every device will silently report `kernel_dtb_mismatch`.

### 5.2 `-generate-` package

Carry over from the PR, unchanged:

- `debian.qcom/fitimage/build-dtb-image.sh`, `qcom-metadata.dts`,
  `qcom-next-fitimage.its`
- `debian.qcom/scripts/qcom_capsule_tool/` (can drop `patch_capsule_cert.py`
  and `BinToHex.py` unless the firmware-provisioning path is wanted)
- `debian.qcom/qcom-ptool/platforms/iq-x7181-evk/spinor/partitions.conf`
- `debian.qcom/dtb-capsule-runtime/config/{hamoa,purwa}/capsule.env`

Build steps, per platform (`hamoa` -> `IQ-X7181`, `purwa` -> `IQ-X5121`; note
both map to the same ptool dir `iq-x7181-evk`, and `NORUFS` maps to the
`spinor` subdir — `UpdateFvXml.py:298-304`):

1. Obtain the DTBs and the provenance marker from `linux-modules-<abi>-qcom`.
2. `fdtput` the provenance node into the capsule copies of the `.dtb` files.
3. `build-dtb-image.sh --dtb-src ... --soc hamoa purwa --size 4 --out dtb.bin --prune`
4. `qcom_capsule_tool.cli` steps 1-4 to produce `firmware.fv`.
5. Emit the signable blob and the metadata needed to rebuild the capsule.

Then extend the ancillary machinery — three concrete limitations:

1. **`parameterise-ancillaries` hardcodes the path.** It emits
   `f"/boot/{binary}-{abi_version}-{flavour}"`. A capsule payload is neither in
   `/boot` nor named per-flavour. Either extend `files.json` generation to take
   a path template, or hand-maintain `files.json` in the ancillary skeleton.
2. **`gen-rules` only knows how to copy.** `copy_or_download` fetches exactly
   one file; a capsule needs a *build* step. Add a new `files.json` entry type
   that emits additional `generate-$(arch)::` recipes, rather than overloading
   the copy macro.
3. **`package.config` grammar** — `sign <arch> <sig_type> <binary> <flavour...>`
   needs a new `sig_type` matching whichever Launchpad mode section 3 settles on.

Use the `GZIP=1` / `.vars` precedent in `gen-rules` to carry the per-platform
metadata (`FMP_GUID`, `TARGET`, `fwver`, `lsv`, `monotonic_count`, expected
kver, provenance sha) alongside the signable blob, so `-signed-` can reassemble
without re-deriving anything.

### 5.3 `-signed-` package (later, out of scope)

- `download-signed` as-is.
- Add a `--signature <der>` mode to `generate_capsule.py` that bypasses
  `sign_payload_openssl()` and splices in the returned DER. The signable blob is
  fully specified (`generate_capsule.py:174-197`):

  ```
  image   = b"MSS1" + pack("<3I", 16, fw_version, lowest_supported_version) + firmware.fv
  to_sign = image + pack("<Q", monotonic_count)
  cert    = PKCS#7(detached, DER, sha256) over to_sign
  auth    = pack("<Q", monotonic_count)
          + pack("<IHH", 24 + len(cert), 0x0200, 0x0EF1)
          + EFI_CERT_TYPE_PKCS7_GUID.bytes_le + cert
  payload = auth + image
  ```

  Everything outside the signature is struct assembly, so this side is
  mechanical. Note the outer `EFI_CAPSULE_HEADER` lengths depend on
  `len(cert)`, so the outer headers must be assembled here, not pre-baked.
- Ship the runtime pieces from the PR (`verify-capsule-result.sh`, the systemd
  unit, `dtb-capsule-recovery`, the MOTD hook, `postinst`/`prerm`) in this
  package, since the `.cap` they stage only exists at this stage.
- Carry over the fixes from `pr111-review.md`: `Depends: efivar`, versioned
  `linux-modules` dependency, kernel-derived `fwver` instead of the constant
  `0.0.2.0`, ESP mount check, `set -e` around the build, and relocate
  `verify-capsule-result.sh` out of `/usr/share`.

---

## 6. Things to drop entirely

- `.github/workflows/premerge-pr.yml` `secrets: inherit` — GitHub-specific and
  a security regression (blocking finding #1 in `pr111-review.md`).
- `debian.qcom/certs/QcFMP*.pem` defaults in `0-common-vars.mk` and the whole
  CI cert-injection approach from #112.
- `FLOWCHART-UPDATES-v3.md`, `IMPLEMENTATION-SUMMARY-P1-P4.md`,
  `dtb-capsule-flowcharts-v3-en.html` from the repo root.

## 7. Open questions

1. Which Launchpad signing mode, and does the firmware need re-provisioning
   with a Canonical-rooted FMP cert? (Section 3 — blocks everything else.)
2. Is `-generate-` per-flavour or once per ABI? The PR builds the capsule only
   for `$(firstword $(flavours))`; `package.config` is flavour-indexed.
3. Does `monotonic_count` need to increment across builds? `create_config()`
   defaults it to `0x2` and it is constant across builds; with
   `lfwver=0.0.0.0` there is no rollback protection at all.
4. Where does `fwver` come from once it is no longer the constant `0.0.2.0` —
   derived from the ABI number, or tracked separately?

---

## 8. Interim: build the capsule inside the kernel package

`ubuntu-qcom-kernel` currently has **no** `-generate-` and **no** `-signed-`
source package, and no signing-tarball plumbing at all (no `dpkg-distaddfile`,
no `raw-signing`, no tarball creation; `uefi_signed`/`opal_signed`/`sipl_signed`
exist at `debian/rules:76` but are never set true). Waiting for that machinery
would block all capsule work indefinitely.

**Interim decision: build an unsigned capsule in the kernel source package,
structured so that extraction later is a file move rather than a rewrite.**

### 8.1 Unsigned generation works — verified

Run end-to-end offline, no certs, no network, from a clean tree:

```sh
PYTHONPATH=<repo>/debian.qcom/scripts python3 -m qcom_capsule_tool.cli create \
    -fwver 0.0.2.0 -lfwver 0.0.0.0 \
    -S NORUFS -T IQ-X7181 \
    --ptool-path <repo>/debian.qcom/qcom-ptool \
    --update-partitions dtb \
    -config config.json \
    -p "" -x "" -oc "" \
    -guid 0F6D58FC-2258-4D27-9E23-D77219B0897C \
    -capsule hamoa-dtb.cap -images Images
```

Result: exit 0,
`WARNING: no OpenSSL certificates given, unsigned capsule payload`,
`firmware.fv` (66944 B) and `hamoa-dtb.cap` (67056 B) from a 64 KiB dummy
payload — a 112-byte header delta.

Why it works:

- `create_config()` (`UpdateJsonParameters.py:76-90`) already defaults the
  three cert fields to `""`. Unsigned is the tool's built-in path, not a hack.
- `-p`/`-x`/`-oc` are `required=True` in argparse, so they must be *present*;
  passing `""` satisfies that, and `to_path()` maps `""` -> `None`, making
  `sign = any(certs)` false in `PayloadDescriptor` (`generate_capsule.py:100-123`).

Capsule header verified: starts with `edd5cb6d-2de8-444c-bda1-7194199ad92a`
(`EFI_FIRMWARE_MANAGEMENT_CAPSULE_ID_GUID` in `bytes_le`), `HeaderSize=0x20`,
`Flags=0x00010000` (PersistAcrossReset), `CapsuleImageSize=0x105f0`.

### 8.2 Structure for extractability

Three rules, in priority order.

**(1) Everything goes in `debian.qcom/rules.d/hooks.mk`.**
The file does not exist yet, but `debian/rules:40` already has
`-include $(DEBIAN)/rules.d/hooks.mk`, so creating it is sufficient. Make
**zero** edits to `debian/rules.d/2-binary-arch.mk` or any other shared
makefile. PR #111 put ~120 lines into the shared file (`:212-304` and
`:709-726`); that was finding #9 in `pr111-review.md` and is the single
decision that determines whether extraction is cheap or painful.

**(2) One standalone entry-point script with explicit arguments.**

```
debian.qcom/dtb-capsule/build-dtb-capsule.sh \
    --dtb-dir <dir> --provenance-sha <hex> \
    --platform hamoa --outdir <dir>
```

No `$(pkgdir)`, `$(builddir)`, `$(abi_release)` or debhelper assumptions
*inside* the script — `hooks.mk` is a thin caller that supplies paths. A future
`-generate-` `rules.gen` then invokes the identical script with different paths
and needs no knowledge of how the capsule is built.

Must begin with `set -euo pipefail`. The PR's recipe is ~90 lines `;`-joined
with no error handling, so any failure silently ships a package containing no
`.cap` (blocking finding #3).

**(3) Keep the three concerns in separate directories**, because they have
different eventual destinations:

| Concern | Interim home | Eventually |
|---|---|---|
| provenance manifest + rolled-up sha | kernel | **stays in kernel** (section 5.1) |
| FIT + FV + capsule payload build | kernel | -> `-generate-` (section 5.2) |
| verify / recovery / systemd / motd / postinst | kernel | -> `-signed-` (section 5.3) |

If these are interleaved in one recipe they will have to be untangled by hand
later; if they are separate scripts under separate directories the split is
`git mv` plus a `files.json` entry.

### 8.3 Emit the handoff artifacts from day one

Even though the interim build produces a finished (unsigned) `.cap`, also emit:

- `firmware.fv` — the cert-independent blob from step 3, i.e. exactly what a
  signing service would consume.
- a metadata sidecar: `FMP_GUID`, `TARGET`, `fwver`, `lsv`, `monotonic_count`,
  expected `kver`, provenance sha.

They cost nothing now and they *are* the future handoff contract. Wiring up
`-generate-` later reduces to "stop running step 5, ship the `.fv` instead".
`gen-rules`' existing `GZIP=1` / `.vars` handling is the precedent for carrying
build-time metadata alongside a signable file.

### 8.4 Fix the non-reproducible build

Two runs with byte-identical input produce **different** output — 34 differing
bytes, including a 16-byte run at offset 0x48 in `firmware.fv`.

Root cause is `XmlFwEntryValidation.py:395`:

```python
uuid_obj = uuid.uuid4()
meta_data_fwentry.FileGuid = (ctypes.c_byte * 16)(*uuid_obj.bytes)
```

A **random FFS file GUID generated per build**. Consequences:

- Fails Ubuntu's reproducible-builds policy.
- Every no-op rebuild yields a different capsule, so no build can be verified
  by hash against another.
- Once signing exists, the kernel package and `-generate-` cannot independently
  reproduce the same `.fv`, which removes any ability to audit what was signed.

The branch immediately above (`:385-392`) uses an explicit `FileGuid` from the
XML when one is present. The fix is therefore to have `UpdateFvXml.py` emit a
stable `FileGuid` — e.g. `uuid5` over the payload name, or a fixed constant per
partition. Worth doing now rather than after signing is wired up.

### 8.5 Caveats to settle before relying on this

- **An unsigned capsule sets `capsule_support = 0`** instead of
  `CAPSULE_SUPPORT_AUTHENTICATION`. Production/fused devices will almost
  certainly reject it. This path is only useful on an unfused development EVK
  whose firmware skips FMP authentication — confirm with the Qualcomm firmware
  team before spending a test cycle, otherwise the symptom is an ESRT
  `LastAttemptStatus` auth error unrelated to the packaging.
  This caveat firmed up considerably once it was established that PR #111
  signs with **genuine** Qualcomm FMP keys from GitHub Secrets (section 1.1),
  not a generated test CA: the firmware demonstrably enforces, so **whether the
  target EVK accepts an unsigned capsule at all is the first thing to test**,
  before any packaging work is committed to.
- The device trust anchor is `QcCapsuleRootCert` burned into UEFI firmware
  (what the unused `patch_capsule_cert.py` provisions). Even once Launchpad
  signing works, an LP key will not chain to it without firmware
  re-provisioning. Section 3 remains the gating blocker for the end state.
- `monotonic_count` defaults to `0x2` and is constant; with `lfwver=0.0.0.0`
  there is no replay or rollback protection.

### 8.6 Fixes from the review to carry into the interim

From `pr111-review.md`, applicable regardless of build location:

- `Depends: efivar` — without it `OsIndications` is never set and the capsule
  is never applied (blocking finding #4).
- Versioned dependency on `linux-modules-<abi>-qcom` so the capsule and the
  DTBs it was derived from cannot be installed out of step.
- Derive `fwver` from the kernel version instead of the constant `0.0.2.0`.
- Check that the ESP is actually mounted before writing the capsule.
- Relocate `verify-capsule-result.sh` out of `/usr/share` (it is an executable,
  not architecture-independent data).
- Do not ship `FLOWCHART-UPDATES-v3.md`, `IMPLEMENTATION-SUMMARY-P1-P4.md` or
  `dtb-capsule-flowcharts-v3-en.html` in the repo root (section 6).

### 8.7 Migration path when `-signed-` arrives

1. Create the `-signed-` source package with a `linux-generate` ancillary,
   modelled on `~/qualcomm/resolute-signed`.
2. `git mv debian.qcom/dtb-capsule/` and `debian.qcom/scripts/qcom_capsule_tool/`
   into it; `hooks.mk` loses the capsule call but keeps provenance (5.1).
3. Extend `parameterise-ancillaries` — it hardcodes
   `f"/boot/{binary}-{abi_version}-{flavour}"` and needs to express a
   `linux-modules` DTB directory instead.
4. Extend `gen-rules` — it only knows how to *copy* one file, not to run a
   build step before uploading it.
5. Add a `sig_type` to `package.config` once section 3 is resolved.
6. Drop step 5 from the interim script; upload `firmware.fv` in the signing
   tarball instead of the `.cap`.

---

## 9. Implemented (2026-09-25)

Sections 3 to 6 above are done. The implementation lives at
`~/qualcomm/linux-signed/`; see its `README.md` for the full description and
the list of what was verified rather than assumed.

Summary of what was built:

| Open item above | Resolution |
|---|---|
| §3 no Launchpad mode understands a capsule | `launchpad_signing/0001-add-capsule-signing-mode.patch` adds `SigningKeyType.CAPSULE`, `.capsule` dispatch and `signCapsule()`; applies cleanly to upstream, verified with `git apply --check` |
| §5.2 `-generate-` must run a build step | `debian/capsule/build-capsule-payload.sh`, driven by an extended `gen-rules` that emits a real build target plus an `unpack_or_use` macro for whole directories |
| §5.3 `-signed-` must reassemble | `signed-build` grew a capsule arm; `signed-install` lays out `dtb-capsule-<abi>-<flavour>` |
| `parameterise-ancillaries` hardcodes `/boot/...` | it now emits a separate `capsules` list in `files.json` carrying the device-tree directory and machine name instead of a file path |
| `package.config` needs a new `sig_type` | new `capsule <arch> <machine> <flavour>` verb, since a capsule has no `/boot` binary |
| `generate_capsule.py` needs a `--signature` mode | added, together with `--emit-signable`; both share the encoders with the original path so they cannot drift |

The key design decision was **what Launchpad is asked to sign**. Not the
capsule and not `firmware.fv`, but `image + pack("<Q", monotonic_count)` —
the exact blob the vendor tool hands to OpenSSL. The monotonic count is
recovered from the trailing eight bytes at assembly time, so the blob is
self-describing and the archive needs no knowledge of capsule layout at all.
That also makes the new signing mode generic: it signs an opaque file and
returns detached CMS with an embedded chain, which is equally what the Ubuntu
Core FIT work needs.

One thing remains unsolved, and it is not a packaging problem:

* **The trust anchor.** Launchpad generates its own keys, so a capsule signed
  with an autogenerated Launchpad key will not chain to `QcCapsuleRootCert`.
  `generateCapsuleKeys()` therefore *refuses* to autogenerate rather than
  producing capsules that build and publish perfectly and are then silently
  rejected by every device. Whether Qualcomm issues Canonical a certificate
  under their root, or provisions a Canonical root into firmware, is a
  business decision.

### Update: lp-signing *can* hold an externally issued certificate

This was previously listed as a second open question — "not determinable from
the published `archivepublisher` code". It is now resolved by reading
`lp-signing` itself, which is public at `https://git.launchpad.net/lp-signing`
(the code is simply not mirrored on GitHub, which is why the earlier pass
missed it).

`Key.inject()` and the `POST /inject` API accept arbitrary key material; the
"freshly autogenerated keys" wording in Launchpad's `injectIntoSigningService`
docstring describes how *that* caller uses the API, not a constraint the
service imposes.

The real concern was different: lp-signing stores exactly **two** blobs per
key, and a capsule signature needs a third thing — the intermediates, which
must be embedded because firmware has no certificate store. That turns out not
to require a schema change. The chain rides in the certificate slot as a PEM
bundle, leaf first, because `_getX509Fingerprint` pipes the slot through
`openssl x509 -noout -fingerprint`, which reads only the first certificate —
verified, a bundle fingerprints identically to its leaf. `sign()` then splits
the bundle rather than passing it as both `-signer` and `-certfile`, which
would embed the leaf twice.

Implemented as `launchpad_signing/0002-lp-signing-capsule-key-type.patch`
(`git apply --check` clean), and tested by executing the patched `sign()`
branch over both real signable blobs, reassembling, and verifying against the
root alone.

What is left is therefore **custody policy, not mechanism**: whether Canonical
will hold a third-party firmware-signing key and under what controls, whether
it is software- or HSM-backed (lp-signing's HSM path is UEFI-only today), and
whether Qualcomm issues a leaf — making every rotation a round-trip — or an
intermediate CA, which would let Canonical mint its own leaves.
