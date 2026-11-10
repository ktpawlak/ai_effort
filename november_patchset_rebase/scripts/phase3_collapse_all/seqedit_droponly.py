import sys
drop=[l.strip() for l in open("/tmp/drop.txt") if l.strip()]
todo=sys.argv[1]; out=[]; n=0
for line in open(todo):
    p=line.split()
    if len(p)>1 and p[0]=="pick" and any(d.startswith(p[1]) for d in drop):
        out.append("drop"+line[4:]); n+=1
    else: out.append(line)
open(todo,"w").writelines(out); open("/tmp/seqedit.count","w").write(str(n))
