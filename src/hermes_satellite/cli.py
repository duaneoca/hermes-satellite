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
        "--log-level", default="INFO",
        help="logging level (DEBUG, INFO, WARNING, ERROR)",
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


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config, profile_override=args.hardware_profile)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

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
