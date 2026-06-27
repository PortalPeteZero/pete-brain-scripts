#!/usr/bin/env python3
"""Publish Sygma Training + Soldo surfaces to the Command Centre (additive).

Publishes report snapshots, each defensive (skips if a source is missing):
  training-kpis          <- Businesses/sygma-solutions/training/kpis.md (md -> html)
  training-audit         <- newest Businesses/sygma-solutions/training/audits/*-weekly-audit.md
  training-evaluations   <- ~/code/sygma-training-eval-dashboard/data/{overview,metadata}.json
  soldo-costs            <- soldo-api.py transactions (last ~30 days), grouped by category

Modules: sygma-training (KPIs / Audit / Evaluations tabs) + sygma-soldo, both Sygma > Internal,
private. The underlying crons + the standalone eval dashboard keep running unchanged — additive.
"""
import os, glob, re, json, datetime, importlib.util
from pathlib import Path
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
SCRIPT_DIR = Path(__file__).resolve().parent
HOME = os.path.expanduser("~")

def _cc():
    spec = importlib.util.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def md2html(md):
    out, in_tbl = [], False
    for ln in md.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_tbl:
                out.append("<table style='border-collapse:collapse;width:100%;font-size:13px;margin:6px 0;background:#fff'>"); in_tbl = True
            out.append("<tr>" + "".join(f"<td style='border:1px solid #e2e6f0;padding:5px 8px'>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>"); in_tbl = False
        ln = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        ln = re.sub(r"`([^`]+)`", r"<code>\1</code>", ln)
        if ln.startswith("### "): out.append(f"<h4 style='margin:12px 0 4px'>{ln[4:]}</h4>")
        elif ln.startswith("## "): out.append(f"<h3 style='margin:14px 0 4px;color:#1B2340'>{ln[3:]}</h3>")
        elif ln.startswith("# "): out.append(f"<h2 style='margin:0 0 6px'>{ln[2:]}</h2>")
        elif ln.startswith("> "): out.append(f"<p style='margin:4px 0;color:#667;border-left:3px solid #e2e6f0;padding-left:10px'>{ln[2:]}</p>")
        elif ln.strip() == "": out.append("")
        else: out.append(f"<p style='margin:3px 0'>{ln}</p>")
    if in_tbl: out.append("</table>")
    return "<div style='font:14px/1.5 -apple-system,Segoe UI,sans-serif;padding:16px;color:#0b1220'>" + "\n".join(out) + "</div>"

def _strip_fm(txt):
    if txt.startswith("---"):
        e = txt.find("\n---", 4)
        if e != -1: return txt[e + 4:]
    return txt

def publish_kpis(cc):
    p = f"{VAULT}/Businesses/sygma-solutions/training/kpis.md"
    if not os.path.exists(p): print("  kpis.md missing — skip"); return
    return cc.publish("training-kpis", datetime.date.today().isoformat(),
        {"subject": "Sygma Training KPIs", "html": md2html(_strip_fm(open(p).read()))})

def publish_audit(cc):
    files = sorted(glob.glob(f"{VAULT}/Businesses/sygma-solutions/training/audits/*-weekly-audit.md"))
    if not files: print("  no audit — skip"); return
    latest = files[-1]
    d = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(latest))
    return cc.publish("training-audit", (d.group(1) if d else datetime.date.today().isoformat()),
        {"subject": f"Weekly training audit — {d.group(1) if d else ''}", "html": md2html(_strip_fm(open(latest).read()))})

def publish_evaluations(cc):
    base = f"{HOME}/code/sygma-training-eval-dashboard/data"
    try:
        ov = json.load(open(f"{base}/overview.json")); meta = json.load(open(f"{base}/metadata.json"))
    except Exception as e:
        print(f"  evals source missing — skip ({e})"); return
    kpis = ov.get("kpis", {}); dims = ov.get("dim_avgs", {})
    def kv(label, val): return f"<tr style='border-bottom:1px solid #eef2f7'><td style='padding:7px 10px;color:#475569'>{label}</td><td style='padding:7px 10px;font-weight:700'>{val}</td></tr>"
    krows = "".join(kv(k.replace('_', ' ').title(), v) for k, v in kpis.items())
    drows = "".join(kv(k, (f"{v:.2f}" if isinstance(v, (int, float)) else v)) for k, v in dims.items())
    html = (f"<div style='font:14px/1.55 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Sygma training evaluations</h2>"
            f"<p style='margin:0 0 14px;color:#667'>{meta.get('total_submissions','?')} submissions · {meta.get('first_submission','')[:10]} → {meta.get('last_submission','')[:10]}.</p>"
            f"<h3 style='margin:8px 0 4px;color:#1B2340'>Headline KPIs</h3><table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>{krows}</table>"
            + (f"<h3 style='margin:16px 0 4px;color:#1B2340'>Average by dimension</h3><table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>{drows}</table>" if drows else "")
            + "</div>")
    return cc.publish("training-evaluations", datetime.date.today().isoformat(), {"subject": "Sygma training evaluations", "html": html})

def publish_soldo(cc):
    try:
        spec = importlib.util.spec_from_file_location("soldo_api", str(SCRIPT_DIR / "soldo-api.py"))
        sa = importlib.util.module_from_spec(spec); spec.loader.exec_module(sa)
        s = sa.SoldoAPI()
        today = datetime.date.today(); frm = (today - datetime.timedelta(days=30)).isoformat()
        txns = s.transactions(frm, today.isoformat())
    except Exception as e:
        print(f"  Soldo pull failed — skip ({e})"); return
    rows = txns.get("results", txns) if isinstance(txns, dict) else txns
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0.0, 0]); total = 0.0; n = 0
    for t in (rows or []):
        amt = float(t.get("amount", 0) or 0)
        if amt >= 0:  # spend only (debits negative in Soldo; keep both but sum spend)
            pass
        cat = t.get("category") or t.get("merchantCategory") or t.get("type") or "Uncategorised"
        by_cat[cat][0] += abs(amt); by_cat[cat][1] += 1; total += abs(amt); n += 1
    cats = sorted(by_cat.items(), key=lambda x: -x[1][0])
    crows = "".join(f"<tr style='border-bottom:1px solid #eef2f7'><td style='padding:7px 10px'>{c}</td><td style='padding:7px 10px;text-align:right'>£{v[0]:,.2f}</td><td style='padding:7px 10px;text-align:right;color:#94a3b8'>{v[1]}</td></tr>" for c, v in cats[:25]) or "<tr><td style='padding:7px 10px;color:#888'>no transactions in window</td></tr>"
    html = (f"<div style='font:14px/1.55 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Soldo — last 30 days</h2>"
            f"<p style='margin:0 0 14px;color:#667'>{n} transactions · £{total:,.2f} total movement ({frm} → {today}).</p>"
            f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>"
            f"<thead><tr style='background:#f8fafc;font-size:12px;color:#64748b;text-transform:uppercase'><th style='text-align:left;padding:7px 10px'>Category</th><th style='text-align:right;padding:7px 10px'>Amount</th><th style='text-align:right;padding:7px 10px'>#</th></tr></thead><tbody>{crows}</tbody></table></div>")
    return cc.publish("soldo-costs", today.isoformat(), {"subject": f"Soldo costs — 30d to {today}", "html": html})

def sync_eval_to_cc():
    """Mirror the eval pipeline's data/ into the Command Centre repo (data/eval/) so the
    NATIVE dashboard at /m/sygma-training/evaluations refreshes. Same git pattern as
    Rebase-first git push pattern. Idempotent — only commits + pushes when the weekly eval pipeline
    has actually changed the data (daily no-op otherwise)."""
    import shutil, subprocess, time
    src = Path(HOME) / "code/sygma-training-eval-dashboard/data"
    repo = Path(HOME) / "code/command-centre"
    dest = repo / "data/eval"
    if not src.exists() or not repo.exists():
        print("  eval->CC sync: skip (src or CC repo missing)"); return
    def git(*args, retries=4):
        # github.com from this host is intermittently flaky (SSL_ERROR_SYSCALL); retry network ops.
        for i in range(retries):
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
            if r.returncode == 0: return r
            if i == retries - 1: raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:160]}")
            time.sleep(3)
    try:
        git("fetch", "origin", "main")
        git("pull", "--rebase", "--autostash", "origin", "main")
        if dest.exists(): shutil.rmtree(dest)
        shutil.copytree(src, dest)
        subprocess.run(["git", "-C", str(repo), "add", "data/eval"], check=True)
        if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0:
            print("  eval->CC sync: no change"); return
        git("commit", "-m", "data: refresh training-evaluations dashboard", retries=1)
        git("push", "origin", "main")
        print("  eval->CC sync: pushed (native dashboard will redeploy)")
    except Exception as e:
        print(f"  eval->CC sync: ERROR {e}")

def main():
    cc = _cc()
    # Soldo + Evaluations were REMOVED from the Command Centre on 2026-06-14 — they live on the
    # Sygma Platform now (/hub/cost-base + /hub/training-evaluation, the source of truth). This
    # script now feeds only the CC-unique training KPIs + Weekly Audit. (publish_soldo /
    # publish_evaluations / sync_eval_to_cc remain defined but are no longer called.)
    for name, fn in [("kpis", publish_kpis), ("audit", publish_audit)]:
        try:
            ok = fn(cc); print(f"  {name}: {'published' if ok else 'skipped/failed'}")
        except Exception as e:
            print(f"  {name}: ERROR {e}")

if __name__ == "__main__":
    main()