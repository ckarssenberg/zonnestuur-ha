#!/usr/bin/env bash
# Maakt het SD-kaartbeeld van de Zonnestuur Box uit een officieel Raspberry Pi OS Lite-beeld (64-bit).
#
#   sudo build-image.sh raspios-lite.img.xz zonnestuur-box.img.xz PAKKET UNITS
#
# Optioneel: BOX_SSH_KEY="ssh-ed25519 AAAA…" zet SSH aan voor gebruiker 'beheer' (alleen met die sleutel;
# handig bij de proefhuizen). Zonder sleutel staat SSH uit en kan er niemand op de Box inloggen.
#
# De Box draait een ingebouwde Home Assistant ("motor") voor alle koppelingen: Raspberry Pi 4 of 5 met 4 GB of meer,
# microSD-kaart van minimaal 16 GB. Zonder motor bouwen: ZS_MOTOR=0.
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
if [ "${ZS_MOTOR:-1}" = 1 ]; then
  # ruimte voor de motor (Docker + Home Assistant + Matter-server, ca. 3 GB); bij de eerste start groeit de
  # partitie verder tot de hele kaart (minimaal 16 GB)
  echo "Beeld vergroten voor de motor…"
  truncate -s +4G "$IMG"
  echo ", +" | sfdisk -N 2 --no-reread "$IMG" >/dev/null
fi
LOOP="$(losetup -Pf --show "$IMG")"
sleep 1
if [ "${ZS_MOTOR:-1}" = 1 ]; then
  e2fsck -fy "${LOOP}p2" >/dev/null || true
  resize2fs "${LOOP}p2" >/dev/null
fi
mkdir -p "$WORK/boot" "$WORK/root"
mount "${LOOP}p2" "$WORK/root"
mount "${LOOP}p1" "$WORK/boot"

echo "Zonnestuur erin zetten…"
"$HERE/rootfs.sh" "$WORK/root" "$PKG" "$UNITS" --image --box

if [ "${ZS_MOTOR:-1}" = 1 ]; then
  # Docker in het beeld (via qemu, als de bouwmachine dat kan) en de images alvast erin, zodat de Box bij de
  # eerste start niet eerst gigabytes hoeft te downloaden. Lukt het niet, dan doet motor.sh het bij de eerste start.
  if [ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    echo "Docker in het beeld zetten…"
    cp /etc/resolv.conf "$WORK/root/etc/resolv.conf.zs" 2>/dev/null || true
    mount --bind /etc/resolv.conf "$WORK/root/etc/resolv.conf" 2>/dev/null || true
    chroot "$WORK/root" /bin/bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose >/dev/null && apt-get clean" \
      || echo "Docker installeren in het beeld mislukt; dat gebeurt bij de eerste start"
    umount "$WORK/root/etc/resolv.conf" 2>/dev/null || true
  fi
  if command -v docker >/dev/null 2>&1; then
    echo "Motor-images ophalen (arm64)…"
    mkdir -p "$WORK/root/opt/zonnestuur/motor/images"
    for img in ghcr.io/home-assistant/home-assistant:stable ghcr.io/home-assistant-libs/python-matter-server:stable; do
      f="$WORK/root/opt/zonnestuur/motor/images/$(echo "$img" | tr '/:' '__').tar"
      docker pull -q --platform linux/arm64 "$img" >/dev/null && docker save -o "$f" "$img" \
        || { echo "  $img overgeslagen (wordt bij de eerste start gedownload)"; rm -f "$f"; }
    done
  fi
fi

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
