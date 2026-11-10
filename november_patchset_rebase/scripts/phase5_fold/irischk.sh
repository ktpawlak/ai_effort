#!/bin/bash
# usage: irischk.sh range ; prints commits where iris Makefile objs lack sources
D=drivers/media/platform/qcom/iris
for c in $(git rev-list --reverse "$1" -- $D); do
  objs=$(git show $c:$D/Makefile 2>/dev/null | grep -o '[a-z0-9_]*\.o' | sed 's/\.o$//' | sort -u)
  files=$(git ls-tree --name-only $c $D/ | sed 's#.*/##;s/\.c$//' | sort -u)
  miss=$(comm -23 <(echo "$objs") <(echo "$files") | grep -v '^qcom-iris$' | tr '\n' ' ')
  [ -n "$miss" ] && echo "$(git log -1 --format='%h %s' $c | cut -c1-80) MISSING: $miss"
done
