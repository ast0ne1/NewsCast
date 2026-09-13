#!/bin/bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/set-hostname.sh NAME"
  exit 1
fi

name="${1:-}"
if [[ ! "$name" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]]; then
  echo "Hostname must be 1-63 letters, digits, or hyphens, and not start or end with a hyphen."
  echo "Usage: sudo ./deploy/set-hostname.sh newscast"
  exit 1
fi

hostnamectl set-hostname "$name"

if grep -qE '^127\.0\.1\.1\b' /etc/hosts; then
  sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1\t${name}/" /etc/hosts
else
  printf '127.0.1.1\t%s\n' "$name" >> /etc/hosts
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
fi

env_file="/opt/newscast/.env"
if [[ -f "$env_file" ]]; then
  port="$(awk -F= '$1=="PORT"{gsub(/\r/,"",$2); print $2; exit}' "$env_file")"
  port="${port:-8080}"
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=http://${name}.local:${port}|" "$env_file"
  if grep -q '^DEVICE_HOSTNAME=' "$env_file"; then
    sed -i "s|^DEVICE_HOSTNAME=.*|DEVICE_HOSTNAME=${name}|" "$env_file"
  else
    printf 'DEVICE_HOSTNAME=%s\n' "$name" >> "$env_file"
  fi
fi

echo "Hostname is ${name}. Browse http://${name}.local:${port:-8080}"
