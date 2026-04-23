import hashlib
import json
import re
import time
from typing import Dict, Tuple

import ollama_handler


_INTENT_CACHE: Dict[str, Tuple[Dict[str, object], float]] = {}
_COMMAND_CACHE: Dict[str, Tuple[Dict[str, object], float]] = {}
_CACHE_TTL_SECONDS = 300
_INTENT_TIMEOUT_SECONDS = 5.0
_COMMAND_TIMEOUT_SECONDS = 7.0

_INTENT_SYSTEM_PROMPT = """You are a filesystem intent classifier.
Answer with only "yes" or "no".

Respond "yes" if the user is asking to interact with files, folders, paths, directories, the desktop, downloads, home folder, or OS file management.
Respond "no" for greetings, weather, time, jokes, music playback, browsing, or general conversation.

Examples:
- "list all files on desktop" -> yes
- "show my documents" -> yes
- "read /tmp/test.txt" -> yes
- "create a folder named hello" -> yes
- "what is the weather" -> no
- "hello how are you" -> no
"""

_COMMAND_SYSTEM_PROMPT = """You convert filesystem requests into JSON.
Return only valid JSON. No markdown. No explanation.

Allowed operations:
- list
- read
- info
- create_file
- create_directory
- delete
- update
- batch_create

Schema rules:
- list: {"operation":"list","path":"...","include_hidden":false,"show_tree":true,"directories_only":false,"files_only":false}
- read: {"operation":"read","path":"..."}
- info: {"operation":"info","path":"..."}
- create_file: {"operation":"create_file","path":"...","content":"..."}
- create_directory: {"operation":"create_directory","path":"..."}
- delete: {"operation":"delete","path":"...","target_kind":"file|directory|auto","recursive":true,"confirm_needed":true}
- update: {"operation":"update","path":"...","content":"...","append":false}
- batch_create: {"operation":"batch_create","actions":[{"create":"directory","path":"..."},{"create":"file","path":"...","content":"..."}]}

Path guidance:
- Prefer aliases like "desktop", "downloads", "documents", "home", "root" when the user says them.
- Use absolute paths when the user provides them.
- Preserve filenames and file content exactly.

If the request is not clearly a filesystem request, return {}.
"""

_HEURISTIC_OS_PATTERNS = (
    r"\b(file|files|folder|folders|directory|directories|desktop|downloads|documents|path|paths)\b",
    r"\b(create|make|list|show|read|open|delete|remove|update|append|write|edit|rename|move|copy)\b.*\b(file|folder|directory)\b",
    r"(?:^|[\s])(/|~)",
)


def _normalize_input(user_input: str) -> str:
    return " ".join(str(user_input or "").strip().split())


def _cache_key(user_input: str) -> str:
    normalized = _normalize_input(user_input).lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _heuristic_is_os_operation(user_input: str) -> bool:
    normalized = _normalize_input(user_input).lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _HEURISTIC_OS_PATTERNS)


def _clear_expired_cache() -> None:
    now = time.time()
    for cache in (_INTENT_CACHE, _COMMAND_CACHE):
        expired_keys = [
            key for key, (_, timestamp) in cache.items()
            if now - timestamp >= _CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            del cache[key]


def detect_os_intent(user_input: str, timeout_seconds: float = _INTENT_TIMEOUT_SECONDS) -> Dict[str, object]:
    start = time.time()
    _clear_expired_cache()

    normalized = _normalize_input(user_input)
    if not normalized:
        return {
            "is_os_operation": False,
            "from_cache": False,
            "response_time_ms": 0.0,
            "error": "empty input",
        }

    key = _cache_key(normalized)
    cached = _INTENT_CACHE.get(key)
    if cached:
        result, _ = cached
        return {
            **result,
            "from_cache": True,
            "response_time_ms": (time.time() - start) * 1000.0,
        }

    try:
        response = ollama_handler.query_ollama(
            user_input=f"Classify this request: {normalized}",
            conversation_history=[],
            system_prompt=_INTENT_SYSTEM_PROMPT,
            timeout_override=timeout_seconds,
        ).strip().lower()
        result = {
            "is_os_operation": response.startswith("yes"),
            "from_cache": False,
            "response_time_ms": (time.time() - start) * 1000.0,
            "error": None,
        }
    except Exception as exc:
        result = {
            "is_os_operation": _heuristic_is_os_operation(normalized),
            "from_cache": False,
            "response_time_ms": (time.time() - start) * 1000.0,
            "error": str(exc),
        }

    _INTENT_CACHE[key] = (dict(result), time.time())
    return result


def _extract_json_object(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return "{}"
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return match.group(0)
    return text


def interpret_os_command(user_input: str, timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS) -> Dict[str, object]:
    _clear_expired_cache()
    normalized = _normalize_input(user_input)
    if not normalized:
        return {}

    key = _cache_key(normalized)
    cached = _COMMAND_CACHE.get(key)
    if cached:
        result, _ = cached
        return dict(result)

    try:
        response = ollama_handler.query_ollama(
            user_input=f"Convert this filesystem request to JSON: {normalized}",
            conversation_history=[],
            system_prompt=_COMMAND_SYSTEM_PROMPT,
            timeout_override=timeout_seconds,
        )
        parsed = json.loads(_extract_json_object(response))
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception:
        parsed = {}

    _COMMAND_CACHE[key] = (dict(parsed), time.time())
    return parsed


def clear_cache() -> None:
    _INTENT_CACHE.clear()
    _COMMAND_CACHE.clear()


def get_cache_stats() -> Dict[str, int]:
    _clear_expired_cache()
    return {
        "intent_cache_size": len(_INTENT_CACHE),
        "command_cache_size": len(_COMMAND_CACHE),
        "ttl_seconds": _CACHE_TTL_SECONDS,
    }
