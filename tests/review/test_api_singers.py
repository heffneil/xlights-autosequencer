"""Tests for the per-singer lyric-parts API."""
import pytest

from src.review.storage.library import load_library, save_library


@pytest.fixture()
def song_with_audio(client, sample_song, tmp_path):
    """Register a song whose source_paths[0] actually exists on disk."""
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00" * 32)
    song = dict(sample_song)
    song["source_paths"] = [str(audio)]
    lib = load_library()
    lib["songs"].append(song)
    save_library(lib)
    return song


@pytest.fixture()
def stub_aligner(monkeypatch):
    """Aligner that is reachable and returns deterministic marks per singer."""
    from src.analyzer import aligner_client
    monkeypatch.setattr(aligner_client, "is_available", lambda *a, **k: True)

    def fake_align(audio_path, lyrics, **kw):
        marks = [{"label": "NOEL", "start_ms": 1000, "end_ms": 1400}]
        if "shepherds" in lyrics.lower():
            marks.append({"label": "SHEPHERDS", "start_ms": 4000, "end_ms": 4400})
        if "star" in lyrics.lower():
            marks.append({"label": "STAR", "start_ms": 9000, "end_ms": 9400})
        return marks

    monkeypatch.setattr(aligner_client, "align_lyrics", fake_align)


@pytest.fixture(autouse=True)
def _clear_cache():
    from src.review.api.v1 import singers as mod
    with mod._singer_cache_lock:
        mod._singer_cache.clear()
    yield
    with mod._singer_cache_lock:
        mod._singer_cache.clear()


class TestAlignerHealth:
    def test_reports_availability_and_url(self, client, monkeypatch):
        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "is_available", lambda *a, **k: False)
        res = client.get("/api/v1/lyrics/aligner/health")
        assert res.status_code == 200
        assert res.get_json()["available"] is False
        assert res.get_json()["url"]


class TestPostSingerParts:
    def test_aligns_and_reports_per_singer_counts(self, client, song_with_audio, stub_aligner):
        res = client.post(
            f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
            json={"parts": [
                {"name": "JC", "lyrics": "Noel shepherds"},
                {"name": "Justin", "lyrics": "Noel star"},
            ]},
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body["found"] is True
        # NOEL is shared, so it merges into a single attributed mark
        assert body["word_count"] == 3
        assert body["shared_word_count"] == 1
        assert body["tracks"] == ["Lyrics - JC", "Lyrics - Justin"]
        counts = {s["name"]: s["word_count"] for s in body["singers"]}
        assert counts == {"JC": 2, "Justin": 2}
        assert body["phoneme_count"] > 0

    def test_result_is_readable_back_and_clearable(self, client, song_with_audio, stub_aligner):
        sid = song_with_audio["song_id"]
        client.post(f"/api/v1/songs/{sid}/lyrics/singers",
                    json={"parts": [{"name": "JC", "lyrics": "Noel"}]})
        assert client.get(f"/api/v1/songs/{sid}/lyrics/singers").get_json()["found"] is True
        assert client.delete(f"/api/v1/songs/{sid}/lyrics/singers").get_json()["found"] is False
        assert client.get(f"/api/v1/songs/{sid}/lyrics/singers").get_json()["found"] is False

    def test_unknown_song_is_404(self, client, stub_aligner):
        res = client.post("/api/v1/songs/deadbeefdeadbeef/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "Noel"}]})
        assert res.status_code == 404

    def test_missing_parts_is_400(self, client, song_with_audio, stub_aligner):
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={})
        assert res.status_code == 400

    @pytest.mark.parametrize("parts", [
        [{"name": "", "lyrics": "Noel"}],
        [{"name": "JC", "lyrics": "   "}],
        ["not-an-object"],
    ])
    def test_malformed_part_is_422(self, client, song_with_audio, stub_aligner, parts):
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": parts})
        assert res.status_code == 422

    def test_duplicate_singer_name_is_422(self, client, song_with_audio, stub_aligner):
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "a"},
                                          {"name": "jc", "lyrics": "b"}]})
        assert res.status_code == 422

    def test_too_many_singers_is_400(self, client, song_with_audio, stub_aligner):
        parts = [{"name": f"S{i}", "lyrics": "Noel"} for i in range(13)]
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": parts})
        assert res.status_code == 400

    def test_aligner_down_is_503_with_actionable_message(self, client, song_with_audio, monkeypatch):
        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "is_available", lambda *a, **k: False)
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "Noel"}]})
        assert res.status_code == 503
        assert "aligner" in res.get_json()["error"]["message"].lower()

    def test_missing_audio_file_is_400(self, client, sample_song, stub_aligner):
        song = dict(sample_song)
        song["song_id"] = "ffffffffffffffff"
        song["source_paths"] = ["/nonexistent/nope.mp3"]
        lib = load_library()
        lib["songs"].append(song)
        save_library(lib)
        res = client.post(f"/api/v1/songs/{song['song_id']}/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "Noel"}]})
        assert res.status_code == 400

    def test_all_singers_failing_is_502(self, client, song_with_audio, monkeypatch):
        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "is_available", lambda *a, **k: True)

        def boom(*a, **k):
            raise aligner_client.AlignerError("engine down")
        monkeypatch.setattr(aligner_client, "align_lyrics", boom)
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "Noel"}]})
        assert res.status_code == 502

    def test_partial_failure_still_returns_the_rest(self, client, song_with_audio, monkeypatch):
        from src.analyzer import aligner_client
        monkeypatch.setattr(aligner_client, "is_available", lambda *a, **k: True)

        def fake(audio_path, lyrics, **kw):
            if "boom" in lyrics:
                raise aligner_client.AlignerError("nope")
            return [{"label": "NOEL", "start_ms": 1000, "end_ms": 1400}]
        monkeypatch.setattr(aligner_client, "align_lyrics", fake)
        res = client.post(f"/api/v1/songs/{song_with_audio['song_id']}/lyrics/singers",
                          json={"parts": [{"name": "JC", "lyrics": "Noel"},
                                          {"name": "Justin", "lyrics": "boom"}]})
        assert res.status_code == 200
        body = res.get_json()
        assert body["word_count"] == 1
        assert any("Justin" in w for w in body["warnings"])
