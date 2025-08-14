# check_ig_credentials.py
from pathlib import Path
from ig_api import read_credentials

CFG = Path(__file__).resolve().parent / "ig_credentials.cfg"
creds = read_credentials(str(CFG))

acc_type = getattr(creds, "IG_ACC_TYPE", "DEMO")
api_key  = getattr(creds, "IG_API_KEY", "")
user     = getattr(creds, "IG_IDENTIFIER", "")
pw_set   = bool(getattr(creds, "IG_PASSWORD", ""))

host = "https://live-api.ig.com" if str(acc_type).upper() == "LIVE" else "https://demo-api.ig.com"

def _mask(s: str, keep: int = 4) -> str:
    s = s or ""
    return (s[:keep] + "…") if len(s) > keep else "…"

print("=== IG credentials (masked) ===")
print("ACC_TYPE :", acc_type)
print("API_KEY  :", _mask(api_key))
print("USER     :", user)
print("PASS_SET :", pw_set)
print("Host     :", host)
