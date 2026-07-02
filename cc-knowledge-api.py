#!/usr/bin/env python3
"""cc-knowledge-api.py — query the Command Centre knowledge DB (vault_notes + note_links).

Helper-first per [[external-service-routing]]. The brain/notes layer of the Business OS lives in
the CC Supabase (zhexcaflgahdcbzvbyfq). This is the read interface Claude + crons use.

Usage:
  cc-knowledge-api.py search "stripe live billing" [--limit 10]   # ranked full-text search
  cc-knowledge-api.py get <slug-or-vault-path>                     # full note body
  cc-knowledge-api.py backlinks <slug-or-vault-path>              # notes linking TO it
  cc-knowledge-api.py outlinks <slug-or-vault-path>              # notes it links to
  cc-knowledge-api.py stats                                        # row counts by type
"""
import json, urllib.request, urllib.parse, urllib.error, sys, argparse
SEC = "/tmp/pbs/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

def rpc(name, payload):
    r = urllib.request.Request(f"{URL}/rest/v1/rpc/{name}", data=json.dumps(payload).encode(), headers=H, method="POST")
    return json.loads(urllib.request.urlopen(r).read())

def embed_query(text):
    vkey = open(f"{SEC}/voyage-api-key").read().strip()
    r = urllib.request.Request("https://api.voyageai.com/v1/embeddings",
        data=json.dumps({"input": [text], "model": "voyage-3.5-lite", "input_type": "query", "output_dimension": 1024}).encode(),
        headers={"Authorization": f"Bearer {vkey}", "Content-Type": "application/json"}, method="POST")
    v = json.loads(urllib.request.urlopen(r, timeout=60).read())["data"][0]["embedding"]
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

def rest(path):
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", headers={**H, "Prefer": "count=exact"})
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read()), resp.headers.get("Content-Range")

def _resolve(target):
    enc = urllib.parse.quote(target); encmd = urllib.parse.quote(target + ".md")
    rows, _ = rest(f"vault_notes?or=(slug.eq.{enc},vault_path.eq.{enc},vault_path.eq.{encmd})&select=id,title,type,vault_path,body&limit=1")
    return rows[0] if rows else None

def cmd_search(q, lim):
    rows = rpc("search_notes", {"q": q, "lim": lim})
    if not rows: print("no matches"); return
    for r in rows:
        print(f"[{r['type']}] {r['title']}  ({r['vault_path']})")
        if r.get("snippet"): print(f"      …{r['snippet'].strip()}…")

def cmd_semantic(q, lim):
    rows = rpc("match_notes", {"query_embedding": embed_query(q), "match_count": lim})
    if not rows: print("no matches"); return
    for r in rows:
        print(f"[{r['type']}] {r['title']}  (sim {r['similarity']:.2f})  ({r['vault_path']})")

def cmd_get(target, head=0):
    n = _resolve(target)
    if not n: print("not found:", target); return
    print(f"# {n['title']}  [{n['type']}]  {n['vault_path']}\n")
    body = n.get("body") or ""
    # Default: print the WHOLE body. The old silent 6,000-char cap hid deep facts (a confirmed value at
    # char ~9k was invisible to every session). Use --head N for a deliberate, MARKED preview instead.
    if head and len(body) > head:
        print(body[:head] + f"\n…[preview: first {head} of {len(body)} chars — omit --head for the full note]")
    else:
        print(body)

def cmd_backlinks(target):
    n = _resolve(target)
    if not n: print("not found:", target); return
    bl, _ = rest(f"note_links?dst_id=eq.{n['id']}&select=src_id")
    ids = ",".join(x["src_id"] for x in bl)
    if not ids: print("no backlinks"); return
    rows, _ = rest(f"vault_notes?id=in.({ids})&select=title,type,vault_path&order=type")
    for r in rows: print(f"  [{r['type']}] {r['title']}  ({r['vault_path']})")

def cmd_outlinks(target):
    n = _resolve(target)
    if not n: print("not found:", target); return
    ol, _ = rest(f"note_links?src_id=eq.{n['id']}&select=dst_target,dst_id")
    for x in ol:
        mark = "→" if x["dst_id"] else "·"
        print(f"  {mark} {x['dst_target']}")

def cmd_stats():
    _, cr = rest("vault_notes?select=id")
    print("total notes:", cr.split("/")[-1] if cr else "?")

SUBCOMMANDS = ("search", "semantic", "get", "backlinks", "outlinks", "stats")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Query the CC knowledge DB. "
                    "With no recognised subcommand, the args are treated as a search query "
                    "(e.g. `cc-knowledge-api.py voice-principles` == `... search voice-principles`).")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("search", help="ranked full-text search"); s.add_argument("q"); s.add_argument("--limit", type=int, default=10)
    sm = sub.add_parser("semantic", help="semantic / vector search"); sm.add_argument("q"); sm.add_argument("--limit", type=int, default=10)
    g = sub.add_parser("get", help="full note body for a <slug> or vault path"); g.add_argument("target"); g.add_argument("--head", type=int, default=0, help="preview first N chars instead of the full body")
    b = sub.add_parser("backlinks", help="notes linking TO <slug>"); b.add_argument("target")
    o = sub.add_parser("outlinks", help="notes <slug> links to"); o.add_argument("target")
    sub.add_parser("stats", help="row counts by type")
    # Forgiving default: if the first arg isn't a known subcommand (and isn't a help flag),
    # treat the whole argv tail as a `search` query instead of erroring. Keeps every
    # explicit subcommand working exactly as before.
    argv = sys.argv[1:]
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["search"] + argv
    a = ap.parse_args(argv)
    if a.cmd == "search": cmd_search(a.q, a.limit)
    elif a.cmd == "semantic": cmd_semantic(a.q, a.limit)
    elif a.cmd == "get": cmd_get(a.target, a.head)
    elif a.cmd == "backlinks": cmd_backlinks(a.target)
    elif a.cmd == "outlinks": cmd_outlinks(a.target)
    elif a.cmd == "stats": cmd_stats()
    else: ap.print_help()
