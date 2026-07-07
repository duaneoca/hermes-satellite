# Porcupine wake word (non-default backend)

> **Note:** Picovoice no longer offers a free tier, so the project's default
> wake word engine is **openWakeWord** — see [wakeword.md](wakeword.md). This
> backend remains available (`wakeword.backend: porcupine`) for anyone with a
> paid Picovoice AccessKey.

[Porcupine](https://picovoice.ai/products/voice/wake-word/) is Picovoice's
on‑device wake‑word engine.

Backend: `src/hermes_satellite/wakeword/porcupine_backend.py` — **implemented**.
It reads frames from the shared mic stream, drains (but ignores) audio while
muted, and supports either a custom `.ppn` or a built‑in keyword.

## 1. Get an AccessKey

Create a free account at the [Picovoice Console](https://console.picovoice.ai/)
and copy your **AccessKey**. Provide it via env var (preferred) or config:

```bash
export PORCUPINE_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxx
```
```yaml
wakeword:
  access_key: ""          # or leave blank and use the env var
```

## 2. Get a keyword (`.ppn`) model

- **Built‑in keywords** ("computer", "jarvis", "blueberry", …) ship with
  `pvporcupine` — no training needed. Use one immediately once your AccessKey
  is active:

  ```yaml
  wakeword:
    builtin_keyword: computer
    sensitivity: 0.5
  ```

- **Custom wake word** ("Hey Hermes"): train one in the Picovoice Console and
  download the `.ppn`. **Pick the correct platform** — a Raspberry Pi model must
  be a `raspberry-pi` `.ppn`; a Linux‑x86 model will not load on the Pi.

  ```yaml
  wakeword:
    model_path: /var/lib/hermes-satellite/hey-hermes.ppn
    sensitivity: 0.5      # 0.0-1.0; higher = fewer misses, more false triggers
  ```

`model_path` wins when both are set; config requires at least one.

## 3. Install

Included in the Pi extras:

```bash
pip install -e ".[pi5]"   # or ".[pi4]" — both include pvporcupine
```

## 4. How the backend works

`PorcupineWakeWord` creates the engine lazily on the first wait:

```python
pvporcupine.create(access_key=..., keyword_paths=[...] | keywords=[...],
                   sensitivities=[...])
```

then loops reading exactly `handle.frame_length` int16 samples at 16 kHz from
the **shared** `MicStream` (the same stream the VAD capture uses — sharing
avoids dropping the start of speech between wake and record). While muted,
frames are still drained from the device but never passed to `process()`, so no
wake can occur. A `process(frame) >= 0` return is a detection.

It raises a clear error if the AccessKey is missing or if `audio.sample_rate`
doesn't match Porcupine's required rate (16 kHz).

## Tuning

- Raise `sensitivity` if the wake word is missed; lower it if it triggers on
  background speech.
- Test in the room's real acoustic conditions; the ReSpeaker's two mics help but
  placement matters.
