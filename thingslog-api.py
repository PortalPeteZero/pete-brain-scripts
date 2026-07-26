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
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py initial-config <num>   # the device-confirmed config layer
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py audit <num> [rev]      # ThingsLog's own change history
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py commands <num>         # list the queued command(s)
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py commands <num> RESET   # QUEUE a command (see caveat below)
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py set-config <num> <field> <value>   # WRITE one DeviceConfigDto field
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py openapi [all]     # write endpoints (all = include GETs)
  VAULT=/tmp/pbs python3 /tmp/pbs/thingslog-api.py schema <Definition>    # dump an OpenAPI definition

COMMANDS QUEUE, THEY DO NOT PUSH. A command POSTs as state=PENDING and is only delivered when the
device next calls in (verified 26 Jul 2026 on 04299212: SEND_CONFIG_OVER_MQTT sat PENDING with
sentDate=None). There is no "do it now" for a sleeping device. Same for config changes.

TWO CONFIG LAYERS, and both return PENDING writes before the device confirms them:
  /api/devices/{n}/config          -> DeviceConfigDto  (countsThreshold, every, deleteOldCounters,
                                      packetAutoCorrection, missedTransmissionSeverity, timeZone)
  /api/devices/{n}/initial-config  -> ConfigModel      (transmissionSettings + per-port sensor setup)
`configured` flips to False on write and back to True when the device picks the config up.

VERIFY EVERY WRITE BY READING BACK. ThingsLog returns HTTP 200 for a field it silently drops --
confirmed 26 Jul 2026: deleteOldCounters=true returned 200 twice and stored false both times, while
countsThreshold in the same request saved fine. `set-config` does the read-back for you and says
NOT PERSISTED rather than reporting a false success.

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

def _post(base, tok, cid, path, body=None):
    """POST with the company header. Returns (status, parsed-or-error-text) -- never raises on 4xx/5xx,
    because the ThingsLog error body carries the reason (e.g. 'Device X is not a modbus master!')."""
    data = json.dumps(body).encode() if body is not None else b"{}"
    req = urllib.request.Request(base+path, data=data, method="POST",
        headers={"Authorization":"Bearer "+tok,"Accept":"application/json","Content-Type":"application/json","X-Company-Id":str(cid),"User-Agent":"curl/8"})
    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:700]

# The ONLY command types the platform accepts (from DeviceCommandCreateDto in /v2/api-docs).
# Commands QUEUE as PENDING and are delivered on the device's NEXT call-in -- there is no push.
COMMAND_TYPES = ["RELAY_SWITCH","SINGLE_RELAY_SWITCH","RELAY_SWITCH_WITH_DELAY","RELAY_STATUSES",
                 "SEND_CONFIG_OVER_MQTT","SEND_DATE_OVER_MQTT","MODBUS","TASMOTA_RELAY_SWITCH",
                 "RESET","SCHEDULED_RELAY_COMMAND","SCHEDULED_RELAY_COMMAND_4"]

# WRITE: set one top-level field on the device config (DeviceConfigDto). Round-trips the device's
# own config so nothing else changes, then VERIFIES by reading back -- ThingsLog returns HTTP 200
# for a field it silently drops (confirmed 26 Jul 2026 with deleteOldCounters), so never trust the 200.
def _set_config_field(base, tok, cid, number, field, value):
    before = _get(base, tok, f"/api/devices/{number}/config")
    after = json.loads(json.dumps(before)); after[field] = value
    st, _ = _put(base, tok, cid, f"/api/devices/{number}/config", after)
    chk = _get(base, tok, f"/api/devices/{number}/config")
    got = chk.get(field)
    ok = st == 200 and got == value
    print(f"{number}: {field} {before.get(field)!r} -> requested {value!r}, stored {got!r}  [{'OK' if ok else 'NOT PERSISTED (HTTP %s)' % st}]")
    return ok

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
    if cmd == "initial-config":
        print(json.dumps(_get(base,tok,f"/api/devices/{sys.argv[2]}/initial-config"), indent=2)); return
    if cmd == "audit":
        # audit <device> [revision] -- ThingsLog's own change history for the device
        n=sys.argv[2]
        if len(sys.argv)>3:
            print(json.dumps(_get(base,tok,f"/api/audit/device/{n}/{sys.argv[3]}"), indent=2)); return
        for r in _get(base,tok,f"/api/audit/device/{n}").get("auditResults",[]):
            e=r.get("defaultRevisionEntity",{})
            print(f"rev {e.get('id')}  {e.get('revisionDate')}  {r.get('revisionType')}")
        return
    if cmd == "commands":
        # commands <device> [<COMMAND_TYPE>] -- no type = list the queue
        n=sys.argv[2]
        if len(sys.argv)>3:
            ctype=sys.argv[3].upper()
            if ctype not in COMMAND_TYPES:
                print(f"Unknown commandType {ctype}. Valid: {', '.join(COMMAND_TYPES)}"); return
            st,body=_post(base,tok,cid,f"/api/v2/devices/{n}/commands",{"commandType":ctype,"commandParameters":{}})
            print(f"POST {ctype} -> HTTP {st}: {body}")
            print("NOTE: the command QUEUES as PENDING and is delivered on the device's next call-in.")
            return
        st,body=_get(base,tok,f"/api/v2/devices/{n}/commands"),None
        for c2 in (st or []):
            print(f"id={c2.get('id')} type={c2.get('commandType')} state={c2.get('commandState')} "
                  f"created={c2.get('creationDate')} sent={c2.get('sentDate')} exec={c2.get('executionDate')}")
        if not st: print("(no commands queued)")
        return
    if cmd == "set-config":
        # set-config <device> <field> <value>  -- e.g. set-config 04299212 deleteOldCounters true
        n,field,raw=sys.argv[2],sys.argv[3],sys.argv[4]
        val = True if raw.lower()=="true" else False if raw.lower()=="false" else (int(raw) if raw.lstrip('-').isdigit() else raw)
        _set_config_field(base,tok,cid,n,field,val); return
    if cmd == "openapi":
        # openapi [all] -- default lists WRITE endpoints; 'all' includes GETs
        spec=_get(base,tok,"/v2/api-docs")
        want_all = len(sys.argv)>2 and sys.argv[2]=="all"
        for p,ms in sorted(spec.get("paths",{}).items()):
            for m in ms:
                if want_all or m.lower() in ("post","put","patch","delete"): print(f"{m.upper():6} {p}")
        return
    if cmd == "schema":
        # schema <DefinitionName> -- dump one OpenAPI definition's properties
        spec=_get(base,tok,"/v2/api-docs")
        defs=spec.get("definitions") or spec.get("components",{}).get("schemas",{})
        d=defs.get(sys.argv[2])
        if not d: print(f"No definition {sys.argv[2]}. Try: {', '.join(sorted(defs)[:20])} ..."); return
        for k,v in sorted(d.get("properties",{}).items()):
            print(f"  {k:32} {v.get('type','?'):10} {'enum='+str(v['enum']) if v.get('enum') else ''}")
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
