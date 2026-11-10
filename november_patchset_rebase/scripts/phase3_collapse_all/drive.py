import subprocess,json,os,sys
def g(*a,**k): return subprocess.run(["git",*a],capture_output=True,text=True,**k)
D=json.load(open("/tmp/groups.json")); G=D["groups"]; P=D["pos"]
log=open("/tmp/auto_resolved.txt","a")
gd=g("rev-parse","--git-dir").stdout.strip()
while os.path.isdir(os.path.join(gd,"rebase-merge")):
    rh=g("rev-parse","-q","--verify","REBASE_HEAD").stdout.strip()
    um=g("ls-files","-u").stdout.splitlines()
    if um:
        pos=P.get(rh)
        if pos is None: print("STOP: conflict but unknown commit",rh); sys.exit(1)
        st={}
        for l in um:
            meta,path=l.split("\t",1); st.setdefault(path,set()).add(meta.split()[2])
        for path,s in st.items():
            if not any(o["start"]<pos<o["end"] and path in o["files"] for o in G):
                print("STOP: conflict outside open pair",rh[:12],path,s); sys.exit(1)
            if "2" not in s: g("rm","-q","--",path); act="rm(deleted in HEAD)"
            elif "3" not in s: g("checkout","--ours","--",path); g("add","--",path); act="ours(deleted by them)"
            else:
                tmp={}
                for n in "123":
                    tmp[n]=f"/tmp/mf.{n}"
                    open(tmp[n],"w").write(g("show",f":{n}:{path}").stdout if n in s else "")
                out=g("merge-file","-p","--ours",tmp["2"],tmp["1"],tmp["3"]).stdout
                open(path,"w").write(out); g("add","--",path); act="merge-ours"
            log.write(f"{rh[:12]} {path} {act}\n")
        log.flush()
    if not um and g("diff","--cached","--quiet").returncode==0 and rh:
        log.write(f"{rh[:12]} EMPTY-skip {g('log','-1','--format=%s',rh).stdout.strip()}\n"); log.flush()
        r=g("rebase","--skip",env=dict(os.environ,GIT_EDITOR="true"))
    elif rh or um:
        r=g("-c","core.editor=true","rebase","--continue",env=dict(os.environ,GIT_EDITOR="true"))
    else:
        print("STOP: rebase stopped without REBASE_HEAD (exec failure?)"); sys.exit(1)
    if r.returncode!=0 and not g("ls-files","-u").stdout and g("rev-parse","-q","--verify","REBASE_HEAD").returncode!=0 and os.path.isdir(os.path.join(gd,"rebase-merge")):
        print("STOP:",r.stdout[-800:],r.stderr[-800:]); sys.exit(1)
print("DONE")
