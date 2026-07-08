#!/usr/bin/env python3
"""
cd-leak-report.py — the Report Brain plumbing (Canary Detect bespoke community leak reports).

Handles the MECHANICAL parts so each report is fast; the HTML content is still hand-built
section-by-section with Pete (every community differs — flexibility by design).
Operating contract: vault_notes [[cd-leak-report-engine]].

Commands:
  pull-job <sale-order-id|S0xxxx>     Pull job facts (partner, dates, lines, total) from Odoo
  publish <dir>                       Publish a report folder (see report.json shape below) to the CC:
                                        uploads images to the PUBLIC 'leak-reports' bucket, writes the
                                        modules row + module_content (html + report.css), registers cd_reports.
  community <json>                    Upsert a public.cd_communities row from a JSON string/file
  list [reports|communities]          List the registry

A report <dir> holds: preview.html, report.css, NN-*.{jpg,png} assets, and report.json:
  { "slug":"las-margaritas-2026-06-17", "community_slug":"las-margaritas",
    "title":"...", "ref":"CD-LM-2026-0617", "report_type":"survey-repair",
    "survey_date":"2026-06-17", "repair_date":"2026-06-22", "engineer":"Tom",
    "odoo_order":"S01630", "methods":["pressure","acoustic","gas"], "outcome":"leak-found-repaired" }
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
    html = re.sub(r'src="([0-9][^"]+\.(?:jpg|png))"', lambda x: f'src="{pub}/{x.group(1)}"', html)
    css = open(os.path.join(d, "report.css")).read()
    mod = {"module_key": slug, "title": m["title"], "section": m.get("section", "Canary Detect"),
           "subsection": m.get("subsection", "External"), "slug": slug, "tier": m.get("tier", "public"),
           "groups": ["work-cd"], "tags": ["leak-report"], "icon": "▨", "status": "live", "enabled": True,
           "sort": m.get("sort", 111), "reads": ["module_content"]}
    print("modules:", rest("POST", "/rest/v1/modules", [mod],
          {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])
    for key, payload in [(slug, html), (f"{slug}/assets/report.css", css)]:
        print(f"content[{key[:36]}]:", rest("POST", "/rest/v1/module_content", [{"module_key": key, "html": payload}],
              {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])
    for f in sorted(x for x in os.listdir(d) if re.match(r'^[0-9].*\.(jpg|png)$', x)):
        data = open(os.path.join(d, f), "rb").read()
        ct = mimetypes.guess_type(f)[0] or "application/octet-stream"
        s, _ = rest("POST", f"/storage/v1/object/{PUBLIC_BUCKET}/{slug}/assets/{f}", data, {"Content-Type": ct, "x-upsert": "true"}, raw=True)
        print(f"img {f}:", s)
    rep = {k: m.get(k) for k in ["slug", "community_slug", "title", "ref", "report_type", "survey_date",
                                 "repair_date", "engineer", "odoo_order", "outcome"]}
    rep["methods"] = m.get("methods", [])
    rep["status"] = "published"; rep["url"] = f"/m/{slug}"
    # Public face = the Canary Detect website (Report Brain Phase 2). The /m/<slug>
    # CC page stays as the internal/admin view; shares + cockpit point at public_url.
    rep["public_url"] = f"https://canary-detect.com/reports/{slug}"
    print("cd_reports:", rest("POST", "/rest/v1/cd_reports", [rep],
          {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})[0])
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
    try:
        {"pull-job": lambda: cmd_pull_job(a[1]), "publish": lambda: cmd_publish(a[1]),
         "community": lambda: cmd_community(a[1]), "list": lambda: cmd_list(a[1] if len(a) > 1 else "reports"),
         "help": lambda: print(__doc__)}[a[0]]()
    except KeyError:
        print(__doc__); sys.exit(1)
