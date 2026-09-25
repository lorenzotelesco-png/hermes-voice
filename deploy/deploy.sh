#!/usr/bin/env bash
# Update the hub on the server: pull, install, build, restart, check.
#   sudo /opt/hermes-hub/deploy/deploy.sh [branch]      (default: master)
set -euo pipefail

APP=/opt/hermes-hub
BRANCH=${1:-master}
as_hub() { sudo -u hermes-hub -H "$@"; }

cd "$APP"
as_hub git fetch -q origin
as_hub git checkout -q "$BRANCH"
as_hub git pull -q --ff-only origin "$BRANCH"
echo "codice: $(as_hub git log -1 --format='%h %s')"

as_hub venv/bin/pip install -q -r requirements.txt
(cd web && as_hub npm ci --silent --no-audit --no-fund && as_hub npm run build --silent >/dev/null)
echo "app compilata"

# What runs as root or defines how the hub runs comes from the commit fetched
# from GitHub, never from the working tree: the hub can write its own
# checkout, and must not be able to change what root executes.
REF="origin/$BRANCH"
from_git() { git -c safe.directory="$APP" -C "$APP" show "$REF:$1"; }
install_from_git() {  # path mode destination
  from_git "$1" > "$3.new" && chmod "$2" "$3.new" && chown root:root "$3.new" && mv "$3.new" "$3"
}
install_from_git deploy/hermes-hub.service 644 /etc/systemd/system/hermes-hub.service
install -d -m 755 /usr/local/libexec
install_from_git deploy/control/hermes-hub-control 755 /usr/local/libexec/hermes-hub-control
install_from_git deploy/control/hermes-hub-control.socket 644 /etc/systemd/system/hermes-hub-control.socket
install_from_git "deploy/control/hermes-hub-control@.service" 644 "/etc/systemd/system/hermes-hub-control@.service"
install_from_git deploy/vault/hermes-hub-vault 755 /usr/local/libexec/hermes-hub-vault
install_from_git deploy/vault/hermes-hub-vault.socket 644 /etc/systemd/system/hermes-hub-vault.socket
install_from_git "deploy/vault/hermes-hub-vault@.service" 644 "/etc/systemd/system/hermes-hub-vault@.service"
systemctl daemon-reload
systemctl enable -q --now hermes-hub-control.socket hermes-hub-vault.socket
systemctl restart hermes-hub

for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:5000/api/health >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS http://127.0.0.1:5000/api/health >/dev/null && echo "hub: ok" || { echo "hub: NON risponde"; journalctl -u hermes-hub -n 30 --no-pager; exit 1; }

python3 scripts/contract_check.py
