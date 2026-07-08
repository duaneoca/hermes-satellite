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

from . import mixer
from .page import PAGE_HTML

logger = logging.getLogger(__name__)

# Serializes lazy construction of numpy-heavy components (wake detector,
# piper voice). First imports of C-extension modules from concurrent request
# threads can poison numpy for the whole process ("cannot load module more
# than once per process") — see _preload_heavy_modules, which is the primary
# defense; this lock covers whatever loads later.
_heavy_lock = threading.Lock()


def _ensure_voices_dir(config) -> None:
    """Make sure the voice download target exists and is writable.

    Fresh-install gap: /var/lib/hermes-satellite is created by the service
    install (step 4), but the wizard (step 3) downloads voices into it. Try
    plain creation, then non-interactive sudo (fixed argument lists, no
    shell), then hand the user the exact commands.
    """
    import getpass
    import os
    import subprocess

    path = Path(config.tts.voices_dir)
    if path.is_dir() and os.access(path, os.W_OK):
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
        return
    except (PermissionError, FileNotFoundError):
        pass
    user = getpass.getuser()
    data_dir = str(config.data_dir)
    for command in (["sudo", "-n", "mkdir", "-p", str(path)],
                    ["sudo", "-n", "chown", "-R", user, data_dir]):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=10)
        except OSError:
            result = None
        if result is None or result.returncode != 0:
            raise RuntimeError(
                f"cannot create {path} — run on the device: "
                f"sudo mkdir -p {path} && sudo chown -R {user} {data_dir}"
            )
    logger.info("created %s (owned by %s) via sudo", path, user)


def _preload_heavy_modules() -> None:
    """Import shared C-extension modules once, in the main thread, BEFORE
    the browser can hit us with parallel requests. Field failure without
    this: the page's simultaneous /api/status + /api/audio/devices +
    /api/voices calls raced numpy's import machinery
    (_ModuleLock deadlock -> numpy poisoned for the process)."""
    for module in ("numpy", "sounddevice", "onnxruntime", "requests", "piper"):
        try:
            __import__(module)
        except Exception as exc:  # missing on this platform: fine
            logger.debug("preload %s skipped: %s", module, exc)


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
        # True once the first audio frame has actually been scored — model
        # load takes seconds on a Pi, and users say the phrase too early.
        self.ready = False
        self.on_listening = None   # callback: scoring has begun
        self.on_stopped = None     # callback: monitor ended

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.best = self.last = 0.0
        self.detections = 0
        self.error = ""
        self.ready = False
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
            with _heavy_lock:
                detector = build_wakeword(self._config, demo=False, mic=mic)
            self._detector = detector

            def on_score(predictions):
                score = max(predictions.values())
                self.last = round(float(score), 3)
                self.best = max(self.best, self.last)

            def on_audio(_pcm):
                if not self.ready:
                    self.ready = True
                    if self.on_listening:
                        self.on_listening()

            detector.on_score = on_score
            detector.on_audio = on_audio
            try:
                while detector.wait_for_wake(lambda: False):
                    self.detections += 1
            finally:
                mic.close()
        except Exception as exc:
            logger.error("wake monitor failed: %s", exc)
            self.error = str(exc)
        finally:
            self.ready = False
            if self.on_stopped:
                self.on_stopped()


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
        self._leds = None
        # Show "listening" on the real HAT LEDs while the wake test runs —
        # the daemon is stopped during setup, so the LEDs are ours to use.
        # IDLE (breathing blue) is deliberately the same indication the
        # daemon shows while waiting for the wake word.
        self.wake.on_listening = lambda: self._led("idle")
        self.wake.on_stopped = lambda: self._led("off")
        self._tts_cache: dict = {}

    def _led(self, name: str) -> None:
        try:
            from ..leds import build_led_controller
            from ..leds.base import LEDState

            if self._leds is None:
                self._leds = build_led_controller(self.config)
                self._leds.start()
            self._leds.set_state(
                LEDState.IDLE if name == "idle" else LEDState.OFF
            )
        except Exception as exc:  # cosmetic: never break the wizard over LEDs
            logger.debug("wizard LEDs unavailable: %s", exc)

    def close_leds(self) -> None:
        if self._leds is not None:
            try:
                self._leds.stop()
            except Exception:
                pass

    # -- change tracking -------------------------------------------------------
    def set_pending(self, section: str, field: str, value):
        sect = getattr(self.config, section)
        setattr(sect, field, value)  # apply live so tests/preview use it
        self.pending[f"{section}.{field}"] = value

    # -- doctor -----------------------------------------------------------------
    @staticmethod
    def _board_model(path: str = "/proc/device-tree/model") -> str:
        try:
            return Path(path).read_text().rstrip("\x00").strip()
        except OSError:
            return ""

    def status(self) -> dict:
        cfg = self.config
        checks = {}
        checks["profile"] = cfg.hardware_profile
        board = self._board_model()
        if board:
            checks["board"] = board
            expected = None
            if "Raspberry Pi 4" in board:
                expected = "pi4"
            elif "Raspberry Pi 5" in board:
                expected = "pi5"
            if expected and not cfg.hardware_profile.startswith(expected):
                checks["profile_warning"] = (
                    f"this board is a {board!r} but hardware_profile is "
                    f"{cfg.hardware_profile!r} — set hardware_profile: "
                    f"{expected}-respeaker-v1"
                    if expected == "pi4" else
                    f"this board is a {board!r} but hardware_profile is "
                    f"{cfg.hardware_profile!r} — set hardware_profile: "
                    f"{expected}-respeaker-v2"
                )
        spidev = Path(f"/dev/spidev{cfg.leds.spi_bus}.{cfg.leds.spi_device}")
        checks["spidev"] = spidev.exists() or f"missing {spidev}"
        try:
            import onnxruntime
            ver = onnxruntime.__version__
            checks["onnxruntime"] = ver if ver < "1.27" else f"{ver} — BROKEN, pin <1.27"
        except ImportError:
            checks["onnxruntime"] = "not installed"
        cards = [c["id"] for c in mixer.list_cards()]
        checks["alsa_cards"] = cards or "none found"
        if cfg.hardware_profile.startswith("pi") and not any(
            "seeed" in c.lower() or "wm8960" in c.lower() for c in cards
        ):
            checks["alsa_cards_warning"] = (
                "no ReSpeaker/seeed card — audio overlay not installed or not "
                "rebooted? See your hardware guide, section 1"
            )
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
    # Credentials never go into config.yaml — they are extracted into a
    # sibling secrets.env (0600), the same file systemd's EnvironmentFile
    # reads after deployment; the config loader also reads it for
    # interactive runs.
    SECRET_FIELDS = (
        ("hermes", "api_key", "HERMES_API_KEY"),
        ("wakeword", "access_key", "PORCUPINE_ACCESS_KEY"),
        ("mqtt", "password", "MQTT_PASSWORD"),
    )

    def save(self) -> dict:
        cfg = self.config
        data = {
            "hardware_profile": cfg.hardware_profile,
            "log_level": cfg.log_level,
            "data_dir": cfg.data_dir,
        }
        for section in ("wakeword", "hermes", "audio", "stt", "tts", "leds",
                        "earcons", "conversation", "mqtt"):
            obj = getattr(cfg, section)
            data[section] = {
                k: v for k, v in obj.__dict__.items() if not k.startswith("_")
            }
        # strip secrets from the yaml; collect them for secrets.env
        secrets_out = {}
        for section, field, env_name in self.SECRET_FIELDS:
            value = data[section].get(field)
            if value:
                secrets_out[env_name] = value
            data[section][field] = ""
        target = Path(self.config_path)
        backup = None
        try:
            if target.exists():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = target.with_name(f"{target.name}.bak-{stamp}")
                backup.write_text(target.read_text())
            target.write_text(yaml.safe_dump(data, sort_keys=False))
        except PermissionError:
            return {
                "error": f"{target} is not writable by this user — rerun the "
                         "wizard with sudo, or point --config at a writable "
                         "copy",
            }
        secrets_path = None
        if secrets_out:
            secrets_path = target.parent / "secrets.env"
            existing = {}
            if secrets_path.exists():
                for line in secrets_path.read_text().splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, _, v = line.partition("=")
                        existing[k.strip()] = v.strip()
            existing.update(secrets_out)

            def _env_value(value):
                # Quote when whitespace/#/; could confuse env-file parsers
                # (systemd EnvironmentFile strips one matched pair, as does
                # our loader).
                if any(ch in value for ch in " \t#;"):
                    return f'"{value}"'
                return value

            secrets_path.write_text(
                "".join(f"{k}={_env_value(v)}\n"
                        for k, v in sorted(existing.items()))
            )
            secrets_path.chmod(0o600)
        return {
            "written": str(target),
            "backup": str(backup) if backup else None,
            "secrets": str(secrets_path) if secrets_path else None,
            "changes": self.pending,
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
                defaults = {"in": None, "out": None}
                try:
                    default_in, default_out = sd.default.device
                    defaults["in"] = default_in if default_in >= 0 else None
                    defaults["out"] = default_out if default_out >= 0 else None
                except Exception:  # no defaults on this host
                    pass
                self._json({"devices": devices,
                            "input_device": audio.input_device,
                            "output_device": audio.output_device,
                            "input_channels": audio.input_channels,
                            "default_input": defaults["in"],
                            "default_output": defaults["out"]})
            elif route == "/api/meter":
                self._json({"rms_pct": state.meter.rms_pct,
                            "p99_pct": state.meter.p99_pct})
            elif route == "/api/wake":
                self._json({"best": state.wake.best, "last": state.wake.last,
                            "detections": state.wake.detections,
                            "threshold": state.config.wakeword.threshold,
                            "ready": state.wake.ready,
                            "error": state.wake.error})
            elif route == "/api/voices":
                self._voices()
            elif route == "/api/mixer/cards":
                self._json({"cards": mixer.list_cards()})
            elif route == "/api/mixer":
                query = parse_qs(urlparse(self.path).query)
                card = query.get("card", [""])[0]
                self._json({"controls": mixer.get_controls(card)} if card
                           else {"error": "card required"})
            elif route == "/api/mqtt":
                mqtt_cfg = state.config.mqtt
                pw = mqtt_cfg.password
                hint = ("••••" + pw[-4:]) if len(pw) > 8 else ("••••" if pw else "")
                self._json({"enabled": mqtt_cfg.enabled,
                            "host": mqtt_cfg.host, "port": mqtt_cfg.port,
                            "username": mqtt_cfg.username,
                            "device_id": mqtt_cfg.device_id,
                            "password_hint": hint})
            elif route == "/api/hermes":
                hermes = state.config.hermes
                key = hermes.api_key
                hint = ("••••" + key[-4:]) if len(key) > 8 else \
                       ("••••" if key else "")
                self._json({"host": hermes.host, "port": hermes.port,
                            "session_key": hermes.session_key,
                            "api_key_hint": hint})
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
                elif route == "/api/mixer/set":
                    self._json(mixer.set_control(
                        body["card"], body["control"], body["value"]))
                elif route == "/api/mixer/recipe":
                    self._json(mixer.apply_recipe(body["card"]))
                elif route == "/api/mixer/store":
                    self._json(mixer.store())
                elif route == "/api/voices/preview":
                    self._preview(body)
                elif route == "/api/hermes/test":
                    self._hermes_test(body)
                elif route == "/api/mqtt/config":
                    state.set_pending("mqtt", "enabled", bool(body["enabled"]))
                    self._json({"ok": True,
                                "enabled": state.config.mqtt.enabled})
                elif route == "/api/mqtt/test":
                    self._mqtt_test(body)
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
            _ensure_voices_dir(state.config)
            name = body.get("name") or state.config.tts.voice
            speaker = body.get("speaker_id")
            length_scale = body.get("length_scale")
            was_downloaded = (
                Path(state.config.tts.voices_dir) / f"{name}.onnx"
            ).exists()

            # Cache loaded engines: reloading the onnx per click looks like a
            # re-download on a Pi. Knobs live on the cached config object and
            # are read at synthesis time, so they stay adjustable.
            cached = state._tts_cache.get(name)
            if cached is None:
                tts_cfg = dataclasses.replace(
                    state.config.tts, voice=name, voice_path="")
                tts = PiperTTS(
                    tts_cfg, sample_rate=state.config.audio.sample_rate)
                state._tts_cache[name] = (tts_cfg, tts)
            else:
                tts_cfg, tts = cached
            tts_cfg.speaker_id = (
                int(speaker) if speaker not in (None, "") else None)
            tts_cfg.length_scale = (
                float(length_scale) if length_scale not in (None, "") else None)

            started = time.monotonic()
            with _heavy_lock:
                pcm = tts.synthesize(
                    body.get("text")
                    or "Good evening. All systems are operating "
                       "within normal parameters."
                )
            elapsed = round(time.monotonic() - started, 1)
            _, sink = build_audio(state.config)
            sink.play(pcm, tts.sample_rate)
            # keep the audition as the pending choice
            state.set_pending("tts", "voice", name)
            if tts_cfg.speaker_id is not None:
                state.set_pending("tts", "speaker_id", tts_cfg.speaker_id)
            if tts_cfg.length_scale is not None:
                state.set_pending("tts", "length_scale", tts_cfg.length_scale)
            self._json({"ok": True, "sample_rate": tts.sample_rate,
                        "downloaded": not was_downloaded,
                        "elapsed": elapsed})

        def _mqtt_test(self, body):
            import paho.mqtt.client as paho

            host = body.get("host") or state.config.mqtt.host
            port = int(body.get("port") or state.config.mqtt.port)
            username = body.get("username", state.config.mqtt.username)
            password = body.get("password") or state.config.mqtt.password
            if not host:
                return self._json({"result": "no broker host given"})

            outcome = {}
            connected = threading.Event()

            def on_connect(client, userdata, flags, rc, properties=None):
                code = getattr(rc, "value", rc)
                outcome["ok"] = code == 0 or str(rc) == "Success"
                outcome["rc"] = str(rc)
                connected.set()

            try:
                try:  # paho >= 2.0
                    client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                                         client_id="hermes-satellite-wizard")
                except AttributeError:  # paho 1.x
                    client = paho.Client(client_id="hermes-satellite-wizard")
                if username:
                    client.username_pw_set(username, password or None)
                client.on_connect = on_connect
                client.connect_async(host, port)
                client.loop_start()
                connected.wait(6)
                client.loop_stop()
                client.disconnect()
            except Exception as exc:
                return self._json(
                    {"result": f"unreachable ({type(exc).__name__}: {exc})"})
            if not connected.is_set():
                return self._json(
                    {"result": f"no answer from {host}:{port} within 6s — "
                               "host/port/firewall?"})
            if not outcome.get("ok"):
                return self._json(
                    {"result": f"broker refused: {outcome.get('rc')} — "
                               "check username/password"})
            # success: pend the settings (password goes to secrets.env on save)
            state.set_pending("mqtt", "host", host)
            state.set_pending("mqtt", "port", port)
            state.set_pending("mqtt", "username", username)
            if body.get("password"):
                state.set_pending("mqtt", "password", body["password"])
            self._json({"result": "connected ✓", "ok": True})

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
                    if body.get("api_key"):
                        state.set_pending("hermes", "api_key", body["api_key"])
                    if body.get("session_key") and \
                            body["session_key"] != state.config.hermes.session_key:
                        state.set_pending(
                            "hermes", "session_key", body["session_key"])
                else:
                    result["chat"] = f"HTTP {r.status_code}: {r.text[:120]}"
            except Exception as exc:
                result["chat"] = f"failed ({type(exc).__name__}: {exc})"
            self._json(result)

    return Handler


def run_wizard(config, config_path: str, host: str = "0.0.0.0",
               port: int = 8321, idle_timeout_s: float = 900.0) -> int:
    _preload_heavy_modules()
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
    state.close_leds()
    server.shutdown()
    print("wizard closed; no ports remain open.", flush=True)
    return 0
