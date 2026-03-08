# Nove7ChatMixInterface

Arctis Nova 7 için Linux ChatMix çözümü.

Bu repo dört parçadan oluşur:

- `mixer/nova7_mixer.py`: `headsetcontrol` ile kulaklıktaki chatmix değerini okur ve `GameMix` / `ChatMix` sink seslerini ayarlar.
- `scripts/nova7-virtualaudio.sh`: PipeWire üzerinde `GameMix` ve `ChatMix` sanal sink'lerini oluşturur.
- `systemd/*.service`: kullanıcı servisleri.
- `applet/`: COSMIC panel için native Rust applet.

## Gereksinimler

- Linux
- PipeWire
- `pactl`
- `headsetcontrol`
- COSMIC applet için `cargo` / Rust toolchain
- `headsetcontrol` erişimi için udev kuralı

`headsetcontrol` resmi proje:
- https://github.com/Sapd/HeadsetControl

Nova 7 taban fikri alınan topluluk projesi:
- https://github.com/jakears93/Nova7ChatmixLinux

## Kurulum

`headsetcontrol` sisteminde çalışıyor olmalı:

```bash
headsetcontrol -b
headsetcontrol -m -o short
```

Sonra bu repo içinden:

```bash
./install-local.sh
```

Bu şunları kurar:

- `~/.local/bin/nova7-mixer`
- `~/.local/bin/nova7-virtualaudio`
- `~/.config/systemd/user/nova7-mixer.service`
- `~/.config/systemd/user/nova7-virtualaudio.service`
- `~/.local/bin/cosmic-applet-nova-chatmix`
- `~/.local/share/applications/io.github.poppolouse.CosmicAppletNovaChatMix.desktop`

## Kullanım

Ses uygulamalarını şu sink'lere yönlendir:

- oyun / genel ses: `GameMix`
- sohbet uygulamaları: `ChatMix`

Kulaklıktaki chatmix tekeri bu iki sink arasındaki dengeyi değiştirir.

## Kaldırma

```bash
./uninstall-local.sh
```
