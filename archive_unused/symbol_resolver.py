# symbol_resolver.py
import json, os
from typing import Dict, List, Any
from trading_ig import IGService

CACHE_FILE   = "symbols_cache.json"
ALIASES_FILE = "aliases.json"

def _load_json(path: str) -> Any:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _candidates(sym: str, aliases: Dict[str, List[str]]) -> List[str]:
    s = sym.strip().upper()
    cands = [s, f"{s}.US", f"{s}.L"]
    for alt in aliases.get(s, []):
        cands.append(alt)
    # de-dup keep order
    seen, out = set(), []
    for x in cands:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def _to_items(res) -> List[dict]:
    """
    Normalize search_markets() result to a list[dict] of markets.
    - If dict: expects {'markets': [...]}
    - If pandas.DataFrame: converts to list of dicts
    - Else: empty list
    """
    if isinstance(res, dict):
        return res.get("markets", []) or []
    # DataFrame path without importing pandas explicitly
    if hasattr(res, "to_dict") and hasattr(res, "columns"):
        try:
            return res.to_dict(orient="records")
        except TypeError:
            # older pandas may use 'records' without keyword
            return res.to_dict("records")
        except Exception:
            return []
    return []

def _pick_best(items: List[dict], prefer_type: str):
    if not items:
        return None
    # Prefer requested type
    for it in items:
        if it.get("instrumentType") == prefer_type:
            return it
    # Prefer CASH epics (common for all-sessions shares)
    for it in items:
        e = (it.get("epic") or "")
        if ".CASH." in e:
            return it
    # Fallback: first
    return items[0]

def resolve_symbols(ig: IGService, symbols: List[str], prefer_type: str = "SHARES") -> Dict[str, str]:
    cache   = _load_json(CACHE_FILE) or {}
    aliases = _load_json(ALIASES_FILE) or {}
    out: Dict[str, str] = {}

    for sym in symbols:
        key = sym.strip().upper()
        if key in cache:
            out[key] = cache[key]
            continue

        epic = None
        for q in _candidates(key, aliases):
            try:
                res = ig.search_markets(q)  # may be dict or DataFrame
            except Exception as e:
                print(f"[WARN] search error for {q}: {e}")
                continue
            items = _to_items(res)
            pick  = _pick_best(items, prefer_type)
            if pick:
                epic = pick.get("epic")
                # Optional: print what we matched
                nm = pick.get("instrumentName")
                it = pick.get("instrumentType")
                print(f"[RESOLVED] {key} via '{q}' -> {epic} ({nm}, {it})")
                break

        if epic:
            cache[key] = epic
            out[key]  = epic
        else:
            print(f"[WARN] No IG instrument found for {key}")

    _save_json(CACHE_FILE, cache)
    return out
