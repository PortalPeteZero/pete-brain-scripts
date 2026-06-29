#!/usr/bin/env python3
# CRON-META
# what: Daily Sygma Platform CRM activity digest (new enquiries / logged activities / edits, by team member)
# why: Pete instructed the team to log all enquiries on the CRM -- this gives daily visibility that they (and Claude) are doing it
# reads: Portal CRM Supabase (rsczwfstwkthaybxhszy): contacts, contact_activities, pipeline_stages, auth.users
# writes: HTML email to Pete + Jim + Sue + Karen (live). --dry-run renders only; --only <email> narrows recipients.
# entity: sygma
# report: daily-crm-activity-digest
# schedule: 0 18 * * *
# timezone: Atlantic/Canary
# secrets: SUPABASE_TOKEN
# CRON-META-END
#
# Schedule is LOCAL (Atlantic/Canary) — cc-cron.py converts it to the UTC Railway
# needs and re-arms across DST. SUPABASE_TOKEN (management API, reads the Portal CRM)
# is provisioned as an env var by cc-cron; CC keys + Gmail come from the standard
# Railway cron bootstrap (same as training-audit).

import sys, os, json, argparse, datetime, importlib.util, urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = f"{VAULT}/Library/processes/secrets"
PORTAL_REF = "rsczwfstwkthaybxhszy"            # Sygma Platform (Portal CRM) Supabase project
TZ = ZoneInfo("Atlantic/Canary")

# Recipients -- Pete instructed these four to have daily visibility.
CRM_DIGEST_RECIPIENTS = [
    "pete.ashcroft@sygma-solutions.com",
    "jim.ashcroft@sygma-solutions.com",
    "sue.owens@sygma-solutions.com",
    "karen.ryan@sygma-solutions.com",
]

# Days with more new contacts than this are treated as a bulk import and summarised,
# not itemised (the 16 Jun 2026 migration created 1,573 in one day).
IMPORT_SPIKE = 100


def _sb_token():
    """Supabase management-API token. Env first (Railway), file fallback (local)."""
    for k in ("SUPABASE_TOKEN", "SB_TOKEN", "SUPABASE_ACCESS_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    with open(f"{SECRETS}/supabase-token") as f:
        return f.read().strip()


SB_TOKEN = _sb_token()


def pq(sql):
    """Run raw SQL against the Portal CRM via the Supabase management API."""
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PORTAL_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {SB_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "curl/8.7.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def disp(who):
    """Human display name. Emails -> 'Sue Owens'; created_by_name passes through;
    the Enquiry Engine is relabelled as Claude."""
    if not who:
        return "Unattributed / web form"
    if who == "Enquiry Engine":
        return "Claude (Enquiry Engine)"
    if "@" not in who:
        return who
    local = who.split("@")[0]
    return " ".join(p.capitalize() for p in local.split("."))


def day_window(date_str=None):
    """Return (date, start_utc_iso, end_utc_iso) for a calendar day in Atlantic/Canary.
    Default = today so far (00:00 local -> now). --date backfills a whole past day."""
    now_local = datetime.datetime.now(TZ)
    d = datetime.date.fromisoformat(date_str) if date_str else now_local.date()
    start_local = datetime.datetime.combine(d, datetime.time.min, TZ)
    if d >= now_local.date():
        end_local = now_local
    else:
        end_local = start_local + datetime.timedelta(days=1)
    to_utc = lambda x: x.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d, to_utc(start_local), to_utc(end_local)


def gather(start, end):
    new_contacts = pq(f"""
        SELECT c.created_at, c.full_name, c.company_name, c.source, c.type,
               s.name AS stage, u.email AS by_email
        FROM contacts c
        LEFT JOIN pipeline_stages s ON s.id = c.stage_id
        LEFT JOIN auth.users u ON u.id = c.created_by
        WHERE c.created_at >= '{start}' AND c.created_at < '{end}'
        ORDER BY c.created_at""")

    activities = pq(f"""
        SELECT a.created_at, a.activity_type, a.subject, a.created_by_name,
               c.full_name AS contact, c.company_name AS contact_company
        FROM contact_activities a
        LEFT JOIN contacts c ON c.id = a.contact_id
        WHERE a.created_at >= '{start}' AND a.created_at < '{end}'
        ORDER BY a.created_at""")

    # Edits come from the app-layer audit trail (sygma-platform PR #50): contact
    # update/delete entries carry the editor (user_email, resolved server-side) and
    # the changed field names. Pre-deploy this is simply empty — no false data.
    edited = pq(f"""
        SELECT a.created_at, a.action, a.changed_fields, a.user_email,
               COALESCE(a.record_reference, c.full_name) AS full_name,
               c.company_name, s.name AS stage
        FROM audit_logs a
        LEFT JOIN contacts c ON c.id = a.record_id
        LEFT JOIN pipeline_stages s ON s.id = c.stage_id
        WHERE a.table_name = 'contacts' AND a.action IN ('update','delete')
          AND a.created_at >= '{start}' AND a.created_at < '{end}'
        ORDER BY a.created_at""")

    return new_contacts, activities, edited


def build_summary(new_contacts, activities, edited):
    """Per-person tally: new enquiries + activities logged + edits."""
    tally = defaultdict(lambda: {"new": 0, "activities": 0, "edits": 0})
    for c in new_contacts:
        tally[disp(c.get("by_email"))]["new"] += 1
    for a in activities:
        tally[disp(a.get("created_by_name"))]["activities"] += 1
    for e in edited:
        tally[disp(e.get("user_email"))]["edits"] += 1
    rows = sorted(tally.items(), key=lambda kv: (-(kv[1]["new"] + kv[1]["activities"] + kv[1]["edits"]), kv[0]))
    return rows


def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def render_email_html(d, new_contacts, activities, edited):
    n_new, n_act, n_edit = len(new_contacts), len(activities), len(edited)
    spike = n_new > IMPORT_SPIKE

    if n_new == 0 and n_act == 0 and n_edit == 0:
        banner = ("#d97706", "⚠️", "No CRM activity logged today",
                  "No new enquiries, activities, or edits were logged on the Platform CRM.")
    else:
        banner = ("#16a34a", "✅", "CRM activity",
                  f"{n_new} new enquir{'y' if n_new==1 else 'ies'} · {n_act} activit{'y' if n_act==1 else 'ies'} logged · {n_edit} record{'s' if n_edit!=1 else ''} edited")

    h = []
    h.append('<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
             '<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;color:#18181b;">')
    h.append('<div style="max-width:720px;margin:0 auto;padding:24px 16px;">')

    # Header
    h.append('<div style="background:#ffffff;border-radius:10px;padding:20px 24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">')
    h.append('<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#71717a;margin-bottom:6px;">Sygma Platform — CRM activity digest</div>')
    h.append(f'<div style="font-size:22px;font-weight:600;color:#18181b;">{d.strftime("%A %d %B %Y")}</div>')
    h.append('</div>')

    # Banner
    color, icon, label, sub = banner
    h.append(f'<div style="background:{color};color:#ffffff;border-radius:10px;padding:16px 20px;margin-bottom:16px;">')
    h.append(f'<div style="font-size:14px;font-weight:600;">{icon} {label}</div>')
    h.append(f'<div style="font-size:13px;opacity:0.92;margin-top:2px;">{esc(sub)}</div>')
    h.append('</div>')

    def card(title, color_hex, rows_html):
        if not rows_html:
            return
        h.append('<div style="background:#ffffff;border-radius:10px;padding:16px 24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">')
        h.append(f'<div style="font-size:14px;font-weight:600;color:{color_hex};margin-bottom:10px;border-left:3px solid {color_hex};padding-left:10px;">{title}</div>')
        h.append('<table style="width:100%;border-collapse:collapse;font-size:13px;">')
        h.extend(rows_html)
        h.append('</table></div>')

    # Who logged what (summary by person)
    summary = build_summary(new_contacts, activities, edited)
    if summary:
        rows = []
        for name, t in summary:
            bits = []
            if t["new"]:
                bits.append(f'{t["new"]} new')
            if t["activities"]:
                bits.append(f'{t["activities"]} activit{"y" if t["activities"]==1 else "ies"}')
            if t["edits"]:
                bits.append(f'{t["edits"]} edit{"s" if t["edits"]!=1 else ""}')
            rows.append(
                '<tr style="border-bottom:1px solid #f4f4f5;">'
                f'<td style="padding:8px 4px;color:#18181b;font-weight:500;">{esc(name)}</td>'
                f'<td style="padding:8px 0 8px 8px;text-align:right;color:#52525b;">{esc(" · ".join(bits))}</td>'
                '</tr>')
        card("Who logged what", "#2563eb", rows)

    # New enquiries
    if spike:
        card("New contacts", "#7c3aed", [
            '<tr><td style="padding:8px 0;color:#52525b;">'
            f'<strong>{n_new}</strong> contacts created today — looks like a bulk import; not itemised.'
            '</td></tr>'])
    elif new_contacts:
        rows = []
        for c in new_contacts:
            who = disp(c.get("by_email"))
            meta = " · ".join(filter(None, [esc(c.get("company_name")), f'src: {esc(c.get("source") or "—")}', f'stage: {esc(c.get("stage") or "—")}']))
            rows.append(
                '<tr style="border-bottom:1px solid #f4f4f5;">'
                f'<td style="padding:8px 4px;"><div style="color:#18181b;font-weight:500;">{esc(c.get("full_name") or "(no name)")}</div>'
                f'<div style="color:#71717a;font-size:12px;">{meta}</div></td>'
                f'<td style="padding:8px 0 8px 8px;text-align:right;color:#52525b;white-space:nowrap;">{esc(who)}</td>'
                '</tr>')
        card(f"New enquiries / contacts — {n_new}", "#7c3aed", rows)

    # Activities logged
    if activities:
        rows = []
        for a in activities:
            who = disp(a.get("created_by_name"))
            contact = a.get("contact") or "—"
            subj = a.get("subject") or "(no subject)"
            rows.append(
                '<tr style="border-bottom:1px solid #f4f4f5;">'
                f'<td style="padding:8px 8px 8px 0;width:70px;color:#71717a;white-space:nowrap;text-transform:uppercase;font-size:11px;">{esc(a.get("activity_type") or "")}</td>'
                f'<td style="padding:8px 4px;"><div style="color:#18181b;">{esc(subj)}</div>'
                f'<div style="color:#71717a;font-size:12px;">{esc(contact)}</div></td>'
                f'<td style="padding:8px 0 8px 8px;text-align:right;color:#52525b;white-space:nowrap;">{esc(who)}</td>'
                '</tr>')
        card(f"Activities logged — {n_act}", "#0891b2", rows)

    # Edits (from the audit trail — editor + changed fields)
    if edited:
        rows = []
        for c in edited:
            fields = c.get("changed_fields") or []
            what = ", ".join(fields) if fields else (c.get("action") or "edited")
            company = c.get("company_name")
            rows.append(
                '<tr style="border-bottom:1px solid #f4f4f5;">'
                f'<td style="padding:8px 4px;"><div style="color:#18181b;font-weight:500;">{esc(c.get("full_name") or "(contact)")}</div>'
                f'<div style="color:#71717a;font-size:12px;">{esc(company + " · " if company else "")}changed: {esc(what)}</div></td>'
                f'<td style="padding:8px 0 8px 8px;text-align:right;color:#52525b;white-space:nowrap;">{esc(disp(c.get("user_email")))}</td>'
                '</tr>')
        card(f"Records edited — {n_edit}", "#a16207", rows)

    # Footer
    h.append('<div style="background:#ffffff;border-radius:10px;padding:16px 24px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">')
    h.append('<div style="font-size:12px;color:#71717a;line-height:1.6;">')
    h.append('<div>Covers contacts + logged activities on the Platform CRM for the day. '
             '“Activities” are the calls / emails / notes the team log against a contact — the clearest signal an enquiry was recorded.</div>')
    h.append('<div style="margin-top:6px;color:#a1a1aa;">Edits show who changed the record and which fields, from the CRM audit trail. '
             'History runs from the audit roll-out forward — earlier edits aren’t retrospective.</div>')
    h.append('<div style="margin-top:6px;color:#a1a1aa;">Generated by <code>crm-activity-digest.py</code> · runs 18:00 Atlantic/Canary · source of truth: live Platform CRM.</div>')
    h.append('</div></div>')

    h.append('</div></body></html>')
    return "".join(h)


def send_email(subject, body_html, recips):
    spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(SCRIPTS_DIR, "gmail-api.py"))
    g_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(g_mod)
    g = g_mod.GmailAPI()
    return g.send(to=recips[0], subject=subject, body=body_html, cc=recips[1:], html=True)


def main():
    ap = argparse.ArgumentParser(description="Daily Sygma Platform CRM activity digest")
    ap.add_argument("--date", help="report a specific day YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="render HTML to stdout, do not send")
    ap.add_argument("--no-email", action="store_true", help="compute + print summary, no send")
    ap.add_argument("--only", help="send to this single address only (verification run)")
    ap.add_argument("--out", help="also write the rendered HTML to this path")
    args = ap.parse_args()

    d, start, end = day_window(args.date)
    print(f"Window: {d} ({start} -> {end} UTC)", file=sys.stderr)
    new_contacts, activities, edited = gather(start, end)
    print(f"new_contacts={len(new_contacts)} activities={len(activities)} edited={len(edited)}", file=sys.stderr)

    html = render_email_html(d, new_contacts, activities, edited)
    if args.out:
        with open(args.out, "w") as f:
            f.write(html)
        print(f"Wrote {args.out}", file=sys.stderr)

    if args.dry_run:
        print(html)
        return
    if args.no_email:
        print(json.dumps({"date": str(d), "new_contacts": len(new_contacts),
                          "activities": len(activities), "edited": len(edited)}, indent=2))
        return

    recips = [args.only] if args.only else CRM_DIGEST_RECIPIENTS
    subject = f"Sygma CRM activity — {d.strftime('%a %d %b')}"
    er = send_email(subject, html, recips)
    print(f"Sent CRM digest to {recips} (msg {er.get('id','?')})", file=sys.stderr)


if __name__ == "__main__":
    main()
