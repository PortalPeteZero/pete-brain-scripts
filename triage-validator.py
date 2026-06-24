"""
Triage ops-table validator.

Single import for inbox-triage skill execution scripts. Refuses to run a batch
that contains malformed rows -- specifically, the 27 April 2026 failure mode
where a `Task this` row was authored as `File under X` + a Task-column entry,
causing the Actions label to be dropped on every tasked thread.

2026-06-06 — Action/Task verb split (plan: Projects/PA-General/files/
email-workflow-plan-2026-06-06-action-task-split.md):
  * `Action this Pn` = tray verb — Actions label + filing label, atomic.
    Reply-shaped asks only (reply / rsvp / decision-by-replying).
  * `Task this Pn`   = Asana-only verb — filing label ONLY, no Actions label.
    The created task's notes must carry `[no-sync-close]` (enforced at the
    task-creation call site, not here — this validator sees only the table).
  One-sentence rule: Actions = waiting on Pete to respond by email.
  Everything else = Asana only.

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


_ALLOWED_PREFIXES = (
    'File under ',
    'Keep in inbox + label ',
    'Silent archive',
    'Skip',
    '-',
    'Action this ',
    'Task this ',
    'Delegate to ',
)

_VALID_ASKS = {'none', 'info-only', 'reply', 'decision', 'review', 'rsvp'}
_ACTIONABLE_ASKS = {'reply', 'decision', 'review', 'rsvp'}
_REPLY_SHAPED_ASKS = {'reply', 'rsvp'}


def _is_allowed_verb(action: str) -> bool:
    a = (action or '').strip()
    if a in ('Silent archive', 'Skip', '-'):
        return True
    return any(a.startswith(p) for p in _ALLOWED_PREFIXES if p.endswith(' '))


def validate_ops(ops: Iterable[Mapping[str, Any]]) -> None:
    """
    Raise TriageOpsError if any row is malformed. Returns None on success.

    Checks:
      1. Action is one of the seven allowed verbs.
      2. Task cell present  ⇒  Action begins with 'Action this ' or 'Task this '.
      3. Action/Task verb  ⇒  Task cell present.
      4. Delegate cell present  ⇒  Action begins with 'Delegate to '.
      5. Action begins with 'Delegate to '  ⇒  Delegate cell present.
      6. (when `ask` present) Ask vocabulary + Ask⇔verb matrix:
         - reply/rsvp ⇒ verb must be Action this / Delegate to
           (Task this on a reply-shaped Ask is the transition-guard case:
           flag to Pete once, only proceed on his explicit override)
         - decision/review ⇒ verb must be Action this / Task this / Delegate to
         - task-bearing verb ⇒ ask must be actionable
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

        is_task_bearing = action.startswith(('Action this ', 'Task this '))
        is_delegate_verb = action.startswith('Delegate to ')

        # 1. Seven-verb rule
        if not _is_allowed_verb(action):
            errors.append(
                f"Row {row}: Action '{action}' is not one of the seven allowed verbs "
                f"({', '.join(p.strip() for p in _ALLOWED_PREFIXES)})"
            )

        # 2 + 3. Task atomicity (both task-bearing verbs)
        if has_task and not is_task_bearing:
            errors.append(
                f"Row {row}: Task cell present ('{task[:60]}...') but Action is '{action}' -- "
                f"row is malformed. Either change Action to 'Action this Pn' / 'Task this Pn' or "
                f"clear the Task cell."
            )
        if is_task_bearing and not has_task:
            errors.append(
                f"Row {row}: Action is '{action}' but Task cell is empty -- row is malformed. "
                f"Either fill in the Task spec or change the verb."
            )

        # 4 + 5. Delegate atomicity
        if has_delegate and not is_delegate_verb:
            errors.append(
                f"Row {row}: Delegate cell present ('{delegate[:60]}...') but Action is '{action}' -- malformed."
            )
        if is_delegate_verb and not has_delegate:
            errors.append(
                f"Row {row}: Action is '{action}' but Delegate cell is empty -- malformed."
            )

        # 6. Ask checks (only when the caller supplies an ask)
        if ask:
            if ask not in _VALID_ASKS:
                errors.append(f"Row {row}: Ask '{ask}' not in vocabulary {sorted(_VALID_ASKS)}")
            else:
                actionable = ask in _ACTIONABLE_ASKS
                if actionable and not (is_task_bearing or is_delegate_verb):
                    errors.append(
                        f"Row {row}: Ask='{ask}' implies action but verb is '{action}' -- malformed"
                    )
                if ask in _REPLY_SHAPED_ASKS and action.startswith('Task this ') \
                        and not op.get('pete_override'):
                    errors.append(
                        f"Row {row}: Ask='{ask}' is reply-shaped — verb should be 'Action this' or "
                        f"'Delegate to'. If Pete explicitly chose Task, set pete_override=True after "
                        f"flagging once (transition guard)."
                    )
                if (is_task_bearing or is_delegate_verb) and not actionable:
                    errors.append(
                        f"Row {row}: verb is '{action}' but Ask='{ask}' doesn't imply action -- malformed"
                    )

    if errors:
        raise TriageOpsError(
            f"Ops table malformed -- {len(errors)} error(s). Refusing to execute.\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def verb_to_primitive(action: str, label_id: str | None, actions_label_id: str | None,
                      delegated_label_id: str | None) -> dict | None:
    """
    Map a row's verb + label IDs to the EXACT modify_thread kwargs.

    Returns None for Skip / '-'. Raises TriageOpsError for unknown verbs.

    `Action this` is the canonical atomic tray form — Actions label in the same
    call as the filing label. `Task this` deliberately does NOT include the
    Actions label (Asana-only class; its task carries [no-sync-close]).
    Use this helper to BUILD the modify_thread call; never construct the
    add/remove lists by hand at the call site.
    """
    a = (action or '').strip()

    if a in ('Skip', '-'):
        return None

    if a == 'Silent archive':
        return {'remove': ['INBOX']}

    if a.startswith('File under '):
        if not label_id:
            raise TriageOpsError(f"verb_to_primitive: 'File under' requires label_id")
        return {'add': [label_id], 'remove': ['INBOX']}

    if a.startswith('Keep in inbox + label '):
        if not label_id:
            raise TriageOpsError(f"verb_to_primitive: 'Keep in inbox + label' requires label_id")
        return {'add': [label_id]}

    if a.startswith('Action this '):
        if not label_id or not actions_label_id:
            raise TriageOpsError(
                f"verb_to_primitive: 'Action this' requires BOTH label_id AND actions_label_id "
                f"(atomicity rule -- Actions must be in the same call as the filing label)"
            )
        return {'add': [label_id, actions_label_id], 'remove': ['INBOX']}

    if a.startswith('Task this '):
        if not label_id:
            raise TriageOpsError(f"verb_to_primitive: 'Task this' requires label_id")
        # NO Actions label — Asana-only class (2026-06-06 split)
        return {'add': [label_id], 'remove': ['INBOX']}

    if a.startswith('Delegate to '):
        if not delegated_label_id:
            raise TriageOpsError(f"verb_to_primitive: 'Delegate to' requires delegated_label_id")
        return {'add': [delegated_label_id]}

    raise TriageOpsError(f"verb_to_primitive: unknown verb '{a}'")


# ---- self-test ------------------------------------------------------------

if __name__ == '__main__':
    # Smoke tests
    good = [
        {'row': 1, 'action': 'Silent archive'},
        {'row': 2, 'action': 'File under Receipts'},
        {'row': 3, 'action': 'Action this P2 in SY-General', 'task': 'Reply to whoever', 'ask': 'reply'},
        {'row': 4, 'action': 'Task this P2 in Team-Finances', 'task': 'Pay invoice 123', 'ask': 'decision'},
        {'row': 5, 'action': 'Delegate to Jane', 'delegate': 'Jane to chase', 'ask': 'reply'},
        {'row': 6, 'action': 'Skip'},
        {'row': 7, 'action': 'Keep in inbox + label Alerts'},
        {'row': 8, 'action': 'Task this P1 in SY-General', 'task': 'Reply-shaped but Pete insisted',
         'ask': 'reply', 'pete_override': True},
    ]
    validate_ops(good)
    print("✓ good ops table passes")

    bad = [
        {'row': 1, 'action': 'File under SY-General', 'task': 'P2 in SY-General "Reply"'},  # the 27 Apr failure
        {'row': 2, 'action': 'Task this P2 in SY-General', 'task': 'Reply to Bob', 'ask': 'reply'},  # reply-shaped on Task, no override
    ]
    try:
        validate_ops(bad)
        print("✗ should have raised")
    except TriageOpsError as e:
        print(f"✓ bad ops table caught:\n{e}")

    # verb_to_primitive
    p = verb_to_primitive('Action this P1 in CD-Invoices', 'L_filing', 'L_actions', None)
    assert p == {'add': ['L_filing', 'L_actions'], 'remove': ['INBOX']}, p
    print(f"✓ Action verb produces atomic add=[filing, Actions]: {p}")

    p = verb_to_primitive('Task this P1 in Team-Finances', 'L_filing', 'L_actions', None)
    assert p == {'add': ['L_filing'], 'remove': ['INBOX']}, p
    print(f"✓ Task verb adds filing ONLY (no Actions): {p}")

    p = verb_to_primitive('File under Receipts', 'L_receipts', 'L_actions', None)
    assert p == {'add': ['L_receipts'], 'remove': ['INBOX']}, p
    print(f"✓ File verb does NOT add Actions: {p}")
