"""Unit tests for the AutoLyrixAlign HTTP wrapper's pure helpers.

The wrapper lives under ``deploy/aligner`` (it ships inside a different
container than the app), so it is loaded by path rather than imported as part
of the ``src`` package.
"""
import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "aligner" / "align_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("align_server", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


align_server = _load()


class TestStripAnnotationHeaders:
    def test_drops_singer_headers(self):
        text = "[Verse 1: JC Chasez]\nThe first Noel\n[Chorus: All]\nNoel, Noel"
        assert align_server.strip_annotation_headers(text) == "The first Noel\nNoel, Noel"

    def test_header_words_do_not_leak(self):
        # The bug this guards: "chasez"/"verse" being aligned as if sung.
        out = align_server.strip_annotation_headers("[Verse 1: JC Chasez]\nNoel")
        assert "Chasez" not in out and "Verse" not in out

    def test_keeps_bracketed_text_that_is_not_a_whole_line(self):
        # Inline brackets are part of the lyric line, not a section header.
        text = "Noel [x2] Noel"
        assert align_server.strip_annotation_headers(text) == text

    def test_blank_lines_and_plain_text_survive(self):
        text = "Noel, Noel\n\nBorn is the King"
        assert align_server.strip_annotation_headers(text) == text

    def test_handles_ampersand_and_multi_singer_headers(self):
        text = "[Chorus: JC Chasez & Justin Timberlake]\nNoel"
        assert align_server.strip_annotation_headers(text) == "Noel"


class TestParseAlignment:
    def test_parses_seconds_into_ms(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("18.57 20.67 NOEL\n20.67 22.44 NOEL\n")
        marks = align_server.parse_alignment(str(p))
        assert marks == [
            {"label": "NOEL", "start_ms": 18570, "end_ms": 20670},
            {"label": "NOEL", "start_ms": 20670, "end_ms": 22440},
        ]

    def test_rounds_float_noise(self, tmp_path):
        # The aligner emits values like 30.060000000000002.
        p = tmp_path / "a.txt"
        p.write_text("30.060000000000002 31.2 THE\n")
        assert align_server.parse_alignment(str(p))[0]["start_ms"] == 30060

    def test_skips_malformed_and_short_lines(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("bad line\n\n1.0 2.0 OK\nalso bad\n")
        marks = align_server.parse_alignment(str(p))
        assert [m["label"] for m in marks] == ["OK"]

    def test_empty_file_yields_no_marks(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("")
        assert align_server.parse_alignment(str(p)) == []
