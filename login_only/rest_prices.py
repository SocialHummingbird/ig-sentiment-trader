# rest_prices.py
"""
REST-only search + candles fetcher for IG with single login + CFD promotion
+ date-range support and tolerant normalization.

Usage:
  # Search CFD instruments
  python rest_prices.py --search "US 500" --instrument CFD

  # Auto-pick first CFD/accessible EPIC and fetch intraday candles
  python rest_prices.py --auto "US 500" --instrument CFD --resolution MINUTE_5 --max 300

  # Fetch by known EPIC, big window
  python rest_prices.py --epic IX.D.FTSE.CFD.IP --resolution DAY --max 300

  # Or with explicit date range (UTC ISO8601)
  python rest_prices.py --epic IX.D.FTSE.CFD.IP --resolution DAY --from-utc 2025-05-01T00:00:00Z --to-utc 2025-08-12T00:00:00Z
"""
import argparse
import json
from typing import Dict, Any, List, Tuple, Optional

import requests
import pandas as pd

from credentials import load_credentials


# ---------- REST session helpers ----------
def rest_login() -> Tuple[str, Dict[str, str]]:
    c = load_credentials("ig_credentials.cfg")
    base = "https://demo-api.ig.com/gateway/deal" if c["IG_ACC_TYPE"].upper() == "DEMO" else "https://api.ig.com/gateway/deal"
    h = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2",
    }
    r = requests.post(f"{base}/session", headers=h,
                      json={"identifier": c["IG_IDENTIFIER"], "password": c["IG_PASSWORD"]},
                      timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        print("[LOGIN ERROR]", r.status_code, r.text)
        raise
    sess = r.json()
    cst = r.headers.get("CST", "")
    xst = r.headers.get("X-SECURITY-TOKEN", "")
    acct = sess.get("currentAccountId", "")
    auth = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
        "IG-ACCOUNT-ID": acct,  # important for /prices
    }
    return base, auth


def rest_search(base: str, h: Dict[str, str], term: str) -> List[Dict[str, Any]]:
    h2 = {**h, "Accept": "application/json", "Version": "1"}
    r = requests.get(f"{base}/markets", headers=h2, params={"searchTerm": term}, timeout=30)
    r.raise_for_status()
    return r.json().get("markets", []) or []


def rest_market_by_epic(base: str, h: Dict[str, str], epic: str) -> Dict[str, Any]:
    h2 = {**h, "Accept": "application/json", "Version": "3"}
    r = requests.get(f"{base}/markets/{epic}", headers=h2, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_prices(
    base: str,
    h: Dict[str, str],
    epic: str,
    resolution: str = "DAY",
    max_points: Optional[int] = 30,
    from_utc: Optional[str] = None,
    to_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If from_utc/to_utc are provided, they take precedence over max_points.
    Use ISO8601 UTC, e.g. 2025-06-01T00:00:00Z
    """
    h2 = {**h, "Accept": "application/json", "Version": "3"}
    params: Dict[str, Any] = {"resolution": resolution}
    if from_utc or to_utc:
        if from_utc:
            params["from"] = from_utc
        if to_utc:
            params["to"] = to_utc
    else:
        params["max"] = max_points or 30

    r = requests.get(f"{base}/prices/{epic}", headers=h2, params=params, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        print(f"[PRICES ERROR] {r.status_code} {r.url}\n{r.text}")
        raise
    return r.json()


# ---------- CFD EPIC promotion helpers ----------
def promote_to_cfd_epics(epic: str) -> List[str]:
    # UA.D.AAPL.CASH.IP -> UA.D.AAPL.CFD.IP + IX.D.AAPL.CFD.IP, etc.
    candidates = set()
    parts = epic.split(".")
    if len(parts) >= 5:
        p = parts[:]
        if p[3] == "CASH":
            p[3] = "CFD"
        candidates.add(".".join(p))
        p2 = p[:]
        p2[0] = "IX"
        candidates.add(".".join(p2))
    candidates.add(epic.replace(".CASH.", ".CFD."))
    candidates.add(epic.replace(".CASH.", ".CFD.").replace("UA.D.", "IX.D.").replace("KA.D.", "IX.D.").replace("UD.D.", "IX.D."))
    return [c for c in candidates if c and c != epic]


def probe_prices_ok(base: str, h: Dict[str, str], epic: str) -> bool:
    try:
        _ = rest_prices(base, h, epic, resolution="DAY", max_points=1)
        return True
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 404):
            return False
        return False
    except Exception:
        return False


def prefer_cfd_or_promote(base: str, h: Dict[str, str], rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    for m in rows:
        e = m.get("epic") or ""
        if ".CFD." in e or ".IF" in e or ".FX" in e or ".MT." in e or ".CS." in e:
            # many CFD/Index/FX epics follow these patterns
            if probe_prices_ok(base, h, e):
                return e, "direct/probed OK"
    if not rows:
        return "", "no results"
    first = rows[0].get("epic") or ""
    if not first:
        return "", "no epic"
    for cand in promote_to_cfd_epics(first):
        if probe_prices_ok(base, h, cand):
            return cand, f"promoted from {first} -> {cand}"
    return first, "only non-accessible instruments in search"


# ---------- Normalization ----------
def _mid(bid: float | None, ask: float | None, ltr: float | None) -> float | None:
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return ltr


def to_frame(prices_payload: Dict[str, Any]) -> pd.DataFrame:
    """
    Convert IG /prices payload to a tidy DataFrame indexed by UTC time.
    Be tolerant: keep rows if CLOSE is present; other fields may be NaN.
    """
    raw = prices_payload.get("prices", []) or []
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    rows = []
    for p in raw:
        t = p.get("snapshotTimeUTC") or p.get("snapshotTime")  # fall back if UTC not provided
        op = p.get("openPrice", {}) or {}
        hp = p.get("highPrice", {}) or {}
        lp = p.get("lowPrice", {}) or {}
        cp = p.get("closePrice", {}) or {}

        row = {
            "time": t,
            "open":  _mid(op.get("bid"), op.get("ask"), op.get("lastTraded")),
            "high":  _mid(hp.get("bid"), hp.get("ask"), hp.get("lastTraded")),
            "low":   _mid(lp.get("bid"), lp.get("ask"), lp.get("lastTraded")),
            "close": _mid(cp.get("bid"), cp.get("ask"), cp.get("lastTraded")),
            "volume": p.get("lastTradedVolume"),
        }
        rows.append(row)

    df = pd.DataFrame.from_records(rows)
    if "time" in df.columns:
        df.index = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.drop(columns=["time"])
    # Only require close; keep partial OHLC if present
    df = df.dropna(subset=["close"], how="any")
    return df


# ---------- CLI ----------
def print_search(rows: List[Dict[str, Any]]):
    if not rows:
        print("No matches."); return
    for m in rows:
        inst = m.get("instrument") or {}
        name = m.get("instrumentName") or inst.get("name")
        epic = m.get("epic") or inst.get("epic")
        ityp = m.get("instrumentType") or inst.get("type")
        expiry = m.get("expiry") or "-"
        curr = (inst.get("currencies") or [{}])[0].get("code", "")
        print(f"{name} | {epic} | {ityp} | {expiry} | {curr}")


def main():
    ap = argparse.ArgumentParser(description="IG REST search & candles (single login, CFD promotion, date-range)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--search", help="Search term (e.g., 'US 500', 'Germany 40', 'EUR/USD')")
    g.add_argument("--epic", help="Exact EPIC to fetch prices")
    g.add_argument("--auto", help="Search term then auto-pick preferred EPIC and fetch")

    ap.add_argument("--instrument", default="CFD", help="Preference hint: CFD | SHARES | ALL (default CFD)")
    ap.add_argument("--resolution", default="DAY",
                    help="MINUTE, MINUTE_5, MINUTE_15, HOUR, DAY, WEEK, MONTH (default DAY)")
    ap.add_argument("--max", type=int, default=300, help="Number of candles to fetch (default 300)")
    ap.add_argument("--from-utc", dest="from_utc", help="ISO8601 UTC start (e.g., 2025-06-01T00:00:00Z)")
    ap.add_argument("--to-utc", dest="to_utc", help="ISO8601 UTC end (e.g., 2025-08-12T00:00:00Z)")
    ap.add_argument("--show", type=int, default=10, help="Print last N rows (default 10)")

    args = ap.parse_args()
    base, h = rest_login()

    if args.search:
        rows = rest_search(base, h, args.search)
        print_search(rows)
        return

    if args.auto:
        rows = rest_search(base, h, args.auto)
        if not rows:
            print("No matches."); return
        epic, note = prefer_cfd_or_promote(base, h, rows)
        print(f"[AUTO] EPIC selected: {epic} ({note})")
        payload = rest_prices(base, h, epic, args.resolution, args.max, args.from_utc, args.to_utc)
    else:
        payload = rest_prices(base, h, args.epic, args.resolution, args.max, args.from_utc, args.to_utc)

    df = to_frame(payload)
    if df.empty:
        print("No candles returned."); return

    print(f"Candles: {len(df)} | res={args.resolution}")
    print(df.tail(args.show).to_string(float_format=lambda x: f'{x:.5f}'))


if __name__ == "__main__":
    main()
