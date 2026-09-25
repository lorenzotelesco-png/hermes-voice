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
echo "codice: $(git log -1 --format='%h %s')"

as_hub venv/bin/pip install -q -r requirements.txt
(cd web && as_hub npm ci --silent --no-audit --no-fund && as_hub npm run build --silent >/dev/null)
echo "app compilata"

install -m 644 deploy/hermes-hub.service /etc/systemd/system/hermes-hub.service
systemctl daemon-reload
systemctl restart hermes-hub

for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:5000/api/health >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS http://127.0.0.1:5000/api/health >/dev/null && echo "hub: ok" || { echo "hub: NON risponde"; journalctl -u hermes-hub -n 30 --no-pager; exit 1; }

python3 scripts/contract_check.py
