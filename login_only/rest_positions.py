# rest_positions.py
"""
List and (optionally) close IG positions (REST).
Examples:
  python rest_positions.py --list
  python rest_positions.py --close <DEAL_ID>            # full close (defaults to full size)
  python rest_positions.py --close <DEAL_ID> --size 0.5 # partial
  python rest_positions.py --close-epic IX.D.FTSE.CFD.IP
"""
import argparse, json, time, requests
from typing import Dict, Any, List

from rest_prices import rest_login  # reuse your working login helper

def get_positions(base: str, h: Dict[str,str]) -> List[Dict[str,Any]]:
    hdr = {**h, "Accept":"application/json", "Version":"2"}
    r = requests.get(f"{base}/positions", headers=hdr, timeout=30)
    r.raise_for_status()
    out = []
    for p in (r.json().get("positions") or []):
        pos = p.get("position") or {}
        mkt = p.get("market") or {}
        out.append({
            "dealId": pos.get("dealId"),
            "direction": (pos.get("direction") or "").upper(),
            "size": float(pos.get("size") or 0),
            "epic": pos.get("epic") or mkt.get("epic"),
            "name": mkt.get("instrumentName") or mkt.get("marketName") or "",
            "currency": pos.get("currency") or (mkt.get("currencies") or [{}])[0].get("code"),
            "level": float(pos.get("level") or 0),
            "stopLevel": pos.get("stopLevel"),
            "limitLevel": pos.get("limitLevel"),
            "expiry": pos.get("expiry") or mkt.get("expiry") or "-",
        })
    return out

def print_positions(rows: List[Dict[str,Any]]):
    print(f"Open positions: {len(rows)}")
    for r in rows:
        print(f"- dealId={r['dealId']} | epic={r['epic']} | name={r['name']} "
              f"| dir={r['direction']} | size={r['size']} | level={r['level']} "
              f"| stop={r['stopLevel']} | limit={r['limitLevel']} | currency={r['currency']}")

def opposite(d: str) -> str:
    return "SELL" if d.upper()=="BUY" else "BUY"

def delete_close(base: str, h: Dict[str,str], payload: Dict[str,Any]) -> requests.Response:
    # Primary close endpoint: DELETE /positions/otc (v1)
    hdr = {**h, "Accept":"application/json", "Content-Type":"application/json", "Version":"1"}
    return requests.delete(f"{base}/positions/otc", headers=hdr, data=json.dumps(payload), timeout=30)

def post_net_close(base: str, h: Dict[str,str], row: Dict[str,Any], size: float) -> requests.Response:
    # Fallback: POST /positions/otc (v2) with opposite direction and forceOpen=false to net/close
    hdr = {**h, "Accept":"application/json", "Content-Type":"application/json", "Version":"2"}
    payload = {
        "epic": row["epic"],
        "expiry": row["expiry"] or "-",
        "direction": opposite(row["direction"]),
        "size": float(size),
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "forceOpen": False,
        "guaranteedStop": False,
        "currencyCode": row["currency"],
        # NOTE: omit dealReference to avoid pattern errors on demo
    }
    return requests.post(f"{base}/positions/otc", headers=hdr, data=json.dumps(payload), timeout=30)

def try_close_once(base: str, h: Dict[str,str], row: Dict[str,Any], size: float) -> bool:
    # 1) try DELETE by dealId
    p1 = {"dealId": row["dealId"], "size": size, "orderType":"MARKET", "timeInForce":"FILL_OR_KILL",
          "direction": opposite(row["direction"])}
    r = delete_close(base, h, p1)
    if r.status_code == 200:
        print(f"CLOSE OK (dealId)  ref={r.json().get('dealReference')}")
        return True
    print(f"[WARN] Close by dealId failed: {r.status_code}\n{r.text}\nTrying epic+expiry…")

    # 2) try DELETE by epic+expiry
    p2 = {"epic": row["epic"], "expiry": row["expiry"] or "-", "size": size,
          "orderType":"MARKET", "timeInForce":"FILL_OR_KILL", "direction": opposite(row["direction"])}
    r2 = delete_close(base, h, p2)
    if r2.status_code == 200:
        print(f"CLOSE OK (epic/expiry)  ref={r2.json().get('dealReference')}")
        return True
    print(f"[WARN] Close by epic/expiry failed: {r2.status_code}\n{r2.text}\nFalling back to POST netting…")

    # 3) fallback: POST to net off
    r3 = post_net_close(base, h, row, size)
    if r3.status_code == 200:
        print(f"CLOSE OK (POST net)   ref={r3.json().get('dealReference')}")
        return True
    print(f"[ERROR] POST net close failed: {r3.status_code}\n{r3.text}")
    return False

def main():
    ap = argparse.ArgumentParser(description="IG REST positions: list/close")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="List open positions")
    g.add_argument("--close", metavar="DEAL_ID", help="Close by dealId")
    g.add_argument("--close-epic", metavar="EPIC", help="Close all on EPIC")
    ap.add_argument("--size", type=float, help="Size to close (defaults to full)")
    args = ap.parse_args()

    base, h = rest_login()

    if args.list:
        rows = get_positions(base, h)
        print_positions(rows); return

    rows = get_positions(base, h)

    if args.close:
        row = next((x for x in rows if x["dealId"] == args.close), None)
        if not row:
            print("No position found with that dealId."); return
        size = args.size or row["size"]
        try_close_once(base, h, row, size); return

    if args.close_epic:
        rows = [x for x in rows if x["epic"] == args.close_epic]
        if not rows:
            print("No positions on that EPIC."); return
        for row in rows:
            size = args.size or row["size"]
            try_close_once(base, h, row, size)
        return

if __name__ == "__main__":
    main()
