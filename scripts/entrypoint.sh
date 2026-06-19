#!/usr/bin/env bash
set -euo pipefail

mkdir -p /tmp/project_dm-browser

Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp >/tmp/project-dm-xvfb.log 2>&1 &
XVFB_PID=$!

fluxbox >/tmp/project-dm-fluxbox.log 2>&1 &
FLUXBOX_PID=$!

sleep 2

x11vnc -display :99 -rfbport 5900 -forever -shared -nopw -bg >/tmp/project-dm-x11vnc.log 2>&1

/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 >/tmp/project-dm-novnc.log 2>&1 &
NOVNC_PID=$!

cleanup() {
  kill "${NOVNC_PID}" "${FLUXBOX_PID}" "${XVFB_PID}" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

exec uv run serve
