"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .app import SatelliteApp
from .config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-satellite",
        description="Voice satellite bridging wake word detection to Hermes.",
    )
    parser.add_argument(
        "--config", "-c", default="config.yaml", help="path to config.yaml"
    )
    parser.add_argument(
        "--log-level", default=None,
        help="logging level (DEBUG, INFO, WARNING, ERROR); "
             "overrides log_level from config.yaml",
    )
    parser.add_argument(
        "--hardware-profile",
        default=None,
        help="override config hardware_profile (pi4-respeaker-v1 | pi5-respeaker-v2 | mock)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="run with mock wakeword/audio/stt/tts to exercise the state machine and LEDs",
    )
    parser.add_argument(
        "--ww-monitor", action="store_true",
        help="wake word tuning mode: stream live detection scores from the mic "
             "to calibrate wakeword.threshold / patience (openwakeword only)",
    )

    sub = parser.add_subparsers(dest="command")
    voices = sub.add_parser(
        "voices", help="browse and audition piper TTS voices on this device"
    )
    voices.add_argument("action", choices=["list", "preview"])
    voices.add_argument("name", nargs="?", help="voice name (for preview)")
    voices.add_argument(
        "--language", default=None,
        help="filter list by language code, e.g. en_GB",
    )
    voices.add_argument(
        "--speaker", type=int, default=None,
        help="speaker id for multi-speaker voices (vctk, aru)",
    )
    voices.add_argument(
        "--text",
        default="Good evening. All systems are operating within normal parameters.",
        help="phrase to speak in preview",
    )

    setup = sub.add_parser(
        "setup",
        help="start the temporary token-protected setup wizard (web UI); "
             "exits on idle or via the Exit button — no resident ports",
    )
    setup.add_argument("--port", type=int, default=8321)
    setup.add_argument(
        "--idle-timeout-min", type=float, default=15.0,
        help="auto-exit after this many idle minutes",
    )
    return parser


def _run_ww_monitor(config) -> int:
    """Stream live wake word scores for threshold calibration.

    Say the wake phrase at realistic distances and let the room be normally
    noisy; pick a threshold comfortably below your spoken scores and above the
    ambient ones. Ctrl-C to exit.
    """
    from .audio.mic import MicStream
    from .wakeword import build_wakeword

    mic = MicStream(
        sample_rate=config.audio.sample_rate,
        device=config.audio.input_device,
        channels=config.audio.input_channels,
    )
    detector = build_wakeword(config, demo=False, mic=mic)
    if not hasattr(detector, "on_score"):
        print(
            "--ww-monitor requires wakeword.backend: openwakeword",
            file=sys.stderr,
        )
        return 2

    threshold = config.wakeword.threshold
    print(f"wake word monitor: {config.wakeword.model_path} "
          f"(threshold {threshold}) — Ctrl-C to exit", flush=True)
    print("status line shows mic level + best score each second; "
          "if 'mic' stays at 0% while you talk, no audio is arriving", flush=True)

    state = {"last": time.monotonic(), "peak_rms": 0.0, "best": 0.0}

    def meter(pcm: bytes) -> None:
        import array
        import math

        samples = array.array("h", pcm)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32767
        state["peak_rms"] = max(state["peak_rms"], rms)
        now = time.monotonic()
        if now - state["last"] >= 1.0:
            level = int(state["peak_rms"] * 100)
            print(f"{time.strftime('%H:%M:%S')}  mic {level:3d}% "
                  f"{'▮' * min(level // 3, 25):25s} best-score {state['best']:0.3f}",
                  flush=True)
            state.update(last=now, peak_rms=0.0, best=0.0)

    def show(predictions: dict) -> None:
        score = max(predictions.values())
        state["best"] = max(state["best"], score)
        if score >= 0.05:
            bar = "#" * int(score * 40)
            marker = "  <<<" if score >= threshold else ""
            print(f"{time.strftime('%H:%M:%S')}  {score:0.3f} {bar}{marker}", flush=True)

    detector.on_score = show
    detector.on_audio = meter
    try:
        while True:
            if detector.wait_for_wake(lambda: False):
                print(f"{time.strftime('%H:%M:%S')}  *** DETECTED ***", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        detector.stop()
        mic.close()
    return 0


def _run_voices(config, args) -> int:
    """``voices list`` / ``voices preview`` — browse and audition on-device."""
    import dataclasses
    import json
    from pathlib import Path
    from urllib.request import urlopen

    if args.action == "list":
        from piper.download_voices import VOICES_JSON

        with urlopen(VOICES_JSON) as response:
            catalog = json.load(response)
        voices_dir = Path(config.tts.voices_dir)
        shown = 0
        for name in sorted(catalog):
            if args.language and not name.startswith(args.language):
                continue
            entry = catalog[name]
            speakers = entry.get("num_speakers", 1)
            marks = []
            if speakers > 1:
                marks.append(f"{speakers} speakers")
            if (voices_dir / f"{name}.onnx").exists():
                marks.append("downloaded")
            suffix = f"   ({', '.join(marks)})" if marks else ""
            print(f"{name}{suffix}")
            shown += 1
        if not shown:
            print(f"no voices match language {args.language!r}", file=sys.stderr)
        return 0

    # preview
    name = args.name or config.tts.voice
    if not name:
        print("voices preview needs a voice name (see: voices list)", file=sys.stderr)
        return 2
    from .audio import build_audio
    from .tts.piper_backend import PiperTTS

    tts_cfg = dataclasses.replace(
        config.tts, voice=name, voice_path="",
        speaker_id=args.speaker if args.speaker is not None else config.tts.speaker_id,
    )
    tts = PiperTTS(tts_cfg, sample_rate=config.audio.sample_rate)
    print(f"synthesizing with {name}"
          + (f" speaker {tts_cfg.speaker_id}" if tts_cfg.speaker_id is not None else "")
          + " ...", flush=True)
    pcm = tts.synthesize(args.text)
    _, sink = build_audio(config)
    sink.play(pcm, tts.sample_rate)
    print(f"({tts.sample_rate} Hz — to keep it, set in config.yaml:  "
          f"tts.voice: {name}"
          + (f"  tts.speaker_id: {tts_cfg.speaker_id}"
             if tts_cfg.speaker_id is not None else "")
          + ")")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config, profile_override=args.hardware_profile)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    # CLI flag overrides config; config defaults to INFO.
    level = (args.log_level or config.log_level).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if getattr(args, "command", None) == "voices":
        return _run_voices(config, args)

    if getattr(args, "command", None) == "setup":
        from .wizard import run_wizard

        return run_wizard(
            config, args.config,
            port=args.port, idle_timeout_s=args.idle_timeout_min * 60,
        )

    if args.ww_monitor:
        return _run_ww_monitor(config)

    app = SatelliteApp.from_config(config, demo=args.demo)
    try:
        app.run()
    except KeyboardInterrupt:  # pragma: no cover
        app.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
