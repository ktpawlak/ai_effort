# Scripts used in the November patchset rebase

I recovered these from the session's event log; the originals lived in `/tmp`
and were deleted. Run all of them from the kernel tree
(`~/qualcomm/resolute/linux-qcom/linux-main`). Most of them hard-code `/tmp`
paths and the branch names and SHAs of this session. They are a record of what
was done and a template for the next rebase. They aren't a general-purpose
tool; for that, see `../../kernel_rederive/`.

Rebases are driven like this:

```bash
GIT_SEQUENCE_EDITOR='python3 <seqeditor>.py' GIT_EDITOR=true \
  git -c rebase.abbreviateCommands=false rebase -i --empty=ask Ubuntu-7.0.0-44.44
```

(`--empty=stop` isn't supported by the installed git, so these runs use `--empty=ask`.)

## phase2_collapse_incoming: `master-next` → `qcom-collapsed`

| File | Purpose |
|------|---------|
| `pair.py`, `pair2.py` | Pair `Revert "X"` with the latest earlier unpaired commit whose subject is `X`. Report which pairs belong to the new drop (the revert isn't in the pre-port `master-next` `f54e8d58bc85`). Writes `/tmp/pairs2.json`. |
| `seqedit.py` | Sequence editor: turns `pick` into `drop` for every SHA listed in `/tmp/drop.txt`. |
| `drive.sh` | Rebase driver loop. When the only conflicts are modify/delete with the file deleted in HEAD, it runs `git rm` on them; when a commit comes out empty, it runs `rebase --skip`. Any other conflict stops the loop. Each action is logged to `/tmp/auto_resolved.txt`. |

## phase3_collapse_all: `qcom-collapsed` → `qcom-collapsed-full`

| File | Purpose |
|------|---------|
| `pair3.py` | Generic pairing over the whole derivative; handles nested `Revert "Revert ..."` parity. Produced the 69 subject pairs (`/tmp/drop2.txt`). |
| `inv.py` | For unmatched reverts, it finds the combination of earlier commits whose combined patch is the exact inverse of the revert. This is how the tsens pair (truncated subject) and the two squashed lpass reverts (4 and 3 commits) were found. |
| `mkgroups.py` | Builds `/tmp/groups.json`, the 72 groups (adds plus revert) with their position and end. |
| `seqedit_droponly.py` | First attempt: a sequence editor that only drops commits. |
| `seqedit.py` | Final sequence editor. It drops the group members and adds `exec python3 /tmp/residual.py <rev>` at each revert's position. |
| `residual.py` | At a revert's position, sets the pair's files back to their state in the original tree right after the revert, skipping files that another open pair covers. If anything differs, it commits a reduced revert under the original author with a "Collapsed history" note. |
| `drive.py` | Conflict driver. It only auto-resolves inside open-pair ranges: `git rm` when the file is deleted in HEAD, otherwise `git merge-file --ours` to keep HEAD. |
| `resinv.py` | Checks whether each residual revert is an exact inverse of some other commit. |
| `seq3.py` | Second rebase that dropped the `QCLINUX: defconfig: Crypto/ICE` pair (`/tmp/drop3.txt`). |

## phase4_leftover_analysis: the 7 residual reverts (analysis only)

| File | Purpose |
|------|---------|
| `cmp.v1.py`, `cmp.py` | Compare the patch-ids of our add and revert commits with Qualcomm's originals in `~/qualcomm/ubuntu-qcom-kernel`, matched by subject (`/tmp/qclog.txt` = `git log --format='%H %cd %s'` of the Qualcomm repo). |
| `attr.py` | For each residual commit, works out which earlier commit last touched the lines it removes or adds (blame plus pickaxe). This attributes each leftover to a specific conflict resolution or wholesale SAUCE commit. |
| `var.py` | Patch-id and first tag of every variant of a commit in the Qualcomm repo (`git tag --contains` is very slow there). |

## phase5_fold: `qcom-collapsed-full` → `qcom-collapsed-folded`

| File | Purpose |
|------|---------|
| `seq.py` | Sequence editor. It removes the 6 residual reverts and replaces 4 target picks with `exec act.py force ...`. It also adds `exec act.py ...` after 5 other picks. |
| `act.py` | `force T key files...`: cherry-pick T, force the listed files to T's original version, and commit as T plus a note. `fixfiles`: amend HEAD with files from another commit. `applydiff`: amend HEAD with another commit's diff. `revertfile`: undo HEAD's change to the listed files. `reword`: only add the note. |
| `notes.json` | The `[ Collapsed history ... ]` notes that were appended to the commit messages. |
| `irischk.sh` | Checks every commit in a range that touches iris: each object in the iris `Makefile` must have a source file. |

The fold rebase ran without conflicts.
