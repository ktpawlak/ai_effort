# kernel_rederive

Tooling + methodology to "re-derive" an Ubuntu kernel derivative: turn a large,
conflict-prone OEM patch stack into a classified inventory and (future work) a set
of clean, independently-mergeable feature branches that reproduce the same tree.

- **`DESIGN.md`** — the full methodology (6 phases + governing invariant).
- **`rederive.py`** — runnable prototype: Phase 0 (extraction) + deterministic
  Phase 2 (classification) → a SQLite `inventory.db` + summary report.

## Quickstart

```bash
# Phase 0 — extract the OEM stack (derivative commits on top of generic base)
./rederive.py extract \
    --tree ~/canonical/kernel/ubuntu/noble/linux-qcom/linux-main \
    --base "UBUNTU: Ubuntu-6.8.0-148.148" --reset

# Phase 2 — deterministic classification (cross-match against a generic tree)
./rederive.py classify --generic ~/canonical/linux--noble --generic-ref HEAD

# Inventory report
./rederive.py report
```

`--base` accepts a ref/tag or a commit-subject substring. The derivative's generic
base is in its `debian.qcom/changelog` top entry (`[ Ubuntu: 6.8.0-148.148 ]`).

## Result on noble linux-qcom 6.8.0-1091.96 (base 148.148)

3,638 OEM commits classified:

| category                     | meaning                                           |
|------------------------------|---------------------------------------------------|
| `UPSTREAMED`                 | provenance SHA already in generic → **drop candidate** |
| `UPSTREAM_PROVENANCE`        | clean upstream SHA; will graduate / modified-upstream risk |
| `MODIFIED_UPSTREAM_CANDIDATE`| `FROMLIST:` preliminary → **flag for semantic review** (Phase 3) |
| `VENDOR_ONLY`                | CodeLinaro/qclinux cherry-pick; OEM-only          |
| `PACKAGING`                  | `UBUNTU:` packaging / config / ABI                |
| `DERIVATIVE_ONLY`            | `PENDING:` / no provenance                        |
| `REVERT_NOISE`               | revert; churn-cancellation candidate (Phase 1)    |

## Inventory DB

`inventory.db` (SQLite, git-ignored) has `commits` (one row per OEM commit, with
provenance + classification) and `commit_files`. Query freely, e.g.:

```sql
SELECT category, COUNT(*) FROM commits GROUP BY category;
SELECT sha, subject FROM commits WHERE category='MODIFIED_UPSTREAM_CANDIDATE';
```

## Pipeline status

All six phases are implemented. Run them in order:

```bash
T=~/canonical/kernel/ubuntu/noble/linux-qcom/linux-main
B="UBUNTU: Ubuntu-6.8.0-148.148"

python3 rederive.py extract  --tree $T --base "$B"
python3 rederive.py churn    --tree $T --base "$B"      # Phase 1
python3 rederive.py classify --tree $T --generic ~/canonical/linux--noble
#   ... Phase 2 semantic fan-out (packets / ingest), Phase 3 reconcile ...
python3 rederive.py cluster                             # Phase 4
python3 rederive.py reconstruct --tree $T --base "$B" --top 3   # Phase 5
python3 rederive.py verify   --tree $T --base "$B"      # Phase 6 gate
```

## Phase 1 churn cancellation (implemented)

```bash
python3 rederive.py churn --tree $T --base "$B"
```

Collapses add/revert/re-add chains and duplicate applications. A pair only
cancels if it is proven to compose to nothing — first by patch-id identity, then
by a **content composition test** over the four line multisets of the two diffs,
which is immune to filenames, whitespace and hunk structure.

| outcome | count |
|---|---|
| revert pairs inside the OEM stack | 42 |
| → `DROP_NET_ZERO` | 33 pairs (66 commits) |
| → `DROP_SUPERSEDED` (add + revert + re-add) | 5 pairs (9 commits) |
| → `KEEP_PARTIAL_REVERT` (real functional residue) | 4 |
| `DROP_DUPLICATE` (same patch-id applied twice) | 5 |
| reverts of *generic-base* commits (always kept) | 25 |
| **total removed as pure churn** | **80** |

### Why a revert is usually not a byte-exact inverse

11 of the 42 pairs failed the patch-id test. All but 4 still cancel — the
asymmetry is a **leftover from a previous conflict resolution**, in three forms:

- **file renamed in between** — `13502a6dacb4` removes the exact nodes
  `dbdbda09c0c3` added, but from `qcs6490-rb3gen2.dts` instead of `qcm6490-rb3.dts`.
- **cosmetic drift** — `914d474ae3ea` bumps copyright `2023`→`2023-2024` and sets
  `DMA_RX_BUF_SIZE 4096`; its revert restores `2048` exactly but bumps the
  copyright to `2023-2025` instead of back to `2023`.
- **whitespace / hunk restructuring** — identical content, different patch-id.

Only the remaining **4** are genuine partial reverts, where the revert removes
lines the original never added because the original was *reworked* in between.

Cancelling a cosmetic-residue pair makes the rebuilt tree differ from `D` by
exactly those comment lines. `churn` prints them; `--strict-revert` disables the
tolerance and keeps those pairs instead.

## Phase 5 reconstruction (implemented)

```bash
python3 rederive.py reconstruct --tree $T --base "$B" --top 3
```

Builds one topic branch per cluster in a scratch `git worktree` (the derivative
checkout and its branches are never touched; branches are deleted afterwards
unless `--keep-branches`).

Replaying the 3 largest clusters (457 commits) onto the 6.8 base:

| | |
|---|---|
| clean cherry-picks | 427 (93%) |
| conflicted | 30 |
| conflicts confined to **hub** files | 29 |
| conflicts touching **non-hub** files | 1 |

29/30 conflicts land on the shared integration surface, as Phase 4 predicts.
Non-hub conflicts are auto-attributed to the dropped commit that caused them.

## Phase 6 acceptance gate (implemented)

```bash
python3 rederive.py verify --tree $T --base "$B"
```

| check | result |
|---|---|
| partition complete (2984 kept + 80 churn + 1 semantic + 559 packaging + 14 upstreamed = 3638) | PASS |
| all drops sound against the replay base | PASS |
| file coverage (no file loses all OEM content) | PASS |

### What the gate caught

The first run **failed**: 26 of 27 semantic DROP verdicts were unsound. The
lightweight agents judged "already upstream" against *linux-next*, but the drop
is only legal against the *replay base* (6.8), which does not carry those
patches. Ten of them would have deleted whole new files (`dispcc-qcs615.c`, the
`qcs615-*` bindings).

Phase 5 had independently flagged the same defect from the other direction: two
non-hub conflicts were traced to dropped predecessors.

```bash
python3 rederive.py verify --tree $T --base "$B" --revoke-unsound
python3 rederive.py cluster
python3 rederive.py verify --tree $T --base "$B"     # -> ACCEPTED
```

After revocation the keep set grows, non-hub conflicts fall 3 → 1,
and the gate passes. **No AI verdict is trusted without mechanical proof.**

## Phase 4 feature clustering (implemented)

Groups the keep-set into **independently-mergeable** feature sets:

```bash
./rederive.py cluster --hub-threshold 15 --top 25
```

**Formalism.** Two commits that touch the same file cannot sit on independent
branches without risking a conflict, so the maximal independently-mergeable sets
are exactly the *connected components* of the "shares a file" relation (commits
sharing a `Change-Id` series are also coupled).

**Hub files.** Integration points like `qcom_defconfig`, `dts/qcom/Makefile`, and
`debian.qcom/changelog` are touched by hundreds of unrelated features. Coupling on
them collapses everything into one blob, so files touched by `>= --hub-threshold`
commits are excluded from the relation and reported as the *integration surface*
to be merged once, centrally.

Result on the 2,984 keep-set commits (79 hub files at threshold 15):

- **1,613 independently-mergeable clusters** (1,458 standalone commits)
- largest cluster only **217 commits (7%)** — no giant blob
- clusters are semantically coherent, e.g. cluster 16 is entirely `Bluetooth: qca:`

**Verified invariant** (asserted by `cluster` on every run): 0 of 2,005 non-hub
files span more than one cluster — i.e. every cluster provably merges without
touching another cluster's files. Phase 5 then confirms this empirically: 29 of
30 replay conflicts land on hub files.

The threshold is the central tuning knob (fewer hubs = less central integration
work, but larger / less independent clusters):

| `--hub-threshold` | hub files | clusters | largest cluster |
|---|---|---|---|
| 5  | 287 | 2412 | 20   |
| 10 | 132 | 1919 | 109  |
| 15 (default) | 79 | 1613 | 217 |
| 25 | 31  | 1189 | 451  |
| 40 | 10  | 750  | 1034 |

Cluster assignment is stored in `commits.cluster_id`:

```sql
SELECT sha, subject FROM commits WHERE cluster_id = 16;
```

## Phase 3 three-way reconciliation (implemented)

For each `KEEP_DELTA` commit, re-express the OEM change as a delta that applies
cleanly on top of the upstream form:

```bash
./rederive.py reconcile \
    --tree ~/canonical/kernel/ubuntu/noble/linux-qcom/linux-main \
    --ref-tree ~/canonical/linux --out reconcile
```

Per touched file it transplants **only the derivative's own hunks** onto the
upstream file (GNU `patch` with fuzz, matching by local context). This is the key
refinement over a whole-file 3-way merge: the derivative base (6.8) and the
upstream form (linux-next 7.x) sit on different kernels, so a whole-file merge
reports all the intervening version drift as conflict. Hunk transplant ignores
drift entirely and only reports hunks that genuinely cannot be placed.

Each rejected hunk is then **subsumption-tested**: if everything the hunk adds is
already present in the upstream file, the change already landed and the hunk is
dropped rather than reported as a conflict.

Outputs per commit under `reconcile/<sha>/`:
- `<path>.delta.patch` — the patch to apply **on top of** upstream to re-add the
  OEM delta (the re-derive output);
- `<path>.rej` — only genuinely conflicting hunks, for manual reconciliation.

Result on the 25 `KEEP_DELTA` commits:

| outcome | n | meaning |
|---|---|---|
| auto-mergeable | 10 | OEM delta applies cleanly over upstream → ready-made commit |
| fully subsumed | 2  | every hunk already upstream → **redundant, DROP** |
| needs manual reconciliation | 13 | real divergence; `.rej` pinpoints the hunks |

Hunk-level: **259 total → 57 applied, 135 already-upstream, 67 genuine conflicts.**
Over half of all hunks were already upstream. Versus the naive whole-file 3-way
merge this cut conflict hunks from **144 → 67 (-54%)** and raised auto-resolved
commits from 2 → 12.

Note the `subsumed` verdict mechanically overrides the Phase 2 LLM verdict: e.g.
`b2d2f6769f11` was called `OEM_DELTA`, but its only change is a copyright bump
already present upstream (and upstream additionally fixes a second file the
derivative misses), so taking upstream is strictly better.

## Phase 2 semantic fan-out (implemented)

The deterministic pass flags ~270 commits (`MODIFIED_UPSTREAM_CANDIDATE` +
`UPSTREAM_PROVENANCE`) that need a content comparison. These are handed to a
**fan-out of lightweight agents** — one cheap model per batch, each comparing the
derivative patch against its upstream form and emitting a verdict.

```bash
# 1. Build one self-contained analysis packet per flagged commit.
#    --ref-tree locates the upstream form (by provenance SHA, Change-Id, subject).
./rederive.py packets \
    --tree ~/canonical/kernel/ubuntu/noble/linux-qcom/linux-main \
    --ref-tree ~/canonical/linux --ref-rev linux-next/master \
    --categories MODIFIED_UPSTREAM_CANDIDATE --out packets

# 2. Split packets into batches and dispatch one lightweight agent per batch.
#    Each agent reads its packet files and writes results/batch_NN.jsonl lines:
#    {"sha","verdict","action","reason"}
grep '\.txt$' packets/manifest.txt | split -n l/8 -d - batches/batch_

# 3. Ingest the agents' verdicts back into the inventory.
./rederive.py ingest results/batch_*.jsonl
./rederive.py report
```

Verdicts: `IDENTICAL`/`COSMETIC_ONLY` → **DROP** (redundant once upstream lands);
`OEM_DELTA` → **KEEP_DELTA** (reconcile in Phase 3); `NO_UPSTREAM` → **KEEP**.

**Reference tree matters.** Match rate depends entirely on it. Index the richest
available tip — `~/canonical/linux` with the `linux-next` remote, via
`--ref-rev linux-next/master` (a superset of mainline + all subsystem -next trees).
Packets whose upstream form isn't located are short-circuited to `NO_UPSTREAM/KEEP`
deterministically; only the matched packets are worth a fan-out agent.

Result on noble `linux-qcom` 6.8.0-1091.96, 233 `FROMLIST` candidates vs
`linux-next/master` (20261028):

| verdict | action | n | meaning |
|---|---|---|---|
| NO_UPSTREAM  | KEEP       | 181 | upstream form not located (newer-kernel targets) |
| OEM_DELTA    | KEEP_DELTA |  25 | derivative adds function beyond upstream → Phase 3 |
| COSMETIC_ONLY| DROP       |  19 | only context/whitespace/backport differs → **redundant** |
| IDENTICAL    | DROP       |   8 | same change → **redundant** |

The **27 DROP** commits are exactly the modified-upstream collisions that cause
rebase conflicts today; dropping them removes those conflict points outright.

**Robustness.** `ingest` matches verdicts on the 12-char sha prefix — lightweight
models occasionally mis-transcribe the full 40-char sha (observed once in 52).

## Real-world validation: the 1090.95 → 1091.96 qcom cycle

The pipeline was tested end-to-end against a rebase Canonical actually
performed, so every number below has a ground truth to be checked against.

| role | ref | sha |
|---|---|---|
| `D_old` | `Ubuntu-qcom-6.8.0-1090.95` | `f834b833dffa` |
| `G_old` | `Ubuntu-6.8.0-146.146` | `bd915784e717` |
| `G_new` | `Ubuntu-6.8.0-148.148` | `70e5091c210e` |
| target  | `D_new^^` — 1090.95 as the maintainer rebased it | `cf64eb717498` |

All work ran in `git clone --shared` copies. A `git worktree` would have
written refs into the OEM repository; `--shared` writes nothing to it.

### What the maintainer did

The cycle is nearly a pure rebase: 2 packaging commits added, **11 OEM patches
dropped** because they reached the generic base. Of 3,647 OEM commits, **only 4
share a patch-id with a generic 146→148 commit** — so `git rebase` can skip 4
and must collide with the rest. That ratio is the whole problem in one number.

### Detection (`graduate`, Phase 2a)

| mechanism | n | notes |
|---|---|---|
| identical patch-id → `DROP_GRADUATED` | 4 | git rebase already skips these |
| same clean subject, different content → `REVIEW_GRADUATED_MODIFIED` | 2 | both `FROMLIST:` — the exact failure mode this project targets |
| generic *reverted* the subject → `REVIEW_REVERTED_IN_GENERIC` | 1 | carrying it forward re-applies something generic rejected |

**7 of the 11** maintainer drops are recovered mechanically. The other 4 were
renamed on their way upstream (`fix duplicate debugfs entries for bt-sco-pcm-wb`
landed as `fix duplicate DAPM widget names for wideband DAI`; `ARM: dt: add
extended SPI specifier` landed as `dt-bindings: ... add ESPI and EPPI
specifiers`). No subject or patch-id test can see those — they are precisely
the residue the Phase 2 semantic fan-out exists for.

### Measured rebase onto `G_new`

Four arms, identical replay harness (cherry-pick, `--theirs` auto-resolution):

| arm | drops | commits | conflicts |
|---|---|---|---|
| A control | none | 3,647 | **30** |
| B | graduated + upstreamed only | 3,629 | 30 |
| C | + churn, no straddle/tree guard | 3,549 | **33** ✗ |
| D | + straddle guard | 3,588 | 29 |
| E | + tree proof | 3,592 | **29** |

### The negative result that mattered

Arm C made things **worse**. All 5 of its introduced conflicts were attributable
to dropped churn commits touching the same files: an add/revert pair is net-zero
*at its endpoints*, but commits landing in between were authored against the
post-add tree, so removing the add strands them.

This produced the strongest check in the tool, `pair_leaves_no_trace`: compare
the **blobs**, not the diffs. If every file the pair touches has an identical
blob at `A^` and at `R`, the pair provably contributed nothing — whatever
happened in between. It rejected **21 of 42** pairs that the patch-id and
line-composition tests had both accepted.

### Honest limits

Arm E leaves 39 files differing from the target; **27 are conflict sites**,
where `--theirs` simply isn't the human's resolution. Of the 12 remaining, 5 are
shared with the control (the 4 undetected maintainer drops) and 7 cascade from a
single differently-resolved stmmac conflict.

An exact tree match is not attainable here, and the experiment was never capable
of producing one: the control — which replays *every* original commit — already
differs from the target in 33 files for the same reason. What is measurable is
the conflict count, and on that the pipeline is a modest win (30 → 29) while
removing 55 commits.

The subtler finding: a pair that is provably inert *on its original base* can
still perturb a downstream three-way merge when replayed onto a **different**
base, in either direction. Dropping inert pairs is semantically safe; it is not
guaranteed to be conflict-monotonic.
