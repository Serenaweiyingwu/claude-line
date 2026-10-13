"""Claude Line terminal: one Claude Code session shown in a browser, on the phone or the Mac.

    .venv/bin/uvicorn terminal:app --port 8701

Whoever can open this page can type into Claude Code, so it must only listen on this Mac.
The phone reaches it through the USB cable (see phone.sh). Never run it with --host 0.0.0.0.
"""

import asyncio
import fcntl
import json
import os
import pty
import shutil
import struct
import termios
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
PORT = 8701
# Any web page can try to open a WebSocket to localhost, so only our own page is let in.
ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
CLAUDE = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
REPLAY_BYTES = 200_000  # recent output shown to a browser that joins late

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    # no-cache: the phone must pick up page changes without a manual reload
    return FileResponse(ROOT / "static" / "terminal.html", headers={"Cache-Control": "no-cache"})


class Session:
    """The Claude Code process, shared by every browser that is watching it."""

    def __init__(self):
        self.fd = None
        self.recent = b""
        self.viewers: set[WebSocket] = set()

    def start(self):
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(ROOT)
            keep = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "PATH")
            env = {name: os.environ[name] for name in keep if name in os.environ}
            os.execve(CLAUDE, ["claude"], env | {"TERM": "xterm-256color"})
        self.pid, self.fd, self.recent = pid, fd, b""
        asyncio.get_running_loop().add_reader(fd, self.read)

    def read(self):
        try:
            data = os.read(self.fd, 65536)
        except OSError:  # Claude Code exited
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.fd)
            os.close(self.fd)
            os.waitpid(self.pid, 0)
            self.fd = None
            data = b"\r\n[Claude Code exited. Reload the page to start it again.]\r\n"
        self.recent = (self.recent + data)[-REPLAY_BYTES:]
        for ws in self.viewers:
            asyncio.create_task(ws.send_bytes(data))

    def resize(self, cols: int, rows: int):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


session = Session()


@app.websocket("/pty")
async def attach(ws: WebSocket):
    if ws.headers.get("origin") not in ALLOWED_ORIGINS:
        return await ws.close()
    await ws.accept()
    if session.fd is None:
        session.start()
    await ws.send_bytes(session.recent)
    session.viewers.add(ws)
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if session.fd is None:
                continue
            if message.get("bytes"):  # keys typed in the browser
                os.write(session.fd, message["bytes"])
            elif message.get("text"):
                session.resize(*json.loads(message["text"])["resize"])
    finally:
        session.viewers.discard(ws)
