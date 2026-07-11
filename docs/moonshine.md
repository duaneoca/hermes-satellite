# Moonshine STT (on-device)

[Moonshine](https://github.com/moonshine-ai/moonshine) is a fast, on‑device
speech‑to‑text model family well suited to the Raspberry Pi. It transcribes the
captured utterance before it is sent to Hermes.

Backend: `src/hermes_satellite/stt/moonshine_backend.py` — **implemented**
against `moonshine-voice` 0.1.x (API verified by introspection; note it differs
from older Moonshine tutorials floating around).

## Install

```bash
pip install moonshine-voice        # already in the [pi4]/[pi5] extras
```

> The package ships a native `libmoonshine` for the host architecture; on the
> Pi (aarch64) it loads fine. First use downloads the model files (~70 MB for
> tiny) into the user cache — pre‑fetch during provisioning on a headless Pi:
>
> ```bash
> python -c "import moonshine_voice as mv; mv.get_model_for_language('en', mv.ModelArch.BASE)"
> ```

## Configuration

```yaml
stt:
  backend: moonshine
  model: moonshine/base    # moonshine/tiny | moonshine/base (batch)
  language: en
  streaming: false         # see below
```

| Model             | Notes                            |
| ----------------- | -------------------------------- |
| `moonshine/tiny`  | Fastest, lowest footprint        |
| `moonshine/base`  | More accurate; default in config |

### Streaming transcription (`stt.streaming: true`)

With streaming on, audio is fed to the model **while you speak** (the capture
loop calls into a `Transcriber.create_stream()` session per utterance), so
the transcript is ready the moment you stop — removing the ~1 s
transcription stall a Pi 4 pays after capture. Verified by loopback:
`update_transcription()` finalizes in ~1 ms after the last frame.

Streaming needs a **streaming model variant**, which are separate weights:
`tiny-streaming-en`, `small-streaming-en`, `medium-streaming-en` — there is
**no base-streaming**. So with streaming on, set `stt.model` to
`moonshine/tiny`, `moonshine/small` or `moonshine/medium`
(`moonshine/small` is the closest to base quality; the backend raises a
clear error if you leave it on `base`, and the wizard's model picker
auto-switches `base` to `small` when you enable streaming). First use downloads the variant —
run the wizard's Transcription test (which uses the same mode and warms the
service's cache) and spot-check accuracy there before trusting it.

## How the backend uses the API (moonshine-voice 0.1.x)

```python
import moonshine_voice as mv

# Resolve + download the model for the language/architecture:
model_name, arch = mv.get_model_for_language("en", mv.ModelArch.BASE)
transcriber = mv.Transcriber(str(model_path), model_arch=arch)

# Non-streaming, one utterance at a time. Input is float samples in [-1, 1]:
transcript = transcriber.transcribe_without_streaming(samples, sample_rate=16000)
text = " ".join(line.text for line in transcript.lines)
```

The backend converts the pipeline's 16‑bit PCM to floats (`int16 / 32768.0`)
and joins `transcript.lines[].text`. The `Transcriber` is created once, on the
first utterance.

`moonshine-voice` also offers a streaming API (`add_audio` / listeners); the
non‑streaming call fits this pipeline's discrete-utterance flow.

## Verifying on the Pi

```bash
python - <<'EOF'
from hermes_satellite.config import STTConfig
from hermes_satellite.stt.moonshine_backend import MoonshineSTT
import wave
w = wave.open("test.wav")           # 16 kHz mono s16le, e.g. from arecord
audio = w.readframes(w.getnframes())
print(MoonshineSTT(STTConfig()).transcribe(audio))
EOF
```
