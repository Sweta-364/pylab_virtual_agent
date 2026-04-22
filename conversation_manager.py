import json
import os
from typing import Dict, List


_BASE_DIR = os.path.dirname(__file__)
_HISTORY: List[Dict[str, str]] = []
_IS_INITIALIZED = False
_ENV_LOADED = False


def _load_local_env() -> None:
    global _ENV_LOADED

    if _ENV_LOADED:
        return

    env_path = os.path.join(_BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        _ENV_LOADED = True
        return

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

    _ENV_LOADED = True


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(path_value: str) -> str:
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(_BASE_DIR, path_value)


def _history_limit() -> int:
    _load_local_env()
    try:
        value = int(os.getenv("CONVERSATION_HISTORY_LIMIT", "10"))
        return value if value > 0 else 10
    except ValueError:
        return 10


def _persistence_enabled() -> bool:
    _load_local_env()
    return _to_bool(os.getenv("PERSIST_CONVERSATION_HISTORY", "false"), default=False)


def _history_file_path() -> str:
    _load_local_env()
    return _resolve_path(os.getenv("CONVERSATION_HISTORY_FILE", "conversation_history.json"))


def _sanitize_message(role: str, content: str):
    if role not in {"user", "assistant"}:
        return None

    text = str(content).strip()
    if not text:
        return None

    return {"role": role, "content": text}


def _trim_history() -> None:
    max_messages = _history_limit() * 2
    if len(_HISTORY) > max_messages:
        del _HISTORY[:-max_messages]


def _save_history_if_enabled() -> None:
    if not _persistence_enabled():
        return

    file_path = _history_file_path()
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    try:
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(_HISTORY, file, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _load_history_if_enabled() -> None:
    if not _persistence_enabled():
        return

    file_path = _history_file_path()
    if not os.path.isfile(file_path):
        return

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError, TypeError):
        return

    if not isinstance(data, list):
        return

    _HISTORY.clear()
    for item in data:
        if not isinstance(item, dict):
            continue
        message = _sanitize_message(item.get("role", ""), item.get("content", ""))
        if message:
            _HISTORY.append(message)

    _trim_history()


def _initialize() -> None:
    global _IS_INITIALIZED

    _load_local_env()
    if _IS_INITIALIZED:
        return

    _load_history_if_enabled()
    _IS_INITIALIZED = True


def add_user_message(text: str) -> None:
    _initialize()
    message = _sanitize_message("user", text)
    if not message:
        return

    _HISTORY.append(message)
    _trim_history()
    _save_history_if_enabled()


def add_assistant_message(text: str) -> None:
    _initialize()
    message = _sanitize_message("assistant", text)
    if not message:
        return

    _HISTORY.append(message)
    _trim_history()
    _save_history_if_enabled()


def get_history() -> List[Dict[str, str]]:
    _initialize()
    return [dict(item) for item in _HISTORY]


def reset_history() -> None:
    _initialize()
    _HISTORY.clear()
    _save_history_if_enabled()
