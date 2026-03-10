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

from gi.repository import Adw, Gdk, Gio, GLib, Gtk


APP_ID = "io.github.poppolouse.NovaChatMix"
SERVICE_NAME = "nova7-mixer.service"
STATE_FILE = Path.home() / ".local/state/nova7-chatmix/status.json"
SERVICE_OVERRIDE_DIR = Path.home() / ".config/systemd/user/nova7-mixer.service.d"
SERVICE_OVERRIDE_FILE = SERVICE_OVERRIDE_DIR / "override.conf"
GAME_SINK = "GameMix"
CHAT_SINK = "ChatMix"
STATE_WATCH_MS = 75
FULL_REFRESH_MS = 5000
STATE_FILE_MAX_AGE_SECONDS = 10


CSS = b"""
.hero-card {
  background: linear-gradient(135deg, rgba(17, 24, 39, 0.98), rgba(31, 41, 55, 0.94));
  color: white;
  border-radius: 24px;
  padding: 20px;
}

.hero-title {
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.eyebrow {
  font-size: 11px;
  font-weight: 700;
  opacity: 0.72;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.status-pill {
  border-radius: 999px;
  padding: 6px 12px;
  font-weight: 700;
}

.status-good {
  background: rgba(52, 211, 153, 0.16);
  color: #b7f7d8;
}

.status-bad {
  background: rgba(248, 113, 113, 0.18);
  color: #fecaca;
}

.metric-card {
  background: alpha(@window_fg_color, 0.04);
  border-radius: 18px;
  padding: 16px;
}

.metric-title {
  font-size: 12px;
  font-weight: 700;
  opacity: 0.72;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.metric-value {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.03em;
}

.app-row {
  background: alpha(@window_fg_color, 0.03);
  border-radius: 16px;
  padding: 14px;
}

.caption {
  opacity: 0.72;
}
"""


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


RESTRICTED_APPS = ("discord", "teams", "zoom", "slack")


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

    def flush() -> None:
        nonlocal current_id, current_sink, current_app
        if current_id is None or current_app is None:
            return
        sink_name = sink_map.get(current_sink or "", f"sink#{current_sink or '?'}")
        app_lower = current_app.lower()
        items.append(
            SinkInput(
                input_id=current_id,
                app_name=current_app,
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
        elif line.startswith("Sink:"):
            current_sink = line.removeprefix("Sink:").strip()
        elif line.startswith("application.name"):
            _, _, value = line.partition("=")
            current_app = value.strip().strip('"')

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
        self.sub_label.add_css_class("caption")
        self.append(self.sub_label)

    def update(self, value: str, sub: str = "") -> None:
        self.value_label.set_label(value)
        self.sub_label.set_label(sub)


class SinkInputRow(Gtk.Box):
    __gtype_name__ = "NovaChatMixSinkInputRow"

    def __init__(self, move_callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("app-row")
        self.move_callback = move_callback

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.append(header)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)
        header.append(title_box)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.set_wrap(True)
        title_box.append(self.title_label)

        self.subtitle_label = Gtk.Label(xalign=0)
        self.subtitle_label.add_css_class("caption")
        title_box.append(self.subtitle_label)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(action_box)

        self.game_button = Gtk.Button(label="Game")
        self.chat_button = Gtk.Button(label="Chat")
        self.system_button = Gtk.Button(label="System")
        action_box.append(self.game_button)
        action_box.append(self.chat_button)
        action_box.append(self.system_button)

        self.warning_label = Gtk.Label(xalign=0)
        self.warning_label.add_css_class("caption")
        self.warning_label.set_wrap(True)
        self.append(self.warning_label)

    def bind(self, sink_input: SinkInput) -> None:
        self.title_label.set_label(sink_input.app_name)
        self.subtitle_label.set_label(f"Current sink: {sink_input.sink_name}")

        self.game_button.connect(
            "clicked", lambda *_: self.move_callback(sink_input.input_id, GAME_SINK)
        )
        self.chat_button.connect(
            "clicked", lambda *_: self.move_callback(sink_input.input_id, CHAT_SINK)
        )
        self.system_button.connect(
            "clicked", lambda *_: self.move_callback(sink_input.input_id, "__SYSTEM__")
        )

        on_game = GAME_SINK in sink_input.sink_name
        on_chat = CHAT_SINK in sink_input.sink_name
        on_system = not on_game and not on_chat
        self.game_button.set_sensitive(not on_game)
        self.chat_button.set_sensitive(not on_chat)
        self.system_button.set_sensitive(not on_system)

        if sink_input.restricted:
            self.warning_label.set_label(
                f"{sink_input.app_name} may override audio routing on its own."
            )
            self.warning_label.set_visible(True)
        else:
            self.warning_label.set_visible(False)


class NovaChatMixWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Nova ChatMix")
        self.set_default_size(980, 760)

        self.toast_overlay = Adw.ToastOverlay()
        self.last_state: Optional[AppState] = None
        self.last_live_state: Optional[LiveState] = None
        self.last_state_mtime_ns: int = -1
        self._refresh_queued = False
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
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self.service_menu)
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

        self.build_ui()
        self._install_state_monitor()
        self.refresh_full()
        GLib.timeout_add(STATE_WATCH_MS, self._poll_live)
        GLib.timeout_add(FULL_REFRESH_MS, self._poll_full)

    def build_ui(self) -> None:
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        hero.add_css_class("hero-card")
        self.main_box.append(hero)

        top_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hero.append(top_line)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_box.set_hexpand(True)
        top_line.append(title_box)

        eyebrow = Gtk.Label(label="SteelSeries Nova 7", xalign=0)
        eyebrow.add_css_class("eyebrow")
        title_box.append(eyebrow)

        self.hero_title = Gtk.Label(label="Live ChatMix Control", xalign=0)
        self.hero_title.add_css_class("hero-title")
        title_box.append(self.hero_title)

        self.hero_subtitle = Gtk.Label(xalign=0)
        self.hero_subtitle.set_wrap(True)
        hero.append(self.hero_subtitle)

        self.status_pill = Gtk.Label()
        self.status_pill.add_css_class("status-pill")
        top_line.append(self.status_pill)

        metric_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        hero.append(metric_grid)

        self.battery_card = MetricCard("Battery")
        self.chatmix_card = MetricCard("ChatMix")
        self.service_card = MetricCard("Service")
        metric_grid.attach(self.battery_card, 0, 0, 1, 1)
        metric_grid.attach(self.chatmix_card, 1, 0, 1, 1)
        metric_grid.attach(self.service_card, 2, 0, 1, 1)

        balance_group = Adw.PreferencesGroup(title="Balance")
        self.main_box.append(balance_group)

        self.game_row = Adw.ActionRow(title="GameMix")
        self.game_progress = Gtk.ProgressBar()
        self.game_progress.set_hexpand(True)
        self.game_row.add_suffix(self.game_progress)
        self.game_row.set_activatable(False)
        balance_group.add(self.game_row)

        self.chat_row = Adw.ActionRow(title="ChatMix")
        self.chat_progress = Gtk.ProgressBar()
        self.chat_progress.set_hexpand(True)
        self.chat_row.add_suffix(self.chat_progress)
        self.chat_row.set_activatable(False)
        balance_group.add(self.chat_row)

        self.apps_group = Adw.PreferencesGroup(title="Audio Routing")
        self.main_box.append(self.apps_group)

        self.apps_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.apps_group.add(self.apps_list)

        self.empty_apps_row = Adw.ActionRow(
            title="No active audio apps",
            subtitle="Start playback in Discord, browser, game, or media app to route it here.",
        )
        self.empty_apps_row.set_activatable(False)
        self.apps_list.append(self.empty_apps_row)

        status_group = Adw.PreferencesGroup(title="Engine")
        self.main_box.append(status_group)

        self.controller_row = Adw.ActionRow(title="Controller")
        self.controller_value = Gtk.Label(xalign=1)
        self.controller_row.add_suffix(self.controller_value)
        self.controller_row.set_activatable(False)
        status_group.add(self.controller_row)

        self.profile_row = Adw.ActionRow(title="Response Mode")
        self.profile_value = Gtk.Label(xalign=1)
        self.profile_row.add_suffix(self.profile_value)
        self.profile_row.set_activatable(False)
        status_group.add(self.profile_row)

        self.autostart_row = Adw.ActionRow(title="Autostart")
        self.autostart_value = Gtk.Label(xalign=1)
        self.autostart_row.add_suffix(self.autostart_value)
        self.autostart_row.set_activatable(False)
        status_group.add(self.autostart_row)

        self.log_row = Adw.ActionRow(title="Last log line")
        self.log_label = Gtk.Label(xalign=1)
        self.log_label.set_wrap(True)
        self.log_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_label.set_max_width_chars(48)
        self.log_row.add_suffix(self.log_label)
        self.log_row.set_activatable(False)
        status_group.add(self.log_row)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.main_box.append(action_box)

        restart_btn = Gtk.Button(label="Restart Service")
        restart_btn.add_css_class("suggested-action")
        restart_btn.connect("clicked", lambda *_: self.run_service_action("restart"))
        action_box.append(restart_btn)

        recreate_btn = Gtk.Button(label="Recreate Sinks")
        recreate_btn.connect("clicked", lambda *_: self.recreate_sinks())
        action_box.append(recreate_btn)

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

    def _on_state_changed(self, _monitor, file_obj, other_file, event_type) -> None:
        paths = {p.get_path() for p in (file_obj, other_file) if p is not None}
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

    def clear_app_rows(self) -> None:
        child = self.apps_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.apps_list.remove(child)
            child = nxt

    def apply_live_state(self, state: LiveState) -> None:
        self.status_pill.set_label("Connected" if state.headset_connected else "Disconnected")
        self.status_pill.remove_css_class("status-good")
        self.status_pill.remove_css_class("status-bad")
        self.status_pill.add_css_class("status-good" if state.headset_connected else "status-bad")

        if state.headset_connected:
            self.hero_subtitle.set_label(
                "Headset is live. Route Discord and voice apps to ChatMix, everything else to GameMix."
            )
        else:
            self.hero_subtitle.set_label(
                "Headset is not reporting live data right now. Services are still ready in the background."
            )

        battery_value = "Charging" if state.battery_charging else (
            f"{state.battery_level}%" if state.battery_level is not None else "Unknown"
        )
        battery_sub = "Auto-shutdown disabled by mixer service"
        self.battery_card.update(battery_value, battery_sub)

        if state.chatmix_raw is not None:
            self.chatmix_card.update(f"{state.chatmix_raw}/128", f"Game {state.game_volume}%  Chat {state.chat_volume}%")
        else:
            self.chatmix_card.update("No data", "Wheel value not currently available")

        self.game_progress.set_fraction(state.game_volume / 100.0)
        self.chat_progress.set_fraction(state.chat_volume / 100.0)
        self.game_row.set_subtitle(f"{state.game_volume}%")
        self.chat_row.set_subtitle(f"{state.chat_volume}%")

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
        app_list_changed = self.last_state is None or self.last_state.sink_inputs != state.sink_inputs
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

        self.service_card.update(state.service_active.title(), f"Autostart {state.service_enabled}")

        if app_list_changed:
            self.clear_app_rows()
            if not state.sink_inputs:
                self.apps_list.append(self.empty_apps_row)
            else:
                for sink_input in state.sink_inputs:
                    row = SinkInputRow(self.move_sink_input)
                    row.bind(sink_input)
                    self.apps_list.append(row)

        self.controller_value.set_label(state.controller_status)
        self.profile_value.set_label(state.poll_profile.title())
        self.autostart_value.set_label(state.service_enabled)
        self.log_label.set_label(state.last_log_line)

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
            self.show_toast((proc.stderr or f"systemctl {action} failed").strip())
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
            self.show_toast((proc.stderr or "Failed to recreate sinks").strip())
        self.refresh_full()

    def show_toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))

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

        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=False)
        self.show_toast(f"{profile.title()} mode enabled")
        self.refresh_full()


class NovaChatMixApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: Optional[NovaChatMixWindow] = None
        self._create_actions()

    def _create_actions(self) -> None:
        for name, callback in (
            ("restart-service", lambda *_: self.window and self.window.run_service_action("restart")),
            ("recreate-sinks", lambda *_: self.window and self.window.recreate_sinks()),
            ("poll-balanced", lambda *_: self.window and self.window.set_poll_profile("balanced")),
            ("poll-ultra", lambda *_: self.window and self.window.set_poll_profile("ultra")),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = NovaChatMixWindow(self)
        self.window.present()


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
