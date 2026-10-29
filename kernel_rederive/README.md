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

## Corroboration: the 146 → 148 rebase that broke Monza

A previous session (`a0b0e9da-f6bd-4946-a92c-25f5add7d703`, 2026-10-12 → 10-19)
performed this exact base bump by hand. Its artefacts are still in the tree and
confirm the `graduate` rule the hard way.

| commit | release | generic base | `arch/arm64/mm/mmu.c` |
|---|---|---|---|
| `f834b833dffa` | 1090.95 (published) | 146.146 | differs from base — correct, 146 has no revert |
| `55063e907cf4` | 1090.95 (that session's rebase) | 148.148 | **differs from base — did not boot** |
| `b4d222f7e299` | `master-next-rebase-backup`, after the fix | 148.148 | matches base ✓ |
| `820e9992e752` | 1091.96 (published) | 148.148 | matches base ✓ |

Generic landed `88bdf35f09e2 Revert "arm64: mm: Don't remap pgtables for
allocate vs populate"` inside the 146→148 window. The derivative carried its own
`UPSTREAM:` copy of the reverted patch, which **applied cleanly** — no conflict,
no empty commit, nothing for `git rebase` to flag. The result silently dropped
two `*_set_fixmap_offset()` assignments and the kernel died in early assembly,
emitting zero bytes on serial.

Diagnosis took seven days of serial-console work on Monza (firmware traces,
`earlycon`, `nokaslr`, `efi=novamap`, no-initrd, config diffs, pstore — all
negative, because the failure precedes any console). The fix was two lines.

`graduate`'s `REVIEW_REVERTED_IN_GENERIC` rule flags that commit mechanically,
from metadata alone, before a single cherry-pick runs. Both the human fix and
the published 1091.96 reached the same resolution the rule points at.

This is the case for the whole approach: a clean apply is not evidence of
correctness. The dangerous commits are the ones that *don't* conflict.

### Known gap, from the same evidence

Of the 8 commits that session dropped, `graduate` detects 4, git's own empty
detection handles 1, 2 were renamed upstream (semantic fan-out territory), and
one — `arm64: mm: Don't remap pgtables per-cont(pte|pmd) block` — is invisible
because its generic counterpart `7c79ff71c122` was **already in `G_old`**, not
in the `G_old..G_new` window `graduate` scans. Duplicates already present in the
current base are now found by `basedup` (see *Blind mode* below).

## Blind mode: rederive at release time, rebase later

Everything above used `graduate`, which reads `G_old..G_new` — it knows what the
next base contains. At release time nobody does. Blind mode rederives against
the **current** base only, and leaves all knowledge of the next base to a
separate `rebase` step.

**Fairness rule (enforced by the CLI):** `anchor`, `similar`, `basedup` and
`build` take only `G_old`, `D_old` and the upstream reference tree. `rebase` is
the only subcommand that accepts `--onto G_new`. `graduate` is relabelled
*audit only*.

```bash
T=/tmp/rd-blind            # git clone --shared of the OEM repo; HEAD gets moved
REF=~/canonical/linux      # upstream: origin/master + linux-next
DB="--db /tmp/blind.db"
G_OLD=bd915784e717; D_OLD=f834b833dffa

python3 rederive.py $DB extract  --tree $T --base $G_OLD --head $D_OLD --reset
python3 rederive.py $DB churn    --tree $T --base $G_OLD --head $D_OLD
python3 rederive.py $DB classify --generic $T --generic-ref $G_OLD
python3 rederive.py $DB anchor   --tree $T --base $G_OLD --head $D_OLD --ref-tree $REF  # upstream identities
python3 rederive.py $DB basedup  --tree $T --base $G_OLD --since e8f897f4afef  # v6.8; dupes already in base
python3 rederive.py $DB similar  --tree $T --ref-tree $REF --out /tmp/similar   # content match
#   fan-out: one lightweight agent per ~12 packets -> JSON lines
python3 rederive.py $DB ingest-similar /tmp/verdicts_*.jsonl
python3 rederive.py $DB build    --tree $T --base $G_OLD --head $D_OLD --branch rederived

# later, when the next base exists
python3 rederive.py $DB rebase --tree $T --base $G_OLD --onto $G_NEW --branch rederived \
        --ref-tree $REF --log rebase.log            # add --plain for a pure git replay
```

### The blind phases

| command | what it finds | can it drop? |
|---|---|---|
| `anchor` | per OEM commit, the identities a future base could carry it under: body-cited upstream SHA (existing upstream), Change-Id, exact subject | no — metadata |
| `similar` | upstream form of *unanchored* commits by content: fingerprints the diff's significant lines, inverted-index against `v6.8..linux-next` on the same paths. ≥60 % line coverage auto-anchors; 25–60 % becomes a fan-out packet | no — metadata |
| `ingest-similar` | lightweight-agent verdicts (`SAME_CHANGE`/`RELATED`/`UNRELATED`) → `up` anchors | no — metadata |
| `basedup` | OEM commits duplicating something **already in `G_old`** (by cited SHA, subject, patch-id) — the gap the Monza post-mortem exposed | candidate only |
| `build` | replays the keep-set onto `G_old`; every drop is a candidate until the replay **proves** it (below) | yes, proven |
| `rebase` | the only step that sees `G_new`; uses anchors to skip/flag | skip, with backtracking |

**`build` = proof by replay.** Drop candidates are churn `DROP*`, `basedup`
and `UPSTREAMED`. The keep-set is replayed onto `G_old` with `cherry-pick -n`.
On a conflict, every dropped candidate that touches a conflicted file (and
precedes the conflict) is restored and the replay restarts from the first unit
whose content changed (units are compared, so the restart is exact). At the
end the tree is compared with `D_old`; dropped candidates touching any
non-cosmetic differing file are restored the same way. Fixpoint = **zero
conflicts on `G_old` and tree == `D_old`**. Every commit gets a
`Rederived-from: <orig sha>` trailer, so anchors survive the rewrite.

**Fix-on-fix squash.** An OEM-only commit (no anchor, no `UPSTREAM`/`BACKPORT`/
`FROMGIT`/`FROMLIST` tag, not basedup, not a revert, not packaging) joins its
predecessor's group when (a) every file it touches was last touched by that
group — so it commutes back across file-disjoint commits exactly — and (b) at
least half its removed lines are lines the group itself added. Members are
recorded as `Squashed-in:` trailers. A squash that conflicts on replay is
undone automatically.

**`rebase` rules (assisted mode).** Indexes `G_old..G_new` for cited upstream
SHAs, clean subjects (packaging subjects excluded), patch-ids and reverts, and
resolves each rederived commit's anchors through its `Rederived-from` trailers:

- anchor or subject **reverted** in the new base → drop + flag (the Monza rule);
- patch-id present in the new base → skip;
- anchor cited / subject present in the new base → *TRY*: clean apply is kept
  and flagged, a conflict means "graduated in another form" → provisional skip.
- **Backtracking:** a provisional skip stands only while no later commit
  conflicts on a file the skipped commit touched. If one does, the skip broke a
  series: rewind to just before it, apply it (flagged for review), replay on.

Remaining conflicts use the same `--theirs` harness as the control, so arms are
directly comparable.

### Result: 146 → 148, blind

`D_old` = 1090.95 on 146; rederived with no knowledge of 148; then rebased onto
148 (`70e5091c210e`) and compared with the maintainer's rebase (`cf64eb717498`).

Rederive at 146: 3,647 → **3,607** commits (39 proven drops: 34 churn, 5
basedup; 20 drop candidates restored by the proof; 1 emptied). With squash:
**3,452** (148 commits in 102 fix-on-fix groups). Tree == `D_old` except one
copyright-year line in `ath11k/hal_rx.c`. `similar`: 38 auto-anchors, 92
packets → 8 lightweight agents → 51 `SAME_CHANGE`.

| arm | commits | conflicts | files ≠ target | `mmu.c` == 148 | maintainer drops recovered |
|---|---|---|---|---|---|
| control (original stack, plain) | 3,647 | 30 | 33 | **no — Monza boot-breaker** | 2/11 |
| lookahead arm E (previous section) | 3,592 | 29 | 39 | — | 7/11 |
| blind, plain git | 3,607 | 29 | 33 | **yes** | 3/11 |
| blind + squash, plain git | 3,452 | 29 | 33 | **yes** | 3/11 |
| blind, assisted `rebase` | 3,607 | **27** | 31 | **yes** | **7/11** |
| blind + squash, assisted `rebase` | 3,452 | **27** | 31 | **yes** | **7/11** |

What made the difference:

- **The Monza boot-breaker is gone without lookahead.** `basedup` found both
  `arm64: mm` commits duplicating generic commits already in 146; `build`
  proved them droppable (tree unchanged at 146). When 148's revert arrives,
  nothing re-applies the reverted code. The control replays it cleanly and
  silently produces a non-booting `mmu.c`.
- **Content matching found a renamed graduation.** bt-sco
  (`fix duplicate debugfs entries for bt-sco-pcm-wb`) has no shared subject,
  SHA or patch-id with its upstream form; `similar` + a lightweight agent
  anchored it to `0b604e886ece`, and `rebase` skipped it when 148 cited it.
- **Backtracking matters.** Without it, the assisted arm skipped two FROMLIST
  commits (`qcom_scm: Rename peripheral as pas_id`, `remoteproc: pas: Replace
  metadata context`) whose series then conflicted 4 times — net 29. With it: 27.
  The maintainer did drop the `pas_id` one; the tool applies it and **flags**
  it rather than guess.

Honest findings:

- **Squash has no effect on conflicts** (identical conflict sets, 29/29 and
  27/27). This is expected: a squash only joins commits that commute across
  file-disjoint neighbours, and commuting file-disjoint patches cannot change
  any per-file three-way merge. It reduces the stack by 148 commits and
  removes intermediate states a maintainer would otherwise resolve; that is
  the whole benefit. **Layered reordering was therefore not built** — for a
  plain rebase it is cosmetic by the same argument.
- **Still missed blind:** `ARM: dt: add extended SPI specifier` (a few
  `#define` lines — below the fingerprint threshold; landed renamed as ESPI/EPPI),
  `replace WLAN_EN pinctrl with gpio-hog`, and `stmmac 10GBASER` (the
  maintainer found it empty after resolving earlier conflicts by hand).
- **The proof is conservative on hub files.** Six differing
  `Documentation/devicetree/bindings/arm/qcom.yaml`-style files restored 15
  candidates at once; finer restoration (bisecting the candidate set) would
  keep more drops.
- Remaining 27 conflicts are real collisions between OEM work and 148's
  backports. Files differing from the target are almost all conflict sites
  (26 of 31), where `--theirs` is not the maintainer's resolution.
- Cost: `similar` ~4 min; `build` ~25 min (4 replay rounds); each `rebase` ~7 min.
