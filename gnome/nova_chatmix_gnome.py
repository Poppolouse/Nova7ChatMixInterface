#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango


# ─── Constants ───────────────────────────────────────────────────────────────

APP_ID = "io.github.poppolouse.NovaChatMix"
SERVICE_NAME = "nova7-mixer.service"
STATE_FILE = Path.home() / ".local/state/nova7-chatmix/status.json"
SERVICE_OVERRIDE_DIR = Path.home() / ".config/systemd/user/nova7-mixer.service.d"
SERVICE_OVERRIDE_FILE = SERVICE_OVERRIDE_DIR / "override.conf"
PRIORITY_CONFIG_DIR = Path.home() / ".config/nova7-chatmix"
PRIORITY_CONFIG_FILE = PRIORITY_CONFIG_DIR / "audio-priority.json"
GAME_SINK = "GameMix"
CHAT_SINK = "ChatMix"
STATE_WATCH_MS = 75
FULL_REFRESH_MS = 5000
STATE_FILE_MAX_AGE_SECONDS = 10
RESTRICTED_APPS = ("discord", "teams", "zoom", "slack")


# ─── App Name & Icon Normalization ───────────────────────────────────────────

APP_NAME_MAP = {
    # Browsers
    "firefox": "Firefox",
    "Firefox": "Firefox",
    "chromium": "Chromium",
    "chromium-browser": "Chromium",
    "google-chrome": "Chrome",
    "Google Chrome": "Chrome",
    "vivaldi": "Vivaldi",
    "brave": "Brave",
    "Brave Browser": "Brave",
    # Communication
    "discord": "Discord",
    "Discord": "Discord",
    "WebCord": "Discord",
    "webcord": "Discord",
    "teams": "Teams",
    "Microsoft Teams": "Teams",
    "zoom": "Zoom",
    "slack": "Slack",
    "Slack": "Slack",
    # Media
    "spotify": "Spotify",
    "Spotify": "Spotify",
    "vlc": "VLC",
    "mpv": "mpv",
    "Audacity": "Audacity",
    "rhythmbox": "Rhythmbox",
    # Gaming
    "steam": "Steam",
    "Steam": "Steam",
    "gamescope": "Gamescope",
    "lutris": "Lutris",
    # System
    "pipewire": "PipeWire",
    "wireplumber": "WirePlumber",
    "gnome-shell": "GNOME Shell",
    "org.gnome.Shell": "GNOME Shell",
    "pulseaudio": "PulseAudio",
    "WEBRTC VoiceEngine": "WebRTC",
    "playStream": "Stream",
    "Speech Dispatcher": "Speech",
    "speech-dispatcher": "Speech",
}

APP_ICON_MAP = {
    "Firefox": "firefox-symbolic",
    "Chromium": "web-browser-symbolic",
    "Chrome": "web-browser-symbolic",
    "Vivaldi": "web-browser-symbolic",
    "Brave": "web-browser-symbolic",
    "Discord": "user-available-symbolic",
    "Teams": "user-available-symbolic",
    "Zoom": "camera-web-symbolic",
    "Slack": "user-available-symbolic",
    "Spotify": "audio-x-generic-symbolic",
    "VLC": "multimedia-video-player-symbolic",
    "mpv": "multimedia-video-player-symbolic",
    "Audacity": "audio-x-generic-symbolic",
    "Rhythmbox": "audio-x-generic-symbolic",
    "Steam": "input-gaming-symbolic",
    "Gamescope": "input-gaming-symbolic",
    "Lutris": "input-gaming-symbolic",
    "PipeWire": "audio-card-symbolic",
    "WirePlumber": "audio-card-symbolic",
    "GNOME Shell": "application-x-executable-symbolic",
    "PulseAudio": "audio-card-symbolic",
    "WebRTC": "audio-input-microphone-symbolic",
    "Stream": "audio-x-generic-symbolic",
    "Speech": "audio-input-microphone-symbolic",
}

DEFAULT_APP_ICON = "application-x-executable-symbolic"


def normalize_app_name(raw_name: str) -> str:
    if raw_name in APP_NAME_MAP:
        return APP_NAME_MAP[raw_name]
    lower = raw_name.lower()
    for key, value in APP_NAME_MAP.items():
        if key.lower() == lower:
            return value
    return raw_name


def get_app_icon(normalized_name: str) -> str:
    return APP_ICON_MAP.get(normalized_name, DEFAULT_APP_ICON)


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class SinkInput:
    input_id: int
    app_name: str
    sink_name: str
    restricted: bool


@dataclass
class AppState:
    headset_connected: bool
    battery_level: Optional[int]
    battery_charging: bool
    chatmix_raw: Optional[int]
    game_volume: int
    chat_volume: int
    service_active: str
    service_enabled: str
    controller_status: str
    sink_inputs: List[SinkInput]
    last_log_line: str
    last_error: Optional[str]
    poll_profile: str


@dataclass(eq=True)
class LiveState:
    headset_connected: bool
    battery_level: Optional[int]
    battery_charging: bool
    chatmix_raw: Optional[int]
    game_volume: int
    chat_volume: int


# ─── Utility Functions ───────────────────────────────────────────────────────

def run_text(*args: str) -> Optional[str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return None
    output = proc.stdout.strip() or proc.stderr.strip()
    return output or None


def run_ok(*args: str) -> bool:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return False
    return proc.returncode == 0


def state_file_data() -> dict:
    try:
        age = datetime.now(timezone.utc).timestamp() - STATE_FILE.stat().st_mtime
        if age > STATE_FILE_MAX_AGE_SECONDS:
            return {}
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def mix_to_volumes(mix: int) -> tuple[int, int]:
    mix = max(0, min(128, mix))
    if mix > 64:
        return max(0, 200 - mix * 100 // 64), 100
    if mix < 64:
        return 100, max(0, mix * 100 // 64)
    return 100, 100


def get_sink_name_map() -> dict[str, str]:
    out = run_text("pactl", "list", "sinks", "short")
    if not out:
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


def preferred_system_sink() -> Optional[str]:
    sinks = get_sink_name_map()
    default_sink = run_text("pactl", "get-default-sink")
    if default_sink and default_sink not in {GAME_SINK, CHAT_SINK}:
        return default_sink

    info_default = run_text("pactl", "info")
    if info_default:
        for line in info_default.splitlines():
            if line.startswith("Default Sink:"):
                candidate = line.partition(":")[2].strip()
                if candidate and candidate not in {GAME_SINK, CHAT_SINK}:
                    return candidate

    for sink_name in sinks.values():
        if sink_name not in {GAME_SINK, CHAT_SINK}:
            return sink_name
    return None


def parse_sink_inputs() -> List[SinkInput]:
    out = run_text("pactl", "list", "sink-inputs")
    if not out:
        return []

    sink_map = get_sink_name_map()
    items: List[SinkInput] = []
    current_id: Optional[int] = None
    current_sink: Optional[str] = None
    current_app: Optional[str] = None
    current_binary: Optional[str] = None
    current_app_id: Optional[str] = None
    current_media_name: Optional[str] = None

    def flush() -> None:
        nonlocal current_id, current_sink, current_app, current_binary
        nonlocal current_app_id, current_media_name
        if current_id is None:
            return
        app_name = current_app or current_binary
        if current_app_id:
            app_name = current_app_id.rsplit(".", 1)[-1]
        if current_media_name == "playStream" and current_binary:
            app_name = current_binary
        if not app_name:
            return
        app_name = normalize_app_name(app_name)
        sink_name = sink_map.get(current_sink or "", f"sink#{current_sink or '?'}")
        app_lower = app_name.lower()
        items.append(
            SinkInput(
                input_id=current_id,
                app_name=app_name,
                sink_name=sink_name,
                restricted=any(name in app_lower for name in RESTRICTED_APPS),
            )
        )

    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("Sink Input #"):
            flush()
            current_id = int(line.removeprefix("Sink Input #"))
            current_sink = None
            current_app = None
            current_binary = None
            current_app_id = None
            current_media_name = None
        elif line.startswith("Sink:"):
            current_sink = line.removeprefix("Sink:").strip()
        elif line.startswith("application.name"):
            _, _, value = line.partition("=")
            current_app = value.strip().strip('"')
        elif line.startswith("application.process.binary"):
            _, _, value = line.partition("=")
            current_binary = value.strip().strip('"')
        elif line.startswith("pipewire.access.portal.app_id"):
            _, _, value = line.partition("=")
            current_app_id = value.strip().strip('"')
        elif line.startswith("media.name"):
            _, _, value = line.partition("=")
            current_media_name = value.strip().strip('"')

    flush()
    return items


def detect_state() -> AppState:
    data = state_file_data()

    connected = bool(data.get("headset_connected"))
    battery_level = data.get("battery_level")
    if isinstance(battery_level, int) and battery_level < 0:
        battery_level = None
    battery_charging = bool(data.get("battery_charging"))

    chatmix_raw = data.get("chatmix_level")
    if not isinstance(chatmix_raw, int):
        text = run_text("headsetcontrol", "-m", "-o", "short")
        chatmix_raw = int(text) if text and text.strip().lstrip("-").isdigit() else None

    if isinstance(chatmix_raw, int):
        game_volume, chat_volume = mix_to_volumes(chatmix_raw)
    else:
        game_volume, chat_volume = 0, 0

    service_active = run_text("systemctl", "--user", "is-active", SERVICE_NAME) or "unknown"
    service_enabled = run_text("systemctl", "--user", "is-enabled", SERVICE_NAME) or "unknown"

    controller_status_raw = run_text("headsetcontrol", "-m", "-o", "short")
    if controller_status_raw and controller_status_raw.strip().lstrip("-").isdigit():
        controller_status = "Ready"
    elif controller_status_raw and "Could not open device" in controller_status_raw:
        controller_status = "No permissions"
    else:
        controller_status = "Unavailable"

    last_log_line = (
        run_text("journalctl", "--user", "-u", SERVICE_NAME, "-n", "1", "--no-pager")
        or "No logs yet"
    )

    poll_profile = "balanced"
    if SERVICE_OVERRIDE_FILE.exists():
        try:
            text = SERVICE_OVERRIDE_FILE.read_text()
            for line in text.splitlines():
                if line.startswith("Environment=NOVA7_POLL_PROFILE="):
                    poll_profile = line.rsplit("=", 1)[-1].strip().strip('"')
                    break
        except OSError:
            pass

    last_error = None
    return AppState(
        headset_connected=connected,
        battery_level=battery_level if isinstance(battery_level, int) else None,
        battery_charging=battery_charging,
        chatmix_raw=chatmix_raw if isinstance(chatmix_raw, int) else None,
        game_volume=game_volume,
        chat_volume=chat_volume,
        service_active=service_active,
        service_enabled=service_enabled,
        controller_status=controller_status,
        sink_inputs=parse_sink_inputs(),
        last_log_line=last_log_line,
        last_error=last_error,
        poll_profile=poll_profile,
    )


def detect_live_state() -> LiveState:
    data = state_file_data()

    connected = bool(data.get("headset_connected"))
    battery_level = data.get("battery_level")
    if isinstance(battery_level, int) and battery_level < 0:
        battery_level = None
    battery_charging = bool(data.get("battery_charging"))

    chatmix_raw = data.get("chatmix_level")
    if not isinstance(chatmix_raw, int):
        chatmix_raw = None

    if isinstance(chatmix_raw, int):
        game_volume, chat_volume = mix_to_volumes(chatmix_raw)
    else:
        game_volume, chat_volume = 0, 0

    return LiveState(
        headset_connected=connected,
        battery_level=battery_level if isinstance(battery_level, int) else None,
        battery_charging=battery_charging,
        chatmix_raw=chatmix_raw,
        game_volume=game_volume,
        chat_volume=chat_volume,
    )


# ─── Audio Priority Functions ────────────────────────────────────────────────

def get_available_sinks() -> list[tuple[str, str]]:
    out = run_text("pactl", "list", "sinks")
    if not out:
        return []
    sinks: list[tuple[str, str]] = []
    current_name: Optional[str] = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped.removeprefix("Name:").strip()
        elif stripped.startswith("Description:") and current_name:
            desc = stripped.removeprefix("Description:").strip()
            if current_name not in {GAME_SINK, CHAT_SINK}:
                sinks.append((current_name, desc))
            current_name = None
    return sinks


def load_priority_config() -> dict:
    try:
        return json.loads(PRIORITY_CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "priorities": []}


def save_priority_config(config: dict) -> None:
    try:
        PRIORITY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PRIORITY_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    except OSError:
        pass


def apply_audio_priority() -> Optional[str]:
    config = load_priority_config()
    if not config.get("enabled"):
        return None
    available = {name for name, _ in get_available_sinks()}
    for sink_name in config.get("priorities", []):
        if sink_name in available:
            run_ok("pactl", "set-default-sink", sink_name)
            return sink_name
    return None


# ─── CSS Theme ───────────────────────────────────────────────────────────────

CSS = """
/* -- Hero Card -- */
.hero-card {
    background-color: @card_bg_color;
    border-radius: 16px;
    padding: 24px;
    border: 1px solid @borders;
}

.hero-title {
    font-size: 22px;
    font-weight: 800;
}

.eyebrow {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.10em;
    opacity: 0.55;
}

.hero-subtitle {
    font-size: 13px;
    opacity: 0.60;
}

/* -- Status Pills -- */
.status-pill {
    border-radius: 999px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 700;
}

.status-good {
    background-color: #1c4a34;
    color: #6ee7b7;
}

.status-bad {
    background-color: #4a1c1c;
    color: #fca5a5;
}

.status-warning {
    background-color: #4a3b1c;
    color: #fcd34d;
}

/* -- Metric Cards -- */
.metric-card {
    background-color: @view_bg_color;
    border: 1px solid @borders;
    border-radius: 12px;
    padding: 14px;
}

.metric-title {
    font-size: 11px;
    font-weight: 700;
    opacity: 0.50;
    letter-spacing: 0.08em;
}

.metric-value {
    font-size: 26px;
    font-weight: 800;
}

.metric-sub {
    font-size: 11px;
    opacity: 0.45;
}

/* -- Section Headers -- */
.section-header {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.10em;
    opacity: 0.40;
    margin-top: 4px;
}

/* -- Kanban Board -- */
.kanban-column {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 16px;
    padding: 14px;
    min-height: 120px;
}

.kanban-column-game {
    border-top: 3px solid #818cf8;
}

.kanban-column-chat {
    border-top: 3px solid #22d3ee;
}

.kanban-column-system {
    border-top: 3px solid #6b7280;
}

.kanban-header {
    font-size: 14px;
    font-weight: 700;
}

.kanban-count {
    font-size: 11px;
    font-weight: 600;
    opacity: 0.50;
    background-color: @view_bg_color;
    border-radius: 999px;
    padding: 2px 10px;
}

.kanban-empty {
    font-size: 12px;
    opacity: 0.30;
    padding: 20px 0px;
}

/* -- App Cards -- */
.app-card {
    background-color: @view_bg_color;
    border: 1px solid @borders;
    border-radius: 10px;
    padding: 10px 12px;
}

.app-card:hover {
    background-color: @card_bg_color;
}

.app-card-game {
    border-left: 3px solid #818cf8;
}

.app-card-chat {
    border-left: 3px solid #22d3ee;
}

.app-card-system {
    border-left: 3px solid #6b7280;
}

.app-card-name {
    font-size: 13px;
    font-weight: 600;
}

.app-card-sink {
    font-size: 11px;
    opacity: 0.45;
}

.drag-hover {
    background-color: alpha(@accent_bg_color, 0.15);
    border-color: @accent_bg_color;
}

/* -- Balance Section -- */
.balance-card {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 16px;
    padding: 18px;
}

.balance-label {
    font-size: 13px;
    font-weight: 600;
}

.balance-value {
    font-size: 13px;
    font-weight: 700;
    opacity: 0.65;
}

progressbar.game-bar > trough {
    background-color: @view_bg_color;
    border-radius: 6px;
    min-height: 9px;
}

progressbar.game-bar > trough > progress {
    background-color: #818cf8;
    border-radius: 6px;
    min-height: 9px;
}

progressbar.chat-bar > trough {
    background-color: @view_bg_color;
    border-radius: 6px;
    min-height: 9px;
}

progressbar.chat-bar > trough > progress {
    background-color: #22d3ee;
    border-radius: 6px;
    min-height: 9px;
}

/* -- Engine Section -- */
.engine-card {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 16px;
    padding: 18px;
}

.engine-row {
    padding: 5px 0px;
}

.engine-label {
    font-size: 13px;
    opacity: 0.60;
}

.engine-value {
    font-size: 13px;
    font-weight: 600;
}

/* -- Priority Section -- */
.priority-card {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 16px;
    padding: 18px;
}

.priority-row {
    background-color: @view_bg_color;
    border: 1px solid @borders;
    border-radius: 10px;
    padding: 9px 12px;
}

.priority-row-active {
    border-color: #1a7a52;
    background-color: #122e20;
}

.priority-number {
    font-size: 14px;
    font-weight: 800;
    opacity: 0.30;
    min-width: 26px;
}

.priority-name {
    font-size: 13px;
    font-weight: 600;
}

.priority-desc {
    font-size: 11px;
    opacity: 0.40;
}

/* -- Action Buttons -- */
.action-button {
    border-radius: 10px;
    padding: 8px 16px;
    font-weight: 600;
}

.caption {
    opacity: 0.55;
}
""".encode()


# ─── Widget: Metric Card ────────────────────────────────────────────────────

class MetricCard(Gtk.Box):
    def __init__(self, title: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("metric-card")

        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("metric-title")
        self.append(title_label)

        self.value_label = Gtk.Label(xalign=0)
        self.value_label.add_css_class("metric-value")
        self.append(self.value_label)

        self.sub_label = Gtk.Label(xalign=0, wrap=True)
        self.sub_label.add_css_class("metric-sub")
        self.append(self.sub_label)

    def update(self, value: str, sub: str = "") -> None:
        self.value_label.set_label(value)
        self.sub_label.set_label(sub)


# ─── Widget: App Card (Draggable) ───────────────────────────────────────────

class AppCard(Gtk.Box):
    def __init__(self, sink_input: SinkInput):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.input_id = sink_input.input_id
        self.add_css_class("app-card")

        if GAME_SINK in sink_input.sink_name:
            self.add_css_class("app-card-game")
        elif CHAT_SINK in sink_input.sink_name:
            self.add_css_class("app-card-chat")
        else:
            self.add_css_class("app-card-system")

        icon_name = get_app_icon(sink_input.app_name)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(22)
        icon.set_opacity(0.7)
        self.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        self.append(text_box)

        name_label = Gtk.Label(label=sink_input.app_name, xalign=0)
        name_label.add_css_class("app-card-name")
        text_box.append(name_label)

        sink_label = Gtk.Label(label=sink_input.sink_name, xalign=0)
        sink_label.add_css_class("app-card-sink")
        sink_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_box.append(sink_label)

        if sink_input.restricted:
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn.set_pixel_size(16)
            warn.set_tooltip_text(f"{sink_input.app_name} may override routing")
            self.append(warn)

        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare)
        drag_source.connect("drag-begin", self._on_drag_begin)
        drag_source.connect("drag-end", self._on_drag_end)
        self.add_controller(drag_source)

    def _on_drag_prepare(self, source, x, y):
        val = GObject.Value()
        val.init(GObject.TYPE_STRING)
        val.set_string(str(self.input_id))
        return Gdk.ContentProvider.new_for_value(val)

    def _on_drag_begin(self, source, drag):
        self.set_opacity(0.4)

    def _on_drag_end(self, source, drag, delete_data):
        self.set_opacity(1.0)


# ─── Widget: Kanban Column (Drop Target) ────────────────────────────────────

class KanbanColumn(Gtk.Box):
    def __init__(self, title: str, emoji: str, target_sink: str,
                 css_class: str, move_callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.target_sink = target_sink
        self.move_callback = move_callback
        self.add_css_class("kanban-column")
        self.add_css_class(css_class)
        self.set_hexpand(True)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.append(header_box)

        header_label = Gtk.Label(label=f"{emoji}  {title}", xalign=0)
        header_label.add_css_class("kanban-header")
        header_label.set_hexpand(True)
        header_box.append(header_label)

        self.count_label = Gtk.Label(label="0")
        self.count_label.add_css_class("kanban-count")
        header_box.append(self.count_label)

        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self.cards_box)

        self._empty_label = Gtk.Label(label="Drop apps here")
        self._empty_label.add_css_class("kanban-empty")
        self.cards_box.append(self._empty_label)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("drop", self._on_drop)
        drop_target.connect("enter", self._on_drag_enter)
        drop_target.connect("leave", self._on_drag_leave)
        self.add_controller(drop_target)

    def _on_drop(self, target, value, x, y):
        try:
            input_id = int(value)
        except (ValueError, TypeError):
            return False
        self.move_callback(input_id, self.target_sink)
        return True

    def _on_drag_enter(self, target, x, y):
        self.add_css_class("drag-hover")
        return Gdk.DragAction.MOVE

    def _on_drag_leave(self, target):
        self.remove_css_class("drag-hover")

    def set_cards(self, sink_inputs: List[SinkInput]) -> None:
        child = self.cards_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.cards_box.remove(child)
            child = nxt

        self.count_label.set_label(str(len(sink_inputs)))

        if not sink_inputs:
            empty = Gtk.Label(label="Drop apps here")
            empty.add_css_class("kanban-empty")
            self.cards_box.append(empty)
        else:
            for si in sink_inputs:
                card = AppCard(si)
                self.cards_box.append(card)


# ─── Main Window ─────────────────────────────────────────────────────────────

class NovaChatMixWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Nova ChatMix")
        self.set_default_size(1060, 820)

        self.toast_overlay = Adw.ToastOverlay()
        self.last_state: Optional[AppState] = None
        self.last_live_state: Optional[LiveState] = None
        self.last_state_mtime_ns: int = -1
        self._refresh_queued = False
        self._priority_order: list[str] = []
        self.set_content(self.toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Nova ChatMix"))
        toolbar_view.add_top_bar(header)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_button.connect("clicked", lambda *_: self.refresh_full())
        header.pack_end(refresh_button)

        self.service_menu = Gio.Menu()
        self.service_menu.append("Restart Service", "app.restart-service")
        self.service_menu.append("Recreate Sinks", "app.recreate-sinks")
        self.service_menu.append("Balanced Mode", "app.poll-balanced")
        self.service_menu.append("Ultra Mode", "app.poll-ultra")
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=self.service_menu
        )
        header.pack_end(menu_button)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scroller)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.main_box.set_margin_top(20)
        self.main_box.set_margin_bottom(24)
        self.main_box.set_margin_start(20)
        self.main_box.set_margin_end(20)
        scroller.set_child(self.main_box)

        self._build_hero()
        self._build_balance()
        self._build_kanban()
        self._build_engine()
        self._build_priority()
        self._build_actions()

        self._install_state_monitor()
        self.refresh_full()
        GLib.timeout_add(STATE_WATCH_MS, self._poll_live)
        GLib.timeout_add(FULL_REFRESH_MS, self._poll_full)

    # ── Hero Section ──

    def _build_hero(self) -> None:
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        hero.add_css_class("hero-card")
        self.main_box.append(hero)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hero.append(top_row)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_col.set_hexpand(True)
        top_row.append(title_col)

        eyebrow = Gtk.Label(label="STEELSERIES", xalign=0)
        eyebrow.add_css_class("eyebrow")
        title_col.append(eyebrow)

        self.hero_title = Gtk.Label(label="Arctis Nova 7", xalign=0)
        self.hero_title.add_css_class("hero-title")
        title_col.append(self.hero_title)

        self.status_pill = Gtk.Label()
        self.status_pill.add_css_class("status-pill")
        self.status_pill.set_valign(Gtk.Align.START)
        top_row.append(self.status_pill)

        self.hero_subtitle = Gtk.Label(xalign=0)
        self.hero_subtitle.add_css_class("hero-subtitle")
        self.hero_subtitle.set_wrap(True)
        hero.append(self.hero_subtitle)

        metrics_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hero.append(metrics_box)

        self.battery_card = MetricCard("BATTERY")
        self.battery_card.set_hexpand(True)
        metrics_box.append(self.battery_card)

        self.chatmix_card = MetricCard("CHATMIX WHEEL")
        self.chatmix_card.set_hexpand(True)
        metrics_box.append(self.chatmix_card)

        self.service_card = MetricCard("SERVICE")
        self.service_card.set_hexpand(True)
        metrics_box.append(self.service_card)

    # ── Balance Section ──

    def _build_balance(self) -> None:
        header = Gtk.Label(label="BALANCE", xalign=0)
        header.add_css_class("section-header")
        self.main_box.append(header)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("balance-card")
        self.main_box.append(card)

        # Game bar
        game_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.append(game_row)

        game_label = Gtk.Label(label="\U0001F3AE  Game", xalign=0)
        game_label.add_css_class("balance-label")
        game_label.set_size_request(90, -1)
        game_row.append(game_label)

        self.game_progress = Gtk.ProgressBar()
        self.game_progress.add_css_class("game-bar")
        self.game_progress.set_hexpand(True)
        self.game_progress.set_valign(Gtk.Align.CENTER)
        game_row.append(self.game_progress)

        self.game_value = Gtk.Label(label="0%", xalign=1)
        self.game_value.add_css_class("balance-value")
        self.game_value.set_size_request(48, -1)
        game_row.append(self.game_value)

        # Chat bar
        chat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.append(chat_row)

        chat_label = Gtk.Label(label="\U0001F4AC  Chat", xalign=0)
        chat_label.add_css_class("balance-label")
        chat_label.set_size_request(90, -1)
        chat_row.append(chat_label)

        self.chat_progress = Gtk.ProgressBar()
        self.chat_progress.add_css_class("chat-bar")
        self.chat_progress.set_hexpand(True)
        self.chat_progress.set_valign(Gtk.Align.CENTER)
        chat_row.append(self.chat_progress)

        self.chat_value = Gtk.Label(label="0%", xalign=1)
        self.chat_value.add_css_class("balance-value")
        self.chat_value.set_size_request(48, -1)
        chat_row.append(self.chat_value)

    # ── Kanban Board ──

    def _build_kanban(self) -> None:
        header = Gtk.Label(label="AUDIO ROUTING", xalign=0)
        header.add_css_class("section-header")
        self.main_box.append(header)

        self.kanban_board = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12
        )
        self.kanban_board.set_homogeneous(True)
        self.main_box.append(self.kanban_board)

        self.game_column = KanbanColumn(
            "Game", "\U0001F3AE", GAME_SINK,
            "kanban-column-game", self.move_sink_input
        )
        self.chat_column = KanbanColumn(
            "Chat", "\U0001F4AC", CHAT_SINK,
            "kanban-column-chat", self.move_sink_input
        )
        self.system_column = KanbanColumn(
            "System", "\U0001F50A", "__SYSTEM__",
            "kanban-column-system", self.move_sink_input
        )
        self.kanban_board.append(self.game_column)
        self.kanban_board.append(self.chat_column)
        self.kanban_board.append(self.system_column)

    # ── Engine Section ──

    def _build_engine(self) -> None:
        header = Gtk.Label(label="ENGINE", xalign=0)
        header.add_css_class("section-header")
        self.main_box.append(header)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("engine-card")
        self.main_box.append(card)

        def make_row(label_text: str) -> Gtk.Label:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("engine-row")
            lbl = Gtk.Label(label=label_text, xalign=0)
            lbl.add_css_class("engine-label")
            lbl.set_hexpand(True)
            row.append(lbl)
            val = Gtk.Label(xalign=1)
            val.add_css_class("engine-value")
            row.append(val)
            card.append(row)
            return val

        self.controller_value = make_row("Controller")
        self.profile_value = make_row("Response Mode")
        self.autostart_value = make_row("Autostart")

        log_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        log_row.add_css_class("engine-row")
        card.append(log_row)

        log_title = Gtk.Label(label="Last Log", xalign=0)
        log_title.add_css_class("engine-label")
        log_row.append(log_title)

        self.log_label = Gtk.Label(xalign=0)
        self.log_label.add_css_class("engine-value")
        self.log_label.set_wrap(True)
        self.log_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_label.set_max_width_chars(80)
        self.log_label.set_opacity(0.5)
        log_row.append(self.log_label)

    # ── Audio Priority Section ──

    def _build_priority(self) -> None:
        header = Gtk.Label(label="AUDIO PRIORITY", xalign=0)
        header.add_css_class("section-header")
        self.main_box.append(header)

        self.priority_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        self.priority_card.add_css_class("priority-card")
        self.main_box.append(self.priority_card)

        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.priority_card.append(toggle_row)

        toggle_label = Gtk.Label(
            label="Auto-switch on device change", xalign=0
        )
        toggle_label.set_hexpand(True)
        toggle_label.add_css_class("engine-label")
        toggle_row.append(toggle_label)

        self.priority_switch = Gtk.Switch()
        self.priority_switch.set_valign(Gtk.Align.CENTER)
        self.priority_switch.connect(
            "notify::active", self._on_priority_switch_changed
        )
        toggle_row.append(self.priority_switch)

        self.priority_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self.priority_card.append(self.priority_list)

        save_btn = Gtk.Button(label="Save Priority")
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("action-button")
        save_btn.connect("clicked", lambda *_: self._save_priority())
        self.priority_card.append(save_btn)

    # ── Action Buttons ──

    def _build_actions(self) -> None:
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.main_box.append(action_box)

        restart_btn = Gtk.Button(label="Restart Service")
        restart_btn.add_css_class("suggested-action")
        restart_btn.add_css_class("action-button")
        restart_btn.connect(
            "clicked", lambda *_: self.run_service_action("restart")
        )
        action_box.append(restart_btn)

        recreate_btn = Gtk.Button(label="Recreate Sinks")
        recreate_btn.add_css_class("action-button")
        recreate_btn.connect("clicked", lambda *_: self.recreate_sinks())
        action_box.append(recreate_btn)

    # ── State Monitoring ──

    def _poll_live(self) -> bool:
        try:
            mtime_ns = STATE_FILE.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if mtime_ns != self.last_state_mtime_ns:
            self.last_state_mtime_ns = mtime_ns
            self.queue_refresh()
        return True

    def _poll_full(self) -> bool:
        self.refresh_full()
        return True

    def _install_state_monitor(self) -> None:
        state_dir = Gio.File.new_for_path(str(STATE_FILE.parent))
        try:
            self.state_monitor = state_dir.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES,
                None,
            )
        except GLib.Error:
            self.state_monitor = None
            return
        self.state_monitor.connect("changed", self._on_state_changed)

    def _on_state_changed(self, _monitor, file_obj, other_file, event_type):
        paths = {
            p.get_path() for p in (file_obj, other_file) if p is not None
        }
        if str(STATE_FILE) not in paths:
            return
        if event_type in {
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.MOVED_OUT,
            Gio.FileMonitorEvent.RENAMED,
            Gio.FileMonitorEvent.ATTRIBUTE_CHANGED,
        }:
            self.queue_refresh()

    def queue_refresh(self) -> None:
        if self._refresh_queued:
            return
        self._refresh_queued = True
        GLib.timeout_add(40, self._run_queued_refresh)

    def _run_queued_refresh(self) -> bool:
        self._refresh_queued = False
        self.refresh_live()
        return False

    # ── State Application ──

    def apply_live_state(self, state: LiveState) -> None:
        self.status_pill.set_label(
            "Connected" if state.headset_connected else "Disconnected"
        )
        self.status_pill.remove_css_class("status-good")
        self.status_pill.remove_css_class("status-bad")
        self.status_pill.add_css_class(
            "status-good" if state.headset_connected else "status-bad"
        )

        if state.headset_connected:
            self.hero_subtitle.set_label(
                "Headset live \u2014 drag apps between columns to route audio"
            )
        else:
            self.hero_subtitle.set_label(
                "Headset not connected \u2014 services running in background"
            )

        if state.battery_charging:
            bat_str = "Charging"
        elif state.battery_level is not None:
            bat_str = f"{state.battery_level}%"
        else:
            bat_str = "Unknown"
        self.battery_card.update(bat_str, "Auto-shutdown disabled by service")

        if state.chatmix_raw is not None:
            self.chatmix_card.update(
                f"{state.chatmix_raw}/128",
                f"Game {state.game_volume}%  Chat {state.chat_volume}%",
            )
        else:
            self.chatmix_card.update("No data", "Wheel position unavailable")

        self.game_progress.set_fraction(state.game_volume / 100.0)
        self.chat_progress.set_fraction(state.chat_volume / 100.0)
        self.game_value.set_label(f"{state.game_volume}%")
        self.chat_value.set_label(f"{state.chat_volume}%")

    def refresh_live(self) -> None:
        state = detect_live_state()
        if state == self.last_live_state:
            return
        self.last_live_state = state
        self.apply_live_state(state)

    def refresh_full(self) -> None:
        state = detect_state()
        if state == self.last_state:
            self.refresh_live()
            return

        # Auto-switch on headset connect/disconnect
        if self.last_state is not None:
            if self.last_state.headset_connected != state.headset_connected:
                switched = apply_audio_priority()
                if switched:
                    self.show_toast("Audio switched to priority device")

        app_list_changed = (
            self.last_state is None
            or self.last_state.sink_inputs != state.sink_inputs
        )
        self.last_state = state
        self.last_live_state = LiveState(
            headset_connected=state.headset_connected,
            battery_level=state.battery_level,
            battery_charging=state.battery_charging,
            chatmix_raw=state.chatmix_raw,
            game_volume=state.game_volume,
            chat_volume=state.chat_volume,
        )
        self.apply_live_state(self.last_live_state)

        self.service_card.update(
            state.service_active.title(),
            f"Autostart {state.service_enabled}",
        )

        if app_list_changed:
            self._rebuild_kanban(state.sink_inputs)

        self.controller_value.set_label(state.controller_status)
        self.profile_value.set_label(state.poll_profile.title())
        self.autostart_value.set_label(state.service_enabled)
        self.log_label.set_label(state.last_log_line)

        self._load_priority_ui()

    def _rebuild_kanban(self, sink_inputs: List[SinkInput]) -> None:
        game_apps: List[SinkInput] = []
        chat_apps: List[SinkInput] = []
        system_apps: List[SinkInput] = []

        for si in sink_inputs:
            if GAME_SINK in si.sink_name:
                game_apps.append(si)
            elif CHAT_SINK in si.sink_name:
                chat_apps.append(si)
            else:
                system_apps.append(si)

        self.game_column.set_cards(game_apps)
        self.chat_column.set_cards(chat_apps)
        self.system_column.set_cards(system_apps)

    # ── Audio Routing ──

    def move_sink_input(self, input_id: int, target: str) -> None:
        if target == "__SYSTEM__":
            resolved_target = preferred_system_sink()
            if not resolved_target:
                self.show_toast("No physical system sink found")
                return
        else:
            resolved_target = target

        proc = subprocess.run(
            ["pactl", "move-sink-input", str(input_id), resolved_target],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            self.show_toast("Audio route updated")
        else:
            self.show_toast((proc.stderr or "Failed to move app").strip())
        self.refresh_full()

    # ── Service Management ──

    def run_service_action(self, action: str) -> None:
        proc = subprocess.run(
            ["systemctl", "--user", action, SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            self.show_toast(f"Service {action} complete")
        else:
            self.show_toast(
                (proc.stderr or f"systemctl {action} failed").strip()
            )
        self.refresh_full()

    def recreate_sinks(self) -> None:
        proc = subprocess.run(
            [str(Path.home() / ".local/bin/nova7-virtualaudio")],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            self.show_toast("Mix sinks recreated")
        else:
            self.show_toast(
                (proc.stderr or "Failed to recreate sinks").strip()
            )
        self.refresh_full()

    def set_poll_profile(self, profile: str) -> None:
        try:
            SERVICE_OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
            SERVICE_OVERRIDE_FILE.write_text(
                "[Service]\n"
                f"Environment=NOVA7_POLL_PROFILE={profile}\n"
            )
        except OSError as exc:
            self.show_toast(f"Failed to save mode: {exc}")
            return

        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=False
        )
        subprocess.run(
            ["systemctl", "--user", "restart", SERVICE_NAME], check=False
        )
        self.show_toast(f"{profile.title()} mode enabled")
        self.refresh_full()

    def show_toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))

    # ── Priority Management ──

    def _load_priority_ui(self) -> None:
        config = load_priority_config()
        self.priority_switch.handler_block_by_func(
            self._on_priority_switch_changed
        )
        self.priority_switch.set_active(config.get("enabled", False))
        self.priority_switch.handler_unblock_by_func(
            self._on_priority_switch_changed
        )
        self._refresh_priority_list(config)

    def _refresh_priority_list(self, config: Optional[dict] = None) -> None:
        if config is None:
            config = load_priority_config()

        child = self.priority_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.priority_list.remove(child)
            child = nxt

        available = get_available_sinks()
        priorities = config.get("priorities", [])
        default_sink = run_text("pactl", "get-default-sink")

        ordered: list[tuple[str, str]] = []
        for name in priorities:
            for sink_name, desc in available:
                if sink_name == name:
                    ordered.append((sink_name, desc))
                    break
        for sink_name, desc in available:
            if sink_name not in priorities:
                ordered.append((sink_name, desc))

        self._priority_order = [name for name, _ in ordered]

        if not ordered:
            empty = Gtk.Label(label="No audio output devices found")
            empty.add_css_class("kanban-empty")
            self.priority_list.append(empty)
            return

        for i, (name, desc) in enumerate(ordered):
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=10
            )
            row.add_css_class("priority-row")
            if name == default_sink:
                row.add_css_class("priority-row-active")

            num = Gtk.Label(label=str(i + 1))
            num.add_css_class("priority-number")
            row.append(num)

            text_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2
            )
            text_box.set_hexpand(True)
            row.append(text_box)

            name_label = Gtk.Label(label=desc, xalign=0)
            name_label.add_css_class("priority-name")
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.append(name_label)

            sink_label = Gtk.Label(label=name, xalign=0)
            sink_label.add_css_class("priority-desc")
            sink_label.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.append(sink_label)

            btn_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=4
            )
            row.append(btn_box)

            up_btn = Gtk.Button(icon_name="go-up-symbolic")
            up_btn.set_sensitive(i > 0)
            up_btn.connect(
                "clicked", lambda *_, idx=i: self._move_priority(idx, -1)
            )
            btn_box.append(up_btn)

            down_btn = Gtk.Button(icon_name="go-down-symbolic")
            down_btn.set_sensitive(i < len(ordered) - 1)
            down_btn.connect(
                "clicked", lambda *_, idx=i: self._move_priority(idx, 1)
            )
            btn_box.append(down_btn)

            self.priority_list.append(row)

    def _move_priority(self, index: int, direction: int) -> None:
        new_index = index + direction
        if 0 <= new_index < len(self._priority_order):
            order = self._priority_order
            order[index], order[new_index] = order[new_index], order[index]
            config = load_priority_config()
            config["priorities"] = order
            save_priority_config(config)
            self._refresh_priority_list(config)

    def _save_priority(self) -> None:
        config = {
            "enabled": self.priority_switch.get_active(),
            "priorities": self._priority_order,
        }
        save_priority_config(config)
        self.show_toast("Audio priority saved")

    def _on_priority_switch_changed(self, switch, pspec) -> None:
        config = load_priority_config()
        config["enabled"] = switch.get_active()
        save_priority_config(config)


# ─── Application ─────────────────────────────────────────────────────────────

class NovaChatMixApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window: Optional[NovaChatMixWindow] = None
        self._create_actions()

    def _create_actions(self) -> None:
        for name, callback in (
            (
                "restart-service",
                lambda *_: self.window and self.window.run_service_action(
                    "restart"
                ),
            ),
            (
                "recreate-sinks",
                lambda *_: self.window and self.window.recreate_sinks(),
            ),
            (
                "poll-balanced",
                lambda *_: self.window and self.window.set_poll_profile(
                    "balanced"
                ),
            ),
            (
                "poll-ultra",
                lambda *_: self.window and self.window.set_poll_profile(
                    "ultra"
                ),
            ),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def do_activate(self) -> None:
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK
        )
        if self.window is None:
            self.window = NovaChatMixWindow(self)
        self.window.present()


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main() -> int:
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    app = NovaChatMixApp()
    return app.run(None)


if __name__ == "__main__":
    raise SystemExit(main())
