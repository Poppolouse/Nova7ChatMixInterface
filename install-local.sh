#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_BIN="$HOME/.local/bin"
HOME_APPS="$HOME/.local/share/applications"
HOME_AUTOSTART="$HOME/.config/autostart"
HOME_SYSTEMD="$HOME/.config/systemd/user"

HOME_STATE="$HOME/.local/state/nova7-chatmix"

mkdir -p "$HOME_BIN" "$HOME_APPS" "$HOME_AUTOSTART" "$HOME_SYSTEMD" "$HOME_STATE"

"$ROOT_DIR/scripts/install-headsetcontrol.sh"

install -m 0755 "$ROOT_DIR/mixer/nova7_mixer.py" "$HOME_BIN/nova7-mixer"
install -m 0755 "$ROOT_DIR/scripts/nova7-virtualaudio.sh" "$HOME_BIN/nova7-virtualaudio"
install -m 0755 "$ROOT_DIR/gnome/nova_chatmix_gnome.py" "$HOME_BIN/nova-chatmix-gnome"
install -m 0755 "$ROOT_DIR/gnome/nova_chatmix_indicator.py" "$HOME_BIN/nova-chatmix-indicator"
install -m 0644 "$ROOT_DIR/systemd/nova7-mixer.service" "$HOME_SYSTEMD/nova7-mixer.service"
install -m 0644 "$ROOT_DIR/systemd/nova7-virtualaudio.service" "$HOME_SYSTEMD/nova7-virtualaudio.service"
install -m 0644 "$ROOT_DIR/gnome/io.github.poppolouse.NovaChatMix.desktop" "$HOME_APPS/io.github.poppolouse.NovaChatMix.desktop"
install -m 0644 "$ROOT_DIR/gnome/io.github.poppolouse.NovaChatMixTray.desktop" "$HOME_AUTOSTART/io.github.poppolouse.NovaChatMixTray.desktop"

if [ "${XDG_CURRENT_DESKTOP:-}" = "COSMIC" ] && command -v cargo >/dev/null 2>&1; then
  (
    cd "$ROOT_DIR/applet"
    cargo build --release
  )
  install -m 0755 "$ROOT_DIR/applet/target/release/cosmic-applet-nova-chatmix" "$HOME_BIN/cosmic-applet-nova-chatmix.bin"
  cat > "$HOME_BIN/cosmic-applet-nova-chatmix" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"
export XLOCALEDIR="${XLOCALEDIR:-/usr/share/X11/locale}"
export XCOMPOSEFILE="${XCOMPOSEFILE:-/usr/share/X11/locale/en_US.UTF-8/Compose}"
export LD_LIBRARY_PATH="/home/linuxbrew/.linuxbrew/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
exec "$HOME/.local/bin/cosmic-applet-nova-chatmix.bin" "$@"
WRAPPER
  chmod 0755 "$HOME_BIN/cosmic-applet-nova-chatmix"
else
  echo "COSMIC applet build skipped" >&2
fi

systemctl --user daemon-reload
systemctl --user enable nova7-virtualaudio.service nova7-mixer.service >/dev/null
systemctl --user restart nova7-virtualaudio.service nova7-mixer.service
if [ "${XDG_CURRENT_DESKTOP:-}" = "COSMIC" ]; then
  install -m 0644 "$ROOT_DIR/applet/res/io.github.poppolouse.CosmicAppletNovaChatMix.desktop" "$HOME_APPS/io.github.poppolouse.CosmicAppletNovaChatMix.desktop"
  "$ROOT_DIR/scripts/add-to-cosmic-panel.sh" || true
else
  rm -f "$HOME_APPS/io.github.poppolouse.CosmicAppletNovaChatMix.desktop"
fi
pkill -f "$HOME_BIN/nova-chatmix-indicator" >/dev/null 2>&1 || true
nohup "$HOME_BIN/nova-chatmix-indicator" >/dev/null 2>&1 &

echo "Installed to $HOME"
