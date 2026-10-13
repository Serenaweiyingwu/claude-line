# Claude Line

A flip phone that is a dedicated line to Claude. This repo is the voice server that runs on
the Mac, plus a browser page that stands in for the phone until the hardware arrives.

```
mic (16 kHz PCM) ──WebSocket──▶ Whisper ──▶ Claude ──▶ Piper ──WebSocket──▶ speaker
```

Speech-to-text (faster-whisper) and text-to-speech (Piper) both run locally. Only the
transcript goes to the Anthropic API.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m piper.download_voices en_US-lessac-medium --download-dir voices
cp .env.example .env   # then put your ANTHROPIC_API_KEY in it
```

The Whisper model downloads itself the first time the server starts.

## Run

```sh
.venv/bin/uvicorn server:app --port 8700
```

`./start.sh` starts this and the Claude Code terminal together in the background, with the voice
line open to the Wi-Fi (see "Without the cable").

Open http://localhost:8700, press **Pick up**, and talk. In VS Code, the
"Claude Line server" launch configuration does the same thing.

## How a call works

- One WebSocket connection to `/call` is one call. Closing it hangs up.
- The client sends 50 ms chunks of 16 kHz mono int16 audio. The server waits for
  `SILENCE_MS` of quiet after speech, then transcribes the utterance.
- Claude's reply is spoken sentence by sentence as it streams in.
- The line is half-duplex: the server ignores the mic until the client reports that
  Claude's audio has finished playing (`playback_done`), so you cannot interrupt yet.

If the server cuts you off or never notices you stopped, tune `VAD_THRESHOLD` and
`SILENCE_MS` in `.env`.

## Using it from the phone

The phone is a Samsung Galaxy Folder 2 (Android 6), which runs Chrome up to version 106.
Browsers only allow the microphone on `localhost` or over HTTPS, so the phone reaches the
server through the USB cable:

1. On the phone, turn on Developer options and USB debugging, and install Chrome.
2. On the Mac: `brew install --cask android-platform-tools`
3. Plug the phone in, start the server, then run `./phone.sh`.

Press the OK key to pick up the first time and allow the microphone. After that, closing
the flip hangs up and opening it picks up, for as long as the page stays loaded.

### Without the cable

Over Wi-Fi the phone calls the Mac's network address instead of `localhost`:

1. Put a long random `LINE_KEY` in `.env`. Callers that are not this Mac must present it.
2. Run the server with `--host 0.0.0.0`.
3. On the phone, open `http://<mac address>:8700/unlock?key=<LINE_KEY>` once. It stores the
   key in a cookie.
4. Chrome only allows the microphone on `localhost` or HTTPS. On the phone, open
   `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add `http://<mac address>:8700`,
   enable it and relaunch Chrome.

The key and the audio cross the Wi-Fi unencrypted, so only do this on a network you trust,
never on the open internet. If the Mac's address changes, repeat steps 3 and 4.

## Claude Code on the phone

`terminal.py` shows one Claude Code session in a browser, so the phone screen can be a
Claude Code terminal:

```sh
.venv/bin/uvicorn terminal:app --port 8701
```

After `./phone.sh`, open http://localhost:8701 in Chrome on the phone (or on the Mac: both
show the same session). `?size=12` changes the text size. Whoever can open the page can type
into Claude Code, so keep it on `localhost`; never run it with `--host 0.0.0.0`.

## Asking the computer to do things

The voice Claude has one tool, `ask_computer`. It writes the task to `bridge/request.txt` and
waits up to two minutes for `bridge/reply.txt`, then speaks the answer. Something on the Mac
has to be watching that folder: so far that is a Claude Code session with browser access,
left on standby, which does the task (for example reading the newest email in Chrome) and
writes the reply. Nothing answers if no session is watching.

## Next

- Map keypad keys to speed dial.
- `POST /ring` so a Claude Code Stop/Notification hook can make the phone ring.
- Interrupting Claude mid-sentence.
- Have the bridge start its own Claude Code session instead of needing one on standby.
