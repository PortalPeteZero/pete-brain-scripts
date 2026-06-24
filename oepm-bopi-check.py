#!/usr/bin/env python3
"""
OEPM BOPI Checker -- Live trademark status verification via BOPI search.

Discovered 2026-04-27: The OEPM CEO and LocalizadorWeb are Angular SPAs behind
reCAPTCHA, but the BOPI buscadorAnotaciones at sede.oepm.gob.es/bopiweb is a
server-rendered Struts app that works with curl + cookies.

Usage:
    python3 oepm-bopi-check.py                    # Check all portfolio marks
    python3 oepm-bopi-check.py M 4359094          # Check single mark
    python3 oepm-bopi-check.py --json             # JSON output for scripting

Returns BOPI publication entries (date, expedition, annotation type) for each mark.
"""

import subprocess
import re
import sys
import json
import tempfile
import os

BOPI_BASE = "https://sede.oepm.gob.es/bopiweb/buscadorAnotaciones"

# Pete's OEPM portfolio
PETE_MARKS = [
    ("M", "4359094", "Canary Detect marca"),
    ("N", "0495644", "Canary Detect nombre"),
    ("M", "4370471", "LeakGuard marca"),
    ("M", "4360295", "LEAKBUSTERS marca"),
    ("N", "0495623", "LEAKBUSTERS nombre"),
    ("M", "4359099", "The Leaky Finders marca"),
]

# Hamilton's OEPM portfolio
HAMILTON_MARKS = [
    ("M", "4359523", "Canary Leakbusters"),
    ("M", "4359528", "Lanzarote Leakbusters"),
    ("M", "4359531", "Pipebusters"),
]


def check_mark(mod, num):
    """Query BOPI for a single mark. Returns list of (date, expedition, annotation_type) tuples."""
    cookie_file = tempfile.mktemp(suffix=".txt")

    try:
        # Get session cookie
        subprocess.run(
            ["curl", "-sL", "-c", cookie_file, f"{BOPI_BASE}/buscarDetalle.action"],
            capture_output=True, timeout=15
        )

        # Search
        result = subprocess.run(
            ["curl", "-sL", "-b", cookie_file,
             f"{BOPI_BASE}/resultBusqueda.action",
             "-d", f"anotacion.numExpediente={num}&anotacion.modalidad={mod}"],
            capture_output=True, text=True, timeout=15
        )

        html = result.stdout

        # Find results section
        start = html.find("resultadosBuscador")
        if start == -1:
            return []

        chunk = html[start:start + 3000]

        # Strip scripts and tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", chunk, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "\n", text)
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Parse entries: date, expedition, annotation type
        entries = []
        i = 0
        while i < len(lines):
            if re.match(r"\d{2}-\d{2}-\d{4}", lines[i]):
                date = lines[i]
                exp = lines[i + 1] if i + 1 < len(lines) else ""
                atype = lines[i + 2] if i + 2 < len(lines) else ""
                entries.append({
                    "date": date,
                    "expedition": exp,
                    "type": atype
                })
                i += 3
            else:
                i += 1

        return entries

    finally:
        if os.path.exists(cookie_file):
            os.unlink(cookie_file)


def check_all(as_json=False):
    """Check all portfolio marks and return results."""
    results = {"pete": [], "hamilton": []}

    for mod, num, name in PETE_MARKS:
        entries = check_mark(mod, num)
        results["pete"].append({
            "mark": f"{mod}{num}",
            "name": name,
            "entries": entries
        })

    for mod, num, name in HAMILTON_MARKS:
        entries = check_mark(mod, num)
        results["hamilton"].append({
            "mark": f"{mod}{num}",
            "name": name,
            "entries": entries
        })

    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("===== PETE'S OEPM MARKS (LIVE BOPI CHECK) =====\n")
        for mark in results["pete"]:
            print(f"{mark['mark']} ({mark['name']}):")
            if mark["entries"]:
                for e in mark["entries"]:
                    print(f"  {e['date']} | {e['type']}")
            else:
                print("  No BOPI entries found")
            print()

        print("===== HAMILTON'S OEPM MARKS (LIVE BOPI CHECK) =====\n")
        for mark in results["hamilton"]:
            print(f"{mark['mark']} ({mark['name']}):")
            if mark["entries"]:
                for e in mark["entries"]:
                    print(f"  {e['date']} | {e['type']}")
            else:
                print("  No BOPI entries found")
            print()

    return results


if __name__ == "__main__":
    if len(sys.argv) == 3:
        # Single mark check: python3 oepm-bopi-check.py M 4359094
        mod = sys.argv[1]
        num = sys.argv[2]
        entries = check_mark(mod, num)
        if entries:
            for e in entries:
                print(f"{e['date']} | {e['expedition']} | {e['type']}")
        else:
            print("No BOPI entries found")
    elif "--json" in sys.argv:
        check_all(as_json=True)
    else:
        check_all()
