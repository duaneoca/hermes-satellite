# Piper TTS

[Piper](https://github.com/rhasspy/piper) is a fast, on‑device neural
text‑to‑speech engine. `hermes-satellite` uses it to speak the Hermes reply.

Backend: `src/hermes_satellite/tts/piper_backend.py` — **implemented**, with
support for both published Piper Python APIs (the package changed shape between
releases): the classic `synthesize_stream_raw()` byte generator and the current
`synthesize()` `AudioChunk` generator. The right one is picked at runtime.

## Install

```bash
pip install piper-tts        # already in the [pi4]/[pi5] extras
```

## Voices

A Piper voice is two files that must sit **side by side**:

```
voice.onnx
voice.onnx.json      # config: sample rate, phoneme map, etc.
```

Browse the [voice catalog](https://github.com/rhasspy/piper/blob/master/VOICES.md)
(samples: [rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/));
files are hosted on [HuggingFace](https://huggingface.co/rhasspy/piper-voices).
Models are **data, not configuration** — keep them in
`/var/lib/hermes-satellite/`, not `/etc`:

```bash
sudo mkdir -p /var/lib/hermes-satellite
sudo chown $USER /var/lib/hermes-satellite    # or the service user, once deployed
V="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/low"
curl -L -o /var/lib/hermes-satellite/en_US-lessac-low.onnx "$V/en_US-lessac-low.onnx"
curl -L -o /var/lib/hermes-satellite/en_US-lessac-low.onnx.json "$V/en_US-lessac-low.onnx.json"
```

(`en_US-lessac-low` is a good satellite default: ~60 MB, natively 16 kHz,
validated with this pipeline. `-medium` variants sound richer at 22 kHz and
cost more CPU.) Point config at the `.onnx`:

```yaml
tts:
  backend: piper
  voice_path: /var/lib/hermes-satellite/en_US-lessac-low.onnx
```

## How the backend works

The voice loads lazily on first synthesis (`PiperVoice.load(voice_path)`), then:

- **classic API** (`piper-tts` <= 1.2): joins `voice.synthesize_stream_raw(text)`
  chunks; rate from `voice.config.sample_rate`;
- **current API** (piper1‑gpl >= 1.3): joins `AudioChunk.audio_int16_bytes` from
  `voice.synthesize(text)`; rate from the chunk.

Piper's output sample rate is set by the **voice model** (often 22050 Hz), not
by `audio.sample_rate`. No resampling is done: `PiperTTS.sample_rate` reports
the voice's native rate and the audio sink opens playback at that rate.

## Playback

`synthesize()` returns PCM; the pipeline passes it to the `AudioSink` (ALSA).
Ensure the output device in config (`audio.output_device`) points at the HAT's
`aplay` device or the 3.5 mm jack — see your board's hardware guide.

## Verifying

Synthesize a known phrase through the project's own backend (handles both
piper APIs and writes a proper WAV with the voice's true sample rate):

```bash
python - <<'EOF'
import wave
from hermes_satellite.config import TTSConfig
from hermes_satellite.tts.piper_backend import PiperTTS
tts = PiperTTS(TTSConfig(voice_path="/var/lib/hermes-satellite/en_US-lessac-low.onnx"))
pcm = tts.synthesize("Hello from Hermes. All systems nominal.")
w = wave.open("/tmp/piper-test.wav", "wb")
w.setnchannels(1); w.setsampwidth(2); w.setframerate(tts.sample_rate)
w.writeframes(pcm); w.close()
print(f"wrote /tmp/piper-test.wav @ {tts.sample_rate} Hz")
EOF
aplay -D plughw:seeed2micvoicec /tmp/piper-test.wav
```
