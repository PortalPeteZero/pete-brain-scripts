#!/usr/bin/env python3
"""clancy-dn-enrich-index.py — turn the enrichment harvest into the queryable v2 layer.

The harvest (clancy-dn-enrich.py) produces per-file extracts and per-image readings. This tool
loads them into the index tables and promotes the findings onto the damage record — into NEW
`doc_*` columns ONLY. Depotnet's own fields are never written to; the pre-enrichment baseline in
`clancy_dn_baseline_pre_enrichment` stays frozen so the v1/v2 difference stays provable.

Panel review slides follow Clancy's own HSF-902 template, so their conclusions parse
deterministically ("Primary Cause:", "Lessons Learnt / How can we prevent this?", "Behaviour:").
Everything else is classified by content and left as full text for the interpretive pass.

  --load     extracts + image readings -> clancy_dn_doc_extracts / clancy_dn_image_readings
  --promote  roll up per damage -> clancy_dn_incidents.doc_* (additive, never destructive)
  --stats    what the enrichment added, measured against the frozen baseline
"""
import os, sys, json, re, glob, argparse, hashlib, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WORK = os.environ.get("ENRICH_WORK", "/tmp/enrich-work")
PARSER_VERSION = "e1-2026-08-03"

SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}


def rest(path, method="GET", body=None, headers=None):
    h = dict(H); h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def rest_all(path, page=1000):
    out, off = [], 0
    while True:
        chunk = rest(path, headers={"Range-Unit": "items", "Range": f"{off}-{off+page-1}"})
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
        off += page
    return out


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def pg_text(s):
    """Postgres text rejects NUL (and other C0 controls travel badly through JSON) — some .msg
    and legacy .doc bodies carry them. Strip, never drop the whole document."""
    if s is None:
        return None
    return _CTRL.sub("", s)


def post_sized(path, rows, max_bytes=3_000_000):
    """PostgREST rejects a giant body — some extracted documents run to megabytes on their own,
    so chunk by PAYLOAD SIZE, not row count."""
    batch, size, sent = [], 0, 0
    for r in rows:
        b = len(json.dumps(r))
        if batch and (size + b > max_bytes or len(batch) >= 50):
            rest(path, "POST", batch, {"Prefer": "resolution=merge-duplicates"})
            sent += len(batch); batch, size = [], 0
        batch.append(r); size += b
    if batch:
        rest(path, "POST", batch, {"Prefer": "resolution=merge-duplicates"})
        sent += len(batch)
    return sent


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


# ---------------------------------------------------------------- classify

PANEL_MARKERS = ("hsf-902", "panel review", "conference call review", "strike conference")
INVEST_MARKERS = ("investigation", "incident report", "root cause", "rca")
PERMIT_MARKERS = ("permit to dig", "permit to work", "dig permit", "ptd")
PLAN_MARKERS = ("plans", "utility plan", "record plan", "openreach", "ukpn", "cadent", "sgn",
                "elec", "gas plan", "water plan", "asset plan")
STATEMENT_MARKERS = ("statement", "gang statement", "operative statement")
CAT_MARKERS = ("c.a.t", "cat manager", "cat & genny", "cat and genny", "gcat", "usage report")
RAMS_MARKERS = ("rams", "risk assessment", "method statement", "safe system")


def classify(name, text):
    n = (name or "").lower()
    t = (text or "")[:6000].lower()
    def any_in(markers, *hay):
        return any(m in h for m in markers for h in hay)
    if any_in(PANEL_MARKERS, n, t):
        return "panel-review"
    if any_in(CAT_MARKERS, n, t):
        return "locator-data"
    if any_in(PERMIT_MARKERS, n, t):
        return "permit"
    if any_in(INVEST_MARKERS, n, t):
        return "investigation"
    if any_in(STATEMENT_MARKERS, n, t):
        return "statement"
    if any_in(RAMS_MARKERS, n, t):
        return "rams"
    if any_in(PLAN_MARKERS, n, t):
        return "utility-plan"
    if n.endswith((".jpg", ".jpeg", ".png")):
        return "photo"
    if n.endswith(".mp4"):
        return "video"
    if "incident manager" in n:
        return "depotnet-form"
    return "other"


# ---------------------------------------------------------------- HSF-902 parse

def _clean_lines(block):
    out = []
    for ln in (block or "").splitlines():
        ln = ln.strip(" \t•-–— ")
        if not ln:
            continue
        if re.match(r"^(hsf-902|insert photos?|type of event|images? (are )?on next)", ln, re.I):
            continue
        out.append(ln)
    return out


def parse_panel(text):
    """Clancy's HSF-902 template puts the conclusion under fixed headings."""
    res = {"conclusions": [], "lessons": [], "behaviour": [], "what_happened": []}
    if not text:
        return res
    pats = [
        ("what_happened", r"what happened[:\?]?\s*(.*?)(?=why did it happen|primary cause|lessons learnt|lessons learned|behaviour|$)"),
        ("conclusions", r"primary cause[:\s]*(.*?)(?=behaviour|lessons learnt|lessons learned|type of event|$)"),
        ("lessons", r"lessons? learn[et]d?\s*(?:/\s*how can we prevent this\??)?[:\s]*(.*?)(?=behaviour|type of event|hsf-902|primary cause|$)"),
        ("behaviour", r"behaviour[:\s]*(.*?)(?=type of event|lessons learnt|lessons learned|hsf-902|$)"),
    ]
    low = text.lower()
    for key, pat in pats:
        m = re.search(pat, low, re.S)
        if not m:
            continue
        raw = text[m.start(1):m.end(1)]
        res[key] = _clean_lines(raw)[:12]
    return res


METHOD_FAILURE_PATTERNS = [
    ("genny not connected to street furniture",
     r"genny (lead )?(was )?not connected|not connected to (the )?(lamp )?column|without connecting the genny"),
    ("service not positively located before excavation",
     r"not positively (identif|locat)|failure to positively locate|position .{0,40}not (positively )?identified|unable to identify (the )?(gas|electric|water|service)"),
    ("mechanical excavation too close to a known service",
     r"mechanical excavation .{0,60}(close|near|over|on top)|breaker on top|pecker .{0,40}(over|on top|close)|digger bucket|machine .{0,30}(pecker|bucket) .{0,30}(caught|struck|damag)"),
    ("no rescanning as the dig progressed",
     r"no rescann?ing|not rescann?ed|rescan .{0,30}every 150|failure to rescan"),
    ("no trial holes or hand digging where uncertain",
     r"no trial hole|trial holes? (were )?not|without (hand ?dig|trial hole)|should have hand dug"),
    ("service exposed and left unprotected",
     r"(exposed service|service was exposed).{0,80}(not protect|unprotect)|arc blanket .{0,40}not used"),
    ("service not on the plans or plans not consulted",
     r"not (shown|marked) on (the )?plan|no (record|plan) of (the )?service|plans (were )?not (checked|consulted)"),
    ("shallow service, below expected cover",
     r"shallow (cable|service|main)|at (only )?\d{2,3} ?mm deep|30mm deep|no (marker )?tape|without marker tape"),
    ("procedures not followed",
     r"procedures? not followed|failure to follow (the )?procedure|did not follow (the )?safe"),
    ("start point / signal check not used",
     r"start point not used|sp-? ?start point|no (signal|start) (point )?check"),
]


def find_method_failures(text):
    low = (text or "").lower()
    return [label for label, pat in METHOD_FAILURE_PATTERNS if re.search(pat, low)]


# ---------------------------------------------------------------- load

def img_key(path):
    return hashlib.md5(path.encode()).hexdigest()


def load():
    xs = sorted(glob.glob(f"{WORK}/extracts/*.json"))
    doc_rows, seen_doc = [], set()
    for p in xs:
        d = json.load(open(p))
        units = d.get("units") or {}
        text = "\n\n".join(f"[{k}]\n{v}" for k, v in units.items())
        cls = classify(d["name"], text)
        panel = parse_panel(text) if cls == "panel-review" else {"conclusions": [], "lessons": []}
        mf = find_method_failures(text)
        if d["file_id"] in seen_doc:
            continue
        seen_doc.add(d["file_id"])
        doc_rows.append({
            "incident_id": d["incident_id"], "file_id": d["file_id"], "file_name": d["name"],
            "kind": d.get("kind"), "doc_class": cls,
            "extracted_text": pg_text(text[:400000]) or None,
            "conclusions": [pg_text(x) for x in panel.get("conclusions") or []] or None,
            "lessons": [pg_text(x) for x in panel.get("lessons") or []] or None,
            "method_failures": mf or None,
            "confidence": ("template" if cls == "panel-review" and panel.get("conclusions")
                           else ("text" if text.strip() else "none")),
            "parser_version": PARSER_VERSION,
        })
    post_sized("clancy_dn_doc_extracts?on_conflict=file_id,parser_version", doc_rows)
    print(f"doc extracts loaded: {len(doc_rows)}")

    qp = f"{WORK}/vision-queue.jsonl"
    q = {}
    if os.path.exists(qp):
        for line in open(qp):
            v = json.loads(line)
            q[v["path"]] = v
    img_rows = []
    for path, v in q.items():
        rp = f"{WORK}/vision/results/{img_key(path)}.json"
        if not os.path.exists(rp):
            continue
        try:
            r = json.load(open(rp))
        except Exception:
            continue
        if not (r.get("description") or "").strip():
            continue
        img_rows.append({
            "incident_id": v["incident_id"], "file_id": v["file_id"], "image_path": path,
            "origin": v["origin"], "label": v["label"],
            "description": pg_text(r["description"][:8000]),
            "has_text": bool(r.get("has_text")),
            "transcription": pg_text(r.get("transcription")) or None,
            "shows": [pg_text(x) for x in r.get("shows") or []] or None,
            "evidence": pg_text(r.get("evidence")) or None,
            "parser_version": PARSER_VERSION,
        })
    post_sized("clancy_dn_image_readings?on_conflict=image_path,parser_version", img_rows)
    print(f"image readings loaded: {len(img_rows)} (of {len(q)} queued)")


# ---------------------------------------------------------------- promote

def promote():
    sql("""
    ALTER TABLE clancy_dn_incidents
      ADD COLUMN IF NOT EXISTS doc_conclusions text[],
      ADD COLUMN IF NOT EXISTS doc_lessons text[],
      ADD COLUMN IF NOT EXISTS doc_method_failures text[],
      ADD COLUMN IF NOT EXISTS doc_sources jsonb,
      ADD COLUMN IF NOT EXISTS doc_enriched_at timestamptz,
      ADD COLUMN IF NOT EXISTS doc_parser_version text;
    """)
    docs = rest_all(f"clancy_dn_doc_extracts?select=incident_id,file_id,file_name,doc_class,"
                    f"conclusions,lessons,method_failures&parser_version=eq.{PARSER_VERSION}")
    imgs = rest_all(f"clancy_dn_image_readings?select=incident_id,file_id,has_text,transcription,"
                    f"description&parser_version=eq.{PARSER_VERSION}")
    by_inc = {}
    for d in docs:
        b = by_inc.setdefault(d["incident_id"], {"c": [], "l": [], "m": [], "src": []})
        b["c"] += d.get("conclusions") or []
        b["l"] += d.get("lessons") or []
        b["m"] += d.get("method_failures") or []
        b["src"].append({"file_id": d["file_id"], "name": d["file_name"], "class": d["doc_class"]})
    img_n = {}
    for i in imgs:
        img_n[i["incident_id"]] = img_n.get(i["incident_id"], 0) + 1
    n = 0
    for iid, b in by_inc.items():
        def dedupe(xs):
            out, seen = [], set()
            for x in xs:
                k = re.sub(r"\W+", " ", x.lower()).strip()
                if k and k not in seen:
                    seen.add(k); out.append(x)
            return out
        payload = {
            "doc_conclusions": dedupe(b["c"]) or None,
            "doc_lessons": dedupe(b["l"]) or None,
            "doc_method_failures": sorted(set(b["m"])) or None,
            "doc_sources": {"documents": b["src"], "images_read": img_n.get(iid, 0)},
            "doc_enriched_at": "now()",
            "doc_parser_version": PARSER_VERSION,
        }
        payload["doc_enriched_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()
        rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH", payload)
        n += 1
    print(f"promoted onto {n} damage records (doc_* columns only)")


# ---------------------------------------------------------------- stats

def stats():
    r = sql("""
    SELECT
      count(*) FILTER (WHERE i.fy='FY26/27') AS damages,
      count(*) FILTER (WHERE i.fy='FY26/27' AND (b.lessons_learnt IS NULL OR btrim(b.lessons_learnt)='')) AS v1_no_lesson,
      count(*) FILTER (WHERE i.fy='FY26/27' AND i.doc_lessons IS NOT NULL) AS v2_has_doc_lessons,
      count(*) FILTER (WHERE i.fy='FY26/27' AND i.doc_conclusions IS NOT NULL) AS v2_has_doc_cause,
      count(*) FILTER (WHERE i.fy='FY26/27' AND i.doc_method_failures IS NOT NULL) AS v2_has_method_failures,
      round(avg(length(b.lessons_learnt)) FILTER (WHERE i.fy='FY26/27' AND b.lessons_learnt<>'')) AS v1_avg_lesson_chars
    FROM clancy_dn_incidents i
    LEFT JOIN clancy_dn_baseline_pre_enrichment b ON b.id=i.id
    """)
    print(json.dumps(r, indent=1))
    print("\nmethod failures counted across this year's damages:")
    rows = sql("""
    SELECT unnest(doc_method_failures) AS failure, count(*) AS damages
    FROM clancy_dn_incidents WHERE fy='FY26/27' AND doc_method_failures IS NOT NULL
    GROUP BY 1 ORDER BY 2 DESC
    """)
    for r2 in rows or []:
        print(f"  {r2['damages']:3}  {r2['failure']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.load:
        load()
    if a.promote:
        promote()
    if a.stats:
        stats()
    if not any([a.load, a.promote, a.stats]):
        ap.print_help()


if __name__ == "__main__":
    main()
