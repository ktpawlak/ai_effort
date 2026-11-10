import subprocess
QC="/home/kuba.pawlak@canonical.com/qualcomm/ubuntu-qcom-kernel"
OURS="/home/kuba.pawlak@canonical.com/qualcomm/resolute/linux-qcom/linux-main"
def g(r,*a,inp=None): return subprocess.run(["git","-C",r,*a],capture_output=True,text=True,input=inp).stdout
def pid(r,c): return (subprocess.run(["git","patch-id","--stable"],input=g(r,"diff",c+"^",c),capture_output=True,text=True).stdout.split() or ["-"])[0][:10]
qc={}
for l in open("/tmp/qclog.txt"):
    h,d,s=l.rstrip("\n").split(" ",2); qc.setdefault(s,[]).append(h)
for o in "8a85727bc446 539f4748b74e bef4ab230037 d1bbea881e63 510d9d2c250c 5291a656371d cac48fed7a54 589e6a8dcda1 950cfeba004e b72ae16597cd".split():
    s=g(OURS,"log","-1","--format=%s",o).strip()
    print(f"{o} ours={pid(OURS,o)} {s[:80]}")
    for q in qc.get(s,[]):
        t=g(QC,"tag","--contains",q,"--sort=creatordate").split()
        print(f"   {q[:12]} {pid(QC,q)} {g(QC,'log','-1','--format=%cn %cd','--date=short',q).strip()} first={t[0] if t else '-'}")
