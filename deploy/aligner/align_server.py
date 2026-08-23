#!/usr/bin/env python3
"""Minimal HTTP wrapper around NUS AutoLyrixAlign's ``RunAlignment.sh``.

Why this exists
---------------
AutoLyrixAlign (GPLv3) is the engine behind the community AutoLyrics service.
It aligns a lyric text to *polyphonic* audio and — unlike plain forced
alignment — genuinely rejects audio that does not match the supplied words, so
you can feed it ONE singer's lines and get that singer's timings back.

This app is MIT-licensed, so it must not link against or vendor GPL code. It
talks to this wrapper over HTTP instead: a separate process, in a separate
container, invoked as a subprocess. That keeps the licence boundary clean.

Runs on Python 3.9 stdlib only — the image has no pip packages and may have no
network at runtime, so adding a dependency is not an option.

API
---
``GET  /health`` -> ``{"ok": true, "busy": bool}``
``POST /align``  -> JSON body:
      {"lyrics": "<plain text>",
       "audio_b64": "<base64 audio>"      # or "audio_path": "/path/in/container"
       "strip_headers": true}             # optional, default true
    200 -> {"words": [{"label","start_ms","end_ms"}, ...], "count": N,
            "supplied_words": N, "seconds": float}
    4xx/5xx -> {"error": "..."}

Alignment needs ~13 GB of RAM, so requests are **serialised** behind a lock.
A concurrent caller waits rather than racing the box into swap.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALIGN_DIR = os.environ.get("ALIGN_DIR", "/NUSAutoLyrixAlign")
RUNNER = "./RunAlignment.sh"
PORT = int(os.environ.get("ALIGN_PORT", "3001"))
MAX_BODY = 200 * 1024 * 1024  # 200 MB
# One alignment at a time: the acoustic model is ~13 GB resident.
_LOCK = threading.Lock()

_HEADER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")


def strip_annotation_headers(text: str) -> str:
    """Drop ``[Verse 1: JC Chasez]``-style lines.

    Genius-formatted lyrics carry section/singer headers. They are how we know
    *who* sings what, but they are not sung — leaving them in would have the
    aligner try to place "verse" and "chasez" as lyrics.
    """
    return "\n".join(l for l in text.splitlines() if not _HEADER_RE.match(l))


def parse_alignment(path: str) -> list[dict]:
    """Parse ``start_sec end_sec WORD`` lines into millisecond marks."""
    out: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                start, end = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            out.append({
                "label": " ".join(parts[2:]).strip(),
                "start_ms": int(round(start * 1000)),
                "end_ms": int(round(end * 1000)),
            })
    return out


def run_alignment(audio_path: str, lyrics_text: str) -> tuple[list[dict], str]:
    """Run the aligner once. Returns ``(marks, stderr_tail)``."""
    tag = uuid.uuid4().hex[:12]
    lyr_name = f"_req_{tag}.txt"
    out_name = f"_req_{tag}_aligned.txt"
    lyr_path = os.path.join(ALIGN_DIR, lyr_name)
    out_path = os.path.join(ALIGN_DIR, out_name)
    # The runner resolves its arguments relative to its own directory.
    audio_arg = os.path.relpath(audio_path, ALIGN_DIR) if audio_path.startswith(ALIGN_DIR) else audio_path
    try:
        with open(lyr_path, "w", encoding="utf-8") as fh:
            fh.write(lyrics_text if lyrics_text.endswith("\n") else lyrics_text + "\n")
        proc = subprocess.run(
            [RUNNER, audio_arg, lyr_name, out_name],
            cwd=ALIGN_DIR, capture_output=True, text=True, timeout=3600,
        )
        if not os.path.exists(out_path):
            tail = (proc.stderr or proc.stdout or "")[-1500:]
            raise RuntimeError(f"aligner produced no output (rc={proc.returncode}): {tail}")
        return parse_alignment(out_path), (proc.stderr or "")[-500:]
    finally:
        for p in (lyr_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoLyrixAlignWrapper/1.0"

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep the log terse but useful
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", ""):
            locked = _LOCK.locked()
            self._json(200, {"ok": True, "busy": locked, "align_dir": ALIGN_DIR})
        else:
            self._json(404, {"error": f"no route {self.path}"})

    def do_POST(self):
        if self.path.rstrip("/") != "/align":
            self._json(404, {"error": f"no route {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "bad Content-Length"})
            return
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": f"body must be 1..{MAX_BODY} bytes"})
            return
        try:
            req = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": f"invalid JSON: {exc}"})
            return

        lyrics = (req.get("lyrics") or "").strip()
        if not lyrics:
            self._json(400, {"error": "lyrics is required"})
            return
        if req.get("strip_headers", True):
            lyrics = strip_annotation_headers(lyrics)
        supplied = len(lyrics.split())
        if not supplied:
            self._json(400, {"error": "lyrics contained no words after stripping headers"})
            return

        tmp_audio = None
        audio_path = req.get("audio_path")
        if req.get("audio_b64"):
            try:
                raw = base64.b64decode(req["audio_b64"], validate=True)
            except (binascii.Error, ValueError) as exc:
                self._json(400, {"error": f"audio_b64 is not valid base64: {exc}"})
                return
            fd, tmp_audio = tempfile.mkstemp(
                dir=ALIGN_DIR, prefix="_aud_", suffix=req.get("audio_ext", ".mp3"))
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            audio_path = tmp_audio
        elif not audio_path:
            self._json(400, {"error": "provide audio_b64 or audio_path"})
            return
        elif not os.path.exists(audio_path):
            self._json(400, {"error": f"audio_path not found: {audio_path}"})
            return

        started = time.time()
        try:
            with _LOCK:  # serialise: the model is ~13 GB resident
                marks, stderr_tail = run_alignment(audio_path, lyrics)
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "aligner timed out"})
            return
        except Exception as exc:
            self._json(500, {"error": str(exc)})
            return
        finally:
            if tmp_audio:
                try:
                    os.unlink(tmp_audio)
                except OSError:
                    pass

        self._json(200, {
            "words": marks,
            "count": len(marks),
            "supplied_words": supplied,
            "seconds": round(time.time() - started, 1),
            "stderr_tail": stderr_tail,
        })


if __name__ == "__main__":
    print(f"AutoLyrixAlign wrapper listening on :{PORT} (align dir {ALIGN_DIR})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
