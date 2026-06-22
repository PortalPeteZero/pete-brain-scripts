#!/usr/bin/env python3
"""Railway cron: refresh the CC data-map (public.data_map). Reads creds from ENV."""
import os, json, urllib.request, urllib.error
URL = os.environ["CC_SUPABASE_URL"]; SVC = os.environ["CC_SUPABASE_SERVICE_KEY"]
MAP = [
 ("Files & documents","Cross","Google Drive (drive_files index)","drive_files / Drive mount","any file/sheet/PDF/image"),
 ("Knowledge","Cross","CC Supabase vault_notes","cc-knowledge-api.py / Brain","1,909 notes + link graph + semantic"),
 ("Automations & crons","Cross","CC Supabase processes (type=cron)","cc-sql / Process Library","synced from automations.json"),
 ("Courses (catalogue)","Sygma","Sygma Portal public.courses","Portal (CC surfaces, never owns)","courses → Sygma Platform, not CC"),
]
rows=[{"domain":d,"owner_system":o,"home":h,"access":a,"notes":n,"sort":i*10} for i,(d,o,h,a,n) in enumerate(MAP)]
req=urllib.request.Request(f"{URL}/rest/v1/data_map?on_conflict=domain",data=json.dumps(rows).encode(),method="POST",
  headers={"apikey":SVC,"Authorization":f"Bearer {SVC}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=representation"})
out=json.loads(urllib.request.urlopen(req,timeout=60).read())
print(f"data-map refreshed: {len(out)} rows upserted")
