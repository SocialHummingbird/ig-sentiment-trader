# login_probe.py
# Direct /session probe using requests, to debug API key vs. environment vs. stray whitespace.

import requests, json
from pathlib import Path

CFG = Path(__file__).resolve().parent / "ig_credentials.cfg"
vals = {}
for line in CFG.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"): 
        continue
    if "=" in line:
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()

acc_type = vals.get("IG_ACC_TYPE", "DEMO").upper()
host = "https://live-api.ig.com" if acc_type == "LIVE" else "https://demo-api.ig.com"

api_key = vals.get("IG_API_KEY", "")
identifier = vals.get("IG_IDENTIFIER", "")
password = vals.get("IG_PASSWORD", "")

def mask(s, keep=4):
    return (s[:keep] + "…") if len(s) > keep else "…"

print("=== Probe config (masked) ===")
print("ACC_TYPE :", acc_type)
print("Host     :", host)
print("API_KEY  :", mask(api_key), "len=", len(api_key))
print("IDENT    :", identifier)
print("PASS_SET :", bool(password))

url = host + "/gateway/deal/session"
headers = {
    "X-IG-API-KEY": api_key,
    "Content-Type": "application/json",
    "Accept": "application/json; charset=UTF-8",
    "Version": "2",
}
body = {"identifier": identifier, "password": password}

print("\nPOST", url)
try:
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
    print("Status:", r.status_code)
    print("Resp headers (subset):", {k: r.headers.get(k) for k in ["CST","X-SECURITY-TOKEN","Date"]})
    print("Body:", r.text[:1000])
    r.raise_for_status()
    print("\nLOGIN OK via probe.")
except requests.HTTPError as e:
    print("\nLOGIN FAILED via probe:", e)
    # show exact body from IG
    try:
        print("Body:", r.text[:1000])
    except Exception:
        pass
    raise
