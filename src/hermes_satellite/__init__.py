"""hermes-satellite: a voice satellite bridging wake word detection to a
Hermes agent backend."""

import os

# Make PortAudio open ALSA devices through the `plughw` conversion layer
# instead of raw `hw`. Raw devices reject sample rates the codec doesn't do
# natively — field failure: playback of a 22050 Hz Piper voice on a pinned
# WM8960 output died with 'Invalid sample rate [-9997]', while `aplay -D
# plughw:` had always worked because ALSA's plug layer resamples. Must be set
# before PortAudio initializes (first sounddevice import anywhere), hence
# here at package import. Export PA_ALSA_PLUGHW=0 to opt out.
os.environ.setdefault("PA_ALSA_PLUGHW", "1")

__version__ = "0.1.0"
