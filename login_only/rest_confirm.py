# rest_confirm.py
"""
Confirm an IG OTC order by dealReference (REST) with retries.
Usage:
  python rest_confirm.py --ref Q9MQALMSAMNT28R
"""
import argparse
import time
import requests
from rest_prices import rest_login  # reuse single-login helper

def get_confirm(base: str, h: dict, deal_ref: str, version: str) -> requests.Response:
    hdr = {**h, "Accept": "application/json", "Version": version}
    return requests.get(f"{base}/confirms/{deal_ref}", headers=hdr, timeout=30)

def confirm_with_retry(base: str, h: dict, deal_ref: str, attempts: int = 6):
    """
    Try v1 then v2, with exponential backoff (0.5s, 1s, 2s, 3s, 4s, 5s).
    Returns dict on success; raises on final failure.
    """
    delays = [0.5, 1, 2, 3, 4, 5]
    for i in range(min(attempts, len(delays))):
        for ver in ("1", "2"):
            r = get_confirm(base, h, deal_ref, ver)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return {"raw": r.text}
            # 404 on v1 sometimes means “try v2” right away; others we retry
            if ver == "1" and r.status_code == 404:
                continue
        time.sleep(delays[i])
    # final try once more, so we can show the latest server message
    r = get_confirm(base, h, deal_ref, "2")
    r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser(description="Confirm an IG deal by dealReference")
    ap.add_argument("--ref", required=True, help="dealReference returned by order placement")
    args = ap.parse_args()

    base, h = rest_login()
    try:
        data = confirm_with_retry(base, h, args.ref)
        print(data if data else "No confirmation payload returned.")
    except requests.HTTPError as e:
        r = e.response
        body = r.text if r is not None else str(e)
        print(f"[CONFIRM ERROR] {getattr(r,'status_code', '??')} {getattr(r,'url','')}\n{body}")
        print("\nTip: use 'python rest_positions.py --list' to verify the position and get the dealId.")
    except Exception as e:
        print(f"[CONFIRM ERROR] {e}\nTip: use 'python rest_positions.py --list' to verify the position and get the dealId.")

if __name__ == "__main__":
    main()
