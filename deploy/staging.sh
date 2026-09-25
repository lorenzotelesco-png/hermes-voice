#!/bin/bash
# Throwaway copy of a hub branch on 127.0.0.1:5056, next to production.
# Same Hermes, same venv; its own token (/root/.hub-staging-token), database
# and build. Used to try a phase against real Hermes data before it replaces
# production. From the PC:
#   ssh -N -L 5056:127.0.0.1:5056 LORE-SERVER   then  http://localhost:5056/?k=<token>
# Stop it with:  deploy/staging.sh --stop
set -euo pipefail
DIR=/tmp/hub-staging
as_hub() { sudo -u hermes-hub -H "$@"; }

if [ "${1:-}" = "--stop" ]; then
  pkill -f "uvicorn hub.main:app --app-dir $DIR/server" || true
  [ -d "$DIR" ] && as_hub git -C /opt/hermes-hub worktree remove --force "$DIR"
  rm -f /root/.hub-staging-token
  echo "staging stopped"
  exit 0
fi
BRANCH=${1:?usage: staging.sh <branch> | --stop}

as_hub git -C /opt/hermes-hub fetch -q origin "$BRANCH"
if [ -d "$DIR" ]; then
  as_hub git -C "$DIR" checkout -q --detach "origin/$BRANCH"
else
  as_hub git -C /opt/hermes-hub worktree add -q --detach "$DIR" "origin/$BRANCH"
fi
as_hub git -C "$DIR" log --oneline -1
cd "$DIR/web" && as_hub npm ci --silent >/dev/null && as_hub npm run build --silent >/dev/null && echo "build ok"

pkill -f "uvicorn hub.main:app --app-dir $DIR/server" || true
sleep 1
TOKEN=$(cat /root/.hub-staging-token 2>/dev/null || openssl rand -hex 16 | tee /root/.hub-staging-token)
chmod 600 /root/.hub-staging-token
set -a; . /opt/hermes-hub/.env; set +a
export VOICE_AUTH_TOKEN="$TOKEN" HUB_DB="$DIR/hub.db" HUB_WEB_DIST="$DIR/web/dist" PYTHONUNBUFFERED=1
cd "$DIR"
nohup sudo -E -u hermes-hub /opt/hermes-hub/venv/bin/uvicorn hub.main:app --app-dir "$DIR/server" \
  --host 127.0.0.1 --port 5056 --no-access-log > /tmp/hub-staging.log 2>&1 &
for i in $(seq 20); do curl -fs http://127.0.0.1:5056/health >/dev/null && break; sleep 0.5; done
curl -fs http://127.0.0.1:5056/health && echo " staging up"
