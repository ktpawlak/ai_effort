# Review: "UEFI capsule signing for the Carmel project"

Review of the document intended for the Launchpad team. Findings are ordered by
severity. Each one that could be checked was checked against the working
implementation in `~/qualcomm/resolute/linux-qcom/linux-signed/debian/capsule/`
and against `openssl` directly, rather than reasoned about; the commands used
are quoted so they can be re-run.

Overall: the context, the PKI model and the two-option framing are right, and
Option 2 is the right direction. Three things would stop an implementer:

1. **The wrong artefact is sent for signing.** Not a flaw in the tooling or in
   `firmware.fv`, which is produced correctly — the signature simply covers 24
   more bytes than that file contains (a 16-byte FMP header before it and the
   8-byte monotonic count after). Fixed by uploading
   `generate-capsule --emit-signable` output instead. (finding 1)
2. **The `openssl` command silently discards the intermediate certificate**,
   because it passes `-certfile` twice and only the last takes effect. Every
   capsule built from the command as written would fail chain validation on
   device. Fixed by concatenating the chain into one file. (finding 2)
3. **Option 1 asks Launchpad to do something its build architecture does not
   permit** — run the generator, with keys, on build workers. (finding 3)

None of the three is expensive to fix, and none implies rework of the capsule
design; items 1 and 2 are a changed artefact and a changed command line. They
are flagged as blocking only because both would pass every build-time check and
fail on hardware, which is the most expensive place to discover them.

---

## 1. BLOCKING — the signed input is not `firmware.fv`

Section 4 states:

> The input consists of the raw firmware.fv payload.

and Option 2 encodes that as `-in firmware-XXX.fv`.

**First, to be fair to the document:** `firmware.fv` is not "raw" in the sense
of being an unwrapped DTB. It is a UEFI firmware volume, correctly produced by
`qcom-capsule-tool create`, and `config.json` legitimately names it as
`"Payload"`. Nothing is wrong with the file itself. The objection is narrower
and entirely about *which bytes carry the signature*.

Under UEFI FMP the signature covers the **FMP payload header, the payload, and
the 64-bit monotonic count appended**:

```
signed_data = "MSS1" || FwVersion(4) || LowestSupportedVersion(4) || reserved(4)
           || <firmware.fv>
           || MonotonicCount  (8 bytes, little-endian)
```

Measured, by running `create` and then `generate-capsule --emit-signable` on
the same `config.json`:

```
firmware.fv  : 66944 bytes
signable blob: 66968 bytes      (+24 = 16 prepended, 8 appended)
cmp          : differ at byte 1
blob[16 : 16+len(fv)] == firmware.fv      exactly
```

The 16 prepended bytes decode against that run's `config.json`:

| Bytes | Field | Value |
|---|---|---|
| `4d53 5331` | signature | `MSS1` |
| `1000 0000` | header size | 16 |
| `0100 0200` | FwVersion | `0x20001` — matches `"FwVersion": "0x20001"` |
| `0000 0000` | LowestSupportedVersion | 0 — matches |
| trailing `0200…` | MonotonicCount | 2 — matches `"MonotonicCount": "0x2"` |

From `qcom_capsule_tool/generate_capsule.py`:

```python
def encode_signable(descriptor):
    """Return the exact byte string an external signing service must sign.

    This is the MSS1 image with the 64-bit monotonic count appended ...
    """
    image = encode_image(payload, descriptor.fw_version,
                         descriptor.lowest_supported_version)
    return image + struct.pack("<Q", descriptor.monotonic_count)
```

Upstream EDK2 `GenerateCapsule.py` does the same — it prepends
`FmpPayloadHeader` and appends `MonotonicCount` before invoking the signer. So
the document is wrong for *both* tools it describes, not just the Qualcomm one.

**Consequences the document should state:**

* Signing `firmware.fv` signs 66944 bytes; the firmware verifies a signature
  over 66968. It does not validate. The failure appears at install time on
  hardware, not at build time.
* `FwVersion`, `LowestSupportedVersion` and `MonotonicCount` are **inside** the
  signature — demonstrably so, per the table above. Section 3.1 dismisses them
  as "additional fields manage firmware versioning, monotonicity, and rollback
  protection properties", which understates them: they are signed inputs, so
  signing the bare fv would leave the entire anti-rollback policy outside the
  signature and therefore modifiable after signing.
* Therefore **Option 2 cannot hand Launchpad `firmware-XXX.fv`.** It must hand
  over the signable blob. `qcom-generate-capsule --emit-signable` produces
  exactly it, and the blob is self-describing: the monotonic count is recovered
  from its last eight bytes at reassembly, so nothing extra has to travel for
  the signature step itself.

The fix is small — it is a change of *which artefact* is uploaded, not a change
of tooling. That is worth saying plainly, because it is otherwise easy to read
this finding as an objection to the whole approach.

### 1a. A real way to get an improper `firmware.fv`

Distinct from the above, and worth a line in the document because it bit us:
**`create` is not reproducible by default.** The FFS `FileGuid` is drawn at
random per run, so byte-identical inputs yield different firmware volumes:

```
run 1 vs run 2, identical inputs, FileGuid unpinned : NO  (34 bytes differ)
run 3 vs run 4, QCOM_CAPSULE_FILE_GUID pinned        : YES
```

A non-reproducible payload means a capsule can never be rebuilt and checked
against what was signed, which is the usual way of answering "is the artefact
in the archive the one we signed?". We pin `QCOM_CAPSULE_FILE_GUID` from the
DTB provenance hash for this reason.

A second trap, already recorded in our build script: the tool catches
exceptions thrown during firmware-volume construction and carries on printing
`FV created successfully`, so a failed FV surfaces as a blob far too small to
hold the payload rather than as a non-zero exit. That shipped once — a
`FileGuid` assigned with the wrong ctypes type produced a 152-byte blob instead
of 4195736. Any Launchpad-side integration should size-check the artefact
rather than trust the exit status.

## 2. BLOCKING — `-certfile` twice drops the intermediate

Both section 4 and Option 2 specify:

```
-certfile keys/IntermediateCA.pem \
-certfile keys/RootCA.pem \
```

Only the last `-certfile` takes effect. Measured with a purpose-built
three-level PKI (`Root` → `Intermediate` → `Signer`):

| Form | Certs embedded | Verify against root |
|---|---|---|
| `-certfile int.crt -certfile root.crt` (as documented) | 2 — Root, Signer | **FAILS**: `unable to get local issuer certificate` |
| `-certfile chain.pem` where `chain.pem = int.crt + root.crt` | 3 — Intermediate, Root, Signer | `Verification successful` |

This is not cosmetic. Section 3.2 mandates a "Launchpad-specific intermediate",
and the firmware has no certificate store — EDK2 populates its `X509_STORE_CTX`
from the PKCS#7 blob itself and verifies against the single root fused into the
device. Anything not carried inside the signature is unavailable to the
verifier. So with the documented command, **every capsule produced would be
rejected on-device**, and again only on-device.

**Fix:** concatenate, and pass one `-certfile`:

```sh
cat keys/IntermediateCA.pem keys/RootCA.pem > keys/chain.pem
openssl smime -sign -binary -outform DER -md sha256 \
    -signer keys/SignerPrivateCert.pem -inkey keys/SignerPrivateCert.pem \
    -certfile keys/chain.pem \
    -in <signable-blob> -out <signable-blob>.sig
```

Including the root is optional — the device already has it — but harmless. The
intermediate is mandatory.

## 3. BLOCKING — Option 1 does not fit Launchpad's build architecture

> Both input files are passed directly to Launchpad build workers. Launchpad
> executes either the EDK2 base script or `qcom-generate-capsule` Python
> modules to output complete capsule files.

Launchpad build workers are untrusted and never hold archive signing keys.
Signing happens afterwards, in the archive publisher
(`lib/lp/archivepublisher/signing.py`) or, in production configuration, in the
separate `lp-signing` service. The build uploads a custom tarball; the
publisher dispatches on file extension to a per-key-type signing routine.

Presented to the Launchpad team as written, Option 1 reads as a request to put
private keys on builders. It should instead be framed as what it actually is:
**add a new signing mode / `SigningKeyType`**. That also removes the awkward
requirement to install a Python capsule tool into the signing environment,
which none of the existing modes need.

Worth telling them this is already prototyped — `~/qualcomm/ai_effort/launchpad_signing/`
holds two patches, both re-verified with `git apply --check` against pristine
upstream on 2026-10-22:

| Patch | Repository | Upstream HEAD | Scope |
|---|---|---|---|
| `0001-add-capsule-signing-mode.patch` | `git.launchpad.net/launchpad` | `4a0ddb9` | `SigningKeyType.CAPSULE`, `.capsule` dispatch, `signCapsule()` |
| `0002-lp-signing-capsule-key-type.patch` | `git.launchpad.net/lp-signing` | `89306e8` | key custody and the signing operation |

(`0001` had to be refreshed for upstream drift — `black` reformatting and the
new `ANDROID_KERNEL` key type. It is `black`-clean at the project's 79-column
setting. Re-check before circulating.)

Note that `signCapsule()` already passes a **single** `-certfile` pointing at a
concatenated `capsule-chain.pem`, i.e. the form that works — so the prototype
and the document disagree, and the prototype is the one that is right.

The patches also record why no existing mode can be reused, which the Launchpad
team will ask:

* `signKmod`/`signOpal`/`signSipl` use `kmodsign -D sha512`, which emits
  neither an embedded certificate chain nor signed attributes — measured at 436
  bytes with `cert: <ABSENT>`, `auth_attr: <ABSENT>`, against 1406 bytes for
  the `openssl cms` equivalent.
* `signUefi` runs `sbsign`, which requires PE/COFF and rejects a capsule blob
  with `Invalid DOS header magic`.
* `signFit` runs `mkimage -F -k -r`, which rewrites in place and understands
  only FIT.

## 4. SIGNIFICANT — the GUID is per product, not per SoC

> Guid: Unique identifier associated with specific SoC hardware.

The second sentence ("Each target platform requires a dedicated GUID") is
correct; the first is not. The example GUID in section 3.1,
`0F6D58FC-2258-4D27-9E23-D77219B0897C`, is our hamoa / **IQ-X7181** GUID. purwa
/ **IQ-X5121** uses `185a798b-13b2-4595-bd08-e2770a4bb190` — and both products
are the **same SoC**, X1E80100 (both pull
`IQ-X.1.8-Ver.1.2-ubuntu-X1E80100-nhlos-bins.tar.gz`).

An FMP GUID identifies *a firmware image type on a product*, not a SoC. This
matters to the reader because it sets how many GUIDs must be provisioned and
how many signing requests a build generates — tying it to SoC count
under-estimates both.

## 5. SIGNIFICANT — nothing says what Launchpad returns

Option 2 ends with "Subsequent packaging ... is handled downstream by Carmel
packaging utilities", but never states the hand-back contract. The Launchpad
team will need:

* **The return artefact**: a detached PKCS#7, `<name>.capsule.sig`, beside the
  input. (`signFit` rewrites in place; capsule signing must not, because the
  signature is detached and the caller reassembles.)
* **The metadata that must survive the round trip**, because it sits *outside*
  the signature and so cannot be recovered from the blob: FMP GUID, image
  index, hardware instance, capsule flags (`PersistAcrossReset`). Our
  implementation emits exactly this as a `.capsule.vars` sidecar.

Add a short "inputs / outputs" table. It is the first thing an implementer will
look for and currently it is not there.

## 6. SIGNIFICANT — key autogeneration must be refused explicitly

Launchpad autogenerates signing keys for new key types on first use. For every
existing type that is safe: a fresh self-signed key produces a signature whose
consumer has to be told about it anyway.

A capsule is different. The trusted root is fused into the device **at flash
time**, before the archive ever signs anything. An autogenerated key yields
capsules that build, publish and install perfectly and are rejected by every
device in the field. The document should say in section 3.2 that the key
material must be the *provisioned* root and its intermediate, and that
autogeneration has to be refused rather than merely avoided.

## 7. MINOR — reproducibility of the signable blob

Covered in finding 1a above with measurements, and repeated here only because
the reviewed document's own structure would raise it under its §4: capsule
assembly draws a random FFS `FileGuid`, so the payload — and with it the
signable blob — differs between otherwise identical builds unless it is pinned.
One sentence in the document would save the next person the same
investigation.

## 8. MINOR — monotonic count ownership

`"MonotonicCount": "0x2"` is hardcoded in the example with no discussion of who
increments it or what happens on rebuild. Since it is inside the signed data
and the device may enforce monotonicity, the document should say whether it is
per-build, per-upload or static.

## 9. Editorial

| Where | Issue |
|---|---|
| §2.2 | "available for Bionic" — 18.04 is EOL/ESM. Presumably Noble or Resolute. |
| §2.2 | `qcom-capsule-tool` is said to be in `carmel-team/carmel-proposed`, `qcom-generate-capsule` merely "in our PPA" — name it. |
| §3 | "Set of SSL keys (Provided by the Launchpad)" — grammar; and it conflicts with §3.2's "to be issued", which does not say by whom. |
| §3.2 | "root certificate public key" — a certificate is not a public key; say "root certificate". |
| §4 | "Subsequently" capitalised mid-sentence. |
| §5.1 | `qcom-generate-capusle` — typo. |
| §3 vs §5 | Payload filename drifts: `firmware.fv`, `firmware-XXX.fw`, `firmware-XXX.fv`, `firmware-XXX.fw-signed`. Pick one convention. |
| §3.1 | `OpenSslSignerPrivateCertFile` is passed as both `-signer` and `-inkey`, so it must hold cert *and* key. Worth stating; it surprises people. |

## 10. What the document gets right

Worth keeping as-is:

* Authentication happens **regardless of Secure Boot state** — frequently
  assumed otherwise, and it is the reason this cannot be deferred.
* The root is provisioned at flash time.
* PKCS#7 / detached signature, `sha256`, `PersistAcrossReset` — all match the
  working implementation.
* One GUID per capsule, so generation iterates over targets.
* Offering Launchpad a path with no Python dependency (Option 2) is the right
  instinct, and is what the prototype does.

---

## Recommended restructure

Collapse the two options into one concrete proposal, because Option 1 is not
available and Option 2 is nearly right:

1. The build worker runs `qcom-generate-capsule --emit-signable`, producing
   `<machine>-dtb.capsule` (the signable blob) and `<machine>-dtb.capsule.vars`
   (the FMP fields outside the signature). No keys involved.
2. These go to the archive as a signing custom upload.
3. Launchpad adds a `CAPSULE` key type, dispatched on the `.capsule` extension,
   which runs the single-`-certfile` `openssl smime` command above and returns
   a detached `<machine>-dtb.capsule.sig`.
4. The `-signed-` source package reassembles blob + signature + vars into the
   final `.cap`.

That is the shape already implemented and prototyped locally, so the document
can point at running code and two applying patches rather than a proposal —
which is a much stronger thing to put in front of the Launchpad team.
