# DTB UEFI capsule update — analysis notes

Review of **qualcomm-linux/ubuntu-qcom-kernel#111**
(`qcom: implement DTB UEFI capsule update with post-boot verification`),
branch `GuanquanTian:dtb-capsule-resolute-devel-tip` → `resolute-qcom-devel`,
7 commits / +8813 lines, diffed against base `e2d8ff83c07f`.
Related: **#112** (injects the capsule signing certs in CI).

Analysis performed 2026-09-10/11, re-checked 2026-09-25, against the working tree at
`~/qualcomm/ubuntu-qcom-kernel` (base kernel also at `~/qualcomm/kernel/`).

## Files

| File | Contents |
|---|---|
| [`status-summary.md`](status-summary.md) | **Start here / shareable.** Short team-facing status: what can be used as-is, what needs adapting for Launchpad, what is missing or blocked, the security item, and proposed next steps. |
| [`pr111-review.md`](pr111-review.md) | Main review: what the PR does (commit table, build → package → postinst staging → next-boot verification → recovery), then findings split into blocking / important / minor / verified-correct, plus a 7-item "minimum before merge" list. |
| [`capsule-signing-analysis.md`](capsule-signing-analysis.md) | What is actually signed: FMP capsule PKCS#7 yes, inner FIT no (no `-k`, no `hash`/`signature` nodes). Consequences for re-verification, non-capsule write paths, provenance-node trust, and downgrade. |
| [`capsule-signing-format.md`](capsule-signing-format.md) | **Why Launchpad cannot sign the capsule.** The exact signed blob and `EFI_FIRMWARE_IMAGE_AUTHENTICATION` layout; why the cert chain must be embedded (firmware has only `QcCapsuleRootCert` and no cert store); the `CMS_NOCERTS`/`CMS_NOATTR` mismatch in `sign-file.c`; and the trust-anchor problem that survives any format fix. |
| [`capsule-binary-layout.md`](capsule-binary-layout.md) | **Byte-level map of a signed `.cap`.** Every header with offsets and field values, the nesting down to the ~40 DTBs, what the signature actually covers (non-contiguous), signed vs unsigned structural differences, and why the outer headers depend on signature length. |
| [`fit-image-comparison.md`](fit-image-comparison.md) | How the new capsule FIT (`dtb.bin` / `qclinux_fit.img`) differs from the existing `qcom.itb`: same `.its`/`.dts` inputs, everything else different — source dir, pruning, `-E -B 8` external data, FAT wrapper, consumer, per-flavour behaviour. |
| [`boot-flow.md`](boot-flow.md) | **Platform background.** Qualcomm boot chain and partition layout from the vendored `partitions.conf`: the SPI-NOR / UFS split, which partition feeds each boot stage, the four different kinds of DTB (only `dtb_a`/`dtb_b` are Linux's), and where capsule processing sits. |
| [`launchpad-port-plan.md`](launchpad-port-plan.md) | **Implementation plan.** How to re-do the PR for Launchpad: the `linux` → `linux-generate` → `linux-signed` split, why the capsule payload should be built in `-generate-`, the signing-mode blocker, the concrete work items per package, and (section 8) the **interim in-kernel unsigned capsule** to use until `-signed-` exists. |

## Headline conclusions

1. **Design is sound**; the failures are in packaging and CI plumbing.
2. **Blocking:** `secrets: inherit` added to a `pull_request` workflow that
   builds fork merge refs on a self-hosted runner — contradicts that file's own
   security header. The inherited secrets include `FMPCERT`, which is a
   **genuine Qualcomm firmware capsule-signing private key** (PR #112 injects
   it; openssl reads the key from the `-signer` file, there is no `-inkey`).
   Any fork PR can run arbitrary code in a job holding that key. Most urgent
   item in the PR, and worth raising with the secret owner regardless of the
   PR's fate.
3. **Blocking:** signing certs are not in the tree and the capsule build is one
   `;`-joined make recipe with no `set -e`, so failures silently ship a package
   containing no `.cap`.
4. **Blocking:** `efivar` is not a dependency, so `OsIndications` is never set
   and the staged capsule is never applied.
5. **Important:** static `fwver 0.0.2.0` / `lfwver 0.0.0.0` risks
   `ErrorIncorrectVersion` on the next update and disables anti-rollback;
   nothing pulls the package in; ~120 lines of qcom-specific logic added to
   shared Canonical packaging despite an existing `hooks.mk` mechanism.
6. Only the outer capsule is signed; the FIT payload carries no signature and
   no hash.

## Launchpad port (2026-09-23)

The PR cannot be used as-is on Launchpad. See `launchpad-port-plan.md`:

- Build the capsule payload in a **`-generate-`** package (synthesised from
  `debian/ancillary/linux-generate/` inside the `-signed-` source), not in the
  kernel package. Steps 1–4 of the capsule pipeline are provably key-free and
  produce `firmware.fv`; only step 5 signs.
- Network is already a non-issue: the sole `git clone` is bypassed by
  `--ptool-path`, and `FVCreation` is pure Python (no EDK2 BaseTools).
- **Blocker:** no existing Launchpad signing mode produces a bare detached
  PKCS#7 with an embedded cert chain — `kmodsign -D` uses `CMS_NOCERTS`, which
  strips exactly the certificates the firmware needs to build a chain to
  `QcCapsuleRootCert`. And even a new signing mode would not be enough, since a
  Canonical key does not chain to a Qualcomm root. See
  `capsule-signing-format.md`. Resolve before writing code.
- Kernel-package delta shrinks to ~15 lines: ship the provenance marker and
  manifest in `linux-modules`, and put it in `hooks.mk`.

## Interim plan: capsule in the kernel package (2026-09-23)

`ubuntu-qcom-kernel` has no `-generate-`/`-signed-` package yet, so the design
above cannot be built today. Section 8 of `launchpad-port-plan.md` covers the
interim: build an **unsigned** capsule in the kernel package, structured so
extraction later is a file move.

- **Verified working.** The vendored tool produces a valid 67056-byte capsule
  offline with no certs — `create_config()` already defaults the cert fields to
  `""`, so unsigned is a built-in path. Pass `-p "" -x "" -oc ""` to satisfy
  argparse; `to_path()` maps `""` → `None`.
- **Structure:** all logic in a new `debian.qcom/rules.d/hooks.mk` (already
  `-include`d at `debian/rules:40`), zero edits to shared makefiles; one
  standalone script taking explicit `--dtb-dir/--platform/--outdir` arguments;
  provenance / payload / runtime kept in separate directories because they have
  three different eventual destinations.
- **Emit `firmware.fv` + a metadata sidecar now** — that is the future signing
  handoff contract, exercised before the split exists.
- **Build is not reproducible:** `XmlFwEntryValidation.py:395` generates a
  random `uuid.uuid4()` FFS file GUID per build. Fix by emitting a stable
  `FileGuid` from `UpdateFvXml.py`.
- **Caveat:** unsigned means `capsule_support = 0` instead of
  `CAPSULE_SUPPORT_AUTHENTICATION`; only usable on unfused dev EVKs. Confirm
  with the Qualcomm firmware team first.

## Correction: the PR signs with real Qualcomm keys (2026-09-25)

An earlier assumption in this set of notes — that PR #111 signed with a
self-generated key — was **wrong**, and the distinction matters.

There is no certificate generation anywhere in the tree. PR #112 injects
genuine Qualcomm FMP certificates from GitHub Secrets (`FMPCERT`, `FMPROOT`,
`FMPSUB`) into `debian.qcom/certs/`, consumed via
`0-common-vars.mk:113-115`. `QcFMPCert.pem` contains a **private key**:
`sign_payload_openssl()` passes `-signer` with no `-inkey`.

Two consequences:

- **The firmware enforces, and the signature cannot be faked.** The chain ends
  at `QcCapsuleRootCert` fused into the EVK. There is no "not checked yet, so
  reproduce the fake signing" shortcut. Testing whether the target board
  accepts an *unsigned* capsule is therefore the first thing to establish
  before building on the section 8 interim.
- **The `secrets: inherit` issue is more serious than first assessed** — it
  exposes a real firmware-signing private key to arbitrary fork-supplied code
  on a self-hosted runner.

Recorded in `launchpad-port-plan.md` section 1.1 and
`capsule-signing-format.md` section 4.1.

## PR re-checked after rebase (2026-09-25)

PR #111 was rebased on 2026-09-22 and is now 100+ commits / 847 files, but the
capsule delta is only **three files**. See `pr111-review.md` section 4.

- **Fixed:** root-level working notes consolidated under `debian.qcom/docs/`
  (finding #10), and a runtime bug in `verify-capsule-result.sh` where the
  suspected-rollback branch shadowed the `apply_failed_with_rollback_available`
  case.
- **Still open:** every blocking finding — `secrets: inherit`, absent certs,
  no `set -e`, no `efivar` dependency, unversioned `linux-modules` dep, qcom
  logic in shared packaging, and the non-reproducible build.
- The signing blocker is unaffected.

Analysis in this directory remains valid; only finding #10 changed state.

## Implementation (2026-09-25)

The port described in `launchpad-port-plan.md` has been implemented at
**`~/qualcomm/linux-signed/`**. That tree contains a working
`linux-signed-qcom` source package with a `linux-generate-qcom` ancillary that
builds the capsule payload without a key, plus two patches adding a `CAPSULE`
signing mode — one to `launchpad` and one to `lp-signing` — both verified to
apply cleanly upstream.

Start with `~/qualcomm/linux-signed/README.md`. Section 9 of
`launchpad-port-plan.md` maps each open item in the plan to what was built.

**Update (2026-09-30).** The second open question — whether lp-signing can
hold an externally issued certificate — is resolved: it can, and the
certificate chain fits the existing two-column schema as a PEM bundle, so no
migration is needed. See the "Update" subsection at the end of
`launchpad-port-plan.md`. Both signing paths, local-key and signing-service,
have now been run end to end and verified the way device firmware verifies.

The trust-anchor question is unchanged and still gates deployment: Launchpad
generates its own keys, so a Launchpad-generated key will not chain to
`QcCapsuleRootCert`. The implementation refuses to autogenerate a capsule key
rather than silently producing capsules no device will accept. If Qualcomm
supplies a key and certificates, the machinery to accept and use them exists
and is tested; what remains is custody policy.

## Related but separate: Ubuntu Core FIT signing

`~/qualcomm/ai_effort/core_fit_signing/carmel-fit-signing.md` analyses the
DTB/FIT signing step in the Carmel Dragonwing Ubuntu Core kernel snap.

Easy to conflate with the capsule work — same vendor, same DTBs, same FIT, and
also a detached CMS signature — but it is a **different workstream**: different
consumer (Secure Boot at boot vs FMP capsule update), different trust anchor
(SB `db` vs fused `QcCapsuleRootCert`), different key owner (Canonical vs
Qualcomm), and a different delivery path (snap vs deb).

Both need a new Launchpad signing mode, but only the capsule is blocked on a
Qualcomm business decision. They should be raised as two requests, not one.
