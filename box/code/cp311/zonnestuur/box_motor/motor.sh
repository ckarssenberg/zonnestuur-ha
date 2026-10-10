#!/usr/bin/env bash
# Start of herstart de motor (Home Assistant + Matter, en de onderdelen uit /var/lib/zonnestuur/motor/profiles).
# Eerste keer: Docker installeren als dat nog niet is gebeurd, en de meegeleverde images inladen.
set -euo pipefail
exec 9>/run/zonnestuur-motor.lock
flock 9                            # nooit twee keer tegelijk (eerste start + wijziging)
APP=/opt/zonnestuur/motor          # compose.yaml en dit script (van root)
DIR=/var/lib/zonnestuur/motor      # wat Zonnestuur aanpast: profiles, motor.env, motor.json
mkdir -p "$DIR" && chown zonnestuur:zonnestuur "$DIR" && chmod 750 "$DIR"
cd "$APP"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker installeren…"
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose >/dev/null
fi
systemctl is-active --quiet docker || systemctl start docker
if [ -d "$APP/images" ]; then                      # images uit het SD-kaartbeeld: eenmalig inladen
  for t in "$APP"/images/*.tar; do [ -e "$t" ] && docker load -q -i "$t" && rm -f "$t"; done
  rmdir "$APP/images" 2>/dev/null || true
fi
mkdir -p /var/lib/zonnestuur/{ha,matter,otbr,evcc/data}
[ -f /var/lib/zonnestuur/ha/configuration.yaml ] || printf 'default_config:\n\nhttp:\n  use_x_forwarded_for: false\n' > /var/lib/zonnestuur/ha/configuration.yaml
mkdir -p /var/lib/zonnestuur/ha/custom_components
chgrp -R zonnestuur /var/lib/zonnestuur/ha/custom_components && chmod -R g+rwX /var/lib/zonnestuur/ha/custom_components
chmod g+s /var/lib/zonnestuur/ha/custom_components
chgrp zonnestuur /var/lib/zonnestuur/ha/configuration.yaml && chmod g+r /var/lib/zonnestuur/ha/configuration.yaml
[ -f /var/lib/zonnestuur/evcc/evcc.yaml ] || echo "interval: 10s" > /var/lib/zonnestuur/evcc/evcc.yaml
chown -R zonnestuur /var/lib/zonnestuur/evcc/evcc.yaml
ARGS=(-p zonnestuur-motor)
for p in $(cat "$DIR/profiles" 2>/dev/null); do
  case "$p" in thread|evcc) ARGS+=(--profile "$p") ;; esac          # alleen bekende onderdelen
done
if grep -qw thread "$DIR/profiles" 2>/dev/null; then              # Thread: IPv6 en doorsturen aan
  sysctl -q -p /opt/zonnestuur/motor/sysctl-otbr.conf || true
fi
ENV=(--env-file /dev/null)
if [ -f "$DIR/motor.env" ]; then                    # alleen bekende sleutels met een dongle uit /dev/serial/by-id
  grep -E '^OTBR_DEVICE=/dev/serial/by-id/[A-Za-z0-9._:-]+$|^OTBR_BACKBONE=[a-z0-9]+$' "$DIR/motor.env" > "$APP/motor.env" || true
  ENV=(--env-file "$APP/motor.env")
fi
if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi
"${DC[@]}" "${ARGS[@]}" -f "$APP/compose.yaml" "${ENV[@]}" up -d --remove-orphans
# evcc-instellingen veranderd (door Zonnestuur): evcc opnieuw starten
if grep -qw evcc "$DIR/profiles" 2>/dev/null && [ /var/lib/zonnestuur/evcc/evcc.yaml -nt "$APP/.evcc-stamp" ]; then
  docker restart zs-evcc >/dev/null 2>&1 || true
  touch "$APP/.evcc-stamp"
fi
