#!/usr/bin/env bash
# Install or update Beeper Desktop for the hub's inbox.
#   sudo deploy/beeper/install.sh            latest stable
#   sudo deploy/beeper/install.sh 4.3.152    a given version (also to go back)
# Versions stay side by side in /opt/beeper; /opt/beeper/current points at the
# one in use. Updates are by hand: a new Beeper can change what the hub reads.
set -euo pipefail

LATEST=https://api.beeper.com/desktop/download/linux/x64/stable/com.automattic.beeper.desktop
if [ -n "${1:-}" ]; then
  VERSION=$1
  URL="https://beeper-desktop.download.beeper.com/builds/Beeper-$VERSION-x86_64.AppImage"
else
  URL=$(curl -fsSI "$LATEST" | tr -d '\r' | awk 'tolower($1)=="location:" {print $2}')
  VERSION=$(basename "$URL" | sed -E 's/^Beeper-(.*)-x86_64\.AppImage$/\1/')
fi
DIR=/opt/beeper/$VERSION

id beeper >/dev/null 2>&1 || useradd --system --home-dir /var/lib/beeper --create-home --shell /usr/sbin/nologin beeper
if [ ! -x "$DIR/beepertexts" ]; then
  tmp=$(mktemp -d /opt/beeper.XXXX)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "$tmp/Beeper.AppImage" "$URL"
  chmod +x "$tmp/Beeper.AppImage"
  (cd "$tmp" && ./Beeper.AppImage --appimage-extract >/dev/null)
  mkdir -p /opt/beeper
  chown -R root:root "$tmp/squashfs-root"
  mv "$tmp/squashfs-root" "$DIR"
fi
# Chromium's sandbox: the setuid helper, so it never needs --no-sandbox.
chown root:root "$DIR/chrome-sandbox" && chmod 4755 "$DIR/chrome-sandbox"
ln -sfn "$DIR" /opt/beeper/current
echo "beeper: $VERSION"

if systemctl is-enabled -q beeper-desktop 2>/dev/null; then
  systemctl restart beeper-desktop
  echo "beeper-desktop riavviato"
fi
