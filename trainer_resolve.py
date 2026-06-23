"""trainer_resolve.py — the ONE place trainer names are matched, so the two-Andys bug can't recur.

The master sheet + curated docs write trainer names in mixed forms ("Andy", "Andrew", "Andrew
Bartholomew", "Steve Mellor"). Several pairs collide on first name (Andrew FOSTER vs Andy BARTHOLOMEW
— the master writes "Andrew Bartholomew" for Andy, his formal name) or on surname (the two Ashcrofts,
Pete + Jim). So:
  1. anchor on SURNAME (from the email local-part: andy.bartholomew -> "bartholomew") when present and
     it hits exactly one roster trainer — that is authoritative,
  2. if a surname is shared, the FIRST name breaks the tie,
  3. otherwise fall back to first-name (exact, or "Steve M" = first + last-initial),
  4. last resort: a roster name appearing anywhere in the string.

Every cron that maps a trainer name from the diary or the sheet imports resolve_trainer() so the rule
is identical everywhere. Each trainer dict needs at least {"name", "email"}.
"""


def _surname(trainer):
    lp = (trainer.get("email") or "").split("@")[0]
    return lp.split(".")[-1].lower() if "." in lp else ""


def resolve_trainer(name, trainers):
    """Return the matching trainer dict from `trainers`, or None."""
    full = (str(name) if name is not None else "").strip()
    if not full:
        return None
    first = full.split("/")[0].split(" ")[0].split("-")[0].strip().lower()
    last = full.split(" ", 1)[1].strip().lower() if " " in full else ""
    tl = last.replace(" ", "").split("/")[0]

    # 1) surname-anchored
    hits = []
    if len(tl) >= 3:
        for t in trainers:
            sn = _surname(t)
            if sn and (tl == sn or tl in sn or sn in tl):
                hits.append(t)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:                       # shared surname -> first name breaks the tie
        for t in hits:
            if t["name"].lower().split()[0] == first:
                return t
        return hits[0]

    # 2) first-name (exact, or 'Steve M' = first + last-initial)
    for t in trainers:
        tn = t["name"].lower().strip()
        if first and tn == first:
            return t
        if " " in tn and first == tn.split()[0] and last and last[0] == tn.split()[1][0]:
            return t

    # 3) loose: a roster name appears anywhere in the string
    for t in trainers:
        if t["name"].lower() in full.lower():
            return t
    return None


def same_trainer(a, b, trainers):
    """True if two free-text trainer names resolve to the same roster trainer (used by exception
    matching: 'Andrew Bartholomew' == 'Andy Bartholomew', but 'Jim Ashcroft' != 'Pete Ashcroft')."""
    ra, rb = resolve_trainer(a, trainers), resolve_trainer(b, trainers)
    if ra and rb:
        return ra["email"] == rb["email"]
    # neither resolves to the roster: fall back to first-name equality (best effort)
    fa = (str(a or "").split() or [""])[0].lower()
    fb = (str(b or "").split() or [""])[0].lower()
    return bool(fa) and fa == fb
