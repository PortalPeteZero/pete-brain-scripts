#!/usr/bin/env python3
"""
remittance-to-xero.py

Scans Gmail for remittance advice emails, ensures each is forwarded to Xero's
inbox address with a PDF attachment, and labels the thread so it's only
processed once.

Detection heuristics:
  - subject contains "remittance" (case-insensitive)
  - OR sender contains "remittance" (case-insensitive)
  - AND no `Xero-Forwarded` label yet
  - AND newer than 14 days

For each match:
  - if the message has a PDF attachment, forward that PDF to Xero as-is
  - otherwise render the body as a PDF and forward the rendered file
  - add Xero-Forwarded label to the thread (dedup gate)

Run from Cowork scheduled-task or ad-hoc via:
    python3 Library/processes/scripts/remittance-to-xero.py

Env:
    XERO_INBOX_ADDR  optional override for the destination (defaults to Pete's)

Created: 2026-05-12. See [[Library/processes/email-workflow#xero-remittance-auto-forward]].
"""
import importlib.util, base64, re, os, sys, tempfile, json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formatdate

# sibling gmail-api: co-located (scripts/ on the Mac, flat /app on Railway) → resolve from __file__.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
spec = importlib.util.spec_from_file_location('gmail_api', os.path.join(_HERE, 'gmail-api.py'))
gm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gm)

XERO = os.environ.get(
    'XERO_INBOX_ADDR',
    'xero.inbox.!2!zhs.b16ycmse1tlm8n1h@xerofiles.com'
)
PROCESSED_LABEL = 'Xero-Forwarded'
SEARCH_QUERY = '(subject:remittance OR from:*remittance*) -label:Xero-Forwarded newer_than:14d'

def get_or_create_label(g, name):
    for lbl in g.list_labels():
        if lbl.get('name') == name:
            return lbl['id']
    res = g._call('POST', '/labels', body={
        'name': name,
        'labelListVisibility': 'labelHide',  # don't clutter sidebar
        'messageListVisibility': 'show',
        'color': {'backgroundColor': '#16a766', 'textColor': '#ffffff'},
    })
    return res.get('id')

def walk(p):
    if 'parts' in p:
        for sp in p['parts']: yield from walk(sp)
    else:
        yield p

def get_plain_body(payload):
    for p in walk(payload):
        if p.get('mimeType','') == 'text/plain':
            b = p.get('body',{}).get('data')
            if b: return base64.urlsafe_b64decode(b).decode('utf-8', errors='replace')
    for p in walk(payload):
        if p.get('mimeType','') == 'text/html':
            b = p.get('body',{}).get('data')
            if b:
                data = base64.urlsafe_b64decode(b).decode('utf-8', errors='replace')
                txt = re.sub(r'<[^>]+>', '\n', data)
                txt = re.sub(r'\n\s*\n+', '\n\n', txt)
                return txt
    return ''

def find_pdf_attachment(g, msg):
    for p in walk(msg.get('payload', {})):
        mime = p.get('mimeType','') or ''
        fname = p.get('filename','') or ''
        body = p.get('body', {})
        att_id = body.get('attachmentId')
        if att_id and ('pdf' in mime.lower() or fname.lower().endswith('.pdf')):
            return att_id, fname
    return None, None

def body_to_pdf(body_text, headers, thread_id):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    path = tempfile.mktemp(suffix='.pdf')
    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    margin = 50
    y = H - margin
    c.setFont('Helvetica-Bold', 11)
    c.drawString(margin, y, 'Remittance Advice — Email Forward to Xero'); y -= 18
    c.setFont('Helvetica', 9)
    c.drawString(margin, y, f"From: {headers.get('From','')[:120]}"); y -= 12
    c.drawString(margin, y, f"Subject: {headers.get('Subject','')[:120]}"); y -= 12
    c.drawString(margin, y, f"Date: {headers.get('Date','')[:120]}"); y -= 12
    c.drawString(margin, y, f"Gmail thread: {thread_id}"); y -= 20
    c.setStrokeColorRGB(0.6, 0.6, 0.6); c.line(margin, y, W-margin, y); y -= 16
    c.setFont('Helvetica', 9)
    for raw in body_text.splitlines():
        wrapped = simpleSplit(raw, 'Helvetica', 9, W - 2*margin) or ['']
        for w in wrapped:
            if y < margin + 20:
                c.showPage(); c.setFont('Helvetica', 9); y = H - margin
            c.drawString(margin, y, w); y -= 11
        y -= 2
    c.save()
    return path

def sanitise_filename(s):
    s = re.sub(r'[^A-Za-z0-9._-]+', '-', s)
    return s.strip('-').lower()[:80] or 'remittance'

def forward_to_xero(g, msg, thread_id, pdf_bytes, suggested_name):
    headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
    out = MIMEMultipart()
    out['To'] = XERO
    out['From'] = 'pete.ashcroft@sygma-solutions.com'
    out['Subject'] = f"Remittance: {headers.get('Subject','')[:140]}"
    out['Date'] = formatdate(localtime=True)
    out.attach(MIMEText(
        f"Forwarded remittance advice (auto-forwarded by remittance-to-xero cron).\n\n"
        f"From: {headers.get('From','')}\n"
        f"Subject: {headers.get('Subject','')}\n"
        f"Date: {headers.get('Date','')}\n"
        f"Gmail thread: {thread_id}\n",
        'plain'
    ))
    att = MIMEApplication(pdf_bytes, _subtype='pdf')
    att.add_header('Content-Disposition', 'attachment', filename=suggested_name)
    out.attach(att)
    raw = base64.urlsafe_b64encode(out.as_bytes()).decode()
    res = g._call('POST', '/messages/send', body={'raw': raw})
    return res.get('id')

def main():
    g = gm.GmailAPI()
    processed_label = get_or_create_label(g, PROCESSED_LABEL)
    threads = g.search_threads(SEARCH_QUERY, max_results=50)
    if not threads:
        print('No remittance threads pending.')
        return 0
    print(f'Found {len(threads)} candidate thread(s).')
    processed = 0
    skipped = 0
    failed = 0
    for t in threads:
        tid = t.get('id')
        try:
            thread = g.get_thread(tid)
            msg = thread['messages'][-1]
            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
            subj = headers.get('Subject','')
            sender = headers.get('From','')
            print(f"\n  Thread {tid}: {sender[:50]} | {subj[:60]}")

            # Try PDF attachment first
            att_id, att_fname = find_pdf_attachment(g, msg)
            if att_id:
                att = g.get_attachment(msg['id'], att_id)
                pdf_bytes = base64.urlsafe_b64decode(att['data'])
                fname = att_fname or f"{sanitise_filename(subj)}.pdf"
                src = f'PDF attachment ({len(pdf_bytes)} bytes)'
            else:
                body = get_plain_body(msg.get('payload', {}))
                if not body.strip():
                    print('    Skip: no PDF and no body text')
                    skipped += 1
                    continue
                pdf_path = body_to_pdf(body, headers, tid)
                with open(pdf_path, 'rb') as f: pdf_bytes = f.read()
                os.remove(pdf_path)
                fname = f"{sanitise_filename(subj)}.pdf"
                src = f'rendered body ({len(pdf_bytes)} bytes)'

            sent_id = forward_to_xero(g, msg, tid, pdf_bytes, fname)
            g.modify_thread(tid, add=[processed_label])
            print(f"    Forwarded -> Xero (msg {sent_id}, {src}, filename={fname})")
            processed += 1
        except Exception as e:
            print(f"    FAILED: {e}")
            failed += 1

    print(f"\nDone. processed={processed} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2

if __name__ == '__main__':
    sys.exit(main())
