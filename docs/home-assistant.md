# Home Assistant integration (MQTT)

The satellite integrates with Home Assistant through **MQTT discovery** —
HA auto-creates the device and its controls the moment the satellite
connects to your broker. The connection is **outbound-only**: the satellite
keeps zero listening ports, preserving the IoT-VLAN posture
([networking.md](networking.md)). Your HA dashboard effectively becomes the
satellite's web UI, served from your trusted network.

## What appears in HA

| Entity | Type | Does |
| ------ | ---- | ---- |
| Mute | `switch` | mirrors the HAT button, both directions |
| Volume | `number` (0–100 %) | speaker volume |
| Wake threshold | `number` (0.05–1.0) | detection sensitivity |
| Speech pace | `number` (0.5–2.0) | TTS length_scale: >1 statelier, <1 brisker |
| LED brightness | `number` (0–31) | APA102 global brightness, applies live |
| Voice | `select` | switch between downloaded Piper voices |
| State | `sensor` | idle / wake / record / process / speak / error |
| Wake word | `event` | fires on detection — automation trigger |
| Availability | — | LWT: HA shows offline the moment the satellite drops |

Knob changes apply **live** (next utterance at the latest) and **persist**
across restarts — they're written to `{data_dir}/runtime.yaml`, the only
place the sandboxed service can write. `config.yaml` stays admin-owned and
untouched.

## Setup

1. You need an MQTT broker HA can see (most setups: the Mosquitto add-on on
   the HA host) and HA's MQTT integration configured.
2. Satellite config:

   ```yaml
   mqtt:
     enabled: true
     host: 192.168.1.20        # your broker
     username: hermes-satellite
     password: ""              # or MQTT_PASSWORD in secrets.env
     # device_id: kitchen      # defaults to the Pi's hostname
   ```

3. **Firewall**: one more outbound rule — satellite → broker : 1883/TCP
   (see the traffic table in [networking.md](networking.md)).
4. Restart the satellite. The device appears in HA under
   Settings → Devices & Services → MQTT.

## Automation ideas

- **Wake word event** → pause the TV / dim the lights while the satellite
  listens.
- **State sensor** = `speak` → duck multiroom audio.
- **Movie mode** script → flip the Mute switch; the HAT LEDs go red exactly
  as if the button were pressed.
- **Availability** → notify if a satellite goes offline.

## Topics (for non-HA MQTT use)

Everything lives under `hermes-satellite/<device_id>/`:

```
availability            online | offline (retained, LWT)
state                   idle|wake|record|process|speak|error (retained)
wake                    {"event_type":"wake"} (fires on detection)
mute                    ON|OFF          command: mute/set
volume                  0-100           command: volume/set
wake_threshold          0.05-1.0        command: wake_threshold/set
length_scale            0.5-2.0         command: length_scale/set
led_brightness          0-31            command: led_brightness/set
voice                   <voice name>    command: voice/set
```

Anything that speaks MQTT (Node-RED, scripts) can drive the same knobs.
