# check_ig_secure.py
from trading_ig import IGService
from requests import HTTPError
from credentials import load_credentials

def normalize_accounts(info):
    """
    Make a list of account dicts regardless of what trading_ig returns
    (dict with 'accounts', list, or pandas.DataFrame-like).
    """
    # dict shape
    if isinstance(info, dict):
        return info.get("accounts") or info.get("accountList") or []

    # pandas DataFrame (or any object that supports to_dict(orient="records"))
    to_dict = getattr(info, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict(orient="records")
        except TypeError:
            # some older DataFrames only support (records)
            return to_dict("records")

    # already a list?
    if isinstance(info, (list, tuple)):
        return list(info)

    # last resort: wrap as single item
    return [info]

def main():
    c = load_credentials("ig_credentials.cfg")
    ig = IGService(
        c["IG_IDENTIFIER"],
        c["IG_PASSWORD"],
        c["IG_API_KEY"],
        acc_type=c["IG_ACC_TYPE"]  # DEMO or LIVE
    )

    # --- login ---
    try:
        ig.create_session()
        print("SESSION OK")
    except HTTPError as e:
        print("Login failed:", e.response.status_code, e.response.text)
        return
    except Exception as e:
        print("Login failed:", e)
        return

    # --- fetch accounts ---
    try:
        raw = ig.fetch_accounts()
        accounts = normalize_accounts(raw)
        print(f"ACCOUNTS ({len(accounts)}):")
        for a in accounts:
            if isinstance(a, dict):
                aid = a.get("accountId") or a.get("id")
                atp = a.get("accountType") or a.get("type")
                cur = a.get("currency") or a.get("currencyIsoCode") or a.get("currencyCode")
                pref = a.get("preferred")
                print(f"  - {aid} | {atp} | {cur} | preferred={pref}")
            else:
                print(f"  - {a}")
    except HTTPError as e:
        print("Accounts fetch failed:", e.response.status_code, e.response.text)
    except Exception as e:
        print("Accounts fetch failed:", e)

    # --- logout ---
    try:
        ig.logout()
        print("LOGOUT OK")
    except Exception as e:
        print("Logout failed:", e)

if __name__ == "__main__":
    main()
