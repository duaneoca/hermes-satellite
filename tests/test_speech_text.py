"""Markdown-to-speakable sanitizer."""

from hermes_satellite.core.speech_text import make_speakable


def test_bold_italic_unwrapped():
    assert make_speakable("**Sure!** That is *quite* right, __sir__.") == \
        "Sure! That is quite right, sir."


def test_bullets_become_sentences():
    text = "Here's the plan:\n- First, warm up\n* Second, launch\n1. Third, profit"
    assert make_speakable(text) == \
        "Here's the plan: First, warm up. Second, launch. Third, profit."


def test_code_block_replaced_inline_code_unwrapped():
    text = "Run `uptime` like this:\n```bash\nuptime -p\n```\nDone."
    out = make_speakable(text)
    assert "uptime" in out and "```" not in out
    assert "Code omitted." in out


def test_links_and_images_reduced_to_text():
    assert make_speakable("See [the docs](http://x/y) and ![a chart](img.png)") == \
        "See the docs and a chart"


def test_headers_blockquotes_tables_stripped():
    text = "# Status\n> All good\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = make_speakable(text)
    for ch in "#>|":
        assert ch not in out
    assert "Status" in out and "All good" in out


def test_emoji_removed():
    assert make_speakable("All systems nominal 🚀✨") == "All systems nominal"


def test_stray_asterisks_never_survive():
    out = make_speakable("rating: ***** and 3 * 4")
    assert "*" not in out


def test_plain_prose_untouched():
    text = "It is 4 o'clock, sir. Shall I close the garage?"
    assert make_speakable(text) == text


def test_empty():
    assert make_speakable("") == ""


# --- sentence chunker ---------------------------------------------------

from hermes_satellite.core.speech_text import iter_sentences


def test_chunker_emits_sentences_across_delta_boundaries():
    deltas = ["It is currently 2.", "47 PM Pacific Time. ",
              "Tomorrow looks sunny and warm", ". Enjoy your afternoon!"]
    out = list(iter_sentences(deltas))
    assert out[0] == "It is currently 2.47 PM Pacific Time."
    assert out[1] == "Tomorrow looks sunny and warm."
    assert out[2] == "Enjoy your afternoon!"


def test_chunker_merges_short_fragments_forward():
    out = list(iter_sentences(["Yes. It will rain heavily this evening. "]))
    assert out == ["Yes. It will rain heavily this evening."]


def test_chunker_tail_without_punctuation_is_flushed():
    assert list(iter_sentences(["no punctuation at all"])) == \
        ["no punctuation at all"]


def test_chunker_empty_stream():
    assert list(iter_sentences([])) == []
