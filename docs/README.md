# hermes-satellite documentation

| Guide | What it covers |
| ----- | -------------- |
| [hermes-satellite.md](hermes-satellite.md) | Architecture, state machine, config reference, extending backends, running as a service |
| [hermes-api.md](hermes-api.md) | The Hermes API contract this client speaks: endpoint, auth, session-key memory scoping |
| [networking.md](networking.md) | Traffic profile, IoT VLAN / firewall rules, Wi-Fi (WPA3) caveats, offline installs |
| [home-assistant.md](home-assistant.md) | HA integration via outbound-only MQTT discovery: knobs, state, wake events |
| [wakeword.md](wakeword.md) | Wake word (openWakeWord, default): models, tuning ladder, `--ww-monitor`, custom "Hey Hermes", personal verifier |
| [porcupine.md](porcupine.md) | Porcupine backend (non-default; needs a paid Picovoice key) |
| [moonshine.md](moonshine.md) | On-device STT: install, model choice, backend integration |
| [piper.md](piper.md) | On-device TTS: install, voices, playback |
| [hardware/pi4-respeaker-v1.md](hardware/pi4-respeaker-v1.md) | Pi 4 + ReSpeaker 2-Mic HAT v1 (WM8960) setup |
| [hardware/pi5-respeaker-v2.md](hardware/pi5-respeaker-v2.md) | Pi 5 + ReSpeaker 2-Mic HAT v2 (TLV320AIC3104) setup |
| [hardware/seeed-software.md](hardware/seeed-software.md) | Which Seeed drivers/overlays/forks are used, and why, per kernel |

New here? Read [hermes-satellite.md](hermes-satellite.md) first, then the guide
for your board under [hardware/](hardware/).
