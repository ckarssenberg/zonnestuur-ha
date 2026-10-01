"""Meldingen versturen, ook zonder Home Assistant.

Kanalen (één of meer tegelijk):
  - Home Assistant-app   {"service": "notify.mobile_app_telefoon"}
  - ntfy                 {"ntfy": {"server": "https://ntfy.sh", "topic": "zonnestuur-abc123"}}
                         Gratis app (Android/iOS). Let op: bij de openbare server gaat de tekst van de melding via
                         internet; het onderwerp is als een wachtwoord, dus kies iets onraadbaars.
  - Telegram             {"telegram": {"token": "123:ABC", "chat_id": "456"}}
  - e-mail               {"email": {"host": "smtp.…", "port": 587, "user": "…", "password": "…", "to": "…", "from": "…"}}

Alleen standaardbibliotheek. Fouten worden teruggegeven, nooit opgegooid: meldingen mogen het sturen niet storen.
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from typing import Callable, Optional

log = logging.getLogger("zonnestuur.notify")

SECRETS = (("telegram", "token"), ("email", "password"))


def channels_of(conf: dict) -> list[str]:
    conf = conf or {}
    out = []
    if conf.get("service"):
        out.append("ha")
    if (conf.get("ntfy") or {}).get("topic"):
        out.append("ntfy")
    t = conf.get("telegram") or {}
    if t.get("token") and t.get("chat_id"):
        out.append("telegram")
    e = conf.get("email") or {}
    if e.get("host") and e.get("to"):
        out.append("email")
    return out


def _post_json(url: str, data: dict, headers: Optional[dict] = None, timeout: float = 10.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def send_ntfy(c: dict, title: str, msg: str, click: str = "") -> None:
    server = (c.get("server") or "https://ntfy.sh").rstrip("/")
    body = {"topic": c["topic"], "title": title, "message": msg, "tags": ["sunny"]}
    if click:
        body["click"] = click
    headers = {"Authorization": f"Bearer {c['token']}"} if c.get("token") else None
    _post_json(server, body, headers)


def send_telegram(c: dict, title: str, msg: str, click: str = "") -> None:
    text = f"{title}\n\n{msg}" + (f"\n\n{click}" if click else "")
    _post_json(f"https://api.telegram.org/bot{c['token']}/sendMessage", {"chat_id": c["chat_id"], "text": text})


def send_email(c: dict, title: str, msg: str, click: str = "") -> None:
    m = EmailMessage()
    m["Subject"] = f"Zonnestuur: {title}"
    m["From"] = c.get("from") or c.get("user") or c["to"]
    m["To"] = c["to"]
    m.set_content(msg + (f"\n\n{click}" if click else "") + "\n\n— Zonnestuur")
    port = int(c.get("port") or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(c["host"], port, timeout=15, context=ctx)
    else:
        s = smtplib.SMTP(c["host"], port, timeout=15)
        try:
            s.starttls(context=ctx)
        except smtplib.SMTPNotSupportedError:
            pass
    try:
        if c.get("user"):
            s.login(c["user"], c.get("password", ""))
        s.send_message(m)
    finally:
        try:
            s.quit()
        except Exception:
            pass


def send_all(conf: dict, ha_factory: Optional[Callable], title: str, msg: str, click: str = "",
             only: Optional[str] = None) -> dict[str, str]:
    """Verstuur naar alle ingestelde kanalen. Geeft {kanaal: 'ok' of foutmelding}."""
    res: dict[str, str] = {}
    for ch in channels_of(conf):
        if only and ch != only:
            continue
        try:
            if ch == "ha":
                if ha_factory is None:
                    raise RuntimeError("Home Assistant niet gekoppeld")
                svc = conf["service"]
                svc = svc.split(".", 1)[1] if svc.startswith("notify.") else svc
                data = {"title": title, "message": msg}
                ha_factory().call("notify", svc, data)
            elif ch == "ntfy":
                send_ntfy(conf["ntfy"], title, msg, click)
            elif ch == "telegram":
                send_telegram(conf["telegram"], title, msg, click)
            elif ch == "email":
                send_email(conf["email"], title, msg, click)
            res[ch] = "ok"
        except Exception as exc:               # netwerk, verkeerde sleutel, mailserver
            res[ch] = str(exc)[:200] or exc.__class__.__name__
            log.warning("melding via %s mislukt: %s", ch, res[ch])
    return res


def public(conf: dict) -> dict:
    """Zonder geheimen, voor de app."""
    out = json.loads(json.dumps(conf or {}))
    for k, secret in SECRETS:
        sec = out.get(k)
        if isinstance(sec, dict):
            out[f"has_{k}_{secret}"] = bool(sec.pop(secret, ""))
    return out


def merge(current: dict, incoming: dict) -> dict:
    """Geheimen die leeg terugkomen uit de app blijven bewaard."""
    out = dict(incoming or {})
    for k, secret in SECRETS:
        if isinstance(out.get(k), dict):
            sec = dict(out[k])
            if not sec.get(secret):
                sec[secret] = ((current or {}).get(k) or {}).get(secret, "")
            out[k] = sec
    return {k: v for k, v in out.items() if not k.startswith("has_")}
