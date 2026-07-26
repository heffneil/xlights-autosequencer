"""Tests for the Genius HTML lyric extractor (parser only; no network)."""
from __future__ import annotations

from src.analyzer.genius_fetch import parse_genius_html, fetch_genius_lyrics

HTML = """\
<html><body>
<div data-lyrics-container="true">[Verse 1: Elton John &amp; <i>Kiki Dee</i>]<br>\
Don't go <i>breaking my heart</i><br>You might also like<br></div>
<div data-lyrics-container="true">[Chorus: A &amp; B]<br>plain <b>bold word</b> tail<br>\
<a href="/x">line in link</a></div>
<div>ignored outside container</div>
</body></html>
"""


class TestParseGeniusHtml:
    def test_preserves_headers(self):
        t = parse_genius_html(HTML)
        assert "[Verse 1: Elton John & *Kiki Dee*]" in t
        assert "[Chorus: A & B]" in t

    def test_italic_and_bold_markup(self):
        t = parse_genius_html(HTML)
        assert "*breaking my heart*" in t
        assert "**bold word**" in t

    def test_keeps_link_text_drops_promo_and_outside(self):
        t = parse_genius_html(HTML)
        assert "line in link" in t
        assert "you might also like" not in t.lower()
        assert "ignored outside container" not in t

    def test_line_breaks(self):
        t = parse_genius_html(HTML)
        # header and first lyric line are on separate lines
        assert t.splitlines()[0].startswith("[Verse 1")


class TestFetchGuard:
    def test_non_genius_url_returns_none(self):
        assert fetch_genius_lyrics("https://example.com/song") is None
        assert fetch_genius_lyrics("") is None
