# AutoLyrixAlign HTTP wrapper

Per-singer lyric timing. Give it one singer's lines and it returns *that
singer's* word timings, leaving real gaps where the others sing.

## Why not WhisperX?

WhisperX does *forced* alignment: it assumes the text covers the audio, so it
cannot skip a stretch that has vocals it wasn't given. Measured on a 3-singer
track, feeding it one singer's lines produced words smeared across the whole
song — every singer's first word landed on the identical millisecond, with no
gap larger than ~3 s despite each singer sitting out whole verses.

NUS AutoLyrixAlign is trained on polyphonic music and rejects non-matching
audio. On the same track it reproduced the community AutoLyrics service's
output **bit-identically** (JC 122 words, Justin 106, Chris 68 — 0 ms delta on
every start and end), including a correct 33 s hole where one singer sits out.

## Licence boundary

AutoLyrixAlign is **GPLv3**; this project is **MIT**. So the engine runs as a
separate service and we talk to it over HTTP. Do not vendor its code or import
it in-process — that would pull the whole project under copyleft.

## API

    GET  /health   -> {"ok": true, "busy": false}
    POST /align    -> {"lyrics": "...", "audio_b64": "..."}   (or "audio_path")
                   -> {"words": [{"label","start_ms","end_ms"}], "count": N}

`strip_headers` (default true) drops `[Verse 1: JC Chasez]` lines — they tell us
*who* sings, but they aren't sung, so the aligner must not see them.

Requests are **serialised**: the model is ~13 GB resident, so a second caller
waits rather than pushing the host into swap. Expect ~2 min per 4-minute song.
