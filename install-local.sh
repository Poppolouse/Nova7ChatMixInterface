#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_BIN="$HOME/.local/bin"
HOME_APPS="$HOME/.local/share/applications"
HOME_SYSTEMD="$HOME/.config/systemd/user"

HOME_STATE="$HOME/.local/state/nova7-chatmix"

mkdir -p "$HOME_BIN" "$HOME_APPS" "$HOME_SYSTEMD" "$HOME_STATE"

"$ROOT_DIR/scripts/install-headsetcontrol.sh"

install -m 0755 "$ROOT_DIR/mixer/nova7_mixer.py" "$HOME_BIN/nova7-mixer"
install -m 0755 "$ROOT_DIR/scripts/nova7-virtualaudio.sh" "$HOME_BIN/nova7-virtualaudio"
install -m 0644 "$ROOT_DIR/systemd/nova7-mixer.service" "$HOME_SYSTEMD/nova7-mixer.service"
install -m 0644 "$ROOT_DIR/systemd/nova7-virtualaudio.service" "$HOME_SYSTEMD/nova7-virtualaudio.service"
install -m 0644 "$ROOT_DIR/applet/res/io.github.poppolouse.CosmicAppletNovaChatMix.desktop" "$HOME_APPS/io.github.poppolouse.CosmicAppletNovaChatMix.desktop"

if command -v cargo >/dev/null 2>&1; then
  (
    cd "$ROOT_DIR/applet"
    cargo build --release
  )
  install -m 0755 "$ROOT_DIR/applet/target/release/cosmic-applet-nova-chatmix" "$HOME_BIN/cosmic-applet-nova-chatmix"
else
  echo "cargo not found; applet build skipped" >&2
fi

systemctl --user daemon-reload
systemctl --user enable nova7-virtualaudio.service nova7-mixer.service >/dev/null
systemctl --user restart nova7-virtualaudio.service nova7-mixer.service
"$ROOT_DIR/scripts/add-to-cosmic-panel.sh" || true

echo "Installed to $HOME"
