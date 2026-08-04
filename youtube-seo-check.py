#!/usr/bin/env python3
"""youtube-seo-check.py -- refuse YouTube metadata that is wrong, unoptimised, or off-brand.

Pete, 4 Aug 2026: "you need to ensure the titles and descriptions and all settings are fully
correct and optimised for SEO, we use every angle we get."

So this is a GATE, not advice. `youtube-api.py upload/update` calls it and REFUSES on a BLOCK.
Run it standalone on a JSON payload to check before you commit to anything.

Limits are YouTube's own, read from developers.google.com/youtube/v3/docs/videos (4 Aug 2026):
title <=100 CHARACTERS, description <=5000 BYTES (not chars -- an em dash costs 3), tags <=500
characters TOTAL including the commas and the quotes around any tag containing a space.

Keyword truth is public.seo_keyword_map (478 commercial terms) -- the SSOT the Sygma front door
names, "not any note, and not Ahrefs". A title targeting nothing in that table is a wasted upload.

Usage:
  youtube-seo-check.py <payload.json>          # exit 0 clean / 1 BLOCK / 2 could not run
  youtube-seo-check.py <payload.json> --json
  youtube-seo-check.py --video VIDEO_ID        # audit something already live
  youtube-seo-check.py <payload.json> --context clancy   # STRICT: Genny-first everywhere

Naming is context-dependent and this is the compromise (Pete, 4 Aug 2026): Clancy insist on
"Genny and CAT"; Sygma's ideal is the same; only SEO wants "CAT and Genny", because that is what
people type. So CAT-first is permitted in TITLES AND TAGS on public search-facing surfaces and
nowhere else. Clancy-facing material (--context clancy) is Genny-first everywhere, because the hub
is gated -- nobody arrives by search, so there is nothing to trade away.

Payload shape (same as videos.insert/update):
  {"snippet": {"title": ..., "description": ..., "tags": [...], "categoryId": "27",
               "defaultLanguage": "en", "defaultAudioLanguage": "en-GB"},
   "status":  {"privacyStatus": "public"}}
"""
import os, re, sys, json, urllib.request, urllib.parse

MAX_TITLE_CHARS = 100
MAX_DESC_BYTES = 5000
MAX_TAGS_CHARS = 500
SNIPPET_CUTOFF = 157          # roughly what search/suggested shows before "...more"

CC_KEY = os.path.join(os.environ.get("VAULT", "/tmp/pbs"),
                      "Library", "processes", "secrets", "command-centre-supabase-keys.json")


def keyword_map():
    """The 478 commercial terms. Empty list => we could NOT check, which is not the same as clean."""
    try:
        k = json.load(open(CC_KEY))
        url = k["url"].rstrip("/") + "/rest/v1/seo_keyword_map?select=keyword,target_page,intent"
        req = urllib.request.Request(url, headers={"apikey": k["service_role_key"],
                                                   "Authorization": "Bearer " + k["service_role_key"]})
        return json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        print(f"WARNING: could not read seo_keyword_map ({e}). Keyword checks SKIPPED -- "
              f"this is 'unchecked', not 'passed'.", file=sys.stderr)
        return []


def check(payload, kws, context="public"):
    sn = payload.get("snippet", {}) or {}
    st = payload.get("status", {}) or {}
    title = (sn.get("title") or "").strip()
    desc = (sn.get("description") or "").strip()
    tags = sn.get("tags") or []
    out = []           # (severity, code, message)
    B = lambda c, m: out.append(("BLOCK", c, m))
    W = lambda c, m: out.append(("WARN", c, m))

    # ── YouTube's hard limits ────────────────────────────────────────────────
    if not title:
        B("title-missing", "No title.")
    elif len(title) > MAX_TITLE_CHARS:
        B("title-too-long", f"Title is {len(title)} chars; YouTube's limit is {MAX_TITLE_CHARS}.")
    if "<" in title or ">" in title:
        B("title-invalid-chars", "Title contains < or >, which YouTube rejects.")

    dbytes = len(desc.encode("utf-8"))
    if dbytes > MAX_DESC_BYTES:
        B("desc-too-long", f"Description is {dbytes} BYTES; limit is {MAX_DESC_BYTES}. "
                           f"(Bytes, not characters -- an em dash costs 3.)")
    if "<" in desc or ">" in desc:
        B("desc-invalid-chars", "Description contains < or >, which YouTube rejects.")

    tag_cost = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
    if tag_cost > MAX_TAGS_CHARS:
        B("tags-too-long", f"Tags cost {tag_cost} chars against YouTube's {MAX_TAGS_CHARS} "
                           f"(quotes around spaced tags + separators count).")

    # ── settings people forget, every time ───────────────────────────────────
    if not sn.get("categoryId"):
        B("no-category", "categoryId missing. Required on update, and it drives what YouTube "
                         "recommends this alongside. Sygma training = 27 (Education).")
    if not sn.get("defaultLanguage"):
        W("no-default-language", "defaultLanguage unset -- set 'en' so title/description are "
                                 "understood as English.")
    if not sn.get("defaultAudioLanguage"):
        W("no-audio-language", "defaultAudioLanguage unset -- set 'en-GB'. This is what makes "
                               "YouTube auto-caption correctly, and captions are how the video "
                               "becomes answerable.")
    if not st.get("privacyStatus"):
        B("no-privacy", "privacyStatus not stated. Never leave this to a default -- say "
                        "private, unlisted or public explicitly.")
    if not tags:
        W("no-tags", "No tags. Minor for ranking, but free.")

    # ── description quality ──────────────────────────────────────────────────
    if not desc:
        B("desc-missing", "No description. This is the single biggest wasted angle.")
    else:
        head = desc[:SNIPPET_CUTOFF]
        if "http" not in desc:
            B("desc-no-link", "No link anywhere in the description. Every video should route "
                              "somewhere -- the course page, the agenda, the hub.")
        elif "http" not in head:
            W("link-below-fold", f"No link in the first {SNIPPET_CUTOFF} chars. Everything after "
                                 f"that is behind '...more' and most people never open it.")
        if len(desc) < 200:
            W("desc-thin", f"Description is only {len(desc)} chars. It is indexable text about "
                           f"a subject you want to rank for -- use it.")
        if re.search(r"^\s*(00:00|0:00)", desc, re.M) is None and len(desc) > 400:
            W("no-chapters", "No 00:00 timestamp line. Chapters make a long video navigable and "
                             "surface as jump-to links in search results.")

    # ── the naming rule, which is CONTEXT-dependent (Pete, 4 Aug 2026) ──────
    # Clancy insist on "Genny and CAT". Sygma's own ideal is "Genny and CAT". Only SEO wants
    # "CAT and Genny", because that is what people type -- seo_keyword_map carries 18+ such terms.
    # So the compromise is confined to SEARCH-FACING surfaces, and nowhere else:
    #   public  (default) -- title/tags may be CAT-first (that is the query); body copy may not.
    #   clancy  (--context clancy) -- Genny-first EVERYWHERE. The hub is gated and Sygma-owned;
    #           nobody arrives by search, so there is nothing to trade away.
    WRONG_ORDER = r"C\.?A\.?T\.?\s*(?:&|and)\s*Genny"
    body_wrong = re.findall(WRONG_ORDER, desc, re.I)
    if body_wrong:
        B("naming-order-body", f"Description says {body_wrong[0]!r}. In BODY COPY it is always "
                               f"'Genny and CAT' -- the name states the method: connect the Genny "
                               f"first. (Titles/tags may use 'CAT and Genny' because that is what "
                               f"people search -- the keyword map carries it. Body copy may not.)")
    if context == "clancy":
        for field, val in (("title", title), ("tags", " ".join(tags))):
            hit = re.findall(WRONG_ORDER, val, re.I)
            if hit:
                B("naming-order-clancy", f"{field} says {hit[0]!r}. Clancy-facing material is "
                                         f"'Genny and CAT' EVERYWHERE, title included. The search "
                                         f"carve-out does not apply -- the hub is gated and nobody "
                                         f"reaches it by searching.")
    if re.search(r"\bcourses\b", title, re.I):
        B("courses-plural", "Sygma naming is 'course' SINGULAR, never 'courses'.")

    # ── is this title actually targeting anything we sell? ──────────────────
    if kws and title:
        tl = title.lower()
        hits = sorted({k["keyword"] for k in kws if k["keyword"].lower() in tl}, key=len, reverse=True)
        if hits:
            out.append(("INFO", "targets", f"Targets {len(hits)} mapped term(s); longest: {hits[0]!r}"))
        else:
            W("no-mapped-term", "Title contains no term from seo_keyword_map (478 commercial "
                                "keywords). Either target one, or accept this video is not "
                                "working for search.")
    return out


def audit_live(video_id):
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube-api.py")
    spec = importlib.util.spec_from_file_location("yt", p)
    yt = importlib.util.module_from_spec(spec)
    saved, sys.argv = sys.argv, ["youtube-api.py"]
    try:
        spec.loader.exec_module(yt)
    except SystemExit:
        pass
    finally:
        sys.argv = saved
    r = yt.data_api("/videos", {"part": "snippet,status", "id": video_id})
    if not r.get("items"):
        print(f"No such video: {video_id}", file=sys.stderr); sys.exit(2)
    return r["items"][0]


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    context = "clancy" if "--context" in args and "clancy" in args else "public"
    args = [a for a in args if a not in ("--json", "--context", "clancy", "public")]
    if not args:
        print(__doc__); sys.exit(2)

    if args[0] == "--video":
        if len(args) < 2:
            print("Usage: youtube-seo-check.py --video VIDEO_ID", file=sys.stderr); sys.exit(2)
        payload = audit_live(args[1]); label = args[1]
    else:
        payload = json.load(open(args[0])); label = args[0]

    findings = check(payload, keyword_map(), context)
    blocks = [f for f in findings if f[0] == "BLOCK"]
    warns = [f for f in findings if f[0] == "WARN"]

    if as_json:
        print(json.dumps({"target": label, "block": len(blocks), "warn": len(warns),
                          "findings": [{"severity": s, "code": c, "message": m}
                                       for s, c, m in findings]}, indent=1))
    else:
        title = (payload.get("snippet") or {}).get("title", "")
        print(f"YouTube SEO check — {label}")
        if title:
            print(f"  title ({len(title)}/{MAX_TITLE_CHARS}): {title}")
        print()
        for sev, code, msg in findings:
            mark = {"BLOCK": "✗ BLOCK", "WARN": "! warn", "INFO": "· info"}[sev]
            print(f"  {mark}  [{code}] {msg}")
        if not findings:
            print("  clean.")
        print()
        print(f"  {len(blocks)} block · {len(warns)} warn")
        if blocks:
            print("  REFUSED — fix the blocks and re-run.")

    sys.exit(1 if blocks else 0)


if __name__ == "__main__":
    main()
