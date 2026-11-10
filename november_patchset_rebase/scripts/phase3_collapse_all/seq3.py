import sys
d=[l.strip() for l in open("/tmp/drop3.txt")]
t=sys.argv[1]; L=open(t).readlines()
L=[("drop"+l[4:]) if l.startswith("pick ") and any(x.startswith(l.split()[1]) for x in d) else l for l in L]
open(t,"w").writelines(L)
