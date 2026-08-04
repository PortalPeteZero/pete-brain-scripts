#!/usr/bin/env python3
"""
recon-sheet.py -- the Bank Reconciliation Queries sheet: the replacement for Xero's "Discuss" tab.

what: Turns Xero "Bank Reconciliation" exports into a live, colour-coded Google Sheet with one tab
      per bank account, every unreconciled line identified (what the merchant IS, not just its
      name), categorised against Sygma's OWN chart of accounts, and split net / VAT so Andy does
      not have to ask "is there VAT on this?".
why:  Xero exposes NO API for unreconciled statement lines or the Discuss comments -- permanently,
      by policy (see [[xero-api-capability]]). So the conversation with the bookkeeper moves to a
      sheet we DO control. Built 4 Aug 2026 on Pete's design.

Re-running is the point. Lines that have since been reconciled disappear from the Xero export, so
they are marked DONE and kept for history rather than deleted -- the sheet is a record of what was
answered, not just a to-do list.

Usage:
  python3 recon-sheet.py build  ~/Downloads/Sygma_Solutions_-_Bank_Reconciliation*.xlsx
  python3 recon-sheet.py build  <files...> --sheet SHEET_ID     # update an existing sheet
"""
import sys, os, re, json, glob, zipfile, html, datetime, subprocess
import urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
VAT_RATE = 0.20

# ---------------------------------------------------------------------------
# Sygma's OWN account names. Never invent a category -- Andy codes against these.
# The rule for each is "which account has Pete actually used for this before".
# ---------------------------------------------------------------------------
ACCOUNTS = {
    "8201": "Subscriptions", "7505": "AMAZON", "7402": "Hotels & Campsites",
    "7400": "Travelling", "7400C": "Flight - Travel", "7400D": "Toll - Travel",
    "7400E": "Parking - Travel", "7400B": "Taxi - Travel", "7405": "Overseas Travelling",
    "7406": "Subsistence", "7300": "Fuel and Oil", "7504": "Office Stationery",
    "7800": "Repairs and Renewals", "8203": "Staff Training Costs", "6201": "Advertising",
    "5001": "Venue/room/facility hire", "7100": "Rent", "7502": "Telephone",
    "6900": "Miscellaneous Expenses", "2301": "Directors Loan Account",
    "5000a": "Parts purchased", "7501": "Postage and Carriage", "7601": "Audit and Accountancy Fees",
    "7603": "Professional & Consultancy Fees", "8202": "Clothing Costs", "5100": "Carriage",
}

# What a merchant IS -- Pete's words: "I'll see a PayPal description and wonder what it is."
# (pattern, plain-English explanation, account code, vat_treatment)
#   vat: "std"  -> VAT reclaimable, VAT = gross/6. THIS IS THE DEFAULT for everything.
#        "none" -> NO VAT, and there must be a stated reason (Pete, 4 Aug 2026: "should default
#                  always to yes unless i specifically say no"). Reasons that qualify: zero-rated
#                  (flights, most cold food, rail), outside the scope (foreign supplier, statutory
#                  fee), exempt (insurance, finance), or not a business cost at all.
KNOWN = [
    (r"ANTHROPIC",            "Claude AI subscription", "8201", "std"),
    (r"OPENAI|CHATGPT",       "ChatGPT / OpenAI AI subscription",                    "8201", "std"),
    (r"GROK|XAI",             "Grok AI subscription (xAI)",                          "8201", "std"),
    (r"GITHUB",               "GitHub code hosting subscription",                    "8201", "std"),
    (r"VERCEL",               "Vercel website hosting",                              "8201", "std"),
    (r"CLOUDTALK",            "CloudTalk cloud phone system",                        "7502", "std"),
    (r"IONOS",                "IONOS web hosting / domains",                         "8201", "std"),
    (r"GODADDY",              "GoDaddy domains, SSL and Microsoft 365 licences",     "8201", "std"),
    (r"GOOGLE CLOUD|GOOGLE WORKSPACE|GSUITE",
                              "Google Workspace email and storage",                  "8201", "std"),
    (r"MICROSOFT",            "Microsoft subscription",                              "8201", "std"),
    (r"CANVA",                "Canva design software subscription",                  "8201", "std"),
    (r"ASANA",                "Asana project management subscription",               "8201", "std"),
    (r"LINKEDIN|LINKD",       "LinkedIn advertising / recruiter",                    "6201", "std"),
    (r"MATTERPORT|MATTER SOFTWARE",
                              "Matterport 3D site scanning subscription",            "8201", "std"),
    (r"MERGIN MAPS",          "Mergin Maps GIS field-survey subscription",           "8201", "std"),
    (r"BLOTATO",              "Blotato social media scheduling tool",                "8201", "std"),
    (r"STARLINK",             "Starlink satellite internet",                         "8201", "std"),
    (r"PADDLE|RUNNA",         "Paddle billing (Runna running app) -- looks PERSONAL, check", "2301", "std"),
    (r"PLAYSTATION|XBOX|NINTENDO",
                              "Games subscription -- PERSONAL, not a business cost",  "2301", "none"),
    (r"APPLE",                "Apple services (iCloud / App Store)",                 "8201", "std"),
    (r"AMAZON|AMZN",          "Amazon purchase",                                     "7505", "std"),
    # SETTLED, do not re-query: Sygma claims VAT on flights. Pete, 4 Aug 2026 -- "already sorted,
    # we claim vat on it". Raised once, answered, closed.
    (r"RYANAIR|EASYJET|JET2|IBERIA|BRITISH AIRWAYS|BINTER|VUELING",
                              "Flight",                                              "7400C", "std"),
    (r"BOOKING\.?COM|BKG\*|HOTELS\.COM|HOTELCOM|PREMIER INN|HOLIDAY INN|TRAVELODGE|IBIS",
                              "Hotel",                                               "7402", "std"),
    (r"UBER|UBR\*|BOLT\.EU",  "Taxi",                                                "7400B", "std"),
    (r"NCP|PARKING|APCOA|AER\.PARKING",
                              "Parking",                                             "7400E", "std"),
    (r"M6 TOLL|MERSEYFLOW|DARTFORD",
                              "Road toll",                                           "7400D", "std"),
    (r"TRAINLINE|LNER|AVANTI|NORTHERN RAIL|TFL",
                              "Train / public transport",                            "7400A", "none"),
    (r"TESCO PFS|MOTO |MFG |SHELL|BP |ESSO|SPENCERS GARAGE|SERVICE ST|ESTACION",
                              "Fuel",                                                "7300", "std"),
    (r"TESCO|SAINSBURY|ASDA|MORRISON|CO-OP|BUDGENS|ALDI|LIDL|MARKS|GREGGS|COSTA|MCDONALD|SUBWAY|PRET",
                              "Food / subsistence (cold food is zero-rated -- check receipt)", "7406", "std"),
    (r"SCREWFIX|TOOLSTATION|B&Q|WICKES",
                              "Tools and hardware",                                  "7800", "std"),
    (r"COMPANIESHOUSE|COMPANIES HOUSE",
                              "Companies House filing fee",                          "6900", "none"),
    (r"HUMANFOCUS",           "Human Focus online training -- bought while testing online "
                              "course providers (Pete confirmed)",                   "8203", "std"),
    (r"BEACCREDITED",         "BeAccredited online training -- same online-course testing exercise",
                                                                                     "8203", "std"),
    (r"NORTHUMBRIA UNIVERSITY",
                              "Northumbria University -- training / accreditation",  "8203", "std"),
    (r"DHL|UPS|FEDEX|PARCELFORCE|ROYAL MAIL",
                              "Courier / carriage",                                  "5100", "std"),
    # --- learned 4 Aug 2026: Pete told me, or looked up ---
    (r"MB TECH",              "MB Tech Warrington -- car servicing (Pete confirmed)", "7301A", "std"),
    (r"F\.?P\.?SMITH|FP SMITH",
                              "F.P. Smith -- car servicing (Pete confirmed)",        "7301A", "std"),
    (r"BUSINESS SPACE SOLUTIONS",
                              "Business Space Solutions -- extra board room hire (Pete confirmed)",
                                                                                     "7100", "std"),
    (r"EMBELLO",              "Embello, Tamworth -- printing, signage and branded workwear",
                                                                                     "7500", "std"),
    (r"INFINITE EVOLUTION",   "Infinite Evolution -- staff training (Pete confirmed). NOTE: no bill "
                              "for this in Xero, their last 3 are all paid -- likely still in Dext",
                                                                                     "8203", "std"),
    # our own stack -- these are in the CC's own connector registry
    (r"AHREFS",               "Ahrefs SEO tool (we use it for the websites)",        "8201", "std"),
    (r"SURFERSEO|SURFER SEO", "Surfer SEO content tool (we use it)",                 "8201", "std"),
    (r"RECRAFT",              "Recraft AI image generation (we use it)",             "8201", "std"),
    (r"SUPABASE",             "Supabase -- the Command Centre database",             "8201", "std"),
    (r"RAILWAY",              "Railway -- runs the Command Centre automations",      "8201", "std"),
    (r"PASSKIT",              "PassKit digital wallet passes (the Wallet Pass work)", "8201", "std"),
    (r"SCRIBE\.HOW",          "Scribe -- how-to documentation tool",                 "8201", "std"),
    (r"LETAIDO",              "Letaido -- Ahrefs' AI marketing workspace, USD 118.80/mo on card 8325 "
                              "(found via the Link/Stripe receipt in Gmail)",        "8201", "std"),
    (r"TASKLET",              "Tasklet -- software subscription (Pete confirmed)",   "8201", "std"),
    (r"CLOUDINARY",           "Cloudinary image hosting",                            "8201", "std"),
    (r"OPENROUTER|PERPLEXITY|MIDJOURNEY|ELEVENLABS",
                              "AI tool subscription",                                "8201", "std"),
    # vehicles and premises
    (r"BLACKCIRCLES|KWIK.?FIT|NATIONAL TYRES",
                              "Tyres / vehicle servicing",                           "7301A", "std"),
    (r"ALTITUDEFS|ALTITUDE FS|ALTITUDE FUNDING|LATITUDE",
                              "Altitude Funding Solutions -- GBP 282 vehicle finance BROKER ADMIN "
                              "FEE for Latitude Leasing, inc VAT, one per deal (10224 = second Ford "
                              "Transit YM26EGK, 10195 = the other). Collected by GoCardless, NOT "
                              "Time Token",                                          "7401", "std"),
    (r"TIME TOKEN",           "Time Token -- rent on the business centre (Pete confirmed)",
                                                                                     "7100", "std"),
    (r"GSY GAS",              "Gas supply",                                          "7201", "std"),
    (r"B ?& ?Q|WICKES|HOMEBASE",
                              "Building / hardware supplies",                        "7800", "std"),
    # money movements -- NOT expenses, do not code as spend
    (r"PAYMENT MADE|VIRTUALBANKTRANSFER|OPENBANKING|BANK TRANSFER|TRANSFER FROM|TRANSFER TO",
                              "Transfer between our own accounts / card repayment -- NOT a cost",
                                                                                     "", "none"),
    (r"FP RETURN",            "DLA refund -- returned payment, reconciles against the matching DLA "
                              "payment out (Pete confirmed)",                        "2301", "none"),
    # ⚠ GoCardless is a payment RAIL, not a supplier. "GOCARDLESS APPEARONLINE-652XJ" is Appear
    # Online being collected by direct debit. Coding it to GoCardless with no VAT was matching the
    # mechanism instead of who was actually paid, and it lost the VAT (Pete, 4 Aug 2026). The
    # specific payees are listed FIRST so they win; the bare rail is only a fallback.
    (r"APPEARONLINE|APPEAR ONLINE",
                              "Appear Online -- backlink SEO work on the website (collected by "
                              "GoCardless direct debit)",                            "6201", "std"),
    (r"CONTROLACCOUNT|FEDEX",
                              "Controlaccount / FedEx payment plan -- these are paying off the FEDEX "
                              "VAT BILL, so the VAT should be claimed. Andy: worth checking how this "
                              "has been treated so far (Pete, 4 Aug 2026)",          "6900", "std"),
    (r"ANDY JONES",           "Andy Jones -- our bookkeeper",                        "7601", "none"),
    (r"MR HAMILTON|HAMILTON.*TIK HUNT",
                              "Staff loan to Hamilton (Pete confirmed) -- a loan, not an expense "
                              "and no VAT",                                          "", "none"),
    (r"CANARY DETECT",        "Canary Detect -- our own other company (intercompany)", "", "none"),
    # travel odds and ends
    (r"TRANSFEERO",           "Transfeero airport transfers",                        "7400", "std"),
    (r"SUPER\.COM",           "Super.com travel booking",                            "7402", "std"),
    (r"NORWEGIAN CRUISE",     "Norwegian Cruise Line -- looks PERSONAL, check",      "2301", "none"),
    (r"FARMACIA|CRV\*FARMACIA",
                              "Spanish pharmacy -- NO UK VAT",                       "6900", "none"),
    (r"MANSFIELD RD|WESTMINSTER HO|CITY CO-IPS|NYX\*PETROGAS",
                              "Parking / small local charge",                        "7400E", "std"),
    (r"HURAK",                "Hurak training courses",                              "8203", "std"),
    (r"TEXIM EUROPE",         "Texim Europe -- Dutch electronics component distributor (EU, check VAT)",
                                                                                     "5000a", "none"),
    (r"CURRY CLUB",           "Curry Club -- food",                                  "7406", "std"),
    # Pete's calls, 4 Aug 2026 -- personal spend on the company card goes to the director's loan
    (r"AYUNTAMIENTO",         "Ayuntamiento de Yaiza (Spanish council) -- Director's Loan",
                                                                                     "2301", "none"),
    (r"NORWEGIAN CRUISE",     "Norwegian Cruise Line -- Director's Loan",            "2301", "none"),
    (r"SUPPS HUB|SUPPSHUB",   "Supps Hub, Dubai -- Director's Loan",                 "2301", "none"),
    (r"KSUPPLIESDECCO",       "K Supplies Decco -- Director's Loan",                 "2301", "none"),
    (r"C C SOCO|SOCO PUERTO", "Parking, Puerto del Carmen (Spain) -- Director's Loan", "2301", "none"),
    (r"FARMACIA",             "Spanish pharmacy -- Director's Loan",                 "2301", "none"),
    (r"TEXIM EUROPE",         "Texim Europe BV -- Dutch electronics distributor, parts. Receipt IS "
                              "on Capital on Tap (Paul Baxter, 11 Jun)",             "5000a", "none"),
    (r"GOOGLE ADS|GOOGLE ADS1",
                              "Google Ads -- website advertising spend",             "6201", "std"),
    (r"FASTFIELD",            "FastField Mobile Forms -- site survey forms app",     "8201", "std"),
    (r"LOVABLE",              "Lovable -- AI app builder (hosts Sygma Mala and Sales-Hire)",
                                                                                     "8201", "std"),
    (r"TIDIO",                "Tidio -- website live-chat widget",                   "8201", "std"),
    (r"COOKIEYES",            "CookieYes -- website cookie consent banner",          "8201", "std"),
    (r"HOLO AI",              "Holo AI -- AI subscription",                          "8201", "std"),
    (r"PLAUD",                "Plaud -- AI voice recorder and meeting transcription", "8201", "std"),
    (r"XRO |XERO CUSTOM CONNE",
                              "Xero custom connections -- the API app fee",          "8201", "std"),
    (r"RESEND",               "Resend -- transactional email service",               "8201", "std"),
    (r"AI ON WHATSAPP",       "AI on WhatsApp -- AI subscription",                   "8201", "std"),
    (r"SKOOLCOM|SKOOL\.COM",  "Skool -- online community platform",                  "8201", "std"),
    (r"COLCHONE",             "Colchones (Spanish mattress retailer) -- Director's Loan",
                                                                                     "2301", "none"),
    (r"SUMINISTROS|PEDIDO",   "Spanish supplier -- NO UK VAT to reclaim",            "6900", "none"),
]


# Line-level overrides, keyed (tab, date, amount). Needed because a merchant rule cannot express
# "Suministros JL Cabrera is TOOLS at GBP 882.94 on the card but the DIRECTOR'S LOAN at GBP 29.66 on
# Natwest" -- which is exactly what Pete said on 4 Aug 2026. Marking the individual record beats
# bending the rule until it fits.
OVERRIDES = {
    ("Capital On Tap", "2026-06-06", -882.94):
        ("Suministros JL Cabrera -- tools for work (Pete confirmed)", "7800", "none"),
    ("Natwest Business Account", "2026-05-29", -29.66):
        ("Suministros JL Cabrera -- Director's Loan (Pete confirmed)", "2301", "none"),
    ("Natwest Business Account", "2026-06-01", -15.05):
        ("Suministros JL Cabrera -- Director's Loan (Pete confirmed)", "2301", "none"),
    ("Natwest Business Account", "2026-06-29", -838.32):
        ("Flight, PayPal booking ref RJPTJM (Pete confirmed)", "7400C", "std"),
    ("Natwest Business Account", "2026-07-22", -362.92):
        ("Flight, PayPal booking ref WWNTMA (Pete confirmed)", "7400C", "std"),
    ("Natwest Business Account", "2026-07-08", -206.53):
        ("Iberia flight (PayPal: purchase at IBERIA LAE SA)", "7400C", "std"),
    ("Natwest Business Account", "2026-06-15", -82.64):
        ("Iberia flight (Pete confirmed)", "7400C", "std"),
}


def _die(m, c=1):
    print(m, file=sys.stderr); sys.exit(c)


def sheets_token():
    """Reuse sheets-api.py's service-account auth rather than re-deriving it."""
    sys.path.insert(0, VAULT)
    import importlib.util
    spec = importlib.util.spec_from_file_location("sheets_api", os.path.join(VAULT, "sheets-api.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.get_token()


def sapi(method, path, body=None, tok=None):
    tok = tok or sheets_token()
    req = urllib.request.Request(f"{SHEETS}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method=method)
    try:
        r = urllib.request.urlopen(req).read()
        return json.loads(r) if r else {}
    except urllib.error.HTTPError as e:
        _die(f"sheets {method} {path} failed {e.code}: {e.read().decode()[:400]}")


# ---------------------------------------------------------------------------
# Parsing Xero's "Bank Reconciliation" export
# ---------------------------------------------------------------------------
def parse_export(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        s = z.read("xl/sharedStrings.xml").decode("utf8")
        shared = [html.unescape(re.sub("<[^>]+>", "", m))
                  for m in re.findall(r"<si>(.*?)</si>", s, re.S)]
    rows = re.findall(r"<row[^>]*>(.*?)</row>",
                      z.read("xl/worksheets/sheet1.xml").decode("utf8"), re.S)
    recs = []
    for r in rows:
        v = {}
        for col, attrs, val in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(?:<v>(.*?)</v>)?', r):
            if val is None: continue
            if 't="s"' in attrs:
                try: val = shared[int(val)]
                except (ValueError, IndexError): pass
            v[col] = val
        recs.append(v)

    def isnum(x):
        try: float(x); return True
        except (TypeError, ValueError): return False

    account = recs[3].get("A", "Unknown") if len(recs) > 3 else "Unknown"
    # The unreconciled list is bounded by its own headings -- do NOT just take every numeric row,
    # the Totals Summary and Statement Balances sections carry numbers too (that mistake produced
    # 61 "lines" for an account that has 57).
    try:
        start = next(i for i, v in enumerate(recs)
                     if v.get("A") == "Plus Unreconciled Statement Lines") + 1
        end = next(i for i, v in enumerate(recs)
                   if v.get("A") == "Total Unreconciled Statement Lines")
    except StopIteration:
        return account, []
    def d(v): return (datetime.date(1899, 12, 30) + datetime.timedelta(days=float(v))).isoformat()
    return account, [{"date": d(v["A"]),
                      "ref": (v.get("C") or v.get("B") or "").strip(),
                      "amount": float(v["D"])}
                     for v in recs[start:end] if isnum(v.get("A")) and isnum(v.get("D"))]


def merchant(ref):
    """Both Xero's card feed and the reconciliation export use 'MERCHANT - PLACE - Card Ending: N'."""
    t = (ref or "").split(" - ")[0].strip()
    t = re.sub(r"\s{2,}.*$", "", t)              # Natwest style: 'MERCHANT   extra ref'
    return re.sub(r"[^A-Za-z0-9 &.'*/-]", "", t).strip().upper()[:40]


def classify(ref, payee=""):
    hay = f"{payee} {ref}".upper()
    for pat, what, acct, vat in KNOWN:
        if re.search(pat, hay):
            return what, acct, vat
    return "", "", "std"   # default YES to VAT -- Pete, 4 Aug 2026


def vat_split(gross, treatment):
    """Default is that VAT IS reclaimable. 'none' needs a stated reason -- see KNOWN.

    ⚠ MONEY IN IS NEVER A PURCHASE. A positive amount is a receipt, refund or transfer INTO the
    account, so there is no input VAT to reclaim on it -- whatever the merchant name suggests. The
    caller must not even reach here with a positive amount expecting a split; is_money_in() is
    checked first in build_rows().

    This is a sign check rather than another pattern because patterns kept missing: the rule
    "PAYMENT MADE OPENBANKING" never fired against the real text "Payment made (OpenBanking)", and
    GBP 4,063.79 of input VAT was invented across 8 lines before Pete spotted it (4 Aug 2026).
    """
    g = abs(gross)
    if treatment == "none":
        return g, 0.0
    vat = round(g * VAT_RATE / (1 + VAT_RATE), 2)
    return round(g - vat, 2), vat


def load_card_export(paths):
    """Capital on Tap's OWN transaction export, if Pete has downloaded one.

    Worth having because it carries three things Xero's bank feed does not: a clean Merchant Name
    (rather than 'MERCHANT - PLACE - Card Ending: NNNN'), the CARDHOLDER, and whether a RECEIPT is
    attached -- which is the difference between "claim the VAT" and "claim it and hope". Keyed on
    date + amount, which is unique enough in practice.
    """
    import csv as _csv
    out = {}
    for p in paths:
        if not p.lower().endswith(".csv"):
            continue
        try:
            for r in _csv.DictReader(open(p, encoding="utf-8-sig")):
                try:
                    d = datetime.datetime.strptime(r["Clearance Date"], "%d/%m/%Y").date().isoformat()
                    amt = round(abs(float(r["Amount"])), 2)
                except (KeyError, ValueError):
                    continue
                out[(d, amt)] = {"merchant": (r.get("Merchant Name") or "").strip(),
                                 "who": (r.get("Cardholder Name") or "").strip(),
                                 "receipt": (r.get("Has Receipts") or "").strip(),
                                 "cat": (r.get("Category") or "").strip()}
        except OSError:
            pass
    return out


def _tokens(s):
    return {w for w in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if len(w) > 2
            and w not in {"LTD","LIMITED","THE","AND","UK","COM","PLC","INC","CARD","ENDING"}}


def match_bills(amount, date, bills, merchant_text="", window=95):
    """Is there an OPEN bill in Xero this bank line pays off?

    Pete, 4 Aug 2026: "always check if there is a matching outstanding invoice when we run this."

    Checks one bill, then PAIRS from the same supplier -- a single card payment routinely clears two
    bills at once (MB Tech Warrington: 034594 GBP 2,018.58 + 034355 GBP 276.00 = the GBP 2,294.58 on
    the card, which one-to-one matching missed entirely).

    ⚠ Amount alone coincides often enough to be dangerous: an Anthropic GBP 75 "matched" two
    Business Space Solutions bills on the first run. So the supplier name is scored too, and a match
    with no name overlap is labelled "possible?" rather than presented as fact. Andy acting on a
    false match is worse than no match at all.
    """
    D = datetime.date.fromisoformat
    amt = abs(amount)
    mt = _tokens(merchant_text)
    near = [b for b in bills if b.get("due_amt") and b.get("date")
            and -window <= (D(date) - D(b["date"])).days <= window]

    def label(b, extra=""):
        return f"{b['contact'][:26]} {b['number']}{extra} ({b['date']})"

    # NAME MUST AGREE. Amount alone coincides constantly and the near-misses were actively
    # misleading -- an Anthropic GBP 75 "matched" two Business Space Solutions bills, and an
    # Altitude vehicle-finance payment "matched" a Time Token bill, which Pete had to correct.
    # A blank is better than a wrong lead: Andy would chase it.
    named = [b for b in near if (mt & _tokens(b["contact"]))
             and abs(abs(b["due_amt"]) - amt) < 0.005]
    if named:
        return label(named[0])
    for i, b1 in enumerate(near):
        for b2 in near[i+1:]:
            if b1["contact"] == b2["contact"] and (mt & _tokens(b1["contact"])) and \
               abs(abs(b1["due_amt"]) + abs(b2["due_amt"]) - amt) < 0.005:
                return label(b1, f" + {b2['number']} (2 bills)")
    return ""


def build_rows(lines, precedent, paypal, bills=(), card=None, acct_name=""):
    D = datetime.date.fromisoformat
    out = []
    for l in sorted(lines, key=lambda x: x["date"]):
        payee = whatpp = ""
        if "PAYPAL" in l["ref"].upper() and paypal:
            c = [p for p in paypal if p["gbp"] is not None
                 and abs(p["gbp"] - abs(l["amount"])) < 0.005
                 and 0 <= (D(l["date"]) - D(p["date"])).days <= 10]
            if c:
                p = min(c, key=lambda p: (D(l["date"]) - D(p["date"])).days)
                payee, whatpp = p["who"] or "", p["what"] or ""
        cinfo = (card or {}).get((l["date"], round(abs(l["amount"]), 2)), {})
        if cinfo.get("merchant") and not payee:
            payee = cinfo["merchant"]
        ov = OVERRIDES.get((acct_name, l["date"], round(l["amount"], 2)))
        money_in = l["amount"] > 0
        what, acct, vat = classify(l["ref"], payee)
        if money_in:
            # a receipt / refund / transfer in -- never an expense, never input VAT
            vat = "none"
            if not what:
                what = "MONEY IN -- receipt, refund or transfer into the account"
            acct = acct if acct in ("", None) else ""
        m = merchant(payee or l["ref"])
        prev = precedent.get(m)
        if prev and not acct:                      # how Pete has coded this merchant before
            acct = prev[0] or ""
            vat = "std" if prev[1] == "INPUT2" else ("none" if prev[1] else vat)
        if ov:
            what, acct, vat = ov          # an explicit call from Pete beats every rule
        net, vatamt = vat_split(l["amount"], vat)
        reason = ("MONEY IN -- not a purchase, no input VAT" if money_in
                  else (what if vat == "none" else ""))
        desc = " / ".join(x for x in [payee, whatpp] if x) or merchant(l["ref"])
        out.append([l["date"], l["ref"][:70], round(l["amount"], 2),
                    "N" if vat == "none" else "Y", net, vatamt,
                    desc[:60], what[:110], ACCOUNTS.get(acct, acct or ""),
                    match_bills(l["amount"], l["date"], bills, payee or l["ref"]),
                    cinfo.get("who", ""),
                    reason if vat == "none" else "", "OPEN", ""])
    return out


HEADERS = ["Date", "Bank reference (as Xero shows it)", "Amount", "VAT?", "Net", "VAT amount",
           "Who it was paid to", "What it actually is", "Category (Sygma's chart)",
           "Matching bill in Xero?", "Who spent it", "Why no VAT (if N)",
           "Status", "Andy's notes"]

# Aurora-ish palette: readable, colourful, not a rainbow.
def rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2],16)/255, "green": int(h[2:4],16)/255, "blue": int(h[4:6],16)/255}

HDR_BG, BAND_BG = rgb("1e3a5f"), rgb("f4f6fc")
AMBER, GREEN, GREY = rgb("fde8c8"), rgb("d9f2e0"), rgb("ededed")
BLUE = rgb("d6e4ff")


def tab_requests(sheet_id, nrows):
    """Formatting.

    ⚠ The one that matters: Google Sheets lets text OVERFLOW into adjacent empty cells unless a wrap
    strategy is set. Descriptions were spilling across neighbouring columns and Pete's verdict was
    "looks crap, confusing and hard to read with overlaps". Every data cell now gets an explicit
    strategy -- WRAP where the text needs reading, CLIP where it just needs to not bleed.
    """
    R = []
    R.append({"updateSheetProperties": {"properties": {"sheetId": sheet_id,
        "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 3}},
        "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}})

    # header
    R.append({"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"backgroundColor": HDR_BG,
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
            "textFormat": {"bold": True, "fontSize": 10,
                           "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)"}})
    R.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
        "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 46}, "fields": "pixelSize"}})

    last = max(nrows, 2)
    body = {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": last}

    # every data cell: top-aligned, small, and NOT allowed to bleed into its neighbour
    R.append({"repeatCell": {"range": body,
        "cell": {"userEnteredFormat": {"verticalAlignment": "TOP", "wrapStrategy": "CLIP",
                 "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat)"}})
    # the two columns people actually read get real wrapping
    for col in (7, 13):                                  # What it actually is, Andy's notes
        R.append({"repeatCell": {"range": {**body, "startColumnIndex": col, "endColumnIndex": col+1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})

    widths = [82, 225, 88, 46, 78, 82, 165, 300, 150, 175, 110, 150, 80, 220]
    for i, w in enumerate(widths):
        R.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id,
            "dimension": "COLUMNS", "startIndex": i, "endIndex": i+1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    for col in (2, 4, 5):                                # Amount, Net, VAT amount
        R.append({"repeatCell": {"range": {**body, "startColumnIndex": col, "endColumnIndex": col+1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER",
                     "pattern": "#,##0.00;[Red]-#,##0.00"}, "horizontalAlignment": "RIGHT"}},
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"}})

    # quiet banding so the eye can follow a row across 13 columns
    R.append({"addBanding": {"bandedRange": {"range": {"sheetId": sheet_id, "startRowIndex": 1,
        "endRowIndex": last},
        "rowProperties": {"firstBandColor": {"red": 1, "green": 1, "blue": 1},
                          "secondBandColor": BAND_BG}}}})

    # colour cues, applied ONLY to the columns they describe so the row stays legible
    vatcol = {**body, "startColumnIndex": 3, "endColumnIndex": 4}
    R.append({"addConditionalFormatRule": {"rule": {"ranges": [vatcol],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "N"}]},
            "format": {"backgroundColor": AMBER, "textFormat": {"bold": True}}}}, "index": 0}})
    R.append({"addConditionalFormatRule": {"rule": {"ranges": [vatcol],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Y"}]},
            "format": {"backgroundColor": GREEN, "textFormat": {"bold": True}}}}, "index": 0}})
    billcol = {**body, "startColumnIndex": 9, "endColumnIndex": 10}
    R.append({"addConditionalFormatRule": {"rule": {"ranges": [billcol],
        "booleanRule": {"condition": {"type": "NOT_BLANK"},
            "format": {"backgroundColor": BLUE, "textFormat": {"bold": True}}}}, "index": 0}})
    # a finished row goes grey and struck through, across the whole row
    R.append({"addConditionalFormatRule": {"rule": {"ranges": [body],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": '=$M2="DONE"'}]},
            "format": {"backgroundColor": GREY,
                       "textFormat": {"strikethrough": True,
                                      "foregroundColor": rgb("888888")}}}}, "index": 0}})

    for col, vals in ((3, ["Y", "N"]), (12, ["OPEN", "ANSWERED", "DONE", "QUERY"])):
        R.append({"setDataValidation": {"range": {**body, "startColumnIndex": col,
            "endColumnIndex": col+1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                     "values": [{"userEnteredValue": v} for v in vals]},
                     "showCustomUi": True, "strict": False}}})
    R.append({"repeatCell": {"range": {**body, "startColumnIndex": 3, "endColumnIndex": 4},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat.horizontalAlignment"}})
    R.append({"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0,
        "endRowIndex": last, "startColumnIndex": 0, "endColumnIndex": 13}}}})
    return R


def build(paths, sheet_id=None):
    S = "/private/tmp"
    accounts = {}
    for p in paths:
        if not p.lower().endswith(".xlsx"):
            continue                        # .csv files are the card export, handled separately
        acct, lines = parse_export(p)
        if lines and len(lines) > len(accounts.get(acct, [])):
            accounts[acct] = lines
    if not accounts:
        _die("no unreconciled lines found in those exports")

    # precedent: how each merchant has been coded in Xero before
    precedent = {}
    cache = os.path.join(S, "xero_precedent.json")
    if os.path.exists(cache):
        precedent = {k: tuple(v) for k, v in json.load(open(cache)).items()}

    paypal = []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pp", os.path.join(VAULT, "paypal-api.py"))
        pp = importlib.util.module_from_spec(spec); spec.loader.exec_module(pp)
        lo = min(l["date"] for ls in accounts.values() for l in ls)
        frm = (datetime.date.fromisoformat(lo) - datetime.timedelta(days=14)).isoformat()
        paypal = pp.payments(pp.list_txns(frm, datetime.date.today().isoformat()))
    except Exception as e:
        print(f"  (PayPal lookup unavailable: {e})", file=sys.stderr)

    card = load_card_export(paths)
    if card:
        print(f"  enriching from the card export: {len(card)} transactions with merchant, "
              "cardholder and receipt flag")

    bills = []
    bcache = os.path.join(S, "xero_open_bills.json")
    if os.path.exists(bcache):
        bills = json.load(open(bcache))
        print(f"  checking against {len(bills)} open bills in Xero")

    tok = sheets_token()
    if not sheet_id:
        r = sapi("POST", "", {"properties": {"title": "Sygma — Bank Reconciliation Queries"}}, tok)
        sheet_id = r["spreadsheetId"]
        print(f"created sheet {sheet_id}")

    meta = sapi("GET", f"/{sheet_id}", tok=tok)
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    reqs = []
    for acct in accounts:
        if acct not in existing:
            reqs.append({"addSheet": {"properties": {"title": acct[:80]}}})
    if reqs:
        sapi("POST", f"/{sheet_id}:batchUpdate", {"requests": reqs}, tok)
        meta = sapi("GET", f"/{sheet_id}", tok=tok)
        existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    fmt = []
    for acct, lines in accounts.items():
        rows = build_rows(lines, precedent, paypal, bills, card, acct)
        title = acct[:80]
        sapi("PUT", f"/{sheet_id}/values/{urllib.parse.quote(title)}!A1:N{len(rows)+1}"
                    "?valueInputOption=USER_ENTERED",
             {"values": [HEADERS] + rows}, tok)
        fmt += tab_requests(existing[title], len(rows) + 1)
        no_vat = sum(1 for r in rows if r[3] == "N")
        named = sum(1 for r in rows if r[7])
        billed = sum(1 for r in rows if r[9])
        print(f"  {acct:30} {len(rows):4} lines | {named} identified | {no_vat} no-VAT | {billed} match an open bill")
    if fmt:
        sapi("POST", f"/{sheet_id}:batchUpdate", {"requests": fmt}, tok)
    print(f"\nhttps://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    return sheet_id


if __name__ == "__main__":
    import urllib.parse
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    if a[0] != "build":
        _die(f"unknown command {a[0]}")
    sid = a[a.index("--sheet")+1] if "--sheet" in a else None
    files = [f for f in a[1:] if f.endswith((".xlsx", ".csv"))]
    if not files:
        _die("give me at least one Xero Bank Reconciliation .xlsx export "
            "(and optionally a Capital on Tap .csv transaction export)")
    build(files, sid)
