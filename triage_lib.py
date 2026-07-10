"""triage_lib.py -- shared plumbing for the Triage Engine helpers (design: [[triage-engine-design]]).

The Triage Engine is the second instance of the shipped EE engine template. This module holds the
plumbing every triage-* helper shares: CC SQL, config keys (the triage-auto-mode kill switch is
re-read IMMEDIATELY before every mutating action, never cached), Telegram delivery, the Gmail
loader, digest assembly (a SWEEP of digest_id-NULL rows, never a run report), and the lint-rules
fence reader (the `json triage-lint-rules` fence in the email-workflow note).

Tables: triage_routing_facts / triage_decisions / triage_digests / triage_sync_actions (+ P5
triage_templates). Kill switch: config key 'triage-auto-mode' ('on' = auto-action allowed).
Sync mode: config key 'triage-sync-mode' ('report' | 'acting').
"""
import os, sys, json, subprocess, urllib.request, urllib.parse, datetime as dt, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")


# ---------- CC SQL ----------

def cc_sql(q):
    """Run SQL against the CC; returns parsed JSON rows (list) or raises on ERROR."""
    r = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"), q],
                       capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if "ERROR" in out and not out.startswith("["):
        raise RuntimeError(f"cc-sql: {out[:400]}")
    try:
        return json.loads(out) if out else []
    except json.JSONDecodeError:
        return []


def esc(s):
    return str(s).replace("'", "''")


# ---------- config keys ----------

def get_config(key, default=None):
    rows = cc_sql(f"SELECT value FROM config WHERE key='{esc(key)}'")
    return rows[0]["value"] if rows else default


def set_config(key, value):
    cc_sql("INSERT INTO config (key, value) VALUES ('%s','%s') "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()"
           % (esc(key), esc(value)))


def auto_mode_on():
    """The kill switch. Re-read IMMEDIATELY before every individual mutating action --
    never cache the value at run start (a mid-run STOP must halt the remainder of a batch)."""
    return (get_config("triage-auto-mode", "off") or "off").strip().lower() == "on"


def trip_kill_switch(reason):
    """Flip the kill switch off + Telegram ping. The reconciler's ONE permitted write."""
    set_config("triage-auto-mode", "off")
    tg_send(f"TRIAGE ENGINE: kill switch TRIPPED — {reason}. All auto-action stopped. "
            f"Say 'start triage' (or flip config triage-auto-mode to 'on') to resume.")


# ---------- secrets / Telegram ----------

def cc_secret(name):
    p = os.path.join(VAULT, "Library", "processes", "secrets", name)
    if os.path.exists(p):
        return open(p).read().strip()
    rows = cc_sql(f"SELECT value FROM secrets WHERE name='{esc(name)}'")
    if rows:
        return rows[0]["value"]
    raise RuntimeError(f"secret '{name}' not found")


def tg_send(text):
    """Deliver to Pete's Telegram (the digest / alert channel). Returns True on success."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or cc_secret("telegram-bot-token")
        chat = os.environ.get("TELEGRAM_ALLOWED_USERID") or cc_secret("telegram-allowed-userid")
        ok = True
        for i in range(0, len(text), 3900):
            data = urllib.parse.urlencode({"chat_id": str(chat).strip(),
                                           "text": text[i:i + 3900]}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                ok = ok and bool(json.loads(r.read().decode()).get("ok"))
        return ok
    except Exception as e:
        print(f"  telegram delivery FAILED: {e}", file=sys.stderr)
        return False


# ---------- Gmail ----------

_g = None

def gmail():
    global _g
    if _g is None:
        spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(VAULT, "gmail-api.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _g = mod.GmailAPI()
    return _g


# ---------- routing facts ----------

def match_fact(sender_addr):
    """Exact-address first, then exact domain, then *.domain suffix. NEVER substring
    (the auto-path anti-lookalike rule). Returns the fact row or None."""
    if not sender_addr or "@" not in sender_addr:
        return None
    addr = sender_addr.strip().lower()
    dom = addr.split("@", 1)[1]
    rows = cc_sql(f"SELECT * FROM triage_routing_facts WHERE sender_pattern='{esc(addr)}'")
    if rows:
        return rows[0]
    rows = cc_sql(f"SELECT * FROM triage_routing_facts WHERE sender_pattern='{esc(dom)}'")
    if rows:
        return rows[0]
    # *.domain rows: match if dom ends with .<base> (real subdomain), never lookalike
    parts = dom.split(".")
    for i in range(1, len(parts) - 1):
        base = ".".join(parts[i:])
        rows = cc_sql(f"SELECT * FROM triage_routing_facts WHERE sender_pattern='*.{esc(base)}'")
        if rows:
            return rows[0]
    return None


# ---------- lint-rules fence ----------

def load_lint_rules():
    """Extract the `json triage-lint-rules` fenced block from the email-workflow note.
    The FENCE LABEL is what the parser keys on (the exact ee-lint mechanism)."""
    import re
    rows = cc_sql("SELECT body FROM vault_notes WHERE vault_path='Library/processes/email-workflow.md'")
    if not rows:
        raise RuntimeError("email-workflow note not found")
    m = re.search(r"```json triage-lint-rules\s*\n(.*?)```", rows[0]["body"], re.S)
    if not m:
        raise RuntimeError("no `json triage-lint-rules` fence in email-workflow — P3 not shipped?")
    return json.loads(m.group(1))


# ---------- digests (assembly is a SWEEP, never a run report) ----------

def assemble_digest(kind="runner", deliver=True):
    """Create a triage_digests row and sweep ALL digest_id-NULL L2+ decision rows and
    sync-action rows (plus stuck applying/sending rows) into it. The empty digest IS the
    runner's heartbeat -- a zero-action window still writes the row. Returns digest_id."""
    rows = cc_sql("INSERT INTO triage_digests (kind, sent_at) VALUES ('%s', now()) RETURNING digest_id" % esc(kind))
    did = rows[0]["digest_id"]
    # sweep: every actioned decision row not yet digested + stuck rows
    cc_sql("UPDATE triage_decisions SET digest_id='%s' WHERE digest_id IS NULL AND "
           "(apply_status IS NOT NULL OR send_status IS NOT NULL)" % did)
    cc_sql("UPDATE triage_sync_actions SET digest_id='%s' WHERE digest_id IS NULL AND apply_status IS NOT NULL" % did)
    dec = cc_sql("SELECT id, thread_id, sender, final_verb, final_label, apply_status, send_status "
                 "FROM triage_decisions WHERE digest_id='%s' ORDER BY decided_at" % did)
    syn = cc_sql("SELECT id, thread_id, action, apply_status, undone_at FROM triage_sync_actions "
                 "WHERE digest_id='%s' ORDER BY created_at" % did)
    stuck = [r for r in dec if r.get("apply_status") == "applying" or r.get("send_status") == "sending"] + \
            [r for r in syn if r.get("apply_status") == "applying"]
    n = len(dec) + len(syn)
    summary = {"decisions": len(dec), "sync_actions": len(syn), "stuck": len(stuck)}
    cc_sql("UPDATE triage_digests SET action_count=%d, summary='%s'::jsonb WHERE digest_id='%s'"
           % (n, esc(json.dumps(summary)), did))
    delivered = False
    if n == 0:
        # heartbeat: nothing to review -- auto-stamp reviewed (an empty digest must not
        # accrue unreviewed-trip pressure; the ROW is the liveness signal)
        cc_sql("UPDATE triage_digests SET delivered=true, reviewed_at=now() WHERE digest_id='%s'" % did)
        delivered = True
    elif deliver:
        lines = [f"Triage digest ({kind}) — {n} action(s):"]
        for r in dec[:20]:
            tag = r.get("send_status") or r.get("apply_status") or "?"
            lines.append(f"• [{tag}] {r.get('final_verb') or '?'} {r.get('final_label') or ''} — {r.get('sender') or r['thread_id']}")
        for r in syn[:20]:
            lines.append(f"• [sync/{r.get('apply_status')}] {r['action']} — thread {r['thread_id']}")
        if stuck:
            lines.append(f"⚠ {len(stuck)} STUCK row(s) (applying/sending) — need a look.")
        lines.append(f"Review + undo: commandcentre.info/m/triage-engine (digest {did[:8]})")
        lines.append("Reply 'reviewed' after checking, or 'stop triage' to halt all auto-action.")
        delivered = tg_send("\n".join(lines))
        cc_sql("UPDATE triage_digests SET delivered=%s WHERE digest_id='%s'"
               % ("true" if delivered else "false", did))
    return did, n, delivered


# ---------- misc ----------

def today():
    return dt.date.today().isoformat()


def log_daily(cron_name, content):
    cc_sql("INSERT INTO daily_log (date, cron_name, content) VALUES ('%s','%s',$tglog$%s$tglog$)"
           % (today(), esc(cron_name), content))
