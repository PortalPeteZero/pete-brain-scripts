#!/usr/bin/env python3
"""sygma-ads-fortnightly-report.py — headless Sygma Google Ads fortnightly performance report.

Cloud rewrite of the old claude-code SKILL.md orchestration (deterministic → headless .py, per
Business OS H7). Pulls last-14d vs prev-14d from the direct Ads API, surfaces waste candidates +
Quality-Score regressions + GA4 paid-attribution health, emails Pete a branded HTML digest, and
publishes it to the CC Sygma-Ads page (Fortnightly tab). Report-only — never mutates the account.

Runs on Railway (1st + 15th, 08:00 Atlantic/Canary). Helpers resolve as flat-repo siblings (dirname),
secrets via the bootstrap. The old vault trend-file + master-negatives-CSV reads are dropped: negatives
come LIVE from the Ads API (better than a stale snapshot), no vault needed.

# CRON-META
# what: Sygma Google Ads fortnightly report — 14d vs prev 14d, waste candidates, QS regressions, GA4 attribution; HTML email to Pete + CC publish
# why: fortnightly ads performance review so Pete can spot waste/QS drift and decide negatives (report-only, never mutates)
# reads: Google Ads API (ads-api.py), GA4 (ga4-api.py, property 354127076)
# writes: HTML email to Pete (gmail-api); reports.snapshots 'sygma-ads-fortnightly' (cc_publish) → /m/sygma-ads
# entity: sygma
# report: sygma-ads
# schedule: 0 8 1,15 * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import importlib.util as _il, os, sys, traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("Atlantic/Canary")
ADVERTISER = "173-909-0181"
GA4_PROPERTY = "354127076"
PETE = "pete.ashcroft@sygma-solutions.com"
ORANGE, TEAL, NAVY = "#F5A623", "#2BBFBF", "#1B2340"
INTENT = ("training", "course", "near me", "cost", "price", "book", "certificate",
          "accreditation", "qualification", "accredited", "courses")


def _load(name, fname):
    s = _il.spec_from_file_location(name, str(_HERE / fname))
    m = _il.module_from_spec(s); s.loader.exec_module(m); return m


def gbp(micros):
    try: return round(int(micros) / 1_000_000, 2)
    except (TypeError, ValueError): return 0.0


def agg(rows):
    """Top-line aggregate over keyword_view rows."""
    cost = sum(int((r.get("metrics") or {}).get("costMicros") or 0) for r in rows)
    clicks = sum(int((r.get("metrics") or {}).get("clicks") or 0) for r in rows)
    conv = sum(float((r.get("metrics") or {}).get("conversions") or 0) for r in rows)
    return {"spend": gbp(cost), "clicks": clicks, "conv": round(conv, 1),
            "cpa": round(gbp(cost) / conv, 2) if conv else None}


def delta(now, prev):
    def d(a, b):
        if b in (None, 0): return "—"
        return f"{'+' if a >= b else ''}{round((a - b) / b * 100)}%"
    return {"spend": d(now["spend"], prev["spend"]), "clicks": d(now["clicks"], prev["clicks"]),
            "conv": d(now["conv"], prev["conv"]),
            "cpa": d(now["cpa"] or 0, prev["cpa"] or 0) if (now["cpa"] and prev["cpa"]) else "—"}


def kw_query(start, end):
    return (f"SELECT campaign.name, ad_group.name, ad_group_criterion.criterion_id, "
            f"ad_group_criterion.keyword.text, ad_group_criterion.quality_info.quality_score, "
            f"metrics.cost_micros, metrics.clicks, metrics.conversions "
            f"FROM keyword_view WHERE segments.date BETWEEN '{start}' AND '{end}' "
            f"AND ad_group_criterion.status != 'REMOVED'")


def term_query(start, end):
    return (f"SELECT search_term_view.search_term, ad_group.name, metrics.cost_micros, "
            f"metrics.clicks, metrics.conversions FROM search_term_view "
            f"WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.clicks > 0 "
            f"ORDER BY metrics.cost_micros DESC")


def main():
    today = datetime.now(TZ).date()
    if today.weekday() >= 5:
        print("SKIPPED_WEEKEND"); return 0
    last_end = today - timedelta(days=1); last_start = today - timedelta(days=14)
    prev_end = today - timedelta(days=15); prev_start = today - timedelta(days=28)
    rng = f"{last_start:%d %b} – {last_end:%d %b %Y}"

    ads = _load("ads_api", "ads-api.py").GoogleAdsAPI()
    kw_now = ads.query(kw_query(f"{last_start}", f"{last_end}"))
    kw_prev = ads.query(kw_query(f"{prev_start}", f"{prev_end}"))
    terms = ads.query(term_query(f"{last_start}", f"{last_end}"))
    a_now, a_prev = agg(kw_now), agg(kw_prev)
    d = delta(a_now, a_prev)

    # --- waste candidates: clicked, no conversions, no commercial-intent modifier, top 15 by spend ---
    waste = []
    for r in terms:
        st = (r.get("searchTermView") or {}).get("searchTerm", "")
        m = r.get("metrics") or {}
        if float(m.get("conversions") or 0) > 0:
            continue
        if any(tok in st.lower() for tok in INTENT):
            continue
        waste.append((st, gbp(m.get("costMicros")), int(m.get("clicks") or 0)))
    waste = sorted(waste, key=lambda x: -x[1])[:15]

    # --- QS regressions: prev→now QS drop ≥2, or QS 1-3 with >£10 spend (this period), top 10 by spend ---
    def qs_map(rows):
        out = {}
        for r in rows:
            c = r.get("adGroupCriterion") or {}
            cid = c.get("criterionId")
            qs = (c.get("qualityInfo") or {}).get("qualityScore")
            if cid and qs is not None:
                out[cid] = (qs, (c.get("keyword") or {}).get("text", "?"))
        return out
    qprev = qs_map(kw_prev)
    spend_now = {}
    for r in kw_now:
        c = r.get("adGroupCriterion") or {}
        cid = c.get("criterionId")
        if cid:
            spend_now[cid] = spend_now.get(cid, 0) + gbp((r.get("metrics") or {}).get("costMicros"))
    qs_reg = []
    for cid, (qs, text) in qs_map(kw_now).items():
        sp = spend_now.get(cid, 0)
        dropped = cid in qprev and (qprev[cid][0] - qs) >= 2
        low = qs <= 3 and sp > 10
        if dropped or low:
            why = f"QS {qprev[cid][0]}→{qs}" if dropped else f"QS {qs}, £{sp:.0f} spend"
            qs_reg.append((text, why, sp))
    qs_reg = sorted(qs_reg, key=lambda x: -x[2])[:10]

    # --- GA4 paid-attribution health (best-effort; degrade on failure) ---
    attr = None
    try:
        ga4 = _load("ga4_api", "ga4-api.py").GA4API()
        rows = ga4.run_report(GA4_PROPERTY, ["sessionSourceMedium"], ["eventCount"],
                              date_ranges=[{"startDate": f"{last_start}", "endDate": f"{last_end}"}],
                              dimension_filter={"filter": {"fieldName": "eventName",
                                  "stringFilter": {"value": "form_submit"}}}, limit=50)
        paid = sum(float(r.get("eventCount", 0)) for r in rows
                   if (r.get("sessionSourceMedium") or "").lower().strip() == "google / cpc")
        tot = sum(float(r.get("eventCount", 0)) for r in rows)
        if tot:
            attr = {"paid": int(paid), "total": int(tot), "pct": round(paid / tot * 100)}
    except Exception as e:
        print(f"  GA4 attribution degraded: {e}", file=sys.stderr)

    # --- HTML ---
    def cell(label, val, dl):
        return (f"<td style='padding:10px 14px;border:1px solid #eee'><div style='color:#888;font-size:12px'>{label}</div>"
                f"<div style='font-size:20px;color:{NAVY};font-weight:700'>{val}</div>"
                f"<div style='font-size:12px;color:{TEAL}'>{dl} vs prev</div></td>")
    rows_html = (f"<tr>{cell('Spend', '£' + format(a_now['spend'], ',.0f'), d['spend'])}"
                 f"{cell('Clicks', a_now['clicks'], d['clicks'])}"
                 f"{cell('Conversions', a_now['conv'], d['conv'])}"
                 f"{cell('CPA', ('£' + format(a_now['cpa'], ',.0f')) if a_now['cpa'] else '—', d['cpa'])}</tr>")
    waste_html = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0'>{st}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;text-align:right'>£{sp:.2f}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;text-align:right'>{cl}</td></tr>"
        for st, sp, cl in waste) or "<tr><td style='padding:6px 10px'>None this period 🎉</td></tr>"
    qs_html = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0'>{t}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #f0f0f0'>{w}</td></tr>"
        for t, w, _ in qs_reg) or "<tr><td style='padding:6px 10px'>None 🎉</td></tr>"
    attr_html = (f"<b style='color:{'#1a9c1a' if attr['pct'] >= 60 else '#c0392b'}'>{attr['pct']}% paid</b> "
                 f"({attr['paid']}/{attr['total']} form submits google/cpc)") if attr else "GA4 data unavailable this run"

    html = f"""<div style="font-family:-apple-system,Segoe UI,Arial;max-width:680px;margin:0 auto;color:{NAVY}">
<div style="background:{NAVY};padding:20px 24px;border-radius:8px 8px 0 0">
  <div style="color:{ORANGE};font-size:13px;letter-spacing:1px">SYGMA GOOGLE ADS</div>
  <div style="color:#fff;font-size:22px;font-weight:700">Fortnightly Report · {rng}</div></div>
<div style="padding:18px 24px;background:#fafbfc;border:1px solid #eee;border-top:none">
  <table style="width:100%;border-collapse:collapse;margin-bottom:18px">{rows_html}</table>
  <h3 style="color:{ORANGE};margin:14px 0 6px">Waste candidates (negatives to consider)</h3>
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <tr style="color:#888;font-size:12px"><td>Search term</td><td style="text-align:right">Spend</td><td style="text-align:right">Clicks</td></tr>{waste_html}</table>
  <h3 style="color:{ORANGE};margin:18px 0 6px">Quality-Score regressions</h3>
  <table style="width:100%;border-collapse:collapse;font-size:14px">{qs_html}</table>
  <h3 style="color:{ORANGE};margin:18px 0 6px">Paid-attribution health</h3>
  <p style="font-size:14px;margin:4px 0">{attr_html} <span style="color:#888">(target &gt;60% paid)</span></p>
  <p style="font-size:12px;color:#aaa;margin-top:18px">Report-only — no account changes made. Advertiser {ADVERTISER}.</p>
</div></div>"""

    subject = f"Sygma Ads Fortnightly Report — {rng}"
    if os.environ.get("ADS_DRY"):
        print(f"  [DRY] data pulled OK — would email + publish ({len(html)} chars HTML, "
              f"spend £{a_now['spend']:.0f}, {len(waste)} waste, {len(qs_reg)} QS, attr={attr})")
        print("REPORT_COMPLETE [DRY]"); return 0
    try:
        _load("gmail_api", "gmail-api.py").GmailAPI().send(PETE, subject, html, html=True)
        print(f"  emailed Pete: {subject}")
    except Exception as e:
        print(f"  EMAIL FAILED: {e}", file=sys.stderr)
    try:
        ok = _load("cc_publish", "cc_publish.py").publish(
            "sygma-ads-fortnightly", f"{last_end}", {"subject": subject, "html": html})
        print(f"  CC publish: {'ok' if ok else 'FAILED'}")
    except Exception as e:
        print(f"  CC publish FAILED: {e}", file=sys.stderr)
    print(f"REPORT_COMPLETE — spend £{a_now['spend']:.0f} ({d['spend']}), {len(waste)} waste, {len(qs_reg)} QS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        try:
            _load("gmail_api", "gmail-api.py").GmailAPI().send(
                PETE, f"Sygma Ads Report — FAILED {datetime.now(TZ):%Y-%m-%d}",
                f"<pre>{traceback.format_exc()[-1500:]}</pre>", html=True)
        except Exception:
            pass
        sys.exit(1)
