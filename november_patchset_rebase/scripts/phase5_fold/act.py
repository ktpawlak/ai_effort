import sys,json,subprocess,re,os
def g(*a,**k):
    r=subprocess.run(["git",*a],capture_output=True,text=True,**k)
    if r.returncode and k.get("check",True) is not False: pass
    return r
N=json.load(open("/tmp/fold/notes.json"))
def msg_with(orig,key,drop_re=None):
    m=g("log","-1","--format=%B",orig).stdout.rstrip("\n")
    if drop_re: m=re.sub(drop_re,"",m,flags=re.S)
    if key=="agatti": m=re.sub(r"\n\n\[ Collapsed history:.*?\]\n?","",m,flags=re.S).rstrip("\n")
    paras=m.split("\n\n"); last=paras[-1].splitlines()
    note=N[key]
    if len(paras)>1 and all(re.match(r"^[A-Za-z-]+: ",l) or l.startswith("(cherry") or l.startswith("(backported") for l in last):
        paras.insert(len(paras)-1,note)
    else: paras.append(note)
    return "\n\n".join(paras)+"\n"
def commit_as(orig,msg,amend):
    env=dict(os.environ)
    for k,f in (("GIT_AUTHOR_NAME","%an"),("GIT_AUTHOR_EMAIL","%ae"),("GIT_AUTHOR_DATE","%ad")):
        env[k]=g("log","-1",f"--format={f}",orig).stdout.strip()
    r=subprocess.run(["git","commit","-q","--allow-empty","-F","-"]+(["--amend"] if amend else []),input=msg,text=True,env=env,capture_output=True)
    assert r.returncode==0,r.stderr
def ok(r): assert r.returncode==0,(r.args,r.stdout,r.stderr)
cmd=sys.argv[1]
if cmd=="force":      # force <T> <key> <files...>: cherry-pick T, force files to T's version
    T,key,files=sys.argv[2],sys.argv[3],sys.argv[4:]
    g("cherry-pick","-n",T)
    for f in files:
        if g("cat-file","-e",f"{T}:{f}").returncode==0: ok(g("checkout",T,"--",f))
        else: g("rm","-q","-f","--ignore-unmatch","--",f)
    um=g("diff","--name-only","--diff-filter=U").stdout.split()
    assert not um,("unresolved",um)
    subprocess.run(["rm","-f",".git/CHERRY_PICK_HEAD"])
    commit_as(T,msg_with(T,key),False)
elif cmd=="fixfiles": # fixfiles <X> <R> <key> <files...>: amend HEAD(=X) with files from R
    X,R,key,files=sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5:]
    for f in files: ok(g("checkout",R,"--",f))
    commit_as(X,msg_with(X,key),True)
elif cmd=="applydiff":# applydiff <X> <R> <key>: amend HEAD with R's diff
    X,R,key=sys.argv[2:5]
    d=g("show","--format=","--binary",R).stdout
    r=subprocess.run(["git","apply","--index","-3"],input=d,text=True,capture_output=True); ok(r)
    commit_as(X,msg_with(X,key),True)
elif cmd=="revertfile":# revertfile <X> <key> <dropregex> <files...>: undo X's change to files
    X,key,dr,files=sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5:]
    for f in files: ok(g("checkout","HEAD^","--",f))
    commit_as(X,msg_with(X,key,dr),True)
elif cmd=="reword":
    X,key=sys.argv[2:4]; commit_as(X,msg_with(X,key),True)
