"""Compatibility shims so ``whisperx`` imports on modern torch/torchaudio.

``pyproject.toml`` pins ``whisperx>=3.1`` but not torch/torchaudio, so a fresh
install resolves to whatever is current. Two things then break, and both are
silent from the user's perspective — alignment simply produces nothing:

1. torchaudio >= 2.8 removed ``AudioMetaData`` and ``list_audio_backends``,
   which ``pyannote.audio`` 3.3.2 still references at import time. ``whisperx``
   imports pyannote at package level, so ``import whisperx`` raises
   ``AttributeError`` before any alignment can run.
2. torch >= 2.6 flipped ``torch.load(weights_only=...)`` to ``True`` by default.
   pyannote's VAD checkpoint stores ``omegaconf`` objects, which the strict
   unpickler rejects.

Import this module **before** ``whisperx``. It is a no-op on versions that
still provide the removed APIs, so it is safe to leave in place.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _patch_torchaudio() -> None:
    import torchaudio

    if not hasattr(torchaudio, "AudioMetaData"):
        class AudioMetaData:  # noqa: D401 - annotation-only stand-in for pyannote
            """Stand-in for the metadata class torchaudio no longer exports."""

            def __init__(
                self,
                sample_rate: int = 0,
                num_frames: int = 0,
                num_channels: int = 0,
                bits_per_sample: int = 0,
                encoding: str = "",
            ) -> None:
                self.sample_rate = sample_rate
                self.num_frames = num_frames
                self.num_channels = num_channels
                self.bits_per_sample = bits_per_sample
                self.encoding = encoding

        torchaudio.AudioMetaData = AudioMetaData
        log.debug("torch_compat: added torchaudio.AudioMetaData stub")

    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]
        log.debug("torch_compat: added torchaudio.list_audio_backends stub")

    if not hasattr(torchaudio, "set_audio_backend"):
        torchaudio.set_audio_backend = lambda *_a, **_k: None


def _patch_torch_load() -> None:
    """Keep ``weights_only=True`` as the default, but allow known-safe globals.

    We allowlist the ``omegaconf`` types pyannote's checkpoint contains rather
    than disabling the check outright. If a load still fails strictly, we retry
    once with ``weights_only=False`` and log it — the checkpoint comes from the
    HuggingFace repo this app already downloads, but the retry is narrow and
    visible rather than a blanket opt-out.
    """
    import torch

    try:
        import typing

        from omegaconf.base import ContainerMetadata, Metadata
        from omegaconf.dictconfig import DictConfig
        from omegaconf.listconfig import ListConfig

        torch.serialization.add_safe_globals(
            [ListConfig, DictConfig, ContainerMetadata, Metadata, typing.Any,
             list, dict, int, float, str, bool, type(None)]
        )
    except Exception as exc:  # omegaconf absent, or API changed
        log.debug("torch_compat: could not extend safe globals: %s", exc)

    if getattr(torch.load, "_xonset_compat", False):
        return

    _orig_load = torch.load

    def _load(*args, **kwargs):
        try:
            return _orig_load(*args, **kwargs)
        except Exception as exc:
            if type(exc).__name__ != "UnpicklingError":
                raise
            # lightning passes weights_only explicitly, so override rather than default
            kwargs["weights_only"] = False
            # The failed attempt consumed the stream; rewind before retrying or
            # the unpickler restarts mid-file ("load persistent id instruction").
            if args and hasattr(args[0], "seek"):
                try:
                    args[0].seek(0)
                except Exception:  # non-seekable stream — nothing we can do
                    raise exc from None
            log.warning(
                "torch_compat: strict checkpoint load failed (%s); retrying with "
                "weights_only=False", exc.__class__.__name__,
            )
            return _orig_load(*args, **kwargs)

    _load._xonset_compat = True
    torch.load = _load


def apply() -> None:
    """Apply every shim. Safe to call repeatedly."""
    try:
        _patch_torchaudio()
        _patch_torch_load()
    except Exception as exc:  # never let a shim break the caller
        log.warning("torch_compat: shim application failed: %s", exc)
