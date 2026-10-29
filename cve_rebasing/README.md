# CVE Rebasing — backporting CVE fixes into Ubuntu kernels

A self-contained playbook for backporting an upstream Linux CVE fix into
one or more Ubuntu kernel trees and opening the SRU pull requests. Written
from the CVE-2026-53009 effort (ice double-free of `tx_buf` skb), backported
to Noble (6.8), Jammy (5.15) and Focal (5.4).

This is the **manual equivalent** of what the `kybele-ng` automation
(`~/canonical/kybele`) does. When in doubt, the kybele source is the
authoritative spec for formats and mechanics — this doc points at the exact
files. The agentic sub-steps are described in `~/canonical/kybele/prompts/*.md`.

---

## TL;DR procedure

For each target series (Noble / Jammy / Focal):

1. Identify the **fix** commit upstream and its true `Fixes:` introducer.
2. In the kernel tree, create a branch `CVE-<id>` off local `master-next`.
3. Apply the fix: `git am --3way` the upstream patch where it lands cleanly;
   otherwise hand-adapt faithfully to the older tree's shape.
4. Rewrite the commit message to Ubuntu CVE/SRU format (provenance tag +
   optional `[handle: note]` + `CVE-<id>` + your SoB).
5. Validate the message against `commit_check.rs` rules.
6. Fork the Forgejo repo, push the branch to the fork, open a PR against
   `master-next--integration` with an SRU cover letter.

Then build + boot test, and only then claim it in the cover letter.

---

## Key locations

| Path | What |
|------|------|
| `~/canonical/linux--noble` | Noble 6.8 kernel tree |
| `~/canonical/linux--jammy` | Jammy 5.15 kernel tree |
| `~/canonical/linux--focal` | Focal 5.4 kernel tree |
| `~/canonical/kybele` | The automation; authoritative spec for formats |
| `~/canonical/kybele/src/backport.rs` | Commit-message construction (`add_cve_tag`) |
| `~/canonical/kybele/src/commit_check.rs` | Ubuntu commit-format validation rules |
| `~/canonical/kybele/src/submit.rs`, `src/forgejo.rs` | PR / cover-letter / fork-push mechanics |
| `~/canonical/kybele/prompts/*.md` | Agentic sub-step instructions (see below) |
| `patches/` (this dir) | The three landed CVE-2026-53009 patches, for reference |

Forgejo API base: `https://kernel.ubuntu.com/forgejo/api/v1`
SSH push: `ssh://git@forgejo.kernel.ubuntu.com/<owner>/<name>.git`

---

## Step 1 — Understand the fix before touching a tree

- The "break commit" the operator gives you may actually be the **fix**, not
  the regression. Read it. Its `Fixes:` tag names the true introducer.
  (CVE-2026-53009: given `1a303baa715e` is the FIX; `Fixes: d76a60ba7afb`.)
- Read the upstream commit message and list **every behaviour it changes**,
  especially the easy-to-miss ones: error paths, teardown reached via `goto`,
  unlock, free, refcount put, ordering, and the condition guarding each.
  (This is the `sanity-check.md` discipline — apply it up front.)

CVE-2026-53009 fix, in `ice_xmit_frame_ring()`
(`drivers/net/ethernet/intel/ice/ice_txrx.c`):
1. Move `first = &tx_ring->tx_buf[tx_ring->next_to_use];` **up** so it is
   valid on every error path.
2. In `out_drop:` reset the tx_buf so `ice_unmap_and_free_tx_buf()` can't
   double-free the skb when the interface is later brought down.

---

## Step 2 — Apply the fix, faithfully, per tree

Newer trees often take the patch verbatim; older trees are shaped
differently and need hand-adaptation. **Preserve behaviour, not just the
visible diff** (`resolve-conflict.md`): if upstream reaches cleanup through a
`goto label`, and that path doesn't exist here, reproduce the side effects
inline. Never turn a jump-into-error-handling into a bare `return`/`break` —
that silently drops a teardown/free/unlock.

Searching an older tree (from `resolve-conflict.md` / `sanity-check.md`):
```bash
rg --files -g '*name*'                    # locate files
rg -n -F -w 'symbol' -- path              # exact symbol, not substring
git grep -n -F -w 'symbol' -- '*.c' '*.h' # tracked-source confirmation
```
`git grep` exit 1 = a completed search found nothing — but still check for a
**renamed or equivalent** analogue before declaring behaviour absent. A
timeout / error / truncated / fuzzy result never proves absence. `git am` on
an old tree can fuzzy-match a hunk into the wrong file/function; if so, find
the real analogue and apply there.

### The per-tree adaptation that mattered here

The double-free guard depends on how each tree frees a tx_buf:

| Series | Tree shape | Faithful reset in `out_drop:` |
|--------|-----------|-------------------------------|
| Noble 6.8 | has `tx_buf->type` (`ICE_TX_BUF_EMPTY`/`_SKB`) — same as upstream | `first->type = ICE_TX_BUF_EMPTY;` (verbatim) |
| Jammy 5.15 | no `type` field; free path guards `if (tx_buf->skb)` | `first->skb = NULL;` |
| Focal 5.4 | no `type` field; same `if (tx_buf->skb)` guard | `first->skb = NULL;` |

- Noble: `git am --3way` (one contextual conflict around
  `ipv6_hopopt_jumbo_remove()` — placed the `first` init before that early
  drop). Result +4/−2.
- Jammy / Focal: hand-adapted — `first->skb = NULL` in `out_drop` plus moving
  the `first` init to the top of the function. Focal also has an extra
  `ice_tx_prepare_vlan_flags` error path that the moved-up init now covers.
  Focal has no `ice_trace()` in xmit (Jammy does), which shifts where the
  init lands.

**Lesson:** translate the fix into the target tree's idiom (here: the
skb/type guard the free path actually checks), don't paste the upstream line.

---

## Step 3 — Commit message format (Ubuntu CVE/SRU)

Authoritative logic: `backport.rs::add_cve_tag`; validation:
`commit_check.rs`. Structure of the message body, in order:

1. Original upstream subject + body + original authorship (keep
   `Signed-off-by` chain from upstream).
2. **Provenance tag**:
   - clean cherry-pick → `(cherry picked from commit <40-hex>)`
   - adapted/backported → `(backported from commit <40-hex>)`
3. For a backport, an adaptation **note** line: `[handle: short note]`
   — handle has **no whitespace** (e.g. `[kuba: reset first->skb ...]`).
   For a clean pick, no note. (See `note-instruction.md`: write one line
   describing the adaptation, or the single word `clean`.)
4. `CVE-<id>` on its own line.
5. Your `Signed-off-by: Name <local@domain.tld>` as the **last line**.
   The CVE tag is inserted **above** the final SoB.

`commit_check.rs` rules an upstream-type commit must pass:
- a valid CVE line (or BugLink) — CVE alone is enough, the tool adds no
  BugLink;
- a valid provenance tag; a `backported` tag **requires** the `[handle:…]`
  note;
- a valid last-line SoB `Name <local@domain.tld>`.
- Single-commit PRs trivially pass series-consistency checks.

Preferred Git identity used here: `Kuba Pawlak <kuba.pawlak@canonical.com>`,
note handle `kuba`. Author stays the upstream author; you are the committer.

> Validate before pushing. A tiny Python/`grep` check that reproduces the
> `commit_check.rs` predicates catches a malformed tag or missing note early.

### kybele / LAWS note
`~/canonical/kybele` itself has its own commit discipline (read `LAWS.md`,
add a `Laws:` trailer, ASCII, wrap 72, `Signed-off-by` + `Assisted-by`).
That applies to commits **to kybele**, not to the kernel-tree CVE commits.

---

## Step 4 — Open the Forgejo PRs

Target repos (note Focal is **not** under `ubuntu-kernel`):

| Series | Forgejo repo |
|--------|--------------|
| Noble | `ubuntu-kernel/linux--noble` |
| Jammy | `ubuntu-kernel/linux--jammy` |
| Focal | `canonical-kernel-esm/linux--focal` |

Auth: a Forgejo token in header `Authorization: token <T>`. **Do not commit
the token anywhere.** Obtain it from the operator / secrets store at runtime.

Mechanics (from `forgejo.rs` / `submit.rs`), with observed quirks:

1. **Fork**: `POST /repos/{owner}/{name}/forks`. This often returns
   **504 Gateway Timeout** but the fork is created **asynchronously** — poll
   `GET /repos/<you>/{name}` until it returns 200.
2. **Push** the `CVE-<id>` branch to your fork over SSH.
3. **Create PR**: `POST /repos/{repo}/pulls` with
   `{head:"<you>:CVE-<id>", base:"master-next--integration", title, body}`.
4. **Edit PR body**: `PATCH /repos/{repo}/pulls/{num}` — returns **201** on
   success (not 200).

- PRs always target **`master-next--integration`**.
- Title tag (`patchset.rs::subject_tag`): series initial, uppercase →
  `[SRU][N] CVE-…`, `[SRU][J] …`, `[SRU][F] …`.

### Focal integration-branch gotcha (important)
Focal's **local** tree pulls from **Launchpad**, but its PR is pushed to
**Forgejo**. The two integration tips diverge:
- Forgejo `canonical-kernel-esm/linux--focal` `master-next--integration` tip
  = `07191284627d` (matched local `master-next`).
- Local tree's `origin/master-next--integration` (Launchpad) = `dd0b52d170`
  (ahead).

Set the fix commit's **parent to the Forgejo base** (`07191284`) so the PR is
a clean single commit. Here `ice_txrx.c` was identical across those bases, so
rebasing was conflict-free — verify that for your file.

---

## Step 5 — Cover letter (SRU)

Template: `submit.rs::cover_text`; guidance: `cover-letter.md`. Sections:

```
[ Impact ]                  the CVE description / what breaks
[ Fix ]                     <series>/linux: backported from <sha12>; <note>
[ Test Plan ]               e.g. "Build and boot tested."
[ Where Problems Could Occur ]
[ Other Info ]
```

- **Honesty constraint** (`cover-letter.md`): never invent test results.
  Write the Test Plan as pending until testing is actually done; only then
  PATCH the PR body to "Build and boot tested." (That sequence is exactly
  what happened here — the "boot tested" line was added after the operator
  confirmed it.)
- Allowed link domains: launchpad.net, ubuntu.com, discourse.ubuntu.com,
  warthogs.atlassian.net, nist.gov, kernel.org, cve.org
  (`git.kernel.org` is fine as a kernel.org subdomain).
- If `~/canonical/ubuntu-cve-tracker` is empty/unavailable, take the CVE
  description from the upstream commit message.

---

## CVE-2026-53009 — result

| Series | PR | Diff |
|--------|----|------|
| Noble | `ubuntu-kernel/linux--noble` #37 | +4/−2 `ice_txrx.c` |
| Jammy | `ubuntu-kernel/linux--jammy` #204 | `ice_txrx.c` |
| Focal | `canonical-kernel-esm/linux--focal` #266 | `ice_txrx.c` |

Each PR = one clean commit against `master-next--integration`, mergeable,
Test Plan updated to "Build and boot tested." The three landed patches are in
`patches/` for reference.

---

## Pitfalls checklist

- [ ] The given "break commit" is often the **fix** — read its `Fixes:` tag.
- [ ] Don't paste the upstream line; translate it to the tree's free/guard
      idiom (type field vs `skb` NULL-ing here).
- [ ] Carry every `goto`-reached side effect across when flattening a path.
- [ ] `backported` tag **must** have a `[handle:note]` line (no whitespace in
      handle); `cherry picked` must not.
- [ ] CVE line and last-line SoB are mandatory; validate with
      `commit_check.rs` rules before pushing.
- [ ] Fork POST → 504 is normal; poll for the fork, don't retry blindly.
- [ ] PR base is always `master-next--integration`.
- [ ] Focal: push to Forgejo, parent on the **Forgejo** base, not Launchpad.
- [ ] Never write test results you didn't run; never commit the Forgejo token.
```
