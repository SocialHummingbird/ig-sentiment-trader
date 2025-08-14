# risk_guards.py
from __future__ import annotations
import csv, json, os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, List, Optional
from zoneinfo import ZoneInfo

# ---------------- time helpers ----------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _today_yyyy_mm_dd_utc() -> str:
    return _now_utc().strftime("%Y-%m-%d")

# ---------------- csv helpers ----------------
def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []

def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    s = ts.rstrip("Z")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None

# ---------------- rollups from log ----------------
def orders_today(log_path: str) -> List[Dict[str, str]]:
    rows = _read_csv_rows(log_path)
    today = _today_yyyy_mm_dd_utc()
    return [r for r in rows if (r.get("ts_utc") or "").startswith(today)]

def orders_today_for_instrument(log_path: str, name: str) -> List[Dict[str, str]]:
    return [r for r in orders_today(log_path) if (r.get("name") or "") == name]

def count_orders_today_total(log_path: str) -> int:
    return sum(1 for r in orders_today(log_path) if (r.get("event") or "").upper() in ("ORDER_SENT","ORDER_DRY"))

def count_orders_today_instrument(log_path: str, name: str) -> int:
    return sum(1 for r in orders_today_for_instrument(log_path, name) if (r.get("event") or "").upper() in ("ORDER_SENT","ORDER_DRY"))

def last_order_time_instrument(log_path: str, name: str) -> Optional[datetime]:
    recs = [r for r in orders_today_for_instrument(log_path, name) if (r.get("event") or "").upper() in ("ORDER_SENT","ORDER_DRY")]
    times = sorted((_parse_ts(r.get("ts_utc") or "") for r in recs if r.get("ts_utc")), reverse=True)
    return times[0] if times else None

def today_committed_risk_gbp(log_path: str) -> float:
    total = 0.0
    for r in orders_today(log_path):
        if (r.get("event") or "").upper() in ("ORDER_SENT","ORDER_DRY"):
            try:
                total += float(r.get("eff_risk_gbp") or 0.0)
            except Exception:
                pass
    return total

# ---------------- balance baseline for daily loss limit ----------------
def _logs_dir_from(log_path: str) -> str:
    d = os.path.dirname(log_path)
    return d if d else "."

def _baseline_path(log_path: str) -> str:
    return os.path.join(_logs_dir_from(log_path), f"pnl_baseline_{_today_yyyy_mm_dd_utc()}.json")

def _current_balance_gbp(ig) -> Optional[float]:
    """
    Use GET /session data via ig.me().
    In prior runs the body included top-level accountInfo: {balance,...}.
    """
    try:
        me = ig.me()
        bal = (me.get("accountInfo") or {}).get("balance")
        if bal is None:
            # fallback: if not present, we can't compute
            return None
        return float(bal)
    except Exception:
        return None

def ensure_daily_baseline_and_loss(ig, log_path: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Returns (baseline_balance, current_balance, realized_loss).
    Baseline is created on first run of the day and stored in logs/pnl_baseline_YYYY-MM-DD.json.
    realized_loss = max(0, baseline - current).
    """
    cur = _current_balance_gbp(ig)
    if cur is None:
        return None, None, None

    p = _baseline_path(log_path)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"baseline_balance_gbp": cur, "created_utc": _now_utc().isoformat()}, f)
        baseline = cur
    else:
        try:
            obj = json.load(open(p, "r", encoding="utf-8"))
            baseline = float(obj.get("baseline_balance_gbp"))
        except Exception:
            baseline = cur

    loss = max(0.0, baseline - cur)
    return baseline, cur, loss

# ---------------- trading window ----------------
def in_trading_window(cfg: dict, now_utc: datetime) -> Tuple[bool, Dict[str, Any]]:
    th = (cfg.get("risk_guards") or {}).get("trading_hours") or {}
    if not th or not th.get("enabled", False):
        return True, {"trading_hours_enabled": False}
    tzname = th.get("timezone", "Europe/London")
    start = th.get("start", "00:00")
    end   = th.get("end", "23:59")

    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = timezone.utc

    local = now_utc.astimezone(tz)
    local_date = local.date()
    s_hour, s_min = map(int, start.split(":"))
    e_hour, e_min = map(int, end.split(":"))
    start_dt = datetime(local_date.year, local_date.month, local_date.day, s_hour, s_min, tzinfo=tz)
    end_dt   = datetime(local_date.year, local_date.month, local_date.day, e_hour, e_min, tzinfo=tz)

    ok = start_dt <= local <= end_dt if end_dt >= start_dt else (local >= start_dt or local <= end_dt)
    return ok, {
        "trading_hours_enabled": True,
        "timezone": tzname, "start": start, "end": end,
        "now_local": local.isoformat(timespec="seconds")
    }

# ---------------- guard API ----------------
@dataclass
class GuardResult:
    ok: bool
    reason: str
    meta: Dict[str, Any]

def guard_preflight(ig, cfg: dict, log_path: str, run_trade_count: int) -> GuardResult:
    """
    Cheap checks before we fetch markets or size orders.
    - max_trades_per_run (uses run_trade_count from caller)
    - max_concurrent_positions
    """
    rg = (cfg.get("risk_guards") or {})
    meta: Dict[str, Any] = {}

    if not rg.get("enabled", False):
        return GuardResult(True, "RG_DISABLED", meta)

    # 1) Per-run cap
    mtr = int(rg.get("max_trades_per_run", 0) or 0)
    meta["max_trades_per_run"] = mtr
    meta["run_trade_count"] = run_trade_count
    if mtr and run_trade_count >= mtr:
        return GuardResult(False, "BLOCK_MAX_TRADES_PER_RUN", meta)

    # 2) Concurrent open positions
    mcp = int(rg.get("max_concurrent_positions", 0) or 0)
    meta["max_concurrent_positions"] = mcp
    if mcp:
        try:
            data = ig.positions()
            arr = data.get("positions") or data.get("response") or []
            open_n = len(arr)
        except Exception:
            open_n = 0
        meta["open_positions"] = open_n
        if open_n >= mcp:
            return GuardResult(False, "BLOCK_MAX_CONCURRENT_POSITIONS", meta)

    return GuardResult(True, "RG_OK", meta)

def guard_postsize(ig, cfg: dict, log_path: str, instrument_name: str, planned_eff_risk_gbp: float) -> GuardResult:
    """
    Checks that require the candidate trade details (size/risk) or instrument:
    - trading_hours window
    - per_instrument.max_trades_per_day
    - cooldown_min between orders on same instrument
    - daily_risk_budget_gbp (today's committed + planned <= budget)
    - daily_loss_limit_gbp (uses balance baseline file + current balance)
    """
    rg = (cfg.get("risk_guards") or {})
    meta: Dict[str, Any] = {"instrument": instrument_name, "planned_eff_risk_gbp": planned_eff_risk_gbp}

    if not rg.get("enabled", False):
        return GuardResult(True, "RG_DISABLED", meta)

    # A) Trading hours
    ok_window, wmeta = in_trading_window(cfg, _now_utc())
    meta.update(wmeta)
    if not ok_window:
        return GuardResult(False, "BLOCK_TRADING_WINDOW", meta)

    # B) Per-instrument caps
    pi = (rg.get("per_instrument") or {})
    max_tpd = int(pi.get("max_trades_per_day", 0) or 0)
    if max_tpd:
        count_i = count_orders_today_instrument(log_path, instrument_name)
        meta["max_trades_per_day"] = max_tpd
        meta["orders_today_instrument"] = count_i
        if count_i >= max_tpd:
            return GuardResult(False, "BLOCK_MAX_TRADES_PER_DAY_INSTRUMENT", meta)

    cooldown_min = int(pi.get("cooldown_min", 0) or 0)
    if cooldown_min > 0:
        last_ts = last_order_time_instrument(log_path, instrument_name)
        meta["cooldown_min"] = cooldown_min
        meta["last_order_ts"] = last_ts.isoformat(timespec="seconds") if last_ts else ""
        if last_ts and _now_utc() < last_ts + timedelta(minutes=cooldown_min):
            return GuardResult(False, "BLOCK_COOLDOWN_INSTRUMENT", meta)

    # C) Daily risk budget (committed + planned)
    budget = float(rg.get("daily_risk_budget_gbp", 0.0) or 0.0)
    meta["daily_risk_budget_gbp"] = budget
    if budget > 0.0:
        committed = today_committed_risk_gbp(log_path)
        meta["today_committed_risk_gbp"] = committed
        if committed + max(0.0, planned_eff_risk_gbp) > budget + 1e-9:
            return GuardResult(False, "BLOCK_DAILY_RISK_BUDGET", meta)

    # D) Daily loss limit — real: use baseline vs current balance
    dll = float(rg.get("daily_loss_limit_gbp", 0.0) or 0.0)
    meta["daily_loss_limit_gbp"] = dll
    if dll > 0.0:
        baseline, current, loss = ensure_daily_baseline_and_loss(ig, log_path)
        meta["pnl_baseline_gbp"] = baseline
        meta["current_balance_gbp"] = current
        meta["realized_loss_gbp"] = loss
        if loss is None or baseline is None:
            # Can't compute -> be safe and allow (or block). We choose allow + expose meta.
            pass
        else:
            if loss >= dll - 1e-9:
                return GuardResult(False, "BLOCK_DAILY_LOSS_LIMIT", meta)

    return GuardResult(True, "RG2_OK", meta)
