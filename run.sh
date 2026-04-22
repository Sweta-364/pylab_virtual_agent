#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="./venv/bin/python"
PIP_BIN="./venv/bin/pip"

load_local_env() {
  local env_file=".env"
  local line
  local key
  local value
  if [[ ! -f "$env_file" ]]; then
    return
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"

    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    if [[ -z "$key" ]]; then
      continue
    fi

    # Keep environment variables passed by the caller as highest priority.
    if [[ -n "${!key+x}" ]]; then
      continue
    fi

    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' ]]; then
      value="${value:1:${#value}-2}"
    fi

    export "$key=$value"
  done < "$env_file"
}

create_venv_if_missing() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "venv not found. Creating one with system python3..."
    python3 -m venv venv
  fi
}

install_deps_if_missing() {
  local missing
  missing="$("$PYTHON_BIN" - <<'PY'
import importlib
import importlib.metadata
import sys

required = [
    ("tkinter", "tkinter"),
    ("PIL", "Pillow"),
    ("speech_recognition", "SpeechRecognition"),
    ("requests", "requests"),
    ("requests_html", "requests-html"),
    ("ollama", "ollama"),
]

missing = []
for module_name, package_name in required:
    try:
        importlib.import_module(module_name)
    except Exception:
        missing.append(package_name)

required_packages = [
    ("pywhatkit", "PyWhatKit"),
    ("openai-whisper", "openai-whisper"),
    ("keyboard", "keyboard"),
    ("pydub", "pydub"),
    ("numpy", "numpy"),
]

for package_key, package_name in required_packages:
    try:
        importlib.metadata.version(package_key)
    except importlib.metadata.PackageNotFoundError:
        missing.append(package_name)

if missing:
    print(" ".join(missing))
    sys.exit(1)
PY
)" || true

  if [[ -n "${missing:-}" ]]; then
    echo "Installing missing dependencies: $missing"
    "$PIP_BIN" install -r requirement.txt
  fi
}

can_open_tk_window() {
  "$PYTHON_BIN" - <<'PY'
import tkinter as tk

root = tk.Tk()
root.withdraw()
root.update_idletasks()
root.destroy()
PY
}

launch_gui() {
  exec "$PYTHON_BIN" gui.py
}

validate_ollama_startup() {
  "$PYTHON_BIN" - <<'PY' || true
from ollama_handler import get_ollama_setup_instructions, validate_ollama_startup

ok, message = validate_ollama_startup()
if ok:
    print(f"[Ollama] {message}")
else:
    print(f"[Ollama Warning] {message}")
    print(get_ollama_setup_instructions())
PY
}

try_set_display() {
  if [[ -n "${DISPLAY:-}" ]]; then
    return 0
  fi

  for socket_path in /tmp/.X11-unix/X*; do
    if [[ -S "$socket_path" ]]; then
      export DISPLAY=":${socket_path##*X}"
      return 0
    fi
  done

  return 1
}

create_venv_if_missing
load_local_env
install_deps_if_missing
validate_ollama_startup

if try_set_display && can_open_tk_window >/dev/null 2>&1; then
  launch_gui
fi

if command -v xvfb-run >/dev/null 2>&1; then
  echo "No usable display found. Trying xvfb-run..."
  if xvfb-run -a "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1; then
import tkinter as tk

root = tk.Tk()
root.withdraw()
root.update_idletasks()
root.destroy()
PY
    exec xvfb-run -a "$PYTHON_BIN" gui.py
  fi
  echo "xvfb-run is available, but Tkinter could not connect to a virtual display."
fi

echo "GUI could not be opened because no graphical display is available."
echo "Run this script from a desktop session, or install xvfb with:"
echo "  sudo apt-get install -y xvfb xauth"
exit 1
