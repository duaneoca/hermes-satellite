"""Home Assistant integration via MQTT discovery.

Outbound-only by design: the satellite connects out to the broker and never
opens a listening port, preserving the IoT-VLAN posture (docs/networking.md).
On connect it publishes Home Assistant `MQTT discovery
<https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery>`_ configs so
HA auto-creates the device with:

* ``switch``  mute — mirrors the HAT button in both directions
* ``number``  volume, wake_threshold, length_scale, led_brightness
* ``select``  voice — options are the voices downloaded on the device
* ``sensor``  pipeline state (idle/wake/record/process/speak/error)
* ``event``   wake — fires on wake-word detection, for automations

Commands arrive on ``hermes-satellite/<device_id>/<key>/set`` and are applied
through the runtime settings layer, so changes take effect live and persist
across restarts. Availability uses an MQTT Last Will so HA shows the
satellite offline the moment it drops.

Requires ``paho-mqtt`` (v1 and v2 client APIs both supported).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ..button import Mute
from ..config import Config
from ..core.settings import RuntimeSettings
from ..core.states import State

logger = logging.getLogger(__name__)

_STATE_NAMES = {s: s.value if hasattr(s, "value") else str(s) for s in State}


def _downloaded_voices(voices_dir: str) -> list:
    try:
        return sorted(p.stem for p in Path(voices_dir).glob("*.onnx"))
    except OSError:
        return []


class MqttBridge:
    """Bridges the settings layer, mute state and pipeline state to MQTT/HA."""

    def __init__(self, config: Config, settings: RuntimeSettings, mute: Mute):
        self._cfg = config.mqtt
        self._config = config
        self._settings = settings
        self._mute = mute
        self._client = None
        self.base = f"hermes-satellite/{self._cfg.device_id}"

        # HA entity definitions: key -> (component, discovery extras, to/from HA)
        self._numbers = {
            "volume": dict(name="Volume", min=0, max=100, step=1,
                           unit_of_measurement="%", scale=100),
            "wake_threshold": dict(name="Wake threshold", min=0.05, max=1.0,
                                   step=0.01, scale=1),
            "length_scale": dict(name="Speech pace", min=0.5, max=2.0,
                                 step=0.05, scale=1),
            "led_brightness": dict(name="LED brightness", min=0, max=31,
                                   step=1, scale=1),
        }

        settings.subscribe(self._on_setting_changed)
        mute.subscribe(self._publish_mute)

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        import paho.mqtt.client as paho

        try:  # paho-mqtt >= 2.0
            client = paho.Client(
                paho.CallbackAPIVersion.VERSION2, client_id=self.base
            )
        except AttributeError:  # paho-mqtt 1.x
            client = paho.Client(client_id=self.base)
        if self._cfg.username:
            client.username_pw_set(self._cfg.username, self._cfg.password or None)
        client.will_set(f"{self.base}/availability", "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client
        client.connect_async(self._cfg.host, self._cfg.port)
        client.loop_start()
        logger.info("mqtt: connecting to %s:%d as %s",
                    self._cfg.host, self._cfg.port, self._cfg.device_id)

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.publish(f"{self.base}/availability", "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # pragma: no cover - best-effort shutdown
            logger.exception("mqtt shutdown failed")

    # -- inbound ---------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        logger.info("mqtt: connected (%s)", reason_code)
        self._publish_discovery(client)
        client.publish(f"{self.base}/availability", "online", retain=True)
        client.subscribe(f"{self.base}/+/set")
        # Publish current state of everything.
        self._publish_mute(self._mute.is_muted())
        for key in self._settings.keys():
            self._publish_setting(key, self._settings.get(key))

    def _on_message(self, client, userdata, message):
        try:
            parts = message.topic.split("/")
            key = parts[-2]
            payload = message.payload.decode("utf-8").strip()
            if key == "mute":
                self._mute.set(payload.upper() == "ON")
                return
            if key in self._numbers:
                value = float(payload) / self._numbers[key]["scale"]
                self._settings.set(key, value)
                return
            if key == "voice":
                self._settings.set("voice", payload)
                return
            logger.warning("mqtt: unknown command topic %s", message.topic)
        except Exception:
            logger.exception("mqtt: failed handling %s", message.topic)

    # -- outbound --------------------------------------------------------------
    def _pub(self, topic: str, payload, retain: bool = True) -> None:
        if self._client is not None:
            self._client.publish(topic, payload, retain=retain)

    def _publish_mute(self, muted: bool) -> None:
        self._pub(f"{self.base}/mute", "ON" if muted else "OFF")

    def _publish_setting(self, key: str, value) -> None:
        if key in self._numbers:
            value = round(value * self._numbers[key]["scale"], 3)
        self._pub(f"{self.base}/{key}", value)

    def _on_setting_changed(self, key: str, value) -> None:
        self._publish_setting(key, value)

    def publish_state(self, state) -> None:
        """Pipeline state change (subscribed by the app to the state machine)."""
        name = getattr(state, "value", None) or str(state).lower()
        self._pub(f"{self.base}/state", name)
        if name == "wake":
            self._pub(f"{self.base}/wake",
                      json.dumps({"event_type": "wake"}), retain=False)

    # -- discovery ---------------------------------------------------------------
    def _device_block(self) -> dict:
        return {
            "identifiers": [self.base],
            "name": self._cfg.device_id,
            "manufacturer": "duaneoca",
            "model": "hermes-satellite",
        }

    def _publish_discovery(self, client) -> None:
        prefix = self._cfg.discovery_prefix
        dev = self._device_block()
        avail = f"{self.base}/availability"

        def announce(component: str, object_id: str, payload: dict) -> None:
            payload.update(
                unique_id=f"{self._cfg.device_id}_{object_id}",
                availability_topic=avail,
                device=dev,
            )
            client.publish(
                f"{prefix}/{component}/{self._cfg.device_id}/{object_id}/config",
                json.dumps(payload),
                retain=True,
            )

        announce("switch", "mute", {
            "name": "Mute",
            "icon": "mdi:microphone-off",
            "command_topic": f"{self.base}/mute/set",
            "state_topic": f"{self.base}/mute",
        })
        for key, spec in self._numbers.items():
            # min/max/step are already in HA units; `scale` only converts
            # payload values (HA percent <-> internal 0-1 for volume).
            payload = {
                "name": spec["name"],
                "command_topic": f"{self.base}/{key}/set",
                "state_topic": f"{self.base}/{key}",
                "min": spec["min"],
                "max": spec["max"],
                "step": spec["step"],
                "mode": "slider",
            }
            if "unit_of_measurement" in spec:
                payload["unit_of_measurement"] = spec["unit_of_measurement"]
            announce("number", key, payload)
        announce("select", "voice", {
            "name": "Voice",
            "icon": "mdi:account-voice",
            "command_topic": f"{self.base}/voice/set",
            "state_topic": f"{self.base}/voice",
            "options": _downloaded_voices(self._config.tts.voices_dir)
                       or [self._config.tts.voice or "none"],
        })
        announce("sensor", "state", {
            "name": "State",
            "icon": "mdi:satellite-uplink",
            "state_topic": f"{self.base}/state",
        })
        announce("event", "wake", {
            "name": "Wake word",
            "state_topic": f"{self.base}/wake",
            "event_types": ["wake"],
        })
