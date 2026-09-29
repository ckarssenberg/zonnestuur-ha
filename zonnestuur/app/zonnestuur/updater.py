"""Automatische updates voor de Zonnestuur Box.

Draait één keer per dag (systemd-timer, als root):
  1. leest box.json in de openbare repository: welke versie de Boxen mogen hebben;
  2. is die nieuwer, dan haalt hij de code op en controleert dat het echt die versie is;
  3. zet hem klaar naast de huidige, schakelt over en herstart Zonnestuur;
  4. reageert de app daarna niet binnen anderhalve minuut, dan terug naar de vorige versie
     (en die nieuwe versie wordt niet nog eens geprobeerd).

Indeling op de Box:
  /opt/zonnestuur/releases/<versie>/zonnestuur   de code per versie
  /opt/zonnestuur/current -> releases/<versie>    wat er draait (PYTHONPATH van de dienst)

Uitzetten: "auto_update": false in /etc/zonnestuur/config.json (of in de instellingen).
Een versie tegenhouden voor alle Boxen: in box.json de oude versie laten staan.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("zonnestuur.updater")

REPO = os.environ.get("ZONNESTUUR_UPDATE_REPO", "ckarssenberg/zonnestuur-ha")
BRANCH = os.environ.get("ZONNESTUUR_UPDATE_BRANCH", "main")
CHANNEL_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/box.json"
TARBALL_URL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{BRANCH}"
PKG_PREFIX = "zonnestuur/app/zonnestuur/"              # plek van het pakket in de add-on-repository
APP_DIR = Path(os.environ.get("ZONNESTUUR_APP_DIR", "/opt/zonnestuur"))
CONFIG = Path(os.environ.get("ZONNESTUUR_CONFIG", "/etc/zonnestuur/config.json"))
STATE = Path(os.environ.get("ZONNESTUUR_UPDATE_STATE", "/var/lib/zonnestuur/update.json"))
KEEP = 3
HEALTH_S = 90


def parse_version(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


def _get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "zonnestuur-box"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(40_000_000)


def _version_in(pkg_dir: Path) -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (pkg_dir / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def extract_package(tar_bytes: bytes, dest: Path) -> None:
    """Alleen het pakket uit de repository-tarball halen, en niets buiten dest."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/", 1)                    # eerste deel = <repo>-<branch>/
            if len(parts) < 2 or not parts[1].startswith(PKG_PREFIX):
                continue
            rel = parts[1][len(PKG_PREFIX):]
            if not rel or "__pycache__" in rel:
                continue
            target = (dest / "zonnestuur" / rel).resolve()
            if not str(target).startswith(str(root) + os.sep):
                raise ValueError(f"onveilig pad in update: {m.name}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o644)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=1))
    except OSError:
        pass


def _auto_update_on() -> bool:
    try:
        return json.loads(CONFIG.read_text()).get("auto_update", True) is not False
    except (OSError, ValueError):
        return True


def _web_port() -> int:
    try:
        return int(json.loads(CONFIG.read_text()).get("web_port") or 80)
    except (OSError, ValueError, TypeError):
        return 80


def current_version() -> str:
    cur = APP_DIR / "current" / "zonnestuur"
    try:
        return _version_in(cur)
    except OSError:
        return ""


def _switch(target: Path) -> None:
    link = APP_DIR / "current"
    tmp = APP_DIR / "current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target.relative_to(APP_DIR))
    os.replace(tmp, link)                                   # in één keer om: nooit een half bijgewerkte map


def _restart() -> None:
    subprocess.run(["systemctl", "restart", "zonnestuur.service"], check=False, timeout=60)


def _healthy(expect: str, timeout: float = HEALTH_S) -> bool:
    url = f"http://127.0.0.1:{_web_port()}/api/status"
    end = time.time() + timeout
    while time.time() < end:
        try:
            data = json.loads(_get(url, timeout=5))
            if data.get("version") == expect:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _cleanup(keep_also: set[str]) -> None:
    rel = APP_DIR / "releases"
    versions = sorted((p for p in rel.iterdir() if p.is_dir()), key=lambda p: parse_version(p.name), reverse=True)
    for p in versions[KEEP:]:
        if p.name not in keep_also:
            shutil.rmtree(p, ignore_errors=True)


def check_and_update(fetch: Callable[[str], bytes] = _get, restart: Callable[[], None] = _restart,
                     healthy: Callable[[str], bool] = _healthy, force: bool = False) -> dict:
    st = _load_state()
    st["checked_at"] = time.time()
    cur = current_version()
    st["current"] = cur
    if not force and not _auto_update_on():
        st["result"] = "uit"
        _save_state(st)
        return st
    try:
        channel = json.loads(fetch(CHANNEL_URL))
        want = str(channel.get("version", ""))
    except Exception as exc:
        st["result"] = f"kon niet kijken: {exc}"
        _save_state(st)
        return st
    st["available"] = want
    bad = set(st.get("bad", []))
    if not want or parse_version(want) <= parse_version(cur) or want in bad:
        st["result"] = "bijgewerkt" if want not in bad else f"{want} overgeslagen (werkte eerder niet)"
        _save_state(st)
        return st

    releases = APP_DIR / "releases"
    staging = releases / f".{want}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        extract_package(fetch(TARBALL_URL), staging)
        got = _version_in(staging / "zonnestuur")
        if got != want:
            raise ValueError(f"download is versie {got or '?'}, verwacht {want}")
        subprocess.run(["python3", "-m", "compileall", "-q", str(staging)], check=False, timeout=300)
        final = releases / want
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        st["result"] = f"download mislukt: {exc}"
        _save_state(st)
        return st

    previous = os.readlink(APP_DIR / "current") if (APP_DIR / "current").is_symlink() else ""
    _switch(final)
    restart()
    if healthy(want):
        st.update(current=want, result=f"bijgewerkt naar {want}", updated_at=time.time())
        log.info("Bijgewerkt van %s naar %s", cur, want)
        _cleanup({want, Path(previous).name if previous else ""})
    else:
        log.warning("Versie %s start niet goed; terug naar %s", want, cur)
        if previous:
            _switch(APP_DIR / previous)
            restart()
        bad.add(want)
        st.update(bad=sorted(bad), result=f"{want} werkte niet, terug naar {cur}")
    _save_state(st)
    return st


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Zonnestuur Box bijwerken")
    ap.add_argument("--nu", action="store_true", help="ook bijwerken als automatisch bijwerken uit staat")
    args = ap.parse_args()
    st = check_and_update(force=args.nu)
    print(st.get("result", ""))


if __name__ == "__main__":
    main()
