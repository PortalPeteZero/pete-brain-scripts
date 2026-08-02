#!/usr/bin/env python3
"""clancy-dn-reports.py — the reports library inside Genny's Damage Depot.

WHY: everything Sygma has produced for Clancy — damage reviews, panel packs, meeting summaries,
business cases, plus their own reference documents — was scattered across separate modules or sat
in `clancy_reports` with no page at all. Pete, 31 Jul 2026: "a reports section, which should then
go into a new page with some nice looking cards which link to all the reports and articles i have
done, and will add to like the damage reports, but basically anything i produce for them, search
and filter at the top".

So: one card per report, search + type filter at the top, and every card links to something that
actually opens.

  - Reports that already have their own live module link straight to it.
  - Reports that only exist as stored text in `clancy_reports` get a sub-page rendered here, so
    nothing in the library is a dead card. Panel packs are text extracted from the source
    PowerPoint and are labelled as such rather than dressed up as prose.
  - Where a report belongs to a damage, the card links to that damage's record too, so the
    finding and the register entry can be read side by side.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-reports.py [--local DIR] [--publish]
"""
import os, re, json, html as H, argparse, datetime, urllib.request, urllib.error
import clancy_dn_ui as ui


def _urlopen_retry(req, timeout=120, tries=9):
    """Supabase answers 429 under load. clancy-dn-publish.py runs six of these tools back to
    back and each writes many rows, so the later steps reliably hit it - observed 2 Aug 2026,
    where the FY26/27 analysis build died mid-run on a 429 and left that page stale while every
    other page had been rebuilt. Without backoff a publish half-updates the section and the
    freshness report is the only clue. Retries 429 and 5xx with exponential backoff."""
    import time as _t
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))
        except Exception:
            if n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))


VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = ui.REPORTS

# Report types, in the order they should appear on the filter bar. The label is what a reader
# sees; the key is what `clancy_reports.report_type` actually stores.
TYPES = [
    ("review", "Damage review", "A written review of a specific service damage."),
    ("revisit", "Site revisit", "A follow-up visit after a damage review."),
    ("panel-pack", "Panel pack", "The incident panel's own pack, text extracted from the source PowerPoint."),
    ("meeting-summary", "Panel summary", "Sygma's note of what was said at the review panel."),
    ("business-case", "Business case", "A case put to Clancy for a change in kit or practice."),
    ("procedure", "Procedure", "A Clancy procedure held here for reference."),
    ("strategy", "Strategy", "A Clancy strategy document held here for reference."),
]
TLABEL = {k_: v for k_, v, _ in TYPES}
TBLURB = {k_: b for k_, _, b in TYPES}
# Who wrote it. Reference documents are Clancy's own; everything else Sygma produced.
CLANCY_OWN = {"procedure", "strategy", "panel-pack"}


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(_urlopen_retry(req, timeout=180).read().decode())


PAGE_CSS = """
.tools{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:22px 0 4px;
 background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;
 box-shadow:var(--sh-1);position:sticky;top:calc(var(--nav-h) + 37px);z-index:40}
.tools input[type=search]{flex:1;min-width:210px;font:inherit;font-size:14px;padding:9px 13px;
 border:1px solid var(--line);border-radius:10px;background:#f8fafc;color:var(--ink)}
.tools input[type=search]:focus{outline:2px solid var(--green);outline-offset:1px;background:#fff}
.fset{display:flex;gap:6px;flex-wrap:wrap}
.fbtn{font:inherit;font-size:12.5px;font-weight:700;padding:7px 13px;border-radius:99px;
 border:1px solid var(--line);background:#f8fafc;color:var(--mid);cursor:pointer;
 transition:background .16s,color .16s,border-color .16s}
.fbtn:hover{border-color:#c8d0da;color:var(--ink)}
.fbtn[aria-pressed=true]{background:var(--char);border-color:var(--char);color:#fff}
.fbtn[aria-pressed=true].all{background:var(--green);border-color:var(--green);color:#1d2b00}
.count{font-size:12.5px;color:var(--faint);margin:14px 0 12px;font-weight:600}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.rc{display:flex;flex-direction:column;background:#fff;border:1px solid var(--line);
 border-radius:16px;padding:20px 21px 17px;text-decoration:none;color:var(--ink);
 box-shadow:var(--sh-2);transition:transform .2s cubic-bezier(.2,.7,.3,1),box-shadow .2s;
 position:relative;overflow:hidden}
.rc:hover{transform:translateY(-4px);box-shadow:var(--sh-3)}
.rc::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--green)}
.rc[data-type=panel-pack]::before,.rc[data-type=procedure]::before,
.rc[data-type=strategy]::before{background:var(--char)}
.rc[data-type=review]::before,.rc[data-type=revisit]::before{background:var(--red)}
.rc .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:8px}
.rc .type{font-size:10.5px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;
 color:var(--faint)}
.rc .date{font-size:11.5px;color:var(--faint);white-space:nowrap;font-variant-numeric:tabular-nums}
.rc h3{font-size:16.5px;font-weight:800;letter-spacing:-.015em;line-height:1.32;margin-bottom:9px}
.rc .ex{font-size:13px;color:var(--mid);line-height:1.5;flex:1}
.rc .meta{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px;padding-top:12px;
 border-top:1px solid #eef1f5}
.tag{font-size:11px;font-weight:700;padding:3px 9px;border-radius:99px;background:#f1f4f8;
 color:var(--muted)}
.tag.sy{background:rgba(151,215,0,.20);color:#4e7300}
.tag.cl{background:#e9edf2;color:#4a5560}
.tag.dm{background:rgba(213,0,50,.10);color:var(--red)}
.rc .go{margin-top:11px;font-size:13px;font-weight:800;color:var(--green-d)}
.rc:hover .go{text-decoration:underline}
.empty{display:none;background:#fff;border:1px dashed #cbd3dd;border-radius:14px;padding:34px;
 text-align:center;color:var(--faint);font-size:14px}
/* report sub-page */
.doc{background:#fff;border:1px solid var(--line);border-radius:16px;padding:30px 34px;
 box-shadow:var(--sh-1);margin-top:22px}
@media(max-width:640px){.doc{padding:22px 20px}}
.doc h2{font-size:17px;font-weight:800;letter-spacing:-.01em;margin:26px 0 10px;
 padding-bottom:8px;border-bottom:2px solid var(--green)}
.doc h2:first-child{margin-top:0}
.doc p{font-size:14.5px;color:var(--mid);margin-bottom:13px;max-width:82ch}
.doc .slide{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
 color:var(--faint);margin:18px 0 5px}
.src{background:#fff;border:1px solid var(--line);border-left:4px solid var(--char);
 border-radius:0 12px 12px 0;padding:14px 18px;font-size:13px;color:var(--mid);margin-top:20px}
.src b{color:var(--ink)}
.backrow{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}
.backrow a{font-size:13px;font-weight:700;text-decoration:none;color:var(--green-d);
 background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 15px;
 box-shadow:var(--sh-1)}
.backrow a:hover{border-color:var(--green)}
"""

FILTER_JS = """
<script>
(function(){
  var q=document.getElementById('q'), cards=[].slice.call(document.querySelectorAll('.rc')),
      btns=[].slice.call(document.querySelectorAll('.fbtn')), out=document.getElementById('count'),
      empty=document.getElementById('empty'), type='all';
  function apply(){
    var t=(q.value||'').toLowerCase().trim(), n=0;
    cards.forEach(function(c){
      var okT = type==='all' || c.dataset.type===type;
      var okQ = !t || c.dataset.hay.indexOf(t)>-1;
      var show = okT && okQ;
      c.style.display = show ? '' : 'none';
      if(show) n++;
    });
    out.textContent = n + (n===1 ? ' report' : ' reports')
      + (type==='all' ? '' : ' of this type') + (t ? ' matching "'+q.value.trim()+'"' : '');
    empty.style.display = n ? 'none' : 'block';
  }
  q.addEventListener('input', apply);
  btns.forEach(function(b){
    b.addEventListener('click', function(){
      type=b.dataset.type;
      btns.forEach(function(x){ x.setAttribute('aria-pressed', x===b ? 'true':'false'); });
      apply();
    });
  });
  apply();
})();
</script>
"""


def esc(s):
    return H.escape(str(s or ""))


def excerpt(full, sections, n=210, title=""):
    """A readable opening line for the card.

    The stored text is markdown carrying markers a reader should never see on a card: [slide 1]
    from the PowerPoint extraction, [figure: …] and [image: …] placeholders, and Obsidian-style
    [!callout] blocks. Several documents also open by repeating their own title and a
    "X | Sygma Solutions for The Clancy Group" masthead line, which wastes the whole excerpt on
    something the card already shows above it. All of that comes off here.
    """
    # Some packs open on a utility drawing whose only text is a scale bar and the printer
    # disclaimer, or on the source filename. Neither tells a reader anything, so skip past them
    # to the first section that carries real words.
    JUNK = re.compile(r"^(?:FILE:|[\d\s.]{6,}$|.{0,40}quality and accuracy of any print)", re.I)
    t = ""
    for s in sorted(sections or [], key=lambda s: s.get("order", 0)):
        cand = re.sub(r"\[slide \d+\]", " ", (s.get("body_md") or "")).strip()
        cand = re.sub(r"^FILE:[^\n]*\n?", "", cand).strip()
        if cand and not JUNK.match(cand):
            t = cand
            break
        t = t or cand
    t = H.unescape(t or (full or ""))
    t = re.sub(r"\[slide \d+\]", " ", t)
    t = re.sub(r"\[!\w+\]", " ", t)                       # [!important], [!note] …
    t = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", r"\1", t)   # [[wikilink]] -> its text
    t = re.sub(r"\[(?:figure|image|photo|chart)[^\]]*\]", " ", t, flags=re.I)
    t = re.sub(r"^\s*#+\s*", "", t)
    t = re.sub(r"[#*_>`]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Drop a leading masthead run. Only strip the title when the text genuinely opens by repeating
    # it — a loose stem match ate the front of real sentences and left them starting mid-phrase.
    if title:
        lead = re.escape(title.strip())
        t = re.sub(rf"^{lead}[:\s|—·-]*", "", t, flags=re.I).strip()
    for _ in range(3):
        before = t
        t = re.sub(r"^Sygma Solutions(?: Ltd)?(?: for The Clancy Group)?[\s|·—-]*", "", t, flags=re.I)
        t = re.sub(r"^(?:The Clancy Group|Prepared (?:for|by)|Strategic Training Partnership)"
                   r"[\s|·—-]*", "", t, flags=re.I)
        t = re.sub(r"^[^|]{0,60}\|\s*", "", t) if "|" in t[:70] else t
        t = t.strip()
        if t == before:
            break
    return (t[:n].rsplit(" ", 1)[0] + "…") if len(t) > n else t


def render_body(sections, full):
    """Render a stored report as readable HTML. Slide markers become their own small headings so
    a panel pack still reads in the order the pack ran."""
    out = []
    for s in sorted(sections or [], key=lambda s: s.get("order", 0)):
        head = (s.get("heading") or "").strip()
        if head:
            out.append(f"<h2>{esc(head.replace('_', ' '))}</h2>")
        body = H.unescape(s.get("body_md") or "")
        for para in [p for p in body.split("\n") if p.strip()]:
            m = re.match(r"^\[slide (\d+)\]\s*(.*)$", para.strip(), re.S)
            if m:
                out.append(f'<div class="slide">Slide {m.group(1)}</div>')
                para = m.group(2)
            if para.strip():
                out.append(f"<p>{esc(para.strip())}</p>")
    if not out and full:
        for para in [p for p in H.unescape(full).split("\n") if p.strip()]:
            out.append(f"<p>{esc(para.strip())}</p>")
    return "\n".join(out)


def fetch():
    rows = sql("""
      SELECT r.id, r.title, r.report_type, r.report_date, r.module_slug, r.damage_id,
             r.shareable, r.sections, r.full_text,
             m.slug AS live_slug,
             i.id AS dn_id, i.location AS dn_loc, i.fy AS dn_fy,
             i.incident_date::date AS dn_date
      FROM clancy_reports r
      LEFT JOIN modules m
        ON m.slug = r.module_slug AND m.enabled AND m.status = 'live'
      LEFT JOIN clancy_dn_incidents i
        ON i.sygma_legacy_id = r.damage_id
      -- Panel packs carry no date of their own. Order and label them by the damage they cover
      -- rather than dumping them all at the bottom as "undated".
      ORDER BY coalesce(r.report_date, i.incident_date::date) DESC NULLS LAST, r.id DESC""")
    STEM = {"FY26/27": "fy-2026-27", "FY25/26": "fy-2025-26",
            "FY24/25": "fy-2024-25", "FY23/24": "fy-2023-24"}
    for r in rows:
        r["href"] = (f"/m/{r['live_slug']}" if r["live_slug"]
                     else f"/raw/{MK}/report-{r['id']}.html")
        r["hosted"] = not r["live_slug"]
        # The damage detail page selects its record from the query string, not a fragment.
        r["dmg_href"] = (f"/raw/{ui.DAMAGES}/{STEM[r['dn_fy']]}-damage.html?id={r['dn_id']}"
                         if r["dn_id"] and r["dn_fy"] in STEM else None)
        # A report is dated by its own date where it has one. Where it does not — the panel packs
        # do not — fall back to the damage's incident date and SAY that is what is being shown,
        # rather than passing an incident date off as the date the document was written.
        if r["report_date"]:
            r["dateline"] = date_label(r["report_date"])
        elif r["dn_date"]:
            r["dateline"] = "Incident " + date_label(r["dn_date"])
        else:
            r["dateline"] = "Undated"
    return rows


def date_label(v):
    if not v:
        return "undated"
    return datetime.datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%-d %b %Y")


def index_page(rows):
    have = [t for t in TYPES if any(r["report_type"] == t[0] for r in rows)]
    fbtns = ('<button class="fbtn all" data-type="all" aria-pressed="true">'
             f"All {len(rows)}</button>")
    fbtns += "".join(
        f'<button class="fbtn" data-type="{k_}" aria-pressed="false">{lab} '
        f'({sum(1 for r in rows if r["report_type"] == k_)})</button>' for k_, lab, _ in have)

    cards = []
    for r in rows:
        typ = r["report_type"] or "review"
        hay = " ".join(str(x or "") for x in
                       (r["title"], TLABEL.get(typ, typ), r["dn_loc"], r["dn_id"],
                        r["dateline"], excerpt(r["full_text"], r["sections"], 400, r["title"]))
                       ).lower()
        tags = [f'<span class="tag {"cl" if typ in CLANCY_OWN else "sy"}">'
                f'{"Clancy document" if typ in CLANCY_OWN else "Sygma"}</span>']
        if r["dn_id"]:
            tags.append(f'<span class="tag dm">Damage {r["dn_id"]}</span>')
        if r["shareable"]:
            tags.append('<span class="tag">Shareable</span>')
        cards.append(
            f'<a class="rc" data-type="{esc(typ)}" data-hay="{esc(hay)}" href="{r["href"]}">'
            f'<div class="top"><span class="type">{esc(TLABEL.get(typ, typ))}</span>'
            f'<span class="date">{esc(r["dateline"])}</span></div>'
            f'<h3>{esc(r["title"])}</h3>'
            f'<div class="ex">{esc(excerpt(r["full_text"], r["sections"], 210, r["title"]))}</div>'
            f'<div class="meta">{"".join(tags)}</div>'
            f'<div class="go">Read it &rarr;</div></a>')

    today = datetime.date.today()
    return f"""{ui.head("Reports &amp; Reviews | Genny&#8217;s Damage Depot", PAGE_CSS)}
{ui.navbar("reports")}
{ui.crumbs(("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}"), "Reports")}
{ui.mast_compact("The library", "Reports &amp; Reviews",
                 "Everything Sygma has written for Clancy on service damage, plus the Clancy "
                 "documents that sit behind it. Search the lot, or filter by what kind of "
                 "document you are after.")}
<div class="wrap body">

<div class="tools">
 <input type="search" id="q" placeholder="Search titles, places, damage numbers, text&hellip;"
        aria-label="Search reports" autocomplete="off">
 <div class="fset">{fbtns}</div>
</div>
<div class="count" id="count"></div>

<div class="rgrid">{"".join(cards)}</div>
<div class="empty" id="empty">Nothing matches that. Clear the search or pick another type.</div>

<div class="dnote"><b>What is in here.</b> Damage reviews, site revisits and panel summaries are
Sygma&#8217;s own work. Panel packs are the incident panel&#8217;s pack, held here as the text
extracted from the source PowerPoint, so the wording is the panel&#8217;s and the slide order is
theirs. Procedures and strategy documents are Clancy&#8217;s own, kept here for reference. Where a
report belongs to a damage on the register, the card carries its damage number and the report
links back to the record.</div>

{ui.foot(today.strftime('%-d %b %Y'))}
</div>
{FILTER_JS}
{ui.TAIL}"""


def report_page(r):
    typ = r["report_type"] or "review"
    src = ""
    if typ == "panel-pack":
        src = ('<div class="src"><b>Source.</b> This is the incident panel&#8217;s own pack. The '
               "text below was extracted from the source PowerPoint, slide by slide, and is not "
               "edited. Slides that were photographs carry only their title, because there is no "
               "text on them to extract.</div>")
    elif typ in ("procedure", "strategy"):
        src = ('<div class="src"><b>Source.</b> This is a Clancy document, held here for '
               "reference. The wording is theirs.</div>")
    back = [f'<a href="/m/{MK}">&larr; All reports</a>']
    if r["dmg_href"]:
        back.append(f'<a href="{r["dmg_href"]}">Damage {r["dn_id"]} on the register &rarr;</a>')
    return f"""{ui.head(esc(r["title"]) + " | Genny&#8217;s Damage Depot", PAGE_CSS)}
{ui.navbar("reports")}
{ui.crumbs(("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}"),
           ("Reports", f"/m/{MK}"), TLABEL.get(typ, typ))}
{ui.mast_compact(TLABEL.get(typ, typ) + " &middot; " + esc(r["dateline"]),
                 esc(r["title"]),
                 (f"Damage {r['dn_id']}, {esc(r['dn_loc'])}." if r["dn_id"] else ""))}
<div class="wrap body">
{src}
<div class="doc">{render_body(r["sections"], r["full_text"])}</div>
<div class="backrow">{"".join(back)}</div>
{ui.foot(datetime.date.today().strftime('%-d %b %Y'))}
</div>
{ui.TAIL}"""



def vocab_gate(html):
    """Refuse to publish wording the section's rules ban. Fail closed - same gate, same
    reason as pages/hub/analysis; these publishers shipped ungated for a month and only
    passed by luck (round-3 plan audit, 2 Aug 2026)."""
    import subprocess as _sp, sys as _s
    r = _sp.run([_s.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                input=html, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit("REFUSED to publish - reword the phrases above and re-run.")

def put(key, html):
    vocab_gate(html)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reason = ("Reports library renders report titles and panel-pack text verbatim - the wording "
              "rules own verbatim-quote exception; Sygma prose says damage throughout")
    assert "$rp$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{key}', $rp${html}$rp$, '{now}') "
        f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
        f"updated_at=EXCLUDED.updated_at;")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    rows = fetch()
    pages = {MK: index_page(rows)}
    for r in rows:
        if r["hosted"]:
            pages[f"{MK}/report-{r['id']}.html"] = report_page(r)

    if a.local:
        os.makedirs(a.local, exist_ok=True)
        for key, html in pages.items():
            p = os.path.join(a.local, key.split("/")[-1] if "/" in key else "index.html")
            open(p, "w").write(html)
        print(f"wrote {len(pages)} pages to {a.local}")
    if a.publish:
        mod = {"module_key": MK, "slug": MK, "title": "Reports & Reviews",
               "section": "Customers", "subsection": "External", "area": "Clancy",
               "tier": "passcode", "passcode": "strive2030",
               # one section, one gate: every Depot page shares this group
               "unlock_group": "clancy-depotnet", "icon": "📄", "accent": "#D50032",
               "status": "live", "enabled": True, "sort": 13,
               "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "reports"]}
        req = urllib.request.Request(f"{URL}/rest/v1/modules?on_conflict=module_key",
            data=json.dumps([mod]).encode(),
            headers={"apikey": SR, "Authorization": f"Bearer {SR}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"}, method="POST")
        _urlopen_retry(req, timeout=60)
        for key, html in pages.items():
            put(key, html)
            print(f"  published {key} ({len(html):,} chars)")
        print(f"published {len(pages)} pages — commandcentre.info/m/{MK}")


if __name__ == "__main__":
    main()
