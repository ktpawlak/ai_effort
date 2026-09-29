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
| Derive `fwver` from the kernel version | Currently the constant `0.0.2.0`; risks `ErrorIncorrectVersion` on the next update. |
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
