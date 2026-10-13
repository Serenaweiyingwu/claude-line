#!/bin/sh
# Points the USB-connected phone at the server on this Mac and opens the page in Chrome.
# The phone reaches the server as localhost, which is what lets Chrome use the microphone.
set -e
adb reverse tcp:8700 tcp:8700
adb reverse tcp:8701 tcp:8701  # the Claude Code terminal, if terminal.py is running
adb shell am start -a android.intent.action.VIEW -d http://localhost:8700 com.android.chrome
