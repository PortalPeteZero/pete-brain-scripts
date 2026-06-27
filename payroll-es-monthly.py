#!/usr/bin/env python3
# CRON-META
# what: Pull each new month's Spanish nóminas (CD + El Atico) from the gestoría, parse + reconcile, store in payroll_es, file the PDF to the private Drive
# why: Keeps the owner-private CD/El Atico payroll sections current automatically — no manual nómina entry; flags any month still missing
# reads: Gmail (nominas@romerodelmas.com + accounts@mvplanzarote.com)
# writes: payroll_es.employee / payroll_es.nomina (+ PDF to CD Private/El Atico drives) + daily_log summary
# entity: finance
# report:
# schedule: 0 9 3,28 * *
# timezone: Atlantic/Canary
# CRON-META-END
import os, sys, re, json, base64, subprocess, datetime, importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, fname))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
gmail = _load("gmailapi", "gmail-api.py")

def sql(q):
    r = subprocess.run([sys.executable, os.path.join(_HERE, "cc-sql.py"), q], capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(r.stderr[:300])
    try: return json.loads(r.stdout) if r.stdout.strip() else []
    except Exception: return []
def drive(*args):
    r = subprocess.run([sys.executable, os.path.join(_HERE, "drive-api.py"), *args], capture_output=True, text=True)
    return r.stdout.strip()

MES_NAME = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}
MES_NUM = {"ENE":1,"FEB":2,"MAR":3,"ABR":4,"MAY":5,"JUN":6,"JUL":7,"AGO":8,"SEP":9,"OCT":10,"NOV":11,"DIC":12}
ENTITY_ACCT = {"CD":"848","Atico":"845"}          # CD = Camello Blanco 848, Atico = El Ático 845
DRIVE_PARENT = {"CD":"1qe6FtygkAUS916EAkmtBU2jN7Mccr_ie", "Atico":"1wxUOH0Wxz3eKrEFCRBn4kF9xr1osE3Ql"}  # CD Private/payroll, El Atico/Finance
PDF_HOME = {"CD":"CD Private/payroll/Nóminas", "Atico":"El Atico/Finance/Nóminas"}

def num(s): return round(float(s.strip().replace(".","").replace(",",".")),2)
def nums(line): return [num(x) for x in re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d{2}", line)]
DNI_RE = re.compile(r"^[0-9XYZ]\d{7}[A-Z]$")
def is_name_row(cols): return len(cols)>=3 and DNI_RE.match(cols[-1] or "") and any(re.match(r"\d{1,2} [A-Z]{3} \d{2}$",c) for c in cols)
def antig_date(s):
    m=re.match(r"(\d{1,2}) ([A-Z]{3}) (\d{2})", s or "")
    return f"20{m.group(3)}-{MES_NUM.get(m.group(2),1):02d}-{int(m.group(1)):02d}" if m else None

def parse_pdf_bytes(data):
    p = subprocess.run(["pdftotext","-layout","-","-"], input=data, capture_output=True)
    txt = p.stdout.decode("utf-8","ignore")
    lines = txt.split("\n")
    # period from the first PERIODO line
    pm = re.search(r"MENS \d{2} ([A-Z]{3}) (\d{2}) a", txt)
    period = f"20{pm.group(2)}-{MES_NUM[pm.group(1)]:02d}-01" if pm else None
    idxs=[i for i,ln in enumerate(lines) if is_name_row([c.strip() for c in re.split(r"\s{2,}",ln.strip()) if c.strip()])]
    rows=[]
    for k,start in enumerate(idxs):
        b=lines[start: idxs[k+1] if k+1<len(idxs) else len(lines)]
        head=[c.strip() for c in re.split(r"\s{2,}",b[0].strip()) if c.strip()]
        name=head[0]; dni=head[-1]
        antig=next((c for c in head if re.match(r"\d{1,2} [A-Z]{3} \d{2}$",c)),None)
        categoria=next((c for c in head[1:-1] if c!=antig),None)
        blob="\n".join(b)
        naf_m=re.search(r"\b(\d{2}/\d{7,8}-\d{2})\b",blob); naf=naf_m.group(1) if naf_m else None
        d_m=re.search(r"a \d{2} [A-Z]{3} \d{2}\s+(\d{1,2})\b",blob); dias=int(d_m.group(1)) if d_m else None
        irpf=0.0; ss_empleado=0.0; concepts=[]
        for ln in b:
            if "COTIZACION" in ln:
                v=nums(ln)
                if not v: continue
                if "I.R.P.F" in ln: irpf=v[-1]
                else: ss_empleado=round(ss_empleado+v[-1],2)
            if any(w in ln for w in ("INDEBID","INDEMNIZ","VACACIONES","FINIQUITO","LIQUIDACION")): concepts.append(ln)
        sal=pex=0.0
        for ln in b:
            v=nums(ln)
            if "*SALARIO BASE" in ln and v: sal=v[0]
            elif "PAGAS EXTRAS" in ln and v: pex=v[0]
        devengado=total_ded=base_irpf=base_ss=printed_liq=coste=None
        for i,ln in enumerate(b):
            if "T. DEVENGADO" in ln and "T. A DEDUCIR" in ln:
                for nl in b[i+1:]:
                    v=nums(nl)
                    if v:
                        total_ded=v[-1]; devengado=v[-2]
                        if len(v)>=3: base_irpf=v[-3]
                        if len(v)>=5: base_ss=v[-5]
                        break
                break
        for ln in b:
            if "LIQUIDO A PERCIBIR" in ln:
                v=nums(ln); printed_liq=v[-1] if v else None
        for ln in b:
            if "COSTE EMPRESA" in ln:
                v=nums(ln)
                if v: coste=v[-1]
        emp_keys=["Contingencias comunes","Mecanismo Equidad","AT y EP","Desempleo","Formación","Fondo Garantía"]
        ss_empresa_check=round(sum(nums(ln)[-1] for ln in b if any(k in ln for k in emp_keys) and nums(ln)),2)
        liquido=round((devengado or 0)-(total_ded or 0),2); ss_empresa=round((coste or 0)-(devengado or 0),2)
        other=round((total_ded or 0)-ss_empleado-irpf,2)
        notes=("finiquito" if any(("INDEMNIZ" in c or "VACACIONES" in c or "FINIQUITO" in c) for c in concepts) else "ajuste") if concepts else None
        rows.append(dict(name=name,dni=dni,naf=naf,categoria=categoria,antiguedad=antig_date(antig),dias=dias,
            devengado=devengado,salario_base=sal or None,pagas_extras=pex or None,irpf=irpf,total_deducido=total_ded,
            ss_empleado=ss_empleado,liquido=liquido,ss_empresa=ss_empresa,coste_empresa=coste,base_ss=base_ss,
            base_irpf=base_irpf,printed_liq=printed_liq,other=other,ss_empresa_check=ss_empresa_check,notes=notes))
    return period, rows

def reconcile(r):
    if None in (r["devengado"],r["total_deducido"],r["coste_empresa"]): return ["missing core"]
    e=[]
    if r["printed_liq"] is not None and abs(r["printed_liq"]-r["liquido"])>0.02: e.append("liq mismatch")
    if abs(r["ss_empleado"]+r["irpf"]+r["other"]-r["total_deducido"])>0.02: e.append("ded parts")
    if r["ss_empresa_check"] and abs(r["ss_empresa"]-r["ss_empresa_check"])>0.15: e.append("ss empresa")
    if r["liquido"]<-0.02 or r["ss_empresa"]<-0.02: e.append("negative")
    return e

def q(v):
    if v is None: return "NULL"
    if isinstance(v,(int,float)): return str(v)
    return "'"+str(v).replace("'","''")+"'"

def find_pdf_part(payload, acct):
    out=[]
    def walk(p):
        for x in p.get("parts",[]) or []:
            fn=x.get("filename","")
            if fn.lower().endswith(".pdf") and x.get("body",{}).get("attachmentId"): out.append((fn, x["body"]["attachmentId"]))
            walk(x)
    walk(payload)
    # prefer the file whose name carries this entity's account number
    for fn,aid in out:
        if acct in fn: return fn,aid
    return None

def fetch_month(cli, entity, year, mon):
    acct=ENTITY_ACCT[entity]; mname=MES_NAME[mon]
    q_str=f'(from:nominas@romerodelmas.com OR from:accounts@mvplanzarote.com) "{mname}" has:attachment newer_than:120d'
    for mm in cli.search_messages(q_str, max_results=40):
        msg=cli.get_message(mm["id"], fmt="full")
        subj=next((h["value"] for h in msg["payload"]["headers"] if h["name"].lower()=="subject"),"")
        if mname.lower() not in subj.lower(): continue
        if "listado" in subj.lower() or "coste" in subj.lower() or "registro" in subj.lower() or "irpf anual" in subj.lower(): continue
        part=find_pdf_part(msg["payload"], acct)
        if not part: continue
        fn,aid=part
        att=cli._call("GET", f"/messages/{mm['id']}/attachments/{aid}")
        data=base64.urlsafe_b64decode(att["data"])
        period,rows=parse_pdf_bytes(data)
        if period != f"{year}-{mon:02d}-01": continue   # wrong month inside — skip
        return data, rows
    return None, None

def upsert(entity, period, rows):
    # ref by DNI, preserving any existing refs for this entity
    existing={r["dni"]:r["ref"] for r in sql(f"SELECT dni, ref FROM payroll_es.employee WHERE entity='{entity}'")}
    maxref=max(list(existing.values())+[0])
    emp_sql=[]; nom_sql=[]
    for r in rows:
        if r["dni"] not in existing: maxref+=1; existing[r["dni"]]=maxref
        ref=existing[r["dni"]]
        emp_sql.append(f"({q(entity)},{ref},{q(r['name'])},{q(r['dni'])},{q(r['naf'])},{q(r['categoria'])},{q(r['antiguedad'])},'active',NULL,{q(r['liquido'])})")
        nom_sql.append("("+",".join([q(entity),str(ref),q(period),q(r['dias']),q(r['devengado']),q(r['salario_base']),q(r['pagas_extras']),
            q(r['irpf']),q(r['total_deducido']),q(r['ss_empleado']),q(r['liquido']),q(r['ss_empresa']),q(r['coste_empresa']),
            q(r['base_ss']),q(r['base_irpf']),"PDF_PATH_PLACEHOLDER",q(r['notes'])])+")")
    return emp_sql, nom_sql, existing

def find_or_create(name, parent):
    out=drive("find-by-name", name, parent)
    m=re.search(r"\b([A-Za-z0-9_-]{25,})\b", out)
    if m: return m.group(1)
    out=drive("create-folder", name, parent)
    m=re.search(r"\b([A-Za-z0-9_-]{25,})\b", out)
    return m.group(1) if m else None

def main():
    today=datetime.date.today(); year=today.year
    cli=gmail.GmailAPI()
    filed=[]; missing=[]; failed=[]
    for entity in ("CD","Atico"):
        have={r["period"][:7] for r in sql(f"SELECT to_char(period,'YYYY-MM-DD') AS period FROM payroll_es.nomina WHERE entity='{entity}' AND extract(year from period)={year}")}
        for mon in range(1, today.month+1):
            key=f"{year}-{mon:02d}"
            if key in have: continue
            data, rows = fetch_month(cli, entity, year, mon)
            if not data: missing.append(f"{entity} {MES_NAME[mon]}"); continue
            errs=[e for r in rows for e in reconcile(r)]
            if errs: failed.append(f"{entity} {MES_NAME[mon]} ({errs[0]})"); continue
            period=f"{key}-01"
            yr_folder=find_or_create(str(year), find_or_create("Nóminas", DRIVE_PARENT[entity]))
            fname=f"Nóminas {entity} {key}.pdf"
            tmp=f"/tmp/{entity}_{key}.pdf"; open(tmp,"wb").write(data)
            up=drive("upload", tmp, yr_folder, fname)
            fid=re.search(r"ID:\s*([A-Za-z0-9_-]{25,})", up)
            pdf_url=f"https://drive.google.com/file/d/{fid.group(1)}/view" if fid else f"{PDF_HOME[entity]}/{year}/{fname}"
            emp_sql, nom_sql, _ = upsert(entity, period, rows)
            nom_sql=[s.replace("PDF_PATH_PLACEHOLDER", q(pdf_url)) for s in nom_sql]
            sql(f"""INSERT INTO payroll_es.employee (entity,ref,full_name,dni,naf,categoria,antiguedad,status,left_on,current_net)
                VALUES {','.join(emp_sql)} ON CONFLICT (entity,ref) DO UPDATE SET full_name=EXCLUDED.full_name,naf=EXCLUDED.naf,
                categoria=EXCLUDED.categoria,current_net=EXCLUDED.current_net,status='active',updated_at=now();""")
            sql(f"""INSERT INTO payroll_es.nomina (entity,ref,period,dias,devengado,salario_base,pagas_extras,irpf,total_deducido,
                ss_empleado,liquido,ss_empresa,coste_empresa,base_ss,base_irpf,pdf_path,notes) VALUES {','.join(nom_sql)}
                ON CONFLICT (entity,ref,period) DO UPDATE SET devengado=EXCLUDED.devengado,irpf=EXCLUDED.irpf,
                total_deducido=EXCLUDED.total_deducido,ss_empleado=EXCLUDED.ss_empleado,liquido=EXCLUDED.liquido,
                ss_empresa=EXCLUDED.ss_empresa,coste_empresa=EXCLUDED.coste_empresa,pdf_path=EXCLUDED.pdf_path,updated_at=now();""")
            filed.append(f"{entity} {MES_NAME[mon]} ({len(rows)} staff)")
    # leavers: mark anyone not in the latest filed month as left (per entity)
    parts=[]
    if filed: parts.append("Filed: "+", ".join(filed))
    if missing: parts.append("Awaiting: "+", ".join(missing))
    if failed: parts.append("⚠ Parse-failed (left for manual review): "+", ".join(failed))
    summary="Payroll-es monthly: "+(" · ".join(parts) if parts else "nothing due — all months on file.")
    print(summary)
    sql(f"INSERT INTO daily_log (date, cron_name, content) VALUES ('{today.isoformat()}', 'payroll-es-monthly', {q(summary)})")
    # raise a CC task only for a previous-month gap past the retry window (day>=3)
    if missing and today.day>=3:
        prev=(today.replace(day=1)-datetime.timedelta(days=1))
        gap=[m for m in missing if MES_NAME[prev.month] in m]
        if gap:
            nm=q("Nómina missing: "+", ".join(gap)+" — chase the gestoría / check both senders")
            sql(f"""INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes)
                SELECT gen_random_uuid(),{nm},'P2',CURRENT_DATE,'Canary Detect','PA-Command-Centre','todo','claude','auto-raised by payroll-es-monthly'
                WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE name={nm} AND status='todo');""")

if __name__=="__main__":
    main()
