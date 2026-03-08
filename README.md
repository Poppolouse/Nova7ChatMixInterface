# Nova7ChatMixInterface

A Linux ChatMix interface for the SteelSeries Arctis Nova 7.

It creates dedicated `GameMix` and `ChatMix` virtual sinks, reads the headset ChatMix wheel through `headsetcontrol`, and applies the wheel position to those sinks in real time. It also includes a native COSMIC panel applet for service status and quick controls.

![Nova7ChatMixInterface screenshot](docs/cosmic-desktop.png)

## Features

- Real-time ChatMix control for SteelSeries Arctis Nova 7
- Dedicated `GameMix` and `ChatMix` PipeWire sinks
- User-level `systemd` services
- Native COSMIC panel applet written in Rust
- Local install and uninstall scripts

## Components

- `mixer/nova7_mixer.py`
  Polls `headsetcontrol -m -o short` and maps the wheel position to `GameMix` and `ChatMix` sink volumes.
- `scripts/nova7-virtualaudio.sh`
  Creates the `GameMix` and `ChatMix` null sinks with `pactl`.
- `systemd/*.service`
  Starts the virtual audio layer and the mixer loop as user services.
- `applet/`
  COSMIC panel applet for status, service actions, and quick recovery actions.

## Requirements

- Linux with PipeWire and `pactl`
- `headsetcontrol` with Arctis Nova 7 support
- Correct udev permissions for HID access
- Rust toolchain (`cargo`) if you want to build the COSMIC applet locally
- COSMIC desktop if you want panel integration

Official `HeadsetControl` project:
- https://github.com/Sapd/HeadsetControl

Community project that helped confirm the Nova 7 approach:
- https://github.com/jakears93/Nova7ChatmixLinux

## Verify Headset Access

Make sure the headset is reachable before installing this repo:

```bash
headsetcontrol -b
headsetcontrol -m -o short
```

Expected result:
- `headsetcontrol -b` should detect the `SteelSeries Arctis Nova 7`
- `headsetcontrol -m -o short` should print a numeric ChatMix value

If device access fails, fix your udev rules first.

## Installation

From the repository root:

```bash
./install-local.sh
```

This installs:

- `~/.local/bin/nova7-mixer`
- `~/.local/bin/nova7-virtualaudio`
- `~/.config/systemd/user/nova7-mixer.service`
- `~/.config/systemd/user/nova7-virtualaudio.service`
- `~/.local/bin/cosmic-applet-nova-chatmix`
- `~/.local/share/applications/io.github.poppolouse.CosmicAppletNovaChatMix.desktop`

The install script also:

- reloads user `systemd`
- enables and restarts the services
- adds the applet to the COSMIC panel configuration

## Usage

Route your applications like this:

- game, browser, and general system audio -> `GameMix`
- voice chat apps such as Discord -> `ChatMix`

Then use the physical ChatMix wheel on the headset to shift balance between the two sinks.

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

## Uninstall

```bash
./uninstall-local.sh
```

## Notes

- This project targets the SteelSeries Arctis Nova 7, not the Nova Pro Wireless.
- The working install on this machine uses user-local paths so it does not depend on `/usr/local`.
- The COSMIC applet is optional. The audio services can run without it.
