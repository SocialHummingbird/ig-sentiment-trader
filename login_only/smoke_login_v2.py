# smoke_login_v2.py
from ig_api import read_credentials, IGRest
from datetime import datetime, timezone

def main():
    creds = read_credentials("ig_credentials.cfg")
    with IGRest(creds) as ig:
        print("Login OK", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print("Account:", ig.account_id, "| Base:", ig.base)
        # ping a known-good index CFD for a quick sanity check
        epic = "IX.D.FTSE.CFD.IP"
        md = ig.markets_by_epic(epic)
        instr = md.get("instrument", {})
        print("Instrument:", instr.get("name"), "| contractSize:", instr.get("contractSize"))

if __name__ == "__main__":
    main()
