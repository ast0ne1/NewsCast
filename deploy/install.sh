#!/bin/bash
set -euo pipefail

DEST="/opt/newscast"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_HOSTNAME=""

usage() {
  echo "Usage: sudo ./deploy/install.sh [--hostname NAME]"
  echo "  --hostname NAME   Set the Pi hostname (reachable as http://NAME.local:8080)"
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/install.sh"
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      DEVICE_HOSTNAME="${2:-}"
      if [[ -z "$DEVICE_HOSTNAME" ]]; then
        usage
        exit 1
      fi
      shift 2
      ;;
    --hostname=*)
      DEVICE_HOSTNAME="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

echo "Installing NewsCast from $SRC to $DEST"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync avahi-daemon

if ! id -u newscast >/dev/null 2>&1; then
  useradd --system --home "$DEST" --shell /usr/sbin/nologin newscast
fi

mkdir -p "$DEST"

if [[ "$SRC" != "$DEST" ]]; then
  rsync -a \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '.git' \
    --exclude 'dist' \
    --exclude 'tests' \
    --exclude '.cursor' \
    "$SRC/" "$DEST/"
fi

chmod +x "$DEST/deploy/install.sh" "$DEST/deploy/run.sh" "$DEST/deploy/set-hostname.sh"

if [[ ! -d "$DEST/.venv" ]]; then
  python3 -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"

mkdir -p "$DEST/data/briefings" "$DEST/data/library"

if [[ ! -f "$DEST/.env" ]]; then
  cp "$DEST/.env.example" "$DEST/.env"
fi

systemctl enable --now avahi-daemon >/dev/null 2>&1 || true

if [[ -n "$DEVICE_HOSTNAME" ]]; then
  "$DEST/deploy/set-hostname.sh" "$DEVICE_HOSTNAME"
else
  lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  lan_ip="${lan_ip:-127.0.0.1}"
  port="$(awk -F= '$1=="PORT"{gsub(/\r/,"",$2); print $2; exit}' "$DEST/.env")"
  port="${port:-8080}"
  if grep -qE '^PUBLIC_BASE_URL=http://127\.0\.0\.1' "$DEST/.env"; then
    sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=http://${lan_ip}:${port}|" "$DEST/.env"
  fi
fi

chown -R newscast:newscast "$DEST"

cat > /etc/sudoers.d/newscast <<EOF
newscast ALL=(root) NOPASSWD: $DEST/deploy/set-hostname.sh, /bin/systemctl restart newscast
EOF
chmod 440 /etc/sudoers.d/newscast

cp "$DEST/deploy/newscast.service" /etc/systemd/system/newscast.service
systemctl daemon-reload
systemctl enable --now newscast
systemctl restart newscast

port="$(awk -F= '$1=="PORT"{gsub(/\r/,"",$2); print $2; exit}' "$DEST/.env")"
port="${port:-8080}"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
lan_ip="${lan_ip:-127.0.0.1}"
host_local="$(hostname -s 2>/dev/null || hostname).local"

echo
echo "NewsCast is installed and running."
echo "  Open  http://${host_local}:${port}"
echo "  or    http://${lan_ip}:${port}"
echo "  Sign in with admin / admin, then change the password on Settings."
echo "  Change hostname later: sudo $DEST/deploy/set-hostname.sh NAME"
echo "  Status: sudo systemctl status newscast"
echo
