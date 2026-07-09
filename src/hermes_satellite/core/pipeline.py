"""Pipeline orchestration: wire the components to the state machine.

The pipeline runs on the main thread. Each cycle it waits for a wake word,
captures an utterance, transcribes it, sends the text to Hermes, synthesizes the
reply and plays it back — dispatching the corresponding :class:`Event` at each
step so the LED controller (subscribed to state transitions) reflects progress.

Two conversational conveniences layer on top:

* **earcons** — a short chime the moment the wake word fires (so you know it
  heard you without looking at the LEDs) and a tone on error;
* **follow-up mode** — after a reply, re-open capture for a few seconds so a
  continuation ("what about tomorrow?") needs no wake word. A follow-up turn
  is modeled as a *virtual wake*: it reuses the same IDLE->WAKE->RECORD
  transitions, so the state machine and LEDs need no special cases.

Microphone input is gated by a ``is_muted`` callable owned by the app: while
muted, wake detection and capture ignore all audio.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Callable, Optional

from ..audio.base import AudioSink, AudioSource
from ..config import ConversationConfig
from ..hermes.base import AgentClient
from ..stt.base import STTEngine
from ..tts.base import TTSEngine
from ..wakeword.base import WakeWordDetector
from .earcons import Earcons
from .events import Event, StateMachine
from .speech_text import is_stop_command, iter_sentences, make_speakable

logger = logging.getLogger(__name__)

# Pause between our last playback (chime/reply) and the mic flush that
# precedes capture. The flush only discards what has already reached the
# input buffer; sound still traveling speaker -> room -> mic -> ALSA when
# we flush would land *after* it and trip the capture VAD.
MIC_SETTLE_S = 0.2


def _chain_first(first, rest):
    """Yield ``first`` then everything from ``rest``."""
    yield first
    yield from rest


class Pipeline:
    def __init__(
        self,
        *,
        state_machine: StateMachine,
        wakeword: WakeWordDetector,
        audio_source: AudioSource,
        stt: STTEngine,
        hermes: AgentClient,
        tts: TTSEngine,
        audio_sink: AudioSink,
        session_key: str,
        is_muted: Callable[[], bool],
        earcons: Optional[Earcons] = None,
        conversation: Optional[ConversationConfig] = None,
        mic_flush: Optional[Callable[[], None]] = None,
        stream_replies: bool = False,
        barge_wakeword: Optional[WakeWordDetector] = None,
    ):
        self.sm = state_machine
        self.wakeword = wakeword
        self.audio_source = audio_source
        self.stt = stt
        self.hermes = hermes
        self.tts = tts
        self.audio_sink = audio_sink
        self.session_key = session_key
        self.is_muted = is_muted
        self.earcons = earcons
        self.conversation = conversation or ConversationConfig()
        self._mic_flush = mic_flush
        self.stream_replies = stream_replies
        # Dedicated interrupt-phrase detector ("jarvis stop"). When present,
        # a barge means STOP (back to idle); when absent, the main wake word
        # barges and opens a new turn.
        self.barge_wakeword = barge_wakeword
        self._running = False
        # Set while playback is interrupted by a barge-in wake word; consumed
        # by run_cycle to start a fresh turn instead of going idle.
        self._barged = False

    def _chime(self, cue: str) -> None:
        if self.earcons is not None:
            self.earcons.play(cue)

    def _flush_mic(self) -> None:
        """Discard buffered mic audio once the room has gone quiet."""
        if self._mic_flush is None:
            return
        time.sleep(MIC_SETTLE_S)
        self._mic_flush()

    @contextlib.contextmanager
    def _barge_listener(self):
        """Run wake detection during playback (barge-in).

        Yields an Event that fires on a mid-playback wake word; pass it to
        ``audio_sink.play(cancel=...)`` so detection cuts the audio short.
        Yields None when barge-in is disabled. On exit, the listener is
        cancelled (not stopped — the detector stays usable) and
        ``self._barged`` records whether a barge happened.
        """
        if not self.conversation.barge_in:
            yield None
            return
        interrupt = threading.Event()
        done = threading.Event()
        detector = self.barge_wakeword or self.wakeword

        def listen():
            try:
                if detector.wait_for_wake(self.is_muted, cancel=done):
                    logger.info("barge-in: interrupt phrase during playback")
                    interrupt.set()
            except Exception:  # never let the listener kill playback
                logger.exception("barge listener failed")

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        try:
            yield interrupt
        finally:
            done.set()
            listener.join(timeout=2)
            self._barged = interrupt.is_set()

    def _handle_turn(self, onset_timeout: Optional[float]) -> bool:
        """Capture -> STT -> Hermes -> TTS -> play one turn.

        Returns True if a reply was spoken (so a follow-up may continue),
        False if there was nothing to act on (no speech / empty transcript),
        in which case the caller resets to idle.
        """
        self.sm.dispatch(Event.RECORDING_STARTED)
        audio = self.audio_source.capture_utterance(
            self.is_muted, onset_timeout=onset_timeout
        )
        if not audio:
            return False
        self.sm.dispatch(Event.SPEECH_CAPTURED)

        text = self.stt.transcribe(audio)
        logger.info("transcript: %s", text)
        if not text.strip():
            return False
        if is_stop_command(text):
            # "stop" / "never mind" after a barge or in a follow-up window:
            # the user wants out, not an answer — skip the Hermes round-trip.
            logger.info("stop command — going idle")
            self._chime("done")
            return False

        if self._stream_enabled():
            spoke = self._stream_reply(text)
        else:
            spoke = self._blocking_reply(text)
        if spoke:
            self.sm.dispatch(Event.PLAYBACK_DONE)
        return spoke

    def _stream_enabled(self) -> bool:
        return (
            self.stream_replies
            and getattr(self.hermes, "send_stream", None) is not None
        )

    def _blocking_reply(self, text: str) -> bool:
        reply = self.hermes.send(text, self.session_key)
        logger.info("hermes reply: %s", reply)
        self.sm.dispatch(Event.RESPONSE_READY)

        # Defense-in-depth against markdown reaching the speaker: the system
        # prompt asks for plain prose; this flattens whatever slipped through.
        speakable = make_speakable(reply)
        if speakable != reply:
            logger.debug("sanitized reply for speech: %s", speakable)

        pcm = self.tts.synthesize(speakable)
        with self._barge_listener() as interrupt:
            self.audio_sink.play(pcm, self.tts.sample_rate, cancel=interrupt)
        return True

    def _stream_reply(self, text: str) -> bool:
        """Speak the reply sentence-by-sentence as it streams in.

        A synth-ahead thread turns sentences into PCM while the previous one
        plays, so inter-sentence gaps are the queue handoff, not synthesis
        time. Failures before the first sentence fall back to the blocking
        path; failures after audio has started raise (the user heard a
        partial reply; the error earcon and reset follow via run_forever).
        """
        import queue as _queue
        import threading as _threading

        from ..hermes.base import HermesStreamNotStarted

        try:
            deltas = self.hermes.send_stream(text, self.session_key)
            sentences = iter_sentences(deltas)
            try:
                first = next(sentences)
            except StopIteration:
                logger.info("hermes reply: (empty stream)")
                return False
        except HermesStreamNotStarted as exc:
            # No turn started on Hermes — the one case where re-sending is
            # safe. Any other failure means Hermes already has the message:
            # re-sending would start a duplicate turn (field incident: the
            # duplicate hit busy_input_mode:interrupt and killed the
            # in-flight turn), so those propagate to the error path instead.
            logger.warning("stream not started (%s); retrying non-streaming",
                           exc)
            return self._blocking_reply(text)

        self.sm.dispatch(Event.RESPONSE_READY)
        spoken: list = []
        pcm_queue: _queue.Queue = _queue.Queue(maxsize=2)
        _END = object()
        abort = _threading.Event()  # tells the producer to stop synthesizing

        def _put(item) -> bool:
            while not abort.is_set():
                try:
                    pcm_queue.put(item, timeout=0.25)
                    return True
                except _queue.Full:
                    continue
            return False

        def synthesize_ahead():
            try:
                for sentence in _chain_first(first, sentences):
                    if abort.is_set():
                        return
                    spoken.append(sentence)
                    speakable = make_speakable(sentence)
                    if speakable and not _put(self.tts.synthesize(speakable)):
                        return
                _put(_END)
            except Exception as exc:  # surfaced to the playback loop
                _put(exc)

        producer = _threading.Thread(target=synthesize_ahead, daemon=True)
        producer.start()
        try:
            with self._barge_listener() as interrupt:
                while True:
                    if interrupt is not None and interrupt.is_set():
                        break  # barged: stop speaking mid-reply
                    try:
                        item = pcm_queue.get(timeout=0.2)
                    except _queue.Empty:
                        continue
                    if item is _END:
                        break
                    if isinstance(item, Exception):
                        raise item
                    self.audio_sink.play(
                        item, self.tts.sample_rate, cancel=interrupt
                    )
        finally:
            # Stop the producer (it may be mid-synthesis or blocked on a
            # full queue), drain what it already queued, then join.
            abort.set()
            try:
                while True:
                    pcm_queue.get_nowait()
            except _queue.Empty:
                pass
            producer.join(timeout=5)
            logger.info("hermes reply: %s", " ".join(spoken))
        return True

    def run_cycle(self) -> None:
        """Run a single wake -> (turn [-> follow-up turns]) cycle."""
        if not self.wakeword.wait_for_wake(self.is_muted):
            return  # stopped or interrupted before a wake word
        self.sm.dispatch(Event.WAKE_DETECTED)
        self._chime("wake")
        # Drop any buffered audio (wake-word tail + the chime's own echo) so
        # it can't false-trigger the capture VAD.
        self._flush_mic()

        onset: Optional[float] = None  # first turn: default speech timeout
        turns = 0
        while True:
            if not self._handle_turn(onset):
                # No speech / empty transcript. On the first turn this is
                # "woke but heard nothing"; on a follow-up it just means the
                # conversation ended. Either way, settle to idle.
                self.sm.dispatch(Event.RESET)
                return
            turns += 1
            # A successful turn ended with PLAYBACK_DONE, so we're at IDLE.
            if self._barged:
                self._barged = False
                if self.barge_wakeword is not None:
                    # Dedicated stop phrase ("jarvis stop"): the user wants
                    # silence, not a new question. Playback already aborted;
                    # acknowledge and settle to idle.
                    logger.info("stop phrase — conversation over")
                    self._chime("done")
                    return
                # The wake word cut playback short: start a fresh turn,
                # exactly like a normal wake (the barge listener already
                # consumed the wake word itself).
                self.sm.dispatch(Event.WAKE_DETECTED)
                self._chime("wake")
                self._flush_mic()
                onset = None
                turns = 0  # a barge is a new conversation
                continue
            if not self.conversation.follow_up or turns >= self.conversation.max_turns:
                return
            # Re-open for a follow-up turn as a virtual wake (IDLE -> WAKE).
            self._chime("listening")
            self.sm.dispatch(Event.WAKE_DETECTED)
            self._flush_mic()
            onset = self.conversation.follow_up_seconds

    def run_forever(self) -> None:
        """Loop cycles until :meth:`stop` is called.

        Unexpected errors route the state machine through ERROR and reset back to
        IDLE. A :class:`NotImplementedError` from a stubbed backend stops the loop
        cleanly rather than spinning.
        """
        self._running = True
        while self._running:
            try:
                self.run_cycle()
            except NotImplementedError as exc:
                logger.error("stubbed component not implemented: %s", exc)
                self.sm.dispatch(Event.ERROR)
                break
            except Exception:
                logger.exception("pipeline cycle failed")
                self.sm.dispatch(Event.ERROR)
                self._chime("error")
                time.sleep(1.0)
                self.sm.dispatch(Event.RESET)

    def stop(self) -> None:
        self._running = False
        self.wakeword.stop()
