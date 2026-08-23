"""Client for the self-hosted AutoLyrixAlign service (see ``deploy/aligner``).

Why a service and not a library: AutoLyrixAlign is GPLv3 and this project is
MIT, so the engine runs out-of-process and we speak HTTP to it. It must not be
imported or vendored.

Why not WhisperX for this job: WhisperX force-aligns, which assumes the text
covers the audio. Give it one singer's lines from a multi-singer song and it
spreads them across the whole vocal timeline instead of leaving the other
singers' parts empty. AutoLyrixAlign is trained on polyphonic music and does
reject audio it has no words for, which is exactly what per-singer tracks need.

Stdlib only — no new dependency for one POST.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:3001"
# A 4-minute song takes ~1-2 min, and requests queue behind a lock on the
# server (the acoustic model is ~13 GB resident), so allow for a wait.
DEFAULT_TIMEOUT = 1800


class AlignerError(RuntimeError):
    """The aligner service was unreachable or returned an error."""


def base_url() -> str:
    return (os.environ.get("ALIGNER_URL") or DEFAULT_URL).rstrip("/")


def is_available(timeout: float = 5.0) -> bool:
    """True when the aligner answers /health. Never raises."""
    try:
        with urllib.request.urlopen(f"{base_url()}/health", timeout=timeout) as resp:
            return bool(json.load(resp).get("ok"))
    except Exception as exc:
        log.debug("aligner health check failed: %s", exc)
        return False


def align_lyrics(
    audio_path: str,
    lyrics_text: str,
    *,
    url: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    strip_headers: bool = True,
) -> list[dict]:
    """Align ``lyrics_text`` against ``audio_path``.

    Returns word marks ``{"label", "start_ms", "end_ms"}`` in time order. The
    text may be a *subset* of what is sung — that is the point of this aligner.

    ``strip_headers`` drops ``[Verse 1: JC Chasez]`` lines server-side: they
    carry the singer attribution but are not sung, so the aligner must not see
    them.
    """
    if not lyrics_text or not lyrics_text.strip():
        raise AlignerError("lyrics_text is empty")
    if not os.path.exists(audio_path):
        raise AlignerError(f"audio not found: {audio_path}")

    with open(audio_path, "rb") as fh:
        audio_b64 = base64.b64encode(fh.read()).decode("ascii")
    payload = json.dumps({
        "lyrics": lyrics_text,
        "audio_b64": audio_b64,
        "audio_ext": os.path.splitext(audio_path)[1] or ".mp3",
        "strip_headers": strip_headers,
    }).encode()

    target = (url or base_url()).rstrip("/") + "/align"
    req = urllib.request.Request(
        target, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("error", "")
        except Exception:
            pass
        raise AlignerError(f"aligner returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AlignerError(
            f"aligner unreachable at {target} ({exc.reason}). Is the "
            "xonset-aligner container running? See deploy/aligner/README.md."
        ) from exc

    words = body.get("words") or []
    if not words:
        raise AlignerError("aligner returned no words")
    log.info("aligner: %d words in %ss (supplied %s)",
             len(words), body.get("seconds"), body.get("supplied_words"))
    return sorted(words, key=lambda w: (w["start_ms"], w["end_ms"]))
