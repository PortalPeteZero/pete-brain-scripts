#!/usr/bin/env python3
"""
sygma-ads-finalise-rsa.py -- finaliser for the 2026-05-21 EXCELLENT RSA rebuild.

Background: on 21 May 2026, new RSAs were created across the live Sygma campaign
23661951284 to lift Quality Score (the Expected-CTR component). Two new RSAs per ad
group (one for Safe Digging, which kept its existing EXCELLENT RSA). The OLD weak RSAs
were left ENABLED as safety nets until Google finished policy review of the new ads.

This script FINALISES: for each ad group, ONCE all its new ads are APPROVED, it pauses
that group's old weak RSA(s) so only the strong new copy serves.

Safety / idempotency:
  - only ever pauses an old ad that is currently ENABLED
  - never pauses a group's old ad(s) unless ALL that group's new ads are APPROVED
  - never touches the kept EXCELLENT Safe-Digging RSA (800599769880)
  - if a new ad is DISAPPROVED, it does NOT pause the old one (keeps the group serving)
  - re-runnable: a finished group is simply reported as already done

Prints a per-group report, a full ad-strength summary, and a final line:
  STATUS: DONE       -> every group finalised (all old weak ads paused), nothing flagged
  STATUS: PENDING    -> at least one group still under Google review (run again later)
  STATUS: ATTENTION  -> a new ad was DISAPPROVED (needs a human look)

Usage:  python3 sygma-ads-finalise-rsa.py [--dry-run]
"""
import importlib.util, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ads_api", os.path.join(HERE, "ads-api.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

CID = "1739090181"; CAMP = "23661951284"
DRY = "--dry-run" in sys.argv

# ad_group_id -> plan. new = the new EXCELLENT-spec RSAs; old_weak = ads to pause once
# the new ones are approved; keep = ads to leave untouched (already EXCELLENT).
PLAN = {
 "198190544150": {"label": "Cable Avoidance & CAT Genny", "new": [809643834715, 809643834718], "old_weak": [800710095782, 800889270404], "keep": []},
 "199759067274": {"label": "HSG47 Training",              "new": [809643890761, 809643890764], "old_weak": [809386711126],              "keep": []},
 "189539619250": {"label": "VSCAN & Transmitter",         "new": [809722447394, 809722447397], "old_weak": [800889270398, 809326971963], "keep": []},
 "194245072013": {"label": "Safe Digging & CAT2",         "new": [809643889804],               "old_weak": [800889270401],              "keep": [800599769880]},
}

ads = mod.GoogleAdsAPI()
rows = ads.query(
    "SELECT ad_group.id, ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad_strength, "
    "ad_group_ad.policy_summary.approval_status FROM ad_group_ad "
    "WHERE campaign.id = " + CAMP + " AND ad_group_ad.status != 'REMOVED'",
    customer_id=CID,
)
st = {}
for r in rows:
    a = r["adGroupAd"]; aid = int(a["ad"]["id"])
    st[aid] = {"status": a.get("status"), "strength": a.get("adStrength"),
               "approval": a.get("policySummary", {}).get("approvalStatus"),
               "agid": r["adGroup"]["id"]}

def pause(agid, aid):
    rn = f"customers/{CID}/adGroupAds/{agid}~{aid}"
    if DRY:
        print(f"      [dry-run] would pause old {aid}"); return
    ads.mutate("adGroupAds", [{"update": {"resourceName": rn, "status": "PAUSED"},
                               "updateMask": "status"}], customer_id=CID)
    print(f"      PAUSED old {aid}")

overall = "DONE"; attention = []
for agid, p in PLAN.items():
    print(f"\n[{p['label']}]")
    for i in p["new"]:
        s = st.get(i, {})
        print(f"   new {i}: status={s.get('status')} strength={s.get('strength')} approval={s.get('approval')}")
        if s.get("approval") == "DISAPPROVED":
            attention.append(f"{p['label']}: new ad {i} DISAPPROVED"); overall = "ATTENTION"
    if any(st.get(i, {}).get("approval") == "DISAPPROVED" for i in p["new"]):
        print("   -> a new ad is DISAPPROVED; NOT pausing old ads for this group."); continue
    if not all(st.get(i, {}).get("approval") == "APPROVED" for i in p["new"]):
        print("   -> new ads still under review; leaving old ads enabled (will retry).")
        if overall != "ATTENTION":
            overall = "PENDING"
        continue
    # All new ads approved. Only finalise (pause olds) if an EXCELLENT ad will still be
    # serving in this group -- never trade a decent old ad for new ones below EXCELLENT.
    RANK = {"EXCELLENT": 4, "GOOD": 3, "AVERAGE": 2, "POOR": 1}
    remaining = p["new"] + p["keep"]   # these stay enabled after old weak ads are paused
    remaining_best = max((RANK.get(st.get(i, {}).get("strength"), 0) for i in remaining), default=0)
    if remaining_best < 4:
        print("   -> approved, but no EXCELLENT ad would remain; leaving old ad enabled (copy needs improving).")
        attention.append(f"{p['label']}: approved but best new/kept strength below EXCELLENT -- old ad kept, RSA copy needs improving")
        overall = "ATTENTION"
        continue
    paused_any = False
    for oldid in p["old_weak"]:
        s = st.get(oldid, {})
        if s.get("status") == "ENABLED":
            pause(agid, oldid); paused_any = True
        else:
            print(f"      old {oldid} already {s.get('status', '?')}")
    print("   -> group finalised: EXCELLENT new RSA serving, old weak paused." if paused_any
          else "   -> group already finalised.")

print("\n=== FULL CAMPAIGN AD STATE ===")
for aid, s in sorted(st.items()):
    print(f"  {aid}  {s['status']:8} {str(s['strength']):10} {s['approval']}")
if attention:
    print("\nATTENTION:")
    for a in attention:
        print("  - " + a)
print(f"\nSTATUS: {overall}")
