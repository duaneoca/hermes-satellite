# Wake Word Detection (openWakeWord)

The default wake word engine is [openWakeWord](https://github.com/dscripka/openWakeWord):
free, Apache-2.0, no vendor account, runs comfortably on the Pi, and custom wake
words ("Hey Hermes") are trained from Piper-generated synthetic speech — the
same TTS engine this project already uses.

Backend: `src/hermes_satellite/wakeword/openwakeword_backend.py` — **implemented
and validated** with a live loopback (Piper speaks → openWakeWord detects,
score 0.99; adversarial near-phrases and silence correctly rejected).

The Porcupine backend remains available (`wakeword.backend: porcupine`) for
anyone with a paid Picovoice key — see [porcupine.md](porcupine.md).

## ⚠ onnxruntime pin

**onnxruntime 1.27.x silently breaks openWakeWord**: its batch-1 inference of
the shared embedding model returns garbage, so every streaming detection score
becomes ~0.0 — the daemon simply never wakes, with no error anywhere. We
verified 1.20–1.26 are good and pin `onnxruntime>=1.15,<1.27` in the Pi
extras. If wake detection ever "goes deaf" after an upgrade, check this first:

```bash
pip show onnxruntime   # must be < 1.27 until upstream fixes batch-1 inference
```

(This class of silent failure may also explain generally "disappointing"
openWakeWord experiences — a broken runtime looks identical to a bad model.)

## Quick start (pretrained model)

```yaml
wakeword:
  backend: openwakeword
  model_path: hey_jarvis     # pretrained: hey_jarvis | alexa | hey_mycroft | hey_rhasspy
  threshold: 0.5
```

Model files auto-download on first run. To pre-fetch during provisioning:

```bash
python -c "import openwakeword.utils; openwakeword.utils.download_models(['hey_jarvis'])"
```

## Tuning ladder (do these in order)

Target: **< 0.5 false accepts/hour, < 5% false rejects.**

### 1. Fix input audio first

Verify gain: `arecord -D <dev> -f S16_LE -r 16000 -c 2 test.wav`, speak at
normal distance, check the waveform isn't clipped or buried. Set the capture
volume in `alsamixer`. Quiet audio depresses all scores (false rejects);
clipping distorts features (both failure modes).

### 2. Calibrate the threshold empirically

```bash
hermes-satellite --ww-monitor -c config.yaml
```

Streams live per-frame scores. Say the wake phrase ~10 times at realistic
distances; let the room be normally noisy (TV, music, conversation) for a
while. Pick `threshold` comfortably below your spoken peaks and above the
ambient ones.

Measured example (synthetic, `hey_jarvis`): true phrase peaks 0.97–0.99;
phonetic attack "hey jar of peanut butter" peaks 0.75–0.89; unrelated speech
0.00–0.04. Default 0.5 accepts the attack; 0.9 rejects it and still fires
reliably. **Real voices typically score lower than synthetic ones** (the
pretrained models were trained on synthetic speech), so calibrate with your
voice, on the device mic — don't copy these numbers.

### 3. Layer the cheap guards

```yaml
wakeword:
  vad_threshold: 0.5        # Silero VAD gate: kills non-speech false accepts
  patience_frames: 2        # require N consecutive frames over threshold
  refractory_seconds: 2.0   # suppress double-fires
  noise_suppression: true   # SpeexDSP, Linux only (apt install libspeexdsp-dev)
```

**patience_frames caveat (measured):** scores can exceed threshold for only a
single 80 ms frame on short, crisp utterances — patience 2 then suppresses real
wakes. Only raise it after `--ww-monitor` shows your spoken phrase holds above
threshold for multiple consecutive frames; pairing patience with a lower
threshold is the usual pattern.

### 4. Train "Hey Hermes" properly

Use the [automatic training Colab](https://github.com/dscripka/openWakeWord#training-new-models)
(or the [community 2026 notebook](https://github.com/alfiedennen/openwakeword-colab-2026))
— under an hour, no account:

- generate **tens of thousands** of synthetic positives (many Piper voices,
  speed/pitch variation); accuracy scales smoothly with dataset size
- keep the heavy augmentation defaults (room impulse responses + noise mixing)
- add **adversarial negatives**: phonetically similar phrases ("her knees",
  "hermit", "heresy", "hey herbie", ...)
- optionally mix in 20–50 real recordings of your voice from the ReSpeaker mic

Then: `model_path: /etc/hermes-satellite/hey_hermes.onnx`.

### 5. The strongest lever: a personal verifier

openWakeWord can train a second-stage classifier on **your own voice** that
runs only when the primary model fires
([docs](https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md)):

```python
from openwakeword import train_custom_verifier
train_custom_verifier(
    positive_reference_clips=["me_1.wav", ...],   # 20-50 clips, device mic
    negative_reference_clips=["other_speech.wav", ...],
    output_path="/etc/hermes-satellite/verifier.pkl",
    model_name="hey_hermes",   # must match the wakeword model's name/stem
)
```

```yaml
wakeword:
  verifier_model_path: /etc/hermes-satellite/verifier.pkl
  verifier_threshold: 0.3
```

For a single-household satellite this converts "generic detector" into
"detector for your voices" — the biggest false-accept reduction available.

## How the backend works

80 ms frames (1280 samples @ 16 kHz) from the shared `MicStream` →
`Model.predict()` → fire at `threshold`, honoring `patience_frames` and
`refractory_seconds`. While muted, frames are drained but never processed, and
model state is reset on unmute. The mic is flushed on entry so audio buffered
during PROCESS/SPEAK (including the assistant's own TTS) is never scored.
`--ww-monitor` attaches to the same code path, so what you calibrate is what
runs.
