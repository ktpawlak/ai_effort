# Re-deriving an Ubuntu kernel derivative

## Problem

A **derivative** branch `D` (e.g. `linux-qcom`) is a generic Ubuntu kernel `G_old`
plus a large stack of OEM patches. Every few weeks `D` is rebased onto a newer
generic `G_new`. This hurts because:

1. **Volume** — the OEM stack is huge (noble `linux-qcom` 6.8.0-1091.96 carries
   **3,638** non-merge commits on top of generic `Ubuntu-6.8.0-148.148`).
2. **Modified-upstream collisions** — a patch was given to the derivative in a
   *preliminary* form (`FROMLIST:` / `PENDING:`) and later merged **upstream in a
   different form**, often with the *same subject*. When that upstream form lands
   in `G_new`, the derivative copy does **not** auto-drop (contents differ) and
   conflicts against the base commit.
3. **Churn** — commits added, reverted, and re-added; and prior conflict
   resolutions that silently *mutated* patch contents over successive rebases.

## Goal

"**Re-derive**" `D`: scan the OEM stack, recover each commit's *intent* and
*upstream provenance*, detect where the derivative's content diverged from its
upstream form and why, then re-express the stack as the **smallest set of clean,
independently-mergeable feature branches** that reproduce the *same tree* while
removing as many conflict points as possible.

## The governing invariant

History is noise; two things are ground truth:

- **Net tree diff:** `git diff G_old..D` — what the derivative actually changes.
- **Reproducibility:** any output `D'` must satisfy
  `tree(D') == tree(naive_rebase(D, G_new))` minus patches that are redundant
  because they are already in `G_new`.

Every transformation below is only *allowed* because it is checked against this
invariant. The AI classifier never gets to silently drop content — anything
uncertain is flagged for human review.

## Pipeline

### Phase 0 — Corpus extraction (deterministic)
Enumerate `BASE..HEAD --no-merges`. Per commit record: subject, SAUCE prefix,
author/committer, `Change-Id`, provenance trailers
(`(cherry picked from commit <sha> <repo>)`, `commit <sha> upstream`, `Link:`),
touched files, diffstat, and `git patch-id`. Persist to SQLite — the working DB.

**Provenance signal is already in the subject prefix** (Ubuntu SAUCE convention):

| Prefix               | Meaning                                   | Re-derive implication            |
|----------------------|-------------------------------------------|----------------------------------|
| `UPSTREAM:`/`FROMGIT:`| cherry-picked from a real upstream/-next  | will graduate into `G_new` → redundancy / modified-upstream risk |
| `BACKPORT:`          | upstream commit adapted to older base     | reconcile against upstream form  |
| `FROMLIST:`          | taken from a mailing-list posting         | **high** collision risk once it lands |
| `PENDING:`           | vendor preliminary, no upstream ref yet   | OEM-only until/unless it lands   |
| `QCLINUX:`           | cherry-pick from CodeLinaro/qclinux vendor tree | OEM-only                   |
| `UBUNTU:`            | packaging / config / ABI / SAUCE          | separate packaging track         |

### Phase 1 — Churn cancellation
Detect commit↔revert↔re-add chains and duplicate applications, then collapse to
net effect. Removes conflict points that net to zero before any matching work.

Patch-ids for the whole stack come from one pipeline
(`git log -p … | git patch-id --stable`, ~15 s for 3.6 k commits).

A `Revert "<subject>"` is matched to the newest earlier commit with that subject
(SAUCE prefixes stripped). **Subject matching alone is not sufficient proof** — the
pair must be shown to compose to nothing. Two tests, cheapest first:

1. **patch-id identity** — `patch-id(REV→REV^) == patch-id(ORIG^→ORIG)`.
2. **content composition** — if (1) fails, compare the four line multisets of the
   two diffs:
   `(A⁺−R⁻) ∪ (R⁻−A⁺) ∪ (A⁻−R⁺) ∪ (R⁺−A⁻)`.

Test (2) exists because **a revert is usually not a byte-exact inverse of its
target, and the reason is almost always a previous conflict resolution.** On the
pilot, 11 of 42 pairs failed test (1); inspecting them showed three distinct
mechanisms, none of which mean the pair fails to cancel:

| mechanism | example |
|---|---|
| file renamed between add and revert | nodes added to `qcm6490-rb3.dts`, removed from `qcs6490-rb3gen2.dts` |
| cosmetic drift during rebase | original bumps copyright `2023`→`2023-2024`; the revert bumps it to `2023-2025` instead of restoring `2023`, while reverting `DMA_RX_BUF_SIZE` exactly |
| whitespace / hunk restructuring | identical content, different patch-id |
| **genuine partial revert** | revert removes lines the original never added — the original was *reworked* in between |

Test (2) is immune to filenames, whitespace and hunk structure, so it separates
these cleanly. Result: 7 of the 11 do cancel (2 structurally, 5 with cosmetic
residue only); just **4** carry a real functional delta and are kept.

Cancelling a pair whose residue is cosmetic means the rebuilt tree differs from
`D` by exactly those comment lines. That is a deliberate, *bounded and reported*
deviation from the net-tree invariant — `churn` prints the residual lines, and
`--strict-revert` disables the tolerance.

| outcome | meaning |
|---|---|
| `DROP_NET_ZERO` | add + revert compose to nothing (exact, structural, or cosmetic-only) |
| `DROP_SUPERSEDED` | add + revert + re-add — only the re-add survives |
| `DROP_DUPLICATE` | identical patch-id applied twice (FROMLIST + FROMGIT pair) |
| `KEEP_PARTIAL_REVERT` | functional residue — the original was reworked in between |

Reverts whose target is **not** in the OEM stack revert a *generic-base* commit.
These are genuine OEM changes and are always kept (25 on the pilot).

### Phase 2 — Upstream matching & classification
For each surviving commit, decide its relation to `G_new`, highest confidence first:

1. `patch-id` equal to a commit in `G_new` → **IDENTICAL** → drop.
2. provenance SHA (`cherry picked from` / `upstream commit`) is reachable in
   `G_new` → **UPSTREAMED**.
3. subject matches a `G_new` commit but diff differs → **MODIFIED-UPSTREAM**
   (the hard case; go to Phase 3).
4. provenance points only at a vendor tree (CodeLinaro/qclinux) → **VENDOR-ONLY**.
5. no provenance at all → **DERIVATIVE-ONLY**.
6. `UBUNTU:` packaging → **PACKAGING** (own track).

Deterministic matchers (prefix, trailers, patch-id, subject) settle ~70-80%.
The residue is handed to **per-commit semantic classification** (one AI agent per
commit, fan-out): given the derivative diff and the candidate upstream diff,
classify the delta as *cosmetic/context-only* (drop, take upstream),
*functional OEM addition* (preserve as delta), or *backport adaptation* (preserve).

### Phase 3 — Modified-upstream reconciliation (hunk transplant)
The naive approach is a whole-file 3-way merge with `base`=derivative pre-patch,
`theirs`=upstream form. That fails in practice: the two sides sit on *different
kernel versions*, so all the intervening drift is reported as conflict.

Instead, transplant **only the derivative's own hunks** onto the upstream file,
matching by local context (GNU `patch` with fuzz). Version drift is then invisible;
only hunks that genuinely cannot be placed are reported. Each rejected hunk is
**subsumption-tested** — if everything it adds is already in the upstream file, the
change already landed and the hunk is dropped rather than flagged.

Outcomes per file: *applied* (emit a `.delta.patch` to apply on top of upstream),
*subsumed* (nothing left to carry ⇒ the commit is redundant, DROP), or *conflict*
(emit a `.rej` with just the genuinely divergent hunks).

Measured effect vs. the whole-file merge: conflict hunks **144 → 67 (-54%)**,
auto-resolved commits **2 → 12** of 25.

### Phase 4 — Feature clustering
Two commits touching the same file cannot sit on independent branches, so the
maximal independently-mergeable sets are the **connected components of the
"shares a file" relation** (plus `Change-Id` series coupling).

**Hub files** — `defconfig`, `dts/.../Makefile`, `changelog` — are touched by
hundreds of unrelated features; coupling on them collapses everything into one
component. Files touched by `>= hub_threshold` commits are therefore excluded from
the relation and reported as the *integration surface*: merged once, centrally.

The correctness invariant to assert is that **no non-hub file spans two clusters**;
if it holds, each cluster is provably mergeable in isolation.

### Phase 5 — Reconstruction
Each cluster becomes a topic branch (`rederive/cNNNN`) cut from the replay base and
cherry-picked in original order, in a **scratch `git worktree`** so the derivative's
checkout and branches are never disturbed. Clean/conflicted status is recorded per
commit.

This is also where Phase 4's claim becomes falsifiable. If clusters really are
independent, then replaying one in isolation can only conflict on the shared
integration surface:

> **Prediction:** every conflict lands on a hub file.

A conflict on a *non-hub* file means a predecessor was missing, and the tool
attributes it automatically by looking for a dropped commit that touched the same
file earlier. On the pilot this immediately exposed a real defect — see below.

### Phase 6 — Equivalence verification (acceptance gate)
Three mechanical checks; the run is `ACCEPTED` only if all pass.

1. **Disposition partition.** Every OEM commit is kept-and-clustered or dropped with
   exactly one recorded reason. The counts must sum to the corpus size.
2. **Drop soundness.** *A drop is sound only if the replay base already contains the
   content.* Each dropped commit's hunks are tested for subsumption (Phase 3's test)
   against the **base** blob. Anything not subsumed would silently change the tree.
3. **File coverage.** No file may lose all of its OEM content. Files whose only
   commits were churn-cancelled are *correctly* empty (proven net-zero); any other
   fully-dropped file is a regression.

Check 2 is the one that matters, because it is what disciplines the AI. A model
asked "is this already upstream?" answers against the *reference* tree, but the
drop is only legal against the *replay base*. On the pilot, **26 of 27** semantic
drops were sound against linux-next yet unsound against the 6.8 base. The gate
caught all 26; `verify --revoke-unsound` flips them back to KEEP, and re-running
`cluster` + `verify` converges to `ACCEPTED`.

This is the net-tree invariant doing its job: the classifier is allowed to be
wrong, because nothing it says is trusted without mechanical proof.

## Why this is tractable
- Phases 0,1,2(det),6 are plain scripting over git + SQLite.
- Phases 2(residue) and 3 are per-commit and embarrassingly parallel — a natural
  AI fan-out, each agent handed two diffs and a narrow question.
- Correctness never rests on the model: the net-tree invariant is the backstop.

## Rollout
Pilot on one real rebase of noble `linux-qcom`. Metrics: conflicting commits
before/after, commits dropped as redundant, feature sets produced, tree-identity
pass/fail. Layer semantic reconciliation in only after the deterministic inventory
is trusted.

## Prototype
`rederive.py` implements all six phases. See `README.md` for measured results.

| subcommand | phase | role |
|---|---|---|
| `extract` | 0 | OEM commits + file lists + provenance → SQLite |
| `churn` | 1 | collapse revert/re-add chains and duplicates (three-tier proof: patch-id, line composition, blob equality) |
| `classify` | 2 | deterministic cross-match against the generic tree |
| `graduate` | 2a | detect patches that landed in the *new* generic base (identical, modified, or reverted) |
| `packets` / `ingest` | 2 | build per-commit analysis packets, fan out to lightweight agents, load verdicts |
| `reconcile` | 3 | hunk transplant onto the upstream form + subsumption test |
| `cluster` | 4 | connected components → independently-mergeable feature sets |
| `reconstruct` | 5 | replay clusters as topic branches in a scratch worktree |
| `verify` | 6 | acceptance gate: partition, drop soundness, file coverage |
| `report` | — | inventory summary |

The pilot ends `ACCEPTED` only after the gate forced 26 unsound AI drops to be
revoked — which is the intended behaviour, not a workaround.

## Phase 1 addendum: proving cancellation against the tree, not the diff

Real-world validation (README, 1090.95 → 1091.96) falsified the diff-level
proof. Patch-id identity and line-composition both answer "do these two
*patches* compose to nothing?". The question that actually matters is "did this
pair leave anything in the *tree*?".

`pair_leaves_no_trace` compares `blob(A^, f)` with `blob(R, f)` for every file
the pair touches. It subsumes the earlier tests, needs no tolerance for
whitespace, renames or copyright drift, and rejected 21 of 42 pairs that both
diff-level tests had accepted. The ordering in `cmd_churn` is therefore:

1. patch-id identity — cheap, exact, rare
2. line composition — tolerates conflict-resolution scar tissue
3. **blob equality at the endpoints** — the authoritative test
4. straddle guard — a conflict-cost heuristic, not a correctness test

Steps 1–3 are correctness; step 4 only avoids stranding commits that were
authored against the intermediate state.

### A limit worth stating plainly

Blob equality proves a pair is inert *on the base it lived on*. Replaying onto
a **different** base makes every cherry-pick a fresh three-way merge, so
removing an inert pair can change whether a later commit merges cleanly — in
either direction. Measured effect on the pilot cycle: 30 conflicts → 29.
Dropping inert pairs is semantically safe but not conflict-monotonic.

