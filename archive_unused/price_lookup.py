# price_lookup.py
import argparse
import json
import sys
from typing import List, Dict, Any

from requests import HTTPError
import requests
from trading_ig import IGService

from credentials import load_credentials


def login_library() -> IGService:
    c = load_credentials("ig_credentials.cfg")
    ig = IGService(c["IG_IDENTIFIER"], c["IG_PASSWORD"], c["IG_API_KEY"], acc_type=c["IG_ACC_TYPE"])
    ig.create_session()
    return ig


def library_search(ig: IGService, term: str) -> List[Dict[str, Any]]:
    res = ig.search_markets(term) or {}
    return res.get("markets") or []


def rest_search(term: str) -> List[Dict[str, Any]]:
    """Raw REST fallback: GET /markets?searchTerm=... (adds IG-ACCOUNT-ID)."""
    c = load_credentials("ig_credentials.cfg")
    base = "https://demo-api.ig.com/gateway/deal" if c["IG_ACC_TYPE"].upper() == "DEMO" else "https://api.ig.com/gateway/deal"

    # login v2
    h = {"X-IG-API-KEY": c["IG_API_KEY"], "Content-Type": "application/json", "Accept": "application/json", "Version": "2"}
    r = requests.post(f"{base}/session", headers=h, data=json.dumps({"identifier": c["IG_IDENTIFIER"], "password": c["IG_PASSWORD"]}), timeout=30)
    r.raise_for_status()
    sess = r.json()
    cst = r.headers["CST"]
    xst = r.headers["X-SECURITY-TOKEN"]
    acct = sess.get("currentAccountId", "")

    # search v1
    h2 = {
        "X-IG-API-KEY": c["IG_API_KEY"],
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
        "IG-ACCOUNT-ID": acct,
        "Accept": "application/json",
        "Version": "1",
    }
    p = requests.get(f"{base}/markets", headers=h2, params={"searchTerm": term}, timeout=30)
    p.raise_for_status()
    return p.json().get("markets", [])


def fetch_by_epic(ig: IGService, epic: str) -> Dict[str, Any]:
    try:
        return ig.fetch_market_by_epic(epic) or {}
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text}") from e


def print_rows(rows: List[Dict[str, Any]]):
    if not rows:
        print("No matches.")
        return
    for m in rows:
        name = m.get("instrumentName") or m.get("instrument", {}).get("name")
        epic = m.get("epic") or m.get("instrument", {}).get("epic")
        ityp = m.get("instrumentType") or m.get("instrument", {}).get("type")
        curr = (m.get("instrument", {}).get("currencies") or [{}])[0].get("code", "")
        expiry = m.get("expiry") or "-"
        print(f"{name} | {epic} | {ityp} | {expiry} | {curr}")


def main():
    ap = argparse.ArgumentParser(description="IG symbol/EPIC lookup")
    ap.add_argument("--search", help="search term (e.g. 'Apple', 'Vodafone')")
    ap.add_argument("--epic", help="exact EPIC to fetch")
    ap.add_argument("--instrument", default="ALL", help="filter instrument type: CFD, SHARES, FX, INDEX, ALL (default ALL)")
    args = ap.parse_args()

    if not args.search and not args.epic:
        ap.error("provide --search or --epic")

    # for EPIC fetch we only need library login
    if args.epic:
        ig = login_library()
        try:
            md = fetch_by_epic(ig, args.epic)
            print(json.dumps(md, indent=2))
        finally:
            try:
                ig.logout()
            except Exception:
                pass
        return

    term = args.search
    filt = args.instrument.upper()

    # try library search first…
    ig = login_library()
    try:
        rows = library_search(ig, term)
    finally:
        try:
            ig.logout()
        except Exception:
            pass

    # …fallback to REST if nothing
    if not rows:
        try:
            rows = rest_search(term)
        except Exception as e:
            print(f"[REST fallback failed] {e}")

    # optional instrument filter
    if filt != "ALL":
        rows = [m for m in rows if (m.get("instrumentType") or "").upper() == filt]

    print_rows(rows)


if __name__ == "__main__":
    main()
