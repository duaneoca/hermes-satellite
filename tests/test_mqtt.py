"""MQTT/HA bridge, tested with a fake paho client."""

import json
import sys
import types

import pytest

from hermes_satellite.button import Mute
from hermes_satellite.config import load_config
from hermes_satellite.core.settings import RuntimeSettings
from hermes_satellite.core.states import State


class FakePahoClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.published = []   # (topic, payload, retain)
        self.subscriptions = []
        self.will = None
        self.on_connect = None
        self.on_message = None
        FakePahoClient.instances.append(self)

    def username_pw_set(self, u, p):
        self.auth = (u, p)

    def will_set(self, topic, payload, retain=False):
        self.will = (topic, payload)

    def publish(self, topic, payload=None, retain=False):
        self.published.append((topic, payload, retain))

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def connect_async(self, host, port):
        self.connected_to = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    # test helper: deliver an inbound message
    def push(self, topic, payload):
        msg = types.SimpleNamespace(topic=topic, payload=payload.encode())
        self.on_message(self, None, msg)


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    FakePahoClient.instances = []
    paho_pkg = types.ModuleType("paho")
    mqtt_mod = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")
    client_mod.Client = FakePahoClient
    # no CallbackAPIVersion attr -> exercises the paho-1.x fallback path
    paho_pkg.mqtt = mqtt_mod
    mqtt_mod.client = client_mod
    monkeypatch.setitem(sys.modules, "paho", paho_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_mod)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client_mod)

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "hardware_profile: mock\n"
        "wakeword:\n  model_path: hey_jarvis\n"
        f"data_dir: {tmp_path}\n"
        "mqtt:\n  enabled: true\n  host: broker.local\n  device_id: test-sat\n"
    )
    config = load_config(str(cfg_file))
    settings = RuntimeSettings(config, tmp_path / "runtime.yaml")
    mute = Mute()

    from hermes_satellite.integrations.mqtt import MqttBridge

    b = MqttBridge(config, settings, mute)
    b.start()
    client = FakePahoClient.instances[-1]
    client.on_connect(client, None, None, 0)  # simulate broker connect
    return b, client, settings, mute, config


def _topics(client):
    return [t for t, _, _ in client.published]


def test_discovery_availability_and_initial_state(bridge):
    b, client, settings, mute, config = bridge
    topics = _topics(client)
    assert "homeassistant/switch/test-sat/mute/config" in topics
    assert "homeassistant/number/test-sat/volume/config" in topics
    assert "homeassistant/select/test-sat/voice/config" in topics
    assert "homeassistant/sensor/test-sat/state/config" in topics
    assert "homeassistant/event/test-sat/wake/config" in topics
    assert ("hermes-satellite/test-sat/availability", "online", True) in client.published
    assert client.will == ("hermes-satellite/test-sat/availability", "offline")
    # discovery payloads carry the device block
    payload = json.loads(
        next(p for t, p, _ in client.published
             if t == "homeassistant/switch/test-sat/mute/config")
    )
    assert payload["device"]["model"] == "hermes-satellite"
    assert client.subscriptions == ["hermes-satellite/test-sat/+/set"]


def test_mute_command_and_state_roundtrip(bridge):
    b, client, settings, mute, config = bridge
    client.push("hermes-satellite/test-sat/mute/set", "ON")
    assert mute.is_muted() is True
    assert ("hermes-satellite/test-sat/mute", "ON", True) in client.published
    client.push("hermes-satellite/test-sat/mute/set", "OFF")
    assert mute.is_muted() is False


def test_volume_command_scales_percent_to_unit(bridge):
    b, client, settings, mute, config = bridge
    client.push("hermes-satellite/test-sat/volume/set", "40")
    assert config.tts.volume == pytest.approx(0.4)
    # state published back in HA units
    assert ("hermes-satellite/test-sat/volume", 40.0, True) in client.published


def test_voice_command_sets_setting(bridge):
    b, client, settings, mute, config = bridge
    client.push("hermes-satellite/test-sat/voice/set", "en_GB-alan-medium")
    assert config.tts.voice == "en_GB-alan-medium"


def test_state_publish_and_wake_event(bridge):
    b, client, settings, mute, config = bridge
    b.publish_state(State.PROCESS)
    assert ("hermes-satellite/test-sat/state", "process", True) in client.published
    b.publish_state(State.WAKE)
    wake = [p for t, p, r in client.published
            if t == "hermes-satellite/test-sat/wake" and not r]
    assert wake and json.loads(wake[0])["event_type"] == "wake"


def test_bad_command_does_not_crash(bridge):
    b, client, settings, mute, config = bridge
    client.push("hermes-satellite/test-sat/volume/set", "not-a-number")
    client.push("hermes-satellite/test-sat/bogus/set", "1")
