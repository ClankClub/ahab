"""The desktop box's driver: one command in, one answer out.

perro/desktop.py runs `docker exec -i perro-desktop python3 /app/driver.py`,
sends one JSON line on standard input and reads one back. It keeps no state
of its own: the state is the X server's (where the windows are, where the
mouse is), so there's nothing to serve and nothing listening.

Every action answers the same way: what happened, the open windows, and a
screenshot taken a moment after, with the cursor drawn on it. The screen is
SCREEN pixels, exactly what the model sees, so x,y in the picture is x,y on
the screen.

Commands: {"do": "shot", "zoom": [x0, y0, x1, y1], "grid": false},
{"do": "click", "x", "y", "button", "count"}, {"do": "move", "x", "y"},
{"do": "drag", "x", "y", "to_x", "to_y"}, {"do": "scroll", "x", "y",
"direction", "amount"}, {"do": "type", "text"}, {"do": "key", "keys"},
{"do": "launch", "app"}, {"do": "focus", "window"}, {"do": "copy"},
{"do": "wait", "seconds"}, {"do": "windows"}, {"do": "ping"}.
"""

import base64
import io
import json
import os
import re
import subprocess
import sys
import time

SCREEN = (1024, 768)
SETTLE = 0.7
TYPE_LIMIT = 2000
COPY_LIMIT = 8000
KEYS = re.compile(r"^[A-Za-z0-9_+\-]{1,40}( [A-Za-z0-9_+\-]{1,40}){0,9}$")
APPS = {
    "files": ["thunar"],
    "editor": ["env", "GSETTINGS_BACKEND=memory", "mousepad"],   # no saved session, so no "restore?" question
    "writer": ["libreoffice", "--writer"],
    "calc": ["libreoffice", "--calc"],
    "firefox": ["firefox-esr"],
    "terminal": ["xfce4-terminal"],
    "images": ["ristretto"],
    "calculator": ["galculator"],
    "obsidian": ["/opt/obsidian/obsidian", "--no-sandbox"],
}
ENV = dict(os.environ, DISPLAY=":1", HOME="/home/pip", XDG_RUNTIME_DIR="/tmp/xdg-pip")
try:
    with open("/tmp/xdg-pip/bus-address") as f:
        ENV["DBUS_SESSION_BUS_ADDRESS"] = f.read().strip()
except OSError:
    pass


class Refused(Exception):
    pass


def run(*args, input=None, timeout=15) -> str:
    done = subprocess.run(list(args), input=input, capture_output=True, timeout=timeout, env=ENV)
    if done.returncode:
        raise Refused(f"{args[0]} failed: {done.stderr.decode('utf-8', 'replace').strip()[-200:]}")
    return done.stdout.decode("utf-8", "replace")


def xy(command, kx="x", ky="y") -> tuple[int, int]:
    try:
        x, y = int(command.get(kx)), int(command.get(ky))
    except (TypeError, ValueError):
        raise Refused(f"{kx} and {ky} are whole numbers of pixels.")
    if not (0 <= x < SCREEN[0] and 0 <= y < SCREEN[1]):
        raise Refused(f"({x}, {y}) is off the screen, which is {SCREEN[0]}x{SCREEN[1]}: "
                      f"x from 0 to {SCREEN[0] - 1}, y from 0 to {SCREEN[1] - 1}.")
    return x, y


def cursor() -> tuple[int, int]:
    out = run("xdotool", "getmouselocation", "--shell")
    vals = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return int(vals.get("X", 0)), int(vals.get("Y", 0))


def windows() -> list[dict]:
    """The managed windows: title, app, where, and which has the focus."""
    try:
        active = int(run("xdotool", "getactivewindow").strip() or 0)
    except Refused:
        active = 0
    found = []
    for line in run("wmctrl", "-lpG").splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        wid, _desk, pid, x, y, w, h = parts[0], parts[1], parts[2], *map(int, parts[3:7])
        title = parts[8] if len(parts) > 8 else ""
        try:
            with open(f"/proc/{pid}/comm") as f:
                app = f.read().strip()
        except OSError:
            app = "?"
        if title in ("Desktop", "xfce4-panel") or app in ("xfdesktop", "xfce4-panel"):
            continue
        found.append({"title": title, "app": app, "x": x, "y": y, "w": w, "h": h,
                      "focused": int(wid, 16) == active})
    return found


def shot(zoom=None, grid=False) -> dict:
    from PIL import Image, ImageDraw
    data = subprocess.run(["import", "-window", "root", "png:-"], capture_output=True, timeout=20,
                          env=ENV).stdout
    img = Image.open(io.BytesIO(data)).convert("RGB")
    draw = ImageDraw.Draw(img)
    cx, cy = cursor()
    arrow = [(cx, cy), (cx, cy + 17), (cx + 4, cy + 13), (cx + 8, cy + 20), (cx + 11, cy + 19),
             (cx + 7, cy + 12), (cx + 12, cy + 12)]
    draw.polygon(arrow, fill="white", outline="black")
    note = ""
    if grid:
        for x in range(100, SCREEN[0], 100):
            draw.line([(x, 0), (x, SCREEN[1])], fill=(255, 0, 255), width=1)
            draw.text((x + 2, 2), str(x), fill=(255, 0, 255))
        for y in range(100, SCREEN[1], 100):
            draw.line([(0, y), (SCREEN[0], y)], fill=(255, 0, 255), width=1)
            draw.text((2, y + 2), str(y), fill=(255, 0, 255))
    if zoom:
        try:
            x0, y0, x1, y1 = (int(v) for v in zoom)
        except (TypeError, ValueError):
            raise Refused("zoom is [x0, y0, x1, y1], the corners of a region on the screen.")
        x0, x1 = sorted((max(0, min(x0, SCREEN[0])), max(0, min(x1, SCREEN[0]))))
        y0, y1 = sorted((max(0, min(y0, SCREEN[1])), max(0, min(y1, SCREEN[1]))))
        if x1 - x0 < 16 or y1 - y0 < 16:
            raise Refused("The zoom region is too small (16 pixels a side at least).")
        scale = min(SCREEN[0] / (x1 - x0), SCREEN[1] / (y1 - y0))
        img = img.crop((x0, y0, x1, y1)).resize((round((x1 - x0) * scale), round((y1 - y0) * scale)),
                                                Image.LANCZOS)
        note = (f"Zoomed: this picture shows the screen from ({x0}, {y0}) to ({x1}, {y1}), {scale:.2f} times "
                f"bigger. Its coordinates aren't the screen's: screen x = {x0} + picture x / {scale:.2f}, "
                f"screen y = {y0} + picture y / {scale:.2f}.")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return {"image": base64.b64encode(out.getvalue()).decode("ascii"), "cursor": [cx, cy],
            "windows": windows(), "zoom_note": note}


def act(command: dict) -> str:
    what = command.get("do")
    if what == "click":
        x, y = xy(command)
        button = {"left": "1", "middle": "2", "right": "3"}.get(str(command.get("button", "left")))
        if not button:
            raise Refused('button is "left", "right" or "middle".')
        count = int(command.get("count", 1) or 1)
        if count not in (1, 2, 3):
            raise Refused("count is 1, 2 or 3.")
        run("xdotool", "mousemove", str(x), str(y), "click", "--repeat", str(count), "--delay", "80", button)
        kind = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[count]
        return f"{kind} {command.get('button', 'left')} at ({x}, {y})."
    if what == "move":
        x, y = xy(command)
        run("xdotool", "mousemove", str(x), str(y))
        return f"Moved the mouse to ({x}, {y})."
    if what == "drag":
        x, y = xy(command)
        tx, ty = xy(command, "to_x", "to_y")
        run("xdotool", "mousemove", str(x), str(y), "mousedown", "1", "sleep", "0.2",
            "mousemove", str((x + tx) // 2), str((y + ty) // 2), "sleep", "0.1",
            "mousemove", str(tx), str(ty), "sleep", "0.2", "mouseup", "1")
        return f"Dragged from ({x}, {y}) to ({tx}, {ty})."
    if what == "scroll":
        x, y = xy(command)
        direction = str(command.get("direction", "down"))
        button = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(direction)
        if not button:
            raise Refused('direction is "up", "down", "left" or "right".')
        amount = max(1, min(10, int(command.get("amount", 3) or 3)))
        run("xdotool", "mousemove", str(x), str(y), "click", "--repeat", str(amount), "--delay", "40", button)
        return f"Scrolled {direction} {amount} at ({x}, {y})."
    if what == "type":
        text = command.get("text")
        if not isinstance(text, str) or not text:
            raise Refused("text is the words to type.")
        if len(text) > TYPE_LIMIT:
            raise Refused(f"That's {len(text)} characters; the limit is {TYPE_LIMIT}.")
        run("xdotool", "type", "--delay", "12", "--", text, timeout=60)
        return f"Typed {len(text)} characters."
    if what == "key":
        keys = str(command.get("keys", "")).strip()
        if not KEYS.match(keys):
            raise Refused('keys are xdotool key names, like "Return", "ctrl+s", "alt+F4" or "ctrl+a ctrl+c" '
                          "(up to 10, separated by spaces).")
        run("xdotool", "key", "--delay", "60", *keys.split())
        return f"Pressed {keys}."
    if what == "launch":
        app = str(command.get("app", "")).strip().lower()
        if app not in APPS:
            raise Refused(f"{app!r} isn't an app here. The apps: {', '.join(APPS)}.")
        subprocess.Popen(APPS[app], env=ENV, cwd="/home/pip", start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)                         # most apps draw their window in this time
        return f"Started {app}. Slow apps (writer, calc, firefox, obsidian) can take a few more seconds."
    if what == "focus":
        want = str(command.get("window", "")).strip().lower()
        if not want:
            raise Refused("window is (part of) a title from the window list.")
        match = [w for w in windows() if want in w["title"].lower()]
        if not match:
            raise Refused(f"No open window's title has {want!r} in it.")
        run("wmctrl", "-a", match[0]["title"])
        return f"Focused \"{match[0]['title']}\"."
    if what == "wait":
        seconds = max(0.0, min(5.0, float(command.get("seconds", 1) or 1)))
        time.sleep(seconds)
        return f"Waited {seconds:g} seconds."
    raise Refused(f"Unknown command: {what!r}")


def handle(command: dict) -> dict:
    what = command.get("do")
    if what == "ping":
        return {"ok": True}
    if what == "windows":
        return {"windows": windows()}
    if what == "copy":
        try:
            text = run("xclip", "-selection", "clipboard", "-o", timeout=5)
        except Refused:
            text = ""
        return {"text": text[:COPY_LIMIT], "length": len(text)}
    if what == "shot":
        return shot(command.get("zoom"), bool(command.get("grid")))
    done = act(command)
    time.sleep(SETTLE)
    answer = shot(grid=bool(command.get("grid")))
    answer["done"] = done
    return answer


def main() -> None:
    try:
        command = json.loads(sys.stdin.readline() or "{}")
        if not isinstance(command, dict):
            raise Refused("A command is a JSON object.")
        answer = handle(command)
    except Refused as e:
        answer = {"error": str(e)}
    except subprocess.TimeoutExpired:
        answer = {"error": "The desktop didn't answer in time."}
    except Exception as e:                      # report it rather than die silently
        answer = {"error": f"The driver failed: {type(e).__name__}: {str(e)[:300]}"}
    sys.stdout.write(json.dumps(answer) + "\n")


if __name__ == "__main__":
    main()
