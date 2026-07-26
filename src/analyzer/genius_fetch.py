"""Fetch part-annotated lyrics from a Genius song URL.

Genius has no public lyrics API (metadata only) and blocks naive requests, so
this is a best-effort scrape of the song page: it pulls the
``data-lyrics-container`` blocks and preserves the markup our attribution needs
— ``[Section: Singer]`` headers, ``<i>`` → ``*italic*`` and ``<b>`` → ``**bold**``
(the multi-vocalist styling), and ``<br>`` → line breaks.

Borrowed in spirit from wease944/xLights-SequenceStarter's Genius import.
Never raises: returns ``None`` on any network/parse failure so callers can fall
back to a manual paste.
"""
from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class _GeniusExtractor(HTMLParser):
    """Walk a Genius page, emitting annotated lyric text from the lyric blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # decodes &amp; etc. in handle_data
        self._in = False
        self._depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self._in:
            if dict(attrs).get("data-lyrics-container") == "true":
                self._in = True
                self._depth = 1
            return
        if tag == "br":
            self.parts.append("\n")
            return
        self._depth += 1
        if tag in ("i", "em"):
            self.parts.append("*")
        elif tag in ("b", "strong"):
            self.parts.append("**")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._in and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._in or tag == "br":
            return
        if tag in ("i", "em"):
            self.parts.append("*")
        elif tag in ("b", "strong"):
            self.parts.append("**")
        self._depth -= 1
        if self._depth <= 0:
            self._in = False

    def handle_data(self, data: str) -> None:
        if self._in:
            self.parts.append(data)


def parse_genius_html(html: str) -> str:
    """Extract annotated lyrics text from a Genius song-page HTML string."""
    ex = _GeniusExtractor()
    ex.feed(html)
    text = "".join(ex.parts)
    # Tidy: trim trailing spaces per line, drop the "You might also like" promo
    # line Genius injects, and collapse blank-line runs.
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip().lower() != "you might also like"]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    # Genius prepends a "N Contributors … Read More" blurb (glued to the first
    # section header) and appends a "…Embed" tail. When section headers exist,
    # trim everything before the first one and drop the trailing Embed marker.
    m = re.search(r"\[[^\]\n]+\]", out)
    if m and m.start() > 0:
        out = out[m.start():]
    out = re.sub(r"\s*\d*Embed\s*$", "", out).strip()
    return out


def fetch_genius_lyrics(url: str, timeout: float = 15.0) -> str | None:
    """Return part-annotated lyrics for a Genius song URL, or None on failure."""
    if not url or "genius.com" not in url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        text = parse_genius_html(raw.decode("utf-8", "replace"))
        return text or None
    except Exception:
        return None
