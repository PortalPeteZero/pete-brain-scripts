#!/usr/bin/env python3
"""
Vault enricher v1.1 -- shared content-extraction helper for inbox-triage and email-task-sync.

[BUSINESS OS — REDESIGN PENDING (2026-06-22, Part D).] Still enriches the legacy
VAULT customer/supplier file. Target must move to the Drive home + the CC
`vault_notes` record (H/E). Still called by inbox-triage + email-task-sync —
keep working off the (still-present) vault files until redesigned. Ledger:
Projects/PA-Command-Centre/files/part-d-reference-repoint-ledger-2026-06-22.md

v1.1 (2026-04-25): Contacts now write to the CUSTOMER-LEVEL README, not the matter-level README.
                   Walks up the path from target_folder until it hits a folder whose parent is
                   `Customers/` or `Suppliers/`, then writes contacts to that folder's README.md.
                   Recent activity continues to write to the target (matter-level) README.
                   Reason: matter READMEs don't carry a `## Key contacts` table; the customer
                   README is the single source for the relationship's contact list.

Closes the "keep vault in sync with Gmail" gap (Issue 14 of email-system-iteration-2026-04-25).

When triage processes an email OR sync auto-creates a task from a manually-Actioned
thread, this helper pulls the substantive content of the thread INTO the relevant
vault folder. Attachments to source/. Body extracts to extracts/. Contacts auto-added
to README's Key contacts table. Recent activity logged.

Skip rules baked in: PA-General + operational labels + signature cruft never enriched.

================================ CANONICAL-HOME RULE ================================
(added 2026-06-14, Pete-directed after a dumb misfile)

This helper pulls files into WHATEVER `target_folder` you pass. It does NOT know the
canonical home for the content. So BEFORE you call it:

  1. SEARCH MAP.md + the vault for the ESTABLISHED home of this content type. Do not
     dump into the first plausible folder. Where do prior items of this kind already live?
  2. Confirm the PRIVACY TIER is right (family tax / financial → Personal/family/Finance,
     NOT a supplier folder; salary/payroll → owner-private; etc.).
  3. Re-run `vault-drift-check.py --map-only` AFTER the pull, not before, and update MAP.

Failure that prompted this: the Renta 2025 tax returns were enriched into
`Suppliers/AT-Clarity-Lanzarote/source/` when the canonical, more-private home
`Personal/family/Finance/Spanish Tax Return YYYY/` already existed (prior year's returns +
this year's WIP lived there). This is the [[vault-writer]] "search first, then write"
golden rule — applied to file-pulls.
====================================================================================

Usage (library):
    from vault_enricher import VaultEnricher
    e = VaultEnricher(gmail_helper)  # gmail_helper is a GmailAPI instance
    result = e.enrich_thread(thread_id, target_folder, dry_run=False)
    # result = {"attachments_pulled": [...], "extract_path": "...", "contacts_added": [...]}

Idempotent: re-running on the same thread is safe (skips files that already exist).
Dry-run: pass dry_run=True to log intended operations without writing.

CLI:
    python3 vault-enricher.py THREAD_ID TARGET_FOLDER [--dry-run]
"""

from __future__ import annotations
import base64
import os
import re
import sys
import datetime
from pathlib import Path

# ============================================================
# SKIP RULES (hard-coded -- v1.0)
# ============================================================

# Filing labels that should NEVER trigger enrichment (per Pete decision)
SKIP_FILING_LABELS = frozenset({
    "General/PA-General",        # Pete personal -- explicitly skipped
})

# Operational labels -- pure noise, no enrichment value
SKIP_OPERATIONAL_LABELS = frozenset({
    "Travel", "Receipts", "Shipping", "Newsletters",
    "Voice-Mail", "Voicemail", "Briefings", "Alerts",
})

# Gmail system labels -- never count as filing
GMAIL_SYSTEM_LABELS = frozenset({
    "INBOX", "SENT", "TRASH", "SPAM", "STARRED", "IMPORTANT",
    "UNREAD", "DRAFT", "CHAT", "Snoozed",
})

# Attachment filename patterns to SKIP (signature cruft)
SKIP_ATTACHMENT_PATTERNS = [
    re.compile(r"^image\d+\.(png|jpg|jpeg|gif)$", re.IGNORECASE),
    re.compile(r"^smime\.p7s$", re.IGNORECASE),
    re.compile(r"\.ics$", re.IGNORECASE),  # calendar invites -- separate concern
    re.compile(r"^untitled.*$", re.IGNORECASE),
]

# Signature/footer markers to truncate body at (keep content above the marker only)
SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$", re.MULTILINE),                   # standard sig delimiter
    re.compile(r"^Sent from my (iPhone|iPad|Android)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^This email and any attachments", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^CONFIDENTIALITY NOTICE", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^DISCLAIMER:", re.MULTILINE | re.IGNORECASE),
]

# Auto-reply / bot subject patterns -- skip enrichment entirely
AUTO_REPLY_PATTERNS = [
    re.compile(r"^(Out of Office|Auto.*Reply|Automatic Reply|Vacation)", re.IGNORECASE),
    re.compile(r"^Delivery Status Notification", re.IGNORECASE),
    re.compile(r"^Mail Delivery", re.IGNORECASE),
]

# Pete's own addresses -- don't add to contact tables (he's not an external contact)
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


# ============================================================
# MAIN ENRICHER CLASS
# ============================================================

class VaultEnricher:
    """Pulls Gmail thread content into the vault.

    Constructor takes a GmailAPI instance (the helper from gmail-api.py).
    Main method: enrich_thread(thread_id, target_folder, dry_run).
    """

    def __init__(self, gmail_helper, vault_root=None):
        # vault_root resolution: explicit arg > VAULT_ROOT env > host default.
        # Lets the script run both on Pete's Mac (host path) and in the Cowork sandbox
        # (mounted at /sessions/{name}/mnt/Command Centre) without code edits.
        if vault_root is None:
            vault_root = os.environ.get("VAULT_ROOT", "/tmp/pbs")
        self.g = gmail_helper
        self.vault_root = Path(vault_root)
        self._labels_cache = None  # populated on first lookup

    # --- Public entry point ---------------------------------------------------

    def enrich_thread(self, thread_id, target_folder, dry_run=False):
        """Enrich the vault folder with content from the thread.

        Args:
            thread_id: Gmail thread ID
            target_folder: vault folder relative to vault_root (e.g. "Suppliers/CD-Gazette-Life")
            dry_run: if True, log intended operations without writing

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

        # Skip-rule pre-checks
        thread = self.g.get_thread(thread_id)
        skip_check = self._should_skip(thread, target_folder)
        if skip_check:
            result["skipped"] = True
            result["skip_reason"] = skip_check
            return result

        target_path = self.vault_root / target_folder
        if not target_path.exists() and not dry_run:
            # Create target if it doesn't exist (Pete confirms via routing)
            target_path.mkdir(parents=True, exist_ok=True)

        # Sub-passes
        result["attachments_pulled"] = self._pull_attachments(thread, target_path, dry_run)
        result["extract_path"] = self._extract_body(thread, target_path, dry_run)

        # Recent activity goes to the matter README (the target itself)
        readme_path = target_path / "README.md"
        if readme_path.exists():
            result["recent_activity_appended"] = self._update_recent_activity(
                thread, readme_path, dry_run
            )

        # Contacts go to the customer-level README (walks up if target is a matter)
        customer_readme = self._find_customer_readme(target_path)
        if customer_readme and customer_readme.exists():
            result["contacts_added"] = self._extract_contacts(thread, customer_readme, dry_run)

        return result

    def _find_customer_readme(self, target_path):
        """Walk up from target_path until we hit a folder whose parent is `Customers/` or `Suppliers/`.
        Return that folder's README.md (the customer/supplier root README).
        Returns None if target is outside Customers/Suppliers (e.g. Projects, Businesses).
        """
        p = target_path
        while p != self.vault_root and p != p.parent:
            if p.parent.name in ("Customers", "Suppliers"):
                return p / "README.md"
            p = p.parent
        return None

    # --- Skip rules -----------------------------------------------------------

    def _should_skip(self, thread, target_folder):
        """Return skip reason string, or None if enrichment should proceed."""
        # PA-General target = explicit skip per Pete
        if "PA-General" in target_folder:
            return "PA-General target -- enrichment skipped per design"

        # Check thread's labels for operational-only labels
        label_names = self._thread_label_names(thread)
        user_labels = [n for n in label_names if self._is_user_label(n)]

        if not user_labels:
            return "thread has no user-applied labels (unfiled, won't enrich)"

        # If thread's ONLY user labels are operational ones, skip
        non_operational = [n for n in user_labels if n not in SKIP_OPERATIONAL_LABELS]
        if not non_operational:
            return f"thread has only operational labels ({user_labels}) -- no enrichment"

        # Auto-reply / bot subject -> skip
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

    # --- Attachment pulling ---------------------------------------------------

    def _pull_attachments(self, thread, target_path, dry_run):
        """Save thread attachments to target_path/source/ -- skip cruft + idempotent."""
        source_dir = target_path / "source"
        pulled = []
        for msg in thread.get("messages", []):
            msg_id = msg["id"]
            attachments = self._list_attachments_in_message(msg)
            for att in attachments:
                fname = att["filename"]
                if not fname:
                    continue
                # Skip cruft
                if any(p.match(fname) for p in SKIP_ATTACHMENT_PATTERNS):
                    continue
                save_path = source_dir / fname
                # Idempotent: skip if already present
                if save_path.exists():
                    continue
                if not dry_run:
                    source_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        self.g.download_attachment(msg_id, att["attachmentId"], str(save_path))
                    except Exception as e:
                        print(f"[vault-enricher] WARN: attachment {fname} failed: {e}",
                              file=sys.stderr)
                        continue
                pulled.append(str(save_path.relative_to(self.vault_root)))
        return pulled

    def _list_attachments_in_message(self, message):
        """Walk message parts, collect attachment metadata."""
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

    # --- Body extraction ------------------------------------------------------

    def _extract_body(self, thread, target_path, dry_run):
        """Write a Markdown extract of the thread to target_path/extracts/.

        Returns extract path (relative to vault_root) or None if nothing extracted.
        """
        extracts_dir = target_path / "extracts"

        # Use first message for slug + date; aggregate body across all messages
        if not thread.get("messages"):
            return None
        first_msg = thread["messages"][0]
        subject = (self._get_header(first_msg, "subject") or "no-subject")[:60]
        date_str = self._extract_date(first_msg) or datetime.date.today().isoformat()

        slug = self._slugify(subject)
        extract_filename = f"{date_str}-{slug}.md"
        extract_path = extracts_dir / extract_filename

        # Idempotent: skip if exists
        if extract_path.exists():
            return None

        # Build the extract body
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
                continue  # skip empty / trivial messages
            sections.append(f"## Message {i+1} -- {msg_date} -- from {sender[:60]}")
            sections.append("")
            sections.append(body[:2000])  # cap each message at 2000 chars
            sections.append("")

        if len(sections) < 11:  # only frontmatter + title, no real content
            return None

        content = "\n".join(sections)
        if not dry_run:
            extracts_dir.mkdir(parents=True, exist_ok=True)
            extract_path.write_text(content, encoding="utf-8")
        return str(extract_path.relative_to(self.vault_root))

    def _get_message_text(self, message):
        """Extract plain-text body from a message (prefer text/plain over text/html)."""
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
            # Fallback to top-level body if no parts
            data = payload.get("body", {}).get("data")
            if data:
                text_parts.append(self._b64url_decode(data))
        return "\n".join(text_parts)

    def _strip_signature(self, body):
        """Truncate body at first signature marker."""
        for pat in SIGNATURE_MARKERS:
            m = pat.search(body)
            if m:
                body = body[:m.start()].rstrip()
                break
        return body

    def _collapse_whitespace(self, text):
        """Reduce 3+ blank lines to 2, trim."""
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # --- Contact extraction ---------------------------------------------------

    def _extract_contacts(self, thread, readme_path, dry_run):
        """Auto-add new contacts to the README's `## Key contacts` table.

        Per Pete: "no need to propose, just add". Auto-adds; Pete cleans manually if wrong.
        """
        added = []
        if not thread.get("messages"):
            return added

        # Read README, find the contacts table
        try:
            readme_text = readme_path.read_text(encoding="utf-8")
        except Exception:
            return added

        # Collect unique senders from thread
        senders = {}  # email -> {name, role, phone, source}
        for msg in thread.get("messages", []):
            from_header = self._get_header(msg, "from") or ""
            email = self._extract_email(from_header)
            if not email or email.lower() in PETE_ADDRESSES:
                continue
            # Skip internal-domain senders: they're Sygma staff CC'd on supplier/customer
            # threads, not third-party contacts. Adding them to a Suppliers/{X}/README
            # Key Contacts table pollutes the supplier's contact record (caught 2026-05-20
            # when Michaela got auto-added to Suppliers/SY-EU-Skills as a (?) role + truncated phone).
            domain = email.split("@", 1)[-1].lower() if "@" in email else ""
            if domain in INTERNAL_DOMAINS:
                continue
            if email in senders:
                continue
            name = self._extract_name(from_header) or email.split("@")[0]
            # Look at body for sig info
            body = self._get_message_text(msg)
            phone = self._extract_phone(body)
            role = self._extract_role(body, name)
            senders[email] = {
                "name": name,
                "role": role,
                "phone": phone or "",
            }

        # Find existing contacts in README to dedupe
        existing_emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", readme_text.lower()))

        new_rows = []
        for email, info in senders.items():
            if email.lower() in existing_emails:
                continue
            row = f"| {info['name']} | {info['role'] or '(?)'} | {email} | {info['phone'] or ''} | auto-added by vault-enricher {datetime.date.today().isoformat()} |"
            new_rows.append(row)
            added.append(info["name"])

        if not new_rows:
            return added

        # Append new rows to the contacts table -- find the table marker, insert before next section
        if dry_run:
            return added

        new_text = self._append_to_contacts_table(readme_text, new_rows)
        if new_text != readme_text:
            readme_path.write_text(new_text, encoding="utf-8")
        return added

    def _append_to_contacts_table(self, readme_text, new_rows):
        """Insert new rows into the `## Key contacts` table."""
        lines = readme_text.split("\n")
        in_section = False
        in_table = False
        last_table_line = -1

        for i, line in enumerate(lines):
            if line.strip().startswith("## Key contacts"):
                in_section = True
                continue
            if in_section and line.startswith("##") and not line.startswith("## Key"):
                # Hit the next section -- table ended above
                break
            if in_section and "|" in line and "---" in line:
                in_table = True
                continue
            if in_table and line.strip().startswith("|"):
                last_table_line = i

        if last_table_line < 0:
            return readme_text  # no table found, don't insert

        # Insert new rows after last_table_line
        new_lines = lines[:last_table_line + 1] + new_rows + lines[last_table_line + 1:]
        return "\n".join(new_lines)

    # --- Recent activity append ----------------------------------------------

    def _update_recent_activity(self, thread, readme_path, dry_run):
        """Append a one-line activity entry to the README's Recent activity section.

        v1.0: simple append. Future: keep last 10, rotate older to a 'historical' section.
        """
        if not thread.get("messages"):
            return False
        first_msg = thread["messages"][0]
        subject = (self._get_header(first_msg, "subject") or "?")[:80]
        sender = self._get_header(first_msg, "from") or "?"
        date_str = self._extract_date(first_msg) or datetime.date.today().isoformat()
        entry = f"- {date_str} -- {subject} (from {sender[:50]}) -- [thread]({'https://mail.google.com/mail/u/0/#all/' + thread['id']})"

        try:
            readme_text = readme_path.read_text(encoding="utf-8")
        except Exception:
            return False

        if "## Recent activity" not in readme_text:
            # Append section + entry
            new_text = readme_text.rstrip() + "\n\n## Recent activity\n\n" + entry + "\n"
        else:
            # Insert entry below section heading (newest first)
            lines = readme_text.split("\n")
            for i, line in enumerate(lines):
                if line.strip() == "## Recent activity":
                    lines.insert(i + 2, entry)
                    break
            new_text = "\n".join(lines)

        if dry_run:
            return True
        readme_path.write_text(new_text, encoding="utf-8")
        return True

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
        # Look for UK or international phone in signature area (last 500 chars)
        sig_area = body[-500:] if len(body) > 500 else body
        m = re.search(r"(\+?\d[\d\s().-]{8,20})", sig_area)
        if m:
            return m.group(1).strip()
        return None

    def _extract_role(self, body, name):
        # v1.0 heuristic: look for line right after the name with a job-title-like word
        if not name:
            return None
        sig_area = body[-500:] if len(body) > 500 else body
        # Common role keywords
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
        # URL-safe base64 with padding
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
        print("Usage: python3 vault-enricher.py THREAD_ID TARGET_FOLDER [--dry-run]")
        print("Example: python3 vault-enricher.py 19d2f55ceb8a021e Customers/SY-Severn-Trent-Water")
        sys.exit(1)

    thread_id = sys.argv[1]
    target_folder = sys.argv[2]
    dry_run = "--dry-run" in sys.argv

    # CANONICAL-HOME guardrail (Pete-directed, 2026-06-14): the CLI path is the manual-
    # invocation case where misfiling happens. Remind the caller before any write.
    sys.stderr.write(
        "NOTE (canonical-home rule): is '%s' the CANONICAL home for this content? "
        "Search MAP.md + the vault for where prior items of this kind live, and check the "
        "privacy tier (family/financial belongs in Personal/family/Finance, salary in "
        "owner-private, NOT a supplier folder). See the CANONICAL-HOME RULE in this file's "
        "header.\n" % target_folder)

    # Load gmail-api helper
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
    result = enricher.enrich_thread(thread_id, target_folder, dry_run=dry_run)
    import json
    print(json.dumps(result, indent=2))
