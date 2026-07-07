#!/bin/sh
# Apply the field-verified WM8960 mixer state for the ReSpeaker 2-Mic HAT v1
# (built-in wm8960-soundcard overlay path). The mainline driver defaults the
# routing switches OFF, and a stale alsactl snapshot can silently re-disable
# the capture path — symptom: recordings show a constant ~1% noise floor that
# does not react to your voice at all.
#
# Usage:  ./scripts/wm8960-mixer.sh [card-name]     (default: seeed2micvoicec)
# Then:   sudo alsactl store     # pin this state across reboots
#
# Gains (Capture 63 / ADC PCM 220) are the calibration for conversational
# distance (~2 m); see docs/hardware/pi4-respeaker-v1.md to re-calibrate.

set -e
C="${1:-seeed2micvoicec}"

# --- capture path: mic -> LINPUT1/RINPUT1 -> boost mixer -> PGA -> ADC
amixer -q -c "$C" sset 'Left Input Mixer Boost' on
amixer -q -c "$C" sset 'Right Input Mixer Boost' on
amixer -q -c "$C" sset 'Left Boost Mixer LINPUT1' on
amixer -q -c "$C" sset 'Right Boost Mixer RINPUT1' on
amixer -q -c "$C" sset 'Capture' 63 cap
amixer -q -c "$C" sset 'ADC PCM' 220
amixer -q -c "$C" sset 'ALC Function' None

# --- playback path: DAC -> output mixer -> speaker/headphone
amixer -q -c "$C" sset 'Left Output Mixer PCM' on
amixer -q -c "$C" sset 'Right Output Mixer PCM' on
amixer -q -c "$C" sset 'Playback' 255
amixer -q -c "$C" sset 'Speaker' 121
amixer -q -c "$C" sset 'Headphone' 110
amixer -q -c "$C" sset 'Speaker DC' 5
amixer -q -c "$C" sset 'Speaker AC' 5

echo "wm8960 mixer state applied to card '$C'."
echo "Run 'sudo alsactl store' to persist it across reboots."
