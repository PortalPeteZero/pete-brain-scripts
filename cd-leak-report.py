#!/usr/bin/env python3
"""
cd-leak-report.py — Bespoke Leak Report engine plumbing (formerly "the Report Brain").
Canary Detect bespoke one-off reports for ANY customer, on a 2-axis model:
  • CUSTOMER type : community | business | individual   (the durable record = cd_communities)
  • SERVICE       : pipework-leak | pool-leak | drain-survey   (the report discipline)
Premises (community / home / business premises: bar, restaurant, hotel, petrol station …) follows
the customer type. NOTE: table is still named cd_communities + community_slug (plumbing, historical);
the CONCEPT is "customer". pool-leak / drain-survey reports get their own template + method wording,
built the first time we do one (the pipework-leak template exists today).

Handles the MECHANICAL parts so each report is fast; the HTML content is still hand-built
section-by-section with Pete (every job differs — flexibility by design).
Operating contract: vault_notes [[cd-leak-report-engine]].

The LEARN-AND-GROW loop (so report N+1 beats N):
  START a repeat community with `pull-community` (durable facts + reusable plan URLs + past
  reports + captured lessons). ADD any new plan/map image with `community-asset`. `publish`
  then writes back to the community record (report history, last_report, optional what-changed
  note), stores per-report learnings, and regenerates the cockpit — so nothing drifts. Capture
  a lesson any time with `learn`.

Commands (customer-* names preferred; community-* kept as aliases):
  pull-job <sale-order-id|S0xxxx>     Pull job facts (partner, dates, lines, total) from Odoo
  pull-customer <slug>                Brief for a (repeat) customer: type + facts + plan URLs + reports + lessons
  publish <dir>                       Publish a report folder to the CC (uploads images to the PUBLIC
                                        'leak-reports' bucket; writes modules + module_content + cd_reports;
                                        writes back customer memory; regenerates the cockpit)
  customer <json>                     Upsert a customer row (public.cd_communities) from a JSON string/file
  customer-asset <slug> <file> [--type schematic|satellite|house-numbers] [--year YYYY]
                                        Store a reusable plan/map image ONCE (public bucket) + record its URL
  learn <report-slug> "<note>"        Capture a lesson on a report (read next time via pull-customer)
  cockpit                             Rebuild the /m/leak-reports index from the registry (auto-run on publish)
  list [reports|communities]          List the registry

A report <dir> holds: preview.html, report.css, NN-*.{jpg,png} assets, and report.json:
  { "slug":"las-margaritas-2026-06-17", "community_slug":"las-margaritas",
    "title":"...", "ref":"CD-LM-2026-0617", "report_type":"survey-repair",
    "survey_date":"2026-06-17", "repair_date":"2026-06-22", "engineer":"Tom",
    "odoo_order":"S01630", "service":"pipework-leak", "methods":["pressure","acoustic","gas"],
    "outcome":"leak-found-repaired",
    // optional, feed the learn-and-grow loop:
    "community_updates":"new isolation valve found at block C", "learnings":["ES caption font too small"] }
A customer <json> holds: { "slug":"...", "name":"...", "type":"community|business|individual",
    "managing_agent":"Olsen Estate" (communities only), "location":"...", "drive_folder_url":"...",
    "odoo_partner_id":807, "network_setup":"...", "integrity_method":"pressure|meter" }
"""
import json, os, re, sys, mimetypes, subprocess, urllib.request, urllib.error

KEYS = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
CC_URL, CC_SR, CC_REF = KEYS["url"].rstrip("/"), KEYS["service_role_key"], KEYS["project_ref"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
PUBLIC_BUCKET = "leak-reports"

def _sbp():
    out = subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "cc-sql.py"),
        "SELECT value FROM secrets WHERE name ILIKE '%supabase-access%' OR name ILIKE '%supabase%token%'"],
        capture_output=True, text=True, env={**os.environ, "VAULT": os.path.dirname(__file__)}).stdout
    v = json.loads(out)[0]["value"]
    return (json.loads(v).get("token", v) if v.strip().startswith("{") else v).strip()

def rest(method, path, data=None, headers=None, raw=False):
    hh = {"apikey": CC_SR, "Authorization": f"Bearer {CC_SR}", "User-Agent": UA}
    hh.update(headers or {})
    body = data if raw else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(CC_URL + path, data=body, method=method, headers=hh)
    try:
        with urllib.request.urlopen(r) as resp: return resp.status, resp.read()
    except urllib.error.HTTPError as e: return e.code, e.read()

def sql(query):
    body = json.dumps({"query": query}).encode()
    r = urllib.request.Request(f"https://api.supabase.com/v1/projects/{CC_REF}/database/query",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {_sbp()}", "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(r) as resp: return resp.read().decode()
    except urllib.error.HTTPError as e: return f"ERR {e.code}: {e.read().decode()[:200]}"

def _odoo_cfg():
    """Odoo config from env, else parsed live from the CC vault note (never hardcoded)."""
    if os.environ.get("ODOO_API_KEY"):
        return {"url": os.environ.get("ODOO_URL", "https://camello-blanco-sl.odoo.com"),
                "db": os.environ.get("ODOO_DB", "camello-blanco-sl"),
                "login": os.environ.get("ODOO_LOGIN", "pete.ashcroft@canary-detect.com"),
                "key": os.environ["ODOO_API_KEY"]}
    # House standard (19 Jul 2026): the key lives in public.secrets, materialised to
    # {VAULT}/Library/processes/secrets/odoo-credentials.json — NOT in the knowledge note.
    _sp = os.path.join(os.environ.get("VAULT", os.path.dirname(__file__)),
                       "Library", "processes", "secrets", "odoo-credentials.json")
    try:
        with open(_sp) as _fh:
            _c = json.load(_fh)
        if all(_c.get(k) for k in ("url", "db", "login", "api_key")):
            return {"url": _c["url"].rstrip("/"), "db": _c["db"],
                    "login": _c["login"], "key": _c["api_key"]}
    except Exception:
        pass
    # Legacy fallback: parse the config note. Retained only until every runtime is proven on the
    # secrets path; the note's key is being removed, so this path will stop working by design.
    out = subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "cc-sql.py"),
        "SELECT body FROM vault_notes WHERE title ILIKE '%odoo%config%' OR title ILIKE '%odoo-api%' LIMIT 1"],
        capture_output=True, text=True, env={**os.environ, "VAULT": os.path.dirname(__file__)}).stdout
    body = json.loads(out)[0]["body"]
    g = lambda label: (re.search(rf"\*\*{re.escape(label)}\*\*\s*\|\s*`([^`]+)`", body) or [None, None])[1]
    return {"url": (g("Instance URL") or "").rstrip("/"), "db": g("Database name"),
            "login": g("Login (API user)"), "key": g("API key")}

def odoo(model, method, args, kwargs=None):
    cfg = _odoo_cfg()
    def rpc(service, meth, params):
        payload = {"jsonrpc": "2.0", "method": "call", "params": {"service": service, "method": meth, "args": params}}
        r = urllib.request.Request(cfg["url"] + "/jsonrpc", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())["result"]
    uid = rpc("common", "authenticate", [cfg["db"], cfg["login"], cfg["key"], {}])
    return rpc("object", "execute_kw", [cfg["db"], uid, cfg["key"], model, method, args, kwargs or {}])

# ── Bespoke Leak Report: registry + community-memory helpers ─────────────────
def _pub_url(path):
    return f"{CC_URL}/storage/v1/object/public/{PUBLIC_BUCKET}/{path}"

def _get(table, query):
    st, body = rest("GET", f"/rest/v1/{table}?{query}")
    return json.loads(body) if st == 200 and body else []

def _patch(table, filt, payload):
    return rest("PATCH", f"/rest/v1/{table}?{filt}", payload,
                {"Content-Type": "application/json", "Prefer": "return=minimal"})[0]

COCKPIT_STYLE = (
    ".wrap{max-width:900px;margin:0 auto;padding:8px 4px 40px;font-family:-apple-system,Segoe UI,Roboto,sans-serif;}"
    ".head h1{font-size:22px;margin:0 0 4px;color:var(--ink,#1b2340);}"
    ".head p{font-size:13px;color:var(--ink2,#5b647a);margin:0 0 18px;}"
    ".comm{border:1px solid var(--line,#e6e8ef);border-radius:12px;margin-bottom:14px;overflow:hidden;background:var(--panel,#fff);}"
    ".ch{padding:12px 16px;background:var(--bg-alt,#f6f7fb);border-bottom:1px solid var(--line,#e6e8ef);display:flex;flex-direction:column;gap:2px;}"
    ".cn{font-weight:700;font-size:15px;color:var(--ink,#1b2340);}"
    ".ca{font-size:12px;color:var(--ink2,#5b647a);}"
    ".reps{padding:8px;display:flex;flex-direction:column;gap:6px;}"
    ".rep{display:block;text-decoration:none;padding:10px 12px;border-radius:8px;border:1px solid var(--line,#e6e8ef);transition:.12s;}"
    ".rep:hover{background:var(--bg-alt,#f6f7fb);border-color:#c9ced9;}"
    ".rt{font-weight:600;font-size:13.5px;color:var(--ink,#1b2340);}"
    ".rd{font-size:11.5px;color:var(--ink2,#5b647a);margin-top:2px;}"
)

def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _bake_orientation(path):
    """Apply any EXIF orientation to the pixels and strip the tag, so every renderer (the
    web browser included) shows the photo the same way. Phone/HEIC-derived photos carry an
    orientation tag (6/8) and otherwise render SIDEWAYS on the report (bit us on the Etna
    gauges + the Los Claveles work-area, 8 Jul 2026). Falls back to the raw bytes if Pillow
    is unavailable or the file is not a normal image."""
    raw = open(path, "rb").read()
    try:
        import io
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        if im.getexif().get(274, 1) in (None, 1):
            return raw  # already upright / no tag — leave the bytes untouched
        buf = io.BytesIO()
        fmt = "PNG" if path.lower().endswith(".png") else "JPEG"
        ImageOps.exif_transpose(im).save(buf, format=fmt, quality=90)
        return buf.getvalue()
    except Exception:
        return raw

def cockpit_gen(verbose=True):
    """Rebuild the /m/leak-reports index straight from the registry (cd_communities +
    cd_reports). DB-driven — called at the end of every publish so it can NEVER drift."""
    comms = _get("cd_communities", "select=slug,name,managing_agent,location&order=name")
    reps = _get("cd_reports", "select=slug,community_slug,title,ref,report_type,outcome,public_url,url,survey_date&status=eq.published&order=survey_date.desc")
    by_comm = {}
    for r in reps:
        by_comm.setdefault(r["community_slug"], []).append(r)
    blocks = []
    for c in comms:
        rows = by_comm.get(c["slug"], [])
        if not rows:
            continue
        reps_html = "".join(
            f'<a class="rep" href="{_esc(r.get("public_url") or r.get("url"))}">'
            f'<div class="rt">{_esc(r.get("title") or r["slug"])}</div>'
            f'<div class="rd">{_esc(r.get("ref",""))} · {_esc(r.get("report_type",""))} · {_esc(r.get("outcome",""))}</div></a>'
            for r in rows)
        agent = _esc(c.get("managing_agent") or "")
        loc = f' · {_esc(c["location"])}' if c.get("location") else ""
        blocks.append(
            f'<div class="comm"><div class="ch"><span class="cn">{_esc(c["name"])}</span>'
            f'<span class="ca">{agent}{loc}</span></div>'
            f'<div class="reps">{reps_html}</div></div>')
    ncomm = len({r["community_slug"] for r in reps})
    html = (f'<div class="wrap"><div class="head"><h1>Bespoke Leak Reports</h1>'
            f'<p>Bespoke Leak Report — every report, indexed. {len(reps)} report(s) across {ncomm} community(ies). '
            f'Public pages on canary-detect.com.</p></div>{"".join(blocks)}</div><style>{COCKPIT_STYLE}</style>')
    st = _patch("module_content", "module_key=eq.leak-reports", {"html": html})
    if verbose:
        print(f"cockpit: {st} ({len(reps)} reports / {ncomm} communities)")
    return len(reps), ncomm

def cmd_cockpit():
    cockpit_gen()

def cmd_community_asset(slug, filepath, atype=None, year=None):
    """Store a reusable plan/map image ONCE in the public bucket + record its real URL on
    the community, so every future report opens with the maps in hand (no Drive re-hunt)."""
    fn = os.path.basename(filepath)
    key = f"community/{slug}/plans/{fn}"
    data = open(filepath, "rb").read()
    ct = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    st, _ = rest("POST", f"/storage/v1/object/{PUBLIC_BUCKET}/{key}", data, {"Content-Type": ct, "x-upsert": "true"}, raw=True)
    url = _pub_url(key)
    rows = _get("cd_communities", f"slug=eq.{slug}&select=plan_assets")
    if not rows:
        print("no such community:", slug); return
    assets = [a for a in (rows[0].get("plan_assets") or []) if a.get("file") != fn]  # dedupe by filename
    entry = {"file": fn, "url": url, "type": atype or "plan"}
    if year:
        entry["year"] = year
    assets.append(entry)
    print(f"upload {fn}: {st} | plan_assets: {_patch('cd_communities', f'slug=eq.{slug}', {'plan_assets': assets})}")
    print("  " + url)

def cmd_pull_community(slug):
    """Open a new job with EVERYTHING in hand: durable facts + reusable plan URLs + past
    reports + lessons captured on this community. Run this FIRST for a repeat community."""
    rows = _get("cd_communities", f"slug=eq.{slug}&select=*")
    if not rows:
        print("no such community:", slug); return
    c = rows[0]
    reps = _get("cd_reports", f"community_slug=eq.{slug}&select=slug,ref,report_type,survey_date,outcome,public_url,extra&order=survey_date.desc")
    print(f"\n=== {c['name']}  ({slug}) ===")
    print(f"Customer type  : {c.get('type')}")
    print(f"Managing agent : {c.get('managing_agent')}")
    print(f"Location       : {c.get('location')}")
    print(f"Odoo partner   : {c.get('odoo_partner_id')}")
    print(f"Drive folder   : {c.get('drive_folder_url')}")
    print(f"Integrity test : {c.get('integrity_method')}")
    print(f"Network        : {c.get('network_setup')}")
    if c.get("sectional_notes"):
        print(f"Sectional notes: {json.dumps(c['sectional_notes'])}")
    if c.get("notes"):
        print(f"Notes          : {c['notes']}")
    print("\n-- Plan / map assets (reuse these) --")
    for a in (c.get("plan_assets") or []):
        yr = f"/{a['year']}" if a.get("year") else ""
        print(f"  [{a.get('type','plan')}{yr}] {a.get('file')}")
        print("      " + (a["url"] if a.get("url") else "(filename only — not stored yet; run community-asset to upload)"))
    lessons = []
    print(f"\n-- Reports ({len(reps)}) --")
    for r in reps:
        print(f"  {r.get('survey_date')}  {r.get('ref')}  {r.get('report_type')}  {r.get('outcome')}  -> {r.get('public_url')}")
        for l in (r.get("extra") or {}).get("learnings", []):
            lessons.append((r["slug"], l))
    if lessons:
        print("\n-- Lessons captured (read BEFORE building) --")
        for s, l in lessons:
            print(f"  • ({s}) {l}")

def cmd_learn(report_slug, note):
    """Capture a lesson on a report so the next job reads it (via pull-community)."""
    rows = _get("cd_reports", f"slug=eq.{report_slug}&select=extra")
    if not rows:
        print("no such report:", report_slug); return
    extra = rows[0].get("extra") or {}
    extra.setdefault("learnings", []).append(note)
    print(f"learn [{report_slug}]: {_patch('cd_reports', f'slug=eq.{report_slug}', {'extra': extra})} ({len(extra['learnings'])} lesson(s))")

def cmd_pull_job(order):
    dom = [["name", "=", order]] if str(order).upper().startswith("S") else [["id", "=", int(order)]]
    ids = odoo("sale.order", "search", [dom])
    if not ids: print("no order found:", order); return
    o = odoo("sale.order", "read", [ids, ["name", "partner_id", "date_order", "amount_untaxed", "amount_total", "order_line"]])[0]
    lines = odoo("sale.order.line", "read", [o["order_line"], ["name", "product_uom_qty", "price_subtotal"]])
    print(json.dumps({"order": o["name"], "partner": o["partner_id"], "date": o["date_order"],
                      "net": o["amount_untaxed"], "total": o["amount_total"],
                      "lines": [{"desc": l["name"], "qty": l["product_uom_qty"], "subtotal": l["price_subtotal"]} for l in lines]}, indent=2))

def cmd_publish(d):
    m = json.load(open(os.path.join(d, "report.json")))
    slug = m["slug"]
    pub = f"{CC_URL}/storage/v1/object/public/{PUBLIC_BUCKET}/{slug}/assets"
    html = open(os.path.join(d, "preview.html")).read()
    html = html.replace('href="report.css"', f'href="/raw/{slug}/assets/report.css"')
    html = re.sub(r'src="([0-9][^"]+\.(?:jpg|png|mp4|webm|mov))"', lambda x: f'src="{pub}/{x.group(1)}"', html, flags=re.I)
    # Report CSS: use the build dir's own if it drops one; otherwise the engine's canonical
    # template (the approved photo-strip + plan-figure layout). Guarantees new reports never
    # ship the old photo-card style, even if the build dir omits report.css.
    css_path = os.path.join(d, "report.css")
    if not os.path.exists(css_path):
        css_path = os.path.join(os.path.dirname(__file__), "report-template", "report.css")
    css = open(css_path).read()
    mod = {"module_key": slug, "title": m["title"], "section": m.get("section", "Canary Detect"),
           "subsection": m.get("subsection", "External"), "slug": slug, "tier": m.get("tier", "public"),
           "groups": ["work-cd"], "tags": ["leak-report"], "icon": "▨", "status": "live", "enabled": True,
           "sort": m.get("sort", 111), "reads": ["module_content"]}
    print("modules:", rest("POST", "/rest/v1/modules", [mod],
          {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])
    for key, payload in [(slug, html), (f"{slug}/assets/report.css", css)]:
        print(f"content[{key[:36]}]:", rest("POST", "/rest/v1/module_content", [{"module_key": key, "html": payload}],
              {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])
    for f in sorted(x for x in os.listdir(d) if re.match(r'^[0-9].*\.(jpg|png|mp4|webm|mov)$', x, re.I)):
        is_img = f.lower().endswith((".jpg", ".png"))
        data = _bake_orientation(os.path.join(d, f)) if is_img else open(os.path.join(d, f), "rb").read()
        ct = mimetypes.guess_type(f)[0] or ("video/mp4" if f.lower().endswith(".mp4") else "application/octet-stream")
        s, _ = rest("POST", f"/storage/v1/object/{PUBLIC_BUCKET}/{slug}/assets/{f}", data, {"Content-Type": ct, "x-upsert": "true"}, raw=True)
        print(f"{'img' if is_img else 'vid'} {f}:", s)
    rep = {k: m.get(k) for k in ["slug", "community_slug", "title", "ref", "report_type", "survey_date",
                                 "repair_date", "engineer", "odoo_order", "outcome"]}
    rep["methods"] = m.get("methods", [])
    rep["service"] = m.get("service", "pipework-leak")  # pipework-leak | pool-leak | drain-survey
    rep["status"] = "published"; rep["url"] = f"/m/{slug}"
    # Public face = the Canary Detect website (Bespoke Leak Report, Phase 2). The /m/<slug>
    # CC page stays as the internal/admin view; shares + cockpit point at public_url.
    rep["public_url"] = f"https://canary-detect.com/reports/{slug}"
    print("cd_reports:", rest("POST", "/rest/v1/cd_reports", [rep],
          {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])

    # ── community-memory write-back: grow the durable record every publish ──
    crow = _get("cd_communities", f"slug=eq.{m['community_slug']}&select=extra,notes")
    if crow:
        cextra = crow[0].get("extra") or {}
        hist = [h for h in cextra.get("report_history", []) if h.get("slug") != slug]  # dedupe
        hist.append({"slug": slug, "ref": m.get("ref"), "date": m.get("survey_date"), "public_url": rep["public_url"]})
        cextra["report_history"] = hist
        cextra["last_report"] = slug
        cpayload = {"extra": cextra}
        upd = m.get("community_updates")  # optional free-text: what changed this job (new valve, revised plan)
        if upd:
            existing = crow[0].get("notes") or ""
            cpayload["notes"] = (existing + "\n" if existing else "") + f"[{m.get('survey_date')}] {upd}"
        print("community write-back:", _patch("cd_communities", f"slug=eq.{m['community_slug']}", cpayload))

    # optional per-report learnings straight from report.json
    if m.get("learnings"):
        rex = _get("cd_reports", f"slug=eq.{slug}&select=extra")[0].get("extra") or {}
        ls = m["learnings"] if isinstance(m["learnings"], list) else [m["learnings"]]
        rex["learnings"] = rex.get("learnings", []) + ls
        print("learnings:", _patch("cd_reports", f"slug=eq.{slug}", {"extra": rex}))

    # rebuild the index from the registry so the cockpit can never drift
    cockpit_gen()

    print("LIVE (internal):", f"https://commandcentre.info/m/{slug}")
    print("LIVE (public):  ", rep["public_url"])

def cmd_community(arg):
    m = json.load(open(arg)) if os.path.exists(arg) else json.loads(arg)
    print("cd_communities:", rest("POST", "/rest/v1/cd_communities", [m],
          {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])

def cmd_list(what="reports"):
    t = "cd_communities" if what.startswith("comm") else "cd_reports"
    cols = "slug,name,managing_agent" if t == "cd_communities" else "slug,ref,report_type,outcome,url,status"
    print(sql(f"SELECT {cols} FROM {t} ORDER BY 1"))

if __name__ == "__main__":
    a = sys.argv[1:] or ["help"]
    def _opt(flag):
        return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else None
    try:
        {"pull-job": lambda: cmd_pull_job(a[1]),
         "publish": lambda: cmd_publish(a[1]),
         # customer = the durable record (community | business | individual). The
         # community-* names are kept as aliases (the table is still cd_communities).
         "customer": lambda: cmd_community(a[1]),
         "community": lambda: cmd_community(a[1]),
         "customer-asset": lambda: cmd_community_asset(a[1], a[2], _opt("--type"), _opt("--year")),
         "community-asset": lambda: cmd_community_asset(a[1], a[2], _opt("--type"), _opt("--year")),
         "pull-customer": lambda: cmd_pull_community(a[1]),
         "pull-community": lambda: cmd_pull_community(a[1]),
         "learn": lambda: cmd_learn(a[1], a[2]),
         "cockpit": lambda: cmd_cockpit(),
         "list": lambda: cmd_list(a[1] if len(a) > 1 else "reports"),
         "help": lambda: print(__doc__)}[a[0]]()
    except KeyError:
        print(__doc__); sys.exit(1)
