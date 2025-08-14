# ig_login_smoke.py
from pathlib import Path
from ig_api import read_credentials, IGRest

CFG = Path(__file__).resolve().parent / "ig_credentials.cfg"
creds = read_credentials(str(CFG))
acc_type = getattr(creds, "IG_ACC_TYPE", "DEMO")
host = "https://live-api.ig.com" if str(acc_type).upper() == "LIVE" else "https://demo-api.ig.com"
print(f"Using ACC_TYPE={acc_type} -> Host={host}")

try:
    with IGRest(creds) as ig:
        print("Login OK (session established).")
        # Optional: basic sanity call if your IGRest implements .me()
        try:
            me = ig.me()
            print("ME() OK:", me if isinstance(me, dict) else type(me))
        except Exception as _:
            pass
except Exception as e:
    print("Login FAILED.")
    raise
