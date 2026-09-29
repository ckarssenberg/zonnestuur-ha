"""Licenties: 30 dagen alles proberen, daarna Zonnestuur Basis of (met sleutel) Zonnestuur Pro.

Basis (gratis)   dashboard, verbruik en teruglevering-inzicht, één apparaat op zon-overschot, klaar-tijd-garantie.
Pro (€ 49/jaar)  alle apparaten, slimme dagplanning op zon én uurprijzen, stroomprijs-sturing zonder panelen,
                 thuisbatterij, omvormer begrenzen bij negatieve prijzen, meldingen.

Een licentiesleutel is een ondertekend stukje tekst: ZS1.<gegevens>.<handtekening>. Zonnestuur controleert de
handtekening met de openbare sleutel hieronder, zonder internet. Alleen wie de geheime sleutel heeft (de maker),
kan geldige sleutels maken (scripts/license.py). Alleen standaardbibliotheek: RSA-verificatie met pow().
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

TRIAL_DAYS = 30
PRICE_TEXT = "€ 49 per jaar"
BUY_URL = "https://github.com/ckarssenberg/zonnestuur-ha/blob/main/PRO.md"

# Openbare sleutel (RSA-2048, e=65537). De geheime sleutel staat NIET in deze code.
PUBLIC_N = int(
    "2661554433823183172686454616477262171474264163697964575535743295173353470481770391196666333011726115"
    "3033171286224373848334325737744179364208519774676112706197701852594793904200843316747035208734002478"
    "6666171940658349172361312386283483074736233046733450617550676084685993954111252681476143870734208004"
    "0910657693352825549434888384896020407824605079749945698853366235668059404207953557227802395524322462"
    "3548804172916665380234954710065497413049309413030840202714073826073189511238660615144785764488437613"
    "1063794343614078661028275780011984209780191449290469188142997044897249539879790136992701859384358335"
    "83827178904459803"
)
PUBLIC_E = 65537

# DER-voorvoegsel van SHA-256 in een PKCS#1 v1.5-handtekening
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _pkcs1(digest: bytes, k: int) -> bytes:
    t = _SHA256_PREFIX + digest
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def verify(key: str, n: int = None, e: int = PUBLIC_E) -> Optional[dict]:
    """Geeft de licentiegegevens terug als de sleutel echt is, anders None."""
    n = n or PUBLIC_N
    try:
        tag, payload_b64, sig_b64 = key.strip().split(".")
        if tag != "ZS1" or n < 2 ** 1000:
            return None
        payload, sig = _b64d(payload_b64), _b64d(sig_b64)
        k = (n.bit_length() + 7) // 8
        m = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
        if m != _pkcs1(hashlib.sha256(payload).digest(), k):
            return None
        return json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def sign(data: dict, n: int, d: int) -> str:
    """Alleen voor de maker (scripts/license.py): een sleutel maken met de geheime exponent d."""
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    k = (n.bit_length() + 7) // 8
    m = int.from_bytes(_pkcs1(hashlib.sha256(payload).digest(), k), "big")
    return "ZS1." + _b64e(payload) + "." + _b64e(pow(m, d, n).to_bytes(k, "big"))


@dataclass
class LicenseState:
    plan: str                    # "pro" | "trial" | "basic"
    days_left: Optional[int]     # proef of licentie
    install_id: str
    email: str = ""
    name: str = ""
    expires: str = ""
    error: str = ""

    @property
    def pro(self) -> bool:
        return self.plan in ("pro", "trial")

    def to_dict(self) -> dict:
        return {"plan": self.plan, "pro": self.pro, "days_left": self.days_left, "install_id": self.install_id,
                "email": self.email, "name": self.name, "expires": self.expires, "error": self.error,
                "price": PRICE_TEXT, "buy_url": BUY_URL, "trial_days": TRIAL_DAYS}


def evaluate(stored: Optional[dict], today: date) -> tuple[LicenseState, dict]:
    """Bepaal de licentietoestand uit wat er bewaard is: {install_id, first_run, key}.

    Geeft de toestand en de (eventueel aangevulde) bewaarde gegevens terug."""
    st = dict(stored or {})
    st.setdefault("install_id", uuid.uuid4().hex[:12].upper())
    st.setdefault("first_run", today.isoformat())
    key = st.get("key") or ""
    if key:
        data = verify(key)
        if data is None:
            state = LicenseState("basic", None, st["install_id"], error="Deze licentiesleutel klopt niet")
        else:
            exp = data.get("exp", "")
            left = (date.fromisoformat(exp) - today).days if exp else None
            if left is not None and left < 0:
                state = LicenseState("basic", 0, st["install_id"], data.get("email", ""), data.get("name", ""), exp,
                                     error=f"Je licentie is verlopen op {exp}")
            else:
                return LicenseState("pro", left, st["install_id"], data.get("email", ""), data.get("name", ""), exp), st
        # ongeldige of verlopen sleutel: val terug op de proef als die nog loopt
    first = date.fromisoformat(st["first_run"])
    left = TRIAL_DAYS - (today - first).days
    if left > 0:
        s = LicenseState("trial", left, st["install_id"])
        if key:
            s.error = state.error
        return s, st
    if key:
        return state, st
    return LicenseState("basic", 0, st["install_id"]), st


def new_key_data(email: str, name: str = "", days: int = 366, today: Optional[date] = None) -> dict:
    today = today or date.today()
    return {"v": 1, "id": uuid.uuid4().hex[:10], "email": email, "name": name, "plan": "pro",
            "issued": today.isoformat(), "exp": (today + timedelta(days=days)).isoformat()}
