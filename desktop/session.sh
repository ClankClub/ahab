#!/bin/sh
# The desktop, piece by piece: no session manager, so no power manager,
# screensaver, lock screen or first-run dialog. The session's D-Bus address is
# kept where driver.py can find it, so apps it launches join this session.
echo "$DBUS_SESSION_BUS_ADDRESS" > "$XDG_RUNTIME_DIR/bus-address"
xset s off -dpms 2>/dev/null
xfsettingsd --daemon 2>/dev/null
xfwm4 --compositor=off &
xfdesktop &
xfce4-panel &
thunar --daemon &
wait
