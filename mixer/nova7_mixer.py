#!/usr/bin/env python3
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

DEFAULT_POLL_SECONDS = 0.1
POLL_PROFILE_SECONDS = {
    "balanced": 0.1,
    "ultra": 0.05,
}
BATTERY_POLL_SECONDS = 5
MAX_BACKOFF_SECONDS = 10
GAME_SINK = "GameMix"
CHAT_SINK = "ChatMix"
DEFAULT_INACTIVE_TIME_MINUTES = 0
STATE_DIR = Path.home() / ".local" / "state" / "nova7-chatmix"
STATE_FILE = STATE_DIR / "status.json"

log = logging.getLogger("nova7-mixer")

_shutdown_requested = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown_requested
    log.info("Received signal %s, shutting down", signal.Signals(signum).name)
    _shutdown_requested = True


def setup_logging() -> None:
    level_name = os.environ.get("NOVA7_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def configured_inactive_time() -> int:
    raw = os.environ.get("NOVA7_INACTIVE_TIME_MINUTES", str(DEFAULT_INACTIVE_TIME_MINUTES))
    try:
        value = int(raw)
    except ValueError:
        log.warning("Invalid NOVA7_INACTIVE_TIME_MINUTES=%r, falling back to %d", raw, DEFAULT_INACTIVE_TIME_MINUTES)
        return DEFAULT_INACTIVE_TIME_MINUTES
    return max(0, min(90, value))


def configured_poll_seconds() -> float:
    profile = os.environ.get("NOVA7_POLL_PROFILE", "").strip().lower()
    if profile in POLL_PROFILE_SECONDS:
        return POLL_PROFILE_SECONDS[profile]

    raw = os.environ.get("NOVA7_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        log.warning("Invalid NOVA7_POLL_SECONDS=%r, falling back to %.2f", raw, DEFAULT_POLL_SECONDS)
        return DEFAULT_POLL_SECONDS
    return max(0.05, min(2.0, value))


def run_checked(*args: str) -> str:
    proc = subprocess.run(args, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(args)}")
    return proc.stdout.strip()


def current_chatmix() -> int | None:
    try:
        proc = subprocess.run(
            ["headsetcontrol", "-m", "-o", "short"],
            text=True, capture_output=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        log.debug("Non-integer chatmix output: %r", text)
        return None


def _clamp_battery_level(value: object) -> int | None:
    if not isinstance(value, int):
        return None
    if value < 0:
        return None
    return max(0, min(100, value))


def current_battery() -> tuple[int | None, bool | None, bool | None]:
    """Return (battery_level, battery_charging, headset_connected)."""
    level: int | None = None
    charging: bool | None = None
    connected: bool | None = None

    # Prefer JSON because it includes the battery status enum.
    try:
        proc = subprocess.run(
            ["headsetcontrol", "-b", "-o", "json"],
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            payload = json.loads(proc.stdout)
            devices = payload.get("devices") or []
            battery = devices[0].get("battery") if devices else None
            if isinstance(battery, dict):
                status = str(battery.get("status", "")).upper()
                level = _clamp_battery_level(battery.get("level"))

                if status == "BATTERY_CHARGING":
                    charging = True
                    connected = True
                elif status == "BATTERY_AVAILABLE":
                    charging = False
                    connected = True
                elif status in {"BATTERY_UNAVAILABLE", "BATTERY_TIMEOUT", "BATTERY_ERROR"}:
                    charging = False
                    connected = False
    except (FileNotFoundError, json.JSONDecodeError, IndexError, KeyError, TypeError):
        pass

    if connected is not None:
        return level, charging, connected

    # Battery level
    try:
        proc = subprocess.run(
            ["headsetcontrol", "-b", "-o", "short"],
            text=True, capture_output=True,
        )
        if proc.returncode == 0:
            text = proc.stdout.strip()
            if text:
                try:
                    level = _clamp_battery_level(int(text))
                except ValueError:
                    log.debug("Non-integer battery output: %r", text)
    except FileNotFoundError:
        pass

    # Battery charging status
    try:
        proc = subprocess.run(
            ["headsetcontrol", "-cb", "-o", "short"],
            text=True, capture_output=True,
        )
        if proc.returncode == 0:
            text = proc.stdout.strip().upper()
            if "CHARGING" in text:
                charging = True
            elif text:
                # Some versions output the level; if we got a number, not charging
                try:
                    int(text)
                    charging = False
                except ValueError:
                    charging = False
    except FileNotFoundError:
        pass

    if level is not None:
        connected = True
    elif charging is True:
        connected = True

    return level, charging, connected


def mix_to_volumes(mix_level: int) -> tuple[int, int]:
    mix_level = max(0, min(128, mix_level))
    if mix_level > 64:
        game = max(0, 200 - (mix_level * 100 // 64))
        chat = 100
    elif mix_level < 64:
        game = 100
        chat = max(0, mix_level * 100 // 64)
    else:
        game = 100
        chat = 100
    return game, chat


def set_sink_volume(sink: str, percent: int) -> None:
    try:
        subprocess.run(["pactl", "set-sink-volume", sink, f"{percent}%"], check=False)
    except FileNotFoundError:
        log.error("pactl not found; cannot set volume for sink %s", sink)


def set_inactive_time(minutes: int) -> bool:
    try:
        proc = subprocess.run(
            ["headsetcontrol", "-i", str(minutes)],
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        log.error("headsetcontrol not found; cannot set inactive time")
        return False

    if proc.returncode == 0:
        log.info("Set headset inactive timeout to %d minute(s)", minutes)
        return True

    message = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
    log.warning("Failed to set inactive timeout to %d minute(s): %s", minutes, message)
    return False


def write_state(
    chatmix_level: int | None,
    game_volume: int,
    chat_volume: int,
    battery_level: int | None,
    battery_charging: bool | None,
    headset_connected: bool,
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "chatmix_level": chatmix_level,
        "game_volume": game_volume,
        "chat_volume": chat_volume,
        "battery_level": battery_level,
        "battery_charging": battery_charging,
        "headset_connected": headset_connected,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # Atomic write: temp file in same dir, then rename
    try:
        fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, STATE_FILE)
    except OSError:
        log.exception("Failed to write state file %s", STATE_FILE)


def main() -> None:
    setup_logging()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not which("headsetcontrol"):
        log.error("headsetcontrol is not installed or not in PATH")
        sys.exit(1)

    inactive_time_minutes = configured_inactive_time()
    poll_seconds = configured_poll_seconds()

    log.info("Nova7 ChatMix mixer started (poll=%.2fs, battery_poll=%ds)", poll_seconds, BATTERY_POLL_SECONDS)

    last_mix: int | None = None
    game_vol = 100
    chat_vol = 100
    battery_level: int | None = None
    battery_charging: bool | None = None
    headset_connected = False
    last_battery_poll = 0.0
    backoff = poll_seconds
    consecutive_failures = 0
    inactive_time_applied = False

    while not _shutdown_requested:
        now = time.monotonic()

        # Poll battery at a lower frequency
        if now - last_battery_poll >= BATTERY_POLL_SECONDS:
            battery_level, battery_charging, battery_connected = current_battery()
            last_battery_poll = now
            if battery_connected is not None:
                headset_connected = battery_connected
            log.debug(
                "Battery: level=%s charging=%s connected=%s",
                battery_level,
                battery_charging,
                headset_connected,
            )

        mix = current_chatmix()

        if mix is None or headset_connected is False:
            consecutive_failures += 1
            if consecutive_failures == 1:
                log.warning("Headset not available, entering backoff")
            # Write disconnected state
            write_state(
                chatmix_level=None,
                game_volume=game_vol,
                chat_volume=chat_vol,
                battery_level=None,
                battery_charging=None,
                headset_connected=False,
            )
            last_mix = None
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            log.debug("Backoff sleep: %.1fs", backoff)
            time.sleep(backoff)
            continue

        # Device is back / available
        if consecutive_failures > 0:
            log.info("Headset reconnected after %d failed polls", consecutive_failures)
            consecutive_failures = 0
            backoff = poll_seconds
            # Force a battery refresh on reconnect
            last_battery_poll = 0.0
            inactive_time_applied = False

        if not inactive_time_applied:
            inactive_time_applied = set_inactive_time(inactive_time_minutes)

        state_changed = mix != last_mix

        if state_changed:
            game_vol, chat_vol = mix_to_volumes(mix)
            set_sink_volume(GAME_SINK, game_vol)
            set_sink_volume(CHAT_SINK, chat_vol)
            log.info("ChatMix %d → game=%d%% chat=%d%%", mix, game_vol, chat_vol)
            last_mix = mix

        # Write state on every change or periodically (every battery poll cycle)
        write_state(
            chatmix_level=mix,
            game_volume=game_vol,
            chat_volume=chat_vol,
            battery_level=battery_level,
            battery_charging=battery_charging,
            headset_connected=True,
        )

        time.sleep(poll_seconds)

    log.info("Mixer shut down cleanly")


if __name__ == "__main__":
    main()
