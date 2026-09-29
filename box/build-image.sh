#!/usr/bin/env bash
# Maakt het SD-kaartbeeld van de Zonnestuur Box uit een officieel Raspberry Pi OS Lite-beeld (64-bit).
#
#   sudo build-image.sh raspios-lite.img.xz zonnestuur-box.img.xz PAKKET UNITS
#
# Optioneel: BOX_SSH_KEY="ssh-ed25519 AAAA…" zet SSH aan voor gebruiker 'beheer' (alleen met die sleutel;
# handig bij de proefhuizen). Zonder sleutel staat SSH uit en kan er niemand op de Box inloggen.
#
# Kaart erin, stekker erin: de Box heet 'zonnestuur', zoekt een netwerkkabel of zet na anderhalve minuut de
# instel-wifi "Zonnestuur-instellen" aan, en de app staat op http://zonnestuur.local.
set -euo pipefail

IN="$1"; OUT="$2"; PKG="$3"; UNITS="$4"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
IMG="$WORK/box.img"
cleanup() {
  set +e
  mountpoint -q "$WORK/boot" && umount "$WORK/boot"
  mountpoint -q "$WORK/root" && umount "$WORK/root"
  [ -n "${LOOP:-}" ] && losetup -d "$LOOP"
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "Uitpakken…"
xz -dc "$IN" > "$IMG"
LOOP="$(losetup -Pf --show "$IMG")"
sleep 1
mkdir -p "$WORK/boot" "$WORK/root"
mount "${LOOP}p2" "$WORK/root"
mount "${LOOP}p1" "$WORK/boot"

echo "Zonnestuur erin zetten…"
"$HERE/rootfs.sh" "$WORK/root" "$PKG" "$UNITS" --image --box

# land voor de wifi (anders blijft de wifi-chip uit) en Nederlandse tijd
if ! grep -q 'cfg80211.ieee80211_regdom' "$WORK/boot/cmdline.txt"; then
  sed -i '1 s/$/ cfg80211.ieee80211_regdom=NL/' "$WORK/boot/cmdline.txt"
fi
ln -sfn /usr/share/zoneinfo/Europe/Amsterdam "$WORK/root/etc/localtime"
echo "Europe/Amsterdam" > "$WORK/root/etc/timezone"

# beheerder: willekeurig wachtwoord dat niemand kent; inloggen alleen met een SSH-sleutel (als die is opgegeven)
HASH="$(openssl passwd -6 "$(head -c 32 /dev/urandom | base64)")"
if [ -f "$WORK/boot/user-data" ] || [ -d "$WORK/root/etc/cloud" ]; then
  # Raspberry Pi OS vanaf eind 2025: eerste start via cloud-init
  {
    echo "#cloud-config"
    echo "hostname: zonnestuur"
    echo "manage_etc_hosts: true"
    echo "timezone: Europe/Amsterdam"
    echo "users:"
    echo "  - name: beheer"
    echo "    groups: users,adm,dialout,netdev,sudo"
    echo "    shell: /bin/bash"
    echo "    lock_passwd: true"
    if [ -n "${BOX_SSH_KEY:-}" ]; then
      echo "    sudo: ALL=(ALL) NOPASSWD:ALL"
      echo "    ssh_authorized_keys:"
      echo "      - ${BOX_SSH_KEY}"
    fi
    if [ -n "${BOX_SSH_KEY:-}" ]; then
      echo "enable_ssh: true"
      echo "ssh_pwauth: false"
    else
      echo "enable_ssh: false"
    fi
  } > "$WORK/boot/user-data"
  [ -f "$WORK/boot/meta-data" ] || echo "instance-id: zonnestuur-box" > "$WORK/boot/meta-data"
  # geen wifi vooraf: dat doet de klant via de instel-wifi
  printf 'network:\n  version: 2\n' > "$WORK/boot/network-config"
else
  # oudere Raspberry Pi OS (Bookworm): userconf.txt
  echo "beheer:$HASH" > "$WORK/boot/userconf.txt"
  if [ -n "${BOX_SSH_KEY:-}" ]; then
    touch "$WORK/boot/ssh"
    mkdir -p "$WORK/root/etc/ssh/sshd_config.d" "$WORK/root/etc/ssh/authorized_keys"
    echo "$BOX_SSH_KEY" > "$WORK/root/etc/ssh/authorized_keys/beheer"
    printf 'AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys/%%u\nPasswordAuthentication no\n' \
      > "$WORK/root/etc/ssh/sshd_config.d/zonnestuur.conf"
  fi
fi

sync
umount "$WORK/boot" "$WORK/root"
losetup -d "$LOOP"; LOOP=""

echo "Inpakken…"
xz -T0 -6 -c "$IMG" > "$OUT"
sha256sum "$OUT" | tee "$OUT.sha256"
echo "Klaar: $OUT"
