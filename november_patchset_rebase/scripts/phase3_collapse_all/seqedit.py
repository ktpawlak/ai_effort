import sys,json
G=json.load(open("/tmp/groups.json"))["groups"]
adds={a for gr in G for a in gr["adds"]}; revs={gr["rev"] for gr in G}
todo=sys.argv[1]; out=[]; n=0
for line in open(todo):
    p=line.split()
    if len(p)>1 and p[0]=="pick":
        full=[x for x in adds|revs if x.startswith(p[1])]
        if full:
            f=full[0]; n+=1
            out.append("drop"+line[4:])
            if f in revs: out.append(f"exec python3 /tmp/residual.py {f}\n")
            continue
    out.append(line)
open(todo,"w").writelines(out); open("/tmp/seqedit.count","w").write(str(n))
