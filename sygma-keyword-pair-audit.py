#!/usr/bin/env python3
"""
Sygma keyword pair-rule audit.

Standing rule (Properties/Sygma Solutions Website/seo-targeting-principles.md
section 1d): every "X training" keyword should have a matching "X course"
keyword in the Rank Tracker (and vice versa).

This script fetches the current Sygma Rank Tracker (project 9613452), finds
keywords missing their twin, and optionally PUTs the missing twins.

Usage:
  python3 sygma-keyword-pair-audit.py            # dry-run (default)
  python3 sygma-keyword-pair-audit.py --apply    # push missing twins to tracker

Output:
  Console: gap summary + sample of missing pairs
  Apply mode: PUTs to https://api.ahrefs.com/v3/management/project-keywords?project_id=9613452
"""

import json
import subprocess
import sys
import time

AHREFS_TOKEN = "lGssv7YX4gEWyDhKaBhDLcmLfs14q-yqlZTzsMQa"
PROJECT_ID = "9613452"
COUNTRY = "gb"
BATCH = 50


def fetch_tracker():
    result = subprocess.run([
        'curl', '-s', '-H', f'Authorization: Bearer {AHREFS_TOKEN}',
        f'https://api.ahrefs.com/v3/management/project-keywords?project_id={PROJECT_ID}&select=keyword,tags&limit=1000'
    ], capture_output=True, text=True)
    return json.loads(result.stdout).get('keywords', [])


def swap_tc(s):
    return s.replace(' training', ' course').replace('training ', 'course ')


def swap_ct(s):
    return s.replace(' course', ' training').replace('course ', 'training ')


def is_noisy(twin):
    if 'course course' in twin or 'training training' in twin:
        return True
    if 'training' in twin and 'course' in twin:
        return True
    return False


def find_gaps(kws):
    by_keyword = {k['keyword']: k for k in kws}
    missing_course = []
    missing_training = []
    for k in kws:
        kw = k['keyword'].lower()
        if 'training' in kw:
            twin = swap_tc(k['keyword'])
            if twin == k['keyword'] or is_noisy(twin):
                continue
            if twin not in by_keyword:
                missing_course.append({'keyword': twin, 'tags': k.get('tags', []), 'src': k['keyword']})
        if 'course' in kw and 'training' not in kw:
            twin = swap_ct(k['keyword'])
            if twin == k['keyword'] or is_noisy(twin):
                continue
            if twin not in by_keyword:
                missing_training.append({'keyword': twin, 'tags': k.get('tags', []), 'src': k['keyword']})
    return missing_course, missing_training


def push_batch(batch):
    body = {'keywords': [{'keyword': k['keyword'], 'tags': k['tags']} for k in batch],
            'locations': [{'country': COUNTRY}]}
    result = subprocess.run([
        'curl', '-s', '-X', 'PUT',
        '-H', f'Authorization: Bearer {AHREFS_TOKEN}',
        '-H', 'Content-Type: application/json',
        f'https://api.ahrefs.com/v3/management/project-keywords?project_id={PROJECT_ID}',
        '-d', json.dumps(body)
    ], capture_output=True, text=True)
    try:
        resp = json.loads(result.stdout)
        return resp.get('error') is None
    except Exception:
        return False


def main():
    apply = '--apply' in sys.argv
    kws = fetch_tracker()
    print(f"Tracker: {len(kws)} keywords")
    missing_course, missing_training = find_gaps(kws)
    total = len(missing_course) + len(missing_training)
    print(f"Missing course twins: {len(missing_course)}")
    print(f"Missing training twins: {len(missing_training)}")
    print(f"Total gaps: {total}")
    if total == 0:
        print("\nPair compliance: 100% — no gaps.")
        return

    print("\nSample missing course twins (first 20):")
    for m in missing_course[:20]:
        tags = '|'.join(m['tags']) or '(no tag)'
        print(f"  + {m['keyword']:55s} [{tags}]  <- {m['src']}")
    print("\nSample missing training twins (first 20):")
    for m in missing_training[:20]:
        tags = '|'.join(m['tags']) or '(no tag)'
        print(f"  + {m['keyword']:55s} [{tags}]  <- {m['src']}")

    if not apply:
        print(f"\nDry-run. Re-run with --apply to push {total} missing twins.")
        return

    all_adds = missing_course + missing_training
    seen = set()
    deduped = []
    for a in all_adds:
        if a['keyword'] not in seen:
            seen.add(a['keyword'])
            deduped.append(a)
    print(f"\nPushing {len(deduped)} unique adds in batches of {BATCH}...")
    added = 0
    for i in range(0, len(deduped), BATCH):
        batch = deduped[i:i+BATCH]
        if push_batch(batch):
            added += len(batch)
            print(f"  Batch {i//BATCH + 1}: added {len(batch)} (cumulative {added})")
        else:
            print(f"  Batch {i//BATCH + 1}: FAILED")
        time.sleep(2)
    print(f"\nPushed {added}/{len(deduped)} keywords.")


if __name__ == '__main__':
    main()
