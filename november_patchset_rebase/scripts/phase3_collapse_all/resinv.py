import subprocess
def g(*a,inp=None): return subprocess.run(["git",*a],capture_output=True,text=True,input=inp).stdout
def pid(d):
    o=g("patch-id","--stable",inp=d).split(); return o[0] if o else None
res="4f445404fce7 57ed862d305c 7294172b3e7b c3c8604da651 2ea23540677c a5a3a3922050 32d3c47197b5".split()
allc=g("rev-list","Ubuntu-7.0.0-44.44..qcom-collapsed-full").split()
for r in res:
    files=g("diff-tree","--no-commit-id","--name-only","-r",r).split()
    inv=pid(g("diff",r,r+"^"))
    cands=g("rev-list","Ubuntu-7.0.0-44.44..qcom-collapsed-full","--",*files).split()
    hit=[c[:12] for c in cands if c!=r and pid(g("diff",c+"^",c))==inv]
    print(r, g("log","-1","--format=%s",r).strip()[:80], "| exact-inverse of:",hit)
