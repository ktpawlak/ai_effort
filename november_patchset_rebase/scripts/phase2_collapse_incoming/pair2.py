import subprocess, re, json
def g(*a): return subprocess.run(["git",*a],capture_output=True,text=True).stdout
BASE="Ubuntu-7.0.0-44.44"; OLD="f54e8d58bc85c87179befcd1208ea27f87804c30"
revs=g("rev-list","--reverse",f"{BASE}..master-next").split()
old=set(g("rev-list",f"{BASE}..{OLD}").split())
subj={c:g("log","-1","--format=%s",c).rstrip("\n") for c in revs}
live={}   # subject -> list of unpaired commits
pairs=[]; unmatched=[]
for c in revs:
    s=subj[c]; m=re.match(r'^Revert "(.*)"$',s)
    if m and live.get(m.group(1)):
        o=live[m.group(1)].pop(); pairs.append((o,c))
    else:
        if m: unmatched.append(c)
        live.setdefault(s,[]).append(c)
# Build chains: union pairs sharing commits
new_pairs=[(o,r) for o,r in pairs if r not in old]
print("total pairs",len(pairs),"pairs whose revert is in new drop:",len(new_pairs))
print("orig location for new pairs: old",sum(o in old for o,_ in new_pairs),"new",sum(o not in old for o,_ in new_pairs))
print("unmatched reverts in new drop:",[ (c[:12],subj[c]) for c in unmatched if c not in old])
for o,r in new_pairs:
    if o not in old or subj[o].startswith("Revert"): print("chain:",o[:12],subj[o],"<-",r[:12])
json.dump(dict(new=[[o,r] for o,r in new_pairs],all=[[o,r] for o,r in pairs]),open("/tmp/pairs2.json","w"))
