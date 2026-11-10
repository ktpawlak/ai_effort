import subprocess,re,json
def g(*a): return subprocess.run(["git",*a],capture_output=True,text=True).stdout
drop=set(l.strip() for l in open("/tmp/drop2.txt") if l.strip())
log=g("log","--reverse","--format=%H %s","Ubuntu-7.0.0-44.44..qcom-collapsed").splitlines()
subj={h:s for h,s in (l.split(" ",1) for l in log)}
order=[l.split()[0] for l in log]
full=lambda x:g("rev-parse",x).strip()
extra={full("cb8a9bf9e1c0"):[full("6d8f001d9ecf")],
 full("3aafaebcd828"):[full(x) for x in "5c6ac730dc06 205263429314 bf3018fcefd3 64fc90edbdf3".split()],
 full("0c4ecdf040b0"):[full(x) for x in "ebee7eaa745e a4b84af542bd f1d3a364ee1c".split()]}
live={};groups=[]
inextra={a for v in extra.values() for a in v}
for c in order:
    if c not in drop or c in inextra: continue
    if c in extra: groups.append({"adds":extra[c],"rev":c}); continue
    m=re.match(r'^Revert "(.*)"$',subj[c])
    if m and live.get(m.group(1)): groups.append({"adds":[live[m.group(1)].pop()],"rev":c})
    else: live.setdefault(subj[c],[]).append(c)
assert not any(live.values()), live
pos={c:i for i,c in enumerate(order)}
for gr in groups:
    fs=set()
    for c in gr["adds"]+[gr["rev"]]:
        fs|=set(g("diff-tree","--no-commit-id","--name-only","-r",c).split())
    gr["files"]=sorted(fs); gr["start"]=min(pos[a] for a in gr["adds"]); gr["end"]=pos[gr["rev"]]
json.dump({"groups":groups,"pos":pos},open("/tmp/groups.json","w"))
print(len(groups),"groups", sum(len(x["adds"])+1 for x in groups),"commits")
