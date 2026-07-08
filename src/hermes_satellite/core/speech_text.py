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
