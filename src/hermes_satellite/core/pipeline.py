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

import logging
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
from .speech_text import iter_sentences, make_speakable

logger = logging.getLogger(__name__)


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
        self._running = False

    def _chime(self, cue: str) -> None:
        if self.earcons is not None:
            self.earcons.play(cue)

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
        self.audio_sink.play(pcm, self.tts.sample_rate)
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

        try:
            deltas = self.hermes.send_stream(text, self.session_key)
            sentences = iter_sentences(deltas)
            try:
                first = next(sentences)
            except StopIteration:
                logger.info("hermes reply: (empty stream)")
                return False
        except Exception as exc:
            logger.warning("stream unavailable (%s); falling back", exc)
            return self._blocking_reply(text)

        self.sm.dispatch(Event.RESPONSE_READY)
        spoken: list = []
        pcm_queue: _queue.Queue = _queue.Queue(maxsize=2)
        _END = object()

        def synthesize_ahead():
            try:
                for sentence in _chain_first(first, sentences):
                    spoken.append(sentence)
                    speakable = make_speakable(sentence)
                    if speakable:
                        pcm_queue.put(self.tts.synthesize(speakable))
                pcm_queue.put(_END)
            except Exception as exc:  # surfaced to the playback loop
                pcm_queue.put(exc)

        producer = _threading.Thread(target=synthesize_ahead, daemon=True)
        producer.start()
        try:
            while True:
                item = pcm_queue.get()
                if item is _END:
                    break
                if isinstance(item, Exception):
                    raise item
                self.audio_sink.play(item, self.tts.sample_rate)
        finally:
            # If playback failed, the producer may be blocked on a full
            # queue — drain so it can finish, then join.
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
        if self._mic_flush is not None:
            self._mic_flush()

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
            if not self.conversation.follow_up or turns >= self.conversation.max_turns:
                return
            # Re-open for a follow-up turn as a virtual wake (IDLE -> WAKE).
            self._chime("listening")
            self.sm.dispatch(Event.WAKE_DETECTED)
            if self._mic_flush is not None:
                self._mic_flush()
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
