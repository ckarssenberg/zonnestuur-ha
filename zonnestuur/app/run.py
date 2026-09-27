"""Start Zonnestuur binnen Home Assistant: instellingen en data in /data (blijft bewaard bij updates)."""
import json
import os
import sys
from pathlib import Path

DATA = Path(os.environ.get("ZONNESTUUR_DATA", "/data"))
cfg_path = DATA / "config.json"
options = {}
try:
    options = json.loads((DATA / "options.json").read_text())
except (OSError, ValueError):
    pass

cfg = {}
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text())
    except ValueError:
        cfg_path.rename(cfg_path.with_suffix(".kapot.json"))    # kapotte instellingen bewaren, opnieuw beginnen


def ingress_port() -> int:
    """De poort die Home Assistant voor deze add-on heeft gekozen (ingress_port: 0 in config.yaml)."""
    if os.environ.get("ZONNESTUUR_PORT"):
        return int(os.environ["ZONNESTUUR_PORT"])
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        import urllib.request
        req = urllib.request.Request("http://supervisor/addons/self/info", headers={"Authorization": f"Bearer {token}"})
        for _ in range(5):
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    port = int(json.load(r)["data"].get("ingress_port") or 0)
                    if port:
                        return port
            except Exception as exc:          # Supervisor nog niet klaar: even opnieuw
                print(f"Ingress-poort opvragen mislukt: {exc}", flush=True)
            import time
            time.sleep(2)
    return 8847


# De add-on bepaalt waar de app luistert en waar de data staat
port = ingress_port()
print(f"Zonnestuur luistert op poort {port}", flush=True)
cfg.update({"web_host": "0.0.0.0", "web_port": port,
            "db_path": str(DATA / "zonnestuur.db")})
if os.environ.get("TZ"):
    cfg["timezone"] = os.environ["TZ"]
cfg_path.write_text(json.dumps(cfg, indent=2))

args = ["python3", "-m", "zonnestuur", "--config", str(cfg_path)]
if options.get("log_level") == "debug":
    args.append("-v")
os.chdir("/app")
os.execvp(args[0], args)
