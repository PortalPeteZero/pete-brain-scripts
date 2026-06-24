#!/usr/bin/env python3
"""
cc-map.py — generates the Command Centre live map from the LIVE CC tables.

100% COVERAGE BY CONSTRUCTION: every module is read from public.modules at runtime and rendered in
the same `sort` order the nav uses — there is NO hardcoded section/module list, so a new section,
page, tier or permission is picked up automatically the next run. A coverage self-check compares the
rendered module set against the DB and FAILS LOUDLY (exit 3, writes nothing) if anything is missing —
so we always know if it ever drops something.

Deterministic across environments (reads only the live tables — no vault files), so local + cloud
produce the identical body and the idempotent hash never churns.

Outputs:
  - public.cc_map (CC, single 'latest' row)          — the durable, always-on copy (Railway-friendly)
  - Properties/Pete Command Centre/cc-map.md (vault)  — local mirror, written ONLY when the vault is present

The live /m/map page renders straight from the same tables (lib/map/data.ts) and can't drift; this
cron keeps a queryable snapshot + the orientation mirror current. Runs on Railway (always-on);
env-first CC keys on the cloud, the vault keys file locally.

# CRON-META
# what: Command Centre live map — regenerated 100% from the live CC tables (modules · access · groups · audit)
# why: future-proof orientation snapshot (what's in the CC, where, who can see what) — durable always-on copy + vault .md mirror; the live /m/map already reads the tables directly
# reads: public.modules, profiles, groups, user_groups, module_user_grants, access_audit (CC, read-only)
# writes: public.cc_map (CC, single 'latest' row); Properties/Pete Command Centre/cc-map.md (vault mirror — local only, skipped headless)
# entity: command-centre
# schedule: 30 8 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import json, os, re, sys, hashlib, datetime, urllib.request, urllib.error
from pathlib import Path

VAULT = os.environ.get("VAULT", "/tmp/pbs")
_SECRETS = (Path(VAULT) / "Library/processes/secrets") if os.environ.get("VAULT") \
    else (Path(__file__).resolve().parents[1] / "secrets")
OUT = os.path.join(VAULT, "Properties/Pete Command Centre/cc-map.md")


def cfg():
    """CC Supabase base URL + service key — env-first (Railway), vault keys file locally."""
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        k = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
        url, key = k["url"], k["service_role_key"]
    return url.rstrip("/"), key


def rest(base, key, path):
    req = urllib.request.Request(
        f"{base}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_map_hash(base, key):
    try:
        rows = rest(base, key, "cc_map?id=eq.latest&select=body_hash")
        return rows[0]["body_hash"] if rows else None
    except Exception:
        return None


def upsert_map(base, key, row):
    body = json.dumps([row]).encode()
    req = urllib.request.Request(
        f"{base}/rest/v1/cc_map", data=body, method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    urllib.request.urlopen(req, timeout=30)


def mkey(m):
    return m.get("module_key") or m.get("slug") or str(m.get("id"))


def can_view(m, prof, groups, grants):
    """Faithful port of lib/access.ts canView for a signed-in (or None=anon) viewer."""
    if not m.get("enabled", True) or m.get("status") == "hidden":
        return False
    tier = m.get("tier")
    if tier == "public":
        return True
    mod_groups = m.get("groups") or []
    if tier == "passcode":
        if not prof or prof.get("status") != "approved":
            return False
        if prof.get("is_owner"):
            return True
        if m["module_key"] in grants:
            return True
        return len(mod_groups) > 0 and any(g in groups for g in mod_groups)
    if not prof or prof.get("status") != "approved":
        return False
    if prof.get("is_owner"):
        return True
    if tier == "private":
        return False
    if m["module_key"] in grants:
        return True
    if len(mod_groups) == 0:
        return True
    return any(g in groups for g in mod_groups)


def order_key(m):
    """Sort exactly like the live nav (lib/map/data.ts: order by sort, then title). No hardcoded list."""
    s = m.get("sort")
    return (s if isinstance(s, (int, float)) else 9999, (m.get("title") or "").lower())


def main():
    base, key = cfg()
    try:
        modules = rest(base, key, "modules?select=*")
        profiles = rest(base, key, "profiles?select=*")
        groups = rest(base, key, "groups?select=*")
        user_groups = rest(base, key, "user_groups?select=user_id,group_key")
        grants = rest(base, key, "module_user_grants?select=user_id,module_key")
        audit = rest(base, key, "access_audit?select=at,actor,action,subject,detail&order=at.desc&limit=20")
    except urllib.error.URLError as e:
        print(f"cc-map: cannot reach CC Supabase ({e}) — map NOT regenerated", file=sys.stderr)
        return 2

    by_id = {p["id"]: p for p in profiles}
    def email(pid): return (by_id.get(pid) or {}).get("email", pid or "?")
    def pname(pid): return (by_id.get(pid) or {}).get("display_name") or email(pid)

    ug = {}
    for r in user_groups:
        ug.setdefault(r["user_id"], set()).add(r["group_key"])
    gr = {}
    for r in grants:
        gr.setdefault(r["user_id"], set()).add(r["module_key"])

    all_sorted = sorted(modules, key=order_key)
    live = [m for m in all_sorted if m.get("enabled", True) and m.get("status") != "hidden"]
    hidden = [m for m in all_sorted if not (m.get("enabled", True) and m.get("status") != "hidden")]
    approved = [p for p in profiles if p.get("status") == "approved"]
    owner = next((p for p in approved if p.get("is_owner")), None)

    rendered = set()  # coverage tracker — every module key actually emitted below
    L = []
    # ---- Structure (grouped by area → subsection in nav `sort` order; sections appear dynamically) ----
    L.append("## Structure — what's in the CC")
    L.append("")
    L.append("_By area → subsection, in nav order (`sort`). tier · slug · status. Generated 100% from the "
             "live `modules` table — any new area/page appears automatically; a coverage self-check fails "
             "the run if a module is ever missed._")
    cur_sec = cur_sub = object()
    for m in live:
        sec, sub = m.get("section"), m.get("subsection")
        if sec != cur_sec:
            cur_sec = sec; cur_sub = object()
            L.append(f"\n### {sec or '(no area)'}")
        if sub != cur_sub:
            cur_sub = sub
            L.append(f"\n**{sub or '(flat)'}**")
        grp = (" · groups: " + ", ".join(f"`{g}`" for g in m["groups"])) if m.get("groups") else ""
        pc = f" · code `{m['passcode']}`" if m.get("tier") == "passcode" and m.get("passcode") else ""
        L.append(f"- **{m.get('title')}** (`{m.get('slug')}`) — {m.get('tier')}{pc}{grp}")
        rendered.add(mkey(m))
    if hidden:
        L.append("\n### (hidden / disabled — not live tiles)")
        for m in sorted(hidden, key=lambda m: (m.get("title") or "")):
            why = "disabled" if not m.get("enabled", True) else "hidden"
            L.append(f"- {m.get('title')} (`{m.get('slug')}`) — {why}")
            rendered.add(mkey(m))

    # ---- Coverage self-check — fail loudly if any module went unrendered ----
    all_keys = {mkey(m) for m in modules}
    missing = all_keys - rendered
    if missing:
        print(f"cc-map: ❌ COVERAGE FAILURE — {len(missing)} of {len(all_keys)} modules not rendered: "
              f"{sorted(missing)} — NOT publishing a partial map", file=sys.stderr)
        return 3

    # ---- Access — who can see what ----
    L.append("\n## Access — who can see what")
    L.append("")
    if owner:
        L.append(f"- **{pname(owner['id'])}** ({owner.get('email')}) — **owner**: sees all {len(live)} live modules + every Settings page.")
    for p in approved:
        if p.get("is_owner"):
            continue
        pg, pgr = ug.get(p["id"], set()), gr.get(p["id"], set())
        seen = [m for m in live if can_view(m, p, pg, pgr)]
        pc_locked = [m for m in live if m.get("tier") == "passcode" and not can_view(m, p, pg, pgr)]
        gtxt = (", groups " + ", ".join(f"`{g}`" for g in sorted(pg))) if pg else ", no groups yet"
        names = ", ".join(m.get("title") for m in seen) or "(public pages only)"
        L.append(f"- **{pname(p['id'])}** ({p.get('email')}) — approved{gtxt}. Can open **{len(seen)}**: {names}."
                 + (f" Could unlock with a code: {', '.join(m.get('title') for m in pc_locked)}." if pc_locked else ""))
    pending = [p for p in profiles if p.get("status") != "approved"]
    if pending:
        L.append(f"- _Pending approval: {', '.join(p.get('email','?') for p in pending)}._")

    # ---- By tier ----
    L.append("\n### By tier")
    for tier in ("private", "passcode", "gated", "public"):
        ms = [m for m in live if m.get("tier") == tier]
        if ms:
            L.append(f"- **{tier}** ({len(ms)}): " + ", ".join(m.get("title") for m in ms))

    # ---- Groups ----
    L.append("\n### Groups (gate gated-internal pages)")
    members = {}
    for uid, gs in ug.items():
        for g in gs:
            members.setdefault(g, []).append(pname(uid))
    for g in sorted(groups, key=lambda g: g.get("key", "")):
        k = g.get("key")
        gates = [m.get("title") for m in live if k in (m.get("groups") or [])]
        mem = ", ".join(members.get(k, [])) or "no members yet"
        L.append(f"- `{k}` ({g.get('label')}) — gates: {', '.join(gates) or '—'} — members: {mem}")

    # ---- Recent access changes ----
    L.append("\n## Recent access changes (from `access_audit`)")
    L.append("")
    if not audit:
        L.append("_No access events recorded yet._")
    for a in audit:
        at = (a.get("at") or "")[:16].replace("T", " ")
        det = a.get("detail") or {}
        subj = det.get("email") or (pname(a.get("subject")) if a.get("subject") in by_id else a.get("subject")) or "—"
        extra = " · ".join(f"{k}={v}" for k, v in det.items() if k != "email")
        L.append(f"- `{at}` — **{a.get('action')}** — {subj}" + (f" ({extra})" if extra else ""))

    body = "\n".join(L).rstrip() + "\n"
    body_hash = hashlib.sha1(body.encode()).hexdigest()[:12]
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    area_count = len({m.get("section") for m in live})

    # ---- Write 1: public.cc_map (the durable, always-on copy) — idempotent on body_hash ----
    if fetch_map_hash(base, key) == body_hash:
        print(f"cc-map: cc_map already current (hash {body_hash}, {len(modules)} modules, coverage OK) — table not rewritten")
    else:
        upsert_map(base, key, {
            "id": "latest", "generated_at": now_iso, "updated_at": now_iso,
            "module_count": len(modules), "live_count": len(live), "hidden_count": len(hidden),
            "area_count": area_count, "people_approved": len(approved),
            "coverage_ok": True, "body_hash": body_hash, "body": body,
        })
        print(f"cc-map: cc_map updated ({len(modules)} modules, {len(live)} live, {area_count} areas, "
              f"{len(approved)} approved, hash {body_hash}, coverage OK)")

    # ---- Write 2: vault .md mirror — LOCAL ONLY (skip headless: the vault dir won't exist on Railway) ----
    out_dir = os.path.dirname(OUT)
    if os.path.isdir(out_dir):
        header = (
            "---\n"
            "type: cc-map\n"
            "generated_by: cc-map.py\n"
            f"generated_at: {now_iso}\n"
            f"module_count: {len(modules)}\n"
            f"live_count: {len(live)}\n"
            f"area_count: {area_count}\n"
            f"people_approved: {len(approved)}\n"
            f"body_hash: {body_hash}\n"
            "coverage_ok: true\n"
            "status: active\n"
            "tags: [command-centre, cc-map, generated, access]\n"
            "---\n\n"
            "# Command Centre — live map (generated)\n\n"
            "> [!warning] GENERATED FILE — do not hand-edit\n"
            "> Produced by `Library/processes/scripts/cc-map.py` from the live Command Centre Supabase\n"
            "> tables. 100% coverage — every module rendered from `modules` in nav order, with a\n"
            "> coverage self-check. Durable copy lives in `public.cc_map`; the live `/m/map` page reads\n"
            "> the tables directly. To change anything, change the CC (Settings / SQL / a deploy) and\n"
            "> regenerate — never edit this file. Master ops doc: [[command-centre]].\n\n"
            f"**{len(modules)} modules · {len(live)} live · {area_count} areas · "
            f"{len(approved)} approved {'person' if len(approved)==1 else 'people'} · "
            f"generated {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')}Z.**\n\n"
        )
        out_text = header + body
        skip = False
        if os.path.exists(OUT):
            prev = open(OUT, encoding="utf-8").read()
            mm = re.search(r"body_hash:\s*([0-9a-f]+)", prev)
            if mm and mm.group(1) == body_hash:
                skip = True
        if skip:
            print(f"cc-map: vault mirror already current (hash {body_hash}) — not rewritten")
        else:
            open(OUT, "w", encoding="utf-8").write(out_text)
            print(f"cc-map: wrote vault mirror {OUT}")
    else:
        print("cc-map: headless (no vault) — vault .md mirror skipped, cc_map table is the source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
