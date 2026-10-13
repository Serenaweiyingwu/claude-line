"""Claude Line voice server: mic audio in -> Whisper -> Claude -> Piper -> audio out.

One WebSocket (/call) is one phone call. The client streams 16 kHz mono int16 PCM
while the line is listening and plays back the int16 PCM it receives.
"""

import asyncio
import json
import logging
import os
import re
import secrets
from collections import deque
from pathlib import Path

import anthropic
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from piper import PiperVoice, SynthesisConfig

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("claude-line")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_US-lessac-medium")
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.015"))  # RMS level that counts as speech
SILENCE_MS = int(os.getenv("SILENCE_MS", "800"))  # quiet time that ends an utterance
LINE_KEY = os.getenv("LINE_KEY", "")  # lets a phone on the Wi-Fi place calls; unset means this Mac only
VOICE_SPEED = float(os.getenv("VOICE_SPEED", "1.0"))  # 1.3 speaks 30% faster

MIC_RATE = 16000
MIN_SPEECH_MS = 250
MAX_UTTERANCE_MS = 30_000
PREROLL_CHUNKS = 6  # ~300 ms of audio kept from before speech starts

GREETING = os.getenv("GREETING", "Hello, this is Claude.")  # empty: answer silently and just listen
SYSTEM_PROMPT = (
    "You are Claude, speaking with someone over a telephone handset. Everything you write is "
    "read aloud by a text-to-speech voice, so reply the way a person talks on the phone: plain "
    "spoken sentences, no markdown, lists, code blocks, emoji or URLs. Keep replies to a few "
    "sentences unless asked for more. What you hear is a speech-to-text transcript and can "
    "contain mis-heard words; when a word looks wrong, go with the most likely meaning, and ask "
    "only if you really cannot tell. "
    "A Claude Code session is running on the caller's computer with access to their browser. "
    "When they ask for something that needs the computer, such as reading or replying to their "
    "email, say only 'Sure.' and call ask_computer straight away with their request as they said "
    "it. Never ask the caller a question first, even if the request seems incomplete: the session "
    "on the computer can see their screen and works out the details. When an answer comes back, "
    "tell it to them in your own spoken words: the main point in the first sentence, two "
    "sentences at most, no lead-in."
)
# The bridge to the Claude Code session on this Mac: the task is left in bridge/request.txt
# and the session writes its answer to bridge/reply.txt.
BRIDGE = ROOT / "bridge"
BRIDGE_WAIT_S = 120
TOOLS = [{
    "name": "ask_computer",
    "description": (
        "Hands a task to the Claude Code session on the caller's computer, which can use their "
        "web browser (email, web pages) and files. Returns that session's written answer. It "
        "can take up to a minute."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"task": {"type": "string", "description": (
            "The caller's request, passed on as they said it. Do not split it into steps or add "
            "conditions of your own; the session on the computer works out how to do it."
        )}},
        "required": ["task"],
    },
}]
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")

voice_path = ROOT / "voices" / f"{PIPER_VOICE}.onnx"
if not voice_path.exists():
    raise SystemExit(
        f"Missing Piper voice {voice_path}. Run:\n"
        f"  python -m piper.download_voices {PIPER_VOICE} --download-dir voices"
    )
voice = PiperVoice.load(voice_path)
speech = SynthesisConfig(length_scale=voice.config.length_scale / VOICE_SPEED)
try:  # use the downloaded copy without asking Hugging Face, so a flaky network can't stop startup
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", local_files_only=True)
except Exception:  # not downloaded yet
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    # no-cache: the phone must pick up page changes without a manual reload
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/unlock")
def unlock(key: str):
    """Opened once on the phone: remembers the key in a cookie so later calls are let through."""
    if not LINE_KEY or not secrets.compare_digest(key, LINE_KEY):
        raise HTTPException(403)
    response = RedirectResponse("/")
    response.set_cookie("line_key", key, max_age=10 * 365 * 86400, httponly=True)
    return response


def may_call(ws: WebSocket) -> bool:
    """This Mac (which includes the USB-cabled phone) may always call; others need the key."""
    if ws.client.host in ("127.0.0.1", "::1"):
        return True
    return bool(LINE_KEY) and secrets.compare_digest(ws.cookies.get("line_key", ""), LINE_KEY)


async def ask_computer(task: str) -> str:
    BRIDGE.mkdir(exist_ok=True)
    request, reply = BRIDGE / "request.txt", BRIDGE / "reply.txt"
    reply.unlink(missing_ok=True)
    request.write_text(task)
    try:
        for _ in range(BRIDGE_WAIT_S * 5):
            if reply.exists():
                return reply.read_text()
            await asyncio.sleep(0.2)
        return "The computer did not answer in time."
    finally:
        request.unlink(missing_ok=True)
        reply.unlink(missing_ok=True)


def transcribe(audio: np.ndarray) -> str:
    segments, _ = whisper.transcribe(audio, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def synthesize(text: str) -> bytes:
    return b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text, speech))


class Endpointer:
    """Collects mic chunks and returns the whole utterance once the caller stops talking."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.preroll = deque(maxlen=PREROLL_CHUNKS)
        self.speech = []
        self.voiced_ms = self.silence_ms = self.total_ms = 0.0

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        ms = len(chunk) * 1000 / MIC_RATE
        loud = float(np.sqrt(np.mean(chunk**2))) > VAD_THRESHOLD
        if not self.speech:
            if not loud:
                self.preroll.append(chunk)
                return None
            self.speech = list(self.preroll)
        self.speech.append(chunk)
        self.total_ms += ms
        if loud:
            self.voiced_ms += ms
            self.silence_ms = 0
        else:
            self.silence_ms += ms
        if self.silence_ms < SILENCE_MS and self.total_ms < MAX_UTTERANCE_MS:
            return None
        utterance = np.concatenate(self.speech) if self.voiced_ms >= MIN_SPEECH_MS else None
        self.reset()
        return utterance


class Call:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.history = []
        self.listening = False
        self.endpointer = Endpointer()
        self.turn: asyncio.Task | None = None

    async def send(self, **message):
        await self.ws.send_text(json.dumps(message))

    async def say(self, text: str):
        if text.strip():
            await self.ws.send_bytes(await asyncio.to_thread(synthesize, text))

    async def listen(self):
        self.endpointer.reset()
        self.listening = True
        await self.send(type="state", state="listening")

    async def greet(self):
        await self.send(type="hello", sample_rate=voice.config.sample_rate)
        if not GREETING:
            return await self.listen()
        await self.send(type="claude", text=GREETING)
        await self.say(GREETING)
        await self.send(type="turn_end")

    async def respond(self, audio: np.ndarray):
        await self.send(type="state", state="thinking")
        text = await asyncio.to_thread(transcribe, audio)
        if not text:
            return await self.listen()
        await self.send(type="you", text=text)
        self.turn_start = len(self.history)  # a failed turn is removed from here on
        self.history.append({"role": "user", "content": text})
        try:
            await self.ask_claude()
        except (anthropic.AnthropicError, TypeError) as e:  # the SDK raises TypeError when no key is set
            log.error("Claude request failed: %s", e)
            del self.history[self.turn_start:]
            await self.apologize(error_line(e))
        await self.send(type="turn_end")

    async def ask_claude(self):
        async with anthropic.AsyncAnthropic() as client:
            while True:  # one request, plus one more after each ask_computer call
                async with client.beta.messages.stream(
                    model=CLAUDE_MODEL,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    messages=self.history,
                    tools=TOOLS,
                    output_config={"effort": "low"},  # shortest pause before Claude starts talking
                    cache_control={"type": "ephemeral"},
                    # if a safety classifier declines the request, the API retries it on another model
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    pending = ""
                    async for delta in stream.text_stream:
                        await self.send(type="claude_delta", text=delta)
                        *sentences, pending = SENTENCE_END.split(pending + delta)
                        for sentence in sentences:
                            await self.say(sentence)
                    await self.say(pending)
                    reply = await stream.get_final_message()
                if reply.stop_reason == "refusal":
                    del self.history[self.turn_start:]
                    return await self.apologize("Sorry, I can't help with that one.")
                # keep every block (thinking included) so the next turn replays it unchanged
                self.history.append({"role": "assistant", "content": reply.content})
                if reply.stop_reason != "tool_use":
                    return
                await self.send(type="claude_break")
                await self.send(type="state", state="thinking")
                results = [
                    {"type": "tool_result", "tool_use_id": block.id, "content": await ask_computer(block.input["task"])}
                    for block in reply.content if block.type == "tool_use"
                ]
                if not any(result["content"] for result in results):  # an empty reply: done, nothing to say
                    for result in results:
                        result["content"] = "Done."
                    self.history.append({"role": "user", "content": results})
                    return
                self.history.append({"role": "user", "content": results})

    async def apologize(self, line: str):
        await self.send(type="claude", text=line)
        await self.say(line)


def error_line(e: Exception) -> str:
    if isinstance(e, anthropic.AuthenticationError) or not isinstance(e, anthropic.APIError):
        return "I can't reach Claude. Check the Anthropic API key in the dot env file."
    if isinstance(e, anthropic.RateLimitError):
        return "Claude is rate limited right now. Try again in a moment."
    if isinstance(e, anthropic.APIConnectionError):
        return "I can't reach Claude. Check the internet connection."
    return "Something went wrong talking to Claude. The server log has the details."


def log_failure(task: asyncio.Task):
    if not task.cancelled() and task.exception():
        log.error("turn failed", exc_info=task.exception())


@app.websocket("/call")
async def call(ws: WebSocket):
    if not may_call(ws):
        return await ws.close()
    await ws.accept()
    line = Call(ws)

    def start(turn):
        line.turn = asyncio.create_task(turn)
        line.turn.add_done_callback(log_failure)

    start(line.greet())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                if not line.listening:
                    continue
                chunk = np.frombuffer(message["bytes"], dtype=np.int16).astype(np.float32) / 32768
                utterance = line.endpointer.feed(chunk)
                if utterance is not None:
                    line.listening = False
                    start(line.respond(utterance))
            elif json.loads(message["text"]).get("type") == "playback_done":
                await line.listen()
    finally:
        line.turn.cancel()
