# rest_amend.py
"""
Amend stops/limits on an open OTC position.
Examples:
  python rest_amend.py --deal <DEAL_ID> --set-stop-level 9080 --set-limit-level 9300
  python rest_amend.py --deal <DEAL_ID> --set-stop-dist 60 --set-limit-dist 120
  python rest_amend.py --deal <DEAL_ID> --remove-stop
  python rest_amend.py --deal <DEAL_ID> --remove-limit
"""
import argparse, json, requests
from typing import Dict, Any, Optional
from rest_prices import rest_login, rest_market_by_epic

def get_position(base: str, h: Dict[str,str], deal_id: str) -> Dict[str,Any]:
    hdr = {**h, "Accept":"application/json", "Version":"2"}
    r = requests.get(f"{base}/positions/{deal_id}", headers=hdr, timeout=30)
    r.raise_for_status()
    return r.json()

def current_mid(base: str, h: Dict[str,str], epic: str) -> float:
    md = rest_market_by_epic(base, h, epic)  # GET /markets/{epic} (v3+) gives snapshot. :contentReference[oaicite:3]{index=3}
    snap = md.get("snapshot") or {}
    bid = snap.get("bid"); offer = snap.get("offer") or snap.get("ask")
    if bid is not None and offer is not None:
        return (float(bid) + float(offer)) / 2.0
    ltr = snap.get("lastTraded")
    if ltr is not None:
        return float(ltr)
    raise RuntimeError("No price snapshot available to compute mid.")

def amend_levels(base: str, h: Dict[str,str], deal_id: str,
                 stop_level: Optional[float], limit_level: Optional[float]) -> Dict[str,Any]:
    # Correct endpoint: PUT /positions/otc/{dealId} (Version 2). :contentReference[oaicite:4]{index=4}
    hdr = {**h, "Accept":"application/json", "Content-Type":"application/json", "Version":"2"}
    body: Dict[str,Any] = {}
    body["stopLevel"]  = stop_level if stop_level is not None else None
    body["limitLevel"] = limit_level if limit_level is not None else None
    r = requests.put(f"{base}/positions/otc/{deal_id}", headers=hdr, data=json.dumps(body), timeout=30)
    if r.status_code != 200:
        print(f"[AMEND ERROR] {r.status_code}\n{r.text}")
        r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser(description="Amend SL/TP on an IG OTC position")
    ap.add_argument("--deal", required=True, help="dealId to amend")
    ap.add_argument("--set-stop-level", type=float)
    ap.add_argument("--set-limit-level", type=float)
    ap.add_argument("--set-stop-dist",  type=float, help="distance (points) from current mid")
    ap.add_argument("--set-limit-dist", type=float, help="distance (points) from current mid")
    ap.add_argument("--remove-stop", action="store_true")
    ap.add_argument("--remove-limit", action="store_true")
    args = ap.parse_args()

    base, h = rest_login()

    # fetch current position (get EPIC + direction; handle epic under market.* too)
    p = get_position(base, h, args.deal)
    pos = p.get("position") or {}
    mkt = p.get("market") or {}
    epic = pos.get("epic") or mkt.get("epic")
    direction = (pos.get("direction") or "").upper()

    stop_level  = args.set_stop_level
    limit_level = args.set_limit_level

    # Convert distances to absolute levels using current mid
    if args.set_stop_dist is not None or args.set_limit_dist is not None:
        mid = current_mid(base, h, epic)
        if args.set_stop_dist is not None:
            stop_level = mid - args.set_stop_dist if direction == "BUY" else mid + args.set_stop_dist
        if args.set_limit_dist is not None:
            limit_level = mid + args.set_limit_dist if direction == "BUY" else mid - args.set_limit_dist

    if args.remove_stop:
        stop_level = None
    if args.remove_limit:
        limit_level = None

    res = amend_levels(base, h, args.deal, stop_level, limit_level)
    print("AMEND OK:", res.get("dealReference"))

if __name__ == "__main__":
    main()
