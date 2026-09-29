#!/usr/bin/env bash
# Zet Zonnestuur in een Linux-systeem: een draaiende Raspberry Pi (ROOT=/) of een uitgepakt SD-kaartbeeld.
#
#   rootfs.sh ROOT PAKKET UNITS [--image] [--box]
#     ROOT    /  of de gekoppelde root-partitie van een beeld
#     PAKKET  map 'zonnestuur' met de Python-code
#     UNITS   map met zonnestuur*.service / .timer
#     --image niets starten, alleen bestanden neerzetten (voor een SD-kaartbeeld)
#     --box   ook de wifi-instelmodus aanzetten (Zonnestuur Box)
set -euo pipefail

ROOT="${1%/}"; PKG="$2"; UNITS="$3"; shift 3
IMAGE=0; BOX=0
for a in "$@"; do
  case "$a" in --image) IMAGE=1 ;; --box) BOX=1 ;; *) echo "onbekende optie $a" >&2; exit 2 ;; esac
done
R="${ROOT}"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PKG/__init__.py")"
[ -n "$VER" ] || { echo "geen versie gevonden in $PKG/__init__.py" >&2; exit 1; }
ZUID=900

# 1. gebruiker 'zonnestuur' (systeemgebruiker, mag de P1-kabel lezen)
if [ "$IMAGE" = 1 ]; then
  if ! grep -q '^zonnestuur:' "$R/etc/passwd"; then
    while grep -q ":x:$ZUID:" "$R/etc/passwd" "$R/etc/group"; do ZUID=$((ZUID + 1)); done
    echo "zonnestuur:x:$ZUID:$ZUID:Zonnestuur:/var/lib/zonnestuur:/usr/sbin/nologin" >> "$R/etc/passwd"
    echo "zonnestuur:x:$ZUID:" >> "$R/etc/group"
    echo "zonnestuur:!*:19000::::::" >> "$R/etc/shadow"
    [ -f "$R/etc/gshadow" ] && echo "zonnestuur:!::" >> "$R/etc/gshadow"
  fi
  ZUID="$(awk -F: '$1=="zonnestuur"{print $3}' "$R/etc/passwd")"
  if ! grep -q '^dialout:.*zonnestuur' "$R/etc/group"; then
    sed -i -E 's/^(dialout:[^:]*:[^:]*:)(.*)$/\1\2,zonnestuur/; s/^(dialout:[^:]*:[^:]*:),zonnestuur$/\1zonnestuur/' "$R/etc/group"
  fi
  OWNER="$ZUID:$ZUID"
else
  id -u zonnestuur >/dev/null 2>&1 || useradd --system --home /var/lib/zonnestuur --shell /usr/sbin/nologin zonnestuur
  usermod -aG dialout zonnestuur
  OWNER="zonnestuur:zonnestuur"
fi

# 2. code: per versie een map, 'current' wijst naar wat draait (zo kan de updater veilig terug)
APP="$R/opt/zonnestuur"
mkdir -p "$APP/releases"
rm -rf "$APP/releases/$VER" "$APP/zonnestuur"          # ook de oude indeling van vóór 0.20 opruimen
mkdir -p "$APP/releases/$VER"
cp -r "$PKG" "$APP/releases/$VER/zonnestuur"
find "$APP/releases/$VER" -name '__pycache__' -prune -exec rm -rf {} +
ln -sfn "releases/$VER" "$APP/current"

# 3. instellingen en gegevens
mkdir -p "$R/etc/zonnestuur" "$R/var/lib/zonnestuur/net"
if [ ! -f "$R/etc/zonnestuur/config.json" ]; then
  cat > "$R/etc/zonnestuur/config.json" <<JSON
{
  "web_host": "0.0.0.0",
  "web_port": 80,
  "db_path": "/var/lib/zonnestuur/zonnestuur.db",
  "interval_s": 10,
  "auto_update": true
}
JSON
fi
chown -R "$OWNER" "$R/var/lib/zonnestuur" "$R/etc/zonnestuur"
chmod 750 "$R/var/lib/zonnestuur/net"

# 4. diensten
for u in zonnestuur.service zonnestuur-update.service zonnestuur-update.timer zonnestuur-net.service; do
  install -m 644 "$UNITS/$u" "$R/etc/systemd/system/$u"
done
ENABLE="zonnestuur.service zonnestuur-update.timer"
[ "$BOX" = 1 ] && ENABLE="$ENABLE zonnestuur-net.service"

# 5. Box: instel-wifi als 'captive portal' (telefoon opent de pagina vanzelf), minder schrijven naar de SD-kaart
if [ "$BOX" = 1 ]; then
  mkdir -p "$R/etc/NetworkManager/dnsmasq-shared.d"
  echo "address=/#/10.42.0.1" > "$R/etc/NetworkManager/dnsmasq-shared.d/zonnestuur-instellen.conf"
  mkdir -p "$R/etc/systemd/journald.conf.d"
  printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=30M\n' > "$R/etc/systemd/journald.conf.d/zonnestuur.conf"
  echo zonnestuur > "$R/etc/hostname"
  if grep -q '^127.0.1.1' "$R/etc/hosts"; then
    sed -i 's/^127.0.1.1.*/127.0.1.1\tzonnestuur/' "$R/etc/hosts"
  else
    printf '127.0.1.1\tzonnestuur\n' >> "$R/etc/hosts"
  fi
  for f in "$R"/var/lib/systemd/rfkill/*:wlan; do [ -e "$f" ] && echo 0 > "$f"; done
fi

if [ "$IMAGE" = 1 ]; then
  for u in $ENABLE; do
    case "$u" in *.timer) T=timers.target.wants ;; *) T=multi-user.target.wants ;; esac
    mkdir -p "$R/etc/systemd/system/$T"
    ln -sfn "/etc/systemd/system/$u" "$R/etc/systemd/system/$T/$u"
  done
else
  systemctl daemon-reload
  # shellcheck disable=SC2086
  systemctl enable $ENABLE >/dev/null
  systemctl restart zonnestuur.service
  [ "$BOX" = 1 ] && systemctl restart zonnestuur-net.service
  [ "$BOX" = 1 ] && systemctl restart systemd-journald || true
fi
echo "Zonnestuur $VER geplaatst in ${ROOT:-/}"
