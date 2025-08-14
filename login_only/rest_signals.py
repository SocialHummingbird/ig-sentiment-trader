# rest_signals.py
"""
REST-only SMA/RSI signals for IG with single login + date-range support.

Examples:
  # Intraday signals, bigger window
  python rest_signals.py --auto "US 500" --resolution MINUTE_5 --max 300 --show 20

  # Daily signals with date range
  python rest_signals.py --epic IX.D.FTSE.CFD.IP --resolution DAY --from-utc 2025-01-01T00:00:00Z --to-utc 2025-08-12T00:00:00Z
"""
import argparse
from typing import Dict, Any, List, Tuple, Optional

import pandas as pd
import requests

from rest_prices import rest_login, rest_search, rest_prices as fetch_prices, to_frame

# ---------- indicators ----------
def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

# ---------- pick a workable EPIC ----------
def first_epic_with_prices(base: str, h: Dict[str,str], rows: List[Dict[str,Any]]) -> Tuple[str,str]:
    tried = []
    for m in rows:
        epic = (m.get("epic") or m.get("instrument", {}).get("epic") or "").strip()
        if not epic: 
            continue
        tried.append(epic)
        try:
            _ = fetch_prices(base, h, epic, resolution="DAY", max_points=1)
            return epic, "search->probe OK"
        except requests.HTTPError:
            continue
        except Exception:
            continue
    raise RuntimeError(f"No search results returned usable /prices (tried {len(tried)} epics).")

# ---------- CLI flow ----------
def main():
    ap = argparse.ArgumentParser(description="IG REST signals (SMA/RSI, date-range)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--auto", help="Search term (e.g., 'US 500', 'Germany 40', 'EUR/USD', 'Gold')")
    g.add_argument("--epic", help="Exact EPIC to fetch prices")

    ap.add_argument("--resolution", default="DAY",
                    help="MINUTE, MINUTE_5, MINUTE_15, HOUR, DAY, WEEK, MONTH (default DAY)")
    ap.add_argument("--max", type=int, default=300, help="Number of candles to fetch (default 300)")
    ap.add_argument("--from-utc", dest="from_utc", help="ISO8601 UTC start (e.g., 2025-06-01T00:00:00Z)")
    ap.add_argument("--to-utc", dest="to_utc", help="ISO8601 UTC end (e.g., 2025-08-12T00:00:00Z)")
    ap.add_argument("--show", type=int, default=12, help="Print last N rows (default 12)")

    args = ap.parse_args()
    base, h = rest_login()

    if args.auto:
        rows = rest_search(base, h, args.auto)
        if not rows:
            print("No matches for search term."); return
        try:
            epic, note = first_epic_with_prices(base, h, rows)
            print(f"[AUTO] Using EPIC: {epic} ({note})")
        except Exception as e:
            print(str(e)); return
    else:
        epic = args.epic

    # fetch candles
    try:
        payload = fetch_prices(base, h, epic, args.resolution, args.max, args.from_utc, args.to_utc)
    except requests.HTTPError as e:
        print(f"[PRICES ERROR] {e.response.status_code} {e.response.url}\n{e.response.text}")
        return

    df = to_frame(payload)
    if df.empty:
        print("No candles returned."); return

    # indicators
    df["sma20"] = sma(df["close"], 20)
    df["sma50"] = sma(df["close"], 50)
    df["rsi14"] = rsi(df["close"], 14)

    last = df.iloc[-1]
    sma20_ok = pd.notna(last["sma20"])
    sma50_ok = pd.notna(last["sma50"])
    rsi_ok   = pd.notna(last["rsi14"])

    if sma20_ok and sma50_ok and rsi_ok:
        if last["close"] > last["sma20"] > last["sma50"] and last["rsi14"] >= 55:
            signal = "BUY"
        elif last["close"] < last["sma20"] < last["sma50"] and last["rsi14"] <= 45:
            signal = "SELL"
        else:
            signal = "HOLD"
    else:
        signal = "HOLD (warming up; need enough candles for SMA/RSI)"

    print(f"\nEPIC: {epic} | res={args.resolution} | candles={len(df)}")
    tail = df[["open","high","low","close","sma20","sma50","rsi14"]].tail(args.show)
    print(tail.to_string(float_format=lambda x: f"{x:.5f}"))
    s20 = f"{last['sma20']:.5f}" if sma20_ok else "nan"
    s50 = f"{last['sma50']:.5f}" if sma50_ok else "nan"
    r14 = f"{last['rsi14']:.1f}" if rsi_ok else "nan"
    print(f"\nLast bar -> close={last['close']:.5f}  SMA20={s20}  SMA50={s50}  RSI14={r14}  =>  {signal}")

if __name__ == "__main__":
    main()
