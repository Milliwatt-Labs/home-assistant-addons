"""The Leokani voice for both servers: leokani_onnx.py's front end and ONNX graph, clause by clause.

The front end (text clean-up, clause chunks, respellings, espeak-ng, phoneme ids) is imported from the release's
leokani_onnx.py rather than copied, so the servers say exactly what the reference script says. Audio comes out one
clause chunk at a time, which is what lets both servers start streaming before the whole text is rendered.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Iterator

import numpy as np

log = logging.getLogger("leokani")

SAMPLE_RATE = 24000
MODEL_FILE = "leokani-tts-fp16.onnx"
VOICE = "leokani"
VERSION = "1.1"
SPEED_MIN, SPEED_MAX = 0.5, 2.0
# Public names of the released voices, by config.json "name".
MODEL_NAMES = {"leokani-tts": "Leokani-tts 7M", "leokani-tts-4.5M": "Leokani-tts 4.5M"}

_HERE = os.path.dirname(os.path.abspath(__file__))
_MARKS = str.maketrans({c: " " for c in "*_`#>|"})      # leokani_onnx.clean's marks, one for one


def default_model_dir() -> str:
    for d in (os.environ.get("LEOKANI_MODEL_DIR"), "/opt/leokani/model",
              os.path.join(_HERE, "..", "model"), os.path.join(_HERE, "..", "..", "leokani-tts")):
        if d and os.path.exists(os.path.join(d, "leokani_onnx.py")):
            return os.path.abspath(d)
    raise SystemExit("model not found: set LEOKANI_MODEL_DIR to a directory with leokani_onnx.py, config.json "
                     f"and {MODEL_FILE}")


def model_info(model_dir: str) -> tuple[str, str]:
    """(public name, version) of the voice in model_dir, from its config.json."""
    try:
        with open(os.path.join(model_dir, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    name = str(cfg.get("name") or "leokani-tts")
    return MODEL_NAMES.get(name, name), str(cfg.get("version") or VERSION)


def load_reference(model_dir: str):
    """Import leokani_onnx.py from the model directory."""
    spec = importlib.util.spec_from_file_location("leokani_onnx", os.path.join(model_dir, "leokani_onnx.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_threads() -> int:
    """Up to 4 threads, within the CPUs this process may use (affinity, and a container's --cpus quota).
    onnxruntime's own default is every core, which oversubscribes a shared or CPU-limited machine."""
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:
        n = os.cpu_count() or 1
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as f:
            quota, period = f.read().split()
        if quota != "max":
            n = min(n, max(1, -(-int(quota) // int(period))))
    except (OSError, ValueError):
        pass
    return max(1, min(4, n))


def pcm16(audio: np.ndarray) -> bytes:
    """Float audio -> 16-bit little-endian PCM, rounded as leokani_onnx.write_wav rounds it."""
    return np.round(np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def clamp_speed(speed: float) -> float:
    return min(SPEED_MAX, max(SPEED_MIN, float(speed)))


class ChunkStream:
    """leokani_onnx.chunks(clean(text)) for text that arrives in pieces (Home Assistant streams an LLM's reply).

    feed() returns the clause chunks that are complete so far; finish() returns the rest. Fed any split of a text,
    the chunks are the same as chunks(clean(text)) gives for the whole of it."""

    def __init__(self, ref):
        self.ref = ref
        self.pending = ""          # text after the last complete word
        self.cur: list[str] = []
        self.started = False       # chunks() has emitted a chunk (its 'out' is non-empty)

    def feed(self, text: str) -> list[str]:
        self.pending += text
        flat = self.pending.translate(_MARKS)
        cut = max((i for i, c in enumerate(flat) if c.isspace()), default=-1)
        if cut < 0:
            return []
        words, self.pending = flat[:cut].split(), self.pending[cut + 1:]
        return self._push(words)

    def finish(self) -> list[str]:
        out = self._push(self.pending.translate(_MARKS).split())
        self.pending = ""
        if self.cur:
            out += self._keep([" ".join(self.cur)])
            self.cur = []
        return out

    def _push(self, words: list[str]) -> list[str]:
        out = []
        for w in words:
            self.cur.append(w)
            end = w[-1]
            brk = end in "!?;:" or (end == "." and w.lower().strip("(\"'").rstrip(".") not in self.ref.TITLES) \
                or (end == "," and len(self.cur) >= 12)
            if brk and len(self.cur) >= (1 if self.started else 3):
                out.append(" ".join(self.cur))
                self.cur, self.started = [], True
        return self._keep(out)

    @staticmethod
    def _keep(chunks: list[str]) -> list[str]:
        return [c for c in chunks if any(ch.isalnum() for ch in c)]


class Engine:
    def __init__(self, model_dir: str | None = None, espeak_lib: str | None = None, threads: int = 0, jobs: int = 2):
        self.model_dir = model_dir or default_model_dir()
        self.ref = load_reference(self.model_dir)
        self.model_name, self.model_version = model_info(self.model_dir)
        lib = espeak_lib or os.environ.get("LEOKANI_ESPEAK_LIB")
        t0 = time.perf_counter()
        self.threads = threads or default_threads()
        # espeak_lib: espeak-ng's library, as the training front end used it; without one, the espeak-ng program.
        self.tts = self.ref.Leokani(self.model_dir, MODEL_FILE, espeak_lib=lib, threads=self.threads)
        self._espeak_lock = threading.Lock()                # espeak-ng keeps global state
        self.pool = ThreadPoolExecutor(max_workers=max(1, jobs), thread_name_prefix="leokani")
        self.espeak_version = self.tts.espeak().version()
        self.render_chunk("Hello there.", 1.0)              # warm up, so the first request isn't the slow one
        log.info("voice ready in %.0f ms (%s %s, %s, %s, %d threads)", (time.perf_counter() - t0) * 1000,
                 self.model_name, self.model_version, MODEL_FILE, self.espeak_version, self.threads)

    # -------------------------------------------------------------------------------------------- blocking

    def chunks(self, text: str) -> list[str]:
        return self.ref.chunks(self.ref.clean(text))

    def chunk_ids(self, chunk: str) -> list[list[int]]:
        """One clause chunk -> its id chunks (Leokani.ids_chunks for a single chunk)."""
        with self._espeak_lock:
            phonemes = self.ref.phonemize(chunk, self.tts.espeak())
        return self.tts.split(self.ref.to_ids(phonemes, self.tts.vocab))

    def ids(self, text: str) -> list[list[int]]:
        return [ids for c in self.chunks(text) for ids in self.chunk_ids(c)]

    def render_chunk(self, chunk: str, speed: float) -> np.ndarray:
        parts = [self.tts.synth_ids(ids, clamp_speed(speed)) for ids in self.chunk_ids(chunk)]
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def stream(self, text: str, speed: float = 1.0) -> Iterator[np.ndarray]:
        """Float audio, clause chunk by clause chunk; back to back it is Leokani.synthesize(text, speed=speed)."""
        for c in self.chunks(text):
            audio = self.render_chunk(c, speed)
            if audio.size:
                yield audio

    # -------------------------------------------------------------------------------------------- async

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    async def astream(self, text: str, speed: float = 1.0) -> AsyncIterator[np.ndarray]:
        it = self.stream(text, speed)
        try:
            while True:
                audio = await self.run(next, it, None)
                if audio is None:
                    return
                yield audio
        finally:
            try:
                it.close()
            except ValueError:                              # cancelled while a chunk renders; that chunk is dropped
                pass


def strip_ssml(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)
