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

Download a voice from the
[Piper voices catalog](https://github.com/rhasspy/piper/blob/master/VOICES.md)
(e.g. `en_US-lessac-medium`) and point config at the `.onnx`:

```yaml
tts:
  backend: piper
  voice_path: /etc/hermes-satellite/voice.onnx
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

Synthesize a known phrase and play it with `aplay` to confirm the voice and
output device before wiring it into the pipeline:

```bash
python -c "from piper.voice import PiperVoice; \
  v=PiperVoice.load('/etc/hermes-satellite/voice.onnx'); \
  open('/tmp/out.raw','wb').write(b''.join(v.synthesize_stream_raw('hello from hermes')))"
aplay -r 22050 -f S16_LE -c 1 /tmp/out.raw
```
