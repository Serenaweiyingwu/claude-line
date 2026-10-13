#!/bin/sh
# Starts (or restarts) both servers in the background: the voice line on 8700, open to the
# Wi-Fi with the LINE_KEY check, and the Claude Code terminal on 8701, this Mac only.
# Restarting ends the Claude Code session that the terminal page was showing.
cd "$(dirname "$0")"
lsof -tiTCP:8700 -tiTCP:8701 -sTCP:LISTEN | xargs kill 2>/dev/null
sleep 1
nohup .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8700 --no-access-log > server.log 2>&1 &
nohup .venv/bin/uvicorn terminal:app --port 8701 > terminal.log 2>&1 &
echo "Claude Line is starting. Logs: server.log, terminal.log. Stop with: lsof -tiTCP:8700 -tiTCP:8701 | xargs kill"
