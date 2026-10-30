"""Streaming encoders for the speech API: feed float audio chunk by chunk, get bytes back as soon as they exist.

wav and pcm are written here; mp3, opus (in Ogg), aac (ADTS) and flac go through PyAV's FFmpeg. WAV is streamed the
way OpenAI streams it: the RIFF and data sizes are 0xFFFFFFFF because the length isn't known yet.
"""
from __future__ import annotations

import struct

import numpy as np

from .engine import SAMPLE_RATE, pcm16

# format -> (content type, PyAV container, codec, bit rate)
FORMATS = {
    "mp3": ("audio/mpeg", "mp3", "libmp3lame", 64000),
    "opus": ("audio/ogg", "ogg", "libopus", 32000),
    "aac": ("audio/aac", "adts", "aac", 64000),
    "flac": ("audio/flac", "flac", "flac", None),
    "wav": ("audio/wav", None, None, None),
    "pcm": ("audio/pcm", None, None, None),
}


class PcmEncoder:
    """Raw 16-bit little-endian mono at 24 kHz (OpenAI's pcm)."""

    def header(self) -> bytes:
        return b""

    def feed(self, audio: np.ndarray) -> bytes:
        return pcm16(audio)

    def finish(self) -> bytes:
        return b""


class WavEncoder(PcmEncoder):
    def header(self) -> bytes:
        unknown = 0xFFFFFFFF
        return (b"RIFF" + struct.pack("<I", unknown) + b"WAVE"
                + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
                + b"data" + struct.pack("<I", unknown))


class _Sink:
    """A write-only, unseekable file for PyAV: whatever the muxer writes is collected until taken."""

    def __init__(self):
        self.buf = bytearray()

    def write(self, b) -> int:
        self.buf += b
        return len(b)

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


class AvEncoder:
    def __init__(self, fmt: str):
        import av                                           # only loaded for the compressed formats
        _, container, codec, bit_rate = FORMATS[fmt]
        self.av = av
        self.sink = _Sink()
        self.out = av.open(self.sink, mode="w", format=container, buffer_size=4096,
                           container_options={"flush_packets": "1"})
        self.stream = self.out.add_stream(codec, rate=SAMPLE_RATE, layout="mono")
        if bit_rate:
            self.stream.bit_rate = bit_rate
        self.pts = 0

    def header(self) -> bytes:
        return b""

    def _encode(self, frame) -> bytes:
        for packet in self.stream.encode(frame):
            self.out.mux(packet)
        return self.sink.take()

    def feed(self, audio: np.ndarray) -> bytes:
        pcm = np.round(np.clip(audio, -1, 1) * 32767).astype(np.int16)[None, :]
        frame = self.av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self.pts
        self.pts += pcm.shape[1]
        return self._encode(frame)

    def finish(self) -> bytes:
        tail = self._encode(None)
        self.out.close()
        return tail + self.sink.take()


def encoder(fmt: str):
    if fmt == "pcm":
        return PcmEncoder()
    if fmt == "wav":
        return WavEncoder()
    return AvEncoder(fmt)
