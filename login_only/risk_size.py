# risk_size.py
"""
Compute a CFD position size from cash risk and stop distance (points).

Examples:
  # FTSE 100 (£10/pt), risk £50, stop 60 pts  -> returns size snapped to min size/step
  python risk_size.py --epic IX.D.FTSE.CFD.IP --risk 50 --stop-points 60

  # Same (alias flag you tried earlier)
  python risk_size.py --epic IX.D.FTSE.CFD.IP --risk-gbp 50 --stop-points 60

Notes:
- Uses /markets/{epic} to read contractSize (≈ currency per point per 1.0 size),
  min deal size, and min stop distance. Works great for indices/commodities.
- For some FX/SHARES epics, contractSize can be quirky on demo; the script will warn
  and suggest a fallback if the value looks missing/odd.
"""
import argparse, sys
from typing import Dict, Any
from math import floor, ceil

from rest_prices import rest_login, rest_market_by_epic  # reuse your working helpers

def fnum(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def snap(value: float, step: float, mode: str = "down") -> float:
    if step <= 0: 
        return value
    k = value / step
    if mode == "up":
        return ceil(k) * step
    if mode == "nearest":
        return round(k) * step
    return floor(k) * step  # default: down (conservative)

def get_point_value(md: Dict[str,Any]) -> float:
    """
    For CFDs on indices/commodities, instrument.contractSize is typically the
    currency value per index point for size=1. Example: FTSE 100 Cash (£10) → 10.
    """
    instr = md.get("instrument") or {}
    cs = fnum(instr.get("contractSize"))
    return cs or 0.0

def get_min_size_and_step(md: Dict[str,Any]) -> tuple[float, float]:
    rules = (md.get("dealingRules") 
             or md.get("instrument", {}).get("dealingRules") 
             or {})
    mds = rules.get("minDealSize") or {}
    min_size = fnum(mds.get("value"), 1.0)
    step = fnum(mds.get("step"), None)
    # Fall back: many index CFDs step the same as min size (e.g., 0.5)
    if step is None:
        step = min_size
    return (min_size, step)

def get_min_stop_points(md: Dict[str,Any]) -> float:
    rules = (md.get("dealingRules") 
             or md.get("instrument", {}).get("dealingRules") 
             or {})
    msl = rules.get("minStopOrLimitDistance") or rules.get("minStopDistance") or {}
    return fnum(msl.get("value"), 0.0)

def main():
    ap = argparse.ArgumentParser(description="Risk-based position sizing for IG CFDs")
    ap.add_argument("--epic", required=True, help="EPIC, e.g. IX.D.FTSE.CFD.IP")
    # accept either --risk or --risk-gbp (alias)
    ap.add_argument("--risk", type=float, help="Risk amount in account currency (e.g., 50)")
    ap.add_argument("--risk-gbp", dest="risk_gbp", type=float, help="Alias for --risk")
    ap.add_argument("--stop-points", type=float, required=True, help="Stop distance in points (index pts, pips, etc.)")
    ap.add_argument("--round", choices=["down","nearest","up"], default="down", help="Rounding mode to align to size step")
    args = ap.parse_args()

    risk_amt = args.risk if args.risk is not None else args.risk_gbp
    if risk_amt is None:
        sys.exit("Provide --risk (or --risk-gbp) with a number, e.g. --risk 50")

    base, h = rest_login()
    md = rest_market_by_epic(base, h, args.epic)

    instr = md.get("instrument") or {}
    name  = instr.get("name") or instr.get("epic") or args.epic
    currencies = instr.get("currencies") or md.get("market", {}).get("currencies") or []
    curr = (currencies[0].get("code") if currencies else "")

    point_value = get_point_value(md)
    min_size, size_step = get_min_size_and_step(md)
    min_stop = get_min_stop_points(md)

    if point_value <= 0:
        print(f"[WARN] Could not determine point value from contractSize for {name}.")
        print("       This script is most reliable on index/commodity CFDs (contractSize ≈ currency/point).")
        print("       If you still want a rough size: size ≈ risk / (stop_points * value_per_point).")
        sys.exit(1)

    if args.stop_points < min_stop:
        print(f"[WARN] stop-points={args.stop_points:g} is below market minimum {min_stop:g}.")
        print("       Your order would likely be rejected; consider increasing your stop distance.")
        # proceed anyway to show a size, but you should adjust

    # raw size before snapping
    size_raw = risk_amt / (args.stop_points * point_value)

    # snap to step and enforce minimum
    size_snapped = snap(size_raw, size_step, mode=args.round)
    if size_snapped < min_size:
        size_snapped = min_size

    # pretty print summary
    print(f"EPIC: {args.epic} | {name}")
    print(f"Account cc y: {curr or '(see account)'}")
    print(f"contractSize (≈ value/pt @ size=1): {point_value:g}")
    print(f"minDealSize: {min_size:g} | sizeStep: {size_step:g} | minStopPts: {min_stop:g}")
    print(f"Inputs -> risk={risk_amt:g}  stopPoints={args.stop_points:g}")
    print(f"Raw size = risk / (stopPts * valuePerPoint) = {size_raw:.6f}")
    print(f"Suggested size (rounded {args.round}) = {size_snapped:g}")

if __name__ == "__main__":
    main()
