# Launchpad bug 2023546: `so_incoming_cpu` on one CPU

## Outcome

The upstream Linux networking selftest was changed to report an unsupported
single-CPU environment as `SKIP` instead of failing an assertion.

The patch was sent on 2026-09-04:

- Subject: `[PATCH net-next] selftests: net: Skip so_incoming_cpu on single-CPU systems`
- Message-ID: `<20260904140334.562593-1-kuba.pawlak@canonical.com>`
- Author: Kuba Pawlak <kuba.pawlak@canonical.com>
- Submitted commit: `881f0b8e042dd8ba2c634020b953ca8cda12cd92`
- Locally amended commit: `f7dcdd312f353a94000a0bc67664aacdb446db3b`
- Mailing-list v2 commit: `fa8de91364ca23b87e53e595fecbe32f54120445`
- Upstream base: `bc35965f6940a9bf834d54187b6088b8eb09206d`

## Problem

Launchpad bug 2023546 reports all 12 `net:so_incoming_cpu` cases failing
with:

```text
Expected 2 (2) <= self->nproc (1)
```

The failures occurred on AWS `a1.medium` and Azure `Standard_B1ms`. Both
instance types expose one vCPU. Larger instances passed.

This is not a failure of `SO_INCOMING_CPU`. The selftest creates listeners
associated with individual CPUs and needs at least two CPUs to test whether
connections are distributed to the correct listener. A single-CPU system
cannot exercise that behavior.

The two-CPU prerequisite was documented when the test was introduced by
upstream commit:

```text
6df96146b202 ("selftest: Add test for SO_INCOMING_CPU.")
```

## Resolution

The fixture setup now checks the prerequisite before changing the network
namespace:

```c
nr_server = get_nprocs();
if (nr_server < 2)
	SKIP(return, "requires at least two CPUs");

setup_netns(_metadata);
```

This produces 12 TAP `# SKIP` results and no `not ok` results on a one-CPU
system. It also avoids creating a network namespace for a test that cannot
run.

## Validation

The target built successfully with:

```bash
make -s -C tools/testing/selftests/net so_incoming_cpu
```

Single-CPU behavior was exercised by preloading a temporary test shim that
made `get_nprocs()` return `1`:

```text
exit=0 skips=12 failures=0
# Totals: pass:0 fail:0 xfail:0 xpass:0 skip:12 error:0
```

The generated patch passed strict `scripts/checkpatch.pl` checks and a reverse
`git apply --check` against the committed tree.

## Linux 6.2 and 6.3 backport note

Linux 6.2 and 6.3 cannot safely use `SKIP(return, ...)` from
`FIXTURE_SETUP()` without also backporting:

```text
372b304c1e51 ("selftests/harness: allow tests to be skipped during setup")
```

Without that harness fix, setup returns but the old harness proceeds into the
test body. Linux 6.4 and newer already support fixture-level skips.

For a 6.2/6.3 backport, apply the harness fix first or make the test runner
skip `so_incoming_cpu` when `nproc < 2`.

## Local working tree

A sparse upstream Linux checkout was created at:

```text
/home/kuba.pawlak@canonical.com/canonical/kernel-versions/linux-upstream
```

Remote:

```text
https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
```

## Files in this directory

| File | Purpose |
|---|---|
| `0001-selftests-net-Skip-so_incoming_cpu-on-single-CPU-sys.patch` | Patch from the locally amended commit; the source diff is identical to the submitted change |
| `sent-message.eml` | Exact submitted message downloaded from Patchwork; suitable for `git am` |
| `sent-diff.patch` | Exact source diff extracted by Patchwork |
| `send-email.txt` | `git send-email` command and recipients used |
| `launchpad-bug-2023546.json` | Launchpad API record |
| `commit-history.txt` | Upstream base, submitted commit, and local amendment |
| `recipients.txt` | Mailing lists and maintainers |
| `links.txt` | Bug, source, commit, and archive URLs |
| `validation.txt` | Build and simulated single-CPU results |
| `backport-6.2.md` | Required compatibility note for Linux 6.2/6.3 |
| `v2/` | Corrected resend without the Launchpad trailer and with conventionally wrapped commit text |
