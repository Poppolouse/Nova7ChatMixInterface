#!/usr/bin/env python3
import subprocess
import time

POLL_SECONDS = 1
GAME_SINK = "GameMix"
CHAT_SINK = "ChatMix"


def run_checked(*args: str) -> str:
    proc = subprocess.run(args, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(args)}")
    return proc.stdout.strip()


def current_chatmix() -> int | None:
    proc = subprocess.run(["headsetcontrol", "-m", "-o", "short"], text=True, capture_output=True)
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


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
    subprocess.run(["pactl", "set-sink-volume", sink, f"{percent}%"], check=False)


def main() -> None:
    last_mix = None
    while True:
        mix = current_chatmix()
        if mix is None:
            time.sleep(POLL_SECONDS)
            continue

        if mix != last_mix:
            game, chat = mix_to_volumes(mix)
            set_sink_volume(GAME_SINK, game)
            set_sink_volume(CHAT_SINK, chat)
            last_mix = mix

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
