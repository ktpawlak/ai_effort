# Capsule signing scope — what is actually signed in PR #111

**Question:** is the FIT image inside the capsule signed, or is only the capsule signed?

**Answer: only the capsule is signed. The FIT inside is neither signed nor hashed.**

---

## 1. Capsule layer — signed

`debian.qcom/scripts/qcom_capsule_tool/generate_capsule.py` builds a standard
`EFI_FIRMWARE_IMAGE_AUTHENTICATION` structure:

- Detached **PKCS#7** over the FMP payload **plus the 64-bit monotonic count**
  (`generate_capsule.py:186-191`; the comment at `:65` notes the signature
  covers the image and certificate data but not the leading monotonic count
  field itself).
- Produced by shelling out to OpenSSL (`sign_payload_openssl`,
  `generate_capsule.py:134-162`):

  ```
  openssl smime -sign -binary -outform DER -md sha256 \
      -signer <OpenSslSignerPrivateCertFile> \
      -certfile <OpenSslOtherPublicCertFile>
  ```

- Default digest `sha256` (`DEFAULT_HASH_ALGORITHM`, `:74`).
- Cert roles, wired from `debian/rules.d/0-common-vars.mk`:
  | JSON field | rules variable | file |
  |---|---|---|
  | `OpenSslSignerPrivateCertFile` | `dtb_capsule_cert_leaf` (`-p`) | `QcFMPCert.pem` |
  | `OpenSslOtherPublicCertFile` | `dtb_capsule_cert_sub` (`-oc`) | `QcFMPSub.pub.pem` |
  | `OpenSslTrustedPublicCertFile` | `dtb_capsule_cert_root` (`-x`) | `QcFMPRoot.pub.pem` |

- `signtool` signing is explicitly rejected (`:109`); OpenSSL only.

### Failure mode

`CapsuleDescriptor` sets `self.sign = any(certs)` and raises only if the set is
*partially* provided (`:113-123`). If **no** certs are given it prints:

```
WARNING: no OpenSSL certificates given, unsigned capsule payload
```

and emits an **unsigned capsule** rather than failing (`:182-183`). Combined with
the missing `debian.qcom/certs/` directory and the non-`set -e` make recipe, an
unsigned or absent capsule can reach the package without the build failing.

---

## 2. FIT layer — unsigned and unhashed

`debian.qcom/fitimage/build-dtb-image.sh:680`:

```
mkimage -f qcom-next-fitimage.its out/qclinux_fit.img -E -B 8
```

- No `-k` (key dir), no `-K` (key destination DTB), no `-r` (required key).
- `qcom-next-fitimage.its` contains **no `signature` subnodes and no `hash`
  subnodes** under `images` or `configurations` — every entry is just
  `data = /incbin/(...)` plus `type = "flat_dt"`. `mkimage` only computes
  hashes for hash nodes that are declared, so the FIT carries **no integrity
  data at all**, not even a checksum.
- The FAT wrapper (`mformat` + `mcopy`, `:697-700`) adds nothing either.

---

## 3. Consequences

1. **Verification happens exactly once.** UEFI firmware validates the PKCS#7 at
   capsule-apply time, before writing the payload to the spinor `dtb_a`/`dtb_b`
   partitions. After that the FIT sits on flash with no signature travelling
   with it. Whether it is re-verified on every subsequent boot depends entirely
   on the Qualcomm XBL/boot chain measuring that partition — nothing in this PR
   provides it.

2. **Non-capsule write paths are unauthenticated.** Anything that writes
   `dtb_a`/`dtb_b` outside the capsule flow (fastboot/QDL flashing, or a
   privileged raw write if the partition is exposed to the OS) bypasses the only
   signature check. A `mkimage`-signed FIT would have given defence in depth
   here.

3. **The provenance node is not a security control.**
   `/qcom-dtb-capsule-provenance/dtb-provenance-sha256` is an unsigned,
   self-reported value embedded *inside* the DTB it describes. It detects
   accidental kernel⇄DTB mispairing and firmware rollback, not tampering — an
   attacker rewriting the DTB rewrites the node too.

4. **Downgrade is not prevented by the signature.** With
   `dtb_capsule_lfwver = 0.0.0.0` (lowest supported version = 0, anti-rollback
   disabled) and a constant `dtb_capsule_fwver = 0.0.2.0`, a validly signed
   *older* capsule can be replayed. Version monotonicity, not the signature, is
   what would stop that.

---

## 4. If FIT-level signing were wanted

It would be a separate key hierarchy from the FMP capsule certs:

- add `hash-1 { algo = "sha256"; }` to each image node, and
  `signature-1 { algo = "sha256,rsa2048"; key-name-hint = ...; sign-images = ...; }`
  to each configuration node in `qcom-next-fitimage.its`;
- run `mkimage -f ... -k <keydir> -K <control-dtb> -r`;
- provision the public key into whatever consumes the FIT (U-Boot control DTB /
  firmware trust store).

Note that `build-dtb-image.sh`'s `--prune` rewrites the ITS, so any signature
nodes would have to survive that rewrite — the awk-based pruner would need
updating.
