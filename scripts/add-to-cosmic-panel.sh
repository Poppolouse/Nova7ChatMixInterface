#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="$HOME/.config/cosmic/com.system76.CosmicPanel.Panel/v1/plugins_wings"
APPLET_ID='"io.github.poppolouse.CosmicAppletNovaChatMix",'

mkdir -p "$(dirname "$CONFIG_FILE")"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "COSMIC panel config not found: $CONFIG_FILE" >&2
    exit 1
fi

if grep -q 'io.github.poppolouse.CosmicAppletNovaChatMix' "$CONFIG_FILE"; then
    exit 0
fi

python3 - "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = '    "com.system76.CosmicAppletPower",'
insert = '    "io.github.poppolouse.CosmicAppletNovaChatMix",\n'
if needle in text:
    text = text.replace(needle, insert + needle, 1)
else:
    marker = ']))'
    if marker not in text:
        raise SystemExit('Could not patch COSMIC panel config')
    text = text.replace(marker, insert + ']))', 1)
path.write_text(text)
PY

pkill -x cosmic-panel || true
