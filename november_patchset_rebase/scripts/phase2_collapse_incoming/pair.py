import subprocess, re, json
def g(*a, inp=None): return subprocess.run(["git",*a],capture_output=True,text=True,input=inp).stdout
BASE="Ubuntu-7.0.0-44.44"; OLD="f54e8d58bc85c87179befcd1208ea27f87804c30"
revs=g("rev-list","--reverse",f"{BASE}..master-next").split()
old=set(g("rev-list",f"{BASE}..{OLD}").split())
subj={c:g("log","-1","--format=%s",c).rstrip("\n") for c in revs}
idx={c:i for i,c in enumerate(revs)}
def pid(diff): 
    o=g("patch-id","--stable",inp=diff).split(); return o[0] if o else None
pairs=[];unmatched=[]
for c in revs:
    s=subj[c]; m=re.match(r'^Revert "(.*)"$',s)
    if not m: continue
    target=m.group(1)
    cands=[x for x in revs[:idx[c]] if subj[x]==target]
    if not cands: unmatched.append((c,s)); continue
    o=cands[-1]
    po=pid(g("show","--format=",o)); pr=pid(g("diff",c,c+"^"))
    pairs.append(dict(rev=c,orig=o,subject=target,orig_in_old=o in old,rev_in_old=c in old,exact=po==pr and po is not None,ncand=len(cands)))
json.dump(dict(pairs=pairs,unmatched=unmatched),open("/tmp/pairs.json","w"),indent=1)
print("pairs:",len(pairs),"unmatched:",len(unmatched))
print("exact inverse:",sum(p["exact"] for p in pairs))
print("orig in existing derivative (44.44..1018.22):",sum(p["orig_in_old"] for p in pairs))
print("orig in new drop:",sum(not p["orig_in_old"] for p in pairs))
print("revert in old range:",sum(p["rev_in_old"] for p in pairs))
print("multi-candidate:",[ (p["rev"][:12],p["ncand"]) for p in pairs if p["ncand"]>1])
for p in pairs:
    if not p["exact"]: print("NOT EXACT:",p["rev"][:12],p["orig"][:12],p["subject"])
for u in unmatched: print("UNMATCHED:",u[0][:12],u[1])
