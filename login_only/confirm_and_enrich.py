# confirm_and_enrich.py
# Enrich ORDER_SENT rows in your CSV log with dealId/levels.
#  - Tries /confirms/{dealReference} with retries
#  - Falls back to open positions (match by epic+direction [+closest size])

from __future__ import annotations
import csv, json, argparse, time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from ig_api import read_credentials, IGRest

LOG_FIELDS = [
    "ts_utc","run_id","env","live","name","epic","resolution","candle_count","warmup_bars",
    "signal","close","sma20","rsi14","risk_gbp","stop_points","limit_points","value_per_point",
    "size_raw","size_final","eff_risk_gbp","currency","event","status","dealReference","error","payload_json"
]

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")+"Z"

def read_log_rows(path: str) -> List[Dict[str,str]]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"[ERR] Log not found: {path}")
        return []

def write_log_row(path: str, row: Dict[str,Any]) -> None:
    out = {k: row.get(k, "") for k in LOG_FIELDS}
    pj = row.get("payload_json")
    if isinstance(pj, (dict, list)):
        out["payload_json"] = json.dumps(pj, separators=(",",":"))
    with open(path, "a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow(out)

def latest_run_id(rows: List[Dict[str,str]]) -> Optional[str]:
    ids = [r["run_id"] for r in rows if r.get("event")=="ORDER_SENT" and r.get("run_id")]
    return sorted(ids)[-1] if ids else None

def fnum(x) -> Optional[float]:
    try: return float(x)
    except: return None

def try_confirm(ig: IGRest, deal_ref: str, attempts=6, sleep_s=0.8) -> Tuple[bool, Dict[str,Any]]:
    last = None
    for _ in range(attempts):
        try:
            data = ig.confirms(deal_ref)
            if isinstance(data, dict) and (data.get("dealId") or data.get("dealStatus")):
                return True, data
            last = {"error":"unexpected_response","raw":data}
        except Exception as e:
            last = {"error": f"{type(e).__name__}: {e}"}
        time.sleep(sleep_s)
    return False, last or {"error":"confirm_failed"}

def load_positions(ig: IGRest) -> List[Dict[str,Any]]:
    js = ig.positions()
    out = []
    for item in js.get("positions", []):
        pos = item.get("position", {})
        mkt = item.get("market", {})
        out.append({
            "dealId": pos.get("dealId"),
            "epic": pos.get("epic") or mkt.get("epic"),
            "direction": (pos.get("direction") or "").upper(),
            "size": fnum(pos.get("size")),
            "level": fnum(pos.get("level")),
            "stopLevel": fnum(pos.get("stopLevel")),
            "limitLevel": fnum(pos.get("limitLevel")),
            "created": pos.get("createdDateUTC") or pos.get("createdDate"),
            "name": mkt.get("instrumentName") or mkt.get("name"),
        })
    return out

def pick_match(positions, epic, direction, size_hint) -> Optional[Dict[str,Any]]:
    cands = [p for p in positions if p.get("epic")==epic and p.get("direction")==direction]
    if not cands: return None
    if size_hint is not None:
        cands.sort(key=lambda p: abs((p.get("size") or 0) - size_hint))
        return cands[0]
    # else latest by created timestamp
    def ts(p):
        try: return datetime.fromisoformat((p.get("created") or "").replace("Z","+00:00"))
        except: return datetime.min.replace(tzinfo=timezone.utc)
    cands.sort(key=ts, reverse=True)
    return cands[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="bot_config.json")
    ap.add_argument("--log", help="override log path")
    ap.add_argument("--run-id", help="run_id to enrich; default = latest ORDER_SENT")
    args = ap.parse_args()

    cfg = {}
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        pass

    log_path = args.log or cfg.get("log_path") or "./logs/trade_log.csv"
    rows = read_log_rows(log_path)
    if not rows:
        print("[ERR] No rows in log."); return

    run_id = args.run_id or latest_run_id(rows)
    if not run_id:
        print("[ERR] No ORDER_SENT rows found; nothing to confirm."); return

    sent = [r for r in rows if r.get("event")=="ORDER_SENT" and r.get("run_id")==run_id]
    if not sent:
        print(f"[INFO] No ORDER_SENT rows for {run_id}"); return

    creds = read_credentials("ig_credentials.cfg")
    with IGRest(creds) as ig:
        print(f"Enriching run_id={run_id} | {now_utc_iso()} | base={ig.base}")

        positions_cache = None
        for r in sent:
            epic = r.get("epic"); direction = (r.get("signal") or "").upper()
            deal_ref = r.get("dealReference") or ""
            size_hint = fnum(r.get("size_final"))

            print(f"- Confirm {epic} {direction} ref={deal_ref} size≈{size_hint or '?'}")
            ok, data = try_confirm(ig, deal_ref)
            base = {
                "ts_utc": now_utc_iso(), "run_id": run_id, "env": creds.IG_ACC_TYPE, "live": True,
                "name": r.get("name"), "epic": epic, "resolution": r.get("resolution"),
                "warmup_bars": r.get("warmup_bars"), "risk_gbp": r.get("risk_gbp"),
                "stop_points": r.get("stop_points"), "limit_points": r.get("limit_points"),
                "signal": direction, "dealReference": deal_ref,
            }
            if ok:
                write_log_row(log_path, {
                    **base, "event":"CONFIRM_OK", "status": data.get("dealStatus") or "ok",
                    "payload_json": {
                        "dealId": data.get("dealId"),
                        "dealReference": data.get("dealReference") or deal_ref,
                        "reason": data.get("reason"),
                        "level": data.get("level"),
                        "size": data.get("size"),
                        "stopLevel": data.get("stopLevel"),
                        "limitLevel": data.get("limitLevel"),
                        "raw": data
                    }
                })
                print(f"  OK dealId={data.get('dealId')} status={data.get('dealStatus')}")
                continue

            # fallback via positions
            if positions_cache is None:
                positions_cache = load_positions(ig)
            match = pick_match(positions_cache, epic, direction, size_hint)
            if match:
                write_log_row(log_path, {
                    **base, "event":"CONFIRM_FALLBACK_OK", "status":"from_positions", "payload_json": match
                })
                print(f"  FALLBACK_OK dealId={match.get('dealId')} level={match.get('level')}")
            else:
                write_log_row(log_path, {
                    **base, "event":"CONFIRM_FAIL", "status":"not_found",
                    "error": data.get("error") or "confirm_failed", "payload_json": {"confirm_error": data}
                })
                print("  FAIL (not found in positions)")

if __name__ == "__main__":
    main()
