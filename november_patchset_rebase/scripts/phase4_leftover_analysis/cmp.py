import subprocess
OURS="/home/kuba.pawlak@canonical.com/qualcomm/resolute/linux-qcom/linux-main"
QC="/home/kuba.pawlak@canonical.com/qualcomm/ubuntu-qcom-kernel"
def g(repo,*a): return subprocess.run(["git","-C",repo,*a],capture_output=True,text=True).stdout
def pid(repo,c,inv=False):
    d=g(repo,"diff",c,c+"^") if inv else g(repo,"diff",c+"^",c)
    o=subprocess.run(["git","patch-id","--stable"],input=d,capture_output=True,text=True).stdout.split(); return o[0][:10] if o else "-"
qc={}
for l in open("/tmp/qclog.txt"):
    h,d,s=l.rstrip("\n").split(" ",2); qc.setdefault(s,[]).append((h,d))
pairs=[("9a87bf233707","60de5dfbfbe9"),("8a85727bc446","539f4748b74e"),("644e456c0dbc","dc55f81f41d6"),("bef4ab230037","d1bbea881e63"),("510d9d2c250c","a0e0b47dbe10"),("589e6a8dcda1","950cfeba004e"),("4a8164c424a1","314082fd1cb1")]
for a,r in pairs:
    for c in (a,r):
        s=g(OURS,"log","-1","--format=%s",c).strip()
        print(f"{c} ours={pid(OURS,c)} {s[:75]}")
        for q,d in qc.get(s,[]): print(f"     qc {q[:12]} {d} {pid(QC,q)}")
    print(f"  inverse-of-revert pid={pid(OURS,r,True)}\n")
