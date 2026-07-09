"""Make agent replies speakable.

LLM replies arrive as markdown; a TTS engine reads the markup literally
("asterisk asterisk"). This module flattens markdown into plain spoken prose.
It is the second line of defense — the first is ``hermes.system_prompt``
asking the agent for plain prose — so whatever slips through never reaches
the speaker.
"""

from __future__ import annotations

import re

# Fenced code is unspeakable; replace with a spoken placeholder.
_CODE_BLOCK = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<![\w_])_([^_\n]+)_(?![\w_])")
_HEADER = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*(?:[-*•+]|\d+[.)])[ \t]+")
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # symbols, pictographs, emoticons
    "←-⇿"          # arrows
    "⌀-➿"          # misc technical .. dingbats
    "⬀-⯿"
    "️"                 # variation selector
    "]+"
)
_WHITESPACE = re.compile(r"[ \t]+")

_SENTENCE_END = (".", "!", "?", ":", ";", ",")


def make_speakable(text: str) -> str:
    """Flatten markdown-ish agent output into plain prose for TTS."""
    if not text:
        return ""

    text = _CODE_BLOCK.sub(" Code omitted. ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _HEADER.sub("", text)
    text = _BLOCKQUOTE.sub("", text)

    # Flatten list items into sentences: strip the marker and make sure each
    # item ends like a sentence so items don't run together when joined.
    lines = []
    for line in text.splitlines():
        stripped = _BULLET.sub("", line)
        if stripped != line:
            stripped = stripped.rstrip()
            if stripped and not stripped.endswith(_SENTENCE_END):
                stripped += "."
        lines.append(stripped)
    text = " ".join(line.strip() for line in lines if line.strip())

    # Table pipes and leftover markup characters read terribly.
    text = text.replace("|", " ")
    text = text.replace("*", " ").replace("#", " ")
    text = _EMOJI.sub("", text)

    return _WHITESPACE.sub(" ", text).strip()


# --- streaming sentence chunker ---------------------------------------------

# A boundary is .!? followed by whitespace — but not inside a decimal number
# ("2.47 PM") and not after a single capital initial ("J. Smith").
_BOUNDARY = re.compile(r"(?<!\d)(?<![A-Z])[.!?]+(?=\s)")

# Sentences shorter than this merge forward so TTS doesn't sound choppy.
MIN_CHUNK_CHARS = 20


def iter_sentences(deltas):
    """Group a stream of text deltas into speakable sentence chunks.

    Yields complete sentences as soon as their boundary arrives, merging
    fragments shorter than :data:`MIN_CHUNK_CHARS` into the next chunk.
    Whatever remains when the stream ends is yielded last.
    """
    buffer = ""
    pending = ""  # short fragment held back to merge forward
    for delta in deltas:
        buffer += delta
        while True:
            match = _BOUNDARY.search(buffer)
            if not match:
                break
            sentence = buffer[: match.end()]
            buffer = buffer[match.end():].lstrip()
            candidate = (pending + " " + sentence).strip() if pending else sentence.strip()
            if len(candidate) < MIN_CHUNK_CHARS:
                pending = candidate
                continue
            pending = ""
            yield candidate
    tail = (pending + " " + buffer).strip() if pending else buffer.strip()
    if tail:
        yield tail


# Spoken commands that mean "end the conversation": after a barge-in or in a
# follow-up window, these short-circuit the turn (no Hermes round-trip).
# Exact-match against the normalized transcript, so ordinary sentences that
# merely contain "stop" can't misfire.
_STOP_PHRASES = frozenset({
    "stop", "jarvis stop", "hey jarvis stop", "stop talking", "stop it",
    "cancel", "cancel that", "never mind", "nevermind", "be quiet", "shut up",
    "thats all",
})


def is_stop_command(text: str) -> bool:
    """True when the transcript is a stop command and nothing else."""
    norm = re.sub(r"[^a-z ]+", " ", text.lower().replace("'", ""))
    return " ".join(norm.split()) in _STOP_PHRASES
