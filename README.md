# Nova7ChatMixInterface

A Linux ChatMix interface for the SteelSeries Arctis Nova 7.

It creates dedicated `GameMix` and `ChatMix` virtual sinks, reads the headset ChatMix wheel through `headsetcontrol`, and applies the wheel position to those sinks in real time. A native COSMIC panel applet provides battery monitoring, ChatMix balance visualization, and per-app audio routing.

![Nova7ChatMixInterface screenshot](docs/cosmic-desktop.png)

## Features

- **Real-time ChatMix control** — reads the headset wheel and adjusts `GameMix` / `ChatMix` sink volumes
- **Battery monitoring** — polls battery level and charging status, displayed in the panel applet
- **ChatMix balance visualization** — progress bars showing Game% / Chat% with wheel position
- **Per-app audio routing** — list running audio apps in the applet and move them between `GameMix` and `ChatMix` sinks
- **App restriction warnings** — warns when moving apps known to manage their own audio (Discord, Teams, Zoom, Slack)
- **Dynamic panel icon** — headphones icon when connected, audio-card icon when disconnected
- **Live connection detection** — the applet treats the headset as connected only when `headsetcontrol` can read live data, not merely when the USB dongle is plugged in
- **State file** — the mixer writes live status to `~/.local/state/nova7-chatmix/status.json` (atomic writes) for the applet and external tools
- **Structured logging** — configurable via the `NOVA7_LOG_LEVEL` environment variable
- **Resilient reconnection** — exponential backoff when the headset disconnects, automatic recovery on reconnect
- **Signal handling** — clean shutdown on `SIGTERM` / `SIGINT`
- Dedicated `GameMix` and `ChatMix` PipeWire sinks
- User-level `systemd` services with automatic restart
- Local install and uninstall scripts

## Components

| Path | Description |
|------|-------------|
| `scripts/install-headsetcontrol.sh` | Downloads and builds the pinned official `HeadsetControl 3.1.0` release into `~/.local/bin/`. |
| `mixer/nova7_mixer.py` | Polls `headsetcontrol` for ChatMix wheel position and battery level; maps the wheel to sink volumes; writes state to `~/.local/state/nova7-chatmix/status.json`. |
| `scripts/nova7-virtualaudio.sh` | Creates the `GameMix` and `ChatMix` null sinks with `pactl`. |
| `systemd/*.service` | Starts the virtual audio layer and the mixer loop as user services. |
| `applet/` | COSMIC panel applet — battery indicator, ChatMix balance bars, per-app audio routing, service controls. |

## Requirements

- Linux with PipeWire and `pactl`
- Network access during install, to fetch and build the pinned `HeadsetControl 3.1.0` source release
- Correct udev permissions for HID access
- Rust toolchain (`cargo`) to build the COSMIC applet
- COSMIC desktop for panel integration (the audio services work without it)

Official `HeadsetControl` project:
- https://github.com/Sapd/HeadsetControl

Community project that helped confirm the Nova 7 approach:
- https://github.com/jakears93/Nova7ChatmixLinux

## Verify Headset Access

Make sure the headset is reachable before installing:

```bash
headsetcontrol -b           # should detect "SteelSeries Arctis Nova 7"
headsetcontrol -b -o short  # battery level (integer percentage)
headsetcontrol -m -o short  # ChatMix wheel value (integer)
```

If device access fails, fix your udev rules first.

## Installation

From the repository root:

```bash
./install-local.sh
```

This installs:

| File | Destination |
|------|-------------|
| HeadsetControl 3.1.0 binary | `~/.local/bin/headsetcontrol.bin` |
| HeadsetControl wrapper | `~/.local/bin/headsetcontrol` |
| Mixer script | `~/.local/bin/nova7-mixer` |
| Virtual audio script | `~/.local/bin/nova7-virtualaudio` |
| Mixer service | `~/.config/systemd/user/nova7-mixer.service` |
| Virtual audio service | `~/.config/systemd/user/nova7-virtualaudio.service` |
| COSMIC applet binary | `~/.local/bin/cosmic-applet-nova-chatmix` |
| Desktop entry | `~/.local/share/applications/io.github.poppolouse.CosmicAppletNovaChatMix.desktop` |

The install script also:

- Downloads and builds the pinned official `HeadsetControl 3.1.0` release into `~/.local/bin/`
- Creates the state directory `~/.local/state/nova7-chatmix/`
- Reloads user `systemd`
- Enables and restarts the services
- Adds the applet to the COSMIC panel configuration

## Usage

Route your applications like this:

- Game, browser, and general system audio → `GameMix`
- Voice chat apps such as Discord → `ChatMix`

Then use the physical ChatMix wheel on the headset to shift balance between the two sinks.

### Per-App Routing via the Applet

The COSMIC applet lists all running audio applications. Click an app to move it between the `GameMix` and `ChatMix` sinks. Apps known to manage their own audio routing (Discord, Teams, Zoom, Slack) will show a warning before being moved.

### State File

The mixer writes live status to:

```
~/.local/state/nova7-chatmix/status.json
```

Example contents:

```json
{
  "chatmix_level": 64,
  "game_volume": 100,
  "chat_volume": 100,
  "battery_level": 85,
  "battery_charging": false,
  "headset_connected": true,
  "timestamp": "2025-01-15T12:00:00+00:00"
}
```

The applet reads this file for live data. External scripts or tools can also consume it.

## Configuration

### `NOVA7_LOG_LEVEL`

Set the log verbosity for the mixer. Accepts standard Python log levels:

```bash
# In your shell or systemd override
export NOVA7_LOG_LEVEL=DEBUG   # DEBUG, INFO (default), WARNING, ERROR, CRITICAL
```

To set it permanently for the service, create a systemd override:

```bash
systemctl --user edit nova7-mixer.service
```

Then add:

```ini
[Service]
Environment=NOVA7_LOG_LEVEL=DEBUG
```

## Service Management

Check service state:

```bash
systemctl --user status nova7-virtualaudio.service
systemctl --user status nova7-mixer.service
```

Restart both services:

```bash
systemctl --user restart nova7-virtualaudio.service nova7-mixer.service
```

View mixer logs:

```bash
journalctl --user -u nova7-mixer.service -f
```

## Uninstall

```bash
./uninstall-local.sh
```

## Notes

- This project targets the SteelSeries Arctis Nova 7, not the Nova Pro Wireless.
- The install uses user-local paths (`~/.local/`) — no root permissions required.
- The COSMIC applet is optional. The audio services and state file work without it.
- The applet polls live data every 3 seconds and battery data every 30 seconds.
