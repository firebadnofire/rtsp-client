#!/usr/bin/env bash

# Get absolute path of the current directory
APPDIR="$(realpath .)"

# Replace RTSPPATH with the absolute path and install the desktop file
sed "s|RTSPPATH|$APPDIR|g" rtsp_viewer.desktop \
  | install -Dm644 /dev/stdin ~/.local/share/applications/rtsp_viewer.desktop
