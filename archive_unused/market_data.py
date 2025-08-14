# market_data.py
from __future__ import annotations
import math
from typing import Optional
import pandas as pd

def _mid(a: Optional[float], b: Optional[float], c: Optional[float]) -> Optional[float]:
    """Prefer (bid+ask)/2, fall back to lastTraded."""
    if a is not None and b is not None:
        return (a + b) / 2.0
    return c

def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize a trading-ig candles DataFrame to columns:
    time (index, UTC), open, high, low, close, volume.
    Handles both 'openPrice.bid' style and nested dict expands.
    """
    cols = {c.lower(): c for c in df.columns}

    def pick(prefix: str, field: str) -> Optional[str]:
        a = f"{prefix}.{field}"
        return cols.get(a) or cols.get(a.lower()) or cols.get(a.capitalize())

    # time column (defensive picking)
    tcol = (
        cols.get("snapshottimeutc")
        or cols.get("snapshottime")
        or cols.get("snapshot time utc")
        or next((c for c in df.columns if c.lower().startswith("snapshot")), None)
    )
    if tcol is None:
        raise ValueError("Could not find snapshot time column in candles frame")

    out = pd.DataFrame(index=pd.to_datetime(df[tcol], utc=True))

    # OHLC from bid/ask/lastTraded mid
    for kind in ("openprice", "highprice", "lowprice", "closeprice"):
        bid = pick(kind, "bid")
        ask = pick(kind, "ask")
        ltr = pick(kind, "lasttraded") or pick(kind, "lastTraded")
        present = [c for c in (bid, ask, ltr) if c is not None and c in df.columns]

        def row_mid(row):
            a = row[ask] if ask in row and pd.notna(row.get(ask)) else None
            b = row[bid] if bid in row and pd.notna(row.get(bid)) else None
            c = row[ltr] if ltr in row and pd.notna(row.get(ltr)) else None
            return _mid(b, a, c)

        out[kind.replace("price", "")] = df[present].apply(row_mid, axis=1)

    # volume if available
    vcol = None
    for c in ("lasttradedvolume", "volume", "lastTradedVolume"):
        if c in df.columns or c.lower() in cols:
            vcol = cols.get(c) or c
            break
    if vcol and vcol in df.columns:
        out["volume"] = pd.to_numeric(df[vcol], errors="coerce")
    else:
        out["volume"] = math.nan

    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna(subset=["open", "high", "low", "close"])

def _norm_any(res) -> pd.DataFrame:
    """Accept whatever trading-ig returns and normalize to a candles DataFrame."""
    if isinstance(res, dict):
        prices = res.get("prices") or []
        if isinstance(prices, pd.DataFrame):
            return _norm_df(prices)
        return _norm_df(pd.DataFrame(prices))
    if hasattr(res, "prices"):
        prices = getattr(res, "prices")
        if not isinstance(prices, pd.DataFrame):
            prices = pd.DataFrame(prices)
        return _norm_df(prices)
    if isinstance(res, pd.DataFrame):
        return _norm_df(res)
    return _norm_df(pd.DataFrame(res))

def get_candles(ig, epic: str, resolution: str = "MINUTE", num_points: int = 200) -> pd.DataFrame:
    """
    Fetch recent candles via the library and return a DataFrame indexed by UTC time
    with columns: open, high, low, close, volume.
    """
    if hasattr(ig, "fetch_historical_prices_by_epic_and_num_points"):
        res = ig.fetch_historical_prices_by_epic_and_num_points(epic, resolution, num_points)
    elif hasattr(ig, "fetch_historical_prices_by_epic"):
        try:
            res = ig.fetch_historical_prices_by_epic(epic, resolution, numpoints=num_points)
        except TypeError:
            res = ig.fetch_historical_prices_by_epic(epic, resolution, num_points)
    else:
        res = ig.fetch_prices(epic, resolution, numpoints=num_points)
    return _norm_any(res)

# ---- indicators ----
def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

# ---- smart fallback over multiple resolutions (library path) ----
class CandleFetchError(Exception):
    pass

def get_candles_smart(ig, epic: str, preferred: str = "MINUTE", num_points: int = 200):
    """
    Try multiple IG resolutions via the library until one works. Returns (df, resolution_used).
    """
    tried = []
    for res in [preferred, "MINUTE_5", "MINUTE_15", "HOUR", "DAY"]:
        if res in tried:
            continue
        tried.append(res)
        try:
            df = get_candles(ig, epic, resolution=res, num_points=num_points)
            if not df.empty:
                return df, res
        except Exception:
            continue
    raise CandleFetchError(f"Could not fetch candles for {epic} with any of {tried}")

# --- raw REST fallback for candles (bypasses trading-ig/pandas freq mapping)
import json, requests
from credentials import load_credentials

def get_candles_rest(epic: str, resolution: str = "DAY", num_points: int = 60):
    """
    Fetch candles via IG REST /prices and return a normalized DataFrame
    (index = UTC time; columns: open, high, low, close, volume).

    Resolutions allowed by REST include:
    MINUTE, MINUTE_2, MINUTE_3, MINUTE_5, MINUTE_10, MINUTE_15, MINUTE_30,
    HOUR, HOUR_2, HOUR_3, HOUR_4, DAY, WEEK, MONTH
    """
    c = load_credentials("ig_credentials.cfg")
    base = (
        "https://demo-api.ig.com/gateway/deal"
        if c["IG_ACC_TYPE"].upper() == "DEMO"
        else "https://api.ig.com/gateway/deal"
    )

    # ---- Login (v2) ----
    h = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2",
    }
    r = requests.post(
        f"{base}/session",
        headers=h,
        data=json.dumps({"identifier": c["IG_IDENTIFIER"], "password": c["IG_PASSWORD"]}),
        timeout=30,
    )
    r.raise_for_status()
    sess = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    cst = r.headers["CST"]
    xst = r.headers["X-SECURITY-TOKEN"]
    acct_id = (sess.get("currentAccountId") or sess.get("accountId") or "").strip()

    # ---- Prices (v3) — use query params (?resolution=&max=) and IG-ACCOUNT-ID ----
    h2 = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
        "IG-ACCOUNT-ID": acct_id,   # <<< important for some endpoints
        "Accept": "application/json",
        "Version": "3",
    }
    params = {"resolution": resolution, "max": num_points}
    p = requests.get(f"{base}/prices/{epic}", headers=h2, params=params, timeout=30)
    p.raise_for_status()
    data = p.json()

    # Normalize using the same pipeline as the library fetcher
    return _norm_any({"prices": data.get("prices", [])})
