#!/usr/bin/env python3
"""
Pete's personal calendar briefing -- emails Pete (and Pete only) a digest of
his Google Calendar events for the following day.

Designed to fire alongside cd-team-briefing.py at 17:00 Atlantic/Canary daily.
The team briefing is Odoo + CD-team; this one is GCal + personal-only.

Usage:
  python3 pete-personal-briefing.py                # send for tomorrow
  python3 pete-personal-briefing.py --date 2026-04-30
  python3 pete-personal-briefing.py --dry-run      # render to stdout
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
TZ = ZoneInfo("Atlantic/Canary")
RECIPIENT = "pete.ashcroft@sygma-solutions.com"  # Pete only

# ── Brand colours (matches cd-team-briefing.py for visual continuity) ─────────

ORANGE = "#F5A623"
TEAL = "#2BBFBF"
NAVY = "#1B2340"
BG_ALT = "#F8FAFC"
BORDER = "#E2E8F0"
MUTED = "#64748B"
TEXT = "#1E293B"


def load_helper(filename: str, module_name: str):
    """Load a sibling -.py file (hyphenated names break import) via importlib."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Data fetch ────────────────────────────────────────────────────────────────


def fetch_events(start_date: dt.date, end_date: dt.date) -> list[dict]:
    """Pull Pete's Google Calendar events between [start_date, end_date) local."""
    cal_mod = load_helper("calendar-api.py", "calendar_api")
    cal = cal_mod.CalendarAPI()
    start_local = dt.datetime.combine(start_date, dt.time(0, 0, tzinfo=TZ))
    end_local = dt.datetime.combine(end_date, dt.time(0, 0, tzinfo=TZ))
    return cal.list_events(
        calendar_id="primary",
        time_min=start_local.isoformat(timespec="seconds"),
        time_max=end_local.isoformat(timespec="seconds"),
        max_results=300,
    )


def event_local_date(ev: dict) -> dt.date:
    s = ev.get("start", {})
    if "date" in s:
        return dt.date.fromisoformat(s["date"])
    return dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(TZ).date()


# ── Helpers ───────────────────────────────────────────────────────────────────


def event_time_range(ev: dict) -> tuple[str, bool]:
    """Returns (display_time, is_allday)."""
    s = ev.get("start", {})
    e = ev.get("end", {})
    if "date" in s:
        return ("All day", True)
    s_dt = dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
    e_dt = dt.datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
    return (f"{s_dt:%H:%M}–{e_dt:%H:%M}", False)


def event_duration_hours(ev: dict) -> float:
    s, e = ev.get("start", {}), ev.get("end", {})
    if "date" in s:
        return 0.0
    s_dt = dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00"))
    e_dt = dt.datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00"))
    return max(0.0, (e_dt - s_dt).total_seconds() / 3600.0)


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*p\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]*", "\n", s)
    return s.strip()


def format_attendees(attendees: list[dict] | None) -> str:
    if not attendees:
        return ""
    names = []
    for a in attendees:
        if a.get("self"):
            continue
        names.append(a.get("displayName") or a.get("email") or "")
    return ", ".join(n for n in names if n)


# ── Rendering ─────────────────────────────────────────────────────────────────


def render_event_card_html(ev: dict) -> str:
    time_str, is_allday = event_time_range(ev)
    title = ev.get("summary") or "(no title)"
    location = ev.get("location") or ""
    description = strip_html(ev.get("description") or "")
    attendees = format_attendees(ev.get("attendees"))

    badge = ""
    if is_allday:
        badge = (
            f'<span style="display:inline-block;background:{TEAL};color:white;'
            f'font-size:10px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;'
            f'padding:3px 9px;border-radius:10px;vertical-align:middle;">All day</span>'
        )

    location_block = ""
    if location:
        location_block = (
            f'<div style="margin-top:8px;font-size:13px;color:{TEXT};line-height:1.5;">'
            f'📍 <a href="https://www.google.com/maps/search/?api=1&query={quote_plus(location)}" '
            f'style="color:{NAVY};text-decoration:none;">{html.escape(location)}</a>'
            f'</div>'
        )

    attendees_block = ""
    if attendees:
        attendees_block = (
            f'<div style="margin-top:6px;font-size:13px;color:{MUTED};">'
            f'👥 {html.escape(attendees)}</div>'
        )

    description_block = ""
    if description:
        excerpt = description if len(description) <= 400 else description[:380].rsplit(" ", 1)[0] + "…"
        description_block = (
            f'<div style="margin-top:12px;padding:12px 14px;background:#FFF7E6;'
            f'border-left:3px solid {ORANGE};border-radius:4px;font-size:13px;'
            f'line-height:1.55;color:{TEXT};white-space:pre-wrap;">{html.escape(excerpt)}</div>'
        )

    event_link = ev.get("htmlLink", "")
    link_block = ""
    if event_link:
        link_block = (
            f'<div style="margin-top:12px;font-size:12px;">'
            f'<a href="{event_link}" style="color:{TEAL};text-decoration:none;">View in Google Calendar →</a>'
            f'</div>'
        )

    time_pill = (
        f'<span style="display:inline-block;background:{NAVY};color:white;'
        f'font-size:15px;font-weight:700;letter-spacing:0.3px;'
        f'padding:6px 14px;border-radius:6px;font-variant-numeric:tabular-nums;">'
        f'⏱ {html.escape(time_str)}</span>'
    )

    return (
        f'<tr><td style="padding:22px 24px 24px;border-top:3px solid {BORDER};background:white;">'
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
        f'{time_pill}'
        + (f'  {badge}' if badge else "")
        + '</div>'
        f'<div style="margin-top:12px;font-weight:700;color:{NAVY};font-size:17px;line-height:1.3;">{html.escape(title)}</div>'
        + location_block
        + attendees_block
        + description_block
        + link_block
        + '</td></tr>'
    )


def render_event_text(ev: dict) -> list[str]:
    time_str, _ = event_time_range(ev)
    out = ["", f"  {ev.get('summary') or '(no title)'}", f"  Time: {time_str}"]
    if ev.get("location"):
        out.append(f"  Where: {ev['location']}")
    attendees = format_attendees(ev.get("attendees"))
    if attendees:
        out.append(f"  With: {attendees}")
    description = strip_html(ev.get("description") or "")
    if description:
        excerpt = description if len(description) <= 400 else description[:380].rsplit(" ", 1)[0] + "…"
        out.append("  Notes:")
        for line in excerpt.split("\n"):
            out.append(f"    {line}")
    if ev.get("htmlLink"):
        out.append(f"  Link: {ev['htmlLink']}")
    return out


def _shell(eyebrow: str, title: str, summary: str, body_table: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{BG_ALT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{TEXT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG_ALT};padding:24px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:white;border-radius:12px;overflow:hidden;max-width:640px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
        <tr><td style="background:{NAVY};color:white;padding:22px 24px 24px;">
          <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:600;">{eyebrow}</div>
          <div style="font-size:26px;font-weight:700;color:white;margin-top:6px;line-height:1.2;">{title}</div>
          {f'<div style="font-size:13px;color:rgba(255,255,255,0.78);margin-top:8px;">{summary}</div>' if summary else ""}
        </td></tr>
        {body_table}
        <tr><td style="padding:20px 24px;background:{BG_ALT};border-top:1px solid {BORDER};font-size:11px;color:{MUTED};line-height:1.6;">
          Pulled live from your primary Google Calendar at {dt.datetime.now(TZ).strftime("%-d %B %Y %H:%M")}.
        </td></tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def render_day_html(events: list[dict], target_date: dt.date) -> str:
    date_long = target_date.strftime("%A %-d %B %Y")

    blocks: list[str] = []
    if not events:
        blocks.append(
            f'<tr><td style="padding:32px 24px;text-align:center;color:{MUTED};">'
            f'<p style="font-size:16px;margin:0;">No events scheduled for {date_long}.</p>'
            f'<p style="font-size:13px;margin-top:6px;">A quiet day on the calendar.</p>'
            f'</td></tr>'
        )
    else:
        for ev in events:
            blocks.append(render_event_card_html(ev))

    return _shell("Your Calendar · Tomorrow", date_long, "", "".join(blocks))


def render_day_text(events: list[dict], target_date: dt.date) -> str:
    date_long = target_date.strftime("%A %-d %B %Y")
    total_hours = round(sum(event_duration_hours(e) for e in events), 1)
    out = [
        f"YOUR CALENDAR -- {date_long.upper()}",
        f"{len(events)} events · {total_hours} hours scheduled",
        "",
    ]
    if not events:
        out.append(f"No events scheduled for {date_long}. A quiet day.")
    else:
        for ev in events:
            out.extend(render_event_text(ev))
    return "\n".join(out)


def render_week_html(events: list[dict], week_start: dt.date) -> str:
    week_end = week_start + dt.timedelta(days=6)
    title = f"Week of {week_start.strftime('%-d %B %Y')}"

    by_date: dict[dt.date, list[dict]] = {}
    for ev in events:
        by_date.setdefault(event_local_date(ev), []).append(ev)

    blocks: list[str] = []
    if not events:
        blocks.append(
            f'<tr><td style="padding:32px 24px;text-align:center;color:{MUTED};">'
            f'<p style="font-size:16px;margin:0;">No events scheduled {week_start:%-d %b}–{week_end:%-d %b %Y}.</p>'
            f'<p style="font-size:13px;margin-top:6px;">A quiet week on the calendar.</p>'
            f'</td></tr>'
        )
    else:
        for offset in range(7):
            day = week_start + dt.timedelta(days=offset)
            day_name = day.strftime("%A")
            day_date = day.strftime("%-d %B")
            day_events = by_date.get(day, [])
            if not day_events:
                blocks.append(
                    f'<tr><td style="padding:0;">'
                    f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                    f'<tr><td style="background:{ORANGE};padding:14px 24px;">'
                    f'<div style="font-size:20px;font-weight:700;color:white;letter-spacing:0.2px;">{day_name}</div>'
                    f'<div style="font-size:13px;color:rgba(255,255,255,0.85);margin-top:1px;">{day_date}</div>'
                    f'</td></tr>'
                    f'<tr><td style="padding:18px 24px 22px;color:{MUTED};font-style:italic;font-size:13px;background:{BG_ALT};">No events.</td></tr>'
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
            for ev in day_events:
                blocks.append(render_event_card_html(ev))

    return _shell("Your Calendar · Week Ahead", title, "", "".join(blocks))


def render_week_text(events: list[dict], week_start: dt.date) -> str:
    week_end = week_start + dt.timedelta(days=6)
    total_hours = round(sum(event_duration_hours(e) for e in events), 1)
    by_date: dict[dt.date, list[dict]] = {}
    for ev in events:
        by_date.setdefault(event_local_date(ev), []).append(ev)
    out = [
        f"YOUR CALENDAR -- WEEK AHEAD ({week_start:%-d %b}–{week_end:%-d %b %Y})",
        f"{len(events)} events · {total_hours} hours scheduled",
    ]
    for offset in range(7):
        day = week_start + dt.timedelta(days=offset)
        out.append("")
        out.append(f"━━ {day.strftime('%A %-d %B').upper()} ━━")
        day_events = by_date.get(day, [])
        if not day_events:
            out.append("  (no events)")
            continue
        for ev in day_events:
            ind_lines = render_event_text(ev)
            out.extend(["  " + l if l else l for l in ind_lines[1:]])
    return "\n".join(out)


# ── Send ──────────────────────────────────────────────────────────────────────


def send_email(to: str, subject: str, html_body: str):
    gmail_mod = load_helper("gmail-api.py", "gmail_api")
    g = gmail_mod.GmailAPI()
    return g.send(to, subject, html_body, html=True)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="Email Pete a personal calendar briefing.")
    p.add_argument("--window", choices=["day", "week"], default="day",
                   help="day = tomorrow's events (default); week = upcoming Mon-Sun.")
    p.add_argument("--date", help="Target date YYYY-MM-DD. For --window day, the day to brief; for --window week, snapped to that week's Monday. Default: tomorrow / next Monday.")
    p.add_argument("--dry-run", action="store_true", help="Render to stdout, do not send.")
    p.add_argument("--to-override", help="Override recipient (test mode).")
    args = p.parse_args()

    today = dt.datetime.now(TZ).date()

    if args.window == "day":
        target = dt.date.fromisoformat(args.date) if args.date else today + dt.timedelta(days=1)
        events = fetch_events(target, target + dt.timedelta(days=1))
        html_body = render_day_html(events, target)
        text_body = render_day_text(events, target)
        subject = f"Your calendar — {target.strftime('%a %-d %b %Y')}"
        window_desc = f"day: {target}"
    else:
        if args.date:
            given = dt.date.fromisoformat(args.date)
            week_start = given - dt.timedelta(days=given.weekday())
        else:
            days_until_mon = (7 - today.weekday()) % 7 or 7
            week_start = today + dt.timedelta(days=days_until_mon)
        events = fetch_events(week_start, week_start + dt.timedelta(days=7))
        html_body = render_week_html(events, week_start)
        text_body = render_week_text(events, week_start)
        subject = f"Your week ahead — w/c {week_start.strftime('%a %-d %b %Y')}"
        window_desc = f"week: {week_start} to {week_start + dt.timedelta(days=6)}"

    recipient = args.to_override or RECIPIENT

    print(f"Window: {window_desc} ({len(events)} events)")
    print(f"Recipient: {recipient}")
    print(f"Subject: {subject}")

    if args.dry_run:
        print("\n=== TEXT ===")
        print(text_body)
        print(f"\n[dry-run -- HTML body is {len(html_body)} chars, not shown]")
        return

    result = send_email(recipient, subject, html_body)
    print(f"\nSent: id={result.get('id')} threadId={result.get('threadId')}")


if __name__ == "__main__":
    main()
