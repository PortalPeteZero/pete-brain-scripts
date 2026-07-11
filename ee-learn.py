#!/usr/bin/env python3
"""ee-learn.py — the Enquiry Engine LEARNING helper (the engine of the self-learning EE).

A lesson becomes a DATABASE EDIT — proposed in-session, confirmed by Pete, applied live to the EE's
own CC tables — NEVER a note/lesson/memory. Edits are idempotent (UPDATE an existing row, or UPSERT
for customer rates by natural key). Every applied edit is logged to public.ee_edits (old->new, why,
when) for the visible learning trail (the /m/ee-rules window) and for revert.

When the edit resolves a draft correction, pass --touch <enquiry_touch_id>: in the SAME run it writes
source_fixed=true + source_fix onto that enquiry_touches row, so ee-signoff can reach zero (the
source-bearing correction is not reconciled until its source is fixed here).

"Add a column" is a SEPARATE, deliberate op (add-column) — never folded into a routine field edit.

Dry-run by default; pass --apply to write.

Usage:
  ee-learn.py rate open_course_pp amount 185 --why "Pete raised the open rate" --apply
  ee-learn.py catalogue C008 note "..." --why "..." --apply
  ee-learn.py phrase sign_off body "New text" --why "cleaner sign-off" --apply
  ee-learn.py rule lapse_days value 10 --why "give a longer window" --apply
  ee-learn.py customer-rate --customer <ref> onsite_day_rate 900 --why "agreed rate" --apply
  ee-learn.py customer-rate --thread <id> open_course_pp 145 --why "honour" --touch <touch_id> --apply
  ee-learn.py add-column ee_catalogue prereq text --why "capture a prereq nuance" --apply
"""
import sys, os, json, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TOK = (os.environ.get("SUPABASE_TOKEN") or "").strip() or open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
REF = "zhexcaflgahdcbzvbyfq"  # CC Supabase
SESSION = os.environ.get("EE_SESSION", "ee-learn")

# subcommand -> (table, natural-key column)
TARGETS = {"rate": ("ee_rates", "item_key"), "catalogue": ("ee_catalogue", "course_key"),
           "phrase": ("ee_phrases", "context_key"), "rule": ("ee_rules", "name")}
ALLOWED_COLS = {"ee_rates": {"label", "category", "amount", "unit", "phrasing", "note"},
                "ee_catalogue": {"cert_options", "attachments", "note"},
                "ee_phrases": {"label", "body", "note"},
                "ee_rules": {"kind", "value", "body"}}
NUMERIC = {("ee_rates", "amount"), ("ee_customer_rates", "rate")}
JSONB = {("ee_catalogue", "cert_options"), ("ee_catalogue", "attachments")}
ADD_COLUMN_TABLES = {"ee_catalogue", "ee_rates", "ee_customer_rates", "ee_phrases", "ee_rules"}


def cc_sql(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "curl/8.7.1"},
        method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        print("DB ERROR", e.code, e.read().decode()); sys.exit(1)


def lit(v):
    return "'" + str(v).replace("'", "''") + "'"


def valsql(table, field, raw):
    if (table, field) in NUMERIC:
        float(raw)  # validate
        return str(raw)
    if (table, field) in JSONB:
        json.loads(raw)  # validate it is JSON
        return lit(raw) + "::jsonb"
    return lit(raw)


def flag(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def has(name):
    return name in sys.argv


def logedit(table, row_key, field, old, new, why):
    cc_sql("INSERT INTO ee_edits (target_table,row_key,field,old_value,new_value,why,session) VALUES "
           f"({lit(table)},{lit(row_key)},{lit(field)},"
           f"{lit(old) if old is not None else 'NULL'},{lit(new) if new is not None else 'NULL'},"
           f"{lit(why)},{lit(SESSION)})")


def fix_touch(touch_id, why):
    cc_sql(f"UPDATE enquiry_touches SET source_fixed=true, source_fix={lit(why)} WHERE id={lit(touch_id)}")
    print(f"  ↳ enquiry_touches {touch_id}: source_fixed=true")


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    cmd = sys.argv[1]
    why = flag("--why") or ""
    apply = has("--apply")
    touch = flag("--touch")
    if not why:
        print("ERROR: --why is required (every edit records why).") ; sys.exit(2)

    # ── add-column: deliberate schema change ──────────────────────────────────────────
    if cmd == "add-column":
        table, col, coltype = sys.argv[2], sys.argv[3], sys.argv[4]
        if table not in ADD_COLUMN_TABLES:
            print(f"ERROR: {table} not an EE table."); sys.exit(2)
        sql = f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {col} {coltype}"
        print(f"PROPOSED (schema): {sql}   why: {why}")
        if not apply:
            print("Dry-run. Re-run with --apply to write."); return
        cc_sql(sql)
        logedit(table, "(schema)", col, None, coltype, why)
        print(f"APPLIED: column {table}.{col} {coltype} added; logged to ee_edits.")
        return

    # ── customer-rate: upsert a per-customer / per-thread special ──────────────────────
    if cmd == "customer-rate":
        item_key, rate = sys.argv[2], sys.argv[3]
        cust, thread = flag("--customer"), flag("--thread")
        if bool(cust) == bool(thread):
            print("ERROR: pass exactly one of --customer / --thread."); sys.exit(2)
        float(rate)
        keycol, keyval = ("customer_ref", cust) if cust else ("thread_id", thread)
        old = cc_sql(f"SELECT rate FROM ee_customer_rates WHERE {keycol}={lit(keyval)} AND item_key={lit(item_key)}")
        oldv = str(old[0]["rate"]) if old else None
        print(f"PROPOSED: ee_customer_rates[{keycol}={keyval}, {item_key}]  {oldv} -> {rate}   why: {why}")
        if touch: print(f"  will set source_fixed on enquiry_touches {touch}")
        if not apply:
            print("Dry-run. Re-run with --apply to write."); return
        cc_sql(f"INSERT INTO ee_customer_rates ({keycol},item_key,rate,note) VALUES "
               f"({lit(keyval)},{lit(item_key)},{rate},{lit(why)}) "
               f"ON CONFLICT ({keycol},item_key) WHERE {keycol} IS NOT NULL DO UPDATE SET rate=EXCLUDED.rate, note=EXCLUDED.note, updated_at=now()")
        logedit("ee_customer_rates", f"{keycol}={keyval}/{item_key}", "rate", oldv, str(rate), why)
        if touch: fix_touch(touch, why)
        print("APPLIED; logged to ee_edits.")
        return

    # ── field edit on a keyed knowledge row (rate/catalogue/phrase/rule) ───────────────
    if cmd in TARGETS:
        table, keycol = TARGETS[cmd]
        keyval, field, newval = sys.argv[2], sys.argv[3], sys.argv[4]
        if field not in ALLOWED_COLS[table]:
            print(f"ERROR: {field} not editable on {table}. Allowed: {sorted(ALLOWED_COLS[table])}"); sys.exit(2)
        old = cc_sql(f"SELECT {field}::text v FROM public.{table} WHERE {keycol}={lit(keyval)}")
        if not old:
            print(f"ERROR: no {table} row with {keycol}={keyval}. (This tool edits existing rows; add a new row deliberately.)"); sys.exit(2)
        oldv = old[0]["v"]
        vsql = valsql(table, field, newval)
        print(f"PROPOSED: {table}[{keyval}].{field}   {oldv} -> {newval}   why: {why}")
        if touch: print(f"  will set source_fixed on enquiry_touches {touch}")
        if not apply:
            print("Dry-run. Re-run with --apply to write."); return
        cc_sql(f"UPDATE public.{table} SET {field}={vsql}, updated_at=now() WHERE {keycol}={lit(keyval)}")
        logedit(table, keyval, field, oldv, newval, why)
        if touch: fix_touch(touch, why)
        print("APPLIED; logged to ee_edits.")
        return

    print(f"ERROR: unknown subcommand '{cmd}'."); print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
