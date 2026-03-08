#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="$HOME/.config/cosmic/com.system76.CosmicPanel.Panel/v1/plugins_wings"
[ -f "$CONFIG_FILE" ] || exit 0
python3 - "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('    "io.github.poppolouse.CosmicAppletNovaChatMix",\n', '')
path.write_text(text)
PY
pkill -x cosmic-panel || true
