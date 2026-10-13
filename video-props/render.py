"""Renders an animated page in this folder to an mp4, frame by frame, with headless Chrome.

    ../.venv/bin/python render.py three-parts.html zh out.mp4 [seconds] [single-frame-second]

The page must define seek(ms). Needs ffmpeg and Google Chrome.
"""

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9339
FPS = 30
WIDTH, HEIGHT = 1080, 1920


async def main(page: str, lang: str, out: str, seconds: float, still: float | None):
    url = f"{(Path(__file__).parent / page).as_uri()}?lang={lang}"
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through the system proxy
        for _ in range(50):
            try:
                tabs = json.load(local.open(f"http://127.0.0.1:{PORT}/json"))
                break
            except OSError:
                await asyncio.sleep(0.2)
        tab = next(t for t in tabs if t["type"] == "page")
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None, proxy=None) as ws:
            sent = 0

            async def call(method, **params):
                nonlocal sent
                sent += 1
                await ws.send(json.dumps({"id": sent, "method": method, "params": params}))
                while True:
                    reply = json.loads(await ws.recv())
                    if reply.get("id") == sent:
                        return reply.get("result", {})

            await call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=url)
            await asyncio.sleep(1.5)
            await call("Runtime.evaluate", expression="document.fonts.ready", awaitPromise=True)

            async def frame(ms):
                await call("Runtime.evaluate", expression=f"seek({ms})")
                return base64.b64decode((await call("Page.captureScreenshot", format="png"))["data"])

            if still is not None:
                Path(out).write_bytes(await frame(still * 1000))
                return
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-movflags", "+faststart", out],
                stdin=subprocess.PIPE,
            )
            for n in range(int(seconds * FPS)):
                ffmpeg.stdin.write(await frame(n * 1000 / FPS))
            ffmpeg.stdin.close()
            ffmpeg.wait()
    finally:
        chrome.kill()


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main(args[0], args[1], args[2], float(args[3]) if len(args) > 3 else 20,
                     float(args[4]) if len(args) > 4 else None))
