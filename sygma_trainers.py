"""sygma_trainers.py — one answer to "who is a trainer", read from the Platform.

Three scripts each kept their own typed list and every one had drifted differently. The worst case:
Kevin Morley held a trainer record and 9 bookings on the 2026 master sheet, yet appeared in NO
automated trainer list, so his diary was never audited and he showed up in no report at all.

TWO DIFFERENT QUESTIONS — do not confuse them:
  core_trainers()  -> employment_type='full_time' on public.trainers. The UTILISATION set only.
  all_trainers()   -> holds a trainer_id on hub.staff_directory. Everyone who trains: audit,
                      KPIs, nights-away, evaluation name-matching.

Calendar address is the WORK EMAIL. Proven 20 Jul 2026 by reading all 11 trainer diaries that way.
Do NOT gate on google_calendar_id — it is blank for trainers whose diaries read perfectly well.
"""
import json, os, urllib.request

PORTAL_REF = "rsczwfstwkthaybxhszy"


def _supabase_token():
    """Resolve the Supabase token the way the rest of the estate does: env var FIRST, then the
    materialised file, then the CC secrets table.

    Why the order matters: a Railway cron gets SUPABASE_TOKEN as an env var and does NOT
    necessarily have the file — railway-bootstrap only writes files for SECRETFILE__* vars. Reading
    the file first (or only) means the job dies on the container with FileNotFoundError while
    working perfectly on a laptop. Caught 20 Jul 2026 before any cron ran, not after.
    """
    import os as _o
    t = (_o.environ.get("SUPABASE_TOKEN") or "").strip()
    if t:
        return t
    p = f"{_o.environ.get('VAULT', '/tmp/pbs')}/Library/processes/secrets/supabase-token"
    if _o.path.exists(p):
        return open(p).read().strip()
    # Last resort: the CC secrets table, reachable from any container that has the CC keys.
    import json as _j, urllib.request as _u
    kp = f"{_o.environ.get('VAULT', '/tmp/pbs')}/Library/processes/secrets/command-centre-supabase-keys.json"
    url = _o.environ.get("CC_SUPABASE_URL"); key = _o.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = _j.loads(open(kp).read()); url, key = d["url"], d["service_role_key"]
    r = _u.Request(url.rstrip("/") + "/rest/v1/secrets?select=value&name=eq.supabase-token",
                   headers={"apikey": key, "Authorization": "Bearer " + key})
    return _j.loads(_u.urlopen(r, timeout=30).read())[0]["value"].strip()


def _q(sql):
    tok = _supabase_token()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PORTAL_REF}/database/query",
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def _short(names):
    """Short display names, disambiguated. Two Steves and two Andy-ish first names collide, so a
    bare first name is not safe: the historical convention is "Steve M" / "Steve S", "Andy F" /
    "Andy B". Add the surname initial only where the first name is shared."""
    from collections import Counter
    first = [n.split()[0] for n in names]
    dupes = {f for f, c in Counter(first).items() if c > 1}
    out = {}
    for n in names:
        parts = n.split()
        out[n] = f"{parts[0]} {parts[-1][0]}" if parts[0] in dupes and len(parts) > 1 else parts[0]
    return out


def all_trainers():
    """Everyone who trains: [{name, email}]. Includes subcontractors; excludes leavers."""
    rows = _q("SELECT full_name AS name, work_email AS email FROM hub.staff_directory "
              "WHERE trainer_id IS NOT NULL AND COALESCE(employment_status,\'\') <> \'Left\' "
              "ORDER BY full_name")
    out = [r for r in rows if (r.get("email") or "").strip()]
    if not out:
        raise RuntimeError("no trainers returned from the Platform — refusing to hand back an "
                           "empty list, which callers would read as 'nobody to check'")
    sh = _short([r["name"] for r in out])
    for r in out:
        r["short"] = sh[r["name"]]
    return out


def core_trainers():
    """The utilisation set only: full-time, excluding the 'Online Trainer' system row."""
    rows = _q("SELECT name, email FROM public.trainers "
              "WHERE employment_type = \'full_time\' AND is_active AND NOT is_system ORDER BY name")
    out = [r for r in rows if (r.get("email") or "").strip()]
    if not out:
        raise RuntimeError("no full-time trainers returned from the Platform")
    sh = _short([r["name"] for r in out])
    for r in out:
        r["short"] = sh[r["name"]]
    return out
