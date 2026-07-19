#!/usr/bin/env python3
"""
CD Team Briefing -- emails the Canary Detect field team a structured digest of
tomorrow's calendar events, pulled live from CD's Odoo instance.

Designed to run as a scheduled task at 17:00 Atlantic/Canary daily (cron 0 16
* * * UTC during summer; 0 17 * * * UTC during winter).

Pulls every calendar.event scheduled for the following day across all CD users,
enriches each with linked crm.lead description + res.partner contact details,
renders a clean HTML+plain-text email, sends via the Gmail API helper.

Usage:
  python3 cd-team-briefing.py                 # send for tomorrow
  python3 cd-team-briefing.py --date 2026-04-30   # specific date (Atlantic/Canary local)
  python3 cd-team-briefing.py --dry-run       # render but don't send
  python3 cd-team-briefing.py --to-override pete@... # send to one address only

Dependencies: Library/processes/scripts/gmail-api.py (sibling), and Odoo
config at Library/processes/odoo-api-configuration.md.

Recipients are hard-coded for the daily digest; edit RECIPIENTS below to change.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ── Recipients (all 6 in To field) ────────────────────────────────────────────

RECIPIENTS = [
    "pete.ashcroft@sygma-solutions.com",
    "dave.poxon@canary-detect.com",
    "tom.robertson@canary-detect.com",
    "marcos.knight@canary-detect.com",
    "nicola.brown@canary-detect.com",
    "jane.williams@sygma-solutions.com",
]

# Send-gate: live to the full team by DEFAULT (recipients verified 2026-07-07). Preview is opt-in —
# set BRIEFING_PREVIEW=1 (or pass --to-override) to route the briefing to Pete ONLY for a test run.
# Live-by-default means a service rebuilt from scratch can never silently revert to Pete-only.
if os.environ.get("BRIEFING_PREVIEW") == "1":
    RECIPIENTS = ["pete.ashcroft@sygma-solutions.com"]

# ── Odoo config (loaded from the canonical config file) ───────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR.parent / "odoo-api-configuration.md"
TZ = ZoneInfo("Atlantic/Canary")




def _secret_cfg():
    """Odoo config from the materialised secret (the house standard: keys live in public.secrets,
    never in a note or a markdown file). Returns None if the secret is not present, so callers keep
    their existing fallbacks until every runtime is proven on this path (19 Jul 2026)."""
    import json as _json, os as _os
    p = _os.path.join(_os.environ.get("VAULT", "/tmp/pbs"),
                      "Library", "processes", "secrets", "odoo-credentials.json")
    try:
        with open(p) as fh:
            c = _json.load(fh)
        if all(c.get(k) for k in ("url", "db", "login", "api_key")):
            return {"url": c["url"].rstrip("/"), "db": c["db"],
                    "login": c["login"], "api_key": c["api_key"]}
    except Exception:
        pass
    return None


def load_odoo_config() -> dict:
    # env-first (Railway sets ODOO_*), then the CC secrets vault, then the legacy config file.
    env = {"url": os.environ.get("ODOO_URL"), "db": os.environ.get("ODOO_DB"),
           "login": os.environ.get("ODOO_LOGIN"), "api_key": os.environ.get("ODOO_API_KEY")}
    if all(env.values()):
        return env
    sec = _secret_cfg()
    if sec:
        return sec
    text = CONFIG_FILE.read_text()

    def grab(label: str) -> str | None:
        m = re.search(rf"\*\*{re.escape(label)}\*\*\s*\|\s*`([^`]+)`", text)
        return m.group(1) if m else None

    cfg = {
        "url": grab("Instance URL"),
        "db": grab("Database name"),
        "login": grab("Login (API user)"),
        "api_key": grab("API key"),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"odoo config missing fields: {missing}")
    return cfg


# ── Tiny inline Odoo JSON-RPC client (deliberately self-contained) ────────────


class Odoo:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._uid: int | None = None

    def _rpc(self, service: str, method: str, args: list) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        req = urllib.request.Request(
            f"{self.cfg['url']}/jsonrpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"odoo http {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}") from e
        if "error" in body:
            err = body["error"]
            msg = err.get("data", {}).get("message") or err.get("message", "unknown")
            raise RuntimeError(f"odoo error: {msg}")
        return body.get("result")

    def _auth(self) -> int:
        if self._uid:
            return self._uid
        uid = self._rpc("common", "authenticate", [self.cfg["db"], self.cfg["login"], self.cfg["api_key"], {}])
        if not uid:
            raise RuntimeError("odoo auth failed -- check login + api key")
        self._uid = uid
        return uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        uid = self._auth()
        return self._rpc(
            "object",
            "execute_kw",
            [self.cfg["db"], uid, self.cfg["api_key"], model, method, args, kwargs or {}],
        )

    def search_read(self, model: str, domain: list, fields: list, **kwargs) -> list[dict]:
        return self.execute(model, "search_read", [domain], {"fields": fields, **kwargs})

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        return self.execute(model, "read", [ids, fields])


# ── Email body composition ────────────────────────────────────────────────────

# Brand colours from the report builder
ORANGE = "#F5A623"
TEAL = "#2BBFBF"
NAVY = "#1B2340"
BG_ALT = "#F8FAFC"
BORDER = "#E2E8F0"
MUTED = "#64748B"
TEXT = "#1E293B"

STAGE_COLOURS = {
    "Survey Booked": "#F5A623",
    "Repair Booked": "#F5A623",
    "Survey Done": "#2BBFBF",
    "Report Sent": "#22c55e",
    "Quote Sent": "#3b82f6",
    "Won": "#22c55e",
    "Lost": "#ef4444",
}


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    # Convert common HTML entities and strip tags. Keep paragraph breaks.
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*p\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]*", "\n", s)
    return s.strip()


def fmt_time_range(start_utc: str, stop_utc: str, allday: bool) -> str:
    if allday:
        return "All day"
    s = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(TZ)
    e = dt.datetime.strptime(stop_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(TZ)
    return f"{s:%H:%M}–{e:%H:%M}"


def hours_between(start_utc: str, stop_utc: str) -> float:
    s = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S")
    e = dt.datetime.strptime(stop_utc, "%Y-%m-%d %H:%M:%S")
    return max(0.0, (e - s).total_seconds() / 3600.0)


def fetch_briefing(start_date: dt.date, end_date: dt.date, odoo: Odoo) -> list[dict]:
    """Pull all calendar events between [start_date, end_date) (Atlantic/Canary
    local, end exclusive) and return enriched job dicts.

    Used for both day-window (start..start+1) and week-window (Mon..next Mon)
    briefings.
    """
    start_local = dt.datetime.combine(start_date, dt.time(0, 0, tzinfo=TZ))
    end_local = dt.datetime.combine(end_date, dt.time(0, 0, tzinfo=TZ))
    start_utc = start_local.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_local.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    events = odoo.search_read(
        "calendar.event",
        [["start", ">=", start_utc], ["start", "<", end_utc]],
        [
            "id", "name", "start", "stop", "allday", "location",
            "user_id", "partner_ids", "opportunity_id",
            "x_studio_related_field_873_1ji5k5ftb",  # Studio "Contact" -> res.partner
        ],
        order="start asc",
    )

    # Batch-fetch leads + customer partners
    lead_ids = sorted({e["opportunity_id"][0] for e in events if e.get("opportunity_id")})
    leads_by_id: dict[int, dict] = {}
    if lead_ids:
        leads = odoo.read(
            "crm.lead", lead_ids,
            ["id", "name", "partner_id", "email_from", "phone", "stage_id", "description"],
        )
        leads_by_id = {l["id"]: l for l in leads}

    partner_ids: set[int] = set()
    for e in events:
        c = e.get("x_studio_related_field_873_1ji5k5ftb")
        if c:
            partner_ids.add(c[0])
    for l in leads_by_id.values():
        if l.get("partner_id"):
            partner_ids.add(l["partner_id"][0])

    partners_by_id: dict[int, dict] = {}
    if partner_ids:
        partners = odoo.read(
            "res.partner", sorted(partner_ids),
            ["id", "name", "email", "phone"],
        )
        partners_by_id = {p["id"]: p for p in partners}

    jobs: list[dict] = []
    for e in events:
        partner = None
        cf = e.get("x_studio_related_field_873_1ji5k5ftb")
        if cf:
            partner = partners_by_id.get(cf[0])
        elif e.get("opportunity_id"):
            l_tmp = leads_by_id.get(e["opportunity_id"][0])
            if l_tmp and l_tmp.get("partner_id"):
                partner = partners_by_id.get(l_tmp["partner_id"][0])

        lead = leads_by_id.get(e["opportunity_id"][0]) if e.get("opportunity_id") else None
        engineer = e["user_id"][1] if e.get("user_id") else "Unassigned"

        # Local-tz date for week-view grouping
        local_dt = (
            dt.datetime.strptime(e["start"], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=dt.timezone.utc)
            .astimezone(TZ)
        )

        jobs.append({
            "id": e["id"],
            "title": e["name"],
            "time": fmt_time_range(e["start"], e["stop"], bool(e.get("allday"))),
            "hours": 0.0 if e.get("allday") else hours_between(e["start"], e["stop"]),
            "date": local_dt.date(),
            "location": e.get("location") or "",
            "engineer": engineer,
            "customer_name": (partner or {}).get("name") or (lead or {}).get("partner_id", [None, ""])[1] or "",
            "customer_email": (partner or {}).get("email") or (lead or {}).get("email_from") or "",
            "customer_phone": (partner or {}).get("phone") or (lead or {}).get("phone") or "",
            "brief": strip_html((lead or {}).get("description")),
            "stage": (lead or {}).get("stage_id", [None, ""])[1] if lead else "",
            "lead_id": (lead or {}).get("id"),
            "partner_id": (partner or {}).get("id"),
        })

    return jobs


def group_by_engineer(jobs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for j in jobs:
        out.setdefault(j["engineer"], []).append(j)
    return out


def group_by_date_then_engineer(jobs: list[dict]) -> dict[dt.date, dict[str, list[dict]]]:
    out: dict[dt.date, dict[str, list[dict]]] = {}
    for j in jobs:
        out.setdefault(j["date"], {}).setdefault(j["engineer"], []).append(j)
    return out


# ── HTML rendering ───────────────────────────────────────────────────────────


def odoo_event_url(odoo_url: str, event_id: int) -> str:
    return f"{odoo_url}/odoo/calendar/{event_id}"


def odoo_lead_url(odoo_url: str, lead_id: int) -> str:
    return f"{odoo_url}/odoo/crm/{lead_id}"


def maps_url(location: str) -> str:
    from urllib.parse import quote_plus
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(location)}"


def render_job_card_html(j: dict, odoo_url: str) -> str:
    """Render one event as an HTML <tr><td> card. Reused by day + week views."""
    stage_colour = STAGE_COLOURS.get(j["stage"], MUTED)
    stage_html = (
        f'<span style="display:inline-block;background:{stage_colour};color:white;'
        f'font-size:10px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;'
        f'padding:3px 9px;border-radius:10px;vertical-align:middle;">{html.escape(j["stage"])}</span>'
    ) if j["stage"] else ""

    phone_html = ""
    if j["customer_phone"]:
        first_phone = re.split(r"[/,]", j["customer_phone"])[0].strip()
        digits = re.sub(r"[^+\d]", "", first_phone)
        phone_html = (
            f'<a href="tel:{digits}" style="color:{NAVY};text-decoration:none;">'
            f'{html.escape(j["customer_phone"])}</a>'
        )

    email_html = ""
    if j["customer_email"]:
        email_html = (
            f' · <a href="mailto:{html.escape(j["customer_email"])}" '
            f'style="color:{NAVY};text-decoration:none;">{html.escape(j["customer_email"])}</a>'
        )

    location_block = ""
    if j["location"]:
        location_block = (
            f'<div style="margin-top:8px;font-size:13px;color:{TEXT};line-height:1.5;">'
            f'📍 <a href="{maps_url(j["location"])}" '
            f'style="color:{NAVY};text-decoration:none;">{html.escape(j["location"])}</a>'
            f'</div>'
        )

    brief_block = ""
    if j["brief"]:
        brief_excerpt = j["brief"]
        if len(brief_excerpt) > 400:
            brief_excerpt = brief_excerpt[:380].rsplit(" ", 1)[0] + "…"
        brief_block = (
            f'<div style="margin-top:12px;padding:12px 14px;background:#FFF7E6;'
            f'border-left:3px solid {ORANGE};border-radius:4px;font-size:13px;'
            f'line-height:1.55;color:{TEXT};white-space:pre-wrap;">{html.escape(brief_excerpt)}</div>'
        )

    links = []
    if j["lead_id"]:
        links.append(
            f'<a href="{odoo_lead_url(odoo_url, j["lead_id"])}" '
            f'style="color:{TEAL};text-decoration:none;">View lead in Odoo →</a>'
        )
    links.append(
        f'<a href="{odoo_event_url(odoo_url, j["id"])}" '
        f'style="color:{TEAL};text-decoration:none;">View event in Odoo →</a>'
    )

    # Big prominent time badge at the top of the card; title underneath; details below.
    time_pill = (
        f'<span style="display:inline-block;background:{NAVY};color:white;'
        f'font-size:15px;font-weight:700;letter-spacing:0.3px;'
        f'padding:6px 14px;border-radius:6px;font-variant-numeric:tabular-nums;">'
        f'⏱ {html.escape(j["time"])}</span>'
    )

    return (
        f'<tr><td style="padding:22px 24px 24px;border-top:3px solid {BORDER};background:white;">'
        # row 1: time pill + (optional) stage badge
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
        f'{time_pill}'
        + (f'  {stage_html}' if stage_html else "")
        + '</div>'
        # row 2: title (big)
        f'<div style="margin-top:12px;font-weight:700;color:{NAVY};font-size:17px;line-height:1.3;">{html.escape(j["title"])}</div>'
        + (
            f'<div style="margin-top:8px;font-size:14px;color:{TEXT};line-height:1.5;">'
            f'<strong>{html.escape(j["customer_name"])}</strong>'
            f'{(" · " + phone_html) if phone_html else ""}'
            f'{email_html}'
            f'</div>' if j["customer_name"] else ""
        )
        + location_block
        + brief_block
        + (
            f'<div style="margin-top:12px;font-size:12px;">{" · ".join(links)}</div>'
            if links else ""
        )
        + '</td></tr>'
    )


def render_job_text(j: dict, odoo_url: str) -> list[str]:
    """Render one event as plain-text lines (indented). Reused by day + week."""
    out = ["", f"  {j['title']}", f"  Time: {j['time']}"]
    if j["stage"]:
        out.append(f"  Stage: {j['stage']}")
    if j["customer_name"]:
        out.append(f"  Customer: {j['customer_name']}")
    if j["customer_phone"]:
        out.append(f"  Phone:    {j['customer_phone']}")
    if j["customer_email"]:
        out.append(f"  Email:    {j['customer_email']}")
    if j["location"]:
        out.append(f"  Address:  {j['location']}")
    if j["brief"]:
        brief = j["brief"]
        if len(brief) > 400:
            brief = brief[:380].rsplit(" ", 1)[0] + "…"
        out.append("  Brief:")
        for line in brief.split("\n"):
            out.append(f"    {line}")
    if j["lead_id"]:
        out.append(f"  Lead:  {odoo_lead_url(odoo_url, j['lead_id'])}")
    out.append(f"  Event: {odoo_event_url(odoo_url, j['id'])}")
    return out


def _shell(header_eyebrow: str, header_title: str, summary: str, body_table: str, odoo_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{header_title}</title>
</head>
<body style="margin:0;padding:0;background:{BG_ALT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{TEXT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG_ALT};padding:24px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:white;border-radius:12px;overflow:hidden;max-width:640px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
        <tr><td style="background:{NAVY};color:white;padding:22px 24px 24px;">
          <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:600;">{header_eyebrow}</div>
          <div style="font-size:26px;font-weight:700;color:white;margin-top:6px;line-height:1.2;">{header_title}</div>
          {f'<div style="font-size:13px;color:rgba(255,255,255,0.78);margin-top:8px;">{summary}</div>' if summary else ""}
        </td></tr>
        {body_table}
        <tr><td style="padding:20px 24px;background:{BG_ALT};border-top:1px solid {BORDER};font-size:11px;color:{MUTED};line-height:1.6;">
          Pulled live from Odoo at {dt.datetime.now(TZ).strftime("%-d %B %Y %H:%M")} · Source: <a href="{odoo_url}/odoo/calendar" style="color:{TEAL};text-decoration:none;">camello-blanco-sl.odoo.com/calendar</a>
        </td></tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


# ── Day view ─────────────────────────────────────────────────────────────────


def render_day_html(jobs: list[dict], target_date: dt.date, odoo_url: str) -> str:
    by_eng = group_by_engineer(jobs)
    date_long = target_date.strftime("%A %-d %B %Y")

    blocks: list[str] = []
    if not jobs:
        blocks.append(
            f'<tr><td style="padding:32px 24px;text-align:center;color:{MUTED};">'
            f'<p style="font-size:16px;margin:0;">No jobs scheduled for {date_long}.</p>'
            f'<p style="font-size:13px;margin-top:6px;">Quiet day. Enjoy.</p>'
            f'</td></tr>'
        )
    else:
        for engineer, eng_jobs in sorted(by_eng.items()):
            blocks.append(
                f'<tr><td style="padding:20px 24px 0;background:white;">'
                f'<div style="font-size:13px;color:{MUTED};font-weight:600;letter-spacing:0.4px;">'
                f'<span style="text-transform:uppercase;font-size:11px;letter-spacing:1.2px;">Engineer</span> · '
                f'<span style="color:{NAVY};font-size:15px;">{html.escape(engineer)}</span></div>'
                f'</td></tr>'
            )
            for j in eng_jobs:
                blocks.append(render_job_card_html(j, odoo_url))

    return _shell("Canary Detect · Jobs Tomorrow", date_long, "", "".join(blocks), odoo_url)


def render_day_text(jobs: list[dict], target_date: dt.date, odoo_url: str) -> str:
    by_eng = group_by_engineer(jobs)
    date_long = target_date.strftime("%A %-d %B %Y")
    total_hours = round(sum(j["hours"] for j in jobs), 1)
    out = [
        f"CANARY DETECT — JOBS {date_long.upper()}",
        f"{len(jobs)} jobs across {len(by_eng)} engineers · {total_hours} hours scheduled",
        "",
    ]
    if not jobs:
        out.append(f"No jobs scheduled for {date_long}. Quiet day.")
    else:
        for engineer, eng_jobs in sorted(by_eng.items()):
            out.append(f"── {engineer} ({len(eng_jobs)} jobs) ──")
            for j in eng_jobs:
                out.extend(render_job_text(j, odoo_url))
            out.append("")
    out.extend(["", f"Pulled from Odoo at {dt.datetime.now(TZ).strftime('%-d %B %Y %H:%M')}.", f"Source: {odoo_url}/odoo/calendar"])
    return "\n".join(out)


# ── Week view ────────────────────────────────────────────────────────────────


def render_week_html(jobs: list[dict], week_start: dt.date, odoo_url: str) -> str:
    by_date = group_by_date_then_engineer(jobs)
    week_end = week_start + dt.timedelta(days=6)
    title = f"Week of {week_start.strftime('%-d %B %Y')}"

    blocks: list[str] = []
    if not jobs:
        blocks.append(
            f'<tr><td style="padding:32px 24px;text-align:center;color:{MUTED};">'
            f'<p style="font-size:16px;margin:0;">No jobs scheduled {week_start:%-d %b}–{week_end:%-d %b %Y}.</p>'
            f'<p style="font-size:13px;margin-top:6px;">A quiet week.</p>'
            f'</td></tr>'
        )
    else:
        for offset in range(7):
            day = week_start + dt.timedelta(days=offset)
            day_jobs_by_engineer = by_date.get(day, {})
            day_name = day.strftime("%A")
            day_date = day.strftime("%-d %B")
            if not day_jobs_by_engineer:
                blocks.append(
                    f'<tr><td style="padding:0;">'
                    f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                    f'<tr><td style="background:{ORANGE};padding:14px 24px;">'
                    f'<div style="font-size:20px;font-weight:700;color:white;letter-spacing:0.2px;">{day_name}</div>'
                    f'<div style="font-size:13px;color:rgba(255,255,255,0.85);margin-top:1px;">{day_date}</div>'
                    f'</td></tr>'
                    f'<tr><td style="padding:18px 24px 22px;color:{MUTED};font-style:italic;font-size:13px;background:{BG_ALT};">No jobs scheduled.</td></tr>'
                    f'</table>'
                    f'</td></tr>'
                )
                continue
            blocks.append(
                f'<tr><td style="background:{ORANGE};padding:16px 24px;">'
                f'<div style="font-size:22px;font-weight:700;color:white;letter-spacing:0.2px;">{day_name}</div>'
                f'<div style="font-size:14px;color:rgba(255,255,255,0.92);margin-top:2px;">{day_date}</div>'
                f'</td></tr>'
            )
            for engineer, eng_jobs in sorted(day_jobs_by_engineer.items()):
                blocks.append(
                    f'<tr><td style="padding:14px 24px 0;background:{BG_ALT};">'
                    f'<div style="font-size:13px;color:{MUTED};font-weight:600;letter-spacing:0.4px;">'
                    f'<span style="text-transform:uppercase;font-size:11px;letter-spacing:1.2px;">Engineer</span> · '
                    f'<span style="color:{NAVY};font-size:15px;">{html.escape(engineer)}</span></div>'
                    f'</td></tr>'
                )
                for j in eng_jobs:
                    blocks.append(render_job_card_html(j, odoo_url))

    return _shell("Canary Detect · Week Ahead", title, "", "".join(blocks), odoo_url)


def render_week_text(jobs: list[dict], week_start: dt.date, odoo_url: str) -> str:
    by_date = group_by_date_then_engineer(jobs)
    week_end = week_start + dt.timedelta(days=6)
    total_hours = round(sum(j["hours"] for j in jobs), 1)
    engineer_set = {j["engineer"] for j in jobs}
    out = [
        f"CANARY DETECT — WEEK AHEAD ({week_start:%-d %b}–{week_end:%-d %b %Y})",
        f"{len(jobs)} jobs across {len(engineer_set)} engineers · {total_hours} hours scheduled",
    ]
    for offset in range(7):
        day = week_start + dt.timedelta(days=offset)
        out.append("")
        out.append(f"━━ {day.strftime('%A %-d %B').upper()} ━━")
        day_jobs_by_engineer = by_date.get(day, {})
        if not day_jobs_by_engineer:
            out.append("  (no jobs scheduled)")
            continue
        for engineer, eng_jobs in sorted(day_jobs_by_engineer.items()):
            out.append(f"  {engineer}:")
            for j in eng_jobs:
                # tighter spacing for week view
                ind_lines = render_job_text(j, odoo_url)
                # render_job_text starts with "" -> drop, and re-indent by 2 more spaces
                out.extend(["  " + l if l else l for l in ind_lines[1:]])
    out.extend(["", f"Pulled from Odoo at {dt.datetime.now(TZ).strftime('%-d %B %Y %H:%M')}.", f"Source: {odoo_url}/odoo/calendar"])
    return "\n".join(out)


# ── Gmail send via the existing helper ────────────────────────────────────────


def load_gmail_helper():
    """Load gmail-api.py via importlib (the file uses a hyphen so direct import fails)."""
    spec = importlib.util.spec_from_file_location(
        "gmail_api",
        SCRIPT_DIR / "gmail-api.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gmail_api"] = mod
    spec.loader.exec_module(mod)
    return mod.GmailAPI()


def send_email(to_list: list[str], subject: str, html_body: str, text_body: str):
    g = load_gmail_helper()
    # GmailAPI.send accepts a single string for to; use comma-join for multi-recipient
    to_str = ", ".join(to_list)
    # The helper's send() takes html=None for auto-detect. Pass html=True so the
    # html_body is sent as HTML; text_body becomes the plain-text alternative
    # via the helper's MIMEMultipart logic. Looking at gmail-api.py: send takes
    # a single body; HTML goes to body when html=True. To do multipart/alternative
    # with both, we'd need to extend the helper. For first cut: send HTML only;
    # most modern mail clients render HTML cleanly. The plain-text version is
    # available via --dry-run for human review.
    return g.send(to_str, subject, html_body, html=True)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="Email CD team a briefing of jobs from Odoo.")
    p.add_argument("--window", choices=["day", "week"], default="day",
                   help="day = tomorrow's jobs (default); week = upcoming Mon-Sun.")
    p.add_argument("--date", help="Target date (YYYY-MM-DD, Atlantic/Canary local). For --window day, the day to brief; for --window week, the Monday of the week. Default: tomorrow's date for day, next Monday for week.")
    p.add_argument("--dry-run", action="store_true", help="Render HTML+text to stdout, do not send.")
    p.add_argument("--no-send", action="store_true", help="Publish the CC snapshot only; do NOT email (backfill).")
    p.add_argument("--to-override", help="Comma-separated override recipients (test mode).")
    args = p.parse_args()

    today = dt.datetime.now(TZ).date()

    cfg = load_odoo_config()
    odoo = Odoo(cfg)

    if args.window == "day":
        target_date = dt.date.fromisoformat(args.date) if args.date else today + dt.timedelta(days=1)
        jobs = fetch_briefing(target_date, target_date + dt.timedelta(days=1), odoo)
        html_body = render_day_html(jobs, target_date, cfg["url"])
        text_body = render_day_text(jobs, target_date, cfg["url"])
        subject = f"CD jobs tomorrow — {target_date.strftime('%a %-d %b %Y')}"
        window_desc = f"day: {target_date}"
        cc_key, cc_period = "cd-briefing-daily", target_date.isoformat()
    else:
        # Week starts Monday. If user passed a date, snap to Monday of that week.
        if args.date:
            given = dt.date.fromisoformat(args.date)
            week_start = given - dt.timedelta(days=given.weekday())
        else:
            # Next Monday from today (if today IS Monday, that's today)
            days_until_mon = (7 - today.weekday()) % 7 or 7
            week_start = today + dt.timedelta(days=days_until_mon)
        week_end = week_start + dt.timedelta(days=7)
        jobs = fetch_briefing(week_start, week_end, odoo)
        html_body = render_week_html(jobs, week_start, cfg["url"])
        text_body = render_week_text(jobs, week_start, cfg["url"])
        subject = f"CD week ahead — w/c {week_start.strftime('%a %-d %b %Y')}"
        window_desc = f"week: {week_start} to {week_start + dt.timedelta(days=6)}"
        cc_key, cc_period = "cd-briefing-week", week_start.isoformat()

    recipients = (
        [r.strip() for r in args.to_override.split(",") if r.strip()]
        if args.to_override else RECIPIENTS
    )

    total_hours = round(sum(j["hours"] for j in jobs), 1)
    engineers = {j["engineer"] for j in jobs}
    print(f"Window: {window_desc} ({len(jobs)} jobs, {len(engineers)} engineers, {total_hours}h)")
    print(f"Recipients: {', '.join(recipients)}")
    print(f"Subject: {subject}")

    if args.dry_run:
        print("\n=== TEXT ===")
        print(text_body)
        print(f"\n[dry-run -- HTML body is {len(html_body)} chars, not shown]")
        return

    # Command Centre publish -- snapshot to reports.snapshots (cd-briefings page,
    # Daily / Week-ahead tabs). Email unchanged. Runs for live send AND --no-send. Non-fatal.
    def _publish_cc():
        try:
            import importlib.util as _il
            _spec = _il.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
            _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
            ok = _cc.publish(cc_key, cc_period, {"subject": subject, "html": html_body})
            print(f"  CC: snapshot {'published' if ok else 'FAILED'} ({cc_key} {cc_period})")
        except Exception as _e:
            print(f"  CC PUBLISH FAILED: {_e}")

    if args.no_send:
        _publish_cc()
        print(f"[no-send] CC snapshot published ({cc_key} {cc_period}); email skipped.")
        return

    result = send_email(recipients, subject, html_body, text_body)
    print(f"\nSent: id={result.get('id')} threadId={result.get('threadId')}")
    _publish_cc()


if __name__ == "__main__":
    main()
