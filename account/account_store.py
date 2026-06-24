#!/usr/bin/env python3
"""account_store — shared PostgREST helper for the Command Centre account_* tables.

The Command Centre runs on Vercel and reads ONLY Supabase. These tables (account_*)
are the live structured record for any customer account (Clancy first). All the
account-* crons write through this helper with the service-role key.

RLS is deny-by-default on these tables; the service-role key bypasses it. Never
expose this key client-side — server / cron use only.
"""
import os
import re
import json
import datetime
import urllib.request
import urllib.error

# env-first (Railway sets CC_SUPABASE_*); fall back to the secrets file on the Mac. $VAULT-aware.
_VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
_URL = os.environ.get("CC_SUPABASE_URL")
_KEY = os.environ.get("CC_SUPABASE_SERVICE_KEY")
if not (_URL and _KEY):
    _KEYS = json.load(open(os.path.join(_VAULT, "Library/processes/secrets/command-centre-supabase-keys.json")))
    _URL, _KEY = _KEYS["url"], _KEYS["service_role_key"]
BASE = _URL.rstrip("/") + "/rest/v1/"
_H = {"apikey": _KEY, "Authorization": "Bearer " + _KEY, "Content-Type": "application/json"}


def _req(method, path, body=None, prefer=None):
    h = dict(_H)
    if prefer:
        h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            t = resp.read().decode()
            return resp.status, (json.loads(t) if t.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def select(table, params=""):
    """GET rows. params is a PostgREST query string e.g. 'customer=eq.clancy&select=id'."""
    s, b = _req("GET", f"{table}?{params}" if params else table)
    return b if isinstance(b, list) else []


def insert(table, rows):
    if not rows:
        return 200
    s, _ = _req("POST", table, rows, prefer="return=minimal")
    return s


def update(table, filt, patch):
    return _req("PATCH", f"{table}?{filt}", patch, prefer="return=minimal")[0]


def delete(table, filt):
    return _req("DELETE", f"{table}?{filt}", prefer="return=minimal")[0]


def upsert(table, rows, on):
    if not rows:
        return 200
    s, _ = _req("POST", f"{table}?on_conflict={on}", rows, prefer="resolution=merge-duplicates,return=minimal")
    return s


def set_state(customer, key, value):
    """Record a cron's last-run / cursor. Read by the Cockpit feed-health strip."""
    return upsert("account_state", [{"customer": customer, "key": key, "value": str(value)}], "customer,key")


def get_state(customer, key):
    r = select("account_state", f"customer=eq.{customer}&key=eq.{key}&select=value")
    return r[0]["value"] if r else None


def daily_note_line(text):
    """Append a status line to today's daily note (the cron-obligation status line),
    mirroring how the rest of the fleet logs. Newest line first under the section."""
    today = datetime.date.today().isoformat()
    path = f"{_VAULT}/Daily/{today}.md"
    line = f"- {datetime.datetime.now().strftime('%H:%M')} {text}\n"
    header = "## Account crons (Automated)\n"
    try:
        content = open(path).read() if os.path.exists(path) else f"# {today}\n"
    except Exception:
        content = f"# {today}\n"
    if header in content:
        content = content.replace(header, header + line, 1)
    else:
        content = content.rstrip() + "\n\n" + header + line
    try:
        open(path, "w").write(content)
    except Exception:
        pass


def refresh_state_of_play(customer):
    """Keep a small auto-maintained live-counts block in state-of-play.md current
    from the store (the source-of-truth rule: the narrative's numbers don't drift)."""
    if customer != "clancy":
        return
    sop = f"{_VAULT}/Customers/SY-Clancy/state-of-play.md"
    if not os.path.exists(sop):
        return
    tbl = ["account_deliverables", "account_actions", "account_meetings", "account_people",
           "account_documents", "account_risks", "account_incidents"]
    c = {t: len(select(t, f"customer=eq.{customer}&select=id")) for t in tbl}
    gw = len(select("account_deliverables", f"customer=eq.{customer}&charge=eq.goodwill&select=id"))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = ("<!-- LIVE-COUNTS (auto, do not edit) -->\n"
             f"> [!info] Live store (auto-refreshed {stamp})\n"
             f"> {c['account_deliverables']} deliverables ({gw} goodwill) · {c['account_actions']} open actions · "
             f"{c['account_meetings']} meetings · {c['account_people']} people · {c['account_documents']} documents · "
             f"{c['account_risks']} risks · {c['account_incidents']} incidents. "
             "Source: the Command Centre `account_*` store ([cockpit](https://commandcentre.info/m/clancy-cockpit)).\n"
             "<!-- /LIVE-COUNTS -->")
    content = open(sop).read()
    if "<!-- LIVE-COUNTS" in content:
        content = re.sub(r"<!-- LIVE-COUNTS.*?/LIVE-COUNTS -->", block, content, flags=re.S)
    else:
        content = content.replace("# Clancy partnership — state of play\n",
                                  "# Clancy partnership — state of play\n\n" + block + "\n", 1)
    try:
        open(sop, "w").write(content)
    except Exception:
        pass
