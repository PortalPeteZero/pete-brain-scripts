#!/usr/bin/env python3
"""clancy-dn-unmapped.py — the Unmapped Damages page in Genny's Damage Depot.

WHY: Sygma's own damage records were merged into the Depotnet register (clancy_dn_incidents, as
its sygma_* fields). Fourteen of the twenty had a counterpart on Depotnet and carried across whole.
Six did not: searched by date window and by location, nothing on the Depotnet register corresponds
to them. Pete, 31 Jul 2026: "Make one section called unmapped damages and move the 6 to here with
all there info and ensure genny cant see them, then completely delete and remove the old damages
section".

So they live in `clancy_unmapped_damages`, on their own, and this page shows them in full.

TWO RULES THIS PAGE EXISTS TO KEEP:
  1. They are NOT Depotnet damages, so they never join a Depotnet count. Nothing on the hub, the
     register or the year dashboards includes them.
  2. Genny does not read `clancy_unmapped_damages`. Her context is built in
     lib/clancy/depotnet-context.ts and lib/clancy/bot.ts, and neither touches this table. If a
     future change adds a table to her context, this one must stay out of it.

Every field of every record is rendered, because this page is the only home the six now have.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-unmapped.py [--local out.html] [--publish]
"""
import os, json, argparse, datetime, html as H, urllib.request
import clancy_dn_ui as ui

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = "clancy-unmapped-damages"

# Field order as a reader wants it, with the label they should see. Anything in the table but not
# named here is still rendered, under "Everything else on the record" — so a column added later
# cannot silently vanish from the only page these six appear on.
FIELDS = [
    ("damage_date", "Date of damage"), ("customer", "Customer"), ("contract", "Contract"),
    ("contract_ref", "Contract reference"), ("job_ref", "Job reference"),
    ("town", "Town"), ("postcode", "Postcode"), ("location", "Location"),
    ("utility", "Utility"), ("location_type", "Location type"), ("depth", "Depth"),
    ("operatives", "Operatives"), ("supervisor", "Supervisor"),
    ("subcontractor", "Subcontractor"), ("cause", "Cause"), ("status", "Status"),
    ("stage_note", "Stage"), ("summary", "Summary"), ("key_findings", "Key findings"),
    ("next_actions", "What we asked for"), ("narrative", "Narrative"),
    ("documents", "Documents"), ("drive_folder", "Drive folder"),
    ("report_url", "Sygma report"), ("shareable", "Shareable"), ("created_at", "Recorded"),
]
SKIP = {"id", "dn_id", "why_unmapped", "candidate_note", "moved_at"}


def rest(path):
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 headers={"apikey": SR, "Authorization": f"Bearer {SR}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())


PAGE_CSS = """
.rec{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:var(--sh-2);
 margin-bottom:18px;overflow:hidden}
.rec>h3{background:#f7f9fc;border-bottom:1px solid var(--line);padding:15px 20px;font-size:17px;
 font-weight:800;letter-spacing:-.015em;display:flex;justify-content:space-between;gap:12px;
 flex-wrap:wrap;align-items:baseline}
.rec>h3 .when{font-size:12.5px;color:var(--faint);font-weight:600;
 font-variant-numeric:tabular-nums}
.rec .in{padding:18px 20px 16px}
dl.f{display:grid;grid-template-columns:190px 1fr;gap:0}
@media(max-width:680px){dl.f{grid-template-columns:1fr}}
dl.f dt{font-size:12px;font-weight:700;color:var(--faint);padding:9px 14px 9px 0;
 border-top:1px solid #eef1f5;text-transform:uppercase;letter-spacing:.05em}
dl.f dd{font-size:14px;color:var(--mid);padding:9px 0;border-top:1px solid #eef1f5;
 min-width:0;overflow-wrap:anywhere}
@media(max-width:680px){dl.f dt{border-top:1px solid #eef1f5;padding-bottom:0}
 dl.f dd{border-top:0;padding-top:2px}}
dl.f dd ul{margin:0;padding-left:18px}
dl.f dd a{color:var(--green-d);font-weight:700}
.why{background:#fff9f0;border-left:4px solid #b45309;border-radius:0 10px 10px 0;
 padding:12px 16px;font-size:13px;color:var(--mid);margin-bottom:14px}
.cand{background:#f6faf0;border-left:4px solid var(--green);border-radius:0 10px 10px 0;
 padding:12px 16px;font-size:13px;color:var(--mid);margin-top:14px}
.cand b{color:var(--ink)}
"""


def esc(v):
    return H.escape(str(v), quote=False)


def render_value(v):
    if v is None or v == "" or v == []:
        return '<span style="color:#b0b8c1">not recorded</span>'
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in v) + "</ul>"
    s = str(v)
    if s.startswith("http"):
        return f'<a href="{esc(s)}" target="_blank" rel="noopener">{esc(s)}</a> &#8599;'
    return esc(s).replace("\n", "<br>")


def build():
    rows = rest("clancy_unmapped_damages?select=*&order=damage_date.asc")
    total_sy = sql("SELECT count(*) n FROM clancy_damages_snapshot_20260731")[0]["n"]
    merged = sql("SELECT count(*) n FROM clancy_dn_incidents "
                 "WHERE sygma_legacy_id IS NOT NULL")[0]["n"]

    recs = []
    for r in rows:
        named = {k_ for k_, _ in FIELDS}
        body = []
        for key, label in FIELDS:
            if key in r:
                body.append(f"<dt>{label}</dt><dd>{render_value(r[key])}</dd>")
        extra = [k_ for k_ in r if k_ not in named and k_ not in SKIP]
        for key in sorted(extra):
            body.append(f"<dt>{esc(key.replace('_', ' ').title())}</dt><dd>{render_value(r[key])}</dd>")
        when = str(r.get("damage_date") or "")[:10]
        nice = (datetime.datetime.strptime(when, "%Y-%m-%d").strftime("%-d %B %Y")
                if when else "date not recorded")
        cand = (f'<div class="cand"><b>Possible Depotnet match, not confirmed.</b> '
                f'{esc(r["candidate_note"])}</div>' if r.get("candidate_note") else "")
        recs.append(
            f'<div class="rec"><h3><span>{esc(r.get("location") or r.get("town") or "Location not recorded")}'
            f'</span><span class="when">{nice}</span></h3><div class="in">'
            f'<div class="why">{esc(r.get("why_unmapped") or "No Depotnet counterpart found.")}</div>'
            f'<dl class="f">{"".join(body)}</dl>{cand}</div></div>')

    today = datetime.date.today()
    return f"""{ui.head("Unmapped Damages | Genny&#8217;s Damage Depot", PAGE_CSS)}
{ui.navbar()}
{ui.crumbs(("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}"), "Unmapped damages")}
{ui.mast_compact("Sygma records with no Depotnet counterpart", "Unmapped Damages",
                 f"{len(rows)} damages Sygma recorded and worked that do not appear on the "
                 "Depotnet register at all. Held here in full so nothing is lost, and held apart "
                 "so they never inflate a Depotnet figure.")}
<div class="wrap body">

<div class="dnote"><b>What this page is.</b> Sygma kept its own damage records alongside Depotnet.
Those records have been merged into the register: {merged} of {total_sy} matched a Depotnet
incident and carried across whole, and their Sygma summary, findings, narrative and report now sit
on the Depotnet record itself. The {len(rows)} below did not match. Each was searched for by date
window and by location and nothing on the Depotnet register corresponds to it, which means either
the damage was never logged on Depotnet or it was logged in a form we cannot recognise.<br><br>
They are deliberately kept out of the register and out of every count on this site, because they
are not Depotnet damages and reporting them as such would overstate what Depotnet holds. They are
also deliberately outside what Genny reads, so she can never quote one as a Depotnet record.</div>

{"".join(recs)}

<div class="dnote"><b>What to do with these.</b> Each one needs a decision: either it exists on
Depotnet under a description we have not matched, in which case it should be linked, or it was
never raised on Depotnet at all, which is itself worth saying to Clancy. Until that decision is
made they stay here, complete and unattached.</div>

{ui.foot(today.strftime('%-d %b %Y'))}
</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    html = build()
    if a.local:
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} chars)")
    if a.publish:
        mod = {"module_key": MK, "slug": MK, "title": "Unmapped Damages",
               "section": "Customers", "subsection": "External", "area": "Clancy",
               "tier": "passcode", "passcode": "strive2030",
               # one section, one gate: every Depot page shares this group
               "unlock_group": "clancy-depotnet", "icon": "❓", "accent": "#b45309",
               "status": "live", "enabled": True, "sort": 15,
               "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "damages"]}
        req = urllib.request.Request(f"{URL}/rest/v1/modules?on_conflict=module_key",
            data=json.dumps([mod]).encode(),
            headers={"apikey": SR, "Authorization": f"Bearer {SR}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"}, method="POST")
        urllib.request.urlopen(req, timeout=60)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reason = ("Unmapped damages page renders Sygma''s own record verbatim; Sygma prose says "
                  "damage throughout")
        assert "$um$" not in html
        sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
            f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
            f"('{MK}', $um${html}$um$, '{now}') "
            f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
            f"updated_at=EXCLUDED.updated_at;")
        print(f"published {MK} — commandcentre.info/m/{MK}")


if __name__ == "__main__":
    main()
