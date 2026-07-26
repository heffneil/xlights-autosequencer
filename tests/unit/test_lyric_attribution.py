"""Tests for annotated-lyric parsing and timed-word attribution (synthetic text)."""
from __future__ import annotations

from src.analyzer.lyric_attribution import (
    attribute_timed_words,
    parse_annotated_lyrics,
)

SAMPLE = """\
[Intro]
La la

[Verse 1: Alpha]
red green blue

[Chorus: Alpha & Beta]
one two three

[Verse 2: Beta]
cat dog (woof woof)
"""


class TestParse:
    def test_singers_first_appearance_order(self):
        p = parse_annotated_lyrics(SAMPLE)
        assert p.singers == ["Alpha", "Beta"]

    def test_intro_words_are_backing(self):
        p = parse_annotated_lyrics(SAMPLE)
        la = [w for w in p.words if w.text.lower() == "la"]
        assert la and all(w.backing and not w.singers for w in la)

    def test_verse_words_attributed_to_single_singer(self):
        p = parse_annotated_lyrics(SAMPLE)
        red = next(w for w in p.words if w.text == "red")
        assert red.singers == frozenset({"Alpha"}) and not red.backing

    def test_chorus_words_attributed_to_both(self):
        p = parse_annotated_lyrics(SAMPLE)
        one = next(w for w in p.words if w.text == "one")
        assert one.singers == frozenset({"Alpha", "Beta"})

    def test_parenthetical_adlibs_are_backing(self):
        p = parse_annotated_lyrics(SAMPLE)
        woof = [w for w in p.words if w.text.lower() == "woof"]
        assert woof and all(w.backing for w in woof)
        cat = next(w for w in p.words if w.text == "cat")
        assert cat.singers == frozenset({"Beta"}) and not cat.backing


class TestAttribute:
    def _timed(self, *words):
        return [{"label": w, "start_ms": i * 100, "end_ms": i * 100 + 90}
                for i, w in enumerate(words)]

    def test_exact_match_transfers_singers(self):
        p = parse_annotated_lyrics(SAMPLE)
        timed = self._timed("RED", "GREEN", "BLUE", "ONE", "TWO", "THREE")
        att = attribute_timed_words(timed, p)
        assert att[0].singers == frozenset({"Alpha"})       # red
        assert att[3].singers == frozenset({"Alpha", "Beta"})  # one (chorus)

    def test_asr_extra_word_inherits_neighbour(self):
        p = parse_annotated_lyrics(SAMPLE)
        # ASR inserts a bogus "uh" between verse-1 words; it should inherit Alpha.
        timed = self._timed("RED", "UH", "GREEN", "BLUE")
        att = attribute_timed_words(timed, p)
        assert att[1].label == "UH"
        assert att[1].singers == frozenset({"Alpha"})

    def test_backing_flag_transfers(self):
        p = parse_annotated_lyrics(SAMPLE)
        timed = self._timed("LA", "LA", "RED")
        att = attribute_timed_words(timed, p)
        assert att[0].backing is True
        assert att[2].backing is False and att[2].singers == frozenset({"Alpha"})

    def test_all_words_attributed(self):
        p = parse_annotated_lyrics(SAMPLE)
        timed = self._timed("RED", "GREEN", "ONE", "CAT", "DOG")
        att = attribute_timed_words(timed, p)
        assert len(att) == len(timed)
        assert all(isinstance(a.singers, frozenset) for a in att)


from src.analyzer.lyric_attribution import apply_to_marks


class TestApplyToMarks:
    def _words(self, *specs):
        return [{"label": l, "start_ms": s, "end_ms": e} for l, s, e in specs]

    def test_sets_singers_and_backing_on_words(self):
        words = self._words(("LA", 0, 100), ("RED", 200, 300), ("ONE", 400, 500))
        phon = [{"label": "AI", "start_ms": 210, "end_ms": 290}]  # inside RED
        apply_to_marks(words, phon, SAMPLE)
        by = {w["label"]: w for w in words}
        assert by["LA"]["backing"] is True and by["LA"]["singers"] == []
        assert by["RED"]["singers"] == ["Alpha"] and by["RED"]["backing"] is False
        assert set(by["ONE"]["singers"]) == {"Alpha", "Beta"}

    def test_phoneme_inherits_containing_word(self):
        words = self._words(("RED", 200, 300), ("ONE", 400, 500))
        phon = [{"label": "etc", "start_ms": 210, "end_ms": 250},
                {"label": "AI", "start_ms": 410, "end_ms": 450}]
        apply_to_marks(words, phon, SAMPLE)
        assert phon[0]["singers"] == ["Alpha"]                 # inside RED
        assert set(phon[1]["singers"]) == {"Alpha", "Beta"}    # inside ONE


from src.analyzer.lyric_attribution import group_marks_by_named_singer


class TestGroupByNamedSinger:
    def _m(self, label, singers, backing=False):
        return {"label": label, "start_ms": 0, "end_ms": 10, "singers": singers, "backing": backing}

    def test_multi_membership_backing_and_order(self):
        marks = [self._m("A", ["Blake"]), self._m("B", ["Gwen"]),
                 self._m("C", ["Blake", "Gwen"]), self._m("ooh", [], backing=True)]
        groups = group_marks_by_named_singer(marks)
        assert [n for n, _ in groups] == ["Blake", "Gwen", "Backing"]
        d = {n: [m["label"] for m in ms] for n, ms in groups}
        assert d["Blake"] == ["A", "C"] and d["Gwen"] == ["B", "C"] and d["Backing"] == ["ooh"]

    def test_no_backing_track_when_none(self):
        groups = group_marks_by_named_singer([self._m("A", ["Blake"])])
        assert [n for n, _ in groups] == ["Blake"]


BOTH_SAMPLE = """\
[Verse 1: Alpha]
one two
[Chorus: Alpha & Beta]
three four
[Verse 2: Alpha, Both, (Beta)]
five six
"""


class TestBothAndParenHeaders:
    def test_both_expands_and_parens_stripped(self):
        p = parse_annotated_lyrics(BOTH_SAMPLE)
        assert p.singers == ["Alpha", "Beta"]        # no "Both" / "(Beta)" junk names
        five = next(w for w in p.words if w.text == "five")
        assert five.singers == frozenset({"Alpha", "Beta"})   # Both -> Alpha & Beta


STYLED_SAMPLE = """\
[Verse 1: Elton John & *Kiki Dee*]
Don't go *breaking my heart*
[Chorus: Alpha & **Beta**]
plain **boldword** tail
[Verse 2: Solo (*I love you*)]
words here
"""


class TestStyledMarkup:
    def test_italic_words_route_to_italic_role(self):
        p = parse_annotated_lyrics(STYLED_SAMPLE)
        dont = next(w for w in p.words if w.text.lower().startswith("don"))
        assert dont.singers == frozenset({"Elton John"})   # plain -> plain role
        heart = next(w for w in p.words if w.text == "heart")
        assert heart.singers == frozenset({"Kiki Dee"})    # italic -> italic role

    def test_bold_words_route_to_bold_role(self):
        p = parse_annotated_lyrics(STYLED_SAMPLE)
        assert next(w for w in p.words if w.text == "boldword").singers == frozenset({"Beta"})
        assert next(w for w in p.words if w.text == "plain").singers == frozenset({"Alpha"})

    def test_singers_list_has_clean_names(self):
        p = parse_annotated_lyrics(STYLED_SAMPLE)
        assert "Kiki Dee" in p.singers and "*Kiki Dee*" not in p.singers
        assert "Beta" in p.singers and "**Beta**" not in p.singers

    def test_plain_header_ignores_stray_markup(self):
        # No styled roles in header -> section-level (back-compat); stray *two* still {A,B}
        p = parse_annotated_lyrics("[Chorus: A & B]\none *two* three\n")
        assert all(w.singers == frozenset({"A", "B"}) for w in p.words)

    def test_italic_paren_is_backing(self):
        # *(...)* italic-paren ad-lib -> backing
        p = parse_annotated_lyrics("[Verse: A]\nhello *(oh yeah)* world\n")
        oh = next(w for w in p.words if w.text == "oh")
        assert oh.backing is True and oh.singers == frozenset()
        assert next(w for w in p.words if w.text == "hello").singers == frozenset({"A"})
