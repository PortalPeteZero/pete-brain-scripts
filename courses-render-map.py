#!/usr/bin/env python3
"""
courses-render-map.py -- Render Sygma course master list YAML into:

  1. Hub/Courses/Course Mapping.xlsx  (presentation-grade xlsx for the team)
  2. Hub/Courses/README.md            (markdown table inside the README)
  3. Vault: Businesses/sygma-solutions/training/courses/master-list.md (rendered table)

Reads canonical YAML at:
  Businesses/sygma-solutions/training/courses/_course-map.yaml

Validates: code uniqueness, primary/used cross-refs, alias structure.

Usage:
  python3 courses-render-map.py [--local-only]
    --local-only  -- write outputs to /tmp/ instead of uploading to Hub Drive
"""

import os
import sys
import yaml
import importlib.util
from pathlib import Path
from datetime import datetime

# ============================================================
# Constants & paths
# ============================================================

# Vault auto-detect: prefer the real Mac mount, fall back to any current
# Cowork sandbox path. The script is run from Desktop Commander on Pete's Mac
# so the real path is what's used in production.
import os as _os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
_CANDIDATE_VAULTS = [
    VAULT,
    *[f"/sessions/{d}/mnt/Command Centre" for d in (_os.listdir("/sessions") if _os.path.isdir("/sessions") else [])],
]
VAULT = next((p for p in _CANDIDATE_VAULTS if _os.path.isdir(p)), _CANDIDATE_VAULTS[0])
COURSES_DIR = f"{VAULT}/Businesses/sygma-solutions/training/courses"
SCRIPTS_DIR = f"{VAULT}/Library/processes/scripts"
YAML_PATH = f"{COURSES_DIR}/_course-map.yaml"
LOCAL_OUT_DIR = "/tmp" if _os.path.isdir("/tmp") else VAULT
VAULT_MASTER_LIST_PATH = f"{COURSES_DIR}/master-list.md"

HUB_COURSES_FOLDER_ID = "1lVk2TtIRyGjJV5cNZMeTcj3gxAOCvani"
HUB_COURSES_BRAIN_FOLDER_ID = "1cImwRzaVNz_q0C7L7MShNDHB7C-5_FGN"  # Hub/Courses/_brain/ — shared brain for both Claudes

# Brain-publish manifest: vault md files to publish to Hub/Courses/_brain/ on every render.
# Each entry: (vault_relative_path, hub_filename). The README is generated from a
# template kept alongside this script — see _publish_brain() for the template.
BRAIN_DOCS = [
    ("Businesses/sygma-solutions/training/courses/code-system.md",          "code-system.md"),
    ("Businesses/sygma-solutions/training/courses/cross-system-usage.md",   "cross-system-usage.md"),
    ("Businesses/sygma-solutions/training/courses/audit-protocol.md",       "audit-protocol.md"),
    ("Businesses/sygma-solutions/training/courses/sop-course-lifecycle.md", "sop-course-lifecycle.md"),
    ("Businesses/sygma-solutions/training/courses/master-list.md",          "master-list.md"),
]

# Sygma branding
SYGMA_NAVY = "1E5A8E"
SYGMA_NAVY_DARK = "0F3E68"
SYGMA_ORANGE = "F47A1F"
LIGHT_BG = "F5F7FA"
WHITE = "FFFFFF"
TEXT = "1B2A3A"
ROW_STRIPE = "F8FAFC"

CERT_COLOURS = {
    "In House":              "6C757D",
    "EUSR Endorsed":         "5BC0DE",
    "EUSR Accredited":       "1E5A8E",
    "ProQual Accredited":    "F47A1F",
    "ProQual L2 Award":      "5CB85C",
    "ProQual L3 Certificate": "28A745",
    "ProQual L4 Diploma":    "1E7E34",
    "ProQual L5 Diploma":    "155724",
    "ProQual L6 Diploma":    "0B3A14",
}

STATUS_COLOURS = {
    "approved":     "28A745",
    "under review": "FFC107",
    "missing":      "DC3545",
    "not approved": "6C757D",
}

# ============================================================
# Load YAML
# ============================================================

def load_yaml():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)


def validate(data):
    """Lightweight validation -- duplicates + cross-refs."""
    courses = data.get("courses", [])
    topics = data.get("topics", [])
    code_set = set()
    for c in courses:
        code = c["code"]
        if code in code_set:
            raise ValueError(f"Duplicate course code: {code}")
        code_set.add(code)
    topic_set = set()
    for t in topics:
        code = t["code"]
        if code in topic_set:
            raise ValueError(f"Duplicate topic code: {code}")
        topic_set.add(code)
    for c in courses:
        for tref in c.get("topics", []) or []:
            if tref not in topic_set:
                raise ValueError(f"Course {c['code']} references unknown topic {tref}")
        if c.get("derived_from") and c["derived_from"] not in code_set:
            raise ValueError(f"Course {c['code']} derived_from unknown code {c['derived_from']}")
    return True


def short_brief(course):
    """Return a brief description for the front tab."""
    notes = (course.get("notes") or "").strip()
    if notes:
        first_sentence = notes.split(".")[0].strip()
        if len(first_sentence) > 80:
            return first_sentence[:77] + "..."
        return first_sentence
    # Fallback: build from cert + duration + delivery
    cert = course.get("cert_type", "")
    dur = course.get("duration", "")
    return f"{cert} delivery, {dur}".strip(" ,")


def cc_current_names(course):
    """Return all CC alias names mapped to this course."""
    return [a.get("name", "") for a in (course.get("aliases", []) or []) if a.get("source") == "competency_cloud"]


def cc_current_label(course):
    """Format CC Current cell -- single name or compact merge label.
    Long lists are truncated; the full list lives on the CC Mismatches tab."""
    names = cc_current_names(course)
    if not names:
        return "(not in CC)"
    if len(names) == 1:
        return names[0]
    # Multiple CC entries that should merge into one -- compact display
    return f"(merge: {len(names)} entries -- see CC Mismatches)"


def cc_ideal_label(course):
    """Format CC Ideal cell -- target CC name PREFIXED with the code.
    Logic:
      - If cc_ideal_name explicitly set in YAML, prefix with code.
      - Else if course already has a single CC alias, prefix that with code.
      - Else (no CC alias), prefix canonical name with code.
    Result is always "{code} {name}" so CC entries are self-identifying.
    """
    code = course.get("code", "").strip()
    ideal = course.get("cc_ideal_name")
    if ideal:
        body = ideal
    else:
        existing = cc_current_names(course)
        body = existing[0] if len(existing) == 1 else course.get("name", "").strip()
    return f"{code} {body}".strip()


def alignment_status(course):
    """Compute overall alignment status for the front tab.
    green = CC current matches CC ideal (already aligned)
    yellow = needs adding to CC, or rename required
    red = merge needed (multiple CC entries collapse to one)
    grey = retired
    """
    if course.get("status") == "retired":
        return "retired"
    current = cc_current_names(course)
    if not current:
        return "yellow"  # not in CC -- needs adding
    if len(current) > 1:
        return "red"  # merge required
    # Check explicit cc_ideal_name vs current
    explicit_ideal = course.get("cc_ideal_name")
    if explicit_ideal and current[0].strip() != explicit_ideal.strip():
        return "yellow"  # rename needed
    return "green"  # aligned


def topics_label(course):
    """Format topic refs."""
    tlist = course.get("topics", []) or []
    if not tlist:
        return "--"
    return " + ".join(tlist)


def days_label(course):
    d = course.get("duration")
    if not d or d == "TBD":
        return "TBD"
    s = str(d)
    # Map common forms
    if s.startswith("0.5"):
        return "0.5"
    for prefix in ("1 day", "2 day", "3 day", "5 day"):
        if s.startswith(prefix):
            return s.split()[0]
    if s.startswith("5 day fast-track"):
        return "5"
    return s


def mode_label(course):
    d = course.get("delivery", "")
    return {
        "in-person": "📍 In person",
        "online-teams": "💻 Online (Teams)",
        "online-assessment": "📝 Online assessment",
    }.get(d, d)


# ============================================================
# Render xlsx
# ============================================================

def render_xlsx(data, out_path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # ---------- helpers ----------
    def hdr_fill():
        return PatternFill("solid", fgColor=SYGMA_NAVY)

    def stripe_fill():
        return PatternFill("solid", fgColor=ROW_STRIPE)

    def thin_border():
        side = Side(style="thin", color="D0D7DE")
        return Border(left=side, right=side, top=side, bottom=side)

    def cert_pill_fill(cert_type):
        c = CERT_COLOURS.get(cert_type, "999999")
        return PatternFill("solid", fgColor=c)

    def cert_pill_text_colour(cert_type):
        # Use white text for darker cert backgrounds
        dark_certs = {
            "EUSR Accredited", "ProQual Accredited",
            "ProQual L3 Certificate", "ProQual L4 Diploma",
            "ProQual L5 Diploma", "ProQual L6 Diploma",
        }
        return WHITE if cert_type in dark_certs else "1B2A3A"

    def style_header_row(ws, row, ncols):
        for col in range(1, ncols + 1):
            cell = ws.cell(row=row, column=col)
            cell.fill = hdr_fill()
            cell.font = Font(name="Calibri", size=12, bold=True, color=WHITE)
            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
            cell.border = thin_border()
        ws.row_dimensions[row].height = 30

    def style_body_row(ws, row, ncols, stripe=False):
        for col in range(1, ncols + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = Font(name="Calibri", size=11, color=TEXT)
            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
            cell.border = thin_border()
            if stripe:
                cell.fill = stripe_fill()
        ws.row_dimensions[row].height = 26

    # ---------- Tab: Front ----------
    ws = wb.active
    ws.title = "Front"

    # Title row
    ws.cell(row=1, column=1, value="SYGMA TRAINING COURSE MASTER")
    ws.cell(row=1, column=1).font = Font(name="Calibri", size=18, bold=True, color=SYGMA_NAVY)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
    ws.row_dimensions[1].height = 30

    courses = data.get("courses", [])
    last_updated = data.get("last_updated", "")
    next_c = data.get("next_codes", {}).get("C", "?")

    subtitle = f"Live -- auto-rendered from canonical YAML  ·  Last updated: {last_updated}  ·  {len(courses)} entries  ·  Next free code: C{next_c:03d}"
    ws.cell(row=2, column=1, value=subtitle)
    ws.cell(row=2, column=1).font = Font(name="Calibri", size=10, italic=True, color="6C757D")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=8)

    # Orange accent line at row 3
    ws.row_dimensions[3].height = 4
    for col in range(1, 9):
        ws.cell(row=3, column=col).fill = PatternFill("solid", fgColor=SYGMA_ORANGE)

    # Header row at row 4: A Code | B Course Name | C Cert | ...
    # Course Name column collapses the previous Master Name / Variant Name /
    # CC Current / CC Ideal / Sygma Portal columns into one canonical name
    # (per Pete 2026-05-26: master sheet locked, YAML name = team-used CC name
    # = Sygma Portal import name). Variants render with `↳ ` indent prefix.
    headers = ["Code", "Course Name", "Cert", "Days", "Mode", "Topics", "Brief", "Alignment"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, 4, len(headers))

    # Body rows
    row = 5
    # Sort: masters by cert_type group (logical clustering), then by code within group.
    # Variants nest under their master (regardless of variant cert_type).
    masters = [c for c in courses if not c.get("derived_from")]
    variants = [c for c in courses if c.get("derived_from")]
    variants_by_master = {}
    for v in variants:
        variants_by_master.setdefault(v["derived_from"], []).append(v)

    # Cert-type ordering -- groups masters logically
    CERT_ORDER = {
        "In House":               1,
        "EUSR Accredited":        2,
        "EUSR Endorsed":          3,
        "ProQual Accredited":     4,
        "ProQual L2 Award":       5,
        "ProQual L3 Certificate": 6,
        "ProQual L4 Diploma":     7,
        "ProQual L5 Diploma":     8,
        "ProQual L6 Diploma":     9,
    }
    masters_sorted = sorted(
        masters,
        key=lambda c: (CERT_ORDER.get(c.get("cert_type", ""), 99), c["code"]),
    )
    striped = False
    for m in masters_sorted:
        # Master row
        _write_front_row(ws, row, m, indent=False)
        style_body_row(ws, row, len(headers), stripe=striped)
        # Cert pill (column 3 — Course Name now at col 2)
        cert_cell = ws.cell(row=row, column=3)
        cert_cell.fill = cert_pill_fill(m.get("cert_type", ""))
        cert_cell.font = Font(name="Calibri", size=11, bold=True, color=cert_pill_text_colour(m.get("cert_type", "")))
        cert_cell.alignment = Alignment(horizontal="center", vertical="center")
        # Alignment colour (column 8)
        align_cell = ws.cell(row=row, column=8)
        s = alignment_status(m)
        align_colour = {"green": "28A745", "yellow": "FFC107", "red": "DC3545", "retired": "6C757D"}.get(s, "999999")
        align_cell.fill = PatternFill("solid", fgColor=align_colour)
        align_cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
        align_cell.alignment = Alignment(horizontal="center", vertical="center")
        row += 1
        striped = not striped

        # Variant rows under this master, sorted by code
        for v in sorted(variants_by_master.get(m["code"], []), key=lambda x: x["code"]):
            _write_front_row(ws, row, v, indent=True)
            style_body_row(ws, row, len(headers), stripe=striped)
            cert_cell = ws.cell(row=row, column=3)
            cert_cell.fill = cert_pill_fill(v.get("cert_type", ""))
            cert_cell.font = Font(name="Calibri", size=11, bold=True, color=cert_pill_text_colour(v.get("cert_type", "")))
            cert_cell.alignment = Alignment(horizontal="center", vertical="center")
            align_cell = ws.cell(row=row, column=8)
            s = alignment_status(v)
            align_colour = {"green": "28A745", "yellow": "FFC107", "red": "DC3545", "retired": "6C757D"}.get(s, "999999")
            align_cell.fill = PatternFill("solid", fgColor=align_colour)
            align_cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
            align_cell.alignment = Alignment(horizontal="center", vertical="center")
            row += 1
            striped = not striped

    # Column widths -- A Code, B Course Name, C Cert, D Days, E Mode, F Topics, G Brief, H Alignment
    widths = [13, 60, 22, 6, 22, 22, 55, 11]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Auto-fit row heights for wrapped content (Course Name col 2, Brief col 7)
    for r in range(5, row):
        name_len = len(str(ws.cell(row=r, column=2).value or ""))
        brief_len = len(str(ws.cell(row=r, column=7).value or ""))
        lines_needed = max(1, max(brief_len // 55 + 1, name_len // 60 + 1))
        ws.row_dimensions[r].height = max(26, lines_needed * 18)

    # Freeze header (freeze first col + header row so codes stay visible when scrolling)
    ws.freeze_panes = "B5"
    # Auto filter on data (now to column H)
    ws.auto_filter.ref = f"A4:H{row - 1}"

    # ---------- Tab: Topics ----------
    ws_t = wb.create_sheet("Topics")
    ws_t.cell(row=1, column=1, value="TOPIC REGISTER (T-codes)")
    ws_t.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_t.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)

    headers_t = ["T-code", "Topic Name", "Primary Course", "Used By", "Notes"]
    for col, h in enumerate(headers_t, 1):
        ws_t.cell(row=3, column=col, value=h)
    style_header_row(ws_t, 3, len(headers_t))

    row = 4
    striped = False
    for t in sorted(data.get("topics", []), key=lambda x: x["code"]):
        ws_t.cell(row=row, column=1, value=t["code"])
        ws_t.cell(row=row, column=2, value=t.get("name", ""))
        ws_t.cell(row=row, column=3, value=t.get("primary_course", ""))
        ws_t.cell(row=row, column=4, value=", ".join(t.get("used_by", []) or []))
        ws_t.cell(row=row, column=5, value=t.get("notes", ""))
        style_body_row(ws_t, row, len(headers_t), stripe=striped)
        row += 1
        striped = not striped

    for i, w in enumerate([12, 42, 18, 50, 80], 1):
        ws_t.column_dimensions[get_column_letter(i)].width = w
    ws_t.freeze_panes = "A4"
    # Row heights for wrapped notes
    for r in range(4, ws_t.max_row + 1):
        notes_len = len(str(ws_t.cell(row=r, column=5).value or ""))
        if notes_len > 80:
            ws_t.row_dimensions[r].height = max(26, (notes_len // 80 + 1) * 18)

    # ---------- Tab: Customer Variants ----------
    ws_v = wb.create_sheet("Customer Variants")
    ws_v.cell(row=1, column=1, value="CUSTOMER-SPECIFIC VARIANTS")
    ws_v.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_v.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)

    headers_v = ["Code", "Customer", "Variant Name", "Master (derived from)", "Cert", "Days", "Notes"]
    for col, h in enumerate(headers_v, 1):
        ws_v.cell(row=3, column=col, value=h)
    style_header_row(ws_v, 3, len(headers_v))

    row = 4
    striped = False
    for v in sorted(variants, key=lambda x: x["code"]):
        ws_v.cell(row=row, column=1, value=v["code"])
        ws_v.cell(row=row, column=2, value=v.get("customer", ""))
        ws_v.cell(row=row, column=3, value=v.get("name", ""))
        ws_v.cell(row=row, column=4, value=v.get("derived_from", ""))
        ws_v.cell(row=row, column=5, value=v.get("cert_type", ""))
        ws_v.cell(row=row, column=6, value=days_label(v))
        ws_v.cell(row=row, column=7, value=v.get("notes", ""))
        style_body_row(ws_v, row, len(headers_v), stripe=striped)
        cert_cell = ws_v.cell(row=row, column=5)
        cert_cell.fill = cert_pill_fill(v.get("cert_type", ""))
        cert_cell.font = Font(name="Calibri", size=11, bold=True, color=cert_pill_text_colour(v.get("cert_type", "")))
        cert_cell.alignment = Alignment(horizontal="center", vertical="center")
        row += 1
        striped = not striped

    for i, w in enumerate([14, 12, 50, 22, 22, 6, 80], 1):
        ws_v.column_dimensions[get_column_letter(i)].width = w
    ws_v.freeze_panes = "A4"
    for r in range(4, ws_v.max_row + 1):
        notes_len = len(str(ws_v.cell(row=r, column=7).value or ""))
        if notes_len > 80:
            ws_v.row_dimensions[r].height = max(26, (notes_len // 80 + 1) * 18)

    # ---------- Tab: Asset Inventory ----------
    ws_a = wb.create_sheet("Asset Inventory")
    ws_a.cell(row=1, column=1, value="ASSET INVENTORY (per course × asset type)")
    ws_a.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_a.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)

    headers_a = ["Code", "Course", "Asset Type", "Source", "Status", "Version", "Last Reviewed", "Reviewed By", "Filename"]
    for col, h in enumerate(headers_a, 1):
        ws_a.cell(row=3, column=col, value=h)
    style_header_row(ws_a, 3, len(headers_a))

    row = 4
    striped = False
    for c in sorted(courses, key=lambda x: x["code"]):
        for asset_type, asset in (c.get("assets") or {}).items():
            inherits = asset.get("inherits_from") if isinstance(asset, dict) else None
            source_label = "OWN" if not inherits else f"↳ {inherits}"
            ws_a.cell(row=row, column=1, value=c["code"])
            ws_a.cell(row=row, column=2, value=c.get("name", ""))
            ws_a.cell(row=row, column=3, value=asset_type)
            ws_a.cell(row=row, column=4, value=source_label)
            ws_a.cell(row=row, column=5, value=asset.get("status", ""))
            ws_a.cell(row=row, column=6, value=asset.get("version", "") or "")
            ws_a.cell(row=row, column=7, value=str(asset.get("last_reviewed") or ""))
            ws_a.cell(row=row, column=8, value=asset.get("reviewed_by", "") or "")
            ws_a.cell(row=row, column=9, value=asset.get("file_name", "") or "")
            style_body_row(ws_a, row, len(headers_a), stripe=striped)
            # Status colour
            status_cell = ws_a.cell(row=row, column=5)
            status_value = asset.get("status", "") or ""
            colour = STATUS_COLOURS.get(status_value, "DDDDDD")
            status_cell.fill = PatternFill("solid", fgColor=colour)
            status_cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
            status_cell.alignment = Alignment(horizontal="center", vertical="center")
            # Source style: italic + lighter text when inherited
            source_cell = ws_a.cell(row=row, column=4)
            if inherits:
                source_cell.font = Font(name="Calibri", size=10, italic=True, color="6C757D")
            else:
                source_cell.font = Font(name="Calibri", size=10, color=TEXT)
            row += 1
            striped = not striped

    for i, w in enumerate([14, 50, 22, 14, 14, 9, 14, 14, 55], 1):
        ws_a.column_dimensions[get_column_letter(i)].width = w
    ws_a.freeze_panes = "A4"
    ws_a.auto_filter.ref = f"A3:I{row - 1}"

    # ---------- Tab: CC Actions (hand-maintained) ----------
    # Consumes outstanding_cc_actions[] from YAML (schema 1.3+). No longer
    # auto-derives from aliases (which proved to be historical noise, not
    # live CC state -- see 2026-05-26 lock decision).
    ws_m = wb.create_sheet("CC Actions")
    ws_m.cell(row=1, column=1, value="CC ACTION ITEMS (hand-maintained)")
    ws_m.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_m.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)

    ws_m.cell(row=2, column=1,
              value="Real, decided CC-side action items. Source: _course-map.yaml `outstanding_cc_actions`. "
                    "When all done, this tab will read 'No outstanding CC actions.'")
    ws_m.cell(row=2, column=1).font = Font(name="Calibri", size=10, italic=True, color="6C757D")
    ws_m.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)

    actions = data.get("outstanding_cc_actions", []) or []

    headers_m = ["Code", "Action", "CC Current", "CC Target", "Owner", "Reason"]
    for col, h in enumerate(headers_m, 1):
        ws_m.cell(row=4, column=col, value=h)
    style_header_row(ws_m, 4, len(headers_m))

    row = 5
    if not actions:
        ws_m.cell(row=row, column=1, value="No outstanding CC actions.")
        ws_m.cell(row=row, column=1).font = Font(name="Calibri", size=11, italic=True, color="28A745")
        ws_m.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    else:
        striped = False
        for a in actions:
            ws_m.cell(row=row, column=1, value=a.get("code", ""))
            ws_m.cell(row=row, column=2, value=a.get("action", ""))
            ws_m.cell(row=row, column=3, value=a.get("cc_current", ""))
            ws_m.cell(row=row, column=4, value=a.get("cc_target", ""))
            ws_m.cell(row=row, column=5, value=a.get("owner", ""))
            ws_m.cell(row=row, column=6, value=a.get("reason", ""))
            style_body_row(ws_m, row, len(headers_m), stripe=striped)
            # Action colour
            action_cell = ws_m.cell(row=row, column=2)
            action_colour = {"ADD to CC": "FFC107", "RENAME in CC": "5BC0DE", "REMOVE from CC": "DC3545"}.get(a.get("action", ""), "999999")
            action_cell.fill = PatternFill("solid", fgColor=action_colour)
            action_cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
            action_cell.alignment = Alignment(horizontal="center", vertical="center")
            row += 1
            striped = not striped

    # If no rows, add an "all aligned" message
    if row == 5:
        ws_m.cell(row=5, column=1, value="✓ All courses aligned with CC. No action needed.")
        ws_m.cell(row=5, column=1).font = Font(name="Calibri", size=12, bold=True, color="28A745")
        ws_m.merge_cells(start_row=5, start_column=1, end_row=5, end_column=5)

    for i, w in enumerate([14, 42, 50, 42, 28], 1):
        ws_m.column_dimensions[get_column_letter(i)].width = w
    ws_m.freeze_panes = "A5"

    # ---------- Tab: Excluded Entries ----------
    ws_e = wb.create_sheet("Excluded Entries")
    ws_e.cell(row=1, column=1, value="EXCLUDED EXTERNAL ENTRIES (the bin record)")
    ws_e.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_e.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)

    headers_e = ["Source", "Name", "Reason", "Excluded Date"]
    for col, h in enumerate(headers_e, 1):
        ws_e.cell(row=3, column=col, value=h)
    style_header_row(ws_e, 3, len(headers_e))

    row = 4
    striped = False
    for entry in data.get("excluded_external_entries", []):
        ws_e.cell(row=row, column=1, value=entry.get("source", ""))
        ws_e.cell(row=row, column=2, value=entry.get("name", ""))
        ws_e.cell(row=row, column=3, value=entry.get("reason", ""))
        ws_e.cell(row=row, column=4, value=str(entry.get("excluded_date", "")))
        style_body_row(ws_e, row, len(headers_e), stripe=striped)
        row += 1
        striped = not striped

    for i, w in enumerate([22, 50, 80, 14], 1):
        ws_e.column_dimensions[get_column_letter(i)].width = w
    ws_e.freeze_panes = "A4"
    for r in range(4, ws_e.max_row + 1):
        reason_len = len(str(ws_e.cell(row=r, column=3).value or ""))
        if reason_len > 80:
            ws_e.row_dimensions[r].height = max(26, (reason_len // 80 + 1) * 18)

    # ---------- Tab: Code Allocation ----------
    ws_c = wb.create_sheet("Code Allocation")
    ws_c.cell(row=1, column=1, value="CODE ALLOCATION REGISTER")
    ws_c.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_c.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)

    next_codes = data.get("next_codes", {})
    customer_suffixes = data.get("customer_suffixes", {})

    ws_c.cell(row=3, column=1, value="Next free C-code:")
    ws_c.cell(row=3, column=2, value=f"C{next_codes.get('C', 0):03d}")
    ws_c.cell(row=4, column=1, value="Next free T-code:")
    ws_c.cell(row=4, column=2, value=f"T{next_codes.get('T', 0):03d}")
    for r in (3, 4):
        ws_c.cell(row=r, column=1).font = Font(name="Calibri", size=12, bold=True)
        ws_c.cell(row=r, column=2).font = Font(name="Calibri", size=12, color=SYGMA_NAVY, bold=True)

    ws_c.cell(row=6, column=1, value="CUSTOMER SUFFIX REGISTER")
    ws_c.cell(row=6, column=1).font = Font(name="Calibri", size=14, bold=True, color=SYGMA_NAVY)
    ws_c.cell(row=8, column=1, value="Suffix")
    ws_c.cell(row=8, column=2, value="Customer")
    style_header_row(ws_c, 8, 2)

    row = 9
    striped = False
    for suffix, customer in customer_suffixes.items():
        ws_c.cell(row=row, column=1, value=suffix)
        ws_c.cell(row=row, column=2, value=customer)
        style_body_row(ws_c, row, 2, stripe=striped)
        row += 1
        striped = not striped

    ws_c.cell(row=row + 1, column=1, value="IMMUTABILITY RULE")
    ws_c.cell(row=row + 1, column=1).font = Font(name="Calibri", size=12, bold=True, color=SYGMA_ORANGE)
    ws_c.cell(row=row + 2, column=1, value="Codes are immutable. Once allocated, they're never reused -- even if the course is renamed or retired. Renames change display name only.")
    ws_c.merge_cells(start_row=row + 2, start_column=1, end_row=row + 2, end_column=2)
    ws_c.cell(row=row + 2, column=1).font = Font(name="Calibri", size=11, italic=True)
    ws_c.cell(row=row + 2, column=1).alignment = Alignment(wrap_text=True)

    for i, w in enumerate([18, 40], 1):
        ws_c.column_dimensions[get_column_letter(i)].width = w

    # ---------- Tab: Change Log ----------
    ws_l = wb.create_sheet("Change Log")
    ws_l.cell(row=1, column=1, value="CHANGE LOG (append-only)")
    ws_l.cell(row=1, column=1).font = Font(name="Calibri", size=16, bold=True, color=SYGMA_NAVY)
    ws_l.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)

    headers_l = ["Date", "Type", "By", "Description"]
    for col, h in enumerate(headers_l, 1):
        ws_l.cell(row=3, column=col, value=h)
    style_header_row(ws_l, 3, len(headers_l))

    row = 4
    striped = False
    for entry in data.get("change_log", []):
        ws_l.cell(row=row, column=1, value=str(entry.get("date", "")))
        ws_l.cell(row=row, column=2, value=entry.get("type", ""))
        ws_l.cell(row=row, column=3, value=entry.get("by", ""))
        ws_l.cell(row=row, column=4, value=entry.get("description", ""))
        style_body_row(ws_l, row, len(headers_l), stripe=striped)
        ws_l.row_dimensions[row].height = 100
        row += 1
        striped = not striped

    for i, w in enumerate([12, 22, 16, 110], 1):
        ws_l.column_dimensions[get_column_letter(i)].width = w
    ws_l.freeze_panes = "A4"

    # Save
    wb.save(out_path)
    return out_path


def _write_front_row(ws, row, course, indent=False):
    """Write a front-tab row.
    A: Code (always)
    B: Course Name (one column; variants render as `    ↳ <name>` indent)
    C: Cert  D: Days  E: Mode  F: Topics  G: Brief  H: Alignment
    """
    name = course.get("name", "")
    code = course["code"]
    ws.cell(row=row, column=1, value=code)
    display_name = f"    ↳ {name}" if indent else name
    ws.cell(row=row, column=2, value=display_name)
    ws.cell(row=row, column=3, value=course.get("cert_type", ""))
    ws.cell(row=row, column=4, value=days_label(course))
    ws.cell(row=row, column=5, value=mode_label(course))
    ws.cell(row=row, column=6, value=topics_label(course))
    ws.cell(row=row, column=7, value=short_brief(course))
    ws.cell(row=row, column=8, value="")                          # alignment -- styled


# ============================================================
# Markdown rendering (vault + Hub READMEs)
# ============================================================

def render_master_list_md(data, out_path):
    CERT_ORDER = {
        "In House":               1,
        "EUSR Accredited":        2,
        "EUSR Endorsed":          3,
        "ProQual Accredited":     4,
        "ProQual L2 Award":       5,
        "ProQual L3 Certificate": 6,
        "ProQual L4 Diploma":     7,
        "ProQual L5 Diploma":     8,
        "ProQual L6 Diploma":     9,
    }
    raw = data.get("courses", [])
    masters = [c for c in raw if not c.get("derived_from")]
    variants_by_master = {}
    for v in [c for c in raw if c.get("derived_from")]:
        variants_by_master.setdefault(v["derived_from"], []).append(v)
    masters_sorted = sorted(masters, key=lambda c: (CERT_ORDER.get(c.get("cert_type", ""), 99), c["code"]))
    courses = []
    for m in masters_sorted:
        courses.append(m)
        for v in sorted(variants_by_master.get(m["code"], []), key=lambda x: x["code"]):
            courses.append(v)
    next_c = data.get("next_codes", {}).get("C", 0)
    last_updated = data.get("last_updated", "")

    lines = []
    lines.append("---")
    lines.append("type: course-master-list-rendered")
    lines.append(f"updated: {last_updated}")
    lines.append("source_of_truth: \"[[_course-map.yaml]]\"")
    lines.append("---")
    lines.append("")
    lines.append("# Sygma Course Master List -- Rendered")
    lines.append("")
    lines.append(f"_Auto-rendered from `_course-map.yaml`. Last updated: {last_updated}. {len(courses)} entries. Next free code: `C{next_c:03d}`._")
    lines.append("")
    lines.append("| Code | Name | Cert | Days | Mode | Topics | Brief |")
    lines.append("|------|------|------|------|------|--------|-------|")
    for c in courses:
        name = c.get("name", "")
        if c.get("derived_from"):
            name = f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {name}"
        lines.append(f"| {c['code']} | {name} | {c.get('cert_type','')} | {days_label(c)} | {mode_label(c)} | {topics_label(c)} | {short_brief(c)} |")
    lines.append("")
    lines.append(f"_Edit `_course-map.yaml` to change. Run `courses-render-map.py` to refresh._")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    return out_path


# ============================================================
# Main
# ============================================================

def main():
    args = sys.argv[1:]
    local_only = "--local-only" in args

    print(f"Loading YAML: {YAML_PATH}")
    data = load_yaml()
    print(f"  - {len(data.get('courses', []))} courses, {len(data.get('topics', []))} topics, {len(data.get('excluded_external_entries', []))} excluded")

    print("Validating...")
    validate(data)
    print("  - OK")

    # Render xlsx locally first
    os.makedirs(LOCAL_OUT_DIR, exist_ok=True)
    xlsx_local = f"{LOCAL_OUT_DIR}/Course Mapping.xlsx"
    print(f"Rendering xlsx -> {xlsx_local}")
    render_xlsx(data, xlsx_local)
    print("  - OK")

    # Render vault master-list.md
    print(f"Rendering vault md -> {VAULT_MASTER_LIST_PATH}")
    render_master_list_md(data, VAULT_MASTER_LIST_PATH)
    print("  - OK")

    if local_only:
        print(f"\n--local-only -- skipping Hub upload")
        print(f"\nXLSX:  {xlsx_local}")
        print(f"MD:    {VAULT_MASTER_LIST_PATH}")
        return

    # Upload xlsx to Hub Drive
    print("\nUploading xlsx to Hub Drive...")
    spec = importlib.util.spec_from_file_location(
        "drive_api",
        os.path.join(SCRIPTS_DIR, "drive-api.py"),
    )
    drive_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drive_api)

    # Update-in-place: find existing Course Mapping.xlsx on Hub; PATCH its content
    # so the file ID + permalink stay stable across renders. Falls back to fresh
    # upload only if the file doesn't exist yet (first-ever render). Behaviour
    # change 2026-05-26 -- was trash-and-replace, which broke external links on
    # every render.
    existing = drive_api.api("GET", "/files", {
        "q": f"'{HUB_COURSES_FOLDER_ID}' in parents and name = 'Course Mapping.xlsx' and trashed = false",
        "fields": "files(id,name)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }).get("files", [])

    if existing:
        # Update content in place. If there are duplicates (rare), update the first + trash the rest.
        target_id = existing[0]["id"]
        with open(xlsx_local, "rb") as f:
            xlsx_bytes = f.read()
        import urllib.request as _ur
        req = _ur.Request(
            f"https://www.googleapis.com/upload/drive/v3/files/{target_id}?uploadType=media&supportsAllDrives=true",
            data=xlsx_bytes,
            headers={
                "Authorization": f"Bearer {drive_api.get_token()}",
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            method="PATCH",
        )
        _ur.urlopen(req).read()
        print(f"  - Updated Course Mapping.xlsx in place (ID {target_id} preserved)")
        # Trash duplicates if any
        for dup in existing[1:]:
            drive_api.trash_file(dup["id"])
            print(f"  - Trashed duplicate {dup['id']}")
    else:
        drive_api.upload_file(xlsx_local, HUB_COURSES_FOLDER_ID, "Course Mapping.xlsx")
        print(f"  - Uploaded fresh Course Mapping.xlsx (first-ever render)")

    # Publish the shared brain folder (md docs both Pete's and Jim's Claude read)
    _publish_brain(drive_api)

    print("\nDone.")
    print(f"\nXLSX (local):  {xlsx_local}")
    print(f"XLSX (Hub):    Hub/Courses/Course Mapping.xlsx (uploaded)")
    print(f"MD (vault):    {VAULT_MASTER_LIST_PATH}")
    print(f"Brain (Hub):   Hub/Courses/_brain/ (5 md docs + README)")


def _publish_brain(drive_api):
    """Publish the shared brain folder to Hub/Courses/_brain/.

    Both Pete's Claude and Jim's Claude can read the Hub. This folder is the
    only sustained shared-knowledge layer between them for the course system.
    Replaces existing files on every run (idempotent).
    """
    print("\nPublishing shared brain folder to Hub/Courses/_brain/...")

    # Build the README inline (avoids a separate template file)
    from datetime import datetime as _dt
    readme_content = f"""---
type: shared-brain-readme
audience: "Both Claudes (Pete's Cowork + Jim's Claude)"
canonical_source: "Pete's vault: Businesses/sygma-solutions/training/courses/"
auto_synced: true
last_published: {_dt.now().strftime('%Y-%m-%d %H:%M')}
---

# Sygma Courses — Shared Operational Brain

This folder is the **shared operational brain** for the Sygma course code system. It exists because:

1. The course code system has to be understood the same way by both Pete's Claude (Cowork on Pete's Mac) and Jim's Claude (running from Jim's vault on Jim's Windows machine).
2. Hub is the only place both Claudes have read access to without one having to share their personal vault.
3. The truth lives in Pete's vault (canonical YAML at `Businesses/sygma-solutions/training/courses/_course-map.yaml`). This folder is **auto-published from there** by `Library/processes/scripts/courses-render-map.py` every time the master sheet is re-rendered.

**Do not edit files in this folder directly.** Edits get overwritten on the next render. To make a change:
- Pete's Claude: edit the vault YAML + run the render script.
- Jim's Claude: ask Pete or open an Asana task. Don't modify Hub course docs directly.

## What's in here

| File | What it covers |
|---|---|
| `code-system.md` | The C-code / T-code / customer-suffix / cert-suffix rules. Read this BEFORE allocating any new code. |
| `cross-system-usage.md` | Where C-codes appear across CC (until cutover), Sygma Portal, calendar, Xero, Asana, training spreadsheets. What to update when. |
| `audit-protocol.md` | When + how to audit course drift between systems. |
| `sop-course-lifecycle.md` | Step-by-step SOPs for adding, renaming, retiring a course. |
| `master-list.md` | Rendered full course list — same data as `Course Mapping.xlsx`, in markdown for grep / direct read. |

## What's NOT in here

This folder is **course-system-only**. Anything broader (voice principles, finance workflows, customer routing rules, etc.) lives in Pete's personal vault and is not shared via Hub. Jim's Claude has its own conventions for those areas (in Jim's vault).

## What about `Course Mapping.xlsx`?

The xlsx is the structured view of the same data. Same source of truth (`_course-map.yaml`), same render script. The xlsx is the human-readable table; the md files in this folder are the rules + the rendered list.

## When does this folder change?

Every run of `courses-render-map.py`. The script publishes:
1. `Course Mapping.xlsx` (one level up at `Hub/Courses/`)
2. The 5 md files in this folder
3. Updates Pete's vault `master-list.md`

If you see stale dates here, ask Pete to re-run the render.
"""

    # Write README to a temp file so we can use drive_api.upload_file (same pattern as xlsx)
    readme_local = f"{LOCAL_OUT_DIR}/_brain_README.md"
    with open(readme_local, "w") as f:
        f.write(readme_content)

    # Manifest: README plus the 5 doc files
    to_publish = [(readme_local, "README.md")]
    for vault_rel, hub_name in BRAIN_DOCS:
        vault_abs = f"{VAULT}/{vault_rel}"
        if not _os.path.isfile(vault_abs):
            print(f"  - SKIP {hub_name} (vault source missing: {vault_abs})")
            continue
        to_publish.append((vault_abs, hub_name))

    # Update-in-place so file IDs + permalinks stay stable across renders.
    import urllib.request as _ur
    for local_path, hub_name in to_publish:
        existing = drive_api.api("GET", "/files", {
            "q": f"'{HUB_COURSES_BRAIN_FOLDER_ID}' in parents and name = '{hub_name}' and trashed = false",
            "fields": "files(id,name)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }).get("files", [])
        if existing:
            target_id = existing[0]["id"]
            with open(local_path, "rb") as f:
                content = f.read()
            req = _ur.Request(
                f"https://www.googleapis.com/upload/drive/v3/files/{target_id}?uploadType=media&supportsAllDrives=true",
                data=content,
                headers={"Authorization": f"Bearer {drive_api.get_token()}", "Content-Type": "text/markdown"},
                method="PATCH",
            )
            _ur.urlopen(req).read()
            print(f"  - Updated {hub_name} in place (ID {target_id} preserved)")
            for dup in existing[1:]:
                drive_api.trash_file(dup["id"])
        else:
            drive_api.upload_file(local_path, HUB_COURSES_BRAIN_FOLDER_ID, hub_name)
            print(f"  - Published fresh {hub_name}")


if __name__ == "__main__":
    main()