import subprocess,re,sys,collections
def g(*a): return subprocess.run(["git",*a],capture_output=True,text=True).stdout
BASE="Ubuntu-7.0.0-44.44"
for r in sys.argv[1:]:
    print("=====",g("log","-1","--format=%h %s",r).strip())
    diff=g("show","--format=","-U0",r)
    f=None; rem=collections.defaultdict(list); add=collections.defaultdict(list)
    for l in diff.splitlines():
        if l.startswith("+++ "): f=l[6:] if l!="+++ /dev/null" else f
        elif l.startswith("--- "): f=l[6:] if l!="--- /dev/null" else None
        elif l.startswith("@@"):
            m=re.match(r"@@ -(\d+)(?:,(\d+))? \+",l); s=int(m.group(1)); n=int(m.group(2) or 1)
            if n and f: rem[f].append((s,s+n-1))
        elif l.startswith("+") and f and len(l.strip())>6: add[f].append(l[1:])
    for fn,rs in rem.items():
        cnt=collections.Counter()
        for a,b in rs:
            for bl in g("blame","-s","--abbrev=12","-L",f"{a},{b}",r+"^","--",fn).splitlines():
                cnt[bl.split()[0].lstrip("^")]+=1
        for c,n in cnt.most_common(): print(f"  -{n:4d} {fn.split('/')[-1]:28s} from {g('log','-1','--format=%h %s',c).strip()[:95]}")
    for fn,ls in add.items():
        cnt=collections.Counter()
        for line in ls[:60:3]:
            o=g("log","--format=%h","-S",line,BASE+".."+r+"^","--",fn).split()
            cnt[o[0] if o else "never-in-range(base?)"]+=1
        for c,n in cnt.most_common():
            s=g('log','-1','--format=%h %s',c).strip() if c[0]!='n' else c
            print(f"  +~{n:3d} {fn.split('/')[-1]:28s} last touched by {s[:95]}")
