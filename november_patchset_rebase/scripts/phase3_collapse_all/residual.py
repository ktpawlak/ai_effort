import sys,json,subprocess,os
def g(*a,**k): return subprocess.run(["git",*a],capture_output=True,text=True,**k)
D=json.load(open("/tmp/groups.json")); G=D["groups"]
rev=sys.argv[1]; me=[x for x in G if x["rev"]==rev][0]
E=me["end"]; restored=[]; skipped=[]
for f in me["files"]:
    if any(o is not me and o["start"]<E<o["end"] and f in o["files"] for o in G):
        skipped.append(f); continue
    if g("cat-file","-e",f"{rev}:{f}").returncode==0:
        r=g("checkout",rev,"--",f)
    else:
        r=g("rm","-q","--ignore-unmatch","-f","--",f)
    assert r.returncode==0,(f,r.stderr)
    restored.append(f)
log=open("/tmp/residual.log","a")
if g("diff","--cached","--quiet").returncode!=0:
    stat=g("diff","--cached","--stat").stdout
    msg=g("log","-1","--format=%B",rev).stdout.rstrip()
    adds=", ".join(g("log","-1","--format=%h (\"%s\")",a).stdout.strip() for a in me["adds"])
    msg+="\n\n[ Collapsed history: the add/revert pair was squashed out; this commit\n  keeps only the part of the revert that did not cancel the original\n  change(s) out. ]\n"
    env=dict(os.environ)
    for k,fmt in (("GIT_AUTHOR_NAME","%an"),("GIT_AUTHOR_EMAIL","%ae"),("GIT_AUTHOR_DATE","%ad")):
        env[k]=g("log","-1",f"--format={fmt}",rev).stdout.strip()
    r=g("commit","-q","-F","-",input=msg,env=env); assert r.returncode==0,r.stderr
    log.write(f"RESIDUAL {rev[:12]} -> {g('rev-parse','--short=12','HEAD').stdout.strip()}\n{stat}\n")
else:
    log.write(f"CLEAN {rev[:12]}\n")
if skipped: log.write(f"  skipped(open pairs) {rev[:12]}: {skipped}\n")
