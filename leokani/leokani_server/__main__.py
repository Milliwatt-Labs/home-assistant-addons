"""leokani-server: the Leokani voice as a Wyoming server (Home Assistant) or an OpenAI-compatible speech API.

    python -m leokani_server wyoming [--port 10200] [--zeroconf]
    python -m leokani_server openai [--port 8880] [--api-key KEY]
    python -m leokani_server healthcheck
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .engine import VERSION

STATE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "leokani-server.json")


def env(name: str, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def common(p: argparse.ArgumentParser):
    p.add_argument("--model-dir", default=env("LEOKANI_MODEL_DIR"),
                   help="directory with config.json, leokani-tts-fp16.onnx and leokani_onnx.py")
    p.add_argument("--espeak-lib", default=env("LEOKANI_ESPEAK_LIB"),
                   help="espeak-ng shared library (default: the espeak-ng program on PATH)")
    p.add_argument("--threads", type=int, default=int(env("LEOKANI_THREADS", 0)),
                   help="CPU threads per synthesis (default: up to 4)")
    p.add_argument("--jobs", type=int, default=int(env("LEOKANI_JOBS", 2)),
                   help="syntheses that run at once (default 2)")
    p.add_argument("--host", default=env("LEOKANI_HOST", "0.0.0.0"), help="address to listen on (default 0.0.0.0)")
    p.add_argument("--debug", action="store_true", help="log every event")


def write_state(doc: dict):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(doc, f)
    except OSError:
        pass


def healthcheck() -> int:
    """Exit 0 if the server in this container answers: GET /health, or a Wyoming describe that returns info."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        print("not started", file=sys.stderr)
        return 1
    if st["mode"] == "openai":
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{st['port']}/health", timeout=4) as r:
                return 0 if r.status == 200 else 1
        except OSError as e:
            print(e, file=sys.stderr)
            return 1
    import asyncio
    from wyoming.client import AsyncTcpClient
    from wyoming.info import Describe, Info

    async def describe() -> bool:
        async with AsyncTcpClient("127.0.0.1", st["port"]) as client:
            await client.write_event(Describe().event())
            event = await asyncio.wait_for(client.read_event(), 4)
            return event is not None and Info.is_type(event.type)

    try:
        return 0 if asyncio.run(describe()) else 1
    except (OSError, asyncio.TimeoutError) as e:
        print(e, file=sys.stderr)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="leokani-server", description=f"Leokani {VERSION} text to speech servers.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wyoming", help="Wyoming server for Home Assistant")
    common(w)
    w.add_argument("--port", type=int, default=int(env("LEOKANI_PORT", 10200)), help="TCP port (default 10200)")
    w.add_argument("--speed", type=float, default=float(env("LEOKANI_SPEED", 1.0)),
                   help="speaking rate, 0.5 to 2.0 (default 1.0)")
    w.add_argument("--samples-per-chunk", type=int, default=1024, help="audio samples per Wyoming audio-chunk")
    w.add_argument("--zeroconf", nargs="?", const="leokani", default=env("LEOKANI_ZEROCONF"), metavar="NAME",
                   help="advertise on mDNS so Home Assistant discovers it (needs host networking)")
    w.add_argument("--zeroconf-host", default=env("LEOKANI_ZEROCONF_HOST"), metavar="IP",
                   help="address to advertise (default: the default route's interface)")
    o = sub.add_parser("openai", help="OpenAI-compatible speech API")
    common(o)
    o.add_argument("--port", type=int, default=int(env("LEOKANI_PORT", 8880)), help="HTTP port (default 8880)")
    o.add_argument("--api-key", default=env("LEOKANI_API_KEY"), help="require this Bearer key (default: none)")
    o.add_argument("--max-input", type=int, default=int(env("LEOKANI_MAX_INPUT", 4096)),
                   help="longest input in characters (default 4096)")
    sub.add_parser("healthcheck", help="exit 0 if the running server answers")
    i = sub.add_parser("ids", help=argparse.SUPPRESS)        # parity: a lines file's id chunks as JSON
    common(i)
    i.add_argument("lines")
    a = ap.parse_args(argv)

    if a.cmd == "healthcheck":
        return healthcheck()
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .engine import Engine
    engine = Engine(a.model_dir, a.espeak_lib, a.threads, a.jobs)
    if a.cmd == "ids":
        with open(a.lines, encoding="utf-8") as f:
            lines = json.load(f)["lines"]
        json.dump({"espeak": engine.espeak_version,
                   "lines": {l["id"]: engine.ids(l["text"]) for l in lines}}, sys.stdout)
        return 0
    if a.cmd == "wyoming":
        from . import wyoming_server
        write_state({"mode": "wyoming", "port": a.port})
        wyoming_server.serve(engine, f"tcp://{a.host}:{a.port}", a.speed, a.samples_per_chunk, a.zeroconf,
                             a.zeroconf_host)
    else:
        from . import openai_server
        write_state({"mode": "openai", "port": a.port})
        openai_server.serve(engine, a.host, a.port, a.api_key, a.max_input)
    return 0


if __name__ == "__main__":
    sys.exit(main())
