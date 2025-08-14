# debug_login.py
import json, requests
from credentials import load_credentials

def main():
    c = load_credentials("ig_credentials.cfg")
    base = "https://demo-api.ig.com/gateway/deal" if c["IG_ACC_TYPE"].upper()=="DEMO" else "https://api.ig.com/gateway/deal"

    print("DEBUG:", f"ident={c['IG_IDENTIFIER']!r}", f"acc_type={c['IG_ACC_TYPE']}", f"key_len={len(c['IG_API_KEY'])}")

    headers = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2"
    }
    payload = {"identifier": c["IG_IDENTIFIER"], "password": c["IG_PASSWORD"]}

    r = requests.post(f"{base}/session", headers=headers, data=json.dumps(payload), timeout=30)
    print("Status:", r.status_code)
    print("Body:", r.text)
    print("CST:", r.headers.get("CST"))
    print("X-SECURITY-TOKEN:", r.headers.get("X-SECURITY-TOKEN"))

if __name__ == "__main__":
    main()
