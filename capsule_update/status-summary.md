# DTB capsule update — status summary for the team

**What:** PR #111 (+ #112) adds UEFI capsule update for the Qualcomm DTB to the
Resolute kernel packaging. This is an assessment of what we can reuse under
Launchpad, what needs rework, and what is genuinely blocked.

Detail behind every point is in the other documents in this directory.

*Current as of PR head `9d7b0cc312d4` (rebased 2026-09-22, re-checked
2026-09-25). The rebase brought in a large amount of unrelated kernel churn but
changed only three capsule files; one minor issue and one runtime bug were
fixed, and every blocking item below is still open.*

---

## Update 2026-10-07 — the Launchpad port is proven on hardware

Our own packaging (`linux-main` / `linux-signed` / `linux-meta`) has now
completed a **clean-room end-to-end run** on a freshly flashed Hamoa EVK:
capsule authenticated by firmware against our embedded root certificate,
applied, and confirmed live in the running kernel's device tree. Full evidence
in `ppa-build-test-report.md` Part 7.

Reproduced independently from a pristine 1013 board (Part 8), and again from
`capsule4` (Part 9) — the first build carrying both the ABI-derived capsule
version and the ESP resolution fix. Part 9 closed the last evidence gap:
ESRT `fw_version` moved `131072` → `132089`, so firmware itself now
distinguishes *this* capsule applying from *some* capsule having applied once.

`capsule5` (1018, Part 10) added the one case the earlier runs could not
cover: an upgrade on a board that had **already** applied a capsule. The
provenance node went present→*changed* (`70208457…` → `c7087de0…`) and
`fw_version` incremented again (`132089` → `132090`), so firmware will
re-apply over capsule-delivered DTB partitions, not merely populate empty
ones. In the same run the ESP guard **fired for real during an `apt`
upgrade** — the label had resolved to the wrong partition again — and
converted what would have been a completely silent non-update into a clear,
actionable error.

This retires the question of whether the mechanism works. It does **not**
retire the blockers below — signing is still a local test key, and items 1, 3
and 4 in "Missing / blocked" are untouched. Read the sections below as
"what still has to change before this ships", not as "what is unproven".

---

## Usable as-is

| Piece | Note |
|---|---|
| `qcom_capsule_tool/` (~5k lines vendored Python) | Works offline. No EDK2 BaseTools, pure `ctypes`/`struct`. |
| `build-dtb-image.sh` | Already has `set -euo pipefail` and `require_cmd` checks. |
| `partitions.conf` + `qcom-ptool` | One vendored copy covers both `hamoa` and `purwa`. |
| Unsigned capsule generation | **Verified working offline**, no certs, no network. Produces a valid 67 KB capsule. |
| Runtime scripts (`verify-capsule-result.sh`, recovery, motd, systemd unit) | Logic is sound; only packaging placement needs changing. |
| Build-dependencies | `mtools`, `u-boot-tools`, `device-tree-compiler`, `python3` — all in the archive. |

The design is sound. The problems are in packaging, CI and signing — not in
the capsule mechanism itself.

---

## Needs adapting for Launchpad

| Item | Why |
|---|---|
| Move ~120 lines out of `debian/rules.d/2-binary-arch.mk` into `debian.qcom/rules.d/hooks.mk` | PR edits shared Canonical packaging. `hooks.mk` is already `-include`d at `debian/rules:40`. Single most important structural change — decides whether later extraction is a file move or a rewrite. |
| Wrap the capsule build in a standalone script with explicit arguments | PR has a ~90-line `;`-joined make recipe with no `set -e`; failures silently ship a package containing **no `.cap`**. |
| Split the three concerns into separate directories | Provenance stays in the kernel; payload build moves to `-generate-`; runtime package moves to `-signed-`. Interleaved today. |
| Add `Depends: efivar` | Without it `OsIndications` is never set and the staged capsule is **never applied**. |
| Versioned dependency on `linux-modules-<abi>-qcom` | Capsule and the DTBs it came from can currently be installed out of step. |
| Derive `fwver` from the kernel version | ~~Currently the constant `0.0.2.0`~~ **Done and verified on hardware (capsule4, re-confirmed capsule5).** Now `0x20000 \| <ABI>`; firmware reported back `132089` for ABI 1017 and `132090` for ABI 1018. |
| Fix non-reproducible build | `XmlFwEntryValidation.py:395` generates a random `uuid.uuid4()` FFS GUID per build. Fails Ubuntu reproducible-build policy; every rebuild yields a different capsule. |
| Drop the GitHub Actions plumbing | CI cert injection, `secrets: inherit`, workflow changes — none of it applies to Launchpad. |

All of the above is ordinary work with no external dependencies. None of it
is blocked.

Two items from the original review have since been fixed by the author
(PR head `9d7b0cc312d4`): the working notes previously committed to the repo
root are now consolidated under `debian.qcom/docs/` and not shipped in any
package, and a real bug in `verify-capsule-result.sh` was fixed — a branch that
shadowed the "apply failed, rollback available" case when firmware reported
failure.

---

## Missing / blocked

### 1. Signing — the real blocker

**The PR signs with genuine Qualcomm FMP private keys pulled from GitHub
Secrets** (PR #112: `FMPCERT`, `FMPROOT`, `FMPSUB`). It does *not* generate a
test CA. This means the firmware really does enforce, and the signature cannot
be faked or reproduced locally.

Launchpad cannot currently produce a signature this firmware accepts:

- No LP signing mode emits a bare detached PKCS#7 **with an embedded
  certificate chain**. `kmodsign -D` uses `CMS_NOCERTS`, which strips exactly
  the certs the firmware needs to chain to the fused `QcCapsuleRootCert`.
  Also wrong digest (sha512 vs sha256) and adds a module-signature trailer.
- LP auto-generates **self-signed** per-archive certs
  (`openssl req -new -x509 ...`), which will never chain to a Qualcomm root.
- LP's key model is one key + one cert; the capsule needs an intermediate too.
- Adding a mode means coordinated changes in Launchpad **and** lp-signing,
  including a DB enum migration.

Even a new signing mode is **not sufficient** on its own — a Canonical key
does not chain to a Qualcomm root. Either Qualcomm signs for us, or Qualcomm
provisions a Canonical-rooted cert into shipping firmware. That is a
provisioning/business decision, not a packaging one.

### 2. No `-generate-` / `-signed-` packages exist

This kernel has no signing-tarball plumbing at all — no `dpkg-distaddfile`,
no `raw-signing`, no tarball creation. The end-state design needs these
created from scratch (`~/qualcomm/resolute-signed` is the reference).

### 3. Certificates are not in the tree

`debian.qcom/certs/QcFMP*.pem` do not exist. Referenced by
`0-common-vars.mk:113-115`. The build fails without them unless the unsigned
path is used.

### 4. `generate_capsule.py` cannot accept an external signature

Outer capsule headers depend on the signature length, so a signing service
cannot return a blob to paste into a pre-built capsule. Needs a
`--signature <der>` mode. Small change, but a prerequisite for any split build.

---

## Packaging defect observed live — ESP selection is an unchecked assumption

`dtb-capsule.postinst.in` line 17 does `ESP_MOUNT="${ESP_MOUNT:-/boot/efi}"`
and never checks that `/boot/efi` is the ESP firmware actually boots from.

This is no longer hypothetical. During the 2026-10-07 reproduction
(`ppa-build-test-report.md` Part 8) the board had **two partitions labelled
`system-boot`** on different disks, `fstab` mounts the ESP by label, and the
label **resolved to a different disk across a single reboot** — `/dev/sda1`
when the postinst ran, `/dev/nvme0n1p1` afterwards. The capsule applied only
because `sda1` happened to win the race at install time.

Had the order been reversed, the postinst would have staged the capsule, set
`OsIndications`, verified its own write by reading it back, and **exited 0** —
with the capsule written to a partition firmware never reads. No error, no
warning, nothing applied. It presents as a *flaky* capsule update, and the
symptom ("no provenance node") is the one operators are told to read as a GRUB
override, so it actively misdirects the investigation.

Fix: resolve `BootCurrent` → `Boot####` → the `HD(…,GPT,<PARTUUID>,…)` in its
device path, compare against the PARTUUID backing `$ESP_MOUNT`, and fail
loudly on mismatch. `efibootmgr` is already present; this is a few lines of
shell. Fixing the duplicate label in `fstab` (mount by UUID) is worth doing
too, but the packaging should not depend on the environment getting it right.

**Worse than it first looks, and now fixed.** EFI variables on this platform
are backed by `VolatileVars.bin` *on the ESP*, which exists only on the boot
ESP — so with the wrong one mounted, `OsIndications` is not persisted either,
while `efivar -w` and the postinst's read-back check both still report
success. Neither existing safeguard can detect it. Implemented in `linux-signed`
as *"dtb-capsule: stage to the ESP firmware actually boots from"*: the ESP is
resolved from `BootCurrent`, staging is retargeted when the boot ESP is mounted
elsewhere, refused when it is not, and left unchanged when detection is
impossible. `efibootmgr` added to `Depends`.

**Confirmed in production conditions (Part 10).** During the 1017 → 1018
upgrade the label had again resolved to the wrong partition, and the guard
fired inside a normal `apt` install — naming both PARTUUIDs, explaining that
staging there would never be applied, and declining to stage while letting the
install complete. After the reboot the label flipped back again, so this is
not settling down on its own: the image-side fix (mount the ESP by UUID, or
stop duplicating the `system-boot` label) is still worth doing independently.

---

## Worth knowing — a version-only rebuild changes every DTB hash

Comparing the 1017 and 1018 capsule payloads: same size, and only **476 of
4,198,306 bytes differ (0.01%)** — yet all **468** entries in
`dtb-provenance-content-sha256sums.txt` change. The cause is that every DTB
embeds the package version string:

```
1017: b'om 7.0.0-1017.20\x00om,...'
1018: b'om 7.0.0-1018.21\x00om,...'
```

~4 bytes × 468 files accounts for essentially all of the difference. The rest
is the RSA signature over the changed content, the `MSS1` `FwVersion` field,
and a FAT directory timestamp in the embedded image.

Two consequences:

- **The provenance hash is a build identifier, not a content identifier.** It
  changes on every rebuild by construction, so it cannot answer "did the
  hardware description actually change?". That is a reasonable design, but it
  should not be relied on for change detection.
- **Every ABI bump ships a full 4 MB capsule and triggers a firmware write**,
  even when the hardware description is unchanged. Across a fleet that is
  flash wear and added boot time for no functional gain. If that matters, the
  capsule should only be emitted when content hashes *excluding* the version
  string actually differ.

The FAT timestamp also means the payload is not byte-reproducible even at a
fixed version — minor, but it would defeat a rebuild-and-compare check.

---

## Security item — needs attention now

`premerge-pr.yml` sets `secrets: inherit` on a `pull_request` trigger that
builds the PR **merge ref** on a **self-hosted** runner, while the inherited
secrets include a genuine Qualcomm capsule-signing **private key**.

Any fork PR can run arbitrary code in a job holding that key. `set +x`
prevents log echo but not deliberate exfiltration.

This is exploitable as soon as the workflow exists on a branch CI will run —
it does not require the PR to merge. Worth raising with the owner of those
secrets independently of the PR review.

Still present as of PR head `9d7b0cc312d4`. The 2026-09-22 rebase arguably
makes it worse: the branch now tracks current devel, so it is more likely to
be picked up and built by CI.

---

## Proposed next steps

1. **Test whether the target EVK accepts an unsigned capsule.** Everything
   below depends on this, and it is one capsule plus an ESRT check. A negative
   answer changes the plan rather than delaying it.
2. **Raise the `secrets: inherit` key exposure** with the secret owner.
3. **Start the signing conversation with Qualcomm** — will they issue a leaf
   cert under their sub-CA, or do they insist on signing themselves? Long lead
   time, so start early.
4. **Build the unsigned capsule in the kernel package** in the meantime,
   structured for later extraction. Exercises the whole build, staging and
   verification pipeline while signing is negotiated.
