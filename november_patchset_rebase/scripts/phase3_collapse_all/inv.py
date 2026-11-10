import subprocess,re
def g(*a,inp=None): return subprocess.run(["git",*a],capture_output=True,text=True,input=inp).stdout
drop=set(l.strip() for l in open("/tmp/drop2.txt") if l.strip())
log=g("log","--reverse","--format=%H %s","Ubuntu-7.0.0-44.44..qcom-collapsed").splitlines()
subj={h:s for h,s in (l.split(" ",1) for l in log)}
order=[l.split()[0] for l in log]
live={}
pairs=[]
for c in order:
    if c not in drop: continue
    s=subj[c]; m=re.match(r'^Revert "(.*)"$',s)
    if m and live.get(m.group(1)): pairs.append((live[m.group(1)].pop(),c))
    else: live.setdefault(s,[]).append(c)
def pid(diff): 
    o=g("patch-id","--stable",inp=diff).split(); return o[0] if o else None
bad=[]
for a,r in pairs:
    pa=pid(g("diff",a+"^",a)); pr=pid(g("diff",r,r+"^"))
    if pa!=pr: bad.append((a,r))
print(len(pairs),"pairs;",len(bad),"non-exact")
for a,r in bad:
    print(a[:12],r[:12],subj[a][:90])
