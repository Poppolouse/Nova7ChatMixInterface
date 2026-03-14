#!/usr/bin/env python3
"""Audio device priority daemon for Nova7 ChatMix.

Monitors PipeWire/PulseAudio device changes via ``pactl subscribe`` and
automatically switches the default sink and source to the highest-priority
available device as defined in the user configuration file.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "nova7-chatmix"
CONFIG_FILE = CONFIG_DIR / "audio-priority.json"

# Virtual sinks managed by nova7-virtualaudio — never treat as priority targets
VIRTUAL_SINK_KEYWORDS = ("GameMix", "ChatMix")

DEBOUNCE_SECONDS = 1.5

log = logging.getLogger("nova7-audio-priority")

_shutdown_requested = False

# ── default config ──────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "sink_priorities": [
        "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo",
        "alsa_output.pci-0000_00_1f.3.analog-stereo",
    ],
    "source_priorities": [
        "alsa_input.usb-SteelSeries_Arctis_Nova_7-00.mono-fallback",
        "alsa_input.pci-0000_00_1f.3.analog-stereo",
    ],
}

# ── helpers ─────────────────────────────────────────────────────────────────


def _run_pactl(*args: str) -> str | None:
    """Run a pactl command and return stripped stdout, or *None* on error."""
    try:
        result = subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        log.debug("pactl %s failed: %s", " ".join(args), result.stderr.strip())
    except FileNotFoundError:
        log.error("pactl not found — is PulseAudio / PipeWire-Pulse installed?")
    except subprocess.TimeoutExpired:
        log.warning("pactl %s timed out", " ".join(args))
    return None


# ── config ──────────────────────────────────────────────────────────────────


def load_config() -> dict:
    """Load priority config from disk; create a default file when missing."""
    if not CONFIG_FILE.exists():
        log.info("Config not found — creating default at %s", CONFIG_FILE)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        return dict(DEFAULT_CONFIG)

    try:
        with CONFIG_FILE.open() as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            raise ValueError("root element must be a JSON object")
        return cfg
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Invalid config (%s) — using defaults", exc)
        return dict(DEFAULT_CONFIG)


# ── device queries ──────────────────────────────────────────────────────────


def _is_virtual(name: str) -> bool:
    return any(kw in name for kw in VIRTUAL_SINK_KEYWORDS)


def get_available_sinks() -> list[str]:
    """Return names of real (non-virtual) sinks currently available."""
    out = _run_pactl("list", "sinks", "short")
    if out is None:
        return []
    sinks = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name = parts[1]
            if not _is_virtual(name):
                sinks.append(name)
    return sinks


def get_available_sources() -> list[str]:
    """Return names of real sources (no monitors, no virtual) currently available."""
    out = _run_pactl("list", "sources", "short")
    if out is None:
        return []
    sources = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name = parts[1]
            if not name.endswith(".monitor") and not _is_virtual(name):
                sources.append(name)
    return sources


def get_current_default_sink() -> str | None:
    return _run_pactl("get-default-sink")


def get_current_default_source() -> str | None:
    return _run_pactl("get-default-source")


def set_default_sink(sink_name: str) -> bool:
    out = _run_pactl("set-default-sink", sink_name)
    return out is not None


def set_default_source(source_name: str) -> bool:
    out = _run_pactl("set-default-source", source_name)
    return out is not None


# ── priority logic ──────────────────────────────────────────────────────────


def apply_priority(
    priorities: list[str],
    available: list[str],
    current: str | None,
    set_func,
    label: str,
) -> None:
    """Switch to the highest-priority available device when it isn't already the default."""
    if not priorities:
        log.debug("No %s priorities configured — skipping", label)
        return

    available_set = set(available)
    best: str | None = None
    for name in priorities:
        if name in available_set:
            best = name
            break

    if best is None:
        log.debug("No prioritised %s currently available", label)
        return

    if best == current:
        log.debug("%s already set to highest-priority device %s", label, best)
        return

    log.info("Switching %s: %s → %s", label, current, best)
    if not set_func(best):
        log.error("Failed to set %s to %s", label, best)


def apply_all_priorities() -> None:
    """Load configuration and apply both sink and source priorities."""
    cfg = load_config()

    if not cfg.get("enabled", True):
        log.debug("Audio priority disabled in config")
        return

    sink_prios: list[str] = cfg.get("sink_priorities", [])
    source_prios: list[str] = cfg.get("source_priorities", [])

    available_sinks = get_available_sinks()
    available_sources = get_available_sources()
    current_sink = get_current_default_sink()
    current_source = get_current_default_source()

    apply_priority(sink_prios, available_sinks, current_sink, set_default_sink, "sink")
    apply_priority(source_prios, available_sources, current_source, set_default_source, "source")


# ── event monitor ───────────────────────────────────────────────────────────

_EVENT_RE = re.compile(
    r"Event\s+'(new|remove|change)'\s+on\s+(sink|source|server)",
    re.IGNORECASE,
)


def monitor_devices() -> None:  # noqa: C901
    """Subscribe to PulseAudio events and re-evaluate priorities on changes.

    Restarts the subscription process if it exits unexpectedly.
    """
    while not _shutdown_requested:
        log.info("Starting pactl subscribe")
        try:
            proc = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            log.error("pactl not found — retrying in 10 s")
            _interruptible_sleep(10)
            continue

        last_apply: float = 0.0

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if _shutdown_requested:
                    break
                if _EVENT_RE.search(line):
                    now = time.monotonic()
                    if now - last_apply < DEBOUNCE_SECONDS:
                        continue
                    # Debounce: wait a moment for the device to fully initialise
                    _interruptible_sleep(DEBOUNCE_SECONDS)
                    if _shutdown_requested:
                        break
                    apply_all_priorities()
                    last_apply = time.monotonic()
        except Exception:
            log.exception("Error reading pactl subscribe output")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        if not _shutdown_requested:
            log.warning("pactl subscribe exited — restarting in 5 s")
            _interruptible_sleep(5)


# ── utilities ───────────────────────────────────────────────────────────────


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in small increments so we can honour shutdown requests promptly."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _shutdown_requested:
        time.sleep(min(0.25, end - time.monotonic()))


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown_requested
    log.info("Received %s — shutting down", signal.Signals(signum).name)
    _shutdown_requested = True


def setup_logging() -> None:
    level_name = os.environ.get("NOVA7_PRIO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ── entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("Audio priority daemon started")

    # Apply priorities once at startup
    apply_all_priorities()

    # Then watch for device changes
    monitor_devices()

    log.info("Audio priority daemon stopped")


if __name__ == "__main__":
    main()
