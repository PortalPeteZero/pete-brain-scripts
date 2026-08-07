#!/usr/bin/env python3
"""
Gmail API helper -- single canonical path for all Gmail work.

Pattern matches the other Google API helpers in Library/processes/
(GSC, GA4, GTM snippets in google-api-credentials.md).

Service account: sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com
Client ID:       117115682242341369700
Impersonates:    pete.ashcroft@sygma-solutions.com (default)
Domain-wide delegation scopes:
  - https://mail.google.com/
  - https://www.googleapis.com/auth/gmail.settings.basic
  - https://www.googleapis.com/auth/gmail.settings.sharing

Usage (CLI):
  python3 gmail-api.py labels
  python3 gmail-api.py rename-label LABEL_ID "{Category}/{prefix}-{slug}"
  python3 gmail-api.py create-label "{Category}/{prefix}-{slug}"
  python3 gmail-api.py delete-label LABEL_ID
  python3 gmail-api.py search "<query>" [limit]   # e.g. search "from:clancy newer_than:30d" 20
  python3 gmail-api.py get-thread THREAD_ID [full|metadata]   # default full (message bodies included)
  python3 gmail-api.py download-attachment MSG_ID ATT_ID /path/to/save.pdf
  python3 gmail-api.py modify-thread THREAD_ID --add LABEL_ID --remove INBOX
  python3 gmail-api.py send to@example.com "Subject" "Body" [THREAD_ID]
  python3 gmail-api.py draft to@example.com "Subject" "Body" [THREAD_ID]
  python3 gmail-api.py reply THREAD_ID "Body"          # threaded reply (To/Subject/In-Reply-To auto from thread)
  python3 gmail-api.py reply-draft THREAD_ID "Body"    # same, saved as a draft on the thread
  python3 gmail-api.py sweep [--dry-run]      # inverted-design sweep (no protect list)
  python3 gmail-api.py audit-sent [DAYS]      # find sent items missing org labels (default 14 days)
  python3 gmail-api.py audit-sent [DAYS] --apply LABEL_ID    # apply label to all flagged threads (manual verify first!)

Usage (library):
  # The file is hyphenated (gmail-api.py), so `from gmail_api import GmailAPI`
  # does NOT work. Load it by path with importlib instead:
  import importlib.util
  spec = importlib.util.spec_from_file_location('gmail_api', '/tmp/pbs/gmail-api.py')
  gmail_api = importlib.util.module_from_spec(spec); spec.loader.exec_module(gmail_api)
  g = gmail_api.GmailAPI()             # defaults to pete.ashcroft@sygma-solutions.com
  g.list_labels()
  g.rename_label(label_id, "{Category}/{prefix}-{slug}")
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware on Railway (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
DEFAULT_USER = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://mail.google.com/ https://www.googleapis.com/auth/gmail.settings.basic https://www.googleapis.com/auth/gmail.settings.sharing"
BASE = "https://gmail.googleapis.com/gmail/v1/users"

# Recipient domains whose mail gateway rejects anything carrying Pete's HTML signature.
# Every send/draft to one of these goes out UNSIGNED, whatever the caller asked for, so that
# ee-send, the triage skill, reply_thread and a bare `gmail-api.py send` all behave the same and
# nobody has to remember. Measured 6 Aug 2026 across 20 sends to M Group over 60 days: 5 of 16
# signed messages were blocked with Mimecast `554 Host network not allowed`, 0 of 4 unsigned ones
# were. Same API send path on both sides, so the path was never the variable. The lesson note
# [[M Group Mimecast: it is the SIGNATURE that gets blocked, not the API send path]] carries the
# full evidence and the untested hypothesis about why.
# To add a domain: put it here, lowercase, no @. To retire one: prove a signed send lands first.
NO_SIGNATURE_DOMAINS = {
    "mgroupltd.com",         # M Group Water
    "mgroupservices.com",    # M Group Services / Train With Us
    "morrisonus.com",        # Morrison Utility Services (Welsh Water ops)
}


def _blocked_signature_domains(*recipient_fields):
    """Return the sorted NO_SIGNATURE_DOMAINS hit by any address in the given to/cc/bcc values.
    Each value may be a string ('a@x, b@y'), a list, or None."""
    import re as _re
    found = set()
    for field in recipient_fields:
        if not field:
            continue
        text = ", ".join(field) if isinstance(field, (list, tuple, set)) else str(field)
        for addr in _re.findall(r"[\w.+-]+@[\w.-]+", text):
            dom = addr.rsplit("@", 1)[-1].lower().strip(".")
            if dom in NO_SIGNATURE_DOMAINS:
                found.add(dom)
    return sorted(found)


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")



class GmailAPI:
    def __init__(self, user=DEFAULT_USER, key_path=KEY_PATH, scope=SCOPE):
        self.user = user
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth -----------------------------------------------------------------

    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"],
            "sub": self.user,        # impersonate this user via DWD
            "scope": self.scope,
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }))
        ts = f"{header}.{claim}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(self.creds["private_key"])
            kf = f.name
        try:
            sig = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                input=ts.encode(), capture_output=True, check=True,
            ).stdout
        finally:
            os.unlink(kf)
        jwt = f"{ts}.{_b64u(sig)}"
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            }).encode(),
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        self._token = resp["access_token"]
        self._token_exp = now + resp.get("expires_in", 3600)
        return self._token

    def _call(self, method, path, body=None, query=None):
        url = f"{BASE}/{urllib.parse.quote(self.user)}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        # Bounded retry with backoff on transient failures (429 / 5xx / SSL / URLError).
        # A single blip mid-pull used to abort a whole triage round (14 Jul 2026).
        import time as _time, ssl as _ssl
        attempt = 0
        while True:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req) as r:
                    raw = r.read()
                    if not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    attempt += 1; _time.sleep(0.5 * (2 ** attempt)); continue
                msg = e.read().decode(errors="replace")
                raise RuntimeError(f"Gmail API {method} {path} -> HTTP {e.code}: {msg}") from e
            except (urllib.error.URLError, _ssl.SSLError, ConnectionError) as e:
                if attempt < 3:
                    attempt += 1; _time.sleep(0.5 * (2 ** attempt)); continue
                raise RuntimeError(f"Gmail API {method} {path} -> transient: {e}") from e

    # --- labels ---------------------------------------------------------------

    def list_labels(self):
        return self._call("GET", "/labels").get("labels", [])

    def get_label(self, label_id):
        return self._call("GET", f"/labels/{label_id}")

    def create_label(self, name, label_list_visibility="labelShow",
                     message_list_visibility="show", ensure_parents=True):
        """
        Create a label. If the name is nested (e.g. 'Customers/SY-Clancy')
        and ensure_parents=True, auto-create any missing bare parent labels
        (e.g. 'Customers') first with no colour. This is required for Gmail
        to render nested labels as collapsible tree nodes in the sidebar --
        see gmail-label-scheme.md's parent-must-exist rule.
        """
        if ensure_parents and "/" in name:
            existing = {l["name"] for l in self.list_labels()}
            parts = name.split("/")
            for i in range(1, len(parts)):
                parent_name = "/".join(parts[:i])
                if parent_name not in existing:
                    self._call("POST", "/labels", body={
                        "name": parent_name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    })
                    existing.add(parent_name)
        body = {
            "name": name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }
        return self._call("POST", "/labels", body=body)

    def patch_label(self, label_id, **fields):
        """Partial update. Pass any of: name, labelListVisibility, messageListVisibility, color."""
        return self._call("PATCH", f"/labels/{label_id}", body=fields)

    def rename_label(self, label_id, new_name):
        return self.patch_label(label_id, name=new_name)

    def delete_label(self, label_id):
        return self._call("DELETE", f"/labels/{label_id}")

    # --- threads / messages ---------------------------------------------------

    def search_threads(self, q, max_results=20):
        return self._call("GET", "/threads", query={"q": q, "maxResults": max_results}).get("threads", [])

    def search_threads_all(self, q):
        """Paginate the FULL result set for q (follows nextPageToken to exhaustion).
        Returns (threads, exhausted): exhausted is True only when no page token remained,
        so the round pull can prove it captured every in-scope thread (no silent 100 cap)."""
        out, token = [], None
        while True:
            query = {"q": q, "maxResults": 100}
            if token:
                query["pageToken"] = token
            resp = self._call("GET", "/threads", query=query) or {}
            out.extend(resp.get("threads", []))
            token = resp.get("nextPageToken")
            if not token:
                return out, True

    def audit_sent_unlabeled(self, days=14, max_results=100):
        """
        Find sent messages from the last N days that don't carry any
        Customers/* / Suppliers/* / Projects/* / Businesses/* / Accreditations/* label.

        Returns a list of dicts: {message_id, thread_id, subject, to, date, suggested_label}.
        `suggested_label` is None unless the recipient domain or address pattern obviously
        matches an existing customer/supplier folder — Claude / email-task-sync proposes, Pete confirms.

        Use case: defence-in-depth check that outbound mail is properly tagged. Run as part
        of `sync` reconciliation, or ad-hoc via `python3 gmail-api.py audit-sent [days]`.
        """
        # Build map of email-domain -> label_id where the label is an organisational tag.
        labels = self.list_labels()
        org_prefixes = ("Customers/", "Suppliers/", "Projects/", "Businesses/", "Accreditations/")
        org_label_ids = {l["id"] for l in labels
                         if any(l.get("name", "").startswith(p) for p in org_prefixes)}
        labels_by_id = {l["id"]: l.get("name", "") for l in labels}

        threads = self.search_threads(f"in:sent newer_than:{days}d", max_results=max_results)
        unlabeled = []
        for t in threads:
            tdata = self.get_thread(t["id"], fmt="metadata")
            msgs = tdata.get("messages", [])
            # Did ANY message in this thread carry an org label?
            has_org_label = any(
                org_label_ids & set(m.get("labelIds", []))
                for m in msgs
            )
            if has_org_label:
                continue
            # Surface the latest sent message in the thread for review
            sent_msgs = [m for m in msgs if "SENT" in m.get("labelIds", [])]
            if not sent_msgs:
                continue
            latest = sent_msgs[-1]
            headers = {h["name"].lower(): h["value"]
                       for h in latest.get("payload", {}).get("headers", [])}
            unlabeled.append({
                "thread_id": t["id"],
                "message_id": latest["id"],
                "subject": headers.get("subject", ""),
                "to": headers.get("to", ""),
                "cc": headers.get("cc", ""),
                "date": headers.get("date", ""),
                "current_labels": [labels_by_id.get(lid, lid)
                                   for lid in latest.get("labelIds", [])
                                   if not lid.startswith("CATEGORY_") and lid not in {"INBOX", "SENT", "IMPORTANT", "STARRED", "UNREAD"}],
            })
        return unlabeled

    def search_messages(self, q, max_results=50):
        return self._call("GET", "/messages", query={"q": q, "maxResults": max_results}).get("messages", [])

    def get_thread(self, thread_id, fmt="full"):
        return self._call("GET", f"/threads/{thread_id}", query={"format": fmt})

    def get_message(self, msg_id, fmt="full"):
        return self._call("GET", f"/messages/{msg_id}", query={"format": fmt})

    def modify_thread(self, thread_id, add=None, remove=None):
        body = {}
        if add: body["addLabelIds"] = add
        if remove: body["removeLabelIds"] = remove
        return self._call("POST", f"/threads/{thread_id}/modify", body=body)

    def modify_message(self, msg_id, add=None, remove=None):
        body = {}
        if add: body["addLabelIds"] = add
        if remove: body["removeLabelIds"] = remove
        return self._call("POST", f"/messages/{msg_id}/modify", body=body)

    def trash_thread(self, thread_id):
        return self._call("POST", f"/threads/{thread_id}/trash")

    def untrash_thread(self, thread_id):
        return self._call("POST", f"/threads/{thread_id}/untrash")

    def delete_thread(self, thread_id):
        """PERMANENT. Bypasses Trash. Requires https://mail.google.com/ scope."""
        return self._call("DELETE", f"/threads/{thread_id}")

    def delete_message(self, msg_id):
        """PERMANENT. Bypasses Trash."""
        return self._call("DELETE", f"/messages/{msg_id}")

    # --- attachments ----------------------------------------------------------

    def get_attachment(self, msg_id, att_id):
        """Returns dict with 'data' (base64-url-encoded) and 'size'."""
        return self._call("GET", f"/messages/{msg_id}/attachments/{att_id}")

    def download_attachment(self, msg_id, att_id, save_path):
        att = self.get_attachment(msg_id, att_id)
        raw = base64.urlsafe_b64decode(att["data"] + "===")
        os.makedirs(os.path.dirname(os.path.abspath(save_path)) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(raw)
        return {"path": save_path, "bytes": len(raw)}

    def list_attachments_in_message(self, msg_id):
        """Walks the MIME parts tree, returns [{filename, mimeType, attachmentId, size}]."""
        m = self.get_message(msg_id)
        out = []

        def walk(parts):
            for p in parts or []:
                body = p.get("body", {})
                if body.get("attachmentId") and (p.get("filename") or p.get("mimeType")):
                    out.append({
                        "filename": p.get("filename") or "(no filename)",
                        "mimeType": p.get("mimeType"),
                        "attachmentId": body["attachmentId"],
                        "size": body.get("size", 0),
                        "partId": p.get("partId"),
                    })
                walk(p.get("parts"))
        walk(m.get("payload", {}).get("parts"))
        return out

    # --- bulk ops -------------------------------------------------------------

    # System labels that don't count as "user-applied" -- excluded from the sweep check
    # so threads with only INBOX/IMPORTANT/etc. (no real filing label) stay in inbox.
    GMAIL_SYSTEM_LABELS = frozenset({
        "INBOX", "SENT", "TRASH", "SPAM", "STARRED", "IMPORTANT",
        "UNREAD", "DRAFT", "CHAT", "Snoozed",
    })

    def _is_user_label(self, name):
        """Return True if the label is user-applied (not a Gmail system flag)."""
        return name not in self.GMAIL_SYSTEM_LABELS and not name.startswith("CATEGORY_")

    def file_all(self, dry_run=False):
        """
        Sweep -- inverted design (2026-04-25): archive any inbox thread that has at
        least one user-applied label. NO protect list.

        Pete's mental model: Inbox = unprocessed. Actions sidebar = real todolist.
        Delegated sidebar = awaiting replies. Other label sidebars = topical, browse
        when needed. Sweep clears INBOX visibility duplication; information stays
        everywhere else (Gmail's recency sort makes new replies bubble in any view).

        Rule: archive (`removeLabelIds: ["INBOX"]`) every inbox thread whose label set
        includes at least one user-applied label (i.e. not just INBOX/IMPORTANT/etc.
        Gmail system flags). Threads with no user labels -- genuinely unfiled -- stay
        in inbox so Pete can triage.

        Trigger: manual only. Pete types `sweep` in chat -> assistant calls this.
        No scheduled sweep, no auto-offers from triage/sync skills.

        Returns {"archived": [...], "skipped_unfiled": [...], "plan": [...],
                 "by_label": {label: count}, "dry_run": bool}.
        Each entry in `archived` / `plan` includes thread_id and a `labels` list (user labels only).
        `by_label` is the aggregate breakdown: for each user label, how many threads carrying it
        were swept (a thread with N labels contributes 1 to each of its N labels' counts).
        """
        labels = self.list_labels()
        id_to_name = {l["id"]: l["name"] for l in labels}

        threads = self.search_threads("in:inbox", max_results=50)
        # 50 is plenty for a curated daily inbox; bump if a real backlog needs sweeping.
        archived = []
        skipped = []
        plan = []

        for t in threads:
            t_full = self.get_thread(t["id"], fmt="metadata")
            thread_label_ids = set()
            for m in t_full.get("messages", []):
                thread_label_ids.update(m.get("labelIds", []))
            thread_label_names = {id_to_name.get(lid, lid) for lid in thread_label_ids}

            user_labels = sorted(n for n in thread_label_names if self._is_user_label(n))

            if not user_labels:
                # No user-applied labels = genuinely unfiled = stays in inbox.
                skipped.append({"thread_id": t["id"], "reason": "no user labels (unfiled)"})
                continue

            plan.append({"thread_id": t["id"], "labels": user_labels})
            if not dry_run:
                self.modify_thread(t["id"], remove=["INBOX"])
                archived.append({"thread_id": t["id"], "labels": user_labels})

        # Aggregate breakdown by label across whichever set actually happened
        # (archived if live, plan if dry-run).
        counted_set = plan if dry_run else archived
        by_label = {}
        for entry in counted_set:
            for lbl in entry["labels"]:
                by_label[lbl] = by_label.get(lbl, 0) + 1

        return {
            "archived": archived,
            "skipped_unfiled": skipped,
            "plan": plan,
            "by_label": by_label,
            "dry_run": dry_run,
        }

    # Backwards-compat alias -- old callers may still reference file_all_labelled.
    # Maps to the new file_all() (inverted, no protect list). Old protect-list
    # parameters are accepted but ignored, with a one-line warning to stderr.
    def file_all_labelled(self, prefix_whitelist=None, action_protect_prefixes=None,
                          action_protect_exact=None, dry_run=False):
        """DEPRECATED -- use file_all() instead. Kept as alias; old protect-list
        parameters are accepted-and-ignored (the new design has no protect list)."""
        if prefix_whitelist or action_protect_prefixes or action_protect_exact:
            import sys
            print("[gmail-api] WARN: file_all_labelled() is deprecated. "
                  "Use file_all(). Protect-list params are ignored under the new "
                  "inverted sweep design (2026-04-25).", file=sys.stderr)
        return self.file_all(dry_run=dry_run)

    # --- send / draft ---------------------------------------------------------

    def _looks_like_html(self, body):
        """Cheap heuristic: body starts with '<' (after whitespace) and contains a closing tag.
        Used when callers don't explicitly set html=True/False so we don't deliver HTML markup
        as text/plain MIME (which renders as raw tags in mail clients)."""
        s = (body or "").lstrip()
        if not s.startswith("<"):
            return False
        return any(t in s.lower() for t in ("</html", "</body", "</div", "</p>", "</table", "</span", "</a>", "<br", "<!doctype"))

    def _raw_rfc822(self, to, subject, body, cc=None, bcc=None, from_=None, html=None, in_reply_to=None,
                    references=None, attachments=None):
        import os
        from email import encoders
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        # GATE — `html` is a BOOLEAN FLAG, not the content. Passing the markup here while the plain
        # text sits in `body` sends the PLAIN TEXT: the string is merely truthy. That exact mistake
        # went out to real customers twice (1 and 2 Jul 2026) — 12 of 18 LeakGuard customers got a
        # bare "Hi," with no name, no styling and a dangling "(Spanish version below)". Refusing here
        # rather than in a hook covers EVERY caller, including scripts and crons.
        if isinstance(html, str):
            raise ValueError(
                "gmail-api: `html=` is a BOOLEAN FLAG (True force HTML / False force plain / None "
                "auto-detect), NOT the HTML content. You passed a string, so the markup would be "
                "discarded and the PLAIN TEXT in `body=` sent instead — the failure that reached 12 "
                "customers on 2 Jul 2026.\n"
                "  Correct:  send(to, subject, body=<the HTML>, html=True)\n"
                "  You wrote: send(to, subject, body=<plain>, html=<markup>)"
            )

        # GATE — a mail.google.com link points into the SENDER'S own mailbox. It can never open for
        # any other recipient (Gmail thread links are account-scoped), so in an email to anyone but
        # the account owner it is always a dead link. That exact mistake went to Neal Sadd on
        # 27 Jul 2026: a Balfour Beatty handoff carried a mail.google.com/#all link instead of a
        # forward of the thread, and Neal could not open it. Refusing here covers EVERY caller.
        # Forward the actual thread/content instead (reply_thread with the original body inline).
        if "mail.google.com" in body:
            def _addrs(v):
                if not v: return []
                items = v if isinstance(v, (list, tuple)) else v.split(",")
                import re as _re
                out = []
                for it in items:
                    m = _re.search(r"[\w.+-]+@[\w.-]+", str(it))
                    if m: out.append(m.group(0).lower())
                return out
            recips = _addrs(to) + _addrs(cc) + _addrs(bcc)
            external = [a for a in recips if a != self.user.lower()]
            if external:
                raise ValueError(
                    "gmail-api: the body contains a mail.google.com link, which only opens inside "
                    f"the sender's own mailbox — it is a guaranteed dead link for {', '.join(external)}. "
                    "Forward the actual thread content instead (e.g. reply_thread on the original "
                    "thread with the message body quoted inline). Failure this prevents: the "
                    "27 Jul 2026 Balfour Beatty handoff link Neal Sadd could not open."
                )

        if html is None:
            html = self._looks_like_html(body)  # auto-detect when caller doesn't specify

        # ATTACHMENTS. Gmail takes raw RFC822, so files have always been possible -- but send() and
        # create_draft() had no parameter for them, and on 4 Aug 2026 a session read the signature,
        # concluded "the helper sends text only", and told Pete he had to drag the PDFs in by hand.
        # Wrong answer from a true observation. The capability now lives here so no caller has to
        # rebuild the MIME tree, and nobody can read the signature and conclude it cannot be done.
        # `attachments` = a path, or a list of paths, or a list of (path, filename) to rename on the way out.
        if attachments:
            import mimetypes
            from email.mime.base import MIMEBase
            items = attachments if isinstance(attachments, (list, tuple)) else [attachments]
            outer = MIMEMultipart()
            outer.attach(MIMEText(body, "html" if html else "plain", "utf-8"))
            for it in items:
                path, name = it if isinstance(it, (list, tuple)) else (it, None)
                if not os.path.isfile(path):
                    raise ValueError(f"gmail-api: attachment not found: {path} -- refusing to send a mail "
                                     "that promises an attachment it does not carry")
                ctype, _ = mimetypes.guess_type(path)
                maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
                part = MIMEBase(maintype, subtype)
                with open(path, "rb") as fh:
                    part.set_payload(fh.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment",
                                filename=name or os.path.basename(path))
                outer.attach(part)
            msg = outer
        else:
            msg = MIMEText(body, "html" if html else "plain", "utf-8")
        msg["To"] = to if isinstance(to, str) else ", ".join(to)
        if cc: msg["Cc"] = cc if isinstance(cc, str) else ", ".join(cc)
        if bcc: msg["Bcc"] = bcc if isinstance(bcc, str) else ", ".join(bcc)
        if from_: msg["From"] = from_
        if in_reply_to: msg["In-Reply-To"] = in_reply_to
        if references: msg["References"] = references
        msg["Subject"] = subject
        return _b64u(msg.as_bytes())

    def _signature_html(self, from_=None):
        """The HTML signature configured in Gmail settings for a send-as alias (the DEFAULT alias when
        from_ is None). Cached per alias. Returns '' if none configured / on any error — Gmail's web app
        inserts this at compose time, but the API never does, so we add it ourselves (else API-made drafts
        + sends go out unsigned)."""
        if not hasattr(self, "_sig_cache"):
            self._sig_cache = {}
        key = from_ or "__default__"
        if key in self._sig_cache:
            return self._sig_cache[key]
        sig = ""
        try:
            for sa in self.list_send_as():
                if (from_ and sa.get("sendAsEmail") == from_) or (not from_ and sa.get("isDefault")):
                    sig = sa.get("signature") or ""
                    break
        except Exception:
            sig = ""
        self._sig_cache[key] = sig
        return sig

    @staticmethod
    def _sig_fingerprint(s):
        import re as _re
        return _re.sub(r"[^a-z0-9]", "", _re.sub(r"<[^>]+>", " ", s or "").lower())

    def _signature_allowed(self, signature, to, cc=None, bcc=None):
        """Force the signature OFF for recipients in NO_SIGNATURE_DOMAINS, whatever the caller asked.
        Central on purpose: send/create_draft are the only two places that sign, and reply_thread
        funnels through them, so every path (ee-send, triage, the CLI, a library call) is covered
        without a single call site having to know. Never turns a signature ON."""
        if not signature:
            return False
        hits = _blocked_signature_domains(to, cc, bcc)
        if hits:
            print(f"gmail-api: signature suppressed for {', '.join(hits)} "
                  f"(their gateway blocks signed mail)", file=sys.stderr)
            return False
        return True

    def _apply_signature(self, body, html, from_):
        """Append the alias's Gmail signature to the body, unless it's already present (re-run, or the
        caller embedded it) — so it never double-signs. Forces HTML output when appending, converting a
        plain-text body so its line breaks survive. Best-effort: any failure returns the body untouched."""
        try:
            sig = self._signature_html(from_)
            if not sig:
                return body, html
            fp = self._sig_fingerprint(sig)
            if fp and fp in self._sig_fingerprint(body):   # already signed -> leave it
                return body, html
            is_html = html if html is not None else self._looks_like_html(body)
            if is_html:
                return body + "<br><br>" + sig, True
            import html as _h   # plain body -> minimal HTML so the signature renders + line breaks survive
            return _h.escape(body).replace("\n", "<br>") + "<br><br>" + sig, True
        except Exception:
            return body, html

    def _house_format(self, body, html):
        """Render a PLAIN-TEXT body into the house HTML style (email-html.py). Pete, 7 Aug 2026:
        "every time you send an email, do it in this nice HTML format."

        Central on purpose, exactly like the signature below it: send() and create_draft() are the
        only two callers and reply_thread() funnels through them, so every path (ee-send, triage,
        the CLI, a cron, a library call) is covered without a single call site knowing.

        Only plain text is touched. A body that is already HTML — the 46 report generators that
        build their own markup, and anything passing html=True — is returned untouched, so this
        cannot restyle something that was already designed. Best-effort: any failure returns the
        body exactly as it came in, because a formatting problem must never block a send.

        THE ONE EXCEPTION is email_html.VoiceViolation (a forbidden em/en/double dash), which is
        re-raised so it actually stops the send. That is the whole point of a gate: swallowing it
        here would render the email unformatted and mail the dash anyway."""
        m = None
        try:
            if html is True or (html is None and self._looks_like_html(body)):
                return body, html
            if html is False:                      # caller explicitly demanded plain text
                return body, html
            import importlib.util, os
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email-html.py")
            if not os.path.exists(p):
                return body, html
            spec = importlib.util.spec_from_file_location("email_html", p)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return m.to_html(body), True
        except Exception as e:
            # duck-typed, NOT isinstance: loading email-html by path twice makes two distinct
            # classes, so identity comparison silently fails and the gate would be swallowed
            if getattr(e, "is_voice_violation", False):
                raise
            return body, html

    def send(self, to, subject, body, cc=None, bcc=None, from_=None, html=None, thread_id=None, signature=True,
             in_reply_to=None, references=None, attachments=None):
        """Send an email. `html` defaults to None = auto-detect from body content.
        `attachments` = a path, a list of paths, or a list of (path, filename) pairs to rename on the way
        out. YES, THIS HELPER SENDS ATTACHMENTS -- do not tell anyone it cannot (4 Aug 2026).
        Pass html=True to force HTML, html=False to force plain text. `signature=True` (default) appends the
        sender alias's Gmail signature (never double-signs); pass signature=False to omit it.
        For a threaded REPLY, prefer reply_thread() — it fills thread_id + in_reply_to + references for you.
        NOTE: `html` is a FLAG, not content. If you pass an HTML *string* to `html`, it is treated as the body
        (common footgun that silently mailed the plain `body` instead — guarded here)."""
        if isinstance(html, str):
            body, html = html, True
        body, html = self._house_format(body, html)   # plain text -> house style; HTML untouched
        signature = self._signature_allowed(signature, to, cc, bcc)
        if signature:
            body, html = self._apply_signature(body, html, from_)
        raw = self._raw_rfc822(to, subject, body, cc, bcc, from_, html, in_reply_to, references, attachments)
        body_obj = {"raw": raw}
        if thread_id: body_obj["threadId"] = thread_id
        return self._call("POST", "/messages/send", body=body_obj)

    def create_draft(self, to, subject, body, cc=None, bcc=None, from_=None, html=None, thread_id=None, signature=True,
                     in_reply_to=None, references=None, attachments=None):
        """Create a draft. `attachments` = a path / list of paths / list of (path, filename) pairs.
        YES, DRAFTS CARRY ATTACHMENTS TOO. `html` defaults to None = auto-detect from body content. `signature=True`
        (default) appends the sender alias's Gmail signature (never double-signs) so a draft opened + sent
        from Gmail looks the same as one you composed yourself; pass signature=False to omit it.
        For a threaded REPLY draft, prefer reply_thread(..., as_draft=True).
        NOTE: `html` is a FLAG, not content. If you pass an HTML *string* to `html`, it is treated as the body."""
        if isinstance(html, str):
            body, html = html, True
        body, html = self._house_format(body, html)   # plain text -> house style; HTML untouched
        signature = self._signature_allowed(signature, to, cc, bcc)
        if signature:
            body, html = self._apply_signature(body, html, from_)
        raw = self._raw_rfc822(to, subject, body, cc, bcc, from_, html, in_reply_to, references, attachments)
        msg_obj = {"raw": raw}
        if thread_id: msg_obj["threadId"] = thread_id
        return self._call("POST", "/drafts", body={"message": msg_obj})

    def reply_thread(self, thread_id, body, as_draft=False, to=None, cc=None, from_=None, html=None, signature=True):
        """Reply ON an existing thread — the safe way to answer an incoming email (never an orphan).
        Derives recipient, 'Re:' subject and the In-Reply-To / References headers from the thread's
        latest message, then sends (or drafts) with threadId set so it lands inside the conversation.
        `to` defaults to the latest message's Reply-To/From. Returns the API response."""
        th = self.get_thread(thread_id)
        msgs = th.get("messages", [])
        if not msgs:
            raise ValueError(f"thread {thread_id} has no messages")
        last = msgs[-1]
        def _h(m, name):
            for h in m.get("payload", {}).get("headers", []):
                if h["name"].lower() == name.lower():
                    return h["value"]
            return ""
        msg_id = _h(last, "Message-ID") or _h(last, "Message-Id")
        prior_refs = _h(last, "References")
        references = (prior_refs + " " + msg_id).strip() if msg_id else (prior_refs or None)
        subject = _h(msgs[0], "Subject") or ""
        if subject and not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        if to is None:
            # Derive the recipient from the latest message NOT sent by us — replying to
            # msgs[-1].From blindly addresses our own last touch back to ourselves when the
            # thread was forwarded/handled on our side (customer-facing bug, 30 Jun 2026).
            import re as _re
            own = set()
            try:
                own = {(sa.get("sendAsEmail") or "").lower() for sa in self.list_send_as()}
            except Exception:
                pass
            def _addr(s):
                mm = _re.search(r"[\w.+-]+@[\w.-]+", s or "")
                return mm.group(0).lower() if mm else ""
            to = ""
            for msg in reversed(msgs):
                cand = _h(msg, "Reply-To") or _h(msg, "From")
                if cand and _addr(cand) not in own:
                    to = cand
                    break
            if not to:  # whole thread is us — fall back to the latest sender
                to = _h(last, "Reply-To") or _h(last, "From")
        fn = self.create_draft if as_draft else self.send
        return fn(to, subject, body, cc=cc, from_=from_, html=html, thread_id=thread_id,
                  signature=signature, in_reply_to=msg_id or None, references=references)

    def forward_message(self, msg_id, to, note, subject=None, cc=None, from_=None, as_draft=False):
        """FORWARD a message with the ORIGINAL ATTACHED INTACT (message/rfc822) — what a mail
        client does. Attachments, headers and formatting all survive, and the recipient can open
        or reply to the real thing.

        Built 7 Aug 2026. A triage session was told to forward two threads to Sue, found no
        forward helper, and instead RETYPED both bodies into a fresh email. That is not a
        forward: every attachment was lost, the headers were lost, and the recipient got a
        paraphrase they could not trust or act on. Pete: "you need to forward to keep the thread
        intact, and if no forward helper build one". So it exists, and nobody has to improvise it
        again.

        `note` is your covering line, ABOVE the standard forwarded-message header block.
        Returns the API response. Use forward_thread() to forward a thread's latest message."""
        import base64 as _b64
        from email import message_from_bytes as _from_bytes, policy as _policy
        from email.message import EmailMessage as _EmailMessage

        raw = self.get_message(msg_id, fmt="raw")["raw"]
        orig = _from_bytes(_b64.urlsafe_b64decode(raw), policy=_policy.default)

        outer = _EmailMessage()
        outer["To"] = to
        if cc:
            outer["Cc"] = cc
        outer["From"] = from_ or "pete.ashcroft@sygma-solutions.com"
        outer["Subject"] = subject or ("Fwd: " + (orig.get("Subject") or "(no subject)"))
        outer.set_content(
            f"{note}\n\n"
            "---------- Forwarded message ----------\n"
            f"From: {orig.get('From','')}\n"
            f"Date: {orig.get('Date','')}\n"
            f"Subject: {orig.get('Subject','')}\n"
            f"To: {orig.get('To','')}\n"
        )
        # message/rfc822 — the whole original, byte for byte
        outer.add_attachment(orig, filename=((orig.get("Subject") or "message")[:60] + ".eml"))

        encoded = _b64.urlsafe_b64encode(outer.as_bytes()).decode()
        if as_draft:
            return self._call("POST", "/drafts", body={"message": {"raw": encoded}})
        return self._call("POST", "/messages/send", body={"raw": encoded})

    def forward_thread(self, thread_id, to, note, subject=None, cc=None, from_=None, as_draft=False):
        """Forward a thread's LATEST message, original attached intact. See forward_message()."""
        msgs = self.get_thread(thread_id).get("messages", [])
        if not msgs:
            raise ValueError(f"thread {thread_id} has no messages")
        return self.forward_message(msgs[-1]["id"], to, note, subject, cc, from_, as_draft)

    def list_drafts(self, max_results=20, q=None):
        query = {"maxResults": max_results}
        if q: query["q"] = q
        return self._call("GET", "/drafts", query=query).get("drafts", [])

    def get_draft(self, draft_id, fmt="full"):
        return self._call("GET", f"/drafts/{draft_id}", query={"format": fmt})

    def send_draft(self, draft_id):
        return self._call("POST", "/drafts/send", body={"id": draft_id})

    def delete_draft(self, draft_id):
        return self._call("DELETE", f"/drafts/{draft_id}")

    # --- settings: filters, send-as, signature, vacation ----------------------

    def list_filters(self):
        return self._call("GET", "/settings/filters").get("filter", [])

    def create_filter(self, criteria, action):
        """criteria/action per https://developers.google.com/gmail/api/reference/rest/v1/users.settings.filters"""
        return self._call("POST", "/settings/filters", body={"criteria": criteria, "action": action})

    def delete_filter(self, filter_id):
        return self._call("DELETE", f"/settings/filters/{filter_id}")

    def list_send_as(self):
        return self._call("GET", "/settings/sendAs").get("sendAs", [])

    def get_send_as(self, email):
        return self._call("GET", f"/settings/sendAs/{urllib.parse.quote(email)}")

    def update_send_as(self, email, **fields):
        """Fields: displayName, signature, isDefault, replyToAddress, treatAsAlias."""
        return self._call("PATCH", f"/settings/sendAs/{urllib.parse.quote(email)}", body=fields)

    def get_vacation(self):
        return self._call("GET", "/settings/vacation")

    def update_vacation(self, **fields):
        """Fields: enableAutoReply, responseSubject, responseBodyPlainText, responseBodyHtml,
           restrictToContacts, restrictToDomain, startTime, endTime."""
        return self._call("PUT", "/settings/vacation", body=fields)


# --- CLI ----------------------------------------------------------------------

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    g = GmailAPI()
    cmd, *args = sys.argv[1:]

    if cmd == "labels":
        labels = g.list_labels()
        for l in sorted(labels, key=lambda x: x.get("name", "")):
            print(f"{l['id']:30s}  {l['name']}")
    elif cmd == "create-label":
        name = args[0]
        out = g.create_label(name)
        print(json.dumps(out, indent=2))
    elif cmd == "rename-label":
        label_id, new_name = args[0], args[1]
        out = g.rename_label(label_id, new_name)
        print(json.dumps(out, indent=2))
    elif cmd == "delete-label":
        out = g.delete_label(args[0])
        print("deleted" if out is None else json.dumps(out, indent=2))
    elif cmd == "search":
        q = args[0]
        limit = int(args[1]) if len(args) > 1 else 20
        print(json.dumps(g.search_threads(q, max_results=limit), indent=2))
    elif cmd == "get-thread":
        fmt = args[1] if len(args) > 1 else "full"
        print(json.dumps(g.get_thread(args[0], fmt=fmt), indent=2))
    elif cmd == "modify-thread":
        thread_id = args[0]
        add, remove = [], []
        i = 1
        while i < len(args):
            if args[i] == "--add":
                add.append(args[i+1]); i += 2
            elif args[i] == "--remove":
                remove.append(args[i+1]); i += 2
            else:
                i += 1
        print(json.dumps(g.modify_thread(thread_id, add=add or None, remove=remove or None), indent=2))
    elif cmd == "list-attachments":
        print(json.dumps(g.list_attachments_in_message(args[0]), indent=2))
    elif cmd == "download-attachment":
        msg_id, att_id, save_path = args[0], args[1], args[2]
        print(json.dumps(g.download_attachment(msg_id, att_id, save_path), indent=2))
    elif cmd == "send":
        to, subject, body = args[0], args[1], args[2]
        thread_id = args[3] if len(args) > 3 else None   # optional: thread to attach to (sets threadId only)
        print(json.dumps(g.send(to, subject, body, thread_id=thread_id), indent=2))
    elif cmd == "draft":
        to, subject, body = args[0], args[1], args[2]
        thread_id = args[3] if len(args) > 3 else None
        print(json.dumps(g.create_draft(to, subject, body, thread_id=thread_id), indent=2))
    elif cmd == "reply":
        # reply ON an incoming thread (recipient/subject/In-Reply-To/References auto-derived) -> never an orphan
        thread_id, body = args[0], args[1]
        print(json.dumps(g.reply_thread(thread_id, body), indent=2))
    elif cmd == "reply-draft":
        thread_id, body = args[0], args[1]
        print(json.dumps(g.reply_thread(thread_id, body, as_draft=True), indent=2))
    elif cmd == "filters":
        print(json.dumps(g.list_filters(), indent=2))
    elif cmd == "send-as":
        print(json.dumps(g.list_send_as(), indent=2))
    elif cmd == "vacation":
        print(json.dumps(g.get_vacation(), indent=2))
    elif cmd in ("sweep", "file-all"):
        # New inverted-sweep design (2026-04-25): no whitelist, no protect list.
        # `sweep` is the canonical verb; `file-all` kept as alias for old muscle memory.
        dry = "--dry-run" in args
        out = g.file_all(dry_run=dry)
        print(json.dumps(out, indent=2))
    elif cmd == "audit-sent":
        # python3 gmail-api.py audit-sent [days] [--apply LABEL_ID]
        # Find sent messages in the last N days that don't carry an org-label.
        # If --apply LABEL_ID is given, applies that label to all flagged messages
        # (only do this when you've manually verified the list).
        days = 14
        apply_label = None
        for i, a in enumerate(args):
            if a.isdigit():
                days = int(a)
            elif a == "--apply" and i + 1 < len(args):
                apply_label = args[i + 1]
        items = g.audit_sent_unlabeled(days=days)
        print(f"Found {len(items)} unlabeled sent threads in last {days} days:\n")
        for x in items:
            print(f"  thread {x['thread_id']:18s} | {x['date'][:25]}")
            print(f"    to:      {x['to'][:80]}")
            if x['cc']:
                print(f"    cc:      {x['cc'][:80]}")
            print(f"    subject: {x['subject'][:80]}")
            if x['current_labels']:
                print(f"    has:     {', '.join(x['current_labels'])}")
            print()
        if apply_label and items:
            confirm = input(f"\nApply label {apply_label} to all {len(items)} threads? (y/N): ")
            if confirm.lower() == "y":
                for x in items:
                    g.modify_thread(x['thread_id'], add=[apply_label])
                    print(f"  ✓ {x['thread_id']}")
    elif cmd == "whoami":
        print(f"Impersonating: {g.user}")
        print(f"Key: {os.path.abspath(KEY_PATH)}")
        print(f"Scopes: {SCOPE}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
