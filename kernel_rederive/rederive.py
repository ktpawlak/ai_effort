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
import json
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
-- Identity keys recorded at rederive time, consumed at rebase time. kind:
--   up   upstream (mainline/linux-next) commit sha this OEM commit carries
--   subj SAUCE-stripped subject
--   pid  stable patch-id
--   base generic commit (already in G_old) this OEM commit duplicates
CREATE TABLE IF NOT EXISTS anchors(sha TEXT, kind TEXT, value TEXT, how TEXT);
CREATE INDEX IF NOT EXISTS idx_anchors_sha ON anchors(sha);
"""

SEM_COLS = {"sem_verdict": "TEXT", "sem_action": "TEXT",
            "sem_reason": "TEXT", "sem_upstream": "TEXT", "cluster_id": "INTEGER",
            "churn_action": "TEXT", "churn_reason": "TEXT",
            "grad_action": "TEXT", "grad_reason": "TEXT",
            "basedup_action": "TEXT", "basedup_reason": "TEXT",
            "squash_into": "TEXT"}

# any 40-hex sha introduced as a commit reference in a body: "commit X upstream",
# "(cherry picked from commit X)", "(backported from commit X ...)", "[ Upstream commit X ]"
RE_CITED = re.compile(r"commit\s+([0-9a-f]{40})", re.I)


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


# ---- anchor (blind: record upstream identity of every OEM commit) -----------
def git_records(tree, fmt, *rev):
    """`git log` with \\x1e-separated records and \\x1f-separated fields."""
    out = git(tree, "log", "--no-merges", f"--format={fmt}%x1e", *rev)
    return [r.strip("\n").split("\x1f") for r in out.split("\x1e") if r.strip()]


def stream_patch_ids(tree, *rev):
    proc = subprocess.run(
        f"git -C {tree} log -p --no-merges {' '.join(rev)} | git -C {tree} patch-id --stable",
        shell=True, capture_output=True, text=True, errors="replace")
    out = {}
    for line in proc.stdout.splitlines():
        p = line.split()
        if len(p) == 2:
            out.setdefault(p[1], p[0])  # sha -> pid
    return out


def cmd_anchor(args):
    """Record, for every OEM commit, the identities a FUTURE base could carry it
    under. Uses only the derivative and the upstream reference -- never a future
    generic base -- so it is legal at rederive time."""
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    ref = os.path.expanduser(args.ref_tree)
    base = resolve_base(tree, args.base)
    con.execute("DELETE FROM anchors WHERE kind IN ('up','subj','pid')")

    print(f"indexing upstream {args.since}..{args.ref_rev} in {ref} ...")
    up_subj, up_cid, up_all = {}, {}, set()
    for p in git_records(ref, "%H%x1f%s%x1f%(trailers:key=Change-Id,valueonly)",
                         f"{args.since}..{args.ref_rev}"):
        h = p[0]
        up_all.add(h)
        if len(p) > 1:
            up_subj.setdefault(p[1].strip(), h)
        if len(p) > 2 and p[2].strip():
            up_cid.setdefault(p[2].strip().split()[0], h)
    print(f"  {len(up_all)} upstream commits indexed")

    bodies = {p[0]: (p[1] if len(p) > 1 else "")
              for p in git_records(tree, "%H%x1f%B", f"{base}..{args.head}")}
    rows = con.execute("SELECT sha,sauce,clean_subject,change_id,patch_id,category "
                       "FROM commits ORDER BY seq").fetchall()
    stats = Counter()
    for sha, sauce, clean, cid, pid, cat in rows:
        if clean:
            con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "subj", clean, "subject"))
        if pid:
            con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "pid", pid, "patch-id"))
        if cat == "PACKAGING":
            continue
        ups = []
        for c in dict.fromkeys(RE_CITED.findall(bodies.get(sha, ""))):
            if c in up_all or commit_exists(ref, c):
                ups.append((c, "body"))
        if not ups and cid and cid in up_cid:
            ups.append((up_cid[cid], "change-id"))
        if not ups and clean and clean in up_subj:
            ups.append((up_subj[clean], "subject"))
        for u, how in ups:
            con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "up", u, how))
        tag = (sauce.split(",")[0] if sauce else "(none)") or "(none)"
        stats[(tag, ups[0][1] if ups else "UNANCHORED")] += 1
    con.commit()

    print("\n=== anchors: upstream identity per OEM commit (non-packaging) ===")
    tags = sorted({t for t, _ in stats}, key=lambda t: -sum(v for (x, _), v in stats.items() if x == t))
    print(f"  {'prefix':<12}{'body':>7}{'chg-id':>8}{'subject':>9}{'NONE':>7}")
    for t in tags:
        print(f"  {t:<12}{stats[(t,'body')]:>7}{stats[(t,'change-id')]:>8}"
              f"{stats[(t,'subject')]:>9}{stats[(t,'UNANCHORED')]:>7}")
    tot_un = sum(v for (t, h), v in stats.items() if h == "UNANCHORED")
    print(f"  unanchored: {tot_un} (candidates for `similar` + fan-out)")


# ---- similar (blind: find upstream forms that landed under another subject) --
SIG_MIN = 10


def sig_line(line):
    """Normalise a diff body line to a fingerprint, or None if too generic."""
    t = re.sub(r"\s+", " ", line[1:].strip())
    if len(t) < SIG_MIN or t.startswith(("/*", "*", "//", "#include")):
        return None
    if not re.search(r"[A-Za-z0-9_]{4}", t):
        return None
    return line[0] + t


def diff_sigs(text):
    """{path: set(sig)} for a unified diff (possibly multi-commit)."""
    out, path = {}, None
    for ln in text.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("--- "):
            continue
        elif path and ln[:1] in "+-" and not ln.startswith(("+++", "---")):
            s = sig_line(ln)
            if s:
                out.setdefault(path, set()).add(s)
    return out


SIMILAR_PACKET = """\
You are matching ONE kernel derivative commit to its upstream form.
The derivative patch was carried before upstream accepted it; upstream may
have reworded the subject, reworked the code, or split/merged patches.

Decide whether UPSTREAM CANDIDATE is the same change as DERIVATIVE PATCH:
- SAME_CHANGE : upstream lands the same fix/feature (possibly reworked, renamed,
                split). When this upstream commit reaches the generic kernel,
                the derivative patch becomes redundant or conflicts.
- RELATED     : touches the same code but is a different change.
- UNRELATED   : no meaningful relation.

Output ONE line of JSON ONLY:
{"sha":"<derivative sha>","up":"<upstream sha>","verdict":"SAME_CHANGE|RELATED|UNRELATED","reason":"<=20 words"}
"""


def cmd_similar(args):
    """Content-fingerprint match of unanchored OEM commits against upstream.

    Subject matching misses patches renamed on the way upstream (bt-sco
    'debugfs entries' landed as 'DAPM widget names'; 'extended SPI specifier'
    as 'ESPI and EPPI specifiers'). Their *lines* survive renaming.
    """
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    ref = os.path.expanduser(args.ref_tree)
    con.execute("DELETE FROM anchors WHERE kind='up' AND how IN ('similar','fanout')")
    rows = con.execute(
        "SELECT c.sha FROM commits c WHERE c.category!='PACKAGING' "
        "AND (c.churn_action IS NULL OR c.churn_action NOT LIKE 'DROP%') "
        "AND (c.sauce IS NULL OR c.sauce NOT LIKE 'QCLINUX%') "
        "AND NOT EXISTS (SELECT 1 FROM anchors a WHERE a.sha=c.sha AND a.kind='up') "
        "ORDER BY c.seq").fetchall()
    hubs = {p for (p,) in con.execute(
        "SELECT path FROM commit_files GROUP BY path HAVING COUNT(*) >= ?", (args.hub_threshold,))}
    oem = {}
    paths = set()
    for (sha,) in rows:
        sg = {p: s for p, s in diff_sigs(git(tree, "show", "--format=", sha)).items()
              if p not in hubs and not os.path.basename(p) in ("Makefile", "Kconfig")
              and not p.endswith("defconfig")}
        if sg:
            oem[sha] = sg
            paths |= set(sg)
    want = set()
    for sg in oem.values():
        for p, s in sg.items():
            want |= {(p, x) for x in s}
    print(f"{len(oem)} unanchored non-vendor commits with fingerprintable lines, "
          f"{len(paths)} paths")

    print(f"streaming upstream diffs {args.since}..{args.ref_rev} on those paths ...")
    inv = {}       # (path, sig) -> set(up sha)
    up_size = Counter()
    up_subj = {}
    cmd = ["git", "-C", ref, "log", "-p", "--no-merges", "--no-renames",
           "--format=@@@COMMIT %H%x1f%s", f"{args.since}..{args.ref_rev}", "--"] + sorted(paths)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
    cur, path = None, None
    for ln in proc.stdout:
        ln = ln.rstrip("\n")
        if ln.startswith("@@@COMMIT "):
            cur, _, s = ln[10:].partition("\x1f")
            up_subj[cur] = s
            path = None
        elif ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("--- ") or not cur or not path:
            continue
        elif ln[:1] in "+-":
            s = sig_line(ln)
            if s:
                up_size[cur] += 1
                if (path, s) in want:
                    inv.setdefault((path, s), set()).add(cur)
    proc.wait()
    print(f"  {len(up_subj)} upstream commits scanned")

    auto = review = 0
    packets = []
    for sha, sg in oem.items():
        mine = [(p, s) for p, ss in sg.items() for s in ss]
        hits = Counter()
        for k in mine:
            ups = inv.get(k, ())
            if len(ups) > args.max_df:   # line too common to be evidence
                continue
            for u in ups:
                hits[u] += 1
        if not hits:
            continue
        u, n = hits.most_common(1)[0]
        cover = n / len(mine)
        back = n / max(up_size[u], 1)
        if n >= 3 and cover >= args.auto and back >= 0.3:
            auto += 1
            con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "up", u, "similar"))
        elif n >= 2 and cover >= args.review:
            review += 1
            packets.append((sha, u, n, cover, back))
    con.commit()

    os.makedirs(args.out, exist_ok=True)
    manifest = []
    for sha, u, n, cover, back in packets:
        path = os.path.join(args.out, f"{sha[:12]}.txt")
        with open(path, "w") as f:
            f.write(SIMILAR_PACKET)
            f.write(f"\n=== DERIVATIVE {sha} ===\n")
            f.write(git(tree, "show", "--stat", "-p", sha)[:args.max_chars])
            f.write(f"\n\n=== UPSTREAM CANDIDATE {u} "
                    f"(shared lines {n}, covers {cover:.0%} of derivative) ===\n")
            f.write(git(ref, "show", "--stat", "-p", u)[:args.max_chars])
        manifest.append(path)
    with open(os.path.join(args.out, "manifest.txt"), "w") as f:
        f.write("\n".join(manifest) + "\n")
    print(f"\n=== similar: content match against upstream ===")
    print(f"  auto-anchored (>= {args.auto:.0%} of lines, >=3 lines): {auto}")
    print(f"  ambiguous -> fan-out packets           : {review}  ({args.out})")
    print(f"  no upstream form found                 : {len(oem) - auto - review}")


def cmd_ingest_similar(args):
    con = db_connect(args.db)
    n = Counter()
    full = {s for (s,) in con.execute("SELECT sha FROM commits")}
    for fn in args.verdicts:
        for line in open(fn):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                v = json.loads(line)
            except ValueError:
                continue
            sha = next((s for s in full if s.startswith(v.get("sha", "")[:12])), None)
            if not sha:
                continue
            n[v.get("verdict")] += 1
            if v.get("verdict") == "SAME_CHANGE" and re.fullmatch(r"[0-9a-f]{40}", v.get("up", "")):
                con.execute("DELETE FROM anchors WHERE sha=? AND kind='up' AND how='fanout'", (sha,))
                con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "up", v["up"], "fanout"))
    con.commit()
    print("ingested:", dict(n))


# ---- basedup (blind: OEM copies of commits already in the current base) -----
def cmd_basedup(args):
    """Find OEM commits that duplicate a generic commit ALREADY in G_old.

    These apply on top of their own base copy and leave only a residue -- often
    conflict-resolution scar tissue. The Monza boot failure was one: an OEM
    copy of a patch generic already had, which applied cleanly after generic
    reverted its own copy. Only candidates are marked here; `build` decides by
    proof (replay + tree equality) which can actually go.
    """
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    base = resolve_base(tree, args.base)
    since = resolve_base(tree, args.since)
    print(f"indexing generic {since[:12]}..{base[:12]} ...")
    g_subj, g_cited = {}, {}
    for p in git_records(tree, "%H%x1f%s%x1f%b", f"{since}..{base}"):
        h, s, b = p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else ""
        g_subj.setdefault(parse_prefixes(s)[1], h)
        for c in RE_CITED.findall(b):
            g_cited.setdefault(c, h)
    g_pid = {pid: sha for sha, pid in stream_patch_ids(tree, f"{since}..{base}").items()}
    print(f"  {len(g_subj)} subjects, {len(g_cited)} cited upstream shas, "
          f"{len(g_pid)} patch-ids")

    con.execute("UPDATE commits SET basedup_action=NULL, basedup_reason=NULL")
    con.execute("DELETE FROM anchors WHERE kind='base'")
    ups = {}
    for sha, v in con.execute("SELECT sha,value FROM anchors WHERE kind='up'"):
        ups.setdefault(sha, []).append(v)
    n = Counter()
    for sha, clean, pid, cat, ch in con.execute(
            "SELECT sha,clean_subject,patch_id,category,churn_action FROM commits "
            "ORDER BY seq").fetchall():
        if cat == "PACKAGING" or (ch or "").startswith("DROP"):
            continue
        hit = None
        if pid and pid in g_pid:
            hit = (g_pid[pid], "patch-id")
        if not hit:
            for u in ups.get(sha, []):
                if u in g_cited:
                    hit = (g_cited[u], "upstream sha")
                    break
        if not hit and clean and clean in g_subj and not RE_REVERT.match(clean):
            hit = (g_subj[clean], "subject")
        if hit:
            n[hit[1]] += 1
            con.execute("UPDATE commits SET basedup_action='CAND_DUP_OF_BASE',"
                        "basedup_reason=? WHERE sha=?",
                        (f"duplicates base {hit[0][:12]} by {hit[1]}", sha))
            con.execute("INSERT INTO anchors VALUES(?,?,?,?)", (sha, "base", hit[0], hit[1]))
    con.commit()
    print(f"\n=== basedup: OEM duplicates of commits already in the base ===")
    for k in ("patch-id", "upstream sha", "subject"):
        print(f"  by {k:<13}: {n[k]}")
    for sha, subj, r in con.execute(
            "SELECT sha,subject,basedup_reason FROM commits WHERE basedup_action IS NOT NULL "
            "ORDER BY seq LIMIT 20"):
        print(f"    {sha[:12]} {subj[:52]:<52} {r}")
    print("  (candidates only -- `build` drops each one only if the tree proves it)")


# ---- build (blind: produce the rederived branch, proven by replay) ----------
UPSTREAM_ID_PREFIXES = ("UPSTREAM", "BACKPORT", "FROMGIT", "FROMLIST")


def stream_diff_lines(tree, rev_range):
    """{sha: (added_lines_list, removed_lines_list)} over a range, one git call."""
    out, cur = {}, None
    proc = subprocess.Popen(["git", "-C", tree, "log", "-p", "--no-merges",
                             "--format=@@@COMMIT %H", rev_range],
                            stdout=subprocess.PIPE, text=True, errors="replace")
    for ln in proc.stdout:
        if ln.startswith("@@@COMMIT "):
            cur = ln[10:].strip()
            out[cur] = ([], [])
        elif cur and ln[:1] in "+-" and not ln.startswith(("+++", "---")):
            t = ln[1:].strip()
            if len(t) >= 4:
                out[cur][0 if ln[0] == "+" else 1].append(t)
    proc.wait()
    return out


def plan_squash(con, tree, base, head, keep):
    """Group OEM-only fix-on-fix chains. B joins A's group when B mostly edits
    lines the group itself added, and B commutes back to the group (no other
    kept commit in between touches B's files). Commuting file-disjoint patches
    is exact, so the per-file patch sequence -- and hence the tree -- is
    unchanged; only intermediate states disappear."""
    meta = {r[0]: r[1:] for r in con.execute(
        "SELECT sha,seq,sauce,subject,category,basedup_action FROM commits")}
    up = {s for (s,) in con.execute("SELECT DISTINCT sha FROM anchors WHERE kind='up'")}

    def oem_only(sha):
        seq, sauce, subj, cat, bd = meta[sha]
        tags = (sauce or "").split(",")
        return (sha not in up and cat != "PACKAGING" and not bd
                and not any(t in UPSTREAM_ID_PREFIXES for t in tags)
                and not RE_REVERT.match(subj) and not subj.startswith("UBUNTU"))

    files = {}
    for sha, p in con.execute("SELECT sha,path FROM commit_files"):
        files.setdefault(sha, set()).add(p)
    lines = stream_diff_lines(tree, f"{base}..{head}")
    last_toucher = {}            # path -> group head of the latest kept commit touching it
    group_of, added = {}, {}     # sha -> group head ; group head -> Counter(added lines)
    for sha in keep:             # keep is in seq order
        fs = files.get(sha, set())
        joined = None
        if oem_only(sha) and fs:
            prev = {last_toucher.get(p) for p in fs}
            if len(prev) == 1 and None not in prev:
                g = prev.pop()
                if oem_only(g):
                    rem = lines.get(sha, ([], []))[1]
                    if rem:
                        hit = sum(1 for t in rem if added[g][t] > 0)
                        if hit >= max(1, len(rem) / 2):
                            joined = g
        g = joined or sha
        group_of[sha] = g
        added.setdefault(g, Counter()).update(lines.get(sha, ([], []))[0])
        for p in fs:
            last_toucher[p] = g
    return group_of


def cmd_build(args):
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    base = resolve_base(tree, args.base)
    head = git(tree, "rev-parse", args.head).strip()
    con.execute("CREATE TABLE IF NOT EXISTS build_map(sha TEXT PRIMARY KEY, seq INTEGER, "
                "status TEXT, new_sha TEXT, group_head TEXT, note TEXT)")
    rows = con.execute("SELECT sha,seq,churn_action,basedup_action,category "
                       "FROM commits ORDER BY seq").fetchall()
    seq_of = {r[0]: r[1] for r in rows}
    cand = {}
    for sha, seq, ch, bd, cat in rows:
        if (ch or "").startswith("DROP"):
            cand[sha] = "churn:" + ch
        elif bd:
            cand[sha] = "basedup"
        elif cat == "UPSTREAMED":
            cand[sha] = "upstreamed"
    restored = {}
    files = {}
    for sha, p in con.execute("SELECT sha,path FROM commit_files"):
        files.setdefault(sha, set()).add(p)

    def keep_list():
        return [r[0] for r in rows if r[0] not in cand or r[0] in restored]

    def units_for(keep, group_of):
        units, idx = [], {}
        for sha in keep:
            g = group_of.get(sha, sha) if not args.no_squash else sha
            if g in idx:
                units[idx[g]].append(sha)
            else:
                idx[g] = len(units)
                units.append([sha])
        return units

    def restore_for(paths, before_seq, why):
        hit = [s for s in cand if s not in restored and seq_of[s] < before_seq
               and files.get(s, set()) & paths]
        for s in hit:
            restored[s] = why
        return hit

    gt = lambda *a, **k: run(["git", "-C", tree] + list(a), check=False, **k)
    gt("cherry-pick", "--abort")
    gt("checkout", "-q", "--detach", base)
    unsquash = set()
    after = {}                   # unit index -> new HEAD sha after applying it
    prev_units, rounds, force = [], 0, None
    while True:
        rounds += 1
        keep = keep_list()
        group_of = plan_squash(con, tree, base, head, keep)
        for s in unsquash:
            group_of[s] = s
        units = units_for(keep, group_of)
        start = next((j for j, (a, b) in enumerate(zip(prev_units, units)) if a != b),
                     min(len(prev_units), len(units)))
        if force is not None:
            start = min(start, force)
        prev_units, force = units, None
        gt("reset", "-q", "--hard", after[start - 1] if start else base)
        print(f"round {rounds}: {len(keep)} kept, {len(units)} units "
              f"({len(keep) - len(units)} squashed), replay from unit {start}", flush=True)
        restart = False
        empty = [units[j][0] for j in range(start) if after[j] == (after[j - 1] if j else base)]
        for i in range(start, len(units)):
            u = units[i]
            for sha in u:
                gt("cherry-pick", "-n", sha)
                U = [l for l in gt("diff", "--name-only", "--diff-filter=U").stdout.split() if l]
                if not U:
                    continue
                gt("cherry-pick", "--abort")
                gt("reset", "-q", "--hard")
                hit = restore_for(set(U), seq_of[sha], f"replay conflict at {sha[:12]} on {U[0]}")
                if not hit and len(u) > 1 and not set(u) <= unsquash:
                    unsquash.update(u)
                    hit = ["(unsquash)"]
                if not hit:
                    sys.exit(f"unrecoverable conflict at {sha[:12]} on {U}")
                print(f"  conflict at {sha[:12]} ({U[0]}): restored {len(hit)}", flush=True)
                force, restart = i, True
                break
            if restart:
                break
            if gt("diff", "--cached", "--quiet").returncode == 0:
                empty.append(u[0])
                after[i] = git(tree, "rev-parse", "HEAD").strip()
                continue
            msg = git(tree, "log", "-1", "--format=%B", u[0]).rstrip("\n")
            if len(u) > 1:
                msg += "\n\n" + "\n".join(
                    f"Squashed-in: {s[:12]} {git(tree, 'log', '-1', '--format=%s', s).strip()}"
                    for s in u[1:])
            msg += "\n\n" + "\n".join(f"Rederived-from: {s}" for s in u)
            an, ae, ad = git(tree, "log", "-1", "--format=%an%x1f%ae%x1f%aI", u[0]).rstrip("\n").split("\x1f")
            gt("commit", "-q", "--no-verify", f"--author={an} <{ae}>", f"--date={ad}", "-m", msg)
            after[i] = git(tree, "rev-parse", "HEAD").strip()
            if i % 500 == 0:
                print(f"  ... unit {i}/{len(units)}", flush=True)
        if restart:
            continue
        # tree proof against the original derivative
        diff = [p for p in gt("diff", "--name-only", "HEAD", head).stdout.split() if p]
        bad = []
        for p in diff:
            body = gt("diff", "HEAD", head, "--", p).stdout
            chg = [l[1:] for l in body.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            if not args.strict and chg and all(COSMETIC_LINE.search(l) for l in chg):
                continue
            bad.append(p)
        if not bad:
            break
        hit = restore_for(set(bad), 10 ** 9, f"tree mismatch on {bad[0]}")
        if not hit:
            sys.exit(f"tree differs from D on {bad[:5]} and no dropped commit explains it")
        print(f"  tree mismatch on {len(bad)} file(s): restored {len(hit)}", flush=True)

    new_head = git(tree, "rev-parse", "HEAD").strip()
    gt("branch", "-f", args.branch, new_head)
    con.execute("DELETE FROM build_map")
    for i, u in enumerate(units):
        for sha in u:
            con.execute("INSERT INTO build_map VALUES(?,?,?,?,?,?)",
                        (sha, seq_of[sha], "squashed" if sha != u[0] else
                         ("empty" if u[0] in empty else "kept"),
                         after.get(i), u[0], restored.get(sha)))
    for s, why in cand.items():
        if s not in restored:
            con.execute("INSERT INTO build_map VALUES(?,?,?,?,?,?)",
                        (s, seq_of[s], "dropped", None, None, why))
    con.commit()

    cosm = [p for p in gt("diff", "--name-only", "HEAD", head).stdout.split() if p]
    drops = Counter(v.split(":")[0] for s, v in cand.items() if s not in restored)
    print(f"\n=== build: rederived branch '{args.branch}' = {new_head[:12]} ===")
    print(f"  original commits      : {len(rows)}")
    print(f"  rederived commits     : {len(units) - len(empty)}")
    print(f"  dropped (proven)      : {sum(drops.values())}  {dict(drops)}")
    print(f"  drop candidates restored by proof: {len(restored)}")
    print(f"  squashed into a predecessor      : {len(keep) - len(units)} "
          f"(in {sum(1 for u in units if len(u) > 1)} groups)")
    print(f"  emptied on replay     : {len(empty)}")
    print(f"  tree vs D_old         : {'IDENTICAL' if not cosm else f'{len(cosm)} file(s) differ, cosmetic lines only'}")
    print(f"  replay rounds         : {rounds}")


# ---- rebase (the only command that may see G_new) ---------------------------
RE_UPCITE = re.compile(r"(?:commit\s+([0-9a-f]{40})\s+upstream|cherry picked from commit\s+([0-9a-f]{40}))", re.I)
RE_REVERTS_SHA = re.compile(r"This reverts commit\s+([0-9a-f]{7,40})", re.I)


def cmd_rebase(args):
    con = db_connect(args.db)
    tree = os.path.expanduser(args.tree)
    base = git(tree, "rev-parse", args.base).strip()
    onto = git(tree, "rev-parse", args.onto).strip()
    branch = git(tree, "rev-parse", args.branch).strip()
    gt = lambda *a: run(["git", "-C", tree] + list(a), check=False)

    units = git_records(tree, "%H%x1f%s%x1f%B", "--reverse", "--no-merges", f"{base}..{branch}")
    rules = {}
    if not args.plain:
        # index the new generic delta: what graduated, what got reverted
        delta = git_records(tree, "%H%x1f%s%x1f%B", "--no-merges", f"{base}..{onto}")
        cited, dsubj, reverted_subj, reverted_sha = set(), set(), set(), set()
        for h, s, b in delta:
            for a, c in RE_UPCITE.findall(b):
                cited.add(a or c)
            if not s.startswith("UBUNTU"):     # packaging subjects repeat every release
                dsubj.add(parse_prefixes(s)[1])
            m = RE_REVERT.match(s)
            if m:
                reverted_subj.add(parse_prefixes(m.group(1))[1])
                for x in RE_REVERTS_SHA.findall(b):
                    reverted_sha.add(x)
        dpid = set(stream_patch_ids(tree, f"{base}..{onto}").values())
        upos = {}
        for sha, v in con.execute("SELECT sha,value FROM anchors WHERE kind IN ('up','base')"):
            upos.setdefault(sha, set()).add(v)
        upos_subj = {}
        for v in {x for vs in upos.values() for x in vs}:
            sj = git(tree, "log", "-1", "--format=%s", v).strip()
            if not sj and args.ref_tree:
                sj = git(os.path.expanduser(args.ref_tree), "log", "-1", "--format=%s", v).strip()
            if sj:
                upos_subj[v] = parse_prefixes(sj)[1]
        upid = stream_patch_ids(tree, f"{base}..{branch}")
        for h, s, b in units:
            origs = re.findall(r"^Rederived-from:\s*([0-9a-f]{40})", b, re.M) or [h]
            anchors = set().union(*(upos.get(o, set()) for o in origs))
            subjects = {parse_prefixes(s)[1]} | {upos_subj[a] for a in anchors if a in upos_subj}
            if any(a[:12] in {x[:12] for x in reverted_sha} for a in anchors) \
                    or subjects & reverted_subj:
                rules[h] = "DROP:anchor-reverted-in-new-base"
            elif upid.get(h) in dpid:
                rules[h] = "SKIP:patch-id-in-new-base"
            elif anchors & cited or subjects & dsubj:
                rules[h] = "TRY:graduated"

    gt("cherry-pick", "--abort")
    gt("checkout", "-q", "--detach", onto)
    # A TRY-skip (graduated commit that conflicts) is provisional: if a later
    # commit then conflicts on a file the skipped one touched, the skip broke a
    # series -- rewind to just before it, apply it instead, and replay on.
    conflicts, skipped, flags = [], [], []          # entries carry the unit index
    before, tried, forced, backtracks = {}, {}, set(), 0
    files_of = lambda h: set(git(tree, "show", "--name-only", "--format=", h).split())
    i = 0
    while i < len(units):
        h, s, b = units[i]
        before[i] = git(tree, "rev-parse", "HEAD").strip()
        rule = rules.get(h, "")
        if h in forced:
            rule = ""
        if rule.startswith(("DROP", "SKIP")):
            skipped.append((i, h, s, rule))
            i += 1
            continue
        r = gt("cherry-pick", "-x", "--allow-empty", "--keep-redundant-commits", h)
        if r.returncode == 0:
            if rule:
                flags.append((i, h, s, rule + ":applied-clean"))
            i += 1
            continue
        U = [l for l in gt("diff", "--name-only", "--diff-filter=U").stdout.split() if l]
        if not U:                      # empty after resolution or other benign stop
            gt("cherry-pick", "--skip")
            skipped.append((i, h, s, "empty"))
            i += 1
            continue
        if rule.startswith("TRY"):
            gt("cherry-pick", "--abort")
            skipped.append((i, h, s, rule + ":conflicts->skipped"))
            tried[h] = (i, files_of(h))
            i += 1
            continue
        culprit = sorted((j, x) for x, (j, fs) in tried.items() if fs & set(U))
        if culprit:
            j, x = culprit[0]
            gt("cherry-pick", "--abort")
            gt("reset", "-q", "--hard", before[j])
            forced.add(x)
            for x2 in [x2 for x2, (j2, _) in tried.items() if j2 >= j]:
                del tried[x2]
            conflicts[:] = [e for e in conflicts if e[0] < j]
            skipped[:] = [e for e in skipped if e[0] < j]
            flags[:] = [e for e in flags if e[0] < j]
            flags.append((j, x, units[j][1], "TRY:graduated:skip-broke-series->applied"))
            backtracks += 1
            print(f"  backtrack: skipping {x[:12]} made {h[:12]} conflict on {U[0]}", flush=True)
            i = j
            continue
        conflicts.append((i, h, s, U))
        gt("checkout", "--theirs", "--", *U)
        gt("add", "-A", "--", *U)
        if gt("-c", "core.editor=true", "cherry-pick", "--continue").returncode != 0:
            gt("cherry-pick", "--skip")
        i += 1
        if i % 500 == 0:
            print(f"  ... {i}/{len(units)} ({len(conflicts)} conflicts)", flush=True)
    conflicts = [e[1:] for e in conflicts]
    skipped = [e[1:] for e in skipped]
    flags = [e[1:] for e in sorted(flags)]
    head = git(tree, "rev-parse", "HEAD").strip()
    if args.out_branch:
        gt("branch", "-f", args.out_branch, head)
    with open(args.log, "w") if args.log else open(os.devnull, "w") as f:
        for h, s, U in conflicts:
            f.write(f"CONFLICT|{h}|{s}|{' '.join(U)}\n")
        for h, s, why in skipped:
            f.write(f"SKIP|{h}|{s}|{why}\n")
        for h, s, why in flags:
            f.write(f"FLAG|{h}|{s}|{why}\n")
    print(f"\n=== rebase ({'plain' if args.plain else 'assisted'}) onto {onto[:12]}: head {head[:12]} ===")
    print(f"  commits replayed : {len(units)}")
    print(f"  conflicts        : {len(conflicts)}")
    print(f"  skipped          : {len(skipped)}  {dict(Counter(w.split(':')[0] + ':' + w.split(':')[1] if ':' in w else w for _, _, w in skipped))}")
    print(f"  flagged (review) : {len(flags)}")
    print(f"  backtracks       : {backtracks}")
    for h, s, w in skipped + flags:
        if w != "empty":
            print(f"    {h[:12]} {w:45s} {s[:70]}")


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

    an = sub.add_parser("anchor", help="blind: record upstream identity (sha/subject/patch-id) per OEM commit")
    an.add_argument("--tree", required=True)
    an.add_argument("--base", required=True, help="current generic base G_old")
    an.add_argument("--head", default="HEAD")
    an.add_argument("--ref-tree", required=True, help="upstream reference tree")
    an.add_argument("--ref-rev", default="linux-next/master")
    an.add_argument("--since", default="v6.8", help="upstream index lower bound")
    an.set_defaults(func=cmd_anchor)

    sm = sub.add_parser("similar", help="blind: content-match unanchored OEM commits to upstream")
    sm.add_argument("--tree", required=True)
    sm.add_argument("--ref-tree", required=True)
    sm.add_argument("--ref-rev", default="linux-next/master")
    sm.add_argument("--since", default="v6.8")
    sm.add_argument("--hub-threshold", type=int, default=15)
    sm.add_argument("--max-df", type=int, default=25, help="ignore lines shared by more upstream commits")
    sm.add_argument("--auto", type=float, default=0.6, help="coverage to anchor without review")
    sm.add_argument("--review", type=float, default=0.25, help="coverage to send to fan-out")
    sm.add_argument("--out", default="similar_packets")
    sm.add_argument("--max-chars", type=int, default=9000)
    sm.set_defaults(func=cmd_similar)

    si = sub.add_parser("ingest-similar", help="load fan-out verdicts for `similar` packets")
    si.add_argument("verdicts", nargs="+")
    si.set_defaults(func=cmd_ingest_similar)

    bd = sub.add_parser("basedup", help="blind: OEM duplicates of commits already in G_old")
    bd.add_argument("--tree", required=True)
    bd.add_argument("--base", required=True, help="current generic base G_old")
    bd.add_argument("--since", required=True, help="upstream release the base derives from, e.g. 'Linux 6.8'")
    bd.set_defaults(func=cmd_basedup)

    bu = sub.add_parser("build", help="blind: replay the keep-set onto G_old (drops + fix-on-fix "
                        "squash), restoring any drop the replay or tree proof rejects")
    bu.add_argument("--tree", required=True, help="scratch clone (HEAD is moved)")
    bu.add_argument("--base", required=True, help="current generic base G_old")
    bu.add_argument("--head", required=True, help="derivative tip D_old")
    bu.add_argument("--branch", default="rederived", help="branch to write in --tree")
    bu.add_argument("--no-squash", action="store_true")
    bu.add_argument("--strict", action="store_true", help="require byte-identical tree (no cosmetic residue)")
    bu.set_defaults(func=cmd_build)

    rb = sub.add_parser("rebase", help="replay a rederived branch onto a NEW base (the only "
                        "command allowed to see G_new)")
    rb.add_argument("--tree", required=True, help="scratch clone (HEAD is moved)")
    rb.add_argument("--base", required=True, help="base the branch sits on (G_old)")
    rb.add_argument("--onto", required=True, help="new generic base G_new")
    rb.add_argument("--branch", required=True, help="rederived branch tip")
    rb.add_argument("--ref-tree", help="upstream tree (resolves anchor subjects)")
    rb.add_argument("--plain", action="store_true", help="no rules: plain replay with the --theirs harness")
    rb.add_argument("--out-branch")
    rb.add_argument("--log", help="write CONFLICT/SKIP/FLAG lines here")
    rb.set_defaults(func=cmd_rebase)

    gr = sub.add_parser("graduate", help="AUDIT ONLY: compare against a known future base "
                        "(uses lookahead; never feed its verdicts into a blind rederive)")
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
