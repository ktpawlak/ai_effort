#!/bin/bash
# Auto-resolve: modify/delete where deleted in HEAD -> keep deletion; empty -> skip
: > /tmp/auto_resolved.txt
while [ -d .git/rebase-merge ]; do
  cur=$(cat .git/rebase-merge/stopped-sha 2>/dev/null || git rev-parse REBASE_HEAD 2>/dev/null)
  U=$(git diff --name-only --diff-filter=U)
  if [ -n "$U" ]; then
    st=$(git status --porcelain=v1 | grep -E '^(UD|DU|AA|UU|AU|UA|DD) ')
    if echo "$st" | grep -qvE '^DU '; then echo "MANUAL needed at $cur"; echo "$st"; exit 2; fi
    for f in $U; do git rm -q -- "$f"; echo "$cur rm $f $(git log -1 --format=%s $cur)" >> /tmp/auto_resolved.txt; done
  fi
  if git diff --cached --quiet; then
    echo "$cur EMPTY-skip $(git log -1 --format=%s $cur)" >> /tmp/auto_resolved.txt
    GIT_EDITOR=true git -c rerere.enabled=true rebase --skip >> /tmp/collapse.log 2>&1
  else
    GIT_EDITOR=true git -c rerere.enabled=true rebase --continue >> /tmp/collapse.log 2>&1
  fi
done
echo DONE
