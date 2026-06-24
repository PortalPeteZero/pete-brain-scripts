"""Polished CD cost-base YTD report — v6."""
import json
from datetime import datetime

with open('/tmp/ytd_data.json') as f:
    months = json.load(f)

APRIL_SOCIAL_KNOWN = 2603.21
CASH_OT_MONTHLY = 800.00

def auto_substitute(actual, baseline, line_name):
    if line_name == 'Insurance': return actual, False
    if baseline > 500 and actual < (baseline * 0.3):
        return baseline, True
    return actual, False

for m in months:
    fx = m['fx']
    if m['mnum'] == 4 and fx['social'] < 100:
        fx['social_display'] = APRIL_SOCIAL_KNOWN; fx['social_sub'] = True
    else:
        fx['social_display'] = fx['social']; fx['social_sub'] = False
    fx['wages_display'], fx['wages_sub'] = auto_substitute(fx['wages'], m['base_rows'][0][2], 'Wages')
    fx['rent_display'], fx['rent_sub'] = auto_substitute(fx['rent'], m['base_rows'][3][2], 'Rent')
    fx['fuel_display'], fx['fuel_sub'] = auto_substitute(fx['fuel'], m['base_rows'][6][2], 'Fuel')
    fx['insurance_display'] = fx['insurance']; fx['insurance_sub'] = False
    m['casual_labour'] = m.get('casual_labour', 0)
    m['grand'] = m['base_total'] + m['var_total'] + m.get('sygma_extras_total', 0) + m['casual_labour']

def eur(n): return f'€{n:,.2f}'
def k(n): return f'€{n/1000:.1f}k' if abs(n) >= 1000 else eur(n)

def variance_html(actual, base, sub=False):
    if sub: return '<span class="badge badge-info">used baseline</span>'
    diff = actual - base
    if abs(diff) < 1: return '<span class="muted">≈ baseline</span>'
    pct = abs(diff/base*100) if base else 0
    if diff > 0:
        return f'<span class="var-up">▲ {eur(diff)} <span class="muted-small">({pct:.0f}%)</span></span>'
    return f'<span class="var-down">▼ {eur(abs(diff))} <span class="muted-small">({pct:.0f}%)</span></span>'

ytd_total = sum(m['grand'] for m in months)
ytd_baseline = sum(m['base_total'] for m in months)
ytd_variable = sum(m['var_total'] for m in months)
ytd_casual = sum(m.get('casual_labour', 0) for m in months)
monthly_avg = ytd_total / len(months)
weekly_avg = monthly_avg / 4.33
mb = months[0]['base_total']

tab_buttons = '<button class="tab active" data-page="cover">YTD Summary</button>'
for m in months:
    tab_buttons += f'<button class="tab" data-page="m{m["mnum"]}">{m["name"]}</button>'

def month_page(m):
    base_rows = ''
    for label, source, amt in m['base_rows']:
        base_rows += f'''<tr>
  <td><div class="line-label">{label}</div><div class="line-source">{source}</div></td>
  <td class="num">{eur(amt)}</td>
</tr>'''
    var_rows = ''
    for b in m['var_rows']:
        details = ''
        for v in b['top'][:5]:
            details += f'<div class="vendor-line">{v[0][:60]} <span class="muted-small">· {eur(v[1])}</span></div>'
        var_rows += f'''<tr>
  <td>
    <div class="line-label">{b["label"]}</div>
    <div class="vendor-list">{details}</div>
  </td>
  <td class="num num-bold">{eur(b["total"])}</td>
</tr>'''

    fx = m['fx']
    def sub_chip(was_sub, source=''):
        if not was_sub: return ''
        return f' <span class="chip chip-warn">{source or "baseline used"}</span>'

    var_check_data = [
        ('Wages', fx['wages_display'], 0, fx['wages_sub'], ''),
        ('Employer social security', fx['social_display'], 1, fx['social_sub'], 'from nóminas' if m['mnum']==4 else ''),
        ('Rent', fx['rent_display'], 3, fx['rent_sub'], ''),
        ('Fuel', fx['fuel_display'], 6, fx['fuel_sub'], ''),
        ('Insurance', fx['insurance_display'], 8, fx['insurance_sub'], ''),
    ]
    var_check = ''
    for lbl, actual, base_idx, sub, source in var_check_data:
        base = m['base_rows'][base_idx][2]
        var_check += f'''<tr>
  <td>{lbl}{sub_chip(sub, source)}</td>
  <td class="num">{eur(actual)}</td>
  <td class="num muted">{eur(base)}</td>
  <td class="num">{variance_html(actual, base, sub)}</td>
</tr>'''

    manual_rows = f'<tr><td>Casual Labour Including Overtime</td><td class="num">{eur(m["casual_labour"])}</td></tr>'

    return f'''
<div class="page" id="page-m{m["mnum"]}">
  <div class="headline">
    <div class="headline-label">{m["name"]} 2026 total cost</div>
    <div class="headline-amount">{eur(m["grand"])}</div>
    <div class="headline-pills">
      <span class="pill">Baseline {eur(m["base_total"])}</span>
      <span class="pill">Variable {eur(m["var_total"])}</span>
      <span class="pill">Casual labour (add\'l) {eur(m["casual_labour"])}</span>
      <span class="pill pill-muted">Weekly ≈ {eur(m["grand"]/4.33)}</span>
    </div>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Baseline burn</h2>
        <p class="section-sub">Steady-state monthly need · rolling 4-mo window Jan–Apr 2026</p>
      </div>
      <div class="section-total">{eur(m["base_total"])}</div>
    </div>
    <table class="data-table">
      <thead><tr><th>Line</th><th class="num">€ / month</th></tr></thead>
      <tbody>{base_rows}</tbody>
      <tfoot><tr><td>Baseline total</td><td class="num num-bold">{eur(m["base_total"])}</td></tr></tfoot>
    </table>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Fixed lines · actual vs baseline</h2>
        <p class="section-sub">Where Odoo is missing a fixed line, baseline is substituted (Insurance excepted — known quarterly).</p>
      </div>
    </div>
    <table class="data-table">
      <thead><tr><th>Line</th><th class="num">Actual</th><th class="num">Baseline</th><th class="num">Variance</th></tr></thead>
      <tbody>{var_check}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Manual additions</h2>
        <p class="section-sub">Cash items that don't flow through Odoo</p>
      </div>
    </div>
    <table class="data-table">
      <thead><tr><th>Line</th><th class="num">€</th></tr></thead>
      <tbody>{manual_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Variable / per-period adds</h2>
        <p class="section-sub">Live from Odoo · Sygma intercompany items distributed into proper buckets</p>
      </div>
      <div class="section-total">{eur(m["var_total"])}</div>
    </div>
    <table class="data-table">
      <thead><tr><th>Bucket · top vendors</th><th class="num">€ / month</th></tr></thead>
      <tbody>{var_rows}</tbody>
      <tfoot><tr><td>Variable total</td><td class="num num-bold">{eur(m["var_total"])}</td></tr></tfoot>
    </table>
  </div>

  <div class="grand-bar">
    <div class="grand-label">{m["name"].upper()} 2026 TOTAL</div>
    <div class="grand-amount">{eur(m["grand"])}</div>
  </div>
</div>
'''

# Cover sheet
cover_rows = ''
for m in months:
    cover_rows += f'''<tr>
  <td><strong>{m["name"]}</strong></td>
  <td class="num">{eur(m["base_total"])}</td>
  <td class="num">{eur(m["var_total"])}</td>
  <td class="num">{eur(m["casual_labour"])}</td>
  <td class="num num-bold">{eur(m["grand"])}</td>
</tr>'''

max_g = max(m['grand'] for m in months)
chart_bars = ''
for i, m in enumerate(months):
    bar_pct = (m['grand'] / max_g) * 100
    bar_h = 64
    chart_bars += f'''
<div class="chart-row">
  <div class="chart-label">{m["name"][:3]}</div>
  <div class="chart-track">
    <div class="chart-bar" style="width:{bar_pct}%">
      <span class="chart-bar-text">{eur(m["grand"])}</span>
    </div>
  </div>
</div>'''

cover_html = f'''
<div class="page active" id="page-cover">
  <div class="headline">
    <div class="headline-label">Year-to-Date · Jan–Apr 2026</div>
    <div class="headline-amount">{eur(ytd_total)}</div>
    <div class="headline-pills">
      <span class="pill">Baseline {eur(ytd_baseline)}</span>
      <span class="pill">Variable {eur(ytd_variable)}</span>
      <span class="pill">Casual labour (add'l) {eur(ytd_casual)}</span>
    </div>
  </div>

  <div class="dual-stats">
    <div class="stat-card stat-card-primary">
      <div class="stat-eyebrow">Baseline burn · steady-state</div>
      <div class="stat-row"><span>Monthly</span><strong>{eur(mb)}</strong></div>
      <div class="stat-row"><span>Weekly</span><strong>{eur(mb/4.33)}</strong></div>
      <div class="stat-row"><span>Daily <span class="muted-small">(5-day week)</span></span><strong>{eur(mb/21.67)}</strong></div>
      <div class="stat-footnote">What CD needs every period to keep the lights on — no jobs, no extras.</div>
    </div>
    <div class="stat-card">
      <div class="stat-eyebrow">YTD actual averages · all-in</div>
      <div class="stat-row"><span>Monthly</span><strong>{eur(monthly_avg)}</strong></div>
      <div class="stat-row"><span>Weekly</span><strong>{eur(weekly_avg)}</strong></div>
      <div class="stat-row"><span>Daily <span class="muted-small">(5-day week)</span></span><strong>{eur(monthly_avg/21.67)}</strong></div>
      <div class="stat-footnote">Average of what CD actually spent — baseline + variable + extras + cash.</div>
    </div>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Monthly breakdown</h2>
        <p class="section-sub">Each row totals to the month's grand cost. Click a month tab for the full breakdown.</p>
      </div>
    </div>
    <table class="data-table">
      <thead><tr><th>Month</th><th class="num">Baseline</th><th class="num">Variable</th><th class="num">Casual labour (add'l)</th><th class="num">Total</th></tr></thead>
      <tbody>{cover_rows}</tbody>
      <tfoot><tr><td>YTD</td><td class="num">{eur(ytd_baseline)}</td><td class="num">{eur(ytd_variable)}</td><td class="num">{eur(ytd_casual)}</td><td class="num num-bold">{eur(ytd_total)}</td></tr></tfoot>
    </table>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <h2>Monthly trend</h2>
        <p class="section-sub">Visual comparison of total monthly cost</p>
      </div>
    </div>
    <div class="chart">{chart_bars}</div>
  </div>

  <div class="section section-info">
    <h2>How this report works</h2>
    <div class="info-grid">
      <div class="info-card">
        <div class="info-card-title">Baseline</div>
        <div class="info-card-body">Averaged from the 4 trailing closed months (Jan–Apr 2026). Same baseline applied to every monthly view. Includes wages, social, rent (net), fuel, utilities, insurance, banking, asesoría, software, recurring marketing, taxes, plus regular casual labour €1,733/mo and Sygma rental pencil €1,070.</div>
      </div>
      <div class="info-card">
        <div class="info-card-title">Variable</div>
        <div class="info-card-body">Odoo actuals for that specific month — materials, subcontractors, repairs, hardware imports, etc. Sygma intercompany invoices (cameras, mixer, consultancy, water meters) routed into their natural buckets as if purchased direct.</div>
      </div>
      <div class="info-card">
        <div class="info-card-title">Casual labour (additional)</div>
        <div class="info-card-body">Overtime + project-specific casual workers, on top of the regular casual labour line in baseline. Pete-provided per period.</div>
      </div>
      <div class="info-card">
        <div class="info-card-title">Auto-substitute</div>
        <div class="info-card-body">Where Odoo is missing a fixed line that should be there (e.g. April employer social not yet posted by gestoría), the baseline value is substituted — flagged in the variance column.</div>
      </div>
    </div>
  </div>
</div>
'''

month_pages = ''.join(month_page(m) for m in months)

html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>CD Cost Base — 2026 YTD</title>
<style>
  :root {{
    --navy: #0a2540;
    --navy-light: #14365c;
    --orange: #ef7c1a;
    --orange-light: #fff1e4;
    --bg: #f4f5f7;
    --card: #ffffff;
    --text: #1f2937;
    --text-muted: #6b7280;
    --text-faint: #9ca3af;
    --border: #e5e7eb;
    --border-light: #f3f4f6;
    --success: #10b981;
    --danger: #ef4444;
    --info: #6366f1;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "SF Pro Display", "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    -webkit-font-smoothing: antialiased;
    line-height: 1.5;
  }}
  .wrap {{ max-width: 920px; margin: 0 auto; padding: 32px 20px; }}

  /* Header */
  .header {{
    background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 100%);
    color: white;
    padding: 32px 36px;
    border-radius: 12px 12px 0 0;
    position: relative;
    overflow: hidden;
  }}
  .header::after {{
    content: '';
    position: absolute; right: -40px; top: -40px;
    width: 200px; height: 200px;
    background: radial-gradient(circle, rgba(239,124,26,0.15) 0%, transparent 70%);
    border-radius: 50%;
  }}
  .header h1 {{
    margin: 0; font-size: 26px; font-weight: 700;
    letter-spacing: -0.5px;
  }}
  .header .sub {{
    margin-top: 8px;
    opacity: 0.75;
    font-size: 13px;
    font-weight: 400;
  }}

  /* Tabs */
  .tabs {{
    background: var(--navy);
    padding: 0 20px;
    display: flex;
    gap: 2px;
    flex-wrap: wrap;
    border-bottom: 3px solid var(--orange);
  }}
  .tab {{
    background: transparent;
    color: rgba(255,255,255,0.6);
    padding: 14px 22px;
    cursor: pointer;
    border: none;
    font-size: 13px;
    font-weight: 600;
    font-family: inherit;
    border-radius: 8px 8px 0 0;
    transition: all 0.15s ease;
    letter-spacing: 0.2px;
  }}
  .tab:hover {{
    background: rgba(255,255,255,0.08);
    color: white;
  }}
  .tab.active {{
    background: var(--bg);
    color: var(--navy);
  }}

  /* Card container */
  .card {{
    background: var(--card);
    border-radius: 0 0 12px 12px;
    box-shadow: 0 4px 24px rgba(10,37,64,0.06);
    overflow: hidden;
  }}
  .page {{ display: none; }}
  .page.active {{ display: block; }}

  /* Headline */
  .headline {{
    background: linear-gradient(135deg, var(--orange) 0%, #d96b0a 100%);
    color: white;
    padding: 32px 36px;
    position: relative;
  }}
  .headline-label {{
    font-size: 11px;
    opacity: 0.9;
    text-transform: uppercase;
    letter-spacing: 1.8px;
    font-weight: 600;
    margin-bottom: 8px;
  }}
  .headline-amount {{
    font-size: 42px;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -1.5px;
    font-variant-numeric: tabular-nums;
  }}
  .headline-pills {{
    margin-top: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }}
  .pill {{
    background: rgba(255,255,255,0.18);
    color: white;
    padding: 6px 12px;
    border-radius: 14px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.2px;
    backdrop-filter: blur(4px);
  }}
  .pill-accent {{ background: rgba(255,255,255,0.32); }}
  .pill-muted {{ background: rgba(0,0,0,0.18); }}

  /* Sections */
  .section {{
    padding: 28px 36px;
    border-top: 1px solid var(--border-light);
  }}
  .section:first-child {{ border-top: none; }}
  .section-head {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 18px;
  }}
  .section h2 {{
    font-size: 16px;
    font-weight: 700;
    color: var(--navy);
    margin: 0 0 4px;
    letter-spacing: -0.2px;
  }}
  .section h2::before {{
    content: '';
    display: inline-block;
    width: 3px; height: 16px;
    background: var(--orange);
    margin-right: 10px;
    vertical-align: -2px;
    border-radius: 2px;
  }}
  .section-sub {{
    font-size: 12px;
    color: var(--text-muted);
    margin: 0;
    line-height: 1.5;
  }}
  .section-total {{
    font-size: 18px;
    font-weight: 700;
    color: var(--navy);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }}

  /* Tables */
  .data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  .data-table thead th {{
    text-align: left;
    color: var(--text-muted);
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
  }}
  .data-table tbody td {{
    padding: 11px 14px;
    border-bottom: 1px solid var(--border-light);
    vertical-align: top;
  }}
  .data-table tbody tr:last-child td {{ border-bottom: none; }}
  .data-table tbody tr:hover td {{ background: rgba(239,124,26,0.04); }}
  .data-table tfoot td {{
    padding: 13px 14px;
    border-top: 2px solid var(--navy);
    font-weight: 700;
    font-size: 14px;
    color: var(--navy);
  }}
  .num {{
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }}
  .num-bold {{ font-weight: 700; color: var(--navy); }}
  .muted {{ color: var(--text-muted); }}
  .muted-small {{ color: var(--text-faint); font-size: 11px; }}
  .line-label {{ font-weight: 600; color: var(--text); }}
  .line-source {{ font-size: 11px; color: var(--text-muted); margin-top: 2px; }}
  .vendor-list {{ margin-top: 4px; }}
  .vendor-line {{ font-size: 11px; color: var(--text-muted); line-height: 1.6; }}

  /* Variance indicators */
  .var-up {{ color: var(--danger); font-weight: 600; }}
  .var-down {{ color: var(--success); font-weight: 600; }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3px;
  }}
  .badge-info {{ background: #eef2ff; color: var(--info); }}
  .chip {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3px;
    margin-left: 6px;
  }}
  .chip-warn {{ background: #fef3c7; color: #92400e; }}

  /* Grand total bar */
  .grand-bar {{
    background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 100%);
    color: white;
    padding: 20px 36px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .grand-label {{
    font-size: 11px;
    opacity: 0.7;
    text-transform: uppercase;
    letter-spacing: 1.8px;
    font-weight: 600;
  }}
  .grand-amount {{
    font-size: 24px;
    font-weight: 800;
    letter-spacing: -0.5px;
    font-variant-numeric: tabular-nums;
  }}

  /* Dual stat cards (cover) */
  .dual-stats {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    padding: 28px 36px;
    background: var(--bg);
  }}
  .stat-card {{
    background: var(--card);
    border-radius: 10px;
    padding: 22px;
    border: 1px solid var(--border);
  }}
  .stat-card-primary {{
    background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%);
    border-color: var(--orange);
  }}
  .stat-eyebrow {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-weight: 700;
    color: var(--text-muted);
    margin-bottom: 14px;
  }}
  .stat-card-primary .stat-eyebrow {{ color: var(--orange); }}
  .stat-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 6px 0;
    border-bottom: 1px solid var(--border-light);
    font-size: 13px;
  }}
  .stat-row:last-of-type {{ border-bottom: none; }}
  .stat-row strong {{
    font-size: 18px;
    font-weight: 700;
    color: var(--navy);
    font-variant-numeric: tabular-nums;
  }}
  .stat-footnote {{
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border-light);
    font-size: 11px;
    color: var(--text-muted);
    line-height: 1.6;
  }}

  /* Chart */
  .chart {{ margin-top: 4px; }}
  .chart-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }}
  .chart-label {{
    width: 40px;
    font-size: 12px;
    font-weight: 700;
    color: var(--navy);
    text-transform: uppercase;
  }}
  .chart-track {{
    flex: 1;
    background: var(--border-light);
    border-radius: 6px;
    overflow: hidden;
    height: 32px;
    position: relative;
  }}
  .chart-bar {{
    background: linear-gradient(90deg, var(--orange) 0%, #d96b0a 100%);
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 0 12px;
    border-radius: 6px;
  }}
  .chart-bar-text {{
    color: white;
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}

  /* Info cards */
  .section-info {{ background: var(--bg); }}
  .info-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 16px;
  }}
  .info-card {{
    background: white;
    border-radius: 8px;
    padding: 16px 18px;
    border: 1px solid var(--border);
  }}
  .info-card-title {{
    font-size: 12px;
    font-weight: 700;
    color: var(--navy);
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .info-card-body {{
    font-size: 12px;
    color: var(--text-muted);
    line-height: 1.6;
  }}

  /* Footer */
  .footer {{
    padding: 18px 36px;
    font-size: 11px;
    color: var(--text-faint);
    background: white;
    border-radius: 0 0 12px 12px;
    border-top: 1px solid var(--border-light);
  }}

  @media (max-width: 640px) {{
    .dual-stats {{ grid-template-columns: 1fr; }}
    .info-grid {{ grid-template-columns: 1fr; }}
    .headline-amount {{ font-size: 32px; }}
    .section, .headline, .grand-bar, .header {{ padding-left: 20px; padding-right: 20px; }}
  }}
</style>
</head><body>
<div class="wrap">
  <div class="header">
    <h1>CD Cost Base · 2026 Year-to-Date</h1>
    <div class="sub">Camello Blanco SL · Jan–Apr 2026 (May excluded) · generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
  </div>
  <div class="tabs">{tab_buttons}</div>
  <div class="card">{cover_html}{month_pages}</div>
  <div class="footer">Source · Odoo (camello-blanco-sl.odoo.com) + Xero (Sygma intercompany) · Baseline config · Businesses/canary-detect/finance/cost-base-reports/baseline-config.md</div>
</div>
<script>
document.querySelectorAll('.tab').forEach(t => {{
  t.addEventListener('click', function() {{
    const key = this.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + key).classList.add('active');
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    this.classList.add('active');
    window.scrollTo({{ top: 0, behavior: 'smooth' }});
  }});
}});
</script>
</body></html>
'''

import os
VAULT_ROOT = os.environ.get("VAULT_ROOT", "/tmp/pbs")
OUT = os.path.join(VAULT_ROOT, "Businesses/canary-detect/finance/cost-base-reports/2026-cost-base-YTD.html")
with open(OUT, 'w') as f:
    f.write(html)
print(f'Saved: {OUT}')
print(f'YTD: {eur(ytd_total)} | Monthly avg: {eur(monthly_avg)} | Weekly avg: {eur(weekly_avg)}')
print(f'Baseline monthly: {eur(mb)} | Baseline weekly: {eur(mb/4.33)}')
