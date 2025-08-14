# check_orders.py
# Lists working (pending) orders in a readable table (or --raw for JSON).
# Run as a module:  python -m login_only.check_orders
# Or directly:      python login_only/check_orders.py

from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from login_only.ig_api import read_credentials, IGRest  # type: ignore


def fmt_num(x: Any, dps: int = 2) -> str:
    try:
        return f"{float(x):.{dps}f}"
    except Exception:
        return "-"

def main():
    ap = argparse.ArgumentParser(description="Show IG working (pending) orders.")
    ap.add_argument("--raw", action="store_true", help="Print raw JSON instead of a table.")
    args = ap.parse_args()

    creds = read_credentials(str(Path(ROOT, "login_only", "ig_credentials.cfg")))
    with IGRest(creds) as ig:
        # Use the same session; IG endpoint for working orders:
        url = f"{ig.base}/workingorders"
        r = ig.sess.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()

    if args.raw:
        print(json.dumps(data, indent=2))
        return

    orders = data.get("workingOrders") or data.get("workingorders") or []
    print("\n=== Working Orders ===")
    if not orders:
        print("No working orders.")
        return

    rows: List[List[str]] = []
    for o in orders:
        wo  = o.get("workingOrderData", {})
        mkt = o.get("marketData", {})
        name  = mkt.get("instrumentName") or "-"
        epic  = mkt.get("epic") or "-"
        dirn  = wo.get("direction") or "-"
        size  = wo.get("size") or "-"
        level = wo.get("level") or "-"
        stop  = wo.get("stopDistance") or wo.get("stopLevel") or "-"
        limit = wo.get("limitDistance") or wo.get("limitLevel") or "-"
        typ   = wo.get("type") or "-"
        rows.append([str(name), str(epic), str(dirn), fmt_num(size,2), fmt_num(level,2), fmt_num(stop,2), fmt_num(limit,2), str(typ)])

    headers = ["Instrument", "Epic", "Dir", "Size", "Level", "Stop", "Limit", "Type"]
    widths  = [24, 18, 4, 6, 10, 10, 10, 8]
    def line(cols: List[str]) -> str:
        return " | ".join(c[:w].ljust(w) for c, w in zip(cols, widths))

    print(line(headers))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    for r in rows:
        print(line(r))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    print(f"Total working orders: {len(rows)}\n")

if __name__ == "__main__":
    main()
