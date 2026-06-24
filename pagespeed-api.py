#!/usr/bin/env python3
"""
PageSpeed Insights + Chrome UX Report (CrUX) API helper.

Key:     Library/processes/secrets/pagespeed-crux-api-key  (project sygma-seo-tools,
         restricted to PageSpeed Insights API + Chrome UX Report API).
APIs:    pagespeedonline.googleapis.com (lab/Lighthouse) + chromeuxreport.googleapis.com (real-user field data)
         Both enabled on sygma-seo-tools 2026-05-27.

Usage (CLI):
  python3 pagespeed-api.py psi <url> [mobile|desktop]
  python3 pagespeed-api.py crux-origin <origin> [PHONE|DESKTOP|TABLET]
  python3 pagespeed-api.py crux-url <url> [PHONE|DESKTOP]

Library:
  from pagespeed_api import psi, crux
"""
import json, os, sys, urllib.request, urllib.error, urllib.parse

KEY = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
      "..", "secrets", "pagespeed-crux-api-key")).read().strip()

LAB = ["largest-contentful-paint","cumulative-layout-shift","total-blocking-time",
       "first-contentful-paint","speed-index","interactive","server-response-time","total-byte-weight"]

def psi(url, strategy="mobile"):
    api="https://www.googleapis.com/pagespeedonline/v5/runPagespeed?"+urllib.parse.urlencode(
        {"url":url,"strategy":strategy,"category":"performance","key":KEY})
    with urllib.request.urlopen(api, timeout=80) as r:
        d=json.loads(r.read())
    lh=d.get("lighthouseResult",{}); a=lh.get("audits",{})
    def m(k):
        x=a.get(k,{}); return {"dv":x.get("displayValue"),"num":x.get("numericValue"),"score":x.get("score")}
    opps=[]
    for aid,au in a.items():
        det=au.get("details",{})
        if det.get("type")=="opportunity" and (au.get("numericValue") or 0)>100:
            opps.append({"title":au.get("title"),"save_s":round(au["numericValue"]/1000,1),"detail":au.get("displayValue")})
    opps.sort(key=lambda x:-x["save_s"])
    return {"url":url,"strategy":strategy,
            "score":round((lh.get("categories",{}).get("performance",{}).get("score") or 0)*100),
            **{k.split("-")[0] if k!="server-response-time" else "ttfb":m(k) for k in []},  # placeholder
            "metrics":{k:m(k) for k in LAB},
            "top_opportunities":opps[:7]}

_THRESH={"largest_contentful_paint":(2500,4000),"cumulative_layout_shift":(0.1,0.25),
         "interaction_to_next_paint":(200,500),"first_contentful_paint":(1800,3000),
         "experimental_time_to_first_byte":(800,1800)}
def _cat(metric, p75):
    if p75 is None or metric not in _THRESH: return None
    try: v=float(p75)
    except (TypeError, ValueError): return None
    good,ni=_THRESH[metric]
    return "good" if v<=good else "needs-improvement" if v<=ni else "poor"

def crux(target, form_factor="PHONE", is_url=False):
    body={("url" if is_url else "origin"):target}
    if form_factor: body["formFactor"]=form_factor
    req=urllib.request.Request("https://chromeuxreport.googleapis.com/v1/records:queryRecord?key="+KEY,
        data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r: d=json.loads(r.read())
    except urllib.error.HTTPError as e:
        b=e.read().decode("utf-8","replace")
        if e.code==404: return {"available":False,"reason":"no CrUX data for this "+("url" if is_url else "origin")+" (insufficient real-user traffic)"}
        return {"available":False,"reason":f"HTTP {e.code}: {b[:120]}"}
    rec=d.get("record",{}); mets=rec.get("metrics",{})
    out={"available":True,"form_factor":form_factor,"period":rec.get("collectionPeriod"),"metrics":{}}
    for k,v in mets.items():
        p75=v.get("percentiles",{}).get("p75")
        out["metrics"][k]={"p75":p75,"category":_cat(k,p75)}
    return out

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else ""
    if cmd=="psi":
        print(json.dumps(psi(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else "mobile"), indent=2))
    elif cmd=="crux-origin":
        print(json.dumps(crux(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else "PHONE"), indent=2))
    elif cmd=="crux-url":
        print(json.dumps(crux(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else "PHONE", is_url=True), indent=2))
    else:
        print(__doc__)
