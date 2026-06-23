#!/usr/bin/env python3
"""Load the training-evaluation aggregates into hub.training_evaluations.

Repoints the Training Evaluation dashboard from the standalone Vercel site to the
Sygma Internal Hub (sygmaportal.com/hub/training-evaluation). Run straight after
`jotform-training-eval-aggregate.py` (which writes the aggregate JSONs into the
dashboard repo's /data). The Hub page renders from this table.

Full parity with the standalone (2026-06-08): every top-level view's aggregate is
loaded — overview, metadata, trainers, courses, concerns, monthly, weekly,
finish_times, missing_feedback — plus a combined `share` built from the per-month
share/ files (the standalone's Client-share grid).

Data lives in the Portal's Supabase (rsczwfstwkthaybxhszy), hub schema, staff-read RLS.
Written here via the Supabase Management API (account token).
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

import os  # noqa: E402
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
TOKEN_FILE = f"{VAULT}/Library/processes/supabase-access-token.md"
_EVAL_DD = os.environ.get("EVAL_DATA_DIR")
DATA = Path(_EVAL_DD) if _EVAL_DD else (Path.home() / "code/sygma-training-eval-dashboard/data")
REF = "rsczwfstwkthaybxhszy"

# (db key, source filename). The hyphenated filenames map to underscore keys the
# Hub page reads.
FILE_KEYS = [
    ("overview", "overview.json"),
    ("metadata", "metadata.json"),
    ("trainers", "trainers.json"),
    ("courses", "courses.json"),
    ("concerns", "concerns.json"),
    ("monthly", "monthly.json"),
    ("weekly", "weekly.json"),
    ("finish_times", "finish-times.json"),
    ("missing_feedback", "missing-feedback.json"),
]

# Per-entity drill-down detail files live in these subdirs; each is loaded as a row
# keyed "{dir}/{slug}" (e.g. "trainer/gareth-phillips") for the Hub drill-down routes.
SUBDIRS = ["trainer", "course", "monthly", "weekly", "share"]


def sbp_token():
    env = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if env.startswith("sbp_"):
        return env
    m = re.search(r"sbp_[A-Za-z0-9]+", open(TOKEN_FILE).read())
    if not m:
        sys.exit("No sbp_ token in env or supabase-access-token.md")
    return m.group(0)


def run_sql(sql, token):
    body = json.dumps({"query": sql}).encode()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def upsert(key, payload_text, token):
    js = payload_text.replace("'", "''")
    sql = (
        f"insert into hub.training_evaluations (key, data, updated_at) "
        f"values ('{key}', '{js}'::jsonb, now()) "
        f"on conflict (key) do update set data = excluded.data, updated_at = now();"
    )
    run_sql(sql, token)


def build_share():
    """Combine per-month share/*.json into one array (the Client-share grid)."""
    share_dir = DATA / "share"
    if not share_dir.is_dir():
        return None
    months = []
    for f in sorted(share_dir.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        months.append({
            "ym": f.stem,
            "n": d.get("n"),
            "avg_rating": d.get("avg_rating"),
            "pct_5_star": d.get("pct_5_star"),
            "nps": d.get("nps"),
        })
    return {"months": months} if months else None


def main():
    token = sbp_token()
    loaded = []
    for key, fname in FILE_KEYS:
        f = DATA / fname
        if not f.exists():
            continue
        upsert(key, f.read_text(), token)
        loaded.append(key)

    share = build_share()
    if share:
        upsert("share", json.dumps(share), token)
        loaded.append("share")

    # Per-entity drill-down detail files → keys like "trainer/gareth-phillips".
    detail_count = 0
    for sub in SUBDIRS:
        d = DATA / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            upsert(f"{sub}/{f.stem}", f.read_text(), token)
            detail_count += 1
    if detail_count:
        loaded.append(f"{detail_count} detail rows")

    if not loaded:
        sys.exit(f"No aggregate files found at {DATA} — run jotform-training-eval-aggregate.py first.")

    res = run_sql("select count(*) as n from hub.training_evaluations;", token)
    print("Loaded:", ", ".join(loaded))
    print(f"hub.training_evaluations now holds {res[0]['n']} rows.")


if __name__ == "__main__":
    main()
