# close_all.py
# Closes all open positions by sending opposite-direction MARKET orders.
# Safety: confirmation prompt; supports --dry (preview) and --force (no prompt).

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from login_only.ig_api import read_credentials, IGRest  # type: ignore


def confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().upper()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"CLOSE ALL", "ALL", "YES"}


def main():
    ap = argparse.ArgumentParser(description="Close all open positions (with confirmation).")
    ap.add_argument("--dry", action="store_true", help="Dry run: do not send orders; just print payloads.")
    ap.add_argument("--force", action="store_true", help="Skip confirmation prompt and send close orders.")
    args = ap.parse_args()

    creds = read_credentials(str(Path(ROOT, "login_only", "ig_credentials.cfg")))
    with IGRest(creds) as ig:
        pos = ig.positions()
        positions = pos.get("positions") or []
        if not positions:
            print("No open positions.")
            return

        # Build close payloads
        payloads = []
        for p in positions:
            posd = p.get("position", {})
            mkt  = p.get("market", {})
            epic = mkt.get("epic") or posd.get("epic")
            name = mkt.get("instrumentName") or posd.get("instrumentName") or epic
            size = float(posd.get("size") or posd.get("dealSize") or 0)
            if size <= 0 or not epic:
                continue
            direction = (posd.get("direction") or "").upper()
            close_dir = "SELL" if direction == "BUY" else "BUY"

            payloads.append({
                "name": name,
                "epic": epic,
                "direction": close_dir,
                "size": size,
                "orderType": "MARKET",
                "timeInForce": "FILL_OR_KILL",
                "guaranteedStop": False,
                "forceOpen": False,  # close, not force open
                "currencyCode": (posd.get("currency") or "GBP")
            })

        if not payloads:
            print("No closeable positions found.")
            return

        print("The following close orders will be sent:")
        print(json.dumps(payloads, indent=2))

        if args.dry:
            print("\nDRY RUN: no orders sent.")
            return

        if not args.force and not confirm("Type CLOSE ALL / ALL / YES to confirm: "):
            print("Aborted.")
            return

        # Send
        results = []
        ok_count = 0
        for pl in payloads:
            try:
                url = f"{ig.base}/positions/otc"
                r = ig.sess.post(url, data=json.dumps(pl), timeout=20)
                if r.status_code >= 400:
                    results.append({"epic": pl["epic"], "status": r.status_code, "error": r.text})
                else:
                    ok_count += 1
                    results.append({"epic": pl["epic"], "ok": True, "resp": r.json()})
            except Exception as e:
                results.append({"epic": pl["epic"], "ok": False, "error": str(e)})

        print("\nResults:")
        print(json.dumps(results, indent=2))
        print(f"\nClosed {ok_count}/{len(payloads)} positions.")

if __name__ == "__main__":
    main()
