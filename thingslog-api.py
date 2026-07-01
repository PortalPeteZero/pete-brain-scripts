#!/usr/bin/env python3
"""thingslog-api.py — ThingsLog IoT platform helper (Canary Detect / LeakGuard).

Auth: POST /login {username,password} -> JWT in the Authorization response header; send as Bearer.
This is a FULL-ACCOUNT session (unlike the old read-scoped THINGSLOG_API_TOKEN baked into the edge
functions) so it can WRITE too: change device config/interval, provision devices, send commands.

Creds live in the CC secrets table as 'thingslog-login.json' (base_url, username, password, company_id).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py devices          # id, name, model, active
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py fleet            # full fleet table (pulse, interval, sim...)
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py config <num>     # one device's config (pulse_coef, etc.)
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py set-transmission <num|all> <hours> [logging_min]  # WRITE call-in interval
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py get <path>       # raw GET any endpoint
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py openapi          # dump write endpoints

set-transmission is the ONE wired write (PUT /api/devices/{n}/config): call-in hours = countsThreshold ×
logging_min / 60, applied on the device's NEXT call-in. Provision/commands/delete are deliberately NOT wired
-- add them consciously, never as a side effect. There is no "transmit now" endpoint.

CHANGING WHICH TIMES a device reports (the phase, not the interval): ThingsLog has no clock-time schedule, so
shift the times with a ONE-OFF interval nudge that straddles a single call-in (shorten before a call-in to move
the schedule earlier, then restore 8h before the next call-in; prefer shortening -- lengthening leaves a
coverage gap). Full method + worked example (Michelle / 04298215) in [[thingslog-connection]] in vault_notes.
"""
import json, sys, subprocess, urllib.request, urllib.error, ssl

BASE_DEFAULT = "https://iot.thingslog.com:4443"
_ctx = ssl.create_default_context()

def _creds():
    raw = subprocess.run(["python3","/tmp/pbs/cc-sql.py",
        "SELECT value FROM secrets WHERE name='thingslog-login.json'"],
        capture_output=True, text=True,
        env={"VAULT":"/tmp/pbs","PATH":"/usr/bin:/bin:/usr/local/bin"}).stdout
    return json.loads(json.loads(raw)[0]["value"])

def _login(c):
    body = json.dumps({"username":c["username"],"password":c["password"]}).encode()
    req = urllib.request.Request(c.get("base_url",BASE_DEFAULT)+"/login", data=body,
                                 headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
        return r.headers.get("Authorization").replace("Bearer ","")

def _get(base, tok, path):
    req = urllib.request.Request(base+path, headers={"Authorization":"Bearer "+tok,"Accept":"application/json","User-Agent":"curl/8"})
    with urllib.request.urlopen(req, context=_ctx, timeout=40) as r:
        return json.loads(r.read())

def _put(base, tok, cid, path, body):
    req = urllib.request.Request(base+path, data=json.dumps(body).encode(), method="PUT",
        headers={"Authorization":"Bearer "+tok,"Accept":"application/json","Content-Type":"application/json","X-Company-Id":str(cid),"User-Agent":"curl/8"})
    with urllib.request.urlopen(req, context=_ctx, timeout=40) as r:
        return r.status, json.loads(r.read() or "{}")

# WRITE: set the call-in interval in HOURS via countsThreshold (device transmits every N records;
# with 15-min logging, 8h => 32 records). Keeps logging at <logging_min> MINUTES. Round-trips the
# device's own config so nothing else changes. Applies on the device's next call-in.
def _set_transmission(base, tok, cid, numbers, hours, logging_min=15):
    for n in numbers:
        cfg = _get(base, tok, f"/api/devices/{n}/config")
        cfg["recordPeriod"]="MINUTES"; cfg["every"]=logging_min
        cfg["countsThreshold"]=round(hours*60/logging_min)
        st,_=_put(base, tok, cid, f"/api/devices/{n}/config", cfg)
        chk=_get(base, tok, f"/api/devices/{n}/config")
        ok = st==200 and chk.get("countsThreshold")==cfg["countsThreshold"] and chk.get("every")==logging_min
        print(f"{n}: countsThreshold={chk.get('countsThreshold')} every={chk.get('every')}{chk.get('recordPeriod')} -> {'OK' if ok else 'FAIL '+str(st)}")

def main():
    c = _creds(); base = c.get("base_url",BASE_DEFAULT); tok = _login(c); cid = c.get("company_id",1251)
    cmd = sys.argv[1] if len(sys.argv)>1 else "fleet"
    if cmd == "get":
        print(json.dumps(_get(base,tok,sys.argv[2]), indent=2)); return
    if cmd == "config":
        print(json.dumps(_get(base,tok,f"/api/devices/{sys.argv[2]}/config"), indent=2)); return
    if cmd == "set-transmission":
        # set-transmission <deviceNumber|all> <hours> [logging_minutes]
        target=sys.argv[2]; hours=float(sys.argv[3]); lmin=int(sys.argv[4]) if len(sys.argv)>4 else 15
        nums=[d["number"] for d in _get(base,tok,"/api/v2/devices").get("content",[])] if target=="all" else [target]
        print(f"Setting {len(nums)} device(s) to {hours}h call-in + {lmin}-min logging (applies on next call-in):")
        _set_transmission(base,tok,cid,nums,hours,lmin); return
    if cmd == "openapi":
        spec=_get(base,tok,"/v2/api-docs"); 
        for p,ms in sorted(spec.get("paths",{}).items()):
            for m in ms:
                if m.lower() in ("post","put","patch","delete"): print(f"{m.upper():6} {p}")
        return
    devs = _get(base,tok,"/api/v2/devices").get("content",[])
    if cmd == "devices":
        for d in devs: print(d.get("number"), "|", d.get("name"), "|", d.get("model"), "| active:", d.get("active"))
        return
    # fleet (default): full per-device table
    print(f"DEVICES: {len(devs)}")
    for d in devs:
        num=d.get("number")
        try: cf=_get(base,tok,f"/api/devices/{num}/config")
        except Exception: cf={}
        sc=(cf.get("sensorConfigs") or [{}])[0].get("parameters",{})
        pc=sc.get("pulse_coef"); units=sc.get("units_type")
        lpp = round(float(pc)*1000,3) if (units=="CUBIC_METER" and pc) else None
        print(f'{num} | {str(d.get("name"))[:34]:34} | {d.get("model"):14} | rec {cf.get("every")} {cf.get("recordPeriod")} | {lpp} L/pulse | active={d.get("active")}')

if __name__ == "__main__":
    try: main()
    except urllib.error.HTTPError as e: print("HTTP", e.code, e.read().decode()[:200]); sys.exit(1)
