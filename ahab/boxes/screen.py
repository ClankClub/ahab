"""A picture of one app's window on the owner's screen, for apps the owner allows.

This looks at the owner's real desktop, so it's fenced harder than anything
Pip reads: only windows of the apps named in home.json's "screen_apps" (by
program name, like "Obsidian", which a window can't fake the way it can a
title), one window per call, never the whole screen, and nothing is clicked
or typed. The list is empty unless the owner fills it in, and then this
refuses everything.

Windows only, standard library only: ctypes asks Windows to draw the window
into memory (PrintWindow, which works for windows behind others but not
minimized ones), and the pixels are written out as a PNG by hand.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path
from ahab import toolkit

MAX_WIDTH = 1600            # wider captures are halved until they fit; the model downsizes anyway


class ScreenError(ValueError):
    """A window that can't or mayn't be captured; the message says why."""


def allowed(memory) -> list[str]:
    """The apps the owner lets Pip look at, as program names without .exe, lowercased."""
    try:
        apps = json.loads((memory.home / "home.json").read_text(encoding="utf-8")).get("screen_apps") or []
    except (OSError, ValueError):
        apps = []
    if not isinstance(apps, list):
        return []
    return sorted({str(a).strip().lower().removesuffix(".exe") for a in apps if str(a).strip()})


# --- PNG, by hand -------------------------------------------------------------------

def png(width: int, height: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG from rows of RGB pixels."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    chunk = lambda kind, data: (struct.pack(">I", len(data)) + kind + data
                                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def bgra_to_rgb(bgra: bytes) -> bytearray:
    rgb = bytearray(len(bgra) // 4 * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return rgb


def halve(width: int, height: int, rgb: bytes) -> tuple[int, int, bytes]:
    """Every other pixel of every other row: crude, fast, and plenty for a model."""
    stride, w2, h2 = width * 3, width // 2, height // 2
    out = bytearray()
    for y in range(0, h2 * 2, 2):
        row = rgb[y * stride:y * stride + w2 * 6]
        half = bytearray(w2 * 3)
        half[0::3], half[1::3], half[2::3] = row[0::6], row[1::6], row[2::6]
        out += half
    return w2, h2, bytes(out)


# --- Windows -------------------------------------------------------------------------

def _windows():
    """(window handle, program name, title, area, minimized) for every visible top-level window."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    def program(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return Path(buf.value).stem.lower()
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                found.append((hwnd, program(hwnd), title.value, area, bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(each, 0)
    return found


def _capture(hwnd) -> tuple[int, int, bytes]:
    """(width, height, BGRA pixels, top row first) of one window, drawn by Windows into memory."""
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    # Real pixels, not the scaled-down ones a program that never said it
    # understands display scaling is shown. Only this thread, and put back.
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise ScreenError("That window has no size on screen.")
        window_dc = user32.GetWindowDC(hwnd)
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
        old = gdi32.SelectObject(memory_dc, bitmap)
        try:
            if not user32.PrintWindow(hwnd, memory_dc, 2):   # PW_RENDERFULLCONTENT: GPU-drawn apps too
                raise ScreenError("Windows wouldn't draw that window.")

            class Header(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)   # top row first
            pixels = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0):
                raise ScreenError("Couldn't read the window's pixels.")
            return width, height, pixels.raw
        finally:
            gdi32.SelectObject(memory_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(hwnd, window_dc)
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


# --- the tool ---------------------------------------------------------------------------

def screenshot_window(memory, app: str) -> str:
    from ahab import senses, tools
    apps = allowed(memory)
    if not apps:
        raise ScreenError("No app is allowed yet: your owner lists the apps you may look at in home.json "
                          "(\"screen_apps\"), and the list is empty. Ask them if you need one.")
    want = str(app).strip().lower().removesuffix(".exe")
    if want not in apps:
        raise ScreenError(f"{app!r} isn't one of the apps your owner allows: {', '.join(apps)}.")
    if sys.platform != "win32":
        raise ScreenError("Window screenshots only work on Windows for now.")
    windows = [w for w in _windows() if w[1] == want]
    if not windows:
        raise ScreenError(f"{app} isn't open on your owner's screen.")
    showing = [w for w in windows if not w[4]]
    if not showing:
        raise ScreenError(f"{app} is minimized, so there's nothing to see.")
    hwnd, _, title, _, _ = max(showing, key=lambda w: w[3])
    width, height, bgra = _capture(hwnd)
    rgb = bgra_to_rgb(bgra)
    while width > MAX_WIDTH:
        width, height, rgb = halve(width, height, rgb)
    rel = senses.save(memory, png(width, height, rgb), f"window-{want}-{datetime.now():%H%M%S}.png")
    head = f"Screenshot of {app}'s window \"{title}\" ({width}x{height}), saved as {rel}"
    if not senses.can_see(memory):
        return f"{head}. The model you're running on can't see images, so it isn't shown to you."
    senses.show(memory, tools.workspace_root(memory) / rel, rel)
    return f"{head}, and attached to this result. {senses.NOT_ORDERS}"


# --- tools (tools.py gathers these; see toolkit.py) ----------------------------------

def available(memory) -> bool:
    try:
        return bool(json.loads((memory.home / "home.json").read_text(encoding="utf-8")).get("screen_apps"))
    except (OSError, ValueError, AttributeError):
        return False


TOOLS = {
    "screenshot_window": (
        toolkit.wrap(screenshot_window, ScreenError),
        "A picture of one app's window on your owner's screen, for the apps they allow you to look "
        "at (none unless they've said); it's attached to the result. Never the whole screen, and you "
        'can only look, not click. Args: {"app": "<program name, like Obsidian>"}',
    ),
}

GROUPS = {
    "screen": ("screenshots of your owner's app windows", ("screenshot_window",), available),
}
