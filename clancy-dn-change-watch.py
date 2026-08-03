#!/usr/bin/env python3
"""clancy-dn-change-watch.py — the Depot's "what changed on old records" page.

Renders `public.clancy_dn_change_ledger` (fed by clancy-dn-change-sweep.py from Depotnet's
own per-incident audit trail, and by clancy-dn-import.py's sheet-level diffs) into a Depot
page: every amendment to a damage record — who, when, and what it said before — newest
first, with deletions given their own card. Born of damage 152586.

The page quotes Depotnet's own timeline text verbatim; publish uses the same
damage_review_override pattern as the glossary (verbatim-source exception).

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-change-watch.py --local /tmp/change-watch.html
  VAULT=/tmp/pbs python3 clancy-dn-change-watch.py --publish
"""
import os, sys, json, argparse, datetime, subprocess, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clancy_dn_ui as ui

MK = "clancy-damage-change-watch"
EDIT_TYPES = ("Incident Amended", "Report Amended", "Question Response Updated",
              "Action Amended", "Action Reopened", "Sheet field changed", "Witness Amended")
DELETE_TYPES = ("Photo Deleted", "Document Deleted")
CAP = 150

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


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def vocab_gate(text, label):
    r = subprocess.run([sys.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                       input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"VOCAB GATE FAILED on {label}:\n{r.stdout}{r.stderr}")
        sys.exit(1)


PAGE_CSS = """
.cwrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
.cintro{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px 24px;
 margin-bottom:22px;box-shadow:var(--sh-2)}
.cstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0 26px}
.cstat{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--sh-1)}
.cstat .n{font-size:26px;font-weight:800;letter-spacing:-.02em}
.cstat .l{font-size:12px;color:var(--faint);margin-top:2px}
.cstat.warn .n{color:#c62828}
.cgrp{margin:26px 0 12px}
.cgrp h2{font-size:19px;letter-spacing:-.02em}
.cgrp .sub{color:var(--faint);font-size:13px;margin-top:2px}
.dmg{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 18px;
 margin-top:12px;box-shadow:var(--sh-1)}
.dmg .dh{font-weight:800;font-size:14.5px;margin-bottom:8px}
.dmg .dh a{color:inherit;text-decoration:none}
.chg{display:flex;gap:10px;padding:7px 0;border-top:1px dashed var(--line);font-size:13px;line-height:1.5}
.chg:first-of-type{border-top:0}
.chg .when{white-space:nowrap;color:var(--faint);font-variant-numeric:tabular-nums;min-width:118px}
.chg .who{white-space:nowrap;font-weight:700;min-width:150px}
.chg .what{color:var(--mid);overflow-wrap:anywhere}
.chg .typ{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
 border-radius:12px;padding:1px 8px;margin-right:6px;background:#fdeeee;color:#a33;vertical-align:1px}
.chg .typ.del{background:#fff3e0;color:#a55e00}
.capnote{color:var(--faint);font-size:12.5px;margin-top:14px}
"""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_when(ts):
    if not ts:
        return "?"
    return ts[:16].replace("T", " ")


def build():
    types_in = ",".join(f'"{t}"' for t in EDIT_TYPES)
    edits = rest(f"clancy_dn_change_ledger?select=incident_id,history_type,detail,changed_by,changed_at"
                 f"&history_type=in.({urllib.request.quote(types_in)})"
                 f"&order=changed_at.desc.nullslast&limit={CAP}")
    dels_in = ",".join(f'"{t}"' for t in DELETE_TYPES)
    dels = rest(f"clancy_dn_change_ledger?select=incident_id,history_type,detail,changed_by,changed_at"
                f"&history_type=in.({urllib.request.quote(dels_in)})"
                f"&order=changed_at.desc.nullslast&limit=60")
    def _count(filt=""):
        req = urllib.request.Request(f"{URL}/rest/v1/clancy_dn_change_ledger?select=id{filt}",
            headers={**H, "Prefer": "count=exact", "Range": "0-0", "Range-Unit": "items"})
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range") or "0/0"
            return int(cr.split("/")[-1])
    types_q = ",".join(f'"{t}"' for t in EDIT_TYPES)
    dels_q = ",".join(f'"{t}"' for t in DELETE_TYPES)
    total = _count()
    n_edits = _count(f"&history_type=in.({urllib.request.quote(types_q)})")
    n_dels = _count(f"&history_type=in.({urllib.request.quote(dels_q)})")
    covered = rest("clancy_dn_incidents?select=id&raw_api=not.is.null",
                   headers={"Prefer": "count=exact", "Range": "0-0", "Range-Unit": "items"})
    when = datetime.datetime.now().strftime("%d %B %Y %H:%M")

    body = ['<div class="cwrap">',
            '<div class="cintro"><b>Every change to a damage record, on the record.</b> '
            'Depotnet keeps an audit trail on each damage &mdash; who changed what, when, and what it said '
            'before. The export sheets never show it, so this page reads it directly from each '
            'damage&#8217;s stored payload, together with the field-level differences each fresh sheet '
            'import finds. Edits to closed damages, reworded conclusions and deleted evidence all '
            'land here, newest first. Coverage: the two current financial years&#8217; damages; older '
            'years join when their records are captured.</div>',
            '<div class="cstats">',
            f'<div class="cstat"><div class="n">{total:,}</div><div class="l">audit entries banked</div></div>',
            f'<div class="cstat warn"><div class="n">{n_edits:,}</div><div class="l">amendments to records</div></div>',
            f'<div class="cstat warn"><div class="n">{n_dels:,}</div><div class="l">photos or documents deleted</div></div>',
            '</div>']

    body.append('<div class="cgrp"><h2>The edit trail</h2>'
                '<div class="sub">Amendments in Depotnet&#8217;s own words, with the value before the change '
                'where Depotnet records it. Newest first.</div></div>')
    cur = None
    for r in edits or []:
        if r["incident_id"] != cur:
            if cur is not None:
                body.append("</div>")
            cur = r["incident_id"]
            body.append(f'<div class="dmg"><div class="dh">DAMAGE {cur}</div>')
        typ = r.get("history_type") or ""
        body.append('<div class="chg">'
                    f'<span class="when">{fmt_when(r.get("changed_at"))}</span>'
                    f'<span class="who">{esc(r.get("changed_by") or "sheet import")}</span>'
                    f'<span class="what"><span class="typ">{esc(typ)}</span>{esc(r["detail"])}</span></div>')
    if cur is not None:
        body.append("</div>")
    body.append(f'<div class="capnote">Showing the most recent {CAP} amendment entries; the full ledger '
                'holds every entry and is queryable in the Command Centre.</div>')

    body.append('<div class="cgrp"><h2>Deleted evidence</h2>'
                '<div class="sub">Photos and documents removed from damage records. A deletion is not '
                'wrong by itself &mdash; but it belongs on the record.</div></div>')
    cur = None
    for r in dels or []:
        if r["incident_id"] != cur:
            if cur is not None:
                body.append("</div>")
            cur = r["incident_id"]
            body.append(f'<div class="dmg"><div class="dh">DAMAGE {cur}</div>')
        body.append('<div class="chg">'
                    f'<span class="when">{fmt_when(r.get("changed_at"))}</span>'
                    f'<span class="who">{esc(r.get("changed_by") or "?")}</span>'
                    f'<span class="what"><span class="typ del">{esc(r.get("history_type") or "")}</span>'
                    f'{esc(r["detail"])}</span></div>')
    if cur is not None:
        body.append("</div>")

    body.append("</div>")
    return (ui.head("Change watch | Genny&#8217;s Damage Depot", PAGE_CSS)
            + ui.navbar("") + "".join(body) + ui.foot(when) + ui.TAIL)


def publish(html):
    vocab_gate(html, "the rendered change-watch page")
    mod = {"module_key": MK, "slug": MK, "title": "Change watch",
           "section": "Customers", "subsection": "External", "area": "Clancy",
           "tier": "passcode", "passcode": "strive2030",
           "unlock_group": "clancy-depotnet",
           "icon": "👁", "accent": "#97D700", "status": "live", "enabled": False, "sort": 20,
           "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "change-watch"]}
    rest("modules?on_conflict=module_key", "POST", [mod],
         {"Prefer": "resolution=merge-duplicates"})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reason = ("Change watch quotes Depotnet timeline text verbatim - the wording rules own "
              "the verbatim-source exception")
    assert "$cw$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{MK}', $cw${html}$cw$, '{now}') "
        f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, updated_at=EXCLUDED.updated_at")
    print(f"published {MK} ({len(html):,} bytes) — module row enabled=False until Pete OKs the look")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    html = build()
    if a.local:
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} bytes)")
    if a.publish:
        publish(html)


if __name__ == "__main__":
    main()
