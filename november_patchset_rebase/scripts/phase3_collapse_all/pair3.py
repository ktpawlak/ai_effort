import subprocess, re, json
def g(*a): return subprocess.run(["git",*a],capture_output=True,text=True).stdout
BASE="Ubuntu-7.0.0-44.44"; TIP="qcom-collapsed"
revs=g("rev-list","--reverse",f"{BASE}..{TIP}").split()
subj={}
for line in g("log","--reverse","--format=%H %s",f"{BASE}..{TIP}").splitlines():
    h,s=line.split(" ",1); subj[h]=s
live={}; parent={}; unmatched=[]
def find(x):
    while parent.get(x,x)!=x: x=parent[x]
    return x
for c in revs:
    s=subj[c]; m=re.match(r'^Revert "(.*)"$',s)
    if m and live.get(m.group(1)):
        o=live[m.group(1)].pop()
        parent[find(c)]=find(o)
        # a revert of a revert re-applies the inner commit
        m2=re.match(r'^Revert "(.*)"$',m.group(1))
        if m2: live.setdefault(m2.group(1),[]).append(c)
    elif m: unmatched.append(c)
    live.setdefault(s,[]).append(c)
groups={}
for c in revs:
    if c in parent or any(parent.get(x)==c for x in parent): groups.setdefault(find(c),[]).append(c)
drop=[];keep_note=[]
for r,mem in groups.items():
    mem.sort(key=revs.index)
    if len(mem)%2==0: drop+=mem
    else:  # net one application: keep last re-application, drop the rest
        drop+=mem[:-1]; keep_note.append((mem[-1][:12],subj[mem[-1]]))
print("groups",len(groups),"drop",len(drop))
print("sizes",sorted({len(m) for m in groups.values()}))
for k in keep_note: print("odd chain, keeping:",*k)
for u in unmatched: print("UNMATCHED:",u[:12],subj[u])
open("/tmp/drop2.txt","w").write("\n".join(drop)+"\n")
for r,mem in groups.items():
    if len(mem)>2: print("chain:",[subj[x][:70] for x in mem])
