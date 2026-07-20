#!/usr/bin/env python3
"""pf-ingest.py — the front door for adding new Passion Fit material to the CC concepts brain.

The PassionFit Concepts area (/m/pf-concepts) reads vault_notes tagged `passionfit-concepts`.
This tool takes new material (a weekly Plaud seminar export, a doc, an image snippet, an article,
or a pasted note), turns it into well-tagged markdown note(s), ingests + embeds them, and (optionally)
backs the raw up to Drive. One consistent pipeline so every weekly drop lands the same way.

Run from the bootstrap clone:   VAULT=/tmp/pbs python3 /tmp/pbs/pf-ingest.py <cmd> ...

Commands
  plaud   <file.txt|dir>  [--concept a,b]   Plaud export (Summary view saved to .txt) → split + ingest
  doc     <file ...>                         .docx (textutil) / .pdf (pdftotext) / .md → ingest
  image   <file>  --caption "<desc>" [--concept a,b]   image snippet → caption note (caption from the
                                             vision model that Read the image) → ingest
  article <url|file.html> [--concept a,b] [--title "..."]   article → cleaned text → ingest
  text    --title "..." [--concept a,b]       a pasted snippet on stdin → ingest
  status                                       print the corpus counts

Common flags:  --concept <comma-slugs>  --no-embed  --drive (back raw up to the PF Drive source folder)

Concept slugs (tag a note to a concept so it shows on that concept's page):
  effective-goal-setting commitment-continuum prioritisation control-the-controllables
  transactional-state direction-support-matrix intuition-scale-learning-behaviours
  high-functioning-matrix the-development-paradox ipsative-assessment potential
  ipsative-progression-curve-green-line impact-influence-control-legacy
  presence safe-space-vs-soft-space listening-behaviours blame-and-ownership
  communication-hierarchy the-behaviours-of-the-accomplished applied-coaching-science
"""
import os, re, sys, glob, json, hashlib, subprocess, urllib.request, argparse, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
STAGE = os.path.join(VAULT, "Personal/passion-fit/concepts/inbox")
TAGBASE = "passion-fit, passionfit-concepts, PA-PassionFit-Concepts"

CONCEPT_MAP = [
    ("human needs", "human-needs"), ("intuition", "intuition-scale-learning-behaviours"),
    ("listening", "listening-behaviours"), ("prioritis", "prioritisation"),
    ("development paradox", "the-development-paradox"), ("safe space", "safe-space-vs-soft-space"),
    ("coachab", "coachability"), ("commitment", "commitment-continuum"),
    ("control", "control-the-controllables"), ("ownership", "blame-and-ownership"),
    ("pie of potential", "potential"), ("potential", "potential"),
    ("high functioning", "high-functioning-matrix"), ("high perform", "high-functioning-matrix"),
    ("accomplished", "the-behaviours-of-the-accomplished"), ("goal", "effective-goal-setting"),
    ("legacy", "impact-influence-control-legacy"), ("vuca", "control-the-controllables"),
    ("direction", "direction-support-matrix"), ("transactional", "transactional-state"),
    ("ipsative", "ipsative-assessment"), ("ironman", "applied-coaching-science"),
    ("swim", "applied-coaching-science"), ("ftp", "applied-coaching-science"),
    ("presence", "presence"), ("communication", "communication-hierarchy"),
]


def slug(s): return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:70]
def today(): return os.environ.get("PF_TODAY") or datetime.date.today().isoformat()


def infer_concepts(text):
    low = text.lower()
    out = []
    for kw, tag in CONCEPT_MAP:
        if kw in low and tag not in out:
            out.append(tag)
    return out[:4]


def write_note(sub, fname, ntype, title, concepts, source, body, extra_tags="", audience="private"):
    os.makedirs(os.path.join(STAGE, sub), exist_ok=True)
    tags = f"[{TAGBASE}, {extra_tags}{', '.join(concepts)}]".replace(", ]", "]").replace(",  ", ", ")
    fm = {"type": ntype, "entity": "Personal", "title": title, "tags": tags, "source": source,
          "audience": audience}  # mirror-sync switch: private (cautious default) unless --audience shared
    out = "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n\n"
    if concepts:
        out += "> Concepts: " + " · ".join(f"[[{c}]]" for c in concepts) + "\n\n"
    out += body.strip() + "\n"
    p = os.path.join(STAGE, sub, fname)
    open(p, "w").write(out)
    return os.path.relpath(p, VAULT)


def clean_plaud(text):
    out = []
    for s in text.split("\n"):
        s = s.rstrip()
        if re.match(r"^(Mind map|Copyright ©)", s, re.I):
            break
        if re.match(r"^(Amplify Human Intelligence|Explore now)\s*$", s, re.I):
            continue
        out.append(s)
    return "\n".join(out)


def chunks(text, words=1500):
    segs, cur, wc = [], [], 0
    for ln in text.split("\n"):
        cur.append(ln); wc += len(ln.split())
        if wc >= words and ln.strip() == "":
            segs.append("\n".join(cur)); cur, wc = [], 0
    if cur and "\n".join(cur).strip():
        segs.append("\n".join(cur))
    return segs or [text]


def extract_doc(path):
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "md":
        return open(path, encoding="utf-8", errors="replace").read()
    if ext == "docx":
        return subprocess.run(["textutil", "-convert", "txt", "-stdout", path], capture_output=True, text=True).stdout
    if ext == "pdf":
        return subprocess.run(["pdftotext", "-nopgbrk", path, "-"], capture_output=True, text=True).stdout
    if ext in ("txt", "text"):
        return open(path, encoding="utf-8", errors="replace").read()
    return ""


def backup_drive(path, subfolder):
    try:
        mount = subprocess.check_output("ls -d ~/Library/CloudStorage/GoogleDrive*/My\\ Drive 2>/dev/null | head -1", shell=True, text=True).strip()
        dest = os.path.join(mount, "Passion Fit/Passion Fit Concepts/New Material (inbox)", subfolder)
        os.makedirs(dest, exist_ok=True)
        subprocess.run(["cp", path, dest], check=True)
        return os.path.join(subfolder, os.path.basename(path))
    except Exception as e:
        return f"(drive backup failed: {e})"


INFLUENCE_HINT = re.compile(r"(?i)\b(created by|developed by|coined by|popularised by|popularized by|author of|his book|her book|the book)\b[^\n]{0,120}")

def ingest_and_embed(rel_paths, do_embed=True):
    roots = " ".join(f'"{p}"' for p in rel_paths)
    r = subprocess.run(f'cd "{VAULT}" && python3 cc-knowledge-ingest.py {roots}', shell=True, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[:200])
    # v2 ritual (2026-07 execution plan Stage F): every drop self-links, keeps the walkable graph
    # current, flags external-model mentions as influence candidates, and prints the gate snapshot.
    for cand_rel in rel_paths:
        try:
            txt = open(os.path.join(VAULT, cand_rel), encoding="utf-8", errors="replace").read()
            for m in INFLUENCE_HINT.finditer(txt):
                print(f"  influence candidate? {os.path.basename(cand_rel)}: …{m.group(0)[:110]}")
        except OSError:
            pass
    subprocess.run(f'cd "{VAULT}" && python3 pf-link-pass.py --apply', shell=True)
    subprocess.run(f'cd "{VAULT}" && python3 cc-note-links-refresh.py --corpus', shell=True)
    if do_embed:
        e = subprocess.run(f'cd "{VAULT}" && python3 cc-knowledge-embed-backfill.py', shell=True, capture_output=True, text=True)
        print(e.stdout.strip().splitlines()[-1] if e.stdout.strip() else e.stderr[:200])
    subprocess.run(f'cd "{VAULT}" && python3 pf-gates.py', shell=True)


# ---------------- commands ----------------
def cmd_plaud(a):
    files = []
    for src in a.paths:
        files += glob.glob(os.path.join(src, "*.txt")) if os.path.isdir(src) else [src]
    rels = []
    for f in files:
        raw = clean_plaud(open(f, encoding="utf-8", errors="replace").read())
        lines = raw.split("\n")
        title = (lines[0].strip() if lines else os.path.basename(f)) or "Plaud seminar"
        concepts = a.concept.split(",") if a.concept else infer_concepts(raw)
        segs = chunks(raw)
        for i, seg in enumerate(segs, 1):
            part = f" [{i}/{len(segs)}]" if len(segs) > 1 else ""
            sl = slug(title) + (f"-{i:02d}" if len(segs) > 1 else "")
            rels.append(write_note("seminars", f"{today()}-{sl}.md", "seminar",
                        f"Seminar — {title}{part}", concepts, f"plaud:{os.path.basename(f)}", seg, "plaud-seminar, ", audience=a.audience))
        if a.drive:
            print("  drive:", backup_drive(f, "plaud-exports"))
        print(f"  {os.path.basename(f)} → {len(segs)} note(s), concepts={concepts}")
    ingest_and_embed(rels, not a.no_embed)


def cmd_doc(a):
    rels = []
    for f in a.paths:
        text = re.sub(r"\n{4,}", "\n\n\n", extract_doc(f)).strip()
        if len(text) < 80:
            print(f"  skip (empty): {f}"); continue
        title = os.path.splitext(os.path.basename(f))[0]
        concepts = a.concept.split(",") if a.concept else infer_concepts(text)
        segs = chunks(text)
        for i, seg in enumerate(segs, 1):
            part = f" [{i}/{len(segs)}]" if len(segs) > 1 else ""
            sl = slug(title) + (f"-{i:02d}" if len(segs) > 1 else "")
            rels.append(write_note("docs", f"{today()}-{sl}.md", "source-doc",
                        f"{title}{part}", concepts, f"drive:{os.path.basename(f)}", seg, "source-doc, ", audience=a.audience))
        print(f"  {os.path.basename(f)} → {len(segs)} note(s), concepts={concepts}")
    ingest_and_embed(rels, not a.no_embed)


def cmd_image(a):
    if not a.caption:
        sys.exit("image needs --caption \"<what the image shows>\" (from the vision model that Read it).")
    title = a.title or os.path.splitext(os.path.basename(a.paths[0]))[0]
    concepts = a.concept.split(",") if a.concept else infer_concepts(a.caption + " " + title)
    body = f"> Image snippet: `{os.path.basename(a.paths[0])}`\n\n{a.caption}"
    rel = write_note("images", f"{today()}-{slug(title)}.md", "concept-diagram",
                     f"{title} (image)", concepts, f"image:{os.path.basename(a.paths[0])}", body, "images, ", audience=a.audience)
    if a.drive:
        print("  drive:", backup_drive(a.paths[0], "concept-snippets"))
    print(f"  image → 1 note, concepts={concepts}")
    ingest_and_embed([rel], not a.no_embed)


def cmd_article(a):
    src = a.paths[0]
    if re.match(r"^https?://", src):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except Exception as e:
            sys.exit(f"fetch failed: {e}")
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        source = src
    else:
        text = extract_doc(src) if src.lower().rsplit(".", 1)[-1] in ("md", "txt", "docx", "pdf") else open(src, encoding="utf-8", errors="replace").read()
        text = re.sub(r"<[^>]+>", " ", text); text = re.sub(r"[ \t]+", " ", text).strip()
        source = f"file:{os.path.basename(src)}"
    if len(text) < 120:
        sys.exit("article text too short after cleaning.")
    title = a.title or (src.rstrip("/").split("/")[-1].replace("-", " ")[:70] or "Article")
    concepts = a.concept.split(",") if a.concept else infer_concepts(text)
    rels = []
    segs = chunks(text)
    for i, seg in enumerate(segs, 1):
        part = f" [{i}/{len(segs)}]" if len(segs) > 1 else ""
        rels.append(write_note("articles", f"{today()}-{slug(title)}{('-%02d'%i) if len(segs)>1 else ''}.md",
                    "source-doc", f"Article — {title}{part}", concepts, source, seg, "article, ", audience=a.audience))
    print(f"  article → {len(segs)} note(s), concepts={concepts}")
    ingest_and_embed(rels, not a.no_embed)


def cmd_text(a):
    body = sys.stdin.read()
    if len(body.strip()) < 20:
        sys.exit("nothing on stdin.")
    concepts = a.concept.split(",") if a.concept else infer_concepts(body + " " + (a.title or ""))
    rel = write_note("notes", f"{today()}-{slug(a.title)}.md", "source-doc",
                     a.title, concepts, "pasted-snippet", body, "snippet, ", audience=a.audience)
    print(f"  snippet → 1 note, concepts={concepts}")
    ingest_and_embed([rel], not a.no_embed)


def cmd_status(a):
    r = subprocess.run(f'cd "{VAULT}" && python3 cc-sql.py "SELECT type, count(*) AS n, count(*) FILTER (WHERE embedding IS NOT NULL AND embedded_hash = md5(embed_input(title,body))) AS embedded FROM vault_notes WHERE tags && ARRAY[\'passionfit-concepts\'] GROUP BY type ORDER BY n DESC"',
                       shell=True, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr[:300])


def main():
    p = argparse.ArgumentParser(prog="pf-ingest.py", description="Add new Passion Fit material to the CC concepts brain.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plaud", "doc", "image", "article", "text", "status"):
        sp = sub.add_parser(name)
        if name != "status" and name != "text":
            sp.add_parser if False else sp.add_argument("paths", nargs="+")
        sp.add_argument("--concept", default="")
        sp.add_argument("--caption", default="")
        sp.add_argument("--title", default="Passion Fit note")
        sp.add_argument("--no-embed", action="store_true")
        sp.add_argument("--drive", action="store_true")
        sp.add_argument("--audience", default="private", choices=["private", "shared", "variant-needed"])
    a = p.parse_args()
    {"plaud": cmd_plaud, "doc": cmd_doc, "image": cmd_image, "article": cmd_article, "text": cmd_text, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
