"""Tests for per-singer lyric alignment, merging and grouping."""
import pytest

from src.analyzer import per_singer


class TestMergeSingerWords:
    def test_shared_word_merges_into_one_mark_listing_both(self):
        merged = per_singer.merge_singer_words({
            "JC":     [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500}],
            "Justin": [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500}],
        })
        assert len(merged) == 1
        assert merged[0]["singers"] == ["JC", "Justin"]

    def test_near_identical_starts_merge_within_tolerance(self):
        # Separate alignment runs land unison words a frame or two apart.
        merged = per_singer.merge_singer_words({
            "JC":     [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500}],
            "Justin": [{"label": "NOEL", "start_ms": 1030, "end_ms": 1560}],
        })
        assert len(merged) == 1
        # Merged mark spans the union of both.
        assert (merged[0]["start_ms"], merged[0]["end_ms"]) == (1000, 1560)

    def test_same_word_far_apart_stays_separate(self):
        merged = per_singer.merge_singer_words({
            "JC": [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500},
                   {"label": "NOEL", "start_ms": 9000, "end_ms": 9500}],
        })
        assert len(merged) == 2

    def test_solo_words_keep_only_their_singer(self):
        merged = per_singer.merge_singer_words({
            "JC":     [{"label": "SHEPHERDS", "start_ms": 4000, "end_ms": 4500}],
            "Justin": [{"label": "STAR", "start_ms": 8000, "end_ms": 8500}],
        })
        by_label = {m["label"]: m["singers"] for m in merged}
        assert by_label == {"SHEPHERDS": ["JC"], "STAR": ["Justin"]}

    def test_result_is_time_ordered(self):
        merged = per_singer.merge_singer_words({
            "JC": [{"label": "C", "start_ms": 3000, "end_ms": 3100},
                   {"label": "A", "start_ms": 1000, "end_ms": 1100}],
            "Justin": [{"label": "B", "start_ms": 2000, "end_ms": 2100}],
        })
        assert [m["label"] for m in merged] == ["A", "B", "C"]

    def test_case_differences_still_merge(self):
        merged = per_singer.merge_singer_words({
            "JC":     [{"label": "Noel", "start_ms": 1000, "end_ms": 1500}],
            "Justin": [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500}],
        })
        assert len(merged) == 1
        assert sorted(merged[0]["singers"]) == ["JC", "Justin"]

    def test_empty_input_yields_nothing(self):
        assert per_singer.merge_singer_words({}) == []


class TestGroupMarksByNamedSinger:
    def test_shared_mark_appears_in_every_singers_bucket(self):
        marks = [{"label": "NOEL", "start_ms": 0, "end_ms": 1, "singers": ["JC", "Justin"]}]
        groups = dict(per_singer.group_marks_by_named_singer(marks))
        assert set(groups) == {"JC", "Justin"}
        assert groups["JC"] == groups["Justin"] == marks

    def test_named_singers_keep_first_appearance_order(self):
        marks = [
            {"label": "A", "start_ms": 0, "end_ms": 1, "singers": ["Chris"]},
            {"label": "B", "start_ms": 2, "end_ms": 3, "singers": ["JC"]},
        ]
        assert [n for n, _ in per_singer.group_marks_by_named_singer(marks)] == ["Chris", "JC"]

    def test_backing_bucket_is_last_and_only_when_present(self):
        marks = [
            {"label": "A", "start_ms": 0, "end_ms": 1, "singers": ["JC"]},
            {"label": "OOH", "start_ms": 2, "end_ms": 3, "backing": True},
        ]
        names = [n for n, _ in per_singer.group_marks_by_named_singer(marks)]
        assert names == ["JC", per_singer.BACKING_TRACK]

    def test_no_backing_bucket_when_no_backing_marks(self):
        marks = [{"label": "A", "start_ms": 0, "end_ms": 1, "singers": ["JC"]}]
        names = [n for n, _ in per_singer.group_marks_by_named_singer(marks)]
        assert names == ["JC"]

    def test_unattributed_marks_are_dropped(self):
        # A mark with neither singers nor backing belongs to no track.
        assert per_singer.group_marks_by_named_singer(
            [{"label": "A", "start_ms": 0, "end_ms": 1}]) == []


class TestDerivePhonemes:
    def test_phonemes_inherit_singers(self):
        words = [{"label": "NOEL", "start_ms": 1000, "end_ms": 1600,
                  "singers": ["JC", "Justin"]}]
        phons = per_singer.derive_phonemes(words)
        assert phons, "expected phoneme marks"
        assert all(p["singers"] == ["JC", "Justin"] for p in phons)

    def test_phonemes_stay_inside_the_word(self):
        words = [{"label": "STAR", "start_ms": 2000, "end_ms": 2500, "singers": ["JC"]}]
        phons = per_singer.derive_phonemes(words)
        assert min(p["start_ms"] for p in phons) >= 2000
        assert max(p["end_ms"] for p in phons) <= 2500

    def test_output_is_time_ordered(self):
        words = [
            {"label": "STAR", "start_ms": 5000, "end_ms": 5400, "singers": ["JC"]},
            {"label": "NOEL", "start_ms": 1000, "end_ms": 1400, "singers": ["JC"]},
        ]
        phons = per_singer.derive_phonemes(words)
        starts = [p["start_ms"] for p in phons]
        assert starts == sorted(starts)


class TestAlignSingerParts:
    def test_aligns_each_part_and_merges(self, monkeypatch):
        calls = []

        def fake_align(audio_path, lyrics, **kw):
            calls.append(lyrics.strip())
            if "shepherds" in lyrics.lower():
                return [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500},
                        {"label": "SHEPHERDS", "start_ms": 4000, "end_ms": 4500}]
            return [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500},
                    {"label": "STAR", "start_ms": 8000, "end_ms": 8500}]

        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "align_lyrics", fake_align)

        words, phonemes, warnings = per_singer.align_singer_parts("/tmp/x.mp3", [
            {"name": "JC", "lyrics": "Noel shepherds"},
            {"name": "Justin", "lyrics": "Noel star"},
        ])
        assert len(calls) == 2
        assert warnings == []
        shared = [w for w in words if len(w["singers"]) > 1]
        assert [w["label"] for w in shared] == ["NOEL"]
        assert phonemes

    def test_one_failing_singer_does_not_sink_the_rest(self, monkeypatch):
        from src.analyzer import aligner_client

        def fake_align(audio_path, lyrics, **kw):
            if "boom" in lyrics:
                raise aligner_client.AlignerError("engine exploded")
            return [{"label": "NOEL", "start_ms": 1000, "end_ms": 1500}]

        monkeypatch.setattr(aligner_client, "align_lyrics", fake_align)
        words, _phon, warnings = per_singer.align_singer_parts("/tmp/x.mp3", [
            {"name": "JC", "lyrics": "noel"},
            {"name": "Justin", "lyrics": "boom"},
        ])
        assert [w["singers"] for w in words] == [["JC"]]
        assert any("Justin" in w for w in warnings)

    def test_parts_missing_name_or_lyrics_are_skipped(self, monkeypatch):
        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "align_lyrics",
                            lambda *a, **k: [{"label": "A", "start_ms": 0, "end_ms": 1}])
        words, _p, warnings = per_singer.align_singer_parts("/tmp/x.mp3", [
            {"name": "", "lyrics": "x"},
            {"name": "JC", "lyrics": "   "},
            {"name": "Chris", "lyrics": "a"},
        ])
        assert [w["singers"] for w in words] == [["Chris"]]
        assert len(warnings) == 2

    def test_no_parts_returns_warning(self):
        words, phons, warnings = per_singer.align_singer_parts("/tmp/x.mp3", [])
        assert (words, phons) == ([], [])
        assert warnings
