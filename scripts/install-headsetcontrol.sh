#!/usr/bin/env bash
set -euo pipefail

HEADSETCONTROL_VERSION="3.1.0"
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/nova7-chatmix"
SRC_DIR="$CACHE_ROOT/HeadsetControl-$HEADSETCONTROL_VERSION"
BUILD_DIR="$SRC_DIR/build"
HOME_BIN="$HOME/.local/bin"
LOCAL_HIDAPI_ROOT="$HOME/.local/opt/headsetcontrol-builddeps/hidapi/usr"
LOCAL_HIDAPI_LIBDIR="$LOCAL_HIDAPI_ROOT/lib/x86_64-linux-gnu"
LOCAL_HIDAPI_INCLUDEDIR="$LOCAL_HIDAPI_ROOT/include/hidapi"

mkdir -p "$CACHE_ROOT" "$HOME_BIN"

current_version() {
  if command -v "$HOME_BIN/headsetcontrol.bin" >/dev/null 2>&1; then
    "$HOME_BIN/headsetcontrol.bin" --help 2>/dev/null | sed -n 's/^Version: //p' | head -n 1
  fi
}

if [ "$(current_version || true)" = "$HEADSETCONTROL_VERSION" ]; then
  needs_build=0
else
  needs_build=1
fi

if [ "$needs_build" -eq 1 ]; then
  if [ ! -d "$SRC_DIR/.git" ]; then
    git clone --branch "$HEADSETCONTROL_VERSION" --depth 1 \
      https://github.com/Sapd/HeadsetControl.git "$SRC_DIR"
  fi

  mkdir -p "$BUILD_DIR"
  cd "$BUILD_DIR"

  cmake_args=(..)
  if [ -f "$LOCAL_HIDAPI_INCLUDEDIR/hidapi.h" ] && [ -f "$LOCAL_HIDAPI_LIBDIR/libhidapi-hidraw.so" ]; then
    cmake_args+=(
      "-DHIDAPI_INCLUDE_DIR=$LOCAL_HIDAPI_INCLUDEDIR"
      "-DHIDAPI_LIBRARY=$LOCAL_HIDAPI_LIBDIR/libhidapi-hidraw.so"
    )
  fi

  cmake "${cmake_args[@]}"
  make -j"$(nproc)"
  install -m 0755 "$BUILD_DIR/headsetcontrol" "$HOME_BIN/headsetcontrol.bin"
fi

if [ -d "$LOCAL_HIDAPI_LIBDIR" ]; then
  cat > "$HOME_BIN/headsetcontrol" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH="$LOCAL_HIDAPI_LIBDIR\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}"
exec "$HOME_BIN/headsetcontrol.bin" "\$@"
WRAPPER
else
  cat > "$HOME_BIN/headsetcontrol" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME_BIN/headsetcontrol.bin" "\$@"
WRAPPER
fi

chmod 0755 "$HOME_BIN/headsetcontrol"
"$HOME_BIN/headsetcontrol.bin" --help 2>/dev/null | sed -n 's/^Version: /HeadsetControl version: /p' | head -n 1
