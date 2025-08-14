# analyze_and_trade.py
import json, sys
from credentials import load_credentials
from trading_ig import IGService
from requests import HTTPError
from symbol_resolver import resolve_symbols
from market_data import (
    get_candles_smart,   # library path with smart resolution fallback
    get_candles_rest,    # raw REST fallback (works even if pandas mapping breaks)
    sma, rsi, CandleFetchError
)
from order_executor import place_market


def login():
    c = load_credentials("ig_credentials.cfg")
    # NOTE: no 'is_encrypted' kwarg — your library version doesn't support it
    ig = IGService(
        c["IG_IDENTIFIER"],
        c["IG_PASSWORD"],
        c["IG_API_KEY"],
        acc_type=c["IG_ACC_TYPE"]
    )
    ig.create_session()
    return ig


def main():
    cfg = json.load(open("universe.json", "r", encoding="utf-8"))
    symbols    = cfg["symbols"]
    prefer_ty  = cfg.get("instrumentType", "SHARES")
    dry_run    = bool(cfg.get("dry_run", True))
    size       = cfg.get("default_size", 1)
    # Optional distances (points). Leave out / null to omit stop/limit.
    stop_pts   = cfg.get("stop_distance_points")      # e.g., 50
    limit_pts  = cfg.get("limit_distance_points")     # e.g., 100

    ig = login()
    try:
        mapping = resolve_symbols(ig, symbols, prefer_type=prefer_ty)
        if not mapping:
            print("No symbols resolved. Exiting.")
            sys.exit(1)

        for sym, epic in mapping.items():
            # --- FETCH CANDLES (library first, then REST fallback) ---
            used_res = None
            try:
                # Try library – cycles MINUTE → MINUTE_5 → MINUTE_15 → HOUR → DAY
                df, used_res = get_candles_smart(ig, epic, preferred="MINUTE", num_points=120)
                print(f"[{sym}] using {used_res}")
            except CandleFetchError:
                # Library failed – try REST MINUTE_5, then REST DAY
                try:
                    df = get_candles_rest(epic, resolution="MINUTE_5", num_points=120)
                    used_res = "REST/MINUTE_5"
                    print(f"[{sym}] fallback REST using MINUTE_5")
                except Exception:
                    df = get_candles_rest(epic, resolution="DAY", num_points=60)
                    used_res = "REST/DAY"
                    print(f"[{sym}] fallback REST using DAY")
            except HTTPError as e:
                print(f"[{sym}] HTTP {e.response.status_code}: {e.response.text}")
                continue

            if df.empty:
                print(f"{sym} [{epic}] no candles.")
                continue

            # --- INDICATORS ---
            df["sma20"] = sma(df["close"], 20)
            df["rsi14"] = rsi(df["close"], 14)

            last = df.iloc[-1]
            sma_ok = (last["sma20"] == last["sma20"])
            rsi_ok = (last["rsi14"] == last["rsi14"])

            # --- TOY SIGNAL (replace later) ---
            if sma_ok and rsi_ok and last["close"] > last["sma20"] and last["rsi14"] >= 55:
                signal = "BUY"
            elif sma_ok and rsi_ok and last["close"] < last["sma20"] and last["rsi14"] <= 45:
                signal = "SELL"
            else:
                signal = "HOLD"

            sma_print = f"{last['sma20']:.4f}" if sma_ok else "nan"
            rsi_print = f"{last['rsi14']:.1f}" if rsi_ok else "nan"
            print(f"{sym} [{epic}] res={used_res} close={last['close']:.4f} SMA20={sma_print} RSI14={rsi_print} -> {signal}")

            # --- (DRY) ORDER EXECUTION ---
            if signal in ("BUY", "SELL"):
                result = place_market(
                    ig, epic, signal, size,
                    stop_distance_points=stop_pts,
                    limit_distance_points=limit_pts,
                    dry_run=dry_run
                )
                if result["ok"]:
                    if result["dry_run"]:
                        print(f"  DRY-RUN: {result['summary']}")
                    else:
                        print(f"  SENT: dealRef={result['dealReference']} | {result['summary']}")
                else:
                    print(f"  FAIL: {result['error']} | {result.get('summary')}")

    finally:
        try:
            ig.logout()
            print("LOGOUT OK")
        except Exception:
            pass


if __name__ == "__main__":
    main()
