import sys,shlex
P="python3 /tmp/fold/act.py "
I="drivers/media/platform/qcom/iris/"
V="drivers/media/platform/qcom/venus/core.c"
drop={"e7477d371747","ebbbee012e93","1180d071b9a1","42f6ed8cfc09","5da6242d2711","ea568187f450"}
replace={"932cf444c7cd":P+"force 932cf444c7cd venus "+V,
 "f5b461b66c45":P+"force f5b461b66c45 irissplit "+" ".join(I+f for f in ["Makefile","iris_platform_sm8550.h","iris_platform_vpu3x.c","iris_platform_vpu4x.c"]),
 "704e6d49d300":P+"force 704e6d49d300 config debian.qcom/config/annotations",
 "e8bed273512d":P+"force e8bed273512d pcie drivers/pci/controller/dwc/pcie-qcom.c"}
after={"f5f67565a2db":P+"revertfile f5f67565a2db fixbuild "+shlex.quote(r"- venus/core\.c:.*?#if\n")+" "+V,
 "a3e151147322":P+"revertfile a3e151147322 irissauce '' "+I+"Makefile",
 "d1d48e12c4c1":P+"applydiff d1d48e12c4c1 5da6242d2711 emmc",
 "9c8f196e2c30":P+"fixfiles 9c8f196e2c30 ea568187f450 hwmode "+I+"iris_vpu3x.c "+I+"iris_vpu_common.c",
 "d19c87958d2c":P+"reword d19c87958d2c agatti"}
f=sys.argv[1]; out=[]; hit=set()
def m(h,keys):
    for k in keys:
        if k.startswith(h) or h.startswith(k[:len(h)]) and len(h)>=7 and k.startswith(h): return k
for l in open(f):
    p=l.split()
    if len(p)>1 and p[0]=="pick":
        k=m(p[1],list(drop)+list(replace)+list(after))
        if k: hit.add(k)
        if k in drop: continue
        if k in replace: out.append("exec "+replace[k]+"\n"); continue
        out.append(l)
        if k in after: out.append("exec "+after[k]+"\n")
        continue
    out.append(l)
assert len(hit)==15,(len(hit),hit)
open(f,"w").writelines(out)
