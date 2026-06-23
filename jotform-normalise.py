#!/usr/bin/env python3
"""JotForm field normaliser — canonical trainer + course names from free-text answers.

Sygma's JotForm Training Evaluation Form (id 201324458767056) has free-text
"Trainer/s" (q7) and "Course" (q5) fields. 119 distinct trainer strings and 446
distinct course strings in our Feb-May 2026 sample alone, mostly typos +
abbreviations + capitalisation variants of a small canonical set.

This module loads the canonical lists from:
  - `Library/processes/sygma-trainer-roster.yaml`
  - `Library/processes/sygma-course-taxonomy.yaml`

and exposes `normalise_trainer(raw)` + `normalise_course(raw)` returning a
tuple `(canonical, confidence, raw)`:

  - `canonical`: clean name string, or None if unmatched
  - `confidence`: one of "exact", "alias", "fuzzy", "ambiguous", "unmatched"
  - `raw`: the original input (always preserved for diagnostics)

Multi-trainer answers (e.g. "Paul baxter / Neal") split into a list of results.

Created 2026-05-30. Pattern matches the other Library/processes/scripts/*-api.py
helpers (jotform-api.py, gmail-api.py).

CLI:
  python3 jotform-normalise.py trainer "Geoff Astley"
  python3 jotform-normalise.py trainer "Andy"
  python3 jotform-normalise.py course "Cat and genny"
  python3 jotform-normalise.py audit       # run against all 1000 sample submissions
"""

import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml  # PyYAML required: `pip install pyyaml`

import os  # noqa: E402
VAULT = Path(os.environ.get("VAULT", "/Users/peterashcroft/Second Brain"))
def _cfg_yaml(name):  # vault path locally; on Railway the bootstrap materialises it into secrets/
    p = VAULT / "Library/processes" / name
    return p if p.exists() else (VAULT / "Library/processes/secrets" / name)
TRAINER_YAML = _cfg_yaml("sygma-trainer-roster.yaml")
COURSE_YAML = _cfg_yaml("sygma-course-taxonomy.yaml")

FUZZY_THRESHOLD = 0.82


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


class _TrainerNormaliser:
    def __init__(self, roster_path: Path = TRAINER_YAML):
        data = _load(roster_path)
        self.trainers = data["trainers"]
        self.multi_seps = data.get("multi_trainer_separators", [])
        ambiguous = data.get("ambiguous_bare", []) or []
        self.ambiguous_bare = {a["bare"].lower(): a["candidates"] for a in ambiguous}

        # Build an alias → canonical lookup (case-insensitive)
        self.alias_map: dict[str, str] = {}
        self.bare_alias_map: dict[str, str] = {}
        self.all_canonical_strings: list[tuple[str, str]] = []
        for t in self.trainers:
            canon = t["canonical"]
            self.alias_map[canon.lower()] = canon
            self.all_canonical_strings.append((canon.lower(), canon))
            for a in t.get("aliases", []) or []:
                self.alias_map[a.lower()] = canon
                self.all_canonical_strings.append((a.lower(), canon))
            for ba in t.get("bare_aliases", []) or []:
                self.bare_alias_map[ba.lower()] = canon

    def _norm_str(self, s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    def _split_multi(self, raw: str) -> list[str]:
        parts = [raw]
        for sep in self.multi_seps:
            parts = [p for part in parts for p in part.split(sep)]
        return [self._norm_str(p) for p in parts if p.strip()]

    def normalise_one(self, raw: str) -> tuple[str | None, str, str]:
        if not raw:
            return (None, "unmatched", raw)
        key = self._norm_str(raw).lower()
        # 1. Exact / alias match
        if key in self.alias_map:
            return (self.alias_map[key], "exact" if key == self.alias_map[key].lower() else "alias", raw)
        # 2. Ambiguous bare
        if key in self.ambiguous_bare:
            return (None, "ambiguous", raw)
        # 3. Bare alias (unambiguous)
        if key in self.bare_alias_map:
            return (self.bare_alias_map[key], "alias", raw)
        # 4. Fuzzy match
        best = None
        best_score = 0.0
        for cand_lower, canon in self.all_canonical_strings:
            score = SequenceMatcher(None, key, cand_lower).ratio()
            if score > best_score:
                best_score = score
                best = canon
        if best_score >= FUZZY_THRESHOLD:
            return (best, "fuzzy", raw)
        return (None, "unmatched", raw)

    def normalise(self, raw: str) -> list[tuple[str | None, str, str]]:
        parts = self._split_multi(raw)
        if len(parts) <= 1:
            return [self.normalise_one(raw)]
        return [self.normalise_one(p) for p in parts]


class _CourseNormaliser:
    def __init__(self, course_path: Path = COURSE_YAML):
        data = _load(course_path)
        # Compile patterns in declaration order (first-match-wins)
        self.buckets: list[dict] = []
        for c in data["courses"]:
            patterns = [re.compile(p, re.IGNORECASE) for p in c.get("patterns", [])]
            self.buckets.append({
                "canonical": c["canonical"],
                "duration_days": c.get("duration_days"),
                "patterns": patterns,
                "description": c.get("description", ""),
            })
        self.exact_aliases = {k.lower(): v for k, v in (data.get("exact_aliases") or {}).items()}
        self.review_strings = set(s.lower() for s in (data.get("review_strings") or []))

    def normalise(self, raw: str) -> tuple[str | None, str, str]:
        if not raw or not raw.strip():
            return (None, "unmatched", raw)
        s = raw.strip()
        slow = s.lower()
        # 1. Review-list strings
        if slow in self.review_strings:
            return (None, "ambiguous", raw)
        # 2. Exact alias map (overrides patterns for known one-offs)
        if slow in self.exact_aliases:
            target = self.exact_aliases[slow]
            return (target, "alias" if target else "ambiguous", raw)
        # 3. Pattern match (first wins)
        for b in self.buckets:
            for pat in b["patterns"]:
                if pat.search(s):
                    return (b["canonical"], "alias", raw)
        return (None, "unmatched", raw)


# Module-level singletons
_TR = None
_CO = None


def _get_trainer():
    global _TR
    if _TR is None:
        _TR = _TrainerNormaliser()
    return _TR


def _get_course():
    global _CO
    if _CO is None:
        _CO = _CourseNormaliser()
    return _CO


def normalise_trainer(raw: str) -> list[tuple[str | None, str, str]]:
    """Returns a list (handles multi-trainer entries like 'Paul / Neal')."""
    return _get_trainer().normalise(raw)


def normalise_course(raw: str) -> tuple[str | None, str, str]:
    return _get_course().normalise(raw)


# -- CLI ---------------------------------------------------------------------

def _audit() -> None:
    """Run normaliser against the saved sample of 1000 submissions + report."""
    sample_path = Path("/sessions/eager-tender-wozniak/mnt/outputs/eval_2025plus.json")
    if not sample_path.exists():
        sample_path = Path.cwd() / "eval_2025plus.json"
    if not sample_path.exists():
        print(f"Sample file not found at {sample_path}. Pull the data first.")
        sys.exit(1)
    data = json.load(open(sample_path))
    subs = data.get("content", [])

    tr_results = Counter()
    tr_unmatched = Counter()
    tr_ambiguous = Counter()
    tr_canonical = Counter()
    multi_count = 0

    co_results = Counter()
    co_unmatched = Counter()
    co_ambiguous = Counter()
    co_canonical = Counter()

    for s in subs:
        # Trainer
        raw_t = s.get("answers", {}).get("7", {}).get("answer", "")
        if isinstance(raw_t, str) and raw_t.strip():
            results = normalise_trainer(raw_t)
            if len(results) > 1:
                multi_count += 1
            for canon, conf, raw in results:
                tr_results[conf] += 1
                if canon:
                    tr_canonical[canon] += 1
                elif conf == "unmatched":
                    tr_unmatched[raw] += 1
                elif conf == "ambiguous":
                    tr_ambiguous[raw] += 1
        # Course
        raw_c = s.get("answers", {}).get("5", {}).get("answer", "")
        if isinstance(raw_c, str) and raw_c.strip():
            canon, conf, raw = normalise_course(raw_c)
            co_results[conf] += 1
            if canon:
                co_canonical[canon] += 1
            elif conf == "unmatched":
                co_unmatched[raw] += 1
            elif conf == "ambiguous":
                co_ambiguous[raw] += 1

    total_tr = sum(tr_results.values())
    total_co = sum(co_results.values())
    print(f"=== Trainer normalisation — {total_tr} entries (incl. {multi_count} multi-trainer) ===")
    for k in ("exact", "alias", "fuzzy", "ambiguous", "unmatched"):
        n = tr_results.get(k, 0)
        pct = n / total_tr * 100 if total_tr else 0
        print(f"  {k:>10}: {n:>4} ({pct:>5.1f}%)")
    print(f"\n  Canonical trainer breakdown:")
    for canon, n in tr_canonical.most_common():
        print(f"    {n:>4}  {canon}")
    if tr_ambiguous:
        print(f"\n  Ambiguous (bare names that match multiple trainers):")
        for raw, n in tr_ambiguous.most_common():
            print(f"    {n:>4}  {raw!r}")
    if tr_unmatched:
        print(f"\n  Unmatched (top 15 — need a roster decision):")
        for raw, n in tr_unmatched.most_common(15):
            print(f"    {n:>4}  {raw!r}")

    print(f"\n=== Course normalisation — {total_co} entries ===")
    for k in ("alias", "ambiguous", "unmatched"):
        n = co_results.get(k, 0)
        pct = n / total_co * 100 if total_co else 0
        print(f"  {k:>10}: {n:>4} ({pct:>5.1f}%)")
    print(f"\n  Canonical course breakdown:")
    for canon, n in co_canonical.most_common():
        print(f"    {n:>4}  {canon}")
    if co_ambiguous:
        print(f"\n  Ambiguous courses (flagged for review):")
        for raw, n in co_ambiguous.most_common():
            print(f"    {n:>4}  {raw!r}")
    if co_unmatched:
        print(f"\n  Unmatched course strings (top 20 — taxonomy gap):")
        for raw, n in co_unmatched.most_common(20):
            print(f"    {n:>4}  {raw!r}")


def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "trainer":
        raw = sys.argv[2]
        for canon, conf, r in normalise_trainer(raw):
            print(f"  '{r}' → canonical={canon!r}  conf={conf}")
    elif cmd == "course":
        raw = sys.argv[2]
        canon, conf, r = normalise_course(raw)
        print(f"  '{r}' → canonical={canon!r}  conf={conf}")
    elif cmd == "audit":
        _audit()
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    _cli()
