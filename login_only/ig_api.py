# ig_api.py
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests


@dataclass
class Credentials:
    IG_ACC_TYPE: str
    IG_API_KEY: str
    IG_IDENTIFIER: str
    IG_PASSWORD: str


def read_credentials(path: str) -> Credentials:
    p = Path(path)
    vals: Dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return Credentials(
        IG_ACC_TYPE=vals.get("IG_ACC_TYPE", "DEMO").upper(),
        IG_API_KEY=vals.get("IG_API_KEY", ""),
        IG_IDENTIFIER=vals.get("IG_IDENTIFIER", ""),
        IG_PASSWORD=vals.get("IG_PASSWORD", ""),
    )


class IGRest:
    """
    Minimal IG REST client for this project.
    - Performs login identical to our working probe.
    - After login, stores CST and X-SECURITY-TOKEN in session headers.
    - Exposes the few endpoints we use in trade_once.py and risk_guards.py.
    """

    def __init__(self, creds: Credentials, timeout: int = 20):
        self.creds = creds
        self.timeout = timeout
        self.base = (
            "https://live-api.ig.com/gateway/deal"
            if creds.IG_ACC_TYPE.upper() == "LIVE"
            else "https://demo-api.ig.com/gateway/deal"
        )
        self.sess = requests.Session()
        # Do NOT set CST/X-SECURITY-TOKEN until after login
        # Only set static headers that are safe pre-login:
        self.sess.headers.update(
            {
                "Accept": "application/json; charset=UTF-8",
                "Content-Type": "application/json",
                "Version": "2",
                "X-IG-API-KEY": self.creds.IG_API_KEY,
            }
        )
        self._logged_in = False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.logout()
        try:
            self.sess.close()
        except Exception:
            pass

    # ---------- session ----------
    def login(self) -> None:
        """
        POST /session using the same headers/body as the probe that worked.
        Then capture CST and X-SECURITY-TOKEN for subsequent calls.
        """
        url = f"{self.base}/session"
        body = {
            "identifier": self.creds.IG_IDENTIFIER,
            "password": self.creds.IG_PASSWORD,
        }
        # Build request with the *current* headers (no CST yet)
        r = self.sess.post(url, data=json.dumps(body), timeout=self.timeout)
        # Force raise to catch 4xx/5xx
        try:
            r.raise_for_status()
        except requests.HTTPError:
            # Make errors easier to read upstream
            raise
        # On success, capture tokens
        cst = r.headers.get("CST")
        sec = r.headers.get("X-SECURITY-TOKEN")
        if not cst or not sec:
            # Very unusual, but fail loudly with body for diagnostics
            raise requests.HTTPError(
                "Login succeeded but tokens missing",
                response=r,
            )
        # Update session headers for subsequent calls
        self.sess.headers.update(
            {
                "CST": cst,
                "X-SECURITY-TOKEN": sec,
                # Keep these so future calls match IG expectations
                "Accept": "application/json; charset=UTF-8",
                "Content-Type": "application/json",
                "Version": "2",
                "X-IG-API-KEY": self.creds.IG_API_KEY,
            }
        )
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in:
            return
        try:
            url = f"{self.base}/session"
            self.sess.delete(url, timeout=self.timeout)
        except Exception:
            pass
        finally:
            self._logged_in = False

    # ---------- endpoints we use ----------
    def me(self) -> Dict[str, Any]:
        url = f"{self.base}/session"
        r = self.sess.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def positions(self) -> Dict[str, Any]:
        url = f"{self.base}/positions"
        r = self.sess.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def markets_by_epic(self, epic: str) -> Dict[str, Any]:
        url = f"{self.base}/markets/{epic}"
        r = self.sess.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def prices(self, epic: str, resolution: str, max_points: int = 150) -> Dict[str, Any]:
        """
        GET /prices/{epic}/{resolution}/{max}?pageSize=...
        For IG, the common form is /prices/{epic}/{resolution}/{max}
        """
        url = f"{self.base}/prices/{epic}/{resolution}/{max_points}"
        r = self.sess.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def place_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /positions/otc for market order.
        The payload shape you already build in trade_once.py is fine.
        """
        url = f"{self.base}/positions/otc"
        r = self.sess.post(url, data=json.dumps(payload), timeout=self.timeout)
        r.raise_for_status()
        return r.json()
