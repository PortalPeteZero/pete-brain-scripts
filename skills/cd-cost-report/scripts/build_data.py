"""Distribute Sygma extras into their proper variable buckets (treated as if direct purchase)."""
import sys, json, calendar
from collections import defaultdict
sys.path.insert(0,'.')
import importlib.util
spec=importlib.util.spec_from_file_location('odoo_api','odoo-api.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

FUEL_VENDORS={'Petroleos Marinos de Canarias S.L.','Combustibles Canarios S.A.','Gasib. Soc. Ibérica de Gas Licuado, SLU','DOMARMEN SOCIEDAD LIMITADA','Comercial Fuelanza S.L.'}
RECURRING_MKT={'CANARY ISLAND IMPACT SERVICES SOCIEDAD LIMITADA.','ONBrand Solutions SL','David Gainford'}
QUARTERLY_MKT={'Suelos Secor Floors, SL/Monster Radio'}

BASELINE_START='2026-01-01'
BASELINE_END='2026-04-30'
APRIL_SOCIAL_KNOWN = 2603.21

# Sygma extras with TARGET BUCKET for each (Pete: treat as if purchased direct)
# Casual labour ADDITIONAL (per-period overtime + project-specific, on top of the regular baseline line)
# Composition: €800/mo overtime + €1,733/mo project-specific (Jan-Mar only)
CASUAL_LABOUR_ADDITIONAL = {
    1: 800.00 + 1733.00,  # Jan (overtime + project)
    2: 800.00 + 1733.00,  # Feb
    3: 800.00 + 1733.00,  # Mar
    4: 800.00 + 0.00,     # Apr (overtime only, no project)
}

SYGMA_EXTRAS = {
    2: [
        {'ref':'INV-11144 (trademark portion)','amount':1420.76,'desc':'Trademark registrations (€2,120.76 total, less €700 van insurance)','bucket':'IP / Legal (one-off)'},
        {'ref':'INV-11144 (insurance portion)','amount':700.00,'desc':'Van insurance — annual one-off (split from INV-11144)','bucket':'Vehicle insurance (annual)'},
        {'ref':'INV-11157 line 1','amount':2200.00,'desc':'Cameras for drain inspection (asset)','bucket':'Equipment / capex (one-off)'},
        {'ref':'INV-11157 line 2','amount':700.00,'desc':'INSV consultancy','bucket':'Subcontractors'},
    ],
    3: [
        {'ref':'INV-11375','amount':682.50,'desc':'Consultancy (JW) March','bucket':'Subcontractors'},
        {'ref':'INV-11233','amount':215.68,'desc':'Festool filter bags ×4','bucket':'Materials'},
        {'ref':'INV-11235','amount':600.00,'desc':'Water meters (Water M)','bucket':'LeakGuard hardware (ITransformers + Thingslog customs)'},
        {'ref':'INV-11236','amount':1736.00,'desc':'RM65 Rotamixer for resin terrace','bucket':'Equipment / capex (one-off)'},
        {'ref':'INV-11267','amount':700.00,'desc':'Consultancy (JW)','bucket':'Subcontractors'},
    ],
    4: [
        {'ref':'INV-11428','amount':669.40,'desc':'Consultancy (JW) April','bucket':'Subcontractors'},
        {'ref':'INV-11374','amount':900.00,'desc':'Water meters (WM)','bucket':'LeakGuard hardware (ITransformers + Thingslog customs)'},
    ],
}

def fetch_lines(s,e):
    domain=[['account_id.account_type','in',['expense','expense_direct_cost']],['parent_state','=','posted'],['date','>=',s],['date','<=',e]]
    return m._execute('account.move.line','search_read',[domain,['date','account_id','partner_id','debit','credit']],{'limit':8000})

def by_acct_total(lines):
    a=defaultdict(float)
    for r in lines:
        c=r['account_id'][1].split()[0] if r['account_id'] else 'NONE'
        a[c]+=r['debit']-r['credit']
    return a
def by_partner_in(lines,codes):
    a=defaultdict(float)
    for r in lines:
        c=r['account_id'][1].split()[0] if r['account_id'] else 'NONE'
        if c not in codes: continue
        p=r['partner_id'][1] if r['partner_id'] else '(no partner)'
        a[p]+=r['debit']-r['credit']
    return a

blines = fetch_lines(BASELINE_START, BASELINE_END)
agg = by_acct_total(blines)
sumi = by_partner_in(blines,['628000'])
rec_mkt = sum((r['debit']-r['credit']) for r in blines if r['partner_id'] and r['partner_id'][1] in RECURRING_MKT)
fuel_4 = sum(v for p,v in sumi.items() if p in FUEL_VENDORS)
nonfuel_4 = sum(v for p,v in sumi.items() if p not in FUEL_VENDORS)
alma_4 = sum(v for p,v in by_partner_in(blines,['623000','629000']).items() if 'Perdomo' in p or 'Alma' in p)
alma_avg = alma_4/4 if alma_4 else 180.00
social_avg_4 = (agg.get('642000',0) + APRIL_SOCIAL_KNOWN) / 4

baseline_rows = [
    ('Wages (gross)','640000 4-mo rolling (Jan-Apr 2026)',agg.get('640000',0)/4),
    ('Employer social security','642000 4-mo rolling (Apr from nóminas)',social_avg_4),
    ('Casual labour (regular)','Manual flat €400/wk',1733.00),
    ('Rent (CL Turquía) — net of IGIC','621000 net 4-mo rolling',agg.get('621000',0)/4),
    ('Van rental (3 × €290)','Manual flat (not active Jan-Apr)',0.00),
    ('Sygma intercompany rental','Manual £920 × 1.16 (no invoice raised)',1070.00),
    ('Fuel','628000 fuel subset 4-mo rolling',fuel_4/4),
    ('Utilities non-fuel','628000 non-fuel 4-mo rolling',nonfuel_4/4),
    ('Insurance','625000 4-mo rolling',agg.get('625000',0)/4),
    ('Bank fees','626000 4-mo rolling',agg.get('626000',0)/4),
    ('Asesoría laboral (Alma)','623000/629000 subset',alma_avg),
    ('Software subscriptions','629003 + Odoo SA flat',200.00),
    ('Recurring marketing (Gainford → ONBrand + Gazette)','RECURRING_MKT 4-mo rolling',rec_mkt/4),
    ('Quarterly marketing (Monster Radio)','627000 subset smoothed',200.00),
    ('Other taxes','631000 4-mo rolling',agg.get('631000',0)/4),
]
baseline_total = sum(r[2] for r in baseline_rows)

# Variable buckets — added 'IP / Legal' and 'Equipment / capex (one-off)' as new buckets
# Odoo doesn't currently book to specific accounts for these, so they'd only get Sygma-extra entries (or be added later)
def compute_period(plines, mn):
    buckets_def=[
        ('Materials',['600000'],None),
        ('Other supplies (ex-LeakGuard)',['602000'],lambda p:'ITRANSFORMERS' not in p.upper()),
        ('LeakGuard hardware (ITransformers + Thingslog customs)',['602000','629003'],lambda p:'ITRANSFORMERS' in p.upper() or 'DIRECTRANS' in p.upper()),
        ('Subcontractors',['607000'],None),
        ('Vehicle repairs',['622000'],None),
        ('Property commissions',['623001'],None),
        ('Transport / courier',['624000'],None),
        ('Uniforms',['629001'],None),
        ('One-off marketing',['627000'],lambda p:p not in RECURRING_MKT and p not in QUARTERLY_MKT),
        ('Other services',['629000'],None),
        ('Indemnities + fines',['641000','678001'],None),
        # New buckets — Odoo-empty but can receive Sygma extras
        ('Equipment / capex (one-off)',[],None),
        ('IP / Legal (one-off)',[],None),
        ('Vehicle insurance (annual)',[],None),
    ]
    bucket_data = {}
    for label,codes,pf in buckets_def:
        by_p=defaultdict(float)
        for r in plines:
            c=r['account_id'][1].split()[0] if r['account_id'] else 'NONE'
            if c not in codes: continue
            p=r['partner_id'][1] if r['partner_id'] else '(no partner)'
            if pf and not pf(p): continue
            by_p[p]+=r['debit']-r['credit']
        bucket_data[label] = {'items': dict(by_p), 'total': sum(by_p.values())}

    # Now route Sygma extras into their target buckets
    extras = SYGMA_EXTRAS.get(mn, [])
    for e in extras:
        bk = e['bucket']
        if bk not in bucket_data:
            bucket_data[bk] = {'items': {}, 'total': 0}
        item_label = f"Sygma → CD ({e['ref']}: {e['desc']})"
        bucket_data[bk]['items'][item_label] = bucket_data[bk]['items'].get(item_label, 0) + e['amount']
        bucket_data[bk]['total'] += e['amount']

    out = []
    for label, _, _ in buckets_def:
        v = bucket_data.get(label, {'items':{},'total':0})
        if abs(v['total']) < 0.01: continue
        top = sorted(v['items'].items(), key=lambda x: -x[1])[:6]
        out.append({'label': label, 'total': v['total'], 'top': top})
    return out, sum(b['total'] for b in out)

def fixed_actuals(plines):
    return {
        'wages':sum((r['debit']-r['credit']) for r in plines if r['account_id'] and r['account_id'][1].startswith('640000')),
        'social':sum((r['debit']-r['credit']) for r in plines if r['account_id'] and r['account_id'][1].startswith('642000')),
        'rent':sum((r['debit']-r['credit']) for r in plines if r['account_id'] and r['account_id'][1].startswith('621000')),
        'fuel':sum((r['debit']-r['credit']) for r in plines if r['partner_id'] and r['partner_id'][1] in FUEL_VENDORS),
        'insurance':sum((r['debit']-r['credit']) for r in plines if r['account_id'] and r['account_id'][1].startswith('625000')),
    }

months_data=[]
for mn in [1,2,3,4]:
    end_day=calendar.monthrange(2026,mn)[1]
    pstart,pend=f"2026-{mn:02d}-01",f"2026-{mn:02d}-{end_day:02d}"
    plines=fetch_lines(pstart,pend)
    vr,vt=compute_period(plines, mn)
    fx=fixed_actuals(plines)
    casual = CASUAL_LABOUR_ADDITIONAL.get(mn, 0)
    months_data.append({
        'mnum':mn,'name':calendar.month_name[mn],'period_start':pstart,'period_end':pend,
        'baseline_window':f'{BASELINE_START} → {BASELINE_END}',
        'base_rows':baseline_rows,'base_total':baseline_total,
        'var_rows':vr,'var_total':vt,'fx':fx,
        'sygma_extras':[], 'sygma_extras_total':0,
        'casual_labour':casual,
        'grand':baseline_total+vt+casual,
    })

with open('/tmp/ytd_data.json','w') as f:
    json.dump(months_data,f,indent=2,default=str)

print(f'Baseline: €{baseline_total:,.2f}/mo')
print()
for m in months_data:
    print(f"  {m['name']:9}  Base €{m['base_total']:>9,.2f} + Var €{m['var_total']:>9,.2f} + Cash OT €800 = €{m['grand']+800:>10,.2f}")
