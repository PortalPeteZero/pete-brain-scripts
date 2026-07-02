#!/usr/bin/env python3
"""property-state-cc.py — headless property live-state → CC public.property_state.

This thin cron REUSES property-live-state.py's probe functions (check_domain / check_github /
check_vercel / check_supabase / resolve_liveness / drift_flags), takes the per-property declarations
from the CC table `property_declarations`, and writes the dashboard feed straight to
`public.property_state` — the table the CC /m/properties page reads.

`property_declarations` is populated and maintained via `cc-property-api.py --create` / `--set`
(one place, read by every skill — see Library/decisions/2026-06-30-skills-read-live-not-hardcoded.md).
The old `--sync-declarations` README-walk was removed 2026-07-02: the local vault it walked was
retired in the 24 Jun Business OS thin-client cutover.

Runs on Railway (always-on), so the properties dashboard stays live (up/down/drift per property) even
when the Mac is asleep. No vault writes. SEO pulls are skipped headless (the core up/down/drift is the
dashboard's job; SEO needs the SA key — a later add).

Cloud:  property-state-cc.py    # read property_declarations → probe → public.property_state

# CRON-META
# what: Property live-state probe (headless) — reads declarations from property_declarations, probes each property's services, writes public.property_state (the /m/properties feed)
# why: keeps the CC properties dashboard live (up/down/drift per property) from the cloud, Mac-independent
# reads: CC property_declarations; GitHub / Vercel / Supabase / domains live
# writes: CC public.property_state → /m/properties
# entity: command-centre
# schedule: 5 1 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import importlib.util, os, sys, json, urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name, fn):
    s = importlib.util.spec_from_file_location(name, str(_HERE / fn))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


pls = _load("property_live_state", "property-live-state.py")  # reuse its probe functions (no vault walk)


def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(_HERE.parent / "secrets/command-centre-supabase-keys.json"))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key


def cc_rest(method, path, body=None, prefer=None):
    base, key = _cc()
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=45) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else None


def run():
    rows = cc_rest("GET", "property_declarations?select=name,f") or []
    records, digest = [], []
    for r in rows:
        name, f = r["name"], r["f"]
        dom = pls.check_domain(f.get("domains"), f.get("hosting"))
        gh = pls.check_github(f.get("github"), f.get("prod_branch"))
        vc = pls.check_vercel(f.get("vercel_project"))
        sb = pls.check_supabase(f.get("supabase_ref"))
        live, live_host, live_note = pls.resolve_liveness(dom, vc, f.get("status"))
        flags = pls.drift_flags(live, gh, vc, dom, f.get("hosting"), live_note)
        prod_head = (vc.get("deployed") if vc else None) or (gh.get("head") if gh else None)
        if flags:
            digest.append((name, flags))
        records.append({
            "name": name, "ptype": f.get("ptype"), "status_field": f.get("status"),
            "business": f.get("business") or None, "live": live, "host": live_host, "note": live_note,
            "domains": f.get("domains"), "primary_domain": (f.get("domains") or [None])[0],
            "github": f.get("github") or None, "vercel_project": f.get("vercel_project") or None,
            "vercel_team": f.get("vercel_team"),
            "repo_head": gh.get("head") if gh else None, "repo_date": gh.get("date") if gh else None,
            "deployed": vc.get("deployed") if vc else None, "deploy_state": vc.get("state") if vc else None,
            "production_head": prod_head or None, "live_verified": (pls.now_date() if live == "up" else None),
            "aliases": vc.get("aliases") if vc else [], "dns": dom.get("dns") if dom else None,
            "supabase_ref": f.get("supabase_ref") or None, "supabase_ok": sb.get("reachable") if sb else None,
            "ga4": f.get("ga4") or None, "gtm": f.get("gtm") or None, "gsc": f.get("gsc") or None,
            "ahrefs": f.get("ahrefs") or None, "surfer": f.get("surfer") or None,
            "seo": None,  # skipped headless (SEO pulls need the SA key — later add)
            "declared": f.get("declared"), "drift": flags, "checked": pls.now_iso(),
        })
    feed = {"generated": pls.now_iso(), "count": len(records),
            "up": sum(1 for r in records if r["live"] == "up"),
            "anomalies": [{"name": n, "drift": fl} for n, fl in digest], "properties": records}
    # property_state is append-per-run (auto id, created_at) — the /m/properties page reads the latest row.
    cc_rest("POST", "property_state",
            [{"generated": feed["generated"], "count": feed["count"], "up": feed["up"], "payload": feed}],
            prefer="return=minimal")
    print(f"property-state-cc: wrote public.property_state — {len(records)} properties, "
          f"{feed['up']} up, {len(digest)} with drift")
    return 0


if __name__ == "__main__":
    if "--sync-declarations" in sys.argv:
        print("--sync-declarations was removed 2026-07-02: the vault READMEs it walked no longer exist.\n"
              "Declarations are managed directly with cc-property-api.py --create / --set.")
        sys.exit(2)
    sys.exit(run())
