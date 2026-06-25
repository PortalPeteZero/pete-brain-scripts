"""
Triage ops-table validator.

Single import for inbox-triage skill execution scripts. Refuses to run a batch
that contains malformed rows -- specifically, the 27 April 2026 failure mode
where a tasked row was authored as `File ...` + a Task-column entry, causing the
tray label to be dropped on every tasked thread.

2026-06-25 — plain-word verbs (supersedes the 2026-06-06 "Action this / Task this"
split and the 2026-06-25 decoupling, which are folded in here). The verbs are now:

  * `Reply`              = tray verb — `Replies` label + filing label, atomic.
                          Reply-shaped asks (reply / rsvp / decision-by-replying).
                          NO task by default — the `Replies` label IS the record.
                          A Task cell is OPTIONAL: a filled Task cell makes it a
                          **Reply + Task** (the overlap — a reply gated on doing
                          work first). That task carries `[no-sync-close]` + the
                          Mimestream link (enforced at the create site, not here).
  * `Task Pn`            = work verb — filing label ONLY, no `Replies` label.
                          REQUIRES a Task cell. Its task carries `[no-sync-close]`.
  * `Hand to {person}`   = delegate — `Delegated` label + a chase task. REQUIRES a
                          Delegate cell.
  * `File {label}`       = filing label + archive (out of inbox).
  * `Keep {label}`       = filing label, LEAVE in inbox (you're watching it).
  * `Clear`              = archive, no label (pure noise). Archives, not deletes.
  * `Skip` / `-`         = no Gmail call (defer).

  One-sentence rule: `Replies` = waiting on Pete to respond by email; a task is
  created only when work is required, never automatically from the label.

  (The Gmail tray label was renamed `Actions` → `Replies` in the same 2026-06-25
  pass; the label keeps its ID, so only name-based searches changed.)

Usage:
    from triage_validator import validate_ops, TriageOpsError

    try:
        validate_ops(ops)
    except TriageOpsError as e:
        print(e)
        sys.exit(1)

The `ops` argument is a list of dicts with at minimum:
    {'row': int, 'action': str, 'task': str|None, 'delegate': str|None}

Optional `ask` key (one of none/info-only/reply/decision/review/rsvp) enables
the Ask⇔verb checks. Optional extra keys (`thread_id`, `label`, `vault`,
`calendar`) are not validated here.
"""
from __future__ import annotations
from typing import Iterable, Mapping, Any


class TriageOpsError(ValueError):
    """Raised when the ops table contains malformed rows."""


# Verbs that take NO argument (exact match).
_EXACT_VERBS = ('Clear', 'Skip', '-')
# Verb prefixes — the verb may be bare or carry a label / project / person / Pn after.
# 'Reply' has no trailing space so it matches both bare "Reply" and "Reply in {project}".
_PREFIX_VERBS = ('Reply', 'Task ', 'Hand to ', 'File ', 'Keep ')
_ALL_VERBS_DISPLAY = ('Reply', 'Task Pn', 'Hand to {person}', 'File {label}',
                      'Keep {label}', 'Clear', 'Skip', '-')

_VALID_ASKS = {'none', 'info-only', 'reply', 'decision', 'review', 'rsvp'}
_ACTIONABLE_ASKS = {'reply', 'decision', 'review', 'rsvp'}
_REPLY_SHAPED_ASKS = {'reply', 'rsvp'}


def _is_allowed_verb(action: str) -> bool:
    a = (action or '').strip()
    if a in _EXACT_VERBS:
        return True
    return any(a.startswith(p) for p in _PREFIX_VERBS)


def validate_ops(ops: Iterable[Mapping[str, Any]]) -> None:
    """
    Raise TriageOpsError if any row is malformed. Returns None on success.

    Checks:
      1. Action is one of the allowed verbs.
      2. Task cell present  ⇒  verb is `Reply` (combo) or `Task` (task-permitted).
      3. `Task` verb  ⇒  Task cell present. (`Reply` may carry a task — the overlap
         combo — but does NOT require one; a task-free Reply passes.)
      4. Delegate cell present  ⇒  verb is `Hand to`.
      5. `Hand to` verb  ⇒  Delegate cell present.
      6. (when `ask` present) Ask vocabulary + Ask⇔verb matrix:
         - reply/rsvp ⇒ verb must be `Reply` or `Hand to`
           (`Task` on a reply-shaped Ask is the transition-guard case: flag to
           Pete once, only proceed on his explicit override)
         - decision/review ⇒ verb must be `Reply` / `Task` / `Hand to`
         - an actionable verb ⇒ ask must be actionable
    """
    errors: list[str] = []

    for op in ops:
        row = op.get('row', '?')
        action = (op.get('action') or '').strip()
        task = (op.get('task') or '').strip()
        delegate = (op.get('delegate') or '').strip()
        ask = (op.get('ask') or '').strip()

        # Treat '-' as empty for cell content checks
        has_task = bool(task) and task != '-'
        has_delegate = bool(delegate) and delegate != '-'

        # Verb classification:
        #   is_reply_verb     — `Reply` (tray). A Task cell is OPTIONAL (combo).
        #   is_task_verb      — `Task`  (work). A Task cell is REQUIRED.
        #   is_hand_verb      — `Hand to` (delegate). A Delegate cell is REQUIRED.
        is_reply_verb = action.startswith('Reply')
        is_task_verb = action.startswith('Task ')
        is_hand_verb = action.startswith('Hand to ')
        is_task_permitted = is_reply_verb or is_task_verb   # a Task cell may appear
        is_task_required = is_task_verb                     # a Task cell MUST appear
        is_actionable_verb = is_reply_verb or is_task_verb or is_hand_verb

        # 1. Allowed-verb rule
        if not _is_allowed_verb(action):
            errors.append(
                f"Row {row}: Action '{action}' is not one of the allowed verbs "
                f"({', '.join(_ALL_VERBS_DISPLAY)})"
            )

        # 2. A Task cell may only appear on `Reply` (combo) or `Task` (catches the
        #    27-Apr 'File X' + Task-cell failure that dropped the tray label).
        if has_task and not is_task_permitted:
            errors.append(
                f"Row {row}: Task cell present ('{task[:60]}...') but Action is '{action}' -- "
                f"row is malformed. Put the task on a `Reply` (combo) or `Task Pn` verb, or "
                f"clear the Task cell."
            )
        # 3. Only `Task` REQUIRES a Task cell. `Reply` is label-only by default
        #    (a task is optional — the overlap combo), so it is exempt.
        if is_task_required and not has_task:
            errors.append(
                f"Row {row}: Action is '{action}' but Task cell is empty -- row is malformed. "
                f"Either fill in the Task spec or change the verb."
            )

        # 4 + 5. Delegate atomicity (`Hand to`)
        if has_delegate and not is_hand_verb:
            errors.append(
                f"Row {row}: Delegate cell present ('{delegate[:60]}...') but Action is '{action}' -- malformed."
            )
        if is_hand_verb and not has_delegate:
            errors.append(
                f"Row {row}: Action is '{action}' but Delegate cell is empty -- malformed."
            )

        # 6. Ask checks (only when the caller supplies an ask)
        if ask:
            if ask not in _VALID_ASKS:
                errors.append(f"Row {row}: Ask '{ask}' not in vocabulary {sorted(_VALID_ASKS)}")
            else:
                actionable = ask in _ACTIONABLE_ASKS
                if actionable and not is_actionable_verb:
                    errors.append(
                        f"Row {row}: Ask='{ask}' implies action but verb is '{action}' -- malformed"
                    )
                if ask in _REPLY_SHAPED_ASKS and is_task_verb and not op.get('pete_override'):
                    errors.append(
                        f"Row {row}: Ask='{ask}' is reply-shaped — verb should be 'Reply' or "
                        f"'Hand to'. If Pete explicitly chose Task, set pete_override=True after "
                        f"flagging once (transition guard)."
                    )
                if is_actionable_verb and not actionable:
                    errors.append(
                        f"Row {row}: verb is '{action}' but Ask='{ask}' doesn't imply action -- malformed"
                    )

    if errors:
        raise TriageOpsError(
            f"Ops table malformed -- {len(errors)} error(s). Refusing to execute.\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def verb_to_primitive(action: str, label_id: str | None, replies_label_id: str | None,
                      delegated_label_id: str | None) -> dict | None:
    """
    Map a row's verb + label IDs to the EXACT modify_thread kwargs.

    Returns None for Skip / '-'. Raises TriageOpsError for unknown verbs.

    `Reply` is the canonical atomic tray form — the `Replies` label in the same
    call as the filing label. `Task` deliberately does NOT include the `Replies`
    label (its task carries [no-sync-close]). A Reply + Task combo uses the SAME
    `Reply` primitive here — the prep task is created separately by the skill, it
    doesn't change the label call. Use this helper to BUILD the modify_thread
    call; never construct the add/remove lists by hand at the call site.
    """
    a = (action or '').strip()

    if a in ('Skip', '-'):
        return None

    if a == 'Clear':
        return {'remove': ['INBOX']}

    if a.startswith('File '):
        if not label_id:
            raise TriageOpsError("verb_to_primitive: 'File' requires label_id")
        return {'add': [label_id], 'remove': ['INBOX']}

    if a.startswith('Keep '):
        if not label_id:
            raise TriageOpsError("verb_to_primitive: 'Keep' requires label_id")
        return {'add': [label_id]}  # leave INBOX — Pete is watching it

    if a.startswith('Reply'):
        if not label_id or not replies_label_id:
            raise TriageOpsError(
                "verb_to_primitive: 'Reply' requires BOTH label_id AND replies_label_id "
                "(atomicity rule -- Replies must be in the same call as the filing label)"
            )
        return {'add': [label_id, replies_label_id], 'remove': ['INBOX']}

    if a.startswith('Task '):
        if not label_id:
            raise TriageOpsError("verb_to_primitive: 'Task' requires label_id")
        # NO Replies label — work item; its task carries [no-sync-close]
        return {'add': [label_id], 'remove': ['INBOX']}

    if a.startswith('Hand to '):
        if not delegated_label_id:
            raise TriageOpsError("verb_to_primitive: 'Hand to' requires delegated_label_id")
        return {'add': [delegated_label_id]}

    raise TriageOpsError(f"verb_to_primitive: unknown verb '{a}'")


# ---- self-test ------------------------------------------------------------

if __name__ == '__main__':
    # Smoke tests
    good = [
        {'row': 1, 'action': 'Clear'},
        {'row': 2, 'action': 'File Receipts'},
        {'row': 3, 'action': 'Reply in SY-General', 'ask': 'reply'},                         # reply only — no task, passes
        {'row': 4, 'action': 'Reply in SY-Survey', 'task': 'Build the quote first',
         'ask': 'decision'},                                                                 # Reply + Task (combo) — passes
        {'row': 5, 'action': 'Task P2 in Team-Finances', 'task': 'Pay invoice 123', 'ask': 'decision'},
        {'row': 6, 'action': 'Hand to Jane', 'delegate': 'Jane to chase', 'ask': 'reply'},
        {'row': 7, 'action': 'Skip'},
        {'row': 8, 'action': 'Keep Alerts'},
        {'row': 9, 'action': 'Task P1 in SY-General', 'task': 'Reply-shaped but Pete insisted',
         'ask': 'reply', 'pete_override': True},
    ]
    validate_ops(good)
    print("✓ good ops table passes (incl. task-free Reply + the Reply+Task combo)")

    bad = [
        {'row': 1, 'action': 'File SY-General', 'task': 'should not be here'},               # task cell on File
        {'row': 2, 'action': 'Task P2 in SY-General', 'task': 'Reply to Bob', 'ask': 'reply'},  # reply-shaped on Task, no override
        {'row': 3, 'action': 'Task P2 in Team-Finances', 'ask': 'decision'},                 # Task with NO task cell
    ]
    try:
        validate_ops(bad)
        print("✗ should have raised")
    except TriageOpsError as e:
        print(f"✓ bad ops table caught:\n{e}")

    # targeted asserts
    validate_ops([{'row': 1, 'action': 'Reply in PA-General', 'ask': 'reply'}])              # no task → must pass
    print("✓ task-free 'Reply' passes (label-only)")
    validate_ops([{'row': 1, 'action': 'Reply in SY-Survey', 'task': 'build quote', 'ask': 'decision'}])
    print("✓ 'Reply + Task' combo passes (Reply with an optional task cell)")
    try:
        validate_ops([{'row': 1, 'action': 'Task P2 in Team-Finances', 'ask': 'decision'}])  # no task → must fail
        print("✗ task-free 'Task' should have raised")
    except TriageOpsError:
        print("✓ task-free 'Task' still rejected (task still required)")

    # verb_to_primitive
    p = verb_to_primitive('Reply in CD-Invoices', 'L_filing', 'L_replies', None)
    assert p == {'add': ['L_filing', 'L_replies'], 'remove': ['INBOX']}, p
    print(f"✓ Reply produces atomic add=[filing, Replies]: {p}")

    p = verb_to_primitive('Task P1 in Team-Finances', 'L_filing', 'L_replies', None)
    assert p == {'add': ['L_filing'], 'remove': ['INBOX']}, p
    print(f"✓ Task adds filing ONLY (no Replies): {p}")

    p = verb_to_primitive('File Receipts', 'L_receipts', 'L_replies', None)
    assert p == {'add': ['L_receipts'], 'remove': ['INBOX']}, p
    print(f"✓ File does NOT add Replies: {p}")

    p = verb_to_primitive('Keep Alerts', 'L_alerts', 'L_replies', None)
    assert p == {'add': ['L_alerts']}, p
    print(f"✓ Keep adds the label but leaves INBOX: {p}")

    p = verb_to_primitive('Hand to Jane', 'L_filing', 'L_replies', 'L_delegated')
    assert p == {'add': ['L_delegated']}, p
    print(f"✓ Hand to adds Delegated: {p}")

    p = verb_to_primitive('Clear', None, None, None)
    assert p == {'remove': ['INBOX']}, p
    print(f"✓ Clear archives with no label: {p}")
