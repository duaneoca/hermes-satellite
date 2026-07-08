"""Wizard HTTP server: stdlib-only, JSON API + one embedded page.

Endpoints (all require the one-time token via ``?token=`` or
``X-Setup-Token``):

  GET  /                      the page
  GET  /api/status            doctor checks
  GET  /api/audio/devices     sounddevice list + current selection
  POST /api/audio/select      {input_device, output_device, input_channels}
  POST /api/audio/tone        play a 1 s test tone on the selected output
  POST /api/meter/start|stop  live mic level capture
  GET  /api/meter             {rms_pct, p99_pct}
  POST /api/wake/start|stop   live wake-word score monitor
  GET  /api/wake              {best, last, detections}
  POST /api/wake/config       {threshold}
  GET  /api/voices            downloaded + catalog voices
  POST /api/voices/preview    {name, speaker_id, length_scale, text}
  GET  /api/pending           accumulated changes
  POST /api/hermes/test       {host, port, api_key, session_key} -> health+chat
  POST /api/save              write <config>.new; returns paths + mv command
  POST /api/exit              shut the wizard down
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .page import PAGE_HTML

logger = logging.getLogger(__name__)


class _Meter:
    """Background mic capture computing a live level."""

    def __init__(self, config):
        self._config = config
        self._thread = None
        self._stop = threading.Event()
        self.rms_pct = 0.0
        self.p99_pct = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        import array
        import math

        from ..audio.mic import MicStream

        audio = self._config.audio
        mic = MicStream(sample_rate=audio.sample_rate,
                        device=audio.input_device,
                        channels=audio.input_channels)
        try:
            mic.start()
            window = []
            while not self._stop.is_set():
                pcm = mic.read(audio.sample_rate // 10)  # 100 ms
                samples = array.array("h", pcm)
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                self.rms_pct = round(100 * rms / 32767, 1)
                window.extend(abs(s) for s in samples)
                if len(window) > audio.sample_rate * 3:  # rolling ~3 s
                    window = window[-audio.sample_rate * 3:]
                    ordered = sorted(window)
                    self.p99_pct = round(
                        100 * ordered[int(len(ordered) * 0.99)] / 32767, 1
                    )
        except Exception as exc:
            logger.error("meter failed: %s", exc)
            self.rms_pct = self.p99_pct = -1.0
        finally:
            mic.close()


class _WakeMonitor:
    """Background wake-word scorer (same backend the daemon runs)."""

    def __init__(self, config):
        self._config = config
        self._thread = None
        self._detector = None
        self.best = 0.0
        self.last = 0.0
        self.detections = 0
        self.error = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.best = self.last = 0.0
        self.detections = 0
        self.error = ""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._detector is not None:
            self._detector.stop()

    def _run(self):
        try:
            from ..audio.mic import MicStream
            from ..wakeword import build_wakeword

            audio = self._config.audio
            mic = MicStream(sample_rate=audio.sample_rate,
                            device=audio.input_device,
                            channels=audio.input_channels)
            detector = build_wakeword(self._config, demo=False, mic=mic)
            self._detector = detector

            def on_score(predictions):
                score = max(predictions.values())
                self.last = round(float(score), 3)
                self.best = max(self.best, self.last)

            detector.on_score = on_score
            try:
                while detector.wait_for_wake(lambda: False):
                    self.detections += 1
            finally:
                mic.close()
        except Exception as exc:
            logger.error("wake monitor failed: %s", exc)
            self.error = str(exc)


class WizardState:
    def __init__(self, config, config_path: str, idle_timeout_s: float = 900.0):
        self.config = config
        self.config_path = config_path
        self.token = secrets.token_urlsafe(16)
        self.pending: dict = {}  # "section.field" -> value
        self.meter = _Meter(config)
        self.wake = _WakeMonitor(config)
        self.last_request = time.monotonic()
        self.idle_timeout_s = idle_timeout_s
        self.shutdown_event = threading.Event()

    # -- change tracking -------------------------------------------------------
    def set_pending(self, section: str, field: str, value):
        sect = getattr(self.config, section)
        setattr(sect, field, value)  # apply live so tests/preview use it
        self.pending[f"{section}.{field}"] = value

    # -- doctor -----------------------------------------------------------------
    def status(self) -> dict:
        cfg = self.config
        checks = {}
        checks["profile"] = cfg.hardware_profile
        spidev = Path(f"/dev/spidev{cfg.leds.spi_bus}.{cfg.leds.spi_device}")
        checks["spidev"] = spidev.exists() or f"missing {spidev}"
        try:
            import onnxruntime
            ver = onnxruntime.__version__
            checks["onnxruntime"] = ver if ver < "1.27" else f"{ver} — BROKEN, pin <1.27"
        except ImportError:
            checks["onnxruntime"] = "not installed"
        voices = sorted(p.stem for p in Path(cfg.tts.voices_dir).glob("*.onnx")) \
            if Path(cfg.tts.voices_dir).exists() else []
        checks["voices_downloaded"] = voices or "none"
        try:
            import requests
            r = requests.get(
                f"http://{cfg.hermes.host}:{cfg.hermes.port}/health", timeout=3
            )
            checks["hermes_health"] = f"HTTP {r.status_code}"
        except Exception as exc:
            checks["hermes_health"] = f"unreachable ({type(exc).__name__})"
        return checks

    # -- save --------------------------------------------------------------------
    def save(self) -> dict:
        cfg = self.config
        data = {
            "hardware_profile": cfg.hardware_profile,
            "log_level": cfg.log_level,
            "data_dir": cfg.data_dir,
        }
        for section in ("wakeword", "hermes", "audio", "stt", "tts", "leds", "mqtt"):
            obj = getattr(cfg, section)
            data[section] = {
                k: v for k, v in obj.__dict__.items() if not k.startswith("_")
            }
        new_path = str(self.config_path) + ".new"
        Path(new_path).write_text(yaml.safe_dump(data, sort_keys=False))
        return {
            "written": new_path,
            "changes": self.pending,
            "note": "Review it, then move it into place:",
            "command": f"mv {new_path} {self.config_path}",
        }


def _make_handler(state: WizardState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            logger.debug("wizard http: " + fmt, *args)

        # -- plumbing ----------------------------------------------------------
        def _authorized(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("token", [""])[0]
                        or self.headers.get("X-Setup-Token", ""))
            return secrets.compare_digest(supplied, state.token)

        def _json(self, payload, code=200):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        # -- routes -------------------------------------------------------------
        def do_GET(self):
            state.last_request = time.monotonic()
            if not self._authorized():
                return self._json({"error": "bad or missing token"}, 403)
            route = urlparse(self.path).path
            if route == "/":
                body = PAGE_HTML.replace("__TOKEN__", state.token).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/status":
                self._json(state.status())
            elif route == "/api/audio/devices":
                import sounddevice as sd

                devices = [
                    {"index": i, "name": d["name"],
                     "inputs": d["max_input_channels"],
                     "outputs": d["max_output_channels"]}
                    for i, d in enumerate(sd.query_devices())
                ]
                audio = state.config.audio
                self._json({"devices": devices,
                            "input_device": audio.input_device,
                            "output_device": audio.output_device,
                            "input_channels": audio.input_channels})
            elif route == "/api/meter":
                self._json({"rms_pct": state.meter.rms_pct,
                            "p99_pct": state.meter.p99_pct})
            elif route == "/api/wake":
                self._json({"best": state.wake.best, "last": state.wake.last,
                            "detections": state.wake.detections,
                            "threshold": state.config.wakeword.threshold,
                            "error": state.wake.error})
            elif route == "/api/voices":
                self._voices()
            elif route == "/api/pending":
                self._json(state.pending)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            state.last_request = time.monotonic()
            if not self._authorized():
                return self._json({"error": "bad or missing token"}, 403)
            route = urlparse(self.path).path
            try:
                body = self._body()
                if route == "/api/audio/select":
                    for field in ("input_device", "output_device", "input_channels"):
                        if field in body:
                            value = body[field]
                            if value is not None:
                                value = int(value)
                            state.set_pending("audio", field, value)
                    self._json({"ok": True})
                elif route == "/api/audio/tone":
                    self._tone()
                elif route == "/api/meter/start":
                    state.wake.stop()  # one mic user at a time
                    state.meter.start()
                    self._json({"ok": True})
                elif route == "/api/meter/stop":
                    state.meter.stop()
                    self._json({"ok": True})
                elif route == "/api/wake/start":
                    state.meter.stop()
                    state.wake.start()
                    self._json({"ok": True})
                elif route == "/api/wake/stop":
                    state.wake.stop()
                    self._json({"ok": True})
                elif route == "/api/wake/config":
                    state.set_pending(
                        "wakeword", "threshold", float(body["threshold"])
                    )
                    self._json({"ok": True,
                                "threshold": state.config.wakeword.threshold})
                elif route == "/api/voices/preview":
                    self._preview(body)
                elif route == "/api/hermes/test":
                    self._hermes_test(body)
                elif route == "/api/save":
                    self._json(state.save())
                elif route == "/api/exit":
                    self._json({"ok": True, "bye": True})
                    state.shutdown_event.set()
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                logger.exception("wizard: %s failed", route)
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        # -- fatter handlers -----------------------------------------------------
        def _tone(self):
            import math

            from ..audio import build_audio

            rate = state.config.audio.sample_rate
            pcm = b"".join(
                int(12000 * math.sin(2 * math.pi * 440 * i / rate)
                    ).to_bytes(2, "little", signed=True)
                for i in range(rate)
            )
            _, sink = build_audio(state.config)
            sink.play(pcm, rate)
            self._json({"ok": True})

        def _voices(self):
            from urllib.request import urlopen

            downloaded = []
            voices_dir = Path(state.config.tts.voices_dir)
            if voices_dir.exists():
                downloaded = sorted(p.stem for p in voices_dir.glob("*.onnx"))
            catalog = {}
            try:
                from piper.download_voices import VOICES_JSON

                with urlopen(VOICES_JSON, timeout=5) as response:
                    raw = json.load(response)
                catalog = {name: raw[name].get("num_speakers", 1) for name in raw}
            except Exception as exc:
                logger.warning("voice catalog unavailable: %s", exc)
            self._json({"downloaded": downloaded, "catalog": catalog,
                        "current": state.config.tts.voice,
                        "speaker_id": state.config.tts.speaker_id,
                        "length_scale": state.config.tts.length_scale})

        def _preview(self, body):
            import dataclasses

            from ..audio import build_audio
            from ..tts.piper_backend import PiperTTS

            state.meter.stop()
            state.wake.stop()
            name = body.get("name") or state.config.tts.voice
            speaker = body.get("speaker_id")
            length_scale = body.get("length_scale")
            tts_cfg = dataclasses.replace(
                state.config.tts, voice=name, voice_path="",
                speaker_id=int(speaker) if speaker not in (None, "") else None,
                length_scale=float(length_scale)
                if length_scale not in (None, "") else None,
            )
            tts = PiperTTS(tts_cfg, sample_rate=state.config.audio.sample_rate)
            pcm = tts.synthesize(
                body.get("text")
                or "Good evening. All systems are operating within normal parameters."
            )
            _, sink = build_audio(state.config)
            sink.play(pcm, tts.sample_rate)
            # keep the audition as the pending choice
            state.set_pending("tts", "voice", name)
            if tts_cfg.speaker_id is not None:
                state.set_pending("tts", "speaker_id", tts_cfg.speaker_id)
            if tts_cfg.length_scale is not None:
                state.set_pending("tts", "length_scale", tts_cfg.length_scale)
            self._json({"ok": True, "sample_rate": tts.sample_rate})

        def _hermes_test(self, body):
            import requests

            host = body.get("host") or state.config.hermes.host
            port = int(body.get("port") or state.config.hermes.port)
            api_key = body.get("api_key") or state.config.hermes.api_key
            result = {}
            try:
                r = requests.get(f"http://{host}:{port}/health", timeout=5)
                result["health"] = f"HTTP {r.status_code}"
            except Exception as exc:
                self._json({"health": f"unreachable ({type(exc).__name__})"})
                return
            try:
                r = requests.post(
                    f"http://{host}:{port}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"model": state.config.hermes.model,
                          "messages": [{"role": "user", "content": "say PONG"}],
                          "stream": False},
                    timeout=30,
                )
                if r.status_code == 200:
                    reply = r.json()["choices"][0]["message"]["content"]
                    result["chat"] = f"OK: {reply[:120]}"
                    state.set_pending("hermes", "host", host)
                    state.set_pending("hermes", "port", port)
                else:
                    result["chat"] = f"HTTP {r.status_code}: {r.text[:120]}"
            except Exception as exc:
                result["chat"] = f"failed ({type(exc).__name__}: {exc})"
            self._json(result)

    return Handler


def run_wizard(config, config_path: str, host: str = "0.0.0.0",
               port: int = 8321, idle_timeout_s: float = 900.0) -> int:
    state = WizardState(config, config_path, idle_timeout_s)
    server = ThreadingHTTPServer((host, port), _make_handler(state))

    def watchdog():
        while not state.shutdown_event.is_set():
            idle = time.monotonic() - state.last_request
            if idle > state.idle_timeout_s:
                logger.info("wizard idle for %.0fs — exiting", idle)
                state.shutdown_event.set()
            time.sleep(5)

    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    import socket

    hostname = socket.gethostname().split(".")[0]
    print("hermes-satellite setup wizard is running (temporary — exits on "
          f"idle after {int(idle_timeout_s // 60)} min or via the Exit button)",
          flush=True)
    print(f"\n  http://{hostname}:{port}/?token={state.token}\n", flush=True)
    print("If the hostname doesn't resolve from your browser, use this "
          "device's IP address instead. Stop the hermes-satellite daemon "
          "first if it is running — they share the microphone.", flush=True)
    try:
        state.shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    state.meter.stop()
    state.wake.stop()
    server.shutdown()
    print("wizard closed; no ports remain open.", flush=True)
    return 0
