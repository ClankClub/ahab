#!/bin/sh
# The box's main process: the virtual screen, then the desktop on it.
export DISPLAY=:1 HOME=/home/pip XDG_RUNTIME_DIR=/tmp/xdg-pip
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
Xvfb :1 -screen 0 1024x768x24 -nolisten tcp -dpi 96 -nocursor &
for _ in $(seq 50); do [ -e /tmp/.X11-unix/X1 ] && break; sleep 0.1; done
exec dbus-run-session -- sh /app/session.sh
