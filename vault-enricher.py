#!/usr/bin/env python3
"""
Vault enricher v2.0 -- shared content-extraction helper for inbox-triage and email-task-sync.

v2.0 (2026-07-02, Pete-directed): REWRITTEN to fix the bug the v1.1 header used to warn
about but never fixed. v1.1 wrote attachments + extracts into a LOCAL SCRATCH FOLDER
(vault_root, default /tmp/pbs) that is NOT Google Drive and NOT the CC database. That
folder is untracked by git and gets deleted on a failed reclone / reboot / tmp cleanup --
so every "attachments_pulled" / "extract_path" result was a lie: nothing durable actually
happened. This was known and documented (Part D migration ledger, 22 Jun 2026) but the
actual fix was deferred indefinitely. Found + fixed 2 Jul 2026 when a Renta 2025 tax
thread got "enriched" into that scratch folder instead of the real Ashcroft Family Drive
folder that already held the rest of that matter.

WHAT CHANGED: this version takes an EXPLICIT, ABSOLUTE, REAL filesystem path for
`target_dir` -- the local CloudStorage-mounted Google Drive folder to write into (e.g.
"~/Library/CloudStorage/GoogleDrive-.../Shared drives/Sygma Hub/Projects/Team-Finances/files/SY-ainscough-hire").
Attachments and the body extract are written DIRECTLY there (this mirrors real Drive --
no separate sync step needed). The caller (Claude, per the CANONICAL-HOME RULE below)
is responsible for resolving that path via search-first against the `drive_files` index
BEFORE calling -- this tool no longer guesses a folder from a bare entity slug, because
guessing is exactly how past misfiles happened (Renta -> Suppliers/AT-Clarity-Lanzarote;
LeakGuard content -> a made-up "CD-LeakGuard" scratch folder that was never a real Drive
path). Established real-world convention confirmed against drive_files (2 Jul 2026):
matter folders sit flat under ".../Team-Finances/files/{slug}/", ".../CD-LeakGuard/files/{slug}/",
etc, with attachments AND a dated .md extract sitting side by side -- no separate
source/extracts subfolders. This version matches that.

The optional "recent activity" / "contacts" README update now targets a `vault_notes` row
(the CC database), not a local README.md file -- because the entity "READMEs" this helper
used to walk up to (e.g. Suppliers/SY-Dext/README.md) are actually vault_notes rows in the
real system, not files on disk. Pass `entity_note_title` (the vault_notes title, e.g.
"SY-Dext") to enable this; omit it to skip (most matter-folder enrichments won't have one).

================================ CANONICAL-HOME RULE (still applies) ================================
This helper writes into WHATEVER `target_dir` you pass. It does NOT know the canonical
home for the content. So BEFORE you call it:

  1. SEARCH the `drive_files` index (cc-sql.py) for the ESTABLISHED home of this content
     type. Do not invent a new folder name. Where do prior items of this kind already live?
  2. Confirm the PRIVACY TIER is right (family tax/financial -> Ashcroft Family Drive,
     NOT a business/supplier folder; salary/payroll -> owner-private; etc).
  3. `target_dir` MUST be an absolute path under a real CloudStorage-mounted Drive (see
     `resolve_drive_root()` below) -- the tool refuses to write anywhere else.
=====================================================================================================

Usage (library):
    from vault_enricher import VaultEnricher
    e = VaultEnricher(gmail_helper)
    result = e.enrich_thread(thread_id, target_dir, dry_run=False, entity_note_title=None)
    # result = {"attachments_pulled": [...], "extract_path": "...", "contacts_added": [...]}

Idempotent: re-running on the same thread is safe (skips files that already exist).
Dry-run: pass dry_run=True to log intended operations without writing.

CLI:
    python3 vault-enricher.py THREAD_ID /absolute/path/to/real/drive/folder [--dry-run] [--entity-note "SY-Dext"]
"""

from __future__ import annotations
import base64
import os
import re
import sys
import datetime
from pathlib import Path

# ============================================================
# SKIP RULES (hard-coded -- v1.0, unchanged in v2)
# ============================================================

SKIP_FILING_LABELS = frozenset({
    "General/PA-General",        # Pete personal -- explicitly skipped
})

SKIP_OPERATIONAL_LABELS = frozenset({
    "Travel", "Receipts", "Shipping", "Newsletters",
    "Voice-Mail", "Voicemail", "Briefings", "Alerts",
})

GMAIL_SYSTEM_LABELS = frozenset({
    "INBOX", "SENT", "TRASH", "SPAM", "STARRED", "IMPORTANT",
    "UNREAD", "DRAFT", "CHAT", "Snoozed",
})

SKIP_ATTACHMENT_PATTERNS = [
    re.compile(r"^image\d+\.(png|jpg|jpeg|gif)$", re.IGNORECASE),
    re.compile(r"^smime\.p7s$", re.IGNORECASE),
    re.compile(r"\.ics$", re.IGNORECASE),
    re.compile(r"^untitled.*$", re.IGNORECASE),
]

SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$", re.MULTILINE),
    re.compile(r"^Sent from my (iPhone|iPad|Android)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^This email and any attachments", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^CONFIDENTIALITY NOTICE", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^DISCLAIMER:", re.MULTILINE | re.IGNORECASE),
]

AUTO_REPLY_PATTERNS = [
    re.compile(r"^(Out of Office|Auto.*Reply|Automatic Reply|Vacation)", re.IGNORECASE),
    re.compile(r"^Delivery Status Notification", re.IGNORECASE),
    re.compile(r"^Mail Delivery", re.IGNORECASE),
]

PETE_ADDRESSES = frozenset({
    "pete.ashcroft@sygma-solutions.com",
    "pete@canary-detect.com",
    "pete.ashcroft@canary-detect.com",
    "sygmasolutions@gmail.com",
})

INTERNAL_DOMAINS = frozenset({
    "sygma-solutions.com",
    "canary-detect.com",
})


def resolve_drive_root():
    """Find the local CloudStorage mount root for Pete's Google Drive on this Mac.
    Returns the absolute path, or None if not found (e.g. running in a sandbox without
    the mount -- callers should fall back to writing via Desktop Commander in that case)."""
    base = Path.home() / "Library" / "CloudStorage"
    if not base.is_dir():
        return None
    for entry in base.iterdir():
        if entry.name.startswith("GoogleDrive-"):
            return entry
    return None


# ============================================================
# MAIN ENRICHER CLASS
# ============================================================

class VaultEnricher:
    """Pulls Gmail thread content DIRECTLY into a real Google Drive folder.

    Constructor takes a GmailAPI instance (the helper from gmail-api.py).
    Main method: enrich_thread(thread_id, target_dir, dry_run, entity_note_title).
    """

    def __init__(self, gmail_helper, cc_sql_runner=None):
        self.g = gmail_helper
        self._labels_cache = None
        # cc_sql_runner(sql: str) -> parsed JSON response; defaults to shelling out to cc-sql.py
        self._cc_sql_runner = cc_sql_runner

    # --- Public entry point ---------------------------------------------------

    def enrich_thread(self, thread_id, target_dir, dry_run=False, entity_note_title=None):
        """Enrich a REAL Drive folder with content from the thread.

        Args:
            thread_id: Gmail thread ID
            target_dir: ABSOLUTE path to a real, already-resolved Drive folder
                        (e.g. under ~/Library/CloudStorage/GoogleDrive-.../Shared drives/...).
                        The caller must have already confirmed this is the canonical home --
                        see the CANONICAL-HOME RULE in this file's header.
            dry_run: if True, log intended operations without writing
            entity_note_title: optional vault_notes title to update with recent-activity +
                        contacts (e.g. "SY-Dext"). Omit to skip -- most matter folders don't
                        have one.

        Returns:
            {
              "skipped": False/True (with reason if True),
              "attachments_pulled": [paths],
              "extract_path": str or None,
              "contacts_added": [names],
              "recent_activity_appended": True/False,
            }
        """
        result = {
            "skipped": False,
            "skip_reason": None,
            "attachments_pulled": [],
            "extract_path": None,
            "contacts_added": [],
            "recent_activity_appended": False,
            "dry_run": dry_run,
        }

        target_path = Path(target_dir)
        if not dry_run:
            drive_root = resolve_drive_root()
            if drive_root is None or drive_root not in target_path.parents and drive_root != target_path:
                result["skipped"] = True
                result["skip_reason"] = (
                    f"target_dir '{target_dir}' is not under a real Drive mount "
                    f"({drive_root or 'no GoogleDrive-* mount found'}). Refusing to write "
                    f"to a non-Drive path -- resolve the real folder first."
                )
                return result

        thread = self.g.get_thread(thread_id)
        skip_check = self._should_skip(thread, target_path)
        if skip_check:
            result["skipped"] = True
            result["skip_reason"] = skip_check
            return result

        if not target_path.exists() and not dry_run:
            target_path.mkdir(parents=True, exist_ok=True)

        result["attachments_pulled"] = self._pull_attachments(thread, target_path, dry_run)
        result["extract_path"] = self._extract_body(thread, target_path, dry_run)

        if entity_note_title:
            result["recent_activity_appended"] = self._update_recent_activity_note(
                thread, entity_note_title, dry_run
            )
            result["contacts_added"] = self._extract_contacts_note(
                thread, entity_note_title, dry_run
            )

        return result

    # --- Skip rules -----------------------------------------------------------

    def _should_skip(self, thread, target_path):
        if "PA-General" in str(target_path):
            return "PA-General target -- enrichment skipped per design"

        label_names = self._thread_label_names(thread)
        user_labels = [n for n in label_names if self._is_user_label(n)]

        if not user_labels:
            return "thread has no user-applied labels (unfiled, won't enrich)"

        non_operational = [n for n in user_labels if n not in SKIP_OPERATIONAL_LABELS]
        if not non_operational:
            return f"thread has only operational labels ({user_labels}) -- no enrichment"

        if thread.get("messages"):
            first_msg = thread["messages"][0]
            subject = self._get_header(first_msg, "subject") or ""
            if any(p.match(subject) for p in AUTO_REPLY_PATTERNS):
                return f"auto-reply subject pattern matched: {subject[:50]}"

        return None

    def _is_user_label(self, name):
        return name not in GMAIL_SYSTEM_LABELS and not name.startswith("CATEGORY_")

    def _thread_label_names(self, thread):
        if self._labels_cache is None:
            self._labels_cache = {l["id"]: l["name"] for l in self.g.list_labels()}
        ids = set()
        for m in thread.get("messages", []):
            ids.update(m.get("labelIds", []))
        return {self._labels_cache.get(i, i) for i in ids}

    # --- Attachment pulling (writes DIRECTLY to the real Drive folder) --------

    def _pull_attachments(self, thread, target_path, dry_run):
        pulled = []
        for msg in thread.get("messages", []):
            msg_id = msg["id"]
            attachments = self._list_attachments_in_message(msg)
            for att in attachments:
                fname = att["filename"]
                if not fname:
                    continue
                if any(p.match(fname) for p in SKIP_ATTACHMENT_PATTERNS):
                    continue
                save_path = target_path / fname
                if save_path.exists():
                    continue
                if not dry_run:
                    target_path.mkdir(parents=True, exist_ok=True)
                    try:
                        self.g.download_attachment(msg_id, att["attachmentId"], str(save_path))
                    except Exception as e:
                        print(f"[vault-enricher] WARN: attachment {fname} failed: {e}",
                              file=sys.stderr)
                        continue
                pulled.append(str(save_path))
        return pulled

    def _list_attachments_in_message(self, message):
        out = []
        def walk(parts):
            if not parts: return
            for p in parts:
                fname = p.get("filename")
                body = p.get("body", {})
                if fname and body.get("attachmentId"):
                    out.append({"filename": fname, "attachmentId": body["attachmentId"],
                                "mimeType": p.get("mimeType")})
                walk(p.get("parts"))
        walk(message.get("payload", {}).get("parts"))
        return out

    # --- Body extraction (writes a dated .md DIRECTLY into the real folder) --

    def _extract_body(self, thread, target_path, dry_run):
        if not thread.get("messages"):
            return None
        first_msg = thread["messages"][0]
        subject = (self._get_header(first_msg, "subject") or "no-subject")[:60]
        date_str = self._extract_date(first_msg) or datetime.date.today().isoformat()

        slug = self._slugify(subject)
        extract_filename = f"{date_str}-{slug}.md"
        extract_path = target_path / extract_filename

        if extract_path.exists():
            return None

        sections = [
            f"---",
            f"type: email-extract",
            f"date: {date_str}",
            f"thread_id: {thread['id']}",
            f"thread_url: https://mail.google.com/mail/u/0/#all/{thread['id']}",
            f"tags: [email-extract]",
            f"---",
            f"",
            f"# {subject}",
            f"",
        ]

        for i, msg in enumerate(thread.get("messages", [])):
            sender = self._get_header(msg, "from") or "?"
            msg_date = self._extract_date(msg) or "?"
            body = self._get_message_text(msg)
            body = self._strip_signature(body)
            body = self._collapse_whitespace(body)
            if not body or len(body.split()) < 5:
                continue
            sections.append(f"## Message {i+1} -- {msg_date} -- from {sender[:60]}")
            sections.append("")
            sections.append(body[:2000])
            sections.append("")

        if len(sections) < 11:
            return None

        content = "\n".join(sections)
        if not dry_run:
            target_path.mkdir(parents=True, exist_ok=True)
            extract_path.write_text(content, encoding="utf-8")
        return str(extract_path)

    def _get_message_text(self, message):
        text_parts = []
        def walk(parts, prefer_text=True):
            if not parts: return
            for p in parts:
                mime = p.get("mimeType", "")
                if mime == "text/plain":
                    data = p.get("body", {}).get("data")
                    if data:
                        text_parts.append(self._b64url_decode(data))
                elif mime.startswith("multipart/"):
                    walk(p.get("parts"), prefer_text)
        payload = message.get("payload", {})
        walk(payload.get("parts"))
        if not text_parts:
            data = payload.get("body", {}).get("data")
            if data:
                text_parts.append(self._b64url_decode(data))
        return "\n".join(text_parts)

    def _strip_signature(self, body):
        for pat in SIGNATURE_MARKERS:
            m = pat.search(body)
            if m:
                body = body[:m.start()].rstrip()
                break
        return body

    def _collapse_whitespace(self, text):
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # --- vault_notes ("README") updates -- recent activity + contacts --------
    # Real entity "READMEs" (e.g. Suppliers/SY-Dext) are vault_notes rows in the CC
    # database, not files on disk. Update via cc-sql.py rather than filesystem I/O.

    def _cc_sql(self, sql):
        if self._cc_sql_runner:
            return self._cc_sql_runner(sql)
        import subprocess, json
        vault = os.environ.get("VAULT", "/tmp/pbs")
        r = subprocess.run(
            ["python3", f"{vault}/cc-sql.py", sql],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(f"cc-sql failed: {r.stderr[:300]}")
        return json.loads(r.stdout)

    def _get_note_body(self, title):
        safe_title = title.replace("'", "''")
        rows = self._cc_sql(f"SELECT id, body FROM vault_notes WHERE title = '{safe_title}'")
        return rows[0] if rows else None

    def _update_note_body(self, note_id, new_body):
        # Dollar-quote to avoid escaping hell on arbitrary body content.
        sql = f"UPDATE vault_notes SET body = $ENRICH${new_body}$ENRICH$, embedding = NULL WHERE id = '{note_id}'"
        self._cc_sql(sql)

    def _update_recent_activity_note(self, thread, entity_note_title, dry_run):
        if not thread.get("messages"):
            return False
        first_msg = thread["messages"][0]
        subject = (self._get_header(first_msg, "subject") or "?")[:80]
        sender = self._get_header(first_msg, "from") or "?"
        date_str = self._extract_date(first_msg) or datetime.date.today().isoformat()
        entry = f"- {date_str} -- {subject} (from {sender[:50]}) -- [thread](https://mail.google.com/mail/u/0/#all/{thread['id']})"

        note = self._get_note_body(entity_note_title)
        if not note:
            return False
        body = note["body"]

        if "## Recent activity" not in body:
            new_body = body.rstrip() + "\n\n## Recent activity\n\n" + entry + "\n"
        else:
            lines = body.split("\n")
            for i, line in enumerate(lines):
                if line.strip() == "## Recent activity":
                    lines.insert(i + 2, entry)
                    break
            new_body = "\n".join(lines)

        if dry_run:
            return True
        self._update_note_body(note["id"], new_body)
        return True

    def _extract_contacts_note(self, thread, entity_note_title, dry_run):
        added = []
        if not thread.get("messages"):
            return added

        note = self._get_note_body(entity_note_title)
        if not note:
            return added
        body = note["body"]

        senders = {}
        for msg in thread.get("messages", []):
            from_header = self._get_header(msg, "from") or ""
            email = self._extract_email(from_header)
            if not email or email.lower() in PETE_ADDRESSES:
                continue
            domain = email.split("@", 1)[-1].lower() if "@" in email else ""
            if domain in INTERNAL_DOMAINS:
                continue
            if email in senders:
                continue
            name = self._extract_name(from_header) or email.split("@")[0]
            msg_body = self._get_message_text(msg)
            phone = self._extract_phone(msg_body)
            role = self._extract_role(msg_body, name)
            senders[email] = {"name": name, "role": role, "phone": phone or ""}

        existing_emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", body.lower()))

        new_rows = []
        for email, info in senders.items():
            if email.lower() in existing_emails:
                continue
            row = f"| {info['name']} | {info['role'] or '(?)'} | {email} | {info['phone'] or ''} | auto-added by vault-enricher {datetime.date.today().isoformat()} |"
            new_rows.append(row)
            added.append(info["name"])

        if not new_rows or dry_run:
            return added

        new_body = self._append_to_contacts_table(body, new_rows)
        if new_body != body:
            self._update_note_body(note["id"], new_body)
        return added

    def _append_to_contacts_table(self, body, new_rows):
        lines = body.split("\n")
        in_section = False
        in_table = False
        last_table_line = -1

        for i, line in enumerate(lines):
            if line.strip().startswith("## Key contacts"):
                in_section = True
                continue
            if in_section and line.startswith("##") and not line.startswith("## Key"):
                break
            if in_section and "|" in line and "---" in line:
                in_table = True
                continue
            if in_table and line.strip().startswith("|"):
                last_table_line = i

        if last_table_line < 0:
            return body

        new_lines = lines[:last_table_line + 1] + new_rows + lines[last_table_line + 1:]
        return "\n".join(new_lines)

    # --- Helpers --------------------------------------------------------------

    def _get_header(self, message, name):
        for h in message.get("payload", {}).get("headers", []):
            if h["name"].lower() == name.lower():
                return h["value"]
        return None

    def _extract_date(self, message):
        d = self._get_header(message, "date")
        if not d:
            return None
        try:
            import email.utils
            dt = email.utils.parsedate_to_datetime(d)
            return dt.date().isoformat()
        except Exception:
            return None

    def _extract_email(self, header_value):
        m = re.search(r"<([^>]+@[^>]+)>", header_value)
        if m:
            return m.group(1).strip()
        m = re.search(r"([^\s,;<>]+@[^\s,;<>]+)", header_value)
        if m:
            return m.group(1).strip()
        return None

    def _extract_name(self, header_value):
        m = re.match(r'^"?([^"<]+?)"?\s*<', header_value)
        if m:
            return m.group(1).strip()
        return None

    def _extract_phone(self, body):
        sig_area = body[-500:] if len(body) > 500 else body
        m = re.search(r"(\+?\d[\d\s().-]{8,20})", sig_area)
        if m:
            return m.group(1).strip()
        return None

    def _extract_role(self, body, name):
        if not name:
            return None
        sig_area = body[-500:] if len(body) > 500 else body
        role_keywords = ["Manager", "Director", "Officer", "Lead", "Head", "Partner",
                         "Executive", "Coordinator", "Administrator", "Accountant",
                         "Broker", "Consultant", "Analyst", "Owner", "Founder", "CEO",
                         "CTO", "CFO", "COO"]
        for line in sig_area.split("\n"):
            for kw in role_keywords:
                if kw in line and len(line) < 100:
                    return line.strip()
        return None

    def _slugify(self, text):
        slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
        slug = re.sub(r"[\s_-]+", "-", slug)
        return slug[:50] or "untitled"

    def _b64url_decode(self, data):
        data = data.replace("-", "+").replace("_", "/")
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        try:
            return base64.b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 vault-enricher.py THREAD_ID /absolute/path/to/real/drive/folder [--dry-run] [--entity-note \"TITLE\"]")
        print("Example: python3 vault-enricher.py 19d2f55ceb8a021e "
              "\"$HOME/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/Shared drives/Sygma Hub/Projects/Team-Finances/files/SY-example\"")
        sys.exit(1)

    thread_id = sys.argv[1]
    target_dir = sys.argv[2]
    dry_run = "--dry-run" in sys.argv
    entity_note_title = None
    if "--entity-note" in sys.argv:
        entity_note_title = sys.argv[sys.argv.index("--entity-note") + 1]

    sys.stderr.write(
        "NOTE (canonical-home rule): is '%s' the CANONICAL, REAL Drive home for this "
        "content? Confirm against the drive_files index before running -- this tool no "
        "longer guesses a folder from an entity slug. See the CANONICAL-HOME RULE in this "
        "file's header.\n" % target_dir)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gmail_api",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail-api.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gmail_api"] = mod
    spec.loader.exec_module(mod)
    g = mod.GmailAPI()

    enricher = VaultEnricher(g)
    result = enricher.enrich_thread(thread_id, target_dir, dry_run=dry_run,
                                     entity_note_title=entity_note_title)
    import json
    print(json.dumps(result, indent=2))
