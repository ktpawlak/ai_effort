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

Collapses add/revert/re-add chains and duplicate applications. A revert is only
treated as cancelling when it is the **exact inverse** of its target, proven by
comparing the reversed patch-id — subject matching alone is not enough.

| outcome | count |
|---|---|
| revert pairs inside the OEM stack | 42 |
| → `DROP_NET_ZERO` (add + exact revert) | 26 pairs (52 commits) |
| → `DROP_SUPERSEDED` (add + revert + re-add) | 5 pairs (9 commits) |
| → `KEEP_PARTIAL_REVERT` (**not** an exact inverse) | 11 |
| `DROP_DUPLICATE` (same patch-id applied twice) | 5 |
| reverts of *generic-base* commits (always kept) | 25 |
| **total removed as pure churn** | **66** |

The inverse-patch-id proof rejected **11 of 42** apparent revert pairs; naive
subject matching would have wrongly dropped 22 commits. Example: the revert of
`PENDING: Bluetooth: hci_sync: Fix disconnect complete event` removes 8 lines
from `hci_conn.c` where the original added 9 — it leaves residue.

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
| partition complete (2998 kept + 66 churn + 1 semantic + 559 packaging + 14 upstreamed = 3638) | PASS |
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

After revocation the keep set grows 2973 → 2998, non-hub conflicts fall 3 → 1,
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

Result on the 2,998 keep-set commits (80 hub files at threshold 15):

- **1,627 independently-mergeable clusters** (1,472 standalone commits)
- largest cluster only **217 commits (7%)** — no giant blob
- clusters are semantically coherent, e.g. cluster 16 is entirely `Bluetooth: qca:`

**Verified invariant** (asserted by `cluster` on every run): 0 of 2,004 non-hub
files span more than one cluster — i.e. every cluster provably merges without
touching another cluster's files. Phase 5 then confirms this empirically: 29 of
30 replay conflicts land on hub files.

The threshold is the central tuning knob (fewer hubs = less central integration
work, but larger / less independent clusters):

| `--hub-threshold` | hub files | clusters | largest cluster |
|---|---|---|---|
| 5  | 287 | 2423 | 20   |
| 10 | 132 | 1926 | 109  |
| 15 (default) | 80 | 1627 | 217 |
| 25 | 32  | 1215 | 451  |
| 40 | 10  | 750  | 1037 |

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
