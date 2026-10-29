#!/usr/bin/env python3
"""re-derive prototype: build a classified inventory of a kernel derivative's OEM stack.

Phase 0 (corpus extraction) + deterministic Phase 2 (classification) from DESIGN.md.
Produces a SQLite inventory you can query, plus a summary report.

Example:
    ./rederive.py extract \
        --tree ~/canonical/kernel/ubuntu/noble/linux-qcom/linux-main \
        --base "UBUNTU: Ubuntu-6.8.0-148.148"
    ./rederive.py classify --generic ~/canonical/linux--noble
    ./rederive.py report
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter

DB_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")

# ---- provenance / subject parsing -------------------------------------------
SAUCE_PREFIXES = ["UBUNTU", "UPSTREAM", "FROMGIT", "FROMLIST", "BACKPORT",
                  "PENDING", "QCLINUX", "SAUCE", "CHROMIUM", "ANDROID"]
RE_CHERRY = re.compile(r"cherry picked from commit\s+([0-9a-f]{12,40})(?:\s+(\S+))?", re.I)
RE_UPSTREAM_COMMIT = re.compile(r"commit\s+([0-9a-f]{12,40})\s+upstream", re.I)
RE_CHANGEID = re.compile(r"^Change-Id:\s*(\S+)", re.I | re.M)
RE_LINK = re.compile(r"^Link:\s*(\S+)", re.I | re.M)
RE_BUGLINK = re.compile(r"^BugLink:\s*(\S+)", re.I | re.M)
VENDOR_HOSTS = ("codelinaro.org", "quic", "qclinux", "codeaurora")


def git(tree, *args):
    return subprocess.run(["git", "-C", tree, *args],
                          capture_output=True, text=True, errors="replace").stdout


def parse_prefixes(subject):
    """Return (list_of_sauce_prefixes, clean_subject). Handles nested 'BACKPORT: UPSTREAM: ...'."""
    found, s = [], subject
    while True:
        m = re.match(r"\s*([A-Z][A-Z0-9_]+):\s*(.*)", s)
        if m and m.group(1) in SAUCE_PREFIXES:
            found.append(m.group(1))
            s = m.group(2)
        else:
            break
    return found, s.strip()


def run(cmd, check=True):
    """Run a command, returning the CompletedProcess (never raises unless check)."""
    return subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", check=check)


def resolve_base(tree, base):
    """Resolve a ref or a subject substring to a commit SHA."""
    sha = git(tree, "rev-parse", "-q", "--verify", f"{base}^{{commit}}").strip()
    if sha:
        return sha
    for line in git(tree, "log", "--format=%H\t%s", "-20000").splitlines():
        h, _, subj = line.partition("\t")
        if base in subj:
            return h
    return ""


# ---- schema -----------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS commits(
    sha TEXT PRIMARY KEY, seq INTEGER, subject TEXT, clean_subject TEXT,
    sauce TEXT, author TEXT, author_email TEXT, author_date TEXT,
    change_id TEXT, prov_sha TEXT, prov_repo TEXT, link TEXT, buglink TEXT,
    files INTEGER, insertions INTEGER, deletions INTEGER, patch_id TEXT,
    category TEXT, confidence REAL, reason TEXT,
    sem_verdict TEXT, sem_action TEXT, sem_reason TEXT, sem_upstream TEXT
);
CREATE TABLE IF NOT EXISTS commit_files(sha TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS idx_files_path ON commit_files(path);
CREATE INDEX IF NOT EXISTS idx_commits_cat ON commits(category);
CREATE TABLE IF NOT EXISTS reconcile_files(
    sha TEXT, path TEXT, up_path TEXT, status TEXT, conflicts INTEGER,
    PRIMARY KEY(sha, path)
);
"""

SEM_COLS = {"sem_verdict": "TEXT", "sem_action": "TEXT",
            "sem_reason": "TEXT", "sem_upstream": "TEXT", "cluster_id": "INTEGER",
            "churn_action": "TEXT", "churn_reason": "TEXT",
            "grad_action": "TEXT", "grad_reason": "TEXT"}


def db_connect(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(commits)")}
    for col, typ in SEM_COLS.items():  # migrate older DBs
        if col not in have:
            con.execute(f"ALTER TABLE commits ADD COLUMN {col} {typ}")
    con.commit()
    return con


# ---- extract ----------------------------------------------------------------
def cmd_extract(args):
    tree = os.path.expanduser(args.tree)
    base = resolve_base(tree, args.base)
    if not base:
        sys.exit(f"could not resolve base ref/subject: {args.base!r}")
    print(f"base = {base[:12]}  head = {args.head}")
    con = db_connect(args.db)
    if args.reset:
        con.executescript("DELETE FROM commits; DELETE FROM commit_files;")

    shas = git(tree, "log", "--no-merges", "--format=%H",
               f"{base}..{args.head}").split()
    shas.reverse()  # oldest first = application order
    print(f"{len(shas)} OEM commits to extract")

    SEP = "\x1e"
    for i, sha in enumerate(shas):
        raw = git(tree, "show", "-s",
                  f"--format=%H{SEP}%an{SEP}%ae{SEP}%aI{SEP}%s{SEP}%b", sha)
        parts = raw.split(SEP)
        if len(parts) < 6:
            continue
        _, an, ae, ad, subj, body = parts[0], parts[1], parts[2], parts[3], parts[4], SEP.join(parts[5:])
        sauce, clean = parse_prefixes(subj)
        cherry = RE_CHERRY.search(body)
        upc = RE_UPSTREAM_COMMIT.search(body)
        prov_sha = (cherry.group(1) if cherry else (upc.group(1) if upc else None))
        prov_repo = cherry.group(2) if (cherry and cherry.group(2)) else None
        m_cid = RE_CHANGEID.search(body)
        m_link = RE_LINK.search(body)
        m_bl = RE_BUGLINK.search(body)
        change_id = m_cid.group(1) if m_cid else None
        link = m_link.group(1) if m_link else None
        buglink = m_bl.group(1) if m_bl else None

        # diffstat + files
        nf = ins = dele = 0
        files = []
        stat = git(tree, "show", "--numstat", "--format=", sha)
        for ln in stat.splitlines():
            cols = ln.split("\t")
            if len(cols) == 3:
                files.append(cols[2])
                ins += int(cols[0]) if cols[0].isdigit() else 0
                dele += int(cols[1]) if cols[1].isdigit() else 0
        nf = len(files)

        con.execute(
            "INSERT OR REPLACE INTO commits(sha,seq,subject,clean_subject,sauce,"
            "author,author_email,author_date,change_id,prov_sha,prov_repo,link,"
            "buglink,files,insertions,deletions) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sha, i, subj, clean, ",".join(sauce), an, ae, ad,
             change_id, prov_sha, prov_repo, link, buglink,
             nf, ins, dele))
        con.executemany("INSERT INTO commit_files(sha,path) VALUES(?,?)",
                         [(sha, f) for f in files])
        if i % 250 == 0:
            con.commit()
            print(f"  ... {i}/{len(shas)}", flush=True)
    con.commit()
    print("extract done ->", args.db)


# ---- classify (deterministic Phase 2) ---------------------------------------
def commit_exists(tree, sha):
    if not sha:
        return False
    return subprocess.run(["git", "-C", tree, "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def commit_in_base(tree, sha, base_ref):
    """True if sha is an ancestor of base_ref (i.e. already in generic)."""
    if not commit_exists(tree, sha):
        return False
    return subprocess.run(["git", "-C", tree, "merge-base", "--is-ancestor", sha, base_ref],
                          capture_output=True).returncode == 0


def cmd_classify(args):
    con = db_connect(args.db)
    generic = os.path.expanduser(args.generic) if args.generic else None
    gref = args.generic_ref
    rows = con.execute(
        "SELECT sha,sauce,prov_sha,prov_repo,subject,clean_subject FROM commits").fetchall()
    print(f"classifying {len(rows)} commits"
          + (f" against generic {generic} ({gref})" if generic else " (deterministic, no generic cross-match)"))
    for sha, sauce, prov_sha, prov_repo, subj, clean in rows:
        sauces = sauce.split(",") if sauce else []
        cat, conf, reason = "DERIVATIVE_ONLY", 0.4, "no upstream provenance found"
        vendor = prov_repo and any(h in prov_repo.lower() for h in VENDOR_HOSTS)

        if subj.strip().lower().startswith(("revert ", 'revert "')) or "UBUNTU" in sauces and "Revert" in subj:
            cat, conf, reason = "REVERT_NOISE", 0.6, "revert; candidate for churn cancellation"
        elif generic and prov_sha and commit_in_base(generic, prov_sha, gref):
            cat, conf, reason = "UPSTREAMED", 0.95, f"provenance {prov_sha[:12]} present in generic"
        elif "UBUNTU" in sauces:
            cat, conf, reason = "PACKAGING", 0.9, "UBUNTU: packaging/config/SAUCE"
        elif vendor or "QCLINUX" in sauces:
            cat, conf, reason = "VENDOR_ONLY", 0.75, f"vendor provenance ({prov_repo or 'QCLINUX'})"
        elif any(p in sauces for p in ("UPSTREAM", "FROMGIT", "BACKPORT")) and prov_sha:
            cat, conf, reason = "UPSTREAM_PROVENANCE", 0.7, "clean upstream SHA; will graduate/modified-upstream risk"
        elif "FROMLIST" in sauces:
            cat, conf, reason = "MODIFIED_UPSTREAM_CANDIDATE", 0.6, "FROMLIST: preliminary; high collision risk"
        elif "PENDING" in sauces:
            cat, conf, reason = "DERIVATIVE_ONLY", 0.5, "PENDING: vendor preliminary, no upstream ref"
        con.execute("UPDATE commits SET category=?,confidence=?,reason=? WHERE sha=?",
                    (cat, conf, reason, sha))
    con.commit()
    print("classify done")


# ---- packets (Phase 2 semantic fan-out input) -------------------------------
SEM_CATEGORIES = ("MODIFIED_UPSTREAM_CANDIDATE", "UPSTREAM_PROVENANCE")

PACKET_INSTRUCTIONS = """\
You are triaging ONE kernel derivative commit for a 're-derive' effort.
Goal: decide how this derivative patch relates to its upstream form, so we can
reproduce the same tree with the fewest merge conflicts.

Read the two diffs below:
- DERIVATIVE PATCH: the commit as it exists in the OEM derivative branch.
- UPSTREAM CANDIDATE: the matching patch found in the reference upstream tree
  (may be '(none found)').

Classify the relationship into exactly one verdict:
- IDENTICAL          : derivative == upstream form (modulo context/whitespace).
- COSMETIC_ONLY      : only context/whitespace/backport-adaptation differs; no
                       functional derivative-specific change.
- OEM_DELTA          : derivative adds/changes real functionality beyond upstream.
- NO_UPSTREAM        : no upstream candidate; genuinely derivative-only.

And an action:
- DROP   : redundant once upstream lands (IDENTICAL / COSMETIC_ONLY).
- KEEP_DELTA : keep only the derivative's extra change on top of upstream (OEM_DELTA).
- KEEP   : keep whole (NO_UPSTREAM).

Output ONE line of JSON ONLY, nothing else:
{"sha":"<sha>","verdict":"...","action":"...","reason":"<=25 words"}
"""


def build_ref_index(ref_tree, ref="HEAD"):
    """One-time index of the reference tree: subject -> sha, change_id -> sha. Much
    faster than per-commit `git log --grep --all` over a huge mainline history."""
    subj_idx, cid_idx = {}, {}
    out = git(ref_tree, "log", "--no-merges", "--format=%H%x1f%s%x1f%(trailers:key=Change-Id,valueonly)",
              "-200000", ref)
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 2:
            continue
        h, subj = parts[0], parts[1]
        cid = parts[2].strip() if len(parts) > 2 else ""
        subj_idx.setdefault(subj.strip(), h)
        if cid:
            cid_idx.setdefault(cid, h)
    return subj_idx, cid_idx


def find_upstream_candidate(ref_tree, prov_sha, change_id, clean_subject, idx=None):
    """Return (sha, how) of a matching commit in the reference upstream tree, or (None,None)."""
    if prov_sha and commit_exists(ref_tree, prov_sha):
        return prov_sha, "provenance"
    subj_idx, cid_idx = idx if idx else ({}, {})
    if change_id and change_id in cid_idx:
        return cid_idx[change_id], "change-id"
    if clean_subject and clean_subject in subj_idx:
        return subj_idx[clean_subject], "subject"
    return None, None


def cmd_packets(args):
    con = db_connect(args.db)
    deriv = os.path.expanduser(args.tree)
    ref = os.path.expanduser(args.ref_tree) if args.ref_tree else None
    os.makedirs(args.out, exist_ok=True)
    cats = args.categories.split(",") if args.categories else list(SEM_CATEGORIES)
    q = ("SELECT sha,subject,clean_subject,change_id,prov_sha FROM commits "
         f"WHERE category IN ({','.join('?' * len(cats))}) "
         "AND sem_verdict IS NULL ORDER BY seq")
    rows = con.execute(q, cats).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    idx = build_ref_index(ref, args.ref_rev) if ref else None
    manifest = []
    for sha, subj, clean, cid, prov in rows:
        dpatch = git(deriv, "show", "--format=medium", sha)
        up_sha, how = (None, None)
        upatch = "(none found)"
        if ref:
            up_sha, how = find_upstream_candidate(ref, prov, cid, clean, idx)
            if up_sha:
                upatch = git(ref, "show", "--format=medium", up_sha)
        path = os.path.join(args.out, f"{sha[:12]}.txt")
        with open(path, "w") as f:
            f.write(PACKET_INSTRUCTIONS)
            f.write(f"\n\n=== SHA: {sha} ===\nSUBJECT: {subj}\n")
            f.write(f"UPSTREAM CANDIDATE: {up_sha or '(none found)'} (match={how})\n")
            f.write("\n===== DERIVATIVE PATCH =====\n")
            f.write(dpatch[:args.max_chars])
            f.write("\n\n===== UPSTREAM CANDIDATE =====\n")
            f.write(upatch[:args.max_chars])
        manifest.append(path)
        if up_sha:
            con.execute("UPDATE commits SET sem_upstream=? WHERE sha=?", (up_sha, sha))
    con.commit()
    man_path = os.path.join(args.out, "manifest.txt")
    with open(man_path, "w") as f:
        f.write("\n".join(manifest) + ("\n" if manifest else ""))
    print(f"wrote {len(manifest)} packets -> {args.out}  (manifest: {man_path})")


def cmd_ingest(args):
    """Read JSONL verdicts (one {'sha','verdict','action','reason'} per line) into the DB."""
    import json
    con = db_connect(args.db)
    n = 0
    for infile in args.inputs:
        with open(os.path.expanduser(infile)) as f:
            for line in f:
                line = line.strip().strip("`")
                if not line or not line.startswith("{"):
                    continue
                try:
                    v = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sha = v.get("sha", "")
                if not sha:
                    continue
                # Match on 12-char prefix: lightweight models sometimes mis-transcribe
                # the full 40-char sha, but the leading 12 are reliable and unique here.
                row = con.execute("SELECT sha FROM commits WHERE sha LIKE ?",
                                  (sha[:12] + "%",)).fetchone()
                if not row:
                    continue
                con.execute(
                    "UPDATE commits SET sem_verdict=?,sem_action=?,sem_reason=? WHERE sha=?",
                    (v.get("verdict"), v.get("action"), v.get("reason"), row[0]))
                n += 1
    con.commit()
    print(f"ingested {n} verdicts")


# ---- reconcile (Phase 3: three-way delta-on-upstream) -----------------------
def blob(tree, rev, path):
    """Return file content at rev:path, or None if absent."""
    r = subprocess.run(["git", "-C", tree, "show", f"{rev}:{path}"],
                       capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def up_touched_paths(ref_tree, up_sha):
    out = git(ref_tree, "show", "--numstat", "--format=", up_sha)
    paths = set()
    for ln in out.splitlines():
        cols = ln.split("\t")
        if len(cols) == 3:
            paths.add(cols[2])
    return paths


def split_hunks(patch_text):
    """Split a unified diff into (preamble, [hunk_text, ...])."""
    lines = patch_text.splitlines(keepends=True)
    pre, hunks, cur = [], [], None
    for ln in lines:
        if ln.startswith("@@"):
            if cur is not None:
                hunks.append("".join(cur))
            cur = [ln]
        elif cur is None:
            pre.append(ln)
        else:
            cur.append(ln)
    if cur is not None:
        hunks.append("".join(cur))
    return "".join(pre), hunks


def hunk_added_lines(hunk):
    """Significant lines a hunk adds (ignores markers, blanks, lone braces)."""
    out = []
    for ln in hunk.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            s = ln[1:].strip()
            if len(s) > 3 and s not in ("*/", "/*"):
                out.append(s)
    return out


def hunk_subsumed(hunk, upstream_lines):
    """True if everything this hunk adds is already present upstream."""
    added = hunk_added_lines(hunk)
    if not added:
        return False
    return all(a in upstream_lines for a in added)


def transplant_hunks(deriv_tree, sha, path, theirs, fuzz=3):
    """Apply ONLY the derivative's own hunks for `path` onto upstream content `theirs`.

    Unlike a whole-file 3-way merge (whose ancestor sits on a different kernel
    base), this matches each hunk by local context, so unrelated version drift
    between the derivative base and upstream never registers as a conflict.

    Returns (result_text, stats_dict, rejected_hunks).
    """
    import tempfile
    dpatch = git(deriv_tree, "diff", f"{sha}^", sha, "--", path)
    _, all_hunks = split_hunks(dpatch)
    total = len(all_hunks)
    upstream_lines = {l.strip() for l in theirs.splitlines()}

    workdir = tempfile.mkdtemp(prefix="rederive_")
    try:
        target = os.path.join(workdir, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(theirs)
        pf = os.path.join(workdir, "deriv.patch")
        with open(pf, "w") as f:
            f.write(dpatch)
        rej = os.path.join(workdir, "out.rej")
        subprocess.run(
            ["patch", "-p1", f"--fuzz={fuzz}", "--forward", "--no-backup-if-mismatch",
             "-r", rej, "-i", pf],
            cwd=workdir, capture_output=True, text=True)
        with open(target, errors="replace") as f:
            result = f.read()
        rej_text = ""
        if os.path.exists(rej):
            with open(rej, errors="replace") as f:
                rej_text = f.read()
        _, rej_hunks = split_hunks(rej_text)
        subsumed = [h for h in rej_hunks if hunk_subsumed(h, upstream_lines)]
        genuine = [h for h in rej_hunks if h not in subsumed]
        stats = {"total": total, "applied": total - len(rej_hunks),
                 "subsumed": len(subsumed), "conflict": len(genuine)}
        return result, stats, genuine
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_reconcile(args):
    con = db_connect(args.db)
    deriv = os.path.expanduser(args.tree)
    ref = os.path.expanduser(args.ref_tree)
    os.makedirs(args.out, exist_ok=True)
    rows = con.execute(
        "SELECT sha, sem_upstream, subject FROM commits "
        "WHERE sem_action='KEEP_DELTA' AND sem_upstream IS NOT NULL ORDER BY seq").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"reconciling {len(rows)} KEEP_DELTA commits against their upstream form")
    con.execute("DELETE FROM reconcile_files")
    summary = {"clean": 0, "subsumed": 0, "conflict": 0, "upstream_absent": 0}
    per_commit = []
    tot_h = {"total": 0, "applied": 0, "subsumed": 0, "conflict": 0}
    for sha, up, subj in rows:
        files = [r[0] for r in con.execute(
            "SELECT path FROM commit_files WHERE sha=?", (sha,))]
        up_paths = up_touched_paths(ref, up)
        cdir = os.path.join(args.out, sha[:12])
        os.makedirs(cdir, exist_ok=True)
        c_clean = c_conf = c_abs = c_sub = 0
        for path in files:
            ours = blob(deriv, sha, path)
            if ours is None:
                continue
            # locate upstream path (same path, else the single upstream file if 1:1)
            up_path = path if path in up_paths else (
                list(up_paths)[0] if len(up_paths) == 1 else None)
            theirs = blob(ref, up, up_path) if up_path else None
            flat = path.replace("/", "__")
            if theirs is None:
                status, conflicts = "upstream_absent", 0
                c_abs += 1
            else:
                result, st, genuine = transplant_hunks(deriv, sha, path, theirs)
                for k in tot_h:
                    tot_h[k] += st[k]
                conflicts = st["conflict"]
                if conflicts:
                    status = "conflict"
                    c_conf += 1
                    with open(os.path.join(cdir, flat + ".rej"), "w") as f:
                        f.write("".join(genuine))
                elif st["applied"] == 0 and st["subsumed"]:
                    # every hunk already present upstream -> nothing left to carry
                    status = "subsumed"
                    c_sub += 1
                else:
                    status = "clean"
                    c_clean += 1
                if status != "subsumed":
                    # delta to apply ON TOP of upstream = diff(theirs -> result)
                    import tempfile
                    ta = tempfile.NamedTemporaryFile("w", suffix=".up", delete=False)
                    tb = tempfile.NamedTemporaryFile("w", suffix=".rs", delete=False)
                    ta.write(theirs); ta.close(); tb.write(result); tb.close()
                    dp = subprocess.run(
                        ["diff", "-u", "--label", f"a/{path}", "--label", f"b/{path}",
                         ta.name, tb.name], capture_output=True, text=True)
                    if dp.stdout.strip():
                        with open(os.path.join(cdir, flat + ".delta.patch"), "w") as f:
                            f.write(dp.stdout)
                    os.unlink(ta.name); os.unlink(tb.name)
            con.execute(
                "INSERT OR REPLACE INTO reconcile_files(sha,path,up_path,status,conflicts) "
                "VALUES(?,?,?,?,?)", (sha, path, up_path, status, conflicts))
        if c_conf:
            overall = "conflict"
        elif c_abs and not (c_clean or c_sub):
            overall = "upstream_absent"
        elif c_sub and not c_clean:
            overall = "subsumed"
        else:
            overall = "clean"
        summary[overall] += 1
        per_commit.append((sha, overall, c_clean, c_conf, c_sub, c_abs, subj))
    con.commit()

    print("\n=== Phase 3 reconciliation (per KEEP_DELTA commit) ===")
    print(f"{'sha':<13}{'result':<16}{'cln/cnf/sub/abs':<17} subject")
    for sha, overall, cc, cf, cs, ca, subj in per_commit:
        print(f"{sha[:12]:<13}{overall:<16}{f'{cc}/{cf}/{cs}/{ca}':<17} {subj[:50]}")
    print("\nHunk-level totals:")
    print(f"  hunks total={tot_h['total']}  applied={tot_h['applied']}  "
          f"already-upstream={tot_h['subsumed']}  genuine-conflict={tot_h['conflict']}")
    print("\nAggregate (per commit):")
    print(f"  auto-mergeable (delta applies cleanly on upstream) : {summary['clean']}")
    print(f"  fully subsumed by upstream (redundant -> DROP)     : {summary['subsumed']}")
    print(f"  needs manual reconciliation (true conflicts)       : {summary['conflict']}")
    print(f"  upstream file not found (treat as derivative-only) : {summary['upstream_absent']}")
    print(f"\nMerged files + markers written under {args.out}/<sha>/")


# ---- churn (Phase 1: collapse add/revert/re-add chains) ---------------------
RE_REVERT = re.compile(r'^\s*(?:[A-Z][A-Z0-9_]+:\s*)*Revert\s+"(.+)"\s*$')


def fill_patch_ids(con, tree, base, head):
    """Bulk-compute git patch-id for every OEM commit (cheap: one pipeline)."""
    proc = subprocess.run(
        f"git -C {tree} log -p --no-merges {base}..{head} | git -C {tree} patch-id --stable",
        shell=True, capture_output=True, text=True, errors="replace")
    n = 0
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            pid, sha = parts
            con.execute("UPDATE commits SET patch_id=? WHERE sha=?", (pid, sha))
            n += 1
    con.commit()
    return n


def reverse_patch_id(tree, sha):
    """patch-id of a commit's diff applied backwards; used to prove a revert
    is the exact inverse of its target."""
    proc = subprocess.run(
        f"git -C {tree} diff {sha} {sha}^ | git -C {tree} patch-id --stable",
        shell=True, capture_output=True, text=True, errors="replace")
    out = proc.stdout.split()
    return out[0] if out else None


# A revert is frequently *not* a byte-exact inverse of its target, because the
# original was reworked by an intervening rebase conflict resolution: copyright
# years get bumped instead of restored, whitespace drifts, or the file is renamed
# underneath it. patch-id is sensitive to all three, so it under-reports
# cancellation. The content test below compares the four line multisets of the
# two diffs, which is immune to filename, whitespace and hunk structure.
COSMETIC_LINE = re.compile(r'copyright|^\s*[/*#]|^\s*$', re.I)


def diff_lines(tree, sha, sign):
    out = Counter()
    for ln in git(tree, "diff", f"{sha}^", sha).splitlines():
        if ln.startswith(sign) and not ln.startswith(sign * 3):
            out[ln[1:].rstrip()] += 1
    return out


def revert_residue(tree, orig, rev):
    """Lines left over after composing a commit with its revert.

    Empty  -> exact content inverse (any patch-id mismatch was structural).
    All cosmetic -> functionally net-zero; the drift is a conflict artifact.
    """
    oa, od = diff_lines(tree, orig, "+"), diff_lines(tree, orig, "-")
    ra, rd = diff_lines(tree, rev, "+"), diff_lines(tree, rev, "-")
    resid = (oa - rd) + (rd - oa) + (od - ra) + (ra - od)
    return list(resid.elements())


def cmd_churn(args):
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    if args.refresh_patch_ids or not con.execute(
            "SELECT COUNT(*) FROM commits WHERE patch_id IS NOT NULL").fetchone()[0]:
        base = resolve_base(tree, args.base)
        print("computing patch-ids ...")
        print(f"  {fill_patch_ids(con, tree, base, args.head)} patch-ids recorded")

    rows = con.execute(
        "SELECT sha,seq,subject,clean_subject,patch_id FROM commits ORDER BY seq").fetchall()
    by_seq = {r[1]: r for r in rows}
    # index by subject (both raw and SAUCE-stripped) for revert matching
    subj_idx, clean_idx = {}, {}
    for sha, seq, subj, clean, pid in rows:
        subj_idx.setdefault(subj.strip(), []).append(seq)
        if clean:
            clean_idx.setdefault(clean.strip(), []).append(seq)

    con.execute("UPDATE commits SET churn_action=NULL, churn_reason=NULL")
    marks = {}

    _fcache = {}

    def files_of(sha):
        if sha not in _fcache:
            _fcache[sha] = {p for (p,) in con.execute(
                "SELECT path FROM commit_files WHERE sha=?", (sha,))}
        return _fcache[sha]

    def mark(seq, action, reason):
        marks[seq] = (action, reason)

    pairs = net_zero = superseded = external = partial = straddled = traced = 0
    structural = cosmetic = 0
    cosmetic_lines = []
    for sha, seq, subj, clean, pid in rows:
        m = RE_REVERT.match(subj)
        if not m:
            continue
        target = m.group(1).strip()
        cands = [s for s in subj_idx.get(target, []) if s < seq]
        if not cands:
            cands = [s for s in clean_idx.get(target, []) if s < seq]
        if not cands:
            external += 1  # reverts something in the generic base, not OEM churn
            continue
        orig = max(cands)
        pairs += 1
        o_pid = by_seq[orig][4]
        # Proof obligation: the pair must compose to nothing. Try the cheap
        # patch-id identity first, then fall back to the content test, which
        # tolerates conflict-resolution drift (renames, whitespace, copyright).
        cancels, why = True, "exact inverse"
        if not o_pid or reverse_patch_id(tree, sha) != o_pid:
            resid = revert_residue(tree, by_seq[orig][0], sha)
            functional = [l for l in resid if not COSMETIC_LINE.search(l)]
            if not resid:
                structural += 1
                why = "exact content inverse (rename/whitespace only)"
            elif not functional and not args.strict_revert:
                cosmetic += 1
                cosmetic_lines.extend(resid)
                why = f"cosmetic residue only ({len(resid)} lines)"
            else:
                cancels = False
                functional = functional or resid
        if not cancels:
            partial += 1
            mark(seq, "KEEP_PARTIAL_REVERT",
                 f"functional residue vs seq {orig}: {functional[0].strip()[:40]}")
            continue
        # Tree proof. The diff-level tests above say the pair composes to
        # nothing in isolation; this says the pair left no trace in the tree it
        # actually lived in. It subsumes the straddle question for correctness:
        # if an intervening commit built on the add and R did not undo that,
        # the blobs differ and the pair is kept.
        pair_files = files_of(by_seq[orig][0]) | files_of(sha)
        ok, offender = pair_leaves_no_trace(tree, by_seq[orig][0], sha, pair_files)
        if not ok:
            traced += 1
            mark(orig, "KEEP_LEAVES_TRACE",
                 f"reverted at seq {seq} but {offender} differs at the endpoints")
            mark(seq, "KEEP_LEAVES_TRACE",
                 f"revert of seq {orig}; {offender} not restored")
            continue
        # Straddle guard. Even a provably net-zero pair can cost conflicts:
        # commits landing between A and R were authored against the post-A
        # tree, so removing A makes them rebase against content that never
        # existed. Correctness is already assured above; this is about churn.
        straddlers = [s for s in range(orig + 1, seq)
                      if s in by_seq and files_of(by_seq[s][0]) & pair_files]
        if straddlers:
            straddled += 1
            victim = by_seq[straddlers[0]]
            mark(orig, "KEEP_STRADDLED",
                 f"reverted at seq {seq} but seq {straddlers[0]} builds on it")
            mark(seq, "KEEP_STRADDLED",
                 f"revert of seq {orig}; {len(straddlers)} commit(s) depend on it, "
                 f"e.g. {victim[0][:12]}")
            continue
        # was it re-added after the revert?
        readd = [s for s in subj_idx.get(target, []) + clean_idx.get(target, [])
                 if s > seq]
        if not readd and o_pid:
            readd = [r[1] for r in rows if r[1] > seq and r[4] == o_pid]
        if readd:
            superseded += 1
            mark(orig, "DROP_SUPERSEDED",
                 f"reverted at seq {seq}, re-added at seq {min(readd)}")
            mark(seq, "DROP_SUPERSEDED", f"revert of seq {orig}; re-added later")
        else:
            net_zero += 1
            mark(orig, "DROP_NET_ZERO", f"reverted at seq {seq} ({why})")
            mark(seq, "DROP_NET_ZERO", f"revert of seq {orig}; {why}")

    # duplicate patch content (e.g. a FROMLIST + FROMGIT pair of the same change)
    dup_groups = con.execute(
        "SELECT patch_id, COUNT(*) FROM commits WHERE patch_id IS NOT NULL "
        "AND patch_id != '' GROUP BY patch_id HAVING COUNT(*) > 1").fetchall()
    dups = 0
    for pid, _ in dup_groups:
        seqs = [r[0] for r in con.execute(
            "SELECT seq FROM commits WHERE patch_id=? ORDER BY seq", (pid,))]
        for s in seqs[:-1]:  # keep the last application
            if s not in marks:
                mark(s, "DROP_DUPLICATE",
                     f"identical patch-id re-applied at seq {seqs[-1]}")
                dups += 1

    for seq, (action, reason) in marks.items():
        con.execute("UPDATE commits SET churn_action=?,churn_reason=? WHERE seq=?",
                    (action, reason, seq))
    con.commit()

    print(f"\n=== Phase 1: churn cancellation ===")
    print(f"  revert pairs found inside the OEM stack : {pairs}")
    print(f"    -> net-zero (add+revert, never re-added): {net_zero} pairs")
    print(f"    -> superseded (add+revert+re-add)       : {superseded} pairs")
    print(f"       of which proven by content, not patch-id:")
    print(f"         exact inverse modulo rename/whitespace: {structural}")
    print(f"         cosmetic residue only (copyright etc.) : {cosmetic}")
    print(f"    -> partial revert, real residue (KEPT)   : {partial}")
    print(f"    -> leaves a trace in the tree (KEPT)     : {traced}")
    print(f"    -> straddled by dependent commits (KEPT) : {straddled}")
    print(f"       (net-zero at the endpoints, but commits in between were")
    print(f"        authored against the post-add tree -- dropping strands them)")
    print(f"  reverts of generic-base commits (kept)   : {external}")
    print(f"  duplicate patch-id applications dropped  : {dups}")
    if cosmetic_lines:
        print(f"\n  NOTE: {cosmetic} pair(s) cancelled with {len(cosmetic_lines)} lines of "
              f"cosmetic residue.\n  The rebuilt tree will differ from D by exactly "
              f"these lines (conflict-resolution\n  drift such as copyright bumps). "
              f"Use --strict-revert to keep those pairs instead.")
        for l in cosmetic_lines[:3]:
            print(f"    | {l.strip()[:66]}")
    total = con.execute("SELECT COUNT(*) FROM commits WHERE churn_action "
                        "LIKE 'DROP%'").fetchone()[0]
    print(f"\n  {total} commits removed from the replay set as pure churn")
    for a, n in con.execute("SELECT churn_action,COUNT(*) FROM commits "
                            "WHERE churn_action IS NOT NULL GROUP BY churn_action"):
        print(f"    {a:<20} {n}")
    print("\n  sample:")
    for sha, subj, r in con.execute(
            "SELECT sha,subject,churn_reason FROM commits "
            "WHERE churn_action LIKE 'DROP%' ORDER BY seq LIMIT 6"):
        print(f"    {sha[:12]} {subj[:48]:<48} [{r}]")


def blob_at(tree, rev, path):
    """Blob sha of `path` at `rev`, or None when absent."""
    p = subprocess.run(["git", "-C", tree, "rev-parse", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def pair_leaves_no_trace(tree, orig_sha, rev_sha, paths):
    """Direct tree proof that an add/revert pair is net-zero.

    patch-id and line-composition tests compare the two *diffs*; this compares
    the two *trees*. If every file the pair touches has an identical blob
    immediately before the add and immediately after the revert, the pair
    provably contributed nothing, whatever happened in between.
    Returns (True, "") or (False, offending_path).
    """
    for path in sorted(paths):
        if blob_at(tree, f"{orig_sha}^", path) != blob_at(tree, rev_sha, path):
            return False, path
    return True, ""


# ---- graduate (did the patch land in the NEW generic base?) -----------------
def cmd_graduate(args):
    """Compare the OEM stack against the generic delta G_old..G_new.

    This is the case that makes derivative rebases painful: a patch supplied to
    the OEM in preliminary form later lands upstream in a *different* form under
    the same subject. git rebase skips only byte-identical patches, so the
    modified ones collide instead of being dropped.
    """
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    base = resolve_base(tree, args.base)
    onto = resolve_base(tree, args.onto)

    print(f"indexing generic delta {base[:12]}..{onto[:12]} ...")
    pid_gen, subj_gen = {}, {}
    proc = subprocess.run(
        f"git -C {tree} log -p --no-merges {base}..{onto} | git -C {tree} patch-id --stable",
        shell=True, capture_output=True, text=True, errors="replace")
    for line in proc.stdout.splitlines():
        p = line.split()
        if len(p) == 2:
            pid_gen[p[0]] = p[1]
    for line in git(tree, "log", "--no-merges", "--format=%H\t%s",
                    f"{base}..{onto}").splitlines():
        h, _, s = line.partition("\t")
        subj_gen.setdefault(parse_prefixes(s)[1], []).append(h)
    # a patch generic *reverted* is now contrary to the new base, not merely
    # redundant -- carrying it forward re-applies something generic rejected.
    rev_gen = {}
    for clean, hs in subj_gen.items():
        m = RE_REVERT.match(clean)
        if m:
            rev_gen.setdefault(m.group(1).strip(), []).append(hs[0])
    print(f"  {len(pid_gen)} patch-ids, {len(subj_gen)} distinct subjects, "
          f"{len(rev_gen)} reverts in the generic delta")

    con.execute("UPDATE commits SET grad_action=NULL, grad_reason=NULL")
    ident = modified = reverted = 0
    for sha, pid, clean in con.execute(
            "SELECT sha,patch_id,clean_subject FROM commits "
            "WHERE category != 'PACKAGING' ORDER BY seq").fetchall():
        if pid and pid in pid_gen:
            ident += 1
            con.execute("UPDATE commits SET grad_action='DROP_GRADUATED',"
                        "grad_reason=? WHERE sha=?",
                        (f"identical patch-id to generic {pid_gen[pid][:12]}", sha))
        elif clean and clean in rev_gen:
            reverted += 1
            con.execute("UPDATE commits SET grad_action='REVIEW_REVERTED_IN_GENERIC',"
                        "grad_reason=? WHERE sha=?",
                        (f"generic {rev_gen[clean][0][:12]} reverts this subject", sha))
        elif clean and clean in subj_gen:
            modified += 1
            con.execute("UPDATE commits SET grad_action='REVIEW_GRADUATED_MODIFIED',"
                        "grad_reason=? WHERE sha=?",
                        (f"same subject as generic {subj_gen[clean][0][:12]}, "
                         f"different content", sha))
    con.commit()

    print(f"\n=== Phase 2a: graduation into the new generic base ===")
    print(f"  DROP_GRADUATED (identical patch-id)        : {ident}")
    print(f"    -> git rebase would skip these by itself")
    print(f"  REVIEW_GRADUATED_MODIFIED (same subject,   : {modified}")
    print(f"     different content)                        ")
    print(f"    -> these are the ones that COLLIDE; git rebase cannot skip them")
    print(f"  REVIEW_REVERTED_IN_GENERIC                 : {reverted}")
    print(f"    -> generic rejected this change; carrying it forward re-applies it")
    for sha, subj, act in con.execute(
            "SELECT sha,subject,grad_action FROM commits "
            "WHERE grad_action LIKE 'REVIEW%' ORDER BY seq LIMIT 12"):
        print(f"       {act[7:]:<26} {sha[:12]} {subj[:50]}")
    return ident, modified


# ---- cluster (Phase 4: independently-mergeable feature sets) ----------------
KEEP_SET_SQL = ("category NOT IN ('PACKAGING','UPSTREAMED') "
                "AND (sem_action IS NULL OR sem_action != 'DROP') "
                "AND (churn_action IS NULL OR churn_action NOT LIKE 'DROP%') "
                "AND (grad_action IS NULL OR grad_action NOT LIKE 'DROP%')")


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        root = x
        while self.p[root] != root:
            root = self.p[root]
        while self.p[x] != root:  # path compression
            self.p[x], x = root, self.p[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def subsystem_of(path):
    """Coarse subsystem label from a path (2-3 leading components)."""
    parts = path.split("/")
    if parts[0] == "arch" and len(parts) > 3:
        return "/".join(parts[:4])
    if parts[0] in ("drivers", "sound", "net", "fs", "kernel", "include") and len(parts) > 2:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def cmd_cluster(args):
    con = db_connect(args.db)
    shas = [r[0] for r in con.execute(
        f"SELECT sha FROM commits WHERE {KEEP_SET_SQL} ORDER BY seq")]
    keep = set(shas)
    print(f"clustering {len(keep)} keep-set commits "
          f"(excludes PACKAGING, UPSTREAMED, DROP verdicts, and churn)")

    # file -> commits, restricted to the keep set
    f2c = {}
    c2f = {}
    for sha, path in con.execute("SELECT sha,path FROM commit_files"):
        if sha not in keep:
            continue
        f2c.setdefault(path, []).append(sha)
        c2f.setdefault(sha, []).append(path)

    # Hub files (Makefile/Kconfig/defconfig/changelog/big dtsi) are integration
    # points touched by many unrelated features. Coupling on them would merge
    # every feature into one blob, so they are excluded from the relation and
    # reported separately as the integration surface.
    hubs = {p for p, cs in f2c.items() if len(cs) >= args.hub_threshold}
    print(f"{len(hubs)} hub files (touched by >= {args.hub_threshold} commits) "
          f"treated as integration points")

    uf = UnionFind()
    for sha in shas:
        uf.find(sha)
    for path, cs in f2c.items():
        if path in hubs:
            continue
        first = cs[0]
        for other in cs[1:]:
            uf.union(first, other)

    # optional: also couple commits sharing a Change-Id patchset series
    if not args.no_changeid:
        cid_groups = {}
        for sha, cid in con.execute(
                f"SELECT sha,change_id FROM commits WHERE {KEEP_SET_SQL} AND change_id IS NOT NULL"):
            cid_groups.setdefault(cid, []).append(sha)
        for cid, cs in cid_groups.items():
            for other in cs[1:]:
                uf.union(cs[0], other)

    comps = {}
    for sha in shas:
        comps.setdefault(uf.find(sha), []).append(sha)
    ordered = sorted(comps.values(), key=len, reverse=True)

    con.execute("UPDATE commits SET cluster_id=NULL")
    for cid, members in enumerate(ordered):
        for sha in members:
            con.execute("UPDATE commits SET cluster_id=? WHERE sha=?", (cid, sha))
    con.commit()

    singles = sum(1 for m in ordered if len(m) == 1)
    print(f"\n=== Phase 4: {len(ordered)} independently-mergeable clusters "
          f"({singles} are single commits) ===\n")
    print(f"{'id':<5}{'commits':>8}  {'subsystem (dominant)':<42} hub files")
    shown = args.top or len(ordered)
    for cid, members in enumerate(ordered[:shown]):
        subs = {}
        hub_hits = set()
        for sha in members:
            for p in c2f.get(sha, []):
                if p in hubs:
                    hub_hits.add(p)
                else:
                    s = subsystem_of(p)
                    subs[s] = subs.get(s, 0) + 1
        dom = sorted(subs.items(), key=lambda kv: -kv[1])[:2]
        label = ", ".join(s for s, _ in dom) or "(hub-only)"
        print(f"{cid:<5}{len(members):>8}  {label[:42]:<42} {len(hub_hits)}")

    big = sum(len(m) for m in ordered if len(m) > 1)
    print(f"\nLargest cluster: {len(ordered[0])} commits "
          f"({100*len(ordered[0])//max(1,len(shas))}% of keep set)")
    print(f"Clusters with >1 commit hold {big} commits; {singles} are standalone.")
    print(f"\nIntegration surface: {len(hubs)} hub files must be merged once, "
          f"centrally (e.g. defconfig, Makefile, changelog).")

    # Independence invariant: if a non-hub file were touched by two clusters,
    # those clusters could not be developed/merged in isolation.
    owner, violations = {}, []
    for cid, members in enumerate(ordered):
        for sha in members:
            for p in c2f.get(sha, []):
                if p in hubs:
                    continue
                prev = owner.setdefault(p, cid)
                if prev != cid:
                    violations.append((p, prev, cid))
    if violations:
        print(f"\n!! INVARIANT VIOLATED: {len(violations)} non-hub files span "
              f"two clusters; clusters are NOT independent")
        for p, a, b in violations[:5]:
            print(f"   {p} in clusters {a} and {b}")
    else:
        print(f"\nInvariant verified: 0 of {len(owner)} non-hub files span two "
              f"clusters -- every cluster provably merges in isolation.")
    print("Query a cluster:  SELECT sha,subject FROM commits WHERE cluster_id=<id>;")


# ---- reconstruct (Phase 5: build independent topic branches) ----------------
def worktree_setup(tree, path, base):
    if os.path.exists(path):
        run(["git", "-C", tree, "worktree", "remove", "--force", path], check=False)
    run(["git", "-C", tree, "worktree", "add", "--detach", path, base])


def worktree_teardown(tree, path):
    run(["git", "-C", tree, "worktree", "remove", "--force", path], check=False)


def cmd_reconstruct(args):
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    con.execute("CREATE TABLE IF NOT EXISTS reconstruct ("
                "cluster_id INTEGER, sha TEXT, status TEXT, conflicts TEXT)")
    base = resolve_base(tree, args.onto or args.base)
    hubs = {r[0] for r in con.execute(
        "SELECT path FROM commit_files GROUP BY path HAVING COUNT(DISTINCT sha) >= ?",
        (args.hub_threshold,))}

    clusters = [r[0] for r in con.execute(
        "SELECT cluster_id FROM commits WHERE cluster_id IS NOT NULL "
        "GROUP BY cluster_id ORDER BY COUNT(*) DESC")]
    if args.cluster is not None:
        clusters = [args.cluster]
    elif args.top:
        clusters = clusters[:args.top]

    wt = os.path.abspath(os.path.expanduser(args.worktree))
    print(f"reconstructing {len(clusters)} cluster(s) onto {base[:12]} in {wt}")
    worktree_setup(tree, wt, base)
    con.execute("DELETE FROM reconstruct")

    tot = {"clean": 0, "conflict": 0, "empty": 0}
    hub_only = non_hub = 0
    try:
        for cid in clusters:
            members = [r for r in con.execute(
                "SELECT sha,subject FROM commits WHERE cluster_id=? ORDER BY seq",
                (cid,))]
            run(["git", "-C", wt, "checkout", "--detach", base], check=False)
            run(["git", "-C", wt, "checkout", "-B", f"rederive/c{cid:04d}"],
                check=False)
            c_clean = c_conf = 0
            for sha, subj in members:
                r = run(["git", "-C", wt, "cherry-pick", "-x",
                         "--allow-empty", "--keep-redundant-commits", sha],
                        check=False)
                if r.returncode == 0:
                    status, conf = "clean", ""
                    c_clean += 1
                    tot["clean"] += 1
                else:
                    files = [l[3:].strip() for l in run(
                        ["git", "-C", wt, "status", "--porcelain"],
                        check=False).stdout.splitlines()
                        if l.startswith(("UU", "AA", "DU", "UD", "AU", "UA"))]
                    status, conf = "conflict", ";".join(files)
                    c_conf += 1
                    tot["conflict"] += 1
                    if files and all(f in hubs for f in files):
                        hub_only += 1
                    elif files:
                        non_hub += 1
                    # take the derivative's version so replay can continue
                    run(["git", "-C", wt, "checkout", "--theirs", "--"] + files,
                        check=False)
                    run(["git", "-C", wt, "add", "-A"], check=False)
                    r2 = run(["git", "-C", wt, "-c", "core.editor=true",
                              "cherry-pick", "--continue"], check=False)
                    if r2.returncode != 0:
                        run(["git", "-C", wt, "cherry-pick", "--skip"], check=False)
                con.execute("INSERT INTO reconstruct VALUES (?,?,?,?)",
                            (cid, sha, status, conf))
            con.commit()
            if args.verbose or c_conf:
                print(f"  cluster {cid:<5} {len(members):>4} commits  "
                      f"clean={c_clean:<4} conflict={c_conf}")
    finally:
        if not args.keep_worktree:
            worktree_teardown(tree, wt)
        if not args.keep_branches:
            for cid in clusters:
                run(["git", "-C", tree, "branch", "-D", f"rederive/c{cid:04d}"],
                    check=False)
        else:
            print(f"\n  kept {len(clusters)} topic branches as rederive/c*")

    done = tot["clean"] + tot["conflict"]
    print(f"\n=== Phase 5: replayed {done} commits across {len(clusters)} clusters ===")
    print(f"  clean cherry-picks   : {tot['clean']} "
          f"({100*tot['clean']//max(1,done)}%)")
    print(f"  conflicted           : {tot['conflict']}")
    print(f"\n  Conflict locality (the Phase 4 independence prediction):")
    print(f"    conflicts confined to hub files : {hub_only}")
    print(f"    conflicts touching non-hub files: {non_hub}")
    if not non_hub:
        print("    -> every conflict is on the shared integration surface, "
              "as predicted by the clustering invariant.")
        return

    # A non-hub conflict means a predecessor was missing. The usual culprit is
    # a Phase 2/3 DROP verdict that removed a commit the successor built on --
    # valid only if the replay base already contains the upstream version.
    print("\n  Attributing non-hub conflicts:")
    regressions = 0
    for sha, conf in con.execute(
            "SELECT sha,conflicts FROM reconstruct WHERE status='conflict'"):
        files = [f for f in conf.split(";") if f and f not in hubs]
        if not files:
            continue
        seq = con.execute("SELECT seq FROM commits WHERE sha=?", (sha,)).fetchone()[0]
        culprits = []
        for f in files:
            culprits += con.execute(
                "SELECT c.sha,c.category,c.churn_action,c.sem_action,c.seq "
                "FROM commit_files cf JOIN commits c ON c.sha=cf.sha "
                "WHERE cf.path=? AND c.seq<? AND c.cluster_id IS NULL",
                (f, seq)).fetchall()
        print(f"    {sha[:12]}  non-hub: {', '.join(f.split('/')[-1] for f in files)}")
        if culprits:
            regressions += 1
            for csha, cat, ch, sem, cseq in culprits[:3]:
                why = sem or ch or cat
                print(f"        caused by dropped predecessor {csha[:12]} "
                      f"(seq {cseq}, dropped as {why})")
        else:
            print("        no dropped predecessor; genuine ordering dependency")
    if regressions:
        print(f"\n  {regressions} of {non_hub} non-hub conflicts are DROP "
              f"regressions, not clustering errors.")
        print("  A DROP is only sound if the replay base already contains the "
              "upstream commit.\n  Re-run `reconstruct --onto <new-generic>` to "
              "confirm, or revoke the drop.")


# ---- verify (Phase 6: acceptance gate) --------------------------------------
DISPOSITIONS = [
    ("kept (clustered)",      "cluster_id IS NOT NULL"),
    ("dropped: churn",        "cluster_id IS NULL AND churn_action LIKE 'DROP%'"),
    ("dropped: semantic",     "cluster_id IS NULL AND (churn_action IS NULL OR churn_action NOT LIKE 'DROP%') AND sem_action='DROP'"),
    ("dropped: packaging",    "cluster_id IS NULL AND (churn_action IS NULL OR churn_action NOT LIKE 'DROP%') AND (sem_action IS NULL OR sem_action!='DROP') AND category='PACKAGING'"),
    ("dropped: upstreamed",   "cluster_id IS NULL AND (churn_action IS NULL OR churn_action NOT LIKE 'DROP%') AND (sem_action IS NULL OR sem_action!='DROP') AND category='UPSTREAMED'"),
]


def cmd_verify(args):
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    base = resolve_base(tree, args.base)
    total = con.execute("SELECT COUNT(*) FROM commits").fetchone()[0]

    print(f"=== Phase 6: acceptance gate (base {base[:12]}) ===\n")
    print("1. Disposition partition -- every OEM commit accounted for exactly once")
    seen = 0
    for label, where in DISPOSITIONS:
        n = con.execute(f"SELECT COUNT(*) FROM commits WHERE {where}").fetchone()[0]
        seen += n
        print(f"   {label:<24} {n:>6}")
    print(f"   {'TOTAL':<24} {seen:>6} of {total}")
    if seen != total:
        orphan = con.execute(
            "SELECT sha,subject,category FROM commits WHERE cluster_id IS NULL "
            "AND (churn_action IS NULL OR churn_action NOT LIKE 'DROP%') "
            "AND (sem_action IS NULL OR sem_action!='DROP') "
            "AND category NOT IN ('PACKAGING','UPSTREAMED') LIMIT 5").fetchall()
        print(f"   FAIL: {total-seen} commits unaccounted for")
        for s, subj, c in orphan:
            print(f"      {s[:12]} [{c}] {subj[:56]}")
    else:
        print("   PASS: partition is complete and disjoint.")

    # 2. Every dropped commit's content must already exist in the replay base,
    #    otherwise dropping it changes the resulting tree.
    print("\n2. Drop soundness -- is each dropped change already in the base?")
    drops = con.execute(
        "SELECT sha,subject FROM commits WHERE cluster_id IS NULL "
        "AND sem_action='DROP' ORDER BY seq").fetchall()
    sound = unsound = 0
    bad = []
    for sha, subj in drops:
        paths = [r[0] for r in con.execute(
            "SELECT path FROM commit_files WHERE sha=?", (sha,))]
        ok = True
        for path in paths:
            patch = git(tree, "diff", f"{sha}^", sha, "--", path)
            _, hunks = split_hunks(patch)
            if not hunks:
                continue
            blob = git(tree, "show", f"{base}:{path}")
            if not blob:
                ok = False
                break
            up = {l.strip() for l in blob.splitlines()}
            if not all(hunk_subsumed(h, up) for h in hunks):
                ok = False
                break
        if ok:
            sound += 1
        else:
            unsound += 1
            bad.append((sha, subj))
    print(f"   sound   (content already in base, safe to drop) : {sound}")
    print(f"   UNSOUND (content absent from base, must be kept): {unsound}")
    for sha, subj in bad[:8]:
        print(f"      {sha[:12]} {subj[:64]}")
    if unsound:
        print(f"\n   These drops are only valid against a base that already "
              f"carries the upstream\n   version. Against {base[:12]} they would "
              f"silently change the tree.")
        if args.revoke_unsound:
            for sha, _ in bad:
                con.execute(
                    "UPDATE commits SET sem_action='KEEP', sem_reason="
                    "'revoked: content absent from replay base' WHERE sha=?", (sha,))
            con.commit()
            print(f"   REVOKED {len(bad)} unsound drops -> re-run `cluster`, "
                  f"then `verify` again.")

    # 3. No file may lose all of its OEM content to drops. Files whose only
    #    commits were churn-cancelled are *correctly* empty (proven net-zero);
    #    anything else means the rebuilt tree would differ from D.
    print("\n3. File coverage -- no file loses every OEM change")
    rows = con.execute(
        "SELECT cf.path, "
        " SUM(CASE WHEN c.churn_action LIKE 'DROP%' THEN 1 ELSE 0 END), COUNT(*) "
        "FROM commit_files cf JOIN commits c ON c.sha=cf.sha GROUP BY cf.path "
        "HAVING SUM(CASE WHEN c.cluster_id IS NOT NULL THEN 1 ELSE 0 END)=0 "
        "AND SUM(CASE WHEN c.category IN ('PACKAGING','UPSTREAMED') THEN 1 ELSE 0 END)=0"
    ).fetchall()
    churn_only = [r for r in rows if r[1] == r[2]]
    lost = [r for r in rows if r[1] != r[2]]
    print(f"   churn-cancelled files (correctly empty) : {len(churn_only)}")
    print(f"   AT RISK (all OEM content would be lost) : {len(lost)}")
    for p, _, n in lost[:8]:
        print(f"      {p} ({n} commits)")

    print("\n=== gate summary ===")
    fails = (seen != total) + (unsound > 0) + (len(lost) > 0)
    print(f"   partition complete : {'PASS' if seen==total else 'FAIL'}")
    print(f"   all drops sound    : {'PASS' if not unsound else 'FAIL'}")
    print(f"   file coverage      : {'PASS' if not lost else 'FAIL'}")
    print(f"\n   {'ACCEPTED' if not fails else 'NOT ACCEPTED -- ' + str(fails) + ' check(s) failed'}")


# ---- report -----------------------------------------------------------------
def cmd_report(args):
    con = db_connect(args.db)
    total = con.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    print(f"\n=== Classified inventory ({total} OEM commits) ===\n")
    print(f"{'category':<30}{'count':>7}{'avg_conf':>10}")
    print("-" * 47)
    for cat, n, c in con.execute(
            "SELECT COALESCE(category,'(unclassified)'),COUNT(*),AVG(confidence) "
            "FROM commits GROUP BY category ORDER BY COUNT(*) DESC"):
        print(f"{cat:<30}{n:>7}{(c or 0):>10.2f}")
    sem_total = con.execute("SELECT COUNT(*) FROM commits WHERE sem_verdict IS NOT NULL").fetchone()[0]
    if sem_total:
        print(f"\n=== Semantic fan-out verdicts ({sem_total} analysed) ===")
        for v, a, n in con.execute(
                "SELECT sem_verdict,sem_action,COUNT(*) FROM commits "
                "WHERE sem_verdict IS NOT NULL GROUP BY sem_verdict,sem_action ORDER BY COUNT(*) DESC"):
            print(f"  {v:<16} {a:<12} {n}")
    rec_total = con.execute("SELECT COUNT(DISTINCT sha) FROM reconcile_files").fetchone()[0]
    if rec_total:
        print(f"\n=== Phase 3 reconciliation ({rec_total} KEEP_DELTA commits) ===")
        for st, nf, nc in con.execute(
                "SELECT status, COUNT(*), SUM(conflicts) FROM reconcile_files "
                "GROUP BY status ORDER BY COUNT(*) DESC"):
            print(f"  {st:<18} files={nf:<5} conflict_hunks={nc or 0}")
    print("\nTop touched subsystems (by file path dir):")
    for d, n in con.execute(
            "SELECT substr(path,1,instr(path||'/','/')-1) AS top, COUNT(*) n "
            "FROM commit_files GROUP BY top ORDER BY n DESC LIMIT 12"):
        print(f"  {d:<24}{n}")



def main():
    p = argparse.ArgumentParser(description="re-derive: classified inventory of a derivative OEM stack")
    p.add_argument("--db", default=DB_DEFAULT)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="Phase 0: build the corpus DB")
    e.add_argument("--tree", required=True, help="derivative git tree")
    e.add_argument("--base", required=True, help="generic base ref or subject substring")
    e.add_argument("--head", default="HEAD")
    e.add_argument("--reset", action="store_true")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("classify", help="deterministic Phase 2 classification")
    c.add_argument("--generic", help="generic tree for upstream cross-match")
    c.add_argument("--generic-ref", default="HEAD", help="ref in generic tree to test ancestry against")
    c.set_defaults(func=cmd_classify)

    r = sub.add_parser("report", help="print the classified inventory summary")
    r.set_defaults(func=cmd_report)

    pk = sub.add_parser("packets", help="Phase 2 semantic: write per-commit analysis packets for the fan-out")
    pk.add_argument("--tree", required=True, help="derivative git tree")
    pk.add_argument("--ref-tree", help="reference upstream tree to locate the upstream form")
    pk.add_argument("--ref-rev", default="HEAD", help="rev in ref-tree to index (e.g. linux-next/master)")
    pk.add_argument("--categories", help="comma list; default MODIFIED_UPSTREAM_CANDIDATE,UPSTREAM_PROVENANCE")
    pk.add_argument("--out", default="packets", help="output dir for packet files")
    pk.add_argument("--limit", type=int, default=0)
    pk.add_argument("--max-chars", type=int, default=12000, help="truncate each diff")
    pk.set_defaults(func=cmd_packets)

    ig = sub.add_parser("ingest", help="Phase 2 semantic: load JSONL verdicts from the agents")
    ig.add_argument("inputs", nargs="+", help="one or more JSONL verdict files")
    ig.set_defaults(func=cmd_ingest)

    rc = sub.add_parser("reconcile", help="Phase 3: 3-way reconcile KEEP_DELTA commits onto their upstream form")
    rc.add_argument("--tree", required=True, help="derivative git tree")
    rc.add_argument("--ref-tree", required=True, help="reference upstream tree holding the landed form")
    rc.add_argument("--out", default="reconcile", help="output dir for merged files + delta patches")
    rc.add_argument("--limit", type=int, default=0)
    rc.set_defaults(func=cmd_reconcile)

    gr = sub.add_parser("graduate", help="Phase 2a: detect patches that landed in the NEW generic base")
    gr.add_argument("--tree", required=True, help="tree containing both generic bases")
    gr.add_argument("--base", required=True, help="old generic base G_old")
    gr.add_argument("--onto", required=True, help="new generic base G_new (rebase target)")
    gr.set_defaults(func=cmd_graduate)

    cl = sub.add_parser("cluster", help="Phase 4: group keep-set commits into independently-mergeable feature sets")
    cl.add_argument("--hub-threshold", type=int, default=15,
                    help="a file touched by >= N commits is an integration point, not a coupling edge")
    cl.add_argument("--no-changeid", action="store_true", help="do not couple commits sharing a Change-Id")
    cl.add_argument("--top", type=int, default=25, help="how many clusters to print")
    cl.set_defaults(func=cmd_cluster)

    ch = sub.add_parser("churn", help="Phase 1: collapse add/revert/re-add chains and duplicate patches")
    ch.add_argument("--tree", required=True, help="derivative git tree")
    ch.add_argument("--base", required=True, help="generic base ref or subject substring")
    ch.add_argument("--head", default="HEAD")
    ch.add_argument("--refresh-patch-ids", action="store_true")
    ch.add_argument("--strict-revert", action="store_true",
                    help="require a byte-exact inverse; do not tolerate cosmetic drift")
    ch.set_defaults(func=cmd_churn)

    rc = sub.add_parser("reconstruct", help="Phase 5: replay clusters as independent topic branches")
    rc.add_argument("--tree", required=True, help="derivative git tree")
    rc.add_argument("--base", required=True, help="generic base ref or subject substring")
    rc.add_argument("--onto", help="replay onto this ref instead (e.g. the new generic)")
    rc.add_argument("--cluster", type=int, help="single cluster id")
    rc.add_argument("--top", type=int, help="N largest clusters")
    rc.add_argument("--hub-threshold", type=int, default=15)
    rc.add_argument("--worktree", default="/tmp/rederive-wt")
    rc.add_argument("--keep-worktree", action="store_true")
    rc.add_argument("--keep-branches", action="store_true",
                    help="retain the rederive/c* topic branches (the deliverable)")
    rc.add_argument("--verbose", action="store_true")
    rc.set_defaults(func=cmd_reconstruct)

    vf = sub.add_parser("verify", help="Phase 6: acceptance gate (partition, drop soundness, coverage)")
    vf.add_argument("--tree", required=True, help="derivative git tree")
    vf.add_argument("--base", required=True, help="replay base ref or subject substring")
    vf.add_argument("--revoke-unsound", action="store_true",
                    help="flip drops whose content is absent from the base back to KEEP")
    vf.set_defaults(func=cmd_verify)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
