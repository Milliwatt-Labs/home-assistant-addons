"""OpenAI-compatible speech API: POST /v1/audio/speech, GET /v1/models, errors in OpenAI's shape."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import time

from aiohttp import web

from . import audio as A
from .engine import VOICE, Engine

log = logging.getLogger("leokani.openai")

MODEL = "leokani-tts"
# Accepted so that clients written for OpenAI work unchanged; they all get the one voice.
MODEL_ALIASES = {MODEL, "tts-1", "tts-1-hd", "gpt-4o-mini-tts"}
VOICE_ALIASES = {VOICE, "alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx", "sage",
                 "shimmer", "verse"}
CREATED = 1761782400                                        # 2025-10-30, the 1.1 release
ENGINE = web.AppKey("engine", Engine)
CONFIG = web.AppKey("config", dict)


class ApiError(Exception):
    def __init__(self, status: int, message: str, param: str | None = None, code: str | None = None,
                 kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.body = status, {"error": {"message": message, "type": kind, "param": param, "code": code}}


def error_response(e: ApiError) -> web.Response:
    return web.json_response(e.body, status=e.status)


def supported(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


@web.middleware
async def errors(request: web.Request, handler):
    try:
        key = request.app[CONFIG].get("api_key")
        if key and request.path.startswith("/v1/"):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise ApiError(401, "You didn't provide an API key. You need to provide your API key in an "
                                    "Authorization header using Bearer auth (i.e. Authorization: Bearer YOUR_KEY).")
            if not hmac.compare_digest(auth[7:].strip().encode(), key.encode()):
                raise ApiError(401, "Incorrect API key provided.", code="invalid_api_key")
        return await handler(request)
    except ApiError as e:
        return error_response(e)
    except web.HTTPNotFound:
        return error_response(ApiError(404, f"Invalid URL ({request.method} {request.path})"))
    except web.HTTPMethodNotAllowed:
        return error_response(ApiError(405, f"Method not allowed ({request.method} {request.path})"))
    except web.HTTPException as e:
        if e.status < 400:
            raise
        return error_response(ApiError(e.status, e.reason))


def model_card(name: str) -> dict:
    return {"id": name, "object": "model", "created": CREATED, "owned_by": "milliwattlabs"}


async def list_models(request: web.Request) -> web.Response:
    return web.json_response({"object": "list", "data": [model_card(MODEL)]})


async def get_model(request: web.Request) -> web.Response:
    name = request.match_info["model"]
    if name not in MODEL_ALIASES:
        raise ApiError(404, f"The model '{name}' does not exist", param="model", code="model_not_found")
    return web.json_response(model_card(name))


async def list_voices(request: web.Request) -> web.Response:
    return web.json_response({"voices": [VOICE]})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def parse(body) -> dict:
    """Validate a speech request the way OpenAI does; returns the cleaned fields."""
    if not isinstance(body, dict):
        raise ApiError(400, "The request body must be a JSON object.")
    for field in ("model", "input", "voice"):
        if body.get(field) is None:
            raise ApiError(400, f"Missing required parameter: '{field}'.", param=field,
                           code="missing_required_parameter")
    model, text, voice = body["model"], body["input"], body["voice"]
    if not isinstance(model, str):
        raise ApiError(400, "Invalid type for 'model': expected a string.", param="model", code="invalid_type")
    if model not in MODEL_ALIASES:
        raise ApiError(404, f"The model '{model}' does not exist", param="model", code="model_not_found")
    if not isinstance(text, str):
        raise ApiError(400, "Invalid type for 'input': expected a string.", param="input", code="invalid_type")
    if not text.strip():
        raise ApiError(400, "Invalid 'input': empty string. Expected a string with minimum length 1.",
                       param="input", code="empty_string")
    if isinstance(voice, dict):                              # {"id": "..."} for custom voices
        voice = voice.get("id")
    if not isinstance(voice, str):
        raise ApiError(400, "Invalid type for 'voice': expected a string.", param="voice", code="invalid_type")
    if voice not in VOICE_ALIASES:
        raise ApiError(400, f"Invalid value: '{voice}'. Supported values are: {supported(sorted(VOICE_ALIASES))}.",
                       param="voice", code="invalid_value")
    fmt = body.get("response_format") or "mp3"
    if fmt not in A.FORMATS:
        raise ApiError(400, f"Invalid value: '{fmt}'. Supported values are: {supported(A.FORMATS)}.",
                       param="response_format", code="invalid_value")
    speed = body.get("speed", 1.0)
    if speed is None:
        speed = 1.0
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ApiError(400, "Invalid type for 'speed': expected a number.", param="speed", code="invalid_type")
    if speed < 0.25:
        raise ApiError(400, f"Invalid 'speed': decimal below minimum value. Expected a value >= 0.25, but got "
                            f"{speed} instead.", param="speed", code="decimal_below_min_value")
    if speed > 4.0:
        raise ApiError(400, f"Invalid 'speed': decimal above maximum value. Expected a value <= 4.0, but got "
                            f"{speed} instead.", param="speed", code="decimal_above_max_value")
    stream_format = body.get("stream_format") or "audio"
    if stream_format not in ("audio", "sse"):
        raise ApiError(400, f"Invalid value: '{stream_format}'. Supported values are: 'sse' and 'audio'.",
                       param="stream_format", code="invalid_value")
    return {"input": text, "format": fmt, "speed": float(speed), "stream_format": stream_format}


async def speech(request: web.Request) -> web.StreamResponse:
    engine, cfg = request.app[ENGINE], request.app[CONFIG]
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "We could not parse the JSON body of your request. (HINT: This likely means you aren't "
                            "using your HTTP library correctly. The OpenAI API expects a JSON payload, but what was "
                            "sent was not valid JSON.)")
    req = parse(body)
    limit = cfg["max_input"]
    if len(req["input"]) > limit:
        raise ApiError(400, f"Invalid 'input': string too long. Expected a string with maximum length {limit}, but "
                            f"got a string with length {len(req['input'])} instead.", param="input",
                       code="string_above_max_length")

    t0 = time.perf_counter()
    chunks = engine.astream(req["input"], req["speed"])
    try:
        first = await chunks.__anext__()
    except StopAsyncIteration:
        first = None                                         # nothing speakable (punctuation only): empty audio
    except Exception as e:                                   # noqa: BLE001
        log.exception("synthesis failed")
        raise ApiError(500, f"Speech synthesis failed: {e}", kind="server_error")
    first_ms = (time.perf_counter() - t0) * 1000

    sse = req["stream_format"] == "sse"
    ctype = "text/event-stream" if sse else A.FORMATS[req["format"]][0]
    resp = web.StreamResponse(status=200, headers={"Content-Type": ctype, "Cache-Control": "no-cache",
                                                   "X-Accel-Buffering": "no"})
    resp.enable_chunked_encoding()
    await resp.prepare(request)
    enc = A.encoder(req["format"])

    async def send(data: bytes):
        if not data:
            return
        if sse:
            event = {"type": "speech.audio.delta", "audio": base64.b64encode(data).decode()}
            await resp.write(f"data: {json.dumps(event)}\n\n".encode())
        else:
            await resp.write(data)

    samples = 0
    try:
        await send(enc.header())
        if first is not None:
            samples += first.size
            await send(enc.feed(first))
            async for audio in chunks:
                samples += audio.size
                await send(enc.feed(audio))
        await send(enc.finish())
        if sse:
            done = {"type": "speech.audio.done", "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
            await resp.write(f"data: {json.dumps(done)}\n\n".encode())
        await resp.write_eof()
    except (ConnectionResetError, ConnectionError):
        log.info("client went away")
        return resp
    finally:
        await chunks.aclose()
    total = time.perf_counter() - t0
    log.info("%s %d chars: first audio %.0f ms, %.2f s audio in %.2f s (%.1fx)", req["format"], len(req["input"]),
             first_ms, samples / 24000, total, samples / 24000 / max(total, 1e-9))
    return resp


def make_app(engine: Engine, api_key: str | None = None, max_input: int = 4096) -> web.Application:
    app = web.Application(middlewares=[errors], client_max_size=1 << 20)
    app[ENGINE] = engine
    app[CONFIG] = {"api_key": api_key, "max_input": max_input}
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", list_models)
    app.router.add_get("/v1/models/{model}", get_model)
    app.router.add_get("/v1/audio/voices", list_voices)
    app.router.add_post("/v1/audio/speech", speech)
    return app


def serve(engine: Engine, host: str, port: int, api_key: str | None, max_input: int):
    log.info("OpenAI-compatible speech API on http://%s:%d/v1", host, port)
    web.run_app(make_app(engine, api_key, max_input), host=host, port=port, print=None, access_log=None)
