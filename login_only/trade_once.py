# trade_once.py  (location: C:\dev\IG\login_only\trade_once.py)
# Flow: prices -> indicators -> signal -> (sentiment gate) -> risk guards -> sizing -> (dry/live) order
# Extras: config auto-resolve (root/login_only), explicit path echoes, optional enrich hook.

from __future__ import annotations
import os, json, csv, argparse, sys
from math import floor, ceil
from typing import Dict, Any, Tuple, List
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

# --- Project layout awareness (this file lives under IG/login_only) ---
BASE_DIR = Path(__file__).resolve().parent            # C:\dev\IG\login_only
ROOT_DIR = BASE_DIR.parent                            # C:\dev\IG

# --- Local modules (all in project root or same folder) ---
from ig_api import read_credentials, IGRest, Credentials            # ROOT
from sentiment_client import get_sentiment_for_price_action         # ROOT
from credentials import ensure_openai_env                           # ROOT
from risk_guards import guard_preflight, guard_postsize             # ROOT

# ---------- logging fields ----------
LOG_FIELDS = [
    "ts_utc","run_id","env","live","name","epic","resolution","candle_count","warmup_bars",
    "signal","close","sma20","rsi14",
    "sentiment_label","sentiment_score","sentiment_reason",
    "rg_reason","rg_meta_json","rg2_reason","rg2_meta_json",
    "risk_gbp","stop_points","limit_points","value_per_point","size_raw","size_final","eff_risk_gbp","currency",
    "event","status","dealReference","error","payload_json"
]

# ---------- small utils ----------
def ensure_log(path: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()

def write_log(path: str, row: Dict[str,Any]):
    ensure_log(path)
    row = {**row}
    for k in ("payload_json","rg_meta_json","rg2_meta_json"):
        if isinstance(row.get(k), (dict, list)):
            row[k] = json.dumps(row[k], separators=(",",":"))
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow({k: row.get(k, "") for k in LOG_FIELDS})

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

def run_id_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

# ---------- indicators ----------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()

def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = (d.clip(lower=0)).rolling(n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs = up / dn
    return 100 - (100 / (1 + rs))

# ---------- helpers ----------
def snap(value: float, step: float, mode: str = "down") -> float:
    if step <= 0: return value
    k = value / step
    if mode == "up": return ceil(k)*step
    if mode == "nearest": return round(k)*step
    return floor(k)*step

def value_per_point(market: Dict[str,Any]) -> float:
    cs = (market.get("instrument") or {}).get("contractSize")
    try: return float(cs)
    except Exception: return 0.0

def min_size_and_step(market: Dict[str,Any]) -> Tuple[float,float]:
    rules = (market.get("dealingRules") or (market.get("instrument") or {}).get("dealingRules") or {})
    mds = rules.get("minDealSize") or {}
    return float(mds.get("value") or 1.0), float(mds.get("step") or (mds.get("value") or 1.0))

def min_stop_points(market: Dict[str,Any]) -> float:
    rules = (market.get("dealingRules") or (market.get("instrument") or {}).get("dealingRules") or {})
    msl = rules.get("minStopOrLimitDistance") or rules.get("minStopDistance") or {}
    try: return float(msl.get("value") or 0.0)
    except Exception: return 0.0

def currency_code(market: Dict[str,Any]) -> str:
    cur = (market.get("instrument") or {}).get("currencies") \
          or (market.get("market") or {}).get("currencies") or []
    return (cur[0].get("code") if cur else "GBP")

def prices_df_from_json(js: Dict[str,Any]) -> pd.DataFrame:
    def mid(obj: Dict[str,Any]) -> float | None:
        if not obj: return None
        b = obj.get("bid"); a = obj.get("ask") or obj.get("offer")
        if b is not None and a is not None:
            try: return (float(b)+float(a))/2.0
            except Exception: return None
        v = obj.get("lastTraded")
        try: return float(v) if v is not None else None
        except Exception: return None

    rows = []
    for p in js.get("prices", []):
        t = p.get("snapshotTimeUTC") or p.get("snapshotTime")
        rows.append({
            "time": pd.to_datetime(t, utc=True, errors="coerce"),
            "open":  mid(p.get("openPrice")  or {}),
            "high":  mid(p.get("highPrice")  or {}),
            "low":   mid(p.get("lowPrice")   or {}),
            "close": mid(p.get("closePrice") or {}),
            "volume": float(p.get("lastTradedVolume") or 0)
        })
    # Correct chaining: drop -> index -> sort_index
    df = pd.DataFrame(rows).dropna(subset=["time"]).set_index("time").sort_index()
    return df

# ---------- signal & sizing ----------
def simple_signal(df: pd.DataFrame, rsi_buy_min=55, rsi_sell_max=45, warmup_bars=20) -> str:
    if len(df) < warmup_bars: return "HOLD"
    last = df.iloc[-1]
    if pd.isna(last.get("sma20")) or pd.isna(last.get("rsi14")): return "HOLD"
    if last["close"] > last["sma20"] and last["rsi14"] >= rsi_buy_min: return "BUY"
    if last["close"] < last["sma20"] and last["rsi14"] <= rsi_sell_max: return "SELL"
    return "HOLD"

def sized_order(market: Dict[str,Any], risk_gbp: float, stop_pts: float, round_mode="down") -> Tuple[float, float]:
    vpp = value_per_point(market)
    if vpp <= 0: raise RuntimeError("Cannot determine value-per-point from market.contractSize")
    raw = risk_gbp / (max(stop_pts, 1e-9) * vpp)
    min_sz, step = min_size_and_step(market)
    size = snap(raw, step, mode=round_mode)
    if size < min_sz: size = min_sz
    return size, raw

# ---------- order placement ----------
def place_order(ig: IGRest, epic: str, direction: str,
                size: float, stop_dist: float, limit_dist: float,
                currency: str, live: bool) -> Dict[str,Any]:
    payload = {
        "epic": epic,
        "expiry": "-",
        "direction": direction,
        "size": float(size),
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "guaranteedStop": False,
        "forceOpen": True,
        "currencyCode": currency
    }
    if stop_dist is not None:
        payload["stopDistance"] = float(stop_dist)
    if limit_dist is not None:
        payload["limitDistance"] = float(limit_dist)

    if not live:
        return {"dry_run": True, "payload": payload}

    try:
        resp = ig.place_position(payload)
        return {"ok": True, "dry_run": False, "payload": payload, "resp": resp}
    except requests.HTTPError as e:
        return {"ok": False, "status": e.response.status_code, "text": e.response.text, "payload": payload}

# ---------- path helpers ----------
def resolve_config_path(arg_value: str) -> str:
    r"""
    Resolve --config against, in order:
      1) absolute path if provided
      2) current working directory
      3) script directory (IG\login_only)
      4) project root (IG)
    """
    p = Path(arg_value)
    if p.is_absolute() and p.exists():
        return str(p)
    # cwd
    cand = (Path.cwd() / arg_value).resolve()
    if cand.exists():
        return str(cand)
    # login_only
    cand = (BASE_DIR / arg_value).resolve()
    if cand.exists():
        return str(cand)
    # root
    cand = (ROOT_DIR / arg_value).resolve()
    if cand.exists():
        return str(cand)
    # give up (will error later with clear message)
    return str(p)

def resolve_log_path(cfg_log_path: str) -> str:
    """
    If log path is absolute, return it. If relative, anchor to ROOT_DIR so logs live under IG\logs\...
    """
    p = Path(cfg_log_path)
    if p.is_absolute():
        return str(p)
    return str((ROOT_DIR / p).resolve())

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="One-shot config-driven trade runner (with CSV logging) via ig_api.")
    ap.add_argument("--config", default="bot_config.json", help="Path to bot_config.json (root or next to this script)")
    ap.add_argument("--live", action="store_true", help="SEND orders (demo/live) instead of dry-run")
    ap.add_argument("--no-enrich", action="store_true", help="Skip post-run confirm/enrich step")
    args = ap.parse_args()

    # --- CONFIG LOADING WITH AUTO-RESOLVE ---
    cfg_path = resolve_config_path(args.config)
    if not Path(cfg_path).exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Echo paths for certainty
    print(f"Script path: {__file__}")
    print(f"Config path: {cfg_path}")

    # Live safety prompt
    if args.live:
        print("=== LIVE MODE CONFIRMATION ===")
        print(json.dumps({
            "risk_per_trade_gbp": cfg.get("risk_per_trade_gbp"),
            "risk_reward": cfg.get("risk_reward"),
            "watchlist": cfg.get("watchlist"),
            "risk_guards": cfg.get("risk_guards")
        }, indent=2))
        ack = input("Type LIVE to confirm sending orders: ").strip()
        if ack.upper() != "LIVE":
            print("Aborted: live confirmation not given.")
            sys.exit(1)

    # Load OpenAI creds (kept in login_only alongside this script)
    try:
        ensure_openai_env(str(BASE_DIR / "openai_credentials.cfg"))
    except Exception:
        pass

    # Load IG creds (kept in login_only alongside this script)
    creds_path = str(BASE_DIR / "ig_credentials.cfg")
    creds: Credentials = read_credentials(creds_path)
    env = creds.IG_ACC_TYPE  # DEMO or LIVE

    # Host echo
    acc_type = getattr(creds, "IG_ACC_TYPE", "DEMO")
    host = "https://live-api.ig.com" if str(acc_type).upper() == "LIVE" else "https://demo-api.ig.com"
    print(f"Target host: {host} (ACC_TYPE={acc_type})")

    # ---- Login + run ----
    try:
        with IGRest(creds) as ig:
            print(f"Login OK. live={bool(args.live)} | {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
            run_loop(ig, cfg, env, args.live, cfg_path)
            # --- Auto confirm + enrich (optional) ---
            if not args.no_enrich:
                try:
                    import subprocess
                    enrich_path = BASE_DIR / "confirm_and_enrich.py"
                    if enrich_path.exists():
                        print("Post-run: confirm_and_enrich.py starting…")
                        subprocess.run([sys.executable, str(enrich_path)], check=False)
                    else:
                        print("Post-run: confirm_and_enrich.py not found; skipping.")
                except Exception as _e:
                    print(f"Post-run enrich failed (non-fatal): {_e}")
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            pass
        print("LOGIN FAILED:", e)
        if body:
            print("IG response body:\n", body[:2000])
        raise

def run_loop(ig: IGRest, cfg: dict, env: str, live_flag: bool, cfg_path_used: str):
    reso     = cfg.get("resolution","MINUTE_5")
    max_c    = int(cfg.get("max_candles", 150))
    rr       = float(cfg.get("risk_reward", 2.0))
    risk_gbp = float(cfg.get("risk_per_trade_gbp", 25))
    warmup   = int(cfg.get("warmup_bars", 20))
    sig_rsi_buy  = int((cfg.get("min_signal_conf") or {}).get("rsi_buy_min",55))
    sig_rsi_sell = int((cfg.get("min_signal_conf") or {}).get("rsi_sell_max",45))
    log_path_cfg = cfg.get("log_path", ".\\logs\\trade_log.csv")
    log_path = resolve_log_path(log_path_cfg)
    rid      = run_id_utc()

    s_cfg    = (cfg.get("sentiment") or {})
    s_enabled = bool(s_cfg.get("enabled", False))
    s_model   = s_cfg.get("model", "gpt-4o-mini")
    s_thr     = float(s_cfg.get("min_score", 0.15))
    s_timeout = int(s_cfg.get("timeout_s", 20))
    s_logexp  = bool(s_cfg.get("explain_in_log", True))

    rg_cfg   = (cfg.get("risk_guards") or {})
    count_dry_as_trade = bool(rg_cfg.get("count_dry_as_trade", True))

    # Collect outcome rows for summary
    outcomes: List[Dict[str, Any]] = []
    run_trade_count = 0

    for item in cfg.get("watchlist", []):
        name = item.get("name") or item.get("epic")
        epic = item["epic"]
        stop_pts = float(item["stop_points"])
        limit_pts = rr * stop_pts

        print("\n" + "="*80)
        print(f"{name} | {epic}")

        base_row = {
            "ts_utc": now_utc_iso(),
            "run_id": rid, "env": env, "live": bool(live_flag),
            "name": name, "epic": epic, "resolution": reso,
            "warmup_bars": warmup, "risk_gbp": risk_gbp,
            "stop_points": stop_pts, "limit_points": limit_pts
        }

        outcome = {
            "instrument": name,
            "signal": "HOLD",
            "sentiment": "N/A",
            "rg_pre": "OK",
            "rg_post": "OK",
            "action": "NO_ORDER",
            "detail": ""
        }

        # -------- Risk Guards (pre-flight) --------
        rg1 = guard_preflight(ig, cfg, log_path, run_trade_count=run_trade_count)
        if not rg1.ok:
            msg = f"{rg1.reason}"
            print(f"RISK_GUARD BLOCK: {msg} {rg1.meta}")
            write_log(log_path, {**base_row, "event":"NO_ORDER", "status":"risk_guard_pre",
                                 "rg_reason": rg1.reason, "rg_meta_json": rg1.meta})
            outcome.update({"rg_pre": rg1.reason, "action":"NO_ORDER", "detail":"pre-flight guard blocked"})
            outcomes.append(outcome)
            continue

        # --- Market & prices ---
        try:
            mk = ig.markets_by_epic(epic)
        except requests.HTTPError as e:
            write_log(log_path, {**base_row, "event":"ERROR", "status": e.response.status_code, "error": e.response.text, "candle_count": 0})
            outcome.update({"action":"ERROR", "detail": f"market fetch {e.response.status_code}"})
            outcomes.append(outcome)
            continue

        vpp = value_per_point(mk); min_stop = min_stop_points(mk)
        min_sz, step = min_size_and_step(mk); curr = currency_code(mk)
        if stop_pts < min_stop:
            stop_pts = min_stop
            limit_pts = rr * stop_pts

        try:
            js = ig.prices(epic, reso, max_points=max_c)
            df = prices_df_from_json(js)
        except requests.HTTPError as e:
            write_log(log_path, {**base_row, "event":"ERROR", "status": e.response.status_code, "error": e.response.text, "candle_count": 0})
            outcome.update({"action":"ERROR", "detail": f"prices {e.response.status_code}"})
            outcomes.append(outcome)
            continue

        print(f"Candles returned: {len(df)}")
        if df.empty:
            write_log(log_path, {**base_row, "event":"ERROR", "status":"no_candles", "candle_count": 0})
            outcome.update({"action":"ERROR", "detail":"no candles"})
            outcomes.append(outcome)
            continue

        df["sma20"] = sma(df["close"], 20)
        df["rsi14"] = rsi(df["close"], 14)
        last = df.iloc[-1]
        sig = simple_signal(df, sig_rsi_buy, sig_rsi_sell, warmup_bars=warmup)
        outcome["signal"] = sig

        print(df.tail(5)[["open","high","low","close","sma20","rsi14"]])
        print(f"Signal: {sig} | close={last['close']:.5f}  SMA20={last['sma20']:.5f}  RSI14={last['rsi14']:.1f}")

        # log signal snapshot
        write_log(log_path, {
            **base_row, "event":"SIGNAL", "status":"ok", "candle_count": len(df),
            "signal": sig, "close": f"{last['close']:.5f}",
            "sma20": f"{last['sma20']:.5f}" if pd.notna(last['sma20']) else "",
            "rsi14": f"{last['rsi14']:.2f}" if pd.notna(last['rsi14']) else "",
            "value_per_point": vpp
        })

        # HOLD → stop here
        if sig == "HOLD":
            write_log(log_path, {**base_row, "event":"NO_ORDER", "status":"hold", "candle_count": len(df), "signal": sig})
            outcome.update({"action":"NO_ORDER", "detail":"signal HOLD"})
            outcomes.append(outcome)
            continue

        # ---- Sentiment Gate ----
        sent_meta = {"sentiment_label":"", "sentiment_score":"", "sentiment_reason":""}
        if s_enabled and sig in ("BUY", "SELL"):
            sent = get_sentiment_for_price_action(
                model=s_model,
                instrument_name=name,
                close=float(last["close"]),
                sma20=float(last["sma20"]),
                rsi14=float(last["rsi14"]),
                timeout_s=s_timeout
            )
            if not sent:
                print("-> NO_ORDER: sentiment unavailable")
                write_log(log_path, {**base_row, **sent_meta, "event":"NO_ORDER", "status":"sentiment_unavailable",
                                     "candle_count": len(df), "signal": sig})
                outcome.update({"sentiment":"UNAVAILABLE", "action":"NO_ORDER", "detail":"sentiment unavailable"})
                outcomes.append(outcome)
                continue

            sent_meta = {
                "sentiment_label": sent.get("label",""),
                "sentiment_score": f"{float(sent.get('score',0.0)):.2f}",
                "sentiment_reason": sent.get("explanation","") if s_logexp else ""
            }
            score = float(sent.get("score", 0.0))
            if score < s_thr:
                print(f"-> NO_ORDER: sentiment score {score:.2f} < thr {s_thr:.2f} ({sent.get('label','')})")
                write_log(log_path, {**base_row, **sent_meta, "event":"NO_ORDER", "status":"sentiment_block",
                                     "candle_count": len(df), "signal": sig})
                outcome.update({"sentiment":f"BLOCK {score:.2f}", "action":"NO_ORDER", "detail":"sentiment below threshold"})
                outcomes.append(outcome)
                continue
            outcome["sentiment"] = f"PASS {score:.2f}"
            print(f"Sentiment PASS: {sent_meta}")
        else:
            outcome["sentiment"] = "N/A"

        # ---- Sizing
        try:
            size, raw_size = sized_order(mk, risk_gbp, stop_pts, round_mode="down")
        except RuntimeError as e:
            write_log(log_path, {**base_row, **sent_meta, "event":"ERROR", "status":"size_error", "error": str(e), "candle_count": len(df), "signal": sig})
            outcome.update({"action":"ERROR", "detail":f"size_error {str(e)}"})
            outcomes.append(outcome)
            continue
        eff_risk = size * vpp * stop_pts

        # ---- Risk Guards (post-size)
        rg2 = guard_postsize(ig, cfg, log_path, name, eff_risk)
        if not rg2.ok:
            print(f"RISK_GUARD BLOCK (post-size): {rg2.reason} {rg2.meta}")
            write_log(log_path, {**base_row, **sent_meta, "event":"NO_ORDER", "status":"risk_guard_post",
                                 "rg2_reason": rg2.reason, "rg2_meta_json": rg2.meta,
                                 "candle_count": len(df), "signal": sig, "value_per_point": vpp,
                                 "size_raw": f"{raw_size:.6f}", "size_final": size, "eff_risk_gbp": f"{eff_risk:.2f}"})
            outcome.update({"rg_post": rg2.reason, "action":"NO_ORDER", "detail":"post-size guard blocked"})
            outcomes.append(outcome)
            continue

        # ---- Place or Dry-run
        out = place_order(ig, epic, sig, size, stop_pts, limit_pts, curr, live=live_flag)
        if out.get("dry_run"):
            write_log(log_path, {
                **base_row, **sent_meta, "event":"ORDER_DRY", "status":"ok", "candle_count": len(df), "signal": sig,
                "value_per_point": vpp, "size_raw": f"{raw_size:.6f}", "size_final": size,
                "eff_risk_gbp": f"{eff_risk:.2f}", "currency": curr, "payload_json": out["payload"]
            })
            outcome.update({"action":"ORDER_DRY", "detail":f"size={size}, eff_risk_gbp={eff_risk:.2f}"})
            if count_dry_as_trade:
                run_trade_count += 1
        else:
            if out.get("ok"):
                j = out["resp"]
                write_log(log_path, {
                    **base_row, **sent_meta, "event":"ORDER_SENT", "status":"ok", "candle_count": len(df), "signal": sig,
                    "value_per_point": vpp, "size_raw": f"{raw_size:.6f}", "size_final": size,
                    "eff_risk_gbp": f"{eff_risk:.2f}", "currency": curr,
                    "dealReference": j.get("dealReference",""), "payload_json": out["payload"]
                })
                outcome.update({"action":"ORDER_SENT", "detail":f"size={size}, eff_risk_gbp={eff_risk:.2f}"})
                run_trade_count += 1
            else:
                write_log(log_path, {
                    **base_row, **sent_meta, "event":"ORDER_FAIL", "status": out["status"], "candle_count": len(df), "signal": sig,
                    "value_per_point": vpp, "size_raw": f"{raw_size:.6f}", "size_final": size,
                    "eff_risk_gbp": f"{eff_risk:.2f}", "currency": curr,
                    "error": out.get("text",""), "payload_json": out["payload"]
                })
                outcome.update({"action":"ORDER_FAIL", "detail":f"http {out.get('status')}"})

        outcomes.append(outcome)

    # --------- End-of-run summary ---------
    print("\n" + "-"*80)
    print("End-of-run summary")
    print("-"*80)
    headers = ["Instrument", "Signal", "Sentiment", "RG(pre)", "RG(post)", "Action", "Detail"]
    widths = [22, 8, 12, 16, 18, 12, 40]

    def fmt(row: List[str], widths: List[int]) -> str:
        return " | ".join((str(s)[:w].ljust(w) for s, w in zip(row, widths)))

    print(fmt(headers, widths))
    print("-"*80)
    for o in outcomes:
        row = [o["instrument"], o["signal"], o["sentiment"], o["rg_pre"], o["rg_post"], o["action"], o["detail"]]
        print(fmt(row, widths))
    print("-"*80)
    print(f"Total instruments processed: {len(outcomes)}")
    print("-"*80)

    # Save summary to IG\logs (root-anchored)
    logs_dir = os.path.dirname(resolve_log_path(cfg.get("log_path", ".\\logs\\trade_log.csv"))) or str(ROOT_DIR / "logs")
    os.makedirs(logs_dir, exist_ok=True)
    sum_path = os.path.join(logs_dir, f"summary_{rid}.txt")
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write(fmt(headers, widths) + "\n")
        f.write("-"*80 + "\n")
        for o in outcomes:
            f.write(fmt([o["instrument"], o["signal"], o["sentiment"], o["rg_pre"], o["rg_post"], o["action"], o["detail"]], widths) + "\n")
        f.write("-"*80 + "\n")
        f.write(f"Total instruments processed: {len(outcomes)}\n")
    print(f"Summary saved: {sum_path}")

if __name__ == "__main__":
    main()
