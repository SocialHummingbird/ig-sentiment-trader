# rest_orders_dry.py
"""
Dry-run order helper (REST-only) for IG.
- Logs in once and reuses the session (CST/XST/IG-ACCOUNT-ID)
- Can take --epic directly OR --auto "search term" to find a tradable EPIC
- Validates min deal size and stop/limit distances from market metadata
- Prints a clear summary; sends nothing unless you pass --live

Examples:
  # Dry-run buy 1 contract of US 500 with stop/limit distances
  python rest_orders_dry.py --auto "US 500" --direction BUY --size 1 --stop-points 50 --limit-points 100

  # Exact EPIC (FTSE 100 CFD), dry-run sell
  python rest_orders_dry.py --epic IX.D.FTSE.CFD.IP --direction SELL --size 2 --stop-points 80 --limit-points 160

  # Actually place the order on DEMO (careful): add --live
  python rest_orders_dry.py --epic IX.D.FTSE.CFD.IP --direction BUY --size 1 --stop-points 50 --limit-points 100 --live
"""
import argparse
from typing import Dict, Any, Tuple, List, Optional

import requests

from rest_prices import (
    rest_login,
    rest_search,
    rest_market_by_epic,
    rest_prices as fetch_prices,         # used to "probe" an EPIC
    prefer_cfd_or_promote,               # pick CFD/accessible EPIC
)

# -------- helpers --------
def probe_prices_ok(base: str, h: Dict[str, str], epic: str) -> bool:
    try:
        _ = fetch_prices(base, h, epic, resolution="DAY", max_points=1)
        return True
    except requests.HTTPError:
        return False
    except Exception:
        return False

def pick_epic(base: str, h: Dict[str, str], auto_term: str) -> Tuple[str, str]:
    rows = rest_search(base, h, auto_term)
    if not rows:
        raise RuntimeError(f"No IG instruments found for search term: {auto_term}")
    epic, note = prefer_cfd_or_promote(base, h, rows)
    if not probe_prices_ok(base, h, epic):
        # last attempt: walk rows and pick first that probes
        for m in rows:
            e = (m.get("epic") or "").strip()
            if e and probe_prices_ok(base, h, e):
                return e, "fallback: first search/probe OK"
        raise RuntimeError(f"No accessible EPIC found for {auto_term}")
    return epic, note

def market_rules(md: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Extract basic dealing rules from /markets/{epic} response."""
    inst = md.get("instrument", {}) or {}
    rules = md.get("dealingRules", {}) or {}

    def _num(path: List[str], default: Optional[float]=None) -> Optional[float]:
        cur = rules
        for k in path:
            cur = (cur or {}).get(k, {})
        # Some fields are {"value": "1.0", "unit": "..."}
        if isinstance(cur, dict) and "value" in cur:
            try: return float(cur["value"])
            except Exception: return default
        try: return float(cur)  # sometimes it's already a number
        except Exception: return default

    return {
        "minDealSize": _num(["minDealSize"]),
        "minNormalStopDistance": _num(["minNormalStopOrLimitDistance"]),
        "minStepDistance": _num(["minStepDistance"]),   # not always present
        "lotSize": _num(["lotSize"]),                   # informational
        "minLimitDistance": _num(["minNormalStopOrLimitDistance"]),
        "minStopDistance": _num(["minNormalStopOrLimitDistance"]),
        "minDealIncrement": _num(["minDealIncrement"]), # rarely present
        "currency": (inst.get("currencies") or [{}])[0].get("code"),
    }

def round_if_step(value: float, step: Optional[float]) -> float:
    if not step or step <= 0:
        return value
    n = round(value / step)
    return max(step, n * step)

def build_order_payload(epic: str, direction: str, size: float,
                        currency: Optional[str],
                        stop_points: Optional[float], limit_points: Optional[float]) -> Dict[str, Any]:
    payload = {
        "epic": epic,
        "expiry": "-",                # cash instruments use "-"
        "direction": direction.upper(),
        "size": size,
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "guaranteedStop": False,
        "forceOpen": True,
    }
    if currency:
        payload["currencyCode"] = currency
    if stop_points is not None:
        payload["stopDistance"] = stop_points
    if limit_points is not None:
        payload["limitDistance"] = limit_points
    return payload

def place_market(base: str, h: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    h2 = {**h, "Accept": "application/json", "Content-Type": "application/json", "Version": "2"}
    r = requests.post(f"{base}/positions/otc", headers=h2, json=payload, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return {"ok": False, "status": r.status_code, "body": r.text}
    data = r.json() if "application/json" in (r.headers.get("Content-Type") or "") else {}
    return {"ok": True, "status": r.status_code, "body": data}

# -------- CLI --------
def main():
    ap = argparse.ArgumentParser(description="IG REST dry-run orders (with validation)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--epic", help="Exact EPIC to trade")
    g.add_argument("--auto", help="Search term, e.g., 'US 500', 'Germany 40', 'EUR/USD', 'Gold'")

    ap.add_argument("--direction", required=True, choices=["BUY", "SELL"], help="BUY or SELL")
    ap.add_argument("--size", type=float, required=True, help="Deal size (contracts)")

    ap.add_argument("--stop-points", type=float, help="Stop distance in points")
    ap.add_argument("--limit-points", type=float, help="Limit distance in points")

    ap.add_argument("--live", action="store_true", help="Send to IG (DEMO). Omit for dry-run.")
    args = ap.parse_args()

    # Login once
    base, h = rest_login()

    # Resolve EPIC
    if args.epic:
        epic = args.epic
        note = "epic provided"
    else:
        epic, note = pick_epic(base, h, args.auto)

    # Market metadata & rules
    md = rest_market_by_epic(base, h, epic)
    rules = market_rules(md)

    # Validate & round sizes (very light-touch)
    size = args.size
    min_size = rules["minDealSize"] or 0.0
    if size < min_size:
        print(f"[ADJUST] size {size} < minDealSize {min_size} → using {min_size}")
        size = min_size

    size = round_if_step(size, rules.get("minDealIncrement"))

    stop_pts = args.stop_points
    limit_pts = args.limit_points

    # Validate distances if provided
    min_stop = rules["minStopDistance"] or 0.0
    min_limit = rules["minLimitDistance"] or 0.0
    step = rules.get("minStepDistance")

    if stop_pts is not None and stop_pts < min_stop:
        print(f"[ADJUST] stop_distance {stop_pts} < minStopDistance {min_stop} → using {min_stop}")
        stop_pts = min_stop
    if limit_pts is not None and limit_pts < min_limit:
        print(f"[ADJUST] limit_distance {limit_pts} < minLimitDistance {min_limit} → using {min_limit}")
        limit_pts = min_limit

    if step:
        if stop_pts is not None:
            stop_pts = round_if_step(stop_pts, step)
        if limit_pts is not None:
            limit_pts = round_if_step(limit_pts, step)

    # Build payload
    payload = build_order_payload(epic, args.direction, size, rules["currency"], stop_pts, limit_pts)

    # Summary
    print("\n=== DRY RUN SUMMARY ===" if not args.live else "\n=== LIVE ORDER SUMMARY (DEMO) ===")
    print(f"Resolved: {note}")
    print(f"EPIC: {epic}")
    print(f"Direction: {args.direction}")
    print(f"Size: {size}  (minDealSize={min_size})")
    print(f"Currency: {rules['currency']}")
    if stop_pts is not None:
        print(f"Stop Distance:  {stop_pts} pts  (min ~ {min_stop}, step ~ {step})")
    else:
        print("Stop Distance:  (none)")
    if limit_pts is not None:
        print(f"Limit Distance: {limit_pts} pts  (min ~ {min_limit}, step ~ {step})")
    else:
        print("Limit Distance: (none)")
    print(f"Payload: {payload}")

    if not args.live:
        print("\n(DRY-RUN ONLY: no request sent)")
        return

    # Send to IG (DEMO)
    res = place_market(base, h, payload)
    if res["ok"]:
        body = res["body"]
        deal_ref = (body or {}).get("dealReference")
        print(f"\nSENT OK  status={res['status']}  dealReference={deal_ref}")
    else:
        print(f"\nSEND FAILED  status={res['status']}\n{res['body']}")

if __name__ == "__main__":
    main()
