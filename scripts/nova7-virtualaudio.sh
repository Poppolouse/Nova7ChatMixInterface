#!/usr/bin/env bash
set -euo pipefail

TARGET_SINK="${1:-alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo}"

unload_matching_modules() {
    local pattern
    for pattern in 'sink_name=GameMix' 'sink_name=ChatMix' 'source=GameMix.monitor' 'source=ChatMix.monitor'; do
        pactl list short modules | awk -v p="$pattern" '$0 ~ p { print $1 }' | while read -r module_id; do
            pactl unload-module "$module_id"
        done
    done
}

create_sink_pair() {
    pactl load-module module-null-sink sink_name=GameMix sink_properties=device.description=GameMix >/dev/null
    pactl load-module module-loopback source=GameMix.monitor sink="$TARGET_SINK" latency_msec=1 >/dev/null

    pactl load-module module-null-sink sink_name=ChatMix sink_properties=device.description=ChatMix >/dev/null
    pactl load-module module-loopback source=ChatMix.monitor sink="$TARGET_SINK" latency_msec=1 >/dev/null
}

main() {
    if ! pactl list sinks short | awk '{print $2}' | grep -qx "$TARGET_SINK"; then
        echo "Target sink not found: $TARGET_SINK" >&2
        exit 1
    fi

    unload_matching_modules
    create_sink_pair

    echo "Created GameMix and ChatMix on $TARGET_SINK"
}

main "$@"
