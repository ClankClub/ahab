#!/usr/bin/env bash
# Perro's installer, for a Mac (Linux works too, but there it can't install
# Python for you). It does four things, in order:
#   1. finds Python 3.11 or newer, and offers to install it if there isn't one
#      (with Homebrew if you have it, otherwise the official python.org installer)
#   2. downloads Perro's code from GitHub as a zip (no Git needed)
#   3. puts it in ~/Perro (or the folder you name with --dir)
#   4. checks that it starts, and tells you what to do next
#
# Run it like this, reading it before you run it:
#   curl -fsSLO https://raw.githubusercontent.com/silascroe/perro/main/install.sh
#   less install.sh
#   bash install.sh                 # or: bash install.sh --dir ~/somewhere/else
#
# Safe to run again: it updates Perro's code and never touches home/, the
# folder where Perro keeps its memory. It asks before installing anything.

set -euo pipefail

REPO_ZIP="${PERRO_REPO_ZIP:-https://github.com/silascroe/perro/archive/refs/heads/main.zip}"
PYTHON_VERSION="3.14"
PYTHON_PKG="${PERRO_PYTHON_PKG:-https://www.python.org/ftp/python/3.14.8/python-3.14.8-macos11.pkg}"
DIR="$HOME/Perro"
ASSUME_YES=0

usage() {
  echo "Usage: bash install.sh [--dir FOLDER] [--yes]"
  echo "  --dir FOLDER  where to put Perro (default: ~/Perro)"
  echo "  --yes         don't ask before installing Python"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) [ $# -ge 2 ] || { usage; exit 2; }; DIR="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf '\nStopped: %s\n' "$*" >&2; exit 1; }

# A yes/no question. Read from the terminal itself, so it still works when the
# script arrives through a pipe.
ask() {
  [ "$ASSUME_YES" = 1 ] && return 0
  printf '%s [Y/n] ' "$1"
  local answer=""
  read -r answer </dev/tty || answer="n"
  case "$answer" in ""|y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}

OS="$(uname -s)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- 1. Python ---------------------------------------------------------------

# Prints the path of a Python that's new enough, or fails quietly.
find_python() {
  local name path
  for name in python3.14 python3.13 python3.12 python3.11 python3; do
    path="$(command -v "$name" 2>/dev/null)" || continue
    # A Mac's own /usr/bin/python3 is a stand-in that pops up an installer
    # for Apple's developer tools, and its Python is too old for Perro anyway.
    if [ "$OS" = "Darwin" ] && [ "$path" = "/usr/bin/python3" ]; then continue; fi
    if "$path" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$path"; return 0
    fi
  done
  # The python.org installer puts Python here, even before a new terminal
  # has it on the PATH.
  local found=""
  for path in /Library/Frameworks/Python.framework/Versions/3.1[1-9]/bin/python3; do
    if [ -x "$path" ]; then found="$path"; fi   # the last one is the newest
  done
  [ -n "$found" ] && echo "$found"
}

install_python() {
  if [ "$OS" != "Darwin" ]; then
    fail "Perro needs Python 3.11 or newer. Install it with your system's package manager (for example: sudo apt install python3), then run this again."
  fi
  if command -v brew >/dev/null 2>&1; then
    ask "Perro needs Python 3.11 or newer. Install Python $PYTHON_VERSION with Homebrew?" \
      || fail "Perro needs Python 3.11 or newer. Install it, then run this again."
    brew install "python@$PYTHON_VERSION"
    return
  fi
  say "Perro needs Python 3.11 or newer, and this Mac doesn't have it."
  say "The official installer is at: $PYTHON_PKG"
  ask "Download it and install it? (macOS will ask for your password)" \
    || fail "Install Python from https://www.python.org/downloads/, then run this again."
  local pkg="$TMP/python.pkg"
  case "$PYTHON_PKG" in
    http*) curl -fL --progress-bar "$PYTHON_PKG" -o "$pkg" || fail "Couldn't download Python. Check the internet connection." ;;
    *) cp "$PYTHON_PKG" "$pkg" ;;
  esac
  # Only run an installer that really comes from the people who make Python.
  pkgutil --check-signature "$pkg" | grep -q "Python Software Foundation" \
    || fail "The downloaded installer isn't signed by the Python Software Foundation, so it wasn't run."
  sudo installer -pkg "$pkg" -target /
}

say "Looking for Python 3.11 or newer..."
if ! PY="$(find_python)"; then
  install_python
  PY="$(find_python)" || fail "Python was installed, but this script can't find it. Open a new terminal and run this again."
fi
say "Using $PY ($("$PY" -c 'import platform; print(platform.python_version())'))."

# --- 2 and 3. Perro's code ---------------------------------------------------

if [ -d "$DIR/.git" ]; then
  fail "$DIR is a Git checkout. Update it with git pull instead."
fi
if [ -d "$DIR" ] && [ ! -f "$DIR/perro/heartbeat.py" ] && [ -n "$(ls -A "$DIR")" ]; then
  fail "$DIR already exists and isn't Perro. Pick another folder with --dir."
fi

say "Downloading Perro..."
case "$REPO_ZIP" in
  http*) curl -fsSL "$REPO_ZIP" -o "$TMP/perro.zip" || fail "Couldn't download Perro from GitHub. Check the internet connection." ;;
  *) cp "$REPO_ZIP" "$TMP/perro.zip" ;;
esac
# Unzipped with Python, so the machine needs nothing else.
"$PY" -m zipfile -e "$TMP/perro.zip" "$TMP/src"
SRC="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$SRC" ] && [ -f "$SRC/perro/heartbeat.py" ] || fail "The download doesn't look like Perro."

if [ -f "$DIR/perro/heartbeat.py" ]; then
  say "Updating the Perro in $DIR (its home/ stays as it is)..."
else
  say "Putting Perro in $DIR..."
fi
mkdir -p "$DIR"
# The download never has a home/ folder, so copying over an old install leaves
# Perro's memory alone.
cp -R "$SRC/." "$DIR/"

# --- 4. Does it start? -------------------------------------------------------

(cd "$DIR" && "$PY" -c 'import perro.heartbeat, perro.tools, perro.inbox') \
  || fail "Perro's code is in $DIR, but it didn't start. The error is above."

say ""
say "Perro is installed in $DIR."
say ""
say "Next, give it a mind to think with. Pick one:"
say ""
say "  A DeepSeek API key (pay as you go). Add it to new terminals, then open one:"
say "    echo 'export DEEPSEEK_API_KEY=\"sk-...\"' >> ~/.zshrc"
say ""
say "  Or a ChatGPT subscription, through Codex (install Codex first). Log in once"
say "  with Perro's own Codex profile, then tell Perro to use it:"
say "    mkdir -p ~/.perro/codex && CODEX_HOME=~/.perro/codex codex login"
say "    mkdir -p \"$DIR/home\" && echo '{\"backend\": \"codex-luna\"}' > \"$DIR/home/home.json\""
say ""
say "Then, to talk to it:"
say "  cd \"$DIR\""
say "  \"$PY\" -m perro.inbox \"Hi\"     # leave it a message"
say "  \"$PY\" -m perro.heartbeat      # run it; Ctrl+C stops it"
say ""
say "README.md has the rest: Telegram, Google, and running it unattended."
