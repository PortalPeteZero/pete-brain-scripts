#!/usr/bin/env python3
"""Import a Banco Sabadell .xls account statement into the CC public.bank_statement_lines.

Idempotent: each line gets a content hash (account|op_date|value_date|amount|balance|desc),
so re-feeding overlapping statements never duplicates. Built for Pete's regular CD (Camello
Blanco) feed; the same parser handles any Sabadell "Transactions query" export.

Usage:  VAULT=/tmp/pbs python3 bank-statement-import.py <file.xls> [--entity "Canary Detect"] [--commit]
        (default is a dry run; pass --commit to write)
"""
import sys, os, json, hashlib, urllib.request, urllib.error, datetime
import xlrd

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json",
     "Prefer": "resolution=merge-duplicates,return=minimal"}

def parse_date(v, wb):
    if isinstance(v, float):
        try: return xlrd.xldate.xldate_as_datetime(v, wb.datemode).strftime("%Y-%m-%d")
        except: return None
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except: pass
    return None

def main():
    path = sys.argv[1]
    entity = "Canary Detect"
    commit = "--commit" in sys.argv
    if "--entity" in sys.argv:
        entity = sys.argv[sys.argv.index("--entity") + 1]

    wb = xlrd.open_workbook(path); sh = wb.sheet_by_index(0)
    meta = {"account": None, "holder": None, "currency": "EUR",
            "period_start": None, "period_end": None, "bank": "Banco Sabadell"}
    hdr_row = None
    for r in range(min(20, sh.nrows)):
        cells = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
        joined = " ".join(cells).lower()
        if "account:" in joined:
            meta["account"] = next((c for c in cells[1:] if c), None)
        if "holder:" in joined:
            meta["holder"] = next((c for c in cells[1:] if c), None)
        if "currency:" in joined:
            meta["currency"] = next((c for c in cells[1:] if c), "EUR")
        if "selection:" in joined or "from" in joined and "/" in joined:
            import re
            ds = re.findall(r"(\d{2})\s*/\s*(\d{2})\s*/\s*(\d{4})", " ".join(cells))
            if len(ds) >= 2:
                meta["period_start"] = f"{ds[0][2]}-{ds[0][1]}-{ds[0][0]}"
                meta["period_end"] = f"{ds[1][2]}-{ds[1][1]}-{ds[1][0]}"
        if "operation date" in joined:
            hdr_row = r; break
    if hdr_row is None:
        sys.exit("could not find header row (Operation date)")

    rows = []
    seen_keys = {}  # natural key -> occurrence count, so identical lines hash distinctly but deterministically
    for r in range(hdr_row + 1, sh.nrows):
        op = parse_date(sh.cell_value(r, 0), wb)
        if not op: continue
        desc = str(sh.cell_value(r, 1)).strip()
        val = parse_date(sh.cell_value(r, 2), wb)
        def num(c):
            v = sh.cell_value(r, c)
            try:
                f = float(v)
                return f if f == f and abs(f) != float("inf") else None  # drop NaN/inf
            except: return None
        amount, balance = num(3), num(4)
        ref1 = str(sh.cell_value(r, 5)).strip() if sh.ncols > 5 else ""
        ref2 = str(sh.cell_value(r, 6)).strip() if sh.ncols > 6 else ""
        key = f"{meta['account']}|{op}|{val}|{amount}|{balance}|{desc}"
        occ = seen_keys.get(key, 0); seen_keys[key] = occ + 1  # nth identical line within statement
        h = hashlib.md5(f"{key}|{occ}".encode()).hexdigest()
        rows.append({"entity": entity, "account": meta["account"], "holder": meta["holder"],
                     "bank": meta["bank"], "currency": meta["currency"], "op_date": op,
                     "value_date": val, "description": desc, "amount": amount, "balance": balance,
                     "ref1": ref1 or None, "ref2": ref2 or None,
                     "source_file": os.path.basename(path),
                     "period_start": meta["period_start"], "period_end": meta["period_end"],
                     "line_hash": h})

    print(f"Account {meta['account']} | {meta['holder']} | {meta['currency']} | "
          f"{meta['period_start']}..{meta['period_end']} | {len(rows)} lines parsed")
    credits = sum(x["amount"] for x in rows if x["amount"] and x["amount"] > 0)
    debits = sum(x["amount"] for x in rows if x["amount"] and x["amount"] < 0)
    print(f"  credits +{credits:.2f} | debits {debits:.2f}")
    if not commit:
        print("DRY RUN (pass --commit to write). Sample:")
        for x in rows[:3]: print("  ", x["op_date"], x["amount"], x["description"][:50])
        return
    # upsert in batches
    written = 0
    for i in range(0, len(rows), 100):
        batch = rows[i:i+100]
        req = urllib.request.Request(f"{URL}/rest/v1/bank_statement_lines?on_conflict=line_hash",
            data=json.dumps(batch, allow_nan=False).encode(), headers=H, method="POST")
        urllib.request.urlopen(req, timeout=60); written += len(batch)
    print(f"UPSERTED {written} lines (duplicates merged on line_hash).")

if __name__ == "__main__":
    main()
