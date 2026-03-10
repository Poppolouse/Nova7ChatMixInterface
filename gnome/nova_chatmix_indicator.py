#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import GLib, Gtk


STATE_FILE = Path.home() / ".local/state/nova7-chatmix/status.json"
APP_BINARY = str(Path.home() / ".local/bin/nova-chatmix-gnome")
SERVICE_NAME = "nova7-mixer.service"


class Indicator:
    def __init__(self):
        self.indicator = AppIndicator3.Indicator.new(
            "nova-chatmix-indicator",
            "audio-headphones-symbolic",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(self._build_menu())
        GLib.timeout_add_seconds(3, self.refresh)
        self.refresh()

    def _build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Nova ChatMix")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        menu.append(Gtk.SeparatorMenuItem())

        open_item = Gtk.MenuItem(label="Open App")
        open_item.connect("activate", self.open_app)
        menu.append(open_item)

        restart_item = Gtk.MenuItem(label="Restart Service")
        restart_item.connect("activate", self.restart_service)
        menu.append(restart_item)

        quit_item = Gtk.MenuItem(label="Quit Tray")
        quit_item.connect("activate", self.quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def refresh(self):
        connected = False
        battery = None
        try:
            data = json.loads(STATE_FILE.read_text())
            connected = bool(data.get("headset_connected"))
            battery = data.get("battery_level")
        except Exception:
            pass

        active = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

        if connected:
            self.indicator.set_icon_full("audio-headphones-symbolic", "Nova ChatMix connected")
            suffix = f" • {battery}%" if isinstance(battery, int) else ""
            self.status_item.set_label(f"Connected{suffix}")
        else:
            self.indicator.set_icon_full("audio-card-symbolic", "Nova ChatMix disconnected")
            self.status_item.set_label(f"{active or 'unknown'}")

        return True

    def open_app(self, *_args):
        subprocess.Popen([APP_BINARY])

    def restart_service(self, *_args):
        subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=False)
        self.refresh()

    def quit(self, *_args):
        Gtk.main_quit()


def main():
    Indicator()
    Gtk.main()


if __name__ == "__main__":
    main()
