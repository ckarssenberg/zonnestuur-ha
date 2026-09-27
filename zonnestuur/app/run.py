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
# De add-on bepaalt waar de app luistert en waar de data staat
cfg.update({"web_host": "0.0.0.0", "web_port": int(os.environ.get("ZONNESTUUR_PORT", "8099")),
            "db_path": str(DATA / "zonnestuur.db")})
if os.environ.get("TZ"):
    cfg["timezone"] = os.environ["TZ"]
cfg_path.write_text(json.dumps(cfg, indent=2))

args = ["python3", "-m", "zonnestuur", "--config", str(cfg_path)]
if options.get("log_level") == "debug":
    args.append("-v")
os.chdir("/app")
os.execvp(args[0], args)
