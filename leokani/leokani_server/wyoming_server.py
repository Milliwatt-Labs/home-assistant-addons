"""Wyoming text-to-speech server for Home Assistant, following wyoming-piper's event flow.

describe -> info (one voice, en_US, described by the model in use). synthesize -> audio-start, audio-chunk..., audio-stop, sent clause by clause
as each one is rendered. Streaming text (synthesize-start, synthesize-chunk..., synthesize-stop) renders each clause
as soon as it is complete and ends with synthesize-stopped.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from functools import partial

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import (Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop, SynthesizeStopped,
                         SynthesizeTextFormat)

from .engine import SAMPLE_RATE, VERSION, VOICE, ChunkStream, Engine, pcm16, strip_ssml

log = logging.getLogger("leokani.wyoming")

WIDTH, CHANNELS = 2, 1


def make_info(model_name: str = "Leokani-tts 7M", model_version: str = VERSION) -> Info:
    """The voice id stays "leokani" whichever model speaks, so a pipeline set to it keeps working after a switch."""
    attribution = Attribution(name="Milliwatt Labs", url="https://milliwattlabs.com")
    voice = TtsVoice(name=VOICE, description=f"{model_name} (English, US)", attribution=attribution, installed=True,
                     version=model_version, languages=["en_US"])
    program = TtsProgram(name="leokani", description=model_name, attribution=attribution, installed=True,
                         version=VERSION, voices=[voice], supports_synthesize_streaming=True)
    return Info(tts=[program])


def text_of(text: str, fmt) -> str:
    return strip_ssml(text) if fmt in (SynthesizeTextFormat.SSML, "ssml") else text


class LeokaniHandler(AsyncEventHandler):
    def __init__(self, info: Info, engine: Engine, speed: float, samples_per_chunk: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info, self.engine, self.speed, self.samples_per_chunk = info, engine, speed, samples_per_chunk
        self.streaming = False
        self.stream: ChunkStream | None = None
        self.text_format = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.info.event())
            return True
        try:
            if Synthesize.is_type(event.type):
                if self.streaming:
                    return True                              # sent alongside a stream for older servers; ignore
                s = Synthesize.from_event(event)
                await self.say(self.engine.chunks(text_of(s.text, s.text_format)), start=True, stop=True)
                return True
            if SynthesizeStart.is_type(event.type):
                start = SynthesizeStart.from_event(event)
                self.streaming, self.stream, self.text_format = True, ChunkStream(self.engine.ref), start.text_format
                return True
            if SynthesizeChunk.is_type(event.type):
                if self.stream is not None:
                    chunk = SynthesizeChunk.from_event(event)
                    for clause in self.stream.feed(text_of(chunk.text, self.text_format)):
                        await self.say([clause], start=True, stop=True)
                return True
            if SynthesizeStop.is_type(event.type):
                if self.stream is not None:
                    rest = self.stream.finish()
                    if rest:
                        await self.say(rest, start=True, stop=True)
                await self.write_event(SynthesizeStopped().event())
                self.streaming, self.stream = False, None
                return True
        except Exception as e:                               # noqa: BLE001
            log.exception("synthesis failed")
            await self.write_event(Error(text=str(e), code=e.__class__.__name__).event())
            return False
        return True

    async def say(self, clauses: list[str], start: bool, stop: bool):
        """Render clause chunks one at a time, sending each as soon as it exists."""
        t0, first_ms, samples = time.perf_counter(), None, 0
        if start:
            await self.write_event(AudioStart(rate=SAMPLE_RATE, width=WIDTH, channels=CHANNELS).event())
        for clause in clauses:
            audio = await self.engine.run(self.engine.render_chunk, clause, self.speed)
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
            data = pcm16(audio)
            step = self.samples_per_chunk * WIDTH * CHANNELS
            for i in range(0, len(data), step):
                await self.write_event(AudioChunk(rate=SAMPLE_RATE, width=WIDTH, channels=CHANNELS,
                                                  audio=data[i:i + step]).event())
            samples += audio.size
        if stop:
            await self.write_event(AudioStop().event())
        total = time.perf_counter() - t0
        if samples:
            log.info("%d clauses: first audio %.0f ms, %.2f s audio in %.2f s (%.1fx)", len(clauses), first_ms or 0,
                     samples / SAMPLE_RATE, total, samples / SAMPLE_RATE / max(total, 1e-9))


async def run(engine: Engine, uri: str, speed: float, samples_per_chunk: int, zeroconf: str | None,
              zeroconf_host: str | None = None):
    info = make_info(engine.model_name, engine.model_version)
    server = AsyncServer.from_uri(uri)
    if zeroconf is not None:
        await advertise(uri, zeroconf, zeroconf_host)
    log.info("Wyoming server on %s (%s)", uri, engine.model_name)
    await server.run(partial(LeokaniHandler, info, engine, speed, samples_per_chunk))


def lan_address() -> str:
    """The address of the interface with the default route (no packet is sent). wyoming's own guess follows the
    multicast route, which on a machine with several interfaces can be a link-local one nobody else can reach."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]


async def advertise(uri: str, name: str, host: str | None):
    """mDNS (_wyoming._tcp) so Home Assistant finds the server by itself. Needs the host's network."""
    try:
        from wyoming.zeroconf import HomeAssistantZeroconf
        port = int(uri.rsplit(":", 1)[1])
        zc = HomeAssistantZeroconf(port=port, name=name or "leokani", host=host or lan_address())
        await zc.register_server()
        log.info("advertised as %s._wyoming._tcp on %s:%d", zc.name, zc.host, port)
    except Exception as e:                                   # noqa: BLE001 - discovery is a convenience
        log.warning("zeroconf discovery not available: %s", e)


def serve(engine: Engine, uri: str, speed: float, samples_per_chunk: int, zeroconf: str | None,
          zeroconf_host: str | None = None):
    asyncio.run(run(engine, uri, speed, samples_per_chunk, zeroconf, zeroconf_host))
