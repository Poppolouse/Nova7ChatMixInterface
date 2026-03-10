#!/usr/bin/env bash
set -euo pipefail

systemctl --user disable --now nova7-mixer.service nova7-virtualaudio.service >/dev/null 2>&1 || true
systemctl --user disable --now nova-chatmix-indicator.service >/dev/null 2>&1 || true
rm -f "$HOME/.config/systemd/user/nova7-mixer.service"
rm -f "$HOME/.config/systemd/user/nova7-virtualaudio.service"
rm -f "$HOME/.config/systemd/user/nova-chatmix-indicator.service"
rm -f "$HOME/.local/bin/nova7-mixer"
rm -f "$HOME/.local/bin/nova7-virtualaudio"
rm -f "$HOME/.local/bin/nova-chatmix-gnome"
rm -f "$HOME/.local/bin/nova-chatmix-indicator"
rm -f "$HOME/.local/bin/cosmic-applet-nova-chatmix"
rm -f "$HOME/.local/bin/cosmic-applet-nova-chatmix.bin"
rm -f "$HOME/.local/share/applications/io.github.poppolouse.NovaChatMix.desktop"
rm -f "$HOME/.local/share/applications/io.github.poppolouse.CosmicAppletNovaChatMix.desktop"
rm -f "$HOME/.config/autostart/io.github.poppolouse.NovaChatMixTray.desktop"
rm -rf "$HOME/.local/state/nova7-chatmix"
"$(cd "$(dirname "$0")" && pwd)/scripts/remove-from-cosmic-panel.sh" || true
systemctl --user daemon-reload

echo "Removed local install"
