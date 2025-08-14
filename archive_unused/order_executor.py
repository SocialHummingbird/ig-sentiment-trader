# order_executor.py
from __future__ import annotations
from typing import Optional, Dict, Any
from trading_ig import IGService
from requests import HTTPError

# ---------- Market + rules ----------

def get_market_details(ig: IGService, epic: str) -> Dict[str, Any]:
    """Fetch /markets for EPIC and normalise useful fields."""
    md = ig.fetch_market_by_epic(epic) or {}
    instrument = md.get("instrument", {}) or {}
    rules      = md.get("dealingRules", {}) or {}
    snap       = md.get("snapshot", {}) or {}

    def _num(x):
        try:
            return float(x)
        except Exception:
            return None

    # Common IG fields (may vary by instrument)
    return {
        "marketStatus": snap.get("marketStatus"),
        "currency": (instrument.get("currencies") or [{}])[0].get("code") or snap.get("currency"),
        "lotSize": _num(instrument.get("lotSize")),
        "minDealSize": _num(rules.get("minDealSize")),
        "minStepDistance": _num(rules.get("minStepDistance")),
        "minNormalStopOrLimitDistance": _num(rules.get("minNormalStopOrLimitDistance") or rules.get("minStopOrLimitDistance")),
        "minControlledRiskStopDistance": _num(rules.get("minControlledRiskStopDistance")),
        "epic": epic,
        "name": instrument.get("name") or epic,
        "offer": _num(snap.get("offer")),
        "bid": _num(snap.get("bid")),
    }

# ---------- Validation & rounding ----------

def _round_to_step(value: float, step: Optional[float]) -> float:
    if step in (None, 0):
        return float(value)
    # round to nearest multiple of step
    return round(round(value / step) * step, 10)

def validate_and_prepare(
    details: Dict[str, Any],
    direction: str,
    size: float,
    stop_distance_points: Optional[float],
    limit_distance_points: Optional[float],
) -> Dict[str, Any]:
    """Apply IG dealing rules (min size, min stop/limit, step). Raise ValueError if invalid."""
    if direction not in ("BUY", "SELL"):
        raise ValueError("direction must be 'BUY' or 'SELL'")

    if details["marketStatus"] != "TRADEABLE":
        raise ValueError(f"Market not tradeable ({details['marketStatus']}).")

    min_size  = details.get("minDealSize") or 0.0
    size_ok   = max(float(size), float(min_size))
    size_send = _round_to_step(size_ok, step=None)  # IG rarely exposes size increment; keep as-is

    min_stop = details.get("minNormalStopOrLimitDistance")
    min_step = details.get("minStepDistance")  # step for stop/limit distances

    def _sanitize_distance(d):
        if d is None:
            return None
        d = float(d)
        if min_stop is not None:
            d = max(d, min_stop)
        return _round_to_step(d, min_step)

    stop_send  = _sanitize_distance(stop_distance_points)
    limit_send = _sanitize_distance(limit_distance_points)

    return {
        "direction": direction,
        "size": size_send,
        "stop_distance": stop_send,
        "limit_distance": limit_send,
        "currency": details.get("currency"),
    }

# ---------- Placement ----------

def place_market(
    ig: IGService,
    epic: str,
    direction: str,
    size: float,
    *,
    stop_distance_points: Optional[float] = None,
    limit_distance_points: Optional[float] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Place a MARKET order (or dry-run). Distances are in *points*.
    Returns dict with summary and (if live) dealReference.
    """
    details = get_market_details(ig, epic)
    prepared = validate_and_prepare(details, direction, size, stop_distance_points, limit_distance_points)

    summary = {
        "epic": epic,
        "name": details["name"],
        "marketStatus": details["marketStatus"],
        "direction": prepared["direction"],
        "size": prepared["size"],
        "stop_distance": prepared["stop_distance"],
        "limit_distance": prepared["limit_distance"],
        "currency": prepared["currency"],
        "dry_run": dry_run,
    }

    if dry_run:
        return {"ok": True, "dry_run": True, "summary": summary}

    try:
        resp = ig.create_open_position(
            epic=epic,
            expiry="-",
            direction=prepared["direction"],
            size=prepared["size"],
            order_type="MARKET",
            level=None,
            guaranteed_stop=False,
            force_open=True,
            limit_distance=prepared["limit_distance"],
            stop_distance=prepared["stop_distance"],
            currency_code=prepared["currency"],
        )
        deal_ref = resp.get("dealReference") if isinstance(resp, dict) else resp
        return {"ok": True, "dry_run": False, "dealReference": deal_ref, "summary": summary}
    except HTTPError as e:
        r = e.response
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text}", "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e), "summary": summary}
