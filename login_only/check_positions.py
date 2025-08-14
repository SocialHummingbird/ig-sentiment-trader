# check_positions.py
# Prints open positions in a readable table (and supports --raw).
# Works when run as a module:      python -m login_only.check_positions
# or as a script from anywhere:    python login_only/check_positions.py

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running directly from project root or anywhere
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
    ap = argparse.ArgumentParser(description="Show IG open positions.")
    ap.add_argument("--raw", action="store_true", help="Print raw JSON instead of a table.")
    args = ap.parse_args()

    creds = read_credentials(str(Path(ROOT, "login_only", "ig_credentials.cfg")))
    with IGRest(creds) as ig:
        data = ig.positions()

    if args.raw:
        print(json.dumps(data, indent=2))
        return

    positions = data.get("positions") or []
    print("\n=== Open Positions ===")
    if not positions:
        print("No open positions.")
        return

    # Build rows
    rows: List[List[str]] = []
    total_count = 0
    for item in positions:
        pos = item.get("position", {})
        mkt = item.get("market", {})
        name   = mkt.get("instrumentName") or pos.get("instrumentName") or "-"
        epic   = mkt.get("epic") or "-"
        dirn   = pos.get("direction") or "-"
        size   = pos.get("size") or pos.get("dealSize") or "-"
        entry  = pos.get("level") or pos.get("openLevel") or "-"
        stop   = pos.get("stopLevel") or "-"
        limit  = pos.get("limitLevel") or "-"
        bid    = mkt.get("bid")
        offer  = mkt.get("offer")
        mid    = None
        try:
            if bid is not None and offer is not None:
                mid = (float(bid) + float(offer)) / 2.0
        except Exception:
            mid = None

        rows.append([
            str(name),
            str(epic),
            str(dirn),
            fmt_num(size, 2),
            fmt_num(entry, 2),
            fmt_num(stop, 2),
            fmt_num(limit, 2),
            fmt_num(mid, 2) if mid is not None else "-"
        ])
        total_count += 1

    # Print fixed-width table
    headers = ["Instrument", "Epic", "Dir", "Size", "Entry", "Stop", "Limit", "Mid"]
    widths  = [24, 18, 4, 6, 10, 10, 10, 10]

    def line(cols: List[str]) -> str:
        return " | ".join(c[:w].ljust(w) for c, w in zip(cols, widths))

    print(line(headers))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    for r in rows:
        print(line(r))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    print(f"Total open positions: {total_count}\n")


if __name__ == "__main__":
    main()
