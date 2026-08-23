"""Per-singer lyric parts: paste one lyric text per singer, get one track each.

You name each singer and paste only their lines. Every part is aligned
separately against the mix by the AutoLyrixAlign service (see
``deploy/aligner``), which — unlike forced alignment — leaves a singer's silent
verses empty instead of smearing their words across the whole song.

The aligned result is cached per song and consumed by the next Analyze run
exactly like an uploaded .xtiming override, so WhisperX is skipped entirely.

Alignment takes roughly a minute per singer and the service processes one
request at a time, so POST here is deliberately synchronous — the caller waits.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from flask import jsonify, request

from . import api_v1
from src.review.storage.library import load_library

log = logging.getLogger(__name__)

# song_id -> (words, phonemes, part_summaries)
_singer_cache: dict[str, tuple[list[dict], list[dict], list[dict]]] = {}
_singer_cache_lock = threading.Lock()
# One alignment run at a time per process: the engine is single-threaded and
# ~13 GB resident, so overlapping runs would just queue and risk timeouts.
_align_lock = threading.Lock()

MAX_SINGERS = 12


def get_singer_override(song_id: str):
    """Return cached ``(words, phonemes, parts)`` for this song, or None."""
    with _singer_cache_lock:
        return _singer_cache.get(song_id)


def _summarise(words: list[dict], phonemes: list[dict], parts: list[dict]) -> dict:
    shared = sum(1 for w in words if len(w.get("singers") or []) > 1)
    return {
        "found": bool(words),
        "word_count": len(words),
        "phoneme_count": len(phonemes),
        "shared_word_count": shared,
        "singers": parts,
    }


@api_v1.route("/lyrics/aligner/health", methods=["GET"])
def aligner_health():
    """Report whether the per-singer aligner service is reachable.

    Lets the UI disable the per-singer panel with a useful message instead of
    failing only after the user has pasted several verses.
    """
    from src.analyzer import aligner_client

    return jsonify({
        "available": aligner_client.is_available(),
        "url": aligner_client.base_url(),
    }), 200


@api_v1.route("/songs/<song_id>/lyrics/singers", methods=["GET"])
def get_singer_parts(song_id: str):
    """Report the per-singer alignment currently queued for this song."""
    cached = get_singer_override(song_id)
    if cached is None:
        return jsonify(_summarise([], [], [])), 200
    words, phonemes, parts = cached
    return jsonify(_summarise(words, phonemes, parts)), 200


@api_v1.route("/songs/<song_id>/lyrics/singers", methods=["DELETE"])
def clear_singer_parts(song_id: str):
    """Discard the queued per-singer alignment for this song."""
    with _singer_cache_lock:
        _singer_cache.pop(song_id, None)
    return jsonify(_summarise([], [], [])), 200


@api_v1.route("/songs/<song_id>/lyrics/singers", methods=["POST"])
def align_singer_parts_route(song_id: str):
    """Align one lyric text per singer and queue the result for Analyze.

    Body: ``{"parts": [{"name": "JC", "lyrics": "..."}, ...]}``

    Runs synchronously — expect about a minute per singer.
    """
    lib = load_library()
    song = next((s for s in lib["songs"] if s["song_id"] == song_id), None)
    if song is None:
        return jsonify({"error": {"code": "song_not_found",
                                  "message": "Song not found"}}), 404

    source_paths = song.get("source_paths") or []
    source_path = source_paths[0] if source_paths else ""
    if not source_path or not Path(source_path).exists():
        return jsonify({"error": {"code": "audio_not_found",
                                  "message": "This song's audio file is missing"}}), 400

    body = request.get_json(silent=True) or {}
    parts = body.get("parts")
    if not isinstance(parts, list) or not parts:
        return jsonify({"error": {"code": "missing_parts",
                                  "message": "parts must be a non-empty array"}}), 400
    if len(parts) > MAX_SINGERS:
        return jsonify({"error": {"code": "too_many_singers",
                                  "message": f"at most {MAX_SINGERS} singers"}}), 400

    cleaned: list[dict] = []
    seen: set[str] = set()
    for raw in parts:
        if not isinstance(raw, dict):
            return jsonify({"error": {"code": "invalid_part",
                                      "message": "each part must be an object"}}), 422
        name = str(raw.get("name") or "").strip()
        lyrics = str(raw.get("lyrics") or "")
        if not name or not lyrics.strip():
            return jsonify({"error": {"code": "invalid_part",
                                      "message": "each part needs a name and lyrics"}}), 422
        key = name.lower()
        if key in seen:
            return jsonify({"error": {"code": "duplicate_singer",
                                      "message": f"duplicate singer name: {name}"}}), 422
        seen.add(key)
        cleaned.append({"name": name, "lyrics": lyrics})

    from src.analyzer import aligner_client, per_singer

    if not aligner_client.is_available():
        return jsonify({"error": {
            "code": "aligner_unavailable",
            "message": (f"The alignment service at {aligner_client.base_url()} is not "
                        "responding. Start the xonset-aligner container "
                        "(see deploy/aligner/README.md)."),
        }}), 503

    if not _align_lock.acquire(blocking=False):
        return jsonify({"error": {"code": "aligner_busy",
                                  "message": "An alignment run is already in progress"}}), 409
    try:
        words, phonemes, warnings = per_singer.align_singer_parts(source_path, cleaned)
    except Exception as exc:  # noqa: BLE001 - surface any engine failure as 502
        log.exception("per-singer alignment failed")
        return jsonify({"error": {"code": "align_failed", "message": str(exc)}}), 502
    finally:
        _align_lock.release()

    if not words:
        return jsonify({"error": {
            "code": "no_alignment",
            "message": "; ".join(warnings) or "No words were aligned",
        }}), 502

    grouped = dict(per_singer.group_marks_by_named_singer(words))
    summaries = [{"name": p["name"], "word_count": len(grouped.get(p["name"], []))}
                 for p in cleaned]
    with _singer_cache_lock:
        _singer_cache[song_id] = (words, phonemes, summaries)

    payload = _summarise(words, phonemes, summaries)
    payload["warnings"] = warnings
    payload["tracks"] = [f"Lyrics - {p['name']}" for p in cleaned]
    return jsonify(payload), 200
