"""Per-singer lyric tracks: align each singer's lines, then merge and group.

The flow this implements
-----------------------
You give one lyric text per singer — each containing only that singer's lines,
which is how sites like Genius already annotate duets. Each text is aligned
independently against the full mix by :mod:`aligner_client` (AutoLyrixAlign can
reject audio it has no words for, so a singer's silent verses stay empty).

Words the singers share — a full-cast chorus — come back from separate
alignment runs with the same timing, so they are merged into a single mark
carrying every singer who sings it. That way a shared word rides on each
singer's track without being duplicated in the combined lyric track.

Phonemes are derived locally from the aligned words via cmudict, exactly as the
WhisperX path does. Singing faces need the phoneme layer, and the aligner only
returns words.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

log = logging.getLogger(__name__)

BACKING_TRACK = "Backing"
# Unison words come back within a few frames of each other across runs (the
# aligner's frame shift is 30 ms), so allow two frames of slack when merging.
DEFAULT_MERGE_TOLERANCE_MS = 60


def merge_singer_words(
    per_singer: dict[str, list[dict]],
    tolerance_ms: int = DEFAULT_MERGE_TOLERANCE_MS,
) -> list[dict]:
    """Merge per-singer word lists into one attributed, time-ordered list.

    Two marks are the same sung word when the label matches and the starts are
    within ``tolerance_ms``. The merged mark spans the union of their times and
    lists every singer involved.
    """
    by_label: dict[str, list[dict]] = {}
    merged: list[dict] = []
    # Process singers in the order given so `singers` lists read predictably.
    for name, marks in per_singer.items():
        for m in sorted(marks, key=lambda w: w["start_ms"]):
            label = str(m["label"]).strip()
            key = label.upper()
            target = None
            for cand in by_label.get(key, ()):
                if abs(cand["start_ms"] - m["start_ms"]) <= tolerance_ms:
                    target = cand
                    break
            if target is None:
                target = {
                    "label": label,
                    "start_ms": int(m["start_ms"]),
                    "end_ms": int(m["end_ms"]),
                    "singers": [name],
                }
                by_label.setdefault(key, []).append(target)
                merged.append(target)
            else:
                target["start_ms"] = min(target["start_ms"], int(m["start_ms"]))
                target["end_ms"] = max(target["end_ms"], int(m["end_ms"]))
                if name not in target["singers"]:
                    target["singers"].append(name)
    merged.sort(key=lambda w: (w["start_ms"], w["end_ms"]))
    return merged


def derive_phonemes(words: Iterable[dict], cmu_dict: Optional[dict] = None) -> list[dict]:
    """Expand attributed word marks into Papagayo phoneme marks.

    Each phoneme inherits its word's ``singers``, so per-singer grouping works
    on both layers.
    """
    from src.analyzer.phonemes import (
        distribute_phoneme_timing,
        get_cmu_dict,
        word_to_papagayo,
    )

    if cmu_dict is None:
        cmu_dict = get_cmu_dict()
    out: list[dict] = []
    for w in words:
        labels = word_to_papagayo(str(w["label"]), cmu_dict)
        for pm in distribute_phoneme_timing(labels, int(w["start_ms"]), int(w["end_ms"])):
            mark = pm.to_dict()
            if w.get("singers") is not None:
                mark["singers"] = list(w["singers"])
            if w.get("backing"):
                mark["backing"] = True
            out.append(mark)
    out.sort(key=lambda m: (m["start_ms"], m["end_ms"]))
    return out


def group_marks_by_named_singer(marks: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    """Group attributed marks into named buckets, plus a Backing bucket last.

    A mark listing several singers lands in each of their buckets. Named
    singers keep first-appearance order; ``"Backing"`` is appended only when
    backing marks exist.
    """
    order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    backing: list[dict] = []
    for m in marks:
        if m.get("backing"):
            backing.append(m)
            continue
        for name in (m.get("singers") or []):
            if name not in buckets:
                buckets[name] = []
                order.append(name)
            buckets[name].append(m)
    result = [(name, buckets[name]) for name in order]
    if backing:
        result.append((BACKING_TRACK, backing))
    return result


def align_singer_parts(
    audio_path: str,
    parts: list[dict],
    *,
    url: Optional[str] = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Align every singer's lyric text and return ``(words, phonemes, warnings)``.

    ``parts`` is ``[{"name": "JC", "lyrics": "..."}, ...]``. A part that fails
    to align is skipped with a warning rather than sinking the whole run — one
    bad paste should not cost you the other singers' tracks.
    """
    from src.analyzer import aligner_client

    if not parts:
        return [], [], ["No singer parts were provided."]

    warnings: list[str] = []
    per_singer: dict[str, list[dict]] = {}
    for part in parts:
        name = (part.get("name") or "").strip()
        lyrics = part.get("lyrics") or ""
        if not name:
            warnings.append("Skipped a singer part with no name.")
            continue
        if not lyrics.strip():
            warnings.append(f"Skipped {name}: no lyrics provided.")
            continue
        try:
            marks = aligner_client.align_lyrics(audio_path, lyrics, url=url)
        except aligner_client.AlignerError as exc:
            log.warning("alignment failed for %s: %s", name, exc)
            warnings.append(f"Alignment failed for {name}: {exc}")
            continue
        if name in per_singer:
            warnings.append(f"Duplicate singer name {name!r}; parts were combined.")
            per_singer[name].extend(marks)
        else:
            per_singer[name] = marks
        log.info("aligned %s: %d words", name, len(marks))

    if not per_singer:
        return [], [], warnings or ["No singer part could be aligned."]

    words = merge_singer_words(per_singer)
    phonemes = derive_phonemes(words)
    shared = sum(1 for w in words if len(w.get("singers") or []) > 1)
    log.info("per-singer merge: %d words (%d shared), %d phonemes across %d singers",
             len(words), shared, len(phonemes), len(per_singer))
    return words, phonemes, warnings
