import json
import time

import requests

BASE = "http://127.0.0.1:8843"

print("== check ==")
r = requests.get(f"{BASE}/api/update/check", timeout=90).json()
print({k: r.get(k) for k in ("ok", "local_version", "latest_sha", "up_to_date", "needs_update", "relation", "error")})

print("== start ==")
print(requests.get(f"{BASE}/api/update/start", timeout=30).json())

last = None
for i in range(240):
    time.sleep(2)
    s = requests.get(f"{BASE}/api/update/status", timeout=20).json()
    mb = s.get("received", 0) / 1048576
    total = s.get("total", 0) / 1048576
    line = f"{i*2:>4}s {s.get('mirror',''):<22} {mb:6.1f}/{total:.1f} MB done={s.get('done')} err={s.get('error')}"
    if line[:60] != (last or "")[:60] and (i % 5 == 0 or s.get("done") or s.get("error")):
        print(line)
        last = line
    if s.get("done") or s.get("error"):
        print("FINAL:", json.dumps({k: s.get(k) for k in ("running", "done", "error", "path", "mirror", "total", "received")}, ensure_ascii=False))
        break
