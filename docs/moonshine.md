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
  model: moonshine/base    # moonshine/tiny | moonshine/base
  language: en
```

| Model             | Notes                            |
| ----------------- | -------------------------------- |
| `moonshine/tiny`  | Fastest, lowest footprint        |
| `moonshine/base`  | More accurate; default in config |

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
